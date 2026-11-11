"""Print a synthetic A/B input dataset. Observed counts are independent of config."""
import json
from pathlib import Path

MS = 1_000_000
EDGE = {"caller": "orders", "callee": "payment", "operation": "POST /pay"}
SAMPLES = 30


def policy(env, limit, revision):
    return {"edge_id": "orders-payment", "edge": EDGE, "service_id": "orders",
            "call_ref": "charge", "environment_id": env, "environment_kind": "lab", "source": "runtime",
            "config_revision": revision, "execution": "serial", "budget_ns": str(700 * MS),
            "per_attempt_timeout_ns": str(250 * MS), "max_attempts": limit,
            "backoff": {"kind": "fixed", "base_ns": str(50 * MS), "jitter": "none"},
            "capabilities": {"apply_recommendation": True, "write_fields": ["max_attempts"]}}


def trace(env, run_id, index, observed, status):
    ref = {"environment_id": env, "trace_id": f"{index:032x}", "generation": 1, "revision": 1}
    spans, attempts = [], []
    t = 0
    for i in range(1, observed + 1):
        t += MS if i == 1 else 50 * MS
        duration = (4 + index % 3) * MS
        client_id = f"{2*i:016x}"
        span = {"span_id": client_id, "parent_span_id": f"{1:016x}", "kind": "client",
                "name": "POST /pay", "service": "orders", "target": "payment",
                "clock_domain": "fixture", "outcome": "error" if status >= 400 else "ok",
                "http_status": status, "error_type": None, "start_offset_ns": str(t),
                "duration_ns": str(duration), "call_ref": "charge", "edge_id": "orders-payment",
                "logical_call_id": "call", "attempt_index": i}
        spans += [span, {**span, "kind": "server", "span_id": f"{2*i+1:016x}",
                         "parent_span_id": client_id, "service": "payment"}]
        attempts.append({"trace_ref": ref, "attempt_id": client_id, "call_id": "call", "attempt_index": i,
                         "edge_id": "orders-payment", "edge": EDGE, "duration_ns": str(duration),
                         "outcome": "http_error" if status >= 400 else "ok", "client_http_status": status})
        t += duration
    root = {"span_id": f"{1:016x}", "parent_span_id": None, "kind": "server", "name": "POST /orders",
            "service": "orders", "clock_domain": "fixture", "run_id": run_id, "http_status": status,
            "outcome": "error" if status >= 400 else "ok", "error_type": None,
            "start_offset_ns": "0", "duration_ns": str(t + MS)}
    call = {"trace_ref": ref, "call_id": "call", "edge_id": "orders-payment", "edge": EDGE,
            "attempt_ids": [x["attempt_id"] for x in attempts], "hypothesis": "retry" if observed > 1 else "single",
            "confidence": "high", "evidence_codes": ["explicit_retry"] if observed > 1 else ["explicit_single"]}
    return {"trace_ref": ref, "spans": [root, *spans], "quality_issues": [],
            "project_id": "demo", "workload_id": "steady", "service_version": "1",
            "summary": {"trace_ref": ref, "trace_id": ref["trace_id"], "service": "orders",
                        "operation": root["name"], "outcome": root["outcome"], "start_ns": "0",
                        "duration_ns": root["duration_ns"], "http_status": status,
                        "span_count": 1+len(spans), "service_count": 2, "finding_count": 0, "quality_issues": []},
            "attempts": attempts, "calls": [call]}


def environment(env, samples, policies, *, kind="lab"):
    return {"model": {"environment_id": env, "environment_kind": kind, "generated_at": "2026-11-10T12:00:00Z",
                      "dataset_ref": f"{env}-fixture-v1", "traces": samples,
                      "sampling": {"mode": "all", "source": "synthetic_fixture"},
                      "quality": {"source": "synthetic_fixture"},
                      "calls": [c for t in samples for c in t["calls"]],
                      "attempts": [a for t in samples for a in t["attempts"]],
                      "normalization_version": "fixture-v1", "services": ["orders", "payment"],
                      "edges": {"orders-payment": EDGE}}, "policies": policies}


def run(run_id, traces, max_attempts, revision):
    return {"run_id": run_id, "stand_id": "stand-1", "environment_id": "lab", "status": "completed",
            "scenario_revision": 1, "fault_revision": 1, "load": {"count": SAMPLES, "concurrency": 1},
            "config_revision": revision,
            "config_snapshot": {"service_id": "orders", "revision": revision, "edges": [
                {"call_ref": "charge", "edge_id": "orders-payment", "max_attempts": max_attempts,
                 "per_attempt_timeout_ns": str(250 * MS), "backoff": {"kind": "fixed", "base_ns": str(50 * MS)}}]},
            "trace_collection": {"status": "ready", "received_count": len(traces), "expected_count": SAMPLES,
                                 "trace_refs": [t["trace_ref"] for t in traces]}}


def dataset(after_attempts=2):
    before = [trace("lab", "base", i+1, 3, 503) for i in range(SAMPLES)]
    after = [trace("lab", "candidate", i+101, after_attempts, 503) for i in range(SAMPLES)]
    healthy = [trace("healthy", "ok", i+201, 1, 200) for i in range(5)]
    return {"environments": {"lab": environment("lab", before, [policy("lab", 3, "12")]),
                             "healthy": environment("healthy", healthy, [policy("healthy", 2, "13")]),
                             "unknown": environment("unknown", [], [], kind="external")},
            "runs": {"base": run("base", before, 3, "12"), "candidate": run("candidate", after, 2, "13")},
            "historical_traces": after}


def audit_examples():
    from app.analytics_api.dto import audit_view, envelope
    from app.analytics_api.provider import FixtureSource
    from app.analytics_api.service import AnalyticsService
    from app.analytics_api.storage import LocalResultStore
    store = LocalResultStore()
    try:
        service = AnalyticsService(FixtureSource(dataset()), store)
        for environment_id, name in (("lab", "budget"), ("healthy", "healthy"), ("unknown", "insufficient")):
            output = envelope("audit", audit_view(service.audit(environment_id)))
            output["generated_at"] = output["payload"]["generated_at"]
            yield f"audit-{name}.json", output
    finally:
        store.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-audit-examples", action="store_true")
    if parser.parse_args().write_audit_examples:
        for name, data in audit_examples():
            Path(__file__).with_name(name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8")
    else:
        print(json.dumps(dataset(), ensure_ascii=False, separators=(",", ":")))
