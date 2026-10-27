import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {TestElement,fire,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';
import {showMethodComparison} from './method-comparison';
const source={id:'p'+'a'.repeat(32),day:'original',version:1};
const fetcher=vi.fn<typeof fetch>(),open=vi.fn(async(_target:{id:string;version:number;title:string})=>{});
let root:TestElement,current:boolean,abort:AbortController;
const response=(body:object,status=200)=>new Response(JSON.stringify(body),{status});
const record=()=>({ok:true,source:{id:source.id,session:source.day,revision:1},operation:{id:'compare-retained-methods-v1'},
  evidence:{format:'hooke-method-evidence-v1',actor:'studio',chosen:null,evidenceComplete:false,source:{id:source.id,revision:2,revisionDigest:'b'.repeat(64)},
    arms:[{id:'a',factor:'boundary-definition',says:'Interior',status:'completed',limitations:['Producing actions unrecorded'],histories:[],lineage:[],runtimes:[],declaration:{},timing:{seconds:null,scope:'recorded actions and cells; not model inference timing'}},
          {id:'b',factor:'algorithm',says:'Unfinished alternative',status:'failed',limitations:['No accepted mask'],histories:[],lineage:[],runtimes:[],declaration:{},outcome:{says:'Stopped before result'},timing:{seconds:null,scope:'recorded actions and cells; not model inference timing'}}]},
  summary:{ranked:false,arms:[{id:'a',pixels:2,area:12,unit:'µm²'},{id:'b',pixels:null,area:null,unit:'µm²'}],differences:[{arm:'b',absolute:null,relative:null,unit:'µm²'}],disagreement:[{arms:['a','b'],onlyA:null,onlyB:null,unit:'µm²'}],says:'Agreement is not accuracy'}});
async function settle(){for(let i=0;i<10;i++)await settleMicrotasks();}
beforeEach(()=>{vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',fetcher);fetcher.mockReset();open.mockClear();current=true;abort=new AbortController();root=document.createElement('section') as unknown as TestElement;});
afterEach(()=>vi.unstubAllGlobals());
test('reopen shows unavailable failed arm and real limitations, with explicit rationale and exact bundle',async()=>{
  fetcher.mockResolvedValueOnce(response(record()));
  await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  expect(root.textContent).toContain('12 µm²');expect(root.textContent).toContain('Unavailable');
  expect(root.textContent).toContain('Producing actions unrecorded');expect(root.textContent).toContain('not model inference timing');
  expect(root.querySelectorAll('button').filter(b=>b.textContent==='Choose B')).toHaveLength(0);
  const choose=root.querySelectorAll('button').find(b=>b.textContent==='Choose A')!;
  expect(choose.disabled).toBe(true);
  const reason=root.querySelectorAll('textarea')[0];reason.value='Matches the experimental question';fire(reason,'input');
  expect(choose.disabled).toBe(false);
  current=false;fire(choose,'click');await settle();expect(fetcher).toHaveBeenCalledTimes(1);
  expect(root.querySelectorAll('a').find(a=>a.textContent==='Export comparison and native evidence')?.getAttribute('href')).toContain('revision=1');
});

test('chooser captures exact source and mask revisions, preserves the same request after refusal, and never runs a producer',async()=>{
  const {methodChooser}=await import('./method-comparison');
  // GET /api/resources returns its session index directly, without a scientific-result ok flag.
  const listing={day:source.day,entries:[{id:source.id,day:source.day,kind:'project',title:'Accepted image and labels',version:2}],unreadable:[]};
  const inventory=(revision:number)=>({ok:true,source:{id:source.id,session:source.day,revision,digest:'b'.repeat(64)},arrays:[{key:'image',name:'Image',semantic:'intensity',reference:{component:'array.image'}},{key:'labels',name:'Labels',semantic:'instance-labels',reference:{component:'array.labels'}}]});
  fetcher.mockResolvedValueOnce(response(listing)).mockResolvedValueOnce(response(inventory(1)))
    .mockResolvedValueOnce(response(inventory(1))).mockResolvedValueOnce(response(inventory(1)))
    .mockResolvedValueOnce(response({ok:true,operation:{id:'compare-retained-methods-v1'},readiness:{available:true,limits:'Exact input grid only'},evidence:{source:{kind:'array',session:source.day,id:source.id,revision:1,component:'array.image',revisionDigest:'b'.repeat(64)},arms:['a','b'].map(id=>({id,factor:'boundary-definition',says:`Method ${id.toUpperCase()}`,status:'completed',mask:{kind:'array',session:source.day,id:source.id,revision:1,component:'array.labels',revisionDigest:'b'.repeat(64)},limitations:id==='a'?['Missing history']:[]}))}}))
    .mockResolvedValueOnce(response({ok:false,says:'Publication interrupted',next:'Retry same request'},503))
    .mockResolvedValueOnce(response({ok:false,says:'Publication interrupted',next:'Retry same request'},503));
  methodChooser(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  const b=(text:string)=>root.querySelectorAll('button').find(el=>el.textContent===text)!;
  fire(b('Compare saved methods'),'click');await settle();
  expect(root.textContent).toContain('Retain calculates counts and differences from saved masks; no producing method is rerun.');
  for(const letter of ['A','B']){const method=root.querySelectorAll('input').find(el=>el.getAttribute('aria-label')===`Arm ${letter} method`)!;method.value=`Method ${letter}`;fire(method,'input');fire(b(`Load arm ${letter} arrays`),'click');await settle();}
  fire(b('Review comparison'),'click');await settle();expect(root.textContent).toContain('Missing history');
  expect(b('Retain comparison').disabled).toBe(false);
  fire(b('Retain comparison'),'click');await settle();expect(root.textContent).toContain('Publication interrupted');
  fire(b('Retain comparison'),'click');await settle();
  const bodies=fetcher.mock.calls.filter(([,args])=>args?.method==='POST').map(([,args])=>JSON.parse(String(args?.body)));
  expect(bodies.map(v=>v.action)).toEqual(['describe','retain','retain']);
  expect(bodies[1]).toEqual(bodies[2]);
  expect(bodies[1].selection.source).toEqual({kind:'array',session:source.day,id:source.id,revision:1,component:'array.image',digest:'b'.repeat(64)});
  expect(bodies[1].selection.arms.map((a:{mask:{revision:number}})=>a.mask.revision)).toEqual([1,1]);
  expect(open).not.toHaveBeenCalled();
});

const resourceListing=()=>({day:source.day,entries:[{id:source.id,day:source.day,kind:'project',title:'Accepted image and labels',version:2}],unreadable:[] as string[]});
async function startChooser(){
  const {methodChooser}=await import('./method-comparison');
  methodChooser(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  fire(root.querySelectorAll('button').find(b=>b.textContent==='Compare saved methods')!,'click');
  await settle();
}
test.each([
  ['wrong Project list',{...resourceListing(),day:'another'}],
  ['missing entries',{day:source.day,unreadable:[]}],
  ['missing unreadable state',{day:source.day,entries:[]}],
  ['malformed unreadable state',{...resourceListing(),unreadable:[null]}],
  ['wrong document owner',{...resourceListing(),entries:[{...resourceListing().entries[0],day:'another'}]}],
  ['wrong resource kind',{...resourceListing(),entries:[{...resourceListing().entries[0],kind:'result'}]}],
  ['missing document identity',{...resourceListing(),entries:[{...resourceListing().entries[0],id:'unverified'}]}],
  ['missing accepted revision',{...resourceListing(),entries:[{...resourceListing().entries[0],version:null}]}],
])('chooser refuses %s before reading arrays',async(_name,listing)=>{
  fetcher.mockResolvedValueOnce(response(listing));
  await startChooser();
  expect(root.textContent).toContain('Reopen Data and retry.');
  expect(root.querySelectorAll('select')).toHaveLength(0);
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(open).not.toHaveBeenCalled();
});

test('resource-list HTTP error uses the endpoint error and permits explicit retry',async()=>{
  fetcher.mockResolvedValueOnce(response({error:'day is a session id'},400));
  await startChooser();
  expect(root.textContent).toContain('day is a session id');
  expect(root.textContent).not.toContain('Comparison did not finish');
  expect(root.querySelectorAll('select')).toHaveLength(0);
  expect(root.querySelectorAll('button').find(b=>b.textContent==='Compare saved methods')?.disabled).toBe(false);
});

test('readable method choices retain the resource index warning about unreadable data',async()=>{
  fetcher.mockResolvedValueOnce(response({...resourceListing(),unreadable:['scientific project unavailable']}))
    .mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:1,digest:'b'.repeat(64)},arrays:[{name:'Image',semantic:'intensity',reference:{component:'array.image'}}]}));
  await startChooser();
  expect(root.textContent).toContain('Some saved data could not be read: scientific project unavailable');
  expect(root.textContent).toContain('only readable documents');
  expect(root.querySelectorAll('select').find(s=>s.getAttribute('aria-label')==='Arm A document')?.textContent).toContain('Accepted image and labels · revision 2');
});

test('accepting the resource index never relaxes the scientific array success contract',async()=>{
  fetcher.mockResolvedValueOnce(response(resourceListing()))
    .mockResolvedValueOnce(response({source:{id:source.id,session:source.day,revision:1,digest:'b'.repeat(64)},arrays:[{name:'Image',semantic:'intensity',reference:{component:'array.image'}}]}));
  await startChooser();
  expect(root.textContent).toContain('Comparison did not finish');
  expect(root.querySelectorAll('select')).toHaveLength(0);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

const chosenRecord=()=>({...record(),evidence:{...record().evidence,chosen:{arm:'a',rationale:'Keep accepted boundaries',by:'studio'},arms:[{...record().evidence.arms[0],mask:{kind:'array',session:source.day,id:'p'+'c'.repeat(32),revision:3,component:'array.corrected',revisionDigest:'d'.repeat(64)}},record().evidence.arms[1]]}});
const chosenArrays=()=>({ok:true,source:{id:'p'+'c'.repeat(32),session:source.day,revision:3,digest:'d'.repeat(64)},arrays:[{name:'Corrected cells',semantic:'instance-labels',reference:{component:'array.corrected'}},{name:'Other labels',semantic:'instance-labels',reference:{component:'array.other'}}]});
test('chosen result opens its exact accepted mask and component without running or taking the head',async()=>{
  fetcher.mockResolvedValueOnce(response(chosenRecord())).mockResolvedValueOnce(response(chosenArrays()));
  await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  const go=root.querySelectorAll('button').find(b=>b.textContent==='Continue with chosen result');expect(go).toBeDefined();
  fire(go!,'click');fire(go!,'click');await settle();
  expect(open).toHaveBeenCalledExactlyOnceWith({id:'p'+'c'.repeat(32),version:3,title:'Corrected cells',selection:{component:'array.corrected',digest:'d'.repeat(64),name:'Corrected cells'}});
  expect(fetcher.mock.calls.map(([url])=>String(url))).toEqual([`/api/scientific-methods/${source.id}?session=original&revision=1`,`/api/scientific-projects/p${'c'.repeat(32)}/arrays?session=original&revision=3`]);
});

test.each(['digest','component','semantic','duplicate'])('chosen result refuses a changed %s instead of opening another mask',async(kind)=>{
  const inventory=chosenArrays();
  if(kind==='digest')inventory.source.digest='e'.repeat(64);
  if(kind==='component')inventory.arrays[0].reference.component='array.changed';
  if(kind==='semantic')inventory.arrays[0].semantic='intensity';
  if(kind==='duplicate')inventory.arrays.push(inventory.arrays[0]);
  fetcher.mockResolvedValueOnce(response(chosenRecord())).mockResolvedValueOnce(response(inventory));
  await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  fire(root.querySelectorAll('button').find(b=>b.textContent==='Continue with chosen result')!,'click');await settle();
  expect(open).not.toHaveBeenCalled();expect(root.textContent).toContain('chosen labels or their saved revision changed');
});
test.each(['foreign','failed','duplicate','missing digest'])('chosen result refuses %s retained identity',async(kind)=>{
  const held=chosenRecord();
  if(kind==='foreign'&&'mask' in held.evidence.arms[0])held.evidence.arms[0].mask.session='another';
  if(kind==='failed')held.evidence.arms[0].status='failed';
  if(kind==='duplicate')held.evidence.arms.push(held.evidence.arms[0]);
  if(kind==='missing digest'&&'mask' in held.evidence.arms[0])held.evidence.arms[0].mask.revisionDigest='';
  fetcher.mockResolvedValueOnce(response(held));
  await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  expect(root.querySelectorAll('button').find(b=>b.textContent==='Continue with chosen result')).toBeUndefined();expect(open).not.toHaveBeenCalled();
});
test.each(['switch','abort'])('late chosen array read cannot open after owner %s',async(kind)=>{
  let resolve!:(r:Response)=>void;const held=new Promise<Response>(done=>{resolve=done;});
  fetcher.mockResolvedValueOnce(response(chosenRecord())).mockReturnValueOnce(held);
  await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  fire(root.querySelectorAll('button').find(b=>b.textContent==='Continue with chosen result')!,'click');await settle();
  if(kind==='switch')current=false;else abort.abort();
  resolve(response(chosenArrays()));await settle();expect(open).not.toHaveBeenCalled();
});
test('chosen native component accepts the shared store uppercase and punctuation identity grammar',async()=>{
  const held=chosenRecord(),inventory=chosenArrays();
  if('mask' in held.evidence.arms[0])held.evidence.arms[0].mask.component='array.E-saved.1';
  inventory.arrays[0].reference.component='array.E-saved.1';
  fetcher.mockResolvedValueOnce(response(held)).mockResolvedValueOnce(response(inventory));
  await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
  fire(root.querySelectorAll('button').find(b=>b.textContent==='Continue with chosen result')!,'click');await settle();
  expect(open.mock.calls[0]?.[0]).toMatchObject({version:3,selection:{component:'array.E-saved.1'}});
});


test('baseline warning describes only the comparison interval without rewriting retained evidence',async()=>{
 const kept=record();kept.evidence.arms[0].limitations=['No producing actions were recorded for this arm'];
 const original=JSON.stringify(kept);
 fetcher.mockResolvedValueOnce(response(kept));
 await showMethodComparison(root as unknown as HTMLElement,source,open,{signal:abort.signal,current:()=>current});
 expect(root.textContent).toContain('No additional producing actions were recorded between the frozen input and this arm. Earlier source provenance is outside this comparison interval.');
 expect(root.textContent).toContain('Evidence is incomplete');expect(root.textContent).toContain('Timing unavailable');
 expect(root.textContent).toContain('No accepted mask');
 expect(JSON.stringify(kept)).toBe(original);expect(open).not.toHaveBeenCalled();expect(fetcher).toHaveBeenCalledTimes(1);
});
