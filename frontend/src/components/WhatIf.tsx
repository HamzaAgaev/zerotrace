import { useEffect, useMemo, useState } from 'react';
import { usePersistentState, usePersistentTab } from '../state';
import { SpanOperation, endpointLabel } from './HttpText';
import { PairBars } from './Metrics';
import { Select } from './Select';
import type { Client, ProjectionResult, Span, TraceDetail } from '../api/types';
import { downloadJson } from '../analysis';
import { Badge, Button, LoadingState, Message, Panel, TableScroll } from './ui';
import { Tabs } from './Tabs';
import { duration } from '../lib';
import { formatMs, formatPercent } from '../presentation';

const maxBranches = 8;
const reason = (code: string) => ({ cross_clock_uncertain: 'Используется допущение о согласованности временных меток источников.', async_or_links: 'В трейсе есть асинхронные связи, которые модель не поддерживает.', temporal_bounds: 'Границы дочерних спанов не согласованы с родительскими.', missing_parent: 'В трейсе отсутствуют связи с родительскими спанами.', cycle: 'В дереве трейса обнаружен цикл.', no_gain_in_selected_scope: 'Выбранное ускорение не сокращает критический путь.', partial_trace_scope: 'Рассчитана только доступная часть трейса.', timestamps_assumed_aligned: 'Временные метки считаются согласованными пользователем.' }[code] ?? code.replaceAll('_', ' '));
function spanOptionLabel(span: Span) { return `${endpointLabel(span.service, span.name)} · смещение ${duration(span.start_offset_ns)} · длительность ${duration(span.duration_ns)}`; }

export function WhatIf({ trace, client, demo }: { trace: TraceDetail; client: Client; demo: boolean }) {
  const traceKey = JSON.stringify(trace.trace_ref);
  const [tab, setTab] = usePersistentTab(`projection:${traceKey}:tab`, 'scenario', ['scenario', 'candidates', 'about'], 'projection');
  const [branches, setBranches] = usePersistentState<Record<string, number>>(`projection:${traceKey}:branches`, {});
  const [pick, setPick] = usePersistentState(`projection:${traceKey}:pick`, trace.spans.find(span => span.parent_span_id)?.span_id ?? '');
  const changes = useMemo(() => Object.entries(branches).map(([span_id, speedup_percent]) => ({ span_id, speedup_percent })), [branches]);
  const requestKey = JSON.stringify([trace.trace_ref, changes]);
  const [state, setState] = useState<{ key: string; data: ProjectionResult | null; error: string; pending: boolean }>({ key: '', data: null, error: '', pending: true });
  const result = state.key === requestKey ? state.data : null;
  const error = state.key === requestKey ? state.error : '';
  const pending = !demo && (state.key !== requestKey || state.pending);
  useEffect(() => {
    if (demo) {
      setState({ key: requestKey, data: null, pending: false, error: 'Подключите сервер, чтобы получить прогноз по этому трейсу. Демоданные не содержат серверного расчёта.' });
      return;
    }
    const controller = new AbortController();
    setState({ key: requestKey, data: null, error: '', pending: true });
    const timer = window.setTimeout(() => {
      void client.projectTrace(trace.trace_ref, changes, controller.signal).then(data => {
        if (!controller.signal.aborted) setState({ key: requestKey, data, error: '', pending: false });
      }).catch(cause => {
        if (!controller.signal.aborted) setState({ key: requestKey, data: null, pending: false,
          error: cause instanceof Error ? cause.message : 'Не удалось рассчитать прогноз.' });
      });
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [client, trace.trace_ref, changes, demo, requestKey]);
  const conflicts = (id: string) => { const map = new Map(trace.spans.map(s => [s.span_id, s])); const ancestor = (child: string, parent: string) => { const seen = new Set<string>(); let current = map.get(child); while (current?.parent_span_id && !seen.has(current.span_id)) { seen.add(current.span_id); if (current.parent_span_id === parent) return true; current = map.get(current.parent_span_id); } return false; }; return Object.keys(branches).some(other => other === id || ancestor(id, other) || ancestor(other, id)); };
  const count = Object.keys(branches).length, atLimit = count >= maxBranches, pickConflict = !!pick && conflicts(pick);
  const add = (id: string) => { if (!atLimit && !conflicts(id)) { setBranches(old => ({ ...old, [id]: 50 })); setTab('scenario'); } };
  const available = result?.status === 'available' && result.baseline_ns !== null && result.projected_ns !== null && result.gain_ns !== null;
  return <div className="whatif-content">
    <Message>Это прогноз ускорения, а не измерение после изменения приложения.</Message>
    <Tabs label="Разделы моделирования" value={tab} onChange={setTab} items={[{ value:'scenario', label:'Настройка' }, { value:'candidates', label:'Что можно ускорить' }, { value:'about', label:'Как считается' }]} />
    <div hidden={tab !== 'scenario'}><div className="policy-layout"><Panel title="Настройка ускорения"><div className="scenario-editor"><p className="muted">Можно добавить до 8 непересекающихся веток.</p><div className="scenario-add"><Select aria-label="Ветка для ускорения" value={pick} onChange={setPick}>{trace.spans.filter(s => s.parent_span_id).map(span => <option disabled={conflicts(span.span_id)} key={span.span_id} value={span.span_id}>{spanOptionLabel(span)}</option>)}</Select><Button disabled={!pick || atLimit || pickConflict} onClick={() => add(pick)}>Добавить</Button></div>{Object.entries(branches).map(([id, percent]) => { const span = trace.spans.find(s => s.span_id === id); return <div className="scenario-branch" key={id}><div><strong>{span?.service ?? 'Вызов недоступен'}</strong><span>{span ? <SpanOperation span={span}/> : 'Спан больше не найден'}</span><Button variant="ghost" icon="close" aria-label="Убрать ветку" onClick={() => setBranches(old => { const next={...old}; delete next[id]; return next; })}/></div><label>Сократить длительность на <strong>{percent}%</strong><input type="range" min="0" max="100" step="5" value={percent} onChange={e => setBranches(old => ({...old,[id]:Number(e.target.value)}))}/></label></div>; })}{!count && <p className="muted">Добавьте вызов или откройте варианты ускорения.</p>}<Button onClick={() => setBranches({})}>Сбросить настройку</Button></div></Panel><Panel title="Прогноз длительности запроса"><div className="budget-preview">{pending ? <LoadingState>Сервер рассчитывает прогноз…</LoadingState> : error ? <Message tone="warning">{error}</Message> : available ? <><Badge tone="accent">Прогноз, не измерение</Badge><PairBars label="Длительность запроса" labels={['Факт','Прогноз']} values={[{amount:Number(BigInt(result.baseline_ns!))/1e6,text:formatMs(Number(BigInt(result.baseline_ns!))/1e6)},{amount:Number(BigInt(result.projected_ns!))/1e6,text:formatMs(Number(BigInt(result.projected_ns!))/1e6)}]}/><p>Возможное сокращение: <strong>{formatMs(Number(BigInt(result.gain_ns!))/1e6)}</strong> ({formatPercent(result.gain_ratio)})</p><Button icon="download" onClick={() => downloadJson('zerotrace-scenario.json', result)}>Скачать прогноз</Button></> : <Message tone="warning">{result?.applicability.reason_codes.map(reason).join(' ') || 'Сервер не может рассчитать этот прогноз.'}</Message>}{result && [...result.warnings, ...result.applicability.reason_codes].map(code => <p className="muted" key={code}>{reason(code)}</p>)}</div></Panel></div></div>
    {tab === 'candidates' && (pending ? <LoadingState>Загружаем варианты с сервера…</LoadingState> : error ? <Message tone="warning">{error}</Message> : result?.candidates.length ? <Panel title="Что можно ускорить" meta={<Badge>Оценка сервера</Badge>}><TableScroll><table className="explore-table"><thead><tr><th>Ветка</th><th>Сокращение 25%</th><th>50%</th><th>100%</th><th></th></tr></thead><tbody>{result.candidates.slice(0, 20).map(item => { const span=trace.spans.find(s=>s.span_id===item.span_id); return <tr key={item.span_id}><td>{span ? <>{span.service}<small className="cell-note"><SpanOperation span={span}/></small></> : item.target}</td><td>{formatMs(Number(BigInt(item.gain25_ns))/1e6)}</td><td>{formatMs(Number(BigInt(item.gain50_ns))/1e6)}</td><td>{formatMs(Number(BigInt(item.gain100_ns))/1e6)}</td><td><Button disabled={atLimit || conflicts(item.span_id)} onClick={() => add(item.span_id)}>Добавить</Button></td></tr>; })}</tbody></table></TableScroll></Panel> : <Message tone="warning">Варианты ускорения недоступны: {result?.applicability.reason_codes.map(reason).join(' ') || 'нет доступных веток'}.</Message>)}
    {tab === 'about' && <Panel title="Как считается прогноз"><div className="info-block">{result?.assumptions.map(text => <p key={text}>{text}</p>) ?? <p>Описание предположений будет показано после ответа сервера.</p>}<p>Можно выбрать до восьми непересекающихся веток. Это предварительный прогноз, а не измерение.</p></div></Panel>}
  </div>;
}
