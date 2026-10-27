// Actual ModelsPage polling and form handlers over the existing synthetic DOM. Browser focus/layout is a later check.
import { afterEach, expect, test, vi } from 'vitest';
import { ModelsPage, type ModelsPageCommands } from './modelspage';
import type { CapabilitiesWire, ModelRow } from './protocol';
import type { VolumeEntry } from './volumespanel';
import { fire, TestElement } from './workspace-windows.test-support';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function mount() {
  vi.useFakeTimers(); vi.setSystemTime(100_000);
  let focused: Field | null = null;
  class Field extends TestElement {
    selectionStart: number | null = 0;
    selectionEnd: number | null = 0;
    selectionDirection: 'forward' | 'backward' | 'none' = 'none';
    focus() { focused = this; }
    setSelectionRange(start: number, end: number, direction: 'forward' | 'backward' | 'none') {
      this.selectionStart = start; this.selectionEnd = end; this.selectionDirection = direction;
    }
    override remove() {
      if (focused && this.contains(focused)) focused = null; // moving/replacing focused DOM can blur it
      super.remove();
    }
  }
  const root = new Field('main');
  vi.stubGlobal('document', { createElement: (tag: string) => new Field(tag), get activeElement() { return focused; } });
  vi.stubGlobal('window', globalThis);
  const model: ModelRow = { id: 'brainreg', name: 'brainreg', runs: 'this-mac', where: 'here', what: 'Registers volumes',
    kind: 'tool-function', version: '1', credit: {}, link: '', licence: '', tier: 'open', mine: false, status: 'ready',
    word: 'Ready', group: 'ready', next: null, steps: [], inUse: false, chosen: false, facts: [], usedFor: [], size: 0,
    files: [], folder: '', revision: '' };
  const wire: CapabilitiesWire = { type: 'capabilities', families: [], actors: { you: '', agent: '', auto: '', researcher: '' },
    capabilities: [], roles: [], preferences: [], pending: 0, build: 'test', agentText: '', models: [model] };
  const volumes: VolumeEntry[] = [{ id: 'v1', day: 'original-session', kind: 'volume', title: 'First brain', state: 'saved', summary: '',
    values: { facts: { series: { value: 0, source: 'file' } }, series: [{ axes: 'ZCYX', shape: [4, 6, 7], channels: 2, times: 2 }] } },
    { id: 'v2', day: 'original-session', kind: 'volume', title: 'Second brain', state: 'saved', summary: '', values: {
      facts: { series: { value: null, source: null } }, series: [{ axes: 'ZYX', shape: [4, 6, 7] }, { axes: 'ZYX', shape: [5, 7, 8] }] } },
    { id: 'j1', kind: 'job', title: 'Registration', state: 'running', summary: 'still registering' }];
  const describe = vi.fn(async () => undefined);
  const commands: ModelsPageCommands = { download: async () => undefined, reveal: async () => undefined,
    catalogUpdate: async () => undefined, channelAdd: async () => ({}), add: async () => ({}),
    volumes: { list: async () => structuredClone(volumes), add: async () => undefined, upload: async () => ({ ok: true }),
      describe, start: async () => undefined, cancel: async () => undefined, again: async () => undefined, open: () => undefined } };
  const page = new ModelsPage(root as unknown as HTMLElement, commands);
  page.update(wire);
  const field = (label: string, volume?: string) => {
    const found = ['input', 'select'].flatMap((tag) => root.querySelectorAll(tag)).find((n) =>
      n.getAttribute('aria-label') === label && (!volume || n.closest('[data-volume]')?.dataset.volume === volume));
    if (!found) throw new Error(`No field ${label}`);
    return found as Field;
  };
  return { root, page, wire, volumes, field, commands, describe, focused: () => focused };
}

test('polling preserves all unfinished fields, selectors and focus while job rows update', async () => {
  const { root, volumes, field, describe, focused } = mount();
  await vi.advanceTimersByTimeAsync(0);
  const drafts: [string, string, string?][] = [
    ['Voxel size z (µm)', '50', 'v1'], ['Voxel size y (µm)', '40', 'v1'], ['Voxel size x (µm)', '30', 'v1'],
    ['Orientation: BrainGlobe’s three letters', 'psl', 'v1'], ['Channel', '1', 'v1'], ['Time point', '1', 'v1'],
    ['Series', '1', 'v2'], ['A volume’s path on this Mac', '/private/brain.tif'],
    ['A community channel’s git address, or a repository on this Mac', '/private/catalog'], ['Its commit', 'abc123'],
    ['A link or a path to a model of your own', '/private/model.pt'],
  ];
  for (const [label, value, volume] of drafts) field(label, volume).value = value;
  const orient = field('Orientation: BrainGlobe’s three letters', 'v1');
  orient.focus(); orient.setSelectionRange(1, 2, 'forward');
  volumes[2] = { ...volumes[2], state: 'done', summary: 'registration completed' };
  await vi.advanceTimersByTimeAsync(2100);
  for (const [label, value, volume] of drafts) expect(field(label, volume).value, label).toBe(value);
  expect(focused()).toBe(field('Orientation: BrainGlobe’s three letters', 'v1'));
  expect([focused()?.selectionStart, focused()?.selectionEnd]).toEqual([1, 2]);
  expect(root.textContent).toContain('registration completed');
  const form = field('Channel', 'v1').closest('form');
  if (!form) throw new Error('No facts form');
  fire(form, 'submit');
  expect(describe).toHaveBeenCalledWith('v1', { session: 'original-session', voxelUm: [50, 40, 30], orientation: 'psl', channel: 1, time: 1 });
});

test('changed series facts or session ownership replace the old editor rather than carrying stale facts', async () => {
  const { volumes, field } = mount();
  await vi.advanceTimersByTimeAsync(0);
  field('Orientation: BrainGlobe’s three letters', 'v1').value = 'psl';
  volumes[0] = { ...volumes[0], day: 'another-session' };
  await vi.advanceTimersByTimeAsync(2100);
  expect(field('Orientation: BrainGlobe’s three letters', 'v1').value).toBe('');
  field('Orientation: BrainGlobe’s three letters', 'v1').value = 'asr';
  volumes[0].values!.facts!.series = { value: 1, source: 'user' };
  await vi.advanceTimersByTimeAsync(2100);
  expect(field('Orientation: BrainGlobe’s three letters', 'v1').value).toBe('');
});

test.each(['channel', 'model'] as const)('a pending %s add still clears its field and shows the outcome after polling', async (kind) => {
  const { root, commands, field } = mount();
  let finish: (value: { says: string }) => void = () => { throw new Error('No add began'); };
  const add = () => new Promise<{ says: string }>((resolve) => { finish = resolve; });
  if (kind === 'channel') commands.channelAdd = add; else commands.add = add;
  await vi.advanceTimersByTimeAsync(0);
  const label = kind === 'channel' ? 'A community channel’s git address, or a repository on this Mac' : 'A link or a path to a model of your own';
  field(label).value = '/private/new-entry';
  const form = field(label).closest('form');
  if (!form) throw new Error('No add form');
  fire(form, 'submit');
  await vi.advanceTimersByTimeAsync(2100);
  finish({ says: 'The new entry is ready for review' });
  await vi.advanceTimersByTimeAsync(0);
  expect(root.textContent).toContain('The new entry is ready for review');
  expect(field(label).value).toBe('');
  expect(form.querySelector('button')?.disabled).toBe(false);
});

// The Spec review of 87ff1e59: an Add volume pending in one session that ends after a switch to another said its words
// there (a success in a session holding no such volume). Its reply belongs to the session it was made in.
function inSessions() {
  const mounted = mount();
  let session = 'session-a';
  mounted.commands.session = () => session;
  const pending: { resolve(value: unknown): void; reject(error: Error): void }[] = [];
  mounted.commands.volumes!.add = () => new Promise((resolve, reject) => { pending.push({ resolve, reject }); });
  const status = () => mounted.root.querySelector('.volumes-section')?.querySelector('.models-own-said')?.textContent ?? '';
  const submit = (path: string) => {
    const input = mounted.field('A volume’s path on this Mac');
    input.value = path;
    fire(input.closest('form')!, 'submit');
    return input.closest('form')!;
  };
  const switchTo = (next: string) => { session = next; mounted.page.sessionChanged(); };
  return { ...mounted, pending, status, submit, switchTo };
}

test.each(['resolved', 'rejected'] as const)('an Add %s after a switch to another session says nothing there', async (outcome) => {
  const { pending, status, submit, switchTo, page } = inSessions();
  page.sessionChanged();  // the page shows session A
  await vi.advanceTimersByTimeAsync(0);
  submit('/a/brain.tif');
  expect(pending).toHaveLength(1);
  switchTo('session-b');
  await vi.advanceTimersByTimeAsync(0);
  if (outcome === 'resolved') pending[0].resolve({ ok: true, says: 'brain.tif: a volume, 3 × 4 × 5 (ZYX)' });
  else pending[0].reject(new Error('No file at /a/brain.tif'));
  await vi.advanceTimersByTimeAsync(0);
  expect(status()).toBe('');  // nothing of session A's Add in session B
});

test('a form kept from the session before says its Add in the session it was submitted in', async () => {
  const { pending, status, submit, switchTo, page } = inSessions();
  page.sessionChanged();
  await vi.advanceTimersByTimeAsync(0);
  const drawnInA = submit('/a/first.tif');
  switchTo('session-b');
  await vi.advanceTimersByTimeAsync(0);
  const submittedInB = submit('/b/second.tif');
  expect(submittedInB).toBe(drawnInA);  // the page keeps its editors across renders, a session switch included
  pending[1].resolve({ ok: true, says: 'second.tif: a volume, 4 × 6 × 7 (ZYX)' });
  await vi.advanceTimersByTimeAsync(0);
  expect(status()).toBe('second.tif: a volume, 4 × 6 × 7 (ZYX)');
  pending[0].resolve({ ok: true, says: 'first.tif: a volume, 3 × 4 × 5 (ZYX)' });  // A's, late: not said in B
  await vi.advanceTimersByTimeAsync(0);
  expect(status()).toBe('second.tif: a volume, 4 × 6 × 7 (ZYX)');
});
