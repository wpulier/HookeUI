/** Import exact retained scientific work without executing its producer. */
import {StudioSheet} from './sheet';
export type ImportedBundle={id:string;session:string;revision:number;digest:string};
export type ValidatedBundleOwner={session:string;state:'matched'|'restored';originDigest:string};
type OwnerResolution={available:true;owner:ValidatedBundleOwner}|{available:false;reason:string};
type Options={saved(target:ImportedBundle):void;open(target:ImportedBundle,owner:ValidatedBundleOwner):Promise<void>};
const object=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const words=(value:unknown)=>value instanceof Error?value.message:String(value);
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
function accepted(value:unknown):ImportedBundle{
 if(!object(value)||typeof value.id!=='string'||!/^p[0-9a-f]{32}$/.test(value.id)
   ||typeof value.session!=='string'||!value.session||value.session.length>128
   ||!Number.isSafeInteger(value.revision)||Number(value.revision)<1
   ||typeof value.digest!=='string'||!/^[0-9a-f]{64}$/.test(value.digest))throw new Error('The import response has no exact retained revision.');
 return {id:value.id,session:value.session,revision:Number(value.revision),digest:value.digest};
}
function ownership(value:unknown,target:ImportedBundle):OwnerResolution{
 if(object(value)&&value.session===target.session){
  if((value.state==='matched'||value.state==='restored')&&typeof value.originDigest==='string'&&/^[0-9a-f]{64}$/.test(value.originDigest))
   return {available:true,owner:{session:value.session,state:value.state,originDigest:value.originDigest}};
  if(value.state==='unavailable'&&typeof value.reason==='string'&&value.reason.trim()&&value.reason.length<=4096)
   return {available:false,reason:value.reason};
 }
 return {available:false,reason:'The response did not validate the original Project owner. Keep this bundle and export an owner-aware bundle from its original profile before trying again.'};
}
export class ScientificBundleImport {
 readonly content=node('section');
 private readonly sheet=new StudioSheet('Import native bundle');
 private readonly picker=node('input');
 private readonly start=node('button','Import bundle');
 private readonly show=node('button','Open imported work');
 private readonly status=node('p','Choose a native bundle exported by Hooke. Its original Project identities and accepted revisions stay intact.');
 private readonly progress=node('progress');
 private file:File|null=null;
 private target:ImportedBundle|null=null;
 private owner:ValidatedBundleOwner|null=null;
 private pending=false;
 private readonly options:Options;
 constructor(options:Options){
  this.options=options;
  this.picker.type='file';this.picker.accept='.zip';this.picker.setAttribute('aria-label','Native Hooke bundle');
  this.start.type=this.show.type='button';this.start.disabled=true;this.show.hidden=true;
  this.status.setAttribute('role','status');this.progress.setAttribute('aria-label','Importing native bundle');this.progress.hidden=true;
  const label=node('label','Native Hooke bundle ');label.append(this.picker);
  this.content.append(label,this.start,this.progress,this.status,this.show);
  this.picker.addEventListener('change',()=>{
   if(this.pending)return;
   this.file=this.picker.files?.[0]??null;this.target=null;this.owner=null;this.show.hidden=true;
   this.start.disabled=!this.file||this.file.size===0||this.file.size>257*1024*1024;
   this.status.textContent=this.start.disabled?'Choose a nonempty exported Hooke bundle up to 257 MiB.':`Ready to import ${this.file!.name}. Existing work is never overwritten; nothing in the bundle runs.`;
  });
  this.start.addEventListener('click',()=>{void this.import();});
  this.show.addEventListener('click',()=>{void this.open();});
 }
 showSheet():void{this.sheet.open(this.content);}
 private async import():Promise<void>{
  if(this.pending||!this.file||this.start.disabled)return;
  const file=this.file;this.owner=null;this.pending=true;this.picker.disabled=true;this.start.disabled=true;this.show.hidden=true;
  this.progress.hidden=false;this.status.textContent='Uploading and verifying native evidence… Keep this file until acceptance is confirmed.';
  try{
   const response=await fetch('/api/scientific-bundles',{method:'POST',headers:{'X-MicroscopeMind':'import-bundle'},body:file});
   const value:unknown=await response.json();
   if(!response.ok||!object(value)||value.ok!==true)throw new Error(object(value)?`${String(value.says??'Import was refused.')} ${String(value.next??'')}`:'The import response was incomplete.');
   this.target=accepted(value.target);
   const resolution=ownership(value.owner,this.target);
   if(resolution.available){
    this.owner=resolution.owner;this.options.saved(this.target);
    const state=this.owner.state==='restored'?'Its Project identity was restored; the full conversation and workspace were not restored.':'Its original Project owner was matched.';
    this.status.textContent=`Native evidence imported, revision ${this.target.revision}. ${state} Nothing was executed. Open the accepted work explicitly.`;
    this.show.hidden=false;
   }else{
    this.status.textContent=`Scientific data imported; original Project ownership is unavailable. ${resolution.reason} Open is unavailable. Nothing was executed.`;
   }
  }catch(error){this.status.textContent=`${words(error)} Acceptance is not confirmed. Keep the original file and retry it to recover an import that may have completed. Existing accepted work is never overwritten.`;}
  finally{this.pending=false;this.picker.disabled=false;this.start.disabled=false;this.progress.hidden=true;}
 }
 private async open():Promise<void>{
  if(this.pending||!this.target||!this.owner||this.show.disabled)return;
  const target=this.target,owner=this.owner;this.pending=true;this.picker.disabled=true;this.start.disabled=true;this.show.disabled=true;
  this.status.textContent='Opening the accepted work in its validated Project… Nothing is executed.';
  try{await this.options.open(target,owner);this.status.textContent=`Accepted revision ${target.revision} opened in its validated Project. Nothing was executed.`;this.sheet.close();}
  catch(error){this.status.textContent=`${words(error)} Imported native evidence remains retained. Return to the validated original Project and retry Open; this import does not restore its full conversation or workspace.`;}
  finally{this.pending=false;this.picker.disabled=false;this.start.disabled=false;this.show.disabled=false;}
 }
}
let importer:ScientificBundleImport|null=null;
/** Retain the exact selected file and pending receipt when the sheet is dismissed. */
export function openScientificBundleImport(options:Options):void{
 importer??=new ScientificBundleImport(options);importer.showSheet();
}
