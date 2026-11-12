"""Both creation selectors execute the same immutable model over HTTP and OTLP."""
import json
import socket
import threading
from pathlib import Path

import requests
from werkzeug.serving import make_server
from app.server.application import create_app
from .test_control_live import value, start_run


def test_server_file_and_upload_execute_identical_snapshot(tmp_path):
    source=json.loads(Path('stand/scenarios/example.json').read_text())
    # Multiple faults belong to routes, including routes not used by the entrypoint.
    fault={'error_rate':1,'http_status':503,'delay_ms':0,'failure_point':{'mode':'fixed','boundary_id':'before-first'}}
    worker=source['services']['worker-b']['routes']['GET /work']
    worker['response_status']=200
    worker['fault']=fault
    worker2=json.loads(json.dumps(worker))
    source['services']['worker-b']['routes']['GET /other']=worker2
    (tmp_path/'model.json').write_text(json.dumps(source))
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));port=listener.getsockname()[1]
    app=create_app({'database':str(tmp_path/'data.db'),'state_directory':str(tmp_path),
        'token_directory':str(tmp_path/'tokens'),'config_directory':str(tmp_path),
        'server_port':port,'environments':[
            {'environment_id':'empty','kind':'external','lab_scenario_file':'model.json'},
            {'environment_id':'sandbox','kind':'lab'}]})
    server=make_server('127.0.0.1',port,app,threaded=True)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{port}'
    try:
        envs=value(requests.get(base+'/api/v1/environments'))
        items=envs if isinstance(envs,list) else envs['items']
        assert items[0]['lab_scenario']['status']=='ready'
        uploaded=value(requests.post(base+'/api/v1/lab/scenarios/validate',json={'scenario':source}))
        configured=value(requests.post(base+'/api/v1/lab/scenarios/validate',json={'environment_id':'empty'}))
        assert uploaded==configured
        assert app.extensions['lab'].stand is None
        assert not app.extensions['lab'].processes
        results=[]
        for selector in ({'environment_id':'empty'},{'scenario_hash':uploaded['scenario_hash']}):
            stand=value(requests.post(base+'/api/v1/lab/stands',json=selector,timeout=30))
            assert stand['scenario_hash']==uploaded['scenario_hash']
            assert len(stand['faults'])==2
            result=start_run(base,stand['stand_id'],count=2,seed=0,concurrency=1)
            assert result['scenario_hash']==uploaded['scenario_hash']
            results.append(result)
            value(requests.delete(base+'/api/v1/lab/stands/'+stand['stand_id'],timeout=20))
        assert results[0]['run_id']!=results[1]['run_id']
        assert len(value(requests.get(base+'/api/v1/lab/runs'))['items'])==2
    finally:
        app.extensions['lab'].shutdown()
        server.shutdown();thread.join(3);server.server_close()
        app.extensions['records'].close();app.extensions['telemetry'].close()


def test_scenario_api_rejects_invalid_requests_without_processes(tmp_path):
    app=create_app({'database':str(tmp_path/'data.db'),'state_directory':str(tmp_path),
        'token_directory':str(tmp_path/'tokens'),'environments':[]})
    client=app.test_client()
    try:
        assert client.post('/api/v1/lab/scenarios/validate',data='{',content_type='application/json').status_code==400
        assert client.post('/api/v1/lab/scenarios/validate',data=' '*1048577,content_type='application/json').status_code==413
        for body in ({},{'scenario':{},'environment_id':'x'},{'path':'/tmp/x'}):
            assert client.post('/api/v1/lab/scenarios/validate',json=body).status_code==400
        for body in ({},{'scenario_hash':'x','environment_id':'x'},{'source':{}},{'path':'/tmp/x'}):
            assert client.post('/api/v1/lab/stands',json=body).status_code==400
        assert client.post('/api/v1/lab/stands',json={'scenario_hash':'unknown'}).status_code==404
        assert client.post('/api/v1/lab/scenarios/validate',json={'scenario':{}}).status_code==422
        assert not app.extensions['lab'].processes
    finally:
        app.extensions['lab'].shutdown()
        app.extensions['records'].close();app.extensions['telemetry'].close()
