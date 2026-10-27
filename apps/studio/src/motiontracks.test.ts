// Motion tracks over the live view (motiontracks.ts): drawn only while verified, in their epoch, moved with
// the slide from the view they were measured on; older parts fainter; a break never joined; the interval and
// units said (pixels without a scale); the verbs' style (colour by speed, one colour, a selection).
import { describe, expect, test } from 'vitest';
import { ageAlpha, FLOOR, keepTracks, trackLook, tracksDrawing, tracksLabel, type MotionTracksWire, type TracksLayerWire } from './motiontracks';
import { heat } from './population';
import type { FrameHeader, Point } from './protocol';

const header = (dx = 0, epoch = 0): FrameHeader => ({
  type: 'frame', id: 9, w: 1280, h: 720, dx, dy: 0, epoch, tracked: true, steady: true, speed: 0, t: 0, sent: 0, fov: null,
});
const same = (pt: Point): Point => pt;

const item: MotionTracksWire = {
  type: 'motion-tracks', space: 'capture', id: 'm1', title: 'Motion · 18:04, 4 s', how: 'measured', capture: 5, epoch: 0, offset: [10, 0],
  from: 100, to: 104, seconds: 4, interval: '18:04:01-18:04:05', unit: 'px/s', umPerPx: null, scale: null, threshold: 8,
  count: 2, left: 0,
  tracks: [
    // a moving one, with a break: two segments, never joined
    { id: 't1', moving: true, net: 30, path: 32, straightness: 0.9, segments: [
      { start: 'first', end: 'gap', points: [[100, 100, 100], [110, 100, 101]] },
      { start: 'reacquired', end: 'lost', points: [[150, 100, 103], [160, 100, 104]] },
    ] },
    { id: 't2', moving: false, net: 1, path: 6, straightness: 0.1, segments: [{ start: 'first', end: 'end', points: [[300, 300, 100], [301, 300, 104]] }] },
  ],
};
const layer: TracksLayerWire = { type: 'tracks-layer', space: 'frame', tracks: 'm1', style: { opacity: 1, colour: 'column', column: 'net', values: { t1: 1, t2: 0 } },
  verified: true, why: '', correction: [0, 0], view: null };

describe('motion tracks on the live view', () => {
  test('drawn only while verified, only the item shown, only in its epoch', () => {
    expect(tracksDrawing(item, { ...layer, verified: false, why: 'its pixels did not match' }, header(), same)).toBeNull();
    expect(tracksDrawing(item, { ...layer, tracks: 'm2' }, header(), same)).toBeNull();
    expect(tracksDrawing(item, layer, header(0, 1), same)).toBeNull();
    expect(tracksDrawing(item, layer, header(), same)).not.toBeNull();
  });

  test('moved with the slide from the view they were measured on, with the correction', () => {
    const drawing = tracksDrawing(item, { ...layer, correction: [2, -1] }, header(30), same)!;
    expect(drawing.pieces[0].from).toEqual([100 + 20 + 2, 100 - 1]); // dx now 30, its view's 10
  });

  test('a rejoin carries them to the epoch rejoined', () => {
    const carried = { ...layer, view: { epoch: 2, offset: [40, 0] as [number, number] } };
    expect(tracksDrawing(item, carried, header(50, 2), same)!.pieces[0].from).toEqual([110, 100]);
    expect(tracksDrawing(item, carried, header(50, 0), same)).toBeNull();
  });

  test('a break is never joined: pieces only within a segment, a ring where found again, a cross where lost', () => {
    const drawing = tracksDrawing(item, layer, header(10), same)!;
    const joins = drawing.pieces.filter((p) => p.from[0] === 110 && p.to[0] === 150);
    expect(joins).toEqual([]);
    expect(drawing.pieces.length).toBe(3); // one per segment of t1, one for t2
    expect(drawing.marks.map((m) => m.kind).sort()).toEqual(['lost', 'reacquired']);
  });

  test('older parts are fainter', () => {
    expect(ageAlpha(100, 100, 104)).toBe(FLOOR);
    expect(ageAlpha(104, 100, 104)).toBe(1);
    const [early, , late] = tracksDrawing(item, layer, header(10), same)!.pieces;
    expect(early.alpha).toBeLessThan(late.alpha);
  });

  test('coloured by speed by default, one colour or each its own; a selection fades or hides the rest', () => {
    expect(trackLook(item.tracks[0], layer.style).colour).toBe(heat(1));
    expect(trackLook(item.tracks[0], { opacity: 1, colour: 'uniform', value: '#ffffff' }).colour).toBe('#ffffff');
    const fast = { ...layer.style, emphasis: { members: ['t1'], rest: 'hide' as const } };
    expect(trackLook(item.tracks[1], fast).hidden).toBe(true);
    expect(trackLook(item.tracks[1], { ...fast, emphasis: { members: ['t1'], rest: 'fade' } }).alpha).toBeLessThan(1);
    const hidden = tracksDrawing(item, { ...layer, style: fast }, header(10), same)!;
    expect(hidden.pieces.length).toBe(2); // only t1's
  });

  test('the interval and units are said: pixels without a scale, µm only with one', () => {
    expect(tracksLabel(item)).toBe('Motion · 18:04, 4 s: 18:04:01-18:04:05 · speeds in px/s (no scale set)');
    expect(tracksLabel({ ...item, umPerPx: 0.5, unit: 'µm/s', scale: 'estimated' })).toContain('speeds in µm/s (scale estimated');
  });

  test('the items kept are the newest few, by id', () => {
    let kept = new Map<string, MotionTracksWire>();
    for (const id of ['m1', 'm2', 'm3', 'm4', 'm5']) kept = keepTracks(kept, { ...item, id });
    expect([...kept.keys()]).toEqual(['m2', 'm3', 'm4', 'm5']);
  });
});
