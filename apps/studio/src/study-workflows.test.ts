import {afterEach,expect,it,vi} from 'vitest';
import {mountStudyWorkflows} from './study-workflows';
import type {Effort,StudyInput} from './research-efforts';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());

it('binds each workflow port to an exact Project revision and preserves the request after a lost reply',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const root=new TestElement('section');
 const study:Effort={id:'study',session:'s1',revision:1,digest:'d',objective:'Compare',criteria:'Measured results',
  status:'active',capMicrousd:0,spentMicrousd:0,heldMicrousd:0,availableMicrousd:0,inputs:[],tasks:{},findings:[]};
 const inputs:StudyInput[]=[{id:'training',revision:3,digest:'train-digest',title:'Training effects',kind:'source-file',managedFileEligible:false},
  {id:'controls',revision:7,digest:'controls-digest',title:'Controls',kind:'source-file',managedFileEligible:false}];
 const posted:Record<string,unknown>[]=[];
 const fetcher=vi.fn(async (url:string,init?:RequestInit)=>{
  if(init?.method==='POST'){posted.push(JSON.parse(String(init.body)));if(posted.length===1)throw new Error('Synthetic lost response');return new Response(JSON.stringify({ok:true}));}
  return new Response(JSON.stringify(url==='/api/workflows'?{ok:true,workflows:[{id:'recipe',revision:4,name:'Predict',graph:{format:1,input:{kind:'source-file'},nodes:[
   {id:'predict',kind:'tool',name:'Predict',needs:['input:effects','input:controls'],settings:{}}]}}]}:
   {ok:true,operations:[{byLabel:'Researcher A',state:'interrupted',workflow:{id:'run1',status:'running',executionObservation:{state:'interrupted',reason:'No execution owner; reconcile before continuing.'},attempts:[]}}]}));
 });
 mountStudyWorkflows(root as unknown as HTMLElement,study,inputs,undefined,fetcher as unknown as typeof fetch);
 await vi.waitFor(()=>expect(root.querySelector('form')).not.toBeNull());
 const selection=root.querySelectorAll('select').find(n=>n.getAttribute('aria-label')==='Saved workflow')!;
 selection.value='recipe';(selection as unknown as HTMLSelectElement).onchange!(new Event('change'));
 const select=(label:string)=>root.querySelectorAll('select').find(n=>n.getAttribute('aria-label')===label)!;
 select('Exact Project input').value='training';select('effects input').value='training';select('controls input').value='controls';
 const form=root.querySelector('form') as unknown as HTMLFormElement;
 await form.onsubmit!(new Event('submit',{cancelable:true}) as SubmitEvent);
 expect(root.textContent).toContain('Retry preserves this request');
 await form.onsubmit!(new Event('submit',{cancelable:true}) as SubmitEvent);
 expect(posted[1]).toEqual(posted[0]);
 expect(posted[0].bindings).toEqual({effects:{id:'training',session:'s1',revision:3,digest:'train-digest'},controls:{id:'controls',session:'s1',revision:7,digest:'controls-digest'}});
 expect(root.textContent).toContain('No execution owner; reconcile before continuing.');
 const link=root.querySelectorAll('a').find(n=>n.textContent==='Open workflow run') as unknown as HTMLAnchorElement;
 expect(link.href).toBe('#workflows?run=run1');
});
