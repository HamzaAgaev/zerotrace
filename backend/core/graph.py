from __future__ import annotations

from contracts import (
    IngestLimits,
    NormalizedSpan,
    Quality,
    QualityIssue,
    TraceGraph,
    TraceRef,
)
from contracts.p2 import AssemblyState


def build_graph(
    spans: list[NormalizedSpan],
    trace_ref: TraceRef,
    first_received_at_ns: int,
    last_received_at_ns: int,
    limits: IngestLimits,
    *,
    assembly_state: AssemblyState = "open",
    forced_reason: str | None = None,
) -> TraceGraph:
    issues: list[QualityIssue] = []

    if assembly_state == "forced":
        issues.append(
            QualityIssue(
                code="forced_close",
                scope="trace",
                span_ids=[],
                details=f"trace force-closed: {forced_reason or 'reason unavailable'}",
            )
        )

    deduped, conflicting_span_count = _dedupe_spans(spans)
    if conflicting_span_count:
        issues.append(
            QualityIssue(
                code="conflicting_span",
                scope="trace",
                span_ids=[],
                details=(
                    f"{conflicting_span_count} duplicate span_id(s) with conflicting"
                    " content; first accepted"
                ),
            )
        )

    by_id = {s.span_id: s for s in deduped}

    parent_index: dict[str, str | None] = {s.span_id: s.parent_span_id for s in deduped}
    children_index: dict[str, list[str]] = {s.span_id: [] for s in deduped}
    for s in deduped:
        if s.parent_span_id is not None and s.parent_span_id in by_id:
            children_index[s.parent_span_id].append(s.span_id)
    for children in children_index.values():
        children.sort(key=lambda sid: (by_id[sid].start_ns, by_id[sid].end_ns, sid))

    roots = sorted(
        (s.span_id for s in deduped if s.parent_span_id is None),
        key=lambda sid: (by_id[sid].start_ns, by_id[sid].end_ns, sid),
    )

    missing_parent_ids = sorted(
        s.span_id for s in deduped if s.parent_span_id is not None and s.parent_span_id not in by_id
    )
    if missing_parent_ids:
        issues.append(
            QualityIssue(
                code="missing_parent",
                scope="span",
                span_ids=missing_parent_ids,
                details=(
                    f"{len(missing_parent_ids)} span(s) reference a parent absent from the trace"
                ),
            )
        )
    if len(roots) > 1:
        issues.append(
            QualityIssue(
                code="multiple_roots",
                scope="trace",
                span_ids=roots,
                details=f"{len(roots)} root spans in one trace",
            )
        )

    cycle_ids = _find_cycles(parent_index, by_id)
    if cycle_ids:
        issues.append(
            QualityIssue(
                code="cycle",
                scope="trace",
                span_ids=cycle_ids,
                details=f"parent cycle among {len(cycle_ids)} span(s)",
            )
        )

    depth_limited_ids, reachable_count = _walk_depths(
        roots, children_index, limits.max_depth, by_id
    )
    if depth_limited_ids:
        issues.append(
            QualityIssue(
                code="depth_limit",
                scope="span",
                span_ids=depth_limited_ids,
                details=(
                    f"{len(depth_limited_ids)} span(s) deeper than max_depth {limits.max_depth}"
                ),
            )
        )
    orphan_count = len(deduped) - reachable_count

    unknown_service_count = sum(1 for s in deduped if not s.service)

    cross_clock_ids = _find_cross_clock(children_index, by_id)
    if cross_clock_ids:
        issues.append(
            QualityIssue(
                code="cross_clock_uncertain",
                scope="trace",
                span_ids=cross_clock_ids,
                details=(
                    "parent/child spans from different clock domains with overlapping intervals"
                ),
            )
        )

    return TraceGraph(
        trace_ref=trace_ref,
        first_received_at_ns=first_received_at_ns,
        last_received_at_ns=last_received_at_ns,
        assembly_state=assembly_state,
        spans=deduped,
        roots=roots,
        parent_index=parent_index,
        children_index=children_index,
        quality=Quality(
            issues=issues,
            orphan_count=orphan_count,
            conflicting_span_count=conflicting_span_count,
            unknown_service_count=unknown_service_count,
        ),
    )


def _dedupe_spans(spans: list[NormalizedSpan]) -> tuple[list[NormalizedSpan], int]:
    seen: dict[str, NormalizedSpan] = {}
    order: list[str] = []
    conflicting = 0
    for span in spans:
        existing = seen.get(span.span_id)
        if existing is None:
            seen[span.span_id] = span
            order.append(span.span_id)
        elif existing != span:
            conflicting += 1
    return [seen[sid] for sid in order], conflicting


def _find_cycles(
    parent_index: dict[str, str | None], by_id: dict[str, NormalizedSpan]
) -> list[str]:
    color: dict[str, int] = {}
    in_cycle: set[str] = set()
    for start in parent_index:
        if color.get(start) == 2:
            continue
        path: list[str] = []
        node: str | None = start
        while node is not None and node in by_id and color.get(node, 0) == 0:
            color[node] = 1
            path.append(node)
            node = parent_index[node]
        if node is not None and color.get(node) == 1:
            idx = path.index(node)
            in_cycle.update(path[idx:])
        for sid in path:
            color[sid] = 2
    return sorted(in_cycle)


def _walk_depths(
    roots: list[str],
    children_index: dict[str, list[str]],
    max_depth: int,
    by_id: dict[str, NormalizedSpan],
) -> tuple[list[str], int]:
    limited: list[str] = []
    reachable = 0
    stack: list[tuple[str, int]] = [(sid, 1) for sid in reversed(roots)]
    visited: set[str] = set()
    while stack:
        sid, depth = stack.pop()
        if sid in visited:
            continue
        visited.add(sid)
        reachable += 1
        if depth > max_depth:
            limited.append(sid)
        for child in reversed(children_index[sid]):
            stack.append((child, depth + 1))
    limited.sort(key=lambda sid: (by_id[sid].start_ns, by_id[sid].end_ns, sid))
    return limited, reachable


def _find_cross_clock(
    children_index: dict[str, list[str]], by_id: dict[str, NormalizedSpan]
) -> list[str]:
    flagged: set[str] = set()
    for parent_id, children in children_index.items():
        parent = by_id[parent_id]
        if parent.clock_domain is None:
            continue
        for child_id in children:
            child = by_id[child_id]
            if child.clock_domain is None or child.clock_domain == parent.clock_domain:
                continue
            if child.start_ns <= parent.end_ns and parent.start_ns <= child.end_ns:
                flagged.add(parent_id)
                flagged.add(child_id)
    return sorted(flagged)
