import {makeEditableTable} from './datatable-presentation';
import type {DataTableMember} from './datatable-view';
import { studyArtifacts } from './workspace-study-artifacts';
import { colourOf, type PopulationWire } from './population';
import { MeasurementsPage, type MeasurementTable } from './measurements';
import type { StudyRetainedView, StudyOpenOptions } from './workspace-study-restoration';
import type { ComparisonView } from './model-comparison';
import { bindPopulationNative } from './population-native';

export function artifactPresentation(entry:{kind:string;table?:boolean}):'masks'|'table'|'datatable'|'native' {
  return entry.kind==='dataset-table'?'datatable':entry.kind==='population'?'masks':(entry.kind==='result-table'||entry.kind==='result'&&entry.table===true)?'table':'native';
}

type Overlay = PopulationWire & {session:string;revision:number;width:number;height:number};
/** The overlay to draw, and the members with no outline to draw (a tiny object saved before every member had one):
 * named in the panel's note, their measurements kept. A malformed outline is still refused. */
export function checkedOverlay(value: Overlay, saved: StudyRetainedView): Overlay & {undrawn:string[]} {
  const ref=saved.resource;
  if(value.session!==ref.day || value.id!==ref.id || value.revision!==ref.version)
    throw new Error('The retained result does not match this project and revision.');
  if(!Number.isSafeInteger(value.width)||value.width<1||!Number.isSafeInteger(value.height)||value.height<1||!Array.isArray(value.members)||value.count!==value.members.length)
    throw new Error('The retained mask geometry is unavailable.');
  if(value.members.some(m=>!Array.isArray(m.polygons)||m.polygons.some(p=>!Array.isArray(p)||p.length<3||p.some(v=>!Array.isArray(v)||v.length!==2||v.some(n=>!Number.isFinite(n))))))
    throw new Error('A retained mask contour is missing or invalid.');
  return {...value,undrawn:value.members.filter(m=>!m.polygons.length).map(m=>m.id)};
}
/** The panel's words for members it can't draw: how many, which (the first few), and that their measurements are kept. */
export function undrawnWords(undrawn:string[]):string {
  if(!undrawn.length)return '';
  const named=undrawn.slice(0,5).join(', ')+(undrawn.length>5?` and ${undrawn.length-5} more`:'');
  return ` · ${undrawn.length} too small to outline (${named}); their measurements are kept`;
}
const makeButton=(title:string, action:()=>void)=>{const b=document.createElement('button');b.textContent=title;b.addEventListener('click',action);return b;};
function register(saved:StudyRetainedView, content:HTMLElement, dispose:()=>void, options:StudyOpenOptions):HTMLElement {
  const kind=saved.presentation!,ref=saved.resource;
  const root=studyArtifacts().document(`${kind}:${ref.day}:${ref.id}:${ref.version}:${saved.placementId??""}`,`${saved.title} · ${kind==='masks'?'Masks':kind==='capture'?'Captured input':'Measurements'}`,content,dispose,{studyRetained:JSON.stringify(saved),...(saved.placementId?{placementId:saved.placementId}:{})},options);
  if(root.contains(content)){
    root.dataset.studyRetained=JSON.stringify({...saved,zoom:1,pan:[.5,.5]});
    studyArtifacts().restoreGeometry(root,saved);
    if(saved.geometryUnavailable){const note=document.createElement('p');note.className='study-note';note.textContent='Earlier position unavailable · shown in a fallback position. Move this window to save its position.';content.before(note);}
  }
  return root;
}
interface StudyTableOpenOptions extends StudyOpenOptions {
  /** Exact rows are visible in the current Project/Tab, not merely fetched. */
  shown?: (table: MeasurementTable) => void;
}
interface TablePresentation { table:MeasurementTable|null; shown?:StudyTableOpenOptions['shown'] }
const tablePresentations = new WeakMap<HTMLElement, TablePresentation>();
function acknowledgeTable(root:HTMLElement,state:TablePresentation):void {
  const table=state.table,shown=state.shown;
  if(!table||!shown)return;
  requestAnimationFrame(()=>{
    if(state.shown!==shown||state.table!==table)return;
    if(root.dataset.dismissed==='true'||root.dataset.workspaceHidden==='true'||
       root.dataset.project!==document.body.dataset.workspaceProject||
       root.dataset.workspaceView!==document.body.dataset.activeWorkspaceView||
       table.session!==document.body.dataset.workspaceProject||!root.checkVisibility()){
      delete state.shown;return;
    }
    // Recovery may have withdrawn rows; keep this opening's intent for its retry.
    if(!root.querySelector('table'))return;
    delete state.shown;
    const rows=root.querySelector('tbody')?.getBoundingClientRect(),box=root.getBoundingClientRect();
    if(!rows||Math.min(rows.right,box.right,window.innerWidth)<=Math.max(rows.left,box.left,0)||
       Math.min(rows.bottom,box.bottom,window.innerHeight)<=Math.max(rows.top,box.top,0))return;
    shown(table);
  });
}
export function openStudyTable(saved:StudyRetainedView, options:StudyTableOpenOptions = {}):void {
  saved={...saved,presentation:'table'};
  const content=document.createElement('section'),ref=saved.resource;
  let state:TablePresentation;
  const page=new MeasurementsPage(content,{compact:true,session:()=>ref.day,loaded:value=>{
    state.table=value;root.dataset.displayState='rendered';acknowledgeTable(root,state);
  },showOnView:()=>void openStudyMasks({...saved,placementId:crypto.randomUUID(),presentation:'masks'})});
  const root=register(saved,content,()=>page.hide(),options);
  state=tablePresentations.get(root)??{table:null};
  tablePresentations.set(root,state);
  if(options.shown&&!options.background)state.shown=options.shown;
  if(root.contains(content)){
    const creation=document.createElement('div'),status=document.createElement('span');creation.className='study-table-actions';status.setAttribute('role','status');
    const create=makeButton('Make editable table',()=>{create.disabled=true;status.textContent='Creating table…';void makeEditableTable(saved,root).then(()=>{status.textContent='Table saved in Data.';}).catch(error=>{status.textContent=error instanceof Error?error.message:String(error);}).finally(()=>{create.disabled=false;});});
    creation.append(create,status);root.append(creation);
    root.dataset.displayState='loading';
    void page.show(ref.id,{session:ref.day,revision:ref.version??0}).then(()=>{
      root.dataset.displayState=state.table?'rendered':'failed';
    });
  }
  // Reuse the exact placement/read; retries notify through the same loaded callback.
  // Presentation is checked after layout, so late reads cannot complete hidden guides.
  if(options.shown&&!options.background)acknowledgeTable(root,state);
}
interface MaskSelection {member:DataTableMember;selected:(member:DataTableMember)=>void}
const maskSelections=new WeakMap<HTMLElement,MaskSelection>();
const maskSelectors=new WeakMap<HTMLElement,(selection:MaskSelection)=>void>();
export async function openStudyMaskMember(member:DataTableMember,selected:(member:DataTableMember)=>void):Promise<void>{
 if(member.kind!=='population')throw new Error('This table does not have a retained mask mapping.');
 await openStudyMasks({resource:{day:member.session,id:member.id,version:member.revision,picture:null},title:member.title,zoom:1,pan:[.5,.5]}, {}, {member,selected});
}
/** A result's retained masks on the picture they were made on, as one SVG: the existing `/overlay` and `/picture` routes,
 * checked to be this exact revision. The masks window (`openStudyMasks`) and the Data reader (data-reader.ts) both draw
 * them with this, each into a container of its own. */
export async function drawRetainedMasks(ref:StudyRetainedView['resource'],signal?:AbortSignal):Promise<{data:Overlay&{undrawn:string[]};svg:SVGSVGElement;source:SVGImageElement}>{
  const query=new URLSearchParams({session:ref.day,revision:String(ref.version)});
  const response=await fetch(`/api/resources/${encodeURIComponent(ref.id)}/overlay?${query}`,{signal});
  const reply=await response.json();if(!response.ok)throw new Error(reply.error||'Retained masks are unavailable.');
  const data=checkedOverlay(reply,{resource:ref,title:'',zoom:1,pan:[.5,.5]});
  const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg') as SVGSVGElement;
  svg.setAttribute('viewBox',`0 0 ${data.width} ${data.height}`);svg.setAttribute('role','img');svg.setAttribute('aria-label',`${data.members.length} retained mask contours on their captured evidence`);
  const source=document.createElementNS(ns,'image') as SVGImageElement;source.setAttribute('width',String(data.width));source.setAttribute('height',String(data.height));
  source.setAttribute('href',`/api/resources/${encodeURIComponent(ref.id)}/picture?${new URLSearchParams({day:ref.day})}`);svg.append(source);
  for(const member of data.members){
    if(!member.polygons.length)continue;  // named in the note under them
    const path=document.createElementNS(ns,'path');path.dataset.member=member.id;
    path.setAttribute('d',member.polygons.map(poly=>`M${poly.map(p=>p.join(',')).join('L')}Z`).join(' '));
    path.setAttribute('fill',colourOf(member));path.setAttribute('fill-opacity','.32');path.setAttribute('fill-rule','evenodd');
    path.setAttribute('stroke',colourOf(member));path.setAttribute('stroke-width','1.2');path.setAttribute('vector-effect','non-scaling-stroke');
    const title=document.createElementNS(ns,'title');title.textContent=member.id;path.append(title);svg.append(path);
  }
  return {data,svg,source};
}
/** A written result's words at its exact revision (the existing detail route): the text window (`openStudyText`) and the
 * Data reader read it the same way. */
export async function readRetainedText(ref:StudyRetainedView['resource'],signal?:AbortSignal):Promise<string>{
  const response=await fetch(`/api/resources/${encodeURIComponent(ref.id)}?${new URLSearchParams({day:ref.day})}`,{signal});const result=await response.json();
  if(!response.ok)throw new Error(result.error||'Saved result unavailable.');
  if(result.entry?.day!==ref.day||result.entry?.id!==ref.id||(result.entry.version??null)!==ref.version)throw new Error('This exact saved revision is unavailable.');
  return result.entry.summary||result.detail?.text||'No written content was retained.';
}
export async function openStudyMasks(saved:StudyRetainedView, options:StudyOpenOptions = {}, selection?:MaskSelection):Promise<void> {
  saved={...saved,resource:{...saved.resource},presentation:'masks'};
  const content=document.createElement('section');content.className='study-mask-artifact';
  let releaseNative=()=>{};
  const controller=new AbortController(),root=register(saved,content,()=>{controller.abort();releaseNative();},options);
  if(selection){maskSelections.set(root,selection);maskSelectors.get(root)?.(selection);}
  if(!root.contains(content))return;
  const ref=saved.resource;
  const load=async()=>{
    root.dataset.displayState='loading';content.textContent='Loading retained masks…';
    try{
      const drawn=await drawRetainedMasks(ref,controller.signal);if(controller.signal.aborted)return;
      const {data,svg,source}=drawn;
      const controls=document.createElement('div');controls.className='study-mask-controls';
      const background=makeButton('On capture',()=>{const visible=source.getAttribute('visibility')!=='hidden';source.setAttribute('visibility',visible?'hidden':'visible');background.textContent=visible?'Masks only':'On capture';background.setAttribute('aria-pressed',String(!visible));const retained=JSON.parse(root.dataset.studyRetained!);retained.display={...retained.display,background:!visible};root.dataset.studyRetained=JSON.stringify(retained);window.dispatchEvent(new CustomEvent('workspace-layout-changed')); });background.setAttribute('aria-pressed',String(saved.display?.background!==false));if(saved.display?.background===false){source.setAttribute('visibility','hidden');background.textContent='Masks only';}
      controls.append(background,makeButton('Measurements',()=>openStudyTable({...saved,placementId:undefined,presentation:'table',position:undefined,size:undefined})));
      const note=document.createElement('p');note.className='study-note';note.textContent=`${data.members.length} retained masks · ${ref.id} · revision ${ref.version} · ${data.width} × ${data.height} px`;
      if(!data.members.length)note.textContent+=' · No regions detected';
      note.textContent+=undrawnWords(data.undrawn);
      source.addEventListener('error',()=>{source.setAttribute('visibility','hidden');background.disabled=true;background.textContent='Masks only';note.textContent+=' · Captured image unavailable';},{once:true});
      content.replaceChildren(controls,svg,note);root.dataset.displayState='rendered';
      const placement=root.dataset.placementId,tab=root.dataset.workspaceView;
      const selected=()=>{
        if(controller.signal.aborted||!root.isConnected||!root.checkVisibility()||root.hidden||
          root.dataset.dismissed==='true'||root.dataset.workspaceHidden==='true'||
          root.dataset.project!==ref.day||document.body.dataset.workspaceProject!==ref.day||document.body.dataset.workspaceOpening==='true'||
          root.dataset.workspaceView!==tab||document.body.dataset.activeWorkspaceView!==tab||
          root.dataset.placementId!==placement)return null;
        try{
          const retained:StudyRetainedView=JSON.parse(root.dataset.studyRetained??'null');
          if(!retained?.resource||retained.resource.day!==ref.day||retained.resource.id!==ref.id||
            retained.resource.version!==ref.version||retained.resource.picture!==ref.picture)return null;
        }catch{return null;}
        return data;
      };
      const choose=(member:string,notify:boolean)=>{
        if(!selected())return;
        const link=maskSelections.get(root);
        if(!link||link.member.session!==ref.day||link.member.id!==ref.id||link.member.revision!==ref.version)return;
        const found=data.members.find(row=>row.id===member);
        if(!found||!found.polygons.length){note.textContent+=' · This row has no retained contour to highlight.';return;}
        for(const path of svg.querySelectorAll<SVGPathElement>('path[data-member]')){
          const on=path.dataset.member===member;path.setAttribute('stroke-width',on?'3':'1.2');path.setAttribute('fill-opacity',on?'.65':'.32');path.setAttribute('aria-pressed',String(on));
          path.setAttribute('tabindex',on?'0':'-1'); // the selected cell is the one tab stop
        }
        if(notify)link.selected({...link.member,member});
      };
      maskSelectors.set(root,link=>choose(link.member.member,false));
      // One tab stop for every retained cell (a roving tabindex): the selected one, else the first; the arrow keys move
      // between cells in their order, Home and End to the ends, Enter or Space selects. Hundreds of cells were each a
      // tab stop before the rail, Data and the Assistant.
      const paths=[...svg.querySelectorAll<SVGPathElement>('path[data-member]')];
      paths.forEach((path,index)=>{
        path.setAttribute('role','button');path.setAttribute('tabindex',index===0?'0':'-1');path.setAttribute('aria-label',`Select retained cell ${path.dataset.member}`);
        path.addEventListener('click',()=>choose(path.dataset.member!,true));
        path.addEventListener('keydown',event=>{
          if(event.key==='Enter'||event.key===' '){event.preventDefault();choose(path.dataset.member!,true);return;}
          const step=event.key==='ArrowRight'||event.key==='ArrowDown'?1:event.key==='ArrowLeft'||event.key==='ArrowUp'?-1:0;
          const to=step?paths[(paths.indexOf(path)+step+paths.length)%paths.length]:event.key==='Home'?paths[0]:event.key==='End'?paths[paths.length-1]:null;
          if(!to)return;event.preventDefault();
          for(const other of paths)other.setAttribute('tabindex',other===to?'0':'-1');(to as unknown as HTMLElement).focus?.();
        });
      });
      const linked=maskSelections.get(root);if(linked)choose(linked.member.member,false);
      controls.append(makeButton('Compare retained results…',()=>{void import('./workspace-compare').then(async({chooseComparison})=>{if(selected())await chooseComparison(saved);}).catch(error=>{note.textContent=error instanceof Error?error.message:String(error);});}));
      const native=bindPopulationNative(controls,selected,async target=>{
        const {openStudyScientific}=await import('./scientific-document');
        if(!selected())return;
        await openStudyScientific({resource:{day:target.session,id:target.id,version:target.revision,picture:null},title:target.title,zoom:1,pan:[.5,.5]});
      });native.sync();
      const ownership=new MutationObserver(()=>native.sync());
      // Visibility and Project/Tab identity can change without destroying the saved window.
      for(let ancestor:HTMLElement|null=root;ancestor;ancestor=ancestor.parentElement)
        ownership.observe(ancestor,{attributes:true,attributeFilter:['hidden','data-dismissed','data-workspace-hidden',
          'data-project','data-workspace-project','data-workspace-opening','data-workspace-view','data-active-workspace-view',
          'data-placement-id','data-study-retained']});
      releaseNative=()=>{ownership.disconnect();native.sync();};
    }catch(error){if(controller.signal.aborted)return;root.dataset.displayState='failed';content.replaceChildren(document.createTextNode(error instanceof Error?error.message:String(error)),makeButton('Retry',()=>void load()));}
  };
  await load();
}

export async function openStudyCapture(saved:StudyRetainedView, options:StudyOpenOptions = {}):Promise<void>{
 saved={...saved,presentation:'capture'};const content=document.createElement('section');const root=register(saved,content,()=>{},options);if(!root.contains(content))return;
 const image=document.createElement('img');image.alt='Captured analysis input';image.style.cssText='width:100%;height:calc(100% - 40px);object-fit:contain';
 root.dataset.displayState='loading';image.onload=()=>{root.dataset.displayState='rendered';};image.onerror=()=>{root.dataset.displayState='failed';content.replaceChildren(document.createTextNode('Captured input unavailable.'),makeButton('Retry',()=>{image.src=image.src;content.replaceChildren(image);}));};
 image.src=`/api/resources/${encodeURIComponent(saved.resource.id)}/picture?${new URLSearchParams({day:saved.resource.day})}`;
 const note=document.createElement('p');note.textContent=`Captured input · ${saved.resource.id} · revision ${saved.resource.version}. Analysis input is unchanged.`;content.append(image,note);
}

/** Written result records keep their own resource identity and never retarget the input. */
export async function openStudyText(saved:StudyRetainedView, options:StudyOpenOptions = {}):Promise<void>{
 const content=document.createElement('section'),controller=new AbortController();
 const root=studyArtifacts().document(`text:${saved.resource.day}:${saved.resource.id}:${saved.placementId??''}`,saved.title,content,()=>controller.abort(),{studyRetained:JSON.stringify(saved),...(saved.placementId?{placementId:saved.placementId}:{})},options);
 if(!root.contains(content))return;studyArtifacts().restoreGeometry(root,saved);
 const load=async()=>{root.dataset.displayState='loading';content.textContent='Loading saved result…';try{
  const words=await readRetainedText(saved.resource,controller.signal);
  const text=document.createElement('p');text.textContent=words;text.style.cssText='white-space:pre-wrap;padding:16px;line-height:1.6';content.replaceChildren(text);root.dataset.displayState='rendered';
 }catch(error){if(controller.signal.aborted)return;root.dataset.displayState='failed';content.replaceChildren(document.createTextNode(error instanceof Error?error.message:'Saved result unavailable.'),makeButton('Retry',()=>void load()));}};await load();
}

/** A comparison on the Stage (Will, 2026-10-20): the shared captured picture once, each compared model's retained masks
 * over it in that model's colour (tools-catalog.ts COMPARE_COLOURS, used nowhere else), a legend that names each model
 * with its count and hides or shows its marks, the line of facts on why the counts differ, and Ask the Assistant which
 * to trust. It reads the exact retained revisions; it runs nothing and changes no default. */
export async function openStudyComparison(view: ComparisonView, ask?: (question: string) => void, options: StudyOpenOptions = {}): Promise<void> {
  const content = document.createElement('section'); content.className = 'study-comparison';
  const controller = new AbortController();
  const title = `Compare models · ${view.task}`;
  // Not part of the saved arrangement (its Results are, each in Data): never restored, and opening it saves nothing.
  const root = studyArtifacts().document(`compare:${view.session}:${view.key}`, title, content, () => controller.abort(), { workspaceUnsaved: 'true' }, options);
  if (!root.contains(content)) return; // already open: brought to the front
  // Large enough to tell the models' marks apart: most of the Stage, never past it.
  studyArtifacts().place(root, [24, 24, Math.min(760, Math.max(420, innerWidth * 0.45)), Math.min(680, Math.max(420, innerHeight * 0.72))]);
  const arms = view.arms.filter(arm => arm.population && !arm.failed);
  const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('role', 'img'); svg.setAttribute('aria-label', `${arms.map(a => `${a.letter}. ${a.name}: ${a.count ?? '?'}`).join(', ')} on the same picture`);
  const legend = document.createElement('div'); legend.className = 'study-comparison-legend';
  const note = document.createElement('p'); note.className = 'study-comparison-why'; note.textContent = view.line;
  const status = document.createElement('p'); status.className = 'study-note'; status.setAttribute('role', 'status');
  const next = document.createElement('div'); next.className = 'study-comparison-next';
  if (ask) next.append(makeButton('Ask the Assistant which to trust', () => ask(view.question)));
  content.replaceChildren(legend, svg, note, next, status);
  for (const arm of view.arms) {
    const row = document.createElement('div'); row.className = 'study-comparison-arm'; row.dataset.impl = arm.impl;
    const mark = document.createElement('span'); mark.className = 'comparison-swatch'; mark.textContent = arm.letter; mark.style.background = arm.colour;
    const name = document.createElement('span'); name.className = 'study-comparison-name'; name.textContent = arm.name;
    const count = document.createElement('span'); count.className = 'study-comparison-count'; count.textContent = arm.failed ? `failed: ${arm.failed}` : `${arm.count ?? '?'} found`;
    row.append(mark, name, count);
    if (arm.population && !arm.failed) {
      const toggle = makeButton('Hide', () => {
        const group = svg.querySelector<SVGGElement>(`g[data-impl="${CSS.escape(arm.impl)}"]`); if (!group) return;
        const hidden = group.getAttribute('visibility') === 'hidden';
        group.setAttribute('visibility', hidden ? 'visible' : 'hidden'); toggle.textContent = hidden ? 'Hide' : 'Show';
        toggle.setAttribute('aria-pressed', String(!hidden)); row.dataset.hidden = String(!hidden);
      });
      toggle.setAttribute('aria-label', `Hide or show ${arm.name}'s marks`); toggle.setAttribute('aria-pressed', 'false');
      row.append(toggle);
    }
    legend.append(row);
  }
  root.dataset.displayState = 'loading'; status.textContent = 'Loading each model’s retained masks…';
  try {
    const read = async (arm: ComparisonView['arms'][number]) => {
      const query = new URLSearchParams({ session: view.session, revision: String(arm.revision) });
      const response = await fetch(`/api/resources/${encodeURIComponent(arm.population!)}/overlay?${query}`, { signal: controller.signal });
      const reply = await response.json(); if (!response.ok) throw new Error(reply.error || `${arm.name}'s masks are unavailable.`);
      return checkedOverlay(reply, { resource: { day: view.session, id: arm.population!, version: arm.revision, picture: 0 }, title: arm.name, zoom: 1, pan: [.5, .5] });
    };
    const overlays = await Promise.all(arms.map(read));
    if (controller.signal.aborted || !overlays.length) return;
    const { width, height } = overlays[0];
    svg.setAttribute('viewBox', `0 0 ${width} ${height}`);
    const picture = document.createElementNS(ns, 'image'); picture.setAttribute('width', String(width)); picture.setAttribute('height', String(height));
    picture.setAttribute('href', `/api/resources/${encodeURIComponent(arms[0].population!)}/picture?${new URLSearchParams({ day: view.session })}`);
    svg.append(picture);
    overlays.forEach((overlay, i) => {
      const arm = arms[i], group = document.createElementNS(ns, 'g'); group.dataset.impl = arm.impl;
      const scale = overlay.width === width && overlay.height === height ? '' : `scale(${width / overlay.width} ${height / overlay.height})`;
      if (scale) group.setAttribute('transform', scale);
      for (const member of overlay.members) {
        const path = document.createElementNS(ns, 'path');
        path.setAttribute('d', member.polygons.map(poly => `M${poly.map(p => p.join(',')).join('L')}Z`).join(' '));
        // Nested rings: where models agree, A's wide line shows under B's and C's thinner ones, so no colour hides another.
        path.setAttribute('fill', 'none'); path.setAttribute('stroke', arm.colour); path.setAttribute('stroke-width', String([4, 2.4, 1][i] ?? 1));
        path.setAttribute('vector-effect', 'non-scaling-stroke'); group.append(path);
      }
      svg.append(group);
    });
    status.textContent = `${arms.length} models on the same picture · ${width} × ${height} px · each Result is kept in Data.`;
    root.dataset.displayState = 'rendered';
  } catch (error) {
    if (controller.signal.aborted) return;
    root.dataset.displayState = 'failed';
    status.textContent = error instanceof Error ? error.message : String(error);
  }
}
