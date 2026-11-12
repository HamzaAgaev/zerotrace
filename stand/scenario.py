"""Scenario loading and validation shared by the stand supervisor and services.

The first laboratory scenario is JSON intentionally.  It keeps the service
image independent from a YAML parser and makes the runtime contract explicit.
"""

from __future__ import annotations

import hashlib
import copy
import json
import math
import re
from pathlib import Path
from typing import Any


class ScenarioError(ValueError):
    """Raised when a scenario cannot be executed safely."""


def load_scenario(path: str | Path) -> dict[str, Any]:
    """Load a laboratory scenario and validate references used at runtime."""
    scenario_path = Path(path)
    try:
        raw = json.loads(scenario_path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ScenarioError(f"scenario file does not exist: {scenario_path}") from error
    except json.JSONDecodeError as error:
        raise ScenarioError(f"scenario is not valid JSON: {error}") from error
    return validate_scenario(raw)


def validate_scenario(raw: Any, *, explicit: bool = False) -> dict[str, Any]:
    """Validate an explicit scenario before starting any service processes."""
    if not isinstance(raw, dict) or not isinstance(raw.get("services"), dict):
        raise ScenarioError("scenario must contain a services object")
    if explicit:
        from stand.schema_validation import check, SchemaError
        try: check(raw)
        except SchemaError as error:
            converted = ScenarioError(str(error)); converted.path = error.path
            raise converted from error
        _keys(raw, {"schema_version", "scenario_id", "scenario_revision", "entrypoint", "services"}, "scenario")
    services = raw["services"]
    if not 1 <= len(services) <= 32:
        raise ScenarioError("services: требуется от 1 до 32 сервисов")
    if raw.get("schema_version") != "1" or not isinstance(raw.get("scenario_id"), str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", raw["scenario_id"]):
        raise ScenarioError("Укажите schema_version: 1 (строка) и scenario_id из букв, цифр, _ или -")
    if type(raw.get("scenario_revision")) is not int or raw["scenario_revision"] < 1:
        raise ScenarioError("scenario_revision: требуется положительное целое число")
    entry = raw.get("entrypoint")
    if not isinstance(entry, dict) or not isinstance(entry.get("service_id"), str) or entry["service_id"] not in services:
        raise ScenarioError("entrypoint.service_id: неизвестный сервис")
    if not isinstance(entry.get("route"), str):
        raise ScenarioError("entrypoint.route: требуется строка METHOD /path")
    if explicit:
        _keys(entry, {"service_id", "route"}, "entrypoint")
    route_pattern = r"(?:GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS) /[^\s?#]*"
    graph = {}

    for service_id, service in services.items():
        if not isinstance(service_id, str) or not isinstance(service, dict):
            raise ScenarioError("service identifiers and definitions must be objects")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", service_id):
            raise ScenarioError("service_id: используйте буквы, цифры, _ или -")
        routes = service.get("routes")
        if not isinstance(routes, dict) or not routes:
            raise ScenarioError(f"service {service_id!r} has no routes")
        if len(routes) > 256:
            raise ScenarioError("Не более 256 эндпоинтов в сервисе")
        if explicit:
            _keys(service, {"config_write", "routes"}, service_id)
            if "config_write" in service and not isinstance(service["config_write"], bool):
                raise ScenarioError("config_write: требуется boolean")
        operation_policies: dict[tuple[str, str, str], tuple[Any, ...]] = {}
        call_definitions: dict[str, tuple[tuple[str, str, str], tuple[Any, ...]]] = {}
        for route, definition in routes.items():
            if not isinstance(route, str) or not re.fullmatch(route_pattern, route) or not isinstance(definition, dict):
                raise ScenarioError(f"invalid route {route!r} in {service_id!r}")
            path = route.split(" ", 1)[1]
            if path.startswith("/_") or "//" in path or "<" in path or ">" in path or "\\" in path:
                raise ScenarioError(f"{service_id} {route}: запрещённый путь")
            if explicit:
                _keys(definition, {"work", "response_status", "tail_ms", "calls", "steps", "fault"}, f"{service_id} {route}")
                if not {"work", "response_status", "calls", "steps"} <= set(definition):
                    raise ScenarioError(f"{service_id} {route}: явно укажите work, response_status, calls и steps")
            response_status = definition.get("response_status", 200)
            if type(response_status) is not int or not 200 <= response_status <= 599:
                raise ScenarioError(
                    f"route {route!r} in {service_id!r} has invalid response_status"
                )
            work = definition.get("work")
            if not isinstance(work, dict):
                raise ScenarioError(f"route {route!r} in {service_id!r} has invalid work")
            if work.get("kind") == "fixed":
                valid_work = set(work) == {"kind", "delay_ms"} and _finite_number(
                    work.get("delay_ms"), 0, 3_600_000
                )
            elif work.get("kind") == "lognormal":
                p50, p99 = work.get("p50_ms"), work.get("p99_ms")
                valid_work = (
                    set(work) == {"kind", "p50_ms", "p99_ms"}
                    and _finite_number(p50, 0, 3_600_000) and p50 > 0
                    and _finite_number(p99, 0, 3_600_000) and p99 > 0
                    and p99 >= p50
                )
            else:
                valid_work = False
            if not valid_work:
                raise ScenarioError(f"route {route!r} in {service_id!r} has invalid work")
            if not _finite_number(definition.get("tail_ms", 0), 0, 3_600_000):
                raise ScenarioError("invalid route tail duration")
            steps = definition.get("steps", [])
            if not isinstance(steps, list):
                raise ScenarioError("steps must be a list")
            calls = definition.get("calls", {})
            if not isinstance(calls, dict):
                raise ScenarioError(f"{service_id} {route}: calls должен быть объектом")
            if len(steps) > 256 or len(calls) > 256:
                raise ScenarioError("Не более 256 шагов и вызовов в эндпоинте")
            step_ids = set()
            used = set()
            for step in steps:
                if not isinstance(step, dict) or ("call" in step) == ("parallel" in step):
                    raise ScenarioError("Шаг должен содержать либо call, либо parallel")
                if explicit:
                    _keys(step, {"id", "call", "parallel", "delay_ms", "offsets_ms"}, "step")
                step_id = step.get("id")
                if not isinstance(step_id, str) or not step_id or step_id in step_ids:
                    raise ScenarioError("Каждому шагу нужен уникальный непустой id")
                step_ids.add(step_id)
                refs = step.get("parallel", [step.get("call")])
                if (not isinstance(refs, list) or not refs or len(refs) > 64
                        or any(not isinstance(ref, str) for ref in refs)
                        or len(set(refs)) != len(refs)
                        or any(ref not in definition.get("calls", {}) for ref in refs)
                        or not _finite_number(step.get("delay_ms", 0), 0, 3_600_000)):
                    raise ScenarioError("invalid step references or duration")
                used.update(refs)
                offsets = step.get("offsets_ms", {})
                if not isinstance(offsets, dict) or any(
                    key not in refs or not _finite_number(value, 0, 3_600_000)
                    for key, value in offsets.items()
                ):
                    raise ScenarioError("invalid parallel start offsets")
            if used != set(calls):
                raise ScenarioError(f"{service_id} {route}: каждый calls должен использоваться в steps")
            graph[(service_id, route)] = []
            for call_ref, call in calls.items():
                if not isinstance(call_ref, str) or not isinstance(call, dict):
                    raise ScenarioError(f"invalid call in {service_id!r} {route!r}")
                if explicit:
                    _keys(call, {"target", "timeout_ms", "max_attempts", "backoff_ms", "retry_on", "required"}, f"call {call_ref}")
                    if not isinstance(call.get("required"), bool):
                        raise ScenarioError(f"call {call_ref}: явно укажите required: true или false")
                target = call.get("target")
                if (
                    not isinstance(target, dict)
                    or not isinstance(target.get("service_id"), str)
                    or target.get("service_id") not in services
                    or not isinstance(target.get("method"), str)
                    or not isinstance(target.get("route"), str)
                ):
                    raise ScenarioError(f"call {call_ref!r} has an unknown target")
                if explicit:
                    _keys(target, {"service_id", "method", "route"}, f"target {call_ref}")
                target_route = f"{target['method']} {target['route']}"
                target_service = services[target["service_id"]]
                if not isinstance(target_service, dict) or target_route not in target_service.get("routes", {}):
                    raise ScenarioError(f"call {call_ref!r}: целевой эндпоинт не существует")
                graph[(service_id, route)].append((target["service_id"], target_route))
                if (
                    not _finite_number(call.get("timeout_ms"), 0.001, 3_600_000)
                    or type(call.get("max_attempts")) is not int
                    or not 1 <= call["max_attempts"] <= 10
                    or not _finite_number(call.get("backoff_ms"), 0, 3_600_000)
                ):
                    raise ScenarioError(f"call {call_ref!r} has invalid retry settings")
                retry_on = call.get("retry_on")
                if not isinstance(retry_on, list) or not all(
                    isinstance(item, str)
                    and (
                        item in {"timeout", "connect", "4xx", "5xx"}
                        or (len(item) == 3 and item.isdigit() and 400 <= int(item) <= 599)
                    )
                    for item in retry_on
                ):
                    raise ScenarioError(f"call {call_ref!r} has invalid retry_on")
                if "required" in call and not isinstance(call["required"], bool):
                    raise ScenarioError(f"call {call_ref!r} required must be boolean")
                operation = (target["service_id"], target["method"], target["route"])
                policy = (
                    call.get("timeout_ms"),
                    call.get("max_attempts"),
                    call.get("backoff_ms"),
                    tuple(call.get("retry_on", []))
                    if isinstance(call.get("retry_on", []), list)
                    else call.get("retry_on"),
                )
                previous_policy = operation_policies.setdefault(operation, policy)
                if previous_policy != policy:
                    raise ScenarioError(
                        f"outgoing operation {operation!r} has conflicting retry policies"
                    )
                previous_call = call_definitions.setdefault(call_ref, (operation, policy))
                if previous_call != (operation, policy):
                    raise ScenarioError(
                        f"call_ref {call_ref!r} has conflicting shared definitions"
                    )
    if (entry["service_id"], entry.get("route")) not in graph:
        raise ScenarioError("entrypoint.route: неизвестный эндпоинт")
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ScenarioError("Циклические вызовы не поддерживаются. Повторы задаются через max_attempts или steps")
        if node in visited:
            return
        if len(visiting) >= 32:
            raise ScenarioError("Цепочка не должна быть глубже 32 эндпоинтов")
        visiting.add(node)
        for target in graph[node]:
            visit(target)
        visiting.remove(node)
        visited.add(node)
    if len(graph)>256:
        raise ScenarioError("Не более 256 эндпоинтов в сценарии")
    for node in graph:
        visit(node)
    for service_id, service in services.items():
        faults = [(route, definition.get("fault")) for route, definition in service["routes"].items()]
        if not explicit and service.get("initial_fault"):
            old = service["initial_fault"]
            faults.append((old.get("route"), {k:v for k,v in old.items() if k != "route"}))
        for route, fault in faults:
            if fault is None:
                continue
            if route not in service["routes"] or not isinstance(fault, dict):
                raise ScenarioError(f"{service_id} {route}: invalid fault")
            _keys(fault, {"error_rate", "http_status", "delay_ms", "failure_point"}, f"{service_id} {route} fault")
            if not _finite_number(fault.get("error_rate"), 0, 1) or type(fault.get("http_status")) is not int or not 400 <= fault["http_status"] <= 599 or type(fault.get("delay_ms")) is not int or not 0 <= fault["delay_ms"] <= 3_600_000:
                raise ScenarioError(f"{service_id} {route}: invalid fault parameters")
            point = fault.get("failure_point")
            if not isinstance(point, dict):
                raise ScenarioError(f"{service_id} {route}: invalid failure_point")
            _keys(point, {"mode", "boundary_id"} if point.get("mode")=="fixed" else {"mode", "boundary_ids"}, "failure_point")
            boundaries = {b["boundary_id"] for b in route_boundaries(service["routes"][route])}
            requested = [point.get("boundary_id")] if point.get("mode") == "fixed" else point.get("boundary_ids") if point.get("mode") == "random" else None
            if not isinstance(requested, list) or not requested or any(not isinstance(b, str) or b not in boundaries for b in requested) or len(set(requested)) != len(requested):
                raise ScenarioError(f"{service_id} {route}: invalid fault boundaries")
    return copy.deepcopy(raw)


def _keys(value, allowed, location):
    extra = set(value) - allowed
    if extra:
        raise ScenarioError(f"{location}: неизвестные поля: {', '.join(sorted(extra))}")


def _finite_number(value: Any, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if isinstance(value, int):
        return minimum <= value <= maximum
    return math.isfinite(value) and minimum <= value <= maximum


def service_config(scenario: dict[str, Any], service_id: str) -> dict[str, Any]:
    """Return the isolated initial configuration for one service."""
    try:
        service = scenario["services"][service_id]
    except KeyError as error:
        raise ScenarioError(f"scenario has no service {service_id!r}") from error
    return copy.deepcopy(service)


def route_boundaries(route: dict[str, Any]) -> list[dict[str, str]]:
    """Describe valid fault positions in execution order for a route."""
    boundaries = [{"boundary_id": "before-first", "label": "До исходящих вызовов"}]
    for index, step in enumerate(route.get("steps", []), start=1):
        step_id = str(step.get("id", f"step-{index}"))
        boundaries.append({"boundary_id": f"after-{step_id}", "label": f"После шага {step_id}"})
    return boundaries


def normalized_scenario(raw):
    def numbers(value):
        if isinstance(value, float) and value.is_integer(): return int(value)
        if isinstance(value, dict): return {k:numbers(v) for k,v in value.items()}
        if isinstance(value, list): return [numbers(v) for v in value]
        return value
    result = validate_scenario(numbers(raw), explicit=True)
    for service in result["services"].values():
        service.setdefault("config_write", True)
        for route in service["routes"].values():
            route.setdefault("tail_ms", 0)
            route.setdefault("fault", None)
            for step in route["steps"]:
                step.setdefault("delay_ms", 0)
                refs = step.get("parallel", [step.get("call")])
                step["offsets_ms"] = {ref: step.get("offsets_ms", {}).get(ref, 0) for ref in refs}
    return result


def scenario_digest(scenario):
    return hashlib.sha256(json.dumps(scenario, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
