// S5a: a population found on the still the Stage shows is drawn on the specimen through the still's fit (the layer
// wire's `fit`: orchestrator/still_fit.py), at every Stage size and letterbox, and clicks and hover land on the member
// drawn there. The fits below are the server's own for these cases (services/tests/test_still_fit.py, test_stage_still.py).
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { drawable, memberAt, placed, type LayerWire, type PopulationWire, type StillFit } from './population';
import type { FrameHeader, Point } from './protocol';
import { frameToStill, frameToWorkspace, stillToFrame, workspaceToFrame, type Affine } from './spaces';
import { placeFrame, View } from './view';

const header = (w: number, h: number, epoch = 4, more: Partial<FrameHeader> = {}): FrameHeader => ({
  type: 'frame', id: 9, w, h, dx: 37, dy: -12, epoch, tracked: true, steady: true, speed: 0, t: 0, sent: 0, fov: null, ...more,
});
// The 96 x 96 fixture's three particles (orchestrator/fiji_bindings.py `fixture`), in the original's own pixels.
const circle = (cx: number, cy: number, r: number): Point[] =>
  Array.from({ length: 24 }, (_, i) => [cx + r * Math.cos((i / 24) * 2 * Math.PI), cy + r * Math.sin((i / 24) * 2 * Math.PI)]);
const pop: PopulationWire = {
  type: 'population', space: 'capture', id: 'e5', operation: 'cells', madeBy: {}, capture: -3, epoch: null, offset: [0, 0],
  at: 0, revision: 0, status: 'saved', count: 3, coverage: null, basis: { unit: 'px', umPerPx: null, scale: null, objective: null },
  members: [
    { id: 'c1', polygons: [circle(25, 25, 10)], colour: 1, placement: 'verified', measure: null },
    { id: 'c2', polygons: [circle(65, 60, 7)], colour: 2, placement: 'verified', measure: null },
    { id: 'c3', polygons: [[[70, 10], [85, 10], [85, 22], [70, 22]]], colour: 3, placement: 'verified', measure: null },
  ],
};
const fits: Record<string, StillFit> = {
  'pillarboxed in 320 x 180': { frame: [320, 180], rect: [70, 0, 180, 180], matrix: [1.875, 0, 70.4375, 0, 1.875, 0.4375] },
  'pillarboxed in 1280 x 720': { frame: [1280, 720], rect: [280, 0, 720, 720], matrix: [7.5, 0, 283.25, 0, 7.5, 3.25] },
  'letterboxed in a tall 200 x 360': { frame: [200, 360], rect: [0, 80, 200, 200], matrix: [2.083333, 0, 0.541667, 0, 2.083333, 80.541667] },
};
const layer = (fit: StillFit, more: Partial<LayerWire> = {}): LayerWire => ({
  type: 'layer', space: 'frame', population: 'e5', style: { opacity: 1, colour: 'id' }, verified: ['c1', 'c2', 'c3'],
  correction: [0, 0], view: { epoch: 4, offset: [37, -12] }, fit, ...more,
});

describe('a population on the Stage\'s still', () => {
  for (const [name, fit] of Object.entries(fits)) {
    test(`${name}: drawn by the fit alone, and a click lands on the member drawn there`, () => {
      const h = header(...fit.frame);
      const { members, matrix } = drawable(pop, layer(fit), h);
      expect(members.map((m) => m.id)).toEqual(['c1', 'c2', 'c3']);
      expect(matrix).toEqual(fit.matrix); // the slide's offset (37, -12) and any correction play no part
      expect(placed(matrix, [10, 20])).toEqual(stillToFrame([10, 20], fit.matrix));
      // the centre of each particle, in frame px, hits it; the letterbox and the light field between them hit nothing
      expect(memberAt(pop, layer(fit), h, stillToFrame([25, 25], fit.matrix))?.id).toBe('c1');
      expect(memberAt(pop, layer(fit), h, stillToFrame([65, 60], fit.matrix))?.id).toBe('c2');
      expect(memberAt(pop, layer(fit), h, stillToFrame([77, 16], fit.matrix))?.id).toBe('c3');
      expect(memberAt(pop, layer(fit), h, stillToFrame([48, 48], fit.matrix))).toBeNull();
      const [x, y, w, hh] = fit.rect;
      expect(memberAt(pop, layer(fit), h, x ? [x / 2, y + hh / 2] : [x + w / 2, y / 2])).toBeNull();
      // a point just inside the large circle's edge in the frame is inside it in the original
      const edge = stillToFrame([25 + 9.5, 25], fit.matrix);
      expect(memberAt(pop, layer(fit), h, edge)?.id).toBe('c1');
      expect(memberAt(pop, layer(fit), h, [edge[0] + 2 * fit.matrix[0], edge[1]])).toBeNull();
    });
  }

  test('a quarter-turned JPEG (EXIF 6) maps by its rotation, there and back', () => {
    const turned: StillFit = { frame: [1280, 720], rect: [520, 0, 240, 720], matrix: [0, -2.4, 758.3, 2.4, 0, 0.7], orientation: 6 };
    expect(stillToFrame([10, 20], turned.matrix)).toEqual([710.3, 24.7]);
    const back = frameToStill([710.3, 24.7], turned.matrix);
    expect(back[0]).toBeCloseTo(10, 9); expect(back[1]).toBeCloseTo(20, 9);
    expect(memberAt(pop, layer(turned), header(1280, 720), stillToFrame([25, 25], turned.matrix))?.id).toBe('c1');
  });

  test('nothing is drawn or clicked on a frame the fit was not checked on', () => {
    const fit = fits['pillarboxed in 320 x 180'];
    const none = (l: LayerWire, h: FrameHeader) => drawable(pop, l, h).members;
    expect(none(layer(fit), header(320, 180, 5))).toEqual([]); // another motion epoch: the picture changed
    expect(none(layer(fit), header(1280, 720))).toEqual([]); // another frame size
    expect(none(layer(fit), header(320, 180, 4, { tracked: false }))).toEqual([]); // a frame the tracker could not register
    expect(none(layer(fit, { view: null }), header(320, 180))).toEqual([]); // no checked view
    expect(none(layer(fit, { verified: ['c2'] }), header(320, 180)).map((m) => m.id)).toEqual(['c2']); // only the verified
    expect(none(layer(fit, { population: 'e9' }), header(320, 180))).toEqual([]);
    expect(memberAt(pop, layer(fit), header(320, 180, 5), stillToFrame([25, 25], fit.matrix))).toBeNull();
  });

  test('without a fit the layer is the live view\'s, as before: moved with the slide', () => {
    const live = { ...pop, epoch: 4, offset: [30, -10] as [number, number] };
    const l = layer(fits['pillarboxed in 320 x 180'], { fit: null, view: null, correction: [1, 2] });
    const { shift, matrix } = drawable(live, l, header(320, 180));
    expect(shift).toEqual([8, 0]);
    expect(matrix).toEqual([1, 0, 8, 0, 1, 0] as Affine);
  });
});

// The drawing itself: view.ts drawPopulation puts each member's points through the fit onto the frame, then the frame's
// own place on the Stage (placeFrame), so the outlines sit on the specimen at every Stage size; the new result ramps in
// once; the member under the pointer is drawn firmer.
type Calls = { moveTo: Point[]; lineWidth: number[]; alpha: number[] };
function recorder(calls: Calls) {
  const saved: number[] = []; // a canvas's save and restore keep its alpha, as the caller's fade relies on
  const ctx: Record<string, unknown> = {
    save() { saved.push(ctx.globalAlpha as number); }, restore() { ctx.globalAlpha = saved.pop() ?? 1; },
    beginPath() {}, closePath() {}, fill() {}, stroke() { calls.alpha.push(ctx.globalAlpha as number); },
    moveTo(x: number, y: number) { calls.moveTo.push([x, y]); }, lineTo() {},
  };
  let width = 0;
  Object.defineProperty(ctx, 'lineWidth', { get: () => width, set: (v: number) => { width = v; calls.lineWidth.push(v); } });
  ctx.globalAlpha = 1;
  return ctx;
}
let now = 0;
beforeEach(() => {
  now = 0;
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  vi.stubGlobal('window', { devicePixelRatio: 1 });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('performance', { now: () => now });
});
afterEach(() => vi.unstubAllGlobals());

type Drawing = { drawPopulation(h: FrameHeader): void; placement: { scale: number; ox: number; oy: number }; ctx: unknown; loadedAt: number;
  hoveredMember: string | null; setPopulation(p: PopulationWire): void; setLayer(l: LayerWire): void };
function viewWith(calls: Calls): Drawing {
  const canvas = { width: 0, height: 0, getContext: () => recorder(calls),
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600, right: 800, bottom: 600 }) };
  return new View(canvas as unknown as HTMLCanvasElement) as unknown as Drawing;
}

describe('drawing it on the Stage', () => {
  // name, Stage width and height, shelf reserve, side column open, whole frame (else the eyepiece's fit)
  const stages: [string, number, number, number, number, boolean][] = [
    ['a wide Stage', 1400, 800, 0, 0, true], ['the side column open', 1400, 800, 0, 1, true],
    ['a narrow Stage', 760, 820, 0, 0, true], ['a phone with the strip', 390, 700, 90, 0, true],
  ];
  for (const [fitName, fit] of Object.entries(fits)) {
    for (const [name, width, height, reserve, open, whole] of stages) {
      test(`${fitName}, ${name}: each outline's first point is where the still's pixel is drawn`, () => {
        const calls: Calls = { moveTo: [], lineWidth: [], alpha: [] };
        const view = viewWith(calls);
        const h = header(...fit.frame);
        view.placement = placeFrame(h, width, height, reserve, whole, open);
        view.setPopulation({ ...pop, at: view.loadedAt / 1000 - 60 }); view.setLayer(layer(fit));
        view.drawPopulation(h);
        expect(calls.moveTo).toHaveLength(3);
        pop.members.forEach((m, i) => {
          // the still's pixel, through the bridge's frame (its fit) and the frame's place on the Stage
          const want = frameToWorkspace(stillToFrame(m.polygons[0][0], fit.matrix), view.placement);
          expect(calls.moveTo[i][0]).toBeCloseTo(want[0], 6); expect(calls.moveTo[i][1]).toBeCloseTo(want[1], 6);
          // and back: the Stage point is the original's pixel (what a click there selects)
          const back = frameToStill(workspaceToFrame(calls.moveTo[i], view.placement), fit.matrix);
          expect(back[0]).toBeCloseTo(m.polygons[0][0][0], 6); expect(back[1]).toBeCloseTo(m.polygons[0][0][1], 6);
        });
      });
    }
  }

  test('a new result on the still ramps in once; the member under the pointer is drawn firmer', () => {
    const calls: Calls = { moveTo: [], lineWidth: [], alpha: [] };
    const view = viewWith(calls);
    const fit = fits['pillarboxed in 320 x 180'], h = header(...fit.frame);
    view.placement = placeFrame(h, 1400, 800, 0, true, 0);
    view.setPopulation({ ...pop, at: view.loadedAt / 1000 + 5 }); view.setLayer(layer(fit));
    now = 1000; view.drawPopulation(h); // its first draw: the ramp starts at nothing
    expect(Math.max(...calls.alpha)).toBe(0);
    calls.alpha.length = 0; now = 1400; view.drawPopulation(h); // past the ramp: whole
    expect(Math.max(...calls.alpha)).toBeCloseTo(0.8, 6);
    calls.alpha.length = 0; calls.lineWidth.length = 0; now = 9000;
    view.hoveredMember = 'c2'; view.drawPopulation(h);
    expect(calls.alpha).toEqual([0.8, 0.8, 1, 1, 0.8, 0.8]); // casing and line per member: c2 firmer
    expect(calls.lineWidth).toEqual([3, 1.5, 3.75, 2.25, 3, 1.5]);
  });
});
