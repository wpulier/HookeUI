import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import { ScientificCanvas } from './scientific-canvas';
import { ScientificPlayback } from './scientific-playback';
import { scientificFrame, type ScientificFrame } from './scientific-view';
import { TestElement, fire, syntheticDOM } from './workspace-windows.test-support';
let root: TestElement, control: ScientificPlayback;
let doc: ReturnType<typeof syntheticDOM>['document'], win: EventTarget;
beforeEach(()=>{
 const dom=syntheticDOM();root=Object.assign(dom.host,{isConnected:true});doc=dom.document;win=new EventTarget();
 vi.stubGlobal('document',doc);vi.stubGlobal('window',win);vi.useFakeTimers();
});
afterEach(()=>{control?.close();vi.useRealTimers();vi.unstubAllGlobals();});
const button=(name:string)=>root.querySelectorAll('button').find(row=>row.textContent===name)!;
const field=(name:string)=>[...root.querySelectorAll('select'),...root.querySelectorAll('input')].find(row=>row.getAttribute('aria-label')===name)!;
function open(step?: (frame:string,axis:number,index:number)=>Promise<ScientificFrame>, drawn: (frame:string)=>Promise<void> = async()=>{}){
 let frame=scientificFrame(cases.valid),serial=0;
 const send=vi.fn(step ?? (async (_:string,axis:number,index:number)=>{
  frame=structuredClone(frame);frame.frame=(++serial).toString(16).padStart(64,'0');
  frame.dimensions.steps[axis]=index;frame.dimensions.point[axis]=frame.dimensions.ranges[axis][0]+index*frame.dimensions.ranges[axis][2];
  control.update(scientificFrame(frame));return frame;
 }));
 control=new ScientificPlayback(root as unknown as HTMLElement,{step:send,drawn,changed:()=>{}});
 control.bindSession('one');control.update(frame);control.setAvailable(true);
 return {send,frame};
}
test.each([['1',4],['-1',2]])('plays a sliced axis %s using the latest accepted frame and stops at its boundary',async(direction,index)=>{
 const {send,frame}=open();field('Playback direction').value=direction;field('Playback mode').value='once';field('Maximum slices per second').value='2';
 expect(field('Playback axis').value).toBe('2');
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(500);
 expect(send).toHaveBeenNthCalledWith(1,frame.frame,2,index);
 await vi.advanceTimersByTimeAsync(500);
 expect(send.mock.calls[1][0]).toBe('1'.padStart(64,'0'));
 await vi.advanceTimersByTimeAsync(60000);
 expect(control.active()).toBe(false);expect(root.textContent).toContain('Reached the end');
 expect(send.mock.calls.every(([,axis])=>axis===2)).toBe(true);
});
test('one in-flight step; Stop admits no later slice even if the pending reply arrives',async()=>{
 let resolve!:(frame:ScientificFrame)=>void;
 const {send,frame}=open(()=>new Promise(done=>resolve=done));
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(5000);expect(send).toHaveBeenCalledOnce();
 fire(button('Stop playback'),'click');expect(control.active()).toBe(true); // pending work remains owned
 const next=structuredClone(frame);next.frame='c'.repeat(64);next.dimensions.steps[2]=4;control.update(next);resolve(next);
 await vi.advanceTimersByTimeAsync(10000);expect(send).toHaveBeenCalledOnce();expect(control.active()).toBe(false);
});
test.each(['hidden','blur','unavailable','closed','session'])('%s prevents further playback requests',async(reason)=>{
 const {send}=open();field('Playback mode').value='once';fire(button('Play slices'),'click');
 if(reason==='hidden')root.dataset.workspaceHidden='true';
 if(reason==='blur')win.dispatchEvent(new Event('blur'));
 if(reason==='unavailable')control.setAvailable(false);
 if(reason==='closed')control.close();
 if(reason==='session')control.bindSession('two');
 await vi.advanceTimersByTimeAsync(1000);expect(send).not.toHaveBeenCalled();expect(control.active()).toBe(false);
});
test('an uncertain operation stops and requires a fresh view before playing again',async()=>{
 const {send,frame}=open(async()=>{throw new Error('Connection lost');});
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(10000);
 expect(send).toHaveBeenCalledOnce();expect(button('Play slices').disabled).toBe(true);expect(root.textContent).toContain('check the current view');
 control.update({...frame,frame:'d'.repeat(64)});expect(button('Play slices').disabled).toBe(false);
});
test('a slow paint waits without queuing another slice, then continues from the rendered frame',async()=>{
 let latest=scientificFrame(cases.valid),serial=0;
 const {send}=open(async(_frame,axis,index)=>{
  latest=structuredClone(latest);latest.frame=(++serial).toString(16).padStart(64,'0');latest.dimensions.steps[axis]=index;
  control.update(latest);control.setAvailable(false,latest.frame);return latest;
 });
 field('Playback mode').value='loop';fire(button('Play slices'),'click');
 await vi.advanceTimersByTimeAsync(2500);
 expect(send).toHaveBeenCalledOnce();expect(control.active()).toBe(true);
 control.setAvailable(true);await vi.advanceTimersByTimeAsync(500);
 expect(send).toHaveBeenCalledTimes(2);expect(send.mock.calls[1][0]).toBe('1'.padStart(64,'0'));
 fire(button('Stop playback'),'click');control.setAvailable(true);await vi.advanceTimersByTimeAsync(2500);
 expect(send).toHaveBeenCalledTimes(2);expect(control.active()).toBe(false);
});
test('a pending paint for another frame cannot keep playback alive',async()=>{
 const {send}=open();field('Playback mode').value='once';fire(button('Play slices'),'click');control.setAvailable(false,'f'.repeat(64));
 await vi.advanceTimersByTimeAsync(1000);expect(send).not.toHaveBeenCalled();expect(control.active()).toBe(false);
});
test('other controls changing the frame stop playback before another step',async()=>{
 const {send,frame}=open();field('Playback mode').value='once';fire(button('Play slices'),'click');control.update({...frame,frame:'d'.repeat(64)});
 await vi.advanceTimersByTimeAsync(1000);expect(send).not.toHaveBeenCalled();expect(control.active()).toBe(false);
});
test('unsupported rate and fully displayed dimensions never start',async()=>{
 const {send,frame}=open();
 for(const rate of ['11','600','0','-2','']){
  field('Maximum slices per second').value=rate;fire(button('Play slices'),'click');
  await vi.advanceTimersByTimeAsync(1000);expect(send).not.toHaveBeenCalled();expect(root.textContent).toContain('0.1 to 10');
 }
 control.update({...frame,dimensions:{...frame.dimensions,ndisplay:3,displayed:[2,0,1]}});
 expect(button('Play slices').disabled).toBe(true);
});
test('reply from a replaced session cannot stop or release the newer session',async()=>{
 const pending:Array<(frame:ScientificFrame)=>void>=[];
 const {send,frame}=open(()=>new Promise(done=>pending.push(done)));
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(500);
 control.bindSession('two');control.update(frame);control.setAvailable(true);fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(500);
 expect(send).toHaveBeenCalledTimes(2);pending[0](frame);await vi.advanceTimersByTimeAsync(5000);
 expect(control.active()).toBe(true);expect(send).toHaveBeenCalledTimes(2);
});
test.each([['loop',[4,2,3,4,2]],['bounce',[4,3,2,3,4]]])('%s keeps the declared range and visits its endpoints',async(mode,sequence)=>{
 const {send}=open();field('First slice').value='2';field('Last slice').value='4';field('Playback mode').value=mode as string;field('Maximum slices per second').value='2';
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(2400);fire(button('Stop playback'),'click');
 expect(send.mock.calls.map(([, ,index])=>index)).toEqual(sequence);
});
test('reversed or outside ranges cannot dispatch',async()=>{
 const {send}=open();field('First slice').value='5';field('Last slice').value='2';fire(button('Play slices'),'click');
 await vi.advanceTimersByTimeAsync(1000);expect(send).not.toHaveBeenCalled();
 field('First slice').value='0';field('Last slice').value='90';fire(button('Play slices'),'click');
 await vi.advanceTimersByTimeAsync(1000);expect(send).not.toHaveBeenCalled();
});
test('an unexpected changed axis in the reply cannot silently continue playback',async()=>{
 const frame=scientificFrame(cases.valid);
 const {send}=open(async()=>{
  const next=structuredClone(frame);next.frame='c'.repeat(64);next.dimensions.steps[2]=4;next.dimensions.steps[0]=0;
  control.update(next);return next;
 });
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(5000);
 expect(send).toHaveBeenCalledOnce();expect(control.active()).toBe(false);expect(root.textContent).toContain('does not match');
});
test('Play once at the end restarts the requested range',async()=>{
 const {send}=open();field('First slice').value='1';field('Last slice').value='3';field('Playback mode').value='once';field('Maximum slices per second').value='2';
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(2000);
 expect(send.mock.calls.map(([, ,index])=>index)).toEqual([1,2,3]);expect(control.active()).toBe(false);
});
test('defaults are napari\'s playback settings and a fractional rate is honoured',async()=>{
 const {send}=open();
 expect(field('Maximum slices per second').value).toBe('10');expect(field('Playback mode').value).toBe('loop');
 field('Maximum slices per second').value='0.5';field('Playback mode').value='once';field('First slice').value='1';field('Last slice').value='5';
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(1999);expect(send).not.toHaveBeenCalled();
 await vi.advanceTimersByTimeAsync(1);expect(send).toHaveBeenCalledOnce();
});
test('ending says how many slices were drawn and the rate reached, never the requested rate',async()=>{
 const {send}=open();field('First slice').value='1';field('Last slice').value='3';field('Playback mode').value='once';field('Maximum slices per second').value='2';
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(5000);
 expect(send).toHaveBeenCalledTimes(3);
 expect(root.textContent).toMatch(/Shown 3 slices, every one drawn, at \d+\.\d per second \(requested 2\)/);
});
test.each([['loop','2','8',true],['bounce','2','8',true],['once','0','8',false],['once','0','3',true]])(
 'like napari\'s AnimationThread.work, %s from %s..%s steps at once: %s',async(mode,first,last,immediate)=>{
 const {send}=open();field('Playback mode').value=mode as string;field('First slice').value=first as string;field('Last slice').value=last as string;
 field('Maximum slices per second').value='2';
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(0);
 expect(send.mock.calls.length).toBe(immediate?1:0);
 await vi.advanceTimersByTimeAsync(500);expect(send.mock.calls.length).toBe(immediate?2:1);
 if(mode==='once'&&last==='3')expect(send.mock.calls[0][2]).toBe(0); // started on the last frame: the first, at once
});
test('Stop while a slice is in flight counts that slice once it is drawn',async()=>{
 let resolve!:(frame:ScientificFrame)=>void;
 const {frame}=open(()=>new Promise(done=>resolve=done));
 field('Playback mode').value='loop';field('Maximum slices per second').value='2';
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(0);
 fire(button('Stop playback'),'click');
 expect(root.textContent).toContain('Finishing the slice already requested');expect(root.textContent).not.toContain('Shown');
 const next=structuredClone(frame);next.frame='c'.repeat(64);next.dimensions.steps[2]=4;control.update(next);resolve(next);
 await vi.advanceTimersByTimeAsync(10);
 expect(root.textContent).toMatch(/Playback stopped\. The current view is kept\. Shown 1 slice, every one drawn/);
});

test('a returned slice is not counted until its matching canvas receipt arrives',async()=>{
 let painted!:()=>void;
 const {send}=open(undefined,()=>new Promise<void>(resolve=>painted=resolve));
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(0);
 fire(button('Stop playback'),'click');await vi.advanceTimersByTimeAsync(1000);
 expect(send).toHaveBeenCalledOnce();expect(root.textContent).not.toContain('Shown');
 expect(control.active()).toBe(true);
 painted();await vi.advanceTimersByTimeAsync(0);
 expect(root.textContent).toContain('Shown 1 slice, every one drawn');expect(control.active()).toBe(false);
});

// Real Canvas and Playback components; only browser pixels and RAF timing are synthetic.
test.each(['paint','hide','replace','close'])('playback counts only real canvas receipts: %s',async(action)=>{
 const callbacks=new Map<number,FrameRequestCallback>();let serial=0;
 vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{callbacks.set(++serial,callback);return serial;});
 vi.stubGlobal('cancelAnimationFrame',(id:number)=>callbacks.delete(id));
 vi.stubGlobal('MutationObserver',class {observe(){} disconnect(){} takeRecords(){return [];}});
 vi.stubGlobal('getComputedStyle',()=>({opacity:'1',transform:'none'}));
 const canvas=Object.assign(new TestElement('canvas'),{width:0,height:0,isConnected:true,
  getContext:()=>({createImageData:(w:number,h:number)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:()=>{}})});
 canvas.style.width='320px';canvas.style.height='240px';
 const receipts:unknown[]=[];canvas.addEventListener('scientific-frame-drawn',event=>receipts.push((event as CustomEvent).detail));
 const drawing=new ScientificCanvas(canvas as unknown as HTMLCanvasElement,{drawn:()=>{},revoked:()=>{}});
 const next=scientificFrame(cases.valid);next.frame='c'.repeat(64);next.dimensions.steps[2]=4;
 next.dimensions.point[2]=next.dimensions.ranges[2][0]+4*next.dimensions.ranges[2][2];
 open(async()=>{control.update(next);drawing.show(next,new Uint8Array(next.width*next.height*4));return next;},frame=>drawing.whenDrawn(frame).then(()=>{}));
 try {
  fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(0);fire(button('Stop playback'),'click');
  expect(root.textContent).not.toContain('Shown');
  // Copied observational events never fulfill the owned canvas promise.
  canvas.dispatchEvent(new CustomEvent('scientific-frame-drawn',{detail:{frame:next.frame,draw:'copied'}}));
  await vi.advanceTimersByTimeAsync(0);expect(root.textContent).not.toContain('Shown');
  if(action==='paint'){const pending=[...callbacks.values()];callbacks.clear();pending.forEach(callback=>callback(0));}
  else if(action==='hide'){Object.assign(doc,{visibilityState:'hidden'});doc.dispatchEvent(new Event('visibilitychange'));}
  else if(action==='replace')drawing.show({...next,frame:'d'.repeat(64)},new Uint8Array(next.width*next.height*4));
  else drawing.close();
  await vi.advanceTimersByTimeAsync(0);
  expect(root.textContent.includes('Shown 1 slice, every one drawn')).toBe(action==='paint');
  expect(control.active()).toBe(false);
  expect(receipts.length).toBe(action==='paint'?2:1);
 } finally {drawing.close();}
});

test('a prior session paint receipt cannot release or count the replacement playback',async()=>{
 const pending:Array<()=>void>=[];
 const {frame}=open(undefined,()=>new Promise<void>(resolve=>pending.push(resolve)));
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(0);
 control.bindSession('two');control.update(frame);control.setAvailable(true);
 fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(0);
 pending[0]();await vi.advanceTimersByTimeAsync(0);
 expect(control.active()).toBe(true);expect(root.textContent).not.toContain('Shown');
 fire(button('Stop playback'),'click');pending[1]();await vi.advanceTimersByTimeAsync(0);
 expect(root.textContent).toContain('Shown 1 slice, every one drawn');expect(control.active()).toBe(false);
});
