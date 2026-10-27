import { afterEach, expect, test, vi } from 'vitest';
import { bindPopulationNative } from './population-native';
import { TestElement, fire } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
const source = {id:'e2',session:'s1',revision:3,status:'saved'};
const operation = {id:'retain-population-labels-v1',implementation:'builtin',preparation:'none',input:'saved-population-revision',output:'native-project-arrays'};
const accepted = {id:'p2',session:'s1',revision:1,document:{title:'Retained labels',state:{populationSource:{source:{id:'e2',session:'s1',revision:3}},scientificView:{inputs:{image:{},labels:{}}}}}};
const settle = async () => {for(let i=0;i<20;i++)await Promise.resolve();};
function setup(){
  const host=new TestElement('div');
  vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag)});
  let current={...source};const open=vi.fn(async()=>{});
  const control=bindPopulationNative(host as unknown as HTMLElement,()=>current,open);control.sync();
  return {host,open,change:()=>{current={...current,revision:4};control.sync();}};
}
test('ordinary retention binds readiness and POST to selected revision, opens accepted native inputs only on request',async()=>{
  const {host,open}=setup();
  const fetch=vi.fn(async(_url:string,options?:RequestInit)=>({ok:true,json:async()=>options?.method==='POST'?{project:accepted}:{operation,source:{id:'e2',session:'s1',revision:3},readiness:{ready:true}}}));
  vi.stubGlobal('fetch',fetch);
  fire(host.querySelector('.population-native')!,'click');await settle();
  const body=JSON.parse(String(fetch.mock.calls[1][1]?.body));
  expect(body).toMatchObject({operation:operation.id,id:'e2',session:'s1',revision:3});
  expect(typeof body.request).toBe('string');expect(open).not.toHaveBeenCalled();
  fire(host.querySelector('.population-native-open')!,'click');await settle();
  expect(open).toHaveBeenCalledWith({id:'p2',session:'s1',revision:1,title:'Retained labels'});
});
test('superseded selection cannot present old retained inputs',async()=>{
  const {host,open,change}=setup();let finish!:(value:unknown)=>void;
  vi.stubGlobal('fetch',vi.fn(()=>new Promise(resolve=>{finish=resolve;})));
  fire(host.querySelector('.population-native')!,'click');change();
  finish({ok:true,json:async()=>({operation,source:{id:'e2',session:'s1',revision:3},readiness:{ready:true}})});await settle();
  expect(host.querySelector('.population-native-open')!.hidden).toBe(true);expect(open).not.toHaveBeenCalled();
});

test('population provenance reports frozen revision and limitations without starting a producer',async()=>{
  const {showPopulationProvenance}=await import('./population-native');
  const {host}=setup();
  showPopulationProvenance(host as unknown as HTMLElement,{populationSource:{format:1,
    source:{id:'e2',session:'s1',revision:3,execution:'execution-1',files:{'source.image':'a'.repeat(64)}},
    producer:{implementation:'fiji-analyze-particles'},caller:'studio',members:[{memberID:'c2',label:2}],
    corrections:[{r:3,change:'removed c1',by:'user'}],runtimeKind:'fiji'}});
  expect(host.textContent).toContain('e2 · revision 3');expect(host.textContent).toContain('fiji-analyze-particles');
  expect(host.textContent).toContain('removed c1');expect(host.textContent).toContain('mean array indices');
  const details=host.querySelector('details')!;
  expect(details).not.toBeNull();
  expect(details.getAttribute('open')).toBeNull();
  expect(details.querySelector('summary')!.textContent).toBe('Population history and provenance');
  expect(details.textContent).toContain('removed c1');
  expect(details.textContent).toContain('SHA-256');
  // An actionable input mismatch must remain visible outside the disclosure.
  expect(details.textContent).not.toContain('current inputs differ');
  expect(host.textContent).toContain('current inputs differ');
});

test('lost acknowledgement retries the same captured request and wrong output identity is not offered',async()=>{
  const {host}=setup();let attempts=0;const requests:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(_url:string,options?:RequestInit)=>{
    if(options?.method!=='POST')return {ok:true,json:async()=>({operation,source:{id:'e2',session:'s1',revision:3},readiness:{ready:true}})};
    requests.push(JSON.parse(String(options.body)).request);attempts++;
    if(attempts===1)throw new Error('connection closed after admission');
    return {ok:true,json:async()=>({project:{...accepted,session:'wrong'}})};
  }));
  fire(host.querySelector('.population-native')!,'click');await settle();
  fire(host.querySelector('.population-native')!,'click');await settle();
  expect(requests).toHaveLength(2);expect(requests[1]).toBe(requests[0]);
  expect(host.querySelector('.population-native-open')!.hidden).toBe(true);
  expect(host.textContent).toContain('do not match');
});

function formatTwoState(){
  const nativeInputs={image:{id:'p'+'2'.repeat(32),session:'s1',component:'array.image',revision:'a'.repeat(64)},
    labels:{id:'p'+'2'.repeat(32),session:'s1',component:'array.labels',revision:'b'.repeat(64)}};
  const captured={id:'e2',session:'s1',revision:3,execution:'execution-1',files:{'source.image':'c'.repeat(64),'masks.npz':'d'.repeat(64)}};
  const producer={implementation:'fiji-analyze-particles',runtime:{generation:7,receiptSha256:'e'.repeat(64)}};
  return {scientificView:{inputs:structuredClone(nativeInputs)},populationSource:{format:2,
    operation:'retain-population-labels-v1',source:captured,nativeInputs,
    association:{input:structuredClone(nativeInputs.image),output:structuredClone(nativeInputs.labels),
      source:structuredClone(captured),producer:structuredClone(producer)},
    producer,caller:'studio',members:[{memberID:'c2',label:2}],corrections:[{r:3,change:'removed c1',by:'user'}],runtimeKind:'fiji'}};
}

test('new native acceptance displays its explicit image-to-label association without inventing verified history',async()=>{
  const {showPopulationProvenance}=await import('./population-native');
  const {host}=setup();const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
  showPopulationProvenance(host as unknown as HTMLElement,formatTwoState());
  expect(host.textContent).not.toContain('unsupported');
  expect(host.textContent).toContain('e2 · revision 3 · producer fiji-analyze-particles');
  expect(host.textContent).toContain('array.image → array.labels');
  expect(host.textContent).toContain('Producing actions and inference timing are not recorded');
  expect(host.textContent).toContain('recorded association');
  expect(host.textContent).not.toContain('verified history');
  expect(host.textContent).not.toContain('current inputs differ');
  expect(host.querySelector('details')!.getAttribute('open')).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

test.each(['missing','input','source','producer'])('format2 %s association warns outside the disclosure without certifying it',async damage=>{
  const {showPopulationProvenance}=await import('./population-native');const {host}=setup();
  const state=formatTwoState(),provenance:Record<string,unknown>={...state.populationSource};
  const association=state.populationSource.association;
  if(damage==='missing')delete provenance.association;
  if(damage==='input')provenance.association={...association,input:{...association.input,revision:'f'.repeat(64)}};
  if(damage==='source')provenance.association={...association,source:{...association.source,revision:4}};
  if(damage==='producer')provenance.association={...association,producer:{implementation:'other-producer'}};
  showPopulationProvenance(host as unknown as HTMLElement,{...state,populationSource:provenance});
  expect(host.textContent).toContain('association is incomplete or inconsistent');
  expect(host.textContent).not.toContain('Explicit recorded association');
  expect(host.querySelector('details')!.textContent).not.toContain('association is incomplete');
  expect(host.textContent).toContain('e2 · revision 3 · producer fiji-analyze-particles');
});

test('later input changes remain a visible warning beside the original format2 record',async()=>{
  const {showPopulationProvenance}=await import('./population-native');const {host}=setup();
  const state=formatTwoState();state.scientificView.inputs.labels.revision='f'.repeat(64);
  showPopulationProvenance(host as unknown as HTMLElement,state);
  expect(host.textContent).toContain('current inputs differ');
  expect(host.querySelector('details')!.textContent).not.toContain('current inputs differ');
  expect(host.querySelector('details')!.textContent).toContain('Explicit recorded association');
});

test('legacy format1 remains attribution without inventing the new association',async()=>{
  const {showPopulationProvenance}=await import('./population-native');const {host}=setup();
  const state=formatTwoState();const {association:omitted,...legacy}=state.populationSource;void omitted;
  showPopulationProvenance(host as unknown as HTMLElement,{...state,populationSource:{...legacy,format:1}});
  expect(host.textContent).toContain('producer fiji-analyze-particles');
  expect(host.textContent).not.toContain('unsupported');
  expect(host.textContent).not.toContain('Explicit recorded association');
  expect(host.textContent).not.toContain('association is incomplete');
});
