import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { syntheticDOM } from './workspace-windows.test-support';
import { showScientificProvenance } from './scientific-provenance';
import { showScientificHistory } from './scientific-history';
beforeEach(() => vi.stubGlobal('document', syntheticDOM().document));
afterEach(() => vi.unstubAllGlobals());
const ref = (id: string, revision: number) => ({ id: 'p' + id.repeat(32), session: 's1', revision, digest: id.repeat(64) });
const state = () => ({ hookeEdit: { format: 1, base: ref('a', 2), input: {}, output: {}, producer: { history: ref('b', 4) } } });
test('saved output opens only its exact producing history and base, without running code', () => {
  const root = document.createElement('div'), open = vi.fn();
  showScientificProvenance(root, state(), open);
  expect(open).not.toHaveBeenCalled();
  const buttons = root.querySelectorAll('button');
  buttons[1].dispatchEvent(new Event('click'));
  expect(open).toHaveBeenLastCalledWith({ id: 'p' + 'b'.repeat(32), day: 's1', version: 4, picture: null }, 'Producing scientific history');
  buttons[0].dispatchEvent(new Event('click'));
  expect(open).toHaveBeenLastCalledWith({ id: 'p' + 'a'.repeat(32), day: 's1', version: 2, picture: null }, 'Original scientific input');
});
test('legacy work gets no invented provenance; bad producer links never become buttons', () => {
  const root = document.createElement('div'), open = vi.fn();
  showScientificProvenance(root, {}, open); expect(root.textContent).toBe('');
  const invalid = state(); invalid.hookeEdit.producer.history.revision = 0;
  expect(() => showScientificProvenance(root, invalid, open)).toThrow('invalid saved history');
  expect(root.querySelectorAll('button')).toHaveLength(0);
});
test('manual action history preserves label precision and displays code as inert text', () => {
  const root = document.createElement('div');
  showScientificHistory(root, { format: 'scientific-python-history-v1', input: ref('a', 2), cells: [],
    actions: [{ state: 'completed', request: { op: 'labels', label: '9007199254740993', note: '<script>bad()</script>' }, context: {}, outcome: { changed: true } }] });
  expect(root.textContent).toContain('9007199254740993');
  expect(root.textContent).toContain('<script>bad()</script>');
  expect(root.querySelectorAll('script')).toHaveLength(0);
});
test('known undispatched source is never described as having run', () => {
  const root = document.createElement('div');
  showScientificHistory(root, { format: 'scientific-python-history-v1', input: ref('a', 2),
    cells: [{ state: 'not-dispatched', source: 'never_run()' }], actions: [] });
  expect(root.textContent).toContain('never sent to the worker');
  expect(root.textContent).not.toContain('may have run');
});

test('keeps every producing history for an atomic multi-layer save', () => {
  const first = state().hookeEdit;
  const second = { ...state().hookeEdit, producer: { history: ref('c', 5) } };
  const root = document.createElement('div'), open = vi.fn();
  showScientificProvenance(root, { hookeEdit: { format: 2, transaction: 'd'.repeat(64), changes: [first, second] } }, open);
  expect(root.querySelectorAll('details')).toHaveLength(2);
  expect(root.textContent).toContain('revision 4');
  expect(root.textContent).toContain('revision 5');
});

test('mixed new outputs retain one exact producing history and reject incomplete sets',()=>{
  const root=document.createElement('div'),open=vi.fn();
  const output={format:2,base:ref('a',2),inputs:[],producer:{history:ref('b',4)},
    outputs:[{output:{},meaning:{},components:{},calibration:{}},{output:{},meaning:{},components:{},calibration:{}}]};
  showScientificProvenance(root,{hookeOutput:output},open);
  expect(root.textContent).toContain('How these outputs were saved together');
  expect(open).not.toHaveBeenCalled();
  root.querySelectorAll('button')[1].dispatchEvent(new Event('click'));
  expect(open).toHaveBeenLastCalledWith({id:'p'+'b'.repeat(32),day:'s1',version:4,picture:null},'Open producing history');
  for(const invalid of [{...output,outputs:[]},{...output,format:'2'},{...output,outputs:[{},{}]}])
    expect(()=>showScientificProvenance(document.createElement('div'),{hookeOutput:invalid},open)).toThrow('unsupported');
});

test('reopened reader candidates identify exact integration and retain an inert original-source link',()=>{
  const open=vi.fn();
  for(const [capability,generation] of [['mine/reader-a#read','a'.repeat(16)],['mine/reader-b#read','b'.repeat(16)]]){
    const root=document.createElement('section');
    showScientificProvenance(root,{sourceReader:{format:1,capability,generation,source:ref('c',1)}},open);
    expect(root.textContent).toContain(capability);expect(root.textContent).toContain(generation);
    expect(root.textContent).toContain('private candidate');
    root.querySelector('button')!.dispatchEvent(new Event('click'));
    expect(open).toHaveBeenLastCalledWith({id:'p'+'c'.repeat(32),day:'s1',version:1,picture:null},'Original scientific source');
  }
});

// "How this was made": the panel reads GET /api/scientific-projects/{id}/provenance for the exact saved revision.
const exact = (id: string, revision: number) => ({ id: 'p' + id.repeat(32), session: 's1', revision, digest: id.repeat(64) });
const source = { id: 'p' + 'e'.repeat(32), day: 's1', version: 3 };
const cell = (code: string) => ({ id: 'cell-first-kernel0000', source: code, state: 'completed', at: 1,
  transcript: { format: 'scientific-console-v1', sequence: 1, stdout: '', stderr: '', result: '', error: '', truncated: false, privatePreview: true } });
const paint = { id: 'gesture-123456789012', state: 'completed', at: 1,
  request: { op: 'labels', request: 'gesture-123456789012', tool: 'paint', label: '9', brush: 1, points: [[1, 4]] },
  context: { dimensions: { order: [0, 1] }, camera: { zoom: 2 } }, outcome: { edit: { state: 'applied', changed: true, label: '9' } } };
const answer = (overrides: Record<string, unknown> = {}, edit: Record<string, unknown> = {}) => ({ ok: true, provenance: {
  format: 1, record: { id: source.id, session: 's1', revision: 3, digest: 'e'.repeat(64) }, runtimes: [], recorded: true,
  outputs: null, copy: null, problems: [], replayable: false, acceptedAt: 3, ...overrides,
  edits: [{ base: exact('a', 2), input: {}, output: { id: source.id, component: 'array.labels' }, replayable: false,
    changed: { component: 'array.labels', axes: ['y', 'x'], granularity: 'chunk', chunks: 4, of: 4, keys: [], truncated: false, bounds: [[0, 4], [0, 6]] },
    producer: { recorded: true, kind: 'scientific-session', kernel: 'first-kernel', actor: 'studio', origin: {},
      history: exact('b', 4), cells: [cell('labels.data[:] = 11')], actions: [] }, ...edit }] } });
const reply = (body: unknown) => vi.fn(async () => ({ json: async () => body }) as unknown as Response);

test('how this was made shows the verified producer, its own code and the exact changed region', async () => {
  const root = document.createElement('div'), open = vi.fn(), request = reply(answer());
  await showScientificProvenance(root, state(), open, source, request);
  expect(request).toHaveBeenCalledWith(`/api/scientific-projects/${source.id}/provenance?session=s1&revision=3`);
  const made = root.querySelector('.scientific-made')!;
  expect(made.textContent).toContain('Checked against the saved records. Nothing was rerun to show this.');
  expect(made.textContent).toContain('These values were accepted in this revision (3).');
  expect(made.textContent).toContain('Change to labels');
  expect(made.textContent).toContain('4 of 4 stored blocks changed, within y 0–3, x 0–5.');
  expect(made.textContent).toContain('Made in scientific session “first-kernel”');
  expect([...made.querySelectorAll('pre')].map(p => p.textContent)).toContain('labels.data[:] = 11');
  const buttons = [...made.querySelectorAll('button')];
  buttons.find(b => b.textContent === 'Open producing history · revision 4')!.dispatchEvent(new Event('click'));
  expect(open).toHaveBeenLastCalledWith({ id: 'p' + 'b'.repeat(32), day: 's1', version: 4, picture: null }, 'Producing scientific history');
  buttons.find(b => b.textContent === 'Open original · revision 2')!.dispatchEvent(new Event('click'));
  expect(open).toHaveBeenLastCalledWith({ id: 'p' + 'a'.repeat(32), day: 's1', version: 2, picture: null }, 'Original scientific input');
});

test('a conflict copy of a manual edit names the copy, the paint action and its one changed block', async () => {
  const root = document.createElement('div');
  const body = answer({ acceptedAt: 1, copy: { conflictCopy: true, steps: [] } }, {
    changed: { component: 'array.labels', axes: ['y', 'x'], granularity: 'chunk', chunks: 1, of: 4, keys: ['0,1'], truncated: false, bounds: [[0, 2], [3, 6]] },
    producer: { recorded: true, kind: 'scientific-session', kernel: 'manual-editor', history: exact('c', 3), cells: [], actions: [paint] } });
  await showScientificProvenance(root, state(), vi.fn(), source, reply(body));
  const text = root.querySelector('.scientific-made')!.textContent;
  expect(text).toContain('Saved as a separate copy because its original changed first. The original was not altered.');
  expect(text).toContain('These values were accepted in revision 1; this revision kept them unchanged.');
  expect(text).toContain('1 of 4 stored blocks changed, within y 0–1, x 3–5.');
  expect(text).toContain('"tool": "paint"');
  expect(text).toContain('labels · completed');
});

test('unconfirmed records are listed and no code is shown in their place', async () => {
  const root = document.createElement('div');
  const body = answer({ problems: ['The producing history could not be read; nothing else was substituted for it'] },
    { producer: { recorded: true, kind: 'scientific-session', kernel: 'first-kernel', history: exact('b', 4), cells: null, actions: null } });
  await showScientificProvenance(root, state(), vi.fn(), source, reply(body));
  const made = root.querySelector('.scientific-made')!;
  expect(made.querySelector('li')!.textContent).toBe('The producing history could not be read; nothing else was substituted for it');
  expect(made.textContent).toContain('Some saved records could not be confirmed. Nothing else was put in their place:');
  expect(made.textContent).toContain('Its code and actions are not shown because the producing history could not be confirmed.');
  expect(made.querySelectorAll('pre')).toHaveLength(0);
  expect(made.querySelector('a')).toBeNull();  // a record with problems is not offered for export
});

test('values saved without a recorded producer say so', async () => {
  const root = document.createElement('div');
  await showScientificProvenance(root, state(), vi.fn(), source,
    reply(answer({}, { producer: { recorded: false, details: 'Producing actions were not recorded by this caller' } })));
  expect(root.querySelector('.scientific-made')!.textContent).toContain('Producing actions were not recorded by this caller');
});

test('a refused or mismatched answer is said plainly and never throws', async () => {
  for (const body of [{ ok: false, says: 'The project session is not valid.' },
                      answer({ record: { id: source.id, session: 's1', revision: 2, digest: 'e'.repeat(64) } })]) {
    const root = document.createElement('div');
    await expect(showScientificProvenance(root, state(), vi.fn(), source, reply(body))).resolves.toBeUndefined();
    const text = root.querySelector('.scientific-made')!.textContent;
    expect(text).toContain('How this was made could not be checked:');
    expect(text).toContain('The saved values are unchanged.');
  }
  const root = document.createElement('div');
  await showScientificProvenance(root, state(), vi.fn(), source, vi.fn(async () => { throw new Error('offline'); }));
  expect(root.querySelector('.scientific-made')!.textContent).toContain('could not be checked: offline');
});

test('nothing is fetched without the exact saved reference or without any saved record', () => {
  const request = reply(answer());
  expect(showScientificProvenance(document.createElement('div'), state(), vi.fn())).toBeUndefined();
  expect(showScientificProvenance(document.createElement('div'), { note: 'plain' }, vi.fn(), source, request)).toBeUndefined();
  expect(request).not.toHaveBeenCalled();
});

test('producing code from the server stays inert text', async () => {
  const root = document.createElement('div');
  await showScientificProvenance(root, state(), vi.fn(), source,
    reply(answer({}, { producer: { recorded: true, kernel: 'k', history: exact('b', 4), cells: [cell('<img src=x onerror=bad()>')], actions: [] } })));
  expect(root.querySelector('.scientific-made')!.querySelector('pre')!.textContent).toBe('<img src=x onerror=bad()>');
  expect(root.querySelectorAll('img')).toHaveLength(0);
});

test('the integration that ran and what was not recorded are said', async () => {
  const root = document.createElement('div');
  await showScientificProvenance(root, state(), vi.fn(), source, reply(answer({}, {
    runtimes: [{ bundle: 'napari-0.6', operation: 'labels', generation: 'a'.repeat(16) }],
    unrecorded: 'Randomness, the environment and any files or network the code used were not recorded.' })));
  const text = root.querySelector('.scientific-made')!.textContent;
  expect(text).toContain(`Integration napari-0.6 · operation labels · generation ${'a'.repeat(16)}`);
  expect(text).toContain('Randomness, the environment and any files or network the code used were not recorded.');
});

test('a producer record Hooke cannot check is shown as given, not as verified', async () => {
  const root = document.createElement('div');
  await showScientificProvenance(root, state(), vi.fn(), source, reply(answer({}, { producer: { recorded: true, verified: false,
    kind: 'test', record: { kind: 'test', seed: 42 }, details: 'Its producer recorded this as given. It names no saved history Hooke can check.' } })));
  const made = root.querySelector('.scientific-made')!;
  expect(made.textContent).toContain('It names no saved history Hooke can check.');
  expect(made.querySelector('pre')!.textContent).toContain('"seed": 42');
  expect(made.textContent).not.toContain('Made in scientific session');
});

test('the verified record can be exported for this exact revision', async () => {
  const root = document.createElement('div');
  await showScientificProvenance(root, state(), vi.fn(), source, reply(answer()));
  const link = root.querySelector('.scientific-made')!.querySelector('a')!;
  expect(link.textContent).toBe('Export with how it was made');
  expect(link.getAttribute('href')).toBe(`/api/scientific-projects/${source.id}/bundle?session=s1&revision=3`);
  expect(link.getAttribute('download')).toBe('');
  expect(root.textContent).toContain('Inputs or Data → Import native bundle');
  expect(root.textContent).not.toContain('there is no import control');
  expect(root.textContent).toContain('Legacy bundles without one are refused; keep the archive for offline recovery.');
});


test('new reader shows its captured name and keeps exact identity in a disclosure',()=>{
 const runtime={bundle:'mine/reader',generation:'a'.repeat(16),operation:'open'};
 const root=document.createElement('section');
 showScientificProvenance(root,{sourceFile:{name:'Cells.zarr'},scientificView:{runtime},
   sourceReader:{format:1,name:'OME-Zarr reader',sourceName:'Cells.zarr',capability:'mine/reader#open',generation:runtime.generation,source:ref('c',1)}},vi.fn());
 expect(root.querySelector('h3')?.textContent).toBe('Reader: OME-Zarr reader');
 expect(root.querySelector('details')?.textContent).toContain('mine/reader#open');
 expect(root.querySelector('details')?.textContent).toContain(runtime.generation);
});
