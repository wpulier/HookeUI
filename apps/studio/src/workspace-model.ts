/** One truth for the workspace (live partner spec 4.8, horizontal 2; docs/architecture/on-screen.md "The workspace
 * model"): what each Tab holds, by kind, said the same way to the Assistant's `tabs list` (workspace-views.ts
 * agentCommand) and the screen report's Tab strip (main.ts), so "which Tab has the measurements?" is answered from what
 * a Tab holds, never from its title.
 *
 * A Tab holds its saved items (the arrangement's windows: masks, tables, captures, documents...), the windows open on it
 * that the arrangement does not save (a live magnifier, a reference photo, a web page or video), its notes and its
 * specimen. Each item has one kind from a short fixed list. */
import type {StudyRetainedView} from './workspace-study-restoration';
import type {WorkspaceView} from './workspace-views-model';
import {readerItem} from './workspace-views-model';

/** What a thing on a Tab is, in the words the Assistant uses (workspace_observation.ITEM_KINDS says the same). */
export const ITEM_KINDS=['table','outlines','tracks','image','reference','document','graph','video','web-page','magnifier','note'] as const;
export type ItemKind=typeof ITEM_KINDS[number];
/** At most this many items of a Tab are listed in full (tabs list); the rest are counted. */
export const LIST_ITEMS=64;

/** One thing on a Tab. `placement` is its window's placement id on that Tab (what present, tabs move and only name);
 * `window` the native window's id (w-…) when it is one; `resource` the Data item it shows; `presentation` how
 * (masks: outlines on their capture; table: its measurements; capture; datatable; analysis; graph), `source` a web
 * window's site and link. */
export interface TabItemFact {
 placement:string;kind:ItemKind;title:string;presentation?:string;
 resource?:{day:string;id:string;version:number|null};window?:string;source?:{site:string;url:string};
}

/** A saved item's kind, from how it is presented and, when it has no presentation, what its resource is. */
export function itemKind(item:Pick<StudyRetainedView,'presentation'|'resource'>,native?:{kind?:string;media?:string}|null):ItemKind{
 switch(item.presentation){
  case 'masks':return 'outlines';
  case 'table':case 'datatable':case 'analysis':return 'table';
  case 'graph':return 'graph';
  case 'capture':return 'image';
 }
 if(native?.kind)return nativeKind(native.kind,native.media??'');
 const id=String(item.resource?.id??'');
 if(/^e\d+$/.test(id))return 'outlines';
 if(/^m\d+$/.test(id))return 'tracks';
 if(/^d\d+$/.test(id))return 'document';
 if(/^p[0-9a-f]{32}$/.test(id))return 'image';
 return item.resource?.picture!=null?'image':'document';
}

/** A native window's kind from its source (workspace-windows.ts): the live view magnified, a reference photo, a video,
 * a written report or lesson, or another picture. */
export function nativeKind(kind:string,media:string):ItemKind{
 if(kind==='live')return 'magnifier';
 if(kind==='video')return 'video';
 if(kind==='web-page')return 'web-page';
 if(kind==='written'||kind==='lesson')return 'document';
 return media==='reference'||media==='external-reference'?'reference':'image';
}

/** A reader Tab's one item: the Data item it shows full width, its kind from its id. */
function readerFact(view:WorkspaceView):TabItemFact[]{
 const item=readerItem(view);if(!item)return [];
 const id=item.resource.id;
 const kind:ItemKind=/^e\d+$/.test(id)?'outlines':/^m\d+$/.test(id)?'tracks':'document';
 return [{placement:`reader:tab:${view.id}`,kind,title:item.title.slice(0,120),resource:{...item.resource}}];
}

/** What a window root on the Stage says it is (data attributes the window owners set): a web window
 * (web-window.ts `webWindow`), a native window (workspace-windows.ts `sourceKind`, `sourceMedia`), else nothing. */
function rootFact(root:HTMLElement):Omit<TabItemFact,'placement'>|null{
 const title=(root.getAttribute('aria-label')||root.querySelector('header h3, .workspace-window-title')?.textContent||'').trim().slice(0,120);
 if(root.dataset.webWindow){
  try{
   const web=JSON.parse(root.dataset.webWindow) as {kind?:string;site?:string;url?:string;page?:string;resource?:TabItemFact['resource']};
   return {kind:web.kind==='video'?'video':'web-page',title:title||web.site||'Web page',source:{site:String(web.site??''),url:String(web.page??web.url??'')},...(web.resource?{resource:web.resource}:{})};
  }catch{return null;}
 }
 if(root.classList.contains('workspace-window')&&root.dataset.sourceKind){
  return {kind:nativeKind(root.dataset.sourceKind,root.dataset.sourceMedia??''),title:title||'Window',...(root.dataset.presentationId?{window:root.dataset.presentationId}:{})};
 }
 return null;
}

/** The windows open on a Tab that its arrangement does not save (a web page or video, a native window not yet
 * captured), each once, after `out`. */
function unsaved(here:readonly HTMLElement[],out:TabItemFact[]):TabItemFact[]{
 const saved=new Set(out.map(i=>i.placement));
 for(const root of here){
  const placement=root.dataset.placementId;if(!placement||saved.has(placement)||root.dataset.windowOpen==='false')continue;
  const fact=rootFact(root);if(fact){out.push({placement,...fact});saved.add(placement);}
 }
 return out;
}

/** Everything a work Tab holds, in its saved order (bottom to top), then the windows open on it that the arrangement
 * does not save. `roots` are the window roots of this Project's Stage (every Tab's; each says its Tab). A root that is
 * closed (dismissed) is not held. A reader Tab holds its one item and the windows opened over it (will-research,
 * 29 Oct: a video opened on a document's Tab was in no Tab's holds). */
export function tabItems(view:WorkspaceView,roots:readonly HTMLElement[]):TabItemFact[]{
 const here=roots.filter(root=>root.dataset.workspaceView===view.id&&root.dataset.dismissed!=='true');
 if(view.kind==='reader')return unsaved(here,readerFact(view));
 const byPlacement=new Map(here.filter(root=>root.dataset.placementId).map(root=>[root.dataset.placementId!,root]));
 // A window closed by its × stays mounted, dismissed: its arrangement is kept, but the Tab no longer holds it
 // (will-display, 29 Oct: after "only the video", Main still listed the closed table, and Hooke hedged it might be open).
 const closed=new Set(roots.filter(root=>root.dataset.workspaceView===view.id&&root.dataset.placementId
  &&(root.dataset.dismissed==='true'||root.dataset.windowOpen==='false')&&!byPlacement.has(root.dataset.placementId))
  .map(root=>root.dataset.placementId!));
 const out:TabItemFact[]=[];
 for(const item of view.items){
  if(item.placementId&&closed.has(item.placementId))continue;
  const root=item.placementId?byPlacement.get(item.placementId):undefined;
  const native=root?.classList.contains('workspace-window')?{kind:root.dataset.sourceKind,media:root.dataset.sourceMedia}:null;
  out.push({placement:item.placementId??'',kind:itemKind(item,native),title:String(item.title||'Untitled window').slice(0,120),
   ...(item.presentation?{presentation:item.presentation}:{}),
   ...(item.resource?{resource:{day:item.resource.day,id:item.resource.id,version:item.resource.version??null}}:{}),
   ...(root?.dataset.presentationId?{window:root.dataset.presentationId}:{})});
 }
 const saved=new Set(out.map(i=>i.placement));
 for(const note of view.notes)if(note.placementId&&!saved.has(note.placementId)){out.push({placement:note.placementId,kind:'note',title:'Note'});saved.add(note.placementId);}
 return unsaved(here,out);
}

/** A Tab's items counted by kind, the screen report's compact form (main.ts Tab strip; workspace_observation `holds`). */
export function holds(items:readonly TabItemFact[]):Partial<Record<ItemKind,number>>{
 const out:Partial<Record<ItemKind,number>>={};
 for(const item of items)out[item.kind]=(out[item.kind]??0)+1;
 return out;
}

/** What a researcher's or Study's Tab shows (it keeps its own work): its conversation and the work it made, by kind.
 * Each piece of work is named by its Data id (`<rid>:<output id>`, research_entries.py) when its researcher is known,
 * the id present, tabs add and the reader take; a bare output id (o1) is also an outline's and is taken by none. */
export interface ResearchShows {kind:ItemKind;title:string;id?:string}
const OUTPUT_KINDS:Record<string,ItemKind>={image:'image',picture:'image',video:'video',html:'document',report:'document',lesson:'document',presentation:'document',figure:'graph',table:'table',mask:'outlines'};
export function researchShows(outputs:readonly {kind?:string;contentKind?:string;caption?:string;id?:string;current?:boolean}[]|undefined,limit=12,rid?:string):ResearchShows[]{
 const shows:ResearchShows[]=[{kind:'document',title:'Its conversation'}];
 for(const output of outputs??[]){
  if(output.current===false)continue;
  const kind=OUTPUT_KINDS[output.contentKind??'']??OUTPUT_KINDS[output.kind??'']??'document';
  shows.push({kind,title:String(output.caption||output.contentKind||output.kind||'Work').slice(0,120),...(output.id?{id:rid?`${rid}:${output.id}`:output.id}:{})});
  if(shows.length>=limit)break;
 }
 return shows;
}
