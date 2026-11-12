"""Derives the sync call model (attempts + logical calls) from a TraceGraph.

Implements rules B5: CLIENT spans of the sync profile become attempts;
messaging is excluded; callee comes from a credible SERVER link, then
target/peer, else unknown; grouping prefers explicit resend markers over
the error->next-call heuristic; overlap without confirmation is ambiguous.
Pure functions: no I/O, no global state, deterministic within a revision.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from contracts import (
    Attempt,
    CallModel,
    EdgeKey,
    EndpointKey,
    LogicalCall,
    NormalizedSpan,
    OperationKey,
    ServiceKey,
    TraceGraph,
)
from contracts.p2 import Confidence, Hypothesis, Outcome

DEFAULT_GROUPING_VERSION = "1.1.0"
OPERATION_NORMALIZATION_VERSION = "1.0.0"
DEFAULT_PROJECT_ID = "default"

_GRPC_DEADLINE_EXCEEDED = 4

_HTTP_METHODS = frozenset(
    {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"}
)
_TIMEOUT_RE = re.compile(r"timeout|timed_?out|deadline", re.IGNORECASE)
_TRANSPORT_RE = re.compile(
    r"connect|reset|refused|dns|eof|broken_?pipe|unreachable|socket|transport",
    re.IGNORECASE,
)
_FAST_FAIL_RE = re.compile(r"circuit_?breaker|fast_?fail|bulkhead|rejected", re.IGNORECASE)
_RETRY_MARKER_RE = re.compile(r"retry|retried|resend|backoff|attempt_?(index|count)", re.IGNORECASE)

_FAILURE_OUTCOMES: frozenset[Outcome] = frozenset(
    {"timeout", "fast_fail", "http_error", "grpc_error", "transport_error", "error_unknown"}
)


@dataclass(frozen=True)
class _AttemptDraft:
    span: NormalizedSpan
    edge_id: str
    outcome: Outcome
    basis: str
    confidence: Confidence
    owner_span_id: str | None
    server: NormalizedSpan | None
    caller_server: NormalizedSpan | None
    upstream_attempt_id: str | None
    offset_in_caller_ns: int | None
    explicit_marker: bool


@dataclass(frozen=True)
class _Segment:
    attempts: tuple[_AttemptDraft, ...]
    hypothesis: Hypothesis
    confidence: Confidence
    evidence: tuple[str, ...]
    gaps_ns: tuple[int, ...]


def derive_calls(
    graph: TraceGraph,
    project_id: str = DEFAULT_PROJECT_ID,
    grouping_version: str = DEFAULT_GROUPING_VERSION,
) -> CallModel:
    spans_by_id = {s.span_id: s for s in graph.spans}
    drafts = [
        _build_draft(graph, project_id, span, spans_by_id)
        for span in sorted(graph.spans, key=lambda s: (s.start_ns, s.span_id))
        if span.kind == "client" and not span.async_markers
    ]

    segments: list[_Segment] = []
    for group in _group_drafts(drafts):
        segments.extend(_segment_group(group))
    segments.sort(key=lambda seg: (seg.attempts[0].span.start_ns, seg.attempts[0].span.span_id))

    attempts: list[Attempt] = []
    calls: list[LogicalCall] = []
    for call_number, segment in enumerate(segments, start=1):
        call_id = f"call-{call_number}"
        first = segment.attempts[0]
        for index, draft in enumerate(segment.attempts, start=1):
            gap_before = (
                None
                if index == 1
                else draft.span.start_ns - segment.attempts[index - 2].span.end_ns
            )
            first_path: bool | None = None if segment.hypothesis == "ambiguous" else index == 1
            attempts.append(
                Attempt(
                    trace_ref=graph.trace_ref,
                    attempt_id=draft.span.span_id,
                    edge_id=draft.edge_id,
                    duration_ns=draft.span.duration_ns,
                    start_ns=draft.span.start_ns,
                    outcome=draft.outcome,
                    classification_basis=draft.basis,
                    confidence=draft.confidence,
                    server_span_id=draft.server.span_id if draft.server else None,
                    server_duration_ns=draft.server.duration_ns if draft.server else None,
                    caller_server_span_id=(
                        draft.caller_server.span_id if draft.caller_server else None
                    ),
                    upstream_attempt_id=draft.upstream_attempt_id,
                    offset_in_caller_ns=draft.offset_in_caller_ns,
                    client_http_status=draft.span.http_status,
                    server_http_status=draft.server.http_status if draft.server else None,
                    error_type=draft.span.error_type,
                    resend_count=draft.span.resend_count,
                    call_id=call_id,
                    attempt_index=index,
                    gap_before_ns=gap_before,
                    first_path=first_path,
                )
            )
        calls.append(
            LogicalCall(
                trace_ref=graph.trace_ref,
                call_id=call_id,
                edge_id=first.edge_id,
                hypothesis=segment.hypothesis,
                confidence=segment.confidence,
                evidence_codes=list(segment.evidence),
                attempt_ids=[d.span.span_id for d in segment.attempts],
                gaps_ns=list(segment.gaps_ns),
                owner_span_id=first.owner_span_id,
            )
        )

    return CallModel(
        trace_ref=graph.trace_ref,
        grouping_version=grouping_version,
        attempts=attempts,
        calls=calls,
    )


def _build_draft(
    graph: TraceGraph,
    project_id: str,
    span: NormalizedSpan,
    spans_by_id: dict[str, NormalizedSpan],
) -> _AttemptDraft:
    candidates = _server_candidates(graph, span, spans_by_id)
    server = candidates[0] if len(candidates) == 1 else None
    ambiguous_link = len(candidates) > 1
    caller = _caller_server(span, spans_by_id)
    edge = _build_edge(project_id, graph.trace_ref.environment_id, span, server, caller)
    outcome, basis, confidence = _classify_outcome(span)
    if ambiguous_link:
        basis = f"{basis};ambiguous_server_link"
        confidence = _min_confidence(confidence, "medium")
    upstream_id = _upstream_attempt_id(caller, spans_by_id)
    offset = span.start_ns - caller.start_ns if caller is not None else None
    return _AttemptDraft(
        span=span,
        edge_id=edge.edge_id(),
        outcome=outcome,
        basis=basis,
        confidence=confidence,
        owner_span_id=caller.span_id if caller is not None else span.parent_span_id,
        server=server,
        caller_server=caller,
        upstream_attempt_id=upstream_id,
        offset_in_caller_ns=offset,
        explicit_marker=_has_explicit_retry_marker(span),
    )


def _server_candidates(
    graph: TraceGraph, client: NormalizedSpan, spans_by_id: dict[str, NormalizedSpan]
) -> list[NormalizedSpan]:
    candidates: list[NormalizedSpan] = []
    seen: set[str] = set()
    for child_id in graph.children_index.get(client.span_id, []):
        child = spans_by_id.get(child_id)
        if child is None or child.kind != "server" or child.span_id in seen:
            continue
        if _intervals_overlap(client, child):
            seen.add(child.span_id)
            candidates.append(child)
    parent_id = client.parent_span_id
    if parent_id is None:
        return candidates
    for sibling_id in graph.children_index.get(parent_id, []):
        sibling = spans_by_id.get(sibling_id)
        if sibling is None or sibling.kind != "server" or sibling.span_id in seen:
            continue
        if not _same_clock(client, sibling):
            continue
        if _intervals_overlap(client, sibling):
            seen.add(sibling.span_id)
            candidates.append(sibling)
    return candidates


def _intervals_overlap(a: NormalizedSpan, b: NormalizedSpan) -> bool:
    return a.start_ns <= b.end_ns and b.start_ns <= a.end_ns


def _same_clock(a: NormalizedSpan, b: NormalizedSpan) -> bool:
    return a.clock_domain is not None and a.clock_domain == b.clock_domain


def _same_scope(a: NormalizedSpan, b: NormalizedSpan) -> bool:
    return _same_clock(a, b) and a.instance_id == b.instance_id


def _caller_server(
    client: NormalizedSpan, spans_by_id: dict[str, NormalizedSpan]
) -> NormalizedSpan | None:
    seen: set[str] = set()
    parent_id = client.parent_span_id
    while parent_id is not None and parent_id not in seen:
        seen.add(parent_id)
        ancestor = spans_by_id.get(parent_id)
        if ancestor is None:
            return None
        if ancestor.kind == "server" and _same_scope(client, ancestor):
            return ancestor
        parent_id = ancestor.parent_span_id
    return None


def _upstream_attempt_id(
    caller: NormalizedSpan | None, spans_by_id: dict[str, NormalizedSpan]
) -> str | None:
    if caller is None or caller.parent_span_id is None:
        return None
    parent = spans_by_id.get(caller.parent_span_id)
    if parent is None or parent.kind != "client" or parent.async_markers:
        return None
    return parent.span_id


def _build_edge(
    project_id: str,
    environment_id: str,
    client: NormalizedSpan,
    server: NormalizedSpan | None,
    caller: NormalizedSpan | None,
) -> EdgeKey:
    caller_key = _service_key(project_id, environment_id, caller or client)
    if caller is not None:
        caller_operation = _operation_key(caller, "server_span")
    else:
        caller_operation = OperationKey(
            protocol="unknown",
            method=None,
            route_or_name="",
            source="fallback_no_caller_server",
            normalization_version=OPERATION_NORMALIZATION_VERSION,
        )

    if server is not None:
        callee = EndpointKey.service_ep(_service_key(project_id, environment_id, server))
        callee_operation = _operation_key(server, "server_span")
    elif client.target:
        callee = EndpointKey.external(client.target)
        callee_operation = _operation_key(client, "peer_address")
    else:
        callee = EndpointKey.unknown()
        callee_operation = _operation_key(client, "client_span")

    return EdgeKey(
        caller=caller_key,
        caller_operation=caller_operation,
        callee=callee,
        callee_operation=callee_operation,
    )


def _service_key(project_id: str, environment_id: str, span: NormalizedSpan) -> ServiceKey:
    return ServiceKey(
        project_id=project_id,
        environment=environment_id,
        namespace=span.namespace,
        service_name=span.service,
    )


def parse_operation(operation: str) -> tuple[str | None, str]:
    head, _, rest = operation.partition(" ")
    if rest and head.upper() in _HTTP_METHODS:
        return head.upper(), rest.strip()
    return None, operation.strip()


def _protocol_of(span: NormalizedSpan) -> str:
    if span.grpc_status is not None:
        return "grpc"
    method, _ = parse_operation(span.operation)
    if span.http_status is not None or method is not None:
        return "http"
    return "unknown"


def _operation_key(span: NormalizedSpan, source: str) -> OperationKey:
    method, route = parse_operation(span.operation)
    return OperationKey(
        protocol=_protocol_of(span),
        method=method,
        route_or_name=route,
        source=source,
        normalization_version=OPERATION_NORMALIZATION_VERSION,
    )


def _classify_outcome(span: NormalizedSpan) -> tuple[Outcome, str, Confidence]:
    if span.grpc_status is not None:
        if span.grpc_status == 0:
            return "ok", "grpc_status_0", "high"
        if span.grpc_status == _GRPC_DEADLINE_EXCEEDED:
            return "timeout", "grpc_deadline_exceeded", "high"
        return "grpc_error", f"grpc_status_{span.grpc_status}", "high"

    if span.http_status is not None:
        if span.http_status >= 400:
            return "http_error", f"http_status_{span.http_status}", "high"
        if span.otel_status == "error":
            return "error_unknown", "otel_error_with_success_http_status", "medium"
        return "ok", f"http_status_{span.http_status}", "high"

    if span.otel_status == "error":
        error_type = span.error_type
        if error_type is None:
            return "error_unknown", "otel_error_no_details", "low"
        if _TIMEOUT_RE.search(error_type):
            return "timeout", "error_type_timeout", "medium"
        if _FAST_FAIL_RE.search(error_type):
            return "fast_fail", "error_type_fast_fail", "medium"
        if _TRANSPORT_RE.search(error_type):
            return "transport_error", "error_type_transport", "medium"
        return "error_unknown", "error_type_unclassified", "low"

    if span.otel_status == "ok":
        return "ok", "otel_status_ok", "high"

    return "outcome_unknown", "status_unset_no_codes", "low"


def _has_explicit_retry_marker(span: NormalizedSpan) -> bool:
    if span.resend_count is not None and span.resend_count > 0:
        return True
    return any(_RETRY_MARKER_RE.search(d) for d in span.diagnostics)


def _min_confidence(a: Confidence, b: Confidence) -> Confidence:
    order: dict[Confidence, int] = {"high": 2, "medium": 1, "low": 0}
    return a if order[a] <= order[b] else b


def _group_drafts(drafts: list[_AttemptDraft]) -> list[list[_AttemptDraft]]:
    groups: dict[tuple[str, str | None, str | None], list[_AttemptDraft]] = {}
    for draft in drafts:
        groups.setdefault((draft.edge_id, draft.owner_span_id, draft.span.logical_call_id), []).append(draft)
    ordered = sorted(
        groups.values(),
        key=lambda g: (min(d.span.start_ns for d in g), min(d.span.span_id for d in g)),
    )
    for group in ordered:
        group.sort(key=lambda d: (d.span.start_ns, d.span.span_id))
    return ordered


def _segment_group(group: list[_AttemptDraft]) -> list[_Segment]:
    if group[0].span.logical_call_id:
        indices = [d.span.attempt_index for d in group]
        consistent = indices == list(range(1, len(group)+1)) and all(
            d.span.resend_count is None or d.span.resend_count == i-1
            for i,d in enumerate(group,1)
        ) and len({d.span.call_ref for d in group}) == 1 and not _has_overlap(group)
        return [_Segment(tuple(group), ("single" if len(group)==1 else "retry") if consistent else "ambiguous",
                         "high" if consistent else "low", ("explicit_logical_call" if consistent else "conflicting_attempt_metadata",), tuple(_gaps(group)))]
    if len(group) == 1:
        return [_Segment((group[0],), "single", "high", ("single_attempt",), ())]

    if any(d.explicit_marker for d in group):
        if _has_overlap(group):
            return [
                _Segment(tuple(group), "hedge", "medium", ("explicit_resend_marker", "overlap"), ())
            ]
        return [
            _Segment(
                tuple(group),
                "retry",
                "high",
                ("explicit_resend_marker", "sequential_gaps"),
                tuple(_gaps(group)),
            )
        ]

    if _has_overlap(group):
        return [_Segment(tuple(group), "ambiguous", "low", ("overlap_unconfirmed",), ())]

    segments: list[list[_AttemptDraft]] = []
    current = [group[0]]
    for nxt in group[1:]:
        last = current[-1]
        if last.outcome in _FAILURE_OUTCOMES and nxt.span.start_ns >= last.span.end_ns:
            current.append(nxt)
        else:
            segments.append(current)
            current = [nxt]
    segments.append(current)

    out: list[_Segment] = []
    for segment in segments:
        if len(segment) == 1:
            out.append(_Segment((segment[0],), "single", "high", ("single_attempt",), ()))
        else:
            out.append(
                _Segment(
                    tuple(segment),
                    "retry",
                    "medium",
                    ("error_then_next_call", "sequential_gaps"),
                    tuple(_gaps(segment)),
                )
            )
    return out


def _has_overlap(attempts: list[_AttemptDraft]) -> bool:
    return any(nxt.span.start_ns < cur.span.end_ns for cur, nxt in itertools.pairwise(attempts))


def _gaps(attempts: list[_AttemptDraft]) -> list[int]:
    return [nxt.span.start_ns - cur.span.end_ns for cur, nxt in itertools.pairwise(attempts)]
