"""Descriptive comparison of two P10 cohorts in one repository snapshot."""
from analytics_support import AnalyticsError, digest, ref_key

from .metrics import VERSION, canonical, measure_trace, ratio, resolve, summarize


def _groups(request):
    all_ids = set()
    groups = []
    for name in ("baseline_trace_refs", "candidate_trace_refs"):
        refs = request.get(name)
        if not isinstance(refs, list) or not 1 <= len(refs) <= 100:
            raise AnalyticsError("invalid_cohort", f"{name} must contain 1 to 100 references")
        for ref in refs:
            # A newer revision of the same execution is still the same observation.
            trace = ref_key(ref)[:2]
            if trace in all_ids:
                raise AnalyticsError("overlapping_traces", "Each input trace must occur exactly once")
            all_ids.add(trace)
        groups.append(sorted(refs, key=ref_key))
    return groups


def compare(graphs, request, policy=None):
    """Pure P10 calculation. Graphs are normalized graphs/TraceDetail records from B."""
    if not isinstance(request, dict):
        raise AnalyticsError("invalid_request", "A cohort comparison object is required", 400)
    version = request.get("comparison_version", VERSION)
    if version != VERSION:
        raise AnalyticsError("unsupported_version", "Unsupported cohort comparison version")
    a_refs, b_refs = _groups(request)
    selector = request.get("entry_selector")
    if selector is not None and (not isinstance(selector, dict) or set(selector) - {"service", "operation"}):
        raise AnalyticsError("invalid_entry_selector", "Supported selectors are service and operation", 400)
    selected = resolve(graphs, a_refs + b_refs)
    rows = [measure_trace(graph, selector) for graph in selected]
    warnings = []
    exploratory = False
    for key, values in (("environment", [r["ref"]["environment_id"] for r in rows]),
                        ("project_id", [r["project_id"] for r in rows]),
                        ("workload_id", [r["workload_id"] for r in rows]),
                        ("service", [r["service"] for r in rows]), ("operation", [r["operation"] for r in rows])):
        known = {canonical(v) for v in values if v is not None and v != "unknown" and v != ""}
        if len(known) > 1:
            raise AnalyticsError("cohorts_not_comparable", f"Different {key} values", details={"field": key})
        if any(v is None or v in ("unknown", "") for v in values):
            exploratory = True
            warnings.append(f"unknown_{key}")
    keys = {key for row in rows for key in row["groups"]}
    a, b = summarize(rows[:len(a_refs)], keys), summarize(rows[len(a_refs):], keys)
    small_n = (policy or {}).get("p95_warning_below", 20)
    if type(small_n) is not int or small_n < 1:
        raise AnalyticsError("invalid_policy", "p95_warning_below must be a positive count")
    if min(a["coverage"]["root_latency_count"], b["coverage"]["root_latency_count"]) < small_n:
        warnings.append("small_sample_p95")
    if any(r["issues"] for r in rows):
        warnings.append("trace_quality_issues")
    if any(s not in ("all", "all_received") for samples in (a["sampling"], b["sampling"]) for s in samples
           if isinstance(s, str)) or any(isinstance(r["sampling"], dict) and
                                         r["sampling"].get("mode") not in ("all", "all_received") for r in rows):
        warnings.append("sampled_or_unknown_input")
    delta = None if None in (a["root_latency_p50_ns"], b["root_latency_p50_ns"]) else (
        b["root_latency_p50_ns"] - a["root_latency_p50_ns"])
    observations = []
    scope = {"baseline_example": a_refs[0], "candidate_example": b_refs[0]}
    for k in ("root_latency_p50_ns", "root_latency_p95_ns", "root_error_ratio", "internal_error_trace_ratio", "overlap_ratio"):
        if a[k] is not None and b[k] is not None:
            observations.append({"metric": k, "baseline": a[k], "candidate": b[k], **scope})
    for left, right in zip(a["direct_calls"], b["direct_calls"]):
        if left["median_calls_per_trace"] != right["median_calls_per_trace"]:
            observations.append({"metric": "median_calls_per_trace", "operation": left["operation"],
                                 "edge_id": left["edge_id"], "baseline": left["median_calls_per_trace"],
                                 "candidate": right["median_calls_per_trace"], **scope})
    refs = {"baseline": a_refs, "candidate": b_refs}
    return {"algorithm_version": VERSION, "input_refs": refs,
            "input_hash": digest({"refs": refs, "selector": selector, "policy": policy, "version": version}),
            "status": "available", "comparability": "exploratory" if exploratory else "comparable",
            "operation": rows[0]["operation"], "baseline": a, "candidate": b,
            "delta_ns": delta, "delta_ratio": ratio(delta, a["root_latency_p50_ns"]) if delta is not None else None,
            "observations": observations, "warnings": warnings}
