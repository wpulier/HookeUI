// The Researchers drawer's list (workspace-feel plan, step 4; the mock's researchersDrawer() and researcherRow()): this
// Project's researchers at every count, grouped by what they need from you (Needs you, Working, Done), then its Studies,
// then researchers still working in other Projects. One 56 px row each: its cover picture with its lit cell on the
// corner, its title in up to two lines, then "Researcher N · what it is doing" with its live clock. Engine and how long
// since its last step are in the row's tooltip; its ⋯ holds Stop, Activity and Open. A researcher that needs you carries
// its answer buttons in the row, and one that made something says what under its row ("Made a presentation, a table and
// outlines ›", ticket 66 D4), leading to that work in Data. A click on a row opens its Tab. From eight researchers the
// list gains a search and ticks to stop several at once. It reads the server's rows and changes nothing itself: Stop asks, and says Stop
// requested until the server lists the researcher as stopped.
import type { ClientCommand, ResearchSession } from './protocol';
import { asking, chipStatus, isReady, pictureOf, researcherCell, researcherWords } from './shelf';
import { isWorking } from './research-state';
import { requestStop, stopButtonWords, stopRequested } from './research-stop';
import { researcherTiming } from './researcher-cards';
import { evidenceActions } from './research-evidence';
import { clean } from './research-report';
import { tabMark } from './tab-marks';
import { litCell, type LitCellState } from './lit-cell';
import { clock } from './working-strip';
import { moreButton, type MenuItem } from './drawer-menu';

/** From this many researchers in the Project the list gains its search and the ticks for Stop selected. */
export const SELECT_FROM = 8;
export type BoardGroup = 'needs' | 'working' | 'queued' | 'done';
/** The drawer's sections: waiting for a free slot is working too (its words say so). */
const SECTIONS: { id: 'needs' | 'working' | 'done'; title: string; groups: BoardGroup[] }[] = [
  { id: 'needs', title: 'Needs you', groups: ['needs'] }, { id: 'working', title: 'Working', groups: ['working', 'queued'] }, { id: 'done', title: 'Done', groups: ['done'] },
];

export function boardGroup(s: ResearchSession): BoardGroup {
  if (asking(s)) return 'needs';
  if (isWorking(s)) return s.status === 'queued' ? 'queued' : 'working';
  return 'done';
}

/** What it is doing now, without its name in front (the row names it already). */
export function nowWords(s: ResearchSession): string {
  const words = chipStatus(s);
  return words.startsWith(`${s.name} · `) ? words.slice(s.name.length + 3) : words.startsWith(`${s.name} `) ? words.slice(s.name.length + 1) : words;
}

/** Whether its row counts its time (it works, unasked, not queued, Stop not requested). */
const counting = (s: ResearchSession) => isWorking(s) && !asking(s) && !stopRequested(s) && s.status !== 'queued' && s.started != null;

/** The row's state words, with its clock while it works ("Working 1:01:14", "Needs you", "Lesson ready"). */
export function rowWords(s: ResearchSession, now = Date.now() / 1000): string {
  if (counting(s)) return `Working ${clock((now - (s.started ?? now)) * 1000)}`;
  const words = researcherWords(s);
  if (s.status === 'interrupted' && s.started != null) return `${words} · ${clock(((s.finished ?? now) - s.started) * 1000)}`;
  return words;
}

/** What a researcher waiting on you asks, in a sentence ("Keep going? It has worked for 60 minutes."). */
export function questionWords(s: ResearchSession): string {
  const q = asking(s); if (!q) return '';
  const sentence = (text: string) => { const t = clean(text).trim(); if (!t) return ''; const c = t.charAt(0).toUpperCase() + t.slice(1); return /[.?!]$/.test(c) ? c : `${c}.`; };
  return q.keepGoing ? `Keep going? ${sentence(q.why)}`.trim() : `It asks for ${clean(q.what)}${q.why ? `: ${clean(q.why)}` : ''}`;
}

/** A Study's row, as the workspace reads it from its Studies (research-efforts.ts). */
export type StudyRow = { id: string; title: string; sub: string; cell: LitCellState; cellLabel: string; open: () => void; current?: boolean };

export type BoardOptions = {
  open: (rid: string) => void;
  send?: (command: ClientCommand) => unknown;
  isNew?: (s: ResearchSession) => boolean;
  /** Its ⋯ › Activity: this researcher's Project's Activity. */
  activity?: (s: ResearchSession) => void;
  /** The researcher whose Tab is in front (its row is marked). */
  current?: () => string;
  /** A Project's name, for researchers still working in another. */
  project?: (id: string) => string;
  /** The empty state's button: a new researcher. */
  start?: () => void;
  /** The cover of a researcher that made no picture: the Project input it started from, else the Project's specimen;
   *  "" when there is neither (the row then shows a researcher's mark, never an empty box). */
  cover?: (s: ResearchSession) => string;
  /** What this Project's researcher made, from Data's index ("Made a presentation, a table and outlines"; data-roles.ts
   *  madeWords), and Data filtered to it; null when it has made nothing yet (its row then says nothing of it). */
  made?: (s: ResearchSession) => { words: string; show: () => void } | null;
};

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = '') => {
  const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node;
};

export class ResearchersBoard {
  readonly root = el('section', 'researchers-board');
  private readonly search = el('input', 'researchers-board-search') as HTMLInputElement;
  private readonly list = el('div', 'researchers-board-list');
  private readonly foot = el('div', 'researchers-board-foot');
  private readonly stopSelected = el('button', 'mm-control researchers-board-stop-selected', 'Stop selected') as HTMLButtonElement;
  private readonly selected = new Set<string>();
  private readonly times = new Map<string, HTMLElement>();
  private readonly cells = new Map<string, LitCellState>();
  private rows: ResearchSession[] = [];
  private elsewhere: ResearchSession[] = [];
  private studies: StudyRow[] | null = null;
  private studiesNote = '';
  private signature = '';
  private timer: ReturnType<typeof setInterval> | null = null;
  private readonly options: BoardOptions;

  constructor(options: BoardOptions) {
    this.options = options;
    this.root.setAttribute('aria-label', 'Researchers in this Project');
    this.search.type = 'search'; this.search.placeholder = 'Find a researcher'; this.search.setAttribute('aria-label', 'Find a researcher by name or brief');
    this.search.addEventListener('input', () => this.draw(true));
    this.stopSelected.type = 'button';
    this.stopSelected.addEventListener('click', () => {
      if (!this.options.send) return;
      for (const rid of this.selected) { const row = this.rows.find((s) => s.rid === rid); if (row && isWorking(row)) requestStop(rid, this.options.send); }
      this.selected.clear(); this.draw(true);
    });
    this.foot.append(this.stopSelected);
    this.root.append(this.search, this.list, this.foot);
  }

  /** Nothing to list: no researcher here or elsewhere and no Study. */
  get empty(): boolean { return !this.rows.length && !this.elsewhere.length && !this.studies?.length && !this.studiesNote; }

  /** This Project's researchers (research only, newest first), those still working in other Projects, and its Studies
   * (null when this build has none). Redrawn only when something it shows changed; the clocks tick on their own. */
  update(rows: ResearchSession[], extra: { elsewhere?: ResearchSession[]; studies?: StudyRow[] | null; studiesNote?: string } = {}): void {
    this.rows = rows; this.elsewhere = extra.elsewhere ?? []; this.studies = extra.studies ?? null; this.studiesNote = extra.studiesNote ?? '';
    for (const rid of [...this.selected]) if (!rows.some((s) => s.rid === rid)) this.selected.delete(rid);
    this.draw(false);
    const working = [...rows, ...this.elsewhere].some(isWorking);
    if (working && this.timer === null) this.timer = setInterval(() => this.tick(), 1000);
    else if (!working) this.stopTick();
  }

  private stopTick(): void { if (this.timer !== null) { clearInterval(this.timer); this.timer = null; } }

  private tick(): void {
    const now = Date.now() / 1000;
    for (const s of [...this.rows, ...this.elsewhere]) {
      const time = this.times.get(s.rid); if (!time) continue;
      const words = rowWords(s, now); if (time.textContent !== words) time.textContent = words;
    }
  }

  private draw(force: boolean): void {
    const current = this.options.current?.() ?? '';
    const key = (s: ResearchSession) => [s.rid, s.status, s.step, s.label, s.brief, s.result?.title, s.finished, s.started, s.engine, s.outputs.length,
      (s.requests ?? []).map((q) => [q.id, q.status]), stopRequested(s), this.options.isNew?.(s) ?? false, this.selected.has(s.rid), s.sitting,
      this.rows.includes(s) ? this.options.made?.(s)?.words ?? '' : ''];
    const signature = JSON.stringify([this.search.value, current, this.rows.map(key), this.elsewhere.map(key), this.studies, this.studiesNote]);
    if (!force && signature === this.signature) return;
    this.signature = signature;
    const focused = this.focusedKey();
    const query = this.search.value.trim().toLowerCase();
    const shown = this.rows.filter((s) => !query || `${s.name} ${s.label} ${s.brief} ${s.result?.title ?? ''}`.toLowerCase().includes(query));
    const selecting = this.rows.length >= SELECT_FROM;
    this.search.hidden = !selecting;
    this.times.clear();
    const parts: HTMLElement[] = [];
    if (this.empty) {
      const empty = el('div', 'mm-drawer-empty researchers-board-empty');
      empty.append(el('p', '', 'No researchers yet. Ask the Assistant, or start one.'));
      if (this.options.start) { const go = el('button', 'mm-control', 'New researcher') as HTMLButtonElement; go.type = 'button'; go.prepend(tabMark('plus', 'mm-dhead-icon')); go.addEventListener('click', () => this.options.start?.()); empty.append(go); }
      parts.push(empty);
    }
    for (const section of SECTIONS) {
      const members = shown.filter((s) => section.groups.includes(boardGroup(s)));
      if (!members.length) continue;
      parts.push(this.heading(section.title, members.length, section.id), ...members.map((s) => this.row(s, boardGroup(s), selecting, current)));
    }
    if (this.studies?.length || this.studiesNote) {
      parts.push(this.heading('Studies', this.studies?.length ?? 0, 'studies'));
      if (this.studiesNote) parts.push(el('p', 'mm-drawer-note', this.studiesNote));
      for (const study of this.studies ?? []) parts.push(this.studyRow(study));
    }
    if (this.elsewhere.length) {
      const head = this.heading('Still working in other Projects', null, 'elsewhere'); head.title = 'Started in another Project, they go on after a switch; their work is filed in that Project.';
      parts.push(head, ...this.elsewhere.map((s) => this.row(s, boardGroup(s), false, current, true)));
    }
    this.list.replaceChildren(...parts);
    this.refocus(focused);
    const stoppable = [...this.selected].filter((rid) => { const s = this.rows.find((row) => row.rid === rid); return s && isWorking(s); }).length;
    this.stopSelected.hidden = !this.options.send || stoppable === 0;
    this.stopSelected.textContent = `Stop selected (${stoppable})`;
  }

  private heading(title: string, count: number | null, group: string): HTMLElement {
    const head = el('div', 'mm-dsection researchers-board-group'); head.dataset.group = group;
    head.append(title);
    if (count !== null) { head.append(' '); head.append(el('span', 'mm-num', String(count))); }
    return head;
  }

  private row(s: ResearchSession, group: BoardGroup, selecting: boolean, current: string, elsewhere = false): HTMLElement {
    const wrap = el('div', 'mm-row-wrap researchers-board-row'); wrap.dataset.rid = s.rid; wrap.dataset.group = group; wrap.dataset.state = s.status;
    if (current === s.rid) wrap.dataset.current = 'true';
    if (this.options.isNew?.(s)) wrap.dataset.fresh = '';
    const title = (s.result?.title || s.label || s.brief || s.name).trim();
    const open = el('button', 'mm-row researchers-board-open') as HTMLButtonElement; open.type = 'button';
    open.setAttribute('aria-label', `${s.name}: ${title}`);
    // Engine and how long since its last step, with what it is doing: the row's tooltip, never its face.
    open.title = `${title}\n${chipStatus(s)}\n${researcherTiming(s)}`;
    const thumb = el('span', 'mm-row-thumb'); const picture = pictureOf(s) || this.options.cover?.(s) || '';
    if (picture) thumb.style.backgroundImage = `url("${picture}")`;
    else { thumb.dataset.empty = ''; thumb.append(tabMark('outline', 'mm-row-thumb-mark')); }
    const state = researcherCell(s), before = this.cells.get(s.rid);
    thumb.append(litCell(state, { size: 12, label: `${s.name}: ${researcherWords(s)}`, settle: before !== undefined && before !== state && (state === 'working' || state === 'ready' || state === 'needs') }));
    this.cells.set(s.rid, state);
    const words = el('span', 'mm-row-words');
    const sub = el('span', 'mm-row-sub');
    const time = el('span', group === 'needs' ? 'researchers-board-time is-needs' : 'researchers-board-time', rowWords(s));
    this.times.set(s.rid, time);
    sub.append(`${elsewhere ? (this.options.project?.(s.sitting ?? '') || s.name) : s.name} · `, time);
    words.append(el('span', 'mm-row-title', title), sub);
    open.append(thumb, words);
    open.addEventListener('click', () => this.options.open(s.rid));
    wrap.append(open);
    // From eight researchers: tick to stop several at once.
    if (selecting && isWorking(s) && this.options.send) {
      const box = el('input', 'researchers-board-pick') as HTMLInputElement; box.type = 'checkbox'; box.checked = this.selected.has(s.rid);
      box.setAttribute('aria-label', `Select ${s.name}`);
      box.addEventListener('change', () => { if (box.checked) this.selected.add(s.rid); else this.selected.delete(s.rid); this.draw(true); });
      wrap.append(box); wrap.dataset.picking = '';
    }
    wrap.append(moreButton(`More for ${s.name}`, () => this.menu(s)));
    // What it made, from Data (this Project's researchers only: one still working elsewhere files its work there).
    const made = elsewhere ? null : this.options.made?.(s);
    if (made) {
      const link = el('button', 'researchers-board-made data-plain') as HTMLButtonElement; link.type = 'button';
      link.title = `Show what ${s.name} made, in Data`;
      // Its last word keeps the › beside it when the line wraps.
      const cut = made.words.lastIndexOf(' ') + 1, end = el('span', 'researchers-board-made-end', made.words.slice(cut));
      end.append(tabMark('right', 'researchers-board-made-mark'));
      link.append(made.words.slice(0, cut), end);
      link.addEventListener('click', () => made.show());
      wrap.append(link); wrap.dataset.made = '';
    }
    const question = asking(s);
    if (question && this.options.send) {
      const ask = el('div', 'mm-row-question researchers-board-question');
      ask.append(el('span', '', questionWords(s)), evidenceActions(s, question, this.options.send));
      wrap.append(ask);
    }
    return wrap;
  }

  /** Its ⋯: Stop (while it works; "Stop requested" until the server confirms), Activity, Open. */
  private menu(s: ResearchSession): MenuItem[] {
    const items: MenuItem[] = [];
    if (isWorking(s) && this.options.send) {
      const words = stopButtonWords(s);
      items.push({ label: words.text, title: words.title, disabled: words.disabled, act: () => { if (this.options.send) requestStop(s.rid, this.options.send); this.draw(true); } });
    }
    if (this.options.activity) items.push({ label: 'Activity', title: `Open Activity for ${s.name}`, act: () => this.options.activity?.(s) });
    items.push({ label: 'Open', title: isReady(s) && !isWorking(s) ? 'Open its work in its Tab' : 'Open its Tab', act: () => this.options.open(s.rid) });
    return items;
  }

  private studyRow(study: StudyRow): HTMLElement {
    const open = el('button', 'mm-row researchers-board-study') as HTMLButtonElement; open.type = 'button'; open.dataset.study = study.id;
    if (study.current) open.setAttribute('aria-current', 'true');
    const icon = el('span', 'mm-row-icon'); icon.append(litCell(study.cell, { size: 12, label: study.cellLabel }));
    const words = el('span', 'mm-row-words'); words.append(el('span', 'mm-row-title', study.title), el('span', 'mm-row-sub', study.sub));
    open.append(icon, words); open.title = `${study.title}\n${study.sub}`;
    open.addEventListener('click', () => study.open());
    return open;
  }

  private focusedKey(): string | null {
    const active = document.activeElement as HTMLElement | null;
    if (!active || !this.list.contains?.(active)) return null;
    const row = active.closest?.<HTMLElement>('[data-rid]');
    return row ? `${row.dataset.rid}|${active.className.split(' ')[0]}` : null;
  }

  private refocus(key: string | null): void {
    if (!key) return;
    const [rid, className] = key.split('|');
    const row = [...this.list.children].find((c) => (c as HTMLElement).dataset.rid === rid) as HTMLElement | undefined;
    (className ? row?.querySelector<HTMLElement>(`.${className}`) : row)?.focus?.({ preventScroll: true });
  }
}

/** How long it has worked (wall time). */
export function elapsedWords(s: ResearchSession, now = Date.now() / 1000): string {
  return researcherTiming(s, now).split(' · ')[1] ?? '';
}
