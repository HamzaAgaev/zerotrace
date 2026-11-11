"""Sequential chain extraction for the stand generator (B -> A seam).

P11 semantics: one full sequential HTTP chain with an unambiguous root and
unambiguous links; parallel branches, cycles and missing links produce a
clear refusal; an external callee without a server span is allowed as the
last step when the outgoing call and its outcome are unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from contracts.base import TraceRef
from contracts.p2 import CallModel, TraceGraph

ChainRefusalCode = Literal[
    "empty_trace",
    "ambiguous_root",
    "missing_link",
    "ambiguous_link",
    "parallel_branches",
    "cycle",
]


@dataclass(frozen=True)
class ChainCall:
    index: int
    caller: str
    callee: str
    operation: str
    duration_ns: int
    server_observed: bool
    http_status: int | None = None
    attempts: int = 1


@dataclass(frozen=True)
class ExtractedChain:
    source: TraceRef
    calls: list[ChainCall]
    incompleteness: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ChainRefusal:
    code: ChainRefusalCode
    problems: list[str]


ChainResult = ExtractedChain | ChainRefusal


def extract_chain(graph: TraceGraph, calls: CallModel) -> ChainResult:
    spans = {s.span_id: s for s in graph.spans}
    if not spans:
        return ChainRefusal("empty_trace", ["trace has no spans"])

    if not graph.roots:
        return ChainRefusal("empty_trace", ["trace has no root spans"])
    if len(graph.roots) > 1:
        return ChainRefusal(
            "ambiguous_root",
            [f"multiple roots: {', '.join(sorted(graph.roots))}"],
        )
    root = spans.get(graph.roots[0])
    if root is None or root.kind != "server":
        return ChainRefusal(
            "ambiguous_root",
            [f"root span {graph.roots[0]!r} is not a SERVER span"],
        )

    by_owner: dict[str, list] = {}
    for call in calls.calls:
        owner = call.owner_span_id or ""
        by_owner.setdefault(owner, []).append(call)

    steps: list[ChainCall] = []
    incompleteness: list[str] = []
    seen: set[str] = set()
    current_id = root.span_id
    index = 0

    while True:
        if current_id in seen:
            return ChainRefusal("cycle", [f"cycle detected at span {current_id!r}"])
        seen.add(current_id)
        owner_span = spans.get(current_id)
        owner_service = owner_span.service if owner_span is not None else "unknown"

        owned = by_owner.get(current_id, [])
        distinct_edges = {c.edge_id for c in owned}
        if len(distinct_edges) > 1:
            return ChainRefusal(
                "parallel_branches",
                [
                    f"span {current_id!r} owns calls on multiple edges: "
                    f"{', '.join(sorted(distinct_edges))}"
                ],
            )
        if not owned:
            break

        call = owned[0]
        if call.hypothesis == "ambiguous":
            return ChainRefusal(
                "ambiguous_link",
                [f"call {call.call_id!r} on edge {call.edge_id!r} is ambiguous"],
            )

        attempt = _representative_attempt(calls, call.attempt_ids)
        if attempt is None:
            return ChainRefusal(
                "missing_link",
                [f"call {call.call_id!r} has no resolvable attempts"],
            )
        client = spans.get(attempt.attempt_id)
        if client is None:
            return ChainRefusal(
                "missing_link",
                [f"attempt span {attempt.attempt_id!r} not in trace"],
            )

        server_span = spans.get(attempt.server_span_id) if attempt.server_span_id else None
        if server_span is not None:
            callee = server_span.service
            server_observed = True
            next_owner = server_span.span_id
        else:
            target = (client.target or "").strip()
            if not target or target == "unknown":
                return ChainRefusal(
                    "missing_link",
                    [
                        f"call {call.call_id!r} has no server span and no "
                        f"observable target (span {client.span_id!r})"
                    ],
                )
            callee = target
            server_observed = False
            incompleteness.append(f"step {index}: callee {callee!r} observed by outgoing call only")
            next_owner = None

        http_status = attempt.server_http_status or attempt.client_http_status
        if http_status is None:
            incompleteness.append(f"step {index}: no observed http status")
        steps.append(
            ChainCall(
                index=index,
                caller=owner_service,
                callee=callee,
                operation=client.operation,
                duration_ns=attempt.duration_ns,
                server_observed=server_observed,
                http_status=http_status,
                attempts=len(call.attempt_ids),
            )
        )
        index += 1
        if next_owner is None:
            break
        current_id = next_owner

    if not steps:
        return ChainRefusal("missing_link", [f"root span {root.span_id!r} has no outgoing calls"])
    return ExtractedChain(source=graph.trace_ref, calls=steps, incompleteness=incompleteness)


def _representative_attempt(calls: CallModel, attempt_ids: list[str]):
    attempts = {a.attempt_id: a for a in calls.attempts}
    ordered = sorted(
        (attempts[aid] for aid in attempt_ids if aid in attempts),
        key=lambda a: (a.start_ns, a.attempt_id),
    )
    return ordered[0] if ordered else None
