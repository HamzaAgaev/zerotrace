import test from 'node:test';
import assert from 'node:assert/strict';
import { checkBudget, cohortStats, compareCohorts, percentile, projectIntervals, summarize, unionLength } from '../src/analysis.js';
import { demoDetail, demoTraces } from '../src/api/fixtures.js';
import { readRoute } from '../src/lib.js';
import type { Span, TraceDetail } from '../src/api/types.js';
const ns=(ms:number)=>String(ms*1e6);
const span=(id:string,parent:string|null,start:number,duration:number):Span=>({span_id:id,parent_span_id:parent,service:id,name:id,kind:'server',start_offset_ns:ns(start),duration_ns:ns(duration),outcome:'ok',http_status:200,error_type:null});
function tree(spans:Span[]):TraceDetail{return {...demoDetail(demoTraces[5].trace_id),spans,quality_issues:[]};}
test('nearest-rank percentiles preserve input, empty is unknown',()=>{const v=[30,10,20];assert.equal(percentile(v,.5),20);assert.equal(percentile(v,.95),30);assert.equal(percentile([], .95),null);assert.deepEqual(v,[30,10,20]);});
test('overlapping child intervals are unioned, not summed',()=>{assert.equal(unionLength([[0,20],[10,30],[40,50]]),40);const stats=summarize([tree([span('root',null,0,100),span('a','root',10,50),span('b','root',20,60)])]);assert.equal(stats.services.find(s=>s.name==='root')?.own,30);});
test('cross-service calls count client/server pair once and retain client error',()=>{const trace=demoDetail(demoTraces[0].trace_id),stats=summarize([trace]);const edge=stats.edges.find(e=>e.caller==='orders'&&e.callee==='payment');assert.equal(edge?.count,3);assert.equal(edge?.errors,3);assert.equal(edge?.median,1000);assert.equal(stats.services.find(s=>s.name==='payment')?.errors,0);});
test('policy budget includes first attempt and n-1 pauses',()=>{assert.deepEqual(checkBudget({timeout:800,attempts:2,backoff:50,budget:2000}),{valid:true,required:1650,reserve:350});assert.equal(checkBudget({timeout:1000,attempts:3,backoff:75,budget:2000}).reserve,-1150);assert.equal(checkBudget({timeout:800,attempts:1,backoff:999,budget:900}).required,800);});
test('remaining budget, including exhausted and overspent values, is compared to all attempts', () => {
  const draft = { timeout: 250, attempts: 3, backoff: null, budget: 700 };
  const configured = { attempts: 3, total: 100 };
  assert.deepEqual(checkBudget(draft, configured), { valid: true, required: 850, reserve: -150 });
  for (const budget of [0, -50]) {
    assert.deepEqual(checkBudget({...draft, budget}, configured), { valid: true, required: 850, reserve: budget - 850 });
  }
  assert.equal(checkBudget({...draft, budget: NaN}, configured).valid, false);
  // The same aggregate is not a pause schedule for a hypothetical attempt count.
  assert.equal(checkBudget({...draft, attempts: 2}, configured).valid, false);
  assert.equal(checkBudget({...draft, attempts: 1}, configured).required, 250);
  assert.equal(checkBudget({...draft, attempts: 2, backoff: 50}, configured).required, 550);
});
test('invalid drafts cannot be exported as calculated budgets',()=>{for(const draft of [{timeout:0,attempts:2,backoff:50,budget:2000},{timeout:800,attempts:2.5,backoff:50,budget:2000},{timeout:800,attempts:2,backoff:NaN,budget:2000},{timeout:Infinity,attempts:2,backoff:50,budget:2000}]) assert.equal(checkBudget(draft).required,null);});
test('cohorts reject overlap, mixed operations and empty groups',()=>{assert.match(compareCohorts([],[])??'',/Выберите/);assert.match(compareCohorts([demoTraces[0]],[demoTraces[0]])??'',/пересекаться/);assert.match(compareCohorts([demoTraces[0]],[demoTraces[9]])??'',/операцию/);assert.equal(compareCohorts([demoTraces[0]],[demoTraces[5]]),null);assert.equal(cohortStats([demoTraces[8]]).unknown,1);});
test('interval projection preserves baseline and shifts sequential blocks',()=>{const trace=tree([span('root',null,0,100),span('a','root',10,30),span('b','root',50,40)]);assert.equal(projectIntervals(trace,{}).projected,100);assert.equal(projectIntervals(trace,{a:50}).projected,85);assert.equal(projectIntervals(trace,{a:50,b:50}).projected,65);assert.equal(projectIntervals(trace,{a:0}).gain,0);});
test('parallel interval model retains offsets and limiting sibling',()=>{const trace=tree([span('root',null,0,100),span('a','root',10,50),span('b','root',20,60)]);assert.equal(projectIntervals(trace,{a:100}).gain,0);assert.equal(projectIntervals(trace,{b:50}).projected,80);assert.equal(projectIntervals(trace,{a:100,b:100}).projected,40);});
test('invalid quality, bounds, cycles and overlapping branch choices reject projection',()=>{assert.throws(()=>projectIntervals(demoDetail(demoTraces[0].trace_id),{}));const trace=tree([span('root',null,0,100),span('a','root',0,80),span('b','a',0,50)]);assert.throws(()=>projectIntervals(trace,{a:50,b:50}),/непересекающиеся/);assert.throws(()=>projectIntervals(trace,{missing:10}));assert.throws(()=>projectIntervals(tree([span('root',null,0,100),span('a','root',90,20)]),{}),/границы/);assert.throws(()=>projectIntervals(tree([span('root',null,0,100),span('a','b',0,10),span('b','a',0,10)]),{}),/недостижимые/);});
test('healthy fixture projection is monotonic for each eligible branch',()=>{const trace=demoDetail(demoTraces[5].trace_id);const baseline=projectIntervals(trace,{});for(const s of trace.spans.filter(s=>s.parent_span_id)){let previous=baseline.baseline;for(const percent of [0,25,50,75,100]){const r=projectIntervals(trace,{[s.span_id]:percent});assert.ok(r.projected<=previous+1e-8);assert.ok(r.projected>=0);previous=r.projected;}}});
test('new deep links retain route when a finding query is attached',()=>{for(const page of ['graph','timings','policies','compare'])assert.deepEqual(readRoute(`#/${page}?finding=x`),{page});});

test('cross-clock durations remain visible without subtracting unrelated child intervals', () => {
  const trace = tree([span('root', null, 0, 100), span('child', 'root', -50, 80)]);
  const stats = summarize([{ ...trace, quality_issues: ['cross_clock_uncertain'] }]);
  const root = stats.services.find(s => s.name === trace.spans[0].service);
  assert.equal(root?.own, null);
  assert.notEqual(root?.median, null);
});
