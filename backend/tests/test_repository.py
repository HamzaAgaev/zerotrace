from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from contracts import NormalizedSpan, SourceBinding
from repository import NormalizedBatch, SpanStore

T0 = 1_700_000_000_000_000_000
BINDING = SourceBinding(source_id="src-1", project_id="proj", environment_id="shop")


def make_span(
    trace_id: str,
    span_id: str,
    *,
    start_ns: int = T0,
    duration_ns: int = 1_000_000,
    parent_span_id: str | None = None,
    name: str = "GET /checkout",
    service: str = "orders",
    kind: str = "server",
    otel_status: str = "ok",
) -> NormalizedSpan:
    return NormalizedSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent_span_id,
        service=service,
        name=name,
        kind=kind,  # type: ignore[arg-type]
        start_ns=start_ns,
        end_ns=start_ns + duration_ns,
        duration_ns=duration_ns,
        clock_domain="mono0",
        operation=name,
        otel_status=otel_status,  # type: ignore[arg-type]
        field_coverage={"http_status": True},
        diagnostics=[],
    )


@pytest.fixture
def store(tmp_path: Path):
    s = SpanStore(tmp_path / "zerotrace.db")
    yield s
    s.close()


def test_ingest_roundtrip(store: SpanStore):
    spans = [
        make_span("a" * 32, "1" * 16),
        make_span("a" * 32, "2" * 16, parent_span_id="1" * 16, start_ns=T0 + 100),
    ]
    result = store.ingest(NormalizedBatch(spans=spans), BINDING, T0)
    assert len(result.trace_refs) == 1
    ref = result.trace_refs[0]
    assert ref.generation == 1
    assert ref.revision == 1
    assert ref.environment_id == "shop"

    loaded = store.get_spans(ref)
    assert loaded is not None
    assert {s.span_id: s for s in loaded} == {s.span_id: s for s in spans}

    meta = store.get_trace_meta(ref)
    assert meta is not None
    assert meta.span_count == 2
    assert meta.first_received_at_ns == T0
    assert meta.last_received_at_ns == T0


def test_duplicate_batch_keeps_revision_and_last_received(store: SpanStore):
    batch = NormalizedBatch(spans=[make_span("a" * 32, "1" * 16)])
    first = store.ingest(batch, BINDING, T0)
    second = store.ingest(batch, BINDING, T0 + 5_000_000_000)
    assert second.trace_refs[0].revision == first.trace_refs[0].revision
    assert second.diagnostics == []

    meta = store.get_trace_meta(second.trace_refs[0])
    assert meta is not None
    assert meta.last_received_at_ns == T0
    assert meta.span_count == 1


def test_late_span_bumps_revision(store: SpanStore):
    root = make_span("a" * 32, "1" * 16)
    ref1 = store.ingest(NormalizedBatch(spans=[root]), BINDING, T0).trace_refs[0]

    late = make_span("a" * 32, "2" * 16, parent_span_id="1" * 16, start_ns=T0 + 500)
    t1 = T0 + 60_000_000_000
    ref2 = store.ingest(NormalizedBatch(spans=[late]), BINDING, t1).trace_refs[0]
    assert ref2.generation == 1
    assert ref2.revision == 2

    meta = store.get_trace_meta(ref2)
    assert meta is not None
    assert meta.first_received_at_ns == T0
    assert meta.last_received_at_ns == t1

    loaded = store.get_spans(ref2)
    assert loaded is not None
    assert {s.span_id for s in loaded} == {"1" * 16, "2" * 16}
    # Старая ревизия больше не является точным снапшотом.
    assert store.get_spans(ref1) is None


def test_conflicting_span_keeps_first_accepted(store: SpanStore):
    original = make_span("a" * 32, "1" * 16, name="GET /checkout")
    store.ingest(NormalizedBatch(spans=[original]), BINDING, T0)

    conflicting = make_span("a" * 32, "1" * 16, name="GET /other", otel_status="error")
    result = store.ingest(NormalizedBatch(spans=[conflicting]), BINDING, T0 + 1_000)
    assert [d.code for d in result.diagnostics] == ["conflicting_span"]
    assert result.trace_refs[0].revision == 1

    loaded = store.get_spans(result.trace_refs[0])
    assert loaded is not None
    assert len(loaded) == 1
    assert loaded[0].name == "GET /checkout"
    assert loaded[0].otel_status == "ok"


def test_rollback_on_write_failure(store: SpanStore, monkeypatch: pytest.MonkeyPatch):
    ok_ref = store.ingest(
        NormalizedBatch(spans=[make_span("a" * 32, "1" * 16)]), BINDING, T0
    ).trace_refs[0]

    def boom(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("injected write failure")

    monkeypatch.setattr(SpanStore, "_insert_span", boom)
    with pytest.raises(sqlite3.OperationalError):
        store.ingest(NormalizedBatch(spans=[make_span("b" * 32, "3" * 16)]), BINDING, T0)
    monkeypatch.undo()

    # Откат: неуспешный trace отсутствует, ранее записанный нетронут, стор работает.
    assert [ref.trace_id for ref, _ in store.list_traces("shop", 10)] == ["a" * 32]
    assert store.get_spans(ok_ref) is not None
    retry = store.ingest(NormalizedBatch(spans=[make_span("b" * 32, "3" * 16)]), BINDING, T0)
    assert store.get_spans(retry.trace_refs[0]) is not None


def test_restart_persistence(tmp_path: Path):
    db = tmp_path / "zerotrace.db"
    spans = [
        make_span("a" * 32, "1" * 16),
        make_span("a" * 32, "2" * 16, parent_span_id="1" * 16, start_ns=T0 + 100),
    ]
    store = SpanStore(db)
    ref = store.ingest(NormalizedBatch(spans=spans), BINDING, T0).trace_refs[0]
    store.close()

    reopened = SpanStore(db)
    loaded = reopened.get_spans(ref)
    assert loaded is not None
    assert {s.span_id: s for s in loaded} == {s.span_id: s for s in spans}
    meta = reopened.get_trace_meta(ref)
    assert meta is not None
    assert meta.first_received_at_ns == T0
    reopened.close()


def test_delete_trace_with_expected_revision(store: SpanStore):
    ref = store.ingest(NormalizedBatch(spans=[make_span("a" * 32, "1" * 16)]), BINDING, T0)
    ref = ref.trace_refs[0]

    assert store.delete_trace(ref, expected_generation=1, expected_revision=99) is False
    assert store.get_spans(ref) is not None

    assert store.delete_trace(ref, expected_generation=2, expected_revision=1) is False
    assert store.delete_trace(ref, expected_generation=1, expected_revision=1) is True
    assert store.get_trace_meta(ref) is None
    assert store.get_spans(ref) is None
    assert store.list_traces("shop", 10) == []


def test_list_traces_pagination(store: SpanStore):
    store.ingest(NormalizedBatch(spans=[make_span("a" * 32, "1" * 16)]), BINDING, T0)
    store.ingest(NormalizedBatch(spans=[make_span("b" * 32, "2" * 16)]), BINDING, T0 + 10)
    store.ingest(NormalizedBatch(spans=[make_span("c" * 32, "3" * 16)]), BINDING, T0 + 20)

    page1 = store.list_traces("shop", limit=2)
    assert [ref.trace_id for ref, _ in page1] == ["c" * 32, "b" * 32]
    page2 = store.list_traces("shop", limit=2, offset=2)
    assert [ref.trace_id for ref, _ in page2] == ["a" * 32]
    assert store.list_traces("other-env", limit=10) == []
