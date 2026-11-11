from __future__ import annotations

import copy
import subprocess
import threading
from types import SimpleNamespace

import pytest
from app.control import laboratory as laboratory_module
from app.control.laboratory import Laboratory
from app.integrations.http_config import ProductError


class Records:
    def __init__(self):
        self.data = {}

    def get(self, kind, key):
        value = self.data.get((kind, key))
        return copy.deepcopy(value)

    def put(self, kind, key, value):
        self.data[(kind, key)] = copy.deepcopy(value)

    def items(self, kind):
        return [
            copy.deepcopy(value)
            for (item_kind, _), value in self.data.items()
            if item_kind == kind
        ]


def ready_laboratory(services, faults, revision=0):
    lab = Laboratory.__new__(Laboratory)
    lab.stand = {
        "stand_id": "stand-test",
        "status": "ready",
        "fault_revision": revision,
        "faults": copy.deepcopy(faults),
        "service_registry": services,
    }
    lab.records = Records()
    lab.lock = threading.RLock()
    lab.guard_edit = lambda: None
    lab._save = lambda: lab.records.put("lab", "active", lab.stand)
    return lab


def test_fault_request_replay_passes_adapter_id_and_preserves_public_cas(monkeypatch):
    service = {
        "service_id": "payment",
        "environment_id": "sandbox",
        "control_url": "unused",
    }
    lab = ready_laboratory({"payment": service}, [])
    adapter = {"fault_revision": 0, "faults": {}}
    remote_calls = []

    def read_config(_service):
        return {
            "revision": "1",
            "fault_revision": adapter["fault_revision"],
            "faults": copy.deepcopy(adapter["faults"]),
        }

    def remote_json(_service, method, _path, body=None, **_kwargs):
        remote_calls.append((method, copy.deepcopy(body)))
        adapter["faults"][body["route"]] = {
            key: value
            for key, value in body.items()
            if key
            in {"route", "error_rate", "http_status", "delay_ms", "failure_point"}
        }
        adapter["fault_revision"] += 1
        return {"request_id": body["request_id"], "status": "applied"}

    monkeypatch.setattr(laboratory_module, "read_config", read_config)
    monkeypatch.setattr(laboratory_module, "remote_json", remote_json)
    body = {
        "request_id": "fault-request-1",
        "expected_fault_revision": 0,
        "service_id": "payment",
        "route": "POST /charge",
        "error_rate": 1,
        "http_status": 503,
        "delay_ms": 0,
        "failure_point": {"mode": "fixed", "boundary_id": "before-first"},
    }

    first = lab.set_fault("stand-test", body)
    repeated = lab.set_fault("stand-test", body)

    assert first["fault_revision"] == repeated["fault_revision"] == 1
    assert len(remote_calls) == 1
    assert remote_calls[0][1]["request_id"] != body["request_id"]
    assert remote_calls[0][1]["expected_fault_revision"] == 0
    with pytest.raises(ProductError) as caught:
        lab.set_fault("stand-test", {**body, "error_rate": 0.5})
    assert caught.value.status == 409
    assert caught.value.code == "request_id_conflict"
    with pytest.raises(ProductError) as action_conflict:
        lab.set_fault("stand-test", body, clear=True)
    assert action_conflict.value.status == 409


def test_global_clear_persists_confirmed_partial_state(monkeypatch):
    services = {
        name: {"service_id": name, "environment_id": "sandbox", "control_url": name}
        for name in ("orders", "payment")
    }
    faults = [
        {
            "service_id": name,
            "route": "POST /charge",
            "error_rate": 1,
            "http_status": 503,
        }
        for name in services
    ]
    lab = ready_laboratory(services, faults, revision=7)
    adapter = {
        name: {
            "fault_revision": 2,
            "faults": {
                "POST /charge": {
                    "route": "POST /charge",
                    "error_rate": 1,
                    "http_status": 503,
                }
            },
        }
        for name in services
    }
    calls = []

    def read_config(service):
        state = adapter[service["service_id"]]
        return {
            "fault_revision": state["fault_revision"],
            "faults": copy.deepcopy(state["faults"]),
        }

    def remote_json(service, method, _path, body=None, **_kwargs):
        service_id = service["service_id"]
        calls.append((service_id, body["request_id"]))
        if service_id == "payment":
            raise ProductError(503, "adapter_unavailable", "Lost adapter response")
        adapter[service_id]["faults"] = {}
        adapter[service_id]["fault_revision"] += 1
        return {"status": "applied"}

    monkeypatch.setattr(laboratory_module, "read_config", read_config)
    monkeypatch.setattr(laboratory_module, "remote_json", remote_json)
    body = {"request_id": "clear-1", "expected_fault_revision": 7}

    with pytest.raises(ProductError) as caught:
        lab.set_fault("stand-test", body, clear=True)

    assert caught.value.status == 503
    assert caught.value.details["partial"] is True
    assert caught.value.details["fault_revision"] == 8
    assert [fault["service_id"] for fault in caught.value.details["faults"]] == [
        "payment"
    ]
    assert [fault["service_id"] for fault in lab.stand["faults"]] == ["payment"]
    assert (
        lab.records.get("fault_request", "stand-test:clear-1")["error"]["code"]
        == "adapter_unavailable"
    )
    with pytest.raises(ProductError) as replay:
        lab.set_fault("stand-test", body, clear=True)
    assert replay.value.status == 503
    assert len(calls) == 2


def test_startup_finalizes_waiting_samples_after_verified_shutdown(
    tmp_path, monkeypatch
):
    records = Records()
    prior = {
        "stand_id": "stand-old",
        "status": "ready",
        "service_registry": {
            "payment": {"service_id": "payment", "control_url": "unused"}
        },
    }
    records.put("lab", "active", prior)
    records.put(
        "run",
        "run-old",
        {
            "run_id": "run-old",
            "status": "completed",
            "trace_collection": {"status": "waiting"},
        },
    )
    events = []

    def remote_json(_service, method, path, **_kwargs):
        events.append((method, path))
        if path == "/_health":
            return {"stand_id": "stand-old"}
        return {"shutdown": True}

    class Telemetry:
        def finalize_collection(self, run_id, now_ns):
            assert events[-1] == ("POST", "/_shutdown")
            events.append(("finalize", run_id))
            return SimpleNamespace(
                status="partial", expected_count=3, received_count=1, trace_refs=[]
            )

    monkeypatch.setattr(laboratory_module, "remote_json", remote_json)
    Laboratory(
        {"state_directory": str(tmp_path)},
        registry=None,
        telemetry=Telemetry(),
        records=records,
        lock=threading.RLock(),
    )

    recovered = records.get("run", "run-old")
    assert recovered["trace_collection"]["status"] == "partial"
    assert events[-1] == ("finalize", "run-old")


def test_stop_retries_after_unconfirmed_process_exit():
    lab = ready_laboratory({}, [])
    del lab.guard_edit
    removed = []
    lab.registry = SimpleNamespace(remove_lab=lambda: removed.append(True))

    class Process:
        can_exit = False
        exit_code = None

        def poll(self):
            return self.exit_code

        def wait(self, timeout):
            if not self.can_exit:
                raise subprocess.TimeoutExpired("stand-service", timeout)
            self.exit_code = 0
            return self.exit_code

        def terminate(self):
            pass

        def kill(self):
            pass

    process = Process()
    lab.processes = {"payment": process}
    with pytest.raises(ProductError) as first_stop:
        lab.stop("stand-test")
    assert (first_stop.value.status, first_stop.value.code) == (503, "stand_stop_unconfirmed")
    assert lab.active()["status"] == "stopping"
    assert lab.records.get("lab", "active")["status"] == "stopping"
    assert not removed
    with pytest.raises(ProductError) as edit:
        lab.guard_edit()
    assert (edit.value.status, edit.value.code) == (409, "stand_stopping")
    with pytest.raises(ProductError) as wrong_id:
        lab.stop("other-stand")
    assert wrong_id.value.status == 404

    process.can_exit = True
    stopped = lab.stop("stand-test")
    assert stopped["status"] == "stopped"
    assert lab.stop("stand-test") == stopped
    assert lab.active() is None and removed == [True]
