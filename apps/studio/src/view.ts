import { CLICK_RADIUS, fitWindow, hitObject, isFresh, isVisible, overlayOpacity, TINY_PX, scaleBar, sizeWords, SPEED_WINDOW, statusWords, taughtWords, fitWords } from './overlay';
import { FrameTiming } from './frametiming';
import { categoryColour, categoryWords, drawable, heat, keepPopulation, memberAt, memberLook, type LayerWire, type LegendCategory, type PopulationMember, type PopulationWire } from './population';
import { notesBeside, type Reserved } from './panels';
import type { Candidate, CoachState, Frame, FrameHeader, Point, ScaleState, SceneObject, ServerMessage } from './protocol';
import { labelled, trailDrawing, type TrailsMessage, type TrailWire } from './trail';
import { keepTracks, tracksDrawing, tracksLabel, type MotionTracksWire, type TracksLayerWire } from './motiontracks';
/** How long a new result's outlines take to ramp in (once, the first time they are drawn). */
const RAMP_MS = 320;

const EOSIN = '#ff4fa3';
// The colours the user can ask for (set_style; options.OUTLINE_COLORS). Pink is eosin, the default.
export const OUTLINE_COLORS: Record<string, string> = {
  pink: EOSIN,
  yellow: '#fff04a',
  cyan: '#3fe3ff',
  green: '#59ff8f',
  white: '#ffffff',
};
const IODINE = '#ffb547';
// Under every outline a dark casing two px wider than its line, so a line of the stain's own colour still shows by
// its edges (live 2026-10-11: pink outlines all but vanished on a magenta-stained section). The server draws its
// pictures the same way (services/orchestrator/drawing.py).
export const CASING = 'rgba(9, 9, 10, 0.78)';
const PAPER = '#e7ecf7';
// The focus meter's words for each cue (orchestrator/coach.py). 'phone': the eyepiece disc changed
// size, which the knob never does, so the phone's own lens is refocusing.
const FOCUS_CUES: Record<string, string> = {
  sharper: 'Sharper',
  passed: 'Past the sharpest: turn back a little',
  phone: 'The phone is refocusing: leave the knob',
};
const MUTED = '#8d98b8';
const INK = '#09090a';
/** The Stage's own black (the eyepiece's surround, the frame's edge feather): true black, as the frame's own black and
 * #stage's --mm-well are, so no seam shows between them. INK stays for text shadows and the magnifier's panel. */
const WELL = '#000';

const DRAW_IN_MS = 650;
// How outlines fade while the slide moves and where they are drawn: overlay.ts (the contract
// the server and services/sim/harness.py read too).
const LABEL_DELAY_MS = 260;
const LABEL_SLIDE_MS = 380;
const NOTE_ROOM = 210; // CSS px kept beside the disc for margin notes when the side column is open
const DETAIL_RESERVE = 300; // the widest a label's second line gets (longer is cut with an ellipsis)
// Blind A/B/C arms share the eosin colour and differ only by line pattern, so no option looks
// more "official" than another. The vote buttons show the same patterns.
export const ARM_DASH: Record<string, number[]> = { A: [], B: [10, 5], C: [1.5, 5], D: [12, 4, 2, 4] };

export interface Placement {
  scale: number;
  ox: number;
  oy: number;
}

/** Where the live frame sits on the canvas (CSS px: canvas px = ox + p * scale): the eyepiece disc as large as
 * the stage allows, or the whole frame. `open` is how far the side column has drawn the disc over beside it
 * (0-1; only on a wide stage). `left`: the room the open sessions rail takes on the left (panels.Reserved): the
 * frame is placed in what is right of it. Pure, so spaces.test.ts can map frame px to the workspace through it. */
export function placeFrame(h: FrameHeader, width: number, fullHeight: number, reserve: number, wholeFrame: boolean, sideOpen: number, left = 0): Placement {
  if (left > 0) {
    const inner = placeFrame(h, Math.max(120, width - left), fullHeight, reserve, wholeFrame, sideOpen);
    return { ...inner, ox: inner.ox + left };
  }
  // The eyepiece disc (not the whole camera frame) as large as the stage allows: nearly the
  // full height, keeping room at the sides for margin notes and clear of the shelf below.
  const height = Math.max(120, fullHeight - reserve);
  if (h.fov && !wholeFrame) {
    const [cx, cy, r] = h.fov;
    const notes = width >= 900 ? 190 : width > 520 ? 120 : 0; // per side
    const centred = Math.min(height * (width > 520 ? 0.94 : 0.96), Math.max(width - 2 * notes, width * 0.6)) / (2 * r);
    // With the side column open the disc moves over beside it and the notes all go to the
    // left, so they keep their room instead of stacking over the specimen.
    const open = width > 520 ? sideOpen : 0;
    const beside = Math.min(height * 0.94, Math.max(width - NOTE_ROOM - 16, width * 0.5)) / (2 * r);
    const scale = centred + (beside - centred) * open;
    const centreX = width / 2 + (width - 16 - r * scale - width / 2) * open;
    return { scale, ox: centreX - cx * scale, oy: height / 2 - cy * scale };
  }
  const scale = Math.min(width / h.w, height / h.h);
  return { scale, ox: (width - h.w * scale) / 2, oy: (height - h.h * scale) / 2 };
}

/** The window of the frame the magnifier shows (frame px at the frame now: x0, y0, width, height) and how many
 * panel CSS px a frame px is (k). `box` is the object's box in frame px now, `centre` the point to centre on
 * (frame px now) or null for the box's centre. */
export function magnifierWindow(box: { w: number; h: number; cx: number; cy: number }, level: number,
  centre: Point | null, cssW: number, cssH: number): { x0: number; y0: number; sw: number; sh: number; k: number } {
  let [sw, sh] = fitWindow(box.w, box.h, cssW / cssH);
  const lv = Math.max(1, level);
  sw /= lv;
  sh /= lv;
  const cx = centre ? centre[0] : box.cx;
  const cy = centre ? centre[1] : box.cy;
  return { x0: cx - sw / 2, y0: cy - sh / 2, sw, sh, k: cssW / sw };
}

/** Where a label was drawn this frame (CSS px), for clicks and hovers on labels. */
export interface LabelHit {
  id: string;
  rect: [number, number, number, number];
  close: [number, number, number, number] | null; // the × on the selected label
}

const TITLE_FONT = (size: number) => `italic 500 ${size}px Newsreader, Georgia, serif`;
const DETAIL_FONT = '400 12.5px "Instrument Sans", system-ui, sans-serif';

interface Callout {
  obj: SceneObject;
  anchor: Point; // canvas px, on the object
  angle: number;
  title: string;
  detail: string;
  tone: string;
  bornAt: number;
}

export class View {
  readonly canvas: HTMLCanvasElement;
  private ctx: CanvasRenderingContext2D;
  private frame: Frame | null = null;
  private frameAt = 0;
  readonly timing = new FrameTiming(); // camera-to-screen time per stage (frametiming.ts)
  scale: ScaleState | null = null; // how big things are (orchestrator/scale.py)
  coach: CoachState | null = null; // the focus meter while the knob turns (orchestrator/coach.py)
  // How outlines are drawn (option STYLE): "classic" (a light fill, the stroke on the edge) or
  // "edge" (no fill, the stroke just outside, so the edge itself stays visible; LYNA, 2018).
  outlineStyle = 'classic';
  outlineColor = EOSIN; // the colour of outlines (set_style, setting outlineColor)
  hideOverlays = false; // held "h": the bare specimen, nothing drawn over it
  liveOpacity = 1; // the live view dimmed (the present verb "opacity"; never hidden)
  outlinesVisible = true; // the app's outlines shown or hidden (present)
  outlinesOpacity = 1;
  private objects: SceneObject[] = [];
  private firstSeen = new Map<string, number>();
  private offsets = new Map<number, [number, number, number]>(); // frame id -> offset and motion epoch, for mapping candidates and marks
  private candidates: { frameId: number; items: Candidate[] } | null = null;
  cornerNotes: { x: number; y: number; w: number; h: number }[] = [];
  private placement: Placement = { scale: 1, ox: 0, oy: 0 };
  private dpr = 1;
  private reducedMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;
  showCandidates = false;
  live = false;
  solo: string | null = null; // show only this arm of a blind trial
  selected: string | null = null; // the selected object: bolder outline, × on its label
  hovered: string | null = null; // the object under the pointer
  labels: LabelHit[] = []; // labels drawn on the last frame
  private layouts = new Map<string, boolean>(); // label id:side -> stacked, decided once
  private details = new Map<string, { text: string; at: number }>(); // second lines, for fading in
  // The magnifier's live source: drawn here from the same full-resolution frame, so it
  // follows the specimen exactly like the outlines do.
  magnifier: { canvas: HTMLCanvasElement; objectId: string } | null = null;
  /** How much closer than fitting the object (1 = fit), and an optional centre (frame px in
   * the object's anchor frame). Set from the server's magnifier state (orchestrator/magnifier.py). */
  magnifierLevel = 1;
  magnifierCenter: Point | null = null;
  magnifierZoom = 0; // how much larger than on the stage, for the "digital zoom" caption
  magnifierPixelated = false; // past the camera's detail (the server says): its pixels are drawn as blocks
  magnifierLostSince = 0; // when the magnified object left the view (0 while it is in view)
  magnifierLostWhy: 'refinding' | 'out' = 'refinding'; // its place lost, or out of the eyepiece
  /** Where the floating magnifier is (canvas CSS px): margin notes on its side keep clear of it. */
  magnifierBox: [number, number, number, number] | null = null;
  paused = false; // the Research tab is showing: nothing to draw
  /** The box being dragged on the view (canvas CSS px), drawn dashed; null when none (boxdrag.ts). */
  dragBox: [number, number, number, number] | null = null;
  /** Room taken by the side column (right), the bottom sheet on phones, the shelf and the sessions rail (left), in
   * CSS px; written only by the panel stack (panels.ts). The eyepiece glides into the remaining area when it
   * changes; the rail's closed strip (`edge`) only keeps the margin notes clear of it. */
  reserved: Reserved = { right: 0, bottom: 0, shelf: 0, left: 0, edge: 0 };
  private inset = { left: 0, right: 0, bottom: 0, reserve: 0, at: 0 };
  /** Show the whole camera picture (no zoom to the eyepiece): for lining the phone up. */
  wholeFrame = false;
  private shown: Placement | null = null;
  private shownAt = 0;
  /** Draws left that place the picture at once: after the stage was hidden (a page such as Measurements open, the
   * canvas 0×0), where it was then means nothing, so coming back it is put where it belongs, never glided there. */
  private settle = 0;
  private snapping = false;

  /** Glide toward where the picture should be (about 250 ms). */
  private easePlacement(target: Placement): Placement {
    const now = performance.now();
    const dt = Math.min(100, now - (this.shownAt || now));
    this.shownAt = now;
    const s = this.shown;
    if (!s || this.reducedMotion || this.snapping) return (this.shown = target);
    const k = 1 - Math.exp(-dt / 80);
    const next = { scale: s.scale + (target.scale - s.scale) * k, ox: s.ox + (target.ox - s.ox) * k, oy: s.oy + (target.oy - s.oy) * k };
    const close = Math.abs(next.scale - target.scale) < 1e-4 && Math.abs(next.ox - target.ox) < 0.5 && Math.abs(next.oy - target.oy) < 0.5;
    return (this.shown = close ? target : next);
  }

  /** Where the magnifier's edge is (canvas CSS px), for the connector from its object. */
  magnifierAnchor: Point | null = null;
  /** Current verified native close-ups, in client coordinates. No line for captured/reference media. */
  windowLeaders: () => { point: Point; rect: number[] }[] = () => [];
  /** A short researched description to show under an object's name, when it has one. */
  describe: ((obj: SceneObject) => string | null) | null = null;
  private recentSpeeds: number[] = []; // slide speed of the last SPEED_WINDOW frames

  constructor(canvas: HTMLCanvasElement) {
    this.canvas = canvas;
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Canvas 2D is not available in this browser.');
    this.ctx = ctx;
    new ResizeObserver(() => this.resize()).observe(canvas);
    this.resize(false);
    const loop = () => {
      if (!this.paused) this.draw();
      requestAnimationFrame(loop);
    };
    requestAnimationFrame(loop);
  }

  /** Whether the outline `id` is drawn on the live view now (tracking, this epoch, inside the eyepiece). */
  objectInView(id: string | null): boolean {
    const h = this.header;
    const obj = id ? this.objects.find((o) => o.id === id) : undefined;
    return !!(h && obj && isVisible(obj, h));
  }

  get header(): FrameHeader | null {
    return this.frame?.header ?? null;
  }

  isFresh(now = performance.now()): boolean {
    return this.live && this.frame !== null && isFresh(now - this.frameAt);
  }

  /** No picture: another session is opening (session-view.ts StageGate), so nothing of the last one stays on the stage.
   * The view draws its empty eyepiece until the next frame comes. */
  clearFrame(): void {
    this.frame?.bitmap.close();
    this.frame = null;
    this.recentSpeeds = [];
  }

  setFrame(frame: Frame): void {
    this.frame?.bitmap.close();
    this.frame = frame;
    if (frame.header.motionTracks) this.setMotionTracks(frame.header.motionTracks);
    // Fresh means a frame arrived recently. A still picture is the normal state of a microscope
    // (a slide at rest), so how long it has not changed (`still`) is logged, never dimmed.
    this.frameAt = performance.now();
    this.offsets.set(frame.header.id, [frame.header.dx, frame.header.dy, frame.header.epoch]);
    this.recentSpeeds.push(frame.header.speed ?? 0);
    if (this.recentSpeeds.length > SPEED_WINDOW) this.recentSpeeds.shift();
    if (this.offsets.size > 300) {
      const oldest = this.offsets.keys().next().value;
      if (oldest !== undefined) this.offsets.delete(oldest);
    }
  }

  /** The population shown over the live view, and which of its members are verified in place now. */
  private populations = new Map<string, PopulationWire>();
  // One structural identity per existing overlay producer, never masks, trails or
  // media authority. Hide keeps it inactive for its original causal receipt;
  // another shown item replaces it, and connection retirement drops it.
  private overlayPresentations: Record<'population' | 'tracks', { id: string; title: string } | null> = { population: null, tracks: null };
  private overlayDay: string | null = null;
  layer: LayerWire | null = null;
  selectedMember: string | null = null;
  /** The drawn member under the pointer (main.ts's hover), drawn a little firmer. */
  hoveredMember: string | null = null;

  setPopulation(pop: PopulationWire): void {
    // Kept by id: a newly shown population's wire may come before the layer that shows it.
    this.populations = keepPopulation(this.populations, pop);
    if (this.layer?.population === pop.id) this.overlayPresentations.population = { id: pop.id, title: pop.title ?? pop.id };
  }

  /** The population the layer shows, if its wire has come. */
  get population(): PopulationWire | null {
    return this.layer?.population ? this.populations.get(this.layer.population) ?? null : null;
  }

  /** A new result's outlines ramp in once (workspace-feel plan, "A run finishing"): a population made after this page
   * loaded (its `at`, the wall time of the picture it was made on) rises over RAMP_MS the first time it is drawn, inside
   * the existing draw (one multiplication a frame while it ramps, nothing after). One made before (a reload, an older
   * result opened from Data) or drawn before (a Tab switch) appears at once; reduced motion skips it. */
  private readonly loadedAt = Date.now();
  private rampFrom = 0;
  private rampId: string | null = null;
  private readonly drawnPopulations = new Set<string>();

  /** This frame's alpha for a population's outlines: below 1 only while a new result ramps in. */
  private rampOf(pop: PopulationWire): number {
    if (!this.drawnPopulations.has(pop.id)) {
      this.drawnPopulations.add(pop.id);
      if (this.drawnPopulations.size > 64) this.drawnPopulations.delete(this.drawnPopulations.values().next().value as string);
      const still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (!still && pop.at * 1000 >= this.loadedAt) { this.rampId = pop.id; this.rampFrom = performance.now(); }
    }
    if (this.rampId !== pop.id) return 1;
    const ramp = Math.min(1, (performance.now() - this.rampFrom) / RAMP_MS);
    if (ramp >= 1) this.rampId = null;
    return ramp;
  }

  setLayer(layer: LayerWire): void {
    this.layer = layer;
    if (layer.population !== null) {
      const pop = this.population;
      this.overlayPresentations.population = pop ? { id: pop.id, title: pop.title ?? pop.id } : null;
    }
    if (layer.population === null) { this.selectedMember = null; this.hoveredMember = null; }
  }

  memberAt(point: Point): PopulationMember | null {
    return this.header ? memberAt(this.population, this.layer, this.header, point) : null;
  }

  /** Whether this population, found on the Stage's still, is drawn on the specimen now: the layer shows it by the still's
   * fit, and some of its members are drawn on the frame on the Stage (overlays shown, the picture fresh). The run's
   * footer says so only then (S5a). */
  stillDrawn(population: string): boolean {
    const h = this.header;
    return !!h && !this.hideOverlays && this.isFresh() && this.layer?.population === population && !!this.layer.fit
      && drawable(this.population, this.layer, h).members.length > 0;
  }

  /** Motion tracks kept as evidence (motiontracks.ts), and the layer that shows one live where verified. */
  private motionTracks = new Map<string, MotionTracksWire>();
  tracksLayer: TracksLayerWire | null = null;

  setMotionTracks(item: MotionTracksWire): void {
    this.motionTracks = keepTracks(this.motionTracks, item);
    if (this.tracksLayer?.tracks === item.id) this.overlayPresentations.tracks = { id: item.id, title: item.title };
  }

  setTracksLayer(layer: TracksLayerWire): void {
    this.tracksLayer = layer;
    if (layer.tracks !== null) {
      const item = this.shownTracks;
      this.overlayPresentations.tracks = item ? { id: item.id, title: item.title } : null;
    }
  }

  /** Current structural openness; inactive identity does not make old pixels drawable. */
  overlayPresentation(kind: 'population' | 'tracks'): { id: string; title: string; open: boolean } | null {
    const identity = this.overlayPresentations[kind];
    return identity ? { ...identity, open: kind === 'population' ? this.layer?.population === identity.id : this.tracksLayer?.tracks === identity.id } : null;
  }

  /** Old connection content cannot become a new accepted session's overlay identity. */
  retireOverlayPresentations(): void {
    this.overlayPresentations = { population: null, tracks: null };
    this.overlayDay = null;
    this.populations.clear(); this.motionTracks.clear(); this.layer = null; this.tracksLayer = null; this.selectedMember = null;
  }

  /** Installed producer publications; UI callbacks remain owned by main. */
  observeOverlayMessage(message: ServerMessage): void {
    switch (message.type) {
      case 'day':
        if (this.overlayDay !== null && this.overlayDay !== message.id) this.retireOverlayPresentations();
        this.overlayDay = message.id;
        break;
      case 'session-cleared': this.retireOverlayPresentations(); break;
      case 'population': this.setPopulation(message); break;
      case 'layer': this.setLayer(message); break;
      case 'motion-tracks': this.setMotionTracks(message); break;
      case 'tracks-layer': this.setTracksLayer(message); break;
    }
  }

  /** The tracks item the layer shows, if its wire has come. */
  get shownTracks(): MotionTracksWire | null {
    return this.tracksLayer?.tracks ? this.motionTracks.get(this.tracksLayer.tracks) ?? null : null;
  }

  /** Whether the shown tracks are drawn on the live view now (verified, in this frame's epoch, overlays on): what
   * the screen report says, so "drawn" is confirmed by the drawing, not by the layer being on (M3.2 review S1). */
  tracksDrawnNow(): boolean {
    const h = this.header;
    return !!h && !this.hideOverlays && tracksDrawing(this.shownTracks, this.tracksLayer, h, (pt) => pt) !== null;
  }

  /** Followed outlines' trails (trail.ts), drawn under the outlines on the live view. */
  private trails: TrailWire[] = [];
  private trailWindow = 60;

  setTrails(message: TrailsMessage): void {
    this.trails = message.trails;
    this.trailWindow = message.windowS;
  }

  setScene(objects: SceneObject[]): void {
    const now = performance.now();
    for (const obj of objects) {
      if (!this.firstSeen.has(obj.id)) this.firstSeen.set(obj.id, now);
    }
    const ids = new Set(objects.map((o) => o.id));
    for (const id of this.firstSeen.keys()) if (!ids.has(id)) this.firstSeen.delete(id);
    for (const key of this.layouts.keys()) if (!ids.has(key.split(':')[0])) this.layouts.delete(key);
    for (const id of this.details.keys()) if (!ids.has(id)) this.details.delete(id);
    this.objects = objects;
  }

  /** Take an object off the screen at once (the server confirms with the next scene). */
  /** An outline's name on the view now ("" when it has none or is gone). */
  labelOf(id: string): string {
    return this.objects.find((o) => o.id === id)?.label ?? '';
  }

  dropLocally(id: string): void {
    this.objects = this.objects.filter((o) => o.id !== id && o.parent !== id);
    if (this.selected === id) this.selected = null;
  }

  setCandidates(frameId: number, items: Candidate[]): void {
    this.candidates = { frameId, items };
  }

  /** Canvas CSS pixel -> frame pixel, or null when outside the eyepiece. */
  toFrame(clientX: number, clientY: number): Point | null {
    const point = this.toFrameAnywhere(clientX, clientY);
    const h = this.header;
    if (!point || !h) return null;
    const [x, y] = point;
    if (h.fov) {
      const [cx, cy, r] = h.fov;
      if (Math.hypot(x - cx, y - cy) > r * CLICK_RADIUS) return null;
    } else if (x < 0 || y < 0 || x > h.w || y > h.h) {
      return null;
    }
    return [x, y];
  }

  /** Canvas CSS pixel -> frame pixel wherever it is (the far corner of a dragged box may lie
   * outside the eyepiece); null only before the first frame. */
  toFrameAnywhere(clientX: number, clientY: number): Point | null {
    if (!this.header) return null;
    const rect = this.canvas.getBoundingClientRect();
    const { scale, ox, oy } = this.placement;
    return [(clientX - rect.left - ox) / scale, (clientY - rect.top - oy) / scale];
  }

  /** The highlight under a frame-pixel point on the current frame, if any. */
  hitTest(point: Point): SceneObject | null {
    return this.header ? hitObject(this.objects, this.header, point, this.placement.scale) : null;
  }

  private resize(redraw = true): void {
    this.dpr = window.devicePixelRatio || 1;
    const { width, height } = this.canvas.getBoundingClientRect();
    const w = Math.round(width * this.dpr), h = Math.round(height * this.dpr);
    if (w === this.canvas.width && h === this.canvas.height) return;
    this.canvas.width = w;
    this.canvas.height = h;
    // A new size (a side panel opening or closing, the window dragged) places the picture at once, insets too: it moves
    // with its box, frame by frame while the box moves, never easing after it from where the old size put it (that was
    // a 325 px jump, cut off by the Stage's edge for half a second). The glide stays for the whole frame and eyepiece.
    this.settle = Math.max(this.settle, 1);
    // Setting a canvas's size clears it, and the browser calls this after the frame's draw and
    // before it paints: drawn again now, so a view being resized (the chat folding, a window
    // dragged) is never shown black.
    if (redraw && !this.paused) this.draw();
  }

  /** How far the side column is open (0 to 1, eased), and whether notes go left only. */
  private sideOpen(): number {
    return this.inset.bottom > 0 ? 0 : clamp01(this.inset.right / 220);
  }

  private place(h: FrameHeader, width: number, fullHeight: number): Placement {
    const inner = width - this.inset.left;
    return placeFrame(h, width, fullHeight, this.inset.reserve, this.wholeFrame, inner > 520 ? this.sideOpen() : 0, this.inset.left);
  }

  /** Where the left of the view's area is (CSS px): right of the sessions rail, open or closed. */
  private leftEdge(): number {
    return Math.max(this.inset.left, this.reserved.edge);
  }

  /** Where the eyepiece reaches past the camera frame (the phone sits off-centre or the
   * circle is taller than the frame), fade the picture into the ink instead of a hard edge. */
  private featherFrameEdges(h: FrameHeader, p: Placement): void {
    const [cx, cy, r] = h.fov!;
    const ctx = this.ctx;
    const x0 = p.ox, y0 = p.oy, x1 = p.ox + h.w * p.scale, y1 = p.oy + h.h * p.scale;
    const f = Math.min(36, r * p.scale * 0.12);
    const fade = (gx0: number, gy0: number, gx1: number, gy1: number, rx: number, ry: number, rw: number, rh: number) => {
      const g = ctx.createLinearGradient(gx0, gy0, gx1, gy1);
      g.addColorStop(0, 'rgba(0, 0, 0, 0)');
      g.addColorStop(1, WELL);
      ctx.fillStyle = g;
      ctx.fillRect(rx, ry, rw, rh);
    };
    if (cy + r > h.h) fade(0, y1 - f, 0, y1, x0, y1 - f, x1 - x0, f);
    if (cy - r < 0) fade(0, y0 + f, 0, y0, x0, y0, x1 - x0, f);
    if (cx + r > h.w) fade(x1 - f, 0, x1, 0, x1 - f, y0, f, y1 - y0);
    if (cx - r < 0) fade(x0 + f, 0, x0, 0, x0, y0, f, y1 - y0);
  }

  /** Ease the insets toward their targets (about 250 ms), so the view glides aside. */
  private easeInsets(): { left: number; right: number; bottom: number } {
    const now = performance.now();
    const dt = Math.min(100, now - (this.inset.at || now));
    this.inset.at = now;
    const k = this.reducedMotion || this.snapping ? 1 : 1 - Math.exp(-dt / 70);
    const { right, bottom, shelf, left } = this.reserved;
    this.inset.left += (left - this.inset.left) * k;
    if (Math.abs(left - this.inset.left) < 0.5) this.inset.left = left;
    this.inset.right += (right - this.inset.right) * k;
    this.inset.bottom += (bottom - this.inset.bottom) * k;
    this.inset.reserve += (shelf - this.inset.reserve) * k;
    if (Math.abs(shelf - this.inset.reserve) < 0.5) this.inset.reserve = shelf;
    if (Math.abs(right - this.inset.right) < 0.5) this.inset.right = right;
    if (Math.abs(bottom - this.inset.bottom) < 0.5) this.inset.bottom = bottom;
    return this.inset;
  }

  /** The eyepiece disc on the canvas now (CSS px: centre and radius), or null without one. */
  discOnCanvas(): [number, number, number] | null {
    const h = this.header;
    if (!h) return null;
    const { scale, ox, oy } = this.placement;
    if (h.fov && !this.wholeFrame) return [ox + h.fov[0] * scale, oy + h.fov[1] * scale, h.fov[2] * scale];
    return [ox + (h.w * scale) / 2, oy + (h.h * scale) / 2, (Math.max(h.w, h.h) * scale) / 2];
  }

  /** The part of the canvas the live view keeps (CSS px): clear of the side column, the
   * bottom sheet and the shelf; x from `left` (right of the sessions rail) to `width`. */
  viewArea(): { width: number; height: number; left: number } {
    const width = this.canvas.width / this.dpr - this.inset.right;
    const height = this.canvas.height / this.dpr - this.inset.bottom - this.inset.reserve;
    return { width: Math.max(120, width), height: Math.max(120, height), left: Math.max(0, Math.min(this.leftEdge(), width - 120)) };
  }

  /** An object's bounding box on the canvas now (CSS px), or null when it is not shown. */
  objectCanvasBox(id: string): [number, number, number, number] | null {
    const h = this.header;
    const obj = this.objects.find((o) => o.id === id);
    if (!h || !obj || !isVisible(obj, h)) return null;
    const pts = obj.polygons.flat();
    if (!pts.length) return null;
    const sx = h.dx - obj.anchorOffset[0];
    const sy = h.dy - obj.anchorOffset[1];
    const { scale, ox, oy } = this.placement;
    const xs = pts.map((q) => ox + (q[0] + sx) * scale);
    const ys = pts.map((q) => oy + (q[1] + sy) * scale);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  /** Whether the scene has the outline `id` at all (drawn or not). */
  hasObject(id: string): boolean {
    return this.objects.some((o) => o.id === id);
  }

  /** A shown population member's bounding box on the canvas now (CSS px), or why it is not drawn: its population is
   * not the one shown, it is not verified in place on this frame, or there is no such member. */
  memberCanvasBox(population: string, member: string): [number, number, number, number] | 'not-found' | 'hidden' | 'out-of-view' {
    const h = this.header;
    const pop = this.population;
    if (!h || !pop || pop.id !== population) return this.populations.has(population) ? 'hidden' : 'not-found';
    if (!pop.members.some((m) => m.id === member)) return 'not-found';
    const { members, matrix: [a, b, c, d, e, f] } = drawable(pop, this.layer, h);
    const m = members.find((m) => m.id === member);
    if (!m || memberLook(m, this.layer?.style).hidden) return 'out-of-view';
    const p = this.placement;
    const pts = m.polygons.flat().map(([x, y]) => [p.ox + (a * x + b * y + c) * p.scale, p.oy + (d * x + e * y + f) * p.scale]);
    if (!pts.length) return 'out-of-view';
    const xs = pts.map((q) => q[0]), ys = pts.map((q) => q[1]);
    return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
  }

  /** A shown population's members drawn now, as one box on the canvas (CSS px), clipped to the canvas; or why not. */
  populationCanvasBox(population: string): [number, number, number, number] | 'not-found' | 'hidden' | 'out-of-view' {
    const h = this.header;
    const pop = this.population;
    if (!h || !pop || pop.id !== population) return this.populations.has(population) ? 'hidden' : 'not-found';
    const { members, matrix: [a, b, c, d, e, f] } = drawable(pop, this.layer, h);
    const p = this.placement;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const m of members) {
      if (memberLook(m, this.layer?.style).hidden) continue;
      for (const [x, y] of m.polygons.flat()) {
        const cx = p.ox + (a * x + b * y + c) * p.scale, cy = p.oy + (d * x + e * y + f) * p.scale;
        x0 = Math.min(x0, cx); y0 = Math.min(y0, cy); x1 = Math.max(x1, cx); y1 = Math.max(y1, cy);
      }
    }
    if (!Number.isFinite(x0)) return 'out-of-view';
    const r = this.canvas.getBoundingClientRect();
    x0 = Math.max(0, x0); y0 = Math.max(0, y0); x1 = Math.min(r.width, x1); y1 = Math.min(r.height, y1);
    return x1 - x0 < 2 || y1 - y0 < 2 ? 'out-of-view' : [x0, y0, x1, y1];
  }

  /** The slide's place (dx, dy, motion epoch) when frame `id` was shown, or the frame shown now's without an id; null
   * when that frame is too old to know. A mark anchored to it moves with the specimen from there. */
  frameOffset(id?: number): [number, number, number] | null {
    const h = this.header;
    if (!h) return null;
    if (id === undefined || id === h.id) return [h.dx, h.dy, h.epoch];
    return this.offsets.get(id) ?? null;
  }

  /** Points in a camera frame's own pixels on the canvas now (CSS px), moved with the slide since `anchor` (frameOffset);
   * null without a picture, while the slide is not tracked, or after a re-find (another motion epoch). */
  sourcePointsOnCanvas(points: Point[], anchor: [number, number, number]): Point[] | null {
    const h = this.header;
    if (!h || h.tracked === false || anchor[2] !== h.epoch) return null;
    const sx = h.dx - anchor[0], sy = h.dy - anchor[1];
    const { scale, ox, oy } = this.placement;
    return points.map(([x, y]) => [ox + (x + sx) * scale, oy + (y + sy) * scale]);
  }

  /** Where an object's centre is on screen now (client px), or null. */
  objectScreenPoint(id: string): Point | null {
    const h = this.header;
    const obj = this.objects.find((o) => o.id === id);
    if (!h || !obj || !this.placement) return null;
    const pts = obj.polygons.flat();
    if (!pts.length) return null;
    const sx = h.dx - obj.anchorOffset[0];
    const sy = h.dy - obj.anchorOffset[1];
    const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length + sx;
    const cy = pts.reduce((a, q) => a + q[1], 0) / pts.length + sy;
    const rect = this.canvas.getBoundingClientRect();
    return [rect.left + this.placement.ox + cx * this.placement.scale, rect.top + this.placement.oy + cy * this.placement.scale];
  }

  private draw(): void {
    // Hidden (another page is open): nothing to draw, and the room around the stage reads as none. The first draws
    // once it is shown again (its size observed, the panels' room measured) place the picture where it belongs.
    if (!this.canvas.width || !this.canvas.height) { this.settle = 2; return; }
    this.snapping = this.settle > 0;
    if (this.snapping) this.settle--;
    if (this.frame) this.timing.painted(this.frame.header, Date.now() / 1000);
    const ctx = this.ctx;
    const inset = this.easeInsets();
    const fullWidth = this.canvas.width / this.dpr;
    const fullHeight = this.canvas.height / this.dpr;
    // The live view keeps the area the side column leaves free.
    const width = Math.max(120, fullWidth - inset.right);
    const height = Math.max(120, fullHeight - inset.bottom);
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = WELL;
    ctx.fillRect(0, 0, fullWidth, fullHeight);
    const frame = this.frame;
    if (!frame) {
      const inner = Math.max(120, width - inset.left), cx = inset.left + inner / 2;
      const radius = Math.min(height * .94, inner * .7) / 2;
      ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,.06)'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.arc(cx, height / 2, radius, 0, Math.PI * 2); ctx.stroke(); ctx.restore();
      this.drawBezel(cx, height / 2, radius);
      return;
    }
    const h = frame.header;
    const fresh = this.isFresh();
    // Eased, so switching between the whole frame and the eyepiece glides instead of jumping.
    const p = (this.placement = this.easePlacement(this.place(h, width, height)));
    const disc = this.wholeFrame ? null : h.fov;

    // The eyepiece disc floats in the ink; the camera's black surround is cut away.
    ctx.save();
    if (disc) {
      const [cx, cy, r] = disc;
      ctx.beginPath();
      ctx.arc(p.ox + cx * p.scale, p.oy + cy * p.scale, r * p.scale * 0.985, 0, Math.PI * 2);
      ctx.clip();
    }
    ctx.globalAlpha = (fresh ? 1 : 0.35) * Math.max(0.15, Math.min(1, this.liveOpacity)); // dimmed, never hidden
    ctx.drawImage(frame.bitmap, p.ox, p.oy, h.w * p.scale, h.h * p.scale);
    if (disc) this.featherFrameEdges(h, p);
    ctx.restore();
    if (disc) {
      // A faint rim keeps the eyepiece round where the camera frame cuts it off.
      const [cx, cy, r] = disc;
      ctx.save();
      ctx.strokeStyle = 'rgba(231, 236, 247, 0.07)';
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.arc(p.ox + cx * p.scale, p.oy + cy * p.scale, r * p.scale * 0.985, 0, Math.PI * 2);
      ctx.stroke();
      ctx.restore();
      this.drawBezel(p.ox + cx * p.scale, p.oy + cy * p.scale, r * p.scale);
    }

    this.drawMagnifier(h, fresh);
    const links = this.windowLeaders(); // also clears placement indicators when the frame becomes stale
    if (fresh && !this.hideOverlays && links.length) {
      const canvas = this.canvas.getBoundingClientRect();
      ctx.save(); ctx.strokeStyle = 'rgba(255,255,255,.28)'; ctx.lineWidth = 1;
      for (const link of links) {
        const [x, y, width, height] = link.rect, [px, py] = link.point;
        if (px >= x && px <= x + width && py >= y && py <= y + height) continue;
        const endX = Math.max(x, Math.min(x + width, px)), endY = Math.max(y, Math.min(y + height, py));
        ctx.beginPath(); ctx.moveTo(px - canvas.left, py - canvas.top);
        ctx.lineTo(endX - canvas.left, endY - canvas.top); ctx.stroke();
      }
      ctx.restore();
    }

    // Freshness contract: never draw overlays on a stale picture.
    if (!fresh) return;

    const now = performance.now();
    const callouts: Callout[] = [];
    ctx.save();
    const opacity = overlayOpacity(Math.max(0, ...this.recentSpeeds));
    ctx.globalAlpha = opacity;
    if (h.fov) {
      // Outlines belong to the specimen, so they are only ever drawn inside the eyepiece.
      const [cx, cy, r] = h.fov;
      ctx.beginPath();
      ctx.arc(p.ox + cx * p.scale, p.oy + cy * p.scale, r * p.scale * 0.985 + 12, 0, Math.PI * 2);
      ctx.clip();
    }
    if (!this.hideOverlays) this.drawPopulation(h);
    if (!this.hideOverlays) this.drawMotionTracks(h);
    ctx.globalAlpha = opacity * Math.max(0, Math.min(1, this.outlinesOpacity));
    // Trails belong to the app's outlines: hidden and faded with them.
    if (!this.hideOverlays && this.outlinesVisible) this.drawTrails(h);
    for (const obj of this.hideOverlays || !this.outlinesVisible ? [] : this.objects) {
      if (this.solo && obj.arm && obj.arm !== this.solo) continue;
      // Dissected parts are shown on the stage only while their object is selected.
      if (obj.parent && obj.parent !== this.selected) continue;
      const callout = this.drawHighlight(obj, h, now);
      // Answers from several blind arms would crowd the margin; their labels show for the
      // arm being inspected (hover its vote button, or keys 1-4).
      const crowded = obj.arm && obj.label && !this.solo;
      // Parts are named in the magnifier, not with margin notes of their own.
      if (callout && !crowded && !obj.parent) callouts.push(callout);
    }
    if (this.showCandidates) this.drawCandidates(h);
    ctx.restore();
    ctx.save();
    ctx.globalAlpha = opacity;
    if (!this.hideOverlays) {
      this.drawCallouts(callouts, h, now, width, height, this.leftEdge());
      this.drawConnector(h);
    }
    ctx.restore();
    if (!this.hideOverlays) this.drawLegend(); // outside the eyepiece's clip
    if (!this.hideOverlays) this.drawTracksLegend(h);
    this.drawScaleBar(h, p);
    this.drawFocusMeter(h, p);
    this.drawDragBox();
  }

  /** The box being dragged to outline what is inside it: dashed, until it is let go. */
  private drawDragBox(): void {
    const box = this.dragBox;
    if (!box) return;
    const ctx = this.ctx;
    ctx.save();
    ctx.lineWidth = 1.5;
    ctx.setLineDash([6, 4]);
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.55)'; // a dark underline keeps it visible on a pale slide
    ctx.strokeRect(box[0] + 0.5, box[1] + 0.5, box[2] - box[0], box[3] - box[1]);
    ctx.strokeStyle = 'rgba(231, 236, 247, 0.95)';
    ctx.strokeRect(box[0], box[1], box[2] - box[0], box[3] - box[1]);
    ctx.restore();
  }

  /** Decorative lens-barrel engraving only: no labels or implied measurement scale. */
  private drawBezel(x: number, y: number, radius: number): void {
    const ctx = this.ctx;
    ctx.save(); ctx.lineCap = 'round';
    for (let degree = 0; degree < 360; degree += 5) {
      const angle = (degree - 90) * Math.PI / 180, major = degree % 30 === 0;
      const inner = radius + 7, outer = radius + (major ? 14 : 10);
      ctx.strokeStyle = major ? 'rgba(255,255,255,.22)' : 'rgba(255,255,255,.09)';
      ctx.lineWidth = major ? 1.2 : 1;
      ctx.beginPath();
      ctx.moveTo(x + Math.cos(angle) * inner, y + Math.sin(angle) * inner);
      ctx.lineTo(x + Math.cos(angle) * outer, y + Math.sin(angle) * outer);
      ctx.stroke();
    }
    ctx.restore();
  }

  /** A scale bar at the lower left of the eyepiece, once the objective is known; "~" when the
   * scale is an estimate from the objective rather than measured. */
  private drawScaleBar(h: FrameHeader, p: { ox: number; oy: number; scale: number }): void {
    const bar = scaleBar(this.scale?.umPerPx ?? null, p.scale);
    if (!bar || !h.fov || this.wholeFrame) return;
    const [cx, cy, r] = h.fov;
    const x = p.ox + (cx - r * 0.62) * p.scale;
    const y = p.oy + (cy + r * 0.74) * p.scale;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = PAPER;
    ctx.fillStyle = PAPER;
    ctx.lineWidth = 2;
    ctx.shadowColor = INK;
    ctx.shadowBlur = 4;
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.lineTo(x + bar.px, y);
    ctx.moveTo(x, y - 5);
    ctx.lineTo(x, y + 5);
    ctx.moveTo(x + bar.px, y - 5);
    ctx.lineTo(x + bar.px, y + 5);
    ctx.stroke();
    ctx.font = '600 12px system-ui, sans-serif';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`${this.scale?.source === 'measured' ? '' : '~'}${bar.um} µm`, x, y - 7);
    ctx.restore();
  }

  /** While the focus knob turns: a meter of sharpness against the sweep's best, and the cue. */
  private drawFocusMeter(h: FrameHeader, p: { ox: number; oy: number; scale: number }): void {
    const coach = this.coach;
    if (!coach || coach.focus === null || !h.fov || this.wholeFrame) return;
    const [cx, cy, r] = h.fov;
    const width = Math.min(160, r * p.scale * 0.6);
    const x = p.ox + cx * p.scale - width / 2;
    const y = p.oy + (cy - r * 0.86) * p.scale;
    const ctx = this.ctx;
    ctx.save();
    ctx.fillStyle = 'rgba(19, 26, 43, 0.72)';
    ctx.fillRect(x - 8, y - 20, width + 16, 34);
    ctx.fillStyle = 'rgba(231, 236, 247, 0.18)';
    ctx.fillRect(x, y, width, 6);
    ctx.fillStyle = coach.cue === 'passed' ? IODINE : PAPER;
    ctx.fillRect(x, y, width * Math.max(0, Math.min(1, coach.focus)), 6);
    ctx.font = '600 12px system-ui, sans-serif';
    ctx.textBaseline = 'bottom';
    ctx.fillStyle = PAPER;
    ctx.fillText(FOCUS_CUES[coach.cue ?? ''] ?? 'Focus', x, y - 4);
    ctx.restore();
  }

  private drawConnector(h: FrameHeader): void {
    const anchor = this.magnifierAnchor;
    const target = this.magnifier;
    if (!anchor || !target) return;
    const obj = this.objects.find((o) => o.id === target.objectId);
    if (!obj || !isVisible(obj, h)) return;
    const rect = this.canvas.getBoundingClientRect();
    const at = this.objectScreenPoint(obj.id);
    if (!at) return;
    const x = at[0] - rect.left;
    const y = at[1] - rect.top;
    const ctx = this.ctx;
    ctx.save();
    ctx.strokeStyle = EOSIN;
    ctx.globalAlpha *= 0.45;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 4]);
    ctx.beginPath();
    ctx.moveTo(x, y);
    ctx.bezierCurveTo(x + (anchor[0] - x) * 0.5, y, x + (anchor[0] - x) * 0.5, anchor[1], anchor[0], anchor[1]);
    ctx.stroke();
    ctx.restore();
  }

  /** The live magnifier: the object's area of the current frame, enlarged, with its outline
   * and any dissected parts. Dimmed and without outlines on a stale picture. */
  private drawMagnifier(h: FrameHeader, fresh: boolean): void {
    const target = this.magnifier;
    if (!target || !this.frame) return;
    const obj = this.objects.find((o) => o.id === target.objectId);
    const canvas = target.canvas;
    const cssW = canvas.clientWidth;
    const cssH = canvas.clientHeight;
    if (!obj || !cssW || !cssH) return;
    const dpr = this.dpr;
    if (canvas.width !== Math.round(cssW * dpr) || canvas.height !== Math.round(cssH * dpr)) {
      canvas.width = Math.round(cssW * dpr);
      canvas.height = Math.round(cssH * dpr);
    }
    const mctx = canvas.getContext('2d');
    if (!mctx) return;
    mctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    mctx.fillStyle = INK;
    mctx.fillRect(0, 0, cssW, cssH);
    if (!isVisible(obj, h) || obj.track === 'refinding' || obj.anchorEpoch !== h.epoch) {
      // Never a blank square: say why there is nothing to magnify right now. A frame the tracker could
      // not register places nothing: that is re-finding, never "out of view" (going live, G2).
      const refinding = obj.track === 'refinding' || obj.anchorEpoch !== h.epoch || h.tracked === false;
      const why = refinding ? 'Re-finding it…' : 'Out of view. Move the slide back to it.';
      this.magnifierLostWhy = refinding ? 'refinding' : 'out';
      mctx.fillStyle = MUTED;
      mctx.font = '400 13px "Instrument Sans", system-ui, sans-serif';
      mctx.textAlign = 'center';
      mctx.textBaseline = 'middle';
      mctx.fillText(why, cssW / 2, cssH / 2);
      this.magnifierZoom = 0;
      if (fresh && !this.magnifierLostSince) this.magnifierLostSince = performance.now();
      return;
    }
    this.magnifierLostSince = 0;
    const sx = h.dx - obj.anchorOffset[0];
    const sy = h.dy - obj.anchorOffset[1];
    const pts = obj.polygons.flat();
    const xs = pts.map((q) => q[0] + sx);
    const ys = pts.map((q) => q[1] + sy);
    const bw = Math.max(...xs) - Math.min(...xs);
    const bh = Math.max(...ys) - Math.min(...ys);
    // Level 1: the object with its padding, fitted to the panel's shape (overlay.ts fitWindow,
    // as fit_window in orchestrator/magnifier.py). A level divides that window; the server
    // keeps it within its maxLevel.
    const win = magnifierWindow({ w: bw, h: bh, cx: (Math.max(...xs) + Math.min(...xs)) / 2, cy: (Math.max(...ys) + Math.min(...ys)) / 2 },
      this.magnifierLevel, this.magnifierCenter ? [this.magnifierCenter[0] + sx, this.magnifierCenter[1] + sy] : null, cssW, cssH);
    const { sw, sh, k } = win;
    const cx = win.x0 + sw / 2;
    const cy = win.y0 + sh / 2;
    // Past the camera's detail (the server's `pixelated`), smoothing only blurs: show the
    // camera's pixels as blocks.
    mctx.globalAlpha = fresh ? 1 : 0.35;
    mctx.imageSmoothingEnabled = !this.magnifierPixelated;
    mctx.imageSmoothingQuality = 'high';
    mctx.drawImage(this.frame.bitmap, cx - sw / 2, cy - sh / 2, sw, sh, 0, 0, cssW, cssH);
    mctx.imageSmoothingEnabled = true;
    mctx.globalAlpha = 1;
    this.magnifierZoom = k / Math.max(this.placement.scale, 1e-6);
    if (!fresh) return;
    const to = ([x, y]: Point): Point => [(x + sx - (cx - sw / 2)) * k, (y + sy - (cy - sh / 2)) * k];
    const outline = (o: SceneObject, color: string, width: number) => {
      mctx.beginPath();
      for (const poly of o.polygons) {
        poly.forEach((pt, i) => {
          const [x, y] = to(pt);
          if (i === 0) mctx.moveTo(x, y);
          else mctx.lineTo(x, y);
        });
        mctx.closePath();
      }
      mctx.strokeStyle = CASING;
      mctx.lineWidth = width + 2;
      mctx.stroke();
      mctx.strokeStyle = color;
      mctx.lineWidth = width;
      mctx.stroke();
    };
    outline(obj, this.outlineColor, 2);
    const parts = this.objects.filter((o) => o.parent === obj.id);
    // Each kind of part is named once, on its largest piece, with a count if there are several.
    const area = (o: SceneObject) => {
      const q = o.polygons.flat();
      const xs = q.map((p) => p[0]);
      const ys = q.map((p) => p[1]);
      return (Math.max(...xs) - Math.min(...xs)) * (Math.max(...ys) - Math.min(...ys));
    };
    const named = new Map<string, { part: SceneObject; count: number }>();
    for (const part of parts) {
      outline(part, PAPER, 1.5);
      const entry = named.get(part.label);
      if (!entry) named.set(part.label, { part, count: 1 });
      else {
        entry.count += 1;
        if (area(part) > area(entry.part)) entry.part = part;
      }
    }
    for (const { part, count } of named.values()) {
      const pp = part.polygons.flat().map(to);
      const px = pp.reduce((a, q) => a + q[0], 0) / pp.length;
      const py = pp.reduce((a, q) => a + q[1], 0) / pp.length;
      const text = count > 1 ? `${part.label} ×${count}` : part.label;
      if (part.label) {
        mctx.font = '600 12px "Instrument Sans", system-ui, sans-serif';
        mctx.textAlign = 'center';
        const w = mctx.measureText(text).width + 10;
        mctx.fillStyle = 'rgba(19, 26, 43, 0.78)';
        mctx.beginPath();
        mctx.roundRect(px - w / 2, py - 9, w, 18, 9);
        mctx.fill();
        mctx.fillStyle = PAPER;
        mctx.fillText(text, px, py + 4);
      }
    }
  }

  private project(obj: SceneObject, h: FrameHeader): ((pt: Point) => Point) | null {
    if (!isVisible(obj, h)) return null;
    const sx = h.dx - obj.anchorOffset[0];
    const sy = h.dy - obj.anchorOffset[1];
    const { scale, ox, oy } = this.placement;
    return ([x, y]) => [ox + (x + sx) * scale, oy + (y + sy) * scale];
  }

  private trace(polygons: Point[][], to: (pt: Point) => Point): void {
    const ctx = this.ctx;
    ctx.beginPath();
    for (const poly of polygons) {
      poly.forEach((pt, i) => {
        const [x, y] = to(pt);
        if (i === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.closePath();
    }
  }

  /** The shown population's verified members, each in its colour (population.ts), moved with the
   * slide from their capture (or, found on the Stage's still, placed by its fit); members not verified in place are not
   * drawn on the live view. */
  private drawPopulation(h: FrameHeader): void {
    const { members, matrix: [a, b, c, d, e, f] } = drawable(this.population, this.layer, h);
    if (!members.length) return;
    const ctx = this.ctx;
    const p = this.placement;
    // A member's pixel, through the frame (its capture's place in it), onto the canvas (the frame's place on the Stage).
    const to = ([x, y]: Point): Point => [p.ox + (a * x + b * y + c) * p.scale, p.oy + (d * x + e * y + f) * p.scale];
    ctx.save();
    const opacity = this.layer?.style.opacity ?? 1;
    const ramp = this.population ? this.rampOf(this.population) : 1;
    const fade = ctx.globalAlpha * ramp; // the overlays' fade while the slide moves (overlayOpacity), set by the caller
    for (const m of members) {
      const look = memberLook(m, this.layer?.style);
      if (look.hidden) continue;
      const selected = m.id === this.selectedMember;
      // The member under the pointer is a little firmer, so it is plain which one a click selects.
      const hovered = !selected && m.id === this.hoveredMember;
      const alpha = fade * Math.max(0, Math.min(1, opacity)) * look.alpha;
      this.trace(m.polygons, to);
      if (look.fill) {
        ctx.globalAlpha = alpha * 0.3;
        ctx.fillStyle = look.colour;
        ctx.fill('evenodd');
      }
      ctx.globalAlpha = alpha * (selected || hovered ? 1 : 0.8);
      const width = selected ? 3 : hovered ? 2.25 : 1.5;
      ctx.lineWidth = width + 1.5;
      ctx.strokeStyle = CASING;
      ctx.stroke();
      ctx.lineWidth = width;
      ctx.strokeStyle = look.colour;
      ctx.stroke();
    }
    ctx.restore();
  }

  /** The shown motion tracks, only while their place is verified (motiontracks.ts): each segment on its
   * own, older parts fainter, a dot where a moving one ended, a ring where one was found again and a
   * cross where one was lost. Drawn with the layer's own opacity and the overlays' fade. */
  private drawMotionTracks(h: FrameHeader): void {
    const { scale, ox, oy } = this.placement;
    const drawing = tracksDrawing(this.shownTracks, this.tracksLayer, h, ([x, y]) => [ox + x * scale, oy + y * scale]);
    if (!drawing) return;
    const ctx = this.ctx;
    const base = ctx.globalAlpha * Math.max(0, Math.min(1, this.tracksLayer?.style.opacity ?? 1));
    ctx.save();
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const piece of drawing.pieces) {
      ctx.globalAlpha = base * piece.alpha;
      ctx.strokeStyle = piece.colour;
      ctx.lineWidth = piece.width;
      ctx.beginPath();
      ctx.moveTo(...piece.from);
      ctx.lineTo(...piece.to);
      ctx.stroke();
    }
    ctx.lineWidth = 1.5;
    for (const mark of drawing.marks) {
      ctx.globalAlpha = base * mark.alpha;
      ctx.strokeStyle = mark.colour;
      ctx.fillStyle = mark.colour;
      const [x, y] = mark.at;
      ctx.beginPath();
      if (mark.kind === 'head') {
        ctx.arc(x, y, 3, 0, Math.PI * 2);
        ctx.fill();
        continue;
      }
      if (mark.kind === 'reacquired') {
        ctx.arc(x, y, 4, 0, Math.PI * 2);
      } else {
        ctx.moveTo(x - 4, y - 4);
        ctx.lineTo(x + 4, y + 4);
        ctx.moveTo(x + 4, y - 4);
        ctx.lineTo(x - 4, y + 4);
      }
      ctx.stroke();
    }
    ctx.restore();
  }

  /** The shown tracks' interval and units, and their colours' range (a legend), at the bottom right of
   * the view: only while they are drawn. */
  private drawTracksLegend(h: FrameHeader): void {
    if (this.externalLegends) return;
    const item = this.shownTracks;
    if (!item || !tracksDrawing(item, this.tracksLayer, h, (pt) => pt)) return;
    const ctx = this.ctx;
    const width = this.canvas.width / this.dpr;
    const y = this.canvas.height / this.dpr - 16;
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.font = '11px system-ui, sans-serif';
    ctx.fillStyle = PAPER;
    ctx.shadowColor = INK;
    ctx.shadowBlur = 4;
    const label = tracksLabel(item);
    ctx.fillText(label, width - 16 - ctx.measureText(label).width, y);
    const legend = this.tracksLayer?.style.colour === 'column' ? this.tracksLayer.style.legend : undefined;
    if (legend) {
      const w = 140;
      const x = width - 16 - w;
      const bar = ctx.createLinearGradient(x, 0, x + w, 0);
      for (let i = 0; i <= 8; i++) bar.addColorStop(i / 8, heat(i / 8));
      ctx.shadowBlur = 0;
      ctx.fillStyle = bar;
      ctx.fillRect(x, y - 30, w, 8);
      ctx.fillStyle = PAPER;
      const n = (v: number) => (Math.abs(v) >= 100 ? Math.round(v).toString() : v.toPrecision(2));
      ctx.fillText(`${legend.label}: ${n(legend.min)}`, x, y - 34);
      const high = `${n(legend.max)} ${legend.unit}`.trim();
      ctx.fillText(high, x + w - ctx.measureText(high).width, y - 34);
    }
    ctx.restore();
  }

  /** Where followed outlines went on their own: older pieces fainter, a ring where one was found
   * again and a cross where it was lost, never a line across a gap; the selected one (or the only
   * one that went somewhere) says how far and over how long. Drawn with the outlines' own opacity. */
  private drawTrails(h: FrameHeader): void {
    if (!this.trails.length) return;
    const ctx = this.ctx;
    const { scale, ox, oy } = this.placement;
    const to = ([x, y]: Point): Point => [ox + x * scale, oy + y * scale];
    // Only for outlines drawn now: inside the eyepiece (inViewRadius), as the outlines themselves are.
    const shown = new Set(this.objects.filter((o) => (!o.arm || o.arm === this.solo) && isVisible(o, h)).map((o) => o.id));
    const label = labelled(this.trails.filter((t) => shown.has(t.id)), this.selected);
    const base = ctx.globalAlpha;
    ctx.save();
    ctx.strokeStyle = this.outlineColor;
    ctx.fillStyle = PAPER;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    for (const trail of this.trails) {
      if (!shown.has(trail.id)) continue;
      const drawing = trailDrawing(trail, h, this.trailWindow, to);
      ctx.lineWidth = trail.id === this.selected ? 2.5 : 1.75;
      for (const piece of drawing.pieces) {
        ctx.globalAlpha = base * piece.alpha * 0.9;
        ctx.beginPath();
        ctx.moveTo(...piece.from);
        ctx.lineTo(...piece.to);
        ctx.stroke();
      }
      ctx.lineWidth = 1.5;
      for (const mark of drawing.marks) {
        ctx.globalAlpha = base * mark.alpha;
        const [x, y] = mark.at;
        ctx.beginPath();
        if (mark.kind === 'reacquired') {
          ctx.arc(x, y, 4, 0, Math.PI * 2);
        } else {
          ctx.moveTo(x - 4, y - 4);
          ctx.lineTo(x + 4, y + 4);
          ctx.moveTo(x + 4, y - 4);
          ctx.lineTo(x - 4, y + 4);
        }
        ctx.stroke();
      }
      if (label === trail && trail.words && drawing.head) {
        ctx.globalAlpha = base;
        ctx.font = '600 11px system-ui, sans-serif';
        ctx.textBaseline = 'top';
        ctx.shadowColor = INK;
        ctx.shadowBlur = 4;
        ctx.fillText(trail.words, drawing.head[0] + 8, drawing.head[1] + 8);
        ctx.shadowBlur = 0;
      }
    }
    ctx.restore();
  }

  /** The legend of a layer coloured by a measurement (its range and unit) or by a category (each with its
   * colour and count), bottom left of the view. */
  externalLegends = false; // The Studio layer pills render these same facts accessibly outside the specimen.
  private drawLegend(): void {
    if (this.externalLegends) return;
    const style = this.layer?.style;
    if (style?.colour === 'category' && style.legend?.categories) {
      this.drawCategories(style.legend.label, style.legend.categories);
      return;
    }
    const legend = style?.colour === 'column' ? style.legend : undefined;
    if (!legend || legend.min === undefined || legend.max === undefined) return;
    const ctx = this.ctx;
    const x = 16; // CSS px (the context is scaled by the device pixel ratio)
    const y = this.canvas.height / this.dpr - 44;
    const w = 140;
    const h = 8;
    ctx.save();
    ctx.globalAlpha = 1;
    const bar = ctx.createLinearGradient(x, 0, x + w, 0);
    for (let i = 0; i <= 8; i++) bar.addColorStop(i / 8, heat(i / 8));
    ctx.fillStyle = bar;
    ctx.fillRect(x, y, w, h);
    ctx.fillStyle = PAPER;
    ctx.font = '11px system-ui, sans-serif';
    const n = (v: number) => (Math.abs(v) >= 100 ? Math.round(v).toString() : v.toPrecision(2));
    ctx.fillText(`${legend.label}: ${n(legend.min)}`, x, y - 4);
    const high = `${n(legend.max)} ${legend.unit ?? ''}`.trim();
    ctx.fillText(high, x + w - ctx.measureText(high).width, y - 4);
    ctx.restore();
  }

  private drawCategories(label: string, categories: LegendCategory[]): void {
    const ctx = this.ctx;
    const x = 16;
    let y = this.canvas.height / this.dpr - 20 - 16 * categories.length;
    ctx.save();
    ctx.globalAlpha = 1;
    ctx.font = '11px system-ui, sans-serif';
    ctx.fillStyle = PAPER;
    ctx.fillText(label, x, y - 6);
    categories.forEach((c, i) => {
      ctx.fillStyle = categoryColour(i);
      ctx.fillRect(x, y, 10, 10);
      ctx.fillStyle = PAPER;
      ctx.fillText(categoryWords(c), x + 16, y + 9);
      y += 16;
    });
    ctx.restore();
  }

  private drawHighlight(obj: SceneObject, h: FrameHeader, now: number): Callout | null {
    const to = this.project(obj, h);
    if (!to) return null;
    const ctx = this.ctx;
    if (obj.parent) {
      // A dissected part is drawn as a part of its object: a thin pale line inside the object's outline, no fill, glow
      // or small-object ring, as the magnifier draws parts (live 2026-10-11: drawn like the stoma's own outline, a
      // stoma's parts read as a doubled outline of it).
      ctx.save();
      this.trace(obj.polygons, to);
      ctx.lineJoin = 'round';
      ctx.lineWidth = 1.5;
      ctx.strokeStyle = PAPER;
      ctx.globalAlpha *= 0.85;
      ctx.stroke();
      ctx.restore();
      return null;
    }
    const refinding = obj.track === 'refinding' || obj.anchorEpoch !== h.epoch;
    // Not sure of the name: a quieter, dashed outline (the label says "Not sure").
    // or the outline check doubts the outline itself (`fit`): the same quieter dashed line, the label says which.
    const unsure = (obj.status === 'unsure' || !!obj.fit) && !obj.arm;
    // "Not sure" is said in the label and by a dashed line, never by a pale outline: on a light
    // slide a near-white outline could not be seen (live 2026-10-06, "make highlight color easier to see").
    const color = refinding || obj.status === 'looking' ? IODINE : this.outlineColor;
    const born = this.firstSeen.get(obj.id) ?? now;
    const progress = this.reducedMotion ? 1 : Math.min(1, (now - born) / DRAW_IN_MS);

    const edge = this.outlineStyle === 'edge';
    ctx.save();
    if (edge) {
      // Only the outside half of a doubled stroke shows: the line sits just outside the edge.
      ctx.beginPath();
      ctx.rect(0, 0, this.canvas.width, this.canvas.height);
      for (const poly of obj.polygons) {
        poly.forEach((pt, i) => {
          const [x, y] = to(pt);
          if (i === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        });
        ctx.closePath();
      }
      ctx.clip('evenodd');
    }
    this.trace(obj.polygons, to);
    if (progress >= 1 && !refinding && !edge) {
      // The fill is faint; the stroke keeps the alpha in force (the outlines' opacity and the fade while
      // the slide moves), so "fade the outlines" is what is drawn (M1.5b review item 2).
      const kept = ctx.globalAlpha;
      ctx.fillStyle = color;
      ctx.globalAlpha = kept * 0.12;
      ctx.fill('evenodd'); // a ring's inner edge is a hole (services/gpu_worker mask_to_polygons)
      ctx.globalAlpha = kept;
    }
    ctx.lineJoin = 'round';
    const width = (obj.id === this.selected ? 3.25 : obj.id === this.hovered ? 2.75 : 2.25) * (edge ? 2 : 1);
    ctx.lineWidth = width;
    ctx.strokeStyle = color;
    // A dark glow, not the line's own colour: the line's colour glowing on a stain of that colour hid it.
    ctx.shadowColor = CASING;
    ctx.shadowBlur = 6;
    if (refinding) {
      ctx.setLineDash([5, 5]);
    } else if (unsure && progress >= 1) {
      ctx.setLineDash([7, 4]);
    } else if (obj.arm) {
      // Arms keep their pattern, so they fade in rather than drawing along their length.
      ctx.lineCap = 'round';
      ctx.setLineDash(ARM_DASH[obj.arm] ?? []);
      ctx.globalAlpha *= progress;
    } else if (progress < 1) {
      // Draw-in: reveal the outline along its length.
      const length = perimeter(obj.polygons[0].map(to));
      ctx.setLineDash([length * progress, length]);
    }
    ctx.save();
    ctx.shadowBlur = 0;
    ctx.strokeStyle = CASING;
    ctx.lineWidth = width + 2;
    ctx.stroke(); // the casing first, under the line (the same dash, so a dashed outline stays dashed)
    ctx.restore();
    ctx.stroke();
    ctx.restore();

    const pts = obj.polygons.flat().map(to);
    const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length;
    const cy = pts.reduce((a, q) => a + q[1], 0) / pts.length;
    // Something tiny (a single speck) still gets a clear mark: a soft ring around it.
    const span = Math.max(Math.max(...pts.map((q) => q[0])) - Math.min(...pts.map((q) => q[0])),
      Math.max(...pts.map((q) => q[1])) - Math.min(...pts.map((q) => q[1])));
    if (span < TINY_PX && !refinding) {
      ctx.save();
      ctx.globalAlpha *= 0.85 * progress;
      ctx.lineWidth = (obj.id === this.selected ? 2 : 1.5) + 2;
      ctx.strokeStyle = CASING;
      ctx.beginPath();
      ctx.arc(cx, cy, TINY_PX * 0.75, 0, Math.PI * 2);
      ctx.stroke();
      ctx.strokeStyle = color;
      ctx.lineWidth = obj.id === this.selected ? 2 : 1.5;
      ctx.shadowColor = CASING;
      ctx.shadowBlur = 4;
      ctx.stroke();
      ctx.restore();
    }
    const fov = h.fov;
    const centre: Point = fov
      ? [this.placement.ox + (fov[0]) * this.placement.scale, this.placement.oy + fov[1] * this.placement.scale]
      : [this.canvas.width / this.dpr / 2, this.canvas.height / this.dpr / 2];
    // With the side column open every note goes left, so its leader leaves the outline leftward.
    const leftOnly = this.canvas.width / this.dpr > 520 && this.reserved.right > 0 && this.reserved.bottom === 0;
    const angle = Math.atan2(cy - centre[1], leftOnly ? -Math.abs(cx - centre[0]) - 1 : cx - centre[0]);
    // Anchor the leader on the outline point that faces outward.
    let anchor: Point = pts[0];
    let best = -Infinity;
    for (const q of pts) {
      const d = (q[0] - cx) * Math.cos(angle) + (q[1] - cy) * Math.sin(angle);
      if (d > best) {
        best = d;
        anchor = q;
      }
    }
    return {
      obj,
      anchor,
      angle,
      title: refinding ? 'Re-finding' : obj.arm && !obj.label ? `Outline ${obj.arm}` : obj.label || (obj.status === 'looking' ? 'Looking…' : 'Outlined'),
      detail: refinding ? detailFor(obj) : [this.describe?.(obj) || detailFor(obj), fitWords(obj), taughtWords(obj)].filter(Boolean).join(' · '),
      tone: color,
      bornAt: born,
    };
  }

  /** `left`: where the notes' room begins on the left (right of the sessions rail); `width`: where it ends. */
  private drawCallouts(callouts: Callout[], h: FrameHeader, now: number, width: number, height: number, left = 0): void {
    this.labels = [];
    if (!callouts.length) return;
    const ctx = this.ctx;
    const { scale, ox, oy } = this.placement;
    const fov = h.fov ?? [h.w / 2, h.h / 2, Math.min(h.w, h.h) / 2];
    const cx = ox + fov[0] * scale;
    const cy = oy + fov[1] * scale;
    const ring = fov[2] * scale + 28;

    // Notes sit in the margin on whichever side the object is, stacked without overlap.
    const sides: Record<'left' | 'right', { c: Callout; y: number }[]> = { left: [], right: [] };
    const leftOnly = width > 520 && this.reserved.right > 0 && this.reserved.bottom === 0; // the side column is open
    for (const c of callouts) {
      const side = leftOnly || Math.cos(c.angle) < 0 ? 'left' : 'right';
      sides[side].push({ c, y: cy + Math.sin(c.angle) * ring });
    }
    // Stack each side's notes without overlap: push down from the top, then back up from
    // the bottom edge; notes that then no longer fit at the top are left out rather than
    // drawn on top of each other (their outlines still show).
    const layout = (items: { c: Callout; y: number }[], top: number, bottom: number) => {
      items.sort((a, b) => a.y - b.y);
      let last = -Infinity;
      for (const item of items) {
        item.y = Math.max(item.y, last + 52, top);
        last = item.y;
      }
      let next = Infinity;
      for (const item of [...items].reverse()) {
        item.y = Math.min(item.y, next - 52, bottom);
        next = item.y;
      }
    };
    // Notes on the floating magnifier's side keep clear of it: below it when it floats at the
    // top, above it when it floats at the bottom (panels.ts notesBeside).
    const tops = { left: 28, right: 28 };
    const bottoms = { left: height - 30, right: height - 30 };
    const room = notesBeside(this.magnifierBox, cx, height);
    const crowded = room?.side ?? 'right';
    const other = crowded === 'right' ? 'left' : 'right';
    if (room) {
      tops[crowded] = room.top;
      bottoms[crowded] = room.bottom;
    }
    for (const box of this.cornerNotes) {
      const side = box.x + box.w / 2 < cx ? 'left' : 'right';
      if (box.y + box.h / 2 < height / 2) tops[side] = Math.max(tops[side], box.y + box.h + 28);
      else bottoms[side] = Math.min(bottoms[side], box.y - 24);
    }
    layout(sides[crowded], tops[crowded], bottoms[crowded]);
    // Notes squeezed out there by the magnifier move to the other side (with the side column
    // open there is no right side to move to: they are left out, their outlines still show).
    const squeezed = sides[crowded].filter((item) => item.y < tops[crowded]);
    if (squeezed.length) {
      sides[crowded] = sides[crowded].filter((item) => item.y >= tops[crowded]);
      if (!(leftOnly && other === 'right')) {
        for (const item of squeezed) sides[other].push({ c: item.c, y: cy + Math.sin(item.c.angle) * ring });
      }
    }
    layout(sides[other], tops[other], bottoms[other]);
    for (const side of ['left', 'right'] as const) {
      const items = sides[side];
      const top = tops[side];
      for (const { c, y } of items.filter((item) => item.y >= top)) {
        const appear = this.reducedMotion ? 1 : clamp01((now - c.bornAt - LABEL_DELAY_MS) / LABEL_SLIDE_MS);
        if (appear <= 0) continue;
        const ease = 1 - (1 - appear) ** 3;
        const dir = side === 'right' ? 1 : -1;
        const elbowX = cx + dir * Math.sqrt(Math.max(0, ring * ring - (y - cy) ** 2));
        const elbow: Point = [Number.isFinite(elbowX) ? elbowX : cx + dir * ring, y];
        const selected = c.obj.id === this.selected;
        const closeW = selected ? 22 : 0;

        // Is there room beside the disc for the whole note? Measured with the real fonts,
        // at the note's final position, so the layout never flips while it slides in and
        // no line is ever cut off at the edge. If not, the note sits above its leader line,
        // which runs out to the edge underneath it like an underline.
        const finalX = elbow[0] + dir * 32;
        const room = side === 'right' ? width - 10 - finalX : finalX - left - 10;
        // Decided once per label (and side), with room reserved for a second line, so a line
        // that arrives or grows later never flips the layout under the user's eyes.
        const layoutKey = `${c.obj.id}:${side}:${leftOnly}`;
        let stacked = this.layouts.get(layoutKey);
        // Re-decide only if the name itself would no longer fit beside it (the window resized).
        if (stacked === false && room < this.textWidth(c.title, TITLE_FONT(21)) + closeW + 8) stacked = undefined;
        if (stacked === undefined) {
          const besideW = Math.max(this.textWidth(c.title, TITLE_FONT(21)), Math.min(DETAIL_RESERVE, Math.max(this.textWidth(c.detail, DETAIL_FONT), 160))) + 22;
          stacked = room < besideW;
          this.layouts.set(layoutKey, stacked);
        }
        const textX = elbow[0] + dir * (14 + 18 * ease);
        const edgeX = side === 'right' ? width - 10 : left + 10;
        const titleSize = stacked ? 18 : 21;
        const maxW = stacked ? width - left - 20 - closeW : room - closeW;
        const title = this.fit(c.title, TITLE_FONT(titleSize), maxW);
        const detail = this.fit(c.detail, DETAIL_FONT, Math.min(stacked ? width - left - 20 : room - 4, DETAIL_RESERVE));
        // A second line that changed fades in rather than popping.
        const seen = this.details.get(c.obj.id);
        if (!seen || seen.text !== detail) this.details.set(c.obj.id, { text: detail, at: seen ? now : now - 1000 });
        const detailAlpha = this.reducedMotion ? 1 : clamp01((now - (this.details.get(c.obj.id)?.at ?? 0)) / 320);
        const lineEndX = stacked ? edgeX : textX - dir * 6;
        const labelX = stacked ? edgeX : textX;
        const titleY = stacked ? y - 6 : y + 6;
        const detailY = stacked ? y + 15 : y + 24;

        ctx.save();
        ctx.globalAlpha *= ease;
        ctx.strokeStyle = c.tone;
        ctx.lineWidth = selected ? 1.75 : 1.25;
        ctx.beginPath();
        ctx.moveTo(c.anchor[0], c.anchor[1]);
        ctx.lineTo(elbow[0], elbow[1]);
        ctx.lineTo(lineEndX, y);
        ctx.stroke();
        ctx.fillStyle = c.tone;
        ctx.beginPath();
        ctx.arc(c.anchor[0], c.anchor[1], 2.5, 0, Math.PI * 2);
        ctx.fill();

        // Text grows away from the leader: rightward beside a right-side leader, leftward
        // beside a left one; stacked notes grow inward from the screen edge.
        const growsRight = side === 'right' ? !stacked : stacked;
        if (stacked) {
          // Stacked notes can sit over the bright specimen; a dark backing keeps them legible.
          ctx.font = TITLE_FONT(titleSize);
          const tw = ctx.measureText(title).width + closeW;
          ctx.font = DETAIL_FONT;
          const bw = Math.max(tw, detail ? ctx.measureText(detail).width : 0) + 12;
          const bx = growsRight ? labelX - 6 : labelX - bw + 6;
          ctx.fillStyle = 'rgba(19, 26, 43, 0.72)';
          ctx.beginPath();
          ctx.roundRect(bx, titleY - titleSize - 2, bw, (detail ? detailY + 6 : titleY + 6) - (titleY - titleSize - 2), 6);
          ctx.fill();
        }
        ctx.textAlign = growsRight ? 'left' : 'right';
        ctx.textBaseline = 'alphabetic';
        ctx.fillStyle = PAPER;
        ctx.font = TITLE_FONT(titleSize);
        ctx.fillText(title, labelX, titleY);
        const titleW = ctx.measureText(title).width;
        let detailW = 0;
        if (detail) {
          ctx.save();
          ctx.globalAlpha *= detailAlpha;
          ctx.fillStyle = MUTED;
          ctx.font = DETAIL_FONT;
          ctx.fillText(detail, labelX, detailY);
          detailW = ctx.measureText(detail).width;
          ctx.restore();
        }
        // The × that removes the selected object, at the far end of its title.
        let close: LabelHit['close'] = null;
        if (selected) {
          const xEnd = growsRight ? labelX + titleW + 8 : labelX - titleW - 8 - 14;
          const xc = xEnd + 7;
          const yc = titleY - titleSize * 0.32;
          ctx.strokeStyle = PAPER;
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.moveTo(xc - 4.5, yc - 4.5);
          ctx.lineTo(xc + 4.5, yc + 4.5);
          ctx.moveTo(xc + 4.5, yc - 4.5);
          ctx.lineTo(xc - 4.5, yc + 4.5);
          ctx.stroke();
          close = [xc - 11, yc - 11, xc + 11, yc + 11];
        } else if (c.obj.label && !c.obj.arm) {
          // A quiet › after the name: it opens its details (brighter while the pointer is on it).
          const xc = growsRight ? labelX + titleW + 9 : labelX - titleW - 9;
          const yc = titleY - titleSize * 0.32;
          const dir = 1; // always ›, whichever way the name runs
          ctx.save();
          ctx.globalAlpha *= c.obj.id === this.hovered ? 0.95 : 0.45;
          ctx.strokeStyle = PAPER;
          ctx.lineWidth = 1.4;
          ctx.lineCap = 'round';
          ctx.lineJoin = 'round';
          ctx.beginPath();
          ctx.moveTo(xc - 2 * dir, yc - 4);
          ctx.lineTo(xc + 2 * dir, yc);
          ctx.lineTo(xc - 2 * dir, yc + 4);
          ctx.stroke();
          ctx.restore();
        }
        ctx.restore();
        const w = Math.max(titleW, detailW) + 16; // with the › or ×
        const x0 = growsRight ? labelX : labelX - w;
        this.labels.push({ id: c.obj.id, rect: [x0 - 4, titleY - titleSize, x0 + w + 4, (detail ? detailY : titleY) + 6], close });
      }
    }
  }

  private textWidth(text: string, font: string): number {
    if (!text) return 0;
    this.ctx.save();
    this.ctx.font = font;
    const w = this.ctx.measureText(text).width;
    this.ctx.restore();
    return w;
  }

  /** The text, shortened with an ellipsis if it is wider than maxW. */
  private fit(text: string, font: string, maxW: number): string {
    if (!text || this.textWidth(text, font) <= maxW) return text;
    let lo = 0;
    let hi = text.length;
    while (lo < hi) {
      const mid = Math.ceil((lo + hi) / 2);
      if (this.textWidth(text.slice(0, mid) + '…', font) <= maxW) lo = mid;
      else hi = mid - 1;
    }
    return lo > 0 ? text.slice(0, lo).trimEnd() + '…' : '';
  }

  /** The label (and whether its ×) under a client point, if any. */
  labelAt(clientX: number, clientY: number): { id: string; close: boolean } | null {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const inside = (r: [number, number, number, number]) => x >= r[0] && x <= r[2] && y >= r[1] && y <= r[3];
    for (const label of [...this.labels].reverse()) {
      if (label.close && inside(label.close)) return { id: label.id, close: true };
      if (inside(label.rect)) return { id: label.id, close: false };
    }
    return null;
  }


  private drawCandidates(h: FrameHeader): void {
    if (!this.candidates) return;
    const from = this.offsets.get(this.candidates.frameId);
    if (!from) return;
    const sx = h.dx - from[0];
    const sy = h.dy - from[1];
    const { scale, ox, oy } = this.placement;
    const ctx = this.ctx;
    ctx.save();
    ctx.lineWidth = 1;
    for (const c of this.candidates.items) {
      ctx.strokeStyle = c.dark ? IODINE : PAPER;
      ctx.globalAlpha = 0.75;
      ctx.beginPath();
      ctx.arc(ox + (c.x + sx) * scale, oy + (c.y + sy) * scale, Math.max(4, c.r * 1.5 * scale), 0, Math.PI * 2);
      ctx.stroke();
    }
    ctx.restore();
  }
}

function detailFor(obj: SceneObject): string {
  if (obj.arm) {
    if (!obj.label) return '';
    const conf = obj.confidence !== null ? `, ${Math.round(obj.confidence * 100)}% confident` : '';
    return `Answer ${obj.arm}${conf}`;
  }
  const size = sizeWords(obj.size);
  const words = statusWords(obj.status, obj.maybe ?? []);
  return [words, size].filter(Boolean).join(', ');
}

function perimeter(points: Point[]): number {
  let total = 0;
  for (let i = 0; i < points.length; i++) {
    const a = points[i];
    const b = points[(i + 1) % points.length];
    total += Math.hypot(b[0] - a[0], b[1] - a[1]);
  }
  return total;
}

function clamp01(v: number): number {
  return Math.max(0, Math.min(1, v));
}
