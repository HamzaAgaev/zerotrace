"""P11 evidence through the product facade, OTLP normalization and frozen SQLite samples."""

import json
import sqlite3
from copy import deepcopy
from pathlib import Path

import pytest

from analytics_support import AnalyticsError
from app.analytics_api.facade import AnalyticsFacade
from contracts import IngestLimits, SourceBinding
from ingest import RedactionProfile, SourceProfile, decode_otlp, normalize
from repository import SpanStore

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "otlp" / "s01_retry.json"
BINDING = SourceBinding("stand", "checkout:sandbox", "sandbox")


def ingest(store, document, now):
    decoded = decode_otlp(json.dumps(document).encode(), "application/json", None, IngestLimits())
    return store.ingest(
        normalize(decoded, SourceProfile(clock_domain="host"), RedactionProfile()), BINDING, now
    )


def attribute(span, key, value):
    kind = (
        "boolValue" if type(value) is bool else "intValue" if type(value) is int else "stringValue"
    )
    span["attributes"].append({"key": "zerotrace." + key, "value": {kind: value}})


def frozen_run(
    store,
    run_id,
    trace_number,
    count,
    *,
    missing=None,
    conflicting=False,
    probe=False,
    recorded_call="charge",
):
    store.start_collection(run_id, "sandbox", 1, 100)
    document = json.loads(FIXTURE.read_bytes())
    scope = document["resourceSpans"][0]["scopeSpans"][0]
    spans = scope["spans"] = scope["spans"][: count + 1]
    for span in spans:
        span["traceId"] = f"{trace_number:032x}"
    root, clients = spans[0], spans[1:]
    if missing != "run_id":
        attribute(root, "run_id", run_id)
    # A nested marker is enough for B to freeze this trace even when the entry
    # has no run_id. It is deliberately insufficient for C's comparison.
    attribute(clients[0], "run_id", "other-run" if conflicting else run_id)
    for index, client in enumerate(clients, 1):
        client["attributes"] = [
            a for a in client["attributes"] if a["key"] != "http.request.resend_count"
        ]
        for key, value in [
            ("call_ref", recorded_call),
            ("logical_call_id", "actual-lab-id"),
            ("attempt_index", index),
        ]:
            if key != missing:
                attribute(client, key, value)
    if probe:
        # CLIENT data and the probe SERVER may be exported in separate batches.
        scope["spans"] = clients
        ingest(store, document, 10)
        scope["spans"] = [root]
        attribute(root, "probe", True)
    ingest(store, document, 20)
    sample = store.finalize_collection(run_id, 100)
    assert sample.status == "ready"
    ref = sample.trace_refs[0].to_dict()

    call = dict(
        call_ref="charge",
        routes=["GET /checkout"],
        max_attempts=count,
        timeout_ms=250,
        backoff_ms=50,
        retry_on=["503"],
        target=dict(service_id="payment", method="POST", route="/pay"),
    )
    route = {
        "route": "GET /checkout",
        "work": {"kind": "fixed", "delay_ms": 0},
        "calls": {
            "charge": {
                key: deepcopy(value)
                for key, value in call.items()
                if key not in {"call_ref", "routes"}
            }
        },
        "steps": [{"call": "charge"}],
    }
    orders = {"service_id": "orders", "revision": run_id, "calls": [call], "routes": [route]}
    payment = {
        "service_id": "payment",
        "revision": "p1",
        "calls": [],
        "routes": [{"route": "POST /pay", "work": {"kind": "fixed", "delay_ms": 0}}],
    }
    return {
        "run_id": run_id,
        "stand_id": "stand",
        "status": "completed",
        "environment_id": "sandbox",
        "scenario_id": "checkout",
        "scenario_revision": 1,
        "fault_revision": 1,
        "faults": [],
        "load": {"request_count": 1},
        "seed": 10,
        "config_snapshots": {"orders": orders, "payment": payment},
        "config_revisions": {"orders": run_id, "payment": "p1"},
        "trace_collection": {
            "status": sample.status,
            "expected_count": 1,
            "received_count": 1,
            "trace_refs": [ref],
        },
    }


@pytest.fixture
def lab(tmp_path):
    with SpanStore(tmp_path / "lab.sqlite") as store:
        facade = AnalyticsFacade(store, {})
        try:
            yield store, facade
        finally:
            facade.results.close()


@pytest.mark.parametrize("missing", ["logical_call_id", "attempt_index", "call_ref", "run_id"])
def test_no_inferred_laboratory_labels(lab, missing):
    store, facade = lab
    before = frozen_run(store, "before", 1, 3)
    after = frozen_run(store, "after", 2, 2, missing=missing)
    with pytest.raises(AnalyticsError) as exc:
        facade.compare_runs(before, after)
    assert exc.value.code == "invalid_run_sample"


@pytest.mark.parametrize("option", ["conflicting", "probe"])
def test_rejects_frozen_probe_or_other_run_marker(lab, option):
    store, facade = lab
    before = frozen_run(store, "before", 1, 3)
    after = frozen_run(store, "after", 2, 2, **{option: True})
    with pytest.raises(AnalyticsError) as exc:
        facade.compare_runs(before, after)
    assert exc.value.code == "invalid_run_sample"


@pytest.mark.parametrize("confound", ["retry_on", "other_service", "new_setting", "changed_target"])
def test_rejects_a_second_config_change(lab, confound):
    store, facade = lab
    before = frozen_run(store, "before", 1, 3)
    after = frozen_run(store, "after", 2, 2)
    configs = after["config_snapshots"]
    orders = configs["orders"]
    if confound == "retry_on":
        orders["routes"][0]["calls"]["charge"]["retry_on"] = orders["calls"][0]["retry_on"] = [
            "429"
        ]
    elif confound == "changed_target":
        for call in (orders["calls"][0], orders["routes"][0]["calls"]["charge"]):
            call["target"]["route"] = "/other"
    elif confound == "other_service":
        configs["payment"]["routes"][0]["work"]["delay_ms"] = 500
        configs["payment"]["revision"] = after["config_revisions"]["payment"] = "p2"
    else:
        orders["routes"][0]["future_option"] = True
    with pytest.raises(AnalyticsError) as exc:
        facade.compare_runs(before, after)
    assert exc.value.code == "runs_not_comparable"


@pytest.mark.parametrize("mode", ["fixed", "lognormal", "probabilistic_fault", "random_boundary"])
def test_seed_matters_for_stochastic_runs(lab, mode):
    store, facade = lab
    before = frozen_run(store, "before", 1, 3)
    after = frozen_run(store, "after", 2, 2)
    for run in (before, after):
        if mode == "lognormal":
            run["config_snapshots"]["payment"]["routes"][0]["work"] = {
                "kind": "lognormal",
                "p50_ms": 7,
                "p99_ms": 11,
            }
        elif mode == "probabilistic_fault":
            run["faults"] = [{"service_id": "payment", "error_rate": 0.3}]
        elif mode == "random_boundary":
            run["faults"] = [
                {"service_id": "payment", "error_rate": 1.0, "failure_point": {"mode": "random"}}
            ]
    assert facade.compare_runs(before, after)["attempts_per_input_delta"] == -1
    after["seed"] = 11
    if mode == "fixed":
        result = facade.compare_runs(before, after)
        assert (result["baseline"]["seed"], result["candidate"]["seed"]) == (10, 11)
    else:
        with pytest.raises(AnalyticsError) as exc:
            facade.compare_runs(before, after)
        assert exc.value.code == "runs_not_comparable"


def test_frozen_span_labels_survive_late_ingest_and_restart(tmp_path):
    path = tmp_path / "lab.sqlite"
    with SpanStore(path) as store:
        before = frozen_run(store, "before", 1, 3)
        after = frozen_run(store, "after", 2, 2)
        facade = AnalyticsFacade(store, {})
        first = facade.compare_runs(before, after)
        facade.results.close()
        document = json.loads(FIXTURE.read_bytes())
        scope = document["resourceSpans"][0]["scopeSpans"][0]
        late = scope["spans"][-1]
        late.update(traceId=f"{1:032x}", spanId="0000000000000009")
        scope["spans"] = [late]
        assert ingest(store, document, 101).trace_refs[0].revision == 2

    with SpanStore(path) as store:
        facade = AnalyticsFacade(store, {})
        try:
            result = facade.compare_runs(before, after)
            assert result["analysis_id"] == first["analysis_id"]
            assert (result["baseline"]["attempts"], result["candidate"]["attempts"]) == (3, 2)
        finally:
            facade.results.close()


def test_replayed_otlp_is_compatible_with_historical_span_payloads(tmp_path):
    document = json.loads(FIXTURE.read_bytes())
    root = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    attribute(root, "run_id", "before")
    path = tmp_path / "lab.sqlite"
    with SpanStore(path) as store:
        ref = ingest(store, document, 10).trace_refs[0]
    # Simulate the payload written by B 0.3, before P11 labels were retained.
    with sqlite3.connect(path) as db:
        for sid, payload in db.execute("SELECT span_id, payload FROM spans").fetchall():
            historical = json.loads(payload)
            for key in ("run_id", "probe", "call_ref", "logical_call_id", "attempt_index"):
                historical.pop(key)
            db.execute("UPDATE spans SET payload=? WHERE span_id=?", (json.dumps(historical), sid))
    with SpanStore(path) as store:
        store.start_collection("before", "sandbox", 1, 20)
        assert store.finalize_collection("before", 20).trace_refs == [ref]
        replay = ingest(store, document, 30)
        assert replay.trace_refs == [ref] and not replay.diagnostics
        assert store.get_spans(ref)[0].run_id is None
        root["name"] = "different"
        assert [i.code for i in ingest(store, document, 40).diagnostics] == ["conflicting_span"]


def test_new_span_payload_detects_conflicting_run_labels(tmp_path):
    document = json.loads(FIXTURE.read_bytes())
    root = document["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    with SpanStore(tmp_path / "lab.sqlite") as store:
        ref = ingest(store, document, 10).trace_refs[0]
        attribute(root, "run_id", "later")
        assert [i.code for i in ingest(store, document, 20).diagnostics] == ["conflicting_span"]
        assert store.get_spans(ref)[0].run_id is None


def test_incomplete_frozen_fault_is_not_comparable(lab):
    store, facade = lab
    before = frozen_run(store, "before", 1, 3)
    after = frozen_run(store, "after", 2, 2)
    for run in (before, after):
        run["faults"] = [{"route": "/pay"}]
    with pytest.raises(AnalyticsError) as exc:
        facade.compare_runs(before, after)
    assert exc.value.code == "runs_not_comparable"


@pytest.mark.parametrize("declared", [True, False])
def test_zero_attempts_require_matching_another_declared_call(lab, declared):
    store, facade = lab
    before = frozen_run(store, "before", 1, 3)
    after = frozen_run(store, "after", 2, 2, recorded_call="second")
    if declared:
        for run in (before, after):
            orders = run["config_snapshots"]["orders"]
            other = {**orders["calls"][0], "call_ref": "second", "max_attempts": 2}
            orders["calls"].append(other)
            route_calls = orders["routes"][0]["calls"]
            route_calls["second"] = {**route_calls["charge"], "max_attempts": 2}
        result = facade.compare_runs(before, after)
        assert result["candidate"]["attempt_count_histogram"] == {"0": 1}
    else:
        with pytest.raises(AnalyticsError) as exc:
            facade.compare_runs(before, after)
        assert exc.value.code == "invalid_run_sample"
