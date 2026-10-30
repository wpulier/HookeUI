import {afterEach,expect,test,vi} from 'vitest';
vi.mock('./screen',()=>({rectOf:(r:unknown)=>r,surfaceVisible:(node:{visible:boolean},open:boolean)=>open&&node.visible}));
vi.mock('./scientific-presentation',()=>({scientificPresentation:()=>null}));
import {documentSurfaces} from './workspace-document-surfaces';
afterEach(()=>vi.unstubAllGlobals());
test('hidden document controls do not consume observation capacity; visible controls keep stable identities',()=>{
 const buttons=Array.from({length:80},(_,i)=>({visible:i>=45&&i<48,disabled:i===47,
  getAttribute:()=>null,getBoundingClientRect:()=>[0,0,100,20]}));
 const root={visible:true,classList:{contains:()=>false},dataset:{placementId:'placement',project:'p',workspaceView:'main',
  studyRetained:JSON.stringify({resource:{day:'p',id:'d1',version:1,picture:null}})},style:{zIndex:'1'},
  getAttribute:()=> 'Scientific tool',getBoundingClientRect:()=>[0,0,400,400],contains:()=>false,
  querySelectorAll:()=>buttons};
 vi.stubGlobal('document',{querySelectorAll:()=>[root],activeElement:null});
 const controls=documentSurfaces()[0].read().controls;
 expect(controls.map(c=>c.fact.id)).toEqual([45,46,47].map(i=>`document:placement:control:${i}`));
 expect(controls.map(c=>c.fact.enabled)).toEqual([true,true,false]);
 root.visible=false;
 expect(documentSurfaces()[0].read().controls).toEqual([]);
 root.visible=true;
 root.dataset=Object.assign(root.dataset,{dismissed:'true'});
 expect(documentSurfaces()[0].read().controls).toEqual([]);
 expect(documentSurfaces()[0].read().legacy?.document?.resource.id).toBe('d1');
});
test('the screen report lists the Data reader and its item, as the right edge’s reader or a reader Tab',()=>{
 const reader={hidden:false,visible:true,classList:{contains:(name:string)=>name==='data-reader-side'},
  dataset:{readerResource:JSON.stringify({day:'p',id:'e8',version:0}),readerOpen:'true',readerTitle:'Bright regions',readerView:'Outlines',displayState:'rendered'},
  getBoundingClientRect:()=>[840,92,600,768],contains:()=>false,querySelectorAll:()=>[]};
 vi.stubGlobal('document',{querySelectorAll:(selector:string)=>selector.startsWith('.data-reader')?[reader]:[],activeElement:null,
  body:{dataset:{workspaceProject:'p',activeWorkspaceView:'main'}}});
 const [surface]=documentSurfaces();
 const read=surface.read();
 expect(surface.id).toBe('document:reader-side');
 expect(read).toMatchObject({kind:'document',open:true,visible:true});
 expect(read.legacy).toMatchObject({title:'Reader · Bright regions · Outlines',shows:'e8',
  document:{project:'p',tab:'main',placement:'reader:side:main',resource:{day:'p',id:'e8',version:0,picture:null},representation:'reader',state:'rendered',focused:false}});
 // Closed, it is not on screen; a reader with no item is not listed at all.
 reader.dataset.readerOpen='';expect(surface.read().open).toBe(false);
 reader.dataset={} as typeof reader.dataset;expect(documentSurfaces()).toEqual([]);
});

test('a reader Tab\'s reader is the Tab\'s own page, behind the windows opened on it (will-research: a video "hidden behind the document")',()=>{
 // On a reader Tab the windows' layer is lifted over the reader (data-reader.css), and the report says the same order:
 // the reader below any window, so "nothing over it" is read from what the person sees.
 const reader={hidden:false,visible:true,classList:{contains:(name:string)=>name==='data-reader-tab'},
  dataset:{readerResource:JSON.stringify({day:'p',id:'d1',version:null}),readerOpen:'true',readerTitle:'Findings',displayState:'rendered'},
  getBoundingClientRect:()=>[0,92,1440,768],contains:()=>false,querySelectorAll:()=>[]};
 const video={visible:true,classList:{contains:()=>false},dataset:{placementId:'v1',project:'p',workspaceView:'r',
  webWindow:JSON.stringify({kind:'video',resource:{day:'p',id:'d2',version:null}})},style:{zIndex:''},
  getAttribute:()=>'What are stomata',getBoundingClientRect:()=>[300,120,640,400],contains:()=>false,querySelectorAll:()=>[]};
 vi.stubGlobal('document',{querySelectorAll:(selector:string)=>selector.startsWith('.data-reader')?[reader]:[video],activeElement:null,
  body:{dataset:{workspaceProject:'p',activeWorkspaceView:'r'}}});
 const [tab,window]=documentSurfaces().map(s=>s.read());
 expect(tab.legacy?.document?.representation).toBe('reader-tab');
 expect(window.legacy?.document?.representation).toBe('video');
 expect((tab.z??0)<(window.z??0)).toBe(true);
});

test('the stylesheet lifts a reader Tab\'s windows over its reader, and the right edge\'s reader stays over them',async()=>{
 const {readFileSync}=await import('node:fs');
 const css=readFileSync(new URL('./data-reader.css',import.meta.url),'utf8').replace(/\s+/g,' ');
 const z=(rule:RegExp)=>Number(rule.exec(css)?.[1]);
 const readerTab=z(/\.data-reader-tab \{[^}]*z-index: (\d+)/),windows=z(/\[data-reader-tab='true'\] #study-artifacts \{[^}]*z-index: (\d+)/);
 expect(windows).toBeGreaterThan(readerTab);
 expect(z(/\.data-reader-side \{[^}]*z-index: (\d+)/)).toBeGreaterThan(windows);
});
