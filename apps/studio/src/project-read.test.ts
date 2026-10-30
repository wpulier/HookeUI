import {afterEach,expect,test,vi} from 'vitest';
import {ProjectRead} from './project-read';
afterEach(()=>vi.useRealTimers());
test('overlapping refresh events accept the original reply instead of starving the Project',async()=>{
 let finish!:()=>void;let accepted=0;
 const read=new ProjectRead();const load=vi.fn(async(_signal:AbortSignal,current:()=>boolean)=>{
  await new Promise<void>(resolve=>{finish=resolve;});if(current())accepted++;
 });
 const first=read.run('A',load);await Promise.resolve();
 for(let n=0;n<50;n++)expect(read.run('A',load)).toBe(first);
 finish();await first;expect(load).toHaveBeenCalledOnce();expect(accepted).toBe(1);
 await read.run('A',async()=>{accepted++;});expect(accepted).toBe(2);
});
test('navigation cancels stale reads, including return to the same Project',async()=>{
 let finish!:()=>void;let oldSignal!:AbortSignal;let accepted=false;
 const read=new ProjectRead();const first=read.run('A',async(signal,current)=>{oldSignal=signal;await new Promise<void>(r=>{finish=r;});accepted=current();});
 await Promise.resolve();await read.run('B',async()=>{});await read.run('A',async()=>{});
 expect(oldSignal.aborted).toBe(true);finish();await first;expect(accepted).toBe(false);
});
test('slow reads announce after ten seconds and abort after thirty; a new read can retry',async()=>{
 vi.useFakeTimers();const changed=vi.fn();const read=new ProjectRead(changed);
 const first=read.run('A',async(signal)=>new Promise<void>(resolve=>signal.addEventListener('abort',()=>resolve())));
 await Promise.resolve();await vi.advanceTimersByTimeAsync(9_999);expect(read.slow).toBe(false);
 await vi.advanceTimersByTimeAsync(1);expect(read.slow).toBe(true);expect(changed).toHaveBeenCalledOnce();
 await vi.advanceTimersByTimeAsync(20_000);await first;expect(read.slow).toBe(false);
 const load=vi.fn(async()=>{});await read.run('A',load);expect(load).toHaveBeenCalledOnce();
});
test('ordinary completed polls preserve the rendered control tree',async()=>{
 const changed=vi.fn();const read=new ProjectRead(changed);
 await read.run('A',async()=>{});await read.run('A',async()=>{});
 expect(changed).not.toHaveBeenCalled();
});
