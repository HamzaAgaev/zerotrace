"""Exercise the complete router against deterministic, independent A/B inputs."""
import io
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from wsgiref.util import setup_testing_defaults

from analytics_support import AnalyticsError
from app.analytics_api.provider import FixtureSource
from app.analytics_api.routes import AnalyticsApp
from app.analytics_api.service import AnalyticsService
from app.analytics_api.storage import LocalResultStore
from fixtures.analytics.p11 import audit_examples, dataset


def http(app, path, method="GET", body=None):
    env = {}
    setup_testing_defaults(env)
    endpoint, _, query = path.partition("?")
    raw = json.dumps(body).encode() if body is not None else b""
    env.update(PATH_INFO="/api/v1/" + endpoint, QUERY_STRING=query, REQUEST_METHOD=method,
               CONTENT_TYPE="application/json", CONTENT_LENGTH=str(len(raw)))
    env["wsgi.input"] = io.BytesIO(raw)
    status = []
    data = b"".join(app(env, lambda s, h: status.append(int(s[:3]))))
    return status[0], data.decode() if data.startswith(b"<!doctype") else json.loads(data)


class AnalyticsApiTest(unittest.TestCase):
    def setUp(self):
        self.data = dataset()
        self.source = FixtureSource(self.data)
        self.store = LocalResultStore()
        self.addCleanup(self.store.close)
        self.service = AnalyticsService(self.source, self.store)
        self.app = AnalyticsApp(self.service)

    def test_audit_contract_and_recommendation_history(self):
        status, envelope = http(self.app, "audit?environment_id=lab")
        self.assertEqual(status, 200)
        self.assertEqual(envelope["schema_version"], "1.0.0")
        finding = next(f for f in envelope["payload"]["findings"] if f["rule_id"] == "R4")
        self.assertEqual(finding["evidence"]["required_ns"], "850000000")
        ids = finding["occurrence_id"], finding["recommendation_id"]
        stored = self.service.recommendation("lab", *ids)
        self.assertEqual(stored["recommendation"]["intents"][0]["set"]["max_attempts"], 2)
        with self.assertRaises(AnalyticsError):
            self.service.recommendation("healthy", *ids)
        self.data["environments"]["lab"]["policies"][0].update(max_attempts=2, config_revision="13")
        self.service.audit("lab")
        self.assertEqual(self.service.recommendation("lab", *ids), stored)

    def test_policy_observation_is_visible_and_external_plan_is_read_only(self):
        env = self.data["environments"]["healthy"]
        env["policies"][0]["upstream_per_attempt_timeout_ns"] = "100000000"
        status, res = http(self.app, "audit?environment_id=healthy&view=full")
        self.assertEqual(status, 200)
        self.assertEqual([f["rule_id"] for f in res["payload"]["findings"]], ["R8"])
        finding = res["payload"]["findings"][0]
        self.assertEqual(finding["evidence"]["kind"], "policy_observation")
        self.assertEqual(finding["evidence"]["inner_timeout_ns"], "250000000")
        self.assertFalse(finding["recommendation"]["can_prepare_plan"])
        legacy = http(self.app, "audit?environment_id=healthy")[1]["payload"]
        self.assertEqual(legacy["omitted_findings"][0]["finding_id"], finding["finding_id"])

        env = self.data["environments"]["lab"]
        env["model"]["environment_kind"] = "external"
        env["policies"][0].update(budget_ns="1000000000", reserve_ns="300000000")
        result = http(self.app, "audit?environment_id=lab&view=full")[1]["payload"]
        r4 = next(f for f in result["findings"] if f["rule_id"] == "R4")
        self.assertEqual(r4["evidence"]["available_ns"], "700000000")
        self.assertFalse(r4["recommendation"]["can_prepare_plan"])

    def test_cohort_identity_ignores_order_and_report_lists_historical_traces(self):
        refs = self.data["runs"]["base"]["trace_collection"]["trace_refs"]
        historical = self.data["runs"]["candidate"]["trace_collection"]["trace_refs"]
        a, b = refs[:2], historical[:2]
        def cohort(first, second):
            return self.service.compare({"baseline_trace_refs": first, "candidate_trace_refs": second})
        result = cohort(a, b)
        self.assertEqual(result["analysis_id"], cohort(a[::-1], b[::-1])["analysis_id"])
        self.assertNotEqual(result["analysis_id"], cohort(b, a)["analysis_id"])
        p11 = self.service.compare({"baseline_run_id": "base", "candidate_run_id": "candidate"})
        manifest = self.service.report("lab", comparison_id=p11["analysis_id"])["input_refs"]
        self.assertEqual(manifest["by_analysis"]["snapshot"], refs)
        self.assertEqual(manifest["traces"], refs + historical)
        self.assertEqual(manifest["by_analysis"]["comparison"], refs + historical)

    def test_trace_only_mode_keeps_config_and_oracle_out_of_r4(self):
        audit = self.service.audit("lab", mode="trace_only")
        self.assertEqual(audit["mode"], "trace_only")
        self.assertNotIn("runtime_config", audit["capabilities"])
        self.assertFalse(audit["config_refs"])
        self.assertEqual([f["rule_id"] for f in audit["findings"]], ["OBS_RETRIES"])
        self.assertEqual(http(self.app, "audit?environment_id=lab&mode=typo")[0], 400)

    def test_trace_only_without_config(self):
        self.data["environments"]["lab"]["policies"] = []
        status, data = http(self.app, "audit?environment_id=lab")
        self.assertEqual(status, 200)
        item = next(f for f in data["payload"]["findings"] if f["rule_id"] == "OBS_RETRIES")
        self.assertEqual(item["evidence"]["kind"], "trace_observation")
        self.assertEqual(item["affected_count"], 30)
        self.assertEqual(item["edge"]["caller"], "orders")
        self.assertNotIn("budget_ns", item["evidence"])

    def test_healthy_and_unknown_fixtures(self):
        healthy = self.service.audit("healthy")
        self.assertFalse(any(f["rule_id"] == "R4" for f in healthy["findings"]))
        unknown = http(self.app, "audit?environment_id=unknown")[1]["payload"]
        self.assertEqual(unknown["findings"], [])
        self.assertIn("insufficient_data", [r["status"] for r in unknown["rule_evaluations"]])

    def test_p11_does_not_derive_observations_from_config(self):
        request = {"baseline_run_id":"base", "candidate_run_id":"candidate"}
        for actual in (2, 3):
            service = AnalyticsService(FixtureSource(dataset(after_attempts=actual)), self.store)
            result = service.compare(request)
            self.assertEqual(result["baseline"]["attempts"], 90)
            self.assertEqual(result["candidate"]["attempts"], 30 * actual)
            self.assertEqual(result["candidate"]["root_errors"], 30)
            self.assertEqual(result["candidate"]["attempt_count_histogram"], {str(actual): 30})

    def test_frozen_refs_and_http_errors(self):
        req = {"baseline_run_id":"base", "candidate_run_id":"candidate"}
        run = self.data["runs"]["candidate"]
        run["trace_collection"]["status"] = "waiting"
        self.assertEqual(http(self.app, "comparisons", "POST", req)[0], 409)
        run["trace_collection"]["status"] = "ready"
        run["trace_collection"]["trace_refs"][0] = {**run["trace_collection"]["trace_refs"][0], "revision": 999}
        status, error = http(self.app, "comparisons", "POST", req)
        self.assertEqual((status, error["error"]["code"]), (409, "stale_revision"))
        self.assertEqual(http(self.app, "audit")[0], 400)
        self.assertEqual(http(self.app, "audit?environment_id=no-such-env")[0], 404)
        self.assertEqual(http(self.app, "comparisons", "POST", ["x"])[0], 400)

    def test_report_persists_and_escapes_recorded_strings(self):
        model = self.data["environments"]["lab"]["model"]
        model["quality"]["example"] = "</pre><script>bad()</script>"
        projection = json.loads(Path("fixtures/analytics/project.json").read_text())
        request = {"baseline_run_id":"base", "candidate_run_id":"candidate"}
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "results.sqlite3"
            store = LocalResultStore(db)
            try:
                service = AnalyticsService(self.source, store)
                proj = service.project(projection)
                self.assertEqual(proj["status"], "available")
                comp = service.compare(request)
                state = service.state("lab")
                report = service.report("lab", projection_id=proj["analysis_id"],
                                        comparison_id=comp["analysis_id"], snapshot_id=state["snapshot_id"])
            finally:
                store.close()
            store = LocalResultStore(db)
            try:
                app = AnalyticsApp(AnalyticsService(self.source, store))
                route = "reports/" + report["report_id"]
                self.assertEqual(http(app, route)[1]["payload"], report)
                self.assertEqual(http(app, route+"?environment_id=healthy")[0], 404)
                status, html = http(app, route+"?format=html")
                self.assertEqual(status, 200)
                self.assertNotIn("<script>bad()", html)
                self.assertIn("&lt;script&gt;bad()", html)
            finally:
                store.close()

    def test_frontend_examples_are_actual_responses(self):
        directory = Path(__file__).resolve().parents[1] / "fixtures/analytics"
        for name, response in audit_examples():
            self.assertEqual(json.loads((directory / name).read_text(encoding="utf-8")), response)

    def test_openapi_routes_and_references_match(self):
        from app.analytics_api.routes import ENDPOINTS, PREFIX

        spec_path = Path(__file__).resolve().parents[1] / "app/analytics_api/openapi.json"
        spec = json.loads(spec_path.read_text())
        paths = {PREFIX + route.replace("<report_id>", "{report_id}"): method.lower()
                 for route, method in ENDPOINTS.items()}
        self.assertEqual(set(spec["paths"]), set(paths))
        for path, method in paths.items():
            self.assertIn(method, spec["paths"][path])

        def check_refs(node):
            if isinstance(node, dict):
                if "$ref" in node:
                    target = spec
                    for key in node["$ref"].split("/")[1:]:
                        target = target[key]
                for child in node.values():
                    check_refs(child)
            elif isinstance(node, list):
                for child in node:
                    check_refs(child)
        check_refs(spec)

    def test_same_snapshot_on_repeated_reads(self):
        first = self.service.state("healthy")
        self.assertEqual(first, self.service.state("healthy"))
        ref = deepcopy(first["trace_refs"][0])
        ref["trace_id"] = "0" * 32
        self.assertEqual(http(self.app, "projections", "POST", {"trace_ref": ref, "changes": []})[0], 422)
