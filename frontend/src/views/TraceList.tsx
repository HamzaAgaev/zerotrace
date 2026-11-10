import { useMemo, useState } from 'react';
import type { TraceSummary } from '../api/types';
import { Badge, Button, EmptyState, Icon, Panel } from '../components/ui';
import { MetricInfo, Metrics } from '../components/Metrics';
import { duration, filterTraces, ms, traceHref, shortId, time } from '../lib';
export function TraceList({ traces, total, refresh, busy }: { traces: TraceSummary[]; total: number; refresh: () => void; busy: boolean }) {
  const [query, setQuery] = useState(''); const [service, setService] = useState(''); const [status, setStatus] = useState(''); const [sort, setSort] = useState('recent'); const [page, setPage] = useState(1);
  const filtered = useMemo(() => filterTraces(traces, query, service, status, sort), [traces, query, service, status, sort]);
  const pages = Math.max(1, Math.ceil(filtered.length / 10)); const current = Math.min(page, pages); const rows = filtered.slice((current - 1) * 10, current * 10);
  const change = (setter: (v: string) => void, value: string) => { setter(value); setPage(1); };
  const outcomes = { ok: 0, error: 0, unknown: 0 };
  for (const trace of traces) outcomes[trace.outcome]++;
  return <>
    <div className="page-title"><div><h1>Трассы</h1></div><Button icon="refresh" onClick={refresh} disabled={busy}>{busy ? 'Обновляем…' : 'Обновить'}</Button></div>
    <div className="metric-section">
      <Metrics items={[
        { label: 'Загружено', value: total > traces.length ? `${traces.length} / ${total}` : traces.length },
        { label: 'Успешные', value: outcomes.ok, tone: 'success' },
        { label: 'С ошибкой', value: outcomes.error, tone: 'danger' },
        { label: 'Исход неизвестен', value: outcomes.unknown },
      ]}/>
      <MetricInfo label="Как считаются исходы трасс">
        Показатели относятся ко всем загруженным трассам. Исход определяется по входному запросу. Отдельные вызовы внутри успешной трассы могут завершиться ошибкой.
      </MetricInfo>
    </div>
    <div className="filters"><label className="search-field"><Icon name="search"/><input aria-label="Поиск трасс" type="search" value={query} onChange={e => change(setQuery, e.target.value)} placeholder="Операция, сервис или Trace ID"/><kbd>⌕</kbd></label><select aria-label="Сервис" value={service} onChange={e => change(setService, e.target.value)}><option value="">Все сервисы</option>{[...new Set(traces.map(t => t.service))].map(s => <option key={s}>{s}</option>)}</select><select aria-label="Исход запроса" value={status} onChange={e => change(setStatus, e.target.value)}><option value="">Любой исход</option><option value="error">С ошибкой</option><option value="ok">Успешные</option><option value="unknown">Неизвестный</option></select><select aria-label="Сортировка" value={sort} onChange={e => change(setSort, e.target.value)}><option value="recent">Сначала новые</option><option value="slow">Сначала медленные</option></select></div>
    <Panel className="trace-table-panel"><div className="table-scroll"><table className="trace-table"><thead><tr><th>Входная операция</th><th>Сервис</th><th>Длительность</th><th>Исход</th><th>Аудит</th><th>Начало</th><th><span className="sr-only">Открыть</span></th></tr></thead><tbody>{rows.map(t => <tr key={t.trace_id} onClick={e => { if (!(e.target as HTMLElement).closest('a')) window.location.hash = traceHref(t.trace_ref); }}><td><a className="trace-operation" href={traceHref(t.trace_ref)}><span className={`method ${t.operation.startsWith('GET') ? 'method--get' : ''}`}>{t.operation.split(' ')[0]}</span>{t.operation.split(' ').slice(1).join(' ')}</a><span className="mono trace-id">{shortId(t.trace_id)}</span></td><td><span className="service-name"><i/>{t.service}</span><small className="cell-note">{t.span_count} спанов, {t.service_count} сервиса</small></td><td><div className="duration-value">{duration(t.duration_ns)}<span className="duration-track"><i style={{ width: `${Math.min(100, (ms(t.duration_ns) ?? 0) / 25)}%` }}/></span></div></td><td><Badge tone={t.outcome === 'error' ? 'danger' : t.outcome === 'ok' ? 'success' : 'neutral'} dot>{t.http_status ?? 'Неизвестно'}</Badge></td><td>{t.finding_count ? <span className="finding-count"><i/>{t.finding_count} находка</span> : <span className="quiet">—</span>}</td><td className="tabular muted">{time(t.start_ns)}</td><td><Icon name="chevron" size={15}/></td></tr>)}</tbody></table></div>{!rows.length && <EmptyState title="Трассы не найдены" description={traces.length ? 'Попробуйте изменить запрос или сбросить фильтры.' : 'Подключите источник или откройте демонстрационный набор.'} action={traces.length > 0 && <Button onClick={() => { setQuery(''); setService(''); setStatus(''); setPage(1); }}>Сбросить фильтры</Button>}/>}<div className="table-footer"><span>{filtered.length ? `${(current - 1) * 10 + 1}–${Math.min(current * 10, filtered.length)} из ${filtered.length}` : '0 результатов'}{total > traces.length && `, загружено ${traces.length} из ${total}`}</span><div className="pagination"><Button variant="ghost" aria-label="Предыдущая страница" onClick={() => setPage(current - 1)} disabled={current <= 1}><Icon name="chevron" className="rotate" size={14}/></Button><span>{current} / {pages}</span><Button variant="ghost" aria-label="Следующая страница" onClick={() => setPage(current + 1)} disabled={current >= pages}><Icon name="chevron" size={14}/></Button></div></div></Panel>

  </>;
}
