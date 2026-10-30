import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({root:{children:[] as unknown[]},place:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {holds,itemKind,nativeKind,researchShows,tabItems,ITEM_KINDS} from './workspace-model';
import {WorkspaceViews} from './workspace-views';
import type {WorkspaceView} from './workspace-views-model';
afterEach(()=>vi.unstubAllGlobals());

/** A window root on the Stage as its owner leaves it: its Tab, placement and what it says it is. */
function root(dataset:Record<string,string>,{classes=[] as string[],label='',close=vi.fn()}={}){
 const classSet=new Set(classes);
 return {dataset:{...dataset},hidden:false,classList:{contains:(c:string)=>classSet.has(c)},getAttribute:(n:string)=>n==='aria-label'?label:null,
  querySelector:(selector:string)=>selector.includes('Close')||selector.includes('close')?{click:close}:null,close} as unknown as HTMLElement&{close:ReturnType<typeof vi.fn>};
}
const view=(items:WorkspaceView['items'],extra:Partial<WorkspaceView>={}):WorkspaceView=>({id:'t1',title:'Cell results',specimen:{visible:false,rect:null},items,notes:[],...extra});
const item=(presentation:string|undefined,id:string,placementId:string,picture:number|null=null)=>({resource:{day:'p',id,version:1,picture},title:`${id} ${presentation??''}`.trim(),zoom:1,pan:[.5,.5],placementId,...(presentation?{presentation}:{})}) as unknown as WorkspaceView['items'][number];

test('a saved item says what it is by how it is shown: outlines, a table, a graph, a capture, a document',()=>{
 expect(itemKind(item('masks','e3','a'))).toBe('outlines');
 expect(itemKind(item('table','e3','b'))).toBe('table');
 expect(itemKind(item('datatable','d4','c'))).toBe('table');
 expect(itemKind(item('analysis','a2','d'))).toBe('table');
 expect(itemKind(item('graph','g1','e'))).toBe('graph');
 expect(itemKind(item('capture','e3','f',0))).toBe('image');
 expect(itemKind(item(undefined,'e7','g'))).toBe('outlines');
 expect(itemKind(item(undefined,'m2','h'))).toBe('tracks');
 expect(itemKind(item(undefined,'d9','i'))).toBe('document');
 expect(itemKind(item(undefined,`p${'a'.repeat(32)}`,'j'))).toBe('image');
});

test('a native window says what it shows from its source: a magnifier, a reference photo, a video, a web page, a report',()=>{
 expect(nativeKind('live','capture')).toBe('magnifier');
 expect(nativeKind('saved-image','reference')).toBe('reference');
 expect(nativeKind('saved-image','external-reference')).toBe('reference');
 expect(nativeKind('saved-image','capture')).toBe('image');
 expect(nativeKind('video','external-reference')).toBe('video');
 expect(nativeKind('web-page','external-reference')).toBe('web-page');
 expect(nativeKind('written','written')).toBe('document');
 // A saved native window (a reference photo kept with its resource) is said by its window's source, not its id.
 expect(itemKind(item(undefined,'r5','k',0),{kind:'saved-image',media:'reference'})).toBe('reference');
});

test('a Tab holds its saved windows, its notes and the windows open on it that are not saved, each by kind; never another Tab\'s or a closed one',()=>{
 const roots=[
  root({workspaceView:'t1',placementId:'a'}),
  root({workspaceView:'t1',placementId:'w1',presentationId:'w-1',sourceKind:'live',sourceMedia:'capture',windowOpen:'true'},{classes:['workspace-window'],label:'Live magnification'}),
  root({workspaceView:'t1',placementId:'v1',webWindow:JSON.stringify({kind:'video',site:'youtube.com',page:'https://www.youtube.com/watch?v=abc',url:'https://www.youtube-nocookie.com/embed/abc'})},{label:'Stomata opening'}),
  root({workspaceView:'t1',placementId:'gone',webWindow:JSON.stringify({kind:'web-page',site:'en.wikipedia.org',page:'https://en.wikipedia.org/wiki/Stoma'}),dismissed:'true'}),
  root({workspaceView:'t1',placementId:'w2',presentationId:'w-2',sourceKind:'saved-image',sourceMedia:'reference',windowOpen:'false'},{classes:['workspace-window']}),
  root({workspaceView:'main',placementId:'elsewhere',sourceKind:'live',sourceMedia:'capture'},{classes:['workspace-window']}),
 ];
 const v=view([item('masks','e3','a'),item('table','e3','b')],{notes:[{id:'n1',rect:[0,0,1,1],placementId:'n'}]});
 const facts=tabItems(v,roots);
 expect(facts.map(f=>[f.placement,f.kind])).toEqual([['a','outlines'],['b','table'],['n','note'],['w1','magnifier'],['v1','video']]);
 expect(facts[0].resource).toEqual({day:'p',id:'e3',version:1});
 expect(facts[3].window).toBe('w-1');
 expect(facts[4].source).toEqual({site:'youtube.com',url:'https://www.youtube.com/watch?v=abc'});
 expect(holds(facts)).toEqual({outlines:1,table:1,note:1,magnifier:1,video:1});
});

test('a reader Tab holds its one Data item; every kind is one of the fixed list',()=>{
 const reader:WorkspaceView={id:'r',title:'Report',specimen:{visible:false,rect:null},kind:'reader',notes:[],items:[{resource:{day:'p',id:'e4',version:2},title:'Cells'} as unknown as WorkspaceView['items'][number]]};
 expect(tabItems(reader,[])).toEqual([{placement:'reader:tab:r',kind:'outlines',title:'Cells',resource:{day:'p',id:'e4',version:2}}]);
 for(const f of tabItems(view([item('masks','e3','a'),item(undefined,'x1','z',0)]),[]))expect(ITEM_KINDS).toContain(f.kind);
});

test('a researcher\'s Tab shows its conversation and the work it made, by kind; a replaced version is not shown',()=>{
 expect(researchShows([{kind:'image',caption:'Stoma close-up',id:'o1'},{kind:'html',contentKind:'report',caption:'Findings'},{kind:'video',caption:'Old',current:false},{kind:'video',caption:'Opening',id:'o3'}]))
  .toEqual([{kind:'document',title:'Its conversation'},{kind:'image',title:'Stoma close-up',id:'o1'},{kind:'document',title:'Findings'},{kind:'video',title:'Opening',id:'o3'}]);
});

/** The Tab's own `only`: what was kept stays, every other window is closed through its own ×, and the specimen is put
 * away (hidden; Main's floats small, it always keeps it). */
function onTab(id:string,roots:ReturnType<typeof root>[]){
 const v:WorkspaceView={id,title:id==='main'?'Main':'Video',specimen:{visible:true,rect:null},items:[],notes:[]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',layout:{active:id,views:[v]},openIds:[id],
  stage:{clientWidth:1200,clientHeight:800},changing:false,visibleRoots:()=>roots,isHome:(t:string)=>t==='main',
  applySpecimen:vi.fn(),capture:vi.fn(),changed:vi.fn(),place:vi.fn()});
 return {current,v};
}
test('only keeps what was asked and closes the rest through their own ×; the specimen is hidden on a Tab',()=>{
 const video=root({workspaceView:'t',placementId:'v1',webWindow:'{}'}),table=root({workspaceView:'t',placementId:'p1'}),magnifier=root({workspaceView:'t',placementId:'m1',presentationId:'w-9'},{classes:['workspace-window']});
 const {current,v}=onTab('t',[video,table,magnifier]);
 Reflect.get(WorkspaceViews.prototype,'only').call(current,v,['v1']);
 expect(video.close).not.toHaveBeenCalled();expect(table.close).toHaveBeenCalledOnce();expect(magnifier.close).toHaveBeenCalledOnce();
 expect(v.specimen).toEqual({visible:false,rect:null});
 expect(current.place).toHaveBeenCalledWith(video,[12,12,1176,716]);expect(current.changed).toHaveBeenCalled();
});
test('only on Main floats the specimen small in its corner and fits what stays beside it, never over it',()=>{
 const video=root({workspaceView:'main',placementId:'v1',webWindow:'{}'}),table=root({workspaceView:'main',placementId:'p1'});
 const {current,v}=onTab('main',[video,table]);
 Reflect.get(WorkspaceViews.prototype,'only').call(current,v,['document:x','v1']);
 expect(table.close).toHaveBeenCalledOnce();expect(v.specimen.visible).toBe(true);expect(v.specimen.rect?.[2]).toBeLessThanOrEqual(320);
 const left=v.specimen.rect![0]+v.specimen.rect![2]+12;
 expect(current.place).toHaveBeenCalledWith(video,[left,12,1200-left-12,716]);
});
test('only refuses when what was to stay is not on the Tab, closing nothing',()=>{
 const table=root({workspaceView:'t',placementId:'p1'});const {current,v}=onTab('t',[table]);
 expect(()=>Reflect.get(WorkspaceViews.prototype,'only').call(current,v,['v9'])).toThrow(/not open on this Tab/);
 expect(table.close).not.toHaveBeenCalled();
});

test('the Tab strip\'s report says each Tab\'s kind and what it holds, the same model tabs list reads',()=>{
 const roots=[root({workspaceView:'main',placementId:'v1',webWindow:JSON.stringify({kind:'video',site:'youtube.com',page:'https://www.youtube.com/watch?v=abc'})})];
 const views=[{id:'main',title:'Main',specimen:{visible:true,rect:null},items:[item('table','e3','b')],notes:[]},
  view([item('masks','e3','a')],{id:'t1'}),{...view([]),id:'r',kind:'reader' as const,items:[{resource:{day:'p',id:'d2',version:null},title:'Report'} as unknown as WorkspaceView['items'][number]]}];
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{layout:{active:'main',views},roots:()=>roots});
 expect(Reflect.get(WorkspaceViews.prototype,'holdsReport').call(current)).toEqual({
  main:{kind:'work',holds:{table:1,video:1}},t1:{kind:'work',holds:{outlines:1}},r:{kind:'reader',holds:{document:1}}});
});

test('a window closed by its × is no longer held by its Tab, though its arrangement is kept',()=>{
 // will-display, 29 Oct: after "only the video", Main still listed the closed table, and Hooke hedged it might be open.
 const table=root({workspaceView:'main',placementId:'b',dismissed:'true'});
 const video=root({workspaceView:'main',placementId:'v1',webWindow:JSON.stringify({kind:'video',site:'youtube.com',page:'https://www.youtube.com/watch?v=abc'})});
 const v={id:'main',title:'Main',specimen:{visible:true,rect:null},items:[item('table','e3','b')],notes:[]} as WorkspaceView;
 expect(holds(tabItems(v,[table,video]))).toEqual({video:1});
 // A Tab whose windows are not mounted (not opened yet) still lists what it holds.
 expect(holds(tabItems(v,[]))).toEqual({table:1});
});
