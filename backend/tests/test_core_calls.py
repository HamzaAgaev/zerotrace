from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contracts import NormalizedSpan, Quality, TraceGraph, TraceRef
from core.calls import derive_calls

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"

MS = 1_000_000
BASE_NS = 1_763_000_000_000_000_000


def load_trace(name: str) -> TraceGraph:
    data = json.loads((FIXTURES / name / "trace.json").read_text())
    return TraceGraph.from_dict(data)


def make_span(
    span_id: str,
    parent: str | None,
    kind: str,
    start_ns: int,
    duration_ns: int,
    trace_id: str = "cc" * 16,
    service: str = "orders",
    operation: str = "GET /checkout",
    clock_domain: str | None = "host-1",
    **kw: Any,
) -> NormalizedSpan:
    args: dict[str, Any] = dict(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        service=service,
        name=f"{kind.upper()} {operation}",
        kind=kind,
        start_ns=start_ns,
        end_ns=start_ns + duration_ns,
        duration_ns=duration_ns,
        clock_domain=clock_domain,
        operation=operation,
        otel_status="unset",
        field_coverage={"service.name": True, "operation": True},
        diagnostics=[],
    )
    args.update(kw)
    return NormalizedSpan(**args)


def make_graph(spans: list[NormalizedSpan], env: str = "shop") -> TraceGraph:
    trace_id = spans[0].trace_id
    ref = TraceRef(environment_id=env, trace_id=trace_id, generation=1, revision=1)
    by_id = {s.span_id: s for s in spans}
    parent_index = {s.span_id: s.parent_span_id for s in spans}
    children: dict[str, list[str]] = {s.span_id: [] for s in spans}
    roots: list[str] = []
    for s in spans:
        if s.parent_span_id is None or s.parent_span_id not in by_id:
            roots.append(s.span_id)
        else:
            children[s.parent_span_id].append(s.span_id)
    for kids in children.values():
        kids.sort(key=lambda sid: (by_id[sid].start_ns, by_id[sid].end_ns, sid))
    last = max(s.end_ns for s in spans)
    return TraceGraph(
        trace_ref=ref,
        first_received_at_ns=min(s.start_ns for s in spans),
        last_received_at_ns=last,
        assembly_state="quiet",
        spans=spans,
        roots=roots,
        parent_index=parent_index,
        children_index=children,
        quality=Quality(),
    )


def sid(n: int) -> str:
    return f"{n:016x}"


class TestFixtures:
    def test_s00_single_call(self):
        model = derive_calls(load_trace("s00_healthy"))
        assert len(model.attempts) == 1
        assert len(model.calls) == 1
        attempt = model.attempts[0]
        call = model.calls[0]
        assert attempt.attempt_id == sid(2)
        assert attempt.outcome == "ok"
        assert attempt.client_http_status == 200
        assert attempt.call_id == call.call_id
        assert attempt.attempt_index == 1
        assert attempt.gap_before_ns is None
        assert attempt.first_path is True
        assert call.hypothesis == "single"
        assert call.confidence == "high"
        assert call.evidence_codes == ["single_attempt"]
        assert call.gaps_ns == []
        assert call.attempt_ids == [sid(2)]

    def test_s01_three_attempts_retry_with_gaps(self):
        model = derive_calls(load_trace("s01_retry"))
        assert len(model.attempts) == 3
        assert len(model.calls) == 1
        call = model.calls[0]
        assert call.hypothesis == "retry"
        assert call.gaps_ns == [50 * MS, 50 * MS]
        assert call.attempt_ids == [sid(2), sid(3), sid(4)]
        assert "error_then_next_call" in call.evidence_codes
        for i, attempt in enumerate(model.attempts):
            assert attempt.outcome == "http_error"
            assert attempt.attempt_index == i + 1
            assert attempt.call_id == call.call_id
            assert attempt.gap_before_ns == (None if i == 0 else 50 * MS)
            assert attempt.first_path == (i == 0)

    def test_grouping_version_propagated(self):
        model = derive_calls(load_trace("s00_healthy"), grouping_version="2.1.0")
        assert model.grouping_version == "2.1.0"
        assert derive_calls(load_trace("s00_healthy")).grouping_version == "1.1.0"


class TestGrouping:
    def test_loop_five_successful_calls_are_five_singles(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 3_000 * MS),
        ]
        for i in range(5):
            spans.append(
                make_span(
                    sid(10 + i),
                    sid(1),
                    "client",
                    BASE_NS + (100 + i * 300) * MS,
                    200 * MS,
                    operation="GET /items",
                    http_status=200,
                )
            )
        model = derive_calls(make_graph(spans))
        assert len(model.attempts) == 5
        assert len(model.calls) == 5
        assert {c.hypothesis for c in model.calls} == {"single"}
        assert len({c.call_id for c in model.calls}) == 5
        assert all(a.first_path is True for a in model.attempts)

    def test_error_then_success_is_retry_hypothesis(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(
                sid(2),
                sid(1),
                "client",
                BASE_NS + 10 * MS,
                250 * MS,
                operation="POST /pay",
                otel_status="error",
                http_status=503,
            ),
            make_span(
                sid(3),
                sid(1),
                "client",
                BASE_NS + 310 * MS,
                250 * MS,
                operation="POST /pay",
                http_status=200,
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert len(model.calls) == 1
        call = model.calls[0]
        assert call.hypothesis == "retry"
        assert call.confidence == "medium"
        assert call.gaps_ns == [50 * MS]
        assert call.attempt_ids == [sid(2), sid(3)]

    def test_overlap_without_confirmation_is_ambiguous(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "client", BASE_NS + 10 * MS, 300 * MS, operation="POST /pay"),
            make_span(
                sid(3), sid(1), "client", BASE_NS + 110 * MS, 300 * MS, operation="POST /pay"
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert len(model.calls) == 1
        call = model.calls[0]
        assert call.hypothesis == "ambiguous"
        assert call.evidence_codes == ["overlap_unconfirmed"]
        assert call.gaps_ns == []
        assert all(a.first_path is None for a in model.attempts)
        assert all(a.gap_before_ns is not None or a.attempt_index == 1 for a in model.attempts)

    def test_explicit_resend_marker_beats_heuristic(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(
                sid(2),
                sid(1),
                "client",
                BASE_NS + 10 * MS,
                200 * MS,
                operation="POST /pay",
                http_status=200,
                resend_count=0,
            ),
            make_span(
                sid(3),
                sid(1),
                "client",
                BASE_NS + 310 * MS,
                200 * MS,
                operation="POST /pay",
                http_status=200,
                resend_count=1,
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert len(model.calls) == 1
        call = model.calls[0]
        assert call.hypothesis == "retry"
        assert call.confidence == "high"
        assert "explicit_resend_marker" in call.evidence_codes
        assert call.gaps_ns == [100 * MS]

    def test_confirmed_overlap_is_hedge(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(
                sid(2),
                sid(1),
                "client",
                BASE_NS + 10 * MS,
                300 * MS,
                operation="POST /pay",
                resend_count=0,
            ),
            make_span(
                sid(3),
                sid(1),
                "client",
                BASE_NS + 110 * MS,
                300 * MS,
                operation="POST /pay",
                resend_count=1,
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert len(model.calls) == 1
        call = model.calls[0]
        assert call.hypothesis == "hedge"
        assert call.gaps_ns == []


class TestCalleeResolution:
    def test_multiple_server_children_ambiguous(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "client", BASE_NS + 10 * MS, 300 * MS, operation="POST /pay"),
            make_span(
                sid(3),
                sid(2),
                "server",
                BASE_NS + 20 * MS,
                200 * MS,
                service="payments",
                operation="POST /pay",
            ),
            make_span(
                sid(4),
                sid(2),
                "server",
                BASE_NS + 30 * MS,
                200 * MS,
                service="payments",
                operation="POST /pay",
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert len(model.attempts) == 1
        attempt = model.attempts[0]
        assert attempt.server_span_id is None
        assert attempt.server_duration_ns is None
        assert "ambiguous_server_link" in attempt.classification_basis

    def test_single_server_child_linked(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "client", BASE_NS + 10 * MS, 300 * MS, operation="POST /pay"),
            make_span(
                sid(3),
                sid(2),
                "server",
                BASE_NS + 20 * MS,
                200 * MS,
                service="payments",
                operation="POST /pay",
                http_status=200,
            ),
        ]
        model = derive_calls(make_graph(spans))
        attempt = model.attempts[0]
        assert attempt.server_span_id == sid(3)
        assert attempt.server_duration_ns == 200 * MS
        assert attempt.server_http_status == 200
        assert attempt.caller_server_span_id == sid(1)
        assert attempt.offset_in_caller_ns == 10 * MS

    def test_caller_fallback_without_server_ancestor(self):
        spans = [
            make_span(
                sid(2),
                None,
                "client",
                BASE_NS + 10 * MS,
                300 * MS,
                operation="POST /pay",
                target="payments.internal",
            ),
        ]
        model = derive_calls(make_graph(spans))
        attempt = model.attempts[0]
        assert attempt.caller_server_span_id is None
        assert attempt.offset_in_caller_ns is None


class TestOutcome:
    def test_unknown_attrs_give_unknown_outcome(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "client", BASE_NS + 10 * MS, 300 * MS, operation=""),
        ]
        model = derive_calls(make_graph(spans))
        attempt = model.attempts[0]
        assert attempt.outcome == "outcome_unknown"
        assert attempt.confidence == "low"
        assert attempt.client_http_status is None
        assert attempt.error_type is None

    def test_unset_status_without_error_is_not_ok(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "client", BASE_NS + 10 * MS, 300 * MS, operation="POST /pay"),
        ]
        model = derive_calls(make_graph(spans))
        assert model.attempts[0].outcome == "outcome_unknown"

    def test_timeout_from_error_type(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(
                sid(2),
                sid(1),
                "client",
                BASE_NS + 10 * MS,
                300 * MS,
                operation="POST /pay",
                otel_status="error",
                error_type="context.deadline exceeded",
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert model.attempts[0].outcome == "timeout"

    def test_grpc_deadline_exceeded_is_timeout(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(
                sid(2),
                sid(1),
                "client",
                BASE_NS + 10 * MS,
                300 * MS,
                operation="PayService/Pay",
                otel_status="error",
                grpc_status=4,
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert model.attempts[0].outcome == "timeout"


class TestMessagingExcluded:
    def test_producer_consumer_and_async_client_excluded(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "producer", BASE_NS + 10 * MS, 50 * MS, operation="publish"),
            make_span(sid(3), sid(1), "consumer", BASE_NS + 20 * MS, 50 * MS, operation="consume"),
            make_span(
                sid(4),
                sid(1),
                "client",
                BASE_NS + 30 * MS,
                50 * MS,
                operation="POST /cb",
                async_markers=["callback"],
            ),
            make_span(
                sid(5),
                sid(1),
                "client",
                BASE_NS + 40 * MS,
                50 * MS,
                operation="POST /sync",
                http_status=200,
            ),
        ]
        model = derive_calls(make_graph(spans))
        assert [a.attempt_id for a in model.attempts] == [sid(5)]


class TestDeterminism:
    def test_same_input_same_model(self):
        graph = load_trace("s01_retry")
        first = derive_calls(graph)
        second = derive_calls(graph)
        assert first == second
        assert first.to_dict() == second.to_dict()

    def test_edge_id_stable(self):
        graph = load_trace("s00_healthy")
        ids = {a.edge_id for a in derive_calls(graph).attempts}
        assert len(ids) == 1
        assert next(iter(ids)) == derive_calls(graph).attempts[0].edge_id
