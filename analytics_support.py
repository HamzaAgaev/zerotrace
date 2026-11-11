"""Small shared primitives for the analytics packages (Python 3.11+)."""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

VERSION = "0.1.0"


class AnalyticsError(ValueError):
    def __init__(self, code: str, message: str, status: int = 422, details=None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.details = details if details is not None else {}

    def body(self):
        return {"error": {"code": self.code, "message": str(self), "details": self.details,
                          "retryable": self.status == 503 or self.code == "traces_pending"}}


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def digest(value) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def valid_span_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{16}", value) is not None and value != "0" * 16


def ref_key(ref: dict) -> tuple[str, str, int, int]:
    if not isinstance(ref, dict):
        raise AnalyticsError("invalid_trace_ref", "Expected a complete TraceRef")
    env, trace = ref.get("environment_id"), ref.get("trace_id")
    versions = (ref.get("generation"), ref.get("revision"))
    if (not isinstance(env, str) or not env or not isinstance(trace, str)
            or not re.fullmatch(r"[0-9a-f]{32}", trace) or int(trace, 16) == 0
            or any(type(x) is not int or x < 0 for x in versions)):
        raise AnalyticsError("invalid_trace_ref", "Invalid environment, trace ID, generation or revision")
    return (env, trace, *versions)


def percentile_ns(values: Iterable[int], q: float) -> int | None:
    """P3 linear interpolation with integer-nanosecond round-half-up."""
    if not 0 <= q <= 1:
        raise ValueError("quantile outside [0, 1]")
    ordered = sorted(values)
    if not ordered:
        return None
    position = Decimal(str(q)) * (len(ordered) - 1)
    lo = int(position)
    hi = min(lo + 1, len(ordered) - 1)
    value = ordered[lo] + (position - lo) * (ordered[hi] - ordered[lo])
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


def wire(value):
    """Convert durations at the public JSON boundary while preserving missing values."""
    if isinstance(value, (list, tuple)):
        return [wire(item) for item in value]
    if isinstance(value, dict):
        return {key: str(item) if key.endswith("_ns") and type(item) is int else wire(item)
                for key, item in value.items()}
    return value
