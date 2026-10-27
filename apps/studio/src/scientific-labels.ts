import type { ScientificCanvas, ScientificPaint } from './scientific-canvas';
import type { ScientificFrame } from './scientific-view';
import type { LabelsAction, LabelsTool, LabelsToolChange, LabelsToolSettings } from './scientific-labels-tool';

type Action = 'paint' | 'erase' | 'fill' | 'polygon' | 'pick' | 'undo' | 'redo' | 'reset';
export type LabelCommand = { op: 'labels'; request: string; frame: string; layer: string; width: number; height: number;
  action: Action; points: number[][]; label?: string; brush?: number; preserve?: boolean; contiguous?: boolean; dimensions?: number };
type Receipt = { request: string; state: 'applied' | 'refused' | 'not-admitted'; changed: boolean; label: string | null; message: string };
type Options = { gestureControls: HTMLElement; send(command: LabelCommand, draw: string): Promise<unknown>; tool(change: LabelsToolChange, frame: string): Promise<void>; otherOwnsKeys?(): boolean; check(request: string): Promise<unknown>; changed(): void; preview(points: number[][] | null, paint: ScientificPaint | null): void };
const element = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const node = document.createElement(tag); node.textContent = text; return node; };
const words = (error: unknown) => error instanceof Error ? error.message : String(error);
export function labelReceipt(value: unknown, identity: string): Receipt {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('The edit outcome is not confirmed. Check its outcome before editing again.');
  const row = value as Record<string, unknown>;
  if (Object.keys(row).sort().join() !== ['request','state','changed','label','message'].sort().join() || row.request !== identity ||
      !['applied','refused','not-admitted'].includes(String(row.state)) || typeof row.changed !== 'boolean' || row.state !== 'applied' && row.changed ||
      row.label !== null && (typeof row.label !== 'string' || !/^(0|[1-9][0-9]{0,19})$/.test(row.label)) ||
      typeof row.message !== 'string' || row.message.length > 2000) throw new Error('The edit outcome is not confirmed. Check its outcome before editing again.');
  return row as Receipt;
}

type Gesture = { pointer: number; paint: ScientificPaint; rectangle: number[]; command: LabelCommand };
/** napari's default Labels shortcuts (napari/utils/shortcuts.py), as keys on the focused preview. */
export const labelsShortcuts: Record<string, LabelsAction> = {
  '1': 'activate_labels_erase_mode', e: 'activate_labels_erase_mode', '2': 'activate_labels_paint_mode', p: 'activate_labels_paint_mode',
  '3': 'activate_labels_polygon_mode', '4': 'activate_labels_fill_mode', f: 'activate_labels_fill_mode',
  '5': 'activate_labels_picker_mode', l: 'activate_labels_picker_mode', '6': 'activate_labels_pan_zoom_mode', z: 'activate_labels_pan_zoom_mode',
  m: 'new_label', x: 'swap_selected_and_background_labels', '-': 'decrease_label_id', '=': 'increase_label_id',
  '[': 'decrease_brush_size', ']': 'increase_brush_size', b: 'toggle_preserve_labels' };
const toolOfMode: Record<string, string> = { pan_zoom: 'inspect', transform: 'inspect', paint: 'paint', erase: 'erase', fill: 'fill', pick: 'pick', polygon: 'polygon' };
const modeOfTool: Record<string, LabelsToolSettings['mode']> = { inspect: 'pan_zoom', paint: 'paint', erase: 'erase', fill: 'fill', pick: 'pick', polygon: 'polygon' };
/** What can be edited and why, from the worker's limits (env_worker scientific_outputs EAGER_BYTES, lazy_labels DIRTY_BYTES and
 * WRITE_BYTES): never a promise of whole-volume editing. */
export const LABELS_SIZE_WORDS = 'What you can edit: Labels up to 512 MiB (with this window’s other editable layers) open whole, so any brush, fill or polygon works. Larger Labels open a region at a time, so the whole volume never has to fit in memory: one edit can change up to 16 MiB of it, and up to 64 MiB of changes wait for Save. An edit bigger than that, such as filling the whole volume, is refused and nothing changes; make it in smaller parts.';
export class ScientificLabels {
  private available = false;
  private session: string | null = null;
  private generation = 0;
  private closed = false;
  private gesture: Gesture | null = null;
  private polygon: Omit<Gesture, 'pointer'> | null = null;
  private pending: string | null = null;
  private executing = false;
  private toolPending = false;
  private queued: LabelsToolSettings | null = null;
  private toolLayer: string | null = null;
  private frame: ScientificFrame | null = null;
  private readonly layer = element('select');
  private readonly tool = element('select');
  private readonly label = element('input');
  private readonly brush = element('input');
  private readonly dimensions = element('select');
  private readonly preserve = element('input');
  private readonly contiguous = element('input');
  private readonly status = element('p', 'Choose a Labels layer and a tool. Edits stay private until Save array changes.');
  private readonly buttons: HTMLButtonElement[] = [];
  private readonly checkButton: HTMLButtonElement;
  private readonly cancelButton: HTMLButtonElement;
  private readonly finishButton: HTMLButtonElement;
  private readonly canvas: HTMLCanvasElement;
  private readonly blur = () => this.cancel('Unsent gesture cancelled. Inspect the current preview before drawing.');
  private readonly visibility = () => { if (document.visibilityState === 'hidden') this.blur(); };
  private readonly key = (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    if (event.key === 'Escape') {
      const unsent=!!this.gesture || !!this.polygon;
      this.blur();
      if (unsent && event.target===this.canvas) event.preventDefault();
    }
    if (event.key === 'Enter' && event.target===this.canvas && this.polygon) { event.preventDefault(); this.finishPolygon(); }
    if (event.target!==this.canvas || event.altKey || event.repeat && !['[',']'].includes(event.key) || this.options.otherOwnsKeys?.()) return;
    if (event.key.toLowerCase()==='z' && (event.metaKey || event.ctrlKey)) {  // napari: Cmd/Ctrl+Z undo, +Shift redo
      event.preventDefault(); void this.direct(event.shiftKey ? 'redo' : 'undo'); return;
    }
    const action = !event.metaKey && !event.ctrlKey && !event.shiftKey ? labelsShortcuts[event.key.toLowerCase()] : undefined;
    if (action && this.root.hidden === false) { event.preventDefault(); void this.toolChange({ action }); }
  };
  private readonly previewKey = (event: KeyboardEvent) => {
    if (event.defaultPrevented) return;
    this.key(event);
    if (event.defaultPrevented) event.stopPropagation();
  };
  private readonly down = (event: PointerEvent) => this.begin(event);
  private readonly move = (event: PointerEvent) => this.extend(event);
  private readonly up = (event: PointerEvent) => this.finish(event);
  private readonly lost = (event: PointerEvent) => { if (this.gesture?.pointer === event.pointerId || this.polygon && event.type==='pointercancel') this.blur(); };

  private readonly root: HTMLElement;
  private readonly drawing: ScientificCanvas;
  private readonly options: Options;
  constructor(root: HTMLElement, drawing: ScientificCanvas, options: Options) {
    this.root=root; this.drawing=drawing; this.options=options;
    this.canvas = drawing.element();
    this.canvas.style.touchAction = 'none';
    this.canvas.tabIndex=0;
    const field = (name: string, input: HTMLElement) => { input.setAttribute('aria-label', name); const label = element('label', name); label.append(input); root.append(label); };
    field('Labels layer', this.layer);
    for (const [value, name] of [['inspect','Inspect'],['paint','Paint'],['erase','Erase'],['fill','Fill'],['polygon','Polygon — displayed plane'],['pick','Pick label']]) {
      const option = element('option', name); option.value = value; this.tool.append(option);
    }
    field('Label tool', this.tool);
    this.label.type = 'text'; this.label.inputMode = 'numeric'; this.label.value = '1'; field('Label value', this.label);
    this.brush.type = 'number'; this.brush.min = '1'; this.brush.max = '256'; this.brush.value = '3'; field('Brush size', this.brush);
    field('Edit dimensions', this.dimensions);
    this.preserve.type = 'checkbox'; field('Preserve other labels', this.preserve);
    this.contiguous.type = 'checkbox'; this.contiguous.checked = true; field('Fill connected region only', this.contiguous);
    const add = (title: string, callback: () => void, target=root) => { const button = element('button', title); button.addEventListener('click', callback); target.append(button); return button; };
    for (const [action,title] of [['undo','Undo label edit'],['redo','Redo label edit'],['reset','Reset manual history to current layer']] as const)
      this.buttons.push(add(title, () => void this.direct(action)));
    this.checkButton = add('Check edit outcome', () => void this.check());
    this.finishButton = add('Finish polygon', () => this.finishPolygon(),options.gestureControls);
    this.cancelButton = add('Cancel unsent stroke', () => this.blur(),options.gestureControls);
    this.status.setAttribute('role','status'); root.append(this.status, element('p',LABELS_SIZE_WORDS), element('p','Edits start on the displayed plane. Choosing more than 2 editing dimensions also changes hidden slices, using the last axes in the current axis order. A stroke is submitted on release. Polygon edits only the displayed plane: click each corner, then Finish polygon or press Enter on the preview. Its outline is an unsent preview; Escape cancels. Reset history keeps current values and clears manual Undo/Redo.'));
    for (const input of [this.layer,this.tool,this.label,this.brush,this.dimensions,this.preserve,this.contiguous]) input.addEventListener('change', this.blur);
    // A changed setting is napari's own layer setting: sent at once, and shown back from the layer's state.
    this.tool.addEventListener('change', () => void this.toolChange({ settings: { mode: modeOfTool[this.tool.value] } }));
    this.label.addEventListener('change', () => { if (/^(0|[1-9][0-9]{0,19})$/.test(this.label.value)) void this.toolChange({ settings: { selectedLabel: this.label.value } }); });
    this.brush.addEventListener('change', () => { const size=Number(this.brush.value); if (Number.isInteger(size) && size>=1 && size<=256) void this.toolChange({ settings: { brushSize: size } }); });
    this.dimensions.addEventListener('change', () => void this.toolChange({ settings: { editDimensions: Number(this.dimensions.value) } }));
    this.preserve.addEventListener('change', () => void this.toolChange({ settings: { preserve: this.preserve.checked } }));
    this.contiguous.addEventListener('change', () => void this.toolChange({ settings: { contiguous: this.contiguous.checked } }));
    this.layer.addEventListener('change', () => { this.queued = null; this.dimensionChoices(); this.showTool(); this.paint(); });
    this.tool.addEventListener('change', () => this.paint());
    this.canvas.addEventListener('pointerdown', this.down); this.canvas.addEventListener('pointermove', this.move);
    this.canvas.addEventListener('pointerup', this.up); this.canvas.addEventListener('pointercancel', this.lost);
    this.canvas.addEventListener('lostpointercapture', this.lost);
    this.canvas.addEventListener('keydown',this.previewKey);
    window.addEventListener('blur',this.blur); window.addEventListener('keydown',this.key);
    document.addEventListener('visibilitychange',this.visibility); this.paint();
  }
  blocked(): boolean { return !!this.gesture || !!this.polygon || !!this.pending || this.executing; }
  /** Whether a Labels tool is taking the preview's pointer (so other editors stand back). */
  editing(): boolean { return this.ready() && this.tool.value !== 'inspect'; }
  bindSession(session: string | null): void {
    if (session === this.session) return;
    this.cancel(); this.session=session; this.generation++; this.pending=null; this.executing=false; this.toolPending=false; this.queued=null; this.frame=null;
    this.layer.replaceChildren(); delete this.layer.dataset.signature; this.dimensions.replaceChildren(); this.dimensions.dataset.count=''; this.paint();
  }
  update(frame: ScientificFrame): void {
    const draft=this.gesture ?? this.polygon;
    if (draft && draft.paint.frame.frame !== frame.frame) this.blur();
    this.frame=frame;
    const selected=this.layer.value, rows=frame.layers.filter(row=>row.type==='labels' && row.editableInput);
    const signature=JSON.stringify(rows.map(row=>[row.id,row.name]));
    if (this.layer.dataset.signature !== signature) {
      this.layer.replaceChildren();
      for (const row of rows) { const option=element('option',row.name); option.value=row.id; this.layer.append(option); }
      this.layer.value=rows.some(row=>row.id===selected)?selected:(rows[0]?.id??''); this.layer.dataset.signature=signature;
    }
    if (this.toolLayer && this.toolLayer !== this.layer.value) this.queued = null;
    this.dimensionChoices(); this.showTool(); this.paint();
  }
  /** The fields show napari's own Labels tool state of the chosen layer (a field being typed in keeps its text). */
  private showTool(): void {
    const state: LabelsTool | undefined = this.frame?.layers.find(row=>row.id===this.layer.value)?.labelsTool;
    if (!state || this.toolPending) return;
    const typing = document.activeElement;
    if (typing !== this.tool) this.tool.value = toolOfMode[state.mode] ?? 'inspect';
    if (typing !== this.label) this.label.value = state.selectedLabel;
    if (typing !== this.brush) this.brush.value = String(Math.min(256, state.brushSize));
    this.preserve.checked = state.preserve; this.contiguous.checked = state.contiguous;
    if ([...this.dimensions.children].some(option=>(option as HTMLOptionElement).value===String(state.editDimensions))) this.dimensions.value = String(state.editDimensions);
    this.label.title = `Background label ${state.background}`;
  }
  /** A setting changed while another is being applied waits and is sent next (the latest value of each wins), so the
   *  fields stay usable: committing one field as focus moves to the next never takes that focus away. */
  private async toolChange(change: { action: LabelsAction } | { settings: LabelsToolSettings }): Promise<void> {
    if ('settings' in change && this.toolPending) { if (this.toolLayer === this.layer.value) this.queued={ ...this.queued, ...change.settings }; return; }
    const frame=this.frame;
    if (!frame || !this.layer.value || this.blocked() || !this.available || !this.session) return;
    const generation=this.generation, layer=this.layer.value; this.toolLayer=layer; this.toolPending=true; this.executing=true; this.changed();
    try { await this.options.tool({ operation: 'labels-tool', value: { layer: this.layer.value, ...change } as LabelsToolChange['value'] }, frame.frame); }
    catch (error) { if (generation===this.generation && !this.closed) { this.status.textContent=words(error); this.queued=null; } }
    finally {
      if (generation===this.generation) {
        const next=layer===this.layer.value ? this.queued : null; this.queued=null; this.toolLayer=null; this.toolPending=false; this.executing=false;
        if (next && !this.closed) void this.toolChange({ settings: next }); else { this.showTool(); this.changed(); }
      }
    }
  }
  private dimensionChoices(): void {
    const count=this.frame?.layers.find(row=>row.id===this.layer.value)?.scale.length ?? 0;
    if (this.dimensions.dataset.count===String(count)) return;
    const selected=Number(this.dimensions.value);
    this.dimensions.replaceChildren(); this.dimensions.dataset.count=String(count);
    for (let value=2;value<=Math.min(count,8);value++) {
      const option=element('option',value===2?'2 — displayed plane':`${value} — includes hidden slices`);
      option.value=String(value); this.dimensions.append(option);
    }
    this.dimensions.value=selected>=2 && selected<=count?String(selected):'2';
  }
  setAvailable(value: boolean): void { this.available=value; if (!value) this.cancel(); this.paint(); }
  revoke(): void { this.cancel('The preview changed. The unsent stroke was cancelled.'); }
  private rectangle(): number[] { const rect=this.canvas.getBoundingClientRect(); return [rect.left,rect.top,rect.width,rect.height]; }
  private ready(): boolean { return this.available && !!this.session && this.frame?.dimensions.ndisplay===2 && !this.frame.grid?.enabled && !!this.frame.layers.find(row=>row.id===this.layer.value && row.visible && row.type==='labels' && row.editableInput); }
  private command(action: Action, paint: ScientificPaint): LabelCommand {
    const request: LabelCommand={op:'labels',request:crypto.randomUUID(),frame:paint.frame.frame,layer:this.layer.value,action,points:[],width:paint.frame.width,height:paint.frame.height};
    if (['paint','erase','fill'].includes(action)) {
      if (!/^(0|[1-9][0-9]{0,19})$/.test(this.label.value) || !Number.isInteger(Number(this.brush.value)) || Number(this.brush.value)<1 || Number(this.brush.value)>256)
        throw new Error('Choose a nonnegative whole label and a brush from 1 to 256.');
      Object.assign(request,{label:this.label.value,brush:Number(this.brush.value),preserve:this.preserve.checked,contiguous:this.contiguous.checked});
      const dimensions=Number(this.dimensions.value), count=paint.frame.layers.find(row=>row.id===this.layer.value)?.scale.length ?? 0;
      if (!Number.isInteger(dimensions) || dimensions<2 || dimensions>Math.min(count,8)) throw new Error('Choose editing dimensions supported by this Labels layer.');
      if (dimensions!==2) request.dimensions=dimensions;
    }
    if (action==='polygon') {
      if (!/^(0|[1-9][0-9]{0,19})$/.test(this.label.value)) throw new Error('Choose a nonnegative whole label.');
      Object.assign(request,{label:this.label.value,preserve:this.preserve.checked});
    }
    return request;
  }
  private begin(event: PointerEvent): void {
    if (event.button!==0 || !event.isPrimary || !this.ready() || this.pending || this.executing || this.gesture || this.tool.value==='inspect') return;
    if (this.tool.value==='polygon') { this.corner(event); return; }
    if (this.polygon) return;
    try {
      const paint=this.drawing.current(); if (!paint) throw new Error('Refresh the preview before editing.');
      const command=this.command(this.tool.value as Action,paint);
      command.points.push(this.drawing.position(paint.draw,event.clientX,event.clientY));
      this.canvas.setPointerCapture(event.pointerId);
      this.gesture={pointer:event.pointerId,paint,rectangle:this.rectangle(),command}; event.preventDefault();
      this.status.textContent='Unsent stroke. Release to apply; Escape cancels.'; this.changed();
    } catch(error) { this.status.textContent=words(error); this.cancel(); }
  }
  private checkPolygon(): void {
    const draft=this.polygon;
    if (!draft || this.drawing.current()?.draw!==draft.paint.draw ||
        this.rectangle().some((value,index)=>value!==draft.rectangle[index]))
      throw new Error('The preview moved or changed. The unsent polygon was cancelled.');
  }
  private corner(event: PointerEvent): void {
    try {
      if (!this.polygon) {
        const paint=this.drawing.current(); if (!paint) throw new Error('Refresh the preview before editing.');
        this.polygon={paint,rectangle:this.rectangle(),command:this.command('polygon',paint)};
      }
      this.checkPolygon(); const draft=this.polygon;
      const point=this.drawing.position(draft.paint.draw,event.clientX,event.clientY);
      const last=draft.command.points.at(-1);
      if (last && Math.hypot(point[0]-last[0],point[1]-last[1])<1) return;
      if (draft.command.points.length>=256) throw new Error('This polygon has too many corners. Nothing was applied; draw a simpler polygon.');
      draft.command.points.push(point);
      this.options.preview(draft.command.points,draft.paint);
      this.status.textContent=`Unsent polygon: ${draft.command.points.length} corners. Click another corner, then Finish polygon or press Enter on the preview. Escape cancels.`;
      this.canvas.focus({preventScroll:true}); event.preventDefault(); this.changed();
    } catch(error) { this.cancel(words(error)); }
  }
  private finishPolygon(): void {
    const draft=this.polygon; if (!draft || this.pending || this.executing) return;
    try {
      this.checkPolygon();
      if (draft.command.points.length<3) { this.status.textContent='Add at least three polygon corners before finishing.'; return; }
      this.polygon=null; this.options.preview(null,null);
      void this.submit(draft.command,draft.paint.draw);
    } catch(error) { this.cancel(words(error)); }
  }
  private extend(event: PointerEvent): void {
    const gesture=this.gesture; if (!gesture || gesture.pointer!==event.pointerId) return;
    try {
      if (this.rectangle().some((value,index)=>value!==gesture.rectangle[index])) throw new Error('The window moved or resized. The unsent stroke was cancelled.');
      const point=this.drawing.position(gesture.paint.draw,event.clientX,event.clientY);
      if (!['paint','erase'].includes(gesture.command.action)) return;
      const last=gesture.command.points.at(-1)!;
      if (Math.hypot(point[0]-last[0],point[1]-last[1])<1) return;
      if (gesture.command.points.length>=256) throw new Error('This stroke is too long. Draw shorter strokes; nothing was applied.');
      gesture.command.points.push(point);
    } catch(error) { this.cancel(words(error)); }
  }
  private finish(event: PointerEvent): void {
    if (this.gesture?.pointer!==event.pointerId) return;
    this.extend(event); const gesture=this.gesture; if (!gesture) return;
    this.gesture=null; this.release(gesture.pointer); event.preventDefault();
    void this.submit(gesture.command,gesture.paint.draw);
  }
  private release(pointer: number): void { if (this.canvas.hasPointerCapture(pointer)) this.canvas.releasePointerCapture(pointer); }
  private cancel(message?: string): void {
    const old=this.gesture, polygon=this.polygon; this.gesture=null; this.polygon=null;
    if (polygon) this.options.preview(null,null);
    if (old) this.release(old.pointer);
    if (old || polygon) { if (message) this.status.textContent=message; this.changed(); }
  }
  private async direct(action: 'undo'|'redo'|'reset'): Promise<void> {
    if (!this.ready() || this.blocked()) return;
    const paint=this.drawing.current(); if (!paint) return;
    await this.submit(this.command(action,paint),paint.draw);
  }
  private async submit(command: LabelCommand, draw: string): Promise<void> {
    const generation=this.generation; this.pending=command.request; this.executing=true; this.changed();
    this.status.textContent='Applying label edit…';
    try { const result=await this.options.send(command,draw); if (generation===this.generation && !this.closed) this.accept(result,command.request); }
    catch(error) { if (generation===this.generation && !this.closed) this.status.textContent=`${words(error)} Check edit outcome; this gesture will not be sent again.`; }
    finally { if (generation===this.generation) { this.executing=false; this.changed(); } }
  }
  private accept(raw: unknown, identity: string): void {
    const result=labelReceipt(raw,identity); this.pending=null;
    if (result.label!==null && result.state==='applied') this.label.value=result.label;
    this.status.textContent=result.state!=='applied'?result.message:result.changed?'Label edit applied to this private view. Save array changes to keep it.':'Label action completed. Array values did not change.';
  }
  private async check(): Promise<void> {
    if (!this.pending || this.executing) return;
    const identity=this.pending,generation=this.generation; this.executing=true; this.changed();
    try { const result=await this.options.check(identity); if (generation===this.generation && !this.closed) this.accept(result,identity); }
    catch(error) { if (generation===this.generation && !this.closed) this.status.textContent=`${words(error)} The edit is still unconfirmed. Stop and reopen accepted Data if the session cannot recover.`; }
    finally { if (generation===this.generation) { this.executing=false; this.changed(); } }
  }
  private changed(): void { this.paint(); this.options.changed(); }
  private paint(): void {
    const ready=this.ready() && !this.blocked();
    // While only a setting is being applied, the settings fields stay enabled (a later change is queued).
    const settling=this.ready() && this.toolPending && !this.gesture && !this.polygon && !this.pending;
    // Assign final states once: even a synchronous disable/re-enable blurs a focused control.
    const settingsReady=ready || settling;
    for (const input of [this.tool,this.label,this.preserve]) input.disabled=!settingsReady;
    for (const action of this.buttons) action.disabled=!ready;
    this.root.setAttribute('aria-busy',String(this.toolPending || this.executing));
    // A hidden layer can still be replaced by another editable selection.
    this.layer.disabled=!this.available || this.blocked() || !this.layer.children.length;
    this.checkButton.hidden=!this.pending; this.checkButton.disabled=this.executing;
    this.cancelButton.hidden=!this.gesture && !this.polygon;
    this.cancelButton.textContent=this.polygon?'Cancel unsent polygon':'Cancel unsent stroke';
    this.finishButton.hidden=this.tool.value!=='polygon';
    this.finishButton.disabled=!this.polygon || this.polygon.command.points.length<3 || this.executing || !!this.pending;
    for (const input of [this.brush,this.dimensions,this.contiguous]) {
      const polygon=this.tool.value==='polygon';
      if (input.parentElement) input.parentElement.hidden=polygon;
      input.disabled=polygon || !settingsReady;
    }
    this.root.hidden=!this.frame?.layers.some(row=>row.type==='labels' && row.editableInput);
  }
  close(): void {
    this.closed=true; this.generation++; this.cancel();
    this.canvas.removeEventListener('pointerdown',this.down); this.canvas.removeEventListener('pointermove',this.move);
    this.canvas.removeEventListener('pointerup',this.up); this.canvas.removeEventListener('pointercancel',this.lost); this.canvas.removeEventListener('lostpointercapture',this.lost);
    this.canvas.removeEventListener('keydown',this.previewKey);
    window.removeEventListener('blur',this.blur); window.removeEventListener('keydown',this.key); document.removeEventListener('visibilitychange',this.visibility);
  }
}
