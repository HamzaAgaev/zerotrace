let pending=0;
const listeners=new Set<()=>void>();
export const subscribeRequests=(listener:()=>void)=>{listeners.add(listener);return()=>{listeners.delete(listener);};};
export const getPendingRequests=()=>pending;
export async function trackRequest<T>(request:()=>Promise<T>):Promise<T> {
  pending++; listeners.forEach(listener=>listener());
  try{return await request();}
  finally{pending--;listeners.forEach(listener=>listener());}
}
