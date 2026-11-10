import { demoOrdersConfig, demoServiceCapabilities } from '../api/fixtures';
import { availableBudget, canPreparePlan, isBudgetFinding } from '../api/types';
import { useEffect, useMemo, useState } from 'react';
import type { AuditResult, Client, Finding, TraceDetail, TraceSummary } from '../api/types';
import { summarize } from '../analysis';
import { Badge, Button, EmptyState, Message, Panel } from '../components/ui';
import { MetricInfo, Metrics } from '../components/Metrics';
import { Tabs } from '../components/Tabs';
import { duration, serviceColors, shortId, traceHref } from '../lib';
export const formatMs = (v: number | null) => v === null ? 'Неизвестно' : `${v.toLocaleString('ru-RU',{maximumFractionDigits:1})} мс`;
export function Services({ client, traces, total, audit, view, onPlan, pending, demo }: { client: Client; traces:TraceSummary[]; total:number; audit:AuditResult|null; view:'graph'|'timings'; onPlan:(f:Finding)=>void; pending:boolean; demo:boolean }) {
  const [details,setDetails] = useState<TraceDetail[]>([]); const [loading,setLoading] = useState(true); const [failed,setFailed] = useState(0); const [reload,setReload] = useState(0);
  const [surface,setSurface]=useState<'graph'|'table'|'about'>('graph');
  const [maxAttempts, setMaxAttempts] = useState(demoOrdersConfig.max_attempts);
  const [query,setQuery] = useState(''); const [onlyProblems,setOnlyProblems] = useState(false); const [selected,setSelected] = useState(''); const [zoom,setZoom] = useState(1);
  useEffect(() => { const ctrl = new AbortController(); let cursor=0, failures=0; const result:TraceDetail[]=[]; setLoading(true); setFailed(0); setDetails([]);
    async function worker() { while (!ctrl.signal.aborted && cursor < Math.min(traces.length,100)) { const trace = traces[cursor++]; try { const detail = await client.getTrace(trace.trace_ref,ctrl.signal); if (!ctrl.signal.aborted) { if (detail.spans.length > 2000) failures++; else result.push(detail); } } catch { if (!ctrl.signal.aborted) failures++; } } }
    Promise.all(Array.from({length:Math.min(4,traces.length)},worker)).then(() => { if (!ctrl.signal.aborted) { setDetails(result); setFailed(failures); setLoading(false); } }); return () => ctrl.abort();
  },[client,traces,reload]);
  const data = useMemo(() => summarize(details),[details]);
  const problematic = (caller:string,callee:string) => audit?.findings.some(f => f.edge.caller === caller && f.edge.callee === callee);
  const edges = data.edges.filter(e => (!query || `${e.caller} ${e.callee}`.toLowerCase().includes(query.toLowerCase())) && (!onlyProblems || e.errors > 0 || problematic(e.caller,e.callee)));
  const nodeNames = new Set(edges.flatMap(e => [e.caller,e.callee]));
  const nodes = data.services.filter(s => nodeNames.has(s.name) || (!onlyProblems && (!query || s.name.toLowerCase().includes(query.toLowerCase())))).slice(0,40);
  const names = new Set(nodes.map(s => s.name)); const visibleEdges=edges.filter(e => names.has(e.caller)&&names.has(e.callee));
  const edge = data.edges.find(e => e.id===selected); const selectedService = data.services.find(s => s.name===selected);
  const findings = edge ? audit?.findings.filter(isBudgetFinding).filter(f => f.edge.caller===edge.caller&&f.edge.callee===edge.callee) ?? [] : [];
  const levels = new Map<string,number>();
  const roots = nodes.filter(n=>!visibleEdges.some(e=>e.callee===n.name));
  const queue = (roots.length?roots:nodes.slice(0,1)).map(n=>n.name); queue.forEach(name=>levels.set(name,0));
  for(let i=0;i<queue.length;i++){const name=queue[i];for(const e of visibleEdges.filter(e=>e.caller===name)){if(!levels.has(e.callee)){levels.set(e.callee,Math.min(4,(levels.get(name)??0)+1));queue.push(e.callee);}}}
  nodes.forEach(n=>{if(!levels.has(n.name))levels.set(n.name,0);});
  const columns = [...new Set(levels.values())].sort((a,b)=>a-b);
  const groups = columns.map(level=>nodes.filter(n=>levels.get(n.name)===level).sort((a,b)=>visibleEdges.findIndex(e=>e.callee===a.name)-visibleEdges.findIndex(e=>e.callee===b.name)));
  const width=Math.max(600,columns.length*280),height=Math.max(340,...groups.map(g=>g.length*135+90));
  const point = (name:string) => {const col=columns.indexOf(levels.get(name)??0);return {x:45+col*280,y:65+groups[col].findIndex(n=>n.name===name)*135};};
  return <><div className="page-title"><div><h1>{view==='graph'?'Граф сервисов':'Время сервисов'}</h1></div><Button icon="refresh" onClick={()=>setReload(v=>v+1)} disabled={loading}>Обновить</Button></div>
    {!loading && <div className="metric-section">
      <Metrics items={[
        { label: 'Трассы в выборке', value: `${details.length} / ${total}` },
        { label: 'Сервисы', value: data.services.length },
        { label: 'Связи', value: data.edges.length },
      ]}/>
      <MetricInfo label="Как читать показатели графа">
        Показатели рассчитаны по загруженной выборке. Медиана — значение в середине ряда длительностей, p95 — граница для 95% измерений. На малой выборке p95 нестабилен.
      </MetricInfo>
    </div>}{failed>0&&<Message tone="warning">Не удалось включить {failed} трасс. Данные могут быть неполными.</Message>}
    {loading?<div className="loading-state" role="status"><span className="spinner"/>Собираем карту сервисов…</div>:!details.length?<EmptyState title="Нет данных о сервисах" description={failed?'Не удалось получить трассы. Повторите загрузку.':'В текущем наборе нет трасс.'} action={<Button onClick={()=>setReload(v=>v+1)}>Повторить</Button>}/>:<>
      <Tabs label="Представление сервисов" value={view==='timings'&&surface==='table'?'graph':surface} onChange={setSurface} items={view==='graph'?[{value:'graph',label:'Карта'},{value:'table',label:'Связи'},{value:'about',label:'О данных'}]:[{value:'graph',label:'Время ответа'},{value:'about',label:'О данных'}]}/>
      {details.some(t=>t.quality_issues.length>0)&&<Message tone="warning">У {details.filter(t=>t.quality_issues.length>0).length} трасс есть замечания к качеству данных. Граф может быть неполным.</Message>}      {view==='timings'&&surface!=='about'&&<div className="filters"><input type="search" aria-label="Поиск времени сервиса" placeholder="Найти сервис" value={query} onChange={e=>setQuery(e.target.value)}/></div>}{view==='graph'&&surface!=='about'&&<div className="filters"><input type="search" aria-label="Поиск сервиса в графе" placeholder="Найти сервис или связь" value={query} onChange={e=>setQuery(e.target.value)}/><label className="check-label"><input type="checkbox" checked={onlyProblems} onChange={e=>setOnlyProblems(e.target.checked)}/>Только проблемные связи</label>{surface==='graph'&&<div className="graph-controls"><Button aria-label="Уменьшить граф" onClick={()=>setZoom(v=>Math.max(.65,v-.15))}>−</Button><span>{Math.round(zoom*100)}%</span><Button aria-label="Увеличить граф" onClick={()=>setZoom(v=>Math.min(1.8,v+.15))}>+</Button><Button onClick={()=>setZoom(1)}>Сбросить масштаб</Button></div>}</div>}{view==='graph'&&surface==='graph'?<><div className={`graph-layout ${!edge&&!selectedService?'graph-layout--full':''}`}><Panel title="Наблюдаемые вызовы"><div className="service-graph-scroll">{nodes.length?<svg className="service-graph" role="group" aria-label="Интерактивный граф сервисов" viewBox={`0 0 ${width} ${height}`} style={{width:`${zoom*100}%`,minWidth:600*zoom}}><defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs>{visibleEdges.map(e=>{const a=point(e.caller), b=point(e.callee), problem=e.errors>0||problematic(e.caller,e.callee); const forward=b.x>a.x; const x1=a.x+(forward?150:0),y1=a.y+30,x2=b.x+(forward?0:150),y2=b.y+30; const midY=(y1+y2)/2; const path=`M${x1},${y1} Q${(x1+x2)/2},${midY} ${x2},${y2}`; return <g key={e.id} role="button" tabIndex={0} aria-label={`Связь ${e.caller} к ${e.callee}`} className={`graph-edge ${problem?'has-problem':''} ${selected===e.id?'is-selected':''}`} onClick={()=>setSelected(e.id)} onKeyDown={event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();setSelected(e.id);}}}><path className="edge-hit" d={path}/><path className="edge-line" d={path} markerEnd="url(#graph-arrow)"/><text x={(x1+x2)/2} y={midY-9} textAnchor="middle">{e.count} выз.</text></g>;})}{nodes.map(s=>{const p=point(s.name);return <g key={s.name} transform={`translate(${p.x},${p.y})`} role="button" tabIndex={0} aria-label={`Сервис ${s.name}`} onClick={()=>setSelected(s.name)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();setSelected(s.name);}}} className={`graph-node ${selected===s.name?'is-selected':''}`}><rect width="150" height="60" rx="7"/><circle cx="16" cy="21" r="4" fill={serviceColors[s.name]??'#96a2ff'}/><text x="29" y="25">{s.name.length>17?s.name.slice(0,16)+'…':s.name}</text><text className="graph-node-meta" x="16" y="46">{s.count} операций</text></g>;})}</svg>:<EmptyState title="Связи не найдены" description="Измените поиск или отключите фильтр проблем."/>}</div><div className="graph-legend"><span><i/> Ошибки или находки</span><MetricInfo label="Как читать граф">Выберите сервис или стрелку, чтобы открыть показатели и примеры трасс. Стрелки идут от вызывающего сервиса к получателю.</MetricInfo></div></Panel>{(edge||selectedService)&&<Panel title={edge?'Выбранная связь':selectedService?'Выбранный сервис':'Инспектор'} meta={<Button variant="ghost" icon="close" aria-label="Закрыть инспектор" onClick={()=>setSelected('')}/>}><div className="explore-inspector">{edge?<><h3>Из {edge.caller} в {edge.callee}</h3><p className="muted">Настройки исходящего вызова находятся в {edge.caller}.</p><dl><div><dt>Вызовы</dt><dd>{edge.count}</dd></div><div><dt>Ошибки</dt><dd>{edge.errors}</dd></div><div><dt>Неизвестный исход</dt><dd>{edge.unknown}</dd></div><div><dt>Медиана интервала</dt><dd>{formatMs(edge.median)}</dd></div><div><dt>p95</dt><dd>{formatMs(edge.p95)}</dd></div></dl>{findings.map(f=><div key={f.finding_id} className="edge-finding"><Badge tone="danger">{f.rule_id}</Badge><p>{f.title}</p><dl><div><dt>Таймаут попытки</dt><dd>{duration(f.evidence.timeout_ns)}</dd></div><div><dt>Всего попыток</dt><dd>{f.evidence.attempts}</dd></div><div><dt>Верхний бюджет</dt><dd>{duration(f.evidence.budget_ns)}</dd></div><div><dt>Доступно после резерва и затрат</dt><dd>{duration(availableBudget(f.evidence))}</dd></div></dl>{canPreparePlan(f)&&<Button onClick={()=>onPlan(f)} disabled={pending}>Посмотреть план</Button>}<a className="button button--primary" href={`#/policies?finding=${encodeURIComponent(f.finding_id)}`}>Изменить параметры</a></div>)}{!findings.length&&<p className="muted">Известной конфигурации в ответе аудита нет. Таймаут и число повторов по длительности не восстанавливаются.</p>}<div className="inspector-examples"><h3>Примеры трасс</h3>{edge.traceIds.slice(0,4).map(id=><a className="trace-example mono" key={id} href={traceHref(traces.find(t => t.trace_id === id)?.trace_ref ?? id)}>{shortId(id)}</a>)}</div></>:selectedService?<><h3>{selectedService.name}</h3><p>Медиана {formatMs(selectedService.median)}</p><p>p95 {formatMs(selectedService.p95)}</p><p>{selectedService.count} серверных операций</p><ServiceConfig key={selectedService.name} service={selectedService.name} demo={demo} maxAttempts={maxAttempts} onSave={setMaxAttempts}/><a className="button" href="#/timings">Все времена сервисов</a></>:<p className="muted">Нажмите на сервис или линию между сервисами. Здесь появятся измерения, примеры трасс и доступные исправления.</p>}</div></Panel>}</div></>:null}
      {(view==='timings'&&surface!=='about'||view==='graph'&&surface==='table')&&<Panel title={view==='graph'?'Статистика по связям':'Время ответа сервисов'}><div className="table-scroll"><table className="explore-table"><thead><tr><th>{view==='graph'?'Связь':'Сервис'}</th><th>Операций</th><th>Медиана</th><th>p95</th><th>Ошибки / неизвестно</th><th>{view==='graph'?'Трасса':'Вне дочерних интервалов'}</th></tr></thead><tbody>{view==='graph'?visibleEdges.map(e=><tr key={e.id}><td><button className="text-button" onClick={()=>{setSelected(e.id);setSurface('graph');}}>Из {e.caller} в {e.callee}</button></td><td>{e.count}</td><td>{formatMs(e.median)}</td><td>{formatMs(e.p95)}</td><td>{e.errors} / {e.unknown}</td><td><a className="mono" href={traceHref(traces.find(t => t.trace_id === e.traceIds[0])?.trace_ref ?? e.traceIds[0])}>{shortId(e.traceIds[0])}</a></td></tr>):data.services.filter(s=>s.name.toLowerCase().includes(query.toLowerCase())).map(s=><tr key={s.name}><td><span className="service-name"><i style={{background:serviceColors[s.name]}}/>{s.name}</span></td><td>{s.count}</td><td>{formatMs(s.median)}</td><td>{formatMs(s.p95)}</td><td>{s.errors} / {s.unknown}</td><td>{formatMs(s.own)} <span className="muted">суммарно</span></td></tr>)}</tbody></table></div></Panel>}{surface==='about'&&<Panel title="Как рассчитаны показатели"><div className="info-block"><p>Статистика относится только к загруженной выборке. Карта ограничена 40 сервисами; при неполных трассах часть связей может отсутствовать.</p><p>Медиана и p95: nearest-rank по наблюдаемым операциям, включая ошибочные. Для связи используется интервал client-спана, при его отсутствии — дочернего server-спана. В таблице сервисов учитываются только server-спаны. Время вне объединения дочерних интервалов не является CPU-временем. На малой выборке p95 нестабилен.</p></div></Panel>}</>}
  </>;
}


function ServiceConfig({ service, demo, maxAttempts, onSave }: {
  service: string; demo: boolean; maxAttempts: number; onSave: (value: number) => void;
}) {
  const [draft, setDraft] = useState(String(maxAttempts));
  const capabilities = demo ? demoServiceCapabilities[service] : undefined;
  if (!capabilities) return <p className="muted">Сведения о доступе к конфигу отсутствуют.</p>;
  if (!capabilities.read_config) return <p className="muted">Конфиг недоступен (демо).</p>;
  const values = { ...demoOrdersConfig, max_attempts: maxAttempts };
  return <form className="service-config" onSubmit={event => {
    event.preventDefault();
    const value = Number(draft);
    if (capabilities.write_fields.includes('max_attempts') && Number.isInteger(value) && value >= 1 && value <= 10) {
      onSave(value);
    }
  }}>
    <Badge tone="warning">Конфиг внешнего сервиса (демо)</Badge>
    <p>Вызов payment (POST /charge)</p>
    {Object.entries(values).map(([field, value]) => <label key={field}>
      <code>{field}</code>
      {capabilities.write_fields.includes(field)
        ? <input type="number" required min={1} max={10} step={1} value={draft} onChange={event => setDraft(event.target.value)}/>
        : <strong>{value} мс (только чтение)</strong>}
    </label>)}
    {capabilities.write_fields.includes('max_attempts') &&
      <Button type="submit" disabled={Number(draft) === maxAttempts}>Сохранить в примере</Button>}
    <p className="muted" role="status">Сохранено попыток: {maxAttempts}</p>
  </form>;
}
