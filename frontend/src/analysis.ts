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
export function downloadJson(name: string, payload: unknown, metadata: { schema?: string; producerVersion?: string } = {}) {
  const content = {
    schema: metadata.schema ?? 'zerotrace.exploration',
    schema_version: '1.0.0',
    generated_at: new Date().toISOString(),
    ...(metadata.producerVersion ? { producer_version: metadata.producerVersion } : {}),
    payload,
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(content, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href=url; link.download=name; link.click(); setTimeout(() => URL.revokeObjectURL(url),1000);
}
