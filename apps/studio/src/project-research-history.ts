/** Read models of retained authorship and original execution receipts. Never dispatches work. */
import {isScientificTask, taskPhase, type Effort} from './research-efforts';
export type ResearchOperation = {
  id:string; project:string; by?:string; byLabel:string; at?:number; endedAt?:number; state:string;
  workflow?:{updatedAt?:number;id:string;session:string;status:string;definition?:{name:string};executionObservation?:{state:string;reason:string}};
};
export type ActivitySnapshot = {operations:ResearchOperation[]; study?:Effort; error?:string};
export type Contributor = {id:string; label:string; origin:string; experiments:number; reports:number; replies:number};
/** `kind` says which record an event is, so a view picks results by record, never by its words. */
export type HistoryKind = 'created'|'run'|'run-ended'|'claim'|'report'|'plan'|'review'|'finding'|'researcher-report'|'challenge'|'question'|'answer';
export type HistoryEvent = {id:string; kind:HistoryKind; at?:number; title:string; detail:string; actor:string; study:string; run?:string};
export function actorOrigin(id:string):string {
  if(id.startsWith('agent:ac_'))return 'Connected agent';
  if(['agent:chat','agent:voice'].includes(id))return 'Hooke Assistant';
  if(id==='agent:system')return 'Hooke system';
  if(id==='agent:researcher')return 'Hooke researcher';
  if(id==='studio'||id==='user')return 'Person in Hooke';
  if(id.startsWith('researcher:'))return 'Hooke researcher';
  return 'Recorded contributor';
}
/** Assignments coordinate people/agents; only admitted scientific work is an experiment. */
export function experimentCount(study:Effort):number {
  return Object.keys(study.experiments??{}).length+Object.values(study.tasks??{}).filter(t=>
    isScientificTask(t)&&!!t.execution).length;
}
export function contributors(studies:readonly Effort[]):Contributor[] {
  const found=new Map<string,Contributor>();
  const add=(id:string|undefined,label:string|undefined,kind?:'experiments'|'reports'|'replies')=>{
    if(!id)return; // An old display label is not a unique identity.
    let row=found.get(id);if(!row){row={id,label:label||'Unnamed contributor',origin:actorOrigin(id),experiments:0,reports:0,replies:0};found.set(id,row);}
    if(kind)row[kind]++;
  };
  for(const s of studies){
    add(s.createdBy?.by,s.createdBy?.byLabel);
    for(const e of Object.values(s.experiments??{}))add(e.by,e.byLabel,'experiments');
    for(const c of s.claims??[])add(c.by,c.byLabel,'reports');
    for(const c of s.claimChanges??[])add(c.by,c.byLabel,'reports');
    for(const q of s.questions??[]){add(q.by,q.byLabel);if(q.answer)add(q.answer.by,q.answer.byLabel,'replies');}
    for(const t of Object.values(s.tasks??{})){const c=t.collaboration;if(c){add(c.proposedBy?.by,c.proposedBy?.byLabel);add(c.claimedBy?.by,c.claimedBy?.byLabel,c.report?'reports':undefined);}}
    for(const t of Object.values(s.tasks??{}))if(t.execution?.provider==='local-researcher'&&t.execution.id)
      add(`researcher:${t.execution.id}`,'Hooke researcher','experiments');
  }
  return [...found.values()];
}
export function operationPhase(op:ResearchOperation):'running'|'waiting'|'check'|'finished'|'failed' {
  const observation=op.workflow?.executionObservation?.state;
  if(observation==='interrupted'||op.state==='interrupted')return 'check';
  const state=op.workflow?.status??op.state;
  if(['completed','succeeded','stopped','cancelled'].includes(state))return 'finished';
  if(['failed','rejected'].includes(state))return 'failed';
  if(observation==='unstarted'||observation==='idle'||['admitted','waiting','review','needs_review','awaiting-review','awaiting-controls','awaiting-research','paused','planned'].includes(state))return 'waiting';
  if(['running','stopping','cancelRequested','cancel_requested'].includes(state))return 'running';
  return 'check';
}
export function studyActivity(study:Effort,snapshot?:ActivitySnapshot){
  const ids=new Set(Object.values(study.experiments??{}).map(e=>e.operation));
  const operations=(snapshot?.operations??[]).filter(op=>ids.has(op.id)&&op.project===study.session&&(!op.workflow||op.workflow.session===study.session));
  const counts={running:0,waiting:0,check:0,finished:0,failed:0};
  // `lapsed`: a claim whose 300 s lease ran out with no admitted operation linked to it. Nothing of Hooke's is
  // running for it, so it is ownership to close or continue, never execution to check (HOOKE-GAPS row 23).
  const assignments={waiting:0,working:0,review:0,check:0,lapsed:0};
  for(const op of operations)counts[operationPhase(op)]++;
  for(const task of Object.values(study.tasks??{})){
    if(task.specification?.provider==='scientific-gcp-grant')continue;
    if(task.specification?.provider==='collaboration'){
      if(task.status==='planned')assignments.waiting++;
      else if(task.status==='running'){if(typeof task.collaboration?.leaseUntil==='number'&&task.collaboration.leaseUntil<Date.now()/1000){if(task.collaboration.operations?.length)assignments.check++;else assignments.lapsed++;}else assignments.working++;}
      else if(task.status==='completed')assignments.review++;
      else if(task.status!=='cancelled')assignments.check++;
      continue;
    }
    const phase=taskPhase(task);
    if(['running','launching','cancel_requested'].includes(phase))counts.running++;
    else if(['review','reported','waiting','planned'].includes(phase))counts.waiting++;
    else if(phase==='unknown')counts.check++;
    else if(phase==='failed')counts.failed++;
    else if(['finished','completed','cancelled'].includes(phase))counts.finished++;
    else counts.check++;
  }
  if(study.coordination){assignments.waiting=study.coordination.waiting;assignments.review=study.coordination.reports;assignments.check=Math.max(assignments.check,study.coordination.unresolvedAdmissions);}
  const missing=ids.size-operations.length;
  const pending=(study.questions??[]).filter(q=>!q.answer).length;
  const unavailable=!!snapshot?.error||missing>0;
  // A failed refresh must not perpetuate a stale running/idle claim.
  const title=unavailable?'Activity needs refreshing':counts.running?`${counts.running} experiment${counts.running===1?'':'s'} running`:
    counts.check||assignments.check?'Execution needs checking':assignments.working?'Agent assignment claimed':assignments.lapsed?'A claimed assignment went quiet':assignments.waiting?'Waiting for an agent':counts.waiting||assignments.review?'Waiting for review or a next step':pending?`${pending} question${pending===1?'':'s'} awaiting a reply`:'No experiments running';
  return {counts,assignments,pending,unavailable,title,operations};
}
export function history(study:Effort,snapshot?:ActivitySnapshot):HistoryEvent[]{
  const out:HistoryEvent[]=[];
  const add=(e:Omit<HistoryEvent,'study'>)=>out.push({...e,study:study.id});
  if(study.createdAt)add({id:'created',kind:'created',at:study.createdAt,title:'Research effort created',detail:study.title||study.objective,actor:study.createdBy?.byLabel||'Initiator not recorded'});
  for(const op of studyActivity(study,snapshot).operations)add({id:op.id,kind:['finished','failed'].includes(operationPhase(op))?'run-ended':'run',at:op.workflow?.updatedAt??op.endedAt??op.at,title:op.workflow?.definition?.name||'Workflow experiment',detail:`${op.workflow?.updatedAt||op.endedAt?'Last retained execution change':'Admitted'} · ${snapshot?.error?'last recorded state': 'current state'}: ${operationPhase(op)==='check'?'needs checking':operationPhase(op)}${op.workflow?.executionObservation?.reason?` — ${op.workflow.executionObservation.reason}`:''}`,actor:op.byLabel,run:op.workflow?.id});
  for(const [id,t] of Object.entries(study.tasks??{})){
    if(t.specification?.provider!=='collaboration')continue;
    const a=t.specification.assignment as {objective?:string}|undefined,m=t.collaboration;
    if(m?.claimedAt)add({id:'claim:'+id,kind:'claim',at:m.claimedAt,title:a?.objective||'Research assignment',detail:'Assignment claimed · not proof of active execution',actor:m.claimedBy?.byLabel||'Contributor not recorded'});
    if(m?.report)add({id:'report:'+id,kind:'report',at:m.completedAt,title:m.report.statement,detail:'Assignment report · awaiting review',actor:m.report.byLabel||m.claimedBy?.byLabel||'Contributor not recorded'});
  }
  for(const [index,plan] of [...(study.researchPlanHistory??[]),study.researchPlan].entries())if(plan){
    if(plan.updatedAt)add({id:`plan:${index}`,kind:'plan',at:plan.updatedAt,title:'Research plan saved',detail:plan.desiredOutput,actor:plan.byLabel||'Author not recorded'});
    if(plan.reviewedAt)add({id:`review:${index}`,kind:'review',at:plan.reviewedAt,title:'Research plan reviewed',detail:plan.desiredOutput,actor:plan.reviewedBy?.byLabel||'Reviewer not recorded'});
  }
  for(const f of study.findings??[])add({id:'finding:'+f.request,kind:'finding',at:f.at,title:f.statement,detail:'Accepted finding',actor:f.byLabel||'Reviewer not recorded'});
  for(const c of study.claims??[])add({id:c.request,kind:'researcher-report',at:c.at,title:c.statement,detail:'Researcher report · not an accepted finding',actor:c.byLabel});
  for(const c of study.claimChanges??[])add({id:`challenge:${c.claim}:${c.at}`,kind:'challenge',at:c.at,title:c.reason,detail:'Report challenged',actor:c.byLabel});
  for(const q of study.questions??[]){
    add({id:q.id,kind:'question',at:q.at,title:q.question,detail:q.answer?'Question answered':`Awaiting ${q.recipient.label} · pickup not confirmed`,actor:q.byLabel});
    if(q.answer)add({id:q.id+':answer',kind:'answer',at:q.answer.at,title:q.answer.statement,detail:'Agent reply · not a reviewed finding',actor:q.answer.byLabel});
  }
  return out.sort((a,b)=>(b.at??0)-(a.at??0));
}
/** Uses existing evidence routes; bounded fan-out and no replacement execution ledger. */
export async function readProjectActivity(studies:readonly Effort[],fetcher:typeof fetch=fetch,signal?:AbortSignal):Promise<Record<string,ActivitySnapshot>>{
  const result:Record<string,ActivitySnapshot>={};let index=0;
  const next=async()=>{while(index<studies.length&&!signal?.aborted){const s=studies[index++];
    if(!Object.keys(s.experiments??{}).length&&!Object.keys(s.tasks??{}).length){result[s.id]={operations:[]};continue;}
    try{const r=await fetcher(`/api/research-efforts/${encodeURIComponent(s.id)}/workflows?session=${encodeURIComponent(s.session)}`,{cache:'no-store',signal});
      if(!r.ok)throw new Error('Original execution receipts could not be refreshed.');
      const body=await r.json();if(body.ok!==true||!Array.isArray(body.operations)||body.study?.id!==s.id||body.study?.session!==s.session)throw new Error('Execution receipts did not match this Study.');
      result[s.id]={operations:body.operations,study:body.study};
    }catch(error){result[s.id]={operations:[],error:error instanceof Error?error.message:String(error)};}
  }};
  await Promise.all(Array.from({length:Math.min(3,studies.length)},()=>next()));return result;
}
export function researchDate(at?:number):string {
  return typeof at==='number'&&Number.isFinite(at)&&at>0?new Date(at*1000).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',timeZoneName:'short'}):'Time not recorded';
}
