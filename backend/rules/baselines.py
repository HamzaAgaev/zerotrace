"""Explicit reference baselines, kept separate from selected audit samples."""
from datetime import UTC, datetime

from analytics_support import percentile_ns

from .common import asref, digest, integer, refkey

DIMENSIONS = ("edge_id", "environment_id", "caller_version", "callee_version", "workload_id")


def timestamp(value):
    try:
        ts = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return ts.astimezone(UTC) if ts.tzinfo else None
    except (ValueError, TypeError):
        return None


def usable_sampling(sampling):
    return isinstance(sampling, dict) and sampling.get("mode") == "all_received" and sampling.get("known_losses") is False


def build_reference(segment, attempts, sampling, *, version, created_at, valid_until):
    if (not all(segment.get(k) for k in DIMENSIONS) or not usable_sampling(sampling)
            or not timestamp(created_at) or not timestamp(valid_until)
            or timestamp(created_at) >= timestamp(valid_until) or not version):
        raise ValueError("baseline requires a scoped unselected sample and validity interval")
    durations, errors, seen, refs = [], 0, set(), set()
    revisions = {}
    for a in attempts:
        key = refkey(a["trace_ref"])
        if revisions.setdefault(key[:3], key[3]) != key[3]:
            raise ValueError("baseline contains multiple revisions of the same trace")
        if (key[0] != segment["environment_id"] or any(a.get(k) != segment[k] for k in
                ("edge_id", "caller_version", "callee_version", "workload_id"))):
            raise ValueError("mixed or unknown baseline segments")
        slot = key, a["attempt_id"]
        if slot in seen:
            raise ValueError("duplicate baseline observation")
        seen.add(slot)
        refs.add(key)
        if a.get("outcome") == "ok":
            d = integer(a.get("duration_ns"))
            if d is None:
                raise ValueError("unknown successful duration")
            durations.append(d)
        elif a.get("outcome") in {"timeout", "fast_fail", "http_error", "grpc_error", "transport_error", "error_unknown", "error"}:
            errors += 1
        else:
            raise ValueError("unknown reference outcome")
    if not durations:
        raise ValueError("healthy reference requires successful durations")
    out = dict(segment, version=version, created_at=created_at, valid_until=valid_until,
               source_window_refs=[asref(k) for k in sorted(refs)], sampling=sampling, state="reference",
               samples=len(seen), success_samples=len(durations), error_ratio=errors / len(seen),
               success_min_ns=str(min(durations)), success_max_ns=str(max(durations)),
               success_p50_ns=str(percentile_ns(durations, .5)), success_p95_ns=str(percentile_ns(durations, .95)),
               success_p99_ns=str(percentile_ns(durations, .99)))
    return {**out, "baseline_id": digest(out)}


def reference_for(baselines, segment, as_of, minimum):
    when = timestamp(as_of)
    if not when:
        return None
    matches = []
    for row in baselines:
        if not all(segment.get(k) and row.get(k) == segment[k] for k in DIMENSIONS):
            continue
        start, end = timestamp(row.get("created_at")), timestamp(row.get("valid_until"))
        if (row.get("state") == "reference" and row.get("version") and row.get("baseline_id")
                and usable_sampling(row.get("sampling")) and start and end and start <= when < end
                and (integer(row.get("success_samples")) or 0) >= minimum and integer(row.get("success_p95_ns"))):
            matches.append(row)
    return matches[0] if len(matches) == 1 else None
