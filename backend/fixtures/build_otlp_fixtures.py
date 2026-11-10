"""Builds fixtures/otlp/*.json (OTLP/JSON export requests) consistent with fixtures/model/.

Run: python fixtures/build_otlp_fixtures.py
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

MS = 1_000_000
BASE_NS = 1_763_000_000_000_000_000

OUT = Path(__file__).resolve().parent / "otlp"


def attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        v: dict[str, Any] = {"stringValue": value}
    elif isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        raise TypeError(f"unsupported attribute value for {key}: {value!r}")
    return {"key": key, "value": v}


def span(
    trace_id: str,
    span_id: str,
    parent_span_id: str | None,
    name: str,
    kind: int,
    start_ns: int,
    end_ns: int,
    attributes: list[dict[str, Any]],
    status_code: int | None = None,
) -> dict[str, Any]:
    s: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span_id,
        "name": name,
        "kind": kind,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano": str(end_ns),
        "attributes": attributes,
    }
    if parent_span_id is not None:
        s["parentSpanId"] = parent_span_id
    if status_code is not None:
        s["status"] = {"code": status_code}
    return s


def resource_spans(environment: str, spans: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "resource": {
            "attributes": [
                attr("service.name", "orders"),
                attr("service.namespace", "default"),
                attr("service.version", "1.4.2"),
                attr("service.instance.id", "orders-7f9c9b6b5-x1"),
                attr("deployment.environment", environment),
            ]
        },
        "scopeSpans": [
            {
                "scope": {"name": "zerotrace-demo-instrumentation", "version": "1.0.0"},
                "spans": spans,
            }
        ],
    }


def s00_spans() -> list[dict[str, Any]]:
    trace_id = "aa" * 16
    t0 = BASE_NS
    root = span(
        trace_id,
        "00" * 7 + "01",
        None,
        "GET /checkout",
        2,
        t0,
        t0 + 500 * MS,
        [
            attr("http.request.method", "GET"),
            attr("http.route", "/checkout"),
            attr("url.path", "/checkout"),
            attr("http.response.status_code", 200),
        ],
    )
    client = span(
        trace_id,
        "00" * 7 + "02",
        "00" * 7 + "01",
        "POST /pay",
        3,
        t0 + 10 * MS,
        t0 + 360 * MS,
        [
            attr("http.request.method", "POST"),
            attr("url.full", "https://payment/pay?order_id=12345"),
            attr("http.response.status_code", 200),
            attr("server.address", "payment"),
        ],
    )
    return [root, client]


def s01_spans() -> list[dict[str, Any]]:
    trace_id = "bb" * 16
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
        "GET /checkout",
        2,
        t0,
        t0 + root_duration,
        [
            attr("http.request.method", "GET"),
            attr("http.route", "/checkout"),
            attr("url.path", "/checkout"),
            attr("http.response.status_code", 503),
        ],
        status_code=2,
    )
    clients = [
        span(
            trace_id,
            "00" * 7 + f"0{i + 2}",
            "00" * 7 + "01",
            "POST /pay",
            3,
            starts[i],
            starts[i] + attempt_durations[i],
            [
                attr("http.request.method", "POST"),
                attr("http.request.resend_count", i),
                attr("url.full", "https://payment/pay?order_id=987654321"),
                attr("http.response.status_code", 503),
                attr("server.address", "payment"),
                attr("error.type", "http_5xx"),
            ],
            status_code=2,
        )
        for i in range(3)
    ]
    return [root, *clients]


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def main() -> None:
    write_json(OUT / "s00_healthy.json", {"resourceSpans": [resource_spans("shop", s00_spans())]})
    write_json(OUT / "s01_retry.json", {"resourceSpans": [resource_spans("sandbox", s01_spans())]})
    duplicate_block = resource_spans("shop", s00_spans())
    write_json(
        OUT / "late_duplicate.json",
        {"resourceSpans": [duplicate_block, copy.deepcopy(duplicate_block)]},
    )
    print(f"fixtures written to {OUT}")


if __name__ == "__main__":
    main()
