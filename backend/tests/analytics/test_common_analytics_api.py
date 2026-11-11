"""Exercise C through the Flask application used by the product UI."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.server.application import create_app
from contracts import IngestLimits, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize


@pytest.fixture
def app(tmp_path):
    app = create_app({
        "database": str(tmp_path / "product.db"),
        "state_directory": str(tmp_path),
        "token_directory": str(tmp_path / "tokens"),
        "environments": [{"environment_id": "shop", "kind": "external"},
                         {"environment_id": "sandbox", "kind": "lab"}],
    })
    yield app
    app.extensions["lab"].shutdown()
    app.extensions["records"].close()
    app.extensions["telemetry"].close()


def ingest(app, *trace_ids):
    payload = (Path(__file__).resolve().parents[2] / "fixtures/otlp/s01_retry.json").read_bytes()
    refs = []
    for trace_id in trace_ids:
        raw = decode_otlp(payload.replace(b"b" * 32, trace_id.encode()), "application/json", None,
                          IngestLimits())
        batch = normalize(raw, SourceProfile(clock_domain="shop-host"), RedactionProfile())
        accepted = app.extensions["telemetry"].ingest(batch, SourceBinding("src", "p:shop", "shop"), 1000)
        refs.append(accepted.trace_refs[0].to_dict())
    return refs


def payload(response):
    assert response.status_code == 200, response.get_data(as_text=True)
    return response.json["payload"]


def test_registered_empty_environments_have_an_audit_and_report(app):
    client = app.test_client()
    for env in ("shop", "sandbox"):
        audit = payload(client.get("/api/v1/audit", query_string={"environment_id": env}))
        assert not audit["findings"]
        assert all(rule["eligible_count"] == 0 for rule in audit["rule_evaluations"])
        report = payload(client.get("/api/v1/report", query_string={"environment_id": env}))
        assert report["snapshot"]["environment_id"] == env
        assert report["snapshot"]["trace_refs"] == []
    assert client.get("/api/v1/audit?environment_id=absent").status_code == 404
    assert client.get("/api/v1/audit?view=unrecognized").status_code == 400


def test_repeated_state_preserves_snapshot_and_report(app):
    client = app.test_client()
    with patch("rules.audit.datetime") as clock:
        clock.now.return_value = datetime(2026, 10, 25, tzinfo=UTC)
        first = payload(client.get("/api/v1/state"))
        query = {"snapshot_id": first["snapshot_id"]}
        report = payload(client.get("/api/v1/report", query_string=query))

        clock.now.return_value = datetime(2026, 10, 26, tzinfo=UTC)
        repeated = payload(client.get("/api/v1/state"))
        assert repeated == first
        assert payload(client.get("/api/v1/report", query_string=query)) == report

        ingest(app, "1" * 32)
        updated = payload(client.get("/api/v1/state"))
        assert updated["snapshot_id"] != first["snapshot_id"]
        assert updated["generated_at"] == clock.now.return_value.isoformat()
        assert payload(client.get("/api/v1/report", query_string=query)) == report


def test_list_and_detail_include_occurrences_beyond_audit_exemplars(app):
    refs = ingest(app, *(f"{i:032x}" for i in range(1, 8)))
    client = app.test_client()
    audit = payload(client.get("/api/v1/audit?view=full"))
    retries = next(f for f in audit["findings"] if f["rule_id"] == "OBS_RETRIES")
    assert retries["affected_count"] == 7 and len(retries["exemplars"]) == 5
    ref = next(ref for ref in refs if ref not in retries["exemplars"])
    summaries = payload(client.get("/api/v1/traces"))["items"]
    assert all(row["finding_count"] >= 2 for row in summaries)
    detail = payload(client.get("/api/v1/traces/" + ref["trace_id"], query_string={
        "environment_id": ref["environment_id"], "generation": ref["generation"], "revision": ref["revision"],
    }))
    assert retries["occurrence_id"] in detail["finding_occurrence_ids"]
    assert detail["summary"] in summaries
    assert detail["summary"]["finding_count"] == len(detail["finding_occurrence_ids"])


def test_product_can_attach_projection_and_trace_cohort_then_load_frozen_report(app):
    left, right = ingest(app, "1" * 32, "2" * 32)
    client = app.test_client()
    projection = payload(client.post("/api/v1/projections", json={
        "trace_ref": left, "changes": [{"span_id": "0000000000000002", "speedup_percent": 50}],
    }))
    cohorts = {"baseline_trace_refs": [left], "candidate_trace_refs": [right]}
    comparison = payload(client.post("/api/v1/comparisons", json=cohorts))
    assert projection["status"] == comparison["status"] == "available"
    mixed = client.post("/api/v1/comparisons", json={**cohorts, "baseline_run_id": "run"})
    assert mixed.status_code == 400 and mixed.json["error"]["code"] == "invalid_request"

    records = app.extensions["records"]
    for env in ("shop", "sandbox"):
        records.put("operation", env, {"operation_id": env, "environment_id": env,
                    "requested_at": "2026-10-25T00:00:00Z", "status": "applied", "verification": None})
    snapshot = payload(client.get("/api/v1/state"))["snapshot_id"]
    report = payload(client.get("/api/v1/report", query_string={"snapshot_id": snapshot,
                "projection_id": projection["analysis_id"], "comparison_id": comparison["analysis_id"]}))
    assert report["projection"]["status"] == report["comparison"]["status"] == "available"
    assert [entry["operation_id"] for entry in report["journal_refs"]] == ["shop"]
    records.put("operation", "shop", {"operation_id": "shop", "environment_id": "shop",
                "requested_at": "2026-10-25T00:00:00Z", "status": "failed", "verification": None})
    saved = "/api/v1/reports/" + report["report_id"]
    assert payload(client.get(saved)) == report
    assert client.get(saved + "?environment_id=sandbox").status_code == 404
    assert client.get(saved + "?format=html").mimetype == "text/html"
    assert client.get("/api/v1/report?format=yaml").status_code == 400
    assert client.get("/api/v1/report?projection_id=missing").status_code == 404


def test_trace_membership_and_full_audit_use_the_live_config(app, monkeypatch):
    ref, = ingest(app, "1" * 32)
    registry = app.extensions["registry"]
    configs = {
        "orders": {"service_id": "orders", "revision": "o1", "calls": [{
            "call_ref": "charge", "routes": ["GET /checkout"],
            "target": {"service_id": "payment", "method": "POST", "route": "/pay"},
            "timeout_ms": 250, "max_attempts": 3, "backoff_ms": 50, "retry_on": ["503"],
        }]},
        "gateway": {"service_id": "gateway", "revision": "g1", "calls": [{
            "call_ref": "checkout", "target": {"service_id": "orders", "method": "GET", "route": "/checkout"},
            "timeout_ms": 100,
        }]},
    }
    for service_id in configs:
        registry.register_service({"service_id": service_id, "environment_id": "shop", "control_url": "test"})
    monkeypatch.setattr("app.server.application.read_config", lambda service: configs[service["service_id"]])
    client = app.test_client()
    short = payload(client.get("/api/v1/audit"))
    full = payload(client.get("/api/v1/audit?view=full"))
    inversion = next(f for f in full["findings"] if f["rule_id"] == "R8")
    assert inversion["evidence"]["kind"] == "policy_observation"
    assert inversion["finding_id"] in {f["finding_id"] for f in short["omitted_findings"]}
    detail = payload(client.get("/api/v1/traces/" + ref["trace_id"]))
    assert inversion["occurrence_id"] in detail["finding_occurrence_ids"]
    configs["gateway"].update(revision="g2")
    configs["gateway"]["calls"][0]["timeout_ms"] = 1000
    updated = payload(client.get("/api/v1/traces/" + ref["trace_id"]))
    assert inversion["occurrence_id"] not in updated["finding_occurrence_ids"]
