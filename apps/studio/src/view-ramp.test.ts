// A new result's outlines ramp in once (view.ts rampOf; workspace-feel plan, "A run finishing"): only a population made
// after the page loaded, only the first time it is drawn, bounded to RAMP_MS, and never under reduced motion. A reload or
// an older result opened from Data appears at once.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { View } from './view';
import type { PopulationWire } from './population';

const noop: unknown = new Proxy(function () {}, { get: () => noop, apply: () => noop });
const canvas = { width: 0, height: 0, getContext: () => noop, getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600, right: 800, bottom: 600 }) };
let now = 0;
let reduced = false;
beforeEach(() => {
  now = 0; reduced = false;
  vi.stubGlobal('matchMedia', () => ({ matches: reduced }));
  vi.stubGlobal('window', { devicePixelRatio: 1 });
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', () => 0);
  vi.stubGlobal('performance', { now: () => now });
});
afterEach(() => vi.unstubAllGlobals());

type Ramp = { rampOf(pop: PopulationWire): number; loadedAt: number };
const pop = (id: string, at: number) => ({ id, at }) as unknown as PopulationWire;

test('a result made after the page loaded ramps in once over its first draws, bounded, then stays whole', () => {
  const view = new View(canvas as unknown as HTMLCanvasElement) as unknown as Ramp;
  const fresh = pop('e7', view.loadedAt / 1000 + 5);
  now = 1000; expect(view.rampOf(fresh)).toBe(0);
  now = 1160; expect(view.rampOf(fresh)).toBeCloseTo(0.5, 2);
  now = 1400; expect(view.rampOf(fresh)).toBe(1);
  now = 5000; expect(view.rampOf(fresh)).toBe(1); // drawn before: never again (a Tab switch, a redraw)
});

test('a result made before the page loaded (a reload, an older one opened from Data) appears at once', () => {
  const view = new View(canvas as unknown as HTMLCanvasElement) as unknown as Ramp;
  now = 1000; expect(view.rampOf(pop('e2', view.loadedAt / 1000 - 60))).toBe(1);
});

test('reduced motion: a new result appears at once', () => {
  reduced = true;
  const view = new View(canvas as unknown as HTMLCanvasElement) as unknown as Ramp;
  now = 1000; expect(view.rampOf(pop('e8', view.loadedAt / 1000 + 5))).toBe(1);
});
