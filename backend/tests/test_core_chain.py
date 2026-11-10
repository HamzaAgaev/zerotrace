from __future__ import annotations

import json
from pathlib import Path

from contracts import IngestLimits, NormalizedSpan, TraceGraph
from core import build_graph, derive_calls, extract_chain
from core.chain import ChainRefusal, ExtractedChain

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"
LIMITS = IngestLimits()


def load_graph(name: str) -> TraceGraph:
    return TraceGraph.from_dict(json.loads((FIXTURES / name / "trace.json").read_text()))


def make_span(
    trace_id: str,
    span_id: str,
    parent: str | None,
    service: str,
    kind: str,
    start_ns: int,
    duration_ns: int,
    operation: str,
    target: str | None = None,
) -> NormalizedSpan:
    return NormalizedSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        service=service,
        name=f"{kind.upper()} {operation}",
        kind=kind,  # type: ignore[arg-type]
        start_ns=start_ns,
        end_ns=start_ns + duration_ns,
        duration_ns=duration_ns,
        clock_domain="host-1",
        operation=operation,
        target=target,
        otel_status="unset",
        field_coverage={},
        diagnostics=[],
    )


class TestS00Healthy:
    def test_single_step_external_last(self):
        graph = load_graph("s00_healthy")
        calls = derive_calls(graph)
        result = extract_chain(graph, calls)
        assert isinstance(result, ExtractedChain)
        assert len(result.calls) == 1
        step = result.calls[0]
        assert step.caller == "orders"
        assert step.callee == "payment"
        assert step.operation == "POST /pay"
        assert step.server_observed is False
        assert step.http_status == 200
        assert step.attempts == 1
        assert result.incompleteness


class TestS01Retry:
    def test_attempts_do_not_multiply_steps(self):
        graph = load_graph("s01_retry")
        calls = derive_calls(graph)
        result = extract_chain(graph, calls)
        assert isinstance(result, ExtractedChain)
        assert len(result.calls) == 1
        assert result.calls[0].attempts == 3
        assert result.calls[0].http_status == 503


class TestRefusals:
    T = 1_000_000_000
    TRACE = "cc" * 16

    def build(self, spans: list[NormalizedSpan]) -> TraceGraph:
        return build_graph(spans, graph_ref(self.TRACE), self.T, self.T + 1, LIMITS)

    def test_empty_trace(self):
        graph = self.build([])
        result = extract_chain(graph, derive_calls(graph))
        assert isinstance(result, ChainRefusal)
        assert result.code == "empty_trace"

    def test_multiple_roots(self):
        a = make_span(self.TRACE, "00" * 7 + "01", None, "orders", "server", self.T, 100, "GET /a")
        b = make_span(self.TRACE, "00" * 7 + "02", None, "orders", "server", self.T, 100, "GET /b")
        graph = self.build([a, b])
        result = extract_chain(graph, derive_calls(graph))
        assert isinstance(result, ChainRefusal)
        assert result.code == "ambiguous_root"

    def test_root_without_outgoing_calls(self):
        root = make_span(
            self.TRACE, "00" * 7 + "01", None, "orders", "server", self.T, 100, "GET /a"
        )
        graph = self.build([root])
        result = extract_chain(graph, derive_calls(graph))
        assert isinstance(result, ChainRefusal)
        assert result.code == "missing_link"

    def test_unknown_target_is_missing_link(self):
        root = make_span(
            self.TRACE, "00" * 7 + "01", None, "orders", "server", self.T, 200, "GET /a"
        )
        client = make_span(
            self.TRACE,
            "00" * 7 + "02",
            root.span_id,
            "orders",
            "client",
            self.T + 10,
            100,
            "POST /x",
            target=None,
        )
        graph = self.build([root, client])
        result = extract_chain(graph, derive_calls(graph))
        assert isinstance(result, ChainRefusal)
        assert result.code == "missing_link"

    def test_parallel_branches(self):
        root = make_span(
            self.TRACE, "00" * 7 + "01", None, "orders", "server", self.T, 400, "GET /a"
        )
        c1 = make_span(
            self.TRACE,
            "00" * 7 + "02",
            root.span_id,
            "orders",
            "client",
            self.T + 10,
            100,
            "POST /x",
            target="a-svc",
        )
        c2 = make_span(
            self.TRACE,
            "00" * 7 + "03",
            root.span_id,
            "orders",
            "client",
            self.T + 20,
            100,
            "POST /y",
            target="b-svc",
        )
        graph = self.build([root, c1, c2])
        result = extract_chain(graph, derive_calls(graph))
        assert isinstance(result, ChainRefusal)
        assert result.code == "parallel_branches"

    def test_two_step_chain(self):
        root = make_span(
            self.TRACE, "00" * 7 + "01", None, "gateway", "server", self.T, 500, "GET /a"
        )
        c1 = make_span(
            self.TRACE,
            "00" * 7 + "02",
            root.span_id,
            "gateway",
            "client",
            self.T + 10,
            300,
            "POST /b",
            target="orders",
        )
        srv = make_span(
            self.TRACE, "00" * 7 + "03", None, "orders", "server", self.T + 20, 200, "POST /b"
        )
        # link server to client via parent for a reliable server pairing
        srv = NormalizedSpan(**{**srv.__dict__, "parent_span_id": c1.span_id})
        c2 = make_span(
            self.TRACE,
            "00" * 7 + "04",
            srv.span_id,
            "orders",
            "client",
            self.T + 30,
            150,
            "POST /pay",
            target="payment",
        )
        graph = build_graph(
            [root, c1, srv, c2],
            graph_ref(self.TRACE),
            self.T,
            self.T + 600,
            LIMITS,
        )
        calls = derive_calls(graph)
        result = extract_chain(graph, calls)
        assert isinstance(result, ExtractedChain)
        assert [s.caller for s in result.calls] == ["gateway", "orders"]
        assert [s.callee for s in result.calls] == ["orders", "payment"]
        assert result.calls[0].server_observed is True
        assert result.calls[1].server_observed is False


def graph_ref(trace_id: str):
    from contracts import TraceRef

    return TraceRef(environment_id="shop", trace_id=trace_id, generation=1, revision=1)
