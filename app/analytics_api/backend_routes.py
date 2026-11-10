"""Read API for the telemetry repository and the analytics derived from it."""
from __future__ import annotations

import re

from analytics_support import AnalyticsError, ref_key
from backend.app.data_api.adapter import SpanStoreAdapter, make_graph_builder
from backend.app.data_api.handlers import MAX_LIMIT, ApiError, get_trace, list_traces

from .routes import PREFIX, AnalyticsApp, _query


def _integer(query, key, default=None):
    if key not in query:
        return default
    value = query[key]
    if not re.fullmatch(r"[0-9]{1,19}", value) or int(value) > (1 << 63) - 1:
        raise AnalyticsError("invalid_" + key, f"{key} must be a nonnegative integer", 400)
    return int(value)


class BackendApp(AnalyticsApp):
    """Mount B's P7 data handlers beside C's routes on one RepositorySource."""

    def dispatch(self, environ):
        path = environ.get("PATH_INFO", "")
        match = re.fullmatch(PREFIX + r"traces(?:/([^/]+))?", path)
        if not match:
            return super().dispatch(environ)
        if environ["REQUEST_METHOD"] != "GET":
            raise AnalyticsError("method_not_allowed", "Unsupported HTTP method", 405)
        query = _query(environ)
        environment = query.get("environment_id")
        if not environment:
            raise AnalyticsError("missing_environment", "Select an environment", 400)

        trace_id = match[1]
        with self.service.source.read() as view:
            store = SpanStoreAdapter(view.span_store)
            build = make_graph_builder(view.span_store, view.limits)
            if trace_id:
                response = get_trace(store, build, environment, trace_id,
                                     _integer(query, "generation"), _integer(query, "revision"))
            else:
                response = list_traces(store, environment, _integer(query, "limit", MAX_LIMIT),
                                       _integer(query, "offset", 0), graph_builder=build)
            if isinstance(response, ApiError):
                raise AnalyticsError(response.code, response.message, response.status)

            payload = response["payload"]
            summaries = [payload["summary"]] if trace_id else payload["items"]
            if summaries:
                findings = self.service.findings_by_trace(view, environment)
                for summary in summaries:
                    ids = findings.get(ref_key(summary["trace_ref"]), ())
                    summary["finding_count"] = len(ids)
                    if trace_id:
                        payload["finding_occurrence_ids"] = list(ids)
            return response
