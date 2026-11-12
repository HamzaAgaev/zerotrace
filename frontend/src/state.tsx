import { decodeStored, writeStored, uiStoragePrefix } from './storage';
import { createContext, useContext, useState, type Dispatch, type SetStateAction } from 'react';

export const StateScope = createContext('default');
const prefix = uiStoragePrefix;
export function usePersistentState<T>(name: string, initial: T | (() => T), validate?: (value: unknown) => value is T): [T, Dispatch<SetStateAction<T>>] {
  const scope = useContext(StateScope);
  const key = prefix + scope + ':' + name;
  const fallback = () => typeof initial === 'function' ? (initial as () => T)() : initial;
  const read = (): T => {
    try {
      return decodeStored(localStorage.getItem(key), fallback(), validate);
    } catch { /* Storage is optional. */ }
    return fallback();
  };
  const [entry, setEntry] = useState(() => ({ key, value: read() }));
  const value = entry.key === key ? entry.value : read();
  const update: Dispatch<SetStateAction<T>> = action => {
    setEntry(old => {
      const previous = old.key === key ? old.value : read();
      const next = typeof action === 'function' ? (action as (v: T) => T)(previous) : action;
      try { writeStored(localStorage, key, next); } catch { /* Keep the in-memory draft. */ }
      return { key, value: next };
    });
  };
  return [value, update];
}

export function usePersistentTab<T extends string>(name:string, initial:T, allowed:readonly T[], parameter='tab'): [T,(value:T)=>void] {
  const [saved,setSaved]=usePersistentState(name,initial);
  const params=new URLSearchParams(location.hash.split('?')[1]);
  const requested=params.get(parameter) as T|null;
  const value=requested&&allowed.includes(requested)?requested:allowed.includes(saved)?saved:initial;
  return [value,(next:T)=>{
    setSaved(next);
    const [path,query]=location.hash.split('?');
    const search=new URLSearchParams(query);search.set(parameter,next);
    location.hash=`${path}?${search}`;
  }];
}
