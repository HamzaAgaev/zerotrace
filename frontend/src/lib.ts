import type { Route, Span, TraceRef, TraceSummary } from './api/types.js';
export const serviceColors: Record<string, string> = { gateway: '#96a2ff', orders: '#65c9ba', payment: '#dbad6b', inventory: '#65c9ba', pricing: '#dbad6b', 'catalog-api': '#96a2ff' };
export function ms(value: string | null | undefined): number | null { if (value == null || !/^\d+$/.test(value)) return null; return Number(BigInt(value)) / 1e6; }
export function duration(value: string | null | undefined): string { const n = ms(value); if (n === null && typeof value === 'string' && /^-\d+$/.test(value)) return '−' + duration(value.slice(1)); if (n === null) return 'Неизвестно'; return n >= 1000 ? `${(n / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })} с` : `${n.toLocaleString('ru-RU', { maximumFractionDigits: 1 })} мс`; }
export function time(value: string): string { try { const date = new Date(Number(BigInt(value) / 1000000n)); return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return 'Неизвестно'; } }
export function shortId(id: string): string { return `${id.slice(0, 8)}…${id.slice(-8)}`; }
export function traceHref(ref: TraceRef | string): string {
  if (typeof ref === 'string') return `#/traces/${encodeURIComponent(ref)}`;
  const params = new URLSearchParams({
    environment_id: ref.environment_id, generation: String(ref.generation), revision: String(ref.revision),
  });
  return `#/traces/${ref.trace_id}?${params}`;
}
export function readRoute(hash: string): Route {
  const [path, query] = hash.replace(/^#\/?/, '').split('?');
  const [page, traceId] = path.split('/');
  if (['audit', 'journal', 'sources', 'graph', 'timings', 'policies', 'compare', 'lab'].includes(page)) {
    return { page: page as 'audit' | 'journal' | 'sources' | 'graph' | 'timings' | 'policies' | 'compare' | 'lab' };
  }
  if (!traceId) return { page: 'traces' };
  const params = new URLSearchParams(query);
  const environment_id = params.get('environment_id');
  const generation = params.get('generation');
  const revision = params.get('revision');
  if (environment_id && generation && revision && /^\d+$/.test(generation) && /^\d+$/.test(revision) &&
      Number.isSafeInteger(Number(generation)) && Number.isSafeInteger(Number(revision))) {
    return { page: 'traces', traceId, traceRef: { environment_id, trace_id: traceId, generation: Number(generation), revision: Number(revision) } };
  }
  return { page: 'traces', traceId };
}
export function filterTraces(traces: TraceSummary[], query: string, service: string, status: string, sort: string): TraceSummary[] { const q = query.trim().toLowerCase(); return traces.filter(t => `${t.operation} ${t.service} ${t.trace_id}`.toLowerCase().includes(q) && (!service || service === t.service) && (!status || t.outcome === status)).sort((a, b) => { const av = BigInt(sort === 'slow' ? a.duration_ns : a.start_ns), bv = BigInt(sort === 'slow' ? b.duration_ns : b.start_ns); return av === bv ? a.trace_id.localeCompare(b.trace_id) : av > bv ? -1 : 1; }); }
export function timelineRows(spans: Span[]): { span: Span; depth: number }[] {
  const map = new Map(spans.map(s => [s.span_id, s])); const visited = new Set<string>(); const output: { span: Span; depth: number }[] = [];
  const walk = (span: Span, depth: number) => { if (visited.has(span.span_id)) return; visited.add(span.span_id); output.push({ span, depth: Math.min(depth, 20) }); spans.filter(s => s.parent_span_id === span.span_id).sort((a,b) => Number(BigInt(a.start_offset_ns) - BigInt(b.start_offset_ns))).forEach(s => walk(s, depth + 1)); };
  spans.filter(s => !s.parent_span_id || !map.has(s.parent_span_id)).forEach(s => walk(s, 0)); spans.forEach(s => walk(s, 0)); return output;
}
