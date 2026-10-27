// Synthetic DOM and table reads; real document mount, Tab transfer, restore and capture.
import {afterEach,expect,test,vi} from 'vitest';
import {syntheticDOM} from './workspace-windows.test-support';
import type {DataTableViewDeps,DataTablePin,DataTableOwner,DataTableSnapshot} from './datatable-view';
const state=vi.hoisted(()=>({artifacts:null as unknown,show:vi.fn(async()=>true)}));
vi.mock('./datatable-view',()=>({DataTableView:class{
 private deps:DataTableViewDeps;
 constructor(_root:HTMLElement,deps:DataTableViewDeps){this.deps=deps;}
 dirty=false;busy=false;hide=vi.fn();
 async show(pin:DataTablePin,_owner:DataTableOwner){
  await state.show();await this.deps.loaded?.(pin,{title:'Measured cells'} as DataTableSnapshot);return true;
 }
}}));
vi.mock('./workspace-study-artifacts',async original=>({
 ...await original<typeof import('./workspace-study-artifacts')>(),studyArtifacts:()=>state.artifacts,
}));
import {StudyArtifacts} from './workspace-study-artifacts';
import {WorkspaceViews} from './workspace-views';
import {configureDataTableDocuments,openStudyDataTable} from './datatable-presentation';
import type {StudyRetainedView} from './workspace-study-restoration';
afterEach(()=>vi.unstubAllGlobals());

test('move an ordinary table, open its destination and resize without duplicating its saved placement',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('window',{dispatchEvent:vi.fn()});
 Object.assign(document.body.dataset,{workspaceProject:'p',activeWorkspaceView:'main'});
 const host=document.createElement('aside'),shells=new Map();
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{root:host,guards:new WeakMap(),documents:new Map(),shells,background:new WeakSet(),sync:vi.fn(),
  mount:(root:HTMLElement)=>{
   root.dataset.project='p';root.dataset.workspaceView=document.body.dataset.activeWorkspaceView;
   root.dataset.placementId??=crypto.randomUUID();Object.defineProperty(root,'isConnected',{value:true});
   host.append(root);shells.set(root,{front:vi.fn(),resizeTo:vi.fn(),moveTo:vi.fn()});
   root.querySelector<HTMLElement>('header')!.focus=vi.fn();
  }});
 state.artifacts=artifacts;state.show.mockClear();
 const view=(id:string)=>({id,title:id,specimen:{visible:false,rect:null},items:[] as StudyRetainedView[],notes:[]});
 const main=view('main'),target=view('second'),savedLayouts:unknown[]=[];
 const tabs=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'p',generation:1,layout:{schema:5,coordinates:'stage',active:'main',views:[main,target]},
  openIds:['main','second'],loaded:new Set(['main','second']),completionRestores:new Set(),
  stage:document.body,empty:document.createElement('p'),specimen:Object.assign(document.createElement('section'),{hidden:true}),
  rememberInput:()=>false,updateTransfer:vi.fn(),navSave:vi.fn(),applySpecimen:vi.fn(),render:vi.fn(),clearFlash:vi.fn(),resumeReveal:vi.fn(),observeInput:vi.fn(),
  changed:()=>{tabs.dirty=true;},
  flush:async()=>{
   const placements=tabs.layout.views.flatMap((v:typeof main)=>v.items.map(i=>i.placementId));
   if(new Set(placements).size!==placements.length)throw new Error('duplicate placement identity');
   savedLayouts.push(structuredClone(tabs.layout));tabs.dirty=false;
  },
  opts:{notice:vi.fn(),captureItems:()=>[],layoutChanged:vi.fn(),restoreItems:async(items:StudyRetainedView[])=>{
   for(const item of items)await openStudyDataTable(item);return {recovered:items.length,missing:[]};
  }},
 });
 configureDataTableDocuments({owner:()=>({session:'p',generation:1}),current:()=>true,settle:vi.fn(),remember:vi.fn()});
 const saved={resource:{day:'p',id:'tb1',version:1,picture:null},title:'Measured cells',zoom:1,pan:[.5,.5]};
 await openStudyDataTable(saved);const original=host.children[0] as HTMLElement,pid=original.dataset.placementId;
 await Reflect.get(WorkspaceViews.prototype,'move').call(tabs,original,'second',false);
 expect(main.items).toEqual([]);expect(target.items.map(i=>i.placementId)).toEqual([pid]);
 await Reflect.get(WorkspaceViews.prototype,'activate').call(tabs,'second');
 // Expanding/moving a window emits the same capture + save sequence.
 Reflect.get(WorkspaceViews.prototype,'capture').call(tabs);await tabs.flush();
 expect(host.children).toHaveLength(1);expect(host.children[0]).toBe(original);
 expect(target.items.map(i=>i.placementId)).toEqual([pid]);expect(target.items[0].resource).toEqual(saved.resource);
 expect(state.show).toHaveBeenCalledOnce();expect(savedLayouts.length).toBeGreaterThan(1);
});
