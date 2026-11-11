"""One configurable HTTP service for the ZeroTrace laboratory.

The module deliberately contains no analytics code.  A scenario describes a
service's incoming routes and its outbound policies, while this process owns
the current revision of that service's configuration.
"""

from __future__ import annotations

import argparse
import contextvars
import copy
import hashlib
import json
import math
import os
import random
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import requests
from flask import Flask, Response, jsonify, request

from .scenario import ScenarioError, load_scenario, route_boundaries, service_config

_OUTBOUND_METADATA: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "zerotrace_outbound_metadata", default=None
)
_MAX_DURATION_MS = 3_600_000


class ConfigConflict(Exception):
    """A requested mutation did not match the current config revision."""


class ConfigRejected(Exception):
    """A requested mutation is not part of the declared service contract."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite_number(value: Any, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return minimum <= value <= maximum
    return math.isfinite(value) and minimum <= value <= maximum


def _valid_retry_token(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    if value in {"timeout", "connect", "4xx", "5xx"}:
        return True
    return len(value) == 3 and value.isdigit() and 400 <= int(value) <= 599


def _keyed_random(*parts: object) -> random.Random:
    payload = "\x1f".join(str(part) for part in parts)
    return random.Random(int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:16]))


def _seed_from_headers() -> str:
    return request.headers.get("X-ZeroTrace-Seed") or request.headers.get("x-zerotrace-seed") or "runtime"


def _request_key() -> str:
    request_index = request.headers.get("X-ZeroTrace-Request-Index")
    if request_index is not None:
        # The load generator sends an index for every root request. Branch path
        # distinguishes fan-out calls, and attempt distinguishes retries of a
        # child request. This keeps simulation stable when concurrency changes.
        branch_path = request.headers.get("X-ZeroTrace-Branch-Path", "")
        attempt = request.headers.get("X-ZeroTrace-Attempt-Index", "1")
        return hashlib.sha256(
            f"{_seed_from_headers()}\x1f{request_index}\x1f{branch_path}\x1f{attempt}".encode()
        ).hexdigest()[:32]
    return request.headers.get("X-ZeroTrace-Request-Key") or uuid.uuid4().hex


def _otel_setup(service_id: str, endpoint: str | None) -> None:
    """Enable language instrumentation when the image includes OTel packages."""
    if not endpoint:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )
        from opentelemetry.instrumentation.flask import FlaskInstrumentor
        from opentelemetry.instrumentation.requests import RequestsInstrumentor
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError as error:  # image configuration error, not an application crash
        raise RuntimeError("OTel instrumentation packages are required when --endpoint is set") from error

    headers = _otlp_headers()
    token = os.getenv("ZEROTRACE_OTLP_TOKEN")
    if token:
        headers["Authorization"] = token
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_id}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=headers)))
    trace.set_tracer_provider(provider)

    def request_hook(span: Any, _request: Any) -> None:
        metadata = _OUTBOUND_METADATA.get()
        if span is None or metadata is None:
            return
        for key, value in metadata.items():
            span.set_attribute(key, value)

    # Management traffic is control-plane noise and must not enter service
    # topology or experiment samples. Keep both instrumentations aligned.
    excluded_urls = r".*/_(?:config(?:/.*)?|health|chaos|flush|shutdown)$"
    RequestsInstrumentor().instrument(request_hook=request_hook, excluded_urls=excluded_urls)
    FlaskInstrumentor().instrument_app(_ACTIVE_APP[0], excluded_urls=excluded_urls)


_ACTIVE_APP: list[Flask] = []


def _otlp_headers() -> dict[str, str]:
    """Parse the standard OTLP exporter headers environment variable."""
    result: dict[str, str] = {}
    for item in os.getenv("OTEL_EXPORTER_OTLP_TRACES_HEADERS", "").split(","):
        if not item.strip() or "=" not in item:
            continue
        key, value = item.split("=", 1)
        result[key.strip()] = unquote(value.strip())
    return result


class ServiceState:
    """Thread-safe, on-disk config and idempotency records for one service."""

    def __init__(self, state_dir: Path, service_id: str, initial_service: dict[str, Any], write_enabled: bool):
        self._path = state_dir / f"{service_id}.json"
        self._lock = threading.RLock()
        self.service_id = service_id
        self.write_enabled = write_enabled
        state_dir.mkdir(parents=True, exist_ok=True)
        if self._path.exists():
            self._state = json.loads(self._path.read_text(encoding="utf-8"))
        else:
            self._state = {
                "schema_version": "1",
                "service_id": service_id,
                "revision": "1",
                "config": initial_service,
                "fault_revision": 0,
                "faults": {},
                "operations": {},
            }
            self._save_locked()

    def _save_locked(self) -> None:
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(_canonical(self._state), encoding="utf-8")
        temporary.replace(self._path)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._state)

    def config_view(self) -> dict[str, Any]:
        with self._lock:
            config = copy.deepcopy(self._state["config"])
            routes: dict[str, Any] = {}
            calls: list[dict[str, Any]] = []
            calls_by_ref: dict[str, dict[str, Any]] = {}
            for route_name, route in config["routes"].items():
                route_copy = copy.deepcopy(route)
                route_copy["boundaries"] = route_boundaries(route_copy)
                route_copy["write_fields"] = ["work"] if self.write_enabled else []
                routes[route_name] = route_copy
                for call_ref, call in route_copy.get("calls", {}).items():
                    row = calls_by_ref.get(call_ref)
                    if row is None:
                        row = {
                            "route": route_name,
                            "routes": [],
                            "call_ref": call_ref,
                            "target": call["target"],
                            "timeout_ms": call["timeout_ms"],
                            "max_attempts": call["max_attempts"],
                            "backoff_ms": call["backoff_ms"],
                            "retry_on": call["retry_on"],
                            "write_fields": ["timeout_ms", "max_attempts", "backoff_ms", "retry_on"],
                        }
                        calls_by_ref[call_ref] = row
                        calls.append(row)
                    row["routes"].append(route_name)
            return {
                "schema_version": "1",
                "service_id": self.service_id,
                "revision": self._state["revision"],
                "routes": routes,
                "calls": calls,
                "fault_revision": self._state["fault_revision"],
                "faults": copy.deepcopy(self._state["faults"]),
                "write_enabled": self.write_enabled,
            }

    def operation(self, request_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._state["operations"].get(request_id)
            return copy.deepcopy(item) if item else None

    def patch(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ConfigRejected("request_id is required")
        fingerprint = _fingerprint(payload)
        with self._lock:
            previous = self._state["operations"].get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ConfigConflict("request_id was already used with different content")
                return copy.deepcopy(previous["result"])
            if not self.write_enabled:
                raise ConfigRejected("config writes are disabled for this service")
            if payload.get("expected_revision") != self._state["revision"]:
                raise ConfigConflict("stale config revision")
            set_values = payload.get("set")
            if not isinstance(set_values, dict) or not set_values:
                raise ConfigRejected("set must contain at least one field")
            config = copy.deepcopy(self._state["config"])
            if isinstance(payload.get("call_ref"), str):
                targets = self._calls(config, payload["call_ref"])
                candidate = copy.deepcopy(targets[0])
                self._apply_call_patch(candidate, set_values)
                for target in targets:
                    target.update(copy.deepcopy(set_values))
            elif isinstance(payload.get("route"), str):
                target = self._route(config, payload["route"])
                self._apply_route_patch(target, set_values)
            else:
                raise ConfigRejected("call_ref or route is required")
            changed = config != self._state["config"]
            self._state["config"] = config
            if changed:
                self._state["revision"] = str(int(self._state["revision"]) + 1)
            result = {"request_id": request_id, "status": "applied" if changed else "no_op", "new_revision": self._state["revision"]}
            self._state["operations"][request_id] = {"fingerprint": fingerprint, "result": result}
            self._save_locked()
            return copy.deepcopy(result)

    def set_fault(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = payload.get("request_id")
        rule = payload.get("rule")
        if not isinstance(request_id, str) or not request_id or not isinstance(rule, dict):
            raise ConfigRejected("request_id and rule are required")
        fingerprint = _fingerprint(payload)
        with self._lock:
            previous = self._state["operations"].get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ConfigConflict("request_id was already used with different content")
                return copy.deepcopy(previous["result"])
            expected = payload.get("expected_fault_revision")
            if expected is not None and expected != self._state["fault_revision"]:
                raise ConfigConflict("stale fault revision")
            route = rule.get("route")
            if not isinstance(route, str):
                raise ConfigRejected("fault route is required")
            route_definition = self._route(self._state["config"], route)
            self._validate_fault(route_definition, rule)
            self._state["faults"][route] = copy.deepcopy(rule)
            self._state["fault_revision"] += 1
            result = {"request_id": request_id, "status": "applied", "fault_revision": self._state["fault_revision"]}
            self._state["operations"][request_id] = {"fingerprint": fingerprint, "result": result}
            self._save_locked()
            return copy.deepcopy(result)

    def clear_faults(self, request_id: str, expected_fault_revision: int | None) -> dict[str, Any]:
        payload = {"request_id": request_id, "expected_fault_revision": expected_fault_revision, "clear": True}
        fingerprint = _fingerprint(payload)
        with self._lock:
            previous = self._state["operations"].get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ConfigConflict("request_id was already used with different content")
                return copy.deepcopy(previous["result"])
            if expected_fault_revision is not None and expected_fault_revision != self._state["fault_revision"]:
                raise ConfigConflict("stale fault revision")
            self._state["faults"] = {}
            self._state["fault_revision"] += 1
            result = {"request_id": request_id, "status": "applied", "fault_revision": self._state["fault_revision"]}
            self._state["operations"][request_id] = {"fingerprint": fingerprint, "result": result}
            self._save_locked()
            return copy.deepcopy(result)

    def clear_fault(self, request_id: str, route: str) -> dict[str, Any]:
        fingerprint = _fingerprint({"request_id": request_id, "route": route, "clear": True})
        with self._lock:
            previous = self._state["operations"].get(request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ConfigConflict("request_id was already used with different content")
                return copy.deepcopy(previous["result"])
            self._route(self._state["config"], route)
            self._state["faults"].pop(route, None)
            self._state["fault_revision"] += 1
            result = {"request_id": request_id, "status": "applied", "fault_revision": self._state["fault_revision"]}
            self._state["operations"][request_id] = {"fingerprint": fingerprint, "result": result}
            self._save_locked()
            return copy.deepcopy(result)

    @staticmethod
    def _route(config: dict[str, Any], route: str) -> dict[str, Any]:
        try:
            return config["routes"][route]
        except KeyError as error:
            raise ConfigRejected(f"unknown route {route!r}") from error

    @staticmethod
    def _calls(config: dict[str, Any], call_ref: str) -> list[dict[str, Any]]:
        calls = [
            route["calls"][call_ref]
            for route in config["routes"].values()
            if call_ref in route.get("calls", {})
        ]
        if not calls:
            raise ConfigRejected(f"unknown call_ref {call_ref!r}")
        return calls

    @staticmethod
    def _apply_call_patch(call: dict[str, Any], values: dict[str, Any]) -> None:
        allowed = {"timeout_ms", "max_attempts", "backoff_ms", "retry_on"}
        if set(values) - allowed:
            raise ConfigRejected("unsupported call field")
        if "timeout_ms" in values and not _finite_number(values["timeout_ms"], 0.001, _MAX_DURATION_MS):
            raise ConfigRejected("timeout_ms must be finite and from 0.001 to 3600000")
        if "max_attempts" in values and (type(values["max_attempts"]) is not int or not 1 <= values["max_attempts"] <= 10):
            raise ConfigRejected("max_attempts must be an integer from 1 to 10")
        if "backoff_ms" in values and not _finite_number(values["backoff_ms"], 0, _MAX_DURATION_MS):
            raise ConfigRejected("backoff_ms must be finite and from 0 to 3600000")
        if "retry_on" in values and (
            not isinstance(values["retry_on"], list)
            or not all(_valid_retry_token(value) for value in values["retry_on"])
        ):
            raise ConfigRejected("retry_on contains an unsupported condition")
        call.update(copy.deepcopy(values))

    @staticmethod
    def _apply_route_patch(route: dict[str, Any], values: dict[str, Any]) -> None:
        if set(values) != {"work"} or not isinstance(values["work"], dict):
            raise ConfigRejected("route patches may only set work")
        work = values["work"]
        if work.get("kind") == "fixed":
            if set(work) != {"kind", "delay_ms"} or not _finite_number(work.get("delay_ms"), 0, _MAX_DURATION_MS):
                raise ConfigRejected("fixed work requires finite delay_ms from 0 to 3600000")
        elif work.get("kind") == "lognormal":
            p50, p99 = work.get("p50_ms"), work.get("p99_ms")
            if (
                set(work) != {"kind", "p50_ms", "p99_ms"}
                or not _finite_number(p50, 0.001, _MAX_DURATION_MS)
                or not _finite_number(p99, 0.001, _MAX_DURATION_MS)
                or p99 < p50
            ):
                raise ConfigRejected("lognormal work requires 0 < p50_ms <= p99_ms")
        else:
            raise ConfigRejected("work kind must be fixed or lognormal")
        route["work"] = copy.deepcopy(work)

    @staticmethod
    def _validate_fault(route: dict[str, Any], rule: dict[str, Any]) -> None:
        if not _finite_number(rule.get("error_rate"), 0, 1):
            raise ConfigRejected("error_rate must be between 0 and 1")
        if type(rule.get("http_status")) is not int or not 400 <= rule["http_status"] <= 599:
            raise ConfigRejected("http_status must be an error status")
        if type(rule.get("delay_ms", 0)) is not int or not 0 <= rule.get("delay_ms", 0) <= _MAX_DURATION_MS:
            raise ConfigRejected("delay_ms must be an integer from 0 to 3600000")
        failure_point = rule.get("failure_point", {"mode": "fixed", "boundary_id": "before-first"})
        if not isinstance(failure_point, dict):
            raise ConfigRejected("failure_point must be an object")
        boundaries = {item["boundary_id"] for item in route_boundaries(route)}
        if failure_point.get("mode") == "fixed":
            boundary_id = failure_point.get("boundary_id")
            if not isinstance(boundary_id, str):
                raise ConfigRejected("fixed failure_point requires a boundary_id")
            requested = {boundary_id}
        elif failure_point.get("mode") == "random":
            boundary_ids = failure_point.get("boundary_ids")
            if not isinstance(boundary_ids, list) or not all(isinstance(item, str) for item in boundary_ids):
                raise ConfigRejected("random failure_point requires boundary_ids")
            requested = set(boundary_ids)
        else:
            raise ConfigRejected("failure_point mode must be fixed or random")
        if not requested or not requested <= boundaries:
            raise ConfigRejected("failure_point contains an unknown boundary")


class ServiceRuntime:
    def __init__(self, state: ServiceState, endpoints: dict[str, str]):
        self.state = state
        self.endpoints = endpoints
        self.activity_lock = threading.Lock()
        self.active_requests = 0

    def handle_route(self, route_name: str) -> int:
        snapshot = self.state.snapshot()
        route = snapshot["config"]["routes"][route_name]
        request_key = _request_key()
        seed = _seed_from_headers()
        self._annotate_server_span(request_key, seed)
        self._sleep_work(route["work"], seed, request_key, route_name)
        fault = snapshot["faults"].get(route_name)
        selected_boundary = self._selected_boundary(fault, route, seed, request_key, route_name)
        if selected_boundary == "before-first":
            return self._fault_status(fault)
        for step in route.get("steps", []):
            call_refs = step.get("parallel") or [step["call"]]
            results = self._run_group(route["calls"], call_refs, seed, request_key)
            mandatory = [
                results[ref]
                for ref in call_refs
                if route["calls"][ref].get("required", True)
                and not 200 <= results[ref] < 300
            ]
            if mandatory:
                return mandatory[0]
            if selected_boundary == f"after-{step.get('id')}":
                return self._fault_status(fault)
        return route.get("response_status", 200)

    def _run_group(self, calls: dict[str, dict[str, Any]], call_refs: list[str], seed: str, request_key: str) -> dict[str, int]:
        if len(call_refs) == 1:
            ref = call_refs[0]
            return {ref: self._call(calls[ref], ref, seed, request_key)}
        contexts = [contextvars.copy_context() for _ in call_refs]
        with ThreadPoolExecutor(max_workers=len(call_refs)) as executor:
            futures = {
                ref: executor.submit(context.run, self._call, calls[ref], ref, seed, request_key)
                for ref, context in zip(call_refs, contexts, strict=True)
            }
            return {ref: future.result() for ref, future in futures.items()}

    def _call(self, call: dict[str, Any], call_ref: str, seed: str, request_key: str) -> int:
        target = call["target"]
        endpoint = self.endpoints.get(target["service_id"], f"http://{target['service_id']}:8080")
        url = endpoint.rstrip("/") + target["route"]
        logical_call_id = hashlib.sha256(f"{request_key}:{self.state.service_id}:{call_ref}".encode()).hexdigest()[:24]
        attempts = call["max_attempts"]
        last_status = 502
        transport_reason: str | None = None
        for index in range(1, attempts + 1):
            headers = {
                "X-ZeroTrace-Seed": seed,
                "X-ZeroTrace-Request-Key": request_key,
                "X-ZeroTrace-Logical-Call-Id": logical_call_id,
                "X-ZeroTrace-Attempt-Index": str(index),
            }
            for source, target_header in (
                ("X-ZeroTrace-Run-Id", "X-ZeroTrace-Run-Id"),
                ("X-ZeroTrace-Probe", "X-ZeroTrace-Probe"),
                ("X-ZeroTrace-Request-Index", "X-ZeroTrace-Request-Index"),
            ):
                if request.headers.get(source):
                    headers[target_header] = request.headers[source]
            parent_branch = request.headers.get("X-ZeroTrace-Branch-Path", "")
            headers["X-ZeroTrace-Branch-Path"] = "/".join(filter(None, (parent_branch, f"{call_ref}#{index}")))
            metadata = {
                "server.address": target["service_id"],
                "http.request.method": target["method"],
                "url.path": target["route"],
                "zerotrace.call_ref": call_ref,
                "zerotrace.logical_call_id": logical_call_id,
                "zerotrace.attempt_index": index,
                "http.request.resend_count": index - 1,
            }
            token = _OUTBOUND_METADATA.set(metadata)
            try:
                timeout = float(call["timeout_ms"]) / 1000
                response = requests.request(
                    target["method"], url, timeout=(timeout, timeout), headers=headers,
                    allow_redirects=False,
                )
                last_status = response.status_code
                transport_reason = None
            except requests.Timeout:
                last_status = 504
                transport_reason = "timeout"
            except requests.ConnectionError:
                last_status = 502
                transport_reason = "connect"
            except requests.RequestException:
                last_status = 502
                transport_reason = "request"
            finally:
                _OUTBOUND_METADATA.reset(token)
            if 200 <= last_status < 300:
                return last_status
            if index == attempts or not self._retry_allowed(last_status, call["retry_on"], transport_reason):
                return last_status
            delay = call["backoff_ms"] / 1000
            if delay:
                time.sleep(delay)
        return last_status

    @staticmethod
    def _retry_allowed(
        status: int, retry_on: list[str], transport_reason: str | None = None
    ) -> bool:
        tokens = set(retry_on)
        if transport_reason is not None:
            return transport_reason in {"timeout", "connect"} and transport_reason in tokens
        return (("5xx" in tokens and 500 <= status <= 599)
                or ("4xx" in tokens and 400 <= status <= 499)
                or str(status) in tokens)

    def _sleep_work(self, work: dict[str, Any], seed: str, request_key: str, route_name: str) -> None:
        if work["kind"] == "fixed":
            delay = work["delay_ms"]
        else:
            rng = _keyed_random(seed, request_key, self.state.service_id, route_name, "work")
            p50, p99 = float(work["p50_ms"]), float(work["p99_ms"])
            sigma = (math.log(max(p99, p50)) - math.log(p50)) / 2.326347874
            delay = math.exp(math.log(p50) + sigma * rng.normalvariate(0, 1))
        if delay:
            time.sleep(delay / 1000)

    def _selected_boundary(self, fault: dict[str, Any] | None, route: dict[str, Any], seed: str, request_key: str, route_name: str) -> str | None:
        if not fault:
            return None
        rng = _keyed_random(seed, request_key, self.state.service_id, route_name, "fault")
        if rng.random() >= fault["error_rate"]:
            return None
        point = fault.get("failure_point", {"mode": "fixed", "boundary_id": "before-first"})
        if point["mode"] == "fixed":
            return point["boundary_id"]
        return rng.choice(point["boundary_ids"])

    @staticmethod
    def _fault_status(fault: dict[str, Any] | None) -> int:
        assert fault is not None
        if fault.get("delay_ms", 0):
            time.sleep(fault["delay_ms"] / 1000)
        return fault["http_status"]

    @staticmethod
    def _annotate_server_span(request_key: str, seed: str) -> None:
        try:
            from opentelemetry import trace
        except ImportError:
            return
        span = trace.get_current_span()
        if span is None:
            return
        span.set_attribute("zerotrace.request_key", request_key)
        span.set_attribute("zerotrace.seed", seed)
        if request.headers.get("X-ZeroTrace-Run-Id"):
            span.set_attribute("zerotrace.run_id", request.headers["X-ZeroTrace-Run-Id"])
        if request.headers.get("X-ZeroTrace-Probe"):
            span.set_attribute("zerotrace.probe", request.headers["X-ZeroTrace-Probe"].lower() == "true")


def create_app(scenario_path: str | Path, service_id: str, state_dir: str | Path, endpoints: dict[str, str] | None = None, write_enabled: bool | None = None) -> Flask:
    """Create a service app. Used by the process CLI and focused tests."""
    scenario = load_scenario(scenario_path)
    service = service_config(scenario, service_id)
    enabled = service.get("config_write", False) if write_enabled is None else write_enabled
    state = ServiceState(Path(state_dir), service_id, service, enabled)
    runtime = ServiceRuntime(state, endpoints or {})
    app = Flask(service_id)

    @app.get("/_health")
    def health() -> Response:
        with runtime.activity_lock:
            active_requests = runtime.active_requests
        return jsonify(
            {
                "service_id": service_id,
                "stand_id": os.getenv("ZEROTRACE_STAND_ID"),
                "ready": True,
                "active_requests": active_requests,
                "revision": state.snapshot()["revision"],
            }
        )

    @app.get("/_config")
    def config() -> Response:
        return jsonify(state.config_view())

    @app.patch("/_config")
    def patch_config() -> Response:
        try:
            return jsonify(state.patch(request.get_json(force=True)))
        except ConfigConflict as error:
            return jsonify({"error": {"code": "stale_revision", "message": str(error)}}), 409
        except ConfigRejected as error:
            status = 403 if "disabled" in str(error) else 400
            return jsonify({"error": {"code": "config_write_forbidden" if status == 403 else "invalid_config", "message": str(error)}}), status

    @app.get("/_config/operations/<request_id>")
    def config_operation(request_id: str) -> Response:
        result = state.operation(request_id)
        if not result:
            return jsonify({"error": {"code": "operation_not_found", "message": "operation is unknown"}}), 404
        return jsonify(result["result"])

    @app.put("/_chaos")
    def set_fault() -> Response:
        try:
            body = request.get_json(force=True)
            # The control server owns public fault revisions.  The service
            # persists an internal operation record so a retry is stable.
            body = {"request_id": body.get("request_id") or f"fault-{_fingerprint(body)[:24]}", "rule": body, **body}
            return jsonify(state.set_fault(body))
        except ConfigConflict as error:
            return jsonify({"error": {"code": "stale_fault_revision", "message": str(error)}}), 409
        except ConfigRejected as error:
            return jsonify({"error": {"code": "invalid_fault", "message": str(error)}}), 400

    @app.delete("/_chaos")
    def clear_faults() -> Response:
        body = request.get_json(silent=True) or {}
        try:
            route = body.get("route")
            if route:
                return jsonify(state.clear_fault(body.get("request_id") or f"fault-clear-{_fingerprint(body)[:24]}", route))
            return jsonify(state.clear_faults(body.get("request_id") or f"fault-clear-{_fingerprint(body)[:24]}", body.get("expected_fault_revision")))
        except ConfigConflict as error:
            return jsonify({"error": {"code": "stale_fault_revision", "message": str(error)}}), 409
        except ConfigRejected as error:
            return jsonify({"error": {"code": "invalid_fault", "message": str(error)}}), 400

    @app.post("/_flush")
    def flush() -> Response:
        try:
            from opentelemetry import trace

            trace.get_tracer_provider().force_flush()  # type: ignore[attr-defined]
        except (ImportError, AttributeError):
            pass
        return jsonify({"flushed": True})

    @app.post("/_shutdown")
    def shutdown() -> Response:
        callback = app.config.get("zerotrace_shutdown")
        if callback:
            threading.Thread(target=callback, daemon=True).start()
        return jsonify({"shutdown": True})

    for route_name in service["routes"]:
        method, path = route_name.split(" ", 1)

        def execute(route: str = route_name) -> Response:
            with runtime.activity_lock:
                runtime.active_requests += 1
            try:
                return Response(status=runtime.handle_route(route))
            finally:
                with runtime.activity_lock:
                    runtime.active_requests -= 1

        app.add_url_rule(path, endpoint=f"route_{route_name}", view_func=execute, methods=[method])
    return app


def _parse_endpoints() -> dict[str, str]:
    raw = os.getenv("ZEROTRACE_SERVICE_ENDPOINTS", "{}")
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ScenarioError("ZEROTRACE_SERVICE_ENDPOINTS must be JSON") from error
    if not isinstance(parsed, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in parsed.items()):
        raise ScenarioError("ZEROTRACE_SERVICE_ENDPOINTS must map service ids to URLs")
    return parsed


def _read_token(path: str | None) -> str | None:
    if not path:
        return os.getenv("ZEROTRACE_OTLP_TOKEN")
    token = Path(path).read_text(encoding="utf-8").strip()
    if not token:
        raise ScenarioError("OTLP token file is empty")
    return token if token.lower().startswith("bearer ") else f"Bearer {token}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one ZeroTrace laboratory service")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--service-id", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--endpoint", default=os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"))
    parser.add_argument("--config-write", choices=["true", "false"], default=None)
    parser.add_argument("--urls", help="JSON file mapping service identifiers to base URLs")
    parser.add_argument("--token-file")
    parser.add_argument("--lab", action="store_true", help="enable config writes for all laboratory services")
    args = parser.parse_args()
    endpoints = _parse_endpoints()
    if args.urls:
        endpoints = json.loads(Path(args.urls).read_text(encoding="utf-8"))
    if args.token_file:
        os.environ["ZEROTRACE_OTLP_TOKEN"] = _read_token(args.token_file) or ""
    elif args.lab:
        default_token = Path(f"/tokens/sandbox/{args.service_id}")
        if default_token.exists():
            os.environ["ZEROTRACE_OTLP_TOKEN"] = _read_token(str(default_token)) or ""
    app = create_app(args.scenario, args.service_id, args.state_dir, endpoints, True if args.lab else None if args.config_write is None else args.config_write == "true")
    _ACTIVE_APP[:] = [app]
    _otel_setup(args.service_id, args.endpoint)
    from werkzeug.serving import make_server

    server = make_server(args.host, args.port, app, threaded=True)
    app.config["zerotrace_shutdown"] = server.shutdown
    server.serve_forever()


if __name__ == "__main__":
    main()
