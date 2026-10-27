// Synthetic UI/HTTP transitions; native workflow qualification is separate.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import { comparisonChooser, showScientificComparison } from './scientific-comparison';
const source={id:'p'+'a'.repeat(32),day:'2026-10-19',version:2};
const input={id:source.id,session:source.day,revision:2,digest:'b'.repeat(64)};
const arms=['a','b'].map(id=>({id,capability:`mine/${id}#viewer`,name:`Tool ${id.toUpperCase()}`,owner:`mine/${id}`,generation:'c'.repeat(16),scene:null}));
const comparison=()=>({format:'scientific-comparison-v1',input,arms,retained:[]});
const group={id:'p'+'d'.repeat(32),day:source.day,version:1};
const fetcher=vi.fn<typeof fetch>(),open=vi.fn(async(_target:{id:string;version:number;title:string})=>{});
let root:TestElement;
const button=(text:string)=>root.querySelectorAll('button').find(b=>b.textContent===text)!;
const response=(data:object,status=200)=>new Response(JSON.stringify(data),{status});
async function settle(){for(let i=0;i<12;i++)await settleMicrotasks();}
beforeEach(()=>{vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',fetcher);fetcher.mockReset();open.mockClear();root=document.createElement('section') as unknown as TestElement;});
afterEach(()=>vi.unstubAllGlobals());

test('comparison captures one input and duplicate click does not create duplicate work',async()=>{
  comparisonChooser(root as unknown as HTMLElement,source,input.digest,arms.map(a=>({...a,key:a.capability})),open);
  for(const el of root.querySelectorAll('input'))(el as unknown as HTMLInputElement).checked=true;
  let resolve!:(v:Response)=>void;fetcher.mockImplementation(()=>new Promise(r=>{resolve=r;}));
  fire(button('Create comparison'),'click');fire(button('Create comparison'),'click');expect(fetcher).toHaveBeenCalledTimes(1);
  const body=JSON.parse(String(fetcher.mock.calls[0][1]?.body));expect(body).toMatchObject({session:source.day,revision:2,choices:arms.map(a=>a.capability)});
  resolve(response({ok:true,project:{id:group.id,session:group.day,revision:1,document:{kind:'scientific-comparison',title:'Compare input',state:{comparison:comparison()}}}}));await settle();
  expect(root.textContent).toContain('No analysis has started');expect(open).not.toHaveBeenCalled();
  fire(button('Open comparison'),'click');await settle();expect(open).toHaveBeenCalledWith({id:group.id,version:1,title:'Compare input'});
});

test('opening saved comparison never starts preparation or execution',()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:comparison()},group,open);
  expect(fetcher).not.toHaveBeenCalled();expect(open).not.toHaveBeenCalled();
  expect(root.querySelectorAll('a').map(a=>(a as unknown as HTMLAnchorElement).href)).toEqual(['#models/mine%2Fa','#models/mine%2Fb']);
  expect(root.textContent).toContain('never changes your future defaults');
});

test('failed preparation preserves comparison and offers its same scoped retry',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:comparison()},group,open);
  fetcher.mockResolvedValue(response({ok:false,says:'Package needs preparation.',next:'Open integration setup.'},409));
  const start=button('Create separate working input');fire(start,'click');await settle();
  expect(root.textContent).toContain('Package needs preparation');expect(start.disabled).toBe(false);
  fire(start,'click');await settle();expect(fetcher.mock.calls[0][1]?.body).toEqual(fetcher.mock.calls[1][1]?.body);
  expect(open).not.toHaveBeenCalled();expect(root.textContent).toContain('Tool B');
});

test('unknown comparison version refuses without showing executable actions',()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:{...comparison(),format:'future'}},group,open);
  expect(root.textContent).toContain('unsupported');expect(root.querySelectorAll('button')).toHaveLength(0);expect(fetcher).not.toHaveBeenCalled();
});


test('a saved result without tool state never implies retained configuration',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:comparison()},group,open);
  fetcher.mockResolvedValue(response({ok:true,results:[{arm:'a',result:input,title:'Output',retained:false,configuration:'not-retained'}]}));
  fire(button('Check saved results'),'click');await settle();
  expect(root.textContent).toContain('Producing settings were not retained');
  expect(root.textContent).not.toContain('Tool settings retained.');
  expect(button('Keep with its settings and timing')).toBeTruthy();
});


test('preparation receipt compares identity fields independently of JSON property order',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:comparison()},group,open);
  const reordered={digest:input.digest,revision:input.revision,session:input.session,id:input.id};
  fetcher.mockResolvedValue(response({ok:true,project:{id:group.id,session:group.day,revision:2,document:{kind:'scientific-comparison',title:'Compare input',state:{comparison:{...comparison(),input:reordered}}}}}));
  fire(button('Create separate working input'),'click');await settle();
  expect(root.textContent).toContain('Open the updated comparison');
  expect(root.textContent).not.toContain('changed unexpectedly');
  expect(open).not.toHaveBeenCalled();
});

test('an action is answered beside the button pressed, not below every alternative (F-L8-7)',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:comparison()},group,open);
  fetcher.mockResolvedValue(response({ok:true,project:{id:group.id,session:group.day,revision:2,document:{kind:'scientific-comparison',title:'Compare input',state:{comparison:comparison()}}}}));
  const pressed=root.querySelectorAll('button').filter(b=>b.textContent==='Create separate working input')[1];
  fire(pressed,'click');await settle();
  const arm=pressed.parentElement!,at=arm.children.indexOf(pressed);
  expect(arm.className).toBe('scientific-comparison-arm');
  expect(arm.children[at+1].textContent).toBe('Working input saved. Open the updated comparison to continue.');
  expect(arm.children[at+1].getAttribute('role')).toBe('status');
  expect(arm.children[at+2].textContent).toBe('Open updated comparison');
  expect(root.querySelectorAll('button').filter(b=>b.textContent==='Open updated comparison')).toHaveLength(1);
  fire(arm.children[at+2],'click');await settle();expect(open).toHaveBeenCalledWith({id:group.id,version:2,title:'Compare input'});
});

test('task filtering preserves explicitly selected alternatives',()=>{
 comparisonChooser(root as unknown as HTMLElement,source,input.digest,arms.map((a,i)=>({...a,key:a.capability,category:i?'Present/Animation':'Analyze/Segmentation'})),open);
 const inputs=root.querySelectorAll('input');const search=inputs[0];const selected=inputs[1] as unknown as HTMLInputElement;selected.checked=true;
 search.value='animation';fire(search,'input');expect(root.querySelectorAll('label').every(row=>!row.hidden)).toBe(true);
 selected.checked=false;fire(search,'input');expect(root.querySelectorAll('label')[0].hidden).toBe(true);expect(root.querySelectorAll('label')[1].hidden).toBe(false);
});


const v2=(attempts:object[],chosen:object|null=null,scene=true)=>({format:'scientific-comparison-v2',input,
  arms:['a','b','c'].map(id=>({id,capability:'mine/cp#segment',name:'Cellpose',owner:'mine/cp',generation:'c'.repeat(16),scene:scene&&id==='a'?{...input,id:'p'+'e'.repeat(32),revision:1}:null})),
  retained:[],attempts,chosen});
const result={id:'p'+'f'.repeat(32),session:source.day,revision:1,digest:'1'.repeat(64)};
const success={arm:'a',outcome:'succeeded',source:'saved-result',at:1792467950,model:{capability:'mine/cp#segment',generation:'c'.repeat(16),name:'Cellpose',device:{declared:'cpu',by:'integration manifest'}},
  settings:{schema:'hooke.cellposesam.2d.settings',version:1,values:{diameter:'30',flow_threshold:0.4},captured:'panel-at-save-tool'},
  outputs:[{name:'masks',type:'labels',shape:[128,128],meaning:{semantic:'instance-labels'}}],result,
  timing:{savedAt:1792467950,computeSeconds:null,computeSays:'The tool does not report its own compute time.',controls:2,controlToSaveSeconds:64,lastControlAt:1792467886,firstControlAt:1792467800,sessionSeconds:150},says:''};
const failure={arm:'c',outcome:'failed',source:'preparation',at:1792467960,model:{device:{declared:null,by:'not declared'}},settings:null,outputs:[],result:null,timing:{preparationSeconds:0.4},says:'The integration needs preparation'};
const cancelled={arm:'b',outcome:'cancelled',source:'researcher',at:1792467970,model:{},settings:null,outputs:[],result:null,timing:{request:'1'.repeat(32)},says:'Stopped during segmentation'};

test('each arm shows its own outcome, settings, declared device, outputs and timing; failures stay beside success',()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:v2([success,failure,cancelled])},group,open);
  const text=root.textContent;
  expect(text).toContain('Succeeded · saved result kept');expect(text).toContain('Panel settings when Save tool ran: diameter 30, flow_threshold 0.4');
  expect(text).toContain('Device: the CPU of this Mac, as the integration declares it');expect(text).toContain('masks (labels 128×128 · instance-labels)');
  expect(text).toContain('64 s from the last plugin-panel action to Save tool (that action may be the Run click or a later change, so this is not the run time)');expect(text).not.toContain('run time plus');expect(text).toContain('does not report its own compute time');
  expect(text).toContain('Failed while preparing its working input: The integration needs preparation');
  expect(text).toContain('Cancelled, recorded by you: Stopped during segmentation');expect(text).toContain('One attempt kept below, newest first.');
  expect(text).not.toContain('compute 64');expect(fetcher).not.toHaveBeenCalled();
});

test('a preparation failure is said as recorded and leaves the other arms alone',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:v2([success])},group,open);
  fetcher.mockResolvedValue(response({ok:true,project:{id:group.id,session:group.day,revision:2,document:{kind:'scientific-comparison',title:'Compare input',state:{comparison:v2([success,failure])}}}}));
  fire(root.querySelectorAll('button').filter(b=>b.textContent==='Create separate working input')[1],'click');await settle();
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toMatchObject({action:'prepare',arm:'c'});
  expect(root.textContent).toContain('C failed while preparing: The integration needs preparation This is recorded beside the other alternatives');
  expect(button('Open updated comparison')).toBeTruthy();
});

test('choosing a preferred result needs a confirmation and says nothing else changes',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:v2([success])},group,open);
  fire(button('Choose as preferred result'),'click');expect(fetcher).not.toHaveBeenCalled();
  expect(root.textContent).toContain('future runs keep their own defaults');
  fetcher.mockResolvedValue(response({ok:true,project:{id:group.id,session:group.day,revision:2,document:{kind:'scientific-comparison',title:'Compare input',state:{comparison:v2([success],{arm:'a',result,at:1792468000})}}}}));
  fire(button('Confirm A as preferred'),'click');await settle();
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({session:group.day,revision:1,action:'choose',arm:'a',result:result.id,resultRevision:1});
  root=document.createElement('section') as unknown as TestElement;
  showScientificComparison(root as unknown as HTMLElement,{comparison:v2([success],{arm:'a',result,at:1792468000})},group,open);
  expect(root.textContent).toContain('Preferred: A, chosen by you');expect(button('Clear preferred result')).toBeTruthy();
});

test('recording a failure keeps one request identity across retries',async()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:v2([])},group,open);
  fetcher.mockResolvedValue(response({ok:false,says:'Busy.',next:''},503));
  const save=root.querySelectorAll('button').filter(b=>b.textContent==='Record for this alternative')[1];
  fire(save,'click');await settle();fire(save,'click');await settle();
  const [first,second]=fetcher.mock.calls.map(c=>JSON.parse(String(c[1]?.body)));
  expect(first).toMatchObject({action:'settle',arm:'b',outcome:'failed'});expect(second.request).toBe(first.request);
});

test('one tool can fill several arms from the chooser',async()=>{
  comparisonChooser(root as unknown as HTMLElement,source,input.digest,[{key:'mine/cp#segment',name:'Cellpose',generation:'c'.repeat(16)}],open);
  const check=root.querySelectorAll('input')[1];(check as unknown as HTMLInputElement).checked=true;fire(check,'change');
  (root.querySelectorAll('select')[0] as unknown as HTMLSelectElement).value='3';
  fetcher.mockResolvedValue(response({ok:false,says:'stop here'},400));
  fire(button('Create comparison'),'click');await settle();
  expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body)).choices).toEqual(Array(3).fill('mine/cp#segment'));
});

test('a v1 comparison still opens, with its retained result',()=>{
  showScientificComparison(root as unknown as HTMLElement,{comparison:{...comparison(),retained:[{arm:'a',result}]}},group,open);
  expect(button('Open retained A result · revision 1')).toBeTruthy();
});

test('a declared device is said in words',()=>{
  const cloud={...success,model:{...success.model,device:{declared:'this-mac',by:'integration manifest'}}};
  showScientificComparison(root as unknown as HTMLElement,{comparison:v2([cloud])},group,open);
  expect(root.textContent).toContain('Device: this Mac, as the integration declares it');expect(root.textContent).not.toContain('THIS-MAC');
});
