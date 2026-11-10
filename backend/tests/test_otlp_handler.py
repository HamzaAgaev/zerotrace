from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.otlp_api import handle_traces
from contracts import IngestLimits, SourceBinding
from ingest.decoder import MEDIA_JSON, MEDIA_PROTOBUF
from repository import SpanStore

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "otlp"
BINDING = SourceBinding(source_id="src-shop", project_id="zerotrace-demo", environment_id="shop")
T0 = 1_763_000_200_000_000_000


class StaticRegistry:
    def __init__(self, binding: SourceBinding | None) -> None:
        self._binding = binding

    def resolve(self, token: str | None) -> SourceBinding | None:
        return self._binding


@pytest.fixture
def store(tmp_path: Path):
    with SpanStore(tmp_path / "otlp.db") as s:
        yield s


def auth_headers(content_type: str = MEDIA_JSON, encoding: str | None = None):
    headers = {"Authorization": "Bearer tok-shop", "Content-Type": content_type}
    if encoding is not None:
        headers["Content-Encoding"] = encoding
    return headers


class TestOtlpHandler:
    def test_json_ingest_success_after_commit(self, store: SpanStore):
        body = (FIXTURES / "s00_healthy.json").read_bytes()
        response = handle_traces(
            store, StaticRegistry(BINDING), body, auth_headers(), IngestLimits(), T0
        )
        assert response.status == 200
        assert response.media_type == MEDIA_JSON
        assert json.loads(response.body)["partialSuccess"] == {}
        listed = store.list_trace_metas("shop")
        assert len(listed) == 1
        assert listed[0].span_count == 2

    def test_protobuf_roundtrip(self, store: SpanStore):
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceRequest,
        )

        body = (FIXTURES / "s01_retry.json").read_bytes()
        json_request = json.loads(body)
        import base64

        def to_b64(value: str) -> str:
            return base64.b64encode(bytes.fromhex(value)).decode("ascii")

        for rs in json_request["resourceSpans"]:
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    sp["traceId"] = to_b64(sp["traceId"])
                    sp["spanId"] = to_b64(sp["spanId"])
                    if sp.get("parentSpanId"):
                        sp["parentSpanId"] = to_b64(sp["parentSpanId"])
        request = ExportTraceServiceRequest()
        from google.protobuf.json_format import ParseDict

        ParseDict(json_request, request)
        payload = request.SerializeToString()
        response = handle_traces(
            store,
            StaticRegistry(BINDING),
            payload,
            auth_headers(content_type=MEDIA_PROTOBUF),
            IngestLimits(),
            T0,
        )
        assert response.status == 200
        assert response.media_type == MEDIA_PROTOBUF
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )

        ack = ExportTraceServiceResponse.FromString(response.body)
        assert ack.partial_success.rejected_spans == 0
        assert store.list_trace_metas("shop")[0].span_count == 4

    def test_unknown_token_is_401(self, store: SpanStore):
        body = (FIXTURES / "s00_healthy.json").read_bytes()
        response = handle_traces(
            store, StaticRegistry(None), body, auth_headers(), IngestLimits(), T0
        )
        assert response.status == 401
        assert store.list_trace_metas("shop") == []

    def test_unsupported_media_type_is_415(self, store: SpanStore):
        body = b"not-otlp"
        response = handle_traces(
            store,
            StaticRegistry(BINDING),
            body,
            auth_headers(content_type="application/xml"),
            IngestLimits(),
            T0,
        )
        assert response.status == 415

    def test_body_limit_is_413(self, store: SpanStore):
        body = (FIXTURES / "s00_healthy.json").read_bytes()
        limits = IngestLimits(body_limit_bytes=len(body) - 1)
        response = handle_traces(store, StaticRegistry(BINDING), body, auth_headers(), limits, T0)
        assert response.status == 413
        assert store.list_trace_metas("shop") == []

    def test_store_failure_is_503_no_false_ack(self, store: SpanStore, monkeypatch):
        body = (FIXTURES / "s00_healthy.json").read_bytes()

        def boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(store, "ingest", boom)
        response = handle_traces(
            store, StaticRegistry(BINDING), body, auth_headers(), IngestLimits(), T0
        )
        assert response.status == 503

    def test_duplicate_delivery_no_double_count(self, store: SpanStore):
        body = (FIXTURES / "s00_healthy.json").read_bytes()
        for _ in range(2):
            response = handle_traces(
                store, StaticRegistry(BINDING), body, auth_headers(), IngestLimits(), T0
            )
            assert response.status == 200
        listed = store.list_trace_metas("shop")
        assert len(listed) == 1
        assert listed[0].span_count == 2
