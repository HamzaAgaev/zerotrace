"""B's data routes and C's audit routes read one repository snapshot."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from app.analytics_api.backend_routes import BackendApp
from app.analytics_api.repository_source import RepositorySource
from app.analytics_api.service import AnalyticsService
from app.analytics_api.storage import LocalResultStore
from contracts import IngestLimits, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from repository import SpanStore
from tests.analytics.test_analytics_api import http


class BackendRoutesTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.spans = SpanStore(Path(directory.name) / "spans.db")
        self.addCleanup(self.spans.close)
        results = LocalResultStore()
        self.addCleanup(results.close)
        self.app = BackendApp(AnalyticsService(RepositorySource(self.spans), results))

    def ingest(self, name, *, trace_id=None, clock="shop-clock"):
        path = Path(__file__).resolve().parents[2] / "fixtures" / "otlp" / f"{name}.json"
        body = path.read_bytes()
        if trace_id:
            body = body.replace(b"b" * 32, trace_id.encode())
        batch = decode_otlp(body, "application/json", None, IngestLimits())
        normalized = normalize(batch, SourceProfile(source_id="src", environment_id="shop", clock_domain=clock), RedactionProfile())
        if clock == "mixed":
            normalized.spans[-1] = replace(normalized.spans[-1], clock_domain="other-clock")
        return self.spans.ingest(normalized, SourceBinding("src", "p", "shop"), 1_000).trace_refs[0]

    def test_data_routes_and_audit_reference_the_same_trace_revisions(self):
        self.ingest("s00_healthy")
        for index in range(1, 8):
            last = self.ingest("s01_retry", trace_id=f"{index:032x}")
        status, audit = http(self.app, "audit?environment_id=shop&view=full")
        self.assertEqual(status, 200)
        retry = next(item for item in audit["payload"]["findings"] if item["rule_id"] == "OBS_RETRIES")
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

    def test_cross_clock_quality_and_public_data_route_errors(self):
        ref = self.ingest("s00_healthy", clock="mixed")
        status, detail = http(self.app, f"traces/{ref.trace_id}?environment_id=shop")
        self.assertEqual(status, 200)
        self.assertIn("cross_clock_uncertain", detail["payload"]["quality_issues"])
        self.assertIn("cross_clock_uncertain", detail["payload"]["summary"]["quality_issues"])
        route = f"traces/{ref.trace_id}?environment_id=shop"
        for path, code in ((route + "&revision=1", "version_pair_required"), ("traces?environment_id=shop&offset=bad", "invalid_offset"), ("traces?limit=10", "missing_environment"), ("traces?environment_id=shop&limit=101", "invalid_limit")):
            status, body = http(self.app, path)
            self.assertEqual((status, body["error"]["code"]), (400, code))
        self.assertEqual(http(self.app, route, method="POST")[0], 405)
        status, missing = http(self.app, route + "&generation=1&revision=99")
        self.assertEqual((status, missing["error"]["code"]), (404, "trace_unavailable"))


class BackendImportTest(unittest.TestCase):
    def test_canonical_imports_resolve_from_the_backend_tree(self):
        repo = Path(__file__).resolve().parents[3]
        env = {**os.environ, "PYTHONPATH": str(repo / "backend") + os.pathsep + str(repo)}
        result = subprocess.run([sys.executable, "-c", "from contracts.p2 import TraceGraph; from app.data_api.handlers import ApiError; from app.analytics_api.backend_routes import BackendApp; assert TraceGraph.__module__ == 'contracts.p2'; assert ApiError.__module__ == 'app.data_api.handlers'; assert BackendApp.__module__ == 'app.analytics_api.backend_routes'"], cwd=repo, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
