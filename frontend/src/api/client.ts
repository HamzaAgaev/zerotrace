import { createDemoLab } from './demoLab.js';
import { filterTraces, traceRequest } from '../lib.js';
import type { ApplyResult, AuditResult, Capabilities, ChangePlan, Client, ConfigSnapshot, Connection, Environment, JournalEntry, LabFault, LabRun, ListPayload, ManagedService, ManualChangeIntent, RunComparison, Stand, TraceDetail, TraceRef } from './types.js';
import { demoAudit, demoCapabilities, demoDetail, demoPlan, demoTraces } from './fixtures.js';
import { successWindowsMinutes } from './types.js';
import type { BudgetFinding, SuccessRates } from './types.js';

export class ApiError extends Error {
  constructor(message: string, public status = 0, public code = 'unknown') { super(message); this.name = 'ApiError'; }
}

export function isUncertainResponse(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 0 || error.status >= 500
    || (error.status >= 200 && error.status < 300 && error.code === 'invalid_json'));
}

export async function createOrRecoverStand(
  client: Client, source?: TraceRef, onProgress?: (stand: Stand) => void,
): Promise<Stand> {
  const signal = AbortSignal.timeout(22_000);
  let current: Stand | null = null;
  try {
    current = await (source ? client.createStandFromTrace(source, signal)
      : client.createStand('checkout', signal));
  } catch (error) {
    if (!(error instanceof ApiError) || (error.status !== 0 && error.code !== 'stand_already_active'
        && error.code !== 'stand_exists' && error.code !== 'invalid_json')) throw error;
  }

  while (!signal.aborted) {
    if (current) {
      const matches = source ? current.source && sameTraceRef(current.source, source)
        : current.scenario_id === 'checkout' && !current.source;
      if (!matches) throw new Error('Активен другой стенд. Откройте лабораторию, чтобы посмотреть его состояние.');
      onProgress?.(current);
      if (current.status === 'ready') return current;
      if (current.status !== 'building') throw new Error(`Стенд в состоянии «${current.status}». Проверьте его в лаборатории.`);
    }
    try {
      await pause(signal, 350);
      current = await client.getActiveStand(signal);
    } catch (error) {
      if (!isUncertainResponse(error) && !signal.aborted) throw error;
    }
  }
  throw new Error('Не удалось подтвердить сборку за 22 секунды. Проверьте состояние стенда в лаборатории.');
}

type PendingAudit = { status: 'pending'; job_id: string };

function isPendingAudit(value: unknown): value is PendingAudit {
  return obj(value) && value.status === 'pending' && typeof value.job_id === 'string'
    && /^[a-f0-9]{32}$/.test(value.job_id);
}

export function normalizeBaseUrl(input: string): string {
  if (!input.trim()) return '';
  let url: URL;
  try { url = new URL(input.trim()); } catch { throw new ApiError('Укажите полный адрес сервера, например http://localhost:8000.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new ApiError('Нужен адрес HTTP или HTTPS без пароля, параметров и фрагмента.');
  return url.href.replace(/\/$/, '');
}

const obj = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const str = (value: unknown): value is string => typeof value === 'string';
const count = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const num = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const nanos = (value: unknown): value is string => str(value) && /^\d+$/.test(value);
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(str);
const outcome = (value: unknown) => ['ok', 'error', 'unknown'].includes(String(value));
const http = (value: unknown) => value === null || (count(value) && value >= 100 && value <= 599);
const changes = (value: unknown) => Array.isArray(value) && value.every(change => obj(change) && str(change.field) && str(change.before) && str(change.after));

export function unwrap(value: unknown): unknown {
  if (!obj(value) || typeof value.schema_version !== 'string' || !/^1\./.test(value.schema_version) || !('payload' in value)) throw new ApiError('Версия ответа сервера не поддерживается. Проверьте совместимость фронтенда и сервера.', 0, 'schema_mismatch');
  return value.payload;
}

function isTraceRef(value: unknown): value is TraceRef { return obj(value) && str(value.environment_id) && str(value.trace_id) && /^[a-f0-9]{32}$/.test(value.trace_id) && count(value.generation) && count(value.revision); }
export function sameTraceRef(a: TraceRef, b: TraceRef): boolean { return a.environment_id === b.environment_id && a.trace_id === b.trace_id && a.generation === b.generation && a.revision === b.revision; }
function validSummary(value: unknown): boolean { return obj(value) && isTraceRef(value.trace_ref) && value.trace_id === value.trace_ref.trace_id && str(value.operation) && (value.http_method === undefined || value.http_method === null || str(value.http_method)) && (value.endpoint === undefined || value.endpoint === null || str(value.endpoint)) && str(value.service) && nanos(value.start_ns) && nanos(value.duration_ns) && outcome(value.outcome) && http(value.http_status) && count(value.span_count) && count(value.service_count) && count(value.finding_count) && strings(value.quality_issues); }
function validWork(value: unknown): boolean { return obj(value) && ((value.kind === 'fixed' && num(value.delay_ms) && value.delay_ms >= 0) || (value.kind === 'lognormal' && num(value.p50_ms) && num(value.p99_ms) && value.p50_ms > 0 && value.p99_ms >= value.p50_ms)); }
function validConfig(value: unknown): boolean { return obj(value) && str(value.schema_version) && str(value.service_id) && str(value.revision) && Array.isArray(value.calls) && value.calls.every(call => obj(call) && str(call.call_ref) && obj(call.target) && str(call.target.service_id) && str(call.target.method) && str(call.target.route) && num(call.timeout_ms) && num(call.max_attempts) && num(call.backoff_ms) && strings(call.retry_on) && strings(call.write_fields)) && Array.isArray(value.routes) && value.routes.every(route => obj(route) && str(route.route) && validWork(route.work) && strings(route.write_fields) && (route.boundaries === undefined || (Array.isArray(route.boundaries) && route.boundaries.every(boundary => obj(boundary) && str(boundary.boundary_id) && str(boundary.label))))); }
function validJournal(value: unknown): boolean { return obj(value) && str(value.operation_id) && str(value.target) && str(value.requested_at) && ['planned', 'applying', 'applied', 'no_op', 'rejected', 'unknown', 'failed'].includes(String(value.status)) && (value.verification === null || ['checking', 'insufficient_data', 'symptom_absent', 'symptom_present', 'inconclusive'].includes(String(value.verification))) && changes(value.actual_changes); }
function validPlan(value: unknown): boolean { return obj(value) && str(value.plan_id) && str(value.plan_hash) && str(value.finding_id) && str(value.target) && str(value.expected_revision) && str(value.expires_at) && changes(value.changes) && strings(value.warnings) && typeof value.apply_enabled === 'boolean'; }
function validStand(value: unknown): boolean { return value === null || (obj(value) && str(value.stand_id) && ['building', 'ready', 'stopping', 'stopped', 'failed', 'unavailable'].includes(String(value.status)) && str(value.scenario_id) && count(value.scenario_revision) && count(value.fault_revision) && obj(value.config_revisions) && Array.isArray(value.services) && Array.isArray(value.faults) && (value.origins === undefined || Array.isArray(value.origins) && value.origins.every(origin => obj(origin) && str(origin.service_id) && str(origin.field) && ['observed', 'default'].includes(String(origin.source))))); }
function validRun(value: unknown): boolean { return obj(value) && str(value.run_id) && str(value.stand_id) && ['running', 'completed', 'failed'].includes(String(value.status)) && (str(value.seed) || num(value.seed)) && count(value.scenario_revision) && count(value.fault_revision) && obj(value.config_revisions) && count(value.request_count) && count(value.concurrency) && count(value.completed_requests) && obj(value.trace_collection) && ['waiting', 'ready', 'partial'].includes(String(value.trace_collection.status)) && count(value.trace_collection.expected_count) && count(value.trace_collection.received_count) && str(value.started_at); }
function validFinding(value: unknown): boolean { if (!obj(value) || !obj(value.edge) || !obj(value.evidence) || !obj(value.recommendation)) return false; const edge = value.edge, evidence = value.evidence, recommendation = value.recommendation; const validEvidence = str(evidence.source) && ((evidence.kind === 'retry_budget' && value.basis === 'config_aware' && count(evidence.attempts) && ['timeout_ns', 'backoff_total_ns', 'required_ns', 'budget_ns'].every(key => nanos(evidence[key])) && str(evidence.config_revision)) || (evidence.kind === 'policy_observation' && value.basis === 'config_aware') || (evidence.kind === 'trace_observation' && ['observed', 'inferred'].includes(String(value.basis)))); return ['finding_id', 'occurrence_id', 'recommendation_id', 'rule_id', 'rule_version', 'title', 'explanation'].every(key => str(value[key])) && ['high', 'medium', 'low'].includes(String(value.severity)) && ['high', 'medium', 'low'].includes(String(value.confidence)) && ['config_aware', 'observed', 'inferred'].includes(String(value.basis)) && ['caller', 'callee', 'operation'].every(key => str(edge[key])) && count(value.affected_count) && count(value.denominator_count) && Array.isArray(value.exemplars) && value.exemplars.every(isTraceRef) && validEvidence && str(recommendation.text) && str(recommendation.why) && strings(recommendation.risks) && changes(recommendation.changes); }

function validSuccessRates(value: unknown): boolean {
  if (!obj(value)) return false;
  return successWindowsMinutes.every(minutes => {
    const rate = value[String(minutes)];
    return rate === null || (num(rate) && rate >= 0 && rate <= 1);
  });
}

function demoSuccessRates(): SuccessRates {
  const now = BigInt(Date.now()) * 1_000_000n;
  return Object.fromEntries(successWindowsMinutes.map(minutes => {
    const cutoff = now - BigInt(minutes) * 60_000_000_000n;
    const known = demoTraces.filter(trace => {
      const start = BigInt(trace.start_ns);
      return start > 0n && start >= cutoff && start <= now && trace.outcome !== 'unknown';
    });
    return [minutes, known.length ? known.filter(trace => trace.outcome === 'ok').length / known.length : null];
  })) as SuccessRates;
}

export function validatePayload(kind: 'list' | 'trace' | 'audit' | 'capabilities' | 'journal' | 'journal-entry' | 'plan' | 'environments' | 'services' | 'config' | 'operation' | 'stand' | 'run' | 'runs' | 'comparison', value: unknown): void {
  let valid = false;
  if (kind === 'list') valid = obj(value) && Array.isArray(value.items) && value.items.every(validSummary)
    && count(value.total) && (value.methods === undefined || strings(value.methods))
    && (value.success_rates === undefined || validSuccessRates(value.success_rates));
  if (kind === 'trace') valid = obj(value) && obj(value.summary) && validSummary(value.summary) && isTraceRef(value.trace_ref) && sameTraceRef(value.summary.trace_ref as TraceRef, value.trace_ref) && (value.finding_occurrence_ids === undefined || strings(value.finding_occurrence_ids)) && (value.compactable_client_spans === undefined || (obj(value.compactable_client_spans) && Object.entries(value.compactable_client_spans).every(([client, server]) => /^[a-f0-9]{16}$/.test(client) && str(server) && /^[a-f0-9]{16}$/.test(server)))) && strings(value.quality_issues) && Array.isArray(value.spans) && value.spans.every(span => obj(span) && str(span.span_id) && /^[a-f0-9]{16}$/.test(span.span_id) && (span.parent_span_id === null || str(span.parent_span_id)) && str(span.service) && str(span.name) && (span.kind === undefined || ['server', 'client', 'internal'].includes(String(span.kind))) && nanos(span.duration_ns) && nanos(span.start_offset_ns) && outcome(span.outcome) && http(span.http_status) && (span.error_type === null || str(span.error_type)) && (span.target === undefined || str(span.target)));
  if (kind === 'audit') valid = obj(value) && str(value.result_id) && str(value.generated_at) && Array.isArray(value.rule_evaluations) && value.rule_evaluations.every(rule => obj(rule) && str(rule.rule_id) && ['evaluated', 'not_implemented', 'insufficient_data', 'not_applicable'].includes(String(rule.status)) && count(rule.eligible_count)) && Array.isArray(value.findings) && value.findings.every(validFinding) && (value.trace_findings === undefined || (Array.isArray(value.trace_findings) && value.trace_findings.every(row => obj(row) && isTraceRef(row.trace_ref) && strings(row.occurrence_ids))));
  if (kind === 'capabilities') valid = obj(value) && str(value.mode) && str(value.api_version) && str(value.source_name) && ['receiving', 'idle', 'unknown'].includes(String(value.source_state)) && typeof value.apply_enabled === 'boolean' && obj(value.limits) && count(value.limits.max_trace_spans);
  if (kind === 'journal') valid = Array.isArray(value) && value.every(validJournal);
  if (kind === 'journal-entry') valid = validJournal(value);
  if (kind === 'plan') valid = validPlan(value);
  if (kind === 'environments') valid = obj(value) && Array.isArray(value.items) && value.items.every(environment => obj(environment) && str(environment.environment_id) && str(environment.name) && ['external', 'lab'].includes(String(environment.kind)));
  if (kind === 'services') valid = obj(value) && Array.isArray(value.items) && value.items.every(service => obj(service) && str(service.service_id) && str(service.display_name) && typeof service.read_config === 'boolean' && strings(service.write_fields) && typeof service.is_lab === 'boolean');
  if (kind === 'config') valid = validConfig(value);
  if (kind === 'operation') valid = obj(value) && str(value.operation_id) && ['planned', 'applying', 'applied', 'no_op', 'rejected', 'unknown', 'failed'].includes(String(value.status));
  if (kind === 'stand') valid = validStand(value);
  if (kind === 'run') valid = validRun(value);
  if (kind === 'runs') valid = obj(value) && Array.isArray(value.items) && value.items.every(validRun);
  if (kind === 'comparison') valid = obj(value) && str(value.algorithm_version) && str(value.stand_id) && obj(value.change) && str(value.change.service_id) && str(value.change.call_ref) && str(value.change.field) && 'before' in value.change && 'after' in value.change && str(value.input_hash) && Array.isArray(value.warnings) && value.warnings.every(str) && [value.baseline, value.candidate].every(row => obj(row) && str(row.run_id) && str(row.config_revision) && ['ready', 'partial'].includes(String(row.collection_status)) && ['received_count', 'expected_count', 'attempts', 'retries', 'root_errors', 'internal_error_trace_count'].every(key => count(row[key])) && (row.attempts_per_input === null || num(row.attempts_per_input)) && (row.root_latency_p50_ns === null || nanos(row.root_latency_p50_ns)) && (row.root_latency_p95_ns === null || nanos(row.root_latency_p95_ns)) && (row.root_error_ratio === null || num(row.root_error_ratio)) && (row.internal_error_trace_ratio === null || num(row.internal_error_trace_ratio)) && obj(row.attempt_count_histogram) && obj(row.http_status_counts)) && (value.attempts_per_input_delta === null || num(value.attempts_per_input_delta));
  if (!valid) throw new ApiError('Сервер вернул данные в неподдерживаемом формате.', 0, 'invalid_payload');
}

function pause(signal?: AbortSignal, delay = 180): Promise<void> { return new Promise((resolve, reject) => { if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError')); const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, delay); const onAbort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }; signal?.addEventListener('abort', onAbort, { once: true }); }); }

function selectSpanFields(detail: TraceDetail, includeKind: boolean): TraceDetail {
  if (!includeKind) {
    // Keep structural pairing before omitting kind from the presentation payload.
    const pairs = { ...detail.compactable_client_spans };
    for (const span of detail.spans) {
      if (span.kind !== 'client') continue;
      const children = detail.spans.filter(child => child.parent_span_id === span.span_id);
      if (children.length === 1 && children[0].kind === 'server' && children[0].service !== span.service) pairs[span.span_id] = children[0].span_id;
    }
    return { ...detail, compactable_client_spans: pairs, spans: detail.spans.map(({ kind: _kind, ...span }) => span) };
  }
  if (detail.spans.some(span => span.kind === undefined)) {
    throw new ApiError('Сервер не вернул тип спана для анализа.', 0, 'invalid_payload');
  }
  return detail;
}

export function createClient(connection: Connection, fetcher: typeof fetch = fetch): Client {
  const base = normalizeBaseUrl(connection.baseUrl), environmentId = connection.environmentId || 'shop';
  const scoped = (path: string, params: Record<string, string> = {}, environment = environmentId) => `${path}?${new URLSearchParams({ environment_id: environment, ...params })}`;
  async function request<T>(path: string, kind: Parameters<typeof validatePayload>[0], signal?: AbortSignal, body?: unknown, method = 'GET', timeoutSeconds = 5): Promise<T> {
    const timeout = AbortSignal.timeout(timeoutSeconds * 1000);
    const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
    const timeoutError = () => new ApiError(`Сервер не ответил за ${timeoutSeconds} секунд. Попробуйте ещё раз.`, 0, 'timeout');
    let response: Response;
    try { response = await fetcher(`${base}${path}`, { method, signal: requestSignal, headers: { Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) }); } catch (error) { if (signal?.aborted) throw error; if (timeout.aborted) throw timeoutError(); if (error instanceof Error && error.name === 'AbortError') throw error; throw new ApiError('Не удалось подключиться к серверу. Проверьте адрес и доступность API.'); }
    if (response.status === 204) return undefined as T;
    let raw: unknown;
    try {
      raw = await response.json();
    } catch (error) {
      if (signal?.aborted) throw error;
      if (timeout.aborted) throw timeoutError();
      throw new ApiError('Сервер вернул ответ не в формате JSON. Проверьте адрес сервера.', response.status, 'invalid_json');
    }
    if (!response.ok) { const error = obj(raw) && obj(raw.error) ? raw.error : null; throw new ApiError(typeof error?.message === 'string' ? error.message : `Ошибка сервера: HTTP ${response.status}`, response.status, typeof error?.code === 'string' ? error.code : 'http_error'); }
    const payload = unwrap(raw);
    if (kind === 'audit' && response.status === 202 && isPendingAudit(payload)) return payload as T;
    validatePayload(kind, payload);
    return payload as T;
  }
  async function getAudit(signal?: AbortSignal, traceRef?: TraceRef): Promise<AuditResult> {
    const query = { view: 'full', defer: 'true', ...(traceRef ? { trace_id: traceRef.trace_id, generation: String(traceRef.generation), revision: String(traceRef.revision) } : {}) };
    const deadline = Date.now() + 45_000;
    let result = await request<AuditResult | PendingAudit>(scoped('/api/v1/audit', query, traceRef?.environment_id), 'audit', signal);
    while (isPendingAudit(result)) {
      if (Date.now() >= deadline) throw new ApiError('Аудит ещё рассчитывается. Попробуйте ещё раз.', 0, 'audit_pending');
      await pause(signal, 750);
      result = await request<AuditResult | PendingAudit>(
        scoped('/api/v1/audit', { ...query, job_id: result.job_id }, traceRef?.environment_id), 'audit', signal,
      );
    }
    return result;
  }
  const unsupported = (name: string) => async (): Promise<never> => { throw new ApiError(`${name} доступен только при подключении к серверу.`, 400, 'demo_readonly'); };
  if (connection.mode === 'live') return {
    listTraces: (signal, query = {}) => request<ListPayload>(scoped('/api/v1/traces', {
      limit: String(query.limit ?? 100), offset: String(query.offset ?? 0),
      ...(query.includeFindings === false ? { include_findings: 'false' } : {}),
      ...(query.includeSuccessRates ? { include_success_rates: 'true' } : {}),
      ...(query.sort ? { sort: query.sort } : {}), ...(query.query ? { query: query.query } : {}),
      ...(query.service ? { service: query.service } : {}), ...(query.status ? { status: query.status } : {}),
      ...(query.method ? { method: query.method } : {}),
      ...(query.span_id !== undefined ? { span_id: query.span_id } : {}),
      ...(query.start_from_ns !== undefined ? { start_from_ns: query.start_from_ns } : {}),
      ...(query.start_to_ns !== undefined ? { start_to_ns: query.start_to_ns } : {}),
    }), 'list', signal, undefined, 'GET',
    query.includeFindings === false && query.includeSuccessRates ? 10 : 5),
    async getTrace(ref, signal, options) {
      const id = typeof ref === 'string' ? ref : ref.trace_id;
      const environment = typeof ref === 'string' ? environmentId : ref.environment_id;
      const params: Record<string, string> = {};
      if (typeof ref !== 'string') {
        params.generation = String(ref.generation);
        params.revision = String(ref.revision);
      }
      if (options?.includeFindings === false) params.include_findings = 'false';
      if (options?.includeSpanKind === false) params.include_span_kind = 'false';
      const detail = await request<TraceDetail>(scoped(`/api/v1/traces/${encodeURIComponent(id)}`, params, environment), 'trace', signal);
      if (detail.trace_ref.environment_id !== environment || detail.trace_ref.trace_id !== id
          || (typeof ref !== 'string' && !sameTraceRef(ref, detail.trace_ref))) {
        throw new ApiError('Сервер вернул другой трейс или другую версию трейса.', 0, 'trace_ref_mismatch');
      }
      return selectSpanFields(detail, options?.includeSpanKind !== false);
    },
    getAudit, getCapabilities: signal => request<Capabilities>(scoped('/api/v1/capabilities'), 'capabilities', signal), getJournal: signal => request<JournalEntry[]>(scoped('/api/v1/journal'), 'journal', signal), getJournalEntry: (id, signal) => request<JournalEntry>(scoped(`/api/v1/journal/${encodeURIComponent(id)}`), 'journal-entry', signal), getJournalEntryForEnvironment: (id, env, signal) => request<JournalEntry>(scoped(`/api/v1/journal/${encodeURIComponent(id)}`, {}, env), 'journal-entry', signal),
    createPlan: (finding, signal) => request<ChangePlan>('/api/v1/change-plans', 'plan', signal, { environment_id: environmentId, occurrence_id: finding.occurrence_id, recommendation_id: finding.recommendation_id, finding_id: finding.finding_id, expected_config_revision: finding.evidence.config_revision }, 'POST'), listEnvironments: async signal => { const payload = await request<{ items: Environment[] }>(`/api/v1/environments`, 'environments', signal); return payload.items; }, listServices: async (environment = environmentId, signal) => { const payload = await request<{ items: ManagedService[] }>(scoped('/api/v1/services', {}, environment), 'services', signal); return payload.items; }, getConfig: (id, environment = environmentId, signal) => request<ConfigSnapshot>(scoped(`/api/v1/services/${encodeURIComponent(id)}/config`, {}, environment), 'config', signal), createManualPlan: (intent, signal) => request<ChangePlan>('/api/v1/change-plans', 'plan', signal, { ...intent, environment_id: intent.environment_id || environmentId }, 'POST'), applyPlan: (id, hash, requestId, signal) => request<ApplyResult>(`/api/v1/change-plans/${encodeURIComponent(id)}/apply`, 'operation', signal, { request_id: requestId, plan_hash: hash }, 'POST'),
    getActiveStand: signal => request<Stand | null>('/api/v1/lab/stands/active', 'stand', signal), createStand: (scenarioId, signal) => request<Stand>('/api/v1/lab/stands', 'stand', signal, { scenario_id: scenarioId }, 'POST'), createStandFromTrace: (source, signal) => request<Stand>('/api/v1/lab/stands', 'stand', signal, { source }, 'POST'), stopStand: (id, signal) => request<Stand>(`/api/v1/lab/stands/${encodeURIComponent(id)}`, 'stand', signal, undefined, 'DELETE'), setFault: (id, fault, revision, requestId, signal) => request<Stand>(`/api/v1/lab/stands/${encodeURIComponent(id)}/fault`, 'stand', signal, { ...fault, request_id: requestId, expected_fault_revision: revision }, 'PUT'), clearFault: (id, revision, requestId, signal) => request<Stand>(`/api/v1/lab/stands/${encodeURIComponent(id)}/fault`, 'stand', signal, { request_id: requestId, expected_fault_revision: revision }, 'DELETE'), startRun: (id, payload, signal) => request<LabRun>(`/api/v1/lab/stands/${encodeURIComponent(id)}/runs`, 'run', signal, payload, 'POST'), getRun: (id, signal) => request<LabRun>(`/api/v1/lab/runs/${encodeURIComponent(id)}`, 'run', signal), async listRuns(signal) { const payload = await request<{ items: LabRun[] }>('/api/v1/lab/runs', 'runs', signal); return payload.items; }, compareRuns: (baseline, candidate, signal) => request<RunComparison>('/api/v1/comparisons', 'comparison', signal, { baseline_run_id: baseline, candidate_run_id: candidate }, 'POST'),
  };
  return { listTraces: async (signal, query = {}) => {
    await pause(signal);
    const method = query.method?.toUpperCase();
    const spanId = query.span_id;
    if (spanId !== undefined && (!/^[a-f0-9]{16}$/.test(spanId) || spanId === '0000000000000000')) {
      throw new ApiError('Введите 16 шестнадцатеричных символов для Span ID.', 400, 'invalid_span_id');
    }
    const boundary = (input: string | undefined, name: string) => {
      if (input === undefined) return undefined;
      if (!/^[0-9]{1,19}$/.test(input) || BigInt(input) > 9223372036854775807n) {
        throw new ApiError('Некорректная граница периода.', 400, `invalid_${name}`);
      }
      return BigInt(input);
    };
    const from = boundary(query.start_from_ns, 'start_from_ns');
    const to = boundary(query.start_to_ns, 'start_to_ns');
    if (from !== undefined && to !== undefined && from >= to) {
      throw new ApiError('Начало периода должно быть раньше конца.', 400, 'invalid_time_range');
    }
    const methods = [...new Set(demoTraces.flatMap(trace => traceRequest(trace).method ?? []))].sort();
    const filtered = filterTraces(demoTraces, query.query ?? '', query.service ?? '', query.status ?? '', query.sort ?? 'recent')
      .filter(trace => {
        if (method && traceRequest(trace).method !== method) return false;
        const start = BigInt(trace.start_ns);
        if ((from !== undefined || to !== undefined) && start === 0n) return false;
        if ((from !== undefined && start < from) || (to !== undefined && start >= to)) return false;
        return !spanId || demoDetail(trace.trace_id).spans.some(span => span.span_id === spanId);
      });
    const offset = query.offset ?? 0;
    const outcomes = { ok: 0, error: 0, unknown: 0 };
    for (const trace of filtered) outcomes[trace.outcome]++;
    return { items: filtered.slice(offset, offset + (query.limit ?? 100)), total: filtered.length,
      services: [...new Set(demoTraces.map(trace => trace.service))], methods, outcomes,
      ...(query.includeSuccessRates ? { success_rates: demoSuccessRates() } : {}) };
  }, getTrace: async (id, signal, options) => { await pause(signal); const detail = demoDetail(typeof id === 'string' ? id : id.trace_id); if (typeof id !== 'string' && !sameTraceRef(id, detail.trace_ref)) throw new ApiError('Эта версия трейса не найдена.', 404, 'trace_ref_not_found'); return selectSpanFields({ ...detail, finding_occurrence_ids: demoAudit.findings.filter(f => f.exemplars.some(ref => sameTraceRef(ref, detail.trace_ref))).map(f => f.occurrence_id) }, options?.includeSpanKind !== false); }, getAudit: async signal => { await pause(signal); return demoAudit; }, getCapabilities: async signal => { await pause(signal); return demoCapabilities; }, createPlan: async (finding: BudgetFinding, signal?: AbortSignal) => { await pause(signal); return demoPlan(finding); }, listEnvironments: unsupported('Список окружений'), listServices: unsupported('Список сервисов'), ...createDemoLab(typeof localStorage === 'undefined' ? undefined : localStorage) };
}
