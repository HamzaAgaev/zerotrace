import copy
import threading
from pathlib import Path
import pytest
from stand.scenario import load_scenario, validate_scenario, ScenarioError
from app.control.laboratory import Laboratory
from app.integrations.http_config import ProductError


def example():
    return load_scenario(Path(__file__).resolve().parents[3] / 'stand/scenarios/example.json')


def test_explicit_graph_preserves_parallel_steps_repeated_calls_and_policy():
    source = example()
    result = validate_scenario(source, explicit=True)
    assert result == source
    route = result['services']['api']['routes']['POST /run']
    assert route['steps'][0]['parallel'] == ['first', 'second']
    assert route['steps'][1]['call'] == 'first'
    assert route['calls']['second']['required'] is False
    assert route['calls']['second']['max_attempts'] == 2
    result['services'].clear()
    assert source['services']


@pytest.mark.parametrize('change', ['missing_target','cycle','unknown_field','bad_step','missing_policy','unused_call','bad_entry'])
def test_invalid_graph_rejected(change):
    source=example()
    route=source['services']['api']['routes']['POST /run']
    if change=='missing_target': route['calls']['first']['target']['route']='/missing'
    if change=='cycle': route['calls']['first']['target']={'service_id':'api','method':'POST','route':'/run'}
    if change=='unknown_field': route['probability']=.5
    if change=='bad_step': route['steps'][0]=42
    if change=='missing_policy': del route['calls']['first']['max_attempts']
    if change=='unused_call': route['steps']=[]
    if change=='bad_entry': source['entrypoint']['route']=[]
    with pytest.raises(ScenarioError): validate_scenario(source, explicit=True)


def test_trace_generation_disabled_before_any_process_launch():
    lab=Laboratory.__new__(Laboratory)
    lab.lock=threading.RLock()
    lab.stand=None
    with pytest.raises(ProductError) as error: lab.create({'source':{'trace_id':'abc'}})
    assert error.value.code=='unsupported_stand_source'


def test_explicit_scenario_executes_parallel_calls_retries_and_sequential_repeat(tmp_path):
    import json
    from collections import Counter
    import requests
    from flask import request
    from werkzeug.serving import make_server
    from stand.service import create_app
    scenario=example()
    path=tmp_path/'scenario.json'
    path.write_text(json.dumps(scenario))
    endpoints={'reserved':'http://127.0.0.1'}
    servers=[]
    events=[]
    identities=[]
    barrier=threading.Barrier(2)
    lock=threading.Lock()
    for name in scenario['services']:
        app=create_app(path,name,tmp_path/'state',endpoints)
        def before(name=name):
            with lock:
                first=not any(e[0]==name for e in events)
                events.append((name,request.headers.get('X-ZeroTrace-Attempt-Index')))
                identities.append((name,request.headers.get('X-ZeroTrace-Logical-Call-Id')))
            if name in {'worker-a','worker-b'} and first:
                barrier.wait(timeout=3)
        app.before_request(before)
        server=make_server('127.0.0.1',0,app,threaded=True)
        endpoints[name]=f'http://127.0.0.1:{server.server_port}'
        servers.append(server)
    threads=[threading.Thread(target=server.serve_forever,daemon=True) for server in servers]
    for thread in threads:thread.start()
    try:
        response=requests.post(endpoints['api']+'/run',timeout=10)
        assert response.status_code==200
        assert Counter(name for name,_ in events)=={'api':1,'worker-a':2,'worker-b':2}
        assert [attempt for name,attempt in events if name=='worker-b']==['1','2']
        assert events[-1][0]=='worker-a'
        independent=[identity for name,identity in identities if name=='worker-a']
        attempts=[identity for name,identity in identities if name=='worker-b']
        assert all(independent) and len(set(independent))==2
        assert all(attempts) and len(set(attempts))==1
    finally:
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join(2)
