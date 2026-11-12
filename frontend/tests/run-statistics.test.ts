import test from 'node:test';
import assert from 'node:assert/strict';
import { runStatistics } from '../src/runStatistics.js';
import { readRoute } from '../src/lib.js';
test('test results distinguish HTTP errors, transport failures and unknown outcomes',()=>{
  const stats=runStatistics([
    {index:0,http_status:200,error:null,duration_ns:'100000000'},
    {index:1,http_status:302,error:null,duration_ns:'300000000'},
    {index:2,http_status:503,error:null,duration_ns:'1000000000'},
    {index:3,http_status:null,error:'timeout',duration_ns:'3000000000'},
    {index:4,http_status:null,error:null,duration_ns:'0'},
  ]);
  assert.equal(stats.success,2);assert.equal(stats.errors,2);assert.equal(stats.unknown,1);
  assert.equal(stats.median,300);assert.ok(Math.abs(stats.p95!-2600)<1e-8);
  assert.equal(stats.responses.find(([name])=>name==='Время ожидания истекло')?.[1],1);
  assert.equal(stats.durations.reduce((sum,b)=>sum+b.count,0),5);
});
test('empty run has no invented latency or successes and direct link retains run',()=>{
  const stats=runStatistics([]);assert.equal(stats.median,null);assert.equal(stats.p95,null);assert.equal(stats.count,0);
  assert.deepEqual(readRoute('#/lab?run=run-123'),{page:'lab',runId:'run-123'});
});
