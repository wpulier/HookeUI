// A Models card's look and its one verb (model-status.ts `cardLook`; workspace-feel step 8): one card shape, one verb per
// state, and its section the one run-spec.ts gives Tools By model, so their counts agree.
import { describe, expect, it } from 'vitest';
import { cardFactsWords, cardKindWords, cardLook, faceState, type CardContext } from './model-status';
import { modelSection, sectionCell } from './run-spec';
import type { CapabilitiesWire, ModelRow, ToolSetup } from './protocol';

const row = (over: Partial<ModelRow> = {}): ModelRow => ({
  id: 'cellpose-sam', name: 'Cellpose-SAM', runs: 'this-mac', where: 'on this Mac', what: 'Outlines every cell in a view', kind: 'model',
  version: '1', credit: {}, link: '', licence: '', tier: 'open', mine: false, status: 'ready', word: 'Ready', group: 'available',
  next: null, steps: [], inUse: false, chosen: false, facts: [], usedFor: [], size: 0, files: [], folder: '', revision: '',
  face: { state: 'on', says: 'Its check passed on this Mac', action: null, also: [] }, ...over,
});
const keys = { FAL_KEY: { service: 'fal', name: 'fal.ai', short: 'fal', getKey: '', enables: '', present: false, source: null, sourceSays: '', canSave: true },
  GEMINI_API_KEY: { service: 'gemini', name: 'Google Gemini API', short: 'Google', getKey: '', enables: '', present: true, source: 'file' as const, sourceSays: '', canSave: true } };
const wire = (setup: Partial<NonNullable<CapabilitiesWire['setup']>> = {}): CapabilitiesWire => ({ type: 'capabilities', families: [],
  actors: { you: '', agent: '', auto: '', researcher: '' }, capabilities: [], roles: [], preferences: [], pending: 0, build: 't', agentText: '',
  setup: { edition: 'open', options: {}, keys, ...setup } });
const workspace = (served: number | null = 1): CardContext => ({ inWorkspace: true, served, shelf: true });
const plan = (steps: { kind: string; says: string; model?: string; by: 'app' | 'you' }[]): ToolSetup[] =>
  [{ role: 'cells', title: 'Cells', option: 'x', recheck: false, canTry: false, plan: { option: 'x', model: 'x', label: 'X', recipe: 'r', privacy: '', cost: '', steps } }];
const dl = (bytes: number) => ({ kind: 'download' as const, says: `Download ${Math.round(bytes / 1e6)} MB`, model: 'micro-sam', bytes });

describe('one verb per state', () => {
  it('a ready model that serves a task here says Use it here; one that serves none, or an atlas, says who uses it', () => {
    expect(cardLook(row(), wire(), workspace(1)).verb).toEqual({ kind: 'use' });
    expect(cardLook(row(), wire(), workspace(0)).verb).toEqual({ kind: 'note', words: 'Used by the Assistant and researchers' });
    expect(cardLook(row({ kind: 'data', face: { state: 'obtained', says: 'Files checked', action: null, also: [] } }), wire(), workspace(2)).verb)
      .toEqual({ kind: 'note', words: 'Used by the Assistant and researchers' });
    const look = cardLook(row(), wire(), workspace());
    expect([look.section, look.cell, look.cellWord, look.facts]).toEqual(['ready', 'ready', 'Ready', 'On this Mac · free']);
  });

  it('a model that ran here says Working here on its card, with when it last ran, and offers Use it here', () => {
    // Will, 2026-10-29: Claude Opus 5.5 answered him all session while its card said Not checked, grey.
    const claude = row({ id: 'claude-opus-5-5', name: 'Claude Opus 5.5', runs: 'cloud', where: 'in the cloud', kind: 'cloud-model', group: 'ready',
      face: { state: 'on', by: 'use', says: 'Working here · last ran 14:02', action: null, also: [{ kind: 'check', says: 'Check it here', model: 'claude-opus-5-5' }] } });
    const look = cardLook(claude, wire(), workspace(1));
    expect([look.section, look.cell, look.cellWord, look.facts, look.verb]).toEqual(['ready', 'ready', 'Working here', 'Working here · last ran 14:02', { kind: 'use' }]);
    expect(faceState(claude, undefined)).toMatchObject({ state: 'on', colour: 'green', word: 'Working here' });
    // A failure is said only from a recorded one (catalog_view: readiness.failed_in_use), with its reason.
    const failed = row({ status: 'broken', group: 'unavailable', face: { state: 'broken', says: 'Its last use here failed (14:05): connection failed',
      action: { kind: 'check', says: 'Its last use here failed (14:05): connection failed' }, also: [] } });
    expect(cardLook(failed, wire(), workspace(1))).toMatchObject({ cell: 'attention', cellWord: 'Needs attention', facts: 'Its last use here failed (14:05): connection failed' });
  });

  it('outside the workspace there is no run page: a ready card keeps its setup step', () => {
    expect(cardLook(row({ setups: plan([{ kind: 'check', says: 'Run its check', by: 'app' }]) }), wire(), { inWorkspace: false, served: null, shelf: true }).verb)
      .toEqual({ kind: 'sheet', label: 'Check it here' });
  });

  it('ready, not checked yet is its own section, said as Ready; its verb Check it here opens its check sheet, in the workspace too', () => {
    const r = row({ face: { state: 'unchecked', says: 'Not checked on this Mac', action: { kind: 'check', says: 'Check it here' }, also: [] },
      setups: plan([{ kind: 'check', says: 'Run its functional check', by: 'app' }]) });
    const look = cardLook(r, wire(), workspace());
    // It can run: a green outline and Ready, never the grey of a model that isn't there (Will, 2026-10-29); its check
    // (Run the check) stays the card's verb, never Details.
    expect([look.section, look.cell, look.cellWord, look.verb]).toEqual(['unchecked', 'partial', 'Ready · not checked yet', { kind: 'sheet', label: 'Check it here' }]);
    expect(faceState(r, undefined)).toMatchObject({ colour: 'green', word: 'Ready · not checked yet' });
    // Serving no task here, or outside the workspace, its check is its sheet.
    expect(cardLook(r, wire(), workspace(0)).verb).toEqual({ kind: 'sheet', label: 'Check it here' });
    expect(cardLook(r, wire(), { inWorkspace: false, served: null, shelf: true }).verb).toEqual({ kind: 'sheet', label: 'Check it here' });
    // A setup with more than its check (a download first) keeps its sheet and its first step's words.
    const more = row({ face: r.face, setups: plan([{ kind: 'download', says: 'Download 4 MB', by: 'app' }, { kind: 'check', says: 'Check', by: 'app' }]) });
    expect(cardLook(more, wire(), workspace()).verb).toEqual({ kind: 'sheet', label: 'Download and turn on' });
  });

  it('a download is Download 413 MB, started in place, and fills from measured bytes; a stopped one offers Retry', () => {
    const waiting = row({ id: 'micro-sam', status: 'needs-setup', word: 'Download 413 MB', next: dl(413e6), size: 413e6,
      face: { state: 'needs-setup', says: 'Download 413 MB', action: dl(413e6), also: [] } });
    const look = cardLook(waiting, wire(), workspace());
    expect(look.verb).toEqual({ kind: 'download', label: 'Download 413 MB', model: 'micro-sam' });
    expect([look.section, look.cell, look.facts]).toEqual(['setup', 'idle', '413 MB · free']);
    const going = cardLook({ ...waiting, download: { model: 'micro-sam', name: 'micro-sam', state: 'downloading', got: 173.6e6, total: 413e6, says: '' } }, wire(), workspace());
    expect(going.verb).toEqual({ kind: 'progress', words: 'Downloading · 42%', model: 'micro-sam' });
    expect([going.cell, going.fraction]).toEqual(['setup', 173.6e6 / 413e6]);
    const unknown = cardLook({ ...waiting, download: { model: 'micro-sam', name: 'micro-sam', state: 'downloading', got: 5e6, total: 0, says: '' } }, wire(), workspace());
    expect([unknown.verb, unknown.fraction]).toEqual([{ kind: 'progress', words: 'Downloading', model: 'micro-sam' }, null]); // never a percent made up
    const stopped = cardLook({ ...waiting, download: { model: 'micro-sam', name: 'micro-sam', state: 'failed', got: 173.6e6, total: 413e6, says: 'no connection' } }, wire(), workspace());
    expect(stopped.verb).toEqual({ kind: 'retry', download: 'micro-sam' });
    expect([stopped.cell, stopped.facts]).toEqual(['attention', 'Download stopped at 42% · no connection']);
  });

  it('a key or a licence is the person’s step, on its button: the blue cell only in Needs you, as Tools By model draws it', () => {
    const keyed = row({ id: 'sam3.1-cloud', runs: 'cloud', where: 'in the cloud', kind: 'cloud-model', status: 'needs-setup', group: 'needs-you',
      next: { kind: 'key', says: 'Add your fal.ai key', service: 'fal' }, facts: [{ kind: 'key', ok: false, says: '', service: 'fal' }],
      face: { state: 'needs-setup', says: 'Needs your fal.ai key', action: null, also: [] },
      usedFor: [{ role: 'fixed:find-all', title: 'Find all like this', option: 'model', options: [], current: true, fixed: false }] });
    const look = cardLook(keyed, wire(), workspace());
    expect([look.section, look.cell, look.verb, look.facts, look.kind]).toEqual(['needs', 'needs', { kind: 'direct' }, 'Your choice for find all like this', 'Cloud model, fal']);
    expect(cardLook({ ...keyed, setups: plan([{ kind: 'key', says: 'Add your fal.ai key', by: 'you' }]) }, wire(), workspace()).verb).toEqual({ kind: 'sheet', label: 'Add key' });
    const gated = row({ status: 'needs-setup', next: { kind: 'licence', says: 'Accept its licence', url: 'https://huggingface.co/x' },
      face: { state: 'needs-setup', says: 'Accept its licence', action: null, also: [] }, setups: plan([{ kind: 'licence', says: 'Accept its licence', by: 'you' }]) });
    expect(cardLook(gated, wire(), workspace())).toMatchObject({ section: 'setup', cell: 'idle', cellWord: 'Not set up', verb: { kind: 'sheet', label: 'Accept licence' } });
    // A key it needs while nobody chose it (Claude Opus 5.5, GPT-6 Luna): Can be set up, the grey outline, Add key on the button.
    const opus = row({ id: 'claude-opus-5-5', runs: 'cloud', where: 'in the cloud', kind: 'cloud-model', status: 'needs-setup', group: 'available',
      next: { kind: 'key', says: 'Add your Anthropic key', service: 'anthropic' }, facts: [{ kind: 'key', ok: false, says: '', service: 'anthropic' }],
      face: { state: 'needs-setup', says: 'Needs your Anthropic key', action: null, also: [] }, setups: plan([{ kind: 'key', says: 'Add your Anthropic key', by: 'you' }]) });
    const card = cardLook(opus, wire(), workspace());
    expect(card).toMatchObject({ section: 'setup', cell: 'idle', verb: { kind: 'sheet', label: 'Add key' } });
    // Models and Tools By model give the same row the same cell (actions-panel.ts drawModel: its section's cell).
    for (const r of [keyed, gated, opus]) expect([cardLook(r, wire(), workspace()).cell, cardLook(r, wire(), workspace()).cellWord], r.id)
      .toEqual([sectionCell(modelSection(r)).cell, sectionCell(modelSection(r)).word]);
  });

  it('an install the app performs says Set up Fiji; weights only fine-tuning makes say so with no button', () => {
    const blur = row({ id: 'fiji-gaussian-blur', kind: 'tool-function', status: 'needs-setup', next: { kind: 'install', says: 'Set up Fiji first', model: 'fiji' },
      face: { state: 'needs-setup', says: 'Set up Fiji first', action: null, also: [] }, setups: plan([{ kind: 'install', says: 'Set up Fiji first', model: 'fiji', by: 'app' }]) });
    expect(cardLook(blur, wire(), workspace())).toMatchObject({ verb: { kind: 'sheet', label: 'Set up Fiji' }, facts: 'Needs Fiji · free', kind: 'Method, on this Mac' });
    const tuned = row({ status: 'needs-setup', next: { kind: 'weights', says: 'Make its weights here' }, face: { state: 'needs-setup', says: 'Make its weights here', action: null, also: [] } });
    expect(cardLook(tuned, wire(), workspace())).toMatchObject({ verb: { kind: 'note', words: '' }, facts: 'Made from your corrections', cell: 'idle' });
  });

  it('a setup under way is Continue setup, its own words its facts; waiting for you is the needs cell; a failed one Retry', () => {
    const r = row({ face: { state: 'unchecked', says: '', action: null, also: [] }, setups: plan([{ kind: 'check', says: 'Run its check', by: 'app' }]) });
    const turning = (state: 'running' | 'waiting' | 'failed', says: string) => wire({ turning: { cells: { job: 'cells', option: 'x', label: 'X', model: 'x', state, says } } });
    expect(cardLook(r, turning('running', 'Downloading 12%'), workspace())).toMatchObject({ verb: { kind: 'continue' }, cell: 'working', cellWord: 'Setting up', facts: 'Downloading 12%' });
    expect(cardLook(r, turning('waiting', 'Waiting for your key'), workspace())).toMatchObject({ verb: { kind: 'continue' }, cell: 'needs' });
    expect(cardLook(r, turning('failed', 'it answered nothing'), workspace())).toMatchObject({ verb: { kind: 'retry', download: null }, cell: 'attention', facts: 'Didn’t finish: it answered nothing' });
  });

  it('a contribution waiting for its review keeps that review, never Use it here; not on this Mac offers nothing', () => {
    expect(cardLook(row({ execution: { action: 'compare', why: 'Compare your picture first.' } }), wire(), workspace()).verb).toEqual({ kind: 'execution' });
    const away = cardLook(row({ status: 'unavailable', group: 'unavailable', face: { state: 'unavailable', says: 'Not in this edition', action: null, also: [] } }), wire(), workspace());
    expect([away.section, away.cell, away.verb, away.facts]).toEqual(['away', 'stopped', { kind: 'none' }, 'Not in this edition']);
    const broken = cardLook(row({ status: 'broken', group: 'unavailable', face: { state: 'broken', says: 'Its last check failed', action: null, also: [] } }), wire(), workspace());
    expect([broken.cell, broken.cellWord]).toEqual(['attention', 'Needs attention']);
  });

  it('its section is always run-spec.ts modelSection, the one Tools By model counts', () => {
    const rows = [row(), row({ face: { state: 'unchecked', says: '', action: null, also: [] } }), row({ group: 'needs-you', status: 'needs-setup' }),
      row({ status: 'needs-setup', face: { state: 'needs-setup', says: '', action: null, also: [] } }), row({ status: 'unavailable', group: 'unavailable' })];
    for (const r of rows) expect(cardLook(r, wire(), workspace()).section).toBe(modelSection(r));
  });
});

describe('its words', () => {
  it('says what it is and where it runs, a cloud model by the service its pictures go to', () => {
    expect(cardKindWords(row(), wire())).toBe('Model, on this Mac');
    expect(cardKindWords(row({ kind: 'tool-function', runs: 'cpu' }), wire())).toBe('Method, on this Mac');
    expect(cardKindWords(row({ kind: 'tool-function', newKind: { operation: 'cells', input: 'image.2d.rgb8', output: 'labels.instance.2d', gives: '', proved: true, proof: 'bundled with the app' } }), wire())).toBe('Built-in method');
    expect(cardKindWords(row({ kind: 'cloud-model', runs: 'cloud', where: 'in the cloud', facts: [{ kind: 'key', ok: true, says: '', service: 'gemini' }] }), wire())).toBe('Cloud model, Google');
    expect(cardKindWords(row({ kind: 'cloud-model', runs: 'cloud', where: 'in the cloud' }), wire())).toBe('Cloud model, in the cloud');
    expect(cardKindWords(row({ mine: true }), wire())).toBe('Model, yours · on this Mac');
    expect(cardKindWords(row({ channel: 'shared-1a2b3c4d', channelSource: 'shared-file' }), wire())).toBe('Model, from a shared file · on this Mac');
    expect(cardKindWords(row({ kind: 'cloud-model', runs: 'cloud', where: 'in the cloud', channel: 'shared-1a2b3c4d', channelSource: 'shared-file' }), wire())).toBe('Cloud model, in the cloud · from a shared file');
  });

  it('one facts line: a cloud model’s key, a download’s size, where it runs and what it costs', () => {
    const gemini = row({ kind: 'cloud-model', runs: 'cloud', facts: [{ kind: 'key', ok: true, says: '', service: 'gemini' }] });
    expect(cardFactsWords(gemini, wire(), 'ready', null)).toBe('Your Google key');
    expect(cardFactsWords(row({ runs: 'cloud', facts: [{ kind: 'key', ok: false, says: '', service: 'fal' }] }), wire(), 'setup', null)).toBe('Needs your fal key');
    expect(cardFactsWords(row(), wire(), 'setup', { kind: 'download', label: 'Download 43 MB', short: 'Download 43 MB', says: 'Download 43 MB', how: '', does: 'download', model: 'x' })).toBe('43 MB · free');
    expect(cardFactsWords(row(), wire(), 'ready', null)).toBe('On this Mac · free');
  });
});

describe('a download that is not the whole setup', () => {
  it('names the whole setup on its card, Set up, with the steps in its facts line', () => {
    const r = row({ id: 'instanseg-brightfield', name: 'InstanSeg (brightfield nuclei)', status: 'needs-setup', word: 'Download 16 MB', size: 15_792_317,
      face: { state: 'needs-setup', says: 'Download 16 MB', action: null, also: [] },
      next: { kind: 'download', says: 'Download 16 MB', model: 'instanseg-brightfield', bytes: 15_792_317 },
      steps: [{ kind: 'download', says: 'Download 16 MB', model: 'instanseg-brightfield', bytes: 15_792_317 },
        { kind: 'install', says: 'Install InstanSeg (brightfield nuclei)', how: 'its own Python and 35 packages, each checked by its hash', model: 'instanseg-brightfield' }],
      setups: plan([{ kind: 'download', says: 'Download 16 MB', by: 'app' }, { kind: 'install', says: 'Install', by: 'app' }]) });
    const look = cardLook(r, wire(), workspace());
    expect(look.verb).toEqual({ kind: 'sheet', label: 'Set up' });
    expect(look.facts).toBe('16 MB, then its own Python (35 packages)');
    // A download that is all of its setup keeps Download 16 MB, started in place.
    const only = cardLook({ ...r, steps: [r.steps[0]] }, wire(), workspace());
    expect(only.verb).toEqual({ kind: 'download', label: 'Download 16 MB', model: 'instanseg-brightfield' });
  });
});
