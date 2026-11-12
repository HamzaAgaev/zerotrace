"""Validated, immutable scenario snapshots shared by upload and server config."""
from __future__ import annotations
import json
from pathlib import Path
from stand.scenario import normalized_scenario, scenario_digest, ScenarioError
from app.integrations.http_config import ProductError

MAX_SCENARIO_BYTES = 1024 * 1024

class Scenarios:
    def __init__(self, config, registry, records):
        self.registry, self.records = registry, records
        self.bindings = {}
        directory = Path(config.get('config_directory', '.'))
        for env in getattr(registry, 'environments', config.get('environments', [])):
            identifier = env['environment_id']
            filename = env.get('lab_scenario_file')
            if not filename:
                self.bindings[identifier] = {'status':'unconfigured'}
                continue
            try:
                path = Path(filename)
                if not path.is_absolute(): path = directory / path
                if path.stat().st_size > MAX_SCENARIO_BYTES:
                    raise ValueError('Файл сценария больше 1 МБ.')
                snapshot = self.save(json.loads(path.read_text(encoding='utf-8')))
                self.bindings[identifier] = {'status':'ready', **self.descriptor(snapshot)}
            except (OSError, ValueError, ProductError) as error:
                message = str(error) if isinstance(error, ProductError) else 'Не удалось прочитать корректный JSON сценария. Проверьте серверную конфигурацию.'
                self.bindings[identifier] = {'status':'invalid', 'error':message}
            records.put('scenario_binding', identifier, self.bindings[identifier])

    @staticmethod
    def descriptor(snapshot):
        document = snapshot['scenario']
        return {key:snapshot[key] for key in ('scenario_hash','scenario_id','scenario_revision','service_count','route_count')}

    def save(self, raw):
        try:
            if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > MAX_SCENARIO_BYTES:
                raise ProductError(413, 'scenario_too_large', 'Сценарий должен быть не больше 1 МБ.')
            scenario = normalized_scenario(raw)
        except (ScenarioError, ValueError, TypeError, RecursionError) as error:
            raise ProductError(422, 'invalid_scenario', str(error), {'errors':[{'path':getattr(error,'path',''), 'code':'invalid_scenario','message':str(error)}]}) from error
        snapshot = {'scenario_hash':scenario_digest(scenario), 'scenario':scenario,
                    'scenario_id':scenario['scenario_id'], 'scenario_revision':scenario['scenario_revision'],
                    'service_count':len(scenario['services']),
                    'route_count':sum(len(s['routes']) for s in scenario['services'].values())}
        self.records.put('scenario', snapshot['scenario_hash'], snapshot)
        return self.records.get('scenario', snapshot['scenario_hash'])

    def environment(self, identifier):
        if not isinstance(identifier,str) or identifier not in self.bindings:
            raise ProductError(404,'environment_not_found','Окружение не найдено.')
        binding = self.bindings[identifier]
        if binding['status'] != 'ready':
            raise ProductError(422,'scenario_unavailable',binding.get('error','Для окружения не настроен сценарий стенда.'))
        return self.get(binding['scenario_hash'])

    def get(self, hash):
        if not isinstance(hash,str) or not (snapshot := self.records.get('scenario',hash)):
            raise ProductError(404,'scenario_not_found','Проверенный сценарий не найден. Загрузите JSON ещё раз.')
        return snapshot

    def validate(self, body):
        if not isinstance(body,dict) or len(body)!=1 or not set(body) <= {'scenario','environment_id'}:
            raise ProductError(400,'invalid_scenario_selector','Укажите scenario или environment_id.')
        return self.save(body['scenario']) if 'scenario' in body else self.environment(body['environment_id'])

    def environments(self):
        return [{k:v for k,v in env.items() if k!='lab_scenario_file'} | {'lab_scenario':self.bindings[env['environment_id']]} for env in self.registry.environments]
