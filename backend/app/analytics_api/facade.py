"""Product-facing C facade used by the common A server.

The facade deliberately receives its telemetry repository and A's immutable
control records from the host.  C therefore never invents traces, runs or
configuration revisions.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from threading import RLock

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
        return {"environments": environments, "runs": runs}
    return {"environments": {}, "runs": {}}


class AnalyticsFacade:
    def __init__(self, telemetry_spanstore, control_records: object,
                 *, results: ResultStore | None = None):
        self._control_records = control_records
        self.source = RepositorySource(telemetry_spanstore, control_snapshot=self._snapshot)
        self.results = results or LocalResultStore()
        self.service = AnalyticsService(self.source, self.results)
        self._lock = RLock()

    def _snapshot(self) -> dict:
        return _records_snapshot(self._control_records)

    def _with_configs(self, environment_id: str, configs: Mapping | None, edges=None) -> Callable[[], dict]:
        supplied = deepcopy(dict(configs or {}))
        def snapshot():
            control = self._snapshot()
            environments = control.setdefault("environments", {})
            env = environments.setdefault(environment_id, {})
            if supplied:
                # Rules consume normalised policies.  Keep raw product configs as
                # evidence too; A remains the authority for revisions.
                env["policies"] = _policies(supplied, environment_id, edges or {})
                env["config_snapshots"] = supplied
                env["environment_kind"] = "lab" if all(config.get("is_lab") for config in supplied.values()) else "external"
            return control
        return snapshot

    def audit(self, environment_id: str, configs_dict: Mapping | None = None):
        with self._lock:
            with self.source.read() as view:
                edges = view.environment(environment_id)["model"].get("edges", {})
            old = self.source.control_snapshot
            self.source.control_snapshot = self._with_configs(environment_id, configs_dict, edges)
            try:
                return self.service.audit(environment_id)
            finally:
                self.source.control_snapshot = old

    def resolve_recommendation(self, environment_id: str, occurrence_id: str,
                               recommendation_id: str, configs_dict: Mapping | None = None):
        self.audit(environment_id, configs_dict)
        record = self.service.recommendation(environment_id, occurrence_id, recommendation_id)
        recommendation = record["recommendation"]
        intents = recommendation.get("intents", [])
        if len(intents) != 1 or not isinstance(intents[0], dict):
            raise AnalyticsError("invalid_recommendation", "Recommendation has no trusted apply intent", 409)
        return intents[0]

    def compare_runs(self, run_left: Mapping, run_right: Mapping):
        # A passes frozen trace refs in these run records. Inject them only for
        # this calculation so comparison can never drift to a live trace set.
        with self._lock:
            old = self.source.control_snapshot
            left, right = _comparison_pair(run_left, run_right)
            call_refs_by_edge = {}
            edges_by_run = {}
            with self.source.read() as view:
                for run in (left, right):
                    run_edges = {}
                    for ref in run["trace_collection"]["trace_refs"]:
                        run_edges.update(view.trace(ref)["edges"])
                    edges_by_run[run["run_id"]] = run_edges
                    call_refs_by_edge[run["run_id"]] = {}
                    product = run.get("config_snapshots", {}).get(
                        run.get("config_snapshot", {}).get("service_id"))
                    if product is None:
                        continue
                    calls = {call["call_ref"]: call for call in product.get("calls", [])}
                    candidates = {}
                    # Bind only when the exact configured owner/target/operation
                    # identifies one call and one observed B edge in this run.
                    for call_ref, call in calls.items():
                        matches = [edge_id for edge_id, label in run_edges.items()
                                   if _matches_call(label, product, call)]
                        for edge_id in matches:
                            candidates.setdefault(edge_id, []).append(call_ref)
                    call_refs_by_edge[run["run_id"]] = {
                        edge_id: refs[0] for edge_id, refs in candidates.items() if len(refs) == 1
                    }
            for run in (left, right):
                snapshot = run["config_snapshot"]
                product = run.get("config_snapshots", {}).get(snapshot["service_id"])
                if product is None:
                    continue
                calls = {call["call_ref"]: call for call in product.get("calls", [])}
                for row in snapshot["edges"]:
                    call = calls[row["call_ref"]]
                    matches = [edge_id for edge_id, label in edges_by_run[run["run_id"]].items()
                               if _matches_call(label, product, call)]
                    if (len(matches) == 1 and
                            call_refs_by_edge.get(run["run_id"], {}).get(matches[0]) == row["call_ref"]):
                        row["edge_id"] = matches[0]
                run["analytics_call_refs_by_edge"] = call_refs_by_edge.get(run["run_id"], {})
            def snapshot():
                control = self._snapshot()
                control.setdefault("runs", {}).update({left["run_id"]: left, right["run_id"]: right})
                return control
            self.source.control_snapshot = snapshot
            try:
                return self.service.compare({"baseline_run_id": left["run_id"], "candidate_run_id": right["run_id"]})
            finally:
                self.source.control_snapshot = old

    def report(self, environment_id: str, configs_dict: Mapping | None = None):
        with self._lock:
            with self.source.read() as view:
                edges = view.environment(environment_id)["model"].get("edges", {})
            old = self.source.control_snapshot
            self.source.control_snapshot = self._with_configs(environment_id, configs_dict, edges)
            try:
                return self.service.report(environment_id)
            finally:
                self.source.control_snapshot = old


def _policies(configs: Mapping, environment_id: str, edges: Mapping) -> list[dict]:
    """Translate product P4 configs to C's explicitly sourced edge-policy input."""
    policies = []
    for service_id, config in configs.items():
        revision = config.get("revision") or config.get("config_revision")
        for call in config.get("calls", []):
            target = call.get("target", {})
            for edge_id, label in edges.items():
                if not _matches_call(label, config, call):
                    continue
                budget = (call.get("budget_ms") or call.get("parent_budget_ms")
                          or _parent_budget_ms(configs, config, label, call))
                policies.append({
                    "edge_id": edge_id, "source": "runtime", "execution": "serial",
                    "service_id": config.get("service_id", service_id), "call_ref": call.get("call_ref"),
                    "target": target, "per_attempt_timeout_ns": _ms_to_ns(call.get("timeout_ms")),
                    "max_attempts": call.get("max_attempts"),
                    "budget_ns": _ms_to_ns(budget),
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


def _matches_call(label: Mapping, config: Mapping, call: Mapping) -> bool:
    caller, callee = label.get("caller") or {}, label.get("callee") or {}
    owner, target = config.get("service_id"), call.get("target") or {}
    # P2 represents an unlinked callee as a string. Accept it only when the
    # observed address alias exactly equals the configured service identifier.
    if not isinstance(caller, Mapping):
        return False
    if owner and caller.get("service_name") != owner:
        return False
    if target.get("service_id"):
        if isinstance(callee, Mapping):
            if callee.get("service_name") != target["service_id"]:
                return False
        elif callee != target["service_id"]:
            return False
    method, _, route = (label.get("operation") or "").partition(" ")
    return (not target.get("method") or method == target["method"]) and (not target.get("route") or route == target["route"])


def _parent_budget_ms(configs: Mapping, owner: Mapping, label: Mapping, call: Mapping | None = None):
    """Use a declared incoming call timeout, never a measured trace duration."""
    service_id = owner.get("service_id")
    # If B cannot link the parent SERVER span, the exact P4 route for this
    # configured outgoing call still identifies the declared parent timeout.
    configured_routes = (call or {}).get("routes") or [(call or {}).get("route")]
    fallback = configured_routes[0] if len(configured_routes) == 1 else None
    operation = label.get("caller_operation") or fallback
    if operation:
        method, _, route = operation.partition(" ")
    else:
        method = route = None
    matches = []
    for parent in configs.values():
        for call in parent.get("calls", []):
            target = call.get("target") or {}
            if (target.get("service_id") == service_id and
                    (method is None or not target.get("method") or target["method"] == method) and
                    (route is None or not target.get("route") or target["route"] == route) and
                    call.get("timeout_ms") is not None):
                matches.append(call["timeout_ms"])
    return matches[0] if len(matches) == 1 else None


def _comparison_pair(left: Mapping, right: Mapping) -> tuple[dict, dict]:
    """Select the one changed service from A's full immutable snapshots."""
    left_map, right_map = left.get("config_snapshots", {}), right.get("config_snapshots", {})
    changed = [key for key in left_map.keys() | right_map.keys()
               if digest(left_map.get(key)) != digest(right_map.get(key))]
    if len(changed) == 1:
        key = changed[0]
        left = {**left, "comparison_config": left_map.get(key)}
        right = {**right, "comparison_config": right_map.get(key)}
    before, after = _comparison_run(left), _comparison_run(right)
    old = {row["call_ref"]: row for row in before["config_snapshot"]["edges"]}
    new = {row["call_ref"]: row for row in after["config_snapshot"]["edges"]}
    differences = [(ref, field) for ref in old.keys() & new.keys()
                   for field in old[ref].keys() | new[ref].keys()
                   if old[ref].get(field) != new[ref].get(field)]
    if len(differences) == 1:
        call_ref, field = differences[0]
        after["expected_change"] = {"service_id": after["config_snapshot"]["service_id"],
                                   "call_ref": call_ref, "field": field,
                                   "before": old[call_ref].get(field), "after": new[call_ref].get(field)}
    return before, after


def _comparison_run(run: Mapping) -> dict:
    """Bridge A's multi-service snapshot to C's single changed-call P11 input."""
    result = dict(run)
    refs = result.get("trace_refs", [])
    result.setdefault("trace_collection", {
        "run_id": result.get("run_id"), "status": "ready",
        "expected_count": result.get("completed_requests", len(refs)),
        "received_count": len(refs), "trace_refs": refs,
    })
    snapshots = result.get("config_snapshots", {})
    if "config_snapshot" not in result and snapshots:
        # The comparison algorithm validates that precisely one addressed P4
        # field differs.  The common server selects/records that target before
        # invoking C, so use its supplied comparable snapshot when available.
        chosen = result.get("comparison_config")
        result["config_snapshot"] = _legacy_snapshot(chosen or next(iter(snapshots.values())))
    return result


def _legacy_snapshot(snapshot: Mapping) -> dict:
    """P11's current comparator names call rows `edges`; P4 calls are equivalent."""
    if "edges" in snapshot:
        return dict(snapshot)
    edges = []
    for call in snapshot.get("calls", []):
        edges.append({
            "call_ref": call.get("call_ref"), "edge_id": call.get("edge_id"),
            "per_attempt_timeout_ns": _ms_to_ns(call.get("timeout_ms")),
            "max_attempts": call.get("max_attempts"),
            "backoff": {"kind": "fixed", "base_ns": _ms_to_ns(call.get("backoff_ms", 0)), "jitter": "none"},
        })
    return {"service_id": snapshot.get("service_id"), "revision": snapshot.get("revision") or snapshot.get("config_revision"),
            "route_work": {route["route"]: route.get("work") for route in snapshot.get("routes", [])},
            "edges": edges}
