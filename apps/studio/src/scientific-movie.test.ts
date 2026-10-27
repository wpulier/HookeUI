// Synthetic DOM/transport; actual native packages and codecs have separate evidence.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import { savedMovieExport, renderedMovieDescription, validMovieFile } from './scientific-movie';
const source = {id:'p'+'a'.repeat(32),day:'2026-10-18',version:3};
const fileRef={id:'p'+'b'.repeat(32),session:source.day,revision:'c'.repeat(64),component:'file.source'};
const provenance={format:'scientific-movie-export-v1',derivedDisplay:true,settings:{format:'mov-argb-v1',width:512,height:384,fps:10},sequence:{format:'scientific-frame-sequence-v1',name:'example',count:3},source:{id:source.id,session:source.day,revision:3,digest:'a'.repeat(64)},encoder:{codec:'rawvideo',sha256:'b'.repeat(64)},rgbaSha256:'c'.repeat(64),inputs:[],runtime:{bundle:'example',operation:'viewer',generation:'d'.repeat(16)}};
let root:TestElement, dispose:()=>void;
const fetcher=vi.fn<typeof fetch>();
const button=(name:string)=>root.querySelectorAll('button').find(el=>el.textContent===name)!;
async function settle(){for(let i=0;i<12;i++)await settleMicrotasks();}
beforeEach(()=>{
  vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',fetcher);fetcher.mockReset();
  const element=document.createElement('section');root=element as unknown as TestElement;dispose=savedMovieExport(element,source).dispose;
});
afterEach(()=>{dispose();vi.unstubAllGlobals();});
test('opening never runs; explicit export captures saved revision and retry identity',async()=>{
  expect(fetcher).not.toHaveBeenCalled();expect(root.textContent).toContain('saved revision 3');
  fetcher.mockRejectedValueOnce(new Error('connection interrupted'));
  fire(button('Export saved movie'),'click');await settle();
  const first=fetcher.mock.calls[0][1];expect(JSON.parse(String(first?.body))).toMatchObject({session:source.day,revision:3,settings:{format:'mov-argb-v1'}});
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ok:true,project:{id:'p'+'b'.repeat(32),session:source.day,revision:1,document:{kind:'source-file',resources:[fileRef],state:{sourceFile:{format:1,name:'Rendered movie.mov',size:123,sha256:'c'.repeat(64),reference:fileRef},movieExport:provenance}}}})));
  fire(button('Export saved movie'),'click');await settle();
  expect(fetcher.mock.calls[1][1]?.body).toBe(first?.body);expect(root.textContent).toContain('saved in Project Data');
  expect(root.querySelector('a')?.textContent).toBe('Download rendered movie');
});
test('cancel and disposal abort only the owned export',async()=>{
  fetcher.mockImplementation((_url,init)=>new Promise((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new Error('aborted')))));
  fire(button('Export saved movie'),'click');fire(button('Cancel'),'click');await settle();
  expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);expect(root.textContent).toContain('Retry to recover');
  fire(button('Export saved movie'),'click');dispose();await settle();
  expect(fetcher.mock.calls[1][1]?.signal?.aborted).toBe(true);
});
test('wrong saved input receipt cannot create a download',async()=>{
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ok:true,project:{id:'p'+'b'.repeat(32),session:'another',revision:1}})));
  fire(button('Export saved movie'),'click');await settle();
  expect(root.textContent).toContain('differs from the selected');expect(root.querySelector('a')).toBeNull();
});
test('unknown movie document versions refuse without attempting execution',()=>{
  expect(()=>renderedMovieDescription(root as unknown as HTMLElement,{format:'scientific-movie-export-v2'})).toThrow('unsupported');
  expect(fetcher).not.toHaveBeenCalled();
});

test.each([{settings:{}},{sequence:{}},{encoder:{codec:'h264',sha256:'b'.repeat(64)}},{source:{}},{rgbaSha256:null}])('incomplete provenance refuses claims',change=>{
  expect(()=>renderedMovieDescription(root as unknown as HTMLElement,{...provenance,...change})).toThrow('unsupported');
});

test.each([{}, {...fileRef,id:source.id}, {...fileRef,session:'wrong'}, {...fileRef,component:'array.output'}, {...fileRef,revision:1}])('invalid retained file refuses download',reference=>{
  expect(validMovieFile({format:1,name:'movie.mov',size:10,sha256:'a'.repeat(64),reference},{id:fileRef.id,session:source.day},[reference])).toBe(false);
});
test('movie file must be a retained resource of the accepted result',()=>{
 expect(validMovieFile({format:1,name:'movie.mov',size:10,sha256:'a'.repeat(64),reference:fileRef},{id:fileRef.id,session:source.day},[])).toBe(false);
});


test('legacy qtrle documents remain readable without execution',()=>{
  renderedMovieDescription(root as unknown as HTMLElement,{...provenance,settings:{...provenance.settings,format:'mov-qtrle-v1'},encoder:{...provenance.encoder,codec:'qtrle'}});
  expect(root.textContent).toContain('qtrle');
  expect(fetcher).not.toHaveBeenCalled();
});
test('format and codec mismatch refuses provenance',()=>{
  expect(()=>renderedMovieDescription(root as unknown as HTMLElement,{...provenance,encoder:{...provenance.encoder,codec:'qtrle'}})).toThrow('unsupported');
});

test('the movie export says plainly that keyframes are edited in napari-animation, and how to open it', async () => {
  const { KEYFRAME_WORDS } = await import('./scientific-movie');
  expect(KEYFRAME_WORDS).toMatch(/^Keyframes are made and edited in napari-animation, not here/);
  for (const step of ['Continue with this window’s saved input', 'Find interactive tools', 'Continue with napari-animation', 'Open napari-animation', 'Open session', 'Save tool and all layers'])
    expect(KEYFRAME_WORDS).toContain(step);
  // Shown in the export itself, before its action, without starting anything.
  expect(root.textContent).toContain(KEYFRAME_WORDS);
  expect(root.textContent.indexOf('Keyframes are made')).toBeLessThan(root.textContent.indexOf('Export saved movie'));
  expect(fetcher).not.toHaveBeenCalled();
});
