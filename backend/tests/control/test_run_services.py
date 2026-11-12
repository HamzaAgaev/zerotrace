from contextlib import contextmanager
from types import SimpleNamespace
from app.control.run_services import service_results


def test_all_audit_rules_use_full_occurrence_index_and_frozen_configs(monkeypatch):
    refs=[dict(environment_id='sandbox',trace_id=f'{i:032x}',generation=2,revision=7) for i in range(12)]
    findings=[dict(occurrence_id=rule,rule_id=rule,exemplars=refs[:5]) for rule in ['R1','R3','R6','R8']]
    result=dict(findings=findings,rule_evaluations=[],trace_findings=[dict(trace_ref=ref,occurrence_ids=['R1','R3','R6','R8']) for ref in refs])
    monkeypatch.setattr('app.control.run_services.audit_view',lambda value,**kw:value)
    configs={'orders':{'revision':'old'}}
    @contextmanager
    def configured(env,config):
        assert env=='sandbox' and config is configs
        yield SimpleNamespace(audit=lambda env:result)
    @contextmanager
    def read():
        def restrict(env,selected):
            assert selected==refs and env=='sandbox'
            return 12,1
        yield SimpleNamespace(restrict_audit_to_traces=restrict)
    analytics=SimpleNamespace(configured=configured,source=SimpleNamespace(read=read))
    value=service_results(analytics,dict(environment_id='sandbox',config_snapshots=configs,trace_collection={'trace_refs':refs}))
    assert value['missing_traces']==1
    assert {f['rule_id'] for f in value['findings']}=={'R1','R3','R6','R8'}
    assert all(f['trace_refs']==refs for f in value['findings'])
