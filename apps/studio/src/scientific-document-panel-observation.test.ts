// Synthetic DOM/transport regression with the real document, native panel and paint admission.
// No Qt callback, browser layout or installed-app qualification is claimed.
import {beforeEach, afterEach, expect, test, vi} from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import {TestElement, fire, settleMicrotasks, syntheticDOM} from './workspace-windows.test-support';
import {scientificPresentation} from './scientific-presentation';
import type {StudyRetainedView} from './workspace-study-restoration';

const fixture=vi.hoisted(()=>({root:null as unknown as TestElement,dispose:()=>{}}));
vi.mock('./workspace-study-artifacts',()=>({studyArtifacts:()=>({
  document:(_key:string,_title:string,content:TestElement,dispose:()=>void,metadata:Record<string,string>)=>{
    Object.assign(fixture.root.dataset,metadata);fixture.root.append(content);fixture.dispose=dispose;return fixture.root;
  },restoreGeometry:()=>{},documentTitle:()=>{},
})}));
vi.mock('./sheet',()=>({StudioSheet:class {onDismiss=()=>{};open(){} close(){}}}));
import {configureScientificDocuments,openStudyScientific} from './scientific-document';

class Canvas extends TestElement {
  constructor(){super('canvas');}
  width=0;height=0;isConnected=true;
  getContext(){return {createImageData:(w:number,h:number)=>({data:new Uint8ClampedArray(w*h*4)}),putImageData:()=>{}};}
  hasPointerCapture(id:number){return this.captured===id;}
  focus(){}
}
const saved:StudyRetainedView={resource:{day:'origin',id:'p'+'a'.repeat(32),version:2,picture:null},title:'Native input',zoom:1,pan:[.5,.5],placementId:'placement'};
const review='review-1234567890123456';
const panelFrame={format:'scientific-native-panel-v1',panel:'a'.repeat(32),frame:'b'.repeat(64),width:32,height:24,pixelRatio:1,focus:null,
  accessibility:[{id:'1',parent:null,role:'Client',name:'Tool',description:'',value:'',rect:[0,0,32,24],disabled:false,focused:false,focusable:false,invisible:false,checked:false,checkable:false,readOnly:false}]};
const reply=(value:object)=>new Response(JSON.stringify({ok:true,...value}),{headers:{'content-type':'application/json'}});
const body=(init?:RequestInit)=>JSON.parse(String(init?.body));
const button=(label:string)=>fixture.root.querySelectorAll('button').find(item=>item.textContent===label)!;
let frames:Map<number,FrameRequestCallback>,serial:number;
let observe=vi.fn<()=>Promise<Response>>(),request=vi.fn<(path:string,init?:RequestInit)=>Promise<Response>>();
async function settle(){for(let i=0;i<12;i++)await settleMicrotasks();}
function paint(){const work=[...frames.values()];frames.clear();work.forEach(callback=>callback(0));}
function pending<T>(){let resolve!:(value:T)=>void;const promise=new Promise<T>(done=>{resolve=done;});return {promise,resolve};}
function changes(){return request.mock.calls.filter(([path,init])=>path.endsWith('/request')&&body(init).request.op==='native-panel-change');}
function click(canvas:TestElement){
  const rect=canvas.getBoundingClientRect();const fields={pointerId:1,clientX:rect.left+10,clientY:rect.top+10,button:0};
  fire(canvas,'pointerdown',fields);fire(canvas,'pointerup',fields);
}

beforeEach(()=>{
  vi.useFakeTimers();frames=new Map();serial=0;
  const dom=syntheticDOM();fixture.root=Object.assign(new TestElement('section'),{isConnected:true});dom.host.append(fixture.root);
  Object.assign(fixture.root.dataset,{project:'origin',workspaceView:'tab-one',placementId:'placement'});dom.host.dataset.activeWorkspaceView='tab-one';
  Object.assign(dom.document,{visibilityState:'visible',createElement:(tag:string)=>tag==='canvas'?new Canvas():Object.assign(new TestElement(tag),{focus:()=>{}})});
  vi.stubGlobal('document',dom.document);vi.stubGlobal('window',new EventTarget());
  vi.stubGlobal('getComputedStyle',(element:TestElement)=>({opacity:'1',transform:'none',...element.style}));
  vi.stubGlobal('MutationObserver',class {observe(){} disconnect(){} takeRecords(){return [];}});
  vi.stubGlobal('requestAnimationFrame',(callback:FrameRequestCallback)=>{frames.set(++serial,callback);return serial;});
  vi.stubGlobal('cancelAnimationFrame',(id:number)=>frames.delete(id));
  vi.stubGlobal('fetch',vi.fn(async()=>reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',state:{scientificView:{}}}}})));
  observe=vi.fn(async()=>reply({imageFrame:cases.valid.frame}));
  request=vi.fn(async(path,init)=>{
    if(path.includes('/frames/')||path.includes('/native-panels/'))return new Response(new Uint8Array(32*24*4));
    if(init?.method==='DELETE')return reply({});
    if(path.endsWith('/request')){
      const command=body(init).request;
      if(command.op==='observe')return observe();
      if(command.op==='native-panel-render')return reply({nativePanel:panelFrame,imageFrame:cases.valid.frame});
      if(command.op==='native-panel-change')return reply({nativeChanged:true});
      return reply({frame:{...cases.valid,nativePanelAvailable:true}});
    }
    if(init?.method==='POST')return reply({review});
    return reply({reviews:[]});
  });
  configureScientificDocuments({prepare:async()=>{},transport:()=>({request,check:()=>{}})});
});
afterEach(()=>{fixture.dispose();vi.useRealTimers();vi.unstubAllGlobals();});

async function openedPanel(){
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();paint();await settle();
  fire(button('Tool'),'click');fire(button('Open plugin panel'),'click');await settle();paint();await settle();
  const canvas=fixture.root.querySelectorAll('canvas').find(item=>item.getAttribute('aria-label')?.startsWith('Interactive plugin controls.'))!;
  expect(button('Type in selected field').disabled).toBe(false);
  const receipt=scientificPresentation(fixture.root as unknown as HTMLElement)!.panelPaint!;
  expect(receipt).not.toBeNull();
  return {canvas,receipt};
}

test('a painted plugin click waits for background observation and executes once with its captured paint',async()=>{
  const {canvas,receipt}=await openedPanel(),waiting=pending<Response>();observe.mockReturnValueOnce(waiting.promise);
  await vi.advanceTimersByTimeAsync(1500);expect(observe).toHaveBeenCalledTimes(1);
  click(canvas);await settle();expect(changes()).toHaveLength(0);
  waiting.resolve(reply({imageFrame:cases.valid.frame}));await settle();paint();await settle();
  expect(changes()).toHaveLength(1);
  expect(body(changes()[0][1])).toEqual({request:{op:'native-panel-change',request:{panel:panelFrame.panel,frame:panelFrame.frame,op:'click',value:[1,1]}},draw:receipt.draw});
  await vi.advanceTimersByTimeAsync(3000);await settle();expect(changes()).toHaveLength(1);
});

test('stopping a session cancels its queued plugin click before opening a replacement',async()=>{
  const {canvas}=await openedPanel(),waiting=pending<Response>();observe.mockReturnValueOnce(waiting.promise);
  await vi.advanceTimersByTimeAsync(1500);click(canvas);await settle();expect(changes()).toHaveLength(0);
  fire(button('Stop scientific session'),'click');await settle();
  waiting.resolve(reply({imageFrame:cases.valid.frame}));await settle();paint();await settle();
  expect(changes()).toHaveLength(0);
  const original=request.getMockImplementation()!;
  request.mockImplementation((path,init)=>path==='/api/scientific-reviews'&&init?.method==='POST'
    ?Promise.resolve(reply({review:'replacement-1234567890123456'})):original(path,init));
  fire(button('Open session'),'click');await settle();paint();await settle();
  expect(request.mock.calls.filter(([path,init])=>path==='/api/scientific-reviews'&&init?.method==='POST')).toHaveLength(2);
  expect(changes()).toHaveLength(0);
});

test('Stop cannot open a replacement while the old observation still owns the request',async()=>{
  const {canvas}=await openedPanel(),waiting=pending<Response>();observe.mockReturnValueOnce(waiting.promise);
  await vi.advanceTimersByTimeAsync(1500);click(canvas);await settle();
  fire(button('Stop scientific session'),'click');await settle();
  const original=request.getMockImplementation()!;
  request.mockImplementation((path,init)=>path==='/api/scientific-reviews'&&init?.method==='POST'
    ?Promise.resolve(reply({review:'replacement-1234567890123456'})):original(path,init));
  expect(button('Open session').disabled).toBe(true);
  // Even a delivered synthetic click cannot bypass startView's in-flight guard.
  fire(button('Open session'),'click');await settle();
  expect(request.mock.calls.filter(([path,init])=>path==='/api/scientific-reviews'&&init?.method==='POST')).toHaveLength(1);
  expect(changes()).toHaveLength(0);
  waiting.resolve(reply({imageFrame:cases.valid.frame}));await settle();paint();await settle();
  expect(changes()).toHaveLength(0);
  expect(button('Open session').disabled).toBe(false);
  fire(button('Open session'),'click');await settle();paint();await settle();
  expect(request.mock.calls.filter(([path,init])=>path==='/api/scientific-reviews'&&init?.method==='POST')).toHaveLength(2);
  expect(changes()).toHaveLength(0);
});

test.each(['project','workspaceView','placementId','closed'])('a queued plugin click refuses changed %s ownership',async changed=>{
  const {canvas}=await openedPanel(),waiting=pending<Response>();observe.mockReturnValueOnce(waiting.promise);
  await vi.advanceTimersByTimeAsync(1500);click(canvas);await settle();
  if(changed==='closed')fixture.dispose();else fixture.root.dataset[changed]='another-owner';
  waiting.resolve(reply({imageFrame:cases.valid.frame}));await settle();paint();await settle();
  expect(changes()).toHaveLength(0);
});

test('repeated pointer events while the first plugin click waits never queue another execution',async()=>{
  const {canvas}=await openedPanel(),waiting=pending<Response>();observe.mockReturnValueOnce(waiting.promise);
  await vi.advanceTimersByTimeAsync(1500);click(canvas);click(canvas);await settle();
  expect(changes()).toHaveLength(0);
  waiting.resolve(reply({imageFrame:cases.valid.frame}));await settle();paint();await settle();
  expect(changes()).toHaveLength(1);
});

test('a foreground render still blocks plugin gestures',async()=>{
  const {canvas}=await openedPanel(),waiting=pending<Response>(),original=request.getMockImplementation()!;
  request.mockImplementation((path,init)=>path.endsWith('/request')&&body(init).request.op==='render'?waiting.promise:original(path,init));
  fire(button('Refresh preview'),'click');await settle();click(canvas);await settle();
  waiting.resolve(reply({frame:{...cases.valid,nativePanelAvailable:true}}));await settle();paint();await settle();
  expect(changes()).toHaveLength(0);
});
