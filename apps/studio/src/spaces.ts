// Coordinate spaces (contract 10 and invariant 14 of docs/design/2026-10-06-captures-operations-evidence.md):
// the studio's twin of services/orchestrator/spaces.py, where the vocabulary and the mappings are written
// first. Every overlay, close-up and mark states which space its coordinates are in:
//   frame      live camera-frame pixels of a stated epoch and slide offset; drawn at p + (offset_now - offset_anchor)
//              (slide px are frame px at offset (0, 0))
//   capture    one capture's image pixels; an enlarged view is a crop and a scale of it. A still's original is one: while
//              the Stage shows it, the bridge's frames hold it fitted and letterboxed (`stillToFrame`, the fit's matrix)
//   workspace  this page's CSS pixels (a screen item's `rect` is always these)
//   map        the slide map: slide px x scale, less the picture's origin
//   picture    a picture's own coordinates, related to nothing on the live view (a researcher's picture, a result page);
//              geometry in it states its unit: 'px', or 'fraction' (0-1 of its width and height: annotated-picture boxes)
// Pen marks are not built (the UX's pen); when they are they state their space and reach models only as
// prompts on the clean capture.
export type Space = 'frame' | 'capture' | 'workspace' | 'map' | 'picture';
export const SPACES: readonly Space[] = ['frame', 'capture', 'workspace', 'map', 'picture'];
export type PictureUnit = 'px' | 'fraction';
export const PICTURE_UNITS: readonly PictureUnit[] = ['px', 'fraction'];

/** What the server sends that carries geometry, and the space it states (`space` on the message). */
export const WIRES: Record<string, Space> = {
  scene: 'frame', population: 'capture', layer: 'frame', 'motion-tracks': 'capture', 'tracks-layer': 'frame',
  trails: 'frame', magnifier: 'frame', slidemap: 'map', candidates: 'frame',
};
/** A magnifier source's pixels: the live view's frame, the sharpest recent frame's still, a picture. */
export const SOURCE_SPACES: Record<string, Space> = { live: 'frame', held: 'capture', image: 'picture' };
/** What each screen item kind shows, in whose pixels (the magnifier's depends on its source). */
export const ITEM_SPACES: Record<string, Space> = {
  live: 'frame', population: 'capture', tracks: 'capture', outlines: 'frame', viewer: 'picture', slidemap: 'map',
  card: 'workspace', 'made-today': 'workspace', sidebar: 'workspace', sessions: 'workspace', document: 'workspace',
};

export function sourceSpace(kind: string | null | undefined): Space {
  return (kind && SOURCE_SPACES[kind]) || 'picture';
}

/** The space of a screen item: its kind's, or for the magnifier its source's ("live", "held", a URL). */
export function itemSpace(kind: string, source?: string | null): Space {
  if (kind === 'magnifier') return !source ? 'frame' : (SOURCE_SPACES[source] ?? 'picture');
  return ITEM_SPACES[kind] ?? 'workspace';
}

// --- the mappings ------------------------------------------------------------------------------------
export type Pt = [number, number];
export interface Crop { x: number; y: number; width: number; height: number }

/** A place stored at an anchor frame's pixels, in a frame with offset `now` (the same epoch). */
export function frameToFrame(p: Pt, anchor: Pt, now: Pt): Pt {
  return [p[0] + now[0] - anchor[0], p[1] + now[1] - anchor[1]];
}
/** A capture's pixel in the live frame now (a capture is a frame as it was), plus the layer's correction. */
export function captureToFrame(p: Pt, capture: Pt, now: Pt, correction: Pt = [0, 0]): Pt {
  const [x, y] = frameToFrame(p, capture, now);
  return [x + correction[0], y + correction[1]];
}
/** An affine map (a, b, c, d, e, f): (x, y) to (a x + b y + c, d x + e y + f). */
export type Affine = [number, number, number, number, number, number];
/** A still's original pixel in the bridge's frames while the Stage shows it (the layer's `fit.matrix`: the still's
 * orientation, its scale to the fitted size and the letterbox; orchestrator/still_fit.py), and back. */
export const stillToFrame = (p: Pt, m: Affine): Pt => [m[0] * p[0] + m[1] * p[1] + m[2], m[3] * p[0] + m[4] * p[1] + m[5]];
export function frameToStill(p: Pt, m: Affine): Pt {
  const det = m[0] * m[4] - m[1] * m[3], x = p[0] - m[2], y = p[1] - m[5];
  return [(m[4] * x - m[1] * y) / det, (m[0] * y - m[3] * x) / det];
}
export const frameToSlide = (p: Pt, offset: Pt): Pt => [p[0] - offset[0], p[1] - offset[1]];
export const slideToFrame = (p: Pt, offset: Pt): Pt => [p[0] + offset[0], p[1] + offset[1]];
export const slideToMap = (p: Pt, scale: number, origin: Pt): Pt => [p[0] * scale - origin[0], p[1] * scale - origin[1]];
export const mapToSlide = (p: Pt, scale: number, origin: Pt): Pt => [(p[0] + origin[0]) / scale, (p[1] + origin[1]) / scale];
export const frameToMap = (p: Pt, offset: Pt, scale: number, origin: Pt): Pt => slideToMap(frameToSlide(p, offset), scale, origin);
/** An enlarged view's pixel to its capture's pixel: the view is `crop` of the capture drawn at `scale`. */
export const viewToCapture = (p: Pt, crop: Crop, scale: number): Pt => [crop.x + p[0] / scale, crop.y + p[1] / scale];
export const captureToView = (p: Pt, crop: Crop, scale: number): Pt => [(p[0] - crop.x) * scale, (p[1] - crop.y) * scale];

/** A live-frame pixel in the workspace: the frame sits on the canvas at `ox + p * scale` (view.ts `placeFrame`), the
 * canvas at `canvas` in the workspace. */
export const frameToWorkspace = (p: Pt, place: { scale: number; ox: number; oy: number }, canvas: Pt = [0, 0]): Pt =>
  [canvas[0] + place.ox + p[0] * place.scale, canvas[1] + place.oy + p[1] * place.scale];
export const workspaceToFrame = (p: Pt, place: { scale: number; ox: number; oy: number }, canvas: Pt = [0, 0]): Pt =>
  [(p[0] - canvas[0] - place.ox) / place.scale, (p[1] - canvas[1] - place.oy) / place.scale];
/** A live-frame pixel in the workspace through a magnifier showing the frame window at (x0, y0), `k` panel px per
 * frame px (view.ts `magnifierWindow`), its panel at `panel`. */
export const frameToMagnifier = (p: Pt, win: { x0: number; y0: number; k: number }, panel: Pt = [0, 0]): Pt =>
  [panel[0] + (p[0] - win.x0) * win.k, panel[1] + (p[1] - win.y0) * win.k];
export const magnifierToFrame = (p: Pt, win: { x0: number; y0: number; k: number }, panel: Pt = [0, 0]): Pt =>
  [(p[0] - panel[0]) / win.k + win.x0, (p[1] - panel[1]) / win.k + win.y0];
/** A picture's fraction coordinates (0-1) in its pixels, and back. */
export const pictureToPx = (p: Pt, size: Pt): Pt => [p[0] * size[0], p[1] * size[1]];
export const pxToPicture = (p: Pt, size: Pt): Pt => [p[0] / size[0], p[1] / size[1]];
