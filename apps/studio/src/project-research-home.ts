/** Project orientation from retained Studies and observed original executions. */
import type {ResearchSession} from './protocol';
import {isWorking} from './research-state';
import {studyName, studyState, type Effort} from './research-efforts';
import {contributors, actorOrigin, history, researchDate, studyActivity, type ActivitySnapshot} from './project-research-history';
const node = <K extends keyof HTMLElementTagNameMap>(tag:K,text='',className='')=>{
  const item=document.createElement(tag);item.textContent=text;item.className=className;return item;
};
type Actions={current():string;openStudy(id:string):void;data():void;inputs():void;openResearcher?(id:string):void};
export class ProjectResearchHome {
  readonly root=node('section');
  private session='';private signature='';private opening=0;
  private readonly host:HTMLElement; private readonly actions:Actions;
  constructor(host:HTMLElement,actions:Actions){
    this.host=host;this.actions=actions;
    this.root.className='project-research-home';this.root.setAttribute('aria-label','Research in this Project');
    this.root.hidden=true;host.append(this.root);
  }
  update(session:string,studies:readonly Effort[],error='',activity:Record<string,ActivitySnapshot>={},researchers:readonly ResearchSession[]=[]):void{
    const owned=studies.filter(s=>s.session===session);
    const local=researchers.filter(r=>r.day===session&&r.purpose==='research');
    const signature=JSON.stringify([session,owned,error,activity,local.map(r=>[r.rid,r.status,r.name,r.brief,r.engine,r.mission?.status])]);if(signature===this.signature)return;
    const disclosures=new Map(Array.from(this.root.querySelectorAll('details')).map(d=>[d.dataset.disclosure,d.open]));
    const focus=(document.activeElement as HTMLElement|null)?.dataset.researchFocus;
    const same=this.session===session;if(!same){disclosures.clear();this.opening++;}
    this.signature=signature;this.session=session;const opening=this.opening;
    this.root.hidden=!owned.length;this.host.dataset.projectResearch=String(owned.length>0);this.root.replaceChildren();if(!owned.length)return;
    const action=(label:string,key:string,run:()=>void,cls='quiet')=>{
      const b=node('button',label,cls);b.type='button';b.dataset.researchFocus=key;
      b.addEventListener('click',()=>{if(this.opening===opening&&this.actions.current()===session)run();});return b;
    };
    const details=(label:string,key:string,cls:string)=>{const d=node('details','',cls);d.dataset.disclosure=key;
      const summary=node('summary',label);summary.dataset.researchFocus=key;d.append(summary);return d;};
    const team=contributors(owned);
    for(const r of local)if(!team.some(c=>c.id===`researcher:${r.rid}`))team.push({id:`researcher:${r.rid}`,label:r.name,origin:'Hooke researcher',experiments:0,reports:0,replies:0});
    const agentCount=team.filter(c=>c.origin==='Connected agent'||c.origin==='Hooke researcher').length;
    const experiments=owned.reduce((n,s)=>n+Object.keys(s.experiments??{}).length+Object.values(s.tasks??{}).filter(t=>t.specification?.provider!=='scientific-gcp-grant').length,0);
    const start=owned.map(s=>s.createdAt).filter((t):t is number=>typeof t==='number'&&Number.isFinite(t)&&t>0).sort((a,b)=>a-b)[0];
    const intro=node('header','','project-research-intro');
    intro.append(node('h2','Research overview'),node('p',start?`First effort created ${researchDate(start)}`:'Creation time not recorded','project-research-caption'));
    this.root.append(intro);
    if(error){const notice=node('p',`Showing saved history. ${error}`,'project-research-notice');notice.setAttribute('role','status');this.root.append(notice);}
    const stats=node('div','','project-research-stats');
    for(const [label,count] of [['Studies',owned.length],['Agents contributed',agentCount],['Experiments',experiments],['Findings',owned.reduce((n,s)=>n+s.findings.length,0)]] as const){
      const item=node('div','','project-research-stat');item.append(node('strong',String(count)),node('span',label));stats.append(item);
    }
    this.root.append(stats);
    if(team.length){const people=details('Contributors & origins','team','project-research-team');
      for(const c of team){const row=node('div','','project-research-person');
        const who=node('div');who.append(node('strong',c.label),node('span',c.origin));
        const work=node('span',[c.experiments?`${c.experiments} experiments`:'',c.reports?`${c.reports} reports`:'',c.replies?`${c.replies} replies`:''].filter(Boolean).join(' · ')||'Initiated or joined the discussion');
        row.title=c.id;row.append(who,work);people.append(row);
      }
      people.append(node('p','Recorded contributions, not live presence. Separate connections count separately.','project-research-caption'));this.root.append(people);
    }
    if(local.length){const workers=details(`Hooke researchers · ${local.length}`,'local-researchers','project-research-team');
      for(const r of local){const row=node('div','','project-research-person');const words=node('div');words.append(node('strong',r.name),node('span',`${r.status} · ${r.engine||'Model not recorded'}`),node('span',r.brief));row.append(words);
        if(this.actions.openResearcher)row.append(action('Open researcher',`researcher:${r.rid}`,()=>this.actions.openResearcher!(r.rid)));workers.append(row);}
      this.root.append(node('p',`${local.filter(isWorking).length} Hooke researchers working in this Project`,'project-research-caption'),workers);
    }
    for(const s of owned){
      const open=()=>this.actions.openStudy(s.id),key=`study:${s.id}`;
      const observed=studyActivity(s,error?{operations:activity[s.id]?.operations??[],error}:activity[s.id]);
      const card=node('article','','project-research-summary'),heading=node('div','','project-research-heading');
      heading.append(node('span','Study · research effort','project-research-eyebrow'));
      const lifecycle=node('span',s.status==='active'?'Open for research':studyState(s),'project-research-state');lifecycle.title='Study lifecycle, separate from current execution.';heading.append(lifecycle);
      card.append(heading,node('h3',studyName(s)));
      const origin=s.createdBy?`${s.createdBy.byLabel} · ${actorOrigin(s.createdBy.by)}`:'Initiator not recorded';
      card.append(node('p',`Created ${researchDate(s.createdAt)} · ${origin}`,'project-research-origin'));
      const question=details('Question & success criteria',key+':question','project-research-question');
      question.append(node('p',s.objective));if(s.criteria)question.append(node('p',s.criteria,'project-research-criteria'));card.append(question);
      const now=node('div','','project-research-now');now.dataset.activity=observed.unavailable?'unknown':observed.counts.running?'running':'idle';
      now.append(node('span','Now','project-research-eyebrow'),node('strong',observed.title));
      const parts=[observed.counts.finished?`${observed.counts.finished} finished`:'',observed.counts.failed?`${observed.counts.failed} failed attempts retained`:'',observed.pending?`${observed.pending} unanswered questions`:''].filter(Boolean);
      now.append(node('p',observed.unavailable?'Open the Study to inspect original executions.':parts.join(' · ')||'Ready for the next experiment.','project-research-caption'));card.append(now);
      const latest=[...(s.claims??[])].sort((a,b)=>(a.at??0)-(b.at??0)).at(-1);
      if(latest){const report=details('',key+':report:'+latest.request,'project-research-report');const summary=report.querySelector('summary')!;
        const meta=node('span','','project-research-report-meta');meta.append(node('span','Latest report'),node('span',`${latest.byLabel||'Author not recorded'} · ${researchDate(latest.at)}`));
        summary.append(meta,node('span',latest.statement,'project-research-report-preview'),node('span','Read report','project-research-report-link'));
        report.append(node('p',latest.statement,'project-research-report-full'));if(latest.limitations)report.append(node('p',latest.limitations,'project-research-criteria'));
        report.append(node('p','Researcher report · not an accepted finding','project-research-caption'));card.append(report);
      }else card.append(node('p','Reports will appear here as the work develops.','project-research-empty'));
      const timeline=details('Research history',key+':history','project-research-history');
      const events=history(s,activity[s.id]);const list=node('ol');
      for(const event of events){const row=node('li');row.append(node('time',researchDate(event.at)),node('strong',event.title),node('span',`${event.actor} · ${event.detail}`));
        if(event.run)row.append(action('Inspect experiment',key+':event:'+event.id,open));list.append(row);}
      timeline.append(list);if(!events.length)timeline.append(node('p','No dated history recorded yet.'));
      const footer=node('div','','project-research-study-footer');
      footer.append(node('span',`${s.inputs.length} inputs · ${s.claims?.length??0} reports`,'project-research-caption'),action('Open Study',key+':open',open,'mm-primary'));
      card.append(timeline,footer);this.root.append(card);
    }
    const choices=node('div','','project-research-actions');choices.append(action('Project data','data',this.actions.data),action('Add material','inputs',this.actions.inputs));this.root.append(choices);
    for(const d of this.root.querySelectorAll('details'))d.open=disclosures.get(d.dataset.disclosure)??false;
    if(same&&focus)for(const item of this.root.querySelectorAll<HTMLElement>('[data-research-focus]'))if(item.dataset.researchFocus===focus){item.focus();break;}
  }
}
