import { usePersistentState } from '../state';
import { Endpoint, endpointLabel } from './HttpText';
import { Tabs } from './Tabs';
import { Select } from './Select';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import type { ChangePlan, Client, ConfigSnapshot, RouteWork } from '../api/types';
import { Badge, Button, Field, LoadingState, Message } from './ui';
import { PlanDialog } from './PlanDialog';

function Setting({label,editable,children}:{label:string;editable:boolean;value:ReactNode;children:ReactNode}) {
  return editable ? <Field label={label}>{children}</Field> : null;
}
type CallValues = { timeout: string; attempts: string; backoff: string; retry: string };
type Draft = { revision: string; call?: CallValues; work?: RouteWork };
export function ServiceConfig({ client, service, environmentId, localOnly = false, directSave = true, disabled = false, onChanged, onSavingChange, initialCall, mode: forcedMode, route: forcedRoute, onRouteChange }: { initialCall?: { callee:string; operation:string }; mode?: 'routes' | 'calls'; route?:string; onRouteChange?:(route:string)=>void; client: Client; service: string; environmentId: string; localOnly?: boolean; directSave?: boolean; disabled?: boolean; onSavingChange?: (saving:boolean) => void; onChanged?: () => void }) {
  const [savedMode,setMode]=usePersistentState<'routes'|'calls'>(`config:${environmentId}:${service}:mode`,'calls');
  const mode=forcedMode ?? savedMode;
  const context = `config:${environmentId}:${service}`;
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState<{value: ChangePlan; target: string} | null>(null);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = usePersistentState(`${context}:selected`, '');
  const [drafts, setDrafts] = usePersistentState<Record<string,Draft>>(`${context}:drafts`, {});
  const saveRequest=useRef<{signature:string;plan:ChangePlan;requestId:string}|null>(null);
  const [saved,setSaved]=useState(false);
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const requestVersion = useRef(0);
  const openedCall = useRef('');
  useEffect(() => {
    const version = ++requestVersion.current;
    const controller = new AbortController();
    setLoading(true); setSnapshot(null); setPlan(null); setError('');
    client.getConfig(service, environmentId, controller.signal).then(config => {
      if (!controller.signal.aborted) setSnapshot(config);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось прочитать настройки.'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); if(requestVersion.current === version) requestVersion.current++; };
  }, [client, service, environmentId, reload]);
  useEffect(()=>{
    if(!initialCall||!snapshot)return;
    const target=JSON.stringify([service,environmentId,initialCall.callee,initialCall.operation]);
    if(openedCall.current===target)return;
    const match=snapshot.calls.find(c=>c.target.service_id===initialCall.callee&&`${c.target.method} ${c.target.route}`===initialCall.operation);
    if(match){setMode('calls');openedCall.current=target;setSelected(`call:${match.call_ref}`);}
  },[initialCall?.callee,initialCall?.operation,snapshot,service,environmentId]);
  const calls = mode === 'calls' ? snapshot?.calls ?? [] : [];
  const routes = mode === 'routes' ? snapshot?.routes ?? [] : [];
  const selectedKey = mode === 'routes' && forcedRoute ? `route:${forcedRoute}` : selected;
  const key = calls.some(c=>`call:${c.call_ref}`===selectedKey) || routes.some(r=>`route:${r.route}`===selectedKey) ? selectedKey : calls[0] ? `call:${calls[0].call_ref}` : routes[0] ? `route:${routes[0].route}` : '';
  const call = calls.find(c=>`call:${c.call_ref}`===key);
  const route = routes.find(r=>`route:${r.route}`===key);
  const draft = drafts[key];
  const values = draft?.call ?? (call ? {timeout:String(call.timeout_ms),attempts:String(call.max_attempts),backoff:String(call.backoff_ms),retry:call.retry_on.join(', ')} : undefined);
  const canEditWork = !!route?.write_fields.includes('work');
  const canEdit = canEditWork || !!call?.write_fields.some(field=>['timeout_ms','max_attempts','backoff_ms','retry_on'].includes(field));
  const readOnlyFields = call ? ['timeout_ms','max_attempts','backoff_ms','retry_on'].filter(field=>!call.write_fields.includes(field)) : route && !canEditWork ? ['work'] : [];
  const work = canEditWork ? draft?.work ?? route?.work : route?.work;
  const conflict = !!draft && draft.revision !== snapshot?.revision;
  const updateCall = (field:keyof CallValues,value:string) => { if(snapshot&&values)setDrafts(old=>({...old,[key]:{revision:old[key]?.revision ?? snapshot.revision,call:{...values,[field]:value}}})); };
  const updateWork = (value:RouteWork) => { if(snapshot)setDrafts(old=>({...old,[key]:{revision:old[key]?.revision ?? snapshot.revision,work:value}})); };
  const removeDraft = (target:string) => setDrafts(old=>{const next={...old};delete next[target];return next;});
  const changes: Record<string,string|number|string[]|RouteWork> = {};
  if(call && values) {
    if(call.write_fields.includes('timeout_ms') && Number(values.timeout)!==call.timeout_ms) changes.per_attempt_timeout_ns=String(Math.round(Number(values.timeout)*1e6));
    if(call.write_fields.includes('max_attempts') && Number(values.attempts)!==call.max_attempts) changes.max_attempts=Number(values.attempts);
    if(call.write_fields.includes('backoff_ms') && Number(values.backoff)!==call.backoff_ms) changes.backoff_ms=Number(values.backoff);
    const retry=values.retry.split(',').map(v=>v.trim()).filter(Boolean);
    if(call.write_fields.includes('retry_on') && JSON.stringify(retry)!==JSON.stringify(call.retry_on)) changes.retry_on=retry;
  }
  if(route && route.write_fields.includes('work') && work && JSON.stringify(work)!==JSON.stringify(route.work))changes.work=work;
  const changed=Object.keys(changes).length>0;
  async function prepare() {
    if(disabled || !snapshot || (!call && !route) || !changed || conflict || inFlight.current)return;
    inFlight.current=true;setPending(true);onSavingChange?.(true);setError('');setSaved(false);
    const version=requestVersion.current;
    try {
      const intent={environment_id:environmentId,service_id:service,expected_config_revision:draft?.revision ?? snapshot.revision,...(call?{call_ref:call.call_ref}:{route:route!.route}),set:changes};
      const signature=JSON.stringify(intent);
      const value=directSave&&saveRequest.current?.signature===signature ? saveRequest.current.plan : await client.createManualPlan(intent);
      if(version!==requestVersion.current)return;
      if(!directSave){setPlan({value,target:key});return;}
      if(!value.apply_enabled)throw new Error('Сервер не разрешает сохранить эти настройки.');
      if(saveRequest.current?.signature!==signature)saveRequest.current={signature,plan:value,requestId:crypto.randomUUID()};
      const result=await client.applyPlan(value.plan_id,value.plan_hash,saveRequest.current!.requestId);
      let status=result.status;
      for(let attempt=0;attempt<5&&['applying','planned','unknown'].includes(status);attempt++){
        await new Promise(resolve=>setTimeout(resolve,400));
        status=(await client.getJournalEntryForEnvironment(result.operation_id,environmentId)).status;
      }
      if(!['applied','no_op'].includes(status))throw new Error('Сохранение не подтверждено. Повторите сохранение, чтобы проверить ту же операцию.');
      saveRequest.current=null;
      if(version===requestVersion.current){removeDraft(key);setSaved(true);setReload(v=>v+1);onChanged?.();}
    } catch(cause){if(version===requestVersion.current)setError(cause instanceof Error?cause.message:'Не удалось сохранить настройки.');}
    finally{inFlight.current=false;setPending(false);onSavingChange?.(false);}
  }
  return <div className="service-config">
    {!forcedMode && <Tabs label="Настройки сервиса" value={mode} onChange={setMode} items={[{value:'routes',label:'Задержка сервиса'},{value:'calls',label:'Таймауты и повторы'}]}/>}
    {error&&<Message tone="danger" action={<Button onClick={()=>setReload(v=>v+1)}>Обновить настройки</Button>}>{error}</Message>}
    {saved&&!draft&&<p role="status" className="muted">Настройки сохранены.</p>}
    {loading?<LoadingState>Читаем настройки…</LoadingState>:!snapshot?null:<>
      {!calls.length&&!routes.length?<Message>Нет доступных настроек в этом разделе.</Message>:<Field label={mode==='routes'?'Эндпоинт этого сервиса':'Эндпоинт другого сервиса'}><Select disabled={disabled||pending} aria-label="Эндпоинт" value={key} onChange={value=>{setSelected(value);if(value.startsWith('route:'))onRouteChange?.(value.slice(6));}}>
        {calls.map(c=><option key={`call:${c.call_ref}`} value={`call:${c.call_ref}`} label={`${endpointLabel(c.target.service_id, `${c.target.method} ${c.target.route}`)}`}><Endpoint service={c.target.service_id} operation={`${c.target.method} ${c.target.route}`}/></option>)}
        {routes.map(r=><option key={`route:${r.route}`} value={`route:${r.route}`} label={`${endpointLabel(service, r.route)}`}><Endpoint service={service} operation={r.route}/></option>)}
      </Select></Field>}
      {draft&&canEdit&&<Badge tone="warning">Есть несохранённые изменения</Badge>}
      {conflict&&canEdit&&<Message tone="warning">Конфигурация на сервере изменилась. Черновик сохранён. Сверьте новые значения перед сохранением.</Message>}
      {readOnlyFields.length>0&&<section className="config-readonly" aria-label="Настройки только для просмотра">
        <div className="config-readonly-heading"><h3>{route?'Задержка сервиса':'Таймауты и повторы'}</h3><Badge>Только просмотр</Badge></div>
        <div className="config-readonly-values">
          {call&&[
            ['timeout_ms','Таймаут попытки',`${call.timeout_ms} мс`],
            ['max_attempts','Всего попыток, включая первую',call.max_attempts],
            ['backoff_ms','Пауза между попытками',`${call.backoff_ms} мс`],
            ['retry_on','Условия повтора',call.retry_on.join(', ')||'Без повторов'],
          ].filter(([field])=>readOnlyFields.includes(String(field))).map(([field,label,value])=><dl className="config-value" key={field}><dt>{label}</dt><dd>{value}</dd></dl>)}
          {route&&work&&<>
            <dl className="config-value"><dt>Задержка ответа</dt><dd>{work.kind==='fixed'?'Постоянная':'Переменная'}</dd></dl>
            {work.kind==='fixed'?<dl className="config-value"><dt>Время обработки</dt><dd>{work.delay_ms} мс</dd></dl>:<>
              <dl className="config-value"><dt>Обычное время (медиана)</dt><dd>{work.p50_ms} мс</dd></dl>
              <dl className="config-value"><dt>Время для 99% запросов</dt><dd>{work.p99_ms} мс</dd></dl>
            </>}
          </>}
        </div>
      </section>}

      {canEdit&&(call||route)&&<form className={route?'service-work-form':undefined} onSubmit={e=>{e.preventDefault();void prepare();}}>
        {call&&values&&<>
          <Setting label="Таймаут попытки, мс" editable={call.write_fields.includes('timeout_ms')} value={call.timeout_ms}><input required type="number" min="1" value={values.timeout} disabled={disabled||pending} onChange={e=>updateCall('timeout',e.target.value)}/></Setting>
          <Setting label="Всего попыток, включая первую" editable={call.write_fields.includes('max_attempts')} value={call.max_attempts}><input required type="number" min="1" max="10" value={values.attempts} disabled={disabled||pending} onChange={e=>updateCall('attempts',e.target.value)}/></Setting>
          <Setting label="Пауза между попытками, мс" editable={call.write_fields.includes('backoff_ms')} value={call.backoff_ms}><input required type="number" min="0" value={values.backoff} disabled={disabled||pending} onChange={e=>updateCall('backoff',e.target.value)}/></Setting>
          <Setting label={call.write_fields.includes('retry_on')?'Условия повтора (через запятую)':'Условия повтора'} editable={call.write_fields.includes('retry_on')} value={call.retry_on.join(', ') || 'Без повторов'}><input value={values.retry} disabled={disabled||pending} onChange={e=>updateCall('retry',e.target.value)}/></Setting>
        </>}
        {route&&work&&<>
          <Setting label="Задержка ответа" editable={canEditWork} value={work.kind==='fixed'?'Постоянная':'Переменная'}><Select value={work.kind} disabled={disabled||pending} onChange={v=>updateWork(v==='fixed'?{kind:'fixed',delay_ms:1}:{kind:'lognormal',p50_ms:1,p99_ms:2})}><option value="fixed">Постоянная</option><option value="lognormal">Переменная</option></Select></Setting>
          {work.kind==='fixed'?<Setting label="Время обработки, мс" editable={canEditWork} value={work.delay_ms}><input required type="number" min="0" value={work.delay_ms} disabled={disabled||pending} onChange={e=>updateWork({...work,delay_ms:Number(e.target.value)})}/></Setting>:<>
            <Setting label="Обычное время (медиана), мс" editable={canEditWork} value={work.p50_ms}><input required type="number" min="1" value={work.p50_ms} disabled={disabled||pending} onChange={e=>updateWork({...work,p50_ms:Number(e.target.value)})}/></Setting>
            <Setting label="Время для 99% запросов, мс" editable={canEditWork} value={work.p99_ms}><input required type="number" min={work.p50_ms} value={work.p99_ms} disabled={disabled||pending} onChange={e=>updateWork({...work,p99_ms:Number(e.target.value)})}/></Setting>
          </>}
        </>}
        {canEdit&&<Button type="submit" loading={pending} disabled={disabled||pending||conflict||!changed}>{pending ? (directSave?'Сохраняем…':'Готовим план…') : (directSave?'Применить':'Посмотреть изменения')}</Button>}
      </form>}
      {draft&&canEdit&&<><Button disabled={pending||disabled} onClick={()=>removeDraft(key)}>Отменить изменения</Button>{conflict&&<><section className="form-section"><h3>Текущие значения на сервере</h3><dl className="technical-grid">{call ? <><div><dt>Таймаут попытки</dt><dd>{call.timeout_ms} мс</dd></div><div><dt>Всего попыток</dt><dd>{call.max_attempts}</dd></div><div><dt>Пауза между попытками</dt><dd>{call.backoff_ms} мс</dd></div><div><dt>Условия повтора</dt><dd>{call.retry_on.join(', ') || 'Без повторов'}</dd></div></> : route?.work.kind === 'fixed' ? <div><dt>Время обработки</dt><dd>{route.work.delay_ms} мс</dd></div> : route && <><div><dt>Медиана</dt><dd>{route.work.p50_ms} мс</dd></div><div><dt>Время для 99% запросов</dt><dd>{route.work.p99_ms} мс</dd></div></>}</dl></section><Button onClick={()=>setDrafts(old=>({...old,[key]:{...old[key],revision:snapshot.revision}}))}>Значения проверены — использовать текущую версию</Button></>}</>}
    </>}
    {plan&&<PlanDialog localOnly={localOnly} key={plan.value.plan_id} client={client} plan={plan.value} canApply={plan.value.apply_enabled} onClose={()=>setPlan(null)} onApplied={entry=>{if(entry?.status==='applied'||entry?.status==='no_op'){removeDraft(plan.target);setPlan(null);setReload(v=>v+1);onChanged?.();}}}/>}
  </div>;
}
