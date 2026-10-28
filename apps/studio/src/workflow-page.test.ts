import {it,expect,vi,afterEach} from 'vitest';
vi.mock('./workflow-editor',()=>({mountWorkflows:vi.fn()}));
import {WorkflowPage} from './workflow-page';
afterEach(()=>vi.unstubAllGlobals());
it('refreshes a reused Project library when reopened without interrupting an already visible selection',async()=>{
 class Element {hidden=true;textContent='';append(){}replaceChildren(){}remove(){}}
 vi.stubGlobal('document',{createElement:()=>new Element()});
 const events=new EventTarget();vi.stubGlobal('window',events);
 const seen:string[]=[];events.addEventListener('workflow-page-visible',e=>seen.push((e as CustomEvent<string>).detail));
 let session='a';const page=new WorkflowPage(new Element() as unknown as HTMLElement,()=>session,()=>{});
 await page.show();await page.show();expect(seen).toEqual(['a']);
 page.hide();await page.show();expect(seen).toEqual(['a','a']);
 session='b';await page.show();session='a';await page.show();expect(seen).toEqual(['a','a','b','a']);
});
