from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import (
    EdgeKey,
    EndpointKey,
    IngestLimits,
    NormalizedSpan,
    OperationKey,
    ServiceKey,
    TraceGraph,
    ValidationReport,
    percentile_ns,
    ratio,
    validate_span,
    validate_trace_graph,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"


def load_trace(name: str) -> TraceGraph:
    data = json.loads((FIXTURES / name / "trace.json").read_text())
    return TraceGraph.from_dict(data)


def make_edge() -> EdgeKey:
    return EdgeKey(
        caller=ServiceKey("p", "shop", "default", "orders"),
        caller_operation=OperationKey("http", "/checkout", "route_template", "1.0.0", method="GET"),
        callee=EndpointKey.external("payment"),
        callee_operation=OperationKey("http", "/pay", "peer_address", "1.0.0", method="POST"),
    )


class TestEdgeId:
    def test_deterministic_and_stable(self):
        assert make_edge().edge_id() == make_edge().edge_id()
        assert len(make_edge().edge_id()) == 64

    def test_field_order_irrelevant(self):
        a = EdgeKey(
            caller=ServiceKey("p", "shop", "default", "orders"),
            caller_operation=OperationKey(
                "http", "/checkout", "route_template", "1.0.0", method="GET"
            ),
            callee=EndpointKey.external("payment"),
            callee_operation=OperationKey("http", "/pay", "peer_address", "1.0.0", method="POST"),
        )
        b = EdgeKey.from_dict(json.loads(json.dumps(a.to_dict())))
        assert a.edge_id() == b.edge_id()

    def test_different_edges_differ(self):
        other = EdgeKey(
            caller=ServiceKey("p", "shop", "default", "inventory"),
            caller_operation=make_edge().caller_operation,
            callee=make_edge().callee,
            callee_operation=make_edge().callee_operation,
        )
        assert other.edge_id() != make_edge().edge_id()


class TestSpanValidation:
    def base_span(self, **kw) -> NormalizedSpan:
        args = dict(
            trace_id="aa" * 16,
            span_id="00" * 7 + "01",
            parent_span_id=None,
            service="orders",
            name="SERVER /checkout",
            kind="server",
            start_ns=1_000_000_000,
            end_ns=1_500_000_000,
            duration_ns=500_000_000,
            clock_domain="host-1",
            operation="GET /checkout",
            otel_status="unset",
            field_coverage={},
            diagnostics=[],
        )
        args.update(kw)
        return NormalizedSpan(**args)

    def test_valid(self):
        report = ValidationReport()
        validate_span(self.base_span(), report)
        assert report.ok

    @pytest.mark.parametrize("trace_id", ["AA" * 16, "0" * 32, "ab" * 8, "zz" * 16])
    def test_bad_trace_id(self, trace_id):
        report = ValidationReport()
        validate_span(self.base_span(trace_id=trace_id), report)
        assert not report.ok

    def test_end_before_start_is_error(self):
        report = ValidationReport()
        validate_span(self.base_span(start_ns=2_000, end_ns=1_000, duration_ns=0), report)
        assert not report.ok

    def test_duration_mismatch_is_error(self):
        report = ValidationReport()
        validate_span(self.base_span(duration_ns=123), report)
        assert not report.ok

    def test_missing_service_is_coverage_warning_not_error(self):
        report = ValidationReport()
        validate_span(self.base_span(service=""), report)
        assert report.ok
        assert report.warnings


class TestTraceGraphValidation:
    def test_s00_fixture_valid(self):
        report = validate_trace_graph(load_trace("s00_healthy"), IngestLimits())
        assert report.ok, report.errors

    def test_s01_fixture_valid(self):
        report = validate_trace_graph(load_trace("s01_retry"), IngestLimits())
        assert report.ok, report.errors

    def test_missing_parent_warns_not_fails(self):
        g = load_trace("s00_healthy")
        client = g.spans[1]
        moved = NormalizedSpan(**{**client.__dict__, "parent_span_id": "ff" * 8})
        g = TraceGraph(
            **{
                **g.__dict__,
                "spans": [g.spans[0], moved],
                "parent_index": {**g.parent_index, moved.span_id: "ff" * 8},
            }
        )
        report = validate_trace_graph(g, IngestLimits())
        assert report.ok
        assert any("missing_parent" in w for w in report.warnings)

    def test_span_limit_enforced(self):
        g = load_trace("s01_retry")
        limits = IngestLimits(max_spans_per_trace=2)
        report = validate_trace_graph(g, limits)
        assert not report.ok


class TestPercentile:
    def test_empty_is_null(self):
        assert percentile_ns([], 0.95) is None

    def test_single_sample(self):
        assert percentile_ns([42], 0.99) == 42

    def test_linear_interpolation_half_up(self):
        samples = [100, 200, 300, 400]
        assert percentile_ns(samples, 0.5) == 250
        assert percentile_ns(samples, 0.95) == 385
        assert percentile_ns(samples, 0.0) == 100
        assert percentile_ns(samples, 1.0) == 400

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            percentile_ns([1], 1.5)


class TestRatio:
    def test_zero_denominator_is_null_not_zero(self):
        assert ratio(0, 0) is None

    def test_normal(self):
        assert ratio(1, 4) == 0.25


class TestRoundTrip:
    def test_trace_json_roundtrip(self):
        g = load_trace("s01_retry")
        again = TraceGraph.from_dict(json.loads(json.dumps(g.to_dict())))
        assert again == g

    def test_ns_serialized_as_decimal_strings(self):
        g = load_trace("s01_retry")
        raw = json.loads(json.dumps(g.to_dict()))
        assert isinstance(raw["first_received_at_ns"], str)
        assert raw["first_received_at_ns"].isdigit()
        for s in raw["spans"]:
            assert isinstance(s["start_ns"], str)
            assert isinstance(s["duration_ns"], str)
