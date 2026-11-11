from __future__ import annotations

import json
from pathlib import Path

from contracts import TraceDetail, TraceSummary

PRODUCT = Path(__file__).resolve().parent.parent / "fixtures" / "product"


def test_trace_list_fixtures_parse():
    for path in sorted(PRODUCT.glob("*-trace-list.json")):
        envelope = json.loads(path.read_text())
        assert envelope["schema"] == "zerotrace.trace-list"
        assert envelope["schema_version"] == "1.0.0"
        for raw in envelope["payload"]["items"]:
            summary = TraceSummary.from_dict(raw)
            roundtrip = json.loads(json.dumps(summary.to_dict()))
            assert TraceSummary.from_dict(roundtrip) == summary


def test_trace_detail_fixtures_parse():
    for path in sorted(PRODUCT.glob("*-detail.json")):
        envelope = json.loads(path.read_text())
        assert envelope["schema"] == "zerotrace.trace"
        detail = TraceDetail.from_dict(envelope["payload"])
        assert detail.spans, f"{path.name} must contain spans"
        for span in detail.spans:
            assert int(span.duration_ns) >= 0
            assert int(span.start_offset_ns) >= 0
