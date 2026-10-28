// The small-models offer (starter-models.ts): its rows from the capabilities wire, the default rule read from the wire,
// the total the button names, the edition hiding Cellpose-SAM, measured progress, and the answer (Not now, Download)
// over a synthetic DOM; and that the Welcome and Models mount it where the brief puts it.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { STARTERS, WORDS, chosenBytes, pickedWords, configureStarterModels, downloadLabel, modelsFolder, noteCapabilities, offerShown,
  starterOffer, starterRows, ticksByDefault, type StarterAnswer, type StarterCommands } from './starter-models';
import { settleMicrotasks, syntheticDOM, TestElement } from './workspace-windows.test-support';
import type { CapabilitiesWire, ModelRow, Role } from './protocol';

const FOLDER = '/Users/someone/MicroscopeMind-Beta/catalog/models';
const model = (over: Partial<ModelRow>): ModelRow => ({
  id: 'x', name: 'X', runs: 'this-mac', where: 'on this Mac', what: '', kind: 'model', version: '', credit: {}, link: '',
  licence: '', tier: 'open', mine: false, status: 'needs-setup', word: '', group: 'available', next: null, steps: [],
  inUse: false, chosen: false, facts: [], usedFor: [], size: 0, files: [], folder: '', revision: '', ...over,
});
const download = (id: string, bytes: number) => ({ kind: 'download' as const, says: `Download ${bytes}`, model: id, bytes });
const use = (role: string, option: string) => ({ role, title: role, option, options: [], current: true, fixed: false });
const role = (id: string, def: string, powers: string[]): Role => ({ id, kind: 'model', title: id, purpose: '', current: def,
  default: def, options: [], powers, agentCanChange: [] });

/** The fresh-Mac wire of 2026-10-26 (the isolated instance with no model caches), cut to the five offered entries. */
function freshWire(over: { roles?: Role[]; models?: ModelRow[]; edition?: string } = {}): CapabilitiesWire {
  const models = over.models ?? [
    model({ id: 'basic-bright-objects', name: 'Bright objects', what: 'Finds bright regions using a basic local threshold method',
      kind: 'tool-function', runs: 'cpu', status: 'ready', usedFor: [use('cells', 'basic-bright-objects')] }),
    model({ id: 'cellpose-sam', name: 'Cellpose-SAM', what: 'Outlines every cell in a view', tier: 'noncommercial',
      size: 1_233_586_851, steps: [download('cellpose-sam', 1_233_586_851)], usedFor: [use('cells', 'cellpose-sam')],
      folder: `${FOLDER}/cellpose-sam` }),
    model({ id: 'dinov2-small', name: 'DINOv2 small', what: 'Turns a picture into numbers that compare how things look',
      size: 88_250_943, steps: [download('dinov2-small', 88_250_943)], usedFor: [use('embed', 'dinov2-small')], folder: `${FOLDER}/dinov2-small` }),
    model({ id: 'edgetam', name: 'EdgeTAM', what: 'Outlines what you click or box, fast enough for every click', size: 55_953_586,
      steps: [download('edgetam', 55_953_586)], usedFor: [use('outline', 'edgetam'), use('cells', 'edgetam-automask')], folder: `${FOLDER}/edgetam` }),
    model({ id: 'instanseg-brightfield', name: 'InstanSeg (brightfield nuclei)', what: 'Outlines every nucleus on a brightfield slide',
      size: 15_792_317, steps: [download('instanseg-brightfield', 15_792_317), { kind: 'install', says: 'Install InstanSeg (brightfield nuclei)', model: 'instanseg-brightfield' }],
      folder: `${FOLDER}/instanseg-brightfield` }),
  ];
  return {
    type: 'capabilities', families: [], actors: { you: 'You', agent: 'I', auto: 'I, in the background', researcher: 'My researchers' },
    capabilities: [
      { id: 'click-outline', family: 'see', actor: 'you', title: '', what: '', how: {} },
      { id: 'outline-check', family: 'see', actor: 'auto', title: '', what: '', how: {} },
      { id: 'seen-auto', family: 'see', actor: 'auto', title: '', what: '', how: {} },
      { id: 'cells-button', family: 'see', actor: 'you', title: '', what: '', how: {} },
    ] as CapabilitiesWire['capabilities'],
    roles: over.roles ?? [role('outline', 'edgetam', ['click-outline', 'outline-check']), role('embed', 'dinov2-small', ['seen-auto']),
      role('cells', 'cellpose-sam', ['cells-button'])],
    preferences: [], pending: 0, build: '', agentText: '',
    setup: { edition: over.edition ?? 'research', options: {}, downloads: [], installs: [], disk: { freeBytes: 57_408_376_832, spareBytes: 1 << 30 } },
    models,
  };
}

describe('the rows, from the wire', () => {
  it('lists the starter entries in order, each in the manifest\'s words with readiness\'s size', () => {
    const rows = starterRows(freshWire());
    expect(rows.map((r) => r.id)).toEqual([...STARTERS]);
    expect(rows.map((r) => [r.name, r.size, r.ticked])).toEqual([
      ['Bright objects', 'Included', false],
      ['EdgeTAM', '56 MB', true],
      ['DINOv2 small', '88 MB', true],
      ['InstanSeg (brightfield nuclei)', '16 MB, plus its own software', false],
      ['Cellpose-SAM', '1.2 GB · non-commercial use only', false],
    ]);
    expect(rows[1].what).toBe('Outlines what you click or box, fast enough for every click');
    expect(rows.find((r) => r.id === 'instanseg-brightfield')!.how).toBe('turn-on'); // its own software: Turn on, not Download alone
    expect(rows.find((r) => r.id === 'edgetam')!.how).toBe('download');
  });

  it('ticks by the rule read from the wire, never a list', () => {
    const wire = freshWire();
    const edgetam = wire.models!.find((m) => m.id === 'edgetam')!;
    expect(ticksByDefault(edgetam, wire)).toBe(true);
    // Not the default of its role any more: unticked.
    expect(ticksByDefault(edgetam, freshWire({ roles: [role('outline', 'sam2.1-tiny', ['outline-check']), role('embed', 'dinov2-small', ['seen-auto'])] }))).toBe(false);
    // A role the workspace does not use by itself (only you run it): unticked.
    expect(ticksByDefault(edgetam, freshWire({ roles: [role('outline', 'edgetam', ['click-outline'])] }))).toBe(false);
    expect(ticksByDefault({ ...edgetam, tier: 'noncommercial' }, wire)).toBe(false); // open licence tier only
    expect(ticksByDefault({ ...edgetam, steps: [...edgetam.steps, { kind: 'install', says: 'Install' }] }, wire)).toBe(false); // no install
    expect(ticksByDefault({ ...edgetam, steps: [download('edgetam', 120e6)] }, wire)).toBe(false); // under 100 MB
    const cellpose = wire.models!.find((m) => m.id === 'cellpose-sam')!;
    expect(ticksByDefault(cellpose, wire)).toBe(false); // the cells default, but run only when you ask, 1.2 GB, non-commercial
  });

  it('names the total of what is ticked on the button', () => {
    const rows = starterRows(freshWire());
    const ticked = new Set(rows.filter((r) => r.ticked).map((r) => r.id));
    expect(chosenBytes(rows, ticked)).toBe(55_953_586 + 88_250_943);
    expect(downloadLabel(rows, ticked)).toBe('Download 144 MB');
    expect(downloadLabel(rows, new Set([...ticked, 'instanseg-brightfield']))).toBe('Download 160 MB');
    expect(downloadLabel(rows, new Set())).toBe('Download');
    expect(chosenBytes(rows, new Set(['basic-bright-objects']))).toBe(0); // included: nothing to download
    expect(pickedWords(rows, ticked)).toBe('EdgeTAM and DINOv2 small, 144 MB');
    expect(pickedWords(rows, new Set())).toBe('Nothing ticked: Choose picks what to download.');
  });

  it('leaves out what the edition excludes, and what is broken here', () => {
    const wire = freshWire();
    wire.models = wire.models!.map((m) => m.id === 'cellpose-sam'
      ? { ...m, status: 'unavailable', steps: [{ kind: 'edition', says: 'Not in the open edition' }] } as ModelRow
      : m.id === 'instanseg-brightfield' ? { ...m, status: 'broken' } as ModelRow : m);
    expect(starterRows(wire).map((r) => r.id)).toEqual(['basic-bright-objects', 'edgetam', 'dinov2-small']);
  });

  it('shows measured progress, then Ready, never a made-up percent', () => {
    const wire = freshWire();
    wire.setup!.downloads = [{ model: 'edgetam', name: 'EdgeTAM', state: 'downloading', got: 23e6, total: 55_953_586, says: 'Downloading EdgeTAM' },
      { model: 'dinov2-small', name: 'DINOv2 small', state: 'queued', got: 0, total: 88_250_943, says: '' }];
    const rows = starterRows(wire);
    expect(rows[1]).toMatchObject({ state: 'downloading', progress: { words: '23 of 56 MB' } });
    expect(rows[1].progress!.fraction).toBeCloseTo(23e6 / 55_953_586);
    expect(rows[2]).toMatchObject({ state: 'queued', progress: { fraction: null, words: 'Waiting to download' } });
    wire.models = wire.models!.map((m) => m.id === 'edgetam' ? { ...m, status: 'ready' } as ModelRow : m);
    expect(starterRows(wire)[1]).toMatchObject({ state: 'ready', size: 'Ready', ticked: false });
  });

  it('asks only while unanswered and something can be downloaded', () => {
    const rows = starterRows(freshWire());
    const open: StarterAnswer = { answered: false, answer: null, at: null, models: [] };
    expect(offerShown(rows, open)).toBe(true);
    expect(offerShown(rows, { ...open, answered: true, answer: 'not-now' })).toBe(false);
    expect(offerShown(rows, null)).toBe(false); // the answer not read yet: nothing is asked
    expect(offerShown(rows.map((r) => ({ ...r, state: 'ready' as const })), open)).toBe(false);
  });

  it('says where model files are kept', () => {
    expect(modelsFolder(freshWire())).toBe('~/MicroscopeMind-Beta/catalog/models');
  });
});

describe('the panel', () => {
  let commands: StarterCommands & { calls: string[] };
  let answer: StarterAnswer;
  beforeEach(() => {
    const dom = syntheticDOM();
    vi.stubGlobal('document', Object.assign(dom.document, { createElementNS: (_ns: string, tag: string) => new TestElement(tag) }));
    answer = { answered: false, answer: null, at: null, models: [] };
    const calls: string[] = [];
    commands = {
      calls,
      status: vi.fn(async () => answer),
      answer: vi.fn(async (a, models) => { calls.push(`answer ${a} ${models.join(',')}`); answer = { answered: true, answer: a, at: 'now', models }; return answer; }),
      download: vi.fn(async (m) => { calls.push(`download ${m}`); }),
      turnOn: vi.fn(async (m) => { calls.push(`turn-on ${m}`); }),
      reveal: vi.fn(async () => { calls.push('reveal'); }),
      models: vi.fn(() => { calls.push('models'); }),
    };
    configureStarterModels(commands);
    noteCapabilities(freshWire());
  });
  afterEach(() => vi.unstubAllGlobals());

  const node = (panel: unknown) => panel as unknown as TestElement;
  const button = (panel: unknown, words: string) =>
    node(panel).querySelectorAll('button').find((b) => b.textContent === words) ?? null;
  const click = (target: TestElement | null) => { expect(target).not.toBeNull(); target!.dispatchEvent(new Event('click')); };

  it('the Welcome shows the offer under the ways in: the words, the rows, the folder, the source and two buttons', async () => {
    const panel = node(starterOffer('welcome'));
    await settleMicrotasks();
    expect(panel.hidden).toBe(false);
    const text = panel.textContent;
    expect(text).toContain(WORDS.heading);
    expect(text).toContain(WORDS.lead);
    expect(text).toContain('Kept in ~/MicroscopeMind-Beta/catalog/models · 57.4 GB free · Show in Finder');
    expect(text).toContain('Downloaded from Hugging Face and GitHub. Downloading sends none of your pictures; these models run on this Mac.');
    const boxes = panel.querySelectorAll('input');
    expect(boxes.map((b) => [b.dataset.focus, (b as unknown as { checked: boolean }).checked])).toEqual([
      ['tick:edgetam', true], ['tick:dinov2-small', true], ['tick:instanseg-brightfield', false], ['tick:cellpose-sam', false]]);
    expect(button(panel, 'Download 144 MB')).not.toBeNull();
    expect(button(panel, 'Not now')).not.toBeNull();
    click(button(panel, 'Show in Finder'));
    expect(commands.calls).toEqual(['reveal']);
  });

  it('Not now keeps the answer, downloads nothing and collapses to one line', async () => {
    const panel = node(starterOffer('welcome'));
    await settleMicrotasks();
    click(button(panel, 'Not now'));
    await settleMicrotasks();
    expect(commands.calls).toEqual(['answer not-now ']);
    expect(commands.download).not.toHaveBeenCalled();
    expect(panel.textContent).toBe(WORDS.declined);
    click(button(panel, 'Models'));
    expect(commands.calls.at(-1)).toBe('models');
  });

  it('Download keeps the answer and gets the ticked models through the ordinary commands; the rows then follow the wire', async () => {
    const panel = node(starterOffer('welcome'));
    await settleMicrotasks();
    const instanseg = panel.querySelectorAll('input').find((b) => b.dataset.focus === 'tick:instanseg-brightfield')!;
    (instanseg as unknown as { checked: boolean }).checked = true;
    instanseg.dispatchEvent(new Event('change'));
    click(button(panel, 'Download 160 MB'));
    await settleMicrotasks(); await settleMicrotasks(); await settleMicrotasks();
    expect(commands.calls).toEqual(['answer download edgetam,dinov2-small,instanseg-brightfield', 'download edgetam',
      'download dinov2-small', 'turn-on instanseg-brightfield']);
    const wire = freshWire();
    wire.setup!.downloads = [{ model: 'edgetam', name: 'EdgeTAM', state: 'downloading', got: 23e6, total: 55_953_586, says: '' }];
    wire.models = wire.models!.map((m) => m.id === 'dinov2-small' ? { ...m, status: 'ready' } as ModelRow : m);
    noteCapabilities(wire);
    expect(panel.textContent).toContain(WORDS.adding);
    expect(panel.textContent).not.toContain('Cellpose-SAM'); // only what this answer started
    const rows = panel.querySelectorAll('li');
    expect(rows.map((r) => [r.dataset.starter, r.querySelector('.starter-offer-size')?.textContent])).toEqual([
      ['edgetam', '23 of 56 MB'], ['dinov2-small', 'Ready'], ['instanseg-brightfield', '16 MB, plus its own software']]);
    expect(panel.querySelectorAll('input')).toEqual([]); // no more ticking once answered
  });

  it('in Models it is one row until answered, its list behind Choose, and gone once answered', async () => {
    const panel = node(starterOffer('row'));
    await settleMicrotasks();
    expect(panel.textContent).toContain(WORDS.heading);
    expect(panel.textContent).toContain('EdgeTAM and DINOv2 small, 144 MB'); // what the button gets, with its list folded
    expect(panel.querySelectorAll('li')).toEqual([]);
    click(button(panel, 'Choose'));
    expect(panel.querySelectorAll('li').length).toBe(5);
    click(button(panel, 'Not now'));
    await settleMicrotasks();
    expect(panel.hidden).toBe(true);
  });

  it('an answered offer is never asked again, in any place', async () => {
    answer = { answered: true, answer: 'not-now', at: 'then', models: [] };
    configureStarterModels(commands);
    const places = (['welcome', 'stage', 'row'] as const).map((where) => node(starterOffer(where)));
    await settleMicrotasks();
    expect(places.map((p) => p.hidden)).toEqual([true, true, true]);
    expect(commands.answer).not.toHaveBeenCalled();
  });

  it('answered in one place, the others go quiet at once', async () => {
    const stage = node(starterOffer('stage')), row = node(starterOffer('row'));
    await settleMicrotasks();
    expect([stage.hidden, row.hidden]).toEqual([false, false]);
    click(button(stage, 'Not now'));
    await settleMicrotasks();
    noteCapabilities(freshWire()); // the next wire redraws every place
    expect(stage.textContent).toBe(WORDS.declined);
    expect(row.hidden).toBe(true);
  });
});

describe('where it is mounted', () => {
  const read = (name: string) => readFileSync(fileURLToPath(new URL(name, import.meta.url)), 'utf8');
  it('the first-launch Welcome puts it under the three ways in, before the quiet line', () => {
    expect(read('./workspace-study.ts')).toContain("page.append(head,ways,starterOffer('welcome'),quiet)");
  });
  it('the empty Stage, where a fresh profile lands, puts it under its ways in and the example', () => {
    expect(read('./session-home.ts')).toContain("root.replaceChildren(this.title, lead, pair, research, example, starterOffer('stage'), this.recent");
  });
  it('Models puts it at the top of the page, one element kept across redraws', () => {
    const page = read('./modelspage.ts');
    expect(page).toContain("if (wire) body.append(this.starter ??= starterOffer('row'));");
    expect(page.indexOf("starterOffer('row')")).toBeLessThan(page.indexOf('for (const [section, title] of MODEL_SECTIONS)'));
  });
  it('main.ts gives it every wire and the existing commands', () => {
    const main = read('./main.ts');
    expect(main).toContain('noteCapabilities(message);');
    expect(main).toContain("download: (model) => connection.request('model-download', { model }),");
    expect(main).toContain("turnOn: (model) => connection.request('model-turn-on', { model }),");
  });
});


// Ticket 67, fix 4: a model downloaded here whose Hooke check has not run was "Ready" in this offer and Not checked in Models.
describe('a downloaded model in the offer, as Models says it', () => {
  it('says Downloaded · not checked until Hooke has checked it, then Ready', () => {
    const wire = freshWire();
    const edgetam = wire.models!.find((m) => m.id === 'edgetam')!;
    Object.assign(edgetam, { status: 'ready', group: 'ready', steps: [], face: { state: 'unchecked', says: 'Ready to run; not checked on this Mac yet', action: null, also: [] } });
    const row = () => starterRows(wire).find((r) => r.id === 'edgetam')!;
    expect([row().state, row().size]).toEqual(['unchecked', 'Downloaded · not checked']);
    expect(row().ticked).toBe(false);
    edgetam.face = { state: 'on', says: 'On · last check: works', action: null, also: [] };
    expect([row().state, row().size]).toEqual(['ready', 'Ready']);
  });
});
