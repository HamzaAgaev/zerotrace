from __future__ import annotations

import json
from pathlib import Path

from contracts import TraceSummary

FRONTEND_FIXTURES = Path(__file__).resolve().parent.parent.parent / "frontend" / "fixtures"


def test_frontend_trace_list_fixture_parses_into_contracts():
    data = json.loads((FRONTEND_FIXTURES / "frontend-contracts.json").read_text())
    envelope = data["list"]
    assert envelope["schema"] == "zerotrace.trace-list"
    items = envelope["payload"]["items"]
    assert items, "fixture must contain trace summaries"
    for raw in items:
        summary = TraceSummary.from_dict(raw)
        again = json.loads(json.dumps(summary.to_dict()))
        assert TraceSummary.from_dict(again) == summary


def test_frontend_trace_detail_fixture_matches_span_view():
    from contracts import SpanView

    data = json.loads((FRONTEND_FIXTURES / "frontend-contracts.json").read_text())
    detail = data.get("detail")
    if detail is None:
        return
    for raw in detail["payload"]["spans"]:
        span = SpanView.from_dict(raw)
        again = json.loads(json.dumps(span.to_dict()))
        assert SpanView.from_dict(again) == span
