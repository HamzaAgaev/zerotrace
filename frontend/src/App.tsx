import { demoOnly, demoConnection } from './deployment';
import { Landing } from './views/Landing';
import { Guide } from './views/Guide';
import { RequestActivity } from './components/RequestActivity';
import { ViewMemory } from './components/ViewMemory';
import { StateScope, usePersistentState } from './state';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { downloadJson } from './analysis';
import { isBudgetFinding } from './api/types';
import { ApiError, createClient, normalizeBaseUrl } from './api/client';
import type { AuditResult, Capabilities, ChangePlan, Connection, Finding, JournalEntry, TraceDetail as TraceData, TraceSummary } from './api/types';
import { Badge, Button, Icon, Message, LoadingState, RetryButton } from './components/ui';
import type { IconName } from './components/ui';
import { PlanDialog } from './components/PlanDialog';
import { readRoute, serviceSurfaceForHash } from './lib';
import { TraceList } from './views/TraceList';
import { BackToTraces, TraceDetail } from './views/TraceDetail';
import { Audit } from './views/Audit';
import { ConnectionView } from './views/Connection';
import { Services } from './views/Services';
import { Lab } from './views/Lab';
import { TestResults } from './views/TestResults';

const nav: { id: 'traces' | 'audit' | 'journal' | 'sources' | 'graph' | 'lab'; label: string; icon: IconName }[] = [
  { id: 'traces', label: 'Трейсы', icon: 'traces' },
  { id: 'graph', label: 'Сервисы', icon: 'graph' },
  { id: 'audit', label: 'Аудит', icon: 'audit' },
  { id: 'lab', label: 'Тестовый стенд', icon: 'compare' },
  { id: 'sources', label: 'Настройки подключения', icon: 'plug' },
];
const pageTitles: Record<string, string> = {
  home: 'О продукте', guide: 'Руководство пользователя', traces: 'Трейсы', graph: 'Сервисы', timings: 'Сервисы', audit: 'Аудит', lab: 'Тестовый стенд',
  journal: 'Журнал изменений', sources: 'Настройки подключения', policies: 'Сервисы',
};
function documentTitle(route: ReturnType<typeof readRoute>): string {
  if (route.page === 'lab' && route.runId) return 'Результаты теста — ZeroTrace';
  if (route.page === 'traces' && route.traceId) return `Трейс ${route.traceId} — ZeroTrace`;
  return `${pageTitles[route.page] ?? 'Трейсы'} — ZeroTrace`;
}
function initialConnection(): Connection { if (demoOnly) return demoConnection(); try { const raw = JSON.parse(localStorage.getItem('zerotrace.connection') ?? 'null'); if (raw && ['live', 'demo'].includes(raw.mode) && typeof raw.baseUrl === 'string') return { mode: raw.mode, baseUrl: normalizeBaseUrl(raw.baseUrl), environmentId: typeof raw.environmentId === 'string' ? raw.environmentId : undefined }; } catch { /* A broken saved setting must not prevent opening settings. */ } return { mode: 'live', baseUrl: import.meta.env.BASE_URL === '/' ? '' : new URL(import.meta.env.BASE_URL, location.origin).href.replace(/\/$/, ''), environmentId: 'shop' }; }
export default function App() {
  const [connection, setConnection] = useState<Connection>(initialConnection);
  const scope = `${connection.mode}:${connection.baseUrl}:${connection.environmentId}`;
  return <StateScope.Provider key={scope} value={scope}><ConnectedApp connection={connection} setConnection={setConnection}/></StateScope.Provider>;
}
function ConnectedApp({ connection, setConnection }: { connection: Connection; setConnection: (value: Connection) => void }) {
  const [route, setRoute] = useState(() => readRoute(location.hash));
  const client = useMemo(() => createClient(connection), [connection]);
  const [audit, setAudit] = useState<AuditResult | null>(null); const [capabilities, setCapabilities] = useState<Capabilities | null>(null); const [journal, setJournal] = useState<JournalEntry[]>([]);
  const [trace, setTrace] = useState<TraceData | null>(null); const [busy, setBusy] = useState(true); const [traceBusy, setTraceBusy] = useState(false); const [error, setError] = useState(''); const [traceError, setTraceError] = useState(''); const [auditError, setAuditError] = useState(''); const [journalError, setJournalError] = useState(''); const [refreshKey, setRefreshKey] = useState(0);
  const [plan, setPlan] = useState<{ plan: ChangePlan; finding: Finding } | null>(null); const [planBusy, setPlanBusy] = useState(false); const [planError, setPlanError] = useState(''); const planRequest = useRef<AbortController | null>(null);
  const [auditBusy, setAuditBusy] = useState(true);
  const [auditRefreshKey, setAuditRefreshKey] = useState(0);
  const [membershipBusy, setMembershipBusy] = useState(false);
  const [journalBusy, setJournalBusy] = useState(true);
  useEffect(() => { const update = () => { if(location.hash.split('?')[0] === '#/journal') { location.hash='#/audit?tab=history'; return; } setRoute(readRoute(location.hash)); }; update(); window.addEventListener('hashchange', update); return () => window.removeEventListener('hashchange', update); }, []);
  useEffect(() => { document.title = documentTitle(route); }, [route]);
  const selectedId = route.page === 'traces' ? route.traceId : undefined;
  const routeRef = route.page === 'traces' ? route.traceRef : undefined;
  const selectedRef = useMemo(() => routeRef, [routeRef?.environment_id, routeRef?.trace_id, routeRef?.generation, routeRef?.revision]);
  const loadedTraceRef = trace?.trace_ref;
  const auditTraceRef = selectedId ? (loadedTraceRef?.trace_id === selectedId ? loadedTraceRef : undefined) : undefined;
  const [savedServiceSurface] = usePersistentState<'graph'|'table'>('services:surface','graph');
  const needsAudit = route.page === 'audit'
    || ((route.page === 'graph' || route.page === 'timings') && serviceSurfaceForHash(location.hash,savedServiceSurface) === 'graph')
    || (route.page === 'traces' && !!route.traceId);
  useEffect(() => {
    const controller = new AbortController();
    const { signal } = controller;
    setBusy(false);
    setJournalBusy(true);
    setError('');
    setJournalError('');
    setCapabilities(null);
    setJournal([]);
    setPlan(null);
    planRequest.current?.abort();
    setPlanBusy(false);

    async function load<T>(request: Promise<T>, accept: (value: T) => void,
      reject?: (message: string) => void, finish?: () => void) {
      try {
        const value = await request;
        if (!signal.aborted) accept(value);
      } catch (error) {
        if (!signal.aborted) reject?.(error instanceof Error ? error.message : 'Не удалось загрузить данные');
      } finally {
        if (!signal.aborted) finish?.();
      }
    }

    void load(client.getCapabilities(signal), setCapabilities);
    void load(client.getJournal(signal), setJournal, setJournalError, () => setJournalBusy(false));
    return () => controller.abort();
  }, [client, refreshKey]);
  useEffect(() => {
    if (!needsAudit || (selectedId && !auditTraceRef)) {
      setAudit(null);
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    setAuditBusy(true);
    setAuditError('');
    setAudit(null);
    void client.getAudit(signal, auditTraceRef).then(result => {
      if (!signal.aborted) setAudit(result);
    }).catch(error => {
      if (!signal.aborted) setAuditError(error instanceof Error ? error.message : 'Не удалось загрузить аудит');
    }).finally(() => {
      if (!signal.aborted) setAuditBusy(false);
    });
    return () => controller.abort();
  }, [client, refreshKey, auditRefreshKey, needsAudit, selectedId, auditTraceRef]);
  const pageBusy = busy;
  useEffect(() => { const controller = new AbortController(); setTrace(null); setTraceError(''); if (!selectedId) { setTraceBusy(false); return () => controller.abort(); } setTraceBusy(true);
    const { signal } = controller;
    setMembershipBusy(connection.mode === 'live');
    client.getTrace(selectedRef ?? selectedId, signal, { includeFindings: false, includeSpanKind: false }).then(data => {
      if (signal.aborted) return;
      setTrace(data);
    }).catch(e => {
      if (!signal.aborted) {
        setTraceError(e instanceof Error ? e.message : 'Не удалось открыть трейс');
        setMembershipBusy(false);
      }
    }).finally(() => {
      if (!signal.aborted) setTraceBusy(false);
    });
    return () => controller.abort();
  }, [client, connection.mode, selectedId, selectedRef, refreshKey]);
  useEffect(() => {
    if (!loadedTraceRef || connection.mode !== 'live' || auditBusy || !audit) return;
    if (audit.trace_findings) {
      const ids = audit.trace_findings.find(row => row.trace_ref.environment_id === loadedTraceRef.environment_id && row.trace_ref.trace_id === loadedTraceRef.trace_id && row.trace_ref.generation === loadedTraceRef.generation && row.trace_ref.revision === loadedTraceRef.revision)?.occurrence_ids ?? [];
      setTrace(current => current?.trace_ref === loadedTraceRef ? { ...current, finding_occurrence_ids: ids, summary: { ...current.summary, finding_count: ids.length } } : current);
      setMembershipBusy(false);
      return;
    }
    const controller = new AbortController();
    const { signal } = controller;
    setMembershipBusy(true);
    void client.getTrace(loadedTraceRef, signal, { includeSpanKind: false }).then(audited => {
      if (!signal.aborted) setTrace(current => current?.trace_ref === loadedTraceRef ? {
        ...current,
        finding_occurrence_ids: audited.finding_occurrence_ids,
        summary: { ...current.summary, finding_count: audited.summary.finding_count },
      } : current);
    }).catch(error => {
      if (!signal.aborted) setAuditError(error instanceof Error ? error.message : 'Не удалось загрузить проблемы трейса.');
    }).finally(() => {
      if (!signal.aborted) setMembershipBusy(false);
    });
    return () => controller.abort();
  }, [client, connection.mode, loadedTraceRef, auditRefreshKey, audit, auditBusy]);
  useEffect(() => () => planRequest.current?.abort(), []);
  const refresh = useCallback(() => setRefreshKey(v => v + 1), []);
  const changeConnection = (requested: Connection) => { const value = demoOnly ? demoConnection() : requested; setPlanError(''); setConnection(value); try { localStorage.setItem('zerotrace.connection', JSON.stringify(value)); } catch { /* Current connection still works if persistence is unavailable. */ } };
  function refreshStaleRecommendation() {
    setPlanError('Рекомендация устарела. Обновляем аудит. Откройте актуальный план, если он доступен.');
    refresh();
  }
  async function openPlan(finding: Finding) {
    if (!isBudgetFinding(finding) || finding.recommendation.can_prepare_plan === false) return;
    planRequest.current?.abort();
    const controller = new AbortController();
    planRequest.current = controller;
    setPlanError('');
    setPlanBusy(true);
    try {
      const result = await client.createPlan(finding, controller.signal);
      if (!controller.signal.aborted) setPlan({ plan: result, finding });
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiError && error.code === 'stale_recommendation') {
        refreshStaleRecommendation();
      } else {
        setPlanError(error instanceof Error ? error.message : 'Не удалось получить план');
      }
    } finally {
      if (!controller.signal.aborted) setPlanBusy(false);
    }
  }
  function exportReport() {
    if (!trace) return;
    downloadJson(`zerotrace-${trace.summary.trace_id}.json`, {
      mode: connection.mode,
      trace,
      audit,
      ...(connection.mode === 'demo' ? { limitations: ['Все значения взяты из демонстрационного набора.'] } : {}),
    }, { schema: 'zerotrace.frontend-report', producerVersion: 'frontend-0.2.0' });
  }
  if (route.page === 'home') return <><ViewMemory route={location.hash}/><main id="main-content" className="landing-page" tabIndex={-1}><Landing/></main></>;
  return <><RequestActivity/><ViewMemory route={location.hash}/><div className="app-shell"><a className="skip-link" href="#main-content" onClick={event=>{event.preventDefault();document.getElementById('main-content')?.focus();}}>Перейти к содержимому</a><aside className="sidebar"><a className="brand" href="#/traces" aria-label="ZeroTrace — трейсы"><span className="brand-mark"><i/><i/><i/></span>ZeroTrace</a><nav aria-label="Основная навигация">{nav.map(n => <a key={n.id} href={n.id === 'graph' ? '#/services' : `#/${n.id}`} className={`nav-link ${(route.page === n.id || (n.id === 'graph' && route.page === 'timings')) ? 'is-active' : ''}`} aria-current={(route.page === n.id || (n.id === 'graph' && route.page === 'timings')) ? 'page' : undefined}><Icon name={n.icon}/><span>{n.label}</span>{n.id === 'audit' && audit?.findings.length ? <small>{audit.findings.length}</small> : null}</a>)}</nav><div className="sidebar-footer"><a href={import.meta.env.BASE_URL} className="nav-link"><Icon name="home"/>О продукте</a><a href="#/guide" className={`nav-link ${route.page === 'guide' ? 'is-active' : ''}`} aria-current={route.page === 'guide' ? 'page' : undefined}><Icon name="book"/>Руководство</a></div></aside><div className="main-shell"><main id="main-content" tabIndex={-1}>
    {connection.mode === 'demo' && <Message>Демонстрационный режим. Данные и изменения сохраняются локально; реальные запросы к сервисам не выполняются.</Message>}
    {planError && <Message tone="danger" action={<Button variant="ghost" onClick={() => setPlanError('')} aria-label="Закрыть ошибку" icon="close"/>}>{planError}</Message>}
    {route.page === 'guide' ? <Guide/> : route.page === 'sources' ? <ConnectionView connection={connection} capabilities={capabilities} onChange={changeConnection}/> : route.page === 'lab' ? (route.runId ? <TestResults key={connection.mode + connection.baseUrl + connection.environmentId + route.runId} client={client} runId={route.runId}/> : <Lab demo={connection.mode === 'demo'} client={client}/>) : error ? <div className="connection-error"><Message tone="danger">{error}</Message><div className="error-actions"><RetryButton onClick={refresh}/><a className="button button--primary" href="#/sources">Настроить подключение</a></div></div> : pageBusy ? <LoadingState>Загружаем данные…</LoadingState> : route.page === 'graph' || route.page === 'timings' ? <>{needsAudit&&auditError&&<Message tone="warning">Аудит недоступен: {auditError.trim().replace(/[.!?…]+$/, '')}. Проблемные связи могут быть показаны не полностью.</Message>}<Services client={client} environmentId={connection.environmentId || 'shop'} audit={audit} view={route.page} demo={connection.mode === 'demo'} onPlan={openPlan} pending={planBusy}/></> : route.page === 'audit' || route.page === 'journal' ? <Audit audit={audit} onPlan={openPlan} pending={planBusy} auditLoading={auditBusy} auditError={auditError} journal={journal} journalLoading={journalBusy} journalError={journalError} onRetry={refresh}/> : selectedId ? traceError ? <Message tone="danger" action={<>
      <RetryButton onClick={refresh}/>
      <BackToTraces/>
    </>}>{traceError}</Message> : traceBusy || !trace ? <LoadingState>Открываем трейс…</LoadingState> : <TraceDetail key={`${trace.trace_ref.environment_id}-${trace.trace_ref.trace_id}-${trace.trace_ref.generation}-${trace.trace_ref.revision}`} trace={trace} audit={audit} auditLoading={!auditError && (auditBusy || membershipBusy)} auditError={auditError} onRetryAudit={() => setAuditRefreshKey(key => key + 1)} onPlan={openPlan} pending={planBusy} onExport={exportReport}/> : <TraceList client={client}/>}
  </main></div>{plan && <PlanDialog key={plan.plan.plan_id} plan={plan.plan} finding={plan.finding} showRuleInfo={!selectedId} demo={connection.mode === 'demo'} canApply={connection.mode === 'live' && connection.environmentId === 'sandbox' && plan.plan.apply_enabled} client={client} onApplied={refresh} onStaleRecommendation={refreshStaleRecommendation} onClose={() => setPlan(null)}/>}</div></>;
}
