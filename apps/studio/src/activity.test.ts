// Activity (CONTEXT.md "Activity"; the user's decision Q2, 2026-10-11): newest first, one entry per request with a line
// per step (what was done, its models, how long, whether it worked), one line for each click, studio action, setting,
// model load or researcher; every line from a recorded fact, replayed as it was.
import { afterEach, expect, test, vi } from 'vitest';
import { activityDays, activityRows, actionSummary, buildActivity, entryOf, hoverLine, noticeText, unresolvedFailures, type ActivityEntry } from './activity';
import { drawActivityCells } from './activity-render';
import { clockWords } from './sessions';
import { ChatLog } from './chatlog';
import { ChatModel } from './chatmodel';
import type { ActivityEvent, AppNotice, ServerMessage, StepModel, ToolRun } from './protocol';
import type { StepFact } from './strip-sources';

const step = (fields: Partial<ToolRun> = {}): ToolRun => ({ tool: 'present', ok: true, summary: 'Window displayed', ms: 50, requestId: 'r1', actionId: 'a1', ...fields });
test('repeated outcomes retain every occurrence for inspection', () => {
  const a = step(), b = step({ actionId: 'a2', ms: 90 });
  expect(activityRows([a, b])).toEqual([{ action: a, count: 2, actions: [a, b] }]);
});
test('distinct requests, running steps and changed failure reasons stay separate', () => {
  const actions = [step(), step({ requestId: 'r2' }), step({ ok: null }), step({ ok: null }),
    step({ ok: false, summary: 'Source unavailable' }), step({ ok: false, summary: 'Browser disconnected' })];
  expect(activityRows(actions)).toHaveLength(6);
});
test('a receipt that hands the model untrusted text stops before it on the line', () => {
  // Live 2026-10-11: "show me what it found" put `<researchers source="web" untrusted="true">` on the Activity line.
  const shown = step({ tool: 'show_me', summary: 'Showing Researcher 3\'s finding (sh1). What it found, in its own words: '
    + '<researchers source="web" untrusted="true">Stomata open when guard cells swell.</researchers>' });
  expect(actionSummary(shown)).toBe('Showing Researcher 3\'s finding (sh1)');
  expect(activityRows([shown])[0].actions[0].summary).toContain('untrusted="true"'); // retained whole
  expect(actionSummary(step({ summary: 'outlined the stoma' }))).toBe('Outlined the stoma');
});
test('source-qualified receipts stay compact while the full occurrence is retained', () => {
  const action = step({ summary: 'the requesting studio confirmed open; actual sources: [{"resource":{"id":"d5"}}]' });
  expect(actionSummary(action)).toBe('Window change displayed');
  expect(activityRows([action])[0].actions[0].summary).toContain('d5');
});

const DAY = '2026-10-11';
const T0 = 1791712000;
const GEMINI: StepModel = { role: 'found by', name: 'Gemini 3.8 Flash', said: 'Gemini 3.8 Flash (cloud)' };
const EDGETAM: StepModel = { role: 'drawn by', name: 'EdgeTAM', said: 'EdgeTAM (on this Mac)' };
const find = (fields: Partial<ToolRun> = {}): ToolRun => ({ tool: 'find', ok: true, summary: 'found 2 stomata', ms: 1200,
  requestId: 'q1', actionId: 'find-1', models: [GEMINI, EDGETAM], ...fields });
const request = (fields: Partial<ActivityEvent> = {}): ActivityEvent => ({
  type: 'activity', id: `request:${DAY}:5`, kind: 'request', text: 'find the stomata', at: T0, state: 'done', thread: 't1', day: DAY,
  ref: { turnId: 5, channel: 'chat' }, steps: [find()],
  details: { answer: { published: true, model: 'Gemini 3.8 Flash', thinkMs: 4800, totalMs: 6100, calls: 2, at: T0 + 6 },
    look: { outlined: 1, models: [GEMINI, EDGETAM] } }, ...fields,
});
const event = (fields: Partial<ActivityEvent> = {}): ActivityEvent => ({
  type: 'activity', id: 'click:o4', kind: 'click', text: 'You clicked: outlined by EdgeTAM (on this Mac) · 84 ms', at: T0 + 20,
  state: 'done', thread: 't1', day: DAY, details: { models: [EDGETAM], ms: 84, ids: { objectId: 'o4' } }, ref: { objectId: 'o4' }, ...fields,
});
const day = (thread = 't1'): ServerMessage => ({ type: 'day', id: DAY, label: '', active: thread, threads: [], subjects: [], prefs: {} });

function model(...messages: ServerMessage[]): ChatModel {
  const m = new ChatModel(() => false);
  m.apply(day());
  for (const message of messages) m.apply(message);
  return m;
}

test('a request is one entry: its words, typed or spoken, then a line for its answer, its look and each step', () => {
  const m = model({ type: 'chat', role: 'user', text: 'find the stomata', thread: 't1', at: T0 }, request());
  const [entry] = buildActivity(m.lines);
  expect(entry).toMatchObject({ kind: 'request', words: 'find the stomata', channel: 'typed', turnId: 5, at: T0 });
  expect(entry.heading).toBe('“find the stomata”');
  expect(entry.lines.map(line => [line.key, line.text, line.state])).toEqual([
    ['answer', 'Answered', 'done'], ['look', 'Outlined 1 thing it pointed at', 'done'], ['q1:find-1', 'Found 2 stomata', 'done']]);
  const found = entry.lines[2];
  expect(found.models.map(mm => mm.name)).toEqual(['Gemini 3.8 Flash', 'EdgeTAM']);
  // The line's words are the whole receipt, so the details begin with its models.
  expect(found.details).toEqual(['Found by Gemini 3.8 Flash (cloud)', 'Drawn by EdgeTAM (on this Mac)', 'Took 1.2 s']);
  expect(found.technical).toEqual(['Tool: find', 'Request: q1', 'Step: find-1']); // ids only behind "Technical"
  expect(found.at).toBe(T0 + 6);
  expect(buildActivity(model(request({ ref: { turnId: 5, channel: 'voice' } })).lines)[0].channel).toBe('spoken');
});

test('a running request says so, then its record is replaced in place by its finished one', () => {
  const loading = model(request({ steps: [find({ ok: null, models: undefined, progress: 'loading micro-sam (light microscopy) on this Mac, usually about 2 min' })] }));
  expect(buildActivity(loading.lines)[0].lines.at(-1)).toMatchObject({
    text: 'Loading micro-sam (light microscopy) on this Mac, usually about 2 min…', state: 'running' }); // what it waits on
  const m = model(request({ state: 'running', steps: undefined, details: undefined }));
  expect(buildActivity(m.lines)[0].lines.map(line => [line.text, line.state])).toEqual([['Working on it…', 'running']]);
  const changes = m.apply(request());
  expect(changes).toEqual([expect.objectContaining({ kind: 'event' })]);
  expect(m.lines).toHaveLength(1);
  expect(buildActivity(m.lines)[0].lines.map(line => line.text)).toContain('Found 2 stomata');
});

test('a late step updates its own occurrence, once, and a retried failure says it worked', () => {
  const pending = find({ ok: null, summary: 'finding', models: undefined, ms: 0 });
  const failed: ToolRun = { tool: 'use', ok: false, summary: "there is no 'SAM 3.1 (local)'", ms: 2, requestId: 'q1', actionId: 'use-1', next: 'name another one' };
  const retried: ToolRun = { tool: 'use', ok: true, summary: 'for this request, outlining uses micro-sam', ms: 1, requestId: 'q1', actionId: 'use-2' };
  const m = model(request({ steps: [failed, retried, pending] }),
    { type: 'chat', role: 'assistant', text: 'Finding them.', turnId: 5, thread: 't1', at: T0 + 6, actions: [failed, retried, pending], model: 'Gemini 3.8 Flash', thinkMs: 4800 },
    { type: 'chat-action', turnId: 5, thread: 't1', at: T0 + 30, ...find() });
  const [entry] = buildActivity(m.lines);
  const steps = entry.lines.filter(line => line.tool);
  expect(steps.map(line => [line.tool, line.state])).toEqual([['use', 'failed'], ['use', 'done'], ['find', 'done']]);
  expect(steps[0].details).toContain('Tried again: it worked');
  expect(steps[0].details).toContain('What to do: name another one');
  expect(steps[2].at).toBe(T0 + 30); // the late receipt's own time
  expect(entry.failed).toEqual([]); // fixed when tried again: no "!"
});

test('entries are newest first: requests, clicks, settings and app notices interleaved by when they happened', () => {
  const notice: AppNotice = { type: 'app-notice', day: DAY, thread: 't1', at: T0 + 10, producer: { kind: 'research', id: 'r9' },
    event: 'completed', summary: 'Researcher 2: done', tone: 'info', facts: { name: 'Researcher 2', status: 'done', result: { one_liner: 'Stomata open by turgor.' } },
    origin: { day: DAY } };
  const setting = event({ id: 'setting:1', kind: 'setting', text: 'You chose SAM 2.1 tiny for outlining (Models menu)', at: T0 + 30, details: undefined, ref: undefined });
  const entries = buildActivity(model(request(), notice, event(), setting).lines);
  expect(entries.map(entry => entry.kind)).toEqual(['setting', 'click', 'notice', 'request']);
  expect(entries[2].lines[0].text).toBe('Researcher 2 finished: Stomata open by turgor.');
  expect(entries[1].lines[0]).toMatchObject({ text: 'You clicked: outlined by EdgeTAM (on this Mac) · 84 ms', models: [EDGETAM] });
  expect(entries[1].lines[0].details).toEqual(['Drawn by EdgeTAM (on this Mac)', 'Took 84 ms']);
});

test('a researcher\'s notices are said in plain words, never ids or characters', () => {
  const base: AppNotice = { type: 'app-notice', day: DAY, thread: 't1', at: T0, producer: { kind: 'research', id: 'r9' }, event: 'request',
    summary: 'Researcher 2: evidence question q1', tone: 'info', facts: { name: 'Researcher 2', request: { id: 'q1', what: '15 seconds at this field' } },
    origin: { day: DAY } };
  expect(noticeText(base)).toBe('Researcher 2 asks you: 15 seconds at this field');
  expect(noticeText({ ...base, event: 'completed', facts: { name: 'Researcher 2', status: 'timed_out' } })).toBe('Researcher 2 ran out of time');
  expect(noticeText({ ...base, event: 'plan', facts: { name: 'Researcher 2', plan: { searches: ['a', 'b'] } } })).toBe('Researcher 2 made a plan: 2 searches');
  expect(noticeText({ ...base, facts: { name: 'Researcher 2', request: { id: 'q2', keepGoing: true, why: 'its time is nearly up' } } }))
    .toBe('Researcher 2 asks whether to keep going: its time is nearly up');
  expect(noticeText({ ...base, event: 'presentation-finished', facts: { name: 'Researcher 2', presentation: { state: 'ready', contentKind: 'lesson', version: 2 } } }))
    .toBe('Researcher 2 restyled its lesson (version 2)');
  // Work the studio could not show beside the view is kept in Researchers (the word the server says too).
  expect(noticeText({ ...base, event: 'presentation', facts: { name: 'Researcher 2', presentation: { contentVerified: false } } }))
    .toBe("Researcher 2's work is in Researchers");
  expect(noticeText({ ...base, event: 'presentation', facts: { name: 'Researcher 2', presentation: { contentVerified: true } } }))
    .toBe("Researcher 2's work opened beside the view");
});

test('the hover over an answer says its request\'s models on one line', () => {
  const m = model(request(), { type: 'chat', role: 'assistant', text: 'Two stomata.', turnId: 5, thread: 't1', model: 'Gemini 3.8 Flash', thinkMs: 4800 });
  const entries = buildActivity(m.lines);
  const answer = m.lines.find(line => line.role === 'assistant')!;
  expect(entryOf(entries, answer)?.key).toBe('turn:5');
  expect(hoverLine(entryOf(entries, answer), answer)).toBe('Gemini 3.8 Flash · drawn by EdgeTAM · 4.8 s');
  expect(hoverLine(undefined, { model: 'Gemini 3.8 Flash', thinkMs: 900 })).toBe('Gemini 3.8 Flash · 0.9 s');
});

test('a blind comparison\'s look names no model', () => {
  const [entry] = buildActivity(model(request({ steps: [], details: { answer: { published: true, model: 'Gemini 3.8 Flash' }, look: { outlined: 2, models: [] } } })).lines);
  const look = entry.lines.find(line => line.key === 'look')!;
  expect(look.models).toEqual([]);
  expect(look.details[0]).toContain('not said');
});

test('a replay draws the same entries; another conversation\'s and another day\'s lines are not drawn', () => {
  const lines: ServerMessage[] = [request(), event()];
  const live = buildActivity(model(...lines).lines);
  const replayed = new ChatModel(() => false);
  replayed.apply(day());
  replayed.apply({ type: 'thread', day: DAY, thread: 't1', title: '', summary: '', lines });
  expect(buildActivity(replayed.lines)).toEqual(live);
  expect(model(event({ thread: 't2' }), event({ id: 'click:o5', day: '2026-10-10' })).lines).toEqual([]);
});

// --- drawn (a tiny DOM stand-in, as app-notices.test.ts) ----------------------------------------
class Element {
  readonly children: Element[] = [];
  readonly dataset: Record<string, string> = {};
  readonly attributes: Record<string, string> = {};
  readonly listeners = new Map<string, ((event?: unknown) => void)[]>();
  parentElement: Element | null = null;
  className = ''; hidden = false; title = ''; type = ''; id = ''; dateTime = ''; scrollTop = 0; scrollHeight = 500; clientHeight = 0;
  private text = '';
  readonly tag: string;
  constructor(tag: string) { this.tag = tag; }
  get textContent(): string { return this.text + this.children.map(node => node.textContent).join(''); }
  set textContent(text: string) { this.replaceChildren(); this.text = text; }
  append(...values: (Element | string)[]): void {
    for (const value of values) {
      const node = typeof value === 'string' ? Object.assign(new Element('#text'), { textContent: value }) : value;
      node.remove(); node.parentElement = this; this.children.push(node);
    }
  }
  prepend(node: Element): void { this.insertBefore(node, this.children[0] ?? null); }
  insertBefore(node: Element, before: Element | null): void {
    if (!before) { this.append(node); return; }
    node.remove(); node.parentElement = this; this.children.splice(this.children.indexOf(before), 0, node);
  }
  replaceWith(node: Element): void { this.parentElement?.insertBefore(node, this); this.remove(); }
  before(...nodes: Element[]): void { for (const node of nodes) this.parentElement?.insertBefore(node, this); }
  remove(): void {
    if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceChildren(...nodes: Element[]): void { for (const node of [...this.children]) node.remove(); this.text = ''; this.append(...nodes); }
  setAttribute(name: string, value: string): void { this.attributes[name] = value; }
  addEventListener(type: string, listener: (event?: unknown) => void): void { this.listeners.set(type, [...this.listeners.get(type) ?? [], listener]); }
  fire(type: string): void { for (const listener of this.listeners.get(type) ?? []) listener(); }
  querySelector(selector: string): Element | undefined { return all(this).find(node => node.className.split(' ').includes(selector.slice(1))); }
}
const all = (root: Element): Element[] => root.children.flatMap(child => [child, ...all(child)]);

function drawn() {
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
  const activity = new Element('ol'), conversation = new Element('ol');
  const counts: number[] = [];
  const openActivity = vi.fn();
  const log = new ChatLog(conversation as unknown as HTMLOListElement, new Element('div') as unknown as HTMLElement, {
    activity: activity as unknown as HTMLOListElement, activityCount: count => counts.push(count), openActivity,
    research: () => undefined, show: vi.fn(), describe: vi.fn(), hint: vi.fn(), voiceLive: () => false, unread: vi.fn() });
  log.apply(day());
  return { log, activity, conversation, counts, openActivity };
}

afterEach(() => vi.unstubAllGlobals());

test('drawn: each line is a dot, what happened, its models briefly, how long and its time; it opens to details', () => {
  const { log, activity, counts } = drawn();
  log.apply(request());
  log.apply(event());
  expect(activity.children.map(node => node.dataset.kind)).toEqual(['click', 'request']);
  expect(counts.at(-1)).toBe(2); // the tab's count is the entries shown
  const entry = activity.children[1];
  expect(entry.children[0].textContent).toContain('“find the stomata”');
  expect(entry.children[0].textContent).toContain('typed');
  const line = all(entry).filter(node => node.tag === 'li' && node.dataset.tool === 'find')[0];
  expect(line.dataset.state).toBe('done');
  expect(all(line).find(node => node.className === 'activity-text')?.textContent).toBe('Found 2 stomata');
  expect(all(line).find(node => node.className === 'activity-meta')?.textContent).toBe('Gemini 3.8 Flash → EdgeTAM · 1.2 s');
  // A click's line already says what outlined it and how long: nothing said twice beside it.
  expect(all(activity.children[0]).find(node => node.className === 'activity-meta')).toBeUndefined();
  const details = all(line).find(node => node.className === 'activity-details')!;
  expect(details.hidden).toBe(true);
  all(line).find(node => node.className === 'activity-open')!.fire('click');
  expect(details.hidden).toBe(false);
  expect(details.textContent).toContain('Drawn by EdgeTAM (on this Mac)');
  expect(all(details).find(node => node.tag === 'summary')?.textContent).toBe('Technical');
  log.apply({ ...event(), text: 'You clicked: outlined by EdgeTAM (on this Mac) · 84 ms, named “guard cell” by Gemini 3.8 Flash (cloud)' });
  expect(activity.children).toHaveLength(2); // replaced in place, not a second line
  expect(activity.children[0].textContent).toContain('named “guard cell”');
  expect(all(activity.children[1]).find(node => node.className === 'activity-details' && !node.hidden)).toBeDefined(); // still open
});

test('drawn: a step that finished after its answer, with no model answer since, puts a ✓ on the answer', () => {
  // Live 2026-10-11: "do it again with micro-sam" answered "loading … still in progress"; micro-sam outlined it 45 s later
  // and the late answer's call timed out, so the answer said "still in progress" with nothing to show it was done.
  const { log, conversation } = drawn();
  const use = step({ tool: 'use', summary: 'for this request, outlining uses micro-sam', requestId: 'r9', actionId: 'use-1' });
  log.apply({ type: 'chat', role: 'assistant', text: 'Loading micro-sam: the highlight is still in progress.', turnId: 7, thread: 't1', actions: [use] });
  const answer = conversation.children.find(node => node.dataset.role === 'assistant')!;
  const mark = () => all(answer).find(node => node.className === 'activity-info')!;
  expect(mark().textContent).toBe('ⓘ');
  const drawnLate = step({ tool: 'highlight', summary: 'the stoma drawn again with micro-sam', requestId: 'r9', actionId: 'hl-1', ms: 48436 });
  log.apply({ type: 'chat-action', turnId: 7, thread: 't1', standalone: true, ...drawnLate });
  expect(mark().textContent).toBe('✓');
  expect(mark().title).toContain('Finished after this answer (Highlight)');
  log.apply({ type: 'chat-action', turnId: 7, thread: 't1', standalone: true, ...drawnLate, answer: 'Done: micro-sam drew it again.' });
  expect(mark().textContent).toBe('ⓘ'); // the model's own words now say it
});

test('drawn: the answer\'s "!" opens Activity at its request with what did not work opened, and its hover names the models', () => {
  const { log, activity, conversation, openActivity } = drawn();
  const failed = find({ ok: false, summary: 'nothing could be outlined there', models: [GEMINI], next: 'click the thing itself' });
  log.apply(request({ steps: [failed] }));
  log.apply({ type: 'chat', role: 'assistant', text: 'I could not outline them.', turnId: 5, thread: 't1', model: 'Gemini 3.8 Flash', thinkMs: 4800, actions: [failed] });
  const answer = conversation.children.find(node => node.dataset.role === 'assistant')!;
  const mark = all(answer).find(node => node.className === 'activity-info')!;
  expect(mark.textContent).toBe('!');
  expect(answer.title).toBe('Gemini 3.8 Flash · drawn by EdgeTAM · 4.8 s');
  expect(mark.title).toContain('Something in this request didn\'t work (Find)');
  mark.fire('click');
  expect(openActivity).toHaveBeenCalled();
  const entry = activity.children.find(node => node.dataset.turn === '5')!;
  expect(entry.className).toContain('activity-focus');
  const line = all(entry).find(node => node.tag === 'li' && node.dataset.tool === 'find')!;
  expect(all(line).find(node => node.className === 'activity-details')?.hidden).toBe(false);
  expect(line.textContent).toContain('What to do: click the thing itself');
});

test("work that finished in another session says which, by its name: from 'Blood smear'", () => {
  const notice: AppNotice = { type: 'app-notice', day: '2026-10-12.2', thread: 't1', at: T0, producer: { kind: 'research', id: 'r2' },
    event: 'completed', summary: 'Researcher 2: done', tone: 'info', facts: { name: 'Researcher 2', status: 'done', result: { one_liner: 'Platelets clump.' } },
    origin: { day: '2026-10-12' }, fromSession: { id: '2026-10-12', name: 'Blood smear' } };
  expect(noticeText(notice)).toBe("Researcher 2 finished: Platelets clump. · from 'Blood smear'");
  expect(noticeText({ ...notice, fromSession: undefined })).toBe('Researcher 2 finished: Platelets clump. · from 2026-10-12'); // an older server
  const line = event({ id: 'research:r2', kind: 'research', text: 'Researcher 2 finished', day: '2026-10-12.2', fromSession: { id: '2026-10-12', name: 'Blood smear' } });
  const m = new ChatModel(() => false);
  m.apply({ type: 'day', id: '2026-10-12.2', label: '', active: 't1', threads: [], subjects: [], prefs: {} });
  m.apply(line);
  expect(buildActivity(m.lines)[0].lines[0].text).toBe("Researcher 2 finished · from 'Blood smear'");
  // Drawn on the conversation's notice line too.
  const { log, conversation } = drawn();
  log.apply({ type: 'day', id: '2026-10-12.2', label: '', active: 't1', threads: [], subjects: [], prefs: {} });
  log.apply(notice);
  expect(all(conversation).find((node) => node.className === 'app-notice-origin')?.textContent).toBe("From 'Blood smear'");
});

test('Activity’s Problems only keeps an entry where a step did not work, or a line failed, and nothing else', async () => {
    const { hasProblem } = await import('./activity');
    const line = (state: 'done' | 'running' | 'failed') => ({ key: state, text: state, state, models: [], at: null, details: [], technical: [] });
    expect(hasProblem({ failed: [], lines: [line('done')] })).toBe(false);
    expect(hasProblem({ failed: [], lines: [line('running')] })).toBe(false);
    expect(hasProblem({ failed: ['outline'], lines: [line('done')] })).toBe(true);
    expect(hasProblem({ failed: [], lines: [line('done'), line('failed')] })).toBe(true);
});

test('drawn: Problems only is made once, follows the list when it moves, and gives way to an answer’s ⓘ', () => {
  const { log, activity, conversation } = drawn();
  const panel = new Element('section'); panel.append(activity);
  log.apply(request());
  log.apply({ type: 'chat', role: 'assistant', text: 'Two stomata.', turnId: 5, thread: 't1', model: 'Gemini 3.8 Flash', thinkMs: 4800 });
  const toggles = () => all(panel).filter(node => node.className === 'activity-problems');
  expect(toggles()).toHaveLength(1);
  // The workspace takes Activity out of the page and back (Data and Activity share a panel); events arrive meanwhile.
  activity.remove(); log.apply(event()); log.apply(event({ id: 'click:o6' }));
  const elsewhere = new Element('section'); elsewhere.append(activity); log.apply(event({ id: 'click:o7' }));
  expect(all(elsewhere).filter(node => node.className === 'activity-problems')).toHaveLength(1);
  const toggle = all(elsewhere).find(node => node.className === 'activity-problems')!;
  toggle.fire('click');
  expect(toggle.attributes['aria-pressed']).toBe('true');
  const request5 = activity.children.find(node => node.dataset.kind === 'request')!;
  expect(request5.hidden).toBe(true); // nothing failed in it
  const answer = conversation.children.find(node => node.dataset.role === 'assistant')!;
  all(answer).find(node => node.className === 'activity-info')!.fire('click');
  expect(toggle.attributes['aria-pressed']).toBe('false');
  expect(activity.children.find(node => node.dataset.kind === 'request')!.hidden).toBe(false);
});

test('Activity’s day separators: the first shown entry of each day, newest first; untimed entries stay in their group', () => {
  const noon = (y: number, m: number, d: number) => new Date(y, m - 1, d, 12).getTime() / 1000;
  const now = noon(2026, 10, 25);
  const entries = [{ key: 'a', at: now - 60 }, { key: 'b', at: now - 120 }, { key: 'c', at: null }, { key: 'd', at: noon(2026, 10, 24) },
    { key: 'e', at: noon(2026, 10, 22) }, { key: 'f', at: noon(2026, 10, 22) - 60 }];
  expect([...activityDays(entries, () => true, now)]).toEqual([['a', 'Today'], ['d', 'Yesterday'], ['e', 'Oct 22']]);
  // A hidden entry (Problems only) never carries its day's words: the next shown one does.
  expect(activityDays(entries, key => key !== 'e', now).get('f')).toBe('Oct 22');
});

test('drawn in the workspace: each entry has its lit cell, its quiet line says when to the minute, and the first of a day says the day', () => {
  const made: string[] = [];
  drawActivityCells((state, label) => { made.push(`${state}:${label}`); return new Element('svg') as unknown as SVGSVGElement; });
  try {
    const { log, activity } = drawn();
    log.apply(request());
    log.apply(event());
    log.apply(event({ id: 'click:o5', state: 'failed', at: T0 + 30, details: { ids: { objectId: 'o5' } }, text: 'Nothing could be outlined there' }));
    const [failed, click, asked] = activity.children;
    // Green is for outcomes: the request ran a tool (find); the click is your own action, done on a quiet cell.
    expect(made).toEqual(['ready:Done', 'idle:Done', 'attention:Did not work']);
    expect(asked.children.at(-1)!.className).toBe('activity-cell');
    expect(asked.children.at(-1)!.dataset.state).toBe('ready');
    expect(failed.children.at(-1)!.dataset.state).toBe('attention');
    // When, to the minute, on the quiet line (the line's exact time stays with its details).
    expect(asked.children[0].textContent).toContain(`typed · ${clockWords(T0)}`);
    expect(click.children[0].textContent).toContain(clockWords(T0 + 20));
    // The newest shown entry starts its day.
    expect(failed.dataset.day).toBeTruthy();
    expect(click.dataset.day).toBeUndefined();
  } finally { drawActivityCells(null); }
});

test('drawn: starters stay until the conversation begins; an Activity line alone does not end them', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
  const starters = new Element('div');
  const log = new ChatLog(new Element('ol') as unknown as HTMLOListElement, starters as unknown as HTMLElement, {
    activity: new Element('ol') as unknown as HTMLOListElement, activityCount: () => {}, openActivity: vi.fn(),
    research: () => undefined, show: vi.fn(), describe: vi.fn(), hint: vi.fn(), voiceLive: () => false, unread: vi.fn() });
  log.apply(day());
  log.apply(event()); // a studio action recorded in Activity, as a new Project's "You started a new session" is
  expect(starters.hidden).toBe(false);
  log.apply({ type: 'chat', role: 'user', text: 'What am I looking at?', turnId: 9, thread: 't1' });
  expect(starters.hidden).toBe(true);
});

test('drawn: steps still running after their answer reach the Working strip; a replayed log left running does not', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
  const reported: StepFact[][] = [];
  const log = new ChatLog(new Element('ol') as unknown as HTMLOListElement, new Element('div') as unknown as HTMLElement, {
    activity: new Element('ol') as unknown as HTMLOListElement, research: () => undefined, show: vi.fn(), describe: vi.fn(), hint: vi.fn(),
    voiceLive: () => false, unread: vi.fn(), steps: (steps) => reported.push(steps) });
  log.apply(day());
  const running = step({ tool: 'outline', ok: null, summary: 'outline running', actionId: 'o-1', progress: 'loading micro-sam on this Mac' });
  log.apply({ type: 'chat', role: 'assistant', text: 'Outlining with micro-sam.', turnId: 8, thread: 't1', actions: [running] });
  expect(reported.at(-1)).toEqual([expect.objectContaining({ key: expect.stringContaining('r1:o-1'), tool: 'outline', ok: null, progress: 'loading micro-sam on this Mac' })]);
  log.apply({ type: 'chat-action', turnId: 8, thread: 't1', ...running, ok: true, summary: 'outlined 12 cells', progress: undefined });
  expect(reported.at(-1)![0]).toMatchObject({ ok: true, summary: 'outlined 12 cells' });
  // A replayed conversation whose step was left running is history, not work at hand.
  log.apply({ type: 'thread', day: DAY, thread: 't1', title: '', summary: '', lines: [
    { type: 'chat', role: 'assistant', text: 'Outlining.', turnId: 9, thread: 't1', actions: [{ ...running, actionId: 'o-2' }] }] });
  expect(reported.at(-1)).toEqual([]);
});

test('Activity lights green only for outcomes: a command is done on a quiet cell, a researcher with work to show is green', async () => {
  const { renderActivityEntry } = await import('./activity-render');
  const made: string[] = [];
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
  drawActivityCells((state, label) => { made.push(`${state}:${label}`); return new Element('svg') as unknown as SVGSVGElement; });
  try {
    const line = (fields: Partial<ActivityEntry['lines'][number]> = {}) => ({ key: 'l1', state: 'done', text: 'You put “Human-cells” on stage', at: T0, details: [], technical: [], models: [], ...fields }) as ActivityEntry['lines'][number];
    const entry = (fields: Partial<ActivityEntry> = {}) => ({ key: 'event:x', kind: 'command', heading: 'You', at: T0, order: 0, lines: [line()], lineKeys: [], failed: [], models: [], ...fields }) as ActivityEntry;
    const hooks = (session?: unknown) => ({ research: () => session as never, show: vi.fn(), opened: new Set<string>() });
    renderActivityEntry(entry(), hooks());
    renderActivityEntry(entry({ kind: 'research', research: 'r3', lines: [line({ text: 'Researcher 3 has findings' })] }), hooks({ outputs: [{ id: 'o1' }], result: null }));
    renderActivityEntry(entry({ kind: 'research', research: 'r4', lines: [line({ text: 'Researcher 4 stopped' })] }), hooks({ outputs: [], result: null }));
    renderActivityEntry(entry({ kind: 'model', lines: [line({ text: 'Cellpose-SAM is ready (loaded in 3 s)' })] }), hooks());
    expect(made).toEqual(['idle:Done', 'ready:Done', 'idle:Done', 'ready:Done']);
  } finally { drawActivityCells(null); }
});

test('a step skipped only because of a failure the brain put right is not a failure of the request', () => {
  // will-research, 30 Oct: the place was refused (no id), the Tab switch after it skipped, then the place made again in
  // that Tab, in front. The request held; its "!" said a step failed. A skip after a failure that stands still does.
  const created = step({ tool: 'tabs', actionId: 'a1' });
  const refused = step({ ok: false, summary: 'name what to place: its id from the screen or Data', actionId: 'a2' });
  const skipped = step({ tool: 'tabs', ok: false, summary: 'step 3 skipped: dependency 2 did not succeed', actionId: 'a3' });
  const placed = step({ summary: 'open in a window in Tab "Graphic", in front', actionId: 'a4' });
  expect(unresolvedFailures([created, refused, skipped, placed])).toEqual([]);
  expect(unresolvedFailures([created, refused, skipped])).toEqual([refused, skipped]);
});
