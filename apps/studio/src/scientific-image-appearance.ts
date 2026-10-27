/** Image display settings shared by frames, remembered views and controls. */
export const imageRendering = [
  ['translucent', 'Translucent'], ['additive', 'Additive'], ['iso', 'Isosurface'],
  ['mip', 'Maximum intensity'], ['minip', 'Minimum intensity'],
  ['attenuated_mip', 'Attenuated maximum intensity'], ['average', 'Average intensity'],
] as const;
export type ImageAppearance = {
  contrastLimits: [number, number]; gamma: number; rendering: typeof imageRendering[number][0];
  isoThreshold: number; attenuation: number; autoContrast: boolean;
};
export function imageAppearance(raw: unknown): ImageAppearance {
  const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
  const fail = (): never => { throw new Error('Choose increasing contrast limits, a positive gamma, a volume rendering mode and finite rendering settings.'); };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fail();
  const v = raw as Record<string, unknown>;
  if (Object.keys(v).sort().join(' ') !== 'attenuation autoContrast contrastLimits gamma isoThreshold rendering' ||
      !Array.isArray(v.contrastLimits) || v.contrastLimits.length !== 2 || !v.contrastLimits.every(finite) ||
      v.contrastLimits[0] >= v.contrastLimits[1] || !finite(v.gamma) || v.gamma <= 0 ||
      !imageRendering.some(([key])=>key===v.rendering) || !finite(v.isoThreshold) ||
      !finite(v.attenuation) || v.attenuation < 0 || typeof v.autoContrast !== 'boolean') return fail();
  return structuredClone(v) as ImageAppearance;
}
