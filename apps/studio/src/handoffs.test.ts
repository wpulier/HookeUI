// Work in flight while you keep talking (handoffs.ts; Will, 29 Oct: "if its a parallel process and you keep talking, we
// have to have some hyper clear way to communicate thats whats happening"): each request the voice hands to Hooke's brain
// is one row under the voice line that started it, working until the brain's own answer, then done or not with its
// reason; the strip over the composer lists what runs, and goes when nothing does.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ChatModel } from './chatmodel';
import { ChatLog } from './chatlog';
import { FlightStrip, flightEntries, flightOrder, flightPills, handoffState, handoffView, plainWords, VOICE_SOURCE, voiceEntries, type FlightEntry } from './handoffs';
import type { HandoffLine, ServerMessage, ToolRun } from './protocol';
import { fire, syntheticDOM, TestElement } from './workspace-windows.test-support';

const DAY = '2026-10-29';
const day: ServerMessage = { type: 'day', id: DAY, label: '', active: 't1', threads: [], subjects: [], prefs: {} };
const NOW = 1_800_000_000;
const PHOTO = 'Reference photo of a stoma';
const OPENED = 'Opened “Stomata | Microscopy of Nature” in Stoma Reference.';
const handoff = (id: string, fields: Partial<HandoffLine> = {}): HandoffLine =>
  ({ type: 'handoff', id, lineId: `handoff:${id}`, state: 'working', label: PHOTO, request: 'search online and open a reference image of a stoma',
    startedAt: NOW - 7, thread: 't1', day: DAY, ...fields });
const said = (role: 'user' | 'assistant', text: string): ServerMessage => ({ type: 'chat', role, text, voice: true, thread: 't1', day: DAY });
const step = (tool: string, ok: boolean | null, summary: string, actionId = 'a1'): ToolRun => ({ tool, ok, summary, ms: 900, requestId: 'RUN/3', actionId });

describe('what a row says', () => {
  test('working: Hooke is working with its clock; past the voice\'s wait, still working', () => {
    expect(handoffView(handoff('h1'), NOW)).toMatchObject({ state: 'working', cell: 'working', title: PHOTO, status: 'Working',
      detail: '0:07', counting: true });
    expect(handoffView(handoff('h1', { long: true, startedAt: NOW - 18 }), NOW)).toMatchObject({ status: 'Still working', detail: '0:18' });
  });

  test('done from the brain\'s answer (its markdown dropped); did not work with its reason; not known when none was recorded', () => {
    expect(handoffView(handoff('h1', { state: 'done', answer: `**${OPENED}**`, endedAt: NOW - 1 }), NOW))
      .toMatchObject({ state: 'done', cell: 'ready', status: 'Done', detail: OPENED, counting: false });
    // No answer: its last step's words, as Activity says them.
    expect(handoffView(handoff('h1', { state: 'done', steps: [step('present', true, 'the studio shows the requested window change')] }), NOW).detail)
      .toBe('Window change displayed');
    expect(handoffView(handoff('h2', { state: 'failed', reason: 'no measurements yet' }), NOW))
      .toMatchObject({ cell: 'attention', status: 'Didn’t work', detail: 'No measurements yet' });
    expect(handoffView(handoff('h3', { state: 'unknown', reason: 'No result was recorded before Hooke restarted' }), NOW))
      .toMatchObject({ cell: 'stopped', status: 'Not known', detail: 'No result was recorded before Hooke restarted' });
    expect(handoffView(handoff('h4', { label: '', request: 'open a `new` tab' }), NOW).title).toBe('open a new tab');
    // Started a researcher: its part is done, the researcher's is not. Sent but never seen on screen: not a failure.
    expect(handoffView(handoff('h5', { state: 'started', answer: 'Researcher 2 is making the graphic.' }), NOW))
      .toMatchObject({ cell: 'partial', status: 'Started', detail: 'Researcher 2 is making the graphic.' });
    expect(handoffView(handoff('h6', { state: 'uncertain', label: 'A new Tab' }), NOW))
      .toMatchObject({ cell: 'idle', status: 'Couldn’t confirm', detail: 'a new Tab, it may not have happened' });
    expect(handoffView(handoff('h7', { state: 'uncertain', label: 'Researcher 2 note' }), NOW).detail).toBe('Researcher 2 note, it may not have happened');
  });

  test('answered with a step still running: working until that step\'s own receipt; a replay that never saw it is not known', () => {
    const settling = handoff('h1', { settling: true, answer: 'Loading it now.', steps: [step('reference_photo', null, 'loading')] });
    expect(handoffView(settling, NOW)).toMatchObject({ state: 'working', status: 'Still working', detail: 'Reference photo · 0:07' });
    expect(handoffState(settling, true)).toBe('unknown');
    expect(handoffState({ ...settling, steps: [step('reference_photo', true, 'shown')] })).toBe('done');
    expect(handoffState({ ...settling, steps: [step('reference_photo', false, 'not found')] })).toBe('failed');
  });

  test('its later step, as it stands; a step done after other work says which', () => {
    const then = (state: NonNullable<HandoffLine['then']>['state'], extra = {}) => handoffView(handoff('h1', { state: 'done', answer: 'Researcher 2 is making it.',
      then: { words: 'put the picture in the Stoma Reference tab', state, after: 'Researcher 2', rid: 'r2', ...extra } }), NOW);
    expect([then('waiting').noteLead, then('waiting').note]).toEqual(['Then, after Researcher 2:', 'put the picture in the Stoma Reference tab']);
    expect(then('running').noteLead).toBe('Doing now:');
    expect([then('done').noteLead, then('done').noteState]).toEqual(['Then, done:', 'done']);
    expect(then('dropped', { reason: 'Researcher 2 did not finish (failed), so this was not done' }))
      .toMatchObject({ noteLead: 'Not done:', note: 'put the picture in the Stoma Reference tab (Researcher 2 did not finish (failed), so this was not done)', noteState: 'failed' });
    expect(handoffView(handoff('h5', { after: 'Researcher 2' }), NOW).noteLead).toBe('After Researcher 2');
    expect(plainWords('# Done\n- [the photo](https://x) is **open**')).toBe('Done the photo is open');
  });
});

describe('in the conversation', () => {
  test('a row under the voice line that started it; later states replace it in place; the voice\'s next words go below it', () => {
    const m = new ChatModel(() => false);
    m.apply(day);
    m.apply(said('assistant', 'I’ll ask Hooke to search online and open a reference image.'));
    m.saidSpeech(''); // the voice is talking
    m.apply(handoff('h1'));
    m.saidSpeech('Anything else?');
    expect(m.lines.map((l) => l.role)).toEqual(['assistant', 'assistant', 'handoff', 'assistant']);
    const changes = m.apply(handoff('h1', { state: 'done', answer: OPENED, turnId: 4 }));
    expect(changes).toEqual([{ kind: 'handoff', line: m.lines[2] }]);
    expect(m.lines.filter((l) => l.handoff)).toHaveLength(1);
    expect(m.lines[2].handoff?.state).toBe('done');
    expect(m.lines[2].turnId).toBe(4);
    expect(m.apply(handoff('h9', { thread: 't2' }))).toEqual([]); // another conversation's
  });

  test('a step the brain left running settles from its own late receipt, by occurrence', () => {
    const m = new ChatModel(() => false);
    m.apply(day);
    m.apply(handoff('h1', { settling: true, steps: [step('reference_photo', null, 'loading')] }));
    const changes = m.apply({ type: 'chat-action', turnId: 4, standalone: true, ...step('reference_photo', true, 'shown') });
    expect(changes.some((c) => c.kind === 'handoff')).toBe(true);
    expect(handoffState(m.lines.find((l) => l.handoff)!.handoff!)).toBe('done');
  });

  test('a replay draws each hand-off as its last state', () => {
    const m = new ChatModel(() => false);
    m.apply({ type: 'thread', day: DAY, thread: 't1', title: '', summary: '', lines: [said('user', 'Find a photo of a stoma'),
      handoff('h1', { state: 'done', answer: OPENED }), handoff('h2', { state: 'unknown', reason: 'No result was recorded before Hooke restarted' })] });
    expect(m.lines.map((l) => l.handoff?.state ?? l.role)).toEqual(['user', 'done', 'unknown']);
    expect(m.lines[1].replayed).toBe(true);
  });
});

describe('drawn', () => {
  let dom: ReturnType<typeof syntheticDOM>;
  beforeEach(() => { dom = syntheticDOM(); vi.stubGlobal('document', dom.document); vi.useFakeTimers(); vi.setSystemTime(NOW * 1000); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  function chat() {
    const root = new TestElement('ol'), flights: FlightEntry[][] = [];
    const log = new ChatLog(root as unknown as HTMLOListElement, new TestElement('div') as unknown as HTMLElement, {
      research: () => undefined, show: vi.fn(), describe: vi.fn(), hint: vi.fn(), voiceLive: () => false, unread: vi.fn(),
      flight: (entries) => flights.push(entries), openActivity: vi.fn() });
    log.apply(day);
    return { log, root, flights, rows: () => root.querySelectorAll('.chat-handoff') };
  }

  test('a hand-off that started a researcher gives its place to the researcher\'s card: one request, one thing', () => {
    const { log, rows } = chat();
    log.apply(handoff('h7', { label: 'Compare models' }));
    const row = rows()[0];
    expect(row.hidden).toBeFalsy();
    log.apply(handoff('h7', { label: 'Compare models', state: 'started', answer: 'Researcher 1 is comparing them.' }));
    expect(row.hidden).toBe(true);
    log.apply(handoff('h8', { state: 'started', then: { words: 'open its report in a new Tab', state: 'waiting' } }));
    expect(rows()[1].hidden).toBeFalsy(); // its later step's note stays in view
  });

  test('a row works, ticks, then settles into its outcome in place; a tap opens its steps', () => {
    const { log, rows } = chat();
    log.apply(said('assistant', 'I’ll ask Hooke to search online and open a reference image.'));
    log.apply(handoff('h1'));
    const row = rows()[0];
    expect(row.dataset.state).toBe('working');
    expect(row.querySelector('.chat-handoff-title')!.textContent).toBe(PHOTO);
    expect(row.querySelector('.chat-handoff-sub')!.textContent).toBe('Working0:07');
    expect(row.dataset.arrived).toBe(''); // it rises in once
    vi.advanceTimersByTime(3000);
    expect(row.querySelector('.chat-handoff-detail')!.textContent).toBe('0:10');
    log.apply(handoff('h1', { long: true }));
    expect(row.querySelector('.chat-handoff-status')!.textContent).toBe('Still working');
    log.apply(handoff('h1', { state: 'done', long: true, answer: OPENED, turnId: 4,
      steps: [step('reference_photo', true, 'found “Stomata | Microscopy of Nature”'), step('present', true, 'the studio shows the requested window change', 'a2')] }));
    expect(rows()).toEqual([row]); // the same row
    expect(row.dataset.state).toBe('done');
    expect(row.querySelector('.chat-handoff-status')!.textContent).toBe('Done');
    expect(row.querySelector('.chat-handoff-detail')!.textContent).toBe(OPENED);
    const head = row.querySelector('.chat-handoff-head')!, body = row.querySelector('.chat-handoff-body')!;
    expect(body.hidden).toBe(true);
    fire(head, 'click');
    expect(body.hidden).toBe(false);
    expect(head.getAttribute('aria-expanded')).toBe('true');
    expect(body.querySelectorAll('li').map((li) => li.getAttribute('aria-label'))).toEqual([
      'Reference photo: Found “Stomata | Microscopy of Nature”', 'Window: Window change displayed']);
    expect(body.querySelector('.chat-handoff-activity')!.hidden).toBe(false);
  });

  test('two in flight at once: two rows and both on the strip; each leaves it as it lands; none, nothing', () => {
    const { log, rows, flights } = chat();
    log.apply(handoff('h1'));
    log.apply(said('user', 'Can you open a new tab for me?'));
    log.apply(handoff('h2', { label: 'A new Tab', startedAt: NOW - 1 }));
    expect(rows()).toHaveLength(2);
    expect(flights.at(-1)!.map((e) => e.name)).toEqual([PHOTO, 'A new Tab']);
    log.apply(handoff('h2', { label: 'A new Tab', startedAt: NOW - 1, state: 'failed', reason: 'The studio did not show the new Tab' }));
    expect(flights.at(-1)!.map((e) => e.name)).toEqual([PHOTO]);
    expect(rows()[1].querySelector('.chat-handoff-status')!.textContent).toBe('Didn’t work');
    log.apply(handoff('h1', { state: 'done', answer: OPENED }));
    expect(flights.at(-1)).toEqual([]);
  });

  test('a later step waiting on a researcher is on the strip after it, not counted as working', () => {
    const { log, flights } = chat();
    log.apply(handoff('h3', { label: 'A stoma graphic', state: 'done', answer: 'Researcher 2 is making it.',
      then: { words: 'put the picture in the Stoma Reference tab', state: 'waiting', after: 'Researcher 2', rid: 'r2' } }));
    const [then] = flights.at(-1)!;
    expect(then).toMatchObject({ key: 'then:h3', name: 'then: put the picture in the Stoma Reference tab', waiting: true, follows: 'researcher:r2' });
    const researcher: FlightEntry = { key: 'researcher:r2', name: 'Researcher 2', since: NOW - 3, doing: 'Researcher 2 · Drawing', go: vi.fn() };
    const other: FlightEntry = { key: 'handoff:h9', name: 'A new Tab', since: NOW - 5, doing: 'Hooke is working', go: vi.fn() };
    expect(flightOrder([then, researcher, other]).map((e) => e.key)).toEqual(['handoff:h9', 'researcher:r2', 'then:h3']);
  });
});

describe('the strip over the composer', () => {
  let dom: ReturnType<typeof syntheticDOM>;
  beforeEach(() => { dom = syntheticDOM(); vi.stubGlobal('document', dom.document); });
  afterEach(() => { vi.unstubAllGlobals(); });

  test('shown only while something runs: "2 working · …", each entry jumping to its work; gone when nothing runs', () => {
    const strip = new FlightStrip();
    const root = strip.root as unknown as TestElement;
    expect(root.hidden).toBe(true);
    const photo = vi.fn(), tab = vi.fn(), r5 = vi.fn();
    strip.set('handoffs', [{ key: 'handoff:h1', name: PHOTO, since: 10, doing: 'Hooke is working', go: photo },
      { key: 'handoff:h2', name: 'A new Tab', since: 12, doing: 'Hooke is working', go: tab }]);
    expect(root.hidden).toBe(false);
    expect(root.dataset.arrived).toBe('');
    expect(root.textContent).toBe(`2 working${PHOTO}A new Tab`);
    strip.set('researchers', [{ key: 'researcher:r5', name: 'Researcher 5', since: 11, doing: 'Researcher 5 · Choosing a model', go: r5 }]);
    const items = root.querySelectorAll('.chat-flight-item');
    expect(items.map((b) => b.textContent)).toEqual([PHOTO, 'Researcher 5', 'A new Tab']);
    expect(items[1].getAttribute('aria-label')).toBe('Researcher 5: Researcher 5 · Choosing a model. Show it');
    fire(items[2], 'click');
    expect(tab).toHaveBeenCalled();
    expect(root.querySelector('.chat-flight-count')!.textContent).toBe('3 working');
    strip.set('handoffs', [{ key: 'then:h3', name: 'then: put it in Stoma Reference', since: 9, doing: 'Waiting for Researcher 5', waiting: true, follows: 'researcher:r5', go: vi.fn() }]);
    expect(root.querySelector('.chat-flight-count')!.textContent).toBe('Working');
    expect(root.querySelectorAll('.chat-flight-item').map((b) => b.textContent)).toEqual(['Researcher 5', 'then: put it in Stoma Reference']);
    strip.set('handoffs', []);
    expect(root.hidden).toBe(true);
    expect(strip.entries.map((e) => e.key)).toEqual(['researcher:r5']);
    strip.set('researchers', []);
    expect(root.hidden).toBe(true);
  });

  test('one piece of work is said by its row and the voice, never a strip of one (Will\'s try-out, 30 Oct)', () => {
    const strip = new FlightStrip();
    const root = strip.root as unknown as TestElement;
    strip.set('handoffs', [{ key: 'handoff:h1', name: 'Magnify organism', since: 10, doing: 'Hooke is working', go: vi.fn() }]);
    expect(root.hidden).toBe(true);
    expect(strip.working).toBe(1);
  });
});

describe('one in-progress list (W3: the voice\'s state counts what the strips list)', () => {
  let dom: ReturnType<typeof syntheticDOM>;
  beforeEach(() => { dom = syntheticDOM(); vi.stubGlobal('document', dom.document); });
  afterEach(() => { vi.unstubAllGlobals(); });
  const entry = (key: string, name: string, since: number, more: Partial<FlightEntry> = {}): FlightEntry =>
    ({ key, name, since, doing: 'Hooke is working', go: vi.fn(), ...more });

  test('a key its row or researcher shows is never doubled by the server\'s own list, which adds only what they do not', () => {
    const sources = new Map<string, FlightEntry[]>([
      [VOICE_SOURCE, [entry('handoff:h1', 'from the server', 10), entry('job:j1', 'Compare three models', 12)]],
      ['handoffs', [entry('handoff:h1', PHOTO, 10)]],
      ['researchers', [entry('researcher:r2', 'Researcher 2', 11)]],
    ]);
    expect(flightEntries(sources).map((e) => [e.key, e.name])).toEqual([
      ['handoff:h1', PHOTO], ['researcher:r2', 'Researcher 2'], ['job:j1', 'Compare three models']]);
  });

  test('the server\'s list as entries: each key, label and since; none once Talk is off', () => {
    const go = vi.fn();
    const message = { type: 'voice-state' as const, state: 'working' as const, n: 2, seq: 3, at: 50,
      working: [{ key: 'handoff:h1', label: PHOTO, since: 10 }, { key: 'job:j1', label: '', since: Number.NaN }] };
    const entries = voiceEntries(message, go);
    expect(entries.map((e) => [e.key, e.name, e.since])).toEqual([['handoff:h1', PHOTO, 10], ['job:j1', 'Hooke’s work', 50]]);
    entries[1].go();
    expect(go).toHaveBeenCalledWith('job:j1');
    expect(voiceEntries({ ...message, state: 'off' }, go)).toEqual([]);
    expect(voiceEntries(null, go)).toEqual([]);
  });

  test('the Working strip gets a pill for each hand-off and job in flight (researchers keep their own), counting its time', () => {
    const open = vi.fn(), dismiss = vi.fn();
    const pills = flightPills([entry('handoff:h1', PHOTO, 10), entry('researcher:r2', 'Researcher 2', 11), entry('job:j1', 'Compare', 12),
      entry('then:h1', 'then: put it in a Tab', 10, { waiting: true })], new Set(['job:j1']), open, dismiss);
    expect(pills.map((p) => [p.id, p.name, p.cell, p.counting, p.since])).toEqual([['handoff:h1', PHOTO, 'working', true, 10_000]]);
    pills[0].open();
    expect(open).toHaveBeenCalledWith(expect.objectContaining({ key: 'handoff:h1' }));
    pills[0].dismiss();
    expect(dismiss).toHaveBeenCalledWith('handoff:h1');
  });

  test('the strip counts what it lists as working and tells each change once', () => {
    const strip = new FlightStrip();
    const changes: string[][] = [];
    strip.onChange = (entries) => changes.push(entries.map((e) => e.key));
    strip.set('handoffs', [entry('handoff:h1', PHOTO, 10), entry('then:h1', 'then: x', 10, { waiting: true })]);
    strip.set('researchers', [entry('researcher:r2', 'Researcher 2', 11)]);
    strip.set('researchers', [entry('researcher:r2', 'Researcher 2', 11)]); // the same: not told again
    strip.set(VOICE_SOURCE, [entry('handoff:h1', 'from the server', 10)]); // already listed: no change
    expect(strip.working).toBe(2);
    expect(changes).toEqual([['handoff:h1', 'then:h1'], ['handoff:h1', 'then:h1', 'researcher:r2']]);
    expect((strip.root as unknown as TestElement).querySelector('.chat-flight-count')!.textContent).toBe('2 working');
  });
});
