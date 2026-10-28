import { afterEach, expect, test, vi } from 'vitest';
import { capabilityEntry, reviewFacts, sourceWords, type CapabilityEntryActions, type CapabilityInspection } from './capability-entry';
import type { ModelRow } from './protocol';
import { fire, settleMicrotasks, TestElement } from './workspace-windows.test-support';

function mount(route: 'find' | 'add' | 'create' = 'find', options: Parameters<typeof capabilityEntry>[2] = {}) {
  let focused: TestElement | null = null;
  class Focusable extends TestElement { focus() { focused = this; } }
  vi.stubGlobal('document', { createElement: (tag: string) => new Focusable(tag), get activeElement() { return focused; } });
  const actions: CapabilityEntryActions = { select: vi.fn(async () => undefined), inspect: vi.fn(async (): Promise<CapabilityInspection> => ({ kind: 'unsupported', says: 'This file needs a supported model format.' })), create: vi.fn(async () => undefined), changed: vi.fn() };
  const controller = capabilityEntry(actions, { route }, options);
  const root = controller.element as unknown as TestElement;
  const button = (text: string) => { const found = root.querySelectorAll('button').find(b => b.textContent === text); if (!found) throw new Error(`Missing button ${text}`); return found; };
  const input = (label: string) => { const wrapper = root.querySelectorAll('label').find(l => l.textContent.startsWith(label)); const found = wrapper?.children[0]; if (!found) throw new Error(`Missing field ${label}`); return found; };
  const enter = (label: string, value: string, event = 'input') => { const field = input(label); field.value = value; fire(field, event); return field; };
  const file = (label: string, file: File) => { const field = input(label); Object.assign(field, { files: [file] }); fire(field, 'change'); };
  return { controller, root, actions, button, input, enter, file };
}
function model(over: Partial<ModelRow> = {}): ModelRow {
  return { id: 'measure', name: 'Spot measure', runs: 'mac', where: 'on this Mac', what: 'Measure bright spots', kind: 'capability', version: '1', credit: {}, link: '', licence: 'MIT', tier: 'open', mine: false, group: 'available', inUse: false, chosen: false, facts: [], usedFor: [], size: 0, files: [], folder: '', revision: '', status: 'needs-setup', word: 'Needs setup', next: null, steps: [{ kind: 'install', says: 'Install its isolated environment' }], newKind: { operation: 'measure', input: 'image.2d.rgb8', output: 'table.objects', gives: 'a measurements table', proved: false }, ...over };
}
afterEach(() => vi.unstubAllGlobals());

test('search explains inputs, requirements and execution blocks before opening setup', async () => {
  const { controller, root, actions, enter, button } = mount();
  controller.update([model({ execution: { action: 'choose-comparison-input', why: 'Choose a picture to compare before enabling it.' } }), model({ id: 'blur', name: 'Smooth image', what: 'Remove image noise' })]);
  enter('Search by job', 'spots');
  expect(root.textContent).toContain('Uses a 2D picture. Produces a measurements table.');
  expect(root.textContent).toContain('Install its isolated environment');
  expect(root.textContent).toContain('Choose a picture to compare');
  expect(root.textContent).not.toContain('Smooth image');
  fire(button('Open setup and try'), 'click'); await settleMicrotasks();
  expect(actions.select).toHaveBeenCalledWith('measure'); expect(actions.inspect).not.toHaveBeenCalled();
});

test('catalog refresh preserves the focused description and values without starting work', () => {
  const { controller, actions, input, enter } = mount('create');
  const description = enter('Describe the job', 'Find small cells');
  (description as unknown as HTMLElement).focus(); enter('Existing model', 'https://github.com/example/tool');
  controller.update([model()]); controller.update([model({ word: 'Installing' })]);
  expect(input('Describe the job')).toBe(description); expect(document.activeElement).toBe(description);
  expect(description.value).toBe('Find small cells'); expect(input('Existing model').value).toBe('https://github.com/example/tool');
  expect(actions.create).not.toHaveBeenCalled();
  expect(actions.changed).toHaveBeenLastCalledWith(expect.objectContaining({ description: 'Find small cells', source: 'https://github.com/example/tool' }));
});

test('one file input routes weights and packages to their existing checks without claiming readiness', async () => {
  const { actions, root, file, button } = mount('add');
  const weights = new File(['weights'], 'model.pt'), packageFile = new File(['package'], 'tool.hookeui.zip');
  file('Model or tool package', weights); expect(actions.inspect).not.toHaveBeenCalled();
  fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenLastCalledWith({ kind: 'model-file', file: weights }, expect.any(Function));
  expect(root.textContent).toContain('This file needs a supported model format.'); expect(root.textContent).not.toContain('Added for review');
  fire(button('Change source'), 'click'); file('Model or tool package', packageFile);
  fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenLastCalledWith({ kind: 'capability-package', file: packageFile }, expect.any(Function)); expect(actions.select).not.toHaveBeenCalled();
});

test('duplicate presses are ignored and a failed source can be corrected and retried', async () => {
  const { actions, root, enter, input, button } = mount('add');
  let reject: (reason: Error) => void = () => undefined;
  vi.mocked(actions.inspect).mockImplementationOnce(() => new Promise((_resolve, no) => { reject = no; }));
  enter('Model or GitHub', 'https://github.com/example/tool');
  fire(button('Review'), 'click'); fire(button('Review'), 'click'); expect(actions.inspect).toHaveBeenCalledTimes(1);
  reject(new Error('The link could not be read.')); await settleMicrotasks(); await settleMicrotasks();
  expect(root.textContent).toContain('Correct the problem and try again here.'); expect(input('Model or GitHub').value).toBe('https://github.com/example/tool');
  enter('Model or GitHub', 'https://github.com/example/fixed'); fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenLastCalledWith({ kind: 'link', url: 'https://github.com/example/fixed' }, expect.any(Function));
});

test('an integration carries the source into the coder route without claiming success', async () => {
  const { actions, root, enter, input, button } = mount('add');
  vi.mocked(actions.inspect).mockResolvedValue({ kind: 'integration', source: 'https://github.com/example/tool', says: 'This repository needs an adapter.' });
  enter('Model or GitHub', 'https://github.com/example/tool'); fire(button('Review'), 'click'); await settleMicrotasks();
  expect(root.textContent).toContain('This needs an integration before it can run'); fire(button('Use my own coding tool'), 'click');
  expect(input('Existing model').value).toBe('https://github.com/example/tool');
  enter('Describe the job', 'Measure each object'); enter('Tool runtime', 'fiji', 'change');
  fire(button('Prepare instructions for my coding tool'), 'click'); await settleMicrotasks();
  expect(actions.create).toHaveBeenCalledWith({ method: 'coder', description: 'Measure each object', source: 'https://github.com/example/tool', job: 'find', runner: 'fiji', input: 'image.2d.rgb8', output: 'table.objects' });
  expect(actions.select).not.toHaveBeenCalled();
});

test('AI building requires a press, description and separate PNG or JPEG test picture', async () => {
  const { actions, root, enter, file, button } = mount('create');
  fire(button('Build with AI, up to $2'), 'click'); expect(actions.create).not.toHaveBeenCalled();
  enter('Describe the job', 'Identify cells'); enter('Job', 'name', 'change'); file('Model or tool package', new File(['weights'], 'model.pt'));
  fire(button('Build with AI, up to $2'), 'click'); expect(root.textContent).toContain('Choose a PNG or JPEG test picture'); expect(actions.create).not.toHaveBeenCalled();
  file('Test picture', new File(['bad'], 'example.tif', { type: 'image/tiff' })); fire(button('Build with AI, up to $2'), 'click'); expect(actions.create).not.toHaveBeenCalled();
  const picture = new File(['picture'], 'cells.png', { type: 'image/png' }); file('Test picture', picture); expect(actions.create).not.toHaveBeenCalled();
  fire(button('Build with AI, up to $2'), 'click'); await settleMicrotasks();
  expect(actions.create).toHaveBeenCalledWith({ method: 'builder', description: 'Identify cells', source: '', job: 'name', picture, budgetUsd: 2, minutes: 15 });
});

test('a checked candidate opens setup only on the next explicit press', async () => {
  const { actions, root, enter, button } = mount('add');
  vi.mocked(actions.inspect).mockResolvedValue({ kind: 'candidate', candidate: 'mine/candidate1', name: 'My tool', says: 'The package was read.' });
  enter('Model or GitHub', 'https://example.com/model'); fire(button('Review'), 'click'); await settleMicrotasks();
  // The candidate first: its name (no "Ready for setup" label, no "Added for review" sentence ahead of the work).
  expect(root.querySelector('.capability-inspection')!.querySelector('h3')!.textContent).toBe('My tool');
  expect(root.textContent).not.toContain('Ready for setup'); expect(root.textContent).not.toContain('Added for review');
  expect(actions.select).not.toHaveBeenCalled();
  fire(button('Review setup'), 'click'); await settleMicrotasks(); expect(actions.select).toHaveBeenCalledWith('mine/candidate1');
});

test('closing ignores late replies and drops selected files', async () => {
  const { controller, actions, root, input, file, button } = mount('add');
  let finish: (result: CapabilityInspection) => void = () => undefined;
  vi.mocked(actions.inspect).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  file('Model or tool package', new File(['weights'], 'model.pt')); input('Model or tool package').value = 'fakepath/model.pt';
  fire(button('Review'), 'click'); controller.dispose();
  finish({ kind: 'candidate', candidate: 'mine/new', name: 'Late tool', says: 'Read' }); await settleMicrotasks();
  expect(root.textContent).not.toContain('Late tool'); expect(input('Model or tool package').value).toBe('');
  fire(button('Review'), 'click'); expect(actions.inspect).toHaveBeenCalledTimes(1);
});

test('a Fiji handoff explains unsupported output and accepts a corrected result', async () => {
  const { actions, root, enter, button } = mount('create');
  enter('Describe the job', 'Report objects'); enter('Where to create it', 'coder', 'change');
  enter('Tool runtime', 'fiji', 'change'); enter('Result to produce', 'text', 'change');
  fire(button('Prepare instructions for my coding tool'), 'click');
  expect(actions.create).not.toHaveBeenCalled(); expect(root.textContent).toContain('For a Fiji script, choose a picture');
  enter('Result to produce', 'image.2d.rgb8', 'change'); fire(button('Prepare instructions for my coding tool'), 'click'); await settleMicrotasks();
  expect(actions.create).toHaveBeenCalledWith(expect.objectContaining({ runner: 'fiji', output: 'image.2d.rgb8' }));
});

test('changing readiness keeps keyboard focus on the same result action', () => {
  const { controller, button } = mount(); controller.update([model()]);
  (button('Open setup and try') as unknown as HTMLElement).focus(); controller.update([model({ word: 'Installing' })]);
  expect(document.activeElement).toBe(button('Open setup and try'));
});

test('ambiguous files ask their meaning and a typed link replaces the file without an extra upload', async () => {
  const { actions, root, file, enter, button, input } = mount('add');
  const weights = new File(['weights'], 'custom-model');
  file('Model or tool package', weights);
  fire(button('Review'), 'click'); expect(actions.inspect).not.toHaveBeenCalled();
  expect(root.textContent).toContain('Choose whether this file contains');
  enter('What kind of file', 'model-file', 'change');
  fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenCalledWith({ kind: 'model-file', file: weights }, expect.any(Function));
  fire(button('Change source'), 'click');
  enter('Model or GitHub', 'https://example.com/tool');
  expect(input('Model or GitHub').value).toBe('https://example.com/tool');
  fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenLastCalledWith({ kind: 'link', url: 'https://example.com/tool' }, expect.any(Function));
});

test('upload progress is bounded, retained on failure, and the same selected file can be retried', async () => {
  const { actions, root, file, button } = mount('add');
  const weights = new File(['weights'], 'custom.pt');
  vi.mocked(actions.inspect).mockImplementationOnce(async (_source, progress) => {
    progress?.(0.42); expect(root.textContent).toContain('Uploading your file · 42%');
    progress?.(2); expect(root.textContent).toContain('Uploading your file · 100%');
    throw new Error('Upload interrupted.');
  });
  file('Model or tool package', weights); fire(button('Review'), 'click'); await settleMicrotasks();
  expect(root.textContent).toContain('Upload interrupted.'); expect(root.textContent).toContain('custom.pt');
  expect(root.getAttribute('aria-busy')).toBe('false');
  fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenCalledTimes(2);
  expect(actions.inspect).toHaveBeenLastCalledWith({ kind: 'model-file', file: weights }, expect.any(Function));
});

test('dropping a package is an explicit pending selection and a multi-file drop is refused', async () => {
  const { actions, root, button } = mount('add');
  const drop = root.querySelector('.capability-import-drop')!;
  const file = new File(['zip'], 'tool.hookeui.zip');
  fire(drop, 'drop', { dataTransfer: { files: [file, file] }, stopPropagation() {} });
  expect(root.textContent).toContain('Add one model or tool package'); expect(actions.inspect).not.toHaveBeenCalled();
  fire(drop, 'drop', { dataTransfer: { files: [file] }, stopPropagation() {} });
  expect(root.textContent).toContain(file.name); expect(actions.inspect).not.toHaveBeenCalled();
  fire(button('Review'), 'click'); await settleMicrotasks();
  expect(actions.inspect).toHaveBeenCalledWith({kind: 'capability-package', file}, expect.any(Function));
});

test('a described task finds segmentation by its declared facts, best match first (L7e walk, 2026-10-20)', () => {
  const { controller, root, enter } = mount();
  const cells = { id: 'cells', title: 'Outline every cell', what: 'A cell finder segments every cell; they are kept and measured',
    actor: 'agent', say: 'outline all the cells', role: 'cells', option: 'cellpose-sam', current: false };
  controller.update([model({ id: 'dino', name: 'DINOv2 small', what: 'Turns a picture into numbers that compare how things look' }),
    model({ id: 'cellpose-sam', name: 'Cellpose-SAM', what: 'Outlines every cell in a view', abilities: [cells] }),
    model({ id: 'instanseg', name: 'InstanSeg (brightfield nuclei)', what: 'Outlines every nucleus on a brightfield slide' })]);
  const titles = () => root.querySelectorAll('h4').map(title => title.textContent);
  for (const [query, first] of [['segmentation', 'Cellpose-SAM'], ['outline every cell', 'Cellpose-SAM'], ['count nuclei', 'InstanSeg (brightfield nuclei)'],
    ['find cell boundaries', 'Cellpose-SAM'], ['cellpose', 'Cellpose-SAM']] as const) {
    enter('Search by job', query);
    expect(titles()[0], query).toBe(first);
    expect(titles()).not.toContain('DINOv2 small');
  }
  enter('Search by job', 'quantum chromodynamics');
  expect(root.textContent).toContain('No matching tool.');
});

test('closest guesses say so; a search of punctuation alone keeps every entry', () => {
  const { controller, root, enter } = mount();
  controller.update([model({ id: 'trackastra', name: 'Trackastra', what: 'Links outlined cells across frames into tracks' }),
    model({ id: 'dino', name: 'DINOv2 small', what: 'Turns a picture into numbers that compare how things look' })]);
  const titles = () => root.querySelectorAll('h4').map(title => title.textContent);
  enter('Search by job', 'track cells in zebrafish');
  expect(titles()).toEqual(['Trackastra']);
  expect(root.textContent).toContain('Closest matches: none does all of “track cells in zebrafish”.');
  enter('Search by job', 'track cells');
  expect(root.textContent).not.toContain('Closest matches');
  enter('Search by job', '?!');
  expect(titles()).toEqual(['Trackastra', 'DINOv2 small']);
});

// --- Add model or tool, restyled (workspace-feel step 8; the mock's addSheet) ---------------------------------------------
test('Add model or tool opens on three plain choices, then the chosen way; the older controls are one press away', () => {
  const advanced = vi.fn();
  const { root, actions } = mount('find', { choose: true, advanced });
  const ways = root.querySelectorAll('.capability-way');
  expect(root.dataset.view).toBe('choose');
  expect(ways.map(b => b.getAttribute('aria-label'))).toEqual(['Find it in the catalog', 'I have a link or file', 'Have AI build it']);
  expect(ways.map(b => b.querySelector('.capability-way-sub')!.textContent)).toEqual(['Models and tools others have packaged',
    'Paste a link, or choose a file on this Mac', 'Describe what it should do']);
  expect(ways.map(b => b.getAttribute('aria-pressed'))).toEqual(['false', 'false', 'false']); // nothing chosen yet
  const section = (heading: string) => root.querySelectorAll('h3').find(h => h.textContent === heading)!.parentElement!;
  expect(section('What do you want to do?').hidden).toBe(true); // no route shown before a choice
  expect(section('What should your tool do?').hidden).toBe(true);
  fire(root.querySelectorAll('button').find(b => b.textContent === 'Advanced contribution controls')!, 'click');
  expect(advanced).toHaveBeenCalledTimes(1);
  fire(ways[1], 'click');
  expect(root.dataset.view).toBe('add');
  expect(ways[1].getAttribute('aria-pressed')).toBe('true');
  expect(root.textContent).toContain('Model or GitHub link'); // the field first: no heading or paragraph ahead of it
  expect(root.textContent).not.toContain('Bring a tool into your workspace');
  expect(root.querySelectorAll('button').find(b => b.textContent === 'Advanced contribution controls')!.hidden).toBe(true);
  expect(actions.changed).toHaveBeenLastCalledWith(expect.objectContaining({ route: 'add' }));
});

test('a candidate is reviewed with its real facts before anything runs: size, where pictures go, licence and cost, unknowns said', async () => {
  expect(reviewFacts(model({ size: 413e6, licence: 'MIT' })).map(f => f.words)).toEqual(['413 MB on this Mac', 'Pictures stay on this Mac', 'Licence MIT', 'Free to run on this Mac']);
  expect(reviewFacts(model({ licence: '' })).map(f => f.words)).toEqual(['Its size isn’t known yet', 'Pictures stay on this Mac', 'Its licence isn’t stated in the catalog', 'Free to run on this Mac']);
  expect(reviewFacts(model({ runs: 'cloud', where: 'in the cloud', licence: '', setups: [{ role: 'r', title: 'T', option: 'o', recheck: false, canTry: false,
    plan: { option: 'o', model: 'm', label: 'L', recipe: 'x', privacy: 'Sends pictures to fal (your key)', cost: 'Billed by the provider per call', steps: [] } }] })).map(f => f.words))
    .toEqual(['Nothing to download', 'Sends pictures to fal (your key)', 'Its licence isn’t stated in the catalog', 'Billed by the provider per call']);
  const { controller, actions, root, enter, button } = mount('add');
  vi.mocked(actions.inspect).mockResolvedValue({ kind: 'candidate', candidate: 'measure', name: 'Spot measure', says: 'The package was read.' });
  enter('Model or GitHub', 'https://example.com/model'); fire(button('Review'), 'click'); await settleMicrotasks();
  expect(root.querySelector('.capability-review-facts')!.textContent).toBe('From example.com · modelReading what it takes…'); // its row has not arrived yet
  controller.update([model({ size: 16e6 })]);
  expect(root.querySelector('.capability-review-facts')!.querySelectorAll('li').map(li => li.dataset.fact)).toEqual(['source', 'size', 'where', 'licence', 'cost']);
  expect(root.querySelector('.capability-review-facts')!.textContent).toContain('16 MB on this Mac');
  expect(actions.select).not.toHaveBeenCalled(); // nothing runs before the next press
});

test('Review asks for a link or a file first; the review is the candidate\'s own page, its source a fact', async () => {
  const { controller, actions, root, enter, file, button } = mount('add');
  expect(button('Review').disabled).toBe(true); // nothing to review yet
  enter('Model or GitHub', 'https://bioimage.io/#/?id=diplomatic-bug'); expect(button('Review').disabled).toBe(false);
  enter('Model or GitHub', '   '); expect(button('Review').disabled).toBe(true);
  file('Model or tool package', new File(['weights'], 'model.pt')); expect(button('Review').disabled).toBe(false);
  file('Model or tool package', null as unknown as File); expect(button('Review').disabled).toBe(true);
  vi.mocked(actions.inspect).mockResolvedValue({ kind: 'candidate', candidate: 'measure', name: 'micro-sam (light microscopy)',
    says: 'Found a bioimage.io model the reviewed runner takes: diplomatic-bug.' });
  enter('Model or GitHub', 'https://bioimage.io/#/?id=diplomatic-bug'); fire(button('Review'), 'click'); await settleMicrotasks();
  expect(root.dataset.view).toBe('review');
  expect(root.querySelector('nav')!.hidden).toBe(true); // Change source is the way back, not the method tabs
  const review = root.querySelector('.capability-inspection')!;
  expect(review.children[0].tagName).toBe('H3');
  expect(review.textContent).not.toContain('reviewed runner'); // the importer's words are not what it does
  controller.update([model({ what: 'Outlines cells, trained on light microscopy' })]);
  expect(review.querySelector('.capability-review-what')!.textContent).toBe('Outlines cells, trained on light microscopy');
  expect(review.querySelector('[data-fact="source"]')!.textContent).toBe('From bioimage.io · diplomatic-bug');
  fire(button('Change source'), 'click');
  expect(root.querySelector('nav')!.hidden).toBe(false);
  expect(sourceWords({ kind: 'link', url: 'https://github.com/lab/tool' })).toBe('From github.com · lab/tool');
  expect(sourceWords({ kind: 'file', name: 'tool.hookeui.zip' })).toBe('From your file · tool.hookeui.zip');
});
