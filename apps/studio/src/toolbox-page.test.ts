// Synthetic renderer/interaction evidence; actual model demonstrations are a separate acceptance gate.
import { afterEach, describe, expect, test, vi } from 'vitest';
import { powerTool } from './capabilities';
import { toolboxTarget } from './toolbox';
import { bandIds } from './keystrip';
import { ModelsPage, errorWords, type ModelsPageCommands, type CapabilityWorkspace } from './modelspage';
import { MODEL_SECTIONS, modelSection } from './run-spec';
import type { CapabilitiesWire, ModelRow, OwnComparison, ToolSetup } from './protocol';
import { fire, settleMicrotasks, TestElement } from './workspace-windows.test-support';

export const tool = (id: string, over: Partial<ModelRow> = {}): ModelRow => ({
  id, name: id, runs: 'this-mac', where: 'on this Mac', what: `What ${id} does`, kind: 'model', version: '1',
  credit: {}, link: '', licence: 'MIT', tier: 'open', mine: false, status: 'ready', word: 'Ready', group: 'ready',
  next: null, steps: [], inUse: false, chosen: false, facts: [], usedFor: [], size: 0, files: [], folder: '', revision: '', ...over,
});
export function mounted(models: ModelRow[], extra: Partial<CapabilitiesWire> = {}, options: { workspace?: boolean } = {}) {
  let focused: TestElement | null = null;
  class Focusable extends TestElement { focus() { focused = this; } }
  // The workspace (Hooke): Use it here and Try on … go to a run page only there (body[data-workspace-study]).
  vi.stubGlobal('document', { createElement: (tag: string) => new Focusable(tag), get activeElement() { return focused; },
    ...(options.workspace ? { body: { dataset: { workspaceStudy: 'true' } } } : {}) });
  vi.stubGlobal('requestAnimationFrame', () => 0);
  const root = new TestElement('main');
  const commands: ModelsPageCommands = { download: vi.fn(async () => undefined), reveal: vi.fn(async () => undefined),
    shelf: { turnOn: vi.fn(async () => undefined), cancel: vi.fn(async () => undefined), tryIt: vi.fn(async () => undefined),
      seen: async () => undefined, dismiss: async () => undefined, get: async () => undefined } };
  const wire: CapabilitiesWire = { type: 'capabilities', families: [], actors: { you: '', agent: '', auto: '', researcher: '' },
    capabilities: [], roles: [], preferences: [], pending: 0, build: 'test', agentText: '', models, ...extra };
  const page = new ModelsPage(root as unknown as HTMLElement, commands); page.update(wire);
  return { root, commands, page, wire, focused: () => focused };
}
afterEach(() => vi.unstubAllGlobals());

test('workspace Try leaves its table presentation to the retained completion instead of opening another window', async () => {
  const {root, commands, page} = mounted([tool('measure', {newKind:{operation:'measure',input:'image.2d.rgb8',output:'table.objects',gives:'measurements',proved:true}})]);
  Object.assign(document, {body:{dataset:{workspaceStudy:'true'}}});
  Object.assign(commands, {session:()=> 's1', tryKind:vi.fn(async()=>({ok:true,table:'d3',tableIdentity:{session:'s1',revision:0}})), openTable:vi.fn()});
  page.show('measure');
  fire(root.querySelector('.tool-kind-try')!, 'click');
  await settleMicrotasks();
  expect(commands.tryKind).toHaveBeenCalledOnce();
  expect(commands.openTable).not.toHaveBeenCalled();
});

test('builder origin and terminal state are honest and its local community draft survives polling', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, drafts: [
    { id: 'd1', link: 'https://example.org/model', state: 'ran', tries: 1, last: { count: 3 }, model: '',
      input: { evidenceClass: 'generated-fixture', identity: 'three-discs' }, terminal: { status: 'cancelled' } },
    { id: 'd2', link: 'https://example.org/other', state: 'interrupted', tries: 0, last: null, model: '', terminal: { status: 'interrupted' } },
  ] } });
  commands.communityDraft = vi.fn(async () => ({ text: 'Local facts without pictures', says: 'Nothing was sent.' }));
  page.update(wire);
  expect(root.textContent).toContain('generated test fixture');
  expect(root.textContent).not.toContain('It ran on your picture');
  expect(root.textContent).toContain('Builder interrupted');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Ask the community')!, 'click');
  await Promise.resolve(); await Promise.resolve();
  const draft = root.querySelector('textarea')!;
  draft.value = 'My reviewed question';
  page.update(wire);
  expect(root.querySelector('textarea')?.value).toBe('My reviewed question');
  expect(commands.communityDraft).toHaveBeenCalledTimes(1);
});

test('community publication reviews a distinct outgoing package and never sends before signoff', async () => {
  const { root, page, wire, commands } = mounted([tool('mine/tool', { mine: true, shareable: true })]);
  commands.publishPrepare = vi.fn(async () => ({ says: 'Publisher-owned community snapshot', package: 'community-package' }));
  commands.publishSubmit = vi.fn(async () => ({ says: 'Pinned community link' }));
  page.show('mine/tool');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Prepare a community link')!, 'click');
  await Promise.resolve(); await Promise.resolve(); page.update(wire);
  expect(commands.publishPrepare).toHaveBeenCalledWith('mine/tool', 'community');
  expect(commands.publishSubmit).not.toHaveBeenCalled();
  const sign = root.querySelector('.model-share-sign')!;
  Object.assign(sign, { checked: true }); fire(sign, 'change');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Publish my community link')!, 'click');
  expect(commands.publishSubmit).toHaveBeenCalledWith('mine/tool', 'community-package', true);
});

test('presents every kind as tool cards, hides unavailable entries until asked and opens only one detail pane', () => {
  const { root, page, wire } = mounted([
    tool('cell-model', { face: { state: 'on', says: 'Passed here', action: null, also: [] } }),
    tool('algorithm', { kind: 'tool-function', face: { state: 'unchecked', says: 'Ready; not checked yet', action: null, also: [] } }),
    tool('atlas', { kind: 'data', face: { state: 'obtained', says: 'Files checked', action: null, also: [] } }),
    tool('unavailable', { status: 'unavailable', group: 'unavailable' }),
  ]);
  expect(root.querySelector('h2')?.textContent).toBe('Models');
  expect(root.querySelectorAll('.model-card')).toHaveLength(4);
  expect(root.querySelector('.shelf')).toBeNull();
  const unavailable = root.querySelector('.toolbox-unavailable')!;
  expect(unavailable.tagName).toBe('DETAILS'); expect(unavailable.getAttribute('open')).toBeNull();
  fire(root.querySelector('[data-model="cell-model"]')!.querySelector('.tool-details-open')!, 'click');
  expect(root.querySelectorAll('.tool-details')).toHaveLength(1);
  // Details as the mock's modelDetail: no filler lines; Size, Licence and Pictures; the rest under a closed disclosure.
  const pane = root.querySelector('.tool-details')!;
  expect(pane.textContent).not.toContain('No recorded demonstration');
  expect(pane.textContent).not.toContain('No app capability is registered');
  expect(pane.querySelector('.tool-details-facts')!.querySelectorAll('dt').map(n => n.textContent)).toEqual(['Size', 'Licence', 'Pictures']);
  expect(pane.querySelector('.tool-details-more')!.getAttribute('open')).toBeNull();
  expect(pane.querySelector('.tool-details-more')!.querySelector('summary')!.textContent).toBe('More about this model');
  expect(pane.querySelector('.tool-details-close')!.getAttribute('aria-label')).toBe('Close details');
  fire(root.querySelector('[data-model="algorithm"]')!.querySelector('.tool-details-open')!, 'click');
  expect(root.querySelectorAll('.tool-details')).toHaveLength(1);
  expect(root.querySelector('.tool-details')?.getAttribute('data-tool')).toBe('algorithm');
  page.update(wire);
  expect(root.querySelector('.tool-details')?.getAttribute('data-tool')).toBe('algorithm');
});

test('the tool face submits its reviewed recipe and the details try binds this exact tool', async () => {
  const plan = { option: 'cloud-other', model: 'other', label: 'Other tool', recipe: 'exact-material-recipe',
    privacy: 'Sends pictures to Provider', cost: 'Paid per call', steps: [{ kind: 'check', says: 'Run its functional check', by: 'app' as const }] };
  const r = tool('other', { setups: [{ role: 'cells', title: 'Cells', option: 'cloud-other', plan, recheck: false, canTry: false }],
    tries: [{ role: 'cells', option: 'cloud-other', title: 'Try Cells with Other tool', try: { command: 'capability-try', job: 'cells', option: 'cloud-other' } }] });
  const { root, commands, page, wire } = mounted([r]);
  const face = root.querySelector('[data-model="other"]')!;
  // One card shape: its terms are in its setup sheet, said before anything starts (workspace-feel step 8).
  expect(face.textContent).not.toContain('Sends pictures to Provider');
  fire(face.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Sends pictures to Provider'); expect(sheet.textContent).toContain('Paid per call');
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click');
  expect(commands.shelf!.turnOn).toHaveBeenCalledWith('cells', plan, false);
  await settleMicrotasks(); fire(root.querySelector('.tool-setup-sheet-cancel')!, 'click');
  r.setups![0] = { ...r.setups![0], canTry: true, recheck: true, check: { state: 'works', why: '', at: '2026-10-13T12:00:00', ms: 123 } };
  page.update(wire); page.show('other');
  const details = root.querySelector('.tool-details')!;
  expect(details.textContent).toContain('123 ms');
  fire(details.querySelector('.tool-try')!, 'click');
  expect(commands.shelf!.tryIt).toHaveBeenCalledWith('cells', 'cloud-other');
});

test('shows attributed demonstrations and measured evidence, with default plus two alternatives and More options', () => {
  const r = tool('cells', { demonstration: { url: '/api/catalog/example/cells', caption: 'Publisher demonstration, not a result from this Mac', kind: 'image' },
    usedFor: [{ role: 'cells', title: 'Cells', option: 'a', options: [], current: true, fixed: false }],
    setups: [{ role: 'cells', title: 'Cells', option: 'a', plan: null, recheck: true, canTry: true,
      check: { state: 'works', why: '', at: '2026-10-13', ms: 123, detail: '3 objects' } }] });
  const shelf = { cards: [{ id: 'cells', title: 'Cells', what: 'Find cells', colour: 'green' as const, word: 'On', using: 'A',
    privacy: 'Runs on this Mac', cost: 'Free', service: '', try: '', tryOption: 'a', turnOn: null,
    outcomes: ['On diatoms: 3 runs, 1 corrected'], options: ['a', 'b', 'c', 'd', 'e'].map(id => ({ id, label: id,
      status: 'ready', word: 'Ready', where: 'Mac', default: id === 'd' })) }], behind: [], counts: { green: 1, red: 0, yellow: 0, grey: 0 } };
  const { root, page } = mounted([r], { setup: { edition: 'open', options: {}, shelf } });
  page.show('cells'); const pane = root.querySelector('.tool-details')!;
  expect(pane.querySelector('img')?.getAttribute('src')).toBe('/api/catalog/example/cells');
  expect(pane.textContent).toContain('Publisher demonstration'); expect(pane.textContent).toContain('123 ms');
  expect(pane.textContent).toContain('On diatoms: 3 runs, 1 corrected');
  expect(pane.querySelector('.tool-options-primary')?.children.map(n => n.getAttribute('data-option'))).toEqual(['d', 'a', 'b']);
  expect(pane.querySelector('.tool-options-more')?.querySelector('summary')?.textContent).toContain('More options');
});

test('keeps sharing, report, retained-version and draft controls in the unified area across polling', async () => {
  const r = tool('mine/tool', { mine: true, shareable: true, versions: [
    { generation: 'g2', version: '2', at: '', inUse: true, chosen: false },
    { generation: 'g1', version: '1', at: '', inUse: false, chosen: false }] });
  const { root, page, wire, commands } = mounted([r], { setup: { edition: 'open', options: {},
    drafts: [{ id: 'd1', link: 'https://example.org/tool', state: 'ran', tries: 1, last: { ok: true, count: 3 }, model: 'mine/tool',
      review: { digest: 'r1', adapter: 'def segment(picture, params): ...', checks: [], tries: [{ ok: true, count: 3 }] } }] } });
  commands.publishPrepare = vi.fn(async () => ({ says: 'Exact package and DCO identity', package: 'p1' }));
  commands.publishSubmit = vi.fn(async () => ({ says: 'Sent' }));
  commands.setVersion = vi.fn(async () => ({})); commands.report = vi.fn(async () => ({ title: 'Tool problem', text: 'Public facts', url: '', says: 'Nothing sent' }));
  commands.useDraft = vi.fn(async () => ({})); commands.githubStatus = vi.fn(async () => ({ setUp: true, signedIn: true, says: 'Signed in' }));
  page.show('mine/tool');
  let pane = root.querySelector('.tool-details')!;
  const name = pane.querySelector('.model-share-name')!; name.value = 'Contributor';
  fire(pane.querySelectorAll('button').find(b => b.textContent === 'Prepare it for everyone')!, 'click');
  await Promise.resolve(); await Promise.resolve(); page.update(wire);
  pane = root.querySelector('.tool-details')!;
  expect(pane.querySelector('.model-share-name')?.value).toBe('Contributor');
  expect(pane.textContent).toContain('Exact package and DCO identity');
  expect(commands.githubStatus).toHaveBeenCalledTimes(1);
  expect(commands.publishSubmit).not.toHaveBeenCalled();
  fire(pane.querySelectorAll('button').find(b => b.textContent === 'Go back to 1')!, 'click');
  expect(commands.setVersion).toHaveBeenCalledWith('mine/tool', 'g1');
  fire(root.querySelector('.models-drafts')!.querySelectorAll('button').find(b => b.textContent === 'Use it')!, 'click');
  expect(commands.useDraft).toHaveBeenCalledWith('d1', 'r1');
});


test('polling retains keyboard focus and the open alternatives fold in tool details', () => {
  const { root, page, wire, focused } = mounted([tool('one')]); page.show('one');
  const close = root.querySelector('.tool-details-close')!;
  (close as TestElement & { focus(): void }).focus();
  page.update(wire);
  expect(focused()).toBe(root.querySelector('.tool-details-close'));
});

test('an added link keeps its explicit AI-draft or Turn on follow-up across polling', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, drafts: [] } });
  commands.add = vi.fn(async () => ({ kind: 'skeleton', model: 'mine/denoise', says: 'Needs an adapter' }));
  commands.build = vi.fn(async () => ({ says: 'Drafting within the disclosed budget' }));
  const input = root.querySelector('.models-own-input')!; input.value = 'https://example.org/denoise';
  fire(input.closest('form')!, 'submit'); for (let i = 0; i < 8; i++) await Promise.resolve();
  page.update(wire);
  const draft = root.querySelector('.tool-draft-start')!;
  expect(draft.textContent).toContain('$2');
  fire(draft, 'click');
  expect(commands.build).toHaveBeenCalledWith('https://example.org/denoise', undefined);
});


test('the atlas links a power to the tool actually selected for that role', () => {
  const { wire } = mounted([tool('other', { usedFor: [{ role: 'cells', title: 'Cells', option: 'other', options: [], current: false, fixed: false }] }),
    tool('selected', { usedFor: [{ role: 'cells', title: 'Cells', option: 'selected', options: [], current: true, fixed: false }] })]);
  expect(powerTool(wire, { kind: 'model', id: 'cells' })).toBe('selected');
  expect(powerTool(wire, { kind: 'code', id: 'cells' })).toBeNull();
});

test('setup refusal survives polling and the next deliberate press can retry', async () => {
  const plan = { option: 'a', model: 'one', label: 'One', steps: [], recipe: 'r', privacy: 'Runs here', cost: 'Free' };
  const { root, page, wire, commands } = mounted([tool('one', { setups: [{ role: 'cells', title: 'Cells', option: 'a', plan,
    recheck: false, canTry: false }] })]);
  commands.shelf!.turnOn = vi.fn(async () => { throw new Error('Setup changed; review its new terms'); });
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); page.update(wire);
  expect(root.textContent).toContain('Setup changed; review its new terms');
  expect(root.querySelector('.tool-setup-act')?.disabled).toBe(false);
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click');
  expect(commands.shelf!.turnOn).toHaveBeenCalledTimes(2);
  await Promise.resolve(); await Promise.resolve();
});


test('a volume tool uses an explicitly selected volume from this session and the shared facts editor without brainreg', async () => {
  vi.stubGlobal('window', { setTimeout: () => 1, clearTimeout: () => undefined });
  const r = tool('mine/volume', { newKind: { proved: true, operation: 'denoise', input: 'volume.3d', output: 'volume.3d', gives: 'a volume' } });
  const { root, page, wire, commands } = mounted([r]);
  let session = 'session-a';
  commands.session = () => session;
  commands.tryKind = vi.fn(async () => ({ ok: true, summary: 'Started chosen volume as job j9 in the background' }));
  commands.volumes = { list: vi.fn(async () => [{ id: 'v1', kind: 'volume' as const, day: session, title: 'Selected specimen', state: 'saved', summary: '3D stack',
    values: { facts: { voxel_um: { value: [1, 2, 3], source: 'user' }, series: { value: 0, source: 'file' } }, series: [{ axes: 'ZYX', shape: [3, 4, 5] }] } }]),
    upload: vi.fn(async () => ({ ok: true })), add: vi.fn(async () => undefined), describe: vi.fn(async () => undefined),
    start: vi.fn(async () => undefined), cancel: vi.fn(async () => undefined), again: vi.fn(async () => undefined), open: vi.fn() };
  page.show(r.id); await Promise.resolve(); await Promise.resolve();
  expect(root.querySelector('.volumes-section')).not.toBeNull();
  expect(root.querySelector('.volume-orientation')).toBeNull();
  expect(root.querySelector('.volumes-section')?.textContent).not.toContain('brainreg');
  let pane = root.querySelector('.tool-details')!;
  expect(pane.querySelector('.tool-kind-try')?.disabled).toBe(true);
  const select = pane.querySelector('.tool-volume-select')!; select.value = 'v1'; fire(select, 'change');
  pane = root.querySelector('.tool-details')!;
  expect(pane.querySelector('.tool-kind-try')?.textContent).toBe('Try this tool on the selected volume');
  fire(pane.querySelector('.tool-kind-try')!, 'click'); await Promise.resolve(); await Promise.resolve();
  expect(commands.tryKind).toHaveBeenCalledWith('mine/volume', { volume: 'v1', session: 'session-a', input: 'volume.3d' });
  page.update(wire);
  expect(root.querySelector('.tool-details')?.textContent).toContain('job j9');
  expect(root.querySelector('.tool-volume-select')?.value).toBe('v1');
  session = 'session-b'; page.update(wire);
  expect(root.querySelector('.tool-kind-try')?.disabled).toBe(true);
  expect(root.querySelector('.tool-volume-select')?.value).toBe('');
});


test('generic volume facts keep the source session and save scale without inventing anatomical orientation', async () => {
  vi.stubGlobal('window', { setTimeout: () => 1, clearTimeout: () => undefined });
  const { root, page, commands } = mounted([tool('mine/volume', { newKind: { proved: true, operation: 'denoise', input: 'volume.3d', output: 'volume.3d', gives: 'a volume' } })]);
  commands.session = () => 'session-a';
  commands.volumes = { list: async () => [{ id: 'v1', day: 'session-a', kind: 'volume', title: 'Stack', state: 'saved', summary: '',
    values: { facts: { series: { value: 0, source: 'file' } }, series: [{ axes: 'ZYX', shape: [3, 4, 5] }] } }],
    upload: async () => ({ ok: true }), add: async () => undefined, describe: vi.fn(async () => undefined),
    start: vi.fn(async () => undefined), cancel: async () => undefined, again: async () => undefined, open: () => undefined };
  page.show('mine/volume'); await Promise.resolve(); await Promise.resolve();
  const form = root.querySelector('.volume-facts')!;
  const scale = form.querySelectorAll('input'); scale.forEach((field, n) => { field.value = String(n + 1); });
  expect(scale).toHaveLength(3);
  fire(form, 'submit');
  expect(commands.volumes.describe).toHaveBeenCalledWith('v1', { voxelUm: [1, 2, 3], session: 'session-a', registration: false });
  expect(commands.volumes.start).not.toHaveBeenCalled();
});


test.each([['image.2d.rgb8', 'current still image'], ['clip.2d.rgb8', 'recent frames']])('the %s trial names its actual input', (input, label) => {
  const { root, page, commands } = mounted([tool('mine/tool', { newKind: { proved: true, operation: 'try', input, output: 'table.objects', gives: 'a table' } })]);
  commands.session = () => 'session-a'; commands.tryKind = vi.fn(async () => ({ ok: true }));
  page.show('mine/tool');
  expect(root.querySelector('.tool-kind-try')?.textContent).toBe(`Try this tool on the ${label}`);
  fire(root.querySelector('.tool-kind-try')!, 'click');
  expect(commands.tryKind).toHaveBeenCalledWith('mine/tool', { session: 'session-a', input });
});


test('the volume facts action opens the shared editor without navigating out of Toolbox', async () => {
  vi.stubGlobal('window', { setTimeout: () => 1, clearTimeout: () => undefined });
  const { root, page, commands } = mounted([tool('mine/tool', { newKind: { proved: true, operation: 'try', input: 'volume.3d', output: 'table.objects', gives: 'a table' } })]);
  commands.session = () => 'session-a'; commands.tryKind = vi.fn(async () => ({ ok: true }));
  commands.volumes = { list: async () => [], upload: async () => ({ ok: true }), add: async () => undefined,
    describe: async () => undefined, start: async () => undefined, cancel: async () => undefined, again: async () => undefined, open: () => undefined };
  page.show('mine/tool'); await Promise.resolve();
  const edit = root.querySelector('.tool-volume-edit')!;
  expect(edit?.tagName).toBe('BUTTON');
  fire(edit, 'click');
  expect(root.querySelector('.tool-details')).toBeNull();
  expect(root.querySelector('.volumes-section')).not.toBeNull();
});


test('an unproved catalog adapter offers its functional setup and cannot run a trial', () => {
  const { root, page, commands, wire } = mounted([tool('mine/tool', { newKind: { proved: false, operation: 'try', input: 'image.2d.rgb8', output: 'table.objects', gives: 'a table' },
    face: { state: 'needs-setup', says: 'Its adapter needs a functional check before it can run', action: { kind: 'install', model: 'mine/tool', says: 'Turn on and check' }, also: [] } })]);
  commands.session = () => 'session-a'; commands.tryKind = vi.fn(async () => ({ ok: true }));
  commands.install = vi.fn(async () => undefined); page.update(wire);
  const setup = root.querySelector('[data-model="mine/tool"]')!.querySelectorAll('button').find(b => b.textContent === 'Turn on and check');
  expect(setup).toBeDefined(); fire(setup!, 'click');
  expect(commands.install).toHaveBeenCalledWith('mine/tool');
  page.show('mine/tool');
  expect(root.querySelector('.tool-kind-try')).toBeNull();
  expect(commands.tryKind).not.toHaveBeenCalled();
});


test('a failed volume list is visible and a successful retry clears the failure', async () => {
  vi.stubGlobal('window', { setTimeout: () => 1, clearTimeout: () => undefined });
  const now = vi.spyOn(Date, 'now').mockReturnValue(10000);
  try {
    const { root, page, commands, wire } = mounted([tool('mine/tool', { newKind: { proved: true, operation: 'try', input: 'volume.3d', output: 'table.objects', gives: 'a table' } })]);
    commands.session = () => 'session-a';
    commands.volumes = { list: vi.fn().mockRejectedValueOnce(new Error('Volume list unavailable')).mockResolvedValue([]),
      upload: async () => ({ ok: true }), add: async () => undefined, describe: async () => undefined,
      start: async () => undefined, cancel: async () => undefined, again: async () => undefined, open: () => undefined };
    page.show('mine/tool'); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(root.textContent).toContain('Volume list unavailable');
    now.mockReturnValue(13000); page.update(wire); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(root.textContent).not.toContain('Volume list unavailable');
  } finally { now.mockRestore(); }
});

test('the card names the first step a person meets and its sheet lists every step before a press admits the whole plan', async () => {
  const plan = { option: 'gated', model: 'gated', label: 'Gated tool', recipe: 'pinned', privacy: 'Runs on this Mac', cost: 'Free',
    steps: [{ kind: 'licence', says: 'Accept its licence', by: 'you' as const },
      { kind: 'download', says: 'Download 3.5 GB', model: 'gated', by: 'app' as const },
      { kind: 'check', says: 'Run its functional check', by: 'app' as const }] };
  const { root, commands } = mounted([tool('gated', { status: 'needs-setup', group: 'available',
    setups: [{ role: 'cells', title: 'Cells', option: 'gated', plan, canTry: false, recheck: false }] })]);
  const face = root.querySelector('[data-model="gated"]')!;
  // One card shape (workspace-feel step 8): no step list on the card; its one button names the first step the person
  // meets (here the licence), and the sheet lists every step before anything starts.
  expect(face.querySelector('.shelf-steps')).toBeNull();
  expect(face.querySelector('.tool-setup-act')!.textContent).toBe('Accept licence');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  fire(face.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  expect(root.querySelector('.tool-setup-steps')!.textContent).toContain('Download 3.5 GB');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled(); // the sheet says what it takes first
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click');
  expect(commands.shelf!.turnOn).toHaveBeenCalledWith('cells', plan, false);
});

const sheetPlan = (): NonNullable<ToolSetup['plan']> => ({ option: 'big', model: 'big', label: 'Big tool', recipe: 'pinned', privacy: 'Sends pictures to Google Gemini (your key)',
  cost: 'Paid per call', service: 'gemini',
  steps: [{ kind: 'key', says: 'Add your Google Gemini API key', service: 'gemini', by: 'you' as const },
    { kind: 'licence', says: 'Accept its licence on Hugging Face', url: 'https://huggingface.co/big', by: 'you' as const },
    { kind: 'download', says: 'Download 3.5 GB', model: 'big', bytes: 3_500_000_000, by: 'app' as const },
    { kind: 'check', says: 'Run its functional check', by: 'app' as const }] });
const sheetKeys = { GEMINI_API_KEY: { service: 'gemini', name: 'Google Gemini API', short: 'Google', getKey: '', enables: '', present: false,
  source: null, sourceSays: '', canSave: true } };
function sheetPage(free: number, plan = sheetPlan()) {
  const m = mounted([tool('big', { status: 'needs-setup', group: 'available', licence: 'Gemma terms', tier: 'terms',
    setups: [{ role: 'cells', title: 'Cells', option: 'big', plan, canTry: false, recheck: false }] })],
  { setup: { edition: 'research', options: {}, keys: sheetKeys, disk: { freeBytes: free, spareBytes: 1 << 30 } } });
  return { ...m, plan };
}

test('#models/<id>/setup (Tools\' Set up, Activity\'s See progress) opens that model\'s setup sheet in one press, starting nothing', async () => {
  const { root, commands, page } = sheetPage(200e9);
  page.show('big/setup'); await settleMicrotasks();
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.getAttribute('data-tool')).toBe('big');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('Turn on opens one sheet that says what it takes before anything starts, and Cancel or Escape starts nothing', async () => {
  const { root, commands, page, wire, focused } = sheetPage(200e9);
  fire(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.getAttribute('role')).toBe('dialog'); expect(sheet.getAttribute('aria-modal')).toBe('true');
  expect(focused()).toBe(sheet);
  expect(root.querySelector('.models-page-body')!.getAttribute('inert')).toBe('');
  const text = sheet.textContent;
  for (const said of ['Add your Google Gemini API key', 'Download 3.5 GB', 'It downloads 3.5 GB: a large download, over 1 GB',
    'free on the disk models are kept on: it fits', 'ten minutes or more on a typical connection', 'Gemma terms (its own terms',
    'Sends pictures to Google Gemini (your key)', 'Not added', 'Test and save', 'Paid per call']) expect(text).toContain(said);
  expect(sheet.querySelector('a')?.getAttribute('href')).toBe('https://huggingface.co/big');
  page.update(wire); // polling keeps it open, drawn from the newest facts
  expect(root.querySelectorAll('.tool-setup-sheet')).toHaveLength(1);
  fire(root.querySelector('.tool-setup-sheet-cancel')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  expect(focused()).toBe(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act'));
  fire(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  fire(root, 'keydown', { key: 'Escape' });
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('Escape closes only the innermost layer (a setup sheet, Add model or tool, Details): Models stays open, focus goes back', async () => {
  const { root, page, focused } = sheetPage(200e9);
  const escape = () => { const event = new Event('keydown', { cancelable: true }); Object.defineProperty(event, 'key', { value: 'Escape' }); root.dispatchEvent(event); return event; };
  // A setup sheet: closed, its default prevented so the Models dialog's own cancel never follows (it would close Models).
  fire(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  let event = escape();
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  expect(event.defaultPrevented).toBe(true);
  expect(focused()).toBe(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act'));
  // Details: the same, and focus returns to the card it opened from.
  fire(root.querySelector('[data-model="big"]')!.querySelector('.tool-details-open')!, 'click');
  expect(root.querySelector('.tool-details')).not.toBeNull();
  event = escape();
  expect(root.querySelector('.tool-details')).toBeNull();
  expect(event.defaultPrevented).toBe(true);
  expect(focused()).toBe(root.querySelector('[data-model="big"]')!.querySelector('.tool-details-open'));
  // Add model or tool from the Models header (its 'hooke-models-add'): back to the press that opened it.
  let headerFocused = false;
  const header = Object.assign(new TestElement('button'), { focus: () => { headerFocused = true; } });
  vi.stubGlobal('document', { ...document, get activeElement() { return header; } });
  (page as unknown as { addWay(way: string): void }).addWay('start');
  expect(root.querySelector('.tool-setup-sheet')).not.toBeNull();
  event = escape();
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  expect(event.defaultPrevented).toBe(true);
  expect(headerFocused).toBe(true);
  // With nothing inside open, Escape is the Models sheet's own (workspace-study-surfaces.ts closes it).
  expect(escape().defaultPrevented).toBe(false);
});

test('under 1100 px Details takes the grid\'s place at its top, and closing it returns to where the grid was', () => {
  const { root } = mounted([tool('a'), tool('b')]);
  const body = Object.assign(new TestElement('div'), { className: 'study-overlay-body', scrollTop: 640 });
  body.append(root);
  let narrow = true;
  vi.stubGlobal('window', { matchMedia: () => ({ matches: narrow }), addEventListener: () => undefined, dispatchEvent: () => true });
  fire(root.querySelector('[data-model="b"]')!.querySelector('.tool-details-open')!, 'click');
  expect(root.querySelector('.tool-details')).not.toBeNull();
  expect(body.scrollTop).toBe(0); // its name and × in view, not the middle of the pane
  body.scrollTop = 120; // read down the pane
  fire(root.querySelector('.tool-details-close')!, 'click');
  expect(body.scrollTop).toBe(640); // back to the card it opened from
  narrow = false; body.scrollTop = 300; // beside the grid, nothing moves
  fire(root.querySelector('[data-model="a"]')!.querySelector('.tool-details-open')!, 'click');
  expect(body.scrollTop).toBe(300);
});

test('the sheet stays open through setup and Stop names the exact attempt', async () => {
  const { root, commands, page, wire, plan } = sheetPage(200e9);
  fire(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click');
  expect(commands.shelf!.turnOn).toHaveBeenCalledWith('cells', plan, false);
  expect(root.querySelector('.tool-setup-sheet')).not.toBeNull();
  await settleMicrotasks();
  wire.setup!.turning = { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big tool', state: 'running', attempt: 'attempt-1', says: 'Downloading 12%' } };
  page.update(wire);
  const face = root.querySelector('[data-model="big"]')!;
  expect(face.textContent).toContain('Downloading 12%');
  fire(root.querySelector('.tool-setup-sheet')!.querySelector('.tool-stop')!, 'click');
  expect(commands.shelf!.cancel).toHaveBeenCalledWith('cells', 'attempt-1');
});

test('without room for the download the sheet says so and offers no Turn on', async () => {
  const { root, commands } = sheetPage(2e9);
  fire(root.querySelector('[data-model="big"]')!.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Not enough space: it needs 3.5 GB and 1.1 GB kept spare, and the disk models are kept on has 2.0 GB free');
  expect(sheet.querySelector('.tool-setup-sheet-go')).toBeNull();
  expect(sheet.querySelector('.tool-setup-sheet-cancel')).not.toBeNull();
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('a check alone uses the same guided sheet', async () => {
  const plan = { option: 'a', model: 'a', label: 'A', recipe: 'r', privacy: 'Runs on this Mac', cost: 'Free',
    steps: [{ kind: 'check', says: 'Run its functional check', by: 'app' as const }] };
  const { root, commands } = mounted([tool('a', { setups: [{ role: 'cells', title: 'Cells', option: 'a', plan, canTry: false, recheck: false }] })]);
  fire(root.querySelector('.tool-setup-act')!, 'click');
  await settleMicrotasks();
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click');
  expect(commands.shelf!.turnOn).toHaveBeenCalledWith('cells', plan, false);
  expect(root.querySelector('.tool-setup-sheet')).not.toBeNull();
});

test('the face follows owned proving and waiting progress and retains the agreed state pill', () => {
  const r = tool('one', { face: { state: 'unchecked', says: 'Ready to run; not checked here', action: null, also: [] },
    setups: [{ role: 'cells', title: 'Cells', option: 'one', plan: null, recheck: false, canTry: false }] });
  const turning = { job: 'cells', option: 'one', model: 'one', label: 'One', state: 'running' as 'running' | 'waiting', says: 'Checking it on the public example' };
  const card = { id: 'cells', title: 'Cells', what: '', colour: 'yellow' as const, word: 'On its way', using: '', privacy: '', cost: '', service: '', try: '', options: [], turnOn: null, turning };
  const { root, page, wire } = mounted([r], { setup: { edition: 'open', options: {}, shelf: { cards: [card], behind: [], counts: { yellow: 1, green: 0, red: 0, grey: 0 } } } });
  const face = () => root.querySelector('[data-model="one"]')!;
  const cell = () => face().querySelector('[data-kind="lit-cell"]')!;
  expect(cell().getAttribute('aria-label')).toBe('Setting up');
  expect(cell().getAttribute('data-state')).toBe('working'); // the lit cell is the card's state (workspace-feel step 8)
  expect(face().querySelector('.tool-state')?.textContent).toBe(turning.says);
  turning.state = 'waiting'; turning.says = 'Waiting for your key'; page.update(wire);
  expect(face().querySelector('.tool-state')?.textContent).toBe('Waiting for your key');
  expect(cell().getAttribute('data-state')).toBe('needs');
  r.face = { state: 'on', says: 'Functional check passed', action: null, also: [] }; wire.setup!.shelf!.cards = []; page.update(wire);
  expect(cell().getAttribute('data-state')).toBe('ready');
  // A ready card's facts line says where it runs and what it costs; the face's own words are its tooltip.
  expect(face().querySelector('.tool-state')?.textContent).toBe('On this Mac · free');
  expect(face().querySelector('.tool-state')?.getAttribute('title')).toBe('Functional check passed');
});

test('the exact generic Try appears with capabilities before technical facts and sharing', () => {
  const { root, page, commands } = mounted([tool('denoiser', { newKind: { operation: 'denoise', input: 'image.2d.rgb8', output: 'image.2d.rgb8', gives: 'a picture', proved: true } })]);
  commands.tryKind = vi.fn(async () => ({ summary: 'Done' })); commands.session = () => 's1'; page.show('denoiser');
  const pane = root.querySelector('.tool-details')!;
  expect(pane.textContent.indexOf('Try this tool on the current still image')).toBeGreaterThan(-1);
  expect(pane.textContent.indexOf('Try this tool on the current still image')).toBeLessThan(pane.textContent.indexOf('Facts and files'));
});

test('a roleless tool opens its continuing setup and stops only that proof', async () => {
  const r = tool('fiji', { face: { state: 'on', says: 'Previous proof passed', action: null, also: [] },
    setups: [{ role: 'model:fiji', title: 'Fiji', option: 'fiji', plan: null, recheck: true, canTry: false }] });
  const { root, page, wire, commands } = mounted([r], { setup: { edition: 'open', options: {} } });
  Object.assign(wire.setup!, { turning: { 'model:fiji': { job: 'model:fiji', option: 'fiji', model: 'fiji',
    label: 'Fiji', state: 'running', attempt: 'fiji-attempt', says: 'Proving a new Fiji generation' } } }); page.update(wire);
  const face = root.querySelector('[data-model="fiji"]')!;
  expect(face.querySelector('.tool-state')?.textContent).toBe('Proving a new Fiji generation');
  fire(face.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  fire(root.querySelector('.tool-setup-sheet')!.querySelector('.tool-stop')!, 'click');
  expect(commands.shelf!.cancel).toHaveBeenCalledWith('model:fiji', 'fiji-attempt');
});

test('any link is added without a picture; the builder asks for it only when it starts, and takes it with its job', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, drafts: [] } });
  commands.ownPicture = vi.fn(async () => ({ id: 'chosen-picture' }));
  commands.add = vi.fn(async () => ({ kind: 'skeleton', model: 'mine/tool', says: 'Needs an adapter' }));
  commands.build = vi.fn(async () => ({})); page.update(wire);
  const input = root.querySelector('.models-own-input')!; input.value = 'https://example.org/denoise';
  fire(input.closest('form')!, 'submit');
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.ownPicture).not.toHaveBeenCalled();
  expect(commands.add).toHaveBeenCalledWith('https://example.org/denoise', undefined, undefined);
  expect(root.textContent).toContain('Needs an adapter');
  expect(root.textContent).not.toContain('Choose your comparison picture first');
  fire(root.querySelector('.tool-draft-start')!, 'click');
  expect(commands.build).not.toHaveBeenCalled();
  expect(root.textContent).toContain('Choose your comparison picture above (PNG or JPEG), then press “Have an AI draft it” again');
  expect(root.textContent).toContain('the AI tries its draft on that picture');
  const file = { name: 'my-picture.png' }; Object.assign(root.querySelector('.own-picture')!, { files: [file] });
  root.querySelector('.own-job')!.value = 'clear';
  fire(root.querySelector('.tool-draft-start')!, 'click');
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.ownPicture).toHaveBeenCalledWith(file, 'clear');
  expect(commands.build).toHaveBeenCalledWith('https://example.org/denoise', 'chosen-picture');
});

test('a picture chosen before Add still goes with the link, and the builder reuses it', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, drafts: [] } });
  commands.ownPicture = vi.fn(async () => ({ id: 'chosen-picture' }));
  commands.add = vi.fn(async () => ({ kind: 'skeleton', model: 'mine/tool', says: 'Needs an adapter' }));
  commands.build = vi.fn(async () => ({})); page.update(wire);
  const input = root.querySelector('.models-own-input')!; input.value = 'https://example.org/denoise';
  const file = { name: 'my-picture.png' }; Object.assign(root.querySelector('.own-picture')!, { files: [file] });
  root.querySelector('.own-job')!.value = 'clear';
  fire(input.closest('form')!, 'submit');
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.ownPicture).toHaveBeenCalledWith(file, 'clear');
  expect(commands.add).toHaveBeenCalledWith('https://example.org/denoise', undefined, 'chosen-picture');
  fire(root.querySelector('.tool-draft-start')!, 'click');
  for (let i = 0; i < 4; i++) await Promise.resolve();
  expect(commands.build).toHaveBeenCalledWith('https://example.org/denoise', 'chosen-picture');
  expect(commands.ownPicture).toHaveBeenCalledTimes(1);
});

test('Set up for comparison asks for the picture then, and sets it up on it', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {} } });
  commands.ownPicture = vi.fn(async () => ({ id: 'chosen-picture' }));
  commands.add = vi.fn(async () => ({ kind: 'template:bioimageio', model: 'mine/diatom-counter', says: 'Added Diatom counter' }));
  commands.turnOnModel = vi.fn(async () => ({})); page.update(wire);
  const input = root.querySelector('.models-own-input')!; input.value = 'https://bioimage.io/#/?id=diatom-counter';
  fire(input.closest('form')!, 'submit');
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.add).toHaveBeenCalledWith('https://bioimage.io/#/?id=diatom-counter', undefined, undefined);
  fire(root.querySelector('.tool-added-on')!, 'click');
  expect(commands.turnOnModel).not.toHaveBeenCalled();
  expect(root.textContent).toContain('then press “Set up for comparison” again: it is compared on that picture before you use it');
  const file = { name: 'my-picture.png' }; Object.assign(root.querySelector('.own-picture')!, { files: [file] });
  fire(root.querySelector('.tool-added-on')!, 'click');
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.ownPicture).toHaveBeenCalledWith(file, 'find');
  expect(commands.turnOnModel).toHaveBeenCalledWith('mine/diatom-counter', 'chosen-picture');
});

test('two comparisons of one name say what tells them apart; a lone one is just its name', () => {
  const { root } = mounted([], { setup: { edition: 'open', options: {}, ownTrials: [
    { id: 't1', model: 'mine/diatom-counter', job: 'find', name: 'Diatom counter', detail: 'made with the bioimageio template v1' },
    { id: 't2', model: 'mine/diatom-counter-2', job: 'find', name: 'Diatom counter', detail: 'made with the bioimageio template v2' },
    { id: 't3', model: 'mine/other', job: 'find', name: 'Other', detail: 'made with the bioimageio template v2' },
  ] } });
  const titles = root.querySelectorAll('[data-own-trial]').map(a => a.querySelector('h4')?.textContent);
  expect(titles).toEqual(['Diatom counter (made with the bioimageio template v1)',
    'Diatom counter (made with the bioimageio template v2)', 'Other']);
});

test('a draft is shown by its repository name, its link beside it', () => {
  const { root } = mounted([], { setup: { edition: 'open', options: {}, drafts: [
    { id: 'napari-segment-blobs', name: 'napari-segment-blobs-and-things-with-membranes', state: 'drafting', tries: 0, last: null, model: '',
      link: 'https://github.com/haesleinhuepf/napari-segment-blobs-and-things-with-membranes/tree/f0a392a0c0ffee0c0ffee0c0ffee0c0ffee0c0ff' },
  ] } });
  const row = root.querySelector('.models-draft')!;
  expect(row.querySelector('strong')?.textContent).toBe('napari-segment-blobs-and-things-with-membranes');
  expect(row.textContent).toContain('github.com/haesleinhuepf/');
});

test('Use is offered only after actual retained comparison and survives a page update', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, ownTrials: [
    { id: 'trial', model: 'mine/tool', job: 'find', name: 'My tool' },
  ] } });
  commands.compareOwn = vi.fn(async () => ({ inputSha256: '123456789abc',
    candidate: { says: 'My tool', count: 3, ms: 12, preview: '/candidate.png' },
    baseline: { says: 'Current cells', available: true, count: 2, ms: 8, preview: '/baseline.png' } }));
  commands.useOwn = vi.fn(async () => ({ says: 'Available as a choice' })); page.update(wire);
  expect(root.querySelectorAll('button').some(b => b.textContent === 'Use it')).toBe(false);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Compare on my picture')!, 'click');
  for (let i = 0; i < 5; i++) await Promise.resolve();
  page.update(wire);
  expect(root.textContent).toContain('3 found'); expect(root.textContent).toContain('2 found');
  expect(root.textContent).toContain('12 ms'); expect(root.textContent).toContain('8 ms');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Use it')!, 'click');
  expect(commands.useOwn).toHaveBeenCalledWith('mine/tool', 'trial');
});

test('comparison polling retains pending admission and a retryable failure', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, ownTrials: [
    { id: 'trial', model: 'mine/tool', job: 'measure', name: 'My tool' },
  ] } });
  let reject: (error: Error) => void = () => undefined;
  commands.compareOwn = vi.fn(() => new Promise<OwnComparison>((_resolve, fail) => { reject = fail; })); page.update(wire);
  const compare = () => root.querySelectorAll('button').find(b => b.textContent === 'Compare on my picture')!;
  fire(compare(), 'click'); page.update(wire);
  expect(compare().disabled).toBe(true); fire(compare(), 'click'); expect(commands.compareOwn).toHaveBeenCalledTimes(1);
  reject(new Error('The example failed; correct the capability and retry.')); await settleMicrotasks(); page.update(wire);
  expect(root.textContent).toContain('The example failed'); expect(compare().disabled).toBe(false);
});

test('the retained picture and job accompany a model dropped after a capabilities refresh', async () => {
  const { root, page, wire, commands } = mounted([]);
  commands.ownPicture = vi.fn(async () => ({ id: 'retained-picture' }));
  commands.upload = vi.fn(async () => ({ ok: true, says: 'Added for comparison' }));
  page.update(wire);
  const picture = { name: 'my-sample.png' }, model = { name: 'my-model.pt' };
  Object.assign(root.querySelector('.own-picture')!, { files: [picture] });
  root.querySelector('.own-job')!.value = 'measure';
  page.update(wire);
  fire(root.querySelector('.models-own-drop')!, 'drop', { dataTransfer: { files: [model] } });
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.ownPicture).toHaveBeenCalledWith(picture, 'measure');
  expect(commands.upload).toHaveBeenCalledWith(model, expect.any(Function), 'retained-picture');
  expect(root.textContent).not.toContain('Choose your comparison picture above first');
});

test('a model file dropped with no picture is added, and its comparison asks for the picture when set up', async () => {
  const { root, page, wire, commands } = mounted([]);
  commands.ownPicture = vi.fn(async () => ({ id: 'retained-picture' }));
  commands.upload = vi.fn(async () => ({ ok: true, says: 'Added My cells', model: 'mine/my-cells', kind: 'cellpose-file' }));
  commands.turnOnModel = vi.fn(async () => ({})); page.update(wire);
  fire(root.querySelector('.models-own-drop')!, 'drop', { dataTransfer: { files: [{ name: 'my-model.pt' }] } });
  for (let i = 0; i < 8; i++) await Promise.resolve();
  expect(commands.ownPicture).not.toHaveBeenCalled();
  expect(commands.upload).toHaveBeenCalledWith({ name: 'my-model.pt' }, expect.any(Function), undefined);
  expect(root.textContent).toContain('Added My cells');
  fire(root.querySelector('.tool-added-on')!, 'click');
  expect(commands.turnOnModel).not.toHaveBeenCalled();
  expect(root.textContent).toContain('Choose your comparison picture above');
});

test('an installed community entry with its own adapter offers Turn off and Remove, Remove after a confirm', async () => {
  const entry = tool('lab/c7-image-q', { channel: 'lab', env: { on: true, installed: true } });
  const { root, page, commands } = mounted([entry]);
  commands.deactivate = vi.fn(async () => undefined);
  commands.uninstall = vi.fn(async () => undefined);
  page.show('lab/c7-image-q');
  const buttons = () => root.querySelectorAll('button').map(b => b.textContent);
  expect(buttons()).toContain('Turn off');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Turn off')!, 'click');
  expect(commands.deactivate).toHaveBeenCalledWith('lab/c7-image-q');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Remove')!, 'click');
  expect(commands.uninstall).not.toHaveBeenCalled(); // asked first, in the page's own words
  expect(root.textContent).toContain('Remove lab/c7-image-q’s installed versions?');
  fire(root.querySelector('.model-remove-yes')!, 'click');
  expect(commands.uninstall).toHaveBeenCalledWith('lab/c7-image-q');
});

test('an entry turned off offers Remove but no Turn off; one never installed offers neither', () => {
  const { root, page, commands, wire } = mounted([tool('lab/off', { channel: 'lab', env: { on: false, installed: true } }),
    tool('lab/never', { channel: 'lab', env: { on: false, installed: false } })]);
  commands.deactivate = vi.fn(async () => undefined);
  commands.uninstall = vi.fn(async () => undefined);
  page.update(wire);
  page.show('lab/off');
  expect(root.querySelectorAll('button').map(b => b.textContent)).toContain('Remove');
  expect(root.querySelectorAll('button').map(b => b.textContent)).not.toContain('Turn off');
  page.show('lab/never');
  expect(root.querySelector('.model-remove')).toBeFalsy();
});

test('removal keeps one pending attempt and its recovery instructions through polling and navigation', async () => {
  const entry = tool('lab/remove', { channel: 'lab', env: { on: true, installed: true } });
  const other = tool('lab/other', { channel: 'lab', env: { on: true, installed: true } });
  const { root, page, commands, wire } = mounted([entry, other]);
  let reject!: (error: unknown) => void;
  commands.uninstall = vi.fn(() => new Promise((_, failed) => { reject = failed; }));
  page.show(entry.id);
  fire(root.querySelector('.model-remove')!.querySelector('button')!, 'click');
  const first = root.querySelector('.model-remove-yes')!;
  fire(first, 'click');
  page.update(wire);
  expect(root.querySelector('.model-remove-yes')?.disabled).toBe(true);
  expect(root.querySelector('.model-remove')?.textContent).toContain('Removing');
  fire(first, 'click');
  fire(root.querySelector('.model-remove-yes')!, 'click');
  expect(commands.uninstall).toHaveBeenCalledTimes(1);
  page.show(other.id);
  reject(Object.assign(new Error('This runtime is kept for 2 saved scientific documents.'),
    { reply: { next: 'Turn off the capability instead of removing its runtime.' } }));
  await settleMicrotasks();
  expect(root.querySelector('.tool-details')?.getAttribute('data-tool')).toBe(other.id);
  expect(root.querySelector('.tool-details')?.textContent).not.toContain('2 saved scientific documents');
  page.show(entry.id); page.update(wire);
  expect(root.querySelector('.model-remove')?.textContent).toContain('2 saved scientific documents');
  expect(root.querySelector('.model-remove')?.textContent).toContain('Turn off the capability');
  expect(root.querySelector('.model-remove-yes')?.disabled).toBe(false);
  commands.uninstall = vi.fn(async () => ({ ok: true, removed: true }));
  fire(root.querySelector('.model-remove-yes')!, 'click'); await settleMicrotasks();
  expect(commands.uninstall).toHaveBeenCalledTimes(1);
  expect(root.querySelector('.model-remove-yes')).toBeNull();
});

test('an installation starting after removal confirmation blocks removal until it finishes', () => {
  const entry = tool('lab/remove', { env: { on: true, installed: true } });
  const { root, page, commands, wire } = mounted([entry]);
  commands.uninstall = vi.fn(async () => undefined);
  page.show(entry.id);
  fire(root.querySelector('.model-remove')!.querySelector('button')!, 'click');
  const previous = root.querySelector('.model-remove-yes')!;
  page.update({ ...wire, setup: { edition: 'open', options: {}, installs: [
    { model: entry.id, name: entry.name, state: 'installing', says: 'Checking its update' },
  ] } });
  expect(root.querySelector('.model-remove-yes')?.disabled).toBe(true);
  fire(previous, 'click'); fire(root.querySelector('.model-remove-yes')!, 'click');
  expect(commands.uninstall).not.toHaveBeenCalled();
  page.update(wire);
  expect(root.querySelector('.model-remove-yes')?.disabled).toBe(false);
});

test('an update being installed from its card says what it is doing and has a Stop', () => {
  const entry = tool('lab/c7-image-a', { channel: 'lab', env: { on: true, installed: true }, facts: [
    { kind: 'code', ok: true, says: 'Update available: B. Install makes it beside the one in use (A)', install: 'lab/c7-image-a' }] });
  const { root, page, commands, wire } = mounted([entry]);
  commands.install = vi.fn(async () => undefined);
  commands.installCancel = vi.fn(async () => undefined);
  page.update(wire);
  page.show('lab/c7-image-a');
  expect(root.querySelectorAll('button').map(b => b.textContent)).toContain('Install');
  page.update({ ...wire, setup: { edition: 'open', options: {}, installs: [{ model: 'lab/c7-image-a', name: 'A', state: 'installing',
    says: 'Installing A: checking its adapter contract' }] } });
  expect(root.textContent).toContain('Installing A: checking its adapter contract');
  expect(root.textContent).toContain('beside the one in use (A)');
  expect(root.textContent).not.toContain('This tool is not ready yet');
  const stop = root.querySelectorAll('button').find(b => b.textContent === 'Stop')!;
  fire(stop, 'click');
  expect(commands.installCancel).toHaveBeenCalledWith('lab/c7-image-a');
});

test('a community channel’s row checks it for updates and says the commit it moved to', async () => {
  const { root, page, commands, wire } = mounted([]);
  commands.catalogUpdate = vi.fn(async () => undefined);
  commands.channelUpdate = vi.fn(async () => ({ says: 'Checked lab: now at d6ca512 (was 3b854e3), 3 entries' }));
  const channels = [{ name: 'lab', url: 'https://example.org/lab', commit: '3b854e3f', reviewed: false, trusted: true, checked: '', entries: 3 },
    { name: 'reviewed', url: 'https://example.org/reviewed', commit: 'aa', reviewed: true, trusted: true, checked: '', entries: 9 }];
  page.update({ ...wire, setup: { edition: 'open', options: {}, channels } });
  const checks = root.querySelectorAll('button').filter(b => b.textContent === 'Check for updates');
  expect(checks.length).toBe(1); // a community channel's row only: the reviewed catalog has its own Check
  fire(checks[0], 'click');
  expect(commands.channelUpdate).toHaveBeenCalledWith('lab');
  await Promise.resolve(); await Promise.resolve();
  expect(root.textContent).toContain('Checked lab: now at d6ca512 (was 3b854e3), 3 entries');
});

const keyFacts = (over: Record<string, unknown> = {}) => ({ service: 'openai', name: 'OpenAI API', short: 'OpenAI', getKey: '',
  enables: 'Cloud research', present: false, source: null, sourceSays: '', canSave: true, ...over }) as never;

test('keys are always open in the band, the most useful first, and a direct request goes to them', () => {
  const { root, page, wire, focused } = mounted([tool('basic')], { setup: { edition: 'open', options: {}, keys: {
    OPENAI_API_KEY: keyFacts(),
    GEMINI_API_KEY: keyFacts({ service: 'gemini', name: 'Google Gemini API', short: 'Gemini' }),
    HF_TOKEN: keyFacts({ service: 'huggingface', name: 'Hugging Face', short: 'Hugging Face' }),
    FAL_KEY: keyFacts({ service: 'fal', name: 'fal.ai', short: 'fal.ai', present: true, source: 'keychain' }),
  }, counts: { works: 154, total: 208, unlock: [{ key: 'OPENAI_API_KEY', service: 'OpenAI', getKey: '', adds: 3 },
    { key: 'GEMINI_API_KEY', service: 'Google Gemini', getKey: '', adds: 15 }] } } });
  const keys = root.querySelector('[data-model="keys"]')!;
  expect(keys.tagName).toBe('SECTION'); // never a fold (Will, 2026-10-15)
  expect(keys.textContent).toContain('The example works without a key'); // honest: chat and vision need one
  expect(keys.querySelectorAll('.toolbox-key-name').map(n => n.textContent)).toEqual(['Gemini', 'OpenAI', 'fal.ai']);
  expect(keys.querySelectorAll('.toolbox-key-adds').map(n => n.textContent)).toEqual(['+15 things it can do', '+3 things it can do', 'In use']);
  expect(keys.querySelectorAll('.toolbox-key-act').map(n => n.textContent)).toEqual(['Add key', 'Add key', 'Change']);
  expect(keys.querySelector('.toolbox-key-other')!.textContent).toContain('Hugging Face'); // adds nothing counted: one quiet line
  page.update(wire);
  expect(root.querySelector('[data-model="keys"]')!.tagName).toBe('SECTION');
  expect(keys.id).toBe('toolbox-keys'); // Everything it can do's band is toolbox-all-keys: no id twice in the document
  const views = [bandIds('tools'), bandIds('all')].map(ids => Object.values(ids));
  expect(views[0].filter(id => views[1].includes(id))).toEqual([]);
  // #models/keys (the agent's open_models, acknowledged once shown) lands on this view's keys and focuses them.
  const scrolled = vi.fn();
  Object.assign(TestElement.prototype, { scrollIntoView: scrolled });
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 0; });
  vi.stubGlobal('CSS', { escape: (s: string) => s });
  try {
    expect(toolboxTarget('#models/keys')).toEqual({ tab: 'tools', id: 'keys' });
    page.show('keys');
    expect(root.querySelector('.tool-details')).toBeNull(); // the band, not a tool's details
    expect(focused()?.id).toBe('toolbox-keys');
    expect(scrolled).toHaveBeenCalled();
  } finally { delete (TestElement.prototype as { scrollIntoView?: unknown }).scrollIntoView; }
});

test('the band keeps three clear routes in one guided sheet and accepts old links', () => {
  const { root, page } = mounted([tool('basic')]);
  const frame = root.querySelector('.toolbox-frame')!;
  expect(frame.children.map(c => c.className)).toEqual(['toolbox-tabs', 'toolbox-band', 'toolbox-head', 'models-page-body']);
  expect(root.querySelectorAll('.toolbox-add-way').map(b => b.dataset.way)).toEqual(['find', 'add', 'create']);
  fire(root.querySelectorAll('.toolbox-add-way').find(b => b.dataset.way === 'create')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('What should your tool do?');
  expect(root.querySelectorAll('.tool-setup-sheet')).toHaveLength(1);
  page.show('add-link');
  expect(root.querySelector('.tool-details')).toBeNull();
});

test('unavailable details keep the reason visible without an install or sharing action', () => {
  const reason = 'This integration needs workspace qualification. Reinstalling cannot complete it.';
  const { root, page } = mounted([tool('private-host', { status: 'unavailable', group: 'unavailable',
    face: { state: 'unavailable', says: reason, action: null, also: [] }, shareable: false, setups: [], tries: [] })]);
  page.show('private-host');
  const details = root.querySelector('.tool-details')!;
  expect(details.querySelector('.tool-state')!.textContent).toBe(reason);
  expect(details.querySelector('.tool-setup-act')).toBeNull();
  expect(details.querySelector('.model-share')).toBeNull();
});

test('the sections are Tools\' own (modelSection), in its words, and the community is always there', () => {
  const { root } = mounted([tool('a', { group: 'needs-you', status: 'needs-setup' }), tool('b'),
    tool('c', { group: 'available', status: 'needs-setup', usedFor: [{ role: 'cells', title: 'Cells', current: false, fixed: false }] as never }),
    tool('d', { group: 'unavailable', status: 'unavailable' })]);
  expect(root.querySelector('.toolbox-head-sub')!.textContent).toBe('1 needs you · 1 ready · 1 can be set up · 1 not on this Mac');
  expect(root.querySelectorAll('.toolbox-section-title').map(n => n.textContent)).toEqual(
    ['Needs you1', 'Ready1', 'Can be set up1', 'From the community', 'Not on this Mac1']);
  expect(root.querySelector('[data-section="setup"]')!.querySelector('[data-model="c"]')).not.toBeNull(); // one grid a section, no job subheadings
  expect(root.querySelector('.toolbox-community')!.textContent).toContain('Nothing new in the catalog since you last looked.');
  expect(root.querySelector('.toolbox-unavailable')!.tagName).toBe('DETAILS');
});

test('a card never says its state line twice, offers its key when its setup has no plan, and is never red for a setup step', () => {
  const plan = { option: 'fiji-ap', model: 'fiji-ap', label: 'Analyze Particles', recipe: 'pinned', privacy: '', cost: '',
    steps: [{ kind: 'install', says: 'Set up Fiji first', by: 'app' as const }, { kind: 'check', says: 'Run its functional check', by: 'app' as const }] };
  const face = (says: string) => ({ state: 'needs-setup', says, action: null, also: [] });
  const { root } = mounted([
    tool('fiji-ap', { status: 'needs-setup', group: 'available', face: face('Set up Fiji first'),
      setups: [{ role: 'cells', title: 'Cells', option: 'fiji-ap', plan, canTry: false, recheck: false }] }),
    tool('keyed', { status: 'needs-setup', group: 'available', face: face('Needs your OpenAI key'),
      next: { kind: 'key', says: 'Add your OpenAI key', service: 'openai' } as never,
      setups: [{ role: 'cells', title: 'Cells', option: 'keyed', plan: null, canTry: false, recheck: false }] }),
    tool('stuck', { status: 'needs-setup', group: 'available', face: face('Make its weights here') }),
  ], { setup: { edition: 'open', options: {}, keys: { OPENAI_API_KEY: keyFacts() } } });
  const fiji = root.querySelector('[data-model="fiji-ap"]')!;
  const cell = (card: TestElement) => card.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state');
  expect(fiji.querySelector('.shelf-steps')).toBeNull(); // its steps are in its setup sheet (workspace-feel step 8)
  expect(fiji.querySelector('.tool-terms')).toBeNull(); // nothing to say: no empty line
  expect(fiji.querySelector('.tool-setup-act')!.textContent).toBe('Turn on');
  expect(fiji.querySelectorAll('.model-card-facts').map(n => n.textContent)).toEqual(['On this Mac · free']); // its state line is said once
  expect(cell(fiji)).toBe('idle'); // Not set up is neutral; its press is on the card (#440: red only for Needs attention)
  const keyed = root.querySelector('[data-model="keyed"]')!;
  expect(keyed.querySelector('.model-action')!.textContent).toBe('Add key');
  // Can be set up: the grey outline, Add key on its button; blue is Needs you only (as Tools By model draws it), never red.
  expect(cell(keyed)).toBe('idle');
  expect(cell(root.querySelector('[data-model="stuck"]')!)).toBe('idle');
});

test('Fiji says the functions connected apart from what installs, and never that one runs where Fiji is not set up', async () => {
  const inventory = { extensions: [], why: '', summary: { listed: 555,
    routes: { 'ships-with-fiji': 133, 'update-site': 76, 'none-known': 346 }, bound: [{ id: 'fiji-analyze-particles', name: 'Analyze Particles (Fiji)' }] } };
  const { root, page, commands, wire } = mounted([tool('fiji-analyze-particles', { kind: 'tool-function', status: 'needs-setup', group: 'available' }),
    tool('fiji', { kind: 'tool', status: 'needs-setup', group: 'available' })]);
  commands.fijiInventory = vi.fn(async () => inventory);
  page.update(wire); page.show(null); await settleMicrotasks();
  const fiji = root.querySelector('.toolbox-fiji')!;
  expect(fiji.querySelector('.toolbox-fiji-counts')!.textContent).toBe(
    '1 Fiji function connected · set up Fiji to use it · 555 extensions listed: 209 install in Fiji, 346 have no install route yet');
  expect(fiji.querySelector('.toolbox-fiji-functions')!.textContent).toContain('Analyze Particles (Fiji)');
  expect(commands.fijiInventory).toHaveBeenCalledTimes(1);
  // Fiji set up and the function proven: its row is ready, and only then is it said to be.
  page.update({ ...wire, models: [tool('fiji-analyze-particles', { kind: 'tool-function' }), tool('fiji', { kind: 'tool' })] });
  expect(root.querySelector('.toolbox-fiji-counts')!.textContent).toBe(
    '1 Fiji function connected · 1 ready here · 555 extensions listed: 209 install in Fiji, 346 have no install route yet');
});

test('Every extension, from Everything it can do, opens the Fiji section with its list, not the Fiji card', async () => {
  const { root, page, commands, focused } = mounted([tool('fiji', { kind: 'tool' })]);
  commands.fijiInventory = vi.fn(async () => ({ extensions: [{ name: 'MorphoLibJ', page: '/plugins/morpholibj', categories: ['Segmentation'],
    link: 'https://imagej.net/plugins/morpholibj', route: { kind: 'update-site' as const, site: 'IJPB-plugins' }, how: 'From the IJPB-plugins update site',
    installed: 'Fiji isn\'t installed here yet', usable: 'No binding yet' }], why: '', summary: { listed: 1, routes: { 'update-site': 1 }, bound: [] } }));
  const scrolled = vi.fn();
  Object.assign(TestElement.prototype, { scrollIntoView: scrolled });
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 0; });
  try {
    expect(toolboxTarget('#toolbox/fiji-extensions')).toEqual({ tab: 'tools', id: 'fiji-extensions' });
    page.show('fiji-extensions'); await settleMicrotasks(); await settleMicrotasks();
    const fold = root.querySelector('.models-fiji')!;
    expect((fold as unknown as { open: boolean }).open).toBe(true);
    expect(fold.querySelectorAll('.fiji-row').length).toBe(1);
    expect(root.querySelector('.tool-details')).toBeNull(); // not the Fiji card's details
    expect(scrolled).toHaveBeenCalled();
    expect(focused()?.tagName).toBe('SUMMARY');
  } finally { delete (TestElement.prototype as { scrollIntoView?: unknown }).scrollIntoView; }
});

test('a Fiji list that could not be read says so with Try again, never "Reading the list…" for ever', async () => {
  const { root, page, commands, wire } = mounted([tool('fiji', { kind: 'tool' })]);
  let fail = true;
  commands.fijiInventory = vi.fn(async () => { if (fail) throw new Error('the list could not be read (503)');
    return { extensions: [], why: '', summary: { listed: 2, routes: { 'none-known': 2 }, bound: [] } }; });
  page.update(wire); page.show(null); await settleMicrotasks(); await settleMicrotasks();
  const counts = root.querySelector('.toolbox-fiji-counts')!;
  expect(counts.textContent).toBe('Couldn’t read Fiji’s list. Try again');
  page.update(wire); await settleMicrotasks();
  expect(commands.fijiInventory).toHaveBeenCalledTimes(1); // a wire message never asks again
  fail = false;
  fire(root.querySelector('.toolbox-fiji-again')!, 'click');
  expect(root.querySelector('.toolbox-fiji-counts')!.textContent).toBe('Reading the list…');
  await settleMicrotasks(); await settleMicrotasks();
  expect(root.querySelector('.toolbox-fiji-counts')!.textContent).toBe('No Fiji function connected yet · 2 extensions listed: 0 install in Fiji, 2 have no install route yet');
});

test('tool cards use plain kind labels while preserving their readiness', () => {
  const { root } = mounted([
    tool('bright', { kind: 'tool-function' }), tool('local-model'), tool('cloud', { kind: 'cloud-model', runs: 'cloud', where: 'in the cloud' }),
    tool('atlas', { kind: 'data' }), tool('fiji', { kind: 'tool' }),
  ].map(row => ({ ...row, face: { state: 'unchecked', says: 'Not checked yet', action: null, also: [] } })));
  expect(root.querySelectorAll('.model-card-kind').map(n => n.textContent)).toEqual([
    'Method, on this Mac', 'Model, on this Mac', 'Cloud model, in the cloud', 'Data, on this Mac', 'Tool, on this Mac',
  ]);
  const cells = root.querySelectorAll('.model-card').map(card => card.querySelector('[data-kind="lit-cell"]')!);
  // Ready, not checked yet: it can run, so never the grey of a model that isn't there (Will, 2026-10-29).
  expect(cells.map(n => n.getAttribute('aria-label'))).toEqual(Array(5).fill('Ready · not checked yet'));
  expect(cells.map(n => n.getAttribute('data-state'))).toEqual(Array(5).fill('partial'));
});

test('Try uses the declared parameter form and opens its exact retained table', async () => {
  const r = tool('measure', { newKind: { operation: 'measure', input: 'image.2d.rgb8', output: 'table', gives: 'measurements', proved: true } });
  const { root, page, commands, wire } = mounted([r]);
  Object.assign(commands, { session: () => 's1', tryKind: vi.fn(async () => ({ ok: true, said: '2 rows', table: 'r7',
    tableIdentity: { session: 's1', revision: 2 } })), openTable: vi.fn() });
  wire.capabilities = [{ id: 'catalog:measure', catalog: 'measure', generated: true, actor: 'agent', fields: [
    { key: 'threshold', label: 'Threshold', kind: 'number', numeric: true, required: false, initial: 40, min: 0, max: 255,
      step: 1, hint: 'Only measure brighter pixels', choices: [], said: {}, advanced: false },
    { key: 'smooth', label: 'Smooth first', kind: 'boolean', numeric: false, required: false, initial: true,
      hint: 'Remove small speckles', choices: [], said: {}, advanced: true },
  ] } as unknown as CapabilitiesWire['capabilities'][number]];
  page.update(wire); page.show('measure');
  const threshold = root.querySelectorAll('input').find(n => n.getAttribute('type') === 'number');
  expect(threshold).toBeDefined();
  expect(threshold?.getAttribute('min') ?? (threshold as unknown as HTMLInputElement)?.min).toBe('0');
  threshold!.value = '70'; fire(threshold!, 'input');
  expect(root.textContent).toContain('Only measure brighter pixels');
  page.update(wire);
  expect(root.querySelectorAll('input').find(n => n.getAttribute('type') === 'number')?.value).toBe('70');
  fire(root.querySelector('.tool-kind-try')!, 'click');
  await settleMicrotasks();
  expect(commands.tryKind).toHaveBeenCalledWith('measure', { session: 's1', input: 'image.2d.rgb8', params: { threshold: 70 } });
  expect(commands.openTable).toHaveBeenCalledWith('r7', { session: 's1', revision: 2 });
});

test('advanced Try settings stay open across polling', () => {
  const r = tool('advanced', { newKind: { operation: 'a', input: 'image.2d.rgb8', output: 'text', gives: 'words', proved: true } });
  const { root, page, commands, wire } = mounted([r]);
  Object.assign(commands, { session: () => 's1', tryKind: vi.fn() });
  wire.capabilities = [{ catalog: r.id, actor: 'agent', fields: [{ key: 'smooth', label: 'Smooth', kind: 'boolean',
    choices: [], said: {}, hint: '', numeric: false, required: false, advanced: true }] } as unknown as CapabilitiesWire['capabilities'][number]];
  page.update(wire); page.show(r.id);
  const more = root.querySelector('.tool-kind-trial')!.querySelector('details')!;
  Object.assign(more, { open: true }); fire(more, 'toggle'); page.update(wire);
  expect(root.querySelector('.tool-kind-trial')!.querySelector('details')).toHaveProperty('open', true);
});

test('a proved contribution awaiting comparison offers review instead of normal Try', () => {
  const { root, page } = mounted([tool('pending', {
    group: 'available', execution: { action: 'compare', why: 'Compare your picture before using this capability.' },
    newKind: { operation: 'measure', input: 'image.2d.rgb8', output: 'table.objects', gives: 'measurements', proved: true },
  })]);
  page.show('pending');
  expect(root.querySelector('.tool-kind-try')).toBeNull();
  expect(root.textContent).toContain('Review comparison');
  expect(root.textContent).toContain('Compare your picture before using this capability.');
});


test('unsupported proved tools explain the input limitation without Try or setup', () => {
  const { root, page } = mounted([tool('unsupported', {
    group: 'unavailable', execution: { action: 'unsupported', why: 'This tool requires two images; only one is supported.' },
    newKind: { operation: 'measure', input: 'image.2d.rgb8', output: 'table.objects', gives: 'measurements', proved: true },
  })]);
  page.show('unsupported');
  expect(root.querySelector('.tool-kind-try')).toBeNull();
  expect(root.textContent).toContain('This tool requires two images; only one is supported.');
});

test.each(['measure', 'clear', 'name'])('comparison shortcut prepares the contract job %s', async job => {
  const { root, page, wire, commands } = mounted([tool('candidate', {
    group: 'available', execution: { action: 'choose-comparison-input', why: 'Choose a comparison picture.', job },
  })], { setup: { edition: 'open', options: {} } });
  commands.ownPicture = vi.fn(async () => ({ id: 'matching-picture' }));
  commands.turnOnModel = vi.fn(async () => ({})); page.update(wire);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Choose a comparison picture')!, 'click');
  const picture = { name: 'example.png' };
  Object.assign(root.querySelector('.own-picture')!, { files: [picture] });
  fire(root.querySelector('.tool-added-on')!, 'click');
  await settleMicrotasks();
  expect(commands.ownPicture).toHaveBeenCalledWith(picture, job);
  expect(commands.turnOnModel).toHaveBeenCalledWith('candidate', 'matching-picture');
});

test('setup polling preserves an unsaved inline key and dismissal clears it without stopping work', async () => {
  const { root, commands, page, wire } = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const field = root.querySelector('.tool-setup-sheet')!.querySelector('input')!;
  field.value = 'private-unsaved-fixture';
  page.update(wire);
  expect(root.querySelector('.tool-setup-sheet')!.querySelector('input')).toBe(field);
  expect(field.value).toBe('private-unsaved-fixture');
  fire(root.querySelector('.tool-setup-sheet-go')!, 'click'); await settleMicrotasks();
  wire.setup!.turning = { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big', state: 'waiting',
    attempt: 'key-attempt', says: 'Waiting for your key', step: { kind: 'key', service: 'gemini', says: 'Add key', by: 'you' } } };
  page.update(wire);
  expect(root.querySelector('.tool-setup-sheet')!.querySelector('input')).toBe(field);
  fire(root.querySelector('.tool-setup-sheet-cancel')!, 'click');
  expect(field.value).toBe(''); expect(commands.shelf!.cancel).not.toHaveBeenCalled();
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  expect(root.querySelector('.tool-setup-sheet')!.querySelector('input')?.value).toBe('');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Waiting for your key');
});

test('a changed session prevents setup completion actions', async () => {
  const { root, commands, page, wire } = sheetPage(200e9);
  commands.session = () => 'original';
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  commands.session = () => 'other'; page.update(wire);
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('The session changed.');
  expect(root.querySelector('.tool-setup-sheet-go')).toBeNull();
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('completion follows the whole option and leads with explicit Use when the current choice cannot run', async () => {
  const { root, page, wire, commands } = sheetPage(200e9);
  commands.session = () => 'session';
  commands.selectCapability = vi.fn(async () => ({ ok: true }));
  commands.tryKind = vi.fn(async () => ({ok: true, said: 'Measured the selected picture'}));
  const row = wire.models![0], setup = row.setups![0];
  Object.assign(setup, { recipe: 'pinned', current: 'old', check: { state: 'works' }, ready: false });
  row.status = 'ready';
  row.newKind = {operation: 'cells', input: 'image.2d.rgb8', output: 'labels.instance.2d', gives: 'outlines', proved: true};
  wire.roles = [{ id: 'cells', title: 'Cells', current: 'old', options: [
    { id: 'old', label: 'Old model', readiness: { status: 'needs-setup' } },
  ] }] as CapabilitiesWire['roles'];
  page.update(wire);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const sheet = () => root.querySelector('.tool-setup-sheet')!;
  expect(sheet().textContent).not.toContain('Ready to use.');
  expect(sheet().querySelectorAll('button').map(b => b.textContent)).not.toContain('Use big');
  Object.assign(setup, { ready: true, recheck: true, canTry: true });
  setup.plan!.steps = [{kind: 'check', says: 'Check it here', by: 'app'}];
  page.update(wire);
  expect(sheet().textContent).toContain('Cells uses Old model, which can’t run here. Use big?');
  expect(sheet().querySelectorAll('.mm-primary').map(b => b.textContent)).toEqual(['Use big']);
  expect(commands.selectCapability).not.toHaveBeenCalled();
  fire(sheet().querySelectorAll('button').find(b => b.textContent === 'Use big')!, 'click');
  expect(commands.selectCapability).toHaveBeenCalledWith({job: 'cells', option: 'big', current: 'old', session: 'session', recipe: 'pinned'});
  await settleMicrotasks();
  fire(sheet().querySelector('.tool-kind-try')!, 'click');
  expect(commands.tryKind).toHaveBeenCalledWith('big', {session: 'session', input: 'image.2d.rgb8'});
  expect(commands.shelf!.tryIt).not.toHaveBeenCalled();
});

test('refresh reconstructs a waiting setup from references without repeating admission', async () => {
  const values = new Map<string, string>();
  vi.stubGlobal('sessionStorage', { getItem: (k: string) => values.get(k) ?? null,
    setItem: (k: string, v: string) => values.set(k, v), removeItem: (k: string) => values.delete(k) });
  const first = sheetPage(200e9);
  fire(first.root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  first.root.querySelector('.tool-setup-sheet')!.querySelector('input')!.value = 'unsaved-fixture';
  const next = sheetPage(200e9);
  next.wire.setup!.turning = { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big', state: 'waiting',
    attempt: 'owned-attempt', says: 'Waiting for your key', step: {kind: 'key', service: 'gemini', says: 'Add key', by: 'you'} } };
  next.page.update(next.wire);
  const sheet = next.root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Waiting for your key');
  expect(sheet.querySelector('input')?.value).toBe('');
  expect(next.commands.shelf!.turnOn).not.toHaveBeenCalled();
  expect([...values.values()].join('')).not.toContain('unsaved-fixture');
});

test('repeated setup presses admit once and a failed admission keeps an actionable sheet', async () => {
  const { root, commands } = sheetPage(200e9);
  let finish: (value: unknown) => void = () => {};
  commands.shelf!.turnOn = vi.fn(() => new Promise<unknown>(resolve => { finish = resolve; }));
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const go = root.querySelector('.tool-setup-sheet-go')!;
  fire(go, 'click'); fire(go, 'click');
  expect(commands.shelf!.turnOn).toHaveBeenCalledTimes(1);
  finish({ok: false, error: 'The requirement changed. Check again.'}); await settleMicrotasks();
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('The requirement changed. Check again.');
  expect(root.querySelector('.tool-setup-sheet-go')!.disabled).toBe(false);
});

test('expanded setup details and keyboard position survive progress updates', async () => {
  const {root, page, wire, focused} = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const details = root.querySelector('.tool-setup-details')!;
  Object.assign(details, {open: true}); fire(details, 'toggle');
  const summary = details.querySelector('summary')! as unknown as HTMLElement; summary.focus();
  page.update(wire);
  expect(root.querySelector('.tool-setup-details')!.getAttribute('open')).not.toBeNull();
  expect(focused()).toBe(root.querySelector('.tool-setup-details')!.querySelector('summary'));
  (root.querySelector('.tool-setup-sheet-go')! as unknown as HTMLElement).focus();
  wire.setup!.turning = {cells: {job:'cells', option:'big', model:'big', label:'Big', state:'running',
    attempt:'owned-attempt', says:'Checking it here'}};
  page.update(wire);
  expect(focused()).toBe(root.querySelector('.tool-setup-sheet'));
});

test('the create route retains request and focus through polling and presents the handoff in the same sheet', async () => {
  const { root, page, wire, commands, focused } = mounted([tool('existing')]);
  commands.session = () => 's1';
  commands.workspace = { create: vi.fn(async () => ({ id: 'w1', folder: '/private/workspaces/w1', candidate: 'cap-1', instructions: 'Canonical rules and commands' })), read: vi.fn(), reveal: vi.fn() };
  page.show('add-build');
  const field = (label: string) => root.querySelectorAll('label').find(node => node.textContent.startsWith(label))!.children[0];
  const description = field('Describe the job'); description.value = 'Measure bright spots'; fire(description, 'input');
  (description as TestElement & { focus(): void }).focus();
  const method = field('Where to create it'); method.value = 'coder'; fire(method, 'change');
  page.update(wire); expect(field('Describe the job')).toBe(description); expect(focused()).toBe(description);
  const prepare = root.querySelectorAll('button').find(b => b.textContent === 'Prepare instructions for my coding tool')!;
  fire(prepare, 'click'); fire(prepare, 'click'); await settleMicrotasks(); await settleMicrotasks();
  expect(commands.workspace!.create).toHaveBeenCalledTimes(1);
  expect(commands.workspace!.create).toHaveBeenCalledWith(expect.objectContaining({ description: 'Measure bright spots' }), 's1');
  expect(root.querySelectorAll('.tool-setup-sheet')).toHaveLength(1);
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Canonical rules and commands');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Import completed capability');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('closing an admitted coder request retains its result for reopening without a duplicate create', async () => {
  const values = new Map<string, string>(); vi.stubGlobal('sessionStorage', { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  const { root, page, wire, commands } = mounted([]);
  commands.session = () => 's1';
  let finish: (value: { id: string; folder: string; candidate: string; instructions: string }) => void = () => undefined;
  commands.workspace = { create: vi.fn(() => new Promise<CapabilityWorkspace>(resolve => { finish = resolve; })), read: vi.fn(), reveal: vi.fn() };
  page.show('add-build');
  const field = (label: string) => root.querySelectorAll('label').find(node => node.textContent.startsWith(label))!.children[0];
  const description = field('Describe the job'); description.value = 'Count spots'; fire(description, 'input');
  const method = field('Where to create it'); method.value = 'coder'; fire(method, 'change');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Prepare instructions for my coding tool')!, 'click');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Close')!, 'click');
  finish({ id: 'retained', folder: '/private/retained', candidate: 'cap-r', instructions: 'Retained request' });
  await settleMicrotasks(); await settleMicrotasks(); page.update(wire);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Continue setup')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Retained request');
  expect(commands.workspace!.create).toHaveBeenCalledTimes(1);
  expect(JSON.parse(values.get('capability-flow')!).workspace).toBe('retained');
  const file = new File(['zip'], 'candidate.zip');
  let imported: (value: { model: string; says: string }) => void = () => undefined;
  commands.importCapability = vi.fn(() => new Promise<{ model: string; says: string }>(resolve => { imported = resolve; }));
  Object.assign(root.querySelectorAll('input').find(node => node.getAttribute('aria-label') === 'Completed capability package')!, { files: [file] });
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Import completed capability')!, 'click');
  page.update(wire);
  const again = root.querySelectorAll('button').find(b => b.textContent === 'Import completed capability')!;
  expect(again.disabled).toBe(true); fire(again, 'click'); expect(commands.importCapability).toHaveBeenCalledTimes(1);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Close')!, 'click');
  imported({ model: 'community/new', says: 'Added for review' }); await settleMicrotasks(); await settleMicrotasks();
  expect(JSON.parse(values.get('capability-flow')!).candidate).toBe('community/new');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test.each(['workspace', 'candidate'])('a refused request retains its published %s but requires explicit session resume', async kind => {
  const values = new Map<string, string>(); vi.stubGlobal('sessionStorage', { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  const { root, page, wire, commands } = mounted([]); let session = 's1'; commands.session = () => session;
  let reject: (error: Error) => void = () => undefined;
  commands.workspace = { create: vi.fn(() => new Promise<CapabilityWorkspace>((_resolve, fail) => { reject = fail; })),
    read: vi.fn(async () => ({ id: 'recovered', folder: '/private/recovered', candidate: 'cap-r', instructions: 'Recovered instructions' })), reveal: vi.fn() };
  page.show('add-build');
  const field = (label: string) => root.querySelectorAll('label').find(node => node.textContent.startsWith(label))!.children[0];
  const description = field('Describe the job'); description.value = 'Count spots'; fire(description, 'input');
  const method = field('Where to create it'); method.value = 'coder'; fire(method, 'change');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Prepare instructions for my coding tool')!, 'click');
  session = 's2';
  reject(Object.assign(new Error('Session changed; your private artifact is kept.'), { recovery: { session: 's1',
    ...(kind === 'workspace' ? { workspace: { id: 'recovered', folder: '/private/recovered', candidate: 'cap-r' } } : { candidate: 'community/recovered' }) } }));
  await settleMicrotasks(); await settleMicrotasks(); page.update(wire);
  expect(JSON.parse(values.get('capability-flow')!)[kind]).toBe(kind === 'workspace' ? 'recovered' : 'community/recovered');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Continue in this session');
  expect(commands.workspace.read).not.toHaveBeenCalled(); expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Continue in this session')!, 'click');
  await settleMicrotasks(); await settleMicrotasks();
  if (kind === 'workspace') expect(commands.workspace.read).toHaveBeenCalledWith('recovered', 's2');
  else expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Continue with your added capability');
  expect(commands.workspace.create).toHaveBeenCalledTimes(1);
});

test('a hidden creation failure is explained when its task is reopened and can be retried', async () => {
  const values = new Map<string, string>(); vi.stubGlobal('sessionStorage', { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  const { root, page, wire, commands } = mounted([]); commands.session = () => 's1';
  let reject: (error: Error) => void = () => undefined;
  commands.workspace = { create: vi.fn(() => new Promise<CapabilityWorkspace>((_resolve, fail) => { reject = fail; })), read: vi.fn(), reveal: vi.fn() };
  page.show('add-build');
  const field = (label: string) => root.querySelectorAll('label').find(node => node.textContent.startsWith(label))!.children[0];
  const description = field('Describe the job'); description.value = 'Count spots'; fire(description, 'input');
  const method = field('Where to create it'); method.value = 'coder'; fire(method, 'change');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Prepare instructions for my coding tool')!, 'click');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Close')!, 'click');
  page.update(wire); fire(root.querySelectorAll('button').find(b => b.textContent === 'Continue setup')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('still being prepared');
  reject(new Error('Not enough disk space to create this workspace.')); await settleMicrotasks(); await settleMicrotasks();
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Not enough disk space');
  expect(field('Describe the job').value).toBe('Count spots');
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Prepare instructions for my coding tool')!, 'click');
  expect(commands.workspace!.create).toHaveBeenCalledTimes(2);
  reject(new Error('Still no disk space')); await settleMicrotasks(); await settleMicrotasks();
});

function retainedJourney(reference: { workspace?: string; draft?: string }) {
  const values = new Map<string, string>([['capability-flow', JSON.stringify({ route: 'create', model: '', role: '', option: '', session: 's1', ...reference })]]);
  vi.stubGlobal('sessionStorage', { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  const view = mounted([], { setup: { edition: 'open', options: {}, drafts: [
    { id: 'd1', state: 'ran', link: '', tries: 1, last: { count: 3 }, model: '', review: { digest: 'r1', checks: [] } },
  ] } });
  view.commands.session = () => 's1';
  const button = (name: string) => view.root.querySelector('.tool-setup-sheet')!.querySelectorAll('button').find(b => b.textContent === name)!;
  return { ...view, values, button };
}

test('a coder import recovered after session change can continue without importing twice', async () => {
  const { root, page, wire, commands, values, button } = retainedJourney({ workspace: 'w1' });
  commands.workspace = { create: vi.fn(), reveal: vi.fn(), read: vi.fn(async () => ({ id: 'w1', folder: '/private/w1', candidate: 'cap-w1', instructions: 'Instructions' })) };
  let reject: (error: Error) => void = () => undefined;
  commands.importCapability = vi.fn(() => new Promise<{ model: string; says: string }>((_resolve, fail) => { reject = fail; }));
  page.update(wire); await settleMicrotasks(); await settleMicrotasks();
  Object.assign(root.querySelectorAll('input').find(n => n.getAttribute('aria-label') === 'Completed capability package')!, { files: [new File(['zip'], 'done.zip')] });
  fire(button('Import completed capability'), 'click'); commands.session = () => 's2';
  reject(Object.assign(new Error('The session changed. Your candidate was kept.'), { recovery: { session: 's1', candidate: 'community/recovered' } }));
  await settleMicrotasks(); await settleMicrotasks(); page.update(wire);
  fire(button('Continue in this session'), 'click'); await settleMicrotasks();
  expect(button('Continue with your added capability')).toBeDefined();
  fire(button('Continue with your added capability'), 'click'); await settleMicrotasks();
  expect(JSON.parse(values.get('capability-flow')!).model).toBe('community/recovered');
  expect(commands.importCapability).toHaveBeenCalledTimes(1); expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test.each(['close', 'session', 'other-task'])('draft adoption retains origin across %s without selecting another task', async change => {
  const { root, page, wire, commands, values, button } = retainedJourney({ draft: 'd1' });
  let finish: (value: { model: string }) => void = () => undefined;
  commands.useDraft = vi.fn(() => new Promise<{ model: string }>(resolve => { finish = resolve; })); page.update(wire);
  fire(button('Use it'), 'click'); await settleMicrotasks(); page.update(wire);
  expect(button('Use it').disabled).toBe(true); fire(button('Use it'), 'click');
  expect(commands.useDraft).toHaveBeenCalledTimes(1);
  if (change === 'session') { commands.session = () => 's2'; page.sessionChanged(); }
  else { fire(button('Close'), 'click'); if (change === 'other-task') page.show('add-link'); }
  wire.setup!.drafts![0].state = 'used'; wire.setup!.drafts![0].model = 'mine/completed';
  finish({ model: 'mine/completed' }); await settleMicrotasks(); await settleMicrotasks(); page.update(wire);
  if (change === 'other-task') {
    expect(JSON.parse(values.get('capability-flow')!).candidate).toBeUndefined();
    expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Model or GitHub link');
  } else {
    expect(JSON.parse(values.get('capability-flow')!).candidate).toBe('mine/completed');
    if (change === 'close') fire(root.querySelectorAll('button').find(b => b.textContent === 'Continue setup')!, 'click');
    else { expect(button('Continue in this session')).toBeDefined(); fire(button('Continue in this session'), 'click'); await settleMicrotasks(); }
    expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Your draft is now a capability');
    expect(button('Continue with your added capability')).toBeDefined();
  }
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('draft adoption failure remains visible after close and polling and supports retry', async () => {
  const { root, page, wire, commands, button } = retainedJourney({ draft: 'd1' });
  let reject: (error: Error) => void = () => undefined;
  commands.useDraft = vi.fn(() => new Promise<{ model?: string; says?: string }>((_resolve, fail) => { reject = fail; })); page.update(wire);
  fire(button('Use it'), 'click'); await settleMicrotasks(); fire(button('Close'), 'click');
  reject(new Error('The candidate changed. Compare it again.')); await settleMicrotasks(); await settleMicrotasks(); page.update(wire);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Continue setup')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('The candidate changed. Compare it again.');
  fire(button('Use it'), 'click'); await settleMicrotasks(); expect(commands.useDraft).toHaveBeenCalledTimes(2);
  reject(new Error('Still changed')); await settleMicrotasks(); await settleMicrotasks();
});

test('an AI draft is used only through its review: the code, packages, files and every try are shown and Use it sends its digest', async () => {
  const review = { digest: 'abc123def4567890', says: 'Drafted by AI.', adapter: 'import numpy\ndef segment(picture, params):\n    return 0',
    requirements: ['numpy==2.3.5'], packages: ['numpy==2.3.5', 'scipy==1.16.3'], imports: ['numpy'], facts: ['It imports numpy.'],
    files: [{ path: '__init__.py', bytes: 45097, sha256: '94cdeec603132e23', url: 'https://raw.githubusercontent.com/x/y/f0a/__init__.py' }],
    checks: [{ blocks: false, says: 'a note' }],
    tries: [{ ok: false, error: 'ImportError: scipy 1.15.3 refused' }, { ok: true, count: 323 }] };
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, drafts: [
    { id: 'd1', link: 'https://github.com/x/y', state: 'ran', tries: 2, last: { ok: true, count: 323 }, model: '', review }] } });
  commands.useDraft = vi.fn(async () => ({ model: 'mine/d1' })); page.update(wire);
  const shown = root.querySelector('.models-draft-review')!;
  for (const words of ['def segment(picture, params)', 'scipy==1.16.3', '__init__.py', 'raw.githubusercontent.com', 'failed: ImportError: scipy 1.15.3 refused',
    'ran, found 323', 'Note: a note', 'abc123def456', 'these pinned files, each by its hash'])
    expect(shown.textContent).toContain(words);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Use it')!, 'click'); await settleMicrotasks();
  expect(commands.useDraft).toHaveBeenCalledWith('d1', 'abc123def4567890');
  wire.setup!.drafts![0].review = { ...review, checks: [{ blocks: true, says: 'it fetches from the network at run time' }] }; page.update(wire);
  expect(root.querySelectorAll('button').find(b => b.textContent === 'Use it')).toBeUndefined();
  expect(root.textContent).toContain('Not offered: it fetches from the network at run time');
  wire.setup!.drafts![0].review = null; page.update(wire);
  expect(root.querySelectorAll('button').find(b => b.textContent === 'Use it')).toBeUndefined();
  wire.setup!.drafts![0].review = { busy: true, unavailable: 'it is being updated right now' }; page.update(wire);
  expect(root.textContent).toContain('Its review is being updated; it appears here in a moment.');
  expect(root.textContent).not.toContain('could not be read');
  expect(root.querySelectorAll('button').find(b => b.textContent === 'Use it')).toBeUndefined();
  wire.setup!.drafts![0].review = { ...review, checks: [], scanned: ['adapter.py', '__init__.py (pinned)'] }; page.update(wire);
  expect(root.textContent).toContain('Static checks found nothing in adapter.py, __init__.py (pinned).');
});

test('a failed try says the step that failed as well as its cause', () => {
  expect(errorWords('locking its requirements (wheels only) failed:\n  × No solution found\n  ╰─▶ scipy==1.99 has no wheels'))
    .toBe('locking its requirements (wheels only) failed: … ╰─▶ scipy==1.99 has no wheels');
  expect(errorWords('ImportError: macOS refused scipy')).toBe('ImportError: macOS refused scipy');
  expect(errorWords('\n')).toBe('no reason kept');
});

test('legacy draft comparison keeps pending and errors through polling', async () => {
  const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, drafts: [
    { id: 'legacy', trial: 'picture', state: 'ran', link: '', tries: 1, last: { count: 3 }, model: '' },
  ] } });
  let reject: (error: Error) => void = () => undefined;
  commands.compareOwn = vi.fn(() => new Promise<OwnComparison>((_resolve, fail) => { reject = fail; })); page.update(wire);
  const compare = () => root.querySelectorAll('button').find(b => b.textContent === 'Compare on my picture')!;
  fire(compare(), 'click'); await settleMicrotasks(); page.update(wire); expect(compare().disabled).toBe(true);
  fire(compare(), 'click'); expect(commands.compareOwn).toHaveBeenCalledTimes(1);
  reject(new Error('No memory is available. Try again.')); await settleMicrotasks(); await settleMicrotasks(); page.update(wire);
  expect(root.textContent).toContain('No memory is available'); expect(compare().disabled).toBe(false);
});

test('refresh reconstructs an adopted draft from its retained wire model without promoting again', () => {
  const { root, page, wire, commands, values, button } = retainedJourney({ draft: 'd1' });
  commands.useDraft = vi.fn(); wire.setup!.drafts![0].state = 'used'; wire.setup!.drafts![0].model = 'mine/retained';
  page.update(wire);
  expect(JSON.parse(values.get('capability-flow')!).candidate).toBe('mine/retained');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Your draft is now a capability');
  expect(button('Continue with your added capability')).toBeDefined(); expect(commands.useDraft).not.toHaveBeenCalled();
});

test('explicitly promoted current version still offers rollback and candidate presses bind the shown generation', () => {
  const r = tool('mine/tool', { versions: [
    { generation: 'g2', version: '2', at: '', inUse: true, chosen: true },
    { generation: 'g1', version: '1', at: '', inUse: false, chosen: false }] });
  const { root, page, wire, commands } = mounted([r]);
  commands.setVersion = vi.fn(async () => ({ says: 'Selected' }));
  page.update(wire); page.show(r.id);
  expect(root.querySelectorAll('button').some(b => b.textContent === 'Go back to 1')).toBe(true);
  expect(root.querySelectorAll('button').some(b => b.textContent === 'Use the newest')).toBe(false);
  r.versions![0].inUse = false; r.versions![1].inUse = true;
  page.update(wire);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Use the newest')!, 'click');
  expect(commands.setVersion).toHaveBeenCalledWith('mine/tool', 'g2');
});

test('a missing selected generation offers the one surviving proven version explicitly', () => {
  const r = tool('mine/tool', { versions: [
    { generation: 'g1', version: '1', at: '', inUse: false, chosen: false }] });
  const { root, page, wire, commands } = mounted([r]);
  commands.setVersion = vi.fn(async () => ({ says: 'Selected' }));
  page.update(wire); page.show(r.id);
  const button = root.querySelectorAll('button').find(b => b.textContent === 'Use the newest');
  expect(button).toBeDefined();
  fire(button!, 'click');
  expect(commands.setVersion).toHaveBeenCalledWith('mine/tool', 'g1');
});

test('setup explains the action and destination before admitting a download', async () => {
  const plan = sheetPlan(); plan.steps = [{ kind: 'download', says: 'Download 88 MB', model: 'big', bytes: 88e6, by: 'app' }];
  const { root, wire, page, commands } = sheetPage(200e9, plan);
  wire.models![0].folder = '~/MicroscopeMind/catalog/models/big/pinned'; page.update(wire);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.tool-setup-steps')?.textContent).toContain('To use this tool');
  expect(sheet.querySelector('.tool-setup-steps')?.textContent).toContain('Download 88 MB');
  expect(sheet.querySelector('.tool-setup-destination')?.textContent).toContain('~/MicroscopeMind/catalog/models/big/pinned');
  expect(sheet.querySelector('.tool-setup-sheet-go')?.textContent).toBe('Download 88 MB and turn on');
  expect(sheet.textContent).not.toContain('Nothing starts until you press'); // its primary's label says what starts
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
});

test('setup shows real bytes, file checking and indeterminate install progress in place', async () => {
  const { root, wire, page } = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  wire.setup!.turning = { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big', state: 'running', attempt: 'a', says: 'Downloading',
    step: { kind: 'download', model: 'big', says: 'Download', by: 'app' } } };
  wire.setup!.downloads = [{ model: 'big', name: 'Big', state: 'downloading', got: 22e6, total: 88e6, says: 'Fetching weights' },
    { model: 'unrelated', name: 'Other', state: 'downloading', got: 99e6, total: 100e6, says: 'Other download' }];
  page.update(wire);
  let sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('progress')?.getAttribute('value')).toBe('22000000');
  expect(sheet.querySelector('progress')?.getAttribute('max')).toBe('88000000');
  expect(sheet.textContent).toContain('22 MB of 88 MB · 25%');
  expect(sheet.textContent).not.toContain('Other download');
  wire.setup!.downloads[0].state = 'checking'; page.update(wire);
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Checking downloaded files');
  expect(root.querySelector('.tool-setup-sheet')!.querySelector('progress')?.getAttribute('value')).toBeNull();
  wire.setup!.turning.cells.step = { kind: 'install', model: 'big', says: 'Install', by: 'app' };
  wire.setup!.installs = [{ model: 'big', name: 'Big', state: 'installing', says: 'Installing pinned packages', folder: '/private/catalog/envs/big/generation' }];
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('progress')?.getAttribute('value')).toBeNull();
  expect(sheet.textContent).toContain('Installing pinned packages');
  expect(sheet.textContent).toContain('/private/catalog/envs/big/generation');
  expect(sheet.textContent).not.toContain('25%');
});

test('setup marks verified downloads green without claiming readiness before checks pass', async () => {
  const { root, wire, page } = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const row = wire.models![0];
  row.facts = [{ kind: 'files', ok: null, says: 'Checking files' }]; page.update(wire);
  expect(root.querySelector('.tool-setup-success')).toBeNull();
  row.facts[0].ok = true; page.update(wire);
  expect(root.querySelector('.tool-setup-success')?.textContent).toContain('Downloaded');
  expect(root.querySelector('.tool-setup-success')?.textContent).not.toContain('Ready');
  row.setups![0].ready = true; page.update(wire);
  expect(root.querySelector('.tool-setup-success')?.textContent).toContain('Ready to use'); // its accessible words
  expect(root.querySelector('.tool-setup-success')?.textContent).toContain('big is ready');
  expect(root.querySelector('.tool-setup-success')!.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('ready'); // the lit cell, not a green ✓
  row.setups![0].ready = false; row.facts[0].ok = false; page.update(wire);
  expect(root.querySelector('.tool-setup-success')).toBeNull();
});

test('prepared unchecked tools stay outside Ready and missing files lead to setup', () => {
  const unchecked = Array.from({ length: 5 }, (_, i) => tool(`unchecked-${i}`, {
    group: 'available', face: { also: [], state: 'unchecked', says: 'Not checked on this Mac', action: { kind: 'check', says: 'Check it here' } },
  }));
  const checked = ['one', 'two'].map(id => tool(id, { face: { also: [], state: 'on', says: 'Check passed', action: null } }));
  const { root, commands } = mounted([...unchecked, ...checked, tool('trackastra', {
    status: 'needs-setup', group: 'available', word: 'Download 43 MB',
    face: { also: [], state: 'needs-setup', says: 'Download 43 MB', action: { kind: 'download', says: 'Download 43 MB', model: 'trackastra' } },
    setups: [{ role: 'fixed:linking', title: 'Linking', option: 'primary', recheck: false, canTry: false,
      plan: { option: 'primary', model: 'trackastra', label: 'Trackastra', recipe: 'fixture', privacy: '', cost: '',
        steps: [{ kind: 'download', says: 'Download 43 MB', model: 'trackastra', by: 'app' }] } }],
  })]);
  expect(root.querySelector('.toolbox-head-sub')!.textContent).toBe('2 ready · 5 ready, not checked yet · 1 can be set up');
  // One meaning of Ready (run-spec.ts modelSection): Ready, not checked yet is its own section, as in Tools By model.
  expect(root.querySelector('[data-section="ready"]')!.querySelectorAll('.model-card').map(n => n.dataset.model)).toEqual(['one', 'two']);
  expect(root.querySelector('[data-section="unchecked"]')!.querySelectorAll('.model-card')).toHaveLength(5);
  const card = root.querySelector('[data-model="trackastra"]')!;
  // Its one verb names its download, in the server's own size words, and starts it in place (the card then fills).
  expect(card.querySelector('.model-card-download')!.textContent).toBe('Download 43 MB');
  expect(card.querySelector('.model-card-facts')!.textContent).toBe('43 MB · free');
  expect(card.textContent).not.toContain('Check it here');
  fire(card.querySelector('.model-card-download')!, 'click');
  expect(commands.download).toHaveBeenCalledWith('trackastra');
});

test('one package exposes separate usable tools and keeps their Try identities and settings', async () => {
  const row = tool('image-tools', { memberKinds: [
    { key: 'image-tools#invert', name: 'Invert image', what: 'Invert pixels', operation: 'invert', input: 'image.2d.rgb8', output: 'image.2d.rgb8', gives: 'a picture', proved: true, proof: 'CPU reference passed' },
    { key: 'image-tools#mean', name: 'Mean intensity', what: 'Measure pixels', operation: 'mean', input: 'image.2d.rgb8', output: 'table.objects', gives: 'measurements', proved: true, proof: 'CPU reference passed' },
  ] });
  const { root, page, commands, wire } = mounted([row]);
  Object.assign(commands, { session: () => 's1', tryKind: vi.fn(async () => ({ ok: true, said: 'Done' })) });
  wire.capabilities = [{ catalog: 'image-tools#invert', actor: 'agent', fields: [
    { key: 'offset', label: 'Offset', kind: 'number', numeric: true, required: false, initial: 0, min: 0, max: 5,
      step: 1, hint: '', choices: [], said: {}, advanced: false },
  ] } as unknown as CapabilitiesWire['capabilities'][number]];
  page.update(wire); page.show('image-tools');
  expect(root.querySelectorAll('.tool-kind-trial').map(n => n.querySelector('h4')?.textContent)).toEqual(['Invert image', 'Mean intensity']);
  const offset = root.querySelectorAll('input').find(n => n.getAttribute('type') === 'number')!;
  offset.value = '1'; fire(offset, 'input'); page.update(wire);
  fire(root.querySelectorAll('.tool-kind-try')[0], 'click'); await settleMicrotasks();
  expect(commands.tryKind).toHaveBeenLastCalledWith('image-tools#invert', { session: 's1', input: 'image.2d.rgb8', params: { offset: 1 } });
  fire(root.querySelectorAll('.tool-kind-try')[1], 'click'); await settleMicrotasks();
  expect(commands.tryKind).toHaveBeenLastCalledWith('image-tools#mean', { session: 's1', input: 'image.2d.rgb8' });
});

test('bundle reference review stays inside setup and enables only the reviewed generation', async () => {
  const model = tool('bundle', { group: 'available', execution: { action: 'review-bundle', why: 'Review checked results.' },
    setups: [{ role: 'model:bundle', title: 'Image tools', option: 'bundle', ready: true, plan: null, canTry: false, recheck: false }] });
  const { root, page, wire, commands } = mounted([model]);
  commands.session = () => 'session-a';
  const artifact = {kind: 'image' as const, sha256: 'a'.repeat(64), bytes: 100};
  const reviewed = { generation: 'g1', digest: 'd1', says: 'Checked', cases: [{case: 'case1', member: 'bundle#invert',
    operation: 'invert', device: 'cpu', source: 'Synthetic reference', parameters: {}, tolerance: {atol: 0}, metrics: {mismatches: 0},
    artifacts: {input: artifact, actual: artifact, reference: artifact}}] };
  commands.reviewBundle = vi.fn(async () => reviewed);
  page.update(wire);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Review results')!, 'click');
  await settleMicrotasks();
  expect(root.querySelector('.tool-setup-sheet')).not.toBeNull();
  expect(commands.reviewBundle).not.toHaveBeenCalled();
  fire(root.querySelector('.tool-setup-sheet')!.querySelectorAll('button').find(b => b.textContent === 'Review results')!, 'click');
  await settleMicrotasks();
  await settleMicrotasks();
  expect(commands.reviewBundle).toHaveBeenCalledWith('bundle', 'session-a', undefined);
  expect(root.textContent).toContain('Actual result');
  expect(root.textContent).toContain('Expected reference');
  expect(root.textContent).toContain('Reference check passed on CPU');
  expect(root.textContent).toContain('Review the recorded results before enabling this tool');
  expect(root.querySelector('.tool-kind-try')).toBeNull();
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Enable these tools')!, 'click');
  await settleMicrotasks();
  expect(commands.reviewBundle).toHaveBeenLastCalledWith('bundle', 'session-a', {generation: 'g1', digest: 'd1'});
});

test('Try chooses an exact retained input without replacing the stage and forwards its revision', async () => {
  const row = tool('mean', { newKind: { operation: 'mean', input: 'image.2d.rgb8', output: 'table.objects', gives: 'measurements', proved: true } });
  const { root, page, commands, wire } = mounted([row]);
  const reference = { session: 's1', id: 'd1', component: 'picture-0', revision: 'a'.repeat(64) };
  Object.assign(commands, { session: () => 's1', tryKind: vi.fn(async () => ({ ok: true })),
    inputResources: vi.fn(async () => ({ inputs: [{ title: 'Same pixels, another result', reference: { ...reference, id: 'd2' } }, { title: 'Inverted image', reference, compatibility: { compatible: false, says: 'Intensity meaning is unknown. Choose an intensity resource.' } }] })), chooseInput: vi.fn() });
  page.update(wire); page.show('mean');
  fire(root.querySelectorAll('button').find(n => n.textContent === 'Find retained results')!, 'click');
  await settleMicrotasks(); await settleMicrotasks();
  const select = root.querySelector('.tool-retained-input')!;
  select.value = JSON.stringify(reference); fire(select, 'change');
  expect(commands.inputResources).toHaveBeenCalledWith('s1', 'mean');
  expect(root.textContent).toContain('Intensity meaning is unknown. Choose an intensity resource.');
  fire(root.querySelector('.tool-kind-try')!, 'click'); await settleMicrotasks();
  expect(commands.tryKind).toHaveBeenCalledWith('mean', { session: 's1', input: 'image.2d.rgb8', resource: reference });
  expect(commands.chooseInput).not.toHaveBeenCalled();
  commands.session = () => 's2'; page.update(wire);
  expect(root.textContent).not.toContain('Using Inverted image.');
});

test('functional checking and failed setup stay in the sheet without a premature Ready action', async () => {
  const { root, wire, page, commands } = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  wire.setup!.turning = { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big', state: 'running', attempt: 'owned-attempt', says: 'Running functional check',
    step: { kind: 'check', model: 'big', says: 'Check operation', by: 'app' } } };
  page.update(wire);
  let sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.tool-setup-sheet-body')!.dataset.state).toBe('working');
  expect(sheet.querySelector('progress')?.getAttribute('value')).toBeNull();
  expect(sheet.textContent).toContain('Running functional check');
  expect(sheet.textContent).not.toContain('Ready to use');
  wire.setup!.turning.cells.state = 'failed'; wire.setup!.turning.cells.says = 'Dependency check failed. Retry after correcting setup.';
  page.update(wire); sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.tool-setup-sheet-body')!.dataset.state).toBe('failed');
  expect(sheet.textContent).toContain('Setup couldn’t finish'); expect(sheet.textContent).toContain('Retry setup');
  expect(sheet.querySelector('progress')).toBeNull();
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  fire(sheet.querySelector('.tool-setup-sheet-go')!, 'click'); await settleMicrotasks();
  expect(commands.shelf!.turnOn).toHaveBeenCalledTimes(1);
});

test('opening the original add URL after reload preserves the private workspace reference', async () => {
  const values = new Map<string, string>([['capability-flow', JSON.stringify({route: 'create', model: '', role: '', option: '', session: 's1', workspace: 'workspace-1', values: {source: 'https://example.org/tool', description: 'Measure images'}})]]);
  vi.stubGlobal('sessionStorage', {getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key)});
  const {page, commands, root, wire} = mounted([]);
  commands.session = () => 's1';
  commands.workspace = {create: vi.fn(), reveal: vi.fn(), read: vi.fn(async () => ({id: 'workspace-1', folder: '/private/workspace-1', instructions: 'Use the canonical skill'} as CapabilityWorkspace))};
  page.show('add-add'); page.update(wire); await settleMicrotasks(); await settleMicrotasks();
  expect(commands.workspace.read).toHaveBeenCalledWith('workspace-1', 's1');
  expect(root.textContent).toContain('Continue in your coding tool');
  expect(JSON.parse(values.get('capability-flow')!).workspace).toBe('workspace-1');
  expect(commands.workspace.create).not.toHaveBeenCalled();
  page.show('add-add'); // The workspace dispatches the initial route again after mounting.
  expect(JSON.parse(values.get('capability-flow')!).workspace).toBe('workspace-1');
  page.show('add-link'); // A different explicit new import still opens a new request.
  expect(root.textContent).toContain('Model or GitHub link');
  expect(JSON.parse(values.get('capability-flow')!).workspace).toBeUndefined();
});

test('a broken tool says Needs attention on its card, never Not set up (L7b, ADR 0019)', () => {
  const { root } = mounted([tool('broken-one', {
    status: 'broken', word: 'Broken', group: 'unavailable',
    face: { state: 'broken', says: 'Its last check failed: it answered nothing', action: { kind: 'check', says: 'Check again' }, also: [] },
  })]);
  const cells = root.querySelectorAll('.model-card').map(card => card.querySelector('[data-kind="lit-cell"]')!);
  expect(cells.map(n => n.getAttribute('aria-label'))).toEqual(['Needs attention']);
  expect(cells[0].getAttribute('data-state')).toBe('attention');
});

test('download progress uses measured bytes, raw status stays in Details and Downloaded waits for its check (L7b)', async () => {
  const { root, wire, page } = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  wire.models![0].facts = [{ kind: 'files', ok: true, says: 'Downloaded' }];
  wire.setup!.turning = { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big', state: 'running', attempt: 'a',
    says: 'Downloading 22%', step: { kind: 'download', model: 'big', says: 'Download', by: 'app' } } };
  wire.setup!.downloads = [{ model: 'big', name: 'Big', state: 'downloading', got: 21e6, total: 88e6, says: 'Fetching weights' }];
  page.update(wire);
  let sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('21 MB of 88 MB · 23%');
  expect(sheet.querySelector('.tool-setup-live')!.textContent).not.toContain('Downloading 22%');
  expect(sheet.querySelector('.tool-setup-details')!.textContent).toContain('Downloading 22%');
  expect(sheet.querySelector('.tool-setup-success')).toBeNull();
  wire.setup!.downloads[0].state = 'checking'; page.update(wire);
  sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).toContain('Checking downloaded files');
  expect(sheet.querySelector('.tool-setup-success')).toBeNull();
  wire.setup!.downloads[0].state = 'done';
  wire.setup!.turning.cells.says = 'Checking it on the public synthetic example';
  wire.setup!.turning.cells.step = { kind: 'check', model: 'big', says: 'Check', by: 'app' };
  page.update(wire);
  sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.tool-setup-success')?.textContent).toContain('Downloaded');
  expect(sheet.textContent).toContain('Checking it on the public synthetic example');
});


test('setup keeps live diagnostics in details and closing preserves the exact attempt without restarting it', async () => {
  const values = new Map<string, string>();
  vi.stubGlobal('sessionStorage', { getItem: (key: string) => values.get(key), setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  const plan = sheetPlan(); plan.steps = [{kind:'install',says:'Install again',model:'big',by:'app'}];
  const {root,wire,page,commands}=sheetPage(200e9,plan);
  wire.models![0].installFolder='/private/planned';
  let session='project-one'; commands.session=()=>session;
  fire(root.querySelector('.tool-setup-act')!,'click'); await settleMicrotasks();
  wire.setup!.turning={cells:{job:'cells',option:'big',model:'big',label:'Big',state:'running',attempt:'exact-attempt',says:'Preparing /private/current',step:plan.steps[0]}};
  wire.setup!.installs=[{model:'big',name:'Big',state:'installing',says:'Installing /private/current: checking imports'},
    {model:'other',name:'Other',state:'installing',says:'Unrelated secret path'}];
  page.update(wire);
  let sheet=root.querySelector('.tool-setup-sheet')!;
  const details=sheet.querySelector('.tool-setup-details')!;
  expect(details.textContent).toContain('Installing /private/current: checking imports');
  expect(details.textContent).toContain('not a complete log');
  expect(details.textContent).toContain('/private/planned');
  expect(sheet.querySelector('.tool-setup-live')!.textContent).toContain('Preparing software and checks on this Mac');
  expect(sheet.querySelector('.tool-setup-live')!.textContent).not.toContain('/private');
  expect(sheet.textContent).not.toContain('Install again');expect(sheet.textContent).not.toContain('Unrelated secret path');
  expect(sheet.textContent).toContain('Activity shows its progress under Setting up, and Continue setup on its card reopens this sheet');
  fire(sheet.querySelector('.tool-setup-sheet-cancel')!,'click');
  expect(commands.shelf!.cancel).not.toHaveBeenCalled();expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  expect(JSON.parse(values.get('capability-flow')!).model).toBe('big');
  fire(root.querySelectorAll('button').find(b=>b.textContent==='Continue setup')!,'click');
  sheet=root.querySelector('.tool-setup-sheet')!;
  fire(sheet.querySelector('.tool-stop')!,'click');await settleMicrotasks();
  expect(commands.shelf!.cancel).toHaveBeenCalledWith('cells','exact-attempt');
  expect(commands.shelf!.turnOn).not.toHaveBeenCalled();
  fire(root.querySelector('.tool-setup-sheet')!.querySelector('.tool-setup-sheet-cancel')!,'click');session='project-two';page.update(wire);
  fire(root.querySelectorAll('button').find(b=>b.textContent==='Continue setup')!,'click');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('The session changed');
  expect(root.querySelector('.tool-stop')).toBeNull();
});


test('running setup does not present recomputed automatic requirements as another install', async () => {
  const plan = sheetPlan();
  plan.steps = [{kind:'install',says:'Install reader again',model:'big',by:'app'},
    {kind:'licence',says:'Review the licence',by:'you'}];
  const {root,wire,page}=sheetPage(200e9,plan);
  fire(root.querySelector('.tool-setup-act')!,'click');await settleMicrotasks();
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain('Prepare and turn on');
  // The attempt may omit its current step while observed requirements still include install.
  wire.setup!.turning={cells:{job:'cells',option:'big',model:'big',label:'Big',state:'running',attempt:'reader-check',says:'Checking prepared reader'}};
  page.update(wire);
  const sheet=root.querySelector('.tool-setup-sheet')!;
  expect(sheet.textContent).not.toContain('Install reader again');
  expect(sheet.textContent).toContain('Review the licence');
  expect(sheet.textContent).toContain('Setup reuses prepared software when possible');
});


test('setup with pending workflow observation keeps its required trial instructions', async () => {
  const {root,wire,page}=sheetPage(200e9);
  const why='Open a saved Project input, use the tool controls, Save tool, then reopen before review.';
  wire.models![0].execution={action:'unsupported',why};
  wire.models![0].setups![0].ready=true;
  page.update(wire);
  // Enter through the existing setup flow, with the same exact row/role/session binding.
  vi.stubGlobal('sessionStorage',{getItem:()=>JSON.stringify({route:'find',model:'big',role:'cells',option:'big',session:''}),setItem:()=>{},removeItem:()=>{}});
  page.update(wire);fire(root.querySelectorAll('button').find(b=>b.textContent==='Continue setup')!,'click');
  expect(root.querySelector('.tool-setup-sheet')!.textContent).toContain(why);
  expect(root.querySelector('.tool-setup-sheet')!.textContent).not.toContain('Review the recorded results');
});


// --- Models and Add model or tool (workspace-feel step 8) ----------------------------------------------------------------
function useEvents() {
  const used: unknown[] = [];
  vi.stubGlobal('window', { addEventListener: () => undefined, dispatchEvent: (event: CustomEvent) => { if (event.type === 'hooke-use-model') used.push(event.detail); return true; } });
  return used;
}

test('in the workspace a ready card that serves a task says Use it here and opens its run page; one that serves none says who uses it', () => {
  const used = useEvents();
  const { root, commands, page, wire } = mounted([
    tool('cellpose-sam', { face: { state: 'on', says: 'Its check passed', action: null, also: [] } }),
    tool('gpt-sol', { kind: 'cloud-model', runs: 'cloud', face: { state: 'on', says: 'Your OpenAI key works', action: null, also: [] } }),
    tool('atlas', { kind: 'data', face: { state: 'obtained', says: 'Files checked', action: null, also: [] } }),
  ], {}, { workspace: true });
  commands.served = (model) => model === 'gpt-sol' ? [] : [{ task: 'cells', title: 'Outline every cell' }]; page.update(wire);
  const card = (id: string) => root.querySelector(`[data-model="${id}"]`)!;
  expect(card('cellpose-sam').querySelector('.model-card-action')!.textContent).toBe('Use it here');
  fire(card('cellpose-sam').querySelector('.model-use-here')!, 'click');
  expect(used).toEqual([{ model: 'cellpose-sam' }]);
  for (const id of ['gpt-sol', 'atlas']) { // only where it leads somewhere: no button that would land on a list
    expect(card(id).querySelector('button.model-use-here')).toBeNull();
    expect(card(id).querySelector('.model-card-note')!.textContent).toBe('Used by the Assistant and researchers');
  }
});

test('in the workspace a contribution waiting for its comparison keeps Review comparison on its card, never Use it here', () => {
  useEvents();
  const { root, commands, page, wire } = mounted([tool('pending', { group: 'available', execution: { action: 'compare', why: 'Compare your picture before using this capability.' },
    face: { state: 'unchecked', says: '', action: null, also: [] } })], {}, { workspace: true });
  commands.served = () => [{ task: 'cells', title: 'Outline every cell' }]; page.update(wire);
  const card = root.querySelector('[data-model="pending"]')!;
  expect(card.querySelector('.model-card-action')!.textContent).toBe('Review comparison');
  expect(card.querySelector('.model-use-here')).toBeNull();
});

test('a download fills its card in place from measured bytes, with Cancel; a stopped one says where it stopped and offers Retry', () => {
  const r = tool('micro-sam', { status: 'needs-setup', group: 'available', size: 413e6, next: { kind: 'download', says: 'Download 413 MB', model: 'micro-sam', bytes: 413e6 },
    face: { state: 'needs-setup', says: 'Download 413 MB', action: null, also: [] } });
  const { root, commands, page, wire } = mounted([r]);
  const card = () => root.querySelector('[data-model="micro-sam"]')!;
  fire(card().querySelector('.model-card-download')!, 'click');
  expect(commands.download).toHaveBeenCalledWith('micro-sam');
  r.download = { model: 'micro-sam', name: 'micro-sam', state: 'downloading', got: 173.6e6, total: 413e6, says: 'Downloading' }; page.update(wire);
  expect(card().querySelector('.model-card-progress')!.textContent).toBe('Downloading · 42%');
  expect(card().querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('setup');
  fire(card().querySelector('.model-card-cancel')!, 'click');
  expect(commands.download).toHaveBeenLastCalledWith('micro-sam', true);
  r.download = { ...r.download, state: 'failed', says: 'no connection' }; page.update(wire);
  expect(card().querySelector('.model-card-facts')!.textContent).toBe('Download stopped at 42% · no connection');
  fire(card().querySelectorAll('button').find(b => b.textContent === 'Retry')!, 'click');
  expect(commands.download).toHaveBeenLastCalledWith('micro-sam');
});

test('a card has no step list; its Details offer Try on <specimen> for each task it serves', () => {
  const used = useEvents();
  const { root, commands, page, wire } = mounted([tool('cellpose-sam', { face: { state: 'unchecked', says: 'Not checked', action: { kind: 'check', says: 'Check it here' }, also: [] },
    abilities: [{ id: 'cells', title: 'Outline every cell', what: 'Finds every cell', actor: 'you', say: '', role: 'cells', option: 'cellpose-sam', current: true },
      { id: 'compare', title: 'Compare named models', what: 'Side by side', actor: 'agent', say: '', role: '', option: '', current: false }] })], {}, { workspace: true });
  Object.assign(commands, { served: () => [{ task: 'cells', title: 'Outline every cell' }], specimen: () => 'Human cells' }); page.update(wire);
  page.show('cellpose-sam');
  const tries = root.querySelector('.tool-details')!.querySelectorAll('.tool-task-try');
  expect(tries.map(b => b.textContent)).toEqual(['Try it on Human cells']); // only the task it serves here
  // Ready, not checked yet is said as what it means: a run that works here makes it working here; its check is Hooke's own.
  expect(root.querySelector('.tool-details')!.querySelector('.tool-check-note')!.textContent)
    .toBe('Ready, not checked yet: it can run here, and no run of it on this Mac is recorded yet. Once a run of it works here it shows as working here; Check it here in its setup runs Hooke’s own check (one run on the public example).');
  expect(root.querySelector('.tool-tasks')!.textContent).not.toContain('Compare named models'); // the Assistant's, under More
  fire(tries[0], 'click');
  expect(used).toEqual([{ model: 'cellpose-sam', task: 'cells' }]);
});

test('a setup sheet that reaches Ready leads with Try it on <specimen>, which lands on the run page', async () => {
  const used = useEvents();
  const { root, commands, page, wire } = sheetPage(200e9);
  Object.assign(commands, { served: () => [{ task: 'cells', title: 'Outline every cell' }], specimen: () => 'Human cells', session: () => 's' });
  vi.stubGlobal('document', { ...document, body: { dataset: { workspaceStudy: 'true' } } });
  const row = wire.models![0];
  page.update(wire);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  Object.assign(row, { status: 'ready', facts: [{ kind: 'files', ok: true, says: 'Its files are on this Mac' }, { kind: 'check', ok: true, says: 'Its check passed on this Mac' }],
    face: { state: 'on', says: 'Ready', action: null, also: [] },
    newKind: { operation: 'cells', input: 'image.2d.rgb8', output: 'labels.instance.2d', gives: 'Gives outlines (labels.instance.2d)', proved: true, activeVersion: 'big/1' } });
  Object.assign(row.setups![0], { ready: true, recheck: true });
  page.update(wire);
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.tool-setup-done')!.textContent).toContain('Checked on this Mac'); // what is true, in plain words
  expect(sheet.querySelector('.tool-setup-ready')!.textContent).toContain('big is ready');
  expect(sheet.querySelectorAll('.mm-primary').map(b => b.textContent)).toEqual(['Try it on Human cells']);
  expect(sheet.querySelector('.tool-setup-sheet-cancel')!.textContent).toBe('Done');
  // It ends on Try it on and Done, one row: the developer trial blocks stay in Details (More about this model).
  const acts = sheet.querySelector('.tool-setup-sheet-acts')!;
  expect(acts.children.map(b => b.textContent)).toEqual(['Try it on Human cells', 'Done']);
  expect(sheet.querySelector('.tool-kind-trial, .tool-kind-try')).toBeNull();
  expect(sheet.textContent).not.toContain('labels.instance.2d');
  fire(sheet.querySelector('.tool-setup-use')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  expect(used).toEqual([{ model: 'big' }]);
});

test('a setup sheet opened by a run page\'s step ends on Use it here for that task, not the model\'s first', async () => {
  const used: unknown[] = [];
  vi.stubGlobal('window', { addEventListener: () => {},
    dispatchEvent: (event: CustomEvent) => { if (event.type === 'hooke-use-model') used.push(event.detail); return true; } });
  const { root, commands, page, wire } = sheetPage(200e9);
  // big serves Outline every cell first; the person is on Count cells' run page, whose step (Add key) says so
  // (actions-panel.ts doStep → main.ts openModels → openedFrom) just before it opens #models/big/setup.
  Object.assign(commands, { served: () => [{ task: 'cells', title: 'Outline every cell' }, { task: 'count', title: 'Count cells' }],
    specimen: () => 'Human cells', session: () => 's' });
  vi.stubGlobal('document', { ...document, body: { dataset: { workspaceStudy: 'true' } } });
  page.openedFrom('count');
  page.show('big/setup'); await settleMicrotasks();
  page.show('big/setup'); // the workspace dispatches its route again, with no new opener: the same sheet, the same task
  const row = wire.models![0];
  Object.assign(row, { status: 'ready', facts: [{ kind: 'files', ok: true, says: 'Its files are on this Mac' }, { kind: 'check', ok: true, says: 'Its check passed on this Mac' }],
    face: { state: 'on', says: 'Ready', action: null, also: [] },
    newKind: { operation: 'cells', input: 'image.2d.rgb8', output: 'labels.instance.2d', gives: 'Gives outlines (labels.instance.2d)', proved: true, activeVersion: 'big/1' } });
  Object.assign(row.setups![0], { ready: true, recheck: true });
  page.update(wire);
  const go = root.querySelector('.tool-setup-sheet')!.querySelector('.tool-setup-use')!;
  expect(go.textContent).toBe('Try it on Human cells');
  expect(go.getAttribute('title')).toBe('Count cells with big');
  fire(go, 'click');
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  expect(used).toEqual([{ model: 'big', task: 'count' }]); // back to Count cells' run page, with big chosen
  // The same sheet opened from anywhere else (a card, Details, Activity's See progress): no opener, its first task.
  page.hide();
  page.show('big/setup'); page.update(wire);
  const first = root.querySelector('.tool-setup-sheet')!.querySelector('.tool-setup-use')!;
  expect(first.getAttribute('title')).toBe('Outline every cell with big');
  fire(first, 'click');
  expect(used.at(-1)).toEqual({ model: 'big' });
});

test('Tools\' Add model or tool opens the three choices every time it is pressed; the route dispatched twice at mount opens them once', () => {
  const { root, page } = mounted([]);
  const view = () => root.querySelector('.tool-setup-sheet')?.querySelector('[data-view]')?.dataset.view;
  page.show('add'); // #toolbox/add: the three ways in
  expect(view()).toBe('choose');
  fire(root.querySelectorAll('.capability-way')[1], 'click'); // I have a link or file
  expect(view()).toBe('add');
  page.show('add'); // the workspace dispatches the initial route again after mounting: absorbed, the chosen way stays
  expect(view()).toBe('add');
  fire(root.querySelector('.tool-setup-sheet-x')!, 'click');
  expect(root.querySelector('.tool-setup-sheet')).toBeNull();
  page.hide(); // closing it returns to the Project (#), which hides Models
  page.show('add'); // pressed again: the three choices, never the Models page alone
  expect(view()).toBe('choose');
  expect(root.querySelectorAll('.capability-way').map(b => b.getAttribute('aria-label'))).toEqual(['Find it in the catalog', 'I have a link or file', 'Have AI build it']);
});

test('Models\' sections and counts are Tools By model\'s (run-spec.ts modelSection), Not checked apart from Ready', () => {
  const rows = [tool('a', { face: { state: 'on', says: '', action: null, also: [] } }), tool('b', { face: { state: 'unchecked', says: '', action: null, also: [] } }),
    tool('c', { face: { state: 'unchecked', says: '', action: null, also: [] } }), tool('d', { group: 'needs-you', status: 'needs-setup' }),
    tool('e', { status: 'needs-setup', face: { state: 'needs-setup', says: '', action: null, also: [] } })];
  const { root } = mounted(rows);
  const counts = Object.fromEntries(MODEL_SECTIONS.map(([s]) => [s, rows.filter(r => modelSection(r) === s).length]));
  for (const section of root.querySelectorAll('.models-section'))
    expect(section.querySelectorAll('.model-card').length).toBe(counts[section.dataset.section!]);
  expect(root.querySelectorAll('.models-section').map(s => s.dataset.section)).toEqual(['needs', 'ready', 'unchecked', 'setup']);
  expect(root.querySelectorAll('.models-section').map(s => s.querySelector('.models-group-count')!.textContent)).toEqual(['1', '1', '2', '1']);
});

test('the setup sheet says what it takes before anything runs, with no capitalised eyebrow, and Cancel last', async () => {
  const { root } = sheetPage(200e9);
  fire(root.querySelector('.tool-setup-act')!, 'click'); await settleMicrotasks();
  const sheet = root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('.tool-setup-eyebrow')).toBeNull();
  expect(sheet.querySelector('.tool-setup-facts')!.querySelectorAll('li').map(li => li.dataset.fact)).toEqual(['size', 'space', 'where', 'cost']);
  // Its licence is a title and one quiet line after them, never a fact joined into a line.
  const licence = sheet.querySelector('.tool-setup-licence')!;
  expect(licence.dataset.fact).toBe('licence');
  expect(licence.querySelector('h4')!.textContent).toBe('Licence');
  expect(licence.querySelector('.tool-setup-quiet')!.textContent).not.toMatch(/^Licence /);
  const acts = sheet.querySelector('.tool-setup-sheet-acts')!;
  expect(acts.children.at(-1)!.textContent).toBe('Cancel'); // nothing has started yet
  expect(sheet.querySelector('.tool-setup-sheet-x')!.getAttribute('aria-label')).toBe('Close');
});

test('a setup that is only its check is said as the check: its title, one line, Run the check, its tasks by name', async () => {
  const check = tool('cellpose-sam', { name: 'Cellpose-SAM', face: { state: 'unchecked', says: 'Not checked on this Mac', action: { kind: 'check', says: 'Check it here' }, also: [] },
    usedFor: [{ role: 'cells', title: 'Cells', option: 'cellpose-sam', options: [], current: true, fixed: false }],
    facts: [{ kind: 'files', ok: true, says: 'Its files are on this Mac', ref: '/models/cellpose-sam' }],
    setups: [{ role: 'cells', title: 'Cells', option: 'cellpose-sam', recheck: false, canTry: false,
      plan: { option: 'cellpose-sam', model: 'cellpose-sam', label: 'Cellpose-SAM', recipe: 'r', steps: [{ kind: 'check', says: 'Run its functional check', by: 'app', model: 'cellpose-sam' }] } }] });
  const m = mounted([check], { setup: { edition: 'research', options: {}, keys: sheetKeys, disk: { freeBytes: 200e9, spareBytes: 0 } } }, { workspace: true });
  m.commands.served = () => [{ task: 'action:cells', title: 'Outline every cell' }, { task: 'action:count', title: 'Count cells' }];
  m.page.update(m.wire);
  // In the workspace its card's Check it here opens its check (trying it on a task would not record the check).
  const card = m.root.querySelectorAll('.tool-setup-act').find(b => b.textContent === 'Check it here')!;
  fire(card, 'click'); await settleMicrotasks();
  const sheet = m.root.querySelector('.tool-setup-sheet')!;
  expect(sheet.querySelector('h3')!.textContent).toBe('Check Cellpose-SAM on this Mac');
  const steps = sheet.querySelector('.tool-setup-steps')!;
  expect(steps.querySelector('h4')!.textContent).toBe('The check');
  expect(steps.textContent).toContain('Its files are here; the check runs it on the public example to prove it works on this Mac');
  expect(sheet.querySelectorAll('.mm-primary').map(b => b.textContent)).toEqual(['Run the check']);
  expect(sheet.textContent).not.toContain('Nothing to download.');
  expect(sheet.querySelector('.tool-setup-placement')!.textContent).toBe('Tasks: Outline every cell, Count cells');
  expect(sheet.textContent).not.toContain('Available in');
  // Its check passed: still the check's title at Ready, never "Set up Cellpose-SAM" under the person who asked to check.
  Object.assign(m.wire.models![0].setups![0], { ready: true });
  m.page.update(m.wire);
  expect(m.root.querySelector('.tool-setup-sheet')!.querySelector('h3')!.textContent).toBe('Check Cellpose-SAM on this Mac');
});

// Ticket 67, fix 8: comparing on your picture showed a raw "Choose File / No file chosen", and its "Compare on my picture"
// overlapped the picture's thumbnail.
test('choosing a comparison picture is a studio control that names the file chosen', () => {
  const { root, page, wire } = mounted([], { setup: { edition: 'open', options: {}, drafts: [] } });
  page.update(wire);
  const input = root.querySelector('.own-picture')!;
  const picker = input.closest('.file-picker')!;
  expect(picker.tagName).toBe('LABEL');
  expect(input.classList.contains('visually-hidden')).toBe(true);
  expect(picker.querySelector('.file-picker-button')?.textContent).toBe('Choose a picture');
  expect(picker.querySelector('.file-picker-name')?.textContent).toBe('No picture chosen');
  Object.assign(input, { files: [{ name: 'cells.png' }] }); fire(input, 'change');
  expect(picker.querySelector('.file-picker-name')?.textContent).toBe('cells.png');
});

test('a comparison row lays its picture and its buttons out apart', async () => {
  const { root } = mounted([], { setup: { edition: 'open', options: {}, ownTrials: [{ id: 'trial', model: 'mine/tool', job: 'find', name: 'My tool' }] } });
  const row = root.querySelector('[data-own-trial]')!;
  expect(row.classList.contains('own-trial')).toBe(true);
  const { readFileSync } = await import('node:fs');
  const css = readFileSync(new URL('./modelspage.css', import.meta.url), 'utf8');
  const rule = /\.own-trial\s*\{([^}]*)\}/.exec(css)?.[1] ?? '';
  expect(rule).toMatch(/display:\s*grid/); expect(rule).toMatch(/gap:/);
  expect(/\.own-input-preview\s*\{[^}]*display:\s*block/.test(css)).toBe(true);
});

// The thesis check on 0.5.2 (Part 1: "see its maintenance state: version, last check, an offered update").
describe('a brought-in tool’s maintenance path on Models', () => {
  const maintained = (over: Record<string, unknown>) => ({ state: 'none', says: '', next: null,
    policy: { policy: 'review', words: 'Review updates', effective: 'review' }, check: { state: 'ok', at: '2026-10-28T02:57:00' },
    active: { generation: 'g1', version: '1', installedAt: '2026-10-28T02:56:00' }, candidate: null, held: [], ...over });

  test('Advanced contribution shows where entries come from, with Add channel', async () => {
    const { root, page, wire, commands } = mounted([tool('a')], { setup: { edition: 'open', options: {}, channels: [] } });
    commands.catalogUpdate = vi.fn(async () => undefined); commands.channelAdd = vi.fn(async () => ({ says: 'Added' }));
    page.update(wire);
    // From the community is one of the page body's own sections, and holds the Add channel form.
    const body = root.querySelector('.models-page-body')!;
    const community = body.children.find(c => c.classList.contains('toolbox-community'))!;
    expect(community.tagName).toBe('SECTION');
    expect(community.querySelector('[data-model-editor="channel-add"]')).not.toBeNull();
    // Advanced contribution (category `own`) hides every other body section but its own: never this one.
    const { readFileSync } = await import('node:fs');
    const css = readFileSync(new URL('./workspace-study-v2.css', import.meta.url), 'utf8');
    const own = /\.study-overlay\[data-catalog-category='own'\] \.models-page-body>section:not\(([^)]*)\)\{display:none\}/.exec(css);
    expect(own?.[1].split(',')).toContain('.toolbox-community');
  });

  test('an update maintenance prepared is not offered as Go back, and after Apply Go back names the version it replaced', () => {
    // The channel's pin stays at 1 (maintenance never moves it), so the server lists 1 first, then 2.
    const r = tool('lab/spot-shape', { versions: [
      { generation: 'g1', version: '1', at: '2026-10-28T02:56:00', inUse: true, chosen: true },
      { generation: 'g2', version: '2', at: '2026-10-28T02:57:00', inUse: false, chosen: false }] });
    const { root, page, wire, commands } = mounted([r], { setup: { edition: 'open', options: {}, maintenance: {
      'lab/spot-shape': maintained({ state: 'ready-to-apply', says: 'Ready to apply: 2', next: 'apply',
        candidate: { generation: 'g2', version: '2' } }) } as never } });
    commands.setVersion = vi.fn(async () => ({}));
    page.update(wire); page.show(r.id);
    const buttons = () => root.querySelectorAll('button').map(b => b.textContent);
    expect(buttons()).not.toContain('Go back to 2'); // 2 was never used
    expect(buttons()).not.toContain('Use the newest'); // its Apply, with its first check, is in Updates
    // Applied: 2 runs, 1 is kept to go back to; the catalog's pin still lists 1 first.
    r.versions = [{ generation: 'g1', version: '1', at: '2026-10-28T02:56:00', inUse: false, chosen: false },
      { generation: 'g2', version: '2', at: '2026-10-28T02:57:00', inUse: true, chosen: true }];
    wire.setup!.maintenance = { 'lab/spot-shape': maintained({ state: 'applied', says: 'Updated to 2 (Oct 28, 2:57 am)', next: 'undo',
      active: { generation: 'g2', version: '2', installedAt: '2026-10-28T02:57:00' } }) } as never;
    page.update(wire);
    expect(buttons()).toContain('Go back to 1');
    expect(buttons()).not.toContain('Use the newest'); // it named 1, the older one
    fire(root.querySelectorAll('button').find(b => b.textContent === 'Go back to 1')!, 'click');
    expect(commands.setVersion).toHaveBeenCalledWith('lab/spot-shape', 'g1');
  });

  test('a check’s time is said as people read it, never ISO', () => {
    const r = tool('cells', { setups: [{ role: 'cells', title: 'Cells', option: 'a', plan: null, recheck: true, canTry: false,
      check: { state: 'works', why: '', at: '2026-10-28T02:46:10', ms: 1700, detail: 'made a table of 3 rows' } }] });
    const { root, page } = mounted([r]);
    page.show('cells');
    const line = root.querySelector('.tool-details')!.querySelector('.tool-check')!.textContent;
    expect(line).toContain('works · Oct 28, 2:46 am · 1700 ms');
    expect(line).not.toContain('2026-10-28T');
  });

  test('a shared file’s row says it doesn’t update itself, never to import a newer one', () => {
    const { root, page, wire, commands } = mounted([], { setup: { edition: 'open', options: {}, channels: [
      { name: 'shared-1a2b', url: '', commit: 'c'.repeat(64), reviewed: false, trusted: true, checked: '', entries: 1,
        label: 'Agent check’s shared file', source: 'shared-file' }] } });
    commands.catalogUpdate = vi.fn(async () => undefined);
    page.update(wire);
    const row = root.querySelector('[data-channel="shared-1a2b"]')!.textContent;
    expect(row).toContain('It doesn’t update itself: a newer shared file is added as its own entry.');
    expect(row).not.toContain('To update');
  });

  test('Add your own names this instance’s own folder', () => {
    const { root } = mounted([], { setup: { edition: 'open', options: {}, mineFolder: '~/MicroscopeMind-Beta/catalog/mine/models' } });
    expect(root.querySelector('.models-own-where')!.textContent).toContain('Yours live in ~/MicroscopeMind-Beta/catalog/mine/models/<name>/');
  });
});
