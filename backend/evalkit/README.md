# P8 scoring

Call `evalkit.score(actual_results, expected)` after the analyzer has finished. The control component owns the scenario and its sealed labels. Pass the audit, capability names, provenance, policy readback, operation outcome and quality observations to the scorer.

A small pair of inputs for evaluating R4:

```json
{
  "scenario_id": "s01_budget",
  "mode": "config_aware",
  "capabilities": ["runtime_config"],
  "audit": {
    "findings": [{"rule_id": "R4", "rule_version": "1", "edge_id": "orders-payment", "severity": "high", "evidence": {"required_ns": "850000000"}}],
    "rule_evaluations": [{"rule_id": "R4", "status": "evaluated", "eligible_count": 30}]
  }
}
```

```json
{
  "scenario_id": "s01_budget",
  "mode": "config_aware",
  "required_capabilities": ["runtime_config"],
  "must_find": [
    {"rule_id": "R4", "rule_version": "1", "selector": {"edge_id": "orders-payment"}, "severity_min": "medium",
     "operands": [{"path": "evidence.required_ns", "equals": "850000000", "tolerance": 0}]}
  ],
  "unexpected_policy": "review"
}
```

- `must_find` and `must_not_find` select a rule/version and exact optional fields (dotted paths). Optional severity bounds and operands test the evidence. Compare operands with `equals`, optional absolute/relative tolerances, or inclusive `min`/`max` bounds. Missing telemetry or unsupported rules block negative assertions. A completed negative assertion requires a positive integer eligible count for the evaluated rule. An edge selector requires coverage for that same edge in `by_edge`.
- `policy_assertions`, `apply_assertions` and `quality_assertions` use the same value comparisons. Their paths start at the actual results root, e.g. `{"path":"apply.status","equals":"conflict"}`. Missing values are recorded separately from explicit nulls.
- Precision counts unique returned findings matching required and forbidden labels. Recall counts mandatory selectors with an observed match among evaluable selectors. Numerical and severity mismatches independently fail their assertions. Unlabelled findings are listed for review; `unexpected_policy: fail` also fails the run when they are present. Missing denominators are null.
- Include `case_id`, `status` and `projected_ns` in each entry in `actual_results.projections`. Matching `expected.projection_measurements` entries include `case_id`, independently acquired `measured_ns`, provenance in `source`, and optional `tolerance_ns`. The projection metrics contain MAE on available comparisons, mean absolute relative error on nonzero measurements, availability and abstentions. Durations are nanoseconds. Synthetic control cases and independent measurements can be scored separately.
- Empty assertion sets or missing prerequisites yield `blocked`. A mandatory assertion failure yields `failed`. Record scenario/build/seed references, trace references, load summary and artifacts in the actual result. Each scenario ID, scenario version, build or seed named in the expectation must be present and equal in the actual result. Missing or conflicting values block the score and appear in `mismatched_inputs`.
