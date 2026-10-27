/** Explicit saved population → shared native inputs. A retry retains its captured request identity. */
type Selection = {id:string;session?:string;revision:number;status:string};
type Accepted = {id:string;session:string;revision:number;title:string};
const object=(value:unknown):value is Record<string,unknown>=>!!value&&typeof value==='object'&&!Array.isArray(value);
async function reply(response:Response):Promise<Record<string,unknown>>{
  const body:unknown=await response.json();
  if(!object(body))throw new Error('The retention receipt is unreadable. Check Project Data before retrying.');
  if(!response.ok)throw new Error(typeof body.error==='string'?body.error:'Labels could not be retained.');
  return body;
}
export function bindPopulationNative(host:HTMLElement,selected:()=>Selection|null,open:(target:Accepted)=>Promise<void>):{sync():void}{
  const button=document.createElement('button');button.type='button';button.className='population-native';button.textContent='Retain labels for tools';
  const stop=document.createElement('button');stop.type='button';stop.textContent='Stop retaining labels';stop.hidden=true;
  const show=document.createElement('button');show.type='button';show.className='population-native-open';show.textContent='Open retained labels';show.hidden=true;
  const status=document.createElement('span');status.setAttribute('role','status');host.append(button,stop,show,status);
  let action:AbortController|null=null,stamp='',request='',accepted:Accepted|null=null;
  const identity=()=>{const value=selected();return value?JSON.stringify([value.session,value.id,value.revision]):'';};
  function sync():void{
    const next=identity();
    if(next!==stamp){action?.abort();action=null;request='';accepted=null;show.hidden=true;status.textContent='';stamp=next;}
    if(selected()?.status!=='saved')action?.abort();
    button.disabled=!!action||selected()?.status!=='saved'||!selected()?.session;stop.hidden=!action;
  }
  stop.addEventListener('click',()=>{action?.abort();status.textContent='Request stopped. Check Project Data before retrying; accepted work is preserved.';});
  show.addEventListener('click',()=>{if(accepted&&stamp===identity())void open(accepted).catch(error=>{status.textContent=String(error);});});
  button.addEventListener('click',()=>{void(async()=>{
    const source=selected();if(!source?.session||source.status!=='saved'||action)return;
    const selectedSource={id:source.id,session:source.session,revision:source.revision};
    const key=identity(),active=new AbortController();action=active;request ||= crypto.randomUUID();sync();
    status.textContent='Checking saved population and native input support…';
    try{
      const ready=await reply(await fetch(`/api/populations/${encodeURIComponent(source.id)}/native-labels?${new URLSearchParams({session:source.session,revision:String(source.revision)})}`,{signal:active.signal,cache:'no-store'}));
      if(active.signal.aborted||key!==identity())return;
      const operation=ready.operation,chosen=ready.source;
      if(!object(operation)||operation.id!=='retain-population-labels-v1'||operation.implementation!=='builtin'||operation.preparation!=='none'
        ||operation.input!=='saved-population-revision'||operation.output!=='native-project-arrays'
        ||!object(chosen)||chosen.id!==source.id||chosen.session!==source.session||chosen.revision!==source.revision
        ||!object(ready.readiness)||ready.readiness.ready!==true)throw new Error('This revision is not ready for native retention. Inspect the saved population again.');
      status.textContent='Retaining original pixels and corrected labels…';
      const result=await reply(await fetch('/api/populations/retain-labels',{method:'POST',signal:active.signal,
        headers:{'Content-Type':'application/json','X-MicroscopeMind':'retain-population-labels'},
        body:JSON.stringify({operation:operation.id,...selectedSource,request})}));
      if(active.signal.aborted||key!==identity())return;
      const project=result.project,doc=object(project)?project.document:null,state=object(doc)?doc.state:null;
      const provenance=object(state)?state.populationSource:null,captured=object(provenance)?provenance.source:null;
      if(!object(project)||typeof project.id!=='string'||project.session!==source.session||project.revision!==1||!object(doc)||typeof doc.title!=='string'
        ||!object(captured)||captured.id!==source.id||captured.session!==source.session||captured.revision!==source.revision)throw new Error('The accepted labels do not match this population revision. Check Project Data before retrying.');
      accepted={id:project.id,session:source.session,revision:1,title:doc.title};show.hidden=false;
      status.textContent=`Revision ${source.revision} retained as native image and labels. Open saved data to measure or export; the producer was not rerun.`;
    }catch(error){if(key===identity())status.textContent=active.signal.aborted?'Request stopped. Retry this same selection to recover any accepted result.':error instanceof Error?error.message:String(error);}
    finally{if(action===active)action=null;sync();}
  })();});
  return {sync};
}

/** Display only the recorded component binding; full provenance admission belongs to method review. */
function populationAssociation(value:Record<string,unknown>):{input:string;output:string}|null{
  const association=value.association,inputs=value.nativeInputs,source=value.source,producer=value.producer;
  if(!object(association)||!object(inputs)||!object(source)||!object(producer)
    ||!object(association.source)||!object(association.producer))return null;
  const origin=association.source;
  if(['id','session','revision','execution'].some(key=>origin[key]!==source[key])
    ||association.producer.implementation!==producer.implementation)return null;
  const input=association.input,output=association.output;
  const matches=(ref:unknown,held:unknown)=>object(ref)&&object(held)
    &&['id','session','component','revision'].every(key=>typeof ref[key]==='string'&&ref[key]===held[key])
    &&typeof ref.revision==='string'&&/^[a-f0-9]{64}$/.test(ref.revision);
  if(!object(input)||!object(output)||!matches(input,inputs.image)||!matches(output,inputs.labels)
    ||input.session!==source.session||output.session!==source.session||input.id!==output.id
    ||input.component!=='array.image'||output.component!=='array.labels')return null;
  return {input:input.component,output:output.component};
}

/** Bounded inert account of this acceptance, distinct from native Python/callback history. */
export function showPopulationProvenance(root:HTMLElement,state:unknown):void{
  const value=object(state)?state.populationSource:null;if(!object(value))return;
  const source=value.source,producer=value.producer;
  const section=document.createElement('section'),details=document.createElement('details'),heading=document.createElement('summary');
  heading.textContent='Population history and provenance';details.append(heading);
  const line=(text:string,parent:HTMLElement=details)=>{const p=document.createElement('p');p.textContent=text;parent.append(p);};
  if((value.format!==1&&value.format!==2)||!object(source)||typeof source.id!=='string'||!Number.isSafeInteger(source.revision)||Number(source.revision)<0
    ||!object(producer)||typeof producer.implementation!=='string'||!Array.isArray(value.members)||!Array.isArray(value.corrections)){
    line('The population provenance is unsupported. Keep the original population and accepted native inputs.',section);root.append(section);return;
  }
  line(`${source.id.slice(0,128)} · revision ${source.revision} · producer ${producer.implementation.slice(0,160)} · ${value.members.length} retained instances`);
  const view=object(state)?state.scientificView:null;
  if(!object(value.nativeInputs)||!object(view)||!object(view.inputs)
    ||Object.entries(value.nativeInputs).some(([name,ref])=>JSON.stringify(ref)!==JSON.stringify((view.inputs as Record<string,unknown>)[name])))
    line('The current inputs differ from this original acceptance or lack its binding. This provenance describes the retained population; inspect the accepted input revision before interpreting later values.',section);
  if(value.format===2){
    const association=populationAssociation(value);
    if(association){
      line(`Explicit recorded association: ${association.input} → ${association.output}. Method review checks its exact accepted source.`);
      line('Producing actions and inference timing are not recorded by this association. The producer remains captured attribution.');
    }else line('The explicit image-to-labels association is incomplete or inconsistent. Keep the original acceptance and review its saved input before comparing methods.',section);
  }
  line('Original pixels and instance IDs were retained without rerunning the producer. Native measurement centroids are mean array indices; particle exchange centroids add 0.5 pixel.');
  line('Original population history remains in its evidence store. This is a frozen acceptance record, not a promise to replay the original analysis.');
  if(value.runtimeKind==='fiji')line('The producing installed Fiji generation is retained. Space removal currently groups Fiji generations, so this hold protects that whole group.');
  else line('The producer identity is recorded; this acceptance does not retain a non-Fiji model environment.');
  for(const correction of value.corrections.slice(-50)){
    if(object(correction)&&Number.isSafeInteger(correction.r)&&typeof correction.change==='string')
      line(`Correction ${correction.r}: ${correction.change.slice(0,1000)}`);
  }
  if(value.corrections.length>50)line('Showing the last 50 corrections; the accepted document retains the complete recorded history.');
  if(object(source.files))for(const [name,digest] of Object.entries(source.files).slice(0,4)){
    if(typeof digest==='string'&&/^[a-f0-9]{64}$/.test(digest))line(`${name.slice(0,80)} SHA-256 ${digest}`);
  }
  section.append(details);root.append(section);
}
