// One motion for the Stage and its side columns (side-motion.ts; b041): which elements move on a press, on one clock
// with pixel keyframes on the house curve; a closing column inert at once and hidden only when it lands; reduced motion
// landing at once; a second press starting from where things are; work held until the sides land.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { holdUntilLanded, playSideMotion, SIDE_EASE, SIDE_MS, sideMoving, sideTracks, expectSideMotion, type Sides } from './side-motion';

type FakeAnimation = { keyframes: Keyframe[]; options: KeyframeAnimationOptions; startTime: number | null; progress: number; cancelled: boolean;
  finished: Promise<void>; resolve: () => void; cancel(): void; effect: { getComputedTiming(): { progress: number } } };
class Node {
  hidden = false; inert = false; animations: FakeAnimation[] = [];
  readonly name: string;
  constructor(name: string) { this.name = name; }
  animate(keyframes: Keyframe[], options: KeyframeAnimationOptions): FakeAnimation {
    let resolve!: () => void, reject!: (e: unknown) => void;
    const finished = new Promise<void>((ok, no) => { resolve = ok; reject = no; });
    finished.catch(() => {});
    const animation: FakeAnimation = { keyframes, options, startTime: null, progress: 0, cancelled: false, finished, resolve,
      cancel() { if (!this.cancelled) { this.cancelled = true; reject(new Error('AbortError')); } }, effect: { getComputedTiming: () => ({ progress: animation.progress }) } };
    this.animations.push(animation);
    return animation;
  }
  get last() { return this.animations[this.animations.length - 1]; }
}
let reduced = false;
const body = { dataset: {} as Record<string, string> };
beforeEach(() => {
  reduced = false; body.dataset = {};
  vi.stubGlobal('document', { body, hidden: false });
  vi.stubGlobal('matchMedia', () => ({ matches: reduced }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const studio = new Node('studio'), strip = new Node('strip');
const el = (n: Node) => n as unknown as HTMLElement;
const sides = (s: Partial<Sides>): Sides => ({ viewport: 1440, rail: 72, stage: { left: 72, right: 360 }, left: null, right: null, under: 360, ...s });
const settle = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };

test('opening a left column moves the Stage’s left edge and the column together; switching places plays nothing', () => {
  const drawer = new Node('drawer'), data = new Node('data'), panel = new Node('assistant');
  const right = { node: el(panel), width: 360 };
  const closed = sides({ right }), open = sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 }, right });
  const tracks = sideTracks(closed, open, el(studio), el(strip));
  expect(tracks.map((t) => [(t.node as unknown as Node).name, t.props, t.closing])).toEqual([
    ['studio', { marginLeft: [72, 412], width: [1008, 668] }, false],
    ['drawer', { left: [-268, 72], width: [340, 340] }, false], // out from behind the rail, its edge on the Stage's
  ]);
  // Data in place of the drawer, both open: nothing moves (and nothing fades in from the page colour).
  expect(sideTracks(open, { ...open, left: { node: el(data), width: 340 } }, el(studio), el(strip))).toEqual([]);
  expect(sideTracks(open, open, el(studio), el(strip))).toEqual([]);
});

test('below 1200 px a left press that closes the Assistant moves both edges and the strip’s reach in the same motion', () => {
  const drawer = new Node('drawer'), panel = new Node('assistant');
  const a = sides({ viewport: 1100, stage: { left: 72, right: 360 }, right: { node: el(panel), width: 360 }, under: 360 });
  const b = sides({ viewport: 1100, stage: { left: 392, right: 0 }, left: { node: el(drawer), width: 320 }, under: 0 });
  const tracks = sideTracks(a, b, el(studio), el(strip));
  expect(tracks.map((t) => [(t.node as unknown as Node).name, t.props, t.closing])).toEqual([
    ['studio', { marginLeft: [72, 392], width: [668, 708] }, false],
    ['drawer', { left: [-248, 72], width: [320, 320] }, false],
    ['assistant', { right: [0, -360], width: [360, 360] }, true], // its width held while the layout's says 0
    ['strip', { marginRight: [-360, -0] }, false],
  ]);
});

test('one clock: every edge starts at the same explicit time, 200 ms on the house curve, pixel keyframes held at both ends', () => {
  const drawer = new Node('drawer');
  const tracks = sideTracks(sides({}), sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 } }), el(studio), el(strip));
  playSideMotion(tracks, () => true, () => 1234.5);
  for (const node of [studio, drawer]) {
    expect(node.last.startTime).toBe(1234.5);
    expect(node.last.options).toMatchObject({ duration: SIDE_MS, easing: SIDE_EASE, fill: 'both' });
  }
  expect(SIDE_MS).toBe(200); expect(SIDE_EASE).toBe('cubic-bezier(.2,.8,.2,1)');
  expect(studio.last.keyframes).toEqual([{ marginLeft: '72px', width: '1008px' }, { marginLeft: '412px', width: '668px' }]);
  expect(drawer.last.keyframes).toEqual([{ left: '-268px', width: '340px' }, { left: '72px', width: '340px' }]);
  expect(body.dataset.sideMotion).toBe('true');
  for (const node of [studio, drawer]) node.last.resolve();
});

test('a closing column is inert at once and stays shown through its exit, hidden only when it lands; held work runs once then', async () => {
  const drawer = new Node('drawer');
  drawer.hidden = true; // the layout write hid it
  const tracks = sideTracks(sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 } }), sides({}), el(studio), el(strip));
  expectSideMotion();
  const fit = vi.fn();
  expect(holdUntilLanded(fit)).toBe(true); // the press's layout asked for it before the motion started
  playSideMotion(tracks, () => true, () => 0);
  expect(drawer.hidden).toBe(false); expect(drawer.inert).toBe(true);
  expect(drawer.last.keyframes).toEqual([{ left: '72px', width: '340px' }, { left: '-268px', width: '340px' }]);
  expect(holdUntilLanded(fit)).toBe(true); expect(holdUntilLanded(fit)).toBe(true); // asked each frame: still once
  expect(sideMoving()).toBe(true);
  studio.last.resolve(); drawer.last.resolve(); await settle();
  expect(drawer.hidden).toBe(true); expect(drawer.inert).toBe(false);
  expect(studio.last.cancelled && drawer.last.cancelled).toBe(true); // nothing left holding a value over the CSS
  expect(body.dataset.sideMotion).toBeUndefined();
  expect(fit).toHaveBeenCalledTimes(1);
  expect(sideMoving()).toBe(false); expect(holdUntilLanded(fit)).toBe(false);
});

test('the end comes on a timer too, if the animations never say they finished (a page put in the background)', () => {
  vi.useFakeTimers();
  const drawer = new Node('drawer');
  playSideMotion(sideTracks(sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 } }), sides({}), el(studio), el(strip)), () => true, () => 0);
  expect(drawer.hidden).toBe(false);
  vi.advanceTimersByTime(SIDE_MS + 60);
  expect(drawer.hidden).toBe(true); expect(drawer.inert).toBe(false); expect(body.dataset.sideMotion).toBeUndefined();
});

test('a press while the sides move starts from where they are, and a column opened again is not hidden', async () => {
  const drawer = new Node('drawer');
  const closed = sides({}), open = sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 } });
  playSideMotion(sideTracks(open, closed, el(studio), el(strip)), () => false, () => 0); // closing...
  studio.last.progress = 0.5; drawer.last.progress = 0.5;
  const first = drawer.last;
  playSideMotion(sideTracks(closed, open, el(studio), el(strip)), () => false, () => 100); // ...opened again half way
  expect(first.cancelled).toBe(true);
  expect(studio.last.keyframes[0]).toEqual({ marginLeft: '242px', width: '838px' });
  expect(drawer.last.keyframes[0]).toEqual({ left: '-98px', width: '340px' });
  expect(drawer.inert).toBe(false);
  studio.last.resolve(); drawer.last.resolve(); await settle();
  expect(drawer.hidden).toBe(false);
});

test('reduced motion: everything lands in one frame, nothing animates', () => {
  reduced = true;
  const drawer = new Node('drawer');
  const before = studio.animations.length;
  playSideMotion(sideTracks(sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 } }), sides({}), el(studio), el(strip)), () => true, () => 0);
  expect(studio.animations.length).toBe(before); expect(drawer.animations).toHaveLength(0);
  expect(drawer.hidden).toBe(true); expect(drawer.inert).toBe(false);
  expect(body.dataset.sideMotion).toBeUndefined();
});

test('a layout written while a column slides out leaves it to the motion; a motion that cannot be made lands at once', async () => {
  const { sideLeaving } = await import('./side-motion');
  const drawer = new Node('drawer');
  playSideMotion(sideTracks(sides({ stage: { left: 412, right: 360 }, left: { node: el(drawer), width: 340 } }), sides({}), el(studio), el(strip)), () => true, () => 0);
  expect(sideLeaving(el(drawer))).toBe(true); expect(sideLeaving(el(studio))).toBe(false);
  studio.last.resolve(); drawer.last.resolve(); await settle();
  expect(sideLeaving(el(drawer))).toBe(false);
  // An element that refuses to animate: nothing is left half made, the closing column is hidden, held work runs.
  const broken = new Node('broken');
  broken.animate = () => { throw new Error('no'); };
  const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
  const fit = vi.fn(); expectSideMotion(); holdUntilLanded(fit);
  const tracks = sideTracks(sides({ stage: { left: 412, right: 360 }, left: { node: el(broken), width: 340 } }), sides({}), el(studio), el(strip));
  playSideMotion(tracks, () => true, () => 0);
  expect(studio.last.cancelled).toBe(true); expect(broken.hidden).toBe(true); expect(broken.inert).toBe(false);
  expect(fit).toHaveBeenCalledTimes(1); expect(sideMoving()).toBe(false); expect(body.dataset.sideMotion).toBeUndefined();
  quiet.mockRestore();
});
