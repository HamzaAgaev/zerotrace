"""Product-facing C facade used by the common A server.

The facade deliberately receives its telemetry repository and A's immutable
control records from the host.  C therefore never invents traces, runs or
configuration revisions.
"""
from __future__ import annotations

from collections.abc import Mapping
from contextlib import contextmanager
from copy import deepcopy

from analytics_support import AnalyticsError, digest

from .repository_source import RepositorySource
from .service import AnalyticsService
from .storage import LocalResultStore, ResultStore


def _records_snapshot(records: object) -> dict:
    """Normalise A's small read-only control-store surface to C's source shape."""
    if callable(records):
        return deepcopy(records())
    if isinstance(records, Mapping):
        return deepcopy(dict(records))
    snapshot = getattr(records, "analytics_snapshot", None)
    if callable(snapshot):
        return deepcopy(snapshot())
    # ControlStore in the common server exposes items(kind).
    items = getattr(records, "items", None)
    if callable(items):
        def rows(kind):
            result = {}
            for row in items(kind):
                if "id" in row and "body" in row:
                    result[row["id"]] = row["body"]
                elif row.get("run_id"):
                    result[row["run_id"]] = row
                elif row.get("environment_id"):
                    result[row["environment_id"]] = row
            return result
        environments, runs = rows("environment"), rows("run")
        for operation in items("operation"):
            environment = environments.setdefault(operation["environment_id"], {})
            summary = environment.setdefault("journal_summary", [])
            summary.append({key: operation.get(key) for key in
                            ("operation_id", "status", "requested_at", "verification")})
        return {"environments": environments, "runs": runs}
    return {"environments": {}, "runs": {}}


class AnalyticsFacade:
    def __init__(self, telemetry_spanstore, control_records: object,
                 *, results: ResultStore | None = None, environments=()):
        self._control_records = control_records
        self._environments = {row["environment_id"]: row.get("kind") for row in environments}
        self.source = RepositorySource(telemetry_spanstore, control_snapshot=self._snapshot)
        self.results = results or LocalResultStore()
        self.service = AnalyticsService(self.source, self.results)

    def _snapshot(self) -> dict:
        snapshot = _records_snapshot(self._control_records)
        for environment_id, kind in self._environments.items():
            settings = snapshot.setdefault("environments", {}).setdefault(environment_id, {})
            if kind is not None:
                settings["environment_kind"] = kind
        return snapshot

    @contextmanager
    def configured(self, environment_id=None, configs=None, trace_ref=None):
        with self.source.pin() as view:
            if trace_ref is not None:
                view.restrict_audit_to_trace(trace_ref)
            if configs:
                view.configure_policies(environment_id, configs, _policies)
                settings = view.control.setdefault("environments", {}).setdefault(environment_id, {})
                settings.setdefault("environment_kind",
                                    "lab" if all(row.get("is_lab") for row in configs.values()) else "external")
            yield self.service

    def audit(self, environment_id: str, configs_dict: Mapping | None = None):
        with self.configured(environment_id, configs_dict) as service:
            return service.audit(environment_id)

    def resolve_recommendation(self, environment_id: str, occurrence_id: str,
                               recommendation_id: str, configs_dict: Mapping | None = None):
        current = self.audit(environment_id, configs_dict)
        record = self.service.recommendation(environment_id, occurrence_id, recommendation_id)
        recommendation = record["recommendation"]
        intents = recommendation.get("intents", [])
        if len(intents) != 1 or not isinstance(intents[0], dict):
            raise AnalyticsError("invalid_recommendation", "Recommendation has no trusted apply intent", 409)
        # More telemetry can change occurrence IDs without changing the policy or the action.
        intent = {k: v for k, v in intents[0].items() if k != "reason_ref"}
        for finding in current["findings"]:
            if finding["finding_id"] != record["finding_id"]:
                continue
            if (finding["occurrence_id"] != occurrence_id
                    and record.get("evidence_hash") != digest(finding["evidence"])):
                break
            for candidate in finding["recommendations"]:
                actions = candidate["intents"]
                if len(actions) == 1 and {k: v for k, v in actions[0].items() if k != "reason_ref"} == intent:
                    return intents[0]
        raise AnalyticsError("stale_recommendation", "The current audit no longer supports this change", 409)

    def compare_runs(self, run_left: Mapping, run_right: Mapping):
        # A passes frozen trace refs in these run records. Inject them only for
        # this calculation so comparison can never drift to a live trace set.
        left, right = _comparison_pair(run_left, run_right)
        with self.source.pin() as view:
            view.control.setdefault("runs", {}).update({left["run_id"]: left, right["run_id"]: right})
            return self.service.compare({"baseline_run_id": left["run_id"], "candidate_run_id": right["run_id"]})

    def report(self, environment_id: str, configs_dict: Mapping | None = None):
        with self.configured(environment_id, configs_dict) as service:
            return service.report(environment_id)


def _policies(configs: Mapping, environment_id: str, edges: Mapping) -> list[dict]:
    """Translate product P4 configs to C's explicitly sourced edge-policy input."""
    policies = []
    identities = {}
    for label in edges.values():
        for service in (label.get("caller"), label.get("callee")):
            if isinstance(service, Mapping):
                identities.setdefault(service["service_name"], set()).add(
                    (service.get("project_id"), service.get("namespace")))
    for service_id, config in configs.items():
        revision = config.get("revision") or config.get("config_revision")
        if (config.get("environment_id", environment_id) != environment_id
                or config.get("service_id") != service_id):
            continue
        for call in config.get("calls", []):
            target = call.get("target", {})
            for edge_id, label in edges.items():
                if not _matches_call(label, config, call):
                    continue
                if _ambiguous_scope(config, identities) or _ambiguous_scope(target, identities):
                    continue
                parent = _incoming_timeout(configs, config, label, environment_id)
                budget = call.get("budget_ms")
                if budget is None:
                    budget = call.get("parent_budget_ms")
                uses_parent_budget = budget is None and parent is not None
                if uses_parent_budget:
                    budget = parent["timeout_ms"]
                policies.append({
                    "edge_id": edge_id, "source": "runtime", "execution": "serial",
                    "service_id": config.get("service_id", service_id), "call_ref": call.get("call_ref"),
                    "target": target, "per_attempt_timeout_ns": _ms_to_ns(call.get("timeout_ms")),
                    "max_attempts": call.get("max_attempts"),
                    "budget_ns": _ms_to_ns(budget),
                    "upstream_per_attempt_timeout_ns": _ms_to_ns(parent["timeout_ms"]) if parent else None,
                    "upstream_config": parent,
                    "budget_config": parent if uses_parent_budget else None,
                    "backoff": {"kind": "fixed", "base_ns": _ms_to_ns(call.get("backoff_ms", 0)), "jitter": "none"},
                    "retry_on": list(call.get("retry_on", [])), "config_revision": revision,
                    "environment_id": environment_id,
                    "environment_kind": "lab" if config.get("is_lab") else "external",
                    "capabilities": {
                        **config.get("capabilities", {}),
                        "write_fields": call.get("write_fields", []),
                    },
                })
    return policies


def _ms_to_ns(value):
    return None if value is None else int(float(value) * 1_000_000)


def _ambiguous_scope(declared, identities):
    scopes = identities.get(declared["service_id"], ())
    candidates = [(project, namespace) for project, namespace in scopes
                  if declared.get("project_id", project) == project
                  and declared.get("namespace", namespace) == namespace]
    return len(candidates) > 1


def _matches_call(label: Mapping, config: Mapping, call: Mapping) -> bool:
    caller, callee = label.get("caller") or {}, label.get("callee") or {}
    owner, target = config.get("service_id"), call.get("target") or {}
    # P2 represents an unlinked callee as a string. Accept it only when the
    # observed address alias exactly equals the configured service identifier.
    if not isinstance(caller, Mapping) or not owner or caller.get("service_name") != owner:
        return False
    if any(config.get(k) is not None and config[k] != caller.get(k)
           for k in ("namespace", "project_id")):
        return False
    if config.get("environment_id") is not None and config["environment_id"] != caller.get("environment"):
        return False
    if not target.get("service_id") or not target.get("method") or not target.get("route"):
        return False
    if isinstance(callee, Mapping):
        if (callee.get("service_name") != target["service_id"]
                or any(target.get(k) is not None and target[k] != callee.get(k)
                       for k in ("namespace", "project_id"))):
            return False
    elif callee != target["service_id"] or target.get("namespace") is not None or target.get("project_id") is not None:
        return False
    # Stand's routes is authoritative. Its singular route is the first display
    # route for a service-wide call_ref on older snapshots.
    if "routes" in call:
        if not isinstance(call["routes"], list) or label.get("caller_operation") not in call["routes"]:
            return False
    else:
        routes = config.get("routes", [])
        if isinstance(routes, Mapping):
            routes = [dict(settings, route=route) for route, settings in routes.items()]
        if routes and all(isinstance(row.get("calls"), Mapping) for row in routes):
            if not any(row.get("route") == label.get("caller_operation")
                       and call.get("call_ref") in row["calls"] for row in routes):
                return False
    method, _, route = (label.get("operation") or "").partition(" ")
    return method == target["method"] and route == target["route"]


def _incoming_timeout(configs: Mapping, owner: Mapping, label: Mapping, environment_id: str):
    """Resolve the one declared incoming call to this observed owner route."""
    service_id = owner.get("service_id")
    method, _, route = (label.get("caller_operation") or "").partition(" ")
    if not method or not route or label.get("mixed_upstream"):
        return None
    upstream = label.get("upstream")
    matches = []
    for parent in configs.values():
        if parent.get("environment_id", environment_id) != environment_id:
            continue
        if upstream and (parent.get("service_id") != upstream["caller"]["service_name"]
                         or any(parent.get(k) is not None and parent[k] != upstream["caller"].get(k)
                                for k in ("namespace", "project_id"))):
            continue
        for call in parent.get("calls", []):
            if upstream and upstream.get("call_ref") and call.get("call_ref") != upstream["call_ref"]:
                continue
            target = call.get("target") or {}
            if (target.get("service_id") == service_id and
                    target.get("method") == method and target.get("route") == route and
                    all(target.get(k) is None or target[k] == label["caller"].get(k)
                        for k in ("namespace", "project_id"))):
                matches.append({"service_id": parent.get("service_id"), "call_ref": call.get("call_ref"),
                                "revision": parent.get("revision") or parent.get("config_revision"),
                                "timeout_ms": call.get("timeout_ms")})
    if len(matches) == 1 and all(v is not None for v in matches[0].values()):
        return matches[0]
    return None


def _comparison_pair(left: Mapping, right: Mapping) -> tuple[dict, dict]:
    """Validate A's complete frozen snapshots before selecting a changed call."""
    from comparison.runs import validate_run_collections

    before, after = dict(left), dict(right)
    validate_run_collections(before, after)
    if any(not isinstance(run.get("run_id"), str) or not run["run_id"] for run in (before, after)):
        raise AnalyticsError("invalid_run", "Missing frozen run ID")
    if "config_snapshots" in before or "config_snapshots" in after:
        old, new = (_product_snapshots(run) for run in (before, after))
        if old.keys() != new.keys():
            raise AnalyticsError("runs_not_comparable", "Configured services differ")
        changed = [key for key in old if old[key][0] != new[key][0]]
        if len(changed) != 1:
            raise AnalyticsError("runs_not_comparable", "Exactly one service policy must change")
        service_id = changed[0]
        before["config_snapshot"] = old[service_id][1]
        after["config_snapshot"] = new[service_id][1]

    # The comparator checks every setting of this service, including additions
    # and deletions, and validates any expected change supplied by the caller.
    if after.get("expected_change") is None:
        old_calls, new_calls = ({row["call_ref"]: row for row in run.get("config_snapshot", {}).get("edges", [])}
                                for run in (before, after))
        differences = [(ref, field) for ref in old_calls.keys() & new_calls.keys()
                       for field in old_calls[ref].keys() | new_calls[ref].keys()
                       if old_calls[ref].get(field) != new_calls[ref].get(field)]
        if len(differences) == 1:
            ref, field = differences[0]
            after["expected_change"] = {"service_id": after["config_snapshot"]["service_id"],
                                        "call_ref": ref, "field": field}
    return before, after


def _product_snapshots(run):
    snapshots = run.get("config_snapshots")
    if not isinstance(snapshots, dict) or not snapshots:
        raise AnalyticsError("runs_not_comparable", "Missing frozen service configurations")
    revisions = run.get("config_revisions")
    if revisions is not None and (not isinstance(revisions, dict) or revisions.keys() != snapshots.keys()):
        raise AnalyticsError("runs_not_comparable", "Incomplete service revision map")
    result = {}
    for service_id, snapshot in snapshots.items():
        if not isinstance(snapshot, dict) or snapshot.get("service_id") != service_id:
            raise AnalyticsError("runs_not_comparable", "Snapshot service identity is inconsistent")
        config = _product_snapshot(snapshot)
        if revisions is not None and revisions.get(service_id) != config["revision"]:
            raise AnalyticsError("runs_not_comparable", "Snapshot differs from the run's revision map")
        result[service_id] = ({k: v for k, v in config.items() if k != "revision"}, config)
    return result


def _product_snapshot(snapshot: Mapping) -> dict:
    # These fields describe acquisition, access and display. Unknown policy
    # settings are kept, including the target, route topology and retry_on.
    metadata = {"revision", "config_revision", "calls", "routes", "display_name", "is_lab",
                "read_config", "write_fields", "write_enabled", "apply_recommendation",
                "capabilities", "fetched_at_ns", "freshness"}
    config = {k: v for k, v in snapshot.items() if k not in metadata}
    config["revision"] = snapshot.get("revision") or snapshot.get("config_revision")
    if not config["revision"]:
        raise AnalyticsError("runs_not_comparable", "Missing historical service revision")
    calls = {}
    for call in snapshot.get("calls", []):
        ref = call.get("call_ref")
        if not isinstance(ref, str) or not ref or ref in calls:
            raise AnalyticsError("runs_not_comparable", "Missing or duplicate call reference")
        calls[ref] = {k: v for k, v in call.items() if k != "write_fields"}
        if "retry_on" in calls[ref]:
            calls[ref]["retry_on"] = sorted(calls[ref]["retry_on"], key=str)
        if "routes" in calls[ref]:
            calls[ref]["routes"] = sorted(calls[ref]["routes"])

    routes = {}
    for route in snapshot.get("routes", []):
        name = route["route"]
        if name in routes:
            raise AnalyticsError("runs_not_comparable", "Duplicate configured route")
        settings = {k: v for k, v in route.items() if k not in {"route", "write_fields", "calls"}}
        if "calls" in route:
            # A includes the shared policy both at the top level and inside
            # each route. Keep route membership and route-only fields (e.g.
            # required), but count the same max_attempts edit only once.
            settings["calls"] = {}
            for ref, nested in route["calls"].items():
                if ref not in calls:
                    raise AnalyticsError("runs_not_comparable", "Route references an unknown call")
                local = {}
                for field, value in nested.items():
                    if field not in calls[ref]:
                        local[field] = value
                    else:
                        if field == "retry_on":
                            value = sorted(value, key=str)
                        if calls[ref][field] != value:
                            raise AnalyticsError("runs_not_comparable", "Conflicting copies of a call policy")
                settings["calls"][ref] = local
        routes[name] = settings

    edges = []
    for ref in sorted(calls):
        row = calls[ref]
        if "timeout_ms" in row:
            row["per_attempt_timeout_ns"] = _ms_to_ns(row.pop("timeout_ms"))
        if "backoff_ms" in row:
            row["backoff"] = {"kind": "fixed", "base_ns": _ms_to_ns(row.pop("backoff_ms")), "jitter": "none"}
        edges.append(row)
    return {**config, "routes": routes, "edges": edges}
