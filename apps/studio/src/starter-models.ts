// The small-models offer (Will, 2026-10-26: "a request to download a few light models to some folder that they can
// reject and do later"): in the first-launch Welcome under the three ways in, and for people past it as one row at the
// top of Models until answered. Not a modal: the person can start at once. Its answer is kept in the data home
// (orchestrator/onboarding.py `models_offer`, the onboarding-models command) so it is asked once.
//
// Every word of a row is the capabilities wire's (the manifest's name and `what`, readiness's size via sizeWords); this
// file only says which entries to offer (STARTERS) and decides nothing a model could judge. Which are ticked follows one
// rule read from the wire (`ticksByDefault`), never a list. Downloading is the ordinary model-download and model-turn-on
// commands, in their one queue; the rows then show the measured progress with the lit cell, then Ready.
import './starter-models.css';
import { litCell, type LitCellState } from './lit-cell';
import { progressWords, sizeWords } from './size-words';
import type { CapabilitiesWire, DownloadRow, InstallRow, ModelRow } from './protocol';
import { modelSection, uncheckedWords } from './run-spec';

/** The entries the offer lists, in order: the built-in method, the two small models the workspace uses by itself, and
 * two larger cell and nucleus models a person may want first. Their words, sizes, licences and ticks are the wire's. */
export const STARTERS = ['basic-bright-objects', 'edgetam', 'dinov2-small', 'instanseg-brightfield', 'cellpose-sam'] as const;
/** "Small": what is ticked by default downloads less than this. */
export const SMALL_BYTES = 100e6;

export interface StarterAnswer { answered: boolean; answer: 'not-now' | 'download' | null; at: string | null; models: string[] }
export type StarterState = 'included' | 'ready' | 'unchecked' | 'offered' | 'queued' | 'downloading' | 'installing' | 'failed';
export interface StarterRow {
  id: string; name: string; what: string; tier: string;
  state: StarterState;
  /** What its download is, in bytes (readiness's download steps; 0 when nothing is downloaded). */
  bytes: number;
  /** It also needs its own software installed (an install step): its size is not known here. */
  install: boolean;
  /** Ticked when the offer opens (`ticksByDefault`). */
  ticked: boolean;
  /** The words beside it: "56 MB", "16 MB, plus its own software", "1.2 GB · non-commercial use only", "Included". */
  size: string;
  /** While it downloads: the server's measured bytes ("23 of 56 MB"), or a step's own words. */
  progress?: { fraction: number | null; words: string };
  /** A failed download or install: its own words. */
  failed?: string;
  /** Which existing command gets it: a download alone, or Turn on (download and its own software). */
  how: 'download' | 'turn-on';
}

const TIER_WORDS: Record<string, string> = { noncommercial: 'non-commercial use only', terms: 'its own terms' };

function downloadBytes(row: ModelRow): number {
  const steps = row.steps.filter((s) => s.kind === 'download');
  return steps.length ? steps.reduce((sum, s) => sum + (s.bytes ?? 0), 0) || row.size : 0;
}

function needsInstall(row: ModelRow): boolean {
  return row.steps.some((s) => s.kind === 'install' && !s.onAgain);
}

/** Steps the offer cannot take for the person (a key, a licence, a channel's Allow): such an entry is not offered here. */
function needsPerson(row: ModelRow): boolean {
  return row.steps.some((s) => s.kind === 'key' || s.kind === 'licence' || s.kind === 'trust' || s.kind === 'edition');
}

/** The default rule (Will's brief, 2026-10-26): ticked only when it is the default of a role the workspace uses by
 * itself (a role that powers a capability whose actor is "I, in the background"), its licence tier is open, it needs no
 * install of its own, and its download is under 100 MB. Read from the wire, so a catalog change moves the ticks. */
export function ticksByDefault(row: ModelRow, wire: CapabilitiesWire): boolean {
  const actors = new Map(wire.capabilities.map((c) => [c.id, c.actor]));
  const roles = new Map(wire.roles.map((r) => [r.id, r]));
  const usedByItself = row.usedFor.some((u) => {
    const role = roles.get(u.role);
    return !!role && !role.fixed && role.default === u.option && role.powers.some((p) => actors.get(p) === 'auto');
  });
  const bytes = downloadBytes(row);
  return usedByItself && row.tier === 'open' && !needsInstall(row) && !needsPerson(row) && bytes > 0 && bytes < SMALL_BYTES;
}

function sizeSaid(row: ModelRow, bytes: number, install: boolean): string {
  const parts = [`${sizeWords(bytes)}${install ? ', plus its own software' : ''}`];
  if (TIER_WORDS[row.tier]) parts.push(TIER_WORDS[row.tier]);
  return parts.join(' · ');
}

/** The offer's rows from the wire: the entries of STARTERS this edition has, each with its state now. An entry the
 * edition excludes, or one broken here, is left out. */
export function starterRows(wire: CapabilitiesWire | null | undefined): StarterRow[] {
  if (!wire?.models) return [];
  const downloads = new Map<string, DownloadRow>((wire.setup?.downloads ?? []).map((d) => [d.model, d]));
  const installs = new Map<string, InstallRow>((wire.setup?.installs ?? []).map((i) => [i.model, i]));
  const rows: StarterRow[] = [];
  for (const id of STARTERS) {
    const row = wire.models.find((m) => m.id === id);
    if (!row || row.status === 'unavailable' || row.status === 'broken') continue;
    const bytes = downloadBytes(row), install = needsInstall(row);
    if (row.status !== 'ready' && needsPerson(row)) continue;
    const base = { id, name: row.name, what: row.what, tier: row.tier, bytes, install, how: (install ? 'turn-on' : 'download') as StarterRow['how'] };
    const download = downloads.get(id) ?? row.download, installing = installs.get(id);
    let state: StarterState = 'offered', progress: StarterRow['progress'], failed: string | undefined;
    // Ready as Models says it: only once Hooke's own check passed here; before that, Downloaded · not checked (ticket 67).
    if (row.status === 'ready') state = row.size > 0 || row.kind === 'model' ? (modelSection(row) === 'unchecked' ? 'unchecked' : 'ready') : 'included';
    else if (download && (download.state === 'downloading' || download.state === 'checking')) {
      state = 'downloading';
      progress = download.state === 'checking' ? { fraction: null, words: 'Checking the files' }
        : { fraction: download.total ? download.got / download.total : null, words: download.total ? progressWords(download.got, download.total) : download.says };
    } else if (download?.state === 'queued') { state = 'queued'; progress = { fraction: null, words: 'Waiting to download' }; }
    else if (installing && (installing.state === 'queued' || installing.state === 'installing')) {
      state = 'installing'; progress = { fraction: null, words: installing.says };
    } else if (download?.state === 'failed') { state = 'failed'; failed = download.says; }
    else if (installing?.state === 'failed') { state = 'failed'; failed = installing.says; }
    const size = state === 'included' ? 'Included' : state === 'ready' ? 'Ready' : state === 'unchecked' ? uncheckedWords(row)
      : sizeSaid(row, bytes, install);
    rows.push({ ...base, state, size, ticked: state === 'offered' && ticksByDefault(row, wire), progress, failed });
  }
  return rows;
}

/** Bytes to download for the ticked rows. */
export function chosenBytes(rows: StarterRow[], ticked: ReadonlySet<string>): number {
  return rows.filter((r) => ticked.has(r.id) && (r.state === 'offered' || r.state === 'failed')).reduce((sum, r) => sum + r.bytes, 0);
}

/** "Download 144 MB": the button follows the ticks. */
export function downloadLabel(rows: StarterRow[], ticked: ReadonlySet<string>): string {
  const bytes = chosenBytes(rows, ticked);
  return bytes ? `Download ${sizeWords(bytes)}` : 'Download';
}

/** "EdgeTAM and DINOv2 small, 144 MB": what Download gets, for the one-row offer whose list is behind Choose. */
export function pickedWords(rows: StarterRow[], ticked: ReadonlySet<string>): string {
  const names = rows.filter((r) => ticked.has(r.id) && (r.state === 'offered' || r.state === 'failed')).map((r) => r.name);
  if (!names.length) return 'Nothing ticked: Choose picks what to download.';
  const said = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0];
  return `${said}, ${sizeWords(chosenBytes(rows, ticked))}`;
}

/** Asked only while unanswered and something can be downloaded (nothing to offer is no question). */
export function offerShown(rows: StarterRow[], answer: StarterAnswer | null): boolean {
  return !!answer && !answer.answered && rows.some((r) => r.state === 'offered');
}

/** "~/MicroscopeMind-Beta/catalog/models": where model files are kept, from a model's own folder on the wire. */
export function modelsFolder(wire: CapabilitiesWire | null | undefined): string {
  const folder = wire?.models?.find((m) => m.folder && !m.mine)?.folder ?? '';
  const parent = folder.replace(/\/[^/]+\/?$/, '');
  return parent.replace(/^\/Users\/[^/]+\//, '~/');
}

export const WORDS = {
  heading: 'Add a few small models?',
  lead: 'They run on this Mac. With them, a click outlines what you point at, and Hooke can compare how things look. '
    + 'Without them everything else works, and each tool offers its download when you first use it.',
  source: 'Downloaded from Hugging Face and GitHub. Downloading sends none of your pictures; these models run on this Mac.',
  notNow: 'Not now',
  declined: 'No models downloaded. Models has them whenever you want them.',
  adding: 'Small models',
} as const;

// --- the shared state: the latest wire, the answer, and the commands (configured once by main.ts) -------------------
export interface StarterCommands {
  status: () => Promise<StarterAnswer>;
  answer: (answer: 'not-now' | 'download', models: string[]) => Promise<StarterAnswer>;
  download: (model: string) => Promise<unknown>;
  turnOn: (model: string) => Promise<unknown>;
  /** Show the models folder in the Finder. */
  reveal: () => Promise<unknown>;
  /** Open Models. */
  models: () => void;
}

let wire: CapabilitiesWire | null = null;
let answer: StarterAnswer | null = null;
let asking: Promise<void> | null = null;
let commands: StarterCommands | null = null;
const listeners = new Set<() => void>();
const changed = () => { for (const listen of [...listeners]) listen(); };

export function configureStarterModels(next: StarterCommands): void { commands = next; asking = null; answer = null; changed(); }
/** Every capabilities wire (main.ts): the rows and their progress follow it. */
export function noteCapabilities(next: CapabilitiesWire): void { wire = next; changed(); }
export function starterState(): { wire: CapabilitiesWire | null; answer: StarterAnswer | null } { return { wire, answer }; }
function readAnswer(): void {
  if (asking || !commands) return;
  asking = commands.status().then((a) => { answer = a; changed(); }).catch(() => { asking = null; });
}

// --- the panel ----------------------------------------------------------------------------------------------------
function el<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text) node.textContent = text;
  return node;
}

const CELL: Partial<Record<StarterState, LitCellState>> = { ready: 'ready', included: 'ready', unchecked: 'idle', queued: 'queued', downloading: 'setup', installing: 'working', failed: 'attention' };

/** The offer: `welcome` under the first-launch Welcome's ways in; `stage` under the empty Stage's ways in (where a fresh
 * profile lands: the current Project opens on launch); `row` at the top of Models (one row until answered, its list
 * behind Choose). It draws itself from the shared state. Answered here, the Welcome and the Stage collapse to one line
 * (Not now) or keep the rows they started with their progress (Download); answered anywhere else, every place stays
 * quiet: it is asked once. */
export function starterOffer(where: 'welcome' | 'stage' | 'row'): HTMLElement {
  // A region, not a <section>: Models' catalog view hides sections that hold no model card.
  const panel = el('div', `starter-offer starter-offer-in-${where}`);
  panel.setAttribute('role', 'region');
  panel.setAttribute('aria-label', WORDS.heading);
  let ticked: Set<string> | null = null, open = where !== 'row', busy = false, error = '', answeredHere = false;
  let chosen: string[] = []; // what this panel's Download started: its rows stay, with their progress
  let shownOnce = false; // a Welcome taken off the page (the launcher redrawn) stops listening; Models keeps its one row
  const render = () => {
    if (where === 'welcome' && shownOnce && !panel.isConnected) { listeners.delete(render); return; }
    if (panel.isConnected) shownOnce = true;
    readAnswer();
    const rows = starterRows(wire);
    if (!ticked && rows.length) ticked = new Set(rows.filter((r) => r.ticked).map((r) => r.id));
    const focused = panel.contains(document.activeElement) ? (document.activeElement as HTMLElement).dataset.focus ?? '' : '';
    panel.replaceChildren(...view(rows));
    if (focused) panel.querySelector<HTMLElement>(`[data-focus="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
  };
  const view = (rows: StarterRow[]): Node[] => {
    panel.hidden = false;
    if (!answer || !rows.length || (answer.answered && !answeredHere)) { panel.hidden = true; return []; }
    if (answer.answered && answer.answer === 'not-now') {
      if (where === 'row') { panel.hidden = true; return []; }
      const line = el('p', 'starter-offer-declined');
      const go = el('button', 'starter-offer-link', 'Models');
      go.type = 'button'; go.dataset.focus = 'models'; go.addEventListener('click', () => commands?.models());
      line.append('No models downloaded. ', go, ' has them whenever you want them.');
      return [line];
    }
    if (answer.answered) {
      // Downloading: the rows it started, each with its progress, then Ready. In Models the cards show it themselves.
      if (where === 'row') { panel.hidden = true; return []; }
      const started = new Set(chosen);
      const shown = rows.filter((r) => started.has(r.id));
      if (!shown.length) { panel.hidden = true; return []; }
      const out: Node[] = [el('h2', 'starter-offer-heading', WORDS.adding), list(shown, false), folderLine()];
      if (error) { const said = el('p', 'starter-offer-error', error); said.setAttribute('role', 'alert'); out.push(said); }
      return out;
    }
    if (!offerShown(rows, answer)) { panel.hidden = true; return []; }
    const head = el('div', 'starter-offer-head');
    head.append(el('h2', 'starter-offer-heading', WORDS.heading), el('p', 'starter-offer-lead', WORDS.lead));
    const actions = el('div', 'starter-offer-actions');
    const download = el('button', 'starter-offer-download', busy ? 'Starting…' : downloadLabel(rows, ticked!));
    download.type = 'button'; download.dataset.focus = 'download';
    download.disabled = busy || !chosenBytes(rows, ticked!);
    download.addEventListener('click', () => void start(rows));
    const later = el('button', 'starter-offer-later', WORDS.notNow);
    later.type = 'button'; later.dataset.focus = 'later'; later.disabled = busy;
    later.addEventListener('click', () => void decline());
    actions.append(download, later);
    if (where === 'row' && !open) {
      const choose = el('button', 'starter-offer-later', 'Choose');
      choose.type = 'button'; choose.dataset.focus = 'choose';
      choose.addEventListener('click', () => { open = true; render(); });
      actions.insertBefore(choose, later);
    }
    const out: Node[] = [head];
    if (open) out.push(list(rows, true), folderLine(), el('p', 'starter-offer-source', WORDS.source));
    else out.push(el('p', 'starter-offer-picked', pickedWords(rows, ticked!)));
    out.push(actions);
    if (error) { const said = el('p', 'starter-offer-error', error); said.setAttribute('role', 'alert'); out.push(said); }
    return out;
  };
  const list = (rows: StarterRow[], choosing: boolean): HTMLElement => {
    const ul = el('ul', 'starter-offer-rows');
    for (const row of rows) {
      const li = el('li', `starter-offer-row is-${row.state}`);
      li.dataset.starter = row.id;
      const mark = el('span', 'starter-offer-mark');
      if (choosing && (row.state === 'offered' || row.state === 'failed')) {
        const box = el('input');
        box.type = 'checkbox'; box.checked = ticked!.has(row.id); box.dataset.focus = `tick:${row.id}`;
        box.id = `starter-${where}-${row.id}`;
        box.addEventListener('change', () => { if (box.checked) ticked!.add(row.id); else ticked!.delete(row.id); render(); });
        mark.append(box);
      } else {
        const cell = CELL[row.state];
        mark.append(cell ? litCell(cell, { size: 12, fraction: row.progress?.fraction ?? null, decorative: true }) : litCell('idle', { size: 12, decorative: true }));
      }
      const text = el('label', 'starter-offer-text');
      if (choosing && (row.state === 'offered' || row.state === 'failed')) text.htmlFor = `starter-${where}-${row.id}`;
      text.append(el('strong', '', row.name), el('span', 'starter-offer-what', row.what));
      const said = row.state === 'failed' ? row.failed ?? 'It did not finish.' : row.progress?.words ?? row.size;
      const size = el('span', `starter-offer-size${row.progress ? ' is-progress' : ''}`, said);
      if (row.progress || row.state === 'failed') size.setAttribute('role', 'status');
      li.append(mark, text, size);
      if (!choosing && row.state === 'failed') {
        const again = el('button', 'starter-offer-later', 'Try again');
        again.type = 'button'; again.dataset.focus = `again:${row.id}`;
        again.addEventListener('click', () => void get(row));
        li.append(again);
      }
      ul.append(li);
    }
    return ul;
  };
  const folderLine = (): HTMLElement => {
    const line = el('p', 'starter-offer-folder');
    const free = wire?.setup?.disk?.freeBytes;
    line.append(`Kept in ${modelsFolder(wire) || 'the models folder'}${typeof free === 'number' ? ` · ${sizeWords(free)} free` : ''} · `);
    const show = el('button', 'starter-offer-link', 'Show in Finder');
    show.type = 'button'; show.dataset.focus = 'finder';
    show.addEventListener('click', () => void commands?.reveal());
    line.append(show);
    return line;
  };
  const get = (row: StarterRow) => (row.how === 'turn-on' ? commands!.turnOn(row.id) : commands!.download(row.id));
  const start = async (rows: StarterRow[]) => {
    if (!commands || busy) return;
    const picked = rows.filter((r) => ticked!.has(r.id) && (r.state === 'offered' || r.state === 'failed'));
    if (!picked.length) return;
    busy = true; error = ''; render();
    try {
      answer = await commands.answer('download', picked.map((r) => r.id));
      answeredHere = true;
      chosen = picked.map((r) => r.id);
      // One at a time into the app's own queue, which already waits while a click is answered.
      const failures: string[] = [];
      for (const row of picked) {
        try { await get(row); } catch (e) { failures.push(`${row.name}: ${e instanceof Error ? e.message : String(e)}`); }
      }
      error = failures.join(' ');
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally { busy = false; render(); }
  };
  const decline = async () => {
    if (!commands || busy) return;
    busy = true; error = ''; render();
    try { answer = await commands.answer('not-now', []); answeredHere = true; } catch (e) { error = e instanceof Error ? e.message : String(e); }
    finally { busy = false; render(); }
  };
  listeners.add(render);
  render();
  return panel;
}
