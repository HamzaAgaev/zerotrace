from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.calls import derive_calls
from core.model import (
    EdgeContribution,
    build_edge_policy,
    build_edge_stats,
    build_model,
    contribution_digest,
    is_current_policy,
    pauses_ns,
    sourced_value,
)
from tests.test_core_calls import BASE_NS, MS, load_trace, make_graph, make_span, sid

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "model"


def contribution(name: str) -> EdgeContribution:
    graph = load_trace(name)
    return EdgeContribution(graph=graph, calls=derive_calls(graph))


class TestEdgeStats:
    def test_healthy_trace_single_successful_attempt(self):
        stats = build_edge_stats([contribution("s00_healthy")])
        assert len(stats) == 1
        (edge,) = stats.values()
        assert edge.attempts == 1
        assert edge.eligible_calls == 1
        assert edge.ambiguous_calls == 0
        assert edge.trace_count == 1
        assert edge.known_outcomes == 1
        assert edge.unknown_outcomes == 0
        assert edge.errors == 0
        assert edge.coverage == 1.0
        assert edge.attempt_error_ratio == 0.0
        assert edge.success_p50_ns == 350 * MS
        assert edge.success_p95_ns == 350 * MS
        assert edge.gaps_by_index == {}
        assert edge.jitter_cv is None

    def test_retry_trace_counts_attempts_gaps_and_errors(self):
        stats = build_edge_stats([contribution("s01_retry")])
        (edge,) = stats.values()
        assert edge.attempts == 3
        assert edge.eligible_calls == 1
        assert edge.errors == 3
        assert edge.success_p50_ns is None
        assert sorted(edge.gaps_by_index) == [2, 3]
        assert edge.jitter_cv == 0.0

    def test_ambiguous_call_is_not_eligible(self):
        spans = [
            make_span(sid(1), None, "server", BASE_NS, 900 * MS),
            make_span(sid(2), sid(1), "client", BASE_NS + 10 * MS, 300 * MS, operation="POST /pay"),
            make_span(
                sid(3), sid(1), "client", BASE_NS + 110 * MS, 300 * MS, operation="POST /pay"
            ),
        ]
        graph = make_graph(spans)
        stats = build_edge_stats([EdgeContribution(graph, derive_calls(graph))])
        (edge,) = stats.values()
        assert edge.eligible_calls == 0
        assert edge.ambiguous_calls == 1
        assert edge.attempts == 2

    def test_merge_across_traces_counts_distinct_traces(self):
        contributions = [contribution("s01_retry"), contribution("s01_retry")]
        stats = build_edge_stats(contributions)
        (edge,) = stats.values()
        assert edge.attempts == 6
        assert edge.trace_count == 1
        assert edge.eligible_calls == 2

    def test_no_samples_means_no_entry(self):
        assert build_edge_stats([]) == {}

    def test_dataset_ref_is_stable_and_input_sensitive(self):
        first = build_edge_stats([contribution("s00_healthy")], dataset_ref="ds-1")
        second = build_edge_stats([contribution("s00_healthy")], dataset_ref="ds-1")
        assert first == second
        assert contribution_digest([contribution("s00_healthy")]) != contribution_digest(
            [contribution("s00_healthy"), contribution("s01_retry")]
        )


class TestEdgePolicy:
    def test_fresh_runtime_snapshot(self):
        policy = build_edge_policy(
            {
                "edge_id": "e-1",
                "call_ref": "charge-payment",
                "timeout_ms": 700,
                "max_attempts": 3,
                "backoff_ms": 50,
                "retry_on": ["503"],
                "config_revision": "2",
                "freshness": "fresh",
            }
        )
        assert policy.per_attempt_timeout_ns.value == 700 * MS
        assert policy.per_attempt_timeout_ns.source == "runtime"
        assert policy.per_attempt_timeout_ns.config_revision == "2"
        assert policy.max_attempts.value == 3
        assert policy.backoff.kind == "fixed"
        assert policy.backoff.base_ns == 50 * MS
        assert policy.retry_on == ["503"]
        assert policy.policy_scope == "charge-payment"
        assert policy.effective_source == "fresh_runtime"

    def test_stale_runtime_becomes_cautious(self):
        policy = build_edge_policy(
            {"edge_id": "e-1", "timeout_ms": 700, "max_attempts": 3, "stale": True}
        )
        assert policy.per_attempt_timeout_ns.stale
        assert policy.per_attempt_timeout_ns.source == "unknown"
        assert policy.per_attempt_timeout_ns.value == 700 * MS
        assert policy.effective_source == "cautious_inference"

    def test_binding_conflict_is_not_fresh(self):
        policy = build_edge_policy({"edge_id": "e-1", "timeout_ms": 700, "binding_conflict": True})
        assert policy.effective_source == "cautious_inference"
        assert policy.per_attempt_timeout_ns.stale

    def test_declared_operand_survives_stale_snapshot(self):
        policy = build_edge_policy(
            {
                "edge_id": "e-1",
                "timeout_ms": {"value": 500, "source": "declared"},
                "max_attempts": 3,
                "stale": True,
            }
        )
        assert policy.per_attempt_timeout_ns.source == "declared"
        assert policy.effective_source == "declared"

    def test_external_budget_binds_explicitly(self):
        policy = build_edge_policy({"edge_id": "e-1", "budget_ns": 2_500_000_000, "stale": True})
        assert policy.per_attempt_timeout_ns.value == 2_500_000_000
        assert policy.per_attempt_timeout_ns.source == "declared"
        assert policy.effective_source == "declared"

    def test_missing_values_have_no_effective_source(self):
        policy = build_edge_policy({"edge_id": "e-1", "stale": True})
        assert policy.per_attempt_timeout_ns.value is None
        assert policy.max_attempts.value is None
        assert policy.effective_source is None

    def test_backoff_dict_passthrough(self):
        policy = build_edge_policy(
            {
                "edge_id": "e-1",
                "backoff": {
                    "kind": "exponential",
                    "base_ns": 1000,
                    "multiplier": 2,
                    "jitter": "full",
                },
            }
        )
        assert policy.backoff.kind == "exponential"
        assert policy.backoff.base_ns == 1000
        assert policy.backoff.multiplier == 2
        assert policy.backoff.jitter == "full"

    def test_edge_id_required(self):
        with pytest.raises(ValueError):
            build_edge_policy({"timeout_ms": 700})


class TestBuildModel:
    def test_known_idle_edge_keeps_null_stats(self):
        model = build_model([contribution("s00_healthy")], known_edges=["idle-edge"])
        observed = [e for e in model.edges.values() if e.stats is not None]
        assert len(observed) == 1
        assert "idle-edge" in model.edges
        idle = model.edges["idle-edge"]
        assert idle.stats is None
        assert idle.policy is None

    def test_policy_attaches_to_observed_edge(self):
        model = build_model(
            [contribution("s01_retry")],
            [
                {
                    "edge_id": next(iter(build_edge_stats([contribution("s01_retry")]))),
                    "timeout_ms": 700,
                    "max_attempts": 3,
                    "config_revision": "1",
                }
            ],
        )
        (edge,) = (e for e in model.edges.values() if e.stats is not None)
        assert edge.policy is not None
        assert edge.policy.effective_source == "fresh_runtime"

    def test_view_dict_roundtrips_through_json(self):
        model = build_model(
            [contribution("s00_healthy"), contribution("s01_retry")], known_edges=["idle"]
        )
        data = json.loads(json.dumps(model.to_dict()))
        assert data["normalization_version"]
        assert data["grouping_version"]
        assert data["dataset_ref"]
        assert "idle" in data["edges"]
        assert data["edges"]["idle"]["stats"] is None

    def test_versions_follow_contributions(self):
        model = build_model([contribution("s00_healthy")])
        assert model.grouping_version == contribution("s00_healthy").calls.grouping_version
        assert model.normalization_version


FRESH_POLICY = {
    "edge_id": "e-1",
    "call_ref": "charge",
    "timeout_ms": 700,
    "max_attempts": 3,
    "backoff_ms": 50,
    "config_revision": "2",
    "source": "runtime",
    "freshness": "fresh",
}


class TestTrustPrimitives:
    """Typed EdgePolicy must stay consistent with the flat trust primitives."""

    FRESH = FRESH_POLICY

    def test_pauses_ns_matches_backoff(self):
        policy = build_edge_policy(self.FRESH)
        assert policy.backoff.kind == "fixed"
        assert pauses_ns(3, policy.backoff.to_dict()) == 100 * MS
        assert pauses_ns(1, policy.backoff.to_dict()) == 0
        assert pauses_ns(3, {"kind": "unknown"}) is None

    def test_fresh_policy_primitives_agree(self):
        assert is_current_policy(self.FRESH)
        assert sourced_value(self.FRESH, "max_attempts") == 3
        policy = build_edge_policy(self.FRESH)
        assert policy.effective_source == "fresh_runtime"
        assert policy.per_attempt_timeout_ns.value == sourced_value(self.FRESH, "timeout_ms") * MS

    def test_stale_snapshot_distrusted_by_policy_but_plain_operand_reads(self):
        stale = {**self.FRESH, "stale": True}
        assert not is_current_policy(stale)
        # Ровно это расхождение зафиксировано: typed-вид понижает доверие,
        # плоское чтение по runtime/declared источнику сохраняется.
        assert sourced_value(stale, "max_attempts") == 3
        assert build_edge_policy(stale).effective_source == "cautious_inference"

    def test_revision_mismatch_hides_sourced_operand(self):
        conflicted = {
            **self.FRESH,
            "max_attempts": {"value": 3, "source": "runtime", "config_revision": "9"},
        }
        assert sourced_value(conflicted, "max_attempts") is None
        assert not is_current_policy({**self.FRESH, "binding_conflict": True})
