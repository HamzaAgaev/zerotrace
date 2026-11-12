from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from flask import Flask, Response, jsonify, request, send_from_directory
from werkzeug.exceptions import HTTPException

from app.analytics_api.backend_routes import BackendApp
from app.analytics_api.facade import AnalyticsFacade
from app.analytics_api.jobs import AuditJobs
from app.control.analytics_results import AnalyticsResults
from app.control.plans import Plans, timestamp
from app.control.store import ControlStore
from app.integrations.http_config import ProductError, read_config
from app.integrations.registry import Registry
from app.otlp_api.handler import handle_traces
from app.server.assembly import QUIET_AFTER_NS, AssemblyTicker
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

    assembly_ticker = AssemblyTicker(
        telemetry,
        interval_s=float(config.get("assembly_tick_s", 1.0)),
        quiet_after_ns=int(config.get("assembly_quiet_after_ns", QUIET_AFTER_NS)),
    ).start()

    analytics = AnalyticsFacade(
        telemetry, records, results=AnalyticsResults(records), environments=registry.environments
    )
    api = BackendApp(analytics.service)
    audit_jobs = AuditJobs()

    def resolve_plan(environment_id, occurrence_id, recommendation_id):
        return analytics.resolve_recommendation(
            environment_id, occurrence_id, recommendation_id, snapshots(environment_id)
        )

    plans = Plans(registry, records, mutation_lock, lab.guard_edit, resolve_plan)
    app.extensions.update(
        telemetry=telemetry, records=records, registry=registry, lab=lab, plans=plans,
        audit_jobs=audit_jobs, assembly_ticker=assembly_ticker,
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
                    **({"request_id": exc.request_id} if exc.request_id is not None else {}),
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

    @app.get("/api/v1/environments")
    def environments():
        return jsonify(envelope({"items": registry.environments}, "environments"))

    @app.get("/api/v1/capabilities")
    def capabilities():
        env = environment()
        last_received = telemetry.last_received_at_ns(env)
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
            resolved = resolve_plan(
                intent["environment_id"], intent.get("occurrence_id"), intent["recommendation_id"]
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

    @app.get("/api/v1/metrics")
    def metrics():
        from app.analytics_api.metrics import period_metrics
        env = environment()
        try:
            minutes = int(request.args.get('period_minutes', '30'))
        except ValueError:
            raise AnalyticsError('invalid_period', 'Недопустимый период.', 400)
        with analytics.source.read() as view:
            return jsonify(envelope(period_metrics(view, env, minutes), 'metrics'))

    @app.get("/api/v1/traces")
    @app.get("/api/v1/traces/<trace_id>")
    @app.get("/api/v1/audit")
    @app.get("/api/v1/state")
    @app.get("/api/v1/policies")
    @app.get("/api/v1/report")
    @app.get("/api/v1/reports/<report_id>")
    def analytics_read(trace_id=None, report_id=None):
        env = environment() if report_id is None or "environment_id" in request.args else None
        if request.path == "/api/v1/traces" and request.args.get("defer") == "true" and request.args.get("rules", "[]") != "[]":
            params = dict(request.args)
            params['environment_id'] = env
            params.pop('job_id', None)
            params.pop('defer', None)
            scope = (env, 'trace-search', urlencode(sorted(params.items())), None)
            def calculate_search():
                with analytics.configured(env, snapshots(env)):
                    return api.dispatch({'REQUEST_METHOD': 'GET', 'PATH_INFO': '/api/v1/traces',
                                         'QUERY_STRING': urlencode(params)})
            job_id = request.args.get('job_id') or audit_jobs.start(scope, calculate_search)
            result = audit_jobs.result(scope, job_id)
            return jsonify(result) if result is not None else (jsonify(envelope({'status': 'pending', 'job_id': job_id}, 'audit-job')), 202)
        audit_ref = None
        if request.path == "/api/v1/audit" and any(k in request.args for k in ("trace_id", "generation", "revision")):
            from analytics_support import ref_key
            try:
                audit_ref = {"environment_id": env, "trace_id": request.args.get("trace_id"),
                             "generation": int(request.args["generation"]), "revision": int(request.args["revision"])}
            except (KeyError, ValueError):
                raise AnalyticsError("invalid_trace_ref", "Укажите полную версию трейса.", 400)
            ref_key(audit_ref)
        if request.path == "/api/v1/audit" and request.args.get("defer") == "true":
            profile, mode = request.args.get("view", "v1"), request.args.get("mode")
            if profile not in {"v1", "full"}:
                raise AnalyticsError("invalid_view", "Expected v1 or full", 400)
            if mode not in {None, "config_aware", "trace_only"}:
                raise AnalyticsError("invalid_mode", "Expected trace_only or config_aware", 400)
            scope = (env, profile, mode, tuple(audit_ref.values()) if audit_ref else None)

            def calculate_audit():
                params = {"environment_id": env, "view": profile}
                if mode is not None:
                    params["mode"] = mode
                configs = snapshots(env) if mode != "trace_only" else None
                with analytics.configured(env, configs, trace_ref=audit_ref):
                    return api.dispatch({"REQUEST_METHOD": "GET", "PATH_INFO": "/api/v1/audit",
                                         "QUERY_STRING": urlencode(params)})

            job_id = request.args.get("job_id") or audit_jobs.start(scope, calculate_audit)
            result = audit_jobs.result(scope, job_id)
            if result is not None:
                return jsonify(result)
            return jsonify(envelope({"status": "pending", "job_id": job_id}, "audit-job")), 202

        environ = dict(request.environ)
        # Flask implements HEAD through the GET handler and omits its body.
        environ["REQUEST_METHOD"] = "GET"
        if env and "environment_id" not in request.args:
            params = [*request.args.items(multi=True), ("environment_id", env)]
            environ["QUERY_STRING"] = urlencode(params)
        saved_snapshot = request.path == "/api/v1/report" and request.args.get("snapshot_id")
        trace_only = (request.path == "/api/v1/traces" or trace_id is not None) and (
            request.args.get("include_findings") == "false" and request.args.get("rules", "[]") == "[]"
        )
        skip_snapshots = report_id is not None or saved_snapshot or trace_only
        configs = snapshots(env) if env and not skip_snapshots else None
        with analytics.configured(env, configs, trace_ref=audit_ref):
            result = api.dispatch(environ)
        if isinstance(result, str):
            return Response(result, mimetype="text/html")
        return jsonify(result)

    @app.post("/api/v1/projections")
    def projections():
        with analytics.configured() as service:
            return jsonify(envelope(service.project(body()), "projection"))

    @app.post("/api/v1/comparisons")
    def comparisons():
        value = body()
        if not ({"baseline_run_id", "candidate_run_id"} & value.keys()) or (
            {"baseline_trace_refs", "candidate_trace_refs"} & value.keys()
        ):
            with analytics.configured() as service:
                return jsonify(envelope(service.compare(value), "comparison"))
        if any(not isinstance(value.get(key), str) or not value[key]
               for key in ("baseline_run_id", "candidate_run_id")):
            raise ProductError(400, "invalid_request", "Укажите идентификаторы обоих прогонов.")
        left = lab.get_run(value["baseline_run_id"])
        right = lab.get_run(value["candidate_run_id"])
        return jsonify(envelope(analytics.compare_runs(left, right), "comparison"))

    @app.get("/")
    @app.get("/<path:asset>")
    def frontend(asset="index.html"):
        if asset.startswith("api/") or asset.startswith("v1/"):
            raise ProductError(404, "route_not_found", "Маршрут API не найден.")
        folder = Path(config.get("frontend_directory", "/app/frontend/dist"))
        return send_from_directory(folder, asset)

    return app
