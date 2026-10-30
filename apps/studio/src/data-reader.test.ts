import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { DataReader, byLine, countWords, howRows, kindWord, makerOf, readerDocument, readerShape, readerTitle, readerWidth, researcherOf, sameItem, sourcesItem, switchLabel, thingView, windowRows, workOf,
  type ReaderDeps, type ReaderEntry, type ReaderPage } from './data-reader';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const researchers = [{ rid: 'r810833', name: 'Researcher 3', day: '2026-10-25' }, { rid: 'r802191', name: 'Researcher 1', day: '2026-10-25' }];
const entry = (fields: Partial<ReaderEntry>): ReaderEntry => ({ id: 'e8', kind: 'population', title: 'Bright regions · Bright objects · 02:35', summary: '', state: 'saved',
  actions: [], day: '2026-10-25.4', version: 0, ...fields } as ReaderEntry);
// 2026-10-26 10:41 local
const now = new Date(2026, 9, 26, 12, 0).getTime() / 1000, today = new Date(2026, 9, 26, 10, 41).getTime() / 1000;

describe('what the reader shows for each kind', () => {
  it('draws each kind with its own renderer, the contract’s new kinds included', () => {
    expect(readerShape(entry({ kind: 'population' }))).toBe('masks');
    expect(readerShape(entry({ kind: 'result', table: true }))).toBe('table');
    expect(readerShape(entry({ kind: 'result-table' }))).toBe('table');
    expect(readerShape(entry({ kind: 'tracks' }))).toBe('table');
    expect(readerShape(entry({ kind: 'result' }))).toBe('answer');
    for (const kind of ['research', 'lesson', 'video', 'figure', 'presentation']) expect(readerShape(entry({ kind }))).toBe('writeup');
    expect(readerShape(entry({ kind: 'finding-table' }))).toBe('finding-table');
    expect(readerShape(entry({ kind: 'finding-chart' }))).toBe('finding-chart');
    expect(readerShape(entry({ kind: 'source' }))).toBe('source');
    expect(readerShape(entry({ kind: 'annotation' }))).toBe('note');
    expect(readerShape(entry({ kind: 'recording' }))).toBe('recording');
    expect(readerShape(entry({ kind: 'asset', asset: { kind: 'recording' } as ReaderEntry['asset'] }))).toBe('recording');
    expect(readerShape(entry({ kind: 'asset', asset: { kind: 'still' } as ReaderEntry['asset'] }))).toBe('picture');
    expect(readerShape(entry({ kind: 'bookmark' }))).toBe('other');
    // A document the Assistant wrote from the Project's records (documents.py): its own renderer, a Document.
    expect(readerShape(entry({ kind: 'result', media: 'document' }))).toBe('document');
    expect([kindWord(entry({ kind: 'result', media: 'document' })), thingView(entry({ kind: 'result', media: 'document' }))]).toEqual(['Document', 'Document']);
  });
  it('reads a document’s sections leniently, each paragraph with the records it cites', () => {
    expect(readerDocument({ values: { leftOut: { paragraphs: 2 }, document: { title: 'Findings', sections: [
      { heading: 'Cells', paragraphs: [{ text: 'There are 142 cells.', cites: [{ id: 'd4', day: 'p', title: 'Count · 14:02' }, { title: 'no id' }] }, { text: '' }] },
      { heading: 'Empty', paragraphs: [] }, 'not a section'] } } })).toEqual({ title: 'Findings', leftOut: 2, sections: [
      { heading: 'Cells', paragraphs: [{ text: 'There are 142 cells.', cites: [{ id: 'd4', day: 'p', title: 'Count · 14:02' }] }] }] });
    expect(readerDocument({ values: { title: 'no document' } })).toBeNull();
    expect(readerDocument(undefined)).toBeNull();
  });
  it('reads an experiment’s results: its byline, a section’s table and its figure, each cell as kept (experiments.py)', () => {
    const doc = readerDocument({ values: { byline: 'Made by the app from Experiment 1’s run records', document: { title: 'Experiment 1', sections: [
      { heading: 'The comparison', paragraphs: [{ text: 'On 2 frames.', cites: [] }],
        table: { columns: ['Frame', 'Model', 'Cells', 'Reused'], rows: [[1, 'Cellpose-SAM', 41, false], ['all', 'Cellpose-SAM', 40.5, { x: 1 }], 'not a row'] },
        figure: { picture: 0, caption: 'The outlines side by side' } },
      { heading: 'Bad parts', paragraphs: [{ text: 'Kept.', cites: [] }], table: { columns: 'no', rows: [] }, figure: { picture: -1 } }] } } });
    expect(doc?.byline).toBe('Made by the app from Experiment 1’s run records');
    expect(doc?.sections[0].table).toEqual({ columns: ['Frame', 'Model', 'Cells', 'Reused'], rows: [[1, 'Cellpose-SAM', 41, false], ['all', 'Cellpose-SAM', 40.5, null]] });
    expect(doc?.sections[0].figure).toEqual({ picture: 0, caption: 'The outlines side by side' });
    expect(doc?.sections[1]).toEqual({ heading: 'Bad parts', paragraphs: [{ text: 'Kept.', cites: [] }] });
  });
  it('names the kind and the first view as the mock does: Outlines, Table, Page for a write-up, Source for a source', () => {
    expect([kindWord(entry({})), thingView(entry({}))]).toEqual(['Outlines', 'Outlines']);
    expect(kindWord(entry({ kind: 'result-table' }))).toBe('Table');
    expect([kindWord(entry({ kind: 'research' })), thingView(entry({ kind: 'research' }))]).toEqual(['Report', 'Page']);
    // A report whose newest form is its presentation (S4's forms) says so.
    expect(kindWord(entry({ kind: 'research', forms: [{ label: 'Presentation', outputId: 'o1', state: 'writing' }, { label: 'Findings v1' }] }))).toBe('Presentation');
    expect(kindWord(entry({ kind: 'lesson' }))).toBe('Lesson');
    expect([kindWord(entry({ kind: 'source', values: { url: 'https://nature.com/x', site: 'nature.com', title: 'Cellpose' } })), thingView(entry({ kind: 'source' }))]).toEqual(['Source', 'Source']);
    expect(kindWord(entry({ kind: 'source', values: { url: 'u', site: 's', title: 't', picture: '/p.jpg' } }))).toBe('Web picture');
    expect(kindWord(entry({ kind: 'named-outline' }))).toBe('Named outline');
  });
  it('titles a result by its first part, the maker and time being said on the quiet line', () => {
    expect(readerTitle('Bright regions · Bright objects · 02:35')).toBe('Bright regions');
    expect(readerTitle('Cell outlines, for Researcher 1 · Cellpose-SAM · 22:03')).toBe('Cell outlines');
    expect(readerTitle('Field A looks denser than Field B')).toBe('Field A looks denser than Field B');
    // An input is named by its name; its credit goes on the quiet line.
    const input = entry({ id: 'a1', kind: 'asset', title: 'Human cells · David Root (CC0)', version: undefined, asset: { kind: 'still' } as ReaderEntry['asset'] });
    expect([readerTitle(input.title, 'asset'), byLine(input, null)]).toEqual(['Human cells', 'Picture · David Root (CC0)']);
  });
});

describe('who made it: only what the record says', () => {
  it('reads the server’s maker when it sends one (S2)', () => {
    expect(makerOf(entry({ maker: { kind: 'assistant', name: 'The Assistant' } }), researchers)).toEqual({ kind: 'assistant', name: 'The Assistant' });
    expect(makerOf(entry({ maker: { kind: 'you', name: 'You' } }), researchers)?.kind).toBe('you');
  });
  it('without one, names the researcher a thing was made for or is, and never says You by default', () => {
    expect(makerOf(entry({ madeBy: { for: 'Researcher 1' } as ReaderEntry['madeBy'] }), researchers)).toEqual({ kind: 'researcher', id: 'r802191', name: 'Researcher 1' });
    expect(makerOf(entry({ kind: 'research', id: 'r810833' }), researchers)).toEqual({ kind: 'researcher', id: 'r810833', name: 'Researcher 3' });
    expect(makerOf(entry({ kind: 'lesson', id: 'r810833:o2' }), researchers)?.name).toBe('Researcher 3');
    expect(makerOf(entry({ id: 'r810833#o1', kind: 'presentation' }), researchers)?.name).toBe('Researcher 3');
    expect(makerOf(entry({ madeBy: { by: 'user' } as ReaderEntry['madeBy'] }), researchers)).toEqual({ kind: 'you', name: 'You' });
    // An older run's requestedBy is set for the Assistant's runs too: not a maker (the server's maker_of agrees).
    expect(makerOf(entry({ madeBy: { requestedBy: 'user' } as ReaderEntry['madeBy'] }), researchers)).toBeNull();
    // A run whose record does not say who started it: no maker (ADR 0006), never "You".
    expect(makerOf(entry({ madeBy: { implementation: 'basic-bright-objects', origin: 'user-chosen' } as ReaderEntry['madeBy'] }), researchers)).toBeNull();
    expect(researcherOf(entry({ madeBy: { for: 'Researcher 9' } as ReaderEntry['madeBy'] }), researchers)).toBeNull();
  });
  it('says its kind, maker and time on one quiet line', () => {
    expect(byLine(entry({ at: today, maker: { kind: 'you', name: 'You' } }), { kind: 'you', name: 'You' }, 'Outlines', now)).toMatch(/^Outlines · You · 10:41/);
    expect(byLine(entry({ at: today }), null, 'Outlines', now)).toMatch(/^Outlines · 10:41/);
  });
});

describe('How it was made', () => {
  it('without `how`, says only what the entry records: its input, the model by name, who and when, the Tabs it is on', () => {
    const rows = howRows(entry({ at: today, madeBy: { implementation: 'basic-bright-objects' } as ReaderEntry['madeBy'] }),
      null, { method: 'Bright objects', input: { id: 'a1', title: 'Human cells' }, places: [{ title: 'Main' }], now }, researchers);
    expect(rows.map((row) => row.label)).toEqual(['From', 'Made with', 'Made', 'Open on']);
    expect(rows[0].refs?.[0]).toMatchObject({ id: 'a1', title: 'Human cells' });
    expect(rows[1].words).toEqual(['Bright objects']);
    expect(rows[2].words?.[0]).toMatch(/^Today at 10:41/);
    expect(rows[3].words).toEqual(['Main']);
    // Nothing recorded: nothing said (no From, no Made with), and an item on no Tab says so.
    const bare = howRows(entry({}), null, { places: [] }, researchers);
    expect(bare.map((row) => [row.label, row.quiet])).toEqual([['Open on', 'Not open on a Tab']]);
  });
  it('with the contract’s `how` (S5), lists its versions, writer with Talk, what it was made from and its sources', () => {
    const report = entry({ id: 'r810833', kind: 'research', title: 'Researcher 3’s notes', at: today });
    const rows = howRows(report, {
      maker: { kind: 'researcher', id: 'r810833', name: 'Researcher 3' }, at: today,
      versions: [{ label: 'Presentation', outputId: 'o1', state: 'writing' }, { label: 'Findings v1', version: 1, at: today }],
      from: [{ id: 'e6', kind: 'population', role: 'analysis', title: 'Bright regions', maker: { kind: 'researcher', id: 'r810833', name: 'Researcher 3' } }],
      sources: [] }, { now }, researchers);
    expect(rows.map((row) => row.label)).toEqual(['Versions', 'Written by', 'Made from', 'Sources']);
    expect(rows[0].versions?.[0]).toMatchObject({ label: 'Presentation', state: 'writing' });
    expect(rows[1]).toMatchObject({ talk: { rid: 'r810833', name: 'Researcher 3' } });
    expect(rows[2].refs?.[0].id).toBe('e6');
    expect(rows[3]).toMatchObject({ quiet: 'None cited' });
  });
  it('says the Assistant’s run with the words you asked, its settings and where it ran', () => {
    const rows = howRows(entry({ at: today }), { maker: { kind: 'assistant', name: 'The Assistant' }, at: today, asked: 'Outline every cell',
      method: { name: 'Cellpose-SAM', where: 'On this Mac' }, settings: [{ name: 'Diameter', value: 30 }], usedIn: [{ id: 'r1', kind: 'research', role: 'writeup', title: 'Report' }] },
    { places: [], now }, researchers);
    expect(rows.find((row) => row.label === 'Made with')).toMatchObject({ words: ['Cellpose-SAM · On this Mac'], quiet: 'Diameter 30' });
    expect(rows.find((row) => row.label === 'Made by')?.words?.[0]).toMatch(/^The Assistant, when you asked “Outline every cell”, today at/);
    expect(rows.find((row) => row.label === 'Used in')?.refs?.[0].title).toBe('Report');
  });
  it('marks a source as web data: background, not proof', () => {
    const rows = howRows(entry({ kind: 'source', id: 'r810833:source-abc', untrusted: true, values: { url: 'u', site: 's', title: 't' } }),
      { citedIn: [{ id: 'r810833', kind: 'research', role: 'writeup', title: 'Measuring nuclei' }] }, {}, researchers);
    expect(rows.map((row) => row.label)).toEqual(['Found by', 'Cited in', 'Note']);
  });
});

describe('the reader’s room and rows', () => {
  it('takes the mock’s 600 px beside Data, narrower when the Stage would keep less, and 540 px below 1200 px', () => {
    expect(readerWidth(1440, 72, 340)).toBe(600);
    expect(readerWidth(1280, 72, 340)).toBe(468);
    expect(readerWidth(1200, 72, 340)).toBe(440);
    expect(readerWidth(1100, 72, 0)).toBe(540);
    expect(readerWidth(800, 72, 0)).toBe(408);
  });
  it('draws only the rows in view and a few either side', () => {
    expect(windowRows(271, 28, 0, 280, 8)).toEqual({ start: 0, end: 26 });
    expect(windowRows(271, 28, 2800, 280, 8)).toEqual({ start: 92, end: 118 });
    expect(windowRows(271, 28, 99999, 280, 8).end).toBe(271);
    expect(windowRows(0, 28, 0, 280)).toEqual({ start: 0, end: 0 });
  });
  it('knows the same item at the same revision', () => {
    expect(sameItem({ id: 'e8', day: 'p', version: 0 }, { id: 'e8', day: 'p', version: 0 })).toBe(true);
    expect(sameItem({ id: 'e8', day: 'p', version: 0 }, { id: 'e8', day: 'p', version: 1 })).toBe(false);
  });
});

describe('the reader on screen', () => {
  const deps = (over: Partial<ReaderDeps> = {}): ReaderDeps => ({
    researchers: () => researchers, working: () => false, method: () => 'Bright objects', input: () => null, places: () => [], tab: () => 'main',
    close: vi.fn(), back: vi.fn(), talk: vi.fn(), openInNewTab: vi.fn(), showOnPicture: vi.fn(async () => {}), onPicture: () => false,
    addToTab: vi.fn(), goTo: vi.fn(), routes: () => [{ label: 'Open masks window', act: () => {} }],
    trash: () => ({ label: 'Move to trash', disabled: true, act: () => {} }), editNote: vi.fn(), open: vi.fn(), ...over });
  const mount = () => {
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {}))); // the body's content never arrives: the header is what is drawn at once
    vi.stubGlobal('requestAnimationFrame', dom.raf);
    return dom;
  };
  const texts = (root: TestElement, cls: string) => root.querySelectorAll(`.${cls}`).map((node) => node.textContent);
  it('opens outlines with their header, views and actions at once, Show on picture only for outlines', () => {
    mount();
    const reader = new DataReader(deps(), 'side'), root = reader.root as unknown as TestElement;
    reader.show(entry({ at: today }));
    expect(root.id).toBe('data-reader'); // the screen report's root identity: never empty
    expect((new DataReader(deps(), 'tab').root as unknown as TestElement).id).toBe('data-reader-tab');
    expect(root.hidden).toBe(false);
    expect(root.dataset.readerOpen).toBe('true');
    expect(JSON.parse(root.dataset.readerResource)).toEqual({ day: '2026-10-25.4', id: 'e8', version: 0 });
    expect(texts(root, 'rd-title')).toEqual(['Bright regions']);
    expect(root.querySelectorAll('[data-view]').map((tab) => [tab.textContent, tab.getAttribute('aria-selected')])).toEqual([['Outlines', 'true'], ['How it was made', 'false']]);
    expect(texts(root, 'rd-picture')).toEqual(['Show on picture']);
    expect(texts(root, 'rd-new-tab')).toEqual(['Open in new Tab']);
    expect(root.querySelectorAll('.rd-talk')).toEqual([]);
    expect(root.dataset.displayState).toBe('loading');
    reader.show(entry({ kind: 'result', table: true, id: 'd9', title: 'Areas · 10:00' }));
    expect(root.querySelectorAll('.rd-picture')).toEqual([]);
  });
  it('opens the exact inspection in another retained Tab and preserves its source', async () => {
    mount();
    const id = 'p' + '1'.repeat(32), report = 'p' + '2'.repeat(32), day = '2026-10-25.4';
    const digest = 'a'.repeat(64), reportDigest = 'b'.repeat(64);
    const fetcher = vi.fn(async (url: string) => {
      if (String(url).startsWith(`/api/scientific-projects/${report}?`))
        return {ok: true, json: async () => ({project: {id: report, session: day, revision: 3, digest: reportDigest,
          document: {kind: 'matrix-result', title: 'Recorded inspection', state: {}}}})};
      if (String(url).startsWith(`/api/scientific-projects/${id}?`))
        return {ok: true, json: async () => ({project: {id, session: day, revision: 1, digest,
          document: {kind: 'source-file', title: 'Original dataset', state: {sourceFile: {
            format: 1, name: 'original.bin', size: 3, sha256: 'c'.repeat(64)}}}}})};
      return {ok: false, json: async () => ({error: 'No fixture reader'})};
    });
    vi.stubGlobal('fetch', fetcher);
    const openInNewTab = vi.fn(), reader = new DataReader(deps({openInNewTab}), 'tab');
    const root = reader.root as unknown as TestElement;
    reader.show(entry({id, day, kind: 'project', title: 'Original dataset', version: 1, source: {sha256: digest},
      material: {kind: 'matrix', origin: 'imported', input: true, section: 'files', category: 'import',
        record: false, dependencies: [], shape: [2, 3], inspection: {status: 'reported', total: 1,
          reports: [{id: report, session: day, revision: 3, digest: reportDigest}]}}}));
    await vi.waitFor(() => expect(root.dataset.displayState).toBe('rendered'));
    root.querySelector('.rd-material-inspection')!.querySelector('button')!.dispatchEvent(new Event('click'));
    await vi.waitFor(() => expect(openInNewTab).toHaveBeenCalledWith(expect.objectContaining({
      id: report, day, version: 3, kind: 'project', source: {sha256: reportDigest}})));
    expect(reader.current()?.id).toBe(id);
    expect(fetcher.mock.calls.some(([url]) => String(url).includes(`${report}?session=${day}&revision=3`))).toBe(true);
    reader.clear();
  });
  it.each(['before response', 'after response', 'show again'])('enriches a restored exact source %s without resetting its preview', async (timing) => {
    mount();
    const id = 'p' + '3'.repeat(32), report = 'p' + '4'.repeat(32), day = '2026-10-25.4';
    const digest = 'd'.repeat(64);
    let release!: () => void;
    const ready = new Promise<void>(resolve => { release = resolve; });
    const fetcher = vi.fn(async () => {
      await ready;
      return {ok: true, json: async () => ({project: {id, session: day, revision: 1, digest,
        document: {kind: 'source-file', title: 'Retained source', state: {sourceFile: {
          format: 1, name: 'source.bin', size: 3, sha256: 'c'.repeat(64)}}}}})};
    });
    vi.stubGlobal('fetch', fetcher);
    const changed = vi.fn();
    const reader = new DataReader(deps({changed}), 'tab'), root = reader.root as unknown as TestElement;
    const fallback = entry({id, day, version: 1, kind: 'project', title: 'Saved material'});
    const known = {...fallback, material: {kind: 'matrix' as const, origin: 'imported' as const,
      input: true, section: 'files' as const, category: 'import', record: false, dependencies: [],
      shape: [2, 3], inspection: {status: 'reported' as const, total: 1,
        reports: [{id: report, session: day, revision: 3, digest: 'b'.repeat(64)}]}}};
    reader.show(fallback);
    if (timing === 'before response') reader.refresh(known);
    release();
    await vi.waitFor(() => expect(root.dataset.displayState).toBe('rendered'));
    const content = root.querySelector('.rd-body')!, original = content.children[0];
    changed.mockClear();
    if (timing === 'after response') { reader.refresh(known); expect(changed).toHaveBeenCalledTimes(1); }
    if (timing === 'show again') reader.show(known);
    expect(reader.kindLine()).toBe('Matrix');
    const inspection = root.querySelector('.rd-material-inspection')!;
    expect(inspection).toBeTruthy();
    const details = inspection as unknown as HTMLDetailsElement;
    details.open = true;
    changed.mockClear();
    reader.refresh({...known});
    expect(changed).not.toHaveBeenCalled();
    expect(root.querySelector('.rd-material-inspection')).toBe(inspection);
    expect(details.open).toBe(true);
    expect(content.children[0]).toBe(original);
    expect(fetcher).toHaveBeenCalledTimes(1);
    reader.refresh({...known, version: 2, material: {...known.material, shape: [9, 9]}});
    expect(reader.current()?.version).toBe(1);
    expect(root.querySelector('.rd-material-inspection')).toBe(inspection);
    reader.clear();
    reader.refresh(known);
    expect(root.querySelector('.rd-material-inspection')).toBeNull();
  });
  it('gives a researcher’s work Talk to Researcher N, the one action that goes to its Tab', () => {
    mount();
    const talk = vi.fn(), reader = new DataReader(deps({ talk }), 'side'), root = reader.root as unknown as TestElement;
    const report = entry({ id: 'r810833', kind: 'research', title: 'Researcher 3’s notes', version: 2 });
    reader.show(report);
    const button = root.querySelector('.rd-talk')!;
    expect(button.textContent).toBe('Talk to Researcher 3');
    button.dispatchEvent(new Event('click'));
    expect(talk).toHaveBeenCalledWith('r810833', report);
    expect(root.querySelectorAll('[data-view]').map((tab) => tab.textContent)).toEqual(['Page', 'How it was made']);
  });
  it('gives a note Edit, which opens today’s note window on this Tab', () => {
    mount();
    const editNote = vi.fn(), reader = new DataReader(deps({ editNote }), 'side'), root = reader.root as unknown as TestElement;
    const note = entry({ id: 'a0123456789abc', kind: 'annotation', title: 'Field A looks denser than Field B', madeBy: { by: 'user' } as ReaderEntry['madeBy'] });
    reader.show(note);
    expect(root.querySelectorAll('[data-view]').map((tab) => tab.textContent)).toEqual(['Note', 'How it was made']);
    root.querySelector('.rd-edit')!.dispatchEvent(new Event('click'));
    expect(editNote).toHaveBeenCalledWith(note);
    expect(root.querySelector('.rd-by')!.textContent).toMatch(/^Note · You/);
  });
  it('draws a document on its page, each paragraph’s records by name, each opening here', async () => {
    mount();
    const values = { leftOut: { paragraphs: 1 }, document: { title: 'Interesting findings so far', sections: [
      { heading: 'What was measured', paragraphs: [{ text: 'There are 142 cells.', cites: [{ id: 'd4', day: 'p', title: 'Count · 14:02' }, { id: 'r1', title: 'Researcher 1’s notes' }] }] }] } };
    const fetch = vi.fn(async (_url: string) => ({ ok: true, json: async () => ({ entry: {}, detail: { text: 'its text', values } }) }));
    vi.stubGlobal('fetch', fetch);
    const open = vi.fn(), reader = new DataReader(deps({ open }), 'tab'), root = reader.root as unknown as TestElement;
    reader.show(entry({ id: 'd7', kind: 'result', media: 'document', title: 'Document · 14:05', day: 'p', version: undefined, untrusted: true }));
    for (let i = 0; i < 8; i++) await Promise.resolve();
    expect(root.dataset.displayState).toBe('rendered');
    expect(String(fetch.mock.calls[0][0])).toMatch(/^\/api\/resources\/d7\?day=p/);
    const page = root.querySelector('.rd-doc')!;
    expect(page.querySelector('h2')!.textContent).toBe('Interesting findings so far');
    expect(page.querySelectorAll('h3').map((h) => h.textContent)).toEqual(['What was measured']);
    expect(page.querySelectorAll('.rd-doc-cite').map((c) => c.textContent)).toEqual(['Count', 'Researcher 1’s notes']);
    expect(page.querySelector('.rd-doc-from')!.textContent).toBe('From Count and Researcher 1’s notes');
    page.querySelectorAll('.rd-doc-cite')[1].dispatchEvent(new Event('click'));
    expect(open).toHaveBeenCalledWith({ id: 'r1', day: 'p' });
    expect(texts(page, 'rd-note')).toEqual(['Left out: 1 paragraph that cited no record of this Project.']);
  });
  it('in a reader Tab keeps only its views, Talk and ⋯ (its title is on the Stage bar; no × and no Open in new Tab)', () => {
    mount();
    const reader = new DataReader(deps(), 'tab'), root = reader.root as unknown as TestElement;
    reader.show(entry({ id: 'r810833', kind: 'research', title: 'Researcher 3’s notes' }));
    expect(root.querySelectorAll('.rd-close')).toEqual([]);
    expect(root.querySelectorAll('.rd-new-tab')).toEqual([]);
    expect(root.querySelectorAll('.rd-talk')).toHaveLength(1);
    expect(root.querySelectorAll('.rd-more')).toHaveLength(1);
  });
  it('switches to How it was made in the same reader, and × and ‹ Data reach the workspace', () => {
    mount();
    const close = vi.fn(), back = vi.fn(), reader = new DataReader(deps({ close, back }), 'side'), root = reader.root as unknown as TestElement;
    reader.show(entry({}), 'thing', { narrow: true });
    root.querySelector('.rd-back')!.dispatchEvent(new Event('click'));
    expect(back).toHaveBeenCalled();
    root.querySelectorAll('[data-view]')[1].dispatchEvent(new Event('click'));
    expect(reader.currentView()).toBe('how');
    expect(root.dataset.readerView).toBe('How it was made');
    root.querySelector('.rd-close')!.dispatchEvent(new Event('click'));
    expect(close).toHaveBeenCalled();
    reader.clear();
    expect([root.hidden, root.dataset.readerResource]).toEqual([true, undefined]);
  });
  it('turns Show on picture into "On the picture ✓" only once the studio shows the outlines', async () => {
    mount();
    let shown = false, finish!: () => void;
    const showOnPicture = vi.fn(() => new Promise<void>((resolve) => { finish = () => { shown = true; resolve(); }; }));
    const reader = new DataReader(deps({ showOnPicture, onPicture: () => shown }), 'side'), root = reader.root as unknown as TestElement;
    reader.show(entry({}));
    root.querySelector('.rd-picture')!.dispatchEvent(new Event('click'));
    expect(showOnPicture).toHaveBeenCalledWith(expect.objectContaining({ id: 'e8' }), true);
    expect(root.querySelector('.rd-picture')!.textContent).toBe('Showing…');
    finish(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    const button = root.querySelector('.rd-picture')!;
    expect([button.textContent, button.getAttribute('aria-pressed')]).toEqual(['On the picture', 'true']);
  });
  it('says plainly when showing failed, and keeps the button to try again', async () => {
    mount();
    const reader = new DataReader(deps({ showOnPicture: vi.fn(async () => { throw new Error('the outlines were made on another picture'); }) }), 'side');
    const root = reader.root as unknown as TestElement;
    reader.show(entry({}));
    root.querySelector('.rd-picture')!.dispatchEvent(new Event('click'));
    await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
    expect(root.querySelector('.rd-problem')?.textContent).toBe('Not shown: the outlines were made on another picture');
    expect(root.querySelector('.rd-picture')?.textContent).toBe('Show on picture');
  });
});

describe('the workspace opens Data in the reader', () => {
  const study = readFileSync(new URL('./workspace-study.ts', import.meta.url), 'utf8');
  it('opens a Data row (and an input) in the reader on the Tab you are on, never moving you to another Tab', () => {
    expect(study).toContain("if (entry.material?.kind === 'assignment') void openStudy(entry.id); else openInReader(entry as ReaderEntry);");
    expect(study).toContain('()=>openInReader(inputEntry(asset))');
    expect(study).toContain("{label:'How it was made',title:'Who made it, from what and with what, in the reader on the right',act:()=>openInReader(entry as ReaderEntry,'how')}");
  });
  it('shares the right edge with the Assistant: one at a time, the Assistant back on ×, docked pages parked', () => {
    expect(study).toContain('if(open&&readerShown)closeReader(false,false);');
    expect(study).toContain("if (preferences.inspector && !readerVisible() && !controlDeck.visibleOn('right')) setInspector(false); else openRightTab('agent');");
    expect(study).toMatch(/function openInReader[\s\S]*parkControls\('right'\);[\s\S]*readerShown = true;/);
    expect(study).toContain("document.body.style.setProperty('--study-inspector-width', `${reading ? readerNow() : preferences.width}px`);");
  });
  it('takes the Data drawer’s place below 1200 px, with ‹ Data', () => {
    expect(study).toMatch(/readerBack = narrow && fromData;\s*if \(narrow\) \{ if \(dataOpen\) setDataOpen\(false, false, false\);/);
    expect(study).toContain('back: () => press(() => closeReader(true)),');
  });
  it('keeps every route a Data row had, by name, in the reader’s ⋯', () => {
    for (const name of ['Open masks window', 'Open in Measurements', 'Open the scientific viewer', 'Open the data table', 'Open in its Tab',
      'as analysis input', 'Open captured input', 'Show in Inputs', 'Edit in a note window', 'Open in a window']) expect(study).toContain(name);
  });
});

describe('a researcher’s Tab: the same reader on its right (ticket 66 D3)', () => {
  const r3 = { kind: 'researcher' as const, id: 'r810833', name: 'Researcher 3' };
  const made = [entry({ id: 'e6', maker: r3, role: 'analysis', summary: '271 bright regions detected by the basic local method in the original image' }),
    entry({ id: 'r810833:table-1', kind: 'finding-table', title: 'Region areas', maker: r3, role: 'analysis', summary: '271 rows, 2 columns' }),
    entry({ id: 'r810833', kind: 'research', title: 'Bright regions in Human cells', maker: r3, role: 'writeup', forms: [{ label: 'Presentation', outputId: 'o1' }] }),
    entry({ id: 'r810833:o3', kind: 'lesson', title: 'Researcher 3’s lesson', maker: r3, role: 'writeup' }),
    entry({ id: 'r810833:source-0123456789ab', kind: 'source', maker: r3, role: 'source' }),
    entry({ id: 'u1', kind: 'outcome', maker: r3, role: null }),
    entry({ id: 'e8', maker: { kind: 'you', name: 'You' }, role: 'analysis' })];
  it('lists everything it made as Data does: its report, its other write-ups, then its analyses; its sources apart; nothing Data does not list', () => {
    const { items, sources } = workOf(made, 'r810833');
    expect(items.map((item) => item.id)).toEqual(['r810833', 'r810833:o3', 'e6', 'r810833:table-1']);
    expect(sources.map((item) => item.id)).toEqual(['r810833:source-0123456789ab']);
    // An older server sends no maker: its own ids still say whose they are.
    expect(workOf([entry({ id: 'r810833', kind: 'research' }), entry({ id: 'r810833:table-1', kind: 'finding-table' }), entry({ id: 'e6' })], 'r810833').items.map((item) => item.id))
      .toEqual(['r810833', 'r810833:table-1']);
  });
  it('names each by its title, kind and count in its record’s own words, never a guessed count', () => {
    expect(countWords(made[0])).toBe('271 bright regions');
    expect(countWords(made[1])).toBe('271 rows');
    expect(countWords(entry({ plain: '312 outlined, 298 still in place' }))).toBe('312 outlines');
    expect(countWords(entry({ summary: 'Its answer, in words' }))).toBe('');
    expect(switchLabel(made[0])).toBe('Bright regions · Outlines, 271 bright regions');
    expect(switchLabel(made[1])).toBe('Region areas · Table, 271 rows');
    expect(switchLabel(made[2])).toBe('Bright regions in Human cells · Presentation');
    expect([readerShape(sourcesItem('r810833', 'p')), kindWord(sourcesItem('r810833', 'p')), sourcesItem('r810833', 'p').id]).toEqual(['sources', 'Sources', 'r810833:sources']);
  });
  const page = (over: Partial<ReaderPage> = {}): ReaderPage => ({ rid: 'r810833', name: 'Researcher 3', day: 'p', entry: made[2], owns: (id) => id === 'r810833' || id === 'r810833:o3',
    show: vi.fn(), host: new TestElement('div') as unknown as HTMLElement, kind: 'Presentation', title: 'Bright regions in Human cells', cell: 'working', words: 'Writing the presentation',
    since: null, tools: [], more: [{ label: 'Show beside the live view', act: () => {} }], versions: [], how: () => null, ...over });
  const researchDeps = (p: ReaderPage, over: Partial<ReaderDeps> = {}): ReaderDeps => ({
    researchers: () => researchers, working: () => true, method: () => '', input: () => null, places: () => [], tab: () => 'main', close: vi.fn(), back: vi.fn(), talk: vi.fn(),
    openInNewTab: vi.fn(), showOnPicture: vi.fn(async () => {}), onPicture: () => false, addToTab: vi.fn(), goTo: vi.fn(), routes: () => [],
    trash: () => ({ label: 'Move to trash', disabled: true, act: () => {} }), editNote: vi.fn(), open: vi.fn(), page: () => p, made: () => made, ...over });
  const mount = () => { const dom = syntheticDOM(); vi.stubGlobal('document', dom.document); vi.stubGlobal('requestAnimationFrame', dom.raf); return dom; };
  it('draws its page as its page gives it, with the switcher as its title and no ×; Talk only for another researcher’s work', () => {
    mount(); vi.stubGlobal('fetch', vi.fn(() => new Promise(() => {})));
    const p = page(), reader = new DataReader(researchDeps(p), 'research'), root = reader.root as unknown as TestElement;
    expect(root.id).toBe('data-reader-research');
    reader.pageChanged();
    expect(reader.current()?.id).toBe('r810833');
    expect([root.dataset.readerPage, root.querySelector('.rd-body-host')!.children[0]]).toEqual(['true', p.host]);
    expect(root.querySelector('.rd-switch')!.textContent).toBe('Bright regions in Human cells');
    expect(root.querySelector('.rd-by')!.textContent).toBe('Presentation · Writing the presentation');
    expect([root.querySelectorAll('.rd-close'), root.querySelectorAll('.rd-talk'), root.querySelectorAll('.rd-new-tab')]).toEqual([[], [], []]);
    // A Data row of another researcher shown here: Talk to it, the one way to its Tab.
    reader.choose(entry({ id: 'r802191', kind: 'research', title: 'Cellpose-SAM nuclei', maker: { kind: 'researcher', id: 'r802191', name: 'Researcher 1' }, role: 'writeup' }));
    expect(root.querySelector('.rd-talk')!.textContent).toBe('Talk to Researcher 1');
    expect(root.dataset.readerPage).toBe('false');
    // Its own lesson is its page's to draw: the page shows it.
    reader.choose(made[3]);
    expect(p.show).toHaveBeenCalledWith('r810833:o3');
  });
  it('opens one of its findings’ tables from its record’s index (Data counts them from 1)', async () => {
    mount();
    const session = { rid: 'r810833', day: '2026-10-25', outputs: [], history: [], result: { title: 'R', tables: [{ title: 'Region areas', columns: ['region', 'area'], rows: [['1', '60']] }] } };
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => session })));
    const reader = new DataReader(researchDeps(page()), 'research'), root = reader.root as unknown as TestElement;
    reader.choose(entry({ ...made[1], source: { index: 0 } as ReaderEntry['source'] }));
    for (let i = 0; i < 6; i++) await Promise.resolve();
    expect(root.dataset.displayState).toBe('rendered');
    expect(root.querySelector('.rtable')!.querySelectorAll('td').map((td) => td.textContent)).toEqual(['1', '60']);
  });
});
