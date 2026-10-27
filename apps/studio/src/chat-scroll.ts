// Scrolling that respects reading (the workspace-feel plan, "The chat"; the mock's followLogs and .newmsg). The log
// follows the newest line while the reader is at its end; a reader who scrolled up stays where they are, and new lines
// raise "New messages ↓" just above the composer, which jumps to the start of the newest answer. Your own send always
// follows. An answer taller than the view is shown from its start, then following stops and the pill offers the rest.
//
// The log has `overflow-anchor: none` and anchors by hand: the Mac app is a WKWebView, which has no scroll anchoring, so
// while the reader is away the first line in view keeps its place however the lines above it change (a late answer
// rewritten, a card added, a picture that loaded). A ResizeObserver (injected, so tests can supply one; none, none used)
// keeps the end in view while following as an answer grows.

/** What the log is to scroll: an element's scroll box (the tests' stand-ins have only these). */
export interface ScrollBox {
  scrollTop: number;
  readonly scrollHeight: number;
  readonly clientHeight: number;
  readonly children: ArrayLike<Element> | Element[];
  addEventListener(type: 'scroll', listener: () => void, options?: AddEventListenerOptions): void;
}

type Observer = { observe(target: Element): void; unobserve(target: Element): void; disconnect(): void };
export type ObserverClass = new (callback: () => void) => Observer;

/** How close to the end still counts as at the end (px). */
const AT_END = 8;
/** The gap left above an answer shown from its start (the log's top padding). */
export const ANSWER_GAP = 16;

const num = (value: unknown): number | null => typeof value === 'number' && Number.isFinite(value) ? value : null;
const top = (el: Element): number | null => num((el as HTMLElement).offsetTop);
const height = (el: Element): number | null => num((el as HTMLElement).offsetHeight);
/** Drawn in the log's column: a hidden line (a standalone step's receipt, an empty line) reports top 0 and height 0,
 * which would break the order the search relies on, and is nothing to anchor to. */
const drawn = (el: Element): boolean => (el as HTMLElement).hidden !== true && height(el) !== 0;

export class ChatFollow {
  /** Whether the log keeps its end in view. */
  following = true;
  /** "New messages ↓": placed by the page 8 px above the composer; hidden unless the reader is away and there is news. */
  readonly pill: HTMLButtonElement;
  private readonly root: ScrollBox;
  private readonly observer: Observer | null;
  private newest: Element | null = null; // the start of the newest answer, where the pill goes
  private anchor: Element | null = null; // while away: the first line in view, and how far below the top it was
  private anchorOffset = 0;

  constructor(root: ScrollBox, observerClass?: ObserverClass | null) {
    this.root = root;
    this.pill = document.createElement('button');
    this.pill.type = 'button'; this.pill.className = 'chat-new-messages'; this.pill.hidden = true;
    this.pill.textContent = 'New messages';
    this.pill.append(arrow());
    this.pill.setAttribute('aria-label', 'New messages: show the newest answer');
    this.pill.addEventListener('click', () => this.jump());
    root.addEventListener('scroll', () => this.scrolled(), { passive: true });
    this.observer = observerClass ? new observerClass(() => this.resized()) : null;
    this.observer?.observe(root as unknown as Element);
  }

  /** A line was drawn: watched, so growing text keeps the end in view while following. */
  watch(item: Element): void { this.observer?.observe(item); }
  unwatch(item: Element): void { this.observer?.unobserve(item); }

  /** Your own send, or your words arriving: follow from here, to the end. */
  follow(): void {
    this.following = true;
    this.newest = null;
    this.pill.hidden = true;
    this.toEnd();
  }

  /** Lines changed. `news`: something new to read arrived (not a redraw of what was there); `answer`: a new answer's
   * line, shown from its start when it is taller than the view; `cleared`: another conversation was drawn. */
  changed(info: { news?: boolean; answer?: Element | null; cleared?: boolean } = {}): void {
    if (info.cleared) { this.follow(); return; }
    if (this.following) {
      const answer = info.answer ?? null;
      const view = num(this.root.clientHeight) ?? 0;
      const tall = answer && view > 0 && (height(answer) ?? 0) > view - ANSWER_GAP;
      if (answer && tall && top(answer) !== null) {
        // Taller than the view: from its start, and following stops there; the pill offers the rest.
        this.write(Math.max(0, top(answer)! - ANSWER_GAP));
        this.following = false;
        this.newest = answer;
        this.pill.hidden = false;
        this.remember();
        return;
      }
      this.toEnd();
      return;
    }
    if (info.news) {
      if (info.answer) this.newest = info.answer;
      this.pill.hidden = false;
    }
    this.restore();
  }

  /** The reading position a Project kept (its saved `reading`): there, following only if that is the end. */
  restoreReading(scrollTop: number): void {
    this.write(scrollTop);
    this.following = this.atEnd();
    this.pill.hidden = true;
    this.remember();
  }

  /** The pill: to the start of the newest answer while that is below the top of the view, else to the end. */
  private jump(): void {
    const at = this.newest && (this.newest as Element & { isConnected?: boolean }).isConnected !== false ? top(this.newest) : null;
    this.pill.hidden = true;
    if (at !== null && at - ANSWER_GAP > this.root.scrollTop + 1) {
      this.write(Math.max(0, at - ANSWER_GAP));
      this.newest = null;
      this.following = this.atEnd();
      this.remember();
      return;
    }
    this.follow();
  }

  private atEnd(): boolean {
    const box = this.root;
    return (num(box.scrollTop) ?? 0) + (num(box.clientHeight) ?? 0) >= (num(box.scrollHeight) ?? 0) - AT_END;
  }

  private toEnd(): void {
    const end = num(this.root.scrollHeight);
    if (end !== null) this.write(end);
  }

  private write(value: number): void {
    this.root.scrollTop = value;
  }

  /** A scroll, the reader's or one of ours (its event comes later): following is whether the end is in view. */
  private scrolled(): void {
    this.following = this.atEnd();
    if (this.following) { this.pill.hidden = true; this.newest = null; }
    this.remember();
  }

  private resized(): void {
    if (this.following) this.toEnd();
    else this.restore();
  }

  /** The first line in view, by its top (drawn lines are in order, so a binary search), and its distance below the top. */
  private remember(): void {
    const items = Array.from(this.root.children ?? []).filter(drawn);
    const scroll = num(this.root.scrollTop) ?? 0;
    let lo = 0, hi = items.length - 1, found: Element | null = null;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1, el = items[mid], t = top(el), h = height(el);
      if (t === null || h === null) return; // not laid out (a stand-in, or hidden): nothing to anchor
      if (t + h > scroll) { found = el; hi = mid - 1; } else lo = mid + 1;
    }
    this.anchor = found;
    this.anchorOffset = found ? top(found)! - scroll : 0;
  }

  /** Away from the end: the remembered line back where it was. */
  private restore(): void {
    const anchor = this.anchor;
    if (!anchor || (anchor as Element & { isConnected?: boolean }).isConnected === false || !drawn(anchor)) { this.remember(); return; }
    const at = top(anchor);
    if (at === null) return;
    const want = Math.max(0, at - this.anchorOffset);
    if (Math.abs(want - this.root.scrollTop) >= 1) this.write(want);
  }
}

/** A line icon (the mock's 1.5 px stroke), built node by node: no markup is parsed. */
export function lineIcon(d: string, className: string): Element {
  const make = (tag: string) => (document.createElementNS?.('http://www.w3.org/2000/svg', tag) ?? document.createElement(tag)) as Element;
  const svg = make('svg'), path = make('path');
  svg.setAttribute('viewBox', '0 0 16 16'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', className);
  path.setAttribute('d', d); svg.append(path);
  return svg;
}
const arrow = () => lineIcon('M4 6.5 8 10.5 12 6.5', 'chat-new-messages-arrow');
