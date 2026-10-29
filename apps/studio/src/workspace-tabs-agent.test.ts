import {afterEach,expect,test,vi} from 'vitest';
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({blockReason:()=>null})}));
import {WorkspaceViews} from './workspace-views';
afterEach(()=>vi.unstubAllGlobals());
function fixture(){
 vi.stubGlobal('document',{hidden:false,querySelector:()=>null,body:{dataset:{}}});
 const view={id:'main',title:'Main',specimen:{visible:true,rect:null},items:[] as any[],notes:[]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout:{schema:5,active:'main',views:[view]},openIds:['main'],
  completionRestores:new Set(),loaded:new Set(['main']),capture:vi.fn(),changed:vi.fn(),render:vi.fn(),flush:vi.fn(async()=>{}),roots:()=>[],
  activate:vi.fn(async()=>true),opts:{notice:vi.fn()},dirty:false,conflict:false});
 return {current,view};
}
test('agent insertion saves and restores the exact active placement',async()=>{
 const {current,view}=fixture(),item={resource:{day:'p',id:'tb1',version:2,picture:null},presentation:'datatable',title:'Counts',zoom:1,pan:[.5,.5]};
 const result=await current.agentCommand('p',{action:'add',tab:'main',item});
 expect(result.ok).toBe(true);expect(result.studioVisible).toBe(true);expect(view.items[0].resource).toEqual(item.resource);
 expect(current.completionRestores.has(view.items[0].placementId)).toBe(true);expect(current.activate).toHaveBeenCalledWith('main',true);
});
test.each(['navigation','reopened-project'])('agent refuses %s beginning during save',async kind=>{
 const {current,view}=fixture();current.flush=vi.fn(async()=>{if(kind==='navigation')current.navigating=true;else current.generation++;});
 const result=await current.agentCommand('p',{action:'rename',tab:'main',title:'Changed'});
 expect(result.ok).toBe(false);expect(view.title).toBe('Main');expect(current.changed).not.toHaveBeenCalled();
});


test.each(['saved','unsaved','switched'])('new run destination admits only its exact saved Tab: %s',async state=>{
 const {current}=fixture();current.addView=vi.fn(async()=>{current.layout.views.push({id:'result',title:'Results',items:[],notes:[]});current.layout.active='result';return 'result';});
 current.flush=vi.fn(async()=>{if(state==='unsaved')current.dirty=true;if(state==='switched')current.generation++;});
 const creating=current.newRunOrigin('run1','Results','p');
 if(state==='saved')await expect(creating).resolves.toEqual({session:'p',tab:'result',placementId:'pending-run1',run:'run1'});
 else await expect(creating).rejects.toThrow('not confirmed saved');
});


test('new result Tab preserves a playing recording without seeking or reselecting it',async()=>{
 const {current}=fixture(),selectInput=vi.fn();current.layout.views.push({id:'result',title:'Results',items:[],notes:[],input:{source:'recording',position:10}} as never);
 Object.assign(current,{opts:{captureInput:()=>({source:'recording',position:12}),selectInput,notice:vi.fn()},
  loaded:new Set(['result']),navSave:vi.fn(),clearFlash:vi.fn(),applySpecimen:vi.fn(),visibility:vi.fn(),resumeReveal:vi.fn(),observeInput:vi.fn()});
 delete current.activate;
 await Reflect.get(WorkspaceViews.prototype,'activate').call(current,'result',false,false,false,true);
 expect(selectInput).not.toHaveBeenCalled();expect(current.layout.active).toBe('result');
});

test('researcher Tabs join the Assistant\'s Tab report; while one is in front no work Tab is the selected one',async()=>{
 const {current}=fixture();current.opts.research={report:()=>[{id:'research:r1',title:'Researcher 1 · Counting',open:true,selected:true,kind:'researcher'},{id:'research:r2',title:'Researcher 2 · Shapes',open:false,selected:false,kind:'researcher'}],switch:vi.fn(),close:vi.fn()};
 const result=await current.agentCommand('p',{action:'list'});
 expect(result.tabs.map((t:{id:string;selected:boolean;open:boolean})=>`${t.id}:${t.selected}:${t.open}`)).toEqual(['main:false:true','research:r1:true:true','research:r2:false:false']);
});
