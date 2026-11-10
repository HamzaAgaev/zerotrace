"""OTLP trace ingestion endpoint (P1): token auth, decode, normalize, persist.

Pure handler over the repository store; the HTTP router (owned by A) maps
OtlpResponse to a real HTTP response. Success (200) is produced only after
the store transaction commits — a write failure yields 503, never a false ACK.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from contracts import SourceBinding
from contracts.p1 import IngestLimits
from ingest.decoder import MEDIA_JSON, MEDIA_PROTOBUF, IngestError, decode_otlp
from ingest.normalize import RedactionProfile, SourceProfile, normalize
from repository.store import SpanStore


class TokenRegistry(Protocol):
    def resolve(self, token: str | None) -> SourceBinding | None: ...


@dataclass(frozen=True)
class OtlpResponse:
    status: int
    body: bytes
    media_type: str


def _bearer(headers: Mapping[str, str]) -> str | None:
    auth = None
    for key, value in headers.items():
        if key.lower() == "authorization":
            auth = value
            break
    if auth is None:
        return None
    parts = auth.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip() or None
    return None


def _error_body(message: str, media_type: str) -> bytes:
    if media_type == MEDIA_PROTOBUF:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )

        response = ExportTraceServiceResponse()
        response.partial_success.rejected_spans = 0
        response.partial_success.error_message = message
        return response.SerializeToString()
    return json.dumps({"partialSuccess": {"rejectedSpans": 0, "errorMessage": message}}).encode(
        "utf-8"
    )


def handle_traces(
    store: SpanStore,
    token_registry: TokenRegistry,
    body: bytes,
    headers: Mapping[str, str],
    limits: IngestLimits,
    received_at_ns: int,
) -> OtlpResponse:
    content_type = ""
    content_encoding = None
    for key, value in headers.items():
        lowered = key.lower()
        if lowered == "content-type":
            content_type = value.split(";", 1)[0].strip().lower()
        elif lowered == "content-encoding":
            content_encoding = value.strip().lower() or None

    media_type = MEDIA_PROTOBUF if content_type == MEDIA_PROTOBUF else MEDIA_JSON
    binding = token_registry.resolve(_bearer(headers))
    if binding is None:
        return OtlpResponse(
            status=401,
            body=_error_body("unknown or missing source token", media_type),
            media_type=media_type,
        )

    try:
        decoded = decode_otlp(body, content_type or MEDIA_JSON, content_encoding, limits)
        normalized = normalize(
            decoded,
            SourceProfile(
                source_id=binding.source_id,
                environment_id=binding.environment_id,
            ),
            RedactionProfile(),
        )
        store.ingest(normalized, binding, received_at_ns)
    except IngestError as exc:
        return OtlpResponse(
            status=exc.status_code,
            body=_error_body(exc.message, media_type),
            media_type=media_type,
        )
    except Exception as exc:  # write failure: no false ACK
        return OtlpResponse(
            status=503,
            body=_error_body(f"storage failure: {exc}", media_type),
            media_type=media_type,
        )

    if media_type == MEDIA_PROTOBUF:
        from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import (
            ExportTraceServiceResponse,
        )

        return OtlpResponse(
            status=200,
            body=ExportTraceServiceResponse().SerializeToString(),
            media_type=MEDIA_PROTOBUF,
        )
    return OtlpResponse(
        status=200,
        body=json.dumps({"partialSuccess": {}}).encode("utf-8"),
        media_type=MEDIA_JSON,
    )
