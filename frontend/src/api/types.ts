export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';
export type Outcome = 'ok' | 'error' | 'unknown';
export type SourceMode = 'demo' | 'live';
export type Route = { page: 'traces'; traceId?: string; traceRef?: TraceRef } | { page: 'audit' | 'journal' | 'sources' | 'graph' | 'timings' | 'policies' | 'compare' | 'lab' };
export interface TraceRef { environment_id: string; trace_id: string; generation: number; revision: number }
export interface TraceSummary {
  trace_ref: TraceRef;
  trace_id: string; operation: string; service: string; start_ns: string; duration_ns: string;
  span_count: number; service_count: number; outcome: Outcome; http_status: number | null;
  finding_count: number; quality_issues: string[];
}
export interface Span {
  span_id: string; parent_span_id: string | null; service: string; name: string;
  kind: 'server' | 'client' | 'internal'; start_offset_ns: string; duration_ns: string;
  outcome: Outcome; http_status: number | null; error_type: string | null;
}
export interface TraceDetail { summary: TraceSummary; trace_ref: TraceRef; spans: Span[]; quality_issues: string[] }
export interface Finding {
  finding_id: string; occurrence_id: string; recommendation_id: string; rule_id: string; rule_version: string;
  title: string; severity: 'high' | 'medium' | 'low'; confidence: 'high' | 'medium' | 'low';
  basis: 'config_aware' | 'observed' | 'inferred'; edge: { caller: string; callee: string; operation: string };
  explanation: string; affected_count: number; denominator_count: number; exemplars: TraceRef[];
  evidence: BudgetEvidence | TraceEvidence | PolicyEvidence;
  recommendation: { can_prepare_plan?: boolean; text: string; why: string; risks: string[]; changes: { field: string; before: string; after: string }[] };
}
export interface BudgetEvidence {
  kind: 'retry_budget'; attempts: number; timeout_ns: string; backoff_total_ns: string;
  required_ns: string; budget_ns: string; available_ns?: string; config_revision: string; source: string;
}
export interface TraceEvidence { kind: 'trace_observation'; source: string }
export interface PolicyEvidence { kind: 'policy_observation'; source: string }
export function availableBudget(e: BudgetEvidence): string {
  return e.available_ns ?? e.budget_ns;
}
export type BudgetFinding = Finding & { evidence: BudgetEvidence };
export function isBudgetFinding(finding: Finding): finding is BudgetFinding {
  return finding.evidence.kind === 'retry_budget';
}
export function canPreparePlan(finding: Finding): finding is BudgetFinding {
  return isBudgetFinding(finding) && (finding.recommendation.can_prepare_plan ?? finding.recommendation.changes.length > 0);
}
export interface AuditResult { result_id: string; generated_at: string; findings: Finding[]; rule_evaluations: { rule_id: string; status: 'evaluated' | 'not_implemented' | 'insufficient_data' | 'not_applicable'; eligible_count: number }[] }
export interface ChangePlan { plan_id: string; plan_hash: string; finding_id: string; target: string; expected_revision: string; expires_at: string; changes: { field: string; before: string; after: string }[]; warnings: string[]; apply_enabled: boolean }
export interface JournalEntry { operation_id: string; target: string; requested_at: string; status: 'planned' | 'applying' | 'applied' | 'no_op' | 'rejected' | 'unknown' | 'failed'; verification: 'checking' | 'insufficient_data' | 'symptom_absent' | 'symptom_present' | 'inconclusive' | null; actual_changes: { field: string; before: string; after: string }[] }
export interface Capabilities { mode: string; api_version: string; source_name: string; source_state: 'receiving' | 'idle' | 'unknown'; apply_enabled: boolean; limits: { max_trace_spans: number } }
export interface ListPayload { items: TraceSummary[]; total: number }
export interface Envelope<T> { schema: string; schema_version: string; generated_at: string; producer_version: string; payload: T }
export interface Connection { mode: SourceMode; baseUrl: string; environmentId?: string }
export interface Client { listTraces(signal?: AbortSignal): Promise<ListPayload>; getTrace(id: string | TraceRef, signal?: AbortSignal): Promise<TraceDetail>; getAudit(signal?: AbortSignal): Promise<AuditResult>; getCapabilities(signal?: AbortSignal): Promise<Capabilities>; getJournal(signal?: AbortSignal): Promise<JournalEntry[]>; createPlan(finding: BudgetFinding, signal?: AbortSignal): Promise<ChangePlan> }
