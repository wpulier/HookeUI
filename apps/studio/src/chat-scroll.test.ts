// Scrolling that respects reading (chat-scroll.ts, and the chat that drives it): your own send always follows; while
// following, the end stays in view (also as an answer grows, through an injected ResizeObserver); a reader who scrolled
// up stays where they are, the first line in view keeping its place by hand (WKWebView has no scroll anchoring), and new
// lines raise "New messages", which jumps to the start of the newest answer; an answer taller than the view is shown
// from its start, then following stops and the pill shows.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { ANSWER_GAP, ChatFollow, type ObserverClass, type ScrollBox } from './chat-scroll';
import { ChatLog } from './chatlog';
import type { ServerMessage } from './protocol';
import { syntheticDOM, TestElement, fire } from './workspace-windows.test-support';

/** A scroll box laid out as a column: each child's top is the sum of the heights before it (style.height, 240 by default). */
class Box extends TestElement {
  clientHeight = 600;
  private top = 0;
  get scrollHeight(): number { return this.children.reduce((sum, child) => sum + child.offsetHeight, 0); }
  get scrollTop(): number { return this.top; }
  set scrollTop(value: number) { this.top = Math.max(0, Math.min(value, Math.max(0, this.scrollHeight - this.clientHeight))); }
  /** The reader scrolls: the position moves and the scroll event fires. */
  read(to: number): void { this.scrollTop = to; this.dispatchEvent(new Event('scroll')); }
}
class Line extends TestElement {
  // A hidden line is display:none: like the browser's, it reports top 0 and height 0.
  get offsetTop(): number { const parent = this.parentElement; if (!parent || this.hidden) return 0; let top = 0; for (const child of parent.children) { if (child === this) return top; top += child.offsetHeight; } return top; }
  get offsetHeight(): number { return this.hidden ? 0 : super.offsetHeight; }
}
const line = (height = 100) => { const node = new Line('li'); node.style.height = `${height}px`; return node; };

class FakeObserver {
  static last: FakeObserver | null = null;
  readonly watched = new Set<unknown>();
  readonly callback: () => void;
  constructor(callback: () => void) { this.callback = callback; FakeObserver.last = this; }
  observe(target: unknown) { this.watched.add(target); }
  unobserve(target: unknown) { this.watched.delete(target); }
  disconnect() { this.watched.clear(); }
}

let dom: ReturnType<typeof syntheticDOM>;
beforeEach(() => { dom = syntheticDOM(); vi.stubGlobal('document', dom.document); FakeObserver.last = null; });
afterEach(() => vi.unstubAllGlobals());

function setup(lines = 10) {
  const box = new Box('ol');
  for (let i = 0; i < lines; i++) box.append(line());
  const follow = new ChatFollow(box as unknown as ScrollBox, FakeObserver as unknown as ObserverClass);
  return { box, follow, observer: FakeObserver.last! };
}

describe('following and reading', () => {
  test('your send follows to the end and hides the pill; new lines keep the end in view while following', () => {
    const { box, follow } = setup();
    follow.follow();
    expect(box.scrollTop).toBe(400); // 1000 of lines in a 600 view
    expect(follow.pill.hidden).toBe(true);
    box.append(line(80)); follow.changed({ news: true });
    expect(box.scrollTop).toBe(480);
    expect(follow.pill.hidden).toBe(true);
  });

  test('scrolled up: the view stays put, the pill shows, and lines changing above keep the first line in view in its place', () => {
    const { box, follow } = setup();
    follow.follow();
    box.read(150); // the reader scrolls up into the second line
    expect(follow.following).toBe(false);
    box.append(line(300)); follow.changed({ news: true, answer: box.children.at(-1)! as unknown as Element });
    expect(box.scrollTop).toBe(150);
    expect(follow.pill.hidden).toBe(false);
    // A line above grew (a late answer rewritten): the line being read stays where it was on screen.
    box.children[0].style.height = '160px'; follow.changed({});
    expect(box.scrollTop).toBe(210);
    // Back at the end: following again, and the pill goes.
    box.read(box.scrollHeight - box.clientHeight);
    expect(follow.following).toBe(true);
    expect(follow.pill.hidden).toBe(true);
  });

  test('the pill jumps to the start of the newest answer, and once there it goes to the end', () => {
    const { box, follow } = setup();
    follow.follow(); box.read(0);
    const answer = line(700); box.append(answer); follow.changed({ news: true, answer: answer as unknown as Element });
    fire(follow.pill as unknown as TestElement, 'click');
    expect(box.scrollTop).toBe(answer.offsetTop - ANSWER_GAP);
    expect(follow.pill.hidden).toBe(true);
    follow.changed({ news: true }); // more arrives while reading it
    fire(follow.pill as unknown as TestElement, 'click');
    expect(box.scrollTop).toBe(box.scrollHeight - box.clientHeight);
    expect(follow.following).toBe(true);
  });

  test('an answer taller than the view is shown from its start, then following stops and the pill shows', () => {
    const { box, follow } = setup();
    follow.follow();
    const answer = line(900); box.append(answer); follow.changed({ news: true, answer: answer as unknown as Element });
    expect(box.scrollTop).toBe(1000 - ANSWER_GAP);
    expect(follow.following).toBe(false);
    expect(follow.pill.hidden).toBe(false);
    // A short answer is followed to the end.
    const other = setup();
    other.follow.follow();
    const short = line(120); other.box.append(short); other.follow.changed({ news: true, answer: short as unknown as Element });
    expect(other.box.scrollTop).toBe(other.box.scrollHeight - other.box.clientHeight);
    expect(other.follow.following).toBe(true);
  });

  test('growing content keeps the end in view while following (the ResizeObserver), and keeps the reading place when not', () => {
    const { box, follow, observer } = setup();
    follow.follow();
    const growing = line(100); box.append(growing); follow.watch(growing as unknown as Element); follow.changed({});
    expect(observer.watched.has(growing)).toBe(true);
    growing.style.height = '400px'; observer.callback();
    expect(box.scrollTop).toBe(box.scrollHeight - box.clientHeight);
    box.read(200);
    box.children[0].style.height = '150px'; observer.callback();
    expect(box.scrollTop).toBe(250);
    follow.unwatch(growing as unknown as Element);
    expect(observer.watched.has(growing)).toBe(false);
  });

  test('hidden lines between drawn ones (a standalone step\'s receipt, an empty line) never become the anchor', () => {
    const box = new Box('ol');
    for (let i = 0; i < 10; i++) { box.append(line()); const hidden = line(); hidden.hidden = true; box.append(hidden); }
    const follow = new ChatFollow(box as unknown as ScrollBox, FakeObserver as unknown as ObserverClass);
    const drawn = box.children.filter((child) => !child.hidden);
    follow.follow();
    box.read(350); // into the fourth drawn line (top 300): that line is the one being read
    // A line further down the view grows (a card group placed under a request): the reader is not moved.
    drawn[4].style.height = '300px'; follow.changed({});
    expect(box.scrollTop).toBe(350);
    // A line above grows (a late answer rewritten): the line being read keeps its place on screen.
    drawn[0].style.height = '160px'; follow.changed({});
    expect(box.scrollTop).toBe(410);
    // The line being read is hidden in turn: the next drawn line in view becomes the anchor, and nothing jumps.
    drawn[3].hidden = true; follow.changed({});
    const before = box.scrollTop;
    drawn[1].style.height = '120px'; follow.changed({});
    expect(box.scrollTop).toBe(before + 20);
  });

  test('without a ResizeObserver it still follows as lines are drawn; a saved reading position comes back', () => {
    const box = new Box('ol'); for (let i = 0; i < 10; i++) box.append(line());
    const follow = new ChatFollow(box as unknown as ScrollBox, null);
    follow.watch(box.children[0] as unknown as Element); // nothing to observe with: no failure
    follow.changed({ news: true });
    expect(box.scrollTop).toBe(400);
    follow.restoreReading(120);
    expect(box.scrollTop).toBe(120);
    expect(follow.following).toBe(false);
    follow.restoreReading(400);
    expect(follow.following).toBe(true);
  });
});

describe('the chat drives it', () => {
  const DAY = '2026-10-24';
  test('your words arriving follow even when you had scrolled up; others\' lines raise the pill; another conversation shows its end', () => {
    const box = new Box('ol');
    const log = new ChatLog(box as unknown as HTMLOListElement, new TestElement('div') as unknown as HTMLElement, {
      research: () => undefined, show: vi.fn(), describe: vi.fn(), hint: vi.fn(), voiceLive: () => false, unread: vi.fn(),
      ResizeObserver: FakeObserver as unknown as ObserverClass });
    const say = (m: ServerMessage) => log.apply(m);
    say({ type: 'day', id: DAY, label: '', active: 't1', threads: [], subjects: [], prefs: {} });
    for (let i = 0; i < 6; i++) { say({ type: 'chat', role: 'user', text: `Question ${i}`, thread: 't1' }); say({ type: 'chat', role: 'assistant', text: `Answer ${i}`, turnId: i, thread: 't1' }); }
    expect(box.scrollTop).toBe(box.scrollHeight - box.clientHeight);
    box.read(100);
    say({ type: 'chat', role: 'assistant', text: 'Late news', turnId: 9, thread: 't1', proactive: true });
    expect(box.scrollTop).toBe(100);
    expect(log.newMessages.hidden).toBe(false);
    say({ type: 'chat', role: 'user', text: 'And now?', thread: 't1' });
    expect(box.scrollTop).toBe(box.scrollHeight - box.clientHeight);
    expect(log.newMessages.hidden).toBe(true);
    box.read(0); log.sent();
    expect(box.scrollTop).toBe(box.scrollHeight - box.clientHeight);
    box.read(0);
    say({ type: 'thread', day: DAY, thread: 't2', title: '', summary: '', lines: Array.from({ length: 4 }, (_, i): ServerMessage[] => [
      { type: 'chat', role: 'user', text: `Elsewhere ${i}`, thread: 't2' }, { type: 'chat', role: 'assistant', text: 'There.', turnId: i + 1, thread: 't2' }]).flat() });
    expect(box.scrollHeight).toBeGreaterThan(box.clientHeight);
    expect(box.scrollTop).toBe(box.scrollHeight - box.clientHeight);
    expect(log.newMessages.hidden).toBe(true); // a conversation drawn afresh is not news
  });
});
