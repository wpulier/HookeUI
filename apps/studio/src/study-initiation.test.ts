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


it('opens the exact returned report evidence and separates it from acceptance', async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const root=new TestElement('section'),open=vi.fn(async()=>undefined);
 const evidence={id:'receipt',session:'s1',revision:3,digest:'exact-digest'};
 const fetcher=vi.fn(async()=>new Response(JSON.stringify({basis:{id:'study',session:'s1',revision:4,digest:'basis'},plan:{...plan,status:'reviewed'},assignments:[{id:'assignment:a',objective:'Interpret a retained measurement',observedState:'completed',claimedBy:{byLabel:'Codex'},report:{statement:'271 bright regions, not biological cells',limitations:'Uncalibrated',evidence:[evidence]}}]})));
 mountStudyInitiation(root as unknown as HTMLElement,study,[],fetcher as unknown as typeof fetch,open);
 await vi.waitFor(()=>expect(root.textContent).toContain('Report awaiting review'));
 const button=root.querySelectorAll('button').find(b=>b.textContent==='Open evidence 1 · version 3') as unknown as HTMLButtonElement;
 button.onclick!(new Event('click') as PointerEvent);
 await vi.waitFor(()=>expect(open).toHaveBeenCalledWith(evidence,'Report evidence 1'));
 await vi.waitFor(()=>expect(root.textContent).not.toContain('Opening evidence…'));
 expect(root.textContent).not.toContain('Saving…');
 expect(root.textContent).toContain('Copy report context');
 expect(root.textContent).not.toContain('Copy agent handoff');
});


it('shows the refusal recovery instruction when saving fails',async()=>{
 const {root,fetcher}=setup();await vi.waitFor(()=>expect(root.querySelector('form')).not.toBeNull());
 const form=root.querySelector('form') as unknown as HTMLFormElement;form.checkValidity=()=>true;
 fetcher.mockImplementationOnce(async()=>new Response(JSON.stringify({ok:false,says:'The active Project changed.',next:'Reopen the original Study before saving.'}),{status:409}));
 const save=root.querySelectorAll('button').find(b=>b.textContent==='Save research plan') as unknown as HTMLButtonElement;
 save.onclick!(new Event('click') as PointerEvent);
 await vi.waitFor(()=>expect(root.textContent).toContain('Reopen the original Study before saving.'));
 expect(root.querySelector('textarea')!.value).toBe('Predictions');
});

it('retains conflicting local fields across reopen of a reviewed remote plan',async()=>{
 const {storage}=setup();await Promise.resolve();
 const key='hooke:research-plan:s1:study';
 storage.set(key,JSON.stringify({basis:'old',output:'Predictions',evaluation:'Measured error',method:'my method',mode:'connected-agent',limits:{experiments:'2',modelMinutes:'30',tokens:'60000'},packageId:'',base:{output:'Predictions',evaluation:'Measured error',method:'original',mode:'connected-agent',experiments:'2',modelMinutes:'30',tokens:'60000',packageId:''}}));
 const fetcher=vi.fn(async(url:string)=>new Response(JSON.stringify(url==='/api/research-instructions'?{packages:[]}:{basis:{id:'study',session:'s1',revision:3,digest:'remote'},plan:{...plan,methodNotes:'remote method',status:'reviewed'},assignments:[]})));
 for(let attempt=0;attempt<2;attempt++){
  const root=new TestElement('section');mountStudyInitiation(root as unknown as HTMLElement,study,[],fetcher as unknown as typeof fetch);
  await vi.waitFor(()=>expect(root.textContent).toContain('Your draft: my method'));
  expect(root.textContent).toContain('Saved: remote method');
  expect(JSON.parse(storage.get(key)!).method).toBe('my method');
  expect(root.querySelector('form')).not.toBeNull();
 }
});
