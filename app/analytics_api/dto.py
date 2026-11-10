from analytics_support import VERSION, timestamp, wire


def envelope(schema, payload):
    return {"schema": "zerotrace." + schema, "schema_version": "1.0.0",
            "producer_version": VERSION, "generated_at": timestamp(), "payload": wire(payload)}


def audit_view(result):
    """Adapt P5 to the two evidence variants supported by the existing TS client."""
    findings, omissions = [], []
    for finding in result["findings"]:
        evidence = finding["evidence"]
        if finding["basis"] == "config_aware" and evidence.get("kind") != "retry_budget":
            omissions.append({"finding_id": finding["finding_id"], "reason": "full_evidence_in_state"})
            continue
        recommendations = finding["recommendations"]
        rec = next((r for r in recommendations if r.get("kind") == "primary"), None)
        if rec is None and recommendations:
            rec = recommendations[0]
        if rec is None:
            omissions.append({"finding_id": finding["finding_id"], "reason": "no_primary_recommendation"})
            continue
        if finding["basis"] != "config_aware":
            evidence = {"kind": "trace_observation", "source": evidence["source"]}
        item = {k: finding[k] for k in ("finding_id", "occurrence_id", "rule_id", "rule_version",
                "title", "severity", "confidence", "basis", "edge", "explanation",
                "affected_count", "denominator_count", "exemplars")}
        item.update(evidence=evidence, recommendation_id=rec["recommendation_id"],
                    recommendation={k: rec.get(k, []) for k in ("text", "why", "risks", "changes")})
        findings.append(item)
    return {"result_id": result["result_id"], "generated_at": result["generated_at"],
            "findings": findings, "rule_evaluations": result["rule_evaluations"],
            "omitted_findings": omissions}
