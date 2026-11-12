import { useId } from 'react';
import type { ReactNode } from 'react';
/** One shared navigation pattern for peer views. Arrow keys move focus and selection. */
export function Tabs<T extends string>({ label, value, onChange, items, fullWidth = true }: { label:string; value:T; onChange:(value:T)=>void; items:{value:T;label:ReactNode;panelId?:string}[]; fullWidth?: boolean }) {
  const id=useId();
  return <div className={`tabs view-tabs${fullWidth ? ' tabs--full-width' : ''}`} role="tablist" aria-label={label}>{items.map((item,index)=><button key={item.value} type="button" role="tab" id={item.panelId ? `${item.panelId}-tab` : `${id}-${item.value}-tab`} aria-controls={item.panelId} aria-selected={value===item.value} tabIndex={value===item.value?0:-1} onClick={()=>onChange(item.value)} onKeyDown={event=>{
    let next=index;
    if(event.key==='ArrowRight') next=(index+1)%items.length;
    else if(event.key==='ArrowLeft') next=(index+items.length-1)%items.length;
    else if(event.key==='Home') next=0;
    else if(event.key==='End') next=items.length-1;
    else return;
    event.preventDefault(); onChange(items[next].value);
    (event.currentTarget.parentElement?.children[next] as HTMLButtonElement|undefined)?.focus();
  }}>{item.label}</button>)}</div>;
}
