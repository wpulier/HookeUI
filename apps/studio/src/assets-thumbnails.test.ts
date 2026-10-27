import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { FootageThumbnails } from './assets';
let changed: IntersectionObserverCallback;
class Observer {
  constructor(callback: IntersectionObserverCallback) { changed = callback; }
  observe() {} unobserve() {} disconnect() {}
}
function image() {
  let src = ''; const requested: string[] = [];
  const node = {dataset:{src:'/assets/thumb?name=leaf'},isConnected:true,
    classList:{add:vi.fn()},onerror:null as (()=>void)|null,onload:null as (()=>void)|null,
    get src(){return src;},set src(value:string){src=value;requested.push(value);}};
  const html = node as unknown as HTMLImageElement;
  const near = (isIntersecting:boolean) => changed([{target:html,isIntersecting} as unknown as IntersectionObserverEntry], {} as IntersectionObserver);
  return {node,html,requested,near};
}
beforeEach(()=>{vi.useFakeTimers();vi.stubGlobal('IntersectionObserver',Observer);vi.stubGlobal('window',globalThis);});
afterEach(()=>{vi.useRealTimers();vi.unstubAllGlobals();});
test('failed thumbnails pause offscreen, resume near screen, and stop after bounded attempts',()=>{
  const loader = new FootageThumbnails({} as HTMLElement), img=image();loader.observe(img.html);
  expect(img.requested).toEqual([]); img.near(true);expect(img.requested).toHaveLength(1);
  img.node.onerror?.();img.near(false);vi.advanceTimersByTime(60_000);expect(img.requested).toHaveLength(1);
  img.near(true);expect(img.requested).toHaveLength(2);
  for(let i=0;i<12;i++){img.node.onerror?.();vi.advanceTimersByTime(60_000);}
  expect(img.requested).toHaveLength(9);
});
test('replaced or disconnected thumbnail nodes never retry',()=>{
  const loader = new FootageThumbnails({} as HTMLElement), img=image();loader.observe(img.html);img.near(true);
  img.node.onerror?.();loader.reset();vi.advanceTimersByTime(60_000);img.node.onerror?.();
  expect(img.requested).toHaveLength(1);
  const next=image();loader.observe(next.html);next.near(true);next.node.onerror?.();next.node.isConnected=false;
  vi.advanceTimersByTime(60_000);expect(next.requested).toHaveLength(1);
});
