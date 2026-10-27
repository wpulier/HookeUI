import { expect, test } from 'vitest';
import { scientificViewport, sameScientificViewport } from './scientific-viewport';
import { TestElement } from './workspace-windows.test-support';

function element(width: number, height: number) {
  const node = new TestElement('canvas');
  node.style.width = `${width}px`; node.style.height = `${height}px`;
  return node as unknown as HTMLElement;
}

test.each([
  [320, 240, 1, 320, 240], [320, 240, 2, 640, 480],
  [800, 600, 2, 1024, 768], [500, 1000, 3, 512, 1024],
  [4000, 3000, 1, 1024, 768], [100.5, 80.5, 1.25, 126, 101],
])('viewport %s × %s at scale %s uses bounded physical pixels', (w,h,dpr,pw,ph) => {
  expect(scientificViewport(element(w,h),dpr)).toEqual({width:pw,height:ph,cssWidth:w,cssHeight:h,deviceScale:dpr});
});

test.each([[0,10,1], [10,0,1], [10,10,0], [10,10,NaN], [10,10,Infinity]])('hidden or invalid viewport is refused: %j', (w,h,dpr) => {
  expect(() => scientificViewport(element(w,h),dpr)).toThrow('visible size');
});

test('a changed display invalidates a pending preview even when both fit the same render limit', () => {
  const first = scientificViewport(element(2000,1500),2);
  const resized = scientificViewport(element(2400,1800),2);
  expect([first.width,first.height]).toEqual([resized.width,resized.height]);
  expect(sameScientificViewport(first,resized)).toBe(false);
  expect(sameScientificViewport(first,{...first,deviceScale:1})).toBe(false);
  expect(sameScientificViewport(first,{...first})).toBe(true);
});
