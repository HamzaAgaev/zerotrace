import assert from 'node:assert/strict';
import test from 'node:test';
import { ApiError, createClient, createOrRecoverStand } from '../src/api/client.js';
import type { Client, Stand } from '../src/api/types.js';
import { labSourceTrace } from '../src/api/fixtures.js';
import { comparisonIssue, initialLab, labReducer, retryBudget } from '../src/api/labFixture.js';

const source = labSourceTrace.trace_ref;
const building = labReducer(initialLab, { type: 'build', source, id: 'stand' });
const ready = labReducer(building, { type: 'ready', id: 'stand' });
const faulted = labReducer(ready, { type: 'fault', enabled: true });
const versions = { scenarioRevision: 1, faultRevision: 1, configRevision: '12' };

test('build recovers the matching revision after losing the POST response', async () => {
  const active: Stand = {
    stand_id: 'stand', scenario_id: 'generated', status: 'building', source,
    scenario_revision: 1, fault_revision: 0, config_revisions: {}, services: [], faults: [],
  };
  const client = createClient({ mode: 'demo', baseUrl: '' });
  let calls = 0;
  const lostResponse: Client = {
    ...client,
    async createStandFromTrace() {
      throw new ApiError('Timeout', 0, 'timeout');
    },
    async getActiveStand() {
      return { ...active, status: ++calls === 1 ? 'building' : 'ready' };
    },
  };
  assert.equal((await createOrRecoverStand(lostResponse, source)).status, 'ready');
  assert.equal(calls, 2);
  await assert.rejects(createOrRecoverStand(lostResponse, { ...source, revision: source.revision + 1 }),
    /Активен другой стенд/);
});

test('one stand holds its slot during build and stop, tied to the full trace reference', async () => {
  const changed = { ...source, revision: source.revision + 1 };
  assert.equal(labReducer(initialLab, { type: 'build', source: changed, id: 'new' }).stand, null);
  await assert.rejects(createClient({ mode: 'demo', baseUrl: '' }).getTrace(changed), { code: 'trace_ref_not_found' });
  assert.equal(labReducer(building, { type: 'fault', enabled: true }).stand?.fault, null);
  assert.equal(labReducer(building, { type: 'build', source: changed, id: 'new' }).stand?.id, 'stand');
  const stopping = labReducer(ready, { type: 'stop' });
  assert.equal(stopping.stand?.status, 'stopping');
  assert.equal(labReducer(stopping, { type: 'build', source, id: 'new' }).stand?.id, 'stand');
  assert.equal(labReducer(stopping, { type: 'stopped', id: 'stand' }).stand, null);
});

test('revision conflicts keep the config and run list unchanged', () => {
  for (const stale of [{ scenarioRevision: 0 }, { faultRevision: 0 }, { configRevision: '11' }]) {
    const result = labReducer(faulted, { type: 'run', id: 'stale', versions: { ...versions, ...stale } });
    assert.deepEqual(result.runs, []);
    assert.match(result.message, /Версии стенда изменились/);
  }
  const result = labReducer(faulted, { type: 'apply', standId: 'stand', revision: '11' });
  assert.equal(result.stand?.configRevision, '12');
  assert.match(result.message, /новый предпросмотр/);
});

test('P11 experiment compares three and two attempts after telemetry is fixed', () => {
  let lab = labReducer(faulted, { type: 'run', id: 'baseline', versions });
  assert.equal(labReducer(lab, { type: 'run', id: 'extra', versions }).runs.length, 1);
  assert.equal(labReducer(lab, { type: 'fault', enabled: false }).stand?.faultRevision, 1);
  assert.equal(labReducer(lab, { type: 'apply', standId: 'stand', revision: '12' }).stand?.configRevision, '12');
  lab = labReducer(lab, { type: 'loadComplete', id: 'baseline' });
  assert.equal(labReducer(lab, { type: 'stop' }).stand?.status, 'ready');
  lab = labReducer(lab, { type: 'tracesReady', id: 'baseline' });
  lab = labReducer(lab, { type: 'apply', standId: 'stand', revision: '12' });
  assert.equal(lab.stand?.faultRevision, 1);
  lab = labReducer(lab, { type: 'run', id: 'candidate', versions: { ...versions, configRevision: '13' } });
  lab = labReducer(lab, { type: 'loadComplete', id: 'candidate' });
  assert.match(comparisonIssue(lab.runs[0], lab.runs[1]), /сбора трейсов/);
  lab = labReducer(lab, { type: 'tracesReady', id: 'candidate' });
  const [before, after] = lab.runs;
  assert.equal(comparisonIssue(before, after), '');
  assert.equal(comparisonIssue(before, { ...after, collection: 'partial', samples: after.samples.slice(0, 2) }), '');
  assert.match(comparisonIssue(before, { ...after, collection: 'partial', samples: [] }), /непустые/);
  assert.match(comparisonIssue(before, { ...after, status: 'failed' }), /ошибкой/);
  assert.match(comparisonIssue(before, { ...after, faultRevision: 2 }), /одинаковые/);
  for (const [run, attempts] of [[before, 3], [after, 2]] as const) {
    assert.equal(run.samples.length, 30);
    assert.ok(run.samples.every(sample =>
      sample.status === 503 && sample.attemptStatuses.length === attempts &&
      sample.attemptStatuses.every(status => status === 503)));
  }
  assert.equal(retryBudget(3), 850);
  assert.equal(retryBudget(2), 550);
  lab = labReducer(labReducer(lab, { type: 'stop' }), { type: 'stopped', id: 'stand' });
  assert.equal(lab.stand, null);
  assert.equal(lab.runs.length, 2);
});
