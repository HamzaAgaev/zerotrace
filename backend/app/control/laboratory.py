from __future__ import annotations

import copy
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import suppress
from pathlib import Path
from urllib.parse import urlsplit

import requests
from stand.scenario import load_scenario

from app.control.generator import from_trace
from app.control.plans import digest, integer, timestamp
from app.integrations.http_config import ProductError, read_config, remote_json


class Laboratory:
    def __init__(self, config, registry, telemetry, records, lock):
        self.config = config
        self.registry = registry
        self.telemetry = telemetry
        self.records = records
        self.lock = lock
        self.processes: dict[str, subprocess.Popen] = {}
        self.directory = Path(config.get("state_directory", "/data"))
        self.directory.mkdir(parents=True, exist_ok=True)
        previous = records.get("lab", "active")
        self.stand = previous if previous and previous["status"] != "stopped" else None
        self._recovering = self.stand is not None
        for run in records.items("run"):
            if run["status"] == "running":
                run.update(status="failed", error="server_restart", finished_at=timestamp())
                records.put("run", run["run_id"], run)
        if self._recovering:
            # Keep the slot in stopping when ownership or exit cannot be confirmed.
            with suppress(ProductError):
                self._cleanup()
        else:
            self._finalize_recovered_collections()

    def _save(self):
        self.records.put("lab", "active", self.stand)

    def _require(self, stand_id=None, *, allow_stopping=False):
        if not self.stand or (stand_id and self.stand["stand_id"] != stand_id):
            raise ProductError(404, "stand_not_found", "Стенд не запущен.")
        if self.stand["status"] == "stopping":
            if allow_stopping:
                return
            raise ProductError(409, "stand_stopping", "Стенд останавливается.")
        if self.stand["status"] != "ready":
            raise ProductError(409, "stand_not_ready", "Стенд ещё не готов.")

    def guard_edit(self):
        self._require()
        for run in self.records.items("run"):
            if run.get("stand_id") == self.stand["stand_id"] and run["status"] == "running":
                raise ProductError(
                    409,
                    "run_in_progress",
                    "Сначала дождитесь завершения прогона.",
                    {"run_id": run["run_id"]},
                )

    @staticmethod
    def _free_port():
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            return listener.getsockname()[1]

    def create(self, body: dict) -> dict:
        with self.lock:
            if self.stand:
                raise ProductError(
                    409, "stand_already_active", "Стенд уже запущен.",
                    {"stand_id": self.stand["stand_id"]},
                )
            if body.get("source"):
                scenario, origins = from_trace(self.telemetry, body["source"])
            else:
                scenario_id = body.get("scenario_id", "checkout")
                if scenario_id != "checkout":
                    raise ProductError(404, "scenario_not_found", "Неизвестный сценарий.")
                scenario = load_scenario(
                    self.config.get("scenario_path", "stand/scenarios/checkout.json")
                )
                origins = []
            stand_id = "stand-" + uuid.uuid4().hex
            directory = self.directory / "stands" / stand_id
            directory.mkdir(parents=True)
            previous = self.records.get("lab", "active")
            restoring = bool(
                previous
                and previous.get("scenario_id") == scenario["scenario_id"]
                and previous.get("source") == body.get("source")
                and previous.get("service_registry")
            )
            if restoring:
                # Each service owns its persisted settings. A new stand restores
                # only an explicitly saved snapshot with matching operation IDs.
                scenario = self._restore_scenario(scenario, previous.get("config_snapshots", {}))
            scenario_path = directory / "scenario.json"
            scenario_path.write_text(json.dumps(scenario, indent=2))
            ports = set()
            while len(ports) < len(scenario["services"]):
                ports.add(self._free_port())
            urls = {
                name: f"http://127.0.0.1:{port}"
                for name, port in zip(scenario["services"], sorted(ports), strict=True)
            }
            urls_path = directory / "urls.json"
            urls_path.write_text(json.dumps(urls))
            services = {}
            for name, url in urls.items():
                item = {
                    "service_id": name,
                    "environment_id": "sandbox",
                    "control_url": url,
                    "display_name": name,
                    "is_lab": True,
                    "write_fields": [
                        "timeout_ms",
                        "max_attempts",
                        "backoff_ms",
                        "retry_on",
                        "work",
                    ],
                }
                services[name] = item
                self.registry.register_service(item)
                self.registry.register_source("sandbox", name)
            self.stand = {
                "stand_id": stand_id,
                "scenario_id": scenario["scenario_id"],
                "scenario_revision": scenario.get("scenario_revision", 1),
                "environment_id": "sandbox",
                "status": "building",
                "fault_revision": previous.get("fault_revision", 0) if restoring else 0,
                "faults": copy.deepcopy(previous.get("faults", [])) if restoring else [],
                "config_revisions": {},
                "entrypoint": scenario["entrypoint"],
                "service_registry": services,
                "scenario_path": str(scenario_path),
                "origins": origins,
                "source": body.get("source"),
                "created_at": timestamp(),
                "config_snapshots": {},
                "active_run_id": None,
                "latest_run_id": None,
            }
            self._save()
            deadline = time.monotonic() + self.config.get("build_timeout_s", 20)
            try:
                for name, url in urls.items():
                    port = int(url.rsplit(":", 1)[1])
                    env = dict(os.environ)
                    env.update(
                        OTEL_SERVICE_NAME=name,
                        ZEROTRACE_STAND_ID=stand_id,
                        OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=(
                            f"http://127.0.0.1:{self.config.get('server_port', 8000)}/v1/traces"
                        ),
                        OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Bearer%20"
                        + self.registry.register_source("sandbox", name),
                        OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf",
                        OTEL_METRICS_EXPORTER="none",
                        OTEL_LOGS_EXPORTER="none",
                        OTEL_BSP_SCHEDULE_DELAY="100",
                        OTEL_TRACES_SAMPLER="always_on",
                        OTEL_SEMCONV_STABILITY_OPT_IN="http/dup",
                    )
                    log = (directory / f"{name}.log").open("ab")
                    try:
                        process = subprocess.Popen(
                            [
                                sys.executable,
                                "-m",
                                "stand.service",
                                "--scenario",
                                str(scenario_path),
                                "--service-id",
                                name,
                                "--host",
                                "127.0.0.1",
                                "--port",
                                str(port),
                                "--state-dir",
                                str(directory / name),
                                "--urls",
                                str(urls_path),
                                "--lab",
                            ],
                            env=env,
                            stdout=log,
                            stderr=subprocess.STDOUT,
                        )
                    finally:
                        log.close()
                    self.processes[name] = process
            except Exception:
                self._cleanup()
                raise
        pending = set(services)
        try:
            while pending and time.monotonic() < deadline:
                for name in list(pending):
                    if self.processes[name].poll() is not None:
                        raise ProductError(
                            503, "stand_process_failed", f"Не удалось запустить {name}."
                        )
                    try:
                        health = remote_json(services[name], "GET", "/_health", timeout=0.2)
                        if (
                            health.get("ready") is True
                            and health.get("stand_id") == stand_id
                            and health.get("service_id") == name
                        ):
                            pending.remove(name)
                    except ProductError:
                        continue
                if pending:
                    time.sleep(0.1)
            if pending:
                raise ProductError(
                    503,
                    "stand_build_timeout",
                    "Истёк срок запуска стенда.",
                    {"services": sorted(pending)},
                )
            for name, definition in scenario["services"].items():
                if definition.get("initial_fault") and not restoring:
                    current = read_config(services[name])
                    payload = {
                        **definition["initial_fault"],
                        "request_id": self._service_fault_request_id(stand_id, f"initial:{name}"),
                        "expected_fault_revision": current.get("fault_revision", 0),
                    }
                    remote_json(services[name], "PUT", "/_chaos", payload)
            if restoring:
                for rule in self.stand["faults"]:
                    payload = {key: value for key, value in rule.items() if key != "service_id"}
                    service_id = rule["service_id"]
                    current = read_config(services[service_id])
                    payload.update(
                        request_id=self._service_fault_request_id(
                            stand_id, f"restore:{service_id}:{rule['route']}"
                        ),
                        expected_fault_revision=current.get("fault_revision", 0),
                    )
                    remote_json(services[service_id], "PUT", "/_chaos", payload)
            self._probe(deadline)
            snapshots = self._snapshots()
            with self.lock:
                if time.monotonic() >= deadline:
                    raise ProductError(503, "stand_build_timeout", "Истёк срок запуска стенда.")
                self._sync_all_faults(snapshots, bump_revision=False)
                if not restoring and self.stand["faults"]:
                    self.stand["fault_revision"] = 1
                self.stand["config_snapshots"] = snapshots
                self.stand["config_revisions"] = {
                    name: item["revision"] for name, item in snapshots.items()
                }
                self.stand["status"] = "ready"
                self._save()
            return self.active()
        except Exception:
            with self.lock:
                self._cleanup()
            raise

    @staticmethod
    def _restore_scenario(scenario: dict, snapshots: dict) -> dict:
        result = copy.deepcopy(scenario)
        for name, snapshot in snapshots.items():
            if name not in result["services"]:
                continue
            definition = result["services"][name]
            policies = {item["call_ref"]: item for item in snapshot.get("calls", [])}
            work = {item["route"]: item["work"] for item in snapshot.get("routes", [])}
            for route, settings in definition["routes"].items():
                if route in work:
                    settings["work"] = work[route]
                for call_ref, call in settings.get("calls", {}).items():
                    if call_ref in policies:
                        for field in ("timeout_ms", "max_attempts", "backoff_ms", "retry_on"):
                            call[field] = policies[call_ref][field]
        return result

    def _snapshots(self, stand=None) -> dict:
        services = (stand or self.stand)["service_registry"]
        with ThreadPoolExecutor(max_workers=9) as pool:
            return dict(pool.map(lambda pair: (pair[0], read_config(pair[1])), services.items()))

    def active(self) -> dict | None:
        stand = self.stand
        if not stand:
            return None
        if stand["status"] == "ready":
            try:
                snapshots = self._snapshots(stand)
                with self.lock:
                    if self.stand is not stand or stand["status"] != "ready":
                        return self.active()
                    self._sync_all_faults(snapshots)
                    stand["config_revisions"] = {
                        name: item["revision"] for name, item in snapshots.items()
                    }
                    stand["config_snapshots"] = snapshots
                    self._save()
            except ProductError:
                with self.lock:
                    if self.stand is not stand or stand["status"] != "ready":
                        return self.active()
                value = copy.deepcopy(stand)
                value["services"] = list(value.get("config_snapshots", {}).values())
                value["status"] = "unavailable"
                value.pop("service_registry", None)
                value.pop("config_snapshots", None)
                value.pop("scenario_path", None)
                return value
        value = copy.deepcopy(stand)
        if value["status"] == "ready":
            value["services"] = list(snapshots.values())
        else:
            value["services"] = []
        value.pop("service_registry", None)
        value.pop("config_snapshots", None)
        value.pop("scenario_path", None)
        return value

    def set_fault(self, stand_id: str, body: dict, clear=False) -> dict:
        with self.lock:
            self._require(stand_id)
            if not isinstance(body, dict):
                raise ProductError(400, "invalid_fault", "Ожидался объект настройки сбоя.")
            request_id = body.get("request_id")
            if request_id is None:
                request_id = "fault-" + uuid.uuid4().hex
            if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
                raise ProductError(400, "invalid_request_id", "Нужен корректный request_id.")
            request_key = f"{stand_id}:{request_id}"
            fingerprint = digest({"body": body, "clear": clear})
            previous = self.records.get("fault_request", request_key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ProductError(
                        409, "request_id_conflict", "request_id использован с другими параметрами."
                    )
                if previous.get("error"):
                    error = previous["error"]
                    raise ProductError(
                        error["status"], error["code"], error["message"], error.get("details")
                    )
                return self.active()
            self.guard_edit()
            expected = body.get("expected_fault_revision", self.stand["fault_revision"])
            current = self.stand["fault_revision"]
            self._check_revision("fault", expected, current, request_id)
            service_id = body.get("service_id")
            services = self.stand["service_registry"]
            if clear and service_id is None:
                current_service_id = None
                try:
                    for name, service in sorted(services.items()):
                        current_service_id = name
                        self._clear_service_faults(stand_id, request_id, name, service)
                except ProductError as exc:
                    state_unknown = False
                    if current_service_id is not None:
                        try:
                            self._sync_service_faults(
                                current_service_id, services[current_service_id]
                            )
                        except ProductError:
                            state_unknown = True
                    partial_error = self._fault_partial_error(exc, state_unknown)
                    self._record_fault_failure(request_key, fingerprint, partial_error)
                    raise partial_error from exc
            else:
                if not isinstance(service_id, str) or service_id not in services:
                    raise ProductError(404, "service_not_found", "Сервис отсутствует в стенде.")
                payload = {
                    key: body[key]
                    for key in ("route", "error_rate", "http_status", "delay_ms", "failure_point")
                    if key in body
                }
                try:
                    service = services[service_id]
                    current = read_config(service)
                    internal_revision = current.get("fault_revision", 0)
                    operation_id = self._service_fault_request_id(
                        stand_id, f"{request_id}:{service_id}:{'clear' if clear else 'set'}"
                    )
                    remote_json(
                        service,
                        "DELETE" if clear else "PUT",
                        "/_chaos",
                        {
                            **payload,
                            "request_id": operation_id,
                            "expected_fault_revision": internal_revision,
                        },
                    )
                    self._sync_service_faults(service_id, service)
                except ProductError as exc:
                    state_unknown = False
                    try:
                        self._sync_service_faults(service_id, services[service_id])
                    except ProductError:
                        state_unknown = True
                    partial_error = self._fault_partial_error(exc, state_unknown)
                    self._record_fault_failure(request_key, fingerprint, partial_error)
                    raise partial_error from exc
            self.records.put(
                "fault_request", request_key, {"fingerprint": fingerprint, "error": None}
            )
            self._save()
            return self.active()

    @staticmethod
    def _service_fault_request_id(stand_id: str, operation: str) -> str:
        return "fault-" + digest({"stand_id": stand_id, "operation": operation})[:40]

    @staticmethod
    def _normalized_service_faults(service_id: str, snapshot: dict) -> list[dict]:
        faults = snapshot.get("faults", {})
        if not isinstance(faults, dict) or any(
            not isinstance(rule, dict) for rule in faults.values()
        ):
            raise ProductError(
                502, "invalid_fault_config", "Адаптер вернул некорректные настройки сбоев."
            )
        fields = {"route", "error_rate", "http_status", "delay_ms", "failure_point"}
        result = []
        for route, rule in faults.items():
            normalized = {key: copy.deepcopy(value) for key, value in rule.items() if key in fields}
            normalized["route"] = route
            normalized["service_id"] = service_id
            result.append(normalized)
        return sorted(result, key=lambda item: item["route"])

    def _sync_service_faults(self, service_id: str, service: dict) -> bool:
        snapshot = read_config(service)
        actual = self._normalized_service_faults(service_id, snapshot)
        previous = [item for item in self.stand["faults"] if item.get("service_id") == service_id]
        if previous == actual:
            return False
        self.stand["faults"] = [
            item for item in self.stand["faults"] if item.get("service_id") != service_id
        ] + actual
        self.stand["faults"].sort(key=lambda item: (item["service_id"], item["route"]))
        self.stand["fault_revision"] += 1
        self._save()
        return True

    def _sync_all_faults(self, snapshots: dict, bump_revision: bool = True) -> None:
        actual = []
        for service_id in sorted(self.stand["service_registry"]):
            if service_id in snapshots:
                actual.extend(self._normalized_service_faults(service_id, snapshots[service_id]))
        actual.sort(key=lambda item: (item["service_id"], item["route"]))
        if actual != self.stand["faults"]:
            self.stand["faults"] = actual
            if bump_revision:
                self.stand["fault_revision"] += 1
            self._save()

    def _clear_service_faults(self, stand_id: str, request_id: str, service_id: str, service: dict):
        current = read_config(service)
        remote_json(
            service,
            "DELETE",
            "/_chaos",
            {
                "request_id": self._service_fault_request_id(
                    stand_id, f"{request_id}:{service_id}:clear-all"
                ),
                "expected_fault_revision": current.get("fault_revision", 0),
            },
        )
        self._sync_service_faults(service_id, service)

    def _record_fault_failure(self, request_key: str, fingerprint: str, exc: ProductError):
        details = dict(exc.details)
        details.update(
            fault_revision=self.stand["fault_revision"],
            faults=copy.deepcopy(self.stand["faults"]),
        )
        self.records.put(
            "fault_request",
            request_key,
            {
                "fingerprint": fingerprint,
                "error": {
                    "status": exc.status,
                    "code": exc.code,
                    "message": str(exc),
                    "details": details,
                },
            },
        )

    def _fault_partial_error(self, exc: ProductError, state_unknown: bool = False) -> ProductError:
        details = dict(exc.details)
        details.update(
            partial=True,
            state_unknown=state_unknown,
            fault_revision=self.stand["fault_revision"],
            faults=copy.deepcopy(self.stand["faults"]),
        )
        return ProductError(exc.status, exc.code, str(exc), details)

    def _finalize_recovered_collections(self) -> None:
        """Freeze old run samples once the prior stand has stopped."""
        for run in self.records.items("run"):
            collection = run.get("trace_collection", {})
            if collection.get("status") != "waiting":
                continue
            sample = self.telemetry.finalize_collection(run["run_id"], time.time_ns(), force=True)
            if sample is None:
                continue
            run["trace_collection"] = {
                "status": sample.status,
                "expected_count": sample.expected_count,
                "received_count": sample.received_count,
                "trace_refs": [ref.to_dict() for ref in sample.trace_refs],
            }
            run["trace_refs"] = run["trace_collection"]["trace_refs"]
            self.records.put("run", run["run_id"], run)

    @staticmethod
    def _check_revision(resource, expected, current, request_id, **details):
        if expected != current or type(expected) is not type(current):
            raise ProductError(
                409, "stale_revision", "Настройки стенда изменились.",
                {"resource": resource, "expected": expected, "current": current, **details},
                request_id=request_id,
            )

    def run(self, stand_id: str, body: dict) -> dict:
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ProductError(400, "invalid_request_id", "Нужен request_id.")
        request_key = stand_id + ":" + request_id
        fingerprint = digest(body)
        with self.lock:
            previous = self.records.get("run_request", request_key)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ProductError(
                        409, "request_id_conflict", "request_id использован с другими параметрами."
                    )
                return self.get_run(previous["run_id"])
            self._require(stand_id)
            self.guard_edit()
            for item in self.records.items("run"):
                if (
                    item["stand_id"] == stand_id
                    and self.get_run(item["run_id"])["trace_collection"]["status"] == "waiting"
                ):
                    raise ProductError(
                        409, "traces_pending", "Ожидаем телеметрию предыдущего прогона.",
                        {"run_id": item["run_id"]}, request_id=request_id,
                    )
            if any(
                item["environment_id"] == "sandbox" and item["status"] == "applying"
                for item in self.records.items("operation")
            ):
                raise ProductError(409, "operation_pending", "Конфигурация ещё применяется.")
            self._check_revision(
                "scenario", body.get("scenario_revision"), self.stand["scenario_revision"],
                request_id,
            )
            self._check_revision(
                "fault", body.get("expected_fault_revision"), self.stand["fault_revision"],
                request_id,
            )
            snapshots = self._snapshots()
            revisions = {name: item["revision"] for name, item in snapshots.items()}
            expected = body.get("config_revisions")
            if isinstance(expected, dict):
                for name in sorted(revisions.keys() | expected.keys()):
                    self._check_revision(
                        "config", expected.get(name), revisions.get(name), request_id,
                        service_id=name, config_revisions=revisions,
                    )
            else:
                self._check_revision(
                    "config", expected, revisions, request_id, config_revisions=revisions,
                )
            count = integer(body.get("request_count", 30), "request_count", 1, 1000)
            concurrency = integer(body.get("concurrency", 1), "concurrency", 1, 32)
            seed = body.get("seed")
            if seed is None:
                seed = secrets.randbits(32)
            integer(seed, "seed", 0, 2**53 - 1)
            run_id = "run-" + uuid.uuid4().hex
            run = {
                "run_id": run_id,
                "stand_id": stand_id,
                "environment_id": "sandbox",
                "scenario_id": self.stand["scenario_id"],
                "scenario_revision": self.stand["scenario_revision"],
                "fault_revision": self.stand["fault_revision"],
                "config_revisions": revisions,
                "config_snapshots": snapshots,
                "faults": copy.deepcopy(self.stand["faults"]),
                "request_count": count,
                "concurrency": concurrency,
                "load": {
                    "request_count": count,
                    "concurrency": concurrency,
                    "entrypoint": copy.deepcopy(self.stand["entrypoint"]),
                },
                "seed": seed,
                "status": "running",
                "completed_requests": 0,
                "started_at": timestamp(),
                "results": [],
                "trace_collection": {
                    "status": "waiting",
                    "expected_count": count,
                    "received_count": 0,
                },
            }
            self.records.put_many(
                [
                    ("run", run_id, run),
                    ("run_request", request_key, {"fingerprint": fingerprint, "run_id": run_id}),
                ]
            )
            self.stand["active_run_id"] = run_id
            self.stand["latest_run_id"] = run_id
            self._save()
            # Collection is opened before the first request can emit telemetry.
            total_budget = (
                math.ceil(count / concurrency) * self.config.get("load_timeout_s", 15) + 25
            )
            self.telemetry.start_collection(
                run_id, "sandbox", count, time.time_ns() + int(total_budget * 1e9)
            )
            threading.Thread(target=self._load, args=(run_id,), daemon=True).start()
            return run

    def _entry(self):
        entry = self.stand["entrypoint"]
        method, path = entry["route"].split(" ", 1)
        base = self.stand["service_registry"][entry["service_id"]]["control_url"]
        return method, base + path

    def _probe(self, deadline):
        method, url = self._entry()
        timeout = min(5, deadline - time.monotonic())
        if timeout <= 0:
            raise ProductError(503, "stand_build_timeout", "Истёк срок запуска стенда.")
        try:
            requests.request(
                method, url, headers={"X-ZeroTrace-Probe": "true"}, timeout=timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            if time.monotonic() >= deadline:
                raise ProductError(
                    503, "stand_build_timeout", "Истёк срок запуска стенда.",
                ) from exc
            raise ProductError(
                503, "stand_probe_failed", "Проверочный запрос к стенду не завершился."
            ) from exc
        self._flush(min(3, deadline - time.monotonic()))

    def _flush(self, timeout=3):
        if not self.stand or timeout <= 0:
            return

        def flush(service):
            with suppress(ProductError):
                remote_json(service, "POST", "/_flush", timeout=timeout)

        with ThreadPoolExecutor(max_workers=9) as pool:
            list(pool.map(flush, self.stand["service_registry"].values()))

    def _wait_idle(self, timeout: float) -> bool:
        """Timed-out callers can leave downstream work running on the stand."""
        deadline = time.monotonic() + timeout
        if not self.stand:
            return False

        def idle(service):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                health = remote_json(service, "GET", "/_health", timeout=min(0.5, remaining))
                return health.get("active_requests") == 0
            except ProductError:
                return False

        services = list(self.stand["service_registry"].values())
        with ThreadPoolExecutor(max_workers=9) as pool:
            while time.monotonic() < deadline:
                states = list(pool.map(idle, services))
                if all(states):
                    return True
                time.sleep(0.1)
        return False

    def _load(self, run_id: str):
        run = self.records.get("run", run_id)
        method, url = self._entry()

        def perform(index):
            started = time.monotonic_ns()
            try:
                response = requests.request(
                    method,
                    url,
                    timeout=self.config.get("load_timeout_s", 15),
                    headers={
                        "X-ZeroTrace-Run-Id": run_id,
                        "X-ZeroTrace-Seed": str(run["seed"]),
                        "X-ZeroTrace-Request-Index": str(index),
                    },
                )
                status, error = response.status_code, None
            except requests.Timeout:
                status, error = None, "timeout"
            except requests.RequestException:
                status, error = None, "connect"
            return {
                "index": index,
                "http_status": status,
                "error": error,
                "duration_ns": str(time.monotonic_ns() - started),
            }

        try:
            next_update = time.monotonic() + 0.25
            with ThreadPoolExecutor(max_workers=run["concurrency"]) as pool:
                futures = [pool.submit(perform, index) for index in range(run["request_count"])]
                for future in as_completed(futures):
                    run["results"].append(future.result())
                    run["completed_requests"] += 1
                    if time.monotonic() >= next_update:
                        self.records.put("run", run_id, run)
                        next_update = time.monotonic() + 0.25
            run.update(status="completed", finished_at=timestamp())
        except Exception as exc:
            run.update(status="failed", error=type(exc).__name__, finished_at=timestamp())
        self.records.put("run", run_id, run)
        deadline = time.monotonic() + 20
        # A timed-out request may still execute nested calls. Wait for those
        # spans to finish before asking B to freeze an exact revision.
        if not self._wait_idle(20):
            run.update(status="failed", error="stand_drain_timeout")
            self.records.put("run", run_id, run)
        # All child processes flush before B freezes exact revisions.
        self._flush(min(3, deadline - time.monotonic()))
        while time.monotonic() < deadline:
            self.telemetry.advance_assembly(time.time_ns(), quiet_after_ns=200_000_000)
            sample = self.telemetry.finalize_collection(run_id, time.time_ns())
            if sample is None or sample.status != "waiting":
                break
            time.sleep(0.25)
        else:
            self.telemetry.finalize_collection(run_id, time.time_ns(), force=True)
        with self.lock:
            if self.stand and self.stand.get("active_run_id") == run_id:
                self.stand["active_run_id"] = None
                self._save()

    def get_run(self, run_id: str):
        run = self.records.get("run", run_id)
        if not run:
            raise ProductError(404, "run_not_found", "Прогон не найден.")
        sample = self.telemetry.get_sample(run_id)
        if sample:
            run["trace_collection"] = {
                "status": sample.status,
                "expected_count": sample.expected_count,
                "received_count": sample.received_count,
                "trace_refs": [ref.to_dict() for ref in sample.trace_refs],
            }
            run["trace_refs"] = run["trace_collection"]["trace_refs"]
        elif run["status"] == "failed":
            run["trace_collection"] = {
                "status": "partial",
                "expected_count": run["request_count"],
                "received_count": 0,
                "trace_refs": [],
            }
            run["trace_refs"] = []
        return run

    def stop(self, stand_id: str):
        with self.lock:
            receipt = self.records.get("stopped_stand", stand_id)
            if receipt is not None:
                return receipt
            if self.stand is None:
                previous = self.records.get("lab", "active")
                if (
                    previous
                    and previous["stand_id"] == stand_id
                    and previous["status"] == "stopped"
                ):
                    return self._stopped_view(previous)
            self._require(stand_id, allow_stopping=True)
            if self.stand["status"] == "ready":
                self.guard_edit()
                for run in self.records.items("run"):
                    if run["stand_id"] != stand_id:
                        continue
                    if self.get_run(run["run_id"])["trace_collection"]["status"] == "waiting":
                        raise ProductError(
                            409, "traces_pending", "Ожидаем телеметрию прогона.",
                            {"run_id": run["run_id"]},
                        )
                with suppress(ProductError):
                    self.stand["config_snapshots"] = self._snapshots()
            return self._cleanup()

    @staticmethod
    def _stopped_view(stand):
        value = copy.deepcopy(stand)
        value["status"] = "stopped"
        value["services"] = list(value.pop("config_snapshots", {}).values())
        value.pop("service_registry", None)
        value.pop("scenario_path", None)
        return value

    @staticmethod
    def _port_released(service):
        url = urlsplit(service["control_url"])
        try:
            with socket.create_connection((url.hostname, url.port), timeout=0.2):
                return False
        except ConnectionRefusedError:
            return True
        except OSError:
            return False

    def _recover_service(self, service):
        with suppress(ProductError):
            health = remote_json(service, "GET", "/_health", timeout=0.3)
            owner = health.get("stand_id")
            if owner == self.stand["stand_id"]:
                remote_json(service, "POST", "/_shutdown", timeout=0.5)
            elif owner:
                return True
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self._port_released(service):
                return True
            time.sleep(0.1)
        return False

    def _cleanup(self):
        if self.stand:
            self.stand["status"] = "stopping"
            self._save()
        services = self.stand["service_registry"] if self.stand else {}

        def finish(item):
            name, process = item
            if process.poll() is not None:
                return
            service = services.get(name)
            if service:
                with suppress(ProductError):
                    health = remote_json(service, "GET", "/_health", timeout=0.3)
                    if health.get("stand_id") == self.stand["stand_id"]:
                        remote_json(service, "POST", "/_shutdown", timeout=0.5)
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)

        if self._recovering:
            with ThreadPoolExecutor(max_workers=9) as pool:
                stopped = list(pool.map(self._recover_service, services.values()))
            if not all(stopped):
                raise ProductError(
                    503, "stand_stop_unconfirmed",
                    "Не удалось подтвердить остановку стенда. Повторите остановку.",
                )
            self._finalize_recovered_collections()
            self._recovering = False
        if self.processes:
            try:
                with ThreadPoolExecutor(max_workers=min(32, len(self.processes))) as pool:
                    list(pool.map(finish, self.processes.items()))
            except (subprocess.TimeoutExpired, OSError) as error:
                raise ProductError(
                    503, "stand_stop_unconfirmed",
                    "Не удалось подтвердить остановку стенда. Повторите остановку.",
                ) from error
        self.processes.clear()
        receipt = None
        if self.stand:
            self.stand.update(status="stopped", stopped_at=timestamp())
            receipt = self._stopped_view(self.stand)
            self.records.put_many([
                ("lab", "active", self.stand),
                ("stopped_stand", self.stand["stand_id"], receipt),
            ])
        self.stand = None
        self.registry.remove_lab()
        return receipt

    def shutdown(self):
        with self.lock:
            if self.stand:
                with suppress(ProductError):
                    self.stand["config_snapshots"] = self._snapshots()
            self._cleanup()
