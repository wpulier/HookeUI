// Coming back to the live view from a page (Measurements, All assets): the picture is placed where it belongs at once,
// never glided in from where a hidden 0×0 stage put it (acceptance 2026-10-14: drawn shifted after Measurements).
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { View } from './view';

const noop: unknown = new Proxy(function () {}, { get: () => noop, apply: () => noop });
let size = { width: 800, height: 600 };
let now = 0;
const canvas = {
  width: 0, height: 0,
  getContext: () => noop,
  getBoundingClientRect: () => ({ left: 0, top: 0, ...size, right: size.width, bottom: size.height }),
};
beforeEach(() => {
  vi.stubGlobal('matchMedia', () => ({ matches: false }));  // motion on: insets glide, as for most people
  vi.stubGlobal('window', { devicePixelRatio: 1 });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('performance', { now: () => now });
});
afterEach(() => vi.unstubAllGlobals());

type Inner = { resize(): void; draw(): void; inset: { left: number } };

test('the view is placed at once when its stage is shown again, never glided from the hidden stage', () => {
  const view = new View(canvas as unknown as HTMLCanvasElement);
  const inner = view as unknown as Inner;
  inner.resize(); now = 16; inner.draw();
  expect(inner.inset.left).toBe(0);
  // A page opens: the stage is hidden, the canvas 0×0 and the room around it reads as none.
  size = { width: 0, height: 0 }; inner.resize(); now = 32; inner.draw();
  // Back on the live view: the side bar's room is measured again and the canvas has its size.
  view.reserved = { right: 0, bottom: 0, shelf: 0, left: 300, edge: 0 };
  size = { width: 800, height: 600 }; now = 5000; inner.resize();
  expect(inner.inset.left).toBe(300);
  now = 5016; inner.draw();  // the next frame's draw, with the panels' room measured in it, is placed at once too
  expect(inner.inset.left).toBe(300);
  // Later changes still glide.
  view.reserved = { right: 0, bottom: 0, shelf: 0, left: 0, edge: 0 };
  now = 5032; inner.draw();
  expect(inner.inset.left).toBeGreaterThan(0);
  expect(inner.inset.left).toBeLessThan(300);
});
