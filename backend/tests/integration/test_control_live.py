"""Exercise A, B and C through real HTTP and nine instrumented processes."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path

import pytest
import requests
from app.server.application import create_app
from werkzeug.serving import make_server


def value(response):
    assert response.ok, (response.status_code, response.text[:1200])
    return response.json()["payload"]


def wait_until(callback, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = callback()
        if result:
            return result
        time.sleep(0.15)
    raise AssertionError("Timed out waiting for operation or telemetry")


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    directory = tmp_path_factory.mktemp("live-control")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    app = create_app(
        {
            "database": str(directory / "product.db"),
            "state_directory": str(directory),
            "token_directory": str(directory / "tokens"),
            "server_port": port,
            "scenario_path": str(Path("stand/scenarios/checkout.json").resolve()),
            "build_timeout_s": 20,
            "environments": [
                {"environment_id": "shop", "kind": "external"},
                {"environment_id": "sandbox", "kind": "lab"},
            ],
        }
    )
    server = make_server("127.0.0.1", port, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{port}"
    try:
        stand = value(
            requests.post(
                base + "/api/v1/lab/stands",
                json={"scenario_id": "checkout"},
                timeout=30,
            )
        )
        assert len(stand["services"]) == 9
        yield base, app, stand["stand_id"]
    finally:
        app.extensions["lab"].shutdown()
        server.shutdown()
        worker.join(timeout=3)
        app.extensions["records"].close()
        app.extensions["telemetry"].close()


def change(base, service_id, call_ref=None, route=None, **fields):
    config = value(
        requests.get(
            f"{base}/api/v1/services/{service_id}/config?environment_id=sandbox"
        )
    )
    intent = {
        "environment_id": "sandbox",
        "service_id": service_id,
        "expected_config_revision": config["revision"],
        "set": fields,
    }
    intent.update({"call_ref": call_ref} if call_ref else {"route": route})
    plan = value(requests.post(base + "/api/v1/change-plans", json=intent))
    return apply_plan(base, plan)


def apply_plan(base, plan):
    body = {"request_id": str(uuid.uuid4()), "plan_hash": plan["plan_hash"]}
    operation = value(
        requests.post(f"{base}/api/v1/change-plans/{plan['plan_id']}/apply", json=body)
    )
    repeated = value(
        requests.post(f"{base}/api/v1/change-plans/{plan['plan_id']}/apply", json=body)
    )
    assert repeated["operation_id"] == operation["operation_id"]

    def completed():
        result = value(
            requests.get(
                f"{base}/api/v1/journal/{operation['operation_id']}?environment_id=sandbox"
            )
        )
        return result if result["status"] != "applying" else None

    result = wait_until(completed)
    assert result["status"] in {"applied", "no_op"}, result
    return result


def start_run(base, stand_id, count=4, seed=512):
    stand = value(requests.get(base + "/api/v1/lab/stands/active"))
    body = {
        "request_id": str(uuid.uuid4()),
        "scenario_revision": stand["scenario_revision"],
        "expected_fault_revision": stand["fault_revision"],
        "config_revisions": stand["config_revisions"],
        "request_count": count,
        "concurrency": 2,
        "seed": seed,
    }
    run = value(requests.post(f"{base}/api/v1/lab/stands/{stand_id}/runs", json=body))
    repeated = value(
        requests.post(f"{base}/api/v1/lab/stands/{stand_id}/runs", json=body)
    )
    assert repeated["run_id"] == run["run_id"]

    def completed():
        current = value(requests.get(f"{base}/api/v1/lab/runs/{run['run_id']}"))
        return (
            current
            if current["status"] != "running"
            and current["trace_collection"]["status"] != "waiting"
            else None
        )

    result = wait_until(completed)
    assert result["status"] == "completed", result
    assert result["trace_collection"]["status"] == "ready", result["trace_collection"]
    assert result["trace_collection"]["received_count"] == count
    assert result["seed"] == seed
    return result


def test_real_trace_and_atomic_plan(live):
    base, app, _ = live
    assert (
        value(requests.get(base + "/api/v1/capabilities?environment_id=sandbox"))[
            "source_state"
        ]
        == "receiving"
    )
    assert (
        len(
            value(requests.get(base + "/api/v1/services?environment_id=sandbox"))[
                "items"
            ]
        )
        == 9
    )
    traces = value(requests.get(base + "/api/v1/traces?environment_id=sandbox"))
    assert traces["total"] >= 1
    services = set()
    for trace in traces["items"]:
        detail = value(
            requests.get(
                f"{base}/api/v1/traces/{trace['trace_id']}?environment_id=sandbox"
            )
        )
        services.update(span["service"] for span in detail["spans"])
        assert all(not span["name"].startswith("GET /_") for span in detail["spans"])
    assert len(services) == 9, services
    result = change(base, "gateway", "orders-create", max_attempts=1, backoff_ms=0)
    assert len(result["actual_changes"]) >= 1
    config = value(
        requests.get(base + "/api/v1/services/gateway/config?environment_id=sandbox")
    )
    stale = requests.post(
        base + "/api/v1/change-plans",
        json={
            "environment_id": "sandbox",
            "service_id": "gateway",
            "call_ref": "orders-create",
            "expected_config_revision": "outdated",
            "set": {"max_attempts": 2},
        },
    )
    assert stale.status_code == 409
    assert (
        next(call for call in config["calls"] if call["call_ref"] == "orders-create")[
            "max_attempts"
        ]
        == 1
    )
    assert app.extensions["records"].items("operation")


def test_fault_runs_and_measured_comparison(live):
    base, _, stand_id = live
    stand = value(requests.get(base + "/api/v1/lab/stands/active"))
    fault = {
        "service_id": "payment",
        "route": "POST /charge",
        "error_rate": 1,
        "http_status": 503,
        "delay_ms": 0,
        "failure_point": {"mode": "fixed", "boundary_id": "before-first"},
        "expected_fault_revision": stand["fault_revision"],
        "request_id": str(uuid.uuid4()),
    }
    value(requests.put(f"{base}/api/v1/lab/stands/{stand_id}/fault", json=fault))
    before = start_run(base, stand_id)
    assert all(row["http_status"] == 503 for row in before["results"])
    audit = value(requests.get(base + "/api/v1/audit?environment_id=sandbox"))
    finding = next(
        item
        for item in audit["findings"]
        if item["rule_id"] == "R4"
        and item["edge"]["caller"] == "orders"
        and item["edge"]["callee"] == "payment"
    )
    assert finding["evidence"]["required_ns"] == "2500000000"
    assert finding["evidence"]["budget_ns"] == "1800000000"
    recommendation = {
        "environment_id": "sandbox",
        "finding_id": finding["finding_id"],
        "occurrence_id": finding["occurrence_id"],
        "recommendation_id": finding["recommendation_id"],
        "expected_config_revision": finding["evidence"]["config_revision"],
    }
    plan = value(requests.post(base + "/api/v1/change-plans", json=recommendation))
    apply_plan(base, plan)
    after = start_run(base, stand_id)
    comparison = requests.post(
        base + "/api/v1/comparisons",
        json={"baseline_run_id": before["run_id"], "candidate_run_id": after["run_id"]},
    )
    assert comparison.ok, comparison.text[:1500]
    payload = comparison.json()["payload"]
    assert payload["baseline"]["attempts_per_input"] == 3
    assert payload["candidate"]["attempts_per_input"] == 2
    assert payload["baseline"]["http_status_counts"] == {"503": 4}
    assert payload["candidate"]["http_status_counts"] == {"503": 4}


def test_seed_and_incoming_work(live):
    base, _, stand_id = live
    value(requests.delete(f"{base}/api/v1/lab/stands/{stand_id}/fault", json={}))
    change(
        base,
        "payment",
        route="POST /charge",
        work={"kind": "lognormal", "p50_ms": 3, "p99_ms": 10},
    )
    first = start_run(base, stand_id, count=2, seed=177)
    second = start_run(base, stand_id, count=2, seed=177)
    assert [
        row["http_status"]
        for row in sorted(first["results"], key=lambda row: row["index"])
    ] == [
        row["http_status"]
        for row in sorted(second["results"], key=lambda row: row["index"])
    ]
    assert first["config_revisions"] == second["config_revisions"]


def test_source_binding_isolates_identical_trace_ids(live):
    import json

    base, app, _ = live
    registry = app.extensions["registry"]
    packet = json.loads(Path("backend/fixtures/otlp/s00_healthy.json").read_bytes())
    for resource in packet["resourceSpans"]:
        for attribute in resource["resource"]["attributes"]:
            if attribute["key"] == "service.name":
                attribute["value"] = {"stringValue": "isolation-fixture"}
    fixture = json.dumps(packet).encode()
    for env in ("shop", "sandbox"):
        token = registry.register_source(env, "isolation-fixture")
        response = requests.post(
            base + "/v1/traces",
            data=fixture,
            headers={
                "Authorization": "Bearer " + token,
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200
    trace_id = json.loads(fixture)["resourceSpans"][0]["scopeSpans"][0]["spans"][0][
        "traceId"
    ]
    for env in ("shop", "sandbox"):
        detail = value(
            requests.get(f"{base}/api/v1/traces/{trace_id}?environment_id={env}")
        )
        assert detail["trace_ref"]["environment_id"] == env
    assert (
        requests.post(
            base + "/v1/traces",
            data=fixture,
            headers={"Content-Type": "application/json"},
        ).status_code
        == 401
    )


def test_timeouts_wait_for_downstream_spans(live):
    base, _, stand_id = live
    value(requests.delete(f"{base}/api/v1/lab/stands/{stand_id}/fault", json={}))
    change(
        base, "payment", route="POST /charge", work={"kind": "fixed", "delay_ms": 80}
    )
    change(base, "orders", "charge-payment", timeout_ms=30, max_attempts=2)
    run = start_run(base, stand_id, count=1)
    assert run["results"][0]["http_status"] == 504
    ref = run["trace_refs"][0]
    detail = value(
        requests.get(
            f"{base}/api/v1/traces/{ref['trace_id']}",
            params={
                "environment_id": "sandbox",
                "generation": ref["generation"],
                "revision": ref["revision"],
            },
        )
    )
    attempts = [
        span
        for span in detail["spans"]
        if span["service"] == "orders"
        and span["kind"] == "client"
        and span.get("target") == "payment"
    ]
    assert len(attempts) == 2
    assert all("timeout" in (span["error_type"] or "").lower() for span in attempts)
    assert any(
        span["service"] == "bank-api" and span["kind"] == "server"
        for span in detail["spans"]
    )


def test_linear_generator_uses_only_local_targets(live):
    base, _, stand_id = live
    listed = value(requests.get(base + "/api/v1/traces?environment_id=shop"))
    source = listed["items"][0]["trace_ref"]
    stopped = value(requests.delete(f"{base}/api/v1/lab/stands/{stand_id}"))
    assert len(stopped["services"]) == 9
    repeated = value(requests.delete(f"{base}/api/v1/lab/stands/{stand_id}"))
    assert repeated["stand_id"] == stopped["stand_id"]
    assert value(requests.get(base + "/api/v1/lab/stands/active")) is None
    generated = value(
        requests.post(base + "/api/v1/lab/stands", json={"source": source}, timeout=30)
    )
    assert generated["source"] == source
    assert len(generated["services"]) == 2
    assert generated["origins"]
    run = start_run(base, generated["stand_id"], count=1)
    assert run["results"][0]["http_status"] == 200


def test_unmodified_application_with_language_launcher(live, tmp_path):
    base, app, _ = live
    script = tmp_path / "customer.py"
    script.write_text(
        "import os\nfrom flask import Flask\nimport requests\n"
        "app = Flask(__name__)\n"
        "@app.get('/hello')\ndef hello():\n"
        "    target = os.environ.get('CUSTOMER_TARGET')\n"
        "    return requests.get(target + '/hello', timeout=2).text if target else 'ok'\n"
        "app.run(host='127.0.0.1', port=int(os.environ['CUSTOMER_PORT']), use_reloader=False)\n"
    )
    ports = []
    for _ in range(2):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports.append(listener.getsockname()[1])
    children = []
    try:
        for index, service in enumerate(("launcher-server", "launcher-client")):
            token = app.extensions["registry"].register_source("shop", service)
            env = dict(os.environ)
            env.update(
                OTEL_SERVICE_NAME=service,
                OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
                OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=base + "/v1/traces",
                OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20" + token,
                OTEL_METRICS_EXPORTER="none",
                OTEL_LOGS_EXPORTER="none",
                OTEL_BSP_SCHEDULE_DELAY="100",
                CUSTOMER_PORT=str(ports[index]),
            )
            if index:
                env["CUSTOMER_TARGET"] = f"http://127.0.0.1:{ports[0]}"
            with (tmp_path / f"{service}.log").open("wb") as log:
                children.append(
                    subprocess.Popen(
                        ["opentelemetry-instrument", "python", str(script)],
                        env=env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                )

        def reachable():
            try:
                return (
                    requests.get(
                        f"http://127.0.0.1:{ports[1]}/hello", timeout=1
                    ).status_code
                    == 200
                )
            except requests.RequestException:
                return False

        wait_until(reachable, timeout=15)

        def linked():
            listed = value(requests.get(base + "/api/v1/traces?environment_id=shop"))
            match = next(
                (
                    item
                    for item in listed["items"]
                    if item["service"] == "launcher-client" and item["span_count"] == 3
                ),
                None,
            )
            return match

        summary = wait_until(linked)
        detail = value(
            requests.get(
                f"{base}/api/v1/traces/{summary['trace_id']}?environment_id=shop"
            )
        )
        assert {span["service"] for span in detail["spans"]} == {
            "launcher-client",
            "launcher-server",
        }
        assert [span["kind"] for span in detail["spans"]].count("client") == 1
        assert "opentelemetry" not in script.read_text()
    finally:
        for child in children:
            child.terminate()
            child.wait(timeout=5)
