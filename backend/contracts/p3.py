from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Generic, Literal, TypeVar

T = TypeVar("T")

SourcedSource = Literal["runtime", "declared", "annotated", "cluster", "observed", "unknown"]
BackoffKind = Literal["fixed", "exponential", "unknown"]
JitterKind = Literal["none", "full", "unknown"]
EffectiveStrategy = Literal["fresh_runtime", "declared", "cautious_inference"]


@dataclass(frozen=True)
class Sourced(Generic[T]):
    value: T | None
    source: SourcedSource
    confidence: Literal["high", "medium", "low"] | None = None
    sample_count: int | None = None
    config_revision: str | None = None
    observed_at_ns: int | None = None
    stale: bool = False
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        value: Any = str(self.value) if isinstance(self.value, int) else self.value
        return {
            "value": value,
            "source": self.source,
            "confidence": self.confidence,
            "sample_count": self.sample_count,
            "config_revision": self.config_revision,
            "observed_at_ns": (
                str(self.observed_at_ns) if self.observed_at_ns is not None else None
            ),
            "stale": self.stale,
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True)
class Backoff:
    kind: BackoffKind
    base_ns: int | None = None
    multiplier: float | None = None
    cap_ns: int | None = None
    jitter: JitterKind = "unknown"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "base_ns": str(self.base_ns) if self.base_ns is not None else None,
            "multiplier": self.multiplier,
            "cap_ns": str(self.cap_ns) if self.cap_ns is not None else None,
            "jitter": self.jitter,
        }


@dataclass(frozen=True)
class EdgePolicy:
    edge_id: str
    per_attempt_timeout_ns: Sourced[int]
    max_attempts: Sourced[int]
    backoff: Backoff
    retry_on: list[str]
    policy_scope: str
    effective_source: EffectiveStrategy | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "per_attempt_timeout_ns": self.per_attempt_timeout_ns.to_dict(),
            "max_attempts": self.max_attempts.to_dict(),
            "backoff": self.backoff.to_dict(),
            "retry_on": list(self.retry_on),
            "policy_scope": self.policy_scope,
            "effective_source": self.effective_source,
        }


@dataclass(frozen=True)
class EdgeStats:
    edge_id: str
    dataset_ref: str
    attempts: int
    eligible_calls: int
    ambiguous_calls: int
    trace_count: int
    known_outcomes: int
    unknown_outcomes: int
    errors: int
    coverage: float
    sampling: str
    attempt_error_ratio: float | None = None
    success_p50_ns: int | None = None
    success_p95_ns: int | None = None
    success_p99_ns: int | None = None
    gaps_by_index: dict[int, list[int]] = field(default_factory=dict)
    jitter_cv: float | None = None

    def to_dict(self) -> dict[str, Any]:
        def ns(v: int | None) -> str | None:
            return str(v) if v is not None else None

        return {
            "edge_id": self.edge_id,
            "dataset_ref": self.dataset_ref,
            "attempts": self.attempts,
            "eligible_calls": self.eligible_calls,
            "ambiguous_calls": self.ambiguous_calls,
            "trace_count": self.trace_count,
            "known_outcomes": self.known_outcomes,
            "unknown_outcomes": self.unknown_outcomes,
            "errors": self.errors,
            "attempt_error_ratio": self.attempt_error_ratio,
            "success_p50_ns": ns(self.success_p50_ns),
            "success_p95_ns": ns(self.success_p95_ns),
            "success_p99_ns": ns(self.success_p99_ns),
            "gaps_by_index": {str(k): [str(g) for g in v] for k, v in self.gaps_by_index.items()},
            "jitter_cv": self.jitter_cv,
            "coverage": self.coverage,
            "sampling": self.sampling,
        }


def percentile_ns(sorted_samples: list[int], q: float) -> int | None:
    if not sorted_samples:
        return None
    if not 0.0 <= q <= 1.0:
        raise ValueError("q must be within [0, 1]")
    n = len(sorted_samples)
    if n == 1:
        return sorted_samples[0]
    pos = Decimal(str(q)) * (n - 1)
    lower = int(pos)
    upper = min(lower + 1, n - 1)
    frac = pos - lower
    value = Decimal(sorted_samples[lower]) * (1 - frac) + Decimal(sorted_samples[upper]) * frac
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def ratio(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator
