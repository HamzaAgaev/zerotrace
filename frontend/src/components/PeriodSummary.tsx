import { PeriodTrends } from './StatisticsCharts';
import { useEffect,useState } from 'react';
import type { Client,PeriodMetrics } from '../api/types';
import { usePersistentState } from '../state';
import { validPeriod } from '../api/periodMetrics';
import { PeriodSelect } from './PeriodSelect';
import { Metrics } from './Metrics';
import { Button, Message, RetryButton, Panel } from './ui';
import { duration } from '../lib';
import { formatPercent } from '../presentation';
export function usePeriodMetrics(client:Client,period:number,reload:number) {
  const [state,setState]=useState<{data?:PeriodMetrics;error?:string;loading:boolean}>({loading:true});
  useEffect(()=>{
    const ctrl=new AbortController(); setState({loading:true});
    client.getMetrics(period,ctrl.signal).then(data=>{if(!ctrl.signal.aborted)setState({data,loading:false});})
      .catch(e=>{if(!ctrl.signal.aborted)setState({error:e.message,loading:false});});
    return()=>ctrl.abort();
  },[client,period,reload]);
  return state;
}
export function PeriodSummary({client,reload,onRetry}:{client:Client;reload:number;onRetry:()=>void}) {
  const [expanded,setExpanded]=usePersistentState('traces:statistics-expanded',false);
  return <section className="period-summary" aria-label="Статистика всей системы">
    <Button variant="primary" aria-expanded={expanded} aria-controls="trace-period-statistics" onClick={()=>setExpanded(value=>!value)}>{expanded?'Скрыть статистику системы':'Показать статистику системы'}</Button>
    {expanded&&<div id="trace-period-statistics"><PeriodSummaryContent client={client} reload={reload} onRetry={onRetry}/></div>}
  </section>;
}
function PeriodSummaryContent({client,reload,onRetry}:{client:Client;reload:number;onRetry:()=>void}) {
  const [period,setPeriod]=usePersistentState('traces:metrics-period',30,validPeriod);
  const {data,error,loading}=usePeriodMetrics(client,period,reload), stats=data?.summary;
  return <Panel title="Статистика системы" meta={<PeriodSelect value={period} onChange={setPeriod}/>}>
    <div className="statistics-summary-body" aria-busy={loading}>
      <Metrics items={[
        {label:'Запросы',value:stats?.count ?? '—'},
        {label:'Успешные запросы',value:formatPercent(stats?.success_rate),tone:stats?.success_rate==null?'neutral':'success'},
        {label:'Медиана времени ответа',value:stats?.median_ns == null ? '—' : duration(stats.median_ns)},
        {label:'95-й перцентиль времени ответа',value:stats?.p95_ns == null ? '—' : duration(stats.p95_ns)},
      ]}/>
      {data&&<PeriodTrends data={data}/>}
      {loading&&<p className="muted" role="status">Загружаем статистику…</p>}
      {error&&<Message tone="warning" action={<RetryButton onClick={onRetry}/>}>{error}</Message>}
    </div>
  </Panel>;
}
