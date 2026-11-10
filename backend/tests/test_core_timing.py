from __future__ import annotations

from contracts import (
    Attempt,
    CallModel,
    LogicalCall,
    NormalizedSpan,
    Quality,
    TraceGraph,
    TraceRef,
    percentile_ns,
)
from core.timing import (
    clip_interval,
    derive_timings,
    offset_ns,
    same_clock,
    uncovered_direct_children,
    uncovered_outgoing_calls,
    union_duration,
)

MS = 1_000_000

TRACE_REF = TraceRef(environment_id="env", trace_id="ab" * 16, generation=1, revision=1)


def make_span(
    span_id: str,
    start_ms: int,
    end_ms: int,
    kind: str = "server",
    parent_span_id: str | None = None,
    clock_domain: str | None = "host-1",
    service: str = "orders",
) -> NormalizedSpan:
    return NormalizedSpan(
        trace_id="ab" * 16,
        span_id=span_id,
        parent_span_id=parent_span_id,
        service=service,
        name=f"{kind.upper()} {span_id}",
        kind=kind,  # type: ignore[arg-type]
        start_ns=start_ms * MS,
        end_ns=end_ms * MS,
        duration_ns=(end_ms - start_ms) * MS,
        clock_domain=clock_domain,
        operation=f"op-{span_id}",
        otel_status="unset",
        field_coverage={},
        diagnostics=[],
    )


def make_graph(spans: list[NormalizedSpan]) -> TraceGraph:
    parent_index = {s.span_id: s.parent_span_id for s in spans}
    children_index: dict[str, list[str]] = {}
    roots: list[str] = []
    for s in spans:
        if s.parent_span_id is None:
            roots.append(s.span_id)
        else:
            children_index.setdefault(s.parent_span_id, []).append(s.span_id)
    return TraceGraph(
        trace_ref=TRACE_REF,
        first_received_at_ns=0,
        last_received_at_ns=0,
        assembly_state="quiet",
        spans=spans,
        roots=roots,
        parent_index=parent_index,
        children_index=children_index,
        quality=Quality(),
    )


def empty_calls() -> CallModel:
    return CallModel(trace_ref=TRACE_REF, grouping_version="1.0.0", attempts=[], calls=[])


class TestUnionDuration:
    def test_empty(self):
        assert union_duration([]) == 0

    def test_single(self):
        assert union_duration([(10, 25)]) == 15

    def test_overlapping_merged(self):
        assert union_duration([(0, 100), (50, 120)]) == 120

    def test_fully_contained(self):
        assert union_duration([(0, 100), (20, 80)]) == 100

    def test_disjoint(self):
        assert union_duration([(0, 10), (20, 35)]) == 25

    def test_unsorted_input(self):
        assert union_duration([(20, 35), (0, 10)]) == 25

    def test_touching_intervals(self):
        assert union_duration([(0, 10), (10, 20)]) == 20

    def test_zero_length_ignored(self):
        assert union_duration([(5, 5), (0, 10)]) == 10


class TestUncoveredDirectChildren:
    def test_overlapped_children(self):
        parent = (0, 1200 * MS)
        children = [(0, 1000 * MS), (0, 1000 * MS)]
        assert uncovered_direct_children(parent, children) == 200 * MS

    def test_sequential_children(self):
        parent = (0, 1200 * MS)
        children = [(0, 500 * MS), (500 * MS, 1000 * MS)]
        assert uncovered_direct_children(parent, children) == 200 * MS

    def test_no_children(self):
        assert uncovered_direct_children((0, 100 * MS), []) == 100 * MS

    def test_child_outside_parent_not_subtracted(self):
        parent = (100 * MS, 200 * MS)
        children = [(0, 50 * MS), (300 * MS, 400 * MS)]
        assert uncovered_direct_children(parent, children) == 100 * MS

    def test_child_partially_outside_clipped(self):
        parent = (100 * MS, 200 * MS)
        child = (0, 150 * MS)
        assert uncovered_direct_children(parent, [child]) == 50 * MS
        assert child == (0, 150 * MS)

    def test_child_covering_parent(self):
        assert uncovered_direct_children((100, 200), [(0, 300)]) == 0


class TestUncoveredOutgoingCalls:
    def test_retry_call_covers_gap(self):
        parent = (0, 1200 * MS)
        attempts_union = (100 * MS, 900 * MS)
        assert uncovered_outgoing_calls(parent, [attempts_union]) == 400 * MS

    def test_two_separate_calls(self):
        parent = (0, 1000 * MS)
        calls = [(0, 400 * MS), (500 * MS, 900 * MS)]
        assert uncovered_outgoing_calls(parent, calls) == 200 * MS

    def test_distinct_from_direct_children(self):
        parent = (0, 1000 * MS)
        children = [(0, 500 * MS)]
        calls = [(0, 500 * MS), (600 * MS, 800 * MS)]
        assert uncovered_direct_children(parent, children) == 500 * MS
        assert uncovered_outgoing_calls(parent, calls) == 300 * MS


class TestClip:
    def test_fully_outside(self):
        assert clip_interval((300, 400), (100, 200)) is None

    def test_clips_both_sides(self):
        assert clip_interval((0, 300), (100, 200)) == (100, 200)


class TestClockAndOffset:
    def test_same_clock(self):
        assert same_clock("h1", "h1")
        assert not same_clock("h1", "h2")
        assert not same_clock(None, "h1")
        assert not same_clock("h1", None)
        assert not same_clock(None, None)

    def test_offset_same_domain(self):
        assert offset_ns(1500, 1000, "h1", "h1") == 500

    def test_offset_different_domain_is_none(self):
        assert offset_ns(1500, 1000, "h1", "h2") is None

    def test_offset_unknown_domain_is_none(self):
        assert offset_ns(1500, 1000, None, "h1") is None
        assert offset_ns(1500, 1000, "h1", None) is None


class TestDeriveTimings:
    def test_control_case_overlap(self):
        parent = make_span("00" * 7 + "01", 0, 1200)
        child_a = make_span(
            "00" * 7 + "02", 0, 1000, kind="internal", parent_span_id=parent.span_id
        )
        child_b = make_span(
            "00" * 7 + "03", 0, 1000, kind="internal", parent_span_id=parent.span_id
        )
        graph = make_graph([parent, child_a, child_b])
        result = derive_timings(graph, empty_calls())
        assert len(result.samples) == 1
        sample = result.samples[0]
        assert sample.span_id == parent.span_id
        assert sample.total_ns == 1200 * MS
        assert sample.uncovered_direct_children_ns == 200 * MS
        assert sample.uncovered_outgoing_calls_ns == 1200 * MS
        assert sample.offset_ns is None

    def test_sequential_children(self):
        parent = make_span("00" * 7 + "01", 0, 1200)
        child_a = make_span("00" * 7 + "02", 0, 500, kind="internal", parent_span_id=parent.span_id)
        child_b = make_span(
            "00" * 7 + "03", 500, 1000, kind="internal", parent_span_id=parent.span_id
        )
        graph = make_graph([parent, child_a, child_b])
        sample = derive_timings(graph, empty_calls()).samples[0]
        assert sample.uncovered_direct_children_ns == 200 * MS

    def test_child_outside_parent_clipped(self):
        parent = make_span("00" * 7 + "01", 100, 200)
        child = make_span("00" * 7 + "02", 0, 150, kind="internal", parent_span_id=parent.span_id)
        graph = make_graph([parent, child])
        sample = derive_timings(graph, empty_calls()).samples[0]
        assert sample.total_ns == 100 * MS
        assert sample.uncovered_direct_children_ns == 50 * MS
        assert (child.start_ns, child.end_ns) == (0, 150 * MS)

    def test_child_different_clock_excluded_and_offset_none(self):
        parent = make_span("00" * 7 + "01", 0, 1000)
        child = make_span(
            "00" * 7 + "02",
            0,
            800,
            kind="server",
            parent_span_id=parent.span_id,
            clock_domain="host-2",
        )
        graph = make_graph([parent, child])
        samples = {s.span_id: s for s in derive_timings(graph, empty_calls()).samples}
        assert samples[parent.span_id].uncovered_direct_children_ns == 1000 * MS
        assert samples[child.span_id].offset_ns is None

    def test_offset_same_clock(self):
        parent = make_span("00" * 7 + "01", 1000, 2000)
        child = make_span("00" * 7 + "02", 1300, 1800, kind="server", parent_span_id=parent.span_id)
        graph = make_graph([parent, child])
        samples = {s.span_id: s for s in derive_timings(graph, empty_calls()).samples}
        assert samples[child.span_id].offset_ns == 300 * MS

    def test_outgoing_calls_with_retry_gap(self):
        parent = make_span("00" * 7 + "01", 0, 1200)
        client_a = make_span(
            "00" * 7 + "02", 100, 400, kind="client", parent_span_id=parent.span_id
        )
        client_b = make_span(
            "00" * 7 + "03", 600, 900, kind="client", parent_span_id=parent.span_id
        )
        graph = make_graph([parent, client_a, client_b])
        attempts = [
            Attempt(
                trace_ref=TRACE_REF,
                attempt_id=client_a.span_id,
                edge_id="e1",
                duration_ns=300 * MS,
                start_ns=100 * MS,
                outcome="timeout",
                classification_basis="test",
                confidence="high",
                call_id="c1",
                attempt_index=0,
            ),
            Attempt(
                trace_ref=TRACE_REF,
                attempt_id=client_b.span_id,
                edge_id="e1",
                duration_ns=300 * MS,
                start_ns=600 * MS,
                outcome="ok",
                classification_basis="test",
                confidence="high",
                call_id="c1",
                attempt_index=1,
                gap_before_ns=200 * MS,
            ),
        ]
        call = LogicalCall(
            trace_ref=TRACE_REF,
            call_id="c1",
            edge_id="e1",
            hypothesis="retry",
            confidence="high",
            evidence_codes=["test"],
            attempt_ids=[client_a.span_id, client_b.span_id],
            gaps_ns=[200 * MS],
            owner_span_id=parent.span_id,
        )
        calls = CallModel(
            trace_ref=TRACE_REF, grouping_version="1.0.0", attempts=attempts, calls=[call]
        )
        sample = derive_timings(graph, calls).samples[0]
        assert sample.uncovered_outgoing_calls_ns == 400 * MS
        assert sample.uncovered_direct_children_ns == 600 * MS

    def test_client_spans_not_sampled(self):
        root = make_span("00" * 7 + "01", 0, 100, kind="client")
        graph = make_graph([root])
        assert derive_timings(graph, empty_calls()).samples == []

    def test_percentiles(self):
        spans = [
            make_span(f"{i + 1:016x}", 0, duration_ms)
            for i, duration_ms in enumerate([100, 200, 300, 400])
        ]
        graph = make_graph(spans)
        result = derive_timings(graph, empty_calls())
        assert result.total_p50_ns == percentile_ns([100 * MS, 200 * MS, 300 * MS, 400 * MS], 0.5)
        assert result.total_p50_ns == 250 * MS
        assert result.total_p95_ns == 385 * MS
        assert result.total_p99_ns == 397 * MS
        assert result.uncovered_direct_children_p50_ns == 250 * MS
        assert result.uncovered_direct_children_p95_ns == 385 * MS
        assert result.uncovered_direct_children_p99_ns == 397 * MS

    def test_empty_graph_percentiles_none(self):
        graph = make_graph([])
        result = derive_timings(graph, empty_calls())
        assert result.samples == []
        assert result.total_p50_ns is None
        assert result.total_p95_ns is None
        assert result.total_p99_ns is None
        assert result.uncovered_outgoing_calls_p99_ns is None
