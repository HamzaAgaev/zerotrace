import { demoOrdersConfig, demoServiceCapabilities } from '../api/fixtures';
import { isBudgetFinding } from '../api/types';
import { useEffect, useMemo, useState } from 'react';
import type { AuditResult, ChangePlan, Client, ConfigSnapshot, Finding, RouteWork, TraceDetail, TraceSummary } from '../api/types';
import { summarize, type EdgeStats } from '../analysis';
import { Badge, Button, EmptyState, Message, Panel } from '../components/ui';
import { MetricInfo, Metrics } from '../components/Metrics';
import { Tabs } from '../components/Tabs';
import { ConfigPlanDialog } from '../components/ConfigPlanDialog';
import { duration, serviceColors, shortId, traceHref } from '../lib';
export const formatMs = (v: number | null) => v === null ? 'Неизвестно' : `${v.toLocaleString('ru-RU',{maximumFractionDigits:1})} мс`;
export function Services({ client, environmentId, traces, total, audit, view, onPlan, pending, demo }: { client: Client; environmentId: string; traces:TraceSummary[]; total:number; audit:AuditResult|null; view:'graph'|'timings'; onPlan:(f:Finding)=>void; pending:boolean; demo:boolean }) {
  const [details,setDetails] = useState<TraceDetail[]>([]); const [loading,setLoading] = useState(true); const [failed,setFailed] = useState(0); const [reload,setReload] = useState(0);
  const [surface,setSurface]=useState<'graph'|'table'|'about'>('graph');
  const [maxAttempts, setMaxAttempts] = useState(demoOrdersConfig.max_attempts);
  const [query,setQuery] = useState(''); const [onlyProblems,setOnlyProblems] = useState(false); const [selected,setSelected] = useState(''); const [zoom,setZoom] = useState(1);
  useEffect(() => { const ctrl = new AbortController(); let cursor=0, failures=0; const result:TraceDetail[]=[]; setLoading(true); setFailed(0); setDetails([]);
    async function worker() { while (!ctrl.signal.aborted && cursor < Math.min(traces.length,100)) { const trace = traces[cursor++]; try { const detail = await client.getTrace(trace.trace_ref,ctrl.signal); if (!ctrl.signal.aborted) { if (detail.spans.length > 2000) failures++; else result.push(detail); } } catch { if (!ctrl.signal.aborted) failures++; } } }
    Promise.all(Array.from({length:Math.min(4,traces.length)},worker)).then(() => { if (!ctrl.signal.aborted) { setDetails(result); setFailed(failures); setLoading(false); } }); return () => ctrl.abort();
  },[client,traces,reload]);
  const data = useMemo(() => summarize(details),[details]);
  const findingsOnEdge = (edge: EdgeStats) => edge.to.external ? [] :
    (audit?.findings.filter(f => f.edge.caller === edge.caller && f.edge.callee === edge.callee) ?? []);
  const edges = data.edges.filter(e => (!query || `${e.caller} ${e.callee}`.toLowerCase().includes(query.toLowerCase())) && (!onlyProblems || e.errors > 0 || findingsOnEdge(e).length > 0));
  const nodeIds = new Set(edges.flatMap(e => [e.from.id,e.to.id]));
  const nodes = data.nodes.filter(s => nodeIds.has(s.id) || (!onlyProblems && (!query || s.name.toLowerCase().includes(query.toLowerCase())))).slice(0,40);
  const visibleIds = new Set(nodes.map(node => node.id));
  const visibleEdges = edges.filter(e => visibleIds.has(e.from.id) && visibleIds.has(e.to.id));
  const edge = data.edges.find(e => e.id===selected); const selectedNode = data.nodes.find(node => node.id === selected);
  const selectedService = selectedNode && !selectedNode.external ? data.services.find(s => s.name === selectedNode.name) : undefined;
  const serviceCounts = new Map(data.services.map(s => [s.name,s.count]));
  const findings = edge ? findingsOnEdge(edge).filter(isBudgetFinding) : [];
  const levels = new Map<string,number>();
  const roots = nodes.filter(n=>!visibleEdges.some(e=>e.to.id===n.id));
  const queue = (roots.length?roots:nodes.slice(0,1)).map(n=>n.id); queue.forEach(name=>levels.set(name,0));
  for(let i=0;i<queue.length;i++){const name=queue[i];for(const e of visibleEdges.filter(e=>e.from.id===name)){if(!levels.has(e.to.id)){levels.set(e.to.id,Math.min(4,(levels.get(name)??0)+1));queue.push(e.to.id);}}}
  nodes.forEach(n=>{if(!levels.has(n.id))levels.set(n.id,0);});
  const columns = [...new Set(levels.values())].sort((a,b)=>a-b);
  const groups = columns.map(level=>nodes.filter(n=>levels.get(n.id)===level).sort((a,b)=>visibleEdges.findIndex(e=>e.to.id===a.id)-visibleEdges.findIndex(e=>e.to.id===b.id)));
  const width=Math.max(600,columns.length*280),height=Math.max(340,...groups.map(g=>g.length*135+90));
  const point = (id:string) => {const col=columns.indexOf(levels.get(id)??0);return {x:45+col*280,y:65+groups[col].findIndex(n=>n.id===id)*135};};
  return <><div className="page-title"><div><h1>{view==='graph'?'Граф сервисов':'Время сервисов'}</h1></div><Button icon="refresh" onClick={()=>setReload(v=>v+1)} disabled={loading}>Обновить</Button></div>
    {!loading && <div className="metric-section">
      <Metrics items={[
        { label: 'Трассы в выборке', value: `${details.length} / ${total}` },
        { label: view === 'graph' ? 'Узлы' : 'Сервисы', value: view === 'graph' ? data.nodes.length : data.services.length },
        { label: 'Связи', value: data.edges.length },
      ]}/>
      <MetricInfo label="Как читать показатели графа">
        Показатели рассчитаны по загруженной выборке. Медиана — значение в середине ряда длительностей, p95 — граница для 95% измерений. На малой выборке p95 нестабилен.
      </MetricInfo>
    </div>}{failed>0&&<Message tone="warning">Не удалось включить {failed} трасс. Данные могут быть неполными.</Message>}
    {loading?<div className="loading-state" role="status"><span className="spinner"/>Собираем карту сервисов…</div>:!details.length?<EmptyState title="Нет данных о сервисах" description={failed?'Не удалось получить трассы. Повторите загрузку.':'В текущем наборе нет трасс.'} action={<Button onClick={()=>setReload(v=>v+1)}>Повторить</Button>}/>:<>
      <Tabs label="Представление сервисов" value={view==='timings'&&surface==='table'?'graph':surface} onChange={setSurface} items={view==='graph'?[{value:'graph',label:'Карта'},{value:'table',label:'Связи'},{value:'about',label:'О данных'}]:[{value:'graph',label:'Время ответа'},{value:'about',label:'О данных'}]}/>
      {details.some(t=>t.quality_issues.length>0)&&<Message tone="warning">У {details.filter(t=>t.quality_issues.length>0).length} трасс есть замечания к качеству данных. Граф может быть неполным.</Message>}
      {view==='graph'&&surface!=='about'&&<div className="filters"><input type="search" aria-label="Поиск сервиса в графе" placeholder="Найти сервис или связь" value={query} onChange={e=>setQuery(e.target.value)}/><label className="check-label"><input type="checkbox" checked={onlyProblems} onChange={e=>setOnlyProblems(e.target.checked)}/>Только проблемные связи</label></div>}
      {view==='graph'&&surface==='graph'&&<div className={`graph-layout ${!edge&&!selectedNode?'graph-layout--full':''}`}><Panel title="Наблюдаемые вызовы"><div className="service-graph-scroll">{nodes.length?<svg className="service-graph" role="group" aria-label="Интерактивный граф сервисов" viewBox={`0 0 ${width} ${height}`} style={{width:`${zoom*100}%`,minWidth:600*zoom}}><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs>{visibleEdges.map(e=>{const a=point(e.from.id),b=point(e.to.id);const path=`M${a.x+150},${a.y+30} Q${(a.x+b.x)/2+75},${(a.y+b.y)/2+30} ${b.x},${b.y+30}`;return <g key={e.id} role="button" tabIndex={0} aria-label={`Связь ${e.caller} к ${e.callee}`} className={`graph-edge ${e.errors?'has-problem':''} ${selected===e.id?'is-selected':''}`} onClick={()=>setSelected(e.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setSelected(e.id);}}}><path className="edge-hit" d={path}/><path className="edge-line" d={path} markerEnd="url(#graph-arrow)"/><text x={(a.x+b.x)/2+75} y={(a.y+b.y)/2+20} textAnchor="middle">{e.count} выз.</text></g>;})}{nodes.map(node=>{const p=point(node.id);return <g key={node.id} transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-label={`${node.external?'Адресат':'Сервис'} ${node.name}`} onClick={()=>setSelected(node.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setSelected(node.id);}}} className={`graph-node ${selected===node.id?'is-selected':''}`}><rect width="150" height="60" rx="7"/><circle cx="16" cy="20" r="4" fill={serviceColors[node.name]??'#96a2ff'}/><text x="29" y="24">{node.name.length>17?node.name.slice(0,16)+'…':node.name}</text><text className="graph-node-meta" x="16" y="46">{node.external?'Адресат':`${serviceCounts.get(node.name)??0} операций`}</text></g>;})}</svg>:<EmptyState title="Связи не найдены" description="Измените поиск или отключите фильтр проблем."/>}</div><div className="graph-controls"><Button onClick={()=>setZoom(v=>Math.max(.65,v-.15))}>−</Button><span>{Math.round(zoom*100)}%</span><Button onClick={()=>setZoom(v=>Math.min(1.8,v+.15))}>+</Button><Button onClick={()=>setZoom(1)}>Сбросить масштаб</Button></div></Panel>{(edge||selectedNode)&&<Panel title={edge?'Выбранная связь':selectedNode?.external?'Выбранный адресат':'Выбранный сервис'} meta={<Button variant="ghost" icon="close" aria-label="Закрыть инспектор" onClick={()=>setSelected('')}/>}><div className="explore-inspector">{edge?<><h3>Из {edge.caller} в {edge.callee}</h3><p className="muted">Настройки исходящего вызова находятся в {edge.caller}.</p><dl><div><dt>Вызовы</dt><dd>{edge.count}</dd></div><div><dt>Ошибки</dt><dd>{edge.errors}</dd></div><div><dt>Неизвестный исход</dt><dd>{edge.unknown}</dd></div><div><dt>Медиана</dt><dd>{formatMs(edge.median)}</dd></div><div><dt>p95</dt><dd>{formatMs(edge.p95)}</dd></div></dl>{findings.map(f=><div className="edge-finding" key={f.finding_id}><Badge tone="danger">{f.rule_id}</Badge><p>{f.title}</p><Button onClick={()=>onPlan(f)} disabled={pending}>Посмотреть план</Button></div>)}</>:selectedService?<><h3>{selectedService.name}</h3><p>Медиана {formatMs(selectedService.median)}, p95 {formatMs(selectedService.p95)}</p><ServiceConfig key={selectedService.name} client={client} service={selectedService.name} environmentId={environmentId} demo={demo} maxAttempts={maxAttempts} onSave={setMaxAttempts}/></>:<p className="muted">Адресат виден по исходящим вызовам; измерения доступны на связи.</p>}</div></Panel>}</div>}
      {(view==='timings'&&surface!=='about'||view==='graph'&&surface==='table')&&<Panel title={view==='graph'?'Статистика по связям':'Время ответа сервисов'}><div className="table-scroll"><table className="explore-table"><thead><tr><th>{view==='graph'?'Связь':'Сервис'}</th><th>Операций</th><th>Медиана</th><th>p95</th><th>Ошибки / неизвестно</th><th>{view==='graph'?'Трасса':'Вне дочерних интервалов'}</th></tr></thead><tbody>{view==='graph'?visibleEdges.map(e=><tr key={e.id}><td><button className="text-button" onClick={()=>{setSelected(e.id);setSurface('graph');}}>Из {e.caller} в {e.callee}</button></td><td>{e.count}</td><td>{formatMs(e.median)}</td><td>{formatMs(e.p95)}</td><td>{e.errors} / {e.unknown}</td><td><a className="mono" href={traceHref(traces.find(t => t.trace_id === e.traceIds[0])?.trace_ref ?? e.traceIds[0])}>{shortId(e.traceIds[0])}</a></td></tr>):data.services.filter(s=>s.name.toLowerCase().includes(query.toLowerCase())).map(s=><tr key={s.name}><td><span className="service-name"><i style={{background:serviceColors[s.name]}}/>{s.name}</span></td><td>{s.count}</td><td>{formatMs(s.median)}</td><td>{formatMs(s.p95)}</td><td>{s.errors} / {s.unknown}</td><td>{formatMs(s.own)} <span className="muted">суммарно</span></td></tr>)}</tbody></table></div></Panel>}{surface==='about'&&<Panel title="Как рассчитаны показатели"><div className="info-block"><p>Статистика относится только к загруженной выборке. Карта ограничена 40 сервисами; при неполных трассах часть связей может отсутствовать.</p><p>Медиана и p95: линейная интерполяция по наблюдаемым операциям, включая ошибочные. Для связи используется интервал client-спана, при его отсутствии — дочернего server-спана. В таблице сервисов учитываются только server-спаны. Время вне объединения дочерних интервалов не является CPU-временем. На малой выборке p95 нестабилен.</p></div></Panel>}</>}
  </>;
}


export function ServiceConfig({ client, service, environmentId, demo = false, disabled = false, maxAttempts = 3, onSave = () => undefined, onChanged }: { client: Client; service: string; environmentId: string; demo?: boolean; disabled?: boolean; maxAttempts?: number; onSave?: (value: number) => void; onChanged?: () => void }) {
  const [draft, setDraft] = useState(String(maxAttempts));
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [loading, setLoading] = useState(!demo);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState<ChangePlan | null>(null);
  const [reload, setReload] = useState(0);
  const [callDrafts, setCallDrafts] = useState<Record<string, { timeout: string; attempts: string; backoff: string; retry: string }>>({});
  const [routeDrafts, setRouteDrafts] = useState<Record<string, RouteWork>>({});
  useEffect(() => {
    if (demo) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    client.getConfig(service, environmentId, controller.signal).then(config => {
      if (controller.signal.aborted) return;
      setSnapshot(config);
      setCallDrafts(Object.fromEntries(config.calls.map(call => [call.call_ref, { timeout: String(call.timeout_ms), attempts: String(call.max_attempts), backoff: String(call.backoff_ms), retry: call.retry_on.join(', ') }])));
      setRouteDrafts(Object.fromEntries(config.routes.map(route => [route.route, route.work])));
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось прочитать конфиг.'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [client, demo, service, environmentId, reload]);
  const capabilities = demo ? demoServiceCapabilities[service] : undefined;
  if (demo) {
    if (!capabilities) return <p className="muted">Сведения о доступе к конфигу отсутствуют.</p>;
    if (!capabilities.read_config) return <p className="muted">Конфиг недоступен в демонстрационном наборе.</p>;
    return <form className="service-config" onSubmit={event => { event.preventDefault(); const value = Number(draft); if (Number.isInteger(value) && value >= 1 && value <= 10) onSave(value); }}><Badge tone="warning">Локальная демонстрация</Badge><p>Вызов payment, POST /charge</p><label>Максимум попыток <input type="number" required min="1" max="10" step="1" value={draft} onChange={event => setDraft(event.target.value)}/></label><Button type="submit" disabled={Number(draft) === maxAttempts}>Сохранить в примере</Button></form>;
  }
  async function buildCallPlan(callRef: string) {
    if (!snapshot) return;
    const call = snapshot.calls.find(item => item.call_ref === callRef), values = callDrafts[callRef];
    if (!call || !values) return;
    const set: Record<string, string | number | string[]> = {};
    if (Number(values.timeout) !== call.timeout_ms) set.per_attempt_timeout_ns = String(Math.round(Number(values.timeout) * 1e6));
    if (Number(values.attempts) !== call.max_attempts) set.max_attempts = Number(values.attempts);
    if (Number(values.backoff) !== call.backoff_ms) set.backoff_ms = Number(values.backoff);
    const retry = values.retry.split(',').map(value => value.trim()).filter(Boolean);
    if (JSON.stringify(retry) !== JSON.stringify(call.retry_on)) set.retry_on = retry;
    if (!Object.keys(set).length) return;
    try { setPlan(await client.createManualPlan({ environment_id: environmentId, service_id: service, expected_config_revision: snapshot.revision, call_ref: callRef, set })); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось построить план.'); }
  }
  async function buildRoutePlan(route: string) {
    if (!snapshot) return;
    const current = snapshot.routes.find(item => item.route === route), work = routeDrafts[route];
    if (!current || !work || JSON.stringify(current.work) === JSON.stringify(work)) return;
    try { setPlan(await client.createManualPlan({ environment_id: environmentId, service_id: service, expected_config_revision: snapshot.revision, route, set: { work } })); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось построить план.'); }
  }
  return <div className="service-config"><h3>Конфигурация сервиса</h3>{loading ? <p className="muted">Читаем действующую конфигурацию…</p> : error ? <Message tone="danger">{error}</Message> : !snapshot ? <p className="muted">Конфигурация недоступна.</p> : <><p className="muted">Ревизия {snapshot.revision}. Новые значения действуют для новых входящих запросов.</p>{snapshot.calls.map(call => { const values = callDrafts[call.call_ref]; const writable = call.write_fields.length > 0; return <form key={call.call_ref} onSubmit={event => { event.preventDefault(); void buildCallPlan(call.call_ref); }}><h4>{call.target.service_id} · {call.target.method} {call.target.route}</h4><label>Таймаут попытки, мс <input type="number" min="1" value={values?.timeout ?? ''} disabled={disabled || !call.write_fields.includes('timeout_ms')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], timeout: event.target.value } }))}/></label><label>Максимум попыток <input type="number" min="1" max="10" value={values?.attempts ?? ''} disabled={disabled || !call.write_fields.includes('max_attempts')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], attempts: event.target.value } }))}/></label><label>Пауза, мс <input type="number" min="0" value={values?.backoff ?? ''} disabled={disabled || !call.write_fields.includes('backoff_ms')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], backoff: event.target.value } }))}/></label><label>Повторять при <input value={values?.retry ?? ''} disabled={disabled || !call.write_fields.includes('retry_on')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], retry: event.target.value } }))}/></label>{writable && <Button type="submit" disabled={disabled}>Построить план</Button>}</form>; })}{snapshot.routes.map(route => { const work = routeDrafts[route.route]; return <form key={route.route} onSubmit={event => { event.preventDefault(); void buildRoutePlan(route.route); }}><h4>Входящий {route.route}</h4><label>Распределение <select value={work?.kind ?? 'fixed'} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: event.target.value === 'fixed' ? { kind: 'fixed', delay_ms: 1 } : { kind: 'lognormal', p50_ms: 1, p99_ms: 2 } }))}><option value="fixed">Фиксированное</option><option value="lognormal">Логнормальное</option></select></label>{work?.kind === 'fixed' ? <label>Собственная задержка, мс <input type="number" min="0" value={work.delay_ms} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: { kind: 'fixed', delay_ms: Number(event.target.value) } }))}/></label> : <><label>p50, мс <input type="number" min="1" value={work?.p50_ms ?? ''} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: { kind: 'lognormal', p50_ms: Number(event.target.value), p99_ms: (old[route.route] as Extract<RouteWork, { kind: 'lognormal' }>).p99_ms } }))}/></label><label>p99, мс <input type="number" min="1" value={work?.p99_ms ?? ''} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: { kind: 'lognormal', p50_ms: (old[route.route] as Extract<RouteWork, { kind: 'lognormal' }>).p50_ms, p99_ms: Number(event.target.value) } }))}/></label></>}{route.write_fields.includes('work') && <Button type="submit" disabled={disabled}>Построить план</Button>}</form>; })}</>}{plan && <ConfigPlanDialog client={client} plan={plan} onClose={() => setPlan(null)} onApplied={() => { setPlan(null); setSnapshot(null); setReload(value => value + 1); onChanged?.(); }} />}</div>;
}
