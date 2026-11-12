import { downloadJson } from '../analysis';
import { usePersistentState } from '../state';
import { Endpoint, endpointLabel } from './HttpText';
import { Select } from './Select';
import { useEffect, useRef, useState } from 'react';
import type { ChangePlan, Client, ConfigSnapshot, RouteWork } from '../api/types';
import { demoServiceCapabilities } from '../api/fixtures';
import { Badge, Button, Field, LoadingState, Message } from './ui';
import { PlanDialog } from './PlanDialog';

type CallValues = { timeout: string; attempts: string; backoff: string; retry: string };
type Draft = { revision: string; call?: CallValues; work?: RouteWork };
export function ServiceConfig({ client, service, environmentId, demo = false, localOnly = false, disabled = false, maxAttempts = 3, onSave = () => undefined, onChanged, initialCall }: { initialCall?: { callee:string; operation:string }; client: Client; service: string; environmentId: string; demo?: boolean; localOnly?: boolean; disabled?: boolean; maxAttempts?: number; onSave?: (value: number) => void; onChanged?: () => void }) {
  const context = `config:${environmentId}:${service}`;
  const [demoDraft, setDemoDraft] = usePersistentState(`${context}:demo`, String(maxAttempts));
  const [snapshot, setSnapshot] = useState<ConfigSnapshot | null>(null);
  const [loading, setLoading] = useState(!demo);
  const [error, setError] = useState('');
  const [plan, setPlan] = useState<{value: ChangePlan; target: string} | null>(null);
  const [reload, setReload] = useState(0);
  const [selected, setSelected] = usePersistentState(`${context}:selected`, '');
  const [drafts, setDrafts] = usePersistentState<Record<string,Draft>>(`${context}:drafts`, {});
  const [budgets, setBudgets] = usePersistentState<Record<string,string>>(`${context}:budgets`, {});
  const [pending, setPending] = useState(false);
  const inFlight = useRef(false);
  const requestVersion = useRef(0);
  const openedCall = useRef('');
  useEffect(() => {
    const version = ++requestVersion.current;
    if (demo) return;
    const controller = new AbortController();
    setLoading(true); setSnapshot(null); setPlan(null); setError('');
    client.getConfig(service, environmentId, controller.signal).then(config => {
      if (!controller.signal.aborted) setSnapshot(config);
    }).catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : 'Не удалось прочитать настройки.'); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => { controller.abort(); if(requestVersion.current === version) requestVersion.current++; };
  }, [client, demo, service, environmentId, reload]);
  useEffect(()=>{
    if(!initialCall||!snapshot)return;
    const target=JSON.stringify([service,environmentId,initialCall.callee,initialCall.operation]);
    if(openedCall.current===target)return;
    const match=snapshot.calls.find(c=>c.target.service_id===initialCall.callee&&`${c.target.method} ${c.target.route}`===initialCall.operation);
    if(match){openedCall.current=target;setSelected(`call:${match.call_ref}`);}
  },[initialCall?.callee,initialCall?.operation,snapshot,service,environmentId]);
  const key = selected || (snapshot?.calls[0] ? `call:${snapshot.calls[0].call_ref}` : snapshot?.routes[0] ? `route:${snapshot.routes[0].route}` : '');
  const call = snapshot?.calls.find(c=>`call:${c.call_ref}`===key);
  const route = snapshot?.routes.find(r=>`route:${r.route}`===key);
  const draft = drafts[key];
  const values = draft?.call ?? (call ? {timeout:String(call.timeout_ms),attempts:String(call.max_attempts),backoff:String(call.backoff_ms),retry:call.retry_on.join(', ')} : undefined);
  const work = draft?.work ?? route?.work;
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
    inFlight.current=true;setPending(true);setError('');
    const version=requestVersion.current;
    try {
      const value=await client.createManualPlan({environment_id:environmentId,service_id:service,expected_config_revision:draft?.revision ?? snapshot.revision,...(call?{call_ref:call.call_ref}:{route:route!.route}),set:changes});
      if(version===requestVersion.current)setPlan({value,target:key});
    } catch(cause){if(version===requestVersion.current)setError(cause instanceof Error?cause.message:'Не удалось подготовить план.');}
    finally{inFlight.current=false;setPending(false);}
  }
  if(demo){
    const capabilities=demoServiceCapabilities[service];
    if(!capabilities?.read_config)return <p className="muted">Настройки недоступны в демонстрационном наборе.</p>;
    return <form className="service-config" onSubmit={e=>{e.preventDefault();onSave(Number(demoDraft));}}><Badge tone="warning">Локальная демонстрация</Badge><Field label="Эндпоинт"><Select value="payment" onChange={()=>undefined}><option value="payment" label="POST payment/charge"><Endpoint service="payment" operation="POST /charge"/></option></Select></Field><Field label="Всего попыток, включая первую"><input type="number" required min="1" max="10" value={demoDraft} onChange={e=>setDemoDraft(e.target.value)}/></Field><Button type="submit" disabled={Number(demoDraft)===maxAttempts}>Сохранить в примере</Button></form>;
  }
  return <div className="service-config">
    <h3>Настройки эндпоинта</h3>
    {error&&<Message tone="danger" action={<Button onClick={()=>setReload(v=>v+1)}>Обновить настройки</Button>}>{error}</Message>}
    {loading?<LoadingState>Читаем настройки…</LoadingState>:!snapshot?<p>Настройки недоступны.</p>:<>
      {!snapshot.calls.length&&!snapshot.routes.length?<Message>У сервиса нет доступных настроек эндпоинтов.</Message>:<Field label="Эндпоинт"><Select aria-label="Эндпоинт" value={key} onChange={setSelected}>
        {snapshot.calls.map(c=><option key={`call:${c.call_ref}`} value={`call:${c.call_ref}`} label={`${endpointLabel(c.target.service_id, `${c.target.method} ${c.target.route}`)}`}><Endpoint service={c.target.service_id} operation={`${c.target.method} ${c.target.route}`}/></option>)}
        {snapshot.routes.map(r=><option key={`route:${r.route}`} value={`route:${r.route}`} label={`${endpointLabel(service, r.route)}`}><Endpoint service={service} operation={r.route}/></option>)}
      </Select></Field>}
      {selected&&!call&&!route&&<Message>Выбранный эндпоинт больше недоступен. Выберите другой.</Message>}
      {draft&&<Badge tone="warning">Есть несохранённые изменения</Badge>}
      {conflict&&<Message tone="warning">Конфигурация на сервере изменилась. Черновик сохранён. Сверьте новые значения перед подготовкой плана.</Message>}
      {(call||route)&&<form onSubmit={e=>{e.preventDefault();void prepare();}}>
        {call&&values&&<>
          <Field label="Таймаут попытки, мс"><input required type="number" min="1" value={values.timeout} disabled={disabled||!call.write_fields.includes('timeout_ms')} onChange={e=>updateCall('timeout',e.target.value)}/></Field>
          <Field label="Всего попыток, включая первую"><input required type="number" min="1" max="10" value={values.attempts} disabled={disabled||!call.write_fields.includes('max_attempts')} onChange={e=>updateCall('attempts',e.target.value)}/></Field>
          <Field label="Пауза между попытками, мс"><input required type="number" min="0" value={values.backoff} disabled={disabled||!call.write_fields.includes('backoff_ms')} onChange={e=>updateCall('backoff',e.target.value)}/></Field>
          <Field label="Условия повтора (через запятую)"><input value={values.retry} disabled={disabled||!call.write_fields.includes('retry_on')} onChange={e=>updateCall('retry',e.target.value)}/></Field>
          <section className="form-section"><h3>Время на попытки</h3><p className="small-text">Максимальное время: {Number(values.timeout)>0&&Number(values.attempts)>0&&Number(values.backoff)>=0?(Number(values.timeout)*Number(values.attempts)+Number(values.backoff)*(Number(values.attempts)-1)).toLocaleString('ru-RU')+' мс':'укажите корректные значения'}.</p><Field label="Доступное время ожидания, мс (для проверки)"><input type="number" min="1" value={budgets[key] ?? ''} placeholder="Укажите, если известно" onChange={e=>setBudgets(old=>({...old,[key]:e.target.value}))}/></Field>{Number(budgets[key])>0&&<p>Запас времени: {(Number(budgets[key])-(Number(values.timeout)*Number(values.attempts)+Number(values.backoff)*(Number(values.attempts)-1))).toLocaleString('ru-RU')} мс. Отрицательное значение означает превышение доступного времени.</p>}<Button type="button" onClick={()=>downloadJson('zerotrace-call-draft.json',{kind:'configuration-preview',environment_id:environmentId,service,call_ref:call.call_ref,expected_revision:draft?.revision??snapshot.revision,values,available_time_ms:budgets[key]||null,applied:false})}>Скачать черновик</Button><p className="muted small-text">Оценка учитывает таймауты всех попыток и паузы между ними.</p></section>
        </>}
        {route&&work&&<>
          <Field label="Время обработки запроса"><Select value={work.kind} disabled={disabled||!route.write_fields.includes('work')} onChange={v=>updateWork(v==='fixed'?{kind:'fixed',delay_ms:1}:{kind:'lognormal',p50_ms:1,p99_ms:2})}><option value="fixed">Постоянное</option><option value="lognormal">Меняется от запроса к запросу</option></Select></Field>
          {work.kind==='fixed'?<Field label="Время обработки, мс"><input required type="number" min="0" value={work.delay_ms} disabled={disabled||!route.write_fields.includes('work')} onChange={e=>updateWork({...work,delay_ms:Number(e.target.value)})}/></Field>:<>
            <Field label="Медиана, мс"><input required type="number" min="1" value={work.p50_ms} disabled={disabled||!route.write_fields.includes('work')} onChange={e=>updateWork({...work,p50_ms:Number(e.target.value)})}/></Field>
            <Field label="99-й перцентиль, мс"><input required type="number" min={work.p50_ms} value={work.p99_ms} disabled={disabled||!route.write_fields.includes('work')} onChange={e=>updateWork({...work,p99_ms:Number(e.target.value)})}/></Field><p className="muted">99% запросов обрабатываются за время 99-го перцентиля или быстрее. Используется логнормальное распределение.</p>
          </>}
        </>}
        <Button type="submit" disabled={disabled||pending||conflict||!changed}>{pending?'Готовим план…':'Подготовить план'}</Button>
      </form>}
      {draft&&<><Button onClick={()=>removeDraft(key)}>Сбросить черновик выбранного эндпоинта</Button>{conflict&&<><section className="form-section"><h3>Текущие значения на сервере</h3><dl className="technical-grid">{call ? <><div><dt>Таймаут попытки</dt><dd>{call.timeout_ms} мс</dd></div><div><dt>Всего попыток</dt><dd>{call.max_attempts}</dd></div><div><dt>Пауза между попытками</dt><dd>{call.backoff_ms} мс</dd></div><div><dt>Условия повтора</dt><dd>{call.retry_on.join(', ') || 'Без повторов'}</dd></div></> : route?.work.kind === 'fixed' ? <div><dt>Время обработки</dt><dd>{route.work.delay_ms} мс</dd></div> : route && <><div><dt>Медиана</dt><dd>{route.work.p50_ms} мс</dd></div><div><dt>99-й перцентиль</dt><dd>{route.work.p99_ms} мс</dd></div></>}</dl></section><Button onClick={()=>setDrafts(old=>({...old,[key]:{...old[key],revision:snapshot.revision}}))}>Значения проверены — использовать текущую версию</Button></>}</>}
    </>}
    {plan&&<PlanDialog localOnly={localOnly} key={plan.value.plan_id} client={client} plan={plan.value} canApply={plan.value.apply_enabled} onClose={()=>setPlan(null)} onApplied={entry=>{if(entry?.status==='applied'||entry?.status==='no_op'){removeDraft(plan.target);setPlan(null);setReload(v=>v+1);onChanged?.();}}}/>}
  </div>;
}
