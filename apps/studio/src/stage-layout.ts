import { cornerMode, cornerWidth } from './layout-rules';
import { placeFrame, type View } from './view';
import { holdUntilLanded } from './side-motion';

/** Layout only: the view owns frame geometry. Measurements use the stable full stage. */
export class StageLayout {
  private readonly stage: HTMLElement;
  private readonly view: View;
  private readonly top: HTMLElement;
  private readonly bottom: HTMLElement;
  private queued = false;
  private readonly later = () => this.schedule();
  private stamp = '';
  private flowHeight: number | null = null;
  private lastViewport = '';
  /** The sessions rail (panels.Reserved): `left` the room it takes from the eyepiece while open, `edge` its width open
   * or closed. The corners begin right of `edge` (studio-shell.css --rail-room). Closed, its slim strip changes no
   * decision here (the corners only move over by it, where the eyepiece has curved away); open, the eyepiece is placed
   * right of it and the corners' room is measured from the rail. */
  private rail = { left: 0, edge: 0 };
  constructor(stage: HTMLElement, view: View) {
    this.stage = stage; this.view = view;
    this.top = stage.querySelector('.stage-top')!;
    this.bottom = stage.querySelector('.stage-bottom')!;
    new ResizeObserver(() => this.schedule()).observe(stage);
    new MutationObserver(() => this.schedule()).observe(this.top, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden'] });
    new MutationObserver(() => this.schedule()).observe(this.bottom, { subtree: true, childList: true, attributes: true, attributeFilter: ['hidden'] });
    window.addEventListener('resize', () => this.schedule());
  }
  setRail(left: number, edge: number): void {
    const next = { left: Math.round(left), edge: Math.round(edge) };
    if (next.left === this.rail.left && next.edge === this.rail.edge) return;
    this.rail = next;
    this.schedule();
  }
  /** Lays the corners out once in the next frame. While the side columns move (side-motion.ts) the decision waits and
   * is made once when they land, never on each in-between size. */
  schedule(): void {
    if (this.queued || holdUntilLanded(this.later)) return;
    this.queued = true;
    requestAnimationFrame(() => { this.queued = false; this.layout(); });
  }
  /** The corners' mode on the stage, mirrored on body (when it changes) for the rules that lay the page out around a
   * flowing stage (studio-shell.css): a :has() there re-styled the whole page on every change inside it. */
  private corners(mode: string | null): void {
    if (mode === null) delete this.stage.dataset.corners; else this.stage.dataset.corners = mode;
    const body = document.body?.dataset;
    if (body && (body.stageCorners ?? null) !== mode) { if (mode === null) delete body.stageCorners; else body.stageCorners = mode; }
  }
  private layout(): void {
    // A hidden stage (another page open) has no room to measure: its corners are left as they were, and laid out
    // when it is shown again.
    if (!this.stage.clientWidth || !this.stage.clientHeight) return;
    const viewport = `${innerWidth}:${innerHeight}`;
    if (viewport !== this.lastViewport) {
      // Remove flow's auto-grid before measuring the new normal stage.
      this.flowHeight = null; this.stage.style.height = '';
      this.corners(null); this.lastViewport = viewport;
    }
    const h = this.view.header, width = this.stage.clientWidth, height = this.flowHeight ?? this.stage.clientHeight;
    const { left, edge } = this.rail;
    const from = left > 0 ? edge : 0; // where the corners' room is measured from: the open rail, else the stage's edge
    // Work coming and going is the Working strip's, under the Stage (working-strip.ts): it never changes this layout.
    const controls = !!this.stage.querySelector('#feed-badge:not([hidden]), #layer-controls:not([hidden]), .layer-pill:not([hidden])');
    const stamp = JSON.stringify([h?.w,h?.h,h?.fov,width,height,innerWidth,this.view.wholeFrame,controls,this.top.textContent,this.bottom.textContent,left,edge]);
    if (stamp === this.stamp) return;
    this.stamp = stamp;
    let room = Math.min(352, (width - from) * .4);
    if (h?.fov && !this.view.wholeFrame) {
      const p = placeFrame(h, width, height, 0, false, 0, left);
      room = p.ox + h.fov[0] * p.scale - h.fov[2] * p.scale * .985 - 14 - 32 - from;
    }
    let mode = cornerMode(innerWidth, room, controls);
    this.corners(mode);
    this.stage.style.setProperty('--corner-room', `${cornerWidth(room)}px`);
    let top = 0, bottom = 0;
    if (mode === 'bands') {
      top = this.top.offsetHeight ? this.top.offsetTop + this.top.offsetHeight + 8 : 0;
      bottom = this.bottom.offsetHeight ? height - this.bottom.offsetTop + 8 : 0;
      // Do not buy control space by shrinking a previously usable eyepiece.
      if (h?.fov) {
        const before = placeFrame(h, width, height, 0, this.view.wholeFrame, 0, left);
        const after = placeFrame(h, width, height - top - bottom, 0, this.view.wholeFrame, 0, left);
        if (after.scale + .001 < before.scale) { mode = 'flow'; top = bottom = 0; this.corners(mode); }
      }
    }
    if (mode === 'flow' && innerWidth > 960) {
      this.flowHeight = height; this.stage.style.height = `${height}px`;
      // The corners hang under the view (the bottom one 36 px down): the dock moves down by all they hold, so none of
      // it hangs over the dock (2026-10-11: a two-row layer pill already overlapped it by 26 px).
      this.stage.style.setProperty('--flow-bottom', `${Math.max(90, 36 + this.bottom.offsetHeight + 10)}px`);
    } else { this.flowHeight = null; this.stage.style.height = ''; }
    this.stage.style.setProperty('--band-top', `${top}px`);
    this.stage.style.setProperty('--band-bottom', `${bottom}px`);
    const canvas = this.view.canvas.getBoundingClientRect();
    const boxes = mode === 'corners' ? [this.top,this.bottom].filter(n => n.offsetHeight).map(n => n.getBoundingClientRect()) : [];
    this.view.cornerNotes = boxes.map(b => ({ x:b.left-canvas.left,y:b.top-canvas.top,w:b.width,h:b.height }));
  }
}
