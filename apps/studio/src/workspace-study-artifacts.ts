import type { StudyRetainedView, StudyOpenOptions } from './workspace-study-restoration';
import {sameStudyResource} from './workspace-study-restoration';
import { FloatingWindow, type PanelStack, type WindowFacts } from './panels';
import { moreButton, type MenuItem } from './drawer-menu';

/** Presentation only: canonical renderers keep ownership of media, revisions and close commands. */
export class StudyArtifacts {
  readonly root = document.createElement('aside');
  private readonly documents = new Map<string, {root: HTMLElement; dispose: () => void}>();
  private readonly background = new WeakSet<HTMLElement>();
  private readonly guards = new WeakMap<HTMLElement, () => string | null>();
  private readonly shells = new Map<HTMLElement, FloatingWindow>();
  private options?: {panels: PanelStack; facts: () => WindowFacts | null};
  private queued = false;
  constructor() {
    this.root.id='study-artifacts';this.root.hidden=true;this.root.setAttribute('aria-label','Open project artifacts');
    document.getElementById('stage')!.append(this.root);
    new MutationObserver(()=>this.schedule()).observe(this.root,{subtree:true,childList:true,attributes:true,attributeFilter:['hidden']});
  }
  configure(panels: PanelStack, facts: () => WindowFacts | null): void {
    this.options={panels,facts};
    for(const node of this.root.children)this.attachShell(node as HTMLElement);
  }
  mount(node: HTMLElement): void {
    node.classList.add('study-artifact');node.dataset.studyArtifact='true';
    if(node.parentElement!==this.root)this.root.append(node);
    const header=node.querySelector<HTMLElement>(':scope > header');
    if(header){header.tabIndex=0;header.title='Drag to move. Arrow keys move; Shift for larger steps.';}
    this.sync();this.attachShell(node);
    window.dispatchEvent(new CustomEvent('workspace-item-mounted',{detail:node}));
  }
  private attachShell(root: HTMLElement): void {
    // Images and masks already have native shells with canonical geometry ownership.
    if(!this.options || this.shells.has(root) || root.matches('.workspace-window, .magnifier, #magnifier'))return;
    const bar=root.querySelector<HTMLElement>(':scope > header');if(!bar)return;
    const {panels,facts}=this.options,background=this.background.has(root);
    const shell=panels.register(new FloatingWindow({id:`study-document-${this.shells.size}-${Date.now()}`,root,bar,panels,facts,
      content:()=>({kind:'text'}),freeResize:true,chromeHeight:48,frontOnOpen:!background,cascade:true,
      onLayout:()=>this.windowControls(root),
      onUserChange:()=>window.dispatchEvent(new CustomEvent('workspace-layout-changed',{detail:root})),
      close:()=>bar.querySelector<HTMLButtonElement>('button[aria-label^="Close"]')?.click()}),{background});
    this.shells.set(root,shell);shell.resizeTo(420,380);shell.frame();if(!background)shell.front();
    // Body actions stay on one line; what does not fit moves into the header's ⋯ (measured when the window's size
    // changes, never per frame).
    if(typeof ResizeObserver==='function'){const watch=new ResizeObserver(()=>this.fitActions(root));watch.observe(root);}
    this.fitActions(root);
  }
  /** The window's body actions that run past its edge (`.study-mask-controls`): marked to step aside (workspace-feel.css
   * hides them) and listed in the header's ⋯ instead, so nothing wraps and nothing is lost. */
  private fitActions(root:HTMLElement):void {
    const row=root.querySelector<HTMLElement>('.study-mask-controls');if(!row||typeof row.getBoundingClientRect!=='function')return;
    const edge=row.getBoundingClientRect().right;
    for(const b of row.querySelectorAll<HTMLButtonElement>(':scope > button')){
      if(!b.hidden&&b.getBoundingClientRect().right>edge+1)b.dataset.overflow='true';else delete b.dataset.overflow;
    }
  }
  /** The header's ⋯: the window's own controls (Add to Tab…, Expand or Restore, by their own names and handlers), where
   * else it is shown (Also in), and the body actions that did not fit. Read when pressed. */
  private windowMenu(root:HTMLElement):MenuItem[] {
    this.fitActions(root);
    const items:MenuItem[]=[],tools=root.querySelector<HTMLElement>(':scope > header > .study-window-tools');
    const press=(b:HTMLButtonElement):MenuItem=>({label:b.textContent?.trim()||b.getAttribute('aria-label')||'',act:()=>b.click(),title:b.title||undefined,disabled:b.disabled,
      ...(b.getAttribute('aria-label')?{aria:b.getAttribute('aria-label')!}:{})});
    for(const b of tools?.querySelectorAll<HTMLButtonElement>(':scope > button')??[])if(!b.hidden)items.push(press(b));
    const also=tools?.querySelector<HTMLElement>('.workspace-also-in'),links=also&&!also.hidden?[...also.querySelectorAll<HTMLButtonElement>('button')]:[];
    if(links.length)items.push('separator',{heading:'Also in'},...links.map(press));
    const clipped=[...root.querySelectorAll<HTMLButtonElement>('.study-mask-controls > button[data-overflow="true"]')].filter(b=>!b.hidden);
    if(clipped.length)items.push('separator',...clipped.map(press));
    return items;
  }
  private windowControls(root:HTMLElement):void {
    this.fitActions(root);
    const button=root.querySelector<HTMLButtonElement>('.study-expand'),shell=this.shells.get(root);
    if(!button)return;
    button.disabled=!shell;
    const expanded=shell?.isExpanded()??false;
    const label=expanded?'Restore':'Expand';
    if(button.textContent===label && button.title)return;
    button.textContent=label;
    button.setAttribute('aria-label',expanded?'Restore window size':'Expand window');
    button.title=expanded?'Return to the previous window arrangement':'Use the available workspace for this document';
  }
  document(key: string, title: string, content: HTMLElement, dispose = () => {}, metadata: Record<string,string> = {}, options: StudyOpenOptions = {}, onDismiss = () => {}): HTMLElement {
    const contentKey=key,scope=document.body.dataset.activeWorkspaceView ?? 'main';
    const moved=[...this.documents.values()].find(row=>row.root.dataset.contentKey===contentKey&&row.root.dataset.workspaceView===scope);
    // Ordinary Measurements Open means reveal this exact table. Explicit restored/copied
    // placement IDs still describe independent windows and retain their own geometry.
    const requested:StudyRetainedView|undefined=metadata.studyRetained?JSON.parse(metadata.studyRetained):undefined;
    // An ordinary open receives its placement when Tabs adopt it. After a move,
    // restoration includes that ID in the renderer key; keep the mounted window.
    const placement=requested?.placementId ? [...this.documents.values()].find(({root})=>{
      if(root.dataset.project!==requested.resource.day||root.dataset.workspaceView!==scope||
        root.dataset.placementId!==requested.placementId||!root.dataset.studyRetained||root.dataset.recoveryPlaceholder==='true')return false;
      const kept:StudyRetainedView=JSON.parse(root.dataset.studyRetained);
      return kept.presentation===requested.presentation&&sameStudyResource(kept.resource,requested.resource);
    }):undefined;
    const table=(requested?.presentation==='table'||requested?.presentation==='datatable'||requested?.presentation==='analysis'||requested?.presentation==='graph')&&!requested.placementId&&requested.resource.day===document.body.dataset.workspaceProject
      ? [...this.documents.values()].find(({root})=>{
        if(root.dataset.project!==requested.resource.day||root.dataset.workspaceView!==scope||!root.dataset.studyRetained)return false;
        const kept:StudyRetainedView=JSON.parse(root.dataset.studyRetained),a=kept.resource,b=requested.resource;
        // The table reader pins absent legacy revisions to zero; picture selection does not identify table rows.
        return kept.presentation===requested.presentation&&a.day===b.day&&a.id===b.id&&(a.version??0)===(b.version??0)&&a.outputId===b.outputId;
      }):undefined;
    key = `${scope}:${key}`;
    const old=placement ?? table ?? moved ?? this.documents.get(key);
    if(old){if(options.background)return old.root;old.root.hidden=false;delete old.root.dataset.dismissed;this.sync();this.shells.get(old.root)?.front();old.root.querySelector<HTMLElement>('header')?.focus();window.dispatchEvent(new CustomEvent('workspace-item-mounted',{detail:old.root}));return old.root;}
    const root=document.createElement('section');root.className='study-artifact study-document-artifact';root.setAttribute('aria-label',title);root.dataset.artifactKey=key;root.dataset.contentKey=contentKey;Object.assign(root.dataset,metadata);
    const header=document.createElement('header'),heading=document.createElement('h3'),close=document.createElement('button'),expand=document.createElement('button');
    expand.className='study-expand';expand.textContent='Expand';expand.setAttribute('aria-label','Expand window');expand.disabled=true;
    expand.addEventListener('click',()=>{this.shells.get(root)?.toggleExpanded();this.windowControls(root);});
    heading.textContent=title;heading.title=title;close.textContent='×';close.setAttribute('aria-label',`Close ${title}`);
    const release=()=>{dispose();this.shells.get(root)?.dispose();this.shells.delete(root);root.remove();};
    close.addEventListener('click',()=>{const why=this.guards.get(root)?.();if(why){root.dispatchEvent(new CustomEvent('workspace-document-close-blocked',{detail:why}));return;}onDismiss();root.hidden=true;root.dataset.dismissed='true';window.dispatchEvent(new CustomEvent('workspace-layout-changed',{detail:root}));this.sync();});
    // The header is its title on one line, ⋯ and ×. Expand (Restore) and "Add to Tab…" with "Also in" (which join when the
    // Tabs layer adopts the window, workspace-views.ts) keep their buttons and handlers in the window's tools, which
    // its ⋯ lists by their own names.
    const tools=document.createElement('span');tools.className='study-window-tools';tools.hidden=true;tools.append(expand);
    const more=moreButton('More window actions',()=>this.windowMenu(root),'study-window-more');more.title='Add to Tab…, Expand and more';
    header.append(heading,tools,more,close);content.classList.add('study-artifact-content');root.append(header,content);
    this.documents.set(`${key}:${crypto.randomUUID()}`,{root,dispose:release});if(options.background)this.background.add(root);this.mount(root);return root;
  }
  /** Drafts belong to their mounted document; callers block destructive navigation before changing Projects. */
  guardDocument(root:HTMLElement,guard:()=>string|null):void {this.guards.set(root,guard);}
  blockReason(project?:string,tab?:string):string|null {
    for(const {root} of this.documents.values()){
      if(project&&root.dataset.project!==project||tab&&root.dataset.workspaceView!==tab)continue;
      const why=this.guards.get(root)?.();if(why)return why;
    }return null;
  }
  /** Explicit continuation only; reuse Expand without replacing any mounted work. */
  focusDocument(root:HTMLElement):void {
    if(![...this.documents.values()].some(row=>row.root===root)||this.background.has(root)
      ||root.hidden||root.dataset.dismissed==='true'||root.dataset.workspaceHidden==='true')return;
    const shell=this.shells.get(root);
    if(shell&&!shell.isExpanded()){shell.toggleExpanded();this.windowControls(root);}
  }
  applyDocument(id:string,set:{open?:boolean;front?:boolean;rect?:number[]}):boolean {
    const root=[...this.documents.values()].map(row=>row.root).find(root=>`document:${root.dataset.placementId}`===id);
    if(!root)return false;
    if(set.open===false&&this.guards.get(root)?.())return false;
    if(set.open===false)root.querySelector<HTMLButtonElement>('header button[aria-label^="Close"]')?.click();
    if(set.open===true){root.hidden=false;delete root.dataset.dismissed;this.sync();}
    if(set.front){this.shells.get(root)?.front();root.querySelector<HTMLElement>('header')?.focus();}
    if(set.rect){const host=this.root.getBoundingClientRect();this.place(root,[set.rect[0]-host.left,set.rect[1]-host.top,set.rect[2],set.rect[3]]);window.dispatchEvent(new CustomEvent('workspace-layout-changed',{detail:root}));}
    return true;
  }
  /** Reconcile the canonical name; optional header context never becomes the retained resource title. */
  documentTitle(root:HTMLElement,title:string,displayTitle=title):void {
    if(![...this.documents.values()].some(row=>row.root===root))return;
    const retained=root.dataset.studyRetained?JSON.parse(root.dataset.studyRetained) as StudyRetainedView:null;
    const heading=root.querySelector<HTMLElement>(':scope > header h3');
    if(heading){heading.textContent=displayTitle;heading.title=displayTitle;}
    root.setAttribute('aria-label',displayTitle);
    root.querySelector<HTMLButtonElement>(':scope > header button[aria-label^="Close"]')?.setAttribute('aria-label',`Close ${displayTitle}`);
    if(retained)root.dataset.studyRetained=JSON.stringify({...retained,title});
  }
  retainedPresentation():StudyRetainedView[] {
    return [...this.documents.values()].filter(row=>row.root.dataset.studyRetained && row.root.dataset.workspaceHidden !== 'true' && row.root.dataset.dismissed!=='true').map(({root})=>{
      const saved=JSON.parse(root.dataset.studyRetained!) as StudyRetainedView,box=root.getBoundingClientRect();
      return {...saved,position:[box.left,box.top],size:[box.width,box.height]};
    });
  }
  restoreGeometry(root:HTMLElement,saved:StudyRetainedView):void {
    const shell=this.shells.get(root);if(!shell)return;
    if(saved.size)shell.resizeTo(saved.size[0],saved.size[1]);
    if(saved.position){const host=this.root.getBoundingClientRect();shell.moveTo(saved.position[0]-(saved.coordinates==='stage'?0:host.left),saved.position[1]-(saved.coordinates==='stage'?0:host.top));}
  }
  place(root:HTMLElement, rect:number[]):void {
    const shell=this.shells.get(root); if(!shell)return;
    shell.resizeTo(rect[2],rect[3]); shell.moveTo(rect[0],rect[1]);
  }
  specimen(root:HTMLElement, bar:HTMLElement, close:()=>void):FloatingWindow {
    if(!this.options)throw new Error('Workspace layout is not ready.');
    const shell=this.options.panels.register(new FloatingWindow({id:'workspace-specimen',stackGroup:'specimen',root,bar,close,panels:this.options.panels,
      facts:this.options.facts,content:()=>({kind:'text'}),freeResize:true,chromeHeight:36,
      onUserChange:()=>window.dispatchEvent(new CustomEvent('workspace-layout-changed',{detail:root}))}));
    return shell;
  }
  /** Release every document; `keep` spares some (a window the arrangement never saves, which no saved layout brings back). */
  clearDocuments(keep: (root: HTMLElement) => boolean = () => false): void {
    for(const [key,row] of this.documents){if(keep(row.root)||this.guards.get(row.root)?.())continue;row.dispose();this.documents.delete(key);}this.sync();
  }
  /** Release one presentation after recovery; retained Project Data are untouched. */
  removeDocument(root:HTMLElement):void {
    for(const [key,row] of this.documents){if(row.root!==root)continue;if(this.guards.get(root)?.())return;this.documents.delete(key);row.dispose();this.sync();return;}
  }
  private schedule(): void {if(this.queued)return;this.queued=true;requestAnimationFrame(()=>{this.queued=false;this.sync();});}
  private sync(): void {
    const open=[...this.root.children].some(child=>!(child as HTMLElement).hidden);
    if(this.root.hidden===open)this.root.hidden=!open;
    document.body.dataset.studyArtifacts=open?'floating':'closed';
  }
}
let instance: StudyArtifacts | undefined;
export function studyArtifacts(): StudyArtifacts {return instance??=new StudyArtifacts();}
