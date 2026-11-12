import type { AuditResult, Capabilities, ChangePlan, BudgetFinding, JournalEntry, Span, TraceDetail, TraceSummary } from './types.js';
export const FIXTURE_DATE = '2026-11-10T11:24:00.000Z';
const demoNow = BigInt(Date.now() - 60_000) * 1_000_000n;
const ns = (ms: number) => String(Math.round(ms * 1e6));
const monitoringTraces: TraceSummary[] = Array.from({ length: 18 }, (_, i) => ({
  trace_id: (0x4bf92000 + i).toString(16).padStart(32, '0'),
  trace_ref: { environment_id: 'demo-shop', trace_id: (0x4bf92000 + i).toString(16).padStart(32, '0'), generation: 1, revision: 2 },
  operation: i >= 5 && i % 5 === 4 ? 'GET /catalog' : 'POST /checkout',
  service: i >= 5 && i % 5 === 4 ? 'catalog-api' : 'gateway',
  start_ns: String(demoNow - BigInt(i) * 14700000000n),
  duration_ns: ns(i >= 5 && i % 5 === 4 ? 126 + i : i < 5 ? 2015 + i * 8 : 245 + i * 19),
  span_count: i < 5 ? 10 : 6, service_count: i < 5 ? 3 : 3,
  outcome: i === 8 ? 'unknown' : i < 5 ? 'error' : 'ok', http_status: i === 8 ? null : i < 5 ? 504 : 200,
  finding_count: i < 5 ? 1 : 0, quality_issues: i === 8 ? ['Отсутствует родитель одного спана. Результат запроса неизвестен.'] : [],
}));
const makeSpan = (id: number, parent: number | null, service: string, name: string, kind: Span['kind'], start: number, duration: number, outcome: Span['outcome'] = 'ok', status: number | null = 200, error: string | null = null): Span => ({ span_id: id.toString(16).padStart(16, '0'), parent_span_id: parent === null ? null : parent.toString(16).padStart(16, '0'), service, name, kind, start_offset_ns: ns(start), duration_ns: ns(duration), outcome, http_status: status, error_type: error });
const labSourceId = '0000000000000000000000004bf92012';
const labSourceRef = { environment_id: 'demo-shop', trace_id: labSourceId, generation: 1, revision: 2 };
export const labSourceTrace: TraceDetail = {
  trace_ref: labSourceRef,
  summary: {
    ...monitoringTraces[0], trace_id: labSourceId,
    trace_ref: labSourceRef,
    operation: 'POST /orders', service: 'orders', start_ns: String(demoNow + 10_000_000_000n),
    duration_ns: ns(25), span_count: 3, service_count: 2, outcome: 'ok', http_status: 200, finding_count: 0,
  },
  spans: [
    makeSpan(1, null, 'orders', 'POST /orders', 'server', 0, 25),
    makeSpan(2, 1, 'orders', 'POST payment /charge', 'client', 3, 20),
    makeSpan(3, 2, 'payment', 'POST /charge', 'server', 4, 18),
  ],
  quality_issues: [],
};
export const demoTraces = [...monitoringTraces, labSourceTrace.summary];
export function demoDetail(id: string): TraceDetail {
  if (id === labSourceId) return labSourceTrace;
  const summary = demoTraces.find(t => t.trace_id === id);
  if (!summary) throw new Error('Трейс не найден. Вернитесь к списку.');
  const bad = summary.finding_count > 0;
  const duration = Number(BigInt(summary.duration_ns)) / 1e6;
  const spans: Span[] = bad ? [
    makeSpan(1, null, 'gateway', 'POST /checkout', 'server', 0, duration, 'error', 504, 'timeout'),
    makeSpan(2, 1, 'gateway', 'POST orders /orders', 'client', 12, 2000, 'error', null, 'timeout'),
    makeSpan(3, 2, 'orders', 'POST /orders', 'server', 15, 3200, 'error', 502, null),
    makeSpan(4, 3, 'orders', 'POST payment /charge, попытка 1', 'client', 25, 1000, 'error', null, 'timeout'),
    makeSpan(5, 4, 'payment', 'POST /charge', 'server', 30, 1500),
    makeSpan(6, 3, 'orders', 'POST payment /charge, попытка 2', 'client', 1075, 1000, 'error', null, 'timeout'),
    makeSpan(7, 6, 'payment', 'POST /charge', 'server', 1080, 1500),
    makeSpan(8, 3, 'orders', 'POST payment /charge, попытка 3', 'client', 2175, 1000, 'error', null, 'timeout'),
    makeSpan(9, 8, 'payment', 'POST /charge', 'server', 2180, 1500),
    makeSpan(10, 1, 'gateway', 'serialize response', 'internal', 2008, 7),
  ] : [
    makeSpan(1, null, summary.service, summary.operation, 'server', 0, duration, summary.outcome, summary.http_status),
    makeSpan(2, 1, summary.service, 'GET inventory /stock', 'client', 20, duration * .48),
    makeSpan(3, 2, 'inventory', 'GET /stock', 'server', 24, duration * .44),
    makeSpan(4, 1, summary.service, 'GET pricing /quote', 'client', 30, duration * .62),
    makeSpan(5, 4, 'pricing', 'GET /quote', 'server', 35, duration * .57),
    makeSpan(6, 1, summary.service, 'serialize response', 'internal', duration - 15, 12),
  ];
  if (summary.outcome === 'unknown') spans[spans.length-1].parent_span_id = 'ffffffffffffffff';
  return { summary, trace_ref: summary.trace_ref, spans, quality_issues: [...summary.quality_issues, ...(bad ? ['Часть серверных операций продолжается после завершения входного запроса. Шкала включает весь полученный трейс.'] : [])] };
}
export const demoFinding: BudgetFinding = {
  finding_id: 'r4-orders-payment', occurrence_id: 'occ-demo-001', recommendation_id: 'rec-demo-001', rule_id: 'R4', rule_version: '1.0.0',
  title: 'Повторы не укладываются в таймаут вызывающего сервиса', severity: 'high', confidence: 'high', basis: 'config_aware',
  edge: { caller: 'orders', callee: 'payment', operation: 'POST /charge' },
  explanation: 'Gateway ждёт ответ от orders 2 секунды. При этом orders может потратить на вызов payment до 3,15 секунды: три попытки по секунде и две паузы. Вызовы payment могут продолжаться после того, как gateway перестанет ждать ответ.',
  affected_count: 5, denominator_count: 5, exemplars: monitoringTraces.slice(0, 5).map(t => t.trace_ref),
  evidence: { kind: 'retry_budget', attempts: 3, timeout_ns: ns(1000), backoff_total_ns: ns(150), required_ns: ns(3150), budget_ns: ns(2000), config_revision: '12', source: 'Конфигурация демостенда' },
  recommendation: { text: 'Ограничить вызов payment двумя попытками по 800 мс.', why: 'Две попытки и пауза 50 мс потребуют до 1 650 мс. Останется 350 мс на прочую работу orders. Это проверка времени отдельных попыток; она не гарантирует длительность всего запроса.', risks: ['Сокращение таймаута может увеличить число отказов, если payment стабильно отвечает дольше 800 мс.', 'Перед применением нужно подтвердить время успешного ответа и свежесть конфигурации.'], changes: [{ field: 'Таймаут одной попытки', before: '1 000 мс', after: '800 мс' }, { field: 'Число попыток', before: '3', after: '2' }] },
};
export const demoAudit: AuditResult = { result_id: 'audit-demo-001', generated_at: FIXTURE_DATE, findings: [demoFinding], rule_evaluations: [{ rule_id: 'R4', status: 'evaluated', eligible_count: 5 }, ...['R1', 'R2', 'R3', 'R5', 'R6', 'R7', 'R8', 'D1'].map(rule_id => ({ rule_id, status: 'not_implemented' as const, eligible_count: 0 }))] };
export const demoCapabilities: Capabilities = { mode: 'demo', api_version: '1.0.0', source_name: 'Интернет-магазин (демоданные)', source_state: 'unknown', apply_enabled: false, limits: { max_trace_spans: 2000 } };
export function demoPlan(finding: BudgetFinding): ChangePlan { return { plan_id: 'plan-demo-001', plan_hash: 'demo-plan-readonly', finding_id: finding.finding_id, target: `${finding.edge.caller} / ${finding.edge.operation}`, expected_revision: finding.evidence.config_revision, expires_at: '2026-11-10T23:59:59Z', changes: finding.recommendation.changes, warnings: ['Демонстрационный план. Изменения не отправляются ни в один сервис.', 'Для изменения настроек подключите сервер с разрешением на их изменение.'], apply_enabled: false }; }
export const demoJournal: JournalEntry[] = [];
