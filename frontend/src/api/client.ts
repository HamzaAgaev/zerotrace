import type { AuditResult, Capabilities, ChangePlan, Client, Connection, JournalEntry, ListPayload, TraceDetail, TraceRef } from './types.js';
import { canPreparePlan } from './types.js';
import { demoAudit, demoCapabilities, demoDetail, demoJournal, demoPlan, demoTraces } from './fixtures.js';
export class ApiError extends Error { constructor(message: string, public status: number = 0, public code: string = 'unknown') { super(message); this.name = 'ApiError'; } }
export function normalizeBaseUrl(input: string): string {
  if (!input.trim()) return '';
  let url: URL; try { url = new URL(input.trim()); } catch { throw new ApiError('Укажите полный адрес сервера, например http://localhost:8000.'); }
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) throw new ApiError('Нужен адрес HTTP или HTTPS без пароля, параметров и фрагмента.');
  return url.href.replace(/\/$/, '');
}
export function unwrap(value: unknown): unknown {
  if (!value || typeof value !== 'object') throw new ApiError('Сервер вернул некорректный ответ.', 0, 'invalid_response');
  const data = value as Record<string, unknown>;
  if (typeof data.schema_version !== 'string' || !/^1\./.test(data.schema_version) || !('payload' in data)) throw new ApiError('Формат ответа не соответствует API v1. Проверьте контракт подключения.', 0, 'schema_mismatch');
  return data.payload;
}
const obj = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const str = (v: unknown): v is string => typeof v === 'string';
const count = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const nanos = (v: unknown) => str(v) && /^\d+$/.test(v);
const signedNanos = (v: unknown) => str(v) && /^-?\d+$/.test(v);
const strings = (v: unknown): v is string[] => Array.isArray(v) && v.every(str);
const outcome = (v: unknown) => ['ok', 'error', 'unknown'].includes(String(v));
const http = (v: unknown) => v === null || (count(v) && v >= 100 && v <= 599);
const changes = (v: unknown) => Array.isArray(v) && v.every(c => obj(c) && str(c.field) && str(c.before) && str(c.after));
function isTraceRef(value: unknown): value is TraceRef {
  return obj(value) && str(value.environment_id) && value.environment_id.length > 0 &&
    str(value.trace_id) && /^[a-f0-9]{32}$/.test(value.trace_id) &&
    count(value.generation) && count(value.revision);
}
export function sameTraceRef(a: TraceRef, b: TraceRef): boolean {
  return a.environment_id === b.environment_id && a.trace_id === b.trace_id &&
    a.generation === b.generation && a.revision === b.revision;
}
function isSummary(t: unknown): t is ListPayload['items'][number] { return obj(t) && isTraceRef(t.trace_ref) && t.trace_id === t.trace_ref.trace_id && str(t.operation) && str(t.service) && nanos(t.start_ns) && nanos(t.duration_ns) && outcome(t.outcome) && http(t.http_status) && count(t.span_count) && count(t.service_count) && count(t.finding_count) && strings(t.quality_issues); }
function isFinding(f: unknown): boolean {
  if (!obj(f) || !obj(f.edge) || !obj(f.evidence) || !obj(f.recommendation)) return false;
  const e = f.evidence, r = f.recommendation;
  const validEvidence = str(e.source) && (
    (e.kind === 'retry_budget' && f.basis === 'config_aware' && count(e.attempts) &&
      ['timeout_ns', 'backoff_total_ns', 'required_ns', 'budget_ns'].every(k => nanos(e[k])) && (e.available_ns === undefined || signedNanos(e.available_ns)) && str(e.config_revision)) ||
    (e.kind === 'policy_observation' && f.basis === 'config_aware') ||
    (e.kind === 'trace_observation' && ['observed', 'inferred'].includes(String(f.basis)))
  );
  return ['finding_id','occurrence_id','recommendation_id','rule_id','rule_version','title','explanation'].every(k => str(f[k])) && ['high','medium','low'].includes(String(f.severity)) && ['high','medium','low'].includes(String(f.confidence)) && ['config_aware','observed','inferred'].includes(String(f.basis)) && ['caller','callee','operation'].every(k => str((f.edge as Record<string,unknown>)[k])) && count(f.affected_count) && count(f.denominator_count) && Array.isArray(f.exemplars) && f.exemplars.every(isTraceRef) && validEvidence && (r.can_prepare_plan === undefined || typeof r.can_prepare_plan === 'boolean') && str(r.text) && str(r.why) && strings(r.risks) && changes(r.changes);
}
export function validatePayload(kind: 'list' | 'trace' | 'audit' | 'capabilities' | 'journal' | 'plan', value: unknown): void {
  let valid = false;
  if (kind === 'journal') valid = Array.isArray(value) && value.every(e => obj(e) && str(e.operation_id) && str(e.target) && str(e.requested_at) && ['planned','applying','applied','no_op','rejected','unknown','failed'].includes(String(e.status)) && (e.verification === null || ['checking','insufficient_data','symptom_absent','symptom_present','inconclusive'].includes(String(e.verification))) && changes(e.actual_changes));
  if (obj(value)) {
    if (kind === 'list') valid = Array.isArray(value.items) && value.items.every(isSummary) && count(value.total);
    if (kind === 'trace') valid = isSummary(value.summary) &&
      (value.finding_occurrence_ids === undefined || (strings(value.finding_occurrence_ids)
        && new Set(value.finding_occurrence_ids).size === value.finding_occurrence_ids.length
        && value.finding_occurrence_ids.length === value.summary.finding_count)) && isTraceRef(value.trace_ref) && sameTraceRef(value.summary.trace_ref, value.trace_ref) && strings(value.quality_issues) && Array.isArray(value.spans) && value.spans.every(s => obj(s) && str(s.span_id) && /^[a-f0-9]{16}$/.test(s.span_id) && (s.parent_span_id === null || str(s.parent_span_id)) && str(s.service) && str(s.name) && ['server','client','internal'].includes(String(s.kind)) && nanos(s.duration_ns) && signedNanos(s.start_offset_ns) && outcome(s.outcome) && http(s.http_status) && (s.error_type === null || str(s.error_type)));
    if (kind === 'audit') valid = str(value.result_id) && str(value.generated_at) && Array.isArray(value.rule_evaluations) && value.rule_evaluations.every(r => obj(r) && str(r.rule_id) && ['evaluated','not_implemented','insufficient_data','not_applicable'].includes(String(r.status)) && count(r.eligible_count)) && Array.isArray(value.findings) && value.findings.every(isFinding);
    if (kind === 'capabilities') valid = str(value.mode) && str(value.api_version) && str(value.source_name) && ['receiving','idle','unknown'].includes(String(value.source_state)) && typeof value.apply_enabled === 'boolean' && obj(value.limits) && count(value.limits.max_trace_spans);
    if (kind === 'plan') valid = str(value.plan_id) && str(value.plan_hash) && str(value.finding_id) && str(value.target) && str(value.expected_revision) && str(value.expires_at) && changes(value.changes) && strings(value.warnings) && typeof value.apply_enabled === 'boolean';
  }
  if (!valid) throw new ApiError('В ответе сервера отсутствуют обязательные поля. Проверьте контракт API.', 0, 'invalid_payload');
}
function pause(signal?: AbortSignal): Promise<void> { return new Promise((resolve, reject) => { if (signal?.aborted) return reject(new DOMException('Aborted', 'AbortError')); const onAbort = () => { clearTimeout(timer); reject(new DOMException('Aborted', 'AbortError')); }; const timer = setTimeout(() => { signal?.removeEventListener('abort', onAbort); resolve(); }, 180); signal?.addEventListener('abort', onAbort, { once: true }); }); }
export function createClient(connection: Connection, fetcher: typeof fetch = fetch): Client {
  const base = normalizeBaseUrl(connection.baseUrl);
  async function request<T>(path: string, kind: Parameters<typeof validatePayload>[0], signal?: AbortSignal, body?: unknown): Promise<T> {
    let response: Response; const timeout = AbortSignal.timeout(8000); const requestSignal = signal ? AbortSignal.any([signal, timeout]) : timeout;
    try { response = await fetcher(`${base}${path}`, { signal: requestSignal, headers: { Accept: 'application/json', ...(body ? { 'Content-Type': 'application/json' } : {}) }, ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}) }); } catch (e) { if (signal?.aborted) throw e; if (timeout.aborted) throw new ApiError('Сервер не ответил за 8 секунд. Попробуйте ещё раз.', 0, 'timeout'); if (e instanceof Error && e.name === 'AbortError') throw e; throw new ApiError('Не удалось подключиться к серверу. Проверьте адрес, доступность API и настройку CORS.'); }
    let raw: unknown; try { raw = await response.json(); } catch { throw new ApiError('Сервер вернул не JSON. Проверьте адрес API.', response.status, 'invalid_json'); }
    if (!response.ok) { const error = obj(raw) && obj(raw.error) ? raw.error : null; throw new ApiError(typeof error?.message === 'string' ? error.message : `Ошибка сервера: HTTP ${response.status}`, response.status, typeof error?.code === 'string' ? error.code : 'http_error'); }
    const payload = unwrap(raw); validatePayload(kind, payload); return payload as T;
  }
  const environmentId = connection.environmentId || 'shop';
  function scoped(path: string, params: Record<string, string> = {}, environment = environmentId): string {
    return `${path}?${new URLSearchParams({ environment_id: environment, ...params })}`;
  }
  if (connection.mode === 'live') return {
    listTraces: signal => request<ListPayload>(scoped('/api/v1/traces', { limit: '100', offset: '0' }), 'list', signal),
    async getTrace(ref, signal) {
      const id = typeof ref === 'string' ? ref : ref.trace_id;
      const environment = typeof ref === 'string' ? environmentId : ref.environment_id;
      const revision: Record<string, string> = typeof ref === 'string' ? {} : { generation: String(ref.generation), revision: String(ref.revision) };
      const detail = await request<TraceDetail>(scoped(`/api/v1/traces/${encodeURIComponent(id)}`, revision, environment), 'trace', signal);
      if (detail.trace_ref.environment_id !== environment || detail.trace_ref.trace_id !== id ||
          (typeof ref !== 'string' && !sameTraceRef(ref, detail.trace_ref))) {
        throw new ApiError('Сервер вернул другую трассу или ревизию.', 0, 'trace_ref_mismatch');
      }
      return detail;
    },
    getAudit: signal => request<AuditResult>(scoped('/api/v1/audit', { view: 'full' }), 'audit', signal),
    getCapabilities: signal => request<Capabilities>(scoped('/api/v1/capabilities'), 'capabilities', signal),
    getJournal: signal => request<JournalEntry[]>(scoped('/api/v1/journal'), 'journal', signal),
    createPlan: async (finding, signal) => {
      if (!canPreparePlan(finding)) throw new ApiError('Для этой рекомендации нет доступного изменения.', 0, 'no_change_intent');
      return request<ChangePlan>('/api/v1/change-plans', 'plan', signal, { environment_id: environmentId, occurrence_id: finding.occurrence_id, recommendation_id: finding.recommendation_id, finding_id: finding.finding_id, expected_config_revision: finding.evidence.config_revision });
    },
  };
  return { listTraces: async signal => { await pause(signal); return { items: demoTraces, total: demoTraces.length }; }, getTrace: async (id, signal) => { await pause(signal); const detail = demoDetail(typeof id === 'string' ? id : id.trace_id); if (typeof id !== 'string' && !sameTraceRef(id, detail.trace_ref)) throw new ApiError('Эта ревизия трассы не найдена.', 404, 'trace_ref_not_found'); return detail; }, getAudit: async signal => { await pause(signal); return demoAudit; }, getCapabilities: async signal => { await pause(signal); return demoCapabilities; }, getJournal: async signal => { await pause(signal); return demoJournal; }, createPlan: async (finding, signal) => { if (!canPreparePlan(finding)) throw new ApiError('Для этой рекомендации нет доступного изменения.', 0, 'no_change_intent'); await pause(signal); return demoPlan(finding); } };
}
