// "Made in this session" (once "Made today"; ids keep that name): the session's one index (orchestrator/resources.py;
// Amendments 2.7 and 3), read the way every reader reads it (GET /api/resources), in the tray. Each kind with its
// entries by plain title; what can be shown opens as its presentation (the same verb the brain uses, done as the
// user's own and only once the screen shows it). A session may span days: the entries then sit under quiet day
// dividers. Nothing here keeps data of its own.
import type { ClientCommand } from './protocol';
import type { Panel } from './panels';
import { portablePictureControls } from './portable-picture';
import { dayLabel, daysAgo } from './sessions';

export interface ResourceEntry {
  id: string;
  kind: string;
  title: string;
  time?: string | null;
  summary: string;
  /** Its row in plain words when the summary is technical (cell outlines: "312 outlined, 298 still in place"). */
  plain?: string;
  state: string;
  placement?: string | null;
  actions: string[];
  day?: string;
  version?: number;
  pictures?: unknown[];
  /** A result that says which capabilities made it: it can be downloaded with them (resources.Entry.portable). */
  portable?: boolean;
  /** Canonical resource index flag: this retained result has a producer-bound measurement table. */
  table?: boolean;
  at?: number;
  madeBy?: { by?: string; for?: string; tool?: string; implementation?: string; requestedBy?: 'user'; web?: { page?: string } };
  media?: string;
  source?: { frame?: number | string; boot?: string; kind?: string; media?: string };
  readBy?: { researcher: string; name: string; at: number; version: number | null; how: string }[];
  /** Data the session made to keep and reuse (CONTEXT.md "Product"); only these are listed here. Decided by the
   * server (produces.is_product); an entry from an older server without it counts as one. */
  product?: boolean;
}

/** Made in this session belongs to the session, not the calendar day (the user's call, 2026-10-11): what was made
 * since the session began (`sessionStart`; a new session begins a new one, a restart does not) is listed, and what came
 * before stays kept and in the index, one step away under "Earlier". */
export function sessionSplit(entries: ResourceEntry[], sessionStart: number | null): { current: ResourceEntry[]; earlier: ResourceEntry[] } {
  if (sessionStart == null) return { current: entries, earlier: [] };
  const earlier = entries.filter((entry) => entry.at != null && entry.at < sessionStart);
  return { current: entries.filter((entry) => !earlier.includes(entry)), earlier };
}

/** Made in this session lists products only: named outlines, answers, outcomes and the like stay in the index for the brain
 * and researchers, not here (the user's decision of 2026-10-11). */
export function products(entries: ResourceEntry[]): ResourceEntry[] {
  return entries.filter((entry) => entry.product !== false);
}

export interface Summary {
  kinds: { kind: string; name: string; count: number }[];
  couldMake: string[];
  running: string[];
  unreadable?: string[];
}

/** The entries grouped by kind, newest first within each, in the summary's order of kinds. */
export function grouped(entries: ResourceEntry[], kinds: { kind: string; name: string }[]): { name: string; entries: ResourceEntry[] }[] {
  return kinds
    .map((k) => ({ name: k.name, entries: entries.filter((e) => e.kind === k.kind).reverse() }))
    .filter((g) => g.entries.length > 0);
}

/** One exact target for opening and matching retained views, including reports with images. */
export function resourceTarget(entry: ResourceEntry) {
  return { day: entry.day ?? '', id: entry.id, version: entry.version ?? null,
    picture: entry.kind === 'research' ? null : entry.pictures?.length ? 0 : null };
}

/** What a listener of Made in this session is given: its products, the index's summary and when the session began. */
export type DataListener = (products: ResourceEntry[], summary: Summary, sessionStart: number | null) => void;

export class MadeToday implements Panel {
  readonly id = 'made-today';
  private readonly root = document.getElementById('made-today')!;
  private readonly list = document.getElementById('made-today-list')!;
  private readonly note = document.getElementById('made-today-note')!;
  private readonly send: (command: ClientCommand) => void;
  private readonly openResource?: (source: { day: string; id: string; version: number | null; picture: number | null }, title: string) => void;
  private entries: ResourceEntry[] = [];
  private summary: Summary = { kinds: [], couldMake: [], running: [] };
  private filter = 'All';
  private sessionStart: number | null = null;
  private showEarlier = false;
  private refreshId = 0;
  private refreshTimer: ReturnType<typeof setTimeout> | null = null;
  onCount: (count: number) => void = () => {};
  /** Opens a measured item's table (the Measurements page): cell outlines and motion have one. */
  onTable: (id: string) => void = () => {};
  /** Others that list what was made (the side bar's column) read it from here: no second fetch. */
  private readonly dataListeners = new Set<DataListener>();
  private read = false; // the index has been read (or emptied for a new session) at least once
  onSummary: (text: string) => void = () => {};
  private viewStamp = '';
  private renderStamp = '';
  private dataStamp = '';
  changed(): void {
    if (this.refreshTimer !== null) return;
    this.refreshTimer = setTimeout(() => { this.refreshTimer = null; void this.refresh(); }, 350);
  }
  visibilityChanged(): void {
    const stamp = JSON.stringify(this.views());
    if (stamp === this.viewStamp) return;
    this.viewStamp = stamp; this.render(this.entries, this.summary);
  }
  private readonly filters = document.createElement('div');
  views: () => { id: string; title: string; visible: boolean; resource: unknown }[] = () => [];
  restore: (id: string) => void = () => {};

  constructor(send: (command: ClientCommand) => void,
              openResource?: (source: { day: string; id: string; version: number | null; picture: number | null }, title: string) => void) {
    this.send = send;
    this.openResource = openResource;
    this.filters.className = 'made-filters'; this.filters.id = 'made-today-filters';
    this.filters.setAttribute('aria-label', 'Filter retained work');
    for (const name of ['All', 'On screen', 'Pictures', 'Overlays', 'Measurements', 'AI research', 'Yours']) {
      const button = document.createElement('button'); button.type = 'button'; button.textContent = name;
      button.addEventListener('click', () => { this.filter = name; this.render(this.entries, this.summary); });
      this.filters.append(button);
    }
    this.list.before(this.filters);
    document.getElementById('made-today-close')!.addEventListener('click', () => this.close());
  }

  isOpen(): boolean {
    return !this.root.hidden && !this.root.closest('[hidden]');
  }

  close(): void {
    this.root.hidden = true;
  }

  footprint(): DOMRect | null {
    return this.isOpen() ? this.root.getBoundingClientRect() : null;
  }

  async open(): Promise<void> {
    this.root.hidden = false;
    await this.refresh();
  }

  toggle(): void {
    if (this.isOpen()) this.close();
    else void this.open();
  }

  /** When the session began (the day's wire, `sessionStart`): what came before is listed under "Earlier". */
  setSessionStart(at: number | null): void {
    if (at === this.sessionStart) return;
    this.sessionStart = at;
    this.render(this.entries, this.summary);
  }

  /** Another session is open (`session-cleared`): what this one listed is not shown a moment longer; it is read
   * again at once. */
  sessionChanged(): void {
    if (this.refreshTimer !== null) { clearTimeout(this.refreshTimer); this.refreshTimer = null; }
    this.showEarlier = false;
    this.entries = [];
    this.renderStamp = '';
    this.render(this.entries, this.summary);
    void this.refresh();
  }

  /** Listen to what is listed: the products, the index's summary and when the session began, on every change (and
   * at once when the index has been read). Returns its removal. */
  onData(listener: DataListener): () => void {
    this.dataListeners.add(listener);
    if (this.read) listener(products(this.entries), this.summary, this.sessionStart);
    return () => this.dataListeners.delete(listener);
  }

  /** Open an entry as its card does: a window already showing it comes back; cell outlines show their layer; the rest
   * open as their picture or work (screen.PRESENTED_AS) over the live workspace, as the user's own change. */
  openEntry(entry: ResourceEntry): void {
    try {
      const existing = this.matching(entry)[0];
      if (existing) this.restore(existing.id);
      else if (this.openResource && entry.day && entry.kind !== 'population') {
        this.openResource(resourceTarget(entry), entry.title);
      } else this.send({ type: 'present', what: entry.id, do: entry.kind === 'population' ? 'show' : 'open' });
    } catch {
      this.note.textContent = 'This view could not be opened. Reconnect to the studio and try again.';
    }
  }

  /** The windows showing an entry now (by its exact day, id, version and picture). */
  matching(entry: ResourceEntry, views = this.views()): { id: string; title: string; visible: boolean; resource: unknown }[] {
    const target = resourceTarget(entry);
    return views.filter(view => {
      const ref = view.resource;
      return ref && typeof ref === 'object' && 'day' in ref && 'id' in ref && 'version' in ref
        && ref.day === target.day && ref.id === target.id && ref.version === target.version && (!('picture' in ref) || ref.picture === target.picture);
    });
  }

  private tellData(): void {
    this.read = true;
    const listed = products(this.entries);
    for (const listener of [...this.dataListeners]) listener(listed, this.summary, this.sessionStart);
  }

  async refresh(): Promise<void> {
    const id = ++this.refreshId;
    try {
      const [listing, summary] = await Promise.all([
        fetch('/api/resources', { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('Resource index unavailable'); return r.json() as Promise<{ entries: ResourceEntry[] }>; }),
        fetch('/api/resources/summary', { cache: 'no-store' }).then((r) => { if (!r.ok) throw new Error('Resource summary unavailable'); return r.json() as Promise<Summary>; }),
      ]);
      if (id !== this.refreshId) return;
      this.entries = listing.entries; this.summary = summary;
      this.render(listing.entries, summary);
    } catch {
      if (id !== this.refreshId) return;
      this.note.textContent = 'What was made in this project could not be read just now.';
    }
  }

  private render(all: ResourceEntry[], summary: Summary): void {
    const entries = products(all);
    // Refresh status can recover even when the retained rows did not change.
    this.note.textContent = summary.unreadable?.length ? `Couldn't read: ${summary.unreadable.join('; ')}.` : '';
    const dataStamp = JSON.stringify([entries, summary, this.sessionStart]);
    if (dataStamp !== this.dataStamp || !this.read) { this.dataStamp = dataStamp; this.tellData(); }
    const stamp = JSON.stringify([entries, summary, this.filter, this.views(), this.sessionStart, this.showEarlier]);
    if (stamp === this.renderStamp) return;
    this.renderStamp = stamp;
    const focused = this.list.contains(document.activeElement) ? (document.activeElement as HTMLElement).closest<HTMLElement>('[data-entry]')?.dataset.entry : undefined;
    this.list.replaceChildren();
    for (const button of this.filters.querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.textContent === this.filter));
    this.filters.hidden = !entries.length; // nothing made: nothing to filter
    const views = this.views();
    const matching = (entry: ResourceEntry) => this.matching(entry, views);
    const shown = entries.filter(entry => this.filter === 'On screen' ? matching(entry).some(view => view.visible) : matchesFilter(entry, this.filter)).sort((a, b) => (b.at ?? 0) - (a.at ?? 0));
    // What is on screen is listed whatever the session made it in; otherwise this session's, then "Earlier".
    const { current, earlier } = this.filter === 'On screen' ? { current: shown, earlier: [] } : sessionSplit(shown, this.sessionStart);
    const listed = this.showEarlier ? [...current, ...earlier] : current;
    const rows = document.createElement('div'); rows.className = 'made-cards'; rows.id = 'made-today-cards';
    if (!current.length) {
      const empty = document.createElement('p'); empty.className = 'made-empty';
      empty.textContent = earlier.length ? 'Nothing made in this project yet.' : entries.length ? 'No retained work matches this filter.'
        : 'Nothing made yet. Results of analyses go here; your snapshots and recordings go to Assets.';
      rows.append(empty);
    }
      // A session may span days: when what is listed does, each day begins under a quiet divider.
      const now = Date.now() / 1000;
      const days = spansDays(listed, now);
      let lastDay: number | null = null;
      for (const entry of listed) {
        if (entry === earlier[0]) rows.append(this.earlierRow(earlier.length));
        if (days && entry.at != null && daysAgo(entry.at, now) !== lastDay) {
          lastDay = daysAgo(entry.at, now);
          const divider = document.createElement('div');
          divider.className = 'made-day';
          divider.setAttribute('role', 'presentation');
          divider.textContent = dayLabel(entry.at, now);
          rows.append(divider);
        }
        const row = document.createElement('article'); row.className = 'made-card'; row.dataset.state = entry.state; row.dataset.entry = entry.id;
        row.append(thumbnail(entry));
        const words = document.createElement('div');
        words.className = 'made-words';
        const title = document.createElement('strong');
        title.className = 'made-title';
        title.textContent = entry.title;
        const detail = document.createElement('span');
        detail.className = 'made-meta';
        // Plain words on the row; what made it and its id are one step away (Amendment 4.1; M1.5c review S7).
        const kind = entry.media === 'reference' ? 'Reference photo' : entry.kind === 'population' ? 'Segmentation instances'
          : summary.kinds.find(k => k.kind === entry.kind)?.name ?? entry.kind;
        detail.textContent = entry.state === 'unavailable' ? entry.plain || entry.summary
          : entry.state === 'running' ? 'Still being made' : [kind, creator(entry)].filter(Boolean).join(' · ');
        const media = entry.media ?? entry.source?.media;
        const captured = ['capture', 'raw', 'labelled', 'annotated', 'derived'].includes(media ?? '')
          || entry.source?.frame != null;
        if (captured || media === 'reference' || media === 'generated') {
          const mark = document.createElement('span'); mark.className = captured ? 'made-placement' : 'made-media';
          mark.textContent = captured ? '○' : media === 'reference' ? '↗' : '✧';
          mark.title = captured ? 'Retained captured view' : media === 'reference' ? 'Reference photo' : 'Generated image';
          mark.setAttribute('role', 'img'); mark.setAttribute('aria-label', mark.title); detail.prepend(mark);
        }
        row.title = [entry.plain || entry.summary, entry.placement, entry.id].filter(Boolean).join(' · ');
        words.append(title, detail);
        row.append(words);
        if (entry.kind === 'result' && entry.portable && entry.pictures?.length && entry.day) {
          // Only a result that says which capabilities made it (its madeWith) has something to carry with it.
          const base = `/api/resources/${encodeURIComponent(entry.id)}`;
          const query = `?day=${encodeURIComponent(entry.day)}&n=0`;
          words.append(portablePictureControls(`${base}/share${query}`, `${base}/picture${query}`));
        }
        if ((entry.kind === 'population' || entry.kind === 'tracks') && entry.state !== 'running') {
          // Its numbers as a table to sort and download (measurements.ts), beside opening it over the view.
          const table = document.createElement('button');
          table.type = 'button'; table.className = 'made-table'; table.textContent = 'Table';
          table.title = `${entry.title}: measurements to sort and download`;
          table.setAttribute('aria-label', `Table of ${entry.title}`);
          table.addEventListener('click', (event) => { event.stopPropagation(); this.onTable(entry.id); });
          row.append(table);
        }
        if (entry.actions.includes('open')) {
          // Cell outlines open by showing their layer; the rest open as their picture or work (screen.PRESENTED_AS),
          // over the live workspace. The whole row opens it (a click or Enter), as its ↗ does (live 2026-10-11: a click
          // on the row did nothing; only a ↗ seen on hover opened it).
          const openIt = () => this.openEntry(entry);
          const open = document.createElement('button');
          open.type = 'button';
          open.textContent = '↗'; open.className = 'made-eye'; open.setAttribute('aria-label', `Open ${entry.title}`);
          open.setAttribute('aria-pressed', String(matching(entry).some(view => view.visible)));
          open.title = `Show ${entry.title}`;
          open.addEventListener('click', (event) => { event.stopPropagation(); openIt(); });
          row.append(open);
          row.dataset.openable = 'true'; row.tabIndex = 0; row.setAttribute('role', 'button');
          row.setAttribute('aria-label', `Open ${entry.title}`);
          row.addEventListener('click', (event) => {
            if ((event.target as Element | null)?.closest?.('button, a, input, select')) return; // its own controls
            openIt();
          });
          row.addEventListener('keydown', (event) => {
            if (event.target !== row || (event.key !== 'Enter' && event.key !== ' ')) return;
            event.preventDefault(); openIt();
          });
        } else row.dataset.openable = 'false';
        if (entry.readBy?.length) {
          const readers = document.createElement('span'); readers.className = 'made-readers';
          readers.textContent = `Read by ${[...new Set(entry.readBy.map(read => read.name))].join(', ')}`;
          readers.title = entry.readBy.map(read => `${read.name} · ${read.version === null ? 'verified retained content' : `revision ${read.version}`} · ${read.how} · ${new Date(read.at * 1000).toISOString()}`).join('\n');
          row.append(readers);
        }
        rows.append(row);
      }
    if (earlier.length && !this.showEarlier) rows.append(this.earlierRow(earlier.length));
    const indexedViews = new Set(entries.flatMap(entry => matching(entry).map(view => view.id)));
    const extra = views.filter(view => !indexedViews.has(view.id));
    const thisSession = this.sessionStart == null ? entries : sessionSplit(entries, this.sessionStart).current;
    this.onCount(thisSession.length + extra.length);
    const running = entries.filter(entry => entry.state === 'running').length;
    this.onSummary(`${entries.length} kept · ${views.filter(view => view.visible).length} on screen${running ? ` · ${running} being made` : ''}`);
    for (const view of extra) {
      if (this.filter !== 'All' && !(this.filter === 'On screen' && view.visible)) continue;
      const row = document.createElement('article'); row.className = 'made-card'; row.dataset.entry = `window:${view.id}`;
      const words = document.createElement('div'); words.className = 'made-words';
      const title = document.createElement('strong'); title.className = 'made-title'; title.textContent = view.title;
      const meta = document.createElement('span'); meta.className = 'made-meta'; meta.textContent = view.visible ? 'Open window' : 'Hidden window';
      words.append(title, meta);
      const open = document.createElement('button'); open.type = 'button'; open.className = 'made-eye'; open.textContent = '↗';
      open.setAttribute('aria-label', `Restore ${view.title}`); open.setAttribute('aria-pressed', String(view.visible));
      open.addEventListener('click', () => {
        try { this.restore(view.id); }
        catch { this.note.textContent = 'This view could not be opened. Reconnect to the studio and try again.'; }
      }); row.append(words, open); rows.append(row);
    }
    if (extra.length && rows.querySelector('.made-card')) rows.querySelector('.made-empty')?.remove();
    this.list.append(rows);
    if (focused) {
      const replacement = [...rows.querySelectorAll<HTMLElement>('[data-entry]')].find(row => row.dataset.entry === focused)?.querySelector<HTMLButtonElement>('button');
      replacement?.focus({ preventScroll: true });
    }
    if (summary.couldMake.length) {
      const could = document.createElement('p');
      could.className = 'made-today-could';
      could.textContent = `Could also be made here: ${summary.couldMake.join('; ')}.`;
      this.list.append(could);
    }
  }

  /** The row that opens (or folds) what was made before this session began. */
  private earlierRow(count: number): HTMLElement {
    const row = document.createElement('button');
    row.type = 'button'; row.className = 'made-earlier'; row.setAttribute('aria-expanded', String(this.showEarlier));
    row.textContent = `${this.showEarlier ? 'Hide' : 'Earlier'} · ${count} from before this project`;
    row.addEventListener('click', () => { this.showEarlier = !this.showEarlier; this.render(this.entries, this.summary); });
    return row;
  }
}

/** Whether the entries were made on more than one calendar day (local time; entries with no time say nothing). */
export function spansDays(entries: readonly ResourceEntry[], now: number): boolean {
  return new Set(entries.filter((entry) => entry.at != null).map((entry) => daysAgo(entry.at!, now))).size > 1;
}

/** Each kind's icon on a card with no picture (studio-shell.css "made-thumb[data-icon]"), as the tray draws them. */
const KIND_ICONS: Record<string, string> = {
  population: 'cells', tracks: 'motion', research: 'research', snapshot: 'camera', result: 'picture', bookmark: 'bookmark',
  clip: 'clip', individual: 'followed', dataset: 'dataset', recording: 'recording',
};

/** What the entry is now, in a few characters: its thumbnail's address changes when it does (a new revision, a
 * finished recording), so the browser and the server keep one per version (thumbnails.py). */
export function thumbnailVersion(entry: ResourceEntry): string {
  const text = JSON.stringify([entry.version ?? null, entry.state, entry.pictures ?? [], entry.source ?? {}, entry.title]);
  let hash = 5381;
  for (let i = 0; i < text.length; i++) hash = ((hash << 5) + hash + text.charCodeAt(i)) | 0;
  return (hash >>> 0).toString(36);
}

/** A card's picture: the product's thumbnail, made by the server from what it keeps (GET .../thumbnail); while it
 * loads, and when there is none, its kind's icon (2026-10-11: until now only entries with an /api/ picture had one). */
export function thumbnail(entry: ResourceEntry): HTMLElement {
  const thumb = document.createElement('span');
  thumb.className = 'made-thumb';
  thumb.dataset.icon = KIND_ICONS[entry.kind] ?? 'made';
  if (entry.product === false) return thumb;
  const img = document.createElement('img');
  const day = entry.day ? `day=${encodeURIComponent(entry.day)}&` : '';
  img.src = `/api/resources/${encodeURIComponent(entry.id)}/thumbnail?${day}v=${thumbnailVersion(entry)}`;
  img.alt = '';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.addEventListener('load', () => { thumb.dataset.picture = ''; }, { once: true });
  img.addEventListener('error', () => { img.remove(); }, { once: true });
  thumb.append(img);
  return thumb;
}

/** Only explicit attribution qualifies for Yours; missing old provenance is not the user. */
export function creator(entry: ResourceEntry): string | null {
  return entry.madeBy?.requestedBy === 'user' || entry.madeBy?.by === 'user' ? 'You'
    : entry.madeBy?.for ?? entry.madeBy?.implementation ?? null;
}
export function matchesFilter(entry: ResourceEntry, filter: string): boolean {
  if (filter === 'Yours') return entry.madeBy?.requestedBy === 'user' || entry.madeBy?.by === 'user';
  if (filter === 'Pictures') return Boolean(entry.pictures?.length) && entry.kind !== 'research';
  if (filter === 'AI research') return entry.kind === 'research';
  // Kinds as the index names them (produces.KINDS): cell outlines and motion tracks draw over the view; they and
  // things followed over time are what was measured.
  if (filter === 'Overlays') return ['population', 'tracks'].includes(entry.kind);
  if (filter === 'Measurements') return ['population', 'tracks', 'individual'].includes(entry.kind);
  return true;
}
