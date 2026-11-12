import test from 'node:test';
import assert from 'node:assert/strict';
import { createDemoLab } from '../src/api/demoLab.js';
import { exampleScenario } from '../src/labScenario.js';

test('shared laboratory client persists stand, faults, runs and applied configuration', async () => {
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  let client = createDemoLab(storage);
  let stand = await client.createStand({scenario_hash:(await client.validateScenario({scenario:exampleScenario})).scenario_hash});
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
  const stand = await client.createStand({scenario_hash:(await client.validateScenario({scenario:exampleScenario})).scenario_hash});
  assert.equal(stand.scenario_id, 'example');
  assert.equal(stand.scenario_origin?.kind, 'uploaded');
  assert.equal(stand.status, 'ready');
});

test('service editor works without a stand and isolates connected demo settings from sandbox', async () => {
  const data = new Map<string, string>();
  const storage = { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => { data.set(key, value); } };
  let client = createDemoLab(storage);
  const config = await client.getConfig('orders', 'demo-shop');
  assert.ok(config.calls.length);
  const plan = await client.createManualPlan({ environment_id: 'demo-shop', service_id: 'orders', expected_config_revision: config.revision, call_ref: config.calls[0].call_ref, set: { max_attempts: 2 } });
  await client.applyPlan(plan.plan_id, plan.plan_hash, 'external-apply');
  assert.equal(await client.getActiveStand(), null);
  client = createDemoLab(storage);
  assert.equal((await client.getConfig('orders', 'demo-shop')).calls[0].max_attempts, 2);
  const stand = await client.createStand({scenario_hash:(await client.validateScenario({scenario:exampleScenario})).scenario_hash});
  assert.equal((await client.getConfig('api', 'sandbox')).calls[0].max_attempts, 2);
  await client.stopStand(stand.stand_id);
  assert.equal((await client.getConfig('orders', 'demo-shop')).calls[0].max_attempts, 2);
  assert.equal((await client.getJournal()).length, 1);
});

test('stand topology is available before tests and faults on different endpoints coexist', async()=>{
  const client=createDemoLab();
  let stand=await client.createStand({scenario_hash:(await client.validateScenario({scenario:exampleScenario})).scenario_hash});
  assert.ok(stand.services.some(s=>s.calls?.length));
  const targets=stand.services.flatMap(s=>s.routes.map(r=>({service_id:s.service_id,route:r.route})));
  for(const target of targets.slice(0,2)) stand=await client.setFault(stand.stand_id,{...target,error_rate:.1,http_status:503,delay_ms:0,failure_point:{mode:'fixed',boundary_id:'before-first'}},stand.fault_revision);
  const restored=(await client.getActiveStand())!;
  assert.equal(restored.faults.length,2);
  assert.ok(restored.services.some(s=>s.calls?.length));
  assert.equal((await client.listRuns()).length,0);
});
