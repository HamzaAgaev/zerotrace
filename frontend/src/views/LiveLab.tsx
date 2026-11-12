import { fieldLabel, statusLabel, scenarioLabel } from '../presentation';
import { usePersistentState } from '../state';
import { HttpOperation } from '../components/HttpText';
import { RunComparisonControls } from '../components/RunComparisonControls';
import { Select } from '../components/Select';
import { useEffect, useMemo, useState } from 'react';
import type { Client, LabFault, LabRun, RunComparison, Stand } from '../api/types';
import { ApiError, createOrRecoverStand, isUncertainResponse } from '../api/client';
import { Badge, Button, EmptyState, Message, Panel, PageHeader, TableScroll, Field } from '../components/ui';
import { Metrics } from '../components/Metrics';
import { ServiceConfig } from '../components/ServiceConfig';

const nsMs = (value: string | number | null) => value === null ? '—' : `${(Number(value) / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 })}\u00A0мс`;
const formatMetric = (value: number | null) => value === null ? '—' : value.toLocaleString('ru-RU', { maximumFractionDigits: 2 });
const formatRate = (value: number | null) => value === null ? '—'
  : value.toLocaleString('ru-RU', { style: 'percent', maximumFractionDigits: 1 });
function validFaultDraft(value: unknown): value is LabFault {
  if (!value || typeof value !== 'object') return false;
  const draft = value as LabFault;
  const point = draft.failure_point;
  return typeof draft.service_id === 'string' && typeof draft.route === 'string'
    && Number.isFinite(draft.error_rate) && draft.error_rate >= 0 && draft.error_rate <= 1
    && Number.isInteger(draft.http_status) && draft.http_status >= 400 && draft.http_status <= 599
    && Number.isFinite(draft.delay_ms) && draft.delay_ms >= 0 && !!point
    && (point.mode === 'fixed' ? typeof point.boundary_id === 'string' : point.mode === 'random' && Array.isArray(point.boundary_ids) && point.boundary_ids.every(id => typeof id === 'string'));
}
type RunRequest = Parameters<Client['startRun']>[1];
const pendingRuns = new WeakMap<Client, { standId: string; request: RunRequest }>();

export function LiveLab({ client }: { client: Client | null }) {
  const [stand, setStand] = useState<Stand | null>(null);
  const [configService, setConfigService] = usePersistentState('lab:config-service', '');
  const [runs, setRuns] = useState<LabRun[]>([]);
  const [fault, setFault] = usePersistentState<LabFault>(`lab:${stand?.stand_id ?? 'new'}:fault`, { service_id: '', route: '', error_rate: .2, http_status: 503, delay_ms: 0, failure_point: { mode: 'fixed', boundary_id: 'before-first' } }, validFaultDraft);
  const [count, setCount] = usePersistentState(`lab:${stand?.stand_id ?? 'new'}:count`, '30'); const [concurrency, setConcurrency] = usePersistentState(`lab:${stand?.stand_id ?? 'new'}:concurrency`, '1');
  const [baseline, setBaseline] = usePersistentState(`lab:${stand?.stand_id ?? 'new'}:baseline`, ''); const [candidate, setCandidate] = usePersistentState(`lab:${stand?.stand_id ?? 'new'}:candidate`, ''); const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const pending = client ? pendingRuns.get(client) : undefined;
  const retry = pending?.standId === stand?.stand_id ? pending?.request : undefined;
  const selectedRoute = useMemo(() => stand?.services.find(service => service.service_id === fault.service_id)?.routes.find(route => route.route === fault.route), [stand, fault]);
  useEffect(() => {
    if (!stand?.services.length) return;
    const service = stand.services.find(item => item.service_id === fault.service_id) ?? stand.services[0];
    const route = service.routes.find(item => item.route === fault.route) ?? service.routes[0];
    if (!route) return;
    const ids = route.boundaries.map(item => item.boundary_id);
    const point = fault.failure_point;
    const failure_point: LabFault['failure_point'] = point.mode === 'fixed'
      ? { mode: 'fixed', boundary_id: ids.includes(point.boundary_id) ? point.boundary_id : ids[0] ?? '' }
      : { mode: 'random', boundary_ids: point.boundary_ids.filter(id => ids.includes(id)) };
    if (service.service_id !== fault.service_id || route.route !== fault.route || JSON.stringify(point) !== JSON.stringify(failure_point)) {
      setFault(old => ({ ...old, service_id: service.service_id, route: route.route, failure_point }));
    }
  }, [stand, fault, setFault]);
  const active = runs.find(run => run.stand_id === stand?.stand_id && (run.status === 'running' || run.trace_collection.status === 'waiting'));

  async function refresh(clearError = true, signal?: AbortSignal) {
    if (!client) return;
    if (clearError) setError('');
    try {
      const [activeStand, history] = await Promise.all([client.getActiveStand(signal), client.listRuns(signal)]);
      if (signal?.aborted) return;
      setStand(activeStand?.status === 'stopped' ? null : activeStand);
      setRuns(history.sort((a, b) => b.started_at.localeCompare(a.started_at)));
    } catch (cause) {
      if (clearError && !signal?.aborted) {
        setError(cause instanceof Error ? cause.message : 'Не удалось загрузить состояние лаборатории.');
      }
    }
  }
  useEffect(() => {
    const controller = new AbortController();
    setStand(null);
    setRuns([]);
    setComparison(null);
    void refresh(true, controller.signal);
    return () => controller.abort();
  }, [client]);
  useEffect(() => {
    if (!client || !active) return;
    const timer = window.setInterval(() => {
      client.getRun(active.run_id).then(run => setRuns(old => old.map(item => item.run_id === run.run_id ? run : item))).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [client, active?.run_id]);
  useEffect(() => {
    if (!client || !stand || !['building', 'stopping'].includes(stand.status)) return;
    const timer = window.setInterval(() => { void client.getActiveStand().then(value => setStand(value?.status === 'stopped' ? null : value)).catch(() => undefined); }, 1000);
    return () => window.clearInterval(timer);
  }, [client, stand?.stand_id, stand?.status]);
  async function build() {
    if (!client) return;
    setBusy(true);
    setError('');
    try {
      setStand(await createOrRecoverStand(client, undefined, setStand));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось собрать стенд.');
      await refresh(false);
    } finally {
      setBusy(false);
    }
  }
  async function saveFault() { if (!client || !stand || !selectedRoute || !validFaultDraft(fault)) return; setBusy(true); setError(''); try { setStand(await client.setFault(stand.stand_id, fault, stand.fault_revision, crypto.randomUUID())); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось применить сбой.'); if (cause instanceof ApiError && cause.status === 409) await refresh(false); } finally { setBusy(false); } }
  async function clearFault() { if (!client || !stand) return; setBusy(true); setError(''); try { setStand(await client.clearFault(stand.stand_id, stand.fault_revision, crypto.randomUUID())); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сбросить сбой.'); if (cause instanceof ApiError && cause.status === 409) await refresh(false); } finally { setBusy(false); } }
  async function run() {
    if (!client || !stand) return;
    setBusy(true); setError('');
    const request = retry ?? {
      request_id: crypto.randomUUID(),
      scenario_revision: stand.scenario_revision,
      expected_fault_revision: stand.fault_revision,
      config_revisions: stand.config_revisions,
      request_count: Number(count),
      concurrency: Number(concurrency),
    };
    pendingRuns.set(client, { standId: stand.stand_id, request });
    try {
      const result = await client.startRun(stand.stand_id, request);
      pendingRuns.delete(client);
      setRuns(old => [result, ...old.filter(item => item.run_id !== result.run_id)]);
    } catch (cause) {
      if (isUncertainResponse(cause)) {
        setError('Сервер не подтвердил запуск. Повторите запрос, чтобы получить состояние того же теста.');
      } else {
        pendingRuns.delete(client);
        setError(cause instanceof Error ? cause.message : 'Не удалось запустить нагрузку.');
        if (cause instanceof ApiError && cause.status === 409) await refresh(false);
      }
    }
    finally { setBusy(false); }
  }
  async function stop() {
    if (!client || !stand) return;
    setBusy(true);
    setError('');
    try {
      await client.stopStand(stand.stand_id);
      await refresh();
    } catch (cause) {
      await refresh();
      setError(cause instanceof Error ? cause.message : 'Не удалось остановить стенд.');
    } finally {
      setBusy(false);
    }
  }
  async function compare() { if (!client || !baseline || !candidate) return; setBusy(true); setError(''); try { setComparison(await client.compareRuns(baseline, candidate)); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сравнить тесты.'); } finally { setBusy(false); } }
  const readyRuns = runs.filter(run => run.status === 'completed' && run.trace_collection.status !== 'waiting' && run.trace_collection.received_count > 0);
  const baselineRun = readyRuns.find(run => run.run_id === baseline);
  const candidateRun = readyRuns.find(run => run.run_id === candidate);
  const comparable = !!baselineRun && !!candidateRun && baseline !== candidate
    && baselineRun.stand_id === candidateRun.stand_id;

  return <><PageHeader title="Лаборатория" actions={<><Button icon="refresh" onClick={() => void refresh()} disabled={busy}>Обновить</Button></>}/>
    {error && <Message tone="danger">{error}</Message>}
    {!client ? <Message tone="warning">Подключите сервер на странице «Настройки подключения».</Message> : !stand ? <EmptyState icon="traces" title="Нет активного стенда" description="Откройте трейс подключённой системы и нажмите «Собрать стенд», чтобы создать тестовую модель цепочки вызовов." action={<div className="empty-state-actions"><a className="button button--primary" href="#/traces">Выбрать трейс</a><Button onClick={() => void build()} disabled={busy}>Запустить демонстрационный стенд</Button></div>}/> : <>
      <div className="lab-grid lab-controls">
        <Panel title="Стенд" meta={<Badge tone={stand.status === 'ready' ? 'success' : ['failed', 'unavailable'].includes(stand.status) ? 'danger' : 'warning'}>{statusLabel(stand.status)}</Badge>}><div className="lab-body"><Metrics items={[{ label: 'Сценарий', value: scenarioLabel(stand.scenario_id) }, { label: 'Сервисов', value: stand.services.length }, { label: 'Настроено ошибок', value: stand.faults.length }]}/>{stand.source && <dl className="context-values"><div><dt>Исходный Trace ID</dt><dd className="mono">{stand.source.trace_id}</dd></div><div><dt>Окружение</dt><dd>{stand.source.environment_id}</dd></div></dl>}<Button disabled={busy || !!active || ['building', 'stopped'].includes(stand.status)}
          onClick={() => void stop()}>{stand.status === 'stopping' ? 'Повторить остановку' : 'Остановить стенд'}</Button></div></Panel>
        <Panel title="Имитация ошибок"><div className="lab-body lab-fault-form">
          <Field label="Сервис"><Select value={fault.service_id} disabled={busy || !!active} onChange={value => { const service = stand.services.find(item => item.service_id === value); const route = service?.routes[0]?.route ?? ''; setFault(old => ({ ...old, service_id: value, route })); }}>{stand.services.map(service => <option value={service.service_id} key={service.service_id}>{service.display_name}</option>)}</Select></Field>
          <Field label="Эндпоинт"><Select value={fault.route} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, route: value }))}>{stand.services.find(service => service.service_id === fault.service_id)?.routes.map(route => <option value={route.route} key={route.route} label={route.route}><HttpOperation operation={route.route}/></option>)}</Select></Field>
          <Field label="Вероятность ошибки, %"><input type="number" min="0" max="100" step="1" value={Math.round(fault.error_rate * 100)} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, error_rate: Number(event.target.value) / 100 }))}/></Field>
          <Field label="Код HTTP при ошибке"><input type="number" min="400" max="599" data-http-status={fault.http_status} value={fault.http_status} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, http_status: Number(event.target.value) }))}/></Field>
          <Field label="Дополнительная задержка, мс"><input type="number" min="0" value={fault.delay_ms} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, delay_ms: Number(event.target.value) }))}/><small className="field-note">Прибавляется к обычному времени ответа перед ошибкой.</small></Field>
          <Field label="Выбор этапа обработки"><Select value={fault.failure_point.mode} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, failure_point: value === 'fixed' ? { mode: 'fixed', boundary_id: selectedRoute?.boundaries[0]?.boundary_id ?? '' } : { mode: 'random', boundary_ids: (selectedRoute?.boundaries ?? []).map(item => item.boundary_id) } }))}><option value="fixed">Фиксированный этап</option><option value="random">Случайный из выбранных этапов</option></Select></Field>
          {fault.failure_point.mode === 'fixed' ? <Field label="Этап обработки"><Select value={fault.failure_point.boundary_id} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, failure_point: { mode: 'fixed', boundary_id: value } }))}>{(selectedRoute?.boundaries ?? []).map(boundary => <option value={boundary.boundary_id} key={boundary.boundary_id}>{boundary.label.replace(/исходящих вызовов/gi, 'обращений к другим сервисам').replaceAll('·', '—')}</option>)}</Select></Field> : <fieldset><legend>Этапы для случайного выбора</legend>{(selectedRoute?.boundaries ?? []).map(boundary => <label key={boundary.boundary_id}><input type="checkbox" checked={fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.includes(boundary.boundary_id)} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, failure_point: { mode: 'random', boundary_ids: event.target.checked ? [...(old.failure_point.mode === 'random' ? old.failure_point.boundary_ids : []), boundary.boundary_id] : (old.failure_point.mode === 'random' ? old.failure_point.boundary_ids : []).filter(id => id !== boundary.boundary_id) } }))}/>{boundary.label.replace(/исходящих вызовов/gi, 'обращений к другим сервисам').replaceAll('·', '—')}</label>)}</fieldset>}
          {fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.length === 0 && <Message tone="warning">Выберите хотя бы один этап обработки.</Message>}
          <div className="lab-actions"><Button variant="primary" disabled={busy || !!active || stand.status !== 'ready' || !selectedRoute || !validFaultDraft(fault) || (fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.length === 0)} onClick={() => void saveFault()}>Применить имитацию ошибок</Button><Button disabled={busy || !!active || !stand.faults.length} onClick={() => void clearFault()}>Отключить имитацию ошибок</Button></div>
        </div></Panel>
        <Panel title="Нагрузка" className="lab-wide lab-load"><div className="lab-body"><div className="form-grid"><Field label="Запросов"><input type="number" min="1" max="1000" value={count} disabled={busy || !!active || !!retry} onChange={event => setCount(event.target.value)}/></Field><Field label="Одновременных запросов"><input type="number" min="1" max="32" value={concurrency} disabled={busy || !!active || !!retry} onChange={event => setConcurrency(event.target.value)}/></Field><Button variant="primary" disabled={busy || (!retry && (!!active || stand.status !== 'ready' || !Number.isInteger(Number(count)) || Number(count) < 1 || Number(count) > 1000 || !Number.isInteger(Number(concurrency)) || Number(concurrency) < 1 || Number(concurrency) > 32))} onClick={() => void run()}>{retry ? 'Повторить запрос запуска' : 'Запустить тест'}</Button></div></div></Panel>
        {client && <Panel title="Настройки сервисов стенда" className="lab-wide"><div className="lab-body">{active && <Message tone="warning">Настройки доступны после завершения теста и сбора его трейсов.</Message>}<Field label="Сервис"><Select aria-label="Сервис для настройки" value={stand.services.some(service=>service.service_id===configService)?configService:stand.services[0]?.service_id??''} onChange={setConfigService}>{stand.services.map(service=><option key={service.service_id} value={service.service_id}>{service.display_name}</option>)}</Select></Field>{stand.services.filter(service=>service.service_id===(stand.services.some(item=>item.service_id===configService)?configService:stand.services[0]?.service_id)).map(service=><ServiceConfig key={service.service_id} client={client} service={service.service_id} environmentId="sandbox" disabled={busy || !!active || stand.status !== 'ready'} onChanged={() => void refresh()}/>)}</div></Panel>}
      </div>
    </>}
    {client && <>
      {stand?.origins?.length ? <Panel title="Источники параметров"><TableScroll><table><thead><tr><th>Сервис</th><th>Параметр</th><th>Значение</th><th>Источник</th></tr></thead><tbody>{stand.origins.map((origin,index)=><tr key={`${origin.service_id}-${origin.field}-${index}`}><td>{origin.service_id}</td><td>{fieldLabel(origin.field)}</td><td>{origin.value_ms !== undefined ? `${origin.value_ms} мс` : origin.duration_ns ? nsMs(origin.duration_ns) : origin.attempts !== undefined ? origin.attempts : '—'}</td><td>{origin.source === 'observed' ? 'Из трейса' : 'По умолчанию'}{origin.approximation && <small className="cell-note">{origin.approximation}</small>}</td></tr>)}</tbody></table></TableScroll></Panel> : null}
      <Panel title="Результаты тестов"><div className="lab-body">{!runs.length ? <p className="muted">Тесты ещё не запускались.</p> : <TableScroll><table className="lab-runs-table"><thead><tr><th>Тест</th><th>Стенд</th><th>Всего запросов</th><th>Одновременных запросов</th><th>Состояние</th><th>Трейсы</th></tr></thead><tbody>{runs.map(run => <tr key={run.run_id}><td className="mono">{run.run_id}</td><td className="mono">{run.stand_id}</td><td>{run.request_count}</td><td>{run.concurrency}</td><td><Badge tone={run.status === 'failed' ? 'danger' : run.status === 'completed' ? 'success' : 'warning'}>{statusLabel(run.status)}</Badge></td><td>{run.trace_collection.received_count} / {run.trace_collection.expected_count} — {statusLabel(run.trace_collection.status)}</td></tr>)}</tbody></table></TableScroll>}</div></Panel>
      {readyRuns.length >= 2 && <Panel title="Сравнение тестов"><div className="lab-body"><RunComparisonControls
        options={readyRuns.map(run => ({ id: run.run_id, label: run.run_id }))}
        baseline={baseline} candidate={candidate} allowClear
        onBaselineChange={value => { setBaseline(value); setComparison(null); }}
        onCandidateChange={value => { setCandidate(value); setComparison(null); }}
      >
        <Button disabled={busy || !comparable} onClick={() => void compare()}>Сравнить</Button>
      </RunComparisonControls>{comparison && <><TableScroll><table><thead><tr><th>Показатель</th><th>До изменения</th><th>После изменения</th></tr></thead><tbody>{[
        ['Получено трейсов', `${comparison.baseline.received_count} / ${comparison.baseline.expected_count}`, `${comparison.candidate.received_count} / ${comparison.candidate.expected_count}`],
        ['Медиана', nsMs(comparison.baseline.root_latency_p50_ns), nsMs(comparison.candidate.root_latency_p50_ns)],
        ['95-й перцентиль', nsMs(comparison.baseline.root_latency_p95_ns), nsMs(comparison.candidate.root_latency_p95_ns)],
        ['Попыток на запрос', formatMetric(comparison.baseline.attempts_per_input), formatMetric(comparison.candidate.attempts_per_input)],
        ['Ответы с ошибкой', `${comparison.baseline.root_errors} (${formatRate(comparison.baseline.root_error_ratio)})`, `${comparison.candidate.root_errors} (${formatRate(comparison.candidate.root_error_ratio)})`],
      ].map(([label,before,after])=><tr key={label}><td>{label}</td><td>{before}</td><td>{after}</td></tr>)}</tbody></table></TableScroll><p>Изменение: {comparison.change.service_id} — {comparison.change.call_ref} — {fieldLabel(comparison.change.field)}: {String(comparison.change.before)} → {String(comparison.change.after)}</p>{comparison.warnings.map(warning => <Message tone="warning" key={warning}>{warning}</Message>)}</>}</div></Panel>}
    </>}
  </>;
}
