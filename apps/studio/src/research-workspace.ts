// A researcher's Tab (ticket 66 D3; the mock's research, research-docs, research-sources and research-narrow frames in
// design/data-research, after the workspace-feel plan's researcherPage()): two columns told apart by tone. Its
// conversation on the left (the brief, its steps, its questions, your notes and follow-ups, the shared composer
// composer.ts); on the right the one Data reader (data-reader.ts, mode 'research'), whose title switches between
// everything it made (Data's entries whose maker it is) and the sources it cited. Its own write-ups are its page: the real
// output on a paper page in its own theme on the desk, which follows its writing, with Fit, download, its versions and
// its ⋯ in the reader's header and Sections in their own band; its tables, charts, outlines and sources draw in the same
// reader, so nothing it made leaves the Tab. The columns are container queries on the page (workspace-feel.css): two
// from 860 px, sharing the width; one below (Conversation | the reader, as inner Tabs). The divider drags, resets on a
// double-click and is remembered per researcher (research-tab.ts readLayout). Browser-local reading state only (drafts,
// scrolls, the item the reader shows), never a second research store: research.ts owns every fact and command, and
// this draws them.
import { Composer } from './composer';
import { popMenu, type MenuItem } from './drawer-menu';
import { litCell, type LitCellState } from './lit-cell';
import { DataReader, sourcesItem, type ReaderDeps, type ReaderEntry, type ReaderPage } from './data-reader';
import { PAGE_ONLY_SECTIONS, outlineOf, readLayout, saveLayout, type ResearchFile, type ResearchLayout, type ResearchPillView, type ResearchUsed } from './research-tab';

interface ReadingState { draft: string; scroll: number; artifactScroll: number; atBottom?: boolean; deskLatest?: boolean;
  one?: 'conv' | 'art'; focused?: boolean; selection?: [number, number];
  /** What the reader showed when it was not its page (a table, outlines, its sources), to show again on return. */
  doc?: { id: string; day?: string; view?: 'thing' | 'how' } | null }
interface WorkspaceState { key: string; title: string; working: boolean; writable: boolean; canSend: boolean; status?: string;
  statusCode?: string; researcher?: string; output?: string | null; stopRequested?: boolean; rid?: string;
  pill?: ResearchPillView; engine?: string; placeholder?: string; waiting?: boolean;
  /** The Project its work is in (its entries' `day`), the entry its page shows (`<rid>` or `<rid>:<output id>`), and
   * that page's kind in a word ("Presentation", "Report", "Lesson"). */
  project?: string; item?: string; kind?: string }
/** The artifact on the desk: its title, the page's theme (the presentation's own; paper when none was chosen), and the
 * one line said while nothing is written. */
export interface ArtifactInfo { title: string; theme?: string | null; frame?: boolean; empty?: string; stage?: HTMLElement | null }
/** The page's controls: its versions (the reader's ⋯ and How it was made), its own part of How it was made, its ⋯ and
 * a download where a real file exists. */
export interface ArtifactControls { versions: { label: string; value: string; checked: boolean }[]; choose: (value: string) => void;
  how: () => HTMLElement; more: MenuItem[]; download: { url: string; name: string } | null }
/** What the workspace asks research.ts to do: show a deliverable or a version on its page (by its value, or by its
 * Data entry's id), open a tool's run page, go to Main, attach a file or a section to the next message, and ask a new
 * researcher. */
export interface WorkspaceHandlers {
  show(value: string): void;
  showItem(id: string): void;
  tool(key: string): void;
  main(): void;
  attachFile(file: ResearchFile): void;
  attachSection(anchor: string, title: string): void;
  newResearcher(): void;
}

/** What the workspace gives the researcher's reader (workspace-study.ts `provideResearchReader`): this Project's Data and
 * the reader's ways out (Open in new Tab, Add to Tab…, Go to a Tab, Show on Main ↗, All its work in Data, Talk to
 * another researcher). Without it (a test, the older shell) the reader shows the researcher's own page and its sources,
 * and nothing leaves the Tab. */
export interface ResearchReaderProvider {
  entries(): ReaderEntry[];
  deps: Partial<ReaderDeps>;
}
let provider: ResearchReaderProvider | null = null;
let current: ResearchWorkspace | null = null;
/** An item to show when a researcher's Tab next opens (Talk to Researcher N from another Tab's reader). */
const pending = new Map<string, { id: string; day?: string; view?: 'thing' | 'how' }>();
export function provideResearchReader(value: ResearchReaderProvider | null): void { provider = value; current?.refreshReader(); }
/** A Data row clicked while a researcher's Tab is in front shows in its reader. False when none is in front. */
export function showInResearchReader(entry: ReaderEntry, view: 'thing' | 'how' = 'thing'): boolean { return current?.showEntry(entry, view) ?? false; }
/** Talk to Researcher N from another Tab: its Tab opens with the same item on its right. */
export function openOnResearcherTab(rid: string, ref: { id: string; day?: string }): void { pending.set(rid, ref); current?.refreshReader(); }
/** This Project's Data changed: the reader's switcher and the item it shows follow. */
export function researchReaderEntriesChanged(): void { current?.refreshReader(); }
/** The reader of the researcher's Tab (the screen report and the measurements read it). */
export function researchReader(): DataReader | null { return current?.reader ?? null; }

const THEMES = new Set(['paper', 'graphite', 'contrast', 'accent']);
const FITS = [['width', 'Fit width'], ['page', 'Fit page'], ['100', '100%'], ['200', '200%']] as const;
const SVG = 'http://www.w3.org/2000/svg';
/** The researcher Tab's icons: the mock's one set (20 px grid, 1.5 px stroke). */
const ICONS: Record<string, string> = {
  presentation: '<rect x="3.5" y="4.5" width="13" height="9" rx="1"/><path d="M10 13.5v2.6M7.4 16.1h5.2"/>',
  report: '<path d="M6 3.5h6l3 3V16a.5.5 0 0 1-.5.5h-8.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z"/><path d="M8 9.5h5M8 12h5M8 14.5h3"/>',
  lesson: '<path d="M3.5 6 10 3.5 16.5 6 10 8.5z"/><path d="M6 7.2v4.3c1 1.2 2.4 1.8 4 1.8s3-.6 4-1.8V7.2"/>',
  video: '<rect x="3.5" y="5" width="9.5" height="10" rx="1.5"/><path d="m13 8.5 3.5-2v7l-3.5-2"/>',
  picture: '<rect x="3.5" y="4.5" width="13" height="11" rx="1.5"/><path d="m4 14 4-4 3 3 2-2 3.5 3"/>',
  figure: '<rect x="3.5" y="4.5" width="13" height="11" rx="1.5"/><path d="m4 14 4-4 3 3 2-2 3.5 3"/>',
  table: '<rect x="3.5" y="4" width="13" height="12" rx="1.5"/><path d="M3.5 8h13M3.5 12h13M8 8v8M12.5 8v8"/>',
  mask: '<path d="M7.6 4.2 11 6.2v3.9l-3.4 2-3.4-2V6.2z"/><path d="M12.4 8.4l3.4 2v3.9l-3.4 2-3.4-2"/>',
  tool: '<path d="M12.6 3.6a3.6 3.6 0 0 0-4 4.9L3.8 13.3a1.4 1.4 0 0 0 2 2l4.8-4.8a3.6 3.6 0 0 0 4.9-4l-2.1 2.1-1.9-.4-.4-1.9z"/>',
  record: '<path d="M6 3.5h6l3 3V16a.5.5 0 0 1-.5.5h-8.5a.5.5 0 0 1-.5-.5V4a.5.5 0 0 1 .5-.5z"/><path d="M8 9h5M8 12h5"/>',
  bubble: '<path d="M4.5 4.5h11a1 1 0 0 1 1 1v7a1 1 0 0 1-1 1H9.5l-3.5 2.8v-2.8H4.5a1 1 0 0 1-1-1v-7a1 1 0 0 1 1-1z"/>',
  down: '<path d="m6 8 4 4 4-4"/>', right: '<path d="m8 5 5 5-5 5"/>', left: '<path d="m12 5-5 5 5 5"/>',
  x: '<path d="M5.5 5.5l9 9M14.5 5.5l-9 9"/>', more: '<circle cx="5" cy="10" r="1.4" fill="currentColor" stroke="none"/><circle cx="10" cy="10" r="1.4" fill="currentColor" stroke="none"/><circle cx="15" cy="10" r="1.4" fill="currentColor" stroke="none"/>',
  download: '<path d="M10 3.8v8.7M6.6 9.2 10 12.6l3.4-3.4M4.5 15.8h11"/>',
  tick: '<path d="m4.5 10.5 3.5 3.5 7.5-8"/>',
  popout: '<path d="M11 4.5h4.5V9"/><path d="M15.5 4.5 9.5 10.5"/><path d="M14 11.5v3a1 1 0 0 1-1 1H5.5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h3"/>',
  plus: '<path d="M10 4.5v11M4.5 10h11"/>',
};
export function researchIcon(name: string, className = 'rtab-icon'): SVGSVGElement {
  const svg = (document.createElementNS?.(SVG, 'svg') ?? document.createElement('svg')) as SVGSVGElement;
  svg.setAttribute('viewBox', '0 0 20 20'); svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('class', className);
  svg.innerHTML = ICONS[name] ?? ICONS.report;
  return svg;
}
/** A file a message can ask about: what it wrote and the tables and charts in its findings (the server checks each
 * selection against them). A result it had the app make lives in the Project, and its notes are not findings. */
const askable = (file: ResearchFile) => !('record' in file.open) && !('show' in file.open && file.open.show === 'notes');
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const el = document.createElement(tag); if (className) el.className = className; if (text) el.textContent = text; return el;
};
/** Its page draws these of its entries: its report (`<rid>`), its lessons, videos and pictures (`<rid>:o<n>`), and the
 * studio's older `<rid>#<output>` rows. */
export function pageOwns(rid: string, id: string): boolean {
  return id === rid || id.startsWith(`${rid}#`) || id.startsWith(`${rid}:`) && /^o\d+$/.test(id.slice(rid.length + 1));
}

export class ResearchWorkspace {
  readonly thread = node('div', 'rlog research-thread');
  readonly artifact = node('section', 'rart research-artifact');
  readonly content = node('div', 'page-in');
  /** The one reader on the right (data-reader.ts, mode 'research'). */
  readonly reader: DataReader;
  private readonly page = node('div', 'rpage');
  private readonly conversation = node('section', 'rconv research-conversation');
  private readonly convBody = node('div', 'rconv-body');
  private readonly tabList = node('div', 'ctabs');
  private readonly suggestions = node('div', 'research-suggestions');
  private suggestionsSignature = '';
  private readonly error = node('p', 'research-composer-error');
  private readonly compose: Composer;
  readonly composer: HTMLFormElement;
  private readonly fitButton = node('button', 'tbtn');
  private readonly downloadLink = node('a', 'rtab-icon-button rart-download');
  /** Its page, as the reader shows it: the desk (the presentation's progress line over the page) and the Sections bar in
   * its band. */
  private readonly pageHost = node('div', 'rpage-host');
  private readonly stage = node('div', 'research-presentation-state');
  private readonly desk = node('div', 'desk');
  private readonly deskPages = node('div', 'desk-pages');
  private readonly paper = node('article', 'page');
  private readonly deskEmpty = node('p', 'desk-empty');
  private readonly pageBar = node('div', 'fbar');
  private readonly pageCount = node('button', 'fbar-n');
  private readonly divider = node('div', 'rdiv');
  private readonly states = new Map<string, ReadingState>();
  private state: WorkspaceState | null = null;
  private threadSignature = '';
  private artifactSignature = '';
  private restore: ReadingState | null = null;
  private artifactRestore: number | null = null;
  private readonly markup = new WeakMap<HTMLElement, string>();
  private visible = false;
  private controls: ArtifactControls | null = null;
  private fileRows: ResearchFile[] = [];
  private usedRows: ResearchUsed[] = [];
  private anchors = new Set<string>();
  private anchorsKey = '';
  private layout: ResearchLayout = {};
  private handlers: WorkspaceHandlers | null = null;
  private foots: { spend: HTMLElement; popout: HTMLElement } | null = null;

  constructor(root: HTMLElement, send: (text: string) => void) {
    root.classList.add('research-workspace');
    current = this;
    // The conversation: its header holds its inner Tabs (Conversation; in one column, the reader's too).
    this.conversation.setAttribute('aria-label', 'Conversation');
    const convHead = node('div', 'rhead rconv-head');
    this.tabList.setAttribute('role', 'tablist'); this.tabList.setAttribute('aria-label', 'Conversation and what it made');
    this.tabList.addEventListener('keydown', (event) => this.roveTabs(event as KeyboardEvent));
    convHead.append(this.tabList);
    this.thread.setAttribute('aria-label', 'Researcher conversation');
    this.thread.setAttribute('role', 'log');
    this.thread.addEventListener('scroll', () => this.remember());
    this.convBody.append(this.thread);
    // The shared composer (composer.ts): no Talk (voice goes to the Project's Assistant), + attaches only files this
    // researcher made, and a model chip naming its engine, fixed for the run.
    this.compose = new Composer(null, { label: 'Message to this researcher', placeholder: 'Ask this researcher',
      attach: { label: 'Ask about a file it made', items: () => this.attachItems() },
      model: { words: '', title: '', open: (chip) => this.engineMenu(chip) } });
    this.composer = this.compose.root;
    this.composer.classList.add('research-composer-dock');
    this.compose.field.id = 'research-reply';
    this.compose.field.addEventListener('input', () => this.remember());
    this.composer.addEventListener('submit', (event) => {
      event.preventDefault();
      const text = this.compose.field.value.trim();
      if (!text || !this.state?.writable || !this.state.canSend) return;
      try { send(text); this.error.hidden = true; }
      catch (error) {
        this.error.textContent = error instanceof Error ? error.message : 'Could not send. Your draft is kept.';
        this.error.hidden = false; this.remember(); return;
      }
      this.compose.set('');
      this.remember();
    });
    this.error.hidden = true; this.error.setAttribute('role', 'status');
    this.suggestions.hidden = true;
    this.suggestions.setAttribute('role', 'group'); this.suggestions.setAttribute('aria-label', 'Suggestions');
    const dock = node('div', 'rcomp');
    dock.append(this.suggestions, this.error, this.composer);
    this.conversation.append(convHead, this.convBody, dock);
    // Its page: the progress line, the desk and the Sections bar, handed to the reader for its write-ups.
    this.fitButton.type = 'button'; this.fitButton.setAttribute('aria-haspopup', 'menu'); this.fitButton.setAttribute('aria-expanded', 'false');
    this.fitButton.addEventListener('click', () => this.fitMenu());
    this.downloadLink.append(researchIcon('download'));
    this.downloadLink.hidden = true;
    this.desk.setAttribute('role', 'region'); this.desk.setAttribute('aria-label', 'The work as written');
    this.desk.tabIndex = 0;
    this.desk.dataset.fit = 'width';
    this.desk.addEventListener('scroll', () => this.remember());
    this.paper.append(this.content);
    this.deskPages.append(this.paper);
    this.desk.append(this.stage, this.deskPages, this.deskEmpty);
    this.deskEmpty.hidden = true;
    // The page bar holds only what can act: Sections (the page's outline, to go to one). The work is one flowing page,
    // so there is no page count and no arrows until it is paged.
    this.pageCount.type = 'button'; this.pageCount.setAttribute('aria-haspopup', 'menu'); this.pageCount.setAttribute('aria-expanded', 'false');
    this.pageCount.textContent = 'Sections'; this.pageCount.title = 'Go to a section of the page';
    this.pageCount.addEventListener('click', () => this.outlineMenu());
    this.pageBar.setAttribute('role', 'toolbar'); this.pageBar.setAttribute('aria-label', 'The page');
    this.pageBar.append(this.pageCount);
    const foot = node('div', 'rart-foot'); foot.append(this.pageBar);
    this.pageHost.append(this.desk, foot);
    // The reader: the right column, beside the divider.
    this.reader = new DataReader(this.readerDeps(), 'research');
    this.artifact.setAttribute('aria-label', 'What it made');
    this.divider.title = 'Drag to resize. Double-click to reset.';
    this.divider.setAttribute('role', 'separator'); this.divider.setAttribute('aria-orientation', 'vertical');
    this.bindDivider();
    this.artifact.append(this.divider, this.reader.root);
    this.page.append(this.conversation, this.artifact);
    const wrap = node('div', 'rwrap'); wrap.append(this.page);
    root.replaceChildren(wrap);
    this.page.dataset.one = 'conv';
    this.syncTabs();
  }

  /** research.ts's handlers for steps, chips and menus. */
  bind(handlers: WorkspaceHandlers): void { this.handlers = handlers; }

  /** Today's research spending and Open in a new window: the spending is said at the foot of How it was made, and Open
   * in a new window is in the page's ⋯ (research.ts). */
  foot(spend: HTMLElement, popout: HTMLElement): void { this.foots = { spend, popout }; }

  attach(label: string | null, clear: () => void): void {
    if (!label) { this.compose.setChips(); return; }
    const chip = node('span', 'mm-composer-chip rtab-selection');
    chip.append(node('span', 'mm-composer-chip-name', label));
    const remove = node('button', 'mm-composer-chip-remove'); remove.type = 'button';
    remove.setAttribute('aria-label', `Stop asking about ${label}`); remove.title = 'Remove';
    remove.append(researchIcon('x')); remove.addEventListener('click', clear);
    chip.append(remove);
    this.compose.setChips(chip);
  }

  suggest(questions: string[]): void {
    const items = this.state?.writable && this.state.canSend && !this.state.working ? [...new Set(questions)].slice(0, 2) : [];
    const signature = JSON.stringify(items);
    if (signature === this.suggestionsSignature) return;
    this.suggestionsSignature = signature;
    this.suggestions.hidden = !items.length;
    this.suggestions.replaceChildren(...items.map(question => {
      const button = node('button', 'rtab-suggestion', question); button.type = 'button'; button.title = question; // whole, where it ends in an ellipsis
      button.addEventListener('click', () => {
        this.compose.set(question);
        this.compose.field.focus?.({preventScroll:true});
        this.remember();
      });
      return button;
    }));
  }

  update(state: WorkspaceState): void {
    const switched = this.state?.key !== state.key;
    if (switched) {
      this.remember();
      this.error.hidden = true;
      this.state = state;
      this.suggest([]);
      let reading = this.states.get(state.key);
      if (!reading) {
        try { reading = JSON.parse(sessionStorage.getItem(`research-reading:${state.key}`) ?? 'null') as ReadingState | null ?? undefined; }
        catch { /* Browser storage is optional; research persistence is server-owned. */ }
        reading ??= { draft: '', scroll: 0, artifactScroll: 0 };
        this.states.set(state.key, reading);
      }
      this.compose.set(reading.draft);
      this.restore = reading;
      this.artifactRestore = reading.artifactScroll;
      this.page.dataset.one = reading.one === 'art' ? 'art' : 'conv';
      this.threadSignature = this.artifactSignature = '';
      this.anchorsKey = '';
      this.layout = state.rid ? readLayout(state.rid) : {};
      this.applyLayout();
    }
    this.state = state;
    if (!state.writable || !state.canSend || state.working) this.suggest([]);
    const placeholder = state.placeholder ?? (!state.writable ? 'Earlier work · read only' : `Ask ${state.researcher ?? 'this researcher'} anything`);
    this.compose.field.placeholder = placeholder;
    this.compose.field.setAttribute('aria-label', placeholder);
    this.compose.field.disabled = this.compose.send.disabled = !state.writable || !state.canSend;
    this.compose.setModel(state.engine ?? '', state.engine ? `${state.researcher ?? 'This researcher'} runs on ${state.engine}` : '');
    if (this.compose.attachButton) this.compose.attachButton.hidden = !state.writable;
    this.syncTabs();
    this.syncCards();
    // Another researcher's page: the reader shows what it showed there last (or the item Talk asked for), else its page.
    if (switched) this.openReader();
  }

  /** Unchanged content keeps its DOM: iframe/quiz, focus, expanded details and media playhead. The log follows the
   * bottom only while the reader is there (the chat's rule). */
  renderThread(signature: string, nodes: HTMLElement[]): void {
    if (signature === this.threadSignature) return;
    const following = !(this.thread.scrollHeight - this.thread.clientHeight - this.thread.scrollTop >= 48);
    const scroll = this.thread.scrollTop;
    const old = new Map([...this.thread.children].map((child, i) => [child.getAttribute('data-message-id') ?? String(i), child as HTMLElement]));
    const retained = nodes.map((item, i) => {
      const previous = this.restore ? undefined : old.get(item.dataset.messageId ?? String(i));
      const markup = item.outerHTML;
      if (previous && markup !== undefined && this.markup.get(previous) === markup) return previous;
      if (previous) {
        const expanded = new Map([...previous.querySelectorAll('details')].map((d) => [d.querySelector('summary')?.textContent, (d as HTMLDetailsElement).open]));
        [...item.querySelectorAll('details')].forEach((d) => { (d as HTMLDetailsElement).open = expanded.get(d.querySelector('summary')?.textContent) ?? (d as HTMLDetailsElement).open; });
      }
      this.markup.set(item, markup);
      return item;
    });
    // Move only new/changed messages; keep focused controls and open disclosures connected.
    retained.forEach((item, i) => { if (this.thread.children[i] !== item) this.thread.insertBefore(item, this.thread.children[i] ?? null); });
    while (this.thread.children.length > retained.length) this.thread.lastElementChild?.remove();
    this.thread.scrollTop = this.restore ? (this.restore.atBottom !== false ? this.thread.scrollHeight : this.restore.scroll) : following ? this.thread.scrollHeight : scroll;
    if (this.restore?.focused && !this.compose.field.disabled) {
      this.compose.field.focus?.({ preventScroll: true });
      if (this.restore.selection) this.compose.field.setSelectionRange?.(...this.restore.selection);
    }
    this.restore = null;
    this.threadSignature = signature;
    this.syncCards();
  }

  /** The page, rendered by the existing renderers, on paper in its own theme. New sections rise in once while it is
   * written; the desk follows new writing only while the reader is at the latest part. */
  renderArtifact(signature: string, nodes: HTMLElement[], info: ArtifactInfo = { title: '' }): void {
    // The reader's header follows every render (its state words, versions, the deliverable shown) once, here: research.ts
    // draws the page last.
    this.refreshReader();
    // Unchanged work keeps its DOM, its state line too (a lesson step being chosen, Try saving again in focus).
    if (signature === this.artifactSignature) return;
    const latest = this.desk.scrollHeight - this.desk.clientHeight - this.desk.scrollTop < 48;
    const scroll = this.desk.scrollTop;
    const old = new Map([...this.content.children].map((child) => [(child as HTMLElement).dataset.artifactKey, child as HTMLElement]));
    const retained = nodes.map((item) => {
      const previous = this.artifactRestore === null && item.dataset.artifactKey ? old.get(item.dataset.artifactKey) : undefined;
      const markup = item.outerHTML;
      if (previous && markup !== undefined && this.markup.get(previous) === markup) return previous;
      this.markup.set(item, markup);
      return item;
    });
    // Leave unchanged artifact nodes connected: moving an iframe also resets its browsing context.
    for (const child of [...this.content.children]) if (!retained.includes(child as HTMLElement)) child.remove();
    retained.forEach((item, index) => {
      if (this.content.children[index] !== item) this.content.insertBefore(item, this.content.children[index] ?? null);
    });
    const empty = !nodes.length;
    this.paper.hidden = empty;
    this.deskEmpty.hidden = !empty;
    this.deskEmpty.textContent = empty ? info.empty ?? 'Nothing written yet' : '';
    this.paper.dataset.theme = info.theme && THEMES.has(info.theme) ? info.theme : 'paper';
    this.paper.classList.toggle('is-frame', !!info.frame);
    this.stage.replaceChildren(...(info.stage ? [info.stage] : []));
    this.stage.hidden = !info.stage;
    this.desk.dataset.empty = String(empty);
    this.markNew();
    this.desk.scrollTop = this.artifactRestore ?? (latest && this.state?.working ? this.desk.scrollHeight : scroll);
    this.artifactRestore = null;
    this.artifactSignature = signature;
    this.syncPageBar();
    this.syncTabs();
  }

  /** What it made (the composer's + asks about these) and what it worked from (How it was made says it). */
  renderFiles(files: ResearchFile[], used: ResearchUsed[]): void { this.fileRows = files; this.usedRows = used; }

  artifactControls(controls: ArtifactControls): void {
    this.controls = controls;
    const download = controls.download;
    this.downloadLink.hidden = !download;
    if (download) {
      this.downloadLink.href = download.url; this.downloadLink.setAttribute('download', download.name);
      this.downloadLink.setAttribute('aria-label', `Download ${download.name}`); this.downloadLink.title = 'Download';
    }
  }

  /** How it was made: the reader's second view (its ⋯ and Versions open it). */
  showHow(): void {
    if (this.columns() === 1) this.setOne('art');
    const page = this.pageView();
    this.reader.show(this.reader.current() ?? page?.entry ?? sourcesItem(this.state?.rid ?? '', this.state?.project ?? ''), 'how');
  }

  /** In one column, show the reader. */
  showPresentation(): void { this.setOne('art'); }

  /** The lesson's Escape: back to the conversation in one column, else to the composer. */
  closePresentation(): void {
    if (this.columns() === 1 && this.page.dataset.one === 'art') { this.setOne('conv'); return; }
    this.compose.field.focus?.({ preventScroll: true });
  }

  setVisible(visible: boolean): void {
    if (this.visible && !visible) this.remember();
    this.visible = visible;
    this.reader.setTicking(visible);
    this.syncCards();
  }

  /** Scroll its page to a section and let it glow once (a step's chip, Sections); the reader shows its page first. */
  reveal(anchor: string): void {
    const page = this.pageView(), shown = this.reader.current();
    if (page && (!shown || !page.owns(shown.id))) this.reader.show(page.entry, 'thing');
    const target = this.content.querySelector<HTMLElement>(`[data-anchor="${anchor}"]`);
    if (!target) return;
    if (this.columns() === 1) this.setOne('art');
    const top = target.getBoundingClientRect().top - this.desk.getBoundingClientRect().top + this.desk.scrollTop - 24;
    if (typeof this.desk.scrollTo === 'function') this.desk.scrollTo({ top, behavior: 'smooth' }); else this.desk.scrollTop = top;
    this.glow(target);
  }

  /** A step row glows once: the step that made what was chosen on the right. */
  flashStep(key: string): void {
    const row = this.thread.querySelector<HTMLElement>(`[data-step="${key}"]`);
    if (!row) return;
    this.glow(row);
    if (this.columns() === 1) this.setOne('conv');
  }

  /** Something it made, in the reader (a step's link, a table's Open on its page): `record:<id>` a result it had the app
   * make, `table:<n>` and `chart:<n>` its findings' (counted from 0, their Data ids from 1). */
  openFileTab(key: string): void {
    const rid = this.state?.rid; if (!rid) return;
    const [kind, rest] = [key.slice(0, key.indexOf(':')), key.slice(key.indexOf(':') + 1)];
    if (kind === 'record') { this.openRef({ id: rest, day: this.state?.project }); return; }
    if (kind !== 'table' && kind !== 'chart') return;
    const index = Number(rest); if (!Number.isInteger(index) || index < 0) return;
    const id = `${rid}:${kind}-${index + 1}`;
    // An older server lists no finding tables or charts: the reader still draws it from the researcher's session.
    const entry = this.entryOf(id) ?? { id, kind: kind === 'table' ? 'finding-table' : 'finding-chart', title: this.fileRows.find((f) => f.key === key)?.name ?? '',
      summary: '', state: '', actions: [], day: this.state?.project, source: { index }, maker: { kind: 'researcher', id: rid, name: this.state?.researcher ?? '' } } as unknown as ReaderEntry;
    this.showEntry(entry, 'thing', true);
  }

  /** A Data item in this Tab's reader (a Data row, a link inside the reader). False when this Tab is not in front. */
  showEntry(entry: ReaderEntry, view: 'thing' | 'how' = 'thing', always = false): boolean {
    if (!always && (!this.visible || !this.state)) return false;
    if (this.columns() === 1) this.setOne('art');
    this.reader.choose(entry, view);
    return true;
  }

  /** The reader follows the page (its state, versions, the deliverable shown) and Data's entries. */
  refreshReader(): void {
    if (!this.state?.rid) return;
    if (pending.has(this.state.rid)) this.openReader(); else this.reader.pageChanged();
  }

  /** The reader on arriving at a researcher's page: the item Talk asked for, else what it showed here last (a table,
   * outlines, its sources), else its page. */
  private openReader(): void {
    const page = this.pageView(); if (!page) return;
    const asked = pending.get(page.rid), want = asked ?? this.reading()?.doc ?? null;
    if (asked && (provider?.entries().length || !provider)) pending.delete(page.rid); // waits only until Data has arrived
    if (want && !page.owns(want.id)) {
      const entry = want.id === `${page.rid}:sources` ? sourcesItem(page.rid, page.day) : this.entryOf(want.id);
      if (entry) { pending.delete(page.rid); this.reader.show(entry, want.view ?? 'thing'); return; }
    }
    this.reader.show(page.entry, 'thing');
  }

  private openRef(ref: { id: string; day?: string }): void {
    const entry = this.entryOf(ref.id);
    if (entry) { this.showEntry(entry, 'thing', true); return; }
    this.reader.showMissing('Not in Data', 'It is not in this Project’s Data. Its researcher’s own record still names it.');
  }

  /** This Project's Data entry by its id (the workspace's index), or null. */
  private entryOf(id: string): ReaderEntry | null {
    const day = this.state?.project;
    return provider?.entries().find((entry) => entry.id === id && (!day || (entry.day ?? day) === day)) ?? null;
  }

  /** Its page, for the reader: what it shows now, its state, its tools, versions and ⋯, and its part of How it was made. */
  private pageView(): ReaderPage | null {
    const s = this.state; if (!s?.rid) return null;
    const rid = s.rid, day = s.project ?? '', id = s.item ?? rid, name = s.researcher ?? '';
    const entry = this.entryOf(id) ?? { id, kind: id === rid ? 'research' : 'lesson', title: s.title, summary: '', state: '', actions: [], day,
      role: 'writeup', maker: { kind: 'researcher', id: rid, name } } as ReaderEntry;
    const controls = this.controls;
    return {
      rid, name, day, entry,
      owns: (other) => pageOwns(rid, other),
      show: (other) => this.handlers?.showItem(other),
      host: this.pageHost,
      kind: s.kind ?? 'Report', title: s.title,
      cell: (s.pill?.cell ?? 'idle') as LitCellState, words: s.pill?.words ?? '', since: s.pill?.since ?? null,
      tools: [this.fitButton, ...(this.downloadLink.hidden ? [] : [this.downloadLink])],
      more: controls?.more ?? [],
      versions: (controls?.versions ?? []).map((version) => ({ label: version.label, checked: version.checked, choose: () => controls?.choose(version.value) })),
      how: () => this.howBox(),
    };
  }

  private readerDeps(): ReaderDeps {
    const given = () => provider?.deps;
    const deps: ReaderDeps = {
      researchers: () => given()?.researchers?.() ?? (this.state?.rid ? [{ rid: this.state.rid, name: this.state.researcher ?? '' }] : []),
      working: (rid) => given()?.working?.(rid) ?? (rid === this.state?.rid && !!this.state?.working),
      method: (entry) => given()?.method?.(entry) ?? '',
      input: (id) => given()?.input?.(id) ?? null,
      places: (entry) => given()?.places?.(entry) ?? [],
      tab: () => given()?.tab?.() ?? '',
      close: () => {}, back: () => {},
      talk: (rid, entry) => given()?.talk?.(rid, entry),
      openInNewTab: (entry) => given()?.openInNewTab?.(entry),
      showOnPicture: async () => {}, onPicture: () => false,
      addToTab: (entry, anchor) => given()?.addToTab?.(entry, anchor),
      goTo: (tab) => given()?.goTo?.(tab),
      routes: () => [],
      trash: (entry) => given()?.trash?.(entry) ?? { label: 'Move to trash', disabled: true, title: 'Not yet: results stay kept in Data.', act: () => {} },
      editNote: (entry) => given()?.editNote?.(entry),
      open: (ref) => this.openRef(ref),
      changed: () => { this.rememberDoc(); given()?.changed?.(); },
      page: () => this.pageView(),
      made: () => provider?.entries() ?? [],
    };
    // Ways out that only the workspace has: offered only when it gives them.
    Object.defineProperties(deps, {
      allInData: { get: () => given()?.allInData },
      leave: { get: () => given()?.leave },
      home: { get: () => given()?.home },
    });
    return deps;
  }

  /** Its part of How it was made: what it worked from (the live view it was asked on opens Main, the records it read
   * open in this reader, the tools it ran open their run pages), then its record's own words (basis, what it noted,
   * where it read, its effort and spending, the live view when it was asked), then today's research spending. */
  private howBox(): HTMLElement | null {
    const box = node('div', 'rhow-body');
    if (this.usedRows.length) {
      const used = node('section', 'rhow-used');
      used.append(node('h3', 'rhow-h', 'Worked from'), ...this.usedRows.map((row) => this.usedRow(row)));
      box.append(used);
    }
    const own = this.controls?.how();
    if (own) box.append(...[...own.children]);
    if (this.foots?.spend.textContent) box.append(node('p', 'rhow-spend', this.foots.spend.textContent));
    return box.children.length ? box : null;
  }

  private usedRow(used: ResearchUsed): HTMLElement {
    const row = node('button', 'frow'); row.type = 'button';
    row.dataset.used = used.key;
    row.title = 'main' in used.open ? `${used.name}, on Main` : 'tool' in used.open ? `${used.name}, in Tools` : `Open ${used.name} here`;
    if (used.kind === 'specimen' && used.thumb) {
      const thumb = node('span', 'frow-thumb'); thumb.style.backgroundImage = `url("${used.thumb.replace(/"/g, '%22')}")`; row.append(thumb);
    } else row.append(researchIcon(used.kind === 'tool' ? 'tool' : used.kind === 'record' ? 'record' : 'picture'));
    const words = node('span', 'frow-words'); words.append(node('span', 'frow-name', `${used.name}${'main' in used.open || 'tool' in used.open ? ' ↗' : ''}`));
    row.append(words);
    row.addEventListener('click', () => {
      if ('main' in used.open) this.handlers?.main();
      else if ('tool' in used.open) this.handlers?.tool(used.open.tool);
      else this.openRef({ id: used.open.record.id, day: this.state?.project });
    });
    return row;
  }

  private glow(target: HTMLElement): void {
    target.classList.remove('is-glow');
    void target.offsetWidth;
    target.classList.add('is-glow');
    setTimeout(() => target.classList.remove('is-glow'), 1700);
  }

  private reading(): ReadingState | undefined { return this.state ? this.states.get(this.state.key) : undefined; }

  private columns(): 1 | 2 {
    const width = this.page.getBoundingClientRect?.().width ?? 0;
    return width >= 860 || width <= 0 ? 2 : 1;
  }

  private setLayout(patch: Partial<Record<keyof ResearchLayout, unknown>>): void {
    this.layout = this.state?.rid ? saveLayout(this.state.rid, patch) : { ...this.layout, ...patch } as ResearchLayout;
    this.applyLayout();
  }

  private applyLayout(): void {
    this.page.style.setProperty('--conv-w', this.layout.conv ? `${this.layout.conv}px` : '');
    this.page.style.setProperty('--conv-w2', this.layout.conv2 ? `${this.layout.conv2}px` : '');
    if (!this.layout.conv) this.page.style.removeProperty?.('--conv-w');
    if (!this.layout.conv2) this.page.style.removeProperty?.('--conv-w2');
  }

  private setOne(one: 'conv' | 'art'): void {
    this.page.dataset.one = one;
    const reading = this.reading(); if (reading) reading.one = one;
    this.syncTabs();
    this.remember();
  }

  /** The inner Tabs: Conversation, and in one column the reader's own (its page's title and the researcher's cell). */
  private syncTabs(): void {
    const one = this.page.dataset.one === 'art';
    const conv = node('div', 'ctab is-conv'); conv.setAttribute('aria-selected', String(!one));
    const convButton = node('button', 'ctab-btn'); convButton.type = 'button'; convButton.setAttribute('role', 'tab');
    convButton.setAttribute('aria-selected', String(!one)); convButton.tabIndex = one ? -1 : 0; convButton.title = 'Conversation'; convButton.dataset.tab = 'conv';
    convButton.append(researchIcon('bubble'), node('span', '', 'Conversation'));
    convButton.addEventListener('click', () => this.setOne('conv'));
    conv.append(convButton);
    const art = node('div', 'ctab ctab-art'); art.setAttribute('aria-selected', String(one));
    const artButton = node('button', 'ctab-btn'); artButton.type = 'button'; artButton.setAttribute('role', 'tab');
    artButton.setAttribute('aria-selected', String(one)); artButton.tabIndex = one ? 0 : -1;
    const title = this.state?.title || 'What it made';
    artButton.title = title;
    artButton.append(litCell(this.state?.pill?.cell ?? 'idle', { size: 12, decorative: true }), node('span', '', title));
    artButton.addEventListener('click', () => this.setOne('art'));
    art.append(artButton);
    this.tabList.replaceChildren(conv, art);
  }

  private roveTabs(event: KeyboardEvent): void {
    if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return;
    const buttons = [...this.tabList.querySelectorAll<HTMLButtonElement>('.ctab-btn')].filter((b) => b.offsetParent !== null || !b.closest?.('.ctab-art'));
    const at = buttons.indexOf(document.activeElement as HTMLButtonElement);
    if (at < 0) return;
    event.preventDefault();
    const next = buttons[(at + (event.key === 'ArrowRight' ? 1 : -1) + buttons.length) % buttons.length];
    next?.focus?.(); next?.click();
  }

  /** New sections rise in once while it writes (same researcher and version), never on first sight. */
  private markNew(): void {
    const key = this.state?.key ?? '';
    const now = new Set([...this.content.querySelectorAll<HTMLElement>('[data-anchor]')].map((n) => n.dataset.anchor ?? ''));
    if (this.anchorsKey === key && this.state?.working) {
      for (const n of this.content.querySelectorAll<HTMLElement>('[data-anchor]')) if (!this.anchors.has(n.dataset.anchor ?? '')) this.glow(n);
    }
    this.anchors = now; this.anchorsKey = key;
  }

  private fitMenu(): void {
    const value = this.desk.dataset.fit ?? 'width';
    popMenu(this.fitButton, FITS.map(([fit, label]) => ({ label, checked: fit === value, act: () => this.setFit(fit) })), 'Fit');
  }

  private setFit(value: string): void {
    this.desk.dataset.fit = value;
    this.fitButton.replaceChildren(node('span', '', FITS.find(([v]) => v === value)?.[1] ?? 'Fit width'), researchIcon('down'));
  }

  private outlineMenu(): void {
    const outline = outlineOf(this.content);
    popMenu(this.pageCount, outline.length ? outline.map(({ anchor, title }) => ({ label: title, act: () => this.reveal(anchor) })) : [{ heading: 'No sections yet' }],
      'Outline', { above: true, start: true });
  }

  private syncPageBar(): void {
    if (!this.fitButton.children.length) this.setFit(this.desk.dataset.fit ?? 'width');
    // Sections shows only when the page has sections to go to.
    this.pageBar.hidden = this.paper.hidden || !outlineOf(this.content).length;
  }

  private attachItems(): MenuItem[] {
    const files = this.fileRows.filter(askable);
    const outline = outlineOf(this.content).filter(({ anchor }) => !PAGE_ONLY_SECTIONS.has(anchor));
    if (!files.length && !outline.length) return [{ heading: 'Nothing made yet' }];
    return [{ heading: 'Ask about a file it made' }, ...files.map((file): MenuItem => ({ label: file.name, act: () => this.handlers?.attachFile(file) })),
      ...(outline.length ? ['separator' as const, { heading: 'Or a section of it' }, ...outline.map(({ anchor, title }): MenuItem => ({ label: title, act: () => this.handlers?.attachSection(anchor, title) }))] : [])];
  }

  private engineMenu(chip: HTMLButtonElement): void {
    const who = this.state?.researcher ?? 'This researcher', engine = this.state?.engine ?? '';
    popMenu(chip, [{ heading: `${who} runs on ${engine}` }, { heading: 'Fixed for this run' }, 'separator',
      { label: 'Ask a new researcher', act: () => this.handlers?.newResearcher(), title: 'A new researcher Tab; its engine is chosen when it starts' }],
    `${who}’s engine`, { above: true, start: true });
  }

  private bindDivider(): void {
    this.divider.addEventListener('dblclick', () => this.setLayout({ conv: null, conv2: null }));
    this.divider.addEventListener('pointerdown', (event) => {
      const e = event as PointerEvent;
      if (this.columns() === 1) return;
      e.preventDefault();
      const rect = this.page.getBoundingClientRect();
      const wide = rect.width >= 1240, min = wide ? 400 : 360, max = rect.width - (wide ? 480 : 320);
      let last: number | null = null;
      this.divider.setPointerCapture?.(e.pointerId); this.divider.classList.add('is-drag');
      const move = (ev: Event) => {
        last = Math.round(Math.max(min, Math.min(max, (ev as PointerEvent).clientX - rect.left)));
        this.page.style.setProperty(wide ? '--conv-w' : '--conv-w2', `${last}px`);
      };
      const up = () => {
        this.divider.classList.remove('is-drag'); this.divider.removeEventListener('pointermove', move);
        if (last !== null) this.setLayout(wide ? { conv: last } : { conv2: last });
      };
      this.divider.addEventListener('pointermove', move);
      this.divider.addEventListener('pointerup', up, { once: true });
      this.divider.addEventListener('pointercancel', up, { once: true });
    });
  }

  private syncCards(): void {
    for (const chip of this.thread.querySelectorAll<HTMLButtonElement>('[data-show]')) {
      chip.setAttribute('aria-pressed', String(this.visible && chip.dataset.show === this.state?.output));
    }
  }

  /** What the reader shows, kept for this researcher while it is not its page (shown again on return). */
  private rememberDoc(): void {
    const reading = this.reading(), shown = this.reader.current(), page = this.pageView();
    if (!reading || !page) return;
    reading.doc = shown && !page.owns(shown.id) ? { id: shown.id, day: shown.day, view: this.reader.currentView() } : null;
    this.remember();
  }

  private remember(): void {
    if (!this.state) return;
    const previous = this.states.get(this.state.key);
    const reading: ReadingState = { ...previous, draft: this.compose.field.value,
      scroll: this.thread.scrollTop, artifactScroll: this.desk.scrollTop,
      atBottom: !(this.thread.scrollHeight - this.thread.clientHeight - this.thread.scrollTop >= 48),
      focused: document.activeElement === this.compose.field,
      selection: [this.compose.field.selectionStart, this.compose.field.selectionEnd] as [number, number] };
    this.states.set(this.state.key, reading);
    try { sessionStorage.setItem(`research-reading:${this.state.key}`, JSON.stringify(reading)); }
    catch { /* Private windows can still keep state until the page closes. */ }
  }
}
