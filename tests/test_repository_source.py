import json
import sqlite3
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from analytics_support import AnalyticsError
from app.analytics_api.repository_source import RepositorySource
from app.analytics_api.service import AnalyticsService
from app.analytics_api.storage import LocalResultStore
from backend.contracts import IngestLimits, SourceBinding
from backend.ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from backend.ingest.normalize import NormalizedBatch
from backend.repository import SpanStore


FIXTURES = Path(__file__).resolve().parents[1] / "backend/fixtures/otlp"
BINDING = SourceBinding("source", "project", "shop")


def ingest_fixture(store, name="s01_retry", clock_domain=None):
    payload = (FIXTURES / f"{name}.json").read_bytes()
    batch = decode_otlp(payload, "application/json", None, IngestLimits())
    normalized = normalize(batch, SourceProfile(clock_domain=clock_domain), RedactionProfile())
    return store.ingest(normalized, BINDING, 10).trace_refs[0]


class RepositorySourceTests(unittest.TestCase):
    def test_pinned_read_survives_new_revisions_and_deletion(self):
        with TemporaryDirectory() as tmp:
            for path in (Path(tmp) / "b.sqlite3", Path(":memory:")):
                with self.subTest(path=path), SpanStore(path) as store:
                    ref = ingest_fixture(store)
                    source = RepositorySource(store if str(path) == ":memory:" else path)
                    with source.read() as old:
                        initial_key = old.audit_cache_key("shop")
                        original = store.get_spans(ref)[0]
                        late = replace(original, span_id="f" * 16, parent_span_id=original.span_id)
                        updated = store.ingest(NormalizedBatch([late]), BINDING, 20).trace_refs[0]
                        self.assertEqual(len(old.trace(ref.to_dict())["spans"]), 4)
                        self.assertEqual(old.audit_cache_key("shop"), initial_key)
                        self.assertEqual(old.environment("shop")["model"]["traces"][0]["trace_ref"], ref.to_dict())
                        with source.read() as current:
                            self.assertNotEqual(current.audit_cache_key("shop"), initial_key)
                            self.assertEqual(len(current.trace(updated.to_dict())["spans"]), 5)
                            with self.assertRaises(AnalyticsError) as err:
                                current.trace(ref.to_dict())
                            self.assertEqual((err.exception.code, err.exception.status), ("stale_revision", 409))
                        self.assertTrue(store.delete_trace(updated, updated.generation, updated.revision))
                        self.assertEqual(old.span_store.get_spans(ref)[0], original)
                    with source.read() as view, self.assertRaises(AnalyticsError) as err:
                        view.trace(updated.to_dict())
                    self.assertEqual(err.exception.status, 404)
                    repeated = ingest_fixture(store)
                    self.assertGreater(repeated.generation, updated.generation)
                    self.assertIsNone(store.get_spans(ref))
                    with source.read() as view, self.assertRaises(AnalyticsError) as err:
                        view.trace(ref.to_dict())
                    self.assertEqual(err.exception.status, 404)

    def test_audit_uses_real_telemetry_and_pinned_control_metadata(self):
        with SpanStore(Path(":memory:")) as store:
            ref = ingest_fixture(store, clock_domain="host-1")
            state = {"environments": {"shop": {"sampling": {"mode": "tail"}}},
                     "runs": {"run-1": {"run_id": "run-1", "status": "running"}}}
            source = RepositorySource(store, control_snapshot=lambda: state)
            with source.read() as view:
                key = view.audit_cache_key("shop")
                state["environments"]["shop"]["sampling"]["mode"] = "head"
                model = view.environment("shop")["model"]
                self.assertEqual((model["project_id"], model["sampling"]["mode"]), ("project", "tail"))
                self.assertEqual(len(model["edges"]), 1)
                self.assertEqual(model["traces"][0]["trace_ref"], ref.to_dict())
                self.assertEqual(view.run("run-1")["run_id"], "run-1")
                self.assertEqual(view.audit_cache_key("shop"), key)
                with self.assertRaises(AnalyticsError) as err:
                    view.run("unregistered")
                self.assertEqual(err.exception.status, 404)
            with source.read() as view:
                self.assertNotEqual(view.audit_cache_key("shop"), key)
            results = LocalResultStore()
            try:
                audit = AnalyticsService(source, results).audit("shop")
                self.assertEqual(audit["mode"], "trace_only")
                self.assertIn("OBS_RETRIES", [row["rule_id"] for row in audit["findings"]])
                self.assertEqual(audit["config_refs"], [])
            finally:
                results.close()

    def test_persisted_conflict_invalidates_projection_and_old_revision(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "b.db"
            with SpanStore(path) as store:
                ref = ingest_fixture(store, clock_domain="host-1")
                source = RepositorySource(store)
                results = LocalResultStore()
                try:
                    service = AnalyticsService(source, results)
                    change = [{"span_id": "0000000000000002", "speedup_percent": 50}]
                    self.assertEqual(service.project({"trace_ref": ref.to_dict(), "changes": change})["status"],
                                     "available")
                    accepted = store.get_spans(ref)[1]
                    conflicting = replace(accepted, name="different")
                    updated = store.ingest(NormalizedBatch([conflicting]), BINDING, 20).trace_refs[0]
                    self.assertGreater(updated.revision, ref.revision)
                    with self.assertRaises(AnalyticsError) as err:
                        service.project({"trace_ref": ref.to_dict(), "changes": change})
                    self.assertEqual(err.exception.status, 409)
                    replay = store.ingest(NormalizedBatch([conflicting]), BINDING, 30).trace_refs[0]
                    self.assertEqual(replay, updated)
                    projected = service.project({"trace_ref": updated.to_dict(), "changes": change})
                    self.assertIsNone(projected["projected_ns"])
                    self.assertIn("conflicting_span", projected["applicability"]["reason_codes"])
                finally:
                    results.close()
            with RepositorySource(path).read() as reopened:
                trace = reopened.trace(updated.to_dict())
                self.assertEqual(trace["quality"]["conflicting_span_count"], 1)
                self.assertEqual(trace["quality"]["issues"][-1]["span_ids"], [accepted.span_id])

    def test_storage_rejects_collisions_even_when_one_batch_contains_new_spans(self):
        with SpanStore(Path(":memory:")) as store:
            ref = ingest_fixture(store)
            original = store.get_spans(ref)[0]
            additional = replace(original, trace_id="c" * 32)
            for binding in (SourceBinding("other", "project", "lab"), SourceBinding("other", "another", "shop")):
                with self.subTest(binding=binding), self.assertRaisesRegex(ValueError, "binding"):
                    store.ingest(NormalizedBatch([additional, original]), binding, 30)
                self.assertIsNone(store.resolve_trace(binding.environment_id, additional.trace_id))
            self.assertEqual(4, len(store.get_spans(ref)))

    def test_replaying_otlp_after_v1_upgrade_keeps_the_original_observation(self):
        packet = json.loads((FIXTURES / "s01_retry.json").read_bytes())
        resource = packet["resourceSpans"][0]
        namespace = next(a for a in resource["resource"]["attributes"] if a["key"] == "service.namespace")
        namespace["value"] = {"stringValue": "checkout"}
        raw_spans = resource["scopeSpans"][0]["spans"]
        raw_spans[0]["attributes"].append({"key": "run_id", "value": {"stringValue": "run-7"}})
        for index, raw in enumerate(raw_spans[1:], 1):
            raw["attributes"].extend([
                {"key": "logical_call_id", "value": {"stringValue": "pay"}},
                {"key": "attempt_index", "value": {"intValue": str(index)}},
            ])
        body = json.dumps(packet).encode()
        def decode():
            return normalize(decode_otlp(body, "application/json", None, IngestLimits()),
                             SourceProfile(clock_domain="host"), RedactionProfile())
        current = decode().spans
        self.assertEqual((current[0].service_namespace, current[0].run_id), ("checkout", "run-7"))
        self.assertEqual((current[1].logical_call_id, current[1].attempt_index), ("pay", 1))
        legacy = [replace(span, service_namespace=None, run_id=None, logical_call_id=None, attempt_index=None)
                  for span in current]
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with SpanStore(path) as store:
                first = store.ingest(NormalizedBatch(legacy), BINDING, 10).trace_refs[0]
            with sqlite3.connect(path) as conn:
                conn.execute("ALTER TABLE spans DROP COLUMN conflicting")
                conn.execute("ALTER TABLE spans DROP COLUMN payload_version")
                conn.execute("DROP TABLE trace_generations")
                conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
            for _ in range(2):
                with SpanStore(path) as store:
                    replay = store.ingest(decode(), BINDING, 20)
                    self.assertEqual(replay.trace_refs, [first])
                    self.assertEqual(replay.diagnostics, [])
                    self.assertEqual(store.get_spans(first), legacy)
            with SpanStore(path) as store:
                changed = replace(current[0], name="genuinely changed")
                update = store.ingest(NormalizedBatch([changed]), BINDING, 30)
                self.assertEqual(update.trace_refs[0].revision, first.revision + 1)
                self.assertEqual([i.code for i in update.diagnostics], ["conflicting_span"])
                # Spans first seen after migration use v2 semantics even in this v1 database.
                fresh = replace(current[1], span_id="f" * 16, logical_call_id=None)
                clean = store.ingest(NormalizedBatch([fresh]), BINDING, 40).trace_refs[0]
                labelled = replace(fresh, logical_call_id="pay")
                update = store.ingest(NormalizedBatch([labelled]), BINDING, 50)
                self.assertEqual(update.trace_refs[0].revision, clean.revision + 1)
                self.assertEqual([i.code for i in update.diagnostics], ["conflicting_span"])

    def test_empty_environment_is_only_known_if_registered_and_path_is_read_only(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "new.sqlite3"
            with self.assertRaises(sqlite3.OperationalError), RepositorySource(path).read():
                pass
            self.assertFalse(path.exists())
            with SpanStore(path) as store:
                original = ingest_fixture(store)
            # A database created by B before the generation fix remains readable without migration.
            with sqlite3.connect(path) as conn:
                conn.execute("ALTER TABLE spans DROP COLUMN conflicting")
                conn.execute("ALTER TABLE spans DROP COLUMN payload_version")
                conn.execute("DROP TABLE trace_generations")
                conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")
            source = RepositorySource(path, control_snapshot=lambda: {"environments": {"lab": {}}})
            with source.read() as view:
                self.assertEqual(len(view.trace(original.to_dict())["spans"]), 4)
                self.assertEqual(view.environment("lab")["model"]["traces"], [])
                with self.assertRaises(AnalyticsError) as err:
                    view.environment("undefined")
                self.assertEqual(err.exception.status, 404)
            with sqlite3.connect(path) as conn:
                self.assertEqual(conn.execute("SELECT value FROM meta").fetchone(), ("1",))
                self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='trace_generations'").fetchone())

            with SpanStore(path) as migrated:
                first = migrated.get_spans(original)[0]
                changed = migrated.ingest(NormalizedBatch([replace(first, name="changed")]), BINDING, 11)
                self.assertGreater(changed.trace_refs[0].revision, original.revision)
