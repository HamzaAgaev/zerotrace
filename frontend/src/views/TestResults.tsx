import { TraceTable } from '../components/TraceTable';
import { Select } from '../components/Select';
import { CopyId } from '../components/CopyId';
import { RunCharts } from '../components/RunCharts';
import { useEffect, useState } from 'react';
import type { Client, LabRun, RunServices, TraceSummary } from '../api/types';
import { runStatistics } from '../runStatistics';
import { traceHref } from '../lib';
import { BackLink, Badge, Button, Field, Icon, LoadingState, Message, PageHeader, Panel } from '../components/ui';
import { Metrics } from '../components/Metrics';

const time = (value: number | null) => value === null ? '—' : `${value.toLocaleString('ru-RU', {maximumFractionDigits:1})} мс`;
export function TestResults({client,runId}:{client:Client|null;runId:string}) {
  const [run,setRun]=useState<LabRun|null>(null);
  const [error,setError]=useState('');
  const [services,setServices]=useState<RunServices|null>(null);
  const [servicesError,setServicesError]=useState('');
  const [servicesLoading,setServicesLoading]=useState(false);
  const [serviceFilter,setServiceFilter]=useState('');
  const [problemFilter,setProblemFilter]=useState('');
  const [page,setPage]=useState(1);
  const [limit,setLimit]=useState(10);
  useEffect(()=>{setPage(1);setServiceFilter('');setProblemFilter('');},[runId]);
  const [reload,setReload]=useState(0);
  useEffect(()=>{
    if(!client)return;
    const controller=new AbortController(); let timer:ReturnType<typeof setTimeout>;
    async function load(){try{
      const next=await client!.getRun(runId,controller.signal);
      if(controller.signal.aborted)return;
      setRun(next);setError('');
      if(next.status==='running'||next.trace_collection.status==='waiting')timer=setTimeout(load,1500);
    }catch(cause){if(!controller.signal.aborted)setError(cause instanceof Error?cause.message:'Не удалось загрузить тест.');}}
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[client,runId,reload]);
  const refsKey=JSON.stringify(run?.trace_collection.trace_refs??[]);
  useEffect(()=>{
    if(!client||!run)return;
    const controller=new AbortController();setServicesLoading(true);setServicesError('');setServices(null);
    client.getRunServices(runId,controller.signal).then(value=>{if(!controller.signal.aborted)setServices(value);})
      .catch(cause=>{if(!controller.signal.aborted)setServicesError(cause instanceof Error?cause.message:'Не удалось загрузить проблемы аудита.');})
      .finally(()=>{if(!controller.signal.aborted)setServicesLoading(false);});
    return()=>controller.abort();
  },[client,runId,refsKey,reload,!!run]);
  const findings=services?.findings??[];
  const serviceNames=[...new Set(findings.flatMap(f=>[f.edge.caller,f.edge.callee]).filter(Boolean))].sort();
  const problems=new Map<string,typeof findings>();
  for(const finding of findings) for(const ref of finding.trace_refs) {
    const key=traceHref(ref);
    problems.set(key,[...(problems.get(key)??[]),finding]);
  }
  const matches=(f:typeof findings[number])=>(!serviceFilter||f.edge.caller===serviceFilter||f.edge.callee===serviceFilter)&&(!problemFilter||f.occurrence_id===problemFilter);
  const visibleRefs=(run?.trace_collection.trace_refs??[]).filter(ref=>{
    if(!serviceFilter&&!problemFilter)return true;
    return (problems.get(traceHref(ref))??[]).some(matches);
  });
  const pages=Math.max(1,Math.ceil(visibleRefs.length/limit));
  const shownPage=Math.min(page,pages);
  const pageRefs=visibleRefs.slice((shownPage-1)*limit,shownPage*limit);
  const pageRefsKey=JSON.stringify(pageRefs);
  const [tracePage,setTracePage]=useState<{key:string;rows:TraceSummary[];error:string}|null>(null);
  useEffect(()=>{
    if(!client)return;
    const controller=new AbortController();
    const refs=JSON.parse(pageRefsKey) as typeof pageRefs;
    async function load(){
      const rows:TraceSummary[]=[];
      try {
        for(let i=0;i<refs.length;i+=5){
          const batch=await Promise.all(refs.slice(i,i+5).map(ref=>client!.getTrace(ref,controller.signal,{includeFindings:false})));
          rows.push(...batch.map(trace=>trace.summary));
        }
        if(!controller.signal.aborted)setTracePage({key:pageRefsKey,rows,error:''});
      } catch(cause) {
        if(!controller.signal.aborted)setTracePage({key:pageRefsKey,rows:[],error:cause instanceof Error?cause.message:'Не удалось загрузить трейсы.'});
      }
    }
    void load();
    return()=>controller.abort();
  },[client,pageRefsKey,reload]);
  const traceRows=tracePage?.key===pageRefsKey?tracePage:null;
  const findingCounts=services?new Map((run?.trace_collection.trace_refs??[]).map(ref=>[traceHref(ref),problems.get(traceHref(ref))?.length??0])):undefined;
  const firstPage=Math.max(1,Math.min(shownPage-2,pages-4));
  const stats=runStatistics(run?.results??[]);
  return <>
    <PageHeader context={<BackLink href="#/lab">К тестовому стенду</BackLink>} title="Результаты теста" />
    {!client&&<Message tone="warning">Подключите сервер в настройках подключения.</Message>}
    {error&&<Message tone="danger" action={<Button onClick={()=>setReload(v=>v+1)}>Повторить загрузку</Button>}>{error}</Message>}
    {!run&&client&&!error&&<LoadingState>Загружаем результаты…</LoadingState>}
    {run&&<div className="test-results">
      <Panel title={<span className="test-run-title"><span className="test-header-label">Test ID</span> <CopyId label="ID теста" value={run.run_id}/><span className="test-header-seed"><span className="test-header-label">Seed</span><CopyId label="Seed" value={String(run.seed)}/></span></span>} meta={<Badge tone={run.status==='failed'?'danger':run.status==='running'?'warning':'neutral'}>{run.status==='running'?'Выполняется':run.status==='failed'?'Запуск прерван':'Запуск завершён'}</Badge>}>
        <div className="lab-body">
          <Metrics items={[
            {label:'Время запуска',info:'Дата и время начала теста в часовом поясе вашего браузера.',value:<time className="test-start-metric" dateTime={run.started_at}>{new Date(run.started_at).toLocaleString('ru-RU')}</time>},
            {label:'Медиана',value:stats.count?time(stats.median):'—',info:'Половина запросов завершилась за это время или быстрее.'},
            {label:'95-й перцентиль',value:stats.count?time(stats.p95):'—',info:'95% запросов завершились за это время или быстрее.'},
          ]}/>
          {run.results===undefined?<Message tone="warning">Для этого запуска подробные результаты запросов не сохранены.</Message>:stats.count===0?<p className="muted">Первые результаты появятся после завершения запросов.</p>:<>
            {stats.count!==run.completed_requests&&<Message tone="warning">Подробные результаты доступны для {stats.count} из {run.completed_requests} выполненных запросов.</Message>}
          </>}
          {run.status==='running'&&<progress aria-label="Выполнение теста" value={run.completed_requests} max={run.request_count}/>}
        </div>
      </Panel>
      {stats.count>0&&<RunCharts stats={stats}/>}
      <Panel title="Трейсы теста" className="trace-table-panel"><div className="lab-body">
        <div className="test-trace-filters">
          <Field label="Сервис с проблемой"><Select value={serviceFilter} disabled={servicesLoading||!services} onChange={value=>{setServiceFilter(value);setPage(1);}}>
            <option value="">Все сервисы</option>
            {serviceNames.map(service=><option key={service} value={service}>{service}</option>)}
          </Select></Field>
          <Field label="Проблема"><Select value={problemFilter} disabled={servicesLoading||!services} onChange={value=>{setProblemFilter(value);setPage(1);}}>
            <option value="">Все проблемы</option>{findings.map(f=><option key={f.occurrence_id} value={f.occurrence_id}>{f.title} ({f.edge.caller} → {f.edge.callee}, {f.edge.operation})</option>)}
          </Select></Field>
          {(serviceFilter||problemFilter)&&<Button variant="ghost" onClick={()=>{setServiceFilter('');setProblemFilter('');setPage(1);}}>Сбросить</Button>}
        </div>
        {servicesLoading&&<p role="status">Проверяем собранные трейсы…</p>}
        {servicesError&&<Message tone="warning" action={<Button onClick={()=>setReload(v=>v+1)}>Повторить загрузку</Button>}>{servicesError}</Message>}
        {run.trace_collection.status==='waiting'&&<p className="muted">Ожидаем оставшиеся трейсы.</p>}
        {(run.trace_collection.status==='partial'||(services?.missing_traces??0)>0)&&<Message tone="warning">
          {run.trace_collection.status==='partial'?'Собраны не все трейсы. Статистика запросов рассчитана по результатам генератора нагрузки.':''}
          {(services?.missing_traces??0)>0?` Данные аудита недоступны для ${services!.missing_traces} трейсов.`:''}
        </Message>}
      </div>
        {visibleRefs.length ? !traceRows ? <LoadingState>Загружаем трейсы…</LoadingState> : traceRows.error
          ? <Message tone="warning" action={<Button onClick={()=>setReload(v=>v+1)}>Повторить загрузку</Button>}>{traceRows.error}</Message>
          : <TraceTable rows={traceRows.rows} findings={findingCounts}/>
          : <p className="muted test-trace-empty">{serviceFilter||problemFilter?'Нет трейсов с выбранными проблемами.':'Нет доступных ссылок на трейсы этого теста.'}</p>}
        <div className="table-footer trace-pagination">
          <span role="status">{visibleRefs.length?`${(shownPage-1)*limit+1}–${Math.min(shownPage*limit,visibleRefs.length)} из ${visibleRefs.length}`:'0 результатов'}</span>
          <Select aria-label="Трейсов на странице" value={String(limit)} onChange={value=>{setLimit(Number(value));setPage(1);}}>{[10,25,50].map(size=><option key={size} value={String(size)}>{size}</option>)}</Select>
          {pages>1&&<nav className="pagination" aria-label="Страницы трейсов теста">
            <Button variant="ghost" aria-label="Предыдущая страница" disabled={shownPage<=1} onClick={()=>setPage(shownPage-1)}><Icon name="arrow" className="rotate" size={16}/></Button>
            {Array.from({length:Math.min(5,pages)},(_,i)=>firstPage+i).map(n=><Button key={n} variant={n===shownPage?'primary':'ghost'} aria-current={n===shownPage?'page':undefined} onClick={()=>setPage(n)}>{n}</Button>)}
            <Button variant="ghost" aria-label="Следующая страница" disabled={shownPage>=pages} onClick={()=>setPage(shownPage+1)}><Icon name="arrow" size={16}/></Button>
          </nav>}
        </div>

      </Panel>
    </div>}
  </>;
}
