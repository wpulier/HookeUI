// Everything that floats over or beside the live view, in one place: which panel is on top (so
// Escape closes exactly one per press), where each one is (so the magnifier keeps clear of the
// others), and the room they take from the view (the eyepiece glides aside for the side column
// and the shelf, never for the floating magnifier or details card). placeAway, where the
// magnifier floats, is pure, so it is unit tested (panels.test.ts).

import contract from '../../../contract/overlay.json';

/** Client px. A DOMRect is one; tests pass plain objects. */
export interface Rect {
  left: number;
  top: number;
  right: number;
  bottom: number;
}

/** Measured usable host area, in layout-viewport CSS pixels (as DOMRect reports). */
export function windowPlacementArea(host: Rect, visual: readonly number[]): [number, number, number, number] | null {
  const [x, y, width, height] = visual;
  if (![host.left, host.top, host.right, host.bottom, x, y, width, height].every(Number.isFinite)) return null;
  const left = Math.max(host.left, x), top = Math.max(host.top, y);
  const right = Math.min(host.right, x + width), bottom = Math.min(host.bottom, y + height);
  return right > left && bottom > top ? [left, top, right - left, bottom - top] : null;
}

export type Box = [number, number, number, number]; // x0, y0, x1, y1 (canvas CSS px)

/** Room taken from the view (CSS px): the side column on the right, the bottom sheet on
 * phones, the shelf's strip along the bottom, and the sessions rail on the left. */
export interface Reserved {
  right: number;
  bottom: number;
  shelf: number;
  /** The side bar, open: the eyepiece glides right of it (sidebar.ts). */
  left: number;
  /** What runs along the left edge, open or closed (the rail's strip): margin notes, new windows and the stage's
   * corners keep right of it; the eyepiece moves only for `left`. */
  edge: number;
}

export interface Panel {
  readonly id: string;
  isOpen(): boolean;
  /** A retained phone result may be tucked without being closed. */
  isVisible?(): boolean;
  close(): void;
  /** Where it is on screen while open (null when closed, or when it floats over everything
   * for a moment, like a menu, and nothing should move for it). */
  footprint(): Rect | null;
  /** Room it takes from the view (asked every frame, open or not). */
  reserves?(stage: Rect): Partial<Reserved>;
  /** Once per animation frame, from the stack's one loop: keep its place. */
  frame?(): void;
  /** Escape closes it even from a text field: the fields are its own (menus, dialogs). */
  readonly whileTyping?: boolean;
  /** Visual surface level: menus/dialogs stay above late-created result windows. */
  readonly escapePriority?: number;
  /** Raising changes order only within this group; dialogs keep their own order. */
  readonly group?: string;
  /** Actual group order, for a shell's bounded CSS stacking layer. */
  setStackOrder?(order: number): void;
  /** Its title row, where Close is (client px; null when closed). */
  titleRow?(): Rect | null;
}

export interface EscapeEvent {
  key: string;
  target: EventTarget | null;
  defaultPrevented: boolean;
  preventDefault(): void;
}

const typing = (target: EventTarget | null) =>
  !!(target as Element | null)?.closest?.('input, textarea, select, [contenteditable]:not([contenteditable="false"])');

export class PanelStack {
  private readonly panels: Panel[] = [];
  private readonly background = new WeakSet<Panel>();
  private readonly fixtures: ((stage: Rect) => Partial<Reserved>)[] = [];
  /** Told which panel the user closed with Escape (the screen report: their change is theirs). */
  closedByUser: ((id: string) => void) | null = null;

  /** Bottom first: the last registered is on top, the first one Escape closes. */
  register<P extends Panel>(panel: P, options: {background?: boolean} = {}): P {
    if(options.background){
      // Restore bottom-to-top without covering an explicitly selected window.
      // Unshift reversed the saved sequence, burying the latest output under its inputs.
      const foreground = this.panels.findIndex(item => !this.background.has(item));
      this.panels.splice(foreground < 0 ? this.panels.length : foreground, 0, panel);
      this.background.add(panel);
      if(panel.group)this.panels.filter(item=>item.group===panel.group).forEach((item,index)=>item.setStackOrder?.(index));
    }else this.panels.push(panel);
    return panel;
  }

  unregister(panel: Panel): void {
    const at = this.panels.indexOf(panel);
    if (at >= 0) this.panels.splice(at, 1);
  }

  raise(id: string): void {
    const at = this.panels.findIndex((panel) => panel.id === id);
    const group = this.panels[at]?.group;
    if (at < 0 || !group) return;
    // Reorder the group's slots, leaving interleaved modal/nonwindow slots intact.
    const slots = this.panels.map((panel, i) => panel.group === group ? i : -1).filter((i) => i >= 0);
    const members = slots.map((i) => this.panels[i]);
    const local = members.findIndex((panel) => panel.id === id);
    const [raised] = members.splice(local, 1); members.push(raised);
    this.background.delete(raised);
    slots.forEach((slot, i) => { this.panels[slot] = members[i]; members[i].setStackOrder?.(i); });
  }

  /** Something that takes room from the view but is not a panel Escape closes (the shelf). */
  reserve(fixture: (stage: Rect) => Partial<Reserved>): void {
    this.fixtures.push(fixture);
  }

  /** The open panel on top, or null. */
  top(): Panel | null {
    let top: Panel | null = null;
    for (const panel of this.panels) {
      if (panel.isOpen() && panel.isVisible?.() !== false && (!top || (panel.escapePriority ?? 0) >= (top.escapePriority ?? 0))) top = panel;
    }
    return top;
  }

  /** Escape closes the panel on top, one per press. A text field keeps its Escape unless the
   * panel on top owns its fields. True when it closed something. */
  onEscape(event: EscapeEvent): boolean {
    if (event.key !== 'Escape' || event.defaultPrevented) return false;
    const top = this.top();
    if (!top || (typing(event.target) && !top.whileTyping)) return false;
    top.close();
    this.closedByUser?.(top.id);
    event.preventDefault();
    return true;
  }

  /** The other open windows, their title rows (client px) and whether each is in front of `except`: a newly placed
   * window keeps clear of them. */
  windows(except?: string): { box: Rect; title: Rect; front: boolean }[] {
    const rows: { box: Rect; title: Rect; front: boolean }[] = [];
    const at = this.panels.findIndex((panel) => panel.id === except);
    this.panels.forEach((panel, index) => {
      if (panel.id === except || panel.group !== 'window' || !panel.isOpen() || panel.isVisible?.() === false) return;
      const box = panel.footprint(), title = panel.titleRow?.();
      if (box && title && title.right > title.left && title.bottom > title.top) rows.push({ box, title, front: at >= 0 && index > at });
    });
    return rows;
  }

  /** Where the other open panels are (client px). */
  obstacles(except?: string): Rect[] {
    const rects: Rect[] = [];
    for (const panel of this.panels) {
      if (panel.id === except || !panel.isOpen() || panel.isVisible?.() === false) continue;
      const rect = panel.footprint();
      if (rect && rect.right > rect.left && rect.bottom > rect.top) rects.push(rect);
    }
    return rects;
  }

  /** The room the view keeps clear of: the most any panel or fixture asks for on each edge. */
  reserved(stage: Rect): Reserved {
    const room: Reserved = { right: 0, bottom: 0, shelf: 0, left: 0, edge: 0 };
    const asks = [...this.panels.map((p) => p.reserves?.(stage) ?? {}), ...this.fixtures.map((f) => f(stage))];
    for (const ask of asks) {
      room.right = Math.max(room.right, ask.right ?? 0);
      room.bottom = Math.max(room.bottom, ask.bottom ?? 0);
      room.shelf = Math.max(room.shelf, ask.shelf ?? 0);
      room.left = Math.max(room.left, ask.left ?? 0);
      room.edge = Math.max(room.edge, ask.edge ?? 0, ask.left ?? 0);
    }
    return room;
  }

  /** The one Escape listener and the one placement loop: each frame the room is worked out
   * and handed on (to the view), then every panel keeps its place. */
  start(stage: HTMLElement, onReserved: (room: Reserved) => void): void {
    document.addEventListener('keydown', (event) => this.onEscape(event));
    const tick = () => {
      requestAnimationFrame(tick); // first: one panel that throws must not stop placement for good
      onReserved(this.reserved(stage.getBoundingClientRect()));
      for (const panel of [...this.panels]) {
        try {
          panel.frame?.();
        } catch (error) {
          console.error(`panel ${panel.id}:`, error);
        }
      }
    };
    requestAnimationFrame(tick);
  }
}

/** Move `root` by dragging `handle` (not from its buttons). `move` gets where its top-left goes,
 * in its offset parent's px; `end` runs once when it is let go or the drag is cancelled. Every
 * listener of a drag goes with it. */
export function dragBy(handle: HTMLElement, root: HTMLElement, move: (x: number, y: number) => void, end: () => void): void {
  handle.addEventListener('pointerdown', (event) => {
    if ((event.target as HTMLElement).closest('button')) return;
    const stage = (root.offsetParent as HTMLElement | null)?.getBoundingClientRect();
    if (!stage) return;
    const box = root.getBoundingClientRect();
    const dx = event.clientX - box.left;
    const dy = event.clientY - box.top;
    handle.setPointerCapture(event.pointerId);
    root.dataset.dragging = '';
    const drag = new AbortController();
    const { signal } = drag;
    handle.addEventListener('pointermove', (e) => move(e.clientX - dx - stage.left, e.clientY - dy - stage.top), { signal });
    const up = () => {
      drag.abort();
      delete root.dataset.dragging;
      end();
    };
    handle.addEventListener('pointerup', up, { signal });
    handle.addEventListener('pointercancel', up, { signal });
  });
}

// --- where the magnifier floats -------------------------------------------------------------

const MARGIN = 10; // CSS px kept from the view's edges
const GAP = 12; // CSS px between the panel and the eyepiece
const MIN_WIDTH = 240; // CSS px: narrower than this it does not go beside the eyepiece
const PAD = 14; // CSS px kept clear around its object
/** Narrower than this the view is a phone's: the magnifier floats top right. */
export const PHONE_WIDTH = 720;
// The widest panel is the one the server works out "pixel-enlarged" for (contract panelCssPx).
const PANEL_PX = contract.magnifier.panelCssPx;
const [ASPECT_W, ASPECT_H] = contract.magnifier.aspect;
export const PICTURE_ASPECT = ASPECT_W / ASPECT_H; // the magnifier's picture, width over height

export interface Spot {
  x: number;
  y: number;
  w: number; // its width there (it narrows to fit beside the eyepiece)
  /** remembered: where the user dragged it or someone moved it; kept: where it already was; beside: over
   * the surround beside the eyepiece; corner: no room there. */
  kind: 'remembered' | 'kept' | 'beside' | 'corner';
}

export interface Placing {
  /** The canvas px the view keeps: x from `left` (0 when not given: the sessions rail's edge, panels.Reserved) to
   * `width`, y from 0 to `height`. */
  area: { width: number; height: number; left?: number };
  disc: [number, number, number] | null; // the eyepiece on the canvas: centre and radius
  object: Box | null; // the thing it shows, while on screen
  obstacles: Box[]; // other panels (the details card): never covered
  labels?: Box[]; // margin notes: avoided when it moves; when it stays they make way (view.ts)
  // Its usual width, its bar under the picture, and its picture's width over height (two pictures side by side: twice
  // the contract's aspect; the contract's when not given).
  size: { width: number; chrome: number; aspect?: number };
  kept?: Spot | null; // where it is now: it stays while that still fits
  /** Where it was put, as wide as it was, and who put it there for which object (`placed`): kept over its object
   * only while it shows the object it was put over (keptOverIt). */
  remembered?: { x: number; y: number; w: number; placed?: Placed } | null;
  objectId?: string | null; // the object it shows now
}

/** Who put the magnifier where, for which object, and whether that place was asked for over it (going live, G3):
 * the user's own drop over its object is the request; the agent's move only when asked for over the view (present's
 * "over"). A place covers only the object it was put over: another object, or a drop made clear of it, keeps clear
 * (the review of G3, B2). */
export interface Placed {
  by: 'user' | 'agent';
  over: boolean; // the agent's move, asked for over the view
  forObject: string | null; // the object it showed when it was put there
  coversAtDrop: boolean; // the user's drop itself covered that object
}

/** Nobody put it anywhere, or a drag remembered from earlier today (no object to have been put over). */
export const NOT_PLACED: Placed = { by: 'user', over: false, forObject: null, coversAtDrop: false };

/** The user dropped it (a drag): over its object only if the drop itself covered it. */
export function droppedByUser(objectId: string | null, covers: boolean): Placed {
  return { by: 'user', over: false, forObject: objectId, coversAtDrop: covers };
}

/** The agent moved it (present's move or resize): over its object only when asked for over the view. */
export function movedByAgent(objectId: string | null, over: boolean): Placed {
  return { by: 'agent', over, forObject: objectId, coversAtDrop: false };
}

/** It now shows `objectId`: a place put over another object no longer covers this one. */
export function forObjectNow(placed: Placed, objectId: string | null): Placed {
  return placed.forObject === objectId ? placed : { ...placed, over: false, coversAtDrop: false };
}

/** Whether a place is kept even over the object it shows: only the object it was put over, and only as asked. */
export function keptOverIt(placed: Placed | undefined, objectId: string | null): boolean {
  if (!placed || placed.forObject !== objectId) return false;
  return placed.by === 'user' ? placed.coversAtDrop : placed.over;
}

/** Whether a panel's box covers an object's (canvas px), with `pad` kept clear around the object (placeAway's). */
export function coversObject(panel: Box | null, object: Box | null, pad = PAD): boolean {
  if (!panel || !object) return false;
  return panel[0] < object[2] + pad && object[0] - pad < panel[2] && panel[1] < object[3] + pad && object[1] - pad < panel[3];
}

/** A move or resize the server sends (present), as the magnifier takes it: where, how wide (a resize only), and
 * whether it was asked for over the view; null when it carries no place. */
export function presentPlace(set: Record<string, unknown>, verb: string): { x: number; y: number; w: number | null; over: boolean } | null {
  const rect = set.rect;
  if (!Array.isArray(rect) || rect.length !== 4 || !rect.every((v) => typeof v === 'number')) return null;
  const [x, y, w] = rect as number[];
  return { x, y, w: verb === 'resize' ? w : null, over: set.over === true };
}

/** Its usual width for the view: a third of it, up to the contract's panel, or less on phones. */
export function panelWidth(area: { width: number }): number {
  return area.width < PHONE_WIDTH ? Math.min(area.width * 0.46, 240) : Math.min(area.width * 0.34, PANEL_PX);
}

/** The band margin notes keep to on the magnifier's side of the eyepiece (canvas px, the y of a
 * note's leader; a note reaches about 26 px above it and 30 below): below the magnifier when it
 * floats in the top half, above it in the bottom half, so no label hides under it. Null when it
 * is not shown. */
export function notesBeside(magnifier: Box | null, discX: number, height: number): { side: 'left' | 'right'; top: number; bottom: number } | null {
  if (!magnifier) return null;
  const side = (magnifier[0] + magnifier[2]) / 2 < discX ? 'left' : 'right';
  const upper = (magnifier[1] + magnifier[3]) / 2 < height / 2;
  return upper ? { side, top: magnifier[3] + 34, bottom: height - 30 } : { side, top: 28, bottom: magnifier[1] - 34 };
}

const overlaps = (a: Box, b: Box) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

/** Where the magnifier floats (canvas px): over the dark surround beside the eyepiece on the
 * side away from its object (narrowed, down to MIN_WIDTH, to fit there); with no room there,
 * the corner farthest from it; on phones top right (top left when its object is there). Never
 * over its object or another panel, and it stays put while its place still fits, so it does
 * not hop about as the slide moves. A place it was put stays, but over its object only as asked: the user's drop
 * over that object, or the agent's move asked for over the view, and only while it shows that object (keptOverIt);
 * otherwise a place that would cover the object yields. Keeping clear is the rule for places nobody asked for
 * (opening, following, a new object), never a refusal of one that was (going live, G3; its review, B2). */
export function placeAway(p: Placing): Spot {
  const { area, disc, size } = p;
  const left = Math.max(0, Math.min(area.left ?? 0, area.width - MIN_WIDTH - 2 * MARGIN)); // never so far it leaves no room
  const phone = area.width < PHONE_WIDTH;
  const usual = size.width;
  const aspect = size.aspect && size.aspect > 0 ? size.aspect : null; // none: the contract's, as before
  const height = (w: number) => (aspect ? w / aspect : (w * ASPECT_H) / ASPECT_W) + size.chrome;
  const at = (s: { x: number; y: number; w: number }): Box => [s.x, s.y, s.x + s.w, s.y + height(s.w)];
  const object: Box | null = p.object ? [p.object[0] - PAD, p.object[1] - PAD, p.object[2] + PAD, p.object[3] + PAD] : null;

  if (p.remembered) {
    const { placed, ...where } = p.remembered;
    const spot: Spot = { ...where, kind: 'remembered' };
    if (keptOverIt(placed, p.objectId ?? null) || !object || !overlaps(at(spot), object)) return spot;
  }

  const objectAt: [number, number] = p.object
    ? [(p.object[0] + p.object[2]) / 2, (p.object[1] + p.object[3]) / 2]
    : disc ? [disc[0], disc[1]] : [area.width / 2, area.height / 2];
  const hard = object ? [object, ...p.obstacles] : p.obstacles;
  const soft = [...hard, ...(p.labels ?? [])];
  const fits = (s: Spot, boxes: Box[]) => {
    const [x0, y0, x1, y1] = at(s);
    return x0 >= left + MARGIN - 0.5 && y0 >= MARGIN - 0.5 && x1 <= area.width - MARGIN + 0.5 && y1 <= area.height - MARGIN + 0.5 &&
      !boxes.some((o) => overlaps([x0, y0, x1, y1], o));
  };

  const spots: Spot[] = [];
  if (disc && !phone) {
    // The surround on each side, the side away from the object first; top, then bottom.
    const sides = [
      { room: disc[0] - disc[2] - GAP - MARGIN - left, x: (w: number) => disc[0] - disc[2] - GAP - w },
      { room: area.width - (disc[0] + disc[2]) - GAP - MARGIN, x: () => disc[0] + disc[2] + GAP },
    ];
    if (objectAt[0] < disc[0]) sides.reverse();
    for (const side of sides) {
      const w = Math.min(usual, side.room);
      if (w < MIN_WIDTH) continue;
      spots.push({ x: side.x(w), y: MARGIN, w, kind: 'beside' }, { x: side.x(w), y: area.height - height(w) - MARGIN, w, kind: 'beside' });
    }
  }
  // The corners, at the width asked for and then narrower until one clears the object (M1.5c review item 3:
  // a wide magnifier in a corner covered it): the widest that fits, in the corner farthest from the object.
  const corner = (right: boolean, bottom: boolean, w: number): Spot =>
    ({ x: right ? area.width - w - MARGIN : left + MARGIN, y: bottom ? area.height - height(w) - MARGIN : MARGIN, w, kind: 'corner' });
  const places: [boolean, boolean][] = [[true, false], [false, false], [true, true], [false, true]];
  const away = ([r, b]: [boolean, boolean]) => {
    const c = corner(r, b, usual);
    return Math.hypot(c.x + usual / 2 - objectAt[0], c.y + height(usual) / 2 - objectAt[1]);
  };
  if (!phone) places.sort((a, b) => away(b) - away(a)); // phones keep top right first
  const widths: number[] = [];
  for (let w = usual; w > MIN_WIDTH; w = Math.floor(w * 0.85)) widths.push(w);
  widths.push(Math.min(usual, MIN_WIDTH));
  const corners: Spot[] = [];
  for (const w of widths) for (const [r, b] of places) corners.push(corner(r, b, w));
  spots.push(...corners);

  const kept = p.kept;
  if (kept) {
    const same = spots.find((s) => Math.abs(s.x - kept.x) < 1 && Math.abs(s.y - kept.y) < 1 && Math.abs(s.w - kept.w) < 1);
    if (same && fits(same, hard)) return { ...same, kind: 'kept' };
  }
  // No room anywhere: the narrowest, in the corner farthest from the object (never the full width unchecked).
  return spots.find((s) => fits(s, soft)) ?? spots.find((s) => fits(s, hard)) ?? corners[corners.length - places.length];
}

// UUID shells share the stack and its one frame loop. They never reserve live-view room.
export type WindowContent = { kind: 'picture'; aspect: number } | { kind: 'text' };
export interface WindowFacts {
  /** `area.left`: where the view's area begins, right of the sessions rail (Placing.area). */
  canvas: Rect; area: { width: number; height: number; left?: number }; disc: [number, number, number] | null;
  object: Box | null; labels: Box[];
}
export interface WindowOptions {
  /** Windows in distinct CSS stacking contexts must not share sibling order. */
  stackGroup?: string;
  id: string; root: HTMLElement; bar: HTMLElement; panels: PanelStack;
  isOpen?: () => boolean; close: () => void; content: () => WindowContent;
  facts: () => WindowFacts | null; onUserChange?: (property: string) => void;
  /** Before the user's own move or resize changes the shell (its owner keeps where it was, for a refusal). */
  onUserStart?: (property: string) => void;
  /** Reflow the owner's contents after an actual geometry change. */
  onLayout?: () => void;
  /** Resize the shell freely; its media fits inside without distortion. */
  freeResize?: boolean;
  /** Native owners commit the requested control, without an extra front command. */
  implicitFront?: boolean;
  /** Restoration stays behind existing windows until explicitly selected. */
  frontOnOpen?: boolean;
  chromeHeight?: number;
  /** Placed once, below the title rows of windows already open so their Close stays reachable; it then keeps that place. */
  cascade?: boolean;
}
const WINDOW_CHROME = 44;
const clamp = (value: number, min: number, max: number) => Math.min(Math.max(value, min), Math.max(min, max));
export function defaultWindowSize(content: WindowContent, stage: { width: number; height: number }, chrome = WINDOW_CHROME): { width: number; height: number; fixedHeight: boolean } {
  if (content.kind === 'text') return { width: Math.min(420, Math.max(0, stage.width - 20)), height: Math.min(360, Math.max(0, stage.height - 20)), fixedHeight: true };
  const aspect = Number.isFinite(content.aspect) && content.aspect > 0 ? content.aspect : 4 / 3;
  // The least useful source-filled content is 240×180; do not distort wide images.
  const wanted = Math.max(320, 240, 180 * aspect);
  const width = Math.max(0, Math.min(wanted, stage.width - 20, Math.max(0, stage.height - 20 - chrome) * aspect));
  return { width, height: width / aspect + chrome, fixedHeight: false };
}
let frontCount = 30;
export function nextFront(): number { return ++frontCount; }

export class FloatingWindow implements Panel {
  readonly id: string;
  readonly group: string;
  private readonly opts: WindowOptions;
  private readonly listeners = new AbortController();
  private readonly gestures = new Set<AbortController>();
  private wanted: { position: [number, number] | null; size: [number, number] | null } = { position: null, size: null };
  private wasOpen = false;
  private raised: number | null = null;
  private expanded = false;
  /** Its position is still the automatic first place (cascade), not one a person or a saved arrangement chose. */
  private autoPlaced = false;
  private grip: HTMLElement;

  constructor(opts: WindowOptions) {
    this.opts = opts; this.id = opts.id; this.group = opts.stackGroup ?? 'window';
    opts.root.classList.add('fwin'); opts.bar.classList.add('fwin-bar');
    this.grip = document.createElement('div'); this.grip.className = 'fwin-grip';
    this.grip.setAttribute('role', 'separator'); this.grip.setAttribute('aria-label', 'Resize window'); opts.root.append(this.grip);
    this.grip.tabIndex = 0; this.grip.title = 'Drag to resize. Arrow keys resize; Shift for larger steps.';
    this.grip.textContent = '↘';
    const handle = document.createElement('div'); handle.className = 'fwin-handle';
    handle.setAttribute('role', 'separator'); handle.setAttribute('aria-label', 'Resize window height'); opts.root.prepend(handle);
    const { signal } = this.listeners;
    opts.root.addEventListener('pointerdown', (event) => {
      if (opts.implicitFront !== false && !(event.target as HTMLElement).closest('.fwin-grip, .fwin-handle, .capability-controls')) this.front(true);
    }, { capture: true, signal });
    opts.bar.addEventListener('pointerdown', (event) => this.drag(event), { signal });
    opts.bar.addEventListener('keydown', (event) => {
      if (opts.root.dataset.studyArtifact !== 'true' || event.target !== opts.bar || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
      const stage=this.stage();if(!stage)return;
      event.preventDefault();event.stopPropagation();opts.onUserStart?.('move');
      const box=opts.root.getBoundingClientRect(),step=event.shiftKey?40:10;
      this.moveTo(box.left-stage.left+(event.key==='ArrowRight'?step:event.key==='ArrowLeft'?-step:0),
        box.top-stage.top+(event.key==='ArrowDown'?step:event.key==='ArrowUp'?-step:0));
      if (opts.implicitFront !== false) this.front(false);
      opts.onUserChange?.('move');
    }, {signal});
    opts.bar.addEventListener('dblclick', (event) => {
      if ((event.target as HTMLElement).closest('button, a, input, select, textarea')) return;
      // Reset size only: a blanket geometry restore would overwrite newer position ownership.
      opts.onUserStart?.('resize'); this.expanded = false; this.wanted.size = null; this.frame(); opts.onUserChange?.('resize');
    }, { signal });
    this.grip.addEventListener('pointerdown', (event) => this.resize(event, this.grip, false), { signal });
    this.grip.addEventListener('keydown', (event) => {
      const step = event.shiftKey ? 40 : 10, box = opts.root.getBoundingClientRect();
      if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault(); event.stopPropagation(); opts.onUserStart?.('resize'); this.autoPlaced = false;
      this.resizeTo(Math.max(180, box.width + (event.key === 'ArrowRight' ? step : event.key === 'ArrowLeft' ? -step : 0)),
        Math.max(140, box.height + (event.key === 'ArrowDown' ? step : event.key === 'ArrowUp' ? -step : 0)));
      opts.onUserChange?.('resize');
    }, { signal });
    handle.addEventListener('pointerdown', (event) => this.resize(event, handle, true), { signal });
  }
  isOpen(): boolean { return (this.opts.isOpen?.() ?? !this.opts.root.hidden) && !this.opts.root.hidden; }
  isVisible(): boolean { return this.opts.root.dataset.resultTucked !== 'true'; }
  close(): void { this.opts.close(); }
  footprint(): DOMRect | null { return this.isOpen() ? this.opts.root.getBoundingClientRect() : null; }
  titleRow(): DOMRect | null { return this.isOpen() ? this.opts.bar.getBoundingClientRect() : null; }
  z(): number | null { return this.raised; }
  setStackOrder(order: number): void { this.raised = 30 + order; this.opts.root.style.zIndex = String(this.raised); }
  front(byUser = false): number {
    this.opts.panels.raise(this.id);
    if (this.raised === null) this.setStackOrder(0);
    if (byUser) this.opts.onUserChange?.('front');
    return this.raised!;
  }
  private stage(): DOMRect | null { return (this.opts.root.offsetParent as HTMLElement | null)?.getBoundingClientRect() ?? null; }
  isExpanded(): boolean { return this.expanded; }
  /** Presentation only. Fit the visible stage while preserving the prior geometry intent. */
  toggleExpanded(): void {
    const stage = this.stage();
    if (!this.opts.freeResize || !this.isOpen() || !stage || stage.width <= 0 || stage.height <= 0) return;
    this.opts.onUserStart?.('geometry');
    this.expanded = !this.expanded; this.layout();
    if (this.opts.implicitFront !== false) this.front();
    this.opts.onUserChange?.('geometry');
  }
  moveTo(x: number, y: number): void { this.expanded = false; this.autoPlaced = false; this.wanted.position = [x, y]; this.layout(); }
  resizeTo(width: number, height?: number): void {
    this.expanded = false;
    // Sized by its opener after the first place was chosen: choose it again for the new size (a person's resize keeps theirs).
    if (this.autoPlaced) { this.wanted.position = null; this.autoPlaced = false; }
    const content = this.opts.content();
    this.wanted.size = [width, height ?? (content.kind === 'picture' ? width / content.aspect + (this.opts.chromeHeight ?? WINDOW_CHROME) : this.opts.root.offsetHeight)];
    this.layout();
  }
  private layout(): void {
    const stage = this.stage(); if (!stage) return;
    // The camera host may be partly above the viewport after chat takes focus.
    // Keep the requested geometry as intent, but fit the shell to its visible area.
    const viewport = window.visualViewport;
    const vx = viewport?.offsetLeft ?? 0, vy = viewport?.offsetTop ?? 0;
    const vw = viewport?.width ?? window.innerWidth, vh = viewport?.height ?? window.innerHeight;
    const visible = windowPlacementArea(stage, [vx, vy, vw, vh]);
    const area = visible
      ? { x: visible[0] - stage.left, y: visible[1] - stage.top, width: visible[2], height: visible[3] }
      : { x: 0, y: 0, width: stage.width, height: stage.height };
    const base = defaultWindowSize(this.opts.content(), area, this.opts.chromeHeight);
    const [width, height] = this.expanded ? [area.width, area.height] : this.wanted.size ?? [base.width, base.height];
    // An explicitly requested shrink is allowed. Bounds remain physical, rather than a fake minimum.
    const w = clamp(width, 1, area.width);
    let h = clamp(height, 1, area.height);
    if (this.opts.cascade && !this.expanded && !this.wanted.position && this.isOpen()) {
      const [x, y, fit] = this.clearOfHeaders(stage, area, w, h);
      this.wanted.position = [x, y]; this.autoPlaced = true; if (fit !== h) { this.wanted.size = [w, fit]; h = fit; }
    }
    const [x, y] = this.expanded ? [area.x, area.y] : this.wanted.position ?? this.autoPosition(stage, w, h);
    Object.assign(this.opts.root.style, { width: `${w}px`, height: `${h}px`, left: `${clamp(x, area.x, area.x + area.width - w)}px`, top: `${clamp(y, area.y, area.y + area.height - h)}px`, right: 'auto', bottom: 'auto' });
    this.opts.onLayout?.();
  }
  /** Its first place: the usual one, or else the first one down the stack where it covers no other window's title row
   * (and Close) and none in front of it covers its own; shorter to fit, if need be. */
  private clearOfHeaders(stage: DOMRect, area: { x: number; y: number; width: number; height: number }, width: number, height: number): [number, number, number] {
    const [ax, ay] = this.autoPosition(stage, width, height);
    const local = (r: Rect) => [r.left - stage.left, r.top - stage.top, r.right - stage.left, r.bottom - stage.top];
    const others = this.opts.panels.windows(this.id).map(({ box, title, front }) => ({ box: local(box), title: local(title), front }));
    const bottom = area.y + area.height, right = area.x + area.width, bar = this.opts.chromeHeight ?? WINDOW_CHROME;
    const meets = (a: number[], b: number[]) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
    const tries = [[ax, ay], ...others.flatMap((o) => [[o.title[0] + 24, o.title[3] + 4], [o.box[0], o.box[3] + 4], [o.box[2] + 4, o.box[1]]]).sort((p, q) => p[1] - q[1])];
    for (const [x, y] of tries) {
      const top = clamp(y, area.y, bottom - Math.min(height, 160)), left = clamp(x, area.x, right - width), h = Math.min(height, bottom - top);
      if (h < Math.min(height, 160)) continue;
      const box = [left, top, left + width, top + h], title = [left, top, left + width, top + bar];
      // Its title row only needs to stay clear of the windows in front of it (restored or completed results can open behind).
      if (!others.some((o) => meets(box, o.title) || (o.front && meets(title, o.box)))) return [left, top, h];
    }
    return [ax, ay, height]; // no such place: the usual one, as before
  }
  private autoPosition(stage: DOMRect, width: number, height: number): [number, number] {
    const facts = this.opts.facts();
    if (!facts) return [10, 10];
    const spot = placeAway({ area: facts.area, disc: facts.disc, object: facts.object, labels: facts.labels,
      obstacles: this.opts.panels.obstacles(this.id).map((r) => [r.left - facts.canvas.left, r.top - facts.canvas.top, r.right - facts.canvas.left, r.bottom - facts.canvas.top]),
      size: { width, chrome: 0, aspect: width / Math.max(1, height) } });
    return [spot.x + facts.canvas.left - stage.left, spot.y + facts.canvas.top - stage.top];
  }
  frame(): void {
    const open = this.isOpen();
    if (open && !this.wasOpen && this.opts.frontOnOpen !== false) this.front(); // front before placing: recovered opening semantics
    this.wasOpen = open; if (open) this.layout();
  }
  private gesture(handle: HTMLElement, event: PointerEvent, move: (event: PointerEvent) => void, done?: () => void): void {
    handle.setPointerCapture(event.pointerId);
    const controller = new AbortController(); this.gestures.add(controller);
    handle.addEventListener('pointermove', (next) => { if (next.pointerId === event.pointerId) move(next); }, { signal: controller.signal });
    const end = () => { controller.abort(); this.gestures.delete(controller); delete this.opts.root.dataset.dragging; done?.(); };
    handle.addEventListener('pointerup', end, { signal: controller.signal }); handle.addEventListener('pointercancel', end, { signal: controller.signal });
  }
  private drag(event: PointerEvent): void {
    if (window.innerWidth <= 960 && this.opts.root.classList.contains('workspace-window')) return;
    if ((event.target as HTMLElement).closest('button, a, input, select, textarea')) return;
    const root = this.opts.root, stage = this.stage(); if (!stage) return;
    const rect = root.getBoundingClientRect(), dx = event.clientX - rect.left, dy = event.clientY - rect.top;
    this.opts.onUserStart?.('move');
    root.dataset.dragging = '';
    this.gesture(this.opts.bar, event, (next) => {
      this.moveTo(next.clientX - dx - stage.left, next.clientY - dy - stage.top);
      if (this.opts.implicitFront !== false) this.opts.onUserChange?.('move');
    }, () => { if (this.opts.implicitFront === false) this.opts.onUserChange?.('move'); });
  }
  private resize(event: PointerEvent, handle: HTMLElement, heightOnly: boolean): void {
    event.preventDefault(); event.stopPropagation(); this.front(this.opts.implicitFront !== false);
    this.opts.onUserStart?.('resize'); this.autoPlaced = false;
    const rect = this.opts.root.getBoundingClientRect(), content = this.opts.content();
    const stage = this.stage();
    if (stage) this.wanted.position = [rect.left - stage.left, rect.top - stage.top];
    this.gesture(handle, event, (next) => {
      const width = heightOnly ? rect.width : Math.max(180, rect.width + next.clientX - event.clientX);
      const height = Math.max(140, !heightOnly && content.kind === 'picture' && !this.opts.freeResize
        ? width / content.aspect + (this.opts.chromeHeight ?? WINDOW_CHROME) : rect.height + next.clientY - event.clientY);
      this.resizeTo(width, height); if (this.opts.implicitFront !== false) this.opts.onUserChange?.('resize');
    }, () => { if (this.opts.implicitFront === false) this.opts.onUserChange?.('resize'); });
  }
  dispose(): void { this.listeners.abort(); for (const gesture of this.gestures) gesture.abort(); this.gestures.clear(); this.opts.panels.unregister(this); }
}
