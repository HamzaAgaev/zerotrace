from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from backend.contracts.base import TraceRef
from backend.contracts.p2 import CallModel, NormalizedSpan, TraceGraph
from backend.contracts.p3 import percentile_ns

Interval = tuple[int, int]


def union_duration(intervals: Iterable[Interval]) -> int:
    sorted_intervals = sorted(intervals)
    total = 0
    cur_start: int | None = None
    cur_end = 0
    for start, end in sorted_intervals:
        if end <= start:
            continue
        if cur_start is None:
            cur_start, cur_end = start, end
        elif start <= cur_end:
            cur_end = max(cur_end, end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = start, end
    if cur_start is not None:
        total += cur_end - cur_start
    return total


def clip_interval(interval: Interval, bounds: Interval) -> Interval | None:
    start = max(interval[0], bounds[0])
    end = min(interval[1], bounds[1])
    if end <= start:
        return None
    return (start, end)


def _uncovered(parent: Interval, cover: Iterable[Interval]) -> int:
    clipped = [c for c in (clip_interval(i, parent) for i in cover) if c is not None]
    return (parent[1] - parent[0]) - union_duration(clipped)


def uncovered_direct_children(parent: Interval, children: Iterable[Interval]) -> int:
    return _uncovered(parent, children)


def uncovered_outgoing_calls(parent: Interval, call_intervals: Iterable[Interval]) -> int:
    return _uncovered(parent, call_intervals)


def same_clock(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a == b


def offset_ns(
    start_ns: int,
    base_ns: int,
    clock_domain: str | None,
    base_clock_domain: str | None,
) -> int | None:
    if not same_clock(clock_domain, base_clock_domain):
        return None
    return start_ns - base_ns


def _span_interval(span: NormalizedSpan) -> Interval:
    return (span.start_ns, span.end_ns)


def _call_intervals(
    owner: NormalizedSpan,
    calls: CallModel,
    spans_by_id: dict[str, NormalizedSpan],
) -> list[Interval]:
    attempts_by_id = {a.attempt_id: a for a in calls.attempts}
    intervals: list[Interval] = []
    for call in calls.calls:
        if call.owner_span_id != owner.span_id:
            continue
        bounds: list[Interval] = []
        for attempt_id in call.attempt_ids:
            attempt = attempts_by_id.get(attempt_id)
            if attempt is None:
                continue
            client_span = spans_by_id.get(attempt.attempt_id)
            client_clock = client_span.clock_domain if client_span is not None else None
            if not same_clock(client_clock, owner.clock_domain):
                continue
            bounds.append((attempt.start_ns, attempt.start_ns + attempt.duration_ns))
        if not bounds:
            continue
        intervals.append((min(b[0] for b in bounds), max(b[1] for b in bounds)))
    return intervals


@dataclass(frozen=True)
class SpanTimingSample:
    span_id: str
    service: str
    operation: str
    total_ns: int
    offset_ns: int | None
    uncovered_direct_children_ns: int
    uncovered_outgoing_calls_ns: int


@dataclass(frozen=True)
class TimingSamples:
    trace_ref: TraceRef
    samples: list[SpanTimingSample] = field(default_factory=list)
    total_p50_ns: int | None = None
    total_p95_ns: int | None = None
    total_p99_ns: int | None = None
    uncovered_direct_children_p50_ns: int | None = None
    uncovered_direct_children_p95_ns: int | None = None
    uncovered_direct_children_p99_ns: int | None = None
    uncovered_outgoing_calls_p50_ns: int | None = None
    uncovered_outgoing_calls_p95_ns: int | None = None
    uncovered_outgoing_calls_p99_ns: int | None = None


def _percentiles(values: list[int]) -> tuple[int | None, int | None, int | None]:
    ordered = sorted(values)
    return (
        percentile_ns(ordered, 0.5),
        percentile_ns(ordered, 0.95),
        percentile_ns(ordered, 0.99),
    )


def derive_timings(graph: TraceGraph, calls: CallModel) -> TimingSamples:
    spans_by_id = {s.span_id: s for s in graph.spans}
    samples: list[SpanTimingSample] = []
    for span in graph.spans:
        if span.kind != "server":
            continue
        parent_interval = _span_interval(span)
        child_intervals: list[Interval] = []
        for child_id in graph.children_index.get(span.span_id, []):
            child = spans_by_id.get(child_id)
            if child is None or not same_clock(child.clock_domain, span.clock_domain):
                continue
            child_intervals.append(_span_interval(child))
        call_intervals = _call_intervals(span, calls, spans_by_id)
        parent_span = spans_by_id.get(graph.parent_index.get(span.span_id) or "")
        if parent_span is not None:
            offset = offset_ns(
                span.start_ns, parent_span.start_ns, span.clock_domain, parent_span.clock_domain
            )
        else:
            offset = None
        samples.append(
            SpanTimingSample(
                span_id=span.span_id,
                service=span.service,
                operation=span.operation,
                total_ns=span.duration_ns,
                offset_ns=offset,
                uncovered_direct_children_ns=uncovered_direct_children(
                    parent_interval, child_intervals
                ),
                uncovered_outgoing_calls_ns=uncovered_outgoing_calls(
                    parent_interval, call_intervals
                ),
            )
        )
    total_p50, total_p95, total_p99 = _percentiles([s.total_ns for s in samples])
    udc_p50, udc_p95, udc_p99 = _percentiles([s.uncovered_direct_children_ns for s in samples])
    uoc_p50, uoc_p95, uoc_p99 = _percentiles([s.uncovered_outgoing_calls_ns for s in samples])
    return TimingSamples(
        trace_ref=graph.trace_ref,
        samples=samples,
        total_p50_ns=total_p50,
        total_p95_ns=total_p95,
        total_p99_ns=total_p99,
        uncovered_direct_children_p50_ns=udc_p50,
        uncovered_direct_children_p95_ns=udc_p95,
        uncovered_direct_children_p99_ns=udc_p99,
        uncovered_outgoing_calls_p50_ns=uoc_p50,
        uncovered_outgoing_calls_p95_ns=uoc_p95,
        uncovered_outgoing_calls_p99_ns=uoc_p99,
    )
