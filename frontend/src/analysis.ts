import type { Span, TraceDetail, TraceSummary } from './api/types.js';
export const numericMs = (ns: string) => Number(BigInt(ns)) / 1e6;
export function percentile(values: number[], p: number): number | null {
  if (!values.length) return null;
  const sorted = [...values].sort((a,b) => a-b);
  const rank = Math.min(1, Math.max(0, p)) * (sorted.length - 1);
  const lower = Math.floor(rank), upper = Math.ceil(rank);
  return sorted[lower] + (sorted[upper] - sorted[lower]) * (rank - lower);
}
export function unionLength(intervals: [number, number][]): number {
  const sorted = intervals.filter(([a,b]) => b > a).sort((a,b) => a[0]-b[0]);
  let end = -Infinity, total = 0;
  for (const [a,b] of sorted) { total += Math.max(0, b-Math.max(a,end)); end = Math.max(end,b); }
  return total;
}
export interface ServiceStats { name: string; count: number; errors: number; unknown: number; median: number | null; p95: number | null; own: number; traceIds: string[] }
interface Endpoint { id: string; name: string; external: boolean }
export interface EdgeStats { id: string; caller: string; callee: string; from: Endpoint; to: Endpoint; count: number; errors: number; unknown: number; median: number | null; p95: number | null; traceIds: string[] }
const endpoint = (name: string, external = false): Endpoint => ({
  id: JSON.stringify([external ? 'external' : 'service', name]), name, external,
});
export function summarize(details: TraceDetail[]): { services: ServiceStats[]; edges: EdgeStats[]; nodes: Endpoint[] } {
  const services = new Map<string, { spans: Span[]; own: number; ids: Set<string> }>();
  const edges = new Map<string, { from: Endpoint; to: Endpoint; spans: Span[]; ids: Set<string> }>();
  const addEdge = (from: Endpoint, to: Endpoint, span: Span, traceId: string) => {
    const id = JSON.stringify([from.id, to.id]);
    const group = edges.get(id) ?? { from, to, spans: [], ids: new Set<string>() };
    group.spans.push(span);
    group.ids.add(traceId);
    edges.set(id, group);
  };
  for (const trace of details) {
    const map = new Map(trace.spans.map(s => [s.span_id,s]));
    const serverParents = new Set(trace.spans.filter(s => s.kind === 'server').map(s => s.parent_span_id));
    const traceId = trace.summary.trace_id;
    for (const span of trace.spans) {
      if (span.kind === 'server') {
        const group = services.get(span.service) ?? { spans: [], own: 0, ids: new Set<string>() };
        group.spans.push(span); group.ids.add(trace.summary.trace_id);
        const start = numericMs(span.start_offset_ns), end = start + numericMs(span.duration_ns);
        const occupied = unionLength(trace.spans.filter(s => s.parent_span_id === span.span_id).map(s => [Math.max(start,numericMs(s.start_offset_ns)), Math.min(end,numericMs(s.start_offset_ns)+numericMs(s.duration_ns))]));
        group.own += Math.max(0,end-start-occupied); services.set(span.service,group);
      }
      const parent = span.parent_span_id ? map.get(span.parent_span_id) : null;
      if (span.kind === 'server' && parent && parent.service !== span.service) {
        addEdge(endpoint(parent.service), endpoint(span.service), parent.kind === 'client' ? parent : span, traceId);
      } else if (span.kind === 'client' && span.target && !serverParents.has(span.span_id)) {
        addEdge(endpoint(span.service), endpoint(span.target, true), span, traceId);
      }
    }
  }
  const stats = (spans: Span[]) => ({ count: spans.length, errors: spans.filter(s => s.outcome === 'error').length, unknown: spans.filter(s => s.outcome === 'unknown').length, median: percentile(spans.map(s => numericMs(s.duration_ns)), .5), p95: percentile(spans.map(s => numericMs(s.duration_ns)), .95) });
  const serviceStats = [...services].map(([name,g]) => ({ name,...stats(g.spans), own:g.own,traceIds:[...g.ids] })).sort((a,b) => b.count-a.count || a.name.localeCompare(b.name));
  const edgeStats = [...edges].map(([id,g]) => ({
    id, from:g.from, to:g.to, caller:g.from.name, callee:g.to.name, ...stats(g.spans), traceIds:[...g.ids],
  }));
  const nodes = new Map(serviceStats.map(s => {
    const node = endpoint(s.name);
    return [node.id, node] as const;
  }));
  for (const { from, to } of edgeStats) {
    nodes.set(from.id, from);
    nodes.set(to.id, to);
  }
  return { services: serviceStats, edges: edgeStats, nodes: [...nodes.values()] };
}
export interface BudgetDraft { timeout: number; attempts: number; backoff: number; budget: number }
export function checkBudget(draft: BudgetDraft) {
  const { timeout, attempts, backoff, budget } = draft;
  const valid = [timeout,attempts,backoff,budget].every(Number.isFinite) && timeout > 0 && budget > 0 && Number.isInteger(attempts) && attempts >= 1 && attempts <= 10 && backoff >= 0 && [timeout,backoff,budget].every(v => v <= 3_600_000);
  const required = valid ? timeout*attempts+backoff*(attempts-1) : null;
  return { valid, required, reserve: required === null ? null : budget-required };
}
export function cohortStats(rows: TraceSummary[]) {
  const values = rows.map(t => numericMs(t.duration_ns));
  return { count: rows.length, median:percentile(values,.5), p95:percentile(values,.95), errors:rows.filter(t => t.outcome === 'error').length, unknown:rows.filter(t => t.outcome === 'unknown').length };
}
export function compareCohorts(a: TraceSummary[], b: TraceSummary[]): string | null {
  if (!a.length || !b.length) return 'Выберите хотя бы одну трассу в каждой группе.';
  const ids = new Set(a.map(t => t.trace_id));
  if (b.some(t => ids.has(t.trace_id))) return 'Группы не должны пересекаться.';
  if (new Set([...a,...b].map(t => JSON.stringify([t.service,t.operation]))).size !== 1) return 'Сравнивать можно только одну входную операцию одного сервиса.';
  return null;
}
/** Browser exploration only. Mirrors the interval-block assumptions of the Go prototype. */
export function projectIntervals(trace: TraceDetail, reductions: Record<string, number>) {
  const spans = trace.spans, map = new Map(spans.map(s => [s.span_id,s]));
  if (spans.length > 2000 || !spans.length || map.size !== spans.length) throw new Error('Модель требует от 1 до 2000 уникальных спанов.');
  if (trace.quality_issues.length) throw new Error('Для этой трассы есть замечания к качеству данных. Выберите полную трассу без ошибок границ.');
  if (Object.entries(reductions).some(([id,p]) => !map.has(id) || !Number.isFinite(p) || p < 0 || p > 100)) throw new Error('Некорректная ветка или процент ускорения.');
  const roots = spans.filter(s => !s.parent_span_id);
  if (roots.length !== 1 || spans.some(s => s.parent_span_id && !map.has(s.parent_span_id))) throw new Error('Требуется дерево с одним корнем и известными родителями.');
  const children = new Map<string,Span[]>();
  for (const s of spans) if (s.parent_span_id) children.set(s.parent_span_id,[...(children.get(s.parent_span_id) ?? []),s]);
  const visited = new Set<string>();
  const walk = (s: Span, depth: number): number => {
    if (depth > 100 || visited.has(s.span_id)) throw new Error('Цикл или слишком глубокое дерево.');
    visited.add(s.span_id);
    const start = numericMs(s.start_offset_ns), length = numericMs(s.duration_ns);
    const groups: { start:number; end:number; members:{span:Span; projected:number}[] }[] = [];
    for (const child of [...(children.get(s.span_id) ?? [])].sort((a,b) => numericMs(a.start_offset_ns)-numericMs(b.start_offset_ns))) {
      const cs = numericMs(child.start_offset_ns), ce = cs+numericMs(child.duration_ns);
      if (cs < start || ce > start+length) throw new Error('Дочерний спан выходит за границы родителя. Такая трасса не подходит для этой модели.');
      const member = { span:child,projected:walk(child,depth+1) };
      const last = groups.at(-1);
      if (!last || cs >= last.end) groups.push({ start:cs,end:ce,members:[member] });
      else { last.end = Math.max(last.end,ce); last.members.push(member); }
    }
    if (reductions[s.span_id] !== undefined) return length*(1-reductions[s.span_id]/100);
    return Math.max(0, length-groups.reduce((sum,g) => sum+g.end-g.start,0)+groups.reduce((sum,g) => sum+Math.min(g.end-g.start,Math.max(...g.members.map(m => numericMs(m.span.start_offset_ns)-g.start+m.projected))),0));
  };
  // Overlapping selections are ambiguous; require disjoint branches.
  for (const id of Object.keys(reductions)) {
    let parent = map.get(id)?.parent_span_id; const chain = new Set<string>();
    while (parent) { if (chain.has(parent)) throw new Error('Цикл в дереве.'); chain.add(parent); if (reductions[parent] !== undefined) throw new Error('Выберите непересекающиеся ветки: родитель и потомок не могут ускоряться отдельно.'); parent = map.get(parent)?.parent_span_id; }
  }
  const projected = walk(roots[0],0);
  if (visited.size !== spans.length) throw new Error('В дереве есть недостижимые спаны.');
  const baseline = numericMs(roots[0].duration_ns);
  return { baseline, projected, gain:Math.max(0,baseline-projected) };
}
export function downloadJson(name: string, payload: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify({ schema:'zerotrace.exploration',schema_version:'1.0.0',generated_at:new Date().toISOString(),payload },null,2)],{type:'application/json'}));
  const link = document.createElement('a'); link.href=url; link.download=name; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
