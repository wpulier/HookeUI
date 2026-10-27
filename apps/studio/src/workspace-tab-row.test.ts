// The Tab row and Stage bar of the workspace-feel plan (step 2): how the row fits keeping the person's own Tabs, one Tab
// anatomy with Main first, the close motion, the Assistant's Tabs, and what the row says about the layout.
import {afterEach,expect,test,vi} from 'vitest';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),root:{id:'',setAttribute:vi.fn(),children:[]}}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {WorkspaceViews,shortLabel} from './workspace-views';
import {fitTabRow} from './tab-row-fit';
import {flyTo} from './tab-close-motion';
import {TestElement} from './workspace-windows.test-support';
import type {LitCellState} from './lit-cell';
afterEach(()=>{vi.unstubAllGlobals();vi.restoreAllMocks();});

/** A Tab for the fit: its kind, its natural width with its × in place, whether it is selected, home, its lit cell's state
 *  (working, needs you, waiting for a free slot, ready, stopped…), and whether it has a thumbnail. As workspace-feel.css has it: an unselected Tab's × (24 px; a
 *  researcher's too) never takes room, the selected Tab keeps it, and Main has none; work Tabs shrink (`flex: 0 1 auto`,
 *  never below 96 px, a narrower one grown to it); researcher, Study and draft Tabs and Main keep their size. Around the
 *  name are 41 px of padding, mark and gap; its label is cut when the Tab leaves it less than its words. */
type FakeTab={kind:string;width:number;selected?:boolean;home?:boolean;cell?:LitCellState;name:string;thumb?:boolean};
const KEEPS=new Set(['researcher','study','draft']);
const X=24,CHROME=41,FLOOR=96;
function row(specs:FakeTab[],room:number){
 const strip={dataset:{} as Record<string,string>};
 const tabs:any={style:{} as Record<string,string>,scrollLeft:0,clientWidth:room,offsetLeft:8,children:[] as any[]};
 const tight=()=>strip.dataset.fit==='tight';
 const minOf=(t:any)=>t.spec.home||t.spec.kind==='researcher'||t.spec.kind==='draft'?0:t.spec.kind==='study'?120:FLOOR;
 const shrinks=(t:any)=>!t.hidden&&!t.dataset.icon&&!t.spec.home&&!KEEPS.has(t.spec.kind);
 const ownX=(t:any)=>t.spec.selected&&!t.spec.home?X:0; // only the selected Tab's × takes room
 const short=(t:any)=>tight()&&t.dataset.short?40:0;
 const natural=(t:any)=>t.hidden?0:t.dataset.icon?36:Math.max(minOf(t),t.spec.width-(t.spec.home?0:X)+ownX(t)-short(t));
 const widths=()=>{
  const all:any[]=tabs.children,ws=all.map(natural);
  let over=ws.reduce((a:number,b:number)=>a+b,0)-room;
  while(over>0.5){
   const can=all.map((_:any,i:number)=>i).filter((i:number)=>shrinks(all[i])&&ws[i]>FLOOR);if(!can.length)break;
   const total=can.reduce((a:number,i:number)=>a+ws[i],0);for(const i of can)ws[i]=Math.max(FLOOR,ws[i]-over*ws[i]/total);
   over=ws.reduce((a:number,b:number)=>a+b,0)-room;
  }
  return ws;
 };
 const width=(t:any)=>widths()[tabs.children.indexOf(t)];
 // The label's words and the room the Tab leaves them (none while it shows its short label or only its thumbnail).
 const label=(tab:any)=>{const gone=()=>tab.hidden||tab.dataset.icon||short(tab)>0;return {textContent:tab.spec.name,
  get scrollWidth(){return gone()?0:tab.spec.width-CHROME-(tab.spec.home?0:X);},get clientWidth(){return gone()?0:width(tab)-CHROME-ownX(tab);}};};
 for(const spec of specs){
  const tab:any={spec,hidden:false,dataset:{tabKind:spec.kind,...(spec.home?{home:'true'}:{}),...(spec.kind==='recording'?{short:'true'}:{})} as Record<string,string>,classList:{contains:(c:string)=>c==='workspace-view-tab'},
   querySelector:(selector:string)=>selector==='.workspace-tab-label'?label(tab):selector==='.workspace-tab-thumb'?(spec.thumb?{}:null)
    :selector.includes('role="tab"')?{getAttribute:()=>spec.selected?'true':'false'}
    // A lit-cell selector matches the Tab when its cell's state is one the selector names, as the DOM would.
    :selector.includes('data-kind="lit-cell"')?(spec.cell&&[...selector.matchAll(/data-state="([a-z]+)"/g)].some(m=>m[1]===spec.cell)?{}:null):null};
  Object.defineProperty(tab,'offsetWidth',{get:()=>width(tab)});
  Object.defineProperty(tab,'offsetLeft',{get:()=>tabs.children.slice(0,tabs.children.indexOf(tab)).reduce((sum:number,t:any)=>sum+width(t),0)});
  tabs.children.push(tab);
 }
 Object.defineProperty(tabs,'scrollWidth',{get:()=>widths().reduce((a:number,b:number)=>a+b,0)});
 const count={textContent:'',dataset:{} as Record<string,string>},more={label:'',setAttribute:(_:string,v:string)=>{more.label=v;}},fade={dataset:{} as Record<string,string>,style:{} as Record<string,string>};
 fitTabRow({strip,tabs,fade,count,more} as never);
 const find=(name:string)=>tabs.children.find((t:any)=>t.spec.name===name);
 return {strip,count,more,tabs,width:(name:string)=>width(find(name)),
  /** Whether a Tab's name shows cut (an ellipsis), as the person sees it. */
  cut:(name:string)=>{const l=label(find(name));return l.scrollWidth>l.clientWidth+1;},
  shown:()=>tabs.children.filter((t:any)=>!t.hidden).map((t:any)=>t.spec.name+(t.dataset.icon?'(icon)':'')),hidden:()=>tabs.children.filter((t:any)=>t.hidden).map((t:any)=>t.spec.name)};
}
const MAIN:FakeTab={kind:'home',width:80,home:true,selected:true,name:'Main'};
const LONG='Human-cells, 2026-10-25 (picture)';

test('when everything fits, nothing changes: no tight row, no icons, no folds, ⌄ counts nothing',()=>{
 const r=row([MAIN,{kind:'picture',width:90,name:'Tab 2'},{kind:'researcher',width:130,cell:'working',name:'Researcher 3'}],600);
 expect(r.strip.dataset.fit).toBeUndefined();expect(r.shown()).toEqual(['Main','Tab 2','Researcher 3']);expect(r.count.textContent).toBe('');expect(r.more.label).toBe('All Tabs');
});
test('an unselected Tab\'s × never takes room: a short name is whole at 96 px; the longest name shrinks first',()=>{
 // The 508 px column at 1210-1320 px with a drawer and the Assistant open (Tab 2 and Tab 4 read "Ta…" at 88 px before).
 const r=row([MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'picture',width:274,name:LONG},{kind:'picture',width:86,name:'Tab 4'}],508);
 expect(r.cut('Tab 2')).toBe(false);expect(r.cut('Tab 4')).toBe(false);expect(r.width('Tab 2')).toBe(96);expect(r.width('Tab 4')).toBe(96);
 expect(r.width(LONG)).toBeLessThan(250);expect(r.cut(LONG)).toBe(true);expect(r.shown()).toEqual(['Main','Tab 2',LONG,'Tab 4']);
 // At 1100 with two researchers (needs you, working): the researchers keep their names, and so do Tab 2 and Tab 4.
 const narrow=row([MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'picture',width:274,name:LONG},{kind:'picture',width:86,name:'Tab 4'},
  {kind:'researcher',width:138,name:'Researcher 4',cell:'needs'},{kind:'researcher',width:137,name:'Researcher 3',cell:'working'}],630);
 expect(['Tab 2','Tab 4','Researcher 4','Researcher 3'].map(narrow.cut)).toEqual([false,false,false,false]);
 expect(narrow.hidden()).toEqual([]);expect(narrow.count.textContent).toBe('');
});
test('running short: work Tabs shrink first; once a name is cut the row goes tight and a recording shortens; a researcher\'s name is never cut',()=>{
 const tabs:FakeTab[]=[MAIN,{kind:'picture',width:110,name:'Tab 2'},{kind:'recording',width:150,name:'Oct 22, 3:40 pm',thumb:true},{kind:'researcher',width:130,name:'Researcher 2',cell:'ready'}];
 // Room for every name: nothing tight, nothing folded, the researcher at its whole width.
 const roomy=row(tabs,430);
 expect(roomy.strip.dataset.fit).toBeUndefined();expect(roomy.shown()).toEqual(['Main','Tab 2','Oct 22, 3:40 pm','Researcher 2']);expect(roomy.width('Researcher 2')).toBe(130-X);
 // The recording's name would be cut: the row goes tight and it shows its short label instead; still nothing folded.
 const r=row(tabs,400);
 expect(r.strip.dataset.fit).toBe('tight');expect(r.shown()).toEqual(['Main','Tab 2','Oct 22, 3:40 pm','Researcher 2']);expect(r.count.textContent).toBe('');
 expect(r.cut('Researcher 2')).toBe(false);expect(r.cut('Tab 2')).toBe(false);
 // Over even then: the recording keeps only its thumbnail before anything folds.
 const over=row(tabs,370);
 expect(over.shown()).toEqual(['Main','Tab 2','Oct 22, 3:40 pm(icon)','Researcher 2']);expect(over.hidden()).toEqual([]);
});
test('a long work Tab shrinks before it is iconified',()=>{
 const r=row([MAIN,{kind:'picture',width:200,name:LONG,thumb:true},{kind:'researcher',width:150,name:'Researcher 1',cell:'working'}],360);
 expect(r.shown()).toEqual(['Main',LONG,'Researcher 1']);expect(r.width(LONG)).toBeCloseTo(154,0);
});
test('with four finished researchers at 1440 with the drawer open, the person\'s own Tabs stay on the row; finished researchers fold first',()=>{
 const ready=(n:number):FakeTab=>({kind:'researcher',width:150,name:`Researcher ${n}`,cell:'ready'});
 const r=row([MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'picture',width:180,name:'Human cells'},{kind:'picture',width:86,name:'Tab 4'},ready(1),ready(2),ready(3),ready(4)],520);
 // Short names are never iconified (no row of identical marks): Tab 2, Human cells and Tab 4 keep their names.
 expect(r.shown()).toEqual(expect.arrayContaining(['Main','Tab 2','Human cells','Tab 4']));
 expect(r.hidden().every((n:string)=>n.startsWith('Researcher'))).toBe(true);expect(r.hidden()).toEqual(['Researcher 2','Researcher 3','Researcher 4']); // rightmost first
 expect(r.count.textContent).toBe('3');
});
test('then pictures and recordings with long names keep only their thumbnail, from the right; then the least urgent fold into ⌄, never one working, needing you or showing',()=>{
 const r=row([MAIN,{kind:'picture',width:110,name:LONG,thumb:true},{kind:'recording',width:150,name:'Oct 22, 3:40 pm',thumb:true},{kind:'researcher',width:130,name:'R2',cell:'ready'},{kind:'researcher',width:130,name:'R3',cell:'working'},{kind:'researcher',width:130,name:'R4',cell:'needs'}],450);
 // A finished researcher folds before the person's own Tabs (they keep their thumbnails; the room the fold frees gives
 // the picture its name back); Main, the one showing and the working ones stay.
 expect(r.shown()).toEqual(['Main',LONG,'Oct 22, 3:40 pm(icon)','R3','R4']);expect(r.hidden()).toEqual(['R2']);
 expect(r.count.textContent).toBe('1');expect(r.count.dataset.urgency).toBeUndefined();expect(r.more.label).toBe('All Tabs, 1 more');
 const tight=row([MAIN,{kind:'researcher',width:130,name:'R3',cell:'working'},{kind:'researcher',width:130,name:'R4',cell:'needs'},{kind:'researcher',width:130,name:'R5',cell:'needs'}],300);
 // Nothing urgent folds: what does not fit scrolls, and ⌄ counts it in the most urgent colour.
 expect(tight.hidden()).toEqual([]);expect(tight.count.dataset.urgency).toBe('needs');expect(tight.more.label).toBe(`All Tabs, ${tight.count.textContent} more, one needs you`);
});
test('a researcher just started and waiting for a free slot never folds into ⌄; finished ones still do, and ⌄ never calls a waiting one working',()=>{
 const tabs=():FakeTab[]=>[MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'researcher',width:130,name:'R1',cell:'ready'},
  {kind:'researcher',width:130,name:'R2',cell:'queued'},{kind:'researcher',width:130,name:'R3',cell:'stopped'}];
 // Short of room, the finished researchers fold (rightmost first) and the waiting one stays beside the person's Tab.
 const r=row(tabs(),300);
 expect(r.shown()).toEqual(['Main','Tab 2','R2']);expect(r.hidden()).toEqual(['R1','R3']);
 expect(r.count.textContent).toBe('2');expect(r.count.dataset.urgency).toBeUndefined();expect(r.more.label).toBe('All Tabs, 2 more');
 // With room for Main alone, everything else that may fold has folded; the waiting researcher does not, so it scrolls,
 // and ⌄ counts it without saying it is working.
 const tight=row(tabs(),180);
 expect(tight.hidden()).toEqual(['Tab 2','R1','R3']);expect(tight.shown()).toContain('R2');
 expect(tight.count.textContent).toBe('4');expect(tight.count.dataset.urgency).toBeUndefined();expect(tight.more.label).toBe('All Tabs, 4 more');
});
test('what folding frees gives a thumbnail its name back',()=>{
 // Over at first: the picture keeps only its thumbnail; folding a finished researcher then leaves room for its name.
 const ready=(n:number):FakeTab=>({kind:'researcher',width:150,name:`Researcher ${n}`,cell:'ready'});
 const r=row([MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'picture',width:200,name:LONG,thumb:true},{kind:'researcher',width:150,name:'Researcher 3',cell:'working'},ready(1),ready(2)],560);
 expect(r.hidden()).toEqual(['Researcher 2']);expect(r.shown()).toEqual(['Main','Tab 2',LONG,'Researcher 3','Researcher 1']);
 // Still no room for it: it keeps its thumbnail.
 const tight=row([MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'picture',width:200,name:LONG,thumb:true},{kind:'researcher',width:150,name:'Researcher 3',cell:'working'},ready(1),ready(2)],500);
 expect(tight.shown()).toEqual(['Main','Tab 2',`${LONG}(icon)`,'Researcher 3','Researcher 1']);
});
test('a picture with no thumbnail, or a short name, is never reduced to a shared mark: it folds into ⌄ instead',()=>{
 const r=row([MAIN,{kind:'picture',width:110,name:LONG},{kind:'picture',width:86,name:'Tab 3',thumb:true},{kind:'researcher',width:130,name:'R3',cell:'working'}],300);
 expect(r.shown().some((n:string)=>n.endsWith('(icon)'))).toBe(false);expect(r.hidden()).toEqual(['Tab 3']); // the rightmost of the person's own
});
test('an unselected researcher\'s × floats too, so a picture keeps its name (the mock\'s workspace frame)',()=>{
 // Main, Tab 2, a recording and two researchers in the mock's 668 px column, less + and ⌄.
 const r=row([MAIN,{kind:'picture',width:86,name:'Tab 2'},{kind:'recording',width:180,name:'Oct 22, 3:40 pm'},{kind:'researcher',width:150,name:'Researcher 3',cell:'working'},{kind:'researcher',width:150,name:'Researcher 4',cell:'needs'}],530);
 expect(r.strip.dataset.fit).toBe('tight');expect(r.shown()).toEqual(['Main','Tab 2','Oct 22, 3:40 pm','Researcher 3','Researcher 4']);expect(r.count.textContent).toBe('');
});
test('the fit and the count allow the same 1 px: a Tab rounding 1 px past the edge is neither cut nor counted',()=>{
 const r=row([MAIN,{kind:'picture',width:90,name:'Tab 2'},{kind:'researcher',width:131,name:'Researcher 4',cell:'needs'}],282);
 expect(r.strip.dataset.fit).toBeUndefined();expect(r.shown()).toEqual(['Main','Tab 2','Researcher 4']);
 expect(r.count.textContent).toBe('');expect(r.count.dataset.urgency).toBeUndefined();expect(r.tabs.style.maxWidth).toBe('');
});
test('a recording\'s short label is its first part, never cut mid-word',()=>{
 expect(shortLabel('Oct 22, 3:40 pm')).toBe('Oct 22');expect(shortLabel('Human cells · David Root (CC0)')).toBe('Human cells');expect(shortLabel('Tab 2')).toBe('Tab 2');
});

/** A WorkspaceViews with a synthetic document, for one Tab's anatomy and the row's order. */
function views(more:Record<string,unknown>={}){
 vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag),getElementById:()=>null,body:{dataset:{}}});
 vi.stubGlobal('window',Object.assign(new EventTarget(),{setTimeout:()=>1}));
 const view=(id:string,title:string,extra={})=>({id,title,specimen:{visible:true,rect:null},items:[],notes:[],...extra});
 return Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,openIds:['t2','main','rec'],tabs:new TestElement('div'),
  layout:{active:'t2',views:[view('t2','Tab 2'),view('main','Main'),view('rec','Oct 22, 3:40 pm',{input:{source:'a7',position:0}})]},
  stage:Object.assign(new TestElement('main'),{id:'stage'}),noteButton:{disabled:false},renderCompletions:vi.fn(),roots:()=>[],refit:vi.fn(),changed:vi.fn(),
  opts:{describeInput:(source:string)=>source==='a7'?'recording':null,notice:vi.fn()},...more});
}
test('one Tab anatomy: Main first whatever the saved order, its mark, its whole name, × on every Tab but Main',()=>{
 const current=views();Reflect.get(WorkspaceViews.prototype,'render').call(current);
 const wraps=current.tabs.children as TestElement[];
 expect(wraps.map(w=>w.dataset.tabKind)).toEqual(['home','picture','recording']);expect(wraps[0].dataset.home).toBe('true');
 expect(wraps.map(w=>w.querySelector('.workspace-tab-label')!.textContent)).toEqual(['Main','Tab 2','Oct 22, 3:40 pm']);
 expect(wraps.map(w=>!!w.querySelector('.workspace-tab-x'))).toEqual([false,true,true]);
 expect(wraps[2].dataset.short).toBe('true');expect(wraps[2].querySelector('.workspace-tab-short')!.textContent).toBe('Oct 22');
 expect(wraps[1].children[0].getAttribute('aria-selected')).toBe('true');expect(current.refit).toHaveBeenCalled();
});
test('Alt+arrow on a work Tab never moves Main or puts a Tab before it',()=>{
 const current=views({openIds:['main','t2','rec'],layout:{active:'t2',views:[{id:'main',title:'Main',specimen:{visible:true,rect:null},items:[],notes:[]},{id:'t2',title:'Tab 2',specimen:{visible:true,rect:null},items:[],notes:[]},{id:'rec',title:'Rec',specimen:{visible:true,rect:null},items:[],notes:[]}]}});
 vi.spyOn(TestElement.prototype,'matches').mockImplementation(function(this:TestElement,selector:string){const data=/^\[data-([a-z-]+)\]$/.exec(selector);return data?data[1].replace(/-([a-z])/g,(_,c:string)=>c.toUpperCase()) in this.dataset:selector.startsWith('.')?this.classList.contains(selector.slice(1)):this.tagName.toLowerCase()===selector;});
 Reflect.get(WorkspaceViews.prototype,'render').call(current);
 const button=(i:number)=>(current.tabs.children[i] as TestElement).children[0] as unknown as {onkeydown:(e:object)=>void};
 const key=(i:number,k:string)=>button(i).onkeydown({key:k,altKey:true,preventDefault:()=>{}});
 key(1,'ArrowLeft');expect(current.layout.views.map((v:{id:string})=>v.id)).toEqual(['main','t2','rec']);expect(current.changed).not.toHaveBeenCalled();
 key(0,'ArrowRight');expect(current.layout.views.map((v:{id:string})=>v.id)).toEqual(['main','t2','rec']);
 key(1,'ArrowRight');expect(current.layout.views.map((v:{id:string})=>v.id)).toEqual(['main','rec','t2']);
});

test('closing shows where the Tab went: a copy flies to its target and the target pulses; reduced motion keeps only the pulse',()=>{
 const body=new TestElement('body'),target=Object.assign(new TestElement('button'),{getBoundingClientRect:()=>({left:10,top:200,width:60,height:50})});
 vi.stubGlobal('document',{createElement:(tag:string)=>new TestElement(tag),body});vi.stubGlobal('window',{setTimeout:vi.fn()});
 const frames:FrameRequestCallback[]=[];vi.stubGlobal('requestAnimationFrame',(f:FrameRequestCallback)=>{frames.push(f);return 1;});
 let reduced=false;vi.stubGlobal('matchMedia',()=>({matches:reduced}));
 flyTo({left:400,top:60,width:120,height:36} as DOMRect,'Tab 2',null,target as unknown as HTMLElement);
 const fly=body.children[0];expect(fly.className).toBe('workspace-tab-fly');expect(fly.textContent).toBe('Tab 2');
 expect(fly.style['--dx']).toBe(`${10+30-(400+60)}px`);expect(fly.style['--dy']).toBe(`${200+25-(60+18)}px`);
 frames.splice(0).forEach(f=>f(0));frames.splice(0).forEach(f=>f(0));expect(fly.classList.contains('go')).toBe(true);
 fly.dispatchEvent(new Event('transitionend'));expect(body.children).toHaveLength(0);expect(target.classList.contains('workspace-pulse')).toBe(true);
 target.classList.remove('workspace-pulse');reduced=true;flyTo({left:0,top:0,width:10,height:10} as DOMRect,'R3',null,target as unknown as HTMLElement);
 expect(body.children).toHaveLength(0);expect(target.classList.contains('workspace-pulse')).toBe(true);
});

function agent(more:Record<string,unknown>={}){
 vi.stubGlobal('document',{hidden:false,querySelector:()=>null,body:{dataset:{}}});
 const view={id:'main',title:'Main',items:[] as any[],notes:[]},other={id:'t2',title:'Tab 2',items:[] as any[],notes:[]};
 const order:string[]=[];
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,layout:{schema:5,active:'main',views:[view,other]},openIds:['main','t2'],
  completionRestores:new Set(),loaded:new Set(['main']),capture:vi.fn(),changed:vi.fn(),render:vi.fn(),flush:vi.fn(async()=>{}),roots:()=>[],
  goTo:vi.fn(async(id:string)=>{order.push(`goTo:${id}`);current.layout.active=id;return true;}),dirty:false,conflict:false,
  opts:{notice:vi.fn(),leaveResearch:vi.fn(()=>{order.push('leave');return true;}),
   research:{report:()=>[{id:'research:r1',title:'Researcher 1 · Bright regions',open:true,selected:true,kind:'researcher'}],switch:vi.fn(async()=>true),close:vi.fn(()=>true)}},...more});
 return {current,order};
}
test('the Assistant\'s switch leaves a researcher\'s page first, as a click does; researcher Tabs are in its report',async()=>{
 const {current,order}=agent();
 const listed=await current.agentCommand('p',{action:'list'});
 expect(listed.tabs).toEqual(expect.arrayContaining([expect.objectContaining({id:'research:r1',kind:'researcher',selected:true}),expect.objectContaining({id:'main',selected:false})]));
 const switched=await current.agentCommand('p',{action:'switch',tab:'t2'});expect(switched.ok).toBe(true);expect(order).toEqual(['leave','goTo:t2']);
 expect((await current.agentCommand('p',{action:'switch',tab:'research:r1'})).ok).toBe(true);expect(current.opts.research.switch).toHaveBeenCalledWith('research:r1');
 expect((await current.agentCommand('p',{action:'close',tab:'research:r1'})).ok).toBe(true);expect(current.opts.research.close).toHaveBeenCalledWith('research:r1');
 expect((await current.agentCommand('p',{action:'rename',tab:'research:r1',title:'x'})).ok).toBe(false);
 const refused=agent();refused.current.opts.leaveResearch=vi.fn(()=>false);
 expect(await refused.current.agentCommand('p',{action:'switch',tab:'t2'})).toEqual({ok:false,error:'Finish or discard the Study form first, then ask again.'});
 expect(refused.current.goTo).not.toHaveBeenCalled();
});

test('the layout chip coming or going refits the row at once; a quiet status that changes does not',()=>{
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{status:{textContent:'Saved',dataset:{quiet:'true'}},refit:vi.fn()});
 const say=(text:string)=>Reflect.get(WorkspaceViews.prototype,'say').call(current,text);
 say('Saving…');expect(current.refit).not.toHaveBeenCalled();expect(current.status.dataset.quiet).toBe('true'); // Saved takes no room, nor does Saving…
 say('Not saved');expect(current.refit).toHaveBeenCalledTimes(1);expect(current.status.dataset.quiet).toBe('false');
 say('Not saved');expect(current.refit).toHaveBeenCalledTimes(1);
 say('Reload required');expect(current.refit).toHaveBeenCalledTimes(2); // a longer chip, less room
 say('Saved');expect(current.refit).toHaveBeenCalledTimes(3);expect(current.status.dataset.quiet).toBe('true');
});
test('the row\'s and the Stage bar\'s rules keep specificity flat: no ID selector, so a state rule is never outweighed',()=>{
 const css=readFileSync(fileURLToPath(new URL('./workspace-feel.css',import.meta.url)),'utf8').replace(/\/\*[\s\S]*?\*\//g,'');
 // Selectors only: the text before each block (property values such as colours are left out).
 const selectors=css.split('}').map(part=>part.split('{')[0]).filter(sel=>sel.trim()&&!sel.trim().startsWith('@'));
 expect(selectors.filter(sel=>/#[A-Za-z_-]/.test(sel))).toEqual([]);
 // A tight row floats every unselected Tab's ×, a researcher's too (the mock's .tabrow[data-fit="tight"] rule).
 expect(css).not.toMatch(/data-fit='tight'\][^{]*data-tab-kind='researcher'/);
 // A researcher Tab's × shows on hover, focus and when selected, exactly like every other Tab's (the approved mock).
 expect(css).not.toMatch(/data-tab-kind='researcher'\][^{]*\.workspace-tab-x/);
});
test('Review recovery is on the row only for a copy at least as new as the loaded revision whose arrangement differs',()=>{
 const saved={schema:5,coordinates:'stage',views:[{id:'main',title:'Main'}]};
 const copy=(revision:number,title:string)=>({value:{revision,layout:{...saved,views:[{id:'main',title}]}}});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',loadedRevision:4,lastSaved:JSON.stringify(saved),recover:{hidden:true},recovery:{list:vi.fn(()=>[copy(3,'Older')])}});
 const update=()=>Reflect.get(WorkspaceViews.prototype,'updateRecovery').call(current);
 update();expect(current.recover.hidden).toBe(true); // an older copy is listed under ⌄, not raised on the row
 current.recovery.list=()=>[copy(4,'Main')];update();expect(current.recover.hidden).toBe(true); // the same arrangement as saved
 current.recovery.list=()=>[copy(3,'Older'),copy(5,'Edited elsewhere')];update();expect(current.recover.hidden).toBe(false);
 // A copy kept as the app quit holds the arrangement this opening loaded: changing the layout afterwards (a window
 // opened, saved) never raises it on the row (Beta 0.4.0's clean-VM check saw it after Show masks).
 Object.assign(current,{loadedViews:JSON.stringify(saved.views),lastSaved:JSON.stringify({...saved,views:[{id:'main',title:'Main',items:[1]}]})});
 current.recovery.list=()=>[copy(4,'Main')];update();expect(current.recover.hidden).toBe(true);
 current.recovery.list=()=>[copy(4,'Work the quit kept')];update();expect(current.recover.hidden).toBe(false); // unsaved work: still offered
});
