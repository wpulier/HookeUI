/** Display-only aggregation; saved native values and calibration stay independent. */
export type SliceMargins = {left:number[];right:number[]};
export const imageProjections = ['none','sum','mean','max','min'] as const;
export type ImageProjection = typeof imageProjections[number];
export function sliceMargins(value:unknown,ndim:number):SliceMargins {
  const fail=():never=>{throw new Error('Enter a finite, nonnegative slice margin on each side of every axis, in world coordinates.');};
  if(!value||typeof value!=='object'||Array.isArray(value))return fail();
  const v=value as Record<string,unknown>;
  if(Object.keys(v).sort().join(' ')!=='left right'||['left','right'].some(key=>!Array.isArray(v[key])||v[key].length!==ndim||v[key].some((n:unknown)=>typeof n!=='number'||!Number.isFinite(n)||n<0)))return fail();
  return structuredClone(value) as SliceMargins;
}
export function imageProjection(value:unknown):ImageProjection {
  if(typeof value!=='string'||!imageProjections.some(mode=>mode===value))throw new Error('Choose current slice, sum, mean, maximum or minimum for this Image layer.');
  return value as ImageProjection;
}
