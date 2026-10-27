// All sessions (#sessions; Will, 2026-10-15: "sessions should have a fuller view like assets"): every session as a card
// in a gallery, as All assets shows the library. Each card is a picture of what the session last looked at (else the
// camera's icon), its name, when it was last used and what it holds, under day headings by when each began; a search
// narrows it, and + New session starts one. A click opens that session and goes back to the workspace (the one open now
// just goes back). The session title's menu stays the quick switcher; this is the full view.
//
// It only shows and asks: switching, starting and renaming are the sessions model's (sessions.ts).
import { matchesSearch } from './session-menu';
import { groupByDay, inlineRename, rowView, sessionName, type SessionsModel } from './sessions';

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text?: string): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  el.className = className;
  if (text !== undefined) el.textContent = text;
  return el;
}

function cameraIcon(): Element {
  const svg = document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('aria-hidden', 'true');
  const use = document.createElementNS?.('http://www.w3.org/2000/svg', 'use') ?? document.createElement('use');
  use.setAttribute('href', '#i-camera');
  svg.append(use);
  return svg;
}

export class SessionsPage {
  readonly search = node('input', 'library-search');
  readonly newButton = node('button', 'mm-primary sessions-new', '+ New project');
  readonly list = node('div', 'sessions-groups');
  /** What went wrong opening or starting a session (the model's `error`: offline, or a switch the server never echoed),
   * said here as the session menu says it, since a click here that did not open one stays on this page. */
  readonly error = node('p', 'panel-note session-error');
  private visible = false;
  private stamp = '';
  private renaming: HTMLInputElement | null = null;

  private readonly model: SessionsModel;
  private readonly go: (hash: string) => void;

  /** `go` leaves the page for the workspace ("#"). */
  constructor(root: HTMLElement, model: SessionsModel, go: (hash: string) => void) {
    this.model = model;
    this.go = go;
    root.classList?.add('library-page', 'sessions-page');
    const head = node('header', 'lib-head');
    const words = node('div', 'lib-words');
    const title = node('h1', 'lib-title', 'All projects');
    title.id = 'sessions-title';
    words.append(title, node('p', 'lib-lead', 'Every project, newest first. Open one to carry on where it left off.'));
    const right = node('div', 'lib-head-right');
    this.newButton.type = 'button';
    this.newButton.addEventListener('click', () => { if (model.create()) go('#'); });
    right.append(this.newButton);
    head.append(words, right);
    const tools = node('div', 'lib-tools');
    this.search.type = 'search';
    this.search.placeholder = 'Search projects';
    this.search.setAttribute('aria-label', 'Search projects');
    this.search.addEventListener('input', () => { this.stamp = ''; this.render(); });
    tools.append(this.search);
    this.error.setAttribute('role', 'status');
    this.error.hidden = true;
    root.replaceChildren(head, tools, this.error, this.list);
    model.onChange(() => this.render());
  }

  show(): void {
    this.visible = true;
    this.stamp = '';
    this.render();
  }

  hide(): void {
    this.visible = false;
  }

  render(): void {
    if (!this.visible || this.renaming) return;
    const model = this.model;
    const now = model.now();
    const rows = model.rows();
    const query = this.search.value;
    const current = model.current ?? '';
    const stamp = JSON.stringify([rows, current, query, model.creating(), model.error, Math.floor(now / 60)]);
    if (stamp === this.stamp) return;
    this.stamp = stamp;
    this.newButton.textContent = model.creating() ? 'Starting…' : '+ New project';
    this.newButton.disabled = model.creating();
    this.error.textContent = model.error;
    this.error.hidden = !model.error;
    const shown = rows.filter((row) => matchesSearch(row, query, now));
    const groups: HTMLElement[] = [];
    for (const group of groupByDay(shown, now, 'started')) {
      const section = node('section', 'sessions-group');
      section.setAttribute('aria-label', group.label);
      const grid = node('div', 'lib-grid');
      for (const row of group.rows) grid.append(this.card(row.id, rowView(row, current, now), model.opening(row.id)));
      section.append(node('h2', 'sessions-day', group.label), grid);
      groups.push(section);
    }
    if (!shown.length) groups.push(node('p', 'lib-empty', query.trim() ? 'No project matches.' : model.list ? 'No projects yet.' : 'Reading the projects…'));
    this.list.replaceChildren(...groups);
  }

  private card(id: string, view: ReturnType<typeof rowView>, opening: boolean): HTMLElement {
    const card = node('div', 'lib-card session-card');
    card.dataset.sid = id;
    if (view.current) card.setAttribute('aria-current', 'true');
    const open = node('button', 'session-card-open');
    open.type = 'button';
    open.title = view.title;
    open.setAttribute('aria-label', view.current ? `${view.name}, open now: back to it` : `Open ${view.name}`);
    const picture = node('span', 'lib-picture session-card-picture');
    if (view.thumb) {
      const img = document.createElement('img');
      img.alt = '';
      img.loading = 'lazy';
      img.decoding = 'async';
      img.src = view.thumb;
      img.addEventListener('error', () => { picture.replaceChildren(cameraIcon()); picture.dataset.icon = 'camera'; }, { once: true });
      picture.append(img);
    } else {
      picture.append(cameraIcon());
      picture.dataset.icon = 'camera';
    }
    if (view.current) picture.append(node('span', 'session-card-badge', 'Open now'));
    else if (view.working) picture.append(node('span', 'session-card-badge', 'Working'));
    const words = node('span', 'lib-card-words');
    const name = node('span', 'lib-card-title', view.name);
    if (view.unnamed) name.dataset.unnamed = '';
    words.append(name, node('span', 'lib-card-meta', opening ? 'Opening…' : `Last used ${view.lastUsed}`));
    if (view.counts) words.append(node('span', 'lib-card-meta', view.counts));
    open.append(picture, words);
    open.addEventListener('click', () => {
      if (view.current) { this.go('#'); return; }
      if (this.model.switchTo(id)) this.go('#');
    });
    const rename = node('button', 'session-card-rename', '✎');
    rename.type = 'button';
    rename.title = 'Rename';
    rename.setAttribute('aria-label', `Rename ${view.name}`);
    rename.addEventListener('click', () => this.rename(id, name));
    card.append(open, rename);
    return card;
  }

  /** Rename a session in place on its card: Enter saves, Escape cancels. */
  private rename(id: string, name: HTMLElement): void {
    const row = this.model.rows().find((r) => r.id === id);
    if (!row || this.renaming) return;
    const said = sessionName(row, this.model.now());
    this.renaming = inlineRename(name, row.name, `Name the project ${said}`, (value) => {
      this.renaming = null;
      if (value !== null) this.model.rename(id, value);
      this.stamp = '';
      this.render();
    });
    if (!row.name.trim()) this.renaming.placeholder = said;
    this.renaming.focus();
    this.renaming.select?.();
  }
}
