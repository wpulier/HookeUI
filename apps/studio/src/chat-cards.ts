// The chat's researcher cards (the workspace-feel plan, "The chat": ack, card, outcome; the mock's rcard()). When the
// server lists a researcher that a request of this conversation started, a compact card appears under that request
// with its lit cell and its state ("Researcher 5 · Choosing a model", "Working 0:12", "Needs you", "Findings ready")
// and a chevron; a click opens its Tab. Nothing here invents a researcher: a card is drawn only for a session the
// server lists, and only where this conversation's own lines say which request started it.
//
// Which request: the session's `requestId` (the server's request id, S1) is the same as its start step's `requestId`
// on that request's steps; the answer's steps name the researcher too (a `research-run` output, by researcherId); and
// a typed request's `turnId` matches the request's Activity line, which arrives as the request starts, so the card
// shows while its answer is still being written. The three reconcile by researcher: one card per researcher.
import type { LogLine } from './chatmodel';
import type { ResearchRunRef, ResearchSession, ToolRun } from './protocol';
import { litCell, setLitCell, type LitCellState } from './lit-cell';
import { lineIcon } from './chat-scroll';

/** What a card says (shelf.ts researcherCardView builds it, so it says what the researcher's Tab and pill say). */
export interface ResearchCardView {
  cell: LitCellState;
  /** The cell's accessible name. */
  label: string;
  /** Its title: what it was asked, in up to two lines. */
  title: string;
  /** "Researcher 5 · Choosing a model", "Researcher 4 · Needs you", "Researcher 3 · Working 0:12". */
  sub: string;
  /** Its clock ticks (the sub line is redrawn each second while it does). */
  counting: boolean;
  /** The tooltip. */
  tip: string;
}

/** Where the cards come from: the server's list, how each is said, and what a click does (openStudyResearch). */
export interface ResearchCardSource {
  list(): Iterable<ResearchSession>;
  view(s: ResearchSession, now: number): ResearchCardView;
  open(rid: string): void;
}

/** A request's cards: after `after` (a line's key), or, while its answer is being written, after the answer's slot
 * at the end of the log (`after` null). */
export interface CardGroup {
  key: string;
  after: number | null;
  rids: string[];
}

/** The researcher a step's outputs say it started (a `research-run` reference), if any. */
export function researchersMade(action: Pick<ToolRun, 'outputs'>): string[] {
  const out: string[] = [];
  for (const output of action.outputs ?? []) {
    const ref = output?.kind === 'resource' ? output.resource as Partial<Record<keyof ResearchRunRef, unknown>> | null | undefined : null;
    if (ref && ref.kind === 'research-run' && typeof ref.researcherId === 'string' && ref.researcherId) out.push(ref.researcherId);
  }
  return out;
}

const stepsOf = (line: LogLine): ToolRun[] => [...line.actions, ...(line.event?.steps ?? [])];

/**
 * The cards of this conversation, grouped by the request that started each researcher, in the log's order.
 * `lines`: the open conversation's lines (ChatModel). `shown(line)`: whether the line is drawn in the conversation.
 * `scope`: the open Project (its session id, chatmodel's activeDay) and conversation; a researcher of another one never
 * gets a card here. A researcher's Project is its `sitting`; its `day` is the calendar date, used only when it has none.
 */
export function cardGroups(lines: readonly LogLine[], sessions: Iterable<ResearchSession>, scope: { day: string | null; thread: string | null },
  shown: (line: LogLine) => boolean): CardGroup[] {
  // Each request of the log, by its turn when it has one, else by the line its steps are on.
  const keyOf = (line: LogLine) => line.turnId !== undefined ? `turn:${line.turnId}` : `line:${line.key}`;
  const byRequest = new Map<string, string>(), byResearcher = new Map<string, string>();
  const turns = new Map<string, { first: number; last: number | null; lineKey: number }>();
  lines.forEach((line, index) => {
    const key = keyOf(line);
    if (line.turnId !== undefined || stepsOf(line).length) {
      const turn = turns.get(key) ?? { first: index, last: null, lineKey: line.key };
      if (shown(line) && line.role !== 'user') turn.last = line.key; // the words that asked are not its answer
      turns.set(key, turn);
    }
    for (const step of stepsOf(line)) {
      if (step.requestId && !byRequest.has(step.requestId)) byRequest.set(step.requestId, key);
      for (const rid of researchersMade(step)) if (!byResearcher.has(rid)) byResearcher.set(rid, key);
    }
  });
  const groups = new Map<string, ResearchSession[]>();
  for (const s of sessions) {
    if (s.purpose !== 'research') continue; // a description's quick lookup is not a researcher of yours
    const home = s.sitting || s.day;
    if (scope.day && home && home !== scope.day) continue;
    if (scope.thread && s.thread && s.thread !== scope.thread) continue;
    const key = byResearcher.get(s.rid) ?? (s.requestId ? byRequest.get(s.requestId) : undefined)
      ?? (typeof s.turnId === 'number' && turns.has(`turn:${s.turnId}`) ? `turn:${s.turnId}` : undefined);
    if (!key || !turns.has(key)) continue;
    groups.set(key, [...groups.get(key) ?? [], s]);
  }
  const out: CardGroup[] = [];
  for (const [key, members] of groups) {
    const turn = turns.get(key)!;
    // Drawn after its request's last line in the conversation (its answer); with nothing of it drawn yet, after the
    // slot at the end while it is the latest request, else under the words that asked for it.
    const later = lines.slice(turn.first + 1).some((line) => line.role === 'user');
    const asked = lines.slice(0, turn.first + 1).reverse().find((line) => line.role === 'user');
    const after = turn.last ?? (later ? asked?.key ?? turn.lineKey : null);
    out.push({ key, after, rids: members.sort((a, b) => a.n - b.n || a.created - b.created).map((s) => s.rid) });
  }
  const order = new Map(lines.map((line, index) => [line.key, index]));
  return out.sort((a, b) => (a.after === null ? Infinity : order.get(a.after) ?? 0) - (b.after === null ? Infinity : order.get(b.after) ?? 0));
}

/** A line or card that just arrived rises in once (workspace-feel.css `[data-arrived]`); the mark goes when it ends, so
 * moving or redrawing it never replays it. */
export function arrive(el: HTMLElement): void {
  el.dataset.arrived = '';
  const done = () => { delete el.dataset.arrived; el.removeEventListener('animationend', done); el.removeEventListener('animationcancel', done); };
  el.addEventListener('animationend', done);
  el.addEventListener('animationcancel', done);
}

const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; return node;
};

/** The chevron (the mock's lineIcon('right')). */
const chevron = () => lineIcon('M6 3.5 10.5 8 6 12.5', 'chat-rcard-chev');

/** One card, updated in place (its cell is never rebuilt, so its breathing never restarts). */
export class ResearchCard {
  readonly root: HTMLButtonElement;
  private readonly cell: SVGSVGElement;
  private readonly title: HTMLElement;
  private readonly sub: HTMLElement;
  private state: LitCellState | null = null;
  view: ResearchCardView | null = null;

  readonly rid: string;

  constructor(rid: string, open: (rid: string) => void) {
    this.rid = rid;
    this.root = make('button', 'chat-rcard'); this.root.type = 'button'; this.root.dataset.rid = rid;
    this.cell = litCell('working', { size: 13 });
    const words = make('span', 'chat-rcard-words');
    this.title = make('span', 'chat-rcard-title'); this.sub = make('span', 'chat-rcard-sub mm-num');
    words.append(this.title, this.sub);
    this.root.append(this.cell, words, chevron());
    this.root.addEventListener('click', () => open(rid));
  }

  /** `settle`: play the cell's one settle when it lights (a card that just arrived) or turns ready. */
  update(view: ResearchCardView, settle: boolean): void {
    if (settle && this.state === null) arrive(this.root);
    const turnedReady = (view.cell === 'ready' || view.cell === 'partial') && this.state !== null && this.state !== view.cell;
    if (this.state !== view.cell || this.view?.label !== view.label) setLitCell(this.cell, view.cell, { size: 13, label: view.label, settle: settle || turnedReady });
    this.state = view.cell;
    if (this.title.textContent !== view.title) this.title.textContent = view.title;
    if (this.sub.textContent !== view.sub) this.sub.textContent = view.sub;
    if (this.root.title !== view.tip) this.root.title = view.tip;
    const name = `${view.title}: ${view.sub}. Open its Tab`;
    if (this.root.getAttribute?.('aria-label') !== name) this.root.setAttribute('aria-label', name);
    this.root.dataset.state = view.cell;
    this.view = view;
  }
}
