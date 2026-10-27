// Tiny DOM stand-in: verifies association and side effects without a new browser dependency.
import { afterEach, expect, test, vi } from 'vitest';
import { ChatLog, SLOT_GRACE_MS, type ChatHooks } from './chatlog';
import { ExecutionRecordingControl } from './executionlog';
import type { ExecutionReference } from './protocol';
import highlight from './fixtures/execution-highlight.json';

let focusedNode: Node | null = null;

class Node {
  readonly tag: string;
  readonly children: Node[] = [];
  readonly dataset: Record<string, string> = {};
  readonly attributes: Record<string, string> = {};
  readonly listeners = new Map<string, (() => void)[]>();
  parentElement: Node | null = null;
  className = '';
  hidden = false;
  open = false;
  title = '';
  type = '';
  disabled = false;
  scrollHeight = 500;
  scrollWrites = 0;
  private scrolled = 0;
  private text = '';
  constructor(tag: string) { this.tag = tag; }
  get textContent(): string { return this.text + this.children.map(n => n.textContent).join(''); }
  set textContent(value: string) { this.replaceChildren(); this.text = value; }
  get scrollTop(): number { return this.scrolled; }
  set scrollTop(value: number) { this.scrolled = value; ++this.scrollWrites; }
  append(...nodes: (Node | string)[]): void {
    for (const value of nodes) {
      const node = typeof value === 'string' ? Object.assign(new Node('text'), { textContent: value }) : value;
      node.remove(); node.parentElement = this; this.children.push(node);
    }
  }
  prepend(node: Node): void { this.insertBefore(node, this.children[0] ?? null); }
  insertBefore(node: Node, next: Node | null): void {
    if (!next) { this.append(node); return; }
    node.remove(); node.parentElement = this; this.children.splice(this.children.indexOf(next), 0, node);
  }
  replaceWith(node: Node): void { this.parentElement?.insertBefore(node, this); this.remove(); }
  remove(): void {
    if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceChildren(...nodes: Node[]): void {
    for (const child of [...this.children]) child.remove();
    this.text = ''; this.append(...nodes);
  }
  addEventListener(type: string, listener: () => void): void {
    this.listeners.set(type, [...this.listeners.get(type) ?? [], listener]);
  }
  focus(): void {
    focusedNode = this;
    for (let node: Node | null = this; node; node = node.parentElement) node.fire('focusin');
  }
  setAttribute(name: string, value: string): void { this.attributes[name] = value; }
  querySelectorAll(selector: string): Node[] {
    if (selector === 'ul.actions > li') return find(this, 'ul').filter(n => n.className === 'actions').flatMap(n => n.children);
    return find(this, 'span').filter(n => n.className === selector.slice(1));
  }
  querySelector(selector: string): Node | undefined { return this.querySelectorAll(selector)[0]; }
  contains(node: Node): boolean { return this === node || this.children.some(child => child.contains(node)); }
  fire(type: string, event?: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) (listener as (event?: unknown) => void)(event);
  }
}

function find(root: Node, tag: string): Node[] {
  return root.children.flatMap(n => [...(n.tag === tag ? [n] : []), ...find(n, tag)]);
}

const ref = (fields: Partial<ExecutionReference> = {}): ExecutionReference => ({
  id: 'e1', kind: 'ask', day: '2026-10-06', thread: 't1', generation: 1, at: 1,
  turnId: 7, objectIds: ['o1'], trialId: null, revision: 1, outcome: 'completed', incomplete: false, ...fields,
});

function make(recording?: ExecutionRecordingControl, more: Partial<ChatHooks> = {}) {
  focusedNode = null;
  vi.stubGlobal('document', { createElement: (tag: string) => new Node(tag), get activeElement() { return focusedNode; } });
  const root = new Node('ol');
  const conversation = new Node('ol');
  const unread = vi.fn();
  const log = new ChatLog(conversation as unknown as HTMLOListElement, new Node('div') as unknown as HTMLElement, {
    activity: root as unknown as HTMLOListElement, research: () => undefined, show: vi.fn(), describe: vi.fn(), hint: vi.fn(), voiceLive: () => false, unread, executionRecording: recording,
    ...more,
  });
  log.apply({ type: 'day', id: '2026-10-06', active: 't1', label: '', threads: [], subjects: [], prefs: {} });
  return { log, root, conversation, unread };
}

afterEach(() => vi.unstubAllGlobals());

test('visible logging action turns off and back on using the same acknowledged state as inspection', () => {
  make();
  const change = vi.fn();
  const control = new ExecutionRecordingControl(change);
  const persistent = control.action();
  const inside = control.action(true);
  const top = find(persistent.root as unknown as Node, 'button')[0];
  const off = find(inside.root as unknown as Node, 'button')[0];
  expect(top.disabled).toBe(true);
  control.connected(true); control.apply(true);
  expect(top.textContent).toBe('Execution logging: On · Turn off');
  expect(off.textContent).toBe('Turn execution logging off');
  off.fire('click');
  expect(change).toHaveBeenCalledExactlyOnceWith(false);
  expect(top.disabled && off.disabled).toBe(true);
  control.apply(false);
  expect(top.textContent).toBe('Execution logging: Off · Turn on');
  expect(top.disabled).toBe(false);
  inside.dispose();
  top.fire('click');
  expect(change.mock.calls).toEqual([[false], [true]]);
  control.apply(true);
  expect(top.textContent).toBe('Execution logging: On · Turn off');
  control.connected(false);
  expect(top.disabled).toBe(true);
});

test('the inspector offers off before details load, keeps failures local, and clears only diagnostics on acknowledgement', async () => {
  make();
  const change = vi.fn();
  const recording = new ExecutionRecordingControl(change);
  recording.connected(true); recording.apply(true);
  const { log, root, unread } = make(recording);
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => highlight })));
  log.apply({ type: 'chat', role: 'assistant', text: 'Here is the cell.', turnId: 7 });
  log.apply({ type: 'execution', entry: ref() });
  const indicator = find(root, 'button')[0].parentElement!;
  indicator.fire('pointerenter');
  const off = find(indicator, 'button').find(b => b.textContent === 'Turn execution logging off');
  expect(off).toBeDefined();
  expect(indicator.textContent).toContain('Off stops logging, not model work.');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  const scrolls = root.scrollWrites, notices = unread.mock.calls.length;
  change.mockImplementationOnce(() => { throw Error('offline'); });
  off!.fire('click');
  expect(indicator.textContent).toContain('not confirmed');
  expect(off!.disabled).toBe(false);
  expect(indicator.textContent).toContain('Draw outline');
  off!.fire('click');
  expect(change.mock.calls).toEqual([[false], [false]]);
  recording.apply(false);
  log.apply({ type: 'execution-list', enabled: false, generation: 1, entries: [] });
  expect(root.textContent).not.toContain('Here is the cell.');
  expect(root.scrollWrites).toBe(scrolls);
  expect(unread.mock.calls.length).toBe(notices);
});

test('the recorded highlight shows each contribution in start order without claiming recovery', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => highlight }));
  vi.stubGlobal('fetch', fetch);
  const { log, root, unread } = make();
  log.apply({ type: 'chat', role: 'assistant', text: 'Highlighting the area.', turnId: 7 });
  log.apply({ type: 'execution', entry: ref() });
  const indicator = find(root, 'button')[0].parentElement!;
  const scrolls = root.scrollWrites, notifications = unread.mock.calls.length;
  expect(fetch).not.toHaveBeenCalled();
  expect(indicator.textContent).toBe('ⓘ');
  indicator.fire('pointerenter');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  const list = find(indicator, 'ol').find(n => n.className === 'execution-powers');
  expect(list?.children.map(n => n.textContent)).toEqual([
    'Handling the request — Gemini 3.5 Flash',
    'Looking at the view — Gemini 3.5 Flash',
    'Highlight attempt — failed',
    'Draw outline — EdgeTAM (on this Mac)Outline o2',
    'Checking the outline — Gemini 3.5 FlashOutline o2',
    'Appearance fingerprint — DINOv2 smallOutline o2',
  ]);
  expect(indicator.textContent).toContain('Started in this order; steps may overlap.');
  expect(indicator.textContent).not.toMatch(/recovered/i);
  expect(find(indicator, 'details')[0].open).toBe(false);
  log.apply({ type: 'execution', entry: ref({ revision: 2 }) });
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(root.scrollWrites).toBe(scrolls);
  expect(unread.mock.calls.length).toBe(notifications);
});

test('only a small indicator is visible; receipt revisions do not fetch, scroll or notify', () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const { log, root, unread } = make();
  log.apply({ type: 'chat', role: 'assistant', text: 'Answer one.', turnId: 7 });
  log.apply({ type: 'chat', role: 'assistant', text: 'Answer two.', turnId: 8 });
  const scrolls = root.scrollWrites, notifications = unread.mock.calls.length;
  log.apply({ type: 'execution', entry: ref() });
  log.apply({ type: 'execution', entry: ref({ revision: 2 }) });
  const buttons = find(root, 'button');
  expect(buttons).toHaveLength(1);
  expect(buttons[0].textContent).toBe('ⓘ');
  expect(buttons[0].attributes['aria-label']).toContain('What powers it');
  // Beside its own request's heading in Activity (the entry of turn 7, not 8).
  expect(buttons[0].parentElement?.parentElement?.parentElement?.dataset.turn).toBe('7');
  expect(find(root, 'section')[0].hidden).toBe(true);
  expect(fetch).not.toHaveBeenCalled();
  expect(root.scrollWrites).toBe(scrolls);
  expect(unread.mock.calls.length).toBe(notifications);
});

test('a click has a separate object-linked row, opens on demand, and vanishes on a conversation switch', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => ({ status: 'expired', steps: [] }) }));
  vi.stubGlobal('fetch', fetch);
  const { log, root } = make();
  log.apply({ type: 'execution', entry: ref({ kind: 'click', turnId: null }) });
  const indicator = find(root, 'button')[0].parentElement!;
  expect(indicator.parentElement?.dataset.role).toBe('execution');
  expect(find(indicator, 'button')[0].attributes['aria-label']).toContain('o1');
  expect(root.scrollWrites).toBe(0);
  expect(fetch).not.toHaveBeenCalled();
  indicator.fire('pointerenter');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(indicator.textContent).toContain('unavailable in memory and the retained archive');
  log.apply({ type: 'thread', day: '2026-10-06', thread: 't2', title: '', summary: '', lines: [] });
  expect(find(root, 'button')).toEqual([]);
  expect(fetch).toHaveBeenCalledTimes(1);
});

test('hover shows catalogue names, leaving discards details, and focus can inspect again', async () => {
  const fetch = vi.fn(async (_url: string, _options: { signal: AbortSignal }) => ({ ok: true, json: async () => ({ status: 'available', root: ref(), steps: [],
    powers: [{ capability: 'Outline cells', label: 'Cellpose-SAM (on this Mac)', provider: 'local', outcome: 'completed' }] }) }));
  vi.stubGlobal('fetch', fetch);
  const { log, root, unread } = make();
  log.apply({ type: 'chat', role: 'assistant', text: 'Outlining the cells.', turnId: 7 });
  log.apply({ type: 'execution', entry: ref() });
  const indicator = find(root, 'button')[0].parentElement!;
  const notifications = unread.mock.calls.length, scrolls = root.scrollWrites;
  indicator.fire('pointerenter');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  expect(indicator.textContent).toContain('Outline cells');
  expect(indicator.textContent).toContain('Cellpose-SAM (on this Mac)');
  expect(find(indicator, 'section')[0].hidden).toBe(false);
  indicator.fire('pointerleave');
  expect(find(indicator, 'section')[0].hidden).toBe(true);
  expect(indicator.textContent).not.toContain('Cellpose-SAM');
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
  indicator.fire('focusin');
  expect(fetch).toHaveBeenCalledTimes(2);
  expect(root.scrollWrites).toBe(scrolls);
  expect(unread.mock.calls.length).toBe(notifications);
});

test('background inspection receipts never add chat activity', () => {
  const { log, root, unread } = make();
  log.apply({ type: 'execution', entry: ref({ kind: 'sight', turnId: null }) });
  expect(root.children).toHaveLength(0);
  expect(unread).not.toHaveBeenCalled();
  expect(root.scrollWrites).toBe(0);
});

test('a pinned inspector stays open while focus leaves the receipt for problem marking', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true, json: async () => ({ status: 'available', root: ref(), steps: [] }) })));
  const { log, root } = make();
  log.apply({ type: 'chat', role: 'assistant', text: 'Photo unavailable.', turnId: 7 });
  log.apply({ type: 'execution', entry: ref() });
  const button = find(root, 'button')[0];
  const indicator = button.parentElement!;
  button.fire('click');
  await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); await Promise.resolve();
  indicator.fire('focusout', { relatedTarget: null });
  expect(find(indicator, 'section')[0].hidden).toBe(false);
  expect(indicator.textContent).toContain('Mark this problem');
  button.fire('click');
  expect(find(indicator, 'section')[0].hidden).toBe(true);
});

test('switching off closes inspection and removes only diagnostic UI without chat activity', async () => {
  const { log, root, unread } = make();
  const fetch = vi.fn(async (_url: string, _init: { signal: AbortSignal }) => ({ ok: true, json: async () => ({ status: 'available', steps: [] }) }));
  vi.stubGlobal('fetch', fetch);
  log.apply({ type: 'chat', role: 'assistant', text: 'Here is the cell.', turnId: 7 });
  log.apply({ type: 'execution-list', generation: 1, enabled: true, entries: [ref()] });
  const indicator = find(root, 'span').find(n => n.className === 'execution-indicator')!;
  indicator.fire('pointerenter');
  const writes = root.scrollWrites;
  const notifications = unread.mock.calls.length;
  log.apply({ type: 'execution-list', generation: 1, enabled: false, entries: [] });
  log.apply({ type: 'execution', entry: ref() }); // stale optional hint is ignored while off
  expect(find(root, 'span').some(n => n.className === 'execution-indicator')).toBe(false);
  expect(root.textContent).not.toContain('Here is the cell.');
  expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
  expect(root.scrollWrites).toBe(writes);
  expect(unread.mock.calls.length).toBe(notifications);
  log.apply({ type: 'execution-list', generation: 1, enabled: true, entries: [] });
  expect(find(root, 'span').some(n => n.className === 'execution-indicator')).toBe(false);
});

test('recording switch uses acknowledged server state, stays usable after failure, and disables offline', () => {
  make();
  const change = vi.fn();
  const control = new ExecutionRecordingControl(change);
  const button = find(control.root as unknown as Node, 'button')[0];
  expect(button.disabled).toBe(true);
  control.connected(true);
  control.apply(true);
  expect(button.attributes['aria-checked']).toBe('true');
  button.fire('click');
  expect(change).toHaveBeenCalledExactlyOnceWith(false);
  expect(button.disabled).toBe(true);
  expect(button.attributes['aria-checked']).toBe('true');
  control.apply(false); // broadcast also updates any other connected tab
  expect(button.disabled).toBe(false);
  expect(button.attributes['aria-checked']).toBe('false');
  change.mockImplementationOnce(() => { throw new Error('disconnected'); });
  button.fire('click');
  expect(button.attributes['aria-checked']).toBe('false');
  expect(control.root.textContent).toContain('not confirmed');
  control.connected(false);
  expect(button.disabled).toBe(true);
  control.connected(true);
  expect(button.disabled).toBe(true); // socket alone is not confirmation of recording state
  control.apply(false);
  expect(button.disabled).toBe(false);
});

test('unacknowledged recording change times out without inventing a state or preventing retry', () => {
  vi.useFakeTimers();
  try {
    make();
    const control = new ExecutionRecordingControl(vi.fn());
    const button = find(control.root as unknown as Node, 'button')[0];
    control.connected(true);
    control.apply(true);
    button.fire('click');
    vi.advanceTimersByTime(5000);
    expect(button.disabled).toBe(false);
    expect(button.attributes['aria-checked']).toBe('true');
    expect(control.root.textContent).toContain('not confirmed');
    button.fire('click');
    control.connected(false);
    expect(vi.getTimerCount()).toBe(0);
    control.connected(true);
    control.apply(false);
    expect(button.disabled).toBe(false);
  } finally {
    vi.useRealTimers();
  }
});


test('a completed answer preserves tool receipts, research button and attached inspection', () => {
  const { log, root, conversation } = make();
  log.apply({ type: 'chat', role: 'assistant', text: 'Measuring.', turnId: 7, research: 'r1',
    actions: [{ tool: 'query_population', ok: null, summary: 'Measuring', ms: 0 }] });
  log.apply({ type: 'execution', entry: ref() });
  log.apply({ type: 'chat-action', turnId: 7, tool: 'query_population', ok: true, ms: 10, summary: 'c1: length 92 px' });
  const receipt = find(root, 'ul')[0];
  const buttons = find(root, 'button');
  log.apply({ type: 'chat-action', turnId: 7, tool: 'query_population', ok: true, ms: 10,
    summary: 'c1: length 92 px', answer: 'The longest is 92 pixels.' });
  expect(conversation.textContent).toContain('The longest is 92 pixels.');
  expect(conversation.textContent).not.toContain('C1: length 92 px');
  expect(root.textContent).not.toContain('Measuring.');
  expect(root.textContent).toContain('C1: length 92 px');
  expect(find(root, 'ul')[0]).toBe(receipt);
  expect(find(root, 'button')).toEqual(buttons);
  expect(root.textContent).toContain('Show it');
});

test('the slot goes as its answer arrives; a step still running after it is the Working strip\'s until its receipt', () => {
  const steps: { tool: string; ok: boolean | null }[][] = [];
  const { log, conversation } = make(undefined, { steps: (facts) => steps.push(facts.map(f => ({ tool: f.tool, ok: f.ok }))) });
  log.thinking(true);
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(1);
  log.apply({ type: 'chat', role: 'assistant', text: 'Looking for your photo.', turnId: 51,
    actions: [{ tool: 'reference', ok: null, summary: 'Searching', ms: 0 }] });
  // Replaced in place: the answer sits where the slot was, and nothing else waits in the chat.
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  expect(conversation.children.at(-1)?.dataset.role).toBe('assistant');
  log.thinking(false);
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  expect(conversation.textContent).not.toContain('Reference photo in progress');
  expect(steps.at(-1)).toEqual([{ tool: 'reference', ok: null }]);
  log.apply({ type: 'chat-action', turnId: 51, tool: 'reference', ok: false, summary: 'No suitable photo found', ms: 1200 });
  expect(steps.at(-1)).toEqual([{ tool: 'reference', ok: false }]);
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
});

test('disconnection and replay never present an old pending receipt as live progress', () => {
  const { log, conversation } = make();
  const line = { type: 'chat' as const, role: 'assistant' as const, text: 'Looking.', turnId: 52,
    actions: [{ tool: 'reference', ok: null, summary: 'Searching', ms: 0 }] };
  log.apply(line); log.connected(false);
  expect(conversation.textContent).toContain('work status unavailable');
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  log.connected(true);
  log.apply({ type: 'thread', day: '2026-10-06', thread: 't1', title: '', summary: '', lines: [line] });
  expect(conversation.textContent).toContain('Earlier work has no final result recorded');
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
});

test('the second-photo request keeps a useful loading cue until the late photo receipt', () => {
  const steps: { tool: string; ok: boolean | null }[][] = [];
  const { log, conversation, root } = make(undefined, { steps: (facts) => steps.push(facts.map(f => ({ tool: f.tool, ok: f.ok }))) });
  const action = { tool: 'reference_photo', requestId: 'photo-request', actionId: 'photo-action' };
  log.thinking(true);
  log.apply({ type: 'chat', role: 'assistant', turnId: 66,
    text: 'I am still pulling up your second stoma reference photo while keeping the open image displayed.',
    actions: [{ ...action, ok: null, summary: 'still working', ms: 0 }] });
  log.thinking(false);
  // The cue is the step's pill on the Working strip ("Finding and loading your photo…" on its hover), not a line that
  // could stick in the chat.
  expect(steps.at(-1)).toEqual([{ tool: 'reference_photo', ok: null }]);
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  // The recorded sequence files the receipt before the separately reconciled answer.
  const complete = { type: 'chat-action' as const, turnId: 66, ...action, ok: true, ms: 9708,
    summary: 'the studio shows the requested window change' };
  log.apply({ ...complete, standalone: true });
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  expect(conversation.textContent).toContain('Photo displayed');
  // A receipt completes the visible status even when no replacement model answer arrives.
  expect(conversation.children.filter(n => n.dataset.role === 'work-complete')).toHaveLength(1);
  log.apply({ ...complete, answer: 'The second photo is open.' });
  expect(conversation.textContent).toContain('The second photo is open.');
  expect(root.textContent).toContain('Reference photo displayed');
});

test('a failed photo receipt replaces the spinner visibly even without a rewritten answer', () => {
  const { log, conversation } = make();
  log.apply({ type: 'chat', role: 'assistant', turnId: 67, text: 'The photo is still in progress.',
    actions: [{ tool: 'reference_photo', ok: null, summary: 'still working', ms: 0 }] });
  log.apply({ type: 'chat-action', turnId: 67, tool: 'reference_photo', ok: false,
    summary: 'No displayable photo (HTTP 403; HTTP 404)', ms: 17872 });
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  // No error card (2026-10-11: the model answers, not an error message): the answer's mark says it didn't work.
  expect(conversation.textContent).not.toContain('Photo unavailable · try another source');
  const mark = find(conversation, 'button').filter(b => b.className === 'activity-info')[0];
  expect(mark.textContent).toBe('!');
  expect(mark.dataset.state).toBe('failed');
  log.apply({ type: 'chat', role: 'user', text: 'What is this cell?' });
  expect(conversation.children.filter(n => n.dataset.role === 'work-failed')).toHaveLength(0);
});

test('a failed late window action replaces progress even when the answer still says in progress', () => {
  const { log, conversation } = make();
  log.apply({ type: 'chat', role: 'assistant', turnId: 68, text: 'Bringing it forward is still in progress.',
    actions: [{ tool: 'present', ok: null, summary: 'still working', ms: 0 }] });
  log.apply({ type: 'chat-action', turnId: 68, tool: 'present', ok: false,
    summary: 'The required draw was not confirmed before its deadline.', ms: 4034 });
  expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  expect(conversation.textContent).not.toContain('Window incomplete');
  expect(find(conversation, 'button').filter(b => b.className === 'activity-info')[0].textContent).toBe('!');
});

test('a step that failed and then worked when tried again is not shown as failed', () => {
  const { log, conversation } = make();
  log.apply({ type: 'chat', role: 'assistant', turnId: 69, text: 'Outlined the pith with micro-sam.',
    actions: [{ tool: 'use', ok: false, summary: "there is no 'SAM 3.1 (local)'", ms: 2 },
              { tool: 'use', ok: true, summary: 'for this request, outlining uses micro-sam', ms: 1 },
              { tool: 'find', ok: true, summary: 'outlined the pith', ms: 9000 }] });
  expect(conversation.textContent).not.toContain('incomplete');
  expect(find(conversation, 'button').filter(b => b.className === 'activity-info')[0].textContent).toBe('ⓘ');
});


test('Escape from inside an inspector restores focus without reopening or fetching, and later inspection still works', async () => {
  const fetch = vi.fn(async () => ({ ok: true, json: async () => highlight }));
  vi.stubGlobal('fetch', fetch);
  const { log, root } = make();
  log.apply({ type: 'execution', entry: ref({ kind: 'click', turnId: null }) });
  const summary = find(root, 'button')[0], indicator = summary.parentElement!;
  summary.fire('click');
  for (let n = 0; n < 4; n++) await Promise.resolve();
  const popup = find(indicator, 'section')[0];
  const inside = find(popup, 'button')[0];
  expect(inside).toBeDefined();
  inside.focus(); // real focusin bubbles through the inspector
  const stopPropagation = vi.fn(), preventDefault = vi.fn();
  indicator.fire('keydown', { key: 'Escape', stopPropagation, preventDefault });
  expect(focusedNode).toBe(summary);
  expect(popup.hidden).toBe(true);
  expect(summary.attributes['aria-expanded']).toBe('false');
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(stopPropagation).toHaveBeenCalledOnce();
  expect(preventDefault).toHaveBeenCalledOnce();
  stopPropagation.mockClear(); preventDefault.mockClear();
  indicator.fire('keydown', { key: 'Escape', stopPropagation, preventDefault });
  expect(stopPropagation).not.toHaveBeenCalled(); // a closed inspector does not swallow the next surface's Escape
  expect(preventDefault).not.toHaveBeenCalled();
  summary.fire('click');
  expect(popup.hidden).toBe(false);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test('once its steps have run, the slot says the answer is being written, not "Working on it"', () => {
  // Live 2026-10-11: the photo was on screen 6.9 s in, and "Working..." stayed 18.7 s more while the answer was written.
  vi.useFakeTimers();
  try {
    const { log, conversation } = make();
    log.thinking(true);
    expect(conversation.textContent).toContain('Working on it');
    log.thinking(true, 'answering');
    expect(conversation.textContent).toContain('Writing the answer');
    expect(conversation.textContent).not.toContain('Working on it');
    // Thinking ends just before its answer is filed: the slot waits for it, then goes even if none comes.
    log.thinking(false);
    expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(1);
    expect(conversation.textContent).toContain('Writing the answer');
    vi.advanceTimersByTime(SLOT_GRACE_MS - 1);
    expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(conversation.children.filter(n => n.dataset.role === 'thinking')).toHaveLength(0);
  } finally { vi.useRealTimers(); }
});
