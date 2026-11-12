import { latencyHistogram } from './latencyHistogram.js';
import type { RunRequestResult } from './api/types.js';
import { percentile } from './analysis.js';

export function runStatistics(results: RunRequestResult[]) {
  let success = 0, errors = 0, unknown = 0;
  const responses = new Map<string, number>();
  const durations: number[] = [];
  for (const result of results) {
    const status = result.http_status;
    const failed = !!result.error || (status !== null && status >= 400);
    const known = failed || (status !== null && status >= 200 && status < 400);
    if (failed) errors++; else if (known) success++; else unknown++;
    const label = result.error === 'timeout' ? 'Время ожидания истекло'
      : result.error === 'connect' ? 'Ошибка соединения'
      : result.error ? 'Ошибка запроса' : status !== null ? `HTTP ${status}` : 'Результат неизвестен';
    responses.set(label, (responses.get(label) ?? 0) + 1);
    const ms = Number(result.duration_ns) / 1e6;
    if (Number.isFinite(ms) && ms >= 0) durations.push(ms);
  }
  return { success, errors, unknown, count: results.length, median: percentile(durations, .5), p95: percentile(durations, .95),
    responses: [...responses].sort((a,b)=>b[1]-a[1]),
    durations: latencyHistogram(durations),
  };
}
