from __future__ import annotations

import json
from pathlib import Path

import pytest

from stand.scenario import ScenarioError, load_scenario, route_boundaries
from stand.service import ConfigRejected, ServiceRuntime, ServiceState, _request_key, create_app

SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "checkout.json"


def make_client(tmp_path: Path, service_id: str = "payment", write_enabled: bool | None = None):
    app = create_app(SCENARIO, service_id, tmp_path, {"fraud": "http://fraud"}, write_enabled)
    app.testing = True
    return app.test_client()


def test_checkout_scenario_has_nine_services_and_parallel_order_branch() -> None:
    scenario = load_scenario(SCENARIO)
    assert len(scenario["services"]) == 9
    order = scenario["services"]["orders"]["routes"]["POST /orders"]
    assert order["steps"][0]["parallel"] == ["inventory-stock", "pricing-quote", "delivery-quote"]
    assert route_boundaries(order)[1]["boundary_id"] == "after-prepare"


def test_config_patch_is_atomic_idempotent_and_requires_current_revision(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    before = client.get("/_config").get_json()
    call = next(item for item in before["calls"] if item["call_ref"] == "bank-authorize")
    assert call["max_attempts"] == 2
    body = {
        "request_id": "change-42",
        "expected_revision": before["revision"],
        "call_ref": "bank-authorize",
        "set": {"timeout_ms": 500, "max_attempts": 3},
    }
    applied = client.patch("/_config", json=body)
    assert applied.status_code == 200
    assert applied.get_json()["new_revision"] == "2"
    assert client.patch("/_config", json=body).get_json() == applied.get_json()
    assert client.patch("/_config", json={**body, "request_id": "stale", "set": {"max_attempts": 4}}).status_code == 409
    after = client.get("/_config").get_json()
    actual = next(item for item in after["calls"] if item["call_ref"] == "bank-authorize")
    assert actual["timeout_ms"] == 500 and actual["max_attempts"] == 3


def test_config_idempotency_survives_service_restart(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    body = {
        "request_id": "restart-safe",
        "expected_revision": "1",
        "call_ref": "bank-authorize",
        "set": {"max_attempts": 3},
    }
    result = client.patch("/_config", json=body).get_json()
    restarted = make_client(tmp_path)
    assert restarted.get("/_config/operations/restart-safe").get_json() == result
    assert restarted.patch("/_config", json=body).get_json() == result
    conflict = restarted.patch("/_config", json={**body, "set": {"max_attempts": 4}})
    assert conflict.status_code == 409


def test_noop_config_patch_keeps_revision(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    before = client.get("/_config").get_json()
    call = next(item for item in before["calls"] if item["call_ref"] == "bank-authorize")
    result = client.patch(
        "/_config",
        json={
            "request_id": "no-op",
            "expected_revision": before["revision"],
            "call_ref": "bank-authorize",
            "set": {"timeout_ms": call["timeout_ms"]},
        },
    )
    assert result.status_code == 200
    assert result.get_json()["status"] == "no_op"
    assert result.get_json()["new_revision"] == before["revision"]


def test_shared_call_ref_is_deduplicated_and_patched_everywhere(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO)
    payment = scenario["services"]["payment"]
    shared_call = payment["routes"]["POST /charge"]["calls"]["bank-authorize"]
    payment["routes"]["POST /charge-copy"] = {
        "work": {"kind": "fixed", "delay_ms": 0},
        "calls": {"bank-authorize": json.loads(json.dumps(shared_call))},
        "steps": [{"id": "bank", "call": "bank-authorize"}],
    }
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    client = create_app(scenario_path, "payment", tmp_path / "state").test_client()
    before = client.get("/_config").get_json()
    shared = [item for item in before["calls"] if item["call_ref"] == "bank-authorize"]
    assert len(shared) == 1
    assert set(shared[0]["routes"]) == {"POST /charge", "POST /charge-copy"}
    result = client.patch(
        "/_config",
        json={
            "request_id": "shared-policy",
            "expected_revision": before["revision"],
            "call_ref": "bank-authorize",
            "set": {"timeout_ms": 321},
        },
    )
    assert result.status_code == 200
    state = json.loads((tmp_path / "state" / "payment.json").read_text())
    for route in ("POST /charge", "POST /charge-copy"):
        assert state["config"]["routes"][route]["calls"]["bank-authorize"]["timeout_ms"] == 321


def test_scenario_rejects_conflicting_policy_for_same_operation(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO)
    payment = scenario["services"]["payment"]
    shared_call = json.loads(json.dumps(payment["routes"]["POST /charge"]["calls"]["bank-authorize"]))
    shared_call["timeout_ms"] += 1
    payment["routes"]["POST /charge-copy"] = {
        "work": {"kind": "fixed", "delay_ms": 0},
        "calls": {"other-ref": shared_call},
        "steps": [{"id": "bank", "call": "other-ref"}],
    }
    scenario_path = tmp_path / "conflict.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    with pytest.raises(ScenarioError, match="conflicting retry policies"):
        load_scenario(scenario_path)


def test_retry_categories_distinguish_http_status_from_transport_errors() -> None:
    allowed = ServiceRuntime._retry_allowed
    assert not allowed(504, ["timeout"])
    assert allowed(504, ["timeout"], "timeout")
    assert not allowed(502, ["connect"])
    assert allowed(502, ["5xx"])
    assert not allowed(502, ["5xx"], "connect")
    assert not allowed(504, ["5xx"], "timeout")


def test_direct_config_validation_rejects_bool_nonfinite_and_oversized_values() -> None:
    call = {"timeout_ms": 100, "max_attempts": 1, "backoff_ms": 0, "retry_on": ["5xx"]}
    for field, value in (
        ("timeout_ms", True),
        ("timeout_ms", float("inf")),
        ("timeout_ms", 3_600_001),
        ("max_attempts", True),
        ("backoff_ms", float("nan")),
        ("backoff_ms", 3_600_001),
        ("retry_on", ["any"]),
    ):
        with pytest.raises(ConfigRejected):
            ServiceState._apply_call_patch(call.copy(), {field: value})


def test_request_key_uses_seed_index_branch_and_attempt() -> None:
    from flask import Flask

    app = Flask(__name__)

    def key(index: str, branch: str = "", attempt: str = "1") -> str:
        with app.test_request_context(
            "/",
            headers={
                "X-ZeroTrace-Seed": "fixed-seed",
                "X-ZeroTrace-Request-Index": index,
                "X-ZeroTrace-Branch-Path": branch,
                "X-ZeroTrace-Attempt-Index": attempt,
            },
        ):
            return _request_key()

    assert key("12") == key("12")
    assert key("12") != key("13")
    assert key("12", "inventory-stock") != key("12", "pricing-quote")
    assert key("12", "inventory-stock", "1") != key("12", "inventory-stock", "2")


def test_route_preserves_configured_success_status_after_204_child(tmp_path: Path) -> None:
    scenario = load_scenario(SCENARIO)
    scenario["services"]["gateway"]["routes"]["POST /checkout"]["response_status"] = 201
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(scenario), encoding="utf-8")
    state = ServiceState(
        tmp_path / "state", "gateway", scenario["services"]["gateway"], False
    )
    runtime = ServiceRuntime(state, {})
    runtime._run_group = lambda _calls, refs, _seed, _key, _offsets=None: {ref: 204 for ref in refs}

    from flask import Flask

    app = Flask(__name__)
    with app.test_request_context(
        "/checkout",
        headers={"X-ZeroTrace-Request-Index": "1", "X-ZeroTrace-Seed": "seed"},
    ):
        assert runtime.handle_route("POST /checkout") == 201


def test_route_work_patch_and_fault_boundaries(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    revision = client.get("/_config").get_json()["revision"]
    patched = client.patch(
        "/_config",
        json={
            "request_id": "work",
            "expected_revision": revision,
            "route": "POST /charge",
            "set": {"work": {"kind": "fixed", "delay_ms": 0}},
        },
    )
    assert patched.status_code == 200
    fault = client.put(
        "/_chaos",
        json={
            "route": "POST /charge",
            "error_rate": 1,
            "http_status": 503,
            "delay_ms": 0,
            "failure_point": {"mode": "fixed", "boundary_id": "before-first"},
        },
    )
    assert fault.status_code == 200
    assert client.get("/_config").get_json()["faults"]["POST /charge"]["http_status"] == 503


def test_external_read_only_service_rejects_patch(tmp_path: Path) -> None:
    client = make_client(tmp_path, "pricing")
    result = client.patch(
        "/_config",
        json={"request_id": "no", "expected_revision": "1", "route": "GET /quote", "set": {"work": {"kind": "fixed", "delay_ms": 1}}},
    )
    assert result.status_code == 403


def test_fault_config_is_persisted(tmp_path: Path) -> None:
    first = make_client(tmp_path)
    assert first.put(
        "/_chaos",
        json={"route": "POST /charge", "error_rate": 0.5, "http_status": 503, "delay_ms": 10, "failure_point": {"mode": "fixed", "boundary_id": "after-bank"}},
    ).status_code == 200
    second = make_client(tmp_path)
    restored = second.get("/_config").get_json()
    assert restored["faults"]["POST /charge"]["failure_point"]["boundary_id"] == "after-bank"
    assert json.loads((tmp_path / "payment.json").read_text())["fault_revision"] == 1


def test_single_route_fault_can_be_cleared(tmp_path: Path) -> None:
    client = make_client(tmp_path)
    client.put(
        "/_chaos",
        json={"route": "POST /charge", "error_rate": 1, "http_status": 503, "delay_ms": 0, "failure_point": {"mode": "fixed", "boundary_id": "before-first"}},
    )
    cleared = client.delete("/_chaos", json={"route": "POST /charge"})
    assert cleared.status_code == 200
    assert "POST /charge" not in client.get("/_config").get_json()["faults"]
