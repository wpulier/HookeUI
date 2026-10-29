// Painting a captured window (screen.ts captureWindow) from plain data, with no page to read: the pictures as
// ImageBitmaps, each window and panel's place and colour, and the text at its place. It runs in capture-worker.ts, off
// the main thread, so the live view never waits for it; a browser without workers' canvases paints it here in short
// steps instead (paintSteps yields after each).

type Box = { left: number; top: number; width: number; height: number };
export type PaintText = { text: string; rect: Box; kind: 'title' | 'tab' | 'cell' | 'header' | 'heading' | 'row'; selected?: boolean };
export interface PaintPlan {
  view: [number, number];
  scale: number;
  background: string;
  pictures: { bitmap: ImageBitmap | null; rect: Box; frame: number | null; opacity: number }[];
  frames: { rect: Box; background: string }[];
  text: PaintText[];
}
type Context = CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D;

/** The picture's size for a plan (at most its maxSide). */
export function paintSize(plan: Pick<PaintPlan, 'view' | 'scale'>): [number, number] {
  return [Math.max(1, Math.round(plan.view[0] * plan.scale)), Math.max(1, Math.round(plan.view[1] * plan.scale))];
}

/** Paint the plan on `ctx`, yielding after each step (the background and the live view, each window, each line). */
export function* paintSteps(ctx: Context, plan: PaintPlan): Generator<void, void> {
  ctx.scale(plan.scale, plan.scale);
  ctx.fillStyle = plan.background;
  ctx.fillRect(0, 0, plan.view[0], plan.view[1]);
  const draw = (p: PaintPlan['pictures'][number]) => {
    if (p.bitmap) {
      ctx.globalAlpha = p.opacity;
      ctx.drawImage(p.bitmap, p.rect.left, p.rect.top, p.rect.width, p.rect.height);
      ctx.globalAlpha = 1;
    } else {
      // a picture that cannot be read back (another origin's, not loaded): its place is drawn instead
      ctx.strokeStyle = '#888';
      ctx.strokeRect(p.rect.left, p.rect.top, p.rect.width, p.rect.height);
    }
  };
  // Pictures in no window (the live view) first, then each window over them with its own pictures.
  plan.pictures.forEach((p) => { if (p.frame === null) draw(p); });
  yield;
  for (const [index, f] of plan.frames.entries()) {
    const bg = f.background;
    if (bg && bg !== 'transparent' && !/rgba\([^)]*,\s*0\)$/.test(bg)) { ctx.fillStyle = bg; ctx.fillRect(f.rect.left, f.rect.top, f.rect.width, f.rect.height); }
    plan.pictures.forEach((p) => { if (p.frame === index) draw(p); });
    ctx.strokeStyle = '#cfd6e6';
    ctx.lineWidth = 1;
    ctx.strokeRect(f.rect.left + 0.5, f.rect.top + 0.5, f.rect.width - 1, f.rect.height - 1);
    yield;
  }
  ctx.textBaseline = 'middle';
  for (const t of plan.text) {
    const size = t.kind === 'title' || t.kind === 'heading' ? 13 : 12;
    ctx.font = `${t.kind === 'title' || t.kind === 'header' || t.kind === 'heading' ? 600 : 400} ${size}px system-ui, sans-serif`;
    const y = t.kind === 'title' ? t.rect.top + 11 : t.rect.top + Math.min(t.rect.height, 24) / 2;
    const width = Math.min(ctx.measureText(t.text).width, Math.max(8, t.rect.width - 8));
    ctx.fillStyle = t.selected ? 'rgba(47, 111, 214, 0.95)' : 'rgba(16, 18, 24, 0.82)';
    ctx.fillRect(t.rect.left + 2, y - size / 2 - 2, width + 8, size + 4);
    ctx.fillStyle = t.kind === 'title' ? '#ffffff' : '#e7ecf7';
    ctx.fillText(t.text, t.rect.left + 6, y, Math.max(8, t.rect.width - 8));
    yield;
  }
}

/** A JPEG's bytes as base64. */
export async function base64Of(blob: Blob): Promise<string> {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let binary = '';
  for (let at = 0; at < bytes.length; at += 8192) binary += String.fromCharCode(...bytes.subarray(at, at + 8192));
  return btoa(binary);
}
