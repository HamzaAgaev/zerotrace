import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.control.scenarios import Scenarios
from app.control.store import ControlStore
from app.integrations.http_config import ProductError
from stand.scenario import normalized_scenario, scenario_digest

EXAMPLE=Path('stand/scenarios/example.json')
def test_file_and_upload_same_snapshot_and_reload(tmp_path):
    document=json.loads(EXAMPLE.read_text())
    (tmp_path/'scenario.json').write_text(json.dumps(document))
    registry=SimpleNamespace(environments=[{'environment_id':'external','name':'External','kind':'external','lab_scenario_file':'scenario.json'}, {'environment_id':'bad','lab_scenario_file':'missing.json'}, {'environment_id':'empty'}])
    records=ControlStore(tmp_path/'test.db')
    try:
        catalog=Scenarios({'config_directory':str(tmp_path)},registry,records)
        uploaded=catalog.validate({'scenario':document})
        assert catalog.environment('external')==uploaded
        assert catalog.bindings['bad']['status']=='invalid'
        assert catalog.bindings['empty']['status']=='unconfigured'
        assert 'lab_scenario_file' not in catalog.environments()[0]
        document['services']['api']['routes']['POST /run']['work']['delay_ms']=11
        (tmp_path/'scenario.json').write_text(json.dumps(document))
        assert catalog.environment('external')==uploaded
        reloaded=Scenarios({'config_directory':str(tmp_path)},registry,records)
        assert reloaded.environment('external')['scenario_hash']!=uploaded['scenario_hash']
        assert catalog.get(uploaded['scenario_hash'])==uploaded
        records.put('scenario',uploaded['scenario_hash'],{'corrupted':True})
        assert catalog.get(uploaded['scenario_hash'])==uploaded
    finally:records.close()

def test_canonical_numbers_defaults_and_faults():
    raw=json.loads(EXAMPLE.read_text());other=copy.deepcopy(raw)
    route=other['services']['api']['routes']['POST /run']
    other['scenario_revision']=1.0
    route['response_status']=200.0
    route['tail_ms']=0.0;route['fault']=None;route['work']['delay_ms']=10.0
    route['steps'][0]['delay_ms']=0
    assert scenario_digest(normalized_scenario(raw))==scenario_digest(normalized_scenario(other))
    route['fault']={'error_rate':1,'http_status':503,'delay_ms':0,'failure_point':{'mode':'fixed','boundary_id':'before-first'}}
    assert scenario_digest(normalized_scenario(raw))!=scenario_digest(normalized_scenario(other))

@pytest.mark.parametrize('field,value',[('observed_status',200),('response_status',True),('response_status',199),('tail_ms',float('nan'))])
def test_public_validation_rejects_invalid_fields_with_pointer(tmp_path,field,value):
    records=ControlStore(tmp_path/'test.db')
    try:
        catalog=Scenarios({},SimpleNamespace(environments=[]),records)
        raw=json.loads(EXAMPLE.read_text());raw['services']['api']['routes']['POST /run'][field]=value
        with pytest.raises(ProductError) as caught:catalog.save(raw)
        assert caught.value.status==422
        assert caught.value.details['errors']
        if field!='tail_ms':assert caught.value.details['errors'][0]['path'].endswith('/'+field)
    finally:records.close()
