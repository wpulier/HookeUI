import {describe,it,expect} from 'vitest';
import {LAYOUT_SCHEMA,LAYOUT_SCHEMAS,SCHEMA5_PRESENTATIONS,bumpForSchema5,layoutForServer,mainLayout,needsSchema5,needsSchema6,readerItem,readerView,renewPlacements,setTabKind,tileRects,transferItem,undoArrange} from './workspace-views-model';

it('retained media requires schema 7 and cannot be stripped for an older server',()=>{
 const layout=mainLayout();
 layout.views[0].input={source:'a1',position:0,retained:{origin:'a'.repeat(32),session:'project',document:'p'+'b'.repeat(32),revision:1,digest:'c'.repeat(64)}};
 bumpForSchema5(layout);
 expect(layout.schema).toBe(7);
 expect(layoutForServer(layout,7).views[0].input).toEqual(layout.views[0].input);
 expect(()=>layoutForServer(layout,6)).toThrow('exact reference cannot be dropped');
});
describe('session arrangements',()=>{
 it('moves the exact revision without duplicating or deleting content',()=>{
  const l=mainLayout(),item={resource:{day:'2026-10-16',id:'e14',version:2,picture:0},title:'Masks',zoom:1,pan:[.5,.5]};
  l.views[0].items.push(item);l.views.push({id:'compare',title:'Compare',specimen:{visible:false,rect:null},items:[],notes:[]});
  const next=transferItem(l,'main','compare',item);
  expect(next.views[0].items).toEqual([]);expect(next.views[1].items).toEqual([item]);expect(l.views[0].items).toEqual([item]);
  expect(transferItem(next,'main','compare',item).views[1].items).toHaveLength(1);
 });
 it('fits explicit arrangements within the work area without overlaps',()=>{
  for(const [w,h] of [[1000,700],[320,500]]){
   const r=tileRects(5,w,h);for(const [x,y,a,b] of r){expect(x).toBeGreaterThanOrEqual(0);expect(y).toBeGreaterThanOrEqual(0);expect(x+a).toBeLessThanOrEqual(w);expect(y+b).toBeLessThanOrEqual(h);}
   for(let i=0;i<r.length;i++)for(let j=i+1;j<r.length;j++){const a=r[i],b=r[j];expect(a[0]+a[2]<=b[0]||b[0]+b[2]<=a[0]||a[1]+a[3]<=b[1]||b[1]+b[3]<=a[1]).toBe(true);}
  }
 });
});
it('uses stage-relative versioned layouts without sharing active navigation',()=>{
 const layout=mainLayout();expect(layout.schema).toBe(2);expect(layout).toHaveProperty('coordinates','stage');
});
it('undoes Arrange without dropping new tabs, completions or edits to retained placements',()=>{
 const layout=mainLayout(),view=layout.views[0];
 view.items.push({placementId:'p1',resource:{day:'project',id:'e1',version:1,picture:0},title:'Result',zoom:1,pan:[.5,.5],position:[2,3],size:[200,180]});
 view.notes.push({id:'a1',placementId:'n1',rect:[4,5,200,200]},{id:'a2',placementId:'n2',rect:[6,7,200,200]});
 const before=structuredClone(view);
 view.specimen.rect=[10,10,300,300];view.items[0].position=[320,10];view.items[0].size=[300,300];view.notes.forEach(n=>{n.rect=[10,320,300,300];});
 const arranged=structuredClone(view);
 view.title='Renamed';view.items[0].title='Updated title';view.items[0].display={background:false};view.specimen.visible=false;
 view.items.push({...structuredClone(view.items[0]),placementId:'completion',title:'New completion'});
 view.notes=view.notes.filter(n=>n.placementId!=='n2');view.notes[0].rect=[500,500,250,250];
 layout.views.push({id:'new',title:'Created after Arrange',specimen:{visible:false,rect:null},items:[],notes:[]});layout.active='new';
 const next=undoArrange(layout,before,arranged);
 expect(next.active).toBe('new');expect(next.views).toHaveLength(2);expect(next.views[0].title).toBe('Renamed');
 expect(next.views[0].items).toHaveLength(2);expect(next.views[0].items[0]).toMatchObject({title:'Updated title',display:{background:false},position:[2,3],size:[200,180]});
 expect(next.views[0].items[1]).toEqual(view.items[1]);expect(next.views[0].notes).toEqual(view.notes);
 expect(next.views[0].specimen).toEqual({visible:false,rect:null});
 expect(undoArrange({...layout,views:layout.views.slice(1)},before,arranged).views).toEqual(layout.views.slice(1));
});
it('Undo Arrange does not move a placement back from another tab',()=>{
 const layout=mainLayout(),view=layout.views[0];
 view.items.push({placementId:'moved',resource:{day:'project',id:'e1',version:1,picture:0},title:'Result',zoom:1,pan:[.5,.5],position:[2,3],size:[200,180]});
 const before=structuredClone(view);view.items[0].position=[10,10];const arranged=structuredClone(view);
 layout.views.push({id:'destination',title:'Destination',specimen:{visible:false,rect:null},items:view.items,notes:[]});view.items=[];
 expect(undoArrange(layout,before,arranged)).toEqual(layout);
});

it('pins a selected scientific input independently of the current specimen feed',async()=>{
 const {workspaceInput}=await import('./workspace-views-model');
 const resource={session:'project',id:'d4',revision:'a'.repeat(64),component:'picture-0'};
 const selected=workspaceInput({resource},'unrelated-image');
 resource.id='d5';
 expect(selected).toEqual({resource:{...resource,id:'d4'}});
 expect(workspaceInput({volume:'v1'},null)).toEqual({volume:'v1'});
 expect(workspaceInput({},null)).toEqual({source:null});
});

it('asks for input before admitting a Try from an empty workspace',async()=>{
 const {workspaceInput}=await import('./workspace-views-model');
 expect(()=>workspaceInput({type:'capability-run'},null)).toThrow('Choose a picture or video before running this tool.');
 expect(workspaceInput({type:'action'},null)).toEqual({source:null});
 expect(workspaceInput({type:'capability-run'},'chosen-image')).toEqual({source:'chosen-image'});
 const resource={session:'project',id:'d4',revision:'a'.repeat(64),component:'picture-0'};
 expect(workspaceInput({type:'capability-run',resource},null)).toEqual({resource});
 expect(workspaceInput({type:'capability-run',volume:'v1'},null)).toEqual({volume:'v1'});
});
describe('layout schema 5',()=>{
 const window=(placementId:string,id:string)=>({placementId,resource:{day:'project',id,version:1,picture:0},title:id,zoom:1,pan:[.5,.5]});
 const compareTab=()=>{
  const layout=mainLayout();layout.schema=4;
  layout.views.push({id:'c1',title:'Compare',specimen:{visible:true,rect:null},items:[window('a','e1'),window('b','e2'),window('c','e3')],notes:[]});
  return layout;
 };
 const lanes=[{placementId:'a',label:'A' as const,impl:'cellpose'},{placementId:'b',label:'B' as const,impl:'stardist'}];
 it('keeps older layouts in their own format until a schema-5 field is written',()=>{
  const layout=compareTab();
  expect(LAYOUT_SCHEMA).toBe(7);expect(needsSchema5(layout)).toBe(false);
  bumpForSchema5(layout);expect(layout.schema).toBe(4);
  setTabKind(layout,'c1',{kind:'compare',compare:{sync:true,lanes}});
  expect(layout.schema).toBe(5);expect(layout.views[1].compare?.lanes).toEqual(lanes);
  const output=mainLayout();output.views[0].kind='output';bumpForSchema5(output);expect(output.schema).toBe(5);
  const provenance=mainLayout();setTabKind(provenance,'main',{kind:'output',provenance:{researcher:'r1',version:2}});
  expect(provenance.schema).toBe(5);expect(provenance.views[0].provenance).toEqual({researcher:'r1',version:2});
 });
 it('treats a table, analysis or graph window as schema 5 and the older windows as not',()=>{
  const layout=compareTab();
  for(const presentation of ['masks','table','capture'] as const){layout.views[1].items[0].presentation=presentation;expect(needsSchema5(layout)).toBe(false);}
  for(const presentation of SCHEMA5_PRESENTATIONS){(layout.views[1].items[0] as any).presentation=presentation;expect(needsSchema5(layout)).toBe(true);}
 });
 it('refuses lanes the server would refuse, before any save',()=>{
  const bad:[any,RegExp][]=[
   [{kind:'output',compare:{sync:true,lanes}},/Only a compare Tab/],
   [{kind:'compare',compare:{sync:true,lanes:lanes.slice(0,1)}},/2 or 3 lanes/],
   [{kind:'compare',compare:{sync:true,lanes:[...lanes,{placementId:'c',label:'C',impl:'x'},{placementId:'c',label:'C',impl:'y'}]}},/2 or 3 lanes/],
   [{kind:'compare',compare:{sync:true,lanes:[lanes[0],{...lanes[1],label:'A'}]}},/A, B or C/],
   [{kind:'compare',compare:{sync:true,lanes:[lanes[0],{...lanes[1],label:'D'}]}},/A, B or C/],
   [{kind:'compare',compare:{sync:true,lanes:[lanes[0],{...lanes[1],placementId:'a'}]}},/different window/],
   [{kind:'compare',compare:{sync:true,lanes:[lanes[0],{...lanes[1],placementId:'gone'}]}},/this Tab's windows/],
  ];
  for(const [fields,reason] of bad){const layout=compareTab();expect(()=>setTabKind(layout,'c1',fields)).toThrow(reason);expect(layout.schema).toBe(4);expect(layout.views[1].kind).toBeUndefined();}
  expect(()=>setTabKind(compareTab(),'missing',{kind:'output'})).toThrow(/no longer in this Project/);
 });
 it('drops a lane with its window, and the comparison under two lanes',()=>{
  const layout=compareTab();
  setTabKind(layout,'c1',{kind:'compare',compare:{sync:false,lanes:[...lanes,{placementId:'c',label:'C',impl:'sam'}]}});
  const moved=transferItem(layout,'c1','main',layout.views[1].items[2]);
  expect(moved.views[1].compare?.lanes.map(l=>l.label)).toEqual(['A','B']);
  const again=transferItem(moved,'c1','main',moved.views[1].items[1]);
  expect(again.views[1].compare).toBeUndefined();expect(again.views[1].kind).toBe('compare');
 });
 it('gives a copied compare Tab new placements whose lanes follow their windows',()=>{
  const layout=compareTab();setTabKind(layout,'c1',{kind:'compare',compare:{sync:true,lanes}});
  const copy=structuredClone(layout.views[1]);let n=0;
  renewPlacements(copy,()=>`new${++n}`);
  expect(copy.items.map(i=>i.placementId)).toEqual(['new1','new2','new3']);
  expect(copy.compare?.lanes.map(l=>l.placementId)).toEqual(['new1','new2']);
 });
});
describe('layout schema 6: the reader Tab (ticket 66)',()=>{
 const item={resource:{day:'2026-10-25.4',id:'r810833',version:2},title:'Bright regions in Human cells'};
 it('is one Data item in a Tab of its own, the specimen hidden, in the contract’s shape',()=>{
  const view=readerView('t9',item);
  expect(view).toEqual({id:'t9',title:'Bright regions in Human cells',specimen:{visible:false,rect:null},items:[{resource:{day:'2026-10-25.4',id:'r810833',version:2},title:'Bright regions in Human cells'}],notes:[],kind:'reader'});
  expect(readerItem(view)).toEqual(item);
  expect(readerItem({kind:'output',items:[]})).toBeNull();
  expect(readerItem({kind:'reader',items:[]})).toBeNull();
 });
 it('moves a layout to schema 6 only once it holds a reader Tab; schema 5 layouts load unchanged',()=>{
  const layout=mainLayout();layout.schema=5;layout.views[0].kind='output';
  bumpForSchema5(layout);expect([needsSchema6(layout),layout.schema]).toEqual([false,5]);
  layout.views.push(readerView('t9',item));bumpForSchema5(layout);
  expect([needsSchema6(layout),layout.schema]).toEqual([true,6]);
  expect(LAYOUT_SCHEMAS).toEqual([2,3,4,5,6,7]);
 });
 it('sends a server that reads only schema 5 the layout without its reader Tabs, which stay in the studio',()=>{
  const layout=mainLayout();layout.views.push(readerView('t9',item));bumpForSchema5(layout);
  expect(layoutForServer(layout,6)).toEqual({schema:6,coordinates:'stage',views:layout.views});
  const old=layoutForServer(layout,5);
  expect(old.schema).toBe(5);expect(old.views.map(v=>v.id)).toEqual(['main']);
  expect(layout.views).toHaveLength(2);
 });
 it('never gives a reader Tab’s item a window placement when the Tab is copied',()=>{
  const view=readerView('t9',item);renewPlacements(view,()=>'new');
  expect(view.items).toEqual([{resource:item.resource,title:item.title}]);
 });
});
