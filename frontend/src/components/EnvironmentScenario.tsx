import {useEffect,useState} from 'react';
import type {Client,ScenarioDescriptor} from '../api/types';
import {createOrRecoverStand} from '../api/client';
import {Button,Message} from './ui';
export function EnvironmentScenario({client,environmentId}:{client:Client;environmentId:string}) {
  const [descriptor,setDescriptor]=useState<ScenarioDescriptor>();
  const [error,setError]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{const ctrl=new AbortController();setDescriptor(undefined);setError('');client.listEnvironments(ctrl.signal).then(items=>{if(!ctrl.signal.aborted)setDescriptor(items.find(e=>e.environment_id===environmentId)?.lab_scenario??{status:'unconfigured'});}).catch(e=>{if(!ctrl.signal.aborted)setError(e.message);});return()=>ctrl.abort();},[client,environmentId]);
  async function create(){setBusy(true);setError('');try{await createOrRecoverStand(client,{environment_id:environmentId});location.hash='#/lab';}catch(e){setError(e instanceof Error?e.message:'Не удалось создать стенд.');}finally{setBusy(false);}}
  return <div className="environment-scenario">{descriptor?.status==='ready'?<><Button onClick={create} loading={busy} disabled={busy}>Создать тестовый стенд</Button></>:descriptor?.status==='invalid'?<Message tone="warning">{descriptor.error}</Message>:descriptor?.status==='unconfigured'?<p className="muted">Сценарий тестового стенда для этого окружения не настроен.</p>:null}{error&&<Message tone="warning" action={<a href="#/lab">Открыть тестовый стенд</a>}>{error}</Message>}</div>;
}
