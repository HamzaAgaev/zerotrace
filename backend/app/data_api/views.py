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

from backend.contracts.p2 import CallModel, NormalizedSpan, TraceGraph
from backend.contracts.p7 import SpanView, TraceDetail, TraceSummary, UiOutcome, UiSpanKind

HTTP_SERVER_ERROR_MIN = 500
FINDING_HYPOTHESES = frozenset({"retry", "hedge"})


def span_outcome(span: NormalizedSpan) -> UiOutcome:
    if span.otel_status == "error":
        return "error"
    if span.kind == "server" and span.http_status is not None:
        if span.http_status >= HTTP_SERVER_ERROR_MIN:
            return "error"
        return "ok"
    if span.otel_status == "ok":
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
    if len(graph.spans) > 1 and (
        reference in (None, "", "unknown") or any(s.clock_domain != reference for s in graph.spans)
    ):
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
    )


def finding_count(call_model: CallModel | None) -> int:
    if call_model is None:
        return 0
    return sum(1 for call in call_model.calls if call.hypothesis in FINDING_HYPOTHESES)


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
    return TraceSummary(
        trace_ref=graph.trace_ref,
        operation=(root.operation or "unknown") if root is not None else "unknown",
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
    return TraceDetail(
        summary=summary,
        spans=spans,
        quality_issues=quality_issue_codes(graph),
    )
