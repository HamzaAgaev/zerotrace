import { HttpOperation, httpCodeLabel } from '../components/HttpText';
import { RunComparisonControls } from '../components/RunComparisonControls';
import { Select } from '../components/Select';
import { useEffect, useMemo, useState } from 'react';
import type { Client, LabFault, LabRun, RunComparison, Stand } from '../api/types';
import { Badge, Button, EmptyState, Message, Panel, PageHeader, TableScroll, Field } from '../components/ui';
import { Metrics } from '../components/Metrics';
import { ServiceConfig } from '../components/ServiceConfig';

const nsMs = (value: string | number | null) => value === null ? '—' : `${(Number(value) / 1e6).toLocaleString('ru-RU', { maximumFractionDigits: 1 })} мс`;
const formatMetric = (value: number | null) => value === null ? '—' : value.toLocaleString('ru-RU', { maximumFractionDigits: 2 });

export function LiveLab({ client }: { client: Client | null }) {
  const [stand, setStand] = useState<Stand | null>(null);
  const [runs, setRuns] = useState<LabRun[]>([]);
  const [fault, setFault] = useState<LabFault>({ service_id: 'payment', route: 'POST /charge', error_rate: .2, http_status: 503, delay_ms: 0, failure_point: { mode: 'fixed', boundary_id: 'before-first' } });
  const [count, setCount] = useState('30'); const [concurrency, setConcurrency] = useState('1'); const [seed, setSeed] = useState('');
  const [baseline, setBaseline] = useState(''); const [candidate, setCandidate] = useState(''); const [comparison, setComparison] = useState<RunComparison | null>(null);
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const selectedRoute = useMemo(() => stand?.services.find(service => service.service_id === fault.service_id)?.routes.find(route => route.route === fault.route), [stand, fault]);
  const active = runs.find(run => run.stand_id === stand?.stand_id && (run.status === 'running' || run.trace_collection.status === 'waiting'));
  const standRuns = stand ? runs.filter(run => run.stand_id === stand.stand_id) : runs;

  async function refresh() {
    if (!client) return;
    setError('');
    try {
      const [activeStand, history] = await Promise.all([client.getActiveStand(), client.listRuns()]);
      setStand(activeStand?.status === 'stopped' ? null : activeStand);
      setRuns(history.sort((a, b) => b.started_at.localeCompare(a.started_at)));
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось загрузить состояние лаборатории.'); }
  }
  useEffect(() => { void refresh(); }, [client]);
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
  async function pollBuild(source?: Stand['source']) {
    if (!client) return null;
    const deadline = Date.now() + 6500;
    while (Date.now() < deadline) {
      try {
        const current = await client.getActiveStand();
        if (current && (!source || (current.source?.trace_id === source.trace_id && current.source.environment_id === source.environment_id && current.source.generation === source.generation && current.source.revision === source.revision))) {
          setStand(current);
          if (current.status !== 'building') return current;
        }
      } catch { /* Retry until the server build window expires. */ }
      await new Promise(resolve => window.setTimeout(resolve, 300));
    }
    return null;
  }
  async function build() { if (!client) return; setBusy(true); setError(''); try { setStand(await client.createStand('checkout')); } catch (cause) { const recovered = await pollBuild(); if (!recovered) setError(`${cause instanceof Error ? cause.message : 'Не удалось собрать стенд.'} Проверьте состояние стенда и обновите страницу лаборатории.`); } finally { setBusy(false); } }
  async function saveFault() { if (!client || !stand) return; setBusy(true); setError(''); try { setStand(await client.setFault(stand.stand_id, fault, stand.fault_revision, crypto.randomUUID())); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось применить сбой.'); if ((cause as { status?: number }).status === 409) void refresh(); } finally { setBusy(false); } }
  async function clearFault() { if (!client || !stand) return; setBusy(true); setError(''); try { setStand(await client.clearFault(stand.stand_id, stand.fault_revision, crypto.randomUUID()) as unknown as Stand); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сбросить сбой.'); if ((cause as { status?: number }).status === 409) void refresh(); } finally { setBusy(false); } }
  async function run() {
    if (!client || !stand) return;
    setBusy(true); setError('');
    try {
      const result = await client.startRun(stand.stand_id, { request_id: crypto.randomUUID(), scenario_revision: stand.scenario_revision, expected_fault_revision: stand.fault_revision, config_revisions: stand.config_revisions, request_count: Number(count), concurrency: Number(concurrency), ...(seed.trim() ? { seed: Number(seed) } : {}) });
      setRuns(old => [result, ...old.filter(item => item.run_id !== result.run_id)]);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось запустить нагрузку.'); if ((cause as { status?: number }).status === 409) void refresh(); }
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
  async function compare() { if (!client || !baseline || !candidate) return; setBusy(true); setError(''); try { setComparison(await client.compareRuns(baseline, candidate)); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сравнить прогоны.'); } finally { setBusy(false); } }
  const readyRuns = standRuns.filter(run => run.status === 'completed' && run.trace_collection.status !== 'waiting' && run.trace_collection.received_count > 0);

  return <><PageHeader title="Лаборатория" detail={<p>Checkout из девяти сервисов с реальными HTTP-вызовами и OTLP-трейсами.</p>} actions={<><Button icon="refresh" onClick={() => void refresh()} disabled={busy}>Обновить</Button></>}/>
    {error && <Message tone="danger">{error}</Message>}
    {!client ? <Message tone="warning">Подключите реальный API на странице «Подключение».</Message> : !stand ? <EmptyState icon="traces" title="Нет активного стенда" description="Соберите готовый сценарий checkout. Затем выберите сбой и запустите нагрузку." action={<Button variant="primary" onClick={() => void build()} disabled={busy}>Собрать checkout</Button>}/> : <>
      <div className="lab-grid lab-controls">
        <Panel title="Стенд" meta={<Badge tone={stand.status === 'ready' ? 'success' : ['failed', 'unavailable'].includes(stand.status) ? 'danger' : 'warning'}>{stand.status}</Badge>}><div className="lab-body"><Metrics items={[{ label: 'Сценарий', value: stand.scenario_id }, { label: 'Ревизия сценария', value: stand.scenario_revision }, { label: 'Сервисов', value: stand.services.length }, { label: 'Сбоев', value: stand.faults.length }]}/><p>Настройки прогона фиксируются по ревизиям всех сервисов.</p>{stand.source && <p className="muted">Исходный трейс <span className="mono">{stand.source.trace_id}</span>, {stand.source.environment_id}, поколение {stand.source.generation}, ревизия {stand.source.revision}.</p>}{stand.origins?.length ? <details><summary>Происхождение параметров</summary><ul>{stand.origins.map((origin, index) => <li key={`${origin.service_id}-${origin.field}-${index}`}>{origin.service_id} · {origin.field}: {origin.source === 'observed' ? 'наблюдалось' : 'значение по умолчанию'}{origin.value_ms !== undefined ? ` · ${origin.value_ms} мс` : ''}{origin.duration_ns ? ` · ${origin.duration_ns} нс` : ''}{origin.attempts !== undefined ? ` · ${origin.attempts} попыток` : ''}{origin.approximation ? ` · ${origin.approximation}` : ''}</li>)}</ul></details> : null}<Button disabled={busy || !!active || ['building', 'stopped'].includes(stand.status)}
          onClick={() => void stop()}>{stand.status === 'stopping' ? 'Повторить остановку' : 'Остановить стенд'}</Button></div></Panel>
        <Panel title="Сбой входящего маршрута"><div className="lab-body">
          <Field label="Сервис"><Select value={fault.service_id} disabled={busy || !!active} onChange={value => { const service = stand.services.find(item => item.service_id === value); const route = service?.routes[0]?.route ?? ''; setFault(old => ({ ...old, service_id: value, route })); }}>{stand.services.map(service => <option value={service.service_id} key={service.service_id}>{service.display_name}</option>)}</Select></Field>
          <Field label="Маршрут"><Select value={fault.route} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, route: value }))}>{stand.services.find(service => service.service_id === fault.service_id)?.routes.map(route => <option value={route.route} key={route.route} label={route.route}><HttpOperation operation={route.route}/></option>)}</Select></Field>
          <Field label="Вероятность ошибки"><input type="number" min="0" max="1" step="0.01" value={fault.error_rate} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, error_rate: Number(event.target.value) }))}/></Field>
          <Field label={httpCodeLabel}><input type="number" min="400" max="599" data-http-status={fault.http_status} value={fault.http_status} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, http_status: Number(event.target.value) }))}/></Field>
          <Field label="Дополнительная задержка, мс"><input type="number" min="0" value={fault.delay_ms} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, delay_ms: Number(event.target.value) }))}/></Field>
          <Field label="Точка ошибки"><Select value={fault.failure_point.mode} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, failure_point: value === 'fixed' ? { mode: 'fixed', boundary_id: selectedRoute?.boundaries[0]?.boundary_id ?? '' } : { mode: 'random', boundary_ids: (selectedRoute?.boundaries ?? []).map(item => item.boundary_id) } }))}><option value="fixed">Выбранная граница</option><option value="random">Случайная из отмеченных</option></Select></Field>
          {fault.failure_point.mode === 'fixed' ? <Field label="Граница"><Select value={fault.failure_point.boundary_id} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, failure_point: { mode: 'fixed', boundary_id: value } }))}>{(selectedRoute?.boundaries ?? []).map(boundary => <option value={boundary.boundary_id} key={boundary.boundary_id}>{boundary.label}</option>)}</Select></Field> : <fieldset><legend>Случайные границы</legend>{(selectedRoute?.boundaries ?? []).map(boundary => <label key={boundary.boundary_id}><input type="checkbox" checked={fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.includes(boundary.boundary_id)} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, failure_point: { mode: 'random', boundary_ids: event.target.checked ? [...(old.failure_point.mode === 'random' ? old.failure_point.boundary_ids : []), boundary.boundary_id] : (old.failure_point.mode === 'random' ? old.failure_point.boundary_ids : []).filter(id => id !== boundary.boundary_id) } }))}/>{boundary.label}</label>)}</fieldset>}
          <div className="lab-actions"><Button variant="primary" disabled={busy || !!active || stand.status !== 'ready' || (fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.length === 0)} onClick={() => void saveFault()}>Применить сбой</Button><Button disabled={busy || !!active || !stand.faults.length} onClick={() => void clearFault()}>Сбросить сбои</Button></div>
        </div></Panel>
        <Panel title="Нагрузка" className="lab-wide"><div className="lab-body"><div className="lab-actions"><Field label="Запросов"><input type="number" min="1" max="1000" value={count} disabled={busy || !!active} onChange={event => setCount(event.target.value)}/></Field><Field label="Параллельность"><input type="number" min="1" max="32" value={concurrency} disabled={busy || !!active} onChange={event => setConcurrency(event.target.value)}/></Field><Field label="Seed"><input type="number" min="0" max="9007199254740991" placeholder="Случайный" value={seed} disabled={busy || !!active} onChange={event => setSeed(event.target.value)}/></Field><Button variant="primary" disabled={busy || !!active || stand.status !== 'ready' || !Number.isInteger(Number(count)) || Number(count) < 1 || Number(count) > 1000 || !Number.isInteger(Number(concurrency)) || Number(concurrency) < 1 || Number(concurrency) > 32 || (seed !== '' && (!Number.isSafeInteger(Number(seed)) || Number(seed) < 0))} onClick={() => void run()}>Запустить прогон</Button></div><p className="muted">Если seed не указан, сервер создаст и сохранит его.</p></div></Panel>
        {client && <Panel title="Конфигурация сервисов стенда" className="lab-wide"><div className="lab-body">{active && <Message tone="warning">Во время выполнения и фиксации трейсов конфигурация заблокирована.</Message>}{stand.services.map(service => <details className="compact-details" key={service.service_id}><summary>{service.display_name} · ревизия {service.revision}</summary><ServiceConfig client={client} service={service.service_id} environmentId="sandbox" disabled={busy || !!active || stand.status !== 'ready'} onChanged={() => void refresh()}/></details>)}</div></Panel>}
      </div>
      <Panel title="Прогоны"><div className="lab-body">{!runs.length ? <p className="muted">Прогоны ещё не запускались.</p> : <TableScroll><table><thead><tr><th>Прогон</th><th>Стенд</th><th>Seed</th><th>Нагрузка</th><th>Состояние</th><th>Трейсы</th></tr></thead><tbody>{runs.map(run => <tr key={run.run_id}><td className="mono">{run.run_id}</td><td className="mono">{run.stand_id}</td><td>{run.seed}</td><td>{run.request_count} × {run.concurrency}</td><td><Badge tone={run.status === 'failed' ? 'danger' : run.status === 'completed' ? 'success' : 'warning'}>{run.status}</Badge></td><td>{run.trace_collection.received_count} / {run.trace_collection.expected_count} · {run.trace_collection.status}</td></tr>)}</tbody></table></TableScroll>}</div></Panel>
      {readyRuns.length >= 2 && <Panel title="Сравнение прогонов"><div className="lab-body"><RunComparisonControls
        options={readyRuns.map(run => ({ id: run.run_id, label: run.run_id }))}
        baseline={baseline} candidate={candidate} allowClear
        onBaselineChange={setBaseline} onCandidateChange={setCandidate}
      >
        <Button disabled={busy || !baseline || !candidate || baseline === candidate} onClick={() => void compare()}>Сравнить</Button>
      </RunComparisonControls>{comparison && <><Metrics items={[{ label: 'Входные трейсы до', value: `${comparison.baseline.received_count}/${comparison.baseline.expected_count}` }, { label: 'Входные трейсы после', value: `${comparison.candidate.received_count}/${comparison.candidate.expected_count}` }, { label: 'Медиана до', value: nsMs(comparison.baseline.root_latency_p50_ns) }, { label: 'Медиана после', value: nsMs(comparison.candidate.root_latency_p50_ns) }, { label: 'p95 до', value: nsMs(comparison.baseline.root_latency_p95_ns) }, { label: 'p95 после', value: nsMs(comparison.candidate.root_latency_p95_ns) }, { label: 'Попыток на запрос до', value: formatMetric(comparison.baseline.attempts_per_input) }, { label: 'Попыток на запрос после', value: formatMetric(comparison.candidate.attempts_per_input) }, { label: 'Ответы с ошибкой до', value: `${comparison.baseline.root_errors} (${formatMetric(comparison.baseline.root_error_ratio)})` }, { label: 'Ответы с ошибкой после', value: `${comparison.candidate.root_errors} (${formatMetric(comparison.candidate.root_error_ratio)})` }]}/><p>Изменение: {comparison.change.service_id} · {comparison.change.call_ref} · {comparison.change.field}: {String(comparison.change.before)} → {String(comparison.change.after)}</p>{comparison.warnings.map(warning => <Message tone="warning" key={warning}>{warning}</Message>)}</>}</div></Panel>}
    </>}
  </>;
}
