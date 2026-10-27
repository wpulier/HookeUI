import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ScientificControls, scientificPanel, type ScientificPanel } from './scientific-controls';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

const id = (part: string) => part.repeat(32);
const base = { label: 'Opacity', help: 'How strongly to show this layer', enabled: true, visible: true };
function panel(revision = 1, value = 1): ScientificPanel {
  return { format: 'scientific-controls-v1', panel: id('a'), revision, controls: [
    { ...base, id: id('b'), kind: 'number', min: 0, max: 1, step: null, value },
    { ...base, id: id('c'), label: 'Apply', help: '', kind: 'button' },
  ] };
}
let root: TestElement;
beforeEach(() => { const dom = syntheticDOM(); root = dom.host; vi.stubGlobal('document', dom.document); });
afterEach(() => vi.unstubAllGlobals());
const input = () => root.querySelector('input')!;
const button = (label: string) => root.querySelectorAll('button').find(button => button.textContent === label)!;
function edit(value: string) { input().value = value; fire(input(), 'input'); }
function deferred() { let resolve!: (value: unknown) => void; const promise = new Promise<unknown>(done => { resolve = done; }); return { resolve, promise }; }

test('snapshot validation refuses ambiguous, oversized or executable declarations', () => {
  expect(scientificPanel(panel())).toEqual(panel());
  for (const value of [null, { ...panel(), actor: 'human' }, { ...panel(), revision: true },
    { ...panel(), controls: [panel().controls[0], panel().controls[0]] },
    { ...panel(), controls: [{ ...panel().controls[0], value: Infinity }] },
    { ...panel(), controls: [{ ...panel().controls[0], help: 'a'.repeat(1001) }] },
    { ...panel(), controls: [{ ...panel().controls[1], callback: 'eval' }] },
  ]) expect(() => scientificPanel(value)).toThrow();
});

test('controls use the captured revision and show acknowledged values', async () => {
  const send = vi.fn(async () => panel(2, .4));
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: async () => panel(2, .4) });
  controls.update(panel());
  edit('.4'); fire(input(), 'change'); await settleMicrotasks();
  expect(send).toHaveBeenCalledWith({ panel: id('a'), revision: 1, control: id('b'), op: 'set', value: .4 });
  expect(input().value).toBe('0.4');
  expect(root.textContent).toContain('Applied.');
  expect(button('Apply').disabled).toBe(false);
});

test('polls preserve a draft and its element; newer settings require explicit recovery', () => {
  const send = vi.fn();
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: async () => panel() });
  controls.update(panel());
  const original = input(); edit('.35'); controls.update(panel());
  expect(input()).toBe(original); expect(input().value).toBe('.35');
  controls.update(panel(2, .6));
  expect(input()).toBe(original); expect(input().value).toBe('.35');
  fire(input(), 'change'); fire(button('Apply'), 'click');
  expect(send).not.toHaveBeenCalled();
  expect(root.textContent).toContain('unsent values are kept');
  fire(button('Discard unsent changes'), 'click');
  expect(input().value).toBe('0.6'); expect(button('Apply').disabled).toBe(false);
});

test('a delayed acknowledgement cannot rewind a newer worker snapshot', async () => {
  const reply = deferred(), send = vi.fn(() => reply.promise);
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: async () => panel(3, .8) });
  controls.update(panel()); edit('.4'); fire(input(), 'change');
  fire(button('Apply'), 'click'); expect(send).toHaveBeenCalledTimes(1);
  controls.update(panel(3, .8)); reply.resolve(panel(2, .4)); await settleMicrotasks();
  expect(input().value).toBe('0.8');
});

test('a lost callback acknowledgement never retries automatically', async () => {
  const send = vi.fn(async () => { throw new Error('connection lost'); });
  const refresh = vi.fn(async () => panel(2, .4));
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh });
  controls.update(panel()); edit('.4'); fire(input(), 'change'); await settleMicrotasks();
  expect(root.textContent).toContain('It may have run'); expect(input().value).toBe('.4');
  fire(button('Apply'), 'click'); expect(send).toHaveBeenCalledTimes(1);
  fire(button('Check current settings'), 'click'); await settleMicrotasks();
  expect(refresh).toHaveBeenCalledTimes(1); expect(send).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('unsent values are kept');
});

test('closed panels ignore late replies and another panel identity is refused', async () => {
  const reply = deferred();
  const controls = new ScientificControls(root as unknown as HTMLElement, { send: () => reply.promise, refresh: async () => panel() });
  controls.update(panel());
  expect(() => controls.update({ ...panel(), panel: id('f') })).toThrow('another scientific panel');
  edit('.4'); fire(input(), 'change'); controls.close(); reply.resolve(panel(2, .4)); await settleMicrotasks();
  expect(root.children).toHaveLength(0);
});

test('invalid numeric edits cannot become omitted defaults', async () => {
  const send = vi.fn();
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: async () => panel() });
  controls.update(panel());
  for (const value of ['', 'Infinity', '-1', '1.1']) { edit(value); fire(input(), 'change'); await settleMicrotasks(); }
  expect(send).not.toHaveBeenCalled(); expect(root.textContent).toContain('Enter a valid value');
});

test('choice values are opaque and old or duplicate options are refused', () => {
  const choice = { ...base, id: id('d'), kind: 'choice' as const, value: id('e'), choices: [{ id: id('e'), label: 'Layer one' }] };
  expect(scientificPanel({ ...panel(), controls: [choice] }).controls).toEqual([choice]);
  for (const changed of [{ ...choice, value: 'Layer one' }, { ...choice, choices: [...choice.choices, ...choice.choices] }])
    expect(() => scientificPanel({ ...panel(), controls: [changed] })).toThrow();
});

test('conflicting acknowledgements preserve the submitted draft', async () => {
  const reply = deferred();
  const controls = new ScientificControls(root as unknown as HTMLElement, { send: () => reply.promise, refresh: async () => panel(2, .8) });
  controls.update(panel()); edit('.4'); fire(input(), 'change');
  controls.update(panel(2, .8)); reply.resolve(panel(2, .4)); await settleMicrotasks();
  expect(input().value).toBe('.4'); expect(root.textContent).toContain('Unsent Opacity: .4');
  expect(root.textContent).toContain('It may have run'); expect(button('Apply').disabled).toBe(true);
});

test('a stale refresh cannot clear callback uncertainty', async () => {
  const reply = deferred();
  const send = vi.fn(async () => { throw new Error('lost'); });
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: () => reply.promise });
  controls.update(panel()); fire(button('Apply'), 'click'); await settleMicrotasks();
  fire(button('Check current settings'), 'click'); controls.update(panel(3, .8));
  reply.resolve(panel(2, .4)); await settleMicrotasks();
  expect(root.textContent).toContain('could not be checked'); expect(button('Apply').disabled).toBe(true);
  fire(button('Apply'), 'click'); expect(send).toHaveBeenCalledTimes(1);
});

test('numeric increments and dynamic help update without replacing the focused input', () => {
  const controls = new ScientificControls(root as unknown as HTMLElement, { send: vi.fn(), refresh: async () => panel() });
  const initial = panel(); initial.controls[0] = { ...base, id: id('b'), kind: 'number', min: 0, max: 1, step: .05, value: .5 };
  controls.update(initial); const original = input();
  expect((original as unknown as HTMLInputElement).step).toBe('0.05');
  const next = panel(2); next.controls[0] = { ...initial.controls[0], help: 'Updated help', min: .2, step: .1 };
  controls.update(next);
  expect(input()).toBe(original); expect((original as unknown as HTMLInputElement).step).toBe('0.1');
  expect(original.title).toBe('Updated help');
});

test('a removed control leaves its unsent value visible for recovery', () => {
  const controls = new ScientificControls(root as unknown as HTMLElement, { send: vi.fn(), refresh: async () => panel() });
  controls.update(panel()); edit('.4'); controls.update({ ...panel(2), controls: [panel().controls[1]] });
  expect(root.textContent).toContain('Unsent Opacity: .4'); expect(button('Apply').disabled).toBe(true);
});


test('replacement sessions preserve unsent values and ignore callbacks from the retired panel', async () => {
  const reply = deferred(), send = vi.fn(() => reply.promise);
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: async () => panel() });
  controls.bindSession('old'); controls.update(panel()); edit('.4'); fire(input(), 'change');
  controls.bindSession('new'); controls.update({ ...panel(), panel: id('f') });
  reply.resolve(panel(2, .4)); await settleMicrotasks();
  expect(root.textContent).toContain('Unsent Opacity: .4'); expect(button('Apply').disabled).toBe(true);
  fire(button('Discard unsent changes'), 'click'); expect(button('Apply').disabled).toBe(false);
  expect(input().value).toBe('1');
});


test('replacement startup makes old controls inert while preserving drafts', () => {
  const send = vi.fn();
  const controls = new ScientificControls(root as unknown as HTMLElement, { send, refresh: async () => panel() });
  controls.bindSession('old'); controls.update(panel()); edit('.4');
  controls.bindSession('new');
  expect(input().disabled).toBe(true); expect(button('Apply').disabled).toBe(true);
  expect(button('Check current settings').disabled).toBe(true);
  fire(input(), 'input'); fire(input(), 'change'); fire(button('Apply'), 'click');
  expect(send).not.toHaveBeenCalled(); expect(input().value).toBe('.4');
  controls.update({ ...panel(), panel: id('f') });
  expect(root.textContent).toContain('Unsent Opacity: .4');
});


test('invalidated previews allow only an explicitly admitted settings refresh', async () => {
  const send=vi.fn(), refresh=vi.fn(async()=>panel(2,.4));
  const controls=new ScientificControls(root as unknown as HTMLElement,{send,refresh});
  controls.bindSession('private-session');controls.update(panel());
  controls.setAvailable(false,true);
  expect(input().disabled).toBe(true);expect(button('Apply').disabled).toBe(true);
  expect(button('Check current settings').disabled).toBe(false);
  fire(button('Check current settings'),'click');await settleMicrotasks();
  expect(refresh).toHaveBeenCalledTimes(1);expect(send).not.toHaveBeenCalled();
  controls.suspend();expect(button('Check current settings').disabled).toBe(true);
  fire(button('Check current settings'),'click');await settleMicrotasks();
  expect(refresh).toHaveBeenCalledTimes(1);
});
