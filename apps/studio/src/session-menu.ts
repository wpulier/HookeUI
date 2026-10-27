// The session's title, top left (docs/design/2026-10-12-workspace-and-assets.md; Will's design of 2026-10-12): you
// always see which session you are in, and its name is the switcher. It reads "Untitled session" until the app names it
// from its first specimen or you do; a dot says the app suggests a new session. Its menu: "+ New session" and a search
// first, then this session (renamed in place, what it holds), the app's suggestion, and every session, newest first by
// when each began (so opening one never moves it), under quiet day separators. A row has a picture of what the session
// last looked at, its name, when it was last used and what it holds; ✎ renames it in place. It keeps its id (#session),
// so the screen report and the header's layout rules still find it, and it reports itself as kind `sessions` ("close
// the sessions list"). Nothing switches until the server's echo: a click sends the command and says "Opening…".
import type { Panel } from './panels';
import type { ScreenItem } from './screen';
import {
  chipView, factsLine, groupByDay, inlineRename, renderSessionRow, rowView, sessionName, suggestWords, type SessionsModel,
} from './sessions';
import type { SessionRow } from './protocol';

export interface SessionMenuOptions {
  /** How the list is read when no `sessions` message came yet (GET /api/sessions). */
  fetcher?: typeof fetch;
  /** Opens All sessions, the full page (sessions-page.ts); without it the menu has no link to it. */
  openAll?: () => void;
}

/** Whether a row matches the search: its name (the user's or the automatic one) or anything seen in it. */
export function matchesSearch(row: SessionRow, query: string, now: number): boolean {
  const words = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return true;
  const text = [sessionName(row, now), row.label ?? '', ...(row.specimens ?? [])].join(' ').toLowerCase();
  return words.every((word) => text.includes(word));
}

export class SessionMenu implements Panel {
  readonly id = 'session-menu';
  readonly whileTyping = true; // its search and rename fields are its own
  readonly escapePriority = 100; // a menu: above the windows
  private readonly model: SessionsModel;
  private readonly chip: HTMLButtonElement;
  private readonly panel: HTMLElement;
  private readonly fetcher: typeof fetch | undefined;
  private readonly name = document.createElement('span');
  private readonly chevron = document.createElement('span');
  private readonly dot = document.createElement('span');
  /** Built once: "+ New session" and the search (a search field rebuilt would lose what is being typed). */
  private readonly top = document.createElement('div');
  readonly createButton = document.createElement('button');
  readonly search = document.createElement('input');
  private readonly body = document.createElement('div');
  private renaming: HTMLInputElement | null = null;
  private readonly openAll: (() => void) | undefined;
  private chipStamp = '';
  private panelStamp = '';

  constructor(model: SessionsModel, chip: HTMLButtonElement, panel: HTMLElement, options: SessionMenuOptions = {}) {
    this.model = model;
    this.chip = chip;
    this.panel = panel;
    this.openAll = options.openAll;
    this.fetcher = options.fetcher;
    this.name.className = 'session-chip-name';
    this.chevron.className = 'session-chip-chevron';
    this.chevron.setAttribute('aria-hidden', 'true');
    this.chevron.textContent = '⌄';
    this.dot.className = 'session-chip-dot';
    this.dot.setAttribute('aria-hidden', 'true');
    this.dot.hidden = true;
    chip.replaceChildren(this.name, this.dot, this.chevron);
    chip.setAttribute('aria-haspopup', 'true');
    chip.setAttribute('aria-expanded', 'false');
    chip.addEventListener('click', () => this.toggle());

    this.top.className = 'session-menu-top';
    this.createButton.type = 'button';
    this.createButton.className = 'session-new mm-primary';
    this.createButton.title = 'A fresh project: its own conversations and work. This one is kept, to come back to.';
    this.createButton.addEventListener('click', () => { if (this.model.create()) this.close(); });
    this.search.type = 'search';
    this.search.className = 'session-search';
    this.search.placeholder = 'Search projects';
    this.search.setAttribute('aria-label', 'Search projects by name or what was seen in them');
    this.search.autocomplete = 'off';
    this.search.spellcheck = false;
    this.search.addEventListener('input', () => { this.panelStamp = ''; this.renderPanel(); });
    this.search.addEventListener('keydown', (event) => {
      if (event.key === 'ArrowDown') { event.preventDefault(); this.panel.querySelector<HTMLElement>('.session-row-open')?.focus?.(); }
    });
    this.top.append(this.search, this.createButton);
    this.body.className = 'session-menu-body';
    this.body.addEventListener('keydown', (event) => this.arrows(event));
    panel.replaceChildren(this.top, this.body);
    model.onChange(() => this.render());
    this.render();
  }

  isOpen(): boolean {
    return !this.panel.hidden;
  }

  /** Menus float over everything for a moment: nothing moves for them. */
  footprint(): null {
    return null;
  }

  toggle(): void {
    if (this.isOpen()) this.close();
    else this.open();
  }

  open(): void {
    this.panel.hidden = false;
    this.chip.setAttribute('aria-expanded', 'true');
    this.search.value = '';
    this.panelStamp = '';
    this.render();
    this.place();
    void this.model.load(this.fetcher);
  }

  close(): void {
    if (this.panel.hidden) return;
    this.panel.hidden = true;
    this.renaming = null;
    this.chip.setAttribute('aria-expanded', 'false');
    this.chip.focus?.({ preventScroll: true });
  }

  /** A click outside the title and its menu closes it (as the camera's panel does). */
  outside(target: Node): boolean {
    if (this.panel.hidden || !target.isConnected || this.panel.contains(target) || this.chip.contains(target)) return false;
    this.close();
    return true;
  }

  /** What the screen report says of it: kind `sessions` (the sessions list), open or not, where. */
  screenItem(): ScreenItem {
    const open = this.isOpen();
    const rect = open ? this.panel.getBoundingClientRect() : null;
    return { id: 'sessions', kind: 'sessions', title: 'Projects', open, visible: open,
      rect: rect && rect.right > rect.left && rect.bottom > rect.top
        ? [Math.round(rect.left), Math.round(rect.top), Math.round(rect.right - rect.left), Math.round(rect.bottom - rect.top)] : null };
  }

  /** A change the server sends (`present` open or close on kind `sessions`): true when it was one of these. */
  applyPresent(set: Record<string, unknown>): boolean {
    if (set.open === true || set.visible === true) { if (!this.isOpen()) this.open(); return true; }
    if (set.open === false || set.visible === false) { this.close(); return true; }
    return false;
  }

  render(): void {
    this.renderChip();
    if (this.isOpen() && !this.renaming) this.renderPanel();
  }

  /** The title: which session you are in, "Opening…" while a switch waits for the server, a dot for a suggestion. */
  renderChip(): void {
    const now = this.model.now();
    const view = chipView(this.model.shown(), now);
    const opening = this.model.opening();
    const target = opening ? this.model.rows().find((row) => this.model.opening(row.id)) : undefined;
    const name = opening ? 'Opening…' : view.name;
    const line = opening ? `Opening ${this.model.creating() ? 'a new project' : target ? sessionName(target, now) : 'a project'}` : view.line;
    // Shown once the server has said which session this is (as the day picker was), so the header never jumps.
    this.chip.hidden = !this.model.state;
    const stamp = JSON.stringify([name, line, view.suggest]);
    if (stamp === this.chipStamp) return;
    this.chipStamp = stamp;
    this.name.textContent = name;
    this.dot.hidden = !view.suggest || opening;
    this.chip.dataset.state = opening ? 'opening' : view.suggest ? 'suggest' : '';
    const suggested = view.suggest && !opening ? '. The app suggests a new project' : '';
    this.chip.setAttribute('aria-label', `Project: ${line}${suggested}. Switch projects or rename this one`);
    this.chip.title = `${line}${suggested}. Click to switch projects, start a new one, or rename this one.`;
  }

  /** Where the menu opens: under the title, its left edge at the title's (never past the window's right edge). */
  place(): void {
    const top = this.chip.offsetTop + this.chip.offsetHeight + 6;
    this.panel.style.top = `${Math.round(top)}px`;
    const parent = this.panel.offsetParent as HTMLElement | null;
    if (parent) {
      const width = this.panel.offsetWidth || 400;
      const left = Math.max(10, Math.min(this.chip.offsetLeft, parent.clientWidth - width - 10));
      this.panel.style.left = `${Math.round(left)}px`;
      this.panel.style.right = 'auto';
    }
  }

  private renderPanel(): void {
    const model = this.model;
    const now = model.now();
    const state = model.shown();
    const current = model.current;
    const rows = model.rows();
    const query = this.search.value;
    const pendingKey = rows.filter((row) => model.opening(row.id)).map((row) => row.id);
    const stamp = JSON.stringify([state, rows, model.error, pendingKey, model.creating(), query, Math.floor(now / 60)]);
    if (stamp === this.panelStamp) return;
    this.panelStamp = stamp;
    const focused = this.focusedSid();
    this.createButton.textContent = model.creating() ? 'Starting a new project…' : '+ New project';
    this.createButton.disabled = model.creating();
    const children: HTMLElement[] = [];

    // This session: its name, renamed in place, and what it holds.
    if (!query.trim()) {
      const here = document.createElement('div');
      here.className = 'session-now';
      const heading = Object.assign(document.createElement('p'), { className: 'feed-group', textContent: 'This project' });
      const nameRow = document.createElement('div');
      nameRow.className = 'session-now-name';
      const title = document.createElement('strong');
      title.className = 'session-now-title';
      const currentRow = model.currentRow();
      title.textContent = state ? chipView(state, now).name : currentRow ? sessionName(currentRow, now) : 'Project';
      const pencil = document.createElement('button');
      pencil.type = 'button';
      pencil.className = 'session-now-rename';
      pencil.textContent = '✎';
      pencil.title = 'Rename this project';
      pencil.setAttribute('aria-label', 'Rename this project');
      pencil.disabled = !current;
      pencil.addEventListener('click', () => this.renameCurrent(title));
      title.addEventListener('dblclick', () => this.renameCurrent(title));
      nameRow.append(title, pencil);
      here.append(heading, nameRow);
      if (state) {
        here.append(Object.assign(document.createElement('p'), { className: 'panel-note session-now-facts',
          textContent: factsLine({ started: state.started, days: state.days ?? currentRow?.days, counts: state.counts ?? currentRow?.counts }, now) }));
      }
      children.push(here);
    }

    // The app's suggestion: a fact decided it, the user decides. Never automatic.
    if (state?.suggest && !query.trim()) {
      const suggest = document.createElement('div');
      suggest.className = 'session-suggest';
      suggest.append(Object.assign(document.createElement('p'), { textContent: suggestWords(state.suggest) }));
      const actions = document.createElement('div');
      actions.className = 'session-suggest-actions';
      const fresh = Object.assign(document.createElement('button'), { type: 'button', className: 'primary', textContent: 'New project' });
      fresh.addEventListener('click', () => { if (model.create()) this.close(); });
      const keep = Object.assign(document.createElement('button'), { type: 'button', textContent: 'Keep here' });
      keep.addEventListener('click', () => model.keep());
      actions.append(fresh, keep);
      suggest.append(actions);
      children.push(suggest);
    }

    if (model.error) {
      const error = Object.assign(document.createElement('p'), { className: 'panel-note session-error', textContent: model.error });
      error.setAttribute('role', 'status');
      children.push(error);
    }

    // Every session (the one open marked), newest first by when each began, under day separators; the search narrows it.
    const list = document.createElement('ol');
    list.className = 'session-rows';
    list.setAttribute('aria-label', 'Projects, newest first');
    const shown = rows.filter((row) => matchesSearch(row, query, now));
    const open = (id: string) => this.switchTo(id);
    const rename = (id: string) => this.renameRow(id);
    for (const group of groupByDay(shown, now, 'started')) {
      const day = Object.assign(document.createElement('li'), { className: 'session-day', textContent: group.label });
      day.setAttribute('role', 'presentation');
      list.append(day);
      for (const row of group.rows) list.append(renderSessionRow(rowView(row, current ?? '', now), 'menu', { open, rename }, model.opening(row.id)));
    }
    if (!shown.length) {
      const empty = Object.assign(document.createElement('li'), { className: 'panel-note session-empty',
        textContent: query.trim() ? 'No project matches.' : model.list ? 'No projects yet.' : model.error ? '' : 'Reading the projects…' });
      if (empty.textContent) list.append(empty);
    }
    children.push(list);
    if (this.openAll && !query.trim()) {
      const all = Object.assign(document.createElement('button'), { type: 'button', className: 'session-all-link', textContent: 'All projects ›' });
      all.addEventListener('click', () => { this.close(); this.openAll?.(); });
      children.push(all);
    }
    this.body.replaceChildren(...children);
    if (focused) [...this.body.querySelectorAll<HTMLElement>('.session-row-open')].find((node) => node.dataset.sid === focused)?.focus?.({ preventScroll: true });
  }

  /** The id of the row focused now, so a rebuild puts the focus back. */
  private focusedSid(): string | null {
    const active = (typeof document !== 'undefined' ? document.activeElement : null) as HTMLElement | null;
    if (!active || !this.body.contains(active as never)) return null;
    return active.dataset?.sid ?? null;
  }

  /** Go to a session: the menu closes and the title says "Opening…" until the server's echo. */
  switchTo(id: string): void {
    if (this.model.switchTo(id)) this.close();
  }

  /** Rename this session in place: Enter saves, Escape cancels (and keeps the menu open). */
  private renameCurrent(title: HTMLElement): void {
    const current = this.model.current;
    if (!current || this.renaming) return;
    const row = this.model.currentRow();
    this.renaming = inlineRename(title, row?.name ?? '', 'Name this project', (name) => {
      this.renaming = null;
      if (name !== null) this.model.rename(current, name);
      this.panelStamp = '';
      this.render();
      this.body.querySelector<HTMLElement>('.session-now-rename')?.focus?.({ preventScroll: true });
    });
    if (row && !row.name.trim()) this.renaming.placeholder = sessionName(row, this.model.now());
    this.renaming.focus();
    this.renaming.select?.();
  }

  /** Rename any session in place, from its row's ✎ (or a double-click on its name). */
  private renameRow(id: string): void {
    if (this.renaming) return;
    const row = this.model.rows().find((r) => r.id === id);
    const name = [...this.body.querySelectorAll<HTMLElement>('.session-row')].find((node) => node.dataset.sid === id)
      ?.querySelector<HTMLElement>('.session-row-name');
    if (!row || !name) return;
    const said = sessionName(row, this.model.now());
    this.renaming = inlineRename(name, row.name, `Name the project ${said}`, (value) => {
      this.renaming = null;
      if (value !== null) this.model.rename(id, value);
      this.panelStamp = '';
      this.render();
    });
    if (!row.name.trim()) this.renaming.placeholder = said;
    this.renaming.focus();
    this.renaming.select?.();
  }

  /** Up and down move between rows (Home and End to the ends); up from the first goes back to the search. */
  private arrows(event: KeyboardEvent): void {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const target = event.target as HTMLElement | null;
    if (!target?.classList?.contains('session-row-open')) return;
    const buttons = [...this.body.querySelectorAll<HTMLElement>('.session-row-open')];
    const at = buttons.indexOf(target);
    if (at < 0) return;
    event.preventDefault();
    if (event.key === 'ArrowUp' && at === 0) { this.search.focus?.(); return; }
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
      : Math.min(buttons.length - 1, Math.max(0, at + (event.key === 'ArrowDown' ? 1 : -1)));
    buttons[next]?.focus?.({ preventScroll: true });
  }
}
