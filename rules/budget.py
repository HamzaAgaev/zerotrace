"""Local sequential policy arithmetic in integer nanoseconds."""
from __future__ import annotations

from .common import MAX_NS, integer


def flatten_policies(policies, environment):
    """Flatten A's P4 snapshots or accept pre-joined P3 edge policies."""
    policies = policies or []
    if isinstance(policies, dict):
        policies = [policies]
    result = []
    for policy in policies:
        if not isinstance(policy, dict):
            raise ValueError("policy must be an object")
        rows = policy.get("edges")
        if rows is None:
            result.append(dict(policy))
            continue
        owner = policy.get("service", {})
        inherited = {k: policy[k] for k in ("capabilities", "environment_id", "source", "freshness",
                     "environment_kind", "stale", "workload_id", "caller_version", "callee_version") if k in policy}
        inherited["config_revision"] = policy.get("revision")
        # The adapter registry owns service_id. Display names can repeat across namespaces.
        inherited["service_id"] = policy.get("service_id")
        if owner:
            inherited["service"] = owner
        if isinstance(owner, dict) and owner.get("environment"):
            if owner["environment"] != environment:
                raise ValueError("policy service belongs to another environment")
            inherited.setdefault("environment_id", owner["environment"])
        for row in rows:
            # An external timeout is usable only if explicitly bound to this call.
            matches = [entry for entry in policy.get("external_timeouts", [])
                       if entry.get("call_ref") and entry["call_ref"] == row.get("call_ref")]
            budget = {"budget_ns": matches[0].get("timeout_ns")} if len(matches) == 1 else {}
            merged = {**budget, **row, **inherited}
            # P4 identifies the configuration owner and epoch at the snapshot level.
            # A nested edge can narrow, but cannot grant, the snapshot's capabilities.
            caps = policy.get("capabilities") or {}
            if "capabilities" in row:
                edge_caps = row["capabilities"] or {}
                caps = {**caps,
                        "apply_recommendation": caps.get("apply_recommendation") is True
                        and edge_caps.get("apply_recommendation") is True,
                        "write_fields": sorted(set(caps.get("write_fields", [])) &
                                               set(edge_caps.get("write_fields", [])))}
            merged["capabilities"] = caps
            identity_fields = ("config_revision", "service_id", "service", "environment_id", "environment_kind", "source")
            if any(k in row and k in inherited and row[k] != inherited[k] for k in identity_fields):
                merged["binding_conflict"] = True
            if row.get("stale") or row.get("freshness") in ("stale", "unavailable", "unknown"):
                merged["stale"] = True
            result.append(merged)
    for row in result:
        if row.get("environment_id", environment) != environment:
            raise ValueError("policy belongs to another environment")
        if not isinstance(row.get("edge_id"), str) or not row["edge_id"]:
            raise ValueError("resolved edge_id required")
    return result


def sourced(policy, key):
    operand = policy.get(key)
    if isinstance(operand, dict) and "value" in operand:
        if operand.get("source") not in {"runtime", "declared"} or operand.get("stale"):
            return None
        if (operand.get("config_revision") is not None and policy.get("config_revision") is not None
                and operand["config_revision"] != policy["config_revision"]):
            return None
        return operand["value"]
    if policy.get("source", policy.get("effective_source")) in {"runtime", "declared"}:
        return operand
    return None


def current(policy):
    return (bool(policy) and not policy.get("binding_conflict") and not policy.get("stale")
            and policy.get("freshness") not in ("stale", "unavailable", "unknown"))


def pauses_ns(n, backoff):
    if n == 1:
        return 0
    if not isinstance(backoff, dict) or backoff.get("jitter") not in ("none", "full"):
        return None
    base = integer(backoff.get("base_ns"))
    cap = integer(backoff.get("cap_ns"))
    if base is None or ("cap_ns" in backoff and backoff["cap_ns"] is not None and cap is None):
        return None
    if cap is not None:
        base = min(cap, base)
    if backoff.get("kind") == "fixed":
        return (n - 1) * base
    multiplier = integer(backoff.get("multiplier"))
    if backoff.get("kind") != "exponential" or not multiplier or multiplier < 1:
        return None
    if multiplier == 1 or not base:
        return (n - 1) * base
    total = 0
    for i in range(n - 1):
        if cap is not None and base >= cap:
            return total + (n - 1 - i) * cap
        total += base
        base *= multiplier
        if base.bit_length() > 64 and cap is None and i < n - 2:
            return None  # next configured pause is outside the supported int64 domain
    return total


def retry_budget(result, policy, refs, limits, *, lab=False):
    if not current(policy):
        result.reject("missing_or_stale_policy")
        return
    execution = policy.get("execution", policy.get("policy_scope"))
    if execution != "serial":
        result.applicable = execution not in {"hedge", "async", "parallel"}
        result.reject("serial_policy_required")
        return
    n, timeout, budget = [integer(sourced(policy, key)) for key in
                         ("max_attempts", "per_attempt_timeout_ns", "budget_ns")]
    backoff = sourced(policy, "backoff")
    if n is None or n < 1 or not timeout or budget is None or not policy.get("config_revision"):
        result.reject("unknown_budget_operands_or_revision")
        return
    pauses = pauses_ns(n, backoff)
    if pauses is None:
        result.reject("unsupported_or_unknown_backoff")
        return
    reserve = integer(policy.get("reserve_ns", 0))
    elapsed = integer(policy.get("elapsed_ns", 0))
    if reserve is None or elapsed is None:
        result.reject("unknown_allocated_budget")
        return
    available = budget - reserve - elapsed
    required = n * timeout + pauses
    if required > MAX_NS or available < -MAX_NS-1:
        result.reject("budget_outside_int64")
        return
    deficit = required > available
    result.consider(refs, deficit)
    if not deficit:
        return
    evidence = dict(kind="retry_budget", formula_code="serial-attempts-v1",
                    source=policy.get("source") or policy.get("effective_source") or "Снимок конфигурации",
                    config_revision=str(policy["config_revision"]), attempts=n, timeout_ns=str(timeout),
                    backoff_total_ns=str(pauses), required_ns=str(required), budget_ns=str(budget),
                    available_ns=str(available), reserve_ns=str(reserve), elapsed_ns=str(elapsed),
                    jitter_bound="upper" if isinstance(backoff, dict) and backoff.get("jitter") == "full" else "configured",
                    operand_sources={k: (policy[k].get("source") if isinstance(policy.get(k), dict) and "value" in policy[k]
                                          else policy.get("source", policy.get("effective_source"))) for k in
                                     ("max_attempts", "per_attempt_timeout_ns", "budget_ns", "backoff")})
    # Find largest admissible count, keeping the remaining recovery opportunities.
    lo, hi = 0, n
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if mid * timeout + pauses_ns(mid, backoff) <= available:
            lo = mid
        else:
            hi = mid
    min_timeout = integer(policy.get("min_timeout_ns", 0))
    healthy = integer(policy.get("healthy_duration_ns", 0))
    floors_known = min_timeout is not None and healthy is not None
    if not floors_known:
        result.reject("unknown_feasibility_floor")
        lo = 0
    elif max(min_timeout, healthy) > min(timeout, available):
        lo = 0
    rec = dict(kind="primary", changes=[], intents=[], risks=[], preconditions=[],
               why="Уменьшение лимита сокращает расчётное число последовательных попыток.")
    if lo:
        after = lo * timeout + pauses_ns(lo, backoff)
        evidence.update(recommended_attempts=lo, recommended_required_ns=str(after))
        rec.update(text=f"Установить максимум {lo} попыток у владельца вызова.", feasibility="feasible",
                   changes=[dict(field="max_attempts", before=str(n), after=str(lo))],
                   risks=["Запрос, который восстанавливался на поздней попытке, может завершиться ошибкой."],
                   preconditions=["Ревизия конфигурации и политика вызова актуальны."])
        caps = policy.get("capabilities") or {}
        if (lab and policy.get("environment_kind", "lab") == "lab" and caps.get("apply_recommendation") is True
                and "max_attempts" in caps.get("write_fields", [])
                and policy.get("service_id") and policy.get("call_ref")):
            rec["intents"].append(dict(target=dict(service_id=policy["service_id"], call_ref=policy["call_ref"]),
                                       expected_config_revision=policy["config_revision"], set={"max_attempts": lo}))
    else:
        if floors_known or available < timeout:
            rec.update(text="Бюджет не позволяет подобрать допустимый лимит попыток.", feasibility="infeasible")
        else:
            rec.update(text="Для подбора лимита нужна нижняя граница допустимого таймаута.", feasibility="unknown")
    why = f"По настройкам требуется {required} нс при доступных {available} нс."
    result.report("Превышен локальный бюджет повторов", why, evidence, basis="config_aware",
                  severity="high" if required > available * limits.budget_high_ratio else "medium", recommendation=rec)
