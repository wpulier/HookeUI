/** Lazy React island. Hidden Project editors retain their unsaved drafts. */
import type {Exact} from './workflow-model';
export class WorkflowPage {
 private entries=new Map<string,HTMLElement>();
 private loading=new Set<string>();
 private visibleSession='';
 private root:HTMLElement; private session:()=>string; private open:(ref:Exact,name:string)=>void;
 constructor(root:HTMLElement,session:()=>string,open:(ref:Exact,name:string)=>void){this.root=root;this.session=session;this.open=open;}
 async show():Promise<void>{
  const session=this.session(),reopened=this.root.hidden||this.visibleSession!==session;this.root.hidden=false;this.visibleSession=session;
  if(reopened&&session)window.dispatchEvent(new CustomEvent('workflow-page-visible',{detail:session}));
  for(const [id,entry] of this.entries)entry.hidden=id!==session;
  if(!session||this.entries.has(session)||this.loading.has(session))return;
  this.loading.add(session);
  const entry=document.createElement('div');entry.textContent='Loading workflows…';this.entries.set(session,entry);this.root.append(entry);
  try{const module=await import('./workflow-editor');entry.replaceChildren();module.mountWorkflows(entry,{session,currentSession:this.session,open:this.open});entry.hidden=session!==this.session();}
  catch(error){entry.textContent=error instanceof Error?error.message:String(error);const retry=document.createElement('button');retry.textContent='Retry loading Workflows';retry.onclick=()=>{this.entries.delete(session);entry.remove();void this.show();};entry.append(retry);}
  finally{this.loading.delete(session);}
 }
 hide():void{this.root.hidden=true;}
}
