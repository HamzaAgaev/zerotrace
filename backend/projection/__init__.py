"""P9 what-if projection over a pinned TraceDetail or P2 TraceGraph."""
import json
import re
from collections import defaultdict
from fractions import Fraction
from math import isfinite

from analytics_support import AnalyticsError, digest, ref_key, valid_span_id

from .intervals import Intervals

VERSION = "interval-blocks-v1.1.0"
TOLERANCE_NS = 500_000
MAX_DEPTH = 256
MAX_CHANGES = 32


def _error(code, message, status=422):
    return AnalyticsError(code, message, status=status)


def _reference(graph, request):
    ref = graph.get("trace_ref")
    stored = ref_key(ref)
    selected = ref_key(request.get("trace_ref"))
    if stored[:2] != selected[:2]:
        raise _error("trace_not_found", "Requested trace is unavailable.", 404)
    if stored[2:] != selected[2:]:
        raise _error("stale_trace_ref", "The requested generation or revision is unavailable.", 409)
    return dict(ref)


def _changes(value):
    if not isinstance(value, list) or len(value) > MAX_CHANGES:
        raise _error("invalid_changes", "Expected up to 32 span reductions.")
    changes = {}
    for change in value:
        if not isinstance(change, dict):
            raise _error("invalid_changes", "A reduction needs a span and percentage.")
        node, percent = change.get("span_id"), change.get("speedup_percent")
        if not valid_span_id(node) or node in changes:
            raise _error("invalid_changes", "Each selected span must have a distinct ID.")
        if type(percent) not in (int, float) or not 0 <= percent <= 100 or not isfinite(percent):
            raise _error("invalid_changes", "Reduction must be a finite number from 0 to 100 inclusive.")
        changes[node] = percent
    return changes


def _integer(value):
    if type(value) is int:
        return value
    if isinstance(value, str) and re.fullmatch(r"-?\d{1,20}", value):
        return int(value)
    raise ValueError("Missing or invalid integer nanoseconds")


def _bounds(span):
    start = _integer(span.get("start_offset_ns", span.get("offset_ns", span.get("start_ns"))))
    if "duration_ns" in span:
        duration = _integer(span["duration_ns"])
    else:
        # end_ns is an absolute timestamp. Offsets are relative to the displayed root.
        duration = _integer(span.get("end_ns")) - _integer(span.get("start_ns"))
    if duration < 0:
        raise ValueError("Negative span duration")
    if "start_ns" in span and "end_ns" in span:
        if _integer(span["end_ns"]) - _integer(span["start_ns"]) != duration:
            raise ValueError("Inconsistent timestamps and duration")
    return start, start + duration


def _quality(graph, scope, root, explicit_root, span_count, locations):
    quality = graph.get("quality") or {}
    summary = graph.get("summary") or {}
    issues = [*quality.get("issues", []), *graph.get("quality_issues", []), *summary.get("quality_issues", [])]
    # Counters without complete span locations cannot be scoped away by choosing a subtree.
    for field, code in (("orphan_count", "missing_parent"), ("conflicting_span_count", "conflicting_span")):
        localized = {span_id for issue in issues if isinstance(issue, dict) and issue.get("code") == code
                     for span_id in issue.get("span_ids", [])}
        if (quality.get(field) or 0) > len(localized | locations[code]):
            issues.append(code)
    if summary.get("span_count", span_count) != span_count:
        issues.append("span_count_mismatch")
    if graph.get("assembly_state") == "forced" or quality.get("forced_reason"):
        issues.append("forced_close")
    sampling = graph.get("sampling")
    if quality.get("source_loss") or isinstance(sampling, dict) and sampling.get("known_losses"):
        issues.append("source_loss")
    blockers, warnings = set(), set()
    for issue in issues:
        ids = issue.get("span_ids", []) if isinstance(issue, dict) else []
        code = issue.get("code", "unknown_quality_issue") if isinstance(issue, dict) else str(issue)
        if ids and not scope.intersection(ids):
            continue
        if explicit_root and (code == "multiple_roots" or (code == "missing_parent" and ids == [root])):
            warnings.add("partial_trace_scope")
        elif code in {"missing_parent", "multiple_roots", "cycle", "depth_limit",
                      "conflicting_span", "source_loss", "invalid_bounds",
                      "disconnected", "orphan", "missing_span", "span_count_mismatch", "forced_close"}:
            blockers.add(code)
        else:
            # Unknown fields (e.g. HTTP status) do not by themselves invalidate intervals.
            warnings.add(code)
    return blockers, warnings


def _observations(spans, order, bounds):
    groups = defaultdict(list)
    for node in order:
        s = spans[node]
        if str(s.get("kind")).lower() == "client":
            operation = s.get("operation") or s.get("name")
            if operation:
                target = json.dumps([s.get("service"), s.get("target"), operation], sort_keys=True)
                groups[(s.get("parent_span_id"), target)].append(node)
    results = []
    for _, nodes in sorted(groups.items(), key=lambda item: str(item[0])):
        if len(nodes) < 2:
            continue
        nodes.sort(key=lambda n: (*bounds[n], n))
        results.append({"kind": "repeated_calls", "message": "Repeated client operation under one parent.",
                        "span_ids": nodes, "basis": "observed", "confidence": "high"})
        for a, b in zip(nodes, nodes[1:]):
            if bounds[a][1] <= bounds[b][0] and spans[a].get("outcome") in {
                "error", "http_error", "timeout", "transport_error", "grpc_error", "fast_fail"
            } and spans[b].get("outcome") == "ok":
                results.append({"kind": "possible_retry", "message": "Sequential client error then success.",
                                "span_ids": [a, b], "basis": "inferred", "confidence": "low"})
    return results


def analyze(graph, request, policy=None):
    """Return a P9 JSON object. policy may select the registered algorithm_version."""
    if not isinstance(graph, dict) or not isinstance(request, dict) or (policy is not None and not isinstance(policy, dict)):
        raise _error("invalid_projection", "Expected a graph, projection request and optional policy object.")
    version = request.get("algorithm_version", (policy or {}).get("algorithm_version", VERSION))
    if version != VERSION:
        raise _error("unsupported_projection_version", "Supported projection algorithm: " + VERSION)
    ref = _reference(graph, request)
    changes = _changes(request.get("changes", []))
    selected = [{"span_id": n, "speedup_percent": p} for n, p in sorted(changes.items())]
    raw = graph.get("spans", [])
    if not isinstance(raw, list) or any(not isinstance(s, dict) for s in raw):
        raise _error("invalid_graph", "Expected a list of spans.")
    if any(not valid_span_id(s.get("span_id")) for s in raw):
        raise _error("invalid_graph", "Every span needs an ID.")
    if any(s.get("parent_span_id") is not None and not valid_span_id(s["parent_span_id"]) for s in raw):
        raise _error("invalid_graph", "Parent span ID must be valid or null.")
    spans = {}
    conflicting_parents = defaultdict(set)
    for s in raw:
        n = s["span_id"]
        if n in spans and s != spans[n]:
            conflicting_parents[n].update((spans[n].get("parent_span_id"), s.get("parent_span_id")))
        else:
            spans[n] = s
    root = request.get("root_span_id")
    explicit = root is not None
    if explicit and not valid_span_id(root):
        raise _error("invalid_projection", "Malformed scope root span ID.")
    if explicit and root not in spans:
        raise _error("unknown_span", "Selected scope root is unavailable.")
    if changes.keys() - spans.keys():
        raise _error("unknown_span", "A selected span is unavailable.")
    canonical = {"trace_ref": ref, "spans": sorted(spans.values(), key=lambda s: s["span_id"]), "conflicts": sorted(conflicting_parents),
                 "summary": {k: v for k, v in (graph.get("summary") or {}).items() if k in {"quality_issues", "span_count"}},
                 "assembly_state": graph.get("assembly_state"), "sampling": graph.get("sampling"),
                 "quality": graph.get("quality"), "quality_issues": graph.get("quality_issues", []),
                 "clock_compatible": graph.get("clock_compatible"), "root_span_id": root, "changes": selected,
                 "algorithm_version": VERSION}
    result = {"trace_ref": ref, "algorithm_version": VERSION,
              "input_hash": digest(canonical),
              "status": "available", "applicability": {"reason_codes": [], "scope_root": root, "excluded_span_ids": []},
              "requested_selection": selected, "effective_selection": [], "selection_notices": [],
              "baseline_ns": None, "projected_ns": None, "gain_ns": None, "gain_ratio": None,
              "spans": [], "candidates": [], "limiting_branches": [], "observations": [],
              "assumptions": ["Parent waits for its synchronous children; start offsets within overlap blocks and uncovered gaps are preserved."],
              "warnings": []}

    def unavailable(reasons, status="insufficient_data"):
        result["status"] = status
        result["applicability"]["reason_codes"] = sorted(reasons)
        return result

    if conflicting_parents and not explicit:
        return unavailable(["conflicting_span"])
    if not spans:
        return unavailable(["empty_trace"])
    children = {n: [] for n in spans}
    roots, missing = [], set()
    for n, s in spans.items():
        p = s.get("parent_span_id")
        if p is None:
            roots.append(n)
        elif p in spans:
            children[p].append(n)
        else:
            missing.add(n)
    if not explicit:
        if len(roots) != 1:
            reasons = {"multiple_roots" if len(roots) > 1 else "missing_root"}
            if missing:
                reasons.add("missing_parent")
            elif not roots:
                reasons.add("cycle")
            return unavailable(reasons)
        root = roots[0]
    result["applicability"]["scope_root"] = root
    if root in changes:
        raise _error("invalid_changes", "Choose an internal branch below the scope root.")
    seen, order, depth = set(), [], {}
    stack = [(root, 0)]
    while stack:
        node, level = stack.pop()
        if node in seen or level > MAX_DEPTH:
            return unavailable(["cycle" if node in seen else "depth_limit"])
        seen.add(node)
        order.append(node)
        depth[node] = level
        stack.extend((c, level + 1) for c in sorted(children[node], reverse=True))
    excluded = sorted(spans.keys() - seen)
    result["applicability"]["excluded_span_ids"] = excluded
    if any(node in seen or parents & seen for node, parents in conflicting_parents.items()):
        return unavailable(["conflicting_span"])
    if not explicit and excluded:
        # Components without a parentless root originate in an orphan or a cycle.
        return unavailable(["disconnected", "missing_parent" if missing else "cycle"])
    if changes.keys() - seen:
        raise _error("span_outside_scope", "A selected branch lies outside the scope root.")
    blockers, warnings = _quality(graph, seen, root, explicit, len(spans),
                                  {"missing_parent": missing, "conflicting_span": set(conflicting_parents)})
    if explicit and (excluded or spans[root].get("parent_span_id")):
        warnings.add("partial_trace_scope")
    bounds, observed = {}, {}
    timestamps_assumed_aligned = False
    for n in order:
        s = spans[n]
        if s.get("links_present") or s.get("links") or s.get("async_markers") or str(s.get("kind")).lower() in {"producer", "consumer"}:
            blockers.add("async_or_links")
        try:
            start, end = observed[n] = _bounds(s)
        except ValueError:
            blockers.add("invalid_bounds")
            continue
        bounds[n] = (start, end)
        p = s.get("parent_span_id")
        if n == root or p not in bounds:
            continue
        parent = spans[p]
        child_relative = "start_offset_ns" in s or "offset_ns" in s
        parent_relative = "start_offset_ns" in parent or "offset_ns" in parent
        if child_relative != parent_relative:
            blockers.add("mixed_time_axes")
        # OTLP timestamps are the user's alignment assertion.  Clock metadata
        # is often absent (and may differ between exporters), so it must not
        # reject an otherwise valid synchronous tree.
        if (not child_relative or s.get("clock_domain") != parent.get("clock_domain")
                or s.get("clock_compatible") is False or graph.get("clock_compatible") is False):
            timestamps_assumed_aligned = True
        lo, hi = bounds[p]
        if start < lo or end > hi:
            if max(lo - start, end - hi) > TOLERANCE_NS:
                blockers.add("temporal_bounds")
            else:
                bounds[n] = (min(hi, max(lo, start)), min(hi, max(lo, end)))
                warnings.add("clipped_temporal_bounds")
    if timestamps_assumed_aligned:
        warnings.add("timestamps_assumed_aligned")
        result["assumptions"].append(
            "Timestamps are treated as aligned as supplied by the user; clock metadata is advisory."
        )
    result["warnings"] = sorted(warnings)
    if root in observed:
        result["baseline_ns"] = str(observed[root][1] - observed[root][0])
    if blockers:
        return unavailable(blockers, "unsupported" if blockers == {"async_or_links"} else "insufficient_data")

    effective = {}
    for node in order:
        if node not in changes:
            continue
        p = spans[node].get("parent_span_id")
        while p in seen and p not in effective and p != root:
            p = spans[p].get("parent_span_id")
        if p in effective:
            result["selection_notices"].append({"code": "covered_by_ancestor", "span_id": node, "ancestor_span_id": p})
        else:
            effective[node] = Fraction(str(changes[node]))
    result["effective_selection"] = [c for c in selected if c["span_id"] in effective]
    model = Intervals(bounds, children, order)
    projected, limiting = model.joint(effective)
    base = model.duration(root)
    gain = base - projected[root]
    result.update(projected_ns=str(projected[root]), gain_ns=str(gain), gain_ratio=gain / base if base else None,
                  limiting_branches=sorted(limiting), observations=_observations(spans, order, observed))
    if effective and gain == 0:
        result["warnings"].append("no_gain_in_selected_scope")
    for n in order:
        result["spans"].append({"span_id": n, "depth": depth[n], "offset_ns": str(observed[n][0] - observed[root][0]),
                                "observed_duration_ns": str(observed[n][1] - observed[n][0]),
                                "uncovered_direct_children_ns": str(model.uncovered[n]),
                                "projected_duration_ns": str(projected[n]), "limiting": n in limiting})
        if n != root:
            candidate = {"span_id": n, "target": spans[n].get("name", n)}
            candidate.update({f"gain{p}_ns": str(model.single_gain(n, Fraction(p))) for p in (25, 50, 100)})
            result["candidates"].append(candidate)
    result["candidates"].sort(key=lambda c: (-int(c["gain100_ns"]), c["span_id"]))
    return result
