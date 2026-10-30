import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),removeDocument:vi.fn(),applyDocument:vi.fn(),document:vi.fn(),clearDocuments:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {WorkspaceViews} from './workspace-views';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());
test.each(['dirty','conflict','changing','navigating','saved'])('scientific preparation requires accepted arrangement: %s',async state=>{
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,
  dirty:state==='dirty',conflict:state==='conflict',changing:state==='changing',navigating:state==='navigating',
  flush:vi.fn(async()=>{})});
 if(state==='saved')await expect(current.prepareScientificView()).resolves.toBeUndefined();
 else await expect(current.prepareScientificView()).rejects.toThrow('layout');
});
test('scientific preparation cannot continue across a Project switch during save',async()=>{
 let finish!:()=>void;
 const pending=new Promise<void>(resolve=>{finish=resolve;});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,
  dirty:false,conflict:false,changing:false,navigating:false,flush:()=>pending});
 const preparing=current.prepareScientificView();current.session='q';current.generation++;finish();
 await expect(preparing).rejects.toThrow('layout');
});
test.each(['document','native','project-changed'])('Reveal raises the exact retained placement: %s',async kind=>{
 const item={resource:{day:'p',id:'d1',version:3,picture:null},placementId:'stable',title:'Data',zoom:1,pan:[.5,.5]};
 const target={id:'target',items:[item],notes:[]},other={id:'other',items:[],notes:[]};
 const frontItem=vi.fn(()=>true),restoreItems=vi.fn(),changed=vi.fn();
 artifacts.applyDocument.mockReset();artifacts.applyDocument.mockReturnValue(kind==='document');
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,revealSequence:0,
  layout:{active:'other',views:[other,target]},openIds:['other'],opts:{frontItem,restoreItems,notice:vi.fn()},changed,
  activate:vi.fn(async(id:string)=>{current.layout.active=id;if(kind==='project-changed'){current.session='q';current.generation++;}})});
 await current.reveal('d1');
 if(kind==='project-changed'){expect(artifacts.applyDocument).not.toHaveBeenCalled();expect(frontItem).not.toHaveBeenCalled();}
 else{
  expect(artifacts.applyDocument).toHaveBeenCalledWith('document:stable',{open:true,front:true});
  if(kind==='native')expect(frontItem).toHaveBeenCalledWith(item);else expect(frontItem).not.toHaveBeenCalled();
 }
 expect(restoreItems).not.toHaveBeenCalled();expect(changed).not.toHaveBeenCalled();
 expect(target.items).toEqual([item]);
});
test('a recovered picture replaces its error placeholder in the saved arrangement',()=>{
 const item={resource:{day:'p',id:'d1',version:0,picture:0},placementId:'stable',title:'Picture',zoom:1,pan:[.5,.5]};
 const placeholder={style:{zIndex:'30'},dataset:{studyRetained:JSON.stringify(item),placementId:'stable',recoveryPlaceholder:'true',project:'p',workspaceView:'main'}};
 const picture={style:{zIndex:'31'},dataset:{studyRetained:JSON.stringify(item),placementId:'stable',project:'p',workspaceView:'main'}};
 const view={items:[],notes:[]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',changing:false,completionRestores:new Set(),active:()=>view,
  specimen:{hidden:true},visibleRoots:()=>[placeholder,picture],opts:{captureItems:()=>[item]},rect:()=>[1,2,300,200]});
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items).toEqual([{...item,position:[1,2],size:[300,200]}]);
 // An unavailable original, or a different revision, never erases the recovery target.
 current.visibleRoots=()=>[placeholder];Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items).toHaveLength(1);
 picture.dataset.studyRetained=JSON.stringify({...item,resource:{...item.resource,version:1}});
 current.visibleRoots=()=>[placeholder,picture];Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items).toHaveLength(2);
});

test.each(['success','failed','thrown','project-changed','tab-changed','dismissed'])('retained retry preserves ownership and recovery: %s',async outcome=>{
 artifacts.removeDocument.mockClear();
 const item={resource:{day:'p',id:'d1',version:0,picture:0},placementId:'stable',title:'Picture',zoom:1,pan:[.5,.5]};
 let complete!:(result:{recovered:boolean;missing:string[]})=>void,fail!:(error:Error)=>void;
 const pending=new Promise<{recovered:boolean;missing:string[]}>((resolve,reject)=>{complete=resolve;fail=reject;});
 const restoreItems=vi.fn(()=>pending),capture=vi.fn(),changed=vi.fn();
 const root={isConnected:true,dataset:{project:'p',workspaceView:'main',placementId:'stable'}};
 const message={textContent:'Original failure'},button={disabled:false,textContent:'Retry retained item'};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout:{active:'main'},opts:{restoreItems},capture,changed});
 const retry=Reflect.get(WorkspaceViews.prototype,'retryItem');
 const job=retry.call(current,item,root,message,button);
 await retry.call(current,item,root,message,button);
 expect(restoreItems).toHaveBeenCalledTimes(1);expect(button.disabled).toBe(true);
 if(outcome==='project-changed'){current.generation++;current.session='other';}
 if(outcome==='tab-changed')current.layout.active='other';
 if(outcome==='dismissed')Object.assign(root.dataset,{dismissed:'true'});
 if(outcome==='thrown')fail(new Error('Reconnect and retry.'));
 else complete({recovered:outcome!=='failed',missing:outcome==='failed'?['Restore the original file.']:[]});
 await job;
 if(outcome==='success'){
  expect(artifacts.removeDocument).toHaveBeenCalledWith(root);expect(capture).toHaveBeenCalledOnce();expect(changed).toHaveBeenCalledOnce();
 }else{expect(artifacts.removeDocument).not.toHaveBeenCalled();expect(changed).not.toHaveBeenCalled();}
 if(outcome==='failed')expect(message.textContent).toBe('Restore the original file.');
 if(outcome==='thrown')expect(message.textContent).toBe('Reconnect and retry.');
 expect(button.disabled).toBe(false);
});
test('a tab switch waiting for a save cannot navigate a newly loaded Project',async()=>{
 vi.stubGlobal('document',{body:{dataset:{}}});
 let finish!:()=>void;
 const pending=new Promise<void>(resolve=>{finish=resolve;});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  generation:1,navigating:false,changing:false,layout:{active:'old'},capture:vi.fn(),flush:()=>pending,
  navSave:vi.fn(),applySpecimen:vi.fn(),render:vi.fn(),visibility:vi.fn(),active:()=>undefined,
 });
 const job=Reflect.get(WorkspaceViews.prototype,'activate').call(current,'old-target');
 current.generation=2;current.layout={active:'new-project-tab'};current.navigating=false;
 finish();await job;
 expect(current.layout.active).toBe('new-project-tab');
 expect(current.navSave).not.toHaveBeenCalled();expect(current.navigating).toBe(false);
});
test('temporary fitting routes native placements through their canonical geometry controller',()=>{
 const item={resource:{day:'p',id:'d1',version:null,picture:0},placementId:'native-one',title:'Picture',zoom:2,pan:[.2,.8]};
 const root={matches:()=>true,dataset:{studyRetained:JSON.stringify(item),placementId:'native-one'}};
 const placeItem=vi.fn(),current=Object.assign(Object.create(WorkspaceViews.prototype),{opts:{placeItem,captureItems:()=>[item]}});
 Reflect.get(WorkspaceViews.prototype,'place').call(current,root,[10,20,300,250]);
 expect(placeItem).toHaveBeenCalledWith({...item,coordinates:'stage',position:[10,20],size:[300,250]});
});
test('Arrange tiles a native window with no retained resource (the live magnifier) through its own geometry',()=>{
 // will-workspace, 30 Oct: the Tab's Arrange tiled the table and the specimen but left the live magnifier where it
 // was (it has no shell of the study artifacts' own), so the table's tile still lay under it: "its windows still overlap".
 const asked:unknown[]=[];vi.stubGlobal('window',{dispatchEvent:(event:{type:string;detail:unknown})=>{asked.push([event.type,event.detail]);return true;}});
 vi.stubGlobal('CustomEvent',class{type:string;detail:unknown;constructor(type:string,init:{detail:unknown}){this.type=type;this.detail=init.detail;}});
 const root={matches:(selector:string)=>selector==='.workspace-window',dataset:{presentationId:'w-magnifier',placementId:'live-one'}};
 const placeItem=vi.fn(),current=Object.assign(Object.create(WorkspaceViews.prototype),{opts:{placeItem,captureItems:()=>[]}});
 Reflect.get(WorkspaceViews.prototype,'place').call(current,root,[12,20,600,400]);
 expect(placeItem).not.toHaveBeenCalled();expect(artifacts.applyDocument).not.toHaveBeenCalled();
 expect(asked).toEqual([['workspace-place-window',{id:'w-magnifier',rect:[12,20,600,400]}]]);
});
test('new notepad opens in its initiating tab before the first save acknowledgement',async()=>{
 let finish!:()=>void;
 const pending=new Promise<void>(resolve=>{finish=resolve;});
 const opened:string[]=[],owners:string[]=[];
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'project-one',layout:{active:'tab-one'},openIds:['tab-one'],active:()=>({id:'tab-one'}),
  recovery:{put:vi.fn()},notes:new Map(),visibleRoots:()=>[],
  openNote:vi.fn(async()=>{opened.push(current.layout.active);return true;}),
  saveNote:vi.fn((note:{session:string})=>{owners.push(note.session);return pending;}),
 });
 const job=Reflect.get(WorkspaceViews.prototype,'newNote').call(current);
 expect(opened).toEqual(['tab-one']);expect(owners).toEqual(['project-one']);
 current.session='project-two';current.layout.active='tab-two';finish();await job;
 expect(opened).toEqual(['tab-one']);
});

test('remembered presentation is confirmed by accepted layout, with failed writes retained locally',async()=>{
 const {default:cases}=await import('../../../contract/scientific-view-preferences-cases.json');
 const resource={day:'project-one',id:'p'+'a'.repeat(32),version:2,picture:null};
 const item={resource,placementId:'window-one',title:'Data',zoom:1,pan:[.5,.5]};
 const root={dataset:{studyRetained:JSON.stringify(item),placementId:'window-one',project:'project-one'}};
 const layout={schema:2,coordinates:'stage',active:'main',views:[{id:'main',items:[item]}]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'project-one',generation:1,layout,changing:false,navigating:false,conflict:false,
  capture:vi.fn(),roots:()=>[root],changed:vi.fn(),lastSaved:JSON.stringify(layout),flush:vi.fn(async()=>{}),
 });
 await expect(current.rememberView('project-one','window-one',resource,cases.valid)).rejects.toThrow('saved in the layout yet');
 expect(current.rememberedView('project-one','window-one',resource)).toBeNull();
 expect(JSON.parse(root.dataset.studyRetained).display.scientific).toEqual(cases.valid);
 current.flush.mockImplementation(async()=>{current.lastSaved=JSON.stringify(current.layout);});
 await current.rememberView('project-one','window-one',resource,cases.valid);
 expect(current.layout.schema).toBe(3);
 expect(current.rememberedView('project-one','window-one',resource)).toEqual(cases.valid);
 expect(current.rememberedView('project-one','window-one',{...resource,version:3})).toBeNull();
 await current.rememberView('project-one','window-one',resource,null);
 expect(current.rememberedView('project-one','window-one',resource)).toBeNull();
});

test('a late native placement cannot be adopted by a different Project',()=>{
 const root={dataset:{project:'original',workspaceView:'original-tab',placementId:'stable'},querySelector:()=>null};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'new',active:()=>({id:'new-tab'}),capture:vi.fn(),changed:vi.fn(),updateTransfer:vi.fn(),visibility:vi.fn()});
 Reflect.get(WorkspaceViews.prototype,'adopt').call(current,root);
 expect(root.dataset).toEqual({project:'original',workspaceView:'original-tab',placementId:'stable',workspaceHidden:'true'});
 expect(current.changed).not.toHaveBeenCalled();
});

test('matching Tab names never make a different Project window visible or capturable',()=>{
 const other={dataset:{project:'other',workspaceView:'main'}},own={dataset:{project:'here',workspaceView:'main'}};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'here',layout:{active:'main'},openIds:['main'],roots:()=>[other,own]});
 expect(Reflect.get(WorkspaceViews.prototype,'visibleRoots').call(current)).toEqual([own]);
});

 test('a slow Reveal cannot raise its window after a newer Reveal',async()=>{
 const item=(id:string)=>({resource:{day:'p',id,version:1,picture:null},placementId:id,title:id,zoom:1,pan:[.5,.5]});
 const first=item('first'),second=item('second');let complete!:(value:unknown)=>void;
 const pending=new Promise(resolve=>{complete=resolve;});
 artifacts.applyDocument.mockReset();artifacts.applyDocument.mockReturnValue(false);
 const frontItem=vi.fn((item:{placementId:string})=>item.placementId==='second');
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,revealSequence:0,
  layout:{active:'main',views:[{id:'main',items:[first,second],notes:[]}]},openIds:['main'],
  opts:{frontItem,restoreItems:vi.fn(()=>pending),notice:vi.fn()},activate:vi.fn(async()=>{})});
 const old=current.reveal('first');await Promise.resolve();
 await current.reveal('second');complete({recovered:true,missing:[]});await old;
 expect(frontItem.mock.calls.map(call=>call[0].placementId)).toEqual(['first','second']);
 expect(artifacts.applyDocument.mock.calls.map(call=>call[0])).toEqual(['document:first','document:second']);
});


test('explicit completed-result Open retains foreground intent',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const dialog=Object.assign(new TestElement('dialog'),{close:vi.fn()});
 const restoreItems=vi.fn(),item={resource:{day:'p',id:'d1',version:1,picture:null},title:'Completed',zoom:1,pan:[.5,.5]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'p',refresh:async()=>{},dialog:()=>dialog,undo:null,layout:{views:[]},removed:[],pending:[],
  recovery:{noteIds:()=>[]},completions:[{state:'retained',item}],active:()=>({id:'main'}),opts:{restoreItems},
 });
 await Reflect.get(WorkspaceViews.prototype,'manage').call(current);
 const open=dialog.querySelectorAll('button').find(button=>button.textContent==='Open in this Tab')!;
 Reflect.get(open,'onclick')();
 expect(restoreItems).toHaveBeenCalledWith([expect.objectContaining({resource:item.resource,coordinates:'stage'})],{background:false});
 expect(dialog.close).toHaveBeenCalledOnce();
});

test('restored placeholders and notes stay in the background',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 artifacts.document.mockClear();const openNote=vi.fn(async()=>true);
 const item={resource:{day:'p',id:'d1',version:1,picture:null},placementId:'saved',title:'Missing',zoom:1,pan:[.5,.5]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  generation:1,navigating:false,changing:false,layout:{active:'main',views:[]},loaded:new Set(),
  navSave:vi.fn(),applySpecimen:vi.fn(),render:vi.fn(),visibility:vi.fn(),visibleRoots:()=>[],
  active:()=>({items:[item],notes:[{id:'n1',rect:[1,2,300,200],placementId:'note-place'}]}),
  opts:{restoreItems:async()=>({recovered:false,missing:['File unavailable']})},openNote,
 });
 await Reflect.get(WorkspaceViews.prototype,'activate').call(current,'main',true);
 expect(artifacts.document).toHaveBeenCalledWith('missing:saved','Missing',expect.anything(),expect.any(Function),
  expect.objectContaining({placementId:'saved',recoveryPlaceholder:'true'}),{background:true});
 expect(openNote).toHaveBeenCalledWith('n1',[1,2,300,200],'note-place',{background:true});
});

test.each([true,false])('existing note restoration preserves focus; explicit opening focuses it (background %s)',async background=>{
 const focus=vi.fn(),root={dataset:{placementId:'note-place'},querySelector:()=>({focus})};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'p',generation:1,layout:{active:'main'},active:()=>({id:'main'}),notes:new Map([['n1',{}]]),visibleRoots:()=>[root],
 });
 await current.openNote('n1',undefined,'note-place',{background});
 expect(focus).toHaveBeenCalledTimes(background?0:1);
});

test.each(['Project','Tab'])('a delayed missing note does not mount in a newer %s',async changed=>{
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve;});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'p',generation:1,layout:{active:'main'},active:()=>({id:'main'}),notes:new Map(),
  opts:{request:()=>pending},recovery:{list:vi.fn()},visibleRoots:vi.fn(),
 });
 const opening=current.openNote('n1',undefined,'note-place',{background:true});
 if(changed==='Project'){current.session='q';current.generation++;}else current.layout.active='other';
 finish({notes:[{id:'n1',title:'Original',text:'Kept',version:1}]});
 await expect(opening).resolves.toBe(false);expect(current.notes.size).toBe(0);
 expect(current.visibleRoots).not.toHaveBeenCalled();
});

test('a late restore failure cannot create its placeholder in a new Project',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);artifacts.document.mockClear();
 let finish!:(value:unknown)=>void;const pending=new Promise(resolve=>{finish=resolve;});
 const item={resource:{day:'p',id:'d1',version:1,picture:null},title:'Missing',zoom:1,pan:[.5,.5]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  generation:1,navigating:false,changing:false,layout:{active:'main',views:[]},loaded:new Set(),
  navSave:vi.fn(),applySpecimen:vi.fn(),render:vi.fn(),visibility:vi.fn(),
  active:()=>({items:[item],notes:[]}),opts:{restoreItems:()=>pending},
 });
 const activation=Reflect.get(WorkspaceViews.prototype,'activate').call(current,'main',true);
 current.generation++;current.layout.active='new';finish({recovered:false,missing:['Old failure']});await activation;
 expect(artifacts.document).not.toHaveBeenCalled();
});

test.each(['same','new-project','newer-reveal'])('Reveal during restoration retains only current explicit intent: %s',async change=>{
 vi.stubGlobal('document',{body:{dataset:{}}});
 const item={resource:{day:'p',id:'d1',version:0,picture:null},placementId:'stable',title:'Data',zoom:1,pan:[.5,.5]};
 let finish!:(value:unknown)=>void;
 const wait=new Promise(resolve=>{finish=resolve;});
 const view={id:'main',items:[item],notes:[]};
 artifacts.applyDocument.mockReset();artifacts.applyDocument.mockReturnValue(true);
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,revealSequence:0,navigating:false,changing:false,
  layout:{active:'main',views:[view]},openIds:['main'],loaded:new Set(),active:()=>view,
  navSave:vi.fn(),applySpecimen:vi.fn(),render:vi.fn(),visibility:vi.fn(),visibleRoots:()=>[],capture:vi.fn(),flush:async()=>{},
  opts:{restoreItems:()=>wait,frontItem:vi.fn(),notice:vi.fn()}});
 const opening=Reflect.get(WorkspaceViews.prototype,'activate').call(current,'main',true);
 await current.reveal('d1');expect(artifacts.applyDocument).not.toHaveBeenCalled();
 if(change==='new-project'){current.generation++;current.session='q';}
 if(change==='newer-reveal')current.revealSequence++;
 finish({recovered:true,missing:[]});await opening;await Promise.resolve();await Promise.resolve();
 if(change==='same')expect(artifacts.applyDocument).toHaveBeenCalledWith('document:stable',{open:true,front:true});
 else expect(artifacts.applyDocument).not.toHaveBeenCalled();
});

test.each(['success','transport','project-during-read','project-during-write'])('recovery keeps its initiating Project and permits one attempt: %s',async outcome=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 let resolveRead!:(value:any)=>void,resolveWrite!:(value:any)=>void;
 const read=new Promise(resolve=>{resolveRead=resolve;}),write=new Promise(resolve=>{resolveWrite=resolve;});
 const request=vi.fn().mockImplementationOnce(()=>read).mockImplementationOnce(()=>write);
 const status=Object.assign(new TestElement('p'),{scrollIntoView:vi.fn()});
 const dialog=Object.assign(new TestElement('dialog'),{close:vi.fn(),querySelector:()=>status});
 const candidate={value:{layout:{schema:3,views:[{id:'a',title:'Analysis',items:[],notes:[]}]}}};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'original',generation:1,recoveryBusy:false,
  opts:{request},recovery:{clear:vi.fn(),replace:vi.fn()},load:vi.fn()});
 const recover=Reflect.get(WorkspaceViews.prototype,'recoverTabs');
 const first=recover.call(current,candidate,new Set(['a']),dialog);
 await recover.call(current,candidate,new Set(['a']),dialog);
 expect(request).toHaveBeenCalledTimes(1);
 if(outcome==='project-during-read'){current.session='other';current.generation++;}
 resolveRead({ok:true,revision:4,layout:{schema:3,views:[]}});await Promise.resolve();await Promise.resolve();
 if(outcome==='project-during-read'){
  await first;expect(request).toHaveBeenCalledTimes(1);expect(current.recovery.clear).not.toHaveBeenCalled();
 }else{
  expect(request.mock.calls[1][1].session).toBe('original');
  if(outcome==='project-during-write'){current.session='other';current.generation++;}
  resolveWrite(outcome==='transport'?{ok:false,error:'Connection lost. Retry.'}:{ok:true});await first;
  if(outcome==='success'){expect(current.recovery.clear).toHaveBeenCalledOnce();expect(current.load).toHaveBeenCalledWith('original');}
  else{expect(current.recovery.clear).not.toHaveBeenCalled();expect(current.load).not.toHaveBeenCalled();}
 }
 expect(current.recoveryBusy).toBe(false);
});

test('failed recovery retains its copy and selection for an explicit successful retry',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const status=Object.assign(new TestElement('p'),{scrollIntoView:vi.fn()});
 const dialog=Object.assign(new TestElement('dialog'),{close:vi.fn(),querySelector:()=>status});
 const request=vi.fn().mockRejectedValueOnce(new Error('Connection lost'))
  .mockResolvedValueOnce({ok:true,revision:4,layout:{schema:3,views:[]}}).mockResolvedValueOnce({ok:true});
 const candidate={value:{layout:{schema:3,views:[{id:'a',title:'Analysis',items:[],notes:[]}]}}},selected=new Set(['a']);
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'original',generation:1,recoveryBusy:false,
  opts:{request},recovery:{clear:vi.fn(),replace:vi.fn()},load:vi.fn()});
 const recover=Reflect.get(WorkspaceViews.prototype,'recoverTabs');
 await recover.call(current,candidate,selected,dialog);
 expect(current.recovery.clear).not.toHaveBeenCalled();expect(dialog.close).not.toHaveBeenCalled();
 expect(status.textContent).toContain('Check saved tabs before retrying');expect(selected).toEqual(new Set(['a']));
 await recover.call(current,candidate,selected,dialog);
 expect(current.recovery.clear).toHaveBeenCalledWith(candidate);expect(dialog.close).toHaveBeenCalledOnce();
 expect(request.mock.calls[2][1].layout.views).toHaveLength(1);
});

test('successful recovery opens its new tabs and reveals the first without hiding existing tabs',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const status=Object.assign(new TestElement('p'),{scrollIntoView:vi.fn()});
 const dialog=Object.assign(new TestElement('dialog'),{close:vi.fn(),querySelector:()=>status});
 const layout={schema:3,views:[{id:'existing',title:'Existing',items:[],notes:[]}]};
 const candidate={value:{layout:{schema:3,views:[{id:'a',title:'Analysis',items:[],notes:[]}]}}};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'original',generation:1,recoveryBusy:false,openIds:['existing'],layout,
  opts:{request:vi.fn().mockResolvedValueOnce({ok:true,revision:4,layout}).mockResolvedValueOnce({ok:true})},
  recovery:{clear:vi.fn()},load:vi.fn(async()=>{current.generation++;}),activate:vi.fn(),render:vi.fn()});
 await Reflect.get(WorkspaceViews.prototype,'recoverTabs').call(current,candidate,new Set(['a']),dialog);
 const recovered=layout.views[1];expect(recovered.title).toBe('Analysis (recovered)');
 expect(current.openIds).toEqual(['existing',recovered.id]);expect(current.activate).toHaveBeenCalledWith(recovered.id);
});

test.each(['dirty','saving','conflict','changing','navigating','note-dirty','note-saving','note-conflict','note-error','saved'])('update restart admission preserves %s work',state=>{
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  dirty:state==='dirty',saving:state==='saving'?Promise.resolve():null,conflict:state==='conflict',
  changing:state==='changing',navigating:state==='navigating',notes:new Map(),
  noteProjects:new Map([['earlier-project',new Map([['note',{status:state.startsWith('note-')?state.slice(5):'saved'}]])]])});
 expect(current.updateRestartBlockers().length).toBe(state==='saved'?0:1);
});

test.each(['loaded','failed'])('a Project opening shows no other Project\'s tabs while its arrangement loads: %s',async outcome=>{
 vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag),body:{dataset:{} as Record<string,string>}});
 vi.stubGlobal('window',{dispatchEvent:vi.fn()});vi.stubGlobal('CustomEvent',class{type:string;constructor(type:string){this.type=type;}});
 vi.stubGlobal('sessionStorage',{getItem:()=>null});
 const tabs=new TestElement('div'),status=new TestElement('span');tabs.append(new TestElement('button'));tabs.children[0].textContent='Tab of the previous Project';
 let answer!:(reply:Record<string,unknown>)=>void;const reply=new Promise<Record<string,unknown>>(resolve=>{answer=resolve;});
 const old=new TestElement('section');old.dataset.project='previous';
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'previous',generation:1,notes:new Map(),noteProjects:new Map(),loaded:new Set(),
  flashNote:new TestElement('div'),strip:new TestElement('nav'),tabs,status,retry:new TestElement('button'),layout:{active:'main',views:[]},roots:()=>[old],navKey:()=>'k',
  opts:{request:(type:string)=>type==='workspace-layout'?reply:Promise.resolve({ok:true,notes:[]}),notice:vi.fn()},
  accept:vi.fn(function(this:{layout:unknown}){this.layout={active:'main',views:[{id:'main',title:'Main',items:[],notes:[]}]};}),
  activate:vi.fn(async()=>{tabs.replaceChildren(Object.assign(new TestElement('button'),{textContent:'Main'}));}),updateRecovery:vi.fn(),clearFlash:vi.fn()});
 const loading=current.load('next');
 expect(tabs.textContent).toBe('Opening…');expect(document.body.dataset.workspaceOpening).toBe('true');
 expect(old.dataset.workspaceHidden).toBe('true');
 expect(JSON.parse(current.strip.dataset.opening)).toEqual({session:'next',generation:2});
 answer(outcome==='loaded'?{ok:true}:{ok:false,error:'unreadable'});await loading;
 expect(tabs.textContent).toBe(outcome==='loaded'?'Main':'Not opened');
 expect(current.clearFlash).toHaveBeenCalled(); // the old Project's toast never speaks in this one
 expect(document.body.dataset.workspaceOpening).toBeUndefined();
});
test('pausing for a Project switch hides the old tabs; a refused switch shows them again',()=>{
 vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag),body:{dataset:{} as Record<string,string>}});
 vi.stubGlobal('window',{dispatchEvent:vi.fn()});vi.stubGlobal('CustomEvent',class{type:string;constructor(type:string){this.type=type;}});
 const tabs=new TestElement('div'),status=new TestElement('span');
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{tabs,status,timer:0,changing:false,
  render:vi.fn(()=>tabs.replaceChildren(Object.assign(new TestElement('button'),{textContent:'Main'}))),visibility:vi.fn()});
 current.pause();
 expect(tabs.textContent).toBe('Opening…');expect(current.changing).toBe(true);expect(document.body.dataset.workspaceOpening).toBe('true');
 current.resume();
 expect(tabs.textContent).toBe('Main');expect(current.changing).toBe(false);expect(document.body.dataset.workspaceOpening).toBeUndefined();
});

test('saved document order follows the visible stack, not its creation order',()=>{
 const view={items:[],notes:[],specimen:{}};
 const roots=['source','reader','output'].map((name,i)=>{
  const root=new TestElement('section');
  root.dataset.placementId=name;
  root.dataset.studyRetained=JSON.stringify({title:name,resource:{day:'project',id:name,version:1,picture:null},zoom:1,pan:[.5,.5]});
  root.style.zIndex=String([32,30,31][i]);return root;
 });
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'project',changing:false,
  active:()=>view,rememberInput:()=>false,specimen:{hidden:true},opts:{captureItems:()=>[]},
  completionRestores:new Set(),visibleRoots:()=>roots,rect:()=>[0,0,420,380]});
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);
 expect(view.items.map((item:{title:string})=>item.title)).toEqual(['reader','output','source']);
});


test('Data reveal targets the exact revision and Tab and rejects an old Project opening',async()=>{
 const old={resource:{day:'p',id:'d1',version:1,picture:null},placementId:'old',title:'Old',zoom:1,pan:[.5,.5]};
 const exact={...old,resource:{...old.resource,version:2},placementId:'exact'};
 const target={id:'target',items:[old,exact],notes:[]};
 artifacts.applyDocument.mockReset();artifacts.applyDocument.mockReturnValue(true);
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,revealSequence:0,
  layout:{active:'target',views:[target]},openIds:['target'],opts:{notice:vi.fn()},activate:vi.fn(async()=>{})});
 await current.reveal('d1','target',2,{session:'p',generation:1});
 expect(artifacts.applyDocument).toHaveBeenCalledWith('document:exact',{open:true,front:true});
 artifacts.applyDocument.mockClear();
 await current.reveal('d1','missing',2);await current.reveal('d1','target',3);
 await current.reveal('d1','target',2,{session:'p',generation:0});
 expect(artifacts.applyDocument).not.toHaveBeenCalled();
 current.navigating=true;await current.reveal('d1','target',2);
 current.navigating=false;Reflect.get(WorkspaceViews.prototype,'resumeReveal').call(current);await Promise.resolve();await Promise.resolve();
 expect(artifacts.applyDocument).toHaveBeenCalledWith('document:exact',{open:true,front:true});
});

test('Reveal from an In pill brings that revision forward, not another revision of the same result',async()=>{
 const r1={resource:{day:'p',id:'d1',version:1,picture:null},placementId:'old',title:'Data',zoom:1,pan:[.5,.5]};
 const r3={resource:{day:'p',id:'d1',version:3,picture:null},placementId:'new',title:'Data',zoom:1,pan:[.5,.5]};
 artifacts.applyDocument.mockReset();artifacts.applyDocument.mockReturnValue(true);
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,revealSequence:0,
  layout:{active:'t',views:[{id:'t',items:[r1,r3],notes:[]}]},openIds:['t'],opts:{frontItem:vi.fn(),restoreItems:vi.fn(),notice:vi.fn()},changed:vi.fn(),activate:vi.fn(async()=>{})});
 await current.reveal('d1','t',3);
 expect(artifacts.applyDocument).toHaveBeenCalledWith('document:new',{open:true,front:true});
});


test('recovering a compare Tab remaps its lane references and preserves accepted result revisions',async()=>{
 const status=Object.assign(new TestElement('p'),{scrollIntoView:vi.fn()});
 const dialog=Object.assign(new TestElement('dialog'),{close:vi.fn(),querySelector:()=>status});
 const old={id:'comparison',title:'Comparison',kind:'compare',specimen:{visible:true,rect:null},notes:[],
  items:[{placementId:'pa',resource:{day:'original',id:'a',version:2}},{placementId:'pb',resource:{day:'original',id:'b',version:3}}],
  compare:{sync:true,lanes:[{placementId:'pa',label:'A',impl:'first'},{placementId:'pb',label:'B',impl:'second'}]}};
 const layout={schema:5,views:[] as typeof old[]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'original',generation:1,recoveryBusy:false,openIds:[],layout,
  opts:{request:vi.fn().mockResolvedValueOnce({ok:true,revision:4,layout}).mockResolvedValueOnce({ok:true})},
  recovery:{clear:vi.fn()},load:vi.fn(async()=>{current.generation++;}),activate:vi.fn(),render:vi.fn()});
 await Reflect.get(WorkspaceViews.prototype,'recoverTabs').call(current,{value:{layout:{schema:5,views:[old]}}},new Set(['comparison']),dialog);
 const recovered=layout.views[0];
 expect(recovered.compare.lanes.map(l=>l.placementId)).toEqual(recovered.items.map(i=>i.placementId));
 expect(recovered.items.map(i=>i.resource)).toEqual(old.items.map(i=>i.resource));
 expect(old.compare.lanes.map(l=>l.placementId)).toEqual(['pa','pb']);
});

test('table Save advances only its exact original placement and refuses stale references',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const previous={day:'p',id:'tb1',version:1,picture:null},next={...previous,version:2};
 const item={placementId:'kept',resource:previous,title:'Table'},copy={...item,placementId:'copy'};
 const root={dataset:{project:'p',placementId:'kept',studyRetained:JSON.stringify(item)}};
 const changed=vi.fn(),flush=vi.fn();
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,changing:false,navigating:false,conflict:false,dirty:false,
 layout:{views:[{items:[item,copy]}]},capture:vi.fn(),roots:()=>[root],changed,flush});
 await current.rememberResource('p','kept',previous,next,'Edited table');
 expect(item.resource).toEqual(next);expect(copy.resource).toEqual(previous);expect(JSON.parse(root.dataset.studyRetained).resource).toEqual(next);
 await expect(current.rememberResource('p','kept',previous,{...next,version:3},'Stale')).rejects.toThrow('unavailable');
 expect(changed).toHaveBeenCalledOnce();expect(flush).toHaveBeenCalledOnce();
 current.generation++;flush.mockImplementationOnce(async()=>{current.session='q';});
 await expect(current.rememberResource('p','kept',next,{...next,version:3},'Next')).rejects.toThrow('Table saved in Data');
});
