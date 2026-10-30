/** Retained native methods: explicit accepted selections, no producer execution. */
type Source={id:string;day:string;version:number};
type Target={id:string;version:number;title:string;selection?:{component:string;digest:string;name:string}};
type Open=(target:Target)=>Promise<void>;
type Owner={signal:AbortSignal;current:()=>boolean};
type Native={kind:'array';session:string;id:string;revision:number;component:string;digest:string};
const OPERATION='compare-retained-methods-v1';
/** A retained comparison's arms, A to F: a workflow's comparison step compares up to six (method_comparison.py ARM_IDS). */
const ARM_IDS=['a','b','c','d','e','f'];
type Selection={session:string;source:Native;arms:Record<string,unknown>[];request:string};
const object=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const el=document.createElement(tag);el.textContent=text;return el;};
const words=(e:unknown)=>e instanceof Error?e.message:String(e);
// Preserve exact retained evidence; clarify the scope of its historical baseline warning only.
const limitation=(value:unknown)=>value==='No producing actions were recorded for this arm'
  ? 'No additional producing actions were recorded between the frozen input and this arm. Earlier source provenance is outside this comparison interval.' : String(value);
const query=(s:Source)=>new URLSearchParams({session:s.day,revision:String(s.version)});
const unique=()=>crypto.randomUUID().replaceAll('-','');
const button=(text:string,action:()=>void)=>{const b=node('button',text);b.type='button';b.addEventListener('click',action);return b;};
const label=(text:string,control:HTMLElement)=>{const row=node('label',text+' ');row.style.display='block';control.setAttribute('aria-label',text);row.append(control);return row;};
const option=(value:string,text:string)=>{const el=node('option',text);el.value=value;return el;};
async function reply(response:Response){const v:unknown=await response.json();if(!response.ok||!object(v)||v.ok!==true)throw new Error(object(v)?`${String(v.says??'Comparison did not finish.')} ${String(v.next??'')}`:'Comparison response was incomplete.');return v;}
/** The session index has its own wire contract, separate from scientific result replies. */
async function resources(source:Source,signal:AbortSignal){
  const response=await fetch(`/api/resources?${new URLSearchParams({kind:'project',day:source.day})}`,{signal});
  const value:unknown=await response.json();
  if(!response.ok)throw new Error(object(value)&&typeof value.error==='string'?value.error:'Saved Project data is unavailable. Reopen Data and retry.');
  if(!object(value)||value.day!==source.day||!Array.isArray(value.entries)||!Array.isArray(value.unreadable)||!value.unreadable.every(v=>typeof v==='string'))throw new Error('Saved Project data does not match this Project or is incomplete. Reopen Data and retry.');
  const entries=value.entries.map(entry=>{
    if(!object(entry)||entry.day!==source.day||entry.kind!=='project'||typeof entry.id!=='string'||!/^p[0-9a-f]{32}$/.test(entry.id)||!Number.isSafeInteger(entry.version)||Number(entry.version)<1||typeof entry.title!=='string')throw new Error('A saved document has an invalid Project or revision. Reopen Data and retry.');
    return {id:entry.id,title:entry.title,version:Number(entry.version)};
  });
  return {entries,unreadable:value.unreadable};
}
async function change(action:string,selection:object,signal:AbortSignal){return reply(await fetch('/api/scientific-methods',{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'compare-methods'},body:JSON.stringify({operation:OPERATION,action,selection}),signal}));}
function target(value:unknown,session:string):Target{
  if(!object(value)||typeof value.id!=='string'||!/^p[0-9a-f]{32}$/.test(value.id)||value.session!==session||!Number.isSafeInteger(value.revision)||Number(value.revision)<1||!object(value.document)||value.document.kind!=='scientific-method-comparison'||typeof value.document.title!=='string')throw new Error('The comparison receipt belongs to another Project or is incomplete. Inspect Project Data before retrying.');
  return {id:value.id,version:Number(value.revision),title:value.document.title};
}
function sameNative(value:unknown,expected:Native):boolean{
  return object(value)&&value.kind===expected.kind&&value.session===expected.session&&value.id===expected.id
    &&value.revision===expected.revision&&value.component===expected.component&&value.revisionDigest===expected.digest;
}
function sameEvidence(value:unknown,chosen:Selection):boolean{
  if(!object(value)||!sameNative(value.source,chosen.source)||!Array.isArray(value.arms)||value.arms.length!==chosen.arms.length)return false;
  return value.arms.every((held,i)=>{
    const arm=chosen.arms[i];
    if(!object(held)||['id','factor','says','status'].some(k=>held[k]!==arm[k]))return false;
    if(arm.status==='completed')return object(arm.mask)&&sameNative(held.mask,arm.mask as Native);
    return object(held.outcome)&&object(arm.outcome)&&held.outcome.says===arm.outcome.says;
  });
}

async function arrays(source:Source,signal:AbortSignal):Promise<{name:string;reference:Native;semantic:string}[]>{
  const value=await reply(await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/arrays?${query(source)}`,{signal}));
  const s=value.source;
  if(!object(s)||s.id!==source.id||s.session!==source.day||s.revision!==source.version||typeof s.digest!=='string'||!/^[a-f0-9]{64}$/.test(s.digest)||!Array.isArray(value.arrays))throw new Error('The array list does not match the chosen saved revision. Select it again.');
  const digest=s.digest;
  return value.arrays.map(row=>{
    if(!object(row)||typeof row.name!=='string'||typeof row.semantic!=='string'||!object(row.reference)||typeof row.reference.component!=='string')throw new Error('A saved array has no exact native identity. Keep the original and select another accepted array.');
    return {name:row.name,semantic:row.semantic,reference:{kind:'array',session:source.day,id:source.id,revision:source.version,component:row.reference.component,digest}};
  });
}

/** Starts from this saved document; every arm can choose an older, exact accepted revision. */
export function methodChooser(root:HTMLElement,source:Source,open:Open,owner:Owner):void{
  const section=node('section'),status=node('p'),body=node('div');status.setAttribute('role','status');
  const start=button('Compare saved methods',()=>{void load();});section.append(start,status,body);root.append(section);
  let loading=false,request=unique(),reviewed='',pending=false,action:AbortController|null=null;
  owner.signal.addEventListener('abort',()=>action?.abort(),{once:true});
  async function load(){
    if(loading||pending||owner.signal.aborted)return;
    if(!owner.current()){status.textContent='Return to this Project and work Tab before comparing.';return;}
    loading=true;start.disabled=true;status.textContent='Loading exact saved input and method choices…';
    try{
      const listing=await resources(source,owner.signal);
      const entries=listing.entries;
      const candidates=await arrays(source,owner.signal);
      if(owner.signal.aborted)return;
      if(!candidates.length)throw new Error('Choose an accepted native image or labels document in Data first. No producer was run.');
      body.replaceChildren();
      if(listing.unreadable.length)body.append(node('p',`Some saved data could not be read: ${listing.unreadable.join('; ')}. Reopen Data to inspect it; these choices include only readable documents.`));
      body.append(node('h3','Compare retained methods'),node('p',`Input revision ${source.version}. Save corrections before selecting arms. Foreground is every nonzero label; counts and calibrated sizes are compared against A. Equal shape alone never establishes alignment. Retain calculates counts and differences from saved masks; no producing method is rerun.`));
      const form=node('fieldset'),input=node('select');
      candidates.forEach((c,i)=>input.append(option(String(i),`${c.name} · ${c.semantic}`)));input.value='0';form.append(label('Frozen input',input));
      const count=node('select');count.append(option('2','Two arms'),option('3','Three arms'));count.value='2';form.append(label('Method arms',count));
      const preview=button('Review comparison',()=>{void perform('describe');}),retain=button('Retain comparison',()=>{void perform('retain');});retain.disabled=true;
      const details=node('div');
      function changed(){reviewed='';request=unique();retain.disabled=true;details.replaceChildren();}
      const rows=['a','b','c'].map((id,index)=>{
        const box=node('fieldset');box.append(node('legend',`Arm ${id.toUpperCase()}`));box.hidden=index===2;
        const factor=node('select');for(const f of ['boundary-definition','algorithm','settings','version'])factor.append(option(f,f.replaceAll('-',' ')));factor.value='boundary-definition';
        const says=node('input');says.maxLength=4096;
        const state=node('select');for(const s of ['completed','failed','cancelled'])state.append(option(s,s));state.value='completed';
        const project=node('select');entries.forEach(e=>project.append(option(String(e.id),`${String(e.title??e.id)} · revision ${String(e.version)}`)));project.value=source.id;
        if(!entries.some(e=>e.id===source.id)){project.append(option(source.id,`Current document · revision ${source.version}`));project.value=source.id;}
        const revision=node('input');revision.type='number';revision.min='1';revision.step='1';revision.value=String(source.version);
        const mask=node('select'),message=node('p'),outcome=node('textarea');outcome.maxLength=4096;
        let selected:{name:string;reference:Native;semantic:string}[]=[];
        function clear(){selected=[];mask.replaceChildren();changed();}
        project.addEventListener('change',()=>{revision.value=String(entries.find(e=>e.id===project.value)?.version??source.version);clear();});revision.addEventListener('input',clear);
        const loadMask=button(`Load arm ${id.toUpperCase()} arrays`,()=>{void(async()=>{
          if(pending||owner.signal.aborted||!owner.current())return;
          const chosen={id:project.value,day:source.day,version:Number(revision.value)};
          if(!Number.isSafeInteger(chosen.version)||chosen.version<1){message.textContent='Choose a positive accepted revision.';return;}
          loadMask.disabled=true;message.textContent='Reading saved labels…';clear();
          try{
            const found=await arrays(chosen,owner.signal);
            if(owner.signal.aborted||project.value!==chosen.id||Number(revision.value)!==chosen.version)return;
            selected=found.filter(r=>['instance-labels','class-labels'].includes(r.semantic));
            selected.forEach((r,i)=>mask.append(option(String(i),r.name)));mask.value='0';
            message.textContent=selected.length?'Accepted arrays loaded. Review checks the shared input and recorded histories.':'No retained labels in this revision. Save labels, then choose their accepted revision.';
          }catch(e){message.textContent=words(e);}finally{loadMask.disabled=false;}
        })();});
        const completed=node('div');completed.append(label(`Arm ${id.toUpperCase()} document`,project),label(`Arm ${id.toUpperCase()} revision`,revision),loadMask,label(`Arm ${id.toUpperCase()} labels`,mask),message);
        const failed=node('div');failed.hidden=true;failed.append(label(`Arm ${id.toUpperCase()} outcome`,outcome),node('p','This outcome is declared by you; no terminal history is selected. It remains unverified and has no accepted mask or zero area.'));
        state.addEventListener('change',()=>{completed.hidden=state.value!=='completed';failed.hidden=state.value==='completed';changed();});
        box.append(label(`Arm ${id.toUpperCase()} varies`,factor),label(`Arm ${id.toUpperCase()} method`,says),label(`Arm ${id.toUpperCase()} status`,state),completed,failed);form.append(box);
        for(const control of [factor,says,mask,outcome])control.addEventListener('input',changed);
        return {box,value:()=>{
          if(!says.value.trim())throw new Error(`Describe arm ${id.toUpperCase()} in a sentence.`);
          const base={id,factor:factor.value,says:says.value.trim(),status:state.value};
          if(state.value!=='completed'){if(!outcome.value.trim())throw new Error(`Record arm ${id.toUpperCase()}'s outcome.`);return {...base,outcome:{says:outcome.value.trim()}};}
          const choice=selected[Number(mask.value)];if(!choice)throw new Error(`Load and choose accepted labels for arm ${id.toUpperCase()}.`);
          return {...base,mask:choice.reference};
        }};
      });
      count.addEventListener('change',()=>{rows[2].box.hidden=count.value!=='3';changed();});input.addEventListener('change',changed);
      const stop=button('Stop comparison',()=>action?.abort());stop.hidden=true;
      body.append(form,preview,retain,stop,details);status.textContent='Choose methods, then review readiness and missing evidence.';
      function selection(){const chosen=candidates[Number(input.value)];if(!chosen)throw new Error('Choose the exact saved input.');return {session:source.day,source:chosen.reference,arms:rows.slice(0,Number(count.value)).map(r=>r.value()),request};}
      async function perform(kind:'describe'|'retain'){
        if(pending||owner.signal.aborted)return;
        if(!owner.current()){status.textContent='Return to this Project and work Tab before comparing.';return;}
        let chosen:ReturnType<typeof selection>;
        try{chosen=selection();}catch(e){status.textContent=words(e);return;}
        const signature=JSON.stringify(chosen);
        if(kind==='retain'&&reviewed!==signature){status.textContent='Review these exact choices before retaining.';return;}
        pending=true;form.disabled=true;preview.disabled=true;retain.disabled=true;stop.hidden=false;action=new AbortController();status.textContent=kind==='describe'?'Checking recorded evidence…':'Comparing accepted masks and retaining evidence…';
        try{
          const result=await change(kind,chosen,action.signal);
          if(owner.signal.aborted)return;
          if(kind==='describe'){
            if(!object(result.operation)||result.operation.id!==OPERATION||!object(result.readiness)||result.readiness.available!==true||!object(result.evidence)||!Array.isArray(result.evidence.arms)||!sameEvidence(result.evidence,chosen))throw new Error('Comparison readiness is incomplete. Keep the current selection and retry Review.');
            details.replaceChildren(node('p',String(result.readiness.limits??'')));
            for(const arm of result.evidence.arms.filter(object))for(const limit of Array.isArray(arm.limitations)?arm.limitations:[])details.append(node('p',`Arm ${String(arm.id).toUpperCase()}: ${limitation(limit)}`));
            reviewed=signature;status.textContent='Ready to retain these exact choices. Missing evidence stays unverified.';
          }else{
            const accepted=target(result.project,source.day);
            const project=result.project;
            if(!object(project)||!object(project.document)||!object(project.document.state)||!object(project.document.state.methodEvidence))throw new Error('Retained comparison has no evidence. Inspect Data before retrying.');
            const held=project.document.state.methodEvidence;
            if(!sameEvidence(held,chosen))throw new Error('Retained comparison differs from the selected input. Inspect Data before retrying.');
            status.textContent='Comparison retained. Original masks and producing histories remain unchanged. No producer was rerun.';
            details.append(button('Open retained comparison',()=>{if(owner.current())void open(accepted).catch(e=>{status.textContent=words(e);});}));reviewed='';
          }
        }catch(e){status.textContent=`${words(e)} Accepted inputs remain unchanged. Retry the same choices to recover a completed retention; edit choices to start a new request.`;}
        finally{pending=false;form.disabled=false;preview.disabled=false;retain.disabled=reviewed!==signature;stop.hidden=true;action=null;}
      }
    }catch(e){status.textContent=words(e);}finally{loading=false;start.disabled=false;}
  }
}

export async function showMethodComparison(root:HTMLElement,source:Source,open:Open,owner:Owner):Promise<void>{
  const section=node('section'),status=node('p','Reading the exact retained comparison…');status.setAttribute('role','status');section.append(status);root.append(section);
  try{
    const value=await reply(await fetch(`/api/scientific-methods/${encodeURIComponent(source.id)}?${query(source)}`,{signal:owner.signal}));
    if(owner.signal.aborted)return;
    const s=value.source,e=value.evidence,summary=value.summary;
    if(!object(s)||s.id!==source.id||s.session!==source.day||s.revision!==source.version||!object(e)||e.format!=='hooke-method-evidence-v1'||!Array.isArray(e.arms)||e.arms.length<2||e.arms.length>3||!object(summary)||summary.ranked!==false||!Array.isArray(summary.arms))throw new Error('The comparison does not match this accepted revision. Keep it and reopen from Data.');
    status.textContent=e.evidenceComplete===true?'Recorded evidence is retained; scientific qualification is not assessed.':'Evidence is incomplete. Missing histories and declared outcomes remain unverified.';
    section.append(node('h3','Retained methods'),node('p',String(summary.says??'Agreement is not accuracy.')));
    if(summary.why)section.append(node('p',String(summary.why)));
    const exportLink=node('a','Export comparison and native evidence');exportLink.setAttribute('href',`/api/scientific-projects/${encodeURIComponent(source.id)}/bundle?${query(source)}`);exportLink.download='';section.append(exportLink);
    const summaryLink=node('a','Download summary JSON');summaryLink.setAttribute('href',`/api/scientific-methods/${encodeURIComponent(source.id)}?${query(source)}`);summaryLink.download='comparison.json';section.append(' ',summaryLink);
    const choices:HTMLButtonElement[]=[],rationale=node('textarea');rationale.maxLength=4096;
    for(const arm of e.arms){
      if(!object(arm)||!ARM_IDS.includes(String(arm.id))||!['completed','failed','cancelled'].includes(String(arm.status)))throw new Error('An arm has incomplete retained evidence.');
      const metric=summary.arms.find(r=>object(r)&&r.id===arm.id);
      if(!object(metric)||!(metric.area===null||typeof metric.area==='number'&&Number.isFinite(metric.area)))throw new Error('An arm has an unsupported summary.');
      const row=node('section');row.append(node('h4',`Arm ${String(arm.id).toUpperCase()} · ${String(arm.says)}`),node('p',`${String(arm.factor)} · ${String(arm.status)}`),node('p',metric.area===null?'Unavailable — no accepted measurement':`${metric.area} ${String(metric.unit)} · ${String(metric.pixels)} foreground elements`));
      if(object(arm.outcome))row.append(node('p',String(arm.outcome.says??'')));
      for(const limit of Array.isArray(arm.limitations)?arm.limitations:[])row.append(node('p',limitation(limit)));
      if(object(arm.timing))row.append(node('p',`${arm.timing.seconds===null?'Timing unavailable':String(arm.timing.seconds)+' s'} · ${String(arm.timing.scope)}`));
      if(object(arm.declaration)&&Object.keys(arm.declaration).length)row.append(node('p',`Attributed declaration: ${JSON.stringify(arm.declaration)}`));
      for(const history of Array.isArray(arm.histories)?arm.histories:[]){if(object(history)&&typeof history.id==='string'&&history.session===source.day&&Number.isSafeInteger(history.revision))row.append(button(`Open producing history · revision ${String(history.revision)}`,()=>{if(owner.current())void open({id:String(history.id),version:Number(history.revision),title:'Producing history'});}));}
      if(arm.status==='completed'&&e.chosen===null){
        const choose=button(`Choose ${String(arm.id).toUpperCase()}`,()=>{void(async()=>{
          if(!owner.current()||owner.signal.aborted||!rationale.value.trim())return;
          const reason=rationale.value.trim();choices.forEach(b=>b.disabled=true);rationale.disabled=true;
          try{
            const result=await change('choose',{session:source.day,project:source.id,revision:source.version,arm:arm.id,rationale:reason},owner.signal);
            const accepted=target(result.project,source.day);
            if(accepted.id!==source.id||accepted.version!==source.version+1)throw new Error('Choice receipt belongs to another revision. Inspect saved Data.');
            const saved=result.project;const doc=object(saved)?saved.document:null;const state=object(doc)?doc.state:null;const evidence=object(state)?state.methodEvidence:null;const choice=object(evidence)?evidence.chosen:null;
            if(!object(choice)||choice.arm!==arm.id||choice.rationale!==reason)throw new Error('The accepted choice differs from your arm or rationale. Reopen its saved revision.');
            status.textContent='Choice and rationale retained. Alternatives remain available.';
            section.append(button('Open accepted choice',()=>{if(owner.current())void open(accepted);}));
          }catch(error){status.textContent=`${words(error)} Your rationale and original comparison are retained here. Reopen the latest accepted comparison before changing a choice.`;choices.forEach(b=>b.disabled=!rationale.value.trim());rationale.disabled=false;}
        })();});choose.disabled=true;choices.push(choose);row.append(choose);
      }
      section.append(row);
    }
    for(const d of Array.isArray(summary.differences)?summary.differences:[])if(object(d))section.append(node('p',`Arm ${String(d.arm).toUpperCase()} minus A: ${d.absolute===null?'Unavailable':String(d.absolute)+' '+String(d.unit)}; relative difference ${d.relative===null?'unavailable':String(d.relative)}.`));
    for(const d of Array.isArray(summary.disagreement)?summary.disagreement:[])if(object(d)&&Array.isArray(d.arms))section.append(node('p',`Disagreement A / ${String(d.arms[1]).toUpperCase()}: ${d.onlyA===null?'unavailable':String(d.onlyA)} / ${d['only'+String(d.arms[1]).toUpperCase()]===null?'unavailable':String(d['only'+String(d.arms[1]).toUpperCase()])} ${String(d.unit)} only in each mask.`));
    if(object(e.chosen)){
      section.append(node('p',`Chosen arm ${String(e.chosen.arm).toUpperCase()}: ${String(e.chosen.rationale)} · ${String(e.chosen.by)}`));
      const chosenId=e.chosen.arm;
      const matches=e.arms.filter(arm=>object(arm)&&arm.id===chosenId);
      const arm=matches.length===1?matches[0]:null,mask=object(arm)?arm.mask:null;
      if(!object(arm)||arm.status!=='completed'||!object(mask)||mask.kind!=='array'||mask.session!==source.day
        ||typeof mask.id!=='string'||!/^p[0-9a-f]{32}$/.test(mask.id)||!Number.isSafeInteger(mask.revision)||Number(mask.revision)<1
        ||typeof mask.component!=='string'||!/^array\.[A-Za-z0-9][A-Za-z0-9._-]{0,95}$/.test(mask.component)
        ||typeof mask.revisionDigest!=='string'||!/^[a-f0-9]{64}$/.test(mask.revisionDigest))
        throw new Error('The chosen result has no exact accepted labels identity. Reopen the comparison from Data.');
      const held:Native={kind:'array',session:source.day,id:mask.id,revision:Number(mask.revision),component:mask.component,digest:mask.revisionDigest};
      let opening=false;
      const proceed=button('Continue with chosen result',()=>{void(async()=>{
        if(opening||owner.signal.aborted)return;
        if(!owner.current()){status.textContent='Return to this comparison’s Project and Tab before continuing.';return;}
        opening=true;proceed.disabled=true;status.textContent='Checking the chosen saved labels…';
        try{
          const rows=await arrays({id:held.id,day:held.session,version:held.revision},owner.signal);
          if(owner.signal.aborted||!owner.current())return;
          const found=rows.filter(row=>row.reference.component===held.component);
          if(found.length!==1||found[0].reference.digest!==held.digest||!['instance-labels','class-labels'].includes(found[0].semantic))
            throw new Error('The chosen labels or their saved revision changed. Keep the comparison and inspect its original data.');
          const name=found[0].name;
          await open({id:held.id,version:held.revision,title:name,selection:{component:held.component,digest:held.digest,name}});
          if(!owner.signal.aborted&&owner.current())status.textContent=`Opened ${name}, saved revision ${held.revision}. No producer was run.`;
        }catch(error){if(!owner.signal.aborted)status.textContent=words(error);}
        finally{opening=false;proceed.disabled=false;}
      })();});
      section.append(node('p',`Continue from arm ${String(arm.id).toUpperCase()} · ${String(arm.says)} · saved revision ${held.revision}. The original comparison remains available.`),proceed);
    }
    else{section.append(label('Why this protocol fits the question',rationale));rationale.addEventListener('input',()=>choices.forEach(b=>b.disabled=!rationale.value.trim()));}
  }catch(error){if(!owner.signal.aborted){status.textContent=words(error);section.append(button('Retry retained comparison',()=>{section.remove();void showMethodComparison(root,source,open,owner);}));}}
}
