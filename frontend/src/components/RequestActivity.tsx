import { useEffect,useState,useSyncExternalStore } from 'react';
import { getPendingRequests,subscribeRequests } from '../api/activity';
export function RequestActivity(){
  const active=useSyncExternalStore(subscribeRequests,getPendingRequests,()=>0)>0;
  const [visible,setVisible]=useState(false);
  useEffect(()=>{if(!active){setVisible(false);return;}const timer=window.setTimeout(()=>setVisible(true),200);return()=>window.clearTimeout(timer);},[active]);
  return active&&visible ? <div className="request-activity" role="status"><span className="spinner" aria-hidden="true"/>Выполняется запрос…</div>:null;
}
