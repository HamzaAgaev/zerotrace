"""Golden harness for the C → core/model migration (step 0 of the plan in
docs/development/analytics-core-model-migration.md).

Фиксирует полный вывод rules.audit.run() на неизменном входе. Любой шаг
миграции обязан сохранить этот вывод побайтово. Обновление golden — только
осознанно: GOLDEN_UPDATE=1 pytest tests/analytics/test_migration_golden.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rules import run
from tests.analytics.test_rules import EDGE, group, model, policy

GOLDEN = (
    Path(__file__).resolve().parents[2]
    / "fixtures"
    / "analytics"
    / "golden"
    / "audit-baseline.json"
)


def build_scenario():
    """Three edges: confirmed retry, single call, nested pair."""
    attempts, call = group()
    single_a, single_call = group(cid="notify", n=1, edge="e-notify")
    nested_a, nested_call = group(cid="child", n=2, edge=EDGE, parent="root-1")
    root_a, root_call = group(cid="root", n=1, edge="e-root")
    m = model(
        attempts + single_a + nested_a + root_a,
        [call, single_call, nested_call, root_call],
        dataset_ref="golden-baseline",
    )
    policies = [policy(), policy(edge_id="e-notify"), policy(edge_id="e-root", max_attempts=1)]
    return m, policies


def test_golden_audit_output_is_stable():
    m, policies = build_scenario()
    output = run(m, policies)
    if os.environ.get("GOLDEN_UPDATE"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(json.dumps(output, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        pytest.skip("golden baseline rewritten; review the diff before committing")
    expected = json.loads(GOLDEN.read_text())
    assert output == expected
