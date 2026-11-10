import { readFile } from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
import { findingsForTrace, filterTraces, ms, duration, readRoute, timelineRows, traceHref } from '../src/lib.js';
import { demoDetail, demoFinding, demoTraces, demoAudit } from '../src/api/fixtures.js';
import { ApiError, createClient, normalizeBaseUrl, unwrap, validatePayload } from '../src/api/client.js';
const envelope = (payload: unknown) => ({ schema: 'test', schema_version: '1.0.0', producer_version: 'test', generated_at: '2026-11-10T00:00:00Z', payload });
test('sorting does not mutate input and query/filter are combined', () => {
 const original = demoTraces.map(t => t.trace_id); const result = filterTraces(demoTraces, 'CHECKOUT', 'gateway', 'error', 'slow');
 assert.equal(result.length,5); assert.equal(result[0].trace_id, demoTraces[4].trace_id); assert.deepEqual(demoTraces.map(t => t.trace_id), original);
});
test('unknown is a separate outcome, not success/zero HTTP', () => { const result = filterTraces(demoTraces, '', '', 'unknown', 'recent'); assert.equal(result.length, 1); assert.equal(result[0].http_status,null); });
test('nanoseconds are parsed from decimal strings and unknown is not zero', () => { assert.equal(ms('1000001'),1.000001); assert.equal(ms(null),null); assert.equal(duration(null),'Неизвестно'); assert.equal(ms('invalid'),null); assert.equal(ms('0'),0); });
test('deep links and unknown routes are deterministic', () => { assert.deepEqual(readRoute('#/traces/abc'),{ page:'traces',traceId:'abc' }); assert.deepEqual(readRoute('#/audit'),{page:'audit'}); assert.deepEqual(readRoute('#/other'),{page:'traces'}); });
test('timeline renders children once and safely terminates disconnected cycles', () => { const graph = demoDetail(demoTraces[0].trace_id); const rows = timelineRows(graph.spans); assert.equal(rows.length,10); assert.equal(new Set(rows.map(r => r.span.span_id)).size,10); assert.equal(rows[2].depth,2); const cycle = graph.spans.slice(0,2).map((s,i,all) => ({...s,parent_span_id:all[1-i].span_id})); assert.equal(timelineRows(cycle).length,2); });
test('demo API has no network side effects and plans cannot apply', async () => { let calls=0; const client=createClient({mode:'demo',baseUrl:''}, (async()=>{calls++; throw Error('must not fetch');}) as typeof fetch); const list=await client.listTraces(); const plan=await client.createPlan(demoFinding); assert.equal(list.total,19); assert.equal(plan.apply_enabled,false); assert.equal(calls,0); assert.equal((await client.getJournal()).length,0); });
test('aborted demo fetch rejects instead of committing stale data', async () => { const abort=new AbortController(); const call=createClient({mode:'demo',baseUrl:''}).listTraces(abort.signal); abort.abort(); await assert.rejects(call, e=>e instanceof Error && e.name==='AbortError'); });
test('base URL accepts HTTP(S), rejects credentials and non-HTTP schemes', () => { assert.equal(normalizeBaseUrl(' http://localhost:8000/ '),'http://localhost:8000'); for (const url of ['javascript:alert(1)','ftp://localhost','http://user:password@localhost','http://localhost?secret=1']) assert.throws(()=>normalizeBaseUrl(url),ApiError); });
test('incompatible schema and malformed payload are explicit failures', () => { assert.throws(()=>unwrap({schema_version:'2.0.0',payload:{}}),ApiError); assert.throws(()=>validatePayload('list',{items:[{}],total:1}),ApiError); assert.throws(()=>validatePayload('trace',{}),ApiError); validatePayload('audit',demoAudit); });
test('live API uses versioned route and unwraps contract envelope', async () => { let requested=''; const client=createClient({mode:'live',baseUrl:'http://localhost:8000'},(async(input)=>{requested=String(input); return new Response(JSON.stringify(envelope({items:demoTraces,total:18})),{headers:{'Content-Type':'application/json'}});}) as typeof fetch); assert.equal((await client.listTraces()).total,18); assert.equal(requested,'http://localhost:8000/api/v1/traces?environment_id=shop&limit=100&offset=0'); });
test('live HTTP 409 preserves conflict error and never becomes demo data', async () => { const client=createClient({mode:'live',baseUrl:''},(async()=>new Response(JSON.stringify({error:{code:'stale_revision',message:'Конфигурация изменилась'}}),{status:409})) as typeof fetch); await assert.rejects(client.createPlan(demoFinding),(e)=>e instanceof ApiError && e.status===409 && e.code==='stale_revision'); });
test('plan request contains exact recommendation refs, never an apply call', async () => { let path=''; let body: Record<string,unknown>={}; const client=createClient({mode:'live',baseUrl:''},(async(input,init)=>{path=String(input); body=JSON.parse(String(init?.body)); return new Response(JSON.stringify(envelope({plan_id:'p1',plan_hash:'hash',finding_id:'r4-orders-payment',target:'orders',expected_revision:'12',expires_at:'2026-11-10T23:59:59Z',changes:[],warnings:[],apply_enabled:false})));}) as typeof fetch); await client.createPlan(demoFinding); assert.equal(path,'/api/v1/change-plans'); assert.equal(body.environment_id,'shop'); assert.equal(body.occurrence_id,demoFinding.occurrence_id); assert.equal(body.expected_config_revision,'12'); assert.equal('apply' in body,false); });
test('non-JSON live response gives a readable error', async () => { const client=createClient({mode:'live',baseUrl:''},(async()=>new Response('<html>wrong port</html>')) as typeof fetch); await assert.rejects(client.listTraces(),(e)=>e instanceof ApiError && e.code==='invalid_json'); });
test('every demo trace has consistent summary identity and graph count',()=> { for(const summary of demoTraces){const detail=demoDetail(summary.trace_id); assert.equal(detail.spans.length,summary.span_count); assert.equal(detail.spans[0].service,summary.service); assert.equal(new Set(detail.spans.map(s=>s.service)).size,summary.service_count); } });

test('nested malformed finding and unknown journal enums fail before render',()=>{ const bad={...demoAudit,findings:[{...demoFinding,recommendation:{...demoFinding.recommendation,changes:[{field:{bad:true},before:'1',after:'2'}]}}]}; assert.throws(()=>validatePayload('audit',bad),ApiError); assert.throws(()=>validatePayload('journal',[{operation_id:'x',target:'orders',requested_at:'now',status:'surprise',verification:'checking',actual_changes:[]}]),ApiError); });

test('shared examples accept trace-only findings and the full journal status set', async () => {
  const fixtures = JSON.parse(await readFile(new URL('../../fixtures/frontend-contracts.json', import.meta.url), 'utf8'));
  const kinds = ['list', 'trace', 'audit', 'capabilities', 'journal', 'plan'] as const;
  for (const kind of kinds) validatePayload(kind, unwrap(fixtures[kind]));
  const audit = fixtures.audit.payload;
  assert.ok(audit.findings.some((finding: { evidence: { kind: string } }) => finding.evidence.kind === 'trace_observation'));
  assert.ok(fixtures.journal.payload.some((entry: { status: string }) => entry.status === 'applying'));
});

test('exact trace links preserve environment and revision and reject mismatched details', async () => {
  const detail = demoDetail(demoTraces[0].trace_id);
  const ref = detail.trace_ref;
  assert.deepEqual(readRoute(traceHref(ref)), { page: 'traces', traceId: ref.trace_id, traceRef: ref });
  let requested = '';
  const client = createClient({ mode: 'live', baseUrl: '', environmentId: 'shop' }, async input => {
    requested = String(input);
    return new Response(JSON.stringify(envelope(detail)));
  });
  assert.deepEqual((await client.getTrace(ref)).trace_ref, ref);
  assert.equal(new URL(requested, 'http://localhost').searchParams.get('environment_id'), ref.environment_id);
  await assert.rejects(client.getTrace({ ...ref, revision: ref.revision + 1 }),
    (e: unknown) => e instanceof ApiError && e.code === 'trace_ref_mismatch');
});

test('audit accepts policy observations, uses remaining budget and prevents a plan without an intent', async () => {
  const { availableBudget, canPreparePlan } = await import('../src/api/types.js');
  const budget = { ...demoFinding.evidence, budget_ns: '1000000000', available_ns: '-100000' };
  const advisory = { ...demoFinding, evidence: budget, recommendation: { ...demoFinding.recommendation, can_prepare_plan: false } };
  const observed = { ...advisory, rule_id: 'D1', evidence: { kind: 'policy_observation', source: 'runtime / declared' } };
  validatePayload('audit', { ...demoAudit, findings: [advisory, observed] });
  assert.throws(() => validatePayload('audit', {...demoAudit, findings: [{...observed, basis: 'observed'}]}), ApiError);
  assert.equal(availableBudget({ ...budget, available_ns: '0' }), '0');
  assert.equal(availableBudget(budget), '-100000');
  assert.equal(availableBudget(demoFinding.evidence), demoFinding.evidence.budget_ns);
  assert.equal(canPreparePlan(advisory), false);
  assert.equal(canPreparePlan({...demoFinding, recommendation: {...demoFinding.recommendation, changes: []}}), false);
  await assert.rejects(createClient({mode: 'demo', baseUrl: ''}).createPlan(advisory),
    (e: unknown) => e instanceof ApiError && e.code === 'no_change_intent');
  let called = false;
  const client = createClient({mode: 'live', baseUrl: ''}, async () => { called = true; throw new Error(); });
  await assert.rejects(client.createPlan(advisory), (e: unknown) => e instanceof ApiError && e.code === 'no_change_intent');
  assert.equal(called, false);
});

test('trace detail matches exact occurrences beyond the audit exemplars', () => {
  const detail = demoDetail(demoTraces[0].trace_id);
  const finding = { ...demoFinding, exemplars: [] };
  const exact = { ...detail, finding_occurrence_ids: [finding.occurrence_id],
    summary: { ...detail.summary, finding_count: 1 } };
  validatePayload('trace', exact);
  assert.deepEqual(findingsForTrace(exact, [finding]), [finding]);
  assert.deepEqual(findingsForTrace(exact, [{ ...finding, occurrence_id: 'other' }]), []);
  assert.deepEqual(findingsForTrace(detail, [finding]), []);
  assert.throws(() => validatePayload('trace', { ...exact, finding_occurrence_ids: [] }));
});


test('trace detail accepts a signed offset from an incompatible clock domain', () => {
  const detail = demoDetail(demoTraces[0].trace_id);
  const spans = detail.spans.map((span, i) => i ? { ...span, start_offset_ns: '-1000' } : span);
  validatePayload('trace', { ...detail, spans, quality_issues: ['cross_clock_uncertain'] });
  assert.throws(() => validatePayload('trace', { ...detail, spans: [{ ...spans[0], duration_ns: '-1000' }] }));
});
