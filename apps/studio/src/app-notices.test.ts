// TEST-only synthetic wire/DOM evidence through actual Link, ChatModel, ChatLog,
// Day/thread replay and ExecutionLogView. No ToolRun/receipt or model reply is
// inferred from notices. The DOM is external input, not painted-browser proof.
import { afterEach, expect, test, vi } from 'vitest';
import { ChatLog } from './chatlog';
import { ChatModel } from './chatmodel';
import { MemoryLink } from './memorylink';
import type { AppNotice, ExecutionReference, ServerMessage } from './protocol';

class Element {
  readonly children: Element[] = [];
  readonly dataset: Record<string, string> = {};
  readonly attributes: Record<string, string> = {};
  readonly listeners = new Map<string, (() => void)[]>();
  parentElement: Element | null = null;
  className = '';
  hidden = false;
  title = '';
  type = '';
  id = '';
  scrollTop = 0;
  scrollHeight = 500;
  private text = '';
  readonly tag: string;
  constructor(tag: string) { this.tag = tag; }
  get textContent(): string { return this.text + this.children.map(node => node.textContent).join(''); }
  set textContent(text: string) { this.replaceChildren(); this.text = text; }
  set innerHTML(_html: string) { throw new Error('Unsafe application notice HTML parsing'); }
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
  remove(): void {
    if (this.parentElement) this.parentElement.children.splice(this.parentElement.children.indexOf(this), 1);
    this.parentElement = null;
  }
  replaceChildren(...nodes: Element[]): void {
    for (const node of [...this.children]) node.remove();
    this.text = ''; this.append(...nodes);
  }
  setAttribute(name: string, value: string): void { this.attributes[name] = value; }
  addEventListener(type: string, listener: () => void): void {
    this.listeners.set(type, [...this.listeners.get(type) ?? [], listener]);
  }
  contains(node: Element): boolean { return this === node || this.children.some(child => child.contains(node)); }
  querySelectorAll(selector: string): Element[] {
    const nodes = descendants(this);
    if (selector === 'ul.actions > li') return nodes.filter(node => node.tag === 'ul' && node.className === 'actions')
      .flatMap(node => node.children.filter(child => child.tag === 'li'));
    if (selector.startsWith('.')) return nodes.filter(node => node.className.split(' ').includes(selector.slice(1)));
    return nodes.filter(node => node.tag === selector);
  }
  querySelector(selector: string): Element | undefined { return this.querySelectorAll(selector)[0]; }
}

function descendants(root: Element): Element[] {
  return root.children.flatMap(child => [child, ...descendants(child)]);
}

const DAY = '2026-10-09';
const SUMMARY = '<img src=x onerror="synthetic()"> Holding stopped: no camera frames arrived.';
const payload = (fields: Partial<AppNotice> = {}): AppNotice => ({
  type: 'app-notice', day: DAY, thread: 't1', at: 1791547200,
  producer: { kind: 'held', id: 'synthetic-external-clip' }, event: 'stopped', summary: SUMMARY, tone: 'error',
  facts: { frames: 0, secondsAgreed: 15, reason: 'no camera frames', released: false },
  origin: { day: DAY, asked: { thread: 't1', generation: 1 } }, ...fields,
});
const day = (thread = 't1', id = DAY): ServerMessage => ({
  type: 'day', id, label: '', active: thread, threads: [], subjects: [], prefs: {},
});
const replay = (thread: string, lines: ServerMessage[], id = DAY): ServerMessage => ({
  type: 'thread', day: id, thread, title: '', summary: '', lines,
});

function renderer(voiceLive = false) {
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
  const root = new Element('ol');
  const conversation = new Element('ol');
  const hooks = { activity: root as unknown as HTMLOListElement, research: vi.fn(() => undefined), show: vi.fn(), describe: vi.fn(), hint: vi.fn(),
    voiceLive: () => voiceLive, unread: vi.fn() };
  const log = new ChatLog(conversation as unknown as HTMLOListElement, new Element('div') as unknown as HTMLElement, hooks);
  // This is an external synthetic server stream; actual Link forwards all events
  // to actual ChatLog. Production main.ts's dispatch is covered by its build gate.
  const link = new MemoryLink(message => log.apply(message));
  link.replay([day()]);
  return { root, conversation, hooks, log, link };
}

afterEach(() => vi.unstubAllGlobals());

test('notice wire becomes separate activity with intact facts and no ToolRun or assistant answer', () => {
  const model = new ChatModel(() => false);
  model.apply(day());
  const notice = payload({ origin: { day: DAY } }); // missing IDs remain unavailable
  const changes = model.apply(notice);
  expect(changes).toHaveLength(1);
  expect(changes[0]).toMatchObject({ kind: 'add', unread: true });
  expect(model.lines).toHaveLength(1);
  expect(model.lines[0]).toMatchObject({ role: 'activity', actions: [], notice });
  expect(model.lines[0].turnId).toBeUndefined();
  expect(model.lines[0].notice?.origin).toEqual({ day: DAY });
  expect(model.lines.filter(line => line.role === 'assistant')).toEqual([]);
  expect(changes.some(change => change.kind === 'hint' || change.kind === 'describe')).toBe(false);
});

test.each([false, true])('actual ChatLog paints safe explicit application status without voice dependence (voice %s)', live => {
  const { root, hooks, link } = renderer(live);
  link.replay([payload()]);
  const activity = root.children.filter(node => node.dataset.role === 'activity');
  expect(activity).toHaveLength(1);
  expect(activity[0].textContent).toContain('App activity');
  expect(activity[0].textContent).toContain(SUMMARY);
  expect(descendants(activity[0]).some(node => node.tag === 'img')).toBe(false);
  expect(activity[0].dataset.turn).toBeUndefined();
  expect(activity[0].dataset.state).toBe('failed');
  expect(root.children.some(node => node.dataset.role === 'assistant' || node.dataset.role === 'request')).toBe(false);
  // Its one line is the notice's own words: no tool step (ToolRun) is made of it.
  expect(descendants(root).filter(node => node.tag === 'li' && node.dataset.tool !== undefined)).toEqual([]);
  expect(hooks.hint).not.toHaveBeenCalled();
  expect(hooks.describe).not.toHaveBeenCalled();
  expect(hooks.unread).not.toHaveBeenCalled();
});

test('switch and replay retain activity as activity without hints or repeated unread signals', () => {
  const { root, hooks, link } = renderer();
  const original = payload();
  link.replay([original]);
  expect(hooks.unread).not.toHaveBeenCalled();
  link.replay([day('t2'), replay('t2', [])]);
  link.replay([original]); // live original-conversation arrival is not shown in t2
  expect(root.children).toEqual([]);
  expect(hooks.unread).not.toHaveBeenCalled();
  link.replay([replay('t1', [original])]);
  expect(root.children).toHaveLength(1);
  expect(root.children[0].dataset.role).toBe('activity');
  expect(root.children[0].textContent).toContain(SUMMARY);
  expect(hooks.unread).not.toHaveBeenCalled();
  expect(hooks.hint).not.toHaveBeenCalled();
  // A new renderer is a reload, not a second authored assistant answer.
  const reload = renderer(true);
  reload.link.replay([replay('t1', [original])]);
  expect(reload.root.children[0].dataset.role).toBe('activity');
  expect(reload.root.children[0].textContent).toBe(root.children[0].textContent);
  expect(reload.hooks.unread).not.toHaveBeenCalled();
});

test('same producer events append independently; real model text stays intact beside them', () => {
  const model = new ChatModel(() => false);
  model.apply(day());
  model.apply({ type: 'chat', role: 'assistant', text: 'Synthetic external model feedback.', turnId: 41, thread: 't1' });
  const first = payload({ event: 'holding', summary: 'Synthetic hold started.', tone: 'info' });
  const second = payload({ origin: { day: DAY, asked: { thread: 't1', generation: 1 }, turnId: 41,
    requestId: 'synthetic-existing-request', actionId: 'synthetic-existing-occurrence' } });
  model.apply(first); model.apply(second);
  expect(model.lines.filter(line => line.role === 'assistant').map(line => line.text))
    .toEqual(['Synthetic external model feedback.']);
  const activity = model.lines.filter(line => line.role === 'activity');
  expect(activity).toHaveLength(2);
  expect(activity.map(line => line.notice)).toEqual([first, second]);
  expect(activity.every(line => line.turnId === undefined && line.actions.length === 0)).toBe(true);
  model.apply(replay('t1', [{ type: 'chat', role: 'assistant', text: 'Synthetic external model feedback.', turnId: 41 }, first, second]));
  expect(model.lines.map(line => line.role)).toEqual(['assistant', 'activity', 'activity']);
});

test('notice origin IDs never become a model-answer execution anchor', () => {
  const { root, link } = renderer();
  link.replay([payload({ origin: { day: DAY, turnId: 41, requestId: 'synthetic-existing-request',
    actionId: 'synthetic-existing-occurrence' } })]);
  // Explicitly synthetic diagnostic wire input, not a fabricated ToolRun.
  const execution: ExecutionReference = { id: 'synthetic-external-execution', kind: 'ask', at: 1791547200,
    day: DAY, thread: 't1', generation: 1, turnId: 41, objectIds: [], trialId: null,
    outcome: 'completed', revision: 1, incomplete: false };
  link.replay([{ type: 'execution-list', enabled: true, entries: [execution], generation: 1 }]);
  const activity = root.children.find(node => node.dataset.role === 'activity')!;
  expect(activity).toBeDefined();
  expect(descendants(activity).some(node => node.className === 'execution-indicator')).toBe(false);
  expect(activity.dataset.turn).toBeUndefined();
  link.replay([{ type: 'chat', role: 'assistant', text: 'Synthetic actual model response.', turnId: 41, thread: 't1' }]);
  const answer = root.children.find(node => node.dataset.role === 'request')!; // the request's own entry
  expect(descendants(answer).some(node => node.className === 'execution-indicator')).toBe(true);
  expect(descendants(activity).some(node => node.className === 'execution-indicator')).toBe(false);
});

test('old origin stays explicit after new-day filing and cannot open a reused research ID', () => {
  const { root, hooks, link } = renderer();
  const notice = payload({ producer: { kind: 'research', id: 'synthetic-reused-r1' }, event: 'terminal',
    summary: 'Synthetic earlier research stopped.', origin: { day: '2026-10-08',
      asked: { thread: 't1', generation: 1 }, researchId: 'synthetic-reused-r1' } });
  link.replay([notice]);
  expect(root.children).toHaveLength(1);
  expect(root.children[0].dataset.role).toBe('activity');
  expect(root.children[0].textContent).toContain('2026-10-08');
  expect(descendants(root).filter(node => node.className === 'open-research')).toEqual([]);
  expect(hooks.show).not.toHaveBeenCalled();
});

test('the resume line is filed once per session load: a reload replays it once, and its live telling beside the replay is one line', () => {
  const SID = '2026-10-12.2';
  const resume = payload({ day: SID, tone: 'info', producer: { kind: 'session', id: SID }, event: 'resumed',
    summary: "Back in 'Leaf epidermis' (last used yesterday 8:12 pm). Its outlines are in its pictures; the live view starts fresh.",
    facts: { liveOutlines: false }, origin: { day: SID } });
  // Opening the session: the conversation's replay holds the line it filed, and the line is also told live.
  const { root, link, hooks } = renderer();
  link.replay([day('t1', SID), replay('t1', [resume], SID), resume]);
  const lines = () => root.children.filter((node) => node.textContent.includes('Back in'));
  expect(lines()).toHaveLength(1);
  // Told live first, then the replay: still one.
  link.replay([resume, replay('t1', [resume], SID)]);
  expect(lines()).toHaveLength(1);
  // A reload (a new connection: hello resets, then the replay): once, never twice.
  link.replay([{ type: 'hello', health: {} as never }, day('t1', SID), replay('t1', [resume], SID)]);
  expect(lines()).toHaveLength(1);
  expect(hooks.hint).not.toHaveBeenCalled();
  // A second, different notice of the same producer is its own line.
  link.replay([{ ...resume, at: resume.at + 60, summary: 'Back in it again.' }]);
  expect(root.children.filter((node) => node.textContent.includes('Back in'))).toHaveLength(2);
});
