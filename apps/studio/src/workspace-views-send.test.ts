import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),removeDocument:vi.fn(),applyDocument:vi.fn(),document:vi.fn(),clearDocuments:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {WorkspaceViews} from './workspace-views';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());

const item=(placementId:string,version=1)=>({resource:{day:'p',id:'d1',version,picture:null},placementId,title:'Cells',zoom:1,pan:[.5,.5]});
type Flash=[string,[string,()=>void][]];
/** A Tabs layer with two Tabs, "Counting" (active, holding one window) and "Compare", whose saves are accepted unless said. */
function tabs(options:{dirtyAfterSave?:boolean}={}){
 const counting={id:'a',title:'Counting',items:[item('p1')],notes:[] as unknown[]},compare={id:'b',title:'Compare',items:[] as ReturnType<typeof item>[],notes:[] as unknown[]};
 const flashes:Flash[]=[];
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,dirty:false,conflict:false,
  layout:{active:'a',views:[counting,compare]},openIds:['a','b'],loaded:new Set(['a','b']),opts:{notice:vi.fn()},
  capture:vi.fn(),changed:vi.fn(),updateTransfer:vi.fn(),visibility:vi.fn(),goTo:vi.fn(async()=>true),
  flush:vi.fn(async()=>{current.dirty=!!options.dirtyAfterSave;}),
  flash:vi.fn((text:string,actions:[string,()=>void][]=[])=>{flashes.push([text,actions]);}),
  addView:vi.fn(async(title:string,activate:boolean)=>{expect(activate).toBe(false);const v={id:'c',title,items:[],notes:[]};current.layout.views.push(v);return v.id;})});
 const root={dataset:{placementId:'p1',workspaceView:'a',project:'p'} as Record<string,string>,isConnected:true};
 current.roots=()=>[root];
 const send=(target:{to?:string;title?:string},copy:boolean)=>Reflect.get(WorkspaceViews.prototype,'send').call(current,root,target,copy) as Promise<void>;
 return {current,counting,compare,flashes,root,send,action:(label:string)=>flashes.at(-1)![1].find(([name])=>name===label)![1]};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,0));

test('Move sends the window to the chosen Tab and says so, with Go to and Undo, once the save is accepted',async()=>{
 const t=tabs();await t.send({to:'b'},false);
 expect(t.counting.items).toEqual([]);expect(t.compare.items).toEqual([item('p1')]);expect(t.root.dataset.workspaceView).toBe('b');
 expect(t.flashes.at(-1)![0]).toBe('Moved to Compare');expect(t.flashes.at(-1)![1].map(([name])=>name)).toEqual(['Go to','Undo']);
 t.action('Go to')();expect(t.current.goTo).toHaveBeenCalledWith('b',{session:'p',generation:1});
 t.action('Undo')();await settle();
 expect(t.counting.items).toEqual([item('p1')]);expect(t.compare.items).toEqual([]);expect(t.root.dataset.workspaceView).toBe('a');
 expect(t.flashes.at(-1)![0]).toBe('Moved back');
});

test('a move whose save was not accepted says it is not saved yet',async()=>{
 const t=tabs({dirtyAfterSave:true});await t.send({to:'b'},false);
 expect(t.flashes.at(-1)![0]).toBe('Moved to Compare · not saved yet');
});

test('Also show keeps the window here, and Undo takes away only the copy',async()=>{
 const t=tabs();await t.send({to:'b'},true);
 expect(t.counting.items).toEqual([item('p1')]);expect(t.compare.items).toHaveLength(1);
 expect(t.compare.items[0].placementId).not.toBe('p1');expect(t.compare.items[0].resource).toEqual(item('p1').resource);
 expect(t.flashes.at(-1)![0]).toBe('Added to Compare');
 t.action('Undo')();await settle();
 expect(t.counting.items).toEqual([item('p1')]);expect(t.compare.items).toEqual([]);expect(t.flashes.at(-1)![0]).toBe('No longer shown in Compare');
});

test('+ New Tab makes a Tab named after the window without leaving this one',async()=>{
 const t=tabs();await t.send({title:'Cells'},false);
 expect(t.current.addView).toHaveBeenCalledWith('Cells',false);
 expect(t.current.layout.views.find((v:{id:string})=>v.id==='c').items).toEqual([item('p1')]);
 expect(t.flashes.at(-1)![0]).toBe('Moved to Cells');
});

test('a new Tab takes a short name from a long result title',async()=>{
 const t=tabs();await t.send({title:'Bright regions · Bright objects · Basic local method · 22:16 · Masks'},false);
 expect(t.current.addView).toHaveBeenCalledWith('Bright regions',false);
});

test('a Project switch while sending says nothing about the old Project',async()=>{
 const t=tabs();t.current.flush=vi.fn(async()=>{t.current.session='q';});
 await t.send({to:'b'},false);expect(t.flashes).toEqual([]);
});

test('where a result is: the Tabs showing it at this revision, each with whether it is open',()=>{
 const t=tabs();t.compare.items.push(item('p2',2));t.current.layout.views.push({id:'c',title:'Old',items:[item('p3',1)],notes:[]});t.current.openIds=['a','b'];
 expect(t.current.places('d1',1)).toEqual([{id:'a',title:'Counting',open:true},{id:'c',title:'Old',open:false}]);
 expect(t.current.places('d1',2)).toEqual([{id:'b',title:'Compare',open:true}]);
 expect(t.current.places('d1').map((p:{id:string})=>p.id)).toEqual(['a','b','c']);
});

test('a window says which other Tabs also show it, and each name goes there',()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const t=tabs();t.compare.items.push(item('p2'));t.current.layout.views.push({id:'c',title:'Newer',items:[item('p3',2)],notes:[]});
 const root=new TestElement('section'),chip=new TestElement('span');chip.className='workspace-also-in';root.append(chip);
 root.dataset.workspaceView='a';root.dataset.studyRetained=JSON.stringify({resource:item('p1').resource});
 Reflect.get(WorkspaceViews.prototype,'updateTransfer').call(t.current,root);
 expect(chip.hidden).toBe(false);expect(chip.textContent).toBe('Also inCompare');
 (chip.querySelector('button') as unknown as {onclick:()=>void}).onclick();expect(t.current.goTo).toHaveBeenCalledWith('b');
 t.compare.items=[];Reflect.get(WorkspaceViews.prototype,'updateTransfer').call(t.current,root);expect(chip.hidden).toBe(true);
});


test('Send refuses a dialog from an earlier Project opening and a root from another Project',async()=>{
 const t=tabs(),owner=t.current.tabOwner();t.current.generation++;
 await Reflect.get(WorkspaceViews.prototype,'send').call(t.current,t.root,{to:'b'},false,owner);
 expect(t.counting.items).toEqual([item('p1')]);expect(t.flashes).toEqual([]);
 t.root.dataset.project='other';await t.send({to:'b'},false);
 expect(t.compare.items).toEqual([]);expect(t.current.flush).not.toHaveBeenCalled();
});

test('Send rechecks its root after creating a destination Tab',async()=>{
 const t=tabs();t.current.addView=vi.fn(async()=>{t.root.dataset.project='other';return 'b';});
 await t.send({title:'Cells'},false);expect(t.compare.items).toEqual([]);expect(t.flashes).toEqual([]);
});

test('Go to retains the original Project opening even if its toast callback runs late',async()=>{
 const t=tabs();await t.send({to:'b'},false);const go=t.action('Go to');
 t.current.generation++;go();expect(t.current.goTo).not.toHaveBeenCalled();
});

test.each(['failed-save','changed-project'])('Undo reports only an accepted current-owner save: %s',async outcome=>{
 const t=tabs();await t.send({to:'b'},false);
 t.current.flush=vi.fn(async()=>{if(outcome==='failed-save')t.current.dirty=true;else t.current.generation++;});
 t.action('Undo')();await settle();
 expect(t.flashes).toHaveLength(outcome==='failed-save'?2:1);
 if(outcome==='failed-save')expect(t.flashes.at(-1)![0]).toBe('Moved back · not saved yet');
});


test.each(['removed-move','removed-copy','removed-source'])('stale Undo neither saves nor claims a transfer: %s',async stale=>{
 const t=tabs();await t.send({to:'b'},stale==='removed-copy');const undo=t.action('Undo');
 if(stale==='removed-source')t.current.layout.views=t.current.layout.views.filter((v:{id:string})=>v.id!=='a');
 else t.compare.items=[];
 t.current.changed.mockClear();t.current.flush.mockClear();undo();await settle();
 expect(t.current.changed).not.toHaveBeenCalled();expect(t.current.flush).not.toHaveBeenCalled();expect(t.flashes).toHaveLength(1);
 expect(t.current.opts.notice).toHaveBeenCalledWith('That placement changed. Nothing was undone.');
});

test('nothing is sent while a Tab is still opening, so half a Tab is never saved; the toast says to wait',async()=>{
 const t=tabs();t.current.changing=true;await t.send({to:'b'},false);
 expect(t.counting.items).toEqual([item('p1')]);expect(t.compare.items).toEqual([]);
 expect(t.flashes.at(-1)![0]).toBe('This Tab is still opening; try again in a moment');
});

test('Undo says when it could not undo, and whether undoing was saved',async()=>{
 const t=tabs();await t.send({to:'b'},false);
 t.compare.items=[];t.action('Undo')();await new Promise(resolve=>setTimeout(resolve,0));
 expect(t.current.opts.notice).toHaveBeenCalledWith('That placement changed. Nothing was undone.');
 const u=tabs();await u.send({to:'b'},false);u.current.flush=vi.fn(async()=>{u.current.dirty=true;});
 u.action('Undo')();await new Promise(resolve=>setTimeout(resolve,0));expect(u.flashes.at(-1)![0]).toBe('Moved back · not saved yet');
 const w=tabs();await w.send({to:'b'},false);w.root.isConnected=false;w.action('Undo')();await new Promise(resolve=>setTimeout(resolve,0));
 expect(w.flashes.at(-1)![0]).toBe('This Tab was redrawn since; use Add to Tab… to move it back');expect(w.compare.items).toEqual([item('p1')]);
});

test('Go to and Undo do nothing once another Project is open',async()=>{
 const t=tabs();await t.send({to:'b'},false);t.current.session='q';
 t.action('Go to')();t.action('Undo')();await new Promise(resolve=>setTimeout(resolve,0));
 expect(t.current.goTo).not.toHaveBeenCalled();expect(t.compare.items).toEqual([item('p1')]);
});


test.each([false,true])('moving a compared window prunes lanes and Undo respects newer comparison edits: %s',async newer=>{
 const t=tabs();t.counting.items.push(item('p2'),item('p3'));
 Object.assign(t.counting,{kind:'compare',compare:{sync:true,lanes:[{placementId:'p1',label:'A',impl:'one'},{placementId:'p2',label:'B',impl:'two'},{placementId:'p3',label:'C',impl:'three'}]}});
 await t.send({to:'b'},false);
 expect(Reflect.get(t.counting,'compare').lanes.map((l:{placementId:string})=>l.placementId)).toEqual(['p2','p3']);
 expect(t.compare.items).toEqual([item('p1')]);
 if(newer)Reflect.get(t.counting,'compare').sync=false;
 t.action('Undo')();await settle();
 expect(Reflect.get(t.counting,'compare').lanes.map((l:{placementId:string})=>l.placementId)).toEqual(newer?['p2','p3']:['p1','p2','p3']);
 expect(Reflect.get(t.counting,'compare').sync).toBe(!newer);
});
