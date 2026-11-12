import type { TraceSummary } from '../api/types';
import { HttpMethod, HttpStatus } from './HttpText';
import { Badge, TableScroll } from './ui';
import { duration, outcomeLabels, traceHref, traceRequest } from '../lib';

const formatStart = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'medium' });

function traceStart(value: string): string {
  const ns = BigInt(value);
  const date = new Date(Number(ns / 1_000_000n));
  return ns === 0n || Number.isNaN(date.getTime()) ? 'Неизвестно' : formatStart.format(date);
}

export function TraceTable({rows,findings}:{rows:TraceSummary[];findings?:Map<string,number>}) {
  return <TableScroll><table className="trace-table" aria-label="Трейсы">
          <thead><tr>
            <th scope="col" className="trace-method-column">Метод</th><th scope="col">Эндпоинт</th>
            <th scope="col" className="trace-column-optional">Сервис</th><th scope="col" className="trace-result-column">Длитель&#173;ность</th>
            <th scope="col" className="trace-result-column">Резуль&#173;тат</th><th scope="col" className="trace-column-optional">Проблемы</th><th scope="col" className="trace-column-optional">Время запуска</th>
          </tr></thead>
          <tbody>{rows.map(trace => {
            const href = traceHref(trace.trace_ref);
            const { method: requestMethod, endpoint } = traceRequest(trace);
            const tone = trace.outcome === 'error' ? 'danger' : trace.outcome === 'ok' ? 'success' : 'neutral';
            const findingCount = findings?.get(href);
            return <tr className="is-clickable" key={href} onClick={event => {
              if (!(event.target as Element).closest('a') && !window.getSelection()?.toString()) window.location.href = href;
            }}>
              <td className="trace-method-column">{requestMethod
                ? <HttpMethod method={requestMethod}/>
                : <span className="quiet">—</span>}</td>
              <td>
                <a className="trace-operation" href={href} title={endpoint}><span className="trace-method-inline">{requestMethod}</span><span>{endpoint}</span></a>
                <small className="trace-service-mobile">{trace.service}</small>
                <small className="mono trace-id">{trace.trace_id}</small>
              </td>
              <td className="trace-column-optional">{trace.service}</td>
              <td className="tabular">{duration(trace.duration_ns)}</td>
              <td>{trace.http_status === null ? <Badge tone={tone}>{outcomeLabels[trace.outcome]}</Badge> : <HttpStatus code={trace.http_status}/>}</td>
              <td className="trace-column-optional">{findingCount ? <Badge tone="warning">{findingCount}</Badge> : <span className="quiet" title={findingCount === undefined ? 'Нет данных аудита для этой версии трейса' : 'Аудит не обнаружил проблем'}>—</span>}</td>
              <td className="trace-column-optional tabular muted">{traceStart(trace.start_ns)}</td>
            </tr>;
          })}</tbody>
        </table></TableScroll>;
}
