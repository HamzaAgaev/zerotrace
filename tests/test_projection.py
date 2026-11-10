from copy import deepcopy
import random
import unittest

from analytics_support import AnalyticsError
from projection import analyze

REF = {"environment_id": "lab", "trace_id": "2" * 32, "generation": 1, "revision": 1}


def span(node, parent, start, dur, **fields):
    return {"span_id": node, "parent_span_id": parent, "start_offset_ns": str(start),
            "duration_ns": str(dur), "kind": "internal", **fields}


def run(spans, changes=None, root=None, **fields):
    request = {"trace_ref": REF, "changes": [{"span_id": s, "speedup_percent": p}
                                              for s, p in (changes or {}).items()]}
    if root:
        request["root_span_id"] = root
    return analyze({"spans": spans, "trace_ref": REF, **fields}, request)


class IntervalTest(unittest.TestCase):
    def test_parallel_serial_shifted_both_and_stable_candidates(self):
        a = span("a", "r", 0, 1000)
        for start, total, after, both in [(0, 1000, 800, 500), (1000, 1800, 1300, 900), (900, 1700, 1700, 1300)]:
            with self.subTest(start=start):
                tree = [span("r", None, 0, total), a, span("b", "r", start, 800)]
                result = run(tree, {"a": 50})
                self.assertEqual(result["projected_ns"], str(after))
                row = next(c for c in result["candidates"] if c["span_id"] == "a")
                self.assertEqual(row["gain50_ns"], str(total-after))
                self.assertEqual(run(tree, {"a": 50, "b": 50})["projected_ns"], str(both))
        self.assertEqual(run(tree, {"a": 100})["projected_ns"], "1700")

    def test_ties_uncovered_nested_and_subtree(self):
        tree = [span("r", None, 0, 100), span("a", "r", 10, 60), span("b", "r", 10, 60),
                span("leaf", "a", 20, 20)]
        self.assertEqual(set(run(tree)["limiting_branches"]), {"a", "b", "leaf"})
        out = run(tree, {"leaf": 100, "a": 50})
        self.assertEqual(out["effective_selection"], [{"span_id":"a", "speedup_percent":50}])
        self.assertEqual(out["selection_notices"][0]["span_id"], "leaf")
        self.assertEqual(out["gain_ns"], "0")
        self.assertEqual(out["spans"][0]["uncovered_direct_children_ns"], "40")
        scope = run(tree, {"leaf":100}, root="a")
        self.assertEqual(scope["projected_ns"], "40")
        self.assertEqual(set(scope["applicability"]["excluded_span_ids"]), {"b", "r"})
        self.assertEqual([c["span_id"] for c in scope["candidates"]], ["leaf"])

    def test_graph_quality_and_scope(self):
        fixtures = {
            "multiple_roots": [span("a", None, 0, 1), span("b", None, 0, 1)],
            "cycle": [span("a", "b", 0, 1), span("b", "a", 0, 1)],
            "missing_parent": [span("r", None, 0, 1), span("b", "lost", 0, 1)],
            "temporal_bounds": [span("r", None, 0, 3), span("b", "r", 0, 500004)],
            "async_or_links": [span("r", None, 0, 4), span("b", "r", 0, 2, links_present=True)],
            "conflicting_span": [span("r", None, 0, 1), span("r", None, 0, 1)],
            "invalid_bounds": [span("r", None, 0, -1)],
        }
        for reason, spans in fixtures.items():
            with self.subTest(reason=reason):
                out = run(spans)
                self.assertIn(reason, out["applicability"]["reason_codes"])
                self.assertIsNone(out["gain_ns"])
        self.assertEqual(run(fixtures["missing_parent"], root="b")["status"], "available")
        issues = {"issues": [{"code": "cross_clock_uncertain", "span_ids": ["other"]}]}
        tree = [span("r", None, 0, 1), span("a", "r", 0, 1), span("other", "r", 0, 1)]
        self.assertEqual(run(tree, root="a", quality=issues)["status"], "available")
        self.assertEqual(run(tree, quality=issues)["status"], "insufficient_data")

    def test_clip_locally_and_prefer_missing_over_fabricated_latency(self):
        tree = [span("r", None, 0, 100), span("a", "r", -10, 90)]
        result = run(tree, {"a":50})
        self.assertIn("clipped_temporal_bounds", result["warnings"])
        self.assertEqual(result["projected_ns"], "60")
        self.assertEqual(result["spans"][1]["observed_duration_ns"], "90")
        self.assertEqual(result["spans"][1]["offset_ns"], "-10")
        self.assertEqual(tree[1]["start_offset_ns"], "-10")

    def test_absolute_clock_domain_and_zero(self):
        tree = [span("r", None, 0, 100), span("a", "r", 0, 100)]
        for s in tree:
            s["start_ns"] = s.pop("start_offset_ns")
        self.assertEqual(run(tree)["status"], "insufficient_data")
        for s in tree:
            s["clock_domain"] = "machine-1"
        self.assertEqual(run(tree)["status"], "available")
        self.assertIsNone(run([span("r", None, 0, 0)])["gain_ratio"])

    def test_observations_scope_and_evidence(self):
        tree = [span("r", None, 0, 100)]
        for i, outcome in enumerate(["error", "ok", "ok"]):
            tree.append(span(str(i), "r", i * 30, 20, name="GET /customers", outcome=outcome, kind="client"))
        results = run(tree)["observations"]
        self.assertEqual([o["kind"] for o in results], ["repeated_calls", "possible_retry"])
        self.assertEqual(results[1]["span_ids"], ["0", "1"])

    def test_ref_and_selection_validation(self):
        graph = {"spans": [span("a", None, 0, 5), span("b", "a", 0, 3)], "trace_ref": REF}
        for bad, code in [({"changes": [{"span_id":"b","speedup_percent":1}]*2}, "invalid_changes"),
                          ({"changes": [{"span_id":"b","speedup_percent":True}]}, "invalid_changes"),
                          ({"changes": [{"span_id":"a","speedup_percent":50}]}, "invalid_changes"),
                          ({"changes": [{"span_id":"z","speedup_percent":50}]}, "unknown_span"),
                          ({"algorithm_version":"v99"}, "unsupported_projection_version"),
                          ({"trace_ref":dict(REF, revision=3)}, "stale_trace_ref"),
                          ({"trace_ref":dict(REF, trace_id="4"*32)}, "trace_not_found")]:
            with self.subTest(code=code):
                with self.assertRaises(AnalyticsError) as e:
                    analyze(graph, {"trace_ref": REF, **bad})
                self.assertEqual(e.exception.code, code)

    def test_tree_depth_gate(self):
        tree = [span("0", None, 0, 1)] + [span(str(i), str(i-1), 0, 1) for i in range(1,260)]
        self.assertIn("depth_limit", run(tree)["applicability"]["reason_codes"])

    def test_invariance_and_ranking_single_matches_joint(self):
        rng = random.Random(31)
        for case in range(12):
            tree = [span("r", None, 0, 4000)]
            for i in range(10):
                p = rng.choice(tree)
                origin, width = int(p["start_offset_ns"]), int(p["duration_ns"])
                offset = rng.randrange(width+1)
                tree.append(span(str(i), p["span_id"], origin+offset, rng.randrange(width-offset+1)))
            original = deepcopy(tree)
            baseline = run(tree)
            self.assertEqual(baseline["gain_ns"], "0")
            for row in baseline["candidates"]:
                gains = [int(run(tree, {row["span_id"]: p})["gain_ns"]) for p in (25, 50, 100)]
                self.assertEqual(gains, sorted(gains))
                self.assertEqual(gains, [int(row[f"gain{p}_ns"]) for p in (25,50,100)])
            self.assertEqual(tree, original)
            rng.shuffle(tree)
            self.assertEqual(run(tree), baseline)
