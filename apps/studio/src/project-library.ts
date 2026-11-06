/** Open Project as a library of the person's Projects (approved by Will, 5 Nov 2026): a cover for each (its main
 * visual, else its page, else its newest picture, else a tile), what it is, when it was updated and created, and what
 * is in it. A left column narrows the list; search and sort sit above. Opening a card opens that Project and starts
 * nothing. A Project folder that is not listed opens through the existing folder dialog (project-open.ts). */
import './project-library.css';
import type {SessionRow} from './protocol';
import {sessionName} from './sessions';

export type LibraryCover = {id:string;revision:number;digest?:string;kind?:string|null;sha256?:string;unavailable?:boolean};
export type LibraryRow = {session:string;about?:string|null;page?:LibraryCover|null;visual?:LibraryCover|null;studies?:number|null;savedIn?:'folder'|'mac'|null;problem?:string};
export interface ProjectLibraryOptions {
  rows(): readonly SessionRow[];
  current(): string;
  read(signal: AbortSignal): Promise<LibraryRow[]>;
  open(session: string): Promise<boolean>;
  openFolder(): void;
  newProject(): void;
}
type Filter = 'all'|'week'|'page'|'studies'|'away'|'folder'|'mac';
type Order = 'updated'|'created'|'name';
const FIRST = 12;
const WEEK = 7 * 86400;
const node = <K extends keyof HTMLElementTagNameMap>(tag:K, text = '', cls = '') => {
  const el = document.createElement(tag); el.textContent = text; el.className = cls; return el;
};
/** "today 8:10 PM", "Nov 3", "Oct 28, 2025": the date words of every card. */
export function dayWords(at: number, now = Date.now() / 1000): string {
  if (!Number.isFinite(at) || at <= 0) return 'not recorded';
  const date = new Date(at * 1000), today = new Date(now * 1000);
  if (date.toDateString() === today.toDateString()) return `today ${date.toLocaleTimeString(undefined, {hour: 'numeric', minute: '2-digit'})}`;
  return date.toLocaleDateString(undefined, date.getFullYear() === today.getFullYear() ? {month: 'short', day: 'numeric'} : {month: 'short', day: 'numeric', year: 'numeric'});
}
/** Two letters for a Project with no picture: its first two words' initials, or its first two letters. */
export function initials(name: string): string {
  const words = name.replace(/[^\p{L}\p{N}\s]/gu, ' ').split(/\s+/).filter(Boolean);
  if (words.length >= 2) return (words[0][0] + words[1][0]).toUpperCase();
  return (words[0] ?? '?').slice(0, 2).replace(/^./, c => c.toUpperCase());
}
/** Initials that tell same-named Projects apart: "Beta readiness — fresh start" and "— local research" become BF and
 * BL when both would read BR, taking the second letter from what follows the dash or colon. */
export function distinctInitials(names: readonly string[]): Map<string, string> {
  const base = new Map(names.map(name => [name, initials(name)]));
  const counts = new Map<string, number>();
  for (const value of base.values()) counts.set(value, (counts.get(value) ?? 0) + 1);
  for (const name of names) {
    if ((counts.get(base.get(name)!) ?? 0) < 2) continue;
    const [head, ...rest] = name.split(/\s[—–-]\s|:\s/);
    const tail = rest.join(' ').replace(/[^\p{L}\p{N}\s]/gu, ' ').trim();
    if (head && tail) base.set(name, ((head.trim()[0] ?? '') + tail[0]).toUpperCase());
  }
  return base;
}
const hue = (id: string) => [...id].reduce((h, c) => (h * 31 + c.charCodeAt(0)) >>> 0, 7) % 4;
export function inWords(row: SessionRow, studies?: number | null): string {
  const inputs = row.counts?.sources ?? 0, made = row.counts?.made ?? 0;
  const parts = [inputs ? `${inputs} ${inputs === 1 ? 'input' : 'inputs'}` : '',
    studies ? `${studies} ${studies === 1 ? 'Study' : 'Studies'}` : '', made ? `${made} ${made === 1 ? 'result' : 'results'}` : ''].filter(Boolean);
  return parts.join(' · ') || 'Nothing added yet';
}
/** What the card shows as its cover, in order: the main visual, the page, the newest picture, a tile. */
export function coverOf(row: SessionRow, extra?: LibraryRow): {kind:'image'|'page'|'thumb'|'tile';src?:string} {
  const visual = extra?.visual && !extra.visual.unavailable ? extra.visual : null;
  const session = encodeURIComponent(row.id);
  if (visual?.kind === 'image' && visual.sha256)
    return {kind: 'image', src: `/api/scientific-files/${encodeURIComponent(visual.id)}/media?session=${session}&revision=${visual.revision}&sha256=${encodeURIComponent(visual.sha256)}`};
  const page = visual?.kind === 'page' ? visual : extra?.page && !extra.page.unavailable ? extra.page : null;
  if (page) return {kind: 'page', src: `/api/scientific-files/${encodeURIComponent(page.id)}/page?session=${session}&revision=${page.revision}`};
  if (row.thumb) return {kind: 'thumb', src: row.thumb};
  return {kind: 'tile'};
}

export function showProjectLibrary(options: ProjectLibraryOptions): HTMLDialogElement {
  const opener = document.activeElement as HTMLElement | null;
  const dialog = node('dialog', '', 'study-overlay project-library');
  dialog.setAttribute('aria-label', 'Projects');
  const header = node('header', '', 'study-overlay-header');
  const close = node('button', '×', 'study-close'); close.type = 'button'; close.setAttribute('aria-label', 'Close Projects');
  close.addEventListener('click', () => dialog.close());
  header.append(node('h2', 'Projects'), close);
  const body = node('div', '', 'pl-body');
  const nav = node('nav', '', 'pl-nav'); nav.setAttribute('aria-label', 'Show');
  const main = node('section', '', 'pl-main');
  body.append(nav, main);
  dialog.append(header, body);
  document.body.append(dialog);

  let filter: Filter = 'all', order: Order = 'updated', query = '', more = false, busy = '';
  let extras = new Map<string, LibraryRow>(), readProblem = '', marks = new Map<string, string>();
  const controller = new AbortController();
  const search = node('input', '', 'pl-search'); search.type = 'search'; search.id = 'project-library-search';
  search.placeholder = 'Search your Projects by name or what they are'; search.setAttribute('aria-label', 'Search your Projects');
  search.addEventListener('input', () => { query = search.value.trim().toLocaleLowerCase(); more = false; draw(); });
  const sort = node('select', '', 'pl-sort'); sort.id = 'project-library-order'; sort.setAttribute('aria-label', 'Order');
  for (const [value, words] of [['updated', 'Last updated'], ['created', 'Date created'], ['name', 'Name']] as const) {
    const option = node('option', words); option.value = value; sort.append(option);
  }
  sort.addEventListener('change', () => { order = sort.value as Order; more = false; draw(); });
  const create = node('button', 'New project', 'pl-primary'); create.type = 'button';
  create.addEventListener('click', () => { dialog.close(); options.newProject(); });
  const lead = node('p', 'Everything you’ve worked on. Open one to carry on where you left off; opening starts nothing.', 'pl-lead');
  const tools = node('div', '', 'pl-tools'); tools.append(search, sort, create);
  const notice = node('p', '', 'pl-notice'); notice.setAttribute('role', 'status');
  const results = node('div', '', 'pl-results');
  main.append(lead, tools, notice, results);

  const now = () => Date.now() / 1000;
  const name = (row: SessionRow) => sessionName(row, now());
  const matches = (row: SessionRow, by: Filter): boolean => {
    const extra = extras.get(row.id);
    if (by !== 'away' && row.archived) return false;
    if (by === 'away') return !!row.archived;
    if (by === 'week') return now() - row.lastActive < WEEK;
    if (by === 'page') return !!extra?.page;
    if (by === 'studies') return !!extra?.studies;
    if (by === 'folder') return extra?.savedIn === 'folder';
    if (by === 'mac') return extra?.savedIn === 'mac';
    return true;
  };
  function drawNav(rows: readonly SessionRow[]) {
    nav.replaceChildren();
    const item = (by: Filter, words: string) => {
      const b = node('button', '', by === filter ? 'pl-filter pl-on' : 'pl-filter'); b.type = 'button';
      b.setAttribute('aria-pressed', String(by === filter));
      b.append(node('span', words), node('span', String(rows.filter(row => matches(row, by)).length), 'pl-count'));
      b.addEventListener('click', () => { filter = by; more = false; draw(); });
      nav.append(b);
    };
    nav.append(node('span', 'Your Projects', 'pl-heading'));
    item('all', 'All'); item('week', 'Updated this week'); item('page', 'With a page'); item('studies', 'With Studies'); item('away', 'Put away');
    if (extras.size) { nav.append(node('span', 'Saved in', 'pl-heading')); item('folder', 'Hooke Projects folder'); item('mac', 'This Mac only'); }
    const elsewhere = node('div', '', 'pl-elsewhere');
    const folder = node('button', 'Open a folder…', 'pl-link'); folder.type = 'button';
    folder.addEventListener('click', () => { dialog.close(); options.openFolder(); });
    elsewhere.append(folder, node('p', 'A Project folder that isn’t listed here, from a drive or a colleague, or one you moved.', 'pl-hint'));
    nav.append(elsewhere);
  }
  function card(row: SessionRow): HTMLElement {
    const extra = extras.get(row.id), current = row.id === options.current();
    const button = node('button', '', 'pl-card'); button.type = 'button'; button.dataset.session = row.id;
    const cover = node('span', '', 'pl-cover'); fillCover(cover, row);
    if (current) cover.append(node('span', 'Open now', 'pl-badge pl-now'));
    else if (row.archived) cover.append(node('span', 'Put away', 'pl-badge'));
    cover.append(node('span', inWords(row, extra?.studies), 'pl-chip'));
    const title = node('strong', name(row), 'pl-title');
    const dates = node('span', '', 'pl-dates');
    const updated = node('span'); updated.append(node('b', 'Updated'), node('span', ` ${dayWords(row.lastActive)}`));
    const created = node('span'); created.append(node('b', 'Created'), node('span', ` ${dayWords(row.started)}`));
    dates.append(updated, created);
    button.append(cover, title);
    if (extra?.about) button.append(node('span', extra.about, 'pl-about'));
    button.append(dates);
    button.setAttribute('aria-label', [name(row), current ? 'open now' : '', `updated ${dayWords(row.lastActive)}`, inWords(row, extra?.studies)].filter(Boolean).join(', '));
    if (busy === row.id) { button.disabled = true; button.classList.add('pl-opening'); }
    button.addEventListener('click', () => {
      if (busy) return;
      busy = row.id;
      const opening = openingView(row, cover);
      notice.textContent = '';
      void options.open(row.id).then(opened => {
        busy = '';
        if (opened) { opening.land(() => dialog.close()); return; }
        opening.undo();
        notice.textContent = `${name(row)} did not open. Your current Project is unchanged.`; draw();
      }, (error: unknown) => {
        busy = ''; opening.undo();
        notice.textContent = error instanceof Error ? error.message : `${name(row)} did not open.`; draw();
      });
    });
    return button;
  }
  /** Opening a Project: its cover grows from the card to fill the sheet with its name and "Opening…", then the sheet
   * fades as the Project's front page appears behind it. A refusal shrinks it back. Reduced motion skips the motion. */
  function openingView(row: SessionRow, cover: HTMLElement) {
    const layer = node('div', '', 'pl-opening'); layer.setAttribute('role', 'status');
    const art = node('span', '', 'pl-opening-cover'); fillCover(art, row);
    const words = node('div', '', 'pl-opening-words');
    words.append(node('span', 'Opening', 'pl-opening-eyebrow'), node('strong', name(row)));
    const about = extras.get(row.id)?.about; if (about) words.append(node('span', about, 'pl-opening-about'));
    words.append(node('span', '', 'pl-opening-bar'));
    layer.append(art, words);
    main.append(layer);
    const still = typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;
    const from = cover.getBoundingClientRect?.(), to = main.getBoundingClientRect?.();
    if (!still && from && to && typeof art.animate === 'function' && to.width && to.height) {
      const dx = from.left - to.left + main.scrollLeft, dy = from.top - to.top;
      art.animate([{transform: `translate(${dx}px, ${dy}px) scale(${from.width / to.width}, ${from.height / to.height})`, borderRadius: '10px'},
        {transform: 'none', borderRadius: '0'}], {duration: 420, easing: 'cubic-bezier(.2,.8,.2,1)'});
      words.animate([{opacity: 0, transform: 'translateY(12px)'}, {opacity: 1, transform: 'none'}], {duration: 320, delay: 180, fill: 'both', easing: 'ease-out'});
    }
    return {
      land(done: () => void) {
        if (still || typeof dialog.animate !== 'function') { done(); return; }
        const fade = dialog.animate([{opacity: 1, transform: 'none'}, {opacity: 0, transform: 'scale(1.015)'}], {duration: 260, easing: 'ease-in'});
        fade.onfinish = () => done();
      },
      undo() { layer.remove(); },
    };
  }
  /** The cover itself (main visual, page, newest picture or tile), for a card and for its opening. */
  function fillCover(target: HTMLElement, row: SessionRow) {
    const shown = coverOf(row, extras.get(row.id));
    if (shown.kind === 'page') {
      const frame = node('iframe', '', 'pl-frame'); frame.src = shown.src!; frame.setAttribute('sandbox', 'allow-scripts');
      frame.setAttribute('referrerpolicy', 'no-referrer'); frame.tabIndex = -1; frame.setAttribute('aria-hidden', 'true'); frame.loading = 'lazy';
      target.append(frame);
    } else if (shown.src) {
      const img = node('img', '', 'pl-img'); img.src = shown.src; img.alt = ''; img.loading = 'lazy'; img.decoding = 'async';
      img.addEventListener('error', () => { img.remove(); target.prepend(tile(row)); });
      target.append(img);
    } else target.append(tile(row));
  }
  function tile(row: SessionRow): HTMLElement {
    const el = node('span', marks.get(name(row)) ?? initials(name(row)), `pl-tile pl-tile-${hue(row.id)}`); el.setAttribute('aria-hidden', 'true'); return el;
  }
  function draw() {
    const all = options.rows().filter(row => !row.launcherBootstrap);
    marks = distinctInitials(all.map(name));
    drawNav(all);
    const shown = all.filter(row => matches(row, filter)).filter(row => !query ||
      `${name(row)} ${extras.get(row.id)?.about ?? ''}`.toLocaleLowerCase().includes(query));
    const key = (row: SessionRow) => order === 'created' ? row.started : row.lastActive;
    shown.sort((a, b) => order === 'name' ? name(a).localeCompare(name(b)) : key(b) - key(a));
    if (readProblem && !notice.textContent) notice.textContent = readProblem;
    results.replaceChildren();
    if (!shown.length) { results.append(node('p', query ? `No Project matches “${search.value.trim()}”.` : 'No Projects here yet.', 'pl-empty')); return; }
    const groups: [string, SessionRow[]][] = order === 'name' ? [['All Projects', shown]] : [
      [order === 'created' ? 'Created this week' : 'This week', shown.filter(row => now() - key(row) < WEEK)],
      ['Earlier', shown.filter(row => now() - key(row) >= WEEK)]];
    const visible = new Set(more ? shown : shown.slice(0, FIRST));
    for (const [heading, rows] of groups) {
      if (!rows.length) continue;
      const head = node('div', '', 'pl-section'); head.append(node('h3', heading), node('span', String(rows.length), 'pl-count'));
      const grid = node('div', '', 'pl-grid');
      for (const row of rows) if (visible.has(row)) grid.append(card(row));
      if (grid.children.length) results.append(head, grid);
    }
    if (shown.length > visible.size) {
      const show = node('button', `Show ${shown.length - visible.size} more`, 'pl-more'); show.type = 'button';
      show.addEventListener('click', () => { more = true; draw(); });
      results.append(show);
    }
  }
  // The Projects list can still be arriving when the sheet opens (and changes while it is open): follow it.
  const listed = () => options.rows().map(row => `${row.id}:${row.lastActive}:${row.archived ? 1 : 0}:${row.counts?.sources ?? ''}:${row.thumb ?? ''}`).join('|');
  let seen = listed();
  const follow = setInterval(() => { if (busy) return; const now = listed(); if (now !== seen) { seen = now; draw(); } }, 600);
  dialog.addEventListener('close', () => { clearInterval(follow); controller.abort(); dialog.remove(); if (opener?.isConnected) opener.focus(); });
  draw();
  void options.read(controller.signal).then(rows => {
    extras = new Map(rows.map(row => [row.session, row])); readProblem = ''; draw();
  }, (error: unknown) => {
    if (controller.signal.aborted) return;
    readProblem = `Covers and descriptions could not be read. ${error instanceof Error ? error.message : ''}`.trim(); draw();
  });
  dialog.showModal();
  search.focus();
  return dialog;
}
