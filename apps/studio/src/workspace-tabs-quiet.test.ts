import {afterEach,expect,test,vi} from 'vitest';
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({blockReason:()=>null})}));
import {WorkspaceViews} from './workspace-views';
afterEach(()=>vi.unstubAllGlobals());

// Hooke prepares Tabs quietly ("show": false) and sets one up in one action (compose); the person's screen stays on the
// Tab it is on until Hooke switches to what it prepared, which then opens already arranged.
function fixture(){
 vi.stubGlobal('document',{hidden:false,querySelector:()=>null,body:{dataset:{}}});
 const main={id:'main',title:'Main',specimen:{visible:true,rect:null},items:[] as any[],notes:[]};
 const other={id:'other',title:'Other',specimen:{visible:true,rect:null},items:[] as any[],notes:[]};
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout:{schema:5,active:'main',views:[main,other]},openIds:['main','other'],
  completionRestores:new Set(),loaded:new Set(['main']),capture:vi.fn(),changed:vi.fn(),render:vi.fn(),flush:vi.fn(async()=>{}),roots:()=>[],
  activate:vi.fn(async()=>true),goTo:vi.fn(async()=>true),arrange:vi.fn(),only:vi.fn(),feedAway:vi.fn(),setSpecimen:vi.fn(),
  opts:{notice:vi.fn()},dirty:false,conflict:false});
 current.addView=vi.fn(async(title:string,activate=true,specimen?:unknown)=>{
  const v={id:'t2',title,specimen:specimen??{visible:true,rect:null},items:[] as any[],notes:[]};current.layout.views.push(v);current.openIds.push('t2');
  if(activate)current.layout.active='t2';return 't2';});
 return {current,main,other};
}
const table={resource:{day:'p',id:'tb1',version:2,picture:null},presentation:'datatable',title:'Counts',zoom:1,pan:[.5,.5]};

test('a quiet new Tab is made without coming to the front, and the reply names it',async()=>{
 const {current}=fixture();
 const result=await current.agentCommand('p',{action:'create',title:'Reference',show:false});
 expect(result.ok).toBe(true);expect(current.addView).toHaveBeenCalledWith('Reference',false);
 expect(current.layout.active).toBe('main');expect(result).toMatchObject({made:{id:'t2',title:'Reference'},quiet:true});
 expect(result.tabs.find((t:{id:string})=>t.id==='t2')).toMatchObject({selected:false});
});

test('a shown new Tab still comes to the front',async()=>{
 const {current}=fixture();
 await current.agentCommand('p',{action:'create',title:'Reference'});
 expect(current.addView).toHaveBeenCalledWith('Reference',true);expect(current.layout.active).toBe('t2');
});

test('a quiet arrange on a Tab not in view waits for that Tab, which then opens arranged once',async()=>{
 const {current}=fixture();
 const result=await current.agentCommand('p',{action:'arrange',tab:'other',show:false});
 expect(result).toMatchObject({ok:true,deferred:true});expect(current.goTo).not.toHaveBeenCalled();expect(current.arrange).not.toHaveBeenCalled();
 current.applyPrepared('other');expect(current.arrange).toHaveBeenCalledTimes(1);
 current.applyPrepared('other');expect(current.arrange).toHaveBeenCalledTimes(1);
});

test('a quiet only on a Tab not in view keeps what was named, when that Tab is shown',async()=>{
 const {current,other}=fixture();
 await current.agentCommand('p',{action:'only',tab:'other',keep:['pl1'],show:false});
 expect(current.only).not.toHaveBeenCalled();
 current.applyPrepared('other');expect(current.only).toHaveBeenCalledWith(other,['pl1']);
});

test('a quiet arrange on the Tab in view is simply done',async()=>{
 const {current}=fixture();
 const result=await current.agentCommand('p',{action:'arrange',tab:'main',show:false});
 expect(result.deferred).toBeUndefined();expect(current.arrange).toHaveBeenCalledTimes(1);
});

test('compose, quiet: one new Tab holding the items, not in front, arranged when it is shown',async()=>{
 const {current}=fixture();
 const result=await current.agentCommand('p',{action:'compose',title:'Leaves',show:false,items:[table]});
 expect(result.ok).toBe(true);expect(result.made).toEqual({id:'t2',title:'Leaves'});
 expect(current.addView).toHaveBeenCalledWith('Leaves',false,{visible:false,rect:null});
 const made=current.layout.views.find((v:{id:string})=>v.id==='t2');
 expect(made.items.map((i:{resource:{id:string}})=>i.resource.id)).toEqual(['tb1']);expect(made.items[0].placementId).toBeTruthy();
 expect(current.layout.active).toBe('main');expect(current.goTo).not.toHaveBeenCalled();
 current.applyPrepared('t2');expect(current.arrange).toHaveBeenCalledTimes(1);
});

test('compose, shown: the new Tab comes to the front and opens arranged',async()=>{
 const {current}=fixture();
 current.goTo=vi.fn(async(id:string)=>{current.layout.active=id;current.applyPrepared(id);return true;});
 const result=await current.agentCommand('p',{action:'compose',title:'Leaves',items:[table],specimen:'float'});
 expect(result.ok).toBe(true);expect(current.goTo).toHaveBeenCalledWith('t2',{session:'p',generation:1});
 expect(current.arrange).toHaveBeenCalledTimes(1);expect(current.setSpecimen).toHaveBeenCalled();
});

test('compose refuses another Project\'s item, more than eight, or a specimen word it does not know',async()=>{
 for(const args of [{items:[{...table,resource:{...table.resource,day:'other'}}]},{items:Array(9).fill(table)},{items:[table],specimen:'shrink'}]){
  const {current}=fixture();
  const result=await current.agentCommand('p',{action:'compose',title:'Leaves',...args});
  expect(result.ok).toBe(false);expect(current.addView).not.toHaveBeenCalled();
 }
});

test('reopen, quiet: a closed Tab is back where it stood among the open Tabs, the person\'s screen unmoved',async()=>{
 const {current}=fixture();
 const dispatched=vi.fn();vi.stubGlobal('window',{dispatchEvent:dispatched});vi.stubGlobal('CustomEvent',class{type:string;constructor(type:string){this.type=type;}});
 Object.assign(current,{openIds:['main'],closedStack:[{id:'other',at:1}],navSave:vi.fn(),closedId:''});
 const result=await current.agentCommand('p',{action:'reopen',tab:'other',show:false});
 expect(result.ok).toBe(true);expect(current.openIds).toEqual(['main','other']);expect(current.closedStack).toEqual([]);
 expect(current.layout.active).toBe('main');expect(current.goTo).not.toHaveBeenCalled();expect(current.navSave).toHaveBeenCalled();
 expect(result.tabs.find((t:{id:string})=>t.id==='other')).toMatchObject({open:true,selected:false});
 expect(dispatched).toHaveBeenCalledTimes(1);
});

test('reopen, shown: the closed Tab comes back in front, the same reopen as the person\'s',async()=>{
 const {current}=fixture();
 Object.assign(current,{openIds:['main'],closedStack:[{id:'other',at:1}],navSave:vi.fn()});
 current.reopenClosed=vi.fn(async(id:string)=>{current.openIds.push(id);current.layout.active=id;});
 const result=await current.agentCommand('p',{action:'reopen',tab:'other'});
 expect(current.reopenClosed).toHaveBeenCalledWith('other');expect(result.tabs.find((t:{id:string})=>t.id==='other')).toMatchObject({open:true,selected:true});
});

test('reopen says so when the Tab did not come back',async()=>{
 const {current}=fixture();
 Object.assign(current,{openIds:['main'],closedStack:[{id:'other',at:1}],navSave:vi.fn()});
 current.reopenClosed=vi.fn(async()=>{});
 expect(await current.agentCommand('p',{action:'reopen',tab:'other'})).toMatchObject({ok:false,error:expect.stringMatching(/could not reopen/)});
});
