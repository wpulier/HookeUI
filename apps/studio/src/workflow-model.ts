export type Exact={id:string;session:string;revision:number;digest:string};
export type StepKind='tool'|'review'|'measure'|'export'|'compare'|'researcher';
export type WorkflowStep={id:string;name:string;kind:StepKind;needs:string[];settings:Record<string,unknown>};
export type WorkflowGraph={format:1;input:{kind:'scientific-scene'|'text-file'|'source-file';semantic?:string;axes?:string[];dtype?:string;calibration?:'optional'|'required'};nodes:WorkflowStep[]};
export type Workflow=Exact & {name:string;graph:WorkflowGraph;removed:boolean};
export type Attempt={id:string;revision:number;node:string;item:number;number:number;status:string;note:string;output:Exact|null;working?:Exact;research?:{id:string;request:string}};
export type WorkflowRun=Exact & {status:string;control:string;executionObservation?:{state:'owned'|'interrupted'|'unstarted'|'idle';reason:string};definition:{id:string;revision:number;digest:string;name:string;graph:WorkflowGraph};inputs:Exact[];attempts:Attempt[]};
export const namedInputKeys=(graph:WorkflowGraph):string[]=>[...new Set(graph.nodes.flatMap(n=>n.needs.filter(d=>d.startsWith('input:')).map(d=>d.slice(6))))];
export function connectStep(graph:WorkflowGraph,from:string,to:string):WorkflowGraph{
 const next=structuredClone(graph),target=next.nodes.find(n=>n.id===to);
 if(!target||from!=='input'&&!next.nodes.some(n=>n.id===from))throw new Error('Choose existing steps');
 target.needs=target.kind==='compare'?[...new Set([...target.needs,from])]:[from];
 if(target.needs.length>3)throw new Error('Compare at most three results');
 const visited=new Set(['input',...next.nodes.flatMap(n=>n.needs.filter(d=>d.startsWith('input:')))]),remaining=new Set(next.nodes.map(n=>n.id));
 while(remaining.size){const ready=next.nodes.filter(n=>remaining.has(n.id)&&n.needs.every(d=>visited.has(d)));if(!ready.length)throw new Error('That connection creates a cycle');for(const n of ready){remaining.delete(n.id);visited.add(n.id);}}
 return next;
}
export function removeStep(graph:WorkflowGraph,id:string):WorkflowGraph{
 if(graph.nodes.some(n=>n.needs.includes(id)))throw new Error('Change downstream connections before removing this step');
 if(graph.nodes.length===1)throw new Error('Keep at least one step');
 return {...graph,nodes:graph.nodes.filter(n=>n.id!==id)};
}
export function requestFor(state:{body:string;request:string},body:unknown,make=()=>crypto.randomUUID().replaceAll('-','')):string{
 const key=JSON.stringify(body);if(state.body!==key||!state.request){state.body=key;state.request=make();}return state.request;
}
export async function read<T>(url:string,body?:unknown):Promise<T>{
 const response=await fetch(url,body===undefined?undefined:{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'workflow'},body:JSON.stringify(body)});
 const data=await response.json();if(!response.ok||data.ok!==true)throw new Error(data.says||'Workflow request was not confirmed. Retry the unchanged request.');return data as T;
}
export const command=<T>(body:unknown)=>read<T>('/api/workflows/command',body);

export const sameReference=(a:Exact|undefined,b:Exact|undefined):boolean=>!!a&&!!b&&a.id===b.id&&a.session===b.session&&a.revision===b.revision&&a.digest===b.digest;

export function orderedSteps(graph:WorkflowGraph):WorkflowStep[]{
 const out:WorkflowStep[]=[],seen=new Set(['input',...graph.nodes.flatMap(n=>n.needs.filter(d=>d.startsWith('input:')))]);
 while(out.length<graph.nodes.length){const ready=graph.nodes.filter(n=>!seen.has(n.id)&&n.needs.every(id=>seen.has(id)));if(!ready.length)throw new Error('The workflow has a cycle or missing input');for(const n of ready){out.push(n);seen.add(n.id);}}
 return out;
}

export function confirmRequest(state:{body:string;request:string},request:string):void{
 if(state.request===request){state.body='';state.request='';}
}
