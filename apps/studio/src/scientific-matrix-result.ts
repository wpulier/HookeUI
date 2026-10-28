/** Retained numerical outputs: render the producer's summary, never recompute on open. */
import {showScientificSource} from './scientific-file-import';
const object=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
export function showMatrixResult(root:HTMLElement,state:unknown,ref:{id:string;day:string;version:number},resources:unknown,
 open:(target:{id:string;version:number;title:string})=>Promise<unknown>):void {
 const op=object(state)?state.matrixOperation:null;
 if(!object(op)||op.format!==1||typeof op.capability!=='string'||!object(op.output)||!object(op.output.summary)||!object(op.inputs))
  throw new Error('This saved result has no supported computation record. Keep its exact revision.');
 showScientificSource(root,state,ref,undefined,resources,true);
 root.append(node('h3','Recorded result'),node('p',`Produced by ${op.capability}. These are recorded outputs, not an independent validation.`));
 const table=node('table');table.className='scientific-result-summary';
 for(const [key,value] of Object.entries(op.output.summary)){
  const row=node('tr'),label=node('th',key.replace(/([a-z])([A-Z])/g,'$1 $2'));
  label.scope='row';row.append(label,node('td',typeof value==='string'?value:JSON.stringify(value)));table.append(row);
 }
 root.append(table);
 const inputs=node('details');inputs.append(node('summary','Exact inputs and computation'));
 inputs.append(node('pre',JSON.stringify({capability:op.capability,generation:op.generation,parameters:op.params,runtime:op.runtime},null,2)));
 const notice=node('p');notice.setAttribute('role','status');
 for(const [port,input] of Object.entries(op.inputs)){
  if(!object(input)||input.session!==ref.day||typeof input.id!=='string'||!Number.isSafeInteger(input.revision)||Number(input.revision)<1)continue;
  const b=node('button',`Open ${port} input · v${input.revision}`);b.type='button';
  b.addEventListener('click',()=>{void open({id:input.id as string,version:Number(input.revision),title:port}).catch(e=>{notice.textContent=String(e);});});inputs.append(b);
 }
 root.append(inputs,notice);
}
