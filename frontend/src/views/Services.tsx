import { ServiceStatistics } from '../components/ServiceStatistics';
import { usePersistentState } from '../state';
import { ServiceCall, serviceCallLabel } from '../components/HttpText';
import { RuleLabel } from '../components/RuleLabel';
import { isBudgetFinding } from '../api/types';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { AuditResult, Client, Finding, TraceDetail, TraceSummary } from '../api/types';
import { summarize, type EdgeStats } from '../analysis';
import { Badge, Button, EmptyState, Message, Panel, PageHeader, LoadingState, RetryButton } from '../components/ui';
import { Metrics } from '../components/Metrics';
import { Tabs } from '../components/Tabs';
import { ServiceConfig } from '../components/ServiceConfig';
import { serviceColors, serviceSurfaceForHash } from '../lib';
import { formatMs } from '../presentation';
const graphPadding = 24;
const graphColumnGap = 104;
const graphRowGap = 44;

export function Services({ client, environmentId, traces, total, audit, view, onPlan, pending, demo }: { client: Client; environmentId: string; traces:TraceSummary[]; total:number; audit:AuditResult|null; view:'graph'|'timings'; onPlan:(f:Finding)=>void; pending:boolean; demo:boolean }) {
  const [details,setDetails] = useState<TraceDetail[]>([]); const [loading,setLoading] = useState(true); const [failed,setFailed] = useState(0); const [reload,setReload] = useState(0);
  const [savedSurface,setSurface]=usePersistentState<'graph'|'table'>('services:surface', view === 'timings' ? 'table' : 'graph');
  const surface = serviceSurfaceForHash(location.hash, savedSurface);
  const [query,setQuery] = usePersistentState('views/Services.tsx:query', ''); const [onlyProblems,setOnlyProblems] = usePersistentState('views/Services.tsx:onlyProblems', false); const [selected,setSelected] = usePersistentState('views/Services.tsx:selected', ''); const [zoom,setZoom] = usePersistentState('views/Services.tsx:zoom', 1);
  const graph = useRef<SVGSVGElement>(null);
  const [nodeSizes, setNodeSizes] = useState(new Map<string, { width: number; height: number }>());
  useEffect(() => { if(surface !== 'graph') return; const ctrl = new AbortController(); let cursor=0, failures=0; const result:TraceDetail[]=[]; setLoading(true); setFailed(0); setDetails([]);
    async function worker() { while (!ctrl.signal.aborted && cursor < Math.min(traces.length,100)) { const trace = traces[cursor++]; try { const detail = await client.getTrace(trace.trace_ref, ctrl.signal, { includeFindings: false }); if (!ctrl.signal.aborted) { if (detail.spans.length > 2000) failures++; else result.push(detail); } } catch { if (!ctrl.signal.aborted) failures++; } } }
    Promise.all(Array.from({length:Math.min(4,traces.length)},worker)).then(() => { if (!ctrl.signal.aborted) { setDetails(result); setFailed(failures); setLoading(false); } }); return () => ctrl.abort();
  },[client,traces,reload,surface]);
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
  const levels = new Map<string,number>();
  const roots = nodes.filter(n=>!visibleEdges.some(e=>e.to.id===n.id));
  const queue = (roots.length?roots:nodes.slice(0,1)).map(n=>n.id); queue.forEach(name=>levels.set(name,0));
  for(let i=0;i<queue.length;i++){const name=queue[i];for(const e of visibleEdges.filter(e=>e.from.id===name)){if(!levels.has(e.to.id)){levels.set(e.to.id,Math.min(4,(levels.get(name)??0)+1));queue.push(e.to.id);}}}
  nodes.forEach(n=>{if(!levels.has(n.id))levels.set(n.id,0);});
  const columns = [...new Set(levels.values())].sort((a,b)=>a-b);
  const groups = columns.map(level=>nodes.filter(n=>levels.get(n.id)===level).sort((a,b)=>visibleEdges.findIndex(e=>e.to.id===a.id)-visibleEdges.findIndex(e=>e.to.id===b.id)));
  const nodeLabels = nodes.map(node => [node.id, node.name, node.external ? 'Адресат' : `${serviceCounts.get(node.name) ?? 0} операций`] as const);
  const nodeLabelsKey = JSON.stringify(nodeLabels);
  const showGraph = surface === 'graph' && !loading;
  useLayoutEffect(() => {
    if (!showGraph || !graph.current) return;
    const svg = graph.current;
    function measureNodes() {
      const measured = new Map<string, { width: number; height: number }>();
      for (const group of svg.querySelectorAll<SVGGElement>('[data-node-id]')) {
        const texts = Array.from(group.querySelectorAll('text'), text => text.getBBox());
        measured.set(group.dataset.nodeId!, {
          width: Math.ceil(Math.max(...texts.map(box => box.x + box.width)) + 16),
          height: Math.ceil(Math.max(...texts.map(box => box.y + box.height)) + 12),
        });
      }
      setNodeSizes(measured);
    }
    measureNodes();
    window.addEventListener('resize', measureNodes);
    return () => window.removeEventListener('resize', measureNodes);
  }, [showGraph, nodeLabelsKey]);

  const positions = new Map<string, { x: number; y: number; width: number; height: number }>();
  let nextColumnX = graphPadding;
  let bottom = graphPadding;
  for (const group of groups) {
    let rowY = graphPadding;
    let columnWidth = 0;
    for (const node of group) {
      const size = nodeSizes.get(node.id) ?? { width: 128, height: 60 };
      positions.set(node.id, { x: nextColumnX, y: rowY, ...size });
      rowY += size.height + graphRowGap;
      columnWidth = Math.max(columnWidth, size.width);
    }
    nextColumnX += columnWidth + graphColumnGap;
    bottom = Math.max(bottom, rowY - graphRowGap);
  }
  const width = Math.max(600, nextColumnX - graphColumnGap + graphPadding);
  const height = bottom + graphPadding;
  if(surface==='table')return <><PageHeader title="Сервисы" actions={<Button icon="refresh" onClick={()=>setReload(v=>v+1)}>Обновить</Button>}/>
    <Tabs label="Представление сервисов" value={surface} onChange={value=>{setSurface(value);location.hash=`#/services?tab=${value==='graph'?'graph':'statistics'}`;}} items={[{value:'graph',label:'Граф'},{value:'table',label:'Статистика'}]}/>
    <ServiceStatistics client={client} reload={reload} onRetry={()=>setReload(v=>v+1)}/></>;
  return <><PageHeader title="Сервисы" actions={<><Button icon="refresh" onClick={()=>setReload(v=>v+1)} loading={loading}>Обновить</Button></>}/>
      <Tabs label="Представление сервисов" value={surface} onChange={value => { setSurface(value); location.hash = `#/services?tab=${value === 'graph' ? 'graph' : 'statistics'}`; }} items={[{value:'graph',label:'Граф'},{value:'table',label:'Статистика'}]}/>
    {!loading && <div>
      <Metrics items={[
        { label: 'Трейсы в выборке', value: `${details.length} / ${total}` },
        { label: view === 'graph' ? 'Узлы' : 'Сервисы', value: view === 'graph' ? data.nodes.length : data.services.length },
        { label: 'Связи', value: data.edges.length, info: 'Направления вызовов между сервисами в загруженной выборке.' },
      ]}/>

    </div>}{failed>0&&<Message tone="warning">Не удалось включить часть трейсов ({failed}). Данные могут быть неполными.</Message>}
    {loading?<LoadingState>Собираем карту сервисов…</LoadingState>:!details.length?<EmptyState title="Нет данных о сервисах" description={failed?'Не удалось получить трейсы. Повторите загрузку.':'В текущем наборе нет трейсов.'} action={<RetryButton onClick={()=>setReload(v=>v+1)}/>}/>:<>

      {requestedFinding && audit && !requestedFindingMatch && <Message tone="warning">Проблема из ссылки больше недоступна. Выберите сервис, чтобы посмотреть текущие настройки.</Message>}
      {requestedFindingMatch && !requestedFindingNode && <Message tone="warning">Проблема найдена, но её сервис {requestedFindingMatch.edge.caller} отсутствует в текущей выборке. Настройки не подставлены из другого сервиса. Обновите выборку или откройте сервис вручную.</Message>}{details.some(t=>t.quality_issues.length>0)&&<Message tone="warning">Есть замечания к качеству данных ({details.filter(t=>t.quality_issues.length>0).length}). Граф может быть неполным.</Message>}
      {true&&<div className="filters"><input type="search" aria-label="Поиск сервиса или связи" placeholder="Найти сервис или связь" value={query} onChange={e=>setQuery(e.target.value)}/><label className="check-label"><input type="checkbox" checked={onlyProblems} onChange={e=>setOnlyProblems(e.target.checked)}/>Только проблемные связи</label></div>}
      {surface==='graph'&&<div className="graph-layout graph-layout--full"><Panel title="Граф сервисов"><div className="service-graph-scroll">{nodes.length?<svg ref={graph} className="service-graph" role="group" aria-label="Интерактивный граф сервисов"
          viewBox={`0 0 ${width} ${height}`} style={{ width: `${zoom * 100}%`, minWidth: width * zoom }}>
          <defs><marker id="graph-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0 0L10 5L0 10z" fill="context-stroke"/>
          </marker></defs>
          {visibleEdges.map(edge => {
            const from = positions.get(edge.from.id)!;
            const to = positions.get(edge.to.id)!;
            const startX = from.x + from.width;
            const startY = from.y + from.height / 2;
            const endY = to.y + to.height / 2;
            const midX = (startX + to.x) / 2;
            const midY = (startY + endY) / 2;
            const path = `M${startX},${startY} Q${midX},${startY} ${to.x},${endY}`;
            return <g key={edge.id} role="button" tabIndex={0} aria-label={serviceCallLabel(edge)}
              className={`graph-edge ${edge.errors ? 'has-problem' : ''} ${selected === edge.id ? 'is-selected' : ''}`}
              onClick={() => setSelected(edge.id)} onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(edge.id); }
              }}>
              <path className="edge-hit" d={path}/>
              <path className="edge-line" d={path} markerEnd="url(#graph-arrow)"/>
              <text x={midX} y={midY - 10} textAnchor="middle">{edge.count} вызовов</text>
            </g>;
          })}
          {nodeLabels.map(([id, name, meta]) => {
            const position = positions.get(id)!;
            const node = nodes.find(item => item.id === id)!;
            return <g key={id} data-node-id={id} transform={`translate(${position.x},${position.y})`} role="button"
              tabIndex={0} aria-label={`${node.external ? 'Адресат' : 'Сервис'} ${name}`}
              className={`graph-node ${selected === id ? 'is-selected' : ''}`}
              onClick={() => setSelected(id)} onKeyDown={event => {
                if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); setSelected(id); }
              }}>
              <rect width={position.width} height={position.height} rx="7"/>
              <circle cx="16" cy="20" r="4" fill={serviceColors[name] ?? '#96a2ff'}/>
              <text x="29" y="1.9em">{name}</text>
              <text className="graph-node-meta" x="16" y="3.6em">{meta}</text>
            </g>;
          })}
        </svg>:<EmptyState title="Связи не найдены" description="Измените поиск или отключите фильтр проблем."/>}</div><div className="graph-controls"><Button onClick={()=>setZoom(v=>Math.max(.65,v-.15))}>−</Button><span>{Math.round(zoom*100)}%</span><Button onClick={()=>setZoom(v=>Math.min(1.8,v+.15))}>+</Button><Button onClick={()=>setZoom(1)}>Сбросить масштаб</Button></div></Panel>{<Panel title={edge?'Выбранная связь':selectedNode?.external?'Выбранный адресат':'Выбранный сервис'} meta={selected ? <Button variant="ghost" icon="close" aria-label="Снять выбор сервиса" onClick={()=>setSelected('')}/> : undefined}><div className="explore-inspector">{selected && !edge && !selectedNode && <Message>Ранее выбранный сервис или связь больше недоступны в этой выборке.</Message>}{edge?<><h3><ServiceCall call={edge}/></h3><p className="muted">Настройки обращения к этому сервису находятся в {edge.caller}.</p><dl><div><dt>Вызовы</dt><dd>{edge.count}</dd></div><div><dt>Ошибки</dt><dd>{edge.errors}</dd></div><div><dt>Результат неизвестен</dt><dd>{edge.unknown}</dd></div><div><dt>Медиана</dt><dd>{formatMs(edge.median)}</dd></div><div><dt>95-й перцентиль</dt><dd>{formatMs(edge.p95)}</dd></div></dl>{findings.map(f=><div className="edge-finding" key={f.finding_id}><RuleLabel id={f.rule_id}/><p>{f.title}</p><Button onClick={()=>onPlan(f)} disabled={pending}>Посмотреть план</Button></div>)}</>:selectedService?<><div className="service-identity"><span className="muted">Сервис</span><strong>{selectedService.name}</strong></div><Metrics items={[
          { label: 'Операций', value: selectedService.count },
          { label: 'Медиана', value: formatMs(selectedService.median) },
          { label: '95-й перцентиль', value: formatMs(selectedService.p95) },
          { label: 'Ошибки', value: selectedService.errors },
          { label: 'Результат неизвестен', value: selectedService.unknown },
        ]}/><ServiceConfig key={selectedService.name} client={client} service={selectedService.name} environmentId={demo ? 'demo-shop' : environmentId} initialCall={requestedFindingMatch?.edge} localOnly={demo}/></>:!selectedNode?<p className="muted">Выберите сервис на графе, чтобы посмотреть его показатели и настроить параметры вызовов.</p>:<p className="muted">Спаны этого сервиса не получены. Данные об обращениях к нему доступны на связи.</p>}</div></Panel>}</div>}
<p className="panel-note">Показатели рассчитаны по загруженным трейсам. На графе отображается до 40 сервисов.</p></>}
  </>;
}
