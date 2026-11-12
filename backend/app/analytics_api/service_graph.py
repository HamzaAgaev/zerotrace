"""Service topology over every retained trace in a selected period."""
import json
from time import time_ns
from analytics_support import AnalyticsError
from app.data_api.adapter import make_graph_builder
from app.data_api.views import trace_detail
from .metrics import PERIODS


def _id(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def _stats(rows):
    values = sorted(float(row['duration_ns']) / 1e6 for row in rows)
    def percentile(p):
        if not values:
            return None
        pos = (len(values)-1)*p
        lo = int(pos)
        return values[lo] + (values[min(lo+1,len(values)-1)]-values[lo])*(pos-lo)
    return dict(count=len(rows), errors=sum(r['outcome']=='error' for r in rows),
                unknown=sum(r['outcome']=='unknown' for r in rows), median=percentile(.5), p95=percentile(.95))


def summarize_graph(details):
    services, edges, own = {}, {}, {}
    count = quality = 0
    quality_traces = []
    for detail in details:
        count += 1
        quality += bool(detail['quality_issues'])
        if detail['quality_issues']:
            quality_traces.append(dict(trace_ref=detail['trace_ref'], issues=detail['quality_issues']))
        spans = detail['spans']
        by_id = {s['span_id']: s for s in spans}
        parents = {s['parent_span_id'] for s in spans if s['kind']=='server'}
        children = {}
        for s in spans:
            children.setdefault(s['parent_span_id'],[]).append(s)
        for s in spans:
            if s['kind']=='server':
                services.setdefault(s['service'], []).append(s)
                start = int(s['start_offset_ns'])
                end = start + int(s['duration_ns'])
                intervals = sorted((max(start,int(c['start_offset_ns'])),min(end,int(c['start_offset_ns'])+int(c['duration_ns']))) for c in children.get(s['span_id'],[]))
                cursor, occupied = start, 0
                for a,b in intervals:
                    occupied += max(0,b-max(a,cursor))
                    cursor = max(cursor,b)
                own[s['service']] = own.get(s['service'],0)+max(0,end-start-occupied)/1e6
            parent = by_id.get(s['parent_span_id'])
            target, source, sample, unpaired = None, s['service'], s, False
            if s['kind']=='server' and parent and parent['service']!=s['service']:
                source, target = parent['service'], s['service']
                sample = parent if parent['kind']=='client' else s
            elif s['kind']=='client' and s.get('target') and s['span_id'] not in parents:
                target, unpaired = s['target'], True
            if target:
                group = edges.setdefault((source,target), dict(rows=[],unpaired=0))
                group['rows'].append(sample)
                group['unpaired'] += unpaired
    # Exact telemetry names only: never guess a service from a hostname or URL.
    known = set(services) | {source for source, _ in edges}
    def node(name):
        external = name not in known
        return dict(id=_id(['external' if external else 'service',name]), name=name, external=external)
    nodes = {name:node(name) for name in known}
    result = []
    for (source,target), group in sorted(edges.items()):
        a,b = node(source),node(target)
        nodes[target]=b
        result.append(dict(id=_id([a['id'],b['id']]),caller=source,callee=target,
                           **{'from':a,'to':b},**_stats(group['rows']), unpaired=group['unpaired'],traceIds=[]))
    return dict(services=[dict(name=name,**_stats(rows),own=own[name],traceIds=[]) for name,rows in sorted(services.items())],
                nodes=sorted(nodes.values(),key=lambda n:n['name']),edges=result,trace_count=count,quality_count=quality,quality_traces=quality_traces)


def service_graph(view, environment, minutes, *, now=None):
    if minutes not in PERIODS:
        raise AnalyticsError('invalid_period','Недопустимый период.',400)
    now = time_ns() if now is None else now
    cutoff = now-minutes*60_000_000_000
    entries = view.trace_list_entries(environment)
    refs = [ref for ref,e in entries.items() if cutoff <= (e.root_start_ns if e.root_start_ns is not None else e.start_ns) <= now]
    build = make_graph_builder(view.span_store,view.limits)
    missing = 0
    def details():
        nonlocal missing
        for ref in refs:
            spans = view.span_store.get_spans(ref)
            if spans is None:
                missing += 1
                continue
            yield trace_detail(build(spans,ref)).to_dict()
    result = summarize_graph(details())
    return dict(result, missing_count=missing,period_minutes=minutes,start_ns=str(cutoff),end_ns=str(now))
