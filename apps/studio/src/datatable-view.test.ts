// Synthetic DOM and D8 transport; no native viewer, algorithm or installed-app qualification.
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {TestElement,fire} from './workspace-windows.test-support';
import {DataTableView,checkedDataTable,dataTableURL,tableFilter,TABLE_PAGE_ROWS,type DataTableSnapshot,type DataTablePin,type DataTableOwner} from './datatable-view';
const pin:DataTablePin={session:'project-one',id:'tb1',version:1};
const owner:DataTableOwner={session:pin.session,generation:1};
function table(count=205,version=1):DataTableSnapshot{return {
 id:'tb1',version,title:'Measured cells',mode:'follow',sources:[{day:pin.session,id:'e1',revision:4,kind:'population',label:'Corrected cells'}],
 columns:[{key:'row',label:'Row',unit:'',type:'text',origin:'source'},
  {key:'source',label:'Source',unit:'',type:'text',origin:'source'},
  {key:'member',label:'Member',unit:'',type:'text',origin:'source'},
  {key:'area',label:'Area',unit:'µm²',type:'number',origin:'source'},
  {key:'condition',label:'Condition',unit:'',type:'text',origin:'added'},
  {key:'excluded',label:'Left out because',unit:'',type:'text',origin:'excluded'}],
 added:[{key:'condition',label:'Condition',type:'category'}],
 rows:Array.from({length:count},(_,i)=>({row:`e1:c${i+1}`,source:'e1',member:`c${i+1}`,area:i+0.123456789012345,condition:null,excluded:null})),
 counts:{rows:count,included:count,excluded:0},versions:[version],freshness:{state:'current',words:'Current · e1 revision 4'},
};}
function reply(value:unknown,ok=true){return {ok,json:async()=>structuredClone(value)} as Response;}
function rig(){
 const root=new TestElement('section'),saved=vi.fn(),loaded=vi.fn(),showMember=vi.fn();
 let opening=owner.generation;
 const request=vi.fn<typeof fetch>().mockResolvedValue(reply(table()));
 const view=new DataTableView(root as unknown as HTMLElement,{current:o=>o.session===owner.session&&o.generation===opening,request,saved,loaded,showMember});
 return {root,view,request,saved,loaded,showMember,switchProject:()=>{opening++;}};
}
beforeEach(()=>{vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag)});vi.stubGlobal('window',{open:vi.fn()});});
afterEach(()=>vi.unstubAllGlobals());
test('exact revision read, bounded rows, native precision/units and both directions of exact member linking',async()=>{
 const r=rig();expect(await r.view.show(pin,owner)).toBe(true);
 expect(r.request.mock.calls[0][0]).toBe('/api/datatables/tb1?session=project-one&version=1');
 expect(r.root.querySelector('tbody')!.children).toHaveLength(TABLE_PAGE_ROWS);
 expect(r.root.textContent).toContain('Area (µm²)');
 // Shown to 3 significant figures; the exact native value is never lost: the cell's title, and More › Show exact values.
 const first=r.root.querySelector('tbody')!.children[0].children[1];expect(first.textContent).toBe('0.123');expect(first.title).toBe('0.123456789012345');
 fire(r.root.querySelectorAll('button').find(b=>b.textContent==='Show exact values')!,'click');
 expect(r.root.textContent).toContain('0.123456789012345');
 const link=r.root.querySelectorAll('button').find(b=>b.textContent==='Corrected cells · c1')!;
 fire(link,'click');await Promise.resolve();
 const selected=r.root.querySelector('[data-row="e1:c2"]')!.querySelector('input')!;Object.assign(selected,{checked:true});fire(selected,'change');await Promise.resolve();
 expect(r.showMember).toHaveBeenLastCalledWith({session:pin.session,id:'e1',revision:4,member:'c2',kind:'population',title:'Corrected cells'});
 expect(r.showMember).toHaveBeenCalledWith({session:pin.session,id:'e1',revision:4,member:'c1',kind:'population',title:'Corrected cells'});
 expect(r.view.selectMember({session:pin.session,id:'e1',revision:3,member:'c205',kind:'population',title:''})).toBe(false);
 expect(r.view.selectMember({session:pin.session,id:'e1',revision:4,member:'c205',kind:'population',title:''})).toBe(true);
 expect(r.root.querySelector('tbody')!.children).toHaveLength(5);
 expect(r.root.querySelector('[data-row="e1:c205"]')!.getAttribute('aria-selected')).toBe('true');
 expect(dataTableURL(pin,true)).toBe('/api/datatables/tb1.csv?session=project-one&version=1');
});
test('filter uses inclusive numeric ranges and equality lists without evaluating expressions or coercing booleans',()=>{
 const t=table(5);t.rows[1].condition='control';t.rows[2].condition='treated';
 expect(tableFilter(t,{area:[1,3],condition:['control','treated']}).map(r=>r.member)).toEqual(['c2','c3']);
 expect(tableFilter(t,{area:3}).map(r=>r.member)).toEqual(['c4','c5']);
 expect(()=>tableFilter(t,{area:'1'})).toThrow('numeric range');
 expect(()=>tableFilter(t,{area:true})).toThrow('numeric range');
 expect(()=>tableFilter(t,{'area > 2':0})).toThrow('existing column');
});
test('drafts are explicit; failed CAS retains edits, successful Save advances only the exact accepted pin and reopens it',async()=>{
 const r=rig();await r.view.show(pin,owner);
 r.view.stage({op:'set',column:'condition',rows:['e1:c1'],value:'control'});
 r.view.stage({op:'exclude',rows:['e1:c2'],reason:'Out of focus'});
 expect(r.view.dirty).toBe(true);expect(r.view.canClose()).toBe(false);expect(r.root.textContent).toContain('Draft');
 expect(r.request).toHaveBeenCalledTimes(1);
 r.request.mockResolvedValueOnce(reply({error:'the table version changed',next:'reload it'},false));
 expect(await r.view.saveDraft()).toBe(false);expect(r.view.dirty).toBe(true);expect(r.saved).not.toHaveBeenCalled();
 expect(r.root.textContent).toContain('Not saved.');
 const accepted=table(205,2);accepted.rows[0].condition='control';accepted.rows[1].excluded='Out of focus';
 r.request.mockResolvedValueOnce(reply(accepted));expect(await r.view.saveDraft()).toBe(true);
 const [url,options]=r.request.mock.calls[2];expect(url).toBe('/api/datatables/tb1');
 expect(options?.headers).toEqual({'Content-Type':'application/json','X-MicroscopeMind':'datatable'});
 expect(JSON.parse(String(options?.body))).toEqual({session:pin.session,version:1,edits:[
  {op:'set',column:'condition',rows:['e1:c1'],value:'control'},{op:'exclude',rows:['e1:c2'],reason:'Out of focus'}]});
 expect(r.saved).toHaveBeenCalledWith({...pin,version:2},accepted);expect(r.view.canClose()).toBe(true);
 r.request.mockResolvedValueOnce(reply(accepted));await r.view.show({...pin,version:2},owner);
 expect(r.request.mock.calls[3][0]).toContain('version=2');expect(r.root.textContent).toContain('Out of focus');
});
test('late reads and writes cannot publish UI success into a later Project opening',async()=>{
 const r=rig();let resolve!:(value:Response)=>void;
 r.request.mockReturnValueOnce(new Promise(res=>{resolve=res;}));const opening=r.view.show(pin,owner);
 r.switchProject();resolve(reply(table()));expect(await opening).toBe(false);expect(r.loaded).not.toHaveBeenCalled();
 const s=rig();await s.view.show(pin,owner);s.view.stage({op:'mode',mode:'frozen'});
 s.request.mockReturnValueOnce(new Promise(res=>{resolve=res;}));const saving=s.view.saveDraft();
 s.switchProject();resolve(reply(table(205,2)));expect(await saving).toBe(false);expect(s.saved).not.toHaveBeenCalled();
 expect(s.view.dirty).toBe(true);expect(s.root.textContent).not.toContain('nothing was made');
});
test('freshness updates do not replace a draft; no-op Update succeeds and freeze remains a draft until accepted',async()=>{
 const r=rig();await r.view.show(pin,owner);r.view.stage({op:'mode',mode:'frozen'});
 const updating=table();updating.freshness={state:'updating',words:'Reading source correction'};
 r.request.mockResolvedValueOnce(reply(updating));await r.view.refresh();
 expect(r.root.textContent).toContain('Updating…');expect(r.root.textContent).toContain('Draft');expect(await r.view.update()).toBe(false);
 r.view.discardDraft();r.request.mockResolvedValueOnce(reply(table()));expect(await r.view.update()).toBe(true);
 expect(r.saved).toHaveBeenCalledWith(pin,table());expect(r.view.dirty).toBe(false);
});
test('response identity and source ownership are refused without admitting rows',async()=>{
 const r=rig(),foreign=table();foreign.sources[0].day='another-project';
 expect(()=>checkedDataTable(foreign,pin)).toThrow('Project and saved revision');
 r.request.mockResolvedValue(reply({...table(),version:2}));expect(await r.view.show(pin,owner)).toBe(false);
 expect(r.loaded).not.toHaveBeenCalled();expect(r.root.textContent).toContain('Opening failed');
});

test('an older freshness response cannot overwrite the revision accepted while it was in flight',async()=>{
 const r=rig();await r.view.show(pin,owner);let resolve!:(value:Response)=>void;
 r.request.mockReturnValueOnce(new Promise(res=>{resolve=res;}));const reading=r.view.refresh();
 r.view.stage({op:'mode',mode:'frozen'});const frozen=table(205,2);frozen.mode='frozen';frozen.freshness={state:'frozen',words:'Frozen at version 2'};
 r.request.mockResolvedValueOnce(reply(frozen));expect(await r.view.saveDraft()).toBe(true);
 const previous=table();previous.freshness={state:'stale',words:'Old source changed'};resolve(reply(previous));await reading;
 expect(r.root.querySelector('.datatable-freshness')!.textContent).toBe('Frozen');
});

test('Save keeps its close guard until the accepted pin reaches its original arrangement',async()=>{
 const r=rig();await r.view.show(pin,owner);r.view.stage({op:'exclude',rows:['e1:c1'],reason:'outside field'});
 let finish!:()=>void;r.saved.mockImplementationOnce(()=>new Promise<void>(resolve=>{finish=resolve;}));
 r.request.mockResolvedValueOnce(reply(table(205,2)));
 const saving=r.view.saveDraft();await Promise.resolve();await Promise.resolve();await Promise.resolve();
 expect(r.view.busy).toBe(true);expect(r.view.canClose()).toBe(false);
 finish();expect(await saving).toBe(true);expect(r.view.canClose()).toBe(true);
});
