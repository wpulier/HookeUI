// The researcher Tab's words and lists (research-tab.ts): what its state (the reader's quiet line) and Stage bar say,
// what the composer invites, what it made from its recorded outputs (the composer's +), what it used (How it was made's
// Worked from), its steps as rows, the page's sections, and which Data entry its page shows (ticket 66 D3).
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { ResearchSession } from './protocol';
import { anchorWords, engineWords, itemValue, lineRoot, outlineOf, pageItem, readLayout, researchBarWords, researchFiles, researchPill, researchPlaceholder, researchQuestion, researchStageBar, researchSteps, researchUsed, researchVersions, saveLayout, splitStep, willMake } from './research-tab';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

const memory = new Map<string, string>();
beforeEach(() => { memory.clear(); vi.stubGlobal('sessionStorage', { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => { memory.set(k, v); }, removeItem: (k: string) => { memory.delete(k); } }); });
afterEach(() => vi.unstubAllGlobals());

const now = 1_800_000_000;
const row = (fields: Partial<ResearchSession> = {}): ResearchSession => ({ rid: 'r3', n: 3, name: 'Researcher 3', brief: 'Find the bright regions', deliverable: null, effort: 'standard',
  engine: 'gpt-6.1-sol', purpose: 'research', label: '', objectId: null, status: 'running', step: 'Writing the presentation', spent: 0, cap: 1, seconds: 3600, created: now - 100,
  started: now - 90, finished: null, snapshot: '/s.png', outputs: [], items: [], findings: [], corrections: [], history: [], canFollowUp: true, result: null, ...fields });
const finding = (fields: Partial<NonNullable<ResearchSession['result']>> = {}): NonNullable<ResearchSession['result']> => ({ title: 'Bright regions in Human cells', summary: 'S',
  one_liner: 'O', significance: '', details: [], look_for: '', confidence: null, sources: [], refs: [], findingRevision: { version: 2, digest: 'd2', complete: true }, ...fields });

describe('the composer invites what the state allows', () => {
  test('a working researcher reads a note at its next step; one that needs you takes the answer; finished work is asked about', () => {
    const can = { writable: true, canSend: true };
    expect(researchPlaceholder(row(), can)).toBe('Tell Researcher 3 something. It reads it at its next step.');
    expect(researchPlaceholder(row({ status: 'queued' }), can)).toBe('Tell Researcher 3 something. It reads it at its next step.');
    expect(researchPlaceholder(row({ n: 4, name: 'Researcher 4', requests: [{ id: 'q', what: '', why: 'an hour', keepGoing: true, status: 'asked', at: now }] }), can))
      .toBe('Answer, or tell Researcher 4 more.');
    expect(researchPlaceholder(row({ n: 2, name: 'Researcher 2', status: 'done', finished: now }), can)).toBe('Ask Researcher 2 about its findings.');
    expect(researchPlaceholder(row({ status: 'partial', finished: now }), can)).toBe('Ask Researcher 3 about its findings.');
    expect(researchPlaceholder(row({ status: 'cancelled', finished: now }), can)).toBe('Ask Researcher 3 about what it kept.');
    expect(researchPlaceholder(row({ status: 'interrupted', finished: now }), can)).toBe('Ask Researcher 3 about what it kept.');
    expect(researchPlaceholder(row({ status: 'done', canFollowUp: false }), { writable: true, canSend: false })).toBe('Researcher 3 can’t take a message now.');
    expect(researchPlaceholder(row(), { writable: false, canSend: true })).toBe('Earlier work · read only');
  });
});

describe('the status pill and the Stage bar say only the state and the server’s words', () => {
  test('working with its step and clock; queued; needs you; Stop requested; partial with its version; interrupted', () => {
    expect(researchPill(row())).toMatchObject({ cell: 'working', words: 'Writing the presentation', since: now - 90, version: '' });
    expect(researchPill(row({ step: '' })).words).toBe('Working');
    expect(researchPill(row({ status: 'queued' }))).toMatchObject({ cell: 'queued', words: 'Waiting for a free slot', since: null });
    expect(researchPill(row({ requests: [{ id: 'q', what: '', why: 'an hour', keepGoing: true, status: 'asked', at: now }] }))).toMatchObject({ cell: 'needs', words: 'Needs you', since: null, title: 'Show the question' });
    memory.set('studio-stop-requested', JSON.stringify(['r3']));
    // research-stop.ts reads its list once per page; a stop asked in this page is kept the same way.
    return import('./research-stop').then(({ requestStop }) => {
      requestStop('r3', () => {});
      expect(researchPill(row())).toMatchObject({ cell: 'still', words: 'Stop requested', title: 'Asked to stop; it is Stopped once the server lists it so.' });
      expect(researchBarWords(row())).toEqual({ words: 'Stop requested', since: null, took: null });
      expect(researchPill(row({ status: 'partial', finished: now, result: finding() }))).toMatchObject({ cell: 'partial', words: 'Partial findings', version: 'v2', since: null });
      expect(researchPill(row({ status: 'interrupted', finished: now }))).toMatchObject({ cell: 'stopped', words: 'Interrupted' });
      expect(researchPill(row({ status: 'done', finished: now, result: finding() })).words).toBe('Findings ready');
    });
  });
  test('the bar: Working with its clock, Waiting for you, Took 0:28, Stopped, Could not finish', () => {
    expect(researchBarWords(row())).toEqual({ words: 'Working', since: now - 90, took: null });
    expect(researchBarWords(row({ requests: [{ id: 'q', what: '', why: 'x', keepGoing: true, status: 'asked', at: now }] })).words).toBe('Waiting for you');
    expect(researchBarWords(row({ status: 'partial', started: now - 28 * 60, finished: now, result: finding() }))).toEqual({ words: 'Took', since: null, took: 28 * 60 });
    expect(researchBarWords(row({ status: 'cancelled', finished: now })).words).toBe('Stopped');
    expect(researchBarWords(row({ status: 'failed', finished: now })).words).toBe('Could not finish');
  });
});

describe('what it made: only what it recorded, current versions, deliverables first', () => {
  test('current outputs and the findings show on its page; tables, charts and results it had made open in the reader beside the conversation', () => {
    const s = row({ outputs: [
      { id: 'o1', kind: 'presentation', path: '', url: '/p1', caption: 'Bright regions, the presentation', generated: false, altered: false, version: 1, current: false },
      { id: 'o2', kind: 'presentation', path: '', url: '/p2', caption: 'Bright regions, the presentation', generated: false, altered: false, version: 2, current: true },
      { id: 'o3', kind: 'html', contentKind: 'lesson', path: '', url: '/l', caption: 'How fluorescence works', generated: false, altered: false },
      { id: 'o4', kind: 'image', path: '', url: '/i', caption: '', generated: true, altered: false },
    ], ran: [{ id: 'e7', operation: 'capability', capability: 'basic-bright-objects', at: now, title: 'Bright regions mask', kind: 'population', count: 271, version: 0 },
      { id: 'e7', operation: 'capability', at: now + 1, title: 'again' }] });
    const files = researchFiles(s, finding({ tables: [{ title: 'areas.csv', columns: ['region', 'area'], rows: [['1', '60'], ['2', '199']] }],
      charts: [{ title: 'Region areas', transformation: 'identity', selectionRule: '', rows: [] }] }), '2026-10-24');
    expect(files.map((f) => [f.kind, f.name, f.meta, f.deliverable])).toEqual([
      ['presentation', 'Bright regions, the presentation', 'v2', true],
      ['lesson', 'How fluorescence works', '', true],
      ['picture', 'Researcher 3’s picture', '', true],
      ['report', 'Bright regions in Human cells', 'Findings v2', true],
      ['table', 'areas.csv', '2 rows', false],
      ['figure', 'Region areas', '0 values', false],
      ['mask', 'Bright regions mask', '271 outlines', false],
    ]);
    expect(files.map((f) => f.open)).toEqual([{ show: 'output:o2' }, { show: 'output:o3' }, { show: 'output:o4' }, { show: 'findings:2' },
      { tab: 'table', index: 0 }, { tab: 'chart', index: 0 }, { record: { id: 'e7', day: '2026-10-24', version: 0, title: 'Bright regions mask' } }]);
    expect(researchFiles(row(), null, 'd')).toEqual([]);
    // No findings, but notes it kept (stopped or interrupted): they are a page of their own.
    expect(researchFiles(row({ status: 'interrupted', findings: [{ kind: 'observation', text: 'A few small, very bright nuclei', sources: [] }] }), null, 'd'))
      .toEqual([{ key: 'notes', kind: 'report', name: 'Notes so far', meta: '1 note', deliverable: true, open: { show: 'notes' } }]);
  });
  test('Worked from: the live view it was asked on (opens Main), the records it read (each in the reader), the tools it ran (their run page)', () => {
    const s = row({ specimen: 'Human cells', working: [{ id: 'k1', how: 'picture 0', title: 'A held clip', kind: 'clip', at: now }, { id: 'e7', how: 'read', title: 'made by it', kind: 'population', at: now },
      { id: 'p1', how: 'picture 0', title: 'Human cells', kind: 'snapshot', at: now }], // the saved picture of the live view is its first row already
      ran: [{ id: 'e7', operation: 'capability', capability: 'basic-bright-objects', at: now, title: 'mask' }] });
    expect(researchUsed(s, 'd', (key) => key === 'basic-bright-objects' ? 'Bright objects · Basic local method' : null)).toEqual([
      { key: 'specimen', kind: 'specimen', name: 'Human cells', thumb: '/s.png', open: { main: true } },
      { key: 'record:k1', kind: 'record', name: 'A held clip', open: { record: { id: 'k1', day: 'd', version: null, title: 'A held clip' } } },
      { key: 'tool:basic-bright-objects', kind: 'tool', name: 'Bright objects', open: { tool: 'basic-bright-objects' } },
    ]);
  });
});

describe('the Data entry its page shows (ticket 66 S4 ids)', () => {
  const out = (fields: Partial<ResearchSession['outputs'][number]>) => ({ kind: 'html', path: '', url: '/x', caption: '', generated: false, altered: false, ...fields }) as ResearchSession['outputs'][number];
  test('a lesson, video or picture is its line, named by the line’s first version; its presentation and findings are its report', () => {
    const outputs = [out({ id: 'o1', contentKind: 'lesson', version: 1, current: false }), out({ id: 'o3', contentKind: 'lesson', version: 2, replaces: 'o1', current: true }),
      out({ id: 'o2', kind: 'presentation', current: true }), out({ id: 'o4', kind: 'video', current: true })];
    expect([lineRoot(outputs, 'o3'), lineRoot(outputs, 'o1'), lineRoot(outputs, 'o9')]).toEqual(['o1', 'o1', 'o9']);
    const s = row({ outputs, result: finding() });
    expect(pageItem(s, outputs[1], 'output:o3')).toEqual({ id: 'r3:o1', kind: 'Lesson' });
    expect(pageItem(s, outputs[3], undefined)).toEqual({ id: 'r3:o4', kind: 'Video' });
    expect(pageItem(s, outputs[2], 'output:o2')).toEqual({ id: 'r3', kind: 'Presentation' });
    expect(pageItem(s, undefined, 'findings:2')).toEqual({ id: 'r3', kind: 'Report' });
    expect(pageItem(row({ findings: [{ kind: 'observation', text: 'n', sources: [] }] }), undefined, 'notes')).toEqual({ id: 'r3', kind: 'Notes' });
  });
  test('the switcher’s choice shows on its page: its report (its default, or its newest presentation), a line’s current version', () => {
    const outputs = [out({ id: 'o1', contentKind: 'lesson', version: 1, current: false }), out({ id: 'o3', contentKind: 'lesson', version: 2, replaces: 'o1', current: true }),
      out({ id: 'o2', kind: 'presentation', current: true })];
    const s = row({ outputs, result: finding() });
    expect(itemValue(s, 'r3', undefined)).toBe(''); // the report is its default: Latest, following new versions
    expect(itemValue(s, 'r3', outputs[2])).toBe('');
    expect(itemValue({ ...s, deliverable: 'lesson' }, 'r3', outputs[1])).toBe('output:o2'); // a lesson is the default: the report by its presentation
    expect(itemValue({ ...s, outputs: outputs.slice(0, 2), deliverable: 'lesson' }, 'r3', outputs[1])).toBe('findings:2');
    expect(itemValue(s, 'r3:o1', undefined)).toBe('output:o3');
    expect(itemValue(s, 'r3#o3', undefined)).toBe('output:o3');
    expect(itemValue(s, 'r3:table-1', undefined)).toBe(null);
    expect(itemValue(s, 'r4:o1', undefined)).toBe(null);
  });
});

describe('its steps as compact rows', () => {
  test('ticks for done, the lit cell on the step at work, partial for an early write-up, a speech bubble for a note; what a run made on the app’s reply', () => {
    const steps = [
      { at: now - 80, kind: 'step', text: 'Asking the app to run Bright objects · Basic local method on the live view' },
      { at: now - 70, kind: 'step', text: 'The app outlined 271 bright regions' },
      { at: now - 60, kind: 'note', text: 'Got a note: count the small ones too' },
      { at: now - 50, kind: 'stall', text: 'Nothing new in 3 rounds: writing up what it has' },
      { at: now - 10, kind: 'step', text: 'Writing the presentation' },
    ];
    const tools = (key: string) => key === 'basic-bright-objects' ? 'Bright objects · Basic local method' : null;
    const rows = researchSteps(steps, row({ ran: [{ id: 'e7', operation: 'capability', capability: 'basic-bright-objects', at: now - 71, title: 'Bright regions mask', kind: 'population' }] }), tools);
    // One run is one row named by its tool: the worker's "Asking the app to …" joins the app's reply, kept whole on hover.
    expect(rows.map((r) => [r.mark, r.title, r.sub])).toEqual([
      ['done', 'Ran Bright objects', 'Outlined 271 bright regions'],
      ['note', 'Got a note', 'count the small ones too'],
      ['partial', 'Nothing new in 3 rounds', 'Writing up what it has'],
      ['live', 'Writing the presentation', ''],
    ]);
    expect(rows[0].made).toEqual([{ id: 'e7', title: 'Bright regions mask', kind: 'population', version: null }]);
    expect(rows[0].detail).toBe('Asking the app to run Bright objects · Basic local method on the live view. The app outlined 271 bright regions.');
    // Without the catalog's name, the worker's own words name it; an operation that is not a tool keeps its words.
    expect(researchSteps(steps.slice(0, 2), row({ status: 'done', finished: now, ran: [{ id: 'e7', operation: 'capability', at: now - 71, title: 'mask' }] }))
      .map((r) => [r.title, r.sub])).toEqual([['Ran Bright objects', 'Outlined 271 bright regions']]);
    expect(researchSteps([{ at: now - 80, kind: 'step', text: 'Asking the app to segment every cell in the live view and measure them' }, { at: now - 70, kind: 'step', text: 'The app outlined 312 cells' }],
      row({ status: 'done', finished: now, ran: [{ id: 'e9', operation: 'segment', at: now - 71, title: 'Cell outlines', kind: 'population' }] })).map((r) => [r.title, r.sub]))
      .toEqual([['Had the app segment every cell in the live view and measure them', 'Outlined 312 cells']]);
    // A question shows once, as the question (research.ts), not also as a step.
    const asked = [{ at: now - 40, kind: 'request', text: 'Asked the user whether to keep going: it has worked for 60 minutes' }];
    expect(researchSteps(asked, row({ requests: [{ id: 'q', what: '', why: 'it has worked for 60 minutes', keepGoing: true, status: 'asked', at: now - 40 }] }))).toEqual([]);
    expect(researchSteps(asked, row({ status: 'done', finished: now })).map((r) => r.title)).toEqual(['Asked the user whether to keep going']);
    // Waiting for the person: no live cell (its question says so); finished: all done.
    expect(researchSteps(steps, row({ requests: [{ id: 'q', what: '', why: 'x', keepGoing: true, status: 'asked', at: now }] })).some((r) => r.mark === 'live')).toBe(false);
    expect(researchSteps(steps, row({ status: 'done', finished: now })).some((r) => r.mark === 'live')).toBe(false);
    // Nothing recorded yet while it works: one live row with its progress words.
    expect(researchSteps([], row({ step: 'Choosing a model' })).map((r) => [r.mark, r.title])).toEqual([['live', 'Choosing a model']]);
    expect(splitStep('Searched the web: published methods')).toEqual({ title: 'Searched the web', sub: 'Published methods' });
  });
});

describe('the artifact’s sections and the words around it', () => {
  test('the Outline is its anchored sections by their own headings, unnumbered', () => {
    vi.stubGlobal('document', syntheticDOM().document);
    const report = new TestElement('div'); report.className = 'viewer-summary';
    const summary = new TestElement('p'); summary.dataset.anchor = 'summary';
    const methods = new TestElement('section'); methods.dataset.anchor = 'report:methods';
    const h = new TestElement('h3'); h.textContent = 'Methods'; methods.append(h);
    const table = new TestElement('div'); table.dataset.anchor = 'table:0';
    const caption = new TestElement('caption'); caption.textContent = 'areas.csv'; table.append(caption);
    report.append(summary, methods, table);
    expect(outlineOf(report as unknown as ParentNode)).toEqual([{ anchor: 'summary', title: 'In short' }, { anchor: 'report:methods', title: 'Methods' }, { anchor: 'table:0', title: 'areas.csv' }]);
    expect(anchorWords('summary', null)).toBe('In short');
    expect(anchorWords('chart:0:point:1', finding({ charts: [{ title: 'Region areas', transformation: 'identity', selectionRule: '', rows: [
      { label: 'a', value: 1, unit: 'px', denominator: null, exclusions: null, source: { day: 'd', id: 'e', version: 0, contentRevision: 'c', valuePath: 'v' } },
      { label: 'region 12', value: 2, unit: 'px', denominator: null, exclusions: null, source: { day: 'd', id: 'e', version: 0, contentRevision: 'c', valuePath: 'v' } }] }] }))).toBe('Region areas, region 12');
    expect(willMake(row({ deliverable: 'lesson' }))).toBe('It will make a lesson, then write up its findings here.');
    expect(willMake(row({ status: 'cancelled' }))).toBe('Nothing written yet');
    expect(engineWords('gpt-6.1-sol', (id) => id === 'gpt-6.1-sol' ? 'GPT-6.1 Sol' : null)).toBe('GPT-6.1 Sol');
    expect(engineWords('unknown-engine')).toBe('unknown-engine');
  });
});

describe('a question it asked, once, in the server’s words', () => {
  const keepGoing = { id: 'q1', what: '', why: 'it has worked for 60 minutes', keepGoing: true, at: now - 40 } as const;
  test('asked while it works: the present tense, waiting for the person', () => {
    expect(researchQuestion({ ...keepGoing, status: 'asked' }, row(), false)).toEqual({ words: 'Keep going? It has worked for 60 minutes.', answer: null, at: now - 40, waiting: true });
    expect(researchQuestion({ id: 'q2', what: 'a closer look at the dividing cells', why: 'whether they are in mitosis', status: 'asked', at: now }, row(), false).words)
      .toBe('It asks you for a closer look at the dividing cells, to see whether they are in mitosis.');
  });
  test('answered on this page: the past tense, and the answer is the person’s own message already', () => {
    const s = row({ interactionJobs: [{ id: 'j1', state: 'done', created: now - 30, request: { text: 'Yes, keep going.', kind: 'note' } }] });
    expect(researchQuestion({ ...keepGoing, status: 'answered', answer: 'Yes, keep going.', answeredAt: now - 30 }, s, false))
      .toEqual({ words: 'It asked whether to keep going (it has worked for 60 minutes).', answer: null, at: now - 30, waiting: false });
  });
  test('answered through the Assistant or by voice (no message on this page): the answer shows as the person’s words', () => {
    expect(researchQuestion({ ...keepGoing, status: 'answered', answer: 'Not now.', answeredAt: now - 20 }, row(), false))
      .toEqual({ words: 'It asked whether to keep going (it has worked for 60 minutes).', answer: 'Not now.', at: now - 20, waiting: false });
    expect(researchQuestion({ id: 'q2', what: 'a held clip', why: 'how they move', status: 'answered', answer: 'No footage, sorry', answeredAt: now, at: now - 5 }, row({ status: 'done', finished: now }), false).words)
      .toBe('It asked you for a held clip (to see how they move).');
  });
  test('finished without an answer: said so, and where its finding says what stayed unresolved', () => {
    expect(researchQuestion({ ...keepGoing, status: 'unanswered' }, row({ status: 'done', finished: now }), false).words)
      .toBe('It asked whether to keep going (it has worked for 60 minutes). It finished without an answer.');
    expect(researchQuestion({ ...keepGoing, status: 'asked' }, row({ status: 'partial', finished: now }), true))
      .toMatchObject({ words: 'It asked whether to keep going (it has worked for 60 minutes). It finished without an answer; its finding says what stayed unresolved.', waiting: false });
  });
});

describe('Versions and the Stage bar', () => {
  test('one real version is listed once, ticked; more add Latest first, which follows new versions until one is chosen', () => {
    const one = researchVersions(row({ status: 'partial', findingRevisions: [{ version: 1, digest: 'd1', at: now, complete: false }] }), undefined);
    expect(one).toHaveLength(1);
    expect(one[0]).toMatchObject({ value: '', checked: true });
    expect(one[0].label).toMatch(/^v1 · /);
    const two = row({ findingRevisions: [{ version: 1, digest: 'd1', at: now - 60, complete: true }, { version: 2, digest: 'd2', at: now, complete: true }],
      outputs: [{ id: 'o1', kind: 'html', contentKind: 'lesson', path: '', url: '/l', caption: 'Lesson', generated: false, altered: false }] });
    expect(researchVersions(two, undefined).map((v) => [v.label.replace(/ · .*$/, ''), v.value, v.checked])).toEqual([
      ['Latest (follows new versions)', '', true], ['v1', 'findings:1', false], ['v2', 'findings:2', false], ['Lesson v1', 'output:o1', false]]);
    expect(researchVersions(two, 'findings:1').filter((v) => v.checked).map((v) => v.value)).toEqual(['findings:1']);
    expect(researchVersions(row(), undefined)).toEqual([]);
  });
  test('the bar names what it was asked to do, says its state, and shows Stop only until Stop is asked', async () => {
    const s = row({ rid: 'r8', label: 'Bright-region analysis of the fluorescent nuclei', result: finding() });
    expect(researchStageBar(s)).toEqual({ title: 'Bright-region analysis of the fluorescent nuclei', words: 'Working', since: now - 90, tip: '', stop: true });
    expect(researchStageBar(row({ rid: 'r8', status: 'partial', started: now - 28 * 60, finished: now, result: finding() }))).toMatchObject({ title: 'Find the bright regions', words: 'Took 28:00', stop: false });
    const { requestStop } = await import('./research-stop');
    const sent: unknown[] = [];
    requestStop('r8', (command) => sent.push(command));
    expect(sent).toEqual([{ type: 'research-cancel', rid: 'r8' }]);
    expect(researchStageBar(s)).toMatchObject({ words: 'Stop requested', tip: 'Asked to stop; it is Stopped once the server lists it so.', stop: false, since: null });
    // Once the server lists it stopped, the bar says so.
    expect(researchStageBar({ ...s, status: 'cancelled', finished: now })).toMatchObject({ words: 'Stopped', stop: false });
  });
});

describe('the layout kept per researcher survives storage the browser blocks', () => {
  test('reading localStorage itself throws (site storage blocked): nothing is read or written, nothing throws', () => {
    const original = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', { configurable: true, get() { throw new DOMException('blocked', 'SecurityError'); } });
    try {
      expect(readLayout('r3')).toEqual({});
      expect(saveLayout('r3', { conv: 500 })).toEqual({ conv: 500 });
    } finally {
      if (original) Object.defineProperty(globalThis, 'localStorage', original); else delete (globalThis as { localStorage?: unknown }).localStorage;
    }
  });
});

describe('red is for what ended the run', () => {
  const warn = (at: number, text: string) => ({ at, kind: 'warning', text });
  test('a warning followed by later steps is the dim stopped mark; only a failed researcher\'s last warning is red', () => {
    const steps = [warn(now - 90, 'Read a long saved result; it isn\'t kept for charts or captures'),
      warn(now - 80, 'Waiting: another researcher\'s operation is running'), { at: now - 70, kind: 'step', text: 'Writing the presentation' }];
    expect(researchSteps(steps, row({ status: 'done', finished: now })).map((r) => [r.mark, r.title]))
      .toEqual([['stopped', 'Read a long saved result; it isn\'t kept for charts or captures'], ['stopped', 'Waiting'], ['done', 'Writing the presentation']]);
    const failed = [...steps, warn(now - 5, 'OpenAI says this account has no credits left. Add credits, or choose another model for Researching.')];
    expect(researchSteps(failed, row({ status: 'failed', finished: now })).map((r) => r.mark)).toEqual(['stopped', 'stopped', 'done', 'attention']);
  });
  test('a run of a built-in operation names the model that ran (hands.py S12) and how many it made', () => {
    const rows = researchSteps([], row({ status: 'done', finished: now, ran: [{ id: 'e9', operation: 'segment', capability: 'cellpose-sam', count: 341, at: now - 71, title: 'Cell outlines', kind: 'population' }] }),
      (key) => key === 'cellpose-sam' ? 'Cellpose-SAM' : null);
    expect(rows.map((r) => [r.title, r.sub])).toEqual([['Ran Cellpose-SAM', '341 outlines']]);
  });
});
