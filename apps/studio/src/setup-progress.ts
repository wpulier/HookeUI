// Setting up, at the top of Activity: every model the app is preparing now, so closing its setup sheet never loses it
// (docs/design/2026-10-23-part1-uiux-pass/README.md §B; Will, 2026-10-23: "closing the sheet must leave progress
// readily findable in Activity"). Drawn only from the capabilities wire's own setup state (turning, installs,
// downloads), in the sheet's words; it starts, stops and changes nothing.
import type { CapabilitiesWire } from './protocol';
import { sizeWords } from './modelspage';

export interface SetupInProgress { model: string; name: string; says: string; progress: string | null; where: string | null }

/** What is being set up now, one line a model: a running or waiting setup, an install, or a download. A failed setup
 * is not progress; its card says Needs attention. */
export function setupInProgress(wire: CapabilitiesWire | null | undefined): SetupInProgress[] {
  const setup = wire?.setup;
  if (!setup) return [];
  const rows = new Map<string, SetupInProgress>();
  const nameOf = (model: string, fallback: string) => wire?.models?.find((row) => row.id === model)?.name || fallback || model;
  const whereOf = (model: string) => wire?.models?.find((row) => row.id === model)?.where || null;
  const add = (model: string, name: string, says: string) => {
    if (!model || rows.has(model)) return;
    rows.set(model, { model, name: nameOf(model, name), says, progress: null, where: whereOf(model) });
  };
  for (const turning of Object.values(setup.turning ?? {})) if (turning && turning.state !== 'failed') add(turning.model, turning.label, turning.says);
  for (const card of [...(setup.shelf?.cards ?? []), ...(setup.shelf?.behind ?? [])]) {
    const turning = card.turning;
    if (turning && turning.state !== 'failed') add(turning.model, turning.label, turning.says);
  }
  for (const install of setup.installs ?? []) if (['queued', 'installing'].includes(install.state)) add(install.model, install.name, install.says);
  for (const download of setup.downloads ?? []) {
    if (!['queued', 'downloading', 'checking'].includes(download.state)) continue;
    add(download.model, download.name, download.says);
    const row = rows.get(download.model)!;
    const got = Math.max(0, Math.min(download.got, download.total || download.got));
    row.progress = download.state === 'checking' ? 'Checking downloaded files'
      : download.state === 'queued' ? 'Waiting to download'
      : download.total ? `${sizeWords(got)} of ${sizeWords(download.total)} · ${Math.floor(100 * got / download.total)}%`
      : `${sizeWords(got)} downloaded · total size not yet known`;
  }
  return [...rows.values()];
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = '') => {
  const node = document.createElement(tag); node.className = className; if (text) node.textContent = text; return node;
};

/** The section above Activity's list. It follows the list wherever the workspace moves it (into Data and back). */
export class SetupProgress {
  readonly root = el('section', 'setup-progress');
  private readonly list = el('ul', 'setup-progress-list');

  private readonly beside: HTMLElement;
  private readonly open: (model: string) => void;

  constructor(beside: HTMLElement, open: (model: string) => void) {
    this.beside = beside; this.open = open;
    this.root.setAttribute('aria-label', 'Setting up');
    this.root.append(el('h3', 'setup-progress-title', 'Setting up'), this.list);
    this.root.hidden = true;
  }

  update(wire: CapabilitiesWire | null | undefined): void {
    const rows = setupInProgress(wire);
    this.list.replaceChildren(...rows.map((row) => {
      const item = el('li', 'setup-progress-row');
      item.dataset.model = row.model;
      const words = el('div', 'setup-progress-words');
      words.append(el('strong', 'setup-progress-name', row.name), el('span', 'setup-progress-says', row.says));
      if (row.progress) words.append(el('span', 'setup-progress-amount', row.progress));
      if (row.where) words.append(el('span', 'setup-progress-where', row.where));
      const see = el('button', 'mm-control setup-progress-open', 'See progress') as HTMLButtonElement;
      see.type = 'button';
      see.setAttribute('aria-label', `See setup progress for ${row.name}`);
      see.addEventListener('click', () => this.open(row.model));
      item.append(words, see);
      return item;
    }));
    this.root.hidden = rows.length === 0;
    const parent = this.beside.parentElement; // followed when the workspace moves Activity into Data and back
    if (parent && this.root.parentElement !== parent) parent.insertBefore(this.root, this.beside);
  }
}
