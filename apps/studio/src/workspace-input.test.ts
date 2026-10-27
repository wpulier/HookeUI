import {expect,test,vi} from 'vitest';
import {CameraInputUnavailable,selectObservedWorkspaceInput} from './workspace-input';

test('startup waits for the first failed bridge observation and opens no camera command',async()=>{
 let health={state:'waiting',bridge:{ok:false,error:''}};
 const select=vi.fn(),check=vi.fn();
 const waitFor=vi.fn(async(observed:()=>boolean,_message:string,_timeout:number)=>{
  expect(observed()).toBe(false);
  // FrameSource keeps state waiting until three failed probes; its first error is already observed absence.
  health={state:'waiting',bridge:{ok:false,error:'ConnectError: refused'}};
  expect(observed()).toBe(true);
 });
 await expect(selectObservedWorkspaceInput('live',{check,health:()=>health,waitFor,select})).rejects.toBeInstanceOf(CameraInputUnavailable);
 expect(select).not.toHaveBeenCalled();expect(check).toHaveBeenCalledTimes(2);
 expect(waitFor.mock.calls[0][2]).toBe(3000);
});
test.each(['live','bridge_not_live','no_camera'])('an observed reachable camera state preserves ordinary source selection and its refusal: %s',async state=>{
 const error=new Error('Selection permission refused');const select=vi.fn().mockRejectedValue(error);
 await expect(selectObservedWorkspaceInput('live',{check:vi.fn(),health:()=>({state,bridge:{ok:state==='live',error:''}}),waitFor:async check=>{expect(check()).toBe(true);},select})).rejects.toBe(error);
 expect(select).toHaveBeenCalledOnce();
});
test('a Project or recording change while waiting refuses before any source command',async()=>{
 const select=vi.fn(),check=vi.fn().mockImplementationOnce(()=>{}).mockImplementationOnce(()=>{throw new Error('Project changed');});
 await expect(selectObservedWorkspaceInput('live',{check,health:()=>({state:'live',bridge:{ok:true,error:''}}),waitFor:async()=>{},select})).rejects.toThrow('Project changed');
 expect(select).not.toHaveBeenCalled();
});
test('a retained recording does not wait for camera health or change the selected source',async()=>{
 const waitFor=vi.fn(),select=vi.fn();await selectObservedWorkspaceInput('a7',{check:vi.fn(),health:()=>null,waitFor,select});
 expect(waitFor).not.toHaveBeenCalled();expect(select).toHaveBeenCalledOnce();
});
test('unknown health timeout is not falsely classified as a disconnected camera',async()=>{
 const select=vi.fn(),failure=new Error('No health observation');
 await expect(selectObservedWorkspaceInput('live',{check:vi.fn(),health:()=>null,waitFor:async()=>{throw failure;},select})).rejects.toBe(failure);
 expect(select).not.toHaveBeenCalled();
});
