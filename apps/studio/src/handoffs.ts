// Work in flight while you keep talking (Will, 29 Oct: "if its a parallel process and you keep talking, we have to have
// some hyper clear way to communicate thats whats happening"). The voice hands a request to Hooke's brain (`do_in_hooke`;
// services/orchestrator/voice/handoffs.py) and goes on talking; each hand-off is:
//   - one row in the chat, under the voice line that started it: its lit cell breathing and "Hooke is working · 0:07"
//     under its title (the voice's few plain words for it, else the request's own), then "Still working" past the
//     voice's wait; it settles into "Done · <the brain's answer>", "Started · <it>" when what it did was start a
//     researcher (whose work goes on, on its card), "Couldn't confirm · <what was asked>, it may not have happened" when
//     a change was sent and never seen on screen (never "Didn't work", which is kept for a real failure and its reason),
//     "Didn't work · <why>", or "Not known · <why>" when no result was recorded. A tap opens what was asked and the steps the brain took (Activity's words for them), and
//     Activity at its request;
//   - an entry in the slim strip over the composer while it runs, beside the researchers at work in this Project
//     ("2 working · Reference photo of a stoma · A new Tab"); each entry jumps to its row or card. The strip goes when
//     nothing runs, so what is going on is in sight even scrolled away;
//   - a step the user asked for after the work it starts (`then`; voice/followups.py) on a line of its own on the row
//     ("Then, after Researcher 2: put the picture in the Stoma Reference tab") and in the strip after that researcher
//     while it waits ("Researcher 2 · then: put the picture…"); done, it is a hand-off row of its own ("After
//     Researcher 2"), and the line says how it went.
// Nothing here decides an outcome: each state is the server's, from the brain's own turn (ADR 0006); the one thing the
// studio settles is a step the brain left running after its answer, from that step's own receipt.
import { actionName, actionSummary, capital } from './activity';
import { arrive } from './chat-cards';
import { lineIcon } from './chat-scroll';
import { litCell, setLitCell, type LitCellState } from './lit-cell';
import { renderMarkdown } from './markdown';
import type { HandoffLine, ToolRun, VoiceStateMessage } from './protocol';
import { clock, type StripPill } from './working-strip';

export type HandoffState = HandoffLine['state'];

/** Its state now: the server's, except a row the brain answered while one of its steps still ran (`settling`), which
 * settles from that step's own receipt; one a replay drew with such a step still open had no result recorded here. */
export function handoffState(h: HandoffLine, replayed = false): HandoffState {
  if (h.state !== 'working' || !h.settling) return h.state;
  const steps = h.steps ?? [];
  if (steps.some((step) => step.ok === false)) return 'failed';
  if (steps.every((step) => step.ok !== null)) return 'done';
  return replayed ? 'unknown' : 'working';
}

/** An answer as one plain line: its markdown marks dropped (the opened row draws it formatted). */
export function plainWords(text: string | undefined): string {
  return (text ?? '').replace(/!\[[^\]]*\]\([^)]*\)/g, '').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/`([^`]*)`/g, '$1')
    .replace(/(\*\*|__)(.+?)\1/g, '$2').replace(/^\s{0,3}(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s?)/gm, '')
    .replace(/\s+/g, ' ').trim();
}

export interface HandoffView {
  state: HandoffState;
  cell: LitCellState;
  /** Its title: what was asked, in a few plain words. */
  title: string;
  /** Its state in a word or two ("Hooke is working", "Done", "Didn't work"), then what it found or why, if anything. */
  status: string;
  detail: string;
  /** Its later step, or the work it came after ("Then, after Researcher 2: put the picture in…", "After Researcher 2"):
   * `noteLead` in the row's quiet voice, `note` its words; empty, none. */
  noteLead: string;
  note: string;
  noteState: 'quiet' | 'done' | 'failed';
  /** Its clock counts (the detail is redrawn each second while it does). */
  counting: boolean;
  /** The tooltip, and the cell's accessible name. */
  tip: string;
  label: string;
}

const CELL: Record<HandoffState, LitCellState> = { working: 'working', done: 'ready', started: 'partial', uncertain: 'idle',
  failed: 'attention', unknown: 'stopped' };

/** A title as words inside a sentence: "A new Tab" -> "a new Tab" (a name such as "Researcher 2" kept). */
function inSentence(title: string): string {
  return /^[A-Z](?:[a-z]*)(?: |$)/.test(title) && !/^[A-Z][a-z]* \d/.test(title) ? title.charAt(0).toLowerCase() + title.slice(1) : title;
}

/** What a row says now (`now`: epoch seconds). */
export function handoffView(h: HandoffLine, now: number, replayed = false): HandoffView {
  const state = handoffState(h, replayed);
  const title = h.label?.trim() || plainWords(h.request) || 'Your request';
  const steps = h.steps ?? [];
  const since = Number.isFinite(h.startedAt) ? h.startedAt : now;
  const took = Math.max(0, (state !== 'working' && h.endedAt ? h.endedAt : now) - since);
  let status: string, detail: string;
  if (state === 'working') {
    const waiting = h.settling ? steps.find((step) => step.ok === null) : undefined;
    status = h.long || h.settling ? 'Still working' : 'Hooke is working';
    detail = [waiting ? actionName(waiting.tool) : '', clock(took * 1000)].filter(Boolean).join(' · ');
  } else if (state === 'done') {
    const last = [...steps].reverse().find((step) => step.ok === true && step.summary);
    status = 'Done';
    detail = plainWords(h.answer) || (last ? actionSummary(last) : '');
  } else if (state === 'started') {
    status = 'Started';
    detail = plainWords(h.answer);
  } else if (state === 'uncertain') {
    status = 'Couldn’t confirm';
    detail = `${inSentence(title)}, it may not have happened`;
  } else if (state === 'failed') {
    const failed = steps.find((step) => step.ok === false && step.summary);
    status = 'Didn’t work';
    detail = capital(plainWords(h.reason) || (failed ? actionSummary(failed) : '') || plainWords(h.answer));
  } else {
    status = 'Not known';
    detail = capital(plainWords(h.reason)) || 'No result was recorded';
  }
  const words = detail && state !== 'working' ? `${status}: ${detail}` : status;
  const time = took >= 1 ? ` · ${took < 60 ? `${Math.round(took)} s` : clock(took * 1000)}` : '';
  const [noteLead, note, noteState] = thenWords(h);
  return { state, cell: CELL[state], title, status, detail, noteLead, note, noteState, counting: state === 'working',
    label: `Hooke: ${status}`, tip: `${title}\n${words}${state === 'working' ? '' : time}${note ? `\n${noteLead} ${note}` : ''}` };
}

/** Its later step's line as it stands, or the work it came after. */
function thenWords(h: HandoffLine): [string, string, 'quiet' | 'done' | 'failed'] {
  const then = h.then;
  if (!then?.words) return h.after ? [`After ${h.after}`, '', 'quiet'] : ['', '', 'quiet'];
  const words = then.words;
  switch (then.state) {
    case 'waiting': return [then.after ? `Then, after ${then.after}:` : 'Then:', words, 'quiet'];
    case 'running': return ['Doing now:', words, 'quiet'];
    case 'done': return ['Then, done:', words, 'done'];
    case 'failed': return ['Then, didn’t work:', words, 'failed'];
    case 'dropped': return ['Not done:', `${words}${then.reason ? ` (${plainWords(then.reason).replace(/^Not done:\s*/, '')})` : ''}`, 'failed'];
    default: return ['Then:', words, 'quiet'];
  }
}

const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; return node;
};
const chevron = () => lineIcon('M4 6.5 8 10.5 12 6.5', 'chat-handoff-chev');

export interface HandoffRowHooks {
  /** Opens Activity at the brain's request (its turn); none, no link. */
  activity?(turnId: number): void;
}

/** One hand-off's row in the log, updated in place: its cell is never rebuilt (its breathing never restarts), and a
 * row opened stays open through its later states. */
export class HandoffRow {
  readonly root: HTMLLIElement;
  readonly id: string;
  readonly head: HTMLButtonElement;
  private readonly cell: SVGSVGElement;
  private readonly titleEl: HTMLElement;
  private readonly statusEl: HTMLElement;
  private readonly detailEl: HTMLElement;
  private readonly noteEl: HTMLElement;
  private readonly noteLeadEl: HTMLElement;
  private readonly noteWordsEl: HTMLElement;
  private readonly body: HTMLElement;
  private readonly asked: HTMLElement;
  private readonly answer: HTMLElement;
  private readonly steps: HTMLOListElement;
  private readonly link: HTMLButtonElement;
  private readonly hooks: HandoffRowHooks;
  private opened = false;
  private stamp = '';
  private turnId: number | undefined;
  view: HandoffView | null = null;
  line: HandoffLine | null = null;

  constructor(id: string, hooks: HandoffRowHooks = {}) {
    this.id = id;
    this.hooks = hooks;
    this.root = make('li', 'chat-handoff');
    this.root.dataset.role = 'handoff';
    this.root.dataset.handoff = id;
    this.head = make('button', 'chat-handoff-head'); this.head.type = 'button';
    this.head.setAttribute('aria-expanded', 'false');
    this.cell = litCell('working', { size: 12 });
    const words = make('span', 'chat-handoff-words');
    this.titleEl = make('span', 'chat-handoff-title');
    const sub = make('span', 'chat-handoff-sub');
    this.statusEl = make('span', 'chat-handoff-status');
    this.detailEl = make('span', 'chat-handoff-detail mm-num');
    sub.append(this.statusEl, this.detailEl);
    this.noteEl = make('span', 'chat-handoff-then');
    this.noteLeadEl = make('span', 'chat-handoff-then-lead');
    this.noteWordsEl = make('span', 'chat-handoff-then-words');
    this.noteEl.append(this.noteLeadEl, this.noteWordsEl);
    this.noteEl.hidden = true;
    words.append(this.titleEl, sub, this.noteEl);
    this.head.append(this.cell, words, chevron());
    this.body = make('div', 'chat-handoff-body'); this.body.hidden = true;
    this.body.id = `handoff-body-${id.replace(/[^A-Za-z0-9_-]/g, '-')}`;
    this.head.setAttribute('aria-controls', this.body.id);
    this.asked = make('p', 'chat-handoff-asked');
    this.answer = make('div', 'chat-handoff-answer');
    this.steps = make('ol', 'chat-handoff-steps');
    this.link = make('button', 'chat-handoff-activity'); this.link.type = 'button'; this.link.textContent = 'Open in Activity';
    this.link.hidden = true;
    this.link.addEventListener('click', () => { if (this.turnId !== undefined) this.hooks.activity?.(this.turnId); });
    this.body.append(this.asked, this.answer, this.steps, this.link);
    this.root.append(this.head, this.body);
    this.head.addEventListener('click', () => this.toggle());
  }

  get open(): boolean { return this.opened; }

  /** Open or close its steps. */
  toggle(open = !this.opened): void {
    this.opened = open;
    this.body.hidden = !open;
    this.head.setAttribute('aria-expanded', String(open));
    if (open) this.root.dataset.open = ''; else delete this.root.dataset.open;
  }

  /** `settle`: a row that just arrived rises in once; a cell that just turned done plays its one settle. */
  update(line: HandoffLine, view: HandoffView, settle: boolean): void {
    if (settle && this.view === null) arrive(this.root);
    const turned = this.view !== null && this.view.state !== view.state;
    if (this.view?.cell !== view.cell || this.view?.label !== view.label)
      setLitCell(this.cell, view.cell, { size: 12, label: view.label, settle: settle || turned });
    if (this.titleEl.textContent !== view.title) this.titleEl.textContent = view.title;
    if (this.statusEl.textContent !== view.status) this.statusEl.textContent = view.status;
    if (this.detailEl.textContent !== view.detail) this.detailEl.textContent = view.detail;
    this.detailEl.hidden = !view.detail;
    if (this.noteLeadEl.textContent !== view.noteLead) this.noteLeadEl.textContent = view.noteLead;
    if (this.noteWordsEl.textContent !== view.note) this.noteWordsEl.textContent = view.note;
    this.noteEl.hidden = !view.noteLead;
    this.noteEl.dataset.state = view.noteState;
    if (this.head.title !== view.tip) this.head.title = view.tip;
    const name = `${view.title}: ${view.status}${view.detail && !view.counting ? `, ${view.detail}` : ''}`
      + `${view.noteLead ? `. ${view.noteLead} ${view.note}` : ''}. ${this.opened ? 'Hide' : 'Show'} its steps`;
    if (this.head.getAttribute('aria-label') !== name) this.head.setAttribute('aria-label', name);
    this.root.dataset.state = view.state;
    if (line.long) this.root.dataset.long = ''; else delete this.root.dataset.long;
    this.view = view;
    this.line = line;
    this.turnId = line.turnId;
    this.link.hidden = line.turnId === undefined || !this.hooks.activity;
    const stamp = JSON.stringify([line.request, line.answer, line.steps, view.state]);
    if (stamp !== this.stamp) { this.stamp = stamp; this.drawBody(line, view); }
  }

  /** What was asked, the brain's whole answer, and its steps, each in Activity's own words. */
  private drawBody(line: HandoffLine, view: HandoffView): void {
    const request = plainWords(line.request);
    this.asked.textContent = request ? `Asked: ${request}` : '';
    this.asked.hidden = !request;
    this.answer.replaceChildren();
    const answer = (line.answer ?? '').trim();
    // The answer is the row's outcome already; opened, it is drawn whole and formatted when there is more of it.
    this.answer.hidden = !answer || plainWords(answer) === view.detail && answer.length <= 140;
    if (!this.answer.hidden) renderMarkdown(this.answer, answer);
    this.steps.replaceChildren(...(line.steps ?? []).map(stepItem));
    this.steps.hidden = !(line.steps ?? []).length;
  }
}

function stepItem(step: ToolRun): HTMLLIElement {
  const item = make('li');
  item.dataset.state = step.ok === null ? 'running' : step.ok ? 'done' : 'failed';
  const dot = make('span', 'chat-handoff-dot'); dot.setAttribute('aria-hidden', 'true');
  const name = make('span', 'chat-handoff-step'); name.textContent = actionName(step.tool);
  const what = make('span', 'chat-handoff-step-what'); what.textContent = step.summary ? actionSummary(step) : '';
  const state = step.ok === null ? 'still running' : step.ok ? 'done' : 'did not work';
  item.setAttribute('aria-label', `${name.textContent}: ${what.textContent || state}`);
  item.append(dot, name, what);
  return item;
}

// --- the strip over the composer -----------------------------------------------------------------------------------

/** One piece of work in flight: a hand-off (its row) or a researcher at work (its card, else its Tab). */
export interface FlightEntry {
  key: string;
  name: string;
  /** When it began (epoch seconds): the strip lists the oldest first, so an entry keeps its place. */
  since: number;
  /** What it is doing, for its tooltip ("Hooke is working", "Researcher 5 · Choosing a model"). */
  doing: string;
  /** A step waiting on other work (a hand-off's `then`): listed right after that work's entry (`follows`, its key),
   * quieter, and not counted as working. */
  waiting?: boolean;
  follows?: string;
  go(): void;
}

/** The hand-offs of a log that are in flight now, as the strip lists them. */
export function handoffsInFlight(rows: readonly { line: HandoffLine; replayed?: boolean }[], go: (id: string) => void): FlightEntry[] {
  const out: FlightEntry[] = [];
  for (const { line, replayed } of rows) {
    if (handoffState(line, !!replayed) !== 'working') continue;
    const view = handoffView(line, line.startedAt, !!replayed);
    out.push({ key: `handoff:${line.id}`, name: view.title, since: line.startedAt, doing: view.status, go: () => go(line.id) });
  }
  for (const { line } of rows) {
    const then = line.then;
    if (!then?.words || then.state !== 'waiting') continue;
    out.push({ key: `then:${line.id}`, name: `then: ${then.words}`, since: line.startedAt, waiting: true,
      follows: then.rid ? `researcher:${then.rid}` : undefined, doing: then.after ? `Waiting for ${then.after}` : 'Waiting',
      go: () => go(line.id) });
  }
  return out;
}

const SHORT_ENTRY = 18;

/** "Working", "2 working"; only steps waiting on other work: "Waiting". */
export function flightWords(count: number): string {
  return count === 0 ? 'Waiting' : count === 1 ? 'Working' : `${count} working`;
}

/** The strip's order: the oldest first, each waiting step right after the work it waits on. */
export function flightOrder(entries: readonly FlightEntry[]): FlightEntry[] {
  const byAge = [...entries].sort((a, b) => a.since - b.since || (a.key < b.key ? -1 : a.key > b.key ? 1 : 0));
  const placed = byAge.filter((entry) => !entry.follows || !byAge.some((other) => other.key === entry.follows));
  for (const entry of byAge) {
    if (!entry.follows || placed.includes(entry)) continue;
    const at = placed.findIndex((other) => other.key === entry.follows);
    let index = at + 1;
    while (index < placed.length && placed[index].follows === entry.follows) index++;
    placed.splice(index, 0, entry);
  }
  return placed;
}

/** One in-progress list: every source's entries, one per key, the first source to name a key keeping it (the hand-off
 * rows and the researchers before the server's own list, `VOICE_SOURCE`, which only adds what they do not show: a think
 * job), in the strip's order. */
export function flightEntries(sources: ReadonlyMap<string, readonly FlightEntry[]>): FlightEntry[] {
  const seen = new Set<string>(), out: FlightEntry[] = [];
  const ordered = [...sources.entries()].sort(([a], [b]) => Number(a === VOICE_SOURCE) - Number(b === VOICE_SOURCE));
  for (const [, entries] of ordered) for (const entry of entries) {
    if (seen.has(entry.key)) continue;
    seen.add(entry.key);
    out.push(entry);
  }
  return flightOrder(out);
}

/** The source the server's own list of Hooke's work in flight is set under (voice-state `working`). */
export const VOICE_SOURCE = 'voice';

/** The server's list of Hooke's work in flight (voice/presence.py), as entries: a hand-off (`handoff:<id>`, the same key
 * as its row's) or a think job (`job:<id>`). `go` shows it: its row, else Activity. */
export function voiceEntries(message: VoiceStateMessage | null, go: (key: string) => void): FlightEntry[] {
  if (!message || message.state === 'off') return [];
  return (message.working ?? []).filter((w) => w && typeof w.key === 'string').map((w) => ({
    key: w.key, name: (w.label || 'Hooke’s work').trim(), since: Number.isFinite(w.since) ? w.since : message.at,
    doing: 'Hooke is working', go: () => go(w.key) }));
}

/** The Working strip's pills for Hooke's own work in flight (hand-offs and think jobs; researchers have the shelf's own
 * pills), from the same list as the strip over the composer, so the strip counts what that strip lists. Each counts its
 * time; a click shows it in the Assistant; × hides the pill only. */
export function flightPills(entries: readonly FlightEntry[], hidden: ReadonlySet<string>, open: (entry: FlightEntry) => void,
  dismiss: (key: string) => void): StripPill[] {
  return entries.filter((e) => !e.waiting && /^(?:handoff|job):/.test(e.key) && !hidden.has(e.key)).map((entry) => ({
    id: entry.key, name: entry.name, cell: 'working', urgency: 1, since: entry.since * 1000, counting: true,
    title: `${entry.name}: ${entry.doing}.\nShow it in the Assistant`, hide: `Hide ${entry.name} from the strip. It keeps working.`,
    open: () => open(entry), dismiss: () => dismiss(entry.key),
  }));
}

/** The slim strip over the composer: shown only while something runs, listing it ("2 working · Reference photo of a
 * stoma · A new Tab"); each entry jumps to where that work shows. Entries are drawn in place, never rebuilt. */
export class FlightStrip {
  readonly root: HTMLElement;
  private readonly count: HTMLElement;
  private readonly list: HTMLElement;
  private readonly sources = new Map<string, FlightEntry[]>();
  private readonly buttons = new Map<string, HTMLButtonElement>();
  private readonly current = new Map<string, FlightEntry>();
  private shown: FlightEntry[] = [];
  /** Told after each change of what it lists (the voice's state counts it; the Working strip lists the same). */
  onChange: (entries: readonly FlightEntry[]) => void = () => {};

  constructor() {
    this.root = make('div', 'chat-flight');
    this.root.setAttribute('role', 'region');
    this.root.setAttribute('aria-label', 'Working now');
    this.root.hidden = true;
    const cell = litCell('working', { size: 10, decorative: true });
    this.count = make('b', 'chat-flight-count');
    this.count.setAttribute('aria-live', 'polite');
    this.list = make('span', 'chat-flight-list');
    this.root.append(cell, this.count, this.list);
  }

  /** The entries one source has in flight now (hand-offs, researchers), replacing that source's last ones. */
  set(source: string, entries: FlightEntry[]): void {
    this.sources.set(source, entries);
    this.render();
  }

  /** What the strip lists now, in its order. */
  get entries(): readonly FlightEntry[] { return this.shown; }

  /** How many pieces of work it lists as working (a later step waiting on other work is not counted). */
  get working(): number { return this.shown.filter((entry) => !entry.waiting).length; }

  private render(): void {
    const all = flightEntries(this.sources);
    const before = this.shown.map((e) => `${e.key}|${e.name}|${e.waiting ? 1 : 0}`).join('\n');
    this.shown = all;
    if (all.map((e) => `${e.key}|${e.name}|${e.waiting ? 1 : 0}`).join('\n') !== before) {
      try { this.onChange(all); } catch { /* a listener's fault never stops the strip */ }
    }
    const was = !this.root.hidden;
    this.root.hidden = all.length === 0;
    this.current.clear();
    for (const entry of all) this.current.set(entry.key, entry);
    if (!all.length) { this.list.replaceChildren(); this.buttons.clear(); return; }
    if (!was) arrive(this.root);
    const words = flightWords(all.filter((entry) => !entry.waiting).length);
    if (this.count.textContent !== words) this.count.textContent = words;
    const keep = new Set(all.map((entry) => entry.key));
    for (const [key, button] of this.buttons) if (!keep.has(key)) { button.remove(); this.buttons.delete(key); }
    const wanted = all.map((entry) => {
      let button = this.buttons.get(entry.key);
      if (!button) {
        button = make('button', 'chat-flight-item'); button.type = 'button';
        const key = entry.key;
        button.addEventListener('click', () => this.current.get(key)?.go());
        this.buttons.set(entry.key, button);
      }
      if (entry.waiting) button.dataset.waiting = ''; else delete button.dataset.waiting;
      // A short entry ("Researcher 2", "A new Tab") keeps its words whole; a long one gives way first.
      if (entry.name.length <= SHORT_ENTRY) button.dataset.short = ''; else delete button.dataset.short;
      if (button.textContent !== entry.name) button.textContent = entry.name;
      const tip = `${entry.name}: ${entry.doing}. Show it`;
      if (button.title !== tip) { button.title = tip; button.setAttribute('aria-label', tip); }
      return button;
    });
    const have = Array.from(this.list.children);
    if (have.length !== wanted.length || have.some((node, i) => node !== wanted[i])) this.list.replaceChildren(...wanted);
  }
}
