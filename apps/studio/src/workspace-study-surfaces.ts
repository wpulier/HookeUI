import { studyArtifacts } from './workspace-study-artifacts';
import { surfaceTrail } from './workspace-study-navigation';
import { tabMark } from './tab-marks';
interface Surface { page: string; hash: string; node: HTMLElement | null }
const titles: Record<string, string> = { models: 'Models', capabilities: 'Capabilities', assets: 'Library', sessions: 'All projects',
  footage: 'Test footage', datasets: 'Datasets', measurements: 'Measurements', research: 'Research', researchers: 'Researchers', settings: 'Settings', help: 'Help', shortcuts: 'Keyboard shortcuts', detail: 'Details' };
/** Models' side: All models, then the sections run-spec.ts names (the ones Tools By model counts: Needs you, Ready, Not
 * checked, Can be set up, Not on this Mac; a section with nothing in it is not listed), then Keys and Storage. Add model or
 * tool is the header's button; its older Advanced contribution controls show here only while open (the add sheet's link). */
const SECTION_CATEGORIES = new Set(['needs', 'ready', 'unchecked', 'setup', 'away']);
export const SURFACE_CATEGORIES: [string, string][] = [['all', 'All models'], ['needs', 'Needs you'], ['ready', 'Ready'], ['unchecked', 'Not checked'],
  ['setup', 'Can be set up'], ['away', 'Not on this Mac'], ['keys', 'Keys'], ['storage', 'Storage'], ['own', 'Advanced contribution']]; // where it runs is the chip row
/** Whether a Models card belongs to a category: by its section (data-section, run-spec.ts `modelSection`, so these counts
 * are Tools By model's), or where it runs (data-runs). Never by its words, so rewording a card cannot empty a category. */
export function cardIn(card: HTMLElement, category: string, words: string): boolean {
  if (SECTION_CATEGORIES.has(category)) return card.dataset.section === category;
  if (category === 'cloud') return card.dataset.runs ? card.dataset.runs === 'cloud' : words.includes('cloud');
  if (category === 'mac') return card.dataset.runs ? card.dataset.runs === 'mac' : words.includes('on this mac');
  return true;
}

/** Whether Models' folded "Not on this Mac" section opens by itself: while a search looks through every card, and when
 * its side item is chosen (category `away`), so one press shows its cards rather than a closed summary. */
export function unavailableFoldOpens(category: string, query: string): boolean {
  return !!query || category === 'away';
}

/** A Models route that is a sheet of its own over the Project, never the Models page under it: a model's setup sheet
 * opened from outside Models (#models/<id>/setup: a run page's step, Tools, Activity) and Add model or tool opened from
 * Tools (#toolbox/add, #toolbox/add-<way>, the mock's addSheet over the workspace). Closing it returns to the Project. */
export function sheetAlone(hash: string): boolean {
  return /^#models\/[^/]+\/setup$/.test(hash) || /^#(?:models|toolbox)\/add(?:-[a-z]+)?$/.test(hash);
}

export class StudySurfaces {
  readonly dialog = document.createElement('dialog');
  readonly body = document.createElement('div');
  private readonly inline = document.createElement('section');
  private readonly inlineTitle = document.createElement('h2');
  readonly heading = document.createElement('h2');
  readonly search = document.createElement('input');
  private readonly add = document.createElement('button');
  readonly pages = new Map<string, HTMLElement>();
  private categories = document.createElement('nav');
  private category = 'all';
  private current: Surface | null = null;
  private opener: HTMLElement | null = null;
  private openerResource: string | null = null;
  private openerAction: string | null = null;
  /** The opener was a run page's footer button (its step): closing goes back to that page's primary. */
  private openerInRun = false;
  private openerText = '';
  private lastFocus: HTMLElement | null = null;
  private scroll = new Map<string, number>();
  private filters = new Map<string, {query:string;category:string}>();
  private cache = document.createElement('div');
  private history: string[] = [];
  private details = new Map<string, Surface>();
  private navigate: (hash: string) => void;
  /** Where a model runs, as a chip row filter (Models only): any, this Mac, the cloud. */
  private readonly chips = document.createElement('div');
  private where: 'any' | 'mac' | 'cloud' = 'any';
  constructor(navigate: (hash: string) => void) {
    this.navigate = navigate;
    this.inline.className='study-data-detail';this.inline.hidden=true;
    this.inline.setAttribute('aria-label','Project data');
    const inlineHeader=document.createElement('header');
    const backToSpecimen=document.createElement('button');backToSpecimen.textContent='×';backToSpecimen.setAttribute('aria-label','Close data detail');
    backToSpecimen.addEventListener('click',()=>this.returnToSpecimen());
    inlineHeader.append(this.inlineTitle,backToSpecimen);this.inline.append(inlineHeader);
    studyArtifacts().mount(this.inline);
    this.dialog.className = 'study-overlay'; this.dialog.setAttribute('aria-labelledby', 'study-overlay-title');
    this.heading.id = 'study-overlay-title';
    // Models opens on its heading (focusable, not in the Tab order), so no focus ring frames its search after a click;
    // search is one Tab away, "/" goes there, and typing a letter on the heading starts a search with it.
    this.heading.tabIndex = -1;
    this.dialog.addEventListener('keydown', (event) => {
      if (this.search.hidden || event.metaKey || event.ctrlKey || event.altKey || event.isComposing) return;
      const active = document.activeElement;
      const typing = active instanceof HTMLElement && active.matches('input, textarea, select, [contenteditable=true]');
      if (event.key === '/' && !typing) { event.preventDefault(); this.search.focus(); return; }
      if (active === this.heading && event.key.length === 1 && event.key !== ' ') {
        event.preventDefault(); this.search.focus(); this.search.value += event.key; this.filter();
      }
    });
    const header = document.createElement('header'); header.className = 'study-overlay-header';
    const back = document.createElement('button'); back.textContent = '← Back'; back.className = 'study-overlay-back';
    back.addEventListener('click', () => { const previous = this.history.at(-1); if (previous) this.navigate(previous); });
    this.search.type = 'search'; this.search.placeholder = 'Search models'; this.search.setAttribute('aria-label', 'Search surface');
    this.search.addEventListener('input', () => this.filter());
    new MutationObserver(() => { if(this.dialog.open) this.filter(); }).observe(this.body,{childList:true,subtree:true});
    const close = document.createElement('button'); close.textContent = '×'; close.className = 'study-close'; close.setAttribute('aria-label', 'Close surface');
    close.addEventListener('click', () => this.dismiss());
    // Models' one action, in its header (the mock's modelsSheet): Add model or tool opens the three ways in.
    this.add.type = 'button'; this.add.className = 'study-overlay-add'; this.add.hidden = true;
    this.add.append(tabMark('plus', 'study-overlay-add-icon'), document.createTextNode('Add model or tool'));
    this.add.addEventListener('click', () => window.dispatchEvent(new CustomEvent('hooke-models-add')));
    // The search with its mark (the mock's search-wrap); the mark goes with it when it is hidden (CSS :has).
    const find = document.createElement('span'); find.className = 'study-overlay-search';
    find.append(tabMark('search', 'study-overlay-search-icon'), this.search);
    header.append(back, this.heading, this.add, find, close);
    this.body.className = 'study-overlay-body'; this.cache.hidden = true;
    this.categories.className = 'study-catalog-categories'; this.categories.setAttribute('aria-label', 'Model categories');
    // Categories in the cards' own words (model-status.ts), each with its count (concept mock: "Ready 6"). They select by a
    // card's status, never by matching its text, so rewording a status cannot empty a category.
    for (const [key, name] of SURFACE_CATEGORIES) {
      if (key === 'keys') this.categories.append(Object.assign(document.createElement('hr'), { className: 'study-catalog-rule' }));
      const button = document.createElement('button'); button.dataset.category = key;
      button.append(document.createTextNode(name), Object.assign(document.createElement('span'), { className: 'study-catalog-count' }));
      button.addEventListener('click', () => { this.category = key; if (key === 'storage') this.search.value = ''; this.filter(); }); this.categories.append(button);
    }
    // The add sheet's "Advanced contribution controls": Models' own section, shown as a category while it is open.
    // From a sheet alone over the Project (Tools' Add model or tool), Models itself opens first.
    window.addEventListener('hooke-models-category', event => { const key = (event as CustomEvent<string>).detail;
      if (!SURFACE_CATEGORIES.some(([k]) => k === key)) return;
      if (this.dialog.dataset.setupOnly === 'true') this.navigate('#models');
      this.category = key; this.filter(); });
    // The chip row under the header (ComfyUI's Templates sheet, Will 2026-10-23): one quiet filter, where it runs.
    this.chips.className = 'study-sheet-chips'; this.chips.setAttribute('role', 'group'); this.chips.setAttribute('aria-label', 'Where models run');
    for (const [key, name] of [['any', 'Anywhere'], ['mac', 'This Mac'], ['cloud', 'Cloud']] as const) {
      const chip = document.createElement('button'); chip.type = 'button'; chip.dataset.where = key; chip.textContent = name;
      chip.addEventListener('click', () => { this.where = key; this.filter(); }); this.chips.append(chip);
    }
    this.dialog.append(header, this.chips, this.categories, this.body); document.body.append(this.dialog, this.cache);
    // Escape closes the innermost layer first: a model's setup sheet or Details, a Library preview or category. The
    // page's own handler does that; only with none open does Escape close the sheet (2026-10-23 audit, F-rail-1).
    this.dialog.addEventListener('cancel', event => { event.preventDefault(); if (!this.innerLayer()) this.dismiss(); });
    document.addEventListener('pointerdown', event => { if (!this.dialog.open && event.target instanceof Element) this.lastFocus = event.target.closest<HTMLElement>('button,a,input') ?? (event.target instanceof HTMLElement ? event.target : null); }, true);
    document.addEventListener('keydown', event => {
      if (this.dialog.open && event.key === 'Escape') { if (this.innerLayer()) return; event.preventDefault(); event.stopImmediatePropagation(); this.dismiss(); }
      else if (!this.inline.hidden && event.key === 'Escape') { event.preventDefault();event.stopImmediatePropagation();this.returnToSpecimen(); }
      else if (!this.dialog.open) this.lastFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    }, true);
    window.addEventListener('workspace-study-surface', event => this.show((event as CustomEvent<Surface>).detail));
  }
  /** An open layer inside the page that Escape should close before the sheet. */
  private innerLayer(): Element | null {
    return this.body.querySelector('.tool-setup-sheet[aria-modal="true"], .tool-details:not([hidden]), .lib-preview:not([hidden]), .lib-crumb:not([hidden]):not(:empty)');
  }
  private stash(): void {
    if (!this.current?.node) return;
    this.scroll.set(this.current.hash, this.body.scrollTop);
    this.filters.set(this.current.hash,{query:this.search.value,category:this.category});
    this.cache.append(this.current.node);
  }
  show(surface: Surface): void {
    surface = this.details.get(surface.hash) ?? surface;
    if(surface.page==='research' && surface.node){this.close();surface.node.hidden=false;window.dispatchEvent(new CustomEvent('workspace-study-research-detail',{detail:surface.node}));return;}
    if(surface.page==='researchers'){this.close();window.dispatchEvent(new CustomEvent('workspace-study-section',{detail:'research'}));return;}
    const replacement = this.pages.get(surface.page);
    if (replacement) { if (surface.node) surface.node.hidden = true; surface = { ...surface, node: replacement }; }
    if (!surface.node) { this.close(); return; }
    if(surface.page==='assets'){surface.node.setAttribute('aria-label','Library');surface.node.querySelector('.lib-preview')?.setAttribute('role','region');}
    if (this.current?.hash === surface.hash && this.dialog.open) { surface.node.hidden = false; return; }
    const nested = this.dialog.open || !this.inline.hidden;
    if (!nested) {
      this.opener = this.lastFocus ?? document.activeElement as HTMLElement; this.history = [];
      this.openerResource = this.opener?.closest<HTMLElement>('[data-resource]')?.dataset.resource ?? null;
      this.openerText = this.opener?.textContent ?? '';
      this.openerAction = this.opener?.closest<HTMLElement>('[data-action]')?.dataset.action ?? null;
      this.openerInRun = !!this.opener?.closest('.run-foot');
    }
    else if (this.current) this.history = surfaceTrail(this.history,this.current.hash,surface.hash);
    this.stash(); this.current = surface;
    surface.node.hidden = false;
    this.body.replaceChildren(surface.node);
    this.heading.textContent = surface.page==='detail' ? decodeURIComponent(surface.hash.slice('#study-detail/'.length)) : titles[surface.page] ?? surface.page;
    this.dialog.dataset.surface = surface.page;
    // A setup sheet or Add model or tool opened from outside Models is that sheet alone over the Project, never the Models
    // page with the sheet on it (`sheetAlone`, workspace-feel.css); closing it returns to where it was opened.
    this.dialog.dataset.setupOnly = String(surface.page === 'models' && sheetAlone(surface.hash));
    this.categories.hidden = surface.page !== 'models';
    this.add.hidden = surface.page !== 'models';
    this.search.hidden = !['models', 'capabilities'].includes(surface.page);
    this.search.placeholder = `Search ${this.heading.textContent.toLowerCase()}`;
    this.search.value = this.filters.get(surface.hash)?.query ?? '';
    this.category = this.filters.get(surface.hash)?.category ?? 'all';
    const back = this.dialog.querySelector<HTMLElement>('.study-overlay-back')!; back.hidden = !this.history.length;
    const previous = this.history.at(-1); back.textContent = `← ${previous ? titles[previous.replace(/^#/, '').split('/')[0]] ?? 'Back' : 'Back'}`;
    this.chips.hidden = surface.page !== 'models';
    const inWorkspace=surface.page==='measurements' || surface.hash==='#study-detail/Provenance';
    this.inline.hidden=!inWorkspace;
    if(inWorkspace){
      this.dialog.close();this.inlineTitle.textContent=this.heading.textContent;this.inline.append(this.body);
    }else{
      this.dialog.append(this.body);
      if (!this.dialog.open) this.dialog.showModal();
    }
    this.body.scrollTop = this.scroll.get(surface.hash) ?? 0;
    document.body.dataset.studyOverlay = surface.hash;
    document.body.dataset.studySurface = surface.page;
    this.filter();
    // Models may have opened its guided sheet before the outer surface mounted.
    // Keep keyboard focus inside that modal, where its existing Tab/Escape handling owns it.
    const guidedSheet = surface.node.querySelector<HTMLElement>('.tool-setup-sheet[aria-modal="true"]');
    (inWorkspace ? this.inline.querySelector('button') : guidedSheet ?? (this.search.hidden
      ? this.dialog.querySelector<HTMLButtonElement>('[aria-label="Close surface"]') : surface.page === 'models' ? this.heading : this.search))?.focus();
  }
  detail(title: string, content: HTMLElement): void {
    const hash = `#study-detail/${encodeURIComponent(title)}`;
    this.details.set(hash, { page: 'detail', hash, node: content });
    this.navigate(hash);
    this.heading.textContent = title;
  }
  private returnToSpecimen(): void {
    this.dismiss();
  }
  dismiss(): void {
    this.navigate('#');
  }
  close(): void {
    if (!this.dialog.open && this.inline.hidden) return;
    this.inline.hidden=true;
    this.stash(); this.current = null; this.dialog.close(); delete this.dialog.dataset.setupOnly; delete document.body.dataset.studyOverlay; document.body.dataset.studySurface = 'live';
    const candidates = this.openerResource ? [...document.querySelectorAll<HTMLElement>(`[data-resource="${this.openerResource}"] button`)] : [];
    const actionButtons = this.openerAction ? [...document.querySelectorAll<HTMLElement>(`[data-action="${CSS.escape(this.openerAction)}"] button`)] : [];
    // A run page's step redrawn meanwhile (its key added, "Add key" is now "Run …"): back to that page's primary, never a
    // same-worded button of a list hidden under it.
    const runPrimary = this.openerAction ? document.querySelector<HTMLElement>(`.run[data-action="${CSS.escape(this.openerAction)}"] .run-foot button.run-go`) : null;
    const shown = (button: HTMLElement) => !button.checkVisibility || button.checkVisibility();
    const target = this.opener?.isConnected ? this.opener : (this.openerInRun ? runPrimary : null)
      ?? [...candidates,...actionButtons].find(button => button.textContent === this.openerText && shown(button))
      ?? runPrimary ?? document.querySelector<HTMLElement>('.study-rail [aria-current="page"]');
    target?.focus({ preventScroll: true });
    // A run page is a top-layer control, suspended while the modal was open: it shows again once the deck sees the
    // dialog close (its observer, a microtask later), so its primary takes focus then, unless something else has.
    if (this.openerInRun && document.activeElement !== target) setTimeout(() => {
      const again = document.querySelector<HTMLElement>(`.run[data-action="${CSS.escape(this.openerAction ?? '')}"] .run-foot button.run-go`);
      if (again && (!document.activeElement || document.activeElement === document.body)) again.focus({ preventScroll: true });
    }, 0);
  }
  private filter(): void {
    const query = this.search.value.trim().toLocaleLowerCase();
    for (const button of this.categories.querySelectorAll('button')) button.setAttribute('aria-pressed', String(button.dataset.category === this.category));
    const setup = this.category === 'keys', own = this.category === 'own';
    const storage = this.category === 'storage';
    this.search.hidden = storage || !['models', 'capabilities'].includes(this.current?.page ?? '');
    this.dialog.dataset.catalogCategory = storage ? 'storage' : setup ? 'setup' : own ? 'own' : 'catalog';
    for (const chip of this.chips.querySelectorAll('button')) chip.setAttribute('aria-pressed', String(chip.dataset.where === this.where));
    for (const card of this.body.querySelectorAll<HTMLElement>('.model-card[data-section]')) {
      const words = card.textContent?.toLocaleLowerCase() ?? '';
      const category = cardIn(card, this.category, words) && (this.where === 'any' || cardIn(card, this.where, words));
      card.hidden = !category || !!query && !words.includes(query);
    }
    const cards = [...this.body.querySelectorAll<HTMLElement>('.model-card[data-section]')];
    for (const button of this.categories.querySelectorAll<HTMLElement>('button[data-category]')) {
      const key = button.dataset.category!, count = button.querySelector('.study-catalog-count');
      const n = ['keys', 'own', 'storage'].includes(key) || !cards.length ? null : cards.filter(card => cardIn(card, key, card.textContent?.toLocaleLowerCase() ?? '')).length;
      if (count) count.textContent = n === null ? '' : String(n);
      // A section with nothing in it is not listed (the mock's side); Advanced contribution only while it is open.
      button.hidden = key === 'own' ? this.category !== 'own' : SECTION_CATEGORIES.has(key) && n === 0 && this.category !== key;
    }
    for (const group of this.body.querySelectorAll<HTMLElement>('.toolbox-section,.models-group,.toolbox-unavailable')) {
      const cards = [...group.querySelectorAll<HTMLElement>('.model-card[data-section]')];
      if (cards.length) group.hidden = cards.every(card => card.hidden);
      if (group.matches('.toolbox-unavailable') && !group.hidden && unavailableFoldOpens(this.category, query))
        (group as HTMLDetailsElement).open = true;
    }
  }
}
