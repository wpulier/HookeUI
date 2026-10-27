import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ScientificPythonHelp, pythonAssistance } from './scientific-python-help';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

class Editor extends TestElement {
  selectionStart = 0; selectionEnd = 0;
  focus() {}
  setSelectionRange(start: number, end: number) { this.selectionStart = start; this.selectionEnd = end; }
}
let root: TestElement, editor: Editor;
beforeEach(() => {
  const dom = syntheticDOM(); root = dom.host; editor = new Editor('textarea'); root.append(editor);
  vi.stubGlobal('document', dom.document);
});
afterEach(() => vi.unstubAllGlobals());
const button = (text: string) => root.querySelectorAll('button').find(row => row.textContent === text)!;
const answer = (expression = 'viewer.add_im', changes = {}) => ({format:'scientific-python-assist-v1',mode:'complete',expression,
  matches:['add_image'],text:'No code was run.',truncated:false,...changes});
const edit = (text: string, cursor = text.length) => { editor.value = text; editor.setSelectionRange(cursor,cursor); fire(editor,'input'); };
const mount = (ask: (mode: 'complete' | 'help', expression: string) => Promise<unknown>) => {
  const help = new ScientificPythonHelp(editor as unknown as HTMLTextAreaElement, ask);
  root.append(help.element as unknown as TestElement); help.setAvailable(true); return help;
};

test('completion uses the cursor and inserts only after explicit selection, preserving the rest of the code', async () => {
  const ask = vi.fn(async () => answer()); mount(ask);
  edit('x = viewer.add_im\nprint(x)', 17);
  fire(button('Complete name'),'click'); await settleMicrotasks();
  expect(ask).toHaveBeenCalledExactlyOnceWith('complete','viewer.add_im');
  expect(editor.value).toBe('x = viewer.add_im\nprint(x)');
  fire(button('add_image'),'click');
  expect(editor.value).toBe('x = viewer.add_image\nprint(x)');
  expect(editor.selectionStart).toBe(20);
});

test('newer edits and kernel resets invalidate pending answers without replacing the draft', async () => {
  let finish!: (value: unknown) => void;
  const help = mount(() => new Promise(resolve => { finish = resolve; }));
  edit('viewer.add_im'); fire(button('Complete name'),'click');
  edit('newer = 12'); finish(answer()); await settleMicrotasks();
  expect(button('add_image')).toBeUndefined(); expect(editor.value).toBe('newer = 12');
  edit('viewer.add_im'); fire(button('Complete name'),'click'); help.reset();
  finish(answer()); await settleMicrotasks(); expect(button('add_image')).toBeUndefined();
});

test('completion inside an identifier replaces its remaining characters without duplicating them', async () => {
  mount(async () => answer()); edit('viewer.add_image(data)', 13);
  fire(button('Complete name'),'click'); await settleMicrotasks(); fire(button('add_image'),'click');
  expect(editor.value).toBe('viewer.add_image(data)');
});

test('moving the cursor prevents a delayed insertion and close discards only assistance', async () => {
  let finish!: (value: unknown) => void;
  const help = mount(() => new Promise(resolve => { finish = resolve; }));
  edit('viewer.add_im'); fire(button('Complete name'),'click');
  editor.setSelectionRange(0,0); finish(answer()); await settleMicrotasks();
  expect(button('add_image')).toBeUndefined();
  help.close(); expect(editor.value).toBe('viewer.add_im');
});

test('help renders text and refuses execution syntax before contacting a worker', async () => {
  const ask = vi.fn(async (_mode, expression) => answer(expression, {mode:'help',matches:[],text:'<script>documentation</script>'}));
  mount(ask); edit('viewer.clear()'); fire(button('Help for name'),'click'); await settleMicrotasks();
  expect(ask).not.toHaveBeenCalled();
  edit('viewer.add_image'); fire(button('Help for name'),'click'); await settleMicrotasks();
  expect(root.querySelector('script')).toBeNull(); expect(root.textContent).toContain('<script>documentation</script>');
});

test('failed and mismatched replies keep code and allow a corrected retry', async () => {
  const ask = vi.fn().mockResolvedValueOnce(answer('different')).mockResolvedValueOnce(answer());
  mount(ask); edit('viewer.add_im'); fire(button('Complete name'),'click'); await settleMicrotasks();
  expect(root.textContent).toContain('invalid assistance'); expect(editor.value).toBe('viewer.add_im');
  fire(button('Complete name'),'click'); await settleMicrotasks(); expect(button('add_image')).toBeDefined();
});

test('unavailable sessions cannot query or insert suggestions, and responses are bounded', async () => {
  const ask = vi.fn(async () => answer()); const help = mount(ask);
  edit('viewer.add_im'); help.setAvailable(false); fire(button('Complete name'),'click');
  expect(ask).not.toHaveBeenCalled(); help.setAvailable(true);
  fire(button('Complete name'),'click'); await settleMicrotasks(); help.setAvailable(false);
  fire(button('add_image'),'click'); expect(editor.value).toBe('viewer.add_im');
  for (const bad of [answer(undefined,{text:'x'.repeat(8193)}), answer(undefined,{matches:['call()']}), answer(undefined,{matches:Array(65).fill('name')})])
    expect(() => pythonAssistance(bad,'complete','viewer.add_im')).toThrow();
});
