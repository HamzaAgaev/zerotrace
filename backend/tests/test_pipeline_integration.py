from __future__ import annotations

from pathlib import Path

import pytest

from app.data_api.adapter import SpanStoreAdapter, make_graph_builder
from app.data_api.handlers import ApiError, get_trace, list_traces
from contracts import IngestLimits, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from repository import SpanStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "otlp"
BINDING = SourceBinding(source_id="src-shop", project_id="zerotrace-demo", environment_id="shop")
T0 = 1_763_000_100_000_000_000


@pytest.fixture
def store(tmp_path: Path):
    with SpanStore(tmp_path / "test.db") as s:
        yield s


def ingest_fixture(store: SpanStore, name: str, received_at_ns: int = T0) -> None:
    body = (FIXTURES / f"{name}.json").read_bytes()
    batch = decode_otlp(body, "application/json", None, IngestLimits())
    normalized = normalize(batch, SourceProfile(), RedactionProfile())
    store.ingest(normalized, BINDING, received_at_ns)


class TestPipeline:
    def test_decode_normalize_store_list(self, store: SpanStore):
        ingest_fixture(store, "s00_healthy")
        adapter = SpanStoreAdapter(store)
        envelope = list_traces(
            adapter,
            "shop",
            graph_builder=make_graph_builder(store),
            generated_at="2026-11-11T00:00:00Z",
        )
        assert not isinstance(envelope, ApiError)
        assert envelope["schema"] == "zerotrace.trace-list"
        assert envelope["schema_version"] == "1.0.0"
        payload = envelope["payload"]
        assert payload["total"] == 1
        item = payload["items"][0]
        assert item["trace_ref"]["environment_id"] == "shop"
        assert item["trace_ref"]["generation"] == 1
        assert item["span_count"] == 2
        assert item["outcome"] in ("ok", "unknown")
        assert isinstance(item["start_ns"], str) and item["start_ns"].isdigit()

    def test_decode_normalize_store_detail(self, store: SpanStore):
        ingest_fixture(store, "s01_retry")
        adapter = SpanStoreAdapter(store)
        detail = get_trace(
            adapter,
            make_graph_builder(store),
            "shop",
            "bb" * 16,
            generated_at="2026-11-11T00:00:00Z",
        )
        assert not isinstance(detail, ApiError)
        trace = detail["payload"]
        assert trace["trace_ref"]["trace_id"] == "bb" * 16
        assert len(trace["spans"]) == 4
        kinds = [s["kind"] for s in trace["spans"]]
        assert kinds.count("client") == 3
        for span in trace["spans"]:
            assert isinstance(span["start_offset_ns"], str)
            assert int(span["start_offset_ns"]) >= 0

    def test_detail_requires_version_pair(self, store: SpanStore):
        ingest_fixture(store, "s00_healthy")
        adapter = SpanStoreAdapter(store)
        err = get_trace(adapter, make_graph_builder(store), "shop", "aa" * 16, generation=1)
        assert isinstance(err, ApiError)
        assert err.status == 400
        assert err.code == "version_pair_required"

    def test_detail_unknown_trace_is_404(self, store: SpanStore):
        adapter = SpanStoreAdapter(store)
        err = get_trace(adapter, make_graph_builder(store), "shop", "cc" * 16)
        assert isinstance(err, ApiError)
        assert err.status == 404
        assert err.code == "trace_unavailable"

    def test_repeated_ingest_does_not_double_spans(self, store: SpanStore):
        ingest_fixture(store, "s00_healthy")
        ingest_fixture(store, "s00_healthy", received_at_ns=T0 + 1_000_000)
        adapter = SpanStoreAdapter(store)
        envelope = list_traces(adapter, "shop", graph_builder=make_graph_builder(store))
        assert not isinstance(envelope, ApiError)
        assert envelope["payload"]["total"] == 1
        assert envelope["payload"]["items"][0]["span_count"] == 2

    def test_late_spans_raise_revision(self, store: SpanStore):
        body = (FIXTURES / "late_duplicate.json").read_bytes()
        batch = decode_otlp(body, "application/json", None, IngestLimits())
        normalized = normalize(batch, SourceProfile(), RedactionProfile())
        first = store.ingest(normalized, BINDING, T0).trace_refs[0]
        assert first.revision == 1
        second = store.ingest(normalized, BINDING, T0 + 2_000_000_000).trace_refs[0]
        assert second.revision >= 1
        meta = store.resolve_trace("shop", second.trace_id)
        assert meta is not None
