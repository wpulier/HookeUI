import type {DataTableOwner} from './datatable-view';
import {sameStudyResource,type StudyRetainedView,type StudyResourceReference} from './workspace-study-restoration';
import type {WorkspaceView} from './workspace-views-model';
import {openStudyDataTable} from './datatable-presentation';
import './workspace-compare.css';

export interface CompareSelection {resource:StudyResourceReference;title:string;implementation:string;inputKey:string}
export interface CompareState {session:string;tab:string;revision:number;view:WorkspaceView}
export interface WorkspaceCompareDeps {
 owner():DataTableOwner;current(owner:DataTableOwner):boolean;state():CompareState|null;
 create(selections:CompareSelection[],owner:DataTableOwner):Promise<void>;
 setSync(sync:boolean,owner:DataTableOwner):Promise<void>;
}
interface Candidate {id:string;day:string;kind:string;version:number;title:string;state?:string;madeBy?:{implementation?:string};source?:{comparisonInput?:string|null;frame?:number;seq?:number;boot?:string}}
let dependencies:WorkspaceCompareDeps|null=null,dispose:()=>void=()=>{};
const button=(title:string,action:()=>void)=>{const b=document.createElement('button');b.type='button';b.textContent=title;b.onclick=action;return b;};
const words=(error:unknown)=>error instanceof Error?error.message:String(error);
export function comparisonCandidates(entries:Candidate[],saved:StudyRetainedView):CompareSelection[]{
 const origin=entries.find(e=>e.id===saved.resource.id&&e.day===saved.resource.day&&e.version===saved.resource.version);
 if(!origin||origin.kind!=='population'||origin.state==='unavailable'||!origin.madeBy?.implementation||!origin.source?.comparisonInput)throw new Error('This retained population revision is unavailable. Reopen it from Data.');
 const inputKey=origin.source.comparisonInput;
 return entries.filter(e=>e.kind==='population'&&e.day===origin.day&&e.state!=='unavailable'&&Number.isSafeInteger(e.version)&&e.version>=0&&
  e.source?.comparisonInput===inputKey&&typeof e.madeBy?.implementation==='string'&&!!e.madeBy.implementation)
  .map(e=>({resource:{day:e.day,id:e.id,version:e.version,picture:null},title:e.title,implementation:e.madeBy!.implementation!,inputKey}));
}
/** Ordinary retained-result selection; no algorithm runs and no current/live frame is substituted. */
export async function chooseComparison(saved:StudyRetainedView):Promise<void>{
 const deps=dependencies;if(!deps)throw new Error('Open the Project workspace first.');
 const owner=deps.owner(),tab=document.body.dataset.activeWorkspaceView;
 const current=()=>deps.current(owner)&&owner.session===saved.resource.day&&document.body.dataset.activeWorkspaceView===tab;
 if(!current())throw new Error('Return to the retained result’s Project and Tab.');
 const response=await fetch(`/api/resources?${new URLSearchParams({day:owner.session,kind:'population'})}`,{cache:'no-store'});
 const reply=await response.json();if(!response.ok)throw new Error(reply.error||'Retained results could not be read.');
 if(!current())return;
 const candidates=comparisonCandidates(reply.entries??[],saved);
 if(candidates.length<2)throw new Error('Keep at least two segmentation results from this exact captured input before comparing.');
 if(candidates.length>200)throw new Error('More than 200 results share this capture. Open a smaller saved comparison from Tabs.');
 const dialog=document.createElement('dialog'),heading=document.createElement('h2'),note=document.createElement('p'),choices=document.createElement('fieldset');
 heading.textContent='Compare retained results';note.textContent='Choose 2 or 3 results of this captured input. This opens saved masks; it does not rerun segmentation.';
 const selected=new Set([saved.resource.id]);
 for(const candidate of candidates){const row=document.createElement('label'),check=document.createElement('input');check.type='checkbox';check.checked=selected.has(candidate.resource.id);check.onchange=()=>{
  if(check.checked&&selected.size>=3){check.checked=false;status.textContent='Choose at most three results.';return;}
  if(check.checked)selected.add(candidate.resource.id);else selected.delete(candidate.resource.id);
 };row.append(check,document.createTextNode(`${candidate.title} · ${candidate.implementation}`));choices.append(row);}
 const status=document.createElement('p');status.setAttribute('role','status');
 const open=button('Open Compare Tab',()=>{if(!current()){status.textContent='The Project or Tab changed. Close and choose results there.';return;}
  if(selected.size<2){status.textContent='Choose at least two results.';return;}open.disabled=true;
  void deps.create(candidates.filter(c=>selected.has(c.resource.id)),owner).then(()=>dialog.close()).catch(error=>{status.textContent=words(error);}).finally(()=>{open.disabled=false;});});
 dialog.className='workspace-compare-picker';dialog.append(heading,note,choices,status,open,button('Cancel',()=>dialog.close()));
 dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();
}

export interface CompareViewport {x:number;y:number;width:number;height:number}
export function zoomComparison(view:CompareViewport,frame:{width:number;height:number},factor:number,at:[number,number]):CompareViewport{
 const width=Math.max(frame.width/16,Math.min(frame.width,view.width*factor)),height=width*frame.height/frame.width;
 return {x:view.x+(view.width-width)*at[0],y:view.y+(view.height-height)*at[1],width,height};
}
/** One latest viewport per frame; disposal cannot apply an old gesture to a new owner. */
export class CompareDrawQueue<T> {
 private frame:number|null=null;
 private latest:{source:T;view:CompareViewport}|null=null;
 private epoch=0;
 private readonly draw:(source:T,view:CompareViewport)=>void;
 private readonly schedule:(callback:FrameRequestCallback)=>number;
 private readonly cancelFrame:(id:number)=>void;
 constructor(draw:(source:T,view:CompareViewport)=>void,
  schedule:(callback:FrameRequestCallback)=>number=callback=>requestAnimationFrame(callback),
  cancelFrame:(id:number)=>void=id=>cancelAnimationFrame(id)){this.draw=draw;this.schedule=schedule;this.cancelFrame=cancelFrame;}
 push(source:T,view:CompareViewport):void{this.latest={source,view:{...view}};if(this.frame===null){const epoch=this.epoch;this.frame=this.schedule(()=>{if(epoch!==this.epoch)return;this.frame=null;const pending=this.latest;this.latest=null;if(pending)this.draw(pending.source,pending.view);});}}
 pending(source:T):CompareViewport|null{return this.latest?.source===source?{...this.latest.view}:null;}
 cancel():void{this.epoch++;if(this.frame!==null)this.cancelFrame(this.frame);this.frame=null;this.latest=null;}
}

/** A late accepted table remains in its original Data; opening is a separate captured-view decision. */
export async function publishComparisonTable(chosen:CompareState,current:()=>boolean,open:(saved:StudyRetainedView)=>Promise<void>=openStudyDataTable):Promise<{saved:boolean;shown:boolean}>{
 if(!current())throw new Error('The comparison changed. Open its saved Tab again.');
 const response=await fetch('/api/datatables/comparison',{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'datatable'},body:JSON.stringify({session:chosen.session,tab:chosen.tab,layoutRevision:chosen.revision,title:chosen.view.title})});
 const result=await response.json();if(!response.ok)throw new Error(result.error||'Comparison table was not saved.');
 if(typeof result.id!=='string'||!/^tb[1-9][0-9]*$/.test(result.id)||!Number.isSafeInteger(result.version)||result.version<1)
  throw new Error('The saved table reference is unavailable. Inspect this Project’s Data before retrying.');
 if(!current())return {saved:true,shown:false};
 await open({resource:{day:chosen.session,id:result.id,version:result.version,picture:null},title:result.title,zoom:1,pan:[.5,.5]});
 return {saved:true,shown:true};
}

/** Bind only the exact retained mask placement surfaces already owned by the active Compare Tab. */
export function configureWorkspaceCompare(deps:WorkspaceCompareDeps):void{
 dispose();dependencies=deps;
 const toolbar=document.createElement('section');toolbar.className='workspace-compare-toolbar';toolbar.hidden=true;
 const title=document.createElement('strong'),sync=document.createElement('input'),label=document.createElement('label'),status=document.createElement('span');sync.type='checkbox';label.append(sync,document.createTextNode('Sync pan and zoom'));status.setAttribute('role','status');
 let owner=deps.owner(),state:CompareState|null=null,serial=0,signature='',unbind:()=>void=()=>{};
 const current=()=>!!state&&deps.current(owner)&&deps.state()?.tab===state.tab&&document.body.dataset.workspaceProject===state.session&&document.body.dataset.activeWorkspaceView===state.tab;
 const reset=button('Reset view',()=>{if(!current())return;draws.cancel();const targets=surfaces(),initial=targets[0]?.dataset.compareOriginal;if(initial)for(const svg of targets)svg.setAttribute('viewBox',initial);});
 const send=button('Send comparison to table',()=>{void publish();});
 toolbar.append(title,label,reset,send,status);document.getElementById('stage')!.append(toolbar);
 const surfaces=()=>state?.view.compare?.lanes.flatMap(lane=>{
  const root=[...document.querySelectorAll<HTMLElement>('.study-artifact[data-placement-id]')].find(r=>r.dataset.project===state!.session&&r.dataset.workspaceView===state!.tab&&r.dataset.placementId===lane.placementId);
  const item=state!.view.items.find(i=>i.placementId===lane.placementId);if(!root||!item||root.hidden||root.dataset.dismissed==='true')return [];
  let retained:StudyRetainedView;try{retained=JSON.parse(root.dataset.studyRetained??'null');}catch{return [];}
  if(!retained||!sameStudyResource(retained.resource,item.resource))return [];
  const svg=root.querySelector<SVGSVGElement>('.study-mask-artifact svg');return svg?[svg]:[];
 })??[];
 const draws=new CompareDrawQueue<SVGSVGElement>((source,view)=>{if(!current()||!surfaces().includes(source))return;const targets=state!.view.compare?.sync?surfaces():[source];for(const svg of targets)svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.width} ${view.height}`);});
 const apply=(source:SVGSVGElement,view:CompareViewport)=>{if(current())draws.push(source,view);};
 const read=(svg:SVGSVGElement)=>{const pending=draws.pending(svg);if(pending)return pending;const [x,y,width,height]=(svg.getAttribute('viewBox')??'').split(/\s+/).map(Number);return {x,y,width,height};};
 const bind=()=>{
  unbind();const cleanup:(()=>void)[]=[];
  for(const svg of surfaces()){
   svg.dataset.compareOriginal??=svg.getAttribute('viewBox')??'';
   const values=svg.dataset.compareOriginal.split(/\s+/).map(Number),frame={width:values[2],height:values[3]};
   if(!frame.width||!frame.height)continue;
   const wheel=(event:WheelEvent)=>{if(!current())return;event.preventDefault();const box=svg.getBoundingClientRect();if(!box.width||!box.height)return;apply(svg,zoomComparison(read(svg),frame,event.deltaY>0?1.15:1/1.15,[(event.clientX-box.left)/box.width,(event.clientY-box.top)/box.height]));};
   let drag:{x:number;y:number;view:CompareViewport}|null=null;
   const down=(event:PointerEvent)=>{if(!current()||event.button!==0||!(event.shiftKey||event.target===svg||event.target instanceof SVGImageElement))return;drag={x:event.clientX,y:event.clientY,view:read(svg)};svg.setPointerCapture(event.pointerId);event.preventDefault();};
   const move=(event:PointerEvent)=>{if(!drag||!current())return;const box=svg.getBoundingClientRect();if(box.width&&box.height)apply(svg,{...drag.view,x:drag.view.x-(event.clientX-drag.x)*drag.view.width/box.width,y:drag.view.y-(event.clientY-drag.y)*drag.view.height/box.height});};
   const up=()=>{drag=null;};
   svg.addEventListener('wheel',wheel,{passive:false});svg.addEventListener('pointerdown',down);svg.addEventListener('pointermove',move);svg.addEventListener('pointerup',up);svg.addEventListener('pointercancel',up);
   cleanup.push(()=>{svg.removeEventListener('wheel',wheel);svg.removeEventListener('pointerdown',down);svg.removeEventListener('pointermove',move);svg.removeEventListener('pointerup',up);svg.removeEventListener('pointercancel',up);});
  }
  if(state?.view.compare?.sync){const targets=surfaces(),initial=targets[0]?.getAttribute('viewBox');if(initial)for(const svg of targets.slice(1))svg.setAttribute('viewBox',initial);}
  unbind=()=>{draws.cancel();cleanup.forEach(fn=>fn());};
 };
 const update=()=>{const next=deps.state();if(!next?.view.compare){state=null;toolbar.hidden=true;signature='';unbind();return;}
  const nextSignature=JSON.stringify([next.session,next.tab,next.view.compare,next.view.items.map(i=>[i.placementId,i.resource])]);
  state=next;owner=deps.owner();toolbar.hidden=false;sync.checked=next.view.compare.sync;title.textContent=next.view.compare.lanes.map(l=>`${l.label} · ${l.impl}`).join('   ');
  if(signature!==nextSignature){signature=nextSignature;serial++;status.textContent='Scroll to zoom; drag the background (or Shift-drag) to pan. Same objects, not independent samples.';bind();}
 };
 sync.onchange=()=>{if(!current())return;sync.disabled=true;void deps.setSync(sync.checked,owner).catch(error=>{status.textContent=words(error);}).finally(()=>{sync.disabled=false;update();});};
 async function publish(){
  const chosen=deps.state(),captured=deps.owner(),token=serial;if(!chosen||!current())return;
  send.disabled=true;status.textContent='Creating table from the saved comparison…';
  try{const result=await publishComparisonTable(chosen,()=>deps.current(captured)&&token===serial&&deps.state()?.tab===chosen.tab);
   if(result.shown)status.textContent='Comparison table saved. Method groups are not independent samples.';
  }catch(error){if(deps.current(captured)&&token===serial)status.textContent=words(error);}finally{send.disabled=false;}
 }
 const observer=new MutationObserver(()=>{if(state)bind();});observer.observe(document.getElementById('study-artifacts')??document.getElementById('stage')!,{childList:true,subtree:true});
 window.addEventListener('workspace-tab-context',update);update();
 dispose=()=>{unbind();observer.disconnect();window.removeEventListener('workspace-tab-context',update);toolbar.remove();};
}
