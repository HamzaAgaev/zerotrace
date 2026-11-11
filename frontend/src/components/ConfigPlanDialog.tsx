import { useEffect, useRef, useState } from 'react';
import type { ChangePlan, Client, JournalEntry } from '../api/types';
import { Badge, Button, Icon, Message } from './ui';

export function ConfigPlanDialog({ client, plan, onClose, onApplied }: { client: Client; plan: ChangePlan; onClose: () => void; onApplied: (entry: JournalEntry | null) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const requestId = useRef(crypto.randomUUID());
  useEffect(() => { dialog.current?.showModal(); return () => dialog.current?.close(); }, []);
  async function apply() {
    setBusy(true); setError('');
    try {
      const result = await client.applyPlan(plan.plan_id, plan.plan_hash, requestId.current);
      let entry: JournalEntry | null = null;
      try {
        entry = await client.getJournalEntryForEnvironment(result.operation_id, plan.environment_id ?? 'sandbox');
        for (let i = 0; i < 5 && entry.status === 'applying'; i++) {
          await new Promise(resolve => window.setTimeout(resolve, 400));
          entry = await client.getJournalEntryForEnvironment(result.operation_id, plan.environment_id ?? 'sandbox');
        }
      } catch { /* The operation is retained by the server and can be refreshed in the journal. */ }
      onApplied(entry);
      if (entry && ['applied', 'no_op'].includes(entry.status)) onClose();
      else if (entry && ['rejected', 'failed'].includes(entry.status)) setError(`Сервер завершил операцию со статусом «${entry.status}». Подробности доступны в журнале.`);
      else if (entry?.status === 'unknown') setError('Сервер не смог подтвердить результат. Проверьте операцию в журнале; повтор использует тот же request_id.');
      else if (entry?.status === 'applying' || (!entry && result.status === 'applying')) setError('Изменение принято и ещё выполняется. Результат появится в журнале после проверки сервиса.');
      else if (result.status === 'applied' || result.status === 'no_op') onClose();
      else setError(`Операция завершилась со статусом «${result.status}». Проверьте журнал.`);
    } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось применить изменения.'); }
    finally { setBusy(false); }
  }
  return <dialog className="plan-dialog" ref={dialog} onCancel={event => { event.preventDefault(); onClose(); }} onClick={event => { if (event.target === dialog.current) onClose(); }} aria-labelledby="config-plan-title"><div className="dialog-content"><header><div><div className="eyebrow">План от сервера</div><h2 id="config-plan-title">Подтвердить изменение</h2></div><Button variant="ghost" aria-label="Закрыть план" icon="close" onClick={onClose}/></header><div className="dialog-body">{error && <Message tone={error.includes('принято') ? 'warning' : 'danger'}>{error}</Message>}<div className="plan-target"><div><span>Цель</span><strong>{plan.target}</strong></div><Badge tone="accent">Ревизия {plan.expected_revision}</Badge></div><div className="table-scroll plan-table"><table><thead><tr><th>Параметр</th><th>Сейчас</th><th>Будет</th></tr></thead><tbody>{plan.changes.map(change => <tr key={change.field}><td>{change.field}</td><td className="muted">{change.before}</td><td className="proposed-value">{change.after}</td></tr>)}</tbody></table></div>{plan.warnings.map(warning => <p className="plan-warning" key={warning}><Icon name="info" size={14}/>{warning}</p>)}</div><footer><span>Изменение вступит в силу для новых входящих запросов.</span><div className="lab-actions"><Button onClick={onClose} disabled={busy}>Отмена</Button><Button variant="primary" onClick={apply} disabled={busy || !plan.apply_enabled}>{busy ? 'Применяем…' : 'Применить'}</Button></div></footer></div></dialog>;
}
