// The message contract both ways (protocol.ts): the studio sends only commands the server has
// a handler for (ClientCommand), waits for replies by requestId, and a recorded conversation
// played through an in-memory connection draws the chat as a live socket would.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ChatLog } from './chatlog';
import { MemoryLink } from './memorylink';
import type { ChatAction, ChatLine, ServerMessage, ToolRun } from './protocol';

// Just enough of the DOM for ChatLog: elements with children, text, dataset and listeners.
class FakeElement {
  readonly tag: string;
  children: FakeElement[] = [];
  parent: FakeElement | null = null;
  dataset: Record<string, string> = {};
  className = '';
  type = '';
  title = '';
  hidden = false;
  scrollTop = 0;
  readonly scrollHeight = 0;
  private text = '';
  private readonly listeners = new Map<string, (() => void)[]>();

  constructor(tag: string, text = '') {
    this.tag = tag;
    this.text = text;
  }

  get textContent(): string {
    return this.tag === '#text' ? this.text : this.children.map((c) => c.textContent).join('');
  }

  set textContent(value: string) {
    this.children = [];
    if (value) this.append(value);
  }

  append(...nodes: (FakeElement | string)[]): void {
    for (const node of nodes) this.insertBefore(typeof node === 'string' ? new FakeElement('#text', node) : node, null);
  }

  prepend(node: FakeElement): void { this.insertBefore(node, this.children[0] ?? null); }
  insertBefore(node: FakeElement, before: FakeElement | null): void {
    node.remove();
    const at = before ? this.children.indexOf(before) : -1;
    this.children.splice(at < 0 ? this.children.length : at, 0, node);
    node.parent = this;
  }

  remove(): void {
    if (this.parent) this.parent.children = this.parent.children.filter((c) => c !== this);
    this.parent = null;
  }

  setAttribute(name: string, value: string): void { this.dataset[name] = value; }
  replaceChildren(...nodes: (FakeElement | string)[]): void { for (const child of [...this.children]) child.remove(); this.append(...nodes); }
  replaceWith(node: FakeElement): void { this.parent?.insertBefore(node, this); this.remove(); }

  private descendants(): FakeElement[] {
    return this.children.flatMap((c) => [c, ...c.descendants()]);
  }

  /** Only the selectors ChatLog uses: ".class" and "ul.actions > li". */
  querySelectorAll(selector: string): FakeElement[] {
    if (selector === 'ul.actions > li') {
      return this.descendants().filter((e) => e.tag === 'ul' && e.className === 'actions').flatMap((ul) => ul.children.filter((c) => c.tag === 'li'));
    }
    return this.descendants().filter((e) => `.${e.className}` === selector);
  }

  querySelector(selector: string): FakeElement | null {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  addEventListener(type: string, listener: () => void): void {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }
}

const running = (tool: string): ToolRun => ({ tool, ok: null, summary: `${tool} running`, ms: 0 });
const chat = (fields: Partial<ChatLine>): ChatLine => ({ type: 'chat', role: 'assistant', text: '', ...fields });
const action = (turnId: number, tool: string, summary: string, thread = 't1'): ChatAction =>
  ({ type: 'chat-action', turnId, tool, ok: true, summary, ms: 900, thread });

// A reload in the middle of a conversation, as the server sends it, then what happens next.
const recording: ServerMessage[] = [
  { type: 'hello', health: { state: 'live', stateLabel: 'Live', fps: 30, bridge: { url: '', targetDevice: null, ok: true, error: '' }, gpu: { ready: true, device: null, error: '' } } },
  { type: 'day', id: '2026-10-05', label: 'Oct 5', active: 't1', threads: [], subjects: [], prefs: {} },
  { type: 'thread', day: '2026-10-05', thread: 't1', title: 'Leaf', summary: '', lines: [
    chat({ role: 'user', text: 'What is that?' }),
    chat({ text: 'A stoma, outlining it.', turnId: 3, actions: [running('outline')] }),
    action(3, 'outline', 'outlined the stoma'),
  ] },
  { type: 'magnifier', space: 'frame', open: false, objectId: null, sources: [], index: 0 }, // not the chat's
  chat({ text: 'In the other conversation.', thread: 't2' }),
  chat({ role: 'user', text: 'Break it into parts', thread: 't1' }),
  { type: 'thinking', on: true },
  chat({ text: 'Breaking it into parts.', turnId: 5, thread: 't1', actions: [running('dissect')] }),
  { type: 'thinking', on: false },
  action(5, 'dissect', 'found 2 guard cells'), // finished after the answer: updates it in place
];

describe('a recorded conversation through an in-memory connection', () => {
  let root: FakeElement;
  let chatLog: ChatLog;
  let activity: FakeElement;
  let hints: string[];
  let link: MemoryLink;

  beforeEach(() => {
    vi.stubGlobal('document', { createElement: (tag: string) => new FakeElement(tag) });
    root = new FakeElement('ol'); activity = new FakeElement('ol');
    hints = [];
    chatLog = new ChatLog(root as unknown as HTMLOListElement, new FakeElement('div') as unknown as HTMLElement, {
      activity: activity as unknown as HTMLOListElement, research: () => undefined, show: () => {}, describe: () => {}, hint: (text) => hints.push(text),
      voiceLive: () => false, unread: () => {},
    });
    // What main.ts does with the chat's messages; the rest is not the chat's.
    link = new MemoryLink((message) => {
      if (message.type === 'thinking') chatLog.thinking(message.on);
      else if (['hello', 'day', 'thread', 'chat', 'chat-action'].includes(message.type)) chatLog.apply(message);
    });
  });

  afterEach(() => vi.unstubAllGlobals());

  test('draws the open conversation, with late results updating their answer in place', () => {
    link.replay(recording);
    const lines = root.children;
    expect(lines.map((li) => li.dataset.role)).toEqual(['user', 'assistant', 'user', 'assistant']);
    expect(lines.map((li) => li.children[0].textContent)).toEqual([
      'What is that?', 'A stoma, outlining it.', 'Break it into parts', 'Breaking it into parts.',
    ]);
    const done = (li: FakeElement) => li.querySelectorAll('ul.actions > li').map((a) => [a.dataset.state, a.children[0].textContent]);
    // Activity is newest first; the conversation itself retains chronological order.
    expect(done(activity.children[0])).toEqual([['done', 'Found 2 guard cells']]);
    expect(done(activity.children[1])).toEqual([['done', 'Outlined the stoma']]);
    // Its line says how long it took and opens to plain details, the tool's name behind "Technical".
    expect(activity.children[1].querySelectorAll('ul.actions > li')[0].textContent).toContain('900 ms');
    expect(activity.children[1].querySelectorAll('ul.actions > li')[0].textContent).toContain('Tool: outline');
    expect(done(lines[1])).toEqual([]); // receipts live only in Activity
    expect(hints).toEqual([]); // nothing was said twice
  });

  test('a reconnect replays it afresh rather than adding to it', () => {
    link.replay(recording);
    link.replay(recording.slice(0, 3));
    expect(root.children.map((li) => li.children[0].textContent)).toEqual(['What is that?', 'A stoma, outlining it.']);
  });
});

describe('commands', () => {
  test.each([
    [{summary:'The retained original changed; reopen its source.'}, 'The retained original changed; reopen its source.'],
    [{error:'Explicit failure', summary:'Other summary'}, 'Explicit failure'],
    [{}, 'The request failed. Please try again.'],
  ])('retains actionable refusal text and the structured reply (%j)', async (fields, text) => {
    const link = new MemoryLink(() => {});
    const pending = link.request('scores', {});
    link.reply({ok:false, ...fields});
    await expect(pending).rejects.toMatchObject({message:text, reply:{ok:false, ...fields}});
  });

  test('a request is answered by the reply with its requestId', async () => {
    const seen: string[] = [];
    const link = new MemoryLink((message) => seen.push(message.type));
    const scores = link.request('scores', {});
    expect(link.sent).toEqual([{ type: 'scores', requestId: 'r1' }]);
    link.reply({ ok: true, scores: { votes: 2, kinds: [] } });
    expect((await scores).scores?.votes).toBe(2);
    expect(seen).toEqual(['result']); // the studio still sees every reply

    const arena = link.request('arena', { arena: { enabled: true } });
    link.reply({ ok: false, error: 'pick 2 to 4' });
    await expect(arena).rejects.toThrow('pick 2 to 4');
  });

  test('only commands the server handles can be sent', () => {
    const link = new MemoryLink(() => {});
    link.send({ type: 'magnifier', action: 'level', level: 2.5 });
    link.send({ type: 'thread', action: 'switch', id: 't2' });
    // @ts-expect-error no such command (commands.py)
    link.send({ type: 'magnify', id: 'o1' });
    // @ts-expect-error a zoom says what to zoom to
    link.send({ type: 'magnifier', action: 'zoom' });
    expect(link.sent).toHaveLength(4);
  });
});

test('durable evidence identity joins identical pending delivery and survives a reconnect retry', async () => {
  class Disconnectable extends MemoryLink { disconnect() { this.rejectPending(new Error('Disconnected')); } }
  const link = new Disconnectable(()=>{});
  const fields = {rid:'r2',day:'2026-10-10',questionId:'q1',run:1,choice:'hold' as const};
  const first = link.request('research-evidence',fields,'evidence-original');
  const joined = link.request('research-evidence',fields,'evidence-original');
  expect(joined).toBe(first); expect(link.sent).toHaveLength(1);
  expect(link.sent[0].requestId).toBe('evidence-original');
  await expect(link.request('research-evidence',{...fields,choice:'decline'},'evidence-original')).rejects.toThrow('different inputs');
  expect(link.sent).toHaveLength(1);
  const rejected = expect(first).rejects.toThrow('Disconnected'); link.disconnect(); await rejected;
  const retry = link.request('research-evidence',fields,'evidence-original');
  expect(link.sent[1]).toEqual(link.sent[0]);
  link.reply({ok:true}); await expect(retry).resolves.toMatchObject({ok:true});
  await expect(link.request('scores',{},'r1')).rejects.toThrow('Invalid durable');
});
