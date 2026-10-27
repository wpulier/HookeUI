// Synthetic canvas/track lifecycle; real encoded pixels remain browser/package acceptance.
import {afterEach,expect,test,vi} from 'vitest';
import {AnnotatedRecording} from './annotated-recording';
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
function setup(){
 vi.useFakeTimers();
 const document=Object.assign(new EventTarget(),{hidden:false}),window=new EventTarget();
 vi.stubGlobal('document',document);vi.stubGlobal('window',window);vi.stubGlobal('getComputedStyle',()=>({display:'block',visibility:'visible'}));
 const track={readyState:'live',stop:vi.fn()},stream={getVideoTracks:()=>[track],getTracks:()=>[track]};
 const canvas={width:640,height:480,isConnected:true,getBoundingClientRect:()=>({width:640,height:480}),closest:()=>null,captureStream:vi.fn(()=>stream)};
 const status={reason:null as string|null,owner:'project/tab/input'};
 const capture=new AnnotatedRecording(canvas as unknown as HTMLCanvasElement,()=>status.reason,()=>status.owner);
 return {capture,canvas,track,document,window,status};
}
test('captures the existing canvas and removes its watch on disposal',()=>{
 const {capture,canvas,status}=setup(),ended=vi.fn();
 const active=capture.start(ended);expect(canvas.captureStream).toHaveBeenCalledWith(30);
 active.dispose();status.owner='other';vi.advanceTimersByTime(500);expect(ended).not.toHaveBeenCalled();
 expect(vi.getTimerCount()).toBe(0);
});
test.each(['hidden','owner','lost'])('stops once when the captured view becomes %s',kind=>{
 const {capture,document,status}=setup(),ended=vi.fn();capture.start(ended);
 if(kind==='hidden'){document.hidden=true;document.dispatchEvent(new Event('visibilitychange'));}
 if(kind==='owner'){status.owner='new-project/tab';capture.beforeFrame();}
 if(kind==='lost'){status.reason='The input disappeared.';vi.advanceTimersByTime(250);}
 expect(ended).toHaveBeenCalledOnce();vi.advanceTimersByTime(1000);expect(ended).toHaveBeenCalledOnce();
 expect(vi.getTimerCount()).toBe(0);
});
test('an unavailable or hidden input never starts canvas capture',()=>{
 const {capture,canvas,status,document}=setup();status.reason='Choose an input';
 expect(()=>capture.start(vi.fn())).toThrow('Choose an input');
 status.reason=null;document.hidden=true;expect(()=>capture.start(vi.fn())).toThrow('Show the input view');
 expect(canvas.captureStream).not.toHaveBeenCalled();
});
