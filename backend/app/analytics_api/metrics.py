"""Period metrics over all current revisions, without running audit rules."""
from time import time_ns
from analytics_support import AnalyticsError, percentile_ns

PERIODS = {5, 30, 60, 180, 1440}


def period_metrics(view, environment_id, minutes, *, now=None):
    if minutes not in PERIODS:
        raise AnalyticsError('invalid_period', 'Недопустимый период.', 400)
    now = time_ns() if now is None else now
    cutoff = now - minutes * 60_000_000_000
    entries = view.trace_list_entries(environment_id).values()
    traces, groups = [], {}
    for entry in entries:
        start = entry.root_start_ns if entry.root_start_ns is not None else entry.start_ns
        if cutoff <= start <= now and start > 0:
            traces.append(entry)
        for operation in entry.operations:
            if not cutoff <= operation['start_ns'] <= now or operation['start_ns'] <= 0:
                continue
            groups.setdefault((operation['service'], operation['endpoint']), []).append(operation)

    def stats(rows):
        durations = [row['duration_ns'] for row in rows if row['duration_ns'] is not None]
        ok = sum(row['outcome'] == 'ok' for row in rows)
        errors = sum(row['outcome'] == 'error' for row in rows)
        return dict(count=len(rows), errors=errors, unknown=len(rows)-ok-errors,
                    success_rate=ok/(ok+errors) if ok+errors else None,
                    median_ns=str(percentile_ns(durations, .5)) if durations else None,
                    p95_ns=str(percentile_ns(durations, .95)) if durations else None,
                    own_ns=str(sum(row.get('own_ns', 0) for row in rows)))
    services = {}
    endpoints = []
    for (service, endpoint), rows in sorted(groups.items()):
        endpoints.append(dict(service=service, endpoint=endpoint, **stats(rows)))
        services.setdefault(service, []).extend(rows)
    return dict(period_minutes=minutes, start_ns=str(cutoff), end_ns=str(now),
                summary=stats([dict(duration_ns=e.root_duration_ns, outcome=e.outcome) for e in traces]),
                services=[dict(service=name, **stats(rows)) for name, rows in sorted(services.items())],
                endpoints=endpoints)
