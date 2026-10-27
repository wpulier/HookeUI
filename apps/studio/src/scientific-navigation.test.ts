import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import { ScientificNavigation, dimensionShortcut, type ViewerChange } from './scientific-navigation';
import { scientificFrame } from './scientific-view';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
let root: TestElement;
beforeEach(() => { const dom = syntheticDOM(); root = dom.host; vi.stubGlobal('document',dom.document); vi.stubGlobal('window',new EventTarget()); });
afterEach(() => vi.unstubAllGlobals());
const field = (name: string) => [...root.querySelectorAll('input'), ...root.querySelectorAll('select')].find(row => row.getAttribute('aria-label') === name)!;
const button = (name: string) => root.querySelectorAll('button').find(row => row.textContent === name)!;
function open() {
  const frame = scientificFrame(cases.valid);
  const send = vi.fn(async (_: string, change: ViewerChange) => {
    const next = structuredClone(frame); next.frame = 'c'.repeat(64);
    if (change.operation === 'dimensions') Object.assign(next.dimensions,change.value);
    controls.update(next); return next;
  });
  const controls = new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send,refresh:async()=>{}});
  controls.bindSession('one'); controls.update(frame); controls.setAvailable(true);
  return { controls,frame,send };
}

test('slice thickness keeps invalid values editable and sends world margins under exact frame authority',async()=>{
 const {controls,frame,send}=open();frame.dimensions.margins={left:[0,0,0],right:[0,0,0]};controls.update(frame);
 const before=field(`Slice margin before ${frame.dimensions.labels[2]} (2)`);before.value='-1';fire(before,'input');
 fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 expect(root.textContent).toContain('nonnegative slice margin');expect(before.value).toBe('-1');
 before.value='2.5';fire(before,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'slice-margins',value:{left:[0,0,2.5],right:[0,0,0]}});
});

test('image projection uses the current layer and preserves original data semantics',async()=>{
 const {controls,frame,send}=open();frame.layers[0].imageProjection='none';controls.update(frame);
 const projection=field('Image slice projection Intensity');projection.value='mean';fire(projection,'change');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'image-projection',value:{layer:frame.layers[0].id,mode:'mean'}});
 expect(root.textContent).toContain('Original values stay unchanged');
});

test('numeric slices and swapped displayed axes use one frame-bound mutation', async () => {
  const {frame,send} = open();
  const slice = field('Slice axis 2'); slice.value = '5'; fire(slice,'input');
  const horizontal = field('Horizontal axis'); horizontal.value = '2'; fire(horizontal,'change');
  fire(button('Apply view changes'),'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'dimensions',value:{order:[1,0,2],ndisplay:2,steps:[1,2,5]}});
  // The window's own status says what is private; View does not repeat a saved-data sentence beside it.
  expect(root.textContent).toContain('View changes applied.');
  expect(root.textContent).not.toContain('Saved Project data');
});

test('unsent fields retain their elements and values when a newer frame arrives', () => {
  const {controls,frame,send} = open();
  const slice = field('Slice axis 2'); slice.value = '5'; fire(slice,'input');
  controls.update({...frame,frame:'c'.repeat(64)});
  expect(field('Slice axis 2')).toBe(slice); expect(slice.value).toBe('5');
  expect(button('Apply view changes').disabled).toBe(true);
  fire(button('Apply view changes'),'click'); expect(send).not.toHaveBeenCalled();
  expect(root.textContent).toContain('Your unsent values are kept');
  fire(button('Discard unsent view changes'),'click'); expect(slice.value).toBe('3');
});

test('layer values address the worker identity and invalid values never dispatch', async () => {
  const {frame,send} = open();
  const opacity = field('Opacity Intensity'); opacity.value = '2'; fire(opacity,'input');
  fire(button('Apply view changes'),'click'); await settleMicrotasks(); expect(send).not.toHaveBeenCalled();
  expect(root.textContent).toContain('between 0 and 1');
  opacity.value = '.25'; fire(opacity,'input'); fire(button('Apply view changes'),'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'layer-presentation',value:{layer:frame.layers[0].id,visible:true,opacity:.25}});
});

test('pending paint and session replacement cannot submit an old draft', () => {
  const {controls,send} = open();
  const slice = field('Slice axis 2'); slice.value = '5'; fire(slice,'input');
  controls.setAvailable(false); fire(button('Apply view changes'),'click'); expect(send).not.toHaveBeenCalled();
  controls.bindSession('two'); controls.update(scientificFrame(cases.valid)); controls.setAvailable(true);
  expect(button('Apply view changes').disabled).toBe(true); expect(field('Slice axis 2').value).toBe('3');
});

test('late completion from a replaced session does not release the new pending request', async () => {
  const frame = scientificFrame(cases.valid);
  const pending: Array<() => void> = [];
  const send = vi.fn(() => new Promise<typeof frame>(resolve => pending.push(() => resolve(frame))));
  const controls = new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send,refresh:async()=>{}});
  const enter = (id: string) => {
    controls.bindSession(id); controls.update(frame); controls.setAvailable(true);
    const slice = field('Slice axis 2'); slice.value = '5'; fire(slice,'input');
    fire(button('Apply view changes'),'click');
  };
  enter('one'); enter('two');
  expect(send).toHaveBeenCalledTimes(2);
  pending[0](); await settleMicrotasks();
  expect(button('Check current view').disabled).toBe(true);
  expect(root.textContent).not.toContain('View changes applied');
  pending[1](); await settleMicrotasks();
  expect(button('Check current view').disabled).toBe(false);
});

test('failed dispatch requires a refreshed frame and keeps the unsent values', async () => {
  const frame = scientificFrame(cases.valid);
  const send = vi.fn(async () => { throw new Error('Connection lost'); });
  let controls: ScientificNavigation;
  const refresh = vi.fn(async () => controls.update({...frame,frame:'d'.repeat(64)}));
  controls = new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send,refresh});
  controls.bindSession('one'); controls.update(frame); controls.setAvailable(true);
  const slice = field('Slice axis 2'); slice.value = '5'; fire(slice,'input');
  fire(button('Apply view changes'),'click'); await settleMicrotasks();
  expect(root.textContent).toContain('Check current view before retrying');
  expect(button('Apply view changes').disabled).toBe(true);
  fire(button('Check current view'),'click'); await settleMicrotasks();
  expect(refresh).toHaveBeenCalledOnce();
  expect(slice.value).toBe('5'); expect(root.textContent).toContain('Your unsent values are kept');
  expect(button('Apply view changes').disabled).toBe(true);
});

test('late refresh failure cannot replace a new session status or release its pending refresh', async () => {
  const frame = scientificFrame(cases.valid);
  const pending: Array<{resolve(): void; reject(error: Error): void}> = [];
  const controls = new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},
    send: async () => frame,
    refresh: () => new Promise<void>((resolve,reject) => pending.push({resolve,reject})),
  });
  for (const session of ['one','two']) {
    controls.bindSession(session); controls.update(frame); controls.setAvailable(true);
    fire(button('Check current view'),'click');
  }
  pending[0].reject(new Error('Old failure')); await settleMicrotasks();
  expect(root.textContent).not.toContain('Old failure');
  expect(button('Check current view').disabled).toBe(true);
  pending[1].resolve(); await settleMicrotasks();
  expect(button('Check current view').disabled).toBe(false);
});

test('native fractional endpoint does not block changing another slice', async () => {
  const frame = scientificFrame(cases.valid);
  frame.dimensions.ranges[0] = [0,9.6,1]; frame.dimensions.point = [9.6,2.5,3.5];
  frame.dimensions.counts[0] = 10; frame.dimensions.steps = [10,2,4];
  const send = vi.fn(async () => frame);
  const controls = new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send,refresh:async()=>{}});
  controls.bindSession('one'); controls.update(scientificFrame(frame)); controls.setAvailable(true);
  const slice = field('Slice axis 2'); slice.value = '3'; fire(slice,'input');
  fire(button('Apply view changes'),'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'dimensions',value:{order:[2,0,1],ndisplay:2,steps:[10,2,3]}});
});

test('ending the worker removes obsolete controls and disables refresh', () => {
  const {controls} = open();
  controls.bindSession(null);
  expect(button('Check current view').disabled).toBe(true);
  expect(root.querySelectorAll('input').filter(input=>!['Maximum slices per second','First slice','Last slice'].includes(input.getAttribute('aria-label') ?? ''))).toHaveLength(0);
  expect(button('Play slices').disabled).toBe(true);
});

test('an unsent slice change blocks playback; playing blocks other view controls until stopped',()=>{
 const {controls}=open();const slice=field('Slice axis 2');slice.value='5';fire(slice,'input');
 expect(button('Play slices').disabled).toBe(true);
 fire(button('Discard unsent view changes'),'click');field('Playback mode').value='once';fire(button('Play slices'),'click');
 expect(controls.playing()).toBe(true);expect(slice.disabled).toBe(true);expect(controls.settled()).toBe(false);
 expect(button('Check current view').disabled).toBe(true);
 fire(button('Stop playback'),'click');expect(controls.playing()).toBe(false);expect(slice.disabled).toBe(false);controls.close();
});

test('layer stacking uses exact IDs and rejects a draft after the frame changes',async()=>{
 const {controls,frame,send}=open();
 const second={...frame.layers[0],id:'e'.repeat(32),name:'Overlay'};
 const third={...frame.layers[0],id:'f'.repeat(32),name:'Annotations'};
 frame.layers.push(second,third);controls.update(frame);
 const position=field('Stack position Intensity');position.value='0';fire(position,'change');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'layer-order',value:[second.id,third.id,frame.layers[0].id]});
 controls.update(frame);position.value='1';fire(position,'change');
 controls.update({...frame,frame:'d'.repeat(64)});
 expect(button('Apply view changes').disabled).toBe(true);
 fire(button('Apply view changes'),'click');expect(send).toHaveBeenCalledTimes(1);
 fire(button('Discard unsent view changes'),'click');
 expect(field('Stack position Intensity').value).toBe('2');
});

test('image appearance validates before dispatch and keeps drafts across frame updates', async () => {
  const {controls,send,frame} = open();
  const settings = {contrastLimits:[2,200] as [number,number],gamma:1,rendering:'mip' as const,isoThreshold:70,attenuation:.25,autoContrast:false};
  frame.layers[0].imageAppearance = settings;
  controls.update(frame);
  const gamma = field(`Gamma ${frame.layers[0].name}`);
  expect(gamma).toBeDefined(); gamma.value='0'; fire(gamma,'input');
  fire(button('Apply view changes'),'click'); await settleMicrotasks();
  expect(send).not.toHaveBeenCalled(); expect(root.textContent).toContain('positive gamma');
  gamma.value='1.4'; fire(gamma,'input');
  fire(button('Apply view changes'),'click'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'image-presentation',value:{layer:frame.layers[0].id,settings:{...settings,gamma:1.4}}});
  gamma.value='2'; fire(gamma,'input');
  controls.update({...structuredClone(frame),frame:'d'.repeat(64)});
  expect(gamma.value).toBe('2'); expect(button('Apply view changes').disabled).toBe(true);
  expect(root.textContent).toContain('unsent values are kept');
});

test('camera coordinates, directions and reset use the current-frame change path',async()=>{
  const {frame,send}=open();
  const zoom=field('Camera zoom');zoom.value='0';fire(zoom,'input');
  fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
  zoom.value='3';fire(zoom,'input');
  field('Camera center horizontal').value='12';fire(field('Camera center horizontal'),'input');
  field('Horizontal direction').value='left';fire(field('Horizontal direction'),'change');
  fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'camera',value:{...frame.camera,zoom:3,center:[...frame.camera.center.slice(0,2),12],orientation:[...frame.camera.orientation.slice(0,2),'left']}});
  fire(button('Reset camera and fit layers'),'click');await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith('c'.repeat(64),{operation:'camera-reset',value:{}});
});

test('layer blending follows the exact frame and preserves the draft after an update',async()=>{
  const {frame,controls,send}=open();frame.layers[0].blending='translucent';controls.update(frame);
  const select=field('Blending Intensity');select.value='additive';fire(select,'change');
  fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'layer-presentation',value:{layer:frame.layers[0].id,visible:true,opacity:1,blending:'additive'}});
  const next={...frame,frame:'d'.repeat(64)};controls.update(next);
  select.value='minimum';fire(select,'change');controls.update({...next,frame:'e'.repeat(64)});
  expect(select.value).toBe('minimum');expect(button('Apply view changes').disabled).toBe(true);
  fire(button('Discard unsent view changes'),'click');expect(select.value).toBe('translucent');
});

test('image plane controls preserve invalid drafts and bind accepted data coordinates to the frame',async()=>{
 const {controls,frame,send}=open();
 frame.layers[0].imagePlane={depiction:'volume',position:[2,11,13],normal:[1,0,0],thickness:1};controls.update(frame);
 const mode=field('Image depiction Intensity'),normal=field('Plane normal depth Intensity');
 mode.value='plane';fire(mode,'change');normal.value='0';fire(normal,'input');
 fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 expect(mode.value).toBe('plane');expect(root.textContent).toContain('nonzero normal');
 normal.value='2';fire(normal,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'image-plane',value:{layer:frame.layers[0].id,settings:{depiction:'plane',position:[2,11,13],normal:[2,0,0],thickness:1}}});
 normal.value='3';fire(normal,'input');controls.update({...frame,frame:'d'.repeat(64)});
 expect(normal.value).toBe('3');expect(button('Apply view changes').disabled).toBe(true);
 fire(button('Discard unsent view changes'),'click');expect(normal.value).toBe('1');
});

test('clipping add, invalid draft recovery, disable and remove preserve other planes',async()=>{
 const {controls,frame,send}=open();frame.layers[0].clippingPlanes=[];controls.update(frame);
 fire(button('Add clipping plane'),'click');
 const normal=field('Clipping plane 1 normal depth Intensity');normal.value='0';fire(normal,'input');
 fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 expect(root.textContent).toContain('nonzero normal');expect(normal.value).toBe('0');
 normal.value='1';fire(normal,'input');fire(button('Add clipping plane'),'click');
 const second=field('Clipping plane 2 position vertical Intensity');second.value='12';fire(second,'input');
 const enabled=field('Enable clipping plane 1 Intensity');Object.assign(enabled,{checked:false});fire(enabled,'change');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send.mock.calls[0][1]).toEqual({operation:'clipping-planes',value:{layer:frame.layers[0].id,planes:[
  {position:[0,0,0],normal:[1,0,0],enabled:false},{position:[0,12,0],normal:[1,0,0],enabled:true}]}});
 const next=structuredClone(frame);next.frame='e'.repeat(64);next.layers[0].clippingPlanes=[{position:[0,12,0],normal:[1,0,0],enabled:true}];controls.update(next);
 fire(button('Remove plane'),'click');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send.mock.calls[1][1]).toEqual({operation:'clipping-planes',value:{layer:frame.layers[0].id,planes:[]}});
});

test('a new draft cannot retain a previous successful application message',async()=>{
 const {controls,frame}=open();const slice=field('Slice axis 2');
 slice.value='4';fire(slice,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(root.textContent).toContain('View changes applied');
 slice.value='5';fire(slice,'input');
 expect(root.textContent).not.toContain('View changes applied');
 expect(root.textContent).toContain('Unsent view changes');
 controls.update({...frame,frame:'f'.repeat(64)});
 expect(slice.value).toBe('5');expect(button('Apply view changes').disabled).toBe(true);
 expect(root.textContent).not.toContain('View changes applied');
});

test('sampling controls preserve a malformed kernel draft and correct it without resampling data',async()=>{
 const {controls,frame,send}=open();frame.layers[0].imageSampling={interpolation2d:'nearest',interpolation3d:'linear',kernel2d:[[1]]};controls.update(frame);
 const mode=field('2D interpolation Intensity');mode.value='custom';fire(mode,'change');
 const kernel=root.querySelectorAll('textarea').find(row=>row.getAttribute('aria-label')==='Custom 2D kernel Intensity')!;
 kernel.value='[[1],[2,3]]';fire(kernel,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(kernel.value).toBe('[[1],[2,3]]');
 kernel.value='[[0,1,0],[1,0,1],[0,1,0]]';fire(kernel,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send.mock.calls[0][1]).toEqual({operation:'image-sampling',value:{layer:frame.layers[0].id,settings:{interpolation2d:'custom',interpolation3d:'linear',kernel2d:[[0,1,0],[1,0,1],[0,1,0]]}}});
});


test('pending view changes say applying until the accepted response arrives',async()=>{
 const frame=scientificFrame(cases.valid);let finish!:(value:typeof frame)=>void;
 const pending=new Promise<typeof frame>(resolve=>{finish=resolve;});
 const controls=new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send:()=>pending,refresh:async()=>{}});
 controls.bindSession('pending');controls.update(frame);controls.setAvailable(true);
 const slice=field('Slice axis 2');slice.value='4';fire(slice,'input');
 fire(button('Apply view changes'),'click');
 expect(root.textContent).toContain('Applying view changes…');expect(root.textContent).not.toContain('View changes applied');
 expect(button('Apply view changes').disabled).toBe(true);expect(slice.disabled).toBe(true);
 const accepted={...frame,frame:'a'.repeat(64)};controls.update(accepted);finish(accepted);await settleMicrotasks();
 expect(root.textContent).toContain('View changes applied');expect(root.textContent).not.toContain('Applying view changes…');
});


test('leaving custom mode does not require repairing an unused kernel draft',async()=>{
 const {controls,frame,send}=open();frame.layers[0].imageSampling={interpolation2d:'nearest',interpolation3d:'linear',kernel2d:[[1]]};controls.update(frame);
 const mode=field('2D interpolation Intensity');mode.value='custom';fire(mode,'change');
 const kernel=root.querySelectorAll('textarea').find(row=>row.getAttribute('aria-label')==='Custom 2D kernel Intensity')!;
 kernel.value='unfinished';fire(kernel,'input');mode.value='linear';fire(mode,'change');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send.mock.calls[0][1]).toEqual({operation:'image-sampling',value:{layer:frame.layers[0].id,settings:{interpolation2d:'linear',interpolation3d:'linear',kernel2d:[[1]]}}});
});

test('named palettes are simple while custom color errors preserve the draft',async()=>{
 const {controls,frame,send}=open();
 frame.layers[0].imageColormap={name:'gray',colors:[[0,0,0,1],[1,1,1,1]],controls:[0,1],interpolation:'linear',nanColor:[0,0,0,0],highColor:null,lowColor:null};frame.layers[0].colormapChoices=['gray','viridis'];controls.update(frame);
 const select=field('Color map Intensity'),editor=root.querySelectorAll('textarea').find(row=>row.getAttribute('aria-label')==='Custom color map Intensity')!;
 expect(select.value).toBe('keep');expect(editor.disabled).toBe(true);
 select.value='custom';fire(select,'change');editor.value='unfinished';fire(editor,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(editor.value).toBe('unfinished');
 select.value='name:viridis';fire(select,'change');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send.mock.calls[0][1]).toEqual({operation:'image-colormap',value:{layer:frame.layers[0].id,settings:{name:'viridis'}}});
});

test('native vector controls bind one current frame and preserve unsent values on polling',async()=>{
 const {controls,frame,send}=open();frame.layers[0].type='vectors';
 frame.layers[0].pathAppearance={kind:'vectors',length:2.5,width:3,style:'arrow',projection:'none'};controls.update(frame);
 const length=field('Length multiplier Intensity');length.value='4.25';fire(length,'input');
 const style=field('Vector style Intensity');style.value='triangle';fire(style,'change');
 controls.update(frame);expect(field('Length multiplier Intensity')).toBe(length);expect(length.value).toBe('4.25');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'path-presentation',value:{layer:frame.layers[0].id,
  settings:{kind:'vectors',length:4.25,width:3,style:'triangle',projection:'none'}}});
});
test('track width refuses native clamping and corrected controls can retry',async()=>{
 const {controls,frame,send}=open();frame.layers[0].type='tracks';
 frame.layers[0].pathAppearance={kind:'tracks',width:3,tail:12,head:4,showIds:true,showTail:true,showGraph:true,hideCompleted:false};controls.update(frame);
 const width=field('Line width Intensity');width.value='30';fire(width,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 width.value='5';fire(width,'input');const graph=field('Show division edges Intensity');Object.assign(graph,{checked:false});fire(graph,'change');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'path-presentation',value:{layer:frame.layers[0].id,
  settings:{kind:'tracks',width:5,tail:12,head:4,showIds:true,showTail:true,showGraph:false,hideCompleted:false}}});
});

test('surface controls retain drafts, validate and dispatch one frame-bound change',async()=>{
 const {controls,frame,send}=open();frame.layers[0].type='surface';
 frame.layers[0].surfaceAppearance={shading:'smooth',gamma:1,contrastLimits:[1,4],autoContrast:false,
  wireframe:{visible:true,width:1.5,color:[1,0,0,1]},faceNormals:{visible:true,width:2,length:3,color:[0,1,0,1]},vertexNormals:{visible:false,width:1,length:5,color:[0,0,1,1]}};
 controls.update(frame);const gamma=field('Surface gamma Intensity');gamma.value='0';fire(gamma,'input');
 fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 gamma.value='1.5';fire(gamma,'input');const shading=field('Shading Intensity');shading.value='flat';fire(shading,'change');
 controls.update(frame);expect(field('Surface gamma Intensity')).toBe(gamma);expect(gamma.value).toBe('1.5');
 const normals=field('Show vertex normals Intensity');Object.assign(normals,{checked:true});fire(normals,'change');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'surface-presentation',value:{layer:frame.layers[0].id,
 settings:{...frame.layers[0].surfaceAppearance,gamma:1.5,shading:'flat',vertexNormals:{...frame.layers[0].surfaceAppearance.vertexNormals,visible:true}}}});
});

test('point rendering validates limits, preserves drafts and submits the exact frame and layer',async()=>{
 const {controls,frame,send}=open();frame.layers[0].type='points';
 frame.layers[0].pointAppearance={shading:'none',projection:'all',antialiasing:1,canvasLimits:[0,10000]};controls.update(frame);
 const minimum=field('Minimum marker size in pixels Intensity');minimum.value='20';fire(minimum,'input');
 const maximum=field('Maximum marker size in pixels Intensity');maximum.value='10';fire(maximum,'input');
 fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 maximum.value='80';fire(maximum,'input');const projection=field('Point slice projection Intensity');projection.value='rescale_spherical';fire(projection,'change');
 controls.update(frame);expect(field('Minimum marker size in pixels Intensity')).toBe(minimum);expect(minimum.value).toBe('20');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'point-presentation',value:{layer:frame.layers[0].id,
 settings:{shading:'none',projection:'rescale_spherical',antialiasing:1,canvasLimits:[20,80]}}});
});


test('numeric entry and slice slider share one draft, including keyboard-produced input',async()=>{
 const {frame,send,controls}=open();const slider=field('Slice slider axis 2'),number=field('Slice axis 2');
 expect(slider.value).toBe(number.value);slider.value='5';fire(slider,'input');expect(number.value).toBe('5');
 number.value='4';fire(number,'input');expect(slider.value).toBe('4');
 expect(button('Roll axes').disabled).toBe(true);expect(button('Swap displayed axes').disabled).toBe(true);
 fire(button('Roll axes'),'click');expect(send).not.toHaveBeenCalled();
 controls.update({...frame,frame:'d'.repeat(64)});expect(number.value).toBe('4');expect(slider.value).toBe('4');
 fire(button('Apply view changes'),'click');expect(send).not.toHaveBeenCalled();
 fire(button('Discard unsent view changes'),'click');expect(number.value).toBe('3');expect(slider.value).toBe('3');
 slider.value='5';fire(slider,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith('d'.repeat(64),{operation:'dimensions',value:{order:frame.dimensions.order,ndisplay:2,steps:[1,2,5]}});
});

test.each([['Roll axes','roll'],['Swap displayed axes','transpose']])('axis shortcut %s uses current native authority without discarding another draft',async(label,value)=>{
 const {frame,send,controls}=open();fire(button(label),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'axis-order',value});
 controls.setAvailable(false);fire(button(label),'click');expect(send).toHaveBeenCalledTimes(1);
});

test('grid controls retain invalid drafts and submit through the current frame',async()=>{
 const {controls,frame,send}=open();frame.grid={enabled:false,shape:[-1,-1],stride:1,spacing:0};controls.update(frame);
 const rows=field('Grid rows');rows.value='0';fire(rows,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(rows.value).toBe('0');expect(root.textContent).toContain('positive rows');
 rows.value='2';fire(rows,'input');const enabled=field('Show layer grid');Object.assign(enabled,{checked:true});fire(enabled,'change');
 field('Grid stride').value='-2';fire(field('Grid stride'),'input');
 fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'grid',value:{enabled:true,shape:[2,-1],stride:-2,spacing:0}});
});

test('native layer selection shows actual active state and sends an explicit frame-bound action',async()=>{
 const {controls,frame,send}=open();const layer=frame.layers[0];
 frame.layerSelection={selected:[],active:null};controls.update(frame);
 expect(root.textContent).toContain('No layers selected.');
 fire(button(`Select layer ${layer.name}`),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'layer-selection',value:{layer:layer.id,action:'replace'}});
 controls.update({...frame,frame:'d'.repeat(64),layerSelection:{selected:[layer.id],active:layer.id}});
 expect(root.textContent).toContain(`Active layer: ${layer.name}`);
 fire(button(`Remove layer ${layer.name} from selection`),'click');await settleMicrotasks();
 expect(send).toHaveBeenLastCalledWith('d'.repeat(64),{operation:'layer-selection',value:{layer:layer.id,action:'toggle'}});
});

test('a detached layer selection control cannot act after its layer or session changes',async()=>{
 const {controls,frame,send}=open();const layer=frame.layers[0];
 frame.layerSelection={selected:[layer.id],active:layer.id};controls.update(frame);
 const old=button(`Select layer ${layer.name}`),oldClear=button('Clear layer selection');
 controls.update({...frame,frame:'d'.repeat(64),layers:[],layerSelection:{selected:[],active:null}});
 fire(old,'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 const withoutSelection={...frame,frame:'e'.repeat(64)};delete withoutSelection.layerSelection;controls.update(withoutSelection);
 fire(oldClear,'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 controls.bindSession('new-session');controls.update(frame);controls.setAvailable(true);
 fire(oldClear,'click');fire(old,'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
 fire(button('Clear layer selection'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'layer-selection',value:{layer:null,action:'clear'}});
});

test('layer selection is unavailable while another view draft is pending',()=>{
 const {controls,frame,send}=open();frame.layerSelection={selected:[],active:null};controls.update(frame);
 const slice=field('Slice axis 2');slice.value='5';fire(slice,'input');
 expect(button(`Select layer ${frame.layers[0].name}`).disabled).toBe(true);
 fire(button(`Select layer ${frame.layers[0].name}`),'click');expect(send).not.toHaveBeenCalled();
});

test('layer selection wire refuses mismatched membership instead of inventing an active layer',()=>{
 const identity=cases.valid.layers[0].id;
 for(const layerSelection of [{selected:[identity],active:null},{selected:[],active:identity},
  {selected:['f'.repeat(32)],active:'f'.repeat(32)},{selected:[identity,identity],active:null},
  {selected:[true],active:true},{selected:[],active:null,extra:1}])
  expect(()=>scientificFrame({...cases.valid,layerSelection})).toThrow();
 for(const layerSelection of [{selected:[],active:null},{selected:[identity],active:identity}])
  expect(scientificFrame({...cases.valid,layerSelection}).layerSelection).toEqual(layerSelection);
});

const textDisplay={visible:true,size:18.5,scaling:false,blending:'translucent',anchor:'upper_left',translation:0,rotation:25.5};
test.each(['points','shapes'] as const)('native %s feature text preserves invalid drafts and sends one exact-frame change',async family=>{
 const {controls,frame,send}=open();frame.layers[0].type=family;frame.layers[0].textAppearance=structuredClone(textDisplay);controls.update(frame);
 const size=field('Text size Intensity');size.value='0';fire(size,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(size.value).toBe('0');expect(root.textContent).toContain('positive size');
 size.value='22.25';fire(size,'input');
 const offset=field('Text offset in data coordinates Intensity');offset.value='1, 2';fire(offset,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(offset.value).toBe('1, 2');
 offset.value='1, 2, 3';fire(offset,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'text-presentation',value:{layer:frame.layers[0].id,settings:{...textDisplay,size:22.25,translation:[1,2,3]}}});
 expect(root.textContent).toContain('Feature values, text templates and color mappings stay unchanged');
});
test('new native frame preserves a text draft until explicit discard',()=>{
 const {controls,frame,send}=open();frame.layers[0].type='points';frame.layers[0].textAppearance=structuredClone(textDisplay);controls.update(frame);
 const rotation=field('Text rotation in degrees Intensity');rotation.value='90';fire(rotation,'input');
 const newer=structuredClone(frame);newer.frame='d'.repeat(64);newer.layers[0].textAppearance!.rotation=45;controls.update(newer);
 expect(field('Text rotation in degrees Intensity')).toBe(rotation);expect(rotation.value).toBe('90');
 fire(button('Apply view changes'),'click');expect(send).not.toHaveBeenCalled();
 fire(button('Discard unsent view changes'),'click');expect(rotation.value).toBe('45');
});

const key=(fields:Record<string,unknown>)=>({key:'',altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,...fields});
test('napari\'s default dimension shortcuts, with Command on macOS and Control elsewhere',()=>{
 expect(dimensionShortcut(key({key:'ArrowLeft'}),true)).toBe('increment_dims_left');
 expect(dimensionShortcut(key({key:'ArrowRight'}),false)).toBe('increment_dims_right');
 expect(dimensionShortcut(key({key:'ArrowUp',altKey:true}),true)).toBe('focus_axes_up');
 expect(dimensionShortcut(key({key:'ArrowDown',altKey:true}),false)).toBe('focus_axes_down');
 expect(dimensionShortcut(key({key:'e',metaKey:true}),true)).toBe('roll_axes');
 expect(dimensionShortcut(key({key:'E',ctrlKey:true}),false)).toBe('roll_axes');
 expect(dimensionShortcut(key({key:'t',ctrlKey:true}),true)).toBe(null); // Control+T is not Command+T on macOS
 expect(dimensionShortcut(key({key:'y',metaKey:true}),true)).toBe('toggle_ndisplay');
 for(const other of [key({key:'ArrowUp'}),key({key:'ArrowLeft',shiftKey:true}),key({key:'ArrowLeft',metaKey:true}),key({key:'x',metaKey:true})])
  expect(dimensionShortcut(other,true)).toBe(null);
});
test('a shortcut in the panel runs napari\'s action at once; typing in a field never does',async()=>{
 const {frame,send}=open();
 fire(root,'keydown',{key:'ArrowRight',altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,target:root});await settleMicrotasks();
 expect(send).toHaveBeenLastCalledWith(frame.frame,{operation:'viewer-action',value:'increment_dims_right'});
 send.mockClear();
 const slice=field('Slice axis 2');
 fire(root,'keydown',{key:'ArrowLeft',altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,target:slice});await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();
});
test('an unsent change refuses a shortcut and says why',async()=>{
 const {send}=open();
 const slice=field('Slice axis 2');slice.value='5';fire(slice,'input');
 fire(root,'keydown',{key:'ArrowRight',altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,target:root});await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(root.textContent).toContain('before using a dimension action');
});
test('action buttons, Go to and Use for keyboard send exact native requests',async()=>{
 const {controls,frame,send}=open();
 for(const [label,action] of [['Next slice','increment_dims_right'],['Previous slice','increment_dims_left'],['Keyboard axis up','focus_axes_up'],['Keyboard axis down','focus_axes_down'],['Toggle 2D/3D','toggle_ndisplay']]){
  fire(button(label),'click');await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith(frame.frame,{operation:'viewer-action',value:action});controls.update(frame);
 }
 const world=field('World coordinate axis 2');world.value='2.75';
 fire(root.querySelectorAll('button').find(row=>row.getAttribute('aria-label')==='Go to world coordinate axis 2')!,'click');await settleMicrotasks();
 expect(send).toHaveBeenLastCalledWith(frame.frame,{operation:'dims-point',value:{axis:2,value:2.75}});controls.update(frame);
 send.mockClear();world.value='';
 fire(root.querySelectorAll('button').find(row=>row.getAttribute('aria-label')==='Go to world coordinate axis 2')!,'click');await settleMicrotasks();
 expect(send).not.toHaveBeenCalled();expect(root.textContent).toContain('Enter a finite world coordinate');
 fire(root.querySelectorAll('button').find(row=>row.getAttribute('aria-label')==='Use axis 2 for keyboard')!,'click');await settleMicrotasks();
 expect(send).toHaveBeenLastCalledWith(frame.frame,{operation:'dims-focus',value:2});
});
test('the keyboard axis, units, range and rollable state are shown as the worker reports them',()=>{
 const {controls,frame}=open();
 frame.dimensions.lastUsed=2;frame.dimensions.units=['second','micrometer','micrometer'];frame.dimensions.rollable=[true,true,false];
 controls.update(frame);
 expect(root.textContent).toContain('Keyboard axis: x (2) at slice 3 of 0–8, coordinate 3 micrometer.');
 expect(root.textContent).toContain('x (2) · index 0–8 · coordinate 3 micrometer · range 0–8 micrometer, step 1');
 expect(root.textContent).toContain('locked for Roll');
 expect(root.querySelectorAll('button').find(row=>row.getAttribute('aria-label')==='Use axis 2 for keyboard')!.getAttribute('aria-pressed')).toBe('true');
});
test('in 3D the arrow keys anywhere in the preview stay with the camera',async()=>{
 const {controls,frame,send}=open();
 frame.dimensions.ndisplay=3;frame.dimensions.displayed=[...frame.dimensions.order];controls.update(frame);
 const preview={tagName:'DIV',closest:(selector:string)=>selector==='.scientific-viewport'?{}:null};
 for(const target of [preview,{tagName:'CANVAS'}]){
  fire(root,'keydown',{key:'ArrowRight',altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,target});await settleMicrotasks();
 }
 expect(send).not.toHaveBeenCalled();
 fire(root,'keydown',{key:'ArrowRight',altKey:false,ctrlKey:false,metaKey:false,shiftKey:false,target:root});await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'viewer-action',value:'increment_dims_right'});
});


test('absolute slices can replace an in-flight target while other controls stay blocked', async () => {
  const frame = scientificFrame(cases.valid);
  let finish!: (delivered: typeof frame) => void;
  const send = vi.fn(() => new Promise<typeof frame>(resolve => { finish = resolve; }));
  const replaceDimensions = vi.fn(async () => {});
  const controls = new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send,replaceDimensions,refresh:async()=>{}});
  controls.bindSession('one');controls.update(frame);controls.setAvailable(true);
  const slice=field('Slice axis 2');slice.value='4';fire(slice,'input');fire(button('Apply view changes'),'click');
  expect(send).toHaveBeenCalledOnce();expect(slice.disabled).toBe(false);
  expect(field('Displayed dimensions').disabled).toBe(true);expect(field('Opacity Intensity').disabled).toBe(true);
  slice.value='5';fire(slice,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(replaceDimensions).toHaveBeenCalledWith({order:frame.dimensions.order,ndisplay:frame.dimensions.ndisplay,steps:[1,2,5]});
  const next=structuredClone(frame);next.frame='d'.repeat(64);next.dimensions.steps=[1,2,5];controls.update(next);finish(next);await settleMicrotasks();
  expect(send).toHaveBeenCalledOnce();expect(controls.settled()).toBe(true);expect(slice.value).toBe('5');
});
