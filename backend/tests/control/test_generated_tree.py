from dataclasses import replace
import json
import threading
import time
from collections import Counter

import pytest
import requests
from flask import request
from werkzeug.serving import make_server
from contracts import IngestLimits, NormalizedSpan, TraceRef
from core import build_graph
from app.control.generator import scenario_from_graph
from app.integrations.http_config import ProductError
from stand.service import create_app
from stand.scenario import load_scenario


def example():
    spans = []
    def span(i, parent, service, kind, start, duration, op, status=200, target=None, **kw):
        spans.append(NormalizedSpan(trace_id='ab'*16, span_id=f'{i:016x}',
            parent_span_id=f'{parent:016x}' if parent else None, service=service,
            kind=kind, name=op, operation=op, start_ns=int(start*1e6), end_ns=int((start+duration)*1e6),
            duration_ns=int(duration*1e6), clock_domain='host', otel_status='error' if status>=400 else 'ok',
            http_status=status, target=target, field_coverage={}, diagnostics=[], **kw))
    span(1,None,'garage','server',0,500,'POST /repair')
    span(2,1,'garage','client',10,180,'POST /reserve',503,'parts',logical_call_id='reserve',attempt_index=1)
    span(3,2,'parts','server',10,180,'POST /reserve',503)
    span(4,1,'garage','client',20,230,'GET /estimate',200,'quotes')
    span(5,4,'quotes','server',20,230,'GET /estimate')
    span(6,3,'parts','client',30,100,'GET /first',200,'stock')
    span(7,6,'stock','server',30,100,'GET /first')
    span(8,1,'garage','client',270,150,'POST /reserve',200,'parts',logical_call_id='reserve',attempt_index=2)
    span(9,8,'parts','server',270,150,'POST /reserve')
    span(10,9,'parts','client',280,90,'GET /second',200,'stock')
    span(11,10,'stock','server',280,90,'GET /second')
    span(12,1,'garage','client',440,30,'GET /receipt',200,'quotes')
    span(13,12,'quotes','server',440,30,'GET /receipt')
    span(14,1,'garage','internal',475,10,'serialize')
    ref=TraceRef('shop','ab'*16,1,1)
    return build_graph(spans,ref,0,500_000_000,IngestLimits())


def test_all_branches_and_attempt_subtrees_are_preserved(tmp_path):
    graph=example()
    scenario,origins=scenario_from_graph(graph)
    path=tmp_path/'scenario.json';path.write_text(json.dumps(scenario))
    load_scenario(path)
    routes=[r for s in scenario['services'].values() for r in s['routes'].values()]
    assert len(routes)==7
    assert sum(len(r['calls']) for r in routes)==6
    assert {o['source_span_id'] for o in origins}=={s.span_id for s in graph.spans}
    root=scenario['services'][scenario['entrypoint']['service_id']]['routes']['POST /repair']
    assert [len(s.get('parallel',[s.get('call')])) for s in root['steps']]==[2,1,1]
    assert root['steps'][0]['offsets_ms']['call-0000000000000004']==10
    parts=next(s for name,s in scenario['services'].items() if name.endswith('-parts'))
    assert len(parts['routes'])==2
    assert sorted(r['response_status'] for r in parts['routes'].values())==[200,503]
    assert all(len(r['calls'])==1 for r in parts['routes'].values())


def test_generated_tree_executes_every_attempt_over_real_http(tmp_path):
    scenario,_=scenario_from_graph(example())
    path=tmp_path/'scenario.json';path.write_text(json.dumps(scenario))
    endpoints={'reserved': 'http://127.0.0.1'}; servers=[]; events=[]; lock=threading.Lock()
    for name in scenario['services']:
        app=create_app(path,name,tmp_path/'state',endpoints)
        # create_app keeps the provided nonempty mapping; populate after binding.
        def before(name=name):
            with lock:events.append(('start',name,request.path,time.monotonic(),request.headers.get('X-ZeroTrace-Attempt-Index')))
        def after(response,name=name):
            with lock:events.append(('end',name,request.path,time.monotonic(),response.status_code))
            return response
        app.before_request(before);app.after_request(after)
        server=make_server('127.0.0.1',0,app,threaded=True)
        endpoints[name]=f'http://127.0.0.1:{server.server_port}'
        servers.append(server)
    threads=[threading.Thread(target=s.serve_forever,daemon=True) for s in servers]
    for t in threads:t.start()
    try:
        root=scenario['entrypoint'];method,path=root['route'].split(' ',1)
        response=requests.request(method,endpoints[root['service_id']]+path,timeout=10)
        assert response.status_code==200
        starts=[e for e in events if e[0]=='start']
        assert len(starts)==7,events
        assert Counter(e[1].split('-',2)[2] for e in starts)=={'garage':1,'parts':2,'quotes':2,'stock':2}
        first_parts=next(e for e in starts if e[1].endswith('-parts') and e[2]=='/reserve')
        first_end=next(e for e in events if e[0]=='end' and e[1:3]==first_parts[1:3])
        estimate=next(e for e in starts if e[2]=='/estimate')
        assert first_parts[3] < estimate[3] < first_end[3],events
        second=next(e for e in starts if e[1].endswith('-parts') and e[2]!='/reserve')
        assert second[3]>first_end[3]
        assert {e[2] for e in starts if e[1].endswith('-stock')}=={'/first','/second'}
        assert [e[4] for e in starts if e[1].endswith('-parts')]==['1','2']
    finally:
        for server in servers:server.shutdown();server.server_close()
        for t in threads:t.join(2)


def test_missing_parent_refuses_instead_of_dropping_branch():
    graph=example();graph.spans[1]=replace(graph.spans[1],parent_span_id='ff'*8)
    graph=build_graph(graph.spans,graph.trace_ref,0,1,IngestLimits())
    with pytest.raises(ProductError):scenario_from_graph(graph)
