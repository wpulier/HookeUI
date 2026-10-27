/** A followed outline's trail: where it went on its own (services/orchestrator/trail.py).
 *
 * The server keeps each followed outline's verified places in slide coordinates (its own motion,
 * the slide's taken out) as segments within one motion epoch, broken wherever its place was not
 * known: a gap, a loss, a change of epoch. This draws the current epoch's segments over the live
 * view at `p + offset_now`, as outlines are drawn, older pieces fainter, a ring where it was found
 * again and a cross where it was lost. Nothing joins two segments. Pure, so it is tested
 * (trail.test.ts); view.ts strokes what it returns. */
import type { FrameHeader, Point } from './protocol';
import type { Space } from './spaces';

export interface TrailSegment {
  epoch: number;
  start: 'first' | 'after-gap' | 'reacquired' | 'after-correction';
  end: 'open' | 'gap' | 'lost' | 'stopped' | 'corrected'; // corrected: the place was re-registered, never joined
  points: [number, number, number][]; // x, y in slide px; t in server seconds
}
export interface TrailWire { id: string; segments: TrailSegment[]; words: string | null }
export interface TrailsMessage { type: 'trails'; space: Space; offset: [number, number]; trails: TrailWire[]; windowS: number }

export interface TrailPiece { from: Point; to: Point; alpha: number }
export interface TrailMark { at: Point; kind: 'reacquired' | 'lost'; alpha: number }
export interface TrailDrawing { pieces: TrailPiece[]; marks: TrailMark[]; head: Point | null }

/** The faintest an old piece is drawn before it ages out of the window. */
const FLOOR = 0.12;
/** A trail's words are drawn at its head only while its newest place is this fresh (s). */
export const HEAD_FRESH_S = 2;

/** How visible a place is by its age: 1 now, down to FLOOR at the window's end, 0 past it. */
export function ageAlpha(age: number, windowS: number): number {
  if (age > windowS) return 0;
  return Math.max(FLOOR, 1 - Math.max(0, age) / windowS);
}

/** What to draw of one trail on this frame: only its segments in this frame's epoch (another
 * epoch's places are not comparable with these), each place at `p + (h.dx, h.dy)`, through `to`
 * (frame px to canvas). */
export function trailDrawing(trail: TrailWire, h: FrameHeader, windowS: number, to: (pt: Point) => Point): TrailDrawing {
  const pieces: TrailPiece[] = [];
  const marks: TrailMark[] = [];
  let head: Point | null = null;
  const place = ([x, y]: [number, number, number]): Point => to([x + h.dx, y + h.dy]);
  for (const seg of trail.segments) {
    if (seg.epoch !== h.epoch || !seg.points.length) continue;
    const pts = seg.points;
    for (let i = 1; i < pts.length; i++) {
      const alpha = ageAlpha(h.t - pts[i][2], windowS);
      if (alpha > 0) pieces.push({ from: place(pts[i - 1]), to: place(pts[i]), alpha });
    }
    const first = pts[0];
    const last = pts[pts.length - 1];
    if (seg.start === 'reacquired') {
      const alpha = ageAlpha(h.t - first[2], windowS);
      if (alpha > 0) marks.push({ at: place(first), kind: 'reacquired', alpha });
    }
    if (seg.end === 'lost') {
      const alpha = ageAlpha(h.t - last[2], windowS);
      if (alpha > 0) marks.push({ at: place(last), kind: 'lost', alpha });
    }
    if (seg.end === 'open' && h.t - last[2] <= HEAD_FRESH_S) head = place(last);
  }
  return { pieces, marks, head };
}

/** Whose trail is labelled: the selected outline's, else the only one that says it went somewhere. */
export function labelled(trails: TrailWire[], selected: string | null): TrailWire | null {
  const said = trails.filter((t) => t.words);
  return said.find((t) => t.id === selected) ?? (said.length === 1 ? said[0] : null);
}
