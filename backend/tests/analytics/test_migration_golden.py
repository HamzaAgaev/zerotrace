"""Golden harness for the C → core/model migration (step 0 of the plan in
docs/development/analytics-core-model-migration.md).

Фиксирует полный вывод rules.audit.run() на неизменных входах. Любой шаг
миграции обязан сохранить эти выводы побайтово. Обновление golden — только
осознанно: GOLDEN_UPDATE=1 pytest tests/analytics/test_migration_golden.py.

Два входа покрывают поверхность миграции:
- audit-baseline.json — общий прогон (R4 через плоские операнды, OBS_RETRIES);
- audit-policy-operands.json — операнды Sourced-dict'ами (сверка ревизий),
  R7 со списком retry_on, R8 с парой таймаутов. D1-дрейф покрыт юнит-тестами
  tests/analytics/test_rules_drift.py.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from rules import run
from tests.analytics.test_rules import EDGE, MS, group, model, policy

GOLDEN_DIR = Path(__file__).resolve().parents[2] / "fixtures" / "analytics" / "golden"
BASELINE = GOLDEN_DIR / "audit-baseline.json"
POLICY_OPERANDS = GOLDEN_DIR / "audit-policy-operands.json"


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


def build_policy_operand_scenario():
    """Sourced-dict operands with revision binding, R7 retry_on, R8 pair."""
    attempts, call = group()
    m = model(attempts, [call], dataset_ref="golden-policy-operands")
    p = policy(
        max_attempts={"value": 3, "source": "runtime", "config_revision": "12"},
        per_attempt_timeout_ns={
            "value": str(250 * MS),
            "source": "runtime",
            "config_revision": "12",
        },
        retry_on=["500"],
        upstream_per_attempt_timeout_ns=str(100 * MS),
    )
    return m, [p]


def _check(output: dict, golden: Path):
    if os.environ.get("GOLDEN_UPDATE"):
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(json.dumps(output, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
        pytest.skip("golden baseline rewritten; review the diff before committing")
    expected = json.loads(golden.read_text())
    assert output == expected


def test_golden_audit_output_is_stable():
    m, policies = build_scenario()
    _check(run(m, policies), BASELINE)


def test_golden_policy_operand_paths_are_stable():
    m, policies = build_policy_operand_scenario()
    _check(run(m, policies), POLICY_OPERANDS)
