import copy
import unittest

from analytics_support import AnalyticsError, wire
from comparison import compare, compare_runs
from tests.span_ids import sid


def span(span_id, parent, kind, start, length, service="orders", **kwargs):
    return {"span_id": sid(span_id), "parent_span_id": sid(parent), "kind": kind, "name": "pay",
            "service": service, "outcome": "ok", "start_offset_ns": str(start),
            "duration_ns": str(length), **kwargs}


def trace(number, n=1, run_id="run-a", root_status=503, length=100):
    ref = {"environment_id": "lab", "trace_id": format(number, "032x"), "generation": 1, "revision": 2}
    root = span("root", None, "server", 0, length, run_id=run_id, http_status=root_status)
    clients = [span(f"c{i}", "root", "client", i * 10, 10, edge_id="edge",
                    logical_call_id="same-call", attempt_index=i + 1, http_status=503)
               for i in range(n)]
    return {"trace_ref": ref, "project_id": "shop", "workload_id": "checkout", "sampling": "all_received",
            "summary": {"operation": "POST /order"}, "spans": [root, *clients]}


def bundle(name, n, start, received=2, expected=2):
    graphs = [trace(start + i, n, name) for i in range(received)]
    return {"run_id": name, "stand_id": "stand", "status": "completed", "scenario_revision": 4,
            "fault_revision": 1, "load": {"requests": expected, "concurrency": 1},
            "config_snapshot": {"service_id": "orders", "revision": name, "fetched_at_ns": start,
                                "edges": [{"call_ref": "pay", "edge_id": "edge", "max_attempts": n, "timeout": 250}]},
            "trace_collection": {"run_id": name, "status": "ready" if received == expected else "partial",
                                 "expected_count": expected, "received_count": received,
                                 "trace_refs": [g["trace_ref"] for g in graphs]}, "traces": graphs}


def cohort(a, b, **kwargs):
    request = {"baseline_trace_refs": [g["trace_ref"] for g in a], "candidate_trace_refs": [g["trace_ref"] for g in b],
               "entry_selector": {"service": "orders", "operation": "POST /order"}, **kwargs}
    return compare([*a, *b], request)


class CohortTests(unittest.TestCase):
    def test_direct_boundary_and_root_internal_outcomes(self):
        a = trace(1, 1, root_status=200)
        a["spans"].extend([span("payment", "c0", "server", 1, 5, "payment", http_status=200),
                           span("bank", "payment", "client", 2, 3, "payment")])
        b = trace(2, 0, root_status=200)
        result = cohort([a], [b])
        left, right = result["baseline"], result["candidate"]
        self.assertEqual((left["root_error_ratio"], left["internal_error_trace_ratio"]), (0, 1))
        self.assertEqual(len(left["direct_calls"]), 1)
        self.assertEqual((left["direct_calls"][0]["sample_count"], right["direct_calls"][0]["sample_count"]), (1, 0))
        self.assertIsNone(right["direct_calls"][0]["pooled_median_duration_ns"])
        self.assertEqual(left["direct_calls"][0]["target"], "payment")

    def test_internal_wrapper_and_overlap(self):
        a, b = trace(1, 2), trace(2)
        a["spans"].append(span("wrap", "root", "internal", 0, 100))
        a["spans"][1].update(parent_span_id=sid("wrap"), start_offset_ns="0", duration_ns="20")
        a["spans"][2].update(start_offset_ns="10", duration_ns="20")
        self.assertAlmostEqual(cohort([a], [b])["baseline"]["overlap_ratio"], 40 / 30)
        for s in a["spans"][1:3]:
            s["start_ns"] = s.pop("start_offset_ns")
        self.assertAlmostEqual(cohort([a], [b])["baseline"]["overlap_ratio"], 40 / 30)

    def test_machine_edge_stays_one_group_when_target_is_only_known_on_one_side(self):
        a, b = trace(1), trace(2)
        b["spans"][1].update(target="payment", duration_ns="12")
        # In a P2 TraceGraph the authoritative edge is on the normalized Attempt.
        client = a["spans"][1]
        a["attempts"] = [{"attempt_id": client["span_id"], "edge_id": client.pop("edge_id")}]
        z = cohort([a], [b])
        for side in ("baseline", "candidate"):
            self.assertEqual(len(z[side]["direct_calls"]), 1)
            self.assertEqual(z[side]["direct_calls"][0]["median_calls_per_trace"], 1)
        metrics = {o["metric"]: o for o in z["observations"]}
        self.assertNotIn("median_calls_per_trace", metrics)
        self.assertEqual(metrics["pooled_median_duration_ns"]["candidate"], "12")
        a["quality"] = {"source_loss": True}
        self.assertFalse(any(o["metric"] == "median_calls_per_trace" for o in cohort([a], [b])["observations"]))

    def test_clock_metadata_is_advisory_for_supplied_intervals(self):
        a, b = trace(1, 2), trace(2)
        a["clock_compatible"] = False
        z = cohort([a], [b])["baseline"]
        self.assertEqual(z["overlap_ratio"], 1)
        self.assertEqual(z["direct_calls"][0]["pooled_median_duration_ns"], 10)
        a.pop("clock_compatible")
        a["spans"][1]["clock_compatible"] = False
        self.assertEqual(cohort([a], [b])["baseline"]["overlap_ratio"], 1)

    def test_percentiles_interpolation_denominators_and_warnings(self):
        a = [trace(i, root_status=200, length=10 * i) for i in (1, 2, 3)]
        b = trace(4, 0, root_status=None, length=15)
        b["spans"][0]["outcome"] = "unknown"
        b.pop("workload_id")
        b["service_version"] = "v2"
        z = cohort(a, [b])
        self.assertEqual((z["baseline"]["root_latency_p50_ns"], z["baseline"]["root_latency_p95_ns"]), (20, 29))
        self.assertEqual(z["delta_ratio"], -.25)
        self.assertIsNone(z["candidate"]["root_error_ratio"])
        self.assertEqual(z["candidate"]["service_versions"], ["v2"])
        self.assertEqual(z["comparability"], "exploratory")
        self.assertIn("small_sample_p95", z["warnings"])
        self.assertIn("unknown_workload_id", z["warnings"])

    def test_reject_invalid_or_stale_refs(self):
        a, b = trace(1), trace(2)
        for left, right in (([], [b]), ([a], [a]), ([a, a], [b]), ([a] * 101, [b])):
            with self.subTest(), self.assertRaises(AnalyticsError):
                cohort(left, right)
        for key, value, expected in (("revision", 3, 409), ("trace_id", format(200, "032x"), 404)):
            changed = dict(a["trace_ref"], **{key: value})
            with self.subTest(key=key), self.assertRaises(AnalyticsError) as exc:
                compare([a, b], {"baseline_trace_refs": [changed], "candidate_trace_refs": [b["trace_ref"]]})
            self.assertEqual(exc.exception.status, expected)

    def test_entry_and_workload_mismatch(self):
        a, b = trace(1), trace(2)
        for key, value in (("workload_id", "other"), ("project_id", "other"),
                           ("summary", {"operation": "other"})):
            with self.subTest(key=key), self.assertRaises(AnalyticsError):
                cohort([a], [{**b, key: value}])
        b["spans"].append(span("orphan", "missing", "client", 0, 2))
        with self.assertRaises(AnalyticsError):
            cohort([a], [b])

    def test_lost_children_excluded_from_direct_call_denominator(self):
        incomplete = trace(1, 0, root_status=200)
        incomplete["quality"] = {"issues": [{"code": "source_loss"}]}
        healthy = trace(2, 2, root_status=200)
        z = cohort([incomplete, healthy], [trace(3)])
        a = z["baseline"]
        self.assertIn("trace_quality_issues", z["warnings"])
        self.assertEqual(a["coverage"]["root_latency_count"], 2)
        self.assertEqual(a["coverage"]["direct_call_trace_count"], 1)
        self.assertEqual(a["coverage"]["known_internal_outcomes"], 1)
        self.assertEqual(a["direct_calls"][0]["median_calls_per_trace"], 2)
        all_lost = cohort([incomplete], [healthy])["baseline"]
        self.assertIsNone(all_lost["direct_calls"][0]["median_calls_per_trace"])

    def test_bad_ids_do_not_enter_cohort_counts(self):
        a, b = trace(1), trace(2)
        for field, value in (("parent_span_id", []), ("span_id", "0" * 16), ("span_id", "A" * 16)):
            graph = {**a, "spans": [a["spans"][0], {**a["spans"][1], field: value}]}
            with self.subTest(field=field, value=value), self.assertRaises(AnalyticsError) as error:
                cohort([graph], [b])
            self.assertEqual(error.exception.code, "invalid_trace")

    def test_identity_independent_of_permutation(self):
        a = [trace(1, 2), trace(2, 0)]
        b = [trace(3, 1)]
        expected = cohort(a, b)
        a[0]["spans"].reverse()
        self.assertEqual(cohort(list(reversed(a)), b), expected)


class RunTests(unittest.TestCase):
    def setUp(self):
        self.a = bundle("run-a", 3, 10)
        self.b = bundle("run-b", 2, 20)

    def test_full_demo_and_one_row_per_input(self):
        a, b = bundle("a", 3, 1, 30, 30), bundle("b", 2, 31, 30, 30)
        z = wire(compare_runs(a, b))
        self.assertEqual(z["baseline"]["attempts"], 90)
        self.assertEqual(z["candidate"]["attempts"], 60)
        self.assertEqual(z["baseline"]["retries"], 60)
        self.assertEqual(z["candidate"]["retries"], 30)
        self.assertEqual(z["baseline"]["attempt_count_histogram"], {"3": 30})
        self.assertEqual(z["candidate"]["http_status_counts"], {"503": 30})
        self.assertEqual(z["candidate"]["root_latency_p50_ns"], "100")

    def test_target_operator_can_be_downstream_from_entry(self):
        for run in (self.a, self.b):
            for trace in run["traces"]:
                root = trace["spans"][0]
                root["parent_span_id"] = sid("forward")
                trace["spans"].extend([
                    span("front", None, "server", 0, 120, "gateway", run_id=root.pop("run_id"), http_status=503),
                    span("forward", "front", "client", 0, 110, "gateway"),
                ])
        result = compare_runs(self.a, self.b)
        self.assertEqual(result["baseline"]["attempt_count_histogram"], {"3": 2})
        self.assertEqual(result["candidate"]["attempt_count_histogram"], {"2": 2})

    def test_opaque_registry_owner_and_namespace_local_call_refs(self):
        key = {"project_id": "shop", "environment": "lab", "namespace": "owner", "service_name": "orders"}
        for run in (self.a, self.b):
            run["config_snapshot"].update(service_id="opaque-registry-id", service=key)
            for graph in run["traces"]:
                for item in graph["spans"]:
                    item["service"] = key
                graph["spans"].append(span("foreign", "root", "client", 10, 10,
                                           {**key, "namespace": "another"}, call_ref="pay"))
        z = compare_runs(self.a, self.b)
        self.assertEqual((z["baseline"]["attempts"], z["candidate"]["attempts"]), (6, 4))

    def test_exact_edge_matches_opaque_id_without_name_mapping(self):
        for run in (self.a, self.b):
            run["config_snapshot"]["service_id"] = "opaque-registry-id"
        self.assertEqual(compare_runs(self.a, self.b)["candidate"]["attempts"], 4)
        graph = self.b["traces"][0]
        graph["spans"][1].pop("edge_id")
        graph["spans"][1]["call_ref"] = "pay"
        self.assert_code("invalid_run_sample")

    def test_contradictory_normalized_attempt_or_non_server_entry_rejected(self):
        graph = self.b["traces"][0]
        for fields in ({"edge_id": "other-edge"}, {"attempt_index": 3}):
            with self.subTest(fields=fields):
                graph["attempts"] = [{"attempt_id": sid("c0"), **fields}]
                self.assert_code("invalid_run_sample")
        del graph["attempts"]
        graph["spans"][0]["kind"] = "client"
        self.assert_code("invalid_run_sample")

    def test_measurements_independent_of_new_limit(self):
        self.b["traces"] = [trace(i, 3, "run-b") for i in (20, 21)]
        z = compare_runs(self.a, self.b)
        self.assertEqual(z["attempts_per_input_delta"], 0)
        self.assertEqual(z["candidate"]["attempt_count_histogram"], {"3": 2})

    def test_partial_different_denominators(self):
        a, b = bundle("a", 3, 10, 1, 3), bundle("b", 2, 20, 2, 3)
        z = compare_runs(a, b)
        self.assertEqual(z["warnings"], ["partial_sample"])
        self.assertEqual((z["baseline"]["received_count"], z["candidate"]["received_count"]), (1, 2))
        self.assertEqual(z["attempts_per_input_delta"], -1)
        # More calls were observed in B in total, despite fewer calls per input.
        self.assertEqual((z["baseline"]["attempts"], z["candidate"]["attempts"]), (3, 4))

    def assert_code(self, code, status=422, **options):
        with self.assertRaises(AnalyticsError) as exc:
            compare_runs(self.a, self.b, **options)
        self.assertEqual((exc.exception.code, exc.exception.status), (code, status))

    def test_failed_pending_empty_distinct(self):
        self.b["status"] = "failed"
        self.assert_code("run_failed")
        self.b["status"] = "completed"
        self.b["trace_collection"]["status"] = "waiting"
        self.assert_code("traces_pending", 409)
        self.b = bundle("b", 2, 21, 0)
        self.assert_code("insufficient_traces")

    def test_compare_all_fields_and_exact_expected_target(self):
        self.assert_code("runs_not_comparable", expected_change={"service_id": "orders", "call_ref": "another",
                                                                "field": "max_attempts"})
        self.b["config_snapshot"]["edges"][0]["timeout"] = 300
        self.assert_code("runs_not_comparable")
        self.b["config_snapshot"]["edges"][0]["timeout"] = 250
        self.b["load"]["concurrency"] = 2
        self.assert_code("runs_not_comparable")

    def test_ref_retention_sample_ownership_and_attempt_labels(self):
        graph = self.b["traces"][0]
        self.b["traces"][0] = {**graph, "trace_ref": {**graph["trace_ref"], "revision": 9}}
        self.assert_code("stale_revision", 409)
        self.b["traces"][0] = graph
        for tag, value in (("probe", True), ("run_id", "not-mine")):
            with self.subTest(tag=tag):
                root = graph["spans"][0].copy()
                graph["spans"][0][tag] = value
                self.assert_code("invalid_run_sample")
                graph["spans"][0] = root
        graph["spans"][2]["attempt_index"] = 3
        self.assert_code("invalid_run_sample")

    def test_real_zero_versus_lost_target_observations(self):
        zero = trace(20, 0, "run-b")
        self.b["traces"][0] = zero
        self.assertEqual(compare_runs(self.a, self.b)["candidate"]["attempt_count_histogram"], {"0": 1, "2": 1})
        for lost in [
            {"quality": {"source_loss": True}},
            {"quality": {"issues": [{"code": "missing_parent"}]}},
            {"sampling": {"known_losses": True}},
            {"attempts": [{"attempt_id": sid("omitted-span")}]},
            {"summary": {"span_count": 2}},
            {"assembly_state": "forced"},
        ]:
            with self.subTest(lost=lost):
                self.b["traces"][0] = {**zero, **lost}
                self.assert_code("invalid_run_sample")

    def test_orphan_or_disconnected_client_cycle_rejected_in_frozen_sample(self):
        graph = self.b["traces"][0]
        graph["spans"][1]["parent_span_id"] = sid("lost-parent")
        self.assert_code("incomplete_trace")
        graph["spans"][1]["parent_span_id"] = graph["spans"][2]["span_id"]
        graph["spans"][2]["parent_span_id"] = graph["spans"][1]["span_id"]
        self.assert_code("incomplete_trace")

    def test_extra_trace_not_in_frozen_sample_ignored(self):
        initial = copy.deepcopy(self.b)
        result = compare_runs(self.a, self.b)
        self.assertEqual(self.b, initial)
        self.b["traces"].append(trace(99, 7, "run-b"))
        self.assertEqual(result, compare_runs(self.a, self.b))

    def test_duplicate_export_location_and_normalized_attempts(self):
        graph = self.b["traces"][0]
        graph["spans"].append(span("processor", "root", "internal", 0, 100))
        graph["spans"][1]["parent_span_id"] = sid("processor")
        graph["attempts"] = []
        for s in graph["spans"][1:3]:
            graph["attempts"].append({"attempt_id": s["span_id"], "edge_id": s.pop("edge_id"),
                                     "attempt_index": s.pop("attempt_index"), "call_id": s.pop("logical_call_id")})
        graph["spans"].append(copy.deepcopy(graph["spans"][1]))
        self.assertEqual(compare_runs(self.a, self.b)["candidate"]["attempts"], 4)
