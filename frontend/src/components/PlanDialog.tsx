import { useEffect, useRef, useState } from 'react';
import type { ChangePlan, Client, Finding } from '../api/types';
import { Badge, Button, Icon, Message } from './ui';

export function PlanDialog({ plan, finding, demo, canApply, client, onApplied, onClose }: { plan: ChangePlan; finding: Finding; demo: boolean; canApply: boolean; client: Client; onApplied: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const requestId = useRef(crypto.randomUUID());
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  useEffect(() => { const dialog = ref.current; dialog?.showModal(); return () => dialog?.close(); }, []);
  async function apply() {
    setBusy(true); setError('');
    try {
      const result = await client.applyPlan(plan.plan_id, plan.plan_hash, requestId.current);
      let entry = await client.getJournalEntryForEnvironment(result.operation_id, plan.environment_id ?? 'sandbox');
      for (let i = 0; i < 5 && entry.status === 'applying'; i++) { await new Promise(resolve => window.setTimeout(resolve, 400)); entry = await client.getJournalEntryForEnvironment(result.operation_id, plan.environment_id ?? 'sandbox'); }
      onApplied();
      if (['applied', 'no_op'].includes(entry.status)) onClose();
      else if (['rejected', 'failed'].includes(entry.status)) setError(`Сервер завершил операцию со статусом «${entry.status}». Подробности доступны в журнале.`);
      else if (entry.status === 'unknown') setError('Сервер не подтвердил результат. Проверьте журнал; повтор использует тот же request_id.');
      else if (entry.status === 'applying') setError('Операция ещё выполняется. Её результат появится в журнале.');
      else if (result.status === 'applied' || result.status === 'no_op') onClose();
      else setError(`Операция завершилась со статусом «${result.status}». Проверьте журнал.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось применить план.'); }
    finally { setBusy(false); }
  }
  return <dialog className="plan-dialog" ref={ref} onCancel={e => { e.preventDefault(); onClose(); }} onClick={e => { if (e.target === ref.current && !busy) onClose(); }} aria-labelledby="plan-title"><div className="dialog-content"><header><div><div className="eyebrow">{finding.rule_id}, {demo ? 'Демонстрационный план' : 'План от сервера'}</div><h2 id="plan-title">План изменения настроек</h2></div><Button variant="ghost" aria-label="Закрыть план" icon="close" onClick={onClose} disabled={busy}/></header><div className="dialog-body">{error ? <Message tone="warning">{error}</Message> : <Message>{canApply ? 'Проверьте значения и подтвердите применение к стенду.' : 'Предпросмотр. Применение доступно только для разрешённого серверного плана в sandbox.'}</Message>}<div className="plan-target"><div><span>Цель</span><strong>{plan.target}</strong></div><Badge tone="accent">Ревизия {plan.expected_revision}</Badge></div><div className="table-scroll plan-table"><table><thead><tr><th>Параметр</th><th>Сейчас</th><th>Предлагается</th></tr></thead><tbody>{plan.changes.map(c => <tr key={c.field}><td>{c.field}</td><td className="muted">{c.before}</td><td className="proposed-value">{c.after}</td></tr>)}</tbody></table></div><div className="plan-rationale"><h3>Почему это предлагается</h3><p>{finding.recommendation.why}</p><h3>Что проверить перед применением</h3><ul>{finding.recommendation.risks.map(r => <li key={r}>{r}</li>)}</ul></div>{plan.warnings.map(w => <p className="plan-warning" key={w}><Icon name="info" size={14}/>{w}</p>)}<dl className="plan-metadata"><div><dt>Plan ID</dt><dd className="mono">{plan.plan_id}</dd></div><div><dt>Hash</dt><dd className="mono">{plan.plan_hash}</dd></div></dl></div><footer><span><Icon name="info" size={14}/>{canApply ? 'Операция и фактический результат будут записаны в журнал.' : 'Информационная рекомендация.'}</span><div className="lab-actions"><Button onClick={onClose} disabled={busy}>Закрыть</Button>{canApply && <Button variant="primary" onClick={() => void apply()} disabled={busy || !plan.apply_enabled}>{busy ? 'Применяем…' : 'Применить'}</Button>}</div></footer></div></dialog>;
}
