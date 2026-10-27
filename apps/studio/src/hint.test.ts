// A hint is dismissed by its own × (the user's notes of 2026-10-11), and its text stays what it says.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { renderHint } from './screen';
import { fire, TestElement } from './workspace-windows.test-support';

beforeEach(() => vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) }));
afterEach(() => vi.unstubAllGlobals());

test("a hint's × dismisses it; a newer hint is never dismissed by an older one's", () => {
  const hint = new TestElement('p');
  renderHint(hint as unknown as HTMLElement, 'c1278: area 244 px²');
  expect(hint.textContent).toBe('c1278: area 244 px²');
  const older = hint.querySelector('.hint-close') as TestElement;
  fire(older, 'click');
  expect(hint.textContent).toBe('');
  const clearOld = renderHint(hint as unknown as HTMLElement, 'first');
  renderHint(hint as unknown as HTMLElement, 'Talking with GPT-Live.');
  clearOld();
  expect(hint.textContent).toBe('Talking with GPT-Live.');
});

test('a passing reason is said once, across reloads too: a second report, or the same one after a reload, says nothing', async () => {
  const { saidOnce } = await import('./screen');
  const memory = new Map<string, string>();
  const storage = { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => { memory.set(k, v); } };
  expect(saidOnce('e12:kept on the original image', storage)).toBe(false); // said now
  expect(saidOnce('e12:kept on the original image', storage)).toBe(true); // not again, nor after a reload (same tab)
  expect(saidOnce('e13:kept on the original image', storage)).toBe(false); // another population's reason is news
  expect(saidOnce('x', null)).toBe(false); // without storage it is said
  const broken = { getItem: () => { throw new Error('blocked'); }, setItem: () => {} };
  expect(saidOnce('x', broken)).toBe(false);
});
