// The run page's states (actions-panel.ts, the mock's runPage() and runFoot()), drawn from the real wire's shapes
// (run-spec.fixture.json): ready, setting up in place (a download filling from measured bytes, a step with no button),
// running (its clock, its latest Activity line, Stop → Stop requested), done (the outcome in the server's words, saved
// where), failed (plain words, Choose another model, Details), a task switch, a click-driven task, a catalog kind not
// set up yet, and Use it here. Synthetic DOM; no model runs.
import { afterEach, expect, test, vi } from 'vitest';
import type { ActionsDeps, ToolList } from './actions-panel';
import type { CapabilitiesWire } from './protocol';
import fixture from './run-spec.fixture.json';

afterEach(() => vi.unstubAllGlobals());

type Node = { textContent: string; dataset: Record<string, string>; disabled?: boolean; title?: string; hidden?: boolean;
  querySelector(s: string): Node | null; querySelectorAll(s: string): Node[]; getAttribute(name: string): string | null };

async function page(over: Partial<ActionsDeps> = {}, edit: (wire: CapabilitiesWire, tools: ToolList) => void = () => {}) {
  const { ActionsPanel } = await import('./actions-panel');
  const support = await import('./workspace-windows.test-support');
  const dom = support.syntheticDOM(), create = dom.document.createElement;
  dom.document.createElement = (tag: string) => Object.assign(create(tag), { focus: vi.fn(), scrollIntoView: vi.fn() });
  dom.host.dataset.workspaceStudy = 'true'; dom.host.dataset.activeWorkspaceView = 'main';
  Object.assign(dom.document, { querySelector: (s: string) => (s === '.workspace-view-tabs [aria-selected=true]' ? { textContent: 'Main' } : null) });
  vi.stubGlobal('document', dom.document);
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
  const base = structuredClone(fixture) as unknown as CapabilitiesWire & { tools: ToolList };
  const tools = base.tools; delete (base as { tools?: ToolList }).tools;
  edit(base, tools);
  const root = dom.document.createElement('section');
  const deps: ActionsDeps = { run: vi.fn(async () => ({ ok: true })), stop: vi.fn(async () => ({ ok: true })), selected: () => null, session: () => 's1',
    openModels: vi.fn(), openActivity: vi.fn(), openTable: vi.fn(), openCamera: () => {}, cameraPage: () => '/camera/', ...over };
  const panel = new ActionsPanel(root as unknown as HTMLElement, deps);
  panel.setTools(tools); panel.setCapabilities(base);
  panel.setInput('Human cells · David Root (CC0)');
  const r = root as unknown as Node;
  const at = (task: string) => r.querySelector(`[data-action="${task}"]`)!;
  const pageOf = (task: string) => at(task).querySelector('.run')!;
  return { panel, root: r, deps, wire: base, dom, fire: support.fire, settle: support.settleMicrotasks, at, pageOf,
    pick: (task: string, option: string) => {
      support.fire(pageOf(task).querySelector('.run-with')!.querySelector('.run-change') as never, 'click');
      support.fire(pageOf(task).querySelectorAll('.mrow').find((m) => m.dataset.option === option) as never, 'click');
    } };
}

test('ready: the mock’s order, the declared fields only, one primary naming the model on one line', async () => {
  const { panel, pageOf, pick } = await page();
  panel.open('cells');
  const p = pageOf('cells');
  expect(p.dataset.phase).toBe('ready');
  expect(p.querySelector('.run-what')!.textContent).toBe('Outlines every cell in a view');
  expect(p.querySelectorAll('.run-key').map((k) => k.textContent).filter(Boolean)).toEqual(['On', 'With', 'Makes']);
  expect(p.querySelector('.run-hline')!.textContent).toBe('Settingsnone declared for Cellpose-SAM');
  expect(p.querySelector('.run-dest')!.textContent).toContain('This Tab');
  const go = p.querySelector('.run-foot')!.querySelector('.action-go')!;
  expect(go.textContent).toBe('Run Cellpose-SAM');
  pick('cells', 'basic-bright-objects');
  const again = pageOf('cells');
  expect(again.querySelector('.run-fields')!.querySelectorAll('.run-field').map((f) => f.querySelector('label')!.textContent)).toEqual(['Smoothing', 'Smallest object']);
  // The run label is the model's one name (catalog S10: "Bright objects", no second name), and its tooltip.
  const run = again.querySelector('.run-foot')!.querySelector('.action-go')!;
  expect(run.textContent).toBe('Run Bright objects');
  expect(run.title).toBe('Run Bright objects');
});

test('setting up in place: the step is the primary; a download fills from the measured bytes, with Cancel', async () => {
  const download = vi.fn(async () => ({ ok: true }));
  // A download-only model (InstanSeg with its install step taken off): its setup is the download, filled in place.
  const { panel, pageOf, pick, fire, settle, wire } = await page({ download }, (wire) => {
    const entry = wire.models!.find((m) => m.id === 'instanseg-brightfield')!;
    entry.steps = entry.steps.filter((s) => s.kind !== 'install');
  });
  panel.open('cells');
  pick('cells', 'instanseg-brightfield');
  let p = pageOf('cells');
  expect(p.dataset.phase).toBe('setup');
  const step = p.querySelector('.run-foot')!.querySelector('.action-step')!;
  expect(step.textContent).toBe('Download 16 MB');
  fire(step as never, 'click');
  await settle();
  expect(download).toHaveBeenCalledWith('instanseg-brightfield');
  // The server's download row comes on the capabilities message: the primary is its progress, never a made-up percent.
  const entry = wire.models!.find((m) => m.id === 'instanseg-brightfield')!;
  entry.download = { model: entry.id, name: entry.name, state: 'downloading', got: 6_000_000, total: 15_792_317, says: 'Downloading' };
  panel.setCapabilities(structuredClone(wire));
  p = pageOf('cells');
  expect(p.dataset.phase).toBe('downloading');
  const bar = p.querySelector('.run-foot')!.querySelector('.dl')!;
  expect(bar.getAttribute('role')).toBe('progressbar');
  // Its label twice (the text colour over the track, a white copy clipped to the fill), read once.
  expect(bar.querySelectorAll('.dl-words').map((w) => [w.textContent, w.getAttribute('aria-hidden')])).toEqual([
    ['Downloading · 6 of 16 MB', null], ['Downloading · 6 of 16 MB', 'true']]);
  expect(bar.getAttribute('aria-label')).toBe('InstanSeg (brightfield nuclei): Downloading · 6 of 16 MB');
  expect(bar.getAttribute('aria-valuenow')).toBe('6000000');
  fire(p.querySelector('.run-foot')!.querySelectorAll('button').find((b) => b.textContent === 'Cancel') as never, 'click');
  expect(download).toHaveBeenLastCalledWith('instanseg-brightfield', true);
});

test('a step nobody can press here says its reason, with no dead-end button; a key opens the setup sheet', async () => {
  // The fine-tuned Cellpose-SAM as the cells' default: its weights are made here from corrections, by no button.
  const { panel, pageOf, fire, deps } = await page({}, (wire) => { wire.roles.find((r) => r.id === 'cells')!.current = 'cellpose-sam-tuned'; });
  panel.open('cells');
  fire(pageOf('cells').querySelector('.run-with')!.querySelector('.run-change') as never, 'click');
  const tuned = pageOf('cells').querySelectorAll('.mrow').find((m) => m.dataset.option === 'cellpose-sam-tuned')!;
  expect(tuned.getAttribute('aria-disabled')).toBe('true'); // quiet in the list: nothing to pick it for
  expect(tuned.querySelector('.mrow-why')!.textContent).toBe('Made from your corrections');
  const foot = pageOf('cells').querySelector('.run-foot')!;
  expect(foot.querySelector('button')).toBeNull();
  expect(foot.textContent).toBe('Made from your corrections');
  panel.open('count');
  const key = pageOf('count').querySelector('.run-foot')!.querySelector('.action-step')!;
  expect(key.textContent).toBe('Add key');
  fire(key as never, 'click');
  expect(deps.openModels).toHaveBeenLastCalledWith('gemini-3.8-flash/setup', { task: 'count' }); // its sheet comes back to Count
});

test('running: its lit cell, Running on its Tab, its clock, its latest Activity line; Stop → Stop requested → Stopped', async () => {
  let answer: (reply: { ok: boolean; stopping?: boolean; stopped?: boolean }) => void = () => {};
  const run = vi.fn(() => new Promise<{ ok: boolean }>((done) => { answer = done; }));
  const stop = vi.fn(async () => { answer({ ok: true, stopping: true }); return { ok: true }; });
  const { panel, pageOf, pick, fire, settle, dom } = await page({ run, stop });
  panel.open('cells');
  pick('cells', 'basic-bright-objects');
  const running = panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  let p = pageOf('cells');
  expect(p.dataset.phase).toBe('running');
  // Left at its defaults it runs as the task's model for this run: one result, as before.
  expect(run).toHaveBeenCalledWith('cells', {}, { cells: 'basic-bright-objects' }, expect.any(String));
  const status = p.querySelector('.run-status')!;
  expect(status.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('working');
  expect(status.textContent).toMatch(/^Running Bright objects on Main0:0\d$/);
  expect(dom.host.dataset.runVeil).toBe('true'); // the origin Tab's specimen gets its faint veil
  // Fields and Change wait; changes apply to the next run.
  expect(p.querySelectorAll('.run-field').flatMap((f) => f.querySelectorAll('input')).every((i) => i.disabled)).toBe(true);
  expect(p.querySelector('.run-with')!.querySelector('.run-change')!.disabled).toBe(true);
  expect(p.textContent).toContain('Changes apply to the next run');
  // Another model's load (a researcher's, another run's) is never said under this run; its own model's is.
  panel.activity({ type: 'activity', id: 'l0', kind: 'model', text: 'Loading micro-sam (light microscopy) on this Mac', at: Date.now() / 1000, state: 'running',
    ref: { model: 'micro-sam (light microscopy) on this Mac' } });
  expect(p.querySelector('.run-note')!.textContent).toBe('');
  panel.activity({ type: 'activity', id: 'l1', kind: 'model', text: 'Loading Bright objects', at: Date.now() / 1000, state: 'running',
    ref: { model: 'Bright objects' } });
  expect(p.querySelector('.run-note')!.textContent).toBe('Loading Bright objects');
  fire(p.querySelector('.run-foot')!.querySelector('.action-stop') as never, 'click');
  await settle(); await running;
  p = pageOf('cells');
  expect(p.dataset.phase).toBe('stopping');
  expect(p.querySelector('.run-status')!.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('still');
  expect(p.querySelector('.run-status')!.querySelector('b')!.textContent).toBe('Stop requested');
  const runs = (run.mock.calls as unknown as [string, unknown, unknown, string][]);
  panel.settled(runs[0][3], { outcome: 'stopped', text: 'Stopped' });
  p = pageOf('cells');
  expect(p.dataset.phase).toBe('stopped');
  expect(p.querySelector('.run-status')!.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('stopped');
  expect(dom.host.dataset.runVeil).toBeUndefined();
});

test('running: its model’s load line is said in one line at once, and in the same words after a redraw', async () => {
  // The server's line puts where it runs and how long it usually takes in two pairs of parentheses (activity.py
  // model_loading); the footer says it once, as `loadLine` does, from the first line on (never re-worded mid-run).
  let answer: (reply: { ok: boolean }) => void = () => {};
  const run = vi.fn(() => new Promise<{ ok: boolean }>((done) => { answer = done; }));
  const { panel, pageOf } = await page({ run });
  panel.open('cells');
  const running = panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  const live = () => pageOf('cells').querySelector('.run-foot')!.querySelector('.run-note')!;
  expect(live().hidden).toBe(true); // no line yet
  const drawn = live();
  panel.activity({ type: 'activity', id: 'l2', kind: 'model', text: 'Loading Cellpose-SAM (on this Mac) (usually about 3.5 s)', at: Date.now() / 1000,
    state: 'running', ref: { model: 'Cellpose-SAM (on this Mac)' } });
  expect(drawn.textContent).toBe('Loading Cellpose-SAM (on this Mac) · usually about 3.5 s'); // rewritten in place
  expect(drawn.hidden).toBe(false);
  panel.selectionChanged(); // a redraw
  expect(live().textContent).toBe('Loading Cellpose-SAM (on this Mac) · usually about 3.5 s');
  // A line of the run itself that is not a model load is said as it came.
  const runId = (run.mock.calls as unknown as [string, unknown, unknown, string][])[0][3];
  panel.activity({ type: 'activity', id: 'l3', kind: 'command', text: 'Outlining cells', at: Date.now() / 1000, state: 'running', details: { ids: { run: runId } } });
  expect(live().textContent).toBe('Outlining cells');
  answer({ ok: true }); await running;
});

test('a changed setting of a format-2 entry runs its own capability, with that setting', async () => {
  const run = vi.fn(async () => ({ ok: true }));
  const { panel, pageOf, pick, fire } = await page({ run });
  panel.open('cells');
  pick('cells', 'basic-bright-objects');
  const sigma = pageOf('cells').querySelector('.run-fields')!.querySelectorAll('input').find((i) => i.getAttribute('type') === 'number')!;
  (sigma as unknown as { value: string }).value = '2';
  fire(sigma as never, 'input');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  expect(run).toHaveBeenCalledWith('catalog:basic-bright-objects', { sigma: 2 }, {}, expect.any(String));
});

test('done: what the model found in the server’s words, how long it took, where it was saved, Open in Data and Run again', async () => {
  const said = 'Outlined and measured 271 bright regions with Bright objects · Basic local method. No scale: sizes are in pixels of the original image.';
  const { panel, pageOf, pick, fire, deps } = await page({ run: vi.fn(async () => ({ ok: true, said })) });
  panel.open('cells');
  pick('cells', 'basic-bright-objects');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  const p = pageOf('cells');
  expect(p.dataset.phase).toBe('done');
  const foot = p.querySelector('.run-foot')!;
  expect(foot.querySelector('.action-result-text')!.textContent).toBe('Bright objects found 271 bright regions');
  // What its model says of what it makes follows it here too (its output's note): never a cell count.
  expect(foot.querySelectorAll('.run-note').map((n) => n.textContent)).toEqual([expect.stringMatching(/^\d+\.\d s, saved on Main and in Data$/),
    'No scale: sizes are in pixels of the original image.', 'Bright regions are not a cell count']);
  // No table to open: Details (Activity: what ran, with which model) on the same one row as Open in Data and Run again.
  expect(foot.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Open in Data', 'Run again', 'Details']);
  const rows = foot.querySelectorAll('.run-foot-row').filter((r) => r.querySelectorAll('button').length);
  expect(rows.map((r) => r.querySelectorAll('button').map((b) => b.textContent))).toEqual([['Open in Data', 'Run again', 'Details']]);
  const details = foot.querySelector('.run-done-details')!;
  expect((details as unknown as { className: string }).className.split(' ')).toContain('btn-quiet'); // quiet, as Failed's
  fire(details as never, 'click');
  expect(deps.openActivity).toHaveBeenCalledTimes(1);
  fire(foot.querySelector('.action-again') as never, 'click');
  expect(pageOf('cells').dataset.phase).toBe('ready');
});

test('done on the Stage’s still: never said as on a Tab while the server keeps its window for the specimen; drawn, said so', async () => {
  // S5a: a This Tab run on the still the Stage shows places no window once its outlines are checked there (the server's
  // `placed`): until the studio draws them it is saved in Data only, never "saved on Main".
  const said = 'Outlined and measured 3 cells with Cellpose-SAM. No scale: sizes are in pixels of the original image.';
  let drawn = false;
  const stillDrawn = vi.fn((population: string) => drawn && population === 'e5');
  const { panel, pageOf } = await page({ run: vi.fn(async () => ({ ok: true, said, table: 'e5', placed: 'specimen' as const })), stillDrawn });
  panel.open('cells');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  const notes = () => pageOf('cells').querySelector('.run-foot')!.querySelectorAll('.run-note').map((n) => n.textContent);
  // Not drawn yet (its first check on the Stage's picture has not come): in Data, and drawn there once checked.
  expect(notes()[0]).toMatch(/^\d+\.\d s, saved in Data; drawn on the specimen once checked there$/);
  drawn = true;
  panel.layerChanged(); // the layer's wire: now drawn there
  expect(notes()[0]).toMatch(/^\d+\.\d s, drawn on the specimen and saved in Data$/);
  drawn = false;
  panel.layerChanged(); // the Stage moved on to another input: the run's outcome stays as it was
  expect(notes()[0]).toMatch(/drawn on the specimen and saved in Data$/);
  expect(pageOf('cells').querySelector('.run-foot')!.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Open in Data', 'Table', 'Run again']);
});

test('done on the Stage’s still, not checked there: the server placed its masks on its Tab, and the footer says so', async () => {
  const said = 'Outlined and measured 3 cells with Cellpose-SAM.';
  let ran = '';
  const run = vi.fn(async (_c: string, _a: Record<string, unknown>, _u: Record<string, string>, id: string) => { ran = id; return { ok: true, said, table: 'e5', placed: 'specimen' as const }; });
  const { panel, pageOf } = await page({ run, stillDrawn: () => false });
  panel.open('cells');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  const notes = () => pageOf('cells').querySelector('.run-foot')!.querySelectorAll('.run-note').map((n) => n.textContent);
  expect(notes()[0]).toMatch(/saved in Data; drawn on the specimen once checked there$/);
  panel.placed('another-run', 'tab'); // another window's run: not this page's
  expect(notes()[0]).toMatch(/saved in Data; drawn on the specimen once checked there$/);
  panel.placed(ran, 'tab'); // `action-placed`: its outlines were not checked on the specimen, its masks are on Main
  expect(notes()[0]).toMatch(/^\d+\.\d s, saved on Main and in Data$/);
  panel.placed(ran, 'specimen'); // a decision is final
  expect(notes()[0]).toMatch(/saved on Main and in Data$/);
});

test('the server’s decision heard before the reply is kept for it; a run whose window never waited says its Tab', async () => {
  const said = 'Outlined and measured 3 cells with Cellpose-SAM.';
  let decide: (run: string) => void = () => {};
  const run = vi.fn(async (_c: string, _a: Record<string, unknown>, _u: Record<string, string>, id: string) => {
    decide(id);
    return { ok: true, said, table: 'e5', placed: 'specimen' as const };
  });
  const { panel, pageOf } = await page({ run, stillDrawn: () => true });
  decide = (id) => panel.placed(id, 'tab'); // `action-placed` came first: its masks placed on its Tab
  panel.open('cells');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  const notes = () => pageOf('cells').querySelector('.run-foot')!.querySelectorAll('.run-note').map((n) => n.textContent);
  expect(notes()[0]).toMatch(/^\d+\.\d s, saved on Main and in Data$/);
  // A New Tab run (or a live camera run): no `placed`, so its window is its Tab's, whatever is drawn on the specimen.
  const other = await page({ run: vi.fn(async () => ({ ok: true, said, table: 'e6' })), stillDrawn: () => true });
  other.panel.open('cells');
  await other.panel.start(other.panel.rowsShown().find((r) => r.id === 'cells')!);
  other.panel.layerChanged();
  expect(other.pageOf('cells').querySelector('.run-foot')!.querySelectorAll('.run-note').map((n) => n.textContent)[0]).toMatch(/saved on Main and in Data$/);
});

test('failed: said in plain words from the server, with Choose another model and Details (Activity)', async () => {
  const { panel, pageOf, fire, deps } = await page({ run: vi.fn(async () => ({ ok: false, error: 'This picture isn’t one 8-bit grey or RGB plane.' })) });
  panel.open('cells');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  const p = pageOf('cells');
  expect(p.dataset.phase).toBe('failed');
  expect(p.querySelector('.run-fail')!.textContent).toBe('Cellpose-SAM didn’t runThis picture isn’t one 8-bit grey or RGB plane.');
  const buttons = p.querySelector('.run-foot')!.querySelectorAll('button');
  fire(buttons.find((b) => b.textContent === 'Details') as never, 'click');
  expect(deps.openActivity).toHaveBeenCalled();
  fire(buttons.find((b) => b.textContent === 'Choose another model') as never, 'click');
  expect(pageOf('cells').querySelector('.run-models')).not.toBeNull(); // the models in place, to pick another
});

test('a model ready but not checked yet that just ran here says so on With', async () => {
  const said = 'Outlined and measured 341 cells with micro-sam (light microscopy). No scale: sizes are in pixels of the original image.';
  const { panel, pageOf } = await page({ run: vi.fn(async () => ({ ok: true, said })) });
  expect(panel.useModel('micro-sam-lm')).toBe(true);
  const sub = () => pageOf('cells').querySelector('.run-with')!.querySelector('.run-val-sub')!.textContent;
  expect(sub()).toBe('On this Mac · free · not checked on this Mac yet');
  expect(pageOf('cells').querySelector('.run-with')!.title).toBe('Ready, not checked yet: micro-sam (light microscopy) can run here, and no run of it on this Mac is recorded yet.');
  await panel.start(panel.rowsShown().find((r) => r.id === 'cells')!);
  expect(pageOf('cells').dataset.phase).toBe('done');
  // The server keeps the run as its real use (observed.py "use"): the next Models page it sends says Working here.
  expect(sub()).toBe('On this Mac · free · ran here');
  expect(pageOf('cells').querySelector('.run-with')!.title).toBe('micro-sam (light microscopy) ran here on your picture.');
});

test('a model with two tasks: a switch under the header; a click-driven task’s primary is its check, then Click in the view', async () => {
  const tryClicks = vi.fn(async () => {});
  const { panel, pageOf, fire, at, deps, wire } = await page({ tryClicks, clicksArmed: () => true });
  expect(panel.useModel('micro-sam-lm')).toBe(true);
  const p = pageOf('cells');
  expect(p.querySelector('.run-with')!.querySelector('.run-val-title')!.textContent).toBe('micro-sam (light microscopy)');
  // Not checked on this Mac yet: said where it is named, so its Run is not a surprise.
  expect(p.querySelector('.run-with')!.querySelector('.run-val-sub')!.textContent).toBe('On this Mac · free · not checked on this Mac yet');
  const tasks = p.querySelector('.run-tasks')!.querySelectorAll('button');
  expect(tasks.map((b) => [b.textContent, b.getAttribute('aria-pressed')])).toEqual([['Outline every cell', 'true'], ['Click to outline', 'false']]);
  fire(tasks[1] as never, 'click');
  let click = pageOf('click-outline');
  expect(at('cells').querySelector('.run')).toBeNull(); // one page: the switch replaced it
  expect(click.querySelectorAll('.run-val-title').map((t) => t.textContent)).toContain('Your clicks');
  // Not checked on this Mac: a click trial would be refused, so its check is the primary (no dead end, no raw refusal).
  const check = click.querySelector('.run-foot')!.querySelector('.action-step')!;
  expect(check.textContent).toBe('Check it here');
  expect(click.querySelector('.run-foot')!.textContent).toContain('micro-sam (light microscopy) hasn’t passed its check on this Mac');
  fire(check as never, 'click');
  expect(deps.openModels).toHaveBeenLastCalledWith('micro-sam-lm/setup', { task: 'click-outline' });
  expect(tryClicks).not.toHaveBeenCalled();
  // Working here by a real use only: Ready, yet a click trial still needs Hooke's own check (capability_trials), so its
  // check stays the primary rather than a Click in the view the server would refuse.
  const entry = wire.models!.find((m) => m.id === 'micro-sam-lm')!;
  entry.face = { state: 'on', by: 'use', says: 'Working here · last ran 14:02', action: null, also: [{ kind: 'check', says: 'Check it here', model: 'micro-sam-lm' }] };
  panel.setCapabilities(structuredClone(wire));
  click = pageOf('click-outline');
  expect(click.querySelector('.run-foot')!.querySelector('.action-step')!.textContent).toBe('Check it here');
  expect(click.querySelector('.run-foot')!.querySelector('.action-go')).toBeNull();
  // Checked: Click in the view arms the next click with it.
  entry.face = { state: 'on', says: 'On', action: null, also: [] };
  panel.setCapabilities(structuredClone(wire));
  click = pageOf('click-outline');
  const go = click.querySelector('.run-foot')!.querySelector('.action-go')!;
  expect(go.textContent).toBe('Click in the view');
  fire(go as never, 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(tryClicks).toHaveBeenCalledWith('outline', 'micro-sam-lm');
  expect(pageOf('click-outline').dataset.phase).toBe('armed');
  // EdgeTAM is the clicks' own model: its first task is clicking, and nothing needs arming.
  tryClicks.mockClear();
  expect(panel.useModel('edgetam')).toBe(true);
  const own = pageOf('click-outline');
  expect(own.querySelector('.run-with')!.querySelector('.run-val-title')!.textContent).toBe('EdgeTAM');
});

test('a catalog kind not set up yet: listed under Can be set up with its step; its page shows only its declared settings, disabled', async () => {
  const { panel, root, pageOf, fire } = await page();
  const setup = root.querySelectorAll('.actions-group').find((g) => g.getAttribute('aria-label') === 'Can be set up')!;
  const blur = setup.querySelector('[data-action="catalog:fiji-gaussian-blur"]')!;
  expect(blur.querySelector('.trow-step')!.textContent).toBe('Set up Fiji');
  // Its title is one line (an ellipsis, the whole sentence in its tooltip), never two.
  const title = blur.querySelector('.trow-title')!;
  expect(title.textContent).toBe('Smooths the view with ImageJ\'s Gaussian Blur');
  expect(title.title).toBe('Smooths the view with ImageJ\'s Gaussian Blur');
  expect(title.dataset && (title as unknown as { className: string }).className).toContain('trow-title-line');
  fire(blur.querySelector('.trow-hit') as never, 'click');
  const p = pageOf('catalog:fiji-gaussian-blur');
  // Its folder declares Sigma (radius), 0.5 to 20 px (the wire carries it, S11): shown, disabled until Fiji is set up.
  const sigma = p.querySelector('.run-fields')!.querySelectorAll('.run-field');
  expect(sigma.map((f) => f.querySelector('label')!.textContent)).toEqual(['Sigma (radius)']);
  expect(sigma[0].querySelectorAll('input').every((i) => i.disabled)).toBe(true);
  expect(p.querySelector('.run-locked')!.textContent).toBe('Set up Fiji to change these');
  // Every run page has the same anatomy: its Results go to as well (its run is a capability-run, with a workspace origin).
  expect(p.querySelector('.run-dest')).not.toBeNull();
  expect(p.querySelector('.run-foot')!.querySelector('.action-step')!.textContent).toBe('Set up Fiji');
  // Bright objects names its own task: its own row, Find bright regions, among the first (Common), its model built in.
  const common = root.querySelectorAll('.actions-group').find((g) => g.getAttribute('aria-label') === 'Common')!;
  const bright = common.querySelector('[data-action="catalog:basic-bright-objects"]')!;
  expect(bright.querySelector('.trow-title')!.textContent).toBe('Find bright regions');
  expect(bright.querySelector('.trow-sub')!.textContent).toBe('Bright objectsbuilt in');
  // InstanSeg implements it too, not set up yet: its row opens Outline every cell with it chosen, its whole setup the primary.
  const instanseg = setup.querySelector('[data-action="catalog:instanseg-brightfield"]')!;
  fire(instanseg.querySelector('.trow-hit') as never, 'click');
  const cells = pageOf('cells');
  expect(cells.querySelector('.run-with')!.querySelector('.run-val-title')!.textContent).toBe('InstanSeg (brightfield nuclei)');
  expect(cells.querySelector('.run-foot')!.querySelector('.action-step')!.textContent).toBe('Set up InstanSeg (brightfield nuclei)');
  void panel;
});

test('a download that is not the whole setup names the whole setup: Set up <name>, its sheet, the steps in one line', async () => {
  const download = vi.fn(async () => ({ ok: true }));
  const { panel, pageOf, pick, fire, deps } = await page({ download });
  panel.open('cells');
  pick('cells', 'instanseg-brightfield');
  const p = pageOf('cells');
  expect(p.querySelector('.run-with')!.querySelector('.run-val-sub')!.textContent).toBe('16 MB, then its own Python (35 packages)');
  const step = p.querySelector('.run-foot')!.querySelector('.action-step')!;
  expect(step.textContent).toBe('Set up InstanSeg (brightfield nuclei)');
  fire(step as never, 'click');
  // Its setup sheet, whose Prepare and turn on runs the download and the install: no install starts behind a Download.
  expect(deps.openModels).toHaveBeenLastCalledWith('instanseg-brightfield/setup', { task: 'cells' });
  expect(download).not.toHaveBeenCalled();
});

test('the Tools list: a fixed icon column, the model it will use beside its lit cell, +n more, sections and counts', async () => {
  // Two more checked-for models than the fixture has, to show the sixth and seventh waiting behind Show all.
  const { panel, root, at, fire, deps } = await page({}, (wire) => {
    const dino = wire.models!.find((m) => m.id === 'sam2.1-tiny')!;
    wire.models!.push({ ...structuredClone(dino), id: 'extra-1', name: 'Extra one', usedFor: [], abilities: [] },
      { ...structuredClone(dino), id: 'extra-2', name: 'Extra two', usedFor: [], abilities: [] });
  });
  const cells = at('cells');
  expect(cells.querySelector('.trow-lead')!.querySelector('svg')).not.toBeNull();
  expect(cells.querySelector('.trow-sub')!.textContent).toBe('Cellpose-SAM+6 more');
  const setup = root.querySelectorAll('.actions-group').find((g) => g.getAttribute('aria-label') === 'Can be set up')!;
  expect(setup.querySelector('.mm-num')!.textContent).toBe(String(setup.querySelectorAll('.trow').length));
  panel.setMode('model');
  const sections = root.querySelectorAll('.tools-section').map((s) => s.querySelector('.mm-dsection')?.textContent);
  expect(sections).toEqual(['Needs you 1', 'Ready, not checked yet 7', 'Can be set up 5']);
  // Past five, the rest wait behind Show all.
  const unchecked = root.querySelectorAll('.tools-section').find((s) => s.dataset.section === 'unchecked')!;
  expect(unchecked.querySelectorAll('.trow-model')).toHaveLength(5);
  expect(unchecked.querySelector('.mm-show-all')!.textContent).toBe('Show all 7');
  // A Not checked model's Check it here opens its check sheet, as the Models card does (never Details).
  const row = unchecked.querySelectorAll('.trow-model')[0];
  const check = row.querySelector('.trow-step')!;
  expect(check.textContent).toBe('Check it here');
  fire(check as never, 'click');
  expect(deps.openModels).toHaveBeenLastCalledWith(`${row.dataset.entry}/setup`, undefined);
});

test('a model serving three or more tasks: the app’s own menu; choosing a task with a field focused shows its page, the old one closed', async () => {
  const { panel, pageOf, fire, at, root, dom } = await page({ tryClicks: vi.fn(async () => {}), clicksArmed: () => true }, (wire) => {
    // A third task micro-sam serves (the instance lists Pick from every cell): a copy of Outline every cell.
    const cells = wire.capabilities.find((c) => c.id === 'cells')!;
    wire.capabilities.push({ ...structuredClone(cells), id: 'pick-cells', title: 'Pick from every cell' });
    wire.models!.find((m) => m.id === 'micro-sam-lm')!.abilities!.push({ id: 'pick-cells', title: 'Pick from every cell', what: '', actor: 'agent', say: '',
      role: 'cells', option: 'micro-sam-lm', current: false });
  });
  expect(panel.useModel('micro-sam-lm')).toBe(true);
  const button = pageOf('cells').querySelector('.run-task-button')!;
  expect(button.textContent).toBe('Task: Outline every cell'); // content-width, with the icon set's down chevron
  expect(button.getAttribute('aria-haspopup')).toBe('menu');
  expect(pageOf('cells').querySelector('select')).toBeNull(); // never the system's select
  fire(button as never, 'click');
  const menu = pageOf('cells').querySelector('.run-task-pop')!;
  expect(menu.getAttribute('role')).toBe('menu');
  const items = menu.querySelectorAll('button');
  expect(items.map((b) => [b.textContent, b.getAttribute('role'), b.getAttribute('aria-checked')])).toEqual([
    ['Outline every cell', 'menuitemradio', 'true'], ['Pick from every cell', 'menuitemradio', 'false'], ['Click to outline', 'menuitemradio', 'false']]);
  // A field of the page has focus (the 0.3.0 select did, deferring the redraw that shows the new page): it is let go.
  const field = Object.assign(dom.document.createElement('input'), { blur: () => { (dom.document as { activeElement?: unknown }).activeElement = null; } });
  (pageOf('cells') as unknown as { append(n: unknown): void }).append(field);
  (dom.document as { activeElement?: unknown }).activeElement = field;
  fire(items[2] as never, 'click');
  expect(pageOf('click-outline')).toBeTruthy(); // the chosen task's page is on screen…
  expect(at('cells').querySelector('.run')).toBeNull(); // …and the old one is closed, its row no longer open
  expect(at('cells').dataset.open).toBe('false');
  expect(root.querySelectorAll('.run')).toHaveLength(1);
});

test('a query runs from a researcher’s Tab through Results go to (New Tab), and its answer says nothing was saved', async () => {
  const run = vi.fn(async () => ({ ok: true, summary: 'There are about 40 nuclei.' }));
  const { panel, pageOf, fire, dom } = await page({ run }, (wire) => {
    wire.setup!.keys!.GEMINI_API_KEY.present = true;
    for (const o of wire.roles.find((r) => r.id === 'vision')!.options) if (o.id === 'gemini-3.8-flash') { o.available = true; delete o.readiness; }
  });
  dom.host.dataset.researchOpen = 'true';
  panel.open('count');
  let p = pageOf('count');
  const dest = p.querySelector('.run-dest')!;
  const [tab, fresh] = dest.querySelectorAll('button');
  expect(tab.disabled).toBe(true); // a researcher's Tab keeps its own work
  fire(fresh as never, 'click');
  p = pageOf('count');
  const label = p.querySelector('.run-fields')!.querySelectorAll('input')[0] as unknown as { value: string };
  label.value = 'nuclei'; fire(label as never, 'input');
  await panel.start(panel.rowsShown().find((r) => r.id === 'count')!);
  fire(pageOf('count').querySelector('.action-ask')!.querySelectorAll('button').find((b) => b.textContent === 'Send') as never, 'click');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(run).toHaveBeenCalledWith('count', { label: 'nuclei' }, {}, expect.any(String), undefined, undefined, undefined, { newTab: true });
  const foot = pageOf('count').querySelector('.run-foot')!;
  expect(foot.querySelector('.action-result-text')!.textContent).toBe('There are about 40 nuclei.');
  expect(foot.textContent).not.toContain('saved'); // an answer keeps nothing (ADR 0006)
  expect(foot.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Run again', 'Details']);
});

test('an effect (Clear outlines) says what it did, never that it saved something, with no Open in Data', async () => {
  const run = vi.fn(async () => ({ ok: true, summary: 'Cleared 3 outlines' }));
  const { panel, pageOf, fire, deps } = await page({ run }, (wire, tools) => {
    tools.clear = { kind: 'effect', family: 'scene', params: { type: 'object', properties: {} } };
    wire.capabilities.push({ id: 'clear', family: 'outlining', actor: 'agent', title: 'Clear outlines', what: 'Takes the outlines off the view',
      how: { tool: 'clear' }, roles: [], available: true, why: '', verdict: null } as unknown as CapabilitiesWire['capabilities'][number]);
  });
  panel.open('clear');
  await panel.start(panel.rowsShown().find((r) => r.id === 'clear')!);
  const foot = pageOf('clear').querySelector('.run-foot')!;
  expect(pageOf('clear').dataset.phase).toBe('done');
  expect(foot.querySelector('.action-result-text')!.textContent).toBe('Cleared 3 outlines');
  expect(foot.textContent).not.toMatch(/saved|in Data/);
  // Nothing kept and no table: Details opens Activity, on Run again's row.
  expect(foot.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Run again', 'Details']);
  expect(foot.querySelectorAll('.run-foot-row').filter((r) => r.querySelectorAll('button').length)).toHaveLength(1);
  fire(foot.querySelector('.run-done-details') as never, 'click');
  expect(deps.openActivity).toHaveBeenCalledTimes(1);
});

test('a click that can’t be armed is said in the Failed footer’s anatomy, with Choose another model (never red text)', async () => {
  const tryClicks = vi.fn(async () => { throw new Error('The view changed before the click was armed'); });
  const { panel, pageOf, fire } = await page({ tryClicks, clicksArmed: () => true }, (wire) => {
    wire.models!.find((m) => m.id === 'micro-sam-lm')!.face = { state: 'on', says: 'On', action: null, also: [] };
  });
  panel.open('click-outline', { role: 'outline', option: 'micro-sam-lm' });
  fire(pageOf('click-outline').querySelector('.run-foot')!.querySelector('.action-go') as never, 'click');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  const foot = pageOf('click-outline').querySelector('.run-foot')!;
  const fail = foot.querySelector('.run-fail')!;
  expect(fail.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('attention');
  expect(fail.textContent).toBe('micro-sam (light microscopy) didn’t startThe view changed before the click was armed');
  expect(foot.querySelector('.is-error')).toBeNull();
  fire(foot.querySelectorAll('button').find((b) => b.textContent === 'Choose another model') as never, 'click');
  expect(pageOf('click-outline').querySelector('.run-models')).not.toBeNull();
});

test('a cloud model says where the picture goes before its key is added; a key step is the person’s (the needs cell)', async () => {
  const { panel, pageOf } = await page();
  panel.open('count');
  const p = pageOf('count');
  expect(p.querySelectorAll('.run-quiet').map((n) => n.textContent)).toContain('Sends this picture to Google, billed per call');
  expect(p.querySelector('.run-foot')!.querySelector('.action-step')!.textContent).toBe('Add key');
  panel.open('find-like');
  const like = pageOf('find-like');
  expect(like.querySelectorAll('.run-quiet').map((n) => n.textContent)).toContain('Sends this picture to fal, billed per call');
  expect(like.querySelector('.run-with')!.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('needs');
});

test('More options only when the chosen model declares advanced fields; Analyze Particles’ comparison only with it; What it runs on from the page’s items', async () => {
  const { panel, pageOf, pick, fire } = await page();
  panel.open('cells');
  expect(pageOf('cells').querySelector('.run-more')).toBeNull(); // Cellpose-SAM declares none
  expect(pageOf('cells').textContent).not.toContain('Compare with the ImageJ macro');
  pick('cells', 'basic-bright-objects');
  const more = pageOf('cells').querySelector('.run-more')!;
  expect(more.querySelectorAll('label').map((l) => l.textContent)).toEqual(['Joined when']);
  pick('cells', 'fiji-analyze-particles');
  expect(pageOf('cells').querySelector('.run-more')!.textContent).toContain('Compare with the ImageJ macro');
  // What it runs on (0.3.0's route, kept): the page's own item, here a quiet link, shown under a hairline.
  expect(pageOf('cells').querySelector('.run-powers')).toBeNull();
  fire(pageOf('cells').querySelector('.run-links')!.querySelectorAll('button').find((b) => b.textContent === 'What it runs on') as never, 'click');
  expect(pageOf('cells').querySelector('.run-powers')!.querySelector('h3')!.textContent).toBe('What it runs on');
});

test('failed with no other model to choose: Run again, never a Choose another model that leads nowhere', async () => {
  const { panel, pageOf, fire } = await page({ run: vi.fn(async () => ({ ok: false, error: 'This picture isn’t one 8-bit grey or RGB plane.' })) });
  panel.open('catalog:basic-bright-objects');
  await panel.start(panel.rowsShown().find((r) => r.id === 'catalog:basic-bright-objects')!);
  const buttons = pageOf('catalog:basic-bright-objects').querySelector('.run-foot')!.querySelectorAll('button');
  expect(buttons.map((b) => b.textContent)).toEqual(['Run again', 'Details']);
  fire(buttons[0] as never, 'click');
  expect(pageOf('catalog:basic-bright-objects').dataset.phase).toBe('ready');
});

test('Escape in the models list closes it and gives focus back to Change model', async () => {
  const { panel, pageOf, fire } = await page();
  panel.open('cells');
  fire(pageOf('cells').querySelector('.run-with')!.querySelector('.run-change') as never, 'click');
  const list = pageOf('cells').querySelector('.run-models')!;
  fire(list as never, 'keydown', { key: 'Escape' });
  expect(pageOf('cells').querySelector('.run-models')).toBeNull();
  const change = pageOf('cells').querySelector('.run-with')!.querySelector('.run-change') as unknown as { focus: { mock: { calls: unknown[] } } };
  expect(change.focus.mock.calls.length).toBeGreaterThan(0);
});
