// Tools and Models are one list (tools-catalog.ts; docs/design/2026-10-20-hooke-words.md, "Tool or model?"): the entries
// are the wire's `models`, one to one; a task's models come from its role and the entries' abilities; a comparison's
// line is facts only; its colours are used nowhere else.
import { readdirSync, readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { afterEach, describe, expect, test, vi } from 'vitest';
import { COMPARE_COLOURS, countsLine, lastUsed, madeByName, modelTasks, rememberUse, taskModels, toolEntries, trustQuestion } from './tools-catalog';
import type { CapabilitiesWire, ModelRow, Role } from './protocol';

const entry = (over: Partial<ModelRow> & { id: string }): ModelRow => ({
  name: over.id, runs: 'this-mac', where: 'on this Mac', what: '', kind: 'model', version: '', credit: {}, link: '', licence: '',
  tier: '', mine: false, group: 'ready', inUse: false, chosen: false, facts: [], usedFor: [], size: 0, files: [], folder: '',
  revision: '', status: 'ready', word: 'Ready', next: null, steps: [], abilities: [], ...over,
} as ModelRow);
const cells: Role = { id: 'cells', kind: 'model', title: 'Cell finder', purpose: '', current: 'cellpose-sam', default: 'cellpose-sam',
  options: [
    { id: 'cellpose-sam', label: 'Cellpose-SAM', note: '', available: true, runs: 'mac' },
    { id: 'micro-sam-lm', label: 'micro-sam (light microscopy)', note: '', available: true, runs: 'mac' },
    { id: 'fiji-analyze-particles', label: 'Analyze Particles (Fiji)', note: '', available: false, why: 'Set up Fiji first' },
    { id: 'off', label: 'Off', note: '', available: true },
  ], powers: [], agentCanChange: [] } as unknown as Role;
const vision: Role = { id: 'vision', kind: 'model', title: 'Vision', purpose: '', current: 'gemini', options: [
  { id: 'gemini', label: 'Gemini 3.8 Flash', note: '', available: true, runs: 'cloud' }], powers: [], agentCanChange: [] } as unknown as Role;
const ability = (id: string, role: string, option: string, current = true) => ({ id, title: id, what: '', actor: 'agent', say: '', role, option, current });
const models = [
  entry({ id: 'cellpose-sam', name: 'Cellpose-SAM', what: 'Outlines every cell in a view', usedFor: [{ role: 'cells', title: '', option: 'cellpose-sam', options: [], current: true, fixed: false }],
    abilities: [ability('cells', 'cells', 'cellpose-sam'), ability('find-cells', 'cells', 'cellpose-sam')] }),
  entry({ id: 'micro-sam-lm', name: 'micro-sam (light microscopy)', what: 'Outlines cells, trained on light microscopy',
    usedFor: [{ role: 'cells', title: '', option: 'micro-sam-lm', options: [], current: false, fixed: false }],
    abilities: [ability('cells', 'cells', 'micro-sam-lm', false), ability('find-cells', 'cells', 'micro-sam-lm', false)] }),
  entry({ id: 'fiji-analyze-particles', name: 'Analyze Particles (Fiji)', status: 'needs-setup', group: 'available', word: 'Set up Fiji first',
    usedFor: [{ role: 'cells', title: '', option: 'fiji-analyze-particles', options: [], current: false, fixed: false }],
    abilities: [ability('cells', 'cells', 'fiji-analyze-particles', false)] }),
  entry({ id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', runs: 'cloud', usedFor: [{ role: 'vision', title: '', option: 'gemini', options: [], current: true, fixed: false }],
    abilities: [ability('find-cells', 'vision', 'gemini')] }),
  entry({ id: 'basic-bright-objects', name: 'Bright objects', status: 'ready' }),
  entry({ id: 'fiji-gaussian-blur', name: 'Gaussian Blur (Fiji)', status: 'unavailable', group: 'unavailable', word: 'Not in this edition' }),
];
const wire = { roles: [cells, vision], models, capabilities: [], setup: { edition: '', options: {},
  news: { new: [{ key: 'mine/lab-counter', name: 'Lab counter', what: 'Counts colonies' }], last: {}, automatic: false } } } as unknown as CapabilitiesWire;

afterEach(() => vi.unstubAllGlobals());

describe('one list in two places', () => {
  test('Tools lists exactly the Models page’s entries, ready first, those to set up and New ones below', () => {
    const tools = toolEntries(wire);
    expect(tools.ready.map((m) => m.id)).toEqual(['cellpose-sam', 'micro-sam-lm', 'gemini-3.8-flash', 'basic-bright-objects']);
    expect(tools.setup.map((m) => m.id)).toEqual(['fiji-analyze-particles']);
    expect(tools.unavailable.map((m) => m.id)).toEqual(['fiji-gaussian-blur']);
    expect(tools.news.map((n) => n.key)).toEqual(['mine/lab-counter']);
    // One to one: every entry of Models once, none of Tools' own.
    expect([...tools.ready, ...tools.setup, ...tools.unavailable].map((m) => m.id).sort()).toEqual(models.map((m) => m.id).sort());
    expect(toolEntries(null)).toEqual({ ready: [], setup: [], unavailable: [], news: [] });
  });
});

describe('a task names its models', () => {
  test('its own choice’s models, the one in use first, then ready ones, then those to set up; Off is no model', () => {
    const named = taskModels(wire, 'cells', 'cells');
    expect(named.map((m) => [m.name, m.model, m.ready, m.choosable])).toEqual([
      ['Cellpose-SAM', 'cellpose-sam', true, true],
      ['micro-sam (light microscopy)', 'micro-sam-lm', true, true],
      ['Analyze Particles (Fiji)', 'fiji-analyze-particles', false, true],
    ]);
    expect(named[2].why).toBe('Set up Fiji first');
  });

  test('the one last used for that job comes first; one that can’t run is never put first', () => {
    expect(taskModels(wire, 'cells', 'cells', 'micro-sam-lm')[0].option).toBe('micro-sam-lm');
    expect(taskModels(wire, 'cells', 'cells', 'fiji-analyze-particles')[0].option).toBe('cellpose-sam');
  });

  test('a model of another job the task also uses is named as it is now, never offered to swap', () => {
    const named = taskModels(wire, 'find-cells', 'cells');
    expect(named.at(-1)).toMatchObject({ name: 'Gemini 3.8 Flash', role: 'vision', choosable: false, model: 'gemini-3.8-flash' });
  });

  test('a catalog entry’s own task is that entry', () => {
    expect(taskModels(wire, 'catalog:basic-bright-objects', null)).toEqual([expect.objectContaining({ model: 'basic-bright-objects', name: 'Bright objects', choosable: false })]);
  });

  test('a model lists the tasks it can do here, each picking it at once when the task chooses among models', () => {
    const tasks = [
      { id: 'cells', title: 'Outline every cell', available: true, runWith: { role: 'cells', options: [{ id: 'cellpose-sam' }, { id: 'micro-sam-lm' }] } },
      { id: 'find-cells', title: 'Pick from every cell', available: true, runWith: { role: 'cells', options: [{ id: 'cellpose-sam' }] } },
      { id: 'catalog:basic-bright-objects', title: 'Bright objects', available: true, runWith: null },
    ];
    expect(modelTasks(models[1], tasks)).toEqual([{ task: 'cells', title: 'Outline every cell', choice: { role: 'cells', option: 'micro-sam-lm' } }]);
    expect(modelTasks(models[3], tasks)).toEqual([{ task: 'find-cells', title: 'Pick from every cell', choice: null }]); // used, not chosen
    expect(modelTasks(models[4], tasks)).toEqual([{ task: 'catalog:basic-bright-objects', title: 'Bright objects', choice: null }]);
  });

  test('a Result names the model that made it', () => {
    expect(madeByName(wire, { operation: 'cells', implementation: 'micro-sam-lm' })).toBe('micro-sam (light microscopy)');
    expect(madeByName(wire, { tool: 'snapshot' })).toBe('');
  });

  test('the model last chosen is remembered in this browser only, and storage that fails is no error', () => {
    const saved = new Map<string, string>();
    vi.stubGlobal('localStorage', { getItem: (k: string) => saved.get(k) ?? null, setItem: (k: string, v: string) => saved.set(k, v) });
    rememberUse('cells', 'micro-sam-lm');
    expect(lastUsed('cells')).toBe('micro-sam-lm');
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } });
    expect(lastUsed('cells')).toBeNull();
    expect(() => rememberUse('cells', 'x')).not.toThrow();
  });
});

describe('Use it here: the tasks a model serves (modelTasks), ordered by run-spec’s orderServed', () => {
  test('a click-driven task is served too, and the task where the model is the current choice comes first', async () => {
    const { orderServed } = await import('./run-spec');
    const outline = { id: 'outline', kind: 'model', title: 'Outlining', purpose: '', current: 'edgetam', options: [
      { id: 'edgetam', label: 'EdgeTAM', note: '', available: true, runs: 'mac' }], powers: [], agentCanChange: [] } as unknown as Role;
    const edgetam = entry({ id: 'edgetam', name: 'EdgeTAM', abilities: [ability('cells', 'cells', 'edgetam-automask', false), ability('click-outline', 'outline', 'edgetam')] });
    const tasks = [
      { id: 'cells', title: 'Outline every cell', available: true, runWith: { role: 'cells', options: [{ id: 'cellpose-sam' }, { id: 'edgetam-automask' }] } },
      { id: 'click-outline', title: 'Click to outline', available: true, runWith: { role: 'outline', options: [{ id: 'edgetam' }] } },
    ];
    const served = modelTasks(edgetam, tasks);
    expect(served.map((t) => t.task)).toEqual(['cells', 'click-outline']);
    const ordered = orderServed({ ...wire, roles: [cells, vision, outline] } as CapabilitiesWire, 'edgetam', served);
    expect(ordered.map((t) => t.task)).toEqual(['click-outline', 'cells']); // EdgeTAM is the clicks' current model
  });
});

describe('a comparison says facts; the Assistant judges', () => {
  test('one line on why the counts differ, from how each works, never which is right', () => {
    const line = countsLine([
      { name: 'Cellpose-SAM', count: 341, how: 'Outlines every cell in a view' },
      { name: 'Bright objects', count: 271, how: 'Finds bright regions using a threshold' },
      { name: 'micro-sam', failed: 'out of memory', how: 'x' },
    ]);
    expect(line).toBe('The counts differ because each model finds them its own way: Cellpose-SAM outlines every cell in a view; '
      + 'Bright objects finds bright regions using a threshold.');
    expect(countsLine([{ name: 'A', count: 4, how: '' }, { name: 'B', count: 4, how: '' }])).toBe('They found the same number (4), each its own way: A works its own way; B works its own way.');
    expect(countsLine([{ name: 'A', count: 4, how: '' }])).toBe('');
    expect(trustQuestion('Outline every cell', [{ name: 'A', count: 4 }, { name: 'B', count: 6 }]))
      .toBe('I compared models for “Outline every cell” on the same picture (A. A: 4, B. B: 6). Which should I trust here, and why?');
  });

  test('each compared model’s colour is used for nothing else in the studio', () => {
    const here = fileURLToPath(new URL('.', import.meta.url));
    const files = readdirSync(here, { recursive: true }).map(String).filter((f) => /\.(ts|css|html)$/.test(f) && !f.endsWith('.test.ts'));
    for (const colour of COMPARE_COLOURS) {
      const users = files.filter((f) => readFileSync(`${here}/${f}`, 'utf8').toLowerCase().includes(colour));
      expect(users).toEqual(['tools-catalog.ts']);
    }
    expect(new Set(COMPARE_COLOURS).size).toBe(3);
  });
});
