/** The Project's front page (docs/superpowers/specs/2026-11-05-project-front-page-design.md): what the Project is, the
 * Project's own page, where the work is (Now, Just finished, Next in the plan, Your own next step) and how it was made.
 * Everything comes from retained records: Studies, their observed executions, Hooke researchers and the Project's
 * front page record (project_front.py). It starts nothing; the words it shows are the records', never a model's. */
import type {ResearchSession} from './protocol';
import {isWorking} from './research-state';
import {todaysResearch} from './shelf';
import {studyName, type Effort} from './research-efforts';
import {contributors, history, studyActivity, experimentCount, operationPhase, type ActivitySnapshot, type HistoryEvent} from './project-research-history';
const node = <K extends keyof HTMLElementTagNameMap>(tag:K,text='',className='')=>{
  const item=document.createElement(tag);item.textContent=text;item.className=className;return item;
};
export type FrontPage={id:string;revision:number;digest:string;title?:string;name?:string;heading?:string|null;savedAt?:number;unavailable?:boolean};
export type FrontStep={id:string;text:string;study?:string;when?:string;by?:string;byLabel?:string;at?:number;done?:{byLabel?:string;at?:number}};
export type FrontVisual={id:string;revision:number;digest:string;name?:string;kind?:'image'|'page'|null;sha256?:string;unavailable?:boolean};
export type ProjectFrontState={about:string|null;page:FrontPage|null;visual?:FrontVisual|null;next:FrontStep[];revision:number;updatedAt?:number};
export type PageCandidate={id:string;revision:number;title:string};
type Actions={current():string;openStudy(id:string):void;data():void;inputs():void;openResearcher?(id:string):void;retry?():void;startResearch?():void;camera?():void;
  /** Opens the Project's page in a reader Tab, at its exact revision. */
  openPage?(page:FrontPage):void;
  /** Puts these words in the Assistant's composer and opens it; nothing is sent. */
  draft?(text:string):void;
  saveAbout?(text:string|null):Promise<void>;
  setPage?(page:PageCandidate|null):Promise<void>;
  /** The Project's main visual: its cover wherever Projects are listed (project-library.ts). */
  setVisual?(visual:PageCandidate|null):Promise<void>;
  addStep?(text:string):Promise<void>;
  finishStep?(id:string):Promise<void>;
};
export type ProjectMaterialSummary={count:number;groups:readonly string[];error?:string;loading?:boolean;slow?:boolean;pages?:readonly PageCandidate[];visuals?:readonly PageCandidate[]};
export type ProjectFrontRead={name:string;front:ProjectFrontState|null;error?:string};
/** A Study's words on one line: the first sentence of a long statement, the rest kept in its tooltip. */
function line(text:string):string{const first=text.replace(/\s+/g,' ').trim();return first.length>160?`${first.slice(0,159).trimEnd()}…`:first;}
function when(at?:number):string{
  if(typeof at!=='number'||!Number.isFinite(at)||at<=0)return 'Time not recorded';
  const date=new Date(at*1000),today=new Date();
  return date.toDateString()===today.toDateString()?date.toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}):date.toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'});
}
const day=(at?:number)=>typeof at==='number'&&at>0?new Date(at*1000).toLocaleDateString(undefined,{month:'short',day:'numeric'}):'';
/** A Study's state for its card: its lifecycle words, never a guessed completion. */
function studyPill(study:Effort):{words:string;kind:string}{
  if(study.status==='stopped')return {words:'Closed',kind:'done'};
  if(study.status==='paused')return {words:'Paused',kind:'paused'};
  return {words:'Open',kind:'open'};
}
/** A just-finished event is a result: a report, a finding, a run that ended, an answered question. */
const RESULTS=new Set<HistoryEvent['kind']>(['report','researcher-report','finding','run-ended','answer']);
const isResult=(event:HistoryEvent)=>RESULTS.has(event.kind);
export class ProjectResearchHome {
  readonly root=node('section');
  private seenSession='';private seenAt=0;private seenOpen=false;
  private front:ProjectFrontRead={name:'',front:null};
  private editing:''|'about'|'step'='';private pageMenu=false;private visualMenu=false;private notice='';private expanded=new Set<string>();
  enter(session:string):void{
    if(this.seenSession!==session||!this.seenOpen){
      this.seenSession=session;this.seenOpen=true;
      try{this.seenAt=Number(localStorage.getItem(`hooke:project-seen:${session}`))||0;localStorage.setItem(`hooke:project-seen:${session}`,String(Date.now()/1000));}catch{this.seenAt=0;}
      this.signature='';
    }
  }
  leave():void{this.seenOpen=false;}
  private last?:Parameters<ProjectResearchHome['update']>;
  private session='';private signature='';private opening=0;
  private readonly host:HTMLElement; private readonly actions:Actions;
  constructor(host:HTMLElement,actions:Actions){
    this.host=host;this.actions=actions;
    this.root.className='project-research-home project-front';this.root.setAttribute('aria-label','Research in this Project');
    this.root.hidden=true;host.append(this.root);
  }
  /** The Project's name and front page record; a later update draws them. */
  setFront(read:ProjectFrontRead):void{this.front=read;this.signature='';if(this.last)this.update(...this.last);}
  private redraw():void{this.signature='';if(this.last)this.update(...this.last);}
  update(session:string,studies:readonly Effort[],error='',activity:Record<string,ActivitySnapshot>={},researchers:readonly ResearchSession[]=[],material:ProjectMaterialSummary={count:0,groups:[]}):void{
    this.last=[session,studies,error,activity,researchers,material];
    const owned=studies.filter(s=>s.session===session);
    const local=todaysResearch(researchers,session);
    const signature=JSON.stringify([session,error,material,owned,activity,local.map(r=>[r.rid,r.status,r.name,r.brief,r.mission?.status]),this.front,this.editing,this.pageMenu,this.visualMenu,this.notice,[...this.expanded],Math.floor(Date.now()/60000)]);
    if(signature===this.signature)return;
    const focus=(document.activeElement as HTMLElement|null)?.dataset.researchFocus;
    const same=this.session===session;if(!same){this.opening++;this.editing='';this.pageMenu=false;this.visualMenu=false;this.notice='';this.expanded.clear();}
    this.signature=signature;this.session=session;const opening=this.opening;
    const populated=!!(owned.length||local.length||material.count||this.front.front?.page||this.front.front?.about);
    const waiting=!populated&&!!material.loading;
    const unavailable=!populated&&!!(error||material.error);
    const shown=populated||waiting||unavailable||!!this.actions.startResearch;
    this.root.hidden=!shown;this.host.dataset.projectResearch=String(shown);this.root.replaceChildren();
    if(!shown)return;
    const live=()=>this.opening===opening&&this.actions.current()===session;
    const action=(label:string,key:string,run:()=>void,cls='quiet')=>{
      const b=node('button',label,cls);b.type='button';b.dataset.researchFocus=key;
      b.addEventListener('click',()=>{if(live())run();});return b;
    };
    const attempt=(work:Promise<void>|undefined,done:()=>void)=>{if(!work)return;void work.then(()=>{if(live()){this.notice='';done();this.redraw();}},(failure:unknown)=>{if(live()){this.notice=failure instanceof Error?failure.message:String(failure);this.redraw();}});};
    if(waiting||unavailable){
      const status=node('p',unavailable?'Project history could not be loaded.':material.slow?'Loading is taking longer than usual.':'Opening your Project…','project-research-caption');
      status.setAttribute('role','status');this.root.append(status);
      if(unavailable||material.slow){
        this.root.append(node('p',[error,material.error].filter(Boolean).join(' '),'project-research-notice'));
        if(this.actions.retry)this.root.append(action('Retry','retry',this.actions.retry,'mm-primary'));
        this.root.append(action('Browse Project data','data',this.actions.data));
      }
      return;
    }
    const front=this.front.front;
    const events=owned.flatMap(study=>history(study,activity[study.id]).map(event=>({event,study}))).sort((a,b)=>(b.event.at??0)-(a.event.at??0));

    // 1. What this Project is.
    const ident=node('header','','pf-ident');
    const words=node('div','','pf-words');
    words.append(node('span','Project','pf-eyebrow'),node('h2',this.front.name||'This Project','pf-name'));
    if(this.editing==='about'&&this.actions.saveAbout){
      const form=node('form','','pf-about-form');const field=node('textarea','','pf-about-field');
      field.value=front?.about??'';field.maxLength=600;field.rows=2;field.setAttribute('aria-label','What this Project is, in one sentence');
      field.placeholder='What is this Project for? One sentence anyone could read.';field.dataset.researchFocus='about-field';
      const save=node('button','Save','mm-primary');save.type='submit';
      form.append(field,save,action('Cancel','about-cancel',()=>{this.editing='';this.redraw();}));
      form.addEventListener('submit',event=>{event.preventDefault();if(!live())return;const text=field.value.trim();
        attempt(this.actions.saveAbout?.(text||null),()=>{this.editing='';});});
      words.append(form);queueMicrotask(()=>{if(live()&&field.isConnected)field.focus();});
    }else if(front?.about){
      const about=node('p',front.about,'pf-about');words.append(about);
      if(this.actions.saveAbout)words.append(action('Edit','about-edit',()=>{this.editing='about';this.redraw();},'quiet pf-edit'));
    }else if(this.actions.saveAbout){
      words.append(action('Say in one sentence what this Project is','about-add',()=>{this.editing='about';this.redraw();},'quiet pf-about-add'));
    }
    const team=contributors(owned).filter(c=>c.origin!=='Hooke system');
    for(const r of local)if(!team.some(c=>c.id===`researcher:${r.rid}`))team.push({id:`researcher:${r.rid}`,label:r.name,origin:'Hooke researcher',experiments:0,reports:0,replies:0});
    const started=owned.map(s=>s.createdAt).filter((t):t is number=>typeof t==='number'&&t>0).sort((a,b)=>a-b)[0];
    const meta=node('div','','pf-meta');
    meta.append(node('span',[started?`Since ${day(started)}`:'',`${owned.length} ${owned.length===1?'Study':'Studies'}`,material.loading?'':`${material.count} ${material.count===1?'input':'inputs'}`].filter(Boolean).join(' · ')));
    if(team.length){const faces=node('span','','pf-faces');faces.title=team.map(c=>`${c.label} · ${c.origin}`).join('\n');
      for(const c of team.slice(0,5)){const face=node('span',(c.label.trim()[0]??'?').toUpperCase(),'pf-face');face.dataset.origin=c.origin;faces.append(face);}
      faces.setAttribute('aria-label',`Contributors: ${team.map(c=>c.label).join(', ')}`);meta.append(faces);}
    if(this.actions.setVisual){
      const visual=front?.visual,label=visual&&!visual.unavailable?`Main visual · ${visual.name||'chosen'}`:'Set a main visual';
      const toggle=action(this.visualMenu?'Close':label,'visual-change',()=>{this.visualMenu=!this.visualMenu;this.redraw();},'quiet pf-visual');
      toggle.title='The picture or page shown as this Project’s cover when you open Projects';meta.append(toggle);
    }
    ident.append(words,meta);this.root.append(ident);
    if(this.visualMenu&&this.actions.setVisual){
      const menu=node('div','','pf-page-menu pf-visual-menu');menu.setAttribute('role','group');menu.setAttribute('aria-label','Choose the main visual');
      menu.append(node('p','Main visual: the cover this Project shows when you open Projects.','pf-hint'));
      const visuals=material.visuals??[];
      if(!visuals.length)menu.append(node('p','No picture or .html page in this Project yet. Add one with Add material.','pf-hint'));
      for(const candidate of visuals){const chosen=front?.visual?.id===candidate.id&&front.visual.revision===candidate.revision;
        const b=action(chosen?`${candidate.title} · current`:candidate.title,`visual:${candidate.id}:${candidate.revision}`,()=>attempt(this.actions.setVisual?.(candidate),()=>{this.visualMenu=false;}),'quiet pf-page-choice');
        b.disabled=chosen;menu.append(b);}
      if(front?.visual)menu.append(action('No main visual','visual:none',()=>attempt(this.actions.setVisual?.(null),()=>{this.visualMenu=false;}),'quiet pf-page-choice'));
      this.root.append(menu);
    }
    if(error){const saved=node('p',`Showing saved history. ${error}`,'project-research-notice');saved.setAttribute('role','status');this.root.append(saved);}
    if(material.error)this.root.append(node('p',material.error,'project-research-notice'));
    if(this.front.error)this.root.append(node('p',this.front.error,'project-research-notice'));
    if((error||material.error||this.front.error)&&this.actions.retry)this.root.append(action('Retry','retry',this.actions.retry));
    if(this.notice){const notice=node('p',this.notice,'project-research-notice');notice.setAttribute('role','alert');this.root.append(notice);}

    // 2. The Project itself: its page.
    const it=node('section','','pf-it');it.setAttribute('aria-label',"This Project's page");
    const page=front?.page;
    const pages=material.pages??[];
    const pageChoices=()=>{
      const menu=node('div','','pf-page-menu');menu.setAttribute('role','group');menu.setAttribute('aria-label',"Choose the Project's page");
      if(!pages.length)menu.append(node('p','No .html file in this Project yet. Add one with Add material, or ask your agent to write the page.','pf-hint'));
      for(const candidate of pages){const chosen=page?.id===candidate.id&&page.revision===candidate.revision;
        const b=action(chosen?`${candidate.title} · current`:candidate.title,`page:${candidate.id}:${candidate.revision}`,()=>attempt(this.actions.setPage?.(candidate),()=>{this.pageMenu=false;}),'quiet pf-page-choice');
        b.disabled=chosen;menu.append(b);}
      if(page)menu.append(action('Show no page','page:none',()=>attempt(this.actions.setPage?.(null),()=>{this.pageMenu=false;}),'quiet pf-page-choice'));
      return menu;
    };
    const brief=(update:boolean)=>`In Hooke Project ${session}, ${update?"update the Project's page":"make the Project's page"}: one self-contained .html file that presents this Project at its most experiential — what it is, its current result, how it works, how it was decided, and its evidence. Build it only from this Project's retained records (project.state, its Studies, their reports and files), cite each number's file, embed any pictures or video as data: URLs (the page loads nothing from the network), import it with project.import, then set it with project.front action "page". Do not invent results.`;
    const copy=(update:boolean)=>action(update?'Copy a brief to update it':'Copy a brief for your agent',update?'page-brief-update':'page-brief',()=>{
      const text=brief(update);void Promise.resolve().then(()=>navigator.clipboard.writeText(text)).then(()=>{if(live()){this.notice='Copied. Paste it into your connected agent; the page appears here once it is set.';this.redraw();}},()=>{if(live())this.actions.draft?.(text);});
    });
    if(page&&!page.unavailable){
      const preview=node('div','','pf-preview');
      const frame=node('iframe','','pf-frame');
      frame.src=`/api/scientific-files/${encodeURIComponent(page.id)}/page?session=${encodeURIComponent(session)}&revision=${page.revision}`;
      frame.setAttribute('sandbox','allow-scripts');frame.setAttribute('referrerpolicy','no-referrer');frame.tabIndex=-1;
      frame.setAttribute('aria-hidden','true');frame.title=`Preview of ${page.heading||page.title||page.name||"the Project's page"}`;frame.loading='lazy';
      const open=node('button','','pf-preview-open');open.type='button';open.dataset.researchFocus='page-preview';
      open.setAttribute('aria-label',`Open the page: ${page.heading||page.title||page.name||'the Project page'}`);
      open.addEventListener('click',()=>{if(live())this.actions.openPage?.(page);});
      preview.append(frame,open);
      const side=node('div','','pf-it-side');
      side.append(node('span',"This Project's page",'pf-eyebrow'),node('h3',page.heading||page.title||page.name||'The Project page','pf-it-title'),
        node('p','The Project in one place: what you would read, watch or send to someone. Every number on it should point back to its evidence here.','pf-it-text'));
      const newer=events.filter(({event})=>isResult(event)&&(event.at??0)>(page.savedAt??0));
      const fresh=node('p',newer.length?`${newer.length} newer ${newer.length===1?'result':'results'} since this page was saved ${when(page.savedAt)}`:`Up to date with everything up to ${when(page.savedAt)}`,newer.length?'pf-fresh pf-behind':'pf-fresh');
      side.append(fresh);
      const buttons=node('div','','pf-it-actions');
      if(this.actions.openPage)buttons.append(action('Open the page','page-open',()=>this.actions.openPage!(page),'mm-primary'));
      if(newer.length)buttons.append(copy(true));
      if(this.actions.setPage)buttons.append(action(this.pageMenu?'Close':'Change page','page-change',()=>{this.pageMenu=!this.pageMenu;this.redraw();}));
      side.append(buttons);if(this.pageMenu&&this.actions.setPage)side.append(pageChoices());
      it.append(preview,side);
    }else{
      it.classList.add('pf-it-empty');
      const side=node('div','','pf-it-side');
      side.append(node('span',"This Project's page",'pf-eyebrow'),node('h3',page?.unavailable?"The chosen page can't be read":'No page yet','pf-it-title'),
        node('p',page?.unavailable?'Its saved revision is unavailable here. Choose another page or restore the file.':
          'A page is the Project in one place: a challenge entry with its result, a proof with each step checked, a short paper with its time-lapse, a lesson. It is one .html file kept in this Project.','pf-it-text'));
      const buttons=node('div','','pf-it-actions');buttons.append(copy(false));
      if(this.actions.setPage)buttons.append(action(this.pageMenu?'Close':pages.length?'Choose an .html file':'Choose a file','page-change',()=>{this.pageMenu=!this.pageMenu;this.redraw();}));
      side.append(buttons);if(this.pageMenu&&this.actions.setPage)side.append(pageChoices());
      it.append(side);
    }
    this.root.append(it);

    // 3. Where the work is.
    this.root.append(node('h3','Where the work is','pf-section'));
    const board=node('div','','pf-board');
    const column=(title:string,key:string,note='')=>{const col=node('section','','pf-col');col.dataset.column=key;col.setAttribute('aria-label',title);
      const head=node('header');head.append(node('span','','pf-dot'),node('b',title));if(note)head.append(node('span',note,'pf-note'));col.append(head);board.append(col);return col;};
    const item=(col:HTMLElement,title:string,detail:string,tag?:string,button?:HTMLButtonElement,full?:string)=>{
      const row=node('div','','pf-item');const t=node('strong',line(title));if(full&&full!==t.textContent)t.title=full;row.append(t);
      if(detail)row.append(node('small',detail));
      if(tag||button){const foot=node('div','','pf-item-foot');if(tag)foot.append(node('span',tag,'pf-tag'));if(button)foot.append(button);row.append(foot);}
      col.append(row);return row;};
    const studyTag=(id:string)=>{const s=owned.find(x=>x.id===id);return s?studyName(s):undefined;};
    // Now: admitted runs, working researchers and live claims.
    const now=column('Now','now');let running=0;
    for(const study of owned){const seen=studyActivity(study,activity[study.id]);
      for(const op of seen.operations)if(operationPhase(op)==='running'){running++;item(now,op.workflow?.definition?.name||'Workflow experiment',`${op.byLabel} · since ${when(op.at)}`,studyName(study),action('Open','now:'+op.id,()=>this.actions.openStudy(study.id)));}
      for(const [id,task] of Object.entries(study.tasks??{})){const m=task.collaboration;
        if(task.specification?.provider==='collaboration'&&task.status==='running'&&typeof m?.leaseUntil==='number'&&m.leaseUntil>=Date.now()/1000){running++;
          const a=task.specification.assignment as {objective?:string}|undefined;
          item(now,a?.objective||'Research assignment',`${m.claimedBy?.byLabel||'A contributor'} · claimed ${when(m.claimedAt)}`,studyName(study),action('Open','now:'+id,()=>this.actions.openStudy(study.id)),a?.objective);}}}
    for(const r of local.filter(isWorking)){running++;
      item(now,r.brief||r.name,`${r.name} · ${r.status}`,'Hooke researcher',this.actions.openResearcher?action('Open','now:'+r.rid,()=>this.actions.openResearcher!(r.rid)):undefined);}
    if(!running){const last=events.find(({event})=>isResult(event));
      const empty=node('p','','pf-empty');empty.append(node('b','Nothing is running.'),node('span',last?` Last result ${when(last.event.at)}.`:' Work started here or by a connected agent shows here, with who is doing it.'));now.append(empty);}
    now.querySelector('.pf-note')?.remove();now.querySelector('header')?.append(node('span',running?`${running} running`:'quiet','pf-note'));
    // Just finished: the newest results.
    const finished=column('Just finished','finished');
    const results=events.filter(({event})=>isResult(event));
    for(const {event,study} of results.slice(0,4)){
      const row=item(finished,event.title,`${when(event.at)} · ${event.actor}`,studyName(study),action('Open','finished:'+study.id+':'+event.id,()=>this.actions.openStudy(study.id)),event.title);
      if((event.at??0)>this.seenAt&&this.seenAt)row.classList.add('pf-new');}
    if(!results.length)finished.append(node('p','Reports, findings and finished runs show here as they arrive.','pf-empty'));
    else if(results.length>4)finished.append(action(`${results.length-4} earlier`,'finished-more',()=>this.actions.data()));
    // Next in the plan: what the Project's records say is waiting.
    const plan=column('Next in the plan','plan');let planned=0;
    for(const step of (front?.next??[]).filter(s=>!s.done)){planned++;
      item(plan,step.text,[step.when,step.byLabel?`added by ${step.byLabel}`:''].filter(Boolean).join(' · '),step.study?studyTag(step.study):undefined,
        this.actions.finishStep?action('Done','step-done:'+step.id,()=>attempt(this.actions.finishStep?.(step.id),()=>{})):undefined,step.text);}
    // Most actionable first: reviews, lapsed claims, questions, failures, paused Studies, then assignments ready to claim.
    const queue:{rank:number;draw:()=>void}[]=[];
    for(const study of owned){const seen=studyActivity(study,activity[study.id]);const open=(label:string,key:string)=>action(label,key+':'+study.id,()=>this.actions.openStudy(study.id));
      const add=(rank:number,draw:()=>void)=>{planned++;queue.push({rank,draw});};
      if(seen.assignments.review)add(0,()=>item(plan,`Review ${seen.assignments.review} ${seen.assignments.review===1?'report':'reports'}`,'Waiting for you',studyName(study),open('Review','review')));
      if(seen.assignments.lapsed){const lapsed=Object.values(study.tasks??{}).map(t=>t.collaboration?.leaseUntil).filter((t):t is number=>typeof t==='number'&&t<Date.now()/1000).sort((x,y)=>y-x)[0];
        add(1,()=>item(plan,`Close or continue ${seen.assignments.lapsed} claimed ${seen.assignments.lapsed===1?'assignment':'assignments'}`,`Their claim lapsed ${when(lapsed)}; nothing is running for them`,studyName(study),open('Open','lapsed')));}
      if(seen.pending)add(2,()=>item(plan,`${seen.pending} ${seen.pending===1?'question is':'questions are'} waiting for an answer`,'',studyName(study),open('Open','questions')));
      if(seen.counts.failed)add(3,()=>item(plan,`Look at ${seen.counts.failed} failed ${seen.counts.failed===1?'experiment':'experiments'}`,'Kept with their evidence',studyName(study),open('Open','failed')));
      if(study.status==='paused')add(4,()=>item(plan,'Continue or close this Study','Paused',studyName(study),open('Open','paused')));
      if(study.status==='active')for(const [id,task] of Object.entries(study.tasks??{})){
        const a=task.specification?.assignment as {objective?:string;dependsOn?:string[]}|undefined;
        if(task.specification?.provider!=='collaboration'||task.status!=='planned'||!a)continue;
        if((a.dependsOn??[]).some(dep=>study.tasks[dep]?.status!=='completed'))continue;
        add(5,()=>item(plan,a.objective||'Research assignment','Ready to claim',studyName(study),open('Open','ready:'+id),a.objective));}
    }
    for(const entry of queue.sort((a,b)=>a.rank-b.rank))entry.draw();
    if(!planned)plan.append(node('p','Nothing planned is waiting. Add a step, or start something of your own.','pf-empty'));
    if(this.actions.addStep){
      if(this.editing==='step'){const form=node('form','','pf-step-form');const field=node('input','','pf-step-field');field.maxLength=280;field.placeholder='A step for this Project';field.setAttribute('aria-label','A planned step for this Project');field.dataset.researchFocus='step-field';
        const add=node('button','Add','mm-primary');add.type='submit';form.append(field,add,action('Cancel','step-cancel',()=>{this.editing='';this.redraw();}));
        form.addEventListener('submit',event=>{event.preventDefault();if(!live()||!field.value.trim())return;attempt(this.actions.addStep?.(field.value.trim()),()=>{this.editing='';});});
        plan.append(form);queueMicrotask(()=>{if(live()&&field.isConnected)field.focus();});}
      else plan.append(action('Add a step','step-add',()=>{this.editing='step';this.redraw();},'quiet pf-add'));
    }
    plan.querySelector('header')?.append(node('span',planned?`${planned} waiting`:'clear','pf-note'));
    // A long column shows its first four, then says how many more wait behind them.
    for(const col of [now,plan]){const key=col.dataset.column!;const items=Array.from(col.querySelectorAll<HTMLElement>('.pf-item'));
      if(items.length<=5||this.expanded.has(key))continue;
      for(const extra of items.slice(4))extra.remove();
      const more=action(`Show ${items.length-4} more`,'more:'+key,()=>{this.expanded.add(key);this.redraw();},'quiet pf-more');
      const after=col.querySelector('.pf-step-form')??col.querySelector('.pf-add');if(after)after.before(more);else col.append(more);}
    // Your own next step.
    const own=column('Your own next step','own');
    if(this.actions.draft){const form=node('form','','pf-ask');const field=node('textarea','','pf-ask-field');field.rows=3;field.placeholder='What do you want to try next?';
      field.setAttribute('aria-label','What do you want to try next? It goes to the Assistant for you to send.');field.dataset.researchFocus='own-field';
      const go=node('button','Write it to the Assistant','quiet pf-ask-go');go.type='submit';
      form.addEventListener('submit',event=>{event.preventDefault();if(live()&&field.value.trim())this.actions.draft?.(field.value.trim());});
      form.append(field,go);own.append(form);}
    const starts=node('div','','pf-starts');
    const start=(label:string,hint:string,key:string,run:()=>void)=>{const b=action('',key,run,'quiet pf-start');b.append(node('span',label),node('small',hint));starts.append(b);};
    if(this.actions.startResearch)start('Start a Study','a question with a plan','start-research',this.actions.startResearch);
    start('Browse inputs',`${material.count} in this Project`,'material',this.actions.inputs);
    if(this.actions.camera)start('Use the camera','look at a specimen','camera',this.actions.camera);
    start('Project data','everything kept here','data',this.actions.data);
    own.append(starts);
    this.root.append(board);

    // 4. How it was made.
    if(owned.length){
      this.root.append(node('h3','How it was made','pf-section'));
      const path=node('ol','','pf-path');path.setAttribute('aria-label','Studies, oldest first');
      for(const study of [...owned].sort((a,b)=>(a.createdAt??0)-(b.createdAt??0))){
        const pill=studyPill(study);const stop=node('li','','pf-stop');stop.dataset.state=pill.kind;
        const card=action('',`study:${study.id}`,()=>this.actions.openStudy(study.id),'pf-study');
        const span=[day(study.createdAt),study.updatedAt&&day(study.updatedAt)!==day(study.createdAt)?day(study.updatedAt):''].filter(Boolean).join(' – ');
        const out=node('span','','pf-out');const experiments=experimentCount(study),reports=study.claims?.length??0;
        out.append(node('b',experiments?`${experiments} ${experiments===1?'experiment':'experiments'}`:`${reports} ${reports===1?'report':'reports'}`),node('span',pill.words,`pf-pill pf-pill-${pill.kind}`));
        card.append(node('time',span||'Date not recorded'),node('strong',studyName(study)),node('span',study.objective,'pf-objective'),out);
        card.setAttribute('aria-label',`${studyName(study)}, ${pill.words}. Open Study`);
        stop.append(node('span','','pf-rule'),card);path.append(stop);}
      this.root.append(path);
    }
    if(same&&focus)for(const element of this.root.querySelectorAll<HTMLElement>('[data-research-focus]'))if(element.dataset.researchFocus===focus){element.focus();break;}
  }
}
