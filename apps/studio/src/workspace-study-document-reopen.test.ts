// Synthetic document lifecycle evidence; browser acceptance checks saved geometry.
import {afterEach,expect,test,vi} from 'vitest';
import {StudyArtifacts} from './workspace-study-artifacts';
import {syntheticDOM} from './workspace-windows.test-support';

afterEach(()=>vi.unstubAllGlobals());
test('reopening a closed document exposes its container before layout captures the mounted window',()=>{
  const dom=syntheticDOM(),events=new EventTarget();
  const create=dom.document.createElement;
  vi.stubGlobal('document',Object.assign(dom.document,{getElementById:()=>dom.host,
    createElement:(tag:string)=>Object.assign(create(tag),{focus(){}})}));
  vi.stubGlobal('window',events);vi.stubGlobal('requestAnimationFrame',dom.raf);
  vi.stubGlobal('MutationObserver',class{observe(){}});
  const artifacts=new StudyArtifacts(),mountedHidden:boolean[]=[];
  events.addEventListener('workspace-item-mounted',event=>{
    const root=(event as CustomEvent<HTMLElement>).detail;
    root.dataset.workspaceView='main'; // WorkspaceViews' ownership assignment.
    mountedHidden.push(Boolean(artifacts.root.hidden));
  });
  const content=dom.document.createElement('section') as unknown as HTMLElement;
  const root=artifacts.document('table:a:e1:0','Measurements',content);
  [...root.querySelectorAll('button')].find(button=>button.getAttribute('aria-label')==='Close Measurements')!.dispatchEvent(new Event('click'));
  expect(artifacts.root.hidden).toBe(true);
  const reopened=artifacts.document('table:a:e1:0','Measurements',dom.document.createElement('section') as unknown as HTMLElement);
  expect(reopened).toBe(root);
  expect(mountedHidden).toEqual([false,false]);
  expect(artifacts.root.hidden).toBe(false);
});

test('ordinary Close cancels a document export without releasing its retained window',async()=>{
  const {savedMovieExport}=await import('./scientific-movie');
  const dom=syntheticDOM(),events=new EventTarget();
  vi.stubGlobal('document',Object.assign(dom.document,{getElementById:()=>dom.host}));
  vi.stubGlobal('window',events);vi.stubGlobal('requestAnimationFrame',dom.raf);
  vi.stubGlobal('MutationObserver',class{observe(){}});
  let signal:AbortSignal|undefined;
  vi.stubGlobal('fetch',vi.fn((_url,init)=>new Promise((_resolve,reject)=>{
    signal=init.signal;signal!.addEventListener('abort',()=>reject(new Error('aborted')));
  })));
  const artifacts=new StudyArtifacts(),content=document.createElement('section'),released=vi.fn();
  const exporter=savedMovieExport(content,{id:'p'+'a'.repeat(32),day:'2026-10-18',version:1});
  const root=artifacts.document('export-test','Saved animation',content,released,{}, {},exporter.cancel);
  const start=[...content.querySelectorAll('button')].find(button=>button.textContent==='Export saved movie')!;
  start.dispatchEvent(new Event('click'));expect(signal?.aborted).toBe(false);
  [...root.querySelector('header')!.querySelectorAll('button')].find(button=>button.getAttribute('aria-label')==='Close Saved animation')!.dispatchEvent(new Event('click'));
  expect(signal?.aborted).toBe(true);expect(root.hidden).toBe(true);expect(released).not.toHaveBeenCalled();
  exporter.dispose();
});
