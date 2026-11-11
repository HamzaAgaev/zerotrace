"""Each detector reports its own units, eligibility and excluded observations."""
from collections import defaultdict
from math import sqrt

from analytics_support import percentile_ns
from core.timing import same_clock

from .budget import current, sourced
from .common import Assessment, asref, integer, trace_evidence
from .context import attempt_error, quality_issue


def span_ref(key, attempt):
    return {"trace_ref": asref(key), "span_id": attempt["attempt_id"]}


def timing_checks(ctx, edge, limits, reference):
    longer, late = Assessment("R1", edge), Assessment("R2", edge)
    longer_examples, late_examples = [], []
    for key, a in ctx.by_edge[edge]:
        client_ns = integer(a.get("duration_ns"))
        server_ns = integer(a.get("server_duration_ns"))
        error = attempt_error(a)
        if (quality_issue(a) or ctx.graph_issue(key, [a["attempt_id"], a.get("server_span_id")], {"missing_parent", "conflicting_span", "cycle"})
                or not ctx.linked(key, a["attempt_id"], a.get("server_span_id"), "client", "server") or server_ns is None
                or client_ns is None or error is None):
            longer.reject("missing_reliable_client_server_pair")
        else:
            diff = server_ns - client_ns
            hit = error and diff > max(limits.tolerance_ns, client_ns * limits.r1_relative)
            longer.consider([key], hit)
            if hit and len(longer_examples) < 5:
                sample = dict(**span_ref(key, a), server_span_id=a["server_span_id"], client_ns=str(client_ns),
                              server_ns=str(server_ns), difference_ns=str(diff))
                if reference:
                    sample["baseline_id"] = reference["baseline_id"]
                    sample["reference_client_p95_ns"] = reference["success_p95_ns"]
                    sample["degradation_hypothesis"] = client_ns > int(reference["success_p95_ns"]) * limits.degradation_ratio
                longer_examples.append(sample)
        upstream = ctx.attempts.get((key, a.get("upstream_attempt_id")))
        offset = integer(a.get("offset_in_caller_ns"))
        upper_wait = integer(upstream.get("duration_ns")) if upstream else None
        if (quality_issue(a) or ctx.graph_issue(key, [a["attempt_id"], a.get("upstream_attempt_id"), a.get("caller_server_span_id")],
                                               {"cross_clock_uncertain", "missing_parent", "conflicting_span", "cycle"})
                or offset is None or upper_wait is None or quality_issue(upstream)
                or upstream.get("server_span_id") != a.get("caller_server_span_id")
                or not ctx.linked(key, upstream["attempt_id"], a.get("caller_server_span_id"), "client", "server")
                or not ctx.linked(key, a.get("caller_server_span_id"), a["attempt_id"], "server", "client")
                or a.get("clock_compatible") is False or a.get("upstream_attempt_id") == a["attempt_id"]):
            late.reject("missing_ancestry_or_compatible_offset")
        else:
            hit = offset > upper_wait + max(limits.tolerance_ns, upper_wait * limits.r2_relative)
            late.consider([key], hit)
            if hit and len(late_examples) < 5:
                late_examples.append(dict(**span_ref(key, a), upstream_attempt_id=upstream["attempt_id"],
                                          offset_ns=str(offset), upstream_wait_ns=str(upper_wait)))
    if longer_examples and longer.count >= limits.min_cases:
        longer.report("Серверный спан длиннее ошибочного клиентского", "Сопоставлены длительности связанной клиентской и серверной попыток.",
                      trace_evidence(samples=longer_examples), confidence="medium")
    if late_examples and late.count >= limits.min_cases:
        late.report("Поздний запуск вложенной попытки", "Смещение старта внутри вызывающего SERVER превышает длительность ожидания его клиента.",
                    trace_evidence(samples=late_examples), confidence="medium")
    return longer, late


def retry_paths(ctx, limits_by_edge):
    """Count descendants on actual call paths; discard fanout at a repeated call site."""
    results = {}
    children = defaultdict(list)
    roots = []
    for groups in ctx.groups.values():
        for group in groups:
            if group.reason:
                continue
            parents = {a.get("upstream_attempt_id") for a in group.attempts}
            if len(parents) != 1:
                continue
            parent = parents.pop()
            if parent is None:
                roots.append(group)
            elif (group.key, parent) in ctx.by_id:
                upstream = ctx.attempts[(group.key, parent)]
                if ctx.linked(group.key, parent, upstream.get("server_span_id"), "client", "server") and all(
                        a.get("caller_server_span_id") == upstream["server_span_id"]
                        and ctx.linked(group.key, a["caller_server_span_id"], a["attempt_id"], "server", "client")
                        for a in group.attempts):
                    children[(group.key, parent)].append(group)
    for root in roots:
        evaluations = []
        stack = [([root], (), 0)]
        while stack:
            groups, path, retry_levels = stack.pop()
            edge = groups[0].data["edge_id"]
            if edge in path or len(path) >= 256:
                # Service loops with repeated edge identities cannot define a first-path multiple.
                continue
            path = path + (edge,)
            retries = retry_levels + any(g.data["hypothesis"] == "retry" for g in groups)
            parts = [a for g in groups for a in g.attempts]
            firsts = [a for a in parts if a.get("first_path") is True]
            complete = all(type(a.get("first_path")) is bool for a in parts) and len(firsts) == 1
            if complete:
                # A claimed first path cannot traverse a later retry of an ancestor.
                first = firsts[0]
                while first is not None:
                    parent_group = ctx.by_id[(root.key, first["attempt_id"])]
                    if parent_group.attempts[0] is not first or first.get("first_path") is False:
                        complete = False
                        break
                    first = ctx.attempts.get((root.key, first.get("upstream_attempt_id")))
            if len(path) > 1:
                limit = limits_by_edge.for_edge(edge)
                hit = complete and retries >= 2 and len(parts) >= limit.amp_medium
                data = None
                if hit:
                    data = trace_evidence(path=list(path), first_path_attempt_id=firsts[0]["attempt_id"],
                                          trace_ref=asref(root.key), observed_attempts=len(parts),
                                          upstream_attempt_ids=sorted({a["upstream_attempt_id"] for a in parts}),
                                          theoretical_bound=None)
                evaluations.append((path, complete, data))
            by_edge = defaultdict(list)
            ambiguous = set()
            for parent in parts:
                same_edge = defaultdict(list)
                for child in children[(root.key, parent["attempt_id"])]:
                    same_edge[child.data["edge_id"]].append(child)
                for ce, siblings in same_edge.items():
                    if len(siblings) != 1:
                        ambiguous.add(ce)
                    else:
                        by_edge[ce].extend(siblings)
            for ce, batch in by_edge.items():
                if ce not in ambiguous:
                    stack.append((batch, path, retries))
        # Publish the deepest affected edge for each independent branch of this root.
        superseded = {path[:i] for path, _, data in evaluations if data
                      for i in range(2, len(path))}
        for path, complete, data in evaluations:
            edge = path[-1]
            result = results.setdefault(edge, Assessment("R3", edge))
            if not complete:
                result.reject("missing_first_path")
                continue
            if data and path in superseded:
                result.reject("deeper_affected_edge")
                continue
            result.consider([root.key], bool(data))
            if data and (not result.finding or result.finding["evidence"]["observed_attempts"] < data["observed_attempts"]):
                severity = "high" if data["observed_attempts"] >= limits_by_edge.for_edge(edge).amp_high else "medium"
                result.report("Вложенные повторы усиливают число попыток",
                              "Число попыток посчитано на связанной цепочке подтверждённых вызовов.",
                              data, severity=severity)
    return results


def retry_checks(ctx, edge, policy, limits):
    pauses, writes, status = (Assessment(rule, edge) for rule in ("R5", "R6", "R7"))
    gaps = defaultdict(list)
    risk, forbidden = [], []
    for group in ctx.groups[edge]:
        if group.reason or group.data["hypothesis"] != "retry":
            for r in (pauses, writes, status):
                r.reject(group.reason or "single_attempt")
            continue
        g = group.data
        key = group.key
        raw_gaps = g.get("gaps_ns")
        if raw_gaps is None:
            raw_gaps = [a.get("gap_before_ns") for a in group.attempts[1:]]
        values = [integer(v) for v in raw_gaps]
        if len(values) != len(group.attempts) - 1:
            pauses.reject("unknown_serial_gaps")
        else:
            clocks = [ctx.spans.get((key, a["attempt_id"]), a).get("clock_domain")
                      for a in group.attempts]
            for i, value in enumerate(values, 1):
                if not clocks[i] or not same_clock(clocks[i - 1], clocks[i]):
                    pauses.reject("cross_clock_uncertain")
                elif value is None:
                    pauses.reject("unknown_serial_gaps")
                else:
                    gaps[i].append((key, value))
        method = g.get("method") or group.attempts[0].get("method")
        client_span = ctx.spans.get((key, group.attempts[0]["attempt_id"]), {})
        op = client_span.get("operation")
        method = method or client_span.get("method") or (op.get("method") if isinstance(op, dict) else None)
        method = method.upper() if isinstance(method, str) else None
        if method in {"POST", "PATCH"}:
            evidence = [a.get("idempotency") or ctx.spans.get((key, a["attempt_id"]), {}).get("idempotency") or "unknown"
                        for a in group.attempts]
            hit = any(item != "present" for item in evidence)
            writes.consider([key], hit)
            if hit and len(risk) < 5:
                risk.append(dict(trace_ref=asref(key), attempt_ids=g["attempt_ids"], method=method, idempotency=evidence))
        elif not method:
            writes.reject("unknown_method")
        else:
            writes.applicable = False

        for attempt in group.attempts[:-1]:
            protocol = attempt.get("protocol", "http" if attempt.get("client_http_status") is not None else None)
            if protocol not in {"http", "grpc"}:
                status.reject("unknown_client_protocol")
                continue
            # gRPC may use a numeric code or its normalised symbolic name.
            code = attempt.get("client_grpc_status") if protocol == "grpc" else integer(attempt.get("client_http_status"))
            if code is None:
                status.reject("missing_client_status")
                continue
            if protocol == "http" and not 100 <= code <= 599:
                status.reject("invalid_client_http_status")
                continue
            allowed = sourced(policy, "retry_on") if current(policy) else None
            if isinstance(allowed, dict):
                allowed = allowed.get(protocol)
            if isinstance(allowed, list):
                hit = str(code) not in {str(x) for x in allowed}
                basis = "configured_retry_on"
            elif protocol == "http":
                hit = 400 <= code < 500 and code not in (408, 425, 429)
                basis = "default_http_v1"
            else:
                status.reject("unknown_grpc_policy")
                continue
            status.consider([key], hit)
            if hit and len(forbidden) < 5:
                forbidden.append(dict(**span_ref(key, attempt), status=code, policy=basis, protocol=protocol))
    gap_evidence = []
    for index, entries in sorted(gaps.items()):
        if len(entries) < limits.min_cases:
            pauses.reject("small_gap_sample")
            continue
        values = [v for _, v in entries]
        p95 = percentile_ns(values, .95)
        mean = sum(values) / len(values)
        cv = sqrt(sum((v - mean)**2 for v in values) / len(values)) / mean if mean else None
        low_cv = cv is not None and len(values) >= limits.jitter_min_samples and cv < limits.jitter_cv
        hit = p95 < limits.no_backoff_p95_ns or low_cv
        for key, _ in entries:
            pauses.consider([key], hit)
        if hit:
            gap_evidence.append(dict(after_attempt=index, samples=len(values), p95_ns=str(p95), cv=cv))
    if gap_evidence:
        pauses.report("Короткие или мало меняющиеся паузы", "Статистика измеренных пауз рассчитана отдельно для каждого номера попытки.",
                      trace_evidence(gaps=gap_evidence), confidence="medium")
    if risk:
        writes.report("Повтор операции записи без подтверждённой идемпотентности",
                      "В телеметрии нет достаточного подтверждения защиты всех попыток. Бизнес-эффект требует проверки.",
                      trace_evidence(samples=risk), confidence="medium")
    if forbidden:
        status.report("Повтор после неподходящего клиентского статуса", "После указанного ответа на клиентском спане последовала следующая попытка.",
                      trace_evidence(samples=forbidden))
    return pauses, writes, status


def check_timeout_order(ctx, edge, policy):
    order = Assessment("R8", edge)
    if not current(policy):
        order.reject("missing_or_stale_policy")
        return order
    timeout = integer(sourced(policy, "per_attempt_timeout_ns"))
    upstream = integer(sourced(policy, "upstream_per_attempt_timeout_ns"))
    if not timeout or not upstream:
        order.reject("unknown_paired_timeouts")
    else:
        hit = timeout >= upstream
        order.consider(ctx.edge_refs[edge], hit)
        if hit:
            order.report("Внутренний таймаут достигает таймаута вызывающей стороны",
                         f"Внутренний таймаут {timeout} нс, таймаут вызывающей стороны {upstream} нс.",
                         trace_evidence(inner_timeout_ns=str(timeout), outer_timeout_ns=str(upstream),
                                        config_revision=policy.get("config_revision"), policy_source=policy.get("source")),
                         basis="config_aware")
            order.finding["evidence"]["source"] = "Конфигурация пары связанных вызовов"
            if policy.get("upstream_config"):
                order.finding["evidence"]["upstream_config"] = policy["upstream_config"]
    return order
