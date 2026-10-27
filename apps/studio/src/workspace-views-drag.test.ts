import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),removeDocument:vi.fn(),applyDocument:vi.fn(),document:vi.fn(),clearDocuments:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {ENTRY_DRAG,draggedEntry,WorkspaceViews} from './workspace-views';
afterEach(()=>vi.unstubAllGlobals());

type Target={kind:'tab';id:string;el:unknown}|{kind:'new';el:unknown}|null;
/** A window on Tab "a", dragged by its header; `land` says what the Tab row has under the pointer at release. */
function drag(land:Target,options:{alt?:boolean;session?:string}={}){
 const listeners:Record<string,(event:unknown)=>void>={};
 vi.stubGlobal('document',{addEventListener:(type:string,fn:(event:unknown)=>void)=>{listeners[type]=fn;}});
 vi.stubGlobal('window',{addEventListener:(type:string,fn:(event:unknown)=>void)=>{listeners[`window:${type}`]=fn;}});
 const header={},root={dataset:{placementId:'p1',workspaceView:'a'} as Record<string,string>,getAttribute:()=>'Cells',querySelector:()=>null,contains:(n:unknown)=>n===header};
 const hit={closest:(selector:string)=>selector.startsWith('button')?null:selector.startsWith('[data-placement-id]')?root:selector.startsWith('header')?header:null};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,changing:false,
  layout:{active:'a',views:[{id:'a',title:'Counting'},{id:'b',title:'Compare'}]},
  roots:()=>[root],rect:()=>[10,20,300,200],navigating:false,openingInput:false,flash:vi.fn(),place:vi.fn(),send:vi.fn(async()=>{}),showDrop:vi.fn(),clearDrop:vi.fn(),dropTarget:()=>land});
 Reflect.get(WorkspaceViews.prototype,'headerDrag').call(current,{button:0,target:hit,pointerId:7});
 if(options.session)current.session=options.session;
 listeners.pointerup?.({pointerId:7,clientX:5,clientY:5,altKey:!!options.alt});
 return {current,root};
}

test('a window dropped on another Tab goes back to where it started, then moves there',()=>{
 const {current,root}=drag({kind:'tab',id:'b',el:{}});
 expect(current.place).toHaveBeenCalledWith(root,[10,20,300,200]);
 expect(current.send).toHaveBeenCalledWith(root,{to:'b'},false);
});

test('Alt also shows it there; + makes a new Tab named after it',()=>{
 expect(drag({kind:'tab',id:'b',el:{}},{alt:true}).current.send).toHaveBeenCalledWith(expect.anything(),{to:'b'},true);
 expect(drag({kind:'new',el:{}}).current.send).toHaveBeenCalledWith(expect.anything(),{title:'Cells'},false);
});

test('a drop on its own Tab, off the Tab row, or after a Project switch changes nothing',()=>{
 for(const t of [drag({kind:'tab',id:'a',el:{}}),drag(null),drag({kind:'tab',id:'b',el:{}},{session:'q'})]){
  expect(t.current.send).not.toHaveBeenCalled();expect(t.current.place).not.toHaveBeenCalled();
 }
});

test('a drag that starts on a header button is left to the button',()=>{
 const listeners:Record<string,unknown>={};vi.stubGlobal('document',{addEventListener:(type:string,fn:unknown)=>{listeners[type]=fn;}});
 Reflect.get(WorkspaceViews.prototype,'headerDrag').call(Object.create(WorkspaceViews.prototype),{button:0,target:{closest:(s:string)=>s.startsWith('button')?{}:null},pointerId:1});
 expect(listeners).toEqual({});
});

test('a Data card dropped on a Tab, or on +, goes to whoever opens Data results, with what it carried',()=>{
 const handler=vi.fn();const current=Object.assign(Object.create(WorkspaceViews.prototype),{clearDrop:vi.fn()});
 current.acceptEntryDrops(handler);
 const drop=(target:Target)=>{const event={dataTransfer:{getData:(type:string)=>type===ENTRY_DRAG?'{"id":"e1"}':''},clientX:1,clientY:1,preventDefault:vi.fn()};current.dropTarget=()=>target;Reflect.get(WorkspaceViews.prototype,'dropEntry').call(current,event);return event;};
 expect(drop({kind:'tab',id:'b',el:{}}).preventDefault).toHaveBeenCalled();expect(handler).toHaveBeenLastCalledWith('{"id":"e1"}',{tab:'b'});
 drop({kind:'new',el:{}});expect(handler).toHaveBeenLastCalledWith('{"id":"e1"}',{fresh:true});
 handler.mockClear();drop(null);expect(handler).not.toHaveBeenCalled();
});


test('Data drag captures one exact revision and Project opening rather than resolving the latest card',()=>{
 const owner={session:'p',generation:1},entry={day:'p',id:'d1',version:2},data=JSON.stringify({owner,entry});
 expect(draggedEntry(data,owner,[entry])).toBe(entry);
 expect(draggedEntry(data,owner,[{...entry,version:3}])).toBeNull();
 expect(draggedEntry(data,{...owner,generation:3},[entry])).toBeNull();
 expect(draggedEntry(data,{session:'q',generation:1},[entry])).toBeNull();
 expect(draggedEntry(data,owner,[entry,entry])).toBeNull();
 expect(draggedEntry('{',owner,[entry])).toBeNull();
 expect(draggedEntry(JSON.stringify({owner,entry:{...entry,version:null}}),owner,[{...entry,version:undefined}])).not.toBeNull();
});

test('a new drag ends the one before; losing focus ends a drag; nothing is sent while the Tab opens',()=>{
 const listeners:Record<string,(event:unknown)=>void>={};const signals:AbortSignal[]=[];
 vi.stubGlobal('document',{addEventListener:(type:string,fn:(event:unknown)=>void,o:{signal:AbortSignal})=>{listeners[type]=fn;signals.push(o.signal);}});
 vi.stubGlobal('window',{addEventListener:(type:string,fn:(event:unknown)=>void)=>{listeners[`window:${type}`]=fn;}});
 const header={},root={dataset:{placementId:'p1',workspaceView:'a'} as Record<string,string>,getAttribute:()=>'Cells',querySelector:()=>null,contains:(n:unknown)=>n===header};
 const hit={closest:(selector:string)=>selector.startsWith('button')?null:selector.startsWith('[data-placement-id]')?root:selector.startsWith('header')?header:null};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,changing:false,navigating:false,openingInput:false,
  layout:{active:'a',views:[{id:'a',title:'Counting'},{id:'b',title:'Compare'}]},roots:()=>[root],rect:()=>[0,0,1,1],
  place:vi.fn(),send:vi.fn(async()=>{}),showDrop:vi.fn(),clearDrop:vi.fn(),flash:vi.fn(),dropTarget:()=>({kind:'tab',id:'b',el:{}})});
 Reflect.get(WorkspaceViews.prototype,'headerDrag').call(current,{button:0,target:hit,pointerId:1});const first=signals[0];
 Reflect.get(WorkspaceViews.prototype,'headerDrag').call(current,{button:0,target:hit,pointerId:1});
 expect(first.aborted).toBe(true);
 listeners['window:blur']({});expect(signals.at(-1)!.aborted).toBe(true);
 Reflect.get(WorkspaceViews.prototype,'headerDrag').call(current,{button:0,target:hit,pointerId:1});
 current.changing=true;listeners.pointerup({pointerId:1,clientX:1,clientY:1,altKey:false});
 expect(current.send).not.toHaveBeenCalled();expect(current.place).not.toHaveBeenCalled();
 expect(current.flash).toHaveBeenCalledWith('This Tab is still opening; try again in a moment');
});

test('the Tab row’s free space beside the Tabs makes a new Tab, lighting up +',()=>{
 const add={},strip={querySelector:(s:string)=>s==='.workspace-new-tab'?add:null,contains:()=>true,closest:()=>null},tabs={};
 vi.stubGlobal('document',{elementFromPoint:()=>strip});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{strip,tabs});
 expect(Reflect.get(WorkspaceViews.prototype,'dropTarget').call(current,1,1)).toEqual({kind:'new',el:add});
});

test('a researcher\'s Tab is no drop target: windows and Data cards land on work Tabs or +',()=>{
 const researcher={closest:(s:string)=>s==='.workspace-view-tab'?{querySelector:()=>null}:null};
 const strip={querySelector:()=>null,contains:()=>true},tabs={};
 vi.stubGlobal('document',{elementFromPoint:()=>researcher});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{strip,tabs});
 expect(Reflect.get(WorkspaceViews.prototype,'dropTarget').call(current,1,1)).toBeNull();
});
