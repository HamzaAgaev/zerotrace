import test from 'node:test';
import assert from 'node:assert/strict';
import { percentile, summarize, unionLength } from '../src/analysis.js';
import { demoDetail, demoTraces } from '../src/api/fixtures.js';
import { readRoute } from '../src/lib.js';
import type { Span, TraceDetail } from '../src/api/types.js';
const ns=(ms:number)=>String(ms*1e6);
const span=(id:string,parent:string|null,start:number,duration:number):Span=>({span_id:id,parent_span_id:parent,service:id,name:id,kind:'server',start_offset_ns:ns(start),duration_ns:ns(duration),outcome:'ok',http_status:200,error_type:null});
function tree(spans:Span[]):TraceDetail{return {...demoDetail(demoTraces[5].trace_id),spans,quality_issues:[]};}
test('linearly interpolated percentiles preserve input, empty is unknown',()=>{const v=[30,10,20];assert.equal(percentile(v,.5),20);assert.equal(percentile(v,.95),29);assert.equal(percentile([], .95),null);assert.deepEqual(v,[30,10,20]);});
test('overlapping child intervals are unioned, not summed',()=>{assert.equal(unionLength([[0,20],[10,30],[40,50]]),40);const stats=summarize([tree([span('root',null,0,100),span('a','root',10,50),span('b','root',20,60)])]);assert.equal(stats.services.find(s=>s.name==='root')?.own,30);});
test('cross-service calls count client/server pair once and retain client error',()=>{const trace=demoDetail(demoTraces[0].trace_id),stats=summarize([trace]);const edge=stats.edges.find(e=>e.caller==='orders'&&e.callee==='payment');assert.equal(edge?.count,3);assert.equal(edge?.errors,3);assert.equal(edge?.median,1000);assert.equal(stats.services.find(s=>s.name==='payment')?.errors,0);});
test('client-only calls show the target without inventing server timings', () => {
  const orders = { ...span('root', null, 0, 100), service: 'orders' };
  const charge: Span = {
    ...span('charge', 'root', 10, 20), kind: 'client', service: 'orders',
    target: 'payment', outcome: 'error',
  };
  const payment = { ...span('server', 'charge', 12, 16), service: 'payment' };
  const clientOnly = summarize([tree([orders, charge])]);
  assert.deepEqual(clientOnly.services.map(s => s.name), ['orders']);
  const [observed] = clientOnly.edges;
  assert.equal(observed.callee, 'payment');
  assert.equal(observed.to.external, true);
  assert.equal(observed.median, 20);
  assert.equal(observed.errors, 1);
  assert.ok(clientOnly.nodes.some(n => n.id === observed.to.id));

  const paired = summarize([tree([orders, charge, payment])]);
  assert.equal(paired.edges.length, 1);
  assert.equal(paired.edges[0].to.external, false);
  assert.equal(paired.edges[0].count, 1);
  assert.notEqual(paired.edges[0].to.id, observed.to.id);
  assert.equal(paired.services.find(s => s.name === 'payment')?.median, 16);
});
test('legacy navigation opens the consolidated sections',()=>{assert.deepEqual(readRoute('#/policies?finding=x'),{page:'graph'});assert.deepEqual(readRoute('#/compare'),{page:'traces'});assert.deepEqual(readRoute('#/services?tab=statistics'),{page:'timings'});assert.deepEqual(readRoute('#/services?tab=graph'),{page:'graph'});});
