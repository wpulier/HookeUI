// The slide map's geometry (slidemap.ts): the view's circle and the outlines' marks land on the
// map picture where the server pasted the same slide px (orchestrator/slidemap.py).
import { describe, expect, test } from 'vitest';
import type { FrameHeader, SceneObject, SlideMapState } from './protocol';
import { mapNote, marksOnMap, onMap, viewOnMap } from './slidemap';

const state = (over: Partial<SlideMapState> = {}): SlideMapState => ({
  type: 'slidemap', space: 'map', open: true, epoch: 2, scale: 0.25, maps: 1, version: 5, url: '/api/slidemap.jpg?epoch=2&v=5',
  origin: [100, 50], size: [400, 300], ...over,
});

const header = (dx: number, dy: number, epoch = 2): FrameHeader => ({
  type: 'frame', id: 9, w: 1280, h: 720, dx, dy, epoch, tracked: true, steady: true, speed: 0, t: 0, sent: 0, fov: [640, 360, 300],
});

const outline = (id: string, x: number, y: number, over: Partial<SceneObject> = {}): SceneObject => ({
  id, kind: 'highlight', polygons: [[[x - 10, y - 10], [x + 10, y - 10], [x + 10, y + 10], [x - 10, y + 10]]], anchorFrame: 1,
  anchorEpoch: 2, anchorOffset: [0, 0], source: 'click', label: 'stoma', labelKey: 'stoma', status: 'likely', confidence: null,
  note: '', seen: '', group: null, trial: null, parent: null, arm: null, track: 'tracking', revisedBy: null, pinned: null,
  createdAt: 0, ...over,
});

describe('slide map', () => {
  test('frame px go to slide px (p - offset), then to map picture px', () => {
    // Slide px (1000, 600) x 0.25 = map px (250, 150); the picture starts at map px (100, 50).
    expect(onMap(state(), 1000, 600, 0, 0)).toEqual([150, 100]);
    expect(onMap(state(), 1400, 800, 400, 200)).toEqual([150, 100]); // the same place after the slide moved
    expect(onMap(state({ origin: null }), 0, 0, 0, 0)).toBeNull();
  });

  test('the view is the eyepiece disc of the newest frame, only in the map\'s epoch', () => {
    expect(viewOnMap(state(), header(0, 0))).toEqual({ x: 60, y: 40, r: 75 });
    // The slide moved: content went 400 px right, so the view is 100 map px further left on the slide.
    expect(viewOnMap(state(), header(400, 0))).toEqual({ x: -40, y: 40, r: 75 });
    expect(viewOnMap(state(), header(0, 0, 3))).toBeNull();
    expect(viewOnMap(state(), null)).toBeNull();
  });

  test('outlines are marked where they were made, in the map\'s epoch only', () => {
    const marks = marksOnMap(state(), [
      outline('o1', 1000, 600),
      outline('o2', 1000, 600, { anchorOffset: [-400, 0] }), // made after the slide moved the other way
      outline('o3', 1000, 600, { anchorEpoch: 1 }),
      outline('o4', 1000, 600, { trial: 't1' }),
      outline('c1', 1000, 600, { kind: 'cell' }),
    ]);
    expect(marks).toEqual([
      { id: 'o1', label: 'stoma', x: 150, y: 100 },
      { id: 'o2', label: 'stoma', x: 250, y: 100 },
    ]);
  });

  test('the note says what is on it', () => {
    expect(mapNote(state({ url: null }))).toMatch(/^Empty so far/);
    expect(mapNote(state())).toMatch(/The circle is the view now\.$/);
    expect(mapNote(state({ maps: 3 }))).toMatch(/2 earlier maps from before the view lost track are kept\.$/);
  });
});
