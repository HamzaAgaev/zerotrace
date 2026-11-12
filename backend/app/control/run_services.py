"""Audit findings for the exact trace revisions and configurations of one test."""
from app.analytics_api.dto import audit_view


def service_results(analytics, run):
    environment = run["environment_id"]
    refs = run.get("trace_collection", {}).get("trace_refs", [])
    with analytics.configured(environment, run.get("config_snapshots")) as service:
        with analytics.source.read() as view:
            analyzed, missing = view.restrict_audit_to_traces(environment, refs)
            result = audit_view(service.audit(environment), include_policy=True)
    # The full occurrence index includes every affected trace, not just exemplars.
    linked = {}
    for row in result["trace_findings"]:
        for occurrence in row["occurrence_ids"]:
            linked.setdefault(occurrence, []).append(row["trace_ref"])
    return {"analyzed_traces": analyzed, "missing_traces": missing,
            "findings": [{**finding, "trace_refs": linked.get(finding["occurrence_id"], [])}
                         for finding in result["findings"]],
            "rule_evaluations": result["rule_evaluations"]}
