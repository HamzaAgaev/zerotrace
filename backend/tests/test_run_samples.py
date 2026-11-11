from __future__ import annotations

from pathlib import Path

import pytest

from contracts import NormalizedSpan, SourceBinding, TraceRef
from ingest.normalize import NormalizedBatch
from repository import SpanStore

BINDING = SourceBinding(source_id="src-lab", project_id="zerotrace-demo", environment_id="sandbox")
T0 = 1_763_001_000_000_000_000
DEADLINE = T0 + 20_000_000_000


def entry_span(trace_id: str, span_id: str, run_id: str, probe: bool = False) -> NormalizedSpan:
    attrs_note = f"run:{run_id}"
    diagnostics = [attrs_note]
    if probe:
        diagnostics.append("probe")
    return NormalizedSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=None,
        service="orders",
        name="SERVER /checkout",
        kind="server",
        start_ns=T0,
        end_ns=T0 + 100_000_000,
        duration_ns=100_000_000,
        clock_domain="host-1",
        operation="GET /checkout",
        otel_status="unset",
        field_coverage={},
        diagnostics=diagnostics,
    )


def make_batch(trace_id: str, run_id: str, probe: bool = False) -> NormalizedBatch:
    return NormalizedBatch(
        spans=[entry_span(trace_id, "00" * 7 + "01", run_id, probe)],
        run_ids={trace_id: run_id},
        probe_trace_ids={trace_id} if probe else set(),
    )


@pytest.fixture
def store(tmp_path: Path):
    with SpanStore(tmp_path / "runs.db") as s:
        yield s


class TestRunSamples:
    def test_waiting_then_ready(self, store: SpanStore):
        store.start_collection("run-1", "sandbox", expected_count=2, deadline_at_ns=DEADLINE)
        assert store.get_sample("run-1") is not None
        store.ingest(make_batch("dd" * 16, "run-1"), BINDING, T0)
        waiting = store.finalize_collection("run-1", T0 + 1_000_000_000)
        assert waiting is not None and waiting.status == "waiting"
        assert waiting.received_count == 1
        assert len(waiting.trace_refs) == 1
        store.ingest(make_batch("de" * 16, "run-1"), BINDING, T0 + 2_000_000_000)
        ready = store.finalize_collection("run-1", T0 + 3_000_000_000)
        assert ready is not None and ready.status == "ready"
        assert ready.received_count == 2
        assert len(ready.trace_refs) == 2
        assert all(isinstance(r, TraceRef) for r in ready.trace_refs)

    def test_frozen_revision_survives_late_spans_and_restart(self, tmp_path: Path):
        db = tmp_path / "frozen-revisions.db"
        store = SpanStore(db)
        trace_id = "ac" * 16
        run_id = "run-frozen"
        initial = make_batch(trace_id, run_id)
        store.start_collection(run_id, "sandbox", expected_count=1, deadline_at_ns=DEADLINE)
        first_ref = store.ingest(initial, BINDING, T0).trace_refs[0]
        sample = store.finalize_collection(run_id, T0 + 1)
        assert sample is not None and sample.status == "ready"
        assert sample.trace_refs == [first_ref]

        late_span = entry_span(trace_id, "00" * 7 + "02", run_id)
        late = NormalizedBatch(spans=[late_span], run_ids={trace_id: run_id})
        current_ref = store.ingest(late, BINDING, T0 + 5_000_000_000).trace_refs[0]
        assert current_ref.revision == first_ref.revision + 1
        store.close()

        reopened = SpanStore(db)
        archived_meta = reopened.get_trace_meta(first_ref)
        assert archived_meta is not None
        assert archived_meta.trace_ref == first_ref
        assert archived_meta.span_count == 1
        assert archived_meta.last_received_at_ns == T0
        archived_spans = reopened.get_spans(first_ref)
        assert archived_spans is not None
        assert [span.span_id for span in archived_spans] == ["00" * 7 + "01"]
        resolved = reopened.resolve_trace(
            "sandbox", trace_id, first_ref.generation, first_ref.revision
        )
        assert resolved == archived_meta

        current = reopened.resolve_trace("sandbox", trace_id, current_ref.generation)
        assert current is not None and current.trace_ref == current_ref
        listed, _ = reopened.list_traces("sandbox", limit=10)[0]
        assert listed == current_ref
        reopened.close()

    def test_freeze_is_stable_for_late_traces(self, store: SpanStore):
        store.start_collection("run-2", "sandbox", expected_count=1, deadline_at_ns=DEADLINE)
        store.ingest(make_batch("e1" * 16, "run-2"), BINDING, T0)
        first = store.finalize_collection("run-2", T0 + 1_000_000_000)
        assert first is not None and first.status == "ready"
        store.ingest(make_batch("e2" * 16, "run-2"), BINDING, T0 + 5_000_000_000)
        again = store.get_sample("run-2")
        assert again is not None
        assert again.trace_refs == first.trace_refs
        assert again.received_count == 1

    def test_partial_after_deadline(self, store: SpanStore):
        store.start_collection("run-3", "sandbox", expected_count=5, deadline_at_ns=DEADLINE)
        store.ingest(make_batch("f1" * 16, "run-3"), BINDING, T0)
        sample = store.finalize_collection("run-3", DEADLINE + 1)
        assert sample is not None and sample.status == "partial"
        assert sample.received_count == 1
        assert len(sample.trace_refs) == 1

    def test_probe_trace_excluded(self, store: SpanStore):
        store.start_collection("run-4", "sandbox", expected_count=1, deadline_at_ns=DEADLINE)
        store.ingest(make_batch("aa" * 16, "run-4", probe=True), BINDING, T0)
        store.ingest(make_batch("ab" * 16, "run-4"), BINDING, T0 + 1)
        sample = store.finalize_collection("run-4", T0 + 2_000_000_000)
        assert sample is not None and sample.status == "ready"
        assert sample.received_count == 1
        assert sample.trace_refs[0].trace_id == "ab" * 16

    def test_start_collection_conflict(self, store: SpanStore):
        store.start_collection("run-5", "sandbox", expected_count=1, deadline_at_ns=DEADLINE)
        store.start_collection("run-5", "sandbox", expected_count=1, deadline_at_ns=DEADLINE)
        with pytest.raises(ValueError):
            store.start_collection("run-5", "sandbox", expected_count=2, deadline_at_ns=DEADLINE)

    def test_unknown_run(self, store: SpanStore):
        assert store.get_sample("nope") is None
        assert store.finalize_collection("nope", T0) is None
