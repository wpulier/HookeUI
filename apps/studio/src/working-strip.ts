// The Working strip (workspace-feel plan, "Working strip"; the mock's strip()): a fixed 40 px row under the Stage and the
// Assistant, always there, so the Stage never resizes and the eyepiece never rescales as work comes and goes. Left, one
// pill per piece of work alive now or finished and not yet seen: a researcher, a run that lasted more than a second, an
// agent step still running after its answer, a model loading. Each pill is its lit cell, its name, its elapsed time
// (tabular) or its state in words, and ×; ordered needs you, working, finished. When they run out of room they go
// compact (the name alone), then the rest fold into one +N that opens Researchers. With nothing at work the left holds
// the view's passing line (#hint: "Click anything in the view to outline it."). Right, the performance meter and
// Spending (moved off the Stage's corner), compact under a narrow Stage. Pills are drawn in place, never rebuilt, so a
// cell that turns ready settles once and focus never moves; the clock is the only thing that changes each second.

import { LIT_CELL_WORDS, litCell, setLitCell, type LitCellState } from './lit-cell';
import { tabMark } from './tab-marks';
import { holdUntilLanded } from './side-motion';
import { setVoiceMark, voiceMark, type ShownVoice } from './voice-mark';

/** Needs you, working, finished (the order of the strip). */
export type Urgency = 0 | 1 | 2;

export interface StripPill {
  id: string;
  name: string;
  cell: LitCellState;
  urgency: Urgency;
  /** When it began (ms since the epoch): orders the pills of one urgency, oldest first, and is where the elapsed time
   * counts from while `counting`. */
  since: number;
  /** Its words are the time since `since`, counted every second (work under way). */
  counting?: boolean;
  /** Its words when not counting ("Needs you", "Partial findings", "Stop requested"). */
  words?: string;
  /** For `setup`: a measured fraction (lit-cell.ts never invents one). */
  fraction?: number | null;
  /** Not shown before this time (ms since the epoch): work that ends within a second never flashes a pill. */
  after?: number;
  /** The pill's tooltip: what it is and what clicking does. */
  title: string;
  /** The ×'s name: what hiding does ("Hide Researcher 4 from the strip. It stays in Researchers."). */
  hide: string;
  /** Opens or focuses where it lives (its Tab, its run page, Data, Activity). */
  open(): void;
  /** Hides the pill only; the work goes on and stays where it lives. */
  dismiss(): void;
  /** The voice's presence (voice-presence.ts): its state mark instead of a lit cell. */
  voice?: ShownVoice;
  /** First in the strip, before every piece of work (the voice's presence). */
  lead?: boolean;
  /** No × (the voice's presence is not a piece of work to hide). */
  fixed?: boolean;
}

/** "0:42", "12:05", "1:01:14": a clock, never a percent. */
export function clock(ms: number): string {
  const t = Math.max(0, Math.floor(ms / 1000)), h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}

/** The strip's order: needs you, then working, then finished; within each, the oldest first (so a pill keeps its place
 * as newer work arrives after it). */
export function orderPills(pills: readonly StripPill[]): StripPill[] {
  return [...pills].sort((a, b) => Number(!!b.lead) - Number(!!a.lead) || a.urgency - b.urgency || a.since - b.since
    || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
}

/** The words a pill shows now. */
export function pillWords(pill: StripPill, now: number): string {
  return pill.counting ? clock(now - pill.since) : pill.words ?? '';
}

/** A Stage narrower than this keeps the meters to their dot and figure (the mock's `is-tight`). */
export const TIGHT_STAGE = 760;
const TICK_MS = 1000;

interface PillNode {
  root: HTMLElement; open: HTMLButtonElement; cell: SVGSVGElement; name: HTMLElement; state: HTMLElement; x: HTMLButtonElement;
  pill: StripPill; shown: LitCellState | null; words: string; mark: HTMLElement | null;
}

export interface StripOptions {
  /** The pills' row (the page's #shelf, re-homed here). */
  pills: HTMLElement;
  /** The view's passing line (#hint): the strip's left while nothing works, after the pills when it has news. */
  hint?: HTMLElement | null;
  /** What sits at the right end (the meters). */
  end?: HTMLElement | null;
  /** The Stage: its width decides the compact meters. */
  stage?: HTMLElement | null;
  /** The workspace grid the strip is a row of: it reaches under the Assistant's column to the window's edge. */
  studio?: HTMLElement | null;
  now?: () => number;
}

export class WorkingStrip {
  readonly root: HTMLElement;
  readonly pills: HTMLElement;
  readonly end: HTMLElement;
  private readonly hint: HTMLElement | null;
  private readonly stage: HTMLElement | null;
  private readonly studio: HTMLElement | null;
  private readonly now: () => number;
  private readonly sources = new Map<string, StripPill[]>();
  private readonly nodes = new Map<string, PillNode>();
  private readonly more: HTMLElement;
  private readonly moreOpen: HTMLButtonElement;
  private readonly moreCell: SVGSVGElement;
  private readonly moreCount: HTMLElement;
  private readonly live: HTMLElement;
  private shown: StripPill[] = [];
  private primed = false;
  private ticker: ReturnType<typeof setInterval> | null = null;
  private waiting: ReturnType<typeof setTimeout> | null = null;
  private queued = false;
  /** The +N pill: Researchers when everything it folded is a researcher, else Activity, where every run, load and step
   * is listed. */
  openList: (where: 'researchers' | 'activity') => void = () => {};
  private foldTarget: 'researchers' | 'activity' = 'researchers';

  constructor(options: StripOptions) {
    this.now = options.now ?? (() => Date.now());
    this.root = document.createElement('footer');
    this.root.className = 'working-strip';
    this.root.id = 'working-strip';
    this.root.setAttribute('aria-label', 'Working now');
    this.pills = options.pills;
    this.pills.classList.add('strip-pills');
    this.pills.setAttribute('aria-label', 'Researchers and runs working now, or with work to open');
    this.pills.hidden = true;
    this.hint = options.hint ?? null;
    this.stage = options.stage ?? null;
    this.studio = options.studio ?? null;
    this.end = options.end ?? document.createElement('div');
    this.end.classList.add('strip-end');
    this.more = document.createElement('span');
    this.more.className = 'strip-pill strip-more';
    this.more.hidden = true;
    this.moreOpen = document.createElement('button');
    this.moreOpen.type = 'button';
    this.moreOpen.className = 'strip-pill-open';
    this.moreCell = litCell('working', { size: 12, decorative: true });
    this.moreCount = document.createElement('b');
    this.moreCount.className = 'strip-pill-name mm-num';
    this.moreOpen.append(this.moreCell, this.moreCount);
    this.moreOpen.addEventListener('click', () => this.openList(this.foldTarget));
    this.more.append(this.moreOpen);
    this.pills.replaceChildren(this.more);
    // Said once when work turns ready, needs the person, or did not finish (the strip itself is quiet).
    this.live = document.createElement('span');
    this.live.className = 'visually-hidden';
    this.live.setAttribute('aria-live', 'polite');
    this.root.append(this.pills, ...(this.hint ? [this.hint] : []), this.live, this.end);
    if (typeof ResizeObserver === 'function') {
      const watch = new ResizeObserver(() => this.layout());
      watch.observe(this.root);
      if (this.stage) watch.observe(this.stage);
      if (this.studio) watch.observe(this.studio);
    }
    if (this.hint && typeof MutationObserver === 'function') {
      // The line's whole words stay in its tooltip when the strip cuts them short; news among the pills is re-fitted.
      new MutationObserver(() => { this.titleHint(); this.schedule(); }).observe(this.hint, { childList: true, characterData: true, subtree: true });
      this.titleHint();
    }
    if (typeof window !== 'undefined') window.addEventListener?.('resize', () => this.layout());
    // A tool's page docking on the right (or leaving), or the Assistant opening or closing, can leave the grid's size as
    // it was (the docked page takes the Assistant's width), so no resize says the strip's reach must change. Re-placed
    // in the next frame: the attributes change inside a press, and measuring there forced a full style pass (b041).
    if (typeof MutationObserver === 'function' && document.body)
      new MutationObserver(() => this.layoutSoon()).observe(document.body, { attributes: true, attributeFilter: ['data-control-right-active', 'data-study-inspector'] });
  }

  /** The pills one source shows now (researchers, runs, steps, loads), replacing that source's last ones. */
  set(source: string, pills: StripPill[]): void {
    this.sources.set(source, pills);
    this.schedule();
  }

  /** What the strip shows now, in its order (folded ones included). */
  current(): readonly StripPill[] {
    return this.shown;
  }

  /** Draw now (tests; the strip otherwise draws once per frame). */
  flush(): void {
    this.queued = false;
    this.render();
  }

  private readonly layoutLater = () => this.layout();
  private layoutQueued = false;
  /** Laid out once, in the next frame. */
  private layoutSoon(): void {
    if (this.layoutQueued) return;
    this.layoutQueued = true;
    const run = () => { this.layoutQueued = false; this.layout(); };
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(run); else queueMicrotask(run);
  }

  private schedule(): void {
    if (this.queued) return;
    this.queued = true;
    if (typeof requestAnimationFrame === 'function') requestAnimationFrame(() => { if (this.queued) this.flush(); });
    else queueMicrotask(() => { if (this.queued) this.flush(); });
  }

  private render(): void {
    const now = this.now();
    const all = orderPills([...this.sources.values()].flat());
    const later = all.filter((p) => p.after !== undefined && p.after > now);
    const pills = all.filter((p) => !later.includes(p));
    if (this.waiting !== null) { clearTimeout(this.waiting); this.waiting = null; }
    if (later.length) this.waiting = setTimeout(() => { this.waiting = null; this.schedule(); }, Math.min(...later.map((p) => p.after!)) - now + 16);
    const keep = new Set(pills.map((p) => p.id));
    for (const [id, node] of this.nodes) if (!keep.has(id)) { node.root.remove(); this.nodes.delete(id); }
    const ordered = pills.map((p) => this.draw(p, now));
    // In place: a pill that stays keeps its element (its cell's motion, its hover, its focus); only a change of order moves one.
    ordered.forEach((node, i) => { if (this.pills.children[i] !== node) this.pills.insertBefore(node, this.pills.children[i] ?? this.more); });
    this.shown = pills;
    this.pills.hidden = pills.length === 0;
    if (pills.length) this.root.dataset.busy = ''; else delete this.root.dataset.busy;
    this.primed = true;
    const counting = pills.some((p) => p.counting);
    if (counting && this.ticker === null) this.ticker = setInterval(() => this.tick(), TICK_MS);
    else if (!counting && this.ticker !== null) { clearInterval(this.ticker); this.ticker = null; }
    this.layout();
  }

  private draw(pill: StripPill, now: number): HTMLElement {
    let node = this.nodes.get(pill.id);
    const fresh = !node;
    if (!node) { node = this.build(pill); this.nodes.set(pill.id, node); }
    const before = node.shown;
    node.pill = pill;
    const words = pillWords(pill, now);
    if (node.name.textContent !== pill.name) node.name.textContent = pill.name;
    if (node.words !== words) { node.words = words; node.state.textContent = words; }
    node.state.hidden = !words;
    // One settle when a pill lights after the strip's first look, or when its work turns ready.
    const settle = this.primed && (fresh || (pill.cell === 'ready' && before !== 'ready'));
    if (node.mark) setVoiceMark(node.mark, pill.voice ?? 'idle');
    else if (before !== pill.cell || settle || pill.cell === 'setup') setLitCell(node.cell, pill.cell, { size: 12, decorative: true, fraction: pill.fraction, settle });
    node.shown = pill.cell;
    if (pill.voice) node.root.dataset.voice = pill.voice; else delete node.root.dataset.voice;
    node.root.dataset.cell = pill.cell;
    node.root.dataset.urgency = String(pill.urgency);
    if (fresh && this.primed) node.root.classList.add('strip-pill-rise');
    if (!fresh && !node.mark && before !== pill.cell && this.primed && ['ready', 'needs', 'attention', 'partial'].includes(pill.cell))
      this.live.textContent = `${pill.name}: ${words || LIT_CELL_WORDS[pill.cell]}`;
    if (node.open.title !== pill.title) node.open.title = pill.title;
    node.open.setAttribute('aria-label', words ? `${pill.name}, ${words}` : pill.name);
    if (node.x.title !== pill.hide) { node.x.title = pill.hide; node.x.setAttribute('aria-label', pill.hide); }
    node.x.hidden = !!pill.fixed;
    return node.root;
  }

  private build(pill: StripPill): PillNode {
    const root = document.createElement('span');
    root.className = 'strip-pill';
    root.dataset.id = pill.id;
    const open = document.createElement('button');
    open.type = 'button';
    open.className = 'strip-pill-open';
    const cell = litCell(pill.cell, { size: 12, decorative: true, fraction: pill.fraction });
    // The voice's presence carries its state mark (voice-mark.ts), not a lit cell: it is the voice, not a piece of work.
    const mark = pill.voice ? voiceMark(pill.voice, 8) : null;
    if (mark) root.classList.add('strip-pill-voice');
    const name = document.createElement('b');
    name.className = 'strip-pill-name';
    const state = document.createElement('span');
    state.className = 'strip-pill-state mm-num';
    open.append(mark ?? cell, name, state);
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'strip-pill-x';
    x.append(tabMark('x', 'strip-pill-x-icon'));
    const node: PillNode = { root, open, cell, name, state, x, pill, shown: null, words: '', mark };
    open.addEventListener('click', () => node.pill.open());
    x.addEventListener('click', () => node.pill.dismiss());
    root.addEventListener('animationend', (event) => { if ((event as AnimationEvent).animationName === 'mm-strip-rise') root.classList.remove('strip-pill-rise'); });
    root.append(open, x);
    return node;
  }

  /** Every second while something works: only the clocks change (in place); the row re-fits only when one grows a digit. */
  private tick(): void {
    const now = this.now();
    let grew = false;
    for (const pill of this.shown) {
      const node = this.nodes.get(pill.id);
      if (!node || !pill.counting) continue;
      const words = pillWords(pill, now);
      if (words === node.words) continue;
      grew ||= words.length !== node.words.length;
      node.words = words;
      node.state.textContent = words;
      node.open.setAttribute('aria-label', `${pill.name}, ${words}`);
    }
    if (grew) this.fit();
  }

  /** Where the strip reaches, how tight the meters are, and which pills fit. While the side columns move it waits and
   * lays out once when they land: the motion itself carries the strip's reach under the Assistant (side-motion.ts). */
  private layout(): void {
    if (holdUntilLanded(this.layoutLater)) return;
    this.place();
    if (this.stage) {
      const tight = this.stage.clientWidth > 0 && this.stage.clientWidth < TIGHT_STAGE;
      if (tight) this.root.dataset.tight = ''; else delete this.root.dataset.tight;
    }
    this.fit();
  }

  /** Under the Assistant's column too, to the window's edge, while that column sits beside the Stage (not while a tool's
   * page is docked on the right, which keeps its own height and would cover the strip's meters). Re-placed when the
   * window, the grid or the Stage resizes and when a page docks or undocks on the right. */
  private place(): void {
    if (!this.studio || typeof this.studio.getBoundingClientRect !== 'function') return;
    const width = document.documentElement?.clientWidth || (typeof innerWidth === 'number' ? innerWidth : 0);
    const right = this.studio.getBoundingClientRect().right;
    const under = document.body?.dataset?.controlRightActive === 'true' || !width ? 0 : Math.max(0, Math.round(width - right));
    const value = under ? `-${under}px` : '';
    if (this.root.style.marginRight !== value) this.root.style.marginRight = value;
  }

  /** The passing line gives way first (`data-squeezed`: it steps aside, or one with an Undo ellipsizes to it, its words
   * in its tooltip), so live work never goes compact or folds to make room for a hint; then compact (names alone), then
   * fold the least urgent into +N from the end; the first pill always shows and +N is never cut. */
  private fit(): void {
    const order = this.shown.map((p) => this.nodes.get(p.id)).filter((n): n is PillNode => !!n);
    const over = () => this.pills.scrollWidth > this.pills.clientWidth + 1;
    delete this.root.dataset.compact;
    delete this.root.dataset.squeezed;
    for (const node of order) node.root.hidden = false;
    this.more.hidden = true;
    if (!order.length || !over()) return;
    if (this.hint) {
      this.root.dataset.squeezed = '';
      if (!over()) return;
    }
    this.root.dataset.compact = '';
    if (!over()) return;
    this.more.hidden = false;
    const folded: PillNode[] = [];
    for (let i = order.length - 1; i > 0 && over(); i--) {
      order[i].root.hidden = true;
      folded.unshift(order[i]);
      this.foldWords(folded);
    }
  }

  private foldWords(folded: PillNode[]): void {
    const top = [...folded].sort((a, b) => a.pill.urgency - b.pill.urgency)[0];
    setLitCell(this.moreCell, top.pill.cell, { size: 12, decorative: true, fraction: top.pill.fraction });
    this.moreCount.textContent = `+${folded.length}`;
    const needs = folded.some((n) => n.pill.urgency === 0);
    // Researchers lists only researchers: a folded run, load or step is in Activity.
    this.foldTarget = folded.every((n) => n.pill.id.startsWith('research:')) ? 'researchers' : 'activity';
    const label = `${folded.length} more${needs ? ', one needs you' : ''}. Open ${this.foldTarget === 'researchers' ? 'Researchers' : 'Activity'}`;
    this.moreOpen.setAttribute('aria-label', label);
    this.moreOpen.title = folded.map((n) => n.words ? `${n.pill.name} · ${n.words}` : n.pill.name).join('\n');
  }

  private titleHint(): void {
    if (!this.hint) return;
    const first = this.hint.firstChild;
    const words = (first && first.nodeType === 3 ? first.textContent : this.hint.textContent)?.trim() ?? '';
    if (this.hint.title !== words) this.hint.title = words;
  }
}
