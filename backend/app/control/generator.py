"""Build an isolated occurrence tree, never a representative linear path.

Repeated operations get separate local routes: their observed subtrees and
outcomes may differ. Each observed attempt is an explicit call, not an inferred
retry policy. Original operation and span IDs remain in the provenance table.
"""
from __future__ import annotations

import re
from collections import defaultdict

from app.integrations.http_config import ProductError
from contracts import IngestLimits, TraceRef
from core import build_graph, derive_calls


def from_trace(store, raw: dict) -> tuple[dict, list[dict]]:
    try:
        ref = TraceRef.from_dict(raw)
    except (KeyError, ValueError, TypeError) as exc:
        raise ProductError(400, "invalid_trace_ref", "Нужна точная ссылка на версию трейса.") from exc
    spans = store.get_spans(ref)
    if not spans:
        raise ProductError(404, "trace_unavailable", "Эта версия трейса недоступна.")
    meta = store.get_trace_meta(ref)
    graph = build_graph(spans, ref, meta.first_received_at_ns, meta.last_received_at_ns,
                        IngestLimits(), assembly_state=meta.assembly_state)
    return scenario_from_graph(graph, meta.project_id)


def scenario_from_graph(graph, project_id=None):
    def refuse(code, message, ids=()):
        raise ProductError(422, code, message, {"span_ids": list(ids)})

    spans = {s.span_id: s for s in graph.spans}
    if len(graph.roots) != 1 or spans[graph.roots[0]].kind != "server":
        refuse("ambiguous_root", "Для стенда нужен один начальный серверный спан.")
    broken = [q for q in graph.quality.issues if q.code in
              {"missing_parent", "cycle", "depth_limit", "conflicting_span", "source_loss"}]
    if broken:
        refuse("incomplete_trace", "В трейсе отсутствуют или противоречат друг другу связи спанов. Сборка без потери вызовов невозможна.",
               [sid for q in broken for sid in q.span_ids])
    unsupported = [s.span_id for s in spans.values() if s.kind in {"producer", "consumer"} or s.async_markers]
    if unsupported:
        refuse("unsupported_async", "Трейс содержит обмен сообщениями. Стенд пока воспроизводит HTTP-вызовы.", unsupported)
    if any(s.duration_ns < 0 or s.duration_ns > 3_600_000_000_000 for s in spans.values()):
        refuse("duration_limit", "Длительность спана выходит за допустимые пределы стенда.")
    model = derive_calls(graph, project_id=project_id)
    attempts = {a.attempt_id: a for a in model.attempts}
    groups = {sid: (c.call_id, i + 1) for c in model.calls
              for i, sid in enumerate(c.attempt_ids)}
    services, origins, names, locations = {}, [], {}, {}
    children = defaultdict(list)
    covered = set()

    def service_id(name):
        if name not in names:
            clean = re.sub(r"[^a-zA-Z0-9_-]", "-", name).strip("-") or "service"
            names[name] = f"node-{len(names)+1}-{clean[:35]}"
            services[names[name]] = {"config_write": True, "routes": {}}
        return names[name]

    def operation(span):
        parts = span.operation.split(" ", 1)
        if len(parts) == 2 and parts[0] in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
            path = parts[1].split("?", 1)[0]
            if path.startswith("/") and not path.startswith("//") and not path.startswith('/_') and '<' not in path:
                return parts[0], path
        return "GET", "/request"

    def register(sid, name, source):
        service = service_id(name)
        method, path = operation(source)
        routes = services[service]["routes"]
        if f"{method} {path}" in routes:
            path = f"/trace-call/{sid}"
        key = f"{method} {path}"
        routes[key] = {"work": {"kind": "fixed", "delay_ms": 0}, "calls": {}, "steps": [],
                       "response_status": source.http_status or 200}
        locations[sid] = (service, method, path)
        origins.append({"service_id": service, "field": key, "source": "observed",
                        "source_span_id": source.span_id, "source_operation": source.operation,
                        "duration_ns": str(source.duration_ns)})

    for s in sorted(spans.values(), key=lambda s: (s.start_ns, s.span_id)):
        if s.kind == "server":
            register(s.span_id, s.service, s)
            covered.add(s.span_id)

    def owner(s):
        parent = s.parent_span_id
        seen = {s.span_id}
        while parent:
            if parent in seen or parent not in spans:
                refuse("missing_link", "Не удалось определить родительский вызов.", [s.span_id])
            seen.add(parent)
            node = spans[parent]
            if node.kind == "server":
                return parent
            parent = node.parent_span_id
        refuse("missing_link", "У вызова нет серверного родительского спана.", [s.span_id])

    linked_servers = set()
    for s in sorted(spans.values(), key=lambda s: (s.start_ns, s.span_id)):
        if s.kind != "client":
            continue
        a = attempts.get(s.span_id)
        if a is None:
            refuse("ambiguous_link", "Не удалось однозначно определить получателя вызова.", [s.span_id])
        target = a.server_span_id
        if target:
            if target not in locations or target in linked_servers:
                refuse("ambiguous_link", "Неоднозначная связь серверного и клиентского спанов.", [s.span_id])
            linked_servers.add(target)
        else:
            if not s.target or s.target == "unknown":
                refuse("missing_link", "У вызова нет спана получателя и имени целевого сервиса.", [s.span_id])
            target = f"external-{s.span_id}"
            register(target, s.target, s)
        children[owner(s)].append((s, target))
        covered.add(s.span_id)
    # Some integrations emit only server spans. Preserve those edges too.
    for s in spans.values():
        if s.kind == "server" and s.span_id != graph.roots[0] and s.span_id not in linked_servers:
            children[owner(s)].append((s, s.span_id))

    visited, visiting = set(), set()
    def build(sid):
        if sid in visiting:
            refuse("cycle", "В связях спанов найден цикл.", [sid])
        if sid in visited:
            return
        visiting.add(sid)
        service, method, path = locations[sid]
        route = services[service]["routes"][f"{method} {path}"]
        source = spans.get(sid) or spans[sid.removeprefix('external-')]
        calls = sorted(children[sid], key=lambda item: (item[0].start_ns, item[0].span_id))
        # Connected overlap groups run concurrently, with individual start offsets.
        batches = []
        for call, target in calls:
            build(target)
            if not batches or call.start_ns >= batches[-1][1]:
                batches.append([call.start_ns, call.end_ns, []])
            else:
                batches[-1][1] = max(batches[-1][1], call.end_ns)
            batches[-1][2].append((call, target))
        cursor = source.start_ns
        for start, end, batch in batches:
            if len(batch) > 64:
                refuse("parallel_limit", "Стенд поддерживает до 64 одновременных вызовов в одной группе.", [s.span_id for s, _ in batch])
            refs, offsets = [], {}
            for call, target in batch:
                target_service, target_method, target_path = locations[target]
                key = f"call-{call.span_id}"
                refs.append(key)
                offsets[key] = max(0, (call.start_ns-start)/1e6)
                group, index = groups.get(call.span_id, (call.span_id, 1))
                origins.append({"service_id": service, "field": key, "source": "observed",
                                "source_span_id": call.span_id, "source_operation": call.operation,
                                "attempts": 1, "duration_ns": str(call.duration_ns)})
                route["calls"][key] = {
                    "target": {"service_id": target_service, "method": target_method, "route": target_path},
                    # Generous model timeout: an observed timeout must not truncate
                    # a receiver subtree which continued after its caller returned.
                    "timeout_ms": 3_600_000, "max_attempts": 1, "backoff_ms": 0,
                    "retry_on": [], "required": False,
                    "observed_group": group, "observed_attempt": index,
                    "observed_status": call.http_status,
                }
            step = {"id": f"group-{len(route['steps'])+1}", "delay_ms": max(0, (start-cursor)/1e6),
                    "offsets_ms": offsets}
            step.update({"call": refs[0]} if len(refs) == 1 else {"parallel": refs})
            route["steps"].append(step)
            cursor = max(cursor, end)
        route["tail_ms"] = max(0, (source.end_ns-cursor)/1e6)
        visiting.remove(sid)
        visited.add(sid)
    build(graph.roots[0])
    if visited != set(locations):
        refuse("unreachable_spans", "Часть вызовов не связана с начальным запросом.", set(locations)-visited)
    # Internal spans are local work, represented by the observed gaps/tail.
    for s in spans.values():
        if s.span_id not in covered:
            owner(s)
            if s.kind not in {"internal", "unspecified"}:
                refuse("unsupported_span", "Тип спана пока не поддерживается стендом.", [s.span_id])
            origins.append({"service_id": locations[owner(s)][0], "field": "local-work", "source": "observed",
                            "source_span_id": s.span_id, "duration_ns": str(s.duration_ns),
                            "approximation": "Локальная работа учтена во времени между вызовами."})
    root_service, method, path = locations[graph.roots[0]]
    return {"schema_version": "1", "scenario_id": "generated", "scenario_revision": 2,
            "entrypoint": {"service_id": root_service, "route": f"{method} {path}"}, "services": services}, origins
