"""Read API for the telemetry repository and the analytics derived from it."""
from __future__ import annotations

import re
import json
from contracts import TraceRef

from analytics_support import AnalyticsError, ref_key
from app.data_api.adapter import SpanStoreAdapter, make_graph_builder
from app.data_api.handlers import MAX_LIMIT, ApiError, get_trace, list_traces

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
        include_findings = query.get("include_findings", "true")
        if include_findings not in {"true", "false"}:
            raise AnalyticsError("invalid_include_findings", "include_findings must be true or false", 400)
        with self.service.source.read() as view:
            store = SpanStoreAdapter(view.span_store, view.current_metas)
            build = make_graph_builder(view.span_store, view.limits)
            rule_membership = None
            if trace_id:
                include_span_kind = query.get("include_span_kind", "true")
                if include_span_kind not in {"true", "false"}:
                    raise AnalyticsError("invalid_include_span_kind", "include_span_kind must be true or false", 400)
                response = get_trace(
                    store, build, environment, trace_id,
                    _integer(query, "generation"), _integer(query, "revision"),
                    include_span_kind=include_span_kind == "true",
                )
            else:
                def values(key):
                    try:
                        value = json.loads(query.get(key, '[]'))
                    except ValueError:
                        raise AnalyticsError('invalid_filter', 'Invalid filter', 400)
                    if not isinstance(value, list) or any(not isinstance(v, str) for v in value) or len(value) > 100:
                        raise AnalyticsError('invalid_filter', 'Invalid filter', 400)
                    return tuple(value)
                selected_rules = values('rules')
                allowed_refs = None
                if selected_rules:
                    result = self.service.audit(environment)
                    rule_membership = {ref_key(row['trace_ref']): row['occurrence_ids'] for row in result['trace_findings']}
                    occurrence_ids = {f['occurrence_id'] for f in result['findings'] if f['rule_id'] in selected_rules}
                    allowed_refs = {TraceRef.from_dict(row['trace_ref']) for row in result['trace_findings']
                                    if occurrence_ids.intersection(row['occurrence_ids'])}
                include_success_rates = query.get("include_success_rates", "false")
                if include_success_rates not in {"true", "false"}:
                    raise AnalyticsError(
                        "invalid_include_success_rates", "include_success_rates must be true or false", 400
                    )
                response = list_traces(
                    store, environment, _integer(query, "limit", MAX_LIMIT),
                    _integer(query, "offset", 0),
                    read_summary=lambda ref: view.trace_summary(ref, summary_only=include_findings == "false"),
                    read_entries=view.trace_list_entries,
                    services_filter=values("services"), methods_filter=values("methods"),
                    statuses=values("statuses"), allowed_refs=allowed_refs,
                    query=query.get("query", ""), service=query.get("service", ""),
                    status=query.get("status", ""), method=query.get("method", ""), sort=query.get("sort"),
                    span_id=query.get("span_id"),
                    start_from_ns=_integer(query, "start_from_ns"),
                    start_to_ns=_integer(query, "start_to_ns"),
                    include_success_rates=include_success_rates == "true",
                )
            if isinstance(response, ApiError):
                raise AnalyticsError(response.code, response.message, response.status)

            payload = response["payload"]
            summaries = [payload["summary"]] if trace_id else payload["items"]
            if rule_membership is not None:
                for summary in summaries:
                    summary["finding_count"] = len(rule_membership.get(ref_key(summary["trace_ref"]), ()))
            elif summaries and include_findings == "true":
                findings = self.service.findings_by_trace(view, environment)
                for summary in summaries:
                    ids = findings.get(ref_key(summary["trace_ref"]), ())
                    summary["finding_count"] = len(ids)
                    if trace_id:
                        payload["finding_occurrence_ids"] = list(ids)
            return response
