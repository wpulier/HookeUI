// Drive: the partner acting in Hooke, seen (the live-partner spec, 4.2 Hands and decision 2). Before each intent runs,
// a partner cursor drawn inside Hooke's window moves to its control or target (by the same ids as marks), with a short
// caption of what it is about to do, or rings the target. It never moves the Mac's pointer and never takes a click: it is
// feedback for the user, not the mechanism (the intent runs through Hooke's own operations on the server).
//
// Takeover: while the partner is acting (the server's `control` says owner "partner"), the user's own pointer moving
// over the window or Escape take control back at once. In the same task as that input the cursor stops and fades and the
// server is told (`takeover`, with the control version the studio last saw); the server raises the version, drops
// everything queued and stops what it was doing where it can. The talk key is a pause, not a takeover (the spec, 4.2:
// "interrupt to ask something"): the server is told the same way (`why: "talk"`) and nothing queued runs, but the action
// being done finishes, so the cursor stays on it until its `drive-end`. Its acknowledgement says when it knew, for the
// Drive line (input to stopped and known within 150 ms).
//
// Motion is a transform on one fixed layer, eased over MOVE_MS: the live view's frames are never waited on.
import type { ControlMessage, DriveEnd, DriveIntent, DriveMessage, DriveOutcome, MarkSpec, PartnerCommand } from './protocol';
import type { FrameAnchor, MarkHooks, Place, Point, Rect } from './marks';
import { resolveTarget } from './marks';

export const MOVE_MS = 320;
export const FADE_MS = 220;
/** Pointer movement over the window less than this (px) is a hand resting on the mouse, not a takeover. */
export const MOVE_PX = 3;

/** Where the cursor's tip points on a target: a control's middle; a large thing (a window, a panel) near its title bar. */
export function aimAt(rect: Rect): Point {
  if (rect[3] > 120 || rect[2] > 360) return [rect[0] + Math.min(56, rect[2] / 2), rect[1] + Math.min(16, rect[3] / 2)];
  return [rect[0] + rect[2] / 2, rect[1] + rect[3] / 2];
}

/** Things on the picture are ringed; windows, Tabs and controls get the cursor (the server's rule when it sends none). */
export function defaultStyle(target: string): 'cursor' | 'ring' {
  return /^(outline|member|population|layer):/.test(target) || target === 'frame' ? 'ring' : 'cursor';
}

const ease = (t: number) => { const k = Math.min(1, Math.max(0, t)); return k < 0.5 ? 4 * k * k * k : 1 - Math.pow(-2 * k + 2, 3) / 2; };

/** What the painter draws now. */
export interface CursorState {
  visible: boolean; at: Point; caption: string; style: 'cursor' | 'ring'; ring: Rect | null; opacity: number;
  outcome: DriveOutcome | null; intent: string | null;
}

interface Move { id: string; spec: MarkSpec; style: 'cursor' | 'ring'; caption: string; kind: string; anchor: FrameAnchor | null;
  from: Point; started: number; arrived: boolean }

export interface CursorOptions {
  hooks: MarkHooks;
  paint: (state: CursorState) => void;
  reply: (command: PartnerCommand) => void;
  now?: () => number;
  frame?: (fn: () => void) => unknown;
  still?: boolean; // reduced motion: it jumps, and appears and goes without fading
}

/** The partner cursor: one at a time, following its target each frame (an outline moves with the slide). */
export class PartnerCursor {
  private move: Move | null = null;
  private at: Point | null = null;
  private fading: { from: number; outcome: DriveOutcome | null } | null = null;
  private looping = false;
  private readonly options: CursorOptions;
  private readonly now: () => number;

  constructor(options: CursorOptions) {
    this.options = options;
    this.now = options.now ?? (() => performance.now());
  }

  /** The intent the cursor is on now, if any. */
  get current(): string | null { return this.move?.id ?? null; }

  /** Move to the intent's target (or ring it); `drive-shown` once there, or at once when the target is not found. */
  show(message: DriveMessage): void {
    const intent = message.intent ?? ({} as DriveIntent);
    const spec: MarkSpec = { target: String(intent.target ?? ''), region: intent.region, polygon: intent.polygon };
    const anchor = spec.target === 'frame' ? this.options.hooks.anchor(message.frame) : null;
    const place = resolveTarget(spec, this.options.hooks, anchor);
    if (!('rect' in place)) {
      this.options.reply({ type: 'drive-shown', id: String(message.id), ok: false, why: place.why });
      return;
    }
    const now = this.now(), aim = aimAt(place.rect);
    const [w, h] = this.options.hooks.viewport();
    // From where it is; else it comes in from the side with room, a short way off.
    const from: Point = this.at && !this.fading ? this.at
      : [aim[0] + (aim[0] > w / 2 ? -90 : 90), aim[1] + (aim[1] > h / 2 ? -60 : 60)];
    this.fading = null;
    this.move = { id: String(message.id), spec, anchor, from, started: now, arrived: false,
      style: intent.style === 'ring' || intent.style === 'cursor' ? intent.style : defaultStyle(spec.target), caption: String(intent.caption ?? '').slice(0, 60), kind: String(intent.kind ?? '') };
    if (this.options.still) this.arrive(place.rect);
    this.paint();
  }

  /** The intent ended: a moment to show how, then it fades. */
  end(message: DriveEnd): void {
    if (!this.move || this.move.id !== String(message.id)) return;
    this.fade(message.outcome ?? null);
  }

  /** Takeover: it stops where it is and fades, now. */
  stop(): void {
    if (!this.move && !this.fading) return;
    this.fade(null);
  }

  private fade(outcome: DriveOutcome | null): void {
    this.fading = { from: this.now(), outcome };
    this.move = null;
    this.paint();
  }

  private arrive(rect: Rect): void {
    const move = this.move!;
    move.arrived = true;
    this.options.reply({ type: 'drive-shown', id: move.id, ok: true, rect: rect.map((v) => Math.round(v)) as Rect });
  }

  /** One frame: the target found again, the cursor eased toward it, the fade. */
  paint(): void {
    const now = this.now(), opts = this.options;
    const empty = (opacity: number, outcome: DriveOutcome | null): CursorState =>
      ({ visible: opacity > 0, at: this.at ?? [0, 0], caption: '', style: 'cursor', ring: null, opacity, outcome, intent: null });
    if (this.fading) {
      const t = opts.still ? 1 : (now - this.fading.from) / FADE_MS;
      const outcome = this.fading.outcome;
      // A finished intent keeps its tick or cross a moment before fading; a takeover fades at once.
      const hold = outcome && !opts.still ? 260 : 0;
      const k = Math.max(0, Math.min(1, (now - this.fading.from - hold) / FADE_MS));
      opts.paint(empty(t >= 1 && k >= 1 ? 0 : 1 - k, outcome));
      if (k >= 1) { this.fading = null; this.at = null; return; }
      this.loop();
      return;
    }
    const move = this.move;
    if (!move) { opts.paint(empty(0, null)); return; }
    const place: Place = resolveTarget(move.spec, opts.hooks, move.anchor);
    if (!('rect' in place)) { opts.paint({ ...empty(0, null), intent: move.kind }); this.loop(); return; } // out of view for now
    const aim = aimAt(place.rect);
    const t = opts.still ? 1 : (now - move.started) / MOVE_MS;
    const k = ease(t);
    this.at = [move.from[0] + (aim[0] - move.from[0]) * k, move.from[1] + (aim[1] - move.from[1]) * k];
    if (t >= 1 && !move.arrived) this.arrive(place.rect);
    opts.paint({ visible: true, at: this.at, caption: move.caption, style: move.style, ring: move.style === 'ring' || t >= 1 ? place.rect : null,
      opacity: opts.still ? 1 : Math.min(1, (now - move.started) / 120), outcome: null, intent: move.kind });
    this.loop();
  }

  private loop(): void {
    if (this.looping || !this.options.frame) return;
    this.looping = true;
    this.options.frame(() => { this.looping = false; this.paint(); });
  }
}

type Listening = Pick<Window, 'addEventListener' | 'removeEventListener'>;
type PointerLike = { isTrusted?: boolean; clientX: number; clientY: number; movementX?: number; movementY?: number; pointerType?: string };

/** Takeover: the user's input while the partner acts takes control back, in the same task. */
export class Takeover {
  /** The control version the studio last saw, and who has control. */
  version = 0;
  owner: 'partner' | 'user' = 'user';
  /** The last takeover's input time and when the server said it knew (performance.now ms): the Drive line. */
  last: { why: string; inputAt: number; stoppedAt: number; sentAt: number; knownAt: number | null } | null = null;
  private start: { x: number; y: number } | null = null;
  private readonly options: { cursor: Pick<PartnerCursor, 'stop'>; send: (command: PartnerCommand) => boolean; now?: () => number;
    trusted?: (event: PointerLike) => boolean };
  private readonly now: () => number;

  constructor(options: Takeover['options']) {
    this.options = options;
    this.now = options.now ?? (() => performance.now());
  }

  get acting(): boolean { return this.owner === 'partner'; }

  /** The server's control state (and its acknowledgement of a takeover). */
  control(message: ControlMessage): void {
    if (!Number.isFinite(message.version)) return;
    if (message.ack && this.last && this.last.knownAt === null) this.last.knownAt = this.now();
    if (message.version < this.version) return; // an older state, overtaken
    this.version = message.version;
    this.owner = message.owner === 'partner' ? 'partner' : 'user';
    this.start = null;
  }

  /** Listen on the window (capture phase): the pointer moving and Escape. The talk key comes from TalkHold (input). */
  attach(win: Listening): () => void {
    const moved = (event: Event) => this.pointer(event as unknown as PointerLike, (event as Event).timeStamp);
    const key = (event: Event) => {
      const e = event as KeyboardEvent;
      if (e.key !== 'Escape' || e.repeat || !this.acting) return;
      e.preventDefault(); e.stopPropagation(); // it stops the partner; it closes nothing else
      this.input('escape', e.timeStamp);
    };
    win.addEventListener('pointermove', moved, true);
    win.addEventListener('keydown', key, true);
    return () => { win.removeEventListener('pointermove', moved, true); win.removeEventListener('keydown', key, true); };
  }

  pointer(event: PointerLike, at?: number): void {
    // Only the user's own pointer: an event a script made is not a hand on the mouse.
    if (!this.acting || !(this.options.trusted ?? ((e: PointerLike) => e.isTrusted !== false))(event)) return;
    // The first event's own movement counts too: one quick move of the mouse is a single event.
    const own = Math.hypot(event.movementX ?? 0, event.movementY ?? 0);
    if (!this.start) { this.start = { x: event.clientX, y: event.clientY }; if (own < MOVE_PX) return; }
    else if (Math.hypot(event.clientX - this.start.x, event.clientY - this.start.y) < MOVE_PX && own < MOVE_PX) return;
    this.input('mouse', at);
  }

  /** The user's input while the partner acts: the server is told, now. The mouse and Escape stop the cursor at once;
   * the talk key pauses (the action being done finishes, and its own end fades the cursor). */
  input(why: 'mouse' | 'escape' | 'talk', inputAt = this.now()): boolean {
    if (!this.acting) return false;
    this.owner = 'user';
    this.start = null;
    if (why !== 'talk') this.options.cursor.stop();
    const stoppedAt = this.now();
    this.options.send({ type: 'takeover', version: this.version, why, at: Date.now() });
    this.last = { why, inputAt, stoppedAt, sentAt: this.now(), knownAt: null };
    return true;
  }
}

/** The cursor as one fixed layer: a pointer, its caption, and a ring, moved by transforms only. */
export class CursorPainter {
  readonly root: HTMLElement;
  private readonly pointer: HTMLElement;
  private readonly caption: HTMLElement;
  private readonly ring: HTMLElement;
  private readonly badge: HTMLElement;

  constructor(doc: Document = document) {
    this.root = doc.createElement('div');
    this.root.id = 'partner-cursor';
    this.root.className = 'partner-cursor';
    this.root.setAttribute('aria-hidden', 'true');
    this.root.hidden = true; // nothing is drawn until a cue asks for it
    this.ring = Object.assign(doc.createElement('div'), { className: 'partner-cursor-ring' });
    this.pointer = Object.assign(doc.createElement('div'), { className: 'partner-cursor-pointer' });
    this.pointer.innerHTML = '<svg viewBox="0 0 24 24" width="24" height="24"><path d="M3 2 L3 19 L8 14.5 L11.5 22 L14.5 20.6 L11 13.4 L17.5 13.4 Z"/></svg>';
    this.badge = Object.assign(doc.createElement('span'), { className: 'partner-cursor-badge' });
    this.caption = Object.assign(doc.createElement('span'), { className: 'partner-cursor-caption' });
    this.pointer.append(this.badge, this.caption);
    this.root.append(this.ring, this.pointer);
    doc.body.append(this.root);
  }

  paint(state: CursorState): void {
    this.root.hidden = !state.visible;
    this.root.style.opacity = state.opacity.toFixed(3);
    this.root.dataset.intent = state.intent ?? '';
    this.root.dataset.outcome = state.outcome ?? '';
    if (!state.visible) return;
    this.pointer.style.transform = `translate(${state.at[0].toFixed(1)}px, ${state.at[1].toFixed(1)}px)`;
    this.pointer.dataset.at = state.at.map((v) => Math.round(v)).join(',');
    this.pointer.hidden = state.style === 'ring' && !!state.ring;
    this.caption.textContent = state.caption;
    this.caption.hidden = !state.caption;
    this.badge.textContent = state.outcome === 'verified' ? '✓' : state.outcome === 'failed' ? '✕' : state.outcome === 'uncertain' ? '?' : '';
    this.badge.hidden = !this.badge.textContent;
    const ring = state.ring;
    this.ring.hidden = !ring;
    if (ring) {
      const pad = 6;
      this.ring.style.transform = `translate(${(ring[0] - pad).toFixed(1)}px, ${(ring[1] - pad).toFixed(1)}px)`;
      this.ring.style.width = `${(ring[2] + pad * 2).toFixed(1)}px`;
      this.ring.style.height = `${(ring[3] + pad * 2).toFixed(1)}px`;
      this.ring.dataset.rect = ring.map((v) => Math.round(v)).join(',');
    }
  }
}
