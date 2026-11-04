/** Project destinations outlive open tabs. This component never admits scientific work. */
export type ProjectDestination='overview'|'studies'|'inputs'|'results'|'activity';
export class ProjectNavigation {
  readonly root=document.createElement('nav');
  private details=document.createElement('details');
  private buttons=new Map<ProjectDestination,HTMLButtonElement>();
  private home=document.createElement('button');
  private choose:(destination:ProjectDestination)=>void;
  constructor(choose:(destination:ProjectDestination)=>void){
    this.choose=choose;
    this.root.className='project-navigation';this.root.setAttribute('aria-label','Project index');
    const summary=document.createElement('summary');summary.textContent='Project index';this.details.append(summary);
    const list=document.createElement('div');list.className='project-navigation-items';
    for(const [key,label]of [['overview','Project overview'],['studies','Studies'],['inputs','Inputs'],['results','Results'],['activity','Activity']]as const){
      const b=document.createElement('button');b.type='button';b.textContent=label;
      b.addEventListener('click',()=>{this.choose(key);this.details.open=false;});this.buttons.set(key,b);list.append(b);
    }
    this.details.append(list);const home=this.home;home.type='button';home.textContent='Project overview';home.title='Studies, results and activity across this Project';home.className='project-overview-link';home.onclick=()=>choose('overview');
    this.root.append(home,this.details);
    this.root.addEventListener('keydown',e=>{if(e.key==='Escape'&&this.details.open){this.details.open=false;summary.focus();e.stopPropagation();}});
    // On macOS a pointer press may blur the summary without focusing its button.
    // Closing on that null focus target removes the button before its click fires.
    this.root.addEventListener('focusout',e=>{const next=e.relatedTarget as Node|null;if(next&&!this.root.contains(next))this.details.open=false;});
  }
  select(destination:ProjectDestination|null):void{
    for(const [key,b]of this.buttons)if(key===destination)b.setAttribute('aria-current','page');else b.removeAttribute('aria-current');
    if(destination==='overview')this.home.setAttribute('aria-current','page');else this.home.removeAttribute('aria-current');
    this.root.dataset.destination=destination??'';
  }
}
