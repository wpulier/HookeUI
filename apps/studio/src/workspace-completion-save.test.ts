import {afterEach,beforeEach,expect,test,vi} from 'vitest';
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({})}));
import {WorkspaceViews} from './workspace-views';
beforeEach(()=>{
 vi.stubGlobal('window',{dispatchEvent:vi.fn()});
 vi.stubGlobal('document',{body:{dataset:{}}});
});
afterEach(()=>vi.unstubAllGlobals());

const layout=()=>({schema:3,coordinates:'stage',active:'main',views:[{id:'main',title:'Local',specimen:{visible:true,rect:null},items:[],notes:[]}]});
const item={placementId:'server-result',resource:{day:'p',id:'e1',version:3,picture:0},title:'Masks',presentation:'masks',zoom:1,pan:[.5,.5]};

test.each(['unchanged','newer-title','removed-tab','project-switch','already-mounted','restore-failed','server-conflict','edit-during-restore'])('save response preserves local ownership and completion: %s',async change=>{
 let finish!:(reply:object)=>void;
 const requests:object[]=[],restored:object[]=[];
 const request=vi.fn(async(_type:string,fields:any)=>{
  requests.push(structuredClone(fields));
  if(requests.length===1)return new Promise<Record<string,any>>(resolve=>{finish=resolve;});
  return {ok:true,revision:6,layout:fields.layout};
 });
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout:layout(),revision:1,
  dirty:true,conflict:false,changing:false,notes:new Map(),noteTimers:new Map(),saving:null,timer:0,lastSaved:'',layoutDraft:undefined,
  status:{textContent:''},retry:{hidden:true},recover:{hidden:true},loaded:new Set(['main']),completionRestores:new Set(),
  opts:{request,restoreItems:vi.fn(async(items:object[])=>{restored.push(...items);if(change==='edit-during-restore'){expect(current.changing).toBe(false);current.layout.views[0].title='Edited during restore';}return{recovered:change==='restore-failed'?0:items.length,missing:change==='restore-failed'?['Restore source']:[]};}),notice:vi.fn()},
  updateRecovery:vi.fn(),cache:vi.fn(),visibility:vi.fn(),render:vi.fn(),recovery:{clear:vi.fn()},
 });
 const job=current.prepareScientificView();
 if(change==='already-mounted')current.layout.views[0].items=[item] as never[];
 if(change==='newer-title')current.layout.views[0].title='Newer local title';
 if(change==='removed-tab')current.layout.views=[];
 if(change==='project-switch'){current.session='q';current.generation++;current.layout=layout();current.layout.views[0].title='Other Project';}
 const {active:_,...accepted}=layout();accepted.views[0].items=[item] as never[];
 finish(change==='server-conflict'?{ok:false,code:'conflict',error:'Another edit',current:{revision:5,layout:accepted}}:{ok:true,revision:5,layout:accepted,completionAdditions:[{tab:'main',item}]});
 if(['removed-tab','project-switch','restore-failed','server-conflict'].includes(change))await expect(job).rejects.toThrow('layout');else await job;
 if(change==='project-switch'){expect(current.layout.views[0].title).toBe('Other Project');expect(restored).toEqual([]);return;}
 if(change==='server-conflict'||change==='restore-failed'){expect(current.conflict).toBe(true);expect(requests).toHaveLength(1);expect(current.status.textContent).toBe('Not saved');return;}
 if(change==='removed-tab'){expect(current.layout.views).toEqual([]);expect(current.conflict).toBe(true);expect(requests).toHaveLength(1);return;}
 expect(current.layout.views[0].items).toEqual([item]);expect(restored).toEqual(change==='already-mounted'?[]:[{...item,coordinates:'stage'}]);
 expect(current.status.textContent).toBe('Saved');expect(current.opts.notice).not.toHaveBeenCalled();
 expect(current.layout.views[0].title).toBe(change==='newer-title'?'Newer local title':change==='edit-during-restore'?'Edited during restore':'Local');
 expect(requests).toHaveLength(['newer-title','edit-during-restore'].includes(change)?2:1);
 expect(JSON.parse(current.lastSaved).views[0].items).toEqual([item]);
});

test('capture during delayed result restoration retains the pending exact placement',()=>{
 const retained={...item},view={items:[retained],notes:[]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',changing:false,
  completionRestores:new Set([item.placementId]),active:()=>view,specimen:{hidden:true},
  opts:{captureItems:()=>[]},visibleRoots:()=>[],rect:()=>[1,2,300,200]});
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items).toEqual([retained]);
 current.completionRestores.clear();
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items).toEqual([]); // Once mounted, ordinary Close is a real local edit.
});

test('an old Project save releases its own promise before the next Project saves',async()=>{
 let finish!:(reply:object)=>void;
 const calls:string[]=[];
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'old',generation:1,layout:layout(),revision:1,
  dirty:true,conflict:false,changing:false,notes:new Map(),noteTimers:new Map(),saving:null,timer:0,lastSaved:'',
  status:{textContent:''},retry:{hidden:true},recover:{hidden:true},recovery:{clear:vi.fn()},updateRecovery:vi.fn(),
  opts:{notice:vi.fn(),request:vi.fn(async(_type:string,fields:any)=>{
   calls.push(fields.session);
   if(fields.session==='old')return new Promise<Record<string,any>>(resolve=>{finish=resolve;});
   return {ok:true,revision:1,layout:fields.layout};
  })},
 });
 const old=current.flush();
 current.session='new';current.generation++;current.revision=0;current.layout=layout();current.layout.views[0].title='New Project';
 finish({ok:true,revision:2});await old;
 // This owned lifecycle invariant prevents infinite microtask recursion before the public next save.
 expect(current.saving).toBeNull();
 await current.prepareScientificView();
 expect(calls).toEqual(['old','new']);expect(current.status.textContent).toBe('Saved');
 expect(JSON.parse(current.lastSaved).views[0].title).toBe('New Project');
});


test('comparison capture keeps pending lanes and prunes an actually closed window only',()=>{
 const entries=[{...item,placementId:'a'},{...item,placementId:'b'},{...item,placementId:'c'}];
 const view={kind:'compare',items:entries,notes:[],compare:{sync:true,lanes:entries.map((i,n)=>({placementId:i.placementId,label:['A','B','C'][n],impl:'same'}))}};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',changing:false,
  completionRestores:new Set(['a','b','c']),active:()=>view,specimen:{hidden:true},
  opts:{captureItems:()=>[]},visibleRoots:()=>[],rect:()=>[1,2,300,200]});
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.compare.lanes.map(l=>l.placementId)).toEqual(['a','b','c']);
 current.completionRestores.delete('b');Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.compare.lanes.map(l=>l.placementId)).toEqual(['a','c']);
 expect(view.items.map(i=>i.placementId)).toEqual(['a','c']);
});


test.each([false,true])('completed researcher output adds a background Tab without selecting it, opened only when its researcher has no Tab of its own (%s)',async researcherHasTab=>{
 const output={id:'output',kind:'output',title:'Research results',provenance:{researcher:'r1',version:1,from:'main'},specimen:{visible:false,rect:null},items:[],notes:[]};
 const accepted={schema:5,coordinates:'stage',views:[...layout().views,{...output,items:[item]}]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout:layout(),revision:1,
  dirty:true,conflict:false,changing:false,notes:new Map(),noteTimers:new Map(),saving:null,timer:0,lastSaved:'',layoutDraft:undefined,
  status:{textContent:''},retry:{hidden:true},recover:{hidden:true},loaded:new Set(['main']),completionRestores:new Set(),openIds:['main'],
  opts:{request:vi.fn(async()=>({ok:true,revision:2,layout:accepted,completionAdditions:[{tab:'output',view:output,item}]})),restoreItems:vi.fn(),notice:vi.fn(),researcherTab:(rid:string)=>researcherHasTab&&rid==='r1'},
  updateRecovery:vi.fn(),cache:vi.fn(),render:vi.fn(),recovery:{clear:vi.fn()},
 });
 await current.prepareScientificView();
 // The researcher's own Tab is where its work shows; its results Tab is kept (under ⌄) but not opened beside it.
 expect(current.layout.active).toBe('main');expect(current.openIds).toEqual(researcherHasTab?['main']:['main','output']);
 expect(current.layout.schema).toBe(5);expect(current.layout.views[1].items).toEqual([item]);
 expect(current.opts.restoreItems).not.toHaveBeenCalled();expect(current.status.textContent).toBe('Saved');
});

test('the row\'s layout status is the layout\'s, said apart from a scientific window\'s own Save, and Saved takes no room',async()=>{
 const {readFileSync}=await import('node:fs');
 const {LAYOUT_STATUS_MEANING,layoutStatusNeedsYou}=await import('./workspace-views');
 const css=readFileSync(new URL('./workspace-feel.css',import.meta.url),'utf8');
 expect(css).toContain('.workspace-layout-status::before { content: "Layout: ";');
 expect(css).toContain(".workspace-layout-status:is(:empty, [data-quiet='true']) { display: none; }");
 for(const quiet of ['Saved','Saving…','Opening…','Unsaved changes','Tab restored'])expect(layoutStatusNeedsYou(quiet)).toBe(false);
 for(const loud of ['Not saved','Not saved · Review completed results','Reload required','Opening failed','Saved · Camera unavailable','Tab removed · Data kept'])expect(layoutStatusNeedsYou(loud)).toBe(true);
 expect(LAYOUT_STATUS_MEANING).toMatch(/Tabs and where their windows are/);
 expect(LAYOUT_STATUS_MEANING).toMatch(/scientific window are kept with its own Save/);
});
