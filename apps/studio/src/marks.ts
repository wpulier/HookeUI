// Marks: the voice pointing at things while it talks about them (the one-brain voice's `mark` tool; the partner
// contract's target grammar). It points by Hooke's own ids, not guessed pixels: an outline on the moving specimen, a
// window, a document, a Tab, a researcher's card, a panel, or a place in the camera frame the model saw. Each target is
// found again every animation frame, so a mark follows its outline as the slide moves and its window as it is dragged.
//
// The layer is a full-window SVG that takes no clicks. A mark is a dashed, slightly rough rounded box (or an arrow, or
// an underline) in the accent colour, drawn in over about 300 ms and faded out; nothing bounces. Only what the voice
// asked for is drawn, and a target that cannot be found is not drawn: the reply (`marks-shown`) says which were drawn
// and why the others were not, so the tool tells the model the truth (ADR 0006).
//
// Marks stay while the voice speaks and clear about 1.5 s after it stops (`until: "speech-end"`), or after `until`
// ms; a new set replaces the last; pressing the talk key clears them (barge-in).
import type { MarkFrameRef, MarkResolution, MarksMessage, MarkSpec } from './protocol';

export type Rect = [number, number, number, number]; // x, y, width, height: window CSS px
export type Point = [number, number];
export type MarkStyle = 'box' | 'point' | 'underline';
export type MarkWhy = 'not-found' | 'out-of-view' | 'ambiguous' | 'hidden' | 'bad-target';
/** Where a target is now, or why it is not drawn. `shape` is an outline to trace instead of the box (frame polygons). */
export type Place = { rect: Rect; shape?: Point[] } | { why: MarkWhy };

export type Target =
  | { kind: 'outline'; id: string }
  | { kind: 'member'; population: string; member: string }
  | { kind: 'population'; id: string }
  | { kind: 'window'; id: string }
  | { kind: 'document'; id: string }
  | { kind: 'tab'; key: string }
  | { kind: 'researcher'; key: string }
  | { kind: 'panel'; name: string }
  | { kind: 'control'; id: string } // a control by its element id (the partner cursor's target: control:talk)
  | { kind: 'frame' };

/** `outline:o12`, `population:e1` (or `layer:e1`: a whole layer of cell outlines), `member:<population>:<member>`,
 * `window:<presentationId>`, `document:<placementId>`,
 * `tab:<view id or exact title>`, `researcher:<number or rid>`, `panel:<name>`, `frame`; null for anything else. */
export function parseTarget(target: unknown): Target | null {
  if (typeof target !== 'string' || target.length > 300) return null;
  if (target === 'frame') return { kind: 'frame' };
  const at = target.indexOf(':');
  if (at < 1) return null;
  const kind = target.slice(0, at), rest = target.slice(at + 1).trim();
  if (!rest) return null;
  switch (kind) {
    case 'outline': return { kind, id: rest };
    case 'population': case 'layer': return { kind: 'population', id: rest };
    case 'member': {
      const last = rest.lastIndexOf(':');
      if (last < 1 || last === rest.length - 1) return null;
      return { kind, population: rest.slice(0, last), member: rest.slice(last + 1) };
    }
    case 'window': case 'document': return { kind, id: rest };
    case 'tab': case 'researcher': return { kind, key: rest };
    case 'panel': return { kind, name: rest };
    case 'control': return { kind, id: rest };
    default: return null;
  }
}

/** The eyepiece crop the voice's camera frame is (services/orchestrator/marks.py fov_crop, the same rounding). */
export function fovCrop(fov: [number, number, number] | null | undefined, width: number, height: number, pad = 1.02): Rect {
  if (!fov) return [0, 0, width, height];
  const r = fov[2] * pad;
  const x0 = Math.max(0, Math.trunc(fov[0] - r)), y0 = Math.max(0, Math.trunc(fov[1] - r));
  const x1 = Math.min(width, Math.trunc(fov[0] + r)), y1 = Math.min(height, Math.trunc(fov[1] + r));
  return [x0, y0, x1 - x0, y1 - y0];
}

/** A normalised point (0-1) of the picture the model saw, in that camera frame's own pixels. */
export function fromNormalised([x, y]: Point, crop: Rect): Point {
  return [crop[0] + x * crop[2], crop[1] + y * crop[3]];
}

export function boundsOf(points: Point[]): Rect {
  const xs = points.map(p => p[0]), ys = points.map(p => p[1]);
  const x0 = Math.min(...xs), y0 = Math.min(...ys);
  return [x0, y0, Math.max(...xs) - x0, Math.max(...ys) - y0];
}

/** A frame mark's anchor: the crop of the camera frame the model saw and the slide's place then (dx, dy, epoch), so
 * the mark moves with the specimen from there. */
export interface FrameAnchor { crop: Rect; offset: [number, number, number] }

type Box = { left: number; top: number; width: number; height: number };
export interface MarkElement {
  getBoundingClientRect(): Box;
  closest(selector: string): unknown;
  querySelector?(selector: string): { textContent: string | null } | null;
  textContent: string | null;
  dataset: Record<string, string | undefined>;
}
export interface MarkDocument {
  querySelector(selector: string): MarkElement | null;
  querySelectorAll(selector: string): Iterable<MarkElement> | ArrayLike<MarkElement>;
}

/** What the studio supplies: the live view's places, the panels, and the page to search. */
export interface MarkHooks {
  doc: MarkDocument;
  viewport(): [number, number];
  outline(id: string): Place;
  member(population: string, member: string): Place;
  /** A layer of cell outlines (a population) as the box around its members drawn now; absent: not found. */
  population?(id: string): Place;
  /** The anchor of frame marks: the frame the model saw (`ref`), or the one shown now; null without a picture. */
  anchor(ref: MarkFrameRef | undefined): FrameAnchor | null;
  /** Camera-frame points (that frame's px) on the window now, moved with the slide since the anchor; or why not. */
  frame(points: Point[], anchor: FrameAnchor): Point[] | { why: MarkWhy };
  panel(name: string): Place;
  /** The researchers numbered `n` in this Project (usually one). */
  researcherRids(n: number): string[];
}

const attr = (value: string) => value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
const HIDDEN = '[hidden], [data-workspace-hidden="true"]';

/** An element's place in the window, or why it is not drawn: not laid out, inside something hidden, or off-screen. */
export function elementPlace(element: MarkElement | null, viewport: [number, number]): Place {
  if (!element) return { why: 'not-found' };
  if (element.closest(HIDDEN)) return { why: 'hidden' };
  const r = element.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return { why: 'hidden' };
  if (r.left + r.width <= 0 || r.top + r.height <= 0 || r.left >= viewport[0] || r.top >= viewport[1]) return { why: 'out-of-view' };
  return { rect: [r.left, r.top, r.width, r.height] };
}

const all = (doc: MarkDocument, selector: string) => Array.from(doc.querySelectorAll(selector) as ArrayLike<MarkElement>);

/** A Tab by its id, else by its exact title as its button shows it (then ignoring case); two Tabs of that title are
 * ambiguous, never a guess. */
export function tabPlace(doc: MarkDocument, key: string, viewport: [number, number]): Place {
  const byId = doc.querySelector(`[id="work-tab-${attr(key)}"]`);
  if (byId) return elementPlace(byId, viewport);
  const tabs = all(doc, '[data-workspace-view-tab]').map(tab => ({ tab,
    title: (tab.querySelector?.('.workspace-tab-label')?.textContent ?? tab.textContent ?? '').trim() }));
  let found = tabs.filter(t => t.title === key.trim());
  if (!found.length) found = tabs.filter(t => t.title.toLowerCase() === key.trim().toLowerCase());
  if (found.length > 1) return { why: 'ambiguous' };
  return found.length ? elementPlace(found[0].tab, viewport) : { why: 'not-found' };
}

const RESEARCHER_PLACES = ['.researchers-board-row', '.researcher-card', '.chat-rcard'];

/** A researcher by its number ("Researcher 3") or its rid: its row on the Researchers board, else its card, else its
 * card in the chat; the first that is on screen. */
export function researcherPlace(doc: MarkDocument, key: string, rids: (n: number) => string[], viewport: [number, number]): Place {
  const n = /^\d+$/.test(key) ? Number(key) : null;
  const candidates = n === null ? [key] : [...new Set(rids(n))];
  if (!candidates.length) return { why: 'not-found' };
  const shown: { rid: string; place: Place }[] = [];
  let why: MarkWhy = 'not-found';
  for (const rid of candidates) {
    for (const selector of RESEARCHER_PLACES) {
      const place = elementPlace(doc.querySelector(`${selector}[data-rid="${attr(rid)}"]`), viewport);
      if ('rect' in place) { shown.push({ rid, place }); break; }
      if (place.why !== 'not-found') why = place.why;
    }
  }
  if (shown.length > 1) return { why: 'ambiguous' };
  return shown[0]?.place ?? { why };
}

/** Where a mark's target is now. */
export function resolveTarget(spec: MarkSpec, hooks: MarkHooks, anchor: FrameAnchor | null): Place {
  const target = parseTarget(spec.target);
  if (!target) return { why: 'bad-target' };
  const viewport = hooks.viewport();
  switch (target.kind) {
    case 'outline': return hooks.outline(target.id);
    case 'member': return hooks.member(target.population, target.member);
    case 'population': return hooks.population ? hooks.population(target.id) : { why: 'not-found' };
    case 'control': return elementPlace(hooks.doc.querySelector(`[id="${attr(target.id)}"]`), viewport);
    case 'window': return elementPlace(hooks.doc.querySelector(`section.workspace-window[data-presentation-id="${attr(target.id)}"]`), viewport);
    case 'document': return elementPlace(hooks.doc.querySelector(`#study-artifacts > [data-placement-id="${attr(target.id)}"]`), viewport);
    case 'tab': return tabPlace(hooks.doc, target.key, viewport);
    case 'researcher': return researcherPlace(hooks.doc, target.key, hooks.researcherRids, viewport);
    case 'panel': return hooks.panel(target.name);
    case 'frame': {
      const normalised = frameShape(spec);
      if (!normalised) return { why: 'bad-target' };
      if (!anchor) return { why: 'out-of-view' };
      const points = hooks.frame(normalised.map(p => fromNormalised(p, anchor.crop)), anchor);
      if (!Array.isArray(points)) return points;
      const rect = boundsOf(points);
      const [w, h] = viewport, cx = rect[0] + rect[2] / 2, cy = rect[1] + rect[3] / 2;
      if (cx < 0 || cy < 0 || cx > w || cy > h) return { why: 'out-of-view' };
      return spec.polygon ? { rect, shape: points } : { rect };
    }
  }
}

// A model's coordinates a little outside the picture are its edge; far outside, a mistake.
const unit = (v: unknown) => typeof v === 'number' && Number.isFinite(v) && v >= -0.5 && v <= 1.5;
const edge = (v: number) => Math.min(1, Math.max(0, v));

/** A frame mark's corners (region) or points (polygon), normalised and kept inside the picture; null when malformed. */
export function frameShape(spec: MarkSpec): Point[] | null {
  if (Array.isArray(spec.polygon)) {
    const points = spec.polygon.filter(p => Array.isArray(p) && unit(p[0]) && unit(p[1])).map(p => [edge(p[0]), edge(p[1])] as Point);
    return points.length >= 3 && points.length === spec.polygon.length && points.length <= 200 ? points : null;
  }
  const r = spec.region;
  if (!Array.isArray(r) || r.length !== 4 || !r.every(unit)) return null;
  const x0 = edge(r[0]), y0 = edge(r[1]), x1 = edge(r[0] + r[2]), y1 = edge(r[1] + r[3]);
  if (x1 - x0 <= 0 || y1 - y0 <= 0) return null;
  return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
}

/** A mark drawn now: its target's place, with when it was first drawn and (while it fades out) when it was let go. */
export interface DrawnMark {
  key: string; setId: string; target: string; style: MarkStyle; label?: string;
  rect: Rect; shape?: Point[]; born: number; leaving: number | null;
}

interface Kept { key: string; setId: string; spec: MarkSpec; style: MarkStyle; anchor: FrameAnchor | null; born: number;
  leaving: number | null; place: Place | null }

export const SPEECH_TAIL_MS = 1500;
/** Marks the voice never spoke over go after this (a mark tool call whose answer was never said). */
export const UNSPOKEN_MS = 8000;
/** Nothing stays longer than this, whatever was asked. */
export const LONGEST_MS = 60000;
export const FADE_MS = 220;

export interface MarksOptions {
  hooks: MarkHooks;
  paint: (drawn: DrawnMark[], now: number) => void;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (timer: unknown) => void;
  frame?: (fn: () => void) => unknown;
}

const STYLES: MarkStyle[] = ['box', 'point', 'underline'];

/** The marks on screen now and when they go. */
export class Marks {
  private kept: Kept[] = [];
  private setId: string | null = null;
  private speaking = false;
  private heard = false; // the voice has spoken since this set came
  private untilSpeech = false; // this set goes with the speech (until "speech-end"), not after its own time
  private timers: unknown[] = [];
  private tailTimer: unknown = null;
  private looping = false;
  private readonly options: MarksOptions;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (timer: unknown) => void;

  constructor(options: MarksOptions) {
    this.options = options;
    this.now = options.now ?? (() => performance.now());
    this.setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
  }

  /** The id of the set on screen, or null. */
  get current(): string | null { return this.setId; }

  /** Draw a set (replacing the last) and say which of its marks were drawn. */
  show(message: MarksMessage): MarkResolution[] {
    this.letGo();
    const now = this.now();
    const marks = Array.isArray(message.marks) ? message.marks.slice(0, 12) : [];
    const needsAnchor = marks.some(m => m?.target === 'frame');
    const anchor = needsAnchor ? this.options.hooks.anchor(message.frame) : null;
    this.setId = String(message.id);
    this.heard = this.speaking;
    const resolved: MarkResolution[] = [];
    marks.forEach((spec, i) => {
      const target = typeof spec?.target === 'string' ? spec.target : String(spec?.target ?? '');
      const style: MarkStyle = STYLES.includes(spec?.style as MarkStyle) ? spec.style as MarkStyle : 'box';
      const place = spec && typeof spec === 'object' ? resolveTarget(spec, this.options.hooks, anchor) : { why: 'bad-target' as const };
      if ('rect' in place) {
        resolved.push({ target, ok: true, rect: place.rect.map(v => Math.round(v)) as Rect });
        this.kept.push({ key: `${this.setId}#${i}`, setId: this.setId!, spec: { ...spec, target }, style, anchor, born: now, leaving: null, place });
      } else resolved.push({ target, ok: false, why: place.why });
    });
    const until = message.until;
    const id = this.setId;
    this.untilSpeech = !(typeof until === 'number' && Number.isFinite(until) && until > 0);
    if (!this.untilSpeech) this.after(Math.min(until as number, LONGEST_MS), () => this.clear(id));
    else {
      this.after(UNSPOKEN_MS, () => { if (!this.heard) this.clear(id); });
      this.after(LONGEST_MS, () => this.clear(id));
    }
    this.paint();
    return resolved;
  }

  /** Clear a set (or every mark): they fade out. */
  clear(id?: string): void {
    if (id !== undefined && id !== this.setId) return;
    this.letGo();
  }

  /** The voice's phase (voice.ts): marks stay while it speaks and go a moment after it stops. */
  phase(phase: string): void {
    const speaking = phase === 'speaking';
    if (speaking === this.speaking) return;
    this.speaking = speaking;
    if (this.tailTimer !== null) { this.clearTimer(this.tailTimer); this.tailTimer = null; }
    if (speaking) { this.heard = true; return; }
    if (!this.setId || !this.heard || !this.untilSpeech) return;
    const id = this.setId;
    this.tailTimer = this.setTimer(() => { this.tailTimer = null; this.clear(id); }, SPEECH_TAIL_MS);
  }

  /** Find every target again and draw (each animation frame while there are marks). */
  paint(): void {
    const now = this.now();
    const drawn: DrawnMark[] = [];
    this.kept = this.kept.filter(k => k.leaving === null || now - k.leaving < FADE_MS);
    for (const k of this.kept) {
      if (k.leaving === null) k.place = resolveTarget(k.spec, this.options.hooks, k.anchor);
      if (!k.place || !('rect' in k.place)) continue; // gone for now (out of view, closed): drawn again when it is back
      drawn.push({ key: k.key, setId: k.setId, target: k.spec.target, style: k.style, label: typeof k.spec.label === 'string' ? k.spec.label.slice(0, 60) : undefined,
        rect: k.place.rect, shape: k.place.shape, born: k.born, leaving: k.leaving });
    }
    this.options.paint(drawn, now);
    if (this.kept.length) this.loop();
  }

  private loop(): void {
    if (this.looping || !this.options.frame) return;
    this.looping = true;
    this.options.frame(() => { this.looping = false; this.paint(); });
  }

  private after(ms: number, fn: () => void): void {
    this.timers.push(this.setTimer(fn, ms));
  }

  private letGo(): void {
    for (const timer of this.timers) this.clearTimer(timer);
    this.timers = [];
    if (this.tailTimer !== null) { this.clearTimer(this.tailTimer); this.tailTimer = null; }
    const now = this.now();
    for (const k of this.kept) if (k.leaving === null) k.leaving = now;
    this.setId = null;
    this.heard = false;
    if (this.kept.length) this.paint();
  }
}

// --- Drawing: a hand-drawn look, steady from frame to frame (its roughness is fixed per mark, never redrawn). ---

/** A small repeatable noise in [-1, 1] for mark `seed` at `t`. */
function wobble(seed: number, t: number): number {
  return Math.sin(t * 2.3 + seed * 1.7) * 0.6 + Math.sin(t * 5.1 + seed * 0.37) * 0.4;
}

function seedOf(key: string): number {
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) | 0;
  return (h % 1000) / 100;
}

const DRAW_IN_MS = 320;
const ease = (t: number) => 1 - Math.pow(1 - Math.min(1, Math.max(0, t)), 3);

/** Points around a rounded rectangle, padded, with a slight hand wobble, going on a little past the start. */
export function roughBox(rect: Rect, seed: number, pad = 6): Point[] {
  const x = rect[0] - pad, y = rect[1] - pad, w = rect[2] + pad * 2, h = rect[3] + pad * 2;
  const r = Math.max(4, Math.min(12, w / 4, h / 4));
  const path: Point[] = [];
  const corner = (cx: number, cy: number, from: number) => {
    for (let k = 0; k <= 4; k++) { const a = from + (k / 4) * (Math.PI / 2); path.push([cx + Math.cos(a) * r, cy + Math.sin(a) * r]); }
  };
  const edge = (x0: number, y0: number, x1: number, y1: number) => {
    const n = Math.max(1, Math.round(Math.hypot(x1 - x0, y1 - y0) / 14));
    for (let k = 1; k < n; k++) path.push([x0 + ((x1 - x0) * k) / n, y0 + ((y1 - y0) * k) / n]);
  };
  corner(x + r, y + r, Math.PI); edge(x + r, y, x + w - r, y);
  corner(x + w - r, y + r, -Math.PI / 2); edge(x + w, y + r, x + w, y + h - r);
  corner(x + w - r, y + h - r, 0); edge(x + w - r, y + h, x + r, y + h);
  corner(x + r, y + h - r, Math.PI / 2); edge(x, y + h - r, x, y + r);
  return roughen(path, seed, true);
}

/** Wobble each point a little across the line (at most 1.6 px), and go on past the start, as a pen does. */
function roughen(points: Point[], seed: number, closed: boolean): Point[] {
  const out = points.map(([px, py], i) => {
    const t = i / Math.max(1, points.length);
    return [px + wobble(seed, t * 9) * 1.6, py + wobble(seed + 3, t * 9) * 1.6] as Point;
  });
  if (closed && out.length > 3) {
    const over = Math.max(2, Math.round(out.length * 0.08));
    for (let i = 0; i < over; i++) out.push([out[i][0] + 2.5, out[i][1] + 1.5]);
  }
  return out;
}

/** An arrow coming in from the side with room (towards the window's middle), its tip just off the target. */
export function arrow(rect: Rect, viewport: [number, number], seed: number): { shaft: Point[]; head: Point[] } {
  const cx = rect[0] + rect[2] / 2, cy = rect[1] + rect[3] / 2;
  let dx = viewport[0] / 2 - cx, dy = viewport[1] / 2 - cy;
  const len = Math.hypot(dx, dy) || 1;
  dx /= len; dy /= len;
  if (Math.abs(dx) < 0.2 && Math.abs(dy) < 0.2) { dx = -0.7; dy = -0.7; }
  // The tip: where the line from the middle meets the target's padded edge.
  const hx = rect[2] / 2 + 8, hy = rect[3] / 2 + 8;
  const k = Math.min(Math.abs(dx) > 1e-6 ? hx / Math.abs(dx) : Infinity, Math.abs(dy) > 1e-6 ? hy / Math.abs(dy) : Infinity);
  const tip: Point = [cx + dx * k, cy + dy * k];
  const tail: Point = [tip[0] + dx * 64, tip[1] + dy * 64];
  const bend = 10 + wobble(seed, 1) * 4;
  const ctrl: Point = [(tip[0] + tail[0]) / 2 - dy * bend, (tip[1] + tail[1]) / 2 + dx * bend];
  const shaft: Point[] = [];
  for (let i = 0; i <= 12; i++) {
    const t = i / 12, a = (1 - t) * (1 - t), b = 2 * (1 - t) * t, c = t * t;
    shaft.push([a * tail[0] + b * ctrl[0] + c * tip[0], a * tail[1] + b * ctrl[1] + c * tip[1]]);
  }
  const ux = tip[0] - shaft[10][0], uy = tip[1] - shaft[10][1], ul = Math.hypot(ux, uy) || 1;
  const bx = ux / ul, by = uy / ul;
  const head: Point[] = [[tip[0] - bx * 11 - by * 6, tip[1] - by * 11 + bx * 6], tip, [tip[0] - bx * 11 + by * 6, tip[1] - by * 11 - bx * 6]];
  return { shaft, head };
}

/** A slightly wavy line under the target, a little wider than it. */
export function underline(rect: Rect, seed: number): Point[] {
  const y = rect[1] + rect[3] + 5, x0 = rect[0] - 4, x1 = rect[0] + rect[2] + 4;
  const n = Math.max(4, Math.round((x1 - x0) / 12));
  return Array.from({ length: n + 1 }, (_, i) => [x0 + ((x1 - x0) * i) / n, y + wobble(seed, i / n * 6) * 1.4] as Point);
}

/** The first `fraction` of a polyline, by length (the draw-in). */
export function partOf(points: Point[], fraction: number): Point[] {
  if (fraction >= 1 || points.length < 2) return points;
  const lengths = points.slice(1).map((p, i) => Math.hypot(p[0] - points[i][0], p[1] - points[i][1]));
  let left = lengths.reduce((a, b) => a + b, 0) * Math.max(0, fraction);
  const out: Point[] = [points[0]];
  for (let i = 0; i < lengths.length; i++) {
    if (left >= lengths[i]) { out.push(points[i + 1]); left -= lengths[i]; continue; }
    const t = lengths[i] ? left / lengths[i] : 0;
    out.push([points[i][0] + (points[i + 1][0] - points[i][0]) * t, points[i][1] + (points[i + 1][1] - points[i][1]) * t]);
    break;
  }
  return out;
}

const d = (points: Point[]) => points.length ? `M${points.map(([x, y]) => `${x.toFixed(1)} ${y.toFixed(1)}`).join('L')}` : '';

/** The shapes of one mark at time `now`: its strokes (draw-in applied) and its opacity. */
export function markShapes(mark: DrawnMark, now: number, viewport: [number, number], still = false): { paths: string[]; opacity: number; labelAt: Point } {
  const seed = seedOf(mark.key);
  const grow = still ? 1 : ease((now - mark.born) / DRAW_IN_MS);
  const fadeIn = still ? 1 : Math.min(1, (now - mark.born) / 120);
  const fadeOut = mark.leaving === null ? 1 : still ? 0 : Math.max(0, 1 - (now - mark.leaving) / FADE_MS);
  const opacity = Math.min(fadeIn, fadeOut);
  if (mark.style === 'point') {
    const { shaft, head } = arrow(mark.rect, viewport, seed);
    return { paths: [d(partOf(shaft, grow)), grow >= 1 ? d(head) : ''].filter(Boolean), opacity, labelAt: shaft[0] };
  }
  if (mark.style === 'underline') {
    const line = underline(mark.rect, seed);
    return { paths: [d(partOf(line, grow))], opacity, labelAt: [mark.rect[0], mark.rect[1] + mark.rect[3] + 22] };
  }
  const outline = mark.shape ? roughen([...mark.shape, mark.shape[0]], seed, true) : roughBox(mark.rect, seed);
  return { paths: [d(partOf(outline, grow))], opacity, labelAt: [mark.rect[0] - 6, mark.rect[1] - 12] };
}

const SVG = 'http://www.w3.org/2000/svg';

/** The overlay: one SVG over the whole window, a <g class="voice-mark"> per mark (kept from frame to frame), each
 * with its target and its rect now as data attributes (the bench compares them with the target's own place). */
export class SvgMarksPainter {
  readonly root: SVGSVGElement;
  private readonly groups = new Map<string, SVGGElement>();
  private readonly still: boolean;

  constructor(doc: Document = document) {
    this.root = doc.createElementNS(SVG, 'svg');
    this.root.id = 'voice-marks';
    this.root.classList.add('voice-marks');
    this.root.setAttribute('aria-hidden', 'true');
    doc.body.append(this.root);
    this.still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
  }

  paint(drawn: DrawnMark[], now: number): void {
    const viewport: [number, number] = [innerWidth, innerHeight];
    this.root.setAttribute('viewBox', `0 0 ${viewport[0]} ${viewport[1]}`);
    const live = new Set(drawn.map(m => m.key));
    for (const [key, group] of this.groups) if (!live.has(key)) { group.remove(); this.groups.delete(key); }
    for (const mark of drawn) {
      let group = this.groups.get(mark.key);
      if (!group) {
        group = this.root.ownerDocument.createElementNS(SVG, 'g');
        group.classList.add('voice-mark');
        group.dataset.markId = mark.setId;
        group.dataset.target = mark.target;
        group.dataset.style = mark.style;
        this.groups.set(mark.key, group);
        this.root.append(group);
      }
      group.dataset.rect = mark.rect.map(v => Math.round(v)).join(',');
      const { paths, opacity, labelAt } = markShapes(mark, now, viewport, this.still);
      group.setAttribute('opacity', opacity.toFixed(3));
      const parts: SVGElement[] = [];
      for (const path of paths) {
        for (const cls of ['voice-mark-casing', 'voice-mark-stroke']) {
          const p = this.root.ownerDocument.createElementNS(SVG, 'path');
          p.setAttribute('class', cls); p.setAttribute('d', path);
          parts.push(p);
        }
      }
      if (mark.label) {
        const text = this.root.ownerDocument.createElementNS(SVG, 'text');
        text.setAttribute('class', 'voice-mark-label');
        text.setAttribute('x', String(Math.max(4, Math.min(viewport[0] - 120, labelAt[0]))));
        text.setAttribute('y', String(Math.max(14, Math.min(viewport[1] - 4, labelAt[1]))));
        text.textContent = mark.label;
        parts.push(text);
      }
      group.replaceChildren(...parts);
    }
  }
}
