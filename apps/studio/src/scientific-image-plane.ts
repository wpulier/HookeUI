/** Image planes change a 3D depiction, never native data or calibration. */
export type ImagePlane = { depiction: 'volume'|'plane'; position: number[]; normal: number[]; thickness: number };
export function imagePlane(raw: unknown): ImagePlane {
  const finite=(v:unknown):v is number=>typeof v==='number' && Number.isFinite(v) && Math.abs(v)<=Number.MAX_SAFE_INTEGER;
  const vector=(v:unknown):v is number[]=>Array.isArray(v) && v.length===3 && v.every(finite);
  const fail=():never=>{throw new Error('Choose volume or plane, finite data coordinates, a nonzero normal and nonnegative thickness.');};
  if(!raw || typeof raw!=='object' || Array.isArray(raw))return fail();
  const value=raw as Record<string,unknown>;
  if(Object.keys(value).sort().join(' ')!=='depiction normal position thickness' || typeof value.depiction!=='string' || !['volume','plane'].includes(value.depiction) ||
    !vector(value.position) || !vector(value.normal) || value.normal.reduce((sum,n)=>sum+n*n,0)===0 || !finite(value.thickness) || value.thickness<0)return fail();
  return structuredClone(value) as ImagePlane;
}
