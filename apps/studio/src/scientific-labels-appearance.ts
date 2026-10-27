/** Labels display shared by frames, remembered views and controls. Label identifiers stay decimal strings. */
export const labelsRendering = [['translucent', 'Translucent'], ['iso_categorical', 'Isosurface (categorical)']] as const;
export const labelsGradient = [['fast', 'Fast'], ['smooth', 'Smooth']] as const;
export const MAX_DIRECT_COLORS = 256;
export const MAX_COLOR_SEED = 2 ** 32 - 2;
export type DirectColor = { label: string | null; color: [number, number, number, number] };
export type LabelsAppearance = {
  contour: number; selectedLabel: string; showSelectedLabel: boolean; colorMode: 'auto' | 'direct';
  backgroundValue: string; autoSource: 'original' | 'seeded' | 'other'; autoSeed: number | null;
  directColors: DirectColor[] | null; rendering: typeof labelsRendering[number][0]; isoGradientMode: typeof labelsGradient[number][0];
};
const LABEL = /^-?(?:0|[1-9][0-9]{0,19})$/;
export const labelId = (value: unknown): value is string => typeof value === 'string' && LABEL.test(value) && value !== '-0';
const unit = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1;

export function labelsAppearance(raw: unknown): LabelsAppearance {
  const fail = (message = 'Choose a contour width of zero or more, whole selected and background labels, automatic or direct colors and a supported 3D rendering.'): never => { throw new Error(message); };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fail();
  const v = raw as Record<string, unknown>;
  if (Object.keys(v).sort().join(' ') !== 'autoSeed autoSource backgroundValue colorMode contour directColors isoGradientMode rendering selectedLabel showSelectedLabel') return fail();
  // Contours above 2**53 cannot be typed here; the worker still refuses widths too slow to redraw.
  if (typeof v.contour !== 'number' || !Number.isSafeInteger(v.contour) || v.contour < 0) return fail('Contour width must be a whole number of pixels, zero or more.');
  if (!labelId(v.selectedLabel) || !labelId(v.backgroundValue)) return fail('Selected and background labels must be whole numbers.');
  if (typeof v.showSelectedLabel !== 'boolean') return fail();
  if (v.colorMode !== 'auto' && v.colorMode !== 'direct') return fail('Choose automatic or direct label colors.');
  if (!['original', 'seeded', 'other'].includes(String(v.autoSource))) return fail();
  if ((v.autoSource === 'seeded') !== (v.autoSeed !== null)) return fail('New automatic colors need a seed; original colors have none.');
  if (v.autoSeed !== null && (typeof v.autoSeed !== 'number' || !Number.isSafeInteger(v.autoSeed) || v.autoSeed < 0 || v.autoSeed > MAX_COLOR_SEED))
    return fail(`Automatic color seeds are whole numbers from 0 to ${MAX_COLOR_SEED}.`);
  const colors = v.directColors;
  if (colors !== null) {
    if (!Array.isArray(colors) || colors.length > MAX_DIRECT_COLORS) return fail(`Direct colors list at most ${MAX_DIRECT_COLORS} labels here. Use Python for larger color tables.`);
    const seen = new Set<string | null>();
    for (const row of colors) {
      if (!row || typeof row !== 'object' || Array.isArray(row) || Object.keys(row).sort().join(' ') !== 'color label' ||
          !(row.label === null || labelId(row.label)) || seen.has(row.label) || !Array.isArray(row.color) || row.color.length !== 4 || !row.color.every(unit))
        return fail('Each direct color needs one whole label (or none, for every other label) and four RGBA values from 0 to 1.');
      seen.add(row.label);
    }
  }
  if (v.colorMode === 'direct' && colors === null) return fail('This layer’s direct colors are too many to show here. Use Python to change them.');
  if (!labelsRendering.some(([key]) => key === v.rendering)) return fail('Choose translucent or isosurface 3D rendering for labels.');
  if (!labelsGradient.some(([key]) => key === v.isoGradientMode)) return fail('Choose fast or smooth isosurface shading.');
  return structuredClone(v) as LabelsAppearance;
}

/** Direct colors as editable text: one `label: r g b a` line each, `other` for every label not listed. */
export function directColorsText(colors: DirectColor[] | null): string {
  return (colors ?? []).map(row => `${row.label ?? 'other'}: ${row.color.join(' ')}`).join('\n');
}
export function parseDirectColors(text: string): DirectColor[] {
  const rows: DirectColor[] = [];
  for (const [index, line] of text.split('\n').entries()) {
    if (!line.trim()) continue;
    const match = /^\s*(other|-?(?:0|[1-9][0-9]{0,19}))\s*:\s*(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s*$/.exec(line);
    const color = match ? match.slice(2).map(Number) : [];
    if (!match || !color.every(unit)) throw new Error(`Line ${index + 1}: write a label (or other), a colon and four RGBA values from 0 to 1.`);
    rows.push({ label: match[1] === 'other' ? null : match[1], color: color as DirectColor['color'] });
  }
  return rows;
}
