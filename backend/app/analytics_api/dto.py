from analytics_support import VERSION, timestamp, wire


def envelope(schema, payload):
    return {"schema": "zerotrace." + schema, "schema_version": "1.0.0",
            "producer_version": VERSION, "generated_at": timestamp(), "payload": wire(payload)}


def audit_view(result, *, include_policy=False):
    """Build the P7 audit view, including policy observations when requested."""
    findings, omissions = [], []
    for finding in result["findings"]:
        evidence = finding["evidence"]
        if not include_policy and finding["basis"] == "config_aware" and evidence.get("kind") != "retry_budget":
            omissions.append({"finding_id": finding["finding_id"], "reason": "full_evidence_in_state"})
            continue
        recommendations = finding["recommendations"]
        rec = next((r for r in recommendations if r.get("kind") == "primary"), None)
        if rec is None and recommendations:
            rec = recommendations[0]
        if rec is None:
            omissions.append({"finding_id": finding["finding_id"], "reason": "no_primary_recommendation"})
            continue
        if finding["basis"] == "config_aware" and evidence.get("kind") != "retry_budget":
            evidence = {**evidence, "kind": "policy_observation"}
        elif finding["basis"] != "config_aware":
            evidence = {**evidence, "kind": "trace_observation"}
        item = {k: finding[k] for k in ("finding_id", "occurrence_id", "rule_id", "rule_version",
                "title", "severity", "confidence", "basis", "edge", "explanation",
                "affected_count", "denominator_count", "exemplars")}
        item.update(evidence=evidence, recommendation_id=rec["recommendation_id"],
                    recommendation={k: rec.get(k, []) for k in ("text", "why", "risks", "changes")})
        item["recommendation"]["can_prepare_plan"] = bool(rec.get("intents"))
        findings.append(item)
    return {"result_id": result["result_id"], "generated_at": result["generated_at"],
            "findings": findings, "rule_evaluations": result["rule_evaluations"],
            "omitted_findings": omissions,
            **({"trace_findings": result["trace_findings"]} if include_policy else {})}
