from backend._compat import publish_legacy_package
from backend.contracts.base import (
    CANONICALIZATION_VERSION,
    EdgeKey,
    EndpointKey,
    OperationKey,
    ServiceKey,
    TraceRef,
    endpoint_label,
)
from backend.contracts.p1 import (
    DEFAULT_BODY_LIMIT_BYTES,
    DEFAULT_MAX_DEPTH,
    DEFAULT_MAX_SPANS_PER_BATCH,
    DEFAULT_MAX_SPANS_PER_TRACE,
    IngestLimits,
    SourceBinding,
    SourceCapabilities,
)
from backend.contracts.p2 import (
    QUALITY_CODES,
    Attempt,
    CallModel,
    LogicalCall,
    NormalizedSpan,
    Quality,
    QualityIssue,
    TraceGraph,
)
from backend.contracts.p3 import (
    Backoff,
    EdgePolicy,
    EdgeStats,
    Sourced,
    percentile_ns,
    ratio,
)
from backend.contracts.p7 import Envelope, SpanView, TraceDetail, TraceSummary
from backend.contracts.validate import ValidationReport, validate_span, validate_trace_graph

__all__ = [
    "CANONICALIZATION_VERSION",
    "DEFAULT_BODY_LIMIT_BYTES",
    "DEFAULT_MAX_DEPTH",
    "DEFAULT_MAX_SPANS_PER_BATCH",
    "DEFAULT_MAX_SPANS_PER_TRACE",
    "QUALITY_CODES",
    "Attempt",
    "Backoff",
    "CallModel",
    "EdgeKey",
    "EdgePolicy",
    "EdgeStats",
    "EndpointKey",
    "Envelope",
    "IngestLimits",
    "LogicalCall",
    "NormalizedSpan",
    "OperationKey",
    "Quality",
    "QualityIssue",
    "ServiceKey",
    "SourceBinding",
    "SourceCapabilities",
    "Sourced",
    "SpanView",
    "TraceDetail",
    "TraceGraph",
    "TraceRef",
    "TraceSummary",
    "ValidationReport",
    "endpoint_label",
    "percentile_ns",
    "ratio",
    "validate_span",
    "validate_trace_graph",
]

publish_legacy_package(__name__, ["base", "p1", "p2", "p3", "p7", "validate"])
