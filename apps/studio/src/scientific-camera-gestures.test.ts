import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import {ScientificCameraGestures} from './scientific-camera-gestures';
import {scientificFrame} from './scientific-view';
import type {ScientificCanvas} from './scientific-canvas';
import {TestElement,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';
let root:TestElement,canvas:TestElement & {hasPointerCapture(id:number):boolean;focus():void},win:EventTarget,control:ScientificCameraGestures;
beforeEach(()=>{const dom=syntheticDOM();root=dom.host;canvas=Object.assign(new TestElement('canvas'),{hasPointerCapture:(id:number)=>canvas.captured===id,focus:()=>{}});root.append(canvas);win=new EventTarget();vi.stubGlobal('document',dom.document);vi.stubGlobal('window',win);});
afterEach(()=>{control?.close();vi.unstubAllGlobals();});
function pointer(type:string,x=100,y=90){canvas.dispatchEvent(Object.assign(new Event(type,{cancelable:true}),{button:0,pointerId:1,clientX:x,clientY:y}));}
function open(send=vi.fn(async()=>{})){
  const frame=scientificFrame(cases.valid);frame.dimensions.ndisplay=3;
  let current={frame,draw:'exact-render'};
  const drawing={element:()=>canvas,current:()=>current,position:(draw:string,x:number,y:number)=>{if(draw!==current.draw)throw new Error('stale');return[y,x];}};
  control=new ScientificCameraGestures(root as unknown as HTMLElement,drawing as unknown as ScientificCanvas,{send,changed:()=>{}});
  control.bindSession('one');control.update(frame);control.setAvailable(true);
  return {frame,send,refresh:()=>{current={frame:{...frame,frame:'d'.repeat(64)},draw:'fresh-render'};control.update(current.frame);}};
}
test('orbit submits once on release with the original frame and exact pixels',async()=>{
 const {send,frame}=open();pointer('pointerdown');pointer('pointermove',115,100);expect(send).not.toHaveBeenCalled();
 pointer('pointerup',135,110);await settleMicrotasks();
 expect(send).toHaveBeenCalledWith(frame.frame,{operation:'camera-gesture',value:{mode:'orbit',width:frame.width,height:frame.height,points:[[90,100],[100,115],[110,135]]}});
 expect(root.textContent).toContain('Camera moved');expect(control.blocked()).toBe(false);
});
test.each(['', 'Camera moved. Saved scientific data is unchanged.'])('feedback does not move the canvas during a drag (previous: %s)',async(previous)=>{
 const {send}=open();const status=root.querySelectorAll('p').find(p=>p.getAttribute('role')==='status')!;
 status.textContent=previous;
 const original=canvas.getBoundingClientRect.bind(canvas);
 canvas.getBoundingClientRect=()=>{const rectangle=original();const top=rectangle.top+status.textContent.length*2;return {...rectangle,top,bottom:top+rectangle.height,y:top};};
 pointer('pointerdown');pointer('pointermove',115,100);pointer('pointerup',135,110);await settleMicrotasks();
 expect(send).toHaveBeenCalledTimes(1);expect(root.textContent).toContain('Camera moved');
});
test.each(['blur','escape','resize','revoke','pointercancel','lostpointercapture'])('%s cancels unsent camera movement',async(reason)=>{
 const {send}=open();pointer('pointerdown');pointer('pointermove',115,100);
 if(reason==='blur')win.dispatchEvent(new Event('blur'));
 else if(reason==='escape')win.dispatchEvent(Object.assign(new Event('keydown'),{key:'Escape'}));
 else if(reason==='resize'){canvas.style.width='500px';pointer('pointermove',120,105);}
 else if(reason==='revoke')control.revoke();else pointer(reason);
 pointer('pointerup',135,110);await settleMicrotasks();expect(send).not.toHaveBeenCalled();expect(control.blocked()).toBe(false);
});
test('uncertain response cannot replay the old frame and permits correction after refresh',async()=>{
 const send=vi.fn(async():Promise<void>=>{throw new Error('connection lost');});const {refresh}=open(send);
 pointer('pointerdown');pointer('pointerup',135,110);await settleMicrotasks();expect(send).toHaveBeenCalledTimes(1);
 pointer('pointerdown');pointer('pointerup',135,110);expect(send).toHaveBeenCalledTimes(1);
 refresh();send.mockResolvedValue(undefined);pointer('pointerdown');pointer('pointerup',135,110);await settleMicrotasks();expect(send).toHaveBeenCalledTimes(2);
});
test('2D views do not intercept annotation gestures',async()=>{
 const {frame,send}=open();control.update({...frame,dimensions:{...frame.dimensions,ndisplay:2}});
 pointer('pointerdown');pointer('pointerup',135,110);await settleMicrotasks();expect(send).not.toHaveBeenCalled();
});

test('a new rendered frame cancels an unsent drag instead of applying old coordinates',async()=>{
 const {send,refresh}=open();pointer('pointerdown');pointer('pointermove',115,100);refresh();
 pointer('pointerup',135,110);await settleMicrotasks();expect(send).not.toHaveBeenCalled();expect(control.blocked()).toBe(false);
});
test('a late old-session camera acknowledgement cannot replace the new session status',async()=>{
 let resolve!:()=>void;const send=vi.fn(()=>new Promise<void>(done=>{resolve=done;}));const {frame}=open(send);
 pointer('pointerdown');pointer('pointerup',135,110);expect(control.blocked()).toBe(true);
 control.bindSession('two');control.update(frame);control.setAvailable(true);resolve();await settleMicrotasks();
 expect(control.blocked()).toBe(false);expect(root.textContent).not.toContain('Camera moved');
});

test('grid inspection cannot dispatch single-view camera coordinates',async()=>{
 const {frame,send}=open();control.update({...frame,grid:{enabled:true,shape:[1,2],stride:1,spacing:0}});
 pointer('pointerdown');pointer('pointerup',135,110);await settleMicrotasks();expect(send).not.toHaveBeenCalled();
});
