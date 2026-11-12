import { Input } from '../components/ui';
import { ScenarioPreview } from '../components/ScenarioPreview';
import { ServiceGraph } from '../components/ServiceGraph';
import { Tabs } from '../components/Tabs';
import { exampleScenario } from '../labScenario';
import { statusLabel, scenarioLabel } from '../presentation';
import { usePersistentState } from '../state';
import { HttpOperation } from '../components/HttpText';
import { Select } from '../components/Select';
import { useEffect, useRef, useState } from 'react';
import type { Client, LabFault, LabRun, Stand, ScenarioSnapshot, ScenarioSelector } from '../api/types';
import { ApiError, createOrRecoverStand, isUncertainResponse } from '../api/client';
import { Badge, Button, Message, Panel, PageHeader, TableScroll, Field, LoadingState } from '../components/ui';
import { Metrics } from '../components/Metrics';
import { ServiceConfig } from '../components/ServiceConfig';

function validFaultDraft(value: unknown): value is LabFault {
  if (!value || typeof value !== 'object') return false;
  const draft = value as LabFault;
  const point = draft.failure_point;
  return typeof draft.service_id === 'string' && typeof draft.route === 'string'
    && Number.isFinite(draft.error_rate) && draft.error_rate >= 0 && draft.error_rate <= 1
    && Number.isInteger(draft.http_status) && draft.http_status >= 400 && draft.http_status <= 599
    && Number.isFinite(draft.delay_ms) && draft.delay_ms >= 0 && !!point
    && (point.mode === 'fixed' ? typeof point.boundary_id === 'string' : point.mode === 'random' && Array.isArray(point.boundary_ids) && point.boundary_ids.every(id => typeof id === 'string'));
}
type RunRequest = Parameters<Client['startRun']>[1];
const pendingRuns = new WeakMap<Client, { standId: string; request: RunRequest }>();

export function Lab({ client, demo = false }: { client: Client | null; demo?: boolean }) {
  const fileInput=useRef<HTMLInputElement>(null);
  const [fileName,setFileName]=usePersistentState('lab:scenario-file-name','');
  const [checked,setChecked]=useState<ScenarioSnapshot|null>(null);
  const [validating,setValidating]=useState(false);
  const [validationRetry,setValidationRetry]=useState(0);
  const [validationError,setValidationError]=useState('');
  const [seed,setSeed]=usePersistentState('lab:seed','');
  useEffect(()=>{const value=new URLSearchParams(location.hash.split('?')[1]).get('seed');if(value!==null)setSeed(value);},[]);
  const [scenarioText,setScenarioText]=usePersistentState('lab:scenario-json', '');
  const [stand, setStand] = useState<Stand | null>(null);
  const [configService, setConfigService] = usePersistentState('lab:config-service', '');
  const [runs, setRuns] = useState<LabRun[]>([]);
  const selectedService = stand?.services.find(item=>item.service_id===configService);
  const [configMode,setConfigMode]=usePersistentState<'routes'|'calls'|'errors'>('lab:config-mode','routes');
  const [routeName,setRouteName]=usePersistentState(`lab:${stand?.stand_id}:${configService}:route`,'');
  const selectedRoute=selectedService?.routes.find(r=>r.route===routeName) ?? selectedService?.routes[0];
  const savedFault=stand?.faults.find(f=>f.service_id===configService&&f.route===selectedRoute?.route);
  const [fault, setFault] = usePersistentState<LabFault>(`lab:${stand?.stand_id}:${configService}:${selectedRoute?.route}:fault`, () => savedFault ?? { service_id: configService, route: selectedRoute?.route ?? '', error_rate: .2, http_status: 503, delay_ms: 0, failure_point: { mode: 'fixed', boundary_id: selectedRoute?.boundaries[0]?.boundary_id ?? 'before-first' } }, validFaultDraft);
  const [count, setCount] = usePersistentState(`lab:${stand?.stand_id ?? 'new'}:count`, '30'); const [concurrency, setConcurrency] = usePersistentState(`lab:${stand?.stand_id ?? 'new'}:concurrency`, '1');
  const [actionBusy, setBusy] = useState(false);
  const [refreshing,setRefreshing]=useState(true);
  const [configSaving,setConfigSaving]=useState(false);
  const busy=actionBusy||refreshing||configSaving||validating;
  const [error, setError] = useState('');
  const pending = client ? pendingRuns.get(client) : undefined;
  const retry = pending?.standId === stand?.stand_id ? pending?.request : undefined;
  const active = runs.find(run => run.stand_id === stand?.stand_id && (run.status === 'running' || run.trace_collection.status === 'waiting'));

  async function refresh(clearError = true, signal?: AbortSignal) {
    if (!client) return;
    if (clearError) setError('');
    setRefreshing(true);
    try {
      const [activeStand, history] = await Promise.all([client.getActiveStand(signal), client.listRuns(signal)]);
      if (signal?.aborted) return;
      setStand(activeStand?.status === 'stopped' ? null : activeStand);
      setRuns(history.sort((a, b) => b.started_at.localeCompare(a.started_at)));
    } catch (cause) {
      if (clearError && !signal?.aborted) {
        setError(cause instanceof Error ? cause.message : 'Не удалось загрузить состояние стенда.');
      }
    } finally { if(!signal?.aborted)setRefreshing(false); }
  }
  useEffect(() => {
    const controller = new AbortController();
    setStand(null);
    setRuns([]);
    void refresh(true, controller.signal);
    return () => controller.abort();
  }, [client]);
  useEffect(() => {
    if (!client || !active) return;
    const timer = window.setInterval(() => {
      client.getRun(active.run_id).then(run => setRuns(old => old.map(item => item.run_id === run.run_id ? run : item))).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [client, active?.run_id]);
  useEffect(() => {
    if (!client || !stand || !['building', 'stopping'].includes(stand.status)) return;
    const timer = window.setInterval(() => { void client.getActiveStand().then(value => setStand(value?.status === 'stopped' ? null : value)).catch(() => undefined); }, 1000);
    return () => window.clearInterval(timer);
  }, [client, stand?.stand_id, stand?.status]);
  async function build(selector?:ScenarioSelector) {
    if (!client) return;
    setBusy(true);
    setError('');
    try {
      setStand(await createOrRecoverStand(client, selector ?? {scenario_hash:checked!.scenario_hash}, setStand));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Не удалось собрать стенд.');
      await refresh(false);
    } finally {
      setBusy(false);
    }
  }
  useEffect(()=>{
    const controller=new AbortController();
    setChecked(null);setValidationError('');setValidating(false);
    if(!client||!scenarioText)return;
    setValidating(true);
    async function validate(){try{
      const result=await client!.validateScenario({scenario:JSON.parse(scenarioText)},controller.signal);
      if(!controller.signal.aborted)setChecked(result);
    }catch(cause){if(!controller.signal.aborted)setValidationError(cause instanceof SyntaxError?'Некорректный JSON. Проверьте запятые и кавычки.':cause instanceof Error?cause.message:'Не удалось проверить JSON.');}
    finally{if(!controller.signal.aborted)setValidating(false);}}
    void validate();return()=>controller.abort();
  },[client,scenarioText,validationRetry]);
  async function saveFault() { if (!client || !stand || !selectedRoute || !validFaultDraft(fault)) return; setBusy(true); setError(''); try { setStand(await client.setFault(stand.stand_id, fault, stand.fault_revision, crypto.randomUUID())); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось применить сбой.'); if (cause instanceof ApiError && cause.status === 409) await refresh(false); } finally { setBusy(false); } }
  async function clearFault() { if (!client || !stand) return; setBusy(true); setError(''); try { setStand(await client.clearFault(stand.stand_id, stand.fault_revision, crypto.randomUUID())); } catch (cause) { setError(cause instanceof Error ? cause.message : 'Не удалось сбросить сбой.'); if (cause instanceof ApiError && cause.status === 409) await refresh(false); } finally { setBusy(false); } }
  async function run() {
    if (!client || !stand || busy) return;
    setBusy(true); setError('');
    const request = retry ?? {
      request_id: crypto.randomUUID(),
      scenario_revision: stand.scenario_revision,
      expected_fault_revision: stand.fault_revision,
      config_revisions: stand.config_revisions,
      ...(seed.trim()?{seed:Number(seed)}:{}),
      request_count: Number(count),
      concurrency: Number(concurrency),
    };
    pendingRuns.set(client, { standId: stand.stand_id, request });
    try {
      const result = await client.startRun(stand.stand_id, request);
      pendingRuns.delete(client);
      location.hash = `#/lab?run=${encodeURIComponent(result.run_id)}`;
      setRuns(old => [result, ...old.filter(item => item.run_id !== result.run_id)]);
    } catch (cause) {
      if (isUncertainResponse(cause)) {
        setError('Сервер не подтвердил запуск. Повторите запрос, чтобы получить состояние того же теста.');
      } else {
        pendingRuns.delete(client);
        setError(cause instanceof Error ? cause.message : 'Не удалось запустить нагрузку.');
        if (cause instanceof ApiError && cause.status === 409) await refresh(false);
      }
    }
    finally { setBusy(false); }
  }
  async function stop() {
    if (!client || !stand) return;
    setBusy(true);
    setError('');
    try {
      await client.stopStand(stand.stand_id);
      await refresh();
    } catch (cause) {
      await refresh();
      setError(cause instanceof Error ? cause.message : 'Не удалось остановить стенд.');
    } finally {
      setBusy(false);
    }
  }
  const displayedRuns = stand ? runs.filter(run=>run.stand_id===stand.stand_id) : runs;
  if(client&&refreshing&&!stand&&!runs.length)return <><PageHeader title="Тестовый стенд"/><LoadingState>Загружаем стенд…</LoadingState></>;
  return <><PageHeader title="Тестовый стенд" actions={<><Button icon="refresh" onClick={() => refresh()} disabled={busy}>Обновить</Button></>}/>
    {!demo && <p className="stand-purpose">Изолированные мок-сервисы по вашей конфигурации. Реальные сервисы не изменяются.</p>}
    {error && <Message tone="danger">{error}</Message>}
    {!client ? <Message tone="warning">Подключите сервер на странице «Настройки подключения».</Message> : !stand ? <Panel title="Создание стенда"><div className="lab-body scenario-upload">
      <p>Загрузите JSON с описанием сервисов и их вызовов.</p>
      <div className="scenario-controls"><div className="scenario-file"><Button disabled={busy} onClick={()=>fileInput.current?.click()}>Загрузить JSON</Button><span className="muted">{fileName || 'Файл не выбран'}</span>
        <Input ref={fileInput} hidden type="file" accept=".json,application/json" disabled={busy} onChange={async event=>{const file=event.target.files?.[0];if(!file)return;setChecked(null);setFileName(file.name);try{if(file.size>1024*1024)throw new Error('Файл должен быть не больше 1 МБ.');const text=await file.text();setScenarioText(text);setValidationRetry(v=>v+1);}catch(cause){setError(cause instanceof Error?cause.message:String(cause));}event.target.value='';}}/>
        {validating&&<span className="muted" role="status">Проверяем JSON…</span>}
      </div>
      <div className="scenario-actions"><Button disabled={busy} onClick={()=>{const blob=new Blob([JSON.stringify(exampleScenario,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const link=document.createElement('a');link.href=url;link.download='scenario.json';link.click();URL.revokeObjectURL(url);}}>Скачать пример JSON</Button><Button disabled={busy} onClick={()=>build({scenario_id:'checkout'})}>Создать пример магазина</Button></div></div>
      {validationError&&<Message tone="danger" action={<Button disabled={busy} onClick={()=>setValidationRetry(v=>v+1)}>Повторить проверку</Button>}>{validationError}</Message>}
      {checked&&<><h3>{checked.scenario_id}</h3><p>Начальный запрос: {checked.scenario.entrypoint.service_id} {checked.scenario.entrypoint.route}. Сервисов: {checked.service_count}. Эндпоинтов: {checked.route_count}.</p>
      <ScenarioPreview key={checked.scenario_hash} scenario={checked.scenario} scenarioHash={checked.scenario_hash}/><Button variant="primary" onClick={()=>build()} disabled={busy}>Создать стенд</Button></>}
    </div></Panel> : <>
      <div className="lab-grid lab-controls">
        <Panel title="Стенд" meta={<Badge tone={stand.status === 'ready' ? 'success' : ['failed', 'unavailable'].includes(stand.status) ? 'danger' : 'warning'}>{statusLabel(stand.status)}</Badge>}><div className="lab-body"><Metrics items={[{ label: 'Сценарий', value: stand.scenario_origin?.kind==='builtin' ? scenarioLabel(stand.scenario_id) : stand.scenario_id }, { label: 'Сервисов', value: stand.services.length }, { label: 'Настроено ошибок', value: stand.faults.length }]}/><Button icon="stop" disabled={busy || !!active || ['building', 'stopped'].includes(stand.status)}
          onClick={() => stop()}>{stand.status === 'stopping' ? 'Повторить остановку' : 'Остановить стенд'}</Button></div></Panel>
        <Panel title="Схема стенда"><ServiceGraph storageKey={`stand:${stand.stand_id}`} nodes={stand.services.map(service=>({id:service.service_id,label:service.display_name,detail:`Эндпоинтов: ${service.routes.length}`}))} edges={stand.services.flatMap(service=>{const targets=[...new Set((service.calls??[]).map(c=>c.target.service_id))];return targets.map(target=>({id:`link:${service.service_id}:${target}`,from:service.service_id,to:target}));})} selected={configService} onSelect={id=>{if(busy)return;if(id.startsWith('link:')){const owner=stand.services.find(service=>(service.calls??[]).some(c=>`link:${service.service_id}:${c.target.service_id}`===id));if(owner){setConfigService(owner.service_id);setConfigMode('calls');}}else setConfigService(id);}}/></Panel>
        <Panel title={selectedService ? `Настройки сервиса ${selectedService.display_name}` : 'Настройки сервиса'}><div className="lab-body stand-settings">
          <Field label="Сервис"><Select value={configService} disabled={busy} onChange={setConfigService}><option value="">Выберите сервис</option>{stand.services.map(service=><option key={service.service_id} value={service.service_id}>{service.display_name}</option>)}</Select></Field>
          {!selectedService ? <p className="muted">Выберите сервис на схеме, чтобы настроить его поведение.</p> : <>
          <Tabs label="Поведение сервиса" value={configMode} onChange={value=>{if(!busy)setConfigMode(value);}} items={[{value:'routes',label:'Задержка сервиса'},{value:'calls',label:'Таймауты и повторы'},{value:'errors',label:'Имитация ошибок'}]}/>
          {configMode!=='errors' && <ServiceConfig onSavingChange={setConfigSaving} directSave localOnly={demo} key={selectedService.service_id} client={client} service={selectedService.service_id} environmentId="sandbox" mode={configMode} route={selectedRoute?.route} onRouteChange={setRouteName} disabled={busy || !!active || stand.status !== 'ready'} onChanged={()=>void refresh()}/>}
          {configMode==='errors' && selectedRoute && <section className="stand-errors"><Field label="Эндпоинт"><Select value={selectedRoute.route} onChange={setRouteName}>{selectedService.routes.map(route=><option key={route.route} value={route.route}>{route.route}</option>)}</Select></Field><div className="lab-fault-form">
          <Field label="Вероятность ошибки, %"><Input type="number" min="0" max="100" step="1" value={Math.round(fault.error_rate * 100)} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, error_rate: Number(event.target.value) / 100 }))}/></Field>
          <Field label="Код HTTP при ошибке"><Input type="number" min="400" max="599" data-http-status={fault.http_status} value={fault.http_status} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, http_status: Number(event.target.value) }))}/></Field>
          <Field label="Дополнительная задержка, мс"><Input type="number" min="0" value={fault.delay_ms} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, delay_ms: Number(event.target.value) }))}/><small className="field-note">Прибавляется к обычному времени ответа перед ошибкой.</small></Field>
          <Field label="Выбор этапа обработки"><Select value={fault.failure_point.mode} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, failure_point: value === 'fixed' ? { mode: 'fixed', boundary_id: selectedRoute?.boundaries[0]?.boundary_id ?? '' } : { mode: 'random', boundary_ids: (selectedRoute?.boundaries ?? []).map(item => item.boundary_id) } }))}><option value="fixed">Фиксированный этап</option><option value="random">Случайный из выбранных этапов</option></Select></Field>
          {fault.failure_point.mode === 'fixed' ? <Field label="Этап обработки"><Select value={fault.failure_point.boundary_id} disabled={busy || !!active} onChange={value => setFault(old => ({ ...old, failure_point: { mode: 'fixed', boundary_id: value } }))}>{(selectedRoute?.boundaries ?? []).map(boundary => <option value={boundary.boundary_id} key={boundary.boundary_id}>{boundary.label.replace(/исходящих вызовов/gi, 'обращений к другим сервисам').replaceAll('·', '—')}</option>)}</Select></Field> : <fieldset><legend>Этапы для случайного выбора</legend>{(selectedRoute?.boundaries ?? []).map(boundary => <label key={boundary.boundary_id}><Input type="checkbox" checked={fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.includes(boundary.boundary_id)} disabled={busy || !!active} onChange={event => setFault(old => ({ ...old, failure_point: { mode: 'random', boundary_ids: event.target.checked ? [...(old.failure_point.mode === 'random' ? old.failure_point.boundary_ids : []), boundary.boundary_id] : (old.failure_point.mode === 'random' ? old.failure_point.boundary_ids : []).filter(id => id !== boundary.boundary_id) } }))}/>{boundary.label.replace(/исходящих вызовов/gi, 'обращений к другим сервисам').replaceAll('·', '—')}</label>)}</fieldset>}
          {fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.length === 0 && <Message tone="warning">Выберите хотя бы один этап обработки.</Message>}
          <div className="lab-actions"><Button icon="save" variant="primary" disabled={busy || !!active || stand.status !== 'ready' || !selectedRoute || !validFaultDraft(fault) || (fault.failure_point.mode === 'random' && fault.failure_point.boundary_ids.length === 0)} onClick={() => saveFault()}>Сохранить настройки ошибок</Button><Button disabled={busy || !!active || !stand.faults.length} onClick={() => clearFault()}>Отключить ошибки на стенде</Button></div>
        </div></section>}</>}
        </div></Panel>
        <Panel title="Запуск теста" className="lab-wide lab-load"><div className="lab-body"><div className="form-grid"><Field label="Запросов"><Input type="number" min="1" max="1000" value={count} disabled={busy || !!active || !!retry} onChange={event => setCount(event.target.value)}/></Field><Field label="Параллельных запросов"><Input type="number" min="1" max="32" value={concurrency} disabled={busy || !!active || !!retry} onChange={event => setConcurrency(event.target.value)}/></Field><Field label="Seed (необязательно)"><Input inputMode="numeric" value={seed} disabled={busy||!!active||!!retry} onChange={event=>setSeed(event.target.value)} placeholder="Случайный"/></Field><Button icon="play" variant="primary" disabled={(!!seed.trim()&&(!Number.isSafeInteger(Number(seed))||Number(seed)<0)) || busy || (!retry && (!!active || stand.status !== 'ready' || !Number.isInteger(Number(count)) || Number(count) < 1 || Number(count) > 1000 || !Number.isInteger(Number(concurrency)) || Number(concurrency) < 1 || Number(concurrency) > 32))} onClick={() => run()}>{retry ? 'Повторить запрос запуска' : 'Запустить тест'}</Button></div></div></Panel>

      </div>
    </>}
    {client && <>
      <Panel title="Результаты тестов"><div className="lab-body">{!displayedRuns.length ? <p className="muted">Тесты ещё не запускались.</p> : <TableScroll><table className="lab-runs-table"><thead><tr><th>Время запуска</th><th>Всего запросов</th><th>Состояние</th></tr></thead><tbody>{displayedRuns.map(run => <tr className="is-clickable" key={run.run_id} onClick={event=>{
        if (!(event.target as Element).closest('a') && !window.getSelection()?.toString()) window.location.href=`#/lab?run=${encodeURIComponent(run.run_id)}`;
      }}><td><a href={`#/lab?run=${encodeURIComponent(run.run_id)}`}>{new Date(run.started_at).toLocaleString('ru-RU')}</a></td><td>{run.request_count}</td><td><Badge tone={run.status === 'failed' ? 'danger' : run.status === 'completed' ? 'success' : 'warning'}>{statusLabel(run.status)}</Badge></td></tr>)}</tbody></table></TableScroll>}</div></Panel>

    </>}
  </>;
}
