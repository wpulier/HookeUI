import {expect,test} from 'vitest';
import {NoteState,RecoveryStore,noteStatusLine} from './workspace-note-state';
class MemoryStorage implements Storage { data=new Map<string,string>();get length(){return this.data.size;} clear(){this.data.clear();}key(n:number){return [...this.data.keys()][n]??null;}getItem(k:string){return this.data.get(k)??null;}setItem(k:string,v:string){this.data.set(k,v);}removeItem(k:string){this.data.delete(k);} }
const saved={id:'a1',title:'Note',text:'Saved',version:1};
test('two documents cannot clear each other or newer typing with an acknowledgement',async()=>{
 const storage=new MemoryStorage(),a=new RecoveryStore(storage,'profile','writer-a'),b=new RecoveryStore(storage,'profile','writer-b');
 const first=new NoteState(saved,a,'project'),second=new NoteState(saved,b,'project');
 first.edit('Note','A');second.edit('Note','B');
 let reply:(r:any)=>void=()=>{};const job=first.save(()=>new Promise(r=>{reply=r;}));
 first.edit('Note','A newer');reply({ok:true,note:{...saved,text:'A',version:2}});await job;
 expect(first.value.text).toBe('A newer');expect(first.value.version).toBe(2);
 expect(a.list('project','a1').map(r=>r.value.text).sort()).toEqual(['A newer','B']);
 await first.save(async()=>({ok:true,note:{...saved,text:'A newer',version:3}}));
 expect(b.list('project','a1').map(r=>r.value.text)).toEqual(['B']);
});
test('conflict retains draft and refreshes saved version for explicit merge',async()=>{
 const store=new RecoveryStore(new MemoryStorage(),'profile','a'),state=new NoteState(saved,store,'project');state.edit('Note','Mine');
 await state.save(async()=>({ok:false,code:'conflict',current:{...saved,text:'Theirs',version:2},error:'changed'}));
 expect(state.value.text).toBe('Mine');expect(state.saved.text).toBe('Theirs');expect(state.status).toBe('conflict');
 state.merge();expect(state.value.version).toBe(2);state.edit('Note','Both');
 await state.save(async()=>({ok:true,note:{...saved,text:'Both',version:3}}));expect(state.status).toBe('saved');
});
test('shared saved revisions refresh clean notes without replacing competing local edits',()=>{
 const store=new RecoveryStore(new MemoryStorage(),'profile','writer'),state=new NoteState(saved,store,'p');
 state.receive({...saved,text:'Remote two',version:2});expect(state.value.text).toBe('Remote two');expect(state.status).toBe('saved');
 state.edit('Note','Local three');state.receive({...saved,text:'Remote three',version:3});
 expect(state.value.text).toBe('Local three');expect(state.value.version).toBe(2);expect(state.saved.version).toBe(3);
 expect(store.list('p','a1')[0].value.text).toBe('Local three');
});
test('a note without a successful first save is discoverable by Project',()=>{
 const storage=new MemoryStorage(),store=new RecoveryStore(storage,'profile','writer');store.put('p','new-note',1,{...saved,id:'new-note',version:0,text:'Never acknowledged'});
 expect(store.noteIds('p')).toEqual(['new-note']);expect(store.noteIds('other')).toEqual([]);
 const reopened=new RecoveryStore(storage,'profile','new-writer');expect(reopened.list('p','new-note')[0].value.text).toBe('Never acknowledged');
});
test('resolving a legacy candidate cannot clear a newer legacy draft',()=>{
 const storage=new MemoryStorage(),store=new RecoveryStore(storage,'profile','writer');storage.setItem('profile:note:p:a1',JSON.stringify(saved));
 const candidate=store.list('p','a1')[0];storage.setItem(candidate.key,JSON.stringify({...saved,text:'Newer'}));store.clear(candidate);
 expect(store.list('p','a1')[0].value.text).toBe('Newer');
});
test('partial layout recovery removes only selected copies from the exact candidate',()=>{
 const storage=new MemoryStorage(),store=new RecoveryStore(storage,'profile','writer');
 const candidate=store.put('p','layout',1,{layout:{views:[{id:'a'},{id:'b'}]}});
 expect(store.replace(candidate,{layout:{views:[{id:'b'}]}})).toBe(true);
 expect(store.list<{layout:{views:{id:string}[]}}>('p','layout')[0].value.layout.views).toEqual([{id:'b'}]);
 expect(store.replace(candidate,{layout:{views:[]}})).toBe(false);
});
test('resolving a recovered draft leaves newer text from its original writer intact',async()=>{
 const storage=new MemoryStorage(),old=new RecoveryStore(storage,'profile','old'),fresh=new RecoveryStore(storage,'profile','fresh');
 const candidate=old.put('p','a1',1,{...saved,text:'Recovered'}),state=new NoteState(saved,fresh,'p');state.recover(candidate);
 old.put('p','a1',2,{...saved,text:'Other writer continued'});state.merge();
 await state.save(async()=>({ok:true,note:{...saved,text:'Recovered',version:2}}));
 expect(fresh.list('p','a1').map(r=>r.value.text)).toEqual(['Other writer continued']);
});
test('new layout editing cannot overwrite a retained conflict from the same browser document',()=>{
 const storage=new MemoryStorage(),store=new RecoveryStore(storage,'profile','writer');
 store.put('p','layout',1,{title:'Retained competing layout'},'conflict-draft');
 store.put('p','layout',2,{title:'New saved-layout edit'},'next-draft');
 expect(store.list<{title:string}>('p','layout').map(r=>r.value.title).sort()).toEqual(['New saved-layout edit','Retained competing layout']);
});
test('loading a competing recovery draft preserves the current unsaved draft separately',async()=>{
 const storage=new MemoryStorage(),store=new RecoveryStore(storage,'profile','writer'),other=new RecoveryStore(storage,'profile','other');
 const candidate=other.put('p','a1',1,{...saved,text:'Competing B'}),state=new NoteState(saved,store,'p');
 state.edit('Note','Unsaved A');state.recover(candidate);
 expect(state.value.text).toBe('Competing B');expect(state.status).toBe('conflict');
 expect(store.list('p','a1').map(r=>r.value.text)).toContain('Unsaved A');
 state.merge();await state.save(async()=>({ok:true,note:{...saved,text:'Competing B',version:2}}));
 expect(store.list('p','a1').map(r=>r.value.text)).toEqual(['Unsaved A']);
});
test('acknowledging an earlier save cannot resolve a subsequently selected competing draft',async()=>{
 const storage=new MemoryStorage(),store=new RecoveryStore(storage,'profile','writer'),other=new RecoveryStore(storage,'profile','other');
 const candidate=other.put('p','a1',1,{...saved,text:'Competing B'}),state=new NoteState(saved,store,'p');
 state.edit('Note','Submitted A');let reply:(r:any)=>void=()=>{};
 const pending=state.save(()=>new Promise(resolve=>{reply=resolve;}));state.recover(candidate);
 reply({ok:true,note:{...saved,text:'Submitted A',version:2}});await pending;
 expect(state.value.text).toBe('Competing B');expect(state.status).toBe('conflict');
 expect(store.list('p','a1').map(r=>r.value.text)).toContain('Submitted A');
});

test('note footer names what happened, keeps the draft and offers Try again after a failed save',()=>{
 expect(noteStatusLine('saved','')).toEqual({text:'Saved',action:'Save note'});
 expect(noteStatusLine('dirty','').text).toBe('Draft · kept on this Mac, not saved yet');
 expect(noteStatusLine('error','Error: disk full')).toEqual({text:'Not saved · your draft is kept on this Mac · disk full',action:'Try again'});
 expect(noteStatusLine('conflict','The saved note changed while this save was pending.').text).toBe('Changed elsewhere since you opened it · your draft is kept. Compare below.');
 expect(noteStatusLine('dirty','Browser recovery unavailable. Keep this note open until saved.').text).toContain('Browser recovery unavailable');
 // Without browser recovery it never claims the draft is kept on this Mac.
 expect(noteStatusLine('dirty','Browser recovery unavailable. Keep this note open until saved.').text).not.toContain('on this Mac');
});
