import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),removeDocument:vi.fn(),applyDocument:vi.fn(),document:vi.fn(),clearDocuments:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {LAYOUT_DIAGNOSTIC,WorkspaceViews,savedTitle} from './workspace-views';
import {rebasedLayout,type WorkspaceLayout,type WorkspaceView} from './workspace-views-model';
import {windowTitle} from './workspace-windows';
afterEach(()=>vi.unstubAllGlobals());

// Live 2026-10-29: a window the Assistant opened without a title was saved with an empty name, the server refused the
// whole arrangement, and a yellow toast said "a tab needs a bounded name and identity"; a layout save that met a newer
// revision put "This project layout changed before this save…" over the Talk bar. Both are the app's to resolve.

const item=(placementId:string,title='Cells')=>({resource:{day:'p',id:'d1',version:1,picture:null},placementId,title,zoom:1,pan:[.5,.5]}) as unknown as WorkspaceView['items'][number];
const view=(id:string,items:WorkspaceView['items']=[]):WorkspaceView=>({id,title:id,specimen:{visible:true,rect:null},items,notes:[]});

test('a window opened with no title has a real name, shown and saved',()=>{
 expect(windowTitle({title:'',source:{kind:'saved-image',media:'reference'}} as never)).toBe('Reference photo');
 expect(windowTitle({title:'  ',source:{kind:'live',media:'capture'}} as never)).toBe('Live magnification');
 expect(windowTitle({title:'Stomata',source:{kind:'saved-image',media:'reference'}} as never)).toBe('Stomata');
 expect(savedTitle('')).toBe('Saved work');
 expect(savedTitle('',{querySelector:()=>({textContent:'Reference photo'})} as never)).toBe('Reference photo');
 expect(savedTitle('x'.repeat(900))).toHaveLength(512);
});

test('a save rebased on a newer revision keeps what was added there and everything shown here',()=>{
 const local:WorkspaceLayout={schema:6,coordinates:'stage',active:'a',views:[view('a',[item('mine')]),view('b')]};
 const known={views:[view('a'),view('b'),view('gone')]};
 const server={schema:6,views:[view('a',[item('theirs')]),view('b'),view('gone'),view('added',[item('new-tab-item')])]};
 const next=rebasedLayout(local,server,known);
 expect(next.views.map(v=>v.id)).toEqual(['a','b','added']); // a Tab removed here is not brought back
 expect(next.views[0].items.map(i=>i.placementId)).toEqual(['mine','theirs']);
 expect(next.active).toBe('a');
 expect(local.views[0].items).toHaveLength(1); // the original is not changed
});

function saving(replies:Record<string,unknown>[]){
 const events:string[]=[],notice=vi.fn(),requests:Record<string,unknown>[]=[];
 const target=Object.assign(new EventTarget(),{setTimeout:(fn:()=>void)=>setTimeout(fn,0)});
 target.addEventListener(LAYOUT_DIAGNOSTIC,(event)=>events.push(String((event as CustomEvent).detail)));
 vi.stubGlobal('window',target);
 const layout:WorkspaceLayout={schema:6,coordinates:'stage',active:'a',views:[view('a',[item('mine')])]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,changing:false,navigating:false,
  dirty:true,conflict:false,saving:null,timer:0,lastSaved:'',revision:3,rebases:0,layout,noteTimers:new Map(),
  recovery:{clear:vi.fn(),put:vi.fn()},retry:{hidden:true},recover:{hidden:true},completions:[],pending:[],
  say:vi.fn(),cache:vi.fn(),retainCameraStatus:vi.fn(),updateRecovery:vi.fn(),render:vi.fn(),
  opts:{notice,request:vi.fn(async(_type:string,fields:Record<string,unknown>)=>{requests.push(fields);return replies.shift()??{ok:true,revision:9};})}});
 return {current,events,notice,requests};
}
const settle=()=>new Promise(resolve=>setTimeout(resolve,5));

test('a layout save that meets a newer revision is rebased and saved again, never a toast',async()=>{
 const t=saving([{ok:false,code:'conflict',error:'This project layout changed before this save. Review your local arrangement before saving.',
  current:{revision:7,layout:{schema:6,coordinates:'stage',views:[view('a',[item('theirs')])]}}}]);
 await t.current.flush();await settle();await t.current.flush();
 expect(t.notice).not.toHaveBeenCalled();
 expect(t.requests.map(r=>r.base)).toEqual([3,7]);
 const saved=t.requests[1].layout as {views:WorkspaceView[]};
 expect(saved.views[0].items.map(i=>i.placementId)).toEqual(['mine','theirs']);
 expect(t.current.conflict).toBe(false);
});

test('an internal check of the arrangement goes to Activity, never a toast over the work',async()=>{
 const t=saving([{ok:false,code:'validation',error:'a tab needs a bounded name and identity'}]);
 await t.current.flush();
 expect(t.notice).not.toHaveBeenCalled();
 expect(t.events).toEqual(['The Tab layout was not saved: a tab needs a bounded name and identity']);
 expect(t.current.retry.hidden).toBe(false); // Retry stays on the layout line
});
