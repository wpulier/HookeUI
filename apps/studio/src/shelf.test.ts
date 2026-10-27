// The shelf (the Working strip's researchers, working-strip.ts) and the tray's Researchers tab, over a synthetic DOM: who
// is on the strip and what each pill says, what clicking and × do, the rings that still mark Researchers' cards, and
// the tab's count (2026-10-11: it said 4 with one card).
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { chipStatus, openedKey, progressOf, researcherCardView, researcherCell, researcherPill, researchersCount, researcherWords, Shelf, shelfChips } from './shelf';
import type { StripPill } from './working-strip';
import { ResearcherCards } from './researcher-cards';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';
import type { PresenterState, ResearchSession } from './protocol';

vi.mock('./research', () => ({ clean: (value: string) => value }));
vi.mock('./viewer', () => ({ outputOf: () => undefined }));

const NOW = 1_791_600_000;
let nodes: Map<string, TestElement>;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW * 1000);
  const dom = syntheticDOM();
  nodes = new Map([['researcher-cards', new TestElement('section')]]);
  vi.stubGlobal('document', Object.assign(dom.document, { getElementById: (id: string) => nodes.get(id) ?? null, activeElement: null }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function researcher(rid: string, status: ResearchSession['status'], extra: Partial<ResearchSession> = {}): ResearchSession {
  return { rid, n: Number(rid.slice(1)) || 1, name: `Researcher ${rid.slice(1)}`, brief: `Look into ${rid}`, deliverable: null,
    effort: 'quick', engine: 'test', purpose: 'research', label: '', objectId: null, status, step: '', spent: 0, cap: 0.25,
    seconds: 100, created: NOW - 60, started: NOW - 25, finished: null, snapshot: `/research/2026-10-11/${rid}/inputs/frame.jpg`,
    outputs: [], items: [], findings: [], corrections: [], history: [], canFollowUp: true, sitting: '2026-10-11', day: '2026-10-11',
    result: null as unknown as ResearchSession['result'], ...extra } as ResearchSession;
}
const done = (rid: string, extra: Partial<ResearchSession> = {}) =>
  researcher(rid, 'done', { result: { title: `Findings of ${rid}` } as ResearchSession['result'], ...extra });
const presenter = (extra: Partial<PresenterState> = {}): PresenterState =>
  ({ type: 'presenter', open: false, rid: null, index: 0, shelfRemoved: [], ...extra }) as PresenterState;
/** A shelf whose pills are kept as the strip gets them. */
function shelfWith(show: (rid: string) => void = () => {}, remove: (rid: string) => void = () => {}) {
  let pills: StripPill[] = [];
  const shelf = new Shelf(show, remove, (next) => { pills = next; });
  return { shelf, pills: () => pills, ids: () => pills.map((p) => p.id.replace('research:', '')), pill: (rid: string) => pills.find((p) => p.id === `research:${rid}`)! };
}

test('the shelf shows researchers working and finished work not opened, never quick lookups or other days', () => {
  const sessions = [
    researcher('r1', 'queued'), researcher('r2', 'running'), researcher('r3', 'revising', { outputs: [{ kind: 'image' } as never] }),
    done('r4'), done('r5', { restored: true }), researcher('r6', 'cancelled'), researcher('r7', 'failed'),
    researcher('r8', 'running', { purpose: 'description', label: 'Quick lookup' }), researcher('r9', 'running', { sitting: '2026-10-10' }),
    done('r10'), done('r11'),
  ];
  const shown = shelfChips(sessions, presenter({ shelfRemoved: ['r11'] }), '2026-10-11', new Set([openedKey(sessions[9])]));
  // Working (queued, running, revising), finished not opened (r4), stopped with nothing to open (r7). Not: restored
  // (r5), stopped by the user (r6), a description (r8), another day (r9), opened (r10) or taken off (r11).
  expect(shown.map((s) => s.rid).sort()).toEqual(['r1', 'r2', 'r3', 'r4', 'r7']);
});

test('news is the strip\'s rule: ready work, or a researcher that stopped on its own, not opened yet; isNew stays ready work', () => {
  const { shelf } = shelfWith();
  const failed = researcher('r7', 'failed', { finished: NOW - 5 });
  shelf.update([researcher('r7', 'running')], presenter(), '2026-10-11');
  shelf.update([failed, done('r4'), researcher('r6', 'cancelled'), researcher('r8', 'failed', { restored: true })], presenter(), '2026-10-11');
  expect(shelf.isNews(failed)).toBe(true);
  expect(shelf.isNew(failed)).toBe(false); // the Researchers count's green is for work to read
  expect(shelf.isNews(done('r4'))).toBe(true);
  expect(shelf.isNews(researcher('r6', 'cancelled'))).toBe(false); // stopped by the user: not news
  expect(shelf.isNews(researcher('r8', 'failed', { restored: true }))).toBe(false); // restored, never worked here
  expect(shelf.isNews(researcher('r2', 'running'))).toBe(false); // working: its Tab is kept for that
  shelf.markOpened('r7');
  expect(shelf.isNews(failed)).toBe(false);
});

test('a pill opens its finished work and leaves the strip; the Researchers tab keeps its card', () => {
  const opened: string[] = [];
  const { shelf, ids, pill } = shelfWith((rid) => opened.push(rid));
  const cards = new ResearcherCards(() => {}, undefined, (s) => shelf.isNew(s));
  const sessions = [done('r4'), researcher('r2', 'running')];
  shelf.update(sessions, presenter(), '2026-10-11');
  cards.update(sessions, presenter(), '2026-10-11');
  expect(ids().sort()).toEqual(['r2', 'r4']);
  expect(pill('r4')).toMatchObject({ cell: 'ready', urgency: 2, words: 'Findings ready' }); // green until opened
  pill('r4').open();
  expect(opened).toEqual(['r4']);
  expect(ids()).toEqual(['r2']);
  expect(shelf.isNew(sessions[0])).toBe(false);
  expect(cards.count()).toBe(2);
  // A follow-up: it works again (on the strip), then its new answer is news again until opened.
  shelf.update([researcher('r4', 'running', { result: sessions[0].result }), sessions[1]], presenter(), '2026-10-11');
  expect(ids().sort()).toEqual(['r2', 'r4']);
  shelf.update([done('r4', { finished: NOW + 30 }), sessions[1]], presenter(), '2026-10-11');
  expect(ids().sort()).toEqual(['r2', 'r4']);
  // Opened from anywhere else (its Tab, its page): the green pill leaves at once.
  shelf.markOpened('r4');
  expect(ids()).toEqual(['r2']);
  // One restored after a restart is not news, unless it works again here (the server keeps `restored` on it).
  shelf.update([done('r6', { restored: true })], presenter(), '2026-10-11');
  expect(ids()).toEqual([]);
  shelf.update([researcher('r6', 'running', { restored: true })], presenter(), '2026-10-11');
  shelf.update([done('r6', { restored: true, finished: NOW + 60 })], presenter(), '2026-10-11');
  expect(ids()).toEqual(['r6']);
});

test('× takes a pill off the strip only: the server is told, and its card stays in the Researchers tab', () => {
  const removed: string[] = [];
  const opened: string[] = [];
  const { shelf, ids, pill } = shelfWith((rid) => opened.push(rid), (rid) => removed.push(rid));
  const cards = new ResearcherCards(() => {});
  const sessions = [researcher('r2', 'running'), done('r4')];
  shelf.update(sessions, presenter(), '2026-10-11');
  cards.update(sessions, presenter(), '2026-10-11');
  expect(pill('r2').hide).toBe('Hide Researcher 2 from the strip. It stays in Researchers.');
  pill('r2').dismiss();
  expect(removed).toEqual(['r2']);
  expect(opened).toEqual([]); // hiding never opens or stops it
  expect(ids()).toEqual(['r4']);
  // Once the server's list says so, it stays off (a reload); the tab still lists both.
  shelf.update(sessions, presenter({ shelfRemoved: ['r2'] }), '2026-10-11');
  expect(ids()).toEqual(['r4']);
  expect(nodes.get('researcher-cards')!.querySelectorAll('.researcher-card').map((c) => c.dataset.rid).sort()).toEqual(['r2', 'r4']);
});

test('a working pill counts its time from its start, never a percent; a waiting or stopping one says so in words', () => {
  const r = researcher('r2', 'running', { started: NOW - 25, seconds: 100 });
  expect(progressOf(r, NOW)).toBeNull();
  expect(progressOf(r, NOW + 500)).toBeNull(); // elapsed time is not progress
  expect(progressOf(researcher('r1', 'queued'), NOW)).toBeNull();
  const working = researcherPill(r, () => {}, () => {});
  expect(working).toMatchObject({ id: 'research:r2', name: 'Researcher 2', cell: 'working', urgency: 1, counting: true, since: (NOW - 25) * 1000 });
  expect(working.title).toContain('Look into r2');
  expect(researcherPill(researcher('r1', 'queued'), () => {}, () => {})).toMatchObject({ cell: 'queued', counting: false, words: 'Waiting for a free slot' });
  const asking = researcher('r3', 'running', { requests: [{ id: 'q', what: 'a closer view', why: '', at: NOW, status: 'asked' }] });
  expect(researcherPill(asking, () => {}, () => {})).toMatchObject({ cell: 'needs', urgency: 0, words: 'Needs you', counting: false });
  // Researchers' rows and cards carry the same lit cell as the pill (no ring): a finished one is green.
  expect(researcherCell(done('r4'))).toBe('ready');
});

test("a pill's words are the mock's: what it made, partial findings, or why it stopped", () => {
  expect(researcherWords(done('r1'))).toBe('Findings ready');
  expect(researcherWords(done('r1', { outputs: [{ kind: 'html' } as never] }))).toBe('Lesson ready');
  expect(researcherWords(researcher('r1', 'partial', { outputs: [{ kind: 'image' } as never] }))).toBe('Partial findings');
  expect(researcherWords(researcher('r1', 'interrupted'))).toBe('Interrupted');
  expect(researcherWords(researcher('r1', 'failed'))).toBe('Could not finish');
  expect(researcherWords(researcher('r1', 'timeout'))).toBe('Stopped');
  expect(researcherPill(researcher('r1', 'failed'), () => {}, () => {})).toMatchObject({ cell: 'attention', urgency: 2 });
});

test('a researcher that could not finish says why on its pill, its chat card and its Tab', () => {
  const failed = researcher('r1', 'failed', { finished: NOW - 5, step: 'OpenAI says this account has no credits left. Add credits, or choose another model for Researching.' });
  expect(chipStatus(failed)).toBe('Researcher 1 could not finish: OpenAI says this account has no credits left. Add credits, or choose another model for Researching.');
  expect(chipStatus(researcher('r1', 'failed'))).toBe('Researcher 1 could not finish');
  expect(researcherPill(failed, () => {}, () => {}).title).toContain('could not finish: OpenAI says this account has no credits left.');
  expect(researcherCardView(failed).tip).toContain('Researcher 1 could not finish: OpenAI says');
  expect(chipStatus(researcher('r1', 'timeout'))).toBe('Researcher 1 stopped');
});

test('every researcher on the strip is a pill (no cap of four): the strip fits them, compact then +N', () => {
  const { shelf, ids } = shelfWith();
  shelf.update([1, 2, 3, 4, 5].map((n) => researcher(`r${n}`, 'running', { created: NOW - 100 + n })), presenter(), '2026-10-11');
  expect(ids()).toEqual(['r1', 'r2', 'r3', 'r4', 'r5']); // oldest first, as the strip orders them
});

test('the Researchers tab counts its cards, and says when one works, has new work or asks', () => {
  const fresh = () => false;
  const sessions = [researcher('r1', 'done', { result: { title: 'x' } as ResearchSession['result'] }),
    ...[2, 3, 4].map((n) => researcher(`r${n}`, 'running', { purpose: 'description', label: 'Quick lookup' }))];
  const cards = new ResearcherCards(() => {});
  cards.update(sessions, presenter(), '2026-10-11');
  const tally = researchersCount(sessions, '2026-10-11', fresh);
  expect(tally.count).toBe(1); // live on 2026-10-11 it said 4: three quick lookups were counted
  expect(tally.count).toBe(cards.count());
  expect(tally.state).toBe('');
  expect(researchersCount([...sessions, researcher('r5', 'queued')], '2026-10-11', fresh)).toMatchObject({ count: 2, working: 1, state: 'working' });
  expect(researchersCount(sessions, '2026-10-11', (s) => s.rid === 'r1').state).toBe('fresh');
  const asking = researcher('r6', 'running', { requests: [{ id: 'q1', what: '15 s of this field', why: '', at: NOW, status: 'asked' }] });
  expect(researchersCount([...sessions, asking], '2026-10-11', (s) => s.rid === 'r1').state).toBe('asking');
});

test('See full research carries the selected earlier finding into the research route', () => {
  const session = done('r1', { day: '2026-10-10', sitting: '2026-10-10' });
  const selected = presenter({ open: true, rid: 'r1', findingRevision: { version: 1, digest: 'original', complete: true } });
  new ResearcherCards(() => {}).update([session], selected, '2026-10-10');
  const href = (nodes.get('researcher-cards')!.querySelector('.researcher-full') as unknown as HTMLAnchorElement).href;
  const query = new URLSearchParams(href.split('?')[1]);
  expect(href.split('?')[0]).toBe('#research/r1');
  expect(query.get('day')).toBe('2026-10-10');
  expect(query.get('findings')).toBe('1');
  expect(query.get('output')).toBeNull();
});

test("the Researchers tab: this session's researchers, then those still working in other sessions, counted on its badge", () => {
  const here = '2026-10-12.2';
  const sessions = [
    researcher('r1', 'done', { sitting: here, result: { title: 'Here, done' } as ResearchSession['result'] }),
    researcher('r2', 'running', { sitting: here, created: NOW - 10 }), // the newest
    researcher('r3', 'running', { sitting: '2026-10-12', created: NOW - 30 }), // started in another session: still working
    researcher('r4', 'done', { sitting: '2026-10-12', result: { title: 'Elsewhere, done' } as ResearchSession['result'] }), // that session's
    researcher('r5', 'running', { sitting: '2026-10-11', purpose: 'description', label: 'Quick lookup' }),
  ];
  const cards = new ResearcherCards(() => {});
  cards.update(sessions, presenter(), here);
  const root = nodes.get('researcher-cards')!;
  const order = root.children.map((node) => node.classList.contains('researcher-elsewhere') ? `— ${node.textContent}` : node.dataset.rid);
  expect(order).toEqual(['r2', 'r1', '— Still working in other projects', 'r3']);
  expect('elsewhere' in root.children.find((node) => node.dataset.rid === 'r3')!.dataset).toBe(true);
  const tally = researchersCount(sessions, here, () => false);
  expect(tally).toMatchObject({ count: 3, working: 2, elsewhere: 1, state: 'working' });
  expect(tally.count).toBe(cards.count());
  // The strip keeps to this session's.
  const { shelf, ids } = shelfWith();
  shelf.update(sessions, presenter(), here);
  expect(ids().sort()).toEqual(['r1', 'r2']);
  // Nothing in this session yet: said so, and the other session's working one is still there.
  cards.update([sessions[2]], presenter(), here);
  expect(root.querySelector('.researcher-empty')?.textContent).toContain('No AI researchers yet');
  expect(root.querySelectorAll('.researcher-card').map((c) => c.dataset.rid)).toEqual(['r3']);
});

import { researcherTiming } from './researcher-cards';
test('researcher timing distinguishes elapsed time, observed updates and unknown older records', () => {
  const r = researcher('r2','running',{engine:'named-model', started:NOW-25,stepAt:NOW-3});
  expect(researcherTiming(r,NOW)).toBe('named-model · 25.0 s elapsed · 3.0 s since last step');
  expect(researcherTiming({...r,stepAt:undefined},NOW)).toContain('Last update unknown');
  expect(researcherTiming({...r,stepAt:NOW-45},NOW)).toContain('No recent update');
  expect(researcherTiming({...r,status:'done',finished:NOW-5},NOW+100)).toBe('named-model · 20.0 s elapsed');
});

test('a researcher\'s chat card says what its Tab and pill say: its own words while it works, its clock without them, then its state', () => {
  // The card under the request that started it (chat-cards.ts): "Researcher 5 · Choosing a model", "Working 0:12", "Needs you", "Findings ready".
  const choosing = researcherCardView(researcher('r5', 'running', { step: 'choosing a model', brief: 'Outline the nuclei with a model of its choice' }), NOW);
  expect(choosing).toMatchObject({ cell: 'working', title: 'Outline the nuclei with a model of its choice', sub: 'Researcher 5 · Choosing a model', counting: false });
  expect(choosing.tip).toBe('Outline the nuclei with a model of its choice · Working 0:25');
  expect(researcherCardView(researcher('r6', 'running', { started: NOW - 12 }), NOW)).toMatchObject({ sub: 'Researcher 6 · Working 0:12', counting: true });
  expect(researcherCardView(researcher('r4', 'running', { requests: [{ id: 'q', what: 'a steadier view', why: '', at: NOW, status: 'asked' }] }), NOW))
    .toMatchObject({ cell: 'needs', sub: 'Researcher 4 · Needs you', counting: false });
  expect(researcherCardView(researcher('r7', 'queued'), NOW)).toMatchObject({ cell: 'queued', sub: 'Researcher 7 · Waiting for a free slot', counting: false });
  expect(researcherCardView(done('r1'), NOW)).toMatchObject({ cell: 'ready', title: 'Findings of r1', sub: 'Researcher 1 · Findings ready', counting: false, tip: 'Findings of r1' });
  expect(researcherCardView(researcher('r2', 'partial', { result: { title: 'Compare methods' } as ResearchSession['result'] }), NOW))
    .toMatchObject({ cell: 'partial', sub: 'Researcher 2 · Partial findings' });
});
