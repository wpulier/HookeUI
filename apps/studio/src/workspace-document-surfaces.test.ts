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
