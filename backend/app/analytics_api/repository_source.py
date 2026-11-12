"""Consistent analytics reads backed by B's SQLite span repository."""
from __future__ import annotations

from collections import Counter, OrderedDict
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from threading import Lock

from app.data_api.views import TraceListEntry, trace_list_entry, trace_summary
from contracts import IngestLimits, TraceRef
from contracts.p7 import TraceSummary
from core.calls import DEFAULT_GROUPING_VERSION, OPERATION_NORMALIZATION_VERSION, derive_calls
from core.graph import build_graph
from repository.store import SpanStore, TraceMeta

from analytics_support import AnalyticsError, digest, ref_key

from .p2_adapter import analytics_trace, merge_edge_labels


class _TraceCache:
    """Keep reusable derived traces bounded by both entries and span count."""

    max_entries = 16_384
    max_spans = 120_000

    def __init__(self):
        self.entries: OrderedDict[TraceMeta, tuple[TraceSummary, dict]] = OrderedDict()
        self.summaries: OrderedDict[TraceMeta, TraceSummary] = OrderedDict()
        self.list_entries: OrderedDict[TraceMeta, TraceListEntry] = OrderedDict()
        self.spans = 0
        self.lock = Lock()
        self.build_locks = tuple(Lock() for _ in range(64))

    def get(self, meta: TraceMeta) -> tuple[TraceSummary, dict] | None:
        with self.lock:
            entry = self.entries.get(meta)
            if entry is not None:
                self.entries.move_to_end(meta)
            return entry

    def put(self, meta: TraceMeta, entry: tuple[TraceSummary, dict]) -> None:
        if meta.span_count > self.max_spans:
            return
        with self.lock:
            if meta in self.entries:
                return
            self.entries[meta] = entry
            self.summaries.pop(meta, None)
            self.spans += meta.span_count
            while len(self.entries) > self.max_entries or self.spans > self.max_spans:
                removed, _ = self.entries.popitem(last=False)
                self.spans -= removed.span_count

    def get_summary(self, meta: TraceMeta) -> TraceSummary | None:
        with self.lock:
            full = self.entries.get(meta)
            if full is not None:
                self.entries.move_to_end(meta)
                return full[0]
            summary = self.summaries.get(meta)
            if summary is not None:
                self.summaries.move_to_end(meta)
            return summary

    def put_summary(self, meta: TraceMeta, summary: TraceSummary) -> None:
        with self.lock:
            if meta not in self.entries:
                self.summaries[meta] = summary
                if len(self.summaries) > self.max_entries:
                    self.summaries.popitem(last=False)

    def get_list_entries(self, metas: list[TraceMeta]) -> dict[TraceRef, TraceListEntry]:
        with self.lock:
            entries = {}
            for meta in metas:
                entry = self.list_entries.get(meta)
                if entry is not None:
                    entries[meta.trace_ref] = entry
                    self.list_entries.move_to_end(meta)
            return entries

    def put_list_entries(self, entries: dict[TraceMeta, TraceListEntry]) -> None:
        with self.lock:
            self.list_entries.update(entries)
            while len(self.list_entries) > self.max_entries:
                self.list_entries.popitem(last=False)


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
        self._trace_cache = _TraceCache()
        self._cache_lock = Lock()
        self._trace_deletion_version = self.store.trace_deletion_version
        self._pinned_view = ContextVar("analytics_repository_view", default=None)

    @contextmanager
    def read(self):
        pinned = self._pinned_view.get()
        if pinned is not None:
            yield pinned
            return
        with self.store.read_snapshot():
            control = deepcopy(self.control_snapshot()) if self.control_snapshot else {}
            version = self.store.trace_deletion_version
            with self._cache_lock:
                if version > self._trace_deletion_version:
                    self._trace_cache = _TraceCache()
                    self._trace_deletion_version = version
                cache = self._trace_cache if version == self._trace_deletion_version else _TraceCache()
            yield RepositoryView(self.store, self.limits, control, cache)

    @contextmanager
    def pin(self):
        """Share one exact repository and control view across a product request."""
        with self.read() as view:
            token = self._pinned_view.set(view)
            try:
                yield view
            finally:
                self._pinned_view.reset(token)

    def close(self):
        if self._owned_store:
            self.store.close()


class RepositoryView:
    def __init__(
        self, span_store: SpanStore, limits: IngestLimits, control: dict, trace_cache: _TraceCache,
    ):
        self.span_store = span_store
        self.limits = limits
        self.control = control
        self._trace_cache = trace_cache
        self._metas = {}
        self._current: dict[str, list[TraceMeta]] = {}
        self._current_by_ref: dict[str, dict[TraceRef, TraceMeta]] = {}
        self._identities = {}
        self._derived_entries: dict[TraceMeta, tuple[TraceSummary, dict]] = {}
        self._traces = {}
        self._environments = {}
        self._configure = {}

    def configure_policies(self, environment_id, configs, resolver):
        settings = self.control.setdefault("environments", {}).setdefault(environment_id, {})
        settings["config_snapshots"] = configs
        self._configure[environment_id] = resolver
        self._environments.pop(environment_id, None)

    def current_metas(self, environment_id: str) -> list[TraceMeta]:
        if environment_id not in self._current:
            metas = self.span_store.list_trace_metas(environment_id)
            self._current[environment_id] = metas
            self._current_by_ref[environment_id] = {meta.trace_ref: meta for meta in metas}
            # Pin existing hits before the scan. Misses can evict shared entries,
            # but the current request still reuses everything it has loaded.
            for meta in metas:
                cached = self._trace_cache.get(meta)
                if cached is not None:
                    self._derived_entries[meta] = cached
        return self._current[environment_id]

    def _environment_metas(self, environment_id):
        if environment_id not in self._metas:
            metas = self.current_metas(environment_id)
            if not metas and environment_id not in self.control.get("environments", {}):
                raise AnalyticsError("unknown_environment", "Environment unavailable", 404)
            if len({m.project_id for m in metas}) > 1:
                raise AnalyticsError("ambiguous_environment", "Environment contains multiple projects", 409)
            self._metas[environment_id] = sorted(metas, key=lambda m: ref_key(m.trace_ref.to_dict()))
        return self._metas[environment_id]

    def _input_identity(self, environment_id):
        if environment_id not in self._identities:
            self._identities[environment_id] = [
                (m.project_id, m.trace_ref.to_dict(), m.assembly_state, m.span_count)
                for m in self._environment_metas(environment_id)
            ]
        return self._identities[environment_id]

    def audit_cache_key(self, environment_id):
        settings = self.control.get("environments", {}).get(environment_id, {})
        if settings.get("baselines") and not settings.get("generated_at"):
            return None  # Baselines can expire even when the exact input refs stay the same.
        return digest([environment_id, self._input_identity(environment_id),
                       self.span_store.trace_deletion_version, self.limits.to_dict(), settings])

    def _derived(self, meta: TraceMeta):
        cached = self._derived_entries.get(meta)
        if cached is not None:
            return cached
        lock = self._trace_cache.build_locks[hash(meta) % len(self._trace_cache.build_locks)]
        with lock:
            entry = self._trace_cache.get(meta)
            if entry is None:
                graph = self._graph(meta)
                if graph is None:
                    return None
                calls = derive_calls(graph, project_id=meta.project_id)
                trace = analytics_trace(graph, calls, project_id=meta.project_id)
                entry = (trace_summary(graph), trace)
                self._trace_cache.put(meta, entry)
            self._derived_entries[meta] = entry
            return entry

    def _graph(self, meta: TraceMeta):
        spans = self.span_store.get_spans(meta.trace_ref)
        if spans is None:
            return None
        return build_graph(spans, meta.trace_ref, meta.first_received_at_ns, meta.last_received_at_ns,
                           self.limits, assembly_state=meta.assembly_state,
                           forced_reason=meta.forced_reason)

    def trace_summary(self, ref: TraceRef, *, summary_only=False) -> TraceSummary | None:
        self.current_metas(ref.environment_id)
        meta = self._current_by_ref[ref.environment_id].get(ref)
        if meta is None:
            return None
        if summary_only:
            summary = self._trace_cache.get_summary(meta)
            if summary is None:
                graph = self._graph(meta)
                if graph is None:
                    return None
                summary = trace_summary(graph)
                self._trace_cache.put_summary(meta, summary)
            return summary
        entry = self._derived(meta)
        return entry[0] if entry is not None else None

    def trace_list_entries(self, environment_id: str) -> dict[TraceRef, TraceListEntry]:
        metas = self.current_metas(environment_id)
        entries = self._trace_cache.get_list_entries(metas)
        missing = [meta for meta in metas if meta.trace_ref not in entries]
        if missing:
            by_ref = self._current_by_ref[environment_id]
            fresh = {
                by_ref[ref]: trace_list_entry(ref, spans)
                for ref, spans in self.span_store.trace_list_spans(environment_id, missing)
            }
            self._trace_cache.put_list_entries(fresh)
            entries.update((meta.trace_ref, entry) for meta, entry in fresh.items())
        return entries

    def _adapt(self, meta):
        ref = meta.trace_ref
        if ref not in self._traces:
            entry = self._derived(meta)
            if entry is None:
                raise AnalyticsError("trace_not_found", "Trace unavailable", 404, ref.to_dict())
            trace = entry[1]
            settings = self.control.get("environments", {}).get(ref.environment_id, {})
            if "sampling" in settings:
                trace = {**trace, "sampling": settings["sampling"]}
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
        if environment_id in self._environments:
            return self._environments[environment_id]
        metas = self._environment_metas(environment_id)
        settings = self.control.get("environments", {}).get(environment_id, {})
        traces = [self._adapt(m) for m in metas]
        edges = {}
        for trace in traces:
            merge_edge_labels(edges, trace["edges"])
        if environment_id in self._configure:
            settings = {**settings, "policies": self._configure[environment_id](
                settings["config_snapshots"], environment_id, edges
            )}
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
        environment = {"model": model, **{k: settings[k] for k in ("policies", "thresholds", "baselines",
                "trace_capabilities", "capabilities", "journal_summary") if k in settings}}
        self._environments[environment_id] = environment
        return environment

    def run(self, run_id):
        runs = self.control.get("runs", {})
        if run_id not in runs:
            raise AnalyticsError("run_not_found", "Run metadata unavailable", 404)
        return runs[run_id]
