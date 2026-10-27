// The magnifier: a closer look at one object, in a panel floating over the dark surround
// beside the eyepiece, on the side away from its object and never over it unless the user put
// it there (the live view never shrinks or moves for it). Drag it by its bar to put it elsewhere,
// even over its object (remembered for the day; double-click the bar to put it back). Its state
// comes from the server (orchestrator/magnifier.py), because the brain's tools open and drive it; this file shows
// it and reports the dots, the level (wheel, pinch, + and -) and the close button back. Where
// it floats is decided by placeAway (panels.ts), clear of the other panels.

import { maxLevel } from './overlay';
import {
  coversObject, dragBy, droppedByUser, forObjectNow, movedByAgent, NOT_PLACED, panelWidth, PHONE_WIDTH, PICTURE_ASPECT, placeAway,
  type Box, type Panel, type PanelStack, type Placed, type Spot,
} from './panels';
import type { ClientCommand, MagnifierSource, MagnifierState } from './protocol';
import type { View } from './view';

const LOST_CLOSE_MS = 6000; // a live magnifier whose object stays out of view this long closes
const LOST_SAY_MS = 1500; // ...and after this long the server is told, so a cell can be shown on its captured view
const SEND_EVERY_MS = 80; // level updates while wheeling or pinching
const PENDING_MS = 1000; // the user's level waits this long for its echo before the server's wins
const MARGIN = 10; // CSS px kept from the stage's edges while dragged
const POS_KEY = 'magnifier-pos';

const fmt = (n: number) => (n < 10 ? n.toFixed(1).replace(/\.0$/, '') : String(Math.round(n)));

/** What it is showing: open or not, which object, which source. */
export function showing(state: MagnifierState | null): string {
  if (!state?.open) return '';
  const source = state.sources[state.index];
  return JSON.stringify([state.objectId, state.index, source?.kind ?? null, source?.url ?? null]);
}

/** The picture shown next to the close-up, both at once: only while the close-up (live or its sharpest frame)
 * is what it shows (the server's `beside`, orchestrator/magnifier.py). */
export function besideOf(state: MagnifierState | null): MagnifierSource | null {
  if (!state?.open || !state.beside?.url) return null;
  const source = state.sources[state.index];
  return source && (source.kind !== 'image' || source.url !== state.beside.url) ? state.beside : null;
}

/** What kind of picture it is, in a line shown on it (orchestrator/media.py; the server's words). A picture with
 * no record of how it was made is never called real (kind (f)). */
export function kindLine(source: MagnifierSource | null | undefined): string {
  if (!source || source.kind !== 'image') return '';
  const line = source.line || 'A picture; how it was made is not recorded';
  return (source.displayScale ?? 1) > 1 ? `${line} · enlarged pixels ×${fmt(source.displayScale!)}; edges may be cropped, no new detail` : line;
}

/** Tracking loss must not discard retained pictures or the user's comparison arrangement. */
export function keepsPictures(state: MagnifierState | null): boolean {
  return !!state?.open && state.sources.some((source) => source.kind === 'image' && !!source.url);
}

export class MagnifierPanel implements Panel {
  hasTemporaryHeldFrame(): boolean {
    const source = this.state?.open ? this.state.sources[this.state.index] : null;
    return source?.kind === 'held' && !source.resource;
  }
  retainedIdentity(): { id: string; day: string } | null {
    const resource = this.state?.open ? this.state.sources[this.state.index]?.resource : null;
    return resource ? { id: resource.id, day: resource.day } : null;
  }
  readonly id = 'magnifier';
  private readonly root: HTMLElement;
  private readonly picture: HTMLElement;
  private readonly bar: HTMLElement;
  private readonly live: HTMLCanvasElement;
  private readonly image: HTMLImageElement;
  private readonly kind: HTMLElement;
  private readonly besidePane: HTMLElement;
  private readonly besideImage: HTMLImageElement;
  private readonly besideKind: HTMLElement;
  private readonly caption: HTMLElement;
  private readonly dots: HTMLElement;
  private readonly credit: HTMLElement;
  private readonly zoomButtons: HTMLElement;
  private readonly less: HTMLButtonElement;
  private readonly more: HTMLButtonElement;
  private readonly view: View;
  private readonly panels: PanelStack;
  private readonly send: (command: ClientCommand) => void;
  private state: MagnifierState | null = null;
  private readonly imageLoads = new Map<HTMLImageElement, { url: string; retry: HTMLButtonElement; status: HTMLElement; attempts: number; settled: boolean }>();
  /** The user's own level (wheel, pinch, + and -), shown at once and kept until the server
   * echoes it, so echoes of earlier steps never pull the picture back while they zoom. */
  private pendingLevel: number | null = null;
  private unsent = false; // pendingLevel is waiting to be sent (at most every SEND_EVERY_MS)
  private sentAt = 0;
  private userPos: [number, number] | null = null; // where the user put it (stage px)
  private userWidth: number | null = null; // the width it was given (resized), kept until the bar is double-clicked
  private beforePresent: { pos: [number, number] | null; width: number | null; placed: Placed } | null = null;
  /** Who put it at userPos: the user's own drag is kept even over its object; the agent's move only when asked
   * for over the view (present's "over"). Kept until it is moved again (going live, G3). */
  private placed: Placed = NOT_PLACED; // over its object only as asked, and only that object (the review of G3, B2)
  private covering = false; // whether it covers its object now (the screen report says so)
  private lostSaid = false; // the server was told its object lost its place, this time
  /** Told when the user moves it or puts it back (the screen report: their change is theirs). */
  onUserChange: ((prop: string) => void) | null = null;
  private auto: { objectId: string | null; phone: boolean; panes: number; spot: Spot } | null = null;

  isVisible(): boolean {
    const bounds = this.root.getBoundingClientRect();
    return !this.root.hidden && this.root.dataset.resultTucked !== 'true' && bounds.width > 0 && bounds.height > 0;
  }

  isOpen(): boolean {
    return !!this.state?.open;
  }

  /** The outline it shows, or null. */
  objectId(): string | null {
    return this.current()?.kind === 'image' ? this.current()?.objectId ?? null : this.state?.objectId ?? null;
  }

  /** The level it shows now (x the fitted view), for the screen report; null when closed. */
  levelShown(): number | null {
    if (!this.state?.open) return null;
    // A picture is drawn as it is (only the live view and the held frame are enlarged): its level is 1 (re-check N4).
    if (this.current()?.kind === 'image') return 1;
    return this.pendingLevel ?? Math.max(1, this.state.zoom ?? 1);
  }

  /** The picture it shows now, for the screen report: a picture's URL, or "held" / "live". */
  source(): string | null {
    const s = this.state?.open ? this.state.sources?.[this.state.index] : undefined;
    return s ? (s.kind === 'image' ? s.url ?? null : s.kind) : null;
  }

  /** The picture it shows beside its close-up (its URL), for the screen report; null when none is drawn. */
  besideShown(): string | null {
    const beside = besideOf(this.state);
    return beside && !this.root.hidden && !this.besidePane.hidden ? beside.url ?? null : null;
  }

  /** Image pixels actually loaded and displayed, for completion confirmation. */
  picturesShown(): { source: string; scale: number }[] {
    if (this.root.hidden || !this.isVisible()) return [];
    const shown: { source: string; scale: number }[] = [];
    for (const [source, image] of [[this.current(), this.image], [besideOf(this.state), this.besideImage]] as const) {
      if (source?.kind === 'image' && source.url && image.complete && image.naturalWidth > 0
          && image.getAttribute('src') === source.url && !image.hidden && !image.parentElement?.hidden) {
        shown.push({ source: source.url, scale: source.displayScale ?? 1 });
      }
    }
    return shown;
  }

  /** How many pictures wide it is now: two while one is shown beside the close-up. */
  private panes(): number {
    return besideOf(this.state) ? 2 : 1;
  }

  close(): void {
    this.send({ type: 'magnifier', action: 'close' });
  }

  footprint(): DOMRect | null {
    return this.root.hidden ? null : this.root.getBoundingClientRect();
  }

  /** Each frame (panels.ts): the caption follows the live drawing, the panel keeps its place. */
  frame(): void {
    // A cell shown on its captured view because it lost its place: back to live once it is found again (re-check S1).
    if (this.lostSaid && this.state?.open && this.state.resumeLive && this.current()?.kind === 'image' && this.view.objectInView(this.state.objectId)) {
      this.lostSaid = false;
      const live = this.state.sources.findIndex((s) => s.kind === 'live');
      if (live >= 0) this.send({ type: 'magnifier', action: 'index', index: live });
    }
    if (this.state?.open && this.current()?.kind === 'live') {
      this.caption.textContent = this.liveCaption();
      // Its object has been out of view a while: fold the magnifier away rather than leave
      // an empty panel (ask again, or double-click it, to bring it back).
      const lost = this.view.magnifierLostSince;
      if (!lost) this.lostSaid = false;
      if (lost && !this.lostSaid && performance.now() - lost > LOST_SAY_MS && this.state.objectId) {
        this.lostSaid = true; // once per loss (M1.5c review S1)
        this.send({ type: 'magnifier', action: 'lost', id: this.state.objectId, why: this.view.magnifierLostWhy });
      }
      if (lost && performance.now() - lost > LOST_CLOSE_MS && !keepsPictures(this.state)) {
        this.view.magnifierLostSince = 0;
        this.send({ type: 'magnifier', action: 'close' });
      }
    }
    this.updateButtons();
    this.position();
  }

  constructor(view: View, panels: PanelStack, send: (command: ClientCommand) => void) {
    const el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
    this.root = el('magnifier');
    this.picture = el('magnifier-picture');
    this.bar = el('magnifier-bar');
    this.live = el<HTMLCanvasElement>('magnifier-live');
    this.image = el<HTMLImageElement>('magnifier-image');
    this.kind = el('magnifier-kind');
    this.besidePane = el('magnifier-beside');
    this.besideImage = el<HTMLImageElement>('magnifier-beside-image');
    this.besideKind = el('magnifier-beside-kind');
    this.caption = el('magnifier-caption');
    this.dots = el('magnifier-dots');
    this.credit = el('magnifier-credit');
    this.zoomButtons = el('magnifier-zoom');
    this.less = el<HTMLButtonElement>('magnifier-less');
    this.more = el<HTMLButtonElement>('magnifier-more');
    this.view = view;
    this.panels = panels;
    this.send = send;
    el<HTMLButtonElement>('magnifier-close').addEventListener('click', () => this.close());
    this.less.addEventListener('click', () => this.setLevel(this.level() / 2, true));
    this.more.addEventListener('click', () => this.setLevel(this.level() * 2, true));
    document.getElementById('magnifier-fit')?.addEventListener('click', () => this.setLevel(1, true));
    const controls = document.getElementById('magnifier-controls');
    controls?.addEventListener('pointerdown', (event) => event.stopPropagation());
    controls?.addEventListener('keydown', (event) => {
      if (event.key !== 'Escape' || !controls.hasAttribute('open')) return;
      event.preventDefault(); event.stopPropagation();
      controls.removeAttribute('open'); controls.querySelector('summary')?.focus();
    });
    this.zoomInput();
    this.draggable();
    this.resizable(el('magnifier-grip'));
  }

  /** Bigger or smaller by its corner grip: its width (the picture keeps its aspect), as the user's own. */
  private resizable(grip: HTMLElement): void {
    grip.addEventListener('pointerdown', (event) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = this.root.offsetWidth;
      grip.setPointerCapture(event.pointerId);
      const drag = new AbortController();
      grip.addEventListener('pointermove', (e) => {
        this.resizeTo(startWidth + (e.clientX - startX));
      }, { signal: drag.signal });
      const up = () => {
        drag.abort();
        this.onUserChange?.('rect');
      };
      grip.addEventListener('pointerup', up, { signal: drag.signal });
      grip.addEventListener('pointercancel', up, { signal: drag.signal });
    });
  }

  // --- the level ------------------------------------------------------------------------

  private level(): number {
    return this.view.magnifierLevel;
  }

  private zoomable(): boolean {
    const kind = this.current()?.kind;
    return !!this.state?.open && (kind === 'live' || kind === 'held');
  }

  /** The server's (orchestrator/magnifier.py max_level); the contract's ceiling until it says. */
  private maxLevel(): number {
    return this.state?.maxLevel ?? maxLevel(null);
  }

  /** Zoom to `value` here at once, and tell the server (the source of truth, so the brain
   * knows the level), at most every SEND_EVERY_MS while wheeling or pinching. */
  private setLevel(value: number, now = false): void {
    if (!this.zoomable()) return;
    const level = Math.min(this.maxLevel(), Math.max(1, value));
    this.pendingLevel = level;
    this.view.magnifierLevel = level;
    this.applyLevel();
    this.unsent = true;
    const wait = now ? 0 : SEND_EVERY_MS - (performance.now() - this.sentAt);
    if (wait <= 0) this.flush();
    else window.setTimeout(() => this.flush(), wait);
  }

  private flush(): void {
    if (!this.unsent || this.pendingLevel === null) return;
    this.send({ type: 'magnifier', action: 'level', level: Math.round(this.pendingLevel * 1000) / 1000 });
    this.unsent = false;
    this.sentAt = performance.now();
  }

  /** Wheel and trackpad pinch (ctrl+wheel in Chrome, gesture events in Safari) and a
   * two-finger pinch on touch screens, on the picture. */
  private zoomInput(): void {
    this.picture.addEventListener(
      'wheel',
      (event) => {
        if (!this.zoomable()) return;
        event.preventDefault();
        const dy = event.deltaMode === 1 ? event.deltaY * 16 : event.deltaY;
        this.setLevel(this.level() * Math.exp(-dy * (event.ctrlKey ? 0.01 : 0.0025)));
      },
      { passive: false },
    );
    let gestureStart = 1;
    type Gesture = Event & { scale: number };
    this.picture.addEventListener('gesturestart', (event) => {
      event.preventDefault();
      gestureStart = this.level();
    });
    this.picture.addEventListener('gesturechange', (event) => {
      event.preventDefault();
      this.setLevel(gestureStart * (event as Gesture).scale);
    });
    const touches = new Map<number, [number, number]>();
    let pinch: { d: number; level: number } | null = null;
    const spread = () => {
      const [a, b] = [...touches.values()];
      return Math.hypot(a[0] - b[0], a[1] - b[1]) || 1;
    };
    this.picture.addEventListener('pointerdown', (event) => {
      if (event.pointerType !== 'touch') return;
      touches.set(event.pointerId, [event.clientX, event.clientY]);
      if (touches.size === 2) pinch = { d: spread(), level: this.level() };
    });
    this.picture.addEventListener('pointermove', (event) => {
      if (!touches.has(event.pointerId)) return;
      touches.set(event.pointerId, [event.clientX, event.clientY]);
      if (pinch && touches.size === 2) this.setLevel(pinch.level * (spread() / pinch.d));
    });
    const lift = (event: PointerEvent) => {
      touches.delete(event.pointerId);
      if (touches.size < 2) pinch = null;
    };
    this.picture.addEventListener('pointerup', lift);
    this.picture.addEventListener('pointercancel', lift);
  }

  /** The held frame is a picture element: enlarge it the same way. */
  private applyLevel(): void {
    const source = this.current();
    const level = source?.kind === 'held' ? this.level() : source?.displayScale ?? 1;
    this.image.style.transform = level > 1 ? `scale(${level})` : '';
    // A standalone web photo has its own shape; the camera and paired views keep
    // their shared comparison frame. No crop is introduced to fill empty chrome.
    const natural = this.referenceAspect();
    this.image.style.aspectRatio = natural ? `${this.image.naturalWidth} / ${this.image.naturalHeight}` : '';
    this.image.style.height = natural ? 'auto' : '';
    this.image.toggleAttribute('data-pixelated', source?.kind === 'held' && this.view.magnifierPixelated);
  }

  private referenceAspect(): number | null {
    return this.current()?.media === 'reference' && this.panes() === 1
      && this.image.complete && this.image.naturalWidth > 0 && this.image.naturalHeight > 0
      ? this.image.naturalWidth / this.image.naturalHeight : null;
  }

  private updateButtons(): void {
    const show = this.zoomable();
    // Called every frame: written only when it changes, since even an unchanged write queues a mutation record that
    // the capability deck's observer answers with a refresh (about 69 a second at idle, b041).
    if (this.zoomButtons.hidden !== !show) this.zoomButtons.hidden = !show;
    if (!show) return;
    this.less.disabled = this.level() <= 1.001;
    this.more.disabled = this.level() >= this.maxLevel() - 0.001;
  }

  private liveCaption(): string {
    const zoom = this.view.magnifierZoom;
    if (!zoom) return 'Live';
    const level = this.level();
    const closer = level > 1.05 ? `×${fmt(level)} · ` : '';
    const pixels = this.view.magnifierPixelated ? ' · pixel-enlarged' : '';
    // A small panel (phones) keeps the caption short enough to read whole.
    if (this.root.offsetWidth < 280) return `Live ×${fmt(level > 1.05 ? level : zoom)}${pixels}`;
    return `Live, ${closer}digital zoom ×${fmt(zoom)}${pixels}`;
  }

  // --- where it floats --------------------------------------------------------------------

  /** Moved by its bar, kept inside the view, and remembered for the day. */
  private draggable(): void {
    try {
      const saved = JSON.parse(localStorage.getItem(POS_KEY) || 'null') as { x: number; y: number; day: string } | null;
      if (saved && saved.day === new Date().toDateString()) {
        this.userPos = [saved.x, saved.y];
        this.placed = NOT_PLACED; // the user's own drag earlier today: no object it was put over now
      }
    } catch {
      // not remembered
    }
    dragBy(this.bar, this.root, (x, y) => {
      this.userPos = [x, y];
      this.place(x, y);
    }, () => {
      if (!this.userPos) return;
      this.userPos = [this.root.offsetLeft, this.root.offsetTop];
      // The user's own place: kept over its object only if they dropped it over that object.
      const id = this.state?.objectId ?? null;
      this.placed = droppedByUser(id, coversObject(this.boxOnCanvas(), id ? this.view.objectCanvasBox(id) : null));
      this.onUserChange?.('rect');
      try {
        localStorage.setItem(POS_KEY, JSON.stringify({ x: this.userPos[0], y: this.userPos[1], day: new Date().toDateString() }));
      } catch {
        // not remembered
      }
    });
    // Double-click the bar: back to its own place beside the eyepiece.
    this.bar.addEventListener('dblclick', (event) => {
      if ((event.target as HTMLElement).closest('button, summary, details')) return;
      this.userPos = null;
      this.userWidth = null;
      this.placed = NOT_PLACED;
      this.auto = null;
      this.onUserChange?.('rect');
      try {
        localStorage.removeItem(POS_KEY);
      } catch {
        // nothing remembered
      }
    });
  }

  /** Before a move or resize the server asks for: kept, so one that did not show as asked is put back. */
  remember(): void {
    this.beforePresent = { pos: this.userPos ? [this.userPos[0], this.userPos[1]] : null, width: this.userWidth,
      placed: this.placed };
  }

  /** Back to where and how wide it was before the server's move or resize (it did not show as asked),
   * its own place beside the eyepiece included: nothing is pinned that the user did not pin. */
  restore(): void {
    if (!this.beforePresent) return;
    this.userPos = this.beforePresent.pos;
    this.userWidth = this.beforePresent.width;
    this.placed = this.beforePresent.placed;
    this.beforePresent = null;
  }

  /** Put it at (x, y) in workspace px, as a drag would (the present verb "move"); `over`: asked for there even
   * over its object, and kept there until it is moved again. */
  moveTo(x: number, y: number, over = false): void {
    const stage = (this.root.offsetParent as HTMLElement | null)?.getBoundingClientRect();
    if (!stage) return;
    this.userPos = [x - stage.left, y - stage.top];
    this.placed = movedByAgent(this.state?.objectId ?? null, over);
    this.place(this.userPos[0], this.userPos[1]);
  }

  /** Its box on the view's canvas (CSS px), or null while hidden. */
  private boxOnCanvas(): Box | null {
    if (this.root.hidden) return null;
    const canvas = this.view.canvas.getBoundingClientRect();
    const box = this.root.getBoundingClientRect();
    return [box.left - canvas.left, box.top - canvas.top, box.right - canvas.left, box.bottom - canvas.top];
  }

  /** Whether it covers its object now (the screen report's `covers`). */
  covers(): boolean {
    return !this.root.hidden && this.covering;
  }

  /** Make it this wide (CSS px; the present verb "resize"); the picture keeps its aspect. */
  resizeTo(width: number): void {
    // Never wider than the area it floats in (M1.5c review item 3); placeAway narrows it further to clear its object.
    const room = (this.root.offsetParent as HTMLElement | null)?.clientWidth ?? (typeof innerWidth === 'number' ? innerWidth : 1280);
    // Kept as one picture's width, so it is as wide again when it shows one picture or two.
    this.userWidth = Math.round(Math.max(160, Math.min(Math.round(width), Math.max(160, room - 16))) / this.panes());
  }

  private get dragging(): boolean {
    return this.root.dataset.dragging !== undefined;
  }

  /** Put it at (x, y) in stage px, kept inside the stage. */
  private place(x: number, y: number): void {
    const parent = this.root.offsetParent as HTMLElement | null;
    if (!parent) return;
    const maxX = Math.max(MARGIN, parent.clientWidth - this.root.offsetWidth - MARGIN);
    const maxY = Math.max(MARGIN, parent.clientHeight - this.root.offsetHeight - MARGIN);
    const left = `${Math.round(Math.min(maxX, Math.max(MARGIN, x)))}px`;
    const top = `${Math.round(Math.min(maxY, Math.max(MARGIN, y)))}px`;
    if (this.root.style.left !== left) this.root.style.left = left;
    if (this.root.style.top !== top) this.root.style.top = top;
    if (this.root.style.right !== 'auto') this.root.style.right = 'auto';
  }

  /** Keep the panel in its place and tell the view where it is (for the connector from the
   * object, and so margin notes on its side keep clear of it). */
  private position(): void {
    if (this.root.hidden) {
      this.view.magnifierAnchor = null;
      this.view.magnifierBox = null;
      return;
    }
    const canvas = this.view.canvas.getBoundingClientRect();
    const stage = (this.root.offsetParent as HTMLElement | null)?.getBoundingClientRect();
    if (!stage || !canvas.width) return;
    const ox = canvas.left - stage.left;
    const oy = canvas.top - stage.top;
    if (!this.dragging) {
      const spot = this.autoPlace(canvas, ox, oy);
      const widthPx = `${Math.round(spot.w)}px`; // the width asked for, as far as placeAway gives room (it covers its object only where put)
      if (this.root.style.width !== widthPx) this.root.style.width = widthPx;
      this.place(spot.x + ox, spot.y + oy);
    }
    const box = this.root.getBoundingClientRect();
    const left = box.left - canvas.left;
    const top = box.top - canvas.top;
    this.view.magnifierBox = [left, top, left + box.width, top + box.height];
    const id = this.state?.objectId;
    const object = id ? this.view.objectCanvasBox(id) : null;
    this.covering = coversObject(this.view.magnifierBox, object, 0);
    const at = id ? this.view.objectScreenPoint(id) : null;
    if (!at) {
      this.view.magnifierAnchor = null;
      return;
    }
    const x = at[0] - canvas.left;
    const y = at[1] - canvas.top;
    if (x < left) this.view.magnifierAnchor = [left - 2, top + box.height * 0.4];
    else if (x > left + box.width) this.view.magnifierAnchor = [left + box.width + 2, top + box.height * 0.4];
    else this.view.magnifierAnchor = [left + box.width / 2, y < top ? top - 2 : top + box.height + 2];
  }

  /** Where it goes now (canvas px), by placeAway: beside the eyepiece away from its object,
   * clear of the other panels and the labels, or where the user dragged it (even over the object),
   * or where the agent moved it (over the object only when asked for over the view). */
  private autoPlace(canvas: DOMRect, ox: number, oy: number): Spot {
    const area = this.view.viewArea();
    const phone = area.width < PHONE_WIDTH;
    const id = this.state?.objectId ?? null;
    const object = id ? this.view.objectCanvasBox(id) : null;
    const toCanvas = (r: { left: number; top: number; right: number; bottom: number }): Box =>
      [r.left - canvas.left, r.top - canvas.top, r.right - canvas.left, r.bottom - canvas.top];
    const panes = this.panes();
    const last = this.auto && this.auto.objectId === id && this.auto.phone === phone && this.auto.panes === panes ? this.auto.spot : null;
    const spot = placeAway({
      area,
      disc: this.view.discOnCanvas(),
      object,
      obstacles: this.panels.obstacles(this.id).map(toCanvas),
      labels: this.view.labels.map((l) => l.rect),
      // Two pictures side by side: twice as wide, as tall as one (each keeps the contract's aspect).
      size: { width: (this.userWidth ?? (phone ? Math.min(340, area.width - 24) / panes : panelWidth(area))) * panes, chrome: Math.max(28, this.root.offsetHeight - this.picture.offsetHeight), // bar (and credit)
        aspect: this.referenceAspect() ?? PICTURE_ASPECT * panes },
      kept: last,
      remembered: this.userPos ? { x: this.userPos[0] - ox, y: this.userPos[1] - oy, w: this.userWidth ? this.userWidth * panes : this.root.offsetWidth, placed: this.placed } : null, // the grip's width counts after a drag (review S2)
      objectId: id,
    });
    // Kept only once its object is on screen: before that there is no away side to keep to.
    if (spot.kind !== 'remembered') this.auto = !id || object ? { objectId: id, phone, panes, spot } : null;
    return spot;
  }

  // --- showing it -----------------------------------------------------------------------

  /** Open out of the object itself, so it reads as a closer look at that thing. */
  private growFrom(objectId: string): void {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    requestAnimationFrame(() => {
      const from = this.view.objectScreenPoint(objectId);
      const to = this.root.getBoundingClientRect();
      if (!from || !to.width) return;
      const dx = from[0] - (to.left + to.width / 2);
      const dy = from[1] - (to.top + to.height / 2);
      this.root.animate(
        [{ transform: `translate(${dx}px, ${dy}px) scale(0.12)`, opacity: 0.2, borderRadius: '50%' },
          { transform: 'none', opacity: 1, borderRadius: '14px' }],
        { duration: 340, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' },
      );
    });
  }

  zoom(objectId: string): void {
    this.send({ type: 'magnifier', action: 'zoom', id: objectId });
  }

  private current(): MagnifierSource | undefined {
    return this.state?.sources[this.state.index];
  }

  render(state: MagnifierState): void {
    const wasHidden = this.root.hidden;
    const previous = this.state?.objectId;
    // Another object or source (or reopened): how long the last one was out of view says
    // nothing about this one, so it must not fold away at once.
    if (showing(this.state) !== showing(state)) this.view.magnifierLostSince = 0;
    this.state = state;
    // Another object: a place put over the last one does not cover this one (the review of G3, B2).
    if (previous !== state.objectId) this.placed = forObjectNow(this.placed, state.objectId ?? null);
    this.root.hidden = !state.open || !state.objectId && !state.sources.length;
    if (!this.root.hidden && (wasHidden || previous !== state.objectId)) {
      // Opening, or moving to another object: straight to its place (no glide from the last one).
      this.auto = null;
      this.root.dataset.instant = '';
      this.position();
      requestAnimationFrame(() => delete this.root.dataset.instant);
      if (state.objectId) this.growFrom(state.objectId);
    }
    // The server's level, unless the user's own is still on its way there: that is kept until
    // the server echoes it (as clamped there), the magnifier moves to another object, or a
    // second has passed since it was sent (the voice or chat set another level meanwhile).
    const level = Math.max(1, state.zoom ?? 1);
    const settled = !this.unsent && (Math.abs(level - Math.min(this.pendingLevel ?? 0, this.maxLevel())) < 0.002
      || performance.now() - this.sentAt > PENDING_MS);
    if (this.pendingLevel !== null && (state.objectId !== previous || !state.open || settled)) {
      this.pendingLevel = null;
      this.unsent = false;
    }
    this.view.magnifierLevel = this.pendingLevel ?? level;
    this.view.magnifierPixelated = !!state.pixelated;
    this.view.magnifierCenter = state.center ?? null;
    const source = this.current();
    if (this.root.hidden || !source) {
      this.view.magnifier = null;
      this.view.magnifierBox = null;
      this.view.magnifierAnchor = null;
      return;
    }
    const isLive = source.kind === 'live';
    this.live.hidden = !isLive;
    this.image.hidden = isLive;
    this.view.magnifier = isLive && state.objectId ? { canvas: this.live, objectId: state.objectId } : null;
    if (!isLive && source.url) this.loadImage(this.image, source);
    for (const [image, load] of this.imageLoads) {
      if (image === this.image && isLive || image === this.besideImage && !besideOf(state)) load.status.hidden = true;
    }
    this.applyLevel();
    if (source.kind === 'held') {
      const level = this.view.magnifierLevel;
      this.caption.textContent = `Sharpest recent frame, full resolution${level > 1.05 ? ` · ×${fmt(level)}` : ''}`;
    } else if (!isLive) {
      this.caption.textContent = source.label;
    }
    // What kind of picture it is, on it (kind (f)): a reference photo is not this slide, an AI illustration not the specimen.
    const kind = kindLine(source);
    this.kind.hidden = !kind;
    this.kind.textContent = source.media === 'reference' ? 'Reference photo' : kind;
    this.kind.title = kind;
    this.root.dataset.reference = String(source.media === 'reference');
    if (source.media === 'reference') { this.caption.textContent = `${source.label} · ${source.credit || 'Reference photo'}`; this.caption.title = [source.label, kind, source.credit].filter(Boolean).join('\n'); }
    const beside = besideOf(state);
    this.besidePane.hidden = !beside;
    this.root.toggleAttribute('data-beside', !!beside);
    if (beside?.url) this.loadImage(this.besideImage, beside);
    this.besideKind.textContent = beside ? kindLine(beside) : '';
    this.besideImage.style.transform = (beside?.displayScale ?? 1) > 1 ? `scale(${beside!.displayScale})` : '';
    const credit = beside ? beside.credit : source.credit;
    this.credit.hidden = !credit;
    this.credit.textContent = credit ? `${beside ? 'Beside it' : 'Source'}: ${credit}` : '';
    this.dots.replaceChildren(
      ...state.sources.map((s, i) => {
        const dot = document.createElement('button');
        dot.type = 'button';
        dot.setAttribute('role', 'tab');
        dot.setAttribute('aria-selected', String(i === state.index));
        dot.setAttribute('aria-label', s.label);
        dot.title = s.label;
        dot.addEventListener('click', () => this.send({ type: 'magnifier', action: 'index', index: i }));
        return dot;
      }),
    );
  }

  /** Reload only these retained bytes, once per source; never repeat acquisition or search. */
  private loadImage(image: HTMLImageElement, source: MagnifierSource): void {
    if (!source.url) return;
    const previous = this.imageLoads.get(image);
    if (previous?.url === source.url) { previous.status.hidden = previous.settled; return; }
    previous?.status.remove();
    const status = document.createElement('div');
    status.className = 'magnifier-image-status';
    status.setAttribute('role', 'status');
    const message = document.createElement('span');
    const retry = document.createElement('button');
    retry.type = 'button'; retry.textContent = 'Reload image'; retry.className = 'mm-control'; retry.hidden = true;
    status.append(message, retry); image.parentElement?.append(status);
    const load = { url: source.url, retry, status, attempts: 0, settled: false };
    this.imageLoads.set(image, load);
    const start = () => {
      load.settled = false; status.hidden = false; message.textContent = 'Loading image…'; retry.hidden = true;
      image.onload = () => {
        if (this.imageLoads.get(image) !== load) return;
        load.settled = true; status.hidden = true; this.applyLevel();
      };
      image.onerror = () => {
        if (this.imageLoads.get(image) !== load) return;
        status.hidden = image.hidden || !!image.parentElement?.hidden;
        message.textContent = load.attempts ? 'Image unavailable. Its saved source is unchanged.' : 'This image could not load.';
        retry.hidden = load.attempts > 0;
      };
      image.src = load.url;
    };
    retry.addEventListener('click', () => {
      if (load.attempts || this.imageLoads.get(image) !== load) return;
      load.attempts++;
      image.removeAttribute('src'); start();
    });
    image.alt = source.label;
    start();
  }

  /** The object it shows was removed or the view went away. */
  objectGone(ids: Set<string>): void {
    if (this.state?.open && this.state.objectId && !ids.has(this.state.objectId) && this.current()?.kind === 'live') {
      this.view.magnifier = null;
    }
  }
}
