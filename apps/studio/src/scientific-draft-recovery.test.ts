// Ordinary console + real recovery store; synthetic browser storage and DOM, no kernel calls.
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {RecoveryStore} from './workspace-note-state';
import {ScientificConsole} from './scientific-console';
import {TestElement,fire,syntheticDOM,settleMicrotasks} from './workspace-windows.test-support';
class StorageFixture implements Storage {
 data=new Map<string,string>();get length(){return this.data.size;}key(index:number){return [...this.data.keys()][index]??null;}
 getItem(key:string){return this.data.get(key)??null;}setItem(key:string,value:string){this.data.set(key,value);}removeItem(key:string){this.data.delete(key);}clear(){this.data.clear();}
}
const resource={day:'project',id:'p'+'a'.repeat(32),version:2,picture:null};
let storage:StorageFixture;
const mounted:ScientificConsole[]=[];
beforeEach(()=>{const dom=syntheticDOM();vi.stubGlobal('document',dom.document);storage=new StorageFixture();});
afterEach(()=>{for(const console of mounted)console.close();mounted.length=0;vi.restoreAllMocks();vi.unstubAllGlobals();});
function mount(change={}){
 const root=new TestElement('section'),execute=vi.fn(),stop=vi.fn();
 const draft={storage,prefix:'test-script-recovery',resource,placement:'view-one',...change};
 // Proposed optional boundary is intentionally ignored by the pre-fix constructor.
 const console=Reflect.construct(ScientificConsole,[root,{execute,stop,draft}]) as ScientificConsole;
 mounted.push(console);
 const input=root.querySelector('textarea')!;Object.assign(input,{focus:vi.fn()});
 const edit=(text:string)=>{input.value=text;fire(input,'input');};
 const recover=()=>root.querySelectorAll('button').filter(button=>button.textContent==='Restore script draft');
 return {root,input,edit,recover,console,execute,stop};
}
test('an unrun draft survives closing/reopening but restores only on an explicit press',()=>{
 const first=mount();first.edit('kept = "µ"\nkept + 4');first.console.close();
 const next=mount();expect(next.input.value).toBe('');expect(next.recover()).toHaveLength(1);
 fire(next.recover()[0],'click');expect(next.input.value).toBe('kept = "µ"\nkept + 4');
 expect(next.execute).not.toHaveBeenCalled();expect(first.stop).not.toHaveBeenCalled();
 expect(next.root.textContent).toContain('this browser');next.console.close();
});
test('different Project, revision and placement do not offer the same draft',()=>{
 const first=mount();first.edit('private_value = 91');first.console.close();
 for(const change of [{resource:{...resource,day:'other'}},{resource:{...resource,version:3}},{placement:'other-view'}]){
  const other=mount(change);expect(other.recover()).toHaveLength(0);other.console.close();
 }
 expect(mount().recover()).toHaveLength(1);
});
test('restoring an earlier draft preserves newer typing and simultaneous writers',()=>{
 const earlier=mount();earlier.edit('old = 1');earlier.console.close();
 const a=mount(),b=mount();a.edit('a = 2');b.edit('b = 3');
 fire(a.recover()[0],'click');expect(a.input.value).toBe('old = 1');expect(b.input.value).toBe('b = 3');
 a.console.close();b.console.close();
 const kept=[...storage.data.values()].join('\n');
 for(const source of ['old = 1','a = 2','b = 3'])expect(kept).toContain(source);
 expect(a.execute).not.toHaveBeenCalled();expect(b.execute).not.toHaveBeenCalled();
});
test('storage refusal keeps the editor, reports recovery unavailable and permits correction',()=>{
 const old=mount();old.edit('older = 1');old.console.close();const next=mount();
 next.edit('newer = 2');const write=vi.spyOn(storage,'setItem').mockImplementation(()=>{throw new Error('quota');});
 next.edit('newest = 3');fire(next.recover()[0],'click');
 expect(next.input.value).toBe('newest = 3');expect(next.root.textContent).toContain('Download');
 expect(next.execute).not.toHaveBeenCalled();write.mockRestore();next.edit('corrected = 4');next.console.close();
 expect([...storage.data.values()].join('\n')).toContain('corrected = 4');
});

test('an oversized Unicode edit keeps the previous saved source and remains recoverable',()=>{
 const first=mount();first.edit('safe = 7');const before=[...storage.data.values()];
 first.edit('µ'.repeat(140000));expect([...storage.data.values()]).toEqual(before);
 expect(first.root.textContent).toContain('exceeds 256 KiB');first.console.close();
 const next=mount();fire(next.recover()[0],'click');expect(next.input.value).toBe('safe = 7');
});
test('an already displayed recovery candidate cannot overwrite a newer writer generation',()=>{
 const writer=mount();writer.edit('old = 1');const reader=mount();writer.edit('latest = 9');
 fire(reader.recover()[0],'click');expect(reader.input.value).toBe('');expect(reader.root.textContent).toContain('changed in another window');
 fire(reader.recover()[0],'click');expect(reader.input.value).toBe('latest = 9');expect(reader.execute).not.toHaveBeenCalled();
});
test('discard requires two presses and cannot discard a newer generation',()=>{
 const writer=mount();writer.edit('first = 1');const reader=mount();
 const discard=reader.root.querySelectorAll('button').find(b=>b.textContent==='Discard saved draft')!;
 fire(discard,'click');expect(storage.length).toBe(1);writer.edit('latest = 2');fire(discard,'click');
 expect([...storage.data.values()].join()).toContain('latest = 2');expect(reader.input.value).toBe('');
});
test('malformed recovery data never becomes runnable code or removes valid copies',()=>{
 const writer=mount();writer.edit('safe = 2');writer.console.close();
 const [key,raw]=[...storage.data.entries()][0],record=JSON.parse(raw);
 storage.setItem(key+'-wrong',JSON.stringify({...record,value:{format:'scientific-script-draft-v1',source:{code:'bad'}}}));
 storage.setItem(key+'-generation',JSON.stringify({...record,generation:'bad'}));
 const next=mount();expect(next.recover()).toHaveLength(1);expect(storage.length).toBe(3);expect(next.execute).not.toHaveBeenCalled();
});
test('late execution completion never clears a newer unrun draft',async()=>{
 const current=mount();let finish!:(value:unknown)=>void;
 current.execute.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 current.edit('first = 1');fire(current.root.querySelectorAll('button').find(b=>b.textContent==='Run code')!,'click');
 current.edit('later = 2');finish({format:'scientific-console-v1',sequence:1,stdout:'',stderr:'',result:'',error:'',truncated:false,privatePreview:true});
 await Promise.resolve();await Promise.resolve();current.console.close();
 const next=mount();fire(next.recover()[0],'click');expect(next.input.value).toBe('later = 2');expect(next.execute).not.toHaveBeenCalled();
});

test('read refusal is an unavailable recovery state, and retry recovers the unchanged copy',()=>{
 const old=mount();old.edit('unrun = 42');old.console.close();const before=[...storage.data];
 const read=vi.spyOn(storage,'getItem').mockImplementation(()=>{throw new Error('read denied');});
 const next=mount();expect(next.root.textContent).toContain('Browser draft recovery unavailable');
 expect(next.input.value).toBe('');expect(next.recover()).toHaveLength(0);expect([...storage.data]).toEqual(before);
 read.mockRestore();fire(next.root.querySelectorAll('button').find(b=>b.textContent==='Retry draft recovery')!,'click');
 expect(next.recover()).toHaveLength(1);fire(next.recover()[0],'click');expect(next.input.value).toBe('unrun = 42');
 expect(next.execute).not.toHaveBeenCalled();
});
test('a read refusal during explicit restore preserves the current editor',()=>{
 const old=mount();old.edit('old = 1');old.console.close();const next=mount();next.edit('current = 2');
 const before=[...storage.data];vi.spyOn(storage,'getItem').mockImplementation(()=>{throw new Error('read denied');});
 fire(next.recover()[0],'click');expect(next.input.value).toBe('current = 2');
 expect(next.root.textContent).toContain('Browser draft recovery unavailable');expect([...storage.data]).toEqual(before);
 expect(next.execute).not.toHaveBeenCalled();
});

test('a script imported through the ordinary file input is recovered inertly after reopening',async()=>{
 const first=mount(),source='# imported µ script\n'.repeat(1800)+'value = 42';
 const input=first.root.querySelector('input')!;Object.assign(input,{files:[new File([source],'science.py')]});fire(input,'change');
 await settleMicrotasks();await settleMicrotasks();expect(first.input.value).toBe(source);
 expect(first.execute).not.toHaveBeenCalled();first.console.close();
 const next=mount();expect(next.input.value).toBe('');fire(next.recover()[0],'click');
 expect(next.input.value).toBe(source);expect(next.execute).not.toHaveBeenCalled();
});

test('strict recovery reads distinguish storage failures from malformed records while legacy callers stay tolerant',()=>{
 const store=new RecoveryStore(storage,'compatibility','writer');store.put('p','note',1,{text:'kept'});
 storage.setItem('compatibility:recovery:v2:malformed','{');
 expect(Reflect.apply(store.list,store,['p','note',{strict:true}])).toHaveLength(1);
 vi.spyOn(storage,'getItem').mockImplementation(()=>{throw new Error('denied');});
 expect(store.list('p','note')).toEqual([]);
 expect(()=>Reflect.apply(store.list,store,['p','note',{strict:true}])).toThrow('denied');
});
