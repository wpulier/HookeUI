// Synthetic exact producer samples at the existing public drawing seam.
// No fabricated source grant, native receipt or actual browser-pixel claim.
import { expect, test } from 'vitest';
import { tracksDrawing, tracksLabel, type MotionTracksWire, type TracksLayerWire } from './motiontracks';
import type { FrameBasis, FrameHeader, Point } from './protocol';

const sample: FrameBasis = {
  schema: 'frame-basis-v1', frameId: 75, captureId: null, run: 'original-run', boot: 'original-bridge', seq: 8075,
  epoch: 7, tracked: false, imageSize: [1280, 720], disc: null, offset: [650, -200], objective: 40,
  umPerPx: .5, scaleId: null, scale: { objective: 40, umPerPx: .5, source: 'estimated' },
};
const header: FrameHeader = { type: 'frame', id: 75, run: sample.run, boot: sample.boot, seq: sample.seq,
  frameBasis: sample, w: 1280, h: 720, dx: 650, dy: -200, epoch: 7, tracked: false, steady: true,
  speed: 0, t: 102.5, sent: 102.5, fov: null };
const item: MotionTracksWire & { basis: 'camera-pixels'; sample: FrameBasis } = {
  type: 'motion-tracks', space: 'frame', basis: 'camera-pixels', sample, id: 'm1', title: 'Motion', how: 'measured',
  capture: 60, epoch: 7, offset: [500, -300], from: 102, to: 102.5, seconds: .5, interval: '102–102.5',
  unit: 'px/s', umPerPx: null, scale: null, threshold: null, count: 1, left: 0,
  tracks: [{ id: 't1', moving: true, net: null, path: null, straightness: null,
    segments: [{ start: 'first', end: 'open', points: [[680, 330, 102], [694, 330, 102.5]] }] }],
};
const layer: TracksLayerWire = { type: 'tracks-layer', space: 'frame', tracks: 'm1', style: { opacity: 1, colour: 'id' },
  verified: true, why: '', correction: [5, 3], view: { epoch: 7, offset: [500, -300] } };
const same = (point: Point): Point => point;

test('current-camera paths draw on their exact source frame without historical displacement', () => {
  const drawn = tracksDrawing(item, layer, header, same);
  expect(drawn).not.toBeNull();
  expect(drawn!.pieces).toHaveLength(1);
  expect(drawn!.pieces[0].from).toEqual([680, 330]);
  expect(drawn!.pieces[0].to).toEqual([694, 330]);
  expect(drawn!.marks.find((mark) => mark.kind === 'head')?.at).toEqual([694, 330]);
});

test.each([
  ['later frame', { id: 76, frameBasis: { ...sample, frameId: 76 } }],
  ['other run', { run: 'other-run', frameBasis: { ...sample, run: 'other-run' } }],
  ['other bridge', { boot: 'other-bridge', frameBasis: { ...sample, boot: 'other-bridge' } }],
  ['other sequence', { seq: 8076, frameBasis: { ...sample, seq: 8076 } }],
  ['other image geometry', { w: 640, frameBasis: { ...sample, imageSize: [640, 720] } }],
  ['other optics', { frameBasis: { ...sample, objective: 10, scale: { objective: 10, umPerPx: 2 } } }],
  ['unknown frame basis', { frameBasis: null }],
] as const)('current-camera paths refuse %s rather than borrowing newer pixels', (_name, changed) => {
  expect(tracksDrawing(item, layer, { ...header, ...changed } as FrameHeader, same)).toBeNull();
});

test('hidden or replaced current-camera layer never draws the old producer', () => {
  expect(tracksDrawing(item, { ...layer, verified: false }, header, same)).toBeNull();
  expect(tracksDrawing(item, { ...layer, tracks: 'm2' }, header, same)).toBeNull();
});

test('current-camera label distinguishes image motion from measured slide-relative speeds', () => {
  expect(tracksLabel(item)).toContain('camera');
  expect(tracksLabel(item)).not.toContain('speeds in');
});
