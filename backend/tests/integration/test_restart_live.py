"""Persist a real run and configuration across a common-server process restart."""

import json
import os
import socket
import subprocess
import sys
from pathlib import Path

import requests

from tests.integration.test_control_live import change, start_run, value, wait_until


def test_restart_preserves_history_and_restores_explicitly_started_stand(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "database": str(tmp_path / "product.db"),
                "state_directory": str(tmp_path),
                "token_directory": str(tmp_path / "tokens"),
                "scenario_path": str(Path(__file__).resolve().parents[3] / "stand/scenarios/checkout.json"),
                "environments": [
                    {"environment_id": "sandbox", "name": "Lab", "kind": "lab"}
                ],
            }
        )
    )
    base = f"http://127.0.0.1:{port}"
    log = (tmp_path / "server.log").open("ab")

    def launch():
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "app.server",
                "--config",
                str(config),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=dict(os.environ),
            stdout=log,
            stderr=subprocess.STDOUT,
        )

        def healthy():
            try:
                return requests.get(base + "/healthz", timeout=0.5).status_code == 200
            except requests.RequestException:
                return False

        wait_until(healthy, timeout=20)
        return process

    process = launch()
    try:
        first = value(
            requests.post(
                base + "/api/v1/lab/stands",
                json={"scenario_id": "checkout"},
                timeout=30,
            )
        )
        operation = change(
            base, "payment", route="POST /charge", work={"kind": "fixed", "delay_ms": 7}
        )
        run = start_run(base, first["stand_id"], count=1, seed=902)
        process.terminate()
        process.wait(timeout=20)
        process = launch()
        assert value(requests.get(base + "/api/v1/lab/stands/active")) is None
        restored_run = value(requests.get(f"{base}/api/v1/lab/runs/{run['run_id']}"))
        assert restored_run["seed"] == 902
        assert restored_run["trace_refs"] == run["trace_refs"]
        journal = value(
            requests.get(
                f"{base}/api/v1/journal/{operation['operation_id']}?environment_id=sandbox"
            )
        )
        assert journal["status"] == "applied"
        second = value(
            requests.post(
                base + "/api/v1/lab/stands",
                json={"scenario_id": "checkout"},
                timeout=30,
            )
        )
        assert second["stand_id"] != first["stand_id"]
        snapshot = value(
            requests.get(
                base + "/api/v1/services/payment/config?environment_id=sandbox"
            )
        )
        route = next(
            item for item in snapshot["routes"] if item["route"] == "POST /charge"
        )
        assert route["work"] == {"kind": "fixed", "delay_ms": 7}
    finally:
        process.terminate()
        process.wait(timeout=20)
        log.close()
