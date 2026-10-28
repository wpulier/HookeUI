import {useEffect,useRef,useState} from 'react';
import {command,confirmRequest,read,requestFor,type Exact,type WorkflowRun} from './workflow-model';

export type Parameter={id:string;type:string;label?:string;default?:unknown;min?:number;max?:number;options?:string[];optional?:boolean};
export type ScientificCapability={id:string;capability:string;name?:string;generation:string;inputs:{id:string;type:string}[];parameters:Parameter[];ready:boolean;reason:string|null};
type Input={input:Exact;title:string};
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

export function ManualCapabilities({session,currentSession,started}:{session:string;currentSession:()=>string;started:(run:WorkflowRun)=>void}){
 const [catalog,setCatalog]=useState<ScientificCapability[]>([]),[inputs,setInputs]=useState<Input[]>([]),[chosen,setChosen]=useState('');
 const [selected,setSelected]=useState<Record<string,string>>({}),[parameters,setParameters]=useState<Record<string,unknown>>({}),[busy,setBusy]=useState(false),[status,setStatus]=useState('');
 const retry=useRef({body:'',request:''});
 const load=async()=>{const [c,i]=await Promise.all([read<{capabilities:ScientificCapability[]}>('/api/workflows/capabilities'),read<{inputs:Input[]}>(`/api/workflows/inputs?${new URLSearchParams({session})}`)]);if(currentSession()!==session)return;setCatalog(c.capabilities);setInputs(i.inputs);setSelected({});};
 useEffect(()=>{let alive=true;void Promise.all([read<{capabilities:ScientificCapability[]}>('/api/workflows/capabilities'),read<{inputs:Input[]}>(`/api/workflows/inputs?${new URLSearchParams({session})}`)]).then(([c,i])=>{if(alive){setCatalog(c.capabilities);setInputs(i.inputs);}}).catch(e=>{if(alive)setStatus(String(e));});return()=>{alive=false;};},[session]);
 const capability=catalog.find(c=>c.id===chosen);
 const choose=(id:string)=>{setChosen(id);setSelected({});setParameters(Object.fromEntries((catalog.find(c=>c.id===id)?.parameters||[]).filter(p=>p.default!==undefined&&p.default!==null).map(p=>[p.id,p.default])));setStatus('');};
 useEffect(()=>{const route=()=>{const id=new URLSearchParams(location.hash.split('?')[1]||'').get('capability');if(id&&catalog.some(c=>c.id===id))choose(id);};route();window.addEventListener('hashchange',route);return()=>window.removeEventListener('hashchange',route);},[catalog]);
 const run=async()=>{if(!capability)return;if(currentSession()!==session){setStatus('Return to the original Project before running this capability.');return;}setBusy(true);setStatus('');try{
  const refs=Object.fromEntries(capability.inputs.map(p=>{const row=inputs.find(i=>i.input.id===selected[p.id]);if(!row)throw new Error('Refresh and select every input.');return [p.id,row.input];}));
  const body={action:'capability',session,capability:capability.capability,generation:capability.generation,inputs:refs,parameters};
  const request=requestFor(retry.current,body);const result=await command<{run:WorkflowRun}>({...body,request});confirmRequest(retry.current,request);
  if(currentSession()===session)started(result.run);else setStatus('Started in the original Project. Return there to inspect the run.');
 }catch(e){setStatus(e instanceof Error?e.message:String(e));}finally{setBusy(false);}};
 return <section className="wf-capabilities"><h2>Run a scientific capability</h2><p>Use a single tool here, or connect the same tools in a saved workflow.</p><label>Capability<select value={chosen} onChange={e=>choose(e.target.value)}><option value="">Choose a capability</option>{catalog.map(c=><option key={c.id} value={c.id}>{c.name||c.id}{c.ready?'':' · setup needed'}</option>)}</select></label><button disabled={busy} onClick={()=>void load().catch(e=>setStatus(String(e)))}>Refresh inputs and readiness</button><p role="status">{status}</p>{capability&&<CapabilityForm capability={capability} inputs={inputs} selected={selected} parameters={parameters} busy={busy} select={(p,id)=>setSelected(old=>({...old,[p]:id}))} parameter={(p,v)=>setParameters(old=>({...old,[p]:v}))} run={()=>void run()}/>}</section>;
}
