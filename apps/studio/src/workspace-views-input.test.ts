import {CameraInputUnavailable} from './workspace-input';
import {afterEach,expect,test,vi} from 'vitest';
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({blockReason:()=>null})}));
import {WorkspaceViews} from './workspace-views';
import {mainLayout,type WorkspaceTabInput} from './workspace-views-model';

afterEach(()=>vi.unstubAllGlobals());
function workspace(){
 vi.stubGlobal('document',{body:{dataset:{}}});vi.stubGlobal('window',new EventTarget());
 let selected:WorkspaceTabInput={source:'live',position:0};
 const layout=mainLayout();
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{
  session:'project',generation:1,layout,loaded:new Set(['main']),openIds:['main'],
  navigating:false,changing:false,dirty:false,conflict:false,
  specimen:{hidden:true},completionRestores:new Set(),visibleRoots:()=>[],
  changed:vi.fn(()=>{current.dirty=true;}),flush:vi.fn(async()=>{current.dirty=false;}),
  tabs:{querySelector:()=>null},navSave:vi.fn(),applySpecimen:vi.fn(),render:vi.fn(),visibility:vi.fn(),resumeReveal:vi.fn(),offerReopen:vi.fn(),clearFlash:vi.fn(),closedStack:[],
  opts:{captureInput:()=>({...selected}),captureItems:()=>[],notice:vi.fn(),
   selectInput:vi.fn(async(input:WorkspaceTabInput)=>{selected={...input};}),inputBlockReason:()=>null},
 });
 return {current,select:(input:WorkspaceTabInput)=>{selected=input;},selected:()=>selected};
}
test('opening a recording preserves Main live input and reuses its saved Tab',async()=>{
 const {current,selected}=workspace();
 await current.openInput({source:'a7',position:0},'Recording');
 expect(current.layout.views[0].input).toEqual({source:'live',position:0});
 expect(current.layout.views).toHaveLength(2);
 expect(current.layout.views[1]).toMatchObject({title:'Recording',input:{source:'a7',position:0}});
 expect(selected().source).toBe('a7');
 await current.openInput({source:'live',position:0},'Live camera');
 expect(current.layout.active).toBe('main');expect(selected().source).toBe('live');
 await current.openInput({source:'a7',position:0},'Recording');
 expect(current.layout.views).toHaveLength(2);
});
test('switching back restores the recording position without changing retained results',async()=>{
 const {current,select}=workspace();
 await current.openInput({source:'a7',position:0},'Recording');
 const recording=current.layout.views[1];
 recording.items.push({resource:{day:'project',id:'e1',version:2,picture:0},title:'Saved result',placementId:'result'});
 const retained=recording.items[0];
 current.visibleRoots=()=>recording.id===current.layout.active?[{dataset:{studyRetained:JSON.stringify(retained),placementId:'result'}}]:[];
 current.rect=()=>[0,0,100,100];
 select({source:'a7',position:8.5});
 await current.openInput({source:'live',position:0},'Live camera');
 await current.openInput({source:'a7',position:0},'Recording');
 expect(current.opts.selectInput).toHaveBeenLastCalledWith({source:'a7',position:8.5},'project');
 expect(recording.items[0].resource).toEqual({day:'project',id:'e1',version:2,picture:0});
});
test('a running operation refuses input changes without adding or activating another Tab',async()=>{
 const {current}=workspace();current.opts.inputBlockReason=()=> 'This run keeps its captured input.';
 await expect(current.openInput({source:'a7',position:0},'Recording')).rejects.toThrow('captured input');
 expect(current.layout.views).toHaveLength(1);expect(current.layout.active).toBe('main');
 expect(current.opts.selectInput).not.toHaveBeenCalled();
});
test('failed layout persistence never switches the source',async()=>{
 const {current}=workspace();current.flush.mockImplementation(async()=>{});
 await expect(current.openInput({source:'a7',position:0},'Recording')).rejects.toThrow('layout');
 expect(current.layout.active).toBe('main');expect(current.opts.selectInput).not.toHaveBeenCalled();
});
test('failed source selection keeps the previous Tab and prevents wrong-input admission',async()=>{
 const {current}=workspace();current.opts.selectInput.mockRejectedValue(new Error('Source missing'));
 await current.openInput({source:'a7',position:0},'Recording');
 expect(current.layout.active).toBe('main');expect(current.navigating).toBe(false);
 expect(document.body.dataset.workspaceInputUnavailable).toBe('true');
 expect(current.opts.notice).toHaveBeenCalledWith(expect.objectContaining({message:'Source missing'}));
 expect(current.layout.views[1].input.source).toBe('a7');
});
test('a late source acknowledgement cannot activate a Tab in another Project',async()=>{
 const {current}=workspace();let finish!:()=>void;
 current.opts.selectInput.mockImplementation(()=>new Promise<void>(resolve=>{finish=resolve;}));
 const opening=current.openInput({source:'a7',position:0},'Recording');
 await vi.waitFor(()=>expect(current.opts.selectInput).toHaveBeenCalledOnce());
 current.generation++;current.session='other';current.layout=mainLayout();current.navigating=false;
 finish();await opening;
 expect(current.layout.active).toBe('main');expect(current.layout.views).toHaveLength(1);
 expect(current.navSave).not.toHaveBeenCalled();
});
test('completion refresh on the same input does not rewind or reselect it',async()=>{
 const {current,select}=workspace();
 await current.openInput({source:'a7',position:0},'Recording');
 select({source:'a7',position:5});current.opts.selectInput.mockClear();
 await Reflect.get(WorkspaceViews.prototype,'activate').call(current,current.layout.active,true);
 expect(current.opts.selectInput).not.toHaveBeenCalled();
});

test('closing a recording Tab preserves its retained item and note through the next save',async()=>{
 const {current}=workspace();
 await current.openInput({source:'a7',position:0},'Recording');
 const recording=current.layout.views[1];
 const item={resource:{day:'project',id:'e1',version:2,picture:0},title:'Saved result',placementId:'result'};
 const roots=[{style:{zIndex:'1'},dataset:{studyRetained:JSON.stringify(item),placementId:'result'}},{style:{zIndex:'2'},dataset:{noteId:'note',placementId:'note-place'}}];
 current.visibleRoots=()=>recording.id===current.layout.active&&current.openIds.includes(recording.id)?roots:[];
 current.rect=()=>[0,0,100,100];
 await Reflect.get(WorkspaceViews.prototype,'closeTab').call(current,recording.id);
 expect(current.layout.active).toBe('main');expect(current.openIds).toEqual(['main']);
 Reflect.get(WorkspaceViews.prototype,'capture').call(current);await current.flush();
 const saved=JSON.parse(JSON.stringify(current.layout)).views[1];
 expect(saved.items[0].resource).toEqual(item.resource);expect(saved.notes[0].id).toBe('note');
});
test('refused fallback source leaves the closing Tab visible and selected',async()=>{
 const {current}=workspace();await current.openInput({source:'a7',position:0},'Recording');
 const recording=current.layout.active;current.opts.inputBlockReason=()=> 'End voice before changing the input.';
 await Reflect.get(WorkspaceViews.prototype,'closeTab').call(current,recording);
 expect(current.layout.active).toBe(recording);expect(current.openIds).toContain(recording);
});
test('duplicate Open cannot create concurrent input Tabs while saving',async()=>{
 const {current}=workspace();let finish!:()=>void;
 current.flush.mockImplementationOnce(()=>new Promise<void>(resolve=>{finish=()=>{current.dirty=false;resolve();};}));
 const opening=current.openInput({source:'a7',position:0},'Recording');
 await expect(current.openInput({source:'a8',position:0},'Other recording')).rejects.toThrow('input is opening');
 finish();await opening;expect(current.layout.views).toHaveLength(2);
});

test('Main is home: closing it is refused, so it stays open with its arrangement, input and specimen',async()=>{
 const {current}=workspace();current.observeInput();await current.flush();
 const retained={resource:{day:'project',id:'e1',version:2,picture:0},title:'Saved',placementId:'kept'};
 current.visibleRoots=()=>current.layout.active==='main'&&current.openIds.includes('main')?[{dataset:{studyRetained:JSON.stringify(retained),placementId:'kept'}}]:[];
 current.rect=()=>[4,5,320,240];
 await Reflect.get(WorkspaceViews.prototype,'closeTab').call(current,'main');
 expect(current.openIds).toEqual(['main']);expect(current.layout.active).toBe('main');
 const saved=JSON.parse(JSON.stringify(current.layout)).views[0];
 expect(saved.input).toEqual({source:'live',position:0});expect(saved.specimen.visible).toBe(true);
});

test('legacy closed Tabs keep their shared source before the first new input Tab',async()=>{
 const {current,select}=workspace();
 current.layout.active='';current.openIds=[];select({source:'a3',position:12});
 current.layout.views[0].notes=[{id:'note',placementId:'kept',rect:[1,2,300,200]}];
 await current.openInput({source:'a7',position:0},'Recording');
 const saved=JSON.parse(JSON.stringify(current.layout));
 expect(saved.views[0].input).toEqual({source:'a3',position:12});
 expect(saved.views[0].notes[0].placementId).toBe('kept');
 await current.openInput({source:'a3',position:0},'Old input');
 expect(current.opts.selectInput).toHaveBeenLastCalledWith({source:'a3',position:12},'project');
});

test('a closed Tab says so with Reopen, where its content lives, which reopens that same Tab; another Project clears it',async()=>{
 const {current}=workspace();vi.stubGlobal('window',Object.assign(new EventTarget(),{setTimeout:()=>7}));
 await current.openInput({source:'a7',position:0},'Recording');const recording=current.layout.views[1];
 const node=()=>({hidden:true,textContent:'',onclick:null as null|(()=>void),attributes:{} as Record<string,string>,setAttribute(k:string,v:string){this.attributes[k]=v;}});
 Object.assign(current,{closedNote:node(),reopen:node(),closedToast:node(),closedId:'',closedTimer:0});
 Reflect.get(WorkspaceViews.prototype,'offerReopen').call(current,recording.id);
 // A Tab with an input says its input is in Data (its copy flies there); one with results says they are.
 expect(current.closedToast.hidden).toBe(false);expect(current.closedNote.textContent).toBe(`Closed ${recording.title}. Its input is in Data.`);
 expect(current.reopen.attributes['aria-label']).toBe(`Reopen ${recording.title}`);
 current.openIds=['main'];current.activate=vi.fn(async()=>true);
 current.reopen.onclick!();await Promise.resolve();await Promise.resolve();
 expect(current.openIds).toEqual(['main',recording.id]);expect(current.activate).toHaveBeenCalledWith(recording.id);
 expect(current.closedToast.hidden).toBe(true);
 recording.items.push({resource:{day:'project',id:'e1',version:1,picture:0},title:'Masks',placementId:'p'} as never);
 Reflect.get(WorkspaceViews.prototype,'offerReopen').call(current,recording.id);expect(current.closedNote.textContent).toBe(`Closed ${recording.title}. Its results are in Data.`);
 Reflect.get(WorkspaceViews.prototype,'offerReopen').call(current,'main');expect(current.closedNote.textContent).toBe('Closed Main.');
});

test('Open in Tab… reaches a Tab only when it really comes into view, reopening a closed one', async () => {
 const {current}=workspace();
 await current.openInput({source:'a7',position:0},'Recording');const recording=current.layout.views[1];
 expect(current.tabChoices().map((t:{title:string;current:boolean})=>`${t.title}${t.current?'*':''}`)).toEqual(['Main','Recording*']);
 expect(await current.goTo('missing')).toBe(false);
 expect(await current.goTo(recording.id)).toBe(true); // already in view
 current.openIds=['main'];current.layout.active='main';
 current.activate=vi.fn(async(id:string)=>{current.layout.active=id;return true;});
 expect(await current.goTo(recording.id)).toBe(true);expect(current.openIds).toEqual(['main',recording.id]);
 current.activate=vi.fn(async()=>undefined); // refused (unsaved arrangement, input blocked…): stays where it was
 expect(await current.goTo('main')).toBe(false);
});


test('Open in Tab refuses stale Project openings and cannot add a Tab after a Project changes during save', async()=>{
 const {current}=workspace();const owner=current.tabOwner();
 current.openIds=[];
 current.activate=vi.fn(async()=>{current.session='other-project';current.generation++;current.layout=mainLayout();current.openIds=['main'];});
 expect(await current.goTo('main',owner)).toBe(false); // same tab id in a different Project is not success.
 expect(await current.goTo('main',owner)).toBe(false);
 current.session=owner.session;current.generation++; // leaving and returning still invalidates the chooser.
 expect(await current.goTo('main',owner)).toBe(false);
 current.navigating=true;expect(await current.goTo('main')).toBe(false);current.navigating=false;
 current.capture=vi.fn();
 current.flush=vi.fn(async()=>{current.session='third-project';current.generation++;current.layout=mainLayout();current.openIds=['main'];});
 expect(await current.newTab()).toBe(false);
 expect(current.layout.views).toHaveLength(1);expect(current.layout.active).toBe('main');
});


test('Open in Tab refuses pending input opening before and after its waits', async()=>{
 const {current}=workspace();current.openingInput=true;
 expect(await current.goTo('main')).toBe(false);
 expect(await current.newTab()).toBe(false);
 await current.addView();expect(current.layout.views).toHaveLength(1);
 expect(current.flush).not.toHaveBeenCalled();
 current.openingInput=false;current.capture=vi.fn();
 current.flush=vi.fn(async()=>{current.openingInput=true;});
 expect(await current.newTab()).toBe(false);expect(current.layout.views).toHaveLength(1);
 current.openingInput=false;current.openIds=[];
 current.activate=vi.fn(async()=>{current.openingInput=true;});
 expect(await current.goTo('main')).toBe(false);
 current.openingInput=false;
 current.addView=vi.fn(async()=>{current.openingInput=true;return 'main';});
 expect(await current.newTab()).toBe(false);
});

test('closing the Tab in view brings the one beside it into view, the one after first', async () => {
 const {current}=workspace();
 current.layout.views.push({id:'t2',title:'Tab 2',specimen:{visible:true,rect:null},items:[],notes:[]},{id:'t3',title:'Tab 3',specimen:{visible:true,rect:null},items:[],notes:[]});
 current.openIds=['main','t2','t3'];current.layout.active='t2';
 const asked:string[]=[];current.activate=vi.fn(async(id:string)=>{asked.push(id);current.layout.active=id;return true;});
 await Reflect.get(WorkspaceViews.prototype,'closeTab').call(current,'t2');
 expect(asked).toEqual(['t3']);expect(current.openIds).toEqual(['main','t3']);
 await Reflect.get(WorkspaceViews.prototype,'closeTab').call(current,'t3');
 expect(asked).toEqual(['t3','main']);
});


test.each(['other-project','away-and-back'])('a stale specimen-menu input choice refuses before saving or selecting: %s',async changed=>{
 const {current}=workspace(),owner=current.tabOwner();current.generation++;
 if(changed==='other-project')current.session='another';
 await expect(current.openInput({source:'a7',position:0},'Recording',owner)).rejects.toThrow('Project changed');
 expect(current.flush).not.toHaveBeenCalled();expect(current.opts.selectInput).not.toHaveBeenCalled();
 expect(current.layout.views).toHaveLength(1);expect(current.layout.active).toBe('main');
});


test('opening a saved live Tab restores retained work when the camera is unavailable without making its input ready',async()=>{
 const {current}=workspace();
 current.layout.views[0].input={source:'live',position:0};
 const item={resource:{day:'project',id:'e1',version:2,picture:0},title:'Saved result',placementId:'kept'};
 current.layout.views[0].items.push(item);
 current.layout.views[0].notes.push({id:'note',placementId:'note-placement',rect:[0,0,100,100]});
 current.loaded.clear();
 current.opts.selectInput.mockRejectedValue(new CameraInputUnavailable());
 current.opts.restoreItems=vi.fn(async()=>({recovered:1,missing:[]}));
 current.openNote=vi.fn();
 const opened=await Reflect.get(WorkspaceViews.prototype,'activate').call(current,'main',true,true);
 expect(opened).toBe(true);expect(current.render).toHaveBeenCalled();
 expect(current.opts.restoreItems).toHaveBeenCalledWith([{...item,coordinates:'stage'}]);
 expect(current.openNote).toHaveBeenCalledWith('note',[0,0,100,100],'note-placement',{background:true});
 expect(current.loaded.has('main')).toBe(true);
 expect(document.body.dataset.workspaceInputUnavailable).toBe('true');
 expect(document.body.dataset.workspaceInputOpening).toBeUndefined();
 expect(current.layout.views[0].input).toEqual({source:'live',position:0});
 expect(current.opts.notice).toHaveBeenCalledWith(expect.objectContaining({message:expect.stringContaining('Camera unavailable')}));
 current.opts.selectInput.mockResolvedValue(undefined);
 current.status={textContent:'Saved · Camera unavailable'};current.retry={hidden:false,textContent:'Retry camera'};
 current.load=vi.fn();current.dirty=true;
 await Reflect.get(WorkspaceViews.prototype,'retryCamera').call(current,'project',1);
 expect(current.load).not.toHaveBeenCalled();expect(current.flush).toHaveBeenCalled();
 expect(document.body.dataset.workspaceInputUnavailable).toBeUndefined();
 expect(current.opts.restoreItems).toHaveBeenCalledOnce();expect(current.openNote).toHaveBeenCalledOnce();
 expect(current.retry.hidden).toBe(true);
});


test('initial live restoration does not downgrade an ownership or permission refusal',async()=>{
 const {current}=workspace();current.layout.views[0].input={source:'live',position:0};
 current.opts.selectInput.mockRejectedValue(new Error('Permission refused'));
 expect(await Reflect.get(WorkspaceViews.prototype,'activate').call(current,'main',true,true)).toBe(false);
 expect(current.render).not.toHaveBeenCalled();
 expect(document.body.dataset.workspaceInputUnavailable).toBe('true');
});


test.each([true,false])('layout save keeps camera recovery only after successful persistence: %s',async ok=>{
 const {current}=workspace();current.layout.views[0].input={source:'live',position:0};
 document.body.dataset.workspaceInputUnavailable='true';
 Object.assign(current,{status:{textContent:'Saved · Camera unavailable'},retry:{hidden:false,textContent:'Retry camera'},
  dirty:true,noteTimers:new Map(),notes:new Map(),recovery:{clear:vi.fn()},updateRecovery:vi.fn(),recover:{hidden:true}});
 let complete!:(reply:Record<string,unknown>)=>void;
 current.opts.request=vi.fn(()=>new Promise(resolve=>{complete=resolve;}));
 const saving=Reflect.get(WorkspaceViews.prototype,'flush').call(current);
 expect(current.status.textContent).toBe('Saving…');expect(current.retry.hidden).toBe(true);
 complete(ok?{ok:true,revision:2}:{ok:false,error:'Save unavailable'});await saving;
 expect(current.status.textContent).toBe(ok?'Saved · Camera unavailable':'Not saved');
 expect(current.retry.textContent).toBe(ok?'Retry camera':'Retry save');
 expect(current.retry.hidden).toBe(false);expect(document.body.dataset.workspaceInputUnavailable).toBe('true');
});
