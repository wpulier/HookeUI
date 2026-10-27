// Synthetic UI/transport regressions. Actual kernel and browser evidence are separate gates.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ScientificConsole, PythonNotSent, pythonTranscript, type PythonTranscript } from './scientific-console';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';

let root: TestElement;
beforeEach(() => { const dom = syntheticDOM(); root = dom.host; vi.stubGlobal('document', dom.document); });
afterEach(() => vi.unstubAllGlobals());
const input = () => root.querySelector('textarea')!;
const button = (text: string) => root.querySelectorAll('button').find(row => row.textContent === text)!;
function edit(code: string) { input().value = code; fire(input(), 'input'); }
function transcript(sequence = 1, change = {}): PythonTranscript {
  return { format: 'scientific-console-v1', sequence, stdout: '', stderr: '', result: '7', error: '', truncated: false, privatePreview: true, ...change };
}
function deferred() { let resolve!: (value: unknown) => void; const promise = new Promise<unknown>(done => { resolve = done; }); return { resolve, promise }; }
const mount = (execute: (source: string) => Promise<unknown> = vi.fn(async () => transcript()), stop: () => Promise<void> = vi.fn(async () => {})) =>
  new ScientificConsole(root as unknown as HTMLElement, { execute, stop });

test('code requires an explicit press, while incoming replies preserve the newer draft and input element', async () => {
  const result = deferred(), execute = vi.fn(() => result.promise), console = mount(execute);
  const original = input(); edit('saved = 7\nsaved');
  expect(execute).not.toHaveBeenCalled(); fire(button('Run code'), 'click');
  edit('saved + 2'); fire(button('Run code'), 'click');
  expect(execute).toHaveBeenCalledExactlyOnceWith('saved = 7\nsaved');
  result.resolve(transcript()); await settleMicrotasks();
  expect(input()).toBe(original); expect(input().value).toBe('saved + 2');
  expect(console.snapshot().cells[0].source).toBe('saved = 7\nsaved');
  expect(console.snapshot().pending).toBeNull();
  expect(root.textContent).toContain('Changes remain in the private view');
});

test('Python errors and HTML-looking output remain text with an explicit correction path', async () => {
  const execute = vi.fn(async () => transcript(1, { stdout: '<img src=x onerror=bad()>', error: 'ValueError: bad range', truncated: true }));
  mount(execute); edit('raise ValueError()'); fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(root.querySelector('img')).toBeNull(); expect(root.textContent).toContain('<img src=x onerror=bad()>');
  expect(root.textContent).toContain('correct your code'); expect(root.textContent).toContain('Output was shortened');
  expect(button('Run code').disabled).toBe(false);
});

test('uncertain execution keeps submitted code and newer draft without retry or false success', async () => {
  const execute = vi.fn(async () => { throw new Error('connection lost'); });
  const console = mount(execute); edit('viewer.layers.clear()'); fire(button('Run code'), 'click'); edit('print("new draft")');
  await settleMicrotasks(); fire(button('Run code'), 'click');
  expect(execute).toHaveBeenCalledTimes(1); expect(button('Run code').disabled).toBe(true);
  expect(console.snapshot().pending).toEqual({ source: 'viewer.layers.clear()', state: 'unknown' });
  expect(console.snapshot().draft).toBe('print("new draft")');
  expect(root.textContent).toContain('code may have run');
});

test('Stop owns only its transport and late execution replies cannot revive a stopped kernel', async () => {
  const result = deferred(), stop = vi.fn(async () => {}), console = mount(vi.fn(() => result.promise), stop);
  edit('while True: pass'); fire(button('Run code'), 'click'); fire(button('Stop Python session'), 'click');
  await settleMicrotasks(); result.resolve(transcript()); await settleMicrotasks();
  expect(stop).toHaveBeenCalledTimes(1); expect(console.snapshot().stopped).toBe(true);
  expect(console.snapshot().cells).toHaveLength(1);
  expect(console.snapshot().pending).toBeNull();
  expect(button('Run code').disabled).toBe(true); expect(root.textContent).toContain('Saved Project data');
});

test('failed Stop remains retryable and never enables another execution', async () => {
  const stop = vi.fn().mockRejectedValueOnce(new Error('not reaped')).mockResolvedValueOnce(undefined);
  const console = mount(undefined, stop); edit('3'); fire(button('Stop Python session'), 'click'); await settleMicrotasks();
  expect(console.snapshot().stopped).toBe(false); expect(root.textContent).toContain('Retry Stop');
  expect(button('Run code').disabled).toBe(true);
  fire(button('Stop Python session'), 'click'); await settleMicrotasks();
  expect(console.snapshot().stopped).toBe(true); expect(stop).toHaveBeenCalledTimes(2);
});

test('closing presentation does not stop work and a late reply cannot change the closed UI', async () => {
  const result = deferred(), stop = vi.fn(async () => {}), console = mount(vi.fn(() => result.promise), stop);
  edit('3'); fire(button('Run code'), 'click'); console.close(); result.resolve(transcript()); await settleMicrotasks();
  expect(stop).not.toHaveBeenCalled(); expect(root.children).toHaveLength(0);
  expect(console.snapshot().cells[0].source).toBe('3');
  expect(console.snapshot().pending).toBeNull();
});

test('closing during Stop retains its settled state without repainting', async () => {
  const stopped = deferred();
  const console = mount(undefined, async () => { await stopped.promise; });
  fire(button('Stop Python session'), 'click'); console.close(); stopped.resolve(undefined); await settleMicrotasks();
  expect(console.snapshot().stopped).toBe(true); expect(root.children).toHaveLength(0);
});

test('history has a bounded allowance and reaching it never discards prior cells', async () => {
  let sequence = 0;
  const execute = vi.fn(async () => transcript(++sequence, { stdout: 'x'.repeat(16384), stderr: 'y'.repeat(16384), result: 'z'.repeat(16384), error: 'e'.repeat(16384) }));
  const console = mount(execute); edit('3');
  for (let i = 0; i < 25; i++) { fire(button('Run code'), 'click'); await settleMicrotasks(); }
  const saved = console.snapshot();
  expect(saved.cells.length).toBeGreaterThan(1); expect(saved.cells.length).toBeLessThan(25);
  expect(execute).toHaveBeenCalledTimes(saved.cells.length);
  expect(saved.cells.map(cell => cell.transcript.sequence)).toEqual(Array.from({ length: saved.cells.length }, (_, i) => i + 1));
  expect(root.textContent).toContain('history limit'); expect(button('Run code').disabled).toBe(true);
});

test('the session cell limit refuses another send while retaining code and allowing a fresh session', async () => {
  let sequence = 0;
  const execute = vi.fn(async () => transcript(++sequence));
  const console = mount(execute); console.bindSession('full-session'); edit('print(1)');
  for (let i = 0; i < 128; i++) { fire(button('Run code'), 'click'); await settleMicrotasks(); }
  expect(execute).toHaveBeenCalledTimes(128);
  expect(button('Run code').disabled).toBe(true);
  expect(root.textContent).toContain('128 cells');
  edit('keep_this_draft = 2'); fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(execute).toHaveBeenCalledTimes(128);
  expect(console.snapshot().cells).toHaveLength(128);
  expect(console.snapshot().draft).toBe('keep_this_draft = 2');
  expect(console.snapshot().uncertain).toBe(false);
  console.bindSession('fresh-session'); sequence = 0;
  expect(button('Run code').disabled).toBe(false);
  expect(execute).toHaveBeenCalledTimes(128);
  fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(execute).toHaveBeenCalledTimes(129);
  expect(console.snapshot().cells.at(-1)?.session).toBe('fresh-session');
  expect(console.snapshot().cells.at(-1)?.transcript.sequence).toBe(1);
});

test('oversized UTF-8 source is refused and forged or replayed transcripts cannot be accepted', async () => {
  const execute = vi.fn(async () => transcript()); mount(execute);
  edit('🎥'.repeat(256 * 1024 / 4 + 1)); fire(button('Run code'), 'click'); expect(execute).not.toHaveBeenCalled();
  edit('3'); fire(button('Run code'), 'click'); await settleMicrotasks();
  fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(root.textContent).toContain('No confirmed Python result');
  for (const raw of [null, { ...transcript(), privatePreview: false }, { ...transcript(), sequence: true },
    { ...transcript(), stdout: '🎥'.repeat(5000) }, { ...transcript(), execute: 'malicious' }])
    expect(() => pythonTranscript(raw)).toThrow();
});

test('a new kernel preserves history and draft without replaying code or accepting the old sequence as its own', async () => {
  const pending = deferred(), execute = vi.fn().mockImplementationOnce(() => pending.promise).mockResolvedValueOnce(transcript(1));
  const console = mount(execute);
  console.bindSession('kernel-one'); edit('old_value = 7'); fire(button('Run code'), 'click');
  console.bindSession('kernel-two'); edit('new_value = 9'); fire(button('Run code'), 'click'); await settleMicrotasks();
  pending.resolve(transcript(8)); await settleMicrotasks();
  expect(execute).toHaveBeenCalledTimes(2);
  expect(console.snapshot().cells.map(cell => cell.session)).toEqual(['kernel-two','kernel-one']);
  expect(console.snapshot().draft).toBe('new_value = 9');
  expect(console.snapshot().pending).toBeNull();
  execute.mockResolvedValueOnce(transcript(2)); edit('new_value'); fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(console.snapshot().cells.at(-1)?.transcript.sequence).toBe(2);
  expect(console.snapshot().uncertain).toBe(false);
});


test('an old Stop acknowledgement cannot disable a replacement kernel', async () => {
  const pending = deferred(), console = mount(undefined, async () => { await pending.promise; });
  console.bindSession('kernel-one'); fire(button('Stop Python session'), 'click');
  console.bindSession('kernel-two'); edit('7');
  pending.resolve(undefined); await settleMicrotasks();
  expect(console.snapshot().stopped).toBe(false); expect(button('Run code').disabled).toBe(false);
  fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(console.snapshot().cells[0].session).toBe('kernel-two');
});

test('confirmed undispatched cell restores editing without replay or loss of newer draft', async () => {
  const execute = vi.fn(async () => { throw new Error('history admission failed'); });
  const console = mount(execute); console.bindSession('kernel-one');
  edit('never_run()'); fire(button('Run code'), 'click'); await settleMicrotasks();
  edit('corrected_draft()');
  console.notDispatched('other-kernel', 'never_run()');
  console.notDispatched('kernel-one', 'unrelated_source()');
  expect(button('Run code').disabled).toBe(true);
  console.notDispatched('kernel-one', 'never_run()');
  expect(button('Run code').disabled).toBe(false);
  expect(console.snapshot().draft).toBe('corrected_draft()');
  expect(console.snapshot().pending).toBeNull();
  expect(execute).toHaveBeenCalledTimes(1);
  expect(root.textContent).toContain('This code was not run');
});

test('interrupt keeps the running cell and draft until its confirmed result, without stopping the session', async () => {
  const running=deferred(), interrupt=vi.fn(async()=>true), stop=vi.fn(async()=>{});
  const console=new ScientificConsole(root as unknown as HTMLElement,{execute:()=>running.promise,stop,interrupt});
  edit('saved = 7\nwhile True: pass');fire(button('Run code'),'click');
  edit('saved + 1');fire(button('Interrupt code'),'click');await settleMicrotasks();
  expect(interrupt).toHaveBeenCalledTimes(1);expect(stop).not.toHaveBeenCalled();
  expect(root.textContent).toContain('Waiting for Python to respond');
  expect(button('Run code').disabled).toBe(true);
  running.resolve(transcript(1,{error:'KeyboardInterrupt: interrupted'}));await settleMicrotasks();
  expect(console.snapshot().stopped).toBe(false);expect(console.snapshot().draft).toBe('saved + 1');
  expect(root.textContent).toContain('Variables and partial changes remain');
  expect(button('Run code').disabled).toBe(false);expect(button('Interrupt code').disabled).toBe(true);
});

test('a late interrupt acknowledgement cannot replace a completed cell status',async()=>{
  const running=deferred(), interrupted=deferred();
  new ScientificConsole(root as unknown as HTMLElement,{execute:()=>running.promise,stop:async()=>{},interrupt:async()=>Boolean(await interrupted.promise)});
  edit('3');fire(button('Run code'),'click');fire(button('Interrupt code'),'click');
  running.resolve(transcript());await settleMicrotasks();
  interrupted.resolve(true);await settleMicrotasks();
  expect(root.textContent).toContain('Finished. Changes remain');
  expect(root.textContent).not.toContain('Waiting for Python');
});

test('an interrupt acknowledgement from an old kernel cannot alter a new kernel execution',async()=>{
  const oldRun=deferred(), newRun=deferred(), oldInterrupt=deferred();
  const execute=vi.fn().mockImplementationOnce(()=>oldRun.promise).mockImplementationOnce(()=>newRun.promise);
  const console=new ScientificConsole(root as unknown as HTMLElement,{execute,stop:async()=>{},interrupt:async()=>Boolean(await oldInterrupt.promise)});
  console.bindSession('first');edit('while True: pass');fire(button('Run code'),'click');fire(button('Interrupt code'),'click');
  console.bindSession('second');edit('7');fire(button('Run code'),'click');
  oldInterrupt.resolve(true);await settleMicrotasks();
  expect(root.textContent).toContain('Running Python');expect(root.textContent).not.toContain('Waiting for Python');
  expect(button('Interrupt code').disabled).toBe(false);
  newRun.resolve(transcript());oldRun.resolve(transcript(1,{error:'KeyboardInterrupt: stopped'}));await settleMicrotasks();
  expect(root.textContent).toContain('Finished. Changes remain');
});

test('a cell the owner refused before sending says it was not run and leaves Run code usable', async () => {
  let refuse = true;
  const execute = vi.fn(async () => { if (refuse) throw new PythonNotSent('Wait for the current scientific operation to finish.'); return transcript(); });
  const console = mount(execute); edit('print(1)'); fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(root.textContent).toContain('This code was not run; your draft is kept');
  expect(root.textContent).not.toContain('code may have run');
  expect(console.snapshot().pending).toBe(null); expect(console.snapshot().uncertain).toBe(false);
  expect(button('Run code').disabled).toBe(false);
  refuse = false; fire(button('Run code'), 'click'); await settleMicrotasks();
  expect(execute).toHaveBeenCalledTimes(2); expect(console.snapshot().cells).toHaveLength(1);
});
