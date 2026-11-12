import type { LatencyBucket, PeriodMetrics } from '../api/types';
import { Panel } from './ui';

export function LatencyHistogram({buckets}:{buckets:LatencyBucket[]}) {
  const peak=Math.max(1,...buckets.map(b=>b.count));
  if(!buckets.some(b=>b.count))return <p className="muted">Нет измерений времени за выбранный период.</p>;
  const step=Math.max(1,Math.ceil(peak/4));
  const ceiling=step*4;
  const left=56, right=580, top=32, bottom=216;
  const width=(right-left)/buckets.length;
  const y=(count:number)=>bottom-count/ceiling*(bottom-top);
  return <svg className="latency-histogram" viewBox="0 0 600 280" role="img" aria-label="Распределение времени ответа: по горизонтали время ответа, по вертикали количество запросов">
    <text x={left} y="16">Количество запросов</text>
    {[0,1,2,3,4].map(i=><g key={i}>
      <line x1={left} x2={right} y1={y(i*step)} y2={y(i*step)} className="chart-gridline"/>
      <text x={left-10} y={y(i*step)+4} textAnchor="end">{i*step}</text>
    </g>)}
    <line x1={left} x2={left} y1={top} y2={bottom} className="chart-gridline"/>
    {buckets.map((bucket,i)=>{const center=left+width*(i+.5);return <g key={bucket.label}>
      <rect x={center-width*.5} y={y(bucket.count)} width={width-1} height={bottom-y(bucket.count)} rx="1" fill="var(--accent)" tabIndex={0} aria-label={`${bucket.label}: ${bucket.count} запросов`}><title>{bucket.label}: {bucket.count} запросов</title></rect>

    </g>;})}
    {[buckets[0].lower_ms,...buckets.map(b=>b.upper_ms)].map((value,i)=><g key={i}>
      <line x1={left+i*width} x2={left+i*width} y1={bottom} y2={bottom+5} className="chart-gridline"/>
      <text x={left+i*width} y={bottom+22} textAnchor="middle">{value.toLocaleString('ru-RU')}</text>
    </g>)}
    <text x={(left+right)/2} y="271" textAnchor="middle">Время ответа, мс</text>
  </svg>;
}
type Point=NonNullable<PeriodMetrics['timeline']>[number];
const time=(ns:string)=>new Date(Number(BigInt(ns)/1_000_000n)).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'});
const fullTime=(ns:string)=>new Date(Number(BigInt(ns)/1_000_000n)).toLocaleString('ru-RU');
function Trend({points,mode}:{points:Point[];mode:'errors'|'latency'}) {
  const series=mode==='errors'?[{label:'Доля ошибок',color:'var(--red)',values:points.map(p=>p.count-p.unknown>0?p.errors/(p.count-p.unknown)*100:null)}]:[
    {label:'Медиана',color:'var(--accent)',values:points.map(p=>p.median_ns===null?null:Number(p.median_ns)/1e6)},
    {label:'95-й перцентиль',color:'var(--green)',values:points.map(p=>p.p95_ns===null?null:Number(p.p95_ns)/1e6)},
  ];
  const max=mode==='errors'?100:Math.max(1,...series.flatMap(s=>s.values.filter((v):v is number=>v!==null)))*1.1;
  const x=(i:number)=>56+i*510/Math.max(1,points.length-1), y=(v:number)=>170-v/max*140;
  const number=(v:number)=>v.toLocaleString('ru-RU',{maximumFractionDigits:1});
  const unit=mode==='errors'?'%':'мс';
  const label=(i:number)=>`${fullTime(points[i].start_ns)} — ${time(points[i].end_ns)}. Запросов: ${points[i].count}, ошибок: ${points[i].errors}, неизвестных: ${points[i].unknown}. ${series.map(s=>`${s.label}: ${s.values[i]===null?'нет данных':number(s.values[i]!)+unit}`).join('. ')}`;
  if(!series.some(s=>s.values.some(v=>v!==null)))return <p className="muted">Нет данных за выбранный период.</p>;
  return <>
    <div className="trend-legend">{series.map(s=><span key={s.label}><i style={{background:s.color}}/>{s.label}</span>)}</div>
    <svg className="trend-chart" viewBox="0 0 600 210" role="group" aria-label={mode==='errors'?'Динамика доли ошибок':'Динамика времени ответа'}>
      {[0,.5,1].map(f=><g key={f}><line x1="56" x2="566" y1={y(max*f)} y2={y(max*f)} className="chart-gridline"/><text x="49" y={y(max*f)+4} textAnchor="end">{number(max*f)}{unit}</text></g>)}
      {series.map(s=>{let previous=false;const d=s.values.map((value,i)=>{if(value===null){previous=false;return '';}const part=`${previous?'L':'M'} ${x(i)} ${y(value)}`;previous=true;return part;}).join(' ');return <g key={s.label}><path d={d} fill="none" stroke={s.color} strokeWidth="2"/>{s.values.map((v,i)=>v!==null?<circle key={i} cx={x(i)} cy={y(v)} r="3" fill={s.color}/>:null)}</g>;})}
      {points.map((p,i)=><g key={p.start_ns}><rect x={x(i)-15} y="20" width="30" height="160" fill="transparent" tabIndex={0} role="img" aria-label={label(i)}><title>{label(i)}</title></rect>{[0,Math.floor(points.length/2),points.length-1].includes(i)&&<text x={x(i)} y="198" textAnchor="middle">{time(p.start_ns)}</text>}</g>)}
    </svg>
  </>;
}
export function PeriodTrends({data}:{data:PeriodMetrics}) {
  if(!data.timeline)return <p className="muted">Сервер не предоставил данные для графиков.</p>;
  return <div className="test-chart-grid period-trends">
    <Panel title="Ошибки по времени"><div className="lab-body"><Trend points={data.timeline} mode="errors"/></div></Panel>
    <Panel title="Время ответа по времени"><div className="lab-body"><Trend points={data.timeline} mode="latency"/></div></Panel>
  </div>;
}
