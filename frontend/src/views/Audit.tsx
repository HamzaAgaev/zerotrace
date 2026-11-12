import { Journal } from './Journal';
import { usePersistentState, usePersistentTab } from '../state';
import { ServiceCall } from '../components/HttpText';
import type { AuditResult, Finding, JournalEntry } from '../api/types';
import { ReadOnlyBlock, ReadOnlyValue, Badge, Button, EmptyState, Icon, Panel, TableScroll, PageHeader, LoadingState, Message, RetryButton } from '../components/ui';
import { Metrics } from '../components/Metrics';
import { RuleLabel, ruleName, ruleImpact } from '../components/RuleLabel';
import { Tabs } from '../components/Tabs';
import { duration, traceHref } from '../lib';

const severityLabel = { high: 'Высокая важность', medium: 'Средняя важность', low: 'Низкая важность' } as const;
const ruleStatusLabel = {
  evaluated: 'Проверено',
  not_implemented: 'Проверка пока недоступна',
  not_applicable: 'Неприменимо к этим данным',
  insufficient_data: 'Недостаточно данных',
} as const;

export function FindingCard({ finding: f, onPlan, pending }: { finding: Finding; onPlan: (finding: Finding) => void; pending: boolean }) {
  const evidence = f.evidence;
  const explanation = f.explanation.trim();
  const why = f.recommendation.why.trim();
  const [tab, setTab] = usePersistentState<'summary' | 'evidence' | 'traces'>(`finding:${f.occurrence_id}:tab`, 'summary');
  const whyDiffers = why && why !== explanation && why !== f.recommendation.text;
  return <Panel className="finding-panel">
    <div className="finding-top">
      <div className="finding-label">
        <Badge tone={f.severity === 'high' ? 'danger' : f.severity === 'medium' ? 'warning' : 'neutral'} dot>{severityLabel[f.severity]}</Badge>
        <RuleLabel id={f.rule_id} version={f.rule_version} showInfo={false} />
      </div>
    </div>
    <div className="finding-body">
      <ServiceCall call={f.edge} />
      <h2>{f.title}</h2>
      <Tabs label={`Детали проверки «${ruleName(f.rule_id)}»`} value={tab} onChange={setTab} fullWidth items={[
        { value: 'summary', label: 'Решение' },
        { value: 'evidence', label: 'Подробности' },
        { value: 'traces', label: 'Примеры трейсов' },
      ]} />
      {tab === 'summary' ? <div className="recommendation">
        <Metrics items={[
          { label: 'Затронуто трейсов', value: `${f.affected_count} из ${f.denominator_count}`, tone: 'warning' },
          ...(evidence.kind === 'retry_budget' ? [
            { label: 'Требуется на попытки', value: duration(evidence.required_ns), tone: 'danger' as const },
            { label: 'Доступное время ожидания', value: duration(evidence.budget_ns) },
          ] : []),
        ]} />
        <ReadOnlyBlock label="Решение">
          <ReadOnlyValue label="Что не так">{explanation || f.title}</ReadOnlyValue>
          <ReadOnlyValue label="На что влияет">{ruleImpact(f.rule_id)}</ReadOnlyValue>
          <ReadOnlyValue label="Что изменить"><p>{f.recommendation.text}</p>{whyDiffers && <p>{why}</p>}</ReadOnlyValue>
        </ReadOnlyBlock>
        {evidence.kind === 'retry_budget' && f.recommendation.can_prepare_plan !== false && <div className="recommendation-bottom">
          <Button variant="primary" icon="external" disabled={pending} onClick={() => onPlan(f)}>{pending ? 'Готовим план…' : 'Посмотреть план'}</Button>
        </div>}
      </div> : tab === 'evidence' ? <div className="finding-evidence">
        <dl className="technical-grid">
          <div><dt>Код проверки</dt><dd className="mono">{f.rule_id}</dd></div>
        </dl>
        {evidence.kind === 'retry_budget' && <div className="evidence">
          <div className="evidence-heading"><span>Расчёт времени попыток</span></div>
          <div className="equation">
            <span>{evidence.attempts}<small>попыток, включая первую</small></span><b>×</b>
            <span>{duration(evidence.timeout_ns)}<small>таймаут попытки</small></span><b>+</b>
            <span>{duration(evidence.backoff_total_ns)}<small>паузы</small></span><b>=</b>
            <span className="text-danger">{duration(evidence.required_ns)}<small>требуется</small></span>
            <div className="budget-value"><span>Доступное время ожидания</span><strong>{duration(evidence.budget_ns)}</strong></div>
          </div>
        </div>}
        {f.recommendation.risks.length > 0 && <div>
          <h3>Что проверить перед изменением</h3>
          <ul className="risk-list">{f.recommendation.risks.map(risk => <li key={risk}>{risk}</li>)}</ul>
        </div>}
      </div> : <div className="example-links">
        {f.exemplars.map(ref => {
          const href = traceHref(ref);
          return <a className="button" key={href} href={href}>
            <span className="tabular">{ref.trace_id}</span>
            <Icon name="arrow" size={16} />
          </a>;
        })}
      </div>}
    </div>
  </Panel>;
}

export function Audit({ audit, onPlan, pending, auditLoading, auditError, journal, journalLoading, journalError, onRetry }: { audit: AuditResult | null; onPlan: (finding: Finding) => void; pending: boolean; auditLoading:boolean; auditError:string; journal:JournalEntry[]; journalLoading:boolean; journalError:string; onRetry:()=>void }) {
  const [tab, setTab] = usePersistentTab('audit:tab', 'findings', ['findings', 'rules', 'history']);
  const rules = audit?.rule_evaluations ?? [];
  const evaluated = rules.filter(rule => rule.status === 'evaluated');
  const limited = rules.filter(rule => rule.status === 'insufficient_data');
  const unavailable = rules.filter(rule => rule.status === 'not_implemented');
  const noProblems = audit
    ? evaluated.length === 0 && unavailable.length > 0
      ? { title: 'Проверка пока недоступна', description: 'Ни одна проверка ещё не может быть выполнена на этих данных.' }
      : limited.length > 0
        ? { title: 'Проблем не обнаружено, но данных недостаточно', description: `Часть проверок не смогла оценить данные: ${limited.length}. Проблемы могут быть не видны.` }
        : evaluated.length === 0
          ? { title: 'Недостаточно данных', description: 'Проверки не получили трейсов, пригодных для оценки. Проблемы не подтверждены и не опровергнуты.' }
          : { title: 'Проблем не обнаружено', description: '' }
    : { title: 'Проверка пока недоступна', description: 'Результат аудита ещё не получен. Повторите загрузку.' };

  return <>
    <PageHeader title="Аудит" />
    <Tabs label="Разделы аудита" value={tab} onChange={setTab} items={[
      { value: 'findings', label: 'Проблемы' },
      { value: 'rules', label: 'Проверки' },
      { value: 'history', label: 'История изменений' },
    ]} />
    {tab === 'history' ? journalLoading ? <LoadingState>Загружаем историю…</LoadingState> : journalError ? <Message tone="danger" action={<RetryButton onClick={onRetry}/>}>{journalError}</Message> : <Journal entries={journal}/> : auditLoading ? <LoadingState>Проверяем трейсы…</LoadingState> : auditError ? <Message tone="danger" action={<RetryButton onClick={onRetry}/>}>{auditError}</Message> : tab === 'findings' ? <div className="audit-list">
      {audit?.findings.length
        ? audit.findings.map(finding => <FindingCard key={finding.finding_id} finding={finding} onPlan={onPlan} pending={pending} />)
        : <Panel><EmptyState icon="audit" title={noProblems.title} description={noProblems.description}
           /></Panel>}
    </div> : <Panel title="Проверки">
      {rules.length ? <TableScroll><table><thead><tr><th>Проверка</th><th>Результат</th><th>Трейсов для проверки</th></tr></thead><tbody>{rules.map(rule => <tr key={rule.rule_id}>
        <td><RuleLabel id={rule.rule_id}/></td>
        <td><Badge tone={rule.status === 'evaluated' ? 'success' : rule.status === 'not_implemented' || rule.status === 'insufficient_data' ? 'warning' : 'neutral'}>{ruleStatusLabel[rule.status]}</Badge></td>
        <td>{rule.status === 'evaluated' ? rule.eligible_count : '—'}</td>
      </tr>)}</tbody></table></TableScroll> : <p className="panel-note">Список проверок ещё не получен.</p>}
    </Panel>}
  </>;
}
