from __future__ import annotations

import re
from dataclasses import dataclass, field

from .p1 import IngestLimits
from .p2 import QUALITY_CODES, NormalizedSpan, TraceGraph

TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")
ZERO_TRACE_ID = "0" * 32
ZERO_SPAN_ID = "0" * 16

VALID_KINDS = {"internal", "server", "client", "producer", "consumer", "unspecified"}
VALID_STATUSES = {"unset", "ok", "error"}


@dataclass(frozen=True)
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_span(span: NormalizedSpan, report: ValidationReport) -> None:
    ctx = f"span {span.span_id!r}"
    if not TRACE_ID_RE.match(span.trace_id) or span.trace_id == ZERO_TRACE_ID:
        report.errors.append(f"{ctx}: invalid trace_id {span.trace_id!r}")
    if not SPAN_ID_RE.match(span.span_id) or span.span_id == ZERO_SPAN_ID:
        report.errors.append(f"{ctx}: invalid span_id")
    if span.parent_span_id is not None and (
        not SPAN_ID_RE.match(span.parent_span_id) or span.parent_span_id == ZERO_SPAN_ID
    ):
        report.errors.append(f"{ctx}: invalid parent_span_id")
    if span.kind not in VALID_KINDS:
        report.errors.append(f"{ctx}: unknown kind {span.kind!r}")
    if span.otel_status not in VALID_STATUSES:
        report.errors.append(f"{ctx}: unknown otel_status {span.otel_status!r}")
    if span.start_ns < 0 or span.end_ns < 0:
        report.errors.append(f"{ctx}: negative timestamp")
    if span.end_ns < span.start_ns:
        report.errors.append(f"{ctx}: end_ns {span.end_ns} < start_ns {span.start_ns}")
    elif span.duration_ns != span.end_ns - span.start_ns:
        report.errors.append(
            f"{ctx}: duration_ns {span.duration_ns} != end-start {span.end_ns - span.start_ns}"
        )
    if span.http_status is not None and not 100 <= span.http_status <= 599:
        report.errors.append(f"{ctx}: http_status {span.http_status} out of range")
    if span.resend_count is not None and span.resend_count < 0:
        report.errors.append(f"{ctx}: negative resend_count")
    if not span.service:
        report.warnings.append(f"{ctx}: missing service.name (coverage)")
    if span.kind in ("client", "server") and not span.operation:
        report.warnings.append(f"{ctx}: missing operation (coverage)")


def validate_trace_graph(graph: TraceGraph, limits: IngestLimits) -> ValidationReport:
    report = ValidationReport()
    if not TRACE_ID_RE.match(graph.trace_ref.trace_id):
        report.errors.append(f"trace: invalid trace_id {graph.trace_ref.trace_id!r}")
    if graph.trace_ref.generation < 1:
        report.errors.append("trace: generation must be >= 1")
    if graph.trace_ref.revision < 1:
        report.errors.append("trace: revision must be >= 1")
    if graph.last_received_at_ns < graph.first_received_at_ns:
        report.errors.append("trace: last_received before first_received")
    if len(graph.spans) > limits.max_spans_per_trace:
        report.errors.append(
            f"trace: {len(graph.spans)} spans exceeds limit {limits.max_spans_per_trace}"
        )

    seen_ids: set[str] = set()
    span_ids = {s.span_id for s in graph.spans}
    for span in graph.spans:
        validate_span(span, report)
        if span.span_id in seen_ids:
            report.errors.append(f"trace: duplicate span_id {span.span_id!r}")
        seen_ids.add(span.span_id)

    for span_id, parent in graph.parent_index.items():
        if span_id not in span_ids:
            report.errors.append(f"trace: parent_index entry for unknown span {span_id!r}")
        if parent is not None and parent not in span_ids:
            report.warnings.append(
                f"span {span_id!r}: parent {parent!r} missing from trace (missing_parent)"
            )
    for span_id, children in graph.children_index.items():
        if span_id not in span_ids:
            report.errors.append(f"trace: children_index entry for unknown span {span_id!r}")
        for child in children:
            if child not in span_ids:
                report.errors.append(f"trace: children_index references unknown span {child!r}")
    for root in graph.roots:
        if root not in span_ids:
            report.errors.append(f"trace: root {root!r} not in spans")

    for issue in graph.quality.issues:
        if issue.code not in QUALITY_CODES:
            report.warnings.append(f"trace: non-registry quality code {issue.code!r}")
        for sid in issue.span_ids:
            if sid not in span_ids:
                report.warnings.append(f"quality issue {issue.code}: unknown span {sid!r}")
    return report
