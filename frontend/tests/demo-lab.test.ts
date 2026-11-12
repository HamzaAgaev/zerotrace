import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoLab } from '../src/api/demoLab.js';
import { labSourceTrace } from '../src/api/fixtures.js';

test('shared laboratory client persists stand, faults, runs and applied configuration', async () => {
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  let client = createDemoLab(storage);
  let stand = await client.createStandFromTrace(labSourceTrace.trace_ref);
  const service = stand.services.find(s => s.routes.length)!;
  stand = await client.setFault(stand.stand_id, { service_id: service.service_id, route: service.routes[0].route, error_rate: .5, http_status: 503, delay_ms: 0, failure_point: { mode: 'fixed', boundary_id: 'before-first' } }, 0);
  await assert.rejects(client.clearFault(stand.stand_id, 0), /Настройки изменились/);
  const run = (request_id: string) => client.startRun(stand.stand_id, { request_id, scenario_revision: stand.scenario_revision, expected_fault_revision: stand.fault_revision, config_revisions: stand.config_revisions, request_count: 3, concurrency: 1 });
  const before = await run('before');
  assert.equal(before.request_count, 3);
  assert.equal((await run('before')).run_id, before.run_id);
  const config = await client.getConfig(service.service_id);
  const plan = await client.createManualPlan({ environment_id: 'sandbox', service_id: service.service_id, expected_config_revision: config.revision, route: config.routes[0].route, set: { work: { kind: 'fixed', delay_ms: 7 } } });
  await client.applyPlan(plan.plan_id, plan.plan_hash, 'apply');
  assert.equal((await client.getJournalEntry('apply')).status, 'applied');
  client = createDemoLab(storage);
  stand = (await client.getActiveStand())!;
  assert.equal(stand.faults.length, 1);
  assert.equal((await client.listRuns()).length, 1);
  assert.equal(stand.config_revisions[service.service_id], '2');
  await run('after');
  const comparison = await client.compareRuns('before', 'after');
  assert.equal(comparison.change.field, 'work');
  assert.equal(comparison.candidate.expected_count, 3);
  await client.stopStand(stand.stand_id);
  assert.equal(await createDemoLab(storage).getActiveStand(), null);
  assert.equal((await client.listRuns()).length, 2);
});

test('default demo stand follows shared creation contract', async () => {
  const client = createDemoLab();
  const stand = await client.createStand();
  assert.equal(stand.scenario_id, 'checkout');
  assert.equal(stand.source, null);
  assert.equal(stand.status, 'ready');
});
