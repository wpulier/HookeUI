// Synthetic renderer behavior. Actual codec support is a separate browser journey.
import {afterEach, expect, it, vi} from 'vitest';
import {syntheticDOM, TestElement} from './workspace-windows.test-support';
import {showRetainedMedia} from './retained-media-preview';

afterEach(()=>vi.unstubAllGlobals());
const ref={id:'p'+'1'.repeat(32),day:'2026-10-28.4',version:2};
const state={sourceFile:{sha256:'a'.repeat(64)},librarySource:{format:1,scope:'selected-media',asset:{kind:'still'}}};
it('uses the exact saved original, preserves download content, reports errors and retries without moving the Stage',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
  const body=new TestElement('div'), retained=new TestElement('a');retained.textContent='Download original';body.append(retained);
  const controller=new AbortController();
  showRetainedMedia(body as unknown as HTMLElement,state,ref,controller.signal);
  const image=body.querySelector('.rd-retained-image')! as unknown as HTMLImageElement;
  expect(image.src).toBe(`/api/scientific-files/${ref.id}/media?session=2026-10-28.4&revision=2&sha256=${'a'.repeat(64)}`);
  expect(body.textContent).toContain('Download original');
  image.dispatchEvent(new Event('load'));
  expect(body.textContent).toContain('Preview leaves the Stage unchanged');
  image.dispatchEvent(new Event('error'));
  expect(image.hidden).toBe(true);expect(body.textContent).toContain('compatible viewer');
  body.querySelector('button')!.dispatchEvent(new Event('click'));
  expect(image.hidden).toBe(false);
  controller.abort();expect(image.getAttribute('src')).toBeNull();
});
it('does not treat a filename, malformed metadata or generic source as a media declaration',()=>{
  const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
  const body=new TestElement('div');
  for(const value of [{sourceFile:{name:'photo.png'}},null,{...state,librarySource:{...state.librarySource,scope:'whole-record-folder'}}])
    showRetainedMedia(body as unknown as HTMLElement,value,ref,new AbortController().signal);
  expect(body.children).toHaveLength(0);
});
it('does not autoplay recordings and releases playback on reader close',()=>{
  const dom=syntheticDOM(),create=dom.document.createElement.bind(dom.document),pause=vi.fn(),load=vi.fn();
  vi.stubGlobal('document',{...dom.document,createElement:(tag:string)=>{
    const node=create(tag);if(tag==='video')Object.assign(node,{pause,load});return node;
  }});
  const body=new TestElement('div'),controller=new AbortController();
  showRetainedMedia(body as unknown as HTMLElement,{...state,librarySource:{...state.librarySource,asset:{kind:'recording'}}},ref,controller.signal);
  const video=body.querySelector('.rd-video')! as unknown as HTMLVideoElement;
  expect(video.controls).toBe(true);expect(video.preload).toBe('metadata');expect(video.autoplay).not.toBe(true);
  video.dispatchEvent(new Event('loadedmetadata'));expect(body.textContent).toContain('Retained original');
  controller.abort();expect(pause).toHaveBeenCalledOnce();expect(load).toHaveBeenCalledOnce();
});
