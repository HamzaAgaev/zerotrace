from __future__ import annotations

import time
from pathlib import Path

import pytest

from contracts import SourceBinding
from ingest.normalize import NormalizedBatch
from repository import SpanStore

BINDING = SourceBinding(source_id="src", project_id="p", environment_id="env")
T0 = 1_763_002_000_000_000_000
QUIET_AFTER = 5_000_000_000


def batch(trace_id: str) -> NormalizedBatch:
    from contracts import NormalizedSpan

    span = NormalizedSpan(
        trace_id=trace_id,
        span_id="00" * 7 + "01",
        parent_span_id=None,
        service="svc",
        name="SERVER /a",
        kind="server",
        start_ns=T0,
        end_ns=T0 + 100,
        duration_ns=100,
        clock_domain="h1",
        operation="GET /a",
        otel_status="unset",
        field_coverage={},
        diagnostics=[],
    )
    return NormalizedBatch(spans=[span])


@pytest.fixture
def store(tmp_path: Path):
    with SpanStore(tmp_path / "asm.db") as s:
        yield s


class TestAssemblyLifecycle:
    def test_quiet_after_silence(self, store: SpanStore):
        store.ingest(batch("aa" * 16), BINDING, T0)
        assert store.advance_assembly(T0 + QUIET_AFTER - 1) == 0
        assert store.advance_assembly(T0 + QUIET_AFTER) == 1
        meta = store.resolve_trace("env", "aa" * 16)
        assert meta is not None
        assert meta.assembly_state == "quiet"

    def test_advance_is_idempotent(self, store: SpanStore):
        store.ingest(batch("ab" * 16), BINDING, T0)
        store.advance_assembly(T0 + 10 * QUIET_AFTER)
        assert store.advance_assembly(T0 + 20 * QUIET_AFTER) == 0

    def test_force_close_records_reason(self, store: SpanStore):
        ref = store.ingest(batch("ac" * 16), BINDING, T0).trace_refs[0]
        assert store.force_close(ref, "span_limit_reached") is True
        meta = store.resolve_trace("env", "ac" * 16)
        assert meta is not None
        assert meta.assembly_state == "forced"
        assert meta.forced_reason == "span_limit_reached"

    def test_force_close_unknown_trace(self, store: SpanStore):
        from contracts import TraceRef

        ref = TraceRef(environment_id="env", trace_id="ff" * 16, generation=1, revision=1)
        assert store.force_close(ref, "x") is False

    def test_forced_reason_reaches_graph_via_adapter(self, store: SpanStore):
        from app.data_api.adapter import make_graph_builder

        ref = store.ingest(batch("af" * 16), BINDING, T0).trace_refs[0]
        store.force_close(ref, "span_limit_reached")
        build = make_graph_builder(store)
        graph = build(store.get_spans(ref) or (), ref)
        assert graph.assembly_state == "forced"
        (issue,) = [i for i in graph.quality.issues if i.code == "forced_close"]
        assert "span_limit_reached" in issue.details

    def test_quiet_not_reopened_by_late_span(self, store: SpanStore):
        ref = store.ingest(batch("ad" * 16), BINDING, T0).trace_refs[0]
        store.advance_assembly(T0 + 10 * QUIET_AFTER)
        store.ingest(batch("ad" * 16), BINDING, T0 + 11 * QUIET_AFTER)
        meta = store.resolve_trace("env", "ad" * 16)
        assert meta is not None
        assert meta.assembly_state == "quiet"
        assert meta.trace_ref.revision >= ref.revision

    def test_schema_upgrade_from_v1(self, tmp_path: Path):
        import sqlite3

        db = tmp_path / "old.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema_version', '1')")
        conn.execute(
            "CREATE TABLE traces (project_id TEXT NOT NULL, environment_id TEXT NOT NULL, "
            "trace_id TEXT NOT NULL, generation INTEGER NOT NULL, revision INTEGER NOT NULL, "
            "first_received_at_ns INTEGER NOT NULL, last_received_at_ns INTEGER NOT NULL, "
            "assembly_state TEXT NOT NULL, PRIMARY KEY (project_id, trace_id, generation))"
        )
        conn.execute(
            "CREATE TABLE spans (project_id TEXT NOT NULL, trace_id TEXT NOT NULL, "
            "generation INTEGER NOT NULL, span_id TEXT NOT NULL, start_ns INTEGER NOT NULL, "
            "payload TEXT NOT NULL, received_at_ns INTEGER NOT NULL, "
            "PRIMARY KEY (project_id, trace_id, generation, span_id))"
        )
        conn.commit()
        conn.close()
        with SpanStore(db) as upgraded:
            version = upgraded._conn.execute(
                "SELECT value FROM meta WHERE key = 'schema_version'"
            ).fetchone()
            assert int(version[0]) == 3


class TestAssemblyTicker:
    def test_ticker_closes_silent_open_traces(self, store: SpanStore):
        from app.server.assembly import AssemblyTicker

        store.ingest(batch("ae" * 16), BINDING, T0)
        ticker = AssemblyTicker(store, interval_s=0.02, quiet_after_ns=50_000_000).start()
        try:
            deadline = time.time() + 5
            meta = store.resolve_trace("env", "ae" * 16)
            while time.time() < deadline and meta.assembly_state != "quiet":
                time.sleep(0.05)
                meta = store.resolve_trace("env", "ae" * 16)
            assert meta.assembly_state == "quiet"
        finally:
            ticker.stop()

    def test_ticker_exits_when_store_closes(self, store: SpanStore):
        from app.server.assembly import AssemblyTicker

        ticker = AssemblyTicker(store, interval_s=0.01).start()
        thread = ticker._thread
        store.close()
        thread.join(timeout=2)
        assert not thread.is_alive()
