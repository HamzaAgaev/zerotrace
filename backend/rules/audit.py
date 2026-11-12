"""P5 orchestration. All reads are supplied by the caller."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import UTC, datetime

from .baselines import reference_for
from .budget import flatten_policies, retry_budget
from .common import (
    RECOMMENDATIONS,
    VERSION,
    Assessment,
    asref,
    digest,
    integer,
    load_thresholds,
    trace_evidence,
)
from core.model import EdgeRetrySamples, build_edge_policy, is_current_policy, trusted_int

from .context import Context
from .diagnostics import check_timeout_order, retry_checks, retry_paths, timing_checks
from .drift import compare_policies

SEVERITY = {"high": 3, "medium": 2, "low": 1}
CONFIDENCE = {"high": 1, "medium": .7, "low": .4}


def materialize(result, context, occurrence, labels):
    item = result.finding
    if not item:
        return None
    fid = digest([context, result.rule, result.edge, "default"])
    oid = digest([fid, occurrence])
    recs = []
    for rec in item["recommendations"]:
        rid = digest([oid, rec])
        recs.append({**rec, "recommendation_id": rid, "version": VERSION,
                     "intents": [{**intent, "reason_ref": oid} for intent in rec["intents"]]})
    denominator = len(result.refs)
    ratio = len(result.affected) / denominator if denominator else None
    score = SEVERITY[item["severity"]] * (.5 + ratio) * CONFIDENCE[item["confidence"]] if ratio is not None else None
    return {**item, "score": score, "score_reason": "eligible_sample_priority" if score is not None else "missing_denominator",
            "finding_id": fid, "occurrence_id": oid, "edge_id": result.edge,
            "edge": dict(labels), "environment_id": context[1], "rule_id": result.rule, "rule_version": VERSION,
            "affected_count": len(result.affected), "denominator_count": denominator,
            "affected_ratio": ratio,
            "exemplars": [asref(key) for key in sorted(result.affected)[:5]], "recommendations": recs}


def evaluation(rule, entries):
    counts, exclude = Counter(), Counter()
    details = []
    for result in entries:
        counts[result.status()] += 1
        exclude.update(result.excluded)
        detail = dict(edge_id=result.edge, status=result.status(), eligible_count=result.count,
                      eligible_trace_count=len(result.refs), triggered_count=result.hit_count,
                      excluded_by_reason=dict(sorted(result.excluded.items())))
        if result.eligible_call_count is not None:
            detail["eligible_call_count"] = result.eligible_call_count
        details.append(detail)
    if counts["evaluated"]:
        status = "evaluated"
    elif counts["insufficient_data"] or not counts:
        status = "insufficient_data"
    else:
        status = "not_applicable"
    return dict(rule_id=rule, rule_version=VERSION, status=status, eligible_count=sum(x.count for x in entries),
                triggered_count=sum(x.hit_count for x in entries), excluded_by_reason=dict(sorted(exclude.items())),
                by_edge=details)


def run(model, policies=(), thresholds=None, baselines=None):
    """Evaluate one pinned dataset and explicit configuration snapshots.

    Set model.generated_at for reproducible offline output. Timing metadata is
    separate from identities except when a reference baseline's age is relevant.
    """
    ctx = Context(model)
    limits = load_thresholds(thresholds)
    by_edge = defaultdict(list)
    for p in flatten_policies(policies, ctx.environment):
        if p not in by_edge[p["edge_id"]]:
            by_edge[p["edge_id"]].append(p)
    findings, observations = [], []
    trace_occurrences = defaultdict(list)
    entries = defaultdict(list)
    generated = model.get("generated_at") or datetime.now(UTC).isoformat()
    edges = sorted(set(ctx.by_edge) | set(ctx.groups) | set(by_edge))
    scoped_baselines = {}
    for edge in edges:
        dims = dict(edge_id=edge, environment_id=ctx.environment,
                    caller_version=model.get("caller_version"), callee_version=model.get("callee_version"),
                    workload_id=model.get("workload_id"))
        scoped_baselines[edge] = reference_for(baselines or [], dims, generated, limits.for_edge(edge).min_cases)
    baseline_refs = sorted(row["baseline_id"] for row in scoped_baselines.values() if row)
    threshold_hash = digest(asdict(limits))
    config_refs = [dict(edge_id=edge, revision=p.get("config_revision"), hash=digest(
        {k: v for k, v in p.items() if k not in {"lab_only", "chaos", "expected"}}))
        for edge in edges for p in sorted(by_edge[edge], key=digest)]
    sampling = model.get("sampling") or {"mode": "unknown"}
    data_hash = digest([ctx.normalized_identity(), model.get("dataset_ref"), sampling, model.get("model_version"),
                        {k: model.get(k) for k in ("environment_kind", "workload_id", "caller_version",
                                                   "callee_version", "config_revision", "timeout_scope", "edges")}])
    grouping_version = model.get("grouping_version")
    if grouping_version is not None:
        data_hash = digest([data_hash, grouping_version])
    identity = [model.get("project_id"), ctx.environment]
    occurrence = digest([identity, data_hash, config_refs, threshold_hash, baseline_refs, VERSION])
    paths = retry_paths(ctx, limits)
    for edge in edges:
        ambiguous_policy = len(by_edge[edge]) > 1
        policy = by_edge[edge][0] if len(by_edge[edge]) == 1 else {}
        mismatched_revision = bool(policy) and not ctx.matching_policy(model, edge, policy)
        if mismatched_revision:
            policy = {}
        lim = limits.for_edge(edge)
        obs, r4 = Assessment("OBS_RETRIES", edge), Assessment("R4", edge)
        samples = EdgeRetrySamples()
        summary = ctx.observations(edge, obs, samples)
        observations.append(summary)
        if obs.hit_count:
            evidence = trace_evidence(**summary)
            evidence["samples"] = [
                dict(trace_ref=asref(g.key), attempt_ids=g.data["attempt_ids"])
                for g in ctx.groups[edge] if not g.reason and g.data["hypothesis"] == "retry"
            ][:5]
            obs.report("Наблюдаются повторные попытки",
                       f"Подтверждённые повторы в {obs.hit_count} из {obs.count} пригодных логических вызовов.",
                       evidence, severity="low")
        retry_budget(r4, policy, ctx.edge_refs[edge], lim, lab=model.get("environment_kind") == "lab")
        r1, r2 = timing_checks(ctx, edge, lim, scoped_baselines[edge])
        for r in (r1, r2):
            if 0 < r.count < lim.min_cases:
                r.reject("too_few_eligible_samples")
                r.count = r.hit_count = 0
        r3 = paths.get(edge) or Assessment("R3", edge)
        if r3.finding:
            bound = 1
            for ancestor in r3.finding["evidence"]["path"]:
                profiles = by_edge[ancestor]
                if (len(profiles) != 1 or not is_current_policy(profiles[0])
                        or not ctx.matching_policy(model, ancestor, profiles[0])
                        or profiles[0].get("execution") != "serial"):
                    break
                cap = trusted_int(build_edge_policy(profiles[0]).max_attempts)
                if not cap:
                    break
                bound *= cap
            else:
                r3.finding["evidence"]["theoretical_bound"] = bound
        if not r3.count and not r3.excluded:
            r3.reject("no_confirmed_nested_retry_path")
        r8 = check_timeout_order(ctx, edge, policy)
        d1, summary["policy_evidence"] = compare_policies(ctx, edge, model, by_edge[edge], lim)
        if ambiguous_policy or mismatched_revision:
            for result in (r4, r8):
                result.reject("conflicting_policy_snapshots" if ambiguous_policy else "mismatched_config_revision")
        batch = [obs, r1, r2, r3, r4, *retry_checks(ctx, edge, policy, lim, samples), r8, d1]
        labels = ctx.labels(edge, policy)
        for result in batch:
            if not result.count and not result.excluded:
                result.reject("no_eligible_observations")
            entries[result.rule].append(result)
            finding = materialize(result, identity, occurrence, labels)
            if finding:
                finding["thresholds_hash"] = threshold_hash
                findings.append(finding)
                for key in result.affected:
                    trace_occurrences[key].append(finding["occurrence_id"])
    findings.sort(key=lambda f: (f["score"] is None, -f["score"] if f["score"] is not None else 0,
                                 -SEVERITY[f["severity"]], -CONFIDENCE[f["confidence"]], f["finding_id"]))
    return dict(result_id=occurrence, generated_at=generated, environment_id=ctx.environment,
                grouping_version=grouping_version,
                ruleset_version=VERSION, model_version=model.get("model_version", "p2-v1"),
                dataset_ref=model.get("dataset_ref"), dataset_hash=data_hash, config_refs=config_refs,
                thresholds_version=limits.defaults.version, thresholds_hash=threshold_hash, baseline_refs=baseline_refs,
                sampling=sampling, observations=observations, findings=findings,
                trace_findings=[{"trace_ref": asref(key), "occurrence_ids": sorted(trace_occurrences[key])}
                                for key in sorted(trace_occurrences)],
                rule_evaluations=[evaluation(rule, entries[rule]) for rule in RECOMMENDATIONS])


def get_recommendation(result, occurrence_id, recommendation_id):
    for finding in result["findings"]:
        if finding["occurrence_id"] == occurrence_id:
            for rec in finding["recommendations"]:
                if rec["recommendation_id"] == recommendation_id:
                    return rec
    return None


def verify_symptom(previous_finding, current_result, *, comparable, grace_elapsed,
                   minimum_calls=30, coverage_complete=True):
    """Pure evaluator; A schedules the grace period and records verification."""
    if not grace_elapsed:
        return "checking"
    if previous_finding.get("environment_id") != current_result.get("environment_id"):
        return "inconclusive"
    if not comparable or not coverage_complete:
        return "inconclusive"
    edge = previous_finding["edge_id"]
    obs = next((item for item in current_result["observations"] if item["edge_id"] == edge), None)
    if not obs or obs["eligible_calls"] < minimum_calls:
        return "insufficient_data"
    rule = previous_finding["rule_id"]
    ev = next((item for item in current_result["rule_evaluations"] if item["rule_id"] == rule), {})
    if (previous_finding.get("rule_version") != ev.get("rule_version")
            or previous_finding.get("thresholds_hash") != current_result.get("thresholds_hash")):
        return "inconclusive"
    detail = next((item for item in ev.get("by_edge", []) if item["edge_id"] == edge), None)
    if not detail or detail["status"] != "evaluated":
        return "insufficient_data"
    if rule == "D1":
        def comparison_key(item):
            return item["field"], item["comparison"], item.get("source")
        expected = {comparison_key(row) for row in previous_finding["evidence"]["differences"]}
        policy_evidence = obs["policy_evidence"]
        observed = {comparison_key(row) for row in policy_evidence["differences"]}
        eligible = {comparison_key(row) for row in policy_evidence["evaluated_comparisons"]}
        if expected & observed:
            return "symptom_present"
        if not expected <= eligible:
            return "insufficient_data"
        return "symptom_absent"
    if any(f["finding_id"] == previous_finding["finding_id"] for f in current_result["findings"]):
        return "symptom_present"
    if rule in {"R1", "R2"} and detail.get("eligible_call_count", 0) < minimum_calls:
        return "insufficient_data"
    return "symptom_absent"
