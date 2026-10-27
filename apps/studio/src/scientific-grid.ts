export type ScientificGrid = { enabled: boolean; shape: [number,number]; stride: number; spacing: number };

export function scientificGrid(value: unknown): ScientificGrid {
  const fail=():never=>{throw new Error('Grid needs positive rows/columns (or -1 for automatic), a nonzero stride, and spacing from 0 to 1500.');};
  if (!value || typeof value!=='object' || Array.isArray(value)) return fail();
  const v=value as Record<string,unknown>;
  if (Object.keys(v).sort().join(' ')!=='enabled shape spacing stride' || typeof v.enabled!=='boolean' ||
      !Array.isArray(v.shape) || v.shape.length!==2 || !v.shape.every(n=>Number.isSafeInteger(n)&&(n===-1||n>0)) ||
      typeof v.stride!=='number' || !Number.isSafeInteger(v.stride) || v.stride===0 ||
      typeof v.spacing!=='number' || !Number.isFinite(v.spacing) || v.spacing<0 || v.spacing>1500) return fail();
  return structuredClone(v) as ScientificGrid;
}
