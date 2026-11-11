from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app.control.plans import Plans, timestamp
from app.control.store import ControlStore
from app.data_api.adapter import SpanStoreAdapter, make_graph_builder
from app.data_api.handlers import ApiError, get_trace, list_traces
from app.integrations.http_config import ProductError, read_config
from app.integrations.registry import Registry
from app.otlp_api.handler import handle_traces
from contracts import IngestLimits
from repository import SpanStore


def envelope(payload: object, name: str = "product") -> dict:
    return {
        "schema": f"zerotrace.{name}",
        "schema_version": "1.0.0",
        "generated_at": timestamp(),
        "producer_version": "a-0.1.0",
        "payload": payload,
    }


def create_app(config: dict) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024
    path = Path(config.get("database", "/data/zerotrace.db"))
    path.parent.mkdir(parents=True, exist_ok=True)
    telemetry = SpanStore(path)
    records = ControlStore(path)
    registry = Registry(config, telemetry)
    mutation_lock = threading.RLock()
    from app.control.laboratory import Laboratory

    lab = Laboratory(config, registry, telemetry, records, mutation_lock)
    plans = Plans(registry, records, mutation_lock, lab.guard_edit)
    app.extensions.update(
        telemetry=telemetry, records=records, registry=registry, lab=lab, plans=plans
    )
    plans.recover()

    @app.before_request
    def check_origin():
        if request.method in {"POST", "PATCH", "PUT", "DELETE"} and request.path.startswith(
            "/api/"
        ):
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                raise ProductError(403, "origin_forbidden", "Запись разрешена с origin интерфейса.")

    @app.errorhandler(ProductError)
    def product_error(exc):
        return jsonify(
            {
                "error": {
                    "code": exc.code,
                    "message": str(exc),
                    "details": exc.details,
                    "retryable": exc.status >= 500,
                }
            }
        ), exc.status

    @app.errorhandler(HTTPException)
    def http_error(exc):
        return jsonify(
            {
                "error": {
                    "code": exc.name.lower().replace(" ", "_"),
                    "message": exc.description,
                    "retryable": False,
                }
            }
        ), exc.code

    from analytics_support import AnalyticsError

    @app.errorhandler(AnalyticsError)
    def analytics_error(exc):
        return jsonify(exc.body()), exc.status

    def body() -> dict:
        value = request.get_json()
        if not isinstance(value, dict):
            raise ProductError(400, "invalid_request", "Ожидался JSON-объект.")
        return value

    def environment() -> str:
        value = request.args.get("environment_id", "shop")
        if value not in {item["environment_id"] for item in registry.environments}:
            raise ProductError(404, "environment_not_found", "Окружение не зарегистрировано.")
        return value

    def integer_query(name: str, default=None):
        value = request.args.get(name)
        if value is None:
            return default
        if not value.isdigit() or len(value) > 12:
            raise ProductError(400, "invalid_" + name, "Ожидалось неотрицательное целое число.")
        return int(value)

    def data_response(value):
        if isinstance(value, ApiError):
            return jsonify(value.to_dict()), value.status
        return jsonify(value)

    def snapshots(env: str) -> dict:
        services = [
            item
            for (scope, _), item in registry.services.items()
            if scope == env and item.get("control_url")
        ]

        def fetch(item):
            try:
                registered = registry.service(item["environment_id"], item["service_id"])
                return item["service_id"], read_config(registered)
            except ProductError:
                return item["service_id"], None

        with ThreadPoolExecutor(max_workers=4) as pool:
            return {key: value for key, value in pool.map(fetch, services) if value is not None}

    @app.get("/healthz")
    @app.get("/health")
    def health():
        return jsonify({"status": "ok", "version": "a-0.1.0"})

    @app.post("/v1/traces")
    def otlp():
        response = handle_traces(
            telemetry,
            registry,
            request.get_data(),
            dict(request.headers),
            IngestLimits(gzip_supported=True),
            time.time_ns(),
        )
        return Response(response.body, status=response.status, content_type=response.media_type)

    @app.get("/api/v1/traces")
    def traces():
        return data_response(
            list_traces(
                SpanStoreAdapter(telemetry),
                environment(),
                integer_query("limit", 100),
                integer_query("offset", 0),
                graph_builder=make_graph_builder(telemetry),
            )
        )

    @app.get("/api/v1/traces/<trace_id>")
    def trace(trace_id):
        return data_response(
            get_trace(
                SpanStoreAdapter(telemetry),
                make_graph_builder(telemetry),
                environment(),
                trace_id,
                integer_query("generation"),
                integer_query("revision"),
            )
        )

    @app.get("/api/v1/environments")
    def environments():
        return jsonify(envelope({"items": registry.environments}, "environments"))

    @app.get("/api/v1/capabilities")
    def capabilities():
        env = environment()
        metas = telemetry.list_trace_metas(env)
        last_received = max((item.last_received_at_ns for item in metas), default=0)
        receiving = last_received > time.time_ns() - 30_000_000_000
        return jsonify(
            envelope(
                {
                    "mode": "live",
                    "api_version": "1.0.0",
                    "source_name": env,
                    "source_state": "receiving" if receiving else "idle",
                    "apply_enabled": True,
                    "limits": {"max_trace_spans": 2000},
                    "features": ["configuration", "lab", "runs", "comparisons"],
                },
                "capabilities",
            )
        )

    @app.get("/api/v1/services")
    def services():
        env = environment()
        items = {}
        for (scope, name), item in list(registry.services.items()):
            if scope == env:
                items[name] = {
                    "service_id": name,
                    "display_name": item.get("display_name", name),
                    "read_config": bool(item.get("control_url")),
                    "write_fields": item.get("write_fields", []),
                    "is_lab": bool(item.get("is_lab")),
                    "apply_recommendation": bool(item.get("is_lab")),
                }
                try:
                    registry.service(env, name)
                except ProductError as exc:
                    items[name].update(
                        read_config=False,
                        write_fields=[],
                        apply_recommendation=False,
                        identity_state=exc.code,
                    )
        for meta in telemetry.list_trace_metas(env)[:100]:
            for span in telemetry.get_spans(meta.trace_ref) or []:
                items.setdefault(
                    span.service,
                    {
                        "service_id": span.service,
                        "display_name": span.service,
                        "read_config": False,
                        "write_fields": [],
                        "is_lab": env == "sandbox",
                    },
                )
        return jsonify(
            envelope(
                {"items": sorted(items.values(), key=lambda item: item["service_id"])}, "services"
            )
        )

    @app.get("/api/v1/services/<service_id>/config")
    def service_config(service_id):
        service = registry.service(environment(), service_id)
        if not service:
            raise ProductError(404, "service_not_found", "Сервис не зарегистрирован.")
        return jsonify(envelope(read_config(service), "config"))

    @app.post("/api/v1/change-plans")
    def create_plan():
        intent = body()
        if intent.get("recommendation_id"):
            if intent.get("environment_id") != "sandbox":
                raise ProductError(
                    403, "recommendation_lab_only", "Применение рекомендации доступно на стенде."
                )
            resolved = analytics().resolve_recommendation(
                intent["environment_id"],
                intent.get("occurrence_id"),
                intent["recommendation_id"],
                snapshots(intent["environment_id"]),
            )
            intent.update(resolved)
            intent.update(resolved.get("target", {}))
        return jsonify(envelope(plans.create(intent), "change-plan")), 201

    @app.post("/api/v1/change-plans/<plan_id>/apply")
    def apply_plan(plan_id):
        return jsonify(envelope(plans.apply(plan_id, body()), "operation")), 202

    @app.get("/api/v1/journal")
    def journal():
        env = environment()
        entries = [item for item in records.items("operation") if item["environment_id"] == env]
        return jsonify(
            envelope(
                sorted(entries, key=lambda item: item["requested_at"], reverse=True), "journal"
            )
        )

    @app.get("/api/v1/journal/<operation_id>")
    def operation(operation_id):
        item = records.get("operation", operation_id)
        if not item or item["environment_id"] != environment():
            raise ProductError(404, "operation_not_found", "Операция не найдена.")
        return jsonify(envelope(item, "operation"))

    @app.post("/api/v1/lab/stands")
    def create_stand():
        return jsonify(envelope(lab.create(body()), "stand")), 201

    @app.get("/api/v1/lab/stands/active")
    def active_stand():
        return jsonify(envelope(lab.active(), "stand"))

    @app.delete("/api/v1/lab/stands/<stand_id>")
    def stop_stand(stand_id):
        return jsonify(envelope(lab.stop(stand_id), "stand"))

    @app.route("/api/v1/lab/stands/<stand_id>/fault", methods=["PUT", "DELETE"])
    def fault(stand_id):
        value = request.get_json(silent=True) or {}
        return jsonify(
            envelope(lab.set_fault(stand_id, value, clear=request.method == "DELETE"), "stand")
        )

    @app.post("/api/v1/lab/stands/<stand_id>/runs")
    def start_run(stand_id):
        return jsonify(envelope(lab.run(stand_id, body()), "run")), 202

    @app.get("/api/v1/lab/runs/<run_id>")
    def run(run_id):
        return jsonify(envelope(lab.get_run(run_id), "run"))

    @app.get("/api/v1/lab/runs")
    def runs():
        stand_id = request.args.get("stand_id")
        items = [
            lab.get_run(item["run_id"])
            for item in records.items("run")
            if not stand_id or item["stand_id"] == stand_id
        ]
        return jsonify(envelope({"items": items}, "runs"))

    facade = None

    def analytics():
        nonlocal facade
        if facade is None:
            from app.analytics_api.facade import AnalyticsFacade
            from app.control.analytics_results import AnalyticsResults

            facade = AnalyticsFacade(telemetry, records, results=AnalyticsResults(records))
        return facade

    @app.get("/api/v1/audit")
    def audit():
        env = environment()
        from app.analytics_api.dto import audit_view

        return jsonify(envelope(audit_view(analytics().audit(env, snapshots(env))), "audit"))

    @app.post("/api/v1/comparisons")
    def comparisons():
        value = body()
        left = lab.get_run(value.get("baseline_run_id", ""))
        right = lab.get_run(value.get("candidate_run_id", ""))
        if any(item["trace_collection"]["status"] == "waiting" for item in (left, right)):
            raise ProductError(409, "traces_pending", "Ожидаем телеметрию прогонов.")
        return jsonify(envelope(analytics().compare_runs(left, right), "comparison"))

    @app.get("/api/v1/report")
    def report():
        env = environment()
        return jsonify(envelope(analytics().report(env, snapshots(env)), "report"))

    @app.get("/")
    @app.get("/<path:asset>")
    def frontend(asset="index.html"):
        if asset.startswith("api/") or asset.startswith("v1/"):
            raise ProductError(404, "route_not_found", "Маршрут API не найден.")
        folder = Path(config.get("frontend_directory", "/app/frontend/dist"))
        return send_from_directory(folder, asset)

    return app
