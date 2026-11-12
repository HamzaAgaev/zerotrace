import { test } from 'node:test';
import assert from 'node:assert/strict';
import { latencyHistogram } from '../src/latencyHistogram.js';
test('equal bins preserve zero, boundaries and maximum without overflow',()=>{
 const bins=latencyHistogram([0,99,100,200,300,400,500]);
 assert.deepEqual(bins.map(b=>b.count),[1,1,1,0,1,0,1,0,1,1]);
 assert.deepEqual(bins.map(b=>[b.lower_ms,b.upper_ms]),Array.from({length:10},(_,i)=>[i*50,(i+1)*50]));
});
test('empty, sub-millisecond and slow requests have finite equal-width bounds',()=>{
 for(const values of [[],[0,.1,.9],[12000,20000,50000]]){
  const bins=latencyHistogram(values);
  assert.equal(bins.reduce((n,b)=>n+b.count,0),values.length);
  assert.ok(bins.every(b=>b.upper_ms-b.lower_ms===bins[0].upper_ms));
 }
});

test('maximum lies in the last bin and scale follows slow requests',()=>{
 const bins=latencyHistogram([80,320,1301]);
 assert.equal(bins.length,10);
 assert.equal(bins.at(-1)!.count,1);
 assert.equal(bins.at(-1)!.upper_ms,1400);
});
