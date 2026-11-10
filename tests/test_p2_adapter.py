import json
import unittest
from dataclasses import replace
from pathlib import Path

from analytics_support import AnalyticsError
from app.analytics_api.p2_adapter import analytics_trace
from backend.app.data_api.views import span_outcome
from backend.contracts import IngestLimits, NormalizedSpan, TraceRef
from backend.core import build_graph, derive_calls
from backend.ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from comparison import compare, compare_runs
from projection import analyze
from rules import run

OTLP = Path(__file__).resolve().parents[1] / "backend" / "fixtures" / "otlp" / "s01_retry.json"


def packet(run_id=None, groups=(), trace_id="b" * 32, clock="host-1", namespace="default"):
    doc = json.loads(OTLP.read_bytes())
    resource = doc["resourceSpans"][0]
    attr = next(a for a in resource["resource"]["attributes"] if a["key"] == "service.namespace")
    attr["value"]["stringValue"] = namespace
    scope = resource["scopeSpans"][0]
    spans = scope["spans"]
    if run_id:
        spans[0]["attributes"].append({"key": "run_id", "value": {"stringValue": run_id}})
    if groups:
        del spans[len(groups) + 1:]
        for span, (group, index) in zip(spans[1:], groups):
            span["attributes"].extend([
                {"key": "logical_call_id", "value": {"stringValue": group}},
                {"key": "attempt_index", "value": {"intValue": str(index)}},
            ])
    for s in spans:
        s["traceId"] = trace_id
    batch = decode_otlp(json.dumps(doc).encode(), "application/json", None, IngestLimits())
    return normalize(batch, SourceProfile(clock_domain=clock), RedactionProfile()).spans


def graph(spans):
    return build_graph(spans, TraceRef("lab", spans[0].trace_id, 1, 1), 10, 12, IngestLimits(),
                       assembly_state="quiet")


def lab_run(run_id, trace, attempts, revision):
    return {"run_id": run_id, "stand_id": "stand", "environment_id": "lab", "status": "completed",
            "scenario_revision": 1, "fault_revision": 1, "load": {"count": 1}, "config_revision": revision,
            "config_snapshot": {"service_id": "svc-opaque", "revision": revision, "edges": [
                {"call_ref": "charge", "edge_id": trace["attempts"][0]["edge_id"], "max_attempts": attempts}]},
            "trace_collection": {"status": "ready", "expected_count": 1, "received_count": 1,
                                 "trace_refs": [trace["trace_ref"]]}, "traces": [trace]}


class P2AdapterTests(unittest.TestCase):
    def test_real_retry_through_audit_projection_and_cohorts(self):
        g = graph(packet())
        calls = derive_calls(g)
        trace = analytics_trace(g, calls)
        findings = {i["rule_id"]: i for i in run({"environment_id": "lab", "traces": [trace]})["findings"]}
        self.assertEqual(findings["OBS_RETRIES"]["edge_id"], calls.calls[0].edge_id)
        self.assertEqual(findings["R6"]["edge"], {"caller": "orders", "callee": "payment", "operation": "POST /pay"})
        self.assertNotIn("R4", findings)
        request = {"trace_ref": trace["trace_ref"], "changes": [{"span_id": g.spans[1].span_id, "speedup_percent": 50}]}
        self.assertEqual(analyze(trace, request)["gain_ns"], "125000000")
        candidate = graph(packet(trace_id="c"*32))
        other = analytics_trace(candidate, derive_calls(candidate))
        result = compare([trace, other], {"baseline_trace_refs": [trace["trace_ref"]],
                                          "candidate_trace_refs": [other["trace_ref"]]})
        self.assertEqual(result["baseline"]["direct_calls"][0]["edge_id"], calls.calls[0].edge_id)
        self.assertEqual(result["candidate"]["direct_calls"][0]["sample_count"], 3)

    def test_project_and_namespace_participate_in_stable_edge_identity(self):
        first = graph(packet(namespace="checkout"))
        second = graph(packet(namespace="billing", trace_id="c" * 32))
        def adapt(g, project):
            return analytics_trace(g, derive_calls(g, project_id=project), project_id=project)
        a, b = adapt(first, "project-a"), adapt(second, "project-a")
        c = adapt(first, "project-b")
        self.assertEqual(len({d["attempts"][0]["edge_id"] for d in (a,b,c)}), 3)
        self.assertEqual(a["spans"][0]["service"], {"project_id": "project-a", "environment": "lab",
                                                    "namespace": "checkout", "service_name": "orders"})
        self.assertNotIn("service_namespace", packet()[0].to_dict())
        with self.assertRaisesRegex(AnalyticsError, "Different service"):
            compare([a, b], {"baseline_trace_refs": [a["trace_ref"]], "candidate_trace_refs": [b["trace_ref"]],
                             "entry_selector": {"service": "orders"}})

    def test_graph_revision_and_clock_evidence(self):
        g = graph(packet(clock=None))
        calls = derive_calls(g)
        self.assertEqual(calls.calls[0].evidence_codes, ["explicit_resend_marker", "unknown_gaps"])
        self.assertEqual(calls.calls[0].gaps_ns, [])
        self.assertTrue(all(a.gap_before_ns is None for a in calls.attempts))
        trace = analytics_trace(g, calls)
        self.assertIsNone(analyze(trace, {"trace_ref": trace["trace_ref"]})["gain_ns"])
        audit = run({"environment_id": "lab", "traces": [trace]})
        self.assertIn("OBS_RETRIES", [f["rule_id"] for f in audit["findings"]])
        self.assertEqual(next(e for e in audit["rule_evaluations"] if e["rule_id"] == "R5")["eligible_count"], 0)
        heuristic = graph([replace(s, resend_count=None) for s in g.spans])
        self.assertEqual(derive_calls(heuristic).calls[0].hypothesis, "ambiguous")
        with self.assertRaisesRegex(AnalyticsError, "revisions"):
            analytics_trace(g, replace(calls, trace_ref=replace(g.trace_ref, revision=2)))

    def test_lab_labels_round_trip_and_cannot_be_replaced_by_heuristics(self):
        g = graph(packet("before", [("a",1),("a",2),("a",3)]))
        c = graph(packet("after", [("a",1),("a",2)], trace_id="c"*32))
        for s in g.spans:
            self.assertEqual(NormalizedSpan.from_dict(s.to_dict()), s)
        unlabelled = packet()
        self.assertNotIn("run_id", unlabelled[0].to_dict())
        self.assertNotIn("attempt_index", unlabelled[1].to_dict())
        before = lab_run("before", analytics_trace(g, derive_calls(g)), 3, "1")
        after = lab_run("after", analytics_trace(c, derive_calls(c)), 2, "2")
        result = compare_runs(before, after)
        self.assertEqual((result["baseline"]["attempts"], result["candidate"]["attempts"]), (3, 2))
        bad = graph(packet("after", trace_id="c"*32))
        with self.assertRaisesRegex(AnalyticsError, "membership"):
            compare_runs(before, lab_run("after", analytics_trace(bad, derive_calls(bad)), 2, "2"))

    def test_http_4xx_matches_p7_for_server_and_preserves_explicit_error(self):
        def parse(trace_id, status):
            spans = packet(trace_id=trace_id)
            spans[0] = replace(spans[0], http_status=404, otel_status=status)
            spans[1] = replace(spans[1], http_status=404, otel_status="unset", error_type=None)
            g = graph(spans)
            return span_outcome(spans[0]), analytics_trace(g, derive_calls(g))
        first_outcome, first = parse("b"*32, "unset")
        second_outcome, second = parse("c"*32, "error")
        out = compare([first, second], {"baseline_trace_refs": [first["trace_ref"]],
                                        "candidate_trace_refs": [second["trace_ref"]]})
        self.assertEqual((first_outcome, out["baseline"]["root_errors"]), ("ok", 0))
        self.assertEqual((second_outcome, out["candidate"]["root_errors"]), ("error", 1))
        self.assertEqual(first["attempts"][0]["outcome"], "http_error")

    def test_explicit_calls_do_not_merge_independent_groups_on_the_same_edge(self):
        g = graph(packet("run", [("one",1),("two",1),("two",2)]))
        calls = derive_calls(g).calls
        self.assertEqual([(c.hypothesis, c.confidence) for c in calls], [("single","high"),("retry","high")])
        self.assertIn("explicit_lab_labels", calls[1].evidence_codes)

    def test_nested_first_path_depends_on_the_ancestor_attempt(self):
        entry, first, second, _ = packet("run", [("top",1),("top",2),("top",3)])
        spans = [entry, first, second]
        for i, parent in enumerate((first, second)):
            server_id, inner_id = f"{10 + 2*i:016x}", f"{11 + 2*i:016x}"
            server = replace(entry, span_id=server_id, parent_span_id=parent.span_id, service="payment",
                             operation="POST /pay", start_ns=parent.start_ns, end_ns=parent.end_ns,
                             duration_ns=parent.duration_ns, run_id=None)
            inner = replace(parent, span_id=inner_id, parent_span_id=server_id, service="payment",
                            target="fraud", operation="GET /check", logical_call_id=f"inner-{i}", attempt_index=1)
            spans.extend((server, inner))
        attempts = {a.attempt_id: a for a in derive_calls(graph(spans)).attempts}
        self.assertTrue(attempts[f"{11:016x}"].first_path)
        self.assertFalse(attempts[f"{13:016x}"].first_path)
