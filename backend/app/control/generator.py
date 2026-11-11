from __future__ import annotations

import re

from app.integrations.http_config import ProductError
from contracts import IngestLimits, TraceRef
from core import build_graph, derive_calls, extract_chain
from core.chain import ChainRefusal


def from_trace(store, raw: dict) -> tuple[dict, list[dict]]:
    try:
        ref = TraceRef.from_dict(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise ProductError(
            400, "invalid_trace_ref", "Нужна точная ссылка на ревизию трассы."
        ) from exc
    spans = store.get_spans(ref)
    if not spans:
        raise ProductError(404, "trace_unavailable", "Ревизия трассы недоступна.")
    meta = store.get_trace_meta(ref)
    graph = build_graph(
        spans,
        ref,
        meta.first_received_at_ns,
        meta.last_received_at_ns,
        IngestLimits(),
        assembly_state=meta.assembly_state,
    )
    model = derive_calls(graph, project_id=meta.project_id)
    chain = extract_chain(graph, model)
    if isinstance(chain, ChainRefusal):
        raise ProductError(
            422,
            chain.code,
            "Трасса не подходит для линейного генератора.",
            {"problems": chain.problems},
        )
    root = next(span for span in graph.spans if span.span_id == graph.roots[0])
    services = {}
    origins = []
    names = {}

    def local_id(name):
        if name not in names:
            cleaned = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-") or "service"
            names[name] = f"node-{len(names) + 1}-{cleaned[:35]}"
        return names[name]

    def operation(value):
        parts = value.split(" ", 1)
        if len(parts) == 2 and parts[0] in {
            "GET",
            "POST",
            "PUT",
            "PATCH",
            "DELETE",
            "HEAD",
            "OPTIONS",
        }:
            route = parts[1]
            # Only templates/paths are copied. Original external origins never
            # become destinations of generated laboratory requests.
            if route.startswith("/") and "?" not in route:
                return parts[0], route
        return "GET", "/request"

    root_method, root_path = operation(root.operation)
    root_name = local_id(root.service)
    entry = {"service_id": root_name, "route": f"{root_method} {root_path}"}
    route_by_service = {root_name: entry["route"]}
    for step in chain.calls:
        method, route = operation(step.operation)
        route_by_service[local_id(step.callee)] = f"{method} {route}"
    for name, route in route_by_service.items():
        services[name] = {
            "config_write": True,
            "routes": {route: {"work": {"kind": "fixed", "delay_ms": 5}, "calls": {}, "steps": []}},
        }
        origins.append({"service_id": name, "field": "work", "source": "default", "value_ms": 5})
    for index, step in enumerate(chain.calls):
        caller, callee = local_id(step.caller), local_id(step.callee)
        method, route = operation(step.operation)
        call_ref = f"call-{index + 1}"
        route_config = services[caller]["routes"][route_by_service[caller]]
        route_config["calls"][call_ref] = {
            "target": {"service_id": callee, "method": method, "route": route},
            "timeout_ms": 1000,
            "max_attempts": step.attempts,
            "backoff_ms": 50,
            "retry_on": ["5xx", "timeout"],
            "required": True,
        }
        route_config["steps"].append({"id": call_ref, "call": call_ref})
        origins.append(
            {
                "service_id": caller,
                "field": call_ref,
                "source": "observed",
                "attempts": step.attempts,
                "duration_ns": str(step.duration_ns),
            }
        )
        origins.append(
            {"service_id": caller, "field": "timeout_ms", "source": "default", "value_ms": 1000}
        )
    if chain.calls:
        last = chain.calls[-1]
        leaf = local_id(last.callee)
        work = min(60_000, max(0, last.duration_ns / 1_000_000))
        services[leaf]["routes"][route_by_service[leaf]]["work"] = {
            "kind": "fixed",
            "delay_ms": work,
        }
        origins.append(
            {
                "service_id": leaf,
                "field": "work",
                "source": "observed",
                "value_ms": work,
                "approximation": "interval includes transport overhead",
            }
        )
        # Measured response status can be replayed as the leaf's fixed fault.
        if last.http_status and last.http_status >= 400:
            services[leaf]["initial_fault"] = {
                "route": route_by_service[leaf],
                "error_rate": 1,
                "http_status": last.http_status,
                "delay_ms": 0,
                "failure_point": {"mode": "fixed", "boundary_id": "before-first"},
            }
        elif last.http_status:
            services[leaf]["routes"][route_by_service[leaf]]["response_status"] = last.http_status
    return {
        "schema_version": "1",
        "scenario_id": "generated",
        "scenario_revision": 1,
        "entrypoint": entry,
        "services": services,
    }, origins
