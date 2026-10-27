import type { FrameCanvas, CanvasFrame } from './scientific-canvas';

/** Observed private preview, not a retained image, native source grant or accepted edit. */
export type ScientificPaintObservation = {
  review: string; frame: string; draw: string; width: number; height: number;
  contentRect: [number, number, number, number];
};

export function scientificPaint(value: unknown): ScientificPaintObservation {
  const fail = () => { throw new Error('The scientific preview has invalid presentation facts.'); };
  if (!value || typeof value !== 'object' || Array.isArray(value)) return fail();
  const row = value as Record<string, unknown>;
  if (Object.keys(row).sort().join(',') !== 'contentRect,draw,frame,height,review,width') return fail();
  for (const key of ['review', 'draw']) if (typeof row[key] !== 'string' || !row[key] || row[key].length > 256) return fail();
  if (typeof row.frame !== 'string' || !/^[a-f0-9]{64}$/.test(row.frame)) return fail();
  for (const key of ['width', 'height']) if (!Number.isSafeInteger(row[key]) || Number(row[key]) < 1 || Number(row[key]) > 1024) return fail();
  const rect = row.contentRect;
  if (!Array.isArray(rect) || rect.length !== 4 || rect.some(n => typeof n !== 'number' || !Number.isFinite(n) || Math.abs(n) > 1e7) || rect[2] <= 0 || rect[3] <= 0) return fail();
  return structuredClone(row) as ScientificPaintObservation;
}

type Binding = { review: string; drawing: FrameCanvas<CanvasFrame> };
const bindings = new WeakMap<HTMLElement, Binding>();
const nativeBindings = new WeakMap<HTMLElement, Binding>();
const documents = new WeakMap<HTMLElement, object>();

export function declareScientificPresentation(root: HTMLElement): () => void {
  const declaration = {};
  documents.set(root, declaration);
  return () => { if (documents.get(root) === declaration) documents.delete(root); };
}

/** Only the owning component binds a root. Copied attributes cannot copy its paint. */
export function bindScientificPresentation(root: HTMLElement, binding: Binding): () => void {
  return bindPresentation(bindings, root, binding);
}

export function bindNativePanelPresentation(root: HTMLElement, binding: Binding): () => void {
  return bindPresentation(nativeBindings, root, binding);
}

function bindPresentation(registry: WeakMap<HTMLElement, Binding>, root: HTMLElement, binding: Binding): () => void {
  if (!binding.review || binding.review.length > 256 || !root.contains(binding.drawing.element()))
    throw new Error('Bind the scientific preview to its owning document window.');
  registry.get(root)?.drawing.invalidate();
  binding.drawing.invalidate();
  const owned = { ...binding };
  registry.set(root, owned);
  return () => { if (registry.get(root) === owned) registry.delete(root); };
}

function paint(root: HTMLElement, binding: Binding | undefined): ScientificPaintObservation | null {
  if (!binding) return null;
  const canvas = binding.drawing.element();
  if (!root.contains(canvas)) return null;
  const current = binding.drawing.current();
  if (!current) return null;
  const rect = canvas.getBoundingClientRect();
  if ([rect.left, rect.top, rect.width, rect.height].some(n => !Number.isFinite(n) || Math.abs(n) > 1e7)
      || rect.width <= 0 || rect.height <= 0) return null;
  return scientificPaint({ review: binding.review, frame: current.frame.frame, draw: current.draw,
    width: current.frame.width, height: current.frame.height,
    contentRect: [rect.left, rect.top, rect.width, rect.height] });
}

export function scientificPresentation(root: HTMLElement): { paint: ScientificPaintObservation | null; panelPaint?: ScientificPaintObservation | null } | null {
  const binding = bindings.get(root);
  const native = nativeBindings.get(root);
  if (!binding && !native && !documents.has(root)) return null;
  return { paint: paint(root, binding), ...(native ? {panelPaint: paint(root, native)} : {}) };
}
