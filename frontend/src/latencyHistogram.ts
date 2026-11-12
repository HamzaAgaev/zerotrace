import type { LatencyBucket } from './api/types.js';

/** Equal-width bins, including the largest observation in the last bin. */
export function latencyHistogram(values: number[]): LatencyBucket[] {
  const valid = values.filter(v => Number.isFinite(v) && v >= 0);
  const maximum = valid.reduce((max, v) => Math.max(max, v), 0);
  const raw = Math.max(1, maximum / 10);
  const power = 10 ** Math.max(0, Math.floor(Math.log10(raw)) - 1);
  const step = Math.ceil(raw / power) * power;
  const count = Math.max(1, Math.ceil(maximum / step));
  const bins = Array.from({length: count}, (_, i) => ({label: `${i*step}–${(i+1)*step} мс`, lower_ms:i*step, upper_ms:(i+1)*step, count:0}));
  for (const value of valid) bins[Math.min(count-1, Math.floor(value/step))].count++;
  return bins;
}
