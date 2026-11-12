"""Exercise the public C entry points with OTLP decoded and stored by B."""
import io
import json
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from analytics_support import AnalyticsError
from app.analytics_api.__main__ import main
from app.analytics_api.repository_source import RepositorySource
from app.analytics_api.service import AnalyticsService
from app.analytics_api.storage import LocalResultStore
from contracts import IngestLimits, SourceBinding
from ingest import decode_otlp, normalize, RedactionProfile, SourceProfile
from repository import SpanStore

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "otlp"


class BackendIntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "spans.sqlite3"
        self.store = SpanStore(self.path)
        self.addCleanup(self.store.close)
        self.results = LocalResultStore()
        self.addCleanup(self.results.close)

    def ingest(self, payload, clock=None):
        batch = decode_otlp(json.dumps(payload).encode(), "application/json", None, IngestLimits())
        batch = normalize(batch, SourceProfile(clock_domain=clock), RedactionProfile())
        return self.store.ingest(batch, SourceBinding("otlp", "demo", "sandbox"), 100).trace_refs[0].to_dict()

    def test_cli_reads_existing_store_and_exports_same_exact_revisions(self):
        ref = self.ingest(json.loads((FIXTURES / "s01_retry.json").read_text()), clock="stand")
        def cli(*args):
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["--span-store", str(self.path), *args]), 0)
            return json.loads(out.getvalue())
        audit = cli("audit", "sandbox", "--require-rule", "OBS_RETRIES")
        retries = next(f for f in audit["findings"] if f["rule_id"] == "OBS_RETRIES")
        self.assertEqual(retries["exemplars"], [ref])
        self.assertEqual(audit["mode"], "trace_only")
        self.assertEqual(audit["grouping_version"], "1.1.0")
        self.assertNotIn("R4", {f["rule_id"] for f in audit["findings"]})
        report = self.path.with_suffix(".json")
        cli("export", "sandbox", "--output", str(report))
        self.assertEqual(json.loads(report.read_text())["input_refs"]["traces"], [ref])
        missing = self.path.with_name("typo.db")
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            main(["--span-store", str(missing), "state", "sandbox"])
        self.assertFalse(missing.exists())
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            main(["--span-store", str(self.path), "--store", str(self.path), "audit", "sandbox"])

    def test_projection_sees_sampling_losses_from_the_same_environment_snapshot(self):
        ref = self.ingest(json.loads((FIXTURES / "s01_retry.json").read_text()), clock="stand")
        sample = {"mode": "all_received", "known_losses": False}
        source = RepositorySource(self.store, control_snapshot=lambda: {
            "environments": {"sandbox": {"sampling": sample}}})
        service = AnalyticsService(source, self.results)
        request = {"trace_ref": ref, "changes": [{"span_id": "0000000000000002", "speedup_percent": 50}]}
        self.assertEqual(service.project(request)["gain_ns"], "125000000")
        sample["known_losses"] = True
        result = service.project(request)
        self.assertIsNone(result["gain_ns"])
        self.assertIn("source_loss", result["applicability"]["reason_codes"])

    def test_run_comparison_requires_recorded_lab_membership_and_control_metadata(self):
        control = {"runs": {}}
        source = RepositorySource(self.store, control_snapshot=lambda: control)
        service = AnalyticsService(source, self.results)
        request = {"baseline_run_id": "base", "candidate_run_id": "candidate"}
        for number, (run_id, attempts) in enumerate((("base", 3), ("candidate", 2)), 1):
            packet = json.loads((FIXTURES / "s01_retry.json").read_text())
            spans = packet["resourceSpans"][0]["scopeSpans"][0]["spans"]
            del spans[1 + attempts:]
            for index, span in enumerate(spans):
                span["traceId"] = f"{number:032x}"
                if index == 0:
                    span["attributes"].append({"key": "run_id", "value": {"stringValue": run_id}})
                else:
                    span["attributes"].extend([
                        {"key": "zerotrace.call_ref", "value": {"stringValue": "charge"}},
                        {"key": "zerotrace.attempt_index", "value": {"intValue": str(index)}},
                        {"key": "zerotrace.logical_call_id", "value": {"stringValue": "charge-1"}},
                    ])
            ref = self.ingest(packet, clock="stand")
            with source.read() as view:
                edge = view.trace(ref)["attempts"][0]["edge_id"]
            control["runs"][run_id] = {
                "run_id": run_id, "status": "completed", "stand_id": "stand", "environment_id": "sandbox",
                "load": {"count": 1}, "scenario_revision": 1, "fault_revision": 1,
                "config_snapshot": {"revision": str(number), "service_id": "svc-18", "edges": [
                    {"call_ref": "charge", "edge_id": edge, "max_attempts": attempts}]},
                "trace_collection": {"status": "ready", "expected_count": 1, "received_count": 1, "trace_refs": [ref]},
            }
        compared = service.compare(request)
        self.assertEqual([compared[side]["attempts"] for side in ("baseline", "candidate")], [3, 2])
        candidate = control["runs"]["candidate"]
        candidate["trace_collection"] = {**candidate["trace_collection"],
            "trace_refs": control["runs"]["base"]["trace_collection"]["trace_refs"]}
        with self.assertRaises(AnalyticsError):
            service.compare(request)
        del candidate["trace_collection"]
        with self.assertRaises(AnalyticsError) as exc:
            service.compare(request)
        self.assertEqual((exc.exception.status, exc.exception.code), (422, "invalid_run_sample"))
