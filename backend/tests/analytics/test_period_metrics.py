from dataclasses import replace
from urllib.parse import urlencode
import time
import pytest
from app.data_api.views import trace_list_entry
from app.analytics_api.metrics import period_metrics
from contracts import TraceRef
from repository.store import TraceListSpan
from tests.analytics.test_common_analytics_api import app, ingest, payload

NOW = 1_800_000_000_000_000_000

def span(id, parent, start, end, service='orders', kind='server', status=200):
    return TraceListSpan(id, start, str(end), 'clock', parent is None, service, 'POST /orders', kind, 'UNSET', status, True, parent)

def test_metrics_root_latency_and_own_time_with_overlapping_children():
    entry=trace_list_entry(TraceRef('shop','1'*32,1,1),[
        span('1',None,NOW-1000,NOW-900),
        span('2','1',NOW-990,NOW-950,kind='client'),
        span('3','1',NOW-970,NOW-920,kind='client'),
        span('4','2',NOW-990,NOW-800,service='payment'),
    ])
    assert entry.duration_ns==200
    class View:
        def trace_list_entries(self,env): return {entry.trace_ref:entry}
    result=period_metrics(View(),'shop',30,now=NOW)
    assert result['summary']['median_ns']=='100'
    assert result['services'][0]['own_ns']=='30'
    assert result['summary']['count']==1

def test_period_metrics_all_traces_and_unknown_denominator():
    base=trace_list_entry(TraceRef('shop','1'*32,1,1),[span('1',None,NOW-1000,NOW-900)])
    entries=[replace(base,trace_ref=TraceRef('shop',f'{i:032x}',1,1),outcome='ok' if i<100 else 'unknown') for i in range(150)]
    entries.append(replace(base,root_start_ns=NOW-31*60_000_000_000,operations=()))
    class View:
        def trace_list_entries(self,env): return {i:e for i,e in enumerate(entries)}
    result=period_metrics(View(),'shop',30,now=NOW)
    assert result['summary']['count']==150
    assert result['summary']['unknown']==50
    assert result['summary']['success_rate']==1
    assert period_metrics(View(),'shop',60,now=NOW)['summary']['count']==151
    with pytest.raises(Exception): period_metrics(View(),'shop',7,now=NOW)

def test_product_period_endpoint_and_filters(app):
    refs=ingest(app,*(f'{i:032x}' for i in range(1,9)))
    client=app.test_client()
    empty=payload(client.get('/api/v1/metrics?period_minutes=30'))
    assert empty['summary']['count']==0  # old fixture timestamps
    assert empty['summary']['success_rate'] is None
    assert client.get('/api/v1/metrics?period_minutes=7').status_code==400
    all_rows=payload(client.get('/api/v1/traces?sort=recent&include_findings=false'))
    assert 'orders' in all_rows['services']
    params={'sort':'recent','include_findings':'false','services':'["absent","orders"]','methods':'["GET"]','statuses':'["ok","error"]','rules':'["OBS_RETRIES"]'}
    found=payload(client.get('/api/v1/traces?'+urlencode(params)))
    assert found['total']==8
    assert refs[-1] in [r['trace_ref'] for r in found['items']] # beyond five audit exemplars
    params['services']='["absent"]'
    assert payload(client.get('/api/v1/traces?'+urlencode(params)))['total']==0
    params['services']='not-json'
    assert client.get('/api/v1/traces?'+urlencode(params)).status_code==400

def test_deferred_rule_filter_preserves_query(app):
    ingest(app,'1'*32)
    client=app.test_client()
    params={'defer':'true','rules':'["OBS_RETRIES"]','include_findings':'false','limit':'1'}
    response=client.get('/api/v1/traces?'+urlencode(params))
    for _ in range(100):
        if response.status_code!=202: break
        params['job_id']=response.json['payload']['job_id']
        time.sleep(.01)
        response=client.get('/api/v1/traces?'+urlencode(params))
    assert payload(response)['total']==1


def test_nested_service_search(app):
    from contracts import SourceBinding
    from ingest.normalize import NormalizedBatch
    ref,=ingest(app,'1'*32)
    store=app.extensions['telemetry']
    meta=store.resolve_trace('shop',ref['trace_id'])
    root=next(s for s in store.get_spans(meta.trace_ref) if s.parent_span_id is None)
    child=replace(root,span_id='e'*16,parent_span_id=root.span_id,service='inventory',operation='GET /stock')
    store.ingest(NormalizedBatch([child]),SourceBinding('src','p:shop','shop'),2000)
    client=app.test_client()
    result=payload(client.get('/api/v1/traces?'+urlencode({'sort':'recent','include_findings':'false','services':'["inventory"]'})))
    assert result['total']==1
    assert result['items'][0]['service']=='orders'
    assert 'inventory' in result['services']
