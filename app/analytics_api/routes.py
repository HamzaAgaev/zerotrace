"""WSGI entry point, also mountable on the team's common Flask application."""
from __future__ import annotations

import json
import logging
import re
from http import HTTPStatus
from urllib.parse import parse_qs
from uuid import uuid4

from analytics_support import AnalyticsError
from app.reports.bundle import render_html
from .dto import audit_view, envelope

PREFIX = "/api/v1/"
MAX_REQUEST = 256 * 1024
ENDPOINTS = {"audit": "GET", "state": "GET", "policies": "GET", "projections": "POST",
             "comparisons": "POST", "report": "GET", "reports/<report_id>": "GET"}


def _query(environ):
    try:
        fields = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True, max_num_fields=16)
    except ValueError as exc:
        raise AnalyticsError("invalid_query", str(exc), 400) from exc
    if any(len(v) != 1 for v in fields.values()):
        raise AnalyticsError("invalid_query", "Duplicate query parameter", 400)
    return {key: values[0] for key, values in fields.items()}


def _json_body(environ):
    if environ.get("CONTENT_TYPE", "").partition(";")[0].lower() != "application/json":
        raise AnalyticsError("unsupported_media_type", "Expected application/json", 415)
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        raise AnalyticsError("invalid_length", "Invalid content length", 400) from None
    if length > MAX_REQUEST:
        raise AnalyticsError("request_too_large", "Request size limit exceeded", 413)
    if length <= 0:
        raise AnalyticsError("invalid_request", "Expected a JSON object", 400)
    def invalid_constant(token):
        raise ValueError(f"Invalid JSON number: {token}")
    try:
        body = json.loads(environ["wsgi.input"].read(length), parse_constant=invalid_constant)
    except (ValueError, RecursionError) as exc:
        raise AnalyticsError("invalid_json", "Malformed JSON body", 400) from exc
    if not isinstance(body, dict):
        raise AnalyticsError("invalid_request", "Expected a JSON object", 400)
    return body


class AnalyticsApp:
    def __init__(self, service):
        self.service = service

    def dispatch(self, environ):
        path = environ.get("PATH_INFO", "")
        if not path.startswith(PREFIX):
            raise AnalyticsError("not_found", "Unknown route", 404)
        name = path[len(PREFIX):]
        saved_id = None
        match = re.fullmatch(r"reports/([a-f0-9]{64})", name)
        if match:
            saved_id, name = match[1], "reports/<report_id>"
        if name not in ENDPOINTS:
            raise AnalyticsError("not_found", "Unknown route", 404)
        if environ["REQUEST_METHOD"] != ENDPOINTS[name]:
            raise AnalyticsError("method_not_allowed", "Unsupported HTTP method", 405)
        query = _query(environ)
        if name in {"audit", "state", "policies", "report"} and not query.get("environment_id"):
            raise AnalyticsError("missing_environment", "Select an environment", 400)
        env = query.get("environment_id")
        if name == "audit":
            profile = query.get("view", "v1")
            if profile not in {"v1", "full"}:
                raise AnalyticsError("invalid_view", "Expected v1 or full", 400)
            return envelope(name, audit_view(self.service.audit(env, query.get("mode")),
                                             include_policy=profile == "full"))
        if name == "state":
            return envelope("snapshot", self.service.state(env))
        if name == "policies":
            return envelope("policies", self.service.state(env)["policies"])
        if name == "projections":
            return envelope("projection", self.service.project(_json_body(environ)))
        if name == "comparisons":
            return envelope("comparison", self.service.compare(_json_body(environ)))
        if saved_id:
            report = self.service.saved_report(saved_id, env)
        else:
            report = self.service.report(env, snapshot_id=query.get("snapshot_id"),
                                         projection_id=query.get("projection_id"),
                                         comparison_id=query.get("comparison_id"))
        fmt = query.get("format", "json")
        if fmt == "html":
            return render_html(report)
        if fmt != "json":
            raise AnalyticsError("invalid_format", "Expected json or html", 400)
        return envelope("report", report)

    def __call__(self, environ, start_response):
        try:
            data = self.dispatch(environ)
            status = 200
        except AnalyticsError as exc:
            data, status = exc.body(), exc.status
            data["error"]["request_id"] = uuid4().hex
        except Exception:
            request_id = uuid4().hex
            logging.getLogger(__name__).exception("Analytics request %s failed", request_id)
            status = 500
            data = AnalyticsError("internal_error", "Analytics calculation failed", status).body()
            data["error"]["request_id"] = request_id
        if isinstance(data, str):
            raw, mime = data.encode("utf-8"), "text/html; charset=utf-8"
        else:
            raw = json.dumps(data, ensure_ascii=False, allow_nan=False).encode("utf-8")
            mime = "application/json; charset=utf-8"
        start_response(f"{status} {HTTPStatus(status).phrase}",
                       [("Content-Type", mime), ("Content-Length", str(len(raw)))])
        return [raw]


def register_analytics(flask_app, service):
    """A calls this once when assembling app/server. Flask stays optional for the CLI."""
    from flask import Response, request

    wsgi = AnalyticsApp(service)
    def handle(**_):
        return Response.from_app(wsgi, request.environ)

    for path, method in ENDPOINTS.items():
        flask_app.add_url_rule(PREFIX + path, endpoint="analytics_" + path.split("/")[0],
                               view_func=handle, methods=[method])
