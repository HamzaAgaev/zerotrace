import { readFile } from 'node:fs/promises';
import test from 'node:test';
import assert from 'node:assert/strict';
import { compactTimelineSpans, filterTraces, findingsForTrace, ms, duration, readRoute, timelineRows, traceHref } from '../src/lib.js';
import { demoDetail, demoFinding, demoTraces, demoAudit } from '../src/api/fixtures.js';
import { ApiError, createClient, normalizeBaseUrl, unwrap, validatePayload } from '../src/api/client.js';
const envelope = (payload: unknown) => ({ schema: 'test', schema_version: '1.0.0', producer_version: 'test', generated_at: '2026-11-10T00:00:00Z', payload });

test('compacting the timeline promotes the server span and keeps the original tree', () => {
  const sample = demoDetail(demoTraces[0].trace_id).spans[0];
  const root = { ...sample, span_id: 'root', parent_span_id: null };
  const client = { ...sample, span_id: 'client', parent_span_id: 'root' };
  const server = { ...sample, span_id: 'server', parent_span_id: 'client' };
  const unpaired = { ...sample, span_id: 'unpaired', parent_span_id: 'root' };
  const spans = [root, client, server, unpaired];
  assert.deepEqual(compactTimelineSpans(spans, { client: 'server', unpaired: 'server' }), [
    root, { ...server, parent_span_id: 'root' }, unpaired,
  ]);
  assert.equal(spans[2].parent_span_id, 'client');
});

test('trace card requests spans without kind, including from older servers', async () => {
  const fixture = demoDetail(demoTraces[0].trace_id);
  const urls: URL[] = [];
  const client = createClient({ mode: 'live', baseUrl: 'http://localhost:8000' }, async input => {
    urls.push(new URL(String(input)));
    return Response.json(envelope(fixture));
  });
  const compact = await client.getTrace(fixture.trace_ref, undefined, { includeSpanKind: false });
  const full = await client.getTrace(fixture.trace_ref);
  assert.equal(urls[0].searchParams.get('include_span_kind'), 'false');
  assert.equal(urls[1].searchParams.get('include_span_kind'), null);
  assert.ok(compact.spans.every(span => !Object.hasOwn(span, 'kind')));
  assert.ok(full.spans.every(span => span.kind !== undefined));
});
test('audit polls the same job until the server returns a result', async () => {
  const calls: URL[] = [];
  const jobId = 'a'.repeat(32);
  const client = createClient({ mode: 'live', baseUrl: 'http://localhost:8000' }, async input => {
    calls.push(new URL(String(input)));
    return calls.length === 1
      ? Response.json(envelope({ status: 'pending', job_id: jobId }), { status: 202 })
      : Response.json(envelope(demoAudit));
  });
  const ref = demoTraces[0].trace_ref;
  assert.deepEqual(await client.getAudit(undefined, ref), demoAudit);
  for (const url of calls) {
    assert.equal(url.searchParams.get('trace_id'), ref.trace_id);
    assert.equal(url.searchParams.get('environment_id'), ref.environment_id);
    assert.equal(url.searchParams.get('generation'), String(ref.generation));
    assert.equal(url.searchParams.get('revision'), String(ref.revision));
  }
  assert.equal(calls.length, 2);
  assert.equal(calls[0].searchParams.get('defer'), 'true');
  assert.equal(calls[1].searchParams.get('job_id'), jobId);
});
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

test('trace membership uses exact occurrence IDs even outside the exemplars', () => {
  const trace = demoDetail(demoTraces[0].trace_id);
  const finding = { ...demoFinding, exemplars: [] };
  assert.deepEqual(findingsForTrace({ ...trace, finding_occurrence_ids: [finding.occurrence_id] }, [finding]), [finding]);
  assert.deepEqual(findingsForTrace({ ...trace, finding_occurrence_ids: [] }, [demoFinding]), []);
  assert.throws(() => validatePayload('trace', { ...trace, finding_occurrence_ids: [42] }), ApiError);
});

test('the live client requests and accepts full config-aware policy observations', async () => {
  const audit = { ...demoAudit, findings: [{ ...demoFinding, evidence: { kind: 'policy_observation', source: 'runtime' } }] };
  const client = createClient({ mode: 'live', baseUrl: '' }, async input => {
    assert.equal(new URL(String(input), 'http://localhost').searchParams.get('view'), 'full');
    return new Response(JSON.stringify(envelope(audit)));
  });
  assert.deepEqual(await client.getAudit(), audit);
});

test('compact chain hides failed senders, preserves receivers, retries and unpaired calls', () => {
  const sample = demoDetail(demoTraces[0].trace_id).spans[0];
  const root = { ...sample, span_id:'root', parent_span_id:null, service:'orders', kind:'server' as const };
  const sender = { ...sample, span_id:'b4565446772bfb58', parent_span_id:'root', service:'orders', name:'POST', kind:'client' as const, outcome:'error' as const, http_status:504, error_type:'timeout' };
  const receiver = { ...sample, span_id:'payment', parent_span_id:sender.span_id, service:'payment', name:'POST /charge', kind:'server' as const, http_status:200 };
  const retrySender = { ...sender, span_id:'retry' };
  const retryReceiver = { ...receiver, span_id:'payment-retry', parent_span_id:'retry' };
  const unpaired = { ...sender, span_id:'unpaired' };
  const spans = [root,sender,receiver,retrySender,retryReceiver,unpaired];
  const compact = compactTimelineSpans(spans);
  assert.deepEqual(compact.map(span=>span.span_id), ['root','payment','payment-retry','unpaired']);
  assert.equal(compact[1].parent_span_id,'root');
  assert.equal(compact[2].parent_span_id,'root');
  assert.equal(receiver.parent_span_id,sender.span_id);
  assert.equal(spans.length,6);
  const withoutKind=spans.map(({kind,...span})=>span);
  assert.deepEqual(compactTimelineSpans(withoutKind,{[sender.span_id]:receiver.span_id,retry:'payment-retry'}).map(span=>span.span_id),compact.map(span=>span.span_id));
});

test('product and guide routes preserve the existing default and versioned trace links', () => {
  assert.deepEqual(readRoute('#/home'), { page: 'home' });
  assert.deepEqual(readRoute('#/guide?section=traces'), { page: 'guide' });
  assert.deepEqual(readRoute(''), { page: 'home' });
  assert.deepEqual(readRoute('#/'), { page: 'home' });
  assert.deepEqual(readRoute('#'), { page: 'home' });
  assert.deepEqual(readRoute('#/traces'), { page: 'traces' });
  const ref = { environment_id: 'shop', trace_id: 'abc', generation: 2, revision: 3 };
  assert.deepEqual(readRoute(traceHref(ref)), { page: 'traces', traceId: 'abc', traceRef: ref });
});
