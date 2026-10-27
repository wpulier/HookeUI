// Synthetic UI transitions; backend tests exercise accepted arrays and publication.
import {beforeEach,afterEach,expect,test,vi} from 'vitest';
import {TestElement,fire,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';
import {scientificResourceActions} from './scientific-resource-actions';
const source={id:'p'+'a'.repeat(32),day:'original',version:2},digest='c'.repeat(64);
const response=(body:object,status=200)=>new Response(JSON.stringify(body),{status});
const fetcher=vi.fn<typeof fetch>(),open=vi.fn(async(_revision:number)=>{});
let root:TestElement,current:boolean,abort:AbortController;
const button=(label:string)=>root.querySelectorAll('button').find(row=>row.textContent===label)!;
const pixelPlan=()=>({format:'scientific-object-measurement-plan-v1',available:true,reason:'',
  method:'Count every array element with the same label as one object. Background 0 is excluded.',
  calibrationReason:'the labels have no calibration, so their size is a count of pixels',
  columns:[{name:'object_id',description:'Label value of the object'},
    {name:'voxel_count',unit:'pixel',description:'Number of array elements with this label'},
    {name:'centroid_y_px',unit:'pixel',description:'Mean array index along y; no calibration applied'},
    {name:'centroid_x_px',unit:'pixel',description:'Mean array index along x; no calibration applied'}]});
const operation={id:'scientific-object-measurements-v2',implementation:'builtin',input:{kind:'array',semantic:'instance-labels',revision:'exact-accepted'},output:{kind:'table',semantic:'object-table',publication:'project-revision'},preparation:'none'};
const discovery=(measurementPlan:unknown=pixelPlan(),canMeasure=true)=>response({ok:true,operation,
  source:{id:source.id,session:source.day,revision:2,digest},arrays:[{key:'labels',name:'Corrected cells',
    shape:[2,3],dtype:'>u8',semantic:'instance-labels',canMeasure,measurementPlan}]});
const accepted=()=>response({ok:true,project:{id:source.id,session:source.day,revision:3,document:{state:{scientificMeasurements:{
  format:'scientific-object-measurements-v1',source:{id:source.id,session:source.day,revision:2,digest,input:'labels'},
  columns:['object_id','voxel_count','centroid_y_px','centroid_x_px'],rows:[['9223372036854775815',2,0,1.5]],units:'array-index pixels'}}}}});
const noResources=()=>response({ok:true,source:{id:source.id,session:source.day,revision:2,digest},resources:[]});
async function settle(){for(let i=0;i<12;i++)await settleMicrotasks();}
beforeEach(()=>{
  Reflect.set(TestElement.prototype,'focus',vi.fn());
  vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',fetcher);fetcher.mockReset();open.mockClear();current=true;abort=new AbortController();
  const host=document.createElement('section');root=host as unknown as TestElement;
  scientificResourceActions(host,source,{},open,{signal:abort.signal,current:()=>current});
});
afterEach(()=>{Reflect.deleteProperty(TestElement.prototype,'focus');vi.unstubAllGlobals();});
test('measurement captures saved input, preserves exact large IDs and opens only on explicit request',async()=>{
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(noResources()).mockResolvedValueOnce(accepted());
  fire(button('Choose saved data'),'click');await settle();
  fire(button('Measure objects'),'click');fire(button('Measure objects'),'click');await settle();
  expect(fetcher).toHaveBeenCalledTimes(3);
  expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({operation:operation.id,session:'original',revision:2,input:'labels'});
  expect(root.textContent).toContain('9223372036854775815');expect(open).not.toHaveBeenCalled();
  current=false;fire(button('Open saved measurements'),'click');expect(open).not.toHaveBeenCalled();
  current=true;fire(button('Open saved measurements'),'click');await settle();expect(open).toHaveBeenCalledWith(3);
});
test('conflict keeps selected source and retry never takes a newer input',async()=>{
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(noResources()).mockResolvedValueOnce(response({ok:false,says:'Another revision was saved.'},409)).mockResolvedValueOnce(accepted());
  fire(button('Choose saved data'),'click');await settle();fire(button('Measure objects'),'click');await settle();
  expect(root.textContent).toContain('Another revision');expect(open).not.toHaveBeenCalled();
  fire(button('Measure objects'),'click');await settle();expect(fetcher.mock.calls[3][1]?.body).toBe(fetcher.mock.calls[2][1]?.body);
});
test('changed Project blocks measurement admission and exports retain the captured revision',async()=>{
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(noResources());fire(button('Choose saved data'),'click');await settle();current=false;
  fire(button('Measure objects'),'click');await settle();expect(fetcher).toHaveBeenCalledTimes(2);
  const link=root.querySelectorAll('a').find(el=>el.textContent==='Export native data + metadata')!;
  expect(Reflect.get(link,'href')).toContain('session=original&revision=2&input=labels');
});

test('reopened measurements identify a changed labels reference',()=>{
  const host=document.createElement('section');
  scientificResourceActions(host,source,{scientificView:{inputs:{labels:{revision:'new'}}},scientificMeasurements:{
    format:'scientific-object-measurements-v1',source:{id:source.id,session:source.day,revision:1,input:'labels',reference:{revision:'old'}},
    columns:['object_id','voxel_count','centroid_y_px','centroid_x_px'],rows:[['7',2,1,1]],units:'array-index pixels',
  }},open,{signal:abort.signal,current:()=>true});
  expect(host.textContent).toContain('current labels have changed');
  expect(host.textContent).toContain('7');
});

test('typed measurement tables are read a page at a time from the saved revision',async()=>{
  const host=document.createElement('section');
  fetcher.mockResolvedValueOnce(response({ok:true,offset:0,rows:[['9223372036854775815',2,0,1.5],[7,1,null,0]],table:{name:'objects',rows:250,
    columns:[{name:'object_id',unit:null},{name:'voxel_count',unit:'pixel'},{name:'centroid_y_px',unit:'pixel'},{name:'centroid_x_px',unit:'pixel'}]}}));
  scientificResourceActions(host,source,{scientificView:{inputs:{labels:{revision:'r'}}},scientificMeasurements:{
    format:'scientific-object-measurements-v2',table:'objects',source:{id:source.id,session:source.day,revision:1,input:'labels',reference:{revision:'r'}},
  }},open,{signal:abort.signal,current:()=>true});
  await settle();
  expect(String(fetcher.mock.calls[0][0])).toContain('/tables/objects?session=original&revision=2&offset=0&limit=100');
  expect(host.textContent).toContain('250 measured objects');expect(host.textContent).toContain('9223372036854775815');
  expect(host.textContent).toContain('Y (px)');expect(host.textContent).toContain('—');
  expect(host.textContent).toContain('Showing the first 2 objects');expect(host.textContent).not.toContain('current labels have changed');
});
test('an unreadable typed table says so and keeps the saved revision',async()=>{
  const host=document.createElement('section');
  fetcher.mockResolvedValueOnce(response({ok:false,says:'Column area of table objects disagrees with its retained values'},400));
  scientificResourceActions(host,source,{scientificMeasurements:{format:'scientific-object-measurements-v2',table:'objects',
    source:{id:source.id,session:source.day,revision:1,input:'labels'}}},open,{signal:abort.signal,current:()=>true});
  await settle();
  expect(host.textContent).toContain('disagrees with its retained values');expect(host.textContent).toContain('saved revision is unchanged');
});

test('saved data lists tables, embeddings and files by family and offers artifact downloads',async()=>{
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:2,digest},resources:[
    {family:'labels',name:'Cells',readable:true},
    {family:'table',name:'objects',readable:true,semantic:'object-table',rows:3,columns:4,key:'object_id'},
    {family:'embeddings',name:'shape_features',readable:true,rows:3,dims:16,table:'objects'},
    {family:'artifact',name:'model',readable:true,semantic:'trained-artifact',size:12,mediaType:'application/octet-stream'},
    {family:'artifact',name:'movie',readable:false,reason:'Artifact movie disagrees with its retained file'}]}));
  fire(button('Choose saved data'),'click');await settle();
  expect(String(fetcher.mock.calls[1][0])).toContain('/resources?session=original&revision=2');
  expect(root.textContent).toContain('objects · object-table · 3 rows · 4 columns');
  expect(root.textContent).toContain('3 × 16 features, keyed to objects');
  expect(root.textContent).toContain('model · trained artifact · 12 bytes');
  expect(root.textContent).toContain('movie · cannot be read: Artifact movie disagrees');
  expect(root.textContent).not.toContain('Cells ·');
  const links=root.querySelectorAll('a').filter(el=>el.textContent==='Download');
  expect(links.length).toBe(1);expect(Reflect.get(links[0],'href')).toContain('/artifacts/model?session=original&revision=2');
});


test('saved input previews the measurement method, pixel units and calibration limitation before execution',async()=>{
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(noResources());
  fire(button('Choose saved data'),'click');await settle();
  expect(root.textContent).toContain('same label as one object');
  expect(root.textContent).toContain('Background 0 is excluded');
  expect(root.textContent).toContain('no calibration, so their size is a count of pixels');
  expect(root.textContent).toContain('Y (px)');expect(root.textContent).toContain('Pixels');
  expect(root.textContent).toContain('Mean array index along y');
  expect(fetcher).toHaveBeenCalledTimes(2);expect(button('Measure objects')).toBeDefined();
});

test('calibrated preview adds physical size while keeping counts and centroids in pixels',async()=>{
  const plan=pixelPlan();plan.calibrationReason='';
  plan.columns.push({name:'area_um2',unit:'µm²',description:"Object size from its element count and the labels' own axis-aligned calibration (0.125 µm² per element)"});
  fetcher.mockResolvedValueOnce(discovery(plan)).mockResolvedValueOnce(noResources());
  fire(button('Choose saved data'),'click');await settle();
  expect(root.textContent).toContain('area_um2 (µm²)');expect(root.textContent).toContain('0.125 µm² per element');
  expect(root.textContent).toContain('Pixels');expect(root.textContent).not.toContain('Voxels');
  expect(root.textContent).toContain('Mean array index along y; no calibration applied');
});

test('unsupported input explains recovery and preserves native export without offering measurement',async()=>{
  const plan={...pixelPlan(),available:false,reason:'Time and channel arrays need an explicit selection; class labels need an explicit conversion',columns:[]};
  fetcher.mockResolvedValueOnce(discovery(plan,false)).mockResolvedValueOnce(noResources());
  fire(button('Choose saved data'),'click');await settle();
  expect(root.textContent).toContain('Time and channel arrays need an explicit selection');
  expect(button('Measure objects')).toBeUndefined();
  expect(root.querySelectorAll('a').some(el=>el.textContent==='Export native data + metadata')).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(2);
});

test.each([undefined,{...pixelPlan(),available:false},{...pixelPlan(),format:'future'},
  {...pixelPlan(),columns:[{name:'voxel_count',description:42}]}])('missing or inconsistent plan refuses measurement without inventing units: %j',async(plan)=>{
  // Explicit undefined omits the plan rather than applying discovery's default.
  const result=await discovery().json();result.arrays[0].measurementPlan=plan;
  fetcher.mockResolvedValueOnce(response(result)).mockResolvedValueOnce(noResources());
  fire(button('Choose saved data'),'click');await settle();
  expect(root.textContent).toContain('Measurement details are unavailable');
  expect(button('Measure objects')).toBeUndefined();
  expect(root.querySelectorAll('a').some(el=>el.textContent==='Export native data + metadata')).toBe(true);
});

test('reopened calibrated table describes retained columns without a false uncalibrated caption',async()=>{
  const host=document.createElement('section');
  fetcher.mockResolvedValueOnce(response({ok:true,offset:0,rows:[['7',2,0,1.5,.25]],table:{name:'objects',rows:1,
    columns:[{name:'object_id',unit:null},{name:'voxel_count',unit:'pixel'},
      {name:'centroid_y_px',unit:'pixel',description:'Mean array index along y; no calibration applied'},
      {name:'centroid_x_px',unit:'pixel'},
      {name:'area_um2',unit:'µm²',description:"Object size from its element count and the labels' own axis-aligned calibration (0.125 µm² per element)"}]}}));
  scientificResourceActions(host,source,{scientificMeasurements:{format:'scientific-object-measurements-v2',table:'objects',
    source:{id:source.id,session:source.day,revision:1,input:'labels'}}},open,{signal:abort.signal,current:()=>true});
  await settle();
  expect(host.textContent).not.toContain('Size and center in pixels · no calibration applied');
  expect(host.textContent).toContain('Pixels');expect(host.textContent).not.toContain('Voxels');
  expect(host.textContent).toContain('0.125 µm² per element');expect(host.textContent).toContain('area_um2 (µm²)');
});


test.each([undefined,{...operation,id:'other'},{...operation,input:{kind:'capture'}}])('missing or different operation cannot authorize measurement: %j',async(declaration)=>{
  const result=await discovery().json();result.operation=declaration;
  fetcher.mockResolvedValueOnce(response(result)).mockResolvedValueOnce(noResources());
  fire(button('Choose saved data'),'click');await settle();
  expect(button('Measure objects')).toBeUndefined();
  expect(root.textContent).toContain('Measurement operation is unavailable');
  expect(root.querySelectorAll('a').some(el=>el.textContent==='Export native data + metadata')).toBe(true);
});

test('continuing chosen labels presents only that exact component for measurement and native export',async()=>{
  const host=document.createElement('section') as unknown as TestElement;
  fetcher.mockResolvedValueOnce(response({ok:true,operation,source:{id:source.id,session:source.day,revision:2,digest},arrays:[
    {key:'image',name:'Original',shape:[2,3],dtype:'|u1',semantic:'intensity',canMeasure:false,reference:{component:'array.image'}},
    {key:'accepted',name:'Corrected cells',shape:[2,3],dtype:'>u8',semantic:'instance-labels',canMeasure:true,measurementPlan:pixelPlan(),reference:{component:'array.corrected'}},
  ]})).mockResolvedValueOnce(noResources());
  scientificResourceActions(host as unknown as HTMLElement,source,{},open,{signal:abort.signal,current:()=>current,selection:{component:'array.corrected',digest,name:'Corrected cells'}});
  await settle();
  expect(host.textContent).toContain('Corrected cells');expect(host.textContent).not.toContain('Original');
  expect(host.querySelectorAll('button').filter(b=>b.textContent==='Measure objects')).toHaveLength(1);
  expect(Reflect.get(host.querySelectorAll('a').find(a=>a.textContent==='Export native data + metadata')!,'href')).toContain('input=accepted');
});

test('chosen saved-data actions fail closed if a later inventory has a different digest',async()=>{
  const host=document.createElement('section') as unknown as TestElement;
  fetcher.mockResolvedValueOnce(response({ok:true,operation,source:{id:source.id,session:source.day,revision:2,digest:'e'.repeat(64)},arrays:[{key:'accepted',name:'Corrected cells',shape:[2,3],dtype:'<u2',semantic:'instance-labels',canMeasure:true,measurementPlan:pixelPlan(),reference:{component:'array.corrected'}}]}));
  scientificResourceActions(host as unknown as HTMLElement,source,{},open,{signal:abort.signal,current:()=>current,selection:{component:'array.corrected',digest,name:'Corrected cells'}});
  await settle();expect(host.textContent).toContain('no longer match');expect(host.querySelectorAll('a')).toHaveLength(0);expect(host.querySelectorAll('button').some(b=>b.textContent==='Measure objects')).toBe(false);
});

test('native measurements continue from their exact accepted revision and current owner',async()=>{
  const host=document.createElement('section'),editable=vi.fn(async(_revision:number)=>{});
  fetcher.mockResolvedValueOnce(response({ok:true,offset:0,rows:[['9223372036854775815',2,0,1.5]],table:{name:'objects',rows:1,
    columns:[{name:'object_id',unit:null},{name:'voxel_count',unit:'pixel'},{name:'centroid_y_px',unit:'pixel'},{name:'centroid_x_px',unit:'pixel'}]}}));
  scientificResourceActions(host,source,{scientificView:{inputs:{labels:{revision:'r'}}},scientificMeasurements:{
    format:'scientific-object-measurements-v2',table:'objects',source:{id:source.id,session:source.day,revision:1,input:'labels',reference:{revision:'r'}},
  }},open,{signal:abort.signal,current:()=>current,editable});
  const next=(host as unknown as TestElement).querySelectorAll('button').find(row=>row.textContent==='Open editable table')!;
  current=false;fire(next,'click');await settle();expect(editable).not.toHaveBeenCalled();
  current=true;fire(next,'click');fire(next,'click');await settle();expect(editable).toHaveBeenCalledTimes(1);expect(editable).toHaveBeenCalledWith(2);
  expect(fetcher).toHaveBeenCalledTimes(1);
});
