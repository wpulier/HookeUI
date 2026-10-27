/** Native display controls; coordinates, features and lineage stay independent. */
export type PathAppearance =
  | {kind:'vectors';length:number;width:number;style:'line'|'triangle'|'arrow';projection:'none'|'all'|'fade'}
  | {kind:'tracks';width:number;tail:number;head:number;showIds:boolean;showTail:boolean;showGraph:boolean;hideCompleted:boolean};
export function pathAppearance(raw:unknown,kind?:string):PathAppearance {
  const fail=():never=>{throw new Error('Choose valid Vector or Track display settings. Track width is 0.5–20 pixels and past/future lengths are nonnegative whole time steps.');};
  if(!raw||typeof raw!=='object'||Array.isArray(raw))return fail();
  const v=raw as Record<string,unknown>;
  const finite=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=Number.MAX_SAFE_INTEGER;
  if(kind!==undefined&&kind!==v.kind||!finite(v.width)||v.width<0)return fail();
  if(v.kind==='vectors'){
    if(Object.keys(v).sort().join(' ')!=='kind length projection style width'||!finite(v.length)||
      !['line','triangle','arrow'].includes(String(v.style))||!['none','all','fade'].includes(String(v.projection))||typeof v.style!=='string'||typeof v.projection!=='string')return fail();
  }else if(v.kind==='tracks'){
    if(Object.keys(v).sort().join(' ')!=='head hideCompleted kind showGraph showIds showTail tail width'||v.width<.5||v.width>20||
      !['tail','head'].every(key=>Number.isSafeInteger(v[key])&&Number(v[key])>=0)||
      !['showIds','showTail','showGraph','hideCompleted'].every(key=>typeof v[key]==='boolean'))return fail();
  }else return fail();
  return structuredClone(v) as PathAppearance;
}
