import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {sessionDOM,SessionNode} from './sessions.test-support';
import {fire} from './workspace-windows.test-support';
import type {ResearchSession} from './protocol';
vi.mock('./viewer',()=>({outputOf:()=>undefined}));
const storage=()=>{const m=new Map<string,string>();return {getItem:(k:string)=>m.get(k)??null,setItem:(k:string,v:string)=>void m.set(k,v),removeItem:(k:string)=>void m.delete(k),clear:()=>m.clear(),key:()=>null,length:0} as Storage;};
let dom:ReturnType<typeof sessionDOM>;
beforeEach(()=>{dom=sessionDOM();vi.stubGlobal('sessionStorage',storage());vi.resetModules();});afterEach(()=>{vi.unstubAllGlobals();});
const row=(rid:string,extra:Partial<ResearchSession>={}):ResearchSession=>({rid,n:Number(rid.slice(1))||1,name:`Researcher ${rid.slice(1)}`,label:`Study ${rid}`,brief:'Fixture research',deliverable:null,effort:'quick',engine:'fixture-engine',purpose:'research',objectId:null,status:'running',step:'Comparing evidence',spent:0.2,cap:1,seconds:1,created:1,started:1,finished:null,snapshot:'/research/p1/x/inputs/frame.jpg',outputs:[],items:[],findings:[],corrections:[],history:[],canFollowUp:true,sitting:'p1',result:null,...extra});
/** The ⋯ menu a row opened: its items, as the person sees them. */
const menuItems=()=>((dom.body.querySelector('.mm-drawer-menu')?.querySelectorAll('button')??[]) as SessionNode[]);
const openMenu=(root:SessionNode,rid:string)=>{const more=root.querySelector(`[data-rid="${rid}"]`)!.querySelector('.mm-row-more')!;fire(more,'click');return menuItems();};

test('a researcher that made no picture shows the input it started from, else its mark on the raised tone: never an empty box',async()=>{
 const {ResearchersBoard}=await import('./researchers-board');
 const board=new ResearchersBoard({open:vi.fn(),cover:s=>s.rid==='r1'?'/api/library/a7/file':''});
 board.update([row('r1',{snapshot:''}),row('r2',{snapshot:''}),row('r3')]);
 const root=board.root as unknown as SessionNode,thumb=(rid:string)=>root.querySelector(`[data-rid="${rid}"]`)!.querySelector('.mm-row-thumb')!;
 expect(thumb('r1').style.backgroundImage).toContain('/api/library/a7/file');expect(thumb('r1').dataset.empty).toBeUndefined();
 expect(thumb('r2').style.backgroundImage||'').toBe('');expect(thumb('r2').dataset.empty).toBe('');expect(thumb('r2').querySelector('.mm-row-thumb-mark')).not.toBe(null);
 // Its own picture first: the cover is only for one that made none.
 expect(thumb('r3').style.backgroundImage).toContain('frame.jpg');
 expect(thumb('r2').querySelector('[data-kind="lit-cell"]')).not.toBe(null);
});

test('every researcher is a row at any count, grouped Needs you · Working · Done, with its name and what it is doing on the second line',async()=>{
 const {ResearchersBoard,boardGroup,nowWords,rowWords}=await import('./researchers-board');
 const open=vi.fn(),send=vi.fn((_command:{type:string})=>Promise.resolve({ok:true}));const board=new ResearchersBoard({open,send});
 board.update([row('r1')]);expect(board.root.hidden).toBe(false);
 const root=board.root as unknown as SessionNode;
 expect(root.querySelectorAll('.researchers-board-row').length).toBe(1);
 const rows=[row('r1',{requests:[{id:'q',what:'frames',why:'motion',at:2,status:'asked',seconds:5}]}),row('r2'),row('r3',{status:'queued',step:''}),row('r4',{status:'done',finished:3,outputs:[{kind:'html'} as never]}),row('r5',{status:'failed',finished:3})];
 board.update(rows);
 expect(rows.map(boardGroup)).toEqual(['needs','working','queued','done','done']);
 // Waiting for a free slot is working too (its words say so); no separate "In line" section.
 expect(root.querySelectorAll('.researchers-board-group').map(g=>g.textContent)).toEqual(['Needs you 1','Working 2','Done 2']);
 expect(nowWords(rows[1])).toBe('Comparing evidence');expect(nowWords(rows[2])).toBe('waiting for a free slot');
 const sub=(rid:string)=>root.querySelector(`[data-rid="${rid}"]`)!.querySelector('.mm-row-sub')!.textContent;
 expect(sub('r2')).toMatch(/^Researcher 2 · Working \d+:\d\d/);
 expect(sub('r1')).toBe('Researcher 1 · Needs you');expect(sub('r3')).toBe('Researcher 3 · Waiting for a free slot');
 expect(sub('r4')).toBe('Researcher 4 · Lesson ready');expect(sub('r5')).toBe('Researcher 5 · Could not finish');
 expect(rowWords(rows[1],1+3674)).toBe('Working 1:01:14');
 // Its title (up to two lines), its cover picture with its lit cell on the corner, engine and step age in its tooltip.
 const r2=root.querySelector('[data-rid="r2"]')!;
 expect(r2.querySelector('.mm-row-title')!.textContent).toBe('Study r2');
 expect(r2.querySelector('.mm-row-thumb')!.style.backgroundImage).toContain('frame.jpg');
 expect(r2.querySelector('.mm-row-thumb')!.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('working');
 expect(r2.querySelector('.researchers-board-open')!.title).toContain('fixture-engine');
 expect(root.querySelector('.shelf-ring')).toBe(null); // the rings are gone
 fire(root.querySelector('.researchers-board-open')!,'click');expect(open).toHaveBeenCalledWith('r1');
});

test('a researcher waiting on you carries its question and answer buttons in its row',async()=>{
 const {ResearchersBoard,questionWords}=await import('./researchers-board');
 const send=vi.fn((_command:{type:string})=>Promise.resolve({ok:true}));const board=new ResearchersBoard({open:vi.fn(),send});
 const asking=row('r4',{requests:[{id:'q1',what:'',why:'it has worked for 60 minutes',at:2,status:'asked',keepGoing:true}]});
 board.update([asking]);
 const root=board.root as unknown as SessionNode,question=root.querySelector('[data-rid="r4"]')!.querySelector('.mm-row-question')!;
 expect(questionWords(asking)).toBe('Keep going? It has worked for 60 minutes.');
 expect(question.textContent).toContain('Keep going? It has worked for 60 minutes.');
 expect(question.querySelectorAll('button').map(b=>b.textContent)).toEqual(['Keep going','Not now']);
});

test('⋯ holds Stop, Activity and Open; Stop reads Stop requested until the server lists the researcher as stopped',async()=>{
 const {ResearchersBoard}=await import('./researchers-board');
 const send=vi.fn((_command:{type:string})=>Promise.resolve({ok:true})),activity=vi.fn(),open=vi.fn();
 const board=new ResearchersBoard({open,send,activity});
 const rows=[row('r1'),row('r2',{status:'done',finished:3})];board.update(rows);
 const root=board.root as unknown as SessionNode;
 expect(openMenu(root,'r1').map(b=>b.textContent)).toEqual(['Stop','Activity','Open']);
 fire(menuItems()[0]!,'click');expect(send).toHaveBeenCalledWith({type:'research-cancel',rid:'r1'});
 const stopItem=openMenu(root,'r1')[0]!;expect(stopItem.textContent).toBe('Stop requested');expect((stopItem as unknown as HTMLButtonElement).disabled).toBe(true);
 expect(root.querySelector('[data-rid="r1"]')!.querySelector('.mm-row-sub')!.textContent).toBe('Researcher 1 · Stop requested');
 fire(dom.body.querySelector('.mm-drawer-menu')!,'keydown',{key:'Escape'});
 board.update(rows.map(r=>r.rid==='r1'?{...r,status:'interrupted',finished:4}:r));
 expect(openMenu(root,'r1').map(b=>b.textContent)).toEqual(['Activity','Open']); // stopped: nothing to stop
 fire(menuItems()[0]!,'click');expect(activity).toHaveBeenCalledWith(expect.objectContaining({rid:'r1'}));
 expect(openMenu(root,'r2').map(b=>b.textContent)).toEqual(['Activity','Open']);fire(menuItems()[1]!,'click');expect(open).toHaveBeenCalledWith('r2');
});

test('from eight researchers the list gains its search and ticks for Stop selected',async()=>{
 const {ResearchersBoard,SELECT_FROM}=await import('./researchers-board');
 const send=vi.fn((_command:{type:string})=>Promise.resolve({ok:true}));const board=new ResearchersBoard({open:vi.fn(),send});
 const root=board.root as unknown as SessionNode;
 board.update([row('r1'),row('r2')]);
 expect((root.querySelector('.researchers-board-search') as unknown as HTMLInputElement).hidden).toBe(true);expect(root.querySelectorAll('.researchers-board-pick').length).toBe(0);
 const many=Array.from({length:SELECT_FROM},(_,i)=>row(`r${i+1}`,i===SELECT_FROM-1?{status:'done',finished:3}:{}));board.update(many);
 expect((root.querySelector('.researchers-board-search') as unknown as HTMLInputElement).hidden).toBe(false);
 const boxes=root.querySelectorAll('.researchers-board-pick');expect(boxes.length).toBe(SELECT_FROM-1); // working ones only
 for(const box of boxes.slice(0,2)){(box as unknown as HTMLInputElement).checked=true;fire(box,'change');}
 const selected=root.querySelector('.researchers-board-stop-selected')!;expect(selected.textContent).toBe('Stop selected (2)');expect(selected.hidden).toBe(false);
 fire(selected,'click');expect(send.mock.calls.filter(c=>c[0].type==='research-cancel').length).toBe(2);
});

test('then its Studies and the researchers still working in other Projects; with nothing at all, one sentence and one action',async()=>{
 const {ResearchersBoard}=await import('./researchers-board');
 const start=vi.fn(),openStudy=vi.fn();const board=new ResearchersBoard({open:vi.fn(),start,project:id=>id==='p2'?'Leaf epidermis':''});
 const root=board.root as unknown as SessionNode;
 board.update([],{studies:[]});
 expect(board.empty).toBe(true);expect(root.querySelector('.researchers-board-empty')!.textContent).toContain('No researchers yet. Ask the Assistant, or start one.');
 const go=root.querySelector('.researchers-board-empty')!.querySelector('button')!;
 expect(go.textContent).toBe('New researcher');expect(go.querySelector('.mm-dhead-icon')).not.toBe(null); // "+ New researcher", as the mock draws it
 fire(go,'click');expect(start).toHaveBeenCalled();
 board.update([row('r1')],{elsewhere:[row('r9',{sitting:'p2',label:'Stomata density'})],studies:[{id:'s1',title:'Nuclear size across three fields',sub:'1 running · $0.32 spent of $1.00',cell:'working',cellLabel:'Working',open:openStudy}]});
 expect(root.querySelector('.researchers-board-empty')).toBe(null);
 expect(root.querySelectorAll('.researchers-board-group').map(g=>g.textContent)).toEqual(['Working 1','Studies 1','Still working in other Projects']);
 const study=root.querySelector('.researchers-board-study')!;expect(study.textContent).toContain('Nuclear size across three fields');fire(study,'click');expect(openStudy).toHaveBeenCalled();
 expect(root.querySelector('[data-rid="r9"]')!.querySelector('.mm-row-sub')!.textContent).toMatch(/^Leaf epidermis · Working/);
});

test('a researcher that made something says what under its row, leading to its work in Data; nothing when it made nothing, nor for one elsewhere',async()=>{
 const {ResearchersBoard}=await import('./researchers-board');
 const show=vi.fn();let words:Record<string,string>={r3:'Made a presentation, a table and outlines'};
 const made=vi.fn((s:ResearchSession)=>words[s.rid]?{words:words[s.rid],show:()=>show(s.rid)}:null);
 const board=new ResearchersBoard({open:vi.fn(),made});
 const root=board.root as unknown as SessionNode;
 board.update([row('r3',{status:'done',finished:3}),row('r4',{status:'done',finished:3})],{elsewhere:[row('r9',{sitting:'p2'})]});
 const line=root.querySelector('[data-rid="r3"]')!.querySelector('.researchers-board-made')!;
 expect(line.textContent).toBe('Made a presentation, a table and outlines');
 expect(line.title).toBe('Show what Researcher 3 made, in Data');
 fire(line,'click');expect(show).toHaveBeenCalledWith('r3');
 // Nothing made yet: its row says nothing of it. One still working in another Project files its work there.
 expect(root.querySelector('[data-rid="r4"]')!.querySelector('.researchers-board-made')).toBe(null);
 expect(made.mock.calls.some(([s])=>s.rid==='r9')).toBe(false);
 // When what it made changes, its row says so (the list redraws only on a change).
 words={r3:'Made a presentation, a table and outlines',r4:'Made a report and 3 sources'};board.update([row('r3',{status:'done',finished:3}),row('r4',{status:'done',finished:3})]);
 expect(root.querySelector('[data-rid="r4"]')!.querySelector('.researchers-board-made')!.textContent).toBe('Made a report and 3 sources');
});
