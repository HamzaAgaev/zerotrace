import { useState } from 'react';
import type { AuditResult, Finding } from '../api/types';
import { Badge, Button, EmptyState, Icon, Panel } from '../components/ui';
import { MetricInfo, Metrics } from '../components/Metrics';
import { Tabs } from '../components/Tabs';
import { duration, shortId, traceHref } from '../lib';
export function FindingCard({ finding:f, onPlan, pending }: { finding:Finding; onPlan:(finding:Finding)=>void; pending:boolean }) {
  const evidence = f.evidence;
  const [tab,setTab]=useState<'summary'|'evidence'|'traces'>('summary');
  return <Panel className="finding-panel"><div className="finding-top"><div className="finding-label"><Badge tone={f.severity==='high'?'danger':f.severity==='medium'?'warning':'neutral'} dot>{f.severity==='high'?'Высокая важность':f.severity==='medium'?'Средняя важность':'Низкая важность'}</Badge><span className="mono muted">{f.rule_id}, v{f.rule_version}</span></div></div><div className="finding-body"><div className="edge-label"><span>Из {f.edge.caller} в {f.edge.callee}</span><span className="mono muted">{f.edge.operation}</span></div><h2>{f.title}</h2>
    <Tabs label={`Детали находки ${f.rule_id}`} value={tab} onChange={setTab} items={[{value:'summary',label:'Решение'},{value:'evidence',label:'Обоснование'},{value:'traces',label:'Примеры трасс'}]}/>
    {tab==='summary'?<div className="recommendation"><div className="metric-section">
      <Metrics items={[
        { label: 'Затронуто трасс', value: `${f.affected_count} / ${f.denominator_count}`, tone: 'warning' },
        ...(evidence.kind === 'retry_budget' ? [
          { label: 'Требуется', value: duration(evidence.required_ns), tone: 'danger' as const },
          { label: 'Бюджет', value: duration(evidence.budget_ns) },
        ] : []),
      ]}/>
      <MetricInfo label="Что означают показатели находки">
        Затронутые трассы считаются среди трасс, пригодных для проверки правила.
        {evidence.kind === 'retry_budget' && ' Требуемое время рассчитывается из таймаута и пауз. Бюджет задаёт доступное время для вызова.'}
      </MetricInfo>
    </div><h3>{f.recommendation.text}</h3>{evidence.kind === 'retry_budget' && <div className="recommendation-bottom"><Button variant="primary" icon="external" disabled={pending} onClick={()=>onPlan(f)}>{pending?'Готовим план…':'Посмотреть план'}</Button></div>}</div>:tab==='evidence'?<div><p className="body-copy">{f.explanation}</p><p className="body-copy">{f.recommendation.why}</p>{evidence.kind === 'retry_budget' ? <div className="evidence"><div className="evidence-heading"><span>Расчёт бюджета</span><Badge tone="accent">{f.basis==='config_aware'?'Из конфигурации':f.basis==='observed'?'Наблюдение':'Оценка'}</Badge></div><div className="equation"><span>{evidence.attempts}<small>попытки</small></span><b>×</b><span>{duration(evidence.timeout_ns)}<small>таймаут</small></span><b>+</b><span>{duration(evidence.backoff_total_ns)}<small>паузы</small></span><b>=</b><span className="text-danger">{duration(evidence.required_ns)}<small>требуется</small></span><div className="budget-value"><span>Доступный бюджет</span><strong>{duration(evidence.budget_ns)}</strong></div></div><div className="evidence-source">{evidence.source}, ревизия {evidence.config_revision}, уверенность: {f.confidence==='high'?'высокая':f.confidence==='medium'?'средняя':'низкая'}</div></div> : <p className="evidence-source">{evidence.source}</p>}<h3>Что проверить перед изменением</h3><ul className="risk-list">{f.recommendation.risks.map(r=><li key={r}>{r}</li>)}</ul></div>:<div className="example-links">{f.exemplars.map(ref=><a className="mono" key={traceHref(ref)} href={traceHref(ref)}>{shortId(ref.trace_id)}<Icon name="external" size={12}/></a>)}</div>}
  </div></Panel>;
}
export function Audit({audit,onPlan,pending}:{audit:AuditResult|null;onPlan:(f:Finding)=>void;pending:boolean}) {
  const [tab,setTab]=useState<'findings'|'rules'>('findings');
  return <><div className="page-title"><div><h1>Аудит</h1></div><Badge tone="accent">Находок: {audit?.findings.length??0}</Badge></div><Tabs label="Разделы аудита" value={tab} onChange={setTab} items={[{value:'findings',label:'Находки'},{value:'rules',label:'Проверки'}]}/>{tab==='findings'?<div className="audit-list">{audit?.findings.length?audit.findings.map(f=><FindingCard key={f.finding_id} finding={f} onPlan={onPlan} pending={pending}/>):<Panel><EmptyState icon="audit" title="Находок нет" description="В полученном результате нет находок. Проверьте покрытие правил на вкладке «Проверки»."/></Panel>}</div>:<Panel title="Покрытие правил"><div className="rule-list">{audit?.rule_evaluations.map(r=><div className="rule-item" key={r.rule_id}><span className="mono">{r.rule_id}</span><Badge tone={r.status==='evaluated'?'success':'neutral'}>{r.status==='evaluated'?'Проверено':r.status==='not_implemented'?'Позже':r.status==='not_applicable'?'Неприменимо':'Мало данных'}</Badge>{r.status==='evaluated'&&<small>{r.eligible_count} трасс</small>}</div>)}</div></Panel>}</>;
}
