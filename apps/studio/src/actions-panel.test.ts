import { tool } from './toolbox-page.test';
// The Actions panel's rows and forms (actions-panel.ts): rows are the capability list's agent capabilities with a tool
// here, in the families that act on the view; a form is the tool's own arguments with the capability's presets; the
// selection fills the argument naming the thing acted on; whether it can run, its next step, its cloud model and "Run
// with…" are the capabilities message's.
import { describe, expect, test } from 'vitest';
import { ACTION_GROUPS, COMMON, actionRows, argsFor, blocked, formOf, matches, powersOf, resultWords, settledReply, stepButton, type ToolList } from './actions-panel';
import type { CapabilitiesWire, Capability, Role } from './protocol';

const cap = (over: Partial<Capability> & { id: string }): Capability => ({
  family: 'outlining', actor: 'agent', title: over.id, what: '', how: {}, available: true, why: '', verdict: null, ...over,
});

const role = (over: Partial<Role> & { id: string }): Role => ({
  kind: 'model', title: over.id, purpose: '', current: null, options: [], powers: [], agentCanChange: [], ...over,
});

const tools: ToolList = {
  clear: { kind: 'effect', family: 'scene', params: { type: 'object', properties: { what: { type: 'string' } } } },
  find: { kind: 'effect', family: 'finding', params: { type: 'object', required: ['what'], properties: {
    what: { type: 'string', description: 'what to find' }, inside: { type: 'string' }, like: { type: 'string' },
    all: { type: 'boolean' }, by: { type: 'string', enum: ['look', 'cells', 'concept'] } } } },
  measure: { kind: 'query', family: 'measuring', params: { type: 'object', required: ['object'], properties: { object: { type: 'string' } } } },
  highlight: { kind: 'effect', family: 'scene', params: { type: 'object', required: ['things'], properties: { things: { type: 'array' } } } },
  set_objective: { kind: 'effect', family: 'measuring', params: { type: 'object', required: ['objective'], properties: {
    objective: { type: 'integer' }, field_number: { type: 'number' } } } },
  outline_cells: { kind: 'effect', family: 'scene', params: { type: 'object', properties: {} } },
  start_researcher: { kind: 'effect', family: 'research', params: { type: 'object', properties: {} } },
};

function wire(capabilities: Capability[], roles: Role[] = []): CapabilitiesWire {
  return { type: 'capabilities', families: [], actors: { you: '', agent: '', auto: '', researcher: '' }, capabilities, roles,
    preferences: [], pending: 0, build: '', agentText: '' };
}

describe('rows', () => {
  test('are the agent capabilities with a tool here, in the families that act on the view, in the groups order', () => {
    const rows = actionRows(wire([
      cap({ id: 'measure', family: 'measuring', how: { tool: 'measure' } }),
      cap({ id: 'clear', how: { tool: 'clear' } }),
      cap({ id: 'researcher', family: 'researchers', how: { tool: 'start_researcher' } }), // the agent's: needs words
      cap({ id: 'clear-button', actor: 'you', how: { control: 'clear' } }), // the user's own control, no tool
      cap({ id: 'cells', how: { tool: 'outline_cells_not_here' } }), // its tool is not in this app
      cap({ id: 'look-at-screen', family: 'display', how: { tool: 'clear' } }), // the agent's own
    ]), tools);
    expect(rows.map((r) => r.id)).toEqual(['clear', 'measure']);
    expect(ACTION_GROUPS[0].family).toBe('outlining');
  });

  test('a row whose required argument is a list stays the agent’s (it is said in words)', () => {
    expect(actionRows(wire([cap({ id: 'highlight', how: { tool: 'highlight' } })]), tools)).toEqual([]);
  });
});

describe('forms', () => {
  test('the selection fills object; fields are the rest; the agent’s machinery is no field', () => {
    const form = formOf(cap({ id: 'find', how: { tool: 'find' } }), tools.find)!;
    expect(form.target).toBeNull(); // a plain find acts on the view: inside and like are its variants'
    expect(form.fields.map((f) => [f.key, f.kind, f.required])).toEqual([['what', 'text', true], ['by', 'choice', false]]);
    // A hint written for a person (contract/actions.json), never the tool schema's description, which is the model's.
    expect(form.fields[0].hint).toBe('The thing\'s name, such as nucleus');
    expect(form.fields[1].hint).toBe('');
    expect(formOf(cap({ id: 'measure', how: { tool: 'measure' } }), tools.measure)!.target).toEqual({ key: 'object', required: true });
    // An outline's own action needs one selected, though its tool would take the view.
    const dissect: ToolList[string] = { kind: 'effect', family: 'scene', params: { type: 'object', properties: { object: { type: 'string' } } } };
    expect(formOf(cap({ id: 'zoom', how: { tool: 'zoom' } }), dissect)!.target).toEqual({ key: 'object', required: false });
    expect(formOf(cap({ id: 'dissect', how: { tool: 'dissect' } }), dissect)!.target).toEqual({ key: 'object', required: true });
  });

  test('a preset naming the one meant is the target (required); other presets are fixed, never fields', () => {
    const inside = formOf(cap({ id: 'find-inside', how: { tool: 'find', args: { inside: 'o1' } } }), tools.find)!;
    expect(inside.target).toEqual({ key: 'inside', required: true });
    const concept = formOf(cap({ id: 'find-concept', how: { tool: 'find', args: { by: 'concept' } } }), tools.find)!;
    expect(concept.target).toBeNull();
    expect(concept.fields.map((f) => f.key)).toEqual(['what']);
  });

  test('an argument whose description only lists its choices is a choice ("motion | appears | look")', () => {
    const watch: ToolList[string] = { kind: 'effect', family: 'time', params: { type: 'object', required: ['by'], properties: {
      by: { type: 'string', description: 'motion | appears | look' }, question: { type: 'string', description: 'look: the yes/no question' } } } };
    const form = formOf(cap({ id: 'watch', how: { tool: 'watch' } }), watch)!;
    expect(form.fields.map((f) => [f.key, f.kind, f.choices])).toEqual([['by', 'choice', ['motion', 'appears', 'look']], ['question', 'text', []]]);
  });

  test('numbers are numbers, empty fields are left out, and the selection is the target', () => {
    const [row] = actionRows(wire([cap({ id: 'objective', family: 'measuring', how: { tool: 'set_objective' } })]), tools);
    expect(argsFor(row, { objective: '40', field_number: '' }, null)).toEqual({ objective: 40 });
    const [measure] = actionRows(wire([cap({ id: 'measure', family: 'measuring', how: { tool: 'measure' } })]), tools);
    expect(argsFor(measure, {}, { id: 'o3', label: 'stoma' })).toEqual({ object: 'o3' });
  });

  test('what blocks a run is said: not here, nothing selected, a required field empty', () => {
    const [measure] = actionRows(wire([cap({ id: 'measure', family: 'measuring', how: { tool: 'measure' } })]), tools);
    expect(blocked(measure, {}, null)).toBe('Select an outline in the view first');
    expect(blocked(measure, {}, { id: 'o3', label: 'stoma' })).toBeNull();
    const [find] = actionRows(wire([cap({ id: 'find', family: 'finding', how: { tool: 'find' } })]), tools);
    expect(blocked(find, {}, null)).toBe('Fill in “What to find”'); // its words: contract/actions.json `fields`
    const [off] = actionRows(wire([cap({ id: 'clear', how: { tool: 'clear' }, available: false, why: 'needs a GPU worker' })]), tools);
    expect(blocked(off, {}, null)).toBe('needs a GPU worker');
  });
});

describe('models', () => {
  const vision = role({ id: 'vision', current: 'gemini-3.8-flash', options: [
    { id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash', note: '', available: true, runs: 'cloud' },
    { id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash', note: '', available: true, runs: 'cloud', said: 'Gemini 3.8 Flash (cloud)' },
    { id: 'gpt-6', label: 'GPT-6', note: '', available: false, why: 'needs OPENAI_API_KEY', runs: 'cloud' },
  ] });
  const cells = role({ id: 'cells', current: 'cellpose-sam', options: [
    { id: 'cellpose-sam', label: 'Cellpose-SAM', note: '', available: false, runs: 'mac',
      readiness: { status: 'needs-setup', word: 'Download 1.2 GB', group: 'Needs setup', steps: [],
        next: { kind: 'download', says: 'Download Cellpose-SAM (1.2 GB)', model: 'cellpose-sam' } } },
  ] });

  test('a cloud model is named (asked once per session); Run with offers the models that can run, the one in use first', () => {
    const [row] = actionRows(wire([cap({ id: 'find', family: 'finding', how: { tool: 'find' }, roles: ['vision'] })], [vision]), tools);
    expect(row.cloud).toBe('Gemini 3.8 Flash (cloud)');
    expect(row.runWith).toEqual({ role: 'vision', title: 'vision', current: 'gemini-3.8-flash', clouds: { 'gemini-3.8-flash': 'Gemini 3.8 Flash (cloud)', 'gemini-3.5-flash': 'Gemini 3.5 Flash' }, options: [
      { id: 'gemini-3.8-flash', label: 'Gemini 3.8 Flash (cloud)' }, { id: 'gemini-3.5-flash', label: 'Gemini 3.5 Flash' }] });
  });

  test('a row that can’t run here says the Models page’s next step', () => {
    const [row] = actionRows(wire([cap({ id: 'cells', how: { tool: 'outline_cells' }, roles: ['cells'], available: false,
      why: 'Cellpose-SAM is not downloaded' })], [cells]), tools);
    expect(row.available).toBe(false);
    expect(row.step).toEqual({ kind: 'download', says: 'Download Cellpose-SAM (1.2 GB)', model: 'cellpose-sam' });
    expect(stepButton(row.step!)).toBe('Download');
    // The server's own SI words (readiness.py size_words): one download never shows two sizes (0.3.0's 413 MB / 394 MB).
    expect(stepButton({ ...row.step!, bytes: 1_288_490_189 })).toBe('Download 1.3 GB');
    expect(row.cloud).toBeNull();
    expect(row.runWith).toBeNull(); // nothing else can run it here
  });

  test('a sole ready alternative can be explicitly selected when the default needs setup', () => {
    const choices = role({ ...cells, options: [...cells.options,
      { id: 'off', label: 'Off', note: '', available: true, runs: 'mac' },
      { id: 'fiji-analyze-particles', label: 'Analyze Particles (Fiji)', note: '', available: true, runs: 'mac' }] });
    const [row] = actionRows(wire([cap({ id: 'cells', how: { tool: 'outline_cells' }, roles: ['cells'], available: false,
      why: 'Cellpose-SAM is not downloaded' })], [choices]), tools);
    expect(row.available).toBe(true);
    expect(row.runWith?.options.map((o) => o.id)).toEqual(['fiji-analyze-particles']);
    expect(blocked(row, {}, null)).toContain('Choose');
    expect(blocked(row, {}, null, 'fiji-analyze-particles')).toBeNull();
    expect(blocked(row, {}, null, 'missing')).toContain('Choose');
  });
});

test('Bright objects search discovers the local alternative without changing an unavailable default', () => {
  const cells = role({ id: 'cells', current: 'cellpose-sam', options: [
    { id: 'cellpose-sam', label: 'Cellpose-SAM', note: '', available: false, runs: 'mac' },
  ] });
  const choices = role({ ...cells, options: [...cells.options,
    { id: 'basic-bright-objects', label: 'Bright objects · Basic local method', note: '', available: true, runs: 'mac' }] });
  const [row] = actionRows(wire([cap({ id: 'cells', how: { tool: 'outline_cells' }, roles: ['cells'], available: false,
    why: 'Cellpose-SAM is not downloaded' })], [choices]), tools);
  expect(matches(row, 'Bright objects')).toBe(true);
  expect(blocked(row, {}, null)).toContain('Choose');
  expect(blocked(row, {}, null, 'basic-bright-objects')).toBeNull();
  expect(row.runWith?.current).toBe(cells.current);
});

test('search matches its title, what it does and how it is asked', () => {
  const [row] = actionRows(wire([cap({ id: 'clear', title: 'Clear outlines', what: 'Removes the app’s outlines',
    how: { tool: 'clear', say: 'clear the screen' } })]), tools);
  expect(matches(row, 'clear')).toBe(true);
  expect(matches(row, 'screen outlines')).toBe(true);
  expect(matches(row, 'count')).toBe(false);
});

test('a described task finds the row by its declared words, by stem (L7e walk, 2026-10-20)', () => {
  const [row] = actionRows(wire([cap({ id: 'cells', title: 'Outline every cell', what: 'A cell finder segments every cell; they are kept and measured',
    how: { tool: 'outline_cells', say: 'outline all the cells' } })]), tools);
  for (const query of ['segmentation', 'segment the cells', 'Outline every cell', 'outlining cells', 'measured cells'])
    expect(matches(row, query), query).toBe(true);
  expect(matches(row, 'track movement')).toBe(false);
  expect(matches(row, 'mask'), 'a word the capability does not declare').toBe(false);
});

describe('plain words', () => {
  test("a field says what it is, its choices plainly (the value sent stays the tool's), a number chosen is sent as one", () => {
    const find = formOf(cap({ id: 'find', how: { tool: 'find' } }), tools.find)!;
    const by = find.fields.find((f) => f.key === 'by')!;
    expect(by.label).toBe('How');
    expect(by.choices).toEqual(['look', 'cells', 'concept']);
    expect(by.said.look).toBe('By how it looks');
    const [objective] = actionRows(wire([cap({ id: 'objective', family: 'measuring', how: { tool: 'set_objective' } })]), tools);
    const field = objective.fields.find((f) => f.key === 'objective')!;
    expect([field.kind, field.numeric, field.said['40']]).toEqual(['choice', true, '40×']);
    expect(argsFor(objective, { objective: '40' }, null)).toEqual({ objective: 40 });
  });

  test("a yes/no the tool takes as yes starts checked; the agent's machinery is no field", () => {
    const snapshot: ToolList[string] = { kind: 'effect', family: 'snapshots', params: { type: 'object', properties: {
      labels: { type: 'boolean' }, overlays: { type: 'boolean' }, object: { type: 'string' } } } };
    const form = formOf(cap({ id: 'snapshot', how: { tool: 'snapshot' } }), snapshot)!;
    expect(form.fields.map((f) => [f.label, f.initial])).toEqual([['With labels', true], ['With outlines', true]]);
    const groups: ToolList[string] = { kind: 'effect', family: 'scene', params: { type: 'object', properties: {
      groups: { type: 'integer' }, group: { type: 'string' }, name: { type: 'string' } } } };
    expect(formOf(cap({ id: 'group-cells', how: { tool: 'group_cells' } }), groups)!.fields.map((f) => f.label)).toEqual(['Into how many groups']);
  });

  test("a result says what was measured when the server wrote it, else the summary without the app's ids", () => {
    expect(resultWords({ ok: true, summary: 'Cell outlines (e1): 6 instances', said: 'Outlined and measured 6 cells with Cellpose-SAM.' }))
      .toBe('Outlined and measured 6 cells with Cellpose-SAM.');
    expect(resultWords({ ok: true, summary: 'the stoma (o1) is in view now' })).toBe('the stoma is in view now');
    expect(resultWords({ ok: false, error: 'nothing to measure' })).toBe('nothing to measure');
  });

  test("the common actions come first, in the contract's order", () => {
    expect(COMMON[0]).toBe('cells');
    expect(COMMON).toContain('measure');
  });
});

describe('Stop', () => {
  test('a running row has a Stop that sends action-cancel with its own run id, and the row then says it was stopped', async () => {
    const { ActionsPanel } = await import('./actions-panel');
    const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
    const { vi } = await import('vitest');
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    try {
      const runs: string[] = [];
      const stops: string[] = [];
      let answer: (reply: { ok: boolean; stopped?: boolean }) => void = () => {};
      const root = dom.document.createElement('section') as unknown as HTMLElement;
      const panel = new ActionsPanel(root, {
        run: (_capability, _args, _use, run) => { runs.push(run); return new Promise((done) => { answer = done; }); },
        stop: async (run) => { stops.push(run); answer({ ok: true, stopped: true }); return { ok: true }; },
        selected: () => null, session: () => 's1', openModels: () => {}, openActivity: () => {}, openTable: () => {},
        openCamera: () => {}, cameraPage: () => '/camera/',
      });
      panel.setTools(tools);
      panel.setCapabilities(wire([cap({ id: 'clear', title: 'Clear outlines', how: { tool: 'clear' } })]));
      const row = () => (root as unknown as { querySelector(s: string): { querySelector(s: string): unknown } | null })
        .querySelector('[data-action="clear"]')!;
      expect(row().querySelector('.action-stop')).toBeNull(); // nothing to stop before it runs
      const running = panel.start(panel.rowsShown()[0]);
      const stop = row().querySelector('.action-stop') as Parameters<typeof fire>[0];
      expect(stop).not.toBeNull();
      expect(runs).toHaveLength(1);
      fire(stop, 'click');
      await settleMicrotasks();
      await running;
      expect(stops).toEqual(runs); // exactly that run
      expect(row().querySelector('.action-stop')).toBeNull();
      // Its page (opened by the run) says Stopped in grey, with the server's words when it said more.
      expect((row().querySelector('.run') as unknown as { dataset: Record<string, string> }).dataset.phase).toBe('stopped');
      expect((row().querySelector('.action-result-text') as { textContent: string }).textContent).toBe('Stopped');
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test('a row whose Stop said "stopping" stays its run (Stopping, no Run) until that exact run settles', async () => {
    const { ActionsPanel } = await import('./actions-panel');
    const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
    const { vi } = await import('vitest');
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    try {
      const runs: string[] = [];
      let answer: (reply: { ok: boolean; stopping?: boolean }) => void = () => {};
      const root = dom.document.createElement('section') as unknown as HTMLElement;
      const panel = new ActionsPanel(root, {
        run: (_capability, _args, _use, run) => { runs.push(run); return new Promise((done) => { answer = done; }); },
        stop: async () => { answer({ ok: true, stopping: true }); return { ok: true }; },
        selected: () => null, session: () => 's1', openModels: () => {}, openActivity: () => {}, openTable: () => {},
        openCamera: () => {}, cameraPage: () => '/camera/',
      });
      panel.setTools(tools);
      panel.setCapabilities(wire([cap({ id: 'clear', title: 'Clear outlines', how: { tool: 'clear' } })]));
      type Node = { querySelector(s: string): Node | null; textContent: string; disabled?: boolean };
      const row = () => (root as unknown as Node).querySelector('[data-action="clear"]')!;
      const go = () => row().querySelector('.action-go');
      const again = () => row().querySelector('.action-again');
      const said = () => row().querySelector('.action-result-text')?.textContent;
      const phase = () => (row().querySelector('.run') as unknown as { dataset: Record<string, string> }).dataset.phase;
      const running = panel.start(panel.rowsShown()[0]);
      fire(row().querySelector('.action-stop') as unknown as Parameters<typeof fire>[0], 'click');
      await settleMicrotasks();
      await running;
      expect(phase()).toBe('stopping');
      expect(said()).toBe("Stop requested: it hasn't ended yet");
      expect(go()).toBeNull(); // no Run while its work is still ending
      expect(row().querySelector('.action-stop')!.textContent).toBe('Stop requested'); // its row's Stop, held
      expect((row().querySelector('.run-foot')!.querySelector('.action-stop') as unknown as { textContent: string; disabled: boolean }))
        .toMatchObject({ textContent: 'Waiting to confirm', disabled: true }); // the footer's, until the server confirms
      await panel.start(panel.rowsShown()[0]); // a second press does nothing while it stops
      expect(runs).toHaveLength(1);
      panel.settled('another-run', { outcome: 'failed', text: 'not this one' }); // another run's settlement: not this row
      expect(said()).toBe("Stop requested: it hasn't ended yet");
      panel.settled(runs[0], { outcome: 'completed', text: '“Clear outlines” ended on its own after you pressed Stop: nothing it made was used' });
      expect(said()).toBe('“Clear outlines” ended on its own after you pressed Stop: nothing it made was used');
      expect(again()).not.toBeNull(); // done: Run again
      expect(row().querySelector('.action-stop')).toBeNull();

      const second = panel.start(panel.rowsShown()[0]); // stopped again, then the connection is lost before it settles
      fire(row().querySelector('.action-stop') as unknown as Parameters<typeof fire>[0], 'click');
      await settleMicrotasks();
      await second;
      panel.connectionLost();
      expect(said()).toContain("Can't tell how it ended: the connection was lost");
      expect(phase()).toBe('failed');
      expect(row().querySelector('.action-stop')).toBeNull();
      panel.settled(runs[1], { outcome: 'stopped', text: '' }); // nothing left to settle: no change
      expect(said()).toContain("Can't tell how it ended");
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test('a run that didn’t run can be run again, and its failure stays with the Tab it was run on', async () => {
    // Demo try-out, 30 Oct: Cellpose-SAM refused a 12-megapixel photo; the row then offered only Choose another model,
    // and on the next Tab (a small recording it could outline) it still said it didn’t run, with no Run at all.
    const { ActionsPanel } = await import('./actions-panel');
    const { syntheticDOM, settleMicrotasks } = await import('./workspace-windows.test-support');
    const { vi } = await import('vitest');
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    try {
      const cells = role({ id: 'cells', current: 'cellpose-sam', options: [
        { id: 'cellpose-sam', label: 'Cellpose-SAM', note: '', available: true, runs: 'mac' },
        { id: 'basic-bright-objects', label: 'Bright objects', note: '', available: true, runs: 'mac' }] });
      const root = dom.document.createElement('section') as unknown as HTMLElement;
      (dom.document.body as unknown as { dataset: Record<string, string> }).dataset.activeWorkspaceView = 'tab-photo';
      const panel = new ActionsPanel(root, {
        run: async () => ({ ok: false, error: 'the picture is too large to outline here' }),
        stop: async () => ({ ok: true }),
        selected: () => null, session: () => 's1', openModels: () => {}, openActivity: () => {}, openTable: () => {},
        openCamera: () => {}, cameraPage: () => '/camera/',
      });
      panel.setTools(tools);
      panel.setCapabilities(wire([cap({ id: 'cells', title: 'Outline every cell', how: { tool: 'outline_cells' }, roles: ['cells'] })], [cells]));
      type Node = { querySelector(s: string): Node | null; textContent: string };
      const row = () => (root as unknown as Node).querySelector('[data-action="cells"]')!;
      const phase = () => (row().querySelector('.run') as unknown as { dataset: Record<string, string> }).dataset.phase;
      await panel.start(panel.rowsShown()[0]);
      await settleMicrotasks();
      expect(phase()).toBe('failed');
      expect(row().querySelector('.action-again')).not.toBeNull(); // Run again, beside Choose another model
      (dom.document.body as unknown as { dataset: Record<string, string> }).dataset.activeWorkspaceView = 'tab-leaf';
      panel.render();
      expect(phase()).toBe('ready'); // another Tab: its own Run, not this photo's failure
      expect(row().querySelector('.action-go')).not.toBeNull();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test('a run still ending after Stop is said as stopping, never stopped', () => {
    expect(resultWords({ ok: true, stopped: true })).toBe('Stopped');
    expect(resultWords({ ok: true, stopping: true })).toBe("Stop requested: it hasn't ended yet");
    const words = 'Stopping “Slow job”: its work hasn\'t ended yet (you pressed Stop). Kept: nothing had finished yet. Discarded: what it was computing; nothing of it is shown or saved.';
    expect(resultWords({ ok: true, stopping: true, summary: words })).toBe(words); // the server's report, as main.ts passes it
  });

  test('a Stop says what it did when the server says it: what stopped, was kept, was discarded, carries on (L7b)', () => {
    const words = 'Stopped “Outline every cell” (you pressed Stop). Kept: nothing had finished yet. Discarded: what it was computing; nothing of it is shown or saved.';
    expect(resultWords({ ok: true, stopped: true, summary: words, stop: { outcome: 'stopped', words, kept: [], lost: [], carriesOn: [] } })).toBe(words);
    const shared = 'Stopped waiting for “Outline every cell” (you pressed Stop); its work was not ended. Still running because another request needs it: Cells with Cellpose-SAM.';
    expect(resultWords({ ok: true, stopped: true, stop: { outcome: 'nothing', words: shared, kept: [], lost: [], carriesOn: ['Cells with Cellpose-SAM'] } })).toBe(shared);
  });

  test('a stop that settles late says what it did, when the server said it (L7b)', () => {
    const said = 'Stopped “Slow job” (you pressed Stop). Kept: nothing had finished yet. Discarded: what it was computing; nothing of it is shown or saved.';
    expect(resultWords(settledReply({ outcome: 'stopped', text: 'Stopped: “Slow job” (you pressed Stop)', said }))).toBe(said);
    expect(resultWords(settledReply({ outcome: 'stopped', text: 'Stopped: “Slow job” (you pressed Stop)' }))).toBe('Stopped');
  });

  test('a row that can only be set up says Needs setup, as the Toolbox does, never not available (L7b)', () => {
    const rows = actionRows(wire([cap({ id: 'cells', title: 'Outline every cell', how: { tool: 'clear' }, available: false,
      why: 'not downloaded yet: 1.2 GB', said: 'Needs setup: Download 1.2 GB (not downloaded yet: 1.2 GB)' } as never)]), tools);
    expect(rows[0].why).toBe('Needs setup: Download 1.2 GB (not downloaded yet: 1.2 GB)');
  });
});

describe('what a row runs on (shown when it is opened)', () => {
  test('each model role’s model in use by its job, then methods, then the algorithms of its code; policies are not', () => {
    const roles = [
      role({ id: 'motion-way', kind: 'method', title: 'How motion is measured', current: 'spots', options: [
        { id: 'spots', label: 'Spots, linked', note: '', available: true }] }),
      role({ id: 'cells', title: 'Cell finder', current: 'cellpose-sam', options: [
        { id: 'cellpose-sam', label: 'Cellpose-SAM', said: 'Cellpose-SAM (on this Mac)', note: '', available: true, runs: 'mac' }] }),
      role({ id: 'naming', title: 'Naming', fixed: true, primary: { label: 'Gemini 3.8 Flash (cloud)', value: 'g', runs: 'cloud', available: true }, current: 'g' }),
      role({ id: 'ask-first', kind: 'policy', title: 'Ask before sending', current: 'once', options: [{ id: 'once', label: 'Once a session', note: '', available: true }] }),
    ];
    const w = { ...wire([], roles), parts: [
      { id: 'tracker', title: 'Slide tracker', kind: 'algorithm' as const, purpose: '', source: '', powers: [] },
      { id: 'bridge', title: 'Camera bridge', kind: 'app' as const, purpose: '', source: '', powers: [] }] };
    expect(powersOf(w, { roles: ['motion-way', 'cells', 'naming', 'ask-first'], parts: ['tracker', 'bridge'] })).toEqual([
      { job: 'Cell finder', name: 'Cellpose-SAM (on this Mac)', kind: 'model', cloud: false },
      { job: 'Naming', name: 'Gemini 3.8 Flash (cloud)', kind: 'model', cloud: true },
      { job: 'How motion is measured', name: 'Spots, linked', kind: 'method', cloud: false },
      { job: 'Algorithm', name: 'Slide tracker', kind: 'algorithm', cloud: false },
    ]);
    const [row] = actionRows(wire([cap({ id: 'cells', how: { tool: 'outline_cells' }, roles: ['cells'] })], roles), tools);
    expect(row.powers).toEqual([{ job: 'Cell finder', name: 'Cellpose-SAM (on this Mac)', kind: 'model', cloud: false }]);
  });

  test('a capability the catalog added says what runs it, never the app’s own code', () => {
    // Live 2026-10-15: Swin2SR (a model) and Fiji's Gaussian Blur both read "No model: the app's own code".
    expect(powersOf(wire([]), { roles: [], parts: [], runsOn: 'Gaussian Blur (Fiji): ImageJ, in the app’s own Fiji on this Mac' }))
      .toEqual([{ job: 'Runs', name: 'Gaussian Blur (Fiji): ImageJ, in the app’s own Fiji on this Mac', kind: 'method', cloud: false }]);
  });
});

describe('Camera', () => {
  test("Camera controls' Open opens the phone panel itself, its click kept from the document (which would close it)", async () => {
    const { ActionsPanel } = await import('./actions-panel');
    const { syntheticDOM, fire } = await import('./workspace-windows.test-support');
    const { vi } = await import('vitest');
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    try {
      const openCamera = vi.fn();
      const root = dom.document.createElement('section') as unknown as HTMLElement;
      const panel = new ActionsPanel(root, {
        run: async () => ({ ok: true }), stop: async () => ({ ok: true }),
        selected: () => null, session: () => 's1', openModels: () => {}, openActivity: () => {}, openTable: () => {},
        openCamera, cameraPage: () => '/camera/',
      });
      panel.setTools(tools);
      panel.setCapabilities(wire([]));
      type Node = Parameters<typeof fire>[0] & { getAttribute(name: string): string | null };
      const open = (root as unknown as { querySelectorAll(s: string): Node[] }).querySelectorAll('.action-go')
        .find((b) => b.getAttribute('aria-label') === 'Open the camera controls')!;
      expect(open).toBeDefined();
      const stopPropagation = vi.fn();
      fire(open, 'click', { stopPropagation });
      expect(openCamera).toHaveBeenCalledTimes(1);
      expect(stopPropagation).toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  test("the studio opens it as Troubleshoot does, never by clicking the header's camera control (Live camera while an asset is on stage)", async () => {
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const main = readFileSync(fileURLToPath(new URL('./main.ts', import.meta.url)), 'utf8');
    const wiring = main.match(/^\s*openCamera: (.*)$/m)?.[1] ?? '';
    expect(wiring).toBe('() => phonePanel.setOpen(true),');
    expect(main).not.toMatch(/openCamera:[^\n]*element<HTMLButtonElement>\('source'\)\.click\(\)/);
  });

  test('a run page step hands its task to Models, and a refused researcher start reaches the New researcher Tab (main.ts wiring)', async () => {
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const main = readFileSync(fileURLToPath(new URL('./main.ts', import.meta.url)), 'utf8');
    expect(main).toMatch(/openModels: \(model, opener\) => \{\s*if \(opener\) modelsPage\.openedFrom\(opener\.task\);/);
    expect(main).toMatch(/run\.tool === 'start_researcher' && run\.ok === false\)\?\.summary/);
    expect(main).toMatch(/'hooke-ask-settled', \{ detail: \{ requestId: message\.requestId, ok: message\.ok, error: failure\?\.error \?\? null, refused \}/);
  });
});

describe('a capability the catalog added (the one door, ADR 0024)', () => {
  const runCapability: ToolList = { ...tools, run_capability: { kind: 'effect', family: 'seeing', params: { type: 'object',
    required: ['capability'], properties: { capability: { type: 'string' }, params: { type: 'object' } } } } };
  const bands = { key: 'bands', label: 'Bands', kind: 'number' as const, required: false, hint: 'How many bands',
    choices: [], said: {}, numeric: true, initial: 4, min: 1, max: 16, advanced: false };

  test('brings its own form, its declared parameters, and is listed in its family', () => {
    const rows = actionRows(wire([cap({ id: 'catalog:band-measure', family: 'measuring', title: 'Band measure',
      how: { tool: 'run_capability', args: { capability: 'band-measure' } }, generated: true, catalog: 'band-measure',
      fields: [bands] })]), runCapability);
    expect(rows.map((r) => [r.id, r.group, r.tool])).toEqual([['catalog:band-measure', 'measuring', 'run_capability']]);
    expect(rows[0].fields).toEqual([bands]);
    expect(rows[0].target).toBeNull();
    expect(argsFor(rows[0], { bands: '6' }, null)).toEqual({ bands: 6 });
    expect(argsFor(rows[0], {}, null)).toEqual({}); // left empty: its default, said as the field's placeholder
    expect(blocked(rows[0], {}, null)).toBeNull();
  });

  test('is not offered while the agent has no run_capability here', () => {
    expect(actionRows(wire([cap({ id: 'catalog:x', family: 'measuring', how: { tool: 'run_capability' },
      generated: true, fields: [] })]), tools)).toEqual([]);
  });
});

test('Actions forwards the exact selected retained original separately from scientific settings', async () => {
  const { ActionsPanel } = await import('./actions-panel');
  const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
  const { vi } = await import('vitest');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  try {
    const root = dom.document.createElement('section');
    const reference = { session: 's1', id: 'd2', component: 'picture-0', revision: 'a'.repeat(64) };
    const run = vi.fn(async () => ({ ok: true }));
    let listed = [{ title: 'Derived original', reference, compatibility: { compatible: false, says: 'Class labels cannot be used as intensity.' } }];
    const inputResources = vi.fn(async () => ({ inputs: listed }));
    const panel = new ActionsPanel(root as unknown as HTMLElement, {
      run, stop: async () => ({ ok: true }), selected: () => null, session: () => 's1',
      openModels: () => {}, openActivity: () => {}, openTable: () => {}, openCamera: () => {}, cameraPage: () => '/camera/',
      inputResources,
    });
    panel.setTools({ run_capability: { kind: 'effect', family: 'seeing', params: { type: 'object' } } });
    const data = wire([cap({ id: 'catalog:mean', family: 'measuring', title: 'Mean', generated: true, fields: [],
      catalog: 'mean', how: { tool: 'run_capability', args: { capability: 'mean' } } })]);
    data.models = [tool('mean', { newKind: { operation: 'mean', input: 'image.2d.rgb8', output: 'table.objects', gives: 'a table', proved: true } })];
    panel.setCapabilities(data); fire(root.querySelector('[data-action="catalog:mean"]')!.querySelector('.action-words')!, 'click');
    // On names the Stage's picture; its Change opens the saved inputs it can take instead.
    fire(root.querySelector('.action-input')!.querySelector('.run-change')!, 'click');
    fire(root.querySelectorAll('button').find(n => n.textContent === 'Find retained results')!, 'click');
    await settleMicrotasks(); await settleMicrotasks();
    const select = root.querySelector('.action-retained-input')!;
    select.value = JSON.stringify(reference); fire(select, 'change');
    expect(inputResources).toHaveBeenCalledWith('s1', 'mean');
    expect(root.textContent).toContain('Class labels cannot be used as intensity.');
    listed = [];
    fire(root.querySelectorAll('button').find(n => n.textContent === 'Find retained results')!, 'click');
    await settleMicrotasks(); await settleMicrotasks();
    expect(root.querySelector('.action-retained-input')!.value).toBe(JSON.stringify(reference));
    await panel.start(panel.rowsShown()[0]);
    expect(run).toHaveBeenCalledWith('catalog:mean', {}, {}, expect.any(String), reference);
  } finally { vi.unstubAllGlobals(); }
});

test('Actions offers one saved resource per typed input, only the ones it takes, and forwards them as inputs', async () => {
  const { ActionsPanel } = await import('./actions-panel');
  const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
  const { vi } = await import('vitest');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  try {
    const root = dom.document.createElement('section');
    const labels = { kind: 'array' as const, session: 's1', id: 'pa', revision: 2, component: 'array.labels' };
    const intensity = { kind: 'array' as const, session: 's1', id: 'pb', revision: 2, component: 'array.intensity' };
    const run = vi.fn(async () => ({ ok: true }));
    const choice = (title: string, reference: typeof labels, fits: boolean, says: string) =>
      ({ title, reference, shape: [3, 4], dtype: '<u2', fits, says });
    const inputResources = vi.fn(async () => ({ inputs: [], typed: [
      { port: 'labels', type: 'labels.instance.2d', choices: [choice('Cells', labels, true, 'takes it'), choice('Signal', intensity, false, 'it means intensity; labels.instance.2d takes instance-labels')] },
      { port: 'intensity', type: 'image.2d.gray16', choices: [choice('Cells', labels, false, 'it means instance-labels'), choice('Signal', intensity, true, 'takes it')] }] }));
    const panel = new ActionsPanel(root as unknown as HTMLElement, {
      run, stop: async () => ({ ok: true }), selected: () => null, session: () => 's1',
      openModels: () => {}, openActivity: () => {}, openTable: () => {}, openCamera: () => {}, cameraPage: () => '/camera/',
      inputResources,
    });
    panel.setTools({ run_capability: { kind: 'effect', family: 'seeing', params: { type: 'object' } } });
    const data = wire([cap({ id: 'catalog:label-means', family: 'measuring', title: 'Label means', generated: true, fields: [],
      catalog: 'label-means', how: { tool: 'run_capability', args: { capability: 'label-means' } } })]);
    data.models = [tool('label-means', { newKind: { operation: 'label-means', input: 'labels.instance.2d', output: 'table.objects',
      gives: 'a table', proved: true, typed: true, inputs: [{ id: 'labels', type: 'labels.instance.2d' }, { id: 'intensity', type: 'image.2d.gray16' }] } })];
    panel.setCapabilities(data); fire(root.querySelector('[data-action="catalog:label-means"]')!.querySelector('.action-words')!, 'click');
    expect(root.querySelector('.action-retained-input')).toBeNull();  // never the view's picture
    fire(root.querySelectorAll('button').find(n => n.textContent === 'Find saved inputs')!, 'click');
    await settleMicrotasks(); await settleMicrotasks();
    expect(root.textContent).toContain('it means intensity; labels.instance.2d takes instance-labels');
    const [first, second] = root.querySelectorAll('.action-typed-input');
    first.value = JSON.stringify(labels); fire(first, 'change');
    await panel.start(panel.rowsShown()[0]);
    expect(run).not.toHaveBeenCalled();
    expect(root.textContent).toContain('Choose the intensity above before running.');
    const again = root.querySelectorAll('.action-typed-input')[1];
    again.value = JSON.stringify(intensity); fire(again, 'change');
    await panel.start(panel.rowsShown()[0]);
    expect(run).toHaveBeenCalledWith('catalog:label-means', {}, {}, expect.any(String), undefined, undefined,
      { labels, intensity });
    void second;
  } finally { vi.unstubAllGlobals(); }
});

test('Actions requires and forwards a selected Project volume separately from parameters', async () => {
  const { ActionsPanel } = await import('./actions-panel');
  const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
  const { vi } = await import('vitest');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  try {
    const root = dom.document.createElement('section');
    const run = vi.fn(async () => ({ok:true}));
    const panel = new ActionsPanel(root as unknown as HTMLElement, {
      run, stop: async()=>({ok:true}), selected:()=>null, session:()=> 's1',
      openModels:()=>{}, openActivity:()=>{}, openTable:()=>{}, openCamera:()=>{}, cameraPage:()=>'/camera/',
      inputVolumes: async()=> [{id:'v1',kind:'volume',day:'s1',title:'Selected volume',state:'ready',summary:''}],
    });
    panel.setTools({run_capability:{kind:'effect',family:'seeing',params:{type:'object'}}});
    const data = wire([cap({id:'catalog:count',family:'measuring',title:'Count',generated:true,fields:[],
      catalog:'count',how:{tool:'run_capability',args:{capability:'count'}}})]);
    data.models = [tool('count',{newKind:{operation:'count',input:'volume.3d',output:'table.objects',gives:'a table',proved:true}})];
    panel.setCapabilities(data);
    await panel.start(panel.rowsShown()[0]);
    expect(run).not.toHaveBeenCalled();
    fire(root.querySelectorAll('button').find(n=>n.textContent==='Find project volumes')!, 'click');
    await settleMicrotasks(); await settleMicrotasks();
    const select = root.querySelector('.action-volume-input')!;
    select.value='v1'; fire(select,'change');
    await panel.start(panel.rowsShown()[0]);
    expect(run).toHaveBeenCalledWith('catalog:count', {}, {}, expect.any(String), undefined, 'v1');
  } finally {vi.unstubAllGlobals();}
});

test('opening local Try pins its sole available model across later default changes', async () => {
  const { ActionsPanel } = await import('./actions-panel');
  const { syntheticDOM } = await import('./workspace-windows.test-support');
  const { vi } = await import('vitest');
  const dom = syntheticDOM(), create = dom.document.createElement;
  dom.document.createElement = (tag: string) => Object.assign(create(tag), { focus: vi.fn(), scrollIntoView: vi.fn() });
  vi.stubGlobal('document', dom.document);
  try {
    const root = dom.document.createElement('section');
    const run = vi.fn(async () => ({ ok: true }));
    const panel = new ActionsPanel(root as unknown as HTMLElement, {
      run, stop: async () => ({ ok: true }), selected: () => null, session: () => 's1',
      openModels: () => {}, openActivity: () => {}, openTable: () => {}, openCamera: () => {}, cameraPage: () => '/camera/',
    });
    panel.setTools({ group_cells: { kind: 'effect', family: 'measuring', params: { type: 'object', properties: {} } } });
    const capability = cap({ id: 'group-cells', family: 'measuring', title: 'Group cells', how: { tool: 'group_cells' }, roles: ['embed'] });
    const initial = role({ id: 'embed', current: 'dinov2-small', options: [
      { id: 'dinov2-small', label: 'DINOv2', note: '', available: true, runs: 'mac' },
    ] });
    panel.setCapabilities(wire([capability], [initial]));
    panel.open('group-cells', { role: 'embed', option: 'dinov2-small' });
    // The With row names the model picked for this run, certain.
    const withName = () => root.querySelector('.run-with')?.querySelector('.run-val-title')?.textContent;
    expect(withName()).toBe('DINOv2');
    panel.setCapabilities(wire([capability], [role({ ...initial, current: 'alternative', options: [
      ...initial.options, { id: 'alternative', label: 'Alternative', note: '', available: true, runs: 'mac' },
    ] })]));
    expect(withName()).toBe('DINOv2'); // a later change of the default never changes the model picked here
    const { fire } = await import('./workspace-windows.test-support');
    // What it runs on is the page's own item (its ⋯; a quiet link where the page sits under its row).
    expect(root.querySelector('.action-powers')).toBeNull();
    fire(root.querySelector('.run-links')!.querySelectorAll('button').find((b) => b.textContent === 'What it runs on')!, 'click');
    expect(root.querySelector('.action-powers')?.textContent).toContain('DINOv2');
    expect(root.querySelector('.action-powers')?.textContent).not.toContain('Alternative');
    await panel.start(panel.rowsShown()[0]);
    expect(run).toHaveBeenCalledWith('group-cells', {}, { embed: 'dinov2-small' }, expect.any(String));
    expect(() => panel.open('group-cells', { role: 'embed', option: 'missing' })).toThrow(/exact model.*no longer/);
    // Change opens the models in place; picking one closes the list and the page follows it.
    fire(root.querySelector('.run-with')!.querySelector('.run-change')!, 'click');
    fire(root.querySelectorAll('.mrow').find((n) => n.dataset.option === 'alternative')!, 'click');
    expect(root.querySelector('.run-models')).toBeNull();
    expect(withName()).toBe('Alternative');
    expect(root.querySelector('.action-powers')?.textContent).toContain('Alternative');
    expect(root.querySelector('.action-powers')?.textContent).not.toContain('DINOv2');
    // A model picked after a run puts the page back to ready: its primary names the model chosen.
    expect(root.querySelector('.action-go')?.textContent).toBe('Run Alternative');
    expect(initial.current).toBe('dinov2-small');
  } finally { vi.unstubAllGlobals(); }
});

test('closest guesses are labelled as guesses, and the panel says so for the workspace (coordinator review of #363)', async () => {
  const { ActionsPanel } = await import('./actions-panel');
  const { syntheticDOM, fire } = await import('./workspace-windows.test-support');
  const { vi } = await import('vitest');
  const dom = syntheticDOM();
  vi.stubGlobal('document', dom.document);
  try {
    const root = dom.document.createElement('section') as unknown as HTMLElement;
    const panel = new ActionsPanel(root, {
      run: async () => ({ ok: true }), stop: async () => ({ ok: true }),
      selected: () => null, session: () => 's1', openModels: () => {}, openActivity: () => {}, openTable: () => {},
      openCamera: () => {}, cameraPage: () => '/camera/',
    });
    panel.setTools(tools);
    panel.setCapabilities(wire([cap({ id: 'cells', title: 'Outline every cell', what: 'A cell finder segments every cell; they are kept and measured',
      how: { tool: 'outline_cells', say: 'outline all the cells' } }), cap({ id: 'clear', title: 'Clear outlines', how: { tool: 'clear' } })]));
    const search = (root as unknown as { querySelector(s: string): { value: string } }).querySelector('.actions-search') as unknown as Parameters<typeof fire>[0] & { value: string };
    const note = () => (root as unknown as { querySelector(s: string): { textContent: string } | null }).querySelector('.actions-partial');
    search.value = 'segment cells in zebrafish'; fire(search, 'input');
    expect(note()?.textContent).toBe('Closest matches: none does all of “segment cells in zebrafish”.');
    expect(root.dataset.taskPartial).toBe('true');
    search.value = 'segment cells'; fire(search, 'input');
    expect(note()).toBeNull();
    expect(root.dataset.taskPartial).toBe('false');
  } finally {
    vi.unstubAllGlobals();
  }
});

describe('Tools: models named on every task, and the Models page’s entries one to one', () => {
  const finder = role({ id: 'cells', title: 'Cell finder', current: 'cellpose-sam', default: 'cellpose-sam', options: [
    { id: 'cellpose-sam', label: 'Cellpose-SAM', said: 'Cellpose-SAM (on this Mac)', note: '', available: true, runs: 'mac' },
    { id: 'micro-sam-lm', label: 'micro-sam (light microscopy)', note: '', available: true, runs: 'mac' },
    { id: 'fiji-analyze-particles', label: 'Analyze Particles (Fiji)', note: '', available: false, why: 'Set up Fiji first' },
  ] });
  const entry = (id: string, name: string, option: string, status: 'ready' | 'needs-setup' = 'ready') => ({
    id, name, runs: 'this-mac', where: 'on this Mac', what: `${name} does it`, kind: 'model', version: '', credit: {}, link: '', licence: '',
    tier: '', mine: false, group: status === 'ready' ? 'ready' : 'available', inUse: false, chosen: false, facts: [], size: 0, files: [],
    folder: '', revision: '', status, word: status === 'ready' ? 'Ready' : 'Set up Fiji first', next: null, steps: [],
    usedFor: [{ role: 'cells', title: 'Cell finder', option, options: [], current: option === 'cellpose-sam', fixed: false }],
    abilities: [{ id: 'cells', title: 'Outline every cell', what: '', actor: 'agent', say: '', role: 'cells', option, current: option === 'cellpose-sam' }],
  });
  async function panelWith() {
    const { ActionsPanel } = await import('./actions-panel');
    const support = await import('./workspace-windows.test-support');
    const { vi } = await import('vitest');
    const dom = support.syntheticDOM(), create = dom.document.createElement;
    dom.document.createElement = (tag: string) => Object.assign(create(tag), { focus: vi.fn(), scrollIntoView: vi.fn() });
    vi.stubGlobal('document', dom.document);
    vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
    const root = dom.document.createElement('section');
    const deps = { run: vi.fn(async () => ({ ok: true })), stop: async () => ({ ok: true }), selected: () => null, session: () => 's1',
      openModels: vi.fn(), openActivity: () => {}, openTable: () => {}, openCamera: () => {}, cameraPage: () => '/camera/' };
    const panel = new ActionsPanel(root as unknown as HTMLElement, deps);
    panel.setTools(tools);
    panel.setCapabilities({ ...wire([cap({ id: 'cells', title: 'Outline every cell', how: { tool: 'outline_cells' }, roles: ['cells'] })], [finder]),
      models: [entry('cellpose-sam', 'Cellpose-SAM', 'cellpose-sam'), entry('micro-sam-lm', 'micro-sam (light microscopy)', 'micro-sam-lm'),
        entry('fiji-analyze-particles', 'Analyze Particles (Fiji)', 'fiji-analyze-particles', 'needs-setup')] } as unknown as CapabilitiesWire);
    return { panel, root, deps, fire: support.fire, vi };
  }
  type Node = { textContent: string; className: string; querySelector(s: string): Node | null; querySelectorAll(s: string): Node[]; dataset: Record<string, string> };

  test('a task row names the model it will use beside its lit cell, and "+n more"; its page lists every model in place', async () => {
    const { panel, root, deps, fire, vi } = await panelWith();
    try {
      const row = () => (root as unknown as Node).querySelector('[data-action="cells"]')!;
      const sub = row().querySelector('.trow-sub')!;
      expect(sub.querySelector('b')!.textContent).toBe('Cellpose-SAM'); // the model it will use, named
      expect(sub.textContent).toContain('+2 more');
      expect(row().querySelector('.tools-chip')).toBeNull(); // no chip walls
      fire(row().querySelector('.trow-hit') as unknown as Parameters<typeof fire>[0], 'click');
      expect(row().querySelector('.action-go')!.textContent).toBe('Run Cellpose-SAM');
      // Change opens the task's models in place, ready first, those to set up too, each with what it does here.
      fire(row().querySelector('.run-with')!.querySelector('.run-change') as unknown as Parameters<typeof fire>[0], 'click');
      const list = row().querySelector('.run-models')!;
      expect(list.querySelectorAll('.mrow').map((n) => n.querySelector('b')!.textContent)).toEqual(['Cellpose-SAM', 'micro-sam (light microscopy)', 'Analyze Particles (Fiji)']);
      expect(list.querySelectorAll('.run-models-h').map((n) => n.textContent)).toEqual(['Ready', 'Can be set up']);
      expect(list.querySelectorAll('.mrow')[1].querySelector('.mrow-what')!.textContent).toBe('micro-sam (light microscopy) does it');
      fire(list.querySelectorAll('.mrow')[1] as unknown as Parameters<typeof fire>[0], 'click');
      expect(row().querySelector('.action-go')!.textContent).toBe('Run micro-sam (light microscopy)');
      // Its card in Models is one click away (the page's ⋯ in the workspace; a quiet link where the page sits in a row).
      fire(row().querySelectorAll('button').find((b) => b.textContent === 'micro-sam (light microscopy) in Models') as unknown as Parameters<typeof fire>[0], 'click');
      expect(deps.openModels).toHaveBeenLastCalledWith('micro-sam-lm');
      await panel.start(panel.rowsShown()[0]);
      expect(deps.run).toHaveBeenCalledWith('cells', {}, { cells: 'micro-sam-lm' }, expect.any(String));
    } finally { vi.unstubAllGlobals(); }
  });

  test('By model lists the Models page’s entries in its sections and counts; a ready one is used here, another opens its card', async () => {
    const { panel, root, deps, fire, vi } = await panelWith();
    try {
      panel.setMode('model');
      const within = (part: string, selector: string) => (root as unknown as Node).querySelector(part)!.querySelectorAll(selector);
      expect(within('.tools-ready', '.trow-title').map((n) => n.textContent)).toEqual(['Cellpose-SAM', 'micro-sam (light microscopy)']);
      expect(within('.tools-ready', '.mm-num').map((n) => n.textContent)).toEqual(['2']);
      expect(within('.tools-setup', '.trow-title').map((n) => n.textContent)).toEqual(['Analyze Particles (Fiji)']);
      // Not ready, nothing it can do here yet: its card in Models.
      fire(within('.tools-setup', '.trow-hit')[0] as unknown as Parameters<typeof fire>[0], 'click');
      expect(deps.openModels).toHaveBeenLastCalledWith('fiji-analyze-particles');
      // Used right there: a ready model opens the first task it serves, with it chosen.
      fire(within('.tools-ready', '.trow-hit')[1] as unknown as Parameters<typeof fire>[0], 'click');
      expect(panel.viewMode).toBe('task');
      expect((root as unknown as Node).querySelector('[data-action="cells"]')!.querySelector('.action-go')!.textContent).toBe('Run micro-sam (light microscopy)');
    } finally { vi.unstubAllGlobals(); }
  });

  test('an opened tool says what is on the Stage, as the header names it, and follows it', async () => {
    const { panel, root, fire, vi } = await panelWith();
    try {
      const row = () => (root as unknown as Node).querySelector('[data-action="cells"]')!;
      fire(row().querySelector('.action-words') as unknown as Parameters<typeof fire>[0], 'click');
      const on = () => row().querySelector('.action-input')!;
      expect(on().querySelector('.run-val-title')!.textContent).toBe('What is on the Stage'); // nothing named before the Stage is known
      panel.setInput('Human cells · David Root (CC0)');
      expect(on().querySelector('.run-key')!.textContent).toBe('On');
      expect(on().querySelector('.run-val-title')!.textContent).toBe('Human cells');
      expect((on() as unknown as HTMLElement).title).toBe('On the Stage: Human cells · David Root (CC0)');
      panel.setInput('Live · iPhone 17 Pro');
      expect(on().querySelector('.run-val-title')!.textContent).toBe('Live · iPhone 17 Pro'); // the camera's words whole, never cut to one
      expect((on() as unknown as HTMLElement).title).toBe('On the Stage: Live · iPhone 17 Pro');
    } finally { vi.unstubAllGlobals(); }
  });

  test('green means a status only: each model’s lit cell says its state, the chosen one is pressed, no status pills', async () => {
    const { root, fire, vi } = await panelWith();
    try {
      const row = () => (root as unknown as Node).querySelector('[data-action="cells"]')!;
      const state = (n: Node) => (n.querySelector('[data-kind="lit-cell"]') as unknown as { getAttribute(name: string): string }).getAttribute('data-state');
      expect(state(row().querySelector('.trow-sub')!)).toBe('ready'); // the model it will use can run: green
      fire(row().querySelector('.action-words') as unknown as Parameters<typeof fire>[0], 'click');
      fire(row().querySelector('.run-with')!.querySelector('.run-change') as unknown as Parameters<typeof fire>[0], 'click');
      const rows = row().querySelector('.run-models')!.querySelectorAll('.mrow');
      expect(rows.map((r) => state(r))).toEqual(['ready', 'ready', 'idle']); // Analyze Particles is not set up: a grey outline
      expect((rows[0] as unknown as { getAttribute(name: string): string }).getAttribute('aria-pressed')).toBe('true');
      expect(row().querySelector('.tools-status')).toBeNull();
    } finally { vi.unstubAllGlobals(); }
  });

  test('a task that compares offers Compare models; the folded section is gone', async () => {
    const { root, fire, vi } = await panelWith();
    try {
      expect((root as unknown as Node).querySelectorAll('details').filter((d) => d.className === 'model-comparison')).toEqual([]);
      const row = () => (root as unknown as Node).querySelector('[data-action="cells"]')!;
      fire(row().querySelector('.action-words') as unknown as Parameters<typeof fire>[0], 'click');
      // In the workspace Compare… is in the page's ⋯; where the page sits under its row it is a quiet link.
      fire(row().querySelectorAll('button').find((b) => b.textContent === 'Compare models…') as unknown as Parameters<typeof fire>[0], 'click');
      expect(row().querySelector('.model-comparison')!.textContent).toContain('Models to compare (up to 3)');
      expect(row().querySelector('.run-foot')).toBeNull(); // one run button at a time: Compare's
    } finally { vi.unstubAllGlobals(); }
  });
});

test('Cells action forwards an exact native revision through the ordinary input picker', async () => {
  const { ActionsPanel } = await import('./actions-panel');
  const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
  const { vi } = await import('vitest');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  try {
    const root = dom.document.createElement('section');
    const reference = { kind: 'array' as const, session: 's1', id: 'p1', component: 'array.image', revision: 2 };
    const run = vi.fn(async () => ({ ok: true }));
    let listed = [{ title: 'Derived original', reference, compatibility: { compatible: false, says: 'Class labels cannot be used as intensity.' } }];
    const inputResources = vi.fn(async () => ({ inputs: listed }));
    const panel = new ActionsPanel(root as unknown as HTMLElement, {
      run, stop: async () => ({ ok: true }), selected: () => null, session: () => 's1',
      openModels: () => {}, openActivity: () => {}, openTable: () => {}, openCamera: () => {}, cameraPage: () => '/camera/',
      inputResources,
    });
    panel.setTools({ run_capability: { kind: 'effect', family: 'seeing', params: { type: 'object' } } });
    const data = wire([cap({ id: 'catalog:mean', family: 'measuring', title: 'Mean', generated: true, fields: [],
      catalog: 'mean', how: { tool: 'run_capability', args: { capability: 'mean' } } })]);
    data.models = [tool('mean', { newKind: { operation: 'cells', input: 'image.2d.rgb8', output: 'labels.instance.2d', gives: 'labels', proved: true } })];
    panel.setCapabilities(data); fire(root.querySelector('[data-action="catalog:mean"]')!.querySelector('.action-words')!, 'click');
    // On names the Stage's picture; its Change opens the saved inputs it can take instead.
    fire(root.querySelector('.action-input')!.querySelector('.run-change')!, 'click');
    fire(root.querySelectorAll('button').find(n => n.textContent === 'Find retained results')!, 'click');
    await settleMicrotasks(); await settleMicrotasks();
    const select = root.querySelector('.action-retained-input')!;
    select.value = JSON.stringify(reference); fire(select, 'change');
    expect(inputResources).toHaveBeenCalledWith('s1', 'mean');
    expect(root.textContent).toContain('Class labels cannot be used as intensity.');
    listed = [];
    fire(root.querySelectorAll('button').find(n => n.textContent === 'Find retained results')!, 'click');
    await settleMicrotasks(); await settleMicrotasks();
    expect(root.querySelector('.action-retained-input')!.value).toBe(JSON.stringify(reference));
    await panel.start(panel.rowsShown()[0]);
    expect(run).toHaveBeenCalledWith('catalog:mean', {}, {}, expect.any(String), reference);
  } finally { vi.unstubAllGlobals(); }
});

describe('the run footer says Stop and loading in one line each', () => {
  test('after Stop: Stopped with what it kept, from the server\'s Kept list; just Stopped when no list came', async () => {
    const { stoppedWords } = await import('./actions-panel');
    const stop = (kept: string[], listed?: boolean) => ({ ok: true, stopped: true, stop: { outcome: 'stopped', words: 'Stopped “Cells” (you pressed Stop). Kept: …', kept, lost: [], carriesOn: [], ...(listed === undefined ? {} : { listed }) } });
    expect(stoppedWords(stop([]))).toBe('Stopped · nothing kept');
    expect(stoppedWords(stop(['e12', 'e13']))).toBe('Stopped · kept 2');
    expect(stoppedWords(stop([], false))).toBe('Stopped'); // only words came: the list is not known here
    expect(stoppedWords({ ok: true, stopped: true })).toBe('Stopped');
  });
  test('while loading: its model with where it runs, then how long it usually takes, one pair of parentheses', async () => {
    const { loadLine } = await import('./actions-panel');
    expect(loadLine({ kind: 'model', text: 'Loading Cellpose-SAM (on this Mac) (usually about 3.5 s)', ref: { model: 'Cellpose-SAM (on this Mac)' } }))
      .toBe('Loading Cellpose-SAM (on this Mac) · usually about 3.5 s');
    expect(loadLine({ kind: 'model', text: 'Loading micro-sam for the first time', ref: { model: 'micro-sam' } })).toBe('Loading micro-sam · the first time on this Mac');
    expect(loadLine({ kind: 'model', text: 'Loading Bright objects', ref: { model: 'Bright objects' } })).toBe('Loading Bright objects');
    expect(loadLine({ kind: 'click', text: 'You clicked', ref: {} })).toBe('You clicked');
  });
});

test('file tools are discoverable by people while staying out of live-view dispatch', async () => {
  const {workflowTools}=await import('./actions-panel');
  const file=cap({id:'catalog:response-fit:you',actor:'you',title:'Fit response regression',how:{href:'#workflows?capability=response-fit'}});
  const w=wire([file,{...file,id:'catalog:response-fit',actor:'agent'},
    {...file,id:'unsafe',how:{href:'https://external.example/run'}}]);
  expect(workflowTools(w,'regression')).toEqual([file]);
  expect(workflowTools(w,'unmatched')).toEqual([]);
  expect(actionRows(w,tools)).toEqual([]);
});
