// One motion for the Stage and its side columns (Beta 0.4.1; Will, 2026-10-26: "clicking the side pannels makes it very
// jumping and jittery and not smooth how our main space resizes"). A press that opens or closes a side column (the
// drawer, the Data window or a docked tool page on the left, the Assistant or a docked page on the right) moves the
// Stage's edge and that column together: 200 ms on the house curve (--mm-base, --mm-ease), driven by Web Animations
// that share one explicit start time and use pixel keyframes, so both edges are computed from the same time in the
// same style pass and never part. The picture follows its box (view.ts places it at once on each new canvas size).
//
// Only a press moves anything (workspace-study.ts press): a Project switch, startup, a window resize across 1200 px
// and a page docking land at once, as before. Closing animates out: the column is inert at once and hidden at the
// end. Switching between places while one is open plays nothing. While the sides move, work that would measure the
// moving layout every frame waits and runs once when they land (`holdUntilLanded`: the Working strip's pills, the
// Stage's corners, the camera menu, the Tab row). Under reduced motion, or without Web Animations, everything lands in
// one frame: the CSS reduced-motion rule does not cover Web Animations, so it is checked here.

export const SIDE_MS = 200;
export const SIDE_EASE = 'cubic-bezier(.2,.8,.2,1)';
/** The end is also handled by a timer, in case the animations' `finished` never comes (a page put in the background). */
const FALLBACK_MS = SIDE_MS + 60;

/** What one side shows: its column and the column's width, or nothing. `layer`: a docked page in the top layer (above
 * the rail), slid in clipped at the rail; it is hidden by the deck at once, so it never animates out. */
export type SideColumn = { node: HTMLElement; width: number; layer?: boolean } | null;

/** The sides as a layout shows them, from state alone (window px). */
export interface Sides {
  viewport: number;
  /** The rail's width: the left column slides out from behind it. */
  rail: number;
  /** The Stage's insets from the window's left and right edges. */
  stage: { left: number; right: number };
  left: SideColumn;
  right: SideColumn;
  /** How far the Working strip reaches past the Stage's right edge (under the Assistant's column). */
  under: number;
}

type Props = Record<string, [number, number]>;
/** One element's motion: each animated property's start and end (px). `closing`: hidden when it lands. */
export interface SideTrack { node: HTMLElement; props: Props; closing: boolean; clip?: [number, number] }

/** The motion from `a` to `b`: nothing unless a column opens or closes (switching places plays nothing; a Stage that
 * changes width on a switch, with custom widths, lands at once). */
export function sideTracks(a: Sides, b: Sides, studio: HTMLElement, strip: HTMLElement | null): SideTrack[] {
  const leftMoves = !a.left !== !b.left, rightMoves = !a.right !== !b.right;
  if (!leftMoves && !rightMoves) return [];
  const tracks: SideTrack[] = [];
  const width = (s: Sides) => s.viewport - s.stage.left - s.stage.right;
  if (a.stage.left !== b.stage.left || a.stage.right !== b.stage.right)
    tracks.push({ node: studio, props: { marginLeft: [a.stage.left, b.stage.left], width: [width(a), width(b)] }, closing: false });
  if (leftMoves) {
    const column = (a.left ?? b.left)!, out = a.rail - column.width, opening = !a.left;
    if (column.layer) {
      // Above the rail: clipped at it, so it comes out from behind it like the drawer.
      if (opening) tracks.push({ node: column.node, props: { left: [out, a.rail] }, closing: false, clip: [column.width, 0] });
    } else {
      tracks.push({ node: column.node, props: { left: opening ? [out, a.rail] : [a.rail, out], width: [column.width, column.width] }, closing: !opening });
    }
  }
  if (rightMoves) {
    const column = (a.right ?? b.right)!, opening = !a.right;
    if (column.layer) {
      if (opening) tracks.push({ node: column.node, props: { left: [a.viewport, a.viewport - column.width] }, closing: false });
    } else {
      tracks.push({ node: column.node, props: { right: opening ? [-column.width, 0] : [0, -column.width], width: [column.width, column.width] }, closing: !opening });
    }
  }
  if (strip && a.under !== b.under) tracks.push({ node: strip, props: { marginRight: [-a.under, -b.under] }, closing: false });
  return tracks;
}

const keyframe = (props: Props, at: 0 | 1, clip?: [number, number]): Keyframe => {
  const frame: Keyframe = {};
  for (const [name, values] of Object.entries(props)) frame[name] = `${values[at]}px`;
  if (clip) frame.clipPath = `inset(0 0 0 ${clip[at]}px)`;
  return frame;
};

type Running = { tracks: SideTrack[]; animations: Map<HTMLElement, Animation>; timer: ReturnType<typeof setTimeout>; stillClosed: (node: HTMLElement) => boolean };
let running: Running | null = null;
let expected = false;
const held = new Set<() => void>();

/** Whether the sides are moving, or are about to inside the press that moves them. */
export function sideMoving(): boolean { return running !== null || expected; }

/** While the sides move, `later` waits (once, however often it is asked for) and runs when they land; returns false
 * when nothing moves, so the caller does its work now. */
export function holdUntilLanded(later: () => void): boolean {
  if (!sideMoving()) return false;
  held.add(later);
  return true;
}

/** A press is about to move the sides: work its layout write asks for waits from now (before the motion starts). */
export function expectSideMotion(): void { expected = true; }

/** Whether `node` is a column on its way out (shown, inert, until the motion hides it): a layout written meanwhile
 * leaves it to the motion instead of hiding it mid-exit. */
export function sideLeaving(node: HTMLElement): boolean { return !!running?.tracks.some((t) => t.closing && t.node === node); }

function reduced(): boolean {
  try { return typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches; } catch { return false; }
}

/** Where an element's motion is now (its eased progress, 0 to 1), or null when it is not moving. */
function progressOf(run: Running | null, node: HTMLElement): { track: SideTrack; at: number } | null {
  const animation = run?.animations.get(node), track = run?.tracks.find((t) => t.node === node);
  if (!animation || !track) return null;
  const progress = animation.effect?.getComputedTiming().progress;
  return { track, at: typeof progress === 'number' ? progress : 1 };
}
const between = (values: [number, number], at: number) => values[0] + (values[1] - values[0]) * at;

function land(run: Running, keep: Set<HTMLElement> = new Set()): void {
  clearTimeout(run.timer);
  for (const [node, animation] of run.animations) if (!keep.has(node)) animation.cancel();
  for (const track of run.tracks) {
    if (!track.closing || keep.has(track.node)) continue;
    track.node.inert = false;
    if (run.stillClosed(track.node) && !track.node.hidden) track.node.hidden = true;
  }
}

function settle(): void {
  expected = false;
  if (typeof document !== 'undefined' && document.body?.dataset.sideMotion !== undefined) delete document.body.dataset.sideMotion;
  const later = [...held];
  held.clear();
  for (const run of later) {
    try { run(); } catch (error) { console.error('after the side motion:', error); }
  }
}

/** Moves the sides along `tracks` in one motion (or lands them at once: reduced motion, no Web Animations, a hidden
 * page). A press while they move starts from where they are. `stillClosed` says whether a closing column is still
 * meant to be closed when it lands (a second press may have opened it again). */
export function playSideMotion(tracks: SideTrack[], stillClosed: (node: HTMLElement) => boolean, now: () => number = () => performance.now()): void {
  const before = running;
  if (!tracks.length) { if (!before && expected) settle(); return; }
  const animated = typeof (tracks[0].node as { animate?: unknown }).animate === 'function' && !reduced() && !(typeof document !== 'undefined' && document.hidden);
  const atOnce = () => {
    if (running) { land(running); running = null; }
    for (const track of tracks) { track.node.inert = false; if (track.closing && stillClosed(track.node) && !track.node.hidden) track.node.hidden = true; }
    settle();
  };
  if (!animated) { atOnce(); return; }
  try { animate(tracks, before, stillClosed, now); } catch (error) { console.error('side motion:', error); atOnce(); }
}

function animate(tracks: SideTrack[], before: Running | null, stillClosed: (node: HTMLElement) => boolean, now: () => number): void {
  // Interrupted: what was moving and is not in this press goes on to where it was going, and every element starts from
  // where it is now, all on the new clock, so no edge jumps to the old motion's end or parts from another.
  if (before) for (const old of before.tracks) if (!tracks.some((t) => t.node === old.node))
    tracks = [...tracks, { ...old, props: Object.fromEntries(Object.entries(old.props).map(([name, values]) => [name, [...values] as [number, number]])) }];
  for (const track of tracks) {
    const moving = progressOf(before, track.node);
    if (!moving) continue;
    for (const [name, values] of Object.entries(track.props)) { const was = moving.track.props[name]; if (was) values[0] = between(was, moving.at); }
    if (track.clip && moving.track.clip) track.clip = [between(moving.track.clip, moving.at), track.clip[1]];
  }
  if (before) land(before, new Set(tracks.map((t) => t.node)));
  const start = now();
  const animations = new Map<HTMLElement, Animation>();
  try {
    for (const track of tracks) {
      before?.animations.get(track.node)?.cancel();
      if (track.closing) { if (track.node.hidden) track.node.hidden = false; track.node.inert = true; } else track.node.inert = false;
      const animation = track.node.animate([keyframe(track.props, 0, track.clip), keyframe(track.props, 1, track.clip)], { duration: SIDE_MS, easing: SIDE_EASE, fill: 'both' });
      animation.startTime = start; // one clock for every edge
      animations.set(track.node, animation);
    }
  } catch (error) {
    // Nothing is left holding a half-made motion's values: the caller lands everything at once.
    for (const animation of animations.values()) animation.cancel();
    throw error;
  }
  document.body.dataset.sideMotion = 'true';
  const run: Running = { tracks, animations, stillClosed, timer: setTimeout(() => finish(), FALLBACK_MS) };
  running = run;
  const finish = () => { if (running !== run) return; running = null; land(run); settle(); };
  void Promise.all([...animations.values()].map((a) => a.finished)).then(finish, () => {});
}
