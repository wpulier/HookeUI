import type { ScientificCanvas, ScientificPaint } from './scientific-canvas';
import type { ScientificFrame } from './scientific-view';

/** A Points or Shapes layer's editing state, as the worker reports it (env_worker/scientific_annotate.py summary). */
export type AnnotationState = { mode: string; selected: number; creating: boolean; undo: number; redo: number };
type PointerRow = { kind: 'move' | 'press' | 'release' | 'double'; point: number[]; modifiers: string[] };
type KeyRow = { kind: 'key'; key: 'Shift'; down: boolean };
export type AnnotateChange = { operation: 'annotate'; value: { layer: string; mode: string; events: Array<PointerRow | KeyRow> } |
  { layer: string; action: string } | { layer: string; history: 'undo' | 'redo' } };
type Options = { send(change: AnnotateChange, frame: string): Promise<void>; changed(): void; labelsEditing(): boolean };

const modes = {
  points: [['pan_zoom', 'Inspect'], ['add', 'Add points'], ['select', 'Select points']],
  shapes: [['pan_zoom', 'Inspect'], ['select', 'Select shapes'], ['direct', 'Direct select (vertices)'], ['vertex_insert', 'Insert vertex'],
    ['vertex_remove', 'Remove vertex'], ['add_rectangle', 'Rectangle'], ['add_ellipse', 'Ellipse'], ['add_line', 'Line'],
    ['add_path', 'Path (drag)'], ['add_polyline', 'Polyline (click)'], ['add_polygon', 'Polygon (click)'], ['add_polygon_lasso', 'Lasso polygon']],
} as const;
/** napari's action that activates each mode (the Points and Shapes key bindings modules). */
const activate: Record<string, string> = {
  'points:add': 'activate_points_add_mode', 'points:select': 'activate_points_select_mode', 'points:pan_zoom': 'activate_points_pan_zoom_mode',
  'shapes:select': 'activate_select_mode', 'shapes:direct': 'activate_direct_mode', 'shapes:vertex_insert': 'activate_vertex_insert_mode',
  'shapes:vertex_remove': 'activate_vertex_remove_mode', 'shapes:pan_zoom': 'activate_shapes_pan_zoom_mode',
  ...Object.fromEntries(['rectangle','ellipse','line','path','polyline','polygon','polygon_lasso'].map(m => [`shapes:add_${m}`, `activate_add_${m}_mode`])),
};
/** napari's default shortcuts (napari/utils/shortcuts.py), on the focused preview. `mod` is Cmd on macOS, Ctrl elsewhere. */
export const annotationShortcuts: Record<'points' | 'shapes', Record<string, string>> = {
  points: { '2': 'activate_points_add_mode', p: 'activate_points_add_mode', '3': 'activate_points_select_mode', s: 'activate_points_select_mode',
    '4': 'activate_points_pan_zoom_mode', z: 'activate_points_pan_zoom_mode', a: 'select_all_in_slice', 'shift+a': 'select_append_all_in_slice',
    'mod+a': 'select_all_data', '1': 'delete_selected_points', Delete: 'delete_selected_points', Backspace: 'delete_selected_points' },
  shapes: { r: 'activate_add_rectangle_mode', e: 'activate_add_ellipse_mode', l: 'activate_add_line_mode', t: 'activate_add_path_mode',
    'shift+l': 'activate_add_polyline_mode', p: 'activate_add_polygon_mode', 'shift+p': 'activate_add_polygon_lasso_mode',
    '4': 'activate_direct_mode', d: 'activate_direct_mode', '5': 'activate_select_mode', s: 'activate_select_mode',
    '6': 'activate_shapes_pan_zoom_mode', z: 'activate_shapes_pan_zoom_mode', '2': 'activate_vertex_insert_mode', i: 'activate_vertex_insert_mode',
    '1': 'activate_vertex_remove_mode', x: 'activate_vertex_remove_mode', 'mod+c': 'copy_selected_shapes', 'mod+v': 'paste_shape',
    f: 'move_shapes_selection_to_front', b: 'move_shapes_selection_to_back', a: 'select_shapes_in_slice', 'mod+a': 'select_shapes_in_slice',
    '3': 'delete_selected_shapes', Delete: 'delete_selected_shapes', Backspace: 'delete_selected_shapes',
    Enter: 'finish_drawing_shape', Escape: 'finish_drawing_shape' },
};
const buttons: Record<'points' | 'shapes', Array<[string, string]>> = {
  points: [['select_all_in_slice', 'Select all in slice'], ['select_all_data', 'Select all points'], ['delete_selected_points', 'Delete selected points'],
    ['copy', 'Copy points'], ['paste', 'Paste points']],
  shapes: [['finish_drawing_shape', 'Finish shape'], ['select_shapes_in_slice', 'Select shapes in slice'], ['delete_selected_shapes', 'Delete selected shapes'],
    ['copy_selected_shapes', 'Copy shapes'], ['paste_shape', 'Paste shapes'], ['move_shapes_selection_to_front', 'Bring to front'],
    ['move_shapes_selection_to_back', 'Send to back']],
};
const CLICK_MODES = ['add_polyline', 'add_polygon'];
const MAX_EVENTS = 512;
const element = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
const words = (error: unknown) => error instanceof Error ? error.message : String(error);

export function annotationState(raw: unknown): AnnotationState {
  const row = raw as Record<string, unknown>;
  if (!row || typeof row !== 'object' || Array.isArray(row) || Object.keys(row).sort().join() !== 'creating,mode,redo,selected,undo' ||
      typeof row.mode !== 'string' || !/^[a-z_]{1,32}$/.test(row.mode) || typeof row.creating !== 'boolean' ||
      ['selected', 'undo', 'redo'].some(key => !Number.isInteger(row[key]) || Number(row[key]) < 0))
    throw new Error('The annotation state is not supported. Keep the original and reopen the session.');
  return row as AnnotationState;
}

/** Points and Shapes editing on the preview: every gesture is napari's own (the worker dispatches it to napari). */
export class ScientificAnnotate {
  private frame: ScientificFrame | null = null;
  private available = false;
  private session: string | null = null;
  private generation = 0;
  private closed = false;
  private busy = false;
  private queuedMode: string | null = null;  // chosen while a request was in flight; sent once the panel is idle
  private pendingClick: { pointer: number; layer: string; mode: string; finish: boolean } | null = null;
  private gesture: { pointer: number; mouse: boolean; paint: ScientificPaint; events: Array<PointerRow | KeyRow>; shift: boolean } | null = null;
  /** Unpressed pointer moves while napari is still creating a shape (path or lasso click-move, a polygon's moving
   *  vertex): napari takes them in order, so they go with the next click or action. */
  private hovering: { paint: ScientificPaint; events: PointerRow[] } | null = null;
  private readonly layer = element('select');
  private readonly mode = element('select');
  private readonly status = element('p', 'Choose a Points or Shapes layer and a mode. Edits stay private until saved.');
  private readonly actionRow = element('div');
  private readonly undoButton = element('button', 'Undo annotation edit');
  private readonly redoButton = element('button', 'Redo annotation edit');
  private readonly canvas: HTMLCanvasElement;
  private readonly root: HTMLElement;
  private readonly drawing: ScientificCanvas;
  private readonly options: Options;
  private readonly down = (event: PointerEvent) => this.begin(event);
  private readonly mouseDown = (event: MouseEvent) => this.countClick(event);
  private readonly move = (event: PointerEvent) => this.extend(event);
  private readonly up = (event: PointerEvent) => this.finish(event);
  private readonly double = (event: MouseEvent) => this.doubleClick(event);
  private readonly lost = (event: PointerEvent) => {
    if (this.pendingClick?.finish && this.pendingClick.pointer === event.pointerId) this.pendingClick = null;
    if (this.gesture?.pointer === event.pointerId) this.cancel('Unsent annotation gesture cancelled.');
  };
  private readonly blur = () => { this.pendingClick = null; this.cancel('Unsent annotation gesture cancelled. Inspect the current preview before drawing.'); };
  private readonly key = (event: KeyboardEvent) => { if (!event.defaultPrevented) this.keyboard(event); };
  private readonly previewKey = (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    this.keyboard(event);
    if (event.defaultPrevented) event.stopPropagation();  // the editor owns this key before workspace dismissal
  };
  private readonly keyUp = (event: KeyboardEvent) => { if (event.key === 'Shift' && this.gesture?.shift) { this.gesture.events.push({ kind: 'key', key: 'Shift', down: false }); this.gesture.shift = false; } };

  constructor(root: HTMLElement, drawing: ScientificCanvas, options: Options) {
    this.root = root; this.drawing = drawing; this.options = options; this.canvas = drawing.element();
    const field = (name: string, input: HTMLElement) => { input.setAttribute('aria-label', name); const label = element('label', name); label.append(input); root.append(label); };
    field('Annotation layer', this.layer); field('Annotation mode', this.mode);
    this.status.setAttribute('role', 'status');
    this.undoButton.addEventListener('click', () => void this.history('undo'));
    this.redoButton.addEventListener('click', () => void this.history('redo'));
    root.append(this.actionRow, this.undoButton, this.redoButton, this.status,
      element('p', 'Points and Shapes are edited by napari itself: each drag is sent when released; Polyline and Polygon send each click, and Enter, Escape or a double click finishes the shape. Hold Shift to lock a shape\'s aspect ratio. napari\'s keyboard shortcuts work on the focused preview. Undo and Redo step through this session\'s annotation edits.'));
    this.layer.addEventListener('change', () => { this.invalidate(); this.showModes(); this.paint(); });
    this.mode.addEventListener('change', () => {
      this.pendingClick = null; this.cancel();
      if (this.busy) { this.queuedMode = this.mode.value; return; }  // the select keeps focus; the latest choice is sent next
      const type = this.type(); if (type) void this.action(activate[`${type}:${this.mode.value}`]);
    });
    this.canvas.addEventListener('pointerdown', this.down); this.canvas.addEventListener('mousedown', this.mouseDown); this.canvas.addEventListener('pointermove', this.move);
    this.canvas.addEventListener('pointerup', this.up); this.canvas.addEventListener('pointercancel', this.lost);
    this.canvas.addEventListener('lostpointercapture', this.lost); this.canvas.addEventListener('dblclick', this.double);
    this.canvas.addEventListener('keydown', this.previewKey);
    window.addEventListener('blur', this.blur); window.addEventListener('keydown', this.key); window.addEventListener('keyup', this.keyUp);
    this.paint();
  }
  /** Busy with an unsent gesture or a request: the preview must not change under it. */
  blocked(): boolean { return !!this.gesture || this.busy; }
  /** Whether an annotation mode is taking the preview's pointer (so other editors stand back). */
  editing(): boolean { return this.ready() && this.mode.value !== 'pan_zoom'; }
  /** napari sends a shortcut to the active layer: this panel takes the preview's keys while one of its modes is on or
   *  its layer is napari's active layer. */
  ownsKeys(): boolean { return this.editing() || (this.ready() && !!this.layer.value && this.frame?.layerSelection?.active === this.layer.value); }
  setAvailable(value: boolean): void { this.available = value; if (!value) this.cancel(); this.paint(); }
  revoke(): void { this.cancel('The preview changed. The unsent annotation gesture was cancelled.'); }
  bindSession(session: string | null): void {
    if (session === this.session) return;
    this.invalidate(); this.session = session; this.frame = null;
    this.layer.replaceChildren(); delete this.layer.dataset.signature; this.paint();
  }
  private invalidate(): void {
    this.generation++; this.cancel(); this.pendingClick = null; this.requestLayer = null; this.hovering = null; this.queuedMode = null; this.busy = false;
  }
  update(frame: ScientificFrame): void {
    if (this.gesture && this.gesture.paint.frame.frame !== frame.frame) this.cancel('The preview changed. The unsent annotation gesture was cancelled.');
    this.frame = frame;
    const rows = frame.layers.filter(row => (row.type === 'points' || row.type === 'shapes') && row.annotation && row.visible);
    const signature = JSON.stringify(rows.map(row => [row.id, row.name, row.type]));
    if (this.layer.dataset.signature !== signature) {
      const selected = this.layer.value; this.layer.replaceChildren();
      for (const row of rows) { const option = element('option', `${row.name} (${row.type})`); option.value = row.id; this.layer.append(option); }
      this.layer.value = rows.some(row => row.id === selected) ? selected : (rows[0]?.id ?? ''); this.layer.dataset.signature = signature;
    }
    if (this.requestLayer && this.requestLayer !== this.layer.value) this.invalidate();
    this.showModes(); this.paint();
  }
  private requestLayer: string | null = null;
  private type(): 'points' | 'shapes' | null {
    const row = this.frame?.layers.find(layer => layer.id === this.layer.value);
    return row?.type === 'points' || row?.type === 'shapes' ? row.type : null;
  }
  private state(): AnnotationState | undefined { return this.frame?.layers.find(layer => layer.id === this.layer.value)?.annotation; }
  private showModes(): void {
    const type = this.type();
    if (this.mode.dataset.type !== (type ?? '')) {
      this.mode.replaceChildren(); this.mode.dataset.type = type ?? '';
      for (const [value, title] of type ? modes[type] : []) { const option = element('option', title); option.value = value; this.mode.append(option); }
      this.actionRow.replaceChildren();
      for (const [action, title] of type ? buttons[type] : []) {
        const button = element('button', title); button.addEventListener('click', () => void this.action(action)); this.actionRow.append(button);
      }
    }
    const state = this.state();
    if (state && document.activeElement !== this.mode && this.queuedMode === null) this.mode.value = [...this.mode.children].some(o => (o as HTMLOptionElement).value === state.mode) ? state.mode : 'pan_zoom';
  }
  private ready(): boolean {
    return !this.closed && !!this.session && this.available && !!this.frame && this.frame.dimensions.ndisplay === 2 && !this.frame.grid?.enabled && !!this.type();
  }
  private async send(value: AnnotateChange['value'], frame: string): Promise<boolean> {
    const generation = this.generation;
    this.requestLayer = value.layer; this.busy = true; this.changed();
    const current = () => !this.closed && generation === this.generation && value.layer === this.layer.value;
    try {
      await this.options.send({ operation: 'annotate', value }, frame);
      if (!current()) return false;
      this.status.textContent = 'Annotation edit applied by napari in this private view. Save outputs to keep it.';
      return true;
    } catch (error) {
      if (current()) { this.status.textContent = words(error); this.queuedMode = null; }
      return false;
    } finally {
      if (current()) {
        this.requestLayer = null; this.busy = false; this.changed();
        setTimeout(() => { if (current()) this.sendQueuedMode(); }, 0);
      }
    }
  }
  /** Runs after a request's own continuation (an action's second request starts first), so requests never overlap. */
  private sendQueuedMode(): void {
    const mode = this.queuedMode, type = this.type();
    if (this.busy || mode === null) return;
    this.queuedMode = null;
    if (type && this.ready() && mode === this.mode.value && mode !== this.state()?.mode) void this.action(activate[`${type}:${mode}`]);
    else this.showModes();
  }
  private async action(action: string | undefined): Promise<void> {
    if (!action || !this.ready() || this.blocked() || !this.frame) return;
    const generation = this.generation, layer = this.layer.value, mode = this.mode.value;
    const moves = this.hovering?.paint.frame.frame === this.frame.frame ? this.hovering.events : [];
    this.hovering = null;
    if (moves.length && mode !== 'pan_zoom' && !await this.send({ layer, mode, events: moves }, this.frame.frame)) return;
    if (!this.closed && generation === this.generation && layer === this.layer.value && this.ready() && this.frame)
      await this.send({ layer, action }, this.frame.frame);
  }
  private async history(which: 'undo' | 'redo'): Promise<void> {
    if (!this.ready() || this.blocked() || !this.frame) return;
    await this.send({ layer: this.layer.value, history: which }, this.frame.frame);
  }
  private keyboard(event: KeyboardEvent): void {
    if (event.key === 'Escape' && event.target === this.canvas && this.pendingClick?.finish) {
      event.preventDefault(); this.pendingClick = null; return;
    }
    if (event.key === 'Shift' && this.gesture && !this.gesture.shift) { this.gesture.events.push({ kind: 'key', key: 'Shift', down: true }); this.gesture.shift = true; return; }
    if (event.key === 'Escape' && this.gesture) { event.preventDefault(); this.cancel('Unsent annotation gesture cancelled.'); return; }
    if (event.target !== this.canvas || event.altKey || event.repeat || !this.ownsKeys() || this.options.labelsEditing()) return;
    const type = this.type(); if (!type) return;
    const key = event.key.length === 1 ? event.key.toLowerCase() : event.key;
    const name = `${event.metaKey || event.ctrlKey ? 'mod+' : ''}${event.shiftKey && key.length === 1 ? 'shift+' : ''}${key}`;
    const action = annotationShortcuts[type][name];
    if (action) { event.preventDefault(); void this.action(action); }
  }
  private point(paint: ScientificPaint, event: MouseEvent): number[] { return this.drawing.position(paint.draw, event.clientX, event.clientY); }
  private modifiers(event: MouseEvent): string[] {
    return [['Shift', event.shiftKey], ['Control', event.ctrlKey], ['Alt', event.altKey], ['Meta', event.metaKey]].filter(([, on]) => on).map(([name]) => name as string);
  }
  private begin(event: PointerEvent): void {
    if (event.button !== 0 || !event.isPrimary || !this.editing() || this.blocked() || this.options.labelsEditing()) return;
    const paint = this.drawing.current(); if (!paint) { this.status.textContent = 'Refresh the preview before editing.'; return; }
    try {
      const point = this.point(paint, event), modifiers = this.modifiers(event);
      const events: Array<PointerRow | KeyRow> = event.shiftKey ? [{ kind: 'key', key: 'Shift', down: true }] : [];
      if (this.hovering?.paint.draw === paint.draw) events.push(...this.hovering.events);
      this.hovering = null;
      events.push({ kind: 'move', point, modifiers }, { kind: 'press', point, modifiers });
      this.gesture = { pointer: event.pointerId, mouse: event.pointerType === 'mouse', paint, events, shift: event.shiftKey };
      this.canvas.setPointerCapture(event.pointerId); this.canvas.focus({ preventScroll: true });
      // Mouse click count arrives on mousedown, not pointerdown. Cancelling pointerdown suppresses it.
      if (!this.gesture.mouse) event.preventDefault();
      this.status.textContent = 'Unsent annotation gesture. Release to apply; Escape cancels.'; this.changed();
    } catch (error) { this.status.textContent = words(error); this.cancel(); }
  }
  private countClick(event: MouseEvent): void {
    if (event.button !== 0) return;
    // Capture the second press now: the first response may arrive before the later dblclick event.
    if (event.detail >= 2 && this.busy && this.pendingClick && this.queuedMode === null) {
      this.pendingClick.finish = true; event.preventDefault(); return;
    }
    if (!this.gesture?.mouse) return;
    event.preventDefault();
    // Qt replaces the second press with a double click. Correct the unsent gesture before pointerup.
    const row = this.gesture.events.at(-1);
    if (event.detail >= 2 && row?.kind === 'press') row.kind = 'double';
  }
  private extend(event: PointerEvent): void {
    const gesture = this.gesture;
    if (!gesture) { this.hover(event); return; }
    if (gesture.pointer !== event.pointerId) return;
    try {
      if (CLICK_MODES.includes(this.mode.value)) return;  // a click mode sends only its clicks
      const point = this.point(gesture.paint, event);
      const last = [...gesture.events].reverse().find((row): row is PointerRow => row.kind !== 'key')!;
      if (Math.hypot(point[0] - last.point[0], point[1] - last.point[1]) < 1) return;
      if (gesture.events.length >= MAX_EVENTS - 2) throw new Error('This gesture is too long. Nothing was applied; draw a shorter one.');
      gesture.events.push({ kind: 'move', point, modifiers: this.modifiers(event) });
    } catch (error) { this.cancel(words(error)); }
  }
  private hover(event: PointerEvent): void {
    if (!this.state()?.creating || !this.editing() || this.busy) { this.hovering = null; return; }
    const paint = this.drawing.current(); if (!paint) return;
    try {
      const point = this.point(paint, event);
      if (this.hovering?.paint.draw !== paint.draw) this.hovering = { paint, events: [] };
      const last = this.hovering.events.at(-1);
      if (last && Math.hypot(point[0] - last.point[0], point[1] - last.point[1]) < 1) return;
      if (this.hovering.events.length < MAX_EVENTS - 8) this.hovering.events.push({ kind: 'move', point, modifiers: this.modifiers(event) });
    } catch { /* outside the frame: napari would not see it either */ }
  }
  private finish(event: PointerEvent): void {
    const gesture = this.gesture; if (!gesture || gesture.pointer !== event.pointerId) return;
    this.extend(event);
    if (this.gesture !== gesture) return;
    this.gesture = null; this.release(gesture.pointer); event.preventDefault();
    try {
      const point = CLICK_MODES.includes(this.mode.value) ? (gesture.events.at(-1) as PointerRow).point : this.point(gesture.paint, event);
      gesture.events.push({ kind: 'release', point, modifiers: this.modifiers(event) });
      if (gesture.shift) gesture.events.push({ kind: 'key', key: 'Shift', down: false });
      const layer = this.layer.value, mode = this.mode.value;
      const click = CLICK_MODES.includes(mode) && !gesture.events.some(row => row.kind === 'double')
        ? { pointer: gesture.pointer, layer, mode, finish: false } : null;
      this.pendingClick = click;
      void this.send({ layer, mode, events: gesture.events }, gesture.paint.frame.frame).then(applied => {
        if (!click || this.pendingClick !== click) return;
        this.pendingClick = null;
        // napari's Finish action and its double-click callback perform the same native operation.
        if (applied && click.finish && this.queuedMode === null && this.layer.value === layer && this.mode.value === mode)
          void this.action('finish_drawing_shape');
      });
    } catch (error) { this.status.textContent = words(error); this.changed(); }
  }
  private doubleClick(event: MouseEvent): void { event.preventDefault(); }  // the counted second press already owns Finish
  private release(pointer: number): void { if (this.canvas.hasPointerCapture(pointer)) this.canvas.releasePointerCapture(pointer); }
  private cancel(message?: string): void {
    const old = this.gesture; this.gesture = null;
    if (old) { this.release(old.pointer); if (message) this.status.textContent = message; this.changed(); }
  }
  private changed(): void { this.paint(); this.options.changed(); }
  private paint(): void {
    const ready = this.ready(), idle = !this.blocked();
    this.layer.disabled = !this.available || !idle || !this.layer.children.length;
    this.mode.disabled = !ready || !!this.gesture;  // usable while a request is in flight: a new mode is queued
    this.root.setAttribute('aria-busy', String(this.busy || this.queuedMode !== null));
    const state = this.state();
    for (const button of [...this.actionRow.querySelectorAll('button')]) button.disabled = !ready || !idle;
    this.undoButton.disabled = !ready || !idle || !state?.undo || state.creating;
    this.redoButton.disabled = !ready || !idle || !state?.redo || state.creating;
    this.root.hidden = !this.frame?.layers.some(row => (row.type === 'points' || row.type === 'shapes') && row.annotation);
  }
  close(): void {
    this.closed = true; this.invalidate(); this.paint();
    this.canvas.removeEventListener('pointerdown', this.down); this.canvas.removeEventListener('mousedown', this.mouseDown); this.canvas.removeEventListener('pointermove', this.move);
    this.canvas.removeEventListener('pointerup', this.up); this.canvas.removeEventListener('pointercancel', this.lost);
    this.canvas.removeEventListener('lostpointercapture', this.lost); this.canvas.removeEventListener('dblclick', this.double);
    this.canvas.removeEventListener('keydown', this.previewKey);
    window.removeEventListener('blur', this.blur); window.removeEventListener('keydown', this.key); window.removeEventListener('keyup', this.keyUp);
  }
}
