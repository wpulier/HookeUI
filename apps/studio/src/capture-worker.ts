// The window picture's paint and JPEG encode, off the main thread (screen.ts captureWindow, window-paint.ts), so the
// live view never waits for them: the encoder's first use alone took 12-32 ms on the main thread (review 2026-10-29).
import { base64Of, paintSize, paintSteps, type PaintPlan } from './window-paint';

type Job = { id: number; plan: PaintPlan; quality: number };
const reply = (message: unknown) => (self as unknown as { postMessage(message: unknown): void }).postMessage(message);

self.onmessage = async (event: MessageEvent<Job>) => {
  const { id, plan, quality } = event.data;
  try {
    const [w, h] = paintSize(plan);
    const canvas = new OffscreenCanvas(w, h);
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('no 2D canvas in a worker here');
    for (const step of paintSteps(ctx, plan)) void step; // all at once: this thread does nothing else
    const jpeg = await base64Of(await canvas.convertToBlob({ type: 'image/jpeg', quality }));
    reply({ id, jpeg, w, h });
  } catch (error) {
    reply({ id, error: error instanceof Error ? error.message : String(error) });
  } finally {
    for (const p of plan.pictures) p.bitmap?.close();
  }
};
