import {afterEach,expect,it,vi} from 'vitest';
import {mountStudyInitiation} from './study-initiation';
import type {Effort} from './research-efforts';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());
const study:Effort={id:'study',session:'s1',revision:1,digest:'basis',objective:'Compare methods',criteria:'Measured error',status:'active',capMicrousd:0,spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[],tasks:{},findings:[]};
const plan={desiredOutput:'Predictions',evaluation:'Measured error',materials:[],methodNotes:'Fit a baseline',actorMode:'connected-agent',limits:{experiments:2,modelMinutes:30,tokens:60000},status:'draft'};
function setup(saved=plan){
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const storage=new Map<string,string>();vi.stubGlobal('localStorage',{getItem:(k:string)=>storage.get(k)||null,setItem:(k:string,v:string)=>storage.set(k,v),removeItem:(k:string)=>storage.delete(k)});
 const root=new TestElement('section');
 const fetcher=vi.fn(async(url:string,_init?:RequestInit)=>new Response(JSON.stringify(url==='/api/research-instructions'?{packages:[]}:{basis:{id:'study',session:'s1',revision:1,digest:'basis'},plan:saved,assignments:[]})));
 mountStudyInitiation(root as unknown as HTMLElement,study,[],fetcher as unknown as typeof fetch);
 return {root,fetcher,storage};
}
it('retains a local draft and refuses reviewing an older saved plan or refreshing over edits',async()=>{
 const {root,fetcher,storage}=setup();await vi.waitFor(()=>expect(root.querySelector('form')).not.toBeNull());
 const review=root.querySelectorAll('button').find(b=>b.textContent==='Review this saved plan')!;
 const form=root.querySelector('form') as unknown as HTMLFormElement;
 root.querySelector('textarea')!.value='My revised output';
 form.oninput!({stopPropagation:()=>{}} as unknown as InputEvent);
 expect(review.disabled).toBe(true);expect([...storage.values()][0]).toContain('My revised output');
 const refresh=root.querySelectorAll('button').find(b=>b.textContent==='Refresh activity') as unknown as HTMLButtonElement;
 refresh.onclick!(new Event('click') as PointerEvent);await Promise.resolve();
 expect(root.textContent).toContain('Save your draft before refreshing');
 expect(fetcher.mock.calls.every(([,init])=>!init?.method)).toBe(true);
});
it('does not invent an active worker from an authorized connection',async()=>{
 const {root}=setup({...plan,status:'reviewed'});
 await vi.waitFor(()=>expect(root.textContent).toContain('Could not read contributors'));
 expect(root.textContent).not.toContain('Agent actively working');
});

it('keeps typing made while a plan save is in flight',async()=>{
 const {root,fetcher,storage}=setup();await vi.waitFor(()=>expect(root.querySelector('form')).not.toBeNull());
 const form=root.querySelector('form') as unknown as HTMLFormElement;
 form.checkValidity=()=>true;
 let finish!:(r:Response)=>void;
 fetcher.mockImplementationOnce(()=>new Promise<Response>(resolve=>{finish=resolve;}));
 const save=root.querySelectorAll('button').find(b=>b.textContent==='Save research plan') as unknown as HTMLButtonElement;
 save.onclick!(new Event('click') as PointerEvent);
 await vi.waitFor(()=>expect(finish).toBeTypeOf('function'));
 root.querySelector('textarea')!.value='Newer work, typed while saving';
 form.oninput!({stopPropagation:()=>{}} as unknown as InputEvent);
 finish(new Response(JSON.stringify({coordination:{basis:{id:'study',session:'s1',revision:2,digest:'new-basis'},plan,assignments:[]}})));
 await vi.waitFor(()=>expect(root.textContent).toContain('Your newer draft is kept here'));
 expect(root.querySelector('textarea')!.value).toBe('Newer work, typed while saving');
 expect([...storage.values()][0]).toContain('new-basis');
 expect([...storage.values()][0]).toContain('Newer work');
});
