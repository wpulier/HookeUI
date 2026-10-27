/** Native clipping changes presentation, never the source's values or calibration. */
export const MAX_CLIPPING_PLANES=64;
export type ClippingPlane = {position:number[];normal:number[];enabled:boolean};
export function clippingPlanes(raw:unknown):ClippingPlane[] {
  const vector=(v:unknown):v is number[]=>Array.isArray(v)&&v.length===3&&v.every(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=Number.MAX_SAFE_INTEGER);
  const fail=():never=>{throw new Error('Each clipping plane needs finite coordinates, a nonzero normal and an enabled choice.');};
  if(!Array.isArray(raw))return fail();
  if(raw.length>MAX_CLIPPING_PLANES)throw new Error(`This view supports at most ${MAX_CLIPPING_PLANES} clipping planes per layer. Remove planes before applying.`);
  for(const value of raw){
    if(!value||typeof value!=='object'||Array.isArray(value)||Object.keys(value).sort().join(' ')!=='enabled normal position'||
      !vector(value.position)||!vector(value.normal)||value.normal.reduce((sum:number,n:number)=>sum+n*n,0)===0||typeof value.enabled!=='boolean')return fail();
  }
  return structuredClone(raw) as ClippingPlane[];
}
