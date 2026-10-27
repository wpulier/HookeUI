/** Bounded plugin display metadata. This never grants browser interaction authority. */
export type NativeControl = {
  id: string; parent: string | null; role: string; name: string; description: string; value: string;
  rect: [number, number, number, number]; disabled: boolean; focused: boolean; focusable: boolean;
  invisible: boolean; checked: boolean; checkable: boolean; readOnly: boolean;
};
export type NativePanelFrame = {
  format: 'scientific-native-panel-v1'; panel: string; frame: string; width: number; height: number;
  pixelRatio: 1; focus: string | null; accessibility: NativeControl[];
};
export type NativePanelCommand = { panel: string; frame: string; op: string; value: unknown };
export const nativeKeys = new Set(['Tab', 'Backtab', 'Backspace', 'Delete', 'Return', 'Enter', 'Escape',
  'Space', 'Left', 'Right', 'Up', 'Down', 'Home', 'End', 'PageUp', 'PageDown']);

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const keys = (v: Record<string, unknown>, expected: string) => Object.keys(v).sort().join(' ') === expected.split(' ').sort().join(' ');
const text = (v: unknown): v is string => typeof v === 'string' && v.length <= 1000 && !/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v);
const token = (v: unknown, length: number) => typeof v === 'string' && new RegExp(`^[a-f0-9]{${length}}$`).test(v);
export function nativePanelFrame(value: unknown): NativePanelFrame {
  const fail = (): never => { throw new Error('The plugin panel returned invalid display information. Refresh its panel before continuing.'); };
  if (!object(value) || !keys(value, 'format panel frame width height pixelRatio focus accessibility') ||
      value.format !== 'scientific-native-panel-v1' || !token(value.panel, 32) || !token(value.frame, 64) || value.pixelRatio !== 1 ||
      ![value.width, value.height].every(n => Number.isSafeInteger(n) && Number(n) >= 1 && Number(n) <= 1024) ||
      value.focus !== null && !text(value.focus) || !Array.isArray(value.accessibility) || !value.accessibility.length || value.accessibility.length > 512) return fail();
  if (new TextEncoder().encode(JSON.stringify(value)).length > (64 << 10)) return fail();
  const depths = new Map<string, number>();
  for (const row of value.accessibility) {
    if (!object(row) || !keys(row, 'id parent role name description value rect disabled focused focusable invisible checked checkable readOnly') ||
        typeof row.id !== 'string' || !/^[0-9]{1,20}$/.test(row.id) || depths.has(row.id) ||
        typeof row.role !== 'string' || !/^[A-Za-z][A-Za-z0-9_]{0,79}$/.test(row.role) ||
        ![row.name, row.description, row.value].every(text) ||
        !['disabled','focused','focusable','invisible','checked','checkable','readOnly'].every(k => typeof row[k] === 'boolean') ||
        !Array.isArray(row.rect) || row.rect.length !== 4 || !row.rect.every(n => Number.isSafeInteger(n) && Math.abs(n) <= 32768) || row.rect[2] < 0 || row.rect[3] < 0) return fail();
    if (!depths.size ? row.parent !== null : typeof row.parent !== 'string' || !depths.has(row.parent)) return fail();
    const depth = row.parent === null ? 0 : depths.get(String(row.parent))! + 1;
    if (depth > 16) return fail();
    depths.set(row.id, depth);
  }
  return structuredClone(value) as NativePanelFrame;
}
