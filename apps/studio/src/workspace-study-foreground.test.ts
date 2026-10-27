// Synthetic restoration/window-stack regression; browser evidence verifies actual stacking.
import {afterEach,expect,test,vi} from 'vitest';
import {StudyArtifacts} from './workspace-study-artifacts';
import {restoreStudyItem,type StudyRetainedView} from './workspace-study-restoration';
import {PanelStack} from './panels';
import {TestElement,syntheticDOM} from './workspace-windows.test-support';

afterEach(()=>{vi.restoreAllMocks();vi.unstubAllGlobals();});
test('late restored documents cannot cover a newer Reveal or reopen a dismissed document',async()=>{
 const dom=syntheticDOM(),events=new EventTarget();let focused:unknown=null;
 const original=dom.document.createElement;
 const document=Object.assign(dom.document,{getElementById:()=>dom.host,
  createElement:(tag:string)=>Object.assign(original(tag),{focus(){focused=this;}})});
 const query=TestElement.prototype.querySelector;
 vi.spyOn(TestElement.prototype,'querySelector').mockImplementation(function(this:TestElement,selector:string){
  return query.call(this,selector===':scope > header'?'header':selector);
 });
 vi.stubGlobal('document',document);vi.stubGlobal('window',Object.assign(events,{innerWidth:1200,innerHeight:800}));
 vi.stubGlobal('MutationObserver',class{observe(){}});vi.stubGlobal('requestAnimationFrame',dom.raf);
 const artifacts=new StudyArtifacts(),panels=new PanelStack();artifacts.configure(panels,()=>null);
 const retained=(id:string):StudyRetainedView=>({resource:{day:'origin',id:'p'+id.repeat(32),version:1,picture:null},title:id,placementId:id,zoom:1,pan:[.5,.5]});
 const open=(item:StudyRetainedView,...options:unknown[])=>{
  // Use the real document registration and native FloatingWindow stack.
  return Reflect.apply(artifacts.document,artifacts,[item.resource.id,item.title,document.createElement('section'),()=>{},
   {workspaceView:'main',project:'origin',placementId:item.placementId,studyRetained:JSON.stringify(item)},...options]);
 };
 const specimenRoot=document.createElement('section'),specimenBar=document.createElement('header');specimenRoot.append(specimenBar);dom.host.append(specimenRoot);
 const specimen=artifacts.specimen(specimenRoot as unknown as HTMLElement,specimenBar as unknown as HTMLElement,()=>{});specimen.frame();
 const selected=open(retained('a'));
 expect(artifacts.applyDocument('document:a',{open:true,front:true})).toBe(true);
 const selectedPanel=panels.top(),selectedFocus=focused;
 await restoreStudyItem(retained('b'),{scientific:(...args)=>{open(...args);},text:vi.fn(),table:vi.fn(),capture:vi.fn(),masks:vi.fn(),nativeReady:async()=>{},native:async()=>{}});
 panels.start(dom.host as unknown as HTMLElement,()=>{});dom.frame();
 expect(panels.top()?.id).toBe(selectedPanel?.id);
 expect(focused).toBe(selectedFocus);
 for(let i=0;i<12;i++)await restoreStudyItem(retained(String(i)),{scientific:(...args)=>{open(...args);},text:vi.fn(),table:vi.fn(),capture:vi.fn(),masks:vi.fn(),nativeReady:async()=>{},native:async()=>{}});
 specimen.front();
 // The specimen is a sibling of the artifacts stacking context (z=35), not one of its child windows.
 expect(Number(specimenRoot.style.zIndex)).toBeLessThan(35);
 const restored=artifacts.root.children[1] as HTMLElement;
 const lastRestored=artifacts.root.children[artifacts.root.children.length-1] as HTMLElement;
 expect(Number(lastRestored.style.zIndex)).toBeGreaterThan(Number(restored.style.zIndex));
 expect(Number(lastRestored.style.zIndex)).toBeLessThan(Number(selected.style.zIndex));
 expect(Number(restored.style.zIndex)).toBeLessThan(Number(selected.style.zIndex));
 // A restoration retry must not reopen a window the user explicitly dismissed.
 restored.hidden=true;restored.dataset.dismissed='true';
 await restoreStudyItem(retained('b'),{scientific:(...args)=>{open(...args);},text:vi.fn(),table:vi.fn(),capture:vi.fn(),masks:vi.fn(),nativeReady:async()=>{},native:async()=>{}});
 expect(restored.hidden).toBe(true);expect(restored.dataset.dismissed).toBe('true');expect(panels.top()?.id).toBe(selectedPanel?.id);
 // An explicit Reveal still reopens and raises that exact placement.
 artifacts.applyDocument('document:b',{open:true,front:true});
 expect(restored.hidden).toBe(false);expect(panels.top()?.id).not.toBe(selectedPanel?.id);
 expect(Number(restored.style.zIndex)).toBeGreaterThan(Number(selected.style.zIndex));
 // Later background arrivals keep both explicit selections above the restored sequence.
 await restoreStudyItem(retained('f'),{scientific:(...args)=>{open(...args);},text:vi.fn(),table:vi.fn(),capture:vi.fn(),masks:vi.fn(),nativeReady:async()=>{},native:async()=>{}});
 const late=artifacts.root.children[artifacts.root.children.length-1] as HTMLElement;
 expect(Number(late.style.zIndex)).toBeLessThan(Number(selected.style.zIndex));
 expect(Number(late.style.zIndex)).toBeGreaterThan(Number(lastRestored.style.zIndex));
});
