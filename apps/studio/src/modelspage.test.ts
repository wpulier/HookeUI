// The Models page and the header's chip (modelspage.ts; P3 of the open catalog): its groups, the chip (green only when
// every current choice can run), first run, a card's one action, and the words, from the server's rows.
import { describe, expect, it, vi } from 'vitest';
import { ModelsPage, integrationWords, type ModelsPageCommands } from './modelspage';
import { fire, settleMicrotasks, TestElement } from './workspace-windows.test-support';
import { action, chipState, downloadTime, needsSheet, sheetFacts, fijiFilter, grouped, isOn, liveNote, mark, neededDownloads, needsWords, problemGroups, summary, usedForWords, whereWords, channelWords } from './modelspage';
import { keyTiles } from './keystrip';
import type { CapabilitiesWire, KeyFacts, ModelRow } from './protocol';

const row = (over: Partial<ModelRow> = {}): ModelRow => ({
  id: 'edgetam', name: 'EdgeTAM', runs: 'this-mac', where: 'on this Mac', what: 'Outlines what you click', kind: 'model',
  version: 'v', credit: {}, link: '', licence: '', tier: 'open', mine: false, status: 'ready', word: 'Ready', group: 'ready',
  next: null, steps: [], inUse: false, chosen: false, facts: [], usedFor: [], size: 0, files: [], folder: '', revision: '', ...over,
});
const gemini = row({
  id: 'gemini-3.8-flash', name: 'Gemini 3.8 Flash', runs: 'cloud', status: 'needs-setup', word: 'Needs your Google Gemini API key',
  group: 'needs-you', chosen: true, inUse: true,
  next: { kind: 'key', says: 'Add your Google Gemini API key', service: 'gemini' }, steps: [],
  usedFor: [{ role: 'vision', title: 'Seeing', option: 'gemini-3.8-flash', options: ['Gemini 3.8 Flash (cloud)'], current: true, fixed: false }],
});
const live = row({
  id: 'gpt-live-1', name: 'GPT-Live', status: 'needs-setup', word: 'Needs your OpenAI API key', group: 'needs-you', chosen: true,
  next: { kind: 'key', says: 'Add your OpenAI API key', service: 'openai' },
  usedFor: [{ role: 'voice', title: 'Talking', option: 'gpt-live', options: ['GPT-Live (OpenAI)'], current: true, fixed: false }],
});
const key = (over: Partial<KeyFacts> = {}): KeyFacts => ({
  service: 'gemini', name: 'Google Gemini API', short: 'Google', getKey: 'https://aistudio.google.com/apikey',
  enables: 'Seeing, naming, the running look, quick decisions and web lookups', present: false, source: null, sourceSays: '',
  canSave: true, ...over,
});

describe('the groups', () => {
  it('puts each row in its group, in the order the server sent', () => {
    const optional = row({ id: 'dinov3-small', group: 'available', status: 'needs-setup' });
    const g = grouped([gemini, row(), optional]);
    expect(g['needs-you'].map((r) => r.id)).toEqual(['gemini-3.8-flash']);
    expect(g.ready.map((r) => r.id)).toEqual(['edgetam']);
    expect(g.available.map((r) => r.id)).toEqual(['dinov3-small']); // nothing you chose needs it: need setup or checks
    // The summary counts the sections Models and Tools By model share (run-spec.ts modelSection).
    expect(summary([gemini, row(), optional])).toBe('1 needs you · 1 ready · 1 can be set up');
  });
});

describe('the chip', () => {
  it('is green only when every model your current choices use can run', () => {
    expect(chipState([row(), row({ id: 'x', group: 'available', status: 'needs-setup' })])).toMatchObject({ tone: 'ok', words: 'All set' });
    expect(chipState(undefined).tone).toBe('unknown');
  });

  it('says how many need you and names them, the job first', () => {
    const chip = chipState([gemini, live, row()]);
    expect(chip).toMatchObject({ tone: 'needs', words: '2 things need you' });
    expect(chip.title).toContain('Seeing needs your Google Gemini API key');
    expect(chip.title).toContain('Talking needs your OpenAI API key');
    expect(chipState([gemini]).words).toBe('1 thing needs you');
  });

  it('with only a Google key, talking still needs OpenAI (one key never turns everything on)', () => {
    const seeing = { ...gemini, status: 'ready' as const, word: 'Ready', group: 'ready' as const, next: null };
    expect(chipState([seeing, live]).title).toBe('Talking needs your OpenAI API key. Open to see how.');
  });

  it('names a download as the step', () => {
    const edgetam = row({ status: 'needs-setup', word: 'Download 56 MB', group: 'needs-you',
      next: { kind: 'download', says: 'Download 56 MB', model: 'edgetam' },
      usedFor: [{ role: 'outline', title: 'Outlining', option: 'edgetam', options: [], current: true, fixed: false }] });
    expect(needsWords(edgetam)).toBe('Outlining: Download 56 MB');
  });
});

describe('the keys band', () => {
  it('leads with the missing keys that add the most, then the keys you have; the rest is one line', () => {
    const { tiles, other } = keyTiles({ GEMINI_API_KEY: key(), OPENAI_API_KEY: key({ service: 'openai', short: 'OpenAI' }),
      HF_TOKEN: key({ service: 'huggingface', short: 'Hugging Face' }), FAL_KEY: key({ service: 'fal', short: 'fal.ai', present: true, source: 'file' }) },
    { works: 60, total: 176, unlock: [{ key: 'OPENAI_API_KEY', service: 'OpenAI API', getKey: '', adds: 13 }, { key: 'GEMINI_API_KEY', service: 'Google Gemini API', getKey: '', adds: 71 }] });
    expect(tiles.map((t) => [t.key.short, t.adds])).toEqual([['Google', 71], ['OpenAI', 13], ['fal.ai', 0]]);
    expect(other.map((t) => t.key.short)).toEqual(['Hugging Face']);
  });
});

describe('a card', () => {
  it('offers the one thing that turns it on', () => {
    expect(action(gemini)).toEqual({ kind: 'key', label: 'Add key', service: 'gemini' });
    expect(action(row({ status: 'needs-setup', next: { kind: 'download', says: 'Download 3.5 GB', model: 'sam3.1-local' } })))
      .toEqual({ kind: 'download', label: 'Download 3.5 GB', model: 'sam3.1-local' });
    expect(action(row({ status: 'needs-setup', next: { kind: 'licence', says: 'Accept its licence', url: 'https://huggingface.co/x' } })).kind).toBe('licence');
    expect(action(row({ status: 'needs-setup', next: { kind: 'install', says: 'Install Trackastra', how: 'make trackastra-setup' } })))
      .toEqual({ kind: 'install', label: 'Install Trackastra', how: 'make trackastra-setup' });
    expect(action(row()).kind).toBe('none');
    expect(action(row({ status: 'unavailable', next: { kind: 'edition', says: 'not in this edition' } })).kind).toBe('none');
  });

  it('says what it is used for, the current ones marked', () => {
    const r = row({ usedFor: [
      { role: 'outline', title: 'Outlining', option: 'edgetam', options: [], current: true, fixed: false },
      { role: 'cells', title: 'Cells', option: 'edgetam-automask', options: [], current: false, fixed: false },
      { role: 'fixed:find-all', title: 'Find all like this', option: 'model', options: [], current: true, fixed: true }] });
    expect(usedForWords(r)).toBe('Outlining (your choice) · Cells · Find all like this (in use)');
  });

  it('marks each fact', () => {
    expect([true, false, null].map((ok) => mark({ kind: 'files', ok, says: '' }))).toEqual(['✓', '✗', '·']);
  });
});

describe('what your choices wait on', () => {
  it('collects the downloads, each model once', () => {
    const a = row({ id: 'edgetam', group: 'needs-you', next: { kind: 'download', says: 'Download 56 MB', model: 'edgetam' } });
    const b = row({ id: 'edgetam-video', name: 'EdgeTAM video', group: 'needs-you', next: { kind: 'download', says: 'Download 56 MB', model: 'edgetam' } });
    const c = row({ id: 'cellpose-sam', name: 'Cellpose-SAM', group: 'needs-you', next: { kind: 'download', says: 'Download 1.2 GB', model: 'cellpose-sam' } });
    expect(neededDownloads([a, b, c, gemini]).map((d) => d.model)).toEqual(['edgetam', 'cellpose-sam']);
  });

  it('notes over the live view only what Seeing needs', () => {
    expect(liveNote([live, gemini])?.words).toBe('Seeing needs your Google Gemini API key');
    expect(liveNote([live])).toBeNull();
  });
});

describe('your own models', () => {
  it('offers Turn on once added and downloaded, and Turn off once on', () => {
    const yours = row({ id: 'mine/lc', name: 'BlueSpotter LC', mine: true, status: 'needs-setup', word: 'Turn it on', group: 'available',
      next: { kind: 'activate', says: 'Turn it on', model: 'mine/lc' },
      facts: [{ kind: 'on', ok: false, says: 'Off: nothing runs it until you turn it on, which checks it first' }] });
    expect(action(yours)).toEqual({ kind: 'activate', label: 'Turn on', model: 'mine/lc' });
    expect(isOn(yours)).toBe(false);
    const on = { ...yours, status: 'ready' as const, word: 'Ready', group: 'ready' as const, next: null,
      facts: [{ kind: 'on' as const, ok: true, says: 'Turned on: a choice under Cells' }] };
    expect(action(on).kind).toBe('none');
    expect(isOn(on)).toBe(true);
    expect(isOn({ ...on, mine: false })).toBe(false); // only yours can be turned off
  });
});

describe('installing', () => {
  it('is a button the app carries out when the model names its own Python, and how to install otherwise', () => {
    const made = row({ id: 'micro-sam-lm', name: 'micro-sam (light microscopy)', status: 'needs-setup', word: 'Needs setup',
      group: 'available', next: { kind: 'install', says: 'Install micro-sam (light microscopy)', how: 'its own Python',
        model: 'micro-sam-lm' } });
    expect(action(made)).toEqual({ kind: 'setup', label: 'Install micro-sam (light microscopy)', model: 'micro-sam-lm' });
    const byHand = { ...made, next: { kind: 'install' as const, says: 'Install SAM 3.1', how: '`make sam3-setup`' } };
    expect(action(byHand)).toEqual({ kind: 'install', label: 'Install SAM 3.1', how: '`make sam3-setup`' });
  });
});

describe('Fiji plugins', () => {
  const row = (name: string, categories: string[], kind: 'ships-with-fiji' | 'update-site' | 'none-known') => ({
    name, page: `/plugins/${name.toLowerCase()}`, categories, link: '', route: { kind }, how: '', installed: '', usable: '' });
  it('keeps the rows a search or a category names, and every row with neither', () => {
    const rows = [row('MorphoLibJ', ['Segmentation'], 'update-site'), row('TrackMate', ['Tracking'], 'ships-with-fiji'),
      row('Old Thing', ['Segmentation'], 'none-known')];
    expect(fijiFilter(rows, '', '').length).toBe(3);
    expect(fijiFilter(rows, 'morpho', '').map((r) => r.name)).toEqual(['MorphoLibJ']);
    expect(fijiFilter(rows, '', 'Segmentation').map((r) => r.name)).toEqual(['MorphoLibJ', 'Old Thing']);
    expect(fijiFilter(rows, 'track', 'Segmentation')).toEqual([]);
  });
});

describe('entries the app won’t run', () => {
  it('says one made for another version of the app apart from one that can’t be read', () => {
    const groups = problemGroups([
      { entry: 'cool-model', problems: ['needs a newer ObservationUI: it is written in catalog format 2'], incompatible: true },
      { entry: 'mine/typo', problems: ['unknown key `rnner`'] },
    ]);
    expect(groups.map((g) => [g.title, g.rows.map((r) => r.entry)])).toEqual([
      ['Made for another version of ObservationUI', ['cool-model']],
      ['Entries that can’t be read', ['mine/typo']],
    ]);
    expect(problemGroups([])).toEqual([]);
  });
});

describe('channels', () => {
  it('says whose entry it is and where it came from', () => {
    const base = { mine: false, where: 'on this Mac' } as unknown as ModelRow;
    expect(whereWords({ ...base, mine: true })).toBe('yours · on this Mac');
    expect(whereWords({ ...base, channel: 'reviewed' })).toBe('new in the catalog · on this Mac');
    expect(whereWords({ ...base, channel: 'lab-models' })).toBe('community: lab-models · on this Mac');
    expect(whereWords(base)).toBe('on this Mac');
  });
  it('says a community channel is not reviewed here, and whether its code is allowed', () => {
    const c = { name: 'lab-models', url: 'https://x/y.git', commit: '9e1d0b2aaaa', reviewed: false, trusted: false, checked: '', entries: 1 };
    expect(channelWords(c)).toBe('lab-models at 9e1d0b2 · 1 entry · not reviewed here, its code not allowed');
    expect(channelWords({ ...c, name: 'reviewed', reviewed: true, entries: 31 })).toBe('The reviewed catalog at 9e1d0b2 · 31 entries');
    expect(channelWords({ ...c, url: '' })).toBe('Shared file · saved copy · 1 entry · not reviewed here, its code not allowed');
    expect(channelWords({ ...c, url: '', label: "Maya's shared file" })).toBe("Maya's shared file · saved copy · 1 entry · not reviewed here, its code not allowed");
  });
});

describe('the sheet before setup', () => {
  const plan = (steps: { kind: string; says: string; by: 'app' | 'you'; bytes?: number; model?: string; service?: string; onAgain?: boolean; recheck?: boolean }[], over = {}) =>
    ({ option: 'o', model: 'edgetam', label: 'EdgeTAM', recipe: 'r', privacy: 'Runs on this Mac', cost: 'Free', steps, ...over });
  it('opens for a download, an install or a step of yours, never for a check alone or Check again', () => {
    expect(needsSheet(plan([{ kind: 'check', says: 'Check', by: 'app' }]))).toBe(false);
    expect(needsSheet(plan([]))).toBe(false);
    expect(needsSheet(plan([{ kind: 'download', says: 'Download 56 MB', by: 'app' }]))).toBe(true);
    expect(needsSheet(plan([{ kind: 'install', says: 'Install it', by: 'app' }]))).toBe(true);
    expect(needsSheet(plan([{ kind: 'key', says: 'Add your key', by: 'you' }]))).toBe(true);
    expect(needsSheet(plan([{ kind: 'download', says: 'Download 56 MB', by: 'app' }]), true)).toBe(false);
  });
  it('says a time only as a range by size', () => {
    expect(downloadTime(56e6)).toBe('about a minute or less on a typical connection');
    expect(downloadTime(669e6)).toBe('a few minutes on a typical connection');
    expect(downloadTime(3.5e9)).toBe('ten minutes or more on a typical connection');
    expect(downloadTime(20e9)).toBe('half an hour or more on a typical connection');
  });
  it('adds up the downloads, names one over 1 GB, and weighs them with the spare against the free space', () => {
    const f = sheetFacts(plan([{ kind: 'download', says: 'Download 669 MB', bytes: 669e6, model: 'edgetam', by: 'app' }]), row(), [], {},
      { freeBytes: 50e9, spareBytes: 1e9 });
    expect(f.download).toBe('It downloads 669 MB.'); expect(f.big).toBe(false);
    expect(f.fits).toBe(true);
    expect(f.time).toEqual(['Downloading 669 MB: a few minutes on a typical connection.']);
    const tight = sheetFacts(plan([{ kind: 'download', says: 'Download 669 MB', bytes: 669e6, by: 'app' }]), row(), [], {}, { freeBytes: 1.5e9, spareBytes: 1e9 });
    expect(tight.fits).toBe(false); expect(tight.space).toContain('Not enough space');
    const two = sheetFacts(plan([{ kind: 'download', says: 'a', bytes: 700e6, by: 'app' }, { kind: 'download', says: 'b', bytes: 400e6, by: 'app' }]),
      row(), [], {}, { freeBytes: 50e9 });
    expect(two.download).toBe('It downloads 1.1 GB: a large download, over 1 GB.'); expect(two.big).toBe(true);
  });
  it('weighs the exact free space at the boundary: never a rounded reading that would wrongly withhold Turn on', () => {
    const GiB = 2 ** 30;
    const download = (bytes: number) => plan([{ kind: 'download', says: 'Download', bytes, by: 'app' }]);
    // 1.75 GiB free, a 0.5 GiB download and the 1 GiB the app keeps spare: it fits (a floor to whole GiB would say not).
    expect(sheetFacts(download(0.5 * GiB), row(), [], {}, { freeBytes: 1.75 * GiB, spareBytes: GiB }).fits).toBe(true);
    // Exactly the need fits; one byte short doesn't.
    expect(sheetFacts(download(0.5 * GiB), row(), [], {}, { freeBytes: 1.5 * GiB, spareBytes: GiB }).fits).toBe(true);
    expect(sheetFacts(download(0.5 * GiB), row(), [], {}, { freeBytes: 1.5 * GiB - 1, spareBytes: GiB }).fits).toBe(false);
  });
  it('says what it can’t read: a size not given (the catalog row’s size when known), free space not read', () => {
    const sized = sheetFacts(plan([{ kind: 'download', says: 'Download', model: 'edgetam', by: 'app' }]), row(), [row({ size: 56e6 })], {}, undefined);
    expect(sized.download).toBe('It downloads 56 MB.');
    expect(sized.fits).toBeNull(); expect(sized.space).toContain('couldn’t be read');
    const unknown = sheetFacts(plan([{ kind: 'download', says: 'Download', model: 'other', by: 'app' }]), row(), [], {}, { freeBytes: 9e9 });
    expect(unknown.download).toBe('It downloads files whose size isn’t known here.');
    expect(unknown.time).toEqual(['Downloading: no estimate, as its size isn’t known here.']);
  });
  it('says the licence and tier, where it runs, the key’s source and the cost', () => {
    const cloud = sheetFacts(plan([{ kind: 'install', says: 'Install it', by: 'app' }],
      { privacy: 'Sends pictures to Google Gemini (your key)', cost: 'Paid per call', service: 'gemini' }),
    row({ licence: 'CC-BY-NC-4.0', tier: 'noncommercial' }), [], { GEMINI_API_KEY: key({ present: true, source: 'keychain', sourceSays: 'saved by this app' }) },
    { freeBytes: 9e9 });
    expect(cloud.licence).toBe('CC-BY-NC-4.0 (non-commercial use only).');
    expect(cloud.where).toBe('Sends pictures to Google Gemini (your key)');
    expect(cloud.keys).toEqual(['It uses your Google key, saved by this app.']);
    expect(cloud.cost).toBe('Paid per call');
    expect(cloud.space).toContain('Additional installation size isn’t known here');
    expect(cloud.time).toContain('If software needs installing, it can take several minutes depending on this Mac and the connection.');
  });
  it('does not count model-file space as room for the unknown software installation', () => {
    const facts = sheetFacts(plan([{ kind: 'download', says: 'Download', bytes: 4e6, by: 'app' },
      { kind: 'install', says: 'Install', by: 'app' }]), row(), [], {}, { freeBytes: 9e9 });
    expect(facts.space).toContain('This covers model files only; the software installation may need additional space.');
    expect(facts.download).toContain('Missing software dependencies may also need downloading');
  });
  it('a functional recheck reuses installed software and promises no dependency download', () => {
    const facts = sheetFacts(plan([{ kind: 'install', says: 'Check its proof', by: 'app', recheck: true }]), row(), [], {}, { freeBytes: 9e9 });
    expect(facts.download).toBe('Nothing to download.');
    expect(facts.space).toBe('');
    expect(facts.time).toEqual(['Then a functional check on this Mac.']);
  });
  it('does not promise zero downloads while permission hides later setup requirements', () => {
    const facts = sheetFacts(plan([{ kind: 'trust', says: 'Allow its code', by: 'you' }]), row(), [], {}, { freeBytes: 9e9 });
    expect(facts.download).toBe('After permission, the app checks which software and model files need preparing. Downloads may still be required.');
    expect(facts.fits).toBeNull();
  });
  it('says a Turn on of a version still installed downloads and installs nothing, and is quick (Window C, 2026-10-14)', () => {
    const again = sheetFacts(plan([{ kind: 'install', says: 'Turn C7 synthetic c7-image-q on', model: 'c7-local/c7-image-q', by: 'app', onAgain: true }]),
      row(), [], {}, { freeBytes: 9e9 });
    expect(again.download).toBe('Nothing to download or install: its installed version is turned back on.');
    expect(again.space).toBe(''); // no install size to weigh
    expect(again.time).toEqual(['Turning it back on: quick, as nothing is installed again.']);
    expect(JSON.stringify(again)).not.toMatch(/several minutes|isn’t known before it runs/);
    // A real install beside it is still said as one.
    const both = sheetFacts(plan([{ kind: 'install', says: 'Turn A on', by: 'app', onAgain: true }, { kind: 'install', says: 'Install B', by: 'app' }]),
      row(), [], {}, { freeBytes: 9e9 });
    expect(both.download).toBe('Setup reuses prepared software when possible. Missing dependencies may need downloading; their size isn’t known here.');
    expect(both.space).toContain('Additional installation size isn’t known here');
    expect(both.time).toContain('If software needs installing, it can take several minutes depending on this Mac and the connection.');
  });
});

describe('a link that needs code (ticket 62 row 7)', () => {
  it('says what the app found it holds and lacks, then the two ways on; the generic line only when nothing was said', () => {
    const why = 'haesleinhuepf/napari-segment-blobs-and-things-with-membranes at commit f0a392a holds a Python package (setup.py); its licence BSD-3-Clause. It has no bioimage.io model description';
    expect(integrationWords(why)).toBe(`${why}. Choose how to build that integration, then check it on your picture before enabling it.`);
    expect(integrationWords('its weights need TensorFlow.')).toBe('Its weights need TensorFlow. Choose how to build that integration, then check it on your picture before enabling it.');
    expect(integrationWords()).toBe('This source needs code to connect it to the app. Choose how to build that integration, then check it on your picture before enabling it.');
  });

  it('the Add a tool sheet shows the reason the app gave, and keeps the link for building', async () => {
    let focused: TestElement | null = null;
    class Focusable extends TestElement { focus() { focused = this; } }
    vi.stubGlobal('document', { createElement: (tag: string) => new Focusable(tag), get activeElement() { return focused; } });
    vi.stubGlobal('requestAnimationFrame', () => 0);
    const root = new TestElement('main');
    const why = 'maya/diatom-tools at commit 0123456 holds 2 notebooks; no licence stated. It has no bioimage.io model description';
    const add = vi.fn(async () => ({ kind: 'skeleton', model: 'mine/diatom-tools', says: `Added diatom-tools as a skeleton: ${why}.`, why }));
    const commands: ModelsPageCommands = { download: vi.fn(async () => undefined), reveal: vi.fn(async () => undefined), add };
    const wire: CapabilitiesWire = { type: 'capabilities', families: [], actors: { you: '', agent: '', auto: '', researcher: '' },
      capabilities: [], roles: [], preferences: [], pending: 0, build: 'test', agentText: '', models: [] };
    const page = new ModelsPage(root as unknown as HTMLElement, commands); page.update(wire);
    page.show('add-add'); page.update(wire);
    const input = root.querySelectorAll('.capability-entry-field').find(l => l.textContent.startsWith('Model or GitHub link'))!.querySelector('input')!;
    input.value = 'https://github.com/maya/diatom-tools';
    fire(root.querySelector('.capability-import-continue')!, 'click');
    for (let i = 0; i < 6; i++) await settleMicrotasks();
    expect(add).toHaveBeenCalledWith('https://github.com/maya/diatom-tools');
    expect(root.textContent).toContain('This needs an integration before it can run');
    expect(root.textContent).toContain(`${why}. Choose how to build that integration`);
    expect(root.textContent).not.toContain('This source needs code to connect it to the app');
    expect(root.textContent).toContain('Have AI build the integration here');
    vi.unstubAllGlobals();
  });
});


describe('local reader result review', () => {
  it.each(['reader', 'workflow', 'update', 'member'] as const)('renders %s evidence truthfully and accepts only after the explicit press', async (kind) => {
    class Focusable extends TestElement { focus() {} }
    vi.stubGlobal('document', {createElement:(tag:string)=>new Focusable(tag), activeElement:null});
    vi.stubGlobal('requestAnimationFrame',()=>0);
    const root = new TestElement('main');
    const artifact = {kind:'json' as const,sha256:'a'.repeat(64),bytes:12,preview:'{"layers":[]}'};
    const review = {generation:'b'.repeat(16),digest:'c'.repeat(64),says:'Exact references only',cases:[
      {case:'d'.repeat(64),member:'image-tools#read-folder',kind:kind==='update'||kind==='member'?'workflow' as const:kind,source:'Synthetic rendering fixture',
       inputs:{'data.npy':{sha256:'e'.repeat(64),bytes:12}},referenceSha256:'f'.repeat(64),
       artifacts:{input:artifact,actual:artifact,reference:artifact}}]};
    const reviewBundle = vi.fn(async () => review);
    const commands:ModelsPageCommands = {download:vi.fn(async()=>undefined),reveal:vi.fn(async()=>undefined),
      session:()=> 'project-one',reviewBundle,shelf:{turnOn:vi.fn(async()=>undefined),cancel:vi.fn(async()=>undefined),
        tryIt:vi.fn(async()=>undefined),get:vi.fn(async()=>undefined),seen:vi.fn(async()=>undefined),dismiss:vi.fn(async()=>undefined)}};
    const model = row({id:'image-tools',name:'Reader',execution:kind==='update'?undefined:{action:'review-bundle',why:'Review exact results'},
      memberKinds:[{key:'image-tools#read-folder',name:'Read folder',what:'Read scientific data',operation:'read',input:'project.scientific',output:'project.scientific',gives:'scientific data',proved:true}],
      setups:[{role:'model:image-tools',title:'Reader',option:'image-tools',ready:true,plan:null,recheck:false,canTry:false}]});
    const wire:CapabilitiesWire={type:'capabilities',families:[],actors:{you:'',agent:'',auto:'',researcher:''},
      capabilities:[],roles:[],preferences:[],pending:0,build:'test',agentText:'',models:[model]};
    if(kind==='update')wire.setup={edition:'open',options:{},maintenance:{'image-tools':{state:'needs-review',says:'Review update',next:null,
      policy:{policy:'manual',words:'',effective:'manual'},check:{state:'never'},active:{generation:'a'.repeat(16),version:'1'},
      candidate:{generation:review.generation,version:'2'},held:[],workflowReview:review.generation}}};
    try {
      const page = new ModelsPage(root as unknown as HTMLElement,commands);page.update(wire);page.show('image-tools');
      const button=(text:string)=>root.querySelectorAll('button').find(button=>button.textContent===text)!;
      const opener = kind === 'member'
        ? root.querySelector('.tool-kind-trial')!.querySelectorAll('button').find(button=>button.textContent==='Review results')!
        : button(kind==='update'?'Review update results':'Review results');
      fire(opener,'click');await settleMicrotasks();
      fire(root.querySelector('.tool-setup-sheet')!.querySelectorAll('button').find(button=>button.textContent===(kind==='update'?'Review update results':'Review results'))!,'click');
      for(let i=0;i<6;i++)await settleMicrotasks();
      expect(root.textContent).toContain(kind === 'reader' ? 'Exact reader data check passed' : 'Local controls, Save and reopen observed');
      if (kind !== 'reader') {
        expect(root.textContent).toContain('Reopened result');expect(root.textContent).toContain('Saved state');
        expect(root.textContent).not.toContain('Expected reference');expect(root.textContent).not.toContain('Reference check passed');
      }
      expect(root.textContent).toContain('data.npy');expect(root.textContent).not.toContain('passed on CPU');
      expect(reviewBundle).toHaveBeenCalledTimes(1);
      if(kind==='update')expect(reviewBundle).toHaveBeenLastCalledWith('image-tools','project-one',{generation:review.generation});
      fire(button(kind==='update'?'Enable this update for Apply':'Enable these tools'),'click');for(let i=0;i<6;i++)await settleMicrotasks();
      expect(reviewBundle).toHaveBeenLastCalledWith('image-tools','project-one',{generation:review.generation,digest:review.digest});
    } finally {vi.unstubAllGlobals();}
  });
});
