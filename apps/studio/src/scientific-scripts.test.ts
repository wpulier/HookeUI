// Real file decoding and editor ownership over a synthetic DOM; no Python execution here.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { readPythonScript, ScientificScripts, SCRIPT_BYTES } from './scientific-scripts';
import { TestElement, fire, settleMicrotasks } from './workspace-windows.test-support';

class Element extends TestElement { focus() {} }
beforeEach(() => vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) }));
afterEach(() => vi.unstubAllGlobals());
const file = (value: string | Uint8Array, name = 'analysis.py') => new File([typeof value === 'string' ? value : new Uint8Array(value)], name);
const deferred = () => { let resolve!: (value: ArrayBuffer) => void; const promise = new Promise<ArrayBuffer>(done => { resolve = done; }); return { resolve, promise }; };
function mount(draft = '') {
  const editor = new Element('textarea'); editor.value = draft;
  const changed = vi.fn(), scripts = new ScientificScripts(editor as unknown as HTMLTextAreaElement, changed);
  const root = scripts.element as unknown as TestElement;
  const choose = (selected: File) => {
    const input = root.querySelector('input')!;
    Object.assign(input, { files: [selected] }); fire(input, 'change');
  };
  const button = (words: string) => root.querySelectorAll('button').find(value => value.textContent === words)!;
  return { editor, root, scripts, choose, changed, button };
}

test('script decoding bounds bytes before reading and refuses malformed or non-Python inputs', async () => {
  expect(await readPythonScript(file('value = "µ"\r\nvalue'))).toBe('value = "µ"\r\nvalue');
  for (const bad of [file('x', 'x.txt'), file(' '), file('x\0y'), file(new Uint8Array([255])), file('x'.repeat(SCRIPT_BYTES + 1))])
    await expect(readPythonScript(bad)).rejects.toThrow();
  const huge = file('x'); Object.defineProperty(huge, 'size', { value: SCRIPT_BYTES + 1 });
  const read = vi.spyOn(huge, 'arrayBuffer');
  await expect(readPythonScript(huge)).rejects.toThrow(); expect(read).not.toHaveBeenCalled();
});

test('opening into an empty editor changes only the draft and offers its exact downloadable bytes', async () => {
  const view = mount();
  view.choose(file('answer = 42\nanswer')); await settleMicrotasks(); await settleMicrotasks();
  expect(view.editor.value).toBe('answer = 42\nanswer'); expect(view.changed).toHaveBeenCalledTimes(1);
  expect(view.root.textContent).toContain('Inspect it, then press Run code');
  const link = view.root.querySelector('a') as unknown as HTMLAnchorElement;
  expect(await (await fetch(link.href)).text()).toBe(view.editor.value);
  const old = link.href; view.scripts.close(); await expect(fetch(old)).rejects.toThrow();
});

test('an existing or concurrently typed draft is never replaced by an asynchronous file read', async () => {
  const view = mount(), pending = deferred(), selected = file('x=7');
  vi.spyOn(selected, 'arrayBuffer').mockReturnValue(pending.promise);
  view.choose(selected); view.editor.value = 'keep this draft'; fire(view.editor, 'input');
  pending.resolve(new TextEncoder().encode('x=7').buffer); await settleMicrotasks(); await settleMicrotasks();
  expect(view.editor.value).toBe('keep this draft'); expect(view.changed).not.toHaveBeenCalled();
  expect(view.button('Replace draft with this script').hidden).toBe(false);
  fire(view.button('Keep current draft'), 'click'); expect(view.editor.value).toBe('keep this draft');
  view.choose(file('x=8')); await settleMicrotasks(); await settleMicrotasks();
  fire(view.button('Replace draft with this script'), 'click');
  expect(view.editor.value).toBe('x=8'); expect(view.changed).toHaveBeenCalledTimes(1);
  view.scripts.close();
});

test('a newer selection or closed console owns late file replies and bad input keeps the draft', async () => {
  const view = mount(), pending = deferred(), old = file('x=1');
  vi.spyOn(old, 'arrayBuffer').mockReturnValue(pending.promise);
  view.choose(old); view.choose(file('x=2')); await settleMicrotasks(); await settleMicrotasks();
  pending.resolve(new TextEncoder().encode('x=1').buffer); await settleMicrotasks(); await settleMicrotasks();
  expect(view.editor.value).toBe('x=2');
  view.choose(file(new Uint8Array([255]))); await settleMicrotasks(); await settleMicrotasks();
  expect(view.editor.value).toBe('x=2'); expect(view.root.textContent).toContain('not UTF-8');
  const stopped = deferred(), last = file('x=3'); vi.spyOn(last, 'arrayBuffer').mockReturnValue(stopped.promise);
  view.choose(last); view.scripts.close(); stopped.resolve(new TextEncoder().encode('x=3').buffer);
  await settleMicrotasks(); await settleMicrotasks(); expect(view.editor.value).toBe('x=2');
  expect(view.root.children).toHaveLength(0);
});


test('large scripts load exactly into a draft without executing or losing an existing draft', async () => {
  const source='# calibrated µ input\n'.repeat(2000)+'print(42)', view=mount('kept = 7');
  expect(new TextEncoder().encode(source).length).toBeGreaterThan(16<<10);
  view.choose(file(source));await settleMicrotasks();await settleMicrotasks();
  expect(view.editor.value).toBe('kept = 7');expect(view.changed).not.toHaveBeenCalled();
  fire(view.button('Replace draft with this script'),'click');
  expect(view.editor.value).toBe(source);expect(view.changed).toHaveBeenCalledTimes(1);
  view.scripts.close();
});
