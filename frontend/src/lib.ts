import type { Finding, Outcome, Route, Span, TraceDetail, TraceRef, TraceSummary } from './api/types.js';
export const outcomeLabels = { ok: 'Успешно', error: 'С ошибкой', unknown: 'Неизвестно' } satisfies Record<Outcome, string>;
export const serviceColors: Record<string, string> = { gateway: '#96a2ff', orders: '#65c9ba', payment: '#dbad6b', inventory: '#65c9ba', pricing: '#dbad6b', 'catalog-api': '#96a2ff' };
const traceColorPalette = [
  '#e795a8', '#75b8ff', '#d5adf3', '#a9c879', '#ef9d74', '#74cbdc',
  '#d3c976', '#b3a4e9', '#86d79b', '#e698d3', '#b0a998', '#e6ba9d',
];

export function traceServiceColors(services: readonly string[]): Map<string, string> {
  const colors = new Map<string, string>();
  const used = new Set<string>();
  const ordered = [...new Set(services)].sort();
  for (const service of ordered) {
    const preferred = Object.hasOwn(serviceColors, service) ? serviceColors[service] : undefined;
    if (preferred && !used.has(preferred)) {
      colors.set(service, preferred);
      used.add(preferred);
    }
  }
  let next = 0;
  for (const service of ordered) {
    if (colors.has(service)) continue;
    const color = traceColorPalette[next] ?? `hsl(${(next * 137.507764) % 360} 64% 67%)`;
    colors.set(service, color);
    next++;
  }
  return colors;
}
export function ms(value: string | null | undefined): number | null { if (value == null || !/^\d+$/.test(value)) return null; return Number(BigInt(value)) / 1e6; }
export function duration(value: string | null | undefined): string { const n = ms(value); if (n === null) return 'Неизвестно'; return n >= 1000 ? `${(n / 1000).toLocaleString('ru-RU', { maximumFractionDigits: 2 })}\u00A0с` : `${n.toLocaleString('ru-RU', { maximumFractionDigits: 1 })}\u00A0мс`; }
export function time(value: string): string { try { const date = new Date(Number(BigInt(value) / 1000000n)); return date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' }); } catch { return 'Неизвестно'; } }
export function splitHttpOperation(operation: string, allowMethodOnly = false) {
  const match = /^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS|TRACE|CONNECT)(?: (.+))?$/i.exec(operation);
  return match && (match[2] || allowMethodOnly)
    ? { method: match[1], endpoint: match[2] ?? '' }
    : null;
}
export function traceRequest(trace: TraceSummary): { method: string | null; endpoint: string } {
  if (trace.http_method !== undefined) return { method: trace.http_method, endpoint: trace.endpoint ?? trace.operation };
  const legacy = splitHttpOperation(trace.operation);
  return legacy ? { method: legacy.method.toUpperCase(), endpoint: legacy.endpoint }
    : { method: null, endpoint: trace.operation };
}
export function traceHref(ref: TraceRef | string): string {
  if (typeof ref === 'string') return `#/traces/${encodeURIComponent(ref)}`;
  const params = new URLSearchParams({
    environment_id: ref.environment_id, generation: String(ref.generation), revision: String(ref.revision),
  });
  return `#/traces/${ref.trace_id}?${params}`;
}
export function findingsForTrace(trace: TraceDetail, findings: Finding[]): Finding[] {
  if (trace.finding_occurrence_ids !== undefined) {
    const ids = new Set(trace.finding_occurrence_ids);
    return findings.filter(f => ids.has(f.occurrence_id));
  }
  const selected = traceHref(trace.trace_ref);
  return findings.filter(f => f.exemplars.some(ref => traceHref(ref) === selected));
}
export function readRoute(hash: string): Route {
  const [path, query] = hash.replace(/^#\/?/, '').split('?');
  const [page, traceId] = path.split('/');
  if (page === 'services') return { page: new URLSearchParams(query).get('tab') === 'statistics' ? 'timings' : 'graph' };
  if (page === 'policies') return { page: 'graph' };
  if (page === 'compare') return { page: 'traces' };
  if (['audit', 'journal', 'sources', 'graph', 'timings', 'policies', 'lab'].includes(page)) {
    return { page: page as 'audit' | 'journal' | 'sources' | 'graph' | 'timings' | 'policies' | 'lab' };
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
export function compactTimelineSpans(spans: Span[], pairs?: Record<string, string>): Span[] {
  const byId = new Map(spans.map(span => [span.span_id, span]));
  const hidden = new Set<string>();
  const parents = new Map<string, string | null>();
  const resolved = { ...pairs };
  for (const client of spans) {
    if (client.kind !== 'client') continue;
    const children = spans.filter(span => span.parent_span_id === client.span_id);
    if (children.length === 1 && children[0].kind === 'server' && children[0].service !== client.service) {
      resolved[client.span_id] = children[0].span_id;
    }
  }
  for (const [clientId, serverId] of Object.entries(resolved)) {
    const client = byId.get(clientId);
    const server = byId.get(serverId);
    if (client && server?.parent_span_id === clientId) {
      hidden.add(clientId);
      parents.set(serverId, client.parent_span_id);
    }
  }
  return spans.filter(span => !hidden.has(span.span_id)).map(span =>
    parents.has(span.span_id) ? { ...span, parent_span_id: parents.get(span.span_id) ?? null } : span,
  );
}

export function timelineRows(spans: Span[]): { span: Span; depth: number }[] {
  const map = new Map(spans.map(s => [s.span_id, s])); const visited = new Set<string>(); const output: { span: Span; depth: number }[] = [];
  const walk = (span: Span, depth: number) => { if (visited.has(span.span_id)) return; visited.add(span.span_id); output.push({ span, depth: Math.min(depth, 20) }); spans.filter(s => s.parent_span_id === span.span_id).sort((a,b) => Number(BigInt(a.start_offset_ns) - BigInt(b.start_offset_ns))).forEach(s => walk(s, depth + 1)); };
  spans.filter(s => !s.parent_span_id || !map.has(s.parent_span_id)).forEach(s => walk(s, 0)); spans.forEach(s => walk(s, 0)); return output;
}

export function serviceSurfaceForHash(hash: string, saved: 'graph' | 'table'): 'graph' | 'table' {
  const [path, query] = hash.replace(/^#\/?/, '').split('?');
  const params = new URLSearchParams(query);
  if (path === 'graph' || path === 'policies' || params.has('finding')) return 'graph';
  if (path === 'timings') return 'table';
  if (params.get('tab') === 'statistics') return 'table';
  if (params.get('tab') === 'graph') return 'graph';
  return saved === 'table' ? 'table' : 'graph';
}
