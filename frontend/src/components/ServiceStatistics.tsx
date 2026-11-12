import { LatencyHistogram } from './StatisticsCharts';
import { usePersistentState } from '../state';
import type { Client } from '../api/types';
import { validPeriod } from '../api/periodMetrics';
import { PeriodSelect } from './PeriodSelect';
import { usePeriodMetrics } from './PeriodSummary';
import { Select } from './Select';
import { Metrics } from './Metrics';
import { Field,Panel,LoadingState,Message,RetryButton,EmptyState } from './ui';
import { duration } from '../lib';
export function ServiceStatistics({client,reload,onRetry}:{client:Client;reload:number;onRetry:()=>void}) {
  const [period,setPeriod]=usePersistentState('services:metrics-period',30,validPeriod);
  const [selection,setSelection]=usePersistentState('services:stats-selection',{service:'',endpoint:''});
  const {data,loading,error}=usePeriodMetrics(client,period,reload);
  const service=selection.service || data?.services[0]?.service || '';
  const endpoints=data?.endpoints.filter(row=>row.service===service) ?? [];
  const stats=selection.endpoint ? endpoints.find(row=>row.endpoint===selection.endpoint) : data?.services.find(row=>row.service===service);
  return <Panel title="Статистика сервиса" meta={<PeriodSelect value={period} onChange={setPeriod}/>}>
    <div className="service-statistics">
      <div className="statistics-filters">
        <Field label="Сервис"><Select aria-label="Сервис для статистики" value={service} onChange={value=>setSelection({service:value,endpoint:''})}>
          {selection.service && !data?.services.some(s=>s.service===selection.service) && <option value={selection.service}>{selection.service}</option>}
          {(data?.services ?? []).map(row=><option key={row.service} value={row.service}>{row.service}</option>)}
        </Select></Field>
        <Field label="Эндпоинт"><Select aria-label="Эндпоинт для статистики" value={selection.endpoint} onChange={endpoint=>setSelection({service,endpoint})}>
          <option value="">Все эндпоинты</option>
          {selection.endpoint && !endpoints.some(e=>e.endpoint===selection.endpoint) && <option value={selection.endpoint}>{selection.endpoint}</option>}
          {endpoints.map(row=><option key={row.endpoint} value={row.endpoint}>{row.endpoint}</option>)}
        </Select></Field>
      </div>
      {loading ? <LoadingState>Загружаем статистику…</LoadingState> : error ? <Message tone="danger" action={<RetryButton onClick={onRetry}/>}>{error}</Message> : !stats ? <EmptyState title="Нет операций за выбранный период" description="Выберите другой период или сервис."/> : <><Metrics items={[
        {label:'Операции',value:stats.count},{label:'Медиана',value:stats.median_ns===null?'—':duration(stats.median_ns)},
        {label:'95-й перцентиль',value:stats.p95_ns===null?'—':duration(stats.p95_ns)},
        {label:'Ошибки',value:stats.errors},{label:'Результат неизвестен',value:stats.unknown},
        {label:'Время вне вложенных вызовов',value:duration(stats.own_ns)},
      ]}/><section className="service-latency"><h3>Распределение времени ответа</h3><p className="muted">{selection.endpoint || 'Все эндпоинты'}: полное время обработки запроса сервисом, включая вложенные вызовы.</p>{stats.latency_histogram?<LatencyHistogram buckets={stats.latency_histogram}/>:<p className="muted">Сервер не предоставил распределение времени.</p>}</section></>}
    </div>
  </Panel>;
}
