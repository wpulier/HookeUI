/** Native mesh display; no vertex, face, measurement or transform edits. */
export type SurfaceLine = {visible:boolean;width:number;color:number[]};
export type SurfaceAppearance = {shading:'none'|'flat'|'smooth';contrastLimits:number[];autoContrast:boolean;gamma:number;
  wireframe:SurfaceLine;faceNormals:SurfaceLine & {length:number};vertexNormals:SurfaceLine & {length:number}};
export function surfaceAppearance(raw:unknown):SurfaceAppearance {
  const fail=():never=>{throw new Error('Choose valid surface shading, positive gamma, ordered contrast limits and finite line settings. RGBA colors must be between 0 and 1.');};
  const obj=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
  const finite=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n);
  if(!obj(raw)||Object.keys(raw).sort().join(' ')!=='autoContrast contrastLimits faceNormals gamma shading vertexNormals wireframe'||
    typeof raw.shading!=='string'||!['none','flat','smooth'].includes(raw.shading)||typeof raw.autoContrast!=='boolean'||!finite(raw.gamma)||raw.gamma<=0||
    !Array.isArray(raw.contrastLimits)||raw.contrastLimits.length!==2||!raw.contrastLimits.every(finite)||raw.contrastLimits[0]>=raw.contrastLimits[1])return fail();
  for(const key of ['wireframe','faceNormals','vertexNormals']){
    const row=raw[key],normal=key!=='wireframe';
    if(!obj(row)||Object.keys(row).sort().join(' ')!==(normal?'color length visible width':'color visible width')||typeof row.visible!=='boolean'||!finite(row.width)||row.width<0||normal&&!finite(row.length)||
      !Array.isArray(row.color)||row.color.length!==4||!row.color.every(n=>finite(n)&&n>=0&&n<=1))return fail();
  }
  return structuredClone(raw) as SurfaceAppearance;
}
