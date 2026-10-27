/** Native marker rendering, independent of point coordinates and per-point styling. */
export type PointAppearance = {shading:'none'|'spherical';projection:'none'|'all'|'rescale_linear'|'rescale_spherical';antialiasing:number;canvasLimits:number[]};
export function pointAppearance(raw:unknown):PointAppearance {
  const fail=():never=>{throw new Error('Choose native point shading, slice visibility, nonnegative antialiasing and ordered canvas size limits.');};
  if(!raw||typeof raw!=='object'||Array.isArray(raw))return fail();
  const value=raw as Record<string,unknown>;
  const finite=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n);
  if(Object.keys(value).sort().join(' ')!=='antialiasing canvasLimits projection shading'||
    typeof value.shading!=='string'||!['none','spherical'].includes(value.shading)||typeof value.projection!=='string'||!['none','all','rescale_linear','rescale_spherical'].includes(value.projection)||
    !finite(value.antialiasing)||value.antialiasing<0||!Array.isArray(value.canvasLimits)||value.canvasLimits.length!==2||
    !value.canvasLimits.every(finite)||value.canvasLimits[0]<0||value.canvasLimits[0]>value.canvasLimits[1])return fail();
  return structuredClone(raw) as PointAppearance;
}
