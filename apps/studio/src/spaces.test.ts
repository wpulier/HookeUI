// Coordinate spaces (spaces.ts; the server's twin is services/orchestrator/spaces.py): the mappings
// between frame, capture, map and an enlarged view round-trip, and agree with the code that draws.
import { describe, expect, test } from 'vitest';
import { onMap } from './slidemap';
import type { SlideMapState } from './protocol';
import { tracksDrawing, type MotionTracksWire, type TracksLayerWire } from './motiontracks';
import { magnifierWindow, placeFrame } from './view';
import type { FrameHeader } from './protocol';
import { frameToMagnifier, frameToWorkspace, magnifierToFrame, pictureToPx, PICTURE_UNITS, pxToPicture, workspaceToFrame, itemSpace, SPACES, WIRES, captureToFrame, captureToView, frameToFrame, frameToMap, frameToSlide, mapToSlide,
  slideToFrame, slideToMap, viewToCapture, type Pt } from './spaces';

const near = (a: Pt, b: Pt) => {
  expect(a[0]).toBeCloseTo(b[0], 9);
  expect(a[1]).toBeCloseTo(b[1], 9);
};

describe('the vocabulary', () => {
  test('every wire and item states one of the five spaces', () => {
    for (const space of Object.values(WIRES)) expect(SPACES).toContain(space);
    for (const kind of ['live', 'population', 'tracks', 'outlines', 'magnifier', 'viewer', 'slidemap', 'card', 'made-today', 'sidebar', 'sessions']) {
      expect(SPACES).toContain(itemSpace(kind));
    }
    expect(itemSpace('entry')).toBe('workspace'); // a kind with no entry (entry, anything new) is the workspace's, as the server's
    expect(itemSpace('weird')).toBe('workspace');
    expect(itemSpace('magnifier', 'held')).toBe('capture');
    expect(itemSpace('magnifier', 'live')).toBe('frame');
    expect(itemSpace('magnifier', '/research/a.png')).toBe('picture');
  });
});

describe('the mappings', () => {
  test('frame and capture by the offsets, and back', () => {
    const p: Pt = [120, 80];
    const anchor: Pt = [10, -4];
    const now: Pt = [35.5, 9.25];
    const there = frameToFrame(p, anchor, now);
    expect(there).toEqual([145.5, 93.25]);
    near(frameToFrame(there, now, anchor), p);
    near(frameToSlide(p, anchor), frameToSlide(there, now));
    near(slideToFrame(frameToSlide(p, anchor), anchor), p);
    expect(captureToFrame(p, anchor, now, [2, -1])).toEqual([147.5, 92.25]);
  });

  test('the slide map, and the same as the map panel draws', () => {
    const p: Pt = [640, 360];
    const offset: Pt = [-120, 40];
    const state = { type: 'slidemap', space: 'map', open: true, epoch: 0, scale: 0.25, maps: 1, version: 1, url: '/x', origin: [-50, -20],
      size: [100, 100] } as SlideMapState;
    const on = frameToMap(p, offset, 0.25, [-50, -20]);
    near(on, onMap(state, p[0], p[1], offset[0], offset[1]) as Pt);
    near(slideToFrame(mapToSlide(on, 0.25, [-50, -20]), offset), p);
    near(mapToSlide(slideToMap([300, 200], 0.25, [-50, -20]), 0.25, [-50, -20]), [300, 200]);
  });

  test('an enlarged view is a crop and a scale of its capture', () => {
    const crop = { x: 200, y: 100, width: 160, height: 120 };
    expect(viewToCapture([0, 0], crop, 4)).toEqual([200, 100]);
    expect(viewToCapture([640, 480], crop, 4)).toEqual([360, 220]);
    near(captureToView(viewToCapture([321.5, 77.25], crop, 4), crop, 4), [321.5, 77.25]);
  });

  test('the tracks drawing places a capture pixel as captureToFrame does', () => {
    const item = { type: 'motion-tracks', space: 'capture', id: 'm1', title: '', how: 'measured', capture: 5, epoch: 0, offset: [10, 0],
      from: 0, to: 4, seconds: 4, interval: '', unit: 'px/s', umPerPx: null, scale: null, threshold: null, count: 1, left: 0,
      tracks: [{ id: 't1', moving: true, net: 1, path: 1, straightness: 1,
        segments: [{ start: 'first', end: 'end', points: [[100, 50, 0], [110, 50, 1]] }] }] } as MotionTracksWire;
    const layer = { type: 'tracks-layer', space: 'frame', tracks: 'm1', style: { opacity: 1, colour: 'uniform' }, verified: true, why: '',
      correction: [2, -1], view: null } as TracksLayerWire;
    const header = { epoch: 0, dx: 35.5, dy: 9.25, t: 0 } as never;
    const drawn = tracksDrawing(item, layer, header, (pt) => pt)!;
    near(drawn.pieces[0].from, captureToFrame([100, 50], [10, 0], [35.5, 9.25], [2, -1]));
  });
});

// The frame reaches the workspace through the studio's own layout (view.ts placeFrame, magnifierWindow).
const header = { w: 1280, h: 720, fov: [640, 360, 300], dx: 0, dy: 0, epoch: 0 } as unknown as FrameHeader;

describe('the frame in the workspace, for each layout', () => {
  const layouts: [string, number, number, number, number][] = [
    // name, stage width, stage height, reserve (shelf), side column open (0-1)
    ['a wide stage', 1400, 800, 0, 0],
    ['the side column open', 1400, 800, 0, 1],
    ['a phone with its sheet up', 390, 440, 0, 0],
    ['a phone with the shelf', 390, 700, 90, 0],
  ];
  for (const [name, width, height, reserve, open] of layouts) {
    test(`${name}: the eyepiece centre lands where the layout puts it, and back`, () => {
      const place = placeFrame(header, width, height, reserve, false, open);
      const canvas: Pt = [0, 56];
      const centre = frameToWorkspace([640, 360], place, canvas);
      const r = 300 * place.scale;
      const h = Math.max(120, height - reserve);
      if (open === 0) {
        expect(centre[0]).toBeCloseTo(width / 2, 6);
      } else {
        expect(centre[0] + r).toBeCloseTo(width - 16, 6); // beside the column, its right edge 16 px from it
      }
      expect(centre[1]).toBeCloseTo(canvas[1] + h / 2, 6);
      near(workspaceToFrame(centre, place, canvas), [640, 360]);
      expect(place.scale).toBeGreaterThan(0);
      expect(r * 2).toBeLessThanOrEqual(Math.max(width, h) + 1e-6); // the disc fits the stage
    });
  }

  test('the whole frame, when asked, is centred in the stage', () => {
    const place = placeFrame(header, 1400, 800, 0, true, 0);
    near(frameToWorkspace([640, 360], place), [700, 400]);
  });

  test('a zoomed magnifier maps its frame window to its panel, and back', () => {
    const box = { w: 40, h: 30, cx: 500, cy: 300 };
    const fit = magnifierWindow(box, 1, null, 352, 264);
    const zoomed = magnifierWindow(box, 4, null, 352, 264);
    expect(zoomed.sw).toBeCloseTo(fit.sw / 4, 9);
    expect(zoomed.k).toBeCloseTo(fit.k * 4, 9);
    near(frameToMagnifier([500, 300], zoomed, [900, 120]), [900 + 176, 120 + 132]); // the object's centre is the panel's
    near(magnifierToFrame(frameToMagnifier([507, 296], zoomed, [900, 120]), zoomed, [900, 120]), [507, 296]);
    const centred = magnifierWindow(box, 4, [520, 310], 352, 264); // a chosen centre
    near(frameToMagnifier([520, 310], centred, [0, 0]), [176, 132]);
  });
});

describe('a picture\'s fractions', () => {
  test('map to its pixels and back, and the unit is named', () => {
    expect(PICTURE_UNITS).toEqual(['px', 'fraction']);
    expect(pictureToPx([0.25, 0.5], [800, 600])).toEqual([200, 300]);
    near(pxToPicture(pictureToPx([0.3, 0.7], [800, 600]), [800, 600]), [0.3, 0.7]);
  });
});
