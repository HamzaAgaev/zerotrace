from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from .base import TraceRef

T = TypeVar("T")

UiOutcome = Literal["ok", "error", "unknown"]
UiSpanKind = Literal["server", "client", "internal"]


@dataclass(frozen=True)
class SpanView:
    span_id: str
    parent_span_id: str | None
    service: str
    name: str
    kind: UiSpanKind | None
    start_offset_ns: int
    duration_ns: int
    outcome: UiOutcome
    http_status: int | None = None
    error_type: str | None = None
    target: str | None = None

    def to_dict(self, *, include_kind: bool = True) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "service": self.service,
            "name": self.name,
            **({"kind": self.kind} if include_kind and self.kind is not None else {}),
            "start_offset_ns": str(self.start_offset_ns),
            "duration_ns": str(self.duration_ns),
            "outcome": self.outcome,
            "http_status": self.http_status,
            "error_type": self.error_type,
            **({"target": self.target} if self.target is not None else {}),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> SpanView:
        return SpanView(
            span_id=d["span_id"],
            parent_span_id=d.get("parent_span_id"),
            service=d["service"],
            name=d["name"],
            kind=d.get("kind"),
            start_offset_ns=int(d["start_offset_ns"]),
            duration_ns=int(d["duration_ns"]),
            outcome=d["outcome"],
            http_status=d.get("http_status"),
            error_type=d.get("error_type"),
            target=d.get("target"),
        )


@dataclass(frozen=True)
class TraceSummary:
    trace_ref: TraceRef
    operation: str
    service: str
    start_ns: int
    duration_ns: int
    span_count: int
    service_count: int
    outcome: UiOutcome
    finding_count: int
    quality_issues: list[str]
    http_status: int | None = None
    http_method: str | None = None
    endpoint: str | None = None

    @property
    def trace_id(self) -> str:
        return self.trace_ref.trace_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_ref": self.trace_ref.to_dict(),
            "trace_id": self.trace_ref.trace_id,
            "operation": self.operation,
            "service": self.service,
            "start_ns": str(self.start_ns),
            "duration_ns": str(self.duration_ns),
            "span_count": self.span_count,
            "service_count": self.service_count,
            "outcome": self.outcome,
            "http_status": self.http_status,
            "http_method": self.http_method,
            "endpoint": self.endpoint,
            "finding_count": self.finding_count,
            "quality_issues": list(self.quality_issues),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraceSummary:
        return TraceSummary(
            trace_ref=TraceRef.from_dict(d["trace_ref"]),
            operation=d["operation"],
            service=d["service"],
            start_ns=int(d["start_ns"]),
            duration_ns=int(d["duration_ns"]),
            span_count=int(d["span_count"]),
            service_count=int(d["service_count"]),
            outcome=d["outcome"],
            http_status=d.get("http_status"),
            http_method=d.get("http_method"),
            endpoint=d.get("endpoint"),
            finding_count=int(d["finding_count"]),
            quality_issues=list(d["quality_issues"]),
        )


@dataclass(frozen=True)
class TraceDetail:
    summary: TraceSummary
    spans: list[SpanView]
    quality_issues: list[str]
    compactable_client_spans: dict[str, str] | None = None

    @property
    def trace_ref(self) -> TraceRef:
        return self.summary.trace_ref

    def to_dict(self, *, include_span_kind: bool = True) -> dict[str, Any]:
        return {
            "summary": self.summary.to_dict(),
            "trace_ref": self.summary.trace_ref.to_dict(),
            "spans": [s.to_dict(include_kind=include_span_kind) for s in self.spans],
            "quality_issues": list(self.quality_issues),
            **({"compactable_client_spans": self.compactable_client_spans}
               if self.compactable_client_spans else {}),
        }

    @staticmethod
    def from_dict(d: dict[str, Any]) -> TraceDetail:
        return TraceDetail(
            summary=TraceSummary.from_dict(d["summary"]),
            spans=[SpanView.from_dict(s) for s in d["spans"]],
            quality_issues=list(d["quality_issues"]),
            compactable_client_spans=d.get("compactable_client_spans"),
        )


@dataclass(frozen=True)
class Envelope(Generic[T]):
    schema: str
    schema_version: str
    generated_at: str
    producer_version: str
    payload: T

    def to_dict(self, payload_to_dict: Any) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "generated_at": self.generated_at,
            "producer_version": self.producer_version,
            "payload": payload_to_dict,
        }
