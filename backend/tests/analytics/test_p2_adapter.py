"""C's P2 adapter keeps B graph/call evidence intact for analytics."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from analytics_support import AnalyticsError
from app.analytics_api.p2_adapter import analytics_trace
from app.data_api.views import span_outcome
from comparison import compare, compare_runs
from contracts import IngestLimits, NormalizedSpan, TraceRef
from core import build_graph, derive_calls
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from projection import analyze
from rules import run


OTLP = Path(__file__).resolve().parents[2] / "fixtures" / "otlp" / "s01_retry.json"


def packet(*, trace_id: str = "b" * 32, clock: str | None = "host-1", namespace: str = "default"):
    document = json.loads(OTLP.read_bytes())
    resource = document["resourceSpans"][0]
    attribute = next(item for item in resource["resource"]["attributes"] if item["key"] == "service.namespace")
    attribute["value"]["stringValue"] = namespace
    for span in resource["scopeSpans"][0]["spans"]:
        span["traceId"] = trace_id
    batch = decode_otlp(json.dumps(document).encode(), "application/json", None, IngestLimits())
    return normalize(batch, SourceProfile(clock_domain=clock), RedactionProfile()).spans


def graph(spans):
    return build_graph(
        spans,
        TraceRef("lab", spans[0].trace_id, 1, 1),
        10,
        12,
        IngestLimits(),
        assembly_state="quiet",
    )


def lab_run(run_id, trace, attempts, revision):
    return {
        "run_id": run_id,
        "stand_id": "stand",
        "environment_id": "lab",
        "status": "completed",
        "scenario_revision": 1,
        "fault_revision": 1,
        "load": {"count": 1},
        "config_revision": revision,
        "config_snapshot": {
            "service_id": "orders",
            "revision": revision,
            "edges": [
                {
                    "call_ref": "charge",
                    "edge_id": trace["attempts"][0]["edge_id"],
                    "max_attempts": attempts,
                }
            ],
        },
        "trace_collection": {
            "status": "ready",
            "expected_count": 1,
            "received_count": 1,
            "trace_refs": [trace["trace_ref"]],
        },
        "traces": [trace],
    }


class P2AdapterTests(unittest.TestCase):
    def test_retry_is_usable_by_audit_projection_and_cohort_comparison(self):
        current = graph(packet())
        calls = derive_calls(current)
        trace = analytics_trace(current, calls)

        findings = {item["rule_id"]: item for item in run({"environment_id": "lab", "traces": [trace]})["findings"]}
        self.assertEqual(findings["OBS_RETRIES"]["edge_id"], calls.calls[0].edge_id)
        self.assertEqual(findings["R6"]["edge"], {"caller": "orders", "callee": "payment", "operation": "POST /pay"})
        self.assertNotIn("R4", findings)

        request = {"trace_ref": trace["trace_ref"], "changes": [{"span_id": current.spans[1].span_id, "speedup_percent": 50}]}
        self.assertEqual(analyze(trace, request)["gain_ns"], "125000000")

        candidate_graph = graph(packet(trace_id="c" * 32))
        candidate = analytics_trace(candidate_graph, derive_calls(candidate_graph))
        result = compare([trace, candidate], {"baseline_trace_refs": [trace["trace_ref"]], "candidate_trace_refs": [candidate["trace_ref"]]})
        self.assertEqual(result["baseline"]["direct_calls"][0]["edge_id"], calls.calls[0].edge_id)
        self.assertEqual(result["candidate"]["direct_calls"][0]["sample_count"], 3)

    def test_project_and_namespace_participate_in_edge_identity(self):
        first = graph(packet(namespace="checkout"))
        second = graph(packet(trace_id="c" * 32, namespace="billing"))

        def adapt(current, project):
            return analytics_trace(current, derive_calls(current, project_id=project), project_id=project)

        first_a, second_a, first_b = adapt(first, "project-a"), adapt(second, "project-a"), adapt(first, "project-b")
        self.assertEqual(len({item["attempts"][0]["edge_id"] for item in (first_a, second_a, first_b)}), 3)
        self.assertEqual(first_a["spans"][0]["service"], {"project_id": "project-a", "environment": "lab", "namespace": "checkout", "service_name": "orders"})
        with self.assertRaisesRegex(AnalyticsError, "Different service"):
            compare([first_a, second_a], {"baseline_trace_refs": [first_a["trace_ref"]], "candidate_trace_refs": [second_a["trace_ref"]], "entry_selector": {"service": "orders"}})

    def test_unknown_clock_preserves_evidence_and_allows_projection(self):
        current = graph(packet(clock=None))
        calls = derive_calls(current)
        # B 0.3 preserves the sequential interval even if no clock domain is
        # declared; C treats supplied OTLP timestamps as aligned by the user.
        self.assertEqual(calls.calls[0].evidence_codes, ["explicit_resend_marker", "sequential_gaps"])
        self.assertTrue(calls.calls[0].gaps_ns)
        self.assertTrue(any(item.gap_before_ns is not None for item in calls.attempts))
        trace = analytics_trace(current, calls)
        projection = analyze(trace, {"trace_ref": trace["trace_ref"]})
        self.assertEqual(projection["status"], "available")
        self.assertIsNotNone(projection["gain_ns"])
        self.assertIn("timestamps_assumed_aligned", projection["warnings"])
        candidate_graph = graph(packet(trace_id="c" * 32, clock=None))
        candidate = analytics_trace(candidate_graph, derive_calls(candidate_graph))
        comparison = compare([trace, candidate], {
            "baseline_trace_refs": [trace["trace_ref"]],
            "candidate_trace_refs": [candidate["trace_ref"]],
        })
        self.assertIsNotNone(comparison["baseline"]["overlap_ratio"])
        self.assertTrue(any("clock metadata is advisory" in text for text in comparison["assumptions"]))
        audit = run({"environment_id": "lab", "traces": [trace]})
        self.assertIn("OBS_RETRIES", [item["rule_id"] for item in audit["findings"]])
        self.assertEqual(next(item for item in audit["rule_evaluations"] if item["rule_id"] == "R5")["eligible_count"], 0)
        heuristic = graph([replace(span, resend_count=None) for span in current.spans])
        self.assertEqual(derive_calls(heuristic).calls[0].hypothesis, "retry")

    def test_adapter_rejects_graph_and_call_model_from_different_revisions(self):
        current = graph(packet())
        calls = derive_calls(current)
        with self.assertRaisesRegex(AnalyticsError, "revisions"):
            analytics_trace(current, replace(calls, trace_ref=replace(current.trace_ref, revision=2)))

    def test_explicit_span_fields_round_trip_and_http_outcome_is_consistent(self):
        spans = packet()
        self.assertEqual(NormalizedSpan.from_dict(spans[0].to_dict()), spans[0])

        def parse(trace_id, status):
            changed = packet(trace_id=trace_id)
            changed[0] = replace(changed[0], http_status=404, otel_status=status)
            changed[1] = replace(changed[1], http_status=404, otel_status="unset", error_type=None)
            current = graph(changed)
            return span_outcome(changed[0]), analytics_trace(current, derive_calls(current))

        ok_outcome, ok = parse("b" * 32, "unset")
        error_outcome, error = parse("c" * 32, "error")
        compared = compare([ok, error], {"baseline_trace_refs": [ok["trace_ref"]], "candidate_trace_refs": [error["trace_ref"]]})
        self.assertEqual((ok_outcome, compared["baseline"]["root_errors"]), ("ok", 0))
        self.assertEqual((error_outcome, compared["candidate"]["root_errors"]), ("error", 1))
        self.assertEqual(ok["attempts"][0]["outcome"], "http_error")

    def test_nested_calls_keep_a_stable_edge_identity_when_parent_attempts_repeat(self):
        entry, first, second, _ = packet()
        spans = [entry, first, second]
        for index, parent in enumerate((first, second)):
            server_id, inner_id = f"{10 + 2 * index:016x}", f"{11 + 2 * index:016x}"
            server = replace(entry, span_id=server_id, parent_span_id=parent.span_id, service="payment", operation="POST /pay", start_ns=parent.start_ns, end_ns=parent.end_ns, duration_ns=parent.duration_ns)
            inner = replace(parent, span_id=inner_id, parent_span_id=server_id, service="payment", target="fraud", operation="GET /check")
            spans.extend((server, inner))
        attempts = {item.attempt_id: item for item in derive_calls(graph(spans)).attempts}
        self.assertTrue(attempts[f"{11:016x}"].first_path)
        self.assertTrue(attempts[f"{13:016x}"].first_path)
        self.assertEqual(attempts[f"{11:016x}"].edge_id, attempts[f"{13:016x}"].edge_id)
