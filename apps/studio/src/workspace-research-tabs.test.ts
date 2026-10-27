import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {WorkspaceResearchTabs,researcherCell} from './workspace-research-tabs';
import {requestStop} from './research-stop';
import {Shelf} from './shelf';
import {sessionDOM,SessionNode} from './sessions.test-support';
import {fire,settleMicrotasks} from './workspace-windows.test-support';
import type {ResearchSession} from './protocol';
vi.mock('./viewer',()=>({outputOf:()=>undefined}));
beforeEach(()=>{sessionDOM();const matches=SessionNode.prototype.matches;vi.spyOn(SessionNode.prototype,'matches').mockImplementation(function(this:SessionNode,selector:string){const role=/^\[role="?([a-z]+)"?\]$/.exec(selector);return role?this.getAttribute('role')===role[1]:matches.call(this,selector);});vi.stubGlobal('window',new EventTarget());});afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
const row=(rid:string,extra:Partial<ResearchSession>={}):ResearchSession=>({rid,n:1,name:rid,label:rid,brief:'Fixture research',deliverable:null,effort:'quick',engine:'fixture',purpose:'research',objectId:null,status:'running',step:'Comparing evidence',spent:0,cap:0,seconds:1,created:1,started:1,finished:null,snapshot:'',outputs:[],items:[],findings:[],corrections:[],history:[],canFollowUp:true,sitting:'p1',result:null,...extra});
function setup(){
 const actions={open:vi.fn(),close:vi.fn(),closed:vi.fn()},host={current:()=>true,row:document.createElement('div'),project:'p1',activeWorkId:'main',homeWorkId:'main',activateWork:vi.fn(async()=>true),reorderWork:vi.fn(),refit:vi.fn()};
 host.row.setAttribute('role','tablist');
 for(const id of ['main','measurements']){const wrap=document.createElement('div'),tab=document.createElement('button');tab.dataset.workspaceViewTab=id;tab.setAttribute('role','tab');tab.textContent=id;wrap.append(tab);host.row.append(wrap);}
 document.body.append(host.row);const isNew=vi.fn((_row:ResearchSession)=>false),view=new WorkspaceResearchTabs({storage:localStorage,session:localStorage,key:'test',host:()=>host,isNew,...actions});
 return {view,host,isNew,root:host.row as unknown as SessionNode,...actions};
}
const tabs=(root:SessionNode)=>root.querySelectorAll('button').filter(b=>b.getAttribute('role')==='tab');
const ids=(root:SessionNode)=>tabs(root).map(tab=>tab.dataset.mixedTab??`work:${tab.dataset.workspaceViewTab}`);
const find=(root:SessionNode,rid:string)=>root.querySelector(`[data-research-focus="${rid}"]`) as SessionNode;
test('one row contains work and this Project research; a new researcher arrives in the background, its cell lit once',()=>{
 const {view,root,open}=setup();view.update('p1',[row('r1'),row('other',{sitting:'p2'}),row('quick',{purpose:'description'})],'');
 expect(ids(root)).toEqual(['work:main','work:measurements','research:r1']);expect(root.querySelectorAll('[role=tablist]')).toHaveLength(0);
 const cellOf=(rid:string)=>find(root,rid).querySelector('[data-kind="lit-cell"]')!;
 expect(cellOf('r1').classList.contains('lit-cell-settle')).toBe(false); // what was there when the Project opened does not flash
 view.update('p1',[row('r1'),row('r2',{created:2})],'');expect(ids(root)).toContain('research:r2');expect(open).not.toHaveBeenCalled();
 expect(cellOf('r2').classList.contains('lit-cell-settle')).toBe(true);expect(cellOf('r2').getAttribute('data-state')).toBe('working');
 // The row holds Tabs only: no "Go to researcher" or "Reopen researcher tabs" beside them.
 expect(root.querySelectorAll('button').filter(b=>/Go to researcher|Reopen researcher/.test(b.textContent))).toHaveLength(0);
 // Its name is the Tab's label; what it works on and its state are the tooltip.
 expect(find(root,'r2').textContent).toBe('r2');expect(find(root,'r2').title).toContain('Comparing evidence');
 fire(find(root,'r2'),'click');expect(open).toHaveBeenCalledWith(expect.objectContaining({rid:'r2',sitting:'p1'}));
});
test('close hides without stopping and says where it went; a relaunch remembers it; the rail reopens it',()=>{
 const {view,root,close,closed,open}=setup();view.update('p1',[row('r1')],'r1');fire(find(root,'close:r1'),'click');expect(close).toHaveBeenCalledOnce();expect(ids(root)).toEqual(['work:main','work:measurements']);
 expect(closed).toHaveBeenCalledWith(expect.objectContaining({id:'research:r1',name:'r1',text:'Closed r1. It keeps working in Researchers.'}));
 closed.mock.calls[0][0].reopen();expect(open).toHaveBeenCalledWith(expect.objectContaining({rid:'r1'})); // the toast's Reopen opens it
 view.update('p1',[row('r1')],'r1');expect(ids(root)).toContain('research:r1');
 fire(find(root,'close:r1'),'click');
 // Closed is kept per Project in localStorage, so a working researcher's Tab does not come back on relaunch.
 const next=setup();next.view.update('p1',[row('r1')],'');expect(ids(next.root)).not.toContain('research:r1');
 next.view.update('p1',[row('r1')],'r1');expect(ids(next.root)).toContain('research:r1');expect(find(next.root,'r1').getAttribute('aria-selected')).toBe('true');
});
test('a researcher has a Tab while it works or needs you, finished and not yet opened, or opened this session',()=>{
 const {view,root,isNew}=setup();
 const asking=row('ask',{created:2,requests:[{id:'q1',status:'asked',what:'a clearer picture',why:'',keepGoing:false}] as never});
 view.update('p1',[row('work'),asking,row('old',{created:3,status:'done',finished:4,result:{title:'Old findings'} as never})],'');
 expect(ids(root)).toEqual(['work:main','work:measurements','research:work','research:ask']); // a finished, opened one is in Researchers
 expect(find(root,'ask').querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('needs');
 isNew.mockImplementation(r=>r.rid==='old');view.update('p1',[row('work'),asking,row('old',{created:3,status:'done',finished:4,outputs:[{kind:'html'}] as never})],'');
 expect(ids(root)).toContain('research:old');
 isNew.mockImplementation(()=>false);view.update('p1',[row('work'),asking,row('old',{created:3,status:'done',finished:4})],'old'); // opened
 view.update('p1',[row('work'),asking,row('old',{created:3,status:'done',finished:4})],'');expect(ids(root)).toContain('research:old'); // kept this session
});
test('a Tab built before the work row loaded still opens and closes: its host is looked up when it is used',()=>{
 const {host,open,close,closed}=setup();const openVirtual=vi.fn();
 // Each host token goes stale when the work row loads again (workspace-views.ts load() bumps its generation).
 let token=0;const view=new WorkspaceResearchTabs({storage:localStorage,session:localStorage,key:'stale',host:()=>{const n=token;return {...host,current:()=>token===n};},open,close,closed,openVirtual});
 const root=host.row as unknown as SessionNode;
 view.update('p1',[row('r1')],'');token=1;view.update('p1',[row('r1')],'');
 fire(find(root,'r1'),'click');expect(open).toHaveBeenCalledWith(expect.objectContaining({rid:'r1'}));
 fire(find(root,'close:r1'),'click');expect(closed).toHaveBeenCalledWith(expect.objectContaining({id:'research:r1'}));
 // A Study's Tab drawn under an older token too.
 view.setVirtual({id:'study:s1',name:'Study 1',line:''},false);token=2;view.update('p1',[row('r1')],'');
 fire(find(root,'study:s1'),'click');expect(openVirtual).toHaveBeenCalledWith('study:s1');
 fire(find(root,'close-study:s1'),'click');expect(closed).toHaveBeenCalledWith(expect.objectContaining({id:'study:s1'}));
});
test('a researcher that ends without findings keeps its Tab beside its pill until it is opened or closed (the strip\'s rule)',()=>{
 const {host,open,close,closed}=setup();const shelf=new Shelf(()=>{},()=>{});
 const view=new WorkspaceResearchTabs({storage:localStorage,session:localStorage,key:'news',host:()=>host,open,close,closed,isNew:r=>shelf.isNew(r),isNews:r=>shelf.isNews(r)});
 const root=host.row as unknown as SessionNode;
 const step=(...rows:ResearchSession[])=>{shelf.update(rows,null,'p1');view.update('p1',rows,'');};
 step(row('r1'));expect(ids(root)).toContain('research:r1');
 const failed=row('r1',{status:'failed',finished:5,step:'OpenAI says this account has no credits left.'});
 step(failed);expect(ids(root)).toContain('research:r1'); // its pill says Could not finish; its Tab stays beside it
 expect(shelf.chips().map(s=>s.rid)).toEqual(['r1']);
 // Opened: it keeps its Tab for this session; closed: it goes to Researchers.
 shelf.update([failed],null,'p1');view.update('p1',[failed],'r1');view.update('p1',[failed],'');expect(ids(root)).toContain('research:r1');
 fire(find(root,'close:r1'),'click');expect(ids(root)).not.toContain('research:r1');
 // One restored after a restart that did not work here is not news: no Tab.
 const restored=row('r2',{status:'failed',finished:3,restored:true});
 step(restored);expect(ids(root)).not.toContain('research:r2');
});
test('mixed keyboard focus/order persists across rerender and reload',()=>{
 const {view,root,host,open}=setup();view.update('p1',[row('r1')],'');
 fire(root,'keydown',{key:'ArrowLeft',altKey:false,target:find(root,'r1')});expect(SessionNode.active).toBe(tabs(root)[1]);
 fire(root,'keydown',{key:'ArrowLeft',altKey:true,target:find(root,'r1')});expect(ids(root)).toEqual(['work:main','research:r1','work:measurements']);expect(host.reorderWork).toHaveBeenCalledWith(['main','measurements']);expect(open).not.toHaveBeenCalled();
 window.dispatchEvent(new Event('workspace-tabs-changed'));expect(ids(root)).toEqual(['work:main','research:r1','work:measurements']);
 const next=setup();next.view.update('p1',[row('r1')],'');expect(ids(next.root)).toEqual(['work:main','research:r1','work:measurements']);
 // Main is always first: Alt+arrow stops at index 1, and Main itself does not move.
 fire(next.root,'keydown',{key:'ArrowLeft',altKey:true,target:find(next.root,'r1')});expect(ids(next.root)).toEqual(['work:main','research:r1','work:measurements']);
 const main=tabs(next.root)[0];fire(next.root,'keydown',{key:'ArrowRight',altKey:true,target:main});expect(ids(next.root)).toEqual(['work:main','research:r1','work:measurements']);
});
test('arrows skip a Tab folded into ⌄, so focus moves on and the row keeps one Tab in the Tab order',()=>{
 const {view,root}=setup();view.update('p1',[row('r1')],'');
 // tab-row-fit.ts folded Measurements (hidden): it cannot take focus.
 const folded=tabs(root)[1].parentElement!;folded.classList.add('workspace-view-tab');folded.hidden=true;
 const main=tabs(root)[0] as SessionNode;main.focus();
 fire(root,'keydown',{key:'ArrowRight',altKey:false,target:main});expect(SessionNode.active).toBe(find(root,'r1'));
 fire(root,'keydown',{key:'ArrowRight',altKey:false,target:find(root,'r1')});expect(SessionNode.active).toBe(main); // round to Main, past the folded one
 fire(root,'keydown',{key:'End',altKey:false,target:main});expect(SessionNode.active).toBe(find(root,'r1'));
 const inOrder=tabs(root).filter(tab=>(tab as unknown as {tabIndex:number}).tabIndex===0);expect(inOrder).toEqual([find(root,'r1')]);expect(inOrder[0].parentElement!.hidden).toBe(false);
 // Alt+arrow moves among the Tabs in sight too: Researcher 1 stays next to Main, the folded Tab keeps its place.
 fire(root,'keydown',{key:'ArrowLeft',altKey:true,target:find(root,'r1')});expect(ids(root)).toEqual(['work:main','work:measurements','research:r1']);
});
test('a saved order that put Main later is normalised; a row still opening orders and saves nothing',()=>{
 localStorage.setItem('test:order:p1',JSON.stringify(['research:r1','work:measurements','work:main']));
 const {view,root}=setup();view.update('p1',[row('r1')],'');expect(ids(root)[0]).toBe('work:main');
 expect(JSON.parse(localStorage.getItem('test:order:p1')!)[0]).toBe('work:main');
 localStorage.removeItem('test:order:p1');
 const opening=setup();opening.host.row.replaceChildren();opening.view.update('p1',[row('r2')],'');
 expect(localStorage.getItem('test:order:p1')).toBeNull(); // researchers were not saved ahead of Main while the row said "Opening…"
});
test('native Tab commands traverse mixed order and reopen without a work command',async()=>{
 const {view,host,open,close}=setup();view.update('p1',[row('r1')],'r1');expect(view.command('next')).toBe(true);expect(host.activateWork).toHaveBeenCalledWith('main');await settleMicrotasks();expect(close).toHaveBeenCalledOnce();
 expect(view.command('go:9')).toBe(true);expect(open).toHaveBeenCalledWith(expect.objectContaining({rid:'r1'}));
 view.update('p1',[row('r1')],'r1');expect(view.command('close')).toBe(true);expect(view.command('reopen')).toBe(true);expect(open).toHaveBeenCalledTimes(2);
});
test('work-only row remains usable; stale Project actions cannot open research',()=>{
 const {view,host,root,open}=setup();view.update('p1',[],'');expect(ids(root)).toEqual(['work:main','work:measurements']);
 view.update('p1',[row('r1')],'');const stale=find(root,'r1');host.project='p2';fire(stale,'click');expect(open).not.toHaveBeenCalled();expect(view.command('go:9')).toBe(false);
 view.update('p2',[row('r2',{sitting:'p2'})],'');expect(ids(root)).not.toContain('research:r1');expect(ids(root)).toContain('research:r2');
});

test('same Project reopening retires old controls and late work navigation cannot close its researcher',async()=>{
 const rig=setup();let generation=1;rig.host.current=()=>generation===1;
 rig.view.update('p1',[row('r1')],'r1');const old=find(rig.root,'r1');
 let finish!:(ok:boolean)=>void;rig.host.activateWork.mockImplementation(()=>new Promise(resolve=>{finish=resolve;}));
 rig.view.command('next');generation=2;finish(true);await settleMicrotasks();expect(rig.close).not.toHaveBeenCalled();
 fire(old,'click');expect(rig.open).not.toHaveBeenCalled();
});

test('Reopen follows the latest closure across both work and virtual researcher Tabs',()=>{
 const {view,root,open}=setup();view.update('p1',[row('r1')],'r1');view.command('close');
 const work=root.querySelector('[data-workspace-view-tab="measurements"]')!;work.parentElement!.remove();
 window.dispatchEvent(new CustomEvent('workspace-tab-closed',{detail:{session:'p1',kind:'work',id:'measurements'}}));
 expect(view.command('reopen')).toBe(false);expect(open).not.toHaveBeenCalled();
 // A refused work activation has not emitted reopened: it remains newest.
 expect(view.command('reopen')).toBe(false);expect(open).not.toHaveBeenCalled();
 window.dispatchEvent(new CustomEvent('workspace-tab-reopened',{detail:{session:'p1',kind:'work',id:'measurements'}}));
 expect(view.command('reopen')).toBe(true);expect(open).toHaveBeenCalledWith(expect.objectContaining({rid:'r1'}));
});
test('an open Study is the selected Tab; another Tab keeps it in the row, a click brings it back, only its × removes it',()=>{
 const {view,root,close}=setup();const openVirtual=vi.fn();(view as unknown as {options:{openVirtual:(id:string)=>void}}).options.openVirtual=openVirtual;
 view.update('p1',[],'');view.setVirtual({id:'study:s1',name:'SAM vs Cellpose',line:'Active · No experiments yet'});
 expect(ids(root)).toEqual(['work:main','work:measurements','study:s1']);
 const selected=()=>tabs(root).filter(tab=>tab.getAttribute('aria-selected')==='true').map(tab=>tab.dataset.mixedTab??`work:${tab.dataset.workspaceViewTab}`);
 expect(selected()).toEqual(['study:s1']); // never two selected, and never Main under a Study
 view.virtualLeft();expect(selected()).toEqual(['work:main']);expect(ids(root)).toContain('study:s1');
 fire(find(root,'study:s1'),'click');expect(openVirtual).toHaveBeenCalledWith('study:s1');
 view.setVirtual({id:'study:s1',name:'SAM vs Cellpose',line:'Active · No experiments yet'});
 fire(find(root,'close-study:s1'),'click');expect(close).toHaveBeenCalledOnce();expect(ids(root)).not.toContain('study:s1');
 view.setVirtual({id:'study:s1',name:'SAM vs Cellpose',line:'Active'});view.update('p2',[],'');expect(ids(root)).not.toContain('study:s1'); // another Project
});
test('a starting New researcher draft and an open Study coexist; the draft’s × is held while its start is in flight',()=>{
 const {view,root}=setup();view.update('p1',[row('r1')],'');
 view.setVirtual({id:'draft:new-researcher',name:'New researcher',line:'Sending…',kind:'',closable:false,closeTitle:'Starting; closes when it settles.'});
 view.setVirtual({id:'study:s1',name:'Stomata',line:'Active'});
 expect(ids(root)).toEqual(['work:main','work:measurements','research:r1','draft:new-researcher','study:s1']);
 const draftClose=find(root,'close-draft:new-researcher');expect(draftClose.disabled).toBe(true);fire(draftClose,'click');
 expect(ids(root)).toContain('draft:new-researcher'); // held
 const selected=tabs(root).filter(tab=>tab.getAttribute('aria-selected')==='true').map(tab=>tab.dataset.mixedTab);expect(selected).toEqual(['study:s1']);
});
test('a closed Study is listed as closed and comes back by Reopen, in turn with other closures; a draft adopted by a researcher keeps its place',()=>{
 const {view,root,open}=setup();const openVirtual=vi.fn();(view as unknown as {options:{openVirtual:(id:string)=>void}}).options.openVirtual=openVirtual;
 view.update('p1',[row('r1')],'');view.setVirtual({id:'study:s1',name:'Stomata',line:'Active'});
 fire(find(root,'close-study:s1'),'click');expect(view.closedVirtual().map(tab=>tab.id)).toEqual(['study:s1']);
 expect(view.command('reopen')).toBe(true);expect(openVirtual).toHaveBeenCalledWith('study:s1');expect(open).not.toHaveBeenCalled();
 view.setVirtual({id:'draft:new-researcher',name:'New researcher',line:'Accepted · waiting for the researcher',kind:'',closable:false});
 const before=ids(root);view.adopt('draft:new-researcher','r2');view.update('p1',[row('r1'),row('r2',{created:2})],'r2');
 expect(ids(root)).toEqual(before.map(id=>id==='draft:new-researcher'?'research:r2':id)); // the researcher stands where the draft stood
 expect(find(root,'r2').getAttribute('aria-selected')).toBe('true');
});
test('many researchers each keep their own Tab: nothing folds into a "Researchers" Tab (the row fits by ⌄ instead)',()=>{
 const {view,root,host}=setup();const rows=['r1','r2','r3','r4','r5'].map((rid,i)=>row(rid,{created:i+1}));
 view.update('p1',rows,'');expect(ids(root)).toEqual(['work:main','work:measurements','research:r1','research:r2','research:r3','research:r4','research:r5']);
 expect(ids(root).some(id=>id.startsWith('fold:'))).toBe(false);expect(host.refit).toHaveBeenCalled();
 view.update('p1',rows,'r3');expect(find(root,'r3').getAttribute('aria-selected')).toBe('true');
});
test('All Tabs and the Assistant hear of closed researcher Tabs, and the cell says Stop requested until it is stopped',()=>{
 const {view,root}=setup();view.update('p1',[row('r1'),row('r2',{created:2})],'');fire(find(root,'close:r1'),'click');
 const closed=view.closedTabs();expect(closed.map(tab=>tab.id)).toEqual(['research:r1']);
 expect(view.report()).toEqual(expect.arrayContaining([expect.objectContaining({id:'research:r1',open:false,selected:false,kind:'researcher'}),expect.objectContaining({id:'research:r2',open:true})]));
 expect(view.has('r1')).toBe(false);expect(view.has('r2')).toBe(true);
 expect(view.closeTab('research:r2')).toBe(true);expect(view.has('r2')).toBe(false);expect(view.show('research:r2')).toBe(true);
 requestStop('r2',()=>undefined);expect(researcherCell(row('r2'))).toBe('still');expect(researcherCell(row('r2',{status:'done'}))).not.toBe('still');
});
test('a pill\'s build is named by data-kind, so it updates in place whatever mark it draws',()=>{
 const {view,root}=setup();view.update('p1',[row('r1')],'');
 const pill=find(root,'r1').parentElement!;const tab=find(root,'r1');
 expect(pill.dataset.kind).toBe('researcher-tab');
 view.update('p1',[row('r1',{step:'Writing the report'})],'');
 expect(find(root,'r1')).toBe(tab);expect(tab.title).toContain('Writing the report'); // the same node, its words current
 view.setVirtual({id:'study:s1',name:'Stomata',line:'Active'});
 const study=find(root,'study:s1');expect(study.parentElement!.dataset.kind).toBe('view-tab');
 // A later step draws a lit cell (or any mark) in a view Tab: the Tab is still updated in place, not rebuilt.
 const mark=document.createElement('span') as unknown as SessionNode;mark.className='shelf-ring';study.parentElement!.append(mark);
 view.setVirtual({id:'study:s1',name:'Stomata',line:'2 working'});
 expect(find(root,'study:s1')).toBe(study);expect(study.title).toContain('2 working');
 // A draft handed to a researcher is rebuilt once into a researcher's pill, in the same place.
 view.setVirtual({id:'draft:new-researcher',name:'New researcher',line:'Sending…',kind:'',closable:false});
 const draft=find(root,'draft:new-researcher').parentElement!;expect(draft.dataset.kind).toBe('view-tab');
 view.adopt('draft:new-researcher','r2');view.update('p1',[row('r1'),row('r2',{created:2})],'r2');
 expect(find(root,'r2').parentElement).toBe(draft);expect(draft.dataset.kind).toBe('researcher-tab');expect(draft.querySelector('[data-kind="lit-cell"]')).not.toBeNull();
});
