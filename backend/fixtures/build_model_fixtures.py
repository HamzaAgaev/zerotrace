"""Builds fixtures/model/*.json with deterministic control numbers.

Run: python fixtures/build_model_fixtures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.contracts import (
    Attempt,
    CallModel,
    EdgeKey,
    EndpointKey,
    LogicalCall,
    NormalizedSpan,
    OperationKey,
    Quality,
    ServiceKey,
    TraceGraph,
    TraceRef,
)

MS = 1_000_000
BASE_NS = 1_763_000_000_000_000_000

OUT = Path(__file__).resolve().parent / "model"


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def orders_key(env: str) -> ServiceKey:
    return ServiceKey(
        project_id="zerotrace-demo",
        environment=env,
        namespace="default",
        service_name="orders",
    )


def edge(env: str) -> EdgeKey:
    return EdgeKey(
        caller=orders_key(env),
        caller_operation=OperationKey(
            protocol="http",
            method="GET",
            route_or_name="/checkout",
            source="route_template",
            normalization_version="1.0.0",
        ),
        callee=EndpointKey.external("payment"),
        callee_operation=OperationKey(
            protocol="http",
            method="POST",
            route_or_name="/pay",
            source="peer_address",
            normalization_version="1.0.0",
        ),
    )


def span(
    trace_id: str,
    span_id: str,
    parent: str | None,
    service: str,
    kind: str,
    start_ns: int,
    duration_ns: int,
    operation: str,
    otel_status: str = "unset",
    http_status: int | None = None,
) -> NormalizedSpan:
    return NormalizedSpan(
        trace_id=trace_id,
        span_id=span_id,
        parent_span_id=parent,
        service=service,
        name=f"{kind.upper()} {operation}",
        kind=kind,  # type: ignore[arg-type]
        start_ns=start_ns,
        end_ns=start_ns + duration_ns,
        duration_ns=duration_ns,
        clock_domain="host-1",
        operation=operation,
        otel_status=otel_status,  # type: ignore[arg-type]
        http_status=http_status,
        field_coverage={"service.name": True, "operation": True, "http_status": True},
        diagnostics=[],
    )


def graph(
    trace_ref: TraceRef, spans: list[NormalizedSpan], first_ns: int, last_ns: int
) -> TraceGraph:
    parent_index = {s.span_id: s.parent_span_id for s in spans}
    children: dict[str, list[str]] = {s.span_id: [] for s in spans}
    by_id = {s.span_id: s for s in spans}
    roots: list[str] = []
    for s in spans:
        if s.parent_span_id is None or s.parent_span_id not in by_id:
            roots.append(s.span_id)
        else:
            children[s.parent_span_id].append(s.span_id)
    for kids in children.values():
        kids.sort(key=lambda sid: (by_id[sid].start_ns, by_id[sid].end_ns, sid))
    return TraceGraph(
        trace_ref=trace_ref,
        first_received_at_ns=first_ns,
        last_received_at_ns=last_ns,
        assembly_state="quiet",
        spans=spans,
        roots=roots,
        parent_index=parent_index,
        children_index=children,
        quality=Quality(),
    )


def build_s00() -> None:
    trace_id = "aa" * 16
    ref = TraceRef(environment_id="shop", trace_id=trace_id, generation=1, revision=1)
    e = edge("shop")
    t0 = BASE_NS
    root = span(
        trace_id,
        "00" * 7 + "01",
        None,
        "orders",
        "server",
        t0,
        500 * MS,
        "GET /checkout",
        http_status=200,
    )
    client = span(
        trace_id,
        "00" * 7 + "02",
        root.span_id,
        "orders",
        "client",
        t0 + 10 * MS,
        350 * MS,
        "POST /pay",
        http_status=200,
    )
    g = graph(ref, [root, client], t0, t0 + 600 * MS)
    write_json(OUT / "s00_healthy" / "trace.json", g.to_dict())

    call = CallModel(
        trace_ref=ref,
        grouping_version="1.0.0",
        attempts=[
            Attempt(
                trace_ref=ref,
                attempt_id=client.span_id,
                edge_id=e.edge_id(),
                duration_ns=client.duration_ns,
                start_ns=client.start_ns,
                outcome="ok",
                classification_basis="single_client_span",
                confidence="high",
                client_http_status=200,
                call_id="call-1",
                attempt_index=1,
                first_path=True,
            )
        ],
        calls=[
            LogicalCall(
                trace_ref=ref,
                call_id="call-1",
                edge_id=e.edge_id(),
                hypothesis="single",
                confidence="high",
                evidence_codes=["single_attempt"],
                attempt_ids=[client.span_id],
                gaps_ns=[],
            )
        ],
    )
    write_json(OUT / "s00_healthy" / "calls.json", call.to_dict())


def build_s01() -> None:
    trace_id = "bb" * 16
    run_id = "run-fixture-1"
    ref = TraceRef(environment_id="sandbox", trace_id=trace_id, generation=1, revision=1)
    e = edge("sandbox")
    t0 = BASE_NS

    attempt_durations = [250 * MS, 250 * MS, 245 * MS]
    gaps = [50 * MS, 50 * MS]
    starts = [t0 + 5 * MS]
    for i in range(1, 3):
        starts.append(starts[-1] + attempt_durations[i - 1] + gaps[i - 1])
    root_duration = (starts[-1] + attempt_durations[-1]) - t0 + 10 * MS

    root = span(
        trace_id,
        "00" * 7 + "01",
        None,
        "orders",
        "server",
        t0,
        root_duration,
        "GET /checkout",
        otel_status="error",
        http_status=503,
    )
    clients = [
        span(
            trace_id,
            "00" * 7 + f"0{i + 2}",
            root.span_id,
            "orders",
            "client",
            starts[i],
            attempt_durations[i],
            "POST /pay",
            otel_status="error",
            http_status=503,
        )
        for i in range(3)
    ]
    g = graph(ref, [root, *clients], t0, t0 + 900 * MS)
    write_json(OUT / "s01_retry" / "trace.json", g.to_dict())

    attempts = [
        Attempt(
            trace_ref=ref,
            attempt_id=c.span_id,
            edge_id=e.edge_id(),
            duration_ns=c.duration_ns,
            start_ns=c.start_ns,
            outcome="http_error",
            classification_basis="explicit_lab_labels",
            confidence="high",
            client_http_status=503,
            call_id="call-1",
            attempt_index=i + 1,
            gap_before_ns=None if i == 0 else gaps[i - 1],
            first_path=i == 0,
        )
        for i, c in enumerate(clients)
    ]
    call = CallModel(
        trace_ref=ref,
        grouping_version="1.0.0",
        attempts=attempts,
        calls=[
            LogicalCall(
                trace_ref=ref,
                call_id="call-1",
                edge_id=e.edge_id(),
                owner_span_id=root.span_id,
                hypothesis="retry",
                confidence="high",
                evidence_codes=["explicit_lab_labels", "sequential_gaps"],
                attempt_ids=[c.span_id for c in clients],
                gaps_ns=gaps,
            )
        ],
    )
    write_json(OUT / "s01_retry" / "calls.json", call.to_dict())
    write_json(
        OUT / "s01_retry" / "run.json",
        {
            "run_id": run_id,
            "environment_id": "sandbox",
            "trace_refs": [ref.to_dict()],
        },
    )


if __name__ == "__main__":
    build_s00()
    build_s01()
    print(f"fixtures written to {OUT}")
