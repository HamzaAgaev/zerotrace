import test from 'node:test';
import assert from 'node:assert/strict';
import { trackRequest,getPendingRequests,subscribeRequests } from '../src/api/activity.js';
test('activity remains pending until all requests settle, including failure and abort',async()=>{
  const observed:number[]=[];
  const unsubscribe=subscribeRequests(()=>observed.push(getPendingRequests()));
  let resolve!:()=>void, reject!:(reason:unknown)=>void;
  const first=trackRequest(()=>new Promise<void>(done=>{resolve=done;}));
  const second=trackRequest(()=>new Promise<void>((_,fail)=>{reject=fail;}));
  assert.equal(getPendingRequests(),2);
  resolve();await first;assert.equal(getPendingRequests(),1);
  reject(new DOMException('Aborted','AbortError'));
  await assert.rejects(second,{name:'AbortError'});
  assert.equal(getPendingRequests(),0);
  await assert.rejects(trackRequest(()=>{throw new Error('failure');}));
  assert.equal(getPendingRequests(),0);
  unsubscribe();assert.deepEqual(observed,[1,2,1,0,1,0]);
});
