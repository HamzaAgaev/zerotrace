"""Data API handlers (P7) over the repository store and core.graph.

Pure functions, framework-free: the HTTP router (owned by A) calls them and
maps ApiError to an HTTP error body ({error:{code,message,retryable}}).

Store contract (the stateful repository layer returns NormalizedSpan and
TraceRef):

- list_traces(environment_id) -> Sequence[TraceMeta]
    Current (non-expired) revisions of all traces of the environment: one
    entry per (trace_id, generation) holding the latest revision.
    Order is unspecified.
- get_trace_meta(environment_id, trace_id, generation=None, revision=None)
    -> TraceMeta | None
    Metadata of the requested revision; generation and revision are passed
    only as a pair, both None resolves to the current revision. None when
    the ref is unavailable (unknown or expired by TTL).
- get_spans(trace_ref) -> Sequence[NormalizedSpan] | None
    Normalized spans of the exact revision; None when the revision became
    unavailable between resolve and read.

graph_builder is core.graph.build_graph(normalized_spans, trace_ref) ->
TraceGraph (delivered by the core stage; list_traces resolves it lazily,
routers and tests pass it explicitly).

finding_count is always 0 at this layer: retry findings are computed by
the audit pipeline, not by the data API.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from backend.contracts.base import TraceRef
from backend.contracts.p2 import NormalizedSpan, TraceGraph
from backend.contracts.p7 import Envelope
from backend.contracts.validate import TRACE_ID_RE, ZERO_TRACE_ID

from .views import trace_detail, trace_summary

SCHEMA_TRACE_LIST = "zerotrace.trace-list"
SCHEMA_TRACE = "zerotrace.trace"
SCHEMA_VERSION = "1.0.0"
PRODUCER_VERSION = "0.1.0"
MAX_LIMIT = 100


@dataclass(frozen=True)
class TraceMeta:
    trace_ref: TraceRef
    first_received_at_ns: int
    last_received_at_ns: int


class TraceStore(Protocol):
    def list_traces(self, environment_id: str) -> Sequence[TraceMeta]: ...

    def get_trace_meta(
        self,
        environment_id: str,
        trace_id: str,
        generation: int | None = None,
        revision: int | None = None,
    ) -> TraceMeta | None: ...

    def get_spans(self, trace_ref: TraceRef) -> Sequence[NormalizedSpan] | None: ...


GraphBuilder = Callable[[Sequence[NormalizedSpan], TraceRef], TraceGraph]


@dataclass(frozen=True)
class ApiError:
    status: int
    code: str
    message: str
    retryable: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
            }
        }


def _default_graph_builder() -> GraphBuilder:
    from backend.core.graph import build_graph

    return build_graph


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _envelope(schema: str, payload: dict[str, Any], generated_at: str | None) -> dict[str, Any]:
    envelope: Envelope[dict[str, Any]] = Envelope(
        schema=schema,
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at or _utc_now_iso(),
        producer_version=PRODUCER_VERSION,
        payload=payload,
    )
    return envelope.to_dict(payload)


def list_traces(
    store: TraceStore,
    environment_id: str,
    limit: int = MAX_LIMIT,
    offset: int = 0,
    *,
    graph_builder: GraphBuilder | None = None,
    generated_at: str | None = None,
) -> dict[str, Any] | ApiError:
    if limit < 1 or limit > MAX_LIMIT:
        return ApiError(400, "invalid_limit", f"limit must be within 1..{MAX_LIMIT}")
    if offset < 0:
        return ApiError(400, "invalid_offset", "offset must be >= 0")
    build = graph_builder if graph_builder is not None else _default_graph_builder()
    metas = sorted(
        store.list_traces(environment_id),
        key=lambda m: (-m.first_received_at_ns, m.trace_ref.trace_id),
    )
    items: list[dict[str, Any]] = []
    for meta in metas[offset : offset + limit]:
        spans = store.get_spans(meta.trace_ref)
        if spans is None:
            # Revision expired between list and read; offset pagination is
            # not snapshot-consistent, a documented first-slice limitation.
            continue
        graph = build(spans, meta.trace_ref)
        items.append(trace_summary(graph).to_dict())
    payload = {"items": items, "total": len(metas)}
    return _envelope(SCHEMA_TRACE_LIST, payload, generated_at)


def get_trace(
    store: TraceStore,
    graph_builder: GraphBuilder,
    environment_id: str,
    trace_id: str,
    generation: int | None = None,
    revision: int | None = None,
    *,
    generated_at: str | None = None,
) -> dict[str, Any] | ApiError:
    if (generation is None) != (revision is None):
        return ApiError(
            400,
            "version_pair_required",
            "generation and revision must be passed together or both omitted",
        )
    if not TRACE_ID_RE.match(trace_id) or trace_id == ZERO_TRACE_ID:
        return ApiError(400, "invalid_trace_id", "trace_id must be 32 lowercase hex, non-zero")
    meta = store.get_trace_meta(environment_id, trace_id, generation, revision)
    if meta is None:
        return ApiError(
            404,
            "trace_unavailable",
            f"trace {trace_id} is unavailable in environment {environment_id!r}",
        )
    spans = store.get_spans(meta.trace_ref)
    if spans is None:
        return ApiError(
            404,
            "trace_unavailable",
            f"trace {trace_id} revision is unavailable in environment {environment_id!r}",
        )
    graph = graph_builder(spans, meta.trace_ref)
    return _envelope(SCHEMA_TRACE, trace_detail(graph).to_dict(), generated_at)
