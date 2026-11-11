from __future__ import annotations

import hashlib
import json
import math
import threading
import time
import uuid
from datetime import UTC, datetime

from analytics_support import AnalyticsError
from app.integrations.http_config import ProductError, read_config, remote_json


def timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def integer(value: object, name: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ProductError(
            400, "invalid_field", f"{name}: допустимо целое число {minimum}…{maximum}."
        )
    return value


def validate_changes(changes: dict) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise ProductError(400, "invalid_changes", "Укажите изменяемые поля.")
    converted = dict(changes)
    if "per_attempt_timeout_ns" in converted:
        if "timeout_ms" in converted:
            raise ProductError(400, "invalid_changes", "Задайте только один вариант таймаута.")
        value = converted.pop("per_attempt_timeout_ns")
        if not isinstance(value, str) or not value.isdigit() or len(value) > 19:
            raise ProductError(400, "invalid_timeout", "Таймаут задаётся строкой наносекунд.")
        converted["timeout_ms"] = int(value) / 1_000_000
    for key, value in converted.items():
        if key in {"timeout_ms", "backoff_ms"}:
            low = 0 if key == "backoff_ms" else 0.001
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not low <= value <= 3_600_000
            ):
                raise ProductError(400, "invalid_field", f"Некорректное значение {key}.")
        elif key == "max_attempts":
            integer(value, key, 1, 10)
        elif key == "retry_on":
            if not isinstance(value, list) or any(
                not isinstance(v, str)
                or (
                    v not in {"timeout", "connect", "4xx", "5xx"}
                    and not (v.isdigit() and 400 <= int(v) <= 599)
                )
                for v in value
            ):
                raise ProductError(
                    400, "invalid_retry_on", "Ожидался список HTTP-ошибок, timeout или connect."
                )
        elif key == "work":
            if not isinstance(value, dict):
                raise ProductError(400, "invalid_work", "Ожидался профиль обработки.")
            if value.get("kind") == "fixed" and set(value) == {"kind", "delay_ms"}:
                bounds = [value["delay_ms"]]
            elif value.get("kind") == "lognormal" and set(value) == {"kind", "p50_ms", "p99_ms"}:
                bounds = [value["p50_ms"], value["p99_ms"]]
                if (
                    not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in bounds)
                    or not 0 < bounds[0] <= bounds[1]
                ):
                    raise ProductError(400, "invalid_work", "Нужно 0 < p50 ≤ p99.")
            else:
                raise ProductError(400, "invalid_work", "Неизвестный профиль обработки.")
            if any(
                isinstance(v, bool)
                or not isinstance(v, (int, float))
                or not math.isfinite(v)
                or not 0 <= v <= 3_600_000
                for v in bounds
            ):
                raise ProductError(
                    400, "invalid_work", "Задержки должны быть конечными и неотрицательными."
                )
        else:
            raise ProductError(400, "unsupported_field", f"Поле {key} не поддерживается.")
    return converted


def select_target(config: dict, intent: dict) -> dict:
    if not isinstance(config, dict) or not isinstance(intent, dict):
        raise ProductError(502, "invalid_config", "Ожидался объект конфигурации.")
    has_call = "call_ref" in intent and intent["call_ref"] is not None
    has_route = "route" in intent and intent["route"] is not None
    if has_call == has_route:
        raise ProductError(400, "invalid_target", "Укажите один call_ref или входящий маршрут.")
    collection, field = ("calls", "call_ref") if has_call else ("routes", "route")
    selected = intent[field]
    if not isinstance(selected, str) or not selected.strip():
        raise ProductError(
            400, "invalid_target", "Идентификатор операции должен быть непустой строкой."
        )
    items = config.get(collection, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise ProductError(502, "invalid_config", "Адаптер вернул некорректный список операций.")
    for item in items:
        if not isinstance(item.get(field), str):
            raise ProductError(
                502, "invalid_config", "У операции отсутствует строковый идентификатор."
            )
        if item[field] == intent[field]:
            return item
    raise ProductError(404, "target_not_found", "Операция отсутствует в конфиге.")


class Plans:
    def __init__(
        self, registry, records, mutation_lock: threading.RLock, lab_guard,
        resolve_recommendation=None,
    ):
        self.registry = registry
        self.records = records
        self.lock = mutation_lock
        self.lab_guard = lab_guard
        self.resolve_recommendation = resolve_recommendation

    def create(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ProductError(400, "invalid_request", "Ожидался объект запроса.")
        environment = body.get("environment_id")
        service_id = body.get("service_id")
        if not isinstance(environment, str) or not environment.strip():
            raise ProductError(400, "invalid_environment_id", "Нужно указать окружение.")
        if not isinstance(service_id, str) or not service_id.strip():
            raise ProductError(400, "invalid_service_id", "Нужно указать сервис.")
        service = self.registry.service(environment, service_id)
        if not service:
            raise ProductError(404, "service_not_found", "Сервис не зарегистрирован.")
        with self.lock:
            if service.get("is_lab"):
                self.lab_guard()
            config = read_config(service)
            revision = body.get("expected_config_revision")
            if (
                isinstance(revision, bool)
                or not isinstance(revision, (int, str))
                or not str(revision).strip()
            ):
                raise ProductError(400, "invalid_config_revision", "Укажите ревизию конфигурации.")
            if revision != config["revision"]:
                raise ProductError(
                    409,
                    "stale_revision",
                    "Конфигурация изменилась. Обновите значения.",
                    {"expected": revision, "current": config["revision"]},
                )
            target = select_target(config, body)
            changes = validate_changes(body.get("set"))
            if any(key not in target.get("write_fields", []) for key in changes):
                raise ProductError(403, "field_forbidden", "Изменение одного из полей запрещено.")
            plan_id = "plan-" + uuid.uuid4().hex
            selector = {key: body[key] for key in ("call_ref", "route") if body.get(key)}
            label = f"{service['service_id']} / {body.get('call_ref') or body.get('route')}"
            plan = {
                "plan_id": plan_id,
                "environment_id": environment,
                "service_id": service["service_id"],
                **selector,
                "finding_id": body.get("finding_id", ""),
                "target": label,
                "expected_revision": revision,
                "set": changes,
                "before": target,
                "expires_at": datetime.fromtimestamp(time.time() + 600, UTC).isoformat(),
                "changes": [
                    {
                        "field": key,
                        "before": self._display(target.get(key)),
                        "after": self._display(value),
                    }
                    for key, value in changes.items()
                    if target.get(key) != value
                ],
                "warnings": [],
                "apply_enabled": True,
                "occurrence_id": body.get("occurrence_id"),
                "recommendation_id": body.get("recommendation_id"),
            }
            plan["plan_hash"] = digest(plan)
            self.records.put("plan", plan_id, plan)
            return plan

    @staticmethod
    def _display(value: object) -> str:
        return (
            json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        )

    def _check_recommendation(self, plan: dict) -> None:
        if not plan.get("recommendation_id"):
            return
        if self.resolve_recommendation is None:
            raise ProductError(409, "stale_recommendation", "Не удалось подтвердить рекомендацию.")
        try:
            intent = self.resolve_recommendation(
                plan["environment_id"], plan["occurrence_id"], plan["recommendation_id"]
            )
        except AnalyticsError as exc:
            raise ProductError(exc.status, exc.code, str(exc)) from exc
        target = {"service_id": plan["service_id"], "call_ref": plan.get("call_ref")}
        if (intent.get("target") != target
                or intent.get("expected_config_revision") != plan["expected_revision"]
                or validate_changes(intent.get("set")) != plan["set"]):
            raise ProductError(409, "stale_recommendation", "Действующая рекомендация изменилась.")

    def apply(self, plan_id: str, body: dict) -> dict:
        if not isinstance(body, dict):
            raise ProductError(400, "invalid_request", "Ожидался объект запроса.")
        request_id = body.get("request_id")
        if not isinstance(request_id, str) or not 1 <= len(request_id) <= 200:
            raise ProductError(400, "invalid_request_id", "Нужен request_id.")
        plan_hash = body.get("plan_hash")
        if not isinstance(plan_hash, str) or not plan_hash:
            raise ProductError(400, "invalid_plan_hash", "Нужен plan_hash.")
        fingerprint = digest({"plan_id": plan_id, "plan_hash": plan_hash})
        with self.lock:
            previous = self.records.get("apply_request", request_id)
            if previous:
                if previous["fingerprint"] != fingerprint:
                    raise ProductError(
                        409,
                        "request_id_conflict",
                        "request_id уже использован для другой операции.",
                    )
                return self.records.get("operation", previous["operation_id"])
            plan = self.records.get("plan", plan_id)
            if not plan:
                raise ProductError(404, "plan_not_found", "План не найден.")
            if plan_hash != plan["plan_hash"]:
                raise ProductError(409, "plan_hash_mismatch", "Подтверждён другой план.")
            if datetime.fromisoformat(plan["expires_at"]) < datetime.now(UTC):
                raise ProductError(409, "plan_expired", "Срок плана истёк.")
            service = self.registry.service(plan["environment_id"], plan["service_id"])
            if not service:
                raise ProductError(404, "service_not_found", "Сервис не зарегистрирован.")
            if service.get("is_lab"):
                self.lab_guard()
            current = read_config(service)
            if current["revision"] != plan["expected_revision"]:
                raise ProductError(409, "stale_revision", "Конфигурация изменилась.")
            target = select_target(current, plan)
            if any(field not in target.get("write_fields", []) for field in plan["set"]):
                raise ProductError(403, "field_forbidden", "Разрешение на запись изменилось.")
            self._check_recommendation(plan)
            for active in self.records.items("operation"):
                if (
                    active["environment_id"] == plan["environment_id"]
                    and active["service_id"] == plan["service_id"]
                    and active["status"] == "applying"
                ):
                    raise ProductError(
                        409, "operation_pending", "Предыдущее изменение сервиса ещё выполняется."
                    )
            operation_id = "op-" + uuid.uuid4().hex
            operation = {
                "operation_id": operation_id,
                "request_id": request_id,
                "plan_id": plan_id,
                "plan_hash": plan["plan_hash"],
                "environment_id": plan["environment_id"],
                "service_id": plan["service_id"],
                "finding_id": plan.get("finding_id", ""),
                "target": plan["target"],
                "requested_at": timestamp(),
                "status": "applying",
                "verification": None,
                "actual_changes": [],
            }
            self.records.put_many(
                [
                    ("operation", operation_id, operation),
                    (
                        "apply_request",
                        request_id,
                        {"fingerprint": fingerprint, "operation_id": operation_id},
                    ),
                ]
            )
            threading.Thread(target=self._execute, args=(operation_id,), daemon=True).start()
            return operation

    def _execute(self, operation_id: str, recovering: bool = False) -> None:
        operation = self.records.get("operation", operation_id)
        plan = self.records.get("plan", operation["plan_id"])
        service = self.registry.service(plan["environment_id"], plan["service_id"])
        if not service:
            operation.update(
                status="unknown",
                error={
                    "code": "service_unavailable",
                    "message": "Сервис недоступен после перезапуска.",
                },
            )
            self.records.put("operation", operation_id, operation)
            return
        if not recovering:
            patch_started = False
            try:
                selector = {key: plan[key] for key in ("call_ref", "route") if plan.get(key)}
                # Another laboratory plan can change the parent after this one
                # was accepted. Serialize the final check and write with other PATCHes.
                with self.lock:
                    self._check_recommendation(plan)
                    patch_started = True
                    remote_json(
                        service,
                        "PATCH",
                        "/_config",
                        {
                            "request_id": operation["request_id"],
                            "expected_revision": plan["expected_revision"],
                            **selector,
                            "set": plan["set"],
                        },
                    )
            except ProductError as exc:
                if not patch_started or exc.status < 500:
                    operation.update(
                        status="rejected", error={"code": exc.code, "message": str(exc)}
                    )
                    self.records.put("operation", operation_id, operation)
                    return
                operation.update(status="unknown", error={"code": exc.code, "message": str(exc)})
                self.records.put("operation", operation_id, operation)
                self._retry_readback(operation, plan, service)
                return
            except Exception:
                if not patch_started:
                    operation.update(
                        status="rejected",
                        error={"code": "recommendation_unavailable",
                               "message": "Не удалось проверить рекомендацию перед записью."},
                    )
                    self.records.put("operation", operation_id, operation)
                    return
                operation.update(
                    status="unknown",
                    error={
                        "code": "operation_unconfirmed",
                        "message": "Результат операции уточняется.",
                    },
                )
                self.records.put("operation", operation_id, operation)
                self._retry_readback(operation, plan, service)
                return

        try:
            self._readback(operation, plan, service)
        except ProductError as exc:
            operation.update(status="unknown", error={"code": exc.code, "message": str(exc)})
            self.records.put("operation", operation_id, operation)
            self._retry_readback(operation, plan, service)
        except Exception:
            operation.update(
                status="unknown",
                error={
                    "code": "operation_unconfirmed",
                    "message": "Результат операции уточняется.",
                },
            )
        self.records.put("operation", operation_id, operation)

    def _retry_readback(self, operation: dict, plan: dict, service: dict) -> None:
        for _ in range(5):
            time.sleep(1)
            try:
                self._readback(operation, plan, service)
                break
            except Exception:
                continue
        self.records.put("operation", operation["operation_id"], operation)

    def _readback(self, operation: dict, plan: dict, service: dict) -> None:
        result = remote_json(service, "GET", f"/_config/operations/{operation['request_id']}")
        if result.get("request_id") != operation["request_id"] or result.get("status") not in {
            "applied",
            "no_op",
        }:
            raise ProductError(503, "operation_unconfirmed", "Сервис ещё не подтвердил запись.")
        current = read_config(service)
        if result.get("new_revision") is not None and current["revision"] != result["new_revision"]:
            raise ProductError(
                503, "readback_mismatch", "Ревизия конфигурации не совпала с результатом записи."
            )
        actual = select_target(current, plan)
        if any(actual.get(field) != value for field, value in plan["set"].items()):
            raise ProductError(
                503, "readback_mismatch", "Действующие значения отличаются от плана."
            )
        operation.update(
            status="applied" if plan["changes"] else "no_op",
            new_revision=current["revision"],
            actual_changes=plan["changes"],
            confirmed_at=timestamp(),
        )
        operation.pop("error", None)

    def recover(self) -> None:
        for operation in self.records.items("operation"):
            if operation["status"] in {"applying", "unknown"}:
                threading.Thread(
                    target=self._execute, args=(operation["operation_id"], True), daemon=True
                ).start()
