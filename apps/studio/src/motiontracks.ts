// Motion tracks kept as evidence (services/orchestrator/motiontracks.py; M3.2 of
// docs/design/2026-10-06-captures-operations-evidence.md, contracts 3 and 10): what track_motion measured,
// or a followed outline's trail the user kept. They are drawn over the live view only while the server has
// verified their place (their own view's pixels matched where the slide's move puts them, in the same
// motion epoch), moved with the slide from the view they were measured on. Older parts are fainter, every
// segment is drawn on its own (a break is never joined), and the interval and units are said. Otherwise the
// server shows them on their captured view, in the magnifier. Pure: drawing is view.ts's.
import { heat, PALETTE } from './population';
import type { FrameBasis, FrameHeader, Point } from './protocol';
import type { Space } from './spaces';
import type { ScreenItem } from './screen';

export interface TrackSegmentWire {
  start: string; // first, after-gap, reacquired, after-correction
  end: string; // end, open, gap, lost, stopped, corrected
  points: [number, number, number][]; // x, y in the pixels of the view it was measured on; t (s)
}
export interface TrackWire {
  id: string;
  moving: boolean;
  net: number | null;
  path: number | null;
  straightness: number | null;
  segments: TrackSegmentWire[];
}
export interface MotionTracksWire {
  type: 'motion-tracks';
  space: Space; // capture: points are in the pixels of `capture`'s view, plus `offset` placed on the live frame
  id: string;
  title: string; // its plain title ("Motion · 18:04, 4 s")
  how: string; // measured | followed
  basis?: 'camera-pixels';
  sample?: FrameBasis; // exact producing JPEG basis for continuing camera-space observations
  capture: number;
  epoch: number | null;
  offset: [number, number]; // its view's slide offset
  from: number;
  to: number;
  seconds: number;
  interval: string; // "18:04:01-18:04:05"
  unit: string; // µm/s or px/s
  umPerPx: number | null;
  scale: string | null; // measured | estimated | null
  threshold: number | null;
  count: number;
  left: number; // tracks kept but not sent (the slowest)
  tracks: TrackWire[];
}
export interface TracksStyle {
  opacity: number;
  colour: string; // column (by a measurement) | uniform | id
  column?: string;
  value?: string;
  values?: Record<string, number>;
  legend?: { label: string; min: number; max: number; unit: string };
  emphasis?: { members: string[]; rest: 'fade' | 'hide' };
}
export interface TracksLayerWire {
  lastChange?: { by: 'user' | 'assistant'; at: number };
  type: 'tracks-layer';
  space: Space;
  tracks: string | null;
  style: TracksStyle;
  verified: boolean;
  why: string;
  correction: [number, number];
  view: { epoch: number | null; offset: [number, number] } | null;
}

/** Installed structural projection of the current tracks layer; no native media authority. */
export function tracksScreenItem(view: { readonly shownTracks: MotionTracksWire | null; readonly tracksLayer: TracksLayerWire | null;
  hideOverlays: boolean; tracksDrawnNow(): boolean;
  overlayPresentation(kind: 'tracks'): { id: string; title: string; open: boolean } | null }): ScreenItem | null {
  const tracks = view.shownTracks;
  const tlayer = view.tracksLayer;
  const presentation = view.overlayPresentation('tracks');
  if (presentation && tlayer) {
    const { opacity, values: _tvalues, legend: _tlegend, ...tstyle } = tlayer.style;
    return { id: `tracks:${presentation.id}`, kind: 'tracks', title: presentation.title, shows: presentation.id,
      open: presentation.open, visible: presentation.open && !view.hideOverlays,
      // Paired camera-space trails do not claim historical field registration or organism identity.
      opacity, style: tstyle, placement: !presentation.open ? 'hidden' : view.tracksDrawnNow() ? (tracks?.basis === 'camera-pixels'
        ? `current-camera trails on frame ${tracks.sample?.frameId ?? 'unknown'}; includes camera/stage motion`
        : 'historical motion trajectories verified in place')
        : `not drawn live: ${tlayer.why || (tlayer.verified ? 'not on this view' : 'not checked yet')}`, z: 1 };
  }
  return null;
}

export interface TrackPiece { from: Point; to: Point; alpha: number; colour: string; width: number }
export interface TrackMark { at: Point; kind: 'reacquired' | 'lost' | 'head'; alpha: number; colour: string }
export interface TracksDrawing { pieces: TrackPiece[]; marks: TrackMark[] }

/** The faintest the oldest part of a track is drawn. */
export const FLOOR = 0.25;

/** How strongly a place is drawn by its age within the measured interval: FLOOR at its start, 1 at its end. */
export function ageAlpha(t: number, from: number, to: number): number {
  if (!(to > from)) return 1;
  return FLOOR + (1 - FLOOR) * Math.max(0, Math.min(1, (t - from) / (to - from)));
}

function paletteOf(id: string): string {
  let n = 0;
  for (let i = 0; i < id.length; i++) n = (n * 31 + id.charCodeAt(i)) >>> 0;
  return PALETTE[n % PALETTE.length];
}

/** How one track is drawn under the style: its colour, how strongly, whether it is hidden. */
export function trackLook(track: TrackWire, style: TracksStyle | undefined): { colour: string; alpha: number; hidden: boolean } {
  const s = style ?? { opacity: 1, colour: 'column' };
  let colour = paletteOf(track.id);
  if (s.colour === 'uniform' && s.value) colour = s.value;
  else if (s.colour === 'column') colour = s.values && track.id in s.values ? heat(s.values[track.id]) : '#8a93a6';
  const chosen = s.emphasis ? s.emphasis.members.includes(track.id) : true;
  return { colour, alpha: chosen ? 1 : 0.2, hidden: !chosen && s.emphasis?.rest === 'hide' };
}

/** What to draw of the shown tracks on frame `h`, through `to` (frame px to canvas), or null: only the
 * item the layer shows, only while its place is verified, only in its epoch (or the one a verified rejoin
 * carried it to), moved with the slide from its view. Each segment's places are joined to each other only. */
export function tracksDrawing(item: MotionTracksWire | null, layer: TracksLayerWire | null, h: FrameHeader,
  to: (pt: Point) => Point): TracksDrawing | null {
  if (!item || !layer || layer.tracks !== item.id || !layer.verified) return null;
  const camera = item.basis === 'camera-pixels';
  if (camera && (!item.sample || !h.frameBasis || !sameFacts(item.sample, h.frameBasis)
    || item.sample.frameId !== h.id || item.sample.run !== h.run || item.sample.boot !== h.boot
    || item.sample.seq !== h.seq || item.sample.epoch !== h.epoch
    || item.sample.imageSize[0] !== h.w || item.sample.imageSize[1] !== h.h)) return null;
  const epoch = layer.view ? layer.view.epoch : item.epoch;
  const offset = layer.view ? layer.view.offset : item.offset;
  if (!camera && epoch !== h.epoch) return null;
  const [cx, cy] = layer.correction ?? [0, 0];
  const sx = camera ? 0 : h.dx - offset[0] + cx;
  const sy = camera ? 0 : h.dy - offset[1] + cy;
  const place = ([x, y]: [number, number, number]): Point => to([x + sx, y + sy]);
  const pieces: TrackPiece[] = [];
  const marks: TrackMark[] = [];
  for (const track of item.tracks) {
    const look = trackLook(track, layer.style);
    if (look.hidden) continue;
    const width = track.moving ? 2 : 1;
    for (const seg of track.segments) {
      const pts = seg.points;
      for (let i = 1; i < pts.length; i++) {
        pieces.push({ from: place(pts[i - 1]), to: place(pts[i]), alpha: look.alpha * ageAlpha(pts[i][2], item.from, item.to),
          colour: look.colour, width });
      }
      if (!pts.length) continue;
      const first = pts[0];
      const last = pts[pts.length - 1];
      if (seg.start === 'reacquired') marks.push({ at: place(first), kind: 'reacquired', alpha: look.alpha * ageAlpha(first[2], item.from, item.to), colour: look.colour });
      if (seg.end === 'lost') marks.push({ at: place(last), kind: 'lost', alpha: look.alpha * ageAlpha(last[2], item.from, item.to), colour: look.colour });
      else if ((camera || track.moving) && (seg.end === 'end' || seg.end === 'open')) marks.push({ at: place(last), kind: 'head', alpha: look.alpha, colour: look.colour });
    }
  }
  return { pieces, marks };
}

/** The interval and units, said beside the tracks: pixels a second when there is no scale (never µm). */
export function tracksLabel(item: MotionTracksWire): string {
  if (item.basis === 'camera-pixels') return `${item.title}: current camera-space trails · includes camera/stage motion`;
  const units = item.umPerPx
    ? `speeds in ${item.unit}${item.scale === 'estimated' ? ' (scale estimated from the objective)' : ''}`
    : 'speeds in px/s (no scale set)';
  const left = item.left > 0 ? ` · ${item.left} slowest not drawn` : '';
  return `${item.title}: ${item.interval} · ${units}${left}`;
}

/** Detached JSON facts agree independently of object key order. Arrays remain ordered. */
function sameFacts(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
  if (Array.isArray(a) || Array.isArray(b)) return Array.isArray(a) && Array.isArray(b)
    && a.length === b.length && a.every((v, i) => sameFacts(v, b[i]));
  const x = Object.entries(a), y = Object.entries(b);
  return x.length === y.length && x.every(([key, value]) => Object.hasOwn(b, key)
    && sameFacts(value, (b as Record<string, unknown>)[key]));
}

/** The items' wires the studio keeps, by id (the newest `most`): the one shown may change before or after its wire comes. */
export function keepTracks(kept: Map<string, MotionTracksWire>, item: MotionTracksWire, most = 4): Map<string, MotionTracksWire> {
  const next = new Map(kept);
  next.delete(item.id);
  next.set(item.id, item);
  while (next.size > most) next.delete(next.keys().next().value as string);
  return next;
}
