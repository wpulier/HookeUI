import {afterEach, expect, test, vi} from 'vitest';
import {InvestigationLauncher, type LauncherActions} from './investigation-launcher';
import {LIGHT_MY_CELLS_URL, type ResearchStarter} from './research-starter';
import {syntheticDOM} from './workspace-windows.test-support';

const brief: ResearchStarter = {
  source: LIGHT_MY_CELLS_URL, title: 'Light My Cells 2', question: 'Predict fluorescence?',
  original: 'Published task.', context: 'Inspect a pair.', status: 'featured',
};
function setup(overrides: Partial<LauncherActions> = {}) {
  const dom = syntheticDOM();
  const make = dom.document.createElement;
  dom.document.createElement = tag => {
    const node = make(tag);
    return Object.assign(node, {
      open: false, focus: vi.fn(),
      showModal() { this.open = true; },
      close() { this.open = false; node.dispatchEvent(new Event('close')); },
    });
  };
  vi.stubGlobal('document', dom.document);
  const actions = {microscope:vi.fn().mockResolvedValue({status:"setup"}), start: vi.fn(), openProject: vi.fn(), openRecent: vi.fn(), dropFiles: vi.fn(),
    previewResearch: vi.fn(async () => brief), createResearch: vi.fn(), ...overrides};
  const launcher = new InvestigationLauncher(actions);
  document.body.append(launcher.root);
  return {actions, launcher};
}
afterEach(() => vi.unstubAllGlobals());

test('each unavailable instrument or specimen has only a closable preview', async () => {
  const {actions, launcher} = setup();
  for (const id of ['onion-root', 'rosette', 'own-telescope', 'remote-microscope', 'remote-telescope']) {
    await launcher.activate(id);
    const dialog = launcher.root.querySelector('dialog')!;
    expect(dialog.textContent).toContain('Coming soon');
    expect(dialog.querySelectorAll('button')).toHaveLength(1);
    dialog.close();
  }
  expect(actions.start).not.toHaveBeenCalled();
  expect(actions.previewResearch).not.toHaveBeenCalled();
  expect(actions.createResearch).not.toHaveBeenCalled();
});

test('both challenge entrances review the same source; editing keeps the original, and a double submit creates once', async () => {
  let finish!: () => void;
  const createResearch = vi.fn(() => new Promise<void>(resolve => {finish = resolve;}));
  const {launcher, actions} = setup({createResearch});
  await launcher.activate('light-my-cells-2');
  expect(launcher.researchContext()).toMatchObject({title: brief.title, original: brief.original, status: 'featured'});
  launcher.root.querySelector('dialog')!.close();
  const linkForm = launcher.root.querySelector('form')!;
  linkForm.querySelector('input')!.value = LIGHT_MY_CELLS_URL;
  linkForm.dispatchEvent(new Event('submit', {cancelable: true}));
  await vi.waitFor(() => expect(launcher.researchContext()?.question).toBe(brief.question));
  expect(actions.previewResearch).toHaveBeenCalledTimes(2);
  expect(actions.previewResearch).toHaveBeenLastCalledWith(LIGHT_MY_CELLS_URL, expect.any(AbortSignal));
  const dialog = launcher.root.querySelector('dialog')!;
  expect(dialog.contains(launcher.assistantHost)).toBe(true);
  const form = dialog.querySelector('form')!;
  const context = form.querySelectorAll('textarea')[1];
  context.value = 'My follow-up.';
  form.dispatchEvent(new Event('submit', {cancelable: true}));
  form.dispatchEvent(new Event('submit', {cancelable: true}));
  expect(createResearch).toHaveBeenCalledExactlyOnceWith({...brief, context: 'My follow-up.'});
  finish();
  await vi.waitFor(() => expect(launcher.root.querySelector('dialog')).toBe(null));
  expect(launcher.root.contains(launcher.assistantHost)).toBe(true);
});

test('closing during a slow link preview cannot replace the next instrument preview', async () => {
  let finish!: (result: ResearchStarter) => void;
  const {launcher, actions} = setup({previewResearch: () => new Promise(resolve => {finish = resolve;})});
  const slow = launcher.activate('light-my-cells-2');
  launcher.root.querySelector('dialog')!.close();
  await launcher.activate('rosette');
  finish(brief); await slow;
  expect(launcher.root.querySelector('dialog')!.textContent).toContain('Rosette Nebula');
  expect(launcher.researchContext()).toBe(null);
  expect(actions.createResearch).not.toHaveBeenCalled();
});

test('an unreadable reference can retain the URL alone or a pasted working brief', async () => {
  const blocked: ResearchStarter = {title: 'Paper', source: 'https://sample.test/paper', original: '', context: '', question: '', status: 'unreadable'};
  for (const userText of ['', 'Look at the image pairs.']) {
    const {launcher, actions} = setup({previewResearch: async () => blocked});
    const form = launcher.root.querySelector('form')!;
    form.querySelector('input')!.value = blocked.source;
    form.dispatchEvent(new Event('submit', {cancelable: true}));
    await vi.waitFor(() => expect(launcher.root.textContent).toContain('link alone'));
    const review = launcher.root.querySelector('dialog')!.querySelector('form')!;
    expect(Object.keys(launcher.assistantChoices()).sort()).toEqual(['close-preview', 'create-research']);
    review.querySelectorAll('textarea')[1].value = userText;
    review.dispatchEvent(new Event('submit', {cancelable: true}));
    expect(actions.createResearch).toHaveBeenCalledExactlyOnceWith({...blocked, context: userText});
  }
});


test('the assistant sees a generic page’s excerpt when it has no summary or known question', async () => {
  const source = {source: 'https://example.org/study', title: 'Example study', question: '', context: '',
    original: 'Paired microscopy measurements are described in the source.', status: 'read' as const};
  const {launcher} = setup({previewResearch: async () => source});
  const link = launcher.root.querySelector('form')!;
  link.querySelector('input')!.value = source.source;
  link.dispatchEvent(new Event('submit', {cancelable: true}));
  await vi.waitFor(() => expect(launcher.researchContext()).toMatchObject({source: source.source, original: source.original, status: 'read'}));
  const dialog = launcher.root.querySelector('dialog')!;
  dialog.querySelectorAll('textarea')[1].value = 'Compare two wells.';
  expect(launcher.researchContext()).toMatchObject({original: source.original, context: 'Compare two wells.'});
  dialog.close();
  expect(launcher.researchContext()).toBe(null);
});

test('a local microscope reads its actual connection and requires an explicit Project start', async () => {
  const {launcher,actions} = setup({microscope:async()=>({status:'demo',name:'Micro-Manager',controls:['focus']})});
  await launcher.activate('own-microscope');
  const dialog=launcher.root.querySelector('dialog')!;
  expect(dialog.textContent).toContain('Simulated microscope');
  expect(actions.start).not.toHaveBeenCalled();
  Array.from(dialog.querySelectorAll('button')).find(b=>b.textContent==='Start Project')!.dispatchEvent(new Event('click'));
  expect(actions.start).toHaveBeenCalledWith('microscope');
});
