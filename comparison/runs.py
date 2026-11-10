"""Validate P11 run metadata, then measure the independently observed attempts."""
from collections import Counter, defaultdict

from analytics_support import AnalyticsError, digest, ref_key

from .metrics import VERSION, canonical, label, measure_trace, ratio, resolve, service_name, summarize


def _incompatible(reason):
    raise AnalyticsError("runs_not_comparable", reason)


def _snapshot(run):
    snapshot = run.get("config_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("revision") in (None, ""):
        _incompatible("Missing historical config snapshot or revision")
    if "config_revision" in run and run["config_revision"] != snapshot["revision"]:
        _incompatible("Snapshot revision differs from the config bound to the run")
    service = snapshot.get("service_id") or service_name(snapshot.get("service"))
    edges = snapshot.get("edges")
    if not service or not isinstance(edges, list) or not edges:
        _incompatible("Missing addressed configuration")
    # Only metadata is removed. Unknown future settings remain comparable.
    metadata = {"revision", "config_id", "fetched_at_ns", "freshness", "capabilities", "schema", "schema_version",
                "source", "edges"}
    settings = {k: v for k, v in snapshot.items() if k not in metadata}
    calls = {}
    for edge in edges:
        if not isinstance(edge, dict):
            _incompatible("Invalid historical edge")
        call = edge.get("call_ref")
        if not isinstance(call, str) or not call or call in calls:
            _incompatible("Missing or duplicate call reference")
        calls[call] = {k: v for k, v in edge.items() if k != "notes"}
    return service, settings, calls


def _change(before, after, expected):
    owner, settings, old = _snapshot(before)
    new_owner, new_settings, new = _snapshot(after)
    if owner != new_owner or settings != new_settings or old.keys() != new.keys():
        _incompatible("Service, configured calls or other service settings differ")
    differences = [(call, k) for call in old for k in old[call].keys() | new[call].keys()
                   if k not in old[call] or k not in new[call] or canonical(old[call][k]) != canonical(new[call][k])]
    if len(differences) != 1:
        _incompatible("The historical configs must differ in precisely one field")
    call, field = differences[0]
    if field not in old[call] or field not in new[call] or field not in {
            "per_attempt_timeout_ns", "max_attempts", "backoff", "jitter"}:
        _incompatible("Only replacement of an addressed retry setting is supported")
    change = {"service_id": owner, "call_ref": call, "field": field, "before": old[call][field], "after": new[call][field]}
    if expected is None:
        # P11 default is unambiguous only on the single mutable laboratory call.
        if field != "max_attempts" or len(old) != 1:
            _incompatible("An explicit expected change is required for this target")
    else:
        if not isinstance(expected, dict) or not {"service_id", "call_ref", "field"} <= expected.keys():
            _incompatible("Expected change must identify service, call and field")
        if any(canonical(expected[k]) != canonical(v) for k, v in change.items() if k in expected):
            _incompatible("The changed field or value differs from the requested recommendation")
    if before["config_snapshot"]["revision"] == after["config_snapshot"]["revision"]:
        _incompatible("Different configurations have the same revision")
    return change, old[call].get("edge_id")


def _sample(run):
    collection = run["trace_collection"]
    refs = collection.get("trace_refs")
    expected, received = collection.get("expected_count"), collection.get("received_count")
    if not isinstance(refs, list) or type(expected) is not int or type(received) is not int:
        raise AnalyticsError("invalid_run_sample", "Frozen references and both collection counts are required")
    if received != len(refs) or not 0 <= received <= expected:
        raise AnalyticsError("invalid_run_sample", "Frozen refs and collection counts differ")
    if not refs:
        raise AnalyticsError("insufficient_traces", "Cannot compare an empty sample")
    if collection["status"] == "ready" and expected != received:
        raise AnalyticsError("invalid_run_sample", "Ready sample is incomplete")
    if len({ref_key(r)[:2] for r in refs}) != len(refs):
        raise AnalyticsError("invalid_run_sample", "Duplicate input trace in frozen sample")
    if collection.get("run_id", run["run_id"]) != run["run_id"]:
        raise AnalyticsError("invalid_run_sample", "Collection belongs to another run")
    return resolve(run.get("traces", ()), refs)


def _attempts(row, graph, run, change, edge):
    if not row["calls_complete"]:
        raise AnalyticsError("invalid_run_sample", "Frozen trace has incomplete call observations",
                             details={"ref": row["ref"], "issues": row["issues"]})
    root = row["root"]
    if (str(root.get("kind", "")).lower() != "server" or label(root, "run_id") != run["run_id"]
            or label(root, "probe") or graph.get("probe")):
        raise AnalyticsError("invalid_run_sample", "Expected a non-probe entry SERVER from the selected run",
                             details={"ref": row["ref"]})
    call_groups = defaultdict(list)
    attempts = {}
    for attempt in graph.get("attempts", []):
        key = attempt.get("attempt_id")
        if not key or key in attempts or attempt.get("trace_ref", row["ref"]) != row["ref"]:
            raise AnalyticsError("invalid_run_sample", "Invalid normalized attempt identity")
        attempts[key] = attempt
    snapshot_service = run["config_snapshot"].get("service")
    has_owner_binding = snapshot_service is not None or any(
        label(span, "service_id") == change["service_id"] or service_name(span.get("service")) == change["service_id"]
        for span in graph["spans"])
    for client in row["clients"]:
        attempt = attempts.get(client["span_id"], {})
        span_service = client.get("service")
        registry_id = label(client, "service_id")
        if registry_id is not None:
            same_owner = registry_id == change["service_id"]
        elif isinstance(span_service, dict) and isinstance(snapshot_service, dict):
            same_owner = span_service == snapshot_service
        else:
            same_owner = service_name(span_service) == (service_name(snapshot_service) or change["service_id"])
        # An exact machine edge already identifies the caller; a display name cannot
        # be compared to an opaque adapter ID. A call_ref alone is local to its owner.
        def observed(name):
            from_span, normalized = label(client, name), attempt.get(name)
            if from_span is not None and normalized is not None and from_span != normalized:
                raise AnalyticsError("invalid_run_sample", f"Conflicting {name} on the same attempt")
            return from_span if from_span is not None else normalized

        edge_id = observed("edge_id")
        has_edge = edge is not None and edge_id is not None
        exact_edge = has_edge and edge_id == edge
        if not same_owner and not exact_edge:
            if (not has_edge and registry_id is None and not has_owner_binding
                    and observed("call_ref") in (None, change["call_ref"])):
                raise AnalyticsError("invalid_run_sample", "Cannot resolve the owner of a possible target call")
            continue
        if exact_edge and not same_owner and (registry_id is not None or snapshot_service is not None):
            raise AnalyticsError("invalid_run_sample", "The observed caller contradicts the configured edge")
        call_ref = observed("call_ref")
        if call_ref is None and not has_edge:
            raise AnalyticsError("invalid_run_sample", "Missing target identity on client span of the configured service")
        if call_ref is not None and has_edge and (call_ref == change["call_ref"]) != exact_edge:
            raise AnalyticsError("invalid_run_sample", "Conflicting call and edge identities")
        if (call_ref is not None and call_ref != change["call_ref"]) or (has_edge and not exact_edge):
            continue
        if attempt.get("membership_source") == "derived":
            # B also produces synthetic IDs/indexes for heuristic groups. A laboratory run
            # must carry its own measured membership on each addressed CLIENT span.
            group = label(client, "logical_call_id")
            index = label(client, "attempt_index")
        else:
            group = label(client, "logical_call_id") or attempt.get("call_id")
            index = observed("attempt_index")
        if not isinstance(group, str) or not group or type(index) is not int or index < 1:
            raise AnalyticsError("invalid_run_sample", "Missing laboratory call membership or attempt index")
        call_groups[group].append(index)
    for indices in call_groups.values():
        if sorted(indices) != list(range(1, len(indices) + 1)):
            raise AnalyticsError("invalid_run_sample", "Incomplete or duplicate attempt indexes")
    n = sum(map(len, call_groups.values()))
    return n, n - len(call_groups)


def _measure(run, graphs, change, edge):
    rows = [measure_trace(g) for g in graphs]
    counts = Counter()
    attempts_total = retries_total = 0
    for row, graph in zip(rows, graphs):
        n, retries = _attempts(row, graph, run, change, edge)
        counts[n] += 1
        attempts_total += n
        retries_total += retries
    result = summarize(rows)
    result.update(run_id=run["run_id"], config_revision=run["config_snapshot"]["revision"],
                  collection_status=run["trace_collection"]["status"], received_count=len(rows),
                  expected_count=run["trace_collection"]["expected_count"], attempts=attempts_total, retries=retries_total,
                  attempts_per_input=ratio(attempts_total, len(rows)), attempt_count_histogram={str(n): count for n, count in sorted(counts.items())},
                  http_status_counts=dict(sorted(Counter(str(r["root"].get("http_status") or "unknown") for r in rows).items())),
                  trace_refs=sorted((r["ref"] for r in rows), key=ref_key))
    return result


def validate_run_collections(*runs):
    if any(not isinstance(run, dict) for run in runs):
        raise AnalyticsError("invalid_run", "Expected run records from the control component", 422)
    if any(run.get("status") == "failed" for run in runs):
        raise AnalyticsError("run_failed", "The load run failed")
    if any(run.get("status") == "running" for run in runs):
        raise AnalyticsError("traces_pending", "Trace collection has not finished", 409)
    for run in runs:
        collection = run.get("trace_collection")
        if not isinstance(collection, dict):
            raise AnalyticsError("invalid_run_sample", "Run metadata must include a frozen trace collection")
        if collection.get("status") == "waiting":
            raise AnalyticsError("traces_pending", "Trace collection has not finished", 409)
        if run.get("status") != "completed" or collection.get("status") not in ("ready", "partial"):
            raise AnalyticsError("invalid_run", "Unsupported run or collection status")
        if not isinstance(collection.get("trace_refs"), list):
            raise AnalyticsError("invalid_run_sample", "Expected a list of frozen trace references")


def compare_runs(before, after, expected_change=None):
    """Compare frozen run bundles. Their ordering is baseline then candidate."""
    validate_run_collections(before, after)
    if not before.get("run_id") or not after.get("run_id") or before["run_id"] == after["run_id"]:
        _incompatible("Two distinct run IDs are required")
    for key in ("stand_id", "scenario_revision", "fault_revision", "load"):
        if before.get(key) is None or after.get(key) is None or before[key] != after[key]:
            _incompatible(f"Missing or different {key}")
    if before["trace_collection"].get("expected_count") != after["trace_collection"].get("expected_count"):
        _incompatible("Expected input counts differ")
    change, edge = _change(before, after, expected_change if expected_change is not None else after.get("expected_change"))
    a_graphs, b_graphs = _sample(before), _sample(after)
    a_ids = {ref_key(g["trace_ref"])[:2] for g in a_graphs}
    b_ids = {ref_key(g["trace_ref"])[:2] for g in b_graphs}
    envs = {env for env, _ in a_ids | b_ids}
    if a_ids & b_ids or len(envs) != 1:
        _incompatible("The frozen samples overlap or belong to different environments")
    if any(run.get("environment_id", next(iter(envs))) not in envs for run in (before, after)):
        _incompatible("Frozen trace environment differs from run metadata")
    a = _measure(before, a_graphs, change, edge)
    b = _measure(after, b_graphs, change, edge)
    return {"algorithm_version": VERSION, "stand_id": before["stand_id"], "change": change,
            "baseline": a, "candidate": b, "attempts_per_input_delta": b["attempts_per_input"] - a["attempts_per_input"],
            "input_hash": digest([VERSION, before["run_id"], after["run_id"], a["config_revision"],
                                  b["config_revision"], a["trace_refs"], b["trace_refs"], change, before["stand_id"],
                                  before["scenario_revision"], before["fault_revision"], before["load"]]),
            "warnings": ["partial_sample"] if "partial" in (a["collection_status"], b["collection_status"]) else []}
