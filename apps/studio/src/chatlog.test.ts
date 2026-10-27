// The chat's rules (AGENTS.md "One brain", "Sessions"): it shows the open conversation only,
// a replay replaces it and says nothing new, late tool results update their answer in place,
// and a spoken line is not shown twice in the studio that is talking.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ChatModel, endsVoiceTurn, type Change } from './chatmodel';
import { ChatLog, SLOT_GRACE_MS } from './chatlog';
import type { ChatAction, ChatLine, ServerMessage, ToolRun, Trial } from './protocol';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

const chat = (fields: Partial<ChatLine> = {}): ChatLine => ({ type: 'chat', role: 'assistant', text: 'A stoma.', ...fields });
const running = (tool: string): ToolRun => ({ tool, ok: null, summary: `${tool} running`, ms: 0 });
const action = (turnId: number, tool: string, fields: Partial<ChatAction> = {}): ChatAction => ({
  type: 'chat-action', turnId, tool, ok: true, summary: `${tool} done`, ms: 900, ...fields,
});
const day = (active: string): ServerMessage => ({ type: 'day', id: '2026-10-05', label: '', active, threads: [], subjects: [], prefs: {} });
const replay = (thread: string, lines: ServerMessage[]): ServerMessage => ({
  type: 'thread', day: '2026-10-05', thread, title: '', lines, summary: '',
});
const hints = (changes: Change[]) => changes.filter((c) => c.kind === 'hint');
const texts = (model: ChatModel) => model.lines.map((l) => l.text);

function model(voiceLive = false): ChatModel {
  const m = new ChatModel(() => voiceLive);
  m.apply(day('t1'));
  return m;
}

describe('conversations', () => {
  test('a delayed query replaces only its original answer and survives replay', () => {
    const m = model();
    const lines = [chat({ role: 'user', text: 'Measure these.', turnId: 4 }),
      chat({ text: 'Measuring.', turnId: 4, actions: [running('query_population')] }),
      action(4, 'query_population', { summary: 'c1 92 px' }),
      action(4, 'query_population', { summary: 'c1 92 px', answer: 'The longest is 92 pixels.' })];
    lines.forEach((line) => m.apply(line));
    expect(texts(m)).toEqual(['Measure these.', 'The longest is 92 pixels.']);
    expect(m.lines[1].actions).toHaveLength(1);
    const other = model();
    expect(hints(other.apply(replay('t1', lines)))).toEqual([]);
    expect(texts(other)).toEqual(texts(m));
    expect(m.apply(action(4, 'query_population', { thread: 't2', answer: 'Elsewhere.' }))).toEqual([]);
    expect(texts(m)[1]).toBe('The longest is 92 pixels.');
  });

  test("a line for another conversation is dropped; one for this or none is shown", () => {
    const m = model();
    expect(m.apply(chat({ text: 'elsewhere', thread: 't2' }))).toEqual([]);
    m.apply(chat({ text: 'here', thread: 't1' }));
    m.apply(chat({ text: 'old server' })); // no thread: the open one
    expect(texts(m)).toEqual(['here', 'old server']);
  });

  test("a late tool result for another conversation changes nothing and is not a hint", () => {
    const m = model();
    m.apply(chat({ turnId: 4, actions: [running('dissect')] }));
    expect(m.apply(action(4, 'dissect', { thread: 't2' }))).toEqual([]);
    expect(m.lines[0].actions[0].ok).toBeNull();
  });

  test('switching conversations follows the day', () => {
    const m = model();
    m.apply(day('t2'));
    m.apply(chat({ text: 'one', thread: 't1' }));
    m.apply(chat({ text: 'two', thread: 't2' }));
    expect(texts(m)).toEqual(['two']);
  });
});

describe('replay', () => {
  test('replaces the log, opens its conversation and never hints', () => {
    const m = model();
    m.apply(chat({ text: 'before' }));
    const changes = m.apply(replay('t3', [
      chat({ role: 'user', text: 'What is that?' }),
      chat({ text: 'A guard cell.', turnId: 7, actions: [running('outline')] }),
      action(7, 'outline', { summary: 'outlined the guard cell' }),
      action(99, 'zoom', { summary: 'opened the magnifier' }), // its answer is not in the replay
    ]));
    expect(changes[0]).toEqual({ kind: 'clear' });
    expect(hints(changes)).toEqual([]);
    expect(texts(m)).toEqual(['What is that?', 'A guard cell.']);
    expect(m.lines[1].actions[0]).toMatchObject({ ok: true, summary: 'outlined the guard cell' });
    expect(m.active).toBe('t3');
  });

  test('a replayed spoken line is shown even while voice is live', () => {
    const m = model(true);
    m.apply(replay('t1', [chat({ role: 'user', text: '... what is that', voice: true, echo: true })]));
    expect(texts(m)).toEqual(['what is that']);
    expect(m.lines[0].voice).toBe(true);
  });

  test('stops any live transcript: what is said next is a new line', () => {
    const m = model();
    m.heardSpeech('what is', true);
    m.apply(replay('t1', []));
    m.heardSpeech('hello', false);
    expect(texts(m)).toEqual(['hello']);
  });
});

describe('late tool results', () => {
  test('update the running action of that tool in that answer', () => {
    const m = model();
    m.apply(chat({ role: 'user', text: 'Break it down', turnId: 5 })); // shares the turn, has no actions
    m.apply(chat({ turnId: 5, actions: [running('zoom'), running('dissect')] }));
    m.apply(chat({ turnId: 6, actions: [running('dissect')] }));
    const changes = m.apply(action(5, 'dissect', { ok: false, summary: 'could not break it down' }));
    expect(changes).toEqual([{ kind: 'action', line: m.lines[1], index: 1 }]);
    expect(m.lines[1].actions[0].ok).toBeNull();
    expect(m.lines[1].actions[1]).toMatchObject({ ok: false, summary: 'could not break it down' });
    expect(m.lines[2].actions[0].ok).toBeNull();
  });

  test('an action with nothing to say is not drawn, so it is not updated', () => {
    const m = model();
    m.apply(chat({ turnId: 5, actions: [{ tool: 'zoom', ok: null, summary: '', ms: 0 }] }));
    expect(m.lines[0].actions).toEqual([]);
  });

  test('a failure for an answer not in the log goes to Activity, never a toast of its raw summary (2026-10-11)', () => {
    const m = model();
    const [change] = m.apply(action(42, 'count', { ok: false, summary: 'counting failed' }));
    expect(change.kind).toBe('add');
    expect(m.lines).toHaveLength(1);
    expect(m.lines[0].role).toBe('activity');
    expect(m.lines[0].actions[0].ok).toBe(false);
    expect(m.apply(action(42, 'count', { summary: '' }))).toEqual([]);
  });

  test('a second result for the same tool does not overwrite the finished one', () => {
    const m = model();
    m.apply(chat({ turnId: 5, actions: [running('zoom')] }));
    m.apply(action(5, 'zoom'));
    expect(hints(m.apply(action(5, 'zoom', { summary: 'zoomed again' })))).toHaveLength(1);
    expect(m.lines[0].actions[0].summary).toBe('zoom done');
  });
});

describe('the voice echo', () => {
  const echo = chat({ role: 'user', text: 'what is that', voice: true, echo: true });

  test('is skipped while this studio is talking or connecting', () => {
    let state = 'live';
    const m = new ChatModel(() => state === 'live' || state === 'connecting');
    expect(m.apply(echo)).toEqual([]);
    state = 'connecting';
    expect(m.apply(echo)).toEqual([]);
    expect(m.lines).toEqual([]);
  });

  test('is shown otherwise (another studio is talking)', () => {
    const m = model(false);
    m.apply(echo);
    expect(texts(m)).toEqual(['what is that']);
  });

  test('a line shown live is not shown again when its closing echo comes after Stop (the review of #200)', () => {
    let live = true;
    const m = new ChatModel(() => live);
    m.heardSpeech('show the cells', false);
    m.apply(chat({ role: 'user', text: 'show the cells', voice: true, echo: true, lineId: 'v1', open: true }));
    live = false; // Stop pressed while the line was open: the server files it whole as the session ends
    expect(m.apply(chat({ role: 'user', text: 'show the cells', voice: true, echo: true, lineId: 'v1' }))).toEqual([]);
    expect(texts(m)).toEqual(['show the cells']);
    // another line, never shown here, still is
    m.apply(chat({ role: 'user', text: 'from the other studio', voice: true, echo: true, lineId: 'v2' }));
    expect(texts(m)).toEqual(['show the cells', 'from the other studio']);
  });

  test("this studio's own line ends when the server says the request settled or the reply ended", () => {
    const m = model(true);
    m.heardSpeech('show the cells', false);
    m.apply(chat({ role: 'user', text: 'show the cells', voice: true, echo: true, lineId: 'v1' })); // settled
    m.heardSpeech('now count them', false);
    m.saidSpeech('Here they are.');
    m.apply(chat({ role: 'assistant', text: 'Here they are.', voice: true, echo: true, lineId: 'v2' })); // ended
    m.saidSpeech('Twelve.');
    expect(texts(m)).toEqual(['show the cells', 'now count them', 'Here they are.', 'Twelve.']);
  });

  test('a spoken line that is not an echo is shown while talking', () => {
    const m = model(true);
    m.apply(chat({ text: 'Researcher 2 finished.', voice: true, proactive: true }));
    expect(m.lines).toHaveLength(1);
  });
});

describe('hello', () => {
  test('resets the log and the live transcripts but keeps the conversation', () => {
    const m = model();
    m.apply(chat());
    m.saidSpeech('It is a ');
    expect(m.apply({ type: 'hello', health: {} as never })).toEqual([{ kind: 'clear' }]);
    expect(m.empty).toBe(true);
    m.saidSpeech('stoma.');
    expect(texts(m)).toEqual(['stoma.']);
    expect(m.active).toBe('t1');
  });
});

describe('live voice transcripts', () => {
  test('interim pieces replace each other; final pieces of one request add to one line', () => {
    const m = model();
    m.heardSpeech('what', true);
    m.heardSpeech('what is that', true);
    m.heardSpeech(', what is that?', false);
    m.heardSpeech('and that', false);
    expect(texts(m)).toEqual(['what is that? and that']);
  });

  test('the voice adds to one line until its turn ends', () => {
    const m = model();
    m.saidSpeech('A guard ');
    m.saidSpeech('cell.');
    m.turnEnd();
    m.saidSpeech('Anything else?');
    m.turnEnd();
    m.heardSpeech('no', false);
    m.turnEnd();
    m.saidSpeech('Fine.');
    expect(texts(m)).toEqual(['A guard cell.', 'Anything else?', 'no', 'Fine.']);
    expect(m.lines.map((l) => l.role)).toEqual(['assistant', 'assistant', 'user', 'assistant']);
  });

  test('one reply and one request stay one line each when their pieces interleave (live 2026-10-07 16:19)', () => {
    const m = model();
    m.saidSpeech(' Yes,');
    m.heardSpeech('the researcher will', false);
    m.heardSpeech('be able to do', false);
    m.saidSpeech(' the tools I');
    m.heardSpeech('this', false);
    m.saidSpeech(' have can’t run a motion model.');
    expect(texts(m)).toEqual(['Yes, the tools I have can’t run a motion model.', 'the researcher will be able to do this']);
    expect(m.lines.map((l) => l.role)).toEqual(['assistant', 'user']);
  });

  test('a long gap without a piece starts a new line', () => {
    let t = 0;
    const m = new ChatModel(() => false, () => t);
    m.saidSpeech('First.');
    t += 20_000;
    m.saidSpeech('Later.');
    m.heardSpeech('a', false);
    t += 20_000;
    m.heardSpeech('b', false);
    expect(texts(m)).toEqual(['First.', 'Later.', 'a', 'b']);
  });

  test('a spoken line the server grows is one line, updated in place, and a replay shows it whole', () => {
    const m = model();
    m.apply(chat({ voice: true, lineId: 'v1', text: ' Yes,', open: true }));
    const changes = m.apply(chat({ voice: true, lineId: 'v1', text: ' Yes, the tools I have.' }));
    expect(changes.map((c) => c.kind)).toEqual(['text']);
    expect(texts(m)).toEqual(['Yes, the tools I have.']);
    m.apply(replay('t1', [chat({ voice: true, lineId: 'v1', text: ' Yes, the tools I have.' })]));
    expect(texts(m)).toEqual(['Yes, the tools I have.']);
  });

  test('a replay never lights the unread dot: nothing in it is new', () => {
    const m = model();
    const unread = (changes: Change[]) => changes.flatMap((c) => (c.kind === 'add' ? [c.unread] : []));
    expect(unread(m.apply(replay('t1', [chat(), chat({ role: 'user', text: 'hi' })])))).toEqual([false, false]);
  });

  test('the user is not a reason for the unread dot; the app is', () => {
    const m = model();
    const unread = (changes: Change[]) => changes.flatMap((c) => (c.kind === 'add' ? [c.unread] : []));
    expect(unread(m.apply(chat({ role: 'user', text: 'hi' })))).toEqual([false]);
    expect(unread(m.apply(chat()))).toEqual([true]);
    expect(unread(m.system('Voice: no microphone'))).toEqual([true]);
  });
});

describe('blind trial answers', () => {
  const trial = (arms: Partial<Trial['arms'][number]>[], kind: Trial['kind'] = 'ask'): Trial => ({
    id: 'tr1', kind, input: { question: 'what is it?' }, frameId: 1, voted: false,
    arms: arms.map((a, i) => ({ letter: 'ABC'[i], done: false, failed: false, objectIds: [], answer: '', labels: [], ...a })),
  });

  test('are added once per trial and arm, as each arrives', () => {
    const m = model();
    expect(m.arms(trial([{ done: true, answer: 'A stoma.' }, {}]))).toHaveLength(1);
    expect(m.arms(trial([{ done: true, answer: 'A stoma.' }, { done: true, failed: true }]))).toHaveLength(1);
    expect(m.arms(trial([{ done: true, answer: 'A stoma.' }, { done: true, failed: true }]))).toEqual([]);
    expect(m.lines.map((l) => [l.tag, l.text])).toEqual([
      ['Answer A', 'A stoma.'], ['Answer B', 'This option failed to answer.'],
    ]);
  });

  test('outline trials post nothing', () => {
    expect(model().arms(trial([{ done: true }], 'outline'))).toEqual([]);
  });

  test('come back after a reload or another conversation until the vote, then stay voted', () => {
    const m = model();
    m.arms(trial([{ done: true, answer: 'x' }, { done: true, answer: 'y' }]));
    m.apply(replay('t1', [chat({ text: 'earlier' })]));  // a reload replays the conversation after the trial
    expect(m.lines.map((l) => l.text)).toEqual(['earlier', 'x', 'y']);
    m.reveal('tr1', { A: { label: 'Gemini' }, B: { label: 'GPT' } }, 'A');
    m.apply(replay('t1', []));
    expect(m.lines).toEqual([]);  // voted: not posted again
  });

  test('a dropped trial does not come back', () => {
    const m = model();
    m.arms(trial([{ done: true, answer: 'x' }]));
    m.endTrial();
    m.apply(replay('t1', []));
    expect(m.lines).toEqual([]);
  });

  test('the vote reveals which option each was', () => {
    const m = model();
    m.arms(trial([{ done: true, answer: 'x' }, { done: true, answer: 'y' }]));
    m.reveal('tr1', { A: { label: 'Gemini' }, B: { label: 'GPT' } }, 'B');
    expect(m.lines.map((l) => l.tag)).toEqual(['Answer A, Gemini', 'Answer B, GPT (your pick)']);
  });
});

describe('which voice events end the voice turn', () => {
  test('the voice going quiet does; the user speaking over it does not', () => {
    expect(endsVoiceTurn({ kind: 'turn_complete', data: { reason: 'quiet' } })).toBe(true);
    expect(endsVoiceTurn({ kind: 'turn_complete', data: { reason: 'user_spoke' } })).toBe(false);
    expect(endsVoiceTurn({ kind: 'interrupted' })).toBe(false);
  });
});

describe('sessions: the chat files by the session id, whole and opaque', () => {
  const sid = '2026-10-12.2'; // a second session on Oct 12: never read as a date
  const daySid = (active: string, id: string): ServerMessage => ({ type: 'day', id, label: '', active, threads: [], subjects: [], prefs: {} });
  test("another session's lines are not this one's, though its conversations have the same t1", () => {
    const m = new ChatModel(() => false);
    m.apply(daySid('t1', sid));
    m.apply(chat({ text: 'here', thread: 't1', day: sid }));
    m.apply(chat({ text: 'from the other session of the same date', thread: 't1', day: '2026-10-12' }));
    m.apply(chat({ text: 'an older server says no session', thread: 't1' }));
    expect(m.apply(action(4, 'dissect', { thread: 't1', day: '2026-10-12', answer: 'Elsewhere.' }))).toEqual([]);
    expect(texts(m)).toEqual(['here', 'an older server says no session']);
    // Its replay is all its own; switching sessions replaces the log with the other one's.
    m.apply({ type: 'thread', day: '2026-10-12', thread: 't1', title: '', summary: '', lines: [chat({ text: 'Leaf, earlier' })] });
    expect(m.activeDay).toBe('2026-10-12');
    expect(texts(m)).toEqual(['Leaf, earlier']);
    m.apply(chat({ text: 'late, from the session left', thread: 't1', day: sid }));
    expect(texts(m)).toEqual(['Leaf, earlier']);
  });
});

describe('authoritative voice captions', () => {
  test('shows one stable line through growth and replay even while Talk is live', () => {
    const m = new ChatModel(() => true);
    const line = {type: 'chat' as const, role: 'user' as const, voice: true, echo: false, lineId: 'voice-1', speechSession: 's', at: 10, text: 'Hey'};
    m.apply(line);
    m.apply({...line, text: 'Hey, what are you seeing here?', open: true});
    m.apply({...line, text: 'Hey, what are you seeing here?'});
    expect(m.lines.map(l => l.text)).toEqual(['Hey, what are you seeing here?']);
  });
  test('orders late captions by speech time within their session', () => {
    const m = new ChatModel(() => true);
    m.apply({type: 'chat', role: 'assistant', voice: true, speechSession: 's', lineId: 'a', at: 12, text: 'Hello'});
    m.apply({type: 'chat', role: 'user', voice: true, speechSession: 's', lineId: 'u', at: 10, text: 'Hi'});
    expect(m.lines.map(l => l.text)).toEqual(['Hi', 'Hello']);
  });
});

// Your words as you wrote them: the composer takes several lines (Shift+Enter), and the bubble keeps its line breaks.
describe('drawn: your words', () => {
  beforeEach(() => { vi.stubGlobal('document', syntheticDOM().document); });
  afterEach(() => vi.unstubAllGlobals());
  test('a message of several lines keeps them in its text, and the bubble draws them (pre-wrap)', async () => {
    const root = new TestElement('ol');
    const log = new ChatLog(root as unknown as HTMLOListElement, new TestElement('div') as unknown as HTMLElement, {
      research: () => undefined, show: () => {}, describe: () => {}, hint: () => {}, voiceLive: () => false, unread: () => {} });
    log.apply(day('t1'));
    log.apply(chat({ role: 'user', text: 'line one\nline 2\n\nline 4', thread: 't1' }));
    const text = root.children.find((node) => node.dataset.role === 'user')!.querySelector('.message-text')!;
    expect(text.textContent).toBe('line one\nline 2\n\nline 4');
    const { readFileSync } = await import('node:fs');
    const { fileURLToPath } = await import('node:url');
    const css = readFileSync(fileURLToPath(new URL('./workspace-feel.css', import.meta.url)), 'utf8');
    expect(css).toMatch(/li\[data-role='user'\] \.message-text \{ white-space: pre-wrap;/);
  });
});

// The answer's slot (chatlog.ts drawWorking; the workspace-feel plan, "One slot for the answer"): a small lit cell and
// calm words where the answer will be, aligned with answers; replaced in place by the answer, never leaving a gap; it
// clears on the answer, an unpublished-answer status, a standalone step, another conversation, the request's own reply,
// or SLOT_GRACE_MS after thinking ends, so it can never stick.
describe('drawn: the answer\'s slot', () => {
  beforeEach(() => { vi.stubGlobal('document', syntheticDOM().document); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
  function drawn() {
    const root = new TestElement('ol'), cells: string[] = [];
    const log = new ChatLog(root as unknown as HTMLOListElement, new TestElement('div') as unknown as HTMLElement, {
      research: () => undefined, show: () => {}, describe: () => {}, hint: () => {}, voiceLive: () => false, unread: () => {},
      cells: { make: (state) => { cells.push(`make:${state}`); const cell = new TestElement('svg'); cell.dataset.state = state; return cell as unknown as Element; },
        set: (cell, state) => { cells.push(`set:${state}`); (cell as unknown as TestElement).dataset.state = state; } } });
    log.apply(day('t1'));
    const slot = () => root.children.find((node) => node.className === 'chat-slot');
    return { log, root, cells, slot, roles: () => root.children.map((node) => node.dataset.role) };
  }
  const asked = (text = 'What is this?'): ChatLine => chat({ role: 'user', text, thread: 't1' });

  test('it shows under your words with its lit cell and calm words, and the answer takes its place', () => {
    const { log, slot, roles, cells } = drawn();
    log.apply(asked());
    log.thinking(true);
    expect(roles()).toEqual(['user', 'thinking']);
    expect(slot()!.textContent).toBe('Working on it');
    expect(slot()!.children[0].dataset.state).toBe('working');
    log.thinking(true, 'answering');
    expect(slot()!.textContent).toBe('Writing the answer');
    log.thinking(false); // just before its answer is filed: the slot waits for it
    expect(roles()).toEqual(['user', 'thinking']);
    log.apply(chat({ text: 'A guard cell.', turnId: 3, thread: 't1' }));
    expect(roles()).toEqual(['user', 'assistant']);
    expect(cells).toEqual(['make:working']);
  });

  test('an unpublished-answer status, a standalone step or the request\'s own reply clears it; a late step of an earlier request does not', () => {
    const status = drawn();
    status.log.apply(asked()); status.log.thinking(true);
    status.log.apply({ type: 'chat-action', turnId: 4, thread: 't1', standalone: true, answerStatus: 'not-published', calls: 1, model: 'Gemini' });
    expect(status.slot()).toBeUndefined();

    const standalone = drawn();
    standalone.log.apply(chat({ text: 'Earlier.', turnId: 2, thread: 't1' }));
    standalone.log.apply(asked()); standalone.log.thinking(true);
    standalone.log.apply(action(2, 'outline', { standalone: true, thread: 't1' })); // the earlier request's late step
    expect(standalone.slot()).toBeDefined();
    standalone.log.apply(action(5, 'outline', { standalone: true, thread: 't1' })); // this request's receipt, with no model answer
    expect(standalone.slot()).toBeUndefined();

    const reply = drawn();
    reply.log.apply(asked()); reply.log.thinking(true); reply.log.thinking(false);
    reply.log.settled(); // the ask's result: the request is over (a failure is said in the composer's status line)
    expect(reply.slot()).toBeUndefined();
  });

  test('another conversation clears it; the same one drawn again while its answer is written keeps it', () => {
    const { log, slot } = drawn();
    log.apply(asked()); log.thinking(true);
    log.apply(replay('t1', [asked()]));
    expect(slot()).toBeDefined();
    log.apply(replay('t2', []));
    expect(slot()).toBeUndefined();
  });

  test('about two seconds after thinking ends with no answer, it goes, so it can never stick', () => {
    const { log, slot } = drawn();
    log.apply(asked()); log.thinking(true); log.thinking(false);
    vi.advanceTimersByTime(SLOT_GRACE_MS - 1);
    expect(slot()).toBeDefined();
    vi.advanceTimersByTime(1);
    expect(slot()).toBeUndefined();
    // Thinking again within the wait keeps it.
    log.thinking(true); log.thinking(false); vi.advanceTimersByTime(SLOT_GRACE_MS / 2); log.thinking(true);
    vi.advanceTimersByTime(SLOT_GRACE_MS * 2);
    expect(slot()!.textContent).toBe('Working on it');
  });

  test('its receipts are facts in quieter words: a lost connection, a photo displayed, earlier work with no final result', () => {
    const lost = drawn();
    lost.log.apply(asked()); lost.log.thinking(true); lost.log.connected(false);
    expect(lost.slot()!.dataset.role).toBe('work-unknown');
    expect(lost.slot()!.textContent).toBe('Connection lost · work status unavailable');
    expect(lost.cells).toEqual(['make:working', 'set:stopped']);
    const photo = drawn();
    photo.log.apply(asked());
    photo.log.apply(chat({ text: 'Here it is.', turnId: 6, thread: 't1', actions: [{ tool: 'reference_photo', ok: true, summary: 'shown', ms: 9 }] }));
    expect(photo.slot()!.dataset.role).toBe('work-complete');
    expect(photo.slot()!.textContent).toBe('Photo displayed');
    const earlier = drawn();
    earlier.log.apply(replay('t1', [chat({ text: 'Looking.', turnId: 7, actions: [running('reference')] })]));
    expect(earlier.slot()!.textContent).toBe('Earlier work has no final result recorded');
  });
});
