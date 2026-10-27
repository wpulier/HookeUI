// A session's sources, the studio's half (sources.ts; ADR 0022): the wire read safely, the sources under their
// specimens, the words of a row and of the live camera, what is on stage, the model (another session's rows never
// shown, × hidden at once and back when refused), adding files from this Mac (uploaded, found by their file, opened),
// and the section and a new session's home over a synthetic DOM (the library itself: asset-library.test.ts).
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { AssetMeta } from './protocol';
import {
  addFiles, alsoIn, groupBySpecimen, isAsset, liveState, magnificationWords, plainError, readSources, sourceMeta, SourcesModel, SourcesSection, stageOf,
  UNNAMED, uploadedFile, joinedSince, type AddDeps, type SourceRow, type SourcesActions,
} from './sources';
import { cameraLine, SessionHome } from './session-home';
import { onStart } from './session-view';
import { fire } from './workspace-windows.test-support';
import { at, NOW, sessionDOM, SessionNode, type SessionDOM } from './sessions.test-support';

function asset(id: string, fields: Partial<AssetMeta> = {}): AssetMeta {
  return { id, kind: 'recording', title: `Asset ${id}`, from: 'added', capturedAt: at(1, 44), captured: 'Oct 12, 1:44 am', seconds: 192,
    capturedOn: null, microscope: true, optics: {}, specimen: null, specimenBy: null, notes: null, origin: null, file: `${id}.mp4`,
    thumb: `/api/library/${id}/thumbnail`, ...fields };
}
function row(id: string, fields: Partial<SourceRow> = {}): SourceRow {
  return { ...asset(id), opened: at(2), joined: false, position: null, ...fields };
}
const wire = (session: string, sources: SourceRow[], onStage: string | null = null, removed: string[] = []) =>
  ({ type: 'sources', session, onStage, sources, removed });

describe('the wire and the words', () => {
  test('a silent Blackmagic stream never gives Continuity locking instructions', () => {
    const status = liveState({connected:true,health:'no_frames',feed:{source:'camera',footage:null,
      bridge:{sourceKind:'blackmagic',devicePresent:true}}});
    expect(status.words).toContain('Blackmagic');
    expect(status.help).not.toMatch(/Lock the iPhone|Continuity/);
    expect(status.help).toContain('camera setup');
  });
  test("a session's sources are read safely: rows that are not an asset's are left out", () => {
    expect(readSources(null)).toBeNull();
    expect(readSources({ onStage: 'live', sources: [] })).toBeNull(); // no session
    const read = readSources({ session: 's1', onStage: 'a7', sources: [row('a7'), { id: 'x', kind: 'tape' }, { id: 'a8', kind: 'still' }, null],
      removed: ['a3', 4] });
    expect(read).toEqual({ session: 's1', onStage: 'a7', sources: [row('a7')], removed: ['a3'] });
    expect(isAsset(asset('a1'))).toBe(true);
    expect(isAsset({ ...asset('a1'), id: 'o3' })).toBe(false);
  });

  test('grouped under their specimens, in the order of their newest, one heading however it is written', () => {
    const groups = groupBySpecimen([
      row('a9', { specimen: 'sperm sample' }), row('a8', { specimen: null }), row('a7', { specimen: 'Sperm  sample ' }), row('a6', { specimen: 'leaf' }),
    ]);
    expect(groups.map((g) => [g.label, g.rows.map((r) => r.id)])).toEqual([
      ['Sperm sample', ['a9', 'a7']], [UNNAMED, ['a8']], ['Leaf', ['a6']],
    ]);
    expect(groupBySpecimen([])).toEqual([]);
  });

  test("a row's second line: its kind, when, a recording's length and its magnification when known", () => {
    expect(sourceMeta(asset('a1', { seconds: 192, optics: { objective: 40 } }), NOW)).toBe('Recording · 1:44 am · 3:12 · 40x');
    expect(sourceMeta(asset('a2', { kind: 'still', seconds: 0, capturedAt: at(9, 0, 3), optics: { objective: 100 } }), NOW)).toBe('Picture · Oct 9 · 100x');
    expect(sourceMeta(asset('a3', { capturedAt: null, captured: 'Jun 14, 9:00 am', seconds: null }), NOW)).toBe('Recording · Jun 14, 9:00 am');
    expect(magnificationWords({ objective: 2.5 })).toBe('2.5x');
    expect(magnificationWords({})).toBe('');
    expect(magnificationWords(null)).toBe('');
  });

  test('what is on stage: the camera or test footage is the live camera; an asset is the session\'s own word, else its file', () => {
    const state = { session: 's1', onStage: 'a7', sources: [row('a7', { file: 'part-1.mp4' }), row('a8', { file: 'part-1.mp4' }), row('a9', { file: 'smear.png' })], removed: [] };
    expect(stageOf({ source: 'camera', footage: null }, state)).toBe('live');
    expect(stageOf({ source: 'footage', footage: 'real/leaf.mp4' }, state)).toBe('live'); // test footage stands for the camera
    // Two Record videos are both part-1.mp4: the session's own word decides.
    expect(stageOf({ source: 'footage', footage: 'asset:/d/ds-2/video/part-1.mp4' }, state)).toBe('a7');
    // The session's word is of another file (another studio put this on): the file it is.
    expect(stageOf({ source: 'footage', footage: 'asset:/m/added/smear.png' }, state)).toBe('a9');
    expect(stageOf(null, state)).toBe('a7');
    expect(stageOf(null, null)).toBeNull();
  });

  test('the live camera: live, test footage, off stage while an asset is on, or what is wrong and the first thing to do', () => {
    const feed = { source: 'camera', footage: null, targetDevice: 'iPhone', bridge: { devicePresent: false, diagnosis: null } };
    expect(liveState({ connected: false, health: null, feed })).toMatchObject({ tone: 'off', words: 'Not connected to the app' });
    expect(liveState({ connected: true, health: 'bridge_offline', feed })).toMatchObject({ tone: 'off', words: 'The camera bridge is not running' });
    expect(liveState({ connected: true, health: 'live', feed })).toEqual({ tone: 'live', words: 'Live · iPhone', help: '' });
    expect(liveState({ connected: true, health: 'live', feed: { ...feed, source: 'footage', footage: 'real/leaf.mp4', footageTitle: 'Leaf' } }))
      .toEqual({ tone: 'footage', words: 'Test footage · Leaf', help: '' });
    expect(liveState({ connected: true, health: 'live', feed: { ...feed, source: 'footage', footage: 'asset:/x/a.mp4' } }).tone).toBe('away');
    const off = liveState({ connected: true, health: 'no_frames', feed });
    expect(off.words).toBe('No camera detected');
    expect(off.help).toMatch(/USB cable/);
    expect(liveState({ connected: true, health: 'no_frames', feed: { ...feed, bridge: { diagnosis: { text: 'The phone is silent', steps: ['Unlock it'] } } } }))
      .toEqual({ tone: 'off', words: 'The phone is silent', help: 'Unlock it' });
  });

  test("the other sessions that use a source are read safely: at most three, each once, its id when it has no words", () => {
    const four = [1, 2, 3, 4].map((n) => ({ id: `2026-10-0${n}`, label: `Day ${n}` }));
    expect(alsoIn({ alsoIn: four }).map((s) => s.id)).toEqual(['2026-10-01', '2026-10-02', '2026-10-03']);
    expect(alsoIn({ alsoIn: [{ id: 's2', label: ' ' }, { id: 's2', label: 'Again' }, { id: '', label: 'x' }] as never }))
      .toEqual([{ id: 's2', label: 's2' }]);
    expect(alsoIn({})).toEqual([]);
    expect(alsoIn({ alsoIn: 'nope' as never })).toEqual([]);
  });

  test('a refusal names a file by its name, never its whole path', () => {
    expect(plainError('the camera bridge could not play it: /Users/me/MicroscopeMind/footage/real/leaf.mp4 is not in a folder it plays from'))
      .toBe('The camera bridge could not play it: leaf.mp4 is not in a folder it plays from.');
    expect(plainError('there is no asset a999.')).toBe('There is no asset a999.');
    expect(plainError('')).toBe('');
  });

  test("an uploaded file's name and stem, as the bridge gives it", () => {
    expect(uploadedFile('added/2026-10-12-onion-picture.png')).toEqual({ file: '2026-10-12-onion-picture.png', stem: '2026-10-12-onion-picture' });
    expect(uploadedFile('plain')).toEqual({ file: 'plain', stem: 'plain' });
  });
});

describe('the model', () => {
  test("only the open session's sources; another session's go at once and its late messages are ignored", async () => {
    const model = new SourcesModel();
    const told: number[] = [];
    model.onChange(() => told.push(model.rows().length));
    model.setSession('s1');
    expect(model.apply(wire('s1', [row('a7'), row('a8')], 'a7'))).toBe(true);
    expect(model.rows().map((r) => r.id)).toEqual(['a7', 'a8']);
    expect(model.row('a8')?.title).toBe('Asset a8');
    model.setSession('s2');
    expect(model.state).toBeNull();
    expect(model.apply(wire('s1', [row('a7')]))).toBe(false); // the session left's, late
    expect(model.rows()).toEqual([]);
    expect(told).toEqual([0, 2, 0]);
  });

  test('read from the route; a read for a session left meanwhile is dropped, and a failed read keeps what was held', async () => {
    const model = new SourcesModel();
    model.setSession('s1');
    let answer: (body: unknown) => void = () => {};
    const fetcher = vi.fn((url: string) => new Promise((resolve) => {
      answer = (body) => resolve({ ok: true, json: async () => body });
      expect(url).toBe('/api/sessions/s1/sources');
    })) as unknown as typeof fetch;
    const read = model.load(fetcher);
    model.setSession('s2');
    answer(wire('s1', [row('a7')]));
    expect(await read).toBeNull();
    expect(model.state).toBeNull();
    model.setSession('s1');
    const failing = vi.fn(async () => { throw new Error('offline'); }) as unknown as typeof fetch;
    model.apply(wire('s1', [row('a7')]));
    expect((await model.load(failing))?.sources.map((r) => r.id)).toEqual(['a7']);
  });

  test('a sources message applied while a read is pending wins: the read\'s older answer never replaces it', async () => {
    const model = new SourcesModel();
    model.setSession('s1');
    let answer: (body: unknown) => void = () => {};
    const fetcher = vi.fn(() => new Promise((resolve) => {
      answer = (body) => resolve({ ok: true, json: async () => body });
    })) as unknown as typeof fetch;
    const read = model.load(fetcher); // a start's read, pending
    model.apply(wire('s1', [row('a7')], 'live')); // another studio went live: the server's message comes first
    answer(wire('s1', [row('a7')], 'a7')); // then the older GET answers with the asset that was on stage before
    const facts = await read;
    expect(model.state?.onStage).toBe('live');
    expect(facts?.onStage).toBe('live');
    // A start decided from it keeps what plays (never brings the old asset back), even with the camera idle.
    expect(onStart(facts, { source: 'camera', busy: false }, { state: 'waiting' })).toBe('keep');
  });

  test('× hides a row at once; the server\'s list without it settles it, and a refusal shows it again', () => {
    const model = new SourcesModel();
    model.setSession('s1');
    model.apply(wire('s1', [row('a7'), row('a8')]));
    model.hide('a7');
    expect(model.rows().map((r) => r.id)).toEqual(['a8']);
    model.unhide('a7');
    expect(model.rows().map((r) => r.id)).toEqual(['a7', 'a8']);
    model.hide('a8');
    model.apply(wire('s1', [row('a7')], null, ['a8']));
    model.apply(wire('s1', [row('a7'), row('a8')])); // Undo: back in the list, and shown
    expect(model.rows().map((r) => r.id)).toEqual(['a7', 'a8']);
  });
});

describe('adding files from this Mac', () => {
  function deps(found: AssetMeta[][] = [[asset('a12', { title: 'Onion, 2026-10-12 (picture)', kind: 'still', file: '2026-10-12-onion-picture.png' })]]) {
    const said: [string, string][] = [];
    const opened: string[] = [];
    const uploads: string[] = [];
    let finds = 0;
    const d: AddDeps = {
      upload: vi.fn(async (file: File, progress: (share: number) => void) => {
        uploads.push(file.name);
        progress(0.5);
        if (file.name.startsWith('broken')) throw new Error('That file does not open as a picture');
        return { name: `added/2026-10-12-${file.name.replace(/\.\w+$/, '')}-picture.png` };
      }),
      find: vi.fn(async () => found[Math.min(finds++, found.length - 1)] ?? []),
      open: vi.fn(async (a: AssetMeta) => { opened.push(a.id); return true; }),
      say: (text, tone) => said.push([text, tone]),
      wait: async () => {},
    };
    return { d, said, opened, uploads };
  }
  const file = (name: string) => ({ name }) as File;

  test('uploaded, found by its own file in the library (read fresh), then opened; its progress said', async () => {
    const { d, said, opened } = deps();
    const added = await addFiles([file('onion.png')], d);
    expect(added.map((a) => a.id)).toEqual(['a12']);
    expect(d.find).toHaveBeenCalledWith('2026-10-12-onion-picture');
    expect(opened).toEqual(['a12']);
    expect(said.map(([text]) => text)).toEqual(['Adding onion.png…', 'Adding onion.png… 50%', 'Opening Onion, 2026-10-12 (picture)…',
      'Added “Onion, 2026-10-12 (picture)” to this project.']);
  });

  test("found a moment later when the library has not read it yet; a file it never finds is said, never opened", async () => {
    const later = deps([[], [asset('a12', { file: '2026-10-12-onion-picture.png' })]]);
    expect((await addFiles([file('onion.png')], later.d)).map((a) => a.id)).toEqual(['a12']);
    expect(later.d.find).toHaveBeenCalledTimes(2);
    const never = deps([[asset('a13', { file: 'something-else.png' })]]);
    expect(await addFiles([file('onion.png')], never.d)).toEqual([]);
    expect(never.opened).toEqual([]);
    expect(never.said.at(-1)).toEqual(['onion.png was added, but it is not in your assets yet. Find it in the Library in a moment.', 'error']);
  });

  test('a file chooser from an earlier Project opening refuses before uploading',async()=>{
    const {d}=deps();let generation=1;const captured=generation;d.current=()=>generation===captured;
    generation=3; // left and returned to the same Project while the chooser was open
    await expect(addFiles([file('onion.png')],d)).rejects.toThrow('Project changed');
    expect(d.upload).not.toHaveBeenCalled();expect(d.open).not.toHaveBeenCalled();
  });

  test.each(['upload','find','between-files'])('an owner change during %s keeps uploaded Library data without opening it in another Project',async phase=>{
    const {d,said}=deps();let generation=1;const captured=generation;d.current=()=>generation===captured;
    let finish!:()=>void;const pending=new Promise<void>(resolve=>{finish=resolve;});
    const original=phase==='upload'?d.upload:phase==='find'?d.find:d.open;
    if(phase==='upload')d.upload=vi.fn(async(...args:Parameters<AddDeps['upload']>)=>{await pending;return (original as AddDeps['upload'])(...args);});
    if(phase==='find')d.find=vi.fn(async(...args:Parameters<AddDeps['find']>)=>{await pending;return (original as AddDeps['find'])(...args);});
    if(phase==='between-files')d.open=vi.fn(async()=>{generation=3;return true;});
    const adding=addFiles([file('onion.png'),file('next.png')],d);
    if(phase==='find'){await Promise.resolve();await Promise.resolve();}
    if(phase!=='between-files'){generation=3;finish();}
    await expect(adding).rejects.toThrow('already uploaded stay in your Library');
    expect(d.upload).toHaveBeenCalledTimes(1);expect(d.open).toHaveBeenCalledTimes(phase==='between-files'?1:0);
    if(phase==='upload')expect(d.find).not.toHaveBeenCalled();
    expect(said.some(([text])=>text.startsWith('Added '))).toBe(false);
  });

  test('only videos and pictures; a failed upload is said and the others still go', async () => {
    const none = deps();
    expect(await addFiles([file('notes.txt')], none.d)).toEqual([]);
    expect(none.d.upload).not.toHaveBeenCalled();
    expect(none.said).toEqual([['Only videos (.mp4, .mov, .m4v, .mkv) and pictures (.png, .jpg, .tif, .bmp, .webp) can be added.', 'error']]);
    const mixed = deps();
    const added = await addFiles([file('broken.png'), file('onion.png'), file('notes.txt')], mixed.d);
    expect(mixed.uploads).toEqual(['broken.png', 'onion.png']);
    expect(added.map((a) => a.id)).toEqual(['a12']);
    expect(mixed.said.at(-1)).toEqual(['broken.png was not added: That file does not open as a picture', 'error']);
    expect(mixed.said.some(([text]) => text === 'Adding onion.png (2 of 2)…')).toBe(true);
  });
});

// --- the views ----------------------------------------------------------------------------------------------------

let dom: SessionDOM;
beforeEach(() => { dom = sessionDOM(); vi.useFakeTimers(); vi.setSystemTime(NOW * 1000); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function section(fetcher?: typeof fetch) {
  const model = new SourcesModel();
  model.setSession('s1');
  const calls: unknown[][] = [];
  const actions: SourcesActions = {
    open: (target, a) => calls.push(['open', target, a?.id]),
    remove: (r) => calls.push(['remove', r.id]),
    setMeta: (id, set) => calls.push(['meta', id, set]),
    addFiles: (files) => calls.push(['add', files.length]),
    openSession: (id) => calls.push(['session', id]),
    fromAssets: () => calls.push(['assets']),
  };
  const root = dom.node('sidebar-sources');
  const view = new SourcesSection(root as unknown as HTMLElement, model, actions, { fetcher, clock: () => NOW * 1000 });
  return { model, calls, root, view };
}
const rowsOf = (root: SessionNode) => (root.querySelectorAll('.source-row') as SessionNode[]).map((n) => n.dataset.source);
const rowNode = (root: SessionNode, id: string) => (root.querySelectorAll('.source-row') as SessionNode[]).find((n) => n.dataset.source === id)!;

describe("the side bar's Assets: this session's", () => {
  test("its assets under their specimens (the live camera is no row: the header's camera control); the one on stage marked; a click puts one on stage", () => {
    const { model, calls, root, view } = section();
    expect(rowsOf(root)).toEqual([]);
    expect((root.querySelector('.source-empty') as SessionNode).textContent).toBe('Reading this project’s assets…');
    model.apply(wire('s1', [row('a9', { specimen: 'sperm', title: 'Sperm, 2 pm', optics: { objective: 40 } }), row('a8', { kind: 'still', title: 'Smear' })], 'a9'));
    expect(rowsOf(root)).toEqual(['a9', 'a8']);
    expect((root.querySelectorAll('.source-group') as SessionNode[]).map((n) => n.textContent)).toEqual(['Sperm', UNNAMED]);
    expect((rowNode(root, 'a9').querySelector('.source-meta') as SessionNode).textContent).toBe('Recording · 1:44 am · 3:12 · 40x');
    view.setStage('a9', null);
    expect(rowNode(root, 'a9').getAttribute('aria-current')).toBe('true');
    view.setStage('a9', 'a8'); // being put on stage
    expect((rowNode(root, 'a8').querySelector('.source-meta') as SessionNode).textContent).toBe('Opening…');
    fire(rowNode(root, 'a8').querySelector('.source-open') as SessionNode, 'click');
    fire(rowNode(root, 'a9').querySelector('.source-remove') as SessionNode, 'click');
    expect(calls).toEqual([['open', 'a8', 'a8'], ['remove', 'a9']]);
    model.apply(wire('s1', [], 'live'));
    expect((root.querySelector('.source-empty') as SessionNode).textContent).toBe('No pictures or recordings yet.');
  });

  test('"Also in:" names the other sessions that use the asset, each a link that opens it (Q27)', () => {
    const { model, calls, root } = section();
    model.apply(wire('s1', [
      row('a9', { alsoIn: [{ id: '2026-10-12', label: 'Oct 12' }, { id: '2026-10-12.2', label: 'Sperm motility' }] }),
      row('a8', { alsoIn: [] }), row('a7')]));
    const line = rowNode(root, 'a9').querySelector('.source-also') as SessionNode;
    expect(line.textContent).toBe('Also in: Oct 12 · Sperm motility');
    const links = line.querySelectorAll('.source-also-link') as SessionNode[];
    expect(links.map((l) => l.textContent)).toEqual(['Oct 12', 'Sperm motility']);
    fire(links[1], 'click');
    expect(calls).toEqual([['session', '2026-10-12.2']]);
    expect(rowNode(root, 'a8').querySelector('.source-also')).toBeNull(); // used nowhere else, or an older server
    expect(rowNode(root, 'a7').querySelector('.source-also')).toBeNull();
    // A changed list is drawn again.
    model.apply(wire('s1', [row('a9', { alsoIn: [{ id: '2026-10-11', label: 'Blood smear' }] })]));
    expect((rowNode(root, 'a9').querySelector('.source-also') as SessionNode).textContent).toBe('Also in: Blood smear');
  });

  test("a row's details are the asset's own fields: each change is sent as it is made; a redraw waits while one is typed in", () => {
    const { model, calls, root } = section();
    model.apply(wire('s1', [row('a9', { specimen: 'sperm', optics: { objective: 63 } })]));
    const more = rowNode(root, 'a9').querySelector('.source-more') as SessionNode;
    expect(more.getAttribute('aria-expanded')).toBe('false');
    fire(more, 'click');
    const form = rowNode(root, 'a9').querySelector('.source-details') as SessionNode;
    const inputs = form.querySelectorAll('input') as SessionNode[];
    const [specimen, other, title] = inputs;
    const select = form.querySelector('select') as SessionNode;
    expect([specimen.value, select.value, other.value, other.hidden, title.value]).toEqual(['sperm', 'other', '63', false, 'Asset a9']);
    specimen.value = '  sperm sample ';
    fire(specimen, 'change');
    select.value = '40';
    fire(select, 'change');
    select.value = '';
    fire(select, 'change');
    title.value = '';
    fire(title, 'change');
    const notes = form.querySelector('textarea') as SessionNode;
    notes.value = 'From the wet mount';
    fire(notes, 'change');
    expect(calls).toEqual([['meta', 'a9', { specimen: 'sperm sample' }], ['meta', 'a9', { objective: 40 }], ['meta', 'a9', { objective: null }],
      ['meta', 'a9', { title: null }], ['meta', 'a9', { notes: 'From the wet mount' }]]);
    // The server's echo while the notes are being typed in: the field is kept, and the list is drawn once it is left.
    Object.defineProperty(document, 'activeElement', { get: () => SessionNode.active, configurable: true });
    notes.focus();
    model.apply(wire('s1', [row('a9', { specimen: 'sperm sample', notes: 'From the wet mount' }), row('a10')]));
    expect(rowsOf(root)).toEqual(['a9']);
    notes.blur();
    fire(root, 'focusout');
    vi.runOnlyPendingTimers();
    expect(rowsOf(root)).toEqual(['a9', 'a10']);
  });

  test('Add…: from this Mac picks files; From All assets opens the library\'s page', () => {
    const { calls, root } = section();
    const add = root.querySelector('.sources-add') as SessionNode;
    fire(add, 'click');
    expect(add.getAttribute('aria-expanded')).toBe('true');
    const [, fromAssets] = root.querySelectorAll('.sources-add-choice') as SessionNode[];
    fire(fromAssets, 'click');
    expect(calls).toEqual([['assets']]);
    expect(add.getAttribute('aria-expanded')).toBe('false');
    expect(root.querySelector('.library-picker')).toBeNull(); // the library is All assets', never a second copy here
  });
});

describe('the empty stage', () => {
  function stage(fetcher: typeof fetch) {
    const asked: unknown[][] = [];
    const root = dom.node('session-home', 'section');
    const home = new SessionHome(root as unknown as HTMLElement, {
      example: () => asked.push(['example']),
      live: () => asked.push(['live']), open: (a) => asked.push(['open', a.id]), addFiles: (f) => asked.push(['add', f.length]),
      library: () => asked.push(['library']), reconnect: async () => { asked.push(['reconnect']); return 'Reconnecting'; },
      troubleshoot: () => asked.push(['troubleshoot']), noCamera: () => asked.push(['no-camera']), projects: () => asked.push(['projects']),
      cameraPage: () => 'http://127.0.0.1:8765/camera/',
    }, { fetcher, clock: () => NOW * 1000 });
    return { asked, root, home };
  }
  const recent = (count: number) => vi.fn(async () => ({ ok: true, json: async () => ({
    assets: Array.from({ length: count }, (_, i) => asset(`a${i + 1}`)) }) })) as unknown as typeof fetch;

  test('or a question: research needs no specimen, starts only with words, and is handed over as asked', () => {
    const asked: unknown[][] = [];
    const root = dom.node('session-home', 'section');
    new SessionHome(root as unknown as HTMLElement, {
      example: () => {}, live: () => {}, open: () => {}, addFiles: () => {}, library: () => {}, reconnect: async () => '',
      troubleshoot: () => {}, noCamera: () => {}, cameraPage: () => '', research: (question) => { asked.push(['research', question]); return !question.includes('offline'); },
    }, { fetcher: recent(0), clock: () => NOW * 1000 });
    const form = root.querySelector('.home-research') as SessionNode, box = root.querySelector('.home-research-question') as SessionNode & { value: string };
    const start = root.querySelector('.home-research-start') as SessionNode & { disabled: boolean };
    expect(form.hidden).toBeFalsy();expect(root.textContent).toContain('New researcher · start with a question');
    expect(start.disabled).toBe(true);
    box.value = '  Which training data transfers to new cell lines?  ';fire(box, 'input');expect(start.disabled).toBe(false);
    fire(form, 'submit');
    expect(asked).toEqual([['research', 'Which training data transfers to new cell lines?']]);expect(box.value).toBe('');
    // Not sent (no connection): the question stays to send again.
    box.value = 'offline question';fire(box, 'input');fire(form, 'submit');expect(box.value).toBe('offline question');
    // Without a way to start research (the classic layout's caller gives none), the row is not offered.
    const plain = stage(recent(0));expect((plain.root.querySelector('.home-research') as SessionNode).hidden).toBe(true);
  });

  test('the example is an explicit choice and the normal image and camera choices remain available', async () => {
    const { asked, root, home } = stage(recent(0));
    home.open('choose');
    expect(root.textContent).toContain('New here?');
    expect(root.textContent).toContain('No account or API key needed.');
    expect(asked).toEqual([]);
    // Named as what it starts (the guide), and an existing Project one quiet step away.
    expect(home.exampleButton.textContent).toBe('Try the guided example');
    fire(root.querySelector('.home-projects') as SessionNode, 'click');
    expect(asked).toEqual([['projects']]);asked.length = 0;
    fire(home.exampleButton as unknown as SessionNode, 'click');
    expect(asked).toEqual([['example']]);
    expect(home.add.getAttribute('aria-label')).toContain('Open a picture or video');
    expect(home.connect.textContent).toBe('Connect a camera');
  });

  test("a new session chooses: the session's name, the live camera (Use live camera), adding one, and the newest of your assets", async () => {
    const fetcher = recent(9);
    const { asked, root, home } = stage(fetcher);
    expect(home.isOpen()).toBe(false);
    home.setTitle('Untitled project');
    home.open('choose');
    home.setLive({ tone: 'live', words: 'Live · iPhone', help: '' });
    expect(root.hidden).toBe(false);
    expect(root.dataset.mode).toBe('choose');
    expect((root.querySelector('.home-title') as SessionNode).textContent).toBe('Untitled project');
    expect((root.querySelector('.home-camera-words') as SessionNode).textContent).toBe('Live · iPhone');
    expect(home.liveButton.hidden).toBe(false);
    expect(home.connect.hidden).toBe(true); // live: nothing to connect
    expect(home.reconnectButton.hidden).toBe(true);
    await vi.waitFor(() => expect((root.querySelectorAll('.home-recent-item') as SessionNode[]).length).toBe(6));
    expect(fetcher).toHaveBeenCalledWith('/api/library?q=&scope=mine&limit=6', { cache: 'no-store' }); // the user's newest
    // A choice is asked once.
    const tile = (root.querySelectorAll('.home-recent-item') as SessionNode[])[0];
    fire(tile, 'click');
    fire(tile, 'click');
    expect(asked).toEqual([['open', 'a1']]);
    // It failed: the box stays, says why, and takes another choice.
    home.setBusy(null);
    home.setStatus('The camera bridge could not play it', 'error');
    fire(home.liveButton as unknown as SessionNode, 'click');
    expect(asked).toEqual([['open', 'a1'], ['live']]);
    expect((root.querySelector('.home-camera-words') as SessionNode).textContent).toBe('Going live…');
    fire(home.libraryButton as unknown as SessionNode, 'click');
    expect(asked.at(-1)).toEqual(['library']);
    home.close();
    expect(root.hidden).toBe(true);
  });

  test('the live camera with no picture: its problem in a line, Connect a camera (the camera page) and Reconnect; no Use live camera', async () => {
    const { asked, root, home } = stage(recent(0));
    home.open('camera');
    home.setLive({ tone: 'off', words: 'No camera detected', help: 'Connect the iPhone.' });
    home.setProblem({ words: 'Not live: no picture is arriving.', help: 'Unlock the iPhone once.', detail: 'capture timed out' });
    expect(root.dataset.mode).toBe('camera');
    expect((root.querySelector('.home-camera-words') as SessionNode).textContent).toBe('No picture is arriving');
    expect((root.querySelector('.home-camera-help') as SessionNode).textContent).toBe('Unlock the iPhone once.');
    expect(home.liveButton.hidden).toBe(true);
    expect(home.connect.hidden).toBe(false);
    expect(home.connect.href).toBe('http://127.0.0.1:8765/camera/');
    expect(home.connect.className).toContain('mm-primary');
    fire(home.reconnectButton as unknown as SessionNode, 'click');
    await vi.waitFor(() => expect((root.querySelector('.home-status') as SessionNode).textContent).toBe('Reconnecting'));
    fire((root.querySelector('.home-troubleshoot') as SessionNode), 'click');
    expect(asked).toEqual([['reconnect'], ['troubleshoot']]);
    await vi.waitFor(() => expect(root.querySelector('.home-recent-empty')).not.toBeNull()); // none yet: said, with the library
    expect(cameraLine('Not live: Blackmagic Camera isn\'t open.')).toBe("Blackmagic Camera isn't open");
    // The way out, quietly beside the fixes: No camera (Will, 2026-10-15).
    expect(home.filesOnly.hidden).toBe(false);
    fire(home.filesOnly as unknown as SessionNode, 'click');
    expect(asked.at(-1)).toEqual(['no-camera']);
  });

  test('No camera chosen: said calmly, Choose a camera, and nothing to connect, reconnect or go live with', () => {
    const { asked, root, home } = stage(recent(0));
    for (const mode of ['choose', 'camera'] as const) {
      home.open(mode);
      home.setLive(liveState({ connected: true, health: 'no_camera', feed: { source: 'camera', footage: null, bridge: { state: 'no_camera' } } }));
      home.setProblem({ words: 'Not live: no picture is arriving.', help: 'Unlock the iPhone once.', detail: '' }); // never shown
      expect((root.querySelector('.home-camera-words') as SessionNode).textContent).toBe('No camera in use');
      expect((root.querySelector('.home-camera-help') as SessionNode).textContent).toBe('Pictures and videos only. Choose a camera when you want one.');
      expect((root.querySelector('.home-camera') as SessionNode).dataset.tone).toBe('none');
      expect([home.liveButton.hidden, home.connect.hidden, home.reconnectButton.hidden, home.filesOnly.hidden]).toEqual([true, true, true, true]);
      expect(home.chooseCamera.hidden).toBe(false);
      home.close();
    }
    fire(home.chooseCamera as unknown as SessionNode, 'click');
    expect(asked).toEqual([['troubleshoot']]); // the camera menu, with its source switch
  });
});

describe('what joins Assets on its own, said once', () => {
  // A row made in the session, as the server lists it (sources.rows): joined on its own, never opened there.
  const made = (id: string, from: string) => row(id, { from, joined: true, opened: null });
  const state = (session: string, sources: SourceRow[] = [], removed: string[] = []) => ({ session, onStage: null, sources, removed });
  test('a snapshot, a Record video or a clearer picture new to the same session; not what was opened, nor another session', () => {
    const before = state('s1', [row('a1', { from: 'added' })]);
    const after = state('s1', [row('a1', { from: 'added' }), made('a2', 'snapshot'), row('a3', { from: 'added' })]);
    expect(joinedSince(before, after).map((j) => [j.row.id, j.words])).toEqual([['a2', 'Snapshot']]);
    expect(joinedSince(before, state('s1', [made('a4', 'record')]))[0].words).toBe('Recording');
    expect(joinedSince(before, state('s1', [made('a5', 'clearer')]))[0].words).toBe('Clearer picture');
    expect(joinedSince(state('s0'), state('s1', [made('a2', 'snapshot')]))).toEqual([]); // a switch, not a join
    expect(joinedSince(null, after)).toEqual([]); // the first read says nothing
  });

  test('another session\'s snapshot opened here is never said to be saved here, nor one the user\'s Undo brought back', () => {
    const before = state('s1', [row('a1', { from: 'added' })], ['a9']);
    // Added from All assets, a recent tile or an "Also in" link: its `from` is snapshot, but it was opened, not made, here.
    const opened = row('a7', { from: 'snapshot', joined: false, opened: at(3) });
    expect(joinedSince(before, state('s1', [opened, row('a1', { from: 'added' })]))).toEqual([]);
    // Undo of × on a snapshot made here: back in the list, but not newly saved.
    expect(joinedSince(before, state('s1', [made('a9', 'snapshot'), row('a1', { from: 'added' })]))).toEqual([]);
  });
});
