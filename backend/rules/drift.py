"""D1 compares separately identified sources within a single policy epoch."""
from collections import Counter
from decimal import ROUND_CEILING, Decimal

from analytics_support import percentile_ns

from .baselines import usable_sampling
from .budget import current, sourced
from .common import Assessment, asref, digest, integer
from .context import quality_issue

DIMENSIONS = ("caller_version", "callee_version", "workload_id", "config_revision")
CONFIG_FIELDS = ("max_attempts", "per_attempt_timeout_ns")


def domain(policy, environment):
    return {k: policy.get(k) for k in (*DIMENSIONS, "edge_id", "service_id", "call_ref")} | {
        "environment_id": policy.get("environment_id", environment)
    }


def matches(segment, *rows):
    """Each dimension must be evidenced; disagreements at different levels are explicit."""
    return all(segment[k] is not None and {str(r[k]) for r in rows if r.get(k) is not None} == {str(segment[k])}
               for k in DIMENSIONS)


def field_value(policy, field):
    source = policy.get("source", policy.get("effective_source"))
    raw = policy.get(field)
    if isinstance(raw, dict):
        source = raw.get("source")
    value = integer(sourced(policy, field))
    if source not in ("runtime", "declared") or not value:
        return None
    return {"source": source, "value": str(value) if field.endswith("_ns") else value,
            "config_revision": policy["config_revision"],
            "confidence": "high" if source == "runtime" else "medium"}


def tolerance(center, relative, absolute):
    relative_ns = (Decimal(center) * Decimal(str(relative))).to_integral_value(rounding=ROUND_CEILING)
    return max(absolute, int(relative_ns))


def timeout_sample(ctx, edge, model, segment, limits):
    """Estimate from timeout-classified client durations, before inspecting configured values."""
    rows = ctx.by_edge[edge]
    values, refs, examples, excluded = [], set(), [], Counter()
    sampling = model.get("sampling") or {}
    sample = {"source": "cluster", "scope": "per_attempt", "strategy": "timeout-duration-median-v1",
              "confidence": "unknown", "sampling": sampling, "sample_count": 0, "trace_count": 0,
              "min_trace_count": limits.min_cases, "examples": examples}
    if not usable_sampling(sampling):
        return sample, set(), "unknown_or_selected_sampling"
    for key, a in rows:
        if a.get("outcome") != "timeout":
            excluded["other_outcome"] += 1
            continue
        trace = ctx.traces.get(key, {})
        group = ctx.by_id.get((key, a["attempt_id"]))
        sources = [model, trace, group.data if group else {}, a]
        scopes = {s["timeout_scope"] for s in sources if s.get("timeout_scope") is not None}
        if scopes != {"per_attempt"}:
            excluded["unknown_or_non_attempt_timeout"] += 1
            continue
        if not matches(segment, *sources):
            excluded["unmatched_workload_or_versions"] += 1
            continue
        if quality_issue(a) or ctx.graph_issue(key, [a["attempt_id"]], {"conflicting_span", "source_loss"}):
            excluded["unreliable_client_attempt"] += 1
            continue
        duration = integer(a.get("duration_ns"))
        if duration is None or duration == 0:
            excluded["missing_duration"] += 1
            continue
        values.append(duration)
        refs.add(key)
        if len(examples) < 5:
            examples.append({"trace_ref": asref(key), "span_id": a["attempt_id"], "duration_ns": str(duration)})
    sample.update(sample_count=len(values), trace_count=len(refs), excluded_by_reason=dict(excluded))
    if values:
        median = percentile_ns(values, .5)
        spread_limit = tolerance(median, limits.cluster_spread_relative, limits.tolerance_ns)
        sample.update(median_ns=str(median), min_ns=str(min(values)), max_ns=str(max(values)),
                      spread_limit_ns=str(spread_limit))
    if excluded["unmatched_workload_or_versions"]:
        return sample, set(), "mixed_or_unidentified_timeout_segments"
    if len(refs) < limits.min_cases:
        return sample, set(), "too_few_independent_timeout_traces"
    if max(values) - min(values) > spread_limit:
        return sample, set(), "dispersed_timeout_durations"
    sample.update(confidence="medium", value_ns=str(median))
    return sample, refs, None


def compare_policies(ctx, edge, model, policies, limits):
    result = Assessment("D1", edge)
    details = {"segment": None, "configured": {}, "observed_attempts": None, "observed_timeout": None,
               "differences": [], "evaluated_comparisons": [], "selection": "compare_fresh_sources_within_one_epoch_v1"}
    fresh = [p for p in policies if current(p) and ctx.matching_policy(model, edge, p)]
    if not fresh:
        result.reject("missing_fresh_matching_snapshot")
        return result, details
    owners = {digest(p["service"]) for p in fresh if p.get("service")}
    segments = {digest(domain(p, ctx.environment)) for p in fresh}
    segment = domain(fresh[0], ctx.environment)
    if len(segments) != 1 or len(owners) > 1 or any(segment.get(k) is None for k in DIMENSIONS):
        result.reject("unmatched_workload_or_versions")
        return result, details
    if owners and all(p.get("service") for p in fresh):
        segment["service"] = fresh[0]["service"]
    details["segment"] = segment
    configured = {}
    fields = details["configured"]
    for field in CONFIG_FIELDS:
        values = [operand for p in fresh if (operand := field_value(p, field)) is not None]
        values.sort(key=lambda item: (item["source"], item["value"]))
        fields[field] = values
        sources = {}
        for row in values:
            sources.setdefault(row["source"], set()).add(int(row["value"]))
        if any(len(unique) > 1 for unique in sources.values()):
            result.reject("conflicting_values_within_source")
            continue
        configured[field] = {src: next(iter(sources[src])) for src in sorted(sources)}
        if len(sources) == 2:
            if not (segment["service_id"] or segment.get("service")) or not segment["call_ref"]:
                result.reject("missing_configuration_target")
                continue
            different = sources["runtime"] != sources["declared"]
            details["evaluated_comparisons"].append({"field": field, "comparison": "runtime_vs_declared"})
            result.consider(triggered=different)
            if different:
                details["differences"].append({"field": field, "comparison": "runtime_vs_declared",
                                               "sources": ["runtime", "declared"]})

    # A concrete confirmed group establishes a lower bound, even in an incomplete population.
    groups = []
    for g in ctx.groups[edge]:
        if g.reason or not matches(segment, model, ctx.traces.get(g.key, {}), g.data, *g.attempts):
            result.reject(g.reason or "unmatched_workload_or_versions")
        else:
            groups.append(g)
    if groups:
        details["observed_attempts"] = {"source": "observed", "lower_bound": max(len(g.attempts) for g in groups),
                                        "eligible_calls": len(groups), "confidence": "high"}
    for source, limit in configured.get("max_attempts", {}).items():
        examples, affected = [], 0
        for g in groups:
            hit = len(g.attempts) > limit
            result.consider([g.key], hit)
            if hit:
                affected += 1
                if len(examples) < 5:
                    examples.append({"trace_ref": asref(g.key), "attempt_ids": g.data["attempt_ids"]})
        if groups:
            details["evaluated_comparisons"].append({"field": "max_attempts", "comparison": "observed_above_config",
                                                      "source": source})
        if affected:
            details["differences"].append({"field": "max_attempts", "comparison": "observed_above_config",
                                           "source": source, "affected_calls": affected, "samples": examples})

    if configured.get("per_attempt_timeout_ns"):
        sample, refs, why = timeout_sample(ctx, edge, model, segment, limits)
        details["observed_timeout"] = sample
        if why:
            result.reject(why)
        else:
            low, high = int(sample["min_ns"]), int(sample["max_ns"])
            sample["comparison_tolerances_ns"] = {}
            for source, value in configured["per_attempt_timeout_ns"].items():
                margin = tolerance(value, limits.timeout_drift_relative, limits.tolerance_ns)
                different = high < value - margin or low > value + margin
                sample["comparison_tolerances_ns"][source] = str(margin)
                details["evaluated_comparisons"].append({"field": "per_attempt_timeout_ns",
                                                         "comparison": "timeout_cluster_vs_config", "source": source})
                result.consider(refs, different)
                if different:
                    details["differences"].append({"field": "per_attempt_timeout_ns",
                        "comparison": "timeout_cluster_vs_config", "source": source, "tolerance_ns": str(margin)})
    if details["differences"]:
        cluster_based = any(d["comparison"] == "timeout_cluster_vs_config" for d in details["differences"])
        result.report("Источники расходятся в оценке политики вызова",
                      "Значения и область сравнения приведены отдельно для каждого источника.",
                      {"kind": "policy_drift", "source": "Конфигурация и наблюдения в одном профиле", **details},
                      basis="config_aware", confidence="medium" if cluster_based else "high")
    return result, details
