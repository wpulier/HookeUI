// Synthetic reopened-window/HTTP boundary; actual browser/native acceptance remains separate.
import {afterEach, expect, test, vi} from 'vitest';
import {openStudyMasks} from './workspace-study-content';
import {syntheticDOM, TestElement, fire} from './workspace-windows.test-support';
import type {StudyRetainedView} from './workspace-study-restoration';
const artifacts=vi.hoisted(()=>({document:vi.fn(),restoreGeometry:vi.fn()}));
const scientific=vi.hoisted(()=>({openStudyScientific:vi.fn(async()=>{})}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
vi.mock('./scientific-document',()=>scientific);
afterEach(()=>{vi.unstubAllGlobals();vi.clearAllMocks();});
const saved:StudyRetainedView={resource:{day:'project-a',id:'e1',version:3,picture:0},placementId:'placement-a',title:'Saved particles',zoom:1,pan:[.5,.5]};
const source={id:'e1',session:'project-a',revision:3};
const operation={id:'retain-population-labels-v1',implementation:'builtin',preparation:'none',input:'saved-population-revision',output:'native-project-arrays'};
const overlay={...source,width:96,height:96,status:'saved',count:1,members:[{id:'c3',polygons:[[[1,1],[2,1],[2,2]]],measure:{areaPx:149}}]};
const accepted={id:'p1',session:'project-a',revision:1,document:{title:'Retained particles',state:{populationSource:{source}}}};
const response=(body:unknown)=>new Response(JSON.stringify(body));
async function settle(){for(let i=0;i<30;i++)await Promise.resolve();}
function setup(){
 const dom=syntheticDOM(),root=new TestElement('section');dom.host.append(root);
 const observers=new Set<()=>void>();let dispose=()=>{};
 vi.stubGlobal('MutationObserver',class{private callback:()=>void;constructor(callback:()=>void){this.callback=callback;}observe(){observers.add(this.callback);}disconnect(){observers.delete(this.callback);}});
 Object.assign(root,{checkVisibility:()=>!root.hidden,isConnected:true});
 Object.assign(root.dataset,{project:'project-a',workspaceView:'main',placementId:'placement-a'});
 Object.assign(dom.document.body.dataset,{workspaceProject:'project-a',activeWorkspaceView:'main'});
 Object.assign(dom.document,{createElementNS:(_ns:string,tag:string)=>new TestElement(tag)});
 vi.stubGlobal('document',dom.document);vi.stubGlobal('window',new EventTarget());
 artifacts.document.mockImplementation((_key,_title,content,_dispose,metadata)=>{dispose=_dispose;Object.assign(root.dataset,metadata);root.append(content);return root;});
 const fetcher=vi.fn(async(url:string,options?:RequestInit)=>response(url.includes('/overlay?')?overlay:options?.method==='POST'?{project:accepted}:{operation,source,readiness:{ready:true}}));vi.stubGlobal('fetch',fetcher);
 return{dom,root,fetcher,changed:()=>{for(const callback of observers)callback();},dispose:()=>dispose()};
}
test('reopened saved Masks retain their exact revision through shared readiness and acceptance, then explicitly open it',async()=>{
 const w=setup();await openStudyMasks(saved);
 const retain=w.root.querySelector('.population-native');expect(retain).not.toBeNull();
 fire(retain!,'click');await settle();
 const post=w.fetcher.mock.calls.find(([,options])=>options?.method==='POST');expect(post).toBeDefined();
 expect(JSON.parse(String(post![1]!.body))).toMatchObject({operation:operation.id,...source});
 expect(scientific.openStudyScientific).not.toHaveBeenCalled();
 fire(w.root.querySelector('.population-native-open')!,'click');await settle();
 expect(scientific.openStudyScientific).toHaveBeenCalledExactlyOnceWith({resource:{day:'project-a',id:'p1',version:1,picture:null},title:'Retained particles',zoom:1,pan:[.5,.5]});
});

test.each(['hidden','project','tab','closed'])('%s owned window aborts pending retention and ignores a late success',async change=>{
 const w=setup();await openStudyMasks(saved);
 let finish!:(response:Response)=>void,signal:AbortSignal|null|undefined;
 w.fetcher.mockImplementation(async(_url,options)=>{
  if(options?.method==='POST'){signal=options.signal;return new Promise<Response>(resolve=>{finish=resolve;});}
  return response({operation,source,readiness:{ready:true}});
 });
 fire(w.root.querySelector('.population-native')!,'click');await settle();
 expect(signal?.aborted).toBe(false);
 if(change==='hidden')w.root.hidden=true;
 if(change==='project')w.dom.document.body.dataset.workspaceProject='project-b';
 if(change==='tab')w.dom.document.body.dataset.activeWorkspaceView='other';
 if(change==='closed')w.dispose();
 w.changed();expect(signal?.aborted).toBe(true);
 finish(response({project:accepted}));await settle();
 expect(w.root.querySelector('.population-native-open')!.hidden).toBe(true);
 fire(w.root.querySelector('.population-native-open')!,'click');await settle();
 expect(scientific.openStudyScientific).not.toHaveBeenCalled();
});

test('historical Masks keep revision zero and expose readiness refusal instead of retaining the latest Population',async()=>{
 const w=setup();w.fetcher.mockImplementation(async url=>response(url.includes('/overlay?')?{...overlay,revision:0}:
  {operation,source:{...source,revision:0},readiness:{ready:false}}));
 await openStudyMasks({...saved,resource:{...saved.resource,version:0}});
 fire(w.root.querySelector('.population-native')!,'click');await settle();
 expect(w.fetcher.mock.calls.map(([url])=>url)).toContain('/api/populations/e1/native-labels?session=project-a&revision=0');
 expect(w.fetcher.mock.calls.some(([,options])=>options?.method==='POST')).toBe(false);
 expect(w.root.textContent).toContain('This revision is not ready');
});
