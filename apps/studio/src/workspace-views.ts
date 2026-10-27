import {CameraInputUnavailable} from './workspace-input';
import {scientificViewPreferences,type ScientificViewPreferences} from './scientific-view-preferences';
import {sameStudyResource} from './workspace-study-restoration';
import {studyArtifacts} from './workspace-study-artifacts';
import {legacyResearcher} from './legacy-output-tab';
import {LAYOUT_SCHEMA,LAYOUT_SCHEMAS,setTabKind,bumpForSchema5,pruneLanes,renewPlacements,mainLayout,tileRects,undoArrange,readerItem,readerView,layoutForServer,type LayoutSchema,type WorkspaceLayout,type WorkspaceView,type WorkspaceTabInput,type Rect,type ReaderTabItem} from './workspace-views-model';
import {NoteState,RecoveryStore,noteStatusLine,type Note,type Recovery} from './workspace-note-state';
import type {StudyRetainedView,StudyRecovery,StudyOpenOptions} from './workspace-study-restoration';
import {fitTabRow,measureTabRow,type TabRowParts} from './tab-row-fit';
import {holdUntilLanded} from './side-motion';
import {flyTo} from './tab-close-motion';
import {tabMark,type TabMark} from './tab-marks';
import './workspace-views.css';
interface Options {
 /** More things "+" can add beside a Tab (a researcher, a study), each opening as a Tab of its own. Without them "+" adds a Tab. */
 addChoices?:()=>{label:string;title:string;act:()=>void}[];
 /** Tabs of the row that are views, not saved Tabs (an open Study, the New researcher draft), closed by their ×: listed under Closed in Manage Tabs… with Reopen (All Tabs lists them through `menuTabs`). */
 extraClosed?:()=>{id:string;name:string;reopen:()=>void}[];
 /** All Tabs (⌄) lists these after the work Tabs: closed researcher and Study Tabs, and studies not open as a Tab. */
 menuTabs?:()=>{closed:MenuTab[];studies:MenuTab[]};
 /** Where a closed Tab's content lives, for the close motion: Data (an input or result Tab) or Researchers. */
 closeTarget?:(kind:'data'|'researchers')=>HTMLElement|null;
 /** Leave a researcher or Study page in the Stage's place, as a click on a work Tab does; false when it cannot. */
 leaveResearch?:()=>boolean;
 /** Researcher and Study Tabs for the Assistant: listed in its Tab report, and its switch and close reach them. */
 research?:{report:()=>ResearchTabReport[];switch:(id:string)=>boolean|Promise<boolean>;close:(id:string)=>boolean};
 /** A researcher whose own Tab is in the row: its server-made results Tab is kept, but not opened beside it. */
 researcherTab?:(rid:string)=>boolean;
 /** A Tab input's kind, for its mark and its short label (a recording or a picture). */
 describeInput?:(source:string)=>'recording'|'picture'|null;
 /** A Tab's input's thumbnail (an image URL), which an iconified picture or recording Tab shows instead of its mark. */
 inputThumb?:(source:string)=>string|null;
 captureInput?:()=>WorkspaceTabInput|null;selectInput?:(input:WorkspaceTabInput,project:string)=>Promise<void>;
 inputBlockReason?:(input:WorkspaceTabInput)=>string|null;
 storageKey:string;request:(type:'workspace-layout'|'session-note',fields:any)=>Promise<Record<string,any>>;
 restoreItems:(items:StudyRetainedView[],options?:StudyOpenOptions)=>Promise<StudyRecovery>;placeItem:(item:StudyRetainedView)=>unknown;
 frontItem:(item:StudyRetainedView)=>boolean;
 captureItems:()=>StudyRetainedView[];openData:()=>void;notice:(error:unknown)=>void;layoutChanged:()=>void;
 /** Something else holding the Stage (a researcher's study) sees each Tab command first: true when it took it. */
 intercept?:(name:string)=>boolean;
 /** A reader Tab came into view (its view), or another Tab did (null): the reader draws its item full width. */
 reader?:(view:WorkspaceView|null)=>void;
 /** A reader Tab's mark in the row: its item's kind. */
 readerMark?:(view:WorkspaceView)=>TabMark;
 /** A Tab 0.3.0 made for a researcher's results came into view (its view), or another Tab did (null): the Stage shows a
  * pointer to the researcher instead of the old report window (legacy-output-tab.ts, ticket 66 D5). */
 legacy?:(view:WorkspaceView|null)=>void;
 /** Open a researcher's own Tab (an old "Go to results" notice of one of those Tabs). */
 openResearcher?:(rid:string)=>void;
}
/** A row of All Tabs (⌄) that another owner provides: a researcher's or Study's Tab, or a study to open as one. */
export interface MenuTab {id:string;name:string;title?:string;mark?:Element|null;act:()=>void}
/** A researcher's or Study's Tab as the Assistant's Tab report lists it beside the work Tabs. */
export interface ResearchTabReport {id:string;title:string;open:boolean;selected:boolean;kind:'researcher'|'study'|'draft'}
const el=<K extends keyof HTMLElementTagNameMap>(tag:K,cls='',text='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=text;return n;};
/** The drag type a Data card carries: its Project and resource, re-checked on drop. */
export const ENTRY_DRAG='application/x-hooke-entry';
interface TabOwner {session:string;generation:number}
interface DraggedEntry {day?:string;id:string;version?:number|null}
/** A drop resolves only the captured card revision in its original Project opening. */
export function draggedEntry<T extends DraggedEntry>(data:string,owner:TabOwner,entries:T[]):T|null {
 if(data.length>16384)return null;
 try {
  const value=JSON.parse(data);
  if(!value||value.owner?.session!==owner.session||value.owner?.generation!==owner.generation||value.entry?.day!==owner.session
    ||typeof value.entry?.id!=='string'||!(value.entry.version===null||Number.isSafeInteger(value.entry.version)&&value.entry.version>=0))return null;
  const matches=entries.filter(entry=>entry.day===value.entry.day&&entry.id===value.entry.id&&(entry.version??null)===value.entry.version);
  return matches.length===1?matches[0]:null;
 }catch{return null;}
}
/** The Tab command a key press asks for, if any: the platform's command key with T, W, Shift+T, 1–9, Shift+[ or Shift+]. */
export function tabShortcut(e:Pick<KeyboardEvent,'key'|'code'|'metaKey'|'ctrlKey'|'altKey'|'shiftKey'|'repeat'>&{defaultPrevented?:boolean},mac:boolean):Parameters<WorkspaceViews['command']>[0]|null{
 if(e.defaultPrevented)return null;
 const mod=mac?e.metaKey&&!e.ctrlKey:e.ctrlKey&&!e.metaKey;if(!mod||e.altKey)return null;
 const key=e.key.toLowerCase();
 // Held down, only moving between Tabs repeats; New, Close and Reopen happen once per press.
 if(e.repeat&&!(e.shiftKey&&['bracketleft','bracketright'].includes(e.code.toLowerCase())))return null;
 if(!e.shiftKey&&key==='t')return 'new';
 if(!e.shiftKey&&key==='w')return 'close';
 if(e.shiftKey&&key==='t')return 'reopen';
 if(e.shiftKey&&(e.code==='BracketRight'||key==='}'))return 'next';
 if(e.shiftKey&&(e.code==='BracketLeft'||key==='{'))return 'previous';
 if(!e.shiftKey&&/^[1-9]$/.test(key))return `go:${Number(key)}`;
 return null;
}
/** A short Tab name from a window's title: its first part before " · ", at most 40 characters. */
export const tabName=(title:string)=>{const first=title.split(' · ')[0].trim();return first.length>40?`${first.slice(0,39).trimEnd()}…`:first;};
/** Small line icons for the Tab row (stroke in the text colour), so its own controls read as tools, not as Tabs. */
const icon=(paths:string)=>{const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.classList.add('workspace-icon');svg.innerHTML=paths;return svg;};
const ICON_NOTE='<path d="M5 4h10l4 4v12H5z"/><path d="M14 4v5h5M8 13h8M8 17h5"/>';
/** A recording's short label for a tight row: its first part ("Oct 22" of "Oct 22, 3:40 pm"), never cut mid-word. */
export const shortLabel=(title:string)=>{const first=title.split(/,\s|\s·\s|\s—\s/)[0].trim();return first&&first.length<title.length?first:title;};
/** The window's width for placing a menu (0 where there is no window, as in tests). */
const viewportWidth=()=>typeof innerWidth==='number'?innerWidth:0;
/** A layout status that needs the person (Not saved, Reload required, …) shows on the row; Saved and Saving… take no room. */
export const layoutStatusNeedsYou=(text:string)=>/^(Not saved|Reload required|Opening failed|Saved · Camera|Tab removed)/.test(text);
const button=(label:string,fn:()=>void)=>{const b=el('button','',label);b.type='button';b.onclick=fn;return b;};
const uuid=()=>crypto.randomUUID();
/** Journal-backed arrangements; browser-local navigation and writer-specific recovery. */
/** What the strip's Saved means (its CSS prefixes "Layout:"), so it is never read as a scientific window's Save. */
export const LAYOUT_STATUS_MEANING='The Tabs and where their windows are. Changes inside a scientific window are kept with its own Save.';
export class WorkspaceViews {
 private session='';private layout=mainLayout();
 // The newest layout format the server reads (6 until a server refuses it: then 5, and reader Tabs stay in this studio
 // for the session, `sessionReaders`, since that server cannot save them).
 private serverSchema:LayoutSchema=LAYOUT_SCHEMA;private sessionReaders=new Map<string,WorkspaceView[]>();
 private schemaNow():LayoutSchema{return this.serverSchema??LAYOUT_SCHEMA;}
 private wire(layout:WorkspaceLayout){return layoutForServer(layout,this.schemaNow());}
private revision=0;private generation=0;private revealSequence=0;
 private queuedReveal?:{id:string;tab?:string;version?:number|null;session:string;generation:number;sequence:number};
 private completionRestores=new Set<string>();
 private openingInput=false;
 private loaded=new Set<string>();private changing=false;private navigating=false;private dirty=false;
 private saving:Promise<void>|null=null;private timer=0;private lastSaved='';private conflict=false;
 private notes=new Map<string,NoteState>();private noteTimers=new Map<string,number>();
 private recovery:RecoveryStore;private layoutDraft:Recovery<any>|undefined;private draftGeneration=0;private layoutIdentity=uuid();
 private openIds:string[]=[];private removed:WorkspaceView[]=[];private completions:any[]=[];private pending:any[]=[];
 // The row's own tools live in the "+" menu (Will, 2026-10-23: the row holds Tabs); the note button keeps its enabled state for the menu.
 private noteButton!:HTMLButtonElement;
 private strip=el('nav','workspace-view-strip');private tabs=el('div','workspace-view-tabs');private status=el('span','workspace-layout-status');
 private recover=button('Review recovery',()=>this.reviewRecovery());private retry=button('Retry save',()=>void this.flush());
 // The row holds only Tabs, then + and ⌄ (All Tabs) and, only while the layout needs the person, its status and Retry.
 private fade=el('span','workspace-tabs-fade');private tools=el('div','workspace-tab-tools');private allTabs!:HTMLButtonElement;private allCount=el('span','workspace-all-count');
 private fitFrame=0;private scrollTimer=0;private loadedRevision=0;private loadedViews='';
 // The Stage bar under the row, on every Tab (workspace-feel plan): the specimen on work Tabs (a researcher's page puts
 // its own bar in `stageResearch`), and the Project's camera, Record and Assistant on the right.
 private stageBar=el('div','workspace-stage-bar');private stageWork=el('div','workspace-stage-work');private stageResearch=el('div','workspace-stage-research');private stageRight=el('div','workspace-stage-right');
 private stageReader=el('div','workspace-stage-reader');
 private stageThumb=el('span','workspace-stage-thumb');private stageName=el('span','workspace-stage-name');private stageCredit=el('span','workspace-stage-credit');private labelKey='';
 private stageSize?:HTMLButtonElement;private stageHide?:HTMLButtonElement;
 // What just happened (a closed Tab with Reopen, a flash, a completed result) floats in a layer under the bars.
 private toasts=el('div','workspace-toasts');private closedToast=el('div','workspace-toast workspace-closed-toast');
 // A closed Tab says so for a moment, with Reopen (the same reopen Tabs… offers); its arrangement is kept either way.
 // What just happened to a sent window ("Moved to B · Go to · Undo"), said only after its save was accepted.
 private flashNote=el('span','workspace-flash');private flashTimer=0;
 private completionNote=el('div','workspace-completion-note');
 // The specimen is the sun of every Tab: the bar's own controls, and the way back when a Tab has hidden it.
 // Dragging onto the Tab row: where it would land, said beside the pointer; and who takes a dropped Data card.
 private dragHint=el('div','workspace-drag-hint');private dropShown:HTMLElement|null=null;private dragAbort?:AbortController;
 private entryDrop?:(data:string,to:{tab:string}|{fresh:true})=>void;
 private specimenSize!:HTMLButtonElement;private specimenHide!:HTMLButtonElement;private specimenReturn=el('div','workspace-specimen-return');
 // Tabs closed in this Project, most recent last, each with where it stood: ⌘⇧T reopens them in turn, in place.
 private closedStack:{id:string;at:number}[]=[];
 private closedNote=el('span','workspace-closed-note');private reopen=button('Reopen',()=>this.reopenClosed());private closedId='';private closedTimer=0;
 private specimen=el('section','workspace-specimen workspace-specimen-full');private specimenBar=el('header','workspace-specimen-bar');
 private specimenShell:ReturnType<ReturnType<typeof studyArtifacts>['specimen']>;private stage=document.getElementById('stage')!;
 private empty=el('section','workspace-view-empty');private undo:{before:WorkspaceView;arranged:WorkspaceView}|null=null;
 private recoveryBusy=false;private refreshBusy=false;private noteProjects=new Map<string,Map<string,NoteState>>();
 private opts:Options;
 constructor(opts:Options){
  this.opts={...opts,request:async(type,fields)=>{try{return await opts.request(type,fields);}catch(error){if(error&&typeof error==='object'&&'reply' in error)return (error as {reply:Record<string,any>}).reply;throw error;}}};
  this.recovery=new RecoveryStore(localStorage,opts.storageKey);
  this.strip.id='workspace-tabs-strip';this.strip.setAttribute('aria-label','Project tabs');this.tabs.setAttribute('role','tablist');this.tabs.setAttribute('aria-label','Workspace tabs');
  this.status.setAttribute('role','status');this.status.title=LAYOUT_STATUS_MEANING;
  const add=button('',()=>this.addMenu(add));add.className='workspace-new-tab';add.setAttribute('aria-label','New Tab, note, researcher or study');add.setAttribute('aria-haspopup','menu');add.title='New Tab (⌘T), note, researcher or study';add.append(tabMark('plus'));
  const all=button('',()=>this.allMenu(all));all.className='workspace-all-tabs';all.setAttribute('aria-label','All Tabs');all.setAttribute('aria-haspopup','menu');all.title='All Tabs: open and closed, researchers, studies, and the layout';all.append(tabMark('down'),this.allCount);this.allTabs=all;
  // New note is in "+" (its enabled state follows this button, kept off the row).
  const note=button('+ Note',()=>void this.newNote());note.className='workspace-new-note';note.setAttribute('aria-label','New note in current tab');note.title='New note in this Tab; notes save to Project Data';note.replaceChildren(icon(ICON_NOTE));this.noteButton=note;
  this.fade.setAttribute('aria-hidden','true');this.retry.className='workspace-layout-retry';this.recover.className='workspace-layout-recover';
  this.tools.append(this.status,this.retry,this.recover,add,all);this.strip.append(this.tabs,this.fade,this.tools);
  this.flashNote.setAttribute('role','status');this.flashNote.hidden=true;this.retry.hidden=true;this.recover.hidden=true;this.closedNote.setAttribute('role','status');this.reopen.className='workspace-reopen';
  this.closedToast.append(this.closedNote,this.reopen);this.closedToast.hidden=true;this.completionNote.setAttribute('role','status');this.completionNote.hidden=true;
  this.toasts.append(this.closedToast,this.flashNote,this.completionNote);
  this.stageBar.setAttribute('aria-label','Stage');this.stageReader.hidden=true;this.stageBar.append(this.stageWork,this.stageReader,this.stageResearch,this.stageRight,this.toasts);
  this.stage.before(this.strip,this.stageBar);
  // Said by an attribute, not :has() (workspace-feel.css): a :has() on the Stage block re-styled all of it on every change inside (b041).
  this.strip.parentElement?.setAttribute('data-view-strip','');
  const float=()=>{if(!this.active())return;this.active()!.specimen.rect=this.active()!.specimen.rect?null:[12,40,480,380];this.specimen.dataset.front='true';this.applySpecimen();this.changed();};
  const size=button('Float',float);
  // Main is home: its specimen can float but never leaves it. Other Tabs may hide theirs, and keep a way back (below).
  const hideSpecimen=()=>{const v=this.active();if(v&&!this.isHome(v.id)){v.specimen.visible=false;this.applySpecimen();this.changed();}};
  const hide=button('×',hideSpecimen);hide.setAttribute('aria-label','Hide specimen from this Tab');hide.title='Hide the specimen from this Tab (Main always keeps it)';
  this.specimenSize=size;this.specimenHide=hide;
  // The Stage bar names the specimen with the same Float or Fill and Hide (the floating window keeps its own bar).
  this.stageSize=button('Float',float);this.stageSize.className='workspace-stage-button';this.stageHide=button('Hide',hideSpecimen);this.stageHide.className='workspace-stage-button';
  this.stageHide.setAttribute('aria-label','Hide specimen from this Tab');this.stageHide.title='Hide the specimen from this Tab (Main always keeps it)';
  this.stageThumb.setAttribute('aria-hidden','true');this.stageThumb.hidden=true;this.stageCredit.hidden=true;this.stageName.textContent='Specimen';
  this.stageWork.append(this.stageThumb,this.stageName,this.stageCredit,this.stageSize,this.stageHide,this.specimenReturn);this.stageWork.dataset.heard='false';
  const synced=el('span','workspace-specimen-synced','Tab input');synced.title='The specimen: this Tab remembers which input it shows. Previewing Data leaves it unchanged.';this.specimenBar.append(document.querySelector('.study-source-bar')??el('span','','Specimen'),synced,size,hide);this.specimenBar.tabIndex=0;
  const content=el('div','workspace-specimen-content');content.append(document.getElementById('view')!);this.specimen.append(this.specimenBar,content);this.stage.prepend(this.specimen);
  // Escape on the specimen does nothing: hiding it is only ever the bar's ×, never a stray key.
  this.specimenShell=studyArtifacts().specimen(this.specimen,this.specimenBar,()=>{});
  // Floated, the specimen is a window like the others: a click brings it in front of them, a click on one of them sends
  // it back. As the backdrop it always stays behind.
  this.specimen.addEventListener('pointerdown',()=>{this.specimen.dataset.front='true';},true);
  studyArtifacts().root.addEventListener('pointerdown',()=>{delete this.specimen.dataset.front;},true);
  // A tab whose specimen was hidden (×, or Escape on it) offers it back here, not only inside Tabs….
  this.empty.append(el('h2','','Your workspace'),el('p','','Tabs arrange this Project’s work. Data and notes stay when a Tab closes.'),button('Show specimen',()=>this.showSpecimen()),button('Open Data',opts.openData),button('New Tab',()=>this.addView()),button('Reopen a Tab',()=>this.manage()));this.stage.append(this.empty);
  this.specimenReturn.append(el('span','','Specimen hidden in this Tab'),button('Show as backdrop',()=>this.showSpecimen()));this.specimenReturn.hidden=true;
  window.addEventListener('workspace-new-tab',()=>void this.addView());
  window.addEventListener('workspace-tabs-request',event=>{const detail=(event as CustomEvent).detail;detail.result=this.agentCommand(detail.session,detail.action,detail.check);});
  // ⌘T, ⌘W, ⌘⇧T, ⌘1–9, ⌘⇧[ and ⌘⇧] (Ctrl off a Mac). In the Mac app its Tab menu claims these keys first and calls
  // hookeTabCommand, except ⌘T: the focused scientific view gets its existing transpose shortcut first.
  // Unclaimed keys reach this fallback; a browser keeps most for its own tabs.
  (window as Window&{hookeTabCommand?:(name:string)=>void}).hookeTabCommand=name=>this.command(name as Parameters<WorkspaceViews['command']>[0]);
  window.addEventListener('keydown',e=>{const name=tabShortcut(e,/Mac|iPhone|iPad/.test(navigator.platform));if(!name)return;e.preventDefault();this.command(name);});
  // A window's header dragged onto a Tab moves it there (Alt: also shows it), onto + or the row's free space makes a
  // new Tab with it. A Data card dragged there opens its result there. Nothing drops onto the Stage or the live view.
  // While the Tabs move the fade shows; at rest the row ends on a whole Tab again and ⌄ recounts.
  this.tabs.addEventListener('scroll',()=>{this.fade.dataset.on='true';clearTimeout(this.scrollTimer);this.scrollTimer=window.setTimeout(()=>{this.tabs.style.maxWidth='';measureTabRow(this.rowParts());},140);},{passive:true});
  window.addEventListener('resize',()=>this.refit());
  // The tools change size when the layout chip, Retry or Review recovery come and go: the Tabs then have less room.
  if(typeof ResizeObserver==='function'){const watch=new ResizeObserver(()=>this.refit());watch.observe(this.strip);watch.observe(this.tools);}
  this.dragHint.hidden=true;this.dragHint.setAttribute('aria-hidden','true');document.body.append(this.dragHint);
  document.addEventListener('pointerdown',e=>this.headerDrag(e),true);
  this.strip.addEventListener('dragover',e=>this.entryDragOver(e));
  this.strip.addEventListener('dragleave',e=>{if(!this.strip.contains(e.relatedTarget as Node|null))this.clearDrop();});
  this.strip.addEventListener('drop',e=>this.dropEntry(e));
  document.addEventListener('dragend',()=>this.clearDrop());
  window.addEventListener('workspace-item-mounted',e=>this.adopt((e as CustomEvent<HTMLElement>).detail));
  window.addEventListener('workspace-layout-changed',e=>{if(!this.changing){const root=(e as CustomEvent<HTMLElement>).detail;if(root?.dataset?.workspaceUnsaved==='true')return;/* a window never saved: moving it saves nothing */if(root)delete root.dataset.savedRect;this.capture();this.changed();}});
  new MutationObserver(records=>{if(!this.changing&&records.some(r=>r.removedNodes.length)){this.capture();this.changed();}}).observe(studyArtifacts().root,{childList:true});
  window.addEventListener('beforeunload',()=>{if(this.dirty)this.cache();});
  window.addEventListener('workspace-study-action',e=>{if((e as CustomEvent).detail.phase!=='start')void this.refresh();});
  window.addEventListener('workspace-origin-request',e=>{const detail=(e as CustomEvent).detail;detail.origin=this.origin(detail.run);});
  setInterval(()=>void this.refresh(),5000);this.render();
 }
 tabTitle(project:string,tab:string):string|null {return project===this.session?this.layout.views.find(v=>v.id===tab)?.title??null:null;}
 rememberedView(project:string,placement:string,resource:StudyRetainedView['resource']):ScientificViewPreferences|null {
  if(project!==this.session||!this.lastSaved)return null;
  const accepted=JSON.parse(this.lastSaved) as WorkspaceLayout;
  const item=accepted.views.flatMap(view=>view.items).find(item=>item.placementId===placement&&sameStudyResource(item.resource,resource));
  return item?.display?.scientific?scientificViewPreferences(item.display.scientific):null;
 }
 /** Accepted table Save advances this placement only; copies and other revisions remain independent. */
 async rememberResource(project:string,placement:string,previous:StudyRetainedView['resource'],next:StudyRetainedView['resource'],title:string):Promise<void>{
  if(project!==this.session||this.changing||this.navigating||this.conflict||next.day!==project||next.id!==previous.id||
   !Number.isSafeInteger(next.version)||Number(next.version)<Number(previous.version))throw new Error('The original table window changed. Reopen the saved result from Data.');
  const generation=this.generation;this.capture();
  const item=this.layout.views.flatMap(v=>v.items).find(i=>i.placementId===placement&&sameStudyResource(i.resource,previous));
  const root=this.roots().find(r=>r.dataset.placementId===placement&&r.dataset.project===project);
  if(!item||!root)throw new Error('The original table placement is unavailable; its saved result remains in Data.');
  const retained=JSON.parse(root.dataset.studyRetained!) as StudyRetainedView;
  if(!sameStudyResource(retained.resource,previous))throw new Error('The table window now shows another revision.');
  item.resource={...next};item.title=title;root.dataset.studyRetained=JSON.stringify({...retained,resource:{...next},title});
  this.changed();await this.flush();
  if(generation!==this.generation||project!==this.session||this.dirty||this.conflict)throw new Error('Table saved in Data. Its place in this Tab isn’t saved yet: use Retry save beside Layout.');
 }
 async rememberView(project:string,placement:string,resource:StudyRetainedView['resource'],settings:ScientificViewPreferences|null):Promise<void>{
  if(project!==this.session||this.changing||this.navigating||this.conflict)throw new Error('Go back to the Project this came from, and use Retry save beside Layout, before adding this view.');
  const accepted=settings===null?null:scientificViewPreferences(settings),generation=this.generation;
  this.capture();
  const item=this.layout.views.flatMap(view=>view.items).find(item=>item.placementId===placement&&sameStudyResource(item.resource,resource));
  const root=this.roots().find(root=>root.dataset.placementId===placement&&root.dataset.project===project);
  if(!item||!root)throw new Error('The saved window changed. Reopen its exact scientific revision.');
  this.layout.schema=Math.max(3,this.layout.schema) as LayoutSchema;
  item.display={...item.display};
  if(accepted)item.display.scientific=accepted;else delete item.display.scientific;
  const retained=JSON.parse(root.dataset.studyRetained!) as StudyRetainedView;
  root.dataset.studyRetained=JSON.stringify({...retained,display:structuredClone(item.display)});
  this.changed();await this.flush();
  if(generation!==this.generation||project!==this.session||this.conflict||
     JSON.stringify(this.rememberedView(project,placement,resource))!==JSON.stringify(accepted))
   throw new Error('This view isn’t saved in the layout yet. Its place is kept on this Mac; use Retry save beside Layout.');
 }
 private active(){return this.layout.views.find(v=>v.id===this.layout.active);}
 private roots(){return [...studyArtifacts().root.children] as HTMLElement[];}
 private visibleRoots(){return this.roots().filter(n=>n.dataset.project===this.session&&n.dataset.workspaceView===this.layout.active&&this.openIds.includes(this.layout.active)&&n.dataset.dismissed!=='true'&&!n.hidden);}
 private navKey(){return `${this.opts.storageKey}:navigation:v2:${this.session}`;}
 private navSave(){try{sessionStorage.setItem(this.navKey(),JSON.stringify({active:this.layout.active,open:this.openIds}));}catch{this.opts.notice('Browser navigation cannot be remembered. Saved Data is unaffected.');}}
 private adopt(root:HTMLElement){
  if(root.dataset.project&&root.dataset.project!==this.session){root.dataset.workspaceHidden='true';return;}
  if(!this.active())return;
  root.dataset.workspaceView??=this.layout.active;root.dataset.placementId??=uuid();root.dataset.project=this.session;
  const bar=root.querySelector<HTMLElement>(':scope > header');if(bar&&!bar.querySelector('.workspace-send')){
   // One way to put a window in another Tab: "Add to Tab…" (Add, or Move this view instead; every Tab or a new one). Native image
   // windows keep it in their tools menu so narrow titles and Close stay reachable; "Also in" says which other Tabs show it.
   const send=button('Add to Tab…',()=>this.sendDialog(root));send.className='workspace-send';send.title='Show this in another Tab too, or move it there';
   const also=el('span','workspace-also-in');also.hidden=true;
   // A document window's own tools (its header ⋯ lists them, workspace-study-artifacts.ts) take them before Expand.
   const own=bar.querySelector<HTMLElement>(':scope > .study-window-tools');
   const expand=bar.querySelector<HTMLElement>(':scope > .study-expand'),tools=bar.querySelector<HTMLElement>('.workspace-window-tools');
   if(own)own.prepend(send,also);else if(tools)tools.append(send,also);else if(expand)expand.before(send,also);else bar.append(send,also);
  }
  // A window the arrangement never saves (a comparison drawn on the Stage) changes nothing to save: no save is started,
  // so none races the server's own placements of the Results it shows.
  const completing=this.completionRestores.delete(root.dataset.placementId??'');
  this.updateTransfer(root);this.visibility();if(!this.changing&&root.dataset.workspaceUnsaved!=='true'&&!completing){this.capture();this.changed();}
 }
 /** "Also in A, C": the other Tabs showing the same saved result at the same revision (or the same note), each a way there. */
 private updateTransfer(root:HTMLElement){
  const chip=root.querySelector<HTMLElement>('.workspace-also-in');if(!chip)return;
  let resource:{id:string;version?:number|null}|undefined;try{resource=root.dataset.studyRetained?JSON.parse(root.dataset.studyRetained).resource:undefined;}catch{resource=undefined;}
  const noteId=root.dataset.noteId,here=root.dataset.workspaceView;
  const others=this.layout.views.filter(v=>v.id!==here&&(resource?v.items.some(i=>i.resource.id===resource!.id&&(i.resource.version??0)===(resource!.version??0)):!!noteId&&v.notes.some(n=>n.id===noteId)));
  const signature=JSON.stringify(others.map(v=>[v.id,v.title]));if(chip.dataset.signature===signature)return;chip.dataset.signature=signature;
  chip.replaceChildren();chip.hidden=!others.length;if(!others.length)return;
  chip.append('Also in');others.forEach(v=>{const go=button(v.title,()=>void this.goTo(v.id));go.className='workspace-chip';go.title=`Go to ${v.title}`;chip.append(go);});
 }
 private visibility(){
  for(const root of this.roots())root.dataset.workspaceHidden=String(root.dataset.project!==this.session||root.dataset.workspaceView!==this.layout.active||!this.openIds.includes(this.layout.active));
  this.empty.hidden=!!this.active()&&(this.active()!.kind==='reader'||!!legacyResearcher(this.active())||this.active()!.specimen.visible||this.visibleRoots().length>0);
  document.body.dataset.activeWorkspaceView=this.layout.active;document.body.dataset.workspaceProject=this.session;window.dispatchEvent(new CustomEvent('workspace-tab-context'));this.opts.layoutChanged();
 }
 private rect(root:HTMLElement):Rect{if(root.dataset.savedRect)return JSON.parse(root.dataset.savedRect);const r=root.getBoundingClientRect(),s=this.stage.getBoundingClientRect();return [r.left-s.left,r.top-s.top,r.width,r.height];}
 private capture(){
  const v=this.active();if(!v||!this.session||this.changing)return;
  if(v.kind==='reader')return; // a reader Tab's one item is its own; it has no windows to capture
  if(legacyResearcher(v))return; // a 0.3.0 results Tab shows its pointer: its saved windows are kept as they were
  if(this.rememberInput())this.changed();
  if(!this.specimen.hidden&&!this.specimen.classList.contains('workspace-specimen-full'))v.specimen.rect=this.rect(this.specimen);
  const native=this.opts.captureItems(),pending=v.items.filter(item=>this.completionRestores.has(item.placementId??''));v.items=[];v.notes=[];
  // The existing item sequence is the saved bottom-to-top arrangement. A selected
  // output must reopen above its source even when it was created earlier.
  const roots=this.visibleRoots().sort((a,b)=>(Number(a.style.zIndex)||0)-(Number(b.style.zIndex)||0));
  for(const root of roots){
   // A retry can publish its native window before the promise removes its error
   // presentation. Only that exact replacement may supersede the placeholder.
   if(root.dataset.recoveryPlaceholder==='true'&&root.dataset.placementId&&root.dataset.studyRetained&&roots.some(other=>
    other!==root&&other.dataset.recoveryPlaceholder!=='true'&&other.dataset.studyRetained&&
    other.dataset.project===root.dataset.project&&other.dataset.workspaceView===root.dataset.workspaceView&&
    other.dataset.placementId===root.dataset.placementId&&sameStudyResource(
     JSON.parse(other.dataset.studyRetained).resource,JSON.parse(root.dataset.studyRetained!).resource)))continue;
   const placementId=root.dataset.placementId??=uuid();
   if(root.dataset.noteId){v.notes.push({id:root.dataset.noteId,placementId,rect:this.rect(root)});continue;}
   if(root.dataset.studyRetained){const {coordinates:_,...item}=JSON.parse(root.dataset.studyRetained) as StudyRetainedView;const r=this.rect(root);const actual=native.find(n=>n.placementId===placementId);v.items.push({...item,...(actual?{zoom:actual.zoom,pan:actual.pan}:{}),placementId,position:r.slice(0,2),size:r.slice(2)});}
  }
  // A user edit during a Result restore cannot erase its not-yet-mounted placement.
  for(const item of pending)if(!v.items.some(n=>n.placementId===item.placementId))v.items.push(item);
  pruneLanes(v);
 }
 private cache(){try{this.layoutDraft=this.recovery.put(this.session,'layout',++this.draftGeneration,{revision:this.revision,layout:this.wire(this.layout)},this.layoutIdentity);}catch{this.say('Browser recovery unavailable');}}
 private changed(){if(!this.session||this.changing)return;bumpForSchema5(this.layout);this.dirty=true;this.visibility();this.cache();clearTimeout(this.timer);this.timer=window.setTimeout(()=>void this.flush(),400);}
 async prepareScientificView():Promise<void>{
  const project=this.session,generation=this.generation;
  await this.flush();
  if(!project||project!==this.session||generation!==this.generation||this.changing||this.navigating||this.dirty||this.conflict)
   throw new Error('The layout isn’t saved. Use Retry on the layout line. If this Tab is full, move the window to another Tab. Your Data is kept.');
 }
 /** Admission only: never silently resolve a conflict or publish scientific drafts to permit restart. */
 updateRestartBlockers():string[]{
  const blockers:string[]=[];const documentDraft=studyArtifacts().blockReason();if(documentDraft)blockers.push(documentDraft);
  if(this.changing||this.navigating||this.dirty||this.saving||this.conflict)
   blockers.push('The Project’s layout isn’t saved yet. Wait for Saved, or use Retry save beside Layout.');
  const all=new Set([...this.notes.values(),...Array.from(this.noteProjects.values()).flatMap(notes=>[...notes.values()])]);
  if([...all].some(note=>note.status!=='saved'))
   blockers.push('A Project note is unsaved or has a conflict. Save it, or resolve the conflict.');
  return blockers;
 }
 async flush():Promise<void>{
  clearTimeout(this.timer);if(this.changing||!this.session)return;
  for(const [id,timer] of this.noteTimers){clearTimeout(timer);this.noteTimers.delete(id);const note=this.notes.get(id);if(note)await this.saveNote(note);}
  if(this.saving){await this.saving;return this.flush();}if(!this.dirty||this.conflict)return;
  const serialized=JSON.stringify(this.wire(this.layout));if(serialized===this.lastSaved){this.dirty=false;return;}
  const session=this.session,g=this.generation,draft=this.layoutDraft;this.say('Saving…');this.retry.hidden=true;this.retry.textContent='Retry save';this.retry.onclick=()=>void this.flush();
  this.saving=(async()=>{try{
   const reply=await this.opts.request('workspace-layout',{session,schema:this.schemaNow(),base:this.revision,layout:JSON.parse(serialized)});if(g!==this.generation)return;
   if(!reply.ok){this.conflict=reply.code==='conflict'||reply.code==='unsupported-schema';this.say(reply.code==='unsupported-schema'?'Reload required':'Not saved');this.retry.hidden=this.conflict;this.recover.hidden=false;this.opts.notice(reply.error);return;}
   if(reply.completionAdditions?.length){
    // Apply only the server-proven additions, never replace edits made during this request.
    const next=structuredClone(this.layout),added:StudyRetainedView[]=[];
    for(const {tab,item,view:descriptor} of reply.completionAdditions){
     let view=next.views.find(v=>v.id===tab);
     if(!view&&descriptor?.id===tab&&descriptor.kind==='output'&&next.views.length<24){
      view=structuredClone(descriptor) as WorkspaceView;next.views.push(view);bumpForSchema5(next);if(!this.openIds.includes(tab)&&!this.besideItsResearcher(view))this.openIds.push(tab);
     }else if(view&&descriptor&&(view.kind!=='output'||JSON.stringify(view.provenance)!==JSON.stringify(descriptor.provenance))){
      this.conflict=true;this.say('Not saved · Review completed results');this.recover.hidden=false;return;
     }
     const existing=next.views.flatMap(v=>[...v.items,...v.notes].filter(n=>n.placementId===item.placementId).map(n=>({tab:v.id,item:n})));
     if(!view||(existing.length&&!(existing.length===1&&existing[0].tab===tab&&JSON.stringify(existing[0].item)===JSON.stringify(item)))){
      this.conflict=true;this.say('Not saved · Review completed results');this.recover.hidden=false;
      this.opts.notice('A result completed while its destination changed. Review recovery before saving.');return;
     }
     if(!existing.length){view.items.push(item);if(tab===next.active)added.push({...item,coordinates:'stage'});else this.loaded.delete(tab);}
    }
    this.layout=next;this.revision=reply.revision;this.lastSaved=JSON.stringify(reply.layout);
    // Restore exact added placements before another capture can omit unseen Results.
    const pending=this.completionRestores;for(const item of added)pending.add(item.placementId!);
    this.cache();
    try{for(const item of added){
     const restored=await this.opts.restoreItems([item]);if(g!==this.generation)return;
     if(!restored.recovered)throw new Error(restored.missing.join('; ')||'Reopen the completed result from Data.');
    }}catch(error){if(g===this.generation){this.conflict=true;this.recover.hidden=false;}throw error;}
    finally{for(const item of added)pending.delete(item.placementId!);}
    if(g!==this.generation)return;
    this.completions=reply.completions??this.completions;this.pending=reply.pending??this.pending;this.render();
   }else{this.revision=reply.revision;this.lastSaved=serialized;}
   this.dirty=JSON.stringify(this.wire(this.layout))!==this.lastSaved;
   if(draft)this.recovery.clear(draft);if(!this.dirty)this.layoutDraft=undefined;
   this.say(this.dirty?'Unsaved changes':'Saved');this.retainCameraStatus();this.updateRecovery();if(!this.dirty)window.dispatchEvent(new CustomEvent('workspace-tab-context'));
  }catch(error){if(g===this.generation){this.say('Not saved');this.retry.hidden=false;this.opts.notice(error);}}})();
  const saving=this.saving;await saving;if(this.saving===saving)this.saving=null;
  if(g!==this.generation)return;if(this.dirty&&!this.conflict&&JSON.stringify(this.wire(this.layout))!==serialized)await this.flush();
 }
 pause(){clearTimeout(this.timer);this.changing=true;this.opening();}
 resume(){this.changing=false;delete document.body.dataset.workspaceOpening;this.render();this.visibility();}
 /** Between Projects, show no tabs: never the previous Project's under the next Project's name. */
 private opening(){this.tabs.replaceChildren(el('span','workspace-tabs-opening','Opening…'));this.say('Opening…');document.body.dataset.workspaceOpening='true';window.dispatchEvent(new CustomEvent('workspace-tab-context'));}
 async load(session:string):Promise<void>{
  const blocker=studyArtifacts().blockReason(this.session);if(blocker){this.opts.notice(new Error(blocker));return;}
  this.clearFlash();
  this.clearReopen(); // a closed Tab's Reopen belongs to the Project it was closed in
  this.closedStack=[];this.clearFlash(); // and so does what the toast was saying
  const g=++this.generation;this.openingInput=false;delete document.body.dataset.workspaceInputOpening;delete document.body.dataset.workspaceInputUnavailable;this.completionRestores=new Set();this.undo=null;this.layoutIdentity=uuid();this.layoutDraft=undefined;this.navigating=false;clearTimeout(this.timer);this.changing=true;if(this.session)this.noteProjects.set(this.session,this.notes);this.session=session;this.strip.dataset.opening=JSON.stringify(this.tabOwner());this.notes=this.noteProjects.get(session)??new Map();this.loaded.clear();this.dirty=false;this.conflict=false;
  for(const root of this.roots())if(root.dataset.project!==session)root.dataset.workspaceHidden='true';
  this.opening();
  try{
   const [reply,notes]=await Promise.all([this.readLayout(session),this.opts.request('session-note',{session})]);if(g!==this.generation)return;
   if(!reply.ok||!notes.ok)throw new Error(reply.error||notes.error);this.accept(reply);this.loadedRevision=this.revision;try{this.loadedViews=JSON.stringify(JSON.parse(this.lastSaved).views??null);}catch{this.loadedViews='';}
   for(const note of notes.notes){if(this.notes.has(note.id))this.notes.get(note.id)!.receive(note);else this.notes.set(note.id,new NoteState(note,this.recovery,session));}
   let nav:any;try{nav=JSON.parse(sessionStorage.getItem(this.navKey())??'null');}catch{}
   this.openIds=nav?.open?.filter((id:string)=>this.layout.views.some(v=>v.id===id))??this.layout.views.map(v=>v.id);
   {const home=this.layout.views.find(v=>this.isHome(v.id));if(home)this.openIds=[home.id,...this.openIds.filter(id=>id!==home.id)];}
   this.layout.active=this.openIds.includes(nav?.active)?nav.active:this.openIds[0]??'';
   studyArtifacts().clearDocuments();this.changing=false;if(await this.activate(this.layout.active,true,true)===false)throw new Error('The saved Tab input could not be opened. Use Retry opening; your Data is kept.');
   this.say(this.dirty?'Saving…':'Saved');this.retry.hidden=true;this.retry.textContent='Retry save';this.retry.onclick=()=>void this.flush();this.retainCameraStatus();this.updateRecovery();
  }catch(error){if(g===this.generation)this.tabs.replaceChildren(el('span','workspace-tabs-opening','Not opened'));this.say('Opening failed');this.retry.hidden=false;this.retry.textContent='Retry opening';this.retry.onclick=()=>void this.load(session);this.opts.notice(error);}finally{if(g===this.generation){this.changing=false;delete document.body.dataset.workspaceOpening;window.dispatchEvent(new CustomEvent('workspace-tab-context'));}}
 }
 /** Camera availability survives a successful layout save; save errors keep priority. */
 private retainCameraStatus():void{
  if(this.dirty||this.conflict||document.body.dataset.workspaceInputUnavailable!=='true'||this.active()?.input?.source!=='live')return;
  const session=this.session,generation=this.generation;
  this.say('Saved · Camera unavailable');this.retry.hidden=false;this.retry.textContent='Retry camera';
  this.retry.onclick=()=>void this.retryCamera(session,generation);
 }
 private async retryCamera(session:string,generation:number):Promise<void>{
  if(this.session!==session||this.generation!==generation)return;
  if(await this.activate(this.layout.active,false,true)!==true)return;
  if(this.session!==session||this.generation!==generation)return;
  this.say(this.dirty?'Saving…':'Saved');this.retry.hidden=true;
  this.retry.textContent='Retry save';this.retry.onclick=()=>void this.flush();
 }
 /** Reads the saved layout in the newest format the server reads: a server that refuses format 6 is asked again for 5
  * (an older Hooke server), and this studio then keeps reader Tabs for the session only. */
 private async readLayout(session:string):Promise<Record<string,any>>{
  const reply=await this.opts.request('workspace-layout',{session,schema:this.schemaNow()});
  if(reply.ok||reply.code!=='unsupported-schema'||this.schemaNow()<=5)return reply;
  this.serverSchema=5;return this.opts.request('workspace-layout',{session,schema:5});
 }
 private accept(reply:any){if(!LAYOUT_SCHEMAS.includes(reply.layout?.schema)||reply.layout.coordinates!=='stage')throw new Error('Reload Hooke to read this saved layout.');this.revision=reply.revision;this.layout={...reply.layout,active:this.layout.active};
  // Reader Tabs a server too old to save them never has: they stay for the session, after the saved Tabs.
  for(const kept of this.schemaNow()<6?this.sessionReaders?.get(this.session)??[]:[])if(!this.layout.views.some(v=>v.id===kept.id))this.layout.views=[...this.layout.views,kept];this.removed=reply.removed??[];this.completions=reply.completions??[];this.pending=reply.pending??[];this.lastSaved=JSON.stringify(this.wire(this.layout));}
 private async refresh(){
  if(this.tabs.querySelector('input')||studyArtifacts().blockReason(this.session))return;
  if(!this.session||this.changing||this.navigating||this.saving||this.refreshBusy||this.dirty||this.conflict)return;
  this.refreshBusy=true;const session=this.session,g=this.generation;
  try{const [reply,notes]=await Promise.all([this.readLayout(session),this.opts.request('session-note',{session})]);if(g!==this.generation||!reply.ok||this.dirty||this.saving||this.tabs.querySelector('input'))return;
   for(const note of notes.notes??[]){if(this.notes.has(note.id))this.notes.get(note.id)!.receive(note);else this.notes.set(note.id,new NoteState(note,this.recovery,session));}
   // A newer revision (the server placed Results) redraws the saved windows; one it never saves, of this Project, stays.
   this.completions=reply.completions??[];this.pending=reply.pending??[];if(reply.revision!==this.revision){this.clearFlash();this.changing=true;const previous=new Set(this.layout.views.map(v=>v.id));this.accept(reply);this.openIds=this.openIds.filter(id=>this.layout.views.some(v=>v.id===id));for(const v of this.layout.views)if(v.kind==='output'&&!previous.has(v.id)&&!this.besideItsResearcher(v))this.openIds.push(v.id);if(!this.openIds.includes(this.layout.active))this.layout.active=this.openIds[0]??'';studyArtifacts().clearDocuments(root=>root.dataset.workspaceUnsaved==='true'&&root.dataset.project===session);this.loaded.clear();this.changing=false;await this.activate(this.layout.active,true);}
  }catch{}finally{this.refreshBusy=false;}
 }
 private async activate(id:string,initial=false,restoreInput=false,ownInputOpening=false,preserveCurrentInput=false){
  if(this.navigating||this.changing||this.openingInput&&!ownInputOpening)return;this.navigating=true;const g=this.generation;
  // Keyboard focus lands on the Tab it opened (Enter, ⌘1–9): the row is redrawn, which would drop it to the page.
  const fromRow=!initial&&typeof document!=='undefined'&&!!this.tabs?.contains?.(document.activeElement);
  if(!initial){this.capture();await this.flush();}if(g!==this.generation)return;
  const target=this.layout.views.find(v=>v.id===id),input=target?.input;let selectingInput=false,cameraUnavailable=false;
  try{
   if(this.dirty||this.conflict)throw new Error('The layout isn’t saved. Use Retry on the layout line before switching.');
   if(input && this.opts.selectInput){
    const current=this.opts.captureInput?.();
    if(preserveCurrentInput&&current?.source!==input.source)throw new Error('The captured input changed. Choose it and run again.');
    if(!preserveCurrentInput&&(restoreInput||current?.source!==input.source||id!==this.layout.active&&current?.position!==input.position)){
     const blocked=this.opts.inputBlockReason?.(input);if(blocked)throw new Error(blocked);
     document.body.dataset.workspaceInputOpening='true';selectingInput=true;
     await this.opts.selectInput(input,this.session);
     if(g!==this.generation)return;
    }
   }
  }catch(error){
   if(g!==this.generation)return false;
   if(initial||selectingInput)document.body.dataset.workspaceInputUnavailable='true';
   // A saved workspace remains useful without its camera. Preserve the failed
   // input boundary while restoring its independently retained documents/notes.
   cameraUnavailable=initial&&selectingInput&&input?.source==='live'&&error instanceof CameraInputUnavailable;
   if(!cameraUnavailable){this.navigating=false;this.opts.notice(error);return false;}
   this.opts.notice(new Error('Camera unavailable. Your saved Data and notes are open. Reconnect the camera, then choose Retry camera.'));
  }
  finally{if(g===this.generation)delete document.body.dataset.workspaceInputOpening;}
  if(!cameraUnavailable)delete document.body.dataset.workspaceInputUnavailable;this.changing=true;
  try{
   // What the toast said belongs to the Tab it was said in.
   if(this.layout.active!==id)this.clearFlash();
   // A reader Tab says so before the Tab context changes, so what follows the Tab reads the right kind.
   this.layout.active=id;this.navSave();document.body.dataset.activeWorkspaceView=id;this.applySpecimen();this.showReader(this.active());this.render();this.visibility();
   const v=this.active();
   if(v&&(v.kind==='reader'||legacyResearcher(v)))this.loaded.add(id); // a 0.3.0 results Tab draws its pointer, not its windows
   if(v&&!this.loaded.has(id)){
    for(const item of [...v.items]){if(g!==this.generation)return;const recovered=await this.opts.restoreItems([{...item,coordinates:'stage'}]);if(g!==this.generation||this.layout.active!==id)return;if(!recovered.recovered){
     const content=el('div'),message=el('p','',recovered.missing.join('; '));
     const retry=button('Retry retained item',()=>void this.retryItem(item,root,message,retry));content.append(message,retry);
     const root=studyArtifacts().document(`missing:${item.placementId}`,item.title,content,()=>{},{placementId:item.placementId??uuid(),studyRetained:JSON.stringify(item),recoveryPlaceholder:'true'},{background:true});
    }}
    for(const n of [...v.notes]){if(g!==this.generation)return;await this.openNote(n.id,n.rect,n.placementId,{background:true});}
    for(const root of this.visibleRoots()){const item=v.items.find(n=>n.placementId===root.dataset.placementId);if(item?.position&&item.size)root.dataset.savedRect=JSON.stringify([...item.position,...item.size]);}this.loaded.add(id);
   }
  }finally{if(g===this.generation){this.changing=false;this.navigating=false;this.visibility();this.resumeReveal();}}
  if(!initial&&(fromRow||this.focusLost()))document.getElementById?.(`work-tab-${id}`)?.focus?.({preventScroll:true});
  this.observeInput();return true;
 }
 /** Focus fell to the page (a control it was on was redrawn or hidden). */
 private focusLost():boolean{if(typeof document==='undefined'||!('activeElement' in document))return false;const active=document.activeElement;return !active||active===document.body;}
 private async retryItem(item:StudyRetainedView,root:HTMLElement,message:HTMLElement,retry:HTMLButtonElement):Promise<void>{
  if(retry.disabled)return;
  const generation=this.generation,session=this.session,tab=this.layout.active;
  const current=()=>generation===this.generation&&session===this.session&&tab===this.layout.active&&
   root.isConnected&&root.dataset.project===session&&root.dataset.workspaceView===tab&&root.dataset.dismissed!=='true';
  if(!current())return;
  retry.disabled=true;retry.textContent='Reopening…';
  try{
   const result=await this.opts.restoreItems([{...item,coordinates:'stage'}]);
   if(!current())return;
   if(result.recovered){studyArtifacts().removeDocument(root);this.capture();this.changed();}
   else message.textContent=result.missing.join('; ')||'The retained item could not be reopened. Try again.';
  }catch(error){if(current())message.textContent=error instanceof Error?error.message:String(error);}
  finally{retry.disabled=false;retry.textContent='Retry retained item';}
 }
 /** Retain references only; this never starts an operation or publishes a result. */
 private rememberInput():boolean {
  const input=this.opts.captureInput?.(),active=this.active();
  if(!input||this.changing||document.body.dataset.workspaceInputUnavailable)return false;
  if(this.layout.schema<4){
   // Legacy Tabs shared one source. Preserve that source for every existing Tab.
   for(const view of this.layout.views)view.input={...input};
   this.layout.schema=4;return true;
  }
  if(!active||active.kind==='reader'||JSON.stringify(active.input)===JSON.stringify(input))return false;
  active.input={...input};return true;
 }
 observeInput():void {
  if(this.changing||this.navigating||!this.session||!this.opts.captureInput||document.body.dataset.workspaceInputUnavailable)return;
  const input=this.opts.captureInput();
  // Playback position is saved on navigation, not on every playback tick.
  if(input&&this.active()?.input?.source!==input.source&&this.rememberInput())this.changed();
 }
 async openInput(input:WorkspaceTabInput,title:string,owner=this.tabOwner()):Promise<boolean>{
  if(!this.ownsTabChoice(owner))throw new Error('The Project changed. Choose the input again in its intended Project.');
  if(this.openingInput)throw new Error('An input is opening. Wait before opening another.');
  const generation=this.generation;this.openingInput=true;
  try{return await this.openInputTab(input,title);}finally{if(generation===this.generation)this.openingInput=false;}
 }
 private async openInputTab(input:WorkspaceTabInput,title:string):Promise<boolean>{
  if(this.changing||this.navigating)throw new Error('A Tab is opening. Wait before opening another input.');
  const blocked=this.opts.inputBlockReason?.(input);if(blocked)throw new Error(blocked);
  const project=this.session,generation=this.generation;
  if(this.rememberInput())this.changed();
  this.capture();await this.flush();
  if(project!==this.session||generation!==this.generation)throw new Error('The Project changed. Open the input in its intended Project.');
  if(this.dirty||this.conflict)throw new Error('The layout isn’t saved. Use Retry on the layout line before opening another input.');
  let target=this.layout.views.find(v=>v.input?.source===input.source);
  if(!target&&this.layout.views.length===1){
   const main=this.layout.views[0];
   if(!main.input&&!main.items.length&&!main.notes.length){
    target=main;target.input={...input};this.layout.schema=Math.max(4,this.layout.schema) as LayoutSchema;this.changed();await this.flush();
    if(project!==this.session||generation!==this.generation)return false;
    if(this.dirty||this.conflict)throw new Error('The input Tab is not saved yet. Use Retry save or Review recovery.');
   }
  }
  if(!target){
   if(this.layout.views.length>=24)throw new Error('24 Tabs are saved. Remove one you no longer use to make room; its Data stays.');
   target={id:uuid(),title:title.slice(0,80),input:{...input},specimen:{visible:true,rect:null},items:[],notes:[]};
   this.layout.schema=Math.max(4,this.layout.schema) as LayoutSchema;this.layout.views.push(target);this.changed();await this.flush();
   if(project!==this.session||generation!==this.generation)return false;
   if(this.dirty||this.conflict)throw new Error('The new Tab is not saved yet. Use Retry save or Review recovery.');
  }
  if(!this.openIds.includes(target.id))this.openIds.push(target.id);
  if(!await this.activate(target.id,false,false,true))return false;
  if(!target.specimen.visible)this.showSpecimen();await this.flush();return true;
 }
 /** A Tab is opening or the Project is changing: arrangement gestures wait, rather than act on half a Tab. */
 private busy(){return this.changing||this.navigating||this.openingInput;}
 /** Main (the Project's first Tab) is home: always open, never removed, and its specimen always shown. */
 private isHome(id:string){return id===(this.layout.views.find(v=>v.id==='main')??this.layout.views[0])?.id;}
 private applySpecimen(){const v=this.active();if(v&&this.isHome(v.id)&&!v.specimen.visible){v.specimen.visible=true;v.specimen.rect=null;this.changed();}
  const home=!!v&&this.isHome(v.id);if(this.specimenHide)this.specimenHide.hidden=home;
  if(this.specimenSize){this.specimenSize.textContent=v?.specimen.rect?'Fill Tab':'Float specimen';this.specimenSize.title=v?.specimen.rect?'Fill the Tab with the specimen, behind its windows':'Float the specimen as a window in this Tab';}
  this.specimenReturn.hidden=!v||home||v.specimen.visible;
  // The Stage bar says the same: Float or Fill, Hide on a Tab that may hide it, or that it is hidden and how to show it.
  if(this.stageSize&&this.stageHide){
   this.stageSize.textContent=v?.specimen.rect?'Fill':'Float';this.stageSize.title=this.specimenSize?.title??'';this.stageHide.hidden=home;
   this.stageWork.dataset.specimen=!v?'none':v.specimen.visible?'shown':'hidden';
  }
  const s=v?.specimen;this.specimen.hidden=!s?.visible;document.body.dataset.workspaceSpecimenVisible=String(!!s?.visible);this.specimen.classList.toggle('workspace-specimen-full',!s?.rect);if(s?.rect)this.specimen.dataset.savedRect=JSON.stringify(s.rect);else delete this.specimen.dataset.savedRect;if(s?.visible&&s.rect){this.specimenShell.resizeTo(s.rect[2],s.rect[3]);this.specimenShell.moveTo(s.rect[0],s.rect[1]);}}
 /** Shows the Tab's specimen on the Stage, as its backdrop when it was hidden (the Assistant's specimen chip, Tabs…). */
 showInput(){this.showSpecimen();}
 private showSpecimen(){if(!this.active())return;const s=this.active()!.specimen;if(!s.visible)s.rect=null;s.visible=true;this.applySpecimen();this.changed();}
 private async addView(title?:string,activate=true){if(this.changing||this.navigating||this.openingInput)return;const session=this.session,generation=this.generation;if(this.layout.views.length>=24){this.opts.notice('24 Tabs are saved. Remove one you no longer use to make room; its Data stays.');this.manage();return;}
  this.capture();await this.flush();if(session!==this.session||generation!==this.generation||this.changing||this.navigating||this.openingInput||this.dirty||this.conflict)return;const input=this.opts.captureInput?.();const v:WorkspaceView={...(input?{input:{...input}}:{}),id:uuid(),title:(title?.trim()||`Tab ${this.layout.views.length+1}`).slice(0,80),specimen:{visible:true,rect:null},items:[],notes:[]};this.layout.views.push(v);this.openIds.push(v.id);this.loaded.add(v.id);this.changed();if(activate)await this.activate(v.id);else{this.navSave();this.render();}return v.id;
 }
 private render(){
  this.renderCompletions();
  this.noteButton.disabled=!this.active()||!this.openIds.includes(this.layout.active);
  // Main is always first, whatever order a saved layout or an older browser kept.
  const open=this.layout.views.filter(v=>this.openIds.includes(v.id)),home=open.find(v=>this.isHome(v.id));
  this.tabs.replaceChildren();for(const v of home?[home,...open.filter(v=>v!==home)]:open)this.tabs.append(this.workTab(v));
  this.stage.setAttribute('role','tabpanel');this.stage.setAttribute('aria-label',this.active()?.title??'No open tab');this.stage.id='stage';
  // A distinct panel anchor avoids changing the established stage identity.
  studyArtifacts().root.id='study-artifacts';studyArtifacts().root.setAttribute('role','tabpanel');studyArtifacts().root.setAttribute('aria-labelledby',`work-tab-${this.layout.active}`);
  for(const root of this.roots())this.updateTransfer(root);
  window.dispatchEvent(new Event('workspace-tabs-changed')); // the Tab in view, or a name, may have changed (Results go to …)
  this.refit();
 }
 /** One Tab anatomy (the mock's tabRow()): its kind's mark, its whole name, and × (Main has none; it is home). */
 private workTab(v:WorkspaceView):HTMLElement{
  const home=this.isHome(v.id),selected=v.id===this.layout.active;
  const kind:TabMark=home?'home':v.kind==='reader'?this.opts.readerMark?.(v)??'output':v.kind==='output'?'output':v.kind==='compare'?'compare':v.input&&this.opts.describeInput?.(v.input.source)==='recording'?'recording':'picture';
  const wrap=el('div','workspace-view-tab'),tab=button('',()=>void this.activate(v.id));
  wrap.dataset.kind='work-tab';wrap.dataset.tabKind=kind;if(home)wrap.dataset.home='true';
  tab.append(tabMark(kind));
  // Iconified (tab-row-fit.ts), a picture or recording shows its own thumbnail, so two of them are told apart; the
  // image loads only when shown (it is not displayed until then).
  const thumb=(kind==='picture'||kind==='recording')&&v.input?this.opts.inputThumb?.(v.input.source):null;
  if(thumb){const picture=el('img','workspace-tab-thumb');picture.src=thumb;picture.alt='';picture.loading='lazy';picture.decoding='async';tab.append(picture);}
  tab.append(el('span','workspace-tab-label',v.title));
  // A recording's label shortens before any Tab folds away (its whole title stays the Tab's tooltip).
  if(kind==='recording'&&shortLabel(v.title)!==v.title){wrap.dataset.short='true';tab.append(el('span','workspace-tab-short',shortLabel(v.title)));}
  tab.title=home?`${v.title} · home: always open, with the specimen`:v.title;tab.dataset.workspaceViewTab=v.id;tab.id=`work-tab-${v.id}`;tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(selected));tab.setAttribute('aria-controls','study-artifacts');tab.tabIndex=selected?0:-1;
  const rename=()=>{const field=el('input');field.value=v.title;field.maxLength=80;field.setAttribute('aria-label','Tab name');tab.replaceWith(field);field.focus();field.select();let done=false;const finish=(save:boolean)=>{if(done)return;done=true;if(save&&field.value.trim()){v.title=field.value.trim();this.changed();}this.render();document.getElementById(`work-tab-${v.id}`)?.focus();};field.onkeydown=e=>{if(e.key==='Enter')finish(true);if(e.key==='Escape')finish(false);};field.onblur=()=>finish(true);};tab.ondblclick=rename;
  tab.onkeydown=e=>{
   // Only the Tabs in sight: one folded into ⌄ is hidden and cannot take focus.
   const all=[...this.tabs.querySelectorAll<HTMLButtonElement>('[data-workspace-view-tab]')].filter(t=>!t.closest<HTMLElement>('.workspace-view-tab')?.hidden);const i=all.indexOf(tab);
   if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){
    e.preventDefault();const j=e.key==='Home'?0:e.key==='End'?all.length-1:(i+(e.key==='ArrowLeft'?-1:1)+all.length)%all.length;
    // Alt+arrow moves a Tab, never Main and never before it.
    if(e.altKey){const target=all[j]?.dataset.workspaceViewTab;if(home||!target||this.isHome(target))return;const from=this.layout.views.indexOf(v);this.layout.views.splice(from,1);this.layout.views.splice(this.layout.views.findIndex(n=>n.id===target)+(j>i?1:0),0,v);this.changed();this.render();document.getElementById(`work-tab-${v.id}`)?.focus();}
    else all[j]?.focus();
   }
   if(e.key==='F2'){e.preventDefault();rename();}
  };
  wrap.append(tab);
  if(!home){const close=button('',()=>void this.closeTab(v.id));close.className='workspace-tab-x';close.append(tabMark('x'));close.setAttribute('aria-label',`Close tab ${v.title}`);close.title='Close this Tab. Nothing stops; it stays under ⌄.';wrap.append(close);}
  return wrap;
 }
 private rowParts():TabRowParts{return {strip:this.strip,tabs:this.tabs,fade:this.fade,count:this.allCount,more:this.allTabs};}
 /** Fit the Tab row after it changed: once per frame at most, never per frame. While the side columns move it waits
  * and fits once when they land (side-motion.ts). */
 refit(){
  if(!this.allTabs||typeof requestAnimationFrame!=='function'||this.fitFrame||holdUntilLanded(this.refitLater))return;
  this.fitFrame=requestAnimationFrame(()=>{this.fitFrame=0;fitTabRow(this.rowParts());});
 }
 private readonly refitLater=()=>this.refit();
 /** A side column is opening and the Stage narrows to `stageWidth` (CSS px): the Tab row is fitted once now for that
  * width, the narrower one, so no Tab is cut while the Stage narrows (the row then waits for the motion's end). */
 fitAhead(stageWidth:number){
  const studio=this.strip.parentElement;if(!this.allTabs||!studio)return;
  const row=this.strip.getBoundingClientRect().width,narrower=row-(studio.getBoundingClientRect().width-stageWidth);
  if(!(narrower>0)||narrower>=row-0.5)return;
  this.strip.style.maxWidth=`${Math.floor(narrower)}px`;
  try{fitTabRow(this.rowParts());}finally{this.strip.style.maxWidth='';}
 }
 /** Where a researcher's page puts its bar (back, title, Stop) and where the Project's own controls go. */
 stageSlots(){return {research:this.stageResearch,right:this.stageRight,reader:this.stageReader};}
 /** A reader Tab shows its item full width (the studio's reader, `opts.reader`), the Stage bar naming it; any other
  * Tab hides it. */
 private showReader(view:WorkspaceView|undefined){
  const reading=view?.kind==='reader',legacy=!reading&&!!legacyResearcher(view);
  if(document.body?.dataset){if(reading)document.body.dataset.readerTab='true';else delete document.body.dataset.readerTab;
   if(legacy)document.body.dataset.legacyTab='true';else delete document.body.dataset.legacyTab;}
  // A 0.3.0 results Tab names itself where a reader Tab names its item ("Researcher 3 results · Made by an earlier version").
  if(this.stageReader)this.stageReader.hidden=!reading&&!legacy;this.opts?.reader?.(reading?view!:null);this.opts?.legacy?.(legacy?view!:null);
 }
 /** Open in new Tab: one Data item in a Tab of its own (kind `reader`, schema 6), the same reader full width. An item
  * that already has one goes to it. True once that Tab is in view. */
 async openReaderTab(item:ReaderTabItem,owner=this.tabOwner()):Promise<boolean>{
  if(!this.ownsTabChoice(owner))throw new Error('The Project changed. Open it again from its Data.');
  if(this.busy())throw new Error('A Tab is still opening. Try again in a moment.');
  const same=this.layout.views.find(v=>{const shown=readerItem(v);return !!shown&&shown.resource.day===item.resource.day&&shown.resource.id===item.resource.id&&shown.resource.version===item.resource.version;});
  if(same)return this.goTo(same.id,owner);
  if(this.layout.views.length>=24)throw new Error('24 Tabs are saved. Remove one you no longer use to make room; its Data stays.');
  this.capture();await this.flush();
  if(!this.ownsTabChoice(owner)||this.dirty||this.conflict)throw new Error('The layout isn’t saved. Use Retry on the layout line, then open it again.');
  const view=readerView(uuid(),item);
  this.layout.views.push(view);this.openIds.push(view.id);this.loaded.add(view.id);
  if(this.schemaNow()<6)this.sessionReaders.set(this.session,[...(this.sessionReaders.get(this.session)??[]),view]);
  this.changed();await this.flush();
  if(!this.ownsTabChoice(owner))return false;
  return this.goTo(view.id,owner);
 }
 /** True when this server keeps reader Tabs only for the session (it cannot save them). */
 readerTabsUnsaved():boolean{return this.schemaNow()<6;}
 /** Add to Tab… from Data or the reader: the item goes into that Tab (or a new one named after it) and you stay where
  * you are; "Added to Tab 2 · Go to Tab 2" says so once its save was accepted. */
 async addItem(to:string|null,item:StudyRetainedView,owner=this.tabOwner()):Promise<void>{
  if(!this.ownsTabChoice(owner))throw new Error('The Project changed. Add it again from its Data.');
  if(this.busy())throw new Error('A Tab is still opening. Try again in a moment.');
  this.capture();await this.flush();
  if(!this.ownsTabChoice(owner)||this.dirty||this.conflict)throw new Error('The layout isn’t saved. Use Retry on the layout line, then add it again.');
  const id=to??await this.addView(tabName(item.title),false);
  const target=id?this.layout.views.find(v=>v.id===id):undefined;
  if(!target||!this.ownsTabChoice(owner))throw new Error('That Tab is no longer in this Project. Nothing was added.');
  if(target.kind==='reader')throw new Error('That Tab shows one thing on its own. Choose another Tab.');
  if(target.items.length>=64)throw new Error('That Tab is full. Choose another Tab.');
  let placed=target.items.find(i=>sameStudyResource(i.resource,item.resource)&&i.presentation===item.presentation)?.placementId;
  if(!placed){
   placed=uuid();target.items.push({...structuredClone(item),placementId:placed});
   if(target.id===this.layout.active)this.completionRestores.add(placed);
   this.loaded.delete(target.id);this.changed();await this.flush();
  }
  if(!this.ownsTabChoice(owner))return;
  if(target.id===this.layout.active&&!this.dirty&&!this.conflict){await this.activate(target.id,true);if(!this.ownsTabChoice(owner))return;}
  const name=target.title,saved=!this.dirty&&!this.conflict;
  this.flash(`Added to ${name}${saved?'':' · not saved yet'}`,target.id===this.layout.active?[]:[[`Go to ${name}`,()=>{if(this.ownsTabChoice(owner))void this.goTo(target.id,owner);}]]);
 }
 /** The specimen's name for the Stage bar: its title's first part, the rest (its credit) quieter, and its picture. */
 /** The Stage bar's left on a work Tab: the specimen's name, its credit and its picture. Until the bridge's first
  * report (`specimenLabel` never called) the left holds nothing (`data-heard="false"` keeps its children out of
  * layout), so nothing claims there is no specimen and nothing moves when it is named: the name, credit and thumbnail
  * then appear together, fading in with the specimen (workspace-feel.css). */
 specimenLabel(title:string,thumb=''){
  if(this.stageWork.dataset.heard!=='true')this.stageWork.dataset.heard='true';
  const key=`${title}\n${thumb}`;if(key===this.labelKey)return;this.labelKey=key;
  const [name,...rest]=title.split(' · ');this.stageName.textContent=name;this.stageName.title=title;
  this.stageCredit.textContent=rest.join(' · ');this.stageCredit.hidden=!rest.length;
  this.stageThumb.style.backgroundImage=thumb?`url("${thumb}")`:'';this.stageThumb.hidden=!thumb;this.stageThumb.title=title;
 }
 private renderCompletions(){
  const rows=this.completions.filter(c=>c.origin?.tab===this.layout.active&&c.notice).slice(-3),owner=this.tabOwner();
  this.completionNote.replaceChildren();this.completionNote.hidden=!rows.length;
  for(const row of rows){const line=el('div','',row.notice);
   // A researcher's results Tab of 0.3.0 now points to the researcher: Go to results opens the researcher's own Tab.
   const pointer=row.state==='placed'&&row.outputTab?legacyResearcher(this.layout.views.find(v=>v.id===row.outputTab.id)):null;
   if(pointer&&this.opts.openResearcher)line.append(button('Go to results',()=>{if(this.ownsTabChoice(owner))this.opts.openResearcher?.(pointer);}));
   else if(row.state==='placed'&&row.outputTab&&this.layout.views.some(v=>v.id===row.outputTab.id))line.append(button('Go to results',()=>{if(this.ownsTabChoice(owner))void this.goTo(row.outputTab.id,owner);}));
   this.completionNote.append(line);
  }
 }
 private async closeTab(id:string){
  const blocked=studyArtifacts().blockReason(this.session,id);if(blocked){this.opts.notice(blocked);return;}
  if(this.changing||this.navigating||this.openingInput||this.isHome(id))return;
  const generation=this.generation;this.capture();await this.flush();
  if(generation!==this.generation||this.dirty||this.conflict)return;
  const remaining=this.openIds.filter(v=>v!==id),at=this.openIds.indexOf(id);
  // The Tab beside it comes into view, as closing a browser tab does (the one after, else the one before).
  const beside=this.openIds[at+1]??this.openIds[at-1]??'';
  // Keep the old Tab visible/capturable until replacement input admission succeeds.
  if(id===this.layout.active&&!await this.activate(beside))return;
  if(generation!==this.generation)return;
  // Where the Tab stood, for the copy that shows where it went; then the state changes at once.
  const from=document.getElementById?.(`work-tab-${id}`)?.closest('.workspace-view-tab')?.getBoundingClientRect()??null;
  this.openIds=remaining;this.navSave();this.visibility();this.render();this.tabs.querySelector<HTMLElement>('[aria-selected=true]')?.focus();
  this.closedStack=[...this.closedStack.filter(c=>c.id!==id),{id,at}].slice(-24);
  this.offerReopen(id,from);window.dispatchEvent(new CustomEvent('workspace-tab-closed',{detail:{session:this.session,kind:'work',id}}));
 }
 /** "Closed X", said for a moment in the layer under the bars with Reopen, where it went said in its words and shown by
  * a copy of the Tab moving there: Data for a Tab with an input or results, ⌄ for any other. */
 private offerReopen(id:string,from:DOMRect|null=null){
  const v=this.layout.views.find(x=>x.id===id);if(!v)return;
  const results=v.kind==='output'||v.items.length>0||v.notes.length>0,input=!!v.input&&v.input.source!=='live';
  this.showClosed(results?`Closed ${v.title}. Its results are in Data.`:input?`Closed ${v.title}. Its input is in Data.`:`Closed ${v.title}.`,`Reopen ${v.title}`,()=>void this.reopenClosed(id));this.closedId=id;
  const mark=document.getElementById?.(`work-tab-${id}`)?.querySelector('.workspace-tab-icon')??null;
  flyTo(from,v.title,mark,results||input?this.opts.closeTarget?.('data')??this.allTabs:this.allTabs);
 }
 /** A researcher's or Study's Tab closed (workspace-research-tabs.ts): the same toast and motion, toward Researchers. */
 researchClosed(text:string,name:string,reopen:()=>void,from:DOMRect|null,mark:Element|null){
  this.showClosed(text,`Reopen ${name}`,reopen);this.closedId='';
  flyTo(from,name,mark,this.opts.closeTarget?.('researchers')??this.allTabs);
 }
 /** On a researcher's or Study's Tab the toasts sit at the Stage's bottom centre, clear of its artifact's toolbar
  * (workspace-feel.css reads the Stage's height from `--stage-h`, measured here, when a toast shows). */
 private placeToasts(){
  if(document.body?.dataset?.researchOpen!=='true'||typeof this.stage.getBoundingClientRect!=='function')return;
  this.toasts.style.setProperty('--stage-h',`${Math.round(this.stage.getBoundingClientRect().height)}px`);
 }
 private showClosed(text:string,label:string,reopen:()=>void){
  this.placeToasts();
  clearTimeout(this.closedTimer);this.closedNote.textContent=text;this.closedToast.hidden=false;
  this.reopen.setAttribute('aria-label',label);this.reopen.onclick=()=>{this.clearReopen();reopen();};this.closedTimer=window.setTimeout(()=>this.clearReopen(),10000);
 }
 private clearReopen(){clearTimeout(this.closedTimer);this.closedId='';if(this.closedToast)this.closedToast.hidden=true;}
 private async reopenClosed(id=this.closedId){
  if(this.busy()){this.flash('A Tab is still opening; reopen it in a moment');return;}
  if(!id||!this.layout.views.some(v=>v.id===id))return;
  const owner=this.tabOwner(),closed=this.closedStack.find(c=>c.id===id),wasOpen=this.openIds.includes(id);
  if(!wasOpen)this.openIds.splice(Math.min(closed?.at??this.openIds.length,this.openIds.length),0,id);
  if(!await this.activate(id)||!this.ownsTabChoice(owner)){
   if(this.ownsTabChoice(owner)&&!wasOpen){this.openIds=this.openIds.filter(open=>open!==id);this.navSave();this.render();}return;
  }
  this.clearReopen();this.closedStack=this.closedStack.filter(c=>c.id!==id);
  if(this.focusLost())document.getElementById?.(`work-tab-${id}`)?.focus?.({preventScroll:true}); // the toast's Reopen went with it
  window.dispatchEvent(new CustomEvent('workspace-tab-reopened',{detail:{session:this.session,kind:'work',id}}));
 }
 /** The Tab commands, one way in for the keyboard, the Mac app's Tab menu and Tabs…: New, Close, Reopen (the most recent
  * closed Tab still closed), Duplicate, Next, Previous, and Go to the nth open Tab (9 is the last, as in browsers). */
 command(name:'new'|'close'|'reopen'|'duplicate'|'next'|'previous'|`go:${number}`){
  if(!this.session||document.querySelector('dialog:modal'))return; // never behind a modal dialog (a sheet stays open, not modal)
  if(this.opts.intercept?.(name))return;
  const open=this.layout.views.filter(v=>this.openIds.includes(v.id)),at=open.findIndex(v=>v.id===this.layout.active);
  if(name==='new'){void this.addView();return;}
  if(name==='duplicate'){void this.duplicateTab();return;}
  if(name==='close'){
   if(this.isHome(this.layout.active)){this.flash('Main stays open: it holds the specimen');return;}
   void this.closeTab(this.layout.active);return;
  }
  if(name==='reopen'){
   const last=[...this.closedStack].reverse().find(c=>this.layout.views.some(v=>v.id===c.id)&&!this.openIds.includes(c.id));
   if(last)this.reopenClosed(last.id);else this.flash('No closed Tab to reopen');return;
  }
  if(!open.length)return;
  const n=name==='next'?(at+1)%open.length:name==='previous'?(at-1+open.length)%open.length:Number(name.slice(3))>=9?open.length-1:Number(name.slice(3))-1;
  if(open[n]&&open[n].id!==this.layout.active)void this.goTo(open[n].id);
 }
 /** Duplicate Tab: the same input, specimen and results in a new Tab beside it; each window is a new placement, so
  * moving or closing one leaves the other Tab as it was. */
 private async duplicateTab(){
  let source=this.active();if(!source||this.changing||this.navigating||this.openingInput)return;
  if(this.layout.views.length>=24){this.opts.notice('24 Tabs are saved. Remove one you no longer use to make room; its Data stays.');this.manage();return;}
  const session=this.session,generation=this.generation,sourceId=source.id;this.capture();await this.flush();
  if(session!==this.session||generation!==this.generation||this.changing||this.navigating||this.openingInput||this.dirty||this.conflict)return;
  // The save may have brought newly placed results (and a new layout object): copy the Tab as it is now.
  source=this.layout.views.find(v=>v.id===sourceId);if(!source)return;
  const copy:WorkspaceView={...structuredClone(source),id:uuid(),title:`${source.title} copy`.slice(0,80)};
  renewPlacements(copy,uuid);
  this.layout.views.splice(this.layout.views.indexOf(source)+1,0,copy);
  this.openIds.splice(this.openIds.indexOf(source.id)+1,0,copy.id);
  this.changed();await this.activate(copy.id);
 }
 /** The layout's status line. Plain Saved is quiet (pass doc H6: saving is silent; it shows on hover or focus); every
  * other state, a problem or a change in progress, stays in view. */
 private say(text:string){
  // The chip takes room only while the layout needs the person, so a change in what it shows refits the row at once
  // (the strip's own size does not change; the observer on the tools also catches Retry and Review recovery).
  const quiet=String(!layoutStatusNeedsYou(text)),changed=this.status.textContent!==text&&!(quiet==='true'&&this.status.dataset?.quiet==='true');
  this.status.textContent=text;if(this.status.dataset)this.status.dataset.quiet=quiet;if(changed)this.refit();
 }
 /** "+": the one place to add to the Tab row (Will, 2026-10-23: the row holds Tabs only). A Tab, or a researcher or study
  * that opens as a Tab of its own. ⌘T still makes a Tab at once. */
 private addMenu(anchor:HTMLElement){
  const extra=this.opts.addChoices?.()??[];
  document.querySelector('.workspace-add-menu')?.remove();
  const menu=el('div','workspace-add-menu');menu.setAttribute('role','menu');menu.setAttribute('aria-label','Add to this Project');
  const close=()=>{menu.remove();document.removeEventListener('pointerdown',outside,true);anchor.focus({preventScroll:true});};
  const outside=(event:Event)=>{if(!menu.contains(event.target as Node)&&event.target!==anchor)close();};
  const item=(label:string,title:string,act:()=>void,key='')=>{const b=button(label,()=>{close();act();});b.setAttribute('role','menuitem');b.title=title;if(key)b.append(el('kbd','',key));menu.append(b);return b;};
  const first=item('New Tab','Another desk for this Project, with the same specimen, Data and conversation',()=>this.command('new'),'⌘T');
  const note=item('New note','A note in this Tab; notes save to Project Data',()=>void this.newNote());note.disabled=this.noteButton.disabled;
  for(const choice of extra)item(choice.label,choice.title,choice.act);
  menu.addEventListener('keydown',event=>{const items=[...menu.querySelectorAll<HTMLButtonElement>('button')],at=items.indexOf(document.activeElement as HTMLButtonElement);
   if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}
   else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();items[(at+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}});
  // Under its button, its right edge on the button's: the row's tools sit at its right end.
  const r=anchor.getBoundingClientRect();menu.style.right=`${Math.max(8,Math.round(viewportWidth()-r.right))}px`;menu.style.top=`${Math.round(r.bottom+4)}px`;
  document.body.append(menu);document.addEventListener('pointerdown',outside,true);first.focus();
 }
 /** ⌄ All Tabs (the mock's menu "all"): every open Tab in row order, the one showing marked; the closed ones (work,
  * researcher, Study) with Reopen; studies to open as Tabs; then the layout's status, its recovery copies and Manage
  * Tabs… (rename, duplicate, remove, arrange and restore stay there). Choosing a Tab goes the way a click on it would. */
 private allMenu(anchor:HTMLElement){
  document.querySelector('.workspace-add-menu')?.remove();
  const menu=el('div','workspace-add-menu workspace-all-menu');menu.setAttribute('role','menu');menu.setAttribute('aria-label','All Tabs');
  const close=()=>{menu.remove();document.removeEventListener('pointerdown',outside,true);anchor.focus({preventScroll:true});};
  const outside=(event:Event)=>{if(!menu.contains(event.target as Node)&&event.target!==anchor)close();};
  const item=(label:string,title:string,act:()=>void,key='',mark?:Element|null)=>{const b=button('',()=>{close();act();});b.setAttribute('role','menuitem');b.title=title;if(mark)b.append(mark);b.append(el('span','workspace-menu-label',label));if(key)b.append(el('kbd','',key));menu.append(b);return b;};
  const heading=(text:string)=>{const h=el('div','workspace-menu-heading',text);h.setAttribute('role','presentation');menu.append(h);};
  heading('Open');let showing:HTMLButtonElement|null=null;
  for(const wrap of [...this.tabs.children] as HTMLElement[]){
   const tab=wrap.querySelector<HTMLButtonElement>('[role=tab]');if(!tab)continue;
   const selected=tab.getAttribute('aria-selected')==='true',name=wrap.querySelector('.workspace-tab-label')?.textContent||tab.textContent||'Tab';
   const b=item(name,tab.title,()=>tab.click(),selected?'Showing':'',wrap.querySelector('.workspace-tab-icon,[data-kind="lit-cell"]')?.cloneNode(true) as Element|null);if(selected)showing=b;
  }
  const extra=this.opts.menuTabs?.()??{closed:[],studies:[]},closedWork=this.layout.views.filter(v=>!this.openIds.includes(v.id));
  if(closedWork.length||extra.closed.length){
   menu.append(el('hr'));heading('Closed');
   for(const v of closedWork)item(v.title,`Reopen ${v.title}`,()=>{if(this.opts.leaveResearch&&!this.opts.leaveResearch())return;void this.reopenClosed(v.id);},'Reopen');
   for(const tab of extra.closed)item(tab.name,tab.title??`Reopen ${tab.name}`,tab.act,'Reopen',tab.mark);
  }
  if(extra.studies.length){menu.append(el('hr'));heading('Studies');for(const study of extra.studies)item(study.name,study.title??study.name,study.act,'',study.mark);}
  menu.append(el('hr'));
  const status=this.status.textContent||'Saved';heading(status==='Saved'?'Layout saved':`Layout: ${status}`);
  if(!this.retry.hidden)item(this.retry.textContent||'Retry','Try the layout again',()=>this.retry.click());
  const copies=this.session?this.recovery.list(this.session,'layout').length:0;
  const recovery=item('Recovery copies',copies?'Review this browser’s copies of the arrangement; Saved Data is unaffected':'This browser holds no copies of the arrangement',()=>this.reviewRecovery(),copies?String(copies):'none');recovery.disabled=!copies;
  item('Manage Tabs…','Rename, duplicate, remove, arrange or restore Tabs',()=>void this.manage());
  menu.addEventListener('keydown',event=>{const items=[...menu.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')],at=items.indexOf(document.activeElement as HTMLButtonElement);
   if(event.key==='Escape'){event.preventDefault();event.stopPropagation();close();}
   else if(event.key==='ArrowDown'||event.key==='ArrowUp'){event.preventDefault();items[(at+(event.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}});
  const r=anchor.getBoundingClientRect();menu.style.right=`${Math.max(8,Math.round(viewportWidth()-r.right))}px`;menu.style.top=`${Math.round(r.bottom+4)}px`;
  document.body.append(menu);document.addEventListener('pointerdown',outside,true);(showing??menu.querySelector<HTMLButtonElement>('button'))?.focus();
 }
 private dialog(title:string){
  const dialog=el('dialog','workspace-recovery-dialog'),header=el('header','workspace-dialog-header'),heading=el('h2','',title);
  heading.id=`workspace-dialog-${uuid()}`;dialog.setAttribute('aria-labelledby',heading.id);
  header.append(heading,button('Close',()=>dialog.close()));dialog.append(header);
  // Focus goes back where it came from, or to the selected Tab when that control was redrawn meanwhile (sheet.ts does
  // the same), so closing never strands the keyboard on the page.
  const active=document.activeElement as HTMLElement|null,opener=typeof active?.focus==='function'?active:null;
  document.body.append(dialog);dialog.addEventListener('close',()=>{dialog.remove();(opener?.isConnected?opener:this.tabs.querySelector<HTMLElement>('[aria-selected=true]'))?.focus({preventScroll:true});});dialog.showModal();return dialog;
 }
 private async manage(){await this.refresh();const d=this.dialog('Tabs in this Project');
  d.append(button('New note',()=>{d.close();void this.newNote();}),button('Show specimen',()=>{d.close();this.showSpecimen();}),button('Arrange',()=>{d.close();this.arrange();}),button('Bring windows into view',()=>{d.close();this.fit();}));
  if(this.undo)d.append(button('Undo arrange',()=>{this.capture();this.layout=undoArrange(this.layout,this.undo!.before,this.undo!.arranged);this.undo=null;this.changing=true;studyArtifacts().clearDocuments();this.loaded.clear();this.changing=false;void this.activate(this.layout.active,true).then(()=>this.changed());d.close();}));
  // Open Tabs first; the closed ones under their own heading, each with Reopen.
  const closedViews=this.layout.views.filter(v=>!this.openIds.includes(v.id)),extraClosed=this.opts.extraClosed?.()??[];
  for(const v of [...this.layout.views.filter(v=>this.openIds.includes(v.id)),...closedViews]){if(v===closedViews[0])d.append(el('h3','',`Closed Tabs · ${closedViews.length+extraClosed.length}`));const row=el('div','workspace-management-row');row.append(el('strong','',v.title));row.append(button(this.openIds.includes(v.id)?'Go to Tab':'Reopen',()=>{d.close();if(this.opts.leaveResearch&&!this.opts.leaveResearch())return;if(!this.openIds.includes(v.id))this.openIds.push(v.id);void this.activate(v.id);}));row.append(button('Rename',()=>{d.close();if(!this.openIds.includes(v.id))this.openIds.push(v.id);void this.activate(v.id).then(()=>this.tabs.querySelector<HTMLElement>(`[data-workspace-view-tab="${v.id}"]`)?.dispatchEvent(new MouseEvent('dblclick')));}));row.append(button('Duplicate',()=>{d.close();void this.goTo(v.id).then(ok=>{if(ok)void this.duplicateTab();});}));if(!this.isHome(v.id))row.append(button('Remove Tab',()=>{const confirm=this.dialog('Remove this Tab?');confirm.append(el('p','','Removes this Tab from the Project. Its Data and notes stay. Restore it under Removed Tabs.'),button('Remove Tab',()=>{confirm.close();d.close();void this.arrangement('remove',v.id);}));}));d.append(row);}
  if(extraClosed.length){if(!closedViews.length)d.append(el('h3','',`Closed Tabs · ${extraClosed.length}`));
   for(const item of extraClosed){const row=el('div','workspace-management-row');row.append(el('strong','',item.name),button('Reopen',()=>{d.close();item.reopen();}));d.append(row);}
   d.append(el('p','workspace-management-note','Researcher and study Tabs are remembered in this browser only; the researchers and studies themselves are in this Project.'));}
  const draftIds=this.recovery.noteIds(this.session);if(draftIds.length){d.append(el('h3','','Note draft recovery'));for(const id of draftIds)d.append(button(`Recover note draft · ${this.notes.get(id)?.value.title??id}`,()=>{d.close();void this.openNote(id);}));}
  if(this.removed.length){d.append(el('h3','','Removed Tabs'));for(const v of this.removed)d.append(button(`Restore ${v.title}`,()=>{d.close();void this.arrangement('restore',v.id);}));}
  for(const pending of this.pending)d.append(button(`Dismiss pending presentation in ${this.layout.views.find(v=>v.id===pending.tab)?.title??pending.tab}`,()=>void this.opts.request('workspace-layout',{session:this.session,schema:this.schemaNow(),action:'dismiss',run:pending.run}).then(r=>{if(r.ok){d.close();this.opts.notice('Presentation dismissed. Work continues; its result will stay in Data.');}else this.opts.notice(r.error);})));
  const available=this.completions.filter(c=>c.state!=='placed');if(available.length){d.append(el('h3','','Completed work in Data'));for(const c of available){d.append(el('p','',`${c.item.title} — ${c.reason??'retained in Data'}`),button('Open in this Tab',()=>{if(!this.active()){this.opts.notice('Reopen or create a Tab first.');return;}d.close();void this.opts.restoreItems([{...c.item,placementId:uuid(),coordinates:'stage'}],{background:false});}));}}
 }
 private async arrangement(action:'remove'|'restore',id:string){const blocked=studyArtifacts().blockReason(this.session,id);if(blocked){this.opts.notice(blocked);return;}if(action==='remove'&&this.isHome(id))return;await this.flush();if(this.conflict)return;const r=await this.opts.request('workspace-layout',{session:this.session,schema:this.schemaNow(),action,tab:id,base:this.revision});if(!r.ok){this.opts.notice(r.error);return;}this.changing=true;this.accept(r);studyArtifacts().clearDocuments();this.loaded.clear();if(action==='restore')this.openIds.push(id);else this.openIds=this.openIds.filter(n=>n!==id);this.changing=false;await this.activate(action==='restore'?id:this.openIds[0]??'',true);this.say('Tab restored');this.retry.hidden=true;this.retry.textContent='Retry save';this.retry.onclick=()=>void this.flush();if(action==='remove'){this.say('Tab removed · Data kept');this.retry.hidden=false;this.retry.textContent='Undo remove';this.retry.onclick=()=>void this.arrangement('restore',id);}}
 /** Add to Tab…: Add (it stays here too, the default) or Move this view instead (it leaves this Tab), to any Tab of this Project, closed ones
  * marked, or to a new Tab named after it. */
 private sendDialog(root:HTMLElement){
  const owner=this.tabOwner();
  const from=root.dataset.workspaceView??this.layout.active,title=(root.getAttribute('aria-label')||root.querySelector('h3')?.textContent||'this window').trim();
  const d=this.dialog(`Add “${title}” to`);d.classList.add('workspace-send-dialog');
  // Add to Tab… (Will, 2026-10-23): adding is the default, since it stays here too; moving is the second choice.
  let copy=true;
  const mode=el('div','workspace-send-mode');mode.setAttribute('role','group');mode.setAttribute('aria-label','Add or move');
  const hint=el('p','workspace-send-hint');
  const moveChoice=button('Move this view instead',()=>{copy=false;sync();}),showChoice=button('Add',()=>{copy=true;sync();});
  const sync=()=>{moveChoice.setAttribute('aria-pressed',String(!copy));showChoice.setAttribute('aria-pressed',String(copy));hint.textContent=copy?'It stays in this Tab too.':'It leaves this Tab.';};
  mode.append(showChoice,moveChoice);sync();
  const list=el('div','workspace-send-list');
  for(const v of this.layout.views.filter(v=>v.id!==from))list.append(button(this.openIds.includes(v.id)?v.title:`${v.title} (closed)`,()=>{d.close();void this.send(root,{to:v.id},copy,owner);}));
  const fresh=button('+ New Tab',()=>{d.close();void this.send(root,{title},copy,owner);});fresh.className='workspace-send-new';list.append(fresh);
  d.append(mode,hint,list);showChoice.focus();
 }
 /** What the Tab row offers under a point: a Tab, or a new Tab (+, or the row's free space beside the Tabs). */
 private dropTarget(x:number,y:number):{kind:'tab';id:string;el:HTMLElement}|{kind:'new';el:HTMLElement}|null{
  const hit=document.elementFromPoint(x,y) as HTMLElement|null;if(!hit||!this.strip.contains(hit))return null;
  const tab=hit.closest<HTMLElement>('.workspace-view-tab'),id=tab?.querySelector<HTMLElement>('[data-workspace-view-tab]')?.dataset.workspaceViewTab;
  if(tab&&id)return {kind:'tab',id,el:tab};
  const add=hit.closest<HTMLElement>('.workspace-new-tab');if(add)return {kind:'new',el:add};
  if(hit===this.tabs||hit===this.strip)return {kind:'new',el:this.strip.querySelector<HTMLElement>('.workspace-new-tab')??this.tabs};
  return null;
 }
 private showDrop(target:ReturnType<WorkspaceViews['dropTarget']>,x:number,y:number,words:(target:NonNullable<ReturnType<WorkspaceViews['dropTarget']>>)=>string|null){
  const text=target?words(target):null;
  if(this.dropShown!==(text?target!.el:null)){this.dropShown?.classList.remove('drop-target');this.dropShown=text?target!.el:null;this.dropShown?.classList.add('drop-target');}
  this.dragHint.hidden=!text;if(!text)return;
  this.dragHint.textContent=text;this.dragHint.style.left=`${x+14}px`;this.dragHint.style.top=`${y+16}px`;
 }
 private clearDrop(){this.dropShown?.classList.remove('drop-target');this.dropShown=null;this.dragHint.hidden=true;}
 private headerDrag(e:PointerEvent){
  if(e.button!==0)return;const hit=e.target as HTMLElement|null;if(!hit||hit.closest('button,a,input,select,textarea'))return;
  const root=hit.closest<HTMLElement>('[data-placement-id][data-workspace-view]');
  if(!root||root.dataset.workspaceUnsaved==='true'||!this.roots().includes(root))return;
  const bar=hit.closest('header,.fwin-bar');if(!bar||!root.contains(bar))return;
  const start=this.rect(root),from=root.dataset.workspaceView,session=this.session,generation=this.generation;
  const title=(root.getAttribute('aria-label')||root.querySelector('h3')?.textContent||'Window').trim();
  const words=(alt:boolean)=>(t:NonNullable<ReturnType<WorkspaceViews['dropTarget']>>)=>t.kind==='new'?'+ New Tab':t.id===from?null:`${alt?'Add to':'Move to'} ${this.layout.views.find(v=>v.id===t.id)?.title??'Tab'}`;
  this.dragAbort?.abort();const controller=new AbortController(),options={signal:controller.signal,capture:true};this.dragAbort=controller;
  window.addEventListener('blur',()=>{controller.abort();this.clearDrop();},{signal:controller.signal});
  document.addEventListener('pointermove',m=>{if(m.pointerId===e.pointerId)this.showDrop(this.dropTarget(m.clientX,m.clientY),m.clientX,m.clientY,words(m.altKey));},options);
  document.addEventListener('pointercancel',c=>{if(c.pointerId===e.pointerId){controller.abort();this.clearDrop();}},options);
  document.addEventListener('pointerup',u=>{
   if(u.pointerId!==e.pointerId)return;controller.abort();
   const target=this.dropTarget(u.clientX,u.clientY);this.clearDrop();
   if(!target||(target.kind==='tab'&&target.id===from)||session!==this.session||generation!==this.generation)return;
   if(this.busy()){this.flash('This Tab is still opening; try again in a moment');return;}
   // It leaves from where it was, not from where the drag left it on its way to the Tab row.
   this.changing=true;try{this.place(root,start);}finally{this.changing=false;}
   void this.send(root,target.kind==='tab'?{to:target.id}:{title},u.altKey);
  },options);
 }
 /** Who opens a Data card dropped on the Tab row (the studio's Data panel), given what the card carried. */
 acceptEntryDrops(handler:(data:string,to:{tab:string}|{fresh:true})=>void){this.entryDrop=handler;}
 private entryDragOver(e:DragEvent){
  if(!this.entryDrop||!e.dataTransfer?.types.includes(ENTRY_DRAG))return;
  const target=this.dropTarget(e.clientX,e.clientY);
  this.showDrop(target,e.clientX,e.clientY,t=>t.kind==='new'?'Open in a new Tab':`Open in ${this.layout.views.find(v=>v.id===t.id)?.title??'Tab'}`);
  if(target){e.preventDefault();e.dataTransfer.dropEffect='copy';}
 }
 private dropEntry(e:DragEvent){
  const data=e.dataTransfer?.getData(ENTRY_DRAG),target=this.dropTarget(e.clientX,e.clientY);this.clearDrop();
  if(!data||!target||!this.entryDrop)return;e.preventDefault();
  this.entryDrop(data,target.kind==='tab'?{tab:target.id}:{fresh:true});
 }
 /** Sends a window and says what happened, only once the arrangement's save was accepted ("not saved yet" otherwise). */
 private async send(root:HTMLElement,target:{to?:string;title?:string},copy:boolean,owner=this.tabOwner()){
  if(!this.ownsTabChoice(owner))return;
  if(this.busy()){this.flash('This Tab is still opening; try again in a moment');return;}
  const from=root.dataset.workspaceView;if(!from)return;
  const current=()=>this.ownsTabChoice(owner)&&from===this.layout.active&&!this.changing&&!this.navigating&&!this.openingInput
    &&root.dataset.project===owner.session&&this.roots().includes(root);
  if(!current()||root.dataset.workspaceUnsaved==='true')return;
  const {session,generation}=owner;
  // A new Tab is named after what it holds, kept short: a result's first words ("Bright regions"), not its full line.
  const to=target.to??await this.addView(tabName(target.title??''),false);if(!to||!current())return;
  // Undo may restore this move's comparison lanes, but never overwrite a later comparison edit.
  const beforeMove=!copy?structuredClone(this.layout.views.find(v=>v.id===from)!):undefined;
  const afterMove=beforeMove?structuredClone(beforeMove):undefined;
  if(afterMove){afterMove.items=afterMove.items.filter(i=>i.placementId!==root.dataset.placementId);pruneLanes(afterMove);}
  const placed=await this.move(root,to,copy);if(!placed||session!==this.session||generation!==this.generation)return;
  const name=this.layout.views.find(v=>v.id===to)?.title??'the Tab',saved=!this.dirty&&!this.conflict;
  const here=()=>session===this.session&&generation===this.generation;
  // Undo says what it did: back, or why not, and whether that was saved.
  const undo=async()=>{
   if(!here())return;
   if(this.busy()){this.flash('This Tab is still opening; try Undo again in a moment',[['Undo',()=>void undo()]]);return;}
   if(!copy&&!root.isConnected){this.flash('This Tab was redrawn since; use Add to Tab… to move it back');return;}
   this.capture();
   if(copy){const v=this.layout.views.find(v=>v.id===to);if(!v||!v.items.some(i=>i.placementId===placed)&&!v.notes.some(n=>n.placementId===placed)){this.opts.notice('That placement changed. Nothing was undone.');return;}
    v.items=v.items.filter(i=>i.placementId!==placed);v.notes=v.notes.filter(n=>n.placementId!==placed);pruneLanes(v);this.loaded.delete(to);}
   else {if(!this.relocate(placed,to,from,false)){this.opts.notice('That placement changed. Nothing was undone.');return;}
    const restored=this.layout.views.find(v=>v.id===from);
    if(restored?.kind==='compare'&&beforeMove?.compare&&JSON.stringify(restored.compare)===JSON.stringify(afterMove?.compare)){
     restored.compare=structuredClone(beforeMove.compare);pruneLanes(restored);
    }
    root.dataset.workspaceView=from;this.visibility();}
   this.updateTransfer(root);this.changed();await this.flush();
   if(!this.ownsTabChoice(owner))return;
   this.flash(`${copy?`No longer shown in ${name}`:'Moved back'}${this.dirty||this.conflict?' · not saved yet':''}`);
  };
  this.flash(`${copy?'Added to':'Moved to'} ${name}${saved?'':' · not saved yet'}`,[['Go to',()=>{if(here())void this.goTo(to,owner); }],['Undo',()=>void undo()]]);
 }
 private flash(text:string,actions:[string,()=>void][]=[]){
  this.placeToasts();
  clearTimeout(this.flashTimer);this.flashNote.replaceChildren(text);
  for(const [label,act] of actions){const b=button(label,()=>{this.clearFlash();act();});b.className='workspace-flash-action';this.flashNote.append(b);}
  this.flashNote.hidden=false;this.flashTimer=window.setTimeout(()=>this.clearFlash(),10000);
 }
 private clearFlash(){clearTimeout(this.flashTimer);this.flashNote.hidden=true;this.flashNote.replaceChildren();}
 /** One window's placement from one Tab to another, as data: a move keeps its placement id, a copy gets a new one. */
 private relocate(pid:string|undefined,fromId:string,toId:string,copy:boolean):string|null{
  const source=this.layout.views.find(v=>v.id===fromId),target=this.layout.views.find(v=>v.id===toId);
  if(!pid||!source||!target||source===target)return null;
  const note=source.notes.find(n=>n.placementId===pid),item=source.items.find(n=>n.placementId===pid),next=copy?uuid():pid;
  if(note){target.notes.push({...structuredClone(note),placementId:next});if(!copy)source.notes=source.notes.filter(n=>n!==note);}
  else if(item){target.items.push({...structuredClone(item),placementId:next});if(!copy)source.items=source.items.filter(n=>n!==item);}
  else return null;
  if(!copy)pruneLanes(source);
  return next;
 }
 private async move(root:HTMLElement,to:string,copy:boolean):Promise<string|null>{
  if(this.busy())return null;this.capture();const source=this.active(),target=this.layout.views.find(v=>v.id===to);if(!source||!target||source===target)return null;
  const placed=this.relocate(root.dataset.placementId,source.id,to,copy);
  if(!placed){this.opts.notice('Retain this item before moving it.');return null;}
  if(!copy)root.dataset.workspaceView=to;
  this.loaded.delete(to);this.updateTransfer(root);this.changed();await this.flush();return placed;
 }
 private place(root:HTMLElement,rect:Rect){
  if(root.matches('.workspace-window')&&root.dataset.studyRetained){const saved=JSON.parse(root.dataset.studyRetained) as StudyRetainedView,actual=this.opts.captureItems().find(v=>v.placementId===root.dataset.placementId);this.opts.placeItem({...saved,...(actual?{zoom:actual.zoom,pan:actual.pan}:{}),coordinates:'stage',position:rect.slice(0,2),size:rect.slice(2)});}
  else studyArtifacts().place(root,rect);
 }
 private arrange(){this.capture();const roots=this.visibleRoots(),v=this.active();if(!v)return;const before=structuredClone(v);const rects=tileRects(roots.length+Number(v.specimen.visible),this.stage.clientWidth,this.stage.clientHeight-60);let i=0;if(v.specimen.visible){v.specimen.rect=rects[i++];this.applySpecimen();}this.changing=true;try{for(const root of roots)this.place(root,rects[i++]);}finally{this.changing=false;}for(const root of roots)delete root.dataset.savedRect;this.capture();this.undo={before,arranged:structuredClone(v)};this.changed();}
 private fit(){this.capture();const roots=this.visibleRoots(),rects=tileRects(roots.length,this.stage.clientWidth,this.stage.clientHeight-60);this.changing=true;try{roots.forEach((root,i)=>{root.dataset.savedRect=JSON.stringify(this.rect(root));this.place(root,rects[i]);});}finally{this.changing=false;}this.opts.notice('Windows fitted temporarily. Saved desktop positions are unchanged. Use Arrange to save a new arrangement.');}
 locations(id:string){return this.layout.views.filter(v=>v.items.some(i=>i.resource.id===id)||v.notes.some(n=>n.id===id)).map(v=>v.title);}
 /** The Tabs showing a saved result at this revision (or a note), each with whether it is open: Data's "In" chips. */
 places(id:string,version?:number|null){return this.layout.views.filter(v=>v.items.some(i=>i.resource.id===id&&(version===undefined||version===null||(i.resource.version??0)===version))||v.notes.some(n=>n.id===id)).map(v=>({id:v.id,title:v.title,open:this.openIds.includes(v.id)}));}
 /** A chooser belongs to this Project opening, even if it closes and later returns. */
 tabOwner(){return {session:this.session,generation:this.generation};}
 ownsTabChoice(owner:{session:string;generation:number}){return owner.session===this.session&&owner.generation===this.generation;}
 /** Named agent actions share the manual controller and acknowledge only accepted saves. */
 async agentCommand(session:string,args:Record<string,any>,original=()=>true):Promise<Record<string,unknown>>{
  const owner=this.tabOwner(),check=()=>{if(!original()||session!==owner.session||!this.ownsTabChoice(owner))throw new Error('The Project changed. Ask again in its workspace.');};
  // Researcher and Study Tabs are reported beside the work Tabs; while one is in front, no work Tab is the selected one.
  const report=()=>{const research=this.opts.research?.report()??[],front=research.some(t=>t.selected);return {ok:true,session:this.session,studioVisible:!document.hidden,tabs:[
   ...this.layout.views.filter(v=>args.action!=='list'||!args.tab||v.id===args.tab).map(v=>({id:v.id,title:v.title,open:this.openIds.includes(v.id),selected:!front&&v.id===this.layout.active,kind:v.kind??'work',omittedItems:Math.max(0,v.items.length-(args.tab?64:8)),items:v.items.slice(0,args.tab?64:8).map(i=>({placement:i.placementId,resource:i.resource,title:i.title}))})),
   ...research.filter(t=>args.action!=='list'||!args.tab||t.id===args.tab).map(t=>({...t,omittedItems:0,items:[]}))]};};
  try{
   check();if(this.busy()||this.navigating||this.changing||document.querySelector('dialog:modal'))throw new Error('Finish the current workspace action first.');
   if(args.action==='list')return report();
   if(typeof args.tab==='string'&&/^(research|study|draft):/.test(args.tab)){
    // A researcher's or Study's Tab: it can be put in front or closed (closing stops nothing; it stays in Researchers).
    const tab=(this.opts.research?.report()??[]).find(t=>t.id===args.tab);if(!tab||!this.opts.research)throw new Error('That exact Tab is no longer available. List Tabs again.');
    if(args.action==='switch'){if(!await this.opts.research.switch(args.tab))throw new Error('The researcher Tab did not open. List Tabs again.');check();return report();}
    if(args.action==='close'){if(!this.opts.research.close(args.tab))throw new Error('The researcher Tab could not close.');check();return report();}
    throw new Error('A researcher’s Tab can only be shown or closed; its work is in Researchers.');
   }
   this.capture();await this.flush();check();if(this.dirty||this.conflict)throw new Error('The arrangement is not saved. Use Retry save or Review recovery.');
   if(this.busy()||document.querySelector('dialog:modal'))throw new Error('The workspace changed while saving. Finish its current action and ask again.');
   const target=this.layout.views.find(v=>v.id===args.tab);
   if(args.action!=='create'&&!target)throw new Error('That exact Tab is no longer available. List Tabs again.');
   if(args.action==='create'){
    const id=await this.addView(String(args.title).trim().slice(0,80));check();if(!id)throw new Error('No Tab was created. Check the workspace.');
   }else if(args.action==='rename'){
    target!.title=String(args.title).trim().slice(0,80);this.changed();this.render();
   }else if(args.action==='switch'){
    // The same way a click goes: a researcher's or Study's page in the Stage's place is left first.
    if(this.opts.leaveResearch&&!this.opts.leaveResearch())throw new Error('Finish or discard the Study form first, then ask again.');
    if(!await this.goTo(target!.id,owner))throw new Error('The target Tab did not open. Check its input and saved work.');
   }else if(args.action==='close'){
    if(this.isHome(target!.id))throw new Error('Main stays open; it holds the live input.');
    await this.closeTab(target!.id);check();if(this.openIds.includes(target!.id))throw new Error('The Tab could not close. Save or finish its work first.');
   }else if(args.action==='move'){
    const source=this.layout.views.find(v=>v.id===args.from);
    if(!source||!source.items.some(i=>i.placementId===args.placement)&&!source.notes.some(i=>i.placementId===args.placement))throw new Error('That exact placement is no longer in the named source Tab.');
    if(source.id!==target!.id){
     if(!this.relocate(args.placement,source.id,target!.id,false))throw new Error('The placement could not move.');
     for(const root of this.roots())if(root.dataset.placementId===args.placement)root.dataset.workspaceView=target!.id;
     if(target!.id===this.layout.active){const moved=target!.items.find(i=>i.placementId===args.placement);if(moved)this.completionRestores.add(args.placement);}
     this.loaded.delete(target!.id);this.changed();this.visibility();
    }
   }else if(args.action==='add'){
    const item=args.item as StudyRetainedView;
    if(!item?.resource||item.resource.day!==session||!item.resource.id||target!.items.length>=64)throw new Error('Choose a retained resource in this Project and a Tab with room.');
    if(!target!.items.some(i=>sameStudyResource(i.resource,item.resource)&&i.presentation===item.presentation)){
     const added={...structuredClone(item),placementId:uuid()};target!.items.push(added);
     if(target!.id===this.layout.active)this.completionRestores.add(added.placementId);
     this.loaded.delete(target!.id);this.changed();
    }
   }else throw new Error('This Tab action is not supported.');
   await this.flush();check();if(this.dirty||this.conflict)throw new Error('The change is not confirmed saved. Check Retry save or Review recovery.');
   if(['add','move'].includes(args.action)&&target!.id===this.layout.active){
    if(!await this.activate(target!.id,true))throw new Error('The placement was saved but its Tab could not reopen. Reopen it from Data.');check();
   }
   this.render();return report();
  }catch(error){return {ok:false,error:error instanceof Error?error.message:String(error)};}
 }
 /** One navigation row; virtual researcher tabs never enter the saved work layout. */
 researchHost(){const owner=this.tabOwner();return {row:this.tabs,project:this.session,activeWorkId:this.layout.active,homeWorkId:(this.layout.views.find(v=>this.isHome(v.id))?.id)??'',current:()=>this.ownsTabChoice(owner),refit:()=>this.refit(),
  activateWork:async(id:string)=>{try{return await this.goTo(id,owner);}catch(error){if(this.ownsTabChoice(owner))this.opts.notice(error);return false;}},reorderWork:(ids:string[])=>{
   if(!this.ownsTabChoice(owner)||this.busy()||ids.length!==new Set(ids).size)return;
   const known=this.layout.views.filter(v=>ids.includes(v.id));if(known.length!==ids.length)return;
   const replacements=ids.map(id=>this.layout.views.find(v=>v.id===id)!);let at=0;
   this.layout.views=this.layout.views.map(view=>ids.includes(view.id)?replacements[at++]:view);
   this.changed();this.render();
  }};
 }
 async newRunOrigin(run:string,title:string,session:string){
  const owner=this.tabOwner();if(session!==owner.session||this.busy())throw new Error('Finish opening this Project before running.');
  const id=await this.addView(title,false);
  if(!id||!this.ownsTabChoice(owner)||this.busy())throw new Error('The result Tab did not open. No analysis was started.');
  await this.activate(id,false,false,false,true);
  if(!this.ownsTabChoice(owner)||this.layout.active!==id||this.busy())throw new Error('The result Tab did not open. No analysis was started.');
  await this.flush();
  if(!this.ownsTabChoice(owner)||this.layout.active!==id||this.busy()||this.dirty||this.conflict)throw new Error('The result Tab is not confirmed saved. No analysis was started.');
  return this.origin(run)??undefined;
 }
 /** Exact accepted active arrangement for comparison controls; never a pending draft. */
 comparisonState(){const view=this.active();return !this.busy()&&!this.dirty&&!this.conflict&&view?{session:this.session,tab:view.id,revision:this.revision,view:structuredClone(view)}:null;}
 async createComparison(selections:import('./workspace-compare').CompareSelection[],owner=this.tabOwner()):Promise<void>{
  if(!this.ownsTabChoice(owner)||this.busy())throw new Error('Return to the original Project before comparing.');
  if(selections.length<2||selections.length>3||new Set(selections.map(s=>s.resource.id)).size!==selections.length||
   selections.some(s=>s.resource.day!==owner.session||!s.capture||s.capture!==selections[0].capture))throw new Error('Choose two or three retained results from the same captured input.');
  const blocked=studyArtifacts().blockReason(this.session);if(blocked)throw new Error(blocked);
  const id=await this.addView('Compare methods',false);
  if(!id||!this.ownsTabChoice(owner)||this.busy())throw new Error('The comparison Tab could not be created. Check its saved arrangement.');
  const view=this.layout.views.find(v=>v.id===id)!;
  view.specimen.visible=false;
  const bounds=this.stage.getBoundingClientRect(),rectangles=tileRects(selections.length,bounds.width,Math.max(200,bounds.height-60));
  view.items=selections.map((s,index)=>({resource:structuredClone(s.resource),title:s.title,presentation:'masks',placementId:uuid(),zoom:1,pan:[.5,.5],position:[rectangles[index][0],rectangles[index][1]+60],size:rectangles[index].slice(2)}));
  setTabKind(this.layout,id,{kind:'compare',compare:{sync:true,lanes:view.items.map((item,index)=>({placementId:item.placementId!,label:(['A','B','C'] as const)[index],impl:selections[index].implementation}))}});
  this.loaded.delete(id);this.changed();await this.flush();
  if(!this.ownsTabChoice(owner)||this.dirty||this.conflict)throw new Error('The comparison arrangement is not confirmed saved.');
  if(!await this.goTo(id,owner))throw new Error('The comparison is saved. Reopen its Tab to inspect it.');
 }
 async setComparisonSync(sync:boolean,owner=this.tabOwner()):Promise<void>{
  const view=this.active(),tab=view?.id;
  if(!view?.compare||!this.ownsTabChoice(owner)||this.busy())throw new Error('Open the saved comparison first.');
  this.capture();await this.flush();
  if(!this.ownsTabChoice(owner)||this.busy()||this.layout.active!==tab||this.dirty||this.conflict)throw new Error('The comparison changed. Try again in its Tab.');
  const current=this.active();if(!current?.compare)throw new Error('The comparison lanes changed.');
  current.compare.sync=sync;this.changed();await this.flush();
  if(!this.ownsTabChoice(owner)||this.dirty||this.conflict)throw new Error('The comparison setting is not confirmed saved.');
  window.dispatchEvent(new CustomEvent('workspace-tab-context'));
 }
 /** Every Tab of this Project, for choosing where a Data result opens (Data's Open in Tab…). */
 tabChoices(){return this.layout.views.map(v=>({id:v.id,title:v.title,kind:v.kind??'work',current:v.id===this.layout.active&&this.openIds.includes(v.id)}));}
 /** Puts the captured Project's Tab in view; interrupted navigation never becomes another Project's success. */
 async goTo(id:string,owner=this.tabOwner()):Promise<boolean>{
  if(!this.ownsTabChoice(owner)||this.navigating||this.changing||this.openingInput||!this.layout.views.some(v=>v.id===id))return false;
  if(this.layout.active===id&&this.openIds.includes(id))return true;
  if(!this.openIds.includes(id))this.openIds.push(id);
  await this.activate(id);
  return this.ownsTabChoice(owner)&&!this.navigating&&!this.changing&&!this.openingInput&&this.layout.active===id&&this.openIds.includes(id);
 }
 /** A new Tab in the captured Project, true only after that exact new Tab is in view. */
 async newTab(owner=this.tabOwner()):Promise<boolean>{
  if(!this.ownsTabChoice(owner)||this.navigating||this.changing||this.openingInput)return false;
  const target=await this.addView();
  return target!==undefined&&this.ownsTabChoice(owner)&&!this.navigating&&!this.changing&&!this.openingInput&&this.layout.active===target&&this.openIds.includes(target);
 }
 private resumeReveal(){
  const pending=this.queuedReveal;this.queuedReveal=undefined;
  if(pending&&pending.session===this.session&&pending.generation===this.generation&&pending.sequence===this.revealSequence){this.opts.notice('Saved tab opened.');void this.reveal(pending.id,pending.tab,pending.version,{session:pending.session,generation:pending.generation});}
 }
 async reveal(id:string,tab?:string,version?:number|null,owner=this.tabOwner()){
  if(!this.ownsTabChoice(owner))return;
  const sequence=++this.revealSequence;
  const matchesItem=(item:StudyRetainedView)=>item.resource.day===owner.session&&item.resource.id===id
    &&(version===undefined||(item.resource.version??0)===(version??0));
  const matches=(v:WorkspaceView)=>v.items.some(matchesItem)||v.notes.some(n=>n.id===id);
  const v=tab?this.layout.views.find(v=>v.id===tab&&matches(v))
    :this.layout.views.find(v=>v.id===this.layout.active&&matches(v))??this.layout.views.find(matches);
  if(!v)return;
  if(v.kind==='reader'){void this.goTo(v.id,owner);return;}
  if(this.navigating||this.changing){this.queuedReveal={id,tab,version,session:this.session,generation:this.generation,sequence};this.opts.notice('Opening the saved tab. Your selected window will be revealed when it is ready.');return;}
  const session=this.session,generation=this.generation;
  const item=v.items.find(matchesItem),placement=item?.placementId??v.notes.find(n=>n.id===id)?.placementId;
  const current=()=>sequence===this.revealSequence&&session===this.session&&generation===this.generation&&this.layout.active===v.id;
  if(!this.openIds.includes(v.id))this.openIds.push(v.id);
  try{
   await this.activate(v.id);if(!current()||!placement||this.navigating||this.changing)return;
   if(studyArtifacts().applyDocument(`document:${placement}`,{open:true,front:true}))return;
   if(!item)return;
   if(this.opts.frontItem(item))return;
   const recovered=await this.opts.restoreItems([{...item,coordinates:'stage'}]);
   if(!current())return;
   if(!recovered.recovered){this.opts.notice(recovered.missing.join('; '));return;}
   if(!studyArtifacts().applyDocument(`document:${placement}`,{open:true,front:true})&&!this.opts.frontItem(item))
    this.opts.notice('The saved window is not available yet. Retry Reveal from Project Data.');
  }catch(error){if(current())this.opts.notice(error);}
 }
 origin(run:string){return this.active()?{session:this.session,tab:this.layout.active,placementId:`pending-${run}`,run}:null;}
 /** A server-made results Tab of a researcher whose own Tab is in the row stays closed (under ⌄), so one researcher
  * never opens two Tabs. */
 private besideItsResearcher(view:WorkspaceView):boolean{const rid=(view.provenance as {researcher?:unknown}|undefined)?.researcher;return typeof rid==='string'&&!!this.opts.researcherTab?.(rid);}
 /** Review recovery shows on the row only for a copy that could hold work the saved layout lacks: made at or after the
  * revision this opening loaded, with an arrangement other than the saved one, and other than the one this opening
  * loaded (a copy kept as the app quit holds what was saved then: changing the layout after reopening is no reason to
  * offer it, Beta 0.4.0's clean-VM check). Older copies stay listed under ⌄ (Recovery copies) and are never deleted here. */
 private updateRecovery(){
  let saved='';try{saved=JSON.stringify(JSON.parse(this.lastSaved||'{}').views??null);}catch{saved='';}
  const relevant=this.recovery.list<any>(this.session,'layout').filter(copy=>{
   const revision=Number(copy.value?.revision),views=JSON.stringify(copy.value?.layout?.views??null);
   return revision>=this.loadedRevision&&views!==saved&&!(revision===this.loadedRevision&&views===this.loadedViews);
  });
  this.recover.hidden=relevant.length===0;
 }
 private reviewRecovery(){
  const project=this.session,generation=this.generation,d=this.dialog('Recover local arrangements');
  const current=()=>project===this.session&&generation===this.generation;
  d.append(el('p','workspace-recovery-intro','Choose the tabs to recover as additional arrangements. Saved Data stays in its Project. Closing this review keeps every recovery copy.'),
   button('Use saved layout, keep recovery',()=>{d.close();if(current())void this.load(project);}));
  for(const candidate of this.recovery.list<any>(project,'layout')){
   const layout=candidate.value.layout;if(!layout?.views)continue;
   const section=el('section','workspace-recovery-candidate'),selected=new Set<string>();
   section.append(el('h3','',`Local arrangement · ${candidate.at?new Date(candidate.at).toLocaleString():'earlier browser version'}`));
   for(const v of layout.views){
    const label=el('label'),check=el('input');check.type='checkbox';
    check.onchange=()=>check.checked?selected.add(v.id):selected.delete(v.id);
    label.append(check,document.createTextNode(`${v.title} · ${v.items.length} windows · ${v.notes.length} notes`));
    const detail=el('details'),list=el('ul','workspace-recovery-contents');
    for(const item of v.items)list.append(el('li','',`${item.title || 'Untitled window'}${item.resource?.version!=null?` · saved revision ${item.resource.version}`:''}`));
    for(const note of v.notes)list.append(el('li','',this.notes.get(note.id)?.value.title??'Saved note'));
    if(v.specimen?.visible)list.append(el('li','','Project specimen view'));
    detail.append(el('summary','','Review windows and notes'),list);section.append(label,detail);
   }
   const recover=button('Recover selected tabs',()=>{if(current())void this.recoverTabs(candidate,selected,d);else this.opts.notice('Return to the original Project and reopen recovery.');});
   const keep=button('Keep saved arrangement',()=>{if(this.recoveryBusy)return;if(current()){this.recovery.clear(candidate);d.close();void this.load(project);}});
   keep.title='Dismiss this local recovery copy; keep the saved Project arrangement and Data.';
   section.append(recover,keep);d.append(section);
  }
 }
 private async recoverTabs(candidate:Recovery<any>,selected:Set<string>,dialog:HTMLDialogElement){
  if(this.recoveryBusy)return;
  const project=this.session,generation=this.generation,current=()=>project===this.session&&generation===this.generation;
  const report=(message:string)=>{let status=dialog.querySelector<HTMLElement>('[role=alert]');if(!status){status=el('p');status.setAttribute('role','alert');dialog.append(status);}status.textContent=message;status.scrollIntoView({block:'nearest'});};
  if(!selected.size){report('Select the arrangements to recover.');return;}
  const chosen=new Set(selected);this.recoveryBusy=true;dialog.setAttribute('aria-busy','true');
  try{
   report('Recovering selected tabs…');
   const latest=await this.readLayout(project);
   if(!current())return;if(!latest.ok){report(latest.error);return;}
   const copies=candidate.value.layout.views.filter((v:WorkspaceView)=>chosen.has(v.id));
   if(latest.layout.views.length+copies.length>24){report('Not enough room. Your recovery is kept. Choose Use saved layout, keep recovery, then Remove an arrangement to free space.');return;}
   const recoveredIds:string[]=[];
   for(const old of copies){const v=structuredClone(old);v.id=uuid();recoveredIds.push(v.id);v.title=`${v.title} (recovered)`.slice(0,80);renewPlacements(v,uuid);for(const item of v.items){if(!LAYOUT_SCHEMAS.includes(candidate.value.layout.schema)){delete item.position;item.geometryUnavailable=true;}}latest.layout.views.push(v);}
   latest.layout.schema=Math.max(latest.layout.schema,candidate.value.layout.schema);
   const result=await this.opts.request('workspace-layout',{session:project,schema:this.schemaNow(),layout:latest.layout,base:latest.revision});
   if(!current())return;if(!result.ok){report(result.error);return;}
   if(copies.length===candidate.value.layout.views.length)this.recovery.clear(candidate);
   else this.recovery.replace(candidate,{...candidate.value,layout:{...candidate.value.layout,views:candidate.value.layout.views.filter((v:WorkspaceView)=>!chosen.has(v.id))}});
   dialog.close();await this.load(project);
   // load retains browser-local closed tabs. Explicit recovery must make its new
   // arrangements reachable without asking the researcher to find them again.
   if(this.session===project && this.generation===generation+1 && recoveredIds.every(id=>this.layout.views.some(v=>v.id===id))){
    this.openIds=[...new Set([...this.openIds,...recoveredIds])];
    await this.activate(recoveredIds[0]);this.render();
   }
  }catch(error){if(current())report(`Recovery was not confirmed. Your copy is kept. Check saved tabs before retrying. ${error instanceof Error?error.message:String(error)}`);}
  finally{this.recoveryBusy=false;dialog.setAttribute('aria-busy','false');}
 }
 private async newNote(){if(!this.active()||!this.openIds.includes(this.layout.active))return;const note:Note={id:'a'+uuid().replaceAll('-',''),title:'Untitled note',text:'',version:0};const state=new NoteState(note,this.recovery,this.session);state.edit(note.title,note.text);this.notes.set(note.id,state);void this.openNote(note.id);this.visibleRoots().find(root=>root.dataset.noteId===note.id)?.querySelector('textarea')?.focus();await this.saveNote(state);}
 async openNote(id:string,rect?:Rect,placementId?:string,options:StudyOpenOptions = {}):Promise<boolean>{
  if(!this.active())return false;const owner=this.session,tab=this.layout.active,generation=this.generation;let state=this.notes.get(id);
  if(!state){const r=await this.opts.request('session-note',{session:owner});if(this.session!==owner||this.layout.active!==tab||this.generation!==generation)return false;for(const note of r.notes??[])if(!this.notes.has(note.id))this.notes.set(note.id,new NoteState(note,this.recovery,owner));state=this.notes.get(id);}
  if(!state&&this.recovery.list(owner,id).length){state=new NoteState({id,title:'Unsaved note recovery',text:'',version:0},this.recovery,owner);state.status='error';state.error='Choose an earlier draft below. This note has not been saved.';this.notes.set(id,state);}
  if(!state)return false;
  const existing=this.visibleRoots().find(n=>placementId?n.dataset.placementId===placementId:n.dataset.noteId===id);if(existing){if(!options.background)existing.querySelector('textarea')?.focus();return true;}
  const note=state,content=el('div','workspace-note'),title=el('input'),text=el('textarea'),status=el('span','workspace-note-status'),conflict=el('section','workspace-note-conflict');title.maxLength=80;title.setAttribute('aria-label','Note title');text.maxLength=3000;text.setAttribute('aria-label','Note text');status.setAttribute('role','status');
  const save=()=>void this.saveNote(note),footer=el('div','workspace-note-footer'),meta=el('div','workspace-note-meta');meta.append(el('small','','Project note · shared in Data'),status);const saveButton=button('Save note',save);footer.append(meta,saveButton);content.append(title,text,footer,conflict);
  const render=()=>{if(title.value!==note.value.title)title.value=note.value.title;if(text.value!==note.value.text)text.value=note.value.text;{const line=noteStatusLine(note.status,note.error);status.textContent=line.text;status.dataset.state=note.status;saveButton.textContent=line.action;}conflict.replaceChildren();if(note.status==='conflict'){const saved=el('textarea');saved.readOnly=true;saved.value=note.saved.text;saved.setAttribute('aria-label','Saved note for comparison');conflict.append(el('strong','',`Saved revision ${note.saved.version}`),saved,button('Use saved',()=>note.useSaved()),button('Keep draft',()=>this.opts.notice('Your draft remains here. Edit it above, then choose Save merged note.')),button('Save merged note',()=>{note.merge();save();}));}};
  note.listeners.add(render);render();
  const root=studyArtifacts().document(`note:${id}:${placementId??''}`,note.value.title,content,()=>{note.listeners.delete(render);note.listeners.delete(metadata);note.listeners.delete(renderRecovery);},{noteId:id,placementId:placementId??uuid()},options);
  if(!root.contains(content)){note.listeners.delete(render);if(options.background)return true;if(rect){studyArtifacts().place(root,rect);root.dataset.savedRect=JSON.stringify(rect);}if(!options.background)root.querySelector('textarea')?.focus();return true;}
  root.querySelector('header button[aria-label^=Close]')?.addEventListener('click',save);
  const metadata=()=>{root.dataset.noteVersion=String(note.saved.version);root.dataset.noteState=note.status;root.setAttribute('aria-label',note.value.title);const heading=root.querySelector('header h3');if(heading){heading.textContent=note.value.title;(heading as HTMLElement).title=note.value.title;}};note.listeners.add(metadata);metadata();
  if(rect){studyArtifacts().place(root,rect);root.dataset.savedRect=JSON.stringify(rect);}
  const changed=()=>{note.edit(title.value,text.value);clearTimeout(this.noteTimers.get(id));if(note.status!=='conflict')this.noteTimers.set(id,window.setTimeout(()=>{this.noteTimers.delete(id);if(this.session===owner)save();},600));};title.oninput=changed;text.oninput=changed;
  const details=el('details');const renderRecovery=()=>{const candidates=this.recovery.list(owner,id).filter(c=>!c.key.endsWith(`:${this.recovery.writer}`));details.hidden=!candidates.length;details.replaceChildren(el('summary','','Recover earlier drafts'));for(const candidate of candidates){details.append(el('pre','',`${candidate.value.title}\n${candidate.value.text}`),button('Load this draft',()=>note.recover(candidate)));}};note.listeners.add(renderRecovery);renderRecovery();content.append(details);
  if(!this.changing){this.capture();this.changed();}return true;
 }
 private async saveNote(note:NoteState){const session=note.session;await note.save(sent=>this.opts.request('session-note',{session,action:'save',id:sent.id,base:sent.version,title:sent.title,text:sent.text}));if(note.status==='dirty'&&session===this.session)this.noteTimers.set(note.value.id,window.setTimeout(()=>{this.noteTimers.delete(note.value.id);void this.saveNote(note);},600));for(const root of this.roots().filter(n=>n.dataset.noteId===note.value.id)){const h=root.querySelector('header h3');if(h){h.textContent=note.value.title;(h as HTMLElement).title=note.value.title;}root.setAttribute('aria-label',note.value.title);root.querySelector('header button[aria-label^=Close]')?.setAttribute('aria-label',`Close ${note.value.title}`);}}
}
