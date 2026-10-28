import {afterEach,expect,it,vi} from 'vitest';
import {mountLocalStudyResearcher} from './study-local-researcher';
import type {Effort} from './research-efforts';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());
const study:Effort={id:'study',session:'s1',revision:1,digest:'d',objective:'Inspect retained evidence',criteria:'Measured results',
 status:'active',capMicrousd:1e6,spentMicrousd:0,heldMicrousd:0,availableMicrousd:1e6,inputs:[],tasks:{},findings:[]};

it('retries a lost launch reply with the same Study request and links the observed original researcher',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);const root=new TestElement('section');
 const posted:Record<string,unknown>[]=[];
 const fetcher=vi.fn(async(_url:string,init?:RequestInit)=>{
  if(init?.method==='POST'){
   posted.push(JSON.parse(String(init.body)));if(posted.length===1)throw new Error('Synthetic lost launch reply');
   return new Response(JSON.stringify({ok:true,researcher:'r-original',execution:{day:'s1'}}));
  }
  return new Response(JSON.stringify({ok:true,harness:{available:true,model:'fixture-model'},efforts:[{id:'quick',maximumUsd:.25}]}));
 });
 mountLocalStudyResearcher(root as unknown as HTMLElement,study,fetcher as unknown as typeof fetch);
 await vi.waitFor(()=>expect(root.querySelector('form')).not.toBeNull());
 const form=root.querySelector('form') as unknown as HTMLFormElement;
 root.querySelector('select')!.value='quick';
 const stopPropagation=vi.fn();form.oninput!({stopPropagation} as unknown as InputEvent);expect(stopPropagation).toHaveBeenCalledOnce();
 await form.onsubmit!(new Event('submit',{cancelable:true}) as SubmitEvent);
 expect(root.textContent).toContain('Retry keeps the same launch identity');expect(root.querySelector('a')).toBeNull();
 await form.onsubmit!(new Event('submit',{cancelable:true}) as SubmitEvent);
 expect(posted[1]).toEqual(posted[0]);expect(posted[0]).toMatchObject({session:'s1',brief:study.objective,effort:'quick'});
 const link=root.querySelector('a') as unknown as HTMLAnchorElement;
 expect(link.href).toBe('#research/r-original?day=s1');expect(root.textContent).toContain('Researcher started');
});

it('shows provider unavailability without exposing a launch button',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);const root=new TestElement('section');
 const fetcher=vi.fn(async()=>new Response(JSON.stringify({ok:true,harness:{available:false,reason:'The Project is local only.'},efforts:[]})));
 mountLocalStudyResearcher(root as unknown as HTMLElement,study,fetcher);
 await vi.waitFor(()=>expect(root.textContent).toContain('The Project is local only.'));
 expect(root.querySelector('form')).toBeNull();expect(fetcher).toHaveBeenCalledOnce();
});
