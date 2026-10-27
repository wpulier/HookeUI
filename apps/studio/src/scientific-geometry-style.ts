/** Points and Shapes styling: markers, widths, colors by value or by feature, text content and z-order.
 *  The worker applies each through napari's public setters; coordinates and feature values never change.
 *  `null` keeps each element's own value (sizes, symbols, widths or direct colors that differ, set in Python). */
export const pointSymbols = ['arrow', 'clobber', 'cross', 'diamond', 'disc', 'hbar', 'ring', 'square', 'star', 'tailed_arrow',
  'triangle_down', 'triangle_up', 'vbar', 'x'] as const;
export const MAX_CYCLE = 64;
export type Rgba = [number, number, number, number];
export type ColorSpec =
  | {mode: 'direct'; color: Rgba | null}
  | {mode: 'cycle'; feature: string; cycle: Rgba[] | null}
  | {mode: 'colormap'; feature: string; colormap: string; contrastLimits: [number, number] | null};
export type PointStyle = {symbol: string | null; size: number | null; borderWidth: number | null; borderWidthIsRelative: boolean; face: ColorSpec; border: ColorSpec};
export type ShapeStyle = {edgeWidth: number | null; face: ColorSpec; edge: ColorSpec};
export type TextContent = {string: {kind: 'constant' | 'feature' | 'format' | 'other'; value: string}; color: Rgba | null};
export type StyleChoices = {features: [string, 'numeric' | 'categorical'][]; colormaps: string[]};

const record = (raw: unknown): raw is Record<string, unknown> => !!raw && typeof raw === 'object' && !Array.isArray(raw);
const keys = (value: Record<string, unknown>, names: string) => Object.keys(value).sort().join(' ') === names.split(' ').sort().join(' ');
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const name = (n: unknown): n is string => typeof n === 'string' && n.length > 0 && n.length <= 1000;
export const rgba = (n: unknown): n is Rgba => Array.isArray(n) && n.length === 4 && n.every(v => finite(v) && v >= 0 && v <= 1);
const width = (n: unknown) => n === null || finite(n) && n >= 0;

/** One side's colors; `editing` refuses what only a description may hold (a cycle too long to edit here). */
export function colorSpec(raw: unknown, editing = true): ColorSpec {
  const fail = (): never => { throw new Error('Colors are a direct color (four values 0–1), a cycle of colors by feature, or a colormap by a numeric feature with ordered limits.'); };
  if (!record(raw)) return fail();
  if (raw.mode === 'direct') { if (!keys(raw, 'mode color') || !(raw.color === null || rgba(raw.color))) return fail(); }
  else if (raw.mode === 'cycle') {
    const cycle = raw.cycle;
    if (!keys(raw, 'mode feature cycle') || !name(raw.feature) ||
        !(cycle === null && !editing || Array.isArray(cycle) && cycle.length >= 1 && cycle.length <= MAX_CYCLE && cycle.every(rgba))) return fail();
  } else if (raw.mode === 'colormap') {
    const limits = raw.contrastLimits;
    if (!keys(raw, 'mode feature colormap contrastLimits') || !name(raw.feature) || !name(raw.colormap) ||
        !(limits === null || Array.isArray(limits) && limits.length === 2 && limits.every(finite) && limits[0] <= limits[1])) return fail();
  } else return fail();
  return structuredClone(raw) as ColorSpec;
}

export function pointStyle(raw: unknown, editing = true): PointStyle {
  const fail = (): never => { throw new Error('Marker style needs one of napari’s symbols, a size and border width of zero or more (a relative border from 0 to 1), and face and border colors.'); };
  if (!record(raw) || !keys(raw, 'symbol size borderWidth borderWidthIsRelative face border') ||
      !(raw.symbol === null || pointSymbols.some(s => s === raw.symbol)) || !width(raw.size) || !width(raw.borderWidth) ||
      typeof raw.borderWidthIsRelative !== 'boolean' || raw.borderWidthIsRelative && finite(raw.borderWidth) && raw.borderWidth > 1) return fail();
  return {...structuredClone(raw) as PointStyle, face: colorSpec(raw.face, editing), border: colorSpec(raw.border, editing)};
}

export function shapeStyle(raw: unknown, editing = true): ShapeStyle {
  if (!record(raw) || !keys(raw, 'edgeWidth face edge') || !width(raw.edgeWidth))
    throw new Error('Shape style needs an edge width of zero or more and face and edge colors.');
  return {...structuredClone(raw) as ShapeStyle, face: colorSpec(raw.face, editing), edge: colorSpec(raw.edge, editing)};
}

export function textContent(raw: unknown, editing = true): TextContent {
  const fail = (): never => { throw new Error('Text is a constant, a feature name or a format string such as {area:.1f}; its color is four values 0–1.'); };
  if (!record(raw) || !keys(raw, 'string color') || !record(raw.string) || !keys(raw.string, 'kind value')) return fail();
  const {kind, value} = raw.string;
  if (!['constant', 'feature', 'format', ...(editing ? [] : ['other'])].includes(String(kind)) || typeof value !== 'string' || value.length > 1000 ||
      (kind === 'feature' || kind === 'format') && !value || kind === 'other' && value !== '' || !(raw.color === null || rgba(raw.color))) return fail();
  return structuredClone(raw) as TextContent;
}

export function styleChoices(raw: unknown): StyleChoices {
  if (!record(raw) || !keys(raw, 'features colormaps') || !Array.isArray(raw.features) || !Array.isArray(raw.colormaps) ||
      raw.features.length > 64 || raw.colormaps.length > 256 || !raw.colormaps.every(n => typeof n === 'string' && n.length > 0 && n.length <= 128) ||
      !raw.features.every(f => Array.isArray(f) && f.length === 2 && typeof f[0] === 'string' && f[0].length > 0 && f[0].length <= 128 && (f[1] === 'numeric' || f[1] === 'categorical')))
    throw new Error('Style choices are feature names with their kinds and colormap names.');
  return structuredClone(raw) as StyleChoices;
}

export const colorText = (color: Rgba | null) => color === null ? '' : color.join(' ');
export function parseColor(text: string, what: string): Rgba {
  const parts = text.trim().split(/[\s,]+/).filter(Boolean).map(Number);
  if (parts.length !== 4 || !rgba(parts)) throw new Error(`${what} is four values from 0 to 1: red, green, blue, alpha.`);
  return parts as Rgba;
}
export function parseCycle(text: string, what: string): Rgba[] {
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean);
  if (!lines.length || lines.length > MAX_CYCLE) throw new Error(`${what}: enter 1 to ${MAX_CYCLE} colors, one per line.`);
  return lines.map(line => parseColor(line, `Each color of ${what.toLowerCase()}`));
}

// --- View controls ----------------------------------------------------------------------------------------------

type Field = HTMLInputElement | HTMLSelectElement | HTMLButtonElement | HTMLTextAreaElement;
type Layer = {id: string; name: string; type: string; pointStyle?: PointStyle; shapeStyle?: ShapeStyle; textContent?: TextContent; styleChoices?: StyleChoices};
export type StyleChange =
  | {operation: 'points-style'; value: {layer: string; settings: PointStyle}}
  | {operation: 'shapes-style'; value: {layer: string; settings: ShapeStyle}}
  | {operation: 'text-content'; value: {layer: string; settings: TextContent}}
  | {operation: 'shapes-order'; value: {layer: string; action: 'front' | 'back'}};
export type StyleControls = {
  input(label: string, type: string, group: string, read: () => StyleChange): HTMLInputElement;
  edit(group: string, read: () => StyleChange): void;
  submit(): void;
};
export type StyleRow = {root: HTMLElement; fields: Field[]; update(layers: Layer[]): void};
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};
const options = (select: HTMLSelectElement, entries: [string, string][]) =>
  select.replaceChildren(...entries.map(([value, label]) => { const option = node('option', label); option.value = value; return option; }));
const line = (root: HTMLElement, label: string, field: HTMLElement) => { const row = node('label', `${label} `); row.append(field); root.append(row); return row; };

/** One side's color controls: mode, a direct color, or a feature with its cycle or colormap and limits. */
function colorControls(side: string, group: string, controls: StyleControls, read: () => StyleChange) {
  const root = node('div'), mode = node('select'), color = controls.input(`${side} color`, 'text', group, read);
  const feature = node('select'), cycle = node('textarea'), colormap = node('select');
  const low = controls.input(`${side} contrast minimum`, 'number', group, read), high = controls.input(`${side} contrast maximum`, 'number', group, read);
  low.step = high.step = 'any'; cycle.rows = 4;
  options(mode, [['direct', 'One color'], ['cycle', 'Cycle by feature'], ['colormap', 'Colormap by feature']]);
  const rows = {color: line(root, `${side} color`, color), feature: line(root, 'Feature', feature), cycle: line(root, 'Colors (one “red green blue alpha” per line)', cycle),
    colormap: line(root, 'Colormap', colormap), low: line(root, 'Contrast minimum', low), high: line(root, 'Contrast maximum', high)};
  root.prepend(line(node('div'), `${side} colors`, mode));
  let numeric = new Set<string>();
  const show = () => {
    const m = mode.value;
    const hidden: [HTMLElement, Field, boolean][] = [[rows.color, color, m !== 'direct'], [rows.feature, feature, m === 'direct'], [rows.cycle, cycle, m !== 'cycle'],
      [rows.colormap, colormap, m !== 'colormap'], [rows.low, low, m !== 'colormap'], [rows.high, high, m !== 'colormap']];
    for (const [row, field, off] of hidden) { row.hidden = off; field.dataset.displayed = String(off); }
  };
  for (const field of [mode, feature, colormap]) field.addEventListener('change', () => { show(); controls.edit(group, read); });
  cycle.addEventListener('input', () => controls.edit(group, read));
  return {root, fields: [mode, color, feature, cycle, colormap, low, high] as Field[],
    label(layer: string) {
      for (const [field, label] of [[mode, 'color mode'], [color, 'color'], [feature, 'color feature'], [cycle, 'color cycle'], [colormap, 'colormap'],
        [low, 'contrast minimum'], [high, 'contrast maximum']] as const) field.setAttribute('aria-label', `${side} ${label} ${layer}`);
    },
    update(spec: ColorSpec, choices: StyleChoices) {
      numeric = new Set(choices.features.filter(([, kind]) => kind === 'numeric').map(([n]) => n));
      options(feature, choices.features.map(([n, kind]) => [n, `${n} (${kind === 'numeric' ? 'number' : 'category'})`]));
      const maps = choices.colormaps.includes(spec.mode === 'colormap' ? spec.colormap : '') || spec.mode !== 'colormap' ? choices.colormaps : [spec.colormap, ...choices.colormaps];
      options(colormap, maps.map(n => [n, n]));
      mode.value = spec.mode; color.value = spec.mode === 'direct' ? colorText(spec.color) : '';
      color.placeholder = spec.mode === 'direct' && spec.color === null ? 'Each element keeps its own color' : '';
      feature.value = spec.mode !== 'direct' ? spec.feature : choices.features[0]?.[0] ?? '';
      cycle.value = spec.mode === 'cycle' && spec.cycle ? spec.cycle.map(colorText).join('\n') : '';
      cycle.placeholder = spec.mode === 'cycle' && spec.cycle === null ? `More than ${MAX_CYCLE} colors: enter a new cycle to change it` : '';
      colormap.value = spec.mode === 'colormap' ? spec.colormap : 'viridis';
      const limits = spec.mode === 'colormap' ? spec.contrastLimits : null;
      low.value = limits ? String(limits[0]) : ''; high.value = limits ? String(limits[1]) : '';
      show();
    },
    read(): ColorSpec {
      const m = mode.value;
      if (m === 'direct') return {mode: 'direct', color: color.value.trim() ? parseColor(color.value, `The ${side.toLowerCase()} color`) : null};
      if (!feature.value) throw new Error('This layer has no features to color by.');
      if (m === 'cycle') return {mode: 'cycle', feature: feature.value, cycle: parseCycle(cycle.value, `${side} color cycle`)};
      if (!numeric.has(feature.value)) throw new Error('A colormap colors a numeric feature; choose a cycle for categories.');
      const both = low.value.trim() && high.value.trim(), neither = !low.value.trim() && !high.value.trim();
      if (!both && !neither) throw new Error('Enter both contrast limits, or neither for the feature’s data range.');
      if (both && !([Number(low.value), Number(high.value)].every(Number.isFinite) && Number(low.value) <= Number(high.value)))
        throw new Error('Contrast limits must be finite, with the minimum at or below the maximum.');
      return {mode: 'colormap', feature: feature.value, colormap: colormap.value, contrastLimits: both ? [Number(low.value), Number(high.value)] : null};
    }};
}

const optional = (input: HTMLInputElement, what: string) => {
  if (!input.value.trim()) return null;
  const n = Number(input.value);
  if (!Number.isFinite(n) || n < 0) throw new Error(`${what} must be zero or more.`);
  return n;
};

export function pointStyleRow(id: string, controls: StyleControls): StyleRow {
  const group = `point-style:${id}`, root = node('fieldset'), title = node('legend'), symbol = node('select');
  const read = (): StyleChange => {
    if (!accepted) throw new Error('Refresh the preview before changing marker style.');
    const size = optional(sizeInput, 'Point size'), border = optional(borderInput, 'Border width');
    if (size === null && accepted.size !== null || border === null && accepted.borderWidth !== null) throw new Error('Enter a size and a border width.');
    return {operation: 'points-style', value: {layer: id, settings: pointStyle({symbol: symbol.value || null, size, borderWidth: border,
      borderWidthIsRelative: relative.checked, face: face.read(), border: edge.read()})}};
  };
  const sizeInput = controls.input('Point size', 'number', group, read), borderInput = controls.input('Border width', 'number', group, read);
  const relative = controls.input('Border width relative to size', 'checkbox', group, read);
  sizeInput.min = borderInput.min = '0'; sizeInput.step = borderInput.step = 'any';
  const face = colorControls('Face', group, controls, read), edge = colorControls('Border', group, controls, read);
  symbol.addEventListener('change', () => controls.edit(group, read));
  let accepted: PointStyle | null = null;
  root.append(title, node('p', 'Marker style for every point. Coordinates and feature values stay unchanged. A blank size, width or color keeps each point’s own value, as set in Python. A cycle or colormap colors each point by one of its features, as napari does.'));
  line(root, 'Symbol', symbol); line(root, 'Size', sizeInput); line(root, 'Border width', borderInput); line(root, 'Border width relative to size', relative);
  root.append(face.root, edge.root);
  return {root, fields: [symbol, sizeInput, borderInput, relative, ...face.fields, ...edge.fields], update: layers => {
    const layer = layers.find(layer => layer.id === id)!, style = layer.pointStyle!;
    accepted = structuredClone(style); title.textContent = `${layer.name} · marker style`;
    for (const [field, label] of [[symbol, 'Point symbol'], [sizeInput, 'Point size'], [borderInput, 'Border width'], [relative, 'Border width relative to size']] as const)
      field.setAttribute('aria-label', `${label} ${layer.name}`);
    face.label(layer.name); edge.label(layer.name);
    options(symbol, [...(style.symbol === null ? [['', 'Mixed (each point keeps its own)'] as [string, string]] : []), ...pointSymbols.map(s => [s, s.replaceAll('_', ' ')] as [string, string])]);
    symbol.value = style.symbol ?? ''; sizeInput.value = style.size === null ? '' : String(style.size);
    borderInput.value = style.borderWidth === null ? '' : String(style.borderWidth); relative.checked = style.borderWidthIsRelative;
    face.update(style.face, layer.styleChoices!); edge.update(style.border, layer.styleChoices!);
  }};
}

export function shapeStyleRow(id: string, controls: StyleControls): StyleRow {
  const group = `shape-style:${id}`, root = node('fieldset'), title = node('legend');
  const front = node('button', 'Move selected to front'), back = node('button', 'Move selected to back');
  let accepted: ShapeStyle | null = null;
  const read = (): StyleChange => {
    if (!accepted) throw new Error('Refresh the preview before changing shape style.');
    const edgeWidth = optional(widthInput, 'Edge width');
    if (edgeWidth === null && accepted.edgeWidth !== null) throw new Error('Enter an edge width.');
    return {operation: 'shapes-style', value: {layer: id, settings: shapeStyle({edgeWidth, face: face.read(), edge: edge.read()})}};
  };
  const widthInput = controls.input('Edge width', 'number', group, read);
  widthInput.min = '0'; widthInput.step = 'any';
  const face = colorControls('Face', group, controls, read), edge = colorControls('Edge', group, controls, read);
  for (const [button, action] of [[front, 'front'], [back, 'back']] as const) {
    button.dataset.requiresClean = 'true';
    button.addEventListener('click', () => {
      if (button.disabled) return;
      controls.edit(group, () => ({operation: 'shapes-order', value: {layer: id, action}})); controls.submit();
    });
  }
  root.append(title, node('p', 'Edge width and colors for every shape; geometry and feature values stay unchanged. A blank width or color keeps each shape’s own. Select shapes in Inspect, then move them in front of or behind the others (napari’s z-order).'));
  line(root, 'Edge width', widthInput); root.append(face.root, edge.root, front, back);
  return {root, fields: [widthInput, ...face.fields, ...edge.fields, front, back], update: layers => {
    const layer = layers.find(layer => layer.id === id)!, style = layer.shapeStyle!;
    accepted = structuredClone(style); title.textContent = `${layer.name} · shape style`;
    widthInput.setAttribute('aria-label', `Edge width ${layer.name}`);
    front.setAttribute('aria-label', `Move selected shapes to front ${layer.name}`); back.setAttribute('aria-label', `Move selected shapes to back ${layer.name}`);
    face.label(layer.name); edge.label(layer.name);
    widthInput.value = style.edgeWidth === null ? '' : String(style.edgeWidth);
    face.update(style.face, layer.styleChoices!); edge.update(style.edge, layer.styleChoices!);
  }};
}

export function textContentRow(id: string, controls: StyleControls): StyleRow {
  const group = `text-content:${id}`, root = node('fieldset'), title = node('legend'), kind = node('select');
  const read = (): StyleChange => {
    if (kind.value === 'other') throw new Error('This text was set in Python. Choose a constant, a feature or a format string to change it.');
    return {operation: 'text-content', value: {layer: id, settings: textContent({string: {kind: kind.value, value: value.value},
      color: color.value.trim() ? parseColor(color.value, 'The text color') : null})}};
  };
  const value = controls.input('Text value', 'text', group, read), color = controls.input('Text color', 'text', group, read);
  kind.addEventListener('change', () => controls.edit(group, read));
  root.append(title, node('p', 'What each element’s text says: the same words for all, one feature’s value, or a format string over features such as {kind}: {area:.1f}. Feature values stay unchanged.'));
  line(root, 'Text from', kind); line(root, 'Text', value); line(root, 'Text color (red green blue alpha)', color);
  return {root, fields: [kind, value, color], update: layers => {
    const layer = layers.find(layer => layer.id === id)!, content = layer.textContent!;
    title.textContent = `${layer.name} · text content`;
    for (const [field, label] of [[kind, 'Text from'], [value, 'Text value'], [color, 'Text color']] as const) field.setAttribute('aria-label', `${label} ${layer.name}`);
    options(kind, [['constant', 'The same text'], ['feature', 'A feature'], ['format', 'A format string'],
      ...(content.string.kind === 'other' ? [['other', 'Set in Python'] as [string, string]] : [])]);
    kind.value = content.string.kind; value.value = content.string.value; color.value = colorText(content.color);
    color.placeholder = content.color === null ? 'Text keeps its color mapping' : '';
  }};
}
