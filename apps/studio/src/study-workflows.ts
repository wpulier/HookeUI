/** Local workflow experiments retain the same receipts as Tools and connected agents. */
import type { Effort, StudyInput } from './research-efforts';
import {namedInputKeys,type Workflow} from './workflow-model';
type Operation = {byLabel:string;state:string;workflow?:{id:string;status:string;executionObservation?:{state:string;reason:string};attempts:{status:string;note?:string;output?:{id:string;revision:number}}[]}};
const node = <K extends keyof HTMLElementTagNameMap>(tag:K,text='') => {const n=document.createElement(tag);n.textContent=text;return n;};
async function reply(r:Response){const b=await r.json();if(!r.ok||b.ok===false)throw new Error(String(b.says||b.error||'Workflow request failed'));return b;}

export function mountStudyWorkflows(root:HTMLElement,study:Effort,inputs:StudyInput[],
  openFile:((ref:{id:string;revision:number},name:string)=>Promise<unknown>)|undefined,fetcher:typeof fetch=fetch):void{
  const box=node('section');box.className='study-rail-section';
  box.append(node('h3','Local experiments'),node('p','Run a saved workflow on exact Project inputs. Closing this Study does not stop its work.'));
  const list=node('div'),notice=node('p');notice.setAttribute('role','status');box.append(list,notice);root.append(box);
  const current=()=>root.contains(box);
  const url=`/api/research-efforts/${encodeURIComponent(study.id)}/workflows`;
  const refresh=async()=>{try{
    const result=await reply(await fetcher(`${url}?session=${encodeURIComponent(study.session)}`,{cache:'no-store'}));
    if(!current())return;list.replaceChildren();
    for(const op of result.operations as Operation[]){
      const row=node('details');row.append(node('summary',`${op.byLabel} · ${op.workflow?.status||op.state}`));
      if(op.workflow){const open=node('a','Open workflow run');open.href=`#workflows?run=${encodeURIComponent(op.workflow.id)}`;row.append(open);
        if(op.workflow.executionObservation&&['interrupted','unstarted'].includes(op.workflow.executionObservation.state))row.append(node('p',op.workflow.executionObservation.reason));}
      for(const attempt of op.workflow?.attempts||[]){
        const line=node('p',`${attempt.status}${attempt.note?` — ${attempt.note}`:''}`);
        if(attempt.output&&openFile){const ref=attempt.output,b=node('button','Open retained result');b.type='button';
          b.onclick=()=>{void openFile(ref,'Experiment result').catch(e=>{notice.textContent=String(e);});};line.append(b);}
        row.append(line);
      }
      list.append(row);
    }
    if(!result.operations.length)list.append(node('p','No workflow experiments yet.'));
  }catch(e){if(current())notice.textContent=String(e);}};
  const refreshButton=node('button','Refresh experiment results');refreshButton.type='button';refreshButton.onclick=()=>{void refresh();};box.append(refreshButton);
  const link=node('a','Open scientific workflows');link.href='#workflows';box.append(link);
  void refresh();
  if(study.status!=='active'||!inputs.length)return;
  void fetcher('/api/workflows',{cache:'no-store'}).then(reply).then(body=>{
    if(!current())return;const workflows=body.workflows as Workflow[];if(!workflows.length)return;
    const form=node('form'),selection=node('select'),source=node('select'),run=node('button','Run workflow');run.type='submit';
    selection.setAttribute('aria-label','Saved workflow');source.setAttribute('aria-label','Exact Project input');
    for(const w of workflows){const o=node('option',w.name);o.value=w.id;selection.append(o);}
    for(const i of inputs){const o=node('option',`${i.title} · revision ${i.revision}`);o.value=i.id;source.append(o);}
    const named=node('div'),bindings=new Map<string,HTMLSelectElement>();
    const populate=(select:HTMLSelectElement,required=false)=>{if(required){const blank=node('option','Choose a retained input');blank.value='';select.append(blank);}
      for(const i of inputs){const o=node('option',`${i.title} · revision ${i.revision}`);o.value=i.id;select.append(o);}};
    const chooseWorkflow=()=>{named.replaceChildren();bindings.clear();const w=workflows.find(w=>w.id===selection.value);if(!w)return;
      for(const key of namedInputKeys(w.graph)){const label=node('label',key),select=node('select');select.required=true;
        select.setAttribute('aria-label',`${key} input`);populate(select,true);bindings.set(key,select);label.append(select);named.append(label);}};
    selection.onchange=chooseWorkflow;chooseWorkflow();
    form.append(selection,source,named,run);box.append(form);let request:string|null=null;
    form.onsubmit=async event=>{
      event.preventDefault();if(!current())return;
      const w=workflows.find(w=>w.id===selection.value),i=inputs.find(i=>i.id===source.value);if(!w||!i)return;
      const exact=(input:StudyInput)=>({id:input.id,session:study.session,revision:input.revision,digest:input.digest});
      const chosen:Record<string,ReturnType<typeof exact>>={};
      for(const [key,select] of bindings){const input=inputs.find(i=>i.id===select.value);if(!input){notice.textContent=`Choose the ${key} input.`;return;}chosen[key]=exact(input);}
      request ||= crypto.randomUUID();selection.disabled=source.disabled=run.disabled=true;for(const select of bindings.values())select.disabled=true;notice.textContent='Admitting workflow…';
      try{await reply(await fetcher(url,{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'research-effort'},body:JSON.stringify({
        session:study.session,request,workflow:w.id,revision:w.revision,inputs:[exact(i)],bindings:chosen})}));
        request=null;if(current()){selection.disabled=source.disabled=false;for(const select of bindings.values())select.disabled=false;notice.textContent='Workflow admitted. Read its retained results below.';await refresh();}
      }catch(e){if(current())notice.textContent=`${String(e)}. Retry preserves this request.`;}
      finally{if(current())run.disabled=false;}
    };
  }).catch(e=>{if(current())notice.textContent=String(e);});
}
