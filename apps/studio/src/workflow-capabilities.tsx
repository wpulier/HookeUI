import {useEffect,useRef,useState} from 'react';
import {createRoot} from 'react-dom/client';
import {command,confirmRequest,read,requestFor,type Exact,type WorkflowRun} from './workflow-model';

export type Parameter={id:string;type:string;label?:string;default?:unknown;min?:number;max?:number;options?:string[];optional?:boolean};
export type ScientificCapability={id:string;capability:string;name?:string;generation:string;inputs:{id:string;type:string}[];parameters:Parameter[];ready:boolean;reason:string|null};
type Input={input:Exact;title:string};
export type MaterialSetup={material:Input;study?:{id:string;title:string};capability?:string};
export function pinnedInputs(inputs:Input[],setup?:MaterialSetup):Input[]{return setup?[setup.material,...inputs.filter(i=>i.input.id!==setup.material.input.id)]:inputs;}
type FormProps={capability:ScientificCapability;inputs:Input[];selected:Record<string,string>;parameters:Record<string,unknown>;busy:boolean;select:(port:string,id:string)=>void;parameter:(key:string,value:unknown)=>void;run:()=>void};

/** These controls use catalog declarations; they do not grant execution readiness. */
export function CapabilityForm({capability,inputs,selected,parameters,busy,select,parameter,run}:FormProps){
 return <section aria-label="Run scientific capability">
  <h3>{capability.name||capability.id}</h3>
  {!capability.ready&&<p role="status">{capability.reason||'This capability is unavailable.'}</p>}
  {capability.inputs.map(port=><label key={port.id}>{port.id}<select aria-label={port.id} value={selected[port.id]||''} onChange={e=>select(port.id,e.target.value)}><option value="">Choose a retained input</option>{inputs.map(row=><option key={row.input.id} value={row.input.id}>{row.title} · revision {row.input.revision}</option>)}</select></label>)}
  {capability.parameters.map(p=><label key={p.id}>{p.label||p.id}{p.type==='bool'?<input type="checkbox" checked={parameters[p.id]===true} onChange={e=>parameter(p.id,e.target.checked)}/>:p.type==='choice'?<select value={String(parameters[p.id]??'')} onChange={e=>parameter(p.id,e.target.value)}>{p.options?.map(v=><option key={v} value={v}>{v}</option>)}</select>:<input type={p.type==='int'||p.type==='number'?'number':'text'} min={p.min} max={p.max} step={p.type==='int'?1:'any'} value={String(parameters[p.id]??'')} onChange={e=>parameter(p.id,p.type==='int'||p.type==='number'?e.target.valueAsNumber:e.target.value)}/>}</label>)}
  <p>Runs on the displayed saved revisions. Results and failures remain in this Project.</p>
  <button disabled={busy||!capability.ready||capability.inputs.some(p=>!selected[p.id])} onClick={run}>{busy?'Starting…':'Run capability'}</button>
 </section>;
}

export function ManualCapabilities({session,currentSession,started,setup}:{session:string;currentSession:()=>string;started:(run:WorkflowRun)=>void;setup?:MaterialSetup}){
 const [catalog,setCatalog]=useState<ScientificCapability[]>([]),[inputs,setInputs]=useState<Input[]>([]),[chosen,setChosen]=useState(setup?.capability||'');
 const [selected,setSelected]=useState<Record<string,string>>({}),[parameters,setParameters]=useState<Record<string,unknown>>({}),[busy,setBusy]=useState(false),[status,setStatus]=useState('');
 const retry=useRef({body:'',request:''}),initialChoice=useRef('');
 const load=async()=>{const [c,i]=await Promise.all([read<{capabilities:ScientificCapability[]}>('/api/workflows/capabilities'),read<{inputs:Input[]}>(`/api/workflows/inputs?${new URLSearchParams({session})}`)]);if(currentSession()!==session)return;setCatalog(c.capabilities);setInputs(pinnedInputs(i.inputs,setup));};
 useEffect(()=>{let alive=true;void Promise.all([read<{capabilities:ScientificCapability[]}>('/api/workflows/capabilities'),read<{inputs:Input[]}>(`/api/workflows/inputs?${new URLSearchParams({session})}`)]).then(([c,i])=>{if(alive){setCatalog(c.capabilities);setInputs(pinnedInputs(i.inputs,setup));}}).catch(e=>{if(alive)setStatus(String(e));});return()=>{alive=false;};},[session]);
 const capability=catalog.find(c=>c.id===chosen);
 const choose=(id:string)=>{setChosen(id);setSelected(setup?{[(catalog.find(c=>c.id===id)?.inputs||[])[0]?.id||'source']:setup.material.input.id}:{});setParameters(Object.fromEntries((catalog.find(c=>c.id===id)?.parameters||[]).filter(p=>p.default!==undefined&&p.default!==null).map(p=>[p.id,p.default])));setStatus('');};
 useEffect(()=>{const route=()=>{const id=setup?.capability||new URLSearchParams(location.hash.split('?')[1]||'').get('capability');if(id&&catalog.some(c=>c.id===id)&&initialChoice.current!==id){initialChoice.current=id;choose(id);}};route();window.addEventListener('hashchange',route);return()=>window.removeEventListener('hashchange',route);},[catalog]);
 const run=async()=>{if(!capability)return;if(currentSession()!==session){setStatus('Return to the original Project before running this capability.');return;}setBusy(true);setStatus('');try{
  const refs=Object.fromEntries(capability.inputs.map(p=>{const row=inputs.find(i=>i.input.id===selected[p.id]);if(!row)throw new Error('Refresh and select every input.');return [p.id,row.input];}));
  const body={action:'capability',session,capability:capability.capability,generation:capability.generation,inputs:refs,parameters,...(setup?.study?{study:setup.study.id}:{})};
  const request=requestFor(retry.current,body);const result=await command<{run:WorkflowRun}>({...body,request});confirmRequest(retry.current,request);
  if(currentSession()===session)started(result.run);else setStatus('Started in the original Project. Return there to inspect the run.');
 }catch(e){setStatus(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 return <section className="wf-capabilities"><h2>{setup?.study?`Experiment in ${setup.study.title}`:'Run a scientific capability'}</h2>{setup&&<p>{setup.material.title} · revision {setup.material.input.revision}. Choose the method and review its settings. Nothing runs until you choose Run capability.</p>}<p>Use a single tool here, or connect the same tools in a saved workflow.</p><label>Capability<select value={chosen} onChange={e=>choose(e.target.value)}><option value="">Choose a capability</option>{catalog.map(c=><option key={c.id} value={c.id}>{c.name||c.id}{c.ready?'':' · setup needed'}</option>)}</select></label><button disabled={busy} onClick={()=>void load().catch(e=>setStatus(String(e)))}>Refresh inputs and readiness</button><p role="status">{status}</p>{capability&&<CapabilityForm capability={capability} inputs={inputs} selected={selected} parameters={parameters} busy={busy} select={(p,id)=>setSelected(old=>({...old,[p]:id}))} parameter={(p,v)=>setParameters(old=>({...old,[p]:v}))} run={()=>void run()}/>}</section>;
}

/** The material overview opens the same capability form and admission path as Tools. */
export function openMaterialCapability(setup:MaterialSetup,currentSession:()=>string,started:(run:WorkflowRun)=>void){
 const dialog=document.createElement('dialog');dialog.className='workspace-recovery-dialog material-capability-dialog';
 dialog.setAttribute('aria-label','Experiment setup');
 const close=document.createElement('button');close.textContent='Close';close.type='button';close.addEventListener('click',()=>dialog.close());
 const content=document.createElement('div');dialog.append(close,content);document.body.append(dialog);
 let alive=true;const current=()=>alive&&dialog.open?currentSession():'';
 const root=createRoot(content);root.render(<ManualCapabilities session={setup.material.input.session} currentSession={current} setup={setup} started={run=>{if(!alive||current()!==setup.material.input.session)return;dialog.close();started(run);}}/>);
 dialog.addEventListener('close',()=>{alive=false;root.unmount();dialog.remove();},{once:true});dialog.showModal();
}
