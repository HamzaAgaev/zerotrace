/** Only our versioned UI records are read; API data remains server-owned. */
export const uiStoragePrefix = 'zerotrace.ui.v1:';
export function decodeStored<T>(raw: string | null, fallback: T, validate?: (value: unknown) => value is T): T {
  if (!raw) return fallback;
  try {
    const record = JSON.parse(raw);
    const value = record.value;
    if (!record || !Number.isFinite(record.updated)) return fallback;
    if (validate) return validate(value) ? value : fallback;
    if (fallback === undefined) return typeof value === 'string' ? value as T : fallback;
    if (value === null || typeof value !== typeof fallback || Array.isArray(value)!==Array.isArray(fallback)) return fallback;
    if (typeof value === 'number' && !Number.isFinite(value)) return fallback;
    if (Array.isArray(fallback) && !value.every((item: unknown) =>
      fallback.length ? matchesShape(item, fallback[0]) : typeof item === 'string')) return fallback;
    if(typeof fallback==='object'&&!Array.isArray(fallback)) {
      for(const [key,base] of Object.entries(fallback as object)) {
        if(base!==undefined && base!==null && !matchesShape(value[key], base))return fallback;
      }
    }
    return value;
  } catch { return fallback; }
}
export function encodeStored(value:unknown) { return JSON.stringify({value,updated:Date.now()}); }

/** Bound expendable navigation history; never evict configuration drafts. */
export function writeStored(storage: Storage, key:string, value:unknown) {
  const history:string[]=[];
  for(let i=0;i<storage.length;i++) {
    const candidate=storage.key(i);
    if(candidate?.startsWith(uiStoragePrefix) && (candidate.includes(':view:') || candidate.includes(':trace:')))history.push(candidate);
  }
  if(history.length>500) {
    const dated=history.map(k=>{try{return {key:k,time:JSON.parse(storage.getItem(k)??'{}').updated??0};}catch{return {key:k,time:0};}}).sort((a,b)=>a.time-b.time);
    for(const record of dated.slice(0,history.length-400))if(record.key!==key)storage.removeItem(record.key);
  }
  storage.setItem(key,encodeStored(value));
}

function matchesShape(value: unknown, base: unknown): boolean {
  if (base === null || base === undefined) return value === base;
  if (typeof value !== typeof base || value === null) return false;
  if (typeof base === 'number') return Number.isFinite(value);
  if (Array.isArray(base)) return Array.isArray(value) &&
    (base.length === 0 || value.every(item => matchesShape(item, base[0])));
  if (typeof base === 'object') return !Array.isArray(value) &&
    Object.entries(base).every(([key, sample]) => matchesShape((value as Record<string, unknown>)[key], sample));
  return true;
}
