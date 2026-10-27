import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import { PanelStack } from './panels';
import { ScientificLabels, labelReceipt, type LabelCommand } from './scientific-labels';
import type { LabelsToolChange } from './scientific-labels-tool';
import { scientificFrame } from './scientific-view';
import type { ScientificCanvas } from './scientific-canvas';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
let root: TestElement, canvas: TestElement & { hasPointerCapture(id:number):boolean }, control: ScientificLabels;
let doc: ReturnType<typeof syntheticDOM>['document'], win: EventTarget;
beforeEach(() => {
  const dom=syntheticDOM(); root=dom.host; doc=dom.document; win=new EventTarget();
  vi.stubGlobal('document',doc); vi.stubGlobal('window',win);
  class Canvas extends TestElement { hasPointerCapture(id:number):boolean { return this.captured===id; } focus() {} }
  canvas=new Canvas('canvas');
});
afterEach(() => {control?.close();vi.unstubAllGlobals();});
const field=(name:string)=>[...root.querySelectorAll('select'),...root.querySelectorAll('input')].find(row=>row.getAttribute('aria-label')===name)!;
const button=(name:string)=>root.querySelectorAll('button').find(row=>row.textContent===name)!;
const pointer=(name:string,x=12,y=10,extra:Record<string,unknown>={})=>fire(canvas,name,{pointerId:1,button:0,isPrimary:true,clientX:x,clientY:y,...extra});
const outcome=(request:string)=>({request,state:'applied',changed:true,label:'18446744073709551615',message:''});
function open(send=vi.fn(async(command:LabelCommand)=>outcome(command.request))) {
  const frame=scientificFrame(cases.valid); frame.layers=[{id:'b'.repeat(32),name:'Instances',type:'labels',visible:true,opacity:.7,scale:[1,1,1],translate:[0,0,0],editableInput:'labels'}];
  let current: {frame:typeof frame;draw:string}|null={frame,draw:'paint-one'};
  const drawing={element:()=>canvas,current:()=>current,position:(draw:string,x:number,y:number)=>{if (!current || draw!==current.draw) throw new Error('old frame'); return [y,x];}};
  const check=vi.fn<(request:string)=>Promise<unknown>>(async(request:string)=>outcome(request));
  const preview=vi.fn();
  const tool=vi.fn(async(_change:LabelsToolChange,_frame:string)=>{});
  control=new ScientificLabels(root as unknown as HTMLElement,drawing as unknown as ScientificCanvas,{send,check,tool,preview,gestureControls:root as unknown as HTMLElement,changed:()=>{}});
  control.bindSession('one');control.update(frame);control.setAvailable(true);field('Label tool').value='paint';
  return {send,check,tool,preview,frame,revoke:()=>{current=null;control.revoke();}};
}
test('submits one complete gesture only on release with exact label string and frame',async()=>{
  const {send}=open(); field('Label value').value='18446744073709551615';
  pointer('pointerdown');pointer('pointermove',16,14);
  expect(control.blocked()).toBe(true);expect(send).not.toHaveBeenCalled();
  pointer('pointerup',18,16);await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0]).toMatchObject({frame:cases.valid.frame,layer:'b'.repeat(32),label:'18446744073709551615',points:[[10,12],[14,16],[16,18]]});
  expect(control.blocked()).toBe(false);
});
test.each(['pointercancel','lostpointercapture','blur','escape','resize','revoke'])('cancels an unsent gesture on %s',async(reason)=>{
  const {send,revoke}=open();pointer('pointerdown');
  if(reason==='blur')win.dispatchEvent(new Event('blur'));
  else if(reason==='escape')win.dispatchEvent(Object.assign(new Event('keydown'),{key:'Escape'}));
  else if(reason==='resize'){canvas.style.width='500px';pointer('pointermove',16,14);}
  else if(reason==='revoke')revoke();
  else pointer(reason);
  pointer('pointerup');await settleMicrotasks();expect(send).not.toHaveBeenCalled();expect(control.blocked()).toBe(false);
});
test('uncertain response locks edits and checks its identity without replaying',async()=>{
  const send=vi.fn(async(_command:LabelCommand)=>{throw new Error('connection lost');});const {check}=open(send);
  pointer('pointerdown');pointer('pointerup');await settleMicrotasks();
  expect(control.blocked()).toBe(true);expect(root.textContent).toContain('will not be sent again');
  pointer('pointerdown');pointer('pointerup');expect(send).toHaveBeenCalledTimes(1);
  fire(button('Check edit outcome'),'click');await settleMicrotasks();
  expect(check).toHaveBeenCalledWith(send.mock.calls[0][0].request);expect(send).toHaveBeenCalledTimes(1);expect(control.blocked()).toBe(false);
});
test('late reply from old session cannot alter current fields or pending state',async()=>{
  let finish!:(value:ReturnType<typeof outcome>)=>void;
  const send=vi.fn((_command:LabelCommand)=>new Promise<ReturnType<typeof outcome>>(resolve=>{finish=resolve;}));
  const {frame}=open(send);pointer('pointerdown');pointer('pointerup');
  control.bindSession('two');control.update(frame);field('Label value').value='19';
  finish(outcome(send.mock.calls[0][0].request));await settleMicrotasks();
  expect(field('Label value').value).toBe('19');expect(control.blocked()).toBe(false);
});
test('3D, missing bindings and hidden layer cannot dispatch',()=>{
  const {frame,send}=open();
  for(const change of ['3d','unbound','hidden']){
    const copy=structuredClone(frame);
    if(change==='3d')copy.dimensions.ndisplay=3;
    if(change==='unbound')delete copy.layers[0].editableInput;
    if(change==='hidden')copy.layers[0].visible=false;
    control.update(copy);pointer('pointerdown');pointer('pointerup');
  }
  expect(send).not.toHaveBeenCalled();
});
test('invalid outcome never certifies completion',()=>{
  const value=outcome('same');expect(labelReceipt(value,'same')).toEqual(value);
  for(const invalid of [null,{...value,request:'other'},{...value,state:'refused'},{...value,label:2**64-1},{...value,extra:true}])
    expect(()=>labelReceipt(invalid,'same')).toThrow();
});

test('confirmed non-admission unlocks existing private work without replay',async()=>{
  const send=vi.fn(async(_command:LabelCommand)=>{throw new Error('manual edit allowance');});const {check}=open(send);
  check.mockImplementation(async(request:string)=>({request,state:'not-admitted',changed:false,label:null,message:'This gesture changed nothing.'}));
  pointer('pointerdown');pointer('pointerup');await settleMicrotasks();expect(control.blocked()).toBe(true);
  fire(button('Check edit outcome'),'click');await settleMicrotasks();
  expect(control.blocked()).toBe(false);expect(send).toHaveBeenCalledTimes(1);expect(root.textContent).toContain('changed nothing');
});

test('volume editing is explicit and a new smaller layer resets to the displayed plane', async () => {
  const {send,frame}=open();
  expect(field('Edit dimensions').value).toBe('2');
  field('Edit dimensions').value='3'; fire(field('Edit dimensions'),'change');await settleMicrotasks();
  pointer('pointerdown');pointer('pointerup');await settleMicrotasks();
  expect(send.mock.calls[0][0]).toMatchObject({dimensions:3});
  const plane=structuredClone(frame);plane.layers[0].scale=[1,1];plane.layers[0].translate=[0,0];
  control.update(plane);
  expect(field('Edit dimensions').value).toBe('2');
  pointer('pointerdown');pointer('pointerup');await settleMicrotasks();
  expect(send.mock.calls[1][0]).not.toHaveProperty('dimensions');
});

test('changing editing dimensions cancels an unsent gesture and rejects invalid dimensions', async () => {
  const {send}=open();pointer('pointerdown');
  field('Edit dimensions').value='3';fire(field('Edit dimensions'),'change');
  pointer('pointerup');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
  field('Edit dimensions').value='4';pointer('pointerdown');pointer('pointerup');
  expect(send).not.toHaveBeenCalled();expect(root.textContent).toContain('supported by this Labels layer');
});


test('polygon collects a visible draft and submits once on Finish, without hidden-slice or brush settings', async () => {
  const {send,preview}=open();field('Label tool').value='polygon';fire(field('Label tool'),'change');await settleMicrotasks();
  field('Edit dimensions').value='3';
  for(const [x,y] of [[2,2],[6,2],[2,6]]) { pointer('pointerdown',x,y);pointer('pointerup',x,y); }
  expect(send).not.toHaveBeenCalled();expect(control.blocked()).toBe(true);
  expect(preview.mock.calls.at(-1)?.[0]).toEqual([[2,2],[2,6],[6,2]]);
  expect(field('Edit dimensions').disabled).toBe(true);
  expect(field('Edit dimensions').parentElement?.hidden).toBe(true);
  fire(button('Finish polygon'),'click');await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(1);
  const command=send.mock.calls[0][0];
  expect(command).toMatchObject({action:'polygon',label:'1',points:[[2,2],[2,6],[6,2]]});
  for(const key of ['brush','dimensions','contiguous'])expect(command).not.toHaveProperty(key);
  expect(preview.mock.calls.at(-1)?.[0]).toBeNull();expect(control.blocked()).toBe(false);
});

test.each(['escape','blur','resize','revoke','session','frame','pointercancel'])('polygon draft cancels on %s without touching scientific values', async(reason) => {
  const {send,preview,revoke,frame}=open();field('Label tool').value='polygon';
  for(const [x,y] of [[2,2],[6,2],[2,6]]){pointer('pointerdown',x,y);pointer('pointerup',x,y);}
  if(reason==='escape')win.dispatchEvent(Object.assign(new Event('keydown'),{key:'Escape'}));
  if(reason==='blur')win.dispatchEvent(new Event('blur'));
  if(reason==='resize')canvas.style.width='500px';
  if(reason==='revoke')revoke();
  if(reason==='session')control.bindSession('two');
  if(reason==='frame')control.update({...frame,frame:'c'.repeat(64)});
  if(reason==='pointercancel')pointer('pointercancel');
  fire(button('Finish polygon'),'click');await settleMicrotasks();
  expect(send).not.toHaveBeenCalled();expect(control.blocked()).toBe(false);
  expect(preview.mock.calls.at(-1)?.[0]).toBeNull();
});

test('unfinished polygon and overflow never submit, and ordinary strokes still work after cancellation', async() => {
  const {send}=open();field('Label tool').value='polygon';
  pointer('pointerdown',2,2);pointer('pointerup',2,2);
  fire(button('Finish polygon'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
  for(let n=0;n<256;n++){pointer('pointerdown',3+n%100,3+Math.floor(n/100));pointer('pointerup',3+n%100,3+Math.floor(n/100));}
  expect(control.blocked()).toBe(false);expect(send).not.toHaveBeenCalled();
  expect(root.textContent).toContain('too many corners');
  field('Label tool').value='paint';fire(field('Label tool'),'change');await settleMicrotasks();
  pointer('pointerdown');pointer('pointerup');await settleMicrotasks();expect(send).toHaveBeenCalledTimes(1);
});


test('Enter finishes only the polygon belonging to the focused preview', async() => {
  const {send}=open();field('Label tool').value='polygon';
  for(const [x,y] of [[2,2],[6,2],[2,6]])pointer('pointerdown',x,y);
  win.dispatchEvent(Object.assign(new Event('keydown'),{key:'Enter'}));expect(send).not.toHaveBeenCalled();
  const key=Object.assign(new Event('keydown'),{key:'Enter'});Object.defineProperty(key,'target',{value:canvas});
  win.dispatchEvent(key);await settleMicrotasks();expect(send).toHaveBeenCalledTimes(1);
});

test('grid panels never submit single-view painting coordinates',()=>{
 const {frame,send}=open();control.update({...frame,grid:{enabled:true,shape:[1,2],stride:1,spacing:0}});
 pointer('pointerdown');pointer('pointerup');expect(send).not.toHaveBeenCalled();expect(field('Label tool').disabled).toBe(true);
});

test('a changed setting is sent at once as napari\'s own layer setting',async()=>{
  const {tool,frame}=open(); field('Brush size').value='9'; fire(field('Brush size'),'change');await settleMicrotasks();
  expect(tool).toHaveBeenCalledWith({operation:'labels-tool',value:{layer:'b'.repeat(32),settings:{brushSize:9}}},frame.frame);
  (field('Preserve other labels') as unknown as HTMLInputElement).checked=true; fire(field('Preserve other labels'),'change');await settleMicrotasks();
  expect(tool).toHaveBeenLastCalledWith({operation:'labels-tool',value:{layer:'b'.repeat(32),settings:{preserve:true}}},frame.frame);
});
test('napari\'s default Labels keys on the focused preview run napari\'s actions; Cmd+Z undoes',async()=>{
  const {tool,send}=open();
  const press=(key:string,target:unknown,extra:Record<string,unknown>={})=>{
    const event=Object.assign(new Event('keydown'),{key,...extra});Object.defineProperty(event,'target',{value:target});win.dispatchEvent(event);
  };
  for (const [key,action] of [['e','activate_labels_erase_mode'],['2','activate_labels_paint_mode'],['m','new_label'],[']','increase_brush_size'],['b','toggle_preserve_labels']]) {
    press(key,canvas);await settleMicrotasks();
    expect(tool).toHaveBeenLastCalledWith({operation:'labels-tool',value:{layer:'b'.repeat(32),action}},expect.any(String));
  }
  press('z',canvas,{metaKey:true});await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith(expect.objectContaining({action:'undo'}),expect.any(String));
  press('z',canvas,{ctrlKey:true,shiftKey:true});await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith(expect.objectContaining({action:'redo'}),expect.any(String));
  const calls=tool.mock.calls.length; press('e',root);await settleMicrotasks();
  expect(tool.mock.calls.length).toBe(calls);  // only the focused preview takes napari's keys
});
test('the fields show napari\'s Labels state, including an ID above 2**53',()=>{
  const {frame}=open();
  frame.layers[0].labelsTool={mode:'fill',selectedLabel:'1152921504606846979',background:'0',brushSize:7,preserve:true,contiguous:false,editDimensions:3,showSelected:false};
  control.update(frame);
  expect(field('Label tool').value).toBe('fill');expect(field('Label value').value).toBe('1152921504606846979');
  expect(field('Brush size').value).toBe('7');expect((field('Preserve other labels') as unknown as HTMLInputElement).checked).toBe(true);
  expect((field('Fill connected region only') as unknown as HTMLInputElement).checked).toBe(false);
});
test('the fields stay usable while a setting is applied; settings changed meanwhile are sent next, together',async()=>{
  const {tool,frame}=open(); let release:()=>void=()=>{};
  tool.mockImplementationOnce(()=>new Promise<void>(resolve=>{release=resolve;}));
  field('Brush size').value='9'; fire(field('Brush size'),'change'); await settleMicrotasks();
  expect(field('Edit dimensions').disabled).toBe(false); expect(field('Label value').disabled).toBe(false);
  expect(root.getAttribute('aria-busy')).toBe('true');
  field('Label value').value='42'; fire(field('Label value'),'change');
  (field('Preserve other labels') as unknown as HTMLInputElement).checked=true; fire(field('Preserve other labels'),'change'); await settleMicrotasks();
  expect(tool).toHaveBeenCalledTimes(1);
  release(); await settleMicrotasks(); await settleMicrotasks();
  expect(tool).toHaveBeenLastCalledWith({operation:'labels-tool',value:{layer:'b'.repeat(32),settings:{selectedLabel:'42',preserve:true}}},frame.frame);
  expect(tool).toHaveBeenCalledTimes(2); await settleMicrotasks(); expect(root.getAttribute('aria-busy')).toBe('false');
});

test('reopening the same layer identities repopulates the layer selector', async () => {
  const { frame, send } = open();
  control.bindSession('replacement'); control.update(frame); field('Label tool').value = 'paint';
  expect(field('Labels layer').children).toHaveLength(1);
  pointer('pointerdown'); pointer('pointerup'); await settleMicrotasks();
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0].layer).toBe('b'.repeat(32));
});

test('queued Labels settings cannot move to a replacement layer', async () => {
  const { frame, tool } = open();
  let release!: () => void;
  tool.mockImplementationOnce(() => new Promise<void>(resolve => { release = resolve; }));
  field('Brush size').value = '5'; fire(field('Brush size'), 'change');
  field('Label value').value = '7'; fire(field('Label value'), 'change');
  const replacement = structuredClone(frame); replacement.layers[0].id = 'c'.repeat(32); control.update(replacement);
  release(); await settleMicrotasks();
  expect(tool).toHaveBeenCalledTimes(1);
  expect(control.blocked()).toBe(false);
});

test.each(['paint','polygon'])('settings focus never sees a transient disabled write while selecting %s', async mode => {
  const {tool}=open();const writes=new Map<string,boolean[]>();
  let release:()=>void=()=>{};
  tool.mockImplementationOnce(()=>new Promise<void>(resolve=>{release=resolve;}));
  for(const name of ['Label tool','Label value','Preserve other labels']) {
    const input=field(name);let value=input.disabled;const history:boolean[]=[];writes.set(name,history);
    // Real Chromium blurs at the true assignment even if the next line re-enables it.
    Object.defineProperty(input,'disabled',{configurable:true,get:()=>value,set:(next:boolean)=>{history.push(next);value=next;}});
  }
  field('Label tool').value=mode;fire(field('Label tool'),'change');
  for(const history of writes.values()) {expect(history.length).toBeGreaterThan(0);expect(history).not.toContain(true);}
  expect(button('Undo label edit').disabled).toBe(true);
  expect(field('Labels layer').disabled).toBe(true);
  expect(field('Brush size').disabled).toBe(mode==='polygon');
  expect(field('Edit dimensions').disabled).toBe(mode==='polygon');
  await settleMicrotasks();release();await settleMicrotasks();
  for(const history of writes.values())expect(history).not.toContain(true);
  control.setAvailable(false);
  for(const history of writes.values())expect(history.at(-1)).toBe(true);
});

test.each(['paint','polygon'])('Escape cancels focused unsent %s before workspace dismissal', async mode => {
  const {send}=open();field('Label tool').value=mode;
  pointer('pointerdown');
  if(mode==='polygon')pointer('pointerup');
  expect(control.blocked()).toBe(true);
  const panels=new PanelStack(), close=vi.fn();
  panels.register({id:'scientific',isOpen:()=>true,close,footprint:()=>null});
  const event=Object.assign(new Event('keydown',{cancelable:true}),{key:'Escape'});
  Object.defineProperty(event,'target',{value:canvas});
  canvas.dispatchEvent(event);panels.onEscape(event);win.dispatchEvent(event);
  await settleMicrotasks();
  expect(close).not.toHaveBeenCalled();expect(send).not.toHaveBeenCalled();expect(control.blocked()).toBe(false);
});

test('Escape without a Labels draft remains available to workspace dismissal',()=>{
  open();const panels=new PanelStack(),close=vi.fn();
  panels.register({id:'scientific',isOpen:()=>true,close,footprint:()=>null});
  const event=Object.assign(new Event('keydown',{cancelable:true}),{key:'Escape'});
  Object.defineProperty(event,'target',{value:canvas});
  canvas.dispatchEvent(event);panels.onEscape(event);
  expect(close).toHaveBeenCalledTimes(1);
});

test('Labels say what can be edited and why, in the worker\'s own limits, never a single 64 MiB cap', async () => {
  const { readFileSync } = await import('node:fs');
  const { LABELS_SIZE_WORDS } = await import('./scientific-labels');
  const worker = (file: string) => readFileSync(new URL(`../../../services/env_worker/${file}`, import.meta.url), 'utf8');
  expect(worker('scientific_outputs.py')).toMatch(/^EAGER_BYTES = 512 << 20/m);
  expect(worker('lazy_labels.py')).toMatch(/^DIRTY_BYTES = 64 << 20/m);
  expect(worker('lazy_labels.py')).toMatch(/^WRITE_BYTES = 16 << 20/m);
  expect(LABELS_SIZE_WORDS).toContain('Labels up to 512 MiB');
  expect(LABELS_SIZE_WORDS).toContain('one edit can change up to 16 MiB');
  expect(LABELS_SIZE_WORDS).toContain('up to 64 MiB of changes wait for Save');
  expect(LABELS_SIZE_WORDS).toContain('is refused and nothing changes');
  expect(LABELS_SIZE_WORDS).not.toMatch(/supports .*up to 64 MiB/);
});
