// The run page's one spec (run-spec.ts), against the real wire's shapes: run-spec.fixture.json is the capabilities
// message and the tool schemas of an isolated Hooke (no keys, nothing downloaded), trimmed to the entries these tests
// read: Cellpose-SAM, its fine-tuned weights, micro-sam, EdgeTAM, Bright objects, Analyze Particles and Gaussian Blur
// (Fiji), InstanSeg, Gemini 3.8 Flash, SAM 3.1 (cloud) and SAM 2.1 tiny. Only fields the entries declare show.
import { describe, expect, test } from 'vitest';
import { actionRows, type ActionRow, type ToolList } from './actions-panel';
import type { CapabilitiesWire, Capability } from './protocol';
import { appliesTo, entrySpec, implementsTask, modelSection, optionEntry, orderServed, outcomeWords, runSpec, runStep, runnableWords, taskChoices, tookWords, type RunTask } from './run-spec';
import { modelTasks } from './tools-catalog';
import fixture from './run-spec.fixture.json';

const base = fixture as unknown as CapabilitiesWire & { tools: ToolList };
const clone = (): CapabilitiesWire => structuredClone(base);
function rowsOf(wire: CapabilitiesWire): { rows: ActionRow[]; map: Map<string, RunTask> } {
  const rows = actionRows(wire, base.tools);
  return { rows, map: new Map(rows.map((r) => [r.id, r])) };
}
const task = (wire: CapabilitiesWire, id: string) => rowsOf(wire).rows.find((r) => r.id === id)!;

describe('a built-in model: Cellpose-SAM outlines every cell', () => {
  test('the task names it as the model in use, says what it does here, and declares no settings of its own', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const spec = runSpec(wire, task(wire, 'cells'), '', map);
    expect(spec.title).toBe('Outline every cell');
    expect(spec.chosen?.name).toBe('Cellpose-SAM');
    expect(spec.chosen?.where).toBe('On this Mac · free');
    expect(spec.what).toBe('Outlines every cell in a view'); // its entry's own words: the option is the entry itself
    expect(spec.makes).toBe('Outlines'); // the cells role's operation returns polygons
    expect(spec.inputs.map((i) => i.kind)).toEqual(['stage']);
    expect(spec.fields).toEqual([]); // nothing shown as Settings…
    // …and no More options: the task's one optional field (Compare with the ImageJ macro) is Analyze Particles' alone.
    expect(spec.advanced).toEqual([]);
    expect(spec.run).toEqual({ capability: 'cells', use: {} }); // the default runs: no model chosen for this run
  });

  test('a field for some models only (contract/actions.json `for`) shows while one of them is chosen, never for another', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const fiji = runSpec(wire, task(wire, 'cells'), 'fiji-analyze-particles', map);
    expect(fiji.advanced.map((f) => [f.key, f.label])).toEqual([['compare_reference', 'Compare with the ImageJ macro']]);
    expect(runSpec(wire, task(wire, 'cells'), 'micro-sam-lm', map).advanced).toEqual([]);
    expect(appliesTo({ for: ['fiji-analyze-particles'] }, { option: 'cellpose-sam', model: 'cellpose-sam' })).toBe(false);
    expect(appliesTo({}, null)).toBe(true);
  });

  test('the model list is in Models’ sections, each model with its one step and one size', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const choices = taskChoices(wire, task(wire, 'cells'), map);
    // This isolated Hooke has checked and run no model yet, so the ready ones are Ready, not checked yet; then those that
    // can be set up.
    expect(choices.map((c) => [c.name, c.section])).toEqual([
      ['Cellpose-SAM', 'unchecked'], ['micro-sam (light microscopy)', 'unchecked'], ['EdgeTAM automatic masks', 'unchecked'],
      ['Bright objects', 'unchecked'],
      ['Cellpose-SAM fine-tuned on your corrections', 'setup'], ['Analyze Particles (Fiji)', 'setup'], ['InstanSeg (brightfield nuclei)', 'setup'],
    ]);
    const by = (name: string) => choices.find((c) => c.name === name)!;
    expect(by('Analyze Particles (Fiji)').step).toMatchObject({ label: 'Set up Fiji', does: 'sheet', model: 'fiji' });
    // The weights step has no fulfiller here: its reason, and no dead-end button.
    expect(by('Cellpose-SAM fine-tuned on your corrections').step).toBeNull();
    expect(by('Cellpose-SAM fine-tuned on your corrections').reason).toBe('Made from your corrections');
    // A kind not set up yet is listed with its whole setup when a download is not all of it (an install follows), its
    // size in the server's SI words (16 MB, not 15.1 MB).
    expect(by('InstanSeg (brightfield nuclei)').step).toMatchObject({ label: 'Set up InstanSeg (brightfield nuclei)', short: 'Set up', does: 'sheet', model: 'instanseg-brightfield' });
    expect(by('InstanSeg (brightfield nuclei)').setup).toBe('16 MB, then its own Python (35 packages)');
    expect(by('InstanSeg (brightfield nuclei)').runnable).toBe(false);
    // What each does for THIS task: EdgeTAM's automatic masks are not "Outlines what you click".
    expect(by('EdgeTAM automatic masks').ability).toBe('Every distinct thing from a grid of EdgeTAM prompts, ~1 s; not benchmarked on cells yet');
    expect(by('InstanSeg (brightfield nuclei)').ability).toBe('Outlines every nucleus on a brightfield slide');
  });

  test('a download under way fills from the measured bytes only', () => {
    const wire = clone();
    const entry = wire.models!.find((m) => m.id === 'instanseg-brightfield')!;
    entry.download = { model: entry.id, name: entry.name, state: 'downloading', got: 6_000_000, total: 15_792_317, says: 'Downloading' };
    const choice = taskChoices(wire, task(wire, 'cells'), rowsOf(wire).map).find((c) => c.model === entry.id)!;
    expect(choice.download).toEqual({ got: 6_000_000, total: 15_792_317, state: 'downloading', says: 'Downloading' });
    expect(choice.cell).toBe('setup');
  });
});

describe('a format-2 entry listed as a task of its own: Bright objects (Find bright regions)', () => {
  test('chosen in Outline every cell too, it shows its own declared fields, says what it makes in its words, and Run runs its own capability', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const spec = runSpec(wire, task(wire, 'cells'), 'basic-bright-objects', map);
    expect(spec.chosen?.name).toBe('Bright objects');
    expect(spec.chosen?.where).toBe('Built in · free');
    expect(spec.fields.map((f) => [f.key, f.label, f.min, f.max, f.unit])).toEqual([
      ['sigma', 'Smoothing', 0.1, 5, 'px'], ['min_area', 'Smallest object', 1, 1000000, 'px²']]);
    expect(spec.advanced.map((f) => f.label)).toEqual(['Joined when']);
    expect(spec.run).toEqual({ capability: 'catalog:basic-bright-objects', use: {} });
    // Its output's own label and note follow it into Outline every cell: never "Outlines" of cells (blob detection is
    // not recognition).
    expect(spec.makes).toBe('Bright regions (not cells)');
    expect(spec.note).toBe('Bright regions are not a cell count');
    expect(spec.inputs.map((i) => i.kind)).toEqual(['picture']);
    expect(spec.what).toBe('Finds bright regions using a basic local threshold method');
  });

  test('its own capability is its own task row (contract/actions.json ownTasks): Find bright regions', () => {
    const wire = clone(); const { rows, map } = rowsOf(wire);
    const own = rows.find((r) => r.id === 'catalog:basic-bright-objects')!;
    expect(own.title).toBe('Find bright regions');
    expect(implementsTask(wire, own, rows)).toBeUndefined();
    const spec = runSpec(wire, own, '', map);
    expect(spec.title).toBe('Find bright regions');
    expect([spec.chosen?.name, spec.chosen?.where]).toEqual(['Bright objects', 'Built in · free']);
    expect(spec.fields.map((f) => f.label)).toEqual(['Smoothing', 'Smallest object']);
    expect([spec.makes, spec.note]).toEqual(['Bright regions (not cells)', 'Bright regions are not a cell count']);
  });

  test('an entry not listed as a task of its own is still a model of the job it is a choice of (one page for one thing)', () => {
    const wire = clone(); const { rows } = rowsOf(wire);
    const fiji = rows.find((r) => r.id === 'catalog:fiji-analyze-particles')!;
    expect(fiji.title).toBe('Analyze Particles (Fiji)');
    expect(implementsTask(wire, fiji, rows)?.id).toBe('cells');
  });

  test('Use it here opens its own task first, then Outline every cell with it chosen', () => {
    const wire = clone(); const { rows } = rowsOf(wire);
    const entry = wire.models!.find((m) => m.id === 'basic-bright-objects')!;
    const served = orderServed(wire, entry.id, modelTasks(entry, rows));
    expect(served.map((t) => [t.task, t.choice])).toEqual([['catalog:basic-bright-objects', null], ['cells', { role: 'cells', option: 'basic-bright-objects' }]]);
  });
});

describe('a Fiji function: Gaussian Blur (Fiji)', () => {
  test('not set up, its page names its step and shows only the settings its folder declares, locked until then', () => {
    const wire = clone();
    const spec = entrySpec(wire, wire.models!.find((m) => m.id === 'fiji-gaussian-blur')!);
    expect(spec.title).toBe('Gaussian Blur (Fiji)');
    expect(spec.chosen?.step).toMatchObject({ label: 'Set up Fiji', model: 'fiji' });
    expect(spec.fields.map((f) => [f.key, f.label, f.min, f.max, f.unit])).toEqual([['sigma', 'Sigma (radius)', 0.5, 20, 'px']]);
    expect(spec.none).toBe('');
    expect(spec.locked).toBe('Set up Fiji to change these');
    // With none declared on the wire, nothing is invented.
    const bare = clone();
    const entry = bare.models!.find((m) => m.id === 'fiji-gaussian-blur')!;
    delete entry.newKind!.fields;
    const none = entrySpec(bare, entry);
    expect(none.fields).toEqual([]);
    expect(none.none).toBe('its settings show once it is set up');
    expect(none.locked).toBe('');
    expect(spec.makes).toBe('Blurred picture'); // its output's own label
    expect(spec.inputs.map((i) => i.kind)).toEqual(['picture']);
  });

  test('set up, its declared Sigma (0.5 to 20 px) is the one setting', () => {
    const wire = clone();
    const entry = wire.models!.find((m) => m.id === 'fiji-gaussian-blur')!;
    Object.assign(entry, { status: 'ready', group: 'ready', next: null, steps: [], face: { state: 'on', says: 'On', action: null, also: [] } });
    entry.newKind = { ...entry.newKind!, proved: true };
    const sigma = { key: 'sigma', label: 'Sigma (radius)', kind: 'number' as const, required: false, hint: 'How far the smoothing reaches',
      choices: [], said: {}, numeric: true, initial: 2, min: 0.5, max: 20, step: 0.5, unit: 'px', advanced: false };
    wire.capabilities.push({ id: 'catalog:fiji-gaussian-blur', family: 'seeing', actor: 'agent', title: 'Gaussian Blur (Fiji)',
      what: "Smooths the view with ImageJ's Gaussian Blur", how: { tool: 'run_capability', args: { capability: 'fiji-gaussian-blur' } },
      roles: [], available: true, why: '', verdict: null, generated: true, catalog: 'fiji-gaussian-blur',
      runsOn: 'Gaussian Blur (Fiji): ImageJ, in the app’s own Fiji on this Mac', fields: [sigma] } as Capability);
    const { map } = rowsOf(wire);
    const spec = runSpec(wire, map.get('catalog:fiji-gaussian-blur')!, '', map);
    expect(spec.chosen?.name).toBe('Gaussian Blur (Fiji)');
    expect(spec.chosen?.section).toBe('ready');
    expect(spec.chosen?.cell).toBe('ready');
    expect(spec.fields).toEqual([sigma]);
    expect(spec.none).toBe('');
    expect(spec.run).toEqual({ capability: 'catalog:fiji-gaussian-blur', use: {} });
  });
});

describe('a cloud model with a key: Gemini 3.8 Flash and SAM 3.1 (cloud)', () => {
  test('Count them names the model, where the picture goes and the key it needs', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const spec = runSpec(wire, task(wire, 'count'), '', map);
    expect(spec.chosen?.name).toBe('Gemini 3.8 Flash');
    expect(spec.chosen?.cloud).toBe('Google');
    expect(spec.chosen?.where).toBe('Cloud · needs your Google key');
    expect(spec.chosen?.section).toBe('needs'); // your choice, and it can't run: Needs you, as Models says
    expect(spec.chosen?.step).toMatchObject({ label: 'Add key', does: 'sheet', model: 'gemini-3.8-flash' });
    expect(spec.fields.map((f) => f.key)).toEqual(['label']); // what to count: the tool's own required field
    expect(spec.makes).toBe('An answer');
  });

  test('once the key is there it says "your Google key" and needs no step', () => {
    const wire = clone();
    wire.setup!.keys!.GEMINI_API_KEY.present = true;
    const role = wire.roles.find((r) => r.id === 'vision')!;
    for (const o of role.options) if (o.id === 'gemini-3.8-flash') { o.available = true; delete o.readiness; }
    const spec = runSpec(wire, task(wire, 'count'), '', rowsOf(wire).map);
    expect(spec.chosen?.where).toBe('Cloud · your Google key');
    expect(spec.chosen?.runnable).toBe(true);
    expect(spec.chosen?.step).toBeNull();
  });

  test('Find all like this runs its fixed model: named, with its key step, no choice', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const spec = runSpec(wire, task(wire, 'find-like'), '', map);
    expect(spec.choices).toHaveLength(1);
    expect(spec.chosen?.name).toBe('SAM 3.1 (cloud)');
    expect(spec.chosen?.step?.label).toBe('Add key');
    // Its step is the person's (their key): the needs cell, as Gemini's, whichever section Models lists it in.
    expect([spec.chosen?.cell, spec.chosen?.cellWord]).toEqual(['needs', 'Needs you']);
    expect(spec.inputs.map((i) => [i.kind, i.required])).toEqual([['outline', true]]); // like this one: an outline is needed
    expect(spec.makes).toBe('Outlines'); // its matches are kept as a population, like every page's Makes line
  });
});

describe('a model with two tasks: micro-sam', () => {
  const clickTask = (wire: CapabilitiesWire): RunTask & { runWith: { role: string; options: { id: string }[] } } => {
    const click = wire.capabilities.find((c) => c.id === 'click-outline')!;
    const role = wire.roles.find((r) => r.id === 'outline')!;
    return { id: click.id, title: click.title, what: click.what, fields: [], target: null, available: click.available, why: click.why,
      step: null, chooses: 'outline', query: false, model: null, gesture: 'click',
      runWith: { role: 'outline', options: role.options.filter((o) => o.available).map((o) => ({ id: o.id })) } };
  };

  test('it serves Outline every cell and Click to outline; the first opens', () => {
    const wire = clone(); const { rows } = rowsOf(wire);
    const entry = wire.models!.find((m) => m.id === 'micro-sam-lm')!;
    const tasks = [...rows, clickTask(wire)];
    const served = orderServed(wire, entry.id, modelTasks(entry, tasks).filter((t) => t.choice));
    expect(served.map((t) => t.title)).toEqual(['Outline every cell', 'Click to outline']);
  });

  test('a click-driven task takes clicks on the Stage; EdgeTAM, its current model, opens there first', () => {
    const wire = clone(); const { rows, map } = rowsOf(wire);
    const click = clickTask(wire);
    const spec = runSpec(wire, click, 'micro-sam-lm', map);
    expect(spec.clicks).toBe(true);
    expect(spec.inputs.map((i) => i.kind)).toEqual(['stage', 'clicks']);
    expect(spec.chosen?.name).toBe('micro-sam (light microscopy)');
    expect(spec.run.use).toEqual({ outline: 'micro-sam-lm' });
    const edgetam = wire.models!.find((m) => m.id === 'edgetam')!;
    const tasks = [...rows, click];
    expect(orderServed(wire, 'edgetam', modelTasks(edgetam, tasks).filter((t) => t.choice))[0].task).toBe('click-outline');
  });

  test('a model not checked on this Mac carries its check (a click trial needs a passed check first)', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    const spec = runSpec(wire, clickTask(wire), 'sam2.1-tiny', map);
    expect(spec.chosen?.section).toBe('unchecked');
    expect(spec.chosen?.check).toMatchObject({ label: 'Check it here', does: 'sheet', model: 'sam2.1-tiny' });
  });
});

describe('words', () => {
  test('sections follow the server: needs-you, then the face state (one meaning of Ready)', () => {
    expect(modelSection({ group: 'ready', status: 'ready', face: { state: 'on' } })).toBe('ready');
    expect(modelSection({ group: 'available', status: 'ready', face: { state: 'unchecked' } })).toBe('unchecked');
    expect(modelSection({ group: 'needs-you', status: 'needs-setup', face: { state: 'needs-setup' } })).toBe('needs');
    expect(modelSection({ group: 'unavailable', status: 'unavailable', face: { state: 'unavailable' } })).toBe('away');
    expect(modelSection({ group: 'available', status: 'needs-setup', face: { state: 'needs-setup' } })).toBe('setup');
  });

  test('step verbs say what happens', () => {
    expect(runStep({ kind: 'download', says: 'Download 413 MB', model: 'micro-sam-lm', bytes: 413_000_000 }).step?.label).toBe('Download 413 MB');
    expect(runStep({ kind: 'download', says: 'Download', model: 'x', bytes: 1_288_490_189 }).step?.label).toBe('Download 1.3 GB');
    expect(runStep({ kind: 'install', says: 'Set up Fiji first', model: 'fiji' }).step?.label).toBe('Set up Fiji');
    expect(runStep({ kind: 'install', says: 'Install Spot measure' }, 'spot-measure').step).toMatchObject({ label: 'Install Spot measure', short: 'Install', model: 'spot-measure' });
    expect(runStep({ kind: 'key', says: 'Add your fal key', service: 'fal' }, 'sam3.1-cloud').step).toMatchObject({ label: 'Add key', model: 'sam3.1-cloud' });
    expect(runStep({ kind: 'weights', says: 'Make its weights here' })).toEqual({ step: null, reason: 'Made from your corrections' });
  });

  test('an outcome says what the model found, in the server’s own count and noun', () => {
    expect(outcomeWords('Bright objects', 'Outlined and measured 271 bright regions with Bright objects · Basic local method. In pixels: set the objective for µm.'))
      .toEqual({ title: 'Bright objects found 271 bright regions', rest: 'In pixels: set the objective for µm.' });
    expect(outcomeWords('Cellpose-SAM', 'Found no cells with Cellpose-SAM. It ran on the CPU, the reference device.'))
      .toEqual({ title: 'Cellpose-SAM found no cells', rest: 'It ran on the CPU, the reference device.' });
    expect(outcomeWords('Bright objects', 'Bright objects · Basic local method found 12 outlines on the view now, kept as e3 (Bright regions), measured; the layer is on.'))
      .toEqual({ title: 'Bright objects found 12 outlines', rest: '' }); // the agent's own words after it (ids, tool names) stay in Activity
    expect(outcomeWords('Gemini 3.8 Flash', 'There are about 40 nuclei.')).toEqual({ title: 'There are about 40 nuclei.', rest: '' });
    expect(tookWords(2600)).toBe('2.6 s');
    expect(tookWords(72_000)).toBe('1:12');
  });
});


// Ticket 67, fix 4: "Ready" meant two things (the small-models offer and the run page said Ready while Models and Tools
// said Not checked). Models' sections are the one meaning: Ready once Hooke's own check passed here or a real use of it
// worked here (Will, 2026-10-29); ready to run with neither is Ready · not checked yet.
describe('one meaning of Ready', () => {
  const cellpose = () => structuredClone(base.models!.find((m) => m.id === 'cellpose-sam')!);
  test('a model that can run but is not checked here is said that way, wherever a word is needed', () => {
    const row = cellpose();
    row.face = { state: 'unchecked', says: 'Ready to run; not checked on this Mac yet', action: null, also: [] };
    expect(runnableWords({ ...row, size: 1_000 })).toBe('Downloaded · not checked yet');
    expect(runnableWords({ ...row, runs: 'cloud', size: 0 })).toBe('Ready · not checked yet');
    row.face = { state: 'on', says: 'On · last check: works', action: null, also: [] };
    expect(runnableWords(row)).toBe('Ready');
    expect(runnableWords(undefined)).toBe('Ready'); // no catalog row to disagree with
    const wire = clone();
    expect(optionEntry(wire, { id: 'cellpose-sam' })?.id).toBe('cellpose-sam');
    expect(optionEntry(wire, { id: 'auto', models: ['cellpose-sam'] })?.id).toBe('cellpose-sam');
  });
  test('a runnable choice whose model Models files under Can be set up is never called Ready on the run page', () => {
    const wire = clone(); const { map } = rowsOf(wire);
    wire.models!.find((m) => m.id === 'cellpose-sam')!.face = { state: 'needs-setup', says: 'Its adapter needs a functional check', action: null, also: [] };
    const choice = taskChoices(wire, task(wire, 'cells'), map).find((c) => c.model === 'cellpose-sam')!;
    expect(choice.runnable).toBe(true);
    expect([choice.section, choice.cellWord]).toEqual(['setup', 'Not set up']);
  });
});
