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

test('ordinary page navigation replaces only the unpinned reader, preserving exact history and work tabs',async()=>{
 const current=views({opts:{notice:vi.fn()},readerPinned:()=>false});
 const first=readerView('reading',item);current.layout.views.push(first);current.layout.active='reading';
 current.active=()=>current.layout.views.find((v:{id:string})=>v.id===current.layout.active);
 const next={...item,title:'Another revision',resource:{...item.resource,version:3}};
 await current.openReaderPage(next);
 expect(current.layout.views).toHaveLength(3);expect(first.items[0].resource.version).toBe(3);
 expect(current.readerCanGoBack()).toBe(true);
 await current.readerBack();expect(first.items[0].resource.version).toBe(2);
 expect(current.layout.views[1].title).toBe('Tab 2');
 current.readerPinned=()=>true;await current.openReaderPage(next);
 expect(current.layout.views).toHaveLength(4);expect(first.items[0].resource.version).toBe(2);
});

test('a switched Project cannot replace a retained reader after waiting for a save',async()=>{
 const current=views({opts:{},readerPinned:()=>false});const first=readerView('reading',item);
 current.layout.views.push(first);current.layout.active='reading';current.active=()=>first;
 current.flush=vi.fn(async()=>{current.generation++;});
 await expect(current.openReaderPage({...item,resource:{...item.resource,version:3}})).rejects.toThrow('layout');
 expect(first.items[0].resource.version).toBe(2);
});

test.each(['new','existing','replace'] as const)('opening a %s reader leaves the covering research page before navigating',async mode=>{
 const order:string[]=[],leaveResearch=vi.fn(()=>{order.push('leave');return true;});
 const current=views({opts:{leaveResearch},readerPinned:()=>false});
 if(mode!=='new'){const view=readerView('reading',item);current.layout.views.push(view);current.layout.active='reading';current.active=()=>view;}
 current.goTo=vi.fn(async()=>{order.push('navigate');return true;});current.activate=current.goTo;
 const chosen=mode==='replace'?{...item,resource:{...item.resource,version:3}}:item;
 expect(await current.openReaderPage(chosen)).toBe(true);
 expect(order).toEqual(['leave','navigate']);
});

test.each(['new','existing','replace'] as const)('a retained draft refusal prevents %s reader navigation or layout changes',async mode=>{
 const current=views({opts:{leaveResearch:()=>false},readerPinned:()=>false});
 if(mode!=='new'){const view=readerView('reading',item);current.layout.views.push(view);current.layout.active='reading';current.active=()=>view;}
 const before=JSON.stringify(current.layout),chosen=mode==='replace'?{...item,resource:{...item.resource,version:3}}:item;
 expect(await current.openReaderPage(chosen)).toBe(false);
 expect(JSON.stringify(current.layout)).toBe(before);expect(current.capture).not.toHaveBeenCalled();expect(current.flush).not.toHaveBeenCalled();expect(current.goTo).not.toHaveBeenCalled();expect(current.activate).not.toHaveBeenCalled();
});


test.each(['new','existing','replace'] as const)('confirmed departure resumes the requested %s reader',async mode=>{
 let resume:(()=>void)|undefined,allowed=false;
 const current=views({opts:{leaveResearch:(then?:()=>void)=>{resume=then;return allowed;},notice:vi.fn()},readerPinned:()=>false});
 if(mode!=='new'){const view=readerView('reading',item);current.layout.views.push(view);current.layout.active='reading';current.active=()=>view;}
 const chosen=mode==='replace'?{...item,resource:{...item.resource,version:3}}:item;
 expect(await current.openReaderPage(chosen)).toBe(false);
 expect(current.goTo).not.toHaveBeenCalled();expect(current.activate).not.toHaveBeenCalled();
 allowed=true;resume!();
 await vi.waitFor(()=>expect(mode==='replace'?current.activate:current.goTo).toHaveBeenCalledTimes(1));
});

test('a deferred reader request cannot cross a Project switch',async()=>{
 let resume:(()=>void)|undefined;
 const notice=vi.fn(),current=views({opts:{leaveResearch:(then?:()=>void)=>{resume=then;return false;},notice}});
 expect(await current.openReaderPage(item)).toBe(false);
 current.generation++;resume!();
 await vi.waitFor(()=>expect(notice).toHaveBeenCalledWith(expect.objectContaining({message:expect.stringContaining('Project changed')})));
 expect(current.goTo).not.toHaveBeenCalled();expect(current.capture).not.toHaveBeenCalled();
});
