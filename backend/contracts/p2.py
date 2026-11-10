from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from .base import TraceRef

SpanKind = Literal["internal", "server", "client", "producer", "consumer", "unspecified"]
OtelStatus = Literal["unset", "ok", "error"]
Idempotency = Literal["unknown", "present", "absent_observed"]
AssemblyState = Literal["open", "quiet", "forced"]
Outcome = Literal[
    "ok",
    "timeout",
    "fast_fail",
    "http_error",
    "grpc_error",
    "transport_error",
    "error_unknown",
    "outcome_unknown",
]
Hypothesis = Literal["single", "retry", "hedge", "ambiguous"]
Confidence = Literal["high", "medium", "low"]

QUALITY_CODES = frozenset(
    {
        "missing_parent",
        "multiple_roots",
        "cycle",
        "depth_limit",
        "cross_clock_uncertain",
        "missing_field",
        "conflicting_span",
        "source_loss",
    }
)


@dataclass(frozen=True)
class NormalizedSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    service: str
    name: str
    kind: SpanKind
    start_ns: int
    end_ns: int
    duration_ns: int
    clock_domain: str | None
    operation: str
    otel_status: OtelStatus
    field_coverage: dict[str, bool]
    diagnostics: list[str]
    instance_id: str | None = None
    service_version: str | None = None
    target: str | None = None
    http_status: int | None = None
    grpc_status: int | None = None
    error_type: str | None = None
    resend_count: int | None = None
    idempotency: Idempotency = "unknown"
    links_present: bool = False
    async_markers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "service": self.service,
            "instance_id": self.instance_id,
            "service_version": self.service_version,
            "name": self.name,
            "kind": self.kind,
            "start_ns": str(self.start_ns),
            "end_ns": str(self.end_ns),
            "duration_ns": str(self.duration_ns),
            "clock_domain": self.clock_domain,
            "operation": self.operation,
            "target": self.target,
            "otel_status": self.otel_status,
            "http_status": self.http_status,
            "grpc_status": self.grpc_status,
            "error_type": self.error_type,
            "resend_count": self.resend_count,
            "idempotency": self.idempotency,
            "links_present": self.links_present,
            "async_markers": list(self.async_markers),
            "field_coverage": dict(self.field_coverage),
            "diagnostics": list(self.diagnostics),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> NormalizedSpan:
        return NormalizedSpan(
            trace_id=d["trace_id"],
            span_id=d["span_id"],
            parent_span_id=d.get("parent_span_id"),
            service=d["service"],
            instance_id=d.get("instance_id"),
            service_version=d.get("service_version"),
            name=d["name"],
            kind=d["kind"],
            start_ns=int(d["start_ns"]),
            end_ns=int(d["end_ns"]),
            duration_ns=int(d["duration_ns"]),
            clock_domain=d.get("clock_domain"),
            operation=d["operation"],
            target=d.get("target"),
            otel_status=d["otel_status"],
            http_status=d.get("http_status"),
            grpc_status=d.get("grpc_status"),
            error_type=d.get("error_type"),
            resend_count=d.get("resend_count"),
            idempotency=d.get("idempotency", "unknown"),
            links_present=bool(d.get("links_present", False)),
            async_markers=list(d.get("async_markers", [])),
            field_coverage=dict(d.get("field_coverage", {})),
            diagnostics=list(d.get("diagnostics", [])),
        )


@dataclass(frozen=True)
class QualityIssue:
    code: str
    scope: str
    span_ids: list[str]
    details: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "scope": self.scope,
            "span_ids": list(self.span_ids),
            "details": self.details,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> QualityIssue:
        return QualityIssue(
            code=d["code"],
            scope=d["scope"],
            span_ids=list(d["span_ids"]),
            details=d["details"],
        )


@dataclass(frozen=True)
class Quality:
    issues: list[QualityIssue] = field(default_factory=list)
    orphan_count: int = 0
    conflicting_span_count: int = 0
    unknown_service_count: int = 0
    source_loss: str | None = None
    parse_errors: int = 0
    forced_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "issues": [i.to_dict() for i in self.issues],
            "orphan_count": self.orphan_count,
            "conflicting_span_count": self.conflicting_span_count,
            "unknown_service_count": self.unknown_service_count,
            "source_loss": self.source_loss,
            "parse_errors": self.parse_errors,
            "forced_reason": self.forced_reason,
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> Quality:
        return Quality(
            issues=[QualityIssue.from_dict(i) for i in d.get("issues", [])],
            orphan_count=int(d.get("orphan_count", 0)),
            conflicting_span_count=int(d.get("conflicting_span_count", 0)),
            unknown_service_count=int(d.get("unknown_service_count", 0)),
            source_loss=d.get("source_loss"),
            parse_errors=int(d.get("parse_errors", 0)),
            forced_reason=d.get("forced_reason"),
        )


@dataclass(frozen=True)
class TraceGraph:
    trace_ref: TraceRef
    first_received_at_ns: int
    last_received_at_ns: int
    assembly_state: AssemblyState
    spans: list[NormalizedSpan]
    roots: list[str]
    parent_index: dict[str, str | None]
    children_index: dict[str, list[str]]
    quality: Quality

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_ref": self.trace_ref.to_dict(),
            "first_received_at_ns": str(self.first_received_at_ns),
            "last_received_at_ns": str(self.last_received_at_ns),
            "assembly_state": self.assembly_state,
            "spans": [s.to_dict() for s in self.spans],
            "roots": list(self.roots),
            "parent_index": {k: v for k, v in self.parent_index.items()},
            "children_index": {k: list(v) for k, v in self.children_index.items()},
            "quality": self.quality.to_dict(),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraceGraph:
        return TraceGraph(
            trace_ref=TraceRef.from_dict(d["trace_ref"]),
            first_received_at_ns=int(d["first_received_at_ns"]),
            last_received_at_ns=int(d["last_received_at_ns"]),
            assembly_state=d["assembly_state"],
            spans=[NormalizedSpan.from_dict(s) for s in d["spans"]],
            roots=list(d["roots"]),
            parent_index=dict(d["parent_index"]),
            children_index={k: list(v) for k, v in d["children_index"].items()},
            quality=Quality.from_dict(d["quality"]),
        )


@dataclass(frozen=True)
class Attempt:
    trace_ref: TraceRef
    attempt_id: str
    edge_id: str
    duration_ns: int
    start_ns: int
    outcome: Outcome
    classification_basis: str
    confidence: Confidence
    server_span_id: str | None = None
    server_duration_ns: int | None = None
    caller_server_span_id: str | None = None
    upstream_attempt_id: str | None = None
    offset_in_caller_ns: int | None = None
    client_http_status: int | None = None
    server_http_status: int | None = None
    error_type: str | None = None
    resend_count: int | None = None
    call_id: str | None = None
    attempt_index: int | None = None
    gap_before_ns: int | None = None
    first_path: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_ref": self.trace_ref.to_dict(),
            "attempt_id": self.attempt_id,
            "edge_id": self.edge_id,
            "duration_ns": str(self.duration_ns),
            "start_ns": str(self.start_ns),
            "server_span_id": self.server_span_id,
            "server_duration_ns": (
                str(self.server_duration_ns) if self.server_duration_ns is not None else None
            ),
            "caller_server_span_id": self.caller_server_span_id,
            "upstream_attempt_id": self.upstream_attempt_id,
            "offset_in_caller_ns": (
                str(self.offset_in_caller_ns) if self.offset_in_caller_ns is not None else None
            ),
            "outcome": self.outcome,
            "client_http_status": self.client_http_status,
            "server_http_status": self.server_http_status,
            "error_type": self.error_type,
            "resend_count": self.resend_count,
            "call_id": self.call_id,
            "attempt_index": self.attempt_index,
            "gap_before_ns": (str(self.gap_before_ns) if self.gap_before_ns is not None else None),
            "first_path": self.first_path,
            "classification_basis": self.classification_basis,
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class LogicalCall:
    trace_ref: TraceRef
    call_id: str
    edge_id: str
    hypothesis: Hypothesis
    confidence: Confidence
    evidence_codes: list[str]
    attempt_ids: list[str]
    gaps_ns: list[int]
    owner_span_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_ref": self.trace_ref.to_dict(),
            "call_id": self.call_id,
            "edge_id": self.edge_id,
            "owner_span_id": self.owner_span_id,
            "attempt_ids": list(self.attempt_ids),
            "hypothesis": self.hypothesis,
            "confidence": self.confidence,
            "evidence_codes": list(self.evidence_codes),
            "gaps_ns": [str(g) for g in self.gaps_ns],
        }


@dataclass(frozen=True)
class CallModel:
    trace_ref: TraceRef
    grouping_version: str
    attempts: list[Attempt]
    calls: list[LogicalCall]

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_ref": self.trace_ref.to_dict(),
            "grouping_version": self.grouping_version,
            "attempts": [a.to_dict() for a in self.attempts],
            "calls": [c.to_dict() for c in self.calls],
        }
