"""Normalization: raw decoded OTLP spans -> NormalizedSpan (contracts.p2)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from contracts.p2 import Idempotency, NormalizedSpan, OtelStatus, SpanKind

from .decoder import DecodedBatch, RawSpan

KIND_MAP: dict[int, SpanKind] = {
    0: "unspecified",
    1: "internal",
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}

STATUS_MAP: dict[int, OtelStatus] = {
    0: "unset",
    1: "ok",
    2: "error",
}

DEFAULT_SECRET_KEY_SUBSTRINGS = (
    "authorization",
    "cookie",
    "token",
    "password",
    "passwd",
    "secret",
    "api-key",
    "api_key",
)

_HTTP_METHOD_KEYS = ("http.request.method", "http.method")
_HTTP_STATUS_KEYS = ("http.response.status_code", "http.status_code")
_HTTP_URL_KEYS = ("url.full", "http.url")
_HTTP_PATH_KEYS = ("url.path", "http.target")
_TARGET_KEYS = ("server.address", "net.peer.name", "net.peer.ip", "http.host")
_IDEMPOTENCY_KEYS = ("idempotency.key", "idempotency-key")

_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_HEX_ID_RE = re.compile(r"[0-9a-fA-F]{8,}")
_LONG_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{24,}")


@dataclass(frozen=True)
class SourceProfile:
    source_id: str = "unknown"
    environment_id: str | None = None
    clock_domain: str | None = None
    default_service_name: str = "unknown"
    default_namespace: str = "default"
    default_environment: str | None = None


@dataclass(frozen=True)
class RedactionProfile:
    placeholder: str = "{id}"
    redact_query: bool = True
    secret_key_substrings: tuple[str, ...] = DEFAULT_SECRET_KEY_SUBSTRINGS


@dataclass(frozen=True)
class ResourceInfo:
    service_name: str
    namespace: str | None = None
    environment: str | None = None
    instance_id: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class NormalizedBatch:
    spans: list[NormalizedSpan]
    resources: list[ResourceInfo] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)
    run_ids: dict[str, str] = field(default_factory=dict)
    probe_trace_ids: set[str] = field(default_factory=set)


_RUN_ID_KEYS = ("zerotrace.run_id", "run_id")
_PROBE_KEYS = ("zerotrace.probe", "lab.probe")


def normalize(
    batch: DecodedBatch,
    source_profile: SourceProfile,
    redaction_profile: RedactionProfile,
) -> NormalizedBatch:
    spans: list[NormalizedSpan] = []
    resources: list[ResourceInfo] = []
    batch_diagnostics: list[str] = []
    run_ids: dict[str, str] = {}
    probe_trace_ids: set[str] = set()
    for rs in batch.resource_spans:
        resource_attrs, resource_notes = _redact_attributes(
            rs.resource_attributes, redaction_profile
        )
        batch_diagnostics.extend(resource_notes)
        info = _resource_info(resource_attrs, source_profile)
        if "service.name" not in resource_attrs:
            batch_diagnostics.append(
                f"missing resource attribute service.name; using {info.service_name!r}"
            )
        resources.append(info)
        for raw in rs.spans:
            spans.append(
                _normalize_span(raw, resource_attrs, info, source_profile, redaction_profile)
            )
            _collect_run_markers(raw, run_ids, probe_trace_ids, batch_diagnostics)
    return NormalizedBatch(
        spans=spans,
        resources=resources,
        diagnostics=batch_diagnostics,
        run_ids=run_ids,
        probe_trace_ids=probe_trace_ids,
    )


def _collect_run_markers(
    raw: RawSpan,
    run_ids: dict[str, str],
    probe_trace_ids: set[str],
    diagnostics: list[str],
) -> None:
    for key in _RUN_ID_KEYS:
        value = raw.attributes.get(key)
        if value is None:
            continue
        run_id = str(value)
        existing = run_ids.get(raw.trace_id)
        if existing is None:
            run_ids[raw.trace_id] = run_id
            diagnostics.append(f"run_id marker {key} for trace {raw.trace_id}")
        elif existing != run_id:
            diagnostics.append(
                f"conflicting run_id for trace {raw.trace_id}: "
                f"keeping {existing!r}, ignoring {run_id!r} from {key}"
            )
        break
    for key in _PROBE_KEYS:
        value = raw.attributes.get(key)
        if value is not None and str(value).lower() == "true":
            probe_trace_ids.add(raw.trace_id)
            diagnostics.append(f"probe trace {raw.trace_id} marked by {key}")
            break


def _redact_attributes(
    attrs: dict[str, Any], profile: RedactionProfile
) -> tuple[dict[str, Any], list[str]]:
    kept: dict[str, Any] = {}
    notes: list[str] = []
    for key, value in attrs.items():
        lowered = key.lower()
        if any(sub in lowered for sub in profile.secret_key_substrings):
            notes.append(f"redacted_attribute:{key}")
            continue
        kept[key] = value
    return kept, notes


def _resource_info(attrs: dict[str, Any], profile: SourceProfile) -> ResourceInfo:
    service = _as_str(attrs.get("service.name")) or profile.default_service_name
    namespace = _as_str(attrs.get("service.namespace")) or profile.default_namespace
    environment = (
        _as_str(attrs.get("deployment.environment"))
        or _as_str(attrs.get("deployment.environment.name"))
        or profile.default_environment
    )
    return ResourceInfo(
        service_name=service,
        namespace=namespace,
        environment=environment,
        instance_id=_as_str(attrs.get("service.instance.id")),
        version=_as_str(attrs.get("service.version")),
    )


def _normalize_span(
    raw: RawSpan,
    resource_attrs: dict[str, Any],
    info: ResourceInfo,
    source_profile: SourceProfile,
    redaction_profile: RedactionProfile,
) -> NormalizedSpan:
    attrs, notes = _redact_attributes(raw.attributes, redaction_profile)
    diagnostics = list(notes)

    kind = KIND_MAP.get(raw.kind)
    if kind is None:
        diagnostics.append(f"unknown span kind value {raw.kind}; treated as unspecified")
        kind = "unspecified"
    otel_status = STATUS_MAP.get(raw.status_code)
    if otel_status is None:
        diagnostics.append(f"unknown status code {raw.status_code}; treated as unset")
        otel_status = "unset"

    method = _first_str(attrs, _HTTP_METHOD_KEYS)
    if method is not None:
        method = method.upper()
    route = _as_str(attrs.get("http.route"))
    url_path = _extract_path(attrs, redaction_profile, diagnostics)
    if route is None and url_path is not None:
        route, route_notes = _redact_path_ids(url_path, redaction_profile)
        diagnostics.extend(route_notes)

    operation, operation_derived = _derive_operation(raw, attrs, method, route)
    http_status = _first_int(attrs, _HTTP_STATUS_KEYS)
    grpc_status = _as_int(attrs.get("rpc.grpc.status_code"))
    target = _derive_target(attrs, kind)
    error_type = _as_str(attrs.get("error.type"))
    resend_count = _as_int(attrs.get("http.request.resend_count"))
    idempotency: Idempotency = (
        "present" if any(key in attrs for key in _IDEMPOTENCY_KEYS) else "unknown"
    )
    async_markers: list[str] = []
    messaging = _as_str(attrs.get("messaging.system"))
    if messaging is not None:
        async_markers.append(f"messaging:{messaging}")

    if kind == "client" and method is not None and http_status is None:
        diagnostics.append("http client span without http status; http_status left unknown")

    field_coverage = {
        "service.name": "service.name" in resource_attrs,
        "operation": operation_derived,
        "kind": kind != "unspecified",
        "parent": raw.parent_span_id is not None,
        "http_status": http_status is not None,
        "target": target is not None,
    }

    return NormalizedSpan(
        trace_id=raw.trace_id,
        span_id=raw.span_id,
        parent_span_id=raw.parent_span_id,
        service=info.service_name,
        instance_id=info.instance_id,
        service_version=info.version,
        namespace=info.namespace,
        name=raw.name,
        kind=kind,
        start_ns=raw.start_ns,
        end_ns=raw.end_ns,
        duration_ns=raw.end_ns - raw.start_ns,
        clock_domain=source_profile.clock_domain,
        operation=operation,
        target=target,
        otel_status=otel_status,
        http_status=http_status,
        grpc_status=grpc_status,
        error_type=error_type,
        resend_count=resend_count,
        idempotency=idempotency,
        links_present=raw.links_count > 0,
        async_markers=async_markers,
        field_coverage=field_coverage,
        diagnostics=diagnostics,
    )


def _derive_operation(
    raw: RawSpan,
    attrs: dict[str, Any],
    method: str | None,
    route: str | None,
) -> tuple[str, bool]:
    if method is not None and route is not None:
        return f"{method} {route}", True
    if route is not None:
        return route, True
    rpc_system = _as_str(attrs.get("rpc.system"))
    rpc_service = _as_str(attrs.get("rpc.service"))
    rpc_method = _as_str(attrs.get("rpc.method"))
    if rpc_system is not None and (rpc_service or rpc_method):
        parts = [p for p in (rpc_service, rpc_method) if p]
        return "/".join(parts), True
    return raw.name, False


def _derive_target(attrs: dict[str, Any], kind: SpanKind) -> str | None:
    db_system = _as_str(attrs.get("db.system"))
    if db_system is not None:
        return db_system
    if kind in ("client", "producer", "consumer"):
        messaging = _as_str(attrs.get("messaging.system"))
        if messaging is not None:
            return messaging
    return _first_str(attrs, _TARGET_KEYS)


def _extract_path(
    attrs: dict[str, Any], profile: RedactionProfile, diagnostics: list[str]
) -> str | None:
    path = _first_str(attrs, _HTTP_PATH_KEYS)
    if path is not None:
        if profile.redact_query and "?" in path:
            path = path.split("?", 1)[0]
            diagnostics.append("query string dropped from http.target")
        return path or None
    url = _first_str(attrs, _HTTP_URL_KEYS)
    if url is None:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.query and profile.redact_query:
        diagnostics.append("query string dropped from url")
    return parts.path or None


def _redact_path_ids(path: str, profile: RedactionProfile) -> tuple[str, list[str]]:
    notes: list[str] = []
    segments = path.split("/")
    redacted = False
    for i, segment in enumerate(segments):
        if _looks_like_id(segment):
            segments[i] = profile.placeholder
            redacted = True
    if redacted:
        notes.append(f"path id segments replaced with {profile.placeholder!r}")
    return "/".join(segments), notes


def _looks_like_id(segment: str) -> bool:
    if not segment:
        return False
    if segment.isdigit():
        return True
    if _UUID_RE.fullmatch(segment):
        return True
    if _HEX_ID_RE.fullmatch(segment):
        return True
    return bool(_LONG_TOKEN_RE.fullmatch(segment)) and any(c.isdigit() for c in segment)


def _as_str(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _first_str(attrs: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = _as_str(attrs.get(key))
        if value is not None:
            return value
    return None


def _first_int(attrs: dict[str, Any], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = _as_int(attrs.get(key))
        if value is not None:
            return value
    return None
