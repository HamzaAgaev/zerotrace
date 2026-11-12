import { useEffect,useState } from 'react';
import type { Client,PeriodMetrics } from '../api/types';
import { usePersistentState } from '../state';
import { validPeriod } from '../api/periodMetrics';
import { PeriodSelect } from './PeriodSelect';
import { Metrics } from './Metrics';
import { Message, RetryButton } from './ui';
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
  const [period,setPeriod]=usePersistentState('traces:metrics-period',30,validPeriod);
  const {data,error,loading}=usePeriodMetrics(client,period,reload), stats=data?.summary;
  return <section className="period-summary" aria-label="Статистика всей системы" aria-busy={loading}>
    <div className="statistics-toolbar"><span className="muted">Вся система</span><PeriodSelect value={period} onChange={setPeriod}/></div>
    <Metrics items={[
      {label:'Запросы',value:stats?.count ?? '—'},
      {label:'Успешные запросы',value:formatPercent(stats?.success_rate), info:'Доля успешных среди запросов с известным результатом.'},
      {label:'Медиана времени ответа',value:stats?.median_ns == null ? '—' : duration(stats.median_ns)},
      {label:'95-й перцентиль времени ответа',value:stats?.p95_ns == null ? '—' : duration(stats.p95_ns),info:'95% запросов завершились за это время или быстрее.'},
    ]}/>
    {!!stats?.unknown && <p className="panel-note">Результат неизвестен: {stats.unknown}. Эти запросы не входят в долю успешных.</p>}
    {error && <Message tone="warning" action={<RetryButton onClick={onRetry}/>}>{error}</Message>}
  </section>;
}
