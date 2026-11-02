/** Retained research plan and handoff; scientific execution stays in WorkflowService. */
import type {Effort, StudyInput} from './research-efforts';

type Ref={id:string;session:string;revision:number;digest:string};
type Plan={desiredOutput:string;evaluation:string;materials:Ref[];methodNotes:string;actorMode:'connected-agent'|'local-researcher'|'manual';limits:{experiments:number;modelMinutes:number;tokens:number};status?:string;instructionPackage?:{id:string;version:number;digest:string}};
type Assignment={id:string;objective:string;observedState:string;deliverable?:string;dependsOn?:string[];parent?:string;approach?:string;claimedAt?:number;leaseUntil?:number;completedAt?:number;operations?:string[];claimedBy?:{byLabel:string};report?:{statement:string;limitations:string;evidence?:Ref[]}};
type State={basis:Ref;plan:Plan|null;assignments:Assignment[];capabilityProposals?:{id:string;byLabel:string;proposal:{title:string;gap:string;package:Ref;licence:string;dependencies:string;permissions:string;resources:string;proofPlan:string}}[];materials?:{title?:string;available:boolean;reason?:string;material?:{kind:string;format?:string}}[]};
const n=<K extends keyof HTMLElementTagNameMap>(tag:K,text='',cls='')=>{const e=document.createElement(tag);e.textContent=text;e.className=cls;return e;};
async function result(r:Response){const body=await r.json();if(!r.ok||body.ok===false)throw new Error([body.says||body.error||'Research setup could not be saved.',typeof body.next==='string'?body.next:''].filter(Boolean).join(' '));return body;}

export function mountStudyInitiation(host:HTMLElement,study:Effort,inputs:StudyInput[],fetcher:typeof fetch=fetch,openFile?:(ref:Ref,name:string)=>Promise<unknown>):void{
  const box=n('section','','study-initiation');box.setAttribute('aria-label','Research plan and collaborators');
  const heading=n('header');heading.append(n('span','RESEARCH PLAN','study-initiation-eyebrow'),n('h3','From question to experiment'));
  const status=n('p','Loading your saved plan…','study-note');status.setAttribute('role','status');
  const content=n('div');box.append(heading,status,content);host.prepend(box);
  const current=()=>host.contains(box),base=`/api/research-efforts/${encodeURIComponent(study.id)}/coordination`;
  let state:State|null=null,pending=false,amending=false,assignmentLimit=12;
  const draftKey=`hooke:research-plan:${study.session}:${study.id}`;
  const clearDraft=()=>{try{localStorage.removeItem(draftKey);}catch{/* Storage may be unavailable. */}};
  const call=async(action:string,fields:object)=>result(await fetcher(`${base}/${action}`,{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'research-effort'},body:JSON.stringify({session:study.session,...fields})}));
  const button=(label:string,run:()=>Promise<void>,primary=false,busy='Saving…')=>{const b=n('button',label,primary?'study-primary':'study-quiet');b.type='button';b.onclick=()=>{if(pending||!current())return;pending=true;box.dataset.pending='true';b.disabled=true;const previous=status.textContent;status.textContent=busy;void run().then(()=>{if(current()&&status.textContent===busy)status.textContent=previous;}).catch(e=>{if(current())status.textContent=String(e.message||e);}).finally(()=>{pending=false;delete box.dataset.pending;if(current())b.disabled=false;});};return b;};
  function draw(){
    if(!current()||!state)return;content.replaceChildren();delete box.dataset.draft;
    status.textContent=state.plan?.status==='reviewed'?'Plan reviewed. Next, hand the work to your agent or run the tools yourself.':'Save your approach, then review it before handing work to an agent.';
    if(!state.plan||state.plan.status==='draft'||amending){
      const form=n('form'),output=n('textarea'),evaluation=n('textarea'),method=n('textarea'),mode=n('select');
      output.value=state.plan?.desiredOutput||'';evaluation.value=state.plan?.evaluation||study.criteria;method.value=state.plan?.methodNotes||'';
      output.placeholder='For example: predictions, a comparison table and a short report';method.placeholder='Describe the first experiment and what would justify a follow-up';
      for(const [value,label]of [['connected-agent','Connected agent'],['manual','I’ll run the tools'],['local-researcher','Hooke researcher']]){const o=n('option',label);o.value=value;mode.append(o);}mode.value=state.plan?.actorMode||'connected-agent';
      const field=(label:string,e:HTMLElement)=>{const l=n('label');l.append(n('span',label),e);form.append(l);};
      field('What should this produce?',output);field('How will we judge it?',evaluation);field('Proposed approach',method);field('Who will do the work?',mode);
      const materials=n('div','','study-initiation-materials');materials.append(n('h4',`${study.inputs.length} selected inputs`));
      for(const ref of study.inputs){const named=inputs.find(i=>i.id===ref.id&&i.revision===ref.revision&&i.digest===ref.digest);materials.append(n('p',`${named?.title||'Retained input'} · version ${ref.revision}`));}
      if(!study.inputs.length)materials.append(n('p','No material selected. Add the required evidence before planning a data experiment.'));
      for(const item of state.materials||[])if(!item.available)materials.append(n('p',item.reason||'Selected material is unavailable.','study-problem'));
      form.append(materials);
      const bounds=state.plan?.limits||{experiments:2,modelMinutes:30,tokens:60000};
      const limits={experiments:n('input'),modelMinutes:n('input'),tokens:n('input')};
      for(const [key,label,max]of [['experiments','Maximum experiments',32],['modelMinutes','Model minutes',120],['tokens','Model input + output tokens',1000000]]as const){const input=limits[key];input.type='number';input.required=true;input.min='1';input.max=String(max);input.value=String(bounds[key]);field(label,input);}
      form.append(n('p','Hooke enforces the experiment allowance. Your connected harness must enforce and report its model token/time limits before execution.','study-note'));
      const packages=n('select');const none=n('option','No instruction package');none.value='';packages.append(none);field('Research instructions',packages);
      let packageRows:{id:string;version:number;digest:string;content:{title:string}}[]=[],packageId=state?.plan?.instructionPackage?.id||'';
      void fetcher('/api/research-instructions').then(result).then(reply=>{if(!current()||!form.contains(packages))return;packageRows=reply.packages;for(const row of packageRows){const option=n('option',`${row.content.title} · v${row.version}`);option.value=row.id;packages.append(option);}packages.value=packageId;}).catch(()=>{if(current())packages.title='Instruction packages could not be loaded; the plan can still be saved.';});
      let review:HTMLButtonElement|null=null,editVersion=0;
      const remember=()=>{
        editVersion++;
        box.dataset.draft='true';if(review)review.disabled=true;
        status.textContent='Draft saved on this device. Save the research plan before reviewing it.';
        try{localStorage.setItem(draftKey,JSON.stringify({basis:state!.basis.digest,output:output.value,evaluation:evaluation.value,method:method.value,mode:mode.value,limits:Object.fromEntries(Object.entries(limits).map(([k,v])=>[k,v.value])),packageId:packages.value}));}catch{status.textContent='Unsaved draft. Save the research plan before leaving.';}
      };
      form.oninput=e=>{e.stopPropagation();remember();};
      form.onchange=e=>{e.stopPropagation();remember();};
      try{
        const draft=JSON.parse(localStorage.getItem(draftKey)||'null');
        if(draft&&draft.basis===state.basis.digest){
          output.value=draft.output;evaluation.value=draft.evaluation;method.value=draft.method;mode.value=draft.mode;
          packageId=draft.packageId||'';
          for(const key of Object.keys(limits) as (keyof typeof limits)[])limits[key].value=draft.limits[key];
          box.dataset.draft='true';status.textContent='Your unfinished draft is restored. Save it before reviewing.';
        }else if(draft){
          status.textContent='The Study changed while you were away. Compare your draft with the saved plan.';
          content.append(button('Restore my draft',async()=>{
            output.value=draft.output||'';evaluation.value=draft.evaluation||'';method.value=draft.method||'';mode.value=draft.mode||'connected-agent';
            for(const key of Object.keys(limits) as (keyof typeof limits)[])limits[key].value=draft.limits?.[key]??limits[key].value;
            packageId=draft.packageId||'';packages.value=packageId;remember();
            status.textContent='Your draft is restored beside the current saved plan. Review your changes, then save.';
          }));
        }
      }catch{/* An unavailable browser store cannot change the retained plan. */}
      const save=button('Save research plan',async()=>{
        if(!form.checkValidity()||!output.value.trim()||!evaluation.value.trim()||!method.value.trim()){status.textContent='Describe the output, evaluation and proposed approach.';return;}
        const plan:Plan={desiredOutput:output.value.trim(),evaluation:evaluation.value.trim(),methodNotes:method.value.trim(),actorMode:mode.value as Plan['actorMode'],materials:study.inputs.map(r=>({...r,session:study.session})),limits:{experiments:Number(limits.experiments.value),modelMinutes:Number(limits.modelMinutes.value),tokens:Number(limits.tokens.value)}};
        const pack=packageRows.find(row=>row.id===packages.value);if(pack)plan.instructionPackage={id:pack.id,version:pack.version,digest:pack.digest};
        const savingVersion=editVersion;
        const response=await call('plan',{basis:state!.basis,plan});
        if(current()){
          state=response.coordination;
          if(editVersion===savingVersion){amending=false;clearDraft();draw();}
          else{remember();status.textContent='Earlier edits saved. Your newer draft is kept here; save again when ready.';}
        }
      },true);form.onsubmit=e=>{e.preventDefault();save.click();};form.append(save);content.append(form);
      if(state.plan?.status==='draft'&&!amending){review=button('Review this saved plan',async()=>{if(box.dataset.draft){status.textContent='Save your edited plan before reviewing it.';return;}const response=await call('review',{basis:state!.basis});if(current()){clearDraft();state=response.coordination;draw();}},true);review.disabled=!!box.dataset.draft;content.append(review);} 
    }else{
      if(state.plan.actorMode==='manual')content.append(n('p','Open Tools or Workflows, choose the exact Study inputs, and run your first experiment. Keep its outputs with this Study.','study-note'));
      if(state.plan.actorMode==='local-researcher')content.append(n('p','Use the Hooke researcher controls in this Study. Its model must be configured before Start research can run it; saving this plan has not started it.','study-note'));
      const summary=n('div','','study-initiation-summary');summary.append(n('h4',state.plan.desiredOutput),n('p',state.plan.methodNotes));
      const evaluation=n('details');evaluation.append(n('summary','Evaluation and limits'),n('p',state.plan.evaluation));
      const bounds=n('div','','study-plan-limits');
      for(const [value,label]of [[state.plan.limits.experiments,'Experiments'],[state.plan.limits.modelMinutes,'Model minutes'],[state.plan.limits.tokens.toLocaleString(),'Model tokens']]){const item=n('div');item.append(n('strong',String(value)),n('span',String(label)));bounds.append(item);}
      evaluation.append(bounds,n('p','Model limits must be enforced by the connected harness. Hooke checks the scientific experiment allowance.','study-note'));summary.append(evaluation);content.append(summary);
      content.append(button('Revise plan',async()=>{amending=true;draw();status.textContent='Propose a revised plan. Existing experiments keep their original methods and inputs.';}));
      const actors=n('p','Checking Project contributors…','study-note');content.append(actors);
      void fetcher(`/api/research-efforts/${study.id}/questions?session=${encodeURIComponent(study.session)}`,{headers:{'X-MicroscopeMind':'research-effort'},cache:'no-store'}).then(result).then(reply=>{
        if(!current()||!content.contains(actors))return;
        const recipients=reply.recipients as {actor:string;label:string}[];
        actors.textContent=recipients.length?`Authorized contributors: ${recipients.map(r=>r.label).join(', ')}. Connection does not mean an agent is running.`:'No connected agent has contribute access. Open Settings → Connected agents → Project access to authorize one.';
        if(state?.plan?.actorMode==='connected-agent'&&recipients.length&&!state.assignments.length){
          content.append(button('Prepare handoff for my agent',async()=>{
            const plan=state!.plan!;
            await call('propose',{request:`initial:${study.id}`,assignment:{objective:study.objective,deliverable:plan.desiredOutput,uncertainty:plan.evaluation,decisionImpact:'Choose the next experiment from measured results',whyNewWork:'Begin the reviewed research plan; inspect existing evidence before admitting work',evidence:plan.materials,dependsOn:[]}});
            await load();
          },true));
        }
      }).catch(e=>{if(current()&&content.contains(actors))actors.textContent=`Could not read contributors: ${String(e.message||e)}`;});
    }
    const assignmentGroups=new Map<string,HTMLElement>();
    const groupFor=(assignment:Assignment)=>{const key=assignment.parent?`Parent: ${state!.assignments.find(a=>a.id===assignment.parent)?.objective||assignment.parent}`:assignment.approach||'Assignments';let group=assignmentGroups.get(key);if(!group){const details=n('details','','study-assignment-group');details.append(n('summary',key));details.open=!assignment.parent;content.append(details);assignmentGroups.set(key,details);group=details;}return group;};
    for(const assignment of state.assignments.slice(0,assignmentLimit)){
      const card=n('article','','study-assignment');card.append(n('h4',assignment.objective),n('p',`${assignment.observedState==='planned'?'Waiting for an agent':assignment.report?'Report awaiting review':assignment.observedState}${assignment.claimedBy?` · ${assignment.claimedBy.byLabel}`:''}`));
      if(assignment.deliverable)card.append(n('p',`Expected output: ${assignment.deliverable}`));
      if(assignment.claimedAt)card.append(n('small',`Claim recorded ${new Date(assignment.claimedAt*1000).toLocaleString()}. This is ownership, not live presence.`));
      if(assignment.dependsOn?.length)card.append(n('p',`Depends on: ${assignment.dependsOn.map(id=>state!.assignments.find(a=>a.id===id)?.objective||id).join('; ')}`));
      if(assignment.operations?.length)card.append(n('p',`${assignment.operations.length} linked scientific operations · see Experiments below`));
      if(['running','needs-reconciliation','cancelled'].includes(assignment.observedState))card.append(button(assignment.observedState==='cancelled'?'Reconcile original operations':'Release assignment for reassignment',async()=>{
        await call('recover',{assignment:assignment.id});await load();
      },false,'Checking original operations…'));
      if(!assignment.report&&assignment.observedState==='planned')card.append(n('p','Copy the handoff below and paste it into your connected agent. The agent reads this saved plan and claims the assignment; replies and results return here.','study-note'));
      if(assignment.report){
        card.append(n('p',assignment.report.statement),n('small',assignment.report.limitations));
        for(const [index,ref]of (assignment.report.evidence||[]).entries())if(openFile)card.append(button(`Open evidence ${index+1} · version ${ref.revision}`,async()=>{await openFile(ref,`Report evidence ${index+1}`);},false,'Opening evidence…'));
      }
      const next=assignment.report?'This assignment has returned its report. Read its evidence and limitations before proposing any new assignment; do not claim or rerun the completed assignment.':'Claim this assignment through study.coordinate (action claim) using your actual harness and model; its schema is in hooke tools.';
      const prompt=`Continue this research in Hooke using your already-authorized named connection and the installed Hooke skill. Do not create another Project or request fresh credentials when your connection works.
Project: ${study.session}
Study: ${study.id}
Assignment: ${assignment.id}
Hooke origin: ${typeof location!=='undefined'?location.origin:'the existing configured Hooke host'} (use your configured connector door, not the browser port).
Use the skill's hooke command and your existing --agent/--connection selector. Discover tools, then run:
hooke api GET 'studies/${study.id}/coordination?session=${study.session}' --json
Read the retained plan, exact material, prior failures and decisions through the connector. ${next} Follow the reviewed allowance. If your harness cannot enforce and report its model limits, report that before running a model experiment. Use only admitted capabilities and frozen workflows; return exact output references through action complete. Do not rewrite Project storage, change grants, install or activate extensions, or use unrelated Projects. Reported results await review; they are not automatically accepted findings.`;
      card.append(button(assignment.report?'Copy report context':'Copy agent handoff',async()=>{try{await navigator.clipboard.writeText(prompt);status.textContent=assignment.report?'Copied report context. This does not restart the assignment.':'Copied. Paste into your connected agent to start. Waiting here does not launch it.';}catch{const text=n('textarea');text.value=prompt;text.readOnly=true;text.setAttribute('aria-label','Agent handoff');card.append(text);text.select();status.textContent='Copy this handoff into your connected agent.';}}));groupFor(assignment).append(card);
    }
    if(state.assignments.length>assignmentLimit)content.append(button('Show more assignments',async()=>{assignmentLimit+=12;draw();}));
    if(state.plan?.status==='reviewed'&&!amending)proposalForm();
    for(const item of state.capabilityProposals||[]){
      const card=n('details','','study-assignment'),p=item.proposal;
      card.append(n('summary',`${p.title} · awaiting your review`),n('p',`Proposed by ${item.byLabel}`),n('p',p.gap));
      for(const [label,value]of [['Licence',p.licence],['Dependencies',p.dependencies],['Permissions',p.permissions],['Resources',p.resources],['Checks',p.proofPlan]])card.append(n('h4',label),n('p',value));
      card.append(n('p','A proposal does not install or activate its package. Models shows its current setup, checks and activation state.','study-note'));
      if(openFile)card.append(button('Open proposed package',async()=>{await openFile(p.package,p.title);},false,'Opening package…'));
      const setup=n('a','Review in Models');setup.href='#models';card.append(setup);content.append(card);
    }
    content.append(button('Refresh activity',async()=>{if(box.dataset.draft){status.textContent='Save your draft before refreshing activity.';return;}await load();}));
  }
  function proposalForm(){
    const draftId=`hooke:proposal:${study.session}:${study.id}`,form=n('form','','study-followup');
    const disclosure=n('details');disclosure.append(n('summary','Propose next work'));disclosure.append(form);content.append(disclosure);
    form.append(n('p',`Proposal for ${study.title||study.objective}. Saved evidence stays attached; nothing runs until an agent claims this work.`,'study-note'));
    const fields={objective:n('textarea'),deliverable:n('textarea'),uncertainty:n('textarea'),decisionImpact:n('textarea'),whyNewWork:n('textarea')};
    const labels={objective:'What should we investigate?',deliverable:'Expected output',uncertainty:'Which uncertainty will this address?',decisionImpact:'What would change the next decision?',whyNewWork:'Why do we need new work?'};
    let request=crypto.randomUUID();
    for(const key of Object.keys(fields) as (keyof typeof fields)[]){const label=n('label');label.append(n('span',labels[key]),fields[key]);fields[key].required=true;form.append(label);}
    const dependencies=n('select');dependencies.multiple=true;dependencies.setAttribute('aria-label','Depends on earlier assignments');
    for(const a of state!.assignments){const option=n('option',a.objective);option.value=a.id;dependencies.append(option);}if(state!.assignments.length)form.append(n('p','Depends on earlier assignments (optional)'),dependencies);
    const saveDraft=()=>{box.dataset.draft='true';try{localStorage.setItem(draftId,JSON.stringify({request,fields:Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value])),dependsOn:Array.from(dependencies.selectedOptions).map(o=>o.value)}));}catch{/* Form remains available. */}};
    try{const draft=JSON.parse(localStorage.getItem(draftId)||'null');if(draft){request=draft.request;for(const key of Object.keys(fields)as(keyof typeof fields)[])fields[key].value=draft.fields?.[key]||'';for(const o of dependencies.options)o.selected=draft.dependsOn?.includes(o.value);box.dataset.draft='true';disclosure.open=true;}}catch{/* No usable draft. */}
    form.oninput=e=>{e.stopPropagation();saveDraft();};form.onchange=e=>{e.stopPropagation();saveDraft();};
    const submit=button('Save proposal',async()=>{
      if(!form.checkValidity()){status.textContent='Describe the proposed work and why it matters.';return;}
      saveDraft();
      await call('propose',{request,assignment:{...Object.fromEntries(Object.entries(fields).map(([k,v])=>[k,v.value.trim()])),evidence:state!.plan!.materials,dependsOn:Array.from(dependencies.selectedOptions).map(o=>o.value)}});
      try{localStorage.removeItem(draftId);}catch{/* Stored draft is only a convenience. */}delete box.dataset.draft;await load();status.textContent='Proposal saved. Waiting for an authorized agent to claim it.';
    },true);form.append(submit);form.onsubmit=e=>{e.preventDefault();submit.click();};
  }
  async function load(){const response=await result(await fetcher(`${base}?session=${encodeURIComponent(study.session)}`,{cache:'no-store'}));if(current()){state=response;draw();}}
  void load().catch(e=>{if(current()){status.textContent=String(e.message||e);content.append(button('Retry',load));}});
}
