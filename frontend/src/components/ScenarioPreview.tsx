import { useState } from 'react';
import type { LabScenario } from '../labScenario';
import { ServiceGraph } from './ServiceGraph';

export function ScenarioPreview({scenario,scenarioHash}:{scenario:LabScenario;scenarioHash:string}) {
  const [selected,setSelected]=useState(scenario.entrypoint.service_id);
  const nodes=Object.entries(scenario.services).map(([id,service])=>({
    id,label:id,detail:id===scenario.entrypoint.service_id?'Начальный сервис':`Эндпоинтов: ${Object.keys(service.routes).length}`,
  }));
  const links=new Map<string,{id:string;from:string;to:string}>();
  for(const [from,service] of Object.entries(scenario.services)) {
    for(const route of Object.values(service.routes)) for(const step of route.steps) {
      for(const ref of step.parallel??(step.call?[step.call]:[])) {
        const to=route.calls[ref].target.service_id;
        const id=JSON.stringify([from,to]);
        links.set(id,{id,from,to});
      }
    }
  }
  return <div className="scenario-preview">
    <ServiceGraph storageKey={`scenario-preview:${scenarioHash}`} nodes={nodes} edges={[...links.values()]} selected={selected} onSelect={id=>setSelected(links.get(id)?.from??id)}/>

  </div>;
}
