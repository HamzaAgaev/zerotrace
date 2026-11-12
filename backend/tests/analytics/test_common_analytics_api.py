"""Exercise C through the Flask application used by the product UI."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from unittest.mock import patch

import pytest

from app.analytics_api.repository_source import _TraceCache
from app.server.application import create_app
from contracts import IngestLimits, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from ingest.normalize import NormalizedBatch


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
    app.extensions["audit_jobs"].close()
    app.extensions["lab"].shutdown()
    app.extensions["records"].close()
    app.extensions["telemetry"].close()


def ingest(app, *trace_ids, method=b"GET"):
    payload = (Path(__file__).resolve().parents[2] / "fixtures/otlp/s01_retry.json").read_bytes()
    payload = payload.replace(b'"stringValue": "GET"', b'"stringValue": "' + method + b'"', 1)
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


def test_read_snapshot_keeps_exact_revision_without_blocking_ingest(app):
    first, = ingest(app, "1" * 32)
    store = app.extensions["telemetry"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store.read_snapshot():
            old = store.resolve_trace("shop", first["trace_id"])
            late = replace(store.get_spans(old.trace_ref)[0], span_id="f" * 16)
            write = pool.submit(
                store.ingest, NormalizedBatch([late]), SourceBinding("src", "p:shop", "shop"), 2000,
            )
            current, = write.result(timeout=3).trace_refs
            assert current.revision > old.trace_ref.revision
            assert store.resolve_trace("shop", first["trace_id"]) == old
            assert len(store.get_spans(old.trace_ref)) == old.span_count
    assert store.resolve_trace("shop", first["trace_id"]).trace_ref == current


def test_trace_sample_can_skip_audit_and_config_reads(app, monkeypatch):
    ref, = ingest(app, "1" * 32)
    registry = app.extensions["registry"]
    registry.register_service({
        "service_id": "gateway", "environment_id": "shop", "control_url": "test",
    })

    def unexpected(*args):
        pytest.fail("A telemetry-only request must skip config and rules")

    monkeypatch.setattr("app.server.application.read_config", unexpected)
    monkeypatch.setattr("app.analytics_api.service.rules.run", unexpected)
    client = app.test_client()
    sample = payload(client.get("/api/v1/traces?include_findings=false"))
    assert sample["items"][0]["trace_ref"] == ref
    detail = payload(client.get("/api/v1/traces/" + ref["trace_id"], query_string={
        "include_findings": "false", "generation": ref["generation"], "revision": ref["revision"],
    }))
    assert detail["summary"]["trace_ref"] == ref
    assert "finding_occurrence_ids" not in detail


def test_telemetry_list_builds_graphs_only_for_the_page(app, monkeypatch):
    ok, = ingest(app, "1" * 32, method=b"PROPFIND")
    error, = ingest(app, "2" * 32)
    store = app.extensions["telemetry"]
    for ref, status in ((ok, 200), (error, 503)):
        meta = store.resolve_trace("shop", ref["trace_id"])
        source = store.get_spans(meta.trace_ref)[0]
        # The earlier root owns the method and outcome. An even earlier span
        # from another clock must not set the trace's start.
        root = replace(source, span_id="e" * 16, parent_span_id=None, kind="server",
                       start_ns=1_000, end_ns=1_010, duration_ns=10,
                       otel_status="unset", http_status=status)
        child = replace(root, span_id="f" * 16, parent_span_id=root.span_id,
                        start_ns=1, end_ns=11, clock_domain="other")
        store.ingest(NormalizedBatch([root, child]), SourceBinding("src", "p:shop", "shop"), 2000)

    monkeypatch.setattr("app.data_api.handlers.time_ns", lambda: 2_000)
    original = store.get_spans
    reads = []

    def get_spans(ref):
        reads.append(ref.trace_id)
        return original(ref)

    monkeypatch.setattr(store, "get_spans", get_spans)
    client = app.test_client()
    query = ("?include_findings=false&include_success_rates=true&sort=slow&limit=1"
             "&method=PROPFIND&start_from_ns=1000&start_to_ns=1001")
    page = payload(client.get("/api/v1/traces" + query))
    assert page["total"] == 1 and page["methods"] == ["GET", "PROPFIND"]
    assert page["outcomes"] == {"ok": 1, "error": 0, "unknown": 0}
    assert page["success_rates"] == dict.fromkeys(("5", "30", "60", "180"), 0.5)
    assert reads == [ok["trace_id"]]
    detail = payload(client.get("/api/v1/traces/" + ok["trace_id"] + "?include_findings=false"))
    assert page["items"] == [detail["summary"]]

    scan = store.trace_list_spans
    scans = []

    def trace_list_spans(environment_id, metas):
        scans.append([meta.trace_ref.trace_id for meta in metas])
        return scan(environment_id, metas)

    monkeypatch.setattr(store, "trace_list_spans", trace_list_spans)
    assert payload(client.get("/api/v1/traces" + query)) == page
    assert scans == []
    meta = store.resolve_trace("shop", error["trace_id"])
    new_root = replace(original(meta.trace_ref)[0], span_id="d" * 16, parent_span_id=None,
                       start_ns=500, end_ns=510, http_status=200)
    store.ingest(NormalizedBatch([new_root]), SourceBinding("src", "p:shop", "shop"), 3000)
    updated = payload(client.get("/api/v1/traces" + query))
    assert updated["success_rates"] == dict.fromkeys(("5", "30", "60", "180"), 1)
    assert updated["items"] == page["items"]
    assert scans == [[error["trace_id"]]]


def test_trace_card_can_omit_span_kind_without_changing_analytics_payload(app):
    ref, = ingest(app, "1" * 32)
    client = app.test_client()
    path = "/api/v1/traces/" + ref["trace_id"]
    query = {"generation": ref["generation"], "revision": ref["revision"], "include_findings": "false"}
    full = payload(client.get(path, query_string=query))
    compact = payload(client.get(path, query_string={**query, "include_span_kind": "false"}))
    assert full["spans"] and all("kind" in span for span in full["spans"])
    assert compact == {**full, "spans": [
        {key: value for key, value in span.items() if key != "kind"} for span in full["spans"]
    ]}


def test_deferred_audit_can_be_polled_without_waiting_for_rules(app, monkeypatch):
    ingest(app, "1" * 32)
    from app.analytics_api.service import rules

    original = rules.run
    release = Event()

    def slow_rules(*args):
        assert release.wait(timeout=5)
        return original(*args)

    monkeypatch.setattr(rules, "run", slow_rules)
    client = app.test_client()
    path = "/api/v1/audit?environment_id=shop&view=full&defer=true"
    try:
        first = client.get(path)
        assert first.status_code == 202
        job_id = first.json["payload"]["job_id"]
        assert client.get(path).json["payload"]["job_id"] == job_id
        assert client.get(path + "&job_id=" + job_id).status_code == 202
        assert client.get(path.replace("environment_id=shop", "environment_id=sandbox")
                          + "&job_id=" + job_id).status_code == 404
    finally:
        release.set()
    app.extensions["audit_jobs"].close()
    result = payload(client.get(path + "&job_id=" + job_id))
    assert result == payload(client.get("/api/v1/audit?environment_id=shop&view=full"))


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
    page = payload(client.get("/api/v1/traces?query=checkout&sort=slow&limit=1&offset=6"))
    assert page["total"] == 7 and len(page["items"]) == 1
    assert page["items"][0]["finding_count"] >= 2
    assert sum(page["outcomes"].values()) == 7
    empty = payload(client.get("/api/v1/traces?query=does-not-exist&sort=recent"))
    assert empty["total"] == 0 and empty["items"] == []
    detail = payload(client.get("/api/v1/traces/" + ref["trace_id"], query_string={
        "environment_id": ref["environment_id"], "generation": ref["generation"], "revision": ref["revision"],
    }))
    assert retries["occurrence_id"] in detail["finding_occurrence_ids"]
    assert detail["summary"] in summaries
    assert detail["summary"]["finding_count"] == len(detail["finding_occurrence_ids"])


def test_trace_method_filter_distinguishes_requests_to_the_same_endpoint(app):
    get_ref, = ingest(app, "1" * 32)
    post_refs = ingest(app, "2" * 32, "3" * 32, method=b"POST")
    ingest(app, "4" * 32, method=b"PROPFIND")
    client = app.test_client()
    traces = "/api/v1/traces"
    page = payload(client.get(traces + "?query=/checkout&method=post&limit=1&offset=1"))
    assert page["total"] == 2
    assert page["methods"] == ["GET", "POST", "PROPFIND"]
    assert sum(page["outcomes"].values()) == 2
    row, = page["items"]
    assert row["trace_ref"] == post_refs[1]
    assert (row["http_method"], row["endpoint"], row["operation"]) == (
        "POST", "/checkout", "POST /checkout",
    )

    get_page = payload(client.get(traces + "?query=/checkout&method=GET"))
    assert [item["trace_ref"] for item in get_page["items"]] == [get_ref]
    assert payload(client.get(traces + "?query=/checkout&method=PUT"))["total"] == 0
    error = client.get(traces + "?method=GET%20POST")
    assert error.status_code == 400 and error.json["error"]["code"] == "invalid_method"


def test_trace_search_by_span_and_start_time(app):
    first, second = ingest(app, "1" * 32, "2" * 32)
    client = app.test_client()
    traces = "/api/v1/traces"
    start = int(payload(client.get(traces))["items"][0]["start_ns"])
    shared_span = "0000000000000001"
    filters = {"span_id": shared_span, "start_from_ns": start, "start_to_ns": start + 1}
    result = payload(client.get(traces, query_string={**filters, "method": "GET", "limit": 1, "offset": 1}))
    assert result["total"] == 2
    assert sum(result["outcomes"].values()) == 2
    assert result["items"][0]["trace_ref"] == second
    assert payload(client.get(traces, query_string={"start_to_ns": start}))["total"] == 0
    assert payload(client.get(traces, query_string={"start_from_ns": start + 1}))["total"] == 0

    store = app.extensions["telemetry"]
    meta = next(meta for meta in store.list_trace_metas("shop") if meta.trace_ref.to_dict() == first)
    root = store.get_spans(meta.trace_ref)[0]
    late = replace(root, span_id="f" * 16, parent_span_id=root.span_id)
    store.ingest(NormalizedBatch([late]), SourceBinding("src", "p:sandbox", "sandbox"), 2000)
    assert payload(client.get(traces, query_string={"span_id": late.span_id}))["total"] == 0
    new_ref, = store.ingest(NormalizedBatch([late]), SourceBinding("src", "p:shop", "shop"), 3000).trace_refs
    result = payload(client.get(traces, query_string={**filters, "span_id": late.span_id}))
    assert [item["trace_ref"] for item in result["items"]] == [new_ref.to_dict()]
    for params, code in (
        ({"span_id": "0000000000000000"}, "invalid_span_id"),
        ({"start_from_ns": "-1"}, "invalid_start_from_ns"),
        ({"start_from_ns": start, "start_to_ns": start}, "invalid_time_range"),
    ):
        error = client.get(traces, query_string=params)
        assert error.status_code == 400 and error.json["error"]["code"] == code


def test_trace_pages_reuse_derived_data_and_refresh_changed_revisions(app, monkeypatch):
    ref, unchanged = ingest(app, "1" * 32, "2" * 32)
    store = app.extensions["telemetry"]
    real_get_spans = store.get_spans
    reads = []

    def get_spans(trace_ref):
        reads.append(trace_ref.to_dict())
        return real_get_spans(trace_ref)

    monkeypatch.setattr(store, "get_spans", get_spans)
    client = app.test_client()
    path = "/api/v1/traces?sort=recent&limit=1"
    first = payload(client.get(path))
    assert first["total"] == 2
    assert first["items"][0]["trace_ref"] == ref
    assert reads == [ref, unchanged]

    second = payload(client.get(path + "&offset=1"))
    assert second["items"][0]["trace_ref"] == unchanged
    assert reads == [ref, unchanged]

    original_ref = next(meta.trace_ref for meta in store.list_trace_metas("shop")
                        if meta.trace_ref.to_dict() == ref)
    late = replace(real_get_spans(original_ref)[0], span_id="f" * 16)
    new_ref, = store.ingest(NormalizedBatch([late]), SourceBinding("src", "p:shop", "shop"),
                            2000).trace_refs
    updated = payload(client.get(path))
    assert updated["items"][0]["trace_ref"] == new_ref.to_dict()
    assert updated["items"][0]["span_count"] == first["items"][0]["span_count"] + 1
    assert reads == [ref, unchanged, new_ref.to_dict()]


def test_trace_scan_reuses_data_when_shared_cache_is_full(app, monkeypatch):
    refs = ingest(app, *(f"{index:032x}" for index in range(1, 5)))
    monkeypatch.setattr(_TraceCache, "max_entries", 2)
    store = app.extensions["telemetry"]
    original = store.get_spans
    reads = []

    def get_spans(ref):
        reads.append(ref.to_dict())
        return original(ref)

    monkeypatch.setattr(store, "get_spans", get_spans)
    client = app.test_client()
    path = "/api/v1/traces?sort=recent&limit=1"
    assert payload(client.get(path))["total"] == 4
    assert reads == refs
    reads.clear()
    assert payload(client.get(path + "&offset=1"))["items"][0]["trace_ref"] == refs[1]
    assert reads == refs[:2]


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


def test_trace_audit_only_analyzes_exact_selected_trace(app, monkeypatch):
    refs = ingest(app, '1' * 32, '2' * 32)
    from app.analytics_api.service import rules
    original = rules.run
    seen = []

    def record(model, *args):
        seen.append([t['trace_ref'] for t in model['traces']])
        return original(model, *args)

    monkeypatch.setattr(rules, 'run', record)
    client = app.test_client()
    query = {**refs[0], 'view': 'full', 'defer': 'true'}
    response = client.get('/api/v1/audit', query_string=query)
    job = response.json['payload'].get('job_id')
    if job:
        app.extensions['audit_jobs'].close()
        response = client.get('/api/v1/audit', query_string={**query, 'job_id': job})
        assert client.get('/api/v1/audit', query_string={**query, 'trace_id': refs[1]['trace_id'], 'job_id': job}).status_code == 404
    result = payload(response)
    assert seen == [[refs[0]]]
    assert 'trace_findings' in result
    assert all(row['trace_ref'] == refs[0] for row in result['trace_findings'])
    payload(client.get('/api/v1/audit', query_string={'environment_id': 'shop', 'view': 'full'}))
    assert len(seen[-1]) == 2
