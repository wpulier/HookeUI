/** Local recovery is namespaced by document writer; it is never the saved note authority. */
export interface Note {id:string;title:string;text:string;version:number}
export interface Recovery<T=Note> {key:string;generation:number;value:T;session:string;id:string;at:number}
export class RecoveryStore {
 storage:Storage;prefix:string;writer:string;
 constructor(storage:Storage,prefix:string,writer:string=crypto.randomUUID()){this.storage=storage;this.prefix=prefix;this.writer=writer;}
 put<T>(session:string,id:string,generation:number,value:T,draftIdentity=''):Recovery<T>{
  const key=`${this.prefix}:recovery:v2:${encodeURIComponent(session)}:${encodeURIComponent(id)}:${this.writer}${draftIdentity?`:${draftIdentity}`:''}`;
  const record={key,session,id,generation,value:structuredClone(value),at:Date.now()};
  this.storage.setItem(key,JSON.stringify(record));return record;
 }
 clear(record:Recovery<unknown>){
  const stored=this.storage.getItem(record.key);
  if(!stored)return;
  const value=JSON.parse(stored);
  if(record.generation<0?JSON.stringify(value)===JSON.stringify(record.value):value.generation===record.generation)this.storage.removeItem(record.key);
 }
 replace<T>(record:Recovery<T>,value:T):boolean {
  const stored=this.storage.getItem(record.key);if(!stored)return false;const current=JSON.parse(stored);
  if(record.generation<0?JSON.stringify(current)!==JSON.stringify(record.value):current.generation!==record.generation)return false;
  this.storage.setItem(record.key,JSON.stringify(record.generation<0?value:{...record,generation:record.generation+1,value:structuredClone(value),at:Date.now()}));return true;
 }
 noteIds(session:string):string[]{
  const ids=new Set<string>();for(let i=0;i<this.storage.length;i++){const key=this.storage.key(i)!;try{if(key.startsWith(`${this.prefix}:recovery:v2:`)){const row=JSON.parse(this.storage.getItem(key)!);if(row.session===session&&row.id!=='layout')ids.add(row.id);}else if(key.startsWith(`${this.prefix}:note:${session}:`))ids.add(key.slice(`${this.prefix}:note:${session}:`.length));}catch{}}return [...ids];
 }
 list<T=Note>(session:string,id:string,options:{strict?:boolean}={}):Recovery<T>[] {
  const read=(key:string):string|null=>{try{return this.storage.getItem(key);}catch(error){if(options.strict)throw error;return null;}};
  const out:Recovery<T>[]=[];
  for(let i=0;i<this.storage.length;i++){
   const key=this.storage.key(i)!;if(!key.startsWith(`${this.prefix}:recovery:v2:`))continue;
   const raw=read(key);
   try{const row=JSON.parse(raw!);if(row.session===session&&row.id===id)out.push({...row,key});}catch{}
  }
  const legacy=id==='layout'?`${this.prefix}:layout-recovery:${session}`:`${this.prefix}:note:${session}:${id}`;
  const raw=read(legacy);
  try{const value=JSON.parse(raw??'null');if(value)out.push({key:legacy,generation:-1,session,id,at:0,value});}catch{}
  return out.sort((a,b)=>b.at-a.at);
 }
}
/** What a note's footer says. It names what happened and always that the draft is kept; Saved only after the
 * server confirmed it (the state machine below sets 'saved' only then). */
export function noteStatusLine(status:NoteState['status'],error:string):{text:string;action:string}{
 const reason=error?` · ${error.replace(/^Error:\s*/,'')}`:'';
 // A draft is kept on this Mac (browser-local recovery) unless that recovery itself failed; then it says so instead.
 const where=/recovery unavailable/i.test(error)?'':' on this Mac';
 switch(status){
  case 'saved':return{text:`Saved${reason}`,action:'Save note'};
  case 'saving':return{text:`Saving…${reason}`,action:'Save note'};
  case 'dirty':return{text:where?`Draft · kept on this Mac, not saved yet${reason}`:`Draft · not saved yet${reason}`,action:'Save note'};
  case 'conflict':return{text:`Changed elsewhere since you opened it · your draft is kept. Compare below.${reason&&error!=='The saved note changed while this save was pending.'?reason:''}`,action:'Save note'};
  case 'error':return{text:`Not saved · your draft is kept${where}${reason}`,action:'Try again'};
 }
}
export class NoteState {
 value:Note;saved:Note;status:'saved'|'dirty'|'saving'|'conflict'|'error'='saved';error='';
 private generation=0;private recovery:Recovery|undefined;private pending:Promise<void>|null=null;private recovered:Recovery|undefined;
 readonly listeners=new Set<()=>void>();
 private store:RecoveryStore;readonly session:string;
 constructor(note:Note,store:RecoveryStore,session:string){this.store=store;this.session=session;this.value={...note};this.saved={...note};}
 receive(note:Note){
  if(note.version<=this.saved.version)return;this.saved={...note};
  if(this.status==='saved')this.value={...note};
  this.changed();
 }
 private changed(){for(const listener of this.listeners)listener();}
 edit(title:string,text:string){
  this.value={...this.value,title,text};this.generation++;
  try{this.recovery=this.store.put(this.session,this.value.id,this.generation,this.value);this.error='';}
  catch{this.error='Browser recovery unavailable. Keep this note open until saved.';}
  if(this.status!=='conflict')this.status='dirty';this.changed();
 }
 recover(candidate:Recovery){
  // Selecting another draft must not replace this writer's only copy of unsaved text.
  if(this.status!=='saved'&&(this.value.title!==candidate.value.title||this.value.text!==candidate.value.text)){
   try{this.store.put(this.session,this.value.id,this.generation,this.value,crypto.randomUUID());}
   catch{this.error='Could not preserve the current draft. Save or copy it before loading another draft.';this.changed();return;}
  }
  this.recovered=candidate;this.edit(candidate.value.title,candidate.value.text);if(this.saved.version>0)this.status='conflict';this.changed();
 }
 merge(){this.value.version=this.saved.version;this.status='dirty';this.edit(this.value.title,this.value.text);}
 useSaved(){if(this.recovered)this.store.clear(this.recovered);this.recovered=undefined;if(this.recovery)this.store.clear(this.recovery);this.recovery=undefined;this.value={...this.saved};this.status='saved';this.error='';this.changed();}
 async save(request:(note:Note)=>Promise<any>):Promise<void>{
  if(this.pending)return this.pending;
  if(this.status==='saved'||this.status==='conflict')return;
  const sent={...this.value},generation=this.generation,recovery=this.recovery,recovered=this.recovered;
  this.status='saving';this.changed();
  this.pending=(async()=>{try{
   const result=await request(sent);
   if(!result.ok){
    if(result.code==='conflict'&&result.current){this.saved={...result.current};this.status='conflict';}
    else this.status='error';this.error=result.error||'Note not saved';return;
   }
   if(result.note.version<this.saved.version){this.status='conflict';this.error='The saved note changed while this save was pending.';return;}
   this.saved={...result.note};this.value.version=this.saved.version;
   if(this.generation===generation){if(recovered){this.store.clear(recovered);if(this.recovered===recovered)this.recovered=undefined;}if(recovery)this.store.clear(recovery);this.recovery=undefined;this.value={...this.saved};this.status='saved';}
   else {if(this.status!=='conflict')this.status='dirty';this.recovery=this.store.put(this.session,this.value.id,this.generation,this.value);}
   this.error='';
  }catch(error){this.status='error';this.error=String(error);}finally{this.changed();}})();
  await this.pending;this.pending=null;
 }
}
