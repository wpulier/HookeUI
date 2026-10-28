/** Launch the existing local researcher against a saved Study, with an explicit allowance. */
import type {Effort} from './research-efforts';
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const n=document.createElement(tag);n.textContent=text;return n;};
async function reply(r:Response){const b=await r.json();if(!r.ok||b.ok===false)throw new Error(String(b.says||b.error||'Researcher request failed'));return b;}
export function mountLocalStudyResearcher(root:HTMLElement,study:Effort,fetcher:typeof fetch=fetch):void{
  const box=node('section');box.className='study-rail-section';box.append(node('h3','Hooke researcher'));
  const notice=node('p','Checking the configured researcher…');notice.setAttribute('role','status');box.append(notice);root.append(box);
  const current=()=>root.contains(box),url=`/api/research-efforts/${encodeURIComponent(study.id)}/local-researcher`;
  void fetcher(`${url}?session=${encodeURIComponent(study.session)}`,{cache:'no-store'}).then(reply).then(({harness,efforts})=>{
    if(!current())return;
    notice.textContent=harness.available?`Uses ${harness.model}. Reads Study evidence and runs local scientific workflows. Model calls use your configured provider and allowance.`:String(harness.reason);
    if(!harness.available||study.status!=='active')return;
    const form=node('form'),brief=node('textarea'),effort=node('select'),start=node('button','Start research');
    brief.setAttribute('aria-label','Research question');brief.value=study.objective;
    effort.setAttribute('aria-label','Research effort');
    for(const item of efforts as {id:string;maximumUsd:number}[]){
      const option=node('option',`${item.id} · reserves $${item.maximumUsd.toFixed(2)}`);option.value=item.id;effort.append(option);
    }
    start.type='submit';form.append(brief,effort,start);box.append(form);let request:string|null=null;
    form.onsubmit=async event=>{event.preventDefault();if(!current()||!brief.value.trim())return;
      request ||= crypto.randomUUID();brief.disabled=effort.disabled=start.disabled=true;notice.textContent='Starting researcher…';
      try{const result=await reply(await fetcher(url,{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'research-effort'},
        body:JSON.stringify({session:study.session,request,brief:brief.value,effort:effort.value})}));
        if(current()){notice.textContent=`Researcher started. Open it in Researchers; its findings and experiment receipts remain with this Study.`;
          const link=node('a','Open researcher');link.href=`#research/${encodeURIComponent(result.researcher)}${result.execution?.day?`?day=${encodeURIComponent(result.execution.day)}`:''}`;box.append(link);}
        request=null;
      }catch(error){if(current()){notice.textContent=`${String(error)}. Retry keeps the same launch identity.`;start.disabled=false;}}
    };
  }).catch(error=>{if(current())notice.textContent=String(error);});
}
