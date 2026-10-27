import { textAppearance, textBlending, type TextAppearance } from './scientific-text-appearance';
import {scientificGrid,type ScientificGrid} from './scientific-grid';
import {sliceMargins,imageProjection,type SliceMargins,type ImageProjection} from './scientific-slices';
import { pointAppearance, type PointAppearance } from './scientific-point-appearance';
import { pointStyleRow, shapeStyleRow, textContentRow, type StyleChange } from './scientific-geometry-style';
import { surfaceAppearance, type SurfaceAppearance } from './scientific-surface-appearance';
import { pathAppearance, type PathAppearance } from './scientific-path-appearance';
import { imageColormap, type ImageColormap } from './scientific-colormap';
import { imageSampling, interpolationModes, type ImageSampling } from './scientific-image-sampling';
import { clippingPlanes, type ClippingPlane } from './scientific-clipping';
import { imagePlane, type ImagePlane } from './scientific-image-plane';
import { imageAppearance, imageRendering, type ImageAppearance } from './scientific-image-appearance';
import { layerBlending, type ScientificFrame } from './scientific-view';
import { labelsAppearance, labelsRendering, labelsGradient, directColorsText, parseDirectColors, labelId, MAX_COLOR_SEED, type LabelsAppearance } from './scientific-labels-appearance';
import { histogramSettings, contrastAction, histogramChart, MAX_HISTOGRAM_BINS, type HistogramSettings, type ContrastAction } from './scientific-image-histogram';
import { ScientificPlayback } from './scientific-playback';

export type ViewerChange =
  | {operation:'layer-selection';value:{layer:string|null;action:'replace'|'toggle'|'clear'}}
  | {operation:'grid';value:ScientificGrid}
  | {operation:'axis-order';value:'roll'|'transpose'}
  | {operation:'viewer-action';value:DimensionAction}
  | {operation:'dims-point';value:{axis:number;value:number}}
  | {operation:'dims-focus';value:number}
  | {operation:'slice-margins';value:SliceMargins}
  | {operation:'image-projection';value:{layer:string;mode:ImageProjection}}
  | {operation:'text-presentation';value:{layer:string;settings:TextAppearance}}
  | {operation:'point-presentation';value:{layer:string;settings:PointAppearance}}
  | StyleChange
  | {operation:'surface-presentation';value:{layer:string;settings:SurfaceAppearance}}
  | {operation:'path-presentation';value:{layer:string;settings:PathAppearance}}
  | {operation:'image-colormap';value:{layer:string;settings:ImageColormap|{name:string}}}
  | {operation:'image-sampling';value:{layer:string;settings:ImageSampling}}
  | { operation:'clipping-planes'; value:{layer:string;planes:ClippingPlane[]} }
  | { operation: 'camera'; value: ScientificFrame['camera'] }
  | { operation: 'camera-reset'; value: Record<string, never> }
  | { operation: 'camera-gesture'; value: { mode: 'orbit' | 'pan' | 'zoom'; width: number; height: number; points: [number,number][] } }
  | { operation: 'image-plane'; value: { layer: string; settings: ImagePlane } }
  | { operation: 'image-presentation'; value: { layer: string; settings: ImageAppearance } }
  | { operation: 'labels-presentation'; value: { layer: string; settings: LabelsAppearance } }
  | { operation: 'image-histogram'; value: { layer: string; settings: HistogramSettings } }
  | { operation: 'image-contrast'; value: { layer: string; settings: ContrastAction } }
  | { operation: 'layer-order'; value: string[] }
  | { operation: 'dimensions'; value: { order: number[]; ndisplay: 2 | 3; steps: number[] } }
  | { operation: 'layer-presentation'; value: { layer: string; visible: boolean; opacity: number; blending?: typeof layerBlending[number] } };
/** napari's own dimension actions (napari.components._viewer_key_bindings), run natively by the worker. */
export type DimensionAction = 'increment_dims_left'|'increment_dims_right'|'focus_axes_up'|'focus_axes_down'|'roll_axes'|'transpose_axes'|'toggle_ndisplay';
/** napari's default shortcuts for those actions (napari.utils.shortcuts). Mod is Command on macOS and Control elsewhere. */
export const dimensionShortcuts: {action:DimensionAction;key:string;alt?:true;mod?:true;label:string}[] = [
  {action:'increment_dims_left',key:'ArrowLeft',label:'Left arrow'},
  {action:'increment_dims_right',key:'ArrowRight',label:'Right arrow'},
  {action:'focus_axes_up',key:'ArrowUp',alt:true,label:'Option/Alt+Up arrow'},
  {action:'focus_axes_down',key:'ArrowDown',alt:true,label:'Option/Alt+Down arrow'},
  {action:'roll_axes',key:'e',mod:true,label:'Command/Control+E'},
  {action:'transpose_axes',key:'t',mod:true,label:'Command/Control+T'},
  {action:'toggle_ndisplay',key:'y',mod:true,label:'Command/Control+Y'},
];
export function dimensionShortcut(event: Pick<KeyboardEvent,'key'|'altKey'|'ctrlKey'|'metaKey'|'shiftKey'>, mac: boolean): DimensionAction | null {
  const mod = mac ? event.metaKey && !event.ctrlKey : event.ctrlKey && !event.metaKey, none = !event.metaKey && !event.ctrlKey;
  if (event.shiftKey) return null;
  for (const shortcut of dimensionShortcuts) {
    if (event.key.toLowerCase() !== shortcut.key.toLowerCase()) continue;
    if (shortcut.mod ? mod && !event.altKey : shortcut.alt ? event.altKey && none : none && !event.altKey) return shortcut.action;
  }
  return null;
}
/** A field that takes typed text or its own arrow keys (a slider, select or text field): shortcuts leave it alone. */
const editable = (target: EventTarget | null) => {
  const element = target as { tagName?: string; type?: string; isContentEditable?: boolean } | null;
  const tag = String(element?.tagName ?? '').toUpperCase();
  return !!element && (element.isContentEditable === true || tag === 'TEXTAREA' || tag === 'SELECT' ||
    tag === 'INPUT' && !['button','checkbox','radio','submit'].includes(String(element.type)));
};
const macintosh = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent || '');
type Transport = {
  send(frame: string, change: ViewerChange): Promise<ScientificFrame>;
  /** Replace only an in-flight absolute slice target; never an edit or plugin callback. */
  replaceDimensions?(value: {order: number[]; ndisplay: 2 | 3; steps: number[]}): Promise<void>;
  refresh(): Promise<void>;
  drawn(frame: string): Promise<unknown>;
  changed?(): void;
};
type Draft = { frame: string; group: string; generation: number; read(): ViewerChange };
type Field = HTMLInputElement | HTMLSelectElement | HTMLButtonElement | HTMLTextAreaElement;
type Row = { root: HTMLElement; fields: Field[]; update(frame: ScientificFrame): void };
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};

export class ScientificNavigation {
  private frame: ScientificFrame | null = null;
  private draft: Draft | null = null;
  private rows = new Map<string, Row>();
  private available = false;
  private awaitingFrame: string | null = null;
  private busy = false;
  private dimensionNavigation = false;
  private replacement: Extract<ViewerChange, {operation:'dimensions'}> | null = null;
  private replacing = false;
  private closed = false;
  private session: string | null = null;
  private generation = 0;
  private uncertain = false;
  private reportedSettled = true;
  private readonly body = node('div');
  private readonly status = node('p');
  private readonly apply = node('button', 'Apply view changes');
  private readonly discard = node('button', 'Discard unsent view changes');
  private readonly refresh = node('button', 'Check current view');

  private readonly playback: ScientificPlayback;
  private readonly root: HTMLElement;
  private readonly transport: Transport;
  private epoch = 0;

  constructor(root: HTMLElement, transport: Transport) {
    this.root = root; this.transport = transport;
    this.keyboard(root);
    const playback = node('section');
    this.playback = new ScientificPlayback(playback, {
      step: async (frame,axis,index) => {
        if (!this.frame || this.frame.frame!==frame || this.draft || this.busy || this.uncertain) throw new Error('The scientific view changed. Check the current view.');
        const steps=[...this.frame.dimensions.steps]; steps[axis]=index;
        return this.transport.send(frame,{operation:'dimensions',value:{order:[...this.frame.dimensions.order],ndisplay:this.frame.dimensions.ndisplay,steps}});
      },
      drawn: frame => this.transport.drawn(frame),
      changed: () => { this.paint(); this.transport.changed?.(); },
    });
    root.classList.add('scientific-navigation');
    this.status.setAttribute('role', 'status');
    this.apply.addEventListener('click', () => void this.submit());
    this.discard.addEventListener('click', () => {
      if (this.busy) return;
      this.draft = null; this.status.textContent = ''; this.paint();
    });
    this.refresh.addEventListener('click', () => void this.recheck());
    const actions = node('div'); actions.className = 'scientific-navigation-actions';
    actions.append(this.status, this.apply, this.discard, this.refresh);
    root.append(node('h4', 'View'), node('p', 'Apply changes to this private session. Remember this view restores supported view settings in a new session. Marker styles, shape styles, text content and shape z-order are not yet retained by Remember this view.'),
      this.body, playback, actions);
    this.paint();
  }

  bindSession(session: string | null): void {
    if (session === this.session) return;
    this.epoch++; this.busy = false; this.dimensionNavigation = false; this.replacement = null; this.replacing = false; this.status.textContent = '';
    this.playback.bindSession(session);
    this.session = session; this.frame = null; this.draft = null; this.uncertain = false;
    this.rows.clear(); this.body.replaceChildren(); this.setAvailable(false);
  }
  playing(): boolean { return this.playback.active(); }
  settled(): boolean { return !this.draft && !this.busy && !this.uncertain && !this.playing(); }
  update(frame: ScientificFrame): void { this.frame = structuredClone(frame); this.playback.update(frame); this.paint(); }
  setAvailable(value: boolean, awaitingFrame: string | null = null): void {
    this.available = value; this.awaitingFrame = awaitingFrame; this.paint();
  }
  close(): void { this.closed = true; this.playback.close(); this.rows.clear(); this.root.replaceChildren(); }
  /** napari's Apply auto-contrast (key C) on the active Image layer, through the same frame-bound change as its button. */
  autoContrastOnce(): void {
    const frame = this.frame, active = frame?.layerSelection?.active ?? null;
    const target = frame?.layers.find(layer => layer.id === active && layer.type === 'image' && layer.imageHistogram);
    if (!target) { this.status.textContent = 'Select an image layer to make it active, then press C to apply auto-contrast.'; return; }
    if (!this.available || this.busy || this.playing() || this.uncertain || this.draft) {
      this.status.textContent = 'Apply or discard unsent view changes, then press C again.'; return;
    }
    this.edit(`contrast:${target.id}`, () => ({operation: 'image-contrast', value: {layer: target.id, settings: contrastAction({action: 'auto-once'})}}));
    void this.submit();
  }
  /** napari's dimension shortcuts while focus is in the preview or this panel (never while typing in a field). */
  keyboard(target: HTMLElement): void {
    target.addEventListener('keydown', event => {
      if (event.defaultPrevented || editable(event.target)) return;
      const action = dimensionShortcut(event, macintosh());
      if (!action || !this.frame) return;
      // In 3D the focused preview canvas uses arrow keys to move the camera (scientific-camera-gestures).
      // So in 3D no arrow key acts on dimensions while focus is anywhere in the preview (its viewport or canvas).
      const inPreview = (target: EventTarget | null) => {
        const element = target as { tagName?: string; closest?: (selector: string) => unknown } | null;
        return String(element?.tagName).toUpperCase() === 'CANVAS' || !!element?.closest?.('.scientific-viewport');
      };
      if (event.key.startsWith('Arrow') && this.frame.dimensions.ndisplay === 3 && inPreview(event.target)) return;
      event.preventDefault();
      this.act({operation:'viewer-action',value:action});
    });
  }
  /** One immediate native action, as Roll axes is: refused while other view changes are unsent or running. */
  private act(change: ViewerChange): void {
    if (!this.available || this.busy || this.playing() || this.uncertain || this.draft || !this.frame) {
      this.status.textContent = this.draft ? 'Apply or discard unsent view changes before using a dimension action.' : 'Wait for the current view before using a dimension action.';
      return;
    }
    this.edit('dimensions', () => change); void this.submit();
  }

  private edit(group: string, read: () => ViewerChange): void {
    if (!this.available || (this.busy && !(this.dimensionNavigation && group === 'dimensions')) || this.playing() || !this.frame || this.uncertain || this.draft && this.draft.group !== group) return;
    this.draft = { frame: this.draft?.frame ?? this.frame.frame, group, generation: ++this.generation, read };
    this.status.textContent = 'Unsent view changes. Apply to update the private preview, or discard to keep the current view.';
    this.paint();
  }
  private input(label: string, type: string, group: string, read: () => ViewerChange): HTMLInputElement {
    const input = node('input'); input.type = type; input.setAttribute('aria-label', label);
    input.addEventListener('input', () => this.edit(group, read));
    return input;
  }
  private dimensions(): Row {
    const root = node('fieldset'), mode = node('select'), order = node('div'), axes = node('div'), details = node('details'), coordinates = node('div');
    root.append(node('legend', 'Dimensions'));
    const roll=node('button','Roll axes'),transpose=node('button','Swap displayed axes');
    for(const [button,value] of [[roll,'roll'],[transpose,'transpose']] as const){
      button.dataset.requiresClean='true';
      button.addEventListener('click',()=>{if(button.disabled)return;this.edit('dimensions',()=>({operation:'axis-order',value}));void this.submit();});
    }
    const actions = node('div'); actions.className = 'scientific-dimension-actions';
    const actionButtons: HTMLButtonElement[] = [];
    for (const [label,action] of [['Previous slice','increment_dims_left'],['Next slice','increment_dims_right'],['Keyboard axis up','focus_axes_up'],['Keyboard axis down','focus_axes_down'],['Toggle 2D/3D','toggle_ndisplay']] as const) {
      const button = node('button', label), shortcut = dimensionShortcuts.find(item => item.action === action)!;
      button.dataset.requiresClean = 'true'; button.title = `${label} (${shortcut.label}), napari's ${action.replaceAll('_',' ')}`;
      button.addEventListener('click', () => { if (!button.disabled) this.act({operation:'viewer-action',value:action}); });
      actions.append(button); actionButtons.push(button);
    }
    const keyboardAxis = node('p'); keyboardAxis.setAttribute('aria-live','polite'); keyboardAxis.className = 'scientific-keyboard-axis';
    mode.setAttribute('aria-label', 'Displayed dimensions');
    for (const n of [2, 3]) { const option = node('option', `${n}D`); option.value = String(n); mode.append(option); }
    details.append(node('summary', 'Axes and coordinates'), node('p', 'Choose the axes to display. Swapping an axis keeps every dimension available.'), order, coordinates);
    root.append(node('label', 'Display'), mode, roll, transpose, actions, keyboardAxis, axes, details, node('p','Use a slice slider or enter its exact index. Arrow keys step a focused slider; Home and End select its endpoints. Apply to show the chosen slice. Roll skips singleton and integration-locked axes. With the preview focused, Left and Right arrows step the keyboard axis and Option/Alt+Up or Down chooses it, as in napari. Go to moves an axis to a world coordinate.'));
    const slices: HTMLInputElement[] = [], sliders: HTMLInputElement[] = [], labels: HTMLElement[] = [], sliceRows: HTMLElement[] = [], orderLabels: HTMLElement[] = [], selectors: HTMLSelectElement[] = [];
    let ordered: number[] = [];
    const read = (): ViewerChange => {
      const ndim = this.frame!.dimensions.order.length, steps = slices.map(input => Number(input.value)), ndisplay = Number(mode.value);
      if (ordered.length !== ndim || [...ordered].sort((a,b) => a-b).some((value,index) => value !== index) ||
          ![2,3].includes(ndisplay) || ndisplay > ndim ||
          steps.some((value,index) => slices[index].value.trim() === '' || !Number.isSafeInteger(value) || value < 0 || (value >= this.frame!.dimensions.counts[index] && value !== this.frame!.dimensions.steps[index])))
        throw new Error('Choose a valid slice for each axis and a supported display mode.');
      return { operation: 'dimensions', value: { order: [...ordered], ndisplay: ndisplay as 2 | 3, steps } };
    };
    const displayOrder = () => {
      const ndisplay = Number(mode.value);
      const displayed = new Set(ordered.slice(-ndisplay));
      slices.forEach((input,axis) => { input.dataset.displayed = sliders[axis].dataset.displayed = String(displayed.has(axis)); sliceRows[axis].hidden = displayed.has(axis); });
      selectors.forEach((select,position) => {
        select.value = String(ordered[position]);
        const remaining = selectors.length-position;
        const label = remaining === 1 ? 'Horizontal axis' : remaining === 2 ? 'Vertical axis'
          : remaining === 3 && ndisplay === 3 ? 'Depth axis' : `Sliced axis ${position+1}`;
        select.setAttribute('aria-label',label);
        orderLabels[position].textContent = label;
      });
    };
    mode.addEventListener('change', () => { displayOrder(); this.edit('dimensions', read); });
    const fields: Field[] = [mode,roll,transpose,...actionButtons];
    const fixed = fields.length;
    const worlds: HTMLInputElement[] = [], goes: HTMLButtonElement[] = [], focuses: HTMLButtonElement[] = [];
    return { root, fields, update: frame => {
      const d = frame.dimensions;
      if (slices.length !== d.order.length) {
        axes.replaceChildren(); order.replaceChildren(); coordinates.replaceChildren(); slices.length = sliders.length = labels.length = sliceRows.length = orderLabels.length = selectors.length = worlds.length = goes.length = focuses.length = 0; fields.splice(fixed);
        for (let axis = 0; axis < d.order.length; axis++) {
          const label = node('p'), row = node('label'), input = this.input(`Slice axis ${axis}`, 'number', 'dimensions', read), select = node('select');
          const slider=node('input');slider.type='range';slider.min='0';slider.step='1';slider.setAttribute('aria-label',`Slice slider axis ${axis}`);
          slider.addEventListener('input',()=>{input.value=slider.value;this.edit('dimensions',read);});
          input.addEventListener('input',()=>{if(input.value.trim() && Number.isFinite(Number(input.value)))slider.value=input.value;});
          input.dataset.latestSlice = slider.dataset.latestSlice = 'true';
          input.min = '0'; input.step = '1'; labels.push(label); sliceRows.push(row); slices.push(input); sliders.push(slider); selectors.push(select); fields.push(input,slider,select);
          select.addEventListener('change', () => {
            const from = ordered.indexOf(Number(select.value));
            if (from < 0) return;
            [ordered[from],ordered[axis]] = [ordered[axis],ordered[from]];
            displayOrder(); this.edit('dimensions',read);
          });
          const orderLabel = node('label'); orderLabels.push(orderLabel);
          const world = node('input'), go = node('button', 'Go to'), focus = node('button', 'Use for keyboard');
          world.type = 'number'; world.step = 'any'; world.setAttribute('aria-label', `World coordinate axis ${axis}`);
          go.setAttribute('aria-label', `Go to world coordinate axis ${axis}`); go.dataset.requiresClean = 'true';
          focus.setAttribute('aria-label', `Use axis ${axis} for keyboard`); focus.dataset.requiresClean = 'true';
          go.addEventListener('click', () => {
            if (go.disabled) return;
            const value = Number(world.value);
            if (!world.value.trim() || !Number.isFinite(value)) { this.status.textContent = 'Enter a finite world coordinate before Go to.'; return; }
            this.act({operation:'dims-point',value:{axis,value}});
          });
          focus.addEventListener('click', () => { if (!focus.disabled) this.act({operation:'dims-focus',value:axis}); });
          worlds.push(world); goes.push(go); focuses.push(focus); fields.push(go, focus);
          const place = node('label', 'World coordinate'); place.append(world);
          row.append(node('span', `${d.labels[axis] || 'Axis'} (${axis}) · slice`), slider, input, place, go, focus);
          order.append(orderLabel,select); axes.append(row); coordinates.append(label);
        }
      }
      mode.value = String(d.ndisplay); ordered = [...d.order];
      const three = mode.querySelector<HTMLOptionElement>('option[value="3"]'); if (three) three.disabled = d.order.length < 3;
      for (let axis = 0; axis < slices.length; axis++) {
        const select = selectors[axis], signature = JSON.stringify(d.labels);
        if (select.dataset.axes !== signature) {
          select.replaceChildren(); select.dataset.axes = signature;
          d.labels.forEach((name,index) => { const option = node('option', `${name || 'Axis'} (${index})`); option.value = String(index); select.append(option); });
        }
        sliceRows[axis].querySelector('span')!.textContent = `${d.labels[axis] || 'Axis'} (${axis}) · slice`;
        const unit = d.units ? ` ${d.units[axis]}` : '', span = d.ranges[axis];
        labels[axis].textContent = `${d.labels[axis] || 'Axis'} (${axis}) · index 0–${d.counts[axis]-1} · coordinate ${d.point[axis]}${unit} · range ${span[0]}–${span[1]}${unit}, step ${span[2]}${d.displayed.includes(axis) ? ' · displayed' : ''}${d.rollable && !d.rollable[axis] ? ' · locked for Roll' : ''}`;
        if (document.activeElement !== worlds[axis]) worlds[axis].value = String(d.point[axis]);
        sliceRows[axis].dataset.keyboardAxis = String(d.lastUsed === axis);
        focuses[axis].setAttribute('aria-pressed', String(d.lastUsed === axis));
        focuses[axis].dataset.displayed = String(d.displayed.includes(axis) || d.counts[axis] <= 1); goes[axis].dataset.displayed = String(d.displayed.includes(axis));
        slices[axis].max = sliders[axis].max = String(Math.max(d.counts[axis]-1,d.steps[axis])); slices[axis].value = sliders[axis].value = String(d.steps[axis]);
      }
      displayOrder();
      const keyed = d.lastUsed === undefined || d.displayed.includes(d.lastUsed) || d.counts[d.lastUsed] <= 1 ? null : d.lastUsed;
      keyboardAxis.textContent = keyed === null ? 'Keyboard axis: none (no sliced axis has more than one slice).'
        : `Keyboard axis: ${d.labels[keyed] || 'Axis'} (${keyed}) at slice ${d.steps[keyed]} of 0–${d.counts[keyed]-1}, coordinate ${d.point[keyed]}${d.units ? ' '+d.units[keyed] : ''}.`;
      actionButtons[4].dataset.displayed = String(d.order.length < 3);
    } };
  }
  private grid():Row {
    const root=node('fieldset'),enabled=node('input'),fields:Field[]=[enabled];
    const inputs:HTMLInputElement[]=[];
    root.append(node('legend','Layer grid'),node('p','Compare layers in separate panels. Rows and columns use -1 for automatic layout. Stride groups consecutive layers; negative values reverse their order. Spacing below 1 is a fraction of panel size; 1 or more is pixels. Turn off the grid for painting and direct camera gestures.'));
    enabled.type='checkbox';enabled.setAttribute('aria-label','Show layer grid');
    const toggle=node('label','Show layer grid');toggle.append(enabled);root.append(toggle);
    const read=():ViewerChange=>{
      if(inputs.some(input=>!input.value.trim()))throw new Error('Complete grid rows, columns, stride and spacing.');
      return {operation:'grid',value:scientificGrid({enabled:enabled.checked,shape:inputs.slice(0,2).map(input=>Number(input.value)),stride:Number(inputs[2].value),spacing:Number(inputs[3].value)})};
    };
    for(const label of ['Grid rows','Grid columns','Grid stride','Grid spacing']){
      const input=this.input(label,'number','grid',read),row=node('label',label);
      input.step=label==='Grid spacing'?'any':'1';
      row.append(input);root.append(row);inputs.push(input);fields.push(input);
    }
    enabled.addEventListener('change',()=>this.edit('grid',read));
    return {root,fields,update:frame=>{
      const grid=frame.grid!;enabled.checked=grid.enabled;
      [...grid.shape,grid.stride,grid.spacing].forEach((value,index)=>{inputs[index].value=String(value);});
    }};
  }
  private thickness():Row {
    const root=node('fieldset'),fields:Field[]=[],rows:HTMLElement[]=[],left:HTMLInputElement[]=[],right:HTMLInputElement[]=[];
    root.append(node('legend','Slice thickness'),node('p','Distances before and after the current slice, in world coordinates. Layer calibration determines these coordinates. A layer uses them when its slice projection is enabled.'));
    const read=():ViewerChange=>{
      if([...left,...right].some(input=>!input.value.trim()))throw new Error('Complete both slice margins for every axis.');
      return {operation:'slice-margins',value:sliceMargins({left:left.map(input=>Number(input.value)),right:right.map(input=>Number(input.value))},this.frame!.dimensions.order.length)};
    };
    return {root,fields,update:frame=>{
      const dims=frame.dimensions;
      if(left.length!==dims.order.length){
        rows.forEach(row=>row.remove());rows.length=left.length=right.length=fields.length=0;
        dims.order.forEach((_,axis)=>{
          const row=node('fieldset');row.append(node('legend',`${dims.labels[axis]} (${axis})`));
          for(const [side,inputs] of [['before',left],['after',right]] as const){
            const label=node('label',`Distance ${side}`),input=this.input(`Slice margin ${side} ${dims.labels[axis]} (${axis})`,'number','thickness',read);
            input.min='0';input.step='any';label.append(input);row.append(label);inputs.push(input);fields.push(input);
          }
          rows.push(row);root.append(row);
        });
      }
      rows.forEach((row,axis)=>{
        row.hidden=dims.displayed.includes(axis);
        row.querySelector('legend')!.textContent=`${dims.labels[axis]} (${axis})`;
        left[axis].setAttribute('aria-label',`Slice margin before ${dims.labels[axis]} (${axis})`);
        right[axis].setAttribute('aria-label',`Slice margin after ${dims.labels[axis]} (${axis})`);
        left[axis].value=String(dims.margins!.left[axis]);right[axis].value=String(dims.margins!.right[axis]);
      });
    }};
  }
  private projection(id:string):Row {
    const root=node('fieldset'),title=node('legend'),select=node('select');
    for(const [value,label] of [['none','Current slice'],['sum','Sum'],['mean','Mean'],['max','Maximum'],['min','Minimum']]){
      const option=node('option',label);option.value=value;select.append(option);
    }
    select.addEventListener('change',()=>this.edit(`projection:${id}`,()=>({operation:'image-projection',value:{layer:id,mode:imageProjection(select.value)}})));
    root.append(title,node('p','Combine neighboring slices for display using Slice thickness. Original values stay unchanged; saving a projected scientific image requires an explicit processing operation.'),select);
    return {root,fields:[select],update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!;
      title.textContent=`${layer.name} · slice projection`;select.setAttribute('aria-label',`Image slice projection ${layer.name}`);select.value=layer.imageProjection!;
    }};
  }
  private camera(): Row {
    const root=node('fieldset'), fields:Field[]=[], inputs:HTMLInputElement[]=[], directions:HTMLSelectElement[]=[];
    root.append(node('legend','Camera'),node('p','Center uses world coordinates in depth, vertical, horizontal order. Angles and perspective apply in 3D; 0° perspective is orthographic.'));
    const read=():ViewerChange=>{
      const values=inputs.map(input=>Number(input.value));
      if(inputs.some(input=>!input.value.trim()) || values.some(value=>!Number.isFinite(value)||Math.abs(value)>Number.MAX_SAFE_INTEGER) ||
         values[6]<1e-6 || values[6]>1e6 || values[7]<0 || values[7]>=180)
        throw new Error('Enter finite camera coordinates, a zoom from 0.000001 to 1000000, and perspective from 0 to below 180°.');
      return {operation:'camera',value:{center:values.slice(0,3),angles:values.slice(3,6),zoom:values[6],perspective:values[7],
        orientation:directions.map(select=>select.value) as ScientificFrame['camera']['orientation']}};
    };
    for(const label of ['Camera center depth','Camera center vertical','Camera center horizontal','Camera angle depth','Camera angle vertical','Camera angle horizontal','Camera zoom','Camera perspective']){
      const input=this.input(label,'number','camera',read),row=node('label',label); input.step='any';row.append(input);root.append(row);inputs.push(input);fields.push(input);
    }
    inputs[6].min='0.000001';inputs[6].max='1000000';inputs[7].min='0';inputs[7].max='179.999';
    const options=[['towards','away'],['up','down'],['left','right']];
    ['Depth direction','Vertical direction','Horizontal direction'].forEach((label,index)=>{
      const select=node('select'),row=node('label',label);select.setAttribute('aria-label',label);
      for(const value of options[index]){const option=node('option',value);option.value=value;select.append(option);}
      select.addEventListener('change',()=>this.edit('camera',read));row.append(select);root.append(row);directions.push(select);fields.push(select);
    });
    const reset=node('button','Reset camera and fit layers');
    reset.addEventListener('click',()=>{if(reset.disabled)return;this.edit('camera',()=>({operation:'camera-reset',value:{}}));void this.submit();});
    root.append(reset);fields.push(reset);
    return {root,fields,update:frame=>{
      const c=frame.camera,values=[...c.center,...c.angles,c.zoom,c.perspective];
      inputs.forEach((input,index)=>{input.value=String(values[index]);input.dataset.displayed=String(frame.dimensions.ndisplay===2 && [3,4,5,7].includes(index));});
      directions.forEach((select,index)=>{select.value=c.orientation[index];select.dataset.displayed=String(frame.dimensions.ndisplay===2 && index===0);});
    }};
  }
  private layerSelection():Row {
    const root=node('fieldset'),status=node('p'),fields:Field[]=[];
    const list=node('div'),clear=node('button','Clear layer selection'),ownerEpoch=this.epoch;
    root.append(node('legend','Selected layers'),node('p','Choose one layer to activate its native tools, or select several layers together. This temporary selection is separate from selected points or shapes and is not remembered with the saved view.'),status,list,clear);
    const rows=new Map<string,{root:HTMLElement;label:HTMLElement;select:HTMLButtonElement;toggle:HTMLButtonElement;frame:string;epoch:number}>();
    const submit=(frame:string,value:{layer:string|null;action:'replace'|'toggle'|'clear'})=>{
      if(ownerEpoch!==this.epoch||this.rows.get('layer-selection')?.root!==root||this.frame?.frame!==frame)return;
      this.edit('layer-selection',()=>({operation:'layer-selection',value}));
      void this.submit();
    };
    clear.dataset.requiresClean='true';fields.push(clear);
    clear.addEventListener('click',()=>{if(!clear.disabled&&this.frame)submit(this.frame.frame,{layer:null,action:'clear'});});
    return {root,fields,update:frame=>{
      const selection=frame.layerSelection!;
      const active=frame.layers.find(layer=>layer.id===selection.active);
      status.textContent=active?`Active layer: ${active.name}`:selection.selected.length?`${selection.selected.length} layers selected; no single active layer.`:'No layers selected.';
      const ids=new Set(frame.layers.map(layer=>layer.id));
      for(const [id,row] of rows)if(!ids.has(id)){
        row.root.remove();fields.splice(fields.indexOf(row.select),1);fields.splice(fields.indexOf(row.toggle),1);rows.delete(id);
      }
      for(const layer of [...frame.layers].reverse()){
        let row=rows.get(layer.id);
        if(!row){
          const line=node('div'),label=node('span'),select=node('button'),toggle=node('button');line.append(label,select,toggle);
          row={root:line,label,select,toggle,frame:frame.frame,epoch:this.epoch};rows.set(layer.id,row);fields.push(select,toggle);
          const owned=row;
          for(const [button,action] of [[select,'replace'],[toggle,'toggle']] as const)button.addEventListener('click',()=>{
            if(rows.get(layer.id)===owned&&owned.epoch===this.epoch&&!button.disabled)submit(owned.frame,{layer:layer.id,action});
          });
          select.dataset.requiresClean=toggle.dataset.requiresClean='true';
        }
        row.label.textContent=`${layer.name} · ${layer.type}${selection.selected.includes(layer.id)?' · selected':''}`;
        row.select.textContent=`Select layer ${layer.name}`;
        row.toggle.textContent=`${selection.selected.includes(layer.id)?'Remove':'Add'} layer ${layer.name} ${selection.selected.includes(layer.id)?'from':'to'} selection`;
        row.frame=frame.frame;row.epoch=this.epoch;
        list.append(row.root);
      }
    }};
  }

  private layerOrder(): Row {
    const root=node('fieldset'); root.append(node('legend','Layer stacking'),node('p','Position 1 is the front layer. Reordering changes this view only.'));
    const fields: Field[]=[];
    const rows=new Map<string,{root:HTMLElement; label:HTMLElement; select:HTMLSelectElement}>();
    let order:string[]=[];
    const refresh=()=>{
      for(const [id,row] of rows)row.select.value=String(order.indexOf(id));
    };
    return {root,fields,update:frame=>{
      order=frame.layers.map(layer=>layer.id).reverse();
      for(const [id,row] of rows)if(!order.includes(id)){row.root.remove();fields.splice(fields.indexOf(row.select),1);rows.delete(id);}
      for(const layer of [...frame.layers].reverse()){
        let row=rows.get(layer.id);
        if(!row){
          const line=node('label'),label=node('span'),select=node('select');line.append(label,select);root.append(line);
          row={root:line,label,select};rows.set(layer.id,row);fields.push(select);
          select.addEventListener('change',()=>{
            if(select.disabled)return;
            const position=Number(select.value),from=order.indexOf(layer.id);
            if(!Number.isInteger(position)||position<0||position>=order.length||from<0)return;
            const next=[...order];next.splice(from,1);next.splice(position,0,layer.id);
            this.edit('layer-order',()=>({operation:'layer-order',value:[...order].reverse()}));
            if(this.draft?.group==='layer-order'){order=next;refresh();}
          });
        }
        row.label.textContent=layer.name;row.select.setAttribute('aria-label',`Stack position ${layer.name}`);
        row.select.replaceChildren();
        order.forEach((_,index)=>{const option=node('option',`${index+1}${index===0?' · front':index===order.length-1?' · back':''}`);option.value=String(index);row.select.append(option);});
      }
      refresh();
    }};
  }
  private layer(id: string): Row {
    const root = node('fieldset'), title = node('legend'), blending = node('select'), blendLabel = node('label','Blend layers'), blendHint = node('p');
    let supportsBlending = false;
    const describeBlend=()=>{
      const words:Record<string,string>={translucent:'Combine layers with opacity and depth.',translucent_no_depth:'Combine layers in stacking order, ignoring depth.',additive:'Add layer colors; bright overlaps can become white.',minimum:'Keep the darkest color at each position. Useful with inverted colors.',opaque:'Show this layer without blending its colors with layers behind it.',multiplicative:'Multiply layer colors; overlapping layers become darker.'};
      blendHint.textContent=words[blending.value]??'';
    };
    blendHint.id=`scientific-blend-${id}`;blending.setAttribute('aria-describedby',blendHint.id);
    const read = (): ViewerChange => {
      const value = Number(opacity.value);
      if (opacity.value.trim() === '' || !Number.isFinite(value) || value < 0 || value > 1)
        throw new Error('Layer opacity must be between 0 and 1.');
      return { operation: 'layer-presentation', value: { layer: id, visible: visible.checked, opacity: value, ...(supportsBlending?{blending:blending.value as typeof layerBlending[number]}:{}) } };
    };
    const visible = this.input('Layer visible', 'checkbox', id, read), opacity = this.input('Layer opacity', 'number', id, read);
    opacity.min = '0'; opacity.max = '1'; opacity.step = '.05';
    for(const mode of layerBlending){const option=node('option',mode==='translucent_no_depth'?'Translucent (ignore depth)':mode[0].toUpperCase()+mode.slice(1));option.value=mode;blending.append(option);}
    blending.addEventListener('change',()=>{describeBlend();this.edit(id,read);});blendLabel.append(blending);
    root.append(title, node('label', 'Visible'), visible, node('label', 'Opacity'), opacity, blendLabel, blendHint);
    return { root, fields: [visible,opacity,blending], update: frame => {
      const layer = frame.layers.find(layer => layer.id === id)!;
      title.textContent = `${layer.name} · ${layer.type}`;
      visible.setAttribute('aria-label', `Show ${layer.name}`); opacity.setAttribute('aria-label', `Opacity ${layer.name}`);
      visible.checked = layer.visible; opacity.value = String(layer.opacity);
      supportsBlending=!!layer.blending;blendLabel.hidden=blendHint.hidden=!supportsBlending;blending.dataset.displayed=String(!supportsBlending);
      blending.value=layer.blending??'';blending.setAttribute('aria-label',`Blending ${layer.name}`);describeBlend();
    } };
  }
  private image(id: string): Row {
    const group = `image:${id}`, root = node('fieldset'), title = node('legend');
    const numbers = new Map<string, HTMLInputElement>();
    const mode = node('select'), automatic = this.input('Automatic contrast', 'checkbox', group, read);
    function read(): ViewerChange {
      const value = (key: string) => { const input = numbers.get(key)!; if (!input.value.trim()) throw new Error('Enter a value for each image display setting.'); return Number(input.value); };
      return {operation:'image-presentation',value:{layer:id,settings:imageAppearance({
        contrastLimits:[value('Contrast minimum'),value('Contrast maximum')], gamma:value('Gamma'),
        rendering:mode.value, isoThreshold:value('Isosurface threshold'), attenuation:value('Attenuation'), autoContrast:automatic.checked,
      })}};
    }
    const fields: Field[] = [automatic,mode];
    const conditional = new Map<string, HTMLElement>();
    const appearance = () => {
      for (const label of ['Contrast minimum','Contrast maximum']) numbers.get(label)!.dataset.displayed=String(automatic.checked);
      conditional.get('Isosurface threshold')!.hidden=mode.value!=='iso';
      conditional.get('Attenuation')!.hidden=mode.value!=='attenuated_mip';
    };
    root.append(title,node('p','Display only. Native values and calibration stay unchanged. Volume rendering applies in 3D.'));
    const autoLabel=node('label','Adjust contrast for each slice'); autoLabel.append(automatic); root.append(autoLabel);
    for (const label of ['Contrast minimum','Contrast maximum','Gamma','Isosurface threshold','Attenuation']) {
      const input=this.input(label,'number',group,read), row=node('label',label);
      input.step='any'; if(label==='Gamma'||label==='Attenuation') input.min='0';
      row.append(input);root.append(row);numbers.set(label,input);fields.push(input);conditional.set(label,row);
    }
    const renderingLabel=node('label','Volume rendering'); renderingLabel.append(mode); root.append(renderingLabel);
    for(const [key,label] of imageRendering){const option=node('option',label);option.value=key;mode.append(option);}
    automatic.addEventListener('input',()=>{appearance();this.paint();});
    mode.addEventListener('change',()=>{appearance();this.edit(group,read);});
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!, settings=layer.imageAppearance!;
      title.textContent=`${layer.name} · image appearance`;
      automatic.setAttribute('aria-label',`Automatic contrast ${layer.name}`);mode.setAttribute('aria-label',`Volume rendering ${layer.name}`);
      automatic.checked=settings.autoContrast;mode.value=settings.rendering;
      const values=[...settings.contrastLimits,settings.gamma,settings.isoThreshold,settings.attenuation];
      [...numbers].forEach(([label,input],index)=>{input.value=String(values[index]);input.setAttribute('aria-label',`${label} ${layer.name}`);});
      appearance();
    }};
  }
  private labelsDisplay(id: string): Row {
    const group = `labels:${id}`, root = node('fieldset'), title = node('legend');
    const selected = this.input('Selected label', 'text', group, read), only = this.input('Show only the selected label', 'checkbox', group, read);
    const contour = this.input('Contour width', 'number', group, read), background = this.input('Background label', 'text', group, read);
    const seed = this.input('Color seed', 'number', group, read), colors = node('select'), shuffle = node('button', 'New random seed');
    const direct = node('textarea'), rendering = node('select'), gradient = node('select'), tooMany = node('p');
    const seedRow = node('label', 'Color seed '), directRow = node('label', 'Direct colors '), gradientRow = node('label', 'Isosurface shading ');
    const swatch = node('span'); swatch.className = 'scientific-label-swatch'; swatch.setAttribute('role', 'img');
    let accepted: LabelsAppearance | null = null;
    function read(): ViewerChange {
      if (!accepted) throw new Error('Refresh the preview before changing label display.');
      const choice = colors.value;
      const whole = (input: HTMLInputElement, what: string) => {
        if (!labelId(input.value.trim())) throw new Error(`${what} must be a whole number.`);
        return input.value.trim();
      };
      if (contour.value.trim() === '' || !Number.isSafeInteger(Number(contour.value)) || Number(contour.value) < 0)
        throw new Error('Contour width must be a whole number of pixels, zero or more.');
      if (choice === 'seeded' && (seed.value.trim() === '' || !Number.isSafeInteger(Number(seed.value)) || Number(seed.value) < 0 || Number(seed.value) > MAX_COLOR_SEED))
        throw new Error(`Automatic color seeds are whole numbers from 0 to ${MAX_COLOR_SEED}.`);
      // Direct colors keep the automatic choice they replace, as napari keeps its automatic colormap.
      const keptAuto = accepted.colorMode === 'auto' && accepted.autoSource !== 'other';
      return {operation: 'labels-presentation', value: {layer: id, settings: labelsAppearance({
        contour: Number(contour.value), selectedLabel: whole(selected, 'The selected label'), showSelectedLabel: only.checked,
        colorMode: choice === 'direct' ? 'direct' : 'auto', backgroundValue: whole(background, 'The background label'),
        autoSource: choice === 'direct' ? (keptAuto ? accepted.autoSource : 'original') : choice,
        autoSeed: choice === 'seeded' ? Number(seed.value) : choice === 'direct' && keptAuto ? accepted.autoSeed : null,
        directColors: choice === 'direct' ? parseDirectColors(direct.value) : accepted.directColors,
        rendering: rendering.value, isoGradientMode: gradient.value,
      })}};
    }
    const show = () => {
      seedRow.hidden = colors.value !== 'seeded'; seed.dataset.displayed = shuffle.dataset.displayed = String(seedRow.hidden);
      directRow.hidden = colors.value !== 'direct'; direct.dataset.displayed = String(directRow.hidden);
      gradientRow.hidden = rendering.value !== 'iso_categorical'; gradient.dataset.displayed = String(gradientRow.hidden);
    };
    contour.min = '0'; contour.step = '1'; seed.min = '0'; seed.max = String(MAX_COLOR_SEED); seed.step = '1'; direct.rows = 6;
    for (const [key, label] of labelsRendering) { const option = node('option', label); option.value = key; rendering.append(option); }
    for (const [key, label] of labelsGradient) { const option = node('option', label); option.value = key; gradient.append(option); }
    colors.addEventListener('change', () => { show(); this.edit(group, read); });
    rendering.addEventListener('change', () => { show(); this.edit(group, read); });
    gradient.addEventListener('change', () => this.edit(group, read));
    direct.addEventListener('input', () => this.edit(group, read));
    shuffle.addEventListener('click', () => {
      if (shuffle.disabled) return;
      seed.value = String(Math.floor(Math.random() * (MAX_COLOR_SEED + 1))); this.edit(group, read);
    });
    const row = (label: string, field: HTMLElement) => { const line = node('label', `${label} `); line.append(field); root.append(line); };
    root.append(title, node('p', 'Changing these settings leaves label values, identifiers and calibration unchanged. The background label is the exception: as in napari, Erase writes it into the data, so after you change it, erasing writes the new background label.'));
    row('Selected label', selected); root.append(swatch); row('Show only the selected label', only);
    row('Contour width (pixels)', contour);
    root.append(node('p', 'Zero fills each label. One or more draws only its outline, that many pixels wide, in 2D.'));
    row('Colors', colors); seedRow.append(seed, shuffle); root.append(seedRow);
    directRow.append(direct, node('span', ' One line per label: “label: red green blue alpha”, values 0–1. “other” colors every label not listed.'));
    root.append(directRow, tooMany);
    row('Background label', background); root.append(node('p', 'Erase writes this label into the label data, as napari’s eraser does.')); row('3D rendering', rendering); gradientRow.append(gradient); root.append(gradientRow);
    return {root, fields: [selected, only, contour, colors, seed, shuffle, direct, background, rendering, gradient], update: frame => {
      const layer = frame.layers.find(layer => layer.id === id)!, settings = layer.labelsAppearance!;
      accepted = structuredClone(settings); title.textContent = `${layer.name} · label display`;
      for (const [field, label] of [[selected, 'Selected label'], [only, 'Show only the selected label'], [contour, 'Contour width'], [colors, 'Label colors'],
        [seed, 'Color seed'], [direct, 'Direct colors'], [background, 'Background label'], [rendering, '3D rendering'], [gradient, 'Isosurface shading']] as const)
        field.setAttribute('aria-label', `${label} ${layer.name}`);
      shuffle.setAttribute('aria-label', `New random color seed ${layer.name}`);
      const choices: [string, string][] = [['original', 'Automatic (original colors)'], ['seeded', 'Automatic (new colors from a seed)']];
      if (settings.colorMode === 'auto' && settings.autoSource === 'other') choices.push(['other', 'Automatic (current colors, set in Python)']);
      if (settings.directColors !== null) choices.push(['direct', 'Direct colors']);
      colors.replaceChildren(...choices.map(([key, label]) => { const option = node('option', label); option.value = key; return option; }));
      colors.value = settings.colorMode === 'direct' ? 'direct' : settings.autoSource;
      selected.value = settings.selectedLabel; only.checked = settings.showSelectedLabel; contour.value = String(settings.contour);
      seed.value = settings.autoSeed === null ? '' : String(settings.autoSeed); direct.value = directColorsText(settings.directColors);
      background.value = settings.backgroundValue; rendering.value = settings.rendering; gradient.value = settings.isoGradientMode;
      tooMany.textContent = settings.directColors === null ? 'This layer has more direct colors than can be edited here; change them in Python.' : '';
      // napari's label control box: the selected label's color, transparent for the background label.
      const color = layer.labelsSelectedColor ?? null;
      swatch.dataset.color = color === null ? 'background' : color.join(' ');
      swatch.style.backgroundColor = color === null ? 'transparent' : `rgba(${color.slice(0, 3).map(n => Math.round(255 * n)).join(', ')}, ${color[3]})`;
      swatch.setAttribute('aria-label', color === null ? `Selected label color ${layer.name}: background (transparent)`
        : `Selected label color ${layer.name}: red ${color[0]}, green ${color[1]}, blue ${color[2]}, alpha ${color[3]}`);
      show();
    }};
  }
  private histogram(id: string): Row {
    const group = `histogram:${id}`, root = node('fieldset'), title = node('legend'), chart = node('div'), caption = node('p');
    const enabled = this.input('Show histogram', 'checkbox', group, read), bins = this.input('Histogram bins', 'number', group, read);
    const log = this.input('Logarithmic counts', 'checkbox', group, read), samples = this.input('Histogram sample limit', 'number', group, read), mode = node('select');
    const clear = node('button', 'Reset histogram');
    function read(): ViewerChange {
      if (!bins.value.trim() || !samples.value.trim()) throw new Error('Enter the number of bins and a sample limit.');
      return {operation: 'image-histogram', value: {layer: id, settings: histogramSettings({enabled: enabled.checked, bins: Number(bins.value), mode: mode.value,
        logScale: log.checked, maxSamples: Number(samples.value)})}};
    }
    bins.min = '1'; bins.max = String(MAX_HISTOGRAM_BINS); bins.step = '1'; samples.min = '1'; samples.step = '1';
    for (const [key, label] of [['canvas', 'Displayed slice'], ['full', 'Whole array (sampled)']]) { const option = node('option', label); option.value = key; mode.append(option); }
    mode.addEventListener('change', () => this.edit(group, read));
    clear.dataset.requiresClean = 'true';
    clear.addEventListener('click', () => {
      if (clear.disabled) return;
      this.edit(group, () => ({operation: 'image-contrast', value: {layer: id, settings: contrastAction({action: 'reset-histogram'})}})); void this.submit();
    });
    const row = (label: string, field: HTMLElement) => { const line = node('label', `${label} `); line.append(field); root.append(line); };
    root.append(title, node('p', 'Counts come from napari’s histogram of the native values: the displayed slice, or the whole array sampled up to the limit. Display only.'));
    row('Show histogram', enabled); row('Bins', bins); row('Values from', mode); row('Logarithmic counts', log); row('Sample limit', samples);
    root.append(clear, chart, caption);
    return {root, fields: [enabled, bins, mode, log, samples, clear], update: frame => {
      const layer = frame.layers.find(layer => layer.id === id)!, histogram = layer.imageHistogram!;
      title.textContent = `${layer.name} · histogram`;
      for (const [field, label] of [[enabled, 'Show histogram'], [bins, 'Histogram bins'], [mode, 'Histogram values from'], [log, 'Logarithmic counts'],
        [samples, 'Histogram sample limit'], [clear, 'Reset histogram']] as const)
        field.setAttribute('aria-label', `${label} ${layer.name}`);
      enabled.checked = histogram.enabled; bins.value = String(histogram.bins); mode.value = histogram.mode; log.checked = histogram.logScale;
      samples.value = String(histogram.maxSamples);
      chart.replaceChildren(...(histogram.counts ? [histogramChart(histogram, `Histogram of ${layer.name}`)] : []));
      caption.textContent = histogram.counts && histogram.binEdges
        ? `${histogram.counts.length} bins from ${histogram.binEdges[0]} to ${histogram.binEdges[histogram.binEdges.length - 1]}${histogram.logScale ? ' · log10(count + 1)' : ''} · tallest ${Math.max(...histogram.counts)}`
        : 'Histogram off.';
    }};
  }
  private contrastRange(id: string): Row {
    const group = `contrast:${id}`, root = node('fieldset'), title = node('legend');
    const low = this.input('Contrast range minimum', 'number', group, range), high = this.input('Contrast range maximum', 'number', group, range);
    const auto = node('button', 'Auto-contrast once'), reset = node('button', 'Full contrast range'), fit = node('button', 'Autoscale range to data');
    function range(): ViewerChange {
      if (!low.value.trim() || !high.value.trim()) throw new Error('Enter both ends of the contrast range.');
      return {operation: 'image-contrast', value: {layer: id, settings: contrastAction({action: 'set-range', range: [Number(low.value), Number(high.value)]})}};
    }
    low.step = high.step = 'any';
    for (const [button, action] of [[auto, 'auto-once'], [reset, 'reset-range'], [fit, 'autoscale-range']] as const) {
      button.dataset.requiresClean = 'true';
      button.addEventListener('click', () => {
        if (button.disabled) return;
        this.edit(group, () => ({operation: 'image-contrast', value: {layer: id, settings: contrastAction({action})}})); void this.submit();
      });
    }
    const row = (label: string, field: HTMLElement) => { const line = node('label', `${label} `); line.append(field); root.append(line); };
    root.append(title, node('p', 'The contrast slider’s range. Auto-contrast once (key C on the preview) sets the contrast limits to the displayed slice’s range now, as napari’s Apply auto-contrast does. In napari’s contrast popup, Full range sets the slider to the whole integer type (offered for integer images only), and Autoscale range to data (its reset) autoscales the limits to the displayed slice, then narrows the slider to them.'), auto, reset, fit);
    row('Range minimum', low); row('Range maximum', high);
    return {root, fields: [auto, reset, fit, low, high], update: frame => {
      const layer = frame.layers.find(layer => layer.id === id)!, histogram = layer.imageHistogram!;
      title.textContent = `${layer.name} · contrast range`;
      for (const [field, label] of [[auto, 'Auto-contrast once'], [reset, 'Full contrast range'], [fit, 'Autoscale contrast range to data'],
        [low, 'Contrast range minimum'], [high, 'Contrast range maximum']] as const)
        field.setAttribute('aria-label', `${label} ${layer.name}`);
      low.value = String(histogram.range[0]); high.value = String(histogram.range[1]);
      reset.hidden = !histogram.integer; reset.dataset.displayed = String(!histogram.integer);
    }};
  }
  private plane(id:string):Row {
    const root=node('fieldset'),title=node('legend'),mode=node('select'),inputs:HTMLInputElement[]=[],fields:Field[]=[mode];
    const group=`plane:${id}`;
    root.append(title,node('p','Position, normal and thickness use the currently displayed data axes in depth, vertical, horizontal order. This changes the 3D view only. Switch to 3D to see the plane.'));
    for(const [value,label] of [['volume','Volume'],['plane','Slicing plane']]){const option=node('option',label);option.value=value;mode.append(option);}
    const modeRow=node('label','Show image as');modeRow.append(mode);root.append(modeRow);
    const read=():ViewerChange=>{
      if(inputs.some(input=>!input.value.trim()))throw new Error('Enter every plane coordinate and thickness.');
      const values=inputs.map(input=>Number(input.value));
      return {operation:'image-plane',value:{layer:id,settings:imagePlane({depiction:mode.value,position:values.slice(0,3),normal:values.slice(3,6),thickness:values[6]})}};
    };
    const names=['Plane position depth','Plane position vertical','Plane position horizontal','Plane normal depth','Plane normal vertical','Plane normal horizontal','Plane thickness'];
    for(const name of names){const input=this.input(name,'number',group,read),row=node('label',name);input.step='any';row.append(input);root.append(row);inputs.push(input);fields.push(input);}
    inputs[6].min='0';
    mode.addEventListener('change',()=>this.edit(group,read));
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!,settings=layer.imagePlane!;
      title.textContent=`${layer.name} · 3D image plane`;mode.setAttribute('aria-label',`Image depiction ${layer.name}`);mode.value=settings.depiction;
      [...settings.position,...settings.normal,settings.thickness].forEach((value,index)=>{inputs[index].value=String(value);inputs[index].setAttribute('aria-label',`${names[index]} ${layer.name}`);});
    }};
  }
  private colormap(id:string):Row {
    const root=node('fieldset'),title=node('legend'),select=node('select'),editor=node('textarea'),custom=node('label','Custom color map (JSON)');
    const group=`colormap:${id}`,fields:Field[]=[select,editor];
    let accepted:ImageColormap;
    const read=():ViewerChange=>({operation:'image-colormap',value:{layer:id,settings:select.value==='custom'?imageColormap(JSON.parse(editor.value)):select.value==='keep'?accepted:{name:select.value.slice(5)}}});
    const show=()=>{custom.hidden=select.value!=='custom';editor.dataset.displayed=String(custom.hidden);};
    root.append(title,node('p','Color changes this view only. Original intensities and measurements stay unchanged.'));
    const label=node('label','Color map');label.append(select);root.append(label);
    select.addEventListener('change',()=>{show();this.edit(group,read);});
    editor.rows=8;editor.addEventListener('input',()=>this.edit(group,read));custom.append(editor,node('span',' RGBA values are 0–1. Up to 1024 colors; the full view must fit its metadata allowance. Special colors cover missing, high and low values.'));
    root.append(custom);
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!;
      title.textContent=`${layer.name} · colors`;select.setAttribute('aria-label',`Color map ${layer.name}`);editor.setAttribute('aria-label',`Custom color map ${layer.name}`);
      select.replaceChildren();
      const current=node('option',`Current: ${layer.imageColormap!.name}`);current.value='keep';select.append(current);
      const edit=node('option','Edit custom colors…');edit.value='custom';select.append(edit);
      for(const name of layer.colormapChoices!){const option=node('option',name);option.value=`name:${name}`;select.append(option);}
      // A custom definition may reuse a built-in name. Never infer equality from its name.
      accepted=structuredClone(layer.imageColormap!);select.value='keep';editor.value=JSON.stringify(accepted,null,2);show();
    }};
  }
  private textDisplay(id:string):Row {
    const root=node('fieldset'),title=node('legend'),group=`text:${id}`,anchor=node('select'),blend=node('select');
    let ndim=2;
    const read=():ViewerChange=>{
      const number=(input:HTMLInputElement)=>{if(!input.value.trim())throw new Error('Complete the text display settings before applying.');return Number(input.value);};
      const parts=offset.value.split(',');
      if(parts.some(part=>!part.trim()))throw new Error('Enter one text offset or one offset per layer axis.');
      return {operation:'text-presentation',value:{layer:id,settings:textAppearance({visible:visible.checked,size:number(size),scaling:scaling.checked,
        anchor:anchor.value,blending:blend.value,translation:parts.length===1?Number(parts[0]):parts.map(Number),rotation:number(rotation)},ndim)}};
    };
    const visible=this.input('Show feature text','checkbox',group,read),size=this.input('Text size','number',group,read),scaling=this.input('Scale text with zoom','checkbox',group,read),rotation=this.input('Text rotation in degrees','number',group,read),offset=this.input('Text offset in data coordinates','text',group,read);
    size.min='0';size.step='any';rotation.step='any';
    for(const [value,label] of [['center','Center'],['upper_left','Upper left'],['upper_right','Upper right'],['lower_left','Lower left'],['lower_right','Lower right']]){const option=node('option',label);option.value=value;anchor.append(option);}
    for(const value of textBlending){const option=node('option',value.replaceAll('_',' '));option.value=value;blend.append(option);}
    anchor.addEventListener('change',()=>this.edit(group,read));blend.addEventListener('change',()=>this.edit(group,read));
    const fields=[visible,size,scaling,anchor,blend,rotation,offset];
    const labels=['Show feature text','Text size','Scale text with zoom','Text anchor','Text blending','Text rotation in degrees','Text offset in data coordinates'];
    root.append(title,node('p','Styles the layer’s existing text. Feature values, text templates and color mappings stay unchanged. Offset is one value for every axis, or comma-separated values in layer-axis order.'));
    fields.forEach((input,index)=>{const label=node('label',labels[index]);label.append(input);root.append(label);});
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!,value=layer.textAppearance!;ndim=layer.scale.length;
      title.textContent=`${layer.name} · feature text`;
      fields.forEach((input,index)=>input.setAttribute('aria-label',`${labels[index]} ${layer.name}`));
      visible.checked=value.visible;size.value=String(value.size);scaling.checked=value.scaling;anchor.value=value.anchor;blend.value=value.blending;rotation.value=String(value.rotation);offset.value=Array.isArray(value.translation)?value.translation.join(', '):String(value.translation);
    }};
  }
  private styleControls() {
    return {input: (label: string, type: string, group: string, read: () => ViewerChange) => this.input(label, type, group, read),
      edit: (group: string, read: () => ViewerChange) => this.edit(group, read), submit: () => { void this.submit(); }};
  }
  private styled(row: {root: HTMLElement; fields: Field[]; update(layers: ScientificFrame['layers']): void}): Row {
    return {root: row.root, fields: row.fields, update: frame => row.update(frame.layers)};
  }
  private points(id:string):Row {
    const root=node('fieldset'),title=node('legend'),group=`points:${id}`,shading=node('select');
    const read=():ViewerChange=>{
      const number=(input:HTMLInputElement)=>{if(!input.value.trim())throw new Error('Complete the point display settings before applying.');return Number(input.value);};
      return {operation:'point-presentation',value:{layer:id,settings:pointAppearance({shading:shading.value,
        projection:projection.value,antialiasing:number(antialiasing),canvasLimits:[number(minimum),number(maximum)]})}};
    };
    const projection=node('select');
    for(const [value,text] of [['none','Current plane'],['all','All in slice thickness'],['rescale_linear','Shrink linearly away from plane'],['rescale_spherical','Sphere intersection with plane']]){
      const option=node('option',text);option.value=value;projection.append(option);
    }
    projection.addEventListener('change',()=>this.edit(group,read));
    const antialiasing=this.input('Antialiasing in pixels','number',group,read);
    const minimum=this.input('Minimum marker size in pixels','number',group,read);
    const maximum=this.input('Maximum marker size in pixels','number',group,read);
    for(const [value,text] of [['none','Flat markers'],['spherical','Spherical shading']]){
      const option=node('option',text);option.value=value;shading.append(option);
    }
    shading.addEventListener('change',()=>this.edit(group,read));
    for(const input of [antialiasing,minimum,maximum]){input.min='0';input.step='any';}
    const fields=[shading,projection,antialiasing,minimum,maximum];
    const labels=['Point shading','Point slice projection','Antialiasing in pixels','Minimum marker size in pixels','Maximum marker size in pixels'];
    root.append(title,node('p','Marker display only. Point coordinates, individual sizes, colors and features stay unchanged. Projection uses the current slice thickness and native point sizes.'));
    fields.forEach((input,index)=>{
      const label=node('label',labels[index]);label.append(input);root.append(label);
    });
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!,value=layer.pointAppearance!;
      title.textContent=`${layer.name} · point display`;
      fields.forEach((input,index)=>input.setAttribute('aria-label',`${labels[index]} ${layer.name}`));
      shading.value=value.shading;projection.value=value.projection;antialiasing.value=String(value.antialiasing);
      minimum.value=String(value.canvasLimits[0]);maximum.value=String(value.canvasLimits[1]);
    }};
  }
  private surface(id:string):Row {
    const root=node('fieldset'),title=node('legend'),group=`surface:${id}`,fields:Field[]=[];
    const inputs=new Map<string,HTMLInputElement|HTMLSelectElement>(),labels=new Map<string,string>();
    const read=():ViewerChange=>{
      const number=(key:string)=>{const value=inputs.get(key)!.value;if(!value.trim())throw new Error('Complete the surface display settings before applying.');return Number(value);};
      const checked=(key:string)=>(inputs.get(key) as HTMLInputElement).checked;
      const line=(key:string)=>({visible:checked(`${key}.visible`),width:number(`${key}.width`),color:['r','g','b','a'].map(c=>number(`${key}.${c}`)),...(key==='wireframe'?{}:{length:number(`${key}.length`)})});
      return {operation:'surface-presentation',value:{layer:id,settings:surfaceAppearance({shading:inputs.get('shading')!.value,
        gamma:number('gamma'),contrastLimits:[number('low'),number('high')],autoContrast:checked('autoContrast'),
        wireframe:line('wireframe'),faceNormals:line('faceNormals'),vertexNormals:line('vertexNormals')})}};
    };
    const add=(parent:HTMLElement,key:string,label:string,type:'number'|'checkbox',min?:number,max?:number)=>{
      const input=node('input'),row=node('label',label);input.type=type;
      if(type==='number'){input.step='any';if(min!==undefined)input.min=String(min);if(max!==undefined)input.max=String(max);}
      input.addEventListener(type==='checkbox'?'change':'input',()=>this.edit(group,read));
      inputs.set(key,input);labels.set(key,label);fields.push(input);row.append(input);parent.append(row);
    };
    root.append(title,node('p','Display the mesh and its normals without changing vertices, triangles or measured values. Shading and normals are visible in 3D.'));
    const shading=node('select'),shadingRow=node('label','Shading');
    for(const [value,text] of [['none','Unlit'],['flat','Flat faces'],['smooth','Smooth surface']]){const option=node('option',text);option.value=value;shading.append(option);}
    shading.addEventListener('change',()=>this.edit(group,read));inputs.set('shading',shading);labels.set('shading','Shading');fields.push(shading);shadingRow.append(shading);root.append(shadingRow);
    add(root,'autoContrast','Automatic surface contrast','checkbox');add(root,'low','Surface contrast minimum','number');add(root,'high','Surface contrast maximum','number');add(root,'gamma','Surface gamma','number',Number.MIN_VALUE);
    for(const [key,label] of [['wireframe','Wireframe'],['faceNormals','Face normals'],['vertexNormals','Vertex normals']]){
      const section=node('details'),summary=node('summary',label);section.append(summary);root.append(section);
      add(section,`${key}.visible`,`Show ${label.toLowerCase()}`,'checkbox');add(section,`${key}.width`,`${label} width`,'number',0);
      if(key!=='wireframe')add(section,`${key}.length`,`${label} length`,'number');
      const color=node('fieldset');color.append(node('legend',`${label} color (RGBA, 0–1)`));section.append(color);
      for(const [channel,name] of [['r','red'],['g','green'],['b','blue'],['a','alpha']])add(color,`${key}.${channel}`,`${label} ${name}`,'number',0,1);
    }
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!,value=layer.surfaceAppearance!;title.textContent=`${layer.name} · surface display`;
      const flattened:Record<string,string|number|boolean>={shading:value.shading,gamma:value.gamma,autoContrast:value.autoContrast,low:value.contrastLimits[0],high:value.contrastLimits[1]};
      for(const key of ['wireframe','faceNormals','vertexNormals'] as const){const line=value[key];flattened[`${key}.visible`]=line.visible;flattened[`${key}.width`]=line.width;
        if('length' in line)flattened[`${key}.length`]=line.length;
        ['r','g','b','a'].forEach((c,i)=>{flattened[`${key}.${c}`]=line.color[i];});}
      for(const [key,input] of inputs){input.setAttribute('aria-label',`${labels.get(key)} ${layer.name}`);const value=flattened[key];if(typeof value==='boolean')(input as HTMLInputElement).checked=value;else input.value=String(value);}
    }};
  }
  private paths(id:string):Row {
    const root=node('fieldset'),title=node('legend'),help=node('p'),group=`paths:${id}`,fields:Field[]=[];
    const kind=this.frame!.layers.find(layer=>layer.id===id)!.pathAppearance!.kind;
    const inputs=new Map<string,HTMLInputElement|HTMLSelectElement>(),labels=new Map<string,string>();
    const read=():ViewerChange=>{
      const value:Record<string,unknown>={kind};
      for(const [key,input] of inputs){
        if(input.tagName==='INPUT' && input.type==='number'){
          if(!input.value.trim())throw new Error('Enter every line width and display length.');
          value[key]=Number(input.value);
        }else if(input.tagName==='INPUT' && input.type==='checkbox')value[key]=(input as HTMLInputElement).checked;
        else value[key]=input.value;
      }
      return {operation:'path-presentation',value:{layer:id,settings:pathAppearance(value,kind)}};
    };
    root.append(title,node('p','Color changes this view only. Original intensities and measurements stay unchanged.'));
    const numeric=(key:string,label:string,min?:number,max?:number,step='any')=>{
      const input=this.input(label,'number',group,read),row=node('label',label);input.step=step;
      if(min!==undefined)input.min=String(min);if(max!==undefined)input.max=String(max);
      inputs.set(key,input);labels.set(key,label);fields.push(input);row.append(input);root.append(row);
    };
    const choice=(key:string,label:string,options:readonly (readonly [string,string])[])=>{
      const input=node('select'),row=node('label',label);
      for(const [value,text] of options){const option=node('option',text);option.value=value;input.append(option);}
      input.addEventListener('change',()=>this.edit(group,read));inputs.set(key,input);labels.set(key,label);fields.push(input);row.append(input);root.append(row);
    };
    if(kind==='vectors'){
      help.textContent='Length scales the displayed displacement only; negative values reverse its display. Stored origins and directions stay unchanged. Projection uses the current slice thickness.';
      numeric('length','Length multiplier');numeric('width','Line width',0);
      choice('style','Vector style',[['line','Line'],['triangle','Triangle'],['arrow','Arrow']]);
      choice('projection','Slice projection',[['none','Current slice'],['all','All in slice thickness'],['fade','Fade through slice thickness']]);
    }else{
      help.textContent='Past and future lengths use native track time steps. Show division edges displays the saved lineage; these controls never change it.';
      numeric('width','Line width',.5,20);numeric('tail','Past length',0,Number.MAX_SAFE_INTEGER,'1');numeric('head','Future length',0,Number.MAX_SAFE_INTEGER,'1');
      for(const [key,label] of [['showIds','Show track IDs'],['showTail','Show trails'],['showGraph','Show division edges'],['hideCompleted','Hide completed tracks']]){
        const input=node('input'),row=node('label',label);input.type='checkbox';input.addEventListener('change',()=>this.edit(group,read));
        inputs.set(key,input);labels.set(key,label);fields.push(input);row.append(input);root.append(row);
      }
    }
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!,value=layer.pathAppearance!;
      title.textContent=`${layer.name} · ${kind==='vectors'?'vector display':'track display'}`;
      for(const [key,input] of inputs){
        input.setAttribute('aria-label',`${labels.get(key)} ${layer.name}`);
        const setting=(value as unknown as Record<string,unknown>)[key];
        if(typeof setting==='boolean')(input as HTMLInputElement).checked=setting;else input.value=String(setting);
      }
    }};
  }
  private sampling(id:string):Row {
    const root=node('fieldset'),title=node('legend'),two=node('select'),three=node('select'),kernel=node('textarea'),kernelRow=node('label','Custom 2D kernel (JSON matrix)');
    const group=`sampling:${id}`,fields:Field[]=[two,three,kernel];
    let acceptedKernel:number[][]=[[1]];
    const read=():ViewerChange=>({operation:'image-sampling',value:{layer:id,settings:imageSampling({interpolation2d:two.value,interpolation3d:three.value,kernel2d:two.value==='custom'?JSON.parse(kernel.value):acceptedKernel})}});
    root.append(title,node('p','Display interpolation changes how pixels are drawn between samples. It never resamples the scientific data. Nearest shows individual samples; other filters smooth the display.'));
    for(const [select,label,modes] of [[two,'2D interpolation',[...interpolationModes,'custom']],[three,'3D interpolation',interpolationModes]] as const){
      const row=node('label',label);row.append(select);root.append(row);
      for(const mode of modes){const option=node('option',mode);option.value=mode;select.append(option);}
      select.addEventListener('change',()=>{show();this.edit(group,read);});
    }
    kernelRow.append(node('span',' Used only for Custom 2D interpolation; other modes keep the previous accepted kernel.'));
    kernel.rows=4;kernel.addEventListener('input',()=>this.edit(group,read));kernelRow.append(kernel);root.append(kernelRow);
    const show=()=>{kernelRow.hidden=two.value!=='custom';kernel.dataset.displayed=String(two.value!=='custom');};
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!,settings=layer.imageSampling!;
      title.textContent=`${layer.name} · pixel display`;two.setAttribute('aria-label',`2D interpolation ${layer.name}`);three.setAttribute('aria-label',`3D interpolation ${layer.name}`);kernel.setAttribute('aria-label',`Custom 2D kernel ${layer.name}`);
      two.value=settings.interpolation2d;three.value=settings.interpolation3d;acceptedKernel=structuredClone(settings.kernel2d);kernel.value=JSON.stringify(settings.kernel2d);show();
    }};
  }
  private clipping(id:string):Row {
    const root=node('fieldset'),title=node('legend'),list=node('div'),add=node('button','Add clipping plane');
    const group=`clipping:${id}`,fields:Field[]=[add]; let name='';
    type PlaneRow={root:HTMLElement;legend:HTMLElement;inputs:HTMLInputElement[];enabled:HTMLInputElement;remove:HTMLButtonElement};
    const rows:PlaneRow[]=[];
    root.append(title,node('p','Clipping hides the negative side of each enabled plane. Position and normal use the currently displayed layer data axes (depth, vertical, horizontal), as in napari. Scientific values stay unchanged. Up to 64 planes per layer; large view metadata may require fewer.'),list,add);
    const names=['position depth','position vertical','position horizontal','normal depth','normal vertical','normal horizontal'];
    const read=():ViewerChange=>({operation:'clipping-planes',value:{layer:id,planes:clippingPlanes(rows.map(row=>{
      if(row.inputs.some(input=>!input.value.trim()))throw new Error('Enter every clipping-plane position and normal coordinate.');
      const values=row.inputs.map(input=>Number(input.value));
      return {position:values.slice(0,3),normal:values.slice(3),enabled:row.enabled.checked};
    }))}});
    const describe=()=>{
      fields.splice(1);
      rows.forEach((row,index)=>{
        row.legend.textContent=`Plane ${index+1}`;
        row.enabled.setAttribute('aria-label',`Enable clipping plane ${index+1} ${name}`);
        row.remove.setAttribute('aria-label',`Remove clipping plane ${index+1} ${name}`);
        row.inputs.forEach((input,n)=>input.setAttribute('aria-label',`Clipping plane ${index+1} ${names[n]} ${name}`));
        fields.push(row.enabled,...row.inputs,row.remove);
      });
      add.setAttribute('aria-label',`Add clipping plane ${name}`);
    };
    const append=(plane:ClippingPlane)=>{
      const section=node('fieldset'),legend=node('legend'),enabled=node('input'),remove=node('button','Remove plane'),inputs:HTMLInputElement[]=[];
      enabled.type='checkbox';enabled.checked=plane.enabled;
      const check=node('label','Enabled');check.append(enabled);section.append(legend,check);
      for(const [index,label] of names.entries()){
        const input=this.input(label,'number',group,read),line=node('label',label);input.step='any';
        input.value=String([...plane.position,...plane.normal][index]);line.append(input);section.append(line);inputs.push(input);
      }
      const row:PlaneRow={root:section,legend,inputs,enabled,remove};
      enabled.addEventListener('change',()=>this.edit(group,read));
      remove.addEventListener('click',()=>{if(remove.disabled)return;rows.splice(rows.indexOf(row),1);section.remove();describe();this.edit(group,read);});
      section.append(remove);rows.push(row);list.append(section);
    };
    add.addEventListener('click',()=>{if(add.disabled)return;append({position:[0,0,0],normal:[1,0,0],enabled:true});describe();this.edit(group,read);});
    return {root,fields,update:frame=>{
      const layer=frame.layers.find(layer=>layer.id===id)!;name=layer.name;title.textContent=`${name} · clipping planes`;
      const planes=layer.clippingPlanes!;
      while(rows.length>planes.length)rows.pop()!.root.remove();
      while(rows.length<planes.length)append(planes[rows.length]);
      planes.forEach((plane,index)=>{
        rows[index].enabled.checked=plane.enabled;
        [...plane.position,...plane.normal].forEach((value,n)=>rows[index].inputs[n].value=String(value));
      });
      describe();
    }};
  }
  private paint(): void {
    if (this.closed) return;
    const settled = this.settled();
    if (settled !== this.reportedSettled) { this.reportedSettled = settled; this.transport.changed?.(); }
    if (this.frame) {
      const keys = ['dimensions', ...(this.frame.grid?['grid']:[]), ...(this.frame.dimensions.margins?['thickness']:[]), 'camera', ...(this.frame.layerSelection?['layer-selection']:[]), 'layer-order', ...this.frame.layers.flatMap(layer => [layer.id,...(layer.imageProjection?[`projection:${layer.id}`]:[]),...(layer.textAppearance?[`text:${layer.id}`]:[]),...(layer.pointAppearance?[`points:${layer.id}`]:[]),...(layer.pointStyle?[`point-style:${layer.id}`]:[]),...(layer.shapeStyle?[`shape-style:${layer.id}`]:[]),...(layer.textContent?[`text-content:${layer.id}`]:[]),...(layer.surfaceAppearance?[`surface:${layer.id}`]:[]),...(layer.pathAppearance?[`paths:${layer.id}`]:[]),...(layer.labelsAppearance?[`labels:${layer.id}`]:[]),...(layer.imageAppearance?[`image:${layer.id}`]:[]),...(layer.imageHistogram?[`histogram:${layer.id}`,`contrast:${layer.id}`]:[]),...(layer.imagePlane?[`plane:${layer.id}`]:[]),...(layer.clippingPlanes?[`clipping:${layer.id}`]:[]),...(layer.imageSampling?[`sampling:${layer.id}`]:[]),...(layer.imageColormap?[`colormap:${layer.id}`]:[])])];
      for (const [key,row] of this.rows) if (!keys.includes(key) && key !== this.draft?.group) { row.root.remove(); this.rows.delete(key); }
      for (const key of keys) {
        let row = this.rows.get(key);
        if (!row) { row = key === 'dimensions' ? this.dimensions() : key === 'grid' ? this.grid() : key === 'thickness' ? this.thickness() : key.startsWith('projection:') ? this.projection(key.slice(11)) : key === 'camera' ? this.camera() : key === 'layer-selection' ? this.layerSelection() : key === 'layer-order' ? this.layerOrder() : key.startsWith('text:') ? this.textDisplay(key.slice(5)) : key.startsWith('points:') ? this.points(key.slice(7)) : key.startsWith('point-style:') ? this.styled(pointStyleRow(key.slice(12), this.styleControls())) : key.startsWith('shape-style:') ? this.styled(shapeStyleRow(key.slice(12), this.styleControls())) : key.startsWith('text-content:') ? this.styled(textContentRow(key.slice(13), this.styleControls())) : key.startsWith('surface:') ? this.surface(key.slice(8)) : key.startsWith('paths:') ? this.paths(key.slice(6)) : key.startsWith('colormap:') ? this.colormap(key.slice(9)) : key.startsWith('sampling:') ? this.sampling(key.slice(9)) : key.startsWith('clipping:') ? this.clipping(key.slice(9)) : key.startsWith('plane:') ? this.plane(key.slice(6)) : key.startsWith('image:') ? this.image(key.slice(6)) : key.startsWith('labels:') ? this.labelsDisplay(key.slice(7)) : key.startsWith('histogram:') ? this.histogram(key.slice(10)) : key.startsWith('contrast:') ? this.contrastRange(key.slice(9)) : this.layer(key); this.rows.set(key,row); this.body.append(row.root); }
        if (this.draft?.group !== key) row.update(this.frame);
      }
    }
    const stale = !!this.draft && this.draft.frame !== this.frame?.frame;
    const navigationClear = !this.busy && !this.uncertain && !this.draft;
    this.playback.setAvailable(this.available && navigationClear, navigationClear ? this.awaitingFrame : null);
    const disabled = !this.available || this.busy || this.playing() || this.uncertain || stale;
    for (const [key,row] of this.rows) for (const field of row.fields) {
      const replaceable = this.available && this.dimensionNavigation && this.busy && !this.uncertain && !stale && field.dataset.latestSlice === 'true';
      field.disabled = (disabled && !replaceable) || !!this.draft && this.draft.group !== key || field.dataset.displayed === 'true' || !!this.draft && field.dataset.requiresClean === 'true';
    }
    if (stale && !this.busy) this.status.textContent = 'The view changed while you were editing. Your unsent values are kept. Discard them and review the current view before applying changes.';
    this.apply.disabled = (disabled && !(this.available && this.dimensionNavigation && !this.uncertain && !stale)) || !this.draft;
    this.discard.hidden = !this.draft; this.discard.disabled = this.busy;
    this.refresh.disabled = this.busy || this.playing() || !this.session;
  }
  private async replaceTarget(change: Extract<ViewerChange, {operation:'dimensions'}>): Promise<void> {
    this.replacement = change;
    if (this.replacing) return;
    const epoch = this.epoch; this.replacing = true;
    try {
      while (this.replacement && this.dimensionNavigation && this.epoch === epoch) {
        const latest = this.replacement; this.replacement = null;
        await this.transport.replaceDimensions!(latest.value);
        if (this.epoch === epoch && this.dimensionNavigation) this.status.textContent = 'Preparing the latest requested slice…';
      }
    } catch (error) {
      if (this.epoch === epoch) this.status.textContent = `${error instanceof Error ? error.message : error}. Your unsent slice target is kept.`;
    } finally { if (this.epoch === epoch) { this.replacing = false; this.paint(); } }
  }
  private async submit(): Promise<void> {
    if (this.apply.disabled || !this.draft) return;
    const draft = this.draft, epoch = this.epoch;
    if (this.busy) {
      try { const change = draft.read(); if (this.dimensionNavigation && change.operation === 'dimensions') await this.replaceTarget(change); }
      catch (error) { this.status.textContent = String(error); }
      return;
    }
    try {
      const change = draft.read();
      this.dimensionNavigation = !!this.transport.replaceDimensions && change.operation === 'dimensions' &&
        change.value.ndisplay === this.frame?.dimensions.ndisplay && JSON.stringify(change.value.order) === JSON.stringify(this.frame?.dimensions.order);
      this.busy = true;
      this.status.textContent = 'Applying view changes…'; this.paint();
      const frame = await this.transport.send(draft.frame,change);
      if (this.closed || this.epoch !== epoch) return;
      if (this.frame?.frame === frame.frame && this.draft) {
        const latest = this.draft.read();
        const reached = this.dimensionNavigation && latest.operation === 'dimensions' &&
          JSON.stringify(latest.value) === JSON.stringify({order:frame.dimensions.order,ndisplay:frame.dimensions.ndisplay,steps:frame.dimensions.steps});
        if (this.draft.generation === draft.generation || reached) this.draft = null;
      }
      this.status.textContent = this.draft ? 'The view updated; your newer unsent target is kept. Review it before applying.' : 'View changes applied.';
    } catch (error) {
      if (!this.closed && this.epoch === epoch) {
        this.uncertain = this.busy;
        this.status.textContent = `${error instanceof Error ? error.message : error}${this.uncertain ? ' Check current view before retrying.' : ''}`;
      }
    } finally { if (this.epoch === epoch) { this.busy = false; this.dimensionNavigation = false; this.replacement = null; this.paint(); } }
  }
  private async recheck(): Promise<void> {
    if (this.refresh.disabled) return;
    const epoch = this.epoch;
    this.busy = true; this.paint();
    try {
      await this.transport.refresh();
      if (!this.closed && this.epoch === epoch) { this.uncertain = false; this.status.textContent = ''; }
    } catch (error) {
      if (!this.closed && this.epoch === epoch) this.status.textContent = error instanceof Error ? error.message : String(error);
    } finally { if (this.epoch === epoch) { this.busy = false; this.paint(); } }
  }
}
