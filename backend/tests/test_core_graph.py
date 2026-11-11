from __future__ import annotations

import json
from pathlib import Path

from contracts import IngestLimits, NormalizedSpan, TraceGraph, TraceRef
from core.graph import build_graph

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"

TRACE_ID = "ab" * 16
REF = TraceRef(environment_id="test", trace_id=TRACE_ID, generation=1, revision=1)
T0 = 1_763_000_000_000_000_000


def load_fixture_spans(name: str) -> tuple[TraceGraph, list[NormalizedSpan]]:
    data = json.loads((FIXTURES / name / "trace.json").read_text())
    graph = TraceGraph.from_dict(data)
    return graph, graph.spans


def make_span(span_id: str, **kw) -> NormalizedSpan:
    args = dict(
        trace_id=TRACE_ID,
        span_id=span_id,
        parent_span_id=None,
        service="orders",
        name=f"INTERNAL {span_id}",
        kind="internal",
        start_ns=T0,
        end_ns=T0 + 100,
        duration_ns=100,
        clock_domain="host-1",
        operation="op",
        otel_status="unset",
        field_coverage={},
        diagnostics=[],
    )
    args.update(kw)
    return NormalizedSpan(**args)


def issue_codes(graph: TraceGraph) -> list[str]:
    return [i.code for i in graph.quality.issues]


def issue(graph: TraceGraph, code: str):
    return next(i for i in graph.quality.issues if i.code == code)


class TestHappyPath:
    def test_s00_fixture_indexes(self):
        expected, spans = load_fixture_spans("s00_healthy")
        graph = build_graph(spans, expected.trace_ref, 1, 2, IngestLimits())
        assert graph.roots == expected.roots
        assert graph.parent_index == expected.parent_index
        assert graph.children_index == expected.children_index
        assert graph.quality.issues == []
        assert graph.quality.orphan_count == 0
        assert graph.quality.unknown_service_count == 0
        assert graph.assembly_state == "open"

    def test_s01_retry_children_sorted(self):
        expected, spans = load_fixture_spans("s01_retry")
        graph = build_graph(spans, expected.trace_ref, 1, 2, IngestLimits())
        assert graph.roots == ["0000000000000001"]
        children = graph.children_index["0000000000000001"]
        by_id = {s.span_id: s for s in graph.spans}
        assert children == sorted(children, key=lambda sid: by_id[sid].start_ns)
        assert graph.quality.issues == []

    def test_received_timestamps_and_state_passthrough(self):
        _, spans = load_fixture_spans("s00_healthy")
        graph = build_graph(spans, REF, 10, 20, IngestLimits(), assembly_state="quiet")
        assert graph.first_received_at_ns == 10
        assert graph.last_received_at_ns == 20
        assert graph.assembly_state == "quiet"
        assert graph.quality.issues == []

    def test_forced_close_emits_quality_issue(self):
        _, spans = load_fixture_spans("s00_healthy")
        graph = build_graph(
            spans, REF, 10, 20, IngestLimits(),
            assembly_state="forced", forced_reason="span_limit_reached",
        )
        assert graph.assembly_state == "forced"
        (issue,) = [i for i in graph.quality.issues if i.code == "forced_close"]
        assert issue.scope == "trace"
        assert "span_limit_reached" in issue.details

    def test_open_state_has_no_forced_issue(self):
        _, spans = load_fixture_spans("s00_healthy")
        graph = build_graph(spans, REF, 10, 20, IngestLimits())
        assert all(i.code != "forced_close" for i in graph.quality.issues)


class TestStructure:
    def test_missing_parent_is_issue_not_root(self):
        child = make_span("00" * 7 + "02", parent_span_id="ff" * 8)
        graph = build_graph([child], REF, 1, 2, IngestLimits())
        assert graph.roots == []
        assert graph.parent_index[child.span_id] == "ff" * 8
        assert graph.quality.orphan_count == 1
        iss = issue(graph, "missing_parent")
        assert iss.span_ids == [child.span_id]
        assert "multiple_roots" not in issue_codes(graph)

    def test_missing_parent_subtree_orphans(self):
        orphan = make_span("00" * 7 + "02", parent_span_id="ff" * 8)
        kid = make_span("00" * 7 + "03", parent_span_id=orphan.span_id)
        root = make_span("00" * 7 + "01")
        graph = build_graph([root, orphan, kid], REF, 1, 2, IngestLimits())
        assert graph.roots == [root.span_id]
        assert graph.quality.orphan_count == 2

    def test_multiple_roots(self):
        a = make_span("00" * 7 + "01")
        b = make_span("00" * 7 + "02")
        graph = build_graph([a, b], REF, 1, 2, IngestLimits())
        assert graph.roots == [a.span_id, b.span_id]
        iss = issue(graph, "multiple_roots")
        assert set(iss.span_ids) == {a.span_id, b.span_id}
        assert graph.quality.orphan_count == 0

    def test_cycle_detected_iteratively(self):
        a = make_span("00" * 7 + "01", parent_span_id="00" * 7 + "02")
        b = make_span("00" * 7 + "02", parent_span_id="00" * 7 + "01")
        graph = build_graph([a, b], REF, 1, 2, IngestLimits())
        iss = issue(graph, "cycle")
        assert set(iss.span_ids) == {a.span_id, b.span_id}
        assert graph.roots == []
        assert graph.quality.orphan_count == 2

    def test_cycle_in_one_component_rooted_other_component(self):
        root = make_span("00" * 7 + "09")
        a = make_span("00" * 7 + "01", parent_span_id="00" * 7 + "02")
        b = make_span("00" * 7 + "02", parent_span_id="00" * 7 + "01")
        graph = build_graph([root, a, b], REF, 1, 2, IngestLimits())
        assert graph.roots == [root.span_id]
        assert set(issue(graph, "cycle").span_ids) == {a.span_id, b.span_id}
        assert graph.quality.orphan_count == 2

    def test_children_sorted_by_start_end_id(self):
        root = make_span("00" * 7 + "01")
        c1 = make_span("00" * 7 + "02", parent_span_id=root.span_id, start_ns=T0 + 20)
        c2 = make_span("00" * 7 + "03", parent_span_id=root.span_id, start_ns=T0 + 10)
        c3 = make_span(
            "00" * 7 + "04",
            parent_span_id=root.span_id,
            start_ns=T0 + 10,
            end_ns=T0 + 50,
            duration_ns=50,
        )
        c4 = make_span(
            "00" * 7 + "05",
            parent_span_id=root.span_id,
            start_ns=T0 + 10,
            end_ns=T0 + 50,
            duration_ns=50,
        )
        graph = build_graph([c1, c4, c2, c3, root], REF, 1, 2, IngestLimits())
        children = graph.children_index[root.span_id]
        assert children == [c3.span_id, c4.span_id, c2.span_id, c1.span_id]


class TestDepthLimit:
    def chain(self, n: int) -> list[NormalizedSpan]:
        spans = [make_span(f"{1:016x}")]
        for i in range(1, n):
            spans.append(make_span(f"{i + 1:016x}", parent_span_id=f"{i:016x}"))
        return spans

    def test_depth_limit_flags_deeper_spans(self):
        spans = self.chain(4)
        graph = build_graph(spans, REF, 1, 2, IngestLimits(max_depth=2))
        iss = issue(graph, "depth_limit")
        assert iss.span_ids == ["0000000000000003", "0000000000000004"]
        assert graph.quality.orphan_count == 0

    def test_deep_chain_within_limit_no_issue_no_recursion(self):
        spans = self.chain(5000)
        graph = build_graph(spans, REF, 1, 2, IngestLimits(max_depth=10_000))
        assert "depth_limit" not in issue_codes(graph)
        assert graph.quality.orphan_count == 0


class TestQuality:
    def test_unknown_service_count(self):
        known = make_span("00" * 7 + "01")
        unknown = make_span("00" * 7 + "02", service="")
        graph = build_graph([known, unknown], REF, 1, 2, IngestLimits())
        assert graph.quality.unknown_service_count == 1

    def test_duplicate_packet_does_not_double(self):
        a = make_span("00" * 7 + "01")
        graph = build_graph([a, a], REF, 1, 2, IngestLimits())
        assert len(graph.spans) == 1
        assert graph.quality.conflicting_span_count == 0
        assert graph.quality.issues == []

    def test_conflicting_duplicate_counted_first_accepted(self):
        a = make_span("00" * 7 + "01")
        b = make_span("00" * 7 + "01", name="different")
        graph = build_graph([a, b], REF, 1, 2, IngestLimits())
        assert len(graph.spans) == 1
        assert graph.spans[0].name == a.name
        assert graph.quality.conflicting_span_count == 1
        assert "conflicting_span" in issue_codes(graph)


class TestCrossClock:
    def test_overlapping_different_domains_flagged(self):
        parent = make_span(
            "00" * 7 + "01",
            clock_domain="host-1",
            start_ns=T0,
            end_ns=T0 + 100,
            duration_ns=100,
        )
        child = make_span(
            "00" * 7 + "02",
            parent_span_id=parent.span_id,
            clock_domain="host-2",
            start_ns=T0 + 10,
            end_ns=T0 + 50,
            duration_ns=40,
        )
        graph = build_graph([parent, child], REF, 1, 2, IngestLimits())
        iss = issue(graph, "cross_clock_uncertain")
        assert set(iss.span_ids) == {parent.span_id, child.span_id}

    def test_same_domain_not_flagged(self):
        parent = make_span("00" * 7 + "01")
        child = make_span(
            "00" * 7 + "02",
            parent_span_id=parent.span_id,
            start_ns=T0 + 10,
            end_ns=T0 + 50,
            duration_ns=40,
        )
        graph = build_graph([parent, child], REF, 1, 2, IngestLimits())
        assert "cross_clock_uncertain" not in issue_codes(graph)

    def test_non_overlapping_different_domains_not_flagged(self):
        parent = make_span(
            "00" * 7 + "01",
            clock_domain="host-1",
            start_ns=T0,
            end_ns=T0 + 10,
            duration_ns=10,
        )
        child = make_span(
            "00" * 7 + "02",
            parent_span_id=parent.span_id,
            clock_domain="host-2",
            start_ns=T0 + 20,
            end_ns=T0 + 50,
            duration_ns=30,
        )
        graph = build_graph([parent, child], REF, 1, 2, IngestLimits())
        assert "cross_clock_uncertain" not in issue_codes(graph)

    def test_unknown_clock_domain_not_flagged(self):
        parent = make_span("00" * 7 + "01", clock_domain=None)
        child = make_span(
            "00" * 7 + "02",
            parent_span_id=parent.span_id,
            clock_domain="host-2",
            start_ns=T0 + 10,
            end_ns=T0 + 50,
            duration_ns=40,
        )
        graph = build_graph([parent, child], REF, 1, 2, IngestLimits())
        assert "cross_clock_uncertain" not in issue_codes(graph)
