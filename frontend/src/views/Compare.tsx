import { useEffect, useState } from 'react';
import type { Client, TraceSummary } from '../api/types';
import { cohortStats, compareCohorts, downloadJson } from '../analysis';
import { Badge, Button, Message, Panel, TableScroll, LoadingState } from '../components/ui';
import { traceHref } from '../lib';
import { formatCount, formatMs, formatPercent } from '../presentation';
export type ComparisonSelection = Record<string, { group: 'a' | 'b'; trace: TraceSummary }>;
export function Compare({ client, selection }: {client: Client; selection: ComparisonSelection}) {
  const [loaded, setLoaded] = useState<{a: TraceSummary[]; b: TraceSummary[]} | null>(null);
  const [missing, setMissing] = useState<{group: 'a'|'b'; trace: TraceSummary}[]>([]);
  const [error, setError] = useState('');
  useEffect(()=>{
    const controller = new AbortController();setLoaded(null);setMissing([]);setError('');
    const items = Object.values(selection);
    Promise.allSettled(items.map(async item=>({group:item.group, trace:item.trace,
      detail: await client.getTrace(item.trace.trace_ref,controller.signal,{includeFindings:false})})))
      .then(results=>{
        if(controller.signal.aborted)return;
        const available = results.flatMap((result,index)=>result.status==='fulfilled'
          ? [{group:items[index].group, trace:result.value.detail.summary}] : []);
        const unavailable = results.flatMap((result,index)=>result.status==='rejected'
          ? [{group:items[index].group, trace:items[index].trace}] : []);
        setMissing(unavailable);
        setLoaded({a:available.filter(i=>i.group==='a').map(i=>i.trace),b:available.filter(i=>i.group==='b').map(i=>i.trace)});
      })
      .catch(()=>{if(!controller.signal.aborted)setError('Не удалось проверить выбранные трейсы. Вернитесь к выбору и обновите группы.');});
    return ()=>controller.abort();
  },[client,selection]);
  if(error)return <Message tone="danger">{error}</Message>;
  if(!loaded)return <LoadingState>Проверяем выбранные трейсы…</LoadingState>;
  if(missing.length) return <Panel title="Результат сравнения"><div className="lab-body">
    <Message tone="warning">Часть выбранных трейсов больше недоступна. Они не исключены молча: уберите их из групп или обновите список, затем повторите сравнение.</Message>
    <ul className="missing-refs">{missing.map(({group,trace})=><li key={traceHref(trace.trace_ref)}>
      Группа {group.toUpperCase()} · <a className="mono" href={traceHref(trace.trace_ref)}>{trace.trace_id}</a>
      <small className="cell-note">{trace.trace_ref.environment_id}, поколение {trace.trace_ref.generation}, версия {trace.trace_ref.revision}</small>
    </li>)}</ul>
    <p className="muted">Доступно записей: группа A — {loaded.a.length}, группа B — {loaded.b.length}. Сравнение не показано, пока состав групп не станет полным.</p>
  </div></Panel>;
  const {a,b}=loaded;const issue=compareCohorts(a,b);
  if(issue)return <Message tone="warning">{issue}</Message>;
  const sa=cohortStats(a),sb=cohortStats(b);
  return <Panel title="Результат сравнения" meta={<Badge>{a.length + b.length} трейсов</Badge>}>
    <div className="lab-body"><p>Сравните длительности и ошибки двух групп запросов одного эндпоинта. Разница между группами сама по себе не доказывает эффект изменения настроек.</p>
    {Math.min(a.length,b.length)<20&&<Message tone="warning">Малая выборка: группа A — {formatCount(a.length)}, группа B — {formatCount(b.length)}. Медиана и перцентили на такой выборке нестабильны.</Message>}
    <TableScroll><table><thead><tr><th>Показатель</th><th>Группа A</th><th>Группа B</th><th>Разница B − A</th></tr></thead><tbody>
    <tr><td>Количество запросов</td><td>{formatCount(sa.count)}</td><td>{formatCount(sb.count)}</td><td>{formatCount(sb.count-sa.count)}</td></tr>
    <tr><td>Медиана длительности</td><td>{formatMs(sa.median)}</td><td>{formatMs(sb.median)}</td><td>{formatMs(sa.median===null||sb.median===null?null:sb.median-sa.median)}</td></tr>
    <tr><td>95-й перцентиль</td><td>{formatMs(sa.p95)}</td><td>{formatMs(sb.p95)}</td><td>{formatMs(sa.p95===null||sb.p95===null?null:sb.p95-sa.p95)}</td></tr>
    <tr><td>Запросы с ошибкой</td><td>{formatCount(sa.errors)} / {formatCount(sa.count)}</td><td>{formatCount(sb.errors)} / {formatCount(sb.count)}</td><td>{formatCount(sb.errors-sa.errors)}</td></tr>
    <tr><td>Результат неизвестен</td><td>{formatCount(sa.unknown)}</td><td>{formatCount(sb.unknown)}</td><td>{formatCount(sb.unknown-sa.unknown)}</td></tr>
    </tbody></table></TableScroll>
    <p className="muted">95% запросов завершились за время 95-го перцентиля или быстрее. Доля запросов с ошибкой: A — {formatPercent(sa.count?sa.errors/sa.count:null)}, B — {formatPercent(sb.count?sb.errors/sb.count:null)}.</p>
    <p className="muted">Ограничения выборки: сравнение использует только выбранные трейсы и не заменяет случайную выборку из всего потока. Неизвестный результат не считается успешным.</p>
    <details><summary>Выбранные трейсы</summary>{(['a','b'] as const).map(group=><div key={group}><h3>Группа {group.toUpperCase()}</h3>{Object.values(selection).filter(i=>i.group===group).map(i=><p key={traceHref(i.trace.trace_ref)}><a className="mono" href={traceHref(i.trace.trace_ref)}>{i.trace.trace_id}</a><small className="cell-note">{i.trace.trace_ref.environment_id}, поколение {i.trace.trace_ref.generation}, версия {i.trace.trace_ref.revision}</small></p>)}</div>)}</details>
    <Button icon="download" onClick={()=>downloadJson('zerotrace-comparison.json',{scope:'selected-traces',a:{refs:Object.values(selection).filter(i=>i.group==='a').map(i=>i.trace.trace_ref),stats:sa},b:{refs:Object.values(selection).filter(i=>i.group==='b').map(i=>i.trace.trace_ref),stats:sb},causal_claim:false})}>Скачать JSON-отчёт</Button></div>
  </Panel>;
}
