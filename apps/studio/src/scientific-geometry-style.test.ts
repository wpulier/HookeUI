import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import shared from '../../../contract/scientific-geometry-style-cases.json';
import { colorSpec, parseColor, parseCycle, pointStyle, shapeStyle, styleChoices, textContent } from './scientific-geometry-style';
import { ScientificNavigation, type ViewerChange } from './scientific-navigation';
import { scientificFrame } from './scientific-view';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

const CHOICES = {features: [['kind', 'categorical'], ['area', 'numeric']], colormaps: ['gray', 'viridis']};
test.each(shared.invalidPoints)('marker style refuses $name', ({patch}) => {
  expect(() => pointStyle({...shared.points, ...patch})).toThrow();
});
test.each(shared.invalidShapes)('shape style refuses $name', ({patch}) => {
  expect(() => shapeStyle({...shared.shapes, ...patch})).toThrow();
});
test.each(shared.invalidText)('text content refuses $name', ({patch}) => {
  expect(() => textContent({...shared.text, ...patch})).toThrow();
});
test('settings round-trip; only a description may hold text set in Python or a cycle too long to edit', () => {
  expect(pointStyle(shared.points)).toEqual(shared.points);
  expect(shapeStyle(shared.shapes)).toEqual(shared.shapes);
  expect(textContent(shared.text)).toEqual(shared.text);
  expect(textContent({string: {kind: 'other', value: ''}, color: null}, false).string.kind).toBe('other');
  expect(() => colorSpec({mode: 'cycle', feature: 'kind', cycle: null})).toThrow();
  expect(colorSpec({mode: 'cycle', feature: 'kind', cycle: null}, false)).toEqual({mode: 'cycle', feature: 'kind', cycle: null});
  expect(styleChoices(CHOICES)).toEqual(CHOICES);
  expect(parseColor('1, 0 0.5 1', 'x')).toEqual([1, 0, 0.5, 1]);
  expect(() => parseColor('1 0 0', 'x')).toThrow('four values');
  expect(parseCycle('1 0 0 1\n\n0 1 0 1\n', 'x')).toEqual([[1, 0, 0, 1], [0, 1, 0, 1]]);
});

test('constant native feature limits survive frame decoding and unrelated style edits', () => {
  const constant = {mode: 'colormap', feature: 'area', colormap: 'gray', contrastLimits: [7, 7]};
  const settings = {...shared.points, border: constant};
  const raw = structuredClone(cases.valid);
  Object.assign(raw.layers[0], {type: 'points', pointStyle: settings, styleChoices: CHOICES});
  expect(scientificFrame(raw).layers[0].pointStyle?.border).toEqual(constant);
  expect(pointStyle({...settings, size: 9}).border).toEqual(constant);
});

let root: TestElement;
beforeEach(() => { const dom = syntheticDOM(); root = dom.host; vi.stubGlobal('document', dom.document); vi.stubGlobal('window', new EventTarget()); });
afterEach(() => vi.unstubAllGlobals());
const field = (name: string) => [...root.querySelectorAll('input'), ...root.querySelectorAll('select'), ...root.querySelectorAll('textarea')]
  .find(row => row.getAttribute('aria-label') === name)!;
const button = (name: string) => root.querySelectorAll('button').find(row => row.textContent === name || row.getAttribute('aria-label') === name)!;
function open(type: 'points' | 'shapes') {
  const frame = scientificFrame(cases.valid);
  const layer = frame.layers[0]; layer.type = type; layer.styleChoices = structuredClone(CHOICES) as typeof layer.styleChoices;
  layer.textContent = {string: {kind: 'constant', value: ''}, color: [0, 1, 1, 1]};
  if (type === 'points') layer.pointStyle = {symbol: null, size: 10, borderWidth: 0.05, borderWidthIsRelative: true,
    face: {mode: 'direct', color: [1, 1, 1, 1]}, border: {mode: 'direct', color: null}};
  else layer.shapeStyle = {edgeWidth: null, face: {mode: 'direct', color: [1, 1, 1, 1]}, edge: {mode: 'direct', color: [0.5, 0.5, 0.5, 1]}};
  const send = vi.fn(async (_: string, __: ViewerChange) => { const next = structuredClone(frame); next.frame = 'c'.repeat(64); controls.update(next); return next; });
  const controls = new ScientificNavigation(root as unknown as HTMLElement, {send, refresh: async () => {}, drawn: async () => {}});
  controls.bindSession('one'); controls.update(frame); controls.setAvailable(true);
  return {frame, send, controls, name: layer.name, id: layer.id};
}

test.each(['points', 'shapes'] as const)('ordinary %s style edit preserves constant face and edge colormap limits', async type => {
  const {frame, send, controls, name, id} = open(type);
  const constant = colorSpec({mode: 'colormap', feature: 'area', colormap: 'gray', contrastLimits: [7, 7]});
  const layer = frame.layers[0];
  if (type === 'points') Object.assign(layer.pointStyle!, {face: constant, border: constant});
  else Object.assign(layer.shapeStyle!, {face: constant, edge: constant});
  controls.update(frame);
  const width = field(`${type === 'points' ? 'Point size' : 'Edge width'} ${name}`);
  width.value = '9'; fire(width, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledExactlyOnceWith(frame.frame, {operation: `${type}-style`, value: {layer: id,
    settings: type === 'points' ? {...layer.pointStyle, size: 9} : {...layer.shapeStyle, edgeWidth: 9}}});
});

test.each([['8', '7'], ['NaN', '7'], ['7', 'Infinity'], ['-Infinity', '7'], ['Infinity', 'Infinity']])(
  'ordinary style edit refuses invalid contrast limits %s..%s', async (minimum, maximum) => {
    const {send, name} = open('points');
    const mode = field(`Face color mode ${name}`); mode.value = 'colormap'; fire(mode, 'change');
    const feature = field(`Face color feature ${name}`); feature.value = 'area'; fire(feature, 'change');
    const low = field(`Face contrast minimum ${name}`); low.value = minimum; fire(low, 'input');
    const high = field(`Face contrast maximum ${name}`); high.value = maximum; fire(high, 'input');
    fire(button('Apply view changes'), 'click'); await settleMicrotasks();
    expect(send).not.toHaveBeenCalled();
    expect(root.textContent).toContain('Contrast limits must be finite');
  });

test('View explains which styling changes Remember this view cannot retain', () => {
  open('points');
  expect(root.textContent).toContain('Marker styles, shape styles, text content and shape z-order are not yet retained by Remember this view.');
});

test('marker style keeps mixed values, refuses a colormap over categories and sends napari settings for the exact frame', async () => {
  const {frame, send, name, id} = open('points');
  expect(field(`Point symbol ${name}`).value).toBe('');  // mixed symbols: each point keeps its own
  expect(field(`Border color ${name}`).value).toBe('');
  const mode = field(`Face color mode ${name}`); mode.value = 'colormap'; fire(mode, 'change');
  const feature = field(`Face color feature ${name}`); feature.value = 'kind'; fire(feature, 'change');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send).not.toHaveBeenCalled(); expect(root.textContent).toContain('numeric feature');
  feature.value = 'area'; fire(feature, 'change');
  const low = field(`Face contrast minimum ${name}`); low.value = '0'; fire(low, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send).not.toHaveBeenCalled(); expect(root.textContent).toContain('both contrast limits');
  const high = field(`Face contrast maximum ${name}`); high.value = '4'; fire(high, 'input');
  const colormap = field(`Face colormap ${name}`); colormap.value = 'gray'; fire(colormap, 'change');
  const symbol = field(`Point symbol ${name}`); symbol.value = 'star'; fire(symbol, 'change');
  const size = field(`Point size ${name}`); size.value = '7'; fire(size, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame, {operation: 'points-style', value: {layer: id, settings: {symbol: 'star', size: 7, borderWidth: 0.05,
    borderWidthIsRelative: true, face: {mode: 'colormap', feature: 'area', colormap: 'gray', contrastLimits: [0, 4]}, border: {mode: 'direct', color: null}}}});
});

test('a color cycle is typed one color per line', async () => {
  const {frame, send, name, id} = open('points');
  const mode = field(`Border color mode ${name}`); mode.value = 'cycle'; fire(mode, 'change');
  const cycle = field(`Border color cycle ${name}`); cycle.value = '1 0 0 1\n0 0 1'; fire(cycle, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks(); expect(send).not.toHaveBeenCalled();
  cycle.value = '1 0 0 1\n0 0 1 1'; fire(cycle, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send.mock.calls[0][1]).toMatchObject({operation: 'points-style', value: {layer: id, settings: {border: {mode: 'cycle', feature: 'kind', cycle: [[1, 0, 0, 1], [0, 0, 1, 1]]}}}});
  expect(send.mock.calls[0][0]).toBe(frame.frame);
});

test('shape style, text content and z-order each send their own change', async () => {
  const {frame, send, name, id} = open('shapes');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks(); expect(send).not.toHaveBeenCalled();
  const width = field(`Edge width ${name}`); width.value = '3'; fire(width, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith(frame.frame, {operation: 'shapes-style', value: {layer: id, settings: {edgeWidth: 3,
    face: {mode: 'direct', color: [1, 1, 1, 1]}, edge: {mode: 'direct', color: [0.5, 0.5, 0.5, 1]}}}});
  const kind = field(`Text from ${name}`); kind.value = 'format'; fire(kind, 'change');
  const value = field(`Text value ${name}`); value.value = '{kind}'; fire(value, 'input');
  fire(button('Apply view changes'), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith('c'.repeat(64), {operation: 'text-content', value: {layer: id, settings: {string: {kind: 'format', value: '{kind}'}, color: [0, 1, 1, 1]}}});
  fire(button(`Move selected shapes to front ${name}`), 'click'); await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith('c'.repeat(64), {operation: 'shapes-order', value: {layer: id, action: 'front'}});
});

test('Inspect accepts and shows the inspected element’s own style, and refuses a malformed one', async () => {
  const {pointInspection, pointDetails} = await import('./scientific-point-inspection');
  const {shapeInspection, shapeDetails} = await import('./scientific-shape-inspection');
  const request = {frame: 'f'.repeat(64), layer: 'a'.repeat(32)};
  const base = {kind: 'points', frame: request.frame, layer: request.layer, world: [0, 4, 14], data: [0, 4, 14], axes: ['z', 'y', 'x'], units: ['', '', '']};
  const point = {...base, hit: {selected: false, index: 1, coordinates: ['0', '4', '14'], dtype: '<f8', features: [],
    style: {symbol: 'tailed_arrow', size: 2, borderWidth: 0.5, faceColor: [0, 0, 1, 1], borderColor: [1, 0, 0, 1]}}};
  expect(pointDetails(pointInspection(point, request)).textContent).toContain('Marker: tailed arrow, size 2, border width 0.5; face color 0 0 1 1; border color 1 0 0 1');
  expect(() => pointInspection({...point, hit: {...point.hit, style: {...point.hit.style, symbol: 'circle'}}}, request)).toThrow();
  const {style: _, ...older} = point.hit;  // a worker that sends no style is still read
  expect(pointInspection({...point, hit: older}, request).hit?.style).toBeUndefined();
  const shape = {...base, kind: 'shapes', hit: {selected: true, index: 0, geometry: 'rectangle', dtype: '<f8', features: [],
    vertices: [['0', '2', '2'], ['0', '2', '12'], ['0', '10', '12'], ['0', '10', '2']],
    style: {edgeWidth: 1, faceColor: [0, 1, 0, 1], edgeColor: [0, 0, 1, 1], z: 1}}};
  expect(shapeDetails(shapeInspection(shape, request)).textContent).toContain('Edge width 1; face color 0 1 0 1; edge color 0 0 1 1 (red green blue alpha); z-order 1.');
  expect(() => shapeInspection({...shape, hit: {...shape.hit, style: {...shape.hit.style, z: 0.5}}}, request)).toThrow();
});
