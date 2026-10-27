// The drawing contract (AGENTS.md "Freshness and overlay contract"), from the numbers the server
// and the simulation harness read too (contract/overlay.json; orchestrator/contract.py). Pure:
// no DOM, so it is unit tested (overlay.test.ts).
import contract from '../../../contract/overlay.json';
import type { FrameHeader, Point, SceneObject } from './protocol';

/** Fade on the fastest of the last few frames: out as soon as a pan starts, back in once the
 * view has been still for a third of a second. */
export const SPEED_WINDOW = contract.speedWindow;
/** Clicks reach this fraction of the eyepiece's radius. */
export const CLICK_RADIUS = contract.clickRadius;
/** A spoken chat line (yours or the voice's) keeps growing until this long passes without a piece. */
export const SPEECH_LINE_GAP_MS = contract.speechLineGapS * 1000;

const { fitPad, fitMinPx, minWindowPx, maxLevel: MAX_LEVEL, aspect } = contract.magnifier;
const PANEL_ASPECT = aspect[0] / aspect[1];

/** Whether a picture is new enough to show as live: older than frameDimMs it is dimmed and
 * nothing is drawn on it (the server calls the feed not live only after feedStaleS). */
export function isFresh(ageMs: number): boolean {
  return ageMs < contract.frameDimMs;
}

/** Fast pans blur the image and tracking error grows until the view settles and is
 * loop-corrected, so outlines are solid below fadeStartPx px/frame, gone above fadeEndPx and
 * fade in between. */
export function overlayOpacity(speed: number): number {
  const t = (speed - contract.fadeStartPx) / (contract.fadeEndPx - contract.fadeStartPx);
  return 1 - Math.max(0, Math.min(1, t));
}

/** Whether the studio draws an object on this frame: its place must be verified on the live view
 * (ADR 0010: tracked, in this frame's motion epoch; an outline "refinding" or from another epoch
 * is placed nowhere until the server verifies it by its own pixels, so it is never drawn at an
 * unverified position), the frame itself must be one the motion tracker registered (a frame it could
 * not register carries the last offset over, so nothing on it is placed: the first frames of a cut,
 * before the epoch changes, are such frames; going live, G2), and its centre must still be inside the
 * eyepiece (the specimen can slide out of view). The simulation harness applies the same rule; the brain's in_view does
 * not check the frame's registration (for the few frames it lasts, it may say on view what the studio hides). */
export function isVisible(obj: SceneObject, h: FrameHeader): boolean {
  if (obj.track !== 'tracking' || obj.anchorEpoch !== h.epoch || h.tracked === false) return false;
  if (!h.fov) return true;
  const pts = obj.polygons.flat();
  const sx = h.dx - obj.anchorOffset[0];
  const sy = h.dy - obj.anchorOffset[1];
  const cx = pts.reduce((a, q) => a + q[0], 0) / pts.length + sx;
  const cy = pts.reduce((a, q) => a + q[1], 0) / pts.length + sy;
  return Math.hypot(cx - h.fov[0], cy - h.fov[1]) <= h.fov[2] * contract.inViewRadius;
}

/** Outlines smaller than this on screen (CSS px) get a ring so they can be seen, and can be
 * hovered and clicked anywhere inside it. */
export const TINY_PX = 18;

/** The outline a click or hover at `point` (camera px) lands on, or null: only one that is drawn
 * (`isVisible`: tracking in this epoch, inside the eyepiece), so the click rule and the drawing rule
 * are the same; an outline that is hidden (refinding, another epoch) never catches a click. Cells are
 * not picked. `scale`: screen px per camera px. */
export function hitObject(objects: SceneObject[], h: FrameHeader, point: Point, scale: number): SceneObject | null {
  for (const obj of [...objects].reverse()) {
    // A part is drawn as part of its selected object (view.ts), never caught apart: a click inside the object is the
    // object's (live 2026-10-11: a click on a dissected stoma selected its invisible part).
    if (obj.kind === 'cell' || obj.kind === 'part' || !isVisible(obj, h)) continue;
    const sx = h.dx - obj.anchorOffset[0];
    const sy = h.dy - obj.anchorOffset[1];
    const local: Point = [point[0] - sx, point[1] - sy];
    if (obj.polygons.some((poly) => insidePolygon(local, poly))) return obj;
    const pts = obj.polygons.flat();
    const xs = pts.map((q) => q[0]);
    const ys = pts.map((q) => q[1]);
    const s = Math.max(scale, 1e-6);
    if (Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) * s < TINY_PX) {
      const cx = (Math.max(...xs) + Math.min(...xs)) / 2;
      const cy = (Math.max(...ys) + Math.min(...ys)) / 2;
      if (Math.hypot(local[0] - cx, local[1] - cy) * s <= TINY_PX * 0.8) return obj;
    }
  }
  return null;
}

export function insidePolygon([x, y]: Point, poly: Point[]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** The magnifier at level 1: the object (box in camera px) with its padding, at least
 * fitMinPx, widened to the panel's shape (w/h). As orchestrator/magnifier.py fit_window. */
export function fitWindow(boxW: number, boxH: number, panelAspect = PANEL_ASPECT): [number, number] {
  let w = Math.max(boxW * (1 + 2 * fitPad), fitMinPx);
  let h = Math.max(boxH * (1 + 2 * fitPad), fitMinPx);
  if (w / h > panelAspect) h = w / panelAspect;
  else w = h * panelAspect;
  return [w, h];
}

/** The closest the magnifier goes for a fitted window: maxLevel x, or a window about
 * minWindowPx camera px across. As orchestrator/magnifier.py level_limit, which is what the
 * studio is sent (MagnifierState.maxLevel); this is the same rule, for the tests. */
export function maxLevel(fit: [number, number] | null): number {
  if (!fit) return MAX_LEVEL;
  return Math.max(1, Math.min(MAX_LEVEL, Math.min(...fit) / minWindowPx));
}

/** How sure a name is, in words (never a raw percentage: the model's numbers run well above how
 * often it is right, docs/bench/naming.md). The server sets the status (scene.status_for). */
export function statusWords(status: string, maybe: string[] = []): string {
  if (status === 'confirmed') return 'Confirmed';
  if (status === 'likely') return 'Probably';
  if (status === 'unsure') return maybe.length ? `Not sure: maybe ${maybe.join(' or ')}` : 'Not sure';
  return '';
}

/** Words for an outline the check said does not fit, while that stands: the check's own reason, said as a doubt. */
const POOR_FIT: Record<string, string> = {
  too_big: 'it covers more than the thing', too_small: 'it covers only part of the thing', wrong: 'it may be around something else',
};
export function fitWords(obj: Pick<SceneObject, 'fit'>): string {
  const fit = obj.fit;
  if (!fit) return '';
  return `may not fit: ${fit.why.trim() || POOR_FIT[fit.fits] || 'the check doubts this outline'}`;
}

/** The closest of the user's own labels, shown apart from the model's name ("your label: guard
 * cell (0.82)"); nothing for a thing the user named themselves (its label is theirs already). */
export function taughtWords(obj: Pick<SceneObject, 'taught' | 'status'>): string {
  const t = obj.taught;
  if (!t || !t.label || obj.status === 'confirmed') return '';
  return `your label: ${t.label} (${t.similarity.toFixed(2)})`;
}

const NICE_UM = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000];

/** A scale bar for the view: the round length (µm) whose bar is closest to `targetPx` screen
 * pixels long, given microns per camera pixel and camera-to-screen pixels. Null without a scale. */
export function scaleBar(umPerPx: number | null, screenPerPx: number, targetPx = 100): { um: number; px: number } | null {
  if (!umPerPx || umPerPx <= 0 || screenPerPx <= 0) return null;
  const umPerScreenPx = umPerPx / screenPerPx;
  let best = NICE_UM[0];
  for (const um of NICE_UM) if (Math.abs(um / umPerScreenPx - targetPx) < Math.abs(best / umPerScreenPx - targetPx)) best = um;
  return { um: best, px: best / umPerScreenPx };
}

/** An outline's size as the studio shows it: "about 12 µm" or "about 12 × 8 µm"; "~" marks an
 * estimate from the objective (orchestrator/scale.py size_words, the server's version). */
export function sizeWords(size: { lengthUm: number; widthUm: number; measured: boolean } | null | undefined): string {
  if (!size) return '';
  const round = (v: number) => (v >= 100 ? Math.round(v / 10) * 10 : v >= 10 ? Math.round(v) : Math.round(v * 10) / 10);
  const approx = size.measured ? '' : '~';
  return size.widthUm > 0.8 * size.lengthUm
    ? `${approx}${round(size.lengthUm)} µm`
    : `${approx}${round(size.lengthUm)} × ${round(size.widthUm)} µm`;
}
