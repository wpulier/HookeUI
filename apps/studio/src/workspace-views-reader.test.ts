import {afterEach,beforeEach,expect,test,vi} from 'vitest';
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({})}));
import {WorkspaceViews} from './workspace-views';
import {mainLayout,readerView} from './workspace-views-model';
beforeEach(()=>{vi.stubGlobal('window',{dispatchEvent:vi.fn()});vi.stubGlobal('document',{body:{dataset:{}}});vi.stubGlobal('crypto',{randomUUID:()=>'new-id'});});
afterEach(()=>vi.unstubAllGlobals());

const item={resource:{day:'p',id:'r810833',version:2},title:'Bright regions in Human cells'};
/** A WorkspaceViews with only the state these paths read (as workspace-completion-save.test.ts does). */
function views(fields:Record<string,unknown>={}){
 const layout=mainLayout();layout.views.push({id:'t2',title:'Tab 2',specimen:{visible:true,rect:null},items:[],notes:[]});
 return Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout,openIds:['main','t2'],loaded:new Set(['main']),
  dirty:false,conflict:false,changing:false,navigating:false,openingInput:false,completionRestores:new Set(),sessionReaders:new Map(),
  capture:vi.fn(),flush:vi.fn(async()=>{}),changed:vi.fn(),goTo:vi.fn(async()=>true),flash:vi.fn(),activate:vi.fn(async()=>true),...fields});
}

test('negotiates layout schemas 7 and 6 before legacy 5, keeping reader Tabs for the session',async()=>{
 const request=vi.fn(async(_type:string,fields:any)=>fields.schema>=6?{ok:false,code:'unsupported-schema',error:'Reload this app: the requested layout format is unsupported.'}
  :{ok:true,revision:4,layout:{schema:5,coordinates:'stage',views:[{id:'main',title:'Main',specimen:{visible:true,rect:null},items:[],notes:[]}]}});
 const current=views({opts:{request}});
 const reply=await Reflect.get(WorkspaceViews.prototype,'readLayout').call(current,'p');
 expect(request.mock.calls.map(call=>call[1].schema)).toEqual([7,6,5]);
 expect(reply.ok).toBe(true);
 expect(current.readerTabsUnsaved()).toBe(true);
 // A reader Tab opened on that server stays after the next accepted read; the saved layout is sent without it.
 const kept=readerView('t9',item);current.sessionReaders.set('p',[kept]);
 Reflect.get(WorkspaceViews.prototype,'accept').call(current,reply);
 expect(current.layout.views.map((v:{id:string})=>v.id)).toEqual(['main','t9']);
 expect(JSON.parse(current.lastSaved).views.map((v:{id:string})=>v.id)).toEqual(['main']);
});

test('a server that reads schema 6 is asked once and takes the reader Tab whole',async()=>{
 const request=vi.fn(async()=>({ok:true,revision:1,layout:{schema:6,coordinates:'stage',views:[readerView('t9',item)]}}));
 const current=views({opts:{request}});
 await Reflect.get(WorkspaceViews.prototype,'readLayout').call(current,'p');
 expect(request).toHaveBeenCalledTimes(1);
 expect(current.readerTabsUnsaved()).toBe(false);
 Reflect.get(WorkspaceViews.prototype,'accept').call(current,{ok:true,revision:1,layout:{schema:6,coordinates:'stage',views:[readerView('t9',item)]}});
 expect(JSON.parse(current.lastSaved)).toMatchObject({schema:6,views:[{kind:'reader',items:[item]}]});
});

test('Open in new Tab makes a reader Tab for the item and goes to it; the same item again goes to that Tab',async()=>{
 const current=views({opts:{}});
 expect(await current.openReaderTab(item)).toBe(true);
 const made=current.layout.views.at(-1);
 expect(made).toMatchObject({id:'new-id',kind:'reader',title:'Bright regions in Human cells',specimen:{visible:false,rect:null},items:[item]});
 expect(current.openIds).toContain('new-id');
 expect(current.goTo).toHaveBeenLastCalledWith('new-id',{session:'p',generation:1});
 current.goTo.mockClear();
 await current.openReaderTab(item);
 expect(current.layout.views.filter((v:{kind?:string})=>v.kind==='reader')).toHaveLength(1);
 expect(current.goTo).toHaveBeenCalledWith('new-id',{session:'p',generation:1});
});

test('Add to Tab… adds without leaving and says where, with Go to that Tab',async()=>{
 const current=views({opts:{}});
 const placed={resource:{day:'p',id:'e8',version:0,picture:0},title:'Bright regions · Bright objects · 02:35',zoom:1,pan:[.5,.5],presentation:'masks' as const};
 await current.addItem('t2',placed);
 const tab2=current.layout.views.find((v:{id:string})=>v.id==='t2');
 expect(tab2.items).toEqual([{...placed,placementId:'new-id'}]);
 expect(current.layout.active).toBe('main');
 expect(current.goTo).not.toHaveBeenCalled();
 const [text,actions]=current.flash.mock.calls[0];
 expect(text).toBe('Added to Tab 2');
 expect(actions.map((a:[string,()=>void])=>a[0])).toEqual(['Go to Tab 2']);
 actions[0][1]();expect(current.goTo).toHaveBeenCalledWith('t2',{session:'p',generation:1});
 // A reader Tab shows one thing on its own: nothing is added there.
 current.layout.views.push(readerView('t9',item));
 await expect(current.addItem('t9',placed)).rejects.toThrow('one thing on its own');
});

test('a reader Tab keeps its one item: capture never rebuilds it from windows, and it takes no input',()=>{
 const view=readerView('t9',item);
 const current=views({active:()=>view,opts:{captureInput:()=>({source:'a1',position:0}),captureItems:()=>[]},visibleRoots:()=>[],specimen:{hidden:true}});
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items).toEqual([item]);
 expect(Reflect.get(WorkspaceViews.prototype,'rememberInput').call(Object.assign(current,{layout:{...current.layout,schema:6}}))).toBe(false);
 expect('input' in view).toBe(false);
});
