from copy import deepcopy
from dataclasses import replace

import pytest

from analytics_support import AnalyticsError
from app.analytics_api.facade import AnalyticsFacade
from app.control import plans as plans_module
from app.integrations import http_config
from app.server.application import create_app
from contracts import SourceBinding
from repository import SpanStore
from tests.analytics.test_repository_source import decoded_fixture

ENV = "sandbox"
BINDING = SourceBinding("agent", "project:sandbox", ENV)


def configs():
    orders = {
        "service_id": "orders",
        "revision": "o1",
        "is_lab": True,
        "capabilities": {"apply_recommendation": True},
        "calls": [
            {
                "call_ref": "charge",
                "routes": ["GET /checkout"],
                "target": {"service_id": "payment", "method": "POST", "route": "/pay"},
                "max_attempts": 3,
                "timeout_ms": 250,
                "backoff_ms": 50,
                "write_fields": ["max_attempts"],
            }
        ],
    }
    gateway = {
        "service_id": "gateway",
        "revision": "g1",
        "is_lab": True,
        "calls": [
            {
                "call_ref": "checkout",
                "target": {"service_id": "orders", "method": "GET", "route": "/checkout"},
                "timeout_ms": 700,
            }
        ],
    }
    return {"orders": orders, "gateway": gateway}


def ingest(
    store, *, trace_id="b" * 32, namespace="default", operation="GET /checkout", clock="host"
):
    packet = decoded_fixture(clock_domain=clock)
    spans = [
        replace(
            span,
            trace_id=trace_id,
            namespace=namespace,
            operation=operation if span.kind == "server" else span.operation,
        )
        for span in packet.spans
    ]
    store.ingest(replace(packet, spans=spans), BINDING, 10)


@pytest.fixture
def facade(tmp_path):
    with SpanStore(tmp_path / "data.db") as store:
        ingest(store)
        result = AnalyticsFacade(store, {"environments": {ENV: {}}})
        yield result
        result.results.close()


def findings(facade, policies, rule):
    return [row for row in facade.audit(ENV, policies)["findings"] if row["rule_id"] == rule]


def identifiers(finding):
    return finding["occurrence_id"], finding["recommendations"][0]["recommendation_id"]


def test_bind_only_to_the_call_reached_from_the_observed_route(facade):
    policies = configs()
    calls = policies["orders"]["calls"]
    charge = calls.pop()
    refund = {**charge, "call_ref": "refund", "routes": ["GET /refund"]}
    calls.append(refund)
    assert not findings(facade, policies, "R4")
    calls.append(charge)
    (finding,) = findings(facade, policies, "R4")
    intent = facade.resolve_recommendation(ENV, *identifiers(finding), policies)
    assert intent["target"] == {"service_id": "orders", "call_ref": "charge"}
    refund["routes"] = ["GET /checkout", "GET /refund"]
    assert not findings(facade, policies, "R4")
    with pytest.raises(AnalyticsError, match="current audit"):
        facade.resolve_recommendation(ENV, *identifiers(finding), policies)


def test_legacy_snapshot_with_route_membership(facade):
    policies = configs()
    orders = policies["orders"]
    (call,) = orders["calls"]
    call.pop("routes")
    call["route"] = "GET /refund"
    orders["routes"] = [
        {"route": "GET /refund", "calls": {"charge": {}}},
        {"route": "GET /checkout", "calls": {}},
    ]
    assert not findings(facade, policies, "R4")


def test_scope_is_ambiguous_across_namespaces(facade):
    ingest(facade.source.store, trace_id="c" * 32, namespace="other")
    policies = configs()
    assert not findings(facade, policies, "R4")
    policies["orders"]["namespace"] = "default"
    (finding,) = findings(facade, policies, "R4")
    assert (
        facade.resolve_recommendation(ENV, *identifiers(finding), policies)["target"]["call_ref"]
        == "charge"
    )


def test_unknown_clocks_bind_via_parent_but_mixed_routes_do_not_overwrite_each_other(tmp_path):
    with SpanStore(tmp_path / "data.db") as store:
        ingest(store, clock=None)
        facade = AnalyticsFacade(store, {"environments": {ENV: {}}})
        try:
            assert findings(facade, configs(), "R4")
            ingest(store, trace_id="c" * 32, operation="GET /refund", clock=None)
            assert not findings(facade, configs(), "R4")
        finally:
            facade.results.close()


def test_resolution_rechecks_upstream_operands_and_permissions(facade):
    policies = configs()
    (finding,) = findings(facade, policies, "R4")
    ids = identifiers(finding)
    assert finding["evidence"]["required_ns"] == "850000000"
    assert finding["evidence"]["budget_ns"] == "700000000"
    ingest(facade.source.store, trace_id="c" * 32)
    assert facade.resolve_recommendation(ENV, *ids, policies)["set"] == {"max_attempts": 2}
    policies["orders"]["calls"][0]["write_fields"] = []
    with pytest.raises(AnalyticsError) as exc:
        facade.resolve_recommendation(ENV, *ids, policies)
    assert (exc.value.status, exc.value.code) == (409, "stale_recommendation")
    policies = configs()
    for new_budget in (650, 1000):
        policies["gateway"]["calls"][0]["timeout_ms"] = new_budget
        with pytest.raises(AnalyticsError) as exc:
            facade.resolve_recommendation(ENV, *ids, policies)
        assert (exc.value.status, exc.value.code) == (409, "stale_recommendation")
    assert not findings(facade, policies, "R4")


def test_paired_timeout_is_separate_from_explicit_retry_budget(facade):
    policies = configs()
    policies["orders"]["calls"][0]["budget_ms"] = 700
    policies["gateway"]["calls"][0]["timeout_ms"] = 100
    (finding,) = findings(facade, policies, "R8")
    evidence = finding["evidence"]
    assert (evidence["outer_timeout_ns"], evidence["inner_timeout_ns"]) == (
        "100000000",
        "250000000",
    )
    assert evidence["upstream_config"] == {
        "service_id": "gateway",
        "call_ref": "checkout",
        "revision": "g1",
        "timeout_ms": 100,
    }
    policies.pop("gateway")
    assert not findings(facade, policies, "R8")
    assert findings(facade, policies, "R4")[0]["evidence"]["budget_ns"] == "700000000"


def test_observed_parent_cannot_take_another_service_timeout(tmp_path):
    with SpanStore(tmp_path / "data.db") as store:
        packet = decoded_fixture()
        root = next(span for span in packet.spans if span.parent_span_id is None)
        upstream = replace(
            root,
            span_id="f" * 16,
            kind="client",
            service="other-gateway",
            instance_id="gw-instance",
            target="orders",
            call_ref="checkout",
        )
        spans = [
            replace(span, parent_span_id=upstream.span_id) if span is root else span
            for span in packet.spans
        ]
        store.ingest(replace(packet, spans=[upstream, *spans]), BINDING, 10)
        facade = AnalyticsFacade(store, {"environments": {ENV: {}}})
        try:
            policies = configs()
            assert not findings(facade, policies, "R4")
            assert not findings(facade, policies, "R8")
            observed = {**policies["gateway"], "service_id": "other-gateway"}
            policies["other-gateway"] = observed
            (finding,) = findings(facade, policies, "R4")
            assert finding["evidence"]["budget_config"]["service_id"] == "other-gateway"
        finally:
            facade.results.close()


def test_common_api_rejects_prepared_plan_after_upstream_change(tmp_path, monkeypatch):
    policies = configs()
    app = create_app(
        {
            "database": str(tmp_path / "product.db"),
            "token_directory": str(tmp_path / "tokens"),
            "state_directory": str(tmp_path),
            "environments": [{"environment_id": ENV, "kind": "lab"}],
            "services": [
                {
                    "service_id": k,
                    "environment_id": ENV,
                    "control_url": "unused",
                    "is_lab": True,
                    "write_fields": ["max_attempts"],
                }
                for k in policies
            ],
        }
    )
    patches = []

    def adapter(service, method, path, *args):
        if method == "GET" and path == "/_config":
            return deepcopy(policies[service["service_id"]])
        patches.append((method, path))
        raise AssertionError("stale plan reached the configuration adapter")

    monkeypatch.setattr(http_config, "remote_json", adapter)
    monkeypatch.setattr(plans_module, "remote_json", adapter)
    monkeypatch.setattr(app.extensions["plans"], "lab_guard", lambda: None)
    try:
        ingest(app.extensions["telemetry"])
        client = app.test_client()
        audit = client.get("/api/v1/audit", query_string={"environment_id": ENV}).json["payload"]
        finding = next(row for row in audit["findings"] if row["rule_id"] == "R4")
        request = {
            key: finding[key] for key in ("finding_id", "occurrence_id", "recommendation_id")
        }
        request["environment_id"] = ENV
        created = client.post("/api/v1/change-plans", json=request)
        assert created.status_code == 201, created.json
        plan = created.json["payload"]
        policies["gateway"]["revision"] = "g2"
        policies["gateway"]["calls"][0]["timeout_ms"] = 1000
        attempt = client.post(
            "/api/v1/change-plans/" + plan["plan_id"] + "/apply",
            json={"plan_hash": plan["plan_hash"], "request_id": "stale-1"},
        )
        assert (attempt.status_code, attempt.json["error"]["code"]) == (409, "stale_recommendation")
        assert not app.extensions["records"].items("operation")
        assert not patches

        # Now accept a valid plan and let the same update happen between
        # admission and execution. No PATCH can leave the worker either.
        policies.update(configs())
        executor = app.extensions["plans"]
        execute = executor._execute
        monkeypatch.setattr(executor, "_execute", lambda _operation_id: None)
        accepted = client.post(
            "/api/v1/change-plans/" + plan["plan_id"] + "/apply",
            json={"plan_hash": plan["plan_hash"], "request_id": "accepted"},
        )
        assert accepted.status_code == 202, accepted.json
        operation_id = accepted.json["payload"]["operation_id"]
        policies["gateway"]["revision"] = "g2"
        policies["gateway"]["calls"][0]["timeout_ms"] = 1000
        execute(operation_id)
        operation = app.extensions["records"].get("operation", operation_id)
        assert (operation["status"], operation["error"]["code"]) == (
            "rejected",
            "stale_recommendation",
        )
        assert not patches
    finally:
        app.extensions["records"].close()
        app.extensions["telemetry"].close()
