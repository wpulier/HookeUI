import { describe, expect, it } from 'vitest';
import type { FrameHeader, Point } from './protocol';
import { ageAlpha, labelled, trailDrawing, type TrailWire } from './trail';

const header = (over: Partial<FrameHeader> = {}): FrameHeader => ({
  type: 'frame', id: 9, w: 1000, h: 1000, dx: 50, dy: 0, epoch: 2, tracked: true, steady: true, speed: 0,
  t: 1060, sent: 1060, fov: [500, 500, 400], ...over,
});
const same = (pt: Point): Point => pt;

const trail: TrailWire = {
  id: 'o1',
  words: '15 µm over the slide in 10 s',
  segments: [
    { epoch: 1, start: 'first', end: 'gap', points: [[0, 0, 1050], [10, 0, 1051]] }, // another epoch
    { epoch: 2, start: 'first', end: 'lost', points: [[100, 100, 1000], [110, 100, 1030], [120, 100, 1059]] },
    { epoch: 2, start: 'reacquired', end: 'open', points: [[200, 100, 1059.5], [210, 100, 1060]] },
  ],
};

describe('a followed outline\'s trail', () => {
  it('draws only this epoch\'s segments, at the slide offset now, never across a break', () => {
    const d = trailDrawing(trail, header(), 60, same);
    // Two pieces in the lost segment and one in the reacquired one; none joins 120 to 200.
    expect(d.pieces.map((p) => [p.from[0], p.to[0]])).toEqual([[150, 160], [160, 170], [250, 260]]);
    expect(d.marks.map((m) => [m.kind, m.at[0]])).toEqual([['lost', 170], ['reacquired', 250]]);
    expect(d.head).toEqual([260, 100]);
    // Its words are drawn at the head only while its newest place is fresh.
    expect(trailDrawing(trail, header({ t: 1063 }), 60, same).head).toBeNull();
  });

  it('fades older pieces and drops what aged out of the window', () => {
    expect(ageAlpha(0, 60)).toBe(1);
    expect(ageAlpha(30, 60)).toBeCloseTo(0.5);
    expect(ageAlpha(59, 60)).toBeGreaterThan(0);
    expect(ageAlpha(61, 60)).toBe(0);
    // At 1095 the piece ending at 1030 is 65 s old: gone; the others are fainter than new.
    const d = trailDrawing(trail, header({ t: 1095 }), 60, same);
    expect(d.pieces.map((p) => p.from[0])).toEqual([160, 250]);
    expect(Math.max(...d.pieces.map((p) => p.alpha))).toBeLessThan(0.5);
    expect(trailDrawing(trail, header({ t: 1125 }), 60, same).pieces).toEqual([]);
  });

  it('labels the selected outline\'s trail, or the only one that went somewhere', () => {
    const quiet: TrailWire = { id: 'o2', words: null, segments: [] };
    const other: TrailWire = { ...trail, id: 'o3' };
    expect(labelled([trail, quiet], null)?.id).toBe('o1');
    expect(labelled([trail, other], null)).toBeNull();
    expect(labelled([trail, other], 'o3')?.id).toBe('o3');
  });
});
