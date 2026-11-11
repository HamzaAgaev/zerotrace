"""Consistent analytics reads backed by B's SQLite span repository."""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

from contracts import IngestLimits
from core.calls import DEFAULT_GROUPING_VERSION, OPERATION_NORMALIZATION_VERSION, derive_calls
from core.graph import build_graph
from repository.store import SpanStore

from analytics_support import AnalyticsError, digest, ref_key

from .p2_adapter import analytics_trace


class RepositorySource:
    """Use an existing store or database path, optionally with a pinned control snapshot.

    control_snapshot() supplies environment settings and run metadata from A.
    In their absence analytics uses only the telemetry recorded by B.
    """

    def __init__(self, store: SpanStore | Path, *, limits: IngestLimits | None = None,
                 control_snapshot: Callable[[], dict] | None = None):
        self._owned_store = isinstance(store, Path)
        self.store = SpanStore(store) if self._owned_store else store
        self.limits = limits or IngestLimits()
        self.control_snapshot = control_snapshot

    @contextmanager
    def read(self):
        # SpanStore serialises writers with this re-entrant lock.  Holding it while
        # creating the analytics view makes a single audit internally consistent.
        lock = getattr(self.store, "_lock", None)
        if lock is not None:
            lock.acquire()
        try:
            control = deepcopy(self.control_snapshot()) if self.control_snapshot else {}
            yield RepositoryView(self.store, self.limits, control)
        finally:
            if lock is not None:
                lock.release()

    def close(self):
        if self._owned_store:
            self.store.close()


class RepositoryView:
    def __init__(self, span_store: SpanStore, limits: IngestLimits, control: dict):
        self.span_store = span_store
        self.limits = limits
        self.control = control
        self._metas = {}
        self._traces = {}

    def _environment_metas(self, environment_id):
        if environment_id not in self._metas:
            metas = self.span_store.list_trace_metas(environment_id)
            if not metas and environment_id not in self.control.get("environments", {}):
                raise AnalyticsError("unknown_environment", "Environment unavailable", 404)
            if len({m.project_id for m in metas}) > 1:
                raise AnalyticsError("ambiguous_environment", "Environment contains multiple projects", 409)
            self._metas[environment_id] = sorted(metas, key=lambda m: ref_key(m.trace_ref.to_dict()))
        return self._metas[environment_id]

    def _input_identity(self, environment_id):
        return [(m.project_id, m.trace_ref.to_dict(), m.assembly_state, m.span_count)
                for m in self._environment_metas(environment_id)]

    def audit_cache_key(self, environment_id):
        settings = self.control.get("environments", {}).get(environment_id, {})
        if settings.get("baselines") and not settings.get("generated_at"):
            return None  # Baselines can expire even when the exact input refs stay the same.
        return digest([environment_id, self._input_identity(environment_id),
                       self.limits.to_dict(), settings])

    def _adapt(self, meta):
        ref = meta.trace_ref
        if ref not in self._traces:
            spans = self.span_store.get_spans(ref)
            if spans is None:
                raise AnalyticsError("trace_not_found", "Trace unavailable", 404, ref.to_dict())
            graph = build_graph(spans, ref, meta.first_received_at_ns, meta.last_received_at_ns,
                                self.limits, assembly_state=meta.assembly_state)
            calls = derive_calls(graph, project_id=meta.project_id)
            trace = analytics_trace(graph, calls, project_id=meta.project_id)
            trace["project_id"] = meta.project_id
            settings = self.control.get("environments", {}).get(ref.environment_id, {})
            if "sampling" in settings:
                trace["sampling"] = settings["sampling"]
            for run in self.control.get("runs", {}).values():
                refs = run.get("trace_refs") or run.get("trace_collection", {}).get("trace_refs", [])
                if any(ref_key(item) == ref_key(ref.to_dict()) for item in refs):
                    for span in trace["spans"]:
                        if span.get("kind") == "server" and span.get("parent_span_id") is None:
                            span["run_id"] = run.get("run_id")
                            break
                    # The call_ref belongs to A's frozen config. B supplies the
                    # observed edge_id, so project the reference onto this view
                    # only through the facade's unique config-to-edge binding.
                    by_edge = run.get("analytics_call_refs_by_edge", {})
                    if by_edge:
                        spans_by_id = {span["span_id"]: span for span in trace["spans"]}
                        for attempt in trace["attempts"]:
                            call_ref = by_edge.get(attempt.get("edge_id"))
                            if call_ref is None:
                                continue
                            if attempt.get("call_ref") is None:
                                attempt["call_ref"] = call_ref
                            client = spans_by_id.get(attempt.get("attempt_id"))
                            if client is not None and client.get("call_ref") is None:
                                client["call_ref"] = call_ref
            self._traces[ref] = trace
        return self._traces[ref]

    def trace(self, ref):
        environment, trace_id, generation, revision = ref_key(ref)
        meta = self.span_store.resolve_trace(environment, trace_id, generation, revision)
        if meta is None:
            raise AnalyticsError("trace_not_found", "Requested trace generation unavailable", 404, ref)
        if meta.trace_ref.revision != revision:
            raise AnalyticsError("stale_revision", "Requested trace revision unavailable", 409, ref)
        return self._adapt(meta)

    def environment(self, environment_id):
        metas = self._environment_metas(environment_id)
        settings = self.control.get("environments", {}).get(environment_id, {})
        traces = [self._adapt(m) for m in metas]
        edges = {eid: edge for trace in traces for eid, edge in trace["edges"].items()}
        services = {}
        for trace in traces:
            for span in trace["spans"]:
                service = span["service"]
                if service:
                    identity = (service["project_id"], service["environment"], service["namespace"],
                                service["service_name"])
                    services[identity] = service
        quality = Counter(issue["code"] for trace in traces for issue in trace["quality"]["issues"])
        model = {k: settings[k] for k in ("environment_kind", "sampling", "workload_id",
                 "generated_at", "config_revision", "caller_version", "callee_version", "timeout_scope")
                 if k in settings}
        model.update(
            environment_id=environment_id, project_id=metas[0].project_id if metas else None,
            dataset_ref=digest([environment_id, self._input_identity(environment_id)]), traces=traces,
            normalization_version=OPERATION_NORMALIZATION_VERSION, grouping_version=DEFAULT_GROUPING_VERSION, edges=edges,
            services=[services[key] for key in sorted(services)],
            quality={"issues_by_code": dict(sorted(quality.items())),
                     "assembly_states": dict(Counter(m.assembly_state for m in metas))},
        )
        return {"model": model, **{k: settings[k] for k in ("policies", "thresholds", "baselines",
                "trace_capabilities", "capabilities", "journal_summary") if k in settings}}

    def run(self, run_id):
        runs = self.control.get("runs", {})
        if run_id not in runs:
            raise AnalyticsError("run_not_found", "Run metadata unavailable", 404)
        return runs[run_id]
