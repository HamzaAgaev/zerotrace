import test from 'node:test';
import assert from 'node:assert/strict';
import { graphLayout, zoomAt } from '../src/graphLayout.js';
test('shared dependency is placed after both callers',()=>{
  const graph=graphLayout(['gateway','orders','stock'].map(id=>({id,label:id})),[
    {id:'1',from:'gateway',to:'stock'},{id:'2',from:'gateway',to:'orders'},{id:'3',from:'orders',to:'stock'},
  ]);
  assert.ok(graph.positions.get('stock')!.x > graph.positions.get('orders')!.x);
});
test('graph keeps branching, cycles, self calls and disconnected services without overlaps',()=>{
  const nodes=['a','b','c','d','isolated'].map(id=>({id,label:id}));
  const edges=[['a','b'],['a','c'],['b','d'],['c','d'],['d','a'],['a','a']].map(([from,to],i)=>({id:String(i),from,to}));
  const graph=graphLayout(nodes,edges);
  assert.equal(graph.positions.size,5);assert.equal(graph.edges.length,6);
  const boxes=[...graph.positions.values()];
  boxes.forEach((a,i)=>boxes.slice(i+1).forEach(b=>assert.ok(a.x+a.width<=b.x||b.x+b.width<=a.x||a.y+a.height<=b.y||b.y+b.height<=a.y)));
});
test('zoom keeps world point under cursor and clamps extremes',()=>{
  const camera={x:40,y:-25,scale:.75},point={x:300,y:200};
  for(const scale of [1.5,.01,10]){
    const next=zoomAt(camera,point,scale);
    assert.ok(next.scale>=.15&&next.scale<=3);
    assert.ok(Math.abs((point.x-camera.x)/camera.scale-(point.x-next.x)/next.scale)<1e-8);
    assert.ok(Math.abs((point.y-camera.y)/camera.scale-(point.y-next.y)/next.scale)<1e-8);
  }
});
