// Sessions (CONTEXT.md "Session"): the project, not the day. A session has a name, its conversations, what was seen
// and its work; it may span days, and a day may hold several. Its title, top left (session-menu.ts), is where it is
// renamed and where sessions are switched: the `sessions` wire, worded here once and drawn by one row renderer.
// Everything but that renderer is pure, so sessions.test.ts checks the words.
//
// A session id (sid) is opaque: "2026-10-12" or "2026-10-12.2". Nothing here reads a date from it; every date said
// comes from the times the server sends (`started`, `lastActive`), in the viewer's local time.
import type { ClientCommand, SessionCounts, SessionRow, SessionState, SessionsList } from './protocol';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const DAY_MS = 86_400_000;

/** Whole calendar days from `at` to `now` (epoch s, local time): 0 today, 1 yesterday; never negative. */
export function daysAgo(at: number, now: number): number {
  const midnight = (s: number) => { const d = new Date(s * 1000); return new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime(); };
  return Math.max(0, Math.round((midnight(now) - midnight(at)) / DAY_MS)); // rounded: a day of 23 or 25 hours is a day
}

/** "Oct 9", with the year when it is not this year's ("Oct 9, 2025"). */
export function monthDay(at: number, now: number): string {
  const d = new Date(at * 1000);
  const words = `${MONTHS[d.getMonth()]} ${d.getDate()}`;
  return d.getFullYear() === new Date(now * 1000).getFullYear() ? words : `${words}, ${d.getFullYear()}`;
}

/** "2:10 pm" (local time). */
export function clockWords(at: number): string {
  const d = new Date(at * 1000);
  const h = d.getHours();
  return `${h % 12 || 12}:${String(d.getMinutes()).padStart(2, '0')} ${h < 12 ? 'am' : 'pm'}`;
}

/** A day separator: "Today", "Yesterday", "Oct 9". */
export function dayLabel(at: number, now: number): string {
  const ago = daysAgo(at, now);
  return ago === 0 ? 'Today' : ago === 1 ? 'Yesterday' : monthDay(at, now);
}

/** When a session began: "since 2:10 pm" today, "since yesterday", "since Oct 11". */
export function sinceWords(started: number, now: number): string {
  const ago = daysAgo(started, now);
  return ago === 0 ? `since ${clockWords(started)}` : ago === 1 ? 'since yesterday' : `since ${monthDay(started, now)}`;
}

/** When a session was last used: "2:10 pm" today, "yesterday 8:12 pm", "Oct 9". */
export function lastUsedWords(at: number, now: number): string {
  const ago = daysAgo(at, now);
  return ago === 0 ? clockWords(at) : ago === 1 ? `yesterday ${clockWords(at)}` : monthDay(at, now);
}

/** An unnamed session of today, until the app names it from its first specimen or the user does. */
export const UNTITLED = 'Untitled project';

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What a session holds, in words: "3 inputs · 5 results · 2 researchers · 3 conversations". A count of nothing is left
 * out, but the conversations always stay (a session always has one), so the line is never empty. Its assets are the
 * recordings and pictures added to it (ADR 0022's sources; the live camera is never counted); an older server sends
 * none. */
export function countWords(counts: SessionCounts | undefined): string[] {
  const c = counts ?? { made: 0, researchers: 0, conversations: 0 };
  const words: string[] = [];
  if ((c.sources ?? 0) > 0) words.push(plural(c.sources!, 'input'));
  if (c.made > 0) words.push(plural(c.made, 'result'));
  if (c.researchers > 0) words.push(plural(c.researchers, 'researcher'));
  words.push(plural(Math.max(0, c.conversations), 'conversation'));
  return words;
}

/** What it holds, short: what it looks at, what was made and who worked ("3 inputs · 5 results · 2 researchers"); with
 * none of them yet, the conversations. */
export function chipSubWords(counts: SessionCounts | undefined): string {
  const words = countWords(counts);
  return (words.length > 1 ? words.slice(0, -1) : words).join(' · ');
}

/** A session's name: the user's or the automatic one (from its first specimen); unnamed, "Untitled session" while it
 * began today, else its date ("Oct 11": a day's session from before sessions were named). */
export function sessionName(row: { name?: string; label?: string; started: number }, now: number): string {
  const name = (row.name ?? '').trim();
  if (name) return name;
  if (row.label?.trim() === 'Untitled session') return UNTITLED; // older servers' generated placeholder; explicit names above stay verbatim
  if (row.label?.trim()) return row.label.trim(); // the server's own words for it (sid.unnamed), said the same everywhere
  return daysAgo(row.started, now) === 0 ? UNTITLED : monthDay(row.started, now);
}

/** A row's stats line: "3 inputs · 5 results · 2 researchers · 3 conversations · since Oct 11". */
export function statsLine(row: { counts?: SessionCounts; started: number }, now: number): string {
  return [...countWords(row.counts), sinceWords(row.started, now)].join(' · ');
}

/** What one row says, for both views (the rail's history and the chip's menu). */
export interface SessionRowView {
  id: string;
  name: string;
  unnamed: boolean;
  stats: string; // the rail's line 2
  lastUsed: string; // "2:10 pm", "yesterday 8:12 pm", "Oct 9"
  counts: string; // "5 results · 2 researchers · 3 conversations"
  working: number; // researchers working in it now
  current: boolean;
  thumb: string | null;
  /** Its whole line, for a tooltip and a screen reader. */
  title: string;
}

export function rowView(row: SessionRow, current: string, now: number): SessionRowView {
  const name = sessionName(row, now);
  const stats = statsLine(row, now);
  const working = Math.max(0, row.working || 0);
  return {
    id: row.id, name, unnamed: !(row.name ?? '').trim(), stats, lastUsed: lastUsedWords(row.lastActive || row.started, now),
    counts: countWords(row.counts).join(' · '), working, current: row.id === current, thumb: row.thumb || null,
    title: [name, stats, working ? `${plural(working, 'researcher')} working` : ''].filter(Boolean).join(' · '),
  };
}

/** How rows are ordered: by when each was last used (the menu's Recent), or by when it was begun (the rail's history,
 * so opening a session never moves it). */
export type SessionOrder = 'lastActive' | 'started';

/** When a row counts as being, for an order. */
function orderTime(row: SessionRow, by: SessionOrder): number {
  return (by === 'lastActive' ? row.lastActive || row.started : row.started) || 0;
}

/** Newest first by `by`. By when begun, two begun in the same second keep one order (by sid), so opening one never
 * swaps them; by when last used, they keep the server's order. */
export function sortSessions(rows: readonly SessionRow[], by: SessionOrder = 'lastActive'): SessionRow[] {
  const tie = (a: SessionRow, b: SessionRow) => (by === 'started' ? (a.id < b.id ? 1 : a.id > b.id ? -1 : 0) : 0);
  return [...rows].sort((a, b) => orderTime(b, by) - orderTime(a, by) || tie(a, b));
}

/** Rows newest first (by when last used, or when begun), under quiet day separators ("Today", "Yesterday", "Oct 9"),
 * each row under the day of the same time it is ordered by. */
export function groupByDay(rows: readonly SessionRow[], now: number, by: SessionOrder = 'lastActive'): { label: string; rows: SessionRow[] }[] {
  const at = (row: SessionRow) => orderTime(row, by);
  const groups: { label: string; rows: SessionRow[] }[] = [];
  for (const row of sortSessions(rows, by)) {
    const label = dayLabel(at(row), now);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.rows.push(row);
    else groups.push({ label, rows: [row] });
  }
  return groups;
}

/** What the header chip says. An older server sends no name: the chip reads "Today · Oct 12" as it did. */
export interface ChipView {
  name: string; // line 1
  since: string; // line 2 on wide screens: "since yesterday"
  sub: string; // after it on line 2: "5 results · 2 researchers"
  line: string; // all of it, for the title and the accessible name
  suggest: boolean; // the app suggests a new session (a dot)
}

export function chipView(state: SessionState | null, now: number): ChipView {
  if (!state) return { name: 'Project', since: '', sub: '', line: 'Project', suggest: false };
  const legacy = state.name === undefined;
  const started = state.started || now;
  const name = legacy ? `Today · ${monthDay(started, now)}` : sessionName({ name: state.name, started }, now);
  const since = legacy ? '' : sinceWords(started, now);
  const sub = state.counts ? chipSubWords(state.counts) : '';
  return { name, since, sub, line: [name, since, sub].filter(Boolean).join(' · '), suggest: !!state.suggest };
}

/** "This session" in the menu: "since 2:10 pm · 2 days · 3 conversations · 2 researchers · 3 inputs · 5 results" (its
 * assets only when it has some). */
export function factsLine(state: Pick<SessionState, 'started' | 'days' | 'counts'>, now: number): string {
  const c = state.counts ?? { made: 0, researchers: 0, conversations: 0 };
  return [sinceWords(state.started || now, now), plural(Math.max(1, state.days?.length ?? 1), 'day'),
    plural(c.conversations, 'conversation'), plural(c.researchers, 'researcher'),
    (c.sources ?? 0) > 0 ? plural(c.sources!, 'input') : '', plural(c.made, 'result')].filter(Boolean).join(' · ');
}

/** The suggestion, in the app's words (the brain and the voice word their own). */
export function suggestWords(suggest: { specimen: string; hours: number }): string {
  const hours = Math.max(1, Math.round(suggest.hours));
  const what = suggest.specimen ? ` (${suggest.specimen})` : '';
  return `This looks like a new specimen${what}, ${plural(hours, 'hour')} after this project was last used.`;
}

// --- one source for both views ------------------------------------------------------------------

export const SWITCH_WAIT_MS = 15_000; // a switch the server never echoes is given up, and said
export const READ_FAILED = 'Projects could not be read just now.';

type Pending = { kind: 'switch'; id: string; from: string; at: number } | { kind: 'new'; from: string; at: number };

/** The sessions as the server says them, and what this studio asked for and is waiting on. Both views read it and
 * redraw on its change; neither keeps sessions of its own. The server's echo (`session`, then `sessions`) is what
 * moves anything: a click only sends the command and says "Opening…". */
export class SessionsModel {
  state: SessionState | null = null;
  list: SessionsList | null = null;
  /** Why the list could not be read or a switch did not happen, in plain words ("" when nothing is wrong). */
  error = '';
  private pending: Pending | null = null;
  /** The session this studio just started, until a view takes it (the rail focuses its name for a rename). */
  private created: string | null = null;
  private readonly renamed = new Map<string, string>(); // names sent, shown until the server's list has them
  private fetched = false;
  private readonly listeners = new Set<() => void>();
  private readonly send: (command: ClientCommand) => boolean;
  private readonly clock: () => number;

  /** `send` is the connection's (false when offline); `clock` in ms. */
  constructor(send: (command: ClientCommand) => boolean, clock: () => number = () => Date.now()) {
    this.send = send;
    this.clock = clock;
  }

  /** Seconds, for the words. */
  now(): number {
    return this.clock() / 1000;
  }

  onChange(listener: () => void): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  private changed(): void {
    for (const listener of [...this.listeners]) listener();
  }

  /** The session open now (its sid). */
  get current(): string {
    return this.state?.id ?? this.list?.current ?? '';
  }

  /** The server's `session`: the one open now. It ends a switch to it, or a new one (any sid but the one left). */
  applySession(state: SessionState): void {
    const pending = this.pending;
    if (pending && (pending.kind === 'switch' ? state.id === pending.id : state.id !== pending.from)) {
      if (pending.kind === 'new') this.created = state.id;
      this.pending = null;
      this.error = '';
    }
    if (state.name !== undefined && this.renamed.get(state.id) === state.name) this.renamed.delete(state.id);
    this.state = state;
    this.changed();
  }

  /** The server's `sessions` list (or GET /api/sessions). */
  applyList(list: SessionsList): void {
    this.list = list;
    this.fetched = true;
    if (this.error === READ_FAILED) this.error = '';
    for (const row of list.sessions) if (this.renamed.get(row.id) === row.name) this.renamed.delete(row.id);
    this.changed();
  }

  /** Read the list once when a view opens and no `sessions` message came yet; says so when it can't. */
  async load(fetcher: typeof fetch = fetch): Promise<void> {
    if (this.list || this.fetched) return;
    this.fetched = true;
    try {
      const response = await fetcher('/api/sessions', { cache: 'no-store' });
      if (!response.ok) throw new Error(`sessions ${response.status}`);
      const body = await response.json() as { current: string; sessions: SessionRow[] };
      if (!this.list) this.applyList({ type: 'sessions', current: body.current, sessions: body.sessions ?? [] });
    } catch {
      if (this.list) return;
      this.fetched = false; // the next opening tries again
      this.error = READ_FAILED;
      this.changed();
    }
  }

  /** Every session, newest first by when it was last used (the menu's Recent) or by when it was begun (`'started'`:
   * the rail, where opening one never moves it); the open one as the server says it now (its name and counts), there
   * even before the list names it. */
  rows(order: SessionOrder = 'lastActive'): SessionRow[] {
    const rows = (this.list?.sessions ?? []).map((row) => ({ ...row }));
    const state = this.state;
    if (state) {
      let row = rows.find((r) => r.id === state.id);
      if (!row) {
        row = { id: state.id, name: state.name ?? '', named: !!state.named, started: state.started, lastActive: state.lastActive ?? state.started,
          days: state.days ?? [], counts: state.counts ?? { made: 0, researchers: 0, conversations: 1 }, specimens: state.specimen ? [state.specimen] : [], working: 0 };
        rows.push(row);
      } else {
        if (state.name !== undefined) row.name = state.name;
        if (state.named !== undefined) row.named = state.named;
        if (state.started) row.started = state.started; // the chip and the rail say the same "since"
        if (state.counts) row.counts = state.counts;
        if (state.lastActive) row.lastActive = Math.max(row.lastActive, state.lastActive);
        if (state.days) row.days = state.days;
      }
    }
    for (const row of rows) {
      const name = this.renamed.get(row.id);
      if (name !== undefined) row.name = name;
    }
    return sortSessions(rows, order);
  }

  /** The open session as it is said: the server's `session`, with a name the user just gave it (until the echo). */
  shown(): SessionState | null {
    const state = this.state;
    if (!state || state.name === undefined) return state;
    const name = this.renamed.get(state.id);
    return name === undefined ? state : { ...state, name };
  }

  /** The open session's row (its name as the user just typed it, too). */
  currentRow(): SessionRow | undefined {
    return this.rows().find((row) => row.id === this.current);
  }

  /** Go back to a session: sent, then "Opening…" until the server's echo. False when offline (nothing pending). */
  switchTo(id: string): boolean {
    if (!id || id === this.current) return false;
    if (!this.send({ type: 'session', action: 'switch', id })) { this.error = 'Reconnect to the studio, then try again.'; this.changed(); return false; }
    this.pending = { kind: 'switch', id, from: this.current, at: this.clock() };
    this.error = '';
    this.changed();
    return true;
  }

  /** Start a new session (only ever on the user's request). */
  create(): boolean {
    if (!this.send({ type: 'session', action: 'new' })) { this.error = 'Reconnect to the studio, then try again.'; this.changed(); return false; }
    this.pending = { kind: 'new', from: this.current, at: this.clock() };
    this.error = '';
    this.changed();
    return true;
  }

  /** Name a session: shown at once, kept once the server's list has it. A blank or unchanged name sends nothing. */
  rename(id: string, name: string): boolean {
    const clean = name.trim().slice(0, 120);
    const row = this.rows().find((r) => r.id === id);
    if (!clean || !row || clean === row.name.trim()) return false;
    if (!this.send({ type: 'session', action: 'rename', id, name: clean })) { this.error = 'Reconnect to the studio, then try again.'; this.changed(); return false; }
    this.renamed.set(id, clean);
    this.changed();
    return true;
  }

  /** Keep working here: the app's suggestion is dismissed. */
  keep(): boolean {
    return this.send({ type: 'session', action: 'keep' });
  }

  /** A switch to `id` (or, with no id, any switch or new session) is waiting for the server's echo. */
  opening(id?: string): boolean {
    const p = this.pending;
    return !!p && (id === undefined || (p.kind === 'switch' && p.id === id));
  }

  /** A new session is being started. */
  creating(): boolean {
    return this.pending?.kind === 'new';
  }

  /** The session this studio just started, once: a view takes it to focus its name. */
  takeCreated(): string | null {
    const id = this.created;
    this.created = null;
    return id;
  }

  /** A switch the server never echoed is given up after SWITCH_WAIT_MS, and said; true when it was. */
  expire(): boolean {
    if (!this.pending || this.clock() - this.pending.at < SWITCH_WAIT_MS) return false;
    this.error = this.pending.kind === 'new' ? 'The new project did not start. Try again.' : 'That project did not open. Try again.';
    this.pending = null;
    this.changed();
    return true;
  }
}

// --- one row renderer, for the rail and the menu -------------------------------------------------

export interface RowActions {
  open: (id: string) => void;
  /** Rename this row (the ✎, or a double-click on its name); absent: no rename here. */
  rename?: (id: string) => void;
}

/** One session row. The rail shows a status dot, the name and its stats line; the menu adds its picture and a
 * "working" pill, and says when it was last used. The open session is marked `aria-current`; a session being
 * opened says "Opening…" until the server's echo. */
export function renderSessionRow(view: SessionRowView, variant: 'rail' | 'menu', actions: RowActions, opening = false): HTMLElement {
  const row = document.createElement('li');
  row.className = 'session-row';
  row.dataset.sid = view.id;
  if (view.current) row.setAttribute('aria-current', 'true');
  if (view.working) row.dataset.working = '';
  if (opening) row.dataset.opening = '';
  const open = document.createElement('button');
  open.type = 'button';
  open.className = 'session-row-open';
  open.dataset.sid = view.id;
  if (view.current) open.setAttribute('aria-current', 'true');
  open.title = view.current ? `${view.title} (open now)` : view.title;
  open.setAttribute('aria-label', opening ? `${view.name}: opening` : view.current ? `${view.title}, open now` : `Open ${view.title}`);
  const dot = document.createElement('span');
  dot.className = 'session-row-dot';
  dot.setAttribute('aria-hidden', 'true');
  open.append(dot);
  if (variant === 'menu') {
    // A picture of what it last looked at (one of its assets), else the camera's icon: it only looked live.
    const frame = document.createElement('span');
    frame.className = 'session-row-thumb';
    frame.setAttribute('aria-hidden', 'true');
    const placeholder = () => {
      const svg = document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg');
      svg.setAttribute('viewBox', '0 0 16 16');
      const use = document.createElementNS?.('http://www.w3.org/2000/svg', 'use') ?? document.createElement('use');
      use.setAttribute('href', '#i-camera');
      svg.append(use);
      frame.replaceChildren(svg);
      frame.dataset.icon = 'camera';
    };
    if (view.thumb) {
      const img = document.createElement('img');
      img.alt = '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.src = view.thumb;
      img.addEventListener('error', placeholder, { once: true });
      frame.append(img);
    } else placeholder();
    open.append(frame);
  }
  const words = document.createElement('span');
  words.className = 'session-row-words';
  const name = document.createElement('span');
  name.className = 'session-row-name';
  name.textContent = view.name;
  if (view.unnamed) name.dataset.unnamed = '';
  const stats = document.createElement('span');
  stats.className = 'session-row-stats';
  stats.textContent = opening ? 'Opening…' : variant === 'menu' ? [`last used ${view.lastUsed}`, view.counts].join(' · ') : view.stats;
  words.append(name, stats);
  open.append(words);
  if (variant === 'menu' && view.working) {
    const pill = document.createElement('span');
    pill.className = 'session-row-pill';
    pill.textContent = 'working';
    pill.title = `${plural(view.working, 'researcher')} working in it now`;
    open.append(pill);
  }
  open.addEventListener('click', () => { if (!view.current) actions.open(view.id); });
  row.append(open);
  if (actions.rename) {
    const rename = actions.rename;
    const pencil = document.createElement('button');
    pencil.type = 'button';
    pencil.className = 'session-row-rename';
    pencil.textContent = '✎';
    pencil.title = 'Rename';
    pencil.setAttribute('aria-label', `Rename ${view.name}`);
    pencil.addEventListener('click', (event) => { event.stopPropagation(); rename(view.id); });
    name.addEventListener('dblclick', (event) => { event.preventDefault(); event.stopPropagation(); rename(view.id); });
    row.append(pencil);
  }
  return row;
}

/** An inline rename in place of `name` (a row's name, or the menu's "This session"): Enter saves, Escape cancels
 * (and is the input's own: nothing else closes), leaving the field saves a changed name. `done` gets the new name, or
 * null when cancelled; it runs once. */
export function inlineRename(name: HTMLElement, value: string, label: string, done: (name: string | null) => void): HTMLInputElement {
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'session-rename';
  input.value = value;
  input.maxLength = 120;
  input.setAttribute('aria-label', label);
  input.autocomplete = 'off';
  input.spellcheck = false;
  let settled = false;
  const finish = (result: string | null) => {
    if (settled) return;
    settled = true;
    done(result);
  };
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); finish(input.value); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(null); }
  });
  input.addEventListener('blur', () => {
    // Looked at once things settle: a view that redraws puts the same field back, focused, and nothing ends.
    setTimeout(() => {
      if (!input.isConnected || document.activeElement === input) return;
      finish(input.value.trim() && input.value.trim() !== value.trim() ? input.value : null);
    }, 0);
  });
  name.replaceWith(input);
  return input;
}
