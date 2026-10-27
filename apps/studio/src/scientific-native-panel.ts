import { FrameCanvas } from './scientific-canvas';
import { bindNativePanelPresentation } from './scientific-presentation';
import { nativePanelFrame, nativeKeys, type NativePanelFrame, type NativePanelCommand } from './scientific-native-panel-wire';

type Options = {
  changed?(): void;
  load(width: number, height: number): Promise<{ frame: unknown; pixels: Uint8Array }>;
  send(command: NativePanelCommand, draw: string): Promise<void>;
};
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};

/** Explicitly declared plugin UI. Its paint receipt is independent of the image. */
export class ScientificNativePanel {
  readonly element = node('details');
  private readonly canvas = node('canvas');
  private readonly status = node('p', 'Load the panel to use this tool’s controls.');
  private readonly refresh = node('button', 'Open plugin panel');
  private readonly accessible = node('div');
  private readonly problem = node('details');
  private readonly problemText = node('p');
  private readonly text = node('input');
  private readonly enterText = node('button', 'Type in selected field');
  private readonly drawing: FrameCanvas<NativePanelFrame>;
  private unbind = () => {};
  private review: string | null = null;
  private busy = false;
  private blocked = true;
  private blockedReason = 'Open the scientific session first.';
  private disposed = false;
  private available = false;
  private loaded = false;
  private textRevision = 0;
  private gesture: { pointer: number; draw: string; points: number[][]; button: 'left' | 'right' } | null = null;

  private readonly root: HTMLElement;
  private readonly options: Options;
  constructor(root: HTMLElement, options: Options) {
    this.root = root; this.options = options;
    this.element.className = 'scientific-native-panel'; this.element.hidden = true; this.element.open = true;
    this.canvas.tabIndex = 0; this.canvas.style.width = '100%'; this.canvas.style.touchAction = 'none'; this.canvas.style.padding = '0'; this.canvas.style.border = '0';
    this.drawing = new FrameCanvas(this.canvas, { drawn: () => this.buttons(), revoked: () => { this.gesture = null; this.buttons(); } }, nativePanelFrame);
    this.canvas.setAttribute('aria-label', 'Interactive plugin controls. Escape returns focus to Refresh panel.');
    this.status.setAttribute('role', 'status');
    this.problem.hidden = true; this.problem.className = 'scientific-native-panel-problem';
    const dismissProblem = node('button', 'Dismiss previous problem');
    dismissProblem.addEventListener('click', () => { this.problem.hidden = true; this.problemText.textContent = ''; });
    this.problem.append(node('summary', 'Previous panel problem'), this.problemText, dismissProblem);
    this.text.setAttribute('aria-label', 'Text to type in the selected plugin field'); this.text.maxLength = 4000;
    this.refresh.addEventListener('click', () => void this.load());
    this.text.addEventListener('input', () => { ++this.textRevision; this.options.changed?.(); });
    this.enterText.addEventListener('click', () => {
      const revision = this.textRevision, value = this.text.value;
      if (value) void this.send('text', value).then(ok => {
        if (ok && revision === this.textRevision && this.text.value === value) {
          this.text.value = ''; this.options.changed?.();
        }
      });
    });
    this.canvas.addEventListener('pointerdown', event => {
      const paint = this.drawing.current();
      if (this.disabled() || !paint || ![0, 2].includes(event.button)) return;
      event.preventDefault(); this.canvas.focus({preventScroll: true});
      this.gesture = { pointer: event.pointerId, draw: paint.draw, points: [this.point(paint.draw, event)], button: event.button === 2 ? 'right' : 'left' };
      this.canvas.setPointerCapture(event.pointerId);
    });
    this.canvas.addEventListener('pointermove', event => {
      const gesture = this.gesture;
      if (!gesture || gesture.pointer !== event.pointerId) return;
      try {
        const point = this.point(gesture.draw, event);
        const last = gesture.points.at(-1)!;
        if (last[0] === point[0] && last[1] === point[1]) return;
        if (gesture.points.length < 256) gesture.points.push(point);
        else this.cancelGesture('Gesture is too long. Start a shorter stroke.');
      } catch { this.cancelGesture('Pointer left the current panel. Start the gesture again.'); }
    });
    this.canvas.addEventListener('pointerup', event => {
      const gesture = this.gesture;
      if (!gesture || gesture.pointer !== event.pointerId) return;
      this.gesture = null;
      try {
        const point = this.point(gesture.draw, event);
        if (gesture.points.length === 1 && gesture.button === 'left') void this.send('click', point);
        else {
          if (gesture.points.length === 1) gesture.points.push(point);
          else gesture.points[gesture.points.length - 1] = point;
          void this.send('drag', { points: gesture.points, button: gesture.button, cancel: false });
        }
      } catch { this.cancelGesture('The panel changed. Refresh it before another gesture.'); }
      finally { if (this.canvas.hasPointerCapture(event.pointerId)) this.canvas.releasePointerCapture(event.pointerId); }
    });
    for (const event of ['pointercancel', 'lostpointercapture']) this.canvas.addEventListener(event, () => this.cancelGesture('Gesture cancelled before it was sent.'));
    this.canvas.addEventListener('contextmenu', event => event.preventDefault());
    this.canvas.addEventListener('wheel', event => {
      const paint = this.drawing.current(); if (!paint || this.disabled() || event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      const delta = [event.deltaX, event.deltaY].map(n => Math.max(-1200, Math.min(1200, Math.round(-n))));
      try { if (delta.some(Boolean)) void this.send('wheel', {point: this.point(paint.draw, event), angleDelta: delta}); }
      catch { this.status.textContent = 'Refresh the panel before scrolling its controls.'; }
    }, {passive: false});
    this.canvas.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        this.cancelGesture('Gesture cancelled.'); this.refresh.focus(); return;
      }
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      if (event.key.length === 1 && event.key !== ' ') {
        event.preventDefault();
        if (this.text.value.length < this.text.maxLength) { this.text.value += event.key; ++this.textRevision; }
        this.text.focus();
        this.options.changed?.();
        this.status.textContent = 'Text is kept here. Press Type in selected field when your text is complete.';
        return;
      }
      if (this.disabled()) return;
      const key = event.key === 'Tab' && event.shiftKey ? 'Backtab' : event.key === ' ' ? 'Space' : event.key.replace(/^Arrow/, '');
      if (nativeKeys.has(key)) { event.preventDefault(); void this.send('key', key); }
    });
    this.element.addEventListener('toggle', () => { if (!this.element.open) this.drawing.invalidate(); });
    const actions = node('div'); actions.className = 'scientific-native-panel-actions';
    actions.append(this.refresh, this.status);
    const help = node('details');
    help.append(node('summary', 'Keyboard and saving'),
      node('p', 'Select a plugin field, then enter text below and choose Type in selected field. Escape returns focus to Refresh panel.'),
      node('p', 'Panel actions change private work. Use Save to keep scientific layers and supported tool state. Closing the panel keeps the session running.'));
    this.element.append(node('summary', 'Plugin panel'), actions, this.problem, this.canvas,
      this.text, this.enterText, this.accessible, help);
  }

  bind(review: string | null): void {
    if (review !== this.review) { this.problem.hidden = true; this.problemText.textContent = ''; }
    this.unbind(); this.loaded=false; this.review = review; this.drawing.invalidate();
    this.status.textContent = 'Load the panel to use this tool’s controls.';
    this.refresh.textContent = 'Open plugin panel';
    this.unbind = review ? bindNativePanelPresentation(this.root, {review, drawing: this.drawing}) : () => {};
    this.buttons();
  }
  gesturing(): boolean { return this.gesture !== null; }
  interacting(): boolean { return this.busy || this.gesture !== null; }
  async refreshObserved(): Promise<void> {
    if (!this.interacting() && !this.hasDraft() && !this.disabled() && this.element.open) await this.load(true);
  }
  /** Re-render only an already opened panel after its presentation moves. This never dispatches a plugin action. */
  layoutChanged(): void {
    if(this.loaded && this.element.open && !this.interacting()) void this.load();
  }
  hasDraft(): boolean { return this.text.value.length > 0; }
  setAvailable(value: boolean): void { this.available = value; this.element.hidden = !value; if (!value) this.drawing.invalidate(); this.buttons(); }
  setBlocked(value: boolean, reason = 'Finish or cancel the current scientific action before opening these controls.'): void { this.blocked = value; this.blockedReason = reason; this.refresh.title = value ? reason : ''; this.buttons(); }
  invalidate(): void { this.drawing.invalidate(); }
  close(): void { this.disposed = true; this.unbind(); this.drawing.close(); }
  private disabled(): boolean { return this.disposed || this.busy || this.blocked || !this.review || !this.available; }
  private buttons(): void {
    // Keep the initiating control in the keyboard sequence during an owned request.
    // load() still enforces admission, including a blocked or repeated activation.
    this.refresh.disabled = this.disposed || !this.review || !this.available;
    this.refresh.setAttribute('aria-disabled', String(this.disabled()));
    this.enterText.disabled = this.disabled() || !this.drawing.current();
  }
  private point(draw: string, event: {clientX: number; clientY: number}): number[] {
    const [y, x] = this.drawing.position(draw, event.clientX, event.clientY); return [Math.floor(x), Math.floor(y)];
  }
  private cancelGesture(message: string): void { if (this.gesture) { this.gesture = null; this.status.textContent = message; } }
  private async load(propagate = false): Promise<void> {
    if (this.disabled() || !this.element.open) {
      if (this.blocked && this.element.open) this.status.textContent = this.blockedReason;
      return;
    }
    this.busy = true; this.buttons(); this.status.textContent = 'Loading plugin controls…';
    const review = this.review;
    try {
      this.drawing.invalidate();
      const result = await this.options.load(480, 420);
      if (this.disposed || this.review !== review || !this.element.open) return;
      this.show(result); this.refresh.textContent = 'Refresh panel';
      this.status.textContent = 'Plugin controls ready.';
    } catch (error) {
      if (this.disposed || this.review !== review || !this.element.open) return;
      this.drawing.invalidate();
      this.status.textContent = `${error instanceof Error ? error.message : String(error)} Open the panel again to retry. This does not repeat a plugin action.`;
      this.rememberProblem();
      if (propagate) throw error;
    }
    finally { this.busy = false; this.buttons(); }
  }
  private async send(op: string, value: unknown): Promise<boolean> {
    const paint = this.drawing.current(); if (this.disabled() || !paint) return false;
    const review = this.review; this.busy = true; this.buttons(); this.status.textContent = 'Applying plugin action…';
    let acknowledged = false;
    try {
      await this.options.send({panel: paint.frame.panel, frame: paint.frame.frame, op, value}, paint.draw);
      if (this.disposed || review !== this.review) return false;
      acknowledged = true;
      this.drawing.invalidate();
      const rect = this.canvas.getBoundingClientRect();
      if (!this.element.open || rect.width <= 0 || rect.height <= 0) {
        this.status.textContent = 'Plugin interaction delivered. Open the panel and refresh to inspect its current state.';
        return true;
      }
      const result = await this.options.load(480, 420);
      if (this.disposed || review !== this.review) return false;
      if (!this.element.open) {
        this.status.textContent = 'Plugin interaction delivered. Open the panel and refresh to inspect its current state.';
        return true;
      }
      this.show(result); this.status.textContent = 'Plugin interaction delivered. Check the tool’s progress and results before saving.'; return true;
    } catch (error) {
      if (this.disposed || review !== this.review) return false;
      this.drawing.invalidate();
      const reason = error instanceof Error ? error.message : String(error);
      this.status.textContent = `${acknowledged ? 'Plugin interaction delivered; its panel refresh failed. ' : ''}${reason} Refresh panel to inspect current state. The action is not replayed.`;
      this.rememberProblem();
      return acknowledged;
    } finally { this.busy = false; this.buttons(); }
  }
  private rememberProblem(): void {
    this.problemText.textContent = this.status.textContent;
    this.problem.hidden = false; this.problem.open = true;
  }
  private show(result: {frame: unknown; pixels: Uint8Array}): void {
    const frame = nativePanelFrame(result.frame); this.drawing.show(frame, result.pixels);this.loaded=true;
    const list = node('ul');
    for (const row of frame.accessibility.filter(row => !row.invisible && (row.name || row.value)))
      list.append(node('li', `${row.name || row.role}${row.value ? `: ${row.value}` : ''}${row.disabled ? ' (unavailable)' : ''}${row.focused ? ' (selected)' : ''}`));
    const details = node('details'); details.append(node('summary', 'Accessible control descriptions'), list); this.accessible.replaceChildren(details);
  }
}
