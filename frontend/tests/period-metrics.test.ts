import test from 'node:test';
import assert from 'node:assert/strict';
import { createClient } from '../src/api/client.js';
import { calculatePeriodMetrics } from '../src/api/periodMetrics.js';
import { demoDetail,demoTraces } from '../src/api/fixtures.js';
test('period summary counts root requests, own endpoints, and respects time boundaries',()=>{
  const details=demoTraces.map(t=>demoDetail(t.trace_id));
  const result=calculatePeriodMetrics(details,30);
  assert.equal(result.summary.count,details.length);
  assert.ok(result.endpoints.some(e=>e.service==='payment' && e.endpoint==='POST /charge'));
  const empty=calculatePeriodMetrics(details,30,1n);
  assert.equal(empty.summary.count,0);
  assert.equal(empty.summary.success_rate,null);
  const fixture=details[0];
  const one=calculatePeriodMetrics([fixture],30);
  assert.equal(one.summary.median_ns,fixture.spans.find(s=>s.parent_span_id===null)!.duration_ns);
});
test('demo filters include nested services and combine multiple selections',async()=>{
  const client=createClient({mode:'demo',baseUrl:''});
  const all=await client.listTraces(undefined,{sort:'recent'});
  assert.ok(all.services!.includes('payment'));
  const rows=await client.listTraces(undefined,{services:['missing','payment'],statuses:['error'],methods:['POST','GET'],rules:['R4']});
  assert.ok(rows.total>0);
  assert.ok(rows.items.every(t=>t.outcome==='error'));
  assert.equal((await client.listTraces(undefined,{services:['missing'],rules:['R4']})).total,0);
});
test('live rule search polls the same job and sends array filters intact',async()=>{
  const urls: URL[]=[];
  const client=createClient({mode:'live',baseUrl:'http://localhost:8000'},async input=>{
    urls.push(new URL(String(input)));
    return Response.json({schema_version:'1.0.0',payload:urls.length===1?{status:'pending',job_id:'a'.repeat(32)}:{items:[],total:0}},{status:urls.length===1?202:200});
  });
  await client.listTraces(undefined,{services:['a,b','payment'],rules:['R4','R1'],includeFindings:false});
  assert.deepEqual(JSON.parse(urls[0].searchParams.get('services')!),['a,b','payment']);
  assert.equal(urls[1].searchParams.get('job_id'),'a'.repeat(32));
  assert.equal(urls[0].searchParams.get('rules'),urls[1].searchParams.get('rules'));
});

test('charts partition the selected period and histogram boundaries without losing requests',()=>{
  const now=1_800_000_000_000_000_000n, cutoff=now-30n*60_000_000_000n;
  const template=demoDetail(demoTraces[0].trace_id);
  const root=template.spans.find(s=>s.parent_span_id===null)!;
  const details=[0n,100_000_000n,300_000_000n,1_000_000_000n,3_000_000_000n].map((duration,i)=>({...template,
    summary:{...template.summary,start_ns:String(i===0?cutoff:now)},
    spans:[{...root,start_offset_ns:'0',duration_ns:String(duration),kind:'server' as const,outcome:'ok' as const}]}));
  const data=calculatePeriodMetrics(details,30,now);
  assert.equal(data.timeline!.length,12);
  assert.equal(data.timeline![0].count,1);
  assert.equal(data.timeline![11].count,4);
  assert.equal(data.timeline!.reduce((n,p)=>n+p.count,0),5);
  assert.equal(data.timeline![1].median_ns,null);
  assert.equal(data.timeline![1].success_rate,null);
  assert.deepEqual(data.summary.latency_histogram!.map(b=>b.count),[2,1,0,1,0,0,0,0,0,1]);
  assert.deepEqual(data.endpoints[0].latency_histogram,data.summary.latency_histogram);
  assert.equal(calculatePeriodMetrics(details,5,now).summary.count,4);
});
