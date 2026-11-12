import { usePersistentState } from '../state';
import { HttpMethod, HttpStatus } from '../components/HttpText';
import { useEffect, useRef, useState } from 'react';
import type { FormEvent } from 'react';
import type { Client, ListPayload, TraceQuery } from '../api/types';
import { isUncertainResponse } from '../api/client';
import { successWindowsMinutes } from '../api/types';
import { Metrics } from '../components/Metrics';
import { Select } from '../components/Select';
import { Badge, Button, EmptyState, Field, Icon, LoadingState, Message, PageHeader, Panel, TableScroll, RetryButton } from '../components/ui';
import { duration, outcomeLabels, traceHref, traceRequest } from '../lib';
import { formatCount, formatPercent } from '../presentation';

type TracePage = {
  client: Client; data: ListPayload; page: number; limit: number;
  findings?: Map<string, number>;
};
const successLabels = { 5: 'Успех за 5 минут', 30: 'Успех за 30 минут', 60: 'Успех за 1 час', 180: 'Успех за 3 часа' };
const formatStart = new Intl.DateTimeFormat('ru-RU', { dateStyle: 'short', timeStyle: 'medium' });

function boundaryNs(value: string): string | undefined {
  if (!value) return undefined;
  const ms = new Date(value).getTime();
  if (!Number.isSafeInteger(ms) || ms < 0 || ms > 9223372036854) {
    throw new Error('Укажите корректную дату и время.');
  }
  return (BigInt(ms) * 1_000_000n).toString();
}

function traceStart(value: string): string {
  const ns = BigInt(value);
  const date = new Date(Number(ns / 1_000_000n));
  return ns === 0n || Number.isNaN(date.getTime()) ? 'Неизвестно' : formatStart.format(date);
}

export function TraceList({ client }: { client: Client }) {
  const [query, setQuery] = usePersistentState('views/TraceList.tsx:query', '');
  const [service, setService] = usePersistentState('views/TraceList.tsx:service', '');
  const [status, setStatus] = usePersistentState('views/TraceList.tsx:status', '');
  const [method, setMethod] = usePersistentState('views/TraceList.tsx:method', '');
  const [sort, setSort] = usePersistentState('views/TraceList.tsx:sort', 'recent');
  const [periodDraft, setPeriodDraft] = usePersistentState('traces:period-draft', {span_id:'',period_start:'',period_end:''});
  const searchForm = useRef<HTMLFormElement>(null);
  const [appliedSearch, setAppliedSearch] = usePersistentState<Pick<TraceQuery, 'span_id' | 'start_from_ns' | 'start_to_ns'>>('traces:applied-search', {});
  const [searchError, setSearchError] = useState('');
  const [page, setPage] = usePersistentState('views/TraceList.tsx:page', 1);
  const [limit, setLimit] = usePersistentState('views/TraceList.tsx:limit', 10);
  const [reload, setReload] = useState(0);
  const [response, setResponse] = useState<TracePage | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState('');
  const [summaryError, setSummaryError] = useState(false);
  const requestedQuery = useRef('');
  const filtersActive = !!(query.trim() || service || status || method
    || appliedSearch.span_id || appliedSearch.start_from_ns || appliedSearch.start_to_ns);

  useEffect(() => {
    const controller = new AbortController();
    setBusy(true);
    setError('');
    setSummaryError(false);

    async function load() {
      requestedQuery.current = query;
      const filters = { query, service, status, method, sort, limit, offset: (page - 1) * limit, ...appliedSearch };
      const recentPage = sort === 'recent' && !filtersActive;
      const summaryQuery = { ...filters, includeFindings: false, includeSuccessRates: true };
      try {
        const data = await client.listTraces(controller.signal, recentPage
          ? { limit, offset: filters.offset, includeFindings: false }
          : summaryQuery);
        if (controller.signal.aborted) return;
        const lastPage = Math.max(1, Math.ceil(data.total / limit));
        if (page > lastPage) {
          setPage(lastPage);
          return;
        }
        let next: TracePage = { client, data, page, limit };
        setResponse(next);
        setBusy(false);
        if (recentPage) {
          try {
            const summary = await client.listTraces(controller.signal, summaryQuery).catch(cause => {
              if (!controller.signal.aborted && isUncertainResponse(cause)) {
                return client.listTraces(controller.signal, summaryQuery);
              }
              throw cause;
            });
            if (controller.signal.aborted) return;
            const first = next;
            const enriched = { ...first, data: {
              ...data, services: summary.services, methods: summary.methods,
              outcomes: summary.outcomes, success_rates: summary.success_rates,
            } };
            next = enriched;
            setResponse(current => current === first ? enriched : current);
          } catch {
            if (!controller.signal.aborted) setSummaryError(true);
            return;
          }
        }
        if (data.items.length) {
          void client.listTraces(controller.signal, filters).then(audited => {
            if (controller.signal.aborted) return;
            const findings = new Map(audited.items.map(item => [traceHref(item.trace_ref), item.finding_count]));
            setResponse(current => current === next ? { ...next, findings } : current);
          }).catch(() => {
            // The telemetry page is still available; omitted counts remain unknown.
          });
        }
      } catch (cause) {
        if (controller.signal.aborted) return;
        setError(cause instanceof Error ? cause.message : 'Не удалось загрузить трейсы.');
        setBusy(false);
      }
    }

    const timer = window.setTimeout(() => void load(), query === requestedQuery.current ? 0 : 250);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [client, query, service, status, method, sort, page, limit, reload, appliedSearch, filtersActive]);

  const displayed = response?.client === client ? response : null;
  const result = displayed?.data;
  const rows = result?.items ?? [];
  const total = result?.total ?? 0;
  const shownPage = displayed?.page ?? 1;
  const shownLimit = displayed?.limit ?? limit;
  const pages = Math.max(1, Math.ceil(total / shownLimit));
  const firstPageButton = Math.max(1, Math.min(shownPage - 2, pages - 4));
  const change = (setter: (value: string) => void, value: string) => {
    setter(value);
    setPage(1);
  };
  const refresh = () => setReload(value => value + 1);
  const resetFilters = () => {
    setQuery('');
    setService('');
    setStatus('');
    setMethod('');
    setPeriodDraft({span_id:'',period_start:'',period_end:''});
    setAppliedSearch({});
    setSearchError('');
    setPage(1);
  };
  const applySearch = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    try {
      const fields = new FormData(event.currentTarget);
      const spanId = String(fields.get('span_id') ?? '').trim().toLowerCase();
      if (spanId && (!/^[a-f0-9]{16}$/.test(spanId) || spanId === '0000000000000000')) {
        throw new Error('Span ID должен содержать 16 шестнадцатеричных символов и хотя бы один ненулевой символ.');
      }
      const from = boundaryNs(String(fields.get('period_start') ?? ''));
      const to = boundaryNs(String(fields.get('period_end') ?? ''));
      if (from !== undefined && to !== undefined && BigInt(from) >= BigInt(to)) {
        throw new Error('Начало периода должно быть раньше конца.');
      }
      setAppliedSearch({ span_id: spanId || undefined, start_from_ns: from, start_to_ns: to });
      setSearchError('');
      setPage(1);
    } catch (cause) {
      setSearchError(cause instanceof Error ? cause.message : 'Проверьте фильтры.');
    }
  };
  const count = (value: number | undefined) => formatCount(value);

  return <>
    <PageHeader title="Трейсы" actions={<><Button icon="refresh" onClick={refresh} disabled={busy}>Обновить</Button></>}/>
    <Metrics items={successWindowsMinutes.map(minutes => {
      const rate = result?.success_rates?.[minutes];
      const tone = rate == null ? 'neutral' : rate >= 0.99 ? 'success' : rate >= 0.95 ? 'warning' : 'danger';
      return { label: successLabels[minutes], value: formatPercent(rate), tone };
    })}/>
    {summaryError && <Message tone="warning" action={<RetryButton onClick={refresh}/>}>
      Не удалось загрузить долю успешных запросов и варианты фильтров.
    </Message>}

    <Panel className="trace-table-panel">
      <div className="trace-toolbar" role="group" aria-label="Фильтры трейсов">
        <label className="search-field">
          <Icon name="search" size={16}/>
          <input aria-label="Поиск трейсов" type="search" value={query}
            onChange={event => change(setQuery, event.target.value)} placeholder="Эндпоинт, сервис или Trace ID"/>
        </label>
        <Select aria-label="Сервис" value={service} onChange={value => change(setService, value)}>
          <option value="">Все сервисы</option>
          {(result?.services ?? []).map(name => <option key={name} value={name}>{name}</option>)}
        </Select>
        <Select aria-label="Метод запроса" value={method} onChange={value => change(setMethod, value)}
          disabled={!result?.methods?.length}>
          <option value="">Все методы</option>
          {(result?.methods ?? []).map(name => <option key={name} value={name} label={name}><HttpMethod method={name}/></option>)}
        </Select>
        <Select aria-label="Результат запроса" value={status} onChange={value => change(setStatus, value)}>
          <option value="">Все результаты</option>
          <option value="error">{outcomeLabels.error}</option>
          <option value="ok">{outcomeLabels.ok}</option>
          <option value="unknown">{outcomeLabels.unknown}</option>
        </Select>
        <Select aria-label="Сортировка" value={sort} onChange={value => change(setSort, value)}>
          <option value="recent">Сначала новые</option>
          <option value="slow">Сначала медленные</option>
        </Select>
        {filtersActive && <Button variant="ghost" onClick={resetFilters}>Сбросить</Button>}
      </div>

      <form ref={searchForm} className="trace-period-filter" aria-label="Фильтры по Span ID и периоду" onSubmit={applySearch}>
        <Field label="Span ID">
          <input type="search" name="span_id" value={periodDraft.span_id} onChange={e=>setPeriodDraft(old=>({...old,span_id:e.target.value}))} placeholder="16 шестнадцатеричных символов"/>
        </Field>
        <Field label="Начало периода">
          <input type="datetime-local" name="period_start" value={periodDraft.period_start} onChange={e=>setPeriodDraft(old=>({...old,period_start:e.target.value}))} step="1"/>
        </Field>
        <Field label="Конец периода">
          <input type="datetime-local" name="period_end" value={periodDraft.period_end} onChange={e=>setPeriodDraft(old=>({...old,period_end:e.target.value}))} step="1"/>
        </Field>
        <Button type="submit">Применить</Button>
        {searchError && <div className="trace-period-error" role="alert">{searchError}</div>}
      </form>

      {error && <div className="trace-list-error">
        <Message tone="danger" action={<RetryButton onClick={refresh}/>}>
          {error}{displayed && <p>Показаны последние загруженные результаты.</p>}
        </Message>
      </div>}

      <div aria-busy={busy}>
        {rows.length > 0 && <TableScroll><table className="trace-table" aria-label="Трейсы">
          <thead><tr>
            <th scope="col" className="trace-method-column">Метод</th><th scope="col">Эндпоинт</th>
            <th scope="col" className="trace-column-optional">Сервис</th><th scope="col" className="trace-result-column">Длитель&#173;ность</th>
            <th scope="col" className="trace-result-column">Резуль&#173;тат</th><th scope="col" className="trace-column-optional">Проблемы</th><th scope="col" className="trace-column-optional">Время запуска</th>
          </tr></thead>
          <tbody>{rows.map(trace => {
            const href = traceHref(trace.trace_ref);
            const { method: requestMethod, endpoint } = traceRequest(trace);
            const tone = trace.outcome === 'error' ? 'danger' : trace.outcome === 'ok' ? 'success' : 'neutral';
            const findingCount = displayed?.findings?.get(href);
            return <tr key={href} onClick={event => {
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
        </table></TableScroll>}
        {!rows.length && busy && <LoadingState>Загружаем трейсы…</LoadingState>}
        {!rows.length && !busy && !error && <EmptyState
          title={filtersActive ? 'Трейсы не найдены' : 'Пока нет трейсов'}
          description={filtersActive ? 'Попробуйте изменить запрос или фильтры.' : 'Здесь появятся запросы, полученные от источника.'}
          action={filtersActive && <Button onClick={resetFilters}>Сбросить фильтры</Button>}/>}
      </div>

      {displayed && <div className="table-footer trace-pagination">
        <span role="status">{busy ? 'Загружаем трейсы…' : total
          ? `${count((shownPage - 1) * shownLimit + 1)}–${count((shownPage - 1) * shownLimit + rows.length)} из ${count(total)}`
          : '0 результатов'}</span>
        <Select aria-label="Запросов на странице" value={String(limit)} onChange={value => { setLimit(Number(value)); setPage(1); }}>
          {[10, 25, 50].map(size => <option key={size} value={String(size)}>{size}</option>)}
        </Select>
        {pages > 1 && <nav className="pagination" aria-label="Страницы трейсов">
          <Button variant="ghost" aria-label="Предыдущая страница" onClick={() => setPage(shownPage - 1)} disabled={busy || shownPage <= 1}>
            <Icon name="arrow" className="rotate" size={16}/>
          </Button>
          {Array.from({ length: Math.min(5, pages) }, (_, index) => firstPageButton + index).map(number =>
            <Button key={number} variant={number === shownPage ? 'primary' : 'ghost'}
              aria-current={number === shownPage ? 'page' : undefined} disabled={busy} onClick={() => setPage(number)}>{number}</Button>
          )}
          <Button variant="ghost" aria-label="Следующая страница" onClick={() => setPage(shownPage + 1)} disabled={busy || shownPage >= pages}>
            <Icon name="arrow" size={16}/>
          </Button>
        </nav>}
      </div>}
    </Panel>
  </>;
}
