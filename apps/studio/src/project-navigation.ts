/** Project destinations outlive open tabs. This component never admits scientific work. */
export type ProjectDestination='overview'|'studies'|'inputs'|'results'|'activity';
export class ProjectNavigation {
  readonly root=document.createElement('nav');
  private details=document.createElement('details');
  private buttons=new Map<ProjectDestination,HTMLButtonElement>();
  private choose:(destination:ProjectDestination)=>void;
  constructor(choose:(destination:ProjectDestination)=>void){
    this.choose=choose;
    this.root.className='project-navigation';this.root.setAttribute('aria-label','Project index');
    const summary=document.createElement('summary');summary.textContent='Project index';this.details.append(summary);
    const list=document.createElement('div');list.className='project-navigation-items';
    for(const [key,label]of [['overview','Overview'],['studies','Studies'],['inputs','Inputs'],['results','Results'],['activity','Activity']]as const){
      const b=document.createElement('button');b.type='button';b.textContent=label;
      b.onclick=()=>{this.choose(key);this.details.open=false;};this.buttons.set(key,b);list.append(b);
    }
    this.details.append(list);const home=document.createElement('button');home.type='button';home.textContent='Overview';home.className='project-overview-link';home.onclick=()=>choose('overview');
    this.root.append(home,this.details);
    this.root.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.details.open){this.details.open=false;summary.focus();e.stopPropagation();}});
    this.root.addEventListener('focusout',()=>queueMicrotask(()=>{if(!this.root.contains(document.activeElement))this.details.open=false;}));
  }
  select(destination:ProjectDestination|null):void{
    for(const [key,b]of this.buttons)if(key===destination)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
    this.root.dataset.destination=destination??'';
  }
}
