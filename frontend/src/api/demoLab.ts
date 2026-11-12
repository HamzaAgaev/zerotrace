import type { Client, Stand, LabRun, LabFault, TraceRef, ConfigSnapshot, ManualChangeIntent, ChangePlan, JournalEntry, ComparisonMetrics } from './types.js';
import { demoDetail, labSourceTrace } from './fixtures.js';

type DemoRun = LabRun & { faults: LabFault[]; configs: Record<string, ConfigSnapshot> };
interface State { stand: Stand | null; runs: DemoRun[]; configs: Record<string, ConfigSnapshot>; plans: Record<string, { plan: ChangePlan; intent: ManualChangeIntent }>; journal: JournalEntry[] }
const storageKey = 'zerotrace.demo-lab.v2';
const empty = (): State => ({ stand: null, runs: [], configs: {}, plans: {}, journal: [] });
/** Same client contract as the server; only the data source is simulated. */
export function createDemoLab(storage?: Pick<Storage, 'getItem' | 'setItem'>) {
  let state = empty();
  try { const saved = storage?.getItem(storageKey); if (saved) { const parsed = JSON.parse(saved); if (parsed && Array.isArray(parsed.runs) && Array.isArray(parsed.journal) && parsed.configs && parsed.plans && 'stand' in parsed) state = parsed; } } catch { /* Start with a clean demonstration. */ }
  const copy = <T,>(value: T): T => structuredClone(value);
  const save = () => { try { storage?.setItem(storageKey, JSON.stringify(state)); } catch { /* Session remains usable. */ } };
  const current = (id?: string) => { if (!state.stand || (id && state.stand.stand_id !== id)) throw new Error('Стенд не найден.'); return state.stand; };
  const build = async (source: TraceRef = labSourceTrace.trace_ref): Promise<Stand> => {
    if (state.stand) throw new Error('Сначала остановите текущий стенд.');
    const trace = demoDetail(source.trace_id);
    if (trace.trace_ref.environment_id !== source.environment_id || trace.trace_ref.generation !== source.generation || trace.trace_ref.revision !== source.revision) throw new Error('Версия демонстрационного трейса не найдена.');
    const services: Stand['services'] = [];
    for (const span of trace.spans.filter(s => s.kind === 'server')) {
      let service = services.find(s => s.service_id === span.service);
      if (!service) { service = { service_id: span.service, display_name: span.service, revision: '1', routes: [] }; services.push(service); }
      if (!service.routes.some(r => r.route === span.name)) service.routes.push({ route: span.name, work: { kind: 'fixed', delay_ms: Number(span.duration_ns) / 1e6 }, write_fields: ['work'], boundaries: [{ boundary_id: 'before-first', label: 'Перед обработкой запроса' }] });
    }
    state.plans = {};
    state.configs = Object.fromEntries(services.map(s => [s.service_id, { schema_version: '1', service_id: s.service_id, revision: '1', routes: s.routes, calls: [] }]));
    for (const span of trace.spans.filter(s => s.kind === 'server' && s.parent_span_id)) {
      let parent = trace.spans.find(s => s.span_id === span.parent_span_id);
      const seen = new Set<string>();
      while (parent && parent.kind !== 'server' && !seen.has(parent.span_id)) { seen.add(parent.span_id); parent = trace.spans.find(s => s.span_id === parent!.parent_span_id); }
      const config = parent && state.configs[parent.service];
      const [method, ...path] = span.name.split(' ');
      if (config && !config.calls.some(c => c.target.service_id === span.service && c.target.route === path.join(' '))) config.calls.push({ call_ref: span.span_id, target: { service_id: span.service, method, route: path.join(' ') }, timeout_ms: 1000, max_attempts: 3, backoff_ms: 50, retry_on: ['5xx', 'timeout'], write_fields: ['timeout_ms', 'max_attempts', 'backoff_ms', 'retry_on'] });
    }
    state.stand = { stand_id: `demo-${crypto.randomUUID()}`, status: 'ready', scenario_id: 'generated', scenario_revision: 1, fault_revision: 0, config_revisions: Object.fromEntries(services.map(s => [s.service_id, '1'])), services, faults: [], source };
    save(); return copy(state.stand);
  };
  const fault = async (id: string, value: LabFault | null, revision: number) => {
    const stand = current(id);
    if (stand.fault_revision !== revision) throw new Error('Настройки изменились. Обновите лабораторию.');
    if (value && !stand.services.some(s => s.service_id === value.service_id && s.routes.some(r => r.route === value.route))) throw new Error('Эндпоинт не найден.');
    stand.faults = value ? [copy(value)] : []; stand.fault_revision++; save(); return copy(stand);
  };
  const findRun = (id: string) => { const run = state.runs.find(r => r.run_id === id); if (!run) throw new Error('Тест не найден.'); return run; };
  const entry = async (id: string) => { const found = state.journal.find(e => e.operation_id === id); if (!found) throw new Error('Изменение не найдено.'); return copy(found); };
  const api = {
    getActiveStand: async () => copy(state.stand), createStand: async () => { await build(); state.stand!.scenario_id = 'checkout'; state.stand!.source = null; save(); return copy(state.stand!); }, createStandFromTrace: build,
    stopStand: async (id: string) => { const result = copy(current(id)); result.status = 'stopped'; state.stand = null; save(); return result; },
    setFault: async (id: string, value: LabFault, revision: number) => fault(id, value, revision),
    clearFault: async (id: string, revision: number) => fault(id, null, revision),
    startRun: async (id: string, request: Parameters<Client['startRun']>[1]) => {
      const existing = state.runs.find(r => r.run_id === request.request_id); if (existing) return copy(existing);
      const stand = current(id);
      if (request.scenario_revision !== stand.scenario_revision || request.expected_fault_revision !== stand.fault_revision || JSON.stringify(request.config_revisions) !== JSON.stringify(stand.config_revisions)) throw new Error('Настройки изменились. Обновите лабораторию.');
      if (!Number.isInteger(request.request_count) || request.request_count < 1 || request.request_count > 1000 || !Number.isInteger(request.concurrency) || request.concurrency < 1 || request.concurrency > 32) throw new Error('Проверьте параметры нагрузки.');
      const run: DemoRun = { run_id: request.request_id, stand_id: id, status: 'completed', seed: request.seed ?? 1, scenario_revision: stand.scenario_revision, fault_revision: stand.fault_revision, config_revisions: copy(stand.config_revisions), request_count: request.request_count, concurrency: request.concurrency, completed_requests: request.request_count, started_at: new Date().toISOString(), finished_at: new Date().toISOString(), trace_collection: { status: 'ready', expected_count: request.request_count, received_count: request.request_count }, faults: copy(stand.faults), configs: copy(state.configs) };
      state.runs.unshift(run); save(); return copy(run);
    },
    getRun: async (id: string) => copy(findRun(id)), listRuns: async () => copy(state.runs),
    getConfig: async (service: string) => { if (!state.configs[service]) throw new Error('Настройки доступны после создания демонстрационного стенда.'); return copy(state.configs[service]); },
    createManualPlan: async (intent: ManualChangeIntent): Promise<ChangePlan> => {
      const config = state.configs[intent.service_id];
      const route = config?.routes.find(r => r.route === intent.route);
      const call = config?.calls.find(c => c.call_ref === intent.call_ref);
      if ((!route && !call) || config.revision !== intent.expected_config_revision || !Object.keys(intent.set).length) throw new Error('Настройки изменились или эндпоинт недоступен.');
      const plan: ChangePlan = { plan_id: crypto.randomUUID(), plan_hash: crypto.randomUUID(), finding_id: '', target: `${intent.service_id} / ${intent.route ?? `${call!.target.method} ${call!.target.service_id}${call!.target.route}`}`, expected_revision: config.revision, environment_id: 'sandbox', expires_at: new Date(Date.now() + 300000).toISOString(), changes: Object.entries(intent.set).map(([field, value]) => ({ field, before: JSON.stringify(field === 'work' ? route?.work : field === 'per_attempt_timeout_ns' ? String((call?.timeout_ms ?? 0) * 1e6) : call?.[field as keyof typeof call]), after: JSON.stringify(value) })), warnings: ['Изменение действует только в локальном демонстрационном наборе.'], apply_enabled: true };
      state.plans[plan.plan_id] = { plan, intent: copy(intent) }; save(); return copy(plan);
    },
    applyPlan: async (id: string, hash: string, request: string) => {
      const previous = state.journal.find(e => e.operation_id === request); if (previous) return { operation_id: request, status: previous.status };
      const saved = state.plans[id]; if (!saved || saved.plan.plan_hash !== hash || Date.parse(saved.plan.expires_at) < Date.now()) throw new Error('План недоступен. Подготовьте новый.');
      const { plan, intent } = saved; const stand = current(); const config = state.configs[intent.service_id];
      if (config.revision !== intent.expected_config_revision) throw new Error('Версия настроек изменилась.');
      if (intent.route) config.routes.find(r => r.route === intent.route)!.work = copy(intent.set.work as ConfigSnapshot['routes'][number]['work']);
      else {
        const call = config.calls.find(c => c.call_ref === intent.call_ref)!;
        for (const [field, value] of Object.entries(intent.set)) {
          if (field === 'per_attempt_timeout_ns') call.timeout_ms = Number(value) / 1e6;
          else if (field === 'max_attempts') call.max_attempts = Number(value);
          else if (field === 'backoff_ms') call.backoff_ms = Number(value);
          else if (field === 'retry_on') call.retry_on = value as string[];
        }
      }
      config.revision = String(Number(config.revision) + 1); stand.config_revisions[intent.service_id] = config.revision;
      const service = stand.services.find(s => s.service_id === intent.service_id)!; service.revision = config.revision; service.routes = copy(config.routes) as typeof service.routes;
      state.journal.push({ operation_id: request, target: plan.target, requested_at: new Date().toISOString(), status: 'applied', verification: null, actual_changes: plan.changes }); save(); return { operation_id: request, status: 'applied' as const };
    },
    getJournal: async () => copy(state.journal), getJournalEntry: entry, getJournalEntryForEnvironment: entry,
    compareRuns: async (a: string, b: string) => {
      const before = findRun(a), after = findRun(b);
      if (a === b || before.stand_id !== after.stand_id || before.fault_revision !== after.fault_revision || before.request_count !== after.request_count || before.concurrency !== after.concurrency) throw new Error('Выберите разные тесты одного стенда с одинаковыми ошибками и нагрузкой.');
      const changes = Object.keys(before.configs).flatMap(service => before.configs[service].routes.flatMap(route => {
        const next = after.configs[service]?.routes.find(r => r.route === route.route);
        return next && JSON.stringify(route.work) !== JSON.stringify(next.work) ? [{ service_id: service, call_ref: route.route, field: 'work', before: route.work, after: next.work }] : [];
      }));
      for (const service of Object.keys(before.configs)) for (const call of before.configs[service].calls) {
        const next = after.configs[service]?.calls.find(c => c.call_ref === call.call_ref);
        for (const field of ['timeout_ms', 'max_attempts', 'backoff_ms', 'retry_on'] as const) if (next && JSON.stringify(call[field]) !== JSON.stringify(next[field])) changes.push({ service_id: service, call_ref: call.call_ref, field, before: call[field] as never, after: next[field] as never });
      }
      if (changes.length !== 1) throw new Error('Для сравнения измените один параметр эндпоинта.');
      const metrics = (run: DemoRun): ComparisonMetrics => {
        const duration = Object.values(run.configs).flatMap(c => c.routes).reduce((sum, r) => sum + (r.work.kind === 'fixed' ? r.work.delay_ms : r.work.p50_ms), 0);
        const attemptCount = 1 + Object.values(run.configs).flatMap(c => c.calls).reduce((sum, c) => sum + (run.faults.length ? c.max_attempts : 1), 0);
        const errors = Math.round(run.request_count * (run.faults[0]?.error_rate ?? 0));
        return { run_id: run.run_id, config_revision: JSON.stringify(run.config_revisions), collection_status: 'ready', received_count: run.request_count, expected_count: run.request_count, attempts: run.request_count * attemptCount, retries: run.faults.length ? run.request_count * Object.values(run.configs).flatMap(c => c.calls).reduce((sum, c) => sum + c.max_attempts - 1, 0) : 0, attempts_per_input: attemptCount, root_latency_p50_ns: String(Math.round(duration * 1e6)), root_latency_p95_ns: String(Math.round(duration * 1e6)), root_errors: errors, root_error_ratio: errors / run.request_count, internal_error_trace_count: errors, internal_error_trace_ratio: errors / run.request_count, attempt_count_histogram: { '1': run.request_count }, http_status_counts: { '200': run.request_count - errors, [run.faults[0]?.http_status ?? 503]: errors } };
      };
      return { algorithm_version: 'demo-fixture-v2', stand_id: before.stand_id, change: changes[0], baseline: metrics(before), candidate: metrics(after), attempts_per_input_delta: metrics(after).attempts_per_input! - metrics(before).attempts_per_input!, input_hash: 'demo', warnings: ['Иллюстративные данные: реальные HTTP-запросы не выполнялись.'] };
    },
  } satisfies Partial<Client>;
  return api;
}
