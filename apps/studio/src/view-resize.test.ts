// The picture follows its box: when the canvas changes size (a side panel opening or closing moves the Stage's edge,
// frame by frame during the motion), the frame is placed at once for the new size, never eased from where the old size
// put it (b041: a 325 px jump, cut off by the Stage's edge for about 30 frames, then a drift back). The glide written for
// the whole frame and eyepiece switch stays.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { View, placeFrame } from './view';
import type { Frame, FrameHeader } from './protocol';

const noop: unknown = new Proxy(function () {}, { get: () => noop, apply: () => noop });
let size = { width: 1008, height: 736 };
let now = 0;
const canvas = {
  width: 0, height: 0,
  getContext: () => noop,
  getBoundingClientRect: () => ({ left: 0, top: 0, ...size, right: size.width, bottom: size.height }),
};
beforeEach(() => {
  size = { width: 1008, height: 736 }; now = 0; canvas.width = 0; canvas.height = 0;
  vi.stubGlobal('matchMedia', () => ({ matches: false })); // motion on
  vi.stubGlobal('window', { devicePixelRatio: 1 });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('performance', { now: () => now });
});
afterEach(() => vi.unstubAllGlobals());

type Inner = { resize(): void; draw(): void; placement: { scale: number; ox: number; oy: number } };
const header = { type: 'frame', id: 1, w: 1600, h: 900, dx: 0, dy: 0, fov: [800, 450, 420] } as unknown as FrameHeader;
const frame = (): Frame => ({ header, bitmap: { close() {} } as unknown as ImageBitmap });
const target = (width: number, height: number, whole = false) => placeFrame(header, width, height, 0, whole, 0);
const settled = (view: View) => { const inner = view as unknown as Inner; for (let i = 0; i < 200; i++) { now += 16; inner.draw(); } };

test('after the canvas changes size the picture is placed at once for the new size, insets too', () => {
  const view = new View(canvas as unknown as HTMLCanvasElement);
  const inner = view as unknown as Inner;
  view.setFrame(frame()); settled(view);
  expect(inner.placement.scale).toBeCloseTo(target(1008, 736).scale, 5);
  // The left column opens: the Stage narrows by 340 px. The ResizeObserver's resize draws at once, placed for 668 px.
  size = { width: 668, height: 736 }; now += 16; inner.resize();
  const p = inner.placement, t = target(668, 736);
  expect(p.scale).toBeCloseTo(t.scale, 5); expect(p.ox).toBeCloseTo(t.ox, 3); expect(p.oy).toBeCloseTo(t.oy, 3);
  // The next frame's own draw keeps it there (no glide resumes from the old size).
  now += 16; inner.draw();
  expect(inner.placement.ox).toBeCloseTo(t.ox, 3);
  // During the motion each frame's new size is placed at once too.
  for (const width of [700, 760, 840, 920, 1008]) {
    size = { width, height: 736 }; now += 16; inner.resize();
    expect(inner.placement.ox).toBeCloseTo(target(width, 736).ox, 3);
    expect(inner.placement.scale).toBeCloseTo(target(width, 736).scale, 5);
  }
});

test('the whole frame and eyepiece switch still glides', () => {
  const view = new View(canvas as unknown as HTMLCanvasElement);
  const inner = view as unknown as Inner;
  view.setFrame(frame()); settled(view);
  const eyepiece = target(1008, 736), whole = target(1008, 736, true);
  expect(eyepiece.scale).not.toBeCloseTo(whole.scale, 3);
  view.wholeFrame = true; now += 16; inner.draw();
  const between = inner.placement.scale;
  expect(Math.abs(between - eyepiece.scale)).toBeGreaterThan(1e-4);
  expect(Math.abs(between - whole.scale)).toBeGreaterThan(1e-4);
  settled(view);
  expect(inner.placement.scale).toBeCloseTo(whole.scale, 5);
});

test('the same size again changes nothing: no snap, a glide in progress goes on', () => {
  const view = new View(canvas as unknown as HTMLCanvasElement);
  const inner = view as unknown as Inner;
  view.setFrame(frame()); settled(view);
  view.wholeFrame = true; now += 16; inner.draw();
  const between = inner.placement.scale;
  now += 16; inner.resize(); // same size: the observer can fire without a change
  expect(inner.placement.scale).toBe(between);
  now += 16; inner.draw();
  expect(Math.abs(inner.placement.scale - target(1008, 736, true).scale)).toBeGreaterThan(1e-4);
});
