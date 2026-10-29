// The live view's places for the voice's marks (view.ts): a frame mark goes through the same source -> canvas transform
// as the outlines and moves with the slide from the frame the model saw; members of the shown population by their box.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import type { LayerWire, PopulationWire } from './population';
import type { FrameHeader, Point, SceneObject } from './protocol';
import { View } from './view';

const noop: unknown = new Proxy(function () {}, { get: () => noop, apply: () => noop });
const canvas = {
  width: 0, height: 0, getContext: () => noop,
  getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600, right: 800, bottom: 600 }),
};
beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  vi.stubGlobal('window', { devicePixelRatio: 1 });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', () => 0);
});
afterEach(() => vi.unstubAllGlobals());

const header = (id: number, dx: number, dy: number, more: Partial<FrameHeader> = {}): FrameHeader => ({
  type: 'frame', id, w: 1000, h: 800, dx, dy, epoch: 2, tracked: true, steady: true, speed: 0, t: 0, sent: 0, fov: [500, 400, 380], ...more,
});
const frame = (h: FrameHeader) => ({ header: h, bitmap: { close() {} } as unknown as ImageBitmap });
type Inner = { placement: { scale: number; ox: number; oy: number } };

function view(): View {
  const v = new View(canvas as unknown as HTMLCanvasElement);
  (v as unknown as Inner).placement = { scale: 0.5, ox: 40, oy: 10 };
  return v;
}

const square: Point[] = [[480, 380], [520, 380], [520, 420], [480, 420]];
const outline = (anchorFrame: number, offset: Point): SceneObject => ({
  id: 'o12', kind: 'part', polygons: [square], anchorFrame, anchorEpoch: 2, anchorOffset: offset, source: 'click', label: '', labelKey: '',
  status: 'outline', confidence: null, note: '', seen: '', track: 'tracking',
} as unknown as SceneObject);

test('a frame mark is placed exactly where an outline of the same pixels is, and both follow the slide', () => {
  const v = view();
  v.setFrame(frame(header(10, 100, 50)));
  const anchor = v.frameOffset(10)!;
  expect(anchor).toEqual([100, 50, 2]);
  v.setScene([outline(10, [100, 50])]);
  const box = (points: Point[]) => {
    const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  };
  expect(box(v.sourcePointsOnCanvas(square, anchor)!)).toEqual(v.objectCanvasBox('o12'));
  // The slide moves: both move by the same amount.
  v.setFrame(frame(header(11, 130, 40)));
  expect(box(v.sourcePointsOnCanvas(square, anchor)!)).toEqual(v.objectCanvasBox('o12'));
  expect(v.sourcePointsOnCanvas([[500, 400]], anchor)).toEqual([[40 + (500 + 30) * 0.5, 10 + (400 - 10) * 0.5]]);
  // The frame the model saw is still known by its id after later frames; the one shown now without an id.
  expect(v.frameOffset(10)).toEqual([100, 50, 2]);
  expect(v.frameOffset()).toEqual([130, 40, 2]);
  expect(v.frameOffset(3)).toBe(null);
});

test('after a re-find (another motion epoch) or while the slide is not tracked, a frame mark is not placed', () => {
  const v = view();
  v.setFrame(frame(header(10, 100, 50)));
  const anchor = v.frameOffset(10)!;
  v.setFrame(frame(header(11, 0, 0, { epoch: 3 })));
  expect(v.sourcePointsOnCanvas(square, anchor)).toBe(null);
  v.setFrame(frame(header(12, 100, 50, { tracked: false })));
  expect(v.sourcePointsOnCanvas(square, v.frameOffset()!)).toBe(null);
});

test('a member of the shown population by its box; another population is hidden; an unverified member is out of view', () => {
  const v = view();
  v.setFrame(frame(header(10, 37, -12)));
  const pop: PopulationWire = {
    type: 'population', space: 'capture', id: 'e5', operation: 'cells', madeBy: {}, capture: 9, epoch: 2, offset: [37, -12],
    at: 0, revision: 0, status: 'saved', count: 2, coverage: null, basis: { unit: 'px', umPerPx: null, scale: null, objective: null },
    members: [
      { id: 'c1', polygons: [[[100, 100], [140, 100], [140, 120], [100, 120]]], colour: 1, placement: 'verified', measure: null },
      { id: 'c2', polygons: [[[300, 300], [310, 300], [310, 310]]], colour: 2, placement: 'verified', measure: null },
    ],
  } as PopulationWire;
  const layer: LayerWire = { type: 'layer', space: 'frame', population: 'e5', style: { opacity: 1, colour: 'id' }, verified: ['c1'],
    correction: [0, 0], view: { epoch: 2, offset: [37, -12] } } as LayerWire;
  v.setPopulation(pop);
  v.setLayer(layer);
  expect(v.memberCanvasBox('e5', 'c1')).toEqual([40 + 100 * 0.5, 10 + 100 * 0.5, 40 + 140 * 0.5, 10 + 120 * 0.5]);
  expect(v.memberCanvasBox('e5', 'c2')).toBe('out-of-view');
  expect(v.memberCanvasBox('e5', 'c9')).toBe('not-found');
  expect(v.memberCanvasBox('e6', 'c1')).toBe('not-found');
  v.setPopulation({ ...pop, id: 'e6' });
  expect(v.memberCanvasBox('e6', 'c1')).toBe('hidden'); // known, not the one shown
});
