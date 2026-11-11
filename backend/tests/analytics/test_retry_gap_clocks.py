from dataclasses import replace

import pytest

from app.analytics_api.p2_adapter import analytics_trace
from core import derive_calls
from rules import run
from tests.analytics.test_p2_adapter import graph, packet


@pytest.mark.parametrize(("clocks", "usable_indices", "excluded"), [
    (("host", "host", "host"), [1, 2], 0),
    ((None, None, None), [], 40),
    (("a", "b", "c"), [], 40),
    (("a", "b", "b"), [2], 20),
])
def test_r5_only_measures_gaps_between_clients_on_the_same_clock(clocks, usable_indices, excluded):
    traces = []
    for i in range(20):
        spans = packet(trace_id=f"{i + 1:032x}")
        clients = sorted((s for s in spans if s.kind == "client"), key=lambda s: s.start_ns)
        domains = dict(zip((s.span_id for s in clients), clocks, strict=True))
        current = graph([replace(s, clock_domain=domains.get(s.span_id, s.clock_domain)) for s in spans])
        traces.append(analytics_trace(current, derive_calls(current)))

    audit = run({"environment_id": "lab", "traces": traces})
    findings = {f["rule_id"]: f for f in audit["findings"]}
    assert "OBS_RETRIES" in findings
    evaluation = next(e for e in audit["rule_evaluations"] if e["rule_id"] == "R5")
    assert evaluation["eligible_count"] == len(usable_indices) * 20
    assert evaluation["excluded_by_reason"].get("cross_clock_uncertain", 0) == excluded
    if usable_indices:
        assert findings["R5"]["evidence"]["gaps"] == [
            {"after_attempt": index, "samples": 20, "p95_ns": "50000000", "cv": 0.0}
            for index in usable_indices
        ]
    else:
        assert evaluation["status"] == "insufficient_data"
        assert "R5" not in findings
