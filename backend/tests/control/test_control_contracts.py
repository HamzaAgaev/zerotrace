from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from app.control import plans as plans_module
from app.control.plans import Plans, ProductError, select_target, validate_changes
from app.control.store import ControlStore
from app.integrations import http_config
from app.integrations.registry import Registry
from contracts import SourceBinding
from repository import NormalizedBatch, SpanStore
from tests.test_repository import make_span


def test_source_token_files_are_scoped_by_environment(tmp_path):
    registry = Registry({"token_directory": str(tmp_path)})
    first = registry.register_source("shop-west", "orders.api")
    second = registry.register_source("shop", "west-orders.api")
    if first == second:
        pytest.fail(
            "Different environment/source pairs must receive independent credentials"
        )
    assert registry.resolve(first).environment_id == "shop-west"
    assert registry.resolve(second).environment_id == "shop"
    reloaded = Registry({"token_directory": str(tmp_path)})
    if reloaded.register_source("shop-west", "orders.api") != first:
        pytest.fail("Source credentials must survive a server restart")


def test_ambiguous_service_namespace_disables_control(tmp_path):
    with SpanStore(tmp_path / "traces.db") as telemetry:
        registry = Registry(
            {
                "token_directory": str(tmp_path),
                "services": [{
                    "environment_id": "shop",
                    "service_id": "orders",
                    "control_url": "http://orders",
                }],
            },
            telemetry,
        )
        shop = SourceBinding("src", "project-shop", "shop")
        sandbox = SourceBinding("src", "project-sandbox", "sandbox")
        span = make_span("1" * 32, "1" * 16)
        telemetry.ingest(NormalizedBatch([span]), shop, 1)
        telemetry.ingest(NormalizedBatch([replace(span, namespace="other")]), sandbox, 2)
        assert registry.service("shop", "orders")["service_id"] == "orders"

        late = replace(span, span_id="2" * 16, namespace="other")
        telemetry.ingest(NormalizedBatch([late]), shop, 3)
        with pytest.raises(ProductError) as caught:
            registry.service("shop", "orders")
        assert caught.value.code == "ambiguous_service_identity"


def error_code(call, code: str) -> None:
    with pytest.raises(ProductError) as caught:
        call()
    assert caught.value.status == 400
    assert caught.value.code == code


def test_plan_request_types_and_selectors_return_400(tmp_path, monkeypatch):
    registry = Registry({"token_directory": str(tmp_path), "services": []})
    records = ControlStore(tmp_path / "control.sqlite")
    plans = Plans(registry, records, __import__("threading").RLock(), lambda: None)

    error_code(
        lambda: plans.create({"environment_id": [], "service_id": "gateway"}),
        "invalid_environment_id",
    )
    error_code(
        lambda: plans.create({"environment_id": "shop", "service_id": {}}),
        "invalid_service_id",
    )

    monkeypatch.setattr(
        plans_module,
        "read_config",
        lambda _service: {
            "revision": "1",
            "calls": [{"call_ref": "payment", "write_fields": ["max_attempts"]}],
            "routes": [],
        },
    )
    registry.register_service(
        {"environment_id": "shop", "service_id": "gateway", "control_url": "unused"}
    )
    base = {
        "environment_id": "shop",
        "service_id": "gateway",
        "expected_config_revision": "1",
    }
    error_code(
        lambda: plans.create({**base, "call_ref": 123, "set": {"max_attempts": 2}}),
        "invalid_target",
    )

    records.close()


def test_multiple_field_timeout_alias_is_rejected_as_ambiguous():
    error_code(
        lambda: validate_changes(
            {"timeout_ms": 10, "per_attempt_timeout_ns": "20000000"}
        ),
        "invalid_changes",
    )


def test_malformed_adapter_selector_is_502():
    with pytest.raises(ProductError) as caught:
        select_target({"calls": [{}]}, {"call_ref": "pay"})
    assert caught.value.status == 502
    assert caught.value.code == "invalid_config"


def test_apply_saves_operation_before_start_and_is_idempotent(tmp_path, monkeypatch):
    import threading

    registry = StaticRegistry()
    records = ControlStore(tmp_path / "apply.sqlite")
    plan = {
        "plan_id": "plan-apply",
        "plan_hash": "hash-1",
        "environment_id": "shop",
        "service_id": "gateway",
        "call_ref": "pay",
        "expected_revision": "3",
        "set": {"max_attempts": 4},
        "changes": [{"field": "max_attempts", "before": "2", "after": "4"}],
        "expires_at": (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
        "target": "gateway / pay",
    }
    records.put("plan", plan["plan_id"], plan)
    plans = Plans(registry, records, threading.RLock(), lambda: None)
    monkeypatch.setattr(
        plans_module,
        "read_config",
        lambda _service: {
            "revision": "3",
            "calls": [{"call_ref": "pay", "write_fields": ["max_attempts"]}],
            "routes": [],
        },
    )
    starts = []

    class DeferredThread:
        def __init__(self, target, args, daemon):
            self.args = args

        def start(self):
            operation_id = self.args[0]
            assert records.get("operation", operation_id) is not None
            assert (
                records.get("apply_request", "request-1")["operation_id"]
                == operation_id
            )
            starts.append(operation_id)

    monkeypatch.setattr(plans_module.threading, "Thread", DeferredThread)
    body = {"request_id": "request-1", "plan_hash": "hash-1"}
    first = plans.apply(plan["plan_id"], body)
    second = plans.apply(plan["plan_id"], body)

    assert second == first
    assert starts == [first["operation_id"]]
    with pytest.raises(ProductError) as caught:
        plans.apply(
            plan["plan_id"], {"request_id": "request-1", "plan_hash": "another-hash"}
        )
    assert caught.value.status == 409
    assert caught.value.code == "request_id_conflict"
    records.close()


def test_read_config_intersects_field_permissions_and_rejects_bad_adapter_shape(
    monkeypatch,
):
    service = {
        "service_id": "gateway",
        "environment_id": "shop",
        "write_fields": ["timeout_ms"],
    }
    payload = {
        "service_id": "gateway",
        "revision": "3",
        "calls": [{"call_ref": "pay", "write_fields": ["timeout_ms", "max_attempts"]}],
        "routes": {"POST /checkout": {"write_fields": ["work", "timeout_ms"]}},
    }
    monkeypatch.setattr(http_config, "remote_json", lambda *_args, **_kwargs: payload)

    config = http_config.read_config(service)

    assert config["calls"][0]["write_fields"] == ["timeout_ms"]
    assert config["routes"][0]["write_fields"] == ["timeout_ms"]
    monkeypatch.setattr(
        http_config,
        "remote_json",
        lambda *_args, **_kwargs: {**payload, "calls": "not-a-list"},
    )
    with pytest.raises(ProductError) as caught:
        http_config.read_config(service)
    assert caught.value.status == 502


class MemoryRecords:
    def __init__(self, operation, plan):
        self.values = {
            ("operation", operation["operation_id"]): operation,
            ("plan", plan["plan_id"]): plan,
        }

    def get(self, kind, key):
        value = self.values.get((kind, key))
        return dict(value) if value else None

    def put(self, kind, key, value):
        self.values[(kind, key)] = dict(value)


class StaticRegistry:
    def service(self, _environment, _service_id):
        return {
            "service_id": "gateway",
            "environment_id": "shop",
            "control_url": "unused",
        }


def test_successful_patch_with_unavailable_readback_stays_unknown(monkeypatch):
    plan = {
        "plan_id": "plan-1",
        "environment_id": "shop",
        "service_id": "gateway",
        "call_ref": "pay",
        "expected_revision": "3",
        "set": {"max_attempts": 4},
        "changes": [{"field": "max_attempts"}],
    }
    operation = {
        "operation_id": "op-1",
        "request_id": "req-1",
        "plan_id": "plan-1",
        "status": "applying",
    }
    records = MemoryRecords(operation, plan)
    plans = Plans(
        StaticRegistry(), records, __import__("threading").RLock(), lambda: None
    )

    def unavailable_readback(_service, method, path, _body=None, **_kwargs):
        if method == "PATCH":
            return {"request_id": "req-1", "status": "applied", "new_revision": "4"}
        raise ProductError(404, "operation_not_found", "Not visible yet")

    monkeypatch.setattr(plans_module, "remote_json", unavailable_readback)
    monkeypatch.setattr(plans_module.time, "sleep", lambda _seconds: None)
    plans._execute("op-1")

    assert records.values[("operation", "op-1")]["status"] == "unknown"


def test_readback_requires_matching_request_id_and_revision(monkeypatch):
    plan = {
        "call_ref": "pay",
        "set": {"max_attempts": 4},
        "changes": [{"field": "max_attempts"}],
    }
    operation = {"operation_id": "op-1", "request_id": "req-1"}
    plans = Plans(StaticRegistry(), None, __import__("threading").RLock(), lambda: None)
    monkeypatch.setattr(
        plans_module,
        "remote_json",
        lambda *_args, **_kwargs: {
            "request_id": "some-other-request",
            "status": "applied",
            "new_revision": "4",
        },
    )

    with pytest.raises(ProductError) as caught:
        plans._readback(
            operation, plan, {"service_id": "gateway", "environment_id": "shop"}
        )
    assert caught.value.code == "operation_unconfirmed"

    monkeypatch.setattr(
        plans_module,
        "remote_json",
        lambda *_args, **_kwargs: {
            "request_id": "req-1",
            "status": "applied",
            "new_revision": "4",
        },
    )
    monkeypatch.setattr(
        plans_module,
        "read_config",
        lambda _service: {
            "revision": "5",
            "calls": [{"call_ref": "pay", "max_attempts": 4}],
            "routes": [],
        },
    )
    with pytest.raises(ProductError) as caught:
        plans._readback(
            operation, plan, {"service_id": "gateway", "environment_id": "shop"}
        )
    assert caught.value.code == "readback_mismatch"
