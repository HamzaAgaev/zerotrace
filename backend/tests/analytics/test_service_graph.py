from app.analytics_api.service_graph import summarize_graph
from tests.analytics.test_common_analytics_api import app, ingest, payload

def span(id,service,kind='server',parent=None,target=None,outcome='ok'):
    return dict(span_id=id,service=service,kind=kind,parent_span_id=parent,target=target,outcome=outcome,duration_ns='10000000',start_offset_ns='0')
def detail(*spans):
    return dict(spans=spans,quality_issues=[])

def test_graph_merges_missing_receivers_and_keeps_processing_separate():
    root=span('r','orders')
    call=span('c','orders','client','r','payment','error')
    server=span('s','payment',parent='c')
    result=summarize_graph([detail(root,call),detail(root,call,server)])
    assert len(result['nodes'])==2
    assert len(result['edges'])==1
    assert result['edges'][0]['count']==2
    assert result['edges'][0]['unpaired']==1
    assert result['edges'][0]['errors']==2
    assert next(s for s in result['services'] if s['name']=='payment')['errors']==0
    assert summarize_graph([detail(root,call)])['edges'][0]['to']['external']

def test_graph_keeps_all_traces_and_nodes():
    result=summarize_graph(detail(span(str(i),'service-'+str(i))) for i in range(150))
    assert result['trace_count']==150
    assert len(result['nodes'])==150

def test_graph_endpoint_period(app):
    ingest(app,'1'*32)
    client=app.test_client()
    assert client.get('/api/v1/service-graph?period_minutes=7').status_code==400
    result=payload(client.get('/api/v1/service-graph?period_minutes=30'))
    assert result['trace_count']==0
    # Include the actual fixture time rather than assuming wall-clock timestamps.
    start=int(payload(client.get('/api/v1/traces?include_findings=false'))['items'][0]['start_ns'])
    from unittest.mock import patch
    with patch('app.analytics_api.service_graph.time_ns',return_value=start+1_000_000_000):
        response=client.get('/api/v1/service-graph?period_minutes=1440')
        assert response.status_code==200
        assert payload(response)['trace_count']==1
        assert payload(response)['nodes']


def test_graph_returns_exact_trace_versions_and_quality_reasons():
    ref = dict(environment_id='shop', trace_id='a'*32, generation=2, revision=4)
    broken = dict(detail(span('child','orders',parent='missing')),
                  trace_ref=ref, quality_issues=['missing_parent','cross_clock_uncertain'])
    result = summarize_graph([detail(span('root','gateway')), broken])
    assert result['quality_count'] == 1
    assert result['quality_traces'] == [
        dict(trace_ref=ref, issues=['missing_parent','cross_clock_uncertain'])
    ]
