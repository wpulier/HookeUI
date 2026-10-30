// Synthetic DOM/HTTP contract tests; native reader and browser evidence are separate.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import { sourceReaders } from './scientific-readers';
const target={id:'p'+'c'.repeat(32),version:1,title:'Source with Native reader'};
const source={id:'p'+'a'.repeat(32),day:'original',version:1};
const item={key:'mine/reader#npy',name:'Native reader',owner:'mine/reader',generation:'b'.repeat(16),canReview:true,reason:'',scope:'Private candidate; unproven'};
const workspace=()=>({id:target.id,session:source.day,revision:1,document:{title:target.title,state:{sourceReader:{capability:item.key,generation:item.generation,source:{id:source.id,session:source.day,revision:source.version}}}}});
const response=(data:object,status=200)=>new Response(JSON.stringify(data),{status});
const discovery=(row=item)=>response({ok:true,source:{id:source.id,session:source.day,revision:source.version},readers:[row]});
let root:TestElement;
const fetcher=vi.fn<typeof fetch>(),open=vi.fn(async(_target:{id:string;version:number;title:string})=>{});
const button=(text:string)=>root.querySelectorAll('button').find(el=>el.textContent===text)!;
async function settle(){for(let i=0;i<12;i++)await settleMicrotasks();}
beforeEach(()=>{
  vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',fetcher);fetcher.mockReset();open.mockClear();
  const host=document.createElement('section');root=host as unknown as TestElement;sourceReaders(host,source,open);
});
afterEach(()=>vi.unstubAllGlobals());

test('discovery, private binding and explicit open remain separate; original source identity is pinned',async()=>{
  expect(fetcher).not.toHaveBeenCalled();fetcher.mockResolvedValueOnce(discovery());
  fire(button('Find readers'),'click');await settle();expect(open).not.toHaveBeenCalled();
  fetcher.mockResolvedValueOnce(response({ok:true,project:workspace()}));
  fire(button('Review with Native reader'),'click');await settle();
  expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({session:'original',revision:1,capability:item.key,generation:item.generation});
  expect(open).not.toHaveBeenCalled();expect(root.textContent).toContain('original source remains unchanged');
  fire(button('Open reader workspace'),'click');await settle();expect(open).toHaveBeenCalledWith(target);
});

test('hardware/setup refusal is explained with its setup route; no automatic install or execution',async()=>{
  fetcher.mockResolvedValueOnce(discovery({...item,canReview:false,reason:'This Mac lacks the required device'}));
  fire(button('Find readers'),'click');await settle();
  expect(button('Review with Native reader').disabled).toBe(true);expect(root.textContent).toContain('lacks the required device');
  expect(root.querySelector('a')).toHaveProperty('href','#models/mine%2Freader');
  expect(fetcher).toHaveBeenCalledTimes(1);expect(open).not.toHaveBeenCalled();
});

test('failed binding stays retryable and does not discard the retained source',async()=>{
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ok:false,says:'Changed generation',next:'Prepare again'},409));
  fire(button('Find readers'),'click');await settle();fire(button('Review with Native reader'),'click');await settle();
  expect(root.textContent).toContain('Changed generation Prepare again');expect(button('Review with Native reader').disabled).toBe(false);
  expect(open).not.toHaveBeenCalled();fetcher.mockResolvedValueOnce(response({ok:true,project:workspace()}));
  fire(button('Review with Native reader'),'click');await settle();
  expect(fetcher.mock.calls[2][1]?.body).toBe(fetcher.mock.calls[1][1]?.body);
});

test('a stale source list cannot supply executable choices',async()=>{
  fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:'different',revision:1},readers:[item]}));
  fire(button('Find readers'),'click');await settle();
  expect(root.textContent).toContain('belongs to another source');expect(button('Review with Native reader')).toBeUndefined();
});

 test('one broken preparation remains visible without hiding a healthy reader',async()=>{
  fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:1},readers:[{...item,name:'Incomplete reader',generation:null,canReview:false,reason:'Repair this integration'},item]}));
  fire(button('Find readers'),'click');await settle();
  expect(button('Review with Incomplete reader').disabled).toBe(true);
  expect(button('Review with Native reader').disabled).toBe(false);
  expect(root.textContent).toContain('Repair this integration');
});

test('a returned workspace for another reader cannot be opened',async()=>{
  const project=workspace();project.document.state.sourceReader.generation='d'.repeat(16);
  fetcher.mockResolvedValueOnce(discovery()).mockResolvedValueOnce(response({ok:true,project}));
  fire(button('Find readers'),'click');await settle();fire(button('Review with Native reader'),'click');await settle();
  expect(root.textContent).toContain('different source or integration');
  expect(button('Open reader workspace')).toBeUndefined();expect(open).not.toHaveBeenCalled();
});

test('the declared choice is shown first and a refusal names its reasons and next step without binding',async()=>{
  const other={...item,key:'mine/other#npy',name:'Other reader',canReview:false,reason:'This reader does not declare that it keeps calibration exact, so Hooke will not open scientific data with it'};
  fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:source.version},readers:[other,item],
    choice:{state:'chosen',capability:item.key,generation:item.generation,says:'Native reader is the one installed reader that declares this source and keeps its native facts exact.'}}));
  fire(button('Find readers'),'click');await settle();
  expect(root.textContent).toContain('Native reader is the one installed reader');
  expect(root.querySelectorAll('h4').map(h=>h.textContent)).toEqual(['Native reader · chosen by its declaration','Other reader']);
  expect(root.textContent).toContain('keeps calibration exact');expect(fetcher).toHaveBeenCalledTimes(1);
  fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:source.version},readers:[other],
    choice:{state:'refused',says:'1 installed reader declares this source, but none can open it exactly now.',next:'follow the step each reader names, then check again'}}));
  fire(button('Refresh reader readiness'),'click');await settle();
  expect(root.textContent).toContain('none can open it exactly now. Next: follow the step each reader names');
  expect(button('Review with Other reader').disabled).toBe(true);expect(open).not.toHaveBeenCalled();
});

test('an incompatible reader is said apart from one that needs setup, and offers no setup link',async()=>{
  fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:source.version},readers:[
    {...item,key:'mine/tif#read',name:'TIFF reader',canReview:false,blocked:'incompatible',reason:'This reader opens names matching *.tif; this source is named “a.npy”'},
    {...item,name:'Native reader',canReview:false,blocked:'setup',reason:'Prepare this integration in Models before reviewing its reader'}]}));
  fire(button('Find readers'),'click');await settle();
  expect(root.textContent).toContain('Can’t open this source: This reader opens names matching *.tif');
  expect(root.textContent).toContain('Needs setup: Prepare this integration');
  expect(root.querySelectorAll('a').map(a=>a.hidden)).toEqual([true,false]);
});


test('an empty discovery has no repeated reader-check loop or invented setup',async()=>{
 fetcher.mockResolvedValueOnce(response({ok:true,source:{id:source.id,session:source.day,revision:source.version},readers:[]}));
 fire(button('Find readers'),'click');await settle();
 expect(root.textContent).toContain('Preview isn’t available yet');
 expect(button('Refresh reader readiness').hidden).toBe(true);
 expect(root.querySelectorAll('a')).toHaveLength(0);
});
