// Real Actions admission/controller and placement code; synthetic DOM, no model execution.
import { afterEach, expect, test, vi } from 'vitest';
import { configureControlDeck, parkControls, type ControlDeck } from './capability-controls';
import { ActionsPanel } from './actions-panel';
import type { CapabilitiesWire } from './protocol';
import { syntheticDOM, TestElement, fire, settleMicrotasks } from './workspace-windows.test-support';
let deck:ControlDeck;
afterEach(()=>{deck?.dispose();vi.unstubAllGlobals();});
function setup(){
 const dom=syntheticDOM();dom.host.dataset.workspaceProject='p1';dom.host.dataset.activeWorkspaceView='main';
 Object.assign(dom.document,{querySelector:()=>null,querySelectorAll:()=>[]});vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
 vi.stubGlobal('window',new EventTarget());vi.stubGlobal('innerWidth',1600);vi.stubGlobal('innerHeight',900);
 vi.stubGlobal('MutationObserver',class{observe(){}disconnect(){}});const saved=new Map<string,string>();
 vi.stubGlobal('localStorage',{getItem:(k:string)=>saved.get(k)??null,setItem:(k:string,v:string)=>saved.set(k,v)});
 deck=configureControlDeck('isolated',()=>{});
 const run=vi.fn(async()=>({ok:true,said:'complete'}));
 const deps={run,stop:vi.fn(async()=>({ok:true})),selected:()=>null,session:()=> 'p1',openModels:vi.fn(),openActivity:vi.fn(),openTable:vi.fn(),openCamera:vi.fn(),cameraPage:()=>'/camera/'};
 return{dom,deps};
}
test('a generated form survives left/right/floating, retains its arguments, and still runs through Actions',async()=>{
 const {dom,deps}=setup();const root=new TestElement('section');dom.host.append(root);
 const panel=new ActionsPanel(root as unknown as HTMLElement,deps);
 panel.setTools({set_objective:{kind:'effect',family:'measuring',params:{type:'object',properties:{objective:{type:'number'}},required:['objective']}}});
 const wire:CapabilitiesWire={type:'capabilities',roles:[],capabilities:[{id:'objective',family:'measuring',actor:'agent',title:'Set objective',what:'Set calibration',how:{tool:'set_objective'},available:true,why:'',verdict:null}],families:[],actors:{you:'',agent:'',auto:'',researcher:''},preferences:[],pending:0,build:'',agentText:''};
 panel.setCapabilities(wire);fire(root.querySelector('[data-action="objective"]')!.querySelector('.action-words')!,'click');
 const handle=[...deck.controls][0],content=handle.options.content as unknown as TestElement;
 const select=content.querySelector('select')!;select.value='40';fire(select,'change');
 expect(dom.host.dataset.controlLeftActive).toBe('true');
 expect(dom.host.style['--control-left-width']).toBe('360px');
 handle.move('right');expect(dom.host.dataset.controlLeftActive).toBe('false');expect(dom.host.dataset.controlRightActive).toBe('true');
 handle.move('floating');expect(dom.host.dataset.controlRightActive).toBe('false');handle.hide();handle.show();
 expect(content.querySelector('select')).toBe(select);expect(select.value).toBe('40');
 await panel.start(panel.rowsShown()[0]);
 expect(deps.run).toHaveBeenCalledWith('objective',{objective:40},{},expect.any(String));
 expect(content.textContent).toContain('complete');
 const search=root.querySelector('.actions-search')!;search.value='no such tool';Object.assign(dom.document,{activeElement:search});fire(search,'input');
 expect(root.querySelector('.actions-none')?.textContent).toBe('No action matches.');
});
test('comparison settings and running ownership survive moving and closing the task’s controls',async()=>{
 const {dom,deps}=setup();let finish!:(r:{ok:boolean;said:string})=>void;
 deps.run.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
 const root=new TestElement('section');dom.host.append(root);
 const panel=new ActionsPanel(root as unknown as HTMLElement,deps);
 panel.setTools({outline_cells:{kind:'effect',family:'scene',params:{type:'object',properties:{}}}});
 const wire={type:'capabilities',capabilities:[{id:'cells',family:'outlining',actor:'agent',title:'Outline every cell',what:'',how:{tool:'outline_cells'},roles:['cells'],available:true,why:'',verdict:null}],
  roles:[{id:'cells',kind:'model',title:'Cell finder',purpose:'',current:'a',options:[{id:'a',label:'Model A',note:'',available:true,runs:'mac'},{id:'b',label:'Model B',note:'',available:true,runs:'mac'}],powers:[],agentCanChange:[]}],
  families:[],actors:{you:'',agent:'',auto:'',researcher:''},preferences:[],pending:0,build:'',agentText:''} as unknown as CapabilitiesWire;
 panel.setCapabilities(wire);fire(root.querySelector('[data-action="cells"]')!.querySelector('.action-words')!,'click');
 const handle=[...deck.controls][0],content=handle.options.content as unknown as TestElement;
 // Compare… is in the page's ⋯ (its own items, above Left / Float / Right).
 fire((handle.shell as unknown as TestElement).querySelectorAll('button').find(n=>n.textContent==='Compare models…')!,'click');
 const input=content.querySelectorAll('input').find(n=>(n as unknown as HTMLInputElement).type==='checkbox'&&n.value==='b')!;Object.assign(input,{checked:true});fire(input,'change');
 handle.move('right');fire(content.querySelectorAll('button').find(n=>n.textContent==='Compare 2 models')!,'click');
 await settleMicrotasks();expect(deps.run).toHaveBeenCalledTimes(1);
 handle.move('floating');handle.hide();expect(deps.stop).not.toHaveBeenCalled();
 finish({ok:true,said:'Both results retained'});await settleMicrotasks();await settleMicrotasks();handle.show();
 expect(deps.run).toHaveBeenCalledExactlyOnceWith('compare-models',{operation:'cells',implementations:['a','b'],policy:'raw',exact:true,input:'view'},{},expect.any(String));
 expect(content.textContent).toContain('Finished.');
});

test('wide controls fall back to overlay instead of collapsing the canvas beside a wide navigation panel',()=>{
 const {dom}=setup();vi.stubGlobal('innerWidth',1280);
 const rail=new TestElement('nav'),nav=new TestElement('aside');rail.style.width='64px';nav.style.width='720px';dom.host.append(rail,nav);
 Object.assign(dom.document,{querySelector:(selector:string)=>selector==='.study-rail'?rail:selector==='.study-drawer'?nav:null});
 const content=new TestElement('div');dom.host.append(content);
 const h=deck.attach({id:'wide',title:'Wide tool',content:content as unknown as HTMLElement});h.show();h.resize(640);h.move('right');
 expect(dom.host.style['--control-stage-left']).toBe('784px');expect(dom.host.style['--control-stage-right']).toBe('0px');
 nav.style.width='360px';deck.refresh();expect(dom.host.style['--control-stage-left']).toBe('424px');expect(dom.host.style['--control-stage-right']).toBe('0px');
 vi.stubGlobal('innerWidth',1600);deck.refresh();expect(dom.host.style['--control-stage-right']).toBe('640px');
});

test('a rail switch parks an Actions page with its arguments; it comes back at the Tools drawer’s width and still runs',async()=>{
 const {dom,deps}=setup();deck.dispose();
 let tools=440;const shown:string[]=[];
 deck=configureControlDeck('isolated',{leftWidth:()=>tools,setLeftWidth:width=>{tools=width;},shown:(side,page)=>shown.push(`${side}:${page}`),toolsShowing:()=>true});
 const root=new TestElement('section');dom.host.append(root);
 const panel=new ActionsPanel(root as unknown as HTMLElement,deps);
 panel.setTools({set_objective:{kind:'effect',family:'measuring',params:{type:'object',properties:{objective:{type:'number'}},required:['objective']}}});
 const wire:CapabilitiesWire={type:'capabilities',roles:[],capabilities:[{id:'objective',family:'measuring',actor:'agent',title:'Set objective',what:'Set calibration',how:{tool:'set_objective'},available:true,why:'',verdict:null}],families:[],actors:{you:'',agent:'',auto:'',researcher:''},preferences:[],pending:0,build:'',agentText:''};
 panel.setCapabilities(wire);fire(root.querySelector('[data-action="objective"]')!.querySelector('.action-words')!,'click');
 const handle=[...deck.controls][0],content=handle.options.content as unknown as TestElement;
 expect(handle.page).toBe(true);expect(handle.shell.style.width).toBe('440px');expect(shown).toEqual(['left:true']);
 expect((handle.shell as unknown as TestElement).querySelector('.capability-controls-back')!.hidden).toBe(false);
 const select=content.querySelector('select')!;select.value='40';fire(select,'change');
 parkControls('left');expect(handle.visible).toBe(false);expect(dom.host.dataset.controlLeftActive).toBe('false');expect(deck.parkedPage()).toBe(handle);
 deck.parkedPage()!.show();expect(handle.visible).toBe(true);expect(content.querySelector('select')).toBe(select);expect(select.value).toBe('40');
 await panel.start(panel.rowsShown()[0]);
 expect(deps.run).toHaveBeenCalledWith('objective',{objective:40},{},expect.any(String));
});

test('below 1200 px a drawer beside a page docked right floats under the Tab row and the Stage bar, never over the Tabs (R-16)',()=>{
 const {dom}=setup();vi.stubGlobal('innerWidth',1100);vi.stubGlobal('innerHeight',800);
 const rail=new TestElement('nav'),drawer=new TestElement('aside'),bar=new TestElement('div');
 Object.assign(rail.style,{width:'72px',height:'752px',top:'48px'});drawer.style.width='360px';Object.assign(bar.style,{top:'96px',height:'34px'});
 dom.host.append(rail,drawer,bar);
 Object.assign(dom.document,{querySelector:(selector:string)=>({'.study-rail':rail,'.study-drawer':drawer,'.workspace-stage-bar':bar} as Record<string,TestElement>)[selector]??null});
 const content=new TestElement('div');dom.host.append(content);
 // Tools, the Bright objects page, ⋯ › Right, then Researchers: 72 + 360 + 360 leaves the Stage under 320 px.
 const page=deck.attach({id:'bright-objects',title:'Bright objects',content:content as unknown as HTMLElement});page.show();page.move('right');
 expect(dom.host.style['--control-stage-left']).toBe('72px');
 expect(dom.host.dataset.drawerDock).toBe('over'); // the drawer starts under the Tab row and the Stage bar (workspace-feel.css)
 expect(page.shell.style).toMatchObject({top:'130px',height:'630px'}); // the right page too
 // At the mock's 320 px they fit beside the Stage: the Tab row starts after the drawer, and nothing floats.
 drawer.style.width='320px';deck.refresh();
 expect(dom.host.dataset.drawerDock).toBe('beside');expect(dom.host.style['--control-stage-left']).toBe('392px');expect(page.shell.style.top).toBe('48px');
 // A page on the left that fits sits beside the Stage under the rail's top; one that cannot be reserved floats too.
 page.move('left');expect(dom.host.dataset.drawerDock).toBe('beside');expect(page.shell.style.top).toBe('48px');
 vi.stubGlobal('innerWidth',640);deck.refresh();expect(dom.host.dataset.drawerDock).toBe('over');expect(page.shell.style.top).toBe('130px');
});

test('the Stage’s insets come from the widths the workspace lays out, never measured, and body is written only on change (b041)',()=>{
 const {dom}=setup();deck.dispose();
 const measured:string[]=[];Object.assign(dom.document,{querySelector:(selector:string)=>{measured.push(selector);return null;}});
 const writes:string[]=[];const write=dom.host.style.setProperty;dom.host.style.setProperty=(name:string,value:string)=>{writes.push(`${name} ${value}`);write(name,value);};
 let sides={rail:72,left:340,right:360};
 deck=configureControlDeck('isolated',{sides:()=>sides});
 deck.refresh();
 expect(dom.host.style['--control-stage-left']).toBe('412px');expect(dom.host.style['--control-stage-right']).toBe('360px');
 expect(measured.filter(selector=>['.study-rail','.study-drawer','.study-data-window','.study-right-panel'].includes(selector))).toEqual([]);
 // The same layout again (a refresh every frame at idle, or the press's own): nothing written, nothing re-styled.
 writes.length=0;deck.refresh();deck.refresh();expect(writes).toEqual([]);
 // The left column closes: only what changed is written.
 sides={rail:72,left:0,right:360};deck.refresh();
 expect(writes).toEqual(['--control-stage-left 72px']);
});

test('the deck’s observer refreshes once a frame however many attributes change, at once while the page is hidden (b041)',async()=>{
 const {dom}=setup();deck.dispose();
 let observed:(()=>void)|null=null;
 vi.stubGlobal('MutationObserver',class{constructor(callback:()=>void){observed=callback;}observe(){}disconnect(){}});
 const frames:FrameRequestCallback[]=[];vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>frames.push(callback));
 deck=configureControlDeck('isolated',()=>{});
 const refresh=vi.spyOn(deck,'refresh');
 // The magnifier's buttons, the readout and a chip changing within one frame: one refresh, in that frame.
 observed!();observed!();observed!();
 expect(refresh).not.toHaveBeenCalled();expect(frames).toHaveLength(1);
 frames.splice(0).forEach(callback=>callback(16));expect(refresh).toHaveBeenCalledTimes(1);
 observed!();frames.splice(0).forEach(callback=>callback(32));expect(refresh).toHaveBeenCalledTimes(2);
 // Hidden, the page draws no frames: the refresh still comes, in a microtask.
 Object.assign(dom.document,{hidden:true});observed!();expect(frames).toHaveLength(0);
 await settleMicrotasks();expect(refresh).toHaveBeenCalledTimes(3);
});
