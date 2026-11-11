import { HttpMethod, HttpOperation } from './HttpText';
import { Select } from './Select';
import { useEffect, useState } from 'react';
import type { ChangePlan, Client, ConfigSnapshot, RouteWork } from '../api/types';
import { demoServiceCapabilities } from '../api/fixtures';
import { Badge, Button, Field, LoadingState, Message } from './ui';
import { PlanDialog } from './PlanDialog';

export function ServiceConfig({ client, service, environmentId, demo = false, disabled = false, maxAttempts = 3, onSave = () => undefined, onChanged }: { client: Client; service: string; environmentId: string; demo?: boolean; disabled?: boolean; maxAttempts?: number; onSave?: (value: number) => void; onChanged?: () => void }) {
  const [draft, setDraft] = useState(String(maxAttempts));
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [loading, setLoading] = useState(!demo);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState<ChangePlan | null>(null);
  const [reload, setReload] = useState(0);
  const [callDrafts, setCallDrafts] = useState<Record<string, { timeout: string; attempts: string; backoff: string; retry: string }>>({});
  const [routeDrafts, setRouteDrafts] = useState<Record<string, RouteWork>>({});
  useEffect(() => {
    if (demo) return;
    const controller = new AbortController();
    setLoading(true); setError('');
    client.getConfig(service, environmentId, controller.signal).then(config => {
      if (controller.signal.aborted) return;
      setSnapshot(config);
      setCallDrafts(Object.fromEntries(config.calls.map(call => [call.call_ref, { timeout: String(call.timeout_ms), attempts: String(call.max_attempts), backoff: String(call.backoff_ms), retry: call.retry_on.join(', ') }])));
      setRouteDrafts(Object.fromEntries(config.routes.map(route => [route.route, route.work])));
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось прочитать конфиг.'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [client, demo, service, environmentId, reload]);
  const capabilities = demo ? demoServiceCapabilities[service] : undefined;
  if (demo) {
    if (!capabilities) return <p className="muted">Сведения о доступе к конфигу отсутствуют.</p>;
    if (!capabilities.read_config) return <p className="muted">Конфиг недоступен в демонстрационном наборе.</p>;
    return <form className="service-config" onSubmit={event => { event.preventDefault(); const value = Number(draft); if (Number.isInteger(value) && value >= 1 && value <= 10) onSave(value); }}><Badge tone="warning">Локальная демонстрация</Badge><p>Вызов payment, <HttpOperation operation="POST /charge"/></p><Field label="Максимум попыток"><input type="number" required min="1" max="10" step="1" value={draft} onChange={event => setDraft(event.target.value)}/></Field><Button type="submit" disabled={Number(draft) === maxAttempts}>Сохранить в примере</Button></form>;
  }
  async function buildCallPlan(callRef: string) {
    if (!snapshot) return;
    const call = snapshot.calls.find(item => item.call_ref === callRef), values = callDrafts[callRef];
    if (!call || !values) return;
    const set: Record<string, string | number | string[]> = {};
    if (Number(values.timeout) !== call.timeout_ms) set.per_attempt_timeout_ns = String(Math.round(Number(values.timeout) * 1e6));
    if (Number(values.attempts) !== call.max_attempts) set.max_attempts = Number(values.attempts);
    if (Number(values.backoff) !== call.backoff_ms) set.backoff_ms = Number(values.backoff);
    const retry = values.retry.split(',').map(value => value.trim()).filter(Boolean);
    if (JSON.stringify(retry) !== JSON.stringify(call.retry_on)) set.retry_on = retry;
    if (!Object.keys(set).length) return;
    try { setPlan(await client.createManualPlan({ environment_id: environmentId, service_id: service, expected_config_revision: snapshot.revision, call_ref: callRef, set })); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось построить план.'); }
  }
  async function buildRoutePlan(route: string) {
    if (!snapshot) return;
    const current = snapshot.routes.find(item => item.route === route), work = routeDrafts[route];
    if (!current || !work || JSON.stringify(current.work) === JSON.stringify(work)) return;
    try { setPlan(await client.createManualPlan({ environment_id: environmentId, service_id: service, expected_config_revision: snapshot.revision, route, set: { work } })); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось построить план.'); }
  }
  return <div className="service-config"><h3>Конфигурация сервиса</h3>{loading ? <LoadingState>Читаем действующую конфигурацию…</LoadingState> : error ? <Message tone="danger">{error}</Message> : !snapshot ? <p className="muted">Конфигурация недоступна.</p> : <><p className="muted">Ревизия {snapshot.revision}. Новые значения действуют для новых входящих запросов.</p>{snapshot.calls.map(call => { const values = callDrafts[call.call_ref]; const writable = call.write_fields.length > 0; return <form key={call.call_ref} onSubmit={event => { event.preventDefault(); void buildCallPlan(call.call_ref); }}><h4>{call.target.service_id} · <HttpMethod method={call.target.method}/> {call.target.route}</h4><Field label="Таймаут попытки, мс"><input type="number" min="1" value={values?.timeout ?? ''} disabled={disabled || !call.write_fields.includes('timeout_ms')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], timeout: event.target.value } }))}/></Field><Field label="Максимум попыток"><input type="number" min="1" max="10" value={values?.attempts ?? ''} disabled={disabled || !call.write_fields.includes('max_attempts')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], attempts: event.target.value } }))}/></Field><Field label="Пауза, мс"><input type="number" min="0" value={values?.backoff ?? ''} disabled={disabled || !call.write_fields.includes('backoff_ms')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], backoff: event.target.value } }))}/></Field><Field label="Повторять при"><input value={values?.retry ?? ''} disabled={disabled || !call.write_fields.includes('retry_on')} onChange={event => setCallDrafts(old => ({ ...old, [call.call_ref]: { ...old[call.call_ref], retry: event.target.value } }))}/></Field>{writable && <Button type="submit" disabled={disabled}>Построить план</Button>}</form>; })}{snapshot.routes.map(route => { const work = routeDrafts[route.route]; return <form key={route.route} onSubmit={event => { event.preventDefault(); void buildRoutePlan(route.route); }}><h4>Входящий <HttpOperation operation={route.route}/></h4><Field label="Распределение"><Select value={work?.kind ?? 'fixed'} disabled={disabled || !route.write_fields.includes('work')} onChange={value => setRouteDrafts(old => ({ ...old, [route.route]: value === 'fixed' ? { kind: 'fixed', delay_ms: 1 } : { kind: 'lognormal', p50_ms: 1, p99_ms: 2 } }))}><option value="fixed">Фиксированное</option><option value="lognormal">Логнормальное</option></Select></Field>{work?.kind === 'fixed' ? <Field label="Собственная задержка, мс"><input type="number" min="0" value={work.delay_ms} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: { kind: 'fixed', delay_ms: Number(event.target.value) } }))}/></Field> : <><Field label="p50, мс"><input type="number" min="1" value={work?.p50_ms ?? ''} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: { kind: 'lognormal', p50_ms: Number(event.target.value), p99_ms: (old[route.route] as Extract<RouteWork, { kind: 'lognormal' }>).p99_ms } }))}/></Field><Field label="p99, мс"><input type="number" min="1" value={work?.p99_ms ?? ''} disabled={disabled || !route.write_fields.includes('work')} onChange={event => setRouteDrafts(old => ({ ...old, [route.route]: { kind: 'lognormal', p50_ms: (old[route.route] as Extract<RouteWork, { kind: 'lognormal' }>).p50_ms, p99_ms: Number(event.target.value) } }))}/></Field></>}{route.write_fields.includes('work') && <Button type="submit" disabled={disabled}>Построить план</Button>}</form>; })}</>}{plan && <PlanDialog key={plan.plan_id} client={client} plan={plan} canApply={plan.apply_enabled} onClose={() => setPlan(null)} onApplied={() => { setPlan(null); setSnapshot(null); setReload(value => value + 1); onChanged?.(); }} />}</div>;
}
