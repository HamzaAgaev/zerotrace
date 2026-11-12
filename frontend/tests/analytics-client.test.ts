import test from 'node:test';
import assert from 'node:assert/strict';
import { ApiError, createClient } from '../src/api/client.js';
import type { ProjectionResult, TraceCohortComparison, TraceRef } from '../src/api/types.js';

const ref: TraceRef = { environment_id: 'shop', trace_id: '1'.repeat(32), generation: 2, revision: 7 };
const other: TraceRef = { ...ref, trace_id: '2'.repeat(32) };
const envelope = (payload: unknown) => ({ schema: 'test', schema_version: '1.0.0', payload });
const projection: ProjectionResult = {
  analysis_id: 'server-analysis', algorithm_version: 'interval-blocks-v1.1.0', input_hash: 'pinned-input',
  status: 'available', trace_ref: ref, baseline_ns: '3000000', projected_ns: '1800000',
  gain_ns: '1200000', gain_ratio: 0.4, candidates: [],
  assumptions: ['Timestamps aligned by the user.'], warnings: ['timestamps_assumed_aligned'],
  applicability: { reason_codes: [], scope_root: 'a'.repeat(16), excluded_span_ids: [] },
};
const metric = { count: 2, root_latency_p50_ns: '5000000', root_latency_p95_ns: '6000000',
  root_errors: 0, root_error_ratio: 0, coverage: { root_latency_count: 2 } };
const comparison: TraceCohortComparison = {
  analysis_id: 'server-comparison', algorithm_version: '1.1.0', input_hash: 'group-input',
  status: 'available', comparability: 'exploratory',
  input_refs: { baseline: [ref], candidate: [other] }, baseline: metric,
  candidate: { ...metric, root_latency_p50_ns: '3000000' },
  delta_ns: '-2000000', delta_ratio: -0.4, warnings: ['small_sample_p95'],
};

test('projection sends pinned revision and preserves the complete server result', async () => {
  let url = ''; let init: RequestInit | undefined;
  const client = createClient({ mode: 'live', baseUrl: '' }, async (input, options) => {
    url = String(input); init = options;
    return Response.json(envelope(projection));
  });
  const changes = [{ span_id: 'b'.repeat(16), speedup_percent: 50 }];
  assert.deepEqual(await client.projectTrace(ref, changes), projection);
  assert.equal(url, '/api/v1/projections');
  assert.equal(init?.method, 'POST');
  assert.deepEqual(JSON.parse(String(init?.body)), { trace_ref: ref, changes });
});

test('cohort comparison accepts negative server deltas without recomputing them', async () => {
  let request: unknown;
  const client = createClient({ mode: 'live', baseUrl: '' }, async (input, options) => {
    assert.equal(String(input), '/api/v1/comparisons');
    assert.equal(options?.method, 'POST');
    request = JSON.parse(String(options?.body));
    return Response.json(envelope(comparison));
  });
  assert.deepEqual(await client.compareTraceGroups([ref], [other]), comparison);
  assert.deepEqual(request, { baseline_trace_refs: [ref], candidate_trace_refs: [other] });
});

test('unavailable projection stays unavailable rather than becoming a local estimate', async () => {
  const unavailable = { ...projection, status: 'insufficient_data', projected_ns: null,
    gain_ns: null, gain_ratio: null, applicability: { ...projection.applicability,
      reason_codes: ['missing_parent'] } };
  const client = createClient({ mode: 'live', baseUrl: '' }, async () => Response.json(envelope(unavailable)));
  assert.deepEqual(await client.projectTrace(ref, []), unavailable);
});

test('server cohort refusal preserves status and reason', async () => {
  const client = createClient({ mode: 'live', baseUrl: '' }, async () => Response.json(
    { error: { code: 'cohorts_not_comparable', message: 'Different entry operation' } }, { status: 422 }));
  await assert.rejects(client.compareTraceGroups([ref], [other]),
    (e: unknown) => e instanceof ApiError && e.status === 422 && e.code === 'cohorts_not_comparable');
});

test('invalid server durations are rejected instead of displayed', async () => {
  const client = createClient({ mode: 'live', baseUrl: '' }, async () => Response.json(
    envelope({ ...projection, projected_ns: 'not-a-duration' })));
  await assert.rejects(client.projectTrace(ref, []),
    (e: unknown) => e instanceof ApiError && e.code === 'invalid_payload');
});

test('analytics transport forwards cancellation to the request', async () => {
  const controller = new AbortController();
  const client = createClient({ mode: 'live', baseUrl: '' }, async (_input, options) => {
    const signal = options?.signal;
    assert.ok(signal);
    return new Promise<Response>((_resolve, reject) => {
      signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
    });
  });
  const pending = client.projectTrace(ref, [], controller.signal);
  controller.abort();
  await assert.rejects(pending, (e: unknown) => e instanceof Error && e.name === 'AbortError');
});

test('demo mode does not substitute browser arithmetic for server analytics', async () => {
  let called = false;
  const client = createClient({ mode: 'demo', baseUrl: '' }, async () => {
    called = true; throw new Error('Must not fetch');
  });
  await assert.rejects(client.projectTrace(ref, []));
  await assert.rejects(client.compareTraceGroups([ref], [other]));
  assert.equal(called, false);
});
