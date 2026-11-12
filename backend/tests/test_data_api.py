from __future__ import annotations

import dataclasses
import json
from collections.abc import Sequence
from pathlib import Path

from app.data_api import (
    ApiError,
    TraceMeta,
    get_trace,
    list_traces,
)
from app.data_api.views import trace_detail, trace_summary
from contracts import TraceRef, TraceSummary
from contracts.p2 import NormalizedSpan, Quality, TraceGraph
from contracts.p7 import SpanView, TraceDetail

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"
FRONTEND_FIXTURES = Path(__file__).resolve().parent.parent.parent / "frontend" / "fixtures"

GENERATED_AT = "2026-11-11T00:00:00.000Z"


def load_graph(name: str) -> TraceGraph:
    return TraceGraph.from_dict(json.loads((FIXTURES / name / "trace.json").read_text()))


def stub_build_graph(spans: Sequence[NormalizedSpan], trace_ref: TraceRef) -> TraceGraph:
    ids = {s.span_id for s in spans}
    parent_index = {
        s.span_id: (s.parent_span_id if s.parent_span_id in ids else None) for s in spans
    }
    children_index: dict[str, list[str]] = {s.span_id: [] for s in spans}
    for span in spans:
        parent = parent_index[span.span_id]
        if parent is not None:
            children_index[parent].append(span.span_id)
    for children in children_index.values():
        children.sort(key=lambda sid: next(s for s in spans if s.span_id == sid).start_ns)
    roots = sorted(
        (sid for sid, parent in parent_index.items() if parent is None),
        key=lambda sid: next(s for s in spans if s.span_id == sid).start_ns,
    )
    return TraceGraph(
        trace_ref=trace_ref,
        first_received_at_ns=min((s.start_ns for s in spans), default=0),
        last_received_at_ns=max((s.end_ns for s in spans), default=0),
        assembly_state="quiet",
        spans=list(spans),
        roots=roots,
        parent_index=parent_index,
        children_index=children_index,
        quality=Quality(),
    )


class StubStore:
    def __init__(self, graphs: list[TraceGraph]) -> None:
        self._graphs = {g.trace_ref: g for g in graphs}

    def _meta(self, graph: TraceGraph) -> TraceMeta:
        return TraceMeta(
            trace_ref=graph.trace_ref,
            first_received_at_ns=graph.first_received_at_ns,
            last_received_at_ns=graph.last_received_at_ns,
        )

    def list_traces(self, environment_id: str) -> list[TraceMeta]:
        latest: dict[tuple[str, int], TraceGraph] = {}
        for graph in self._graphs.values():
            ref = graph.trace_ref
            if ref.environment_id != environment_id:
                continue
            key = (ref.trace_id, ref.generation)
            if key not in latest or ref.revision > latest[key].trace_ref.revision:
                latest[key] = graph
        return [self._meta(g) for g in latest.values()]

    def trace_refs_for_span(self, environment_id: str, span_id: str) -> set[TraceRef]:
        return {meta.trace_ref for meta in self.list_traces(environment_id)
                if any(span.span_id == span_id for span in self._graphs[meta.trace_ref].spans)}

    def get_trace_meta(
        self,
        environment_id: str,
        trace_id: str,
        generation: int | None = None,
        revision: int | None = None,
    ) -> TraceMeta | None:
        candidates = [
            g
            for g in self._graphs.values()
            if g.trace_ref.environment_id == environment_id and g.trace_ref.trace_id == trace_id
        ]
        if generation is not None:
            candidates = [g for g in candidates if g.trace_ref.generation == generation]
        if revision is not None:
            candidates = [g for g in candidates if g.trace_ref.revision == revision]
        if not candidates:
            return None
        return self._meta(max(candidates, key=lambda g: g.trace_ref.revision))

    def get_spans(self, trace_ref: TraceRef) -> list[NormalizedSpan] | None:
        graph = self._graphs.get(trace_ref)
        return graph.spans if graph is not None else None


def rekey_graph(
    graph: TraceGraph,
    trace_id: str,
    *,
    generation: int = 1,
    revision: int = 1,
    shift_ns: int = 0,
    environment_id: str | None = None,
) -> TraceGraph:
    ref = TraceRef(
        environment_id=environment_id or graph.trace_ref.environment_id,
        trace_id=trace_id,
        generation=generation,
        revision=revision,
    )
    spans = [
        dataclasses.replace(
            s,
            trace_id=trace_id,
            start_ns=s.start_ns + shift_ns,
            end_ns=s.end_ns + shift_ns,
        )
        for s in graph.spans
    ]
    return dataclasses.replace(
        graph,
        trace_ref=ref,
        spans=spans,
        first_received_at_ns=graph.first_received_at_ns + shift_ns,
        last_received_at_ns=graph.last_received_at_ns + shift_ns,
    )


def make_store() -> StubStore:
    return StubStore([load_graph("s00_healthy"), load_graph("s01_retry")])


def test_success_rates_use_known_outcomes_and_ignore_table_filters(monkeypatch) -> None:
    now = 20_000_000_000_000
    minute = 60_000_000_000
    monkeypatch.setattr("app.data_api.handlers.time_ns", lambda: now)
    base = load_graph("s00_healthy")
    samples = [(5, "ok"), (10, "error"), (35, "ok"), (120, "error"),
               (2, "unknown"), (-1, "ok"), (181, "ok")]
    graphs = [rekey_graph(base, f"{index:032x}") for index in range(1, len(samples) + 1)]
    summaries = {
        graph.trace_ref: dataclasses.replace(
            trace_summary(graph), start_ns=now - age * minute, outcome=outcome,
        )
        for graph, (age, outcome) in zip(graphs, samples)
    }
    result = list_traces(StubStore(graphs), "shop", limit=1, status="error",
                         include_success_rates=True, read_summary=summaries.get)
    assert not isinstance(result, ApiError)
    assert result["payload"]["total"] == 2
    assert len(result["payload"]["items"]) == 1
    assert result["payload"]["success_rates"] == {"5": 1, "30": .5, "60": 2 / 3, "180": .5}

    empty = list_traces(StubStore([]), "shop", include_success_rates=True)
    assert not isinstance(empty, ApiError)
    assert empty["payload"]["success_rates"] == dict.fromkeys(("5", "30", "60", "180"))


def test_list_envelope_shape_matches_frontend_fixture() -> None:
    store = make_store()
    result = list_traces(store, "shop", graph_builder=stub_build_graph, generated_at=GENERATED_AT)
    assert not isinstance(result, ApiError)

    fixture = json.loads((FRONTEND_FIXTURES / "frontend-contracts.json").read_text())["list"]
    assert set(result) == set(fixture)
    assert result["schema"] == "zerotrace.trace-list"
    assert result["schema_version"] == "1.0.0"
    assert set(result["payload"]) == set(fixture["payload"])
    fixture_item_keys = set(fixture["payload"]["items"][0])
    for item in result["payload"]["items"]:
        assert set(item) == fixture_item_keys
        summary = TraceSummary.from_dict(item)
        assert isinstance(summary.start_ns, int)
    assert result["payload"]["total"] == 1


def test_list_sorted_first_received_desc_tie_by_trace_id() -> None:
    base = load_graph("s00_healthy")
    older = rekey_graph(base, "cccccccccccccccccccccccccccccccc", shift_ns=-10_000_000_000)
    tie_a = rekey_graph(base, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01")
    tie_b = rekey_graph(base, "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa02")
    store = StubStore([base, older, tie_a, tie_b])

    result = list_traces(store, "shop", graph_builder=stub_build_graph, generated_at=GENERATED_AT)
    assert not isinstance(result, ApiError)
    items = result["payload"]["items"]
    assert [i["trace_id"] for i in items] == [
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa01",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa02",
        base.trace_ref.trace_id,
        "cccccccccccccccccccccccccccccccc",
    ]
    assert result["payload"]["total"] == 4


def test_list_pagination() -> None:
    base = load_graph("s00_healthy")
    graphs = [base] + [
        rekey_graph(base, f"cccccccccccccccccccccccccccccc{i:02d}", shift_ns=-(i + 1) * 1_000_000)
        for i in range(4)
    ]
    store = StubStore(graphs)

    page = list_traces(
        store,
        "shop",
        limit=2,
        offset=1,
        graph_builder=stub_build_graph,
        generated_at=GENERATED_AT,
    )
    assert not isinstance(page, ApiError)
    assert page["payload"]["total"] == 5
    assert len(page["payload"]["items"]) == 2
    assert page["payload"]["items"][0]["trace_id"] == "cccccccccccccccccccccccccccccc00"

    tail = list_traces(
        store,
        "shop",
        limit=2,
        offset=4,
        graph_builder=stub_build_graph,
        generated_at=GENERATED_AT,
    )
    assert not isinstance(tail, ApiError)
    assert len(tail["payload"]["items"]) == 1

    empty = list_traces(
        store,
        "shop",
        limit=2,
        offset=5,
        graph_builder=stub_build_graph,
        generated_at=GENERATED_AT,
    )
    assert not isinstance(empty, ApiError)
    assert empty["payload"]["items"] == []
    assert empty["payload"]["total"] == 5

    other_env = list_traces(
        store, "sandbox", graph_builder=stub_build_graph, generated_at=GENERATED_AT
    )
    assert not isinstance(other_env, ApiError)
    assert other_env["payload"]["total"] == 0


def test_list_rejects_invalid_limit_and_offset() -> None:
    store = make_store()
    for bad_limit in (0, -1, 101):
        result = list_traces(
            store,
            "shop",
            limit=bad_limit,
            graph_builder=stub_build_graph,
            generated_at=GENERATED_AT,
        )
        assert isinstance(result, ApiError)
        assert result.status == 400
        assert result.code == "invalid_limit"
    result = list_traces(
        store, "shop", offset=-1, graph_builder=stub_build_graph, generated_at=GENERATED_AT
    )
    assert isinstance(result, ApiError)
    assert result.status == 400
    assert result.code == "invalid_offset"


def test_get_trace_detail_spans_and_offsets() -> None:
    store = make_store()
    result = get_trace(
        store,
        stub_build_graph,
        "shop",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        generated_at=GENERATED_AT,
    )
    assert not isinstance(result, ApiError)
    assert result["schema"] == "zerotrace.trace"

    detail = TraceDetail.from_dict(result["payload"])
    assert detail.summary.trace_id == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert detail.summary.operation == "GET /checkout"
    assert detail.summary.service == "orders"
    assert detail.summary.span_count == 2
    assert detail.summary.service_count == 1
    assert detail.summary.outcome == "ok"
    assert detail.summary.http_status == 200
    assert detail.summary.start_ns == 1763000000000000000
    assert detail.summary.duration_ns == 500_000_000
    assert detail.quality_issues == []

    root, client = detail.spans
    assert root.start_offset_ns == 0
    assert client.start_offset_ns == 10_000_000
    assert root.kind == "server"
    assert client.kind == "client"
    assert root.outcome == "ok"
    # CLIENT http_status is not used for derivation: otel unset -> unknown.
    assert client.outcome == "unknown"
    for raw_span in result["payload"]["spans"]:
        assert SpanView.from_dict(raw_span).span_id


def test_paired_client_is_compacted_regardless_of_status() -> None:
    source = make_store().get_spans(TraceRef("shop", "a" * 32, 1, 1))
    assert source is not None
    root, original_client = source
    client = dataclasses.replace(original_client, http_status=200, otel_status="unset")
    server = dataclasses.replace(
        root, span_id="f" * 16, parent_span_id=client.span_id, service="payment",
    )
    ref = TraceRef("shop", "a" * 32, 1, 1)
    detail = trace_detail(stub_build_graph([root, client, server], ref))
    assert detail.compactable_client_spans == {client.span_id: server.span_id}
    assert len(detail.spans) == 3
    failed = dataclasses.replace(client, otel_status="error")
    assert trace_detail(stub_build_graph([root, failed, server], ref)).compactable_client_spans == {client.span_id: server.span_id}
    timed_out = dataclasses.replace(client, http_status=504, otel_status="error", error_type="timeout")
    assert trace_detail(stub_build_graph([root, timed_out, server], ref)).compactable_client_spans == {client.span_id: server.span_id}
    second = dataclasses.replace(server, span_id="e" * 16)
    assert trace_detail(stub_build_graph([root, client, server, second], ref)).compactable_client_spans is None
    assert trace_detail(stub_build_graph([root, client], ref)).compactable_client_spans is None


def test_get_trace_error_outcome_from_root_server_span() -> None:
    store = make_store()
    result = get_trace(
        store,
        stub_build_graph,
        "sandbox",
        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        generated_at=GENERATED_AT,
    )
    assert not isinstance(result, ApiError)
    detail = TraceDetail.from_dict(result["payload"])
    assert detail.summary.outcome == "error"
    assert detail.summary.http_status == 503
    assert detail.spans[0].outcome == "error"


def test_get_trace_version_resolution() -> None:
    base = load_graph("s00_healthy")
    rev2 = rekey_graph(base, base.trace_ref.trace_id, revision=2)
    store = StubStore([base, rev2])

    latest = get_trace(
        store, stub_build_graph, "shop", base.trace_ref.trace_id, generated_at=GENERATED_AT
    )
    assert not isinstance(latest, ApiError)
    assert latest["payload"]["trace_ref"]["revision"] == 2

    exact = get_trace(
        store,
        stub_build_graph,
        "shop",
        base.trace_ref.trace_id,
        generation=1,
        revision=1,
        generated_at=GENERATED_AT,
    )
    assert not isinstance(exact, ApiError)
    assert exact["payload"]["trace_ref"]["revision"] == 1

    missing = get_trace(
        store,
        stub_build_graph,
        "shop",
        base.trace_ref.trace_id,
        generation=1,
        revision=99,
        generated_at=GENERATED_AT,
    )
    assert isinstance(missing, ApiError)
    assert missing.status == 404


def test_get_trace_requires_version_pair() -> None:
    store = make_store()
    for generation, revision in ((1, None), (None, 1)):
        result = get_trace(
            store,
            stub_build_graph,
            "shop",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            generation=generation,
            revision=revision,
            generated_at=GENERATED_AT,
        )
        assert isinstance(result, ApiError)
        assert result.status == 400
        assert result.code == "version_pair_required"
        assert result.to_dict()["error"]["retryable"] is False


def test_get_trace_not_found_and_invalid_id() -> None:
    store = make_store()
    missing = get_trace(
        store,
        stub_build_graph,
        "shop",
        "dddddddddddddddddddddddddddddddd",
        generated_at=GENERATED_AT,
    )
    assert isinstance(missing, ApiError)
    assert missing.status == 404
    assert missing.code == "trace_unavailable"

    wrong_env = get_trace(
        store,
        stub_build_graph,
        "sandbox",
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        generated_at=GENERATED_AT,
    )
    assert isinstance(wrong_env, ApiError)
    assert wrong_env.status == 404

    for bad_id in ("abc", "A" * 32, "0" * 32):
        invalid = get_trace(store, stub_build_graph, "shop", bad_id, generated_at=GENERATED_AT)
        assert isinstance(invalid, ApiError)
        assert invalid.status == 400
        assert invalid.code == "invalid_trace_id"


def test_quality_issue_codes_multiple_roots_and_cross_clock() -> None:
    base = load_graph("s00_healthy")
    orphan = dataclasses.replace(
        base.spans[1],
        span_id="0000000000000009",
        parent_span_id=None,
        clock_domain="host-2",
    )
    graph = dataclasses.replace(base, spans=[*base.spans, orphan])
    store = StubStore([graph])
    result = list_traces(store, "shop", graph_builder=stub_build_graph, generated_at=GENERATED_AT)
    assert not isinstance(result, ApiError)
    (item,) = result["payload"]["items"]
    assert "multiple_roots" in item["quality_issues"]
    assert "cross_clock_uncertain" in item["quality_issues"]


def test_list_search_filters_before_pagination() -> None:
    base = load_graph("s00_healthy")
    other = rekey_graph(base, "cccccccccccccccccccccccccccccccc", shift_ns=-1_000_000)
    store = StubStore([base, other])

    page = list_traces(
        store, "shop", limit=1, query=other.trace_ref.trace_id.upper(),
        sort="recent", graph_builder=stub_build_graph,
    )

    assert not isinstance(page, ApiError)
    payload = page["payload"]
    assert [item["trace_id"] for item in payload["items"]] == [other.trace_ref.trace_id]
    assert payload["total"] == 1
    assert sum(payload["outcomes"].values()) == 1
    assert payload["items"][0]["service"] in payload["services"]
