import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeStored, encodeStored } from '../src/storage.js';
import { serviceSurfaceForHash, readRoute, traceHref } from '../src/lib.js';
import { fieldLabel, formatCount, formatMs, formatPercent, journalStatusLabel, statusLabel, verificationLabel } from '../src/presentation.js';
test('stored state restores drafts and rejects malformed or incompatible records',()=>{
 const draft={service:'orders',timeout:'',attempts:'2'};
 assert.deepEqual(decodeStored(encodeStored(draft),{service:'',timeout:'500',attempts:'1'}),draft);
 assert.equal(decodeStored('{bad', 'timeline'),'timeline');
 assert.equal(decodeStored(encodeStored(4),'timeline'),'timeline');
 assert.deepEqual(decodeStored(encodeStored([null]),[] as string[]),[]);
 assert.deepEqual(decodeStored(encodeStored({service:4}),{service:''}),{service:''});
});
test('exact trace contexts remain distinct and identifiers are not truncated',()=>{
 const ref={environment_id:'shop',trace_id:'abcdef1234567890abcdef1234567890',generation:1,revision:1};
 assert.notEqual(traceHref(ref),traceHref({...ref,revision:2}));
 assert.notEqual(traceHref(ref),traceHref({...ref,environment_id:'sandbox'}));
 assert.match(traceHref(ref),new RegExp(ref.trace_id));
 assert.deepEqual(readRoute(traceHref(ref)),{page:'traces',traceId:ref.trace_id,traceRef:ref});
});

test('retired comparison links open the ordinary trace list',()=>{
 assert.equal(readRoute('#/compare').page,'traces');
 assert.equal(readRoute('#/traces?mode=compare').page,'traces');
});
test('nested state rejects arrays in place of records and malformed nested fields',()=>{
 const fallback={positions:{},details:{}};
 assert.deepEqual(decodeStored(encodeStored({positions:[],details:{}}),fallback),fallback);
 const fault={failure_point:{mode:'fixed',boundary_id:'before-first'}};
 assert.deepEqual(decodeStored(encodeStored({failure_point:{mode:4}}),fault),fault);
 const lab={runs:[] as {id:string}[],stand:null};
 assert.deepEqual(decodeStored(encodeStored({runs:[{id:'saved'}],stand:null}),lab),{runs:[{id:'saved'}],stand:null});
});

test('service deep links override saved tabs including legacy graph and finding links',()=>{
 assert.equal(serviceSurfaceForHash('#/graph','table'),'graph');
 assert.equal(serviceSurfaceForHash('#/policies?finding=p11','table'),'graph');
 assert.equal(serviceSurfaceForHash('#/timings','graph'),'table');
 assert.equal(serviceSurfaceForHash('#/services?tab=graph','table'),'graph');
 assert.equal(serviceSurfaceForHash('#/services?tab=statistics','graph'),'table');
 assert.equal(serviceSurfaceForHash('#/services','table'),'table');
});

test('human-facing labels separate applied settings from symptom checks',()=>{
 assert.equal(statusLabel('waiting'),'Ожидаем трейсы');
 assert.equal(journalStatusLabel('applied'),'Настройки применены');
 assert.notEqual(journalStatusLabel('applied'),verificationLabel('symptom_absent'));
 assert.equal(verificationLabel('symptom_absent'),'Проблема больше не наблюдается');
 assert.equal(verificationLabel(null),'Не проверялась');
 assert.equal(fieldLabel('work.delay_ms'),'Время обработки запроса, мс');
 assert.equal(fieldLabel('calls.payment.max_attempts'),'Всего попыток, включая первую');
 assert.equal(formatMs(12.34),'12,3\u00A0мс');
 assert.equal(formatMs(null),'Неизвестно');
 assert.equal(formatCount(1200),'1 200');
 assert.equal(formatPercent(.951),'95,1 %');
 assert.equal(formatPercent(null),'—');
});

test('explicit draft validation restores alternate modes instead of imposing the default shape',()=>{
 type Draft = {mode:'fixed';boundary_id:string} | {mode:'random';boundary_ids:string[]};
 const fallback:Draft={mode:'fixed',boundary_id:'before-first'};
 const validate=(value:unknown):value is Draft=>{
  if(!value||typeof value!=='object')return false;
  const draft=value as Draft;
  return draft.mode==='fixed'?typeof draft.boundary_id==='string':draft.mode==='random'&&Array.isArray(draft.boundary_ids)&&draft.boundary_ids.every(id=>typeof id==='string');
 };
 const random:Draft={mode:'random',boundary_ids:['before-first','after-first']};
 assert.deepEqual(decodeStored<Draft>(encodeStored(random),fallback,validate),random);
 assert.deepEqual(decodeStored<Draft>(encodeStored({mode:'random',boundary_ids:[null]}),fallback,validate),fallback);
});
