"""Per-edge P3 model: aggregate EdgeStats from trace contributions and resolve EdgePolicy.

B04 (M2): full per-edge statistics and policies. Statistics are derived purely
from (TraceGraph, CallModel) pairs; policy is the typed P3 view over A's flat
configuration snapshots (post-flatten shape: edge_id, timeout_ms, max_attempts,
backoff_ms/backoff, retry_on, source, freshness, stale, config_revision).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from hashlib import sha256
from json import dumps
from typing import Any

from contracts.p2 import CallModel, TraceGraph
from contracts.p3 import Backoff, EdgePolicy, EdgeStats, Sourced, percentile_ns, ratio

from .calls import OPERATION_NORMALIZATION_VERSION

ERROR_OUTCOMES = frozenset(
    {"timeout", "fast_fail", "http_error", "grpc_error", "transport_error", "error_unknown"}
)
UNKNOWN_OUTCOME = "outcome_unknown"
MS_NS = 1_000_000
STALE_FRESHNESS = {"stale", "unavailable", "unknown"}


def _canonical(value: Any) -> str:
    return dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EdgeContribution:
    """One trace's share of edge evidence."""

    graph: TraceGraph
    calls: CallModel


def contribution_digest(contributions: list[EdgeContribution]) -> str:
    identity = [
        [
            item.graph.trace_ref.to_dict(),
            item.calls.grouping_version,
            len(item.calls.attempts),
        ]
        for item in contributions
    ]
    return _digest(identity)


def build_edge_stats(
    contributions: list[EdgeContribution],
    *,
    dataset_ref: str | None = None,
    sampling: str = "all",
) -> dict[str, EdgeStats]:
    """Aggregate per-edge samples across traces of one dataset.

    All attempts count, including members of heuristic groups; eligible_calls
    and ambiguous_calls split LogicalCall by hypothesis. An edge without
    attempts gets no entry — no samples means null, not zero.
    """
    per_edge: dict[str, dict[str, Any]] = {}
    for contribution in contributions:
        calls = contribution.calls
        by_edge_calls: dict[str, list] = {}
        for call in calls.calls:
            by_edge_calls.setdefault(call.edge_id, []).append(call)
        attempts_by_edge: dict[str, list] = {}
        for attempt in calls.attempts:
            attempts_by_edge.setdefault(attempt.edge_id, []).append(attempt)
        edge_ids = set(by_edge_calls) | set(attempts_by_edge)
        for edge_id in edge_ids:
            bucket = per_edge.setdefault(
                edge_id,
                {
                    "traces": set(),
                    "attempts": [],
                    "calls": [],
                },
            )
            bucket["traces"].add(contribution.graph.trace_ref.trace_id)
            bucket["attempts"].extend(attempts_by_edge.get(edge_id, ()))
            bucket["calls"].extend(by_edge_calls.get(edge_id, ()))

    stats: dict[str, EdgeStats] = {}
    for edge_id, bucket in per_edge.items():
        attempts = bucket["attempts"]
        calls = bucket["calls"]
        known = sum(1 for a in attempts if a.outcome != UNKNOWN_OUTCOME)
        unknown = len(attempts) - known
        errors = sum(1 for a in attempts if a.outcome in ERROR_OUTCOMES)
        success_durations = sorted(a.duration_ns for a in attempts if a.outcome == "ok")
        gaps_by_index: dict[int, list[int]] = {}
        for a in attempts:
            if a.attempt_index is not None and a.gap_before_ns is not None:
                gaps_by_index.setdefault(a.attempt_index, []).append(a.gap_before_ns)
        all_gaps = [gap for gaps in gaps_by_index.values() for gap in gaps]
        jitter_cv = None
        if len(all_gaps) >= 2:
            mean = statistics.fmean(all_gaps)
            if mean > 0:
                jitter_cv = statistics.pstdev(all_gaps) / mean
        stats[edge_id] = EdgeStats(
            edge_id=edge_id,
            dataset_ref=dataset_ref or "",
            attempts=len(attempts),
            eligible_calls=sum(1 for c in calls if c.hypothesis != "ambiguous"),
            ambiguous_calls=sum(1 for c in calls if c.hypothesis == "ambiguous"),
            trace_count=len(bucket["traces"]),
            known_outcomes=known,
            unknown_outcomes=unknown,
            errors=errors,
            coverage=ratio(known, len(attempts)) or 0.0,
            sampling=sampling,
            attempt_error_ratio=ratio(errors, len(attempts)),
            success_p50_ns=percentile_ns(success_durations, 0.5) if success_durations else None,
            success_p95_ns=percentile_ns(success_durations, 0.95) if success_durations else None,
            success_p99_ns=percentile_ns(success_durations, 0.99) if success_durations else None,
            gaps_by_index=gaps_by_index,
            jitter_cv=jitter_cv,
        )
    return stats


def _operand(
    policy: dict[str, Any],
    key: str,
    *,
    fresh: bool,
    to_ns: bool = False,
) -> Sourced[int]:
    value = policy.get(key)
    source = "unknown"
    confidence = None
    reasons: list[str] = []
    if isinstance(value, dict) and "value" in value:
        raw = value["value"]
        source = value.get("source") or "unknown"
        confidence = value.get("confidence")
        reasons = list(value.get("reasons") or ())
    else:
        raw = value
        if fresh and raw is not None:
            source = "runtime"
    if raw is None:
        return Sourced(value=None, source="unknown", reasons=["missing"])
    number = int(raw)
    if to_ns:
        number *= MS_NS
    stale = not fresh
    if stale and source == "runtime":
        source = "unknown"
        reasons = [*reasons, "stale_runtime"]
    return Sourced(
        value=number,
        source=source,
        confidence=confidence,
        config_revision=policy.get("config_revision"),
        stale=stale and source != "declared",
        reasons=reasons,
    )


def _backoff(policy: dict[str, Any]) -> Backoff:
    configured = policy.get("backoff")
    if isinstance(configured, dict):
        return Backoff(
            kind=configured.get("kind") or "unknown",
            base_ns=configured.get("base_ns"),
            multiplier=configured.get("multiplier"),
            cap_ns=configured.get("cap_ns"),
            jitter=configured.get("jitter") or "unknown",
        )
    base_ms = policy.get("backoff_ms")
    if base_ms is None:
        return Backoff(kind="unknown")
    return Backoff(kind="fixed", base_ns=int(base_ms) * MS_NS, jitter="none")


def build_edge_policy(configured: dict[str, Any]) -> EdgePolicy:
    """Typed P3 view over one flat policy record.

    effective_source: fresh_runtime is a fresh runtime snapshot; declared keeps
    explicitly declared operands only (declared/annotated); cautious_inference
    is everything else, C's algorithm must lower confidence there.
    """
    edge_id = configured.get("edge_id")
    if not isinstance(edge_id, str) or not edge_id:
        raise ValueError("edge_id required")
    stale = bool(configured.get("stale")) or configured.get("freshness") in STALE_FRESHNESS
    conflicted = bool(configured.get("binding_conflict"))
    fresh = not stale and not conflicted
    timeout = _operand(configured, "timeout_ms", fresh=fresh, to_ns=True)
    if timeout.value is None and configured.get("budget_ns") is not None:
        timeout = Sourced(
            value=int(configured["budget_ns"]),
            source="declared" if not fresh else "runtime",
            config_revision=configured.get("config_revision"),
            stale=not fresh,
        )
    max_attempts = _operand(configured, "max_attempts", fresh=fresh)
    if fresh:
        effective: str | None = "fresh_runtime"
    elif timeout.source in {"declared", "annotated"} or max_attempts.source in {
        "declared",
        "annotated",
    }:
        effective = "declared"
    elif timeout.value is None and max_attempts.value is None:
        effective = None
    else:
        effective = "cautious_inference"
    return EdgePolicy(
        edge_id=edge_id,
        per_attempt_timeout_ns=timeout,
        max_attempts=max_attempts,
        backoff=_backoff(configured),
        retry_on=list(configured.get("retry_on") or ()),
        policy_scope=str(configured.get("call_ref") or configured.get("route") or edge_id),
        effective_source=effective,
    )


@dataclass(frozen=True)
class ModelEdge:
    edge_id: str
    stats: EdgeStats | None
    policy: EdgePolicy | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "stats": self.stats.to_dict() if self.stats is not None else None,
            "policy": self.policy.to_dict() if self.policy is not None else None,
        }


@dataclass(frozen=True)
class ModelView:
    """Dataset-wide per-edge model: statistics plus policies plus idle edges."""

    dataset_ref: str
    grouping_version: str
    normalization_version: str
    edges: dict[str, ModelEdge] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_ref": self.dataset_ref,
            "grouping_version": self.grouping_version,
            "normalization_version": self.normalization_version,
            "edges": {key: edge.to_dict() for key, edge in sorted(self.edges.items())},
        }


def build_model(
    contributions: list[EdgeContribution],
    configured_policies: list[dict[str, Any]] | None = None,
    *,
    known_edges: list[str] | None = None,
    dataset_ref: str | None = None,
    sampling: str = "all",
) -> ModelView:
    """Assemble a ModelView: statistics from contributions, policies from A configs.

    Idle edges from known_edges stay on the graph with stats=None (not zero).
    """
    resolved_ref = dataset_ref or contribution_digest(contributions)
    stats = build_edge_stats(contributions, dataset_ref=resolved_ref, sampling=sampling)
    policies = {}
    for configured in configured_policies or ():
        policy = build_edge_policy(configured)
        policies[policy.edge_id] = policy
    edges: dict[str, ModelEdge] = {}
    for edge_id in sorted(set(stats) | set(policies) | set(known_edges or ())):
        edges[edge_id] = ModelEdge(
            edge_id=edge_id,
            stats=stats.get(edge_id),
            policy=policies.get(edge_id),
        )
    grouping_version = contributions[0].calls.grouping_version if contributions else "none"
    return ModelView(
        dataset_ref=resolved_ref,
        grouping_version=grouping_version,
        normalization_version=OPERATION_NORMALIZATION_VERSION,
        edges=edges,
    )
