/** Browser-local source recovery. It never restores a kernel or executes code. */
import {RecoveryStore,type Recovery} from './workspace-note-state';
import type {StudyResourceReference} from './workspace-study-restoration';
import {SCRIPT_BYTES} from './scientific-scripts';
export type ScriptDraftBinding={storage:Storage;prefix:string;resource:StudyResourceReference;placement:string};
type Draft={format:'scientific-script-draft-v1';source:string};
const bytes=(source:string)=>new TextEncoder().encode(source).length;
const el=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const node=document.createElement(tag);node.textContent=text;return node;};
function draft(value:unknown):value is Draft {
 if(!value||typeof value!=='object'||Array.isArray(value))return false;
 const row=value as Record<string,unknown>;
 return Object.keys(row).sort().join() === 'format,source' && row.format==='scientific-script-draft-v1' &&
  typeof row.source==='string' && !!row.source && bytes(row.source)<=SCRIPT_BYTES;
}
export class ScientificScriptDrafts {
 readonly element=el('section');
 private readonly status=el('p');
 private readonly recovery=el('details');
 private readonly retry=el('button','Retry draft recovery');
 private readonly store:RecoveryStore;
 private readonly session:string;
 private readonly id:string;
 private generation=0;
 private own:Recovery<Draft>|null=null;
 private urls:string[]=[];
 private closed=false;
 private readonly input:HTMLTextAreaElement;
 private readonly changed:()=>void;
 constructor(input:HTMLTextAreaElement,binding:ScriptDraftBinding,changed:()=>void){
  this.input=input;this.changed=changed;
  this.store=new RecoveryStore(binding.storage,binding.prefix);this.session=binding.resource.day;
  this.id=JSON.stringify([binding.resource.id,binding.resource.version,binding.resource.picture,binding.resource.outputId??null,binding.placement]);
  this.status.setAttribute('role','status');
  const retry=this.retry;retry.hidden=true;retry.type='button';retry.addEventListener('click',()=>{this.edit();this.refresh();});
  this.element.append(el('p','Drafts stay in this browser for this saved revision and window. Restoring a draft never runs code.'),this.status,retry,this.recovery);
  this.refresh();
 }
 edit():void {
  if(this.closed)return;
  if(bytes(this.input.value)>SCRIPT_BYTES){this.status.textContent='This draft exceeds 256 KiB and was not saved for recovery. The earlier saved copy is kept. Shorten or copy this text before leaving.';return;}
  try{
   if(this.input.value){this.own=this.store.put(this.session,this.id,++this.generation,{format:'scientific-script-draft-v1',source:this.input.value});this.status.textContent='Draft kept in this browser.';this.retry.hidden=true;}
   else{if(this.own)this.store.clear(this.own);this.own=null;this.status.textContent='Editor cleared. Other saved drafts are kept.';this.retry.hidden=true;}
  }catch{this.failed();}
 }
 private failed(){this.retry.hidden=false;this.status.textContent='Browser draft recovery unavailable. Your editor and earlier saved copies are kept. Download the draft before leaving, then retry after making browser storage available.';}
 private refresh():void {
  for(const url of this.urls)URL.revokeObjectURL(url);this.urls=[];this.recovery.replaceChildren();
  let rows:Recovery<Draft>[];
  try{rows=this.store.list<Draft>(this.session,this.id,{strict:true}).filter(row=>Number.isSafeInteger(row.generation)&&row.generation>=0&&Number.isFinite(row.at)&&draft(row.value)&&row.key!==this.own?.key);}
  catch{this.failed();return;}
  this.recovery.hidden=!rows.length;
  this.recovery.append(el('summary',`Recover script drafts (${rows.length})`));
  for(const row of rows.slice(0,16)){
   const section=el('section'),preview=el('pre',row.value.source.slice(0,2000));
   section.append(el('p',Number.isFinite(row.at)?new Date(row.at).toLocaleString():'Saved draft'),preview);
   if(row.value.source.length>2000)section.append(el('p','Preview shortened. Restore or download keeps the complete source.'));
   const restore=el('button','Restore script draft');restore.type='button';restore.addEventListener('click',()=>this.restore(row));
   const download=el('a','Download saved draft');download.download='hooke-recovered-script.py';
   download.href=URL.createObjectURL(new Blob([row.value.source],{type:'text/x-python;charset=utf-8'}));this.urls.push(download.href);
   const discard=el('button','Discard saved draft');discard.type='button';let confirmed=false;
   discard.addEventListener('click',()=>{
    if(this.closed)return;
    if(!confirmed){confirmed=true;discard.textContent='Confirm discard from this browser';return;}
    try{this.store.clear(row);this.status.textContent='That saved draft was discarded if unchanged. Editor text and other copies are kept.';this.refresh();}catch{this.failed();}
   });
   section.append(restore,download,discard);this.recovery.append(section);
  }
  if(rows.length>16)this.recovery.append(el('p','Showing the 16 newest drafts. Older drafts remain stored; recover or discard newer copies to reach them.'));
 }
 private restore(candidate:Recovery<Draft>):void {
  if(this.closed)return;
  try{
   const current=this.store.list<Draft>(this.session,this.id,{strict:true}).find(row=>row.key===candidate.key);
   if(!current||current.generation!==candidate.generation||!draft(current.value)||current.value.source!==candidate.value.source){
    this.status.textContent='That draft changed in another window. Review the current copies before restoring.';this.refresh();return;
   }
   if(this.input.value&&this.input.value!==candidate.value.source){
    if(bytes(this.input.value)>SCRIPT_BYTES)throw new Error('Current source needs a download');
    this.store.put(this.session,this.id,++this.generation,{format:'scientific-script-draft-v1',source:this.input.value},crypto.randomUUID());
   }
   const own=this.store.put(this.session,this.id,++this.generation,candidate.value);
   this.own=own;this.input.value=candidate.value.source;
   this.changed();
   this.status.textContent='Draft restored into the editor. Current and earlier copies are kept in this browser. Inspect it before Run code.';
   this.refresh();this.input.focus();
  }catch{this.failed();}
 }
 close():void{this.closed=true;for(const url of this.urls)URL.revokeObjectURL(url);this.urls=[];this.element.replaceChildren();}
}
