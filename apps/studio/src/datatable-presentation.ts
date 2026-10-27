import {DataTableView, type DataTableOwner, type DataTablePin, type DataTableSnapshot} from './datatable-view';
import {studyArtifacts} from './workspace-study-artifacts';
import type {StudyRetainedView, StudyOpenOptions, StudyResourceReference} from './workspace-study-restoration';

export interface DataTableDocuments {
 owner():DataTableOwner;
 settle():Promise<void>;
 current(owner:DataTableOwner):boolean;
 remember(project:string,placement:string,previous:StudyResourceReference,next:StudyResourceReference,title:string):Promise<void>;
 analyze?(pin:DataTablePin,table:DataTableSnapshot,owner:DataTableOwner):void|Promise<void>;
 graph?(pin:DataTablePin,table:DataTableSnapshot,owner:DataTableOwner):void|Promise<void>;
 export?(pin:DataTablePin,table:DataTableSnapshot,owner:DataTableOwner):void|Promise<void>;
}
let documents:DataTableDocuments|null=null;
const reopenTables=new WeakMap<HTMLElement,()=>Promise<void>>();
export function configureDataTableDocuments(value:DataTableDocuments):void {documents=value;}
export function dataTableDocuments():DataTableDocuments {if(!documents)throw new Error('Open a Project first.');return documents;}
const key=(saved:StudyRetainedView)=>`datatable:${saved.resource.day}:${saved.resource.id}:${saved.resource.version}:${saved.placementId??''}`;
const button=(text:string,action:()=>void)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.addEventListener('click',action);return b;};

/** A retained table shares the Project's window shell and exact versioned arrangement. */
export async function openStudyDataTable(saved:StudyRetainedView,options:StudyOpenOptions={}):Promise<void>{
 const deps=documents;if(!deps)throw new Error('Open a Project before opening this table.');
 const owner=deps.owner(),ref={...saved.resource};
 if(!deps.current(owner)||owner.session!==ref.day||!Number.isSafeInteger(ref.version)||Number(ref.version)<1)
  throw new Error('Open this table in its originating Project and saved revision.');
 saved={...saved,resource:ref,presentation:'datatable'};
 const content=document.createElement('section');let view:DataTableView;
 const root=studyArtifacts().document(key(saved),saved.title,content,()=>view?.hide(),
  {studyRetained:JSON.stringify(saved),...(saved.placementId?{placementId:saved.placementId}:{})},options);
 if(!root.contains(content)){await reopenTables.get(root)?.();return;}
 studyArtifacts().restoreGeometry(root,saved);
 let pin:DataTablePin={session:ref.day,id:ref.id,version:Number(ref.version)},snapshot:DataTableSnapshot|null=null;
 const current=()=>deps.current(owner)&&root.isConnected&&root.dataset.dismissed!=='true'&&root.dataset.project===owner.session;
 const active=()=>current()&&root.dataset.workspaceView===document.body.dataset.activeWorkspaceView&&root.dataset.workspaceHidden!=='true';
 const note=document.createElement('p');note.setAttribute('role','status');
 const updatePin=async(next:DataTablePin,table:DataTableSnapshot)=>{
  if(!current())throw new Error('The table was saved in Data. Reopen its original Project to see it.');
  const previous={day:pin.session,id:pin.id,version:pin.version,picture:null};
  const accepted={day:next.session,id:next.id,version:next.version,picture:null};
  await deps.remember(owner.session,root.dataset.placementId??'',previous,accepted,table.title);
  pin={...next};snapshot=table;root.dataset.contentKey=key({...saved,resource:accepted});
  studyArtifacts().documentTitle(root,table.title);
 };
 view=new DataTableView(content,{
  current:()=>current(),
  loaded:async(next,table)=>{snapshot=table;root.dataset.displayState='rendered';if(next.version!==pin.version)await updatePin(next,table);},
  saved:updatePin,
  showMember:async member=>{const tab=root.dataset.workspaceView;if(!active())throw new Error('Return to this table’s Tab to select its retained cell.');
   const {openStudyMaskMember}=await import('./workspace-study-content');
   if(!active()||root.dataset.workspaceView!==tab)throw new Error('The table’s Project or Tab changed. Select the row again there.');
   await openStudyMaskMember(member,selected=>{if(current())view.selectMember(selected);});},
  analyze:deps.analyze?async next=>{if(active()&&snapshot)await deps.analyze!(next,snapshot,owner);}:undefined,
  graph:deps.graph?async next=>{if(active()&&snapshot)await deps.graph!(next,snapshot,owner);}:undefined,
  export:deps.export?async next=>{if(active()&&snapshot)await deps.export!(next,snapshot,owner);}:undefined,
 });
 root.append(note);
 studyArtifacts().guardDocument(root,()=>view.busy?'Wait for the table save to finish.':view.dirty?'Save or discard the table draft before closing this Tab or Project.':null);
 root.addEventListener('workspace-document-close-blocked',()=>{
  if(view.busy){note.textContent='Saving this table… Wait before closing.';return;}
  note.replaceChildren(document.createTextNode('Keep your table changes? '),
   button('Save and close',()=>{void view.saveDraft().then(ok=>{if(ok&&current())root.querySelector<HTMLButtonElement>('header button[aria-label^="Close"]')?.click();});}),
   button('Discard and close',()=>{if(!current()||view.busy)return;view.discardDraft();root.querySelector<HTMLButtonElement>('header button[aria-label^="Close"]')?.click();}),
   button('Cancel',()=>note.replaceChildren()));
 });
 const open=async()=>{if(!current()||view.dirty||view.busy)return;root.dataset.displayState='loading';if(!await view.show(pin,owner)&&current())root.dataset.displayState='failed';};
 reopenTables.set(root,async()=>{if(root.dataset.displayState!=='rendered')await open();});
 await open();
}

/** Creation captures one existing measured result; it neither recomputes nor silently changes Projects. */
export async function makeEditableTable(saved:StudyRetainedView,sourceWindow:HTMLElement):Promise<void>{
 const deps=documents;if(!deps)throw new Error('Open a Project before creating a table.');
 const owner=deps.owner(),tab=sourceWindow.dataset.workspaceView,placement=sourceWindow.dataset.placementId,ref={...saved.resource};
 const current=()=>deps.current(owner)&&owner.session===ref.day&&sourceWindow.isConnected&&
  sourceWindow.dataset.project===owner.session&&sourceWindow.dataset.workspaceView===tab&&
  document.body.dataset.activeWorkspaceView===tab&&sourceWindow.dataset.placementId===placement&&sourceWindow.dataset.dismissed!=='true';
 if(!current())throw new Error('Return to the original measured result before creating its table.');
 const response=await fetch('/api/datatables',{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'datatable'},
  body:JSON.stringify({session:owner.session,title:saved.title,sources:[{id:ref.id,revision:ref.version??0}],mode:/^p[0-9a-f]{32}$/.test(ref.id)?'frozen':'follow'})});
 const reply=await response.json();if(!response.ok)throw new Error(reply.error||'The editable table could not be created.');
 if(typeof reply.id!=='string'||!Number.isSafeInteger(reply.version)||reply.version<1)throw new Error('The saved table reference is unavailable. Check Data before trying again.');
 if(!current())throw new Error('The table was saved in its original Project’s Data. Return there to open it.');
 await openStudyDataTable({resource:{day:owner.session,id:reply.id,version:reply.version,picture:null},title:reply.title??saved.title,zoom:1,pan:[.5,.5]});
}
