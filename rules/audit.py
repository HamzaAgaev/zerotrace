"""P5 orchestration. All reads are supplied by the caller."""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict
from datetime import datetime, timezone

from .baselines import reference_for
from .budget import current, flatten_policies, retry_budget, sourced
from .common import Assessment, RECOMMENDATIONS, VERSION, asref, digest, integer, load_thresholds, trace_evidence
from .context import Context
from .diagnostics import check_timeout_order, retry_checks, retry_paths, timing_checks
from .drift import compare_policies


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
    return {**item, "finding_id": fid, "occurrence_id": oid, "edge_id": result.edge,
            "edge": dict(labels), "environment_id": context[1], "rule_id": result.rule, "rule_version": VERSION,
            "affected_count": len(result.affected), "denominator_count": denominator,
            "affected_ratio": len(result.affected) / denominator if denominator else None,
            "exemplars": [asref(key) for key in sorted(result.affected)[:5]], "recommendations": recs}


def evaluation(rule, entries):
    counts, exclude = Counter(), Counter()
    details = []
    for result in entries:
        counts[result.status()] += 1
        exclude.update(result.excluded)
        details.append(dict(edge_id=result.edge, status=result.status(), eligible_count=result.count,
                            eligible_trace_count=len(result.refs), triggered_count=result.hit_count,
                            excluded_by_reason=dict(sorted(result.excluded.items()))))
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
    entries = defaultdict(list)
    generated = model.get("generated_at") or datetime.now(timezone.utc).isoformat()
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
        summary = ctx.observations(edge, obs)
        observations.append(summary)
        if obs.hit_count:
            evidence = trace_evidence(**summary)
            samples = [dict(trace_ref=asref(g.key), attempt_ids=g.data["attempt_ids"]) for g in ctx.groups[edge]
                       if not g.reason and g.data["hypothesis"] == "retry"][:5]
            evidence["samples"] = samples
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
                if (len(profiles) != 1 or not current(profiles[0])
                        or not ctx.matching_policy(model, ancestor, profiles[0])
                        or profiles[0].get("execution") != "serial"):
                    break
                cap = integer(sourced(profiles[0], "max_attempts"))
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
        batch = [obs, r1, r2, r3, r4, *retry_checks(ctx, edge, policy, lim), r8, d1]
        labels = ctx.labels(edge, policy)
        for result in batch:
            if not result.count and not result.excluded:
                result.reject("no_eligible_observations")
            entries[result.rule].append(result)
            finding = materialize(result, identity, occurrence, labels)
            if finding:
                findings.append(finding)
    severity_order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (severity_order[f["severity"]], f["rule_id"] != "R4", f["rule_id"], f["finding_id"]))
    return dict(result_id=occurrence, generated_at=generated, environment_id=ctx.environment,
                ruleset_version=VERSION, model_version=model.get("model_version", "p2-v1"),
                dataset_ref=model.get("dataset_ref"), dataset_hash=data_hash, config_refs=config_refs,
                thresholds_version=limits.defaults.version, thresholds_hash=threshold_hash, baseline_refs=baseline_refs,
                sampling=sampling, observations=observations, findings=findings,
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
    detail = next((item for item in ev.get("by_edge", []) if item["edge_id"] == edge), None)
    if not detail or detail["status"] != "evaluated":
        return "insufficient_data"
    return ("symptom_present" if any(f["finding_id"] == previous_finding["finding_id"]
                                     for f in current_result["findings"]) else "symptom_absent")
