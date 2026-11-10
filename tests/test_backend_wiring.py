"""The data API and the audit must describe the same OTLP trace revisions."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.analytics_api.backend_routes import BackendApp
from app.analytics_api.repository_source import RepositorySource
from app.analytics_api.service import AnalyticsService
from app.analytics_api.storage import LocalResultStore
from backend.contracts import IngestLimits, SourceBinding
from backend.ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from backend.repository import SpanStore
from tests.test_analytics_api import http


class BackendRoutesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.spans = SpanStore(Path(directory.name) / "spans.db")
        self.addCleanup(self.spans.close)
        results = LocalResultStore()
        self.addCleanup(results.close)
        self.app = BackendApp(AnalyticsService(RepositorySource(self.spans), results))

    def ingest(self, name, *, new_trace_id=None, clock="shop-clock"):
        path = Path(__file__).resolve().parents[1] / "backend" / "fixtures" / "otlp" / f"{name}.json"
        body = path.read_bytes()
        if new_trace_id:
            body = body.replace(b"b" * 32, new_trace_id.encode())
        batch = decode_otlp(body, "application/json", None, IngestLimits())
        profile = SourceProfile(source_id="src", environment_id="shop", clock_domain=clock)
        normalized = normalize(batch, profile, RedactionProfile())
        return self.spans.ingest(normalized, SourceBinding("src", "p", "shop"), 1_000).trace_refs[0]

    def test_counts_and_exact_links_beyond_audit_exemplars(self):
        self.ingest("s00_healthy")
        for i in range(1, 8):
            last = self.ingest("s01_retry", new_trace_id=f"{i:032x}")
        status, audit = http(self.app, "audit?environment_id=shop&view=full")
        self.assertEqual(status, 200)
        retry = next(f for f in audit["payload"]["findings"] if f["rule_id"] == "OBS_RETRIES")
        self.assertEqual(len(retry["exemplars"]), 5)
        self.assertNotIn(last.to_dict(), retry["exemplars"])
        status, listed = http(self.app, "traces?environment_id=shop&limit=100")
        self.assertEqual(status, 200)
        items = listed["payload"]["items"]
        self.assertEqual(len(items), 8)
        expected = next(item for item in items if item["trace_ref"] == last.to_dict())
        self.assertGreater(expected["finding_count"], 0)
        status, detail = http(self.app, f"traces/{last.trace_id}?environment_id=shop&generation=1&revision=1")
        self.assertEqual(status, 200)
        payload = detail["payload"]
        self.assertEqual(payload["summary"], expected)
        self.assertIn(retry["occurrence_id"], payload["finding_occurrence_ids"])
        self.assertEqual(len(payload["finding_occurrence_ids"]), expected["finding_count"])

    def test_unknown_clock_domains_do_not_authorize_one_timeline(self):
        ref = self.ingest("s00_healthy", clock=None)
        status, detail = http(self.app, f"traces/{ref.trace_id}?environment_id=shop")
        self.assertEqual(status, 200)
        self.assertIn("cross_clock_uncertain", detail["payload"]["quality_issues"])
        self.assertIn("cross_clock_uncertain", detail["payload"]["summary"]["quality_issues"])

    def test_data_route_errors_retain_public_codes(self):
        ref = self.ingest("s00_healthy")
        route = f"traces/{ref.trace_id}?environment_id=shop"
        for path, code in (
            (route + "&revision=1", "version_pair_required"),
            ("traces?environment_id=shop&offset=bad", "invalid_offset"),
            ("traces?limit=10", "missing_environment"),
            ("traces?environment_id=shop&limit=101", "invalid_limit"),
        ):
            status, data = http(self.app, path)
            self.assertEqual((status, data["error"]["code"]), (400, code))
        status, _ = http(self.app, route, method="POST")
        self.assertEqual(status, 405)
        status, missing = http(self.app, route + "&generation=1&revision=99")
        self.assertEqual((status, missing["error"]["code"]), (404, "trace_unavailable"))


class BackendImportTest(unittest.TestCase):
    def test_historical_imports_share_classes_in_both_import_orders(self):
        repo = Path(__file__).resolve().parents[1]
        env = {**os.environ, "PYTHONPATH": str(repo / "backend") + os.pathsep + str(repo)}
        for first, second in (("contracts", "backend.contracts"), ("backend.contracts", "contracts")):
            with self.subTest(first=first):
                result = subprocess.run(
                    [sys.executable, "-c",
                     f"from {first}.p2 import TraceGraph; from {second}.p2 import TraceGraph as B; "
                     "from app.data_api.handlers import ApiError; "
                     "from backend.app.data_api.handlers import ApiError as Canonical; "
                     "from app.analytics_api.backend_routes import BackendApp; "
                     "assert TraceGraph is B and ApiError is Canonical"],
                    cwd=repo, env=env, capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
