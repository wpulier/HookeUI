import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),removeDocument:vi.fn(),applyDocument:vi.fn(),document:vi.fn(),clearDocuments:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {WorkspaceViews,tabShortcut} from './workspace-views';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());

const press=(key:string,more:Partial<KeyboardEvent>={})=>({key,code:'',metaKey:true,ctrlKey:false,altKey:false,shiftKey:false,repeat:false,...more});

test('the browser’s tab keys ask for the Tab commands, with ⌘ on a Mac and Ctrl elsewhere',()=>{
 expect(tabShortcut(press('t'),true)).toBe('new');expect(tabShortcut(press('w'),true)).toBe('close');
 expect(tabShortcut(press('T',{shiftKey:true}),true)).toBe('reopen');
 expect(tabShortcut(press('1'),true)).toBe('go:1');expect(tabShortcut(press('9'),true)).toBe('go:9');
 expect(tabShortcut(press('}',{shiftKey:true,code:'BracketRight'}),true)).toBe('next');
 expect(tabShortcut(press('{',{shiftKey:true,code:'BracketLeft'}),true)).toBe('previous');
 expect(tabShortcut(press('t',{metaKey:false,ctrlKey:true}),false)).toBe('new');
 expect(tabShortcut(press('t',{metaKey:false,ctrlKey:true}),true)).toBe(null);
});

test('plain typing, Alt combinations and other keys are left alone; a held key opens or closes only once',()=>{
 for(const e of [press('t',{metaKey:false}),press('t',{altKey:true}),press('a'),press('0'),press('t',{repeat:true}),press('w',{repeat:true})])expect(tabShortcut(e,true)).toBe(null);
 expect(tabShortcut(press('}',{shiftKey:true,code:'BracketRight',repeat:true}),true)).toBe('next');
});

/** Main, Counting and Compare open (Counting active), Old closed. */
function tabs(active='b'){
 vi.stubGlobal('document',{querySelector:()=>null});vi.stubGlobal('window',new EventTarget());
 const views=[{id:'main',title:'Main'},{id:'b',title:'Counting'},{id:'c',title:'Compare'},{id:'d',title:'Old'}].map(v=>({...v,specimen:{visible:true,rect:null},items:[] as unknown[],notes:[] as unknown[]}));
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,changing:false,navigating:false,openingInput:false,dirty:false,conflict:false,
  layout:{active,views},openIds:['main','b','c'],closedStack:[],loaded:new Set(),opts:{notice:vi.fn()},
  goTo:vi.fn(async()=>true),addView:vi.fn(),closeTab:vi.fn(),flash:vi.fn(),clearReopen:vi.fn(),capture:vi.fn(),flush:vi.fn(async()=>{}),changed:vi.fn(),
  activate:vi.fn(async(id:string)=>{current.layout.active=id;return true;})});
 return current;
}

test('Go to, next and previous move among the open Tabs in their row order; 9 is the last',()=>{
 const t=tabs();
 t.command('go:1');expect(t.goTo).toHaveBeenLastCalledWith('main');
 t.command('go:9');expect(t.goTo).toHaveBeenLastCalledWith('c');
 t.command('next');expect(t.goTo).toHaveBeenLastCalledWith('c');
 t.command('previous');expect(t.goTo).toHaveBeenLastCalledWith('main');
 t.goTo.mockClear();t.command('go:2');t.command('go:7');expect(t.goTo).not.toHaveBeenCalled();
});

test('Close never closes Main; it says why',()=>{
 const t=tabs('main');t.command('close');expect(t.closeTab).not.toHaveBeenCalled();expect(t.flash).toHaveBeenCalledWith('Main stays open: it holds the specimen');
 const u=tabs('b');u.command('close');expect(u.closeTab).toHaveBeenCalledWith('b');
});

test('Reopen brings back the most recent closed Tab where it stood, then the one before',async()=>{
 const t=tabs();t.openIds=['main','c'];t.closedStack=[{id:'d',at:3},{id:'b',at:1}];
 const reopened=()=>new Promise<unknown>(resolve=>window.addEventListener('workspace-tab-reopened',event=>resolve((event as CustomEvent).detail),{once:true}));
 const first=reopened();t.command('reopen');await expect(first).resolves.toEqual({session:'p',kind:'work',id:'b'});
 expect(t.openIds).toEqual(['main','b','c']);expect(t.activate).toHaveBeenLastCalledWith('b');
 const second=reopened();t.command('reopen');await expect(second).resolves.toEqual({session:'p',kind:'work',id:'d'});expect(t.openIds).toEqual(['main','b','c','d']);
 t.command('reopen');expect(t.flash).toHaveBeenCalledWith('No closed Tab to reopen');
});

test('Duplicate puts a copy beside it with the same results as new placements, and goes there',async()=>{
 const t=tabs();const counting=t.layout.views[1];counting.items=[{placementId:'p1',resource:{id:'e1'}}];counting.notes=[{id:'n1',placementId:'p2'}];
 t.active=()=>counting;
 await Reflect.get(WorkspaceViews.prototype,'duplicateTab').call(t);
 const copy=t.layout.views[2];
 expect(copy.title).toBe('Counting copy');expect(t.openIds).toEqual(['main','b',copy.id,'c']);expect(t.activate).toHaveBeenCalledWith(copy.id);
 expect(copy.items[0].resource).toEqual({id:'e1'});expect(copy.items[0].placementId).not.toBe('p1');
 expect(copy.notes[0].id).toBe('n1');expect(copy.notes[0].placementId).not.toBe('p2');
 expect(counting.items[0].placementId).toBe('p1');
});

test('nothing happens behind an open dialog or without a Project',()=>{
 const t=tabs();vi.stubGlobal('document',{querySelector:(s:string)=>s==='dialog:modal'?{}:null});
 t.command('new');t.command('go:1');expect(t.addView).not.toHaveBeenCalled();expect(t.goTo).not.toHaveBeenCalled();
 const u=tabs();u.session='';u.command('new');expect(u.addView).not.toHaveBeenCalled();
});

test('Duplicate copies the Tab as the save left it, including results the save placed',async()=>{
 const t=tabs();const counting=t.layout.views[1];t.active=()=>counting;
 t.flush=vi.fn(async()=>{t.layout={...t.layout,views:t.layout.views.map((v:{id:string})=>v.id==='b'?{...structuredClone(v),items:[{placementId:'placed',resource:{id:'new'}}]}:v)};});
 await Reflect.get(WorkspaceViews.prototype,'duplicateTab').call(t);
 const at=t.layout.views.findIndex((v:{title:string})=>v.title==='Counting copy');
 expect(at).toBe(2);expect(t.layout.views[at].items[0].resource).toEqual({id:'new'});
});

test('Reopen waits while another Tab is opening',()=>{
 const t=tabs();t.openIds=['main','c'];t.closedStack=[{id:'b',at:1}];t.navigating=true;
 t.command('reopen');expect(t.openIds).toEqual(['main','c']);expect(t.closedStack).toEqual([{id:'b',at:1}]);
});

test('a focused scientific control keeps an already handled shortcut',()=>{
 expect(tabShortcut(press('t',{defaultPrevented:true}),true)).toBeNull();
 expect(tabShortcut(press('t',{metaKey:false,ctrlKey:true,defaultPrevented:true}),false)).toBeNull();
});


test('Duplicate remaps comparison lanes to its new placements without changing the original',async()=>{
 const t=tabs();const source=t.layout.views[1];
 Object.assign(source,{kind:'compare',compare:{sync:true,lanes:[{placementId:'p1',label:'A',impl:'first'},{placementId:'p2',label:'B',impl:'second'}]}});
 source.items=[{placementId:'p1',resource:{id:'e1',version:1}},{placementId:'p2',resource:{id:'e2',version:2}}];
 t.active=()=>source;await Reflect.get(WorkspaceViews.prototype,'duplicateTab').call(t);
 const copy=t.layout.views[2];
 expect(copy.compare.lanes.map((lane:{placementId:string})=>lane.placementId)).toEqual(copy.items.map((item:{placementId:string})=>item.placementId));
 expect(copy.compare.lanes.map((lane:{label:string})=>lane.label)).toEqual(['A','B']);
 expect(source.compare.lanes.map((lane:{placementId:string})=>lane.placementId)).toEqual(['p1','p2']);
});

/** "+" with extra starts (New researcher, New study…) opens a menu; the Tab row itself holds only Tabs (Will, 2026-10-23). */
function addMenuViews(choices:{label:string;title:string;act:()=>void}[]){
 const dom=syntheticDOM(),focusable=(tag:string)=>Object.assign(new TestElement(tag),{focus:vi.fn(),type:'',onclick:null as null|(()=>void)});
 vi.stubGlobal('document',Object.assign(dom.document,{createElement:focusable,querySelector:(s:string)=>dom.host.querySelector(s),activeElement:null}));
 const views=Object.assign(Object.create(WorkspaceViews.prototype),{opts:{addChoices:()=>choices},noteButton:{disabled:false},command:vi.fn(),addView:vi.fn(async()=>{}),newNote:vi.fn(async()=>{}),manage:vi.fn(async()=>{})});
 const anchor=focusable('button');
 return {dom,views,anchor,menu:()=>dom.host.querySelector('.workspace-add-menu')};
}

test('"+" offers New Tab and New note, then each extra start by name (All Tabs is ⌄ now); choosing one closes the menu, does it and returns focus',()=>{
 const researcher=vi.fn(),t=addMenuViews([{label:'New researcher',title:'A researcher Tab',act:researcher},{label:'New study…',title:'A question',act:vi.fn()}]);
 t.views.addMenu(t.anchor);
 const menu=t.menu()!;expect(menu.getAttribute('role')).toBe('menu');
 const items=menu.children.filter(item=>item.tagName==='BUTTON');
 expect(items.map(item=>item.textContent)).toEqual(['New Tab⌘T','New note','New researcher','New study…']);
 expect(items.every(item=>item.getAttribute('role')==='menuitem')).toBe(true);
 (items[2] as unknown as {onclick:()=>void}).onclick();
 expect(researcher).toHaveBeenCalledTimes(1);expect(t.views.addView).not.toHaveBeenCalled();
 expect(t.menu()).toBeNull();expect((t.anchor as unknown as {focus:ReturnType<typeof vi.fn>}).focus).toHaveBeenCalled();
});

test('New Tab from the menu goes through the Tab command, so an open researcher Tab is left first; New note follows the note button’s state',()=>{
 const t=addMenuViews([]);t.views.noteButton.disabled=true;t.views.addMenu(t.anchor);
 const items=t.menu()!.children.filter(item=>item.tagName==='BUTTON');
 expect(items.map(item=>item.textContent)).toEqual(['New Tab⌘T','New note']);expect(items[1].disabled).toBe(true);
 (items[0] as unknown as {onclick:()=>void}).onclick();expect(t.views.command).toHaveBeenCalledWith('new');expect(t.views.addView).not.toHaveBeenCalled();
});

test('⌄ All Tabs lists the open Tabs in row order with the one showing, the closed ones with Reopen, studies, then the layout, its recovery copies and Manage Tabs…',()=>{
 const t=addMenuViews([]);vi.stubGlobal('innerWidth',1440);
 vi.spyOn(TestElement.prototype,'matches').mockImplementation(function(this:TestElement,selector:string){const role=/^\[role="?([a-z]+)"?\]$/.exec(selector);if(role)return this.getAttribute('role')===role[1];if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));return this.tagName.toLowerCase()===selector;});
 const tab=(label:string,selected:boolean)=>{const wrap=new TestElement('div');wrap.className='workspace-view-tab';const b=Object.assign(new TestElement('button'),{click:vi.fn()});b.setAttribute('role','tab');b.setAttribute('aria-selected',String(selected));const words=new TestElement('span');words.className='workspace-tab-label';words.textContent=label;b.append(words);wrap.append(b);return {wrap,b};};
 const main=tab('Main',true),researcher=tab('Researcher 3',false),tabs=new TestElement('div');tabs.append(main.wrap,researcher.wrap);
 const reopenStudy=vi.fn(),closedResearcher=vi.fn();
 Object.assign(t.views,{session:'p',tabs,openIds:['main'],layout:{active:'main',views:[{id:'main',title:'Main'},{id:'old',title:'Old counts'}]},status:{textContent:'Saved'},retry:{hidden:true},
  recovery:{list:()=>[]},reopenClosed:vi.fn(async()=>{}),opts:{menuTabs:()=>({closed:[{id:'research:r1',name:'Researcher 1',act:closedResearcher}],studies:[{id:'study:s1',name:'Nuclear size',act:reopenStudy}]}),leaveResearch:()=>true}});
 t.views.allMenu(t.anchor);
 const menu=t.menu()!;expect(menu.getAttribute('aria-label')).toBe('All Tabs');
 const items=menu.children.filter(item=>item.tagName==='BUTTON'),headings=menu.children.filter(item=>item.className==='workspace-menu-heading');
 expect(items.map(item=>item.textContent)).toEqual(['MainShowing','Researcher 3','Old countsReopen','Researcher 1Reopen','Nuclear size','Recovery copiesnone','Manage Tabs…']);
 expect(headings.map(h=>h.textContent)).toEqual(['Open','Closed','Studies','Layout saved']);expect(items[5].disabled).toBe(true);
 (items[1] as unknown as {onclick:()=>void}).onclick();expect(researcher.b.click).toHaveBeenCalled(); // the way a click on it goes
 t.views.allMenu(t.anchor);(t.menu()!.children.filter(item=>item.tagName==='BUTTON')[2] as unknown as {onclick:()=>void}).onclick();expect(t.views.reopenClosed).toHaveBeenCalledWith('old');
 t.views.allMenu(t.anchor);(t.menu()!.children.filter(item=>item.tagName==='BUTTON')[4] as unknown as {onclick:()=>void}).onclick();expect(reopenStudy).toHaveBeenCalled();
});
