import type {ResearchSession} from './protocol';
import {asking,chipStatus,isWorking,pillState,researcherCell,todaysResearch} from './shelf';
import {litCell,setLitCell,type LitCellState} from './lit-cell';
import {stopRequested} from './research-stop';
import {tabMark} from './tab-marks';
import {researchShows,type ResearchShows} from './workspace-model';

/** Virtual researchers share the work Tab row but never enter the server layout or its 24-Tab cap. */
type Host={current:()=>boolean;row:HTMLElement;project:string;activeWorkId:string;homeWorkId?:string;activateWork:(id:string)=>Promise<boolean>;reorderWork:(ids:string[])=>void;refit?:()=>void};
/** A researcher's or Study's Tab the person closed: what to say, and where its copy flies from (workspace-views.ts researchClosed). */
export type ClosedTab={id:string;name:string;text:string;from:DOMRect|null;mark:Element|null;reopen:()=>void};
type Options={
 /** Closed researcher Tabs and the row's order, per Project: localStorage, so a closed Tab does not return on relaunch. */
 storage:Storage;
 /** Researchers opened this session (sessionStorage): they keep their Tab until closed. */
 session?:Storage;
 key:string;host:()=>Host;open:(row:ResearchSession)=>void;close:()=>void;openVirtual?:(id:string)=>void;
 /** Finished work not yet opened in this browser (shelf.ts isNew): its researcher gets a Tab until opened or closed. */
 isNew?:(row:ResearchSession)=>boolean;
 /** An ended researcher that is news (shelf.ts isNews: ready, or it stopped on its own, not opened yet), the strip's
  * rule; used in place of isNew when given, so a researcher that could not finish keeps its Tab beside its pill. */
 isNews?:(row:ResearchSession)=>boolean;
 /** The person closed a Tab: say so with Reopen and show where it went. */
 closed?:(tab:ClosedTab)=>void;
};
const researchId=(rid:string)=>`research:${rid}`;
const workId=(id:string)=>`work:${id}`;
/** A Tab of the row that is a view, not a saved Tab: an open Study (`study:<id>`) or the New researcher draft
 * (`draft:…`). Several coexist (Will, 2026-10-23: a starting draft and an open Study at once); `closable:false` holds
 * a Tab while its start is in flight. */
export type VirtualTab={id:string;name:string;line:string;kind?:string;closable?:boolean;closeTitle?:string};
const isVirtual=(id:string)=>id.startsWith('study:')||id.startsWith('draft:');
const STOP_TITLE='Asked to stop; it is Stopped once the server lists it so.';
/** A copy of a Tab's mark for the copy that flies when it closes (none where the page cannot copy nodes). */
const copyOf=(node:Element|null|undefined):Element|null=>node&&typeof node.cloneNode==='function'?node.cloneNode(true) as Element:null;
/** A researcher's lit cell: shelf.ts owns it, so the Tab and the Working strip's pill always say the same. */
export {researcherCell};
/** What it is working on, for the Stage bar and the Tab's tooltip. */
export const researcherObjective=(row:ResearchSession)=>(row.result?.title||row.label||row.brief||row.name).trim();
/** Which researchers have a Tab (the workspace-feel plan): those working or needing you, ended and not yet opened (its
 * work ready, or it stopped on its own: the strip's rule), or opened this session, unless the person closed theirs.
 * Everything else is in Researchers and under ⌄. */
export class WorkspaceResearchTabs {
 private options:Options;
 private rows:ResearchSession[]=[];
 private project='';
 private active='';
 /** Researcher Tabs the person closed in this Project (remembered across launches). */
 private hidden=new Set<string>();
 private opened=new Set<string>();
 private order:string[]=[];
 /** The reopen stack, newest last: work, researcher and virtual Tabs alike (⌘⇧T). */
 private closed:string[]=[];
 private signature='';
 private persisted='';
 private fitKey='';
 private hostRow?:HTMLElement;
 private authority?:Host;
 private painting=false;
 private primed=false;
 private states=new Map<string,LitCellState>();
 private settle=new Set<string>();
 private pills=new Map<string,HTMLElement>();
 private virtual=new Map<string,VirtualTab>();
 /** The virtual Tab on the Stage now (selected); null while a work Tab or a researcher is. */
 private shown:string|null=null;
 private closedVirtualTabs=new Map<string,VirtualTab>();
 constructor(options:Options){
  this.options=options;
  window.addEventListener('workspace-tabs-changed',()=>this.mount());
  for(const type of ['workspace-tab-closed','workspace-tab-reopened'])window.addEventListener(type,event=>{
   const detail=(event as CustomEvent<{session:string;kind:string;id:string}>).detail;
   if(detail?.session!==this.project||detail.kind!=='work'||typeof detail.id!=='string')return;
   const id=workId(detail.id);this.closed=this.closed.filter(value=>value!==id);if(type==='workspace-tab-closed')this.closed.push(id);
  });
 }
 update(project:string,sessions:ResearchSession[],active:string){
  const focus=(document.activeElement as HTMLElement|null)?.dataset?.researchFocus;
  const changed=project!==this.project;
  if(changed){this.virtual.clear();this.closedVirtualTabs.clear();this.shown=null;this.project=project;this.hidden=new Set(this.read(this.options.storage,''));this.order=this.read(this.options.storage,':order');this.opened=new Set(this.read(this.options.session,':opened'));this.closed=[];this.signature='';this.persisted='';this.primed=false;this.states.clear();}
  this.rows=project?todaysResearch(sessions,project).reverse():[];
  this.active=active;
  // Opening from the rail is an explicit reopen, including a previously closed researcher; it is then opened this session.
  if(active&&this.hidden.delete(active)){this.closed=this.closed.filter(id=>id!==researchId(active));this.persist();}
  if(active&&!this.opened.has(active)){this.opened.add(active);this.persistOpened();}
  // One settle when a researcher's Tab first lights (after this Project's first look) or its work turns ready.
  for(const row of this.tabbed()){
   const state=researcherCell(row),before=this.states.get(row.rid);
   if(this.primed&&(before===undefined||state==='ready'&&before!=='ready'))this.settle.add(row.rid);
   this.states.set(row.rid,state);
  }
  this.primed=true;
  // The signature names what a Tab shows, not whole rows: a researcher's step changes often and must not redraw the row.
  const signature=JSON.stringify([project,active,this.tabbed().map(row=>[row.rid,row.name,researcherObjective(row),chipStatus(row),researcherCell(row)]),[...this.virtual.values()],this.shown]);
  if(signature!==this.signature||!this.authority?.current()){this.signature=signature;this.render();}
  this.mount();
  if(focus)[...this.hostRow?.querySelectorAll<HTMLElement>('[data-research-focus]')??[]].find(node=>node.dataset.researchFocus===focus)?.focus();
 }
 /** The researchers that have a Tab in the row now. */
 private tabbed():ResearchSession[]{
  const news=this.options.isNews??this.options.isNew;
  return this.rows.filter(row=>!this.hidden.has(row.rid)&&(row.rid===this.active||isWorking(row)||!!asking(row)||this.opened.has(row.rid)||!!news?.(row)));
 }
 /** True while this researcher's own Tab is in the row. */
 has(rid:string):boolean{return this.tabbed().some(row=>row.rid===rid);}
 /** A virtual Tab is on the Stage (or, with `show` false, merely in the row): named for its Study or the draft and
  * selected while shown. Choosing another Tab leaves it in the row, a click brings it back, only its × takes it out. */
 setVirtual(tab:VirtualTab,show=true){
  const same=JSON.stringify(tab)===JSON.stringify(this.virtual.get(tab.id)),shownBefore=this.shown;
  this.virtual.set(tab.id,tab);this.closedVirtualTabs.delete(tab.id);this.closed=this.closed.filter(id=>id!==tab.id);
  if(show)this.shown=tab.id;
  if(same&&shownBefore===this.shown)return;
  this.signature='';this.render();this.mount();
 }
 /** The shown virtual Tab left the Stage (another Tab chosen): its Tab stays, not selected. */
 virtualLeft(){if(this.shown===null)return;this.shown=null;this.mount();}
 /** A virtual Tab goes without being closed by the person (the draft became a researcher). */
 removeVirtual(id:string){if(!this.virtual.delete(id))return;if(this.shown===id)this.shown=null;this.signature='';this.render();this.mount();}
 /** The researcher this draft started takes its Tab over: the Tab's node stays where it is and becomes the
  * researcher's (one orchestrated moment, nothing moves; `data-adopted` fades the new name in). */
 adopt(fromId:string,rid:string){
  const id=researchId(rid),pill=this.pills.get(fromId);
  this.virtual.delete(fromId);if(this.shown===fromId)this.shown=null;
  if(pill){this.pills.delete(fromId);this.pills.set(id,pill);pill.dataset.adopted='true';}
  this.order=this.order.map(value=>value===fromId?id:value);
  this.opened.add(rid);this.persistOpened();
  this.signature='';
 }
 /** Virtual Tabs closed by their ×, newest last, for All Tabs and Reopen. */
 closedVirtual():VirtualTab[]{return this.closed.filter(id=>this.closedVirtualTabs.has(id)).map(id=>this.closedVirtualTabs.get(id)!);}
 reopenVirtual(id:string):boolean{if(!this.closedVirtualTabs.has(id))return false;this.options.openVirtual?.(id);return true;}
 /** All Tabs (⌄): this Project's closed researcher Tabs (newest first), then closed Study and draft Tabs, each reopening. */
 closedTabs():{id:string;name:string;title:string;mark:Element;reopen:()=>void}[]{
  const researchers=[...this.rows].reverse().filter(row=>this.hidden.has(row.rid)).map(row=>({id:researchId(row.rid),name:row.name,title:`${researcherObjective(row)} · ${chipStatus(row)}`,mark:litCell(researcherCell(row),{decorative:true}) as Element,reopen:()=>this.select(researchId(row.rid))}));
  const views=[...this.closedVirtual()].reverse().map(tab=>({id:tab.id,name:tab.name,title:[tab.kind??'Study',tab.name,tab.line].filter(Boolean).join(' · '),mark:(tab.id.startsWith('study:')?tabMark('study'):litCell('idle',{decorative:true})) as Element,reopen:()=>{this.reopenVirtual(tab.id);}}));
  return [...researchers,...views];
 }
 /** The Assistant's Tab report: every researcher and view Tab, open or closed, and which one is in front. */
 report():{id:string;title:string;open:boolean;selected:boolean;kind:'researcher'|'study'|'draft';shows:ResearchShows[]}[]{
  const selected=this.selectedId(),open=new Set(this.tabbed().map(row=>row.rid));
  // What each one shows (workspace-model.ts): a researcher's conversation and the work it made, by kind; a Study's
  // question, steps and write-up; a draft's form.
  return [
   ...this.rows.filter(row=>open.has(row.rid)||this.hidden.has(row.rid)).map(row=>({id:researchId(row.rid),title:`${row.name} · ${researcherObjective(row)}`,open:open.has(row.rid),selected:selected===researchId(row.rid),kind:'researcher' as const,shows:researchShows(row.outputs,12,row.rid)})),
   ...[...this.virtual.values()].map(tab=>({id:tab.id,title:tab.name,open:true,selected:selected===tab.id,kind:tab.id.startsWith('study:')?'study' as const:'draft' as const,
    shows:tab.id.startsWith('study:')?[{kind:'document' as const,title:'The Study: its question, steps and write-up'}]:[{kind:'document' as const,title:'A new researcher’s form'}]})),
  ];
 }
 /** The research or view Tab in front, or '' while a work Tab is. */
 selectedId():string{return this.shown&&this.virtual.has(this.shown)?this.shown:this.active&&!this.hidden.has(this.active)?researchId(this.active):'';}
 /** Put a researcher's or view Tab in front, as a click on it would (the Assistant's switch). */
 show(id:string):boolean{
  if(id.startsWith('research:')?!this.rows.some(row=>researchId(row.rid)===id):!this.virtual.has(id))return false;
  this.select(id);return true;
 }
 /** Close a researcher's or view Tab, as its × would (the Assistant's close). */
 closeTab(id:string):boolean{
  if(this.virtual.has(id)){if(this.virtual.get(id)!.closable===false)return false;this.closeVirtual(id);return true;}
  const rid=id.startsWith('research:')?id.slice(9):'';if(!rid||!this.rows.some(row=>row.rid===rid))return false;
  if(!this.hidden.has(rid))this.hide(rid);return true;
 }
 private closeVirtual(id:string){
  const tab=this.virtual.get(id);if(!tab||tab.closable===false)return;
  const pill=this.pills.get(id),from=pill?.getBoundingClientRect?.()??null,mark=copyOf(pill?.querySelector('[role="tab"] > svg'));
  const shown=this.shown===id;this.virtual.delete(id);this.closedVirtualTabs.set(id,tab);
  this.closed=this.closed.filter(value=>value!==id);this.closed.push(id);
  if(shown){this.shown=null;this.options.close();}
  this.signature='';this.render();this.mount();
  this.options.closed?.({id,name:tab.name,text:id.startsWith('study:')?`Closed ${tab.name}. It stays in Researchers.`:`Closed ${tab.name}.`,from,mark,reopen:()=>{this.reopenVirtual(id);}});
 }
 private read(storage:Storage|undefined,suffix:string):string[]{
  try{const saved=JSON.parse(storage?.getItem(`${this.options.key}${suffix}:${this.project}`)??'[]');return Array.isArray(saved)?saved.filter((id):id is string=>typeof id==='string').slice(0,2048):[];}catch{return [];}
 }
 private persist(){const hidden=JSON.stringify([...this.hidden]),order=JSON.stringify(this.order),key=JSON.stringify([this.project,hidden,order]);if(key===this.persisted)return;try{
  this.options.storage.setItem(`${this.options.key}:${this.project}`,hidden);
  this.options.storage.setItem(`${this.options.key}:order:${this.project}`,order);this.persisted=key;
 }catch{/* Browser-local navigation only; no scientific save is implied. */}}
 private persistOpened(){try{this.options.session?.setItem(`${this.options.key}:opened:${this.project}`,JSON.stringify([...this.opened].slice(-512)));}catch{/* browser-local convenience only */}}
 /** Tabs are kept and updated in place (a researcher's step changes every few seconds); only new ones are built and
  * only gone ones removed, so the row never flickers and focus never jumps. */
 private render(){
  this.authority=this.options.host();
  // Looked up when a click runs, never captured here: a Tab built before the work row loaded keeps its closures, and
  // the row's load replaces the host token (workspace-views.ts researchHost), so a captured one would refuse forever.
  const valid=()=>{const host=this.options.host();return host.current()&&host.project===this.project;};
  const keep=new Set<string>();
  for(const row of this.tabbed()){
   const id=researchId(row.rid);keep.add(id);
   let pill=this.pills.get(id);if(!pill){pill=document.createElement('div');this.pills.set(id,pill);}
   this.drawResearcher(pill,row,valid);
  }
  for(const tab of this.virtual.values()){
   keep.add(tab.id);
   let pill=this.pills.get(tab.id);if(!pill){pill=document.createElement('div');this.pills.set(tab.id,pill);}
   this.drawVirtual(pill,tab,valid);
  }
  for(const [id,pill] of [...this.pills])if(!keep.has(id)){pill.remove();this.pills.delete(id);}
  this.settle.clear();
 }
 /** A researcher's Tab: its lit cell and its name ("Researcher 3"); what it is doing is the Tab's tooltip. Its × is
  * always shown (closing stops nothing). */
 private drawResearcher(wrap:HTMLElement,row:ResearchSession,valid:()=>boolean){
  const rid=row.rid,name=row.name,objective=researcherObjective(row),status=chipStatus(row),state=researcherCell(row);
  wrap.className='workspace-view-tab';wrap.dataset.tabKind='researcher';wrap.dataset.state=pillState(row);
  let tab=wrap.querySelector<HTMLButtonElement>('[role="tab"]');
  // A Tab's build is named by data-kind, not found by the mark it draws, so a later mark keeps the update in place; a
  // Tab built for another kind (a draft handed over by adopt) is rebuilt once.
  if(!tab||wrap.dataset.kind!=='researcher-tab'){
   wrap.replaceChildren();wrap.dataset.kind='researcher-tab';
   tab=this.button('',()=>{const current=this.rows.find(item=>item.rid===rid);if(current&&valid())this.select(researchId(rid));});tab.setAttribute('role','tab');tab.setAttribute('aria-controls','workspace-research-panel');
   tab.append(litCell(state,{decorative:true}),Object.assign(document.createElement('span'),{className:'workspace-tab-label'}));
   const close=this.button('',()=>{if(valid())this.hide(rid);});close.className='workspace-tab-x';close.append(tabMark('x'));wrap.append(tab,close);
  }
  tab.dataset.researchFocus=rid;tab.dataset.mixedTab=researchId(rid);
  tab.title=stopRequested(row)?`${objective} · ${STOP_TITLE}`:`${objective} · ${status}`;tab.setAttribute('aria-label',`${name}: ${objective}. ${status}`);
  const cell=tab.querySelector<SVGSVGElement>('[data-kind="lit-cell"]');if(cell)setLitCell(cell,state,{decorative:true,settle:this.settle.has(rid)});
  wrap.querySelector('.workspace-tab-label')!.textContent=name;
  const close=wrap.querySelector<HTMLButtonElement>('.workspace-tab-x')!;close.setAttribute('aria-label',`Close Tab ${name}`);
  close.title=isWorking(row)?'Close this Tab. The researcher keeps working; it stays in Researchers.':'Close this Tab. It stays in Researchers, its results in Data.';close.dataset.researchFocus=`close:${rid}`;close.disabled=false;
 }
 /** A Study's or the New researcher draft's Tab: the Study mark (or the draft's quiet cell) and its name; its state is
  * the tooltip. */
 private drawVirtual(wrap:HTMLElement,tab:VirtualTab,valid:()=>boolean){
  const id=tab.id,study=id.startsWith('study:');
  wrap.className='workspace-view-tab';wrap.dataset.tabKind=study?'study':'draft';delete wrap.dataset.adopted;delete wrap.dataset.state;
  let button=wrap.querySelector<HTMLButtonElement>('[role="tab"]');
  if(!button||wrap.dataset.kind!=='view-tab'){
   wrap.replaceChildren();wrap.dataset.kind='view-tab';
   button=this.button('',()=>{if(valid())this.select(id);});button.setAttribute('role','tab');button.setAttribute('aria-controls','workspace-research-panel');
   button.append(study?tabMark('study'):litCell('idle',{decorative:true}),Object.assign(document.createElement('span'),{className:'workspace-tab-label'}));
   const close=this.button('',()=>{if(valid())this.closeVirtual(id);});close.className='workspace-tab-x';close.append(tabMark('x'));wrap.append(button,close);
  }
  const kindWord=tab.kind??'Study';
  button.dataset.mixedTab=id;button.dataset.researchFocus=id;button.title=[kindWord,tab.name,tab.line].filter(Boolean).join(' · ');button.setAttribute('aria-label',button.title);
  wrap.querySelector('.workspace-tab-label')!.textContent=tab.name;
  const close=wrap.querySelector<HTMLButtonElement>('.workspace-tab-x')!;close.disabled=tab.closable===false;close.dataset.researchFocus=`close-${id}`;
  close.setAttribute('aria-label',`Close Tab ${tab.name}`);
  close.title=tab.closeTitle??(study?'Close this Tab. The study keeps going; it stays in Researchers.':'Close this Tab.');
 }
 private controls(){return this.hostRow?[...this.hostRow.querySelectorAll<HTMLButtonElement>('[role="tab"]')]:[];}
 private identity(tab:HTMLElement){return tab.dataset.mixedTab??workId(tab.dataset.workspaceViewTab??'');}
 private home(host:Host,work:HTMLElement[]){return host.homeWorkId?workId(host.homeWorkId):work[0]?workId(work[0].dataset.workspaceViewTab!):'';}
 private mount(){
  if(this.painting)return;
  const host=this.options.host();if(!this.project||host.project!==this.project||!host.current())return;
  if(this.authority&&!this.authority.current())this.render();
  this.painting=true;
  try{
   if(this.hostRow!==host.row){this.hostRow?.removeEventListener('keydown',this.keydown,true);this.hostRow=host.row;host.row.addEventListener('keydown',this.keydown,true);}
   const work=[...host.row.querySelectorAll<HTMLButtonElement>('[data-workspace-view-tab]')];
   // While the row is still opening (no work Tabs yet) nothing is ordered or saved, so Main never lands after researchers.
   if(!work.length)return;
   const nodes=new Map(work.map(tab=>[workId(tab.dataset.workspaceViewTab!),tab.parentElement!]));
   for(const [id,pill] of this.pills)nodes.set(id,pill);
   const workIds=work.map(tab=>workId(tab.dataset.workspaceViewTab!)),home=this.home(host,work);
   const known=new Set([...workIds,...this.tabbed().map(row=>researchId(row.rid)),...this.virtual.keys()]);
   // One order for every Tab, work or virtual, kept per browser: Main first; a new work Tab after the work Tabs, a new
   // researcher or view Tab at the end, where it was made.
   this.order=this.order.filter((id,i,all)=>(id.startsWith('work:')||known.has(id))&&all.indexOf(id)===i);
   for(const id of known){
    if(this.order.includes(id))continue;
    if(id.startsWith('work:')){const last=this.order.reduce((at,value,i)=>value.startsWith('work:')&&nodes.has(value)?i:at,-1);this.order.splice(last+1,0,id);}
    else this.order.push(id);
   }
   if(home&&this.order[0]!==home)this.order=[home,...this.order.filter(id=>id!==home)];
   const wanted=this.order.flatMap(id=>{const node=nodes.get(id);return node?[node]:[];});
   // A Tab this owner no longer knows (one handed over by adopt, a stale render) never lingers in the row.
   const mine=new Set(this.pills.values());
   for(const child of [...host.row.children] as HTMLElement[])if(['researcher-tab','view-tab'].includes(child.dataset.kind??'')&&!mine.has(child))child.remove();
   let moved=false;
   if(wanted.length!==host.row.children.length||wanted.some((node,i)=>host.row.children[i]!==node)){moved=true;for(const node of wanted)host.row.append(node);}
   this.persist();
   const selected=this.selectedId()||workId(host.activeWorkId);
   for(const tab of this.controls()){const active=this.identity(tab)===selected;tab.setAttribute('aria-selected',String(active));tab.tabIndex=active?0:-1;}
   const fitKey=JSON.stringify([this.order,selected,this.signature]);
   if(moved||fitKey!==this.fitKey){this.fitKey=fitKey;host.refit?.();}
  }finally{this.painting=false;}
 }
 private hide(rid:string){
  const row=this.rows.find(item=>item.rid===rid);if(!row)return;
  const pill=this.pills.get(researchId(rid)),from=pill?.getBoundingClientRect?.()??null,mark=copyOf(pill?.querySelector('[data-kind="lit-cell"]'));
  this.hidden.add(rid);this.closed=this.closed.filter(id=>id!==researchId(rid));this.closed.push(researchId(rid));if(this.active===rid){this.active='';this.options.close();}this.persist();this.signature='';this.update(this.project,this.rows,this.active);
  const text=asking(row)?`Closed ${row.name}. It needs you, in Researchers.`:isWorking(row)?`Closed ${row.name}. It keeps working in Researchers.`:researcherCell(row)==='ready'||researcherCell(row)==='partial'?`Closed ${row.name}. Its findings stay in Researchers.`:`Closed ${row.name}. It stays in Researchers.`;
  this.options.closed?.({id:researchId(rid),name:row.name,text,from,mark,reopen:()=>this.select(researchId(rid))});
 }
 private select(id:string,host=this.options.host()){
  if(host.project!==this.project||!host.current())return;
  if(this.virtual.has(id)){if(this.shown!==id)this.options.openVirtual?.(id);return;}
  if(id.startsWith('research:')){const row=this.rows.find(row=>researchId(row.rid)===id);if(row){this.hidden.delete(row.rid);this.closed=this.closed.filter(value=>value!==id);this.persist();this.options.open(row);}return;}
  const project=this.project;void host.activateWork(id.slice(5)).then(ok=>{if(ok&&this.project===project&&host.current()){this.active='';this.options.close();this.mount();}});
 }
 /** Native menus and keyboard use the same mixed, browser-local order. */
 command(name:string):boolean{
  const host=this.options.host();if(host.project!==this.project||!host.current())return false;
  if(name==='close'&&this.shown&&this.virtual.has(this.shown)){this.closeVirtual(this.shown);return true;}
  if(name==='close'&&this.active){this.hide(this.active);return true;}
  if(name==='reopen'){
   while(this.closed.length){
    const id=this.closed[this.closed.length-1];
    if(id.startsWith('work:')){if(!this.controls().some(tab=>this.identity(tab)===id))return false;this.closed.pop();continue;}
    if(isVirtual(id)){this.closed.pop();if(this.reopenVirtual(id))return true;continue;}
    this.closed.pop();const rid=id.slice(9);if(this.hidden.has(rid)&&this.rows.some(row=>row.rid===rid)){this.select(id);return true;}
   }
   return false;
  }
  if(!['next','previous'].includes(name)&&!/^go:[1-9]$/.test(name))return false;
  const ids=this.controls().map(tab=>this.identity(tab));if(!ids.length)return false;
  const active=this.selectedId()||workId(host.activeWorkId),at=ids.indexOf(active);
  const next=name==='next'?(at+1)%ids.length:name==='previous'?(at-1+ids.length)%ids.length:Number(name.slice(3))===9?ids.length-1:Number(name.slice(3))-1;
  if(ids[next])this.select(ids[next]);return true;
 }
 private keydown=(event:KeyboardEvent)=>{
  if(!this.authority?.current())return;
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  // Arrows move among the Tabs in sight: one folded into ⌄ is hidden and cannot take focus (⌘1–9 and next/previous
  // still reach it, since selecting a Tab unfolds it).
  const tabs=this.controls().filter(node=>!node.closest<HTMLElement>('.workspace-view-tab')?.hidden),tab=tabs.find(node=>node===event.target||node.contains(event.target as Node));if(!tab)return;
  event.preventDefault();event.stopImmediatePropagation();
  const at=tabs.indexOf(tab),next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(at+(event.key==='ArrowLeft'?-1:1)+tabs.length)%tabs.length;
  if(event.altKey){
   // Alt+arrow moves a Tab within the row: never Main, and never before it (index 1 is as far left as a Tab goes).
   const host=this.options.host(),home=this.home(host,[...host.row.querySelectorAll<HTMLElement>('[data-workspace-view-tab]')]);
   const id=this.identity(tab),target=this.identity(tabs[next]);if(id===target||id===home||target===home)return;
   this.order=this.order.filter(value=>value!==id);const to=this.order.indexOf(target);this.order.splice(to+(next>at?1:0),0,id);this.persist();
   host.reorderWork(this.controls().map(tab=>this.identity(tab)).filter(id=>id.startsWith('work:')).sort((a,b)=>this.order.indexOf(a)-this.order.indexOf(b)).map(id=>id.slice(5)));this.mount();
   this.controls().find(node=>this.identity(node)===id)?.focus();
  }else{tabs.forEach((node,i)=>node.tabIndex=i===next?0:-1);tabs[next]?.focus();}
 };
 private button(text:string,click:()=>void){const b=document.createElement('button');b.type='button';b.textContent=text;b.addEventListener('click',click);return b;}
}
