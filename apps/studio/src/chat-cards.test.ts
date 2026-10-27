// The chat's researcher cards (chat-cards.ts; the workspace-feel plan, "The chat": ack, card, outcome): a card appears
// only for a researcher the server lists, under the request of this conversation that started it, matched by the
// session's requestId (the request's own steps carry it), by the answer's research-run outputs, or, for a typed request
// still being answered, by its turn; the three reconcile by researcher.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ChatModel } from './chatmodel';
import { cardGroups, researchersMade, type ResearchCardView } from './chat-cards';
import { ChatLog } from './chatlog';
import type { ResearchSession, ServerMessage, ToolRun } from './protocol';
import { syntheticDOM, TestElement, fire } from './workspace-windows.test-support';

const DAY = '2026-10-24';
const day: ServerMessage = { type: 'day', id: DAY, label: '', active: 't1', threads: [], subjects: [], prefs: {} };
const user = (text: string): ServerMessage => ({ type: 'chat', role: 'user', text, thread: 't1', day: DAY });
const request = (turn: number, state: 'running' | 'done' = 'running', steps?: ToolRun[]): ServerMessage =>
  ({ type: 'activity', id: `request:${DAY}:${turn}`, kind: 'request', text: '', at: 1, state, ref: { turnId: turn, channel: 'chat' }, thread: 't1', day: DAY, ...(steps ? { steps } : {}) });
const answer = (text: string, turn: number, actions: ToolRun[] = []): ServerMessage =>
  ({ type: 'chat', role: 'assistant', text, turnId: turn, thread: 't1', day: DAY, actions });
const started = (rid: string, requestId: string): ToolRun => ({ tool: 'start_researcher', ok: true, summary: 'started a researcher', ms: 40, requestId, actionId: `a-${rid}`,
  outputs: [{ kind: 'resource', resource: { kind: 'research-run', researcherId: rid, day: DAY, runId: 'x', run: 0 } }] });
function researcher(rid: string, n: number, fields: Partial<ResearchSession> = {}): ResearchSession {
  return { rid, n, name: `Researcher ${n}`, brief: `Brief of ${rid}`, deliverable: null, effort: 'standard', engine: 'GPT-6.1 Sol', purpose: 'research',
    label: '', objectId: null, status: 'running', step: '', spent: 0, cap: 1, seconds: 3600, created: 100 + n, started: 100 + n, finished: null, snapshot: '',
    outputs: [], items: [], findings: [], corrections: [], history: [], canFollowUp: true, day: DAY, thread: 't1', turnId: null, requestId: null,
    result: null as unknown as ResearchSession['result'], ...fields } as ResearchSession;
}
function lines(...messages: ServerMessage[]) {
  const model = new ChatModel(() => false);
  model.apply(day);
  for (const message of messages) model.apply(message);
  return model;
}
const conversation = (line: { role: string; event?: unknown; text: string; answerStatus?: unknown }) =>
  !line.event && (!!line.answerStatus || (line.role !== 'activity' && line.role !== 'system' && !!line.text.trim()));
const scope = { day: DAY, thread: 't1' };

describe('which request started a researcher', () => {
  test('a typed request still being answered: its card waits under the slot at the end, from the turn on its Activity line', () => {
    const m = lines(user('What am I looking at?'), request(1, 'done'), answer('Cells.', 1), user('Spawn two researchers'), request(2));
    const groups = cardGroups(m.lines, [researcher('r5', 5, { turnId: 2, requestId: 'RUN/r9' }), researcher('r6', 6, { turnId: 2, requestId: 'RUN/r9' })], scope, conversation);
    expect(groups).toEqual([{ key: 'turn:2', after: null, rids: ['r5', 'r6'] }]);
  });

  test('once answered, the cards sit under the answer; its outputs reconcile with the researchers listed earlier (one card each)', () => {
    const m = lines(user('Spawn two researchers'), request(2), answer('Sure.', 2, [started('r5', 'RUN/r9'), started('r6', 'RUN/r9')]));
    const said = m.lines.find(l => l.role === 'assistant')!;
    const groups = cardGroups(m.lines, [researcher('r6', 6, { turnId: 2, requestId: 'RUN/r9' }), researcher('r5', 5, { turnId: 2, requestId: 'RUN/r9' })], scope, conversation);
    expect(groups).toEqual([{ key: 'turn:2', after: said.key, rids: ['r5', 'r6'] }]);
  });

  test('a voice start (no turn) is matched by its request id on the request\'s steps; by the outputs when its id is unknown', () => {
    const m = lines(user('look into it'), answer('Starting one.', 7, [started('r8', 'RUN/r12')]));
    const said = m.lines.find(l => l.role === 'assistant')!;
    expect(cardGroups(m.lines, [researcher('r8', 8, { requestId: 'RUN/r12' })], scope, conversation)).toEqual([{ key: 'turn:7', after: said.key, rids: ['r8'] }]);
    expect(cardGroups(m.lines, [researcher('r8', 8)], scope, conversation)).toEqual([{ key: 'turn:7', after: said.key, rids: ['r8'] }]);
    // The request's Activity line carries its steps too (a spoken request's answer may be filed elsewhere).
    const spoken = lines(user('look into it'), request(9, 'done', [started('r9', 'RUN/r20')]));
    expect(cardGroups(spoken.lines, [researcher('r9', 9, { requestId: 'RUN/r20' })], scope, conversation)[0].rids).toEqual(['r9']);
  });

  test('nothing is invented: no card for a manual start, another conversation\'s or session\'s researcher, or a quick lookup', () => {
    const m = lines(user('Spawn one'), request(2));
    expect(cardGroups(m.lines, [
      researcher('r1', 1), // started by hand: no request here names it
      researcher('r2', 2, { turnId: 2, thread: 't9' }),
      researcher('r3', 3, { turnId: 2, sitting: '2026-10-24.2' }), // another Project of the same calendar day
      researcher('r4', 4, { turnId: 2, purpose: 'description' }),
      researcher('r5', 5, { turnId: 4 }), // a turn this conversation does not have
    ], scope, conversation)).toEqual([]);
  });

  test('a researcher belongs to its Project (its sitting), not its calendar day: every Project of the day gets its cards', () => {
    const m = lines(user('Spawn one'), request(2));
    const third = { day: '2026-10-25.3', thread: 't1' };
    expect(cardGroups(m.lines, [researcher('r4', 4, { turnId: 2, day: '2026-10-25', sitting: '2026-10-25.3' })], third, conversation))
      .toEqual([{ key: 'turn:2', after: null, rids: ['r4'] }]);
    // The day's first Project (its id is the date) does not take a later Project's researcher whose ids happen to match.
    expect(cardGroups(m.lines, [researcher('r4', 4, { turnId: 2, day: '2026-10-25', sitting: '2026-10-25.2' })], { day: '2026-10-25', thread: 't1' }, conversation))
      .toEqual([]);
    // With no sitting, the calendar day stands in.
    expect(cardGroups(m.lines, [researcher('r4', 4, { turnId: 2, day: '2026-10-25', sitting: '' })], { day: '2026-10-25', thread: 't1' }, conversation)[0].rids)
      .toEqual(['r4']);
  });

  test('an earlier request keeps its cards under its answer; groups follow the log\'s order', () => {
    const m = lines(user('One'), request(1, 'done'), answer('Sure.', 1, [started('r1', 'RUN/a')]), user('Two'), request(2), answer('Also sure.', 2, [started('r2', 'RUN/b')]));
    const answers = m.lines.filter(l => l.role === 'assistant');
    expect(cardGroups(m.lines, [researcher('r2', 2, { requestId: 'RUN/b' }), researcher('r1', 1, { requestId: 'RUN/a' })], scope, conversation))
      .toEqual([{ key: 'turn:1', after: answers[0].key, rids: ['r1'] }, { key: 'turn:2', after: answers[1].key, rids: ['r2'] }]);
    // Not answered yet and no longer the latest request: under the words that asked for it.
    const unanswered = lines(user('One'), request(1), user('Two'));
    const asked = unanswered.lines.find(l => l.role === 'user')!;
    expect(cardGroups(unanswered.lines, [researcher('r1', 1, { turnId: 1 })], scope, conversation)).toEqual([{ key: 'turn:1', after: asked.key, rids: ['r1'] }]);
  });

  test('a step names the researcher it started only by a research-run reference', () => {
    expect(researchersMade(started('r5', 'x'))).toEqual(['r5']);
    expect(researchersMade({ outputs: [{ kind: 'resource', resource: { kind: 'research-result', researcherId: 'r1' } }, { kind: 'outline', id: 'o1' }] })).toEqual([]);
    expect(researchersMade({})).toEqual([]);
  });
});

describe('drawn in the chat', () => {
  let dom: ReturnType<typeof syntheticDOM>;
  beforeEach(() => { dom = syntheticDOM(); vi.stubGlobal('document', dom.document); vi.useFakeTimers(); vi.setSystemTime(200_000); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  const view = (s: ResearchSession): ResearchCardView => ({ cell: s.status === 'done' ? 'ready' : 'working', label: s.name, title: s.brief,
    sub: `${s.name} · ${s.status === 'done' ? 'Findings ready' : s.step || `Working 0:${String(Math.round(Date.now() / 1000 - s.started!)).padStart(2, '0')}`}`,
    counting: s.status !== 'done' && !s.step, tip: s.brief });
  function chat() {
    const root = new TestElement('ol'), sessions = new Map<string, ResearchSession>(), opened: string[] = [];
    const log = new ChatLog(root as unknown as HTMLOListElement, new TestElement('div') as unknown as HTMLElement, {
      research: (rid) => sessions.get(rid), show: vi.fn(), describe: vi.fn(), hint: vi.fn(), voiceLive: () => false, unread: vi.fn(),
      researchCards: { list: () => sessions.values(), view, open: (rid) => opened.push(rid) } });
    log.apply(day);
    return { log, root, sessions, opened, order: () => root.children.map(n => n.dataset.role ?? n.className) };
  }
  const cards = (root: TestElement) => root.querySelectorAll('.chat-rcard');

  test('ack, card, outcome: the cards arrive under your words as the server lists them, and the answer takes the slot\'s place above them', () => {
    const { log, root, sessions, opened, order } = chat();
    log.apply(user('Spawn two researchers'));
    log.thinking(true);
    log.apply(request(2));
    expect(order()).toEqual(['user', 'thinking']);
    sessions.set('r5', researcher('r5', 5, { turnId: 2, requestId: 'RUN/r9', step: 'Choosing a model', started: 190 }));
    log.researchChanged();
    expect(order()).toEqual(['user', 'thinking', 'cards']);
    expect(cards(root).map(c => c.textContent)).toEqual(['Brief of r5Researcher 5 · Choosing a model']);
    expect(cards(root)[0].dataset.arrived).toBe(''); // it rises in once
    sessions.set('r6', researcher('r6', 6, { turnId: 2, requestId: 'RUN/r9', started: 190 }));
    log.researchChanged();
    expect(cards(root).map(c => c.dataset.rid)).toEqual(['r5', 'r6']);
    expect(cards(root)[1].textContent).toContain('Researcher 6 · Working 0:10');
    vi.advanceTimersByTime(2000); // its clock ticks; nothing else is redrawn
    expect(cards(root)[1].textContent).toContain('Working 0:12');
    log.thinking(false);
    log.apply(answer('Sure.', 2, [started('r5', 'RUN/r9'), started('r6', 'RUN/r9')]));
    expect(order()).toEqual(['user', 'assistant', 'cards']);
    expect(cards(root)).toHaveLength(2); // reconciled with the outputs: still one card each
    fire(cards(root)[1], 'click');
    expect(opened).toEqual(['r6']);
  });

  test('a card follows its researcher and goes when the server no longer lists it; another conversation clears them', () => {
    const { log, root, sessions } = chat();
    log.apply(user('Spawn one')); log.apply(request(2)); log.apply(answer('Sure.', 2, [started('r5', 'RUN/r9')]));
    sessions.set('r5', researcher('r5', 5, { requestId: 'RUN/r9', step: 'Choosing a model' }));
    log.researchChanged();
    const card = cards(root)[0];
    sessions.set('r5', researcher('r5', 5, { requestId: 'RUN/r9', status: 'done' }));
    log.researchChanged();
    expect(cards(root)[0]).toBe(card); // updated in place
    expect(card.textContent).toContain('Researcher 5 · Findings ready');
    expect(card.dataset.state).toBe('ready');
    sessions.clear(); log.researchChanged();
    expect(cards(root)).toHaveLength(0);
    sessions.set('r5', researcher('r5', 5, { requestId: 'RUN/r9' })); log.researchChanged();
    log.apply({ type: 'thread', day: DAY, thread: 't2', title: '', summary: '', lines: [] });
    expect(cards(root)).toHaveLength(0);
  });
});
