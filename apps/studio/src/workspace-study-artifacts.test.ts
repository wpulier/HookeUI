import {expect,test,vi} from 'vitest';
import {StudyArtifacts} from './workspace-study-artifacts';
import {fire,syntheticDOM} from './workspace-windows.test-support';

test('ordinary Measurements reopen focuses the retained placement while explicit copies and exact ownership remain distinct',()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);vi.stubGlobal('window',{dispatchEvent:vi.fn()});
 Object.assign(dom.document.body.dataset,{workspaceProject:'project-a',activeWorkspaceView:'tab-a'});
 const front=vi.fn(),shells=new Map();
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),documents:new Map(),shells,background:new WeakSet(),sync:vi.fn(),
  mount:(root:HTMLElement)=>{Object.assign(root.dataset,{project:document.body.dataset.workspaceProject,workspaceView:document.body.dataset.activeWorkspaceView});shells.set(root,{front});}});
 const resource={day:'project-a',id:'d3',version:null,picture:null};
 const saved={resource,title:'Measured result',zoom:1,pan:[.5,.5],presentation:'table',placementId:'completion-kept',position:[356,44],size:[420,380]};
 const open=(key:string,value:object)=>artifacts.document(key,'Measurements',dom.document.createElement('section'),()=>{},{studyRetained:JSON.stringify(value)});
 try{
  const retained=open('table:project-a:d3:null:completion-kept',saved);
  Object.assign(retained.querySelector('header')!,{focus:vi.fn()});
  retained.dataset.placementId='completion-kept';retained.hidden=true;retained.dataset.dismissed='true';
  const before=retained.dataset.studyRetained;
  const ordinary={...saved,placementId:undefined,resource:{...resource,version:0,picture:0}};
  expect(open('table:project-a:d3:0:',ordinary)).toBe(retained);
  expect(open('table:project-a:d3:null:',{...ordinary,resource})).toBe(retained);
  expect(retained.hidden).toBe(false);expect(retained.dataset.dismissed).toBeUndefined();expect(front).toHaveBeenCalledTimes(2);
  expect(retained.dataset.studyRetained).toBe(before);expect(retained.dataset.placementId).toBe('completion-kept');
  const copy=open('table:project-a:d3:null:explicit-copy',{...saved,placementId:'explicit-copy'});
  expect(copy).not.toBe(retained);
  expect(open('table:project-a:d3:1:',{...ordinary,resource:{...resource,version:1}})).not.toBe(retained);
  document.body.dataset.activeWorkspaceView='tab-b';
  expect(open('table:project-a:d3:0:',ordinary)).not.toBe(retained);
  document.body.dataset.workspaceProject='project-b';document.body.dataset.activeWorkspaceView='tab-a';
  expect(open('table:project-b:d3:0:',{...ordinary,resource:{...resource,day:'project-b',version:0}})).not.toBe(retained);
 }finally{vi.unstubAllGlobals();}
});

test('revealing a locally closed document reopens and raises its own shell without changing data',()=>{
 const focus=vi.fn(),front=vi.fn(),sync=vi.fn();
 const root={hidden:true,dataset:{placementId:'kept',dismissed:'true'},querySelector:()=>({focus})};
 const other={hidden:false,dataset:{placementId:'other'}};
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),
  documents:new Map([['one',{root}],['two',{root:other}]]),shells:new Map([[root,{front}]]),sync});
 expect(artifacts.applyDocument('document:kept',{open:true,front:true})).toBe(true);
 expect(root.hidden).toBe(false);expect(root.dataset).not.toHaveProperty('dismissed');
 expect(front).toHaveBeenCalledOnce();expect(focus).toHaveBeenCalledOnce();expect(other.hidden).toBe(false);
});

test('releasing one recovery document disposes only that presentation, once',()=>{
 const oldRoot={},otherRoot={},oldDispose=vi.fn(),otherDispose=vi.fn(),sync=vi.fn();
 const documents=new Map([['old',{root:oldRoot,dispose:oldDispose}],['other',{root:otherRoot,dispose:otherDispose}]]);
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),documents,sync});
 artifacts.removeDocument(oldRoot);artifacts.removeDocument(oldRoot);
 expect(oldDispose).toHaveBeenCalledOnce();expect(otherDispose).not.toHaveBeenCalled();
 expect([...documents.keys()]).toEqual(['other']);expect(sync).toHaveBeenCalledOnce();
});


test('canonical document title updates presentation and retained metadata without a layout write or identity change',()=>{
 const heading={textContent:'3 saved scientific layers',title:'3 saved scientific layers'},close={setAttribute:vi.fn()};
 const saved={title:'3 saved scientific layers',placementId:'kept',resource:{day:'project',id:'p123',version:1},position:[12,34],display:{scientific:{kept:true}}};
 const root={dataset:{studyRetained:JSON.stringify(saved)},setAttribute:vi.fn(),querySelector:(selector:string)=>selector.endsWith('h3')?heading:close};
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),documents:new Map([['one',{root}]])});
 artifacts.documentTitle(root,'Mixed mesh + 2 layers');
 expect(heading).toEqual({textContent:'Mixed mesh + 2 layers',title:'Mixed mesh + 2 layers'});
 expect(root.setAttribute).toHaveBeenCalledWith('aria-label','Mixed mesh + 2 layers');
 expect(close.setAttribute).toHaveBeenCalledWith('aria-label','Close Mixed mesh + 2 layers');
 expect(JSON.parse(root.dataset.studyRetained)).toEqual({...saved,title:'Mixed mesh + 2 layers'});
 const display='Scientific view · revision 1 — Mixed mesh + 2 layers';
 artifacts.documentTitle(root,'Mixed mesh + 2 layers',display);
 expect(heading.textContent).toBe(display);
 expect(root.setAttribute).toHaveBeenLastCalledWith('aria-label',display);
 expect(close.setAttribute).toHaveBeenLastCalledWith('aria-label',`Close ${display}`);
 expect(JSON.parse(root.dataset.studyRetained)).toEqual({...saved,title:'Mixed mesh + 2 layers'});
 const foreign={setAttribute:vi.fn()};artifacts.documentTitle(foreign,'Wrong window');expect(foreign.setAttribute).not.toHaveBeenCalled();
});

test('a document header expands its existing shell without recreating or disposing its content',()=>{
 const dom=syntheticDOM(),content=dom.document.createElement('section'),dispose=vi.fn();
 content.append(dom.document.createElement('input'));content.children[0].value='kept draft';
 vi.stubGlobal('document',dom.document);
 let expanded=false;
 const toggleExpanded=vi.fn(()=>{expanded=!expanded;});
 const shells=new Map();
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),
  documents:new Map(),shells,background:new WeakSet(),
  mount:(root:HTMLElement)=>{shells.set(root,{toggleExpanded,isExpanded:()=>expanded});},
 });
 try {
  const root=artifacts.document('saved','Retained image',content,dispose);
  const expand=root.querySelector('.study-expand')!;
  fire(expand,'click');
  expect(toggleExpanded).toHaveBeenCalledOnce();expect(expand.textContent).toBe('Restore');
  expect(expand.getAttribute('aria-label')).toBe('Restore window size');
  fire(expand,'click');expect(expand.textContent).toBe('Expand');
  expect(root.children).toContain(content);expect(content.children[0].value).toBe('kept draft');
  expect(dispose).not.toHaveBeenCalled();
 } finally {vi.unstubAllGlobals();}
});

test('a document window’s header is its title, ⋯ and Close; Expand (and Add to Tab…, added by the Tabs layer) are in its ⋯',()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),documents:new Map(),shells:new Map(),background:new WeakSet(),mount:vi.fn()});
 try{
  const content=dom.document.createElement('section');
  const root=artifacts.document('saved','Scientific result',content),header=root.querySelector('header')!;
  expect([...header.children].map(child=>child.tagName.toLowerCase()+(child.className?'.'+child.className:''))).toEqual(['h3','span.study-window-tools','button.study-window-more','button']);
  // Expand keeps its button, handler and name in the window's tools, which the ⋯ lists by their own names.
  const tools=header.querySelector('.study-window-tools')!;expect(tools.hidden).toBe(true);expect(tools.querySelector('.study-expand')!.getAttribute('aria-label')).toBe('Expand window');
  expect(header.querySelector('.study-window-more')!.getAttribute('aria-haspopup')).toBe('menu');
  expect(root.querySelector('.study-arrange')).toBe(null);expect(root.children).toContain(content);
 }finally{vi.unstubAllGlobals();}
});


test('focusing a newly opened document reuses Expand and preserves other live drafts and Restore',()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const shells=new Map();let expanded=false;const toggleExpanded=vi.fn(()=>{expanded=!expanded;});
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),documents:new Map(),shells,background:new WeakSet(),
  mount:(root:HTMLElement)=>shells.set(root,{toggleExpanded,isExpanded:()=>expanded})});
 try{
  const content=dom.document.createElement('section'),draft=dom.document.createElement('input');draft.value='unsaved correction';content.append(draft);
  const old=artifacts.document('old','Earlier work',content);
  const next=artifacts.document('next','Next result',dom.document.createElement('section'));
  artifacts.focusDocument(next);artifacts.focusDocument(next);
  expect(toggleExpanded).toHaveBeenCalledOnce();expect(next.querySelector('.study-expand')!.textContent).toBe('Restore');
  expect(old.hidden).toBe(false);expect(draft.value).toBe('unsaved correction');
  fire(next.querySelector('.study-expand')!,'click');expect(expanded).toBe(false);
  const foreign=dom.document.createElement('section');artifacts.focusDocument(foreign);expect(toggleExpanded).toHaveBeenCalledTimes(2);
 }finally{vi.unstubAllGlobals();}
});

test('table drafts block destructive release only in their original Project and Tab',()=>{
 const root={dataset:{project:'p',workspaceView:'work'}},other={dataset:{project:'q',workspaceView:'main'}};
 const dispose=vi.fn(),otherDispose=vi.fn();let dirty=true;
 const artifacts=Object.assign(Object.create(StudyArtifacts.prototype),{guards:new WeakMap(),documents:new Map([
 ['draft',{root,dispose}],['other',{root:other,dispose:otherDispose}]]),sync:vi.fn()});
 artifacts.guardDocument(root,()=>dirty?'Save table draft':null);
 expect(artifacts.blockReason('p','work')).toBe('Save table draft');expect(artifacts.blockReason('p','main')).toBeNull();expect(artifacts.blockReason('q')).toBeNull();
 artifacts.removeDocument(root);artifacts.clearDocuments();expect(dispose).not.toHaveBeenCalled();expect(otherDispose).toHaveBeenCalledOnce();
 dirty=false;artifacts.removeDocument(root);expect(dispose).toHaveBeenCalledOnce();
});
