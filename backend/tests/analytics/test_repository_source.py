"""RepositorySource adapts exact B revisions without owning their storage."""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from analytics_support import AnalyticsError
from app.analytics_api.repository_source import RepositorySource
from app.analytics_api.service import AnalyticsService
from app.analytics_api.storage import LocalResultStore
from contracts import IngestLimits, NormalizedSpan, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from ingest.normalize import NormalizedBatch
from repository import SpanStore


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "otlp"
BINDING = SourceBinding("source", "project", "shop")


def decoded_fixture(name="s01_retry", clock_domain="host-1"):
    payload = (FIXTURES / f"{name}.json").read_bytes()
    batch = decode_otlp(payload, "application/json", None, IngestLimits())
    return normalize(batch, SourceProfile(clock_domain=clock_domain), RedactionProfile())


def ingest_fixture(store, name="s01_retry", clock_domain="host-1"):
    return store.ingest(decoded_fixture(name, clock_domain), BINDING, 10).trace_refs[0]


def run_batch(trace_id, run_id):
    return NormalizedBatch(
        [
            NormalizedSpan(
                trace_id=trace_id,
                span_id="0" * 15 + "1",
                parent_span_id=None,
                service="orders",
                name="POST /orders",
                kind="server",
                start_ns=10,
                end_ns=20,
                duration_ns=10,
                clock_domain="host",
                operation="POST /orders",
                otel_status="unset",
                field_coverage={},
                diagnostics=[],
            )
        ],
        run_ids={trace_id: run_id},
    )


class RepositorySourceTests(unittest.TestCase):
    def test_frozen_run_revision_remains_readable_after_late_span_and_restart(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "spans.db"
            store = SpanStore(path)
            trace_id = "a" * 32
            store.start_collection("run-frozen", "shop", expected_count=1, deadline_at_ns=20)
            first = store.ingest(run_batch(trace_id, "run-frozen"), BINDING, 10).trace_refs[0]
            self.assertEqual(store.finalize_collection("run-frozen", 11).status, "ready")
            source = RepositorySource(store)
            with source.read() as view:
                self.assertEqual(len(view.trace(first.to_dict())["spans"]), 1)

            original = store.get_spans(first)[0]
            late = replace(original, span_id="f" * 16, parent_span_id=original.span_id)
            current = store.ingest(NormalizedBatch([late]), BINDING, 20).trace_refs[0]
            self.assertEqual(current.revision, first.revision + 1)
            store.close()

            reopened = SpanStore(path)
            source = RepositorySource(reopened)
            try:
                with source.read() as view:
                    archived = view.trace(first.to_dict())
                    latest = view.trace(current.to_dict())
                self.assertEqual(len(archived["spans"]), 1)
                self.assertEqual(len(latest["spans"]), 2)
            finally:
                reopened.close()

    def test_control_snapshot_is_copied_for_one_consistent_read(self):
        with SpanStore(Path(":memory:")) as store:
            ref = ingest_fixture(store)
            state = {"environments": {"shop": {"sampling": {"mode": "tail"}}}, "runs": {"run-1": {"run_id": "run-1"}}}
            source = RepositorySource(store, control_snapshot=lambda: state)
            with source.read() as view:
                key = view.audit_cache_key("shop")
                state["environments"]["shop"]["sampling"]["mode"] = "head"
                model = view.environment("shop")["model"]
                self.assertEqual((model["project_id"], model["sampling"]["mode"]), ("project", "tail"))
                self.assertEqual(model["traces"][0]["trace_ref"], ref.to_dict())
                self.assertEqual(view.run("run-1")["run_id"], "run-1")
                self.assertEqual(view.audit_cache_key("shop"), key)
                with self.assertRaises(AnalyticsError) as error:
                    view.run("unknown")
                self.assertEqual(error.exception.status, 404)
            with source.read() as view:
                self.assertNotEqual(view.audit_cache_key("shop"), key)

    def test_conflicting_span_keeps_the_first_accepted_revision(self):
        with SpanStore(Path(":memory:")) as store:
            ref = ingest_fixture(store)
            service = AnalyticsService(RepositorySource(store), LocalResultStore())
            try:
                request = {"trace_ref": ref.to_dict(), "changes": [{"span_id": "0000000000000002", "speedup_percent": 50}]}
                self.assertEqual(service.project(request)["status"], "available")
                accepted = store.get_spans(ref)[1]
                result = store.ingest(NormalizedBatch([replace(accepted, name="different")]), BINDING, 20)
                updated = result.trace_refs[0]
                self.assertEqual(updated, ref)
                self.assertEqual([item.code for item in result.diagnostics], ["conflicting_span"])
                self.assertEqual(service.project(request)["status"], "available")
            finally:
                service.results.close()

    def test_environment_with_multiple_projects_is_rejected_as_ambiguous(self):
        with SpanStore(Path(":memory:")) as store:
            ingest_fixture(store)
            second = replace(decoded_fixture().spans[0], trace_id="c" * 32)
            store.ingest(NormalizedBatch([second]), SourceBinding("other", "another", "shop"), 30)
            with RepositorySource(store).read() as view, self.assertRaises(AnalyticsError) as error:
                view.environment("shop")
            self.assertEqual((error.exception.code, error.exception.status), ("ambiguous_environment", 409))
