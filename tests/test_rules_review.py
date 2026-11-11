import unittest

from analytics_support import AnalyticsError
from rules import build_reference, run, verify_symptom
from tests.test_rules import EDGE, MS, REF, by_rule, ev, group, model, policy


class AuditReviewTests(unittest.TestCase):
    def test_p4_snapshot_binding_and_field_capabilities(self):
        p = policy()
        snap = {"environment_id": "lab", "service_id": "orders", "revision": "12", "source": "runtime",
                "capabilities": {"apply_recommendation": False, "write_fields": []}, "edges": [p]}
        self.assertFalse(by_rule(run(model(), [snap]), "R4")["recommendations"][0]["intents"])
        snap["capabilities"] = p["capabilities"]
        p["service_id"] = "payment"
        p["config_revision"] = "777"
        self.assertIsNone(by_rule(run(model(), [snap]), "R4"))

    def test_display_name_is_not_a_registered_write_target(self):
        edge = policy()
        del edge["service_id"]
        owner = {"project_id": "p", "environment": "lab", "namespace": "payments", "service_name": "orders"}
        snap = {"service": owner, "revision": "12", "source": "runtime",
                "capabilities": edge["capabilities"], "edges": [edge]}
        self.assertEqual(by_rule(run(model(), [snap]), "R4")["recommendations"][0]["intents"], [])
        snap["service_id"] = "svc-18"
        recommendation = by_rule(run(model(), [snap]), "R4")["recommendations"][0]
        self.assertEqual(recommendation["intents"][0]["target"]["service_id"], "svc-18")

    def test_historical_policy_must_match_attempt_not_just_top_level_revision(self):
        rows, call = group()
        rows[1]["config_revision"] = "obsolete"
        audit = run(model(rows, [call]), [policy()])
        self.assertIsNone(by_rule(audit, "R4"))
        self.assertNotEqual(ev(audit, "R4")["status"], "evaluated")
        self.assertIsNotNone(by_rule(audit, "OBS_RETRIES"))

    def test_an_audit_cannot_count_two_revisions_or_relabel_a_nested_call(self):
        rows, call = group()
        next_ref = {**REF, "revision": REF["revision"] + 1}
        other_ref = {**REF, "trace_id": "b" * 32}
        with self.assertRaisesRegex(AnalyticsError, "multiple revisions"):
            run(model(rows + [{**a, "trace_ref": next_ref} for a in rows], [call, {**call, "trace_ref": next_ref}]))
        with self.assertRaisesRegex(AnalyticsError, "different trace"):
            run(model(traces=[{"trace_ref": REF, "calls": [{**call, "trace_ref": other_ref}]}]))

    def test_source_loss_prevents_complete_call_claim(self):
        rows, call = group()
        trace = {"trace_ref": REF, "quality": {"source_loss": True}}
        audit = run(model(rows, [call], traces=[trace]))
        self.assertIsNone(by_rule(audit, "OBS_RETRIES"))
        self.assertIn("unreliable_trace_evidence", ev(audit, "OBS_RETRIES")["excluded_by_reason"])

    def test_r1_r2_reject_unrelated_spans_and_allow_internal_wrappers(self):
        outer, c1 = group("outer", 1, "outer")
        inner, c2 = group("inner", 1, parent="outer-1")
        outer[0]["server_duration_ns"] = 100 * MS
        inner[0]["offset_in_caller_ns"] = 60 * MS
        spans = [
            dict(span_id="outer-1", kind="client", parent_span_id=None),
            dict(span_id="server-outer-1", kind="server", parent_span_id="outer-1"),
            dict(span_id="work", kind="internal", parent_span_id="server-outer-1"),
            dict(span_id="inner-1", kind="client", parent_span_id="work")]
        data = model(outer + inner, [c1,c2], traces=[{"trace_ref": REF, "spans":spans}])
        def rules():
            aud = run(data, [], {"min_cases": 1})
            return bool(by_rule(aud, "R1")), bool(by_rule(aud, "R2"))
        self.assertEqual(rules(), (True, True))
        spans[-1]["parent_span_id"] = "different-owner"
        self.assertEqual(rules(), (True, False))
        spans[1]["parent_span_id"] = "different-client"
        self.assertEqual(rules(), (False, False))

    def test_nested_retry_admission_counts_healthy_paths_and_reports_deepest_edge(self):
        attempts, calls = [], []
        for i, limit in enumerate((2, 1)):
            ref = {**REF, "trace_id": format(i+1, "032x")}
            def chain(depth, name, parent=None, first=True):
                rows, call = group(name, limit, edge=("outer", "inner", "deep")[depth], parent=parent)
                call["trace_ref"] = ref
                calls.append(call)
                for j, row in enumerate(rows):
                    row.update(trace_ref=ref, first_path=first and j==0)
                    attempts.append(row)
                    if depth < 2:
                        chain(depth + 1, name+str(j), row["attempt_id"], first and j==0)
            chain(0, "root")
        audit = run(model(attempts, calls))
        findings = [f for f in audit["findings"] if f["rule_id"] == "R3"]
        self.assertEqual([(f["edge_id"], f["affected_count"], f["denominator_count"]) for f in findings],
                         [("deep", 1, 2)])
        self.assertEqual(findings[0]["evidence"]["observed_attempts"], 8)

    def test_percentage_uses_selected_data_and_unknown_has_no_score(self):
        rows, call = group()
        measured = run(model(rows, [call]), [policy()])
        for f in measured["findings"]:
            self.assertGreater(f["score"], 0)
        config_only = by_rule(run(model(), [policy()]), "R4")
        self.assertIsNone(config_only["score"])
        self.assertIn("denominator", config_only["score_reason"])

    def test_declared_retry_policy_does_not_turn_invalid_client_status_into_a_violation(self):
        rows, call = group(n=2)
        rows[0]["client_http_status"] = 799
        audit = run(model(rows, [call]), [policy(retry_on=[503])])
        self.assertIsNone(by_rule(audit, "R7"))
        self.assertEqual(ev(audit,"R7")["excluded_by_reason"]["invalid_client_http_status"], 1)

    def test_output_durations_fit_p3_integer_domain(self):
        result = run(model(), [policy(per_attempt_timeout_ns=(1 << 63) - 1, max_attempts=2)])
        self.assertIsNone(by_rule(result, "R4"))
        self.assertEqual(ev(result, "R4")["excluded_by_reason"]["budget_outside_int64"], 1)

    def test_verification_requires_same_detection_semantics(self):
        rows, call = group()
        data = model(rows, [call])
        previous = by_rule(run(data, [policy()]), "R4")
        # The operator also changed the thresholds/rules between audit epochs.
        changed = run(data, [policy(max_attempts=2)], {"budget_high_ratio":2})
        self.assertEqual(verify_symptom(previous, changed, comparable=True, grace_elapsed=True, minimum_calls=1),
                         "inconclusive")

    def test_first_path_label_cannot_belong_to_later_attempt(self):
        outer, root = group("outer", 2, "outer")
        attempts, calls = list(outer), [root]
        for i, parent in enumerate(outer):
            parts, child = group(f"inner{i}", 2, parent=parent["attempt_id"])
            for j, part in enumerate(parts):
                part["first_path"] = i == 1 and j == 1
            attempts.extend(parts)
            calls.append(child)
        out = run(model(attempts, calls))
        self.assertIsNone(by_rule(out, "R3"))
        self.assertIn("missing_first_path", ev(out,"R3")["excluded_by_reason"])

    def test_baselines_reject_duplicate_revisions(self):
        rows, call = group(n=1)
        dims = dict(environment_id="lab", edge_id=EDGE,caller_version="a",callee_version="b",workload_id="w")
        rows[0].update(dims, outcome="ok")
        dup = {**rows[0], "trace_ref": {**REF,"revision":3}}
        with self.assertRaisesRegex(ValueError,"multiple revisions"):
            build_reference(dims,[rows[0],dup],{"mode":"all_received","known_losses":False},version="v1",
                            created_at="2026-01-01T00:00:00Z",valid_until="2026-12-01T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
