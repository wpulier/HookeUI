import {afterEach,expect,test,vi} from 'vitest';
const artifacts=vi.hoisted(()=>({blockReason:vi.fn(()=>null),removeDocument:vi.fn(),applyDocument:vi.fn(),document:vi.fn(),clearDocuments:vi.fn()}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>artifacts}));
import {WorkspaceViews} from './workspace-views';
import {FloatingWindow,PanelStack} from './panels';
import {floatingSpecimen} from './workspace-views-model';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());

/** Main (home) and a work Tab, each with its specimen as the backdrop unless said. */
function tabs(main={visible:true,rect:null as number[]|null},work={visible:true,rect:null as number[]|null}){
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const el=(tag:string)=>new TestElement(tag) as unknown as HTMLElement;
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',
  layout:{active:'main',views:[{id:'main',title:'Main',specimen:{...main},items:[],notes:[]},{id:'w',title:'Counting',specimen:{...work},items:[],notes:[]}]},
  specimen:el('section'),specimenSize:el('button'),specimenHide:el('button'),specimenReturn:el('div'),stageSize:el('button'),stageHide:el('button'),stageWork:el('div'),
  specimenShell:{resizeTo:vi.fn(),moveTo:vi.fn()},changed:vi.fn()});
 const show=(id:string)=>{current.layout.active=id;Reflect.get(WorkspaceViews.prototype,'applySpecimen').call(current);};
 const view=(id:string)=>current.layout.views.find((v:{id:string})=>v.id===id);
 return {current,show,view};
}

test('Main always shows the specimen: a saved layout that hid it is shown again, as the backdrop',()=>{
 const t=tabs({visible:false,rect:[1,2,3,4]});t.show('main');
 expect(t.view('main').specimen).toEqual({visible:true,rect:null});expect(t.current.specimen.hidden).toBe(false);
 expect(t.current.changed).toHaveBeenCalled();
});

test('Main offers no way to hide its specimen; a work Tab does, and keeps a way back when hidden',()=>{
 const t=tabs();t.show('main');
 expect(t.current.specimenHide.hidden).toBe(true);expect(t.current.specimenReturn.hidden).toBe(true);
 t.show('w');expect(t.current.specimenHide.hidden).toBe(false);expect(t.current.specimenReturn.hidden).toBe(true);
 t.view('w').specimen.visible=false;t.show('w');
 expect(t.current.specimen.hidden).toBe(true);expect(t.current.specimenReturn.hidden).toBe(false);
});

test('showing a hidden specimen brings it back as the backdrop, not a small window',()=>{
 const t=tabs(undefined,{visible:false,rect:[12,40,480,380]});t.show('w');
 Reflect.get(WorkspaceViews.prototype,'showSpecimen').call(Object.assign(t.current,{active:()=>t.view('w')}));
 expect(t.view('w').specimen).toEqual({visible:true,rect:null});expect(t.current.specimenReturn.hidden).toBe(true);
});

test('the size control says what it will do: Float from the backdrop, Backdrop from a window',()=>{
 const t=tabs(undefined,{visible:true,rect:[12,40,480,380]});t.show('main');
 expect(t.current.specimenSize.textContent).toBe('Float specimen');
 t.show('w');expect(t.current.specimenSize.textContent).toBe('Fill Tab');
});

test('Main is never closed or removed',async()=>{
 const t=tabs();const request=vi.fn();Object.assign(t.current,{openIds:['main','w'],changing:false,navigating:false,openingInput:false,capture:vi.fn(),flush:vi.fn(),opts:{request}});
 await Reflect.get(WorkspaceViews.prototype,'closeTab').call(t.current,'main');expect(t.current.openIds).toEqual(['main','w']);expect(t.current.capture).not.toHaveBeenCalled();
 await Reflect.get(WorkspaceViews.prototype,'arrangement').call(t.current,'remove','main');expect(request).not.toHaveBeenCalled();
});

test('the Stage bar names the specimen with the same Float or Fill and Hide, or says it is hidden with the way back',()=>{
 const t=tabs(undefined,{visible:true,rect:[12,40,480,380]});t.show('main');
 expect(t.current.stageSize.textContent).toBe('Float');expect(t.current.stageHide.hidden).toBe(true);expect(t.current.stageWork.dataset.specimen).toBe('shown');
 t.show('w');expect(t.current.stageSize.textContent).toBe('Fill');expect(t.current.stageHide.hidden).toBe(false);
 t.view('w').specimen.visible=false;t.show('w');expect(t.current.stageWork.dataset.specimen).toBe('hidden');expect(t.current.specimenReturn.hidden).toBe(false);
});
test('the Stage bar says the specimen\'s name, its credit quieter, and its picture, writing only when they change',()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const el=(tag:string)=>new TestElement(tag);
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{labelKey:'',stageWork:Object.assign(el('div'),{}),stageName:el('span'),stageCredit:el('span'),stageThumb:el('span')});
 current.stageWork.dataset.heard='false'; // as the constructor leaves it
 // Before the bridge's first report: no words, nothing to claim there is no specimen, the left out of layout.
 expect(current.stageName.textContent).toBe('');expect(current.stageCredit.textContent).toBe('');expect(current.stageWork.dataset.heard).toBe('false');
 Reflect.get(WorkspaceViews.prototype,'specimenLabel').call(current,'Human cells · David Root (CC0)','/t.png');
 expect(current.stageWork.dataset.heard).toBe('true'); // heard: name, credit and picture appear together
 expect(current.stageThumb.title).toBe('Human cells · David Root (CC0)');
 expect(current.stageName.textContent).toBe('Human cells');expect(current.stageCredit.textContent).toBe('David Root (CC0)');expect(current.stageCredit.hidden).toBe(false);
 expect(current.stageThumb.style.backgroundImage).toBe('url("/t.png")');expect(current.stageThumb.hidden).toBe(false);
 Reflect.get(WorkspaceViews.prototype,'specimenLabel').call(current,'Live camera','');
 expect(current.stageName.textContent).toBe('Live camera');expect(current.stageCredit.hidden).toBe(true);expect(current.stageThumb.hidden).toBe(true);
});

test('the details card opens in front of the main feed\'s window, and stays above it after another window is pressed (Will, 2026-10-29)',async()=>{
 const {readFileSync}=await import('node:fs');const dir=new URL('.',import.meta.url).pathname;
 const rules=(file:string)=>[...readFileSync(dir+file,'utf8').replace(/\/\*[\s\S]*?\*\//g,'').matchAll(/([^{}]+)\{([^{}]*)\}/g)].map(([,selector,body])=>({selector:selector.trim(),z:Number(/z-index:\s*(\d+)/.exec(body)?.[1])}));
 const level=(file:string,selector:string)=>rules(file).find(rule=>rule.selector===selector)?.z;
 // The main feed's window as the workspace makes it: the real FloatingWindow in its own stack group, fronted on open.
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const root=new TestElement('section'),bar=new TestElement('header');root.append(bar);const panels=new PanelStack();
 const specimen=panels.register(new FloatingWindow({id:'workspace-specimen',stackGroup:'specimen',root:root as unknown as HTMLElement,bar:bar as unknown as HTMLElement,
  panels,close:()=>{},facts:()=>null,content:()=>({kind:'text'})})).front();
 const floated=level('workspace-views.css','.workspace-specimen[data-front=true]:not(.workspace-specimen-full)')!,windows=level('workspace-study-v2.css','#study-artifacts')!;
 expect(level('style.css','.description')).toBeLessThan(specimen); // the card's own level, which left it behind the feed
 // The workspace's levels for the card: one while the specimen was pressed last (how the card opens), one after.
 const card=rules('workspace-views.css').filter(rule=>/(^|[\s>~)])#description$/.test(rule.selector));
 const front=card.find(rule=>rule.selector.includes('.workspace-specimen[data-front=true]'))?.z,back=card.find(rule=>!rule.selector.includes('data-front'))?.z;
 expect(front).toBeGreaterThan(Math.max(specimen,floated,windows));
 // Level with the specimen, the card is above it: the specimen is the stage's first child, the card later in the stage.
 expect(back).toBeGreaterThanOrEqual(specimen);expect(back).toBeLessThan(windows);
 const html=readFileSync(dir+'../index.html','utf8'),stage=html.indexOf('id="stage"');
 expect(html.indexOf('id="description"')).toBeGreaterThan(stage);expect(html.indexOf('id="description"')).toBeLessThan(html.indexOf('</main>',stage));
});

// The specimen is part of what Hooke knows is on screen and can change (docs/architecture/on-screen.md "A Tab's
// specimen"). Live, 29 Oct: "only have the image open in the Stoma Reference tab" left the specimen filling that Tab
// behind the image, and the Assistant said the Tab held only the image.
/** The Assistant's Tab commands on Main and a work Tab, through the same specimen code as the Tab's own buttons. */
function agent(work={visible:true,rect:null as number[]|null},active='w'){
 vi.stubGlobal('document',{hidden:false,querySelector:()=>null,body:{dataset:{}}});
 const el=(tag:string)=>new TestElement(tag) as unknown as HTMLElement;
 const stage=Object.assign(el('div'),{clientWidth:1200,clientHeight:800});
 const current=Object.assign(Object.create(WorkspaceViews.prototype),{session:'p',generation:1,
  layout:{schema:6,active,views:[{id:'main',title:'Main',specimen:{visible:true,rect:null},items:[],notes:[]},{id:'w',title:'Stoma Reference',specimen:{...work},items:[],notes:[]}]},
  openIds:['main','w'],loaded:new Set(['main','w']),completionRestores:new Set(),dirty:false,conflict:false,changing:false,navigating:false,openingInput:false,
  stage,specimen:el('section'),specimenSize:el('button'),specimenHide:el('button'),specimenReturn:el('div'),stageSize:el('button'),stageHide:el('button'),stageWork:el('div'),
  specimenShell:{resizeTo:vi.fn(),moveTo:vi.fn()},capture:vi.fn(),changed:vi.fn(),render:vi.fn(),flush:vi.fn(async()=>{}),roots:()=>[],opts:{notice:vi.fn()}});
 const view=(id:string)=>current.layout.views.find((v:{id:string})=>v.id===id);
 return {current,view,stage};
}

test('a new Tab opens as a canvas for data, with no specimen, made by "+" or by the Assistant alike (Will, 29 Oct)',async()=>{
 const t=agent();Object.assign(t.current,{activate:vi.fn(async()=>true),opts:{notice:vi.fn(),captureInput:()=>null}});
 const result=await t.current.agentCommand('p',{action:'create',title:'Reference'});
 expect(result.ok).toBe(true);
 const made=t.current.layout.views[2];
 expect(made.specimen).toEqual({visible:false,rect:null});
 expect(result.tabs.find((tab:{id:string})=>tab.id===made.id).specimen).toBe('hidden');
 // A run's result Tab draws on the specimen: it is made with its specimen filling, as before.
 const id=await Reflect.get(WorkspaceViews.prototype,'addView').call(t.current,'Results',false,{visible:true,rect:null});
 expect(t.current.layout.views.find((v:{id:string})=>v.id===id).specimen).toEqual({visible:true,rect:null});
});

test('the Assistant floats, fills and hides a Tab\'s specimen through the code its Float and Hide buttons run',async()=>{
 const t=agent();const set=vi.spyOn(WorkspaceViews.prototype as unknown as {setSpecimen:()=>void},'setSpecimen');
 let result=await t.current.agentCommand('p',{action:'specimen',tab:'w',specimen:'hide'});
 expect(result.ok).toBe(true);expect(t.view('w').specimen.visible).toBe(false);expect(t.current.specimen.hidden).toBe(true);
 expect(result.tabs.find((tab:{id:string})=>tab.id==='w').specimen).toBe('hidden');
 result=await t.current.agentCommand('p',{action:'specimen',tab:'w',specimen:'float'});
 expect(t.view('w').specimen).toEqual({visible:true,rect:floatingSpecimen(1200,800)});expect(t.current.specimen.hidden).toBe(false);
 expect(t.current.specimen.classList.contains('workspace-specimen-full')).toBe(false);expect(result.tabs.find((tab:{id:string})=>tab.id==='w').specimen).toBe('float');
 result=await t.current.agentCommand('p',{action:'specimen',tab:'w',specimen:'fill'});
 expect(t.view('w').specimen).toEqual({visible:true,rect:null});expect(t.current.specimen.classList.contains('workspace-specimen-full')).toBe(true);
 expect(set).toHaveBeenCalledTimes(3);expect(t.current.flush).toHaveBeenCalled();
 set.mockRestore();
});

test('Main never hides its specimen, for the Assistant either: it floats or fills',async()=>{
 const t=agent(undefined,'main');
 const refused=await t.current.agentCommand('p',{action:'specimen',tab:'main',specimen:'hide'});
 expect(refused).toEqual({ok:false,error:'Main always keeps the specimen: it can float, never hide.'});
 expect(t.view('main').specimen).toEqual({visible:true,rect:null});expect(t.current.specimen.hidden).toBe(false);
 const floated=await t.current.agentCommand('p',{action:'specimen',tab:'main',specimen:'float'});
 expect(floated.ok).toBe(true);expect(t.view('main').specimen.rect).toEqual(floatingSpecimen(1200,800));
 expect((await t.current.agentCommand('p',{action:'specimen',tab:'main',specimen:'gone'})).ok).toBe(false);
});

test('a Tab not in front keeps the change in its arrangement, and the Stage it is not on is left as it is',async()=>{
 const t=agent(undefined,'main');
 const result=await t.current.agentCommand('p',{action:'specimen',tab:'w',specimen:'hide'});
 expect(result.ok).toBe(true);expect(t.view('w').specimen.visible).toBe(false);expect(t.current.specimen.hidden).toBe(false);
});

test('the screen report says each Tab\'s specimen: the one in front as the Stage shows it, the others as kept',()=>{
 const t=agent({visible:true,rect:[16,560,240,216]},'main');
 Object.assign(t.stage.style,{left:'200px',top:'80px'});
 Reflect.get(WorkspaceViews.prototype,'applySpecimen').call(t.current);
 expect(t.current.specimenReport()).toEqual({main:{state:'fill'},w:{state:'float',rect:[216,640,240,216]}});
 t.current.layout.active='w';Reflect.get(WorkspaceViews.prototype,'applySpecimen').call(t.current);
 Object.assign(t.current.specimen.style,{width:'240px',height:'216px',left:'20px',top:'30px'});
 expect(t.current.specimenReport().w).toEqual({state:'float',rect:[20,30,240,216]}); // as laid out, in workspace px
 t.view('w').specimen.visible=false;Reflect.get(WorkspaceViews.prototype,'applySpecimen').call(t.current);
 expect(t.current.specimenReport()).toEqual({main:{state:'fill'},w:{state:'hidden'}});
});
