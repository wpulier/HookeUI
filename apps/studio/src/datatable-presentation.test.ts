// Synthetic shell and transport tests; no native runtime or installed-app acceptance.
import {beforeEach,afterEach,expect,test,vi} from 'vitest';
import {syntheticDOM,TestElement} from './workspace-windows.test-support';
import type {DataTableViewDeps} from './datatable-view';
const state=vi.hoisted(()=>({deps:null as DataTableViewDeps|null,root:null as unknown as HTMLElement,guard:null as null|(()=>string|null),dirty:false,show:vi.fn(),remember:vi.fn()}));
vi.mock('./datatable-view',()=>({DataTableView:class{
 constructor(_root:HTMLElement,deps:DataTableViewDeps){state.deps=deps;}
 get dirty(){return state.dirty;}get busy(){return false;}
 show=state.show;hide=vi.fn();selectMember=vi.fn();
}}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({
 document:(_key:string,_title:string,content:HTMLElement,_dispose:unknown,metadata:Record<string,string>)=>{
  const root=document.createElement('section');Object.assign(root.dataset,metadata,{project:'p',workspaceView:'tab',placementId:'place'});Object.defineProperty(root,'isConnected',{value:true});root.append(content);state.root=root;return root;
 },restoreGeometry:vi.fn(),documentTitle:vi.fn(),guardDocument:(_root:HTMLElement,guard:()=>string|null)=>{state.guard=guard;},
})}));
import {configureDataTableDocuments,makeEditableTable,openStudyDataTable} from './datatable-presentation';
const saved={resource:{day:'p',id:'tb1',version:2,picture:null},title:'Measured cells',zoom:1,pan:[.5,.5]};
let generation=1;
beforeEach(()=>{
 const dom=syntheticDOM();Object.assign(dom.document.body.dataset,{workspaceProject:'p',activeWorkspaceView:'tab'});vi.stubGlobal('document',dom.document);
 generation=1;state.dirty=false;state.remember.mockReset();state.show.mockReset().mockResolvedValue(true);
 configureDataTableDocuments({owner:()=>({session:'p',generation}),current:o=>o.session==='p'&&o.generation===generation,remember:state.remember,settle:vi.fn()});
});
afterEach(()=>vi.unstubAllGlobals());
test('reopen pins exact accepted revision and Save updates the original placement only',async()=>{
 await openStudyDataTable(saved);
 expect(state.show).toHaveBeenCalledWith({session:'p',id:'tb1',version:2},{session:'p',generation:1});
 state.dirty=true;expect(state.guard?.()).toContain('table draft');state.dirty=false;
 await state.deps!.saved!({session:'p',id:'tb1',version:3},{title:'Edited table'} as never);
 expect(state.remember).toHaveBeenCalledWith('p','place',saved.resource,{...saved.resource,version:3},'Edited table');
 generation+=2;
 await expect(state.deps!.saved!({session:'p',id:'tb1',version:4},{title:'Wrong opening'} as never)).rejects.toThrow('saved in Data');
 expect(state.remember).toHaveBeenCalledOnce();
});
test.each(['away-and-back','different-tab'])('accepted creation after %s stays in original Data without mounting a new window',async change=>{
 const root=new TestElement('section');Object.assign(root.dataset,{project:'p',workspaceView:'tab',placementId:'source'});Object.defineProperty(root,'isConnected',{value:true});
 let finish!:(value:unknown)=>void;const request=vi.fn((_url:string,_init:RequestInit)=>new Promise(resolve=>{finish=resolve;}));vi.stubGlobal('fetch',request);
 const creating=makeEditableTable({...saved,resource:{day:'p',id:'e3',version:7,picture:null}},root as unknown as HTMLElement);
 expect(JSON.parse(String(request.mock.calls[0][1].body))).toMatchObject({session:'p',sources:[{id:'e3',revision:7}]});
 if(change==='away-and-back')generation+=2;else document.body.dataset.activeWorkspaceView='other';
 finish({ok:true,json:async()=>({id:'tb2',version:1,title:'Created'})});
 await expect(creating).rejects.toThrow('saved in its original Project');expect(state.show).not.toHaveBeenCalled();
});
