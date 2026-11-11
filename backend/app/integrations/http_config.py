from __future__ import annotations

import requests


class ProductError(Exception):
    def __init__(self, status: int, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.status = status
        self.code = code
        self.details = details or {}


def remote_json(
    service: dict, method: str, path: str, body: dict | None = None, timeout: float = 4
) -> dict:
    base = service.get("control_url")
    if not base:
        raise ProductError(403, "config_unavailable", "У сервиса нет адаптера конфигурации.")
    try:
        response = requests.request(method, base.rstrip("/") + path, json=body, timeout=timeout)
    except requests.RequestException as exc:
        raise ProductError(
            503, "adapter_unavailable", "Управляющий API сервиса недоступен."
        ) from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise ProductError(
            502, "invalid_adapter_response", "Адаптер вернул некорректный JSON."
        ) from exc
    if not response.ok:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        if not isinstance(error, dict):
            error = {}
        code = error.get("code", "adapter_rejected")
        message = error.get("message", "Сервис отклонил изменение.")
        details = error.get("details")
        if not isinstance(code, str) or not code:
            code = "adapter_rejected"
        if not isinstance(message, str) or not message:
            message = "Сервис отклонил изменение."
        if not isinstance(details, dict):
            details = None
        raise ProductError(response.status_code, code, message, details)
    if not isinstance(data, dict):
        raise ProductError(502, "invalid_adapter_response", "Ожидался объект конфигурации.")
    return data


def read_config(service: dict) -> dict:
    value = remote_json(service, "GET", "/_config")
    if value.get("service_id") != service["service_id"]:
        raise ProductError(
            409, "service_identity_mismatch", "Управляющий адрес относится к другому сервису."
        )
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, str) or not revision.strip():
        raise ProductError(
            502, "invalid_adapter_response", "Адаптер не вернул ревизию конфигурации."
        )
    allowed_fields = service.get("write_fields", [])
    if not isinstance(allowed_fields, list) or any(
        not isinstance(field, str) for field in allowed_fields
    ):
        raise ProductError(
            502, "invalid_adapter_response", "Реестр содержит некорректные разрешения на запись."
        )
    allowed = set(allowed_fields)

    calls = value.get("calls", [])
    if not isinstance(calls, list) or any(not isinstance(call, dict) for call in calls):
        raise ProductError(
            502, "invalid_adapter_response", "Адаптер вернул некорректный список операций."
        )
    routes = value.get("routes", {})
    if isinstance(routes, dict):
        if any(not isinstance(settings, dict) for settings in routes.values()):
            raise ProductError(
                502, "invalid_adapter_response", "Адаптер вернул некорректный список маршрутов."
            )
        value["routes"] = [dict(settings, route=route) for route, settings in routes.items()]
    routes = value.get("routes", [])
    if not isinstance(routes, list) or any(not isinstance(route, dict) for route in routes):
        raise ProductError(
            502, "invalid_adapter_response", "Адаптер вернул некорректный список маршрутов."
        )

    def intersect_permissions(target: dict) -> None:
        fields = target.get("write_fields", [])
        if not isinstance(fields, list) or any(not isinstance(field, str) for field in fields):
            raise ProductError(
                502, "invalid_adapter_response", "Адаптер вернул некорректные разрешения на запись."
            )
        target["write_fields"] = sorted(set(fields) & allowed)

    value["display_name"] = service.get("display_name", service["service_id"])
    value["environment_id"] = service["environment_id"]
    value["is_lab"] = bool(service.get("is_lab"))
    value["read_config"] = True
    value["write_fields"] = sorted(allowed)
    value["apply_recommendation"] = bool(service.get("is_lab"))
    value["capabilities"] = {
        "read_config": True,
        "write_fields": sorted(allowed),
        "apply_recommendation": bool(service.get("is_lab")),
    }
    for call in calls:
        intersect_permissions(call)
    for route in routes:
        intersect_permissions(route)
    return value
