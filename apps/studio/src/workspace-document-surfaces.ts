import {rectOf,surfaceVisible,type StudioSurface,type StudioSurfaceLegacy} from './screen';
import { scientificPresentation } from './scientific-presentation';
/** The Data reader (data-reader.ts) as the screen report says it: the item it shows by its exact identity, its view
 * (the thing, or How it was made) and whether it is the right edge's reader or a reader Tab's. Reported as a document
 * surface (`document:reader-…`, the one structural kind a server of any version takes for a thing on screen), so the
 * Assistant sees it and "done" stays what the studio shows (on-screen.md). */
export function readerSurfaces():StudioSurface[]{
 // Three readers: the right edge's, a reader Tab's and a researcher's Tab's (ticket 66 D3), each its own identity.
 return [...document.querySelectorAll<HTMLElement>('.data-reader[data-reader-resource]')].filter(root=>!!root.dataset?.readerResource).slice(0,3).map(root=>{
  const mode=root.classList.contains('data-reader-tab')?'tab':root.classList.contains('data-reader-research')?'research':'side';
  const id=`document:reader-${mode}`;
  return {id,root,read:()=>{
   let resource:{day:string;id:string;version:number|null;picture:number|null};
   try{const value=JSON.parse(root.dataset.readerResource!);resource={day:String(value.day),id:String(value.id),version:typeof value.version==='number'?value.version:null,picture:null};}
   catch{resource={day:document.body?.dataset.workspaceProject??'',id:'unknown',version:null,picture:null};}
   const open=!root.hidden&&root.dataset.readerOpen==='true',rect=rectOf(root.getBoundingClientRect()),visible=surfaceVisible(root,open,true,rect);
   const project=document.body?.dataset.workspaceProject||resource.day,tab=document.body?.dataset.activeWorkspaceView||'none';
   const words=[mode==='tab'?'Reader Tab':mode==='research'?'Researcher’s reader':'Reader',root.dataset.readerTitle,root.dataset.readerView].filter(Boolean).join(' · ');
   const legacy:StudioSurfaceLegacy={schema:'studio-surface-v1',scope:'dom-observation',title:words.slice(0,120),shows:resource.id,
    document:{project,tab,placement:`reader:${mode}:${tab}`,resource,representation:mode==='tab'?'reader-tab':mode==='research'?'reader-research':'reader',state:root.dataset.displayState??'requested',focused:root.contains(document.activeElement)}};
   // A reader Tab's reader is the Tab's own page, under the windows opened on it (data-reader.css lifts their layer).
   return {kind:'document',open,visible,rect,z:mode==='side'?105:mode==='tab'?1:40,space:'workspace',legacy,contentKey:JSON.stringify({resource,mode,view:root.dataset.readerView??''}),
    controls:visible?[...root.querySelectorAll<HTMLButtonElement>('button')].map((node,index)=>({node,fact:{id:`${id}:control:${index}`,targetId:id,role:node.getAttribute('aria-label')?.startsWith('Close')?'close':'button',present:open&&surfaceVisible(node,true,true,rectOf(node.getBoundingClientRect())),enabled:!node.disabled,editable:false as const,stateRevision:null,observedValue:null}})).filter(control=>control.fact.present).slice(0,40):[]};
  }};
 });
}
/** DOM evidence only: these documents never claim native source or paint authority. */
export function documentSurfaces():StudioSurface[]{
 return [...readerSurfaces(),...windowSurfaces()];
}
/** A web window (web-window.ts): what it shows by its Data item, its kind (web-page or video) as its representation. */
function webOf(root:HTMLElement):{kind:string;resource:{day:string;id:string;version:number|null}}|null{
 if(!root.dataset.webWindow)return null;
 try{const web=JSON.parse(root.dataset.webWindow);return web?.resource?.id?{kind:web.kind==='video'?'video':'web-page',resource:web.resource}:null;}catch{return null;}
}
function windowSurfaces():StudioSurface[]{
 return [...document.querySelectorAll<HTMLElement>('#study-artifacts > [data-placement-id]')].filter(root=>!root.classList.contains('workspace-window')&&(root.dataset.noteId||root.dataset.studyRetained||webOf(root))).sort((a,b)=>Number(b.dataset.workspaceHidden!=='true')-Number(a.dataset.workspaceHidden!=='true')).slice(0,192).map(root=>{
  const id=`document:${root.dataset.placementId}`;
  return {id,root,read:()=>{
   const retained=root.dataset.studyRetained?JSON.parse(root.dataset.studyRetained):null,web=webOf(root);
   const resource=web?{...web.resource,picture:null}:retained?.resource??{day:root.dataset.project,id:root.dataset.noteId,version:Number(root.dataset.noteVersion)||null,picture:null};
   const open=root.dataset.dismissed!=='true',rect=rectOf(root.getBoundingClientRect());
   const scientific=scientificPresentation(root),visible=surfaceVisible(root,open,true,rect);
   const legacy:StudioSurfaceLegacy={schema:'studio-surface-v1',scope:'dom-observation',title:(root.getAttribute('aria-label')??'Document').slice(0,120),shows:resource.id,
    document:{project:root.dataset.project!,tab:root.dataset.workspaceView!,placement:root.dataset.placementId!,resource,representation:web?web.kind:root.dataset.noteId?'note':retained.presentation??'resource',state:root.dataset.noteId?(root.dataset.noteState==='saved'?'saved-note':'draft-note'):root.dataset.displayState??'requested',focused:root.contains(document.activeElement)}};
   if(scientific)legacy.document!.representation='scientific-review';
   return {kind:'document',open,visible,rect,z:Number(root.style.zIndex)||10,space:'workspace',legacy,...(scientific?{scientificPaint:scientific.paint,...(scientific.panelPaint===undefined?{}:{scientificPanelPaint:scientific.panelPaint})}:{}),contentKey:JSON.stringify({resource,project:root.dataset.project,tab:root.dataset.workspaceView,placement:root.dataset.placementId,representation:legacy.document!.representation}),
    controls:visible?[...root.querySelectorAll<HTMLButtonElement>('button')].map((node,index)=>({node,fact:{id:`${id}:control:${index}`,targetId:id,role:node.getAttribute('aria-label')?.startsWith('Close')?'close':'button',present:open&&surfaceVisible(node,true,true,rectOf(node.getBoundingClientRect())),enabled:!node.disabled,editable:false as const,stateRevision:null,observedValue:null}})).filter(control=>control.fact.present).slice(0,40):[]};
  }};
 });
}
