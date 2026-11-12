import type { PeriodMetrics, PeriodStats, TraceDetail } from './types.js';
export const periods = [5, 30, 60, 180, 1440] as const;
export const validPeriod = (value: unknown): value is number => typeof value === 'number' && (periods as readonly number[]).includes(value);
type Row = { duration: bigint | null; own: bigint; outcome: string };
function stats(rows: Row[]): PeriodStats {
  const durations = rows.flatMap(r => r.duration === null ? [] : [r.duration]).sort((a,b) => a < b ? -1 : a > b ? 1 : 0);
  const percentile = (p: number) => {
    if (!durations.length) return null;
    const position = (durations.length - 1) * p, index = Math.floor(position);
    const fraction = BigInt(Math.round((position - index) * 100));
    return (durations[index] + (((durations[Math.min(index+1,durations.length-1)]-durations[index])*fraction+50n)/100n)).toString();
  };
  const ok = rows.filter(r=>r.outcome==='ok').length, errors = rows.filter(r=>r.outcome==='error').length;
  return {count:rows.length, errors, unknown:rows.length-ok-errors, success_rate:ok+errors ? ok/(ok+errors) : null,
    median_ns:percentile(.5), p95_ns:percentile(.95), own_ns:rows.reduce((sum,r)=>sum+r.own,0n).toString()};
}
export function calculatePeriodMetrics(details: TraceDetail[], minutes: number, now = BigInt(Date.now())*1_000_000n): PeriodMetrics {
  const cutoff = now-BigInt(minutes)*60_000_000_000n;
  const within = (start: bigint) => start > 0n && start >= cutoff && start <= now;
  const requests: Row[] = [], services = new Map<string,Row[]>(), endpoints = new Map<string,{service:string;endpoint:string;rows:Row[]}>();
  for (const detail of details) {
    const origin = BigInt(detail.summary.start_ns);
    const root = detail.spans.find(s=>s.parent_span_id===null);
    if (within(origin + BigInt(root?.start_offset_ns ?? 0))) requests.push({duration:root ? BigInt(root.duration_ns) : null,own:0n,outcome:root?.outcome ?? 'unknown'});
    for (const span of detail.spans) {
      const start = BigInt(span.start_offset_ns), end = start+BigInt(span.duration_ns);
      if (span.kind !== 'server' || !within(origin+start)) continue;
      const children = detail.spans.filter(s=>s.parent_span_id===span.span_id).map(s=>[BigInt(s.start_offset_ns), BigInt(s.start_offset_ns)+BigInt(s.duration_ns)]).sort((a,b)=>a[0]<b[0]?-1:1);
      let occupied=0n, cursor=start;
      for (const [left,right] of children) { const a=left>cursor?left:cursor, b=right<end?right:end; if(b>a) occupied+=b-a; if(b>cursor) cursor=b; }
      const row={duration:BigInt(span.duration_ns),own:BigInt(span.duration_ns)-occupied,outcome:span.outcome};
      const group=services.get(span.service) ?? []; group.push(row); services.set(span.service,group);
      const key=JSON.stringify([span.service,span.name]), endpoint=endpoints.get(key) ?? {service:span.service,endpoint:span.name,rows:[]};
      endpoint.rows.push(row); endpoints.set(key,endpoint);
    }
  }
  return {period_minutes:minutes,start_ns:cutoff.toString(),end_ns:now.toString(),summary:stats(requests),
    services:[...services].sort(([a],[b])=>a.localeCompare(b)).map(([service,rows])=>({service,...stats(rows)})),
    endpoints:[...endpoints.values()].map(({service,endpoint,rows})=>({service,endpoint,...stats(rows)}))};
}
