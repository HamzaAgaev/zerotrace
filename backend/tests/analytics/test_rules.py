from copy import deepcopy
import unittest

from rules import build_reference, get_recommendation, load_thresholds, run, verify_symptom
from rules.budget import pauses_ns


REF = dict(environment_id="lab", trace_id="a" * 32, generation=1, revision=2)
EDGE = "e-payment"
NOW = "2026-11-01T00:00:00Z"
MS = 1_000_000


def model(attempts=(), calls=(), **kwargs):
    return {"environment_id": "lab", "environment_kind": "lab", "dataset_ref": "frozen-run",
            "generated_at": NOW, "attempts": list(attempts), "calls": list(calls), **kwargs}


def policy(**kwargs):
    return {"edge_id": EDGE, "edge": {"caller": "orders", "callee": "payment", "operation": "charge"},
            "service_id": "orders", "call_ref": "charge", "execution": "serial", "source": "runtime",
            "config_revision": "12", "budget_ns": str(700 * MS), "max_attempts": 3,
            "per_attempt_timeout_ns": str(250 * MS), "backoff": {"kind": "fixed", "jitter": "none", "base_ns": str(50 * MS)},
            "capabilities": {"apply_recommendation": True, "write_fields": ["max_attempts"]}, **kwargs}


def group(cid="charge", n=3, edge=EDGE, parent=None, **kwargs):
    attempts = [{"trace_ref": REF, "edge_id": edge, "attempt_id": f"{cid}-{i}",
                 "call_id": cid, "attempt_index": i, "method": "GET", "outcome": "http_error",
                 "client_http_status": 503, "duration_ns": 4 * MS, "server_span_id": f"server-{cid}-{i}",
                 "upstream_attempt_id": parent, "caller_server_span_id": "server-" + parent if parent else None,
                 "server_duration_ns": 6 * MS} for i in range(1, n + 1)]
    call = {"trace_ref": REF, "call_id": cid, "edge_id": edge, "attempt_ids": [a["attempt_id"] for a in attempts],
            "confidence": "high", "hypothesis": "retry" if n > 1 else "single", **kwargs}
    return attempts, call


def by_rule(output, key):
    return next((f for f in output["findings"] if f["rule_id"] == key), None)


def ev(output, key):
    return next(e for e in output["rule_evaluations"] if e["rule_id"] == key)


class BudgetTests(unittest.TestCase):
    def test_p11_arithmetic_intent_and_frozen_occurrence(self):
        p = policy()
        a, call = group()
        m = model(a, [call])
        before = deepcopy((p, m))
        out = run(m, [p])
        f = by_rule(out, "R4")
        rec = f["recommendations"][0]
        self.assertEqual(int(f["evidence"]["required_ns"]), 850 * MS)
        self.assertEqual(int(f["evidence"]["recommended_required_ns"]), 550 * MS)
        intent, = rec["intents"]
        self.assertEqual(intent["set"], {"max_attempts": 2})
        self.assertEqual(intent["target"], {"service_id": "orders", "call_ref": "charge"})
        self.assertEqual(intent["expected_config_revision"], "12")
        self.assertEqual(get_recommendation(out, f["occurrence_id"], rec["recommendation_id"]), rec)
        self.assertEqual((p, m), before)
        self.assertEqual(run(m, [p]), out)
        second = by_rule(run(m, [policy(config_revision="13")]), "R4")
        self.assertEqual(second["finding_id"], f["finding_id"])
        self.assertNotEqual(second["occurrence_id"], f["occurrence_id"])
        self.assertIsNone(get_recommendation(out, second["occurrence_id"], rec["recommendation_id"]))

    def test_config_without_traces_or_permission(self):
        res = by_rule(run(model(environment_kind="external"), [policy()]), "R4")
        self.assertIsNone(res["affected_ratio"])
        self.assertEqual(res["recommendations"][0]["intents"], [])
        self.assertEqual(res["recommendations"][0]["feasibility"], "feasible")

    def test_environment_or_historical_revision_never_expands_apply(self):
        p = policy(environment_kind="lab")
        f = by_rule(run(model(environment_kind="external"), [p]), "R4")
        self.assertEqual(f["recommendations"][0]["intents"], [])
        a, c = group()
        old = run(model(a, [c], config_revision="11"), [p])
        self.assertIsNone(by_rule(old, "R4"))
        self.assertIn("mismatched_config_revision", ev(old,"R4")["excluded_by_reason"])
        self.assertIsNotNone(by_rule(old, "OBS_RETRIES"))
        unknown = by_rule(run(model(), [policy(healthy_duration_ns=None)]), "R4")["recommendations"][0]
        self.assertEqual((unknown["feasibility"], unknown["intents"]), ("unknown", []))

    def test_budget_equality_unknown_and_infeasible(self):
        for p in (policy(max_attempts=2, budget_ns=550*MS), policy(max_attempts=1, budget_ns=250*MS, backoff=None)):
            out = run(model(), [p])
            self.assertIsNone(by_rule(out, "R4"))
            self.assertEqual(ev(out, "R4")["status"], "evaluated")
        for p in (policy(budget_ns=None), policy(freshness="stale"), policy(per_attempt_timeout_ns={"value": 1, "source":"observed"}),
                  policy(backoff={"kind":"fixed","base_ns":50*MS,"jitter":"unknown"})):
            self.assertEqual(ev(run(model(), [p]), "R4")["status"], "insufficient_data")
        out = run(model(), [policy(budget_ns=200*MS)])
        rec = by_rule(out, "R4")["recommendations"][0]
        self.assertEqual((rec["feasibility"], rec["intents"]), ("infeasible", []))
        self.assertEqual(ev(run(model(), [policy(execution="hedge")]), "R4")["status"], "not_applicable")

    def test_exponential_cap_jitter_and_actual_available_budget(self):
        backoff = {"kind": "exponential", "base_ns": 50*MS, "multiplier": 2, "jitter": "full"}
        self.assertEqual(pauses_ns(3, backoff), 150 * MS)
        self.assertEqual(pauses_ns(4, {**backoff, "cap_ns": 75*MS}), 200 * MS)
        f = by_rule(run(model(), [policy(backoff=backoff, reserve_ns=100*MS, elapsed_ns=100*MS)]), "R4")
        self.assertEqual(f["evidence"]["jitter_bound"], "upper")
        self.assertEqual(f["evidence"]["recommended_attempts"], 1)

    def test_p4_binding_and_conflicting_policies(self):
        edge = policy()
        for field in ("service_id", "source", "config_revision", "capabilities", "budget_ns"):
            edge.pop(field)
        snap = {"service":{"service_name":"orders","environment":"lab"}, "revision":"12", "source":"runtime",
                "capabilities":policy()["capabilities"],"edges":[edge],
                "external_timeouts":[{"call_ref":"foreign","timeout_ns":1},{"call_ref":"charge","timeout_ns":700*MS}]}
        self.assertEqual(by_rule(run(model(), [snap]), "R4")["evidence"]["budget_ns"], str(700*MS))
        self.assertIsNone(by_rule(run(model(), [policy(), policy(config_revision="13")]), "R4"))
        with self.assertRaises(ValueError):
            run(model(), [policy(environment_id="other")])

    def test_threshold_selectors(self):
        limits = load_thresholds({"version":"demo","defaults":{"min_cases":5},
                                  "overrides":[{"edge_id":"only","values":{"min_cases":1}}]})
        self.assertEqual(limits.for_edge("only").min_cases, 1)
        self.assertEqual(limits.for_edge("else").min_cases, 5)
        with self.assertRaises(ValueError):
            load_thresholds({"min_cases":0})


class DetectorTests(unittest.TestCase):
    def test_logical_call_denominator_differs_from_affected_traces(self):
        a, c = group()
        b, d = group("other", 1)
        out = run(model(a + b + a, [c,d,c]))
        obs, = out["observations"]
        self.assertEqual((obs["eligible_calls"],obs["retry_calls"],obs["observed_attempts"],obs["retry_ratio"]), (2,1,4,.5))
        f = by_rule(out, "OBS_RETRIES")
        self.assertEqual((f["affected_count"],f["denominator_count"],f["affected_ratio"]), (1,1,1))

    def test_trace_only_labels_from_list_or_attempt(self):
        a,c=group()
        edge=policy()["edge"]
        for rows in ([{"edge_id":EDGE,**edge}], [EDGE], [], {EDGE:edge}):
            a[0]["edge"]=edge
            m=model(a,[c],edges=rows)
            self.assertEqual(by_rule(run(m, []), "OBS_RETRIES")["edge"], edge)

    def test_ambiguous_missing_and_overlapping_group(self):
        for mode in ("ambiguous", "missing", "overlap"):
            a, c = group()
            groups = [c]
            if mode == "ambiguous":
                c["hypothesis"] = "ambiguous"
            if mode == "missing":
                a.pop()
            if mode == "overlap":
                groups.append({**c,"call_id":"other"})
            out = run(model(a,groups))
            self.assertIsNone(by_rule(out,"OBS_RETRIES"))
            self.assertIsNone(out["observations"][0]["retry_ratio"])

    def test_r1_paired_durations_and_r2_ancestry(self):
        a, first = group("outer", 1, "outer")
        a[0]["server_duration_ns"] = 100*MS
        b, second = group("inner", 1, parent=a[0]["attempt_id"])
        b[0]["offset_in_caller_ns"] = 60*MS
        m = model(a+b,[first,second])
        out = run(m, [], {"min_cases":1})
        self.assertEqual(by_rule(out,"R1")["evidence"]["samples"][0]["difference_ns"], str(96*MS))
        self.assertIsNotNone(by_rule(out,"R2"))
        b[0]["clock_compatible"] = False
        a[0]["server_span_id"] = None
        self.assertIsNone(by_rule(run(m, [], {"min_cases":1}),"R2"))
        self.assertIsNone(by_rule(run(m, [], {"min_cases":1}),"R1"))

    def test_nested_amplification_requires_real_groups_and_ancestry(self):
        a, root = group("root", 2, "outer")
        calls, attempts = [root], list(a)
        for i, upper in enumerate(a):
            b, child = group(f"nested-{i}",2, parent=upper["attempt_id"])
            for j, part in enumerate(b):
                part["first_path"] = i == 0 and j == 0
            calls.append(child)
            attempts.extend(b)
        f = by_rule(run(model(attempts,calls)), "R3")
        self.assertEqual(f["evidence"]["observed_attempts"], 4)
        self.assertEqual(f["evidence"]["path"], ["outer", EDGE])
        # Multiple unrelated invocations of the same operation are business fanout.
        extra, other = group("fanout",1,parent=a[0]["attempt_id"])
        self.assertIsNone(by_rule(run(model(attempts+extra,calls+[other])),"R3"))

    def test_r5_independent_by_index_and_enough_samples(self):
        a,c = group(n=2, gaps_ns=[1*MS])
        self.assertIsNone(by_rule(run(model(a,[c])),"R5"))
        f = by_rule(run(model(a,[c]), [], {"min_cases":1}), "R5")
        self.assertEqual(f["evidence"]["gaps"][0]["p95_ns"], str(MS))

    def test_r6_unknown_key_is_risk_and_r7_only_client_status(self):
        a,c=group(n=2, method="POST")
        a[0]["server_http_status"]=403
        r = run(model(a,[c]))
        self.assertIn("unknown", by_rule(r,"R6")["evidence"]["samples"][0]["idempotency"])
        self.assertIsNone(by_rule(r,"R7"))
        a[0]["client_http_status"]=403
        self.assertIsNotNone(by_rule(run(model(a,[c])),"R7"))
        a[0]["client_http_status"]=429
        self.assertIsNone(by_rule(run(model(a,[c])),"R7"))
        for p in a: p["idempotency"]="present"
        self.assertIsNone(by_rule(run(model(a,[c])),"R6"))

    def test_configured_r7_grpc_and_r8(self):
        a,c=group(n=2)
        a[0].update(protocol="grpc",client_grpc_status="INVALID_ARGUMENT")
        p = policy(retry_on={"grpc":["UNAVAILABLE"]}, upstream_per_attempt_timeout_ns=200*MS)
        out=run(model(a,[c]),[p])
        self.assertIsNotNone(by_rule(out,"R7"))
        self.assertIsNotNone(by_rule(out,"R8"))
        self.assertIsNone(by_rule(run(model(a,[c]),[]),"R7"))

    def test_d1_compares_only_compatible_revisions_and_versions(self):
        dims=dict(workload_id="work",caller_version="A",callee_version="B",config_revision="12")
        a,c=group(n=4,**dims)
        p=policy(**dims)
        self.assertIsNotNone(by_rule(run(model(a,[c]),[p]),"D1"))
        c["config_revision"]="13"
        self.assertIsNone(by_rule(run(model(a,[c]),[p]),"D1"))
        a,c=group(n=2,**dims)
        self.assertIsNone(by_rule(run(model(a,[c]),[p]),"D1"))

    def test_oracle_and_order_dont_change_inference(self):
        a,c=group()
        one=run(model(a,[c],hidden_scenario="healthy"))
        two=run(model(list(reversed(a)),[c],hidden_scenario="bad"))
        self.assertEqual(one,two)

    def test_baseline_requires_fixed_segment_age_and_unselected_data(self):
        a,c=group(n=1)
        dims=dict(environment_id="lab",edge_id=EDGE,caller_version="a",callee_version="b",workload_id="w")
        a[0].update({**dims, "outcome":"ok"})
        sampling={"mode":"all_received","known_losses":False}
        kw=dict(version="v1",created_at="2026-10-10T00:00:00Z",valid_until="2026-12-10T00:00:00Z")
        baseline=build_reference(dims,a,sampling,**kw)
        with self.assertRaises(ValueError): build_reference(dims,a,{"mode":"tail"},**kw)
        a[0].update(outcome="timeout",duration_ns=30*MS,server_duration_ns=100*MS)
        m=model(a,[c],**{k:v for k,v in dims.items() if k!="edge_id"})
        f=by_rule(run(m,[],{"min_cases":1},[baseline]),"R1")
        self.assertTrue(f["evidence"]["samples"][0]["degradation_hypothesis"])
        m["workload_id"]="x"
        f=by_rule(run(m,[],{"min_cases":1},[baseline]),"R1")
        self.assertNotIn("baseline_id",f["evidence"]["samples"][0])

    def test_verification_needs_eligible_edge_calls(self):
        a,c=group()
        before=run(model(a,[c]),[policy()])
        after=run(model(a,[c]),[policy(max_attempts=2)])
        f=by_rule(before,"R4")
        kw=dict(comparable=True,grace_elapsed=True,minimum_calls=1)
        self.assertEqual(verify_symptom(f,after,**kw),"symptom_absent")
        self.assertEqual(verify_symptom(f,before,**kw),"symptom_present")
        self.assertEqual(verify_symptom(f,run(model(),[policy()]),**kw),"insufficient_data")
        self.assertEqual(verify_symptom(f,after,**{**kw,"comparable":False}),"inconclusive")


if __name__ == "__main__":
    unittest.main()
