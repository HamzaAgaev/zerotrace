"""P8 scorer. See README.md for assertion and independent measurement formats."""
from collections import Counter
from decimal import Decimal, InvalidOperation

from analytics_support import AnalyticsError

VERSION = "p8-score-v1.0.1"
SEVERITIES = ("low", "medium", "high")
ABSENT = object()


def _invalid(message):
    return AnalyticsError("invalid_eval", message, status=422)


def _number(value):
    if isinstance(value, bool):
        raise _invalid("Boolean is not a numeric operand.")
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise _invalid("Expected a numeric operand.") from None
    if not n.is_finite():
        raise _invalid("Operands must be finite.")
    return n


def _lookup(value, path):
    if not isinstance(path, str) or not path:
        raise _invalid("An assertion needs a non-empty field path.")
    if not isinstance(value, (dict, list)):
        return ABSENT
    for key in path.split("."):
        if isinstance(value, dict):
            value = value.get(key, ABSENT)
        elif isinstance(value, list) and key.isascii() and key.isdigit() and len(key) < 10 and int(key) < len(value):
            value = value[int(key)]
        else:
            return ABSENT
    return value


def _list(value, field):
    items = value.get(field, [])
    if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
        raise _invalid(f"{field} must be a list of objects.")
    return items


def _matches(finding, spec):
    if not isinstance(spec.get("rule_id"), str) or not spec["rule_id"]:
        raise _invalid("A labelled finding needs rule_id.")
    if finding.get("rule_id") != spec["rule_id"]:
        return False
    version = spec.get("rule_version")
    if version is not None and finding.get("rule_version", finding.get("version")) != version:
        return False
    return all(_lookup(finding, path) == value for path, value in spec.get("selector", {}).items())


def _matches_value(value, condition):
    if "equals" in condition:
        expected = condition["equals"]
        if "tolerance" in condition or "relative_tolerance" in condition:
            absolute = _number(condition.get("tolerance", 0))
            relative = _number(condition.get("relative_tolerance", 0))
            if absolute < 0 or relative < 0:
                raise _invalid("Tolerance must be non-negative.")
            try:
                return abs(_number(value) - _number(expected)) <= absolute + abs(_number(expected)) * relative
            except AnalyticsError:
                return False
        return value == expected and isinstance(value, bool) == isinstance(expected, bool)
    if "min" not in condition and "max" not in condition:
        raise _invalid("A value assertion needs equals, min, or max.")
    try:
        actual = _number(value)
    except AnalyticsError:
        return False
    return ("min" not in condition or actual >= _number(condition["min"])) and (
        "max" not in condition or actual <= _number(condition["max"]))


def _finding_checks(finding, spec):
    low, high = spec.get("severity_min", "low"), spec.get("severity_max", "high")
    failures = []
    if low not in SEVERITIES or high not in SEVERITIES or SEVERITIES.index(low) > SEVERITIES.index(high):
        raise _invalid("Invalid severity range.")
    severity = finding.get("severity")
    if "severity_min" in spec or "severity_max" in spec:
        if severity not in SEVERITIES or not SEVERITIES.index(low) <= SEVERITIES.index(severity) <= SEVERITIES.index(high):
            failures.append("severity")
    for check in _list(spec, "operands"):
        value = _lookup(finding, check.get("path"))
        if value is ABSENT or not _matches_value(value, check):
            failures.append(check["path"])
    return failures


def _evaluated_scope(evaluation, spec):
    if evaluation.get("rule_id") != spec["rule_id"]:
        return False
    if spec.get("rule_version") is not None and evaluation.get("rule_version") != spec["rule_version"]:
        return False
    edge = spec.get("selector", {}).get("edge_id")
    if edge is not None:
        rows = [r for r in _list(evaluation, "by_edge") if r.get("edge_id") == edge]
    else:
        rows = [evaluation]
    return any(r.get("status") == "evaluated" and type(r.get("eligible_count")) is int
               and r["eligible_count"] > 0 for r in rows)


def _projection_scores(actual, expected, assertions, blocked):
    predictions = {}
    for row in _list(actual, "projections"):
        key = row.get("case_id")
        if not isinstance(key, str) or key in predictions:
            raise _invalid("Each projection needs a distinct case_id.")
        predictions[key] = row
    errors, relative, rows, seen = [], [], [], set()
    for measured in _list(expected, "projection_measurements"):
        key = measured.get("case_id")
        if not isinstance(key, str) or key in seen:
            raise _invalid("Each measurement needs a distinct case_id.")
        seen.add(key)
        y = _number(measured.get("measured_ns"))
        if y < 0:
            raise _invalid("Measurement must be non-negative.")
        predicted = predictions.get(key, {})
        item = {"case_id": key, "measured_ns": str(y), "projected_ns": None,
                "source": measured.get("source"), "trace_ref": predicted.get("trace_ref")}
        status = predicted.get("status", "missing")
        if blocked:
            state, reason = "blocked", "prerequisites_not_met"
        elif status != "available" or predicted.get("projected_ns") is None:
            state, reason = "blocked", "projection_" + str(status)
        else:
            value = _number(predicted["projected_ns"])
            if value < 0:
                raise _invalid("Projection duration must be non-negative.")
            error = abs(value - y)
            errors.append(error)
            if y:
                relative.append(error / y)
            item.update(projected_ns=str(value), absolute_error_ns=str(error),
                        relative_error=float(error / y) if y else None)
            tol = measured.get("tolerance_ns")
            if tol is not None and _number(tol) < 0:
                raise _invalid("Measurement tolerance must be non-negative.")
            state = "failed" if tol is not None and error > _number(tol) else "passed"
            reason = "outside_tolerance" if state == "failed" else "compared"
        assertions.append({"kind": "projection", "case_id": key, "status": state, "reason": reason})
        rows.append(item)
    total = len(rows)
    return {"count": total, "available": len(errors), "abstained": total - len(errors),
            "availability": len(errors) / total if total else None,
            "mae_ns": str(sum(errors) / len(errors)) if errors else None,
            "mean_relative_error": float(sum(relative) / len(relative)) if relative else None,
            "relative_error_count": len(relative), "measurements": rows}


def score(actual_results, expected):
    """Compare already-produced outputs with independently authored expectations."""
    if not isinstance(actual_results, dict) or not isinstance(expected, dict):
        raise _invalid("Expected actual results and an independent expectation object.")
    actual = actual_results
    audit = actual.get("audit", actual)
    if not isinstance(audit, dict):
        raise _invalid("audit must be an object.")
    findings = _list(audit, "findings")
    rules = _list(audit, "rule_evaluations")
    unexpected_policy = expected.get("unexpected_policy", "review")
    if unexpected_policy not in {"review", "fail"}:
        raise _invalid("unexpected_policy must be review or fail.")
    required, supplied = expected.get("required_capabilities", []), actual.get("capabilities", [])
    if not all(isinstance(c, list) and all(isinstance(k, str) for k in c) for c in (required, supplied)):
        raise _invalid("Capabilities must be lists of names.")
    missing = sorted(set(required) - set(supplied))
    mode = expected.get("mode")
    if mode is not None and mode not in {"trace_only", "config_aware"}:
        raise _invalid("Expected trace_only or config_aware mode.")
    mode_mismatch = mode is not None and actual.get("mode") != mode
    mismatches = {field: {"expected": expected[field], "actual": actual[field]}
                  for field in ("scenario_id", "scenario_version", "build_ref", "seed")
                  if expected.get(field) is not None and actual.get(field) is not None
                  and expected[field] != actual[field]}
    blocked = bool(missing or mode_mismatch or mismatches)
    assertions, positive, forbidden = [], set(), set()
    if blocked:
        assertions.append({"kind": "prerequisites", "status": "blocked", "missing_capabilities": missing,
                           "expected_mode": mode, "actual_mode": actual.get("mode"),
                           "mismatched_inputs": mismatches})
    positive_hit = positive_miss = 0
    for kind in ("must_find", "must_not_find"):
        for spec in _list(expected, kind):
            if not isinstance(spec.get("rule_id"), str) or not spec["rule_id"]:
                raise _invalid("A finding assertion needs a rule ID.")
            if not isinstance(spec.get("selector", {}), dict):
                raise _invalid("Finding selector must be an object.")
            matches = [i for i, f in enumerate(findings) if _matches(f, spec)]
            candidates = [{"finding": findings[i], "failed_fields": _finding_checks(findings[i], spec)} for i in matches]
            usable = [i for i, row in zip(matches, candidates) if not row["failed_fields"]]
            evaluated = any(_evaluated_scope(r, spec) for r in rules)
            # A qualified forbidden finding proves a violation even on partial input.
            evidence = usable if kind == "must_not_find" else matches
            if blocked or (not evidence and not evaluated):
                state = "blocked"
            elif kind == "must_find":
                state = "passed" if usable else "failed"
                positive.update(matches)
                positive_hit += int(bool(matches))
                positive_miss += int(not matches)
            else:
                state = "failed" if usable else "passed"
                forbidden.update(usable)
            assertions.append({"kind": kind, "spec": spec, "status": state, "actual_evidence": candidates})
    for category in ("policy_assertions", "apply_assertions", "quality_assertions"):
        for condition in _list(expected, category):
            value = _lookup(actual, condition.get("path"))
            state = "blocked" if blocked or value is ABSENT else ("passed" if _matches_value(value, condition) else "failed")
            assertions.append({"kind": category, "spec": condition, "status": state,
                               "actual": None if value is ABSENT else value, "present": value is not ABSENT})

    if positive & forbidden:
        raise _invalid("A finding is labelled both required and forbidden.")
    unexpected = [f for i, f in enumerate(findings) if i not in positive and i not in forbidden]
    if unexpected and unexpected_policy == "fail":
        assertions.append({"kind": "unexpected", "status": "blocked" if blocked else "failed",
                           "count": len(unexpected)})
    projections = _projection_scores(actual, expected, assertions, blocked)
    counts = Counter(a["status"] for a in assertions)
    status = "failed" if counts["failed"] else ("blocked" if counts["blocked"] or not assertions else "passed")
    labelled = len(positive) + len(forbidden)
    return {"scorer_version": VERSION, "scenario_id": actual.get("scenario_id", expected.get("scenario_id")),
            "scenario_version": actual.get("scenario_version", expected.get("scenario_version")), "build_ref": actual.get("build_ref"),
            "seed": actual.get("seed"), "mode": actual.get("mode"), "status": status,
            "counts": {s: counts[s] for s in ("passed", "failed", "blocked")}, "assertions": assertions,
            "unlabelled_findings": unexpected, "unexpected_findings": unexpected,
            "trace_refs": actual.get("trace_refs", []), "artifacts": actual.get("artifacts", []),
            "load_summary": actual.get("load_summary"), "metrics": {
                "true_positives": len(positive), "false_positives": len(forbidden),
                "false_negatives": positive_miss, "unlabelled_count": len(unexpected),
                "precision": len(positive) / labelled if labelled else None,
                "recall": positive_hit / (positive_hit + positive_miss) if positive_hit + positive_miss else None,
                "projection": projections}}
