import {afterEach,expect,test,vi} from 'vitest';
import {mountAppUpdates,updateState,updateWords} from './app-updates';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());
const state={currentVersion:'0.1.2',availableVersion:null,downloadedVersion:null,error:null};
const nothing=async()=>({blockers:[] as string[],running:[] as string[]});
test('browser workspaces never advertise a native installer',()=>{
 vi.stubGlobal('window',new EventTarget());
 const rail=new TestElement('nav'),settings=new TestElement('section');
 mountAppUpdates(rail as unknown as HTMLElement,settings as unknown as HTMLElement,nothing);
 expect(rail.children).toHaveLength(0);expect(settings.children).toHaveLength(0);
 expect((window as unknown as {hookeUpdateReadiness?:unknown}).hookeUpdateReadiness).toBeUndefined();
});
function mounted(readiness:()=>Promise<{blockers:string[];running:string[]}>=nothing){
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);const host=new EventTarget();let resolve!:(value:unknown)=>void;
 const postMessage=vi.fn().mockResolvedValueOnce(state).mockImplementation(()=>new Promise(r=>resolve=r));
 Object.assign(host,{webkit:{messageHandlers:{hookeUpdates:{postMessage}}}});vi.stubGlobal('window',host);
 const rail=new TestElement('nav'),settings=new TestElement('section');
 mountAppUpdates(rail as unknown as HTMLElement,settings as unknown as HTMLElement,readiness);
 const reminder=()=>rail.children[0] as TestElement & {onclick:()=>void};
 return {host,postMessage,rail,settings,reminder,resolve:(value:unknown)=>resolve(value),
  readiness:()=>(host as unknown as {hookeUpdateReadiness:()=>Promise<unknown>}).hookeUpdateReadiness()};
}
test('native reminder follows release state, opens native review once, and reports what is unsaved and running',async()=>{
 let answer={blockers:[] as string[],running:[] as string[]};
 const m=mounted(async()=>answer);
 await Promise.resolve();expect(m.reminder().hidden).toBe(true);
 m.host.dispatchEvent(new CustomEvent('hooke-update-state',{detail:{...state,availableVersion:'0.1.3'}}));
 expect(m.reminder().hidden).toBe(false);expect(m.reminder().textContent).toBe('↑ Update');
 expect(m.settings.textContent).toContain('Review update…');
 m.reminder().onclick();m.reminder().onclick();expect(m.postMessage.mock.calls.filter(([arg])=>arg.op==='check')).toHaveLength(1);
 expect(m.reminder().disabled).toBe(true);m.resolve({...state,availableVersion:'0.1.3'});await Promise.resolve();
 expect(m.reminder().disabled).toBe(false);
 answer={blockers:['A Project note is unsaved or has a conflict. Save it, or resolve the conflict.'],running:['Record (it carries on when Hooke opens again)']};
 await expect(m.readiness()).resolves.toEqual(answer);
 m.host.dispatchEvent(new CustomEvent('hooke-update-state',{detail:{...state,error:'Check failed. Retry.'}}));
 expect(m.reminder().hidden).toBe(true);expect(m.settings.textContent).toContain('Check failed. Retry.');
});
test('a downloaded update waiting for the restart: the rail says Restart to update, Settings says so, and either asks again',async()=>{
 const m=mounted();await Promise.resolve();
 m.host.dispatchEvent(new CustomEvent('hooke-update-state',{detail:{...state,availableVersion:'0.4.1',downloadedVersion:'0.4.1'}}));
 expect(m.reminder().hidden).toBe(false);expect(m.reminder().textContent).toBe('Restart to update');
 expect(m.reminder().title).toBe('Hooke 0.4.1 is downloaded. Restart Hooke to finish updating.');
 expect(m.reminder().attributes['aria-label']).toBe('Hooke 0.4.1 is downloaded. Restart Hooke to finish updating.');
 expect(m.settings.textContent).toContain('Hooke 0.4.1 is downloaded. Restart Hooke to finish updating.');
 expect(m.settings.textContent).toContain('Restart to update…');expect(m.settings.textContent).not.toContain('Review update');
 m.reminder().onclick();expect(m.postMessage).toHaveBeenLastCalledWith({op:'check'});
});
test('a readiness read that fails reaches the Mac app as a failure, never as nothing running',async()=>{
 const m=mounted(async()=>{throw new Error('/api/research could not be read.');});
 await expect(m.readiness()).rejects.toThrow('could not be read');
});
test('the Settings detail promises only what the app does',()=>{
 const m=mounted();
 expect(m.settings.textContent).toContain('restarts only when you say so');
 expect(m.settings.textContent).not.toContain('do not automatically resume');
});
test('update words for each state',()=>{
 expect(updateWords(state)).toEqual({reminder:null,title:'',status:'Installed version 0.1.2. Updates are checked periodically.',button:'Check for updates…'});
 expect(updateWords({...state,availableVersion:'0.4.1'}).reminder).toBe('↑ Update');
 expect(updateWords({...state,availableVersion:'0.4.1',downloadedVersion:'0.4.1'}).button).toBe('Restart to update…');
 // An older Mac app says nothing of a download: the field is optional.
 expect(updateState({currentVersion:'0.4.0',availableVersion:null,error:null}).downloadedVersion).toBeNull();
});
test.each([null,{}, {...state,availableVersion:true},{...state,error:3},{...state,downloadedVersion:4}])('malformed native status cannot imply an available update: %j',value=>{
 expect(()=>updateState(value)).toThrow();
});
