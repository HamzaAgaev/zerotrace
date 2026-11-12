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
from time import time_ns
from typing import Any, Protocol

from contracts.base import TraceRef
from contracts.p2 import NormalizedSpan, TraceGraph
from contracts.p7 import Envelope, TraceSummary
from contracts.validate import SPAN_ID_RE, TRACE_ID_RE, ZERO_SPAN_ID, ZERO_TRACE_ID

from .views import HTTP_METHOD_TOKEN, TraceListEntry, trace_detail, trace_summary

SCHEMA_TRACE_LIST = "zerotrace.trace-list"
SCHEMA_TRACE = "zerotrace.trace"
SCHEMA_VERSION = "1.0.0"
PRODUCER_VERSION = "0.1.0"
MAX_LIMIT = 100
SUCCESS_WINDOWS_MINUTES = (5, 30, 60, 180)


@dataclass(frozen=True)
class TraceMeta:
    trace_ref: TraceRef
    first_received_at_ns: int
    last_received_at_ns: int


class TraceStore(Protocol):
    def list_traces(self, environment_id: str) -> Sequence[TraceMeta]: ...

    def trace_refs_for_span(self, environment_id: str, span_id: str) -> set[TraceRef]: ...

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
    from core.graph import build_graph

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
    read_summary: Callable[[TraceRef], TraceSummary | None] | None = None,
    read_entries: Callable[[str], dict[TraceRef, TraceListEntry]] | None = None,
    query: str = "",
    service: str = "",
    status: str = "",
    method: str = "",
    span_id: str | None = None,
    start_from_ns: int | None = None,
    start_to_ns: int | None = None,
    sort: str | None = None,
    include_success_rates: bool = False,
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
    if status and status not in {"ok", "error", "unknown"}:
        return ApiError(400, "invalid_status", "status must be ok, error or unknown")
    if sort is not None and sort not in {"recent", "slow"}:
        return ApiError(400, "invalid_sort", "sort must be recent or slow")

    if method and not HTTP_METHOD_TOKEN.fullmatch(method):
        return ApiError(400, "invalid_method", "method must be an HTTP method token")

    if span_id is not None and (not SPAN_ID_RE.fullmatch(span_id) or span_id == ZERO_SPAN_ID):
        return ApiError(
            400, "invalid_span_id", "span_id must be 16 lowercase hex characters and nonzero"
        )
    if start_from_ns is not None and start_to_ns is not None and start_from_ns >= start_to_ns:
        return ApiError(400, "invalid_time_range", "start_from_ns must be less than start_to_ns")

    selected_method = method.upper()
    has_period = start_from_ns is not None or start_to_ns is not None
    expanded = bool(
        query or service or status or method or sort is not None or span_id or has_period
    )
    span_matches = (
        store.trace_refs_for_span(environment_id, span_id) if span_id is not None else None
    )
    scan_all = expanded or include_success_rates
    entries = read_entries(environment_id) if scan_all and read_entries is not None else None

    def load_summary(ref: TraceRef) -> TraceSummary | None:
        if read_summary is not None:
            return read_summary(ref)
        spans = store.get_spans(ref)
        return trace_summary(build(spans, ref)) if spans is not None else None

    items: list[TraceSummary | TraceListEntry] = []
    services: set[str] = set()
    methods: set[str] = set()
    outcomes = {"ok": 0, "error": 0, "unknown": 0}
    now_ns = time_ns() if include_success_rates else 0
    cutoffs = {minutes: now_ns - minutes * 60_000_000_000
               for minutes in SUCCESS_WINDOWS_MINUTES} if include_success_rates else {}
    successes = dict.fromkeys(cutoffs, 0)
    known = dict.fromkeys(cutoffs, 0)
    search = query.strip().casefold()
    for meta in metas if scan_all else metas[offset : offset + limit]:
        summary = (
            entries.get(meta.trace_ref) if entries is not None else load_summary(meta.trace_ref)
        )
        if summary is None:
            continue
        if 0 < summary.start_ns <= now_ns and summary.outcome != "unknown":
            for minutes, cutoff in cutoffs.items():
                if summary.start_ns >= cutoff:
                    known[minutes] += 1
                    successes[minutes] += summary.outcome == "ok"
        if expanded:
            services.add(summary.service)
            if summary.http_method is not None:
                methods.add(summary.http_method)
            if span_matches is not None and meta.trace_ref not in span_matches:
                continue
            if has_period and (
                summary.start_ns == 0
                or (start_from_ns is not None and summary.start_ns < start_from_ns)
                or (start_to_ns is not None and summary.start_ns >= start_to_ns)
            ):
                continue
            if selected_method and summary.http_method != selected_method:
                continue
            searchable = f"{summary.operation} {summary.service} {summary.trace_id}".casefold()
            if search not in searchable or (service and summary.service != service):
                continue
            if status and summary.outcome != status:
                continue
            outcomes[summary.outcome] += 1
        items.append(summary)

    if sort == "slow":
        items.sort(key=lambda item: (-item.duration_ns, item.trace_ref.trace_id))
    page = items[offset : offset + limit] if scan_all else items
    summaries = [
        item if isinstance(item, TraceSummary) else load_summary(item.trace_ref) for item in page
    ]
    payload: dict[str, Any] = {
        "items": [summary.to_dict() for summary in summaries if summary is not None],
        "total": len(metas),
    }
    if expanded:
        payload.update(
            total=len(items), services=sorted(services), methods=sorted(methods), outcomes=outcomes
        )
    if include_success_rates:
        payload["success_rates"] = {
            str(minutes): successes[minutes] / known[minutes] if known[minutes] else None
            for minutes in SUCCESS_WINDOWS_MINUTES
        }
    return _envelope(SCHEMA_TRACE_LIST, payload, generated_at)


def get_trace(
    store: TraceStore,
    graph_builder: GraphBuilder,
    environment_id: str,
    trace_id: str,
    generation: int | None = None,
    revision: int | None = None,
    *,
    include_span_kind: bool = True,
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
    return _envelope(
        SCHEMA_TRACE, trace_detail(graph).to_dict(include_span_kind=include_span_kind), generated_at
    )
