from copy import deepcopy
import unittest

from analytics_support import AnalyticsError
from evalkit import score


def finding(rule, edge="x", amount=11):
    return {"rule_id": rule, "rule_version": "1", "edge_id": edge, "severity": "medium",
            "evidence": {"required_ns": str(amount)}}


def case(findings, **fields):
    return {"mode":"config_aware", "capabilities":["runtime_config"], "audit": {
        "findings": findings, "rule_evaluations": [
            {"rule_id":r, "status":"evaluated", "eligible_count":1} for r in ("R1","R4","R7")]}, **fields}


class EvalTest(unittest.TestCase):
    def test_labelled_only_statistics_and_evidence_tolerance(self):
        expected = {"mode":"config_aware", "required_capabilities":["runtime_config"], "must_find":[
            {"rule_id":"R4","rule_version":"1", "selector":{"edge_id":"x"}, "severity_min":"medium",
             "operands":[{"path":"evidence.required_ns","equals":"10","tolerance":1}]},
            {"rule_id":"R1"}], "must_not_find":[{"rule_id":"R7"}]}
        actual = case([finding("R4"), finding("R7"), finding("D1")])
        out = score(actual, expected)
        self.assertEqual(out["status"], "failed")
        self.assertEqual(out["metrics"]["precision"], .5)
        self.assertEqual(out["metrics"]["recall"], .5)
        self.assertEqual(out["metrics"]["unlabelled_count"], 1)
        self.assertEqual(out["unlabelled_findings"][0]["rule_id"], "D1")
        self.assertEqual(out["counts"], {"passed":1, "failed":2, "blocked":0})

    def test_failed_arithmetic_reports_operands_independently_of_detection(self):
        out = score(case([finding("R4")]), {"must_find":[{"rule_id":"R4", "severity_max":"low",
            "operands":[{"path":"evidence.required_ns","equals":9,"tolerance":1}]}]})
        self.assertEqual(out["status"], "failed")
        evidence = out["assertions"][0]["actual_evidence"][0]
        self.assertEqual(evidence["failed_fields"], ["severity", "evidence.required_ns"])
        self.assertEqual(out["metrics"]["recall"], 1)

    def test_rule_abstention_or_missing_capabilities_is_blocked(self):
        unavailable = case([])
        unavailable["audit"]["rule_evaluations"] = [{"rule_id":"R4", "status":"insufficient_data"}]
        for expected in ({"must_not_find":[{"rule_id":"R4"}]}, {"must_find":[{"rule_id":"R4"}]},
                         {"mode":"trace_only", "must_find":[{"rule_id":"R4"}]},
                         {"required_capabilities":["missing"], "must_not_find":[{"rule_id":"R7"}]}):
            out = score(unavailable, expected)
            self.assertEqual(out["status"], "blocked")
            self.assertIsNone(out["metrics"]["precision"])
            self.assertIsNone(out["metrics"]["recall"])

    def test_negative_on_ineligible_edge_does_not_borrow_other_edges_coverage(self):
        actual = case([])
        actual["audit"]["rule_evaluations"] = [{"rule_id": "R4", "rule_version": "1",
            "status": "evaluated", "eligible_count": 20, "by_edge": [
                {"edge_id": "ready", "eligible_count": 20, "status": "evaluated"},
                {"edge_id": "lost", "eligible_count": 0, "status": "insufficient_data"}]}]
        def negative(edge, version):
            return score(actual, {"must_not_find": [{"rule_id": "R4", "rule_version": version,
                                                     "selector": {"edge_id": edge}}]})["status"]
        self.assertEqual(negative("ready", "1"), "passed")
        self.assertEqual(negative("lost", "1"), "blocked")
        self.assertEqual(negative("ready", "2"), "blocked")

    def test_negative_healthy_checked_and_unexpected_policy(self):
        expected = {"must_not_find":[{"rule_id":"R4"}]}
        self.assertEqual(score(case([]), expected)["status"], "passed")
        self.assertEqual(score(case([finding("R8")]), expected)["status"], "passed")
        fail = score(case([finding("R8")]), {**expected, "unexpected_policy":"fail"})
        self.assertEqual(fail["status"], "failed")
        self.assertEqual(fail["metrics"]["false_positives"], 0)

    def test_scenario_identity_and_evidence_of_eligibility(self):
        actual = case([], scenario_id="different", scenario_version="v2")
        result = score(actual, {"scenario_id": "s00", "scenario_version": "v1",
                                "must_not_find": [{"rule_id": "R4"}]})
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["scenario_id"], "different")
        self.assertEqual(result["assertions"][0]["mismatched_inputs"]["scenario_version"],
                         {"expected": "v1", "actual": "v2"})
        for count in (None, False, -1, "1"):
            actual["audit"]["rule_evaluations"] = [{"rule_id":"R4", "status":"evaluated",
                                                    "eligible_count": count}]
            self.assertEqual(score(actual, {"must_not_find":[{"rule_id":"R4"}]})["status"], "blocked")
        actual["audit"]["rule_evaluations"][0]["eligible_count"] = 3
        self.assertEqual(score(actual, {"must_not_find":[{"rule_id":"R4",
                         "selector":{"edge_id":"missing"}}]})["status"], "blocked")

    def test_expected_run_identity_must_be_present_in_actual_results(self):
        identity = dict(scenario_id="s01", scenario_version="v1", build_ref="build-a", seed=0)
        expected = {**identity, "must_find": [{"rule_id": "R4"}]}
        actual = case([finding("R4")], **identity)
        self.assertEqual(score(actual, expected)["status"], "passed")
        self.assertEqual(score({**actual, "seed": False}, expected)["status"], "blocked")
        for field, value in identity.items():
            for missing in ({}, {field: None}):
                with self.subTest(field=field, missing=missing):
                    result = score({**{k: v for k, v in actual.items() if k != field}, **missing}, expected)
                    self.assertEqual(result["status"], "blocked")
                    self.assertIsNone(result[field])
                    self.assertEqual(result["assertions"][0]["mismatched_inputs"],
                                     {field: {"expected": value, "actual": None}})
        result = score(case([finding("R4")]), expected)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(set(result["assertions"][0]["mismatched_inputs"]), set(identity))

    def test_unqualified_finding_cannot_clear_an_ineligible_negative_assertion(self):
        actual = case([finding("R4")])
        actual["audit"]["rule_evaluations"] = []
        spec = {"rule_id": "R4", "severity_min": "high"}
        self.assertEqual(score(actual, {"must_not_find": [spec]})["status"], "blocked")
        actual["audit"]["findings"][0]["severity"] = "high"
        self.assertEqual(score(actual, {"must_not_find": [spec]})["status"], "failed")
        mismatch = score(actual, {"mode": "trace_only", "must_not_find": [spec]})
        self.assertEqual((mismatch["status"], mismatch["mode"]), ("blocked", "config_aware"))
        with self.assertRaises(AnalyticsError):
            score(case([]), {"must_find": [{"rule_id": "R7", "selector": ["wrong"]}]})

    def test_quality_policy_and_apply_evidence(self):
        actual = case([], quality={"missing":0}, apply={"outcome":"conflict"}, policy={"effective":2})
        expected = {"apply_assertions":[{"path":"apply.outcome", "equals":"conflict"}],
                    "policy_assertions":[{"path":"policy.effective", "min":1, "max":2}],
                    "quality_assertions":[{"path":"quality.missing", "equals":0},
                                          {"path":"quality.unknown", "equals":None}]}
        out = score(actual, expected)
        self.assertEqual(out["counts"], {"passed":3,"failed":0,"blocked":1})
        self.assertFalse(out["assertions"][-1]["present"])

    def test_independent_projection_measurements_separate_accuracy_and_availability(self):
        actual = case([], projections=[{"case_id":"a","status":"available","projected_ns":"85"},
                        {"case_id":"b","status":"insufficient_data","projected_ns":None},
                        {"case_id":"c","status":"available","projected_ns":"5"}])
        expected = {"projection_measurements":[
            {"case_id":"a","measured_ns":"100","source":"lab/run/3","tolerance_ns":"10"},
            {"case_id":"b","measured_ns":"80"}, {"case_id":"c","measured_ns":"0"}]}
        out = score(actual, expected)
        p = out["metrics"]["projection"]
        self.assertEqual(out["counts"], {"passed":1,"failed":1,"blocked":1})
        self.assertEqual(p["mae_ns"], "10")
        self.assertAlmostEqual(p["mean_relative_error"], .15)
        self.assertEqual(p["relative_error_count"], 1)
        self.assertAlmostEqual(p["availability"], 2/3)
        self.assertEqual(p["abstained"], 1)

    def test_oracle_separate_from_actual_and_invalid_labels(self):
        actual = case([finding("R4")])
        original = deepcopy(actual)
        self.assertEqual(score(actual, {"must_find":[{"rule_id":"R4"}]})["status"], "passed")
        self.assertEqual(score(actual, {"must_not_find":[{"rule_id":"R4"}]})["status"], "failed")
        self.assertEqual(actual, original)
        with self.assertRaises(AnalyticsError):
            score(actual, {"must_find":[{"rule_id":"R4"}], "must_not_find":[{"rule_id":"R4"}]})
        with self.assertRaises(AnalyticsError):
            score(actual, {"projection_measurements":[{"case_id":"x","measured_ns":-1}]})
