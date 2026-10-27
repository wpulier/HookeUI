import { afterEach, expect, it, vi } from 'vitest';
import { DataWindowBody } from './data-window-body';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

afterEach(() => vi.restoreAllMocks());

it('keeps Search Data focused while typing redraws the sections, and while a background refresh redraws them', () => {
  const dom = syntheticDOM();
  vi.stubGlobal('document', dom.document);
  // As in a browser: taking the focused box (or anything holding it) out of the page blurs it.
  let active: TestElement | null = null;
  const remove = TestElement.prototype.remove;
  vi.spyOn(TestElement.prototype, 'remove').mockImplementation(function (this: TestElement) {
    if (active && this.contains(active)) active = null;
    remove.call(this);
  });
  const root = new TestElement('div'), wrap = new TestElement('div'), search = new TestElement('input');
  const filter = new TestElement('div'), list = new TestElement('div');
  wrap.append(search); dom.host.append(root);
  const body = new DataWindowBody(root as unknown as HTMLElement, wrap as unknown as HTMLElement, filter as unknown as HTMLElement, list as unknown as HTMLElement);
  const draw = (rows: number) => {
    // What renderResults does on every keystroke and every refresh: the filter's and the sections' contents, never them.
    body.filter.replaceChildren(new TestElement('button') as unknown as HTMLElement);
    body.list.replaceChildren(new TestElement('h3') as unknown as HTMLElement, ...Array.from({ length: rows }, () => new TestElement('div') as unknown as HTMLElement));
    body.showResults();
  };
  draw(3);
  // Search Data, Made by, then the sections: there is no separate Inputs section any more (ticket 66).
  expect(root.children).toEqual([body.results]);
  expect(body.results.children).toEqual([wrap, filter, list]);
  active = search;
  draw(1); draw(0); draw(2);
  expect(active).toBe(search);
  expect(search.parentElement).toBe(wrap);
  expect(body.list.children).toHaveLength(3);
  // Activity takes their place, and the list comes back with the same box.
  const activity = new TestElement('section');
  body.show(activity as unknown as HTMLElement);
  expect(root.children).toEqual([activity]);
  expect(active).toBe(null);
  draw(1);
  expect(root.children).toEqual([body.results]);
  expect(body.results.contains(search as unknown as Node)).toBe(true);
});
