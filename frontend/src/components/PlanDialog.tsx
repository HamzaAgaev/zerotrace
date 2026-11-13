import { changeValue, timingText, fieldLabel } from '../presentation';
import { HttpOperation } from './HttpText';
import { useEffect, useId, useRef, useState } from 'react';
import { ApiError } from '../api/client';
import type { ChangePlan, Client, Finding, JournalEntry } from '../api/types';
import { RuleLabel } from './RuleLabel';
import { Button, Message, TableScroll } from './ui';

function targetParts(target: string) {
  const index = target.indexOf(' / ');
  if (index < 0) return { service: '', operation: target };
  return { service: target.slice(0, index), operation: target.slice(index + 3) };
}

export function PlanDialog({ plan, finding, demo = false, localOnly = false, canApply, client, onApplied, onStaleRecommendation, onClose, showRuleInfo = true }: {
  plan: ChangePlan;
  finding?: Finding;
  showRuleInfo?: boolean;
  demo?: boolean;
  localOnly?: boolean;
  canApply: boolean;
  client: Client;
  onApplied: (entry: JournalEntry | null) => void;
  onStaleRecommendation?: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const requestId = useRef<string | null>(null);
  const inFlight = useRef(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { service, operation } = targetParts(plan.target);
  const applyBlocked = demo
    ? 'Демонстрационный план · только просмотр'
    : !canApply
      ? 'Применение недоступно: подключите сервер и выберите окружение sandbox.'
      : !plan.apply_enabled
        ? 'Сервер не разрешает применить этот план. Причина указана в предупреждениях.'
        : '';

  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);

  function close() {
    if (!inFlight.current) onClose();
  }

  async function apply() {
    if (inFlight.current || !canApply || !plan.apply_enabled) return;
    inFlight.current = true;
    setBusy(true);
    setError('');
    requestId.current ??= crypto.randomUUID();
    try {
      const result = await client.applyPlan(plan.plan_id, plan.plan_hash, requestId.current);
      const environment = plan.environment_id ?? 'sandbox';
      let entry: JournalEntry | null = null;
      try {
        entry = await client.getJournalEntryForEnvironment(result.operation_id, environment);
        for (let attempt = 0; attempt < 5 && entry.status === 'applying'; attempt++) {
          await new Promise(resolve => window.setTimeout(resolve, 400));
          entry = await client.getJournalEntryForEnvironment(result.operation_id, environment);
        }
      } catch {
        // The operation is still available in the server journal.
      }
      onApplied(entry);
      const status = entry?.status ?? result.status;
      switch (status) {
        case 'applied':
        case 'no_op':
          onClose();
          break;
        case 'applying':
        case 'planned':
          setError('Операция ещё выполняется. Результат появится в журнале.');
          break;
        case 'unknown':
          setError('Сервер пока не подтвердил результат. Проверьте статус в журнале.');
          break;
        default:
          setError('Сервер отклонил изменение или вернул ошибку. Подробности доступны в журнале.');
      }
    } catch (cause) {
      if (onStaleRecommendation && cause instanceof ApiError && cause.code === 'stale_recommendation') {
        onStaleRecommendation();
        onClose();
        return;
      }
      setError(cause instanceof Error ? cause.message : 'Не удалось применить план.');
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  }

  return <dialog ref={dialog} className="plan-dialog" aria-labelledby={titleId}
    onCancel={event => { event.preventDefault(); close(); }}
    onClick={event => { if (event.target === dialog.current) close(); }}>
    <div className="dialog-content">
      <header>
        <div>
          <h2 id={titleId} tabIndex={-1} autoFocus>План изменения настроек</h2>
          {finding && <div className="eyebrow"><RuleLabel id={finding.rule_id} version={finding.rule_version} showInfo={showRuleInfo} /></div>}
        </div>
      </header>
      <div className="dialog-body">
        {error && <Message tone="warning">{error}</Message>}
        <div className="plan-target">
          <div><span>Сервис</span><strong>{service || 'Не указан'}</strong></div>
          <div><span>Вызов</span><strong><HttpOperation operation={operation} /></strong></div>

        </div>
        <TableScroll className="plan-table">
          <table>
            <thead><tr><th scope="col">Параметр</th><th scope="col">Было</th><th scope="col">Станет</th></tr></thead>
            <tbody>{plan.changes.map((change, index) => <tr key={`${change.field}-${index}`}>
              <td>{fieldLabel(change.field)}</td>
              <td className="muted">{changeValue(change.field, change.before)}</td>
              <td className="proposed-value">{changeValue(change.field, change.after)}</td>
            </tr>)}</tbody>
          </table>
        </TableScroll>
        {(!demo || plan.environment_id) && <dl className="plan-metadata">
          {!demo && <div><dt>Версия настроек</dt><dd>{plan.expected_revision}</dd></div>}
          {plan.environment_id && <div><dt>Окружение</dt><dd>{plan.environment_id}</dd></div>}
          {!demo && <div><dt>Действует до</dt><dd>{new Date(plan.expires_at).toLocaleString('ru-RU')}</dd></div>}
        </dl>}
        {finding && <div className="plan-rationale">
          <h3>Почему это предлагается</h3>
          <p>{timingText(finding.recommendation.why)}</p>
          {finding.recommendation.risks.length > 0 && <>
            <h3>Что проверить перед применением</h3>
            <ul>{finding.recommendation.risks.map(risk => <li key={risk}>{timingText(risk)}</li>)}</ul>
          </>}
        </div>}
        {plan.warnings.map(warning => <p className="plan-warning" key={warning}>{timingText(warning)}</p>)}
      </div>
      <footer>
        <span>{applyBlocked || (localOnly ? 'Изменения сохранятся только в браузере.' : 'Изменения будут применены на сервере.')}</span>
        <div className="page-actions">
          <Button onClick={close} disabled={busy}>Закрыть</Button>
          {canApply && <Button variant="primary" loading={busy} onClick={() => apply()} disabled={busy || !plan.apply_enabled} title={!plan.apply_enabled ? 'Сервер не разрешает применить этот план' : undefined}>
            {busy ? 'Применяем…' : 'Применить'}
          </Button>}
        </div>
      </footer>
    </div>
  </dialog>;
}
