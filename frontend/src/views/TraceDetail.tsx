import { CopyId } from '../components/CopyId';
import { usePersistentState, usePersistentTab } from '../state';
import { HttpMethod, HttpStatus, SpanOperation, httpCodeLabel } from '../components/HttpText';
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import type { AuditResult, Finding, TraceDetail as TraceDetailData } from '../api/types';
import { Metrics } from '../components/Metrics';
import { Tabs } from '../components/Tabs';
import { Button, Icon, LoadingState, Message, Panel, PageHeader, RetryButton } from '../components/ui';
import { WhatIf } from '../components/WhatIf';
import { compactTimelineSpans, duration, findingsForTrace, ms, outcomeLabels, timelineRows, traceRequest, traceServiceColors } from '../lib';
import { FindingCard } from './Audit';

const axisNumber = new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 });
const maxTimelineZoom = 8;

export function BackToTraces() {
  return <a href="#/traces" className="button button--primary trace-header-back">
    <Icon name="arrow" size={16} className="rotate"/>К списку трейсов
  </a>;
}


export function TraceDetail({ trace, audit, auditLoading, auditError, onRetryAudit, demo, onPlan, pending, onExport, onBuild }: {
  trace: TraceDetailData;
  audit: AuditResult | null;
  auditLoading: boolean;
  auditError: string;
  onRetryAudit: () => void;
  demo: boolean;
  onPlan: (finding: Finding) => void;
  pending: boolean;
  onExport: () => void;
  onBuild?: () => void;
}) {
  const [tab, setTab] = usePersistentTab(`trace:${JSON.stringify(trace.trace_ref)}:tab`, 'timeline', ['timeline','audit','whatif']);
  const [selected, setSelected] = usePersistentState<string | undefined>(`trace:${JSON.stringify(trace.trace_ref)}:span`, undefined);
  const [collapsedIds, setCollapsedIds] = usePersistentState<string[]>(`trace:${JSON.stringify(trace.trace_ref)}:collapsed`, []);
  const collapsed = new Set(collapsedIds);
  const setCollapsed = (action: Set<string> | ((old: Set<string>) => Set<string>)) => setCollapsedIds(old => [...(typeof action === 'function' ? action(new Set(old)) : action)]);
  const [zoom, setZoom] = usePersistentState(`trace:${JSON.stringify(trace.trace_ref)}:zoom`, 1);
  const [showAllSpans, setShowAllSpans] = usePersistentState(`trace:${JSON.stringify(trace.trace_ref)}:all`, false);
  const [baseRulerWidth, setBaseRulerWidth] = useState(0);
  const timelineScroll = useRef<HTMLDivElement>(null);
  const timelineRuler = useRef<HTMLDivElement>(null);
  const zoomAnchor = useRef<number | null>(null);
  const { summary, spans } = trace;
  const { method, endpoint } = traceRequest(summary);
  const findings = findingsForTrace(trace, audit?.findings ?? []);
  const services = useMemo(() => [...new Set(spans.map(span => span.service))], [spans]);
  const colors = useMemo(() => traceServiceColors(services), [services]);
  const compactSpans = useMemo(() => compactTimelineSpans(spans, trace.compactable_client_spans), [spans, trace.compactable_client_spans]);
  const timelineSpans = showAllSpans ? spans : compactSpans;
  const tree = useMemo(() => timelineRows(timelineSpans), [timelineSpans]);
  const parents = useMemo(() => new Set(timelineSpans.map(span => span.parent_span_id)), [timelineSpans]);
  const rows = useMemo(() => {
    const hidden = new Set<string>();
    return tree.filter(({ span }) => {
      const parent = span.parent_span_id;
      if (!parent || (!collapsed.has(parent) && !hidden.has(parent))) return true;
      hidden.add(span.span_id);
      return false;
    });
  }, [tree, collapsed]);
  const selectedSpan = rows.find(({ span }) => span.span_id === selected)?.span ?? rows[0]?.span;
  const total = spans.reduce((max, span) => Math.max(max,
    (ms(span.start_offset_ns) ?? 0) + (ms(span.duration_ns) ?? 0)), 0);
  const scale = total || 1;
  const axisInMicroseconds = total > 0 && total < 1;
  const tickCount = zoom * 4;
  const zoomExtraWidth = baseRulerWidth * (zoom - 1);
  const axisTicks = total ? Array.from({ length: tickCount + 1 }, (_, index) => index / tickCount) : [0];
  const traceAuditEmpty = (() => {
    if (!audit) return { title: 'Проверка пока недоступна', description: 'Результат аудита ещё не получен.' };
    if (trace.finding_occurrence_ids === undefined && !demo) {
      return { title: 'Результаты аудита для этой версии трейса недоступны', description: 'Обновите аудит, чтобы проверить именно эту версию трейса.' };
    }
    const evaluated = audit.rule_evaluations.filter(rule => rule.status === 'evaluated').length;
    const limited = audit.rule_evaluations.filter(rule => rule.status === 'insufficient_data' || rule.status === 'not_applicable').length;
    if (!evaluated && audit.rule_evaluations.some(rule => rule.status === 'not_implemented')) {
      return { title: 'Проверка пока недоступна', description: 'Сервер ещё не выполняет проверки для этого трейса.' };
    }
    if (!evaluated) return { title: 'Недостаточно данных', description: 'Проверки не смогли оценить данные этого трейса. Отсутствие проблем не подтверждено.' };
    if (limited) return { title: 'Проблем не обнаружено, но данных недостаточно', description: 'Часть проверок не смогла оценить данные этого трейса.' };
    return { title: 'Проблем не обнаружено', description: 'Аудит не нашёл проблем, связанных с этим трейсом.' };
  })();

  useLayoutEffect(() => {
    if (tab !== 'timeline') return;
    const ruler = timelineRuler.current;
    if (!ruler) return;
    function measureBaseWidth() {
      if (ruler) setBaseRulerWidth(Math.round(ruler.getBoundingClientRect().width - zoomExtraWidth));
    }
    measureBaseWidth();
    const observer = new ResizeObserver(measureBaseWidth);
    observer.observe(ruler);
    return () => observer.disconnect();
  }, [tab, rows.length, zoomExtraWidth]);

  useLayoutEffect(() => {
    const anchor = zoomAnchor.current;
    const scroller = timelineScroll.current;
    const ruler = timelineRuler.current;
    zoomAnchor.current = null;
    if (anchor !== null && scroller && ruler) scroller.scrollLeft = anchor * ruler.clientWidth;
  }, [zoom]);

  function changeZoom(next: number) {
    const scroller = timelineScroll.current;
    const ruler = timelineRuler.current;
    if (next === zoom || !scroller || !ruler?.clientWidth) return;
    zoomAnchor.current = scroller.scrollLeft / ruler.clientWidth;
    setZoom(next);
  }

  function toggle(id: string) {
    setCollapsed(previous => {
      const next = new Set(previous);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
    if (!collapsed.has(id)) setSelected(id);
  }

  return <>
    <PageHeader titleClassName="trace-title"
      title={<>{method && <HttpMethod method={method}/>}<span>{endpoint}</span></>}
      context={<BackToTraces/>}
      detail={<div className="trace-header-meta">
        <div><span>Сервис</span><strong>{summary.service}</strong></div>
        <div>
          <span>Trace ID</span>
          <CopyId key={summary.trace_id} label="Trace ID" value={summary.trace_id}/>
        </div>
      </div>}
      actions={<><Button icon="download" onClick={onExport}>JSON-отчёт</Button>
        {onBuild && <Button variant="primary" onClick={onBuild}>Собрать стенд</Button>}</>}/>
    <Metrics items={[
      { label: summary.http_status === null ? 'Результат' : httpCodeLabel,
        value: summary.http_status === null ? outcomeLabels[summary.outcome] : <HttpStatus code={summary.http_status}/>,
        tone: summary.http_status !== null ? 'neutral' : summary.outcome === 'error' ? 'danger' : summary.outcome === 'ok' ? 'success' : 'neutral' },
      { label: 'Длительность', value: duration(summary.duration_ns) },
      { label: 'Сервисы', value: services.length },
    ]}/>

    {trace.quality_issues.map(issue => <Message tone="warning" key={issue}>{issue}</Message>)}
    <Tabs label="Разделы трейса" value={tab} onChange={setTab} fullWidth items={[
      { value: 'timeline', label: 'Выполнение', panelId: 'timeline-panel' },
      { value: 'audit', label: <>Аудит {findings.length > 0 && <span>{findings.length}</span>}</>, panelId: 'audit-panel' },
      { value: 'whatif', label: 'Прогноз ускорения', panelId: 'whatif-panel' },
    ]}/>

    {tab === 'timeline' && <div id="timeline-panel" role="tabpanel" aria-labelledby="timeline-panel-tab">
      {rows.length ? <div className="timeline-layout">
        <Panel className="waterfall" title="Цепочка вызовов" meta={<div className="page-actions">
          {compactSpans.length < spans.length && <Button type="button" variant="ghost" onClick={() => {
            if (showAllSpans && selected) setSelected(trace.compactable_client_spans?.[selected] ?? selected);
            setShowAllSpans(!showAllSpans);
          }}>{showAllSpans ? 'Свернуть пары спанов' : 'Показать все спаны'}</Button>}
          {collapsed.size > 0 && <Button variant="ghost" onClick={() => setCollapsed(new Set())}>Развернуть всё</Button>}
          {total > 0 && <div className="timeline-zoom" role="group" aria-label="Масштаб шкалы">
            <Button type="button" aria-label="Уменьшить масштаб" disabled={zoom === 1}
              onClick={() => changeZoom(zoom / 2)}>−</Button>
            <Button type="button" aria-label={`Масштаб ${zoom * 100}%. Сбросить масштаб`} title="Сбросить масштаб"
              disabled={zoom === 1} onClick={() => changeZoom(1)}>{zoom * 100}%</Button>
            <Button type="button" aria-label="Увеличить масштаб" disabled={zoom === maxTimelineZoom}
              onClick={() => changeZoom(zoom * 2)}>+</Button>
          </div>}
        </div>}>
          <div ref={timelineScroll} className="timeline-scroll" tabIndex={0} role="region" aria-label="Шкала вызовов">
            <div className="timeline-canvas" style={{ '--timeline-zoom-extra': `${zoomExtraWidth}px`, '--timeline-tick-step': `${100 / tickCount}%` } as CSSProperties}>
              <div className="timeline-head">
                <span>Сервис / операция</span>
                <div ref={timelineRuler} className="timeline-ruler">
                  {axisTicks.map((tick, index) => <span key={tick}
                    style={{ left: `${tick * 100}%`, transform: index === 0 ? 'none' : tick === 1 ? 'translateX(-100%)' : 'translateX(-50%)' }}>
                    {axisNumber.format(total * tick * (axisInMicroseconds ? 1000 : 1))} {axisInMicroseconds ? 'мкс' : 'мс'}
                  </span>)}
                </div>
                <span>Длительность</span>
              </div>
              {rows.map(({ span, depth }) => {
                const active = selectedSpan?.span_id === span.span_id;
                const isOpen = !collapsed.has(span.span_id);
                const color = colors.get(span.service);
                return <div className={`span-row${active ? ' is-selected' : ''}`} key={span.span_id}>
                  <div className="span-label" style={{ paddingInlineStart: `min(${12 + depth * 13}px, 35%)` }}>
                    {parents.has(span.span_id) ? <button type="button" className={`collapse-button${isOpen ? ' is-open' : ''}`}
                      aria-expanded={isOpen} aria-label={`${isOpen ? 'Свернуть' : 'Развернуть'} ${span.name}`}
                      onClick={() => toggle(span.span_id)}><Icon name="chevron" size={12}/></button> : <span className="collapse-space"/>}
                    <i style={{ background: color }}/>
                    <button type="button" className="span-select" aria-pressed={active} onClick={() => setSelected(span.span_id)}>
                      <strong>{span.service}</strong><span title={span.name}><SpanOperation span={span}/></span>
                    </button>
                  </div>
                  <button type="button" className="span-track" tabIndex={-1}
                    aria-label={`Выбрать ${span.service} ${span.name}`} onClick={() => setSelected(span.span_id)}>
                    <span className={`span-bar${span.outcome === 'error' ? ' is-error' : ''}`} style={{
                      left: `${(ms(span.start_offset_ns) ?? 0) / scale * 100}%`,
                      width: `${Math.max(.4, (ms(span.duration_ns) ?? 0) / scale * 100)}%`,
                      backgroundColor: color,
                    }}>{span.outcome === 'error' && <span/>}</span>
                  </button>
                  <span className="span-duration">{duration(span.duration_ns)}</span>
                </div>;
              })}
            </div>
          </div>
          <div className="timeline-footer">
            {services.map(service => <span key={service}><i style={{ background: colors.get(service) }}/>{service}</span>)}
          </div>
        </Panel>
        <Panel className="trace-inspector" title="Выбранный спан">
          {selectedSpan && <div className="inspector">
            <dl><div><dt>Сервис</dt><dd className="service-name"><i style={{ background: colors.get(selectedSpan.service) }}/>{selectedSpan.service}</dd></div><div><dt>{/^(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b/.test(selectedSpan.name) ? 'Эндпоинт' : 'Операция'}</dt><dd><SpanOperation span={selectedSpan}/></dd></div>
              <div><dt>Длительность</dt><dd>{duration(selectedSpan.duration_ns)}</dd></div>
              <div><dt>Время от начала трейса</dt><dd>{duration(selectedSpan.start_offset_ns)}<small className="field-note">Через сколько после начала трейса начался этот спан</small></dd></div>
              <div><dt>{httpCodeLabel}</dt><dd>{selectedSpan.http_status === null ? 'Не наблюдался' : <HttpStatus code={selectedSpan.http_status}/>}</dd></div>
              {selectedSpan.error_type && <div><dt>Тип ошибки</dt><dd className="text-danger">{selectedSpan.error_type}</dd></div>}
              <div><dt>Span ID</dt><dd><CopyId key={selectedSpan.span_id} label="Span ID" value={selectedSpan.span_id}/></dd></div>
            </dl>
          </div>}
        </Panel>
      </div> : <Message>В этом трейсе нет доступных спанов.</Message>}
    </div>}

    {tab === 'audit' && <div id="audit-panel" role="tabpanel" aria-labelledby="audit-panel-tab">
      {auditLoading ? <LoadingState>Загружаем аудит…</LoadingState>
        : auditError ? <Message tone="danger" action={<RetryButton onClick={onRetryAudit}/>}>{auditError}</Message>
        : findings.length ? findings.map(finding => <FindingCard key={finding.occurrence_id} finding={finding} onPlan={onPlan} pending={pending}/>)
        : <Panel><div className="inline-empty">
          <Icon name="audit"/>
          <h2>{traceAuditEmpty.title}</h2>
          <p>{traceAuditEmpty.description}</p>
        </div></Panel>}
    </div>}

    {tab === 'whatif' && <div id="whatif-panel" role="tabpanel" aria-labelledby="whatif-panel-tab">
      <WhatIf trace={trace}/>
    </div>}
  </>;
}
