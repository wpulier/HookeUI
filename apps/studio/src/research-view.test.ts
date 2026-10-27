// The researcher Tab as research.ts assembles it (ResearchView over research-workspace.ts and its reader), over
// synthetic researcher records on the real wire fields: how a stopped, interrupted, partial, waiting, answered,
// following-up or failed researcher's conversation reads and acts, and what its reader's ⋯ and How it was made offer.
// Synthetic DOM evidence; the look is the ticket 66 D3 screenshots.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { ResearchSession } from './protocol';
import { ResearchView } from './research';
import { provideResearchReader, researchReader } from './research-workspace';
import type { ReaderEntry } from './data-reader';
import { fire, settleMicrotasks, syntheticDOM, TestElement } from './workspace-windows.test-support';

// Menus give focus back to their button when they close.
Object.assign(TestElement.prototype, { focus() {} });

const now = 1_800_000_000;
type Step = { at: number; kind: string; text: string };
const row = (fields: Partial<ResearchSession> & { steps?: Step[] } = {}): ResearchSession & { steps?: Step[] } => ({ rid: 'r3', n: 3, name: 'Researcher 3',
  brief: 'Find the bright regions in Human cells.', deliverable: null, effort: 'standard', engine: 'gpt-6.1-sol', purpose: 'research', label: '', objectId: null,
  status: 'running', step: 'Writing it up', spent: 0.05, cap: 0.5, seconds: 3600, created: now - 100, started: now - 90, finished: null, snapshot: '/api/x/2026-10-24/s.png',
  outputs: [], items: [], findings: [], corrections: [], history: [], canFollowUp: true, result: null, day: '2026-10-24', sitting: 'p', ...fields });
const finding = (fields: Partial<NonNullable<ResearchSession['result']>> = {}): NonNullable<ResearchSession['result']> => ({ title: 'Bright regions in Human cells',
  summary: 'Bright objects found 271 regions.', one_liner: 'It wrote up before trying a trained model.', significance: '', details: [], look_for: '', confidence: null,
  sources: [], refs: [], findingRevision: { version: 1, digest: 'd1', complete: false }, ...fields });

let byId: Map<string, TestElement>, host: TestElement, sent: unknown[], events: Event[], served: ResearchSession[], fetched: string[];
beforeEach(() => {
  const dom = syntheticDOM(); host = dom.host; byId = new Map(); sent = []; events = []; served = []; fetched = [];
  const memory = new Map<string, string>();
  const storage = { getItem: (k: string) => memory.get(k) ?? null, setItem: (k: string, v: string) => { memory.set(k, v); }, removeItem: (k: string) => { memory.delete(k); } };
  vi.stubGlobal('document', Object.assign(dom.document, { getElementById: (id: string) => byId.get(id) ?? null,
    createTextNode: (text: string) => Object.assign(new TestElement('#text'), { textContent: text }), activeElement: null }));
  const win = Object.assign(new EventTarget(), { setInterval: () => 1, clearInterval: () => {}, setTimeout: () => 1, clearTimeout: () => {}, open: vi.fn() });
  win.addEventListener('hooke-research-activity', (event) => events.push(event));
  win.addEventListener('hooke-research-open-record', (event) => events.push(event));
  vi.stubGlobal('window', win);
  vi.stubGlobal('location', { hash: '#research/r3', pathname: '/' });
  vi.stubGlobal('sessionStorage', storage); vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('fetch', async (url: string) => {
    fetched.push(url);
    const day = /\/api\/research-day\/([^?]+)/.exec(url);
    if (day) return { ok: true, json: async () => ({ sessions: served }) };
    const rid = /\/api\/research\/([^/?]+)/.exec(url)?.[1];
    const found = served.find((s) => s.rid === rid);
    return { ok: !!found, json: async () => found };
  });
  const make = (tag: string, id: string) => { const node = new TestElement(tag); node.id = id; byId.set(id, node); return node; };
  const root = make('section', 'research');
  root.append(make('p', 'research-empty'), make('ol', 'research-list'), make('details', 'research-quick'), make('ol', 'research-quick-list'), make('span', 'research-quick-count'),
    make('article', 'research-session'), make('p', 'research-spend'), make('button', 'research-popout'));
  host.append(root);
});
afterEach(() => { provideResearchReader(null); vi.unstubAllGlobals(); });

async function open(s: ResearchSession & { steps?: Step[] }, setup: (view: ResearchView) => void = () => {}) {
  served = [s];
  const view = new ResearchView((command) => { sent.push(command); return { ok: true, requestId: (command as { requestId?: string }).requestId }; });
  view.sitting = 'p';
  view.toolName = (key) => key === 'basic-bright-objects' ? 'Bright objects · Basic local method' : null;
  view.modelName = (id) => id === 'gpt-6.1-sol' ? 'GPT-6.1 Sol' : null;
  setup(view);
  view.apply({ sessions: [s] });
  view.show(s.rid);
  for (let i = 0; i < 4; i++) await settleMicrotasks();
  const page = byId.get('research-session')!;
  const log = page.querySelector('.rlog')!;
  return { view, page, log, steps: () => log.querySelectorAll('.step').map((step) => [step.dataset.mark, step.querySelector('b')?.textContent, step.querySelector('.step-sub')?.textContent ?? '']) };
}
const button = (scope: TestElement, words: string) => scope.querySelectorAll('button').find((b) => b.textContent === words);
const menu = () => host.querySelector('.workspace-add-menu');

describe('how its conversation ends, in its record’s words, with what can act', () => {
  test('stopped by you: the dim row says what it kept, or that nothing was kept', async () => {
    const { steps } = await open(row({ status: 'cancelled', finished: now - 10, step: '' }));
    expect(steps().at(-1)).toEqual(['stopped', 'Stopped by you', 'Nothing was kept']);
  });

  test('interrupted with notes but no findings: Kept 1 observation shows its notes on a page; Continue asks the Assistant', async () => {
    const asked: string[] = [];
    const { page, log, steps } = await open(row({ rid: 'r11', n: 11, name: 'Researcher 11', status: 'interrupted', finished: now - 52, step: '',
      findings: [{ kind: 'observation', text: 'A few small, very bright nuclei', sources: ['https://www.nature.com/articles/x'] }],
      steps: [{ at: now - 88, kind: 'step', text: 'Looked at a saved picture from today' }] }), (view) => { view.onAsk = (text) => asked.push(text); });
    expect(steps().at(-1)).toEqual(['stopped', 'Interrupted when Hooke closed', '']);
    const last = log.querySelectorAll('.step').at(-1)!;
    expect(last.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Kept 1 observation', 'Continue']);
    fire(button(last, 'Continue')!, 'click');
    expect(asked).toEqual(['Continue Researcher 11’s work.']);
    // Its notes are the page, in the reader on the right (its kind says Notes).
    expect(page.querySelector('.data-reader-research')!.querySelector('.rd-by')!.textContent).toMatch(/^Notes · /);
    expect(page.querySelector('.rtab-notes')!.textContent).toContain('What it noted so far');
    expect(page.querySelector('.rtab-notes')!.textContent).toContain('A few small, very bright nuclei');
    expect(page.querySelector('.desk-empty')!.hidden).toBe(true);
  });

  test('an earlier day is read only: Continue is there but cannot act', async () => {
    served = [row({ rid: 'r11', status: 'interrupted', finished: now - 52, step: '', day: '2026-10-20' })];
    const view = new ResearchView((command) => { sent.push(command); return { ok: true }; });
    await view.setDay('2026-10-20');
    view.show('r11');
    for (let i = 0; i < 4; i++) await settleMicrotasks();
    const last = byId.get('research-session')!.querySelector('.rlog')!.querySelectorAll('.step').at(-1)!;
    expect(button(last, 'Continue')).toBeUndefined(); // the offer is only made where it can act
    expect(byId.get('research-session')!.querySelector('textarea')!.disabled).toBe(true);
  });

  test('a link naming the researcher\'s Project (its sitting) opens it live, writable, with the selected output kept', async () => {
    const live = row({ rid: 'r602534', day: '2026-10-25', sitting: '2026-10-25.3', status: 'done', finished: now - 5, step: '', steps: [],
      outputs: [{ id: 'o1', kind: 'image', url: '/research/2026-10-25/r602534/outputs/a.png', caption: 'A figure', current: true } as unknown as ResearchSession['outputs'][number]] });
    served = [live];
    const view = new ResearchView((command) => { sent.push(command); return { ok: true }; });
    view.sitting = '2026-10-25.3';
    view.apply({ sessions: [live] });
    vi.stubGlobal('location', { hash: '#research/r602534?day=2026-10-25.3&output=o1', pathname: '/' });
    await view.openReference('2026-10-25.3', 'r602534', 'o1');
    for (let i = 0; i < 4; i++) await settleMicrotasks();
    expect(view.day).toBeNull(); // live: not an earlier day read from disk
    expect(fetched.some((url) => url.includes('/api/research-day/'))).toBe(false);
    expect(fetched.filter((url) => url.startsWith('/api/research/r602534'))).toEqual(['/api/research/r602534']); // its steps, live
    const page = byId.get('research-session')!;
    expect(page.textContent).not.toContain('not in this project any more');
    expect(page.querySelector('textarea')!.disabled).toBe(false);
    expect((page.querySelector('textarea') as unknown as { placeholder?: string }).placeholder).toBe('Ask Researcher 3 about its findings.');
    expect((view as unknown as { selectedArtifacts: Map<string, string> }).selectedArtifacts.get('2026-10-25/r602534')).toBe('output:o1');
    // Its calendar day names the same live researcher.
    await view.openReference('2026-10-25', 'r602534');
    expect(view.day).toBeNull();
  });

  test('a failed presentation says why in one line with Details; no clock once it ended; the foot counts only what was billed', async () => {
    const failedJob = { id: 'p1', state: 'failed' as const, findingRevision: { version: 2, digest: 'd2', complete: true }, model: 'gpt-6.1-sol', cost: 0, estimatedCost: 0.4,
      error: 'OpenAI has no credits left, so the presentation was not made.', created: now - 30, finished: now - 20 } as unknown as NonNullable<ResearchSession['presentationJobs']>[number];
    const { view, page } = await open(row({ status: 'done', finished: now - 5, step: '', steps: [], result: finding({ findingRevision: { version: 2, digest: 'd2', complete: true } }),
      findingRevisions: [{ version: 2, digest: 'd2', at: now - 40, complete: true }], presentationJobs: [failedJob] } as Partial<ResearchSession>));
    const stage = page.querySelector('.rtab-stage')!;
    // Said once, short (a job saved with the older, doubled words too); Details has the rest.
    expect(stage.querySelector('.rtab-stage-line')!.textContent).toBe('Presentation not made: OpenAI has no credits left.');
    expect(stage.textContent).not.toContain('production limit'); // its clock is for a job still being made
    fire(button(stage, 'Details')!, 'click');
    expect(events.map((e) => e.type)).toContain('hooke-research-activity');
    view.apply({ spentToday: 0.6 });
    expect(byId.get('research-spend')!.textContent).toBe('Research today: $0.20 · up to $0.40 more, receipts pending');
    // The server's estimated part is every researcher's today (another Project's too), from the same ledger: never read
    // as spent because the researcher it belongs to is not listed here.
    view.apply({ spentToday: 1.2, estimatedToday: 0.8 });
    expect(byId.get('research-spend')!.textContent).toBe('Research today: $0.40 · up to $0.80 more, receipts pending');
    view.apply({ spentToday: 0.4, estimatedToday: 0 });
    expect(byId.get('research-spend')!.textContent).toBe('Research today: $0.40');
  });

  test('partial findings: its short answer, then Continue', async () => {
    const { log } = await open(row({ status: 'partial', finished: now - 5, step: '', result: finding(), findingRevisions: [{ version: 1, digest: 'd1', at: now - 5, complete: false }] }));
    const offer = log.querySelectorAll('.m-q').at(-1)!;
    expect(offer.textContent).toContain('It wrote up before trying a trained model.');
    expect(button(offer, 'Continue')!.disabled).toBe(false);
  });

  test('could not finish: the reason, and Details opens Activity', async () => {
    const { log, steps } = await open(row({ status: 'failed', finished: now - 5, step: 'the model stopped answering' }));
    expect(steps().at(-1)).toEqual(['attention', 'Could not finish', 'the model stopped answering']);
    fire(button(log, 'Details')!, 'click');
    expect(events.map((e) => e.type)).toEqual(['hooke-research-activity']);
  });
});

describe('its questions: asked once, answered once, wherever the answer came from', () => {
  const asked = { id: 'q1', run: 0, what: '', why: 'it has worked for 60 minutes', keepGoing: true, at: now - 40 };
  const question = { at: now - 40, kind: 'request', text: 'Asked the user whether to keep going: it has worked for 60 minutes' };

  test('waiting for you: the question with Keep going and Not now, and no step row repeating it', async () => {
    const { log } = await open(row({ requests: [{ ...asked, status: 'asked' }], steps: [question] }));
    const q = log.querySelector('.m-q')!;
    expect(q.querySelector('p')!.textContent).toBe('Keep going? It has worked for 60 minutes.');
    expect(q.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Keep going', 'Not now']);
    expect(log.querySelectorAll('.step').some((s) => /keep going/i.test(s.textContent))).toBe(false);
    fire(button(q, 'Keep going')!, 'click');
    expect(sent.at(-1)).toMatchObject({ type: 'research-note', rid: 'r3', text: 'Yes, keep going.' });
  });

  test('answered through the Assistant or by voice: past tense, and the answer as your words', async () => {
    const { log } = await open(row({ status: 'done', finished: now, step: '', requests: [{ ...asked, status: 'answered', answer: 'Not now.', answeredAt: now - 20 }], steps: [question] }));
    expect(log.querySelector('.m-q')!.textContent).toBe('It asked whether to keep going (it has worked for 60 minutes).');
    expect(log.querySelector('.m-q')!.querySelectorAll('button')).toHaveLength(0);
    expect(log.querySelectorAll('.m-user').map((b) => b.textContent)).toEqual(['Not now.']);
  });

  test('answered on this page: the answer is your message already, never twice', async () => {
    const { log } = await open(row({ requests: [{ ...asked, status: 'answered', answer: 'Yes, keep going.', answeredAt: now - 30 }],
      interactionJobs: [{ id: 'j1', state: 'done', created: now - 30, request: { text: 'Yes, keep going.', kind: 'note' }, outcome: { ok: true } }] }));
    expect(log.querySelectorAll('.m-user').map((b) => b.textContent)).toEqual(['Yes, keep going.']);
  });

  test('finished without an answer: said so', async () => {
    const { log } = await open(row({ status: 'done', finished: now, step: '', requests: [{ ...asked, status: 'unanswered' }] }));
    expect(log.querySelector('.m-q')!.textContent).toBe('It asked whether to keep going (it has worked for 60 minutes). It finished without an answer.');
  });
});

describe('your notes and follow-ups', () => {
  test('a note says It reads this at its next step until its Got a note step; a follow-up holds the answer’s slot', async () => {
    const note = { id: 'n1', state: 'done', created: now - 30, request: { text: 'Count the small ones too', kind: 'note' }, outcome: { ok: true } };
    const first = await open(row({ interactionJobs: [note] }));
    expect(first.log.querySelector('.m-you')!.textContent).toBe('Count the small ones tooIt reads this at its next step');
    const read = await open(row({ interactionJobs: [note], steps: [{ at: now - 20, kind: 'note', text: 'Got a note: Count the small ones too' }] }));
    expect(read.log.querySelector('.m-you')!.textContent).toBe('Count the small ones too');
    expect(read.steps()[0]).toEqual(['note', 'Got a note', 'Count the small ones too']);
    const asking = await open(row({ status: 'done', finished: now - 60, step: '', result: finding(),
      interactionJobs: [{ id: 'f1', state: 'dispatched', created: now - 5, request: { text: 'Which would split them?', kind: 'followup' } }] }));
    expect(asking.log.querySelector('.slot')!.textContent).toBe('Working on it');
  });
});

describe('a run is one row, and what it made opens in the reader beside the conversation', () => {
  test('Ran Bright objects with its result as a chip; the chip opens the result in the reader, whose Show on Main ↗ leaves the Tab', async () => {
    const leave = vi.fn();
    const mask = { id: 'e7', kind: 'population', title: 'Bright regions mask · Bright objects · 22:18', summary: '', plain: '271 outlined, not shown now', state: 'saved', actions: [],
      day: 'p', version: 0, role: 'analysis', maker: { kind: 'researcher', id: 'r3', name: 'Researcher 3' } } as ReaderEntry;
    provideResearchReader({ entries: () => [mask], deps: { leave } });
    const { page, steps } = await open(row({ status: 'done', finished: now, step: '', result: finding(), findingRevisions: [{ version: 1, digest: 'd1', at: now, complete: true }],
      ran: [{ id: 'e7', operation: 'capability', capability: 'basic-bright-objects', at: now - 71, title: 'Bright regions mask', kind: 'population', count: 271 }],
      steps: [{ at: now - 80, kind: 'step', text: 'Asking the app to run Bright objects · Basic local method on the live view' }, { at: now - 70, kind: 'step', text: 'The app outlined 271 bright regions' }] }));
    expect(steps()[0]).toEqual(['done', 'Ran Bright objects', 'Outlined 271 bright regions']);
    const chip = page.querySelector('.chip')!;
    expect(chip.textContent).toBe('Bright regions mask');
    fire(chip, 'click');
    // In the reader, never leaving the Tab: its outlines and their count, said as its record says them.
    expect(researchReader()!.current()?.id).toBe('e7');
    const reader = page.querySelector('.data-reader-research')!;
    expect(reader.querySelector('.rd-switch')!.textContent).toBe('Bright regions mask');
    expect(reader.querySelector('.rd-by')!.textContent).toMatch(/^Outlines · 271 outlines/);
    fire(reader.querySelector('.rd-more')!, 'click');
    fire(button(menu()!, 'Show on Main ↗')!, 'click');
    expect(leave).toHaveBeenCalledWith(mask);
    expect(events.map((e) => e.type)).toEqual([]); // nothing else leaves: no event takes it elsewhere
  });
});

describe('the reader’s menus say what each does', () => {
  test('one version is listed once, ticked, in How it was made; ⋯ shows the work beside the live view, how it was made, a new window, and asks about the page', async () => {
    const { page } = await open(row({ status: 'partial', finished: now - 5, step: '', result: finding(),
      findings: [{ kind: 'observation', text: 'Touching nuclei join', sources: [] }], findingRevisions: [{ version: 1, digest: 'd1', at: now - 5, complete: false }] }));
    const reader = page.querySelector('.data-reader-research')!;
    // Its state where the status pill said it: its kind, then the server's words.
    expect(reader.querySelector('.rd-by')!.textContent).toBe('Report · Partial findings');
    fire(reader.querySelector('.rd-more')!, 'click');
    const more = menu()!;
    // One version: no Versions in ⋯ (it is in How it was made). Make a presentation needs a full report.
    expect(more.querySelectorAll('button').map((b) => b.textContent)).toEqual(['Show beside the live view', 'How it was made', 'Open in a new window', 'Ask about this page',
      'Open in new Tab', 'Add to Tab…', 'Move to trash']);
    fire(button(more, 'How it was made')!, 'click');
    expect(researchReader()!.currentView()).toBe('how');
    // How it was made: its one version ticked, then what it noted as it went (record_finding), with the rest of its record.
    expect(reader.querySelectorAll('.rd-ver-pick').map((b) => [b.textContent.replace(/ · .*$/, ''), b.getAttribute('aria-pressed')])).toEqual([['v1', 'true']]);
    expect(reader.textContent).toContain('What it noted:');
    expect(reader.textContent).toContain('Touching nuclei join');
    fire(reader.querySelector('.rd-more')!, 'click');
    fire(button(menu()!, 'Show beside the live view')!, 'click');
    expect(sent.at(-1)).toMatchObject({ type: 'present', do: 'open', source: { day: '2026-10-24', id: 'r3' } });
  });
});
