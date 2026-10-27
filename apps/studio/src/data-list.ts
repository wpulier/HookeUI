// Data's list (ticket 66, D2; the mock's frames "data" and "data-by"): under Search Data, "Made by everyone ▾" (or the
// chosen maker's chip with ×), then the sections in their order, Other folded last. Each row is the shared anatomy
// (workspace-feel.css .mm-row): its picture or kind, its title with how many it holds, one quiet line ("On this Tab" first
// when it is on the Tab you are on; the lit cell while a write-up is being written), the New dot and ⋯. What a row does
// (its click, its ⋯, its drag) is the workspace's (`view`), so this list only lays them out; the rows and their words
// come from data-roles.ts.
import './data-list.css';
import type { MenuItem } from './drawer-menu';
import { moreButton, popMenu } from './drawer-menu';
import { litCell, type LitCellState } from './lit-cell';
import { thumbnail } from './madetoday';
import { tabMark } from './tab-marks';
import {
  ROLE_SECTIONS, makerName, rowCount, rowHolds, rowIsNew, rowLine, rowSeenKeys, rowTitle, rowWriting, sourceValues,
  type DataEntry, type DataRow, type MakerChoice, type MakerKey,
} from './data-roles';

/** What a row does, from the workspace: today's open, its ⋯, its tooltip's first lines and, for a thing made, its drag;
 * `current`: the row is the one shown where it opens (marked `data-current`, as the Data reader marks its item). */
export interface DataRowView { open: () => void; menu: () => MenuItem[]; tip: string; drag?: (wrap: HTMLElement) => void; current?: boolean }

export interface DataListOptions {
  view: (row: DataRow) => DataRowView;
  /** Made by: a maker chosen, or everyone (null). */
  choose: (key: MakerKey | null) => void;
  /** The chip's Open Researcher N: its Tab. */
  openResearcher?: (rid: string) => void;
  /** The input filter's ×. */
  clearSource?: () => void;
  /** Empty Data's one action: a picture or video from this Mac. */
  addInput?: () => void;
  /** A researcher's lit cell for its chip, by name; null when it is not one of this Project's. */
  researcherCell?: (name: string) => { state: LitCellState; label: string } | null;
}

export interface DataListState {
  /** The rows to show: already narrowed by the maker, the input and the search. */
  rows: DataRow[];
  choices: MakerChoice[];
  maker: MakerKey | null;
  /** The input the list is narrowed to, by its title. */
  source: string | null;
  query: string;
  /** The index has not been read for this Project yet (its inputs show meanwhile). */
  loading: boolean;
  /** Whether the row is on the Tab you are on. */
  here: (row: DataRow) => boolean;
  seen: ReadonlySet<string>;
  now: number;
  /** A model's display name for an older entry whose title does not say what made it. */
  model?: (entry: DataEntry) => string;
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node;
};
/** Data's own buttons keep their look: the quiet fill every other button of a panel gets (workspace-calm.css) skips them. */
const button = (className: string, text = ''): HTMLButtonElement => { const b = el('button', `${className} data-plain`, text); b.type = 'button'; return b; };

/** Kind marks for things with no picture of their own (the mock's icons: a presentation's screen, a report's page). */
const MARKS: Record<string, string> = {
  presentation: '<rect x="3.5" y="4.5" width="13" height="9" rx="1"/><path d="M10 13.5v2.6M7.4 16.1h5.2"/>',
  report: '<path d="M6 3.5h6l3 3V16a.5.5 0 0 1-.5.5h-8.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z"/><path d="M8 9.5h5M8 12h5M8 14.5h3"/>',
  lesson: '<path d="M3.5 6 10 3.5 16.5 6 10 8.5z"/><path d="M6 7.2v4.3c1 1.2 2.4 1.8 4 1.8s3-.6 4-1.8V7.2"/>',
  note: '<path d="M4.5 4.5h11a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H9.5l-3.5 2.8v-2.8H4.5a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/>',
  table: '<rect x="3.5" y="4" width="13" height="12" rx="1.5"/><path d="M3.5 8h13M3.5 12h13M8 8v8M12.5 8v8"/>',
  chart: '<path d="M4 16V4M4 16h12"/><path d="M7 13v-3M10 13V7M13 13v-5"/>',
  link: '<path d="M8.6 11.4a3 3 0 0 0 4.2 0l2.3-2.3a3 3 0 0 0-4.2-4.2l-.9.9"/><path d="M11.4 8.6a3 3 0 0 0-4.2 0l-2.3 2.3a3 3 0 0 0 4.2 4.2l.9-.9"/>',
  recording: '<rect x="3.5" y="5" width="9.5" height="10" rx="1.5"/><path d="m13 8.5 3.5-2v7l-3.5-2"/>',
  picture: '<rect x="3.5" y="4.5" width="13" height="11" rx="1.5"/><path d="m4 14 4-4 3 3 2-2 3.5 3"/>',
};
function mark(name: string): HTMLElement {
  const lead = el('span', 'mm-row-icon data-row-mark');
  const svg = (document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg')) as SVGSVGElement;
  svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', 'mm-row-kind');
  svg.innerHTML = MARKS[name] ?? MARKS.report;
  lead.append(svg); lead.dataset.mark = name;
  return lead;
}

/** A row's picture: an input's own, a thing's thumbnail where the server makes one (outlines on their picture, a
 * researcher's picture or video), else its kind's mark. */
export function rowLead(row: DataRow): HTMLElement {
  const entry = row.entry, asset = row.asset;
  if (asset || entry?.kind === 'asset') {
    const id = asset?.id ?? entry!.id;
    if (asset?.kind === 'recording' && !asset.thumb) return mark('recording');
    const lead = el('span', 'mm-row-thumb'), img = el('img');
    img.src = asset?.thumb || `/api/library/${encodeURIComponent(id)}/file`; img.alt = ''; img.loading = 'lazy'; lead.append(img);
    return lead;
  }
  if (!entry) return mark('report');
  if (entry.kind === 'research') return mark((entry.forms?.[0]?.label ?? '') === 'Presentation' ? 'presentation' : 'report');
  const kinds: Record<string, string> = { lesson: 'lesson', annotation: 'note', 'finding-table': 'table', 'finding-chart': 'chart' };
  if (kinds[entry.kind]) return mark(kinds[entry.kind]);
  if (entry.kind === 'source' && !sourceValues(entry).picture && !entry.pictures?.length) return mark('link');
  const lead = el('span', 'mm-row-lead');
  // A researcher's picture, video poster or saved web picture has a thumbnail though it is not a product.
  lead.append(thumbnail(['figure', 'video', 'source'].includes(entry.kind) ? { ...entry, product: true } : entry));
  return lead;
}

export class DataList {
  /** "Made by everyone ▾", or the chosen maker's chip; the input filter's chip beside it. */
  readonly filter = el('div', 'data-filter');
  /** The sections and their rows. */
  readonly list = el('div', 'data-sections');
  private otherOpen = false;
  private readonly options: DataListOptions;
  private last: DataListState | null = null;

  constructor(options: DataListOptions) {
    this.options = options;
    this.filter.setAttribute('role', 'group'); this.filter.setAttribute('aria-label', 'Show what was made by');
  }

  render(state: DataListState): void {
    this.last = state;
    this.renderFilter(state);
    const parts: HTMLElement[] = [];
    for (const [role, name] of ROLE_SECTIONS) {
      const rows = state.rows.filter((row) => row.role === role);
      if (!rows.length) continue;
      if (role !== 'other') { parts.push(this.heading(name, rows.length, role), ...rows.map((row) => this.row(row, state))); continue; }
      // Other (bookmarks, calibrations, taught labels): folded last, open while a search finds something in it.
      const open = this.otherOpen || !!state.query.trim();
      const fold = button('mm-dsection data-fold');
      fold.dataset.role = 'other'; fold.setAttribute('aria-expanded', String(open));
      fold.append(name, ' ', el('span', 'mm-num', String(rows.length)), tabMark(open ? 'down' : 'right', 'data-fold-mark'));
      if (!open && rows.some((row) => rowIsNew(row, state.seen))) { const dot = el('span', 'mm-new-dot data-fold-new'); dot.title = 'New since you last looked'; fold.append(dot); }
      fold.title = open ? 'Fold Other' : 'Bookmarks, calibrations and taught labels';
      fold.addEventListener('click', () => { this.otherOpen = !open; if (this.last) this.render(this.last); });
      parts.push(fold, ...(open ? rows.map((row) => this.row(row, state)) : []));
    }
    if (state.loading) parts.push(el('p', 'mm-drawer-note data-loading', 'Loading what this Project made…'));
    else if (!state.rows.length) parts.push(this.empty(state));
    this.list.replaceChildren(...parts);
  }

  private renderFilter(state: DataListState): void {
    const parts: HTMLElement[] = [];
    if (state.maker) {
      const name = makerName(state.maker), chip = el('span', 'data-chip');
      const cell = state.maker.startsWith('r:') ? this.options.researcherCell?.(name) : null;
      if (cell) chip.append(litCell(cell.state, { size: 11, label: cell.label }));
      chip.append(el('span', 'data-chip-name', name));
      const clear = button('data-chip-x'); clear.setAttribute('aria-label', 'Show what everyone made'); clear.title = 'Show what everyone made';
      clear.append(tabMark('x', 'data-chip-x-mark')); clear.addEventListener('click', () => this.options.choose(null));
      chip.append(clear); parts.push(chip);
      const rid = state.choices.find((choice) => choice.key === state.maker)?.rid;
      if (rid && this.options.openResearcher) {
        const open = button('data-chip-open', `Open ${name}`); open.title = `Open ${name}’s Tab, to talk to it`;
        open.addEventListener('click', () => this.options.openResearcher?.(rid)); parts.push(open);
      }
    } else {
      const choose = button('data-made-by', 'Made by everyone');
      choose.append(tabMark('down', 'data-made-by-mark'));
      choose.setAttribute('aria-haspopup', 'menu'); choose.setAttribute('aria-expanded', 'false');
      choose.addEventListener('click', () => popMenu(choose, this.makerMenu(state), 'Made by', { start: true }));
      parts.push(choose);
    }
    if (state.source !== null) {
      const chip = el('span', 'data-chip data-chip-source');
      chip.append(el('span', 'data-chip-name', `From ${state.source}`));
      const clear = button('data-chip-x'); clear.setAttribute('aria-label', `Clear the filter: from ${state.source}`); clear.title = 'Show what was made from every input';
      clear.append(tabMark('x', 'data-chip-x-mark')); clear.addEventListener('click', () => this.options.clearSource?.());
      chip.append(clear); parts.push(chip);
    }
    this.filter.replaceChildren(...parts);
  }

  private makerMenu(state: DataListState): MenuItem[] {
    return [{ label: 'Everyone', checked: true, act: () => this.options.choose(null) }, 'separator',
      ...state.choices.map((choice): MenuItem => ({ label: choice.name, checked: false, title: `Show only what ${choice.name === 'You' ? 'you' : choice.name} made`, act: () => this.options.choose(choice.key) }))];
  }

  private heading(name: string, count: number, role: string): HTMLElement {
    const head = el('h3', 'mm-dsection data-section', name); head.dataset.role = role;
    head.append(' ', el('span', 'mm-num', String(count)));
    return head;
  }

  private empty(state: DataListState): HTMLElement {
    if (state.query.trim() || state.source !== null) return el('p', 'mm-drawer-empty data-empty', 'Nothing matches. Clear the search or the filter.');
    // Said as what the records say: an older record that names no maker may still be theirs.
    if (state.maker) return el('p', 'mm-drawer-empty data-empty', `Nothing in Data is recorded as made by ${state.maker === 'you' ? 'you' : makerName(state.maker)}.`);
    const empty = el('div', 'mm-drawer-empty data-empty');
    empty.append(el('p', '', 'Nothing in Data yet. Open a picture or video, or run a Tool on one.'));
    if (this.options.addInput) { const add = button('mm-control study-data-open-input', 'Open a picture or video'); add.addEventListener('click', () => this.options.addInput?.()); empty.append(add); }
    return empty;
  }

  private row(row: DataRow, state: DataListState): HTMLElement {
    const view = this.options.view(row), entry = row.entry, input = !entry || entry.kind === 'asset';
    const title = rowTitle(row), count = rowCount(row), holds = row.parts.length > 1 || row.entry?.kind === 'population' ? rowHolds(row) : [];
    // Its whole title (the server's, "Bright regions · Bright objects · 02:35"; an input's with its credit) stays its
    // accessible name and its tooltip's first line, as before ticket 66.
    const whole = (input ? row.asset?.title ?? entry?.title : entry?.title) || title;
    const theirs = !!entry?.untrusted && row.maker?.kind === 'researcher' ? row.maker.name : '';
    const wrap = el('div', `mm-row-wrap data-row ${input ? 'study-data-input' : 'study-result-card'}`);
    wrap.dataset.resource = row.key; wrap.dataset.role = row.role; wrap.dataset.kind = entry?.kind ?? 'asset';
    if (row.parts.length > 1) wrap.dataset.parts = row.parts.map((part) => part.id).join(' ');
    const keys = rowSeenKeys(row);
    if (keys.length) wrap.dataset.seen = keys.join(' ');
    if (rowIsNew(row, state.seen)) wrap.dataset.fresh = 'true';
    if (theirs) wrap.dataset.untrusted = 'true';
    if (view.current) wrap.dataset.current = 'true';
    const here = state.here(row);
    if (here) wrap.dataset.here = 'true';
    const open = button(`mm-row data-row-open${input ? '' : ' study-data-title'}`);
    open.setAttribute('aria-label', `Open ${whole}`);
    // Its whole title (a researcher's words said as theirs), what it holds, then what a click does.
    open.title = [theirs ? `“${whole}”, ${theirs}’s words` : whole, holds.length ? `Holds ${holds.join(' · ')}` : '', view.tip].filter(Boolean).join('\n');
    if (input) open.dataset.input = row.asset?.id ?? row.key;
    const words = el('span', 'mm-row-words'), head = el('span', 'mm-row-title data-row-title');
    head.append(el('span', 'data-row-t', title));
    if (count) head.append(el('span', 'data-row-n mm-num', count));
    const sub = el('span', 'mm-row-sub data-row-sub');
    if (here) { const mark = el('span', 'data-here', 'On this Tab'); mark.title = 'On the Tab you are on'; sub.append(mark); }
    if (rowWriting(row)) { const live = el('span', 'data-row-live'); live.append(litCell('working', { size: 11, label: 'Being written' })); sub.append(live); }
    sub.append(rowLine(row, { filtered: !!state.maker, now: state.now, model: state.model }));
    words.append(head, sub);
    const side = el('span', 'mm-row-side'), dot = el('span', 'mm-new-dot'); dot.setAttribute('aria-hidden', 'true'); dot.title = 'New since you last looked';
    side.append(dot);
    open.append(rowLead(row), words, side);
    open.addEventListener('click', () => view.open());
    wrap.append(open, moreButton(`More for ${whole}`, view.menu));
    view.drag?.(wrap);
    return wrap;
  }
}
