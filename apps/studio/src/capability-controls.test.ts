import { afterEach, describe, expect, it, vi } from 'vitest';
import { controlStageInsets, ControlDeck, controlPreference, controlRect, controlModalOpen } from './capability-controls';
import { syntheticDOM, TestElement, fire } from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());
describe('personal capability controls', () => {
 it('remembers independent tool openness and placement per Tab without moving owned native controls',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let scope=['p','A'];const deck=new ControlDeck({scope:()=>scope,viewport:()=>[1500,900],storage:new MapStorage(),profile:'test'});
  const input=new TestElement('input');input.value='kept settings';const content=new TestElement('div');content.append(input);dom.host.append(content);
  const tool=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement,rememberOpenPerScope:true});
  const native=deck.attach({id:'native',title:'Native',content:dom.document.createElement('div') as unknown as HTMLElement,owner:content as unknown as HTMLElement});
  tool.show();tool.move('right');native.show();native.move('floating');
  scope=['p','B'];deck.refresh();expect(tool.visible).toBe(false);expect(native.visible).toBe(false);
  tool.show();tool.move('left');tool.hide();
  scope=['p','A'];deck.refresh();expect(tool.visible).toBe(true);expect(tool.position).toBe('right');expect(native.visible).toBe(true);
  scope=['p','B'];deck.refresh();expect(tool.visible).toBe(false);expect(tool.position).toBe('left');expect(native.visible).toBe(false);
  tool.show();scope=['other','A'];deck.refresh();expect(tool.visible).toBe(false);
  scope=['p','B'];deck.refresh();expect(tool.visible).toBe(true);expect(input.value).toBe('kept settings');expect(content.parentElement).toBe(tool.body);deck.dispose();
 });
 it('recognizes visible application setup sheets as well as native modals',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
  const sheet=new TestElement('div');sheet.setAttribute('role','dialog');sheet.setAttribute('aria-modal','true');
  let displayed=true;Object.assign(sheet,{checkVisibility:()=>displayed});
  Object.assign(dom.document,{querySelector:()=>null,querySelectorAll:(selector:string)=>selector==='[role="dialog"][aria-modal="true"]'?[sheet]:[]});
  expect(controlModalOpen()).toBe(true);displayed=false;expect(controlModalOpen()).toBe(false);
  Object.assign(dom.document,{querySelector:()=>sheet});expect(controlModalOpen()).toBe(true);
 });
 it('validates preferences and keeps floating controls reachable after resizing', () => {
  expect(controlPreference('{"position":"evil","width":9999}')).toMatchObject({position:'left',width:720});
  expect(controlPreference('broken')).toMatchObject({position:'left',width:360});
  expect(controlRect({x:1700,y:900,width:600,height:700},800,600)).toEqual({x:200,y:0,width:600,height:600});
 });
 it('moves the same controls without losing values, listeners or ownership; close never invokes work', () => {
  const dom=syntheticDOM(); vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const owner=new TestElement('section'), content=new TestElement('div'), input=new TestElement('input');
  input.value='unsaved scientific parameter';content.append(input);owner.append(content);dom.host.append(owner);
  let clicks=0;input.addEventListener('click',()=>clicks++);
  const deck=new ControlDeck({scope:()=>['p1','t1'],viewport:()=>[1500,900],storage:new MapStorage(),profile:'isolated'});
  const handle=deck.attach({id:'segmentation',title:'Segment cells',content:content as unknown as HTMLElement,owner:owner as unknown as HTMLElement});
  handle.show();expect(handle.visible).toBe(true);
  for(const side of ['right','floating','left'] as const)handle.move(side);
  expect(owner.contains(input)).toBe(true);expect(input.value).toBe('unsaved scientific parameter');
  input.dispatchEvent(new Event('click'));expect(clicks).toBe(1);
  handle.hide();expect(handle.visible).toBe(false);handle.show();expect(handle.visible).toBe(true);
  expect(owner.contains(input)).toBe(true);deck.dispose();
 });
 it('switches one tool per side, restores underlying sides, and isolates Project/Tab preferences', () => {
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);let scope=['p1','t1'];let width=1600;
  const storage=new MapStorage();const deck=new ControlDeck({scope:()=>scope,viewport:()=>[width,900],storage,profile:'isolated'});
  const add=(id:string)=>{const content=new TestElement('div');dom.host.append(content);return deck.attach({id,title:id,content:content as unknown as HTMLElement});};
  const a=add('a'),b=add('b');a.show();b.show();expect(a.visible).toBe(false);expect(b.visible).toBe(true);
  a.show();b.move('right');expect(a.visible).toBe(true);expect(b.visible).toBe(true);
  // No "Show <side> underneath" any more: the ⋯ lists only the open pages on that side.
  expect([...a.pages.querySelectorAll('button')].map(o=>o.dataset.page)).toEqual(['a']);expect(a.pages.hidden).toBe(true);
  expect([...b.pages.querySelectorAll('button')].map(o=>o.dataset.page)).toEqual(['b']);
  width=900;deck.refresh();expect(a.visible).toBe(false);expect(b.visible).toBe(true);
  b.hide();expect(a.visible).toBe(true);
  scope=['p1','t2'];deck.refresh();expect(a.visible).toBe(false);expect(b.visible).toBe(false);
  deck.reveal('left');scope=['p1','t1'];deck.refresh();expect(a.visible).toBe(true);scope=['p1','t2'];deck.refresh();
  b.show();expect(b.position).toBe('left');scope=['p1','t1'];deck.refresh();b.show();expect(b.position).toBe('right');
  deck.dispose();
 });
 it('closing the newest same-side controls reveals the base, not an older revision',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1600,900],storage:new MapStorage(),profile:'test'});
  const add=(revision:number)=>{const owner=new TestElement('section'),content=new TestElement('div'),input=new TestElement('input');
    input.value=`revision ${revision} draft`;content.append(input);owner.append(content);dom.host.append(owner);
    const handle=deck.attach({id:`placement-${revision}`,title:`Result · revision ${revision}`,content:content as unknown as HTMLElement,owner:owner as unknown as HTMLElement});
    owner.append(handle.launcher as unknown as TestElement);const focus=vi.fn();Object.assign(handle.launcher,{isConnected:true,focus});return{handle,owner,input,focus};};
  const older=add(3),current=add(4);older.handle.show();current.handle.show();
  const close=(current.handle.shell as unknown as TestElement).querySelectorAll('button').find(b=>b.getAttribute('aria-label')==='Close Result · revision 4 controls')!;fire(close,'click');
  expect(current.handle.visible).toBe(false);expect(older.handle.visible).toBe(false);
  expect(current.focus).toHaveBeenCalledOnce();expect(older.focus).not.toHaveBeenCalled();
  expect(older.owner.contains(older.input)).toBe(true);expect(older.input.value).toBe('revision 3 draft');
  expect(current.owner.contains(current.input)).toBe(true);expect(current.input.value).toBe('revision 4 draft');
  current.handle.show();expect(current.handle.visible).toBe(true);expect(older.handle.visible).toBe(false);
  older.handle.show();expect(older.handle.visible).toBe(true);expect(current.handle.visible).toBe(false);deck.dispose();
 });
 it('suspends for setup, keeps a detached navigation form alive, and refuses movement during a gesture',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let modal=false,locked=false;const content=new TestElement('div'),navigation=new TestElement('aside');navigation.append(content);dom.host.append(navigation);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1200,800],storage:new MapStorage(),profile:'test',obscured:()=>modal});
  const handle=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement,canMove:()=>!locked});
  handle.show();navigation.remove();expect(dom.host.contains(content)).toBe(true);expect(handle.visible).toBe(true);
  modal=true;deck.refresh();expect(handle.visible).toBe(false);modal=false;deck.refresh();expect(handle.visible).toBe(true);
  locked=true;handle.move('right');expect(handle.position).toBe('left');expect(handle.status.hidden).toBe(false);
  locked=false;handle.move('right');expect(handle.position).toBe('right');deck.dispose();
 });
 it('repaints an already loaded native panel when viewport clamping changes its geometry',async()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let width=1600;const geometryChanged=vi.fn();const content=new TestElement('div');dom.host.append(content);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[width,900],storage:new MapStorage(),profile:'test'});
  const handle=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement,geometryChanged});
  handle.show();handle.move('right');await Promise.resolve();geometryChanged.mockClear();
  width=900;deck.refresh();await Promise.resolve();expect(geometryChanged).toHaveBeenCalledOnce();
  deck.refresh();await Promise.resolve();expect(geometryChanged).toHaveBeenCalledOnce();deck.dispose();
 });
 it('keeps the active floating tool above restored sides without remounting or repeated popover churn',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[900,700],storage:new MapStorage(),profile:'test'});
  vi.stubGlobal('window',new EventTarget());
  const stack:string[]=[];const calls:string[]=[];
  const add=(id:string)=>{const content=new TestElement('div'),input=new TestElement('input');input.value=`${id} draft`;content.append(input);dom.host.append(content);
    const h=deck.attach({id,title:id,content:content as unknown as HTMLElement});
    Object.assign(h.shell,{showPopover:()=>{if(!stack.includes(id))stack.push(id);calls.push(`show ${id}`);},hidePopover:()=>{const i=stack.indexOf(id);if(i>=0)stack.splice(i,1);calls.push(`hide ${id}`);}});
    return{h,input};};
  const older=add('older'),active=add('active');older.h.show();active.h.show();active.h.move('right');
  expect(stack).toEqual(['active']);
  const focus=vi.fn();Object.assign(active.input,{focus});Object.assign(dom.document,{activeElement:active.input});
  active.h.move('floating');expect(stack).toEqual(['older','active']);
  expect(active.h.body.contains(active.input as unknown as Node)).toBe(true);expect(active.input.value).toBe('active draft');expect(focus).toHaveBeenCalled();
  const count=calls.length;deck.refresh();deck.refresh();expect(calls).toHaveLength(count);
  older.h.move('floating');expect(stack).toEqual(['active','older']);active.h.show(false);expect(stack).toEqual(['older','active']);
  older.h.move('left');active.h.move('floating');
  const header=(active.h.shell as unknown as TestElement).querySelector('header')!;
  fire(header,'pointerdown',{target:header,button:0,pointerId:7,clientX:650,clientY:140});
  const beforeDrag=calls.length;
  fire(header,'pointermove',{pointerId:7,clientX:450,clientY:240});
  expect(calls.slice(beforeDrag).filter(call=>call.endsWith('active'))).toEqual([]); // no hide/re-show may release capture
  fire(header,'pointerup',{pointerId:7,clientX:450,clientY:240});expect(stack).toEqual(['older','active']);
  deck.dispose();
 });
 it('offers explicit placement buttons that move live fields and report the selected position',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let locked=false;const content=new TestElement('div'),input=new TestElement('input');input.value='working settings';content.append(input);dom.host.append(content);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1400,900],storage:new MapStorage(),profile:'test'});
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement,canMove:()=>!locked});h.show();
  const buttons=(h.shell as unknown as TestElement).querySelectorAll('button');
  const left=buttons.find(b=>b.getAttribute('aria-label')==='Dock Tool controls left');
  const floating=buttons.find(b=>b.getAttribute('aria-label')==='Float Tool controls');
  const right=buttons.find(b=>b.getAttribute('aria-label')==='Dock Tool controls right');
  expect(left).toBeDefined();expect(floating).toBeDefined();expect(right).toBeDefined();
  // They are choices in the header's ⋯ (menuitemradio), each saying whether it is the current place.
  expect(left!.getAttribute('role')).toBe('menuitemradio');expect(left!.getAttribute('aria-checked')).toBe('true');
  fire(floating!,'click');expect(h.position).toBe('floating');expect(floating!.getAttribute('aria-checked')).toBe('true');expect(left!.getAttribute('aria-checked')).toBe('false');
  fire(right!,'click');expect(h.position).toBe('right');expect(right!.getAttribute('aria-checked')).toBe('true');
  locked=true;fire(left!,'click');expect(h.position).toBe('right');expect(right!.getAttribute('aria-checked')).toBe('true');
  expect(content.contains(input)).toBe(true);expect(input.value).toBe('working settings');deck.dispose();
 });
 it('attaches side controls to the measured sidebar bounds, preserving the floating rectangle',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let width=1400;const content=new TestElement('div');dom.host.append(content);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[width,900],insets:()=>({top:72,left:80}),storage:new MapStorage(),profile:'test'});
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();
  expect(h.shell.style).toMatchObject({left:'80px',top:'72px',height:'828px',width:'360px'}); // after the rail
  h.move('right');expect(h.shell.style).toMatchObject({left:'1040px',top:'72px',height:'828px'});
  h.move('floating');expect(h.shell.style).toMatchObject({left:'440px',top:'120px',height:'620px'});
  h.move('left');width=430;deck.refresh();expect(h.shell.style).toMatchObject({left:'80px',width:'350px'});deck.dispose();
 });
 it('shows actual dock destinations during dragging and removes them on Escape without saving the preview',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);vi.stubGlobal('window',new EventTarget());
  const content=new TestElement('div');dom.host.append(content);const storage=new MapStorage();
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1400,900],insets:()=>({top:72,left:80}),storage,profile:'test'});
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();h.move('floating');
  const header=(h.shell as unknown as TestElement).querySelector('header')!;
  fire(header,'pointerdown',{target:header,button:0,pointerId:1,clientX:600,clientY:140});
  fire(header,'pointermove',{pointerId:1,clientX:40,clientY:200});
  const drop=dom.host.querySelector('.capability-controls-drop')!;
  expect(drop?.getAttribute('popover')).toBe('manual');
  const left=drop.querySelector('[data-side="left"]')!;
  for(const side of ['left','right'] as const){const zone=drop.querySelector(`[data-side="${side}"]`)!;const r=zone.getBoundingClientRect();
    expect(deck.dropPosition(r.x+r.width/2,r.y+24)).toBe(side);}
  expect(left?.style).toMatchObject({left:'80px',top:'72px',height:'828px',width:'96px'});
  expect(drop.querySelector('.capability-controls-drop-landing')?.style).toMatchObject({left:'80px',top:'72px',height:'828px',width:'360px'});
  expect(drop.dataset.position).toBe('left');expect(drop.textContent).toContain('Escape');
  dom.document.dispatchEvent(Object.assign(new Event('keydown'),{key:'Escape'}));expect(h.position).toBe('floating');expect(header.captured).toBe(null);
  expect(dom.host.querySelector('.capability-controls-drop')).toBe(null);h.hide();h.show();expect(h.position).toBe('floating');deck.dispose();
 });
 it('cancelled dragging restores placement; keyboard resizing is bounded and persists',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);vi.stubGlobal('window',new EventTarget());
  const content=new TestElement('div');dom.host.append(content);const storage=new MapStorage();
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1200,800],storage,profile:'test'});
  const handle=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});handle.show();handle.move('floating');
  const shell=handle.shell as unknown as TestElement,header=shell.querySelector('header')!,before={...handle.preference.floating};
  fire(header,'pointerdown',{target:header,button:0,pointerId:1,clientX:500,clientY:140});
  fire(header,'pointermove',{pointerId:1,clientX:700,clientY:240});expect(handle.preference.floating.x).not.toBe(before.x);
  fire(header,'pointercancel',{pointerId:1});expect(handle.position).toBe('floating');expect(handle.preference.floating).toEqual(before);
  const resize=shell.querySelector('.capability-controls-resize')!;fire(resize,'keydown',{key:'End'});expect(handle.preference.width).toBe(720);
  fire(resize,'keydown',{key:'ArrowRight'});expect(handle.preference.width).toBe(720);
  fire(resize,'keydown',{key:'Home'});expect(handle.preference.width).toBe(320);deck.dispose();
 });

 it('parks a docked page when its side shows something else, and brings back the same page with its draft and scroll',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let scope=['p','t'],modal=false;const revealed:string[]=[];
  const deck:ControlDeck=new ControlDeck({scope:()=>scope,viewport:()=>[1500,900],storage:new MapStorage(),profile:'test',obscured:()=>modal,reveal:side=>{revealed.push(side);}});
  const content=new TestElement('div'),input=new TestElement('input');input.value='unsent model settings';content.append(input);dom.host.append(content);
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();h.body.scrollTop=123;
  const shell=h.shell as unknown as TestElement;
  expect(shell.querySelector('.capability-controls-surface-switch')).toBe(null); // no "Navigation | Controls" switch
  deck.park('left');expect(h.visible).toBe(false);expect(h.open).toBe(true);expect(h.parked).toBe(true);expect(deck.parkedPage()).toBe(h);expect(revealed).toEqual([]);
  h.show();expect(h.visible).toBe(true);expect(h.parked).toBe(false);expect(h.body.scrollTop).toBe(123);expect(input.value).toBe('unsent model settings');expect(content.parentElement).toBe(h.body);
  // On the right it comes back from the Assistant's chip, not from Tools.
  h.move('right');deck.park('right');expect(deck.parkedRight()).toBe(h);expect(deck.parkedPage()).toBe(undefined);
  modal=true;deck.refresh();expect(h.visible).toBe(false);modal=false;deck.refresh();expect(h.visible).toBe(false);
  scope=['p','other'];deck.refresh();expect(deck.parkedRight()).toBe(undefined);scope=['p','t'];deck.refresh();expect(deck.parkedRight()).toBe(h);
  deck.parkedRight()!.show();expect(h.visible).toBe(true);expect(input.value).toBe('unsent model settings');
  // Only its own × closes it, and hands the side back.
  h.hide();expect(h.open).toBe(false);expect(h.parked).toBe(false);expect(revealed).toEqual(['right']);expect(deck.parkedRight()).toBe(undefined);
  deck.dispose();
 });
 it('drags only while floating: the grip shows then, and a docked page moves from ⋯',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);vi.stubGlobal('window',new EventTarget());
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1500,900],storage:new MapStorage(),profile:'test'});
  const content=new TestElement('div');dom.host.append(content);
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();
  const shell=h.shell as unknown as TestElement,header=shell.querySelector('header')!,grip=shell.querySelector('.capability-controls-grip')!;
  expect(grip.hidden).toBe(true);
  fire(header,'pointerdown',{target:header,button:0,pointerId:3,clientX:200,clientY:100});
  fire(header,'pointermove',{pointerId:3,clientX:700,clientY:300});expect(h.position).toBe('left');expect(header.captured??null).toBe(null);
  fire(header,'pointerup',{pointerId:3,clientX:700,clientY:300});expect(h.position).toBe('left');
  h.move('floating');expect(grip.hidden).toBe(false);
  fire(header,'pointerdown',{target:header,button:0,pointerId:4,clientX:600,clientY:140});
  fire(header,'pointermove',{pointerId:4,clientX:700,clientY:240});fire(header,'pointerup',{pointerId:4,clientX:700,clientY:240});
  expect(h.position).toBe('floating');expect(h.preference.floating.x).toBe(540);deck.dispose();
 });
 it('keeps placement in the header’s ⋯: closed at first, opened and closed by it, width only floating or on the right',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1500,900],storage:new MapStorage(),profile:'test'});
  const content=new TestElement('div');dom.host.append(content);
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();
  const shell=h.shell as unknown as TestElement,menu=shell.querySelector('.capability-controls-menu')!,more=shell.querySelector('.capability-controls-more')!;
  expect(shell.querySelector('.capability-controls-layout')).toBe(null);
  expect(menu.hidden).toBe(true);expect(more.getAttribute('aria-expanded')).toBe('false');expect(more.getAttribute('aria-haspopup')).toBe('menu');
  fire(more,'click');expect(menu.hidden).toBe(false);expect(more.getAttribute('aria-expanded')).toBe('true');
  expect(shell.querySelector('.capability-controls-widths')!.hidden).toBe(true); // a left page takes the Tools drawer's width
  fire(more,'click');expect(menu.hidden).toBe(true);
  h.move('right');expect(shell.querySelector('.capability-controls-widths')!.hidden).toBe(false);
  const wide=shell.querySelectorAll('button').find(b=>b.textContent==='Wide')!;fire(wide,'click');expect(h.preference.width).toBe(640);expect(wide.getAttribute('aria-checked')).toBe('true');
  deck.dispose();
 });
 it('puts “← Tools” on a tool page in every placement, never on an inspector, and parks the page when pressed',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const back=vi.fn();const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1500,900],storage:new MapStorage(),profile:'test',back});
  const content=new TestElement('div');dom.host.append(content);
  const page=deck.attach({id:'action:cells',title:'Outline every cell',content:content as unknown as HTMLElement,rememberOpenPerScope:true});page.show();
  const arrow=(h:typeof page)=>(h.shell as unknown as TestElement).querySelector('.capability-controls-back')!;
  for(const where of ['left','floating','right'] as const){page.move(where);expect(arrow(page).hidden).toBe(false);}
  fire(arrow(page),'click');expect(page.parked).toBe(true);expect(page.visible).toBe(false);expect(back).toHaveBeenCalledWith(page);
  const owner=new TestElement('section'),inner=new TestElement('div');owner.append(inner);dom.host.append(owner);
  const inspector=deck.attach({id:'scientific:1',title:'Cells · revision 2',content:inner as unknown as HTMLElement,owner:owner as unknown as HTMLElement});
  inspector.show();expect(arrow(inspector).hidden).toBe(true);expect(inspector.page).toBe(false);
  expect((inspector.shell as unknown as TestElement).querySelector('.capability-controls-title')!.textContent).toBe('Cells · revision 2');
  deck.dispose();
 });
 it('docks a left page at the Tools drawer’s width, so the Stage does not move, and its resize sets that width',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let tools=420;const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1500,900],insets:()=>({top:48,left:72}),storage:new MapStorage(),profile:'test',
    leftWidth:()=>tools,setLeftWidth:width=>{tools=width;}});
  const content=new TestElement('div');dom.host.append(content);
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();
  expect(h.shell.style).toMatchObject({left:'72px',top:'48px',width:'420px'});
  const resize=(h.shell as unknown as TestElement).querySelector('.capability-controls-resize')!;
  fire(resize,'keydown',{key:'ArrowRight'});expect(tools).toBe(444);expect(h.preference.width).toBe(360);expect(h.shell.style.width).toBe('444px');
  h.move('floating');fire(resize,'keydown',{key:'End'});expect(h.preference.width).toBe(720);expect(tools).toBe(444);
  deck.dispose();
 });
 it('brings a Tab’s left page back on a Tab switch only while the drawer is on Tools; otherwise it waits, parked',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let scope=['p','A'],onTools=true;const shown:string[]=[];
  const deck=new ControlDeck({scope:()=>scope,viewport:()=>[1500,900],storage:new MapStorage(),profile:'test',toolsShowing:()=>onTools,shown:(side,page)=>shown.push(`${side}:${page}`)});
  const content=new TestElement('div');dom.host.append(content);
  const h=deck.attach({id:'action:cells',title:'Outline every cell',content:content as unknown as HTMLElement,rememberOpenPerScope:true});
  h.show();expect(shown).toEqual(['left:true']);deck.refresh();expect(shown).toEqual(['left:true']); // told once
  scope=['p','B'];deck.refresh();expect(h.visible).toBe(false);
  scope=['p','A'];deck.refresh();expect(h.visible).toBe(true);expect(shown).toEqual(['left:true','left:true']);
  scope=['p','B'];deck.refresh();onTools=false;scope=['p','A'];deck.refresh();
  expect(h.visible).toBe(false);expect(h.parked).toBe(true);expect(deck.parkedPage()).toBe(h);
  h.show();expect(h.visible).toBe(true);deck.dispose();
 });
 it('parks a page that keeps the Tab it opened in (Bright objects) when its Tab comes back while the drawer is elsewhere',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let scope=['p','Main'],onTools=true;const shown:string[]=[];
  const deck=new ControlDeck({scope:()=>scope,viewport:()=>[1500,900],storage:new MapStorage(),profile:'test',toolsShowing:()=>onTools,shown:(side,page)=>shown.push(`${side}:${page}`)});
  const content=new TestElement('div');dom.host.append(content);
  // No owner and no rememberOpenPerScope: it stays open in the Tab it was opened in.
  const bright=deck.attach({id:'bright-objects',title:'Bright objects',content:content as unknown as HTMLElement});
  bright.show();expect(bright.visible).toBe(true);
  scope=['p','Tab 2'];deck.refresh();expect(bright.visible).toBe(false);
  onTools=false;scope=['p','Main'];deck.refresh(); // Researchers was opened in Tab 2
  expect(bright.visible).toBe(false);expect(bright.parked).toBe(true);expect(bright.open).toBe(true);expect(deck.parkedPage()).toBe(bright);
  expect(shown).toEqual(['left:true']); // never re-shown, so the drawer did not flip to Tools
  // With the drawer on Tools it comes back with its Tab.
  bright.show();scope=['p','Tab 2'];deck.refresh();onTools=true;scope=['p','Main'];deck.refresh();
  expect(bright.visible).toBe(true);expect(bright.parked).toBe(false);
  // A page floating or on the right is not a page of the drawer: it comes back with its Tab regardless.
  bright.move('floating');scope=['p','Tab 2'];deck.refresh();onTools=false;scope=['p','Main'];deck.refresh();expect(bright.visible).toBe(true);
  deck.dispose();
 });
 it('starts a docked control under the Tab row and the Stage bar when its side floats over the Stage',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  let over=false;const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1100,800],storage:new MapStorage(),profile:'test',
    insets:floating=>floating?{top:130,left:72,bottom:40}:{top:48,left:72},occupy:()=>({left:false,right:over})});
  const content=new TestElement('div');dom.host.append(content);
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();h.move('right');
  expect(h.shell.style).toMatchObject({top:'48px',height:'752px'});
  over=true;deck.refresh();expect(h.shell.style).toMatchObject({top:'130px',height:'630px'});
  deck.dispose();
 });
 it('coming back unparks the other pages open on its side, so the switcher still lists every open page',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1500,900],storage:new MapStorage(),profile:'test'});
  const add=(id:string)=>{const content=new TestElement('div');dom.host.append(content);return deck.attach({id,title:id,content:content as unknown as HTMLElement});};
  const a=add('a'),b=add('b');a.show();b.show();deck.park('left');expect(a.parked&&b.parked).toBe(true);
  b.show();expect(b.visible).toBe(true);expect(a.parked).toBe(false);
  expect([...b.pages.querySelectorAll('button')].map(o=>o.dataset.page)).toEqual(['a','b']);expect(b.pages.hidden).toBe(false);
  deck.dispose();
 });
 it('closing a docked page with nothing it came from in view sends focus to the side’s rail item',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const focusBase=vi.fn();const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1500,900],storage:new MapStorage(),profile:'test',focusBase});
  const content=new TestElement('div');dom.host.append(content);
  const h=deck.attach({id:'tool',title:'Tool',content:content as unknown as HTMLElement});h.show();
  Object.assign(h.launcher,{isConnected:true,checkVisibility:()=>false});
  h.hide();expect(focusBase).toHaveBeenCalledWith('left');deck.dispose();
 });
 it('explicit tool selection activates only its visible owner and preserves both drafts',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1100,900],storage:new MapStorage(),profile:'isolated'});
  const add=(id:string)=>{const content=new TestElement('div'),input=new TestElement('input');input.value=`${id} draft`;content.append(input);dom.host.append(content);
    const activate=vi.fn();return {activate,input,handle:deck.attach({id,title:id,content:content as unknown as HTMLElement,activate})};};
  const a=add('segmentation'),b=add('animation');a.handle.show(false);b.handle.show(false);
  expect(a.activate).not.toHaveBeenCalled();expect(b.activate).not.toHaveBeenCalled();
  fire([...b.handle.pages.querySelectorAll('button')].find(o=>o.dataset.page==='segmentation') as unknown as TestElement,'click');
  expect(a.handle.visible).toBe(true);expect(b.handle.visible).toBe(false);
  expect(a.activate).toHaveBeenCalledOnce();expect(b.activate).not.toHaveBeenCalled();
  expect(a.input.value).toBe('segmentation draft');expect(b.input.value).toBe('animation draft');deck.dispose();
 });
 it('shows a compact title for one tool and an in-header switcher for multiple open tools',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('HTMLElement',TestElement);
  const deck=new ControlDeck({scope:()=>['p','t'],viewport:()=>[1400,900],storage:new MapStorage(),profile:'test'});
  const add=(id:string)=>{const content=new TestElement('div');dom.host.append(content);return deck.attach({id,title:id,content:content as unknown as HTMLElement});};
  const a=add('a');a.show();expect(a.pages.hidden).toBe(true);
  const b=add('b');b.show();expect(b.pages.hidden).toBe(false);expect((b.pages.parentElement as unknown as TestElement)?.className).toBe('capability-controls-menu');
  expect((b.shell as unknown as TestElement).querySelector('.capability-controls-title')?.hidden).toBeFalsy(); // never a select in the header
  expect((b.shell as unknown as TestElement).querySelector('select')).toBeNull();
  expect([...b.pages.querySelectorAll('button')].map(o=>[o.dataset.page,o.getAttribute('role'),o.getAttribute('aria-checked')])).toEqual([['a','menuitemradio','false'],['b','menuitemradio','true']]);
  b.move('right');expect(a.pages.hidden).toBe(true);expect(b.pages.hidden).toBe(true);deck.dispose();
 });

});
class MapStorage {private data=new Map<string,string>();getItem(k:string){return this.data.get(k)??null;}setItem(k:string,v:string){this.data.set(k,v);}}


it('keeps both compact panels outside the canvas when a 1100px workspace has room',()=>{
 const beside={overLeft:false,overRight:false};
 expect(controlStageInsets(1100,360,null,64,360)).toEqual({left:360,right:360,...beside});
 expect(controlStageInsets(1100,null,360,360,360)).toEqual({left:360,right:360,...beside});
 expect(controlStageInsets(1440,480,null,424,360)).toEqual({left:480,right:360,...beside});
 expect(controlStageInsets(900,360,null,64,0)).toEqual({left:360,right:0,...beside});
 // User-selected wide panels and phone-size windows retain the recoverable overlay fallback; the Stage still starts
 // after the rail, since a left page does (it never slides under the rail). What is not reserved floats over the Stage.
 expect(controlStageInsets(1100,640,null,64,360)).toEqual({left:64,right:0,overLeft:true,overRight:true});
 expect(controlStageInsets(640,360,null,72,360,72)).toEqual({left:72,right:0,overLeft:true,overRight:true});
});
it('says when the drawer floats over the Stage: below 1200 px with a page docked right too (R-16), never at 1440',()=>{
 // 1100 px: the rail (72), the drawer (360) and a page docked right (360) leave the Stage under 320 px. The Stage starts
 // after the rail, so the drawer and the right page float over it under the Tab row and the Stage bar.
 expect(controlStageInsets(1100,null,360,72+360,0,72)).toEqual({left:72,right:0,overLeft:true,overRight:true});
 // At the mock's 320 px below 1200 px they all fit beside a 348 px Stage.
 expect(controlStageInsets(1100,null,360,72+320,0,72)).toEqual({left:392,right:360,overLeft:false,overRight:false});
 // Nothing on the right: the drawer sits beside the Stage, so the Tab row starts after it.
 expect(controlStageInsets(1100,null,null,72+340,0,72)).toMatchObject({left:412,overLeft:false});
 // 1440 px, the drawer and the Assistant: beside. A crowded wide window keeps the drawer beside (the Stage narrows),
 // while a left page that cannot be reserved floats under the Stage bar.
 expect(controlStageInsets(1440,null,null,72+340,360,72)).toMatchObject({left:412,right:360,overLeft:false,overRight:false});
 expect(controlStageInsets(1280,null,null,72+720,520,72)).toMatchObject({left:792,overLeft:false});
 expect(controlStageInsets(1440,72+720,null,72+340,360,72)).toMatchObject({left:72,overLeft:true,overRight:false});
});

it('the Stage’s place beside a docked page is not !important, so the side motion’s animations can move it (b041)', async () => {
  const { readFileSync } = await import('node:fs');
  const css = readFileSync(new URL('./capability-controls.css', import.meta.url), 'utf8');
  const rule = css.split('\n').find((line) => /data-control-left-active[^{]*\.studio\{/.test(line));
  expect(rule).toBeTruthy();
  expect(rule).toMatch(/^html body/); // outranks the floating column's rule (workspace-feel.css) by specificity instead
  expect(rule).not.toContain('!important');
});
