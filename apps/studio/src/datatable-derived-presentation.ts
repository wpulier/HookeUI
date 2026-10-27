import {TableDerivedView,checkedDerived,derivedURL,type DerivedKind,type DerivedRecord} from './table-derived-view';
import {dataTableDocuments} from './datatable-presentation';
import {studyArtifacts} from './workspace-study-artifacts';
import type {DataTableOwner,DataTablePin,DataTableSnapshot} from './datatable-view';
import type {StudyRetainedView,StudyOpenOptions} from './workspace-study-restoration';

const reopenDerived=new WeakMap<HTMLElement,()=>Promise<void>>();

/** Only accepted analyses for this exact table revision are eligible annotation choices. */
export async function savedTableAnalyses(pin:DataTablePin,owner:DataTableOwner):Promise<DerivedRecord[]>{
 const deps=dataTableDocuments();if(!deps.current(owner)||owner.session!==pin.session)return [];
 const response=await fetch(`/api/resources?${new URLSearchParams({day:pin.session,kind:'analysis'})}`,{cache:'no-store'});
 if(!response.ok)throw new Error('Saved analyses could not be read.');
 const body=await response.json();if(!deps.current(owner))return [];
 if(!Array.isArray(body.entries))throw new Error('Saved analysis index is unavailable.');
 const entries=body.entries.filter((e:{kind:string;day:string;values?:{source?:DataTablePin}})=>e.kind==='analysis'&&e.day===pin.session&&e.values?.source?.id===pin.id&&e.values.source.version===pin.version);
 if(entries.length>100)throw new Error('More than 100 analyses match this revision. Open the desired saved analysis from Data; graph annotation selection is limited to 100.');
 const results:DerivedRecord[]=[];
 for(const entry of entries){
  if(!deps.current(owner))return [];
  const exact={session:pin.session,id:entry.id,version:entry.version},reply=await fetch(derivedURL(exact),{cache:'no-store'});
  if(!reply.ok)throw new Error('A saved analysis is unavailable; refresh Data before choosing it.');
  const record=checkedDerived(await reply.json(),pin.session,exact);
  if(record.kind==='analysis'&&record.source.id===pin.id&&record.source.version===pin.version)results.push(record);
 }
 return deps.current(owner)?results:[];
}
function windowFor(kind:DerivedKind,owner:DataTableOwner,saved?:StudyRetainedView,options:StudyOpenOptions={}){
 const deps=dataTableDocuments();if(!deps.current(owner))throw new Error('The Project changed. Open this result in its original Project.');
 const content=document.createElement('section');let view:TableDerivedView;
 const key=saved?`${kind}:${saved.resource.day}:${saved.resource.id}:${saved.resource.version}:${saved.placementId??''}`:`${kind}-setup:${crypto.randomUUID()}`;
 const root=studyArtifacts().document(key,saved?.title??(kind==='analysis'?'Analyze saved table':'Graph saved table'),content,()=>view?.hide(),
  saved?{studyRetained:JSON.stringify(saved),...(saved.placementId?{placementId:saved.placementId}:{})}:{workspaceUnsaved:'true'},options);
 if(!root.contains(content)){void reopenDerived.get(root)?.();return null;}
 if(saved)studyArtifacts().restoreGeometry(root,saved);
 let retained=saved;
 const tab=root.dataset.workspaceView,placement=root.dataset.placementId;
 const current=()=>deps.current(owner)&&root.isConnected&&root.dataset.project===owner.session&&root.dataset.dismissed!=='true';
 view=new TableDerivedView(content,{current:()=>current(),analyses:pin=>savedTableAnalyses(pin,owner),saved:async record=>{
  if(!current()||root.dataset.workspaceView!==tab||root.dataset.placementId!==placement)throw new Error('The original result window changed. Its accepted result remains in Data.');
  const resource={day:owner.session,id:record.id,version:record.version,picture:null};
  if(retained)await deps.remember(owner.session,placement??'',retained.resource,resource,record.title);
  else{
   if(document.body.dataset.activeWorkspaceView!==tab)throw new Error('Return to the original Tab or open this saved result from Data.');
   root.dataset.studyRetained=JSON.stringify({resource,title:record.title,presentation:kind,zoom:1,pan:[.5,.5],placementId:placement});
   delete root.dataset.workspaceUnsaved;window.dispatchEvent(new CustomEvent('workspace-item-mounted',{detail:root}));
   await deps.settle();
  }
  retained={resource,title:record.title,presentation:kind,zoom:1,pan:[.5,.5],placementId:placement};
  root.dataset.contentKey=`${kind}:${owner.session}:${record.id}:${record.version}:${placement??''}`;
  root.dataset.displayState='rendered';studyArtifacts().documentTitle(root,record.title);
 }});
 studyArtifacts().guardDocument(root,()=>view.busy?'Wait for this calculation and save to finish.':view.dirty?'Calculate and save, or discard the analysis/graph setup before closing this Tab or Project.':null);
 const note=document.createElement('p');note.setAttribute('role','status');root.append(note);
 root.addEventListener('workspace-document-close-blocked',()=>{
  if(view.busy){note.textContent='Wait for calculation and save to finish.';return;}
  const discard=document.createElement('button');discard.textContent='Discard setup and close';discard.onclick=()=>{if(!current()||view.busy)return;view.discardDraft();root.querySelector<HTMLButtonElement>('header button[aria-label^="Close"]')?.click();};
  const cancel=document.createElement('button');cancel.textContent='Keep editing';cancel.onclick=()=>note.replaceChildren();
  note.replaceChildren(document.createTextNode('Use Calculate and save to keep this setup, or discard it. '),discard,cancel);
 });
 return {root,view};
}
export function setupTableDerived(kind:DerivedKind,pin:DataTablePin,table:DataTableSnapshot,owner:DataTableOwner):void{
 if(pin.session!==owner.session)throw new Error('This table belongs to another Project.');
 const item=windowFor(kind,owner);if(item&&!item.view.setup(kind,pin,table,owner))throw new Error('The setup could not open. Return to the saved table and try again.');
}
export async function openTableDerived(saved:StudyRetainedView,options:StudyOpenOptions={}):Promise<void>{
 const deps=dataTableDocuments(),owner=deps.owner(),kind=saved.presentation;
 if((kind!=='analysis'&&kind!=='graph')||owner.session!==saved.resource.day||!Number.isSafeInteger(saved.resource.version)||Number(saved.resource.version)<1)
  throw new Error('Open this saved result in its originating Project and exact revision.');
 const item=windowFor(kind,owner,saved,options);if(!item)return;
 const open=async()=>{if(!deps.current(owner)||!item.view.canClose())return;item.root.dataset.displayState='loading';
 const ok=await item.view.open({session:owner.session,id:saved.resource.id,version:Number(saved.resource.version)},owner);
 if(deps.current(owner))item.root.dataset.displayState=ok?'rendered':'failed';};
 reopenDerived.set(item.root,async()=>{if(item.root.dataset.displayState!=='rendered')await open();});
 await open();
}
