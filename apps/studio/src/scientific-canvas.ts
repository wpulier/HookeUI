import { scientificViewport, sameScientificViewport, type ScientificViewport } from './scientific-viewport';
import { scientificFrame, type ScientificFrame } from './scientific-view';

export type CanvasFrame = { frame: string; width: number; height: number };
export type ScientificPaint<Frame extends CanvasFrame = ScientificFrame> = { frame: Frame; draw: string };
type Observer<Frame extends CanvasFrame> = { drawn(paint: ScientificPaint<Frame>): void; revoked(draw: string): void };

/** Pixels and local paint evidence only. The workspace owner must admit any interaction. */
export class FrameCanvas<Frame extends CanvasFrame> {
  private readonly decode: (value: unknown) => Frame;
  private readonly canvas: HTMLCanvasElement;
  private readonly observer: Observer<Frame>;
  private paint: ScientificPaint<Frame> | null = null;
  private viewport: ScientificViewport | null = null;
  private generation = 0;
  private pending: { id: number; frame: string } | null = null;
  private waiters = new Set<{ frame: string; resolve(paint: ScientificPaint<Frame>): void; reject(error: Error): void }>();
  private closed = false;
  private lost = false;
  private readonly mutations: MutationObserver;
  private readonly contextLost = () => { this.lost = true; this.invalidate(); };
  private readonly contextRestored = () => { this.lost = false; this.invalidate(); };
  private readonly visibility = () => { if (document.visibilityState === 'hidden') this.invalidate(); };
  private readonly blur = () => this.invalidate();

  constructor(canvas: HTMLCanvasElement, observer: Observer<Frame>, decode: (value: unknown) => Frame) {
    this.canvas = canvas; this.observer = observer; this.decode = decode;
    this.mutations = new MutationObserver(() => this.invalidate());
    this.watch();
    canvas.setAttribute('role', 'img');
    canvas.setAttribute('aria-label', 'Private scientific preview. Changes are not saved.');
    document.addEventListener('visibilitychange', this.visibility);
    window.addEventListener('blur', this.blur);
    canvas.addEventListener('contextlost', this.contextLost);
    canvas.addEventListener('contextrestored', this.contextRestored);
  }

  show(metadata: unknown, pixels: Uint8Array): void {
    if (this.closed) throw new Error('This scientific view is closed.');
    this.invalidate();
    if (this.lost) throw new Error('The scientific preview lost its drawing context. Wait for recovery before retrying.');
    const frame = this.decode(metadata);
    if (!(pixels instanceof Uint8Array) || pixels.byteLength !== frame.width * frame.height * 4)
      throw new Error('The scientific preview pixels do not match their frame.');
    const context = this.canvas.getContext('2d');
    if (!context) throw new Error('This window could not prepare its scientific preview.');
    const image = context.createImageData(frame.width, frame.height);
    image.data.set(pixels);
    this.mutations.disconnect();
    try {
      this.canvas.width = frame.width; this.canvas.height = frame.height;
      this.canvas.style.aspectRatio = `${frame.width} / ${frame.height}`;
      context.putImageData(image, 0, 0);
    } finally { this.watch(); }
    const viewport = scientificViewport(this.canvas);
    const generation = this.generation;
    const id = requestAnimationFrame(() => {
      if (this.closed || generation !== this.generation) return;
      this.pending = null;
      this.checkBitmap();
      if (this.closed || generation !== this.generation) return;
      if (!this.visible()) { this.invalidate(); return; }
      if (!sameScientificViewport(viewport, scientificViewport(this.canvas))) { this.invalidate(); return; }
      this.viewport = viewport;
      const paint = { frame, draw: crypto.randomUUID() };
      this.paint = paint;
      try { this.observer.drawn(structuredClone(paint)); }
      catch (error) { this.invalidate(); throw error; }
      if (this.paint === paint) {
        // Observation only: events never grant interaction or replace current()'s owned receipt.
        this.canvas.dispatchEvent(new CustomEvent('scientific-frame-drawn', { detail: { frame: frame.frame, draw: paint.draw } }));
        for (const waiter of this.waiters) if (waiter.frame === frame.frame) {
          this.waiters.delete(waiter); waiter.resolve(structuredClone(paint));
        }
      }
    });
    this.pending = { id, frame: frame.frame };
  }

  /** This exact frame has pixels, but no browser paint receipt yet. */
  awaiting(frame: string): boolean {
    this.checkBitmap();
    return this.pending?.frame === frame && this.visible();
  }

  /** Resolve only this canvas's matching receipt; cancellation never certifies submitted pixels. */
  async whenDrawn(frame: string): Promise<ScientificPaint<Frame>> {
    const current = this.current();
    if (current?.frame.frame === frame) return current;
    if (!this.awaiting(frame)) throw new Error('The requested scientific frame was not drawn. Check the current view.');
    return new Promise((resolve, reject) => this.waiters.add({ frame, resolve, reject }));
  }

  current(): ScientificPaint<Frame> | null {
    this.checkBitmap();
    if (!this.visible() || this.paint && (this.paint.frame.width !== this.canvas.width || this.paint.frame.height !== this.canvas.height)) this.invalidate();
    if (this.paint && this.viewport && !sameScientificViewport(this.viewport, scientificViewport(this.canvas))) this.invalidate();
    return this.paint ? structuredClone(this.paint) : null;
  }

  element(): HTMLCanvasElement { return this.canvas; }

  position(draw: string, clientX: number, clientY: number): [number, number] {
    const paint = this.current(), rect = this.canvas.getBoundingClientRect();
    if (!paint || paint.draw !== draw || !Number.isFinite(clientX) || !Number.isFinite(clientY) ||
        clientX < rect.left || clientY < rect.top || clientX >= rect.right || clientY >= rect.bottom)
      throw new Error('Wait for the current scientific preview before interacting.');
    return [(clientY - rect.top) * paint.frame.height / rect.height,
      (clientX - rect.left) * paint.frame.width / rect.width];
  }

  invalidate(): void {
    ++this.generation;
    if (this.pending !== null) cancelAnimationFrame(this.pending.id);
    this.pending = null;
    for (const waiter of this.waiters) waiter.reject(new Error('The requested scientific frame was not drawn. Check the current view.'));
    this.waiters.clear();
    const old = this.paint;
    this.paint = null; this.viewport = null;
    if (old) this.observer.revoked(old.draw);
  }

  clear(): void {
    this.invalidate();
    this.canvas.width = 0; this.canvas.height = 0;
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    document.removeEventListener('visibilitychange', this.visibility);
    window.removeEventListener('blur', this.blur);
    this.canvas.removeEventListener('contextlost', this.contextLost);
    this.canvas.removeEventListener('contextrestored', this.contextRestored);
    this.mutations.disconnect();
    try { this.invalidate(); }
    finally { this.canvas.width = 0; this.canvas.height = 0; }
  }

  private visible(): boolean {
    if (this.closed || this.lost || !this.canvas.isConnected || document.visibilityState === 'hidden' ||
        this.canvas.width < 1 || this.canvas.height < 1) return false;
    const rect = this.canvas.getBoundingClientRect();
    if (![rect.left, rect.top, rect.width, rect.height].every(Number.isFinite) || rect.width <= 0 || rect.height <= 0) return false;
    for (let node: HTMLElement | null = this.canvas; node; node = node.parentElement) {
      const style = getComputedStyle(node);
      if (node.hidden || style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse' || Number(style.opacity) === 0) return false;
      if ([style.transform, style.rotate, style.scale].some(value => value && value !== 'none')) return false;
      if (node === this.canvas && style.objectFit && style.objectFit !== 'fill') return false;
      if (node === this.canvas && [style.borderLeftWidth, style.borderRightWidth, style.borderTopWidth, style.borderBottomWidth,
        style.paddingLeft, style.paddingRight, style.paddingTop, style.paddingBottom].some(v => v && parseFloat(v) !== 0)) return false;
    }
    return true;
  }

  private watch(): void { this.mutations.observe(this.canvas, { attributes: true, attributeFilter: ['width', 'height'] }); }
  private checkBitmap(): void { if (this.mutations.takeRecords().length) this.invalidate(); }
}

/** The existing scientific-image path keeps its exact validator. */
export class ScientificCanvas extends FrameCanvas<ScientificFrame> {
  constructor(canvas: HTMLCanvasElement, observer: Observer<ScientificFrame>) { super(canvas, observer, scientificFrame); }
}
