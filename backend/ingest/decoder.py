"""OTLP/HTTP decoder: protobuf and OTLP JSON payloads -> validated raw spans."""

from __future__ import annotations

import json
import re
import zlib
from dataclasses import dataclass, field
from typing import Any

from contracts.p1 import IngestLimits

MEDIA_PROTOBUF = "application/x-protobuf"
MEDIA_JSON = "application/json"

_TRACE_ID_RE = re.compile(r"[0-9a-f]{32}")
_SPAN_ID_RE = re.compile(r"[0-9a-f]{16}")

_KIND_NAME_TO_INT = {
    "SPAN_KIND_UNSPECIFIED": 0,
    "SPAN_KIND_INTERNAL": 1,
    "SPAN_KIND_SERVER": 2,
    "SPAN_KIND_CLIENT": 3,
    "SPAN_KIND_PRODUCER": 4,
    "SPAN_KIND_CONSUMER": 5,
}
_STATUS_NAME_TO_INT = {
    "STATUS_CODE_UNSET": 0,
    "STATUS_CODE_OK": 1,
    "STATUS_CODE_ERROR": 2,
}


class IngestError(Exception):
    status_code: int = 400

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class PayloadTooLargeError(IngestError):
    status_code = 413


class UnsupportedMediaTypeError(IngestError):
    status_code = 415


class BadRequestError(IngestError):
    status_code = 400


@dataclass(frozen=True)
class RawSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    kind: int
    start_ns: int
    end_ns: int
    attributes: dict[str, Any]
    status_code: int
    status_message: str | None
    links_count: int
    scope_name: str | None = None


@dataclass(frozen=True)
class RawResourceSpans:
    resource_attributes: dict[str, Any]
    spans: list[RawSpan] = field(default_factory=list)


@dataclass(frozen=True)
class DecodedBatch:
    resource_spans: list[RawResourceSpans]
    media_type: str
    span_count: int
    duplicate_span_count: int = 0

    @property
    def spans(self) -> list[RawSpan]:
        return [s for rs in self.resource_spans for s in rs.spans]


def decode_otlp(
    body: bytes,
    media_type: str,
    encoding: str | None,
    limits: IngestLimits,
) -> DecodedBatch:
    if len(body) > limits.body_limit_bytes:
        raise PayloadTooLargeError(
            f"request body {len(body)} bytes exceeds limit {limits.body_limit_bytes}"
        )
    data = _decompress(body, encoding, limits)
    base_media = media_type.split(";")[0].strip().lower()
    if base_media == MEDIA_PROTOBUF:
        resource_spans = _decode_protobuf(data)
    elif base_media == MEDIA_JSON:
        resource_spans = _decode_json(data)
    else:
        raise UnsupportedMediaTypeError(f"unsupported media type: {media_type!r}")
    resource_spans, span_count, duplicates = _dedupe(resource_spans, limits)
    return DecodedBatch(
        resource_spans=resource_spans,
        media_type=base_media,
        span_count=span_count,
        duplicate_span_count=duplicates,
    )


def _decompress(body: bytes, encoding: str | None, limits: IngestLimits) -> bytes:
    enc = (encoding or "identity").strip().lower()
    if enc in ("", "identity"):
        return body
    if enc == "gzip":
        if not limits.gzip_supported:
            raise UnsupportedMediaTypeError("gzip content-encoding is not supported")
        try:
            decomp = zlib.decompressobj(zlib.MAX_WBITS | 16)
            out = decomp.decompress(body, limits.max_decompressed_bytes + 1)
            remaining = limits.max_decompressed_bytes + 1 - len(out)
            if remaining > 0:
                out += decomp.flush(remaining)
        except zlib.error as exc:
            raise BadRequestError(f"invalid gzip payload: {exc}") from exc
        if len(out) > limits.max_decompressed_bytes or decomp.unconsumed_tail:
            raise PayloadTooLargeError(
                f"decompressed body exceeds limit {limits.max_decompressed_bytes} bytes"
            )
        return out
    raise UnsupportedMediaTypeError(f"unsupported content-encoding: {enc!r}")


def _validate_trace_id(value: str) -> str:
    if not _TRACE_ID_RE.fullmatch(value):
        raise BadRequestError(f"invalid trace_id: {value!r}")
    if int(value, 16) == 0:
        raise BadRequestError("trace_id must be non-zero")
    return value


def _validate_span_id(value: str) -> str:
    if not _SPAN_ID_RE.fullmatch(value):
        raise BadRequestError(f"invalid span_id: {value!r}")
    if int(value, 16) == 0:
        raise BadRequestError("span_id must be non-zero")
    return value


def _validate_times(start_ns: int, end_ns: int) -> None:
    if start_ns <= 0:
        raise BadRequestError(f"invalid start time: {start_ns}")
    if end_ns < start_ns:
        raise BadRequestError(f"span end {end_ns} is before start {start_ns}")


def _check_span(raw: RawSpan) -> RawSpan:
    _validate_trace_id(raw.trace_id)
    _validate_span_id(raw.span_id)
    if raw.parent_span_id is not None:
        _validate_span_id(raw.parent_span_id)
    _validate_times(raw.start_ns, raw.end_ns)
    return raw


def _dedupe(
    resource_spans: list[RawResourceSpans], limits: IngestLimits
) -> tuple[list[RawResourceSpans], int, int]:
    seen: set[tuple[str, str]] = set()
    out: list[RawResourceSpans] = []
    total = 0
    duplicates = 0
    for rs in resource_spans:
        kept: list[RawSpan] = []
        for span in rs.spans:
            key = (span.trace_id, span.span_id)
            if key in seen:
                duplicates += 1
                continue
            seen.add(key)
            kept.append(_check_span(span))
        total += len(kept)
        out.append(RawResourceSpans(resource_attributes=rs.resource_attributes, spans=kept))
    if total > limits.max_spans_per_batch:
        raise BadRequestError(f"span count {total} exceeds limit {limits.max_spans_per_batch}")
    return out, total, duplicates


def _decode_protobuf(data: bytes) -> list[RawResourceSpans]:
    from google.protobuf.message import DecodeError
    from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
        ExportTraceServiceRequest,
    )

    request = ExportTraceServiceRequest()
    try:
        request.ParseFromString(data)
    except DecodeError as exc:
        raise BadRequestError(f"invalid protobuf payload: {exc}") from exc

    out: list[RawResourceSpans] = []
    for rs in request.resource_spans:
        resource_attrs = {kv.key: _pb_any_value(kv.value) for kv in rs.resource.attributes}
        spans: list[RawSpan] = []
        for ss in rs.scope_spans:
            scope_name = ss.scope.name or None
            for sp in ss.spans:
                spans.append(_pb_span(sp, scope_name))
        out.append(RawResourceSpans(resource_attributes=resource_attrs, spans=spans))
    return out


def _pb_any_value(value: Any) -> Any:
    which = value.WhichOneof("value")
    if which is None:
        return None
    if which == "array_value":
        return [_pb_any_value(v) for v in value.array_value.values]
    if which == "kvlist_value":
        return {kv.key: _pb_any_value(kv.value) for kv in value.kvlist_value.values}
    if which == "bytes_value":
        return value.bytes_value.hex()
    return getattr(value, which)


def _pb_span(sp: Any, scope_name: str | None) -> RawSpan:
    trace_bytes = sp.trace_id
    if len(trace_bytes) != 16:
        raise BadRequestError(f"invalid trace_id length: {len(trace_bytes)} bytes")
    span_bytes = sp.span_id
    if len(span_bytes) != 8:
        raise BadRequestError(f"invalid span_id length: {len(span_bytes)} bytes")
    parent = sp.parent_span_id
    if parent and len(parent) != 8:
        raise BadRequestError(f"invalid parent_span_id length: {len(parent)} bytes")
    return RawSpan(
        trace_id=trace_bytes.hex(),
        span_id=span_bytes.hex(),
        parent_span_id=parent.hex() if parent else None,
        name=sp.name,
        kind=int(sp.kind),
        start_ns=int(sp.start_time_unix_nano),
        end_ns=int(sp.end_time_unix_nano),
        attributes={kv.key: _pb_any_value(kv.value) for kv in sp.attributes},
        status_code=int(sp.status.code),
        status_message=sp.status.message or None,
        links_count=len(sp.links),
        scope_name=scope_name,
    )


def _decode_json(data: bytes) -> list[RawResourceSpans]:
    try:
        payload = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BadRequestError(f"invalid JSON payload: {exc}") from exc
    if not isinstance(payload, dict):
        raise BadRequestError("OTLP JSON payload must be an object")
    resource_spans = _get(payload, "resourceSpans", "resource_spans", default=[])
    if not isinstance(resource_spans, list):
        raise BadRequestError("resourceSpans must be a list")

    out: list[RawResourceSpans] = []
    for rs in resource_spans:
        if not isinstance(rs, dict):
            raise BadRequestError("resourceSpans entries must be objects")
        resource = rs.get("resource") or {}
        resource_attrs = _json_attributes(resource.get("attributes") or [])
        spans: list[RawSpan] = []
        scope_spans = _get(rs, "scopeSpans", "scope_spans", default=[]) or []
        for ss in scope_spans:
            scope = ss.get("scope") or {}
            scope_name = scope.get("name") or None
            for sp in ss.get("spans") or []:
                spans.append(_json_span(sp, scope_name))
        out.append(RawResourceSpans(resource_attributes=resource_attrs, spans=spans))
    return out


def _get(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in d:
            return d[key]
    return default


def _json_attributes(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, list):
        raise BadRequestError("attributes must be a list")
    attrs: dict[str, Any] = {}
    for item in raw:
        if not isinstance(item, dict) or "key" not in item:
            raise BadRequestError("attribute entries must be objects with a key")
        attrs[str(item["key"])] = _json_any_value(item.get("value") or {})
    return attrs


def _json_any_value(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    for key, camel in (
        ("string_value", "stringValue"),
        ("bool_value", "boolValue"),
        ("double_value", "doubleValue"),
    ):
        v = _get(value, camel, key, default=None)
        if v is not None:
            return v
    int_v = _get(value, "intValue", "int_value", default=None)
    if int_v is not None:
        try:
            return int(int_v)
        except (TypeError, ValueError) as exc:
            raise BadRequestError(f"invalid intValue: {int_v!r}") from exc
    array = _get(value, "arrayValue", "array_value", default=None)
    if array is not None:
        return [_json_any_value(v) for v in array.get("values") or []]
    kvlist = _get(value, "kvlistValue", "kvlist_value", default=None)
    if kvlist is not None:
        return {
            kv["key"]: _json_any_value(kv.get("value") or {}) for kv in kvlist.get("values") or []
        }
    bytes_v = _get(value, "bytesValue", "bytes_value", default=None)
    if bytes_v is not None:
        return bytes_v
    return None


def _json_span(sp: Any, scope_name: str | None) -> RawSpan:
    if not isinstance(sp, dict):
        raise BadRequestError("span entries must be objects")
    trace_id = _get(sp, "traceId", "trace_id")
    span_id = _get(sp, "spanId", "span_id")
    if not isinstance(trace_id, str) or not isinstance(span_id, str):
        raise BadRequestError("span requires traceId and spanId strings")
    parent = _get(sp, "parentSpanId", "parent_span_id", default=None)
    if parent is not None and not isinstance(parent, str):
        raise BadRequestError("parentSpanId must be a string")
    kind = _json_enum(sp.get("kind", 0), _KIND_NAME_TO_INT, "span kind")
    status = sp.get("status") or {}
    status_code = _json_enum(status.get("code", 0), _STATUS_NAME_TO_INT, "status code")
    status_message = status.get("message") or None
    return RawSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent or None,
        name=str(sp.get("name") or ""),
        kind=kind,
        start_ns=_json_ns(
            _get(sp, "startTimeUnixNano", "start_time_unix_nano"), "startTimeUnixNano"
        ),
        end_ns=_json_ns(_get(sp, "endTimeUnixNano", "end_time_unix_nano"), "endTimeUnixNano"),
        attributes=_json_attributes(sp.get("attributes") or []),
        status_code=status_code,
        status_message=status_message,
        links_count=len(sp.get("links") or []),
        scope_name=scope_name,
    )


def _json_enum(value: Any, names: dict[str, int], label: str) -> int:
    if isinstance(value, bool):
        raise BadRequestError(f"invalid {label}: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value in names:
        return names[value]
    raise BadRequestError(f"invalid {label}: {value!r}")


def _json_ns(value: Any, label: str) -> int:
    if value is None:
        raise BadRequestError(f"missing {label}")
    if isinstance(value, bool):
        raise BadRequestError(f"invalid {label}: {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise BadRequestError(f"invalid {label}: {value!r}") from exc
    raise BadRequestError(f"invalid {label}: {value!r}")
