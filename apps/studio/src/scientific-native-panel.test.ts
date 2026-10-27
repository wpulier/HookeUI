// Synthetic browser event/pixel receipts; actual confined Qt has separate tests.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { ScientificNativePanel } from './scientific-native-panel';
import { nativePanelFrame, type NativePanelFrame } from './scientific-native-panel-wire';
import { scientificPresentation } from './scientific-presentation';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

const state = (): NativePanelFrame => ({format:'scientific-native-panel-v1',panel:'a'.repeat(32),frame:'b'.repeat(64),width:32,height:24,pixelRatio:1,focus:null,
  accessibility:[{id:'1',parent:null,role:'Client',name:'Tool',description:'',value:'',rect:[0,0,32,24],disabled:false,focused:false,focusable:false,invisible:false,checked:false,checkable:false,readOnly:false}]});
class Canvas extends TestElement {
  constructor(){super("canvas");}
  width=0; height=0; isConnected=true;
  getContext() { return {createImageData:(w:number,h:number)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:()=>{}}; }
  hasPointerCapture(id:number) { return this.captured===id; }
  focus() {}
}
let root:TestElement, canvas:Canvas, panel:ScientificNativePanel;
let scheduled:Map<number,FrameRequestCallback>, index:number;
let load = vi.fn(), send = vi.fn();
const reply = () => ({frame:state(),pixels:new Uint8Array(32*24*4)});
beforeEach(() => {
  const dom=syntheticDOM(); root=dom.host; canvas=new Canvas();
  const doc=Object.assign(new EventTarget(),dom.document,{visibilityState:'visible',createElement:(tag:string)=>tag==='canvas'?canvas:new TestElement(tag)});
  vi.stubGlobal('document',doc); vi.stubGlobal('window',new EventTarget());
  vi.stubGlobal('getComputedStyle',(element:TestElement)=>({opacity:'1',transform:'none',...element.style}));
  vi.stubGlobal('MutationObserver',class {observe(){} disconnect(){} takeRecords(){return [];}});
  scheduled=new Map(); index=0;
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{scheduled.set(++index,callback);return index;});
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>scheduled.delete(id));
  load=vi.fn(async()=>reply()); send=vi.fn(async()=>{});
  panel=new ScientificNativePanel(root as unknown as HTMLElement,{load,send});
  root.append(panel.element as unknown as TestElement);
  panel.bind('review-one'); panel.setAvailable(true); panel.setBlocked(false); panel.element.open=true;
});
afterEach(()=>{panel?.close();vi.unstubAllGlobals();});
const buttons=()=>root.querySelectorAll('button').filter(button=>button.textContent!=='Dismiss previous problem');
async function settle(){for(let i=0;i<8;i++)await settleMicrotasks();}
function paint(){const work=[...scheduled.values()];scheduled.clear();work.forEach(f=>f(0));}
async function open(){fire(buttons()[0],'click');await settle();paint();}
const gesture=()=>{fire(canvas,'pointerdown',{pointerId:1,clientX:10,clientY:10,button:0});fire(canvas,'pointerup',{pointerId:1,clientX:10,clientY:10,button:0});};

test('panel paint is independent, admitted only after drawing; a click uses that exact receipt',async()=>{
  fire(buttons()[0],'click');await settle();
  expect(scientificPresentation(root as unknown as HTMLElement)).toEqual({paint:null,panelPaint:null});
  gesture();expect(send).not.toHaveBeenCalled();
  paint();const receipt=scientificPresentation(root as unknown as HTMLElement)!.panelPaint!;
  gesture();await settle();
  expect(send).toHaveBeenCalledExactlyOnceWith({panel:'a'.repeat(32),frame:'b'.repeat(64),op:'click',value:[1,1]},receipt.draw);
  expect(load).toHaveBeenCalledTimes(2);
  expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).toBeNull();
  paint();expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint?.draw).not.toBe(receipt.draw);
});
test('callback failure retains text, revokes paint and never retries until explicit inspection',async()=>{
  await open();const input=root.querySelector('input')!;input.value='µm';
  send.mockRejectedValueOnce(new Error('Plugin callback failed'));
  fire(buttons()[1],'click');await settle();
  expect(input.value).toBe('µm');expect(send).toHaveBeenCalledTimes(1);expect(load).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('action is not replayed');expect(buttons()[1].disabled).toBe(true);
  fire(buttons()[0],'click');await settle();paint();expect(send).toHaveBeenCalledTimes(1);
  fire(buttons()[1],'click');await settle();expect(send).toHaveBeenCalledTimes(2);expect(input.value).toBe('');
});
test('closing while loading or moving to another session cannot publish late panel pixels',async()=>{
  let resolve!:(value:ReturnType<typeof reply>)=>void;
  load.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
  fire(buttons()[0],'click');await settle();panel.element.open=false;fire(panel.element as unknown as TestElement,'toggle');
  resolve(reply());await settle();paint();expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).toBeNull();
  panel.element.open=true;await open();panel.bind('other-review');gesture();expect(send).not.toHaveBeenCalled();
});
test('cancelled pointer gestures never dispatch partial actions',async()=>{
  await open();fire(canvas,'pointerdown',{pointerId:1,clientX:10,clientY:10,button:0});fire(canvas,'pointercancel');
  fire(canvas,'pointerup',{pointerId:1,clientX:11,clientY:11,button:0});await settle();expect(send).not.toHaveBeenCalled();
});
test('focusing a partially visible panel cannot scroll the clicked control away',async()=>{
  await open();
  const rect=canvas.getBoundingClientRect();
  let scrolled=false;
  canvas.focus=(options?:FocusOptions)=>{if(!options?.preventScroll)scrolled=true;};
  canvas.getBoundingClientRect=()=>scrolled?{...rect,top:rect.top-80,bottom:rect.bottom-80}:rect;
  gesture();await settle();
  expect(scrolled).toBe(false);
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0]).toMatchObject({op:'click',value:[1,1]});
});
test.each([{width:true},{height:1025},{pixelRatio:true},{extra:'untrusted'},{accessibility:[]},{frame:'c'.repeat(63)}])('invalid display is rejected %j',change=>{
  expect(()=>nativePanelFrame({...state(),...change})).toThrow();
});
test('malformed accessibility, Unicode and excessive descriptions cannot enter the controls',()=>{
  for(const change of [{parent:'1'},{name:'\ud800'},{disabled:1},{rect:[0,0,-1,1]}]){
    const frame=state();Object.assign(frame.accessibility[0],change);expect(()=>nativePanelFrame(frame)).toThrow();
  }
  const frame=state();frame.accessibility.push(...Array.from({length:90},(_,i)=>({...frame.accessibility[0],id:String(i+2),parent:'1',name:'x'.repeat(1000)})));
  expect(()=>nativePanelFrame(frame)).toThrow();
});


test('newer unsent text survives an acknowledgement, including edit-away-and-back',async()=>{
  await open();const input=root.querySelector('input')!;input.value='first';
  let resolve!:()=>void;send.mockImplementationOnce(()=>new Promise<void>(done=>{resolve=done;}));
  fire(buttons()[1],'click');await settle();input.value='new';fire(input,'input');input.value='first';fire(input,'input');
  resolve();await settle();expect(input.value).toBe('first');expect(send).toHaveBeenCalledTimes(1);
  expect(panel.hasDraft()).toBe(true);
});
test('direct typing is preserved as one explicit draft rather than dropped between network roundtrips',async()=>{
  await open();const input=root.querySelector('input')!;
  Object.assign(input,{focus:()=>{}});
  for(const key of ['a','b','c'])fire(canvas,'keydown',{key});
  expect(input.value).toBe('abc');expect(send).not.toHaveBeenCalled();
  expect(panel.hasDraft()).toBe(true);
  fire(buttons()[1],'click');await settle();expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0]).toMatchObject({op:'text',value:'abc'});
  expect(panel.hasDraft()).toBe(false);
});


// B0i: a delivered command is not a failed command when its display cannot refresh.
test('acknowledged text clears its draft when the panel becomes hidden before refresh',async()=>{
  await open();const input=root.querySelector('input')!;input.value='µm';
  let resolve!:()=>void;send.mockImplementationOnce(()=>new Promise<void>(done=>{resolve=done;}));
  fire(buttons()[1],'click');await settle();
  const rect=canvas.getBoundingClientRect();
  canvas.getBoundingClientRect=()=>({...rect,width:0,height:0,right:rect.left,bottom:rect.top});
  resolve();await settle();
  expect(input.value).toBe('');
  expect(root.textContent).toContain('Plugin interaction delivered');
  expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).toBeNull();
  expect(send).toHaveBeenCalledTimes(1);
  canvas.getBoundingClientRect=()=>rect;await open();
  expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).not.toBeNull();
  expect(send).toHaveBeenCalledTimes(1);
});
test('acknowledged text stays delivered when the display reload fails',async()=>{
  await open();const input=root.querySelector('input')!;input.value='µm';
  load.mockRejectedValueOnce(new Error('Preview transport unavailable'));
  fire(buttons()[1],'click');await settle();
  expect(input.value).toBe('');
  expect(root.textContent).toContain('Plugin interaction delivered');
  expect(root.textContent).not.toContain('Plugin action completed');
  expect(root.textContent).toContain('Preview transport unavailable');
  expect(send).toHaveBeenCalledTimes(1);
  await open();
  expect(root.textContent).toContain('Preview transport unavailable');
  expect(root.textContent).toContain('Previous panel problem');
  expect(send).toHaveBeenCalledTimes(1);
});
test('a queued callback failure found on refresh remains inspectable after recovery',async()=>{
  await open();
  load.mockRejectedValueOnce(new Error('PermissionError: Prepared model unavailable'));
  await open(); await open();
  expect(root.textContent).toContain('Plugin controls ready');
  expect(root.textContent).toContain('PermissionError: Prepared model unavailable');
  expect(send).not.toHaveBeenCalled();
  panel.bind('replacement-session');
  expect(root.textContent).not.toContain('PermissionError: Prepared model unavailable');
});
test('closing the panel after delivery does not retain text as unsent',async()=>{
  await open();const input=root.querySelector('input')!;input.value='µm';
  let resolve!:()=>void;send.mockImplementationOnce(()=>new Promise<void>(done=>{resolve=done;}));
  fire(buttons()[1],'click');await settle();panel.element.open=false;fire(panel.element as unknown as TestElement,'toggle');
  resolve();await settle();expect(input.value).toBe('');expect(send).toHaveBeenCalledTimes(1);
  expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).toBeNull();
});
test('a failed command retains text and never claims successful delivery',async()=>{
  await open();const input=root.querySelector('input')!;input.value='µm';
  send.mockRejectedValueOnce(new Error('Command acknowledgement lost'));
  fire(buttons()[1],'click');await settle();
  expect(input.value).toBe('µm');expect(root.textContent).not.toContain('Plugin action completed');
  expect(root.textContent).toContain('Command acknowledgement lost');
  expect(load).toHaveBeenCalledTimes(1);expect(send).toHaveBeenCalledTimes(1);
});
test('an old acknowledgement cannot clear a replacement session draft',async()=>{
  await open();const input=root.querySelector('input')!;input.value='µm';
  let resolve!:()=>void;send.mockImplementationOnce(()=>new Promise<void>(done=>{resolve=done;}));
  fire(buttons()[1],'click');await settle();panel.bind('replacement-review');
  resolve();await settle();expect(input.value).toBe('µm');
  expect(load).toHaveBeenCalledTimes(1);expect(send).toHaveBeenCalledTimes(1);
  expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).toBeNull();
});

test('panel loading keeps its keyboard entry reachable while refusing repeated activation',async()=>{
  let resolve!:(value:ReturnType<typeof reply>)=>void;
  load.mockImplementationOnce(()=>new Promise(done=>{resolve=done;}));
  const openButton=buttons()[0];
  fire(openButton,'click');await settle();panel.setBlocked(true);
  expect(openButton.disabled).toBe(false);
  expect(openButton.getAttribute('aria-disabled')).toBe('true');
  fire(openButton,'click');expect(load).toHaveBeenCalledTimes(1);
  resolve(reply());await settle();paint();panel.setBlocked(false);
  expect(openButton.getAttribute('aria-disabled')).toBe('false');
  expect(openButton.textContent).toBe('Refresh panel');
  expect(send).not.toHaveBeenCalled();
});

test('a late panel-load failure cannot replace a new session’s status',async()=>{
  let reject!:(error:Error)=>void;
  load.mockImplementationOnce(()=>new Promise((_,fail)=>{reject=fail;}));
  fire(buttons()[0],'click');await settle();panel.bind('replacement-review');
  const before=root.textContent;
  reject(new Error('Old session failed'));await settle();
  expect(root.textContent).toBe(before);
  expect(root.textContent).not.toContain('Old session failed');
  await open();expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint).not.toBeNull();
});

test('Escape leaves plugin controls without also closing their workspace window',async()=>{
  await open();const focus=vi.fn();Object.assign(buttons()[0],{focus});
  const event=new Event('keydown',{cancelable:true,bubbles:true});Object.defineProperty(event,'key',{value:'Escape'});
  canvas.dispatchEvent(event);
  expect(focus).toHaveBeenCalledOnce();expect(event.defaultPrevented).toBe(true);
  expect(send).not.toHaveBeenCalled();
});


test('relocating a loaded panel renews its paint without sending or clearing a text draft',async()=>{
  panel.layoutChanged();await settle();expect(load).not.toHaveBeenCalled();
  await open();const input=root.querySelector('input')!;input.value='unsent title';
  const before=scientificPresentation(root as unknown as HTMLElement)?.panelPaint?.draw;
  panel.layoutChanged();await settle();paint();
  expect(load).toHaveBeenCalledTimes(2);expect(send).not.toHaveBeenCalled();expect(input.value).toBe('unsent title');
  expect(scientificPresentation(root as unknown as HTMLElement)?.panelPaint?.draw).not.toBe(before);
});


test('stationary browser pointer moves remain one native button click',async()=>{
  await open();
  fire(canvas,'pointerdown',{pointerId:1,clientX:10,clientY:10,button:0});
  for(let i=0;i<4;i++)fire(canvas,'pointermove',{pointerId:1,clientX:10,clientY:10,button:0});
  fire(canvas,'pointerup',{pointerId:1,clientX:10,clientY:10,button:0});await settle();
  expect(send).toHaveBeenCalledTimes(1);expect(send.mock.calls[0][0]).toMatchObject({op:'click',value:[1,1]});
});
