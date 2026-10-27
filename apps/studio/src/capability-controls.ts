/** Browser-local placement of controls. Never owns execution, scientific data or shared layout.
 *
 * Two kinds of controls share it (workspace-feel plan, step 4). A tool page (a task's or Bright objects' run page, no
 * `owner`) belongs to Tools: its header starts with "← Tools" in every placement, and docked left it uses the Tools
 * drawer's width, so the Stage never shifts between the Tools list and a page. A scientific inspector (`owner`, its
 * document window) shows its window's title and × only. Switching the rail parks what is docked (`park`) instead of
 * closing it; a parked control is hidden but stays open, and comes back with `show` (the Tools rail item, the
 * Assistant's "<tool> controls" chip or the owner's launcher). Only a control's own × closes it. */
import { tabMark } from './tab-marks';

export type ControlPosition = 'left' | 'right' | 'floating';
type Rect = { x: number; y: number; width: number; height: number };
type Preference = { position: ControlPosition; width: number; floating: Rect };
type StorageLike = Pick<Storage, 'getItem' | 'setItem'>;
type Side = 'left' | 'right';
type Overlap = { left: boolean; right: boolean };
type Options = { profile: string; storage: StorageLike; scope: () => string[]; viewport: () => number[];
  /** Where docked controls start. `over`: this side's column does not fit beside the Stage (stage insets fell back),
   * so it starts under the Tab row and the Stage bar, never over the Tabs. */
  insets?: (over: boolean) => {top:number;left:number;bottom?:number}; reveal?: (side: Side) => void; obscured?:()=>boolean;
  /** Reserves the docked widths beside the Stage; says which sides could not be reserved and so float over it. */
  occupy?: (left:number|null,right:number|null)=>Overlap|void;
  /** The width a page docked left takes: the Tools drawer's (so the Stage does not move), and where its resize goes. */
  leftWidth?: () => number; setLeftWidth?: (width:number) => void;
  /** Whether the left drawer is on Tools now: a Tab's left page comes back on a Tab switch only then, else it parks. */
  toolsShowing?: () => boolean;
  /** A control just came into view docked on a side (the workspace makes the rail and drawer agree with it). */
  shown?: (side: Side, page: boolean) => void;
  /** "← Tools" on a page: the Tools list (the page is parked first). */
  back?: (handle: ControlHandle) => void;
  /** Where focus goes when a docked control closes and nothing it came from is in view: the rail item, or the
   * Assistant's button. */
  focusBase?: (side: Side) => void;
  /** Something parked, unparked, opened or closed: the Tools rail mark and the Assistant's chip follow. */
  changed?: () => void;
  /** The rail's and the side columns' widths as the workspace lays them out (its state, so nothing is measured while
   * the layout is being written); measured from the page when not given. */
  sides?: () => { rail: number; left: number; right: number } };
export type ControlOptions = { id: string; title: string; content: HTMLElement; owner?: HTMLElement; scope?: () => string[];
  rememberOpenPerScope?: boolean; canMove?: () => boolean; geometryChanged?: () => void; activate?: () => void };
const bounded = (n: unknown, low: number, high: number, fallback: number) => typeof n === 'number' && Number.isFinite(n) ? Math.max(low, Math.min(high, n)) : fallback;
export function controlPreference(raw: string | null): Preference {
  let p: Partial<Preference> = {}; try { const value=JSON.parse(raw??'{}'); if(value && typeof value==='object')p=value; } catch { /* private layout is recoverable */ }
  return {position: p.position==='right'||p.position==='floating'?p.position:'left',width:bounded(p.width,320,720,360),
    floating:{x:bounded(p.floating?.x,0,100000,440),y:bounded(p.floating?.y,0,100000,120),width:bounded(p.width,320,720,360),height:bounded(p.floating?.height,240,2000,620)}};
}
export function controlRect(rect: Rect, width: number, height: number): Rect {
  const w=Math.min(rect.width,width),h=Math.min(rect.height,height);
  return {x:Math.max(0,Math.min(rect.x,width-w)),y:Math.max(0,Math.min(rect.y,height-h)),width:w,height:h};
}
/** Reserve usable dock space whenever it fits; narrow does not automatically mean overlay. `left` is a left page's
 * footprint including the rail, `baseLeft` the rail plus the drawer or Data window shown; `right` a right page's width,
 * `baseRight` the Assistant's. `overLeft`/`overRight` say a side's column was not reserved and so floats over the
 * Stage: it then starts under the Tab row and the Stage bar (the drawer's `data-drawer-dock="over"`, a docked page's
 * top), so nothing on either side ever covers the Tabs (R-16). This is the one place that decides it. */
export function controlStageInsets(viewport: number, left: number | null, right: number | null,
                                   baseLeft: number, baseRight: number, rail = 64): {left:number;right:number;overLeft:boolean;overRight:boolean} {
  const wantedLeft=left??baseLeft,wantedRight=right??baseRight;
  if(wantedLeft+wantedRight+320<=viewport)return {left:wantedLeft,right:wantedRight,overLeft:false,overRight:false};
  // Retain the small-screen overlay fallback when the canvas would become unusable. A left page starts after the rail
  // (sideRect), so the Stage never slides under the rail.
  const stageLeft=viewport<1200||left!==null?rail:baseLeft,stageRight=viewport<1200||right!==null?0:baseRight;
  return {left:stageLeft,right:stageRight,overLeft:stageLeft<wantedLeft,overRight:stageRight<wantedRight};
}
const element = <K extends keyof HTMLElementTagNameMap>(tag: K, className='', text='') => {
  const e=document.createElement(tag); e.className=className;e.textContent=text;return e;
};
const button=(text:string,run:()=>void)=>{const e=element('button','',text);e.type='button';e.addEventListener('click',run);return e;};
const setHidden=(node:HTMLElement,hidden:boolean)=>{if(node.hidden!==hidden)node.hidden=hidden;};
/** Width choices for a floating or right-docked page (a left page takes the Tools drawer's width). */
const WIDTHS:[number,string][]=[[360,'Compact'],[480,'Comfortable'],[640,'Wide']];

export class ControlDeck {
  readonly controls = new Set<ControlHandle>();
  private serial=0;
  private popoverStack:ControlHandle[]=[];
  readonly options: Options;
  constructor(options: Options) {this.options=options;}
  attach(options: ControlOptions): ControlHandle { const handle=new ControlHandle(this,options);this.controls.add(handle);return handle; }
  touch(handle: ControlHandle) {handle.order=++this.serial;this.refresh();}
  /** Which sides float over the Stage (occupy's answer at the last refresh). */
  private overlap:Overlap={left:false,right:false};
  insets(side:Side='left'){
    const [width,height]=this.options.viewport(),bounds=this.options.insets?.(this.overlap[side]);
    return {top:bounded(bounds?.top??52,0,Math.max(0,height-120),0),left:bounded(bounds?.left??64,0,Math.max(0,width-120),0),bottom:bounded(bounds?.bottom??0,0,Math.max(0,height-240),0)};
  }
  /** A side's docked rectangle. The left one starts after the rail, so navigation stays in view while a tool is
   * docked, and takes the Tools drawer's width when the workspace gives one. */
  sideRect(side:Side,requestedWidth:number):Rect {
    const [vw,vh]=this.options.viewport(),{top,left,bottom}=this.insets(side),start=side==='left'?left:0;
    const wanted=side==='left'?bounded(this.options.leftWidth?.(),320,720,requestedWidth):requestedWidth,width=Math.min(wanted,vw-start);
    return {x:side==='left'?start:vw-width,y:top,width,height:vh-top-bottom};
  }
  /** A side's drop target while dragging: a strip at its edge (never the page's own width). */
  dropRect(side:Side):Rect {
    const [width]=this.options.viewport(),r=this.sideRect(side,96),w=Math.min(96,width/3);
    return {...r,x:side==='left'?r.x:width-w,width:w};
  }
  dropPosition(x:number,y:number):ControlPosition {
    const left=this.dropRect('left'),right=this.dropRect('right');
    if(y<left.y||y>left.y+left.height)return 'floating';
    return x<left.x+left.width?'left':x>right.x?'right':'floating';
  }
  private scopeKey(){return JSON.stringify(this.options.scope());}
  refresh() {
    const scope=this.scopeKey();
    for(const control of this.controls)control.restoreScope();
    // A Tab or Project switch brings back a left page only while the drawer is on Tools; otherwise it waits, parked
    // (the Tools item's dot). Every tool page, not only those that keep their openness per Tab: Bright objects keeps
    // the Tab it was opened in, and comes back with it.
    if(this.lastScope!==null&&scope!==this.lastScope&&this.options.toolsShowing&&!this.options.toolsShowing())
      for(const c of this.controls)if(c.page&&c.open&&!c.parked&&c.position==='left'&&c.scopeKey===scope&&c.ownerVisible())c.parked=true;
    this.lastScope=scope;
    const eligible=[...this.controls].filter(c=>!this.options.obscured?.() && c.open && !c.parked && c.scopeKey===scope && c.ownerVisible());
    const latest=(side:ControlPosition):ControlHandle|undefined=>eligible.filter(c=>c.position===side).sort((a,b)=>b.order-a.order)[0];
    let left=latest('left'),right=latest('right');
    if(left&&right&&this.sideRect('left',left.preference.width).width+right.preference.width+480>this.options.viewport()[0]) {
      if(left.order>right.order)right=undefined;else left=undefined;
    }
    const visible=eligible.filter(c=>c.position==='floating'||c===left||c===right).sort((a,b)=>a.order-b.order);
    // The Stage's insets first: whether a side floats over the Stage decides where its docked control starts.
    this.overlap=this.options.occupy?.(left?this.sideRect('left',left.preference.width).width:null,right?this.sideRect('right',right.preference.width).width:null)||{left:false,right:false};
    for(const control of this.controls)if(!visible.includes(control))control.present(false,eligible);
    this.popoverStack=this.popoverStack.filter(c=>visible.includes(c));
    for(const control of visible){
      const appeared=!control.visible;control.present(true,eligible);
      if(appeared)this.popoverStack.push(control);
    }
    // Browser top-layer order ignores z-index. A restored older side must not cover
    // the active floating tool. Reorder only when needed, never during pointer capture.
    if(!visible.some(c=>c.gesturing)){
      const mismatch=visible.findIndex((c,i)=>this.popoverStack[i]!==c);
      if(mismatch>=0){for(const control of visible.slice(mismatch))control.raise();this.popoverStack=[...visible];}
    }
    for(const control of visible)control.dockedShown();
    const signature=JSON.stringify([scope,[...this.controls].map(c=>[c.options.id,c.open,c.parked,c.visible,c.position])]);
    if(signature!==this.changeSignature){this.changeSignature=signature;this.options.changed?.();}
  }
  private changeSignature='';
  private lastScope:string|null=null;
  private soon=false;
  /** Refreshes once after the current task's layout changes (the workspace's applyLayout), coalesced. */
  refreshSoon(){if(this.soon)return;this.soon=true;queueMicrotask(()=>{this.soon=false;this.refresh();});}
  /** Hides what is docked on a side without closing it: the rail switched, or the Assistant was asked for. */
  park(side:Side){const scope=this.scopeKey();for(const c of this.controls)if(c.scopeKey===scope&&c.open&&c.position===side)c.parked=true;this.refresh();}
  /** The newest tool page parked in this Project and Tab (docked left or floating): the Tools rail item opens it. */
  parkedPage():ControlHandle|undefined{
    const scope=this.scopeKey();
    return [...this.controls].filter(c=>c.page&&c.open&&c.parked&&c.position!=='right'&&c.scopeKey===scope&&c.ownerVisible()).sort((a,b)=>b.order-a.order)[0];
  }
  /** The newest control parked on the right (the Assistant's "<tool> controls" chip). */
  parkedRight():ControlHandle|undefined{
    const scope=this.scopeKey();
    return [...this.controls].filter(c=>c.open&&c.parked&&c.position==='right'&&c.scopeKey===scope&&c.ownerVisible()).sort((a,b)=>b.order-a.order)[0];
  }
  /** What shows docked on a side now, if anything. */
  visibleOn(side:Side):ControlHandle|undefined{return [...this.controls].find(c=>c.visible&&c.position===side);}
  focusBase(side:Side){this.options.focusBase?.(side);}
  reveal(side:Side){const scope=this.scopeKey();for(const c of this.controls)if(c.scopeKey===scope&&c.position===side){c.open=false;c.parked=false;}this.refresh();}
  dispose(){for(const c of [...this.controls])c.dispose();}
}

export class ControlHandle {
  readonly shell=element('section','capability-controls');
  readonly launcher=button('Controls',()=>this.show());
  readonly body=element('div','capability-controls-body');
  /** The other pages open on this side, in the header's ⋯ ("Open pages", the one showing checked): never a native
   * select in the header, whose title stays plain. */
  readonly pages=element('div','capability-controls-pages');
  private readonly back:HTMLButtonElement;
  private readonly grip:HTMLElement;
  private readonly more:HTMLButtonElement;
  private readonly menu:HTMLElement;
  private readonly positionButtons=new Map<ControlPosition,HTMLButtonElement>();
  private readonly widthGroup:HTMLElement;
  private readonly widthButtons=new Map<number,HTMLButtonElement>();
  private readonly customWidth:HTMLButtonElement;
  /** The page's own menu items (setActions), shown first in ⋯. */
  private readonly own=element('div','capability-controls-own');
  readonly status=element('p','capability-controls-status');
  preference=controlPreference(null);
  scopeKey='';open=false;visible=false;order=0;
  /** Hidden by a rail switch (or the Assistant), still open: `show` brings it back. */
  parked=false;
  private storageKey='';
  private readonly scopeStates=new Map<string,{open:boolean;order:number;parked:boolean}>();
  private readonly abort=new AbortController();
  private returnFocus: HTMLElement | null=null;
  private cancelGesture: (()=>void)|null=null;
  private disposed=false;
  private switchSignature='';
  private geometrySignature='';
  private shownSide:ControlPosition|null=null;
  get position(){return this.preference.position;}
  /** A tool page (it belongs to Tools); a control with an owner is that window's inspector. */
  get page(){return !this.options.owner;}
  get gesturing(){return this.cancelGesture!==null||this.options.canMove?.()===false;}
  raise(){
    if(!this.visible||this.gesturing)return;
    const focused=document.activeElement instanceof HTMLElement && this.shell.contains(document.activeElement)?document.activeElement:null;
    try{this.shell.hidePopover?.();this.shell.showPopover?.();}catch{/* detached during teardown */}
    focused?.focus({preventScroll:true});
  }
  private readonly deck:ControlDeck;
  readonly options:ControlOptions;
  constructor(deck:ControlDeck,options:ControlOptions) {
    this.deck=deck;this.options=options;
    const title=element('strong','capability-controls-title',options.title);title.title=options.title;
    const header=element('header','capability-controls-header');
    this.shell.setAttribute('popover','manual');this.shell.setAttribute('aria-label',`${options.title} controls`);
    this.shell.setAttribute('role','region');this.shell.setAttribute('tabindex','-1');
    this.shell.dataset.kind=this.page?'page':'inspector';
    this.launcher.setAttribute('aria-label',`Show ${options.title} controls`);
    this.launcher.title='Open controls. Placement is personal to this Project and Tab.';
    // Only between several open pages on one side: which one shows, chosen from the ⋯ (the header keeps its title).
    this.pages.setAttribute('role','group');this.pages.setAttribute('aria-label','Open pages');
    // "← Tools": a page belongs to Tools in every placement; it parks, and the Tools list shows.
    this.back=button('Tools',()=>{if(!this.allowed())return;this.park();this.deck.options.back?.(this);});
    this.back.className='capability-controls-back';this.back.setAttribute('aria-label','Back to Tools');this.back.title='Back to the Tools list. This page stays open: the Tools rail item brings it back.';
    this.back.prepend(tabMark('left','capability-controls-back-icon'));
    this.back.hidden=!this.page;
    // The grip shows, and the header drags, only while floating; docked pages move from ⋯.
    const grip=this.grip=element('span','capability-controls-grip','⠿');grip.setAttribute('aria-hidden','true');
    const close=button('×',()=>this.hide());close.className='capability-controls-close';close.setAttribute('aria-label',`Close ${options.title} controls`);close.title='Close this page; running work continues';
    // ⋯ holds where it sits (Left, Float, Right) and, floating or on the right, how wide.
    const menu=this.menu=element('div','capability-controls-menu');menu.setAttribute('role','menu');menu.setAttribute('aria-label',`${options.title} placement`);menu.hidden=true;
    const more=this.more=button('⋯',()=>this.toggleMenu());more.className='capability-controls-more';
    more.setAttribute('aria-label',`More for ${options.title}: placement`);more.setAttribute('aria-haspopup','menu');more.setAttribute('aria-expanded','false');
    more.title='Place this page on the left, floating or on the right';
    // The page's own items (a run page's Compare… and its model in Models), the other pages open on its side, then
    // where it sits.
    menu.append(this.own,this.pages);
    menu.append(element('div','capability-controls-menu-heading','Placement'));
    for(const [value,label]of [['left','Left'],['floating','Float'],['right','Right']] as const){
      const action=button('',()=>{this.move(value);this.closeMenu(true);});action.dataset.position=value;action.setAttribute('role','menuitemradio');
      action.setAttribute('aria-label',value==='floating'?`Float ${options.title} controls`:`Dock ${options.title} controls ${value}`);
      action.title=value==='floating'?'Float over the workspace':value==='left'?'In the left drawer, at the Tools drawer’s width':'Attach to the right sidebar';
      action.setAttribute('aria-checked','false');
      const icon=element('span','capability-controls-position-icon');icon.setAttribute('aria-hidden','true');
      action.append(icon,element('span','',label));menu.append(action);this.positionButtons.set(value,action);
    }
    const widths=this.widthGroup=element('div','capability-controls-widths');widths.setAttribute('role','group');widths.setAttribute('aria-label',`${options.title} width`);
    widths.append(element('div','capability-controls-menu-heading','Width'));
    for(const [value,text]of WIDTHS){const b=button(text,()=>{this.resize(value);this.closeMenu(true);});b.setAttribute('role','menuitemradio');b.setAttribute('aria-checked','false');b.dataset.width=String(value);widths.append(b);this.widthButtons.set(value,b);}
    this.customWidth=button('',()=>this.closeMenu(true));this.customWidth.setAttribute('role','menuitemradio');this.customWidth.setAttribute('aria-checked','true');this.customWidth.hidden=true;widths.append(this.customWidth);
    menu.append(widths);
    menu.addEventListener('keydown',e=>{
      const items=[...menu.querySelectorAll<HTMLButtonElement>('button')].filter(b=>!b.hidden&&!b.closest('[hidden]'));const at=items.indexOf(document.activeElement as HTMLButtonElement);
      if(e.key==='Escape'){e.preventDefault();e.stopPropagation();this.closeMenu(true);}
      else if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();items[(at+(e.key==='ArrowDown'?1:-1)+items.length)%items.length]?.focus();}
    });
    header.append(this.back,grip,title,more,close);
    header.title='';
    this.status.setAttribute('role','status');this.status.hidden=true;
    const resize=element('div','capability-controls-resize');resize.setAttribute('aria-valuemin','320');resize.setAttribute('aria-valuemax','720');resize.setAttribute('role','separator');resize.setAttribute('tabindex','0');resize.setAttribute('aria-orientation','vertical');resize.setAttribute('aria-label','Resize controls');
    resize.addEventListener('keydown',e=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();
      const now=this.currentWidth();
      this.resize(e.key==='Home'?320:e.key==='End'?720:now+(e.key==='ArrowRight'?24:-24));
    });
    resize.addEventListener('pointerdown',e=>this.gesture(e,resize,true));
    header.addEventListener('pointerdown',e=>{if(this.position==='floating'&&!(e.target as HTMLElement).closest('button,select'))this.gesture(e,header,false);});
    this.shell.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.cancelGesture){e.preventDefault();e.stopPropagation();this.cancelGesture();}});
    // Keep the scientific ownership subtree intact: top-layer presentation, not a DOM portal.
    const parent=options.content.parentElement;
    if(options.owner)parent?.insertBefore(this.shell,options.content);else document.body.append(this.shell);
    this.body.append(options.content);this.shell.append(header,menu,this.status,this.body,resize);
    this.shell.hidden=true;
  }
  ownerVisible(){const root=this.options.owner;return !root || (root.isConnected!==false&&!root.hidden&&root.dataset.dismissed!=='true'&&root.dataset.workspaceHidden!=='true');}
  /** Global tool controls retain openness per Tab; native controls remain pinned to their owner. A left page comes back
   * on a Tab switch only while the drawer is on Tools; otherwise it parks (the Tools rail item shows its dot). */
  restoreScope(){
    if(!this.options.rememberOpenPerScope || this.options.owner || this.disposed)return;
    const scope=JSON.stringify(this.options.scope?.()??this.deck.options.scope());
    if(scope===this.scopeKey)return;
    if(this.scopeKey)this.scopeStates.set(this.scopeKey,{open:this.open,order:this.order,parked:this.parked});
    this.cancelGesture?.();
    const prior=this.scopeStates.get(scope);this.open=prior?.open??false;this.order=prior?.order??0;this.parked=prior?.parked??false;
    this.loadScope(scope);
    if(this.open&&!this.parked&&this.position==='left'&&this.deck.options.toolsShowing&&!this.deck.options.toolsShowing())this.parked=true;
  }
  private loadScope(scope:string){
    if(scope===this.scopeKey)return;
    this.scopeKey=scope;this.storageKey=`hooke-control-placement-v1:${this.deck.options.profile}:${scope}:${this.options.id}`;
    try{this.preference=controlPreference(this.deck.options.storage.getItem(this.storageKey));}catch{this.preference=controlPreference(null);}
  }
  show(focus = true) {
    if(this.disposed)return;
    this.restoreScope();
    this.loadScope(JSON.stringify(this.options.scope?.()??this.deck.options.scope()));
    if(focus && document.activeElement instanceof HTMLElement && !this.shell.contains(document.activeElement))this.returnFocus=document.activeElement;
    // Coming back unparks the others open on its side too, so the in-header switcher still lists every open page.
    for(const c of this.deck.controls)if(c!==this&&c.scopeKey===this.scopeKey&&c.open&&c.parked&&c.position===this.position&&this.position!=='floating')c.parked=false;
    this.open=true;this.parked=false;this.deck.touch(this);if(focus && this.visible){this.options.activate?.();this.shell.focus?.({preventScroll:true});}
  }
  /** Hidden but kept open: the rail switched away, or "← Tools". */
  park(){this.closeMenu(false);this.cancelGesture?.();if(!this.open)return;this.parked=true;this.deck.refresh();}
  /** The page's own items in ⋯ (a run page: Compare…, its model in Models), above Left / Float / Right. */
  setActions(items:{label:string;run:()=>void;title?:string;disabled?:boolean}[]){
    const signature=JSON.stringify(items.map(i=>[i.label,i.title??'',!!i.disabled]));
    if(signature===this.own.dataset.signature)return;this.own.dataset.signature=signature;
    this.own.replaceChildren(...items.map(item=>{const b=button(item.label,()=>{this.closeMenu(false);item.run();});b.setAttribute('role','menuitem');if(item.title)b.title=item.title;b.disabled=!!item.disabled;return b;}),
      ...(items.length?[element('hr','capability-controls-menu-rule')]:[]));
    this.own.hidden=!items.length;
    this.more.setAttribute('aria-label',`More for ${this.options.title}: ${items.length?`${items.map(i=>i.label.replace(/…$/,'')).join(', ').toLowerCase()}, `:''}placement`);
  }
  /** Closed in favour of another page on the same side (a run page's task switch): nothing is revealed underneath and
   * focus stays where the new page put it. */
  dismiss(){this.cancelGesture?.();this.closeMenu(false);if(!this.open)return;this.open=false;this.parked=false;this.deck.refresh();}
  hide(){
    const restore=this.visible;this.cancelGesture?.();this.open=false;this.parked=false;this.closeMenu(false);
    // Closing a docked page closes its side's pages and hands the side back (the drawer closes; the Assistant shows),
    // never a silently substituted older tool. Other controls keep their live nodes and can be explicitly reopened.
    const side=this.position;
    if(restore&&side!=='floating'){this.deck.reveal(side);this.deck.options.reveal?.(side);}else this.deck.refresh();
    if(restore){const target=[this.launcher,this.returnFocus].find(e=>e?.isConnected && !this.shell.contains(e) && (!e.checkVisibility || e.checkVisibility()));
      if(target)target.focus({preventScroll:true});else if(side!=='floating')this.deck.focusBase(side);}
  }
  private allowed(){if(this.options.canMove?.()===false){this.status.textContent='Finish or cancel the current gesture before moving controls.';this.status.hidden=false;return false;}this.status.hidden=true;return true;}
  move(position:ControlPosition){
    if(!['left','right','floating'].includes(position)||!this.allowed())return;
    this.preference.position=position;this.open=true;this.parked=false;this.save();this.deck.touch(this);
  }
  /** The width it shows at: docked left, the Tools drawer's; else its own. */
  private currentWidth(){return this.position==='left'?this.deck.sideRect('left',this.preference.width).width:this.preference.width;}
  resize(width:number){
    if(!this.allowed())return;
    const value=bounded(width,320,720,360);
    if(this.position==='left'&&this.deck.options.setLeftWidth){this.deck.options.setLeftWidth(value);this.deck.refresh();return;}
    this.preference.width=value;this.preference.floating.width=value;this.save();this.deck.refresh();
  }
  private save(){try{this.deck.options.storage.setItem(this.storageKey,JSON.stringify(this.preference));}catch{this.status.textContent='Placement could not be saved in this browser. Your work is unaffected.';this.status.hidden=false;}}
  private toggleMenu(){if(this.menu.hidden)this.openMenu();else this.closeMenu(true);}
  private openMenu(){
    this.menu.hidden=false;this.more.setAttribute('aria-expanded','true');
    const first=[...this.menu.querySelectorAll<HTMLButtonElement>('button[aria-checked="true"]')].find(b=>!b.hidden&&!b.closest('[hidden]'))??this.menu.querySelector<HTMLButtonElement>('button');
    first?.focus?.({preventScroll:true});
    const outside=(event:Event)=>{if(!this.menu.contains(event.target as Node)&&event.target!==this.more){this.closeMenu(false);}};
    this.menuOutside=outside;document.addEventListener('pointerdown',outside,true);
  }
  private menuOutside:((event:Event)=>void)|null=null;
  private closeMenu(refocus:boolean){
    if(this.menuOutside){document.removeEventListener('pointerdown',this.menuOutside,true);this.menuOutside=null;}
    if(this.menu.hidden)return;this.menu.hidden=true;this.more.setAttribute('aria-expanded','false');
    if(refocus&&this.visible)this.more.focus?.({preventScroll:true});
  }
  /** Tells the workspace once that this control is in view docked on a side (so the rail and drawer agree with it). */
  dockedShown(){
    const side=this.visible&&this.position!=='floating'?this.position:null;
    if(side===this.shownSide)return;this.shownSide=side;
    if(side)this.deck.options.shown?.(side,this.page);
  }
  present(visible:boolean,eligible:ControlHandle[]){
    if(this.disposed)return;
    const appeared=visible&&!this.visible;
    if(visible!==this.visible){this.visible=visible;this.shell.hidden=!visible;
      if(visible){try{this.shell.showPopover?.();}catch{/* detached during mounting; refreshed on attachment */}}
      else{this.cancelGesture?.();this.closeMenu(false);this.shownSide=null;try{this.shell.hidePopover?.();}catch{/* already hidden */}}
      this.launcher.setAttribute('aria-expanded',String(visible));
    }
    if(!visible)return;
    const [vw,vh]=this.deck.options.viewport();
    const r=this.position==='floating'?controlRect(this.preference.floating,vw,vh):this.deck.sideRect(this.position,this.preference.width);
    Object.assign(this.shell.style,{left:`${r.x}px`,top:`${r.y}px`,width:`${r.width}px`,height:`${r.height}px`});
    this.shell.querySelector('.capability-controls-resize')?.setAttribute('aria-valuenow',String(Math.round(r.width)));
    const geometry=JSON.stringify(r);
    if(appeared||geometry!==this.geometrySignature){
      this.geometrySignature=geometry;
      if(!this.cancelGesture)queueMicrotask(()=>{if(this.visible&&!this.cancelGesture)this.options.geometryChanged?.();});
    }
    this.shell.dataset.position=this.position;
    setHidden(this.grip,this.position!=='floating');
    for(const [position,action]of this.positionButtons)action.setAttribute('aria-checked',String(position===this.position));
    // Compact, Comfortable and Wide apply floating and on the right; on the left the page takes the Tools drawer's width.
    setHidden(this.widthGroup,this.position==='left');
    const custom=!WIDTHS.some(([w])=>w===this.preference.width);
    for(const [width,b]of this.widthButtons)b.setAttribute('aria-checked',String(width===this.preference.width));
    setHidden(this.customWidth,!custom);if(custom){const label=`${this.preference.width} px`;if(this.customWidth.textContent!==label)this.customWidth.textContent=label;}
    // The other pages open on this side, in the ⋯ (never replaced while one of them has keyboard focus).
    const sideControls=eligible.filter(c=>c.position===this.position);
    setHidden(this.pages,sideControls.length<2);
    const signature=JSON.stringify([this.position,sideControls.map(c=>[c.options.id,c.options.title])]);
    if(!this.pages.contains(document.activeElement)&&signature!==this.switchSignature){this.switchSignature=signature;
      this.pages.replaceChildren(element('div','capability-controls-menu-heading','Open pages'));
      for(const control of sideControls){
        const item=button('',()=>{this.closeMenu(false);control.show();});item.dataset.page=control.options.id;item.setAttribute('role','menuitemradio');
        item.title=`Show ${control.options.title} here`;item.append(element('span','',control.options.title));this.pages.append(item);
      }
    }
    for(const item of this.pages.querySelectorAll<HTMLButtonElement>('button[data-page]'))item.setAttribute('aria-checked',String(item.dataset.page===this.options.id));
  }
  private gesture(event:PointerEvent,target:HTMLElement,resizing:boolean){
    if(event.button!==0||!this.allowed())return;event.preventDefault();event.stopPropagation();
    this.deck.touch(this);
    const original=structuredClone(this.preference),rect=this.shell.getBoundingClientRect(),startWidth=this.currentWidth();let x=event.clientX,y=event.clientY,moved=false;
    const threshold=5;
    target.setPointerCapture(event.pointerId);const abort=new AbortController();
    const preview=element('div','capability-controls-drop');preview.setAttribute('popover','manual');
    const landing=element('div','capability-controls-drop-landing');landing.setAttribute('aria-hidden','true');preview.append(landing);
    const destinations=(['left','right'] as const).map(side=>{
      const zone=element('div','capability-controls-drop-zone');zone.dataset.side=side;
      zone.append(element('span','',`Dock ${side}`));preview.append(zone);return{side,zone};
    });
    const hint=element('div','capability-controls-drop-hint');hint.setAttribute('role','status');preview.append(hint);
    let previewShown=false;
    const showDestination=()=>{
      const side=this.deck.dropPosition(x,y);preview.dataset.position=side;
      for(const {side,zone}of destinations){const r=this.deck.dropRect(side);
        Object.assign(zone.style,{left:`${r.x}px`,top:`${r.y}px`,width:`${r.width}px`,height:`${r.height}px`});}
      landing.hidden=side==='floating';
      if(side!=='floating'){const r=this.deck.sideRect(side,this.preference.width);Object.assign(landing.style,{left:`${r.x}px`,top:`${r.y}px`,width:`${r.width}px`,height:`${r.height}px`});}
      hint.textContent=`Release to ${side==='floating'?'float in workspace':`dock ${side}`} · Escape to cancel`;
      if(!previewShown){document.body.append(preview);try{preview.showPopover?.();}catch{/* detached */}previewShown=true;}
    };
    const end=(commit:boolean)=>{abort.abort();try{target.releasePointerCapture(event.pointerId);}catch{}preview.remove();this.shell.dataset.dragging='false';this.cancelGesture=null;
      if(!commit){this.preference=original;if(resizing&&original.position==='left')this.deck.options.setLeftWidth?.(startWidth);}else if(moved){this.save();this.options.geometryChanged?.();}this.deck.refresh();};
    this.cancelGesture=()=>end(false);
    target.addEventListener('pointermove',e=>{if(e.pointerId!==event.pointerId)return;x=e.clientX;y=e.clientY;if(Math.abs(x-event.clientX)+Math.abs(y-event.clientY)<threshold&&!moved)return;moved=true;
      if(resizing){const width=bounded(startWidth+(x-event.clientX)*(original.position==='right'?-1:1),320,720,360);
        if(original.position==='left'&&this.deck.options.setLeftWidth)this.deck.options.setLeftWidth(width);else{this.preference.width=width;this.preference.floating.width=width;}}
      else{this.shell.dataset.dragging='true';
        this.preference.position='floating';this.preference.floating={x:rect.left+x-event.clientX,y:rect.top+y-event.clientY,width:rect.width,height:rect.height};}
      this.deck.refresh();if(!resizing)showDestination();
    },{signal:abort.signal});
    target.addEventListener('pointerup',e=>{if(e.pointerId!==event.pointerId)return;if(moved&&!resizing)this.preference.position=this.deck.dropPosition(e.clientX,e.clientY);end(true);},{signal:abort.signal});
    target.addEventListener('pointercancel',()=>end(false),{signal:abort.signal});
    window.addEventListener('blur',()=>end(false),{signal:abort.signal});
    document.addEventListener('keydown',e=>{if(e.key==='Escape'){e.preventDefault();e.stopPropagation();end(false);}},{signal:abort.signal,capture:true});
    target.addEventListener('lostpointercapture',()=>{if(this.cancelGesture)end(false);},{signal:abort.signal});
  }
  dispose(){if(this.disposed)return;this.cancelGesture?.();this.closeMenu(false);this.open=false;this.parked=false;this.present(false,[]);this.disposed=true;this.abort.abort();this.deck.controls.delete(this);this.shell.remove();this.launcher.remove();this.deck.refresh();}
}
/** Both native dialogs and the existing application setup sheets suspend top-layer controls. */
export function controlModalOpen():boolean {
  if(document.querySelector('dialog:modal'))return true;
  return [...document.querySelectorAll<HTMLElement>('[role="dialog"][aria-modal="true"]')].some(dialog=>
    !dialog.closest('[hidden],[aria-hidden="true"]') && dialog.checkVisibility());
}
let deck:ControlDeck|null=null;
/** Parks what is docked on a side: the rail switched, or the Assistant was asked for. Nothing closes. */
export function parkControls(side:Side){deck?.park(side);}
export function dockControls(options:ControlOptions):ControlHandle|undefined{return deck?.attach(options);}
/** The workspace's side of placement: where pages go, what the rail shows, and what a closed side hands back to. */
export type DeckHooks = Pick<Options,'reveal'|'leftWidth'|'setLeftWidth'|'toolsShowing'|'shown'|'back'|'focusBase'|'changed'|'sides'>;
export function configureControlDeck(profile:string,hooks:DeckHooks|((side:Side)=>void)){
  const given:DeckHooks=typeof hooks==='function'?{reveal:hooks}:hooks;
  // What occupy last wrote on body: a changed custom property there re-styles the whole page, so it writes only changes.
  const written=new Map<string,string>();
  const put=(name:string,value:string)=>{if(written.get(name)===value)return;written.set(name,value);document.body.style.setProperty(name,value);};
  deck?.dispose();deck=new ControlDeck({profile,storage:localStorage,scope:()=>[document.body.dataset.workspaceProject??'',document.body.dataset.activeWorkspaceView??''],viewport:()=>[innerWidth,innerHeight],insets:over=>{
    const rail=document.querySelector('.study-rail')?.getBoundingClientRect();
    const left=rail?.width?rail.right:64,top=rail?.height?rail.top:52;
    // Not reserved beside the Stage: the docked page floats over it under the Tab row and the Stage bar, above the
    // strip, never over the Tabs.
    if(over){const bar=document.querySelector('.workspace-stage-bar')?.getBoundingClientRect();if(bar?.height)return {top:bar.bottom,left,bottom:40};}
    return {top,left};
  },occupy:(left,right)=>{
    const shownWidth=(selector:string)=>{const node=document.querySelector<HTMLElement>(selector);return node&&!node.hidden?node.getBoundingClientRect().width:0;};
    const sides=given.sides?.();
    const rail=sides?sides.rail:shownWidth('.study-rail'),baseLeft=rail+(sides?sides.left:Math.max(shownWidth('.study-drawer'),shownWidth('.study-data-window'))),baseRight=sides?sides.right:shownWidth('.study-right-panel');
    // Docked left controls sit beside the rail (sideRect), so the stage starts after both.
    const stage=controlStageInsets(innerWidth,left===null?null:rail+left,right,baseLeft,baseRight,rail||64);
    put('--control-stage-left',`${stage.left}px`);
    put('--control-stage-right',`${stage.right}px`);
    for(const [side,width]of [['Left',left],['Right',right]] as const){
      const key=`control${side}Active`,value=String(width!==null);
      if(document.body.dataset[key]!==value)document.body.dataset[key]=value;
      if(width!==null)put(`--control-${side.toLowerCase()}-width`,`${width}px`);
    }
    // The drawer and the Data window follow the same decision: beside the Stage, or over it under the Tab row and the
    // Stage bar (workspace-feel.css), never over the Tabs.
    const dock=stage.overLeft?'over':'beside';if(document.body.dataset.drawerDock!==dock)document.body.dataset.drawerDock=dock;
    return {left:stage.overLeft,right:stage.overRight};
  },...given,obscured:controlModalOpen});
  // Coalesced to one refresh a frame (at once while the page is hidden and draws no frames): the magnifier's buttons,
  // the readout and the chips change attributes many times a second, and each refresh is about a millisecond.
  let queued=false;
  const refreshOnce=()=>{if(queued)return;queued=true;const run=()=>{queued=false;deck?.refresh();};
    if(typeof requestAnimationFrame==='function'&&!document.hidden)requestAnimationFrame(run);else queueMicrotask(run);};
  const observer=new MutationObserver(refreshOnce);observer.observe(document.body,{subtree:true,attributes:true,attributeFilter:['hidden','data-dismissed','data-workspace-hidden','data-workspace-project','data-active-workspace-view','open','aria-hidden','aria-modal'],childList:true});
  window.addEventListener('resize',()=>deck?.refresh());
  return deck;
}
