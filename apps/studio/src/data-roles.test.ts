import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  dataRows, madeWords, makerChoices, makerKey, makerName, researcherKey, researcherWork, roleOf, rowCount, rowFrom, rowHolds, rowIsNew, rowLine,
  rowMaker, rowMatches, rowSeenKeys, rowTitle, rowWriting, unseenRows, type DataEntry, type DataRow,
} from './data-roles';
import { sessionDOM, SessionNode } from './sessions.test-support';
import { fire } from './workspace-windows.test-support';
import type { AssetMeta } from './protocol';

// Entries as the server sends them (trimmed from a copy of a home with 12 real research sessions, 2026-10-25/26).
const DAY = '2026-10-25.4';
const NOW = new Date(2026, 9, 26, 15, 0).getTime() / 1000;
const TODAY = (h: number, m: number) => new Date(2026, 9, 26, h, m).getTime() / 1000;
const YESTERDAY = (h: number, m: number) => new Date(2026, 9, 25, h, m).getTime() / 1000;
const r = (n: number, id = `r80219${n}`) => ({ kind: 'researcher' as const, id, name: `Researcher ${n}` });
const entry = (id: string, kind: string, fields: Partial<DataEntry> = {}): DataEntry =>
  ({ id, kind, title: id, summary: '', state: 'done', actions: [], day: DAY, at: TODAY(10, 0), ...fields }) as DataEntry;
const asset = (id: string, fields: Partial<AssetMeta> = {}): AssetMeta =>
  ({ id, kind: 'still', title: 'Human cells · David Root (CC0)', from: 'example', capturedAt: null, captured: '', seconds: null, capturedOn: null,
    microscope: null, optics: {}, specimen: null, specimenBy: null, notes: null, origin: null, file: 'human_mitosis.png', thumb: `/api/library/${id}/thumbnail`, ...fields });

const picture = entry('a1', 'asset', { title: 'Human cells · David Root (CC0)', role: 'acquired', maker: null, at: 0 });
// One run: its outlines and its answer (S3), made by you.
const outlines = entry('e8', 'population', { title: 'Bright regions · Bright objects · 02:35', plain: '271 outlined, not shown now', role: 'analysis',
  maker: { kind: 'you', name: 'You' }, run: 'x43ab17-1', at: TODAY(2, 35), time: '02:35:18', version: 0, product: true,
  source: { sourceImage: { asset: 'a1' } } });
const answer = entry('d1', 'result', { title: 'Bright objects · 02:35', role: 'analysis', maker: { kind: 'you', name: 'You' }, run: 'x43ab17-1', at: TODAY(2, 35) + 0.2, time: '02:35:18' });
// A researcher's hands' outlines, its report (a presentation being written) and its table.
const forR1 = entry('e4', 'population', { title: 'Cell outlines, for Researcher 1 · Cellpose-SAM · 22:03', plain: '341 outlined, not shown now', role: 'analysis',
  maker: r(1), run: 'x51a388-1', at: YESTERDAY(22, 3), time: '22:03:49', product: true });
const report1 = entry('r802191', 'research', { title: 'Cellpose-SAM Nuclear Segmentation and Mitotic Figure Analysis', role: 'writeup', maker: r(1), untrusted: true,
  forms: [{ label: 'Presentation', state: 'writing', at: TODAY(10, 5) }, { label: 'Findings v1', version: 1, state: 'ready', at: TODAY(10, 1) }], at: TODAY(10, 6), version: 1 });
const table1 = entry('r802191:table-1', 'finding-table', { title: 'Morphological and Intensity Comparison', summary: '4 rows, 3 columns', role: 'analysis', maker: r(1), untrusted: true,
  source: { researcher: 'r802191', part: 'tables', index: 0 } as never, at: TODAY(10, 4), time: '10:04:24', version: 1 });
// Researcher 2: a report (its second findings), and a page it cited; Researcher 4 failed before it wrote anything.
const report2 = entry('r802192', 'research', { title: 'Nuclear Segmentation Using micro-sam', role: 'writeup', maker: r(2), untrusted: true,
  forms: [{ label: 'Findings v2', version: 2, state: 'ready' }, { label: 'Findings v1', version: 1, state: 'ready' }], version: 2, at: TODAY(9, 0) });
const page = entry('r802192:source-26cfa2eb2b96', 'source', { title: 'computational-cell-analytics.github.io/micro-sam/micro_sam.html', role: 'source', maker: r(2), untrusted: true,
  values: { url: 'https://computational-cell-analytics.github.io/micro-sam/micro_sam.html', site: 'computational-cell-analytics.github.io' },
  source: { researcher: 'r802192', citedIn: ['r802192'] } as never });
const failed = entry('r802194', 'research', { title: "Researcher 4's notes", role: 'writeup', maker: r(4), forms: [], state: 'failed' });
const bookmark = entry('b1', 'bookmark', { title: 'Bookmark · 10:20', role: 'other', maker: { kind: 'you', name: 'You' }, time: '10:20:00', at: TODAY(10, 20) });
const outcome = entry('u1', 'outcome', { role: null });
const fact = entry('f1', 'fact', { role: null, maker: { kind: 'you', name: 'You' } });
const ALL = [picture, outlines, answer, forR1, report1, table1, report2, page, failed, bookmark, outcome, fact];
const row = (rows: DataRow[], key: string) => rows.find((item) => item.key === key)!;

describe('Data by what each thing is', () => {
  it('lists the four kinds in order, newest first in each, then Other; leaves out what Data never lists and a report with nothing written yet', () => {
    const rows = dataRows(ALL, [asset('a1')]);
    expect(rows.map((item) => `${item.role}:${item.key}`)).toEqual([
      'acquired:a1', 'analysis:r802191:table-1', 'analysis:e8', 'analysis:e4', 'writeup:r802191', 'writeup:r802192',
      'source:r802192:source-26cfa2eb2b96', 'other:b1']);
    // Researchers are never rows; what they wrote is. Researcher 4 has written nothing: it is in Researchers only.
    expect(rows.some((item) => item.key === 'r802194')).toBe(false);
  });

  it('folds one run into one row that opens its outlines, says how many and holds its table and answer', () => {
    const run = row(dataRows(ALL), 'e8');
    expect(run.parts.map((part) => part.id)).toEqual(['e8', 'd1']);
    expect(run.entry?.id).toBe('e8');
    expect(rowTitle(run)).toBe('Bright regions');
    expect(rowCount(run)).toBe('271 regions');
    expect(rowHolds(run)).toEqual(['Outlines', 'Table, 271 rows', 'Answer']);
    expect(rowLine(run, { now: NOW })).toBe('Bright objects · 02:35');
    // Without a shared run, the answer is a row of its own.
    expect(dataRows([outlines, { ...answer, run: null }]).map((item) => item.key)).toEqual(['d1', 'e8']);
  });

  it('names who made it once, in the quiet line, never guessing: nothing for you or an older record, nothing when filtered to it', () => {
    const rows = dataRows(ALL);
    const r1 = row(rows, 'e4');
    expect(rowTitle(r1)).toBe('Cell outlines');
    expect(rowCount(r1)).toBe('341 outlines');
    expect(rowLine(r1, { now: NOW })).toBe('Researcher 1 · Cellpose-SAM · Oct 25');
    expect(rowLine(r1, { now: NOW, filtered: true })).toBe('Cellpose-SAM · Oct 25');
    const older = dataRows([{ ...outlines, maker: null, run: null }])[0];
    expect(older.maker).toBe(null);
    expect(rowLine(older, { now: NOW })).toBe('Bright objects · 02:35');
    // A researcher's own table keeps its words whole and says what it is in the app's.
    const table = row(rows, 'r802191:table-1');
    expect(rowTitle(table)).toBe('Morphological and Intensity Comparison');
    expect(rowCount(table)).toBe('4 rows');
    expect(rowLine(table, { now: NOW })).toBe('Researcher 1 · Table · 10:04');
  });

  it('says a write-up’s current form, with the lit cell while it is being written', () => {
    const rows = dataRows(ALL);
    const presentation = row(rows, 'r802191'), findings = row(rows, 'r802192');
    expect(rowTitle(presentation)).toBe('Cellpose-SAM Nuclear Segmentation and Mitotic Figure Analysis');
    expect(rowLine(presentation, { now: NOW })).toBe('Researcher 1 · Presentation · being written');
    expect(rowWriting(presentation)).toBe(true);
    expect(rowLine(findings, { now: NOW })).toBe('Researcher 2 · Findings v2');
    expect(rowLine(findings, { now: NOW, filtered: true })).toBe('Findings v2');
    expect(rowWriting(findings)).toBe(false);
    const note = dataRows([entry('n1', 'annotation', { title: 'Your note · Field A looks denser than Field B', role: 'writeup', maker: { kind: 'you', name: 'You' } })])[0];
    expect([rowTitle(note), rowLine(note, { now: NOW })]).toEqual(['Field A looks denser than Field B', 'Note']);
  });

  it('lists the Project’s inputs from its sources, joined with the index, before the index has read them too', () => {
    const recording = asset('a2', { kind: 'recording', title: 'Oct 22, 3:40 pm', seconds: 24, thumb: undefined });
    const early = dataRows([], [asset('a1'), recording]);
    expect(early.map((item) => [item.key, item.entry?.id ?? null, rowTitle(item), rowLine(item, { now: NOW })])).toEqual([
      ['a1', null, 'Human cells', 'Picture · David Root (CC0)'], ['a2', null, 'Oct 22, 3:40 pm', 'Recording · 0:24']]);
    const joined = dataRows([picture], [asset('a1')]);
    expect(joined).toHaveLength(1);
    expect(joined[0].entry?.id).toBe('a1');
    expect(rowFrom(row(dataRows(ALL), 'e8'), 'a1')).toBe(true);
    expect(rowFrom(row(dataRows(ALL), 'e4'), 'a1')).toBe(false);
  });

  it('a source says its site, and is never marked New nor counted on Data’s badge; nor is an input', () => {
    const rows = dataRows(ALL, [asset('a1')]);
    const source = row(rows, 'r802192:source-26cfa2eb2b96');
    // Its title is its address (no title was kept), so the site is not said twice.
    expect(rowLine(source, { now: NOW })).toBe('Researcher 2 · Page');
    const titled = dataRows([{ ...page, title: 'micro-sam documentation', values: { ...(page.values as object), authors: 'Archit et al.' } }])[0];
    expect(rowLine(titled, { now: NOW })).toBe('Researcher 2 · Page · Archit et al. · computational-cell-analytics.github.io');
    const pictured = dataRows([{ ...page, title: 'Mitosis', values: { site: 'commons.wikimedia.org', picture: 'https://upload.wikimedia.org/x.jpg', credit: 'J. Doe, CC BY 4.0' } }])[0];
    expect(rowLine(pictured, { now: NOW })).toBe('Researcher 2 · Web picture · J. Doe, CC BY 4.0 · commons.wikimedia.org');
    expect(rowSeenKeys(source)).toEqual([]);
    expect(rowSeenKeys(row(rows, 'a1'))).toEqual([]);
    expect(rowIsNew(source, new Set())).toBe(false);
  });

  it('counts New by rows, not entries: a run is new until each of its parts was seen', () => {
    const rows = dataRows([outlines, answer, bookmark]);
    expect(unseenRows(rows, new Set())).toBe(2);
    expect(unseenRows(rows, new Set(['e8@0']))).toBe(2);
    expect(unseenRows(rows, new Set(['e8@0', 'd1@0']))).toBe(1);
    expect(rowSeenKeys(row(rows, 'e8'))).toEqual(['e8@0', 'd1@0']);
  });

  it('Made by lists you, the Assistant and each researcher here by its number, each only when a record says it made something', () => {
    const r10 = entry('x', 'population', { role: 'analysis', maker: { kind: 'researcher', id: 'r9', name: 'Researcher 10' } });
    const nameOnly = entry('y', 'population', { role: 'analysis', maker: { kind: 'researcher', name: 'Researcher 2' } });
    const rows = dataRows([...ALL, r10, nameOnly]);
    expect(makerChoices(rows).map((choice) => [choice.key, choice.name, choice.rid ?? ''])).toEqual([
      ['you', 'You', ''], ['r:Researcher 1', 'Researcher 1', 'r802191'], ['r:Researcher 2', 'Researcher 2', 'r802192'],
      ['r:Researcher 10', 'Researcher 10', 'r9']]);
    // Nothing here is recorded as the Assistant's: it is not offered (an older record names no maker, never a guess).
    const asked = entry('c1', 'result', { role: 'analysis', maker: { kind: 'assistant', name: 'The Assistant' } });
    expect(makerChoices(dataRows([asked, outlines])).map((choice) => choice.name)).toEqual(['You', 'The Assistant']);
    expect(makerChoices(dataRows([{ ...outlines, maker: null }]))).toEqual([]);
    expect(rows.filter((item) => rowMaker(item) === researcherKey('Researcher 2')).map((item) => item.key)).toEqual(['y', 'r802192', 'r802192:source-26cfa2eb2b96']);
    expect(rows.filter((item) => rowMaker(item) === 'you').map((item) => item.key)).toEqual(['e8', 'b1']);
    expect(makerKey(null)).toBe(null);
  });

  it('Made by names an outside agent by its own label, after the Assistant and before researchers (ADR 0026)', () => {
    const byAgent = entry('a1', 'population', { role: 'analysis', maker: { kind: 'agent', id: 'ac_1', name: 'Claude Code' } });
    const asked = entry('c1', 'result', { role: 'analysis', maker: { kind: 'assistant', name: 'The Assistant' } });
    const choices = makerChoices(dataRows([byAgent, asked, outlines]));
    expect(choices.map((choice) => [choice.key, choice.name])).toEqual([['you', 'You'], ['assistant', 'The Assistant'], ['a:Claude Code', 'Claude Code']]);
    expect(makerName('a:Claude Code')).toBe('Claude Code');
    expect(dataRows([byAgent, outlines]).filter((item) => rowMaker(item) === 'a:Claude Code').map((item) => item.key)).toEqual(['a1']);
  });

  it('search finds a row by any of its parts, its maker, its form and a source’s site', () => {
    const rows = dataRows(ALL);
    expect(rowMatches(row(rows, 'e8'), 'found')).toBe(false);
    expect(rowMatches(row(rows, 'e8'), 'd1')).toBe(true);
    expect(rowMatches(row(rows, 'r802191'), 'presentation')).toBe(true);
    expect(rowMatches(row(rows, 'r802191'), 'researcher 1')).toBe(true);
    expect(rowMatches(row(rows, 'r802192:source-26cfa2eb2b96'), 'micro-sam')).toBe(true);
    expect(rowMatches(row(rows, 'e4'), '  ')).toBe(true);
  });

  it('reads an index from before ticket 66 (no roles) as before: inputs, Other, and outcomes left out', () => {
    expect(roleOf({ kind: 'asset' })).toBe('acquired');
    expect(roleOf({ kind: 'population' })).toBe('other');
    expect(roleOf({ kind: 'outcome' })).toBe(null);
    expect(roleOf({ kind: 'fact', role: null })).toBe(null);
    expect(dataRows([{ ...failed, forms: undefined }]).map((item) => item.key)).toEqual(['r802194']);
  });

  it('opens a researcher’s lesson, table or source on its Tab, as its rows did', () => {
    expect(researcherWork({ kind: 'lesson', source: { researcher: 'r1', outputId: 'o3' } as never })).toEqual({ rid: 'r1', output: 'o3' });
    expect(researcherWork(table1)).toEqual({ rid: 'r802191' });
    expect(researcherWork(outlines)).toBe(null);
  });
});

describe('what a researcher made (Researchers rows, D4)', () => {
  it('says it in a line, write-ups first, one thing per row, sources counted last', () => {
    expect(madeWords(ALL, { rid: 'r802191', name: 'Researcher 1' })).toBe('Made a report, a table and outlines');
    // Its presentation counts once it is made, never while it is being written.
    const presented = { ...report1, forms: [{ label: 'Presentation', outputId: 'o1', state: 'ready' as const }, ...report1.forms!.slice(1)] };
    expect(madeWords([...ALL.filter((item) => item !== report1), presented], { rid: 'r802191', name: 'Researcher 1' })).toBe('Made a presentation, a table and outlines');
    expect(madeWords([{ ...report1, forms: [{ label: 'Presentation', state: 'writing' }] }], { rid: 'r802191', name: 'Researcher 1' })).toBe('');
    expect(madeWords([...ALL, entry('r802192:source-2', 'source', { role: 'source', maker: r(2) }), entry('r802192:source-3', 'source', { role: 'source', maker: r(2) })],
      { rid: 'r802192', name: 'Researcher 2' })).toBe('Made a report and 3 sources');
    const two = [entry('t1', 'finding-table', { role: 'analysis', maker: r(5, 'r5') }), entry('t2', 'finding-table', { role: 'analysis', maker: r(5, 'r5') })];
    expect(madeWords(two, { rid: 'r5', name: 'Researcher 5' })).toBe('Made 2 tables');
    // A record that knows only its name ("for Researcher 2") counts for that researcher.
    expect(madeWords([entry('z', 'population', { role: 'analysis', maker: { kind: 'researcher', name: 'Researcher 6' } })], { rid: 'r6', name: 'Researcher 6' })).toBe('Made outlines');
  });
  it('says nothing when it has made nothing yet', () => {
    expect(madeWords(ALL, { rid: 'r802194', name: 'Researcher 4' })).toBe('');
    expect(madeWords([], { rid: 'r1', name: 'Researcher 1' })).toBe('');
  });
});

describe('Data’s list', () => {
  beforeEach(() => { sessionDOM(); });
  afterEach(() => { vi.unstubAllGlobals(); });
  const state = (rows: DataRow[], extra: Record<string, unknown> = {}) => ({ rows, choices: makerChoices(rows), maker: null, source: null, query: '', loading: false,
    here: (item: DataRow) => item.key === 'e8', seen: new Set<string>(), now: NOW, ...extra });

  it('draws the sections in order with their counts, Other folded, one row per run, On this Tab and the lit cell where they belong', async () => {
    const { DataList } = await import('./data-list');
    const open = vi.fn();
    const list = new DataList({ view: (item) => ({ open: () => open(item.key), menu: () => [], tip: 'Open', current: item.key === 'r802192' }), choose: vi.fn() });
    const rows = dataRows(ALL, [asset('a1')]);
    list.render(state(rows));
    const root = list.list as unknown as SessionNode;
    expect(root.querySelectorAll('.mm-dsection').map((head) => head.textContent)).toEqual([
      'Pictures and recordings 1', 'Analyses 3', 'Write-ups 2', 'Sources 1', 'Files 1']);
    // Other is folded: its rows show once it is opened.
    expect(root.querySelector('[data-resource="b1"]')).toBe(null);
    fire(root.querySelector('.data-fold')!, 'click');
    expect(root.querySelector('[data-resource="b1"]')).not.toBe(null);
    const run = root.querySelector('[data-resource="e8"]')!;
    expect(run.dataset.parts).toBe('e8 d1');
    expect(run.dataset.here).toBe('true');
    expect(run.querySelector('.data-here')!.textContent).toBe('On this Tab');
    expect(run.querySelector('.data-row-n')!.textContent).toBe('271 regions');
    expect(run.querySelector('.mm-row')!.title).toContain('Holds Outlines · Table, 271 rows · Answer');
    // Its whole server title stays its accessible name and its tooltip's first line.
    expect(run.querySelector('.mm-row')!.getAttribute('aria-label')).toBe('Open Bright regions · Bright objects · 02:35');
    expect(run.querySelector('.mm-row')!.title.split('\n')[0]).toBe('Bright regions · Bright objects · 02:35');
    expect(root.querySelector('[data-resource="a1"]')!.querySelector('.mm-row')!.getAttribute('aria-label')).toBe('Open Human cells · David Root (CC0)');
    expect(run.dataset.fresh).toBe('true');
    expect(root.querySelector('[data-resource="r802192"]')!.dataset.current).toBe('true');
    expect(run.dataset.current).toBeUndefined();
    expect(run.classList.contains('study-result-card')).toBe(true);
    // A researcher's words are marked as theirs.
    const writing = root.querySelector('[data-resource="r802191"]')!;
    expect(writing.dataset.untrusted).toBe('true');
    expect(writing.querySelector('.mm-row')!.title).toContain('Researcher 1’s words');
    expect(writing.querySelector('[data-kind="lit-cell"]')).not.toBe(null);
    // An input is not a result card (scripts that find results by their row skip it), and it opens as before.
    const input = root.querySelector('[data-resource="a1"]')!;
    expect(input.classList.contains('study-data-input')).toBe(true);
    fire(input.querySelector('.mm-row')!, 'click');
    expect(open).toHaveBeenCalledWith('a1');
  });

  it('Made by everyone ▾ lists the makers; one chosen is a chip with × and Open Researcher N', async () => {
    const { DataList } = await import('./data-list');
    const choose = vi.fn(), openResearcher = vi.fn();
    const list = new DataList({ view: () => ({ open: vi.fn(), menu: () => [], tip: '' }), choose, openResearcher,
      researcherCell: (name) => (name === 'Researcher 1' ? { state: 'working', label: 'Researcher 1: Working' } : null) });
    const rows = dataRows(ALL);
    list.render(state(rows));
    const filter = list.filter as unknown as SessionNode;
    const made = filter.querySelector('.data-made-by')!;
    expect(made.textContent).toBe('Made by everyone');
    fire(made, 'click');
    const items = (document.body as unknown as SessionNode).querySelector('.mm-drawer-menu')!.querySelectorAll('button');
    expect(items.map((item) => item.textContent)).toEqual(['✓Everyone', 'You', 'Researcher 1', 'Researcher 2']);
    fire(items[2], 'click');
    expect(choose).toHaveBeenCalledWith('r:Researcher 1');
    const mine = rows.filter((item) => rowMaker(item) === 'r:Researcher 1');
    list.render(state(mine, { maker: 'r:Researcher 1', choices: makerChoices(rows) }));
    expect(filter.querySelector('.data-chip-name')!.textContent).toBe('Researcher 1');
    expect(filter.querySelector('.data-chip')!.querySelector('[data-kind="lit-cell"]')).not.toBe(null);
    // Under its chip the rows do not say its name again.
    expect((list.list as unknown as SessionNode).querySelector('[data-resource="e4"]')!.querySelector('.data-row-sub')!.textContent).toBe('Cellpose-SAM · Oct 25');
    fire(filter.querySelector('.data-chip-open')!, 'click');
    expect(openResearcher).toHaveBeenCalledWith('r802191');
    fire(filter.querySelector('.data-chip-x')!, 'click');
    expect(choose).toHaveBeenLastCalledWith(null);
  });

  it('says what is empty, what is loading and what matched nothing', async () => {
    const { DataList } = await import('./data-list');
    const addInput = vi.fn();
    const list = new DataList({ view: () => ({ open: vi.fn(), menu: () => [], tip: '' }), choose: vi.fn(), addInput });
    const root = list.list as unknown as SessionNode;
    list.render(state([]));
    expect(root.textContent).toContain('Nothing in Data yet.');
    fire(root.querySelector('.study-data-open-input')!, 'click');
    expect(addInput).toHaveBeenCalled();
    list.render(state([], { query: 'zebra' }));
    expect(root.textContent).toBe('Nothing matches. Clear the search or the filter.');
    list.render(state([], { maker: 'assistant' }));
    expect(root.textContent).toBe('Nothing in Data is recorded as made by The Assistant.');
    list.render(state(dataRows([], [asset('a1')]), { loading: true }));
    expect(root.querySelectorAll('.data-row')).toHaveLength(1);
    expect(root.textContent).toContain('Loading what this Project made…');
  });
});
