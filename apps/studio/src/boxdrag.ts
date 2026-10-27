// Drag a box on the live view to outline what is inside it. A press that moves less than
// DRAG_MIN_PX is a plain click, exactly as before; past it a dashed box follows the pointer and,
// on release, the studio sends one click with that box in frame pixels. Escape (or the browser
// taking the pointer away) cancels it, and the click the browser fires on release is then
// swallowed so a cancelled drag never becomes a point click. Pure, so it is unit tested
// (boxdrag.test.ts); main.ts wires it to the canvas and view.ts draws `rect()`.

import type { Point } from './protocol';

export type Box = [number, number, number, number]; // x0, y0, x1, y1, smallest first

/** Screen px a press may move and still be a click (a hand on a trackpad wobbles a little). */
export const DRAG_MIN_PX = 8;
/** Frame px: a box narrower than this has nothing inside to outline (the server says the same). */
export const MIN_BOX_FRAME_PX = 2;

/** What a release does: nothing (no press, or the drag was cancelled), a plain click at the
 * press, or a box between the press and the release (screen px, in drag order). */
export type Release = { kind: 'none' } | { kind: 'click' } | { kind: 'box'; from: Point; to: Point };

export class BoxDrag {
  private from: Point | null = null;
  private to: Point | null = null;
  private moved = false;
  private swallow = false;

  /** The pointer went down on the live view. */
  press(x: number, y: number): void {
    this.from = [x, y];
    this.to = [x, y];
    this.moved = false;
    this.swallow = false; // a release that never sent its click does not eat the next one
  }

  /** The pointer moved; true while it is a box drag (it stays one once past the threshold). */
  move(x: number, y: number): boolean {
    if (!this.from) return false;
    this.to = [x, y];
    if (!this.moved && Math.hypot(x - this.from[0], y - this.from[1]) > DRAG_MIN_PX) this.moved = true;
    return this.moved;
  }

  /** The pointer came up: a click, a box, or nothing. */
  release(x: number, y: number): Release {
    const from = this.from;
    if (!from) return { kind: 'none' };
    this.move(x, y);
    const box: Release = this.moved ? { kind: 'box', from, to: [x, y] } : { kind: 'click' };
    this.swallow = this.moved; // the browser's click after a drag is not a click
    this.from = this.to = null;
    this.moved = false;
    return box;
  }

  /** Escape or a lost pointer: the drag is dropped and its release does nothing. */
  cancel(): void {
    if (this.from && this.moved) this.swallow = true;
    this.from = this.to = null;
    this.moved = false;
  }

  get dragging(): boolean {
    return this.from !== null && this.moved;
  }

  /** The browser's click event: false when it ends a drag (or a cancelled one) and must be ignored. */
  clickAllowed(): boolean {
    const allowed = !this.swallow;
    this.swallow = false;
    return allowed;
  }

  /** The dashed box to draw while dragging (screen px, smallest corner first), else null. */
  rect(): Box | null {
    return this.dragging && this.from && this.to ? ordered(this.from, this.to) : null;
  }
}

/** Two corners as a box, smallest first, whichever way the drag went. */
export function ordered(a: Point, b: Point): Box {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

/** A dragged box in frame pixels: both corners through the view's screen-to-frame conversion
 * (the same one a click uses), ordered, and kept to the picture. Null when it is too small to
 * hold anything or a corner cannot be converted. */
export function frameBox(from: Point, to: Point, toFrame: (x: number, y: number) => Point | null,
  size: { w: number; h: number }): Box | null {
  const a = toFrame(from[0], from[1]);
  const b = toFrame(to[0], to[1]);
  if (!a || !b) return null;
  const [x0, y0, x1, y1] = ordered(a, b);
  const box: Box = [clamp(x0, size.w), clamp(y0, size.h), clamp(x1, size.w), clamp(y1, size.h)];
  if (box[2] - box[0] < MIN_BOX_FRAME_PX || box[3] - box[1] < MIN_BOX_FRAME_PX) return null;
  return box;
}

const clamp = (v: number, max: number) => Math.min(Math.max(v, 0), max);
