"""Measurements from a single version of the entry trace and aggregate statistics."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from statistics import median

from analytics_support import AnalyticsError, percentile_ns, ref_key, valid_span_id

ERRORS = {"error", "timeout", "fast_fail", "http_error", "grpc_error", "transport_error", "error_unknown"}
VERSION = "1.0.0"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def resolve(graphs, refs):
    """Resolve exact revisions from the caller's consistent repository snapshot."""
    indexed = {}
    for graph in graphs:
        key = ref_key(graph["trace_ref"])
        if key in indexed and indexed[key] != graph:
            raise AnalyticsError("conflicting_revision", "Two records for the same revision", 409)
        indexed[key] = graph
    resolved = []
    identities = {key[:3] for key in indexed}
    for ref in refs:
        key = ref_key(ref)
        if key not in indexed:
            stale = key[:3] in identities
            raise AnalyticsError("stale_revision" if stale else "trace_not_found",
                                 "Requested trace revision unavailable", 409 if stale else 404, {"ref": ref})
        resolved.append(indexed[key])
    return resolved


def label(span, name):
    return span.get(name, span.get("attributes", {}).get(name))


def ns(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?[0-9]+", value):
        return int(value)
    return None


def duration(span):
    value = ns(span.get("duration_ns"))
    if value is None:
        start, end = ns(span.get("start_ns")), ns(span.get("end_ns"))
        if start is not None and end is not None:
            value = end - start
    return value if value is not None and value >= 0 else None


def outcome(span):
    value = str(span.get("outcome", "")).lower()
    if value in ERRORS or str(span.get("otel_status", "")).lower() == "error" or span.get("error_type"):
        return "error"
    status = span.get("http_status")
    if type(status) is int and 100 <= status <= 599:
        return "error" if status >= 400 else "ok"
    if value == "ok" or str(span.get("otel_status", "")).lower() == "ok":
        return "ok"
    return "unknown"


def union_length(intervals):
    total = 0
    end = None
    for left, right in sorted(intervals):
        if end is None or left >= end:
            total += right - left
        elif right > end:
            total += right - end
        end = right if end is None else max(end, right)
    return total


def comparable_intervals(spans, clock_compatible=None):
    if not spans:
        return 0, 0
    offsets = [ns(s.get("start_offset_ns", s.get("offset_ns"))) for s in spans]
    use_offsets = all(start is not None for start in offsets)
    if len(spans) > 1:
        domains = {s.get("clock_domain") for s in spans}
        known = domains - {None, "", "unknown"}
        if clock_compatible is False or any(s.get("clock_compatible") is False for s in spans):
            return None
        if clock_compatible is not True and (len(known) > 1 or not use_offsets and domains != known):
            return None
    intervals = []
    for span, offset in zip(spans, offsets):
        start = offset if use_offsets else ns(span.get("start_ns"))
        length = duration(span)
        if start is None or length is None:
            return None
        intervals.append((start, start + length))
    return sum(end - start for start, end in intervals), union_length(intervals)


def service_name(service):
    return service.get("service_name") if isinstance(service, dict) else service


def operation(span):
    return span.get("operation") or span.get("name")


# B can flag loss even when the remaining spans still form a valid tree.
# A completely missing child cannot be detected from parent IDs alone.
INCOMPLETE_CALL_DATA = {
    "source_loss", "missing_parent", "multiple_roots", "cycle", "depth_limit",
    "conflicting_span", "span_count_mismatch", "missing_attempt_span", "conflicting_attempt", "forced_close",
}


def quality_codes(graph, spans):
    quality = graph.get("quality", {})
    summary = graph.get("summary", {})
    codes = set()
    for issues in (graph.get("quality_issues", []), quality.get("issues", []), summary.get("quality_issues", [])):
        codes.update(issue["code"] if isinstance(issue, dict) else issue for issue in issues)
    if quality.get("source_loss") or isinstance(graph.get("sampling"), dict) and graph["sampling"].get("known_losses"):
        codes.add("source_loss")
    if quality.get("orphan_count"):
        codes.add("missing_parent")
    if quality.get("conflicting_span_count"):
        codes.add("conflicting_span")
    if graph.get("assembly_state") == "forced" or quality.get("forced_reason"):
        codes.add("forced_close")
    if summary.get("span_count", len(spans)) != len(spans):
        codes.add("span_count_mismatch")
    attempts = {}
    for attempt in graph.get("attempts", []):
        key = attempt.get("attempt_id") if isinstance(attempt, dict) else None
        if not valid_span_id(key) or key not in spans or str(spans[key].get("kind", "")).lower() != "client":
            codes.add("missing_attempt_span")
            continue
        from_span, from_attempt = label(spans[key], "edge_id"), attempt.get("edge_id")
        if (key in attempts and attempts[key] != attempt or attempt.get("trace_ref", graph["trace_ref"]) != graph["trace_ref"]
                or (from_span is not None and from_attempt is not None and from_span != from_attempt)):
            codes.add("conflicting_attempt")
        attempts[key] = attempt
    return codes, attempts


def measure_trace(graph, entry_selector=None):
    ref = graph["trace_ref"]
    ref_key(ref)
    spans = {}
    if not isinstance(graph.get("spans"), list):
        raise AnalyticsError("invalid_trace", "Expected a list of spans")
    for span in graph["spans"]:
        span_id = span.get("span_id") if isinstance(span, dict) else None
        if (not valid_span_id(span_id)
                or span.get("parent_span_id") is not None and not valid_span_id(span["parent_span_id"])):
            raise AnalyticsError("invalid_trace", "Invalid span or parent identity")
        if span_id in spans and spans[span_id] != span:
            raise AnalyticsError("invalid_trace", "Conflicting span identity", details={"ref": ref})
        spans[span_id] = span
    roots = [s for s in spans.values() if s.get("parent_span_id") is None]
    if len(roots) != 1:
        raise AnalyticsError("ambiguous_entry", "Exactly one root is needed to identify the input request")
    root = roots[0]
    children = defaultdict(list)
    for span in spans.values():
        children[span.get("parent_span_id")].append(span)
    seen = set()
    stack = [root]
    while stack:
        span = stack.pop()
        seen.add(span["span_id"])
        stack.extend(children[span["span_id"]])
    if len(seen) != len(spans):
        raise AnalyticsError("incomplete_trace", "The entry contains disconnected or cyclic spans")
    summary = graph.get("summary", {})
    service = root.get("service") or summary.get("service")
    entry_operation = summary.get("operation") or operation(root)
    if not service or not entry_operation:
        raise AnalyticsError("ambiguous_entry", "Entry service and operation are required")
    if entry_selector:
        for key, actual in (("service", service), ("operation", entry_operation)):
            if key in entry_selector and entry_selector[key] != actual:
                raise AnalyticsError("cohorts_not_comparable", "Entry does not match the requested selector",
                                     details={"ref": ref, "field": key})

    # Traverse wrappers on the entry service. A client or nested server starts a new boundary.
    direct = []
    stack = list(children[root["span_id"]])
    while stack:
        span = stack.pop()
        if span.get("service") != service:
            continue
        kind = str(span.get("kind", "")).lower()
        if kind == "client":
            direct.append(span)
        elif kind == "internal":
            stack.extend(children[span["span_id"]])
    codes, attempts = quality_codes(graph, spans)
    groups = defaultdict(list)
    descriptors = defaultdict(list)
    for span in direct:
        servers = [s for s in children[span["span_id"]] if str(s.get("kind", "")).lower() == "server"]
        target = span.get("target")
        if target is None and len(servers) == 1:
            target = servers[0].get("service")
        edge = {"edge_id": label(span, "edge_id") or attempts.get(span["span_id"], {}).get("edge_id"), "operation": operation(span), "target": target}
        key = canonical({"edge_id": edge["edge_id"]} if edge["edge_id"] else edge)
        groups[key].append(span)
        descriptors[key].append(edge)
    internal = [outcome(span) for span in spans.values() if span["span_id"] != root["span_id"]]
    calls_complete = codes.isdisjoint(INCOMPLETE_CALL_DATA)
    internal_status = "error" if "error" in internal else ("unknown" if not calls_complete or "unknown" in internal else "ok")
    intervals = comparable_intervals(direct, graph.get("clock_compatible")) if calls_complete and "cross_clock_uncertain" not in codes else None
    return {"ref": ref, "root": root, "service": service, "operation": entry_operation,
            "outcome": outcome(root), "duration": duration(root), "internal_outcome": internal_status,
            "groups": groups, "descriptors": descriptors, "intervals": intervals, "issues": sorted(codes), "calls_complete": calls_complete,
            "clients": [s for s in spans.values() if str(s.get("kind", "")).lower() == "client"],
            "project_id": graph.get("project_id", service.get("project_id") if isinstance(service, dict) else None),
            "workload_id": graph.get("workload_id", summary.get("workload_id")),
            "version": root.get("service_version") or graph.get("service_version"),
            "sampling": graph.get("sampling", "unknown")}


def ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def edge_descriptors(rows):
    by_key = defaultdict(list)
    for row in rows:
        for key, labels in row["descriptors"].items():
            by_key[key].extend(labels)
    # Labels are presentation only. Prefer observed targets; make ties independent
    # of export order, including when only the opposite cohort observed a target.
    return {key: min(labels, key=lambda d: (d["target"] is None, canonical(d))) for key, labels in by_key.items()}


def summarize(rows, descriptors=None):
    descriptors = {**(descriptors or {}), **edge_descriptors(rows)}
    lengths = [r["duration"] for r in rows if r["duration"] is not None]
    outcomes = Counter(r["outcome"] for r in rows)
    internal = Counter(r["internal_outcome"] for r in rows)
    direct_calls = []
    complete_rows = [row for row in rows if row["calls_complete"]]
    for key in sorted(descriptors):
        counts = [len(r["groups"].get(key, ())) for r in complete_rows]
        times = [d for r in complete_rows for s in r["groups"].get(key, ()) if (d := duration(s)) is not None]
        direct_calls.append({**descriptors[key], "median_calls_per_trace": median(counts) if counts else None,
                             "pooled_median_duration_ns": percentile_ns(times, .5), "sample_count": sum(counts),
                             "duration_count": len(times)})
    intervals = [r["intervals"] for r in rows if r["intervals"] is not None]
    return {"count": len(rows), "root_latency_p50_ns": percentile_ns(lengths, .5),
            "root_latency_p95_ns": percentile_ns(lengths, .95),
            "root_errors": outcomes["error"], "root_error_ratio": ratio(outcomes["error"], len(rows)-outcomes["unknown"]),
            "internal_error_trace_count": internal["error"],
            "internal_error_trace_ratio": ratio(internal["error"], len(rows)-internal["unknown"]),
            "direct_calls": direct_calls, "overlap_ratio": ratio(sum(s for s, _ in intervals), sum(u for _, u in intervals)),
            "coverage": {"root_latency_count": len(lengths), "known_root_outcomes": len(rows)-outcomes["unknown"],
                         "known_internal_outcomes": len(rows)-internal["unknown"], "overlap_trace_count": len(intervals),
                         "direct_call_trace_count": len(complete_rows),
                         "issues": dict(sorted(Counter(i for r in rows for i in r["issues"]).items()))},
            "service_versions": sorted({r["version"] for r in rows if r["version"]}),
            "sampling": [json.loads(v) for v in sorted({canonical(r["sampling"]) for r in rows})]}
