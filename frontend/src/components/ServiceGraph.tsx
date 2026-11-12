import { useEffect, useId, useMemo, useRef, useState } from 'react';
import { graphLayout, zoomAt, type GraphNode, type GraphEdge } from '../graphLayout';
import { usePersistentState } from '../state';
import { serviceColor } from '../lib';
import { Button, EmptyState } from './ui';
export function ServiceGraph({ nodes, edges, selected, onSelect, storageKey }: { nodes:GraphNode[]; edges:GraphEdge[]; selected:string; onSelect:(id:string)=>void; storageKey:string }) {
  const layout = useMemo(()=>graphLayout(nodes,edges),[nodes,edges]);
  const host = useRef<HTMLDivElement>(null);
  const [size,setSize] = useState({width:900,height:420});
  const [camera,setCamera] = usePersistentState<{x:number;y:number;scale:number}|null>(`graph:${storageKey}:camera`,null);
  const fitScale = Math.min(1,(size.width-32)/layout.width,(size.height-32)/layout.height);
  const fitted = {scale:fitScale,x:(size.width-layout.width*fitScale)/2,y:(size.height-layout.height*fitScale)/2};
  const current = camera ?? fitted;
  const live = useRef(current); live.current=current;
  const drag = useRef<{x:number;y:number;camera:typeof current;moved:boolean}|null>(null);
  const suppressClick = useRef(false);
  const marker=useId().replaceAll(':','');
  useEffect(()=>{const el=host.current;if(!el)return;const observer=new ResizeObserver(([entry])=>setSize({width:entry.contentRect.width,height:entry.contentRect.height}));observer.observe(el);return()=>observer.disconnect();},[]);
  useEffect(()=>{const el=host.current;if(!el)return;const wheel=(event:WheelEvent)=>{event.preventDefault();const rect=el.getBoundingClientRect();setCamera(zoomAt(live.current,{x:event.clientX-rect.left,y:event.clientY-rect.top},live.current.scale*Math.exp(-event.deltaY*.0015)));};el.addEventListener('wheel',wheel,{passive:false});return()=>el.removeEventListener('wheel',wheel);},[storageKey]);
  const select=(id:string)=>{if(!suppressClick.current)onSelect(id);};
  return <><div ref={host} className="graph-canvas" tabIndex={0} role="group" aria-label="Схема сервисов. Перетаскивайте для перемещения, используйте колесо для масштаба" onKeyDown={e=>{
    if(e.target!==e.currentTarget)return;
    const offsets:Record<string,[number,number]>={ArrowLeft:[40,0],ArrowRight:[-40,0],ArrowUp:[0,40],ArrowDown:[0,-40]};
    if(offsets[e.key]){e.preventDefault();const [x,y]=offsets[e.key];setCamera({...current,x:current.x+x,y:current.y+y});}
    if(e.key==='Home'){e.preventDefault();setCamera(null);}
  }} onPointerDown={e=>{if(e.button!==0)return;suppressClick.current=false;drag.current={x:e.clientX,y:e.clientY,camera:current,moved:false};}} onPointerMove={e=>{
    const start=drag.current;if(!start)return;const dx=e.clientX-start.x,dy=e.clientY-start.y;
    if(Math.hypot(dx,dy)>4){start.moved=true;suppressClick.current=true;e.currentTarget.setPointerCapture(e.pointerId);}
    if(start.moved)setCamera({...start.camera,x:start.camera.x+dx,y:start.camera.y+dy});
  }} onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}}>
    {!nodes.length?<EmptyState title="Сервисы не найдены" description="Измените фильтры или выберите другой стенд."/>:<svg role="group" aria-label="Узлы и связи сервисов" width="100%" height="100%" viewBox={`0 0 ${size.width} ${size.height}`}>
      <defs><marker id={marker} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto"><path d="M0 0L10 5L0 10z" fill="context-stroke"/></marker></defs>
      <g transform={`translate(${current.x} ${current.y}) scale(${current.scale})`}>
        {layout.edges.map((edge,index)=>{const a=layout.positions.get(edge.from)!,b=layout.positions.get(edge.to)!;
          const forward=b.x>a.x;const sx=a.x+a.width,sy=a.y+36,tx=forward?b.x:b.x+b.width,ty=b.y+36;
          const bend=forward?(sx+tx)/2:Math.max(sx,tx)+40+(index%4)*18;
          const d=forward?`M${sx} ${sy} C${bend} ${sy} ${bend} ${ty} ${tx} ${ty}`:edge.from===edge.to?`M${sx} ${sy} C${bend+40} ${sy-100} ${bend+40} ${sy+100} ${tx} ${ty+16}`:`M${sx} ${sy} C${bend} ${sy} ${bend} ${ty} ${tx} ${ty}`;
          return <g key={edge.id} className={`graph-edge ${edge.problem?'has-problem':''} ${selected===edge.id?'is-selected':''}`} role="button" tabIndex={0} aria-label={`${nodes.find(n=>n.id===edge.from)?.label} → ${nodes.find(n=>n.id===edge.to)?.label}`} onClick={()=>select(edge.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(edge.id);}}}><path className="edge-hit" d={d}/><path className="edge-line" d={d} markerEnd={`url(#${marker})`}/>{edge.label&&<text x={bend} y={(sy+ty)/2-10} textAnchor="middle">{edge.label}</text>}</g>;
        })}
        {nodes.map(node=>{const p=layout.positions.get(node.id)!;return <g key={node.id} transform={`translate(${p.x} ${p.y})`} className={`graph-node ${selected===node.id?'is-selected':''}`} role="button" tabIndex={0} aria-label={`Сервис ${node.label}`} onClick={()=>select(node.id)} onKeyDown={e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();onSelect(node.id);}}}><rect width={p.width} height={p.height} rx="8"/><circle cx="18" cy="25" r="4" fill={node.color??serviceColor(node.id)}/><text x="32" y="30">{node.label}</text>{node.detail&&<text className="graph-node-meta" x="16" y="54">{node.detail}</text>}</g>;})}
      </g>
    </svg>}
  </div><div className="graph-controls"><Button aria-label="Уменьшить масштаб" onClick={()=>setCamera(zoomAt(current,{x:size.width/2,y:size.height/2},current.scale/1.2))}>−</Button><span>{Math.round(current.scale*100)}%</span><Button aria-label="Увеличить масштаб" onClick={()=>setCamera(zoomAt(current,{x:size.width/2,y:size.height/2},current.scale*1.2))}>+</Button><Button onClick={()=>setCamera(null)}>Уместить схему</Button></div></>;
}
