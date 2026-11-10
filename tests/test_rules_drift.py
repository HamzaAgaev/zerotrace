import unittest

from rules import run, verify_symptom
from tests.test_rules import EDGE, MS, by_rule, ev, group, model, policy


SEGMENT = {"caller_version": "orders/v1", "callee_version": "pay/v1", "workload_id": "checkout",
           "config_revision": "12"}
ALL = {"mode": "all_received", "known_losses": False}


def samples(durations_ms, *, outcome="timeout", scope="per_attempt", sampling=None):
    attempts = []
    for i, duration in enumerate(durations_ms, 1):
        rows, _ = group(n=1)
        row = rows[0]
        row.update(trace_ref={**row["trace_ref"], "trace_id": format(i, "032x")}, timeout_scope=scope,
                   duration_ns=duration * MS, outcome=outcome,
                   client_http_status=None if outcome == "timeout" else 200)
        attempts.append(row)
    return model(attempts, sampling=ALL if sampling is None else sampling, **SEGMENT)


class DriftTests(unittest.TestCase):
    def test_compare_runtime_declared_and_cluster_and_provenance(self):
        runtime = policy(**SEGMENT)
        declared = policy(**SEGMENT, source="declared", per_attempt_timeout_ns=500 * MS)
        data = samples([490, 495, 500, 505, 510])
        out = run(data, [declared, runtime])
        finding = by_rule(out, "D1")
        evidence = finding["evidence"]
        self.assertEqual(evidence["segment"]["edge_id"], EDGE)
        self.assertEqual(evidence["configured"]["per_attempt_timeout_ns"], [
            {"source":"declared", "confidence":"medium", "value":str(500*MS), "config_revision":"12"},
            {"source":"runtime", "confidence":"high", "value":str(250*MS), "config_revision":"12"}])
        cluster = evidence["observed_timeout"]
        self.assertEqual((cluster["value_ns"],cluster["min_ns"],cluster["max_ns"],cluster["trace_count"]),
                         (str(500*MS),str(490*MS),str(510*MS),5))
        differences = [(diff["comparison"], diff.get("source")) for diff in evidence["differences"]]
        self.assertCountEqual(differences, [("runtime_vs_declared", None), ("timeout_cluster_vs_config", "runtime")])
        self.assertEqual(finding["confidence"], "medium")
        self.assertIsNone(by_rule(out, "R4"))
        self.assertEqual(out, run(data,[runtime,declared]))

    def test_healthy_durations_outcomes_and_bad_clusters_abstain(self):
        p = policy(**SEGMENT)
        cases = [
            (samples([500]*8, outcome="ok"),"too_few_independent_timeout_traces"),
            (samples([500]*4),"too_few_independent_timeout_traces"),
            (samples([500]*8, scope="upstream_deadline"),"too_few_independent_timeout_traces"),
            (samples([250,250,250,500,500,500]),"dispersed_timeout_durations"),
            (samples([500]*8, sampling={"mode":"tail","known_losses":False}),"unknown_or_selected_sampling"),
            (samples([500]*8, sampling={"mode":"all_received","known_losses":True}),"unknown_or_selected_sampling"),
        ]
        for data, reason in cases:
            with self.subTest(reason=reason):
                out = run(data, [p])
                self.assertIsNone(by_rule(out,"D1"))
                self.assertIn(reason, ev(out,"D1")["excluded_by_reason"])
        data = samples([500]*8)
        data["attempts"][-1]["caller_version"]="different"
        out = run(data,[p])
        self.assertIsNone(by_rule(out, "D1"))
        self.assertIn("mixed_or_unidentified_timeout_segments", ev(out,"D1")["excluded_by_reason"])

    def test_attached_config_source_requires_matching_epoch_and_binding(self):
        data = model()
        runtime = policy(**SEGMENT)
        for change in [{"call_ref":"another"}, {"callee_version":"different"}, {"config_revision":"11"},
                       {"stale":True}, {"workload_id":None}]:
            declared = {**runtime, "source":"declared", "max_attempts":7, **change}
            self.assertIsNone(by_rule(run(data,[runtime,declared]), "D1"))
        declared = {**runtime, "source":"declared", "max_attempts":7}
        f = by_rule(run(data,[runtime,declared]),"D1")
        self.assertEqual(f["evidence"]["differences"],[
            {"field":"max_attempts","comparison":"runtime_vs_declared","sources":["runtime","declared"]}])
        self.assertEqual(f["denominator_count"], 0)
        self.assertIsNone(f["affected_ratio"])

    def test_declared_and_runtime_policies_require_the_same_service_key(self):
        data = model(**SEGMENT)
        def snapshot(namespace, source, attempts):
            owner = {"project_id": "demo", "environment": "lab", "namespace": namespace,
                     "service_name": "orders"}
            edge = policy(**SEGMENT, source=source, max_attempts=attempts)
            del edge["service_id"]
            return {"service": owner, "revision": "12", "source": source, "edges": [edge]}
        runtime = snapshot("payments", "runtime", 3)
        declared = snapshot("payments", "declared", 4)
        self.assertIsNotNone(by_rule(run(data, [runtime, declared]), "D1"))
        declared["service"]["namespace"] = "shipping"
        self.assertIsNone(by_rule(run(data, [runtime, declared]), "D1"))

    def test_exact_cluster_tolerance_is_not_a_mismatch(self):
        # Default relative allowance for 250ms is 25ms; inclusive threshold.
        data = samples([275]*5)
        out = run(data,[policy(**SEGMENT)])
        self.assertEqual(ev(out,"D1")["status"],"evaluated")
        self.assertIsNone(by_rule(out,"D1"))
        self.assertEqual(out["observations"][0]["policy_evidence"]["observed_timeout"]["value_ns"],str(275*MS))
        tight = run(data,[policy(**SEGMENT)],{"tolerance_ns":1,"timeout_drift_relative":.01})
        self.assertIsNotNone(by_rule(tight,"D1"))

    def test_verification_requires_the_original_drift_comparison(self):
        data = samples([500]*5)
        a, c = group("logical")
        c.update(SEGMENT)
        data["attempts"].extend(a)
        data["calls"] = [c]
        p = policy(**SEGMENT)
        before = run(data, [p])
        f = by_rule(before, "D1")
        data["sampling"] = {"mode":"tail"}
        after = run(data, [p])
        self.assertEqual(ev(after,"D1")["status"], "evaluated")
        self.assertEqual(verify_symptom(f, after, comparable=True, grace_elapsed=True, minimum_calls=1),
                         "insufficient_data")

    def test_multiple_timeouts_in_one_trace_do_not_meet_minimum(self):
        data = samples([500]*5)
        for i,a in enumerate(data["attempts"]):
            a["trace_ref"]=data["attempts"][0]["trace_ref"]
            a["attempt_id"]=format(i+1,"016x")
        out = run(data,[policy(**SEGMENT)])
        self.assertIsNone(by_rule(out,"D1"))
        est = out["observations"][0]["policy_evidence"]["observed_timeout"]
        self.assertEqual((est["sample_count"],est["trace_count"]),(5,1))


if __name__ == "__main__":
    unittest.main()
