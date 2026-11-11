"""Scenario loading and validation shared by the stand supervisor and services.

The first laboratory scenario is JSON intentionally.  It keeps the service
image independent from a YAML parser and makes the runtime contract explicit.
"""

from __future__ import annotations

import copy
import json
import math
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
    if not isinstance(raw, dict) or not isinstance(raw.get("services"), dict):
        raise ScenarioError("scenario must contain a services object")
    services = raw["services"]
    for service_id, service in services.items():
        if not isinstance(service_id, str) or not isinstance(service, dict):
            raise ScenarioError("service identifiers and definitions must be objects")
        routes = service.get("routes")
        if not isinstance(routes, dict) or not routes:
            raise ScenarioError(f"service {service_id!r} has no routes")
        operation_policies: dict[tuple[str, str, str], tuple[Any, ...]] = {}
        call_definitions: dict[str, tuple[tuple[str, str, str], tuple[Any, ...]]] = {}
        for route, definition in routes.items():
            if not isinstance(route, str) or " " not in route or not isinstance(definition, dict):
                raise ScenarioError(f"invalid route {route!r} in {service_id!r}")
            response_status = definition.get("response_status", 200)
            if type(response_status) is not int or not 100 <= response_status <= 599:
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
                    and _finite_number(p50, 0.001, 3_600_000)
                    and _finite_number(p99, 0.001, 3_600_000)
                    and p99 >= p50
                )
            else:
                valid_work = False
            if not valid_work:
                raise ScenarioError(f"route {route!r} in {service_id!r} has invalid work")
            calls = definition.get("calls", {})
            if not isinstance(calls, dict):
                raise ScenarioError(f"route {route!r} calls must be an object")
            for call_ref, call in calls.items():
                if not isinstance(call_ref, str) or not isinstance(call, dict):
                    raise ScenarioError(f"invalid call in {service_id!r} {route!r}")
                target = call.get("target")
                if (
                    not isinstance(target, dict)
                    or not isinstance(target.get("service_id"), str)
                    or target.get("service_id") not in services
                    or not isinstance(target.get("method"), str)
                    or not isinstance(target.get("route"), str)
                ):
                    raise ScenarioError(f"call {call_ref!r} has an unknown target")
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
    return copy.deepcopy(raw)


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
