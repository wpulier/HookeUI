import {Composer} from './composer';
import type {Effort} from './research-efforts';

export interface Reference { id:string; session:string; revision:number; digest:string }
export interface Recipient { actor:string; label:string }
export interface StudyQuestion {
  id:string; question:string; basis:Reference; recipient:Recipient; byLabel:string;
  answer:null|{statement:string; limitations:string; evidence:Reference[]; byLabel:string; by:string};
}
export interface QuestionState { questions:StudyQuestion[]; recipients:Recipient[] }
const headers={'Content-Type':'application/json','X-MicroscopeMind':'research-effort'};
async function response(reply:Response) {
  const value=await reply.json();
  if(!reply.ok||value.ok===false)throw new Error(value.says||value.error||'Could not reach this Study. Your question is kept.');
  return value;
}
const url=(study:Effort)=>`/api/research-efforts/${encodeURIComponent(study.id)}/questions`;
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,cls='',words='')=>{const n=document.createElement(tag);n.className=cls;n.textContent=words;return n;};

/** Questions and answers live in the Study. No provider, subprocess or side-panel dispatch here. */
export function studyConversation(initial:Effort, options:{
  current:()=>boolean; open:(ref:Reference,title:string)=>Promise<void>;
  fetcher?:typeof fetch; copy?:(text:string)=>Promise<void>;
}) {
  let study=initial, sending=false, reading=false, signature='', loaded=false, sendError='';
  let retry:{text:string;recipient:string;body:Record<string,unknown>}|null=null;
  const fetcher=options.fetcher??fetch;
  const root=node('section','study-conversation');root.setAttribute('aria-label','Study conversation');
  const log=node('div','study-conversation-log');log.setAttribute('role','log');log.setAttribute('aria-label','Study questions and answers');
  const status=node('p','study-ask-status','Loading connected contributors…');status.setAttribute('role','status');
  const select=node('select','study-question-recipient');select.setAttribute('aria-label','Ask connected agent');
  const compose=new Composer(null,{label:'Ask about this study',placeholder:'Ask about this study'});
  compose.send.setAttribute('aria-label','Send Study question');
  compose.chips.hidden=false;compose.chips.append(node('span','','Ask'),select);
  const refreshButton=node('button','study-quiet','Refresh replies');refreshButton.type='button';
  const controls=node('div','study-conversation-controls');controls.append(status,refreshButton);
  root.append(log,controls,compose.root);
  function ready(){compose.send.disabled=sending||!loaded||!select.value||!options.current();select.disabled=sending;compose.field.readOnly=sending;}
  compose.field.addEventListener('input',ready);compose.field.addEventListener('focus',ready);
  function render(rows:StudyQuestion[]){
    const next=JSON.stringify(rows);if(next===signature)return;signature=next;
    const follow=log.scrollHeight-log.scrollTop-log.clientHeight<40;
    log.replaceChildren();
    for(const question of rows){
      const card=node('article','study-question');card.dataset.questionId=question.id;
      card.append(node('small','',question.byLabel),node('p','study-question-words',question.question));
      if(question.answer){
        const answer=question.answer;
        card.append(node('strong','study-answer-author',answer.byLabel),node('p','study-answer-words',answer.statement));
        if(answer.limitations)card.append(node('p','study-answer-limits',answer.limitations));
        const evidence=node('details','study-answer-evidence');evidence.append(node('summary','',`Evidence · ${answer.evidence.length}`));
        for(const ref of answer.evidence){
          const link=node('button','study-quiet',`Open evidence · revision ${ref.revision}`);link.type='button';link.title=ref.id;
          link.addEventListener('click',()=>{void options.open(ref,'Answer evidence').catch(error=>{status.textContent=String(error.message||error);});});evidence.append(link);
        }
        card.append(evidence,node('small','study-answer-status','Agent answer · not a reviewed finding'));
      }else{
        card.append(node('p','study-answer-waiting',`Waiting for ${question.recipient.label} to respond.`));
        const copy=node('button','study-quiet','Copy question for agent');copy.type='button';
        copy.addEventListener('click',()=>{const prompt=`Use your existing Hooke connection to answer question ${question.id} in Study ${study.id}, Project ${study.session}. Read its pinned context through GET studies/${study.id}/questions/${question.id}?session=${study.session}, inspect the cited retained evidence, then publish an answer through POST studies/${study.id}/questions/${question.id}/answer. Use your own connection identity. Report limitations; do not launch experiments or submit anything.`;
          void (options.copy??(text=>navigator.clipboard.writeText(text)))(prompt).then(()=>{status.textContent='Question instructions copied.';}).catch(()=>{status.textContent='Could not copy. The saved question remains in this Study.';});});card.append(copy);
      }
      log.append(card);
    }
    if(follow)log.scrollTop=log.scrollHeight;
  }
  async function refresh(){
    if(reading||!options.current())return;reading=true;
    try{
      const value=await response(await fetcher(`${url(study)}?session=${encodeURIComponent(study.session)}`,{headers,cache:'no-store'})) as QuestionState;
      if(!options.current())return;
      const chosen=select.value;
      const recipientSignature=JSON.stringify(value.recipients);
      if(select.dataset.options!==recipientSignature){select.replaceChildren();for(const recipient of value.recipients){const option=node('option','',recipient.label);option.value=recipient.actor;select.append(option);}if(value.recipients.some(r=>r.actor===chosen))select.value=chosen;select.dataset.options=recipientSignature;}
      loaded=true;render(value.questions);
      if(!sending)status.textContent=sendError||(value.recipients.length?'Replies stay here. Your agent must pick up the saved question.':'No connected contributor has access to this Project. Manage access in Settings → Connected agents.');
    }catch(error){if(options.current())status.textContent=error instanceof Error?error.message:String(error);}
    finally{reading=false;ready();}
  }
  refreshButton.addEventListener('click',()=>void refresh());
  compose.root.addEventListener('submit',event=>{
    event.preventDefault();const text=compose.field.value.trim();if(!text||sending||!select.value||!loaded||!options.current())return;
    if(!retry||retry.text!==text||retry.recipient!==select.value)retry={text,recipient:select.value,body:{session:study.session,request:crypto.randomUUID(),question:text,basis:{id:study.id,session:study.session,revision:study.revision,digest:study.digest},recipient:select.value}};
    const captured=retry;sendError='';sending=true;root.dataset.pending='true';ready();status.textContent='Saving your question…';
    void responseAfterSave(captured);
  });
  async function responseAfterSave(captured:NonNullable<typeof retry>){
    try{await response(await fetcher(url(study),{method:'POST',headers,body:JSON.stringify(captured.body)}));
      if(!options.current())return;compose.set('');retry=null;status.textContent='Question saved. Waiting for your agent.';
    }catch(error){if(options.current()){sendError=`${error instanceof Error?error.message:String(error)} Retry keeps the same question identity.`;status.textContent=sendError;}}
    finally{sending=false;delete root.dataset.pending;ready();void refresh();}
  }
  ready();
  return {root,refresh,update(next:Effort){study=next;ready();}};
}
