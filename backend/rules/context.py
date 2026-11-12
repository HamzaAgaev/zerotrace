"""Indexes over B's immutable P2 call model. All IDs are scoped to a trace revision."""
from collections import Counter, defaultdict
from dataclasses import dataclass

from analytics_support import AnalyticsError
from contracts.p3 import ratio
from core.model import EdgeRetrySamples

from .common import digest, integer, refkey


@dataclass
class Group:
    data: dict
    key: tuple
    attempts: list
    reason: str | None


def quality_issue(row):
    return bool(row.get("conflicting_span") or row.get("quality_issues") or row.get("invalid"))


def attempt_error(attempt):
    outcome = attempt.get("outcome")
    if outcome in {"error", "timeout", "fast_fail", "http_error", "grpc_error", "transport_error", "error_unknown"}:
        return True
    if outcome == "ok":
        return False
    status = integer(attempt.get("client_http_status"))
    return None if status is None else status >= 400


class Context:
    def __init__(self, model):
        self.environment = model.get("environment_id")
        if not isinstance(self.environment, str) or not self.environment:
            raise ValueError("environment_id required")
        self.revisions = {}
        self._ref_keys = {}
        self.traces = {}
        self.spans = {}
        attempts = list(model.get("attempts", ()))
        calls = list(model.get("calls", ()))
        for trace in model.get("traces", ()):
            key = self.ref(trace["trace_ref"])
            if key in self.traces and trace != self.traces[key]:
                raise ValueError("conflicting trace revision")
            self.traces[key] = trace
            for span in trace.get("spans", ()):
                slot = (key, span["span_id"])
                if slot in self.spans and self.spans[slot] != span:
                    raise ValueError("conflicting span")
                self.spans[slot] = span
            for field, dest in (("attempts", attempts), ("calls", calls)):
                for item in trace.get(field, ()):
                    if "trace_ref" in item and self.ref(item["trace_ref"]) != key:
                        raise AnalyticsError("trace_ref_mismatch", "Nested evidence belongs to a different trace")
                    dest.append({**item, "trace_ref": trace["trace_ref"]})
        self.attempts = {}
        self.by_edge = defaultdict(list)
        self.groups = defaultdict(list)
        self.by_id = {}
        self.edge_refs = defaultdict(set)
        self.edge_labels = {}
        edges = model.get("edges") or {}
        if isinstance(edges, dict):
            self.edge_labels.update(edges)
        elif isinstance(edges, list):
            for row in edges:
                if isinstance(row, dict) and row.get("edge_id"):
                    self.edge_labels[row["edge_id"]] = row.get("edge", row)
        else:
            raise ValueError("edges must be an edge map or a list")
        for row in attempts:
            key, aid = self.ref(row["trace_ref"]), row["attempt_id"]
            if not isinstance(aid, str) or not aid or not isinstance(row.get("edge_id"), str):
                raise ValueError("attempt ID and edge required")
            slot = (key, aid)
            if slot in self.attempts:
                if self.attempts[slot] != row:
                    raise ValueError("conflicting attempt")
                continue
            if isinstance(row.get("edge"), dict):
                self.edge_labels.setdefault(row["edge_id"], row["edge"])
            self.attempts[slot] = row
            self.by_edge[row["edge_id"]].append((key, row))
            self.edge_refs[row["edge_id"]].add(key)
        rows = {}
        memberships = Counter()
        for call in calls:
            key = self.ref(call["trace_ref"])
            slot = (key, call["call_id"])
            if slot in rows:
                if rows[slot] != call:
                    raise ValueError("conflicting logical call")
                continue
            if isinstance(call.get("edge"), dict):
                self.edge_labels.setdefault(call["edge_id"], call["edge"])
            rows[slot] = call
            memberships.update((key, aid) for aid in call.get("attempt_ids", ()))
        for (key, _), call in sorted(rows.items()):
            ids = call.get("attempt_ids", ())
            parts = [self.attempts.get((key, aid)) for aid in ids]
            reason = self.group_issue(call, parts, memberships, key)
            if self.graph_issue(key, ids, {"conflicting_span", "cycle", "missing_parent", "source_loss"}):
                reason = "unreliable_trace_evidence"
            group = Group(call, key, parts, reason)
            self.groups[call["edge_id"]].append(group)
            self.edge_refs[call["edge_id"]].add(key)
            if reason is None:
                for aid in ids:
                    self.by_id[(key, aid)] = group
        for rows in self.by_edge.values():
            rows.sort(key=lambda item: (item[0], item[1]["attempt_id"]))

    def graph_issue(self, key, ids, codes):
        trace = self.traces.get(key, {})
        quality = trace.get("quality") or {}
        if "source_loss" in codes and quality.get("source_loss"):
            return True
        relevant = {sid for sid in ids if sid}
        for issue in [*trace.get("quality_issues", []), *quality.get("issues", [])]:
            if isinstance(issue, str):
                # Unscoped machine codes still identify the affected property.
                # Legacy free text supplies no reliable signal for this purpose.
                if issue in codes:
                    return True
                if issue in {"cross_clock_uncertain", "missing_parent", "conflicting_span", "source_loss",
                             "multiple_roots", "depth_limit", "unknown_service", "missing_field", "cycle"}:
                    continue
                return True
            if issue.get("code") in codes:
                affected_ids = issue.get("span_ids")
                if not affected_ids or relevant.intersection(affected_ids):
                    return True
        return False

    def ref(self, ref):
        cached = self._ref_keys.get(id(ref))
        if cached is not None:
            return cached[1]
        key = refkey(ref)
        if key[0] != self.environment:
            raise ValueError("trace belongs to another environment")
        if self.revisions.setdefault(key[:3], key[3]) != key[3]:
            raise AnalyticsError("conflicting_trace_revisions",
                                 "An audit snapshot contains multiple revisions of the same trace", 409)
        self._ref_keys[id(ref)] = (ref, key)
        return key

    def linked(self, key, ancestor_id, descendant_id, ancestor_kind, descendant_kind):
        """Check P2 relationship against actual spans when the graph is supplied."""
        if not ancestor_id or not descendant_id or ancestor_id == descendant_id:
            return False
        if not self.traces.get(key, {}).get("spans"):
            return True  # The normalized attempt supplies the verified relationship.
        ancestor = self.spans.get((key, ancestor_id))
        child = self.spans.get((key, descendant_id))
        if not ancestor or not child or str(ancestor.get("kind")).lower() != ancestor_kind:
            return False
        if str(child.get("kind")).lower() != descendant_kind:
            return False
        parent_id = child.get("parent_span_id")
        seen = {descendant_id}
        while parent_id != ancestor_id:
            parent = self.spans.get((key, parent_id))
            if (not parent or parent_id in seen or str(parent.get("kind")).lower() != "internal"
                    or len(seen) > 256):
                return False
            seen.add(parent_id)
            parent_id = parent.get("parent_span_id")
        return True

    @staticmethod
    def group_issue(call, parts, memberships, key):
        hyp = call.get("hypothesis")
        if hyp not in {"single", "retry"}:
            return "ambiguous_or_nonserial"
        if call.get("confidence") not in {"high", "confirmed"} or quality_issue(call):
            return "unconfirmed_group"
        if not parts or any(a is None for a in parts):
            return "missing_attempts"
        if any(a["edge_id"] != call["edge_id"] or quality_issue(a) or a.get("call_id", call["call_id"]) != call["call_id"]
               or memberships[(key, a["attempt_id"])] != 1 for a in parts):
            return "inconsistent_membership"
        if (hyp == "single") != (len(parts) == 1):
            return "inconsistent_group_size"
        indices = [a.get("attempt_index") for a in parts]
        if any(i is not None for i in indices) and indices != list(range(1, len(parts) + 1)):
            return "incomplete_or_unordered_attempts"
        return None

    def labels(self, edge, policy):
        fields = policy.get("edge") or self.edge_labels.get(edge) or {}
        if not isinstance(fields, dict):
            fields = {}
        def display(key):
            value = fields.get(key)
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                return value.get("service_name") or value.get("route_or_name") or "unknown"
            return "unknown"
        return {k: display(k) for k in ("caller", "callee", "operation")}

    def matching_policy(self, model, edge, policy):
        revision = policy.get("config_revision")
        if revision is None:
            return True
        refs = self.edge_refs.get(edge, ())
        declarations = {str(v) for v in [model.get("config_revision")] if v is not None}
        for key in refs:
            declared = self.traces.get(key, {}).get("config_revision")
            if declared is not None:
                declarations.add(str(declared))
        for group in self.groups.get(edge, ()):
            if group.data.get("config_revision") is not None:
                declarations.add(str(group.data["config_revision"]))
        declarations.update(str(a["config_revision"]) for _, a in self.by_edge.get(edge, ())
                            if a.get("config_revision") is not None)
        return not declarations or declarations == {str(revision)}

    def observations(self, edge, result, samples: EdgeRetrySamples):
        calls = self.groups[edge]
        missing = sum((key, a["attempt_id"]) not in self.by_id for key, a in self.by_edge[edge])
        for group in calls:
            if group.reason:
                result.reject(group.reason)
                continue
            retry = group.data["hypothesis"] == "retry"
            result.consider([group.key], retry)
            samples.add_call(group.key, len(group.attempts), retry=retry)
        if not calls:
            result.reject("missing_call_model")
        summary = dict(
            edge_id=edge, eligible_calls=samples.eligible_calls, retry_calls=samples.retry_calls,
            observed_attempts=samples.grouped_attempts, attempts_without_confirmed_group=missing,
            excluded_by_reason=dict(result.excluded), eligible_traces=len(samples.trace_refs),
            retry_ratio=ratio(samples.retry_calls, samples.eligible_calls),
        )
        return summary

    def normalized_identity(self):
        # Hash normalized public model fields only; hidden test labels are not model inputs.
        def public(row, keys):
            return {k: row[k] for k in keys if k in row}
        a_fields = ("attempt_id", "edge_id", "duration_ns", "server_span_id", "server_duration_ns",
                    "upstream_attempt_id", "caller_server_span_id", "offset_in_caller_ns", "outcome",
                    "client_http_status", "client_grpc_status", "first_path", "attempt_index", "method",
                    "idempotency", "quality_issues", "gap_before_ns", "clock_compatible", "protocol",
                    "conflicting_span", "invalid", "call_id", "caller_version", "callee_version", "config_revision",
                    "workload_id", "timeout_scope", "clock_domain")
        c_fields = ("call_id", "edge_id", "hypothesis", "confidence", "attempt_ids", "gaps_ns",
                    "evidence_codes", "quality_issues", "method", "workload_id", "caller_version",
                    "callee_version", "config_revision", "timeout_scope", "invalid", "conflicting_span")
        return digest({
            "trace_refs": [(key, public(row, ("config_revision", "caller_version", "callee_version", "workload_id",
                                                "timeout_scope", "quality_issues", "quality")))
                           for key, row in sorted(self.traces.items())],
            "edge_labels": self.edge_labels,
            "attempts": [(key, public(row, a_fields)) for key, row in sorted(self.attempts.items())],
            "calls": [(g.key, public(g.data, c_fields)) for edge in sorted(self.groups) for g in self.groups[edge]],
            "spans": [(key, public(span, ("kind", "parent_span_id", "method", "operation", "idempotency", "clock_domain"))) for key, span in sorted(self.spans.items())],
        })
