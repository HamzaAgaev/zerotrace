"""Conversion of TraceGraph/CallModel (P2) into frontend view DTOs (P7).

Derivation rules:
- span outcome: error when otel_status=error or http_status>=500 on SERVER;
  ok on an explicit success signal (otel_status=ok or a present SERVER
  http_status<500); otherwise unknown. CLIENT http_status is never used for
  derivation: CLIENT and SERVER statuses are not mixed.
- start_offset_ns is relative to the min start_ns among spans of the
  reference clock domain (the root span's domain); the frontend never
  subtracts clocks itself. Spans of other domains get a raw offset and the
  cross_clock_uncertain quality code.
- quality_issues are string codes from the QUALITY_CODES registry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from contracts.base import TraceRef
from contracts.p2 import CallModel, NormalizedSpan, TraceGraph
from contracts.p7 import SpanView, TraceDetail, TraceSummary, UiOutcome, UiSpanKind
from core.calls import parse_operation
from repository.store import TraceListSpan

HTTP_METHOD_TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")
HTTP_SERVER_ERROR_MIN = 500
FINDING_HYPOTHESES = frozenset({"retry", "hedge"})


def span_outcome(span: NormalizedSpan) -> UiOutcome:
    return _outcome(span.otel_status, span.kind, span.http_status)


def _outcome(otel_status: str, kind: str, http_status: int | None) -> UiOutcome:
    if otel_status == "error":
        return "error"
    if kind == "server" and http_status is not None:
        if http_status >= HTTP_SERVER_ERROR_MIN:
            return "error"
        return "ok"
    if otel_status == "ok":
        return "ok"
    return "unknown"


def ui_kind(kind: str) -> UiSpanKind:
    if kind == "server":
        return "server"
    if kind == "client":
        return "client"
    return "internal"


def root_span(graph: TraceGraph) -> NormalizedSpan | None:
    by_id = {s.span_id: s for s in graph.spans}
    roots = [by_id[r] for r in graph.roots if r in by_id]
    if not roots:
        return None
    return min(roots, key=lambda s: (s.start_ns, s.span_id))


def reference_clock_domain(graph: TraceGraph) -> str | None:
    root = root_span(graph)
    if root is not None:
        return root.clock_domain
    if graph.spans:
        earliest = min(graph.spans, key=lambda s: (s.start_ns, s.span_id))
        return earliest.clock_domain
    return None


def compatible_spans(graph: TraceGraph) -> list[NormalizedSpan]:
    reference = reference_clock_domain(graph)
    return [s for s in graph.spans if s.clock_domain == reference]


def trace_origin_ns(graph: TraceGraph) -> int | None:
    compatible = compatible_spans(graph)
    if not compatible:
        return None
    return min(s.start_ns for s in compatible)


def quality_issue_codes(graph: TraceGraph) -> list[str]:
    codes: list[str] = []

    def add(code: str) -> None:
        if code not in codes:
            codes.append(code)

    for issue in graph.quality.issues:
        add(issue.code)
    if len(graph.roots) > 1:
        add("multiple_roots")
    if graph.quality.source_loss is not None:
        add("source_loss")
    reference = reference_clock_domain(graph)
    if any(s.clock_domain != reference for s in graph.spans):
        add("cross_clock_uncertain")
    return codes


def span_view(span: NormalizedSpan, origin_ns: int) -> SpanView:
    return SpanView(
        span_id=span.span_id,
        parent_span_id=span.parent_span_id,
        service=span.service,
        name=span.name,
        kind=ui_kind(span.kind),
        start_offset_ns=span.start_ns - origin_ns,
        duration_ns=span.duration_ns,
        outcome=span_outcome(span),
        http_status=span.http_status,
        error_type=span.error_type,
        target=span.target if span.kind == "client" else None,
    )


def finding_count(call_model: CallModel | None) -> int:
    if call_model is None:
        return 0
    return sum(1 for call in call_model.calls if call.hypothesis in FINDING_HYPOTHESES)


def _operation_parts(operation: str, observed_http: bool) -> tuple[str | None, str]:
    method, endpoint = parse_operation(operation)
    if method is None and observed_http:
        prefix, _, path = operation.partition(" ")
        if HTTP_METHOD_TOKEN.fullmatch(prefix) and path.startswith("/"):
            return prefix.upper(), path.strip()
    return method, endpoint


@dataclass(frozen=True)
class TraceListEntry:
    trace_ref: TraceRef
    operation: str
    service: str
    start_ns: int
    duration_ns: int
    outcome: UiOutcome
    http_method: str | None
    services: tuple[str, ...] = ()
    root_duration_ns: int | None = None
    operations: tuple[dict, ...] = ()
    root_start_ns: int | None = None

    @property
    def trace_id(self) -> str:
        return self.trace_ref.trace_id


def trace_list_entry(ref: TraceRef, spans: list[TraceListSpan]) -> TraceListEntry:
    """Project the fields needed before pagination without constructing a graph."""
    root = min((span for span in spans if span.is_root), key=_span_start, default=None)
    reference = root if root is not None else min(spans, key=_span_start)
    compatible = [span for span in spans if span.clock_domain == reference.clock_domain]
    start_ns = min(span.start_ns for span in compatible)
    duration_ns = max(int(span.end_ns) for span in compatible) - start_ns
    services = tuple(sorted({s.service for s in spans if s.service}))
    operations = []
    children = {}
    for span in spans:
        children.setdefault(span.parent_span_id, []).append(span)
    for span in spans:
        if span.kind != 'server':
            continue
        intervals = sorted((max(span.start_ns, child.start_ns), min(int(span.end_ns), int(child.end_ns)))
                           for child in children.get(span.span_id, ()) if child.clock_domain == span.clock_domain)
        occupied, end = 0, span.start_ns
        for left, right in intervals:
            occupied += max(0, right - max(left, end))
            end = max(end, right)
        duration = max(0, int(span.end_ns) - span.start_ns)
        operations.append(dict(service=span.service, endpoint=span.operation, start_ns=span.start_ns,
                               duration_ns=duration, own_ns=max(0, duration-occupied),
                               outcome=_outcome(span.otel_status, span.kind, span.http_status)))
    if root is None:
        return TraceListEntry(ref, "unknown", "unknown", start_ns, duration_ns, "unknown", None, services, None, tuple(operations))
    operation = root.operation or "unknown"
    method, _ = _operation_parts(
        operation, root.http_status is not None or bool(root.operation_observed),
    )
    return TraceListEntry(
        ref, operation, root.service or "unknown", start_ns, duration_ns,
        _outcome(root.otel_status, root.kind, root.http_status), method, services,
        max(0, int(root.end_ns) - root.start_ns), tuple(operations), root.start_ns,
    )


def _span_start(span: TraceListSpan) -> tuple[int, str]:
    return span.start_ns, span.span_id


def trace_summary(graph: TraceGraph, call_model: CallModel | None = None) -> TraceSummary:
    root = root_span(graph)
    compatible = compatible_spans(graph)
    origin = trace_origin_ns(graph)
    if compatible and origin is not None:
        start_ns = origin
        duration_ns = max(s.end_ns for s in compatible) - origin
    else:
        start_ns = 0
        duration_ns = 0
    service_count = len({s.service for s in graph.spans if s.service})
    operation = (root.operation or "unknown") if root is not None else "unknown"
    method, endpoint = _operation_parts(operation, root is not None and (
        root.http_status is not None or bool(root.field_coverage.get("operation"))
    ))
    return TraceSummary(
        trace_ref=graph.trace_ref,
        operation=operation,
        http_method=method,
        endpoint=endpoint,
        service=(root.service or "unknown") if root is not None else "unknown",
        start_ns=start_ns,
        duration_ns=duration_ns,
        span_count=len(graph.spans),
        service_count=service_count,
        outcome=span_outcome(root) if root is not None else "unknown",
        finding_count=finding_count(call_model),
        quality_issues=quality_issue_codes(graph),
        http_status=root.http_status if root is not None else None,
    )


def trace_detail(graph: TraceGraph, call_model: CallModel | None = None) -> TraceDetail:
    summary = trace_summary(graph, call_model)
    origin = trace_origin_ns(graph)
    ordered = sorted(graph.spans, key=lambda s: (s.start_ns, s.end_ns, s.span_id))
    spans = [span_view(s, origin) for s in ordered] if origin is not None else []
    by_id = {span.span_id: span for span in graph.spans}
    compactable = {}
    for client in graph.spans:
        children = graph.children_index.get(client.span_id, [])
        if (
            client.kind != "client"
            or client.async_markers
            or len(children) != 1
        ):
            continue
        server = by_id.get(children[0])
        if (
            server is not None
            and server.kind == "server"
            and server.service != client.service
        ):
            compactable[client.span_id] = server.span_id
    return TraceDetail(
        summary=summary,
        spans=spans,
        quality_issues=quality_issue_codes(graph),
        compactable_client_spans=compactable or None,
    )
