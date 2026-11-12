import { useContext, useEffect } from 'react';
import { StateScope } from '../state';
import { decodeStored, writeStored, uiStoragePrefix } from '../storage';
/** Restore presentation only, never server operations or form submission. */
export function ViewMemory({route}:{route:string}) {
  const scope=useContext(StateScope);
  useEffect(()=>{
    const key=uiStoragePrefix+scope+':view:'+route;
    const empty={positions:{} as Record<string,{x:number;y:number}>,details:{} as Record<string,boolean>};
    let memory=empty;
    try{memory=decodeStored(localStorage.getItem(key),empty);}catch{/* optional storage */}
    const restored=new WeakSet<Element>();let touched=false;let pageRestored=false;let disposed=false;let timer:number|undefined;let retryFrame:number|undefined;let retries=0;
    const scrollSelector='.table-scroll,.timeline-scroll,.service-graph-scroll';
    /** Panel headings keep several scroll areas and disclosures on one page distinct. */
    const panelKey=(el:Element)=>el.closest('.panel')?.querySelector('.panel-heading h2, h2')?.textContent?.trim()||el.closest('main')?.id||'';
    const identity=(el:Element)=>{
      const explicit=el.getAttribute('aria-label')||el.id;
      if(explicit && !el.classList.contains('table-scroll'))return explicit;
      const panel=el.closest('.panel');
      const peers=panel?Array.from(panel.querySelectorAll(scrollSelector)):[];
      return `${panelKey(el)}|${el.className}|${Math.max(0,peers.indexOf(el))}`;
    };
    const detailKey=(el:Element)=>{
      const summary=el.querySelector(':scope > summary')?.textContent?.trim()||'';
      return panelKey(el)?`${panelKey(el)}|${summary}`:summary;
    };
    const persist=()=>{try{writeStored(localStorage,key,memory);}catch{/* optional storage */}};
    const saveSoon=()=>{window.clearTimeout(timer);timer=window.setTimeout(persist,150);};
    /** Content and fonts can settle after the first paint; retry a few frames without waiting for another mutation. */
    const restore=()=>{
      if (disposed) return;
      if (retryFrame !== undefined) window.cancelAnimationFrame(retryFrame);
      let pending=false;
      document.querySelectorAll<HTMLElement>(scrollSelector).forEach(el=>{
        if(restored.has(el))return;const position=memory.positions[identity(el)];
        if(position && Number.isFinite(position.x) && Number.isFinite(position.y)){
          if(el.scrollWidth-el.clientWidth < position.x || el.scrollHeight-el.clientHeight < position.y){pending=true;return;}
          el.scrollLeft=position.x;el.scrollTop=position.y;
        }
        restored.add(el);
      });
      document.querySelectorAll<HTMLDetailsElement>('main details').forEach(el=>{
        if(restored.has(el)||el.classList.contains('metric-info'))return;
        const name=detailKey(el);
        if(name in memory.details)el.open=memory.details[name];restored.add(el);
      });
      const page=memory.positions.page ?? {x:0,y:0};
      if(page&&!touched&&!pageRestored&&Number.isFinite(page.x)&&Number.isFinite(page.y)){
        if(document.documentElement.scrollHeight>=page.y+window.innerHeight){window.scrollTo(page.x,page.y);pageRestored=true;}
        else pending=true;
      }
      if(pending&&!touched&&retries++<30)retryFrame=window.requestAnimationFrame(restore);
    };
    const scroll=(event:Event)=>{
      if(!touched)return;
      const el=event.target;
      if(el===document){if(!touched)return;memory.positions.page={x:window.scrollX,y:window.scrollY};}
      else if(el instanceof HTMLElement&&el.matches('.table-scroll,.timeline-scroll,.service-graph-scroll'))memory.positions[identity(el)]={x:el.scrollLeft,y:el.scrollTop};
      else return;saveSoon();
    };
    const touch=(event:Event)=>{
      if(event instanceof KeyboardEvent && !['ArrowDown','ArrowUp','ArrowLeft','ArrowRight','PageDown','PageUp','Home','End',' '].includes(event.key))return;
      touched=true;
      const el=event.target instanceof Element ? event.target.closest(scrollSelector) : null;
      if(el)restored.add(el);
    };
    const toggle=(event:Event)=>{const el=event.target;if(el instanceof HTMLDetailsElement&&el.closest('main')&&!el.classList.contains('metric-info')){memory.details[detailKey(el)]=el.open;saveSoon();}};
    const observer=new MutationObserver(restore);observer.observe(document.getElementById('main-content')??document.body,{childList:true,subtree:true});
    document.addEventListener('scroll',scroll,true);document.addEventListener('toggle',toggle,true);window.addEventListener('wheel',touch,{passive:true});window.addEventListener('touchstart',touch,{passive:true});window.addEventListener('keydown',touch);
    const resize=new ResizeObserver(restore);
    resize.observe(document.getElementById('main-content')??document.body);
    window.addEventListener('pointerdown',touch,{passive:true});
    restore();
    return()=>{disposed=true;if(retryFrame!==undefined)window.cancelAnimationFrame(retryFrame);resize.disconnect();window.removeEventListener('pointerdown',touch);observer.disconnect();window.clearTimeout(timer);persist();document.removeEventListener('scroll',scroll,true);document.removeEventListener('toggle',toggle,true);window.removeEventListener('wheel',touch);window.removeEventListener('touchstart',touch);window.removeEventListener('keydown',touch);};
  },[scope,route]);
  return null;
}
