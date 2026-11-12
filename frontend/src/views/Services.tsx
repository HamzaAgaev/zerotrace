import { CopyId } from '../components/CopyId';
import { EnvironmentScenario } from '../components/EnvironmentScenario';
import { PeriodSelect } from '../components/PeriodSelect';
import { validPeriod } from '../api/periodMetrics';
import type { ServiceGraphData } from '../api/types';
import { ServiceGraph } from '../components/ServiceGraph';
import { ServiceStatistics } from '../components/ServiceStatistics';
import { usePersistentState } from '../state';
import { ServiceCall } from '../components/HttpText';
import { RuleLabel } from '../components/RuleLabel';
import { isBudgetFinding } from '../api/types';
import { useEffect, useState } from 'react';
import type { AuditResult, Client, Finding } from '../api/types';
import { type EdgeStats } from '../analysis';
import { Badge, Button, EmptyState, Message, Panel, PageHeader, LoadingState, RetryButton } from '../components/ui';
import { Metrics, MetricLabel } from '../components/Metrics';
import { Tabs } from '../components/Tabs';
import { ServiceConfig } from '../components/ServiceConfig';
import { serviceColor, serviceSurfaceForHash, traceHref } from '../lib';
import { formatMs } from '../presentation';
export function Services({ client, environmentId, audit, view, onPlan, pending, demo }: { client: Client; environmentId: string; audit:AuditResult|null; view:'graph'|'timings'; onPlan:(f:Finding)=>void; pending:boolean; demo:boolean }) {
  const [data,setData] = useState<ServiceGraphData>({services:[],edges:[],nodes:[],trace_count:0,missing_count:0,quality_count:0,quality_traces:[]});
  const [period,setPeriod]=usePersistentState('services:metrics-period',30,validPeriod);
  const [error,setError]=useState(''); const [loading,setLoading] = useState(true); const [failed,setFailed] = useState(0); const [reload,setReload] = useState(0);
  const [savedSurface,setSurface]=usePersistentState<'graph'|'table'>('services:surface', view === 'timings' ? 'table' : 'graph');
  const surface = serviceSurfaceForHash(location.hash, savedSurface);
  const [onlyProblems,setOnlyProblems] = usePersistentState('views/Services.tsx:onlyProblems', false); const [selected,setSelected] = usePersistentState('views/Services.tsx:selected', '');
  useEffect(()=>{if(surface!=='graph')return;const ctrl=new AbortController();setLoading(true);setError('');setFailed(0);
    client.getServiceGraph(period,ctrl.signal).then(value=>{if(!ctrl.signal.aborted){setData(value);setFailed(value.missing_count);}})
      .catch(cause=>{if(!ctrl.signal.aborted)setError(cause instanceof Error?cause.message:'Не удалось загрузить граф.');})
      .finally(()=>{if(!ctrl.signal.aborted)setLoading(false);});return()=>ctrl.abort();
  },[client,reload,surface,period]);
  const findingsOnEdge = (edge: EdgeStats) => edge.to.external ? [] :
    (audit?.findings.filter(f => f.edge.caller === edge.caller && f.edge.callee === edge.callee) ?? []);
  const edges = data.edges.filter(e => (!onlyProblems || e.errors > 0 || findingsOnEdge(e).length > 0));
  const nodeIds = new Set(edges.flatMap(e => [e.from.id,e.to.id]));
  const nodes = data.nodes.filter(s => nodeIds.has(s.id) || !onlyProblems);
  const visibleIds = new Set(nodes.map(node => node.id));
  const visibleEdges = edges.filter(e => visibleIds.has(e.from.id) && visibleIds.has(e.to.id));
  const edge = data.edges.find(e => e.id===selected); const selectedNode = data.nodes.find(node => node.id === selected);
  const selectedService = selectedNode && !selectedNode.external ? data.services.find(s => s.name === selectedNode.name) : undefined;
  const serviceCounts = new Map(data.services.map(s => [s.name,s.count]));
  const requestedFinding = new URLSearchParams(location.hash.split('?')[1]).get('finding');
  const requestedFindingMatch = requestedFinding && audit
    ? audit.findings.find(f=>f.finding_id===requestedFinding||f.occurrence_id===requestedFinding)
    : undefined;
  const requestedFindingNode = requestedFindingMatch
    ? data.nodes.find(n=>!n.external&&n.name===requestedFindingMatch.edge.caller)
    : undefined;
  useEffect(()=>{
    if(!requestedFinding||!audit||!data.nodes.length)return;
    setSelected(requestedFindingNode?.id ?? '');
    if(requestedFindingNode)setSurface('graph');
  },[requestedFinding,audit,data,requestedFindingNode?.id]);
  const findings = edge ? findingsOnEdge(edge).filter(isBudgetFinding) : [];
  if(surface==='table')return <><PageHeader title="Сервисы" actions={<><EnvironmentScenario client={client} environmentId={environmentId}/><Button icon="refresh" onClick={()=>setReload(v=>v+1)}>Обновить</Button></>}/>
    <Tabs label="Представление сервисов" value={surface} onChange={value=>{setSurface(value);location.hash=`#/services?tab=${value==='graph'?'graph':'statistics'}`;}} items={[{value:'graph',label:'Граф'},{value:'table',label:'Статистика'}]}/>
    <ServiceStatistics client={client} reload={reload} onRetry={()=>setReload(v=>v+1)}/></>;
  return <><PageHeader title="Сервисы" actions={<><EnvironmentScenario client={client} environmentId={environmentId}/><PeriodSelect value={period} onChange={setPeriod}/><Button icon="refresh" onClick={()=>setReload(v=>v+1)} loading={loading}>Обновить</Button></>}/>
      <Tabs label="Представление сервисов" value={surface} onChange={value => { setSurface(value); location.hash = `#/services?tab=${value === 'graph' ? 'graph' : 'statistics'}`; }} items={[{value:'graph',label:'Граф'},{value:'table',label:'Статистика'}]}/>
    {!loading && <div>
      <Metrics items={[
        { label: 'Трейсы за период', value: data.trace_count },
        { label: view === 'graph' ? 'Узлы' : 'Сервисы', value: view === 'graph' ? data.nodes.length : data.services.length },
        { label: 'Связи', value: data.edges.length, info: 'Направления вызовов между сервисами в загруженной выборке.' },
      ]}/>

    </div>}{!loading&&!error&&data.trace_count>0&&failed>0&&<Message tone="warning">Не удалось включить часть трейсов ({failed}). Данные могут быть неполными.</Message>}
    {loading?<LoadingState>Собираем карту сервисов…</LoadingState>:error?<Message tone="danger" action={<RetryButton onClick={()=>setReload(v=>v+1)}/>}>{error}</Message>:!data.trace_count?<EmptyState title="Нет данных о сервисах" description={failed?`Не удалось получить трейсы (${failed}). Повторите загрузку.`:'В текущем наборе нет трейсов.'} action={<RetryButton onClick={()=>setReload(v=>v+1)}/>}/>:<>

      {requestedFinding && audit && !requestedFindingMatch && <Message tone="warning">Проблема из ссылки больше недоступна. Выберите сервис, чтобы посмотреть текущие настройки.</Message>}
      {requestedFindingMatch && !requestedFindingNode && <Message tone="warning">Проблема найдена, но её сервис {requestedFindingMatch.edge.caller} отсутствует в текущей выборке. Обновите выборку или откройте сервис вручную.</Message>}{data.quality_traces.length>0&&<Message tone="warning">
        <div className="graph-quality-note">
          <span>Трейсов с неполными или некорректными данными: {data.quality_count}. Граф может быть неполным.</span>
          <ul className="graph-quality-list">{data.quality_traces.map(item=><li key={traceHref(item.trace_ref)}>
            <CopyId label="Trace ID" value={item.trace_ref.trace_id} href={traceHref(item.trace_ref)}/>
          </li>)}</ul>
        </div>
      </Message>}

      {surface==='graph'&&<div className="graph-layout graph-layout--full"><Panel title="Граф сервисов" meta={<label className="check-label"><input type="checkbox" checked={onlyProblems} onChange={e=>setOnlyProblems(e.target.checked)}/>Только проблемные связи</label>}><ServiceGraph storageKey="services" nodes={nodes.map(n=>({id:n.id,label:n.name,detail:n.external?'Нет данных обработки':`${serviceCounts.get(n.name)??0} операций`,color:serviceColor(n.name)}))} edges={visibleEdges.map(e=>({id:e.id,from:e.from.id,to:e.to.id,label:`${e.count} вызовов`,problem:e.errors>0}))} selected={selected} onSelect={setSelected}/></Panel>{<Panel title={edge?'Выбранная связь':selectedNode?.external?'Выбранный адресат':'Выбранный сервис'} meta={selected ? <Button variant="ghost" icon="close" aria-label="Снять выбор сервиса" onClick={()=>setSelected('')}/> : undefined}><div className="explore-inspector">{selected && !edge && !selectedNode && <Message>Ранее выбранный сервис или связь больше недоступны в этой выборке.</Message>}{edge?<><h3><ServiceCall call={edge}/></h3><p className="muted">Настройки обращения к этому сервису находятся в {edge.caller}.</p><dl><div><dt><MetricLabel label="Вызовы"/></dt><dd>{edge.count}</dd></div>{!!edge.unpaired&&<div><dt>Без спана обработки</dt><dd>{edge.unpaired}</dd></div>}<div><dt><MetricLabel label="Ошибки"/></dt><dd>{edge.errors}</dd></div><div><dt><MetricLabel label="Результат неизвестен"/></dt><dd>{edge.unknown}</dd></div><div><dt><MetricLabel label="Медиана"/></dt><dd>{formatMs(edge.median)}</dd></div><div><dt><MetricLabel label="95-й перцентиль"/></dt><dd>{formatMs(edge.p95)}</dd></div></dl>{findings.map(f=><div className="edge-finding" key={f.finding_id}><RuleLabel id={f.rule_id}/><p>{f.title}</p><Button onClick={()=>onPlan(f)} disabled={pending}>Посмотреть план</Button></div>)}</>:selectedService?<><div className="service-identity"><span className="muted">Сервис</span><strong className="service-name-color" style={{color:serviceColor(selectedService.name)}}><span className="service-color-dot" aria-hidden="true"/>{selectedService.name}</strong></div><Metrics items={[
          { label: 'Операций', value: selectedService.count },
          { label: 'Медиана', value: formatMs(selectedService.median) },
          { label: '95-й перцентиль', value: formatMs(selectedService.p95) },
          { label: 'Ошибки', value: selectedService.errors },
          { label: 'Результат неизвестен', value: selectedService.unknown },
        ]}/><ServiceConfig key={selectedService.name} client={client} service={selectedService.name} environmentId={demo ? 'demo-shop' : environmentId} initialCall={requestedFindingMatch?.edge} localOnly={demo}/></>:!selectedNode?<p className="muted">Выберите сервис на графе, чтобы посмотреть его показатели и настроить параметры вызовов.</p>:<p className="muted">Спаны этого сервиса не получены. Данные об обращениях к нему доступны на связи.</p>}</div></Panel>}</div>}
</>}
  </>;
}
