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
  target?: string;
}
export interface TraceDetail { summary: TraceSummary; trace_ref: TraceRef; spans: Span[]; quality_issues: string[] }
export interface Finding {
  finding_id: string; occurrence_id: string; recommendation_id: string; rule_id: string; rule_version: string;
  title: string; severity: 'high' | 'medium' | 'low'; confidence: 'high' | 'medium' | 'low';
  basis: 'config_aware' | 'observed' | 'inferred'; edge: { caller: string; callee: string; operation: string };
  explanation: string; affected_count: number; denominator_count: number; exemplars: TraceRef[];
  evidence: BudgetEvidence | TraceEvidence;
  recommendation: { text: string; why: string; risks: string[]; changes: { field: string; before: string; after: string }[] };
}
export interface BudgetEvidence {
  kind: 'retry_budget'; attempts: number; timeout_ns: string; backoff_total_ns: string;
  required_ns: string; budget_ns: string; config_revision: string; source: string;
}
export interface TraceEvidence { kind: 'trace_observation'; source: string }
export type BudgetFinding = Finding & { evidence: BudgetEvidence };
export function isBudgetFinding(finding: Finding): finding is BudgetFinding {
  return finding.evidence.kind === 'retry_budget';
}
export interface AuditResult { result_id: string; generated_at: string; findings: Finding[]; rule_evaluations: { rule_id: string; status: 'evaluated' | 'not_implemented' | 'insufficient_data' | 'not_applicable'; eligible_count: number }[] }
export interface ChangePlan { plan_id: string; plan_hash: string; finding_id: string; target: string; expected_revision: string; environment_id?: string; expires_at: string; changes: { field: string; before: string; after: string }[]; warnings: string[]; apply_enabled: boolean }
export interface JournalEntry { operation_id: string; target: string; requested_at: string; status: 'planned' | 'applying' | 'applied' | 'no_op' | 'rejected' | 'unknown' | 'failed'; verification: 'checking' | 'insufficient_data' | 'symptom_absent' | 'symptom_present' | 'inconclusive' | null; actual_changes: { field: string; before: string; after: string }[] }
export interface Capabilities { mode: string; api_version: string; source_name: string; source_state: 'receiving' | 'idle' | 'unknown'; apply_enabled: boolean; limits: { max_trace_spans: number } }
export interface ListPayload { items: TraceSummary[]; total: number }
export interface Envelope<T> { schema: string; schema_version: string; generated_at: string; producer_version: string; payload: T }
export interface Connection { mode: SourceMode; baseUrl: string; environmentId?: string }
export interface Environment { environment_id: string; name: string; kind: 'external' | 'lab' }
export interface ManagedService { service_id: string; display_name: string; read_config: boolean; write_fields: string[]; is_lab: boolean }
export interface CallConfig {
  call_ref: string;
  target: { service_id: string; method: string; route: string };
  timeout_ms: number;
  max_attempts: number;
  backoff_ms: number;
  retry_on: string[];
  write_fields: string[];
}
export type RouteWork = { kind: 'fixed'; delay_ms: number } | { kind: 'lognormal'; p50_ms: number; p99_ms: number };
export interface RouteConfig { route: string; work: RouteWork; write_fields: string[]; boundaries?: { boundary_id: string; label: string }[] }
export interface ConfigSnapshot { schema_version: string; service_id: string; revision: string; calls: CallConfig[]; routes: RouteConfig[] }
export interface ManualChangeIntent {
  environment_id: string;
  service_id: string;
  expected_config_revision: string;
  call_ref?: string;
  route?: string;
  set: Record<string, string | number | string[] | RouteWork>;
}
export interface ApplyResult { operation_id: string; status: JournalEntry['status'] }
export type FaultPoint = { mode: 'fixed'; boundary_id: string } | { mode: 'random'; boundary_ids: string[] };
export interface LabFault {
  service_id: string;
  route: string;
  error_rate: number;
  http_status: number;
  delay_ms: number;
  failure_point: FaultPoint;
}
export interface LabService { service_id: string; display_name: string; revision: string; routes: { route: string; work: RouteWork; write_fields: string[]; boundaries: { boundary_id: string; label: string }[] }[]; calls?: CallConfig[] }
export interface LabOrigin { service_id: string; field: string; source: 'observed' | 'default'; value_ms?: number; duration_ns?: string; attempts?: number; approximation?: string }
export interface Stand {
  stand_id: string;
  status: 'building' | 'ready' | 'stopping' | 'stopped' | 'failed' | 'unavailable';
  scenario_id: string;
  scenario_revision: number;
  fault_revision: number;
  config_revisions: Record<string, string>;
  services: LabService[];
  faults: LabFault[];
  source?: TraceRef | null;
  origins?: LabOrigin[];
}
export interface TraceCollection { status: 'waiting' | 'ready' | 'partial'; expected_count: number; received_count: number; trace_refs?: TraceRef[] }
export interface LabRun {
  run_id: string;
  stand_id: string;
  status: 'running' | 'completed' | 'failed';
  seed: string | number;
  scenario_revision: number;
  fault_revision: number;
  config_revisions: Record<string, string>;
  request_count: number;
  concurrency: number;
  completed_requests: number;
  trace_collection: TraceCollection;
  started_at: string;
  finished_at?: string | null;
}
export interface ComparisonMetrics {
  run_id: string; config_revision: string; collection_status: 'ready' | 'partial';
  received_count: number; expected_count: number; attempts: number; retries: number;
  attempts_per_input: number | null; root_latency_p50_ns: string | null; root_latency_p95_ns: string | null;
  root_errors: number; root_error_ratio: number | null; internal_error_trace_count: number;
  internal_error_trace_ratio: number | null; attempt_count_histogram: Record<string, number>;
  http_status_counts: Record<string, number>;
}
export interface RunComparison {
  algorithm_version: string; stand_id: string; change: { service_id: string; call_ref: string; field: string; before: unknown; after: unknown };
  baseline: ComparisonMetrics; candidate: ComparisonMetrics; attempts_per_input_delta: number | null;
  input_hash: string; warnings: string[];
}
export interface Client {
  listTraces(signal?: AbortSignal): Promise<ListPayload>;
  getTrace(id: string | TraceRef, signal?: AbortSignal): Promise<TraceDetail>;
  getAudit(signal?: AbortSignal): Promise<AuditResult>;
  getCapabilities(signal?: AbortSignal): Promise<Capabilities>;
  getJournal(signal?: AbortSignal): Promise<JournalEntry[]>;
  getJournalEntry(operationId: string, signal?: AbortSignal): Promise<JournalEntry>;
  getJournalEntryForEnvironment(operationId: string, environmentId: string, signal?: AbortSignal): Promise<JournalEntry>;
  createPlan(finding: BudgetFinding, signal?: AbortSignal): Promise<ChangePlan>;
  listEnvironments(signal?: AbortSignal): Promise<Environment[]>;
  listServices(environmentId?: string, signal?: AbortSignal): Promise<ManagedService[]>;
  getConfig(serviceId: string, environmentId?: string, signal?: AbortSignal): Promise<ConfigSnapshot>;
  createManualPlan(intent: ManualChangeIntent, signal?: AbortSignal): Promise<ChangePlan>;
  applyPlan(planId: string, planHash: string, requestId: string, signal?: AbortSignal): Promise<ApplyResult>;
  getActiveStand(signal?: AbortSignal): Promise<Stand | null>;
  createStand(scenarioId: 'checkout', signal?: AbortSignal): Promise<Stand>;
  createStandFromTrace(source: TraceRef, signal?: AbortSignal): Promise<Stand>;
  stopStand(standId: string, signal?: AbortSignal): Promise<Stand>;
  setFault(standId: string, fault: LabFault, expectedFaultRevision: number, requestId: string, signal?: AbortSignal): Promise<Stand>;
  clearFault(standId: string, expectedFaultRevision: number, requestId: string, signal?: AbortSignal): Promise<Stand>;
  startRun(standId: string, request: { request_id: string; scenario_revision: number; expected_fault_revision: number; config_revisions: Record<string, string>; request_count: number; concurrency: number; seed?: number }, signal?: AbortSignal): Promise<LabRun>;
  getRun(runId: string, signal?: AbortSignal): Promise<LabRun>;
  listRuns(signal?: AbortSignal): Promise<LabRun[]>;
  compareRuns(baselineRunId: string, candidateRunId: string, signal?: AbortSignal): Promise<RunComparison>;
}
