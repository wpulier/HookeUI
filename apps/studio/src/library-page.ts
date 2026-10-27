// All assets (#assets; docs/design/2026-10-12-workspace-and-assets.md, Will's design of 2026-10-12): the library, a
// feature of its own, as a gallery sorted by what is in the pictures. The session stays loaded behind it.
//
// - Yours | Examples, top right (yours: your own, and the examples a session used); a search; Recordings / Pictures.
// - Categories first (the library's fixed top level, library.py CATEGORIES, each with its newest as its cover and how
//   many), "Not sure yet" for what has had no look; a category opens a grid of its assets under the names the looks gave
//   them (the examples: their specimens, then a specimen's clips).
// - A click opens the preview, never the session: a large player (or the picture), its details (the user's own fields,
//   its category), the sessions that use it, and Add to this session. Previewing touches no session; Add puts it on stage
//   and makes it the session's (ADR 0022's amendment).
// - Move to trash, after a confirm naming the sessions that use it, with Undo. Files dropped on the page, or Add files,
//   join your assets only.
//
// It only shows and asks: what a choice does (the server's `source`, `asset-meta`, `asset-trash`, an upload) is
// main.ts's, through LibraryActions.
import type { AssetMeta } from './protocol';
import { clipCount, clipName, libraryMeta, libraryUrl, readLibrary, specimenTitle, type LibraryPage as LibraryRead, type LibraryScope } from './asset-library';
import { assetThumb } from './sources';
import { portablePictureControls } from './portable-picture';

/** The library's top level, in its order (library.py CATEGORIES), and the card of what has had no look. */
export const CATEGORIES = ['Plant tissue', 'Blood', 'Human & animal tissue', 'Invertebrates', 'Protists & algae', 'Bacteria & fungi', 'Other'] as const;
export const NOT_SURE = 'Not sure yet';
/** Said where "Not sure yet" shows while Library looks is off: no look is coming, so the user files them. */
export const LOOKS_OFF = 'Automatic organization is off. Choose a category in an input’s details, or open Organization settings to enable Library looks.';
/** How many assets a page reads at a time (Show more reads the next). */
export const PAGE = 48;
const SCOPE_KEY = 'studio-assets-group';

export interface CategoryCard { group: string; count: number; cover: AssetMeta }
export interface AssetCategory { group: string; name: string; by: 'user' | 'app'; sure: boolean }
/** What a shared picture says made it, and where each capability stands on this Mac (made_with.py). */
export interface MadeWith { key: string; name: string; state: 'on' | 'one-press' | 'not-here' | 'unknown' | 'acquire'; says: string; model?: string }
export interface AssetDetails extends AssetMeta {
  category?: AssetCategory | null; sessions?: { id: string; label: string }[]; madeWith?: MadeWith[];
}

export interface LibraryActions {
  /** Open the existing Library looks setting; browsing never enables it. */
  organize?(): void;
  /** Open in this Project: in a Tab, and one of its inputs from then on. True when it is. */
  add(asset: AssetMeta): Promise<boolean>;
  /** Open in a new Project: the Project is begun, then this opens in it. True when it did. */
  openInNewProject?(asset: AssetMeta): Promise<boolean>;
  /** The user's own fields of an asset (null clears one). */
  setMeta(id: string, set: Record<string, string | number | null>): Promise<void>;
  /** Move to trash; null when refused (said by main.ts). */
  trash(asset: AssetMeta): Promise<boolean>;
  /** Undo of the trash. */
  restore(id: string): Promise<boolean>;
  /** Files from this Mac (chosen, or dropped on the page): into your assets only, never opened. */
  upload(files: File[]): Promise<void>;
  /** A session the asset is in: open it. */
  openSession(id: string): void;
  /** Made with: turn on a capability the picture says made it (its every step, carried through: model-turn-on). */
  turnOn?(model: string): Promise<unknown>;
  acquire?(asset: string, key: string): Promise<unknown>;
}

/** The categories a page of the library offers, read safely (the server's order kept). */
export function readCategories(body: unknown): CategoryCard[] {
  const list = body && typeof body === 'object' && Array.isArray((body as { categories?: unknown }).categories)
    ? (body as { categories: unknown[] }).categories : [];
  return list.filter((c): c is CategoryCard => !!c && typeof c === 'object' && typeof (c as CategoryCard).group === 'string'
    && typeof (c as CategoryCard).count === 'number' && !!(c as CategoryCard).cover && typeof (c as CategoryCard).cover.id === 'string');
}

/** "1 item", "57 items". */
export function itemCount(n: number): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? 'item' : 'items'}`;
}

/** A category's assets under the names its looks gave them, the named first by name, then those with none. */
export function byName(assets: readonly AssetDetails[]): { name: string; assets: AssetDetails[] }[] {
  const groups = new Map<string, AssetDetails[]>();
  for (const asset of assets) {
    const name = (asset.category?.name ?? '').trim();
    groups.set(name, [...(groups.get(name) ?? []), asset]);
  }
  return [...groups.entries()].sort(([a], [b]) => (a ? (b ? a.localeCompare(b) : -1) : 1))
    .map(([name, list]) => ({ name: name || 'Not named yet', assets: list }));
}

/** Where a category's card says its assets are: "Invertebrates", and under it what the looks named most. */
export function categoryLine(category: AssetCategory | null | undefined): string {
  if (!category) return NOT_SURE;
  return category.name ? `${category.group} › ${category.name}` : category.group;
}

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}
function button(className: string, text: string, label = ''): HTMLButtonElement {
  const b = node('button', className, text);
  b.type = 'button';
  if (label) b.setAttribute('aria-label', label);
  return b;
}

function storedScope(): LibraryScope {
  try {
    return localStorage.getItem(SCOPE_KEY) === 'example' ? 'example' : 'mine';
  } catch {
    return 'mine'; // optional: yours first
  }
}

/** In-flight/unsaved metadata only: survives a preview replacement, never a second asset store. */
interface MetadataDraft {
  draft: string | null; accepted: string | null; revision: number;
  saving: boolean; queued: boolean; confirmed: boolean;
  phase: '' | 'dirty' | 'saving' | 'saved' | 'failed'; reason: string;
  pause?: ReturnType<typeof setTimeout>;
  render?: () => void;
  onSaved?: () => void;
}

export class LibraryPage {
  readonly root: HTMLElement;
  readonly search = node('input', 'library-search');
  readonly addFiles = button('lib-add-files', 'Add files…');
  readonly fileInput = node('input', 'lib-file');
  readonly status = node('p', 'lib-status');
  readonly grid = node('div', 'lib-grid');
  readonly more = button('library-more', '');
  readonly preview = node('section', 'lib-preview');
  private readonly organizationStatus = node('span', 'lib-organization-status', 'Automatic organization: checking…');
  private readonly catalogLayout: boolean;
  private readonly categoryNav = node('nav', 'lib-category-nav');
  private readonly categoryButtons = new Map<string | null, {button: HTMLButtonElement; count: HTMLElement}>();
  private readonly galleryTitle = node('h2', 'lib-gallery-title');
  private readonly scopes = new Map<LibraryScope, HTMLButtonElement>();
  private readonly kinds = new Map<string, HTMLButtonElement>();
  private readonly kindBar = node('div', 'library-kinds');
  private readonly crumb = node('nav', 'lib-crumb');
  private readonly actions: LibraryActions;
  private readonly fetcher: typeof fetch | undefined;
  private readonly clock: () => number;
  private readonly inSession: (id: string) => boolean;
  scope: LibraryScope;
  kind: 'recording' | 'still' | null = null;
  category: string | null = null;
  specimen: string | null = null;
  /** Nothing has had a look yet: "Not sure yet" is all there is, so it is opened at once (one card is no choice). */
  private onlyUnsure = false;
  /** Library looks is off (the server's `looks`): "Not sure yet" says no look is coming. */
  private looksOff = false;
  private shown: AssetDetails[] = [];
  private count = 0;
  private cards: CategoryCard[] = [];
  private specimens: LibraryRead['specimens'] = null;
  private reads = 0;
  // An upload must outlive hidden pages and superseding reads until its fresh result is displayed.
  private freshnessRevision = 0;
  private displayedFreshnessRevision = 0;
  private searchTimer: ReturnType<typeof setTimeout> | null = null;
  /** The asset previewed now (its details read fresh), or null. */
  previewing: AssetDetails | null = null;
  private busy = false;
  private readonly metadataDrafts = new Map<string, MetadataDraft>();
  private visible = false;

  constructor(root: HTMLElement, actions: LibraryActions, options: { fetcher?: typeof fetch; clock?: () => number; inSession?: (id: string) => boolean; catalogLayout?: boolean } = {}) {
    this.root = root;
    this.catalogLayout = options.catalogLayout === true;
    this.actions = actions;
    this.fetcher = options.fetcher;
    this.clock = options.clock ?? (() => Date.now());
    this.inSession = options.inSession ?? (() => false);
    this.scope = storedScope();
    root.classList?.add('library-page');

    const head = node('header', 'lib-head');
    const words = node('div', 'lib-words');
    words.append(node('h1', 'lib-title', 'Library'),
      node('p', 'lib-lead', 'Your recordings and pictures across all Projects. Browse and organize here, then open one in any Project.'));
    const packages = node('p', 'lib-package-note', 'Datasets package inputs with their Project metadata for sharing and later analysis. ');
    const datasets = node('a', '', 'Open datasets');datasets.href='#datasets';packages.append(datasets);words.append(packages);
    const organization = node('div', 'lib-organization');
    this.organizationStatus.setAttribute('role','status');organization.append(this.organizationStatus);
    if(actions.organize){const configure=button('lib-organize','Organization settings');configure.addEventListener('click',()=>actions.organize!());organization.append(configure);}
    const right = node('div', 'lib-head-right');
    const scopes = node('div', 'assets-groups');
    scopes.setAttribute('role', 'group');
    scopes.setAttribute('aria-label', 'Whose');
    for (const [id, label] of [['mine', 'Yours'], ['example', 'Examples']] as const) {
      const b = button('library-kind assets-group-choice', label);
      b.dataset.group = id;
      b.addEventListener('click', () => this.setScope(id));
      this.scopes.set(id, b);
      scopes.append(b);
    }
    this.addFiles.title = 'Add recordings or pictures from this Mac to your Library (or drop them on this page)';
    this.addFiles.addEventListener('click', () => this.fileInput.click());
    this.fileInput.type = 'file';
    this.fileInput.multiple = true;
    this.fileInput.accept = 'video/*,image/*,.mov,.mp4,.m4v,.mkv,.png,.jpg,.jpeg,.tif,.tiff,.bmp,.webp';
    this.fileInput.hidden = true;
    this.fileInput.addEventListener('change', () => {
      const files = [...(this.fileInput.files ?? [])];
      this.fileInput.value = '';
      if (files.length) void this.upload(files);
    });
    right.append(scopes, this.addFiles, this.fileInput);
    head.append(words, right);

    const tools = node('div', 'lib-tools');
    this.search.type = 'search';
    this.search.autocomplete = 'off';
    this.search.spellcheck = false;
    this.search.setAttribute('aria-label', 'Search your assets');
    this.search.addEventListener('input', () => {
      if (this.searchTimer) clearTimeout(this.searchTimer);
      this.searchTimer = setTimeout(() => { this.searchTimer = null; void this.read(); }, 200);
    });
    this.kindBar.setAttribute('role', 'group');
    this.kindBar.setAttribute('aria-label', 'Recordings or pictures');
    for (const [id, label] of [['', 'All'], ['recording', 'Recordings'], ['still', 'Pictures']] as const) {
      const b = button('library-kind', label);
      b.addEventListener('click', () => { this.kind = id || null; void this.read(); });
      this.kinds.set(id, b);
      this.kindBar.append(b);
    }
    tools.append(this.search, this.kindBar);
    this.status.setAttribute('role', 'status');
    this.status.hidden = true;
    this.more.addEventListener('click', () => void this.read(true));
    this.preview.hidden = true;
    this.preview.setAttribute('role', 'dialog');
    this.preview.setAttribute('aria-label', 'Preview');
    if (this.catalogLayout) {
      root.classList.add('lib-catalog');
      const sidebar = node('aside', 'lib-sidebar');
      sidebar.setAttribute('aria-label', 'Library filters');
      this.categoryNav.setAttribute('aria-label', 'Library categories');
      sidebar.append(scopes, node('h3', 'lib-sidebar-label', 'Media'), this.kindBar,
        node('h3', 'lib-sidebar-label', 'Categories'), this.categoryNav);
      tools.append(this.addFiles, this.fileInput);
      const content = node('div', 'lib-catalog-content');
      content.append(words, organization, tools, this.galleryTitle, this.crumb, this.status, this.grid, this.more);
      const browser = node('div', 'lib-browser'); browser.append(sidebar, content);
      root.replaceChildren(browser, this.preview);
    } else root.replaceChildren(head, organization, tools, this.crumb, this.status, this.grid, this.more, this.preview);
    root.addEventListener('keydown', (event) => {
      if ((event as KeyboardEvent).key !== 'Escape') return;
      if (!this.preview.hidden) { event.preventDefault(); event.stopPropagation(); this.closePreview(); return; }
      if (this.specimen || this.category) { event.preventDefault(); event.stopPropagation(); this.back(); }
    });
    // Files dropped on the page join your assets (never opened, never the session's).
    root.addEventListener('dragover', (event) => { if (carriesFiles(event as DragEvent)) event.preventDefault(); });
    root.addEventListener('drop', (event) => {
      const drop = event as DragEvent;
      if (!carriesFiles(drop)) return;
      drop.preventDefault();
      drop.stopPropagation();
      void this.upload(Array.from(drop.dataTransfer?.files ?? []));
    });
    this.draw();
  }

  /** The page shown: what it lists is read again (the first time, read at all). */
  show(): void {
    this.visible = true;
    void this.read();
  }

  hide(): void {
    this.visible = false;
    this.closePreview();
  }

  setScope(scope: LibraryScope): void {
    if (scope === this.scope) return;
    this.scope = scope;
    this.onlyUnsure = false;
    this.category = null;
    this.specimen = null;
    this.kind = null;
    this.search.value = '';
    try { localStorage.setItem(SCOPE_KEY, scope); } catch { /* optional */ }
    void this.read();
  }

  openCategory(group: string | null): void {
    this.onlyUnsure = false;
    this.category = group;
    if (this.catalogLayout) this.search.value = '';
    this.specimen = null;
    void this.read();
  }

  openSpecimen(name: string | null): void {
    this.specimen = name;
    void this.read();
  }

  /** One step back: a specimen's clips to its category, a category to them all. */
  back(): void {
    if (this.specimen) this.openSpecimen(null);
    else if (!this.onlyUnsure) this.openCategory(null);
  }

  setStatus(text: string, tone: '' | 'busy' | 'error' = '', undo?: { label: string; run: () => void }): void {
    this.status.replaceChildren(text);
    this.status.dataset.tone = tone;
    this.status.hidden = !text;
    if (undo) {
      const b = button('lib-undo', undo.label);
      b.addEventListener('click', () => { undo.run(); this.setStatus(''); });
      this.status.append(' ', b);
    }
  }

  /** Read again what is shown now (the library changed: a look filed something, an upload, the trash). */
  refresh(): void {
    if (this.visible) void this.read();
  }

  private query(): string {
    return this.search.value.trim();
  }

  /** What is shown: the categories (nothing chosen, no search), a category's assets (or its specimens, for the
   * examples), a specimen's clips, or the search's matches. `more`: the next page of the same. */
  async read(more = false): Promise<void> {
    const read = ++this.reads;
    const freshness = this.freshnessRevision;
    const fresh = freshness > this.displayedFreshnessRevision;
    const q = this.query();
    const categoriesOnly = !this.catalogLayout && !q && !this.category;
    const exampleSpecimens = !this.catalogLayout && this.scope === 'example' && !q && !!this.category && !this.specimen;
    const offset = more ? this.shown.length : 0;
    let page: LibraryRead | null = null;
    let cards: CategoryCard[] = [];
    try {
      const url = libraryUrl({ q, scope: this.scope, kind: this.scope === 'mine' ? this.kind : null, specimen: this.specimen,
        category: q ? null : this.category, offset, limit: categoriesOnly || exampleSpecimens ? 1 : PAGE, fresh });
      const response = await (this.fetcher ?? fetch)(url, { cache: 'no-store' });
      if (!response.ok) throw new Error(String(response.status));
      const body = await response.json();
      page = readLibrary(body);
      cards = readCategories(body);
      this.looksOff = !!body && typeof body === 'object' && (body as { looks?: unknown }).looks === 'off';
      const looks = body && typeof body === 'object' ? (body as {looks?:unknown}).looks : undefined;
      this.organizationStatus.textContent = looks==='off' ? 'Automatic organization: off · manual categories stay available' : looks==='on' ? 'Automatic organization: on · Library looks files inputs by what they show' : 'Automatic organization: status unavailable';
    } catch {
      if (read !== this.reads) return;
      this.setStatus('Your Library could not be read. Try again in a moment.', 'error');
      return;
    }
    if (read !== this.reads) return;
    if (categoriesOnly && cards.length === 1 && cards[0].group === NOT_SURE) {
      this.onlyUnsure = true;
      this.category = NOT_SURE;
      void this.read();
      return;
    }
    this.displayedFreshnessRevision = freshness;
    this.cards = cards;
    this.specimens = page.specimens;
    this.count = page.count;
    this.shown = more ? [...this.shown, ...page.assets] : page.assets;
    this.draw();
  }

  private draw(): void {
    for (const [id, b] of this.scopes) b.setAttribute('aria-pressed', String(id === this.scope));
    this.kindBar.hidden = this.scope !== 'mine';
    for (const [id, b] of this.kinds) b.setAttribute('aria-pressed', String((id || null) === this.kind));
    this.addFiles.hidden = this.scope !== 'mine';
    this.search.placeholder = this.scope === 'mine' ? 'Search your recordings and pictures' : 'Search the examples';
    this.drawCrumb();
    if (this.catalogLayout) this.drawCategoryNav();
    const q = this.query();
    const cells: HTMLElement[] = [];
    let moreLeft = 0;
    const unsureShown = this.scope === 'mine' && !q && (this.category === NOT_SURE || (!this.category && this.cards.some((c) => c.group === NOT_SURE)));
    if (this.looksOff && unsureShown && !this.actions.organize) cells.push(node('p', 'lib-note', LOOKS_OFF));
    if (!this.catalogLayout && !q && !this.category) {
      this.grid.dataset.view = 'categories';
      for (const card of this.cards) cells.push(this.categoryCard(card));
      if (!this.cards.length) cells.push(node('p', 'lib-empty', this.scope === 'mine'
        ? 'Nothing here yet. Add files, or drop recordings and pictures on this page.' : 'No examples on this Mac yet.'));
    } else if (!this.catalogLayout && this.scope === 'example' && !q && this.category && !this.specimen) {
      this.grid.dataset.view = 'specimens';
      for (const s of this.specimens ?? []) cells.push(this.specimenCard(s.name, s.count, s.first));
    } else {
      this.grid.dataset.view = 'assets';
      if (!this.catalogLayout && this.category && !q && this.scope === 'mine') {
        for (const group of byName(this.shown)) {
          cells.push(node('h2', 'lib-name', group.name));
          for (const asset of group.assets) cells.push(this.assetCard(asset));
        }
      } else {
        for (const asset of this.shown) cells.push(this.assetCard(asset));
      }
      if (!this.shown.length) cells.push(node('p', 'lib-empty', q ? 'Nothing matches.' : 'Nothing here.'));
      moreLeft = Math.max(0, this.count - this.shown.length);
    }
    this.grid.replaceChildren(...cells);
    this.more.hidden = !moreLeft;
    this.more.textContent = moreLeft ? `Show ${Math.min(PAGE, moreLeft)} more of ${this.count.toLocaleString('en-US')}` : '';
  }

  private drawCategoryNav(): void {
    const counts = new Map(this.cards.map(card => [card.group, card.count]));
    const items = [{group:null, label:'All items'}, ...[...CATEGORIES, NOT_SURE].map(group=>({group,label:group}))];
    for (const {group,label} of items) {
      let choice = this.categoryButtons.get(group);
      if (!choice) {
        const b = button('lib-category-choice', '', label);
        const count = node('span','lib-category-count');
        b.append(node('span','',label),count);
        b.addEventListener('click',()=>this.openCategory(group));
        this.categoryNav.append(b);
        choice = {button:b,count}; this.categoryButtons.set(group,choice);
      }
      const count = group === null ? this.cards.reduce((sum,c)=>sum+c.count,0) : counts.get(group) ?? 0;
      choice.count.textContent = String(count);
      choice.button.setAttribute('aria-pressed',String(this.category===group && !this.query()));
    }
    this.galleryTitle.textContent = `${this.query() ? 'Search results' : this.category ?? 'All items'} · ${itemCount(this.count)}`;
    // Category selection is already visible in the persistent sidebar.
    this.crumb.hidden = true;
  }

  private drawCrumb(): void {
    const parts: HTMLElement[] = [];
    if (this.category && !this.query() && !(this.onlyUnsure && !this.specimen)) {
      const all = button('lib-crumb-link', 'All categories');
      all.addEventListener('click', () => this.openCategory(null));
      parts.push(all);
      if (this.onlyUnsure) parts.splice(0, parts.length); // no categories yet: the way back is the specimens
      if (this.specimen) {
        const up = button('lib-crumb-link', this.onlyUnsure ? 'All examples' : this.category);
        up.addEventListener('click', () => this.openSpecimen(null));
        parts.push(...(parts.length ? [node('span', 'lib-crumb-sep', '›')] : []), up, node('span', 'lib-crumb-sep', '›'),
          node('span', 'lib-crumb-here', specimenTitle(this.specimen)));
      } else {
        parts.push(node('span', 'lib-crumb-sep', '›'), node('span', 'lib-crumb-here', this.category));
      }
    }
    this.crumb.replaceChildren(...parts);
    this.crumb.hidden = !parts.length;
  }

  private picture(asset: AssetMeta): HTMLElement {
    const thumb = assetThumb(asset);
    thumb.classList?.add('lib-picture');
    return thumb;
  }

  private categoryCard(card: CategoryCard): HTMLElement {
    const b = button('lib-card lib-category', '', `${card.group}, ${itemCount(card.count)}`);
    b.dataset.group = card.group;
    const words = node('span', 'lib-card-words');
    words.append(node('span', 'lib-card-title', card.group), node('span', 'lib-card-meta', itemCount(card.count)));
    b.append(this.picture(card.cover), words);
    b.addEventListener('click', () => this.openCategory(card.group));
    return b;
  }

  private specimenCard(name: string, count: number, first: AssetMeta): HTMLElement {
    const b = button('lib-card lib-specimen', '', `${specimenTitle(name)}, ${clipCount(count)}`);
    const words = node('span', 'lib-card-words');
    words.append(node('span', 'lib-card-title', specimenTitle(name)), node('span', 'lib-card-meta', clipCount(count)));
    b.append(this.picture(first), words);
    b.addEventListener('click', () => (count === 1 ? void this.openPreview(first) : this.openSpecimen(name)));
    return b;
  }

  private assetCard(asset: AssetDetails): HTMLElement {
    const shown = this.scope === 'example' && this.specimen ? clipName(asset.title, asset.specimen ?? '') : asset.title;
    const b = button('lib-card lib-asset', '', `Preview ${asset.title}`);
    b.dataset.asset = asset.id;
    const words = node('span', 'lib-card-words');
    words.append(node('span', 'lib-card-title', shown), node('span', 'lib-card-meta', libraryMeta(asset, this.clock() / 1000)));
    b.append(this.picture(asset), words);
    if (this.inSession(asset.id)) {
      const tag = node('span', 'library-tag', 'In this project');
      b.append(tag);
    }
    b.addEventListener('click', () => void this.openPreview(asset));
    return b;
  }

  // --- the preview -------------------------------------------------------------------------------------------------

  /** Its preview: read fresh (its sessions), never the session's until Add. */
  async openPreview(asset: AssetMeta): Promise<void> {
    this.previewing = asset as AssetDetails;
    this.drawPreview();
    try {
      const response = await (this.fetcher ?? fetch)(`/api/library/${encodeURIComponent(asset.id)}`, { cache: 'no-store' });
      if (response.ok && this.previewing?.id === asset.id) {
        this.previewing = (await response.json()) as AssetDetails;
        this.drawPreview();
      }
    } catch {
      // the preview stands on what the grid knew; its sessions are not said
    }
  }

  closePreview(): void {
    if (this.preview.hidden) return;
    this.previewing = null;
    this.preview.hidden = true;
    this.preview.replaceChildren();
  }

  private drawPreview(): void {
    const asset = this.previewing;
    if (!asset) { this.closePreview(); return; }
    this.preview.hidden = false;
    const close = button('lib-preview-close', '×', 'Close the preview');
    close.addEventListener('click', () => this.closePreview());
    const media = node('div', 'lib-media');
    if (asset.kind === 'still') {
      const img = node('img');
      img.src = `/api/library/${encodeURIComponent(asset.id)}/file`;
      img.alt = asset.title;
      media.append(img);
    } else {
      const video = node('video');
      video.src = `/api/library/${encodeURIComponent(asset.id)}/file`;
      video.controls = true;
      video.muted = true;
      video.preload = 'metadata';
      video.setAttribute('playsinline', '');
      if (asset.thumb) video.poster = asset.thumb;
      media.append(video);
    }
    const side = node('div', 'lib-details');
    side.append(node('h2', 'lib-preview-title', asset.title), node('p', 'lib-preview-meta', libraryMeta(asset, this.clock() / 1000)),
      node('p', 'lib-preview-category', categoryLine(asset.category)));
    side.append(this.fields(asset));
    const sessions = asset.sessions ?? [];
    if (sessions.length) {
      const line = node('p', 'lib-sessions');
      line.append('In Projects: ');
      sessions.forEach((s, i) => {
        const link = button('lib-session-link', s.label);
        link.addEventListener('click', () => this.actions.openSession(s.id));
        line.append(...(i ? [', ', link] : [link]));
      });
      side.append(line);
    }
    const made = asset.madeWith ?? [];
    if (made.length) side.append(this.madeWith(made, asset.id));
    const buttons = node('div', 'lib-preview-actions');
    const inSession = this.inSession(asset.id);
    // One set of actions, each saying what it does (2026-10-23 audit): open here (it becomes one of this Project's
    // inputs and opens in a Tab), open in a new Project, or move to trash.
    const add = button('mm-primary lib-add', 'Open in this Project');
    add.title = inSession ? 'It is one of this Project’s inputs: opens it in its Tab' : 'Opens it in a Tab of this Project; it becomes one of its inputs';
    add.disabled = this.busy;
    add.addEventListener('click', () => void this.add(asset));
    const trash = button('lib-trash', 'Move to trash');
    trash.disabled = this.busy;
    trash.addEventListener('click', () => this.confirmTrash(asset, buttons));
    buttons.append(add);
    // Without an action of its own, the workspace hears the request (workspace-study.ts) and begins the Project.
    const openInNewProject = this.actions.openInNewProject ?? ((a: AssetMeta) => { window.dispatchEvent(new CustomEvent('hooke-open-in-new-project', { detail: { asset: a } })); return Promise.resolve(true); });
    {
      const fresh = button('lib-open-new', 'Open in new Project');
      fresh.title = 'Starts a new Project with this as its first input';
      fresh.disabled = this.busy;
      fresh.addEventListener('click', () => { this.busy = true; fresh.disabled = add.disabled = true; this.setStatus(`Opening “${asset.title}” in a new Project…`, 'busy');
        void openInNewProject(asset).then((ok) => { if (ok) this.setStatus(''); }).catch((error) => this.setStatus(error instanceof Error ? error.message : String(error), 'error'))
          .finally(() => { this.busy = false; fresh.disabled = add.disabled = false; }); });
      buttons.append(fresh);
    }
    buttons.append(trash);
    if (asset.kind === 'still' && (asset.madeWith ?? []).length) {
      // Only a picture that says what made it can carry it: an upload or a clearer picture has nothing to carry.
      const base = `/api/library/${encodeURIComponent(asset.id)}`;
      buttons.append(portablePictureControls(`${base}/share`, `${base}/file`));
    }
    side.append(buttons);
    this.preview.replaceChildren(close, media, side);
  }

  /** Made with: each capability the picture says made it, as it stands here; one not on yet has its Turn on. */
  private madeWith(made: MadeWith[], asset: string): HTMLElement {
    const box = node('div', 'lib-made-with');
    box.append(node('p', 'lib-made-with-head', 'Made with'));
    for (const c of made) {
      const line = node('p', `lib-made-with-row lib-made-${c.state}`, c.says);
      if (c.state === 'acquire' && this.actions.acquire) {
        const get = button('mm-primary lib-made-with-get', 'Add this model');
        get.addEventListener('click', () => {
          get.disabled = true;
          void this.actions.acquire!(asset, c.key).then(() => { get.textContent = 'Added · see it in Models'; })
            .catch(error => { get.disabled = false; get.textContent = `Try again: ${String(error)}`; });
        });
        line.append(' ', get);
      }
      if (c.state === 'one-press' && c.model && this.actions.turnOn) {
        const on = button('mm-primary lib-made-with-on', 'Turn it on');
        const model = c.model;
        on.addEventListener('click', () => {
          on.disabled = true;
          void this.actions.turnOn!(model).then(() => { on.textContent = 'Turning on · see Models'; })
            .catch(() => { on.disabled = false; });
        });
        line.append(' ', on);
      }
      box.append(line);
    }
    return box;
  }

  /** Its own fields, each saved as it is changed: specimen, title, notes, and its category (the user's word wins).
   * Each says whether it saved: Saving…, Saved, or Not saved with the reason and Try again (what was typed stays). */
  private fields(asset: AssetDetails): HTMLElement {
    const form = node('div', 'lib-fields');
    const bindSave = (wrap: HTMLElement, input: HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement, field: string, afterSave = () => {}): void => {
      const value = () => input.value.trim() || null;
      const key = JSON.stringify([asset.id, field]);
      const pending: MetadataDraft = this.metadataDrafts.get(key) ?? {
        draft: value(), accepted: value(), revision: 0, saving: false, queued: false, confirmed: true,
        phase: '', reason: '',
      };
      input.value = pending.draft ?? '';
      const status = node('span', 'lib-field-status'); status.setAttribute('role', 'status');
      const render = () => {
        if (!pending.phase) return;
        if (!status.parentElement) wrap.append(status);
        status.dataset.state = pending.phase;
        const words = { dirty: 'Not saved yet', saving: 'Saving…', saved: 'Saved',
          failed: `Not saved: ${pending.reason}. What you typed is kept here. ` };
        status.replaceChildren(words[pending.phase]);
        if (pending.phase === 'failed') {
          const retry = node('button', 'lib-field-retry', 'Try again'); retry.type = 'button';
          retry.addEventListener('click', event => { event.preventDefault(); void save(); });
          status.append(retry);
        }
      };
      pending.render = render; pending.onSaved = afterSave;
      const say = (phase: MetadataDraft['phase']) => { pending.phase = phase; pending.render?.(); };
      const changed = () => {
        // A replaced field cannot reintroduce its text through an old event callback.
        if (pending.render !== render) return;
        const next = value(); if (next === pending.draft) return;
        pending.draft = next; pending.revision++;
        this.metadataDrafts.set(key, pending);
        say(pending.confirmed && next === pending.accepted && !pending.saving ? 'saved' : 'dirty');
        if (pending.phase === 'saved') this.metadataDrafts.delete(key);
      };
      const save = async (): Promise<void> => {
        clearTimeout(pending.pause);
        pending.queued = true;
        this.metadataDrafts.set(key, pending);
        if (pending.saving) return; // one request per asset/field, including across preview replacements
        pending.saving = true;
        try {
          while (pending.queued) {
            pending.queued = false;
            const sent = pending.draft, attempt = pending.revision;
            say('saving');
            try {
              await this.actions.setMeta(asset.id, { [field]: sent });
              pending.accepted = sent; pending.confirmed = true;
              if (attempt === pending.revision) { say('saved'); pending.onSaved?.(); }
              else say('dirty');
            } catch (error) {
              pending.confirmed = false; // A lost reply needs an acknowledged retry of the current draft.
              if (attempt !== pending.revision) { say('dirty'); continue; }
              pending.reason = error instanceof Error && error.message ? error.message : 'the app did not confirm it';
              say('failed');
            }
          }
        } finally {
          pending.saving = false;
          // Keep only work still awaiting acknowledgement, not a cache of persisted metadata.
          if (pending.confirmed && pending.draft === pending.accepted && this.metadataDrafts.get(key) === pending)
            this.metadataDrafts.delete(key);
        }
      };
      // The queue and typing pause belong to the asset/field, so closing preserves the draft and reopening adopts it.
      input.addEventListener('input', () => {
        if (pending.render !== render) return;
        changed(); clearTimeout(pending.pause);
        pending.pause = setTimeout(() => { if (pending.draft !== pending.accepted || !pending.confirmed) void save(); }, 800);
      });
      input.addEventListener('change', () => {
        if (pending.render !== render) return;
        changed(); clearTimeout(pending.pause);
        if (pending.draft !== pending.accepted || !pending.confirmed || pending.saving) void save();
      });
      render();
    };
    const text = (label: string, field: string, value: string, multiline = false) => {
      const wrap = node('label', 'lib-field');
      const input = multiline ? node('textarea') : node('input');
      input.value = value;
      bindSave(wrap, input, field);
      wrap.append(node('span', 'lib-field-label', label), input);
      return wrap;
    };
    const category = node('label', 'lib-field');
    const select = node('select');
    for (const group of ['', ...CATEGORIES]) {
      const option = node('option', '', group || NOT_SURE);
      option.value = group;
      option.selected = (asset.category?.group ?? '') === group;
      select.append(option);
    }
    bindSave(category, select, 'category', () => this.refresh());
    category.append(node('span', 'lib-field-label', 'Category'), select);
    form.append(category, text('What it is', 'categoryName', asset.category?.name ?? ''), text('Specimen', 'specimen', asset.specimen ?? ''),
      text('Title', 'title', asset.title), text('Notes', 'notes', asset.notes ?? '', true));
    return form;
  }

  private confirmTrash(asset: AssetDetails, where: HTMLElement): void {
    const sessions = asset.sessions ?? [];
    const ask = node('div', 'lib-confirm');
    ask.setAttribute('role', 'alertdialog');
    ask.append(node('p', '', sessions.length
      ? `Move “${asset.title}” to the trash? It leaves ${sessions.length === 1 ? 'the project' : `the ${sessions.length} projects`} that use it (${sessions.map((s) => s.label).join(', ')}); the work made from it stays.`
      : `Move “${asset.title}” to the trash? It leaves your Library; you can undo it.`));
    const yes = button('lib-trash-yes', 'Move to trash');
    const no = button('lib-trash-no', 'Cancel');
    no.addEventListener('click', () => ask.replaceWith(where)); // kept: Add and Move to trash again
    yes.addEventListener('click', () => void this.trash(asset));
    ask.append(yes, no);
    where.replaceWith(ask);
  }

  private async add(asset: AssetMeta): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.drawPreview();
    const added = await this.actions.add(asset);
    this.busy = false;
    if (added) this.closePreview();
    else this.drawPreview();
  }

  private async trash(asset: AssetMeta): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    const done = await this.actions.trash(asset);
    this.busy = false;
    if (!done) { this.drawPreview(); return; }
    this.closePreview();
    this.setStatus(`Moved “${asset.title}” to the trash.`, '', { label: 'Undo', run: () => void this.actions.restore(asset.id).then(() => this.refresh()) });
    this.refresh();
  }

  private async upload(files: File[]): Promise<void> {
    if (!files.length) return;
    this.setStatus(`Adding ${files.length === 1 ? files[0].name : `${files.length} files`}…`, 'busy');
    try {
      await this.actions.upload(files);
      this.setStatus(files.length === 1 ? `Added ${files[0].name} to your Library.` : `Added ${files.length} files to your Library.`);
    } catch (error) {
      this.setStatus(error instanceof Error ? error.message : 'The files could not be added.', 'error');
    }
    this.freshnessRevision++;
    if (this.scope !== 'mine') this.setScope('mine');
    else this.refresh();
  }
}

function carriesFiles(event: DragEvent): boolean {
  return Array.from(event.dataTransfer?.types ?? []).includes('Files');
}
