"""Adapter wiring repository.SpanStore to the data API store protocol.

The repository layer is the stateful owner of SQLite; this module bridges its
SpanStore to the TraceStore protocol consumed by handlers.py and provides a
graph_builder closure that supplies first/last received timestamps and ingest
limits to core.graph.build_graph.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence

from contracts.base import TraceRef
from contracts.p1 import IngestLimits
from contracts.p2 import NormalizedSpan
from repository.store import SpanStore, TraceMeta as RepositoryTraceMeta

from .handlers import GraphBuilder, TraceMeta


class SpanStoreAdapter:
    def __init__(
        self, store: SpanStore,
        list_metas: Callable[[str], Iterable[RepositoryTraceMeta]] | None = None,
    ) -> None:
        self._store = store
        self._list_metas = list_metas if list_metas is not None else store.list_trace_metas

    def list_traces(self, environment_id: str) -> Sequence[TraceMeta]:
        return [
            TraceMeta(
                trace_ref=m.trace_ref,
                first_received_at_ns=m.first_received_at_ns,
                last_received_at_ns=m.last_received_at_ns,
            )
            for m in self._list_metas(environment_id)
        ]

    def get_trace_meta(
        self,
        environment_id: str,
        trace_id: str,
        generation: int | None = None,
        revision: int | None = None,
    ) -> TraceMeta | None:
        meta = self._store.resolve_trace(environment_id, trace_id, generation, revision)
        if meta is None:
            return None
        return TraceMeta(
            trace_ref=meta.trace_ref,
            first_received_at_ns=meta.first_received_at_ns,
            last_received_at_ns=meta.last_received_at_ns,
        )

    def trace_refs_for_span(self, environment_id: str, span_id: str) -> set[TraceRef]:
        return self._store.trace_refs_for_span(environment_id, span_id)

    def get_spans(self, trace_ref: TraceRef) -> Sequence[NormalizedSpan] | None:
        return self._store.get_spans(trace_ref)


def make_graph_builder(store: SpanStore, limits: IngestLimits | None = None) -> GraphBuilder:
    from core.graph import build_graph

    resolved_limits = limits if limits is not None else IngestLimits()

    def build(spans: Sequence[NormalizedSpan], trace_ref: TraceRef):
        meta = store.resolve_trace(
            trace_ref.environment_id,
            trace_ref.trace_id,
            trace_ref.generation,
            trace_ref.revision,
        )
        first_received = meta.first_received_at_ns if meta is not None else 0
        last_received = meta.last_received_at_ns if meta is not None else 0
        return build_graph(
            list(spans),
            trace_ref,
            first_received,
            last_received,
            resolved_limits,
            assembly_state=meta.assembly_state if meta is not None else "open",
        )

    return build
