from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Any

import pytest

from contracts import IngestLimits, TraceGraph
from ingest import (
    BadRequestError,
    IngestError,
    PayloadTooLargeError,
    RedactionProfile,
    SourceProfile,
    UnsupportedMediaTypeError,
    decode_otlp,
    normalize,
)
from ingest.decoder import DecodedBatch

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
OTLP_FIXTURES = FIXTURES / "otlp"
MODEL_FIXTURES = FIXTURES / "model"

LIMITS = IngestLimits()
PROFILE = SourceProfile(
    source_id="fixture",
    environment_id="shop",
    clock_domain="host-1",
    default_environment="shop",
)
REDACTION = RedactionProfile()


def decode_fixture(name: str) -> DecodedBatch:
    body = (OTLP_FIXTURES / name).read_bytes()
    return decode_otlp(body, "application/json", None, LIMITS)


def model_trace(name: str) -> TraceGraph:
    return TraceGraph.from_dict(json.loads((MODEL_FIXTURES / name / "trace.json").read_text()))


def pb_fixture_request(name: str) -> Any:
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    data = json.loads((OTLP_FIXTURES / name).read_text())
    request = ExportTraceServiceRequest()
    for rs in data["resourceSpans"]:
        resource_spans = request.resource_spans.add()
        for kv in rs["resource"]["attributes"]:
            attr = resource_spans.resource.attributes.add()
            attr.key = kv["key"]
            value = kv["value"]
            if "stringValue" in value:
                attr.value.string_value = value["stringValue"]
            elif "intValue" in value:
                attr.value.int_value = int(value["intValue"])
            elif "boolValue" in value:
                attr.value.bool_value = value["boolValue"]
            elif "doubleValue" in value:
                attr.value.double_value = value["doubleValue"]
            else:
                raise AssertionError(f"unsupported fixture value {value!r}")
        for ss in rs["scopeSpans"]:
            scope_spans = resource_spans.scope_spans.add()
            scope_spans.scope.name = ss["scope"].get("name", "")
            scope_spans.scope.version = ss["scope"].get("version", "")
            for sp in ss["spans"]:
                span = scope_spans.spans.add()
                span.trace_id = bytes.fromhex(sp["traceId"])
                span.span_id = bytes.fromhex(sp["spanId"])
                if sp.get("parentSpanId"):
                    span.parent_span_id = bytes.fromhex(sp["parentSpanId"])
                span.name = sp["name"]
                span.kind = sp["kind"]
                span.start_time_unix_nano = int(sp["startTimeUnixNano"])
                span.end_time_unix_nano = int(sp["endTimeUnixNano"])
                for kv in sp["attributes"]:
                    attr = span.attributes.add()
                    attr.key = kv["key"]
                    value = kv["value"]
                    if "stringValue" in value:
                        attr.value.string_value = value["stringValue"]
                    elif "intValue" in value:
                        attr.value.int_value = int(value["intValue"])
                    elif "boolValue" in value:
                        attr.value.bool_value = value["boolValue"]
                    else:
                        raise AssertionError(f"unsupported fixture value {value!r}")
                if "status" in sp:
                    span.status.code = sp["status"]["code"]
    return request


class TestDecodeRoundtrip:
    def test_protobuf_json_equivalence(self):
        json_batch = decode_fixture("s00_healthy.json")
        pb_body = pb_fixture_request("s00_healthy.json").SerializeToString()
        pb_batch = decode_otlp(pb_body, "application/x-protobuf", None, LIMITS)
        assert pb_batch.resource_spans == json_batch.resource_spans
        assert pb_batch.span_count == json_batch.span_count == 2

    def test_ns_roundtrip_json(self):
        batch = decode_fixture("s00_healthy.json")
        root = batch.spans[0]
        assert root.start_ns == 1_763_000_000_000_000_000
        assert root.end_ns == 1_763_000_000_500_000_000
        assert root.end_ns > 2**53

    def test_ns_roundtrip_protobuf(self):
        pb_body = pb_fixture_request("s01_retry.json").SerializeToString()
        batch = decode_otlp(pb_body, "application/x-protobuf", None, LIMITS)
        starts = [s.start_ns for s in batch.spans]
        assert starts[0] == 1_763_000_000_000_000_000
        assert starts[1] == 1_763_000_000_005_000_000

    def test_late_duplicate_deduped(self):
        batch = decode_fixture("late_duplicate.json")
        assert batch.span_count == 2
        assert batch.duplicate_span_count == 2


class TestDecodeValidation:
    def _payload(self, **span_overrides: Any) -> bytes:
        span = {
            "traceId": "aa" * 16,
            "spanId": "00" * 7 + "01",
            "name": "op",
            "kind": 2,
            "startTimeUnixNano": "1763000000000000000",
            "endTimeUnixNano": "1763000000001000000",
        }
        span.update(span_overrides)
        return json.dumps(
            {"resourceSpans": [{"resource": {}, "scopeSpans": [{"spans": [span]}]}]}
        ).encode()

    def _expect(self, status_code: int, **overrides: Any) -> None:
        with pytest.raises(IngestError) as exc_info:
            decode_otlp(self._payload(**overrides), "application/json", None, LIMITS)
        assert exc_info.value.status_code == status_code

    def test_non_hex_trace_id(self):
        self._expect(400, traceId="zz" * 16)

    def test_zero_trace_id(self):
        self._expect(400, traceId="00" * 16)

    def test_uppercase_trace_id(self):
        self._expect(400, traceId="AA" * 16)

    def test_short_span_id(self):
        self._expect(400, spanId="00" * 5 + "01")

    def test_zero_span_id(self):
        self._expect(400, spanId="00" * 8)

    def test_invalid_parent_span_id(self):
        self._expect(400, parentSpanId="not-a-span-id")

    def test_end_before_start(self):
        self._expect(
            400, startTimeUnixNano="1763000000001000000", endTimeUnixNano="1763000000000000000"
        )

    def test_zero_start_time(self):
        self._expect(400, startTimeUnixNano="0", endTimeUnixNano="1000")

    def test_bad_request_subclass(self):
        with pytest.raises(BadRequestError):
            decode_otlp(self._payload(traceId="x"), "application/json", None, LIMITS)


class TestDecodeLimits:
    def test_body_limit(self):
        body = (OTLP_FIXTURES / "s00_healthy.json").read_bytes()
        with pytest.raises(PayloadTooLargeError) as exc_info:
            decode_otlp(body, "application/json", None, IngestLimits(body_limit_bytes=16))
        assert exc_info.value.status_code == 413

    def test_span_count_limit(self):
        body = (OTLP_FIXTURES / "late_duplicate.json").read_bytes()
        with pytest.raises(BadRequestError):
            decode_otlp(body, "application/json", None, IngestLimits(max_spans_per_batch=1))

    def test_unsupported_media_type(self):
        with pytest.raises(UnsupportedMediaTypeError) as exc_info:
            decode_otlp(b"{}", "text/plain", None, LIMITS)
        assert exc_info.value.status_code == 415

    def test_unsupported_encoding(self):
        with pytest.raises(UnsupportedMediaTypeError) as exc_info:
            decode_otlp(b"{}", "application/json", "br", LIMITS)
        assert exc_info.value.status_code == 415

    def test_gzip_not_supported_by_default(self):
        body = gzip.compress((OTLP_FIXTURES / "s00_healthy.json").read_bytes())
        with pytest.raises(UnsupportedMediaTypeError) as exc_info:
            decode_otlp(body, "application/json", "gzip", LIMITS)
        assert exc_info.value.status_code == 415

    def test_gzip_supported(self):
        raw = (OTLP_FIXTURES / "s00_healthy.json").read_bytes()
        limits = IngestLimits(gzip_supported=True)
        batch = decode_otlp(gzip.compress(raw), "application/json", "gzip", limits)
        assert batch.span_count == 2

    def test_gzip_decompressed_limit(self):
        raw = (OTLP_FIXTURES / "s00_healthy.json").read_bytes()
        limits = IngestLimits(gzip_supported=True, max_decompressed_bytes=len(raw) - 1)
        with pytest.raises(PayloadTooLargeError) as exc_info:
            decode_otlp(gzip.compress(raw), "application/json", "gzip", limits)
        assert exc_info.value.status_code == 413

    def test_invalid_json(self):
        with pytest.raises(BadRequestError):
            decode_otlp(b"{not json", "application/json", None, LIMITS)

    def test_invalid_protobuf(self):
        with pytest.raises(BadRequestError):
            decode_otlp(b"\xff\xff\xff", "application/x-protobuf", None, LIMITS)


class TestNormalize:
    def test_s00_consistent_with_model(self):
        batch = decode_fixture("s00_healthy.json")
        normalized = normalize(batch, PROFILE, REDACTION)
        model = model_trace("s00_healthy")
        assert len(normalized.spans) == len(model.spans)
        by_id = {s.span_id: s for s in normalized.spans}
        for expected in model.spans:
            actual = by_id[expected.span_id]
            assert actual.trace_id == expected.trace_id
            assert actual.parent_span_id == expected.parent_span_id
            assert actual.start_ns == expected.start_ns
            assert actual.end_ns == expected.end_ns
            assert actual.duration_ns == expected.duration_ns
            assert actual.service == expected.service
            assert actual.kind == expected.kind
            assert actual.operation == expected.operation
            assert actual.http_status == expected.http_status
            assert actual.otel_status == expected.otel_status

    def test_s00_query_redacted(self):
        batch = decode_fixture("s00_healthy.json")
        normalized = normalize(batch, PROFILE, REDACTION)
        client = next(s for s in normalized.spans if s.kind == "client")
        assert client.operation == "POST /pay"
        assert "order_id" not in client.operation
        assert "12345" not in json.dumps(client.to_dict())
        assert client.target == "payment"
        assert any("query" in d for d in client.diagnostics)

    def test_s01_retry_consistent_with_model(self):
        batch = decode_fixture("s01_retry.json")
        normalized = normalize(batch, PROFILE, REDACTION)
        model = model_trace("s01_retry")
        by_id = {s.span_id: s for s in normalized.spans}
        assert set(by_id) == {s.span_id for s in model.spans}
        for expected in model.spans:
            actual = by_id[expected.span_id]
            assert actual.start_ns == expected.start_ns
            assert actual.duration_ns == expected.duration_ns
            assert actual.otel_status == "error"
            assert actual.http_status == 503
        clients = [s for s in normalized.spans if s.kind == "client"]
        clients.sort(key=lambda s: s.start_ns)
        assert [c.resend_count for c in clients] == [0, 1, 2]
        assert all(c.error_type == "http_5xx" for c in clients)
        gaps = [clients[i + 1].start_ns - clients[i].end_ns for i in range(len(clients) - 1)]
        assert gaps == [50_000_000, 50_000_000]

    def test_unknown_fields_are_none_not_zero(self):
        batch = decode_fixture("s00_healthy.json")
        normalized = normalize(batch, PROFILE, REDACTION)
        root = next(s for s in normalized.spans if s.kind == "server")
        assert root.parent_span_id is None
        assert root.grpc_status is None
        assert root.error_type is None
        assert root.resend_count is None
        assert root.idempotency == "unknown"

    def test_resource_info_extraction(self):
        batch = decode_fixture("s00_healthy.json")
        normalized = normalize(batch, PROFILE, REDACTION)
        assert len(normalized.resources) == 1
        info = normalized.resources[0]
        assert info.service_name == "orders"
        assert info.namespace == "default"
        assert info.environment == "shop"
        assert info.instance_id == "orders-7f9c9b6b5-x1"
        assert info.version == "1.4.2"
        assert all(s.service_version == "1.4.2" for s in normalized.spans)


class TestRedaction:
    def _normalize_raw(self, attributes: dict[str, Any]) -> Any:
        from ingest.decoder import RawResourceSpans, RawSpan

        raw = RawSpan(
            trace_id="cc" * 16,
            span_id="00" * 7 + "09",
            parent_span_id=None,
            name="POST /pay/12345",
            kind=3,
            start_ns=1_763_000_000_000_000_000,
            end_ns=1_763_000_000_100_000_000,
            attributes=attributes,
            status_code=0,
            status_message=None,
            links_count=0,
        )
        batch = DecodedBatch(
            resource_spans=[
                RawResourceSpans(resource_attributes={"service.name": "orders"}, spans=[raw])
            ],
            media_type="application/json",
            span_count=1,
        )
        return normalize(batch, PROFILE, REDACTION).spans[0]

    def test_path_id_placeholder(self):
        span = self._normalize_raw(
            {
                "http.request.method": "POST",
                "url.full": "https://payment/pay/12345?secret=topsecret",
            }
        )
        assert span.operation == "POST /pay/{id}"
        assert "12345" not in span.operation
        assert "topsecret" not in json.dumps(span.to_dict())
        assert any("placeholder" in d or "{id}" in d for d in span.diagnostics)

    def test_uuid_and_hex_segments_redacted(self):
        span = self._normalize_raw(
            {
                "http.request.method": "GET",
                "url.path": "/orders/550e8400-e29b-41d4-a716-446655440000/items/abcdef0123456789",
            }
        )
        assert span.operation == "GET /orders/{id}/items/{id}"

    def test_secret_attributes_dropped(self):
        span = self._normalize_raw(
            {
                "http.request.method": "GET",
                "url.path": "/checkout",
                "authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.secret",
                "x-api-key": "abcd1234",
                "http.response.status_code": 200,
            }
        )
        payload = json.dumps(span.to_dict())
        assert "eyJhbGciOiJIUzI1NiJ9" not in payload
        assert "abcd1234" not in payload
        assert "redacted_attribute:authorization" in span.diagnostics
        assert "redacted_attribute:x-api-key" in span.diagnostics
        assert span.http_status == 200

    def test_explicit_route_wins_over_url(self):
        span = self._normalize_raw(
            {
                "http.request.method": "GET",
                "http.route": "/orders/{order_id}",
                "url.full": "https://api.example/orders/98765?expand=items",
            }
        )
        assert span.operation == "GET /orders/{order_id}"

    def test_missing_service_name_fallback(self):
        from ingest.decoder import RawResourceSpans, RawSpan

        raw = RawSpan(
            trace_id="cc" * 16,
            span_id="00" * 7 + "09",
            parent_span_id=None,
            name="op",
            kind=1,
            start_ns=1_763_000_000_000_000_000,
            end_ns=1_763_000_000_000_100_000,
            attributes={},
            status_code=0,
            status_message=None,
            links_count=0,
        )
        batch = DecodedBatch(
            resource_spans=[RawResourceSpans(resource_attributes={}, spans=[raw])],
            media_type="application/json",
            span_count=1,
        )
        result = normalize(batch, PROFILE, REDACTION)
        assert result.spans[0].service == "unknown"
        assert result.spans[0].field_coverage["service.name"] is False
        assert any("service.name" in d for d in result.diagnostics)
