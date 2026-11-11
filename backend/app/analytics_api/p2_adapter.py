"""Adapt a pinned P2 graph and B's call classification for the analytics algorithms."""
from __future__ import annotations

from contracts import ServiceKey
from contracts.p2 import CallModel, TraceGraph
from core.calls import DEFAULT_PROJECT_ID

from analytics_support import AnalyticsError, ref_key


def merge_edge_labels(edges, observed):
    # A B edge can lack a caller operation (e.g. unknown clocks). Conflicting
    # routes observed for that edge cannot be bound to a route-scoped policy.
    for edge_id, label in observed.items():
        previous = edges.get(edge_id)
        if previous is not None:
            if previous.get("caller_operation") != label.get("caller_operation"):
                label = {**label, "caller_operation": None}
            if previous.get("mixed_upstream") or previous.get("upstream") != label.get("upstream"):
                label = {**label, "upstream": None, "mixed_upstream": True}
        edges[edge_id] = label


def analytics_trace(graph: TraceGraph, calls: CallModel, *, project_id: str = DEFAULT_PROJECT_ID) -> dict:
    trace = graph.to_dict()
    model = calls.to_dict()
    trace_ref = trace["trace_ref"]
    key = ref_key(trace_ref)
    if (ref_key(model["trace_ref"]) != key or any(
        row["trace_ref"] != trace_ref and ref_key(row["trace_ref"]) != key
        for row in model["attempts"] + model["calls"]
    )):
        raise AnalyticsError("trace_ref_mismatch", "Graph and call evidence use different trace revisions", 409)

    spans = {s["span_id"]: s for s in trace["spans"]}
    for span in graph.spans:
        spans[span.span_id]["service"] = ServiceKey(
            project_id=project_id,
            environment=graph.trace_ref.environment_id,
            namespace=span.namespace,
            service_name=span.service,
        ).to_dict()
    edges = {}
    for span in spans.values():
        # Scope the gRPC status to the span that reported it.
        if span["grpc_status"] is not None:
            span["outcome"] = "ok" if span["grpc_status"] == 0 else "grpc_error"
    for attempt in model["attempts"]:
        client = spans.get(attempt["attempt_id"])
        if client is None or client["kind"] != "client":
            raise AnalyticsError("inconsistent_dataset", "Attempt must identify a CLIENT span in its own trace", 409)
        server = spans.get(attempt["server_span_id"])
        caller = spans.get(attempt["caller_server_span_id"])
        if caller is None:
            # A local W3C parent identifies the route even when clock domains
            # are absent. This link supplies identity only, never a duration.
            parent = spans.get(client.get("parent_span_id"))
            if (parent and parent["kind"] == "server" and parent["service"] == client["service"]
                    and parent.get("instance_id") == client.get("instance_id")):
                caller = parent
        # An external target may be displayed, but cannot identify a writable adapter.
        labels = {"caller": caller["service"] if caller else client["service"],
                  "callee": server["service"] if server else client["target"] or "unknown",
                  "operation": server["operation"] if server else client["operation"],
                  "caller_operation": caller["operation"] if caller else None}
        upstream = spans.get(caller.get("parent_span_id")) if caller else None
        if upstream and upstream["kind"] == "client":
            labels["upstream"] = {"caller": upstream["service"], "call_ref": upstream.get("call_ref")}
        edge_id = attempt["edge_id"]
        merge_edge_labels(edges, {edge_id: labels})
        client["edge_id"] = edge_id
        client["outcome"] = attempt["outcome"]
        attempt["edge"] = labels
        # B numbers all grouped attempts, including heuristic groups. P11 needs the lab labels.
        attempt["membership_source"] = "derived"
        if client["grpc_status"] is not None:
            attempt.update(protocol="grpc", client_grpc_status=client["grpc_status"])
        elif client["field_coverage"].get("operation"):
            method, separator, _ = client["operation"].partition(" ")
            if separator and method in {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"}:
                attempt.update(method=method, protocol="http")
        if client.get("service_version") is not None:
            attempt["caller_version"] = client["service_version"]
        if server and server.get("service_version") is not None:
            attempt["callee_version"] = server["service_version"]
    trace.update(project_id=project_id, attempts=model["attempts"], calls=model["calls"], grouping_version=model["grouping_version"],
                 edges=edges)
    return trace
