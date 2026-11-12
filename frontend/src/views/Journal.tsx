import { fieldLabel, journalStatusLabel, verificationLabel } from '../presentation';
import { HttpTarget } from '../components/HttpText';
import type { JournalEntry } from '../api/types';
import { Badge, EmptyState, Panel, PageHeader, TableScroll } from '../components/ui';

const statusTone = (status: JournalEntry['status']) =>
  status === 'applied' || status === 'no_op' ? 'success' : status === 'failed' || status === 'rejected' ? 'danger' : 'warning';

export function Journal({ entries }: { entries: JournalEntry[] }) {
  return <>
    <PageHeader title="Журнал изменений" detail={<p>Операции применения и фактические значения, подтверждённые сервером.</p>} />
    <Panel>
      {entries.length ? <TableScroll>
        <table>
          <thead><tr>
            <th>Операция / время</th>
            <th>Цель и изменения</th>
            <th>Статус изменения</th>
            <th>Проверка проблемы</th>
          </tr></thead>
          <tbody>{entries.map(entry => <tr key={entry.operation_id}>
            <td className="mono">{entry.operation_id}<small className="cell-note">{new Date(entry.requested_at).toLocaleString('ru-RU')}</small></td>
            <td>
              <HttpTarget target={entry.target} />
              {entry.actual_changes.length > 0 ? <>
                <ul className="journal-changes">{entry.actual_changes.map((change, index) => <li key={`${change.field}-${index}`}>
                  <span>{fieldLabel(change.field)}</span>: <code>{change.before}</code> → <code>{change.after}</code>
                </li>)}</ul>
                <details className="cell-details">
                  <summary>Технические названия полей</summary>
                  <ul>{entry.actual_changes.map((change, index) => <li key={`${change.field}-${index}`}>
                    <code>{change.field}</code>: {change.before} → {change.after}
                  </li>)}</ul>
                </details>
              </> : <small className="cell-note">Сервер не сообщил фактические изменения.</small>}
            </td>
            <td>
              <Badge tone={statusTone(entry.status)}>{journalStatusLabel(entry.status)}</Badge>
              {entry.status === 'unknown' && <small className="cell-note">Не считайте изменение успешным до подтверждения сервером.</small>}
            </td>
            <td>
              {verificationLabel(entry.verification)}
              {entry.verification === 'symptom_absent' && <small className="cell-note">Это результат проверки проблемы, а не статус применения настроек.</small>}
            </td>
          </tr>)}</tbody>
        </table>
      </TableScroll> : <EmptyState icon="journal" title="Изменений пока нет" description="Подтверждённые операции появятся здесь после применения плана."
        action={<a className="button button--secondary" href="#/audit">Перейти к аудиту</a>} />}
    </Panel>
  </>;
}
