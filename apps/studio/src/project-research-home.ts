/** Project orientation from retained Studies and observed original executions. */
import type {ResearchSession} from './protocol';
import {isWorking} from './research-state';
import {todaysResearch} from './shelf';
import {studyName, studyState, type Effort} from './research-efforts';
import {contributors, actorOrigin, history, researchDate, studyActivity, experimentCount, type ActivitySnapshot} from './project-research-history';
const node = <K extends keyof HTMLElementTagNameMap>(tag:K,text='',className='')=>{
  const item=document.createElement(tag);item.textContent=text;item.className=className;return item;
};
type Actions={current():string;openStudy(id:string):void;data():void;inputs():void;openResearcher?(id:string):void;retry?():void;startResearch?():void;camera?():void};
export type ProjectMaterialSummary={count:number;groups:readonly string[];error?:string;loading?:boolean;slow?:boolean};
export class ProjectResearchHome {
  readonly root=node('section');
  private seenSession='';private seenAt=0;private seenOpen=false;
  enter(session:string):void{
    if(this.seenSession!==session||!this.seenOpen){
      this.seenSession=session;this.seenOpen=true;
      try{this.seenAt=Number(localStorage.getItem(`hooke:project-seen:${session}`))||0;localStorage.setItem(`hooke:project-seen:${session}`,String(Date.now()/1000));}catch{this.seenAt=0;}
      this.signature='';
    }
  }
  leave():void{this.seenOpen=false;}
  private session='';private signature='';private opening=0;private studyLimit=12;private eventLimit=3;private statusLimit=12;
  private readonly host:HTMLElement; private readonly actions:Actions;
  constructor(host:HTMLElement,actions:Actions){
    this.host=host;this.actions=actions;
    this.root.className='project-research-home';this.root.setAttribute('aria-label','Research in this Project');
    this.root.hidden=true;host.append(this.root);
  }
  update(session:string,studies:readonly Effort[],error='',activity:Record<string,ActivitySnapshot>={},researchers:readonly ResearchSession[]=[],material:ProjectMaterialSummary={count:0,groups:[]}):void{
    const owned=studies.filter(s=>s.session===session);
    const local=todaysResearch(researchers,session);
    const expired=owned.flatMap(s=>Object.values(s.tasks??{})).map(t=>t.status==='running'&&!!t.collaboration?.leaseUntil&&t.collaboration.leaseUntil<Date.now()/1000);
    const signature=JSON.stringify([session,owned,error,activity,material,expired,local.map(r=>[r.rid,r.status,r.name,r.brief,r.engine,r.mission?.status])]);if(signature===this.signature)return;
    const disclosures=new Map(Array.from(this.root.querySelectorAll('details')).map(d=>[d.dataset.disclosure,d.open]));
    const focus=(document.activeElement as HTMLElement|null)?.dataset.researchFocus;
    const same=this.session===session;if(!same){disclosures.clear();this.opening++;this.studyLimit=12;this.eventLimit=3;this.statusLimit=12;}
    this.signature=signature;this.session=session;const opening=this.opening;
    const populated=!!(owned.length||local.length||material.count);
    const waiting=!populated&&!!material.loading;
    const unavailable=!populated&&!!(error||material.error);
    this.root.hidden=!(populated||waiting||unavailable||this.actions.startResearch);this.host.dataset.projectResearch=String(populated||waiting||unavailable||!!this.actions.startResearch);this.root.replaceChildren();
    if(!populated&&!waiting&&!unavailable&&!this.actions.startResearch)return;
    const action=(label:string,key:string,run:()=>void,cls='quiet')=>{
      const b=node('button',label,cls);b.type='button';b.dataset.researchFocus=key;
      b.addEventListener('click',()=>{if(this.opening===opening&&this.actions.current()===session)run();});return b;
    };
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
    if(this.actions.startResearch){
      const start=node('div','','project-research-actions');
      if(this.actions.camera)start.append(action('Use camera','camera',this.actions.camera));
      start.append(action('Add material','add-material',this.actions.inputs), action('Start research','start-research',this.actions.startResearch,'mm-primary'));
      this.root.append(start);
      const guide=node('p',owned.length?'Start another question here, or continue this Project with your connected agent.':'Add your files, then Start research to describe a question and review a plan. A Study keeps that investigation and its results in this Project.','project-research-caption');
      const handoff=node('div','','project-research-actions project-agent-handoff'),message=node('p','','project-research-caption');message.setAttribute('role','status');
      handoff.append(action('Copy instructions for my agent','agent-handoff',()=>{
        message.hidden=false;const prompt=`Use Hooke through its supported connector and installed Hooke skill. Continue in Project ${session}; do not create a duplicate Project. Read project.state, the existing Studies, exact material and prior decisions first. ${owned.length?'Show me what is already planned or running and the next useful step.':'Help me describe a research question, desired output and evaluation; prepare a retained Study and research plan using the selected material.'} Review the plan and limits with me before execution. Use study.coordination and study.coordinate from tool discovery to prepare, claim and return assignments. Use admitted capabilities and frozen workflows; retain exact outputs and limitations in this Project. A connected agent is not necessarily running. Do not change grants, install tools, use paid services or submit results externally. If access or readiness is missing, explain the specific next step. Return the Project and Study locators so I can open the same work in Hooke.`;
        void Promise.resolve().then(()=>navigator.clipboard.writeText(prompt)).then(()=>{if(this.opening===opening&&this.actions.current()===session)message.textContent='Copied. Paste into your connected Claude, Codex or other agent. Work starts there; results stay in this Project.';}).catch(()=>{if(this.opening!==opening||this.actions.current()!==session)return;const text=node('textarea');text.value=prompt;text.readOnly=true;text.setAttribute('aria-label','Instructions for connected agent');handoff.append(text);text.select();message.textContent='Copy these instructions into your connected agent.';});
      }));if(!owned.length)this.root.append(guide);start.append(...Array.from(handoff.children));this.root.append(message);message.hidden=true;
    }
    const details=(label:string,key:string,cls:string)=>{const d=node('details','',cls);d.dataset.disclosure=key;
      const summary=node('summary',label);summary.dataset.researchFocus=key;d.append(summary);return d;};
    const team=contributors(owned);
    for(const r of local)if(!team.some(c=>c.id===`researcher:${r.rid}`))team.push({id:`researcher:${r.rid}`,label:r.name,origin:'Hooke researcher',experiments:0,reports:0,replies:0});
    const agentCount=team.filter(c=>c.origin==='Connected agent'||c.origin==='Hooke researcher').length;
    const experiments=owned.reduce((n,s)=>n+experimentCount(s),0);
    const start=owned.map(s=>s.createdAt).filter((t):t is number=>typeof t==='number'&&Number.isFinite(t)&&t>0).sort((a,b)=>a-b)[0];
    const intro=node('header','','project-research-intro');
    intro.append(node('h2','Project overview'),node('p',start?`First effort created ${researchDate(start)}`:owned.length?'Creation time not recorded':'Keep your sources together, then build on them.','project-research-caption'));
    this.root.prepend(intro);
    if(error){const notice=node('p',`Showing saved history. ${error}`,'project-research-notice');notice.setAttribute('role','status');this.root.append(notice);}
    if(material.error){const notice=node('p',material.error,'project-research-notice');notice.setAttribute('role','status');this.root.append(notice);}
    if(material.slow){const notice=node('p','Loading is taking longer than usual. Showing available material.','project-research-notice');notice.setAttribute('role','status');this.root.append(notice);}
    if((error||material.error||material.slow)&&this.actions.retry)this.root.append(action('Retry','retry',this.actions.retry));
    const sections=node('div','','project-overview-sections');
    const section=(title:string)=>{const card=node('section','','project-overview-section');card.append(node('h3',title));sections.append(card);return card;};
    const purpose=section('Purpose');
    for(const study of owned.slice(0,4))purpose.append(action(studyName(study),'purpose:'+study.id,()=>this.actions.openStudy(study.id)),node('p',study.objective));
    if(!owned.length)purpose.append(node('p','Keep your material here. Ask a quick question, use a tool, or start a Study for a continuing investigation.'));
    const recent=section(this.seenAt?'Since your last visit':'Recent research');
    const events=owned.flatMap(study=>history(study,activity[study.id])).sort((a,b)=>(b.at??0)-(a.at??0));
    const fresh=events.filter(event=>event.at&&event.at>this.seenAt);
    const eventList=node('ol');
    for(const event of fresh.slice(0,this.eventLimit)){const li=node('li');li.append(node('time',researchDate(event.at)),node('span',event.title,'project-event-preview'),node('small',`${event.actor} · ${event.detail}`),action('Inspect evidence','recent:'+event.study+':'+event.id,()=>this.actions.openStudy(event.study)));eventList.append(li);}
    recent.append(eventList);
    if(!fresh.length)recent.append(node('p','No newer dated research events in the loaded history.'));
    if(fresh.length>this.eventLimit)recent.append(action(`Show next ${Math.min(8,fresh.length-this.eventLimit)} changes`,'more-events',()=>{this.eventLimit+=8;this.signature='';this.update(session,studies,error,activity,researchers,material);}));
    recent.append(action('Mark seen','mark-seen',()=>{this.seenAt=Date.now()/1000;try{localStorage.setItem(`hooke:project-seen:${session}`,String(this.seenAt));}catch{/* View state only. */}this.signature='';this.update(session,studies,error,activity,researchers,material);}));
    const now=section('Now'),attention=section('Needs attention'),continuation=section('Continue');
    let issues=0;
    const ordered=[...owned].sort((a,b)=>studyGroup(a,activity[a.id])-studyGroup(b,activity[b.id]));
    for(const study of ordered.slice(0,this.statusLimit)){
      const observed=studyActivity(study,error?{operations:activity[study.id]?.operations??[],error}:activity[study.id]);
      now.append(action(studyName(study),'now:'+study.id,()=>this.actions.openStudy(study.id)),node('p',observed.title));
      const reasons=[observed.unavailable?'Current execution could not be confirmed':'',observed.counts.failed?`${observed.counts.failed} failed experiments`:'',observed.assignments.review?`${observed.assignments.review} reports awaiting review`:'',observed.assignments.check?'Assignment ownership needs checking':'',study.claimChanges?.length?'Challenges are recorded; inspect their evidence':''].filter(Boolean);
      if(reasons.length){issues++;attention.append(action(studyName(study),'attention:'+study.id,()=>this.actions.openStudy(study.id)),node('p',reasons.join(' · ')));}
    }
    if(!owned.length)now.append(node('p',local.length?'Open a researcher to inspect its observed activity.':'No Study work recorded yet.'));
    if(!issues)attention.append(node('p',error?'Refresh the Project to check its current state.':'No attention items in the displayed Studies.'));
    if(ordered.length>this.statusLimit){attention.append(node('p',`Showing ${this.statusLimit} of ${ordered.length} Studies, attention first.`),action('Show more current work','more-status',()=>{this.statusLimit+=12;this.signature='';this.update(session,studies,error,activity,researchers,material);}));}
    if(owned[0])continuation.append(node('p','Inspect the latest evidence, then propose the next experiment in its Study.'),action('Continue investigation','continue',()=>this.actions.openStudy(owned[0].id)));
    else if(this.actions.startResearch)continuation.append(action('Start research','continue',this.actions.startResearch));
    continuation.append(node('small','Connected agents and Hooke researchers contribute to the same Project. Opening work starts nothing.'));
    sections.replaceChildren(purpose,now,attention,continuation,recent);purpose.classList.add('project-overview-purpose');recent.classList.add('project-overview-recent');this.root.append(sections);
    const studyIndex=details(`Studies · ${owned.length}`,'studies','project-overview-studies');
    studyIndex.open=false;
    const stats=node('div','','project-research-stats');
    for(const [label,count] of [['Studies',owned.length],['Agents contributed',agentCount],['Experiments',experiments],['Findings',owned.reduce((n,s)=>n+s.findings.length,0)]] as const){
      const item=node('div','','project-research-stat');item.append(node('strong',String(count)),node('span',label));stats.append(item);
    }
    if(owned.length||local.length)this.root.append(stats);
    const supporting:HTMLElement[]=[];
    if(material.count){
      const materials=node('article','','project-research-material');
      materials.append(node('h3',`${material.count} Project ${material.count===1?'input':'inputs'}`),
        node('p',material.groups.length?material.groups.join(' · '):'Retained files and selected research inputs','project-research-caption'),
        action('Browse inputs','material',this.actions.inputs,owned.length?'quiet':'mm-primary'));
      if(!owned.length&&!material.loading&&!error)materials.append(node('p','No Study yet. Explore your material, then choose Start research to shape a question and plan.','project-research-caption'));
      supporting.push(materials);
    }
    if(team.length){const people=details('Contributors & origins','team','project-research-team');
      for(const c of team){const row=node('div','','project-research-person');
        const who=node('div');who.append(node('strong',c.label),node('span',c.origin));
        const work=node('span',[c.experiments?`${c.experiments} experiments`:'',c.reports?`${c.reports} reports`:'',c.replies?`${c.replies} replies`:''].filter(Boolean).join(' · ')||'Initiated or joined the discussion');
        row.title=c.id;row.append(who,work);people.append(row);
      }
      people.append(node('p','Recorded contributions, not live presence. Separate connections count separately.','project-research-caption'));supporting.push(people);
    }
    if(local.length){const workers=details(`Hooke researchers · ${local.length}`,'local-researchers','project-research-team');
      for(const r of local){const row=node('div','','project-research-person');const words=node('div');words.append(node('strong',r.name),node('span',`${r.status} · ${r.engine||'Model not recorded'}`),node('span',r.brief));row.append(words);
        if(this.actions.openResearcher)row.append(action('Open researcher',`researcher:${r.rid}`,()=>this.actions.openResearcher!(r.rid)));workers.append(row);}
      supporting.push(node('p',`${local.filter(isWorking).length} Hooke researchers working in this Project`,'project-research-caption'),workers);
    }
    let lastGroup=-1;
    for(const s of ordered.slice(0,this.studyLimit)){
      const group=studyGroup(s,activity[s.id]);if(group!==lastGroup){studyIndex.append(node('h3',['Needs attention','Ongoing','Completed or stopped'][group]));lastGroup=group;}
      const open=()=>this.actions.openStudy(s.id),key=`study:${s.id}`;
      const receipt=error?{operations:activity[s.id]?.operations??[],error}:activity[s.id];
      const observed=studyActivity(s,receipt);
      const card=node('article','','project-research-summary'),heading=node('div','','project-research-heading');
      heading.append(node('span','Study · research effort','project-research-eyebrow'));
      const lifecycle=node('span',s.status==='active'?'Open for research':studyState(s),'project-research-state');lifecycle.title='Study lifecycle, separate from current execution.';heading.append(lifecycle);
      const titlebar=node('div','','project-research-titlebar');
      titlebar.append(node('h3',studyName(s)),action('Open Study',key+':open',open,'mm-primary'));
      card.append(heading,titlebar);
      const origin=s.createdBy?`${s.createdBy.byLabel} · ${actorOrigin(s.createdBy.by)}`:'Initiator not recorded';
      card.append(node('p',`Created ${researchDate(s.createdAt)} · ${origin}`,'project-research-origin'));
      const question=details('Question & success criteria',key+':question','project-research-question');
      question.append(node('p',s.objective));if(s.criteria)question.append(node('p',s.criteria,'project-research-criteria'));
      const now=node('div','','project-research-now');now.dataset.activity=observed.unavailable?'unknown':observed.counts.running?'running':'idle';
      now.append(node('span','Now','project-research-eyebrow'),node('strong',observed.title));
      const parts=[observed.counts.finished?`${observed.counts.finished} finished`:'',observed.counts.failed?`${observed.counts.failed} failed experiments retained`:'',observed.pending?`${observed.pending} unanswered questions`:''].filter(Boolean);
      now.append(node('p',observed.unavailable?'Open the Study to inspect original executions.':parts.join(' · ')||(observed.assignments.waiting?'Open the Study and copy its handoff into your connected agent.':observed.assignments.working?'Open the Study to check the claimed assignment. Scientific runs appear separately when admitted.':'Open the Study to review the plan and choose the next step.'),'project-research-caption'));card.append(now);
      const latest=[...(s.claims??[])].sort((a,b)=>(a.at??0)-(b.at??0)).at(-1);
      if(latest){const report=details('',key+':report:'+latest.request,'project-research-report');const summary=report.querySelector('summary')!;
        const meta=node('span','','project-research-report-meta');meta.append(node('span','Latest report'),node('span',`${latest.byLabel||'Author not recorded'} · ${researchDate(latest.at)}`));
        summary.append(meta,node('span',latest.statement,'project-research-report-preview'),node('span','Read report','project-research-report-link'));
        report.append(node('p',latest.statement,'project-research-report-full'));if(latest.limitations)report.append(node('p',latest.limitations,'project-research-criteria'));
        report.append(node('p','Researcher report · not an accepted finding','project-research-caption'));card.append(report);
      }else card.append(node('p','Reports will appear here as the work develops.','project-research-empty'));
      card.append(question);
      const timeline=details('Research history',key+':history','project-research-history');
      const events=history(s,receipt);const list=node('ol');
      for(const event of events.slice(0,20)){const row=node('li');row.append(node('time',researchDate(event.at)),node('strong',event.title),node('span',`${event.actor} · ${event.detail}`));
        if(event.run)row.append(action('View in Study',key+':event:'+event.id,open));list.append(row);}
      timeline.append(list);if(events.length>20)timeline.append(action(`Inspect all ${events.length} events in Study`,key+':more-history',open));if(!events.length)timeline.append(node('p','No dated history recorded yet.'));
      const footer=node('div','','project-research-study-footer');
      footer.append(node('span',`${s.inputs.length} inputs · ${s.claims?.length??0} reports`,'project-research-caption'));
      card.append(timeline,footer);studyIndex.append(card);
    }
    if(owned.length>this.studyLimit)studyIndex.append(action('Show more Studies','more-studies',()=>{this.studyLimit+=12;this.signature='';this.update(session,studies,error,activity,researchers,material);}));
    if(owned.length)this.root.append(studyIndex);
    this.root.append(...supporting);
    const choices=node('div','','project-research-actions');choices.append(action('Project data','data',this.actions.data),action('Add material','inputs',this.actions.inputs));this.root.append(choices);
    for(const d of this.root.querySelectorAll('details'))d.open=disclosures.get(d.dataset.disclosure)??false;
    if(same&&focus)for(const item of this.root.querySelectorAll<HTMLElement>('[data-research-focus]'))if(item.dataset.researchFocus===focus){item.focus();break;}
  }
}

/** Groups use retained lifecycle and observed work, never a guessed completion percentage. */
function studyGroup(study:Effort,snapshot?:ActivitySnapshot):number{
 const seen=studyActivity(study,snapshot);
 if(seen.unavailable||seen.counts.failed||seen.counts.check||seen.assignments.check||seen.assignments.review||study.claimChanges?.length)return 0;
 return study.status==='stopped'?2:1;
}
