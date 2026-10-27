/** Declared reader candidates use the existing scientific workspace and admission. */
type Source = { id: string; day: string; version: number };
type Reader = { key: string; name: string; generation: string | null; owner: string; canReview: boolean; reason: string; scope: string; blocked: string };
const BLOCKED: Record<string, string> = { incompatible: 'Can’t open this source', setup: 'Needs setup', device: 'Not available on this Mac' };
const object=(value:unknown):value is Record<string,unknown>=>value!==null&&typeof value==='object'&&!Array.isArray(value);
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const el=document.createElement(tag);el.textContent=text;return el;};
async function reply(response:Response):Promise<Record<string,unknown>> {
  const result:unknown=await response.json();
  if(!object(result)||!response.ok||result.ok!==true)throw new Error(object(result)?`${String(result.says??'Reader request failed.')} ${String(result.next??'')}`:'The reader response is invalid. Retry from the saved source.');
  return result;
}
function reader(value:unknown):Reader {
  if(!object(value)||typeof value.key!=='string'||typeof value.name!=='string'||typeof value.owner!=='string'
    ||typeof value.canReview!=='boolean'||!(typeof value.generation==='string'&&(/^[a-f0-9]{16}$/).test(value.generation)||value.generation===null&&value.canReview===false)
    ||typeof value.reason!=='string'||typeof value.scope!=='string')throw new Error('Reader declarations changed. Refresh before choosing.');
  return {key:value.key,name:value.name,generation:value.generation,owner:value.owner,canReview:value.canReview,reason:value.reason,scope:value.scope,blocked:typeof value.blocked==='string'?value.blocked:''};
}
export function sourceReaders(root:HTMLElement,source:Source,open:(target:{id:string;version:number;title:string})=>Promise<void>):void {
  const section=node('section'),find=node('button','Find readers'),status=node('p'),list=node('div');
  section.className='scientific-source-readers'; list.className='scientific-reader-choices';
  status.setAttribute('role','status');section.append(node('h3','Read this source'),find,status,list);root.append(section);
  let loading=false;
  find.addEventListener('click',()=>{void (async()=>{
    if(loading)return;loading=true;find.disabled=true;status.textContent='Checking declared readers…';
    try {
      const result=await reply(await fetch(`/api/scientific-files/${encodeURIComponent(source.id)}/readers?${new URLSearchParams({session:source.day,revision:String(source.version)})}`));
      if(!object(result.source)||result.source.id!==source.id||result.source.session!==source.day||result.source.revision!==source.version||!Array.isArray(result.readers))
        throw new Error('The reader list belongs to another source. Reopen this saved revision.');
      const rows=result.readers.map(reader);list.replaceChildren();
      const choice=object(result.choice)&&typeof result.choice.says==='string'?result.choice:null;
      const chosen=choice?.state==='chosen'&&typeof choice.capability==='string'?choice.capability:null;
      if(chosen)rows.sort((a,b)=>Number(b.key===chosen)-Number(a.key===chosen));
      status.textContent=choice?`${choice.says}${choice.state==='refused'&&typeof choice.next==='string'?` Next: ${choice.next}.`:''}${choice.state!=='refused'?' Preparing a reader workspace does not start it, and no declaration certifies scientific results.':''}`
        :rows.length?'Choose an integration to prepare a private reader workspace. Starting it is a separate action; none of these declarations certifies scientific results.':'No declared reader is installed for review. Add a reader integration in Models, then check again.';
      for(const item of rows){
        const row=node('article'),choose=node('button',`Review with ${item.name}`),message=node('p',item.reason?`${BLOCKED[item.blocked]?`${BLOCKED[item.blocked]}: `:''}${item.reason}`:item.scope),setup=node('a','Open integration setup');
        setup.hidden=item.blocked==='incompatible';
        setup.href=`#models/${encodeURIComponent(item.owner)}`;choose.disabled=!item.canReview;message.setAttribute('role','status');
        const identity=node('details'); identity.append(node('summary','Reader identity'),node('code',`${item.key} · ${item.generation??'Preparation incomplete'}`));
        const actions=node('div'); actions.className='scientific-reader-actions'; actions.append(choose,setup);
        if(item.key===chosen)row.className='scientific-reader-chosen';
        row.append(node('h4',item.key===chosen?`${item.name} · chosen by its declaration`:item.name),message,actions,identity);list.append(row);
        let pending=false;
        choose.addEventListener('click',()=>{void(async()=>{
          if(pending)return;pending=true;choose.disabled=true;message.textContent='Checking the exact source and prepared reader…';
          try {
            const result=await reply(await fetch(`/api/scientific-files/${encodeURIComponent(source.id)}/reader`,{method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'choose-scientific-reader'},
              body:JSON.stringify({session:source.day,revision:source.version,capability:item.key,generation:item.generation})}));
            const project=result.project;
            if(!object(project)||typeof project.id!=='string'||!/^p[a-f0-9]{32}$/.test(project.id)||project.id===source.id||project.session!==source.day||project.revision!==1)
              throw new Error('The reader workspace response changed. Reopen Project Data before retrying.');
            const document=project.document;
            const state=object(document)?document.state:null,origin=object(state)?state.sourceReader:null;
            if(!object(document)||typeof document.title!=='string'||!object(origin)||origin.capability!==item.key||origin.generation!==item.generation||!object(origin.source)
              ||origin.source.id!==source.id||origin.source.session!==source.day||origin.source.revision!==source.version)
              throw new Error('The reader workspace belongs to a different source or integration. Reopen the saved source.');
            const target={id:project.id,version:Number(project.revision),title:document.title},show=node('button','Open reader workspace');
            message.textContent='Reader workspace saved. Your original source remains unchanged. Start its private scientific session to inspect the result.';
            choose.hidden=true;actions.prepend(show);
            show.addEventListener('click',()=>{void open(target).catch(error=>{message.textContent=String(error instanceof Error?error.message:error);});});
          }catch(error){message.textContent=String(error instanceof Error?error.message:error);choose.disabled=false;}
          finally{pending=false;}
        })();});
      }
    }catch(error){status.textContent=String(error instanceof Error?error.message:error);}
    finally{loading=false;find.disabled=false;find.textContent='Check readers again';}
  })();});
}
