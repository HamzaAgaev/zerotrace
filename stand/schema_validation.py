"""Validation of the JSON Schema subset used by our published v1 contract.

Structural validation uses the same schema file delivered to users. Cross-route
references, cycles and policy consistency are checked by scenario.py.
"""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

class SchemaError(ValueError):
    def __init__(self,path,message):
        self.path=path
        super().__init__(f'{path or "/"}: {message}')

@lru_cache(maxsize=1)
def schema():
    return json.loads((Path(__file__).parent/'scenarios/scenario-v1.schema.json').read_text())

def check(value, rule=None, path=''):
    rule=schema() if rule is None else rule
    if '$ref' in rule: return check(value,schema()['$defs'][rule['$ref'].split('/')[-1]],path)
    def fail(message):raise SchemaError(path,message)
    for combinator in ('oneOf','anyOf'):
        if combinator in rule:
            successes=0
            for option in rule[combinator]:
                try:check(value,option,path);successes+=1
                except SchemaError:pass
            if not successes or combinator=='oneOf' and successes!=1:fail('Неверный формат значения.')
            return
    if 'const' in rule and (type(value)!=type(rule['const']) or value!=rule['const']):fail('Недопустимое значение.')
    if 'enum' in rule and value not in rule['enum']:fail('Недопустимое значение.')
    kind=rule.get('type')
    valid={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),'number':type(value) in (int,float),'integer':type(value) is int,'boolean':type(value) is bool,'null':value is None}
    if kind and not valid[kind]:fail(f'Ожидается {kind}.')
    if type(value) in (int,float):
        if isinstance(value,float) and not math.isfinite(value):fail('Число должно быть конечным.')
        if 'minimum' in rule and value<rule['minimum'] or 'maximum' in rule and value>rule['maximum'] or 'exclusiveMinimum' in rule and value<=rule['exclusiveMinimum']:fail('Число вне допустимого диапазона.')
    if isinstance(value,str):
        if len(value)<rule.get('minLength',0) or 'pattern' in rule and not re.search(rule['pattern'],value):fail('Неверный формат строки.')
    if isinstance(value,dict):
        if len(value)<rule.get('minProperties',0) or len(value)>rule.get('maxProperties',10**9):fail('Недопустимое количество полей.')
        for key in rule.get('required',[]):
            if key not in value:raise SchemaError(path+'/'+key,'Обязательное поле.')
        for key,item in value.items():
            pointer=path+'/'+key.replace('~','~0').replace('/','~1')
            if 'propertyNames' in rule:check(key,rule['propertyNames'],pointer)
            sub=rule.get('properties',{}).get(key,rule.get('additionalProperties',True))
            if sub is False:raise SchemaError(pointer,'Неизвестное поле.')
            if isinstance(sub,dict):check(item,sub,pointer)
    if isinstance(value,list):
        if len(value)<rule.get('minItems',0) or len(value)>rule.get('maxItems',10**9):fail('Недопустимая длина списка.')
        if rule.get('uniqueItems') and len({json.dumps(v,sort_keys=True) for v in value})!=len(value):fail('Элементы не должны повторяться.')
        for i,item in enumerate(value):check(item,rule.get('items',{}),path+'/'+str(i))
