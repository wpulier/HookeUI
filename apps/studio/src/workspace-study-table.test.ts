// Synthetic network/presentation ordering. Actual visibility is repeated in the browser.
import {afterEach, expect, test, vi} from 'vitest';
import {openStudyTable} from './workspace-study-content';
import {syntheticDOM} from './workspace-windows.test-support';
import type {StudyRetainedView} from './workspace-study-restoration';

const artifacts = vi.hoisted(() => ({document: vi.fn(), restoreGeometry: vi.fn()}));
vi.mock('./workspace-study-artifacts', () => ({studyArtifacts: () => artifacts}));
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });
const saved: StudyRetainedView = {resource:{day:'project-a',id:'e1',version:0,picture:0},title:'Example',zoom:1,pan:[.5,.5]};
const response = (data: unknown) => new Response(JSON.stringify(data));
const result = {id:'e1',session:'project-a',revision:0,kind:'population',title:'Example',count:1,note:'Original pixels',
  memberLabel:'region',columns:[{key:'area',label:'Area',unit:'px²',type:'number'}],rows:[{area:42}],stats:[]};
async function settle() { for (let i=0;i<30;i++) await Promise.resolve(); }
function setup() {
  const dom=syntheticDOM(),root=dom.document.createElement('section');
  let visible=true,indexRevision=0,resolve!: (r:Response)=>void;
  Object.assign(root,{checkVisibility:()=>visible&&!root.hidden});
  Object.assign(root.dataset,{project:'project-a',workspaceView:'main'});
  Object.assign(dom.document.body.dataset,{workspaceProject:'project-a',activeWorkspaceView:'main'});
  dom.host.append(root);
  vi.stubGlobal('document',dom.document);vi.stubGlobal('window',{innerWidth:1200,innerHeight:800});vi.stubGlobal('requestAnimationFrame',dom.raf);
  artifacts.document.mockImplementation((_key,_title,content) => {
    if (!root.children.length) root.append(content);
    root.hidden=false;
    return root;
  });
  vi.stubGlobal('fetch',async (url:string) => url.includes('/table') ? new Promise<Response>(r=>{resolve=r;}) :
    response({day:'project-a',entries:url.includes('kind=tracks')?[]:[{id:'e1',day:'project-a',version:indexRevision,kind:'population'}]}));
  return {dom,root,revision:(value:number)=>{indexRevision=value;},visible:(value:boolean)=>{visible=value;},finish:async(data:unknown=result)=>{await settle();resolve(response(data));await settle();dom.frame();}};
}

test('a guided opening acknowledges exact rendered measurements, including an already open table',async()=>{
  const w=setup(),shown=vi.fn();openStudyTable(saved,{shown});
  expect(shown).not.toHaveBeenCalled();await w.finish();
  expect(w.root.textContent).toContain('42');expect(w.root.textContent).toContain('px²');
  expect(shown).toHaveBeenCalledExactlyOnceWith(expect.objectContaining({id:'e1',session:'project-a',revision:0}));
  openStudyTable(saved,{shown});await settle();w.dom.frame();
  expect(shown).toHaveBeenCalledTimes(2);
});

test.each(['hidden','dismissed','project','tab','layout','clipped','offscreen','failed','background'])('%s result cannot advance a guided workflow',async(reason)=>{
  const w=setup(),shown=vi.fn();openStudyTable(saved,{shown,background:reason==='background'});
  if(reason==='hidden')w.root.hidden=true;
  if(reason==='dismissed')w.root.dataset.dismissed='true';
  if(reason==='project')w.dom.document.body.dataset.workspaceProject='project-b';
  if(reason==='tab')w.dom.document.body.dataset.activeWorkspaceView='other';
  if(reason==='layout')w.visible(false);
  if(reason==='clipped')Object.assign(w.root.style,{width:'0px',height:'0px'});
  if(reason==='offscreen')w.root.style.left='1300px';
  await w.finish(reason==='failed'?{...result,revision:1}:result);
  expect(shown).not.toHaveBeenCalled();
});

test('an explicit opening can acknowledge a completed background restoration',async()=>{
  const w=setup(),shown=vi.fn();openStudyTable(saved,{background:true});await w.finish();
  openStudyTable(saved,{shown});await settle();w.dom.frame();
  expect(shown).toHaveBeenCalledOnce();
});

test('a failed read can be retried through its ordinary control before acknowledging the guide',async()=>{
  const w=setup(),shown=vi.fn();openStudyTable(saved,{shown});await w.finish({...result,revision:1});
  expect(shown).not.toHaveBeenCalled();
  const retry=w.root.querySelectorAll('button').find(button=>button.textContent==='Reopen measurements');
  expect(retry).toBeDefined();retry!.dispatchEvent(new Event('click'));
  await w.finish();expect(shown).toHaveBeenCalledOnce();
});

test('a previously rendered table withdrawn by a failed CSV read cannot acknowledge another opening',async()=>{
  const w=setup(),shown=vi.fn(),fetcher=globalThis.fetch;openStudyTable(saved);await w.finish();
  vi.stubGlobal('fetch',async()=>new Response(JSON.stringify({error:'The measurements revision changed.'}),{status:409}));
  w.root.querySelector('.meas-primary')!.dispatchEvent(new Event('click'));await settle();
  expect(w.root.textContent).toContain('revision changed');
  openStudyTable(saved,{shown});await settle();w.dom.frame();expect(shown).not.toHaveBeenCalled();
  vi.stubGlobal('fetch',fetcher);
  w.root.querySelectorAll('button').find(button=>button.textContent==='Reopen measurements')!.dispatchEvent(new Event('click'));
  await w.finish();expect(shown).toHaveBeenCalledOnce();
});

test('retry cannot replace a pinned revision with a newer result from the index',async()=>{
  const w=setup(),shown=vi.fn();openStudyTable(saved,{shown});await w.finish({...result,revision:1});
  w.revision(1);
  w.root.querySelectorAll('button').find(button=>button.textContent==='Reopen measurements')!.dispatchEvent(new Event('click'));
  await w.finish({...result,revision:1});
  expect(w.root.querySelector('table')).toBeNull();
  expect(w.root.textContent).toContain('measurements changed');expect(shown).not.toHaveBeenCalled();
});
