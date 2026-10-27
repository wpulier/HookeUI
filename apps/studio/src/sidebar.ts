// The side bar is the session (docs/design/2026-10-12-workspace-and-assets.md; Will's design of 2026-10-12): the live
// workspace's left edge, a labelled strip of four toggles under the session's name (Assets, Made, Researchers, Activity),
// each with its count. A click opens that section in one column beside the strip; a click on its toggle again closes it,
// a click on another switches to it. The open section is remembered per viewer. Sessions and All assets are not here:
// the session's title is the switcher (session-menu.ts) and All assets is a page of its own (the header's button).
//
// - Assets: this session's recordings and pictures (sources.ts draws them; CONTEXT.md "Source"). The live camera is no
//   row: it is the header's camera control.
// - Made: what this session made (madetoday.ts draws it: its thumbnails, filters and hidden windows).
// - Researchers: this session's, and those still working in another (researcher-cards.ts, compact).
// - Activity: what happened, newest first (chatlog.ts draws it; an answer's ⓘ opens it at its request).
//
// Below them, the Actions panel (actions-panel.ts; docs/design/2026-10-13-actions-panel.md): everything the agent can
// run on the view, by the user's own hand. As in Premiere's lower panel it takes the side bar's whole width, under the
// strip too (Will's ask of 2026-10-13); the strip and the open section sit above it. Its header bar is the divider
// (Will, 2026-10-15): dragged (or moved with the arrow keys) to share the height with the open section, remembered per
// viewer; dragged all the way down, or clicked, Actions closes to that bar alone at the foot, quiet and out of the way,
// and a click (or a drag up) opens it again. With no section open it takes the column; with both closed the side bar
// is the slim rail again, Actions a toggle at its foot. The section's own head (the session's name, the section's, ‹)
// is the phone's only: on the desktop the header bar names the session and the lit toggle the section. On a phone's
// sheet Actions is a fifth tab.
//
// The whole side bar hides and comes back as the chat does (Will, 2026-10-15): the panel icon at the end of the tab row
// hides it to the slim rail, remembering what was open; the same icon at the top of the rail brings exactly that back. A
// first visit opens it on this session's Assets with Actions below, taking 30% of the height.
//
// This module only hosts them. It never covers the live view: the panel stack (panels.ts) asks it for its room each
// frame. Closed, its strip is an edge the margin notes, new windows and the stage's corners keep right of (the eyepiece
// stays where it is); open, the eyepiece glides right of it as it does beside the side column. On a phone (narrower
// than PHONE_WIDTH) the strip is a button in the conversation's toolbar and the side bar a sheet whose four sections are
// tabs. The screen report lists it as kind `sidebar` (its section in `shows`).
import { PHONE_WIDTH, type Panel, type Rect, type Reserved } from './panels';
import type { ScreenItem } from './screen';
import { sessionName, type SessionsModel } from './sessions';

export type SideSection = 'sources' | 'made' | 'researchers' | 'activity';

/** The four sections, in the strip's order: their names (the screen report's `title`), the word under each icon
 * (`short`: the strip is a labelled rail, so it is seen and reads as something to press) and icons (index.html's
 * sprite). `sources` is said "Assets": this session's (CONTEXT.md "Source": to the user, the session's assets). */
export const SIDE_SECTIONS: readonly { id: SideSection; label: string; short: string; icon: string; hint: string }[] = [
  // What you look at against what the app worked out (Will, 2026-10-15: "what is Made for vs Assets").
  { id: 'sources', label: 'Assets', short: 'Assets', icon: 'sources', hint: "Assets: what this project looks at: your pictures, recordings and snapshots" },
  { id: 'made', label: 'Made in this project', short: 'Made', icon: 'made', hint: 'Made: what the app worked out here: outlines, measurements, motion and research' },
  // Said "AI researchers" (the background helpers the agent sends off, "Researcher 1"...), never just "Researchers": the
  // people using the app are researchers (docs/design/2026-10-13-researcher-ready.md).
  { id: 'researchers', label: 'AI researchers', short: 'AI research', icon: 'researchers', hint: "AI researchers: the background work the agent sends off for this project, and any still working in another" },
  { id: 'activity', label: 'Activity', short: 'Activity', icon: 'activity', hint: 'Activity: what the app did in this project, newest first' },
];

export function isSection(value: unknown): value is SideSection {
  return SIDE_SECTIONS.some((s) => s.id === value);
}

export function sectionLabel(section: SideSection): string {
  return SIDE_SECTIONS.find((s) => s.id === section)!.label;
}

/** A toggle's accessible name: its section, its count, and what the count's colour says (as the tray's tabs). */
export function toggleLabel(section: SideSection, count: number, tone: CountTone = ''): string {
  const said = tone === 'asking' ? ', a researcher is asking you something' : tone === 'fresh' ? ', new work ready' : tone === 'working' ? ', working' : '';
  return `${sectionLabel(section)}${count ? `, ${count}` : ''}${said}`;
}

export type CountTone = '' | 'working' | 'fresh' | 'asking';

const STORAGE_KEY = 'studio-sessions-rail'; // the rail's key: its section now
const ACTIONS_KEY = 'studio-actions'; // Actions: "open" or "closed" (never set: open, the view's tools are in it)
const SPLIT_KEY = 'studio-actions-split'; // the share of the column's height the section above takes with both open
export const SPLIT_MIN = 0.2;
export const SPLIT_MAX = 0.8;
/** Dragged further down than this share of the column, Actions closes to its bar. */
export const SPLIT_CLOSE = 0.9;
const GAP = 8; // px kept between the open side bar and the eyepiece's area
const LAST_KEY = 'studio-sidebar-last'; // what was open when it was hidden ("<section or empty>|open|closed")
/** A first visit: the section takes this share, Actions the rest (30%). */
export const SPLIT_DEFAULT = 0.7;

/** The section this viewer left open, or null when it is closed (or storage is refused). Never set: this session's
 * Assets (a first visit opens on it). */
export function storedSection(): SideSection | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === null) return 'sources';
    return isSection(value) ? value : null; // the rail's old values ("1", Sessions, Assets) are no section now: closed
  } catch {
    return null; // optional: closed by default
  }
}

function storeSection(section: SideSection | null): void {
  try { localStorage.setItem(STORAGE_KEY, section ?? ''); } catch { /* browser storage is optional */ }
}

/** Whether this viewer left Actions open: open unless they closed it (the view's tools live there). */
export function storedActions(): boolean {
  try {
    return localStorage.getItem(ACTIONS_KEY) !== 'closed';
  } catch {
    return true;
  }
}

/** The share of the height the section above takes with both open, within its limits (SPLIT_DEFAULT by default). */
export function storedSplit(): number {
  try {
    const value = Number(localStorage.getItem(SPLIT_KEY));
    return value ? clampSplit(value) : SPLIT_DEFAULT;
  } catch {
    return SPLIT_DEFAULT;
  }
}

/** What was open when the side bar was hidden: its section (null: none) and whether Actions was open. */
function storedLast(): { section: SideSection | null; actions: boolean } | null {
  try {
    const [section, actions] = (localStorage.getItem(LAST_KEY) ?? '').split('|');
    return actions ? { section: isSection(section) ? section : null, actions: actions === 'open' } : null;
  } catch {
    return null;
  }
}

export function clampSplit(value: number): number {
  return Math.min(SPLIT_MAX, Math.max(SPLIT_MIN, value));
}

function store(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* browser storage is optional */ }
}

export interface SideBarOptions {
  /** Narrower than a phone's: the strip is a toolbar button and the side bar a sheet. */
  phone?: () => boolean;
  /** Where the phone's button goes (the conversation's toolbar). */
  toolbar?: HTMLElement | null;
  /** Told when the user opens, closes or switches it themselves (the screen report: their change is theirs). */
  onUserChange?: (property: string) => void;
  /** Told when a section is shown (a column may draw what it put off while hidden). */
  onSection?: (section: SideSection) => void;
}

function svgIcon(name: string): Element {
  const svg = document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('viewBox', '0 0 16 16');
  const use = document.createElementNS?.('http://www.w3.org/2000/svg', 'use') ?? document.createElement('use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

export class SideBar implements Panel {
  readonly id = 'sidebar';
  readonly whileTyping = false;
  private readonly model: SessionsModel;
  private readonly root: HTMLElement;
  private readonly phone: () => boolean;
  private readonly onUserChange: (property: string) => void;
  private readonly onSection: (section: SideSection) => void;
  /** The strip's toggles, one per section. */
  readonly toggles = new Map<SideSection, HTMLButtonElement>();
  readonly strip = document.createElement('nav');
  readonly closeButton = document.createElement('button');
  readonly phoneButton = document.createElement('button');
  /** Hides the side bar to the slim rail (the panel icon at the tab row's end) and brings it back (at the rail's top). */
  readonly hideButton = document.createElement('button');
  /** The Actions toggle: the slim rail's foot (both closed), or a phone's sheet's fifth tab. Open, its bar is the toggle. */
  readonly actionsToggle = document.createElement('button');
  private readonly panel = document.createElement('div');
  /** The section above (its head and the four sections, in the column beside the strip); the divider and the Actions
   * half below span the side bar's whole width. */
  private readonly upper = document.createElement('div');
  readonly divider = document.createElement('div');
  private readonly actionsBox = document.createElement('section');
  private readonly actionsRoot = document.createElement('div');
  private actionsOpen: boolean;
  private split: number;
  /** The session's name, over the section's: the side bar is this session's. */
  private readonly session = document.createElement('p');
  private readonly title = document.createElement('h2');
  /** Each section's container; their content roots are filled by their own views. */
  private readonly containers = new Map<SideSection, HTMLElement>();
  private readonly roots = new Map<SideSection, HTMLElement>();
  private opened: boolean;
  private current: SideSection;
  private readonly counts = new Map<SideSection, { count: number; tone: CountTone }>();

  constructor(model: SessionsModel, root: HTMLElement, options: SideBarOptions = {}) {
    this.model = model;
    this.root = root;
    this.phone = options.phone ?? (() => typeof innerWidth === 'number' && innerWidth < PHONE_WIDTH);
    this.onUserChange = options.onUserChange ?? (() => {});
    this.onSection = options.onSection ?? (() => {});
    const stored = storedSection();
    this.current = stored ?? 'sources';
    // A first visit opens on this session's Assets (storedSection); a phone's sheet never opens by itself.
    this.opened = stored !== null && !this.phone();
    this.actionsOpen = storedActions() && !this.phone(); // a phone's sheet never opens by itself
    this.split = storedSplit();
    root.classList?.add('sidebar');
    root.setAttribute('aria-label', 'This project: its assets, what it made, its researchers and activity');

    // The strip: four toggles stacked (on a phone's sheet, its tabs).
    this.strip.className = 'sidebar-strip';
    this.strip.setAttribute('aria-label', 'This project');
    for (const section of SIDE_SECTIONS) {
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.id = `sidebar-toggle-${section.id}`;
      toggle.className = 'sidebar-toggle';
      toggle.dataset.section = section.id;
      toggle.setAttribute('aria-controls', 'sidebar-panel');
      const words = document.createElement('span');
      words.className = 'sidebar-toggle-label';
      words.textContent = section.short;
      words.setAttribute('aria-hidden', 'true'); // its accessible name is the button's (toggleLabel), said in full
      const count = document.createElement('span');
      count.className = 'sidebar-count';
      count.hidden = true;
      toggle.append(svgIcon(section.icon), words, count);
      toggle.addEventListener('click', () => this.toggle(section.id, true));
      this.toggles.set(section.id, toggle);
      this.strip.append(toggle);
    }
    // The Actions toggle (the slim rail's foot, a phone's sheet's tab).
    this.actionsToggle.type = 'button';
    this.actionsToggle.id = 'sidebar-toggle-actions';
    this.actionsToggle.className = 'sidebar-toggle sidebar-toggle-actions';
    this.actionsToggle.dataset.section = 'actions';
    this.actionsToggle.setAttribute('aria-controls', 'sidebar-actions');
    const actionWords = document.createElement('span');
    actionWords.className = 'sidebar-toggle-label';
    actionWords.textContent = 'Actions';
    actionWords.setAttribute('aria-hidden', 'true');
    this.actionsToggle.append(svgIcon('actions'), actionWords);
    this.actionsToggle.addEventListener('click', () => this.toggleActions(true));
    this.strip.append(this.actionsToggle);
    this.hideButton.type = 'button';
    this.hideButton.id = 'sidebar-hide';
    this.hideButton.className = 'sidebar-hide';
    // The panel icon (a window with its left side marked), the same as the chat's on the right: hide and show alike.
    this.hideButton.innerHTML = '<svg viewBox="0 0 16 16" aria-hidden="true"><rect x="1.75" y="2.75" width="12.5" height="10.5" rx="2" /><path d="M6.25 2.75v10.5" /></svg>';
    this.hideButton.addEventListener('click', () => (this.shown() ? this.hide(true) : this.unhide(true)));
    this.strip.prepend(this.hideButton);
    this.strip.addEventListener('keydown', (event) => this.stripArrows(event));

    // The open column: the session's name, its section's name and close, then the section.
    this.panel.className = 'sidebar-panel';
    this.panel.id = 'sidebar-panel';
    this.panel.setAttribute('role', 'region');
    const head = document.createElement('div');
    head.className = 'sidebar-head';
    const names = document.createElement('div');
    names.className = 'sidebar-names';
    this.session.className = 'sidebar-session';
    this.title.className = 'sidebar-title';
    names.append(this.session, this.title);
    this.closeButton.type = 'button';
    this.closeButton.id = 'sidebar-close';
    this.closeButton.className = 'sidebar-close';
    this.closeButton.textContent = '‹';
    this.closeButton.addEventListener('click', () => {
      this.setOpen(false, true);
      this.focusToggle();
    });
    head.append(names, this.closeButton);
    const sections: HTMLElement[] = [];
    for (const section of SIDE_SECTIONS) {
      const box = document.createElement('section');
      box.className = 'sidebar-section';
      box.dataset.section = section.id;
      const content = document.createElement('div');
      content.id = `sidebar-${section.id}`;
      // The Researchers' cards are compact here (researcher-cards.ts draws them into this root).
      content.className = section.id === 'researchers' ? 'sidebar-content researcher-cards researcher-cards-compact' : `sidebar-content sidebar-${section.id}`;
      content.setAttribute('aria-label', section.id === 'researchers' ? "This project's researchers" : section.label);
      box.append(content);
      this.containers.set(section.id, box);
      this.roots.set(section.id, content);
      sections.push(box);
    }
    this.upper.className = 'sidebar-upper';
    this.upper.append(head, ...sections);
    // Actions' header bar is the divider: a click opens or closes Actions; dragged, or moved with the arrow keys, it
    // shares the height with the section above; dragged all the way down it closes Actions to the bar.
    this.divider.className = 'sidebar-divider';
    const grip = document.createElement('span');
    grip.className = 'sidebar-divider-grip';
    grip.setAttribute('aria-hidden', 'true');
    const dividerTitle = document.createElement('h2');
    dividerTitle.className = 'sidebar-title';
    dividerTitle.textContent = 'Actions';
    this.divider.append(dividerTitle, grip);
    this.divider.tabIndex = 0;
    this.divider.setAttribute('role', 'separator');
    this.divider.setAttribute('aria-orientation', 'horizontal');
    this.divider.setAttribute('aria-label', 'Actions: click to open or close, drag to share the height');
    this.divider.setAttribute('aria-valuemin', String(SPLIT_MIN * 100));
    this.divider.setAttribute('aria-valuemax', String(SPLIT_MAX * 100));
    this.divider.addEventListener('pointerdown', (event) => this.dragDivider(event));
    this.divider.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.setActions(!this.actionsOpen, true); return; }
      const step = event.key === 'ArrowUp' ? -0.05 : event.key === 'ArrowDown' ? 0.05 : 0;
      if (!step) return;
      event.preventDefault();
      if (!this.actionsOpen) { if (step < 0) this.setActions(true, true); return; }
      if (!this.opened) { if (step > 0) this.setActions(false, true); return; }
      if (step > 0 && this.split >= SPLIT_MAX) { this.setActions(false, true); return; }
      this.setSplit(this.split + step);
    });
    // The Actions half: the panel (actions-panel.ts fills `actions()`), named by the divider above it.
    this.actionsBox.className = 'sidebar-actions-box';
    this.actionsBox.setAttribute('aria-label', 'Actions');
    this.actionsRoot.id = 'sidebar-actions';
    this.actionsRoot.className = 'sidebar-content sidebar-actions';
    this.actionsBox.append(this.actionsRoot);
    this.panel.append(this.upper);
    root.replaceChildren(this.strip, this.panel, this.divider, this.actionsBox);
    root.addEventListener('keydown', (event) => {
      // Escape inside the side bar closes it (a field's own Escape, such as a rename, is its own: it cancels).
      if (event.key !== 'Escape' || event.defaultPrevented || !this.opened) return;
      event.preventDefault();
      this.setOpen(false, true);
      this.focusToggle();
    });

    // On a phone the strip is a button in the conversation's toolbar.
    this.phoneButton.type = 'button';
    this.phoneButton.id = 'session-rail-phone';
    this.phoneButton.className = 'session-rail-phone';
    this.phoneButton.textContent = 'This project…';
    this.phoneButton.title = "This project's assets, what it made, its researchers and activity";
    this.phoneButton.setAttribute('aria-controls', 'session-rail');
    this.phoneButton.addEventListener('click', () => this.setOpen(!this.opened, true));
    const toolbar = options.toolbar;
    if (toolbar) {
      const before = toolbar.querySelector('#activity-toggle');
      if (before) toolbar.insertBefore(this.phoneButton, before);
      else toolbar.append(this.phoneButton);
    }

    root.dataset.sheet = String(this.phone());
    model.onChange(() => this.render());
    this.render();
  }

  /** The section shown when open (or that opens next). */
  section(): SideSection {
    return this.current;
  }

  /** A section's content root, for the view that fills it. */
  content(section: SideSection): HTMLElement {
    return this.roots.get(section)!;
  }

  /** The Actions half's content root, for actions-panel.ts. */
  actions(): HTMLElement {
    return this.actionsRoot;
  }

  /** Open, as the panel stack means it (Escape closes it): a section is open above Actions (on a phone's sheet, the
   * sheet is up). On the desktop Actions alone is not something Escape closes: it is always there. */
  isOpen(): boolean {
    return (this.opened || (this.sheet() && this.actionsOpen)) && !this.root.hidden;
  }

  /** Its column shows: the section above, Actions, or both (on the desktop, always). */
  shown(): boolean {
    return (this.opened || this.actionsOpen) && !this.root.hidden;
  }

  /** Hides it to the slim rail, remembering what was open (the chat's hide, on the left). */
  hide(byUser = false): void {
    if (!this.shown()) return;
    store(LAST_KEY, `${this.opened ? this.current : ''}|${this.actionsOpen ? 'open' : 'closed'}`);
    this.opened = false;
    this.actionsOpen = false;
    this.remember();
    if (byUser) this.onUserChange('open');
    this.render();
  }

  /** Brings back what was open when it was hidden (never anything: this session's Assets with Actions below). */
  unhide(byUser = false): void {
    if (this.shown()) return;
    const last = storedLast();
    const section = last ? last.section : this.current;
    this.actionsOpen = last ? last.actions : true;
    if (section) this.current = section;
    this.opened = !!section || !this.actionsOpen; // something always comes back
    this.remember();
    if (byUser) this.onUserChange('open');
    this.render();
    if (this.opened) this.onSection(this.current);
  }

  /** Whether `section` is what shows now (open, and not behind the Actions tab on a phone's sheet). */
  showing(section: SideSection): boolean {
    return this.opened && this.current === section && !this.root.hidden;
  }

  /** Whether the Actions half is open. */
  actionsShown(): boolean {
    return this.actionsOpen && !this.root.hidden;
  }

  /** Closes the section above (on a phone's sheet, the sheet); on the desktop Actions is its bar's to open or close. */
  close(): void {
    const sheet = this.sheet();
    const changed = this.opened || (sheet && this.actionsOpen);
    this.opened = false;
    if (sheet) this.actionsOpen = false;
    if (changed) { this.remember(); this.render(); }
  }

  /** Open or close Actions; remembered per viewer. On a phone's sheet it is a tab: opening it shows it alone. */
  setActions(open: boolean, byUser = false): void {
    if (open === this.actionsOpen && !(open && this.sheet() && this.opened)) return;
    this.actionsOpen = open;
    if (open && this.sheet()) this.opened = false;
    this.remember();
    if (byUser) this.onUserChange('open');
    this.render();
    // Closed from its bar with nothing else open: the bar is gone with the column, so the focus goes to the rail's toggle.
    if (!open && !this.shown() && !this.sheet() && this.root.contains?.(document.activeElement)) this.actionsToggle.focus?.({ preventScroll: true });
  }

  /** The Actions toggle: on the slim rail it opens Actions; on a phone's sheet it shows it alone and stays, as a tab. */
  toggleActions(byUser = false): void {
    this.setActions(true, byUser);
  }

  /** The share of the height the section above takes with both open (clamped), remembered per viewer. */
  setSplit(value: number): void {
    this.split = clampSplit(value);
    store(SPLIT_KEY, String(this.split));
    this.render();
  }

  splitShare(): number {
    return this.split;
  }

  private sheet(): boolean {
    return this.root.dataset.sheet === 'true';
  }

  /** Open or close it, on the section it has; remembered per viewer (a phone's sheet is not). `byUser`: their own
   * change, said in the screen report. */
  setOpen(open: boolean, byUser = false): void {
    if (open === this.opened) return;
    this.opened = open;
    this.remember();
    if (byUser) this.onUserChange('open');
    this.render();
    if (open) this.onSection(this.current);
  }

  /** Open it on `section` (switching to it when another is open). */
  open(section: SideSection, byUser = false): void {
    if (this.sheet() && this.actionsOpen) { this.toggle(section, byUser); return; } // one tab at a time: the section's
    if (this.opened && this.current === section) return;
    const switching = this.opened;
    this.current = section;
    if (!switching) { this.setOpen(true, byUser); return; }
    this.remember();
    if (byUser) this.onUserChange('shows');
    this.render();
    this.onSection(section);
  }

  /** A toggle's click: its section opens; open already, it closes (a phone's sheet keeps it: its toggles are tabs). */
  toggle(section: SideSection, byUser = false): void {
    if (this.sheet() && this.actionsOpen) { // a phone's sheet shows one tab: the section's, not Actions
      this.actionsOpen = false;
      this.current = section;
      this.opened = true;
      this.remember();
      if (byUser) this.onUserChange('shows');
      this.render();
      this.onSection(section);
      return;
    }
    if (this.opened && this.current === section) {
      if (this.root.dataset.sheet !== 'true') this.setOpen(false, byUser);
      return;
    }
    this.open(section, byUser);
  }

  /** Lights a section's toggle for a moment: something just joined it (a snapshot, in Assets). */
  flash(section: SideSection): void {
    const toggle = this.toggles.get(section);
    if (!toggle) return;
    delete toggle.dataset.flash;
    void toggle.offsetWidth; // a second flash while one shows starts again
    toggle.dataset.flash = 'true';
    setTimeout(() => { delete toggle.dataset.flash; }, 1800);
  }

  /** A section's count on its toggle (0 hides it), coloured when it needs you. */
  count(section: SideSection, count: number, tone: CountTone = ''): void {
    const toggle = this.toggles.get(section);
    const badge = toggle?.querySelector<HTMLElement>('.sidebar-count');
    if (!toggle || !badge) return;
    badge.textContent = String(count);
    badge.hidden = count === 0;
    badge.dataset.state = tone;
    this.counts.set(section, { count, tone });
    toggle.setAttribute('aria-label', toggleLabel(section, count, tone));
  }

  /** Where it is while open (client px): new windows keep clear of it. */
  footprint(): DOMRect | null {
    return this.shown() ? this.root.getBoundingClientRect() : null;
  }

  /** The room it takes from the view: closed, its strip is an edge things keep right of; open, the eyepiece glides
   * right of it too. A phone's sheet takes none (it is a sheet over the page, not beside the view). */
  reserves(stage: Rect): Partial<Reserved> {
    if (this.root.hidden || this.phone() || stage.right <= stage.left) return {};
    const rect = this.root.getBoundingClientRect();
    const width = Math.max(0, rect.right - stage.left);
    if (!width) return {};
    return this.shown() ? { left: width + GAP, edge: width + GAP } : { edge: width };
  }

  /** Each frame: a window that narrows to a phone's width closes it (a sheet never opens by itself); widened again,
   * it is as this viewer left it on the desktop. */
  frame(): void {
    const sheet = this.phone();
    if (this.root.dataset.sheet === String(sheet)) return;
    this.root.dataset.sheet = String(sheet);
    const stored = sheet ? null : storedSection();
    if (stored) this.current = stored;
    this.opened = stored !== null;
    this.actionsOpen = !sheet && storedActions();
    this.render();
  }

  /** What the screen report says of it: kind `sidebar`, open or not, where, and the section it shows (`title` its
   * name). */
  screenItem(): ScreenItem {
    const open = this.shown();
    const rect = open ? this.root.getBoundingClientRect() : null;
    const shows = this.opened ? this.current : 'actions'; // with only Actions open, it shows Actions
    return { id: 'sidebar', kind: 'sidebar', title: this.opened ? sectionLabel(this.current) : 'Actions', open, visible: open,
      rect: rect && rect.right > rect.left && rect.bottom > rect.top
        ? [Math.round(rect.left), Math.round(rect.top), Math.round(rect.right - rect.left), Math.round(rect.bottom - rect.top)] : null,
      shows };
  }

  /** A change the server sends (`present` open, close or hide on kind `sidebar`): true when it was one of these. */
  applyPresent(set: Record<string, unknown>): boolean {
    if (set.open === true || set.visible === true) {
      if (set.shows === 'actions') this.setActions(true);
      else this.open(isSection(set.shows) ? set.shows : this.current);
      return true;
    }
    // Closed or hidden by the agent: on the desktop the whole side bar hides to its rail (as its hide does), so the screen
    // report says what was asked; a phone's sheet closes.
    if (set.open === false || set.visible === false) { if (this.sheet()) this.close(); else this.hide(); return true; }
    return false;
  }

  render(): void {
    const open = this.opened;
    const section = this.current;
    this.root.dataset.open = String(open || this.actionsOpen); // the column shows: the section above, Actions, or both
    this.root.dataset.sectionOpen = String(open);
    this.root.dataset.section = section;
    for (const [id, toggle] of this.toggles) {
      const active = open && id === section;
      toggle.setAttribute('aria-expanded', String(active));
      const counted = this.counts.get(id);
      toggle.setAttribute('aria-label', toggleLabel(id, counted?.count ?? 0, counted?.tone ?? ''));
      const hint = SIDE_SECTIONS.find((s) => s.id === id)!.hint;
      toggle.title = active && this.root.dataset.sheet !== 'true' ? `Close ${sectionLabel(id)} (Esc)` : hint;
    }
    this.phoneButton.setAttribute('aria-expanded', String(this.shown()));
    const shown = this.shown();
    this.hideButton.hidden = this.sheet();
    this.hideButton.dataset.shows = String(shown);
    this.hideButton.setAttribute('aria-label', shown ? 'Hide the side bar' : 'Show the side bar');
    this.hideButton.title = shown ? 'Hide the side bar (more room for the view)' : 'Show the side bar';
    const actions = this.actionsOpen;
    this.root.dataset.actions = String(actions);
    const sheet = this.sheet();
    // The toggle: a phone's tab, or the slim rail's foot while both are closed (open, Actions' bar is its toggle).
    this.actionsToggle.hidden = !sheet && (open || actions);
    this.actionsToggle.setAttribute('aria-expanded', String(actions));
    this.actionsToggle.title = 'Actions: run any of its tools yourself';
    this.actionsBox.hidden = !actions;
    this.panel.hidden = !open;
    // Actions' bar: on the desktop whenever the column shows; closed, it is the quiet bar at the foot.
    this.divider.hidden = sheet || !(open || actions);
    this.divider.dataset.collapsed = String(!actions);
    this.divider.dataset.drags = String(open && actions);
    this.divider.setAttribute('aria-valuenow', String(Math.round(this.split * 100)));
    this.divider.title = actions ? (open ? 'Drag to share the height; drag down or click to close Actions' : 'Click to close Actions')
      : 'Click or drag up to open Actions';
    // The toggles on top, then the open section and Actions sharing the rest; closed, Actions is its bar at the foot.
    this.root.style.gridTemplateRows = sheet || !(open || actions) ? ''
      : open && actions ? `auto minmax(0, ${this.split}fr) auto minmax(0, ${1 - this.split}fr)`
        : open ? 'auto minmax(0, 1fr) auto 0px' : 'auto 0px auto minmax(0, 1fr)';
    const row = this.model.currentRow();
    this.session.textContent = row ? sessionName(row, this.model.now()) : '';
    this.session.hidden = !row;
    this.title.textContent = sectionLabel(section);
    this.panel.setAttribute('aria-label', `${this.session.textContent ? `${this.session.textContent}: ` : ''}${sectionLabel(section)}`);
    for (const [id, box] of this.containers) box.hidden = !open || id !== section;
    this.closeButton.setAttribute('aria-label', `Close ${sectionLabel(section)}`);
    this.closeButton.title = `Close ${sectionLabel(section)} (Esc)`;
  }

  private remember(): void {
    if (this.root.dataset.sheet === 'true' || this.phone()) return;
    storeSection(this.opened ? this.current : null);
    store(ACTIONS_KEY, this.actionsOpen ? 'open' : 'closed');
  }

  /** Actions' bar under the pointer (captured until it is let go): a click opens or closes Actions; a drag shares the
   * column's height with the section above, opening Actions when dragged up from closed, and closing it when let go
   * past SPLIT_CLOSE (or, with no section above, dragged well down). */
  private dragDivider(event: PointerEvent): void {
    const box = this.root.getBoundingClientRect();
    if (box.height <= 0) return;
    event.preventDefault();
    this.divider.setPointerCapture?.(event.pointerId);
    const startY = event.clientY;
    const before = this.split; // closing by a drag keeps the height Actions had, for when it opens again
    // The bar moves as far as the pointer, over the height the section and Actions share (the column less the tab row and
    // the bar itself); closed, the bar is at the foot (share 1).
    const shared = Math.max(1, box.height - this.strip.getBoundingClientRect().height - this.divider.getBoundingClientRect().height);
    const from = this.actionsOpen ? this.split : 1;
    let dragged = false;
    let share = from;
    const move = (e: PointerEvent) => {
      if (!dragged && Math.abs(e.clientY - startY) < 4) return;
      dragged = true;
      share = from + (e.clientY - startY) / shared;
      if (!this.opened) return; // nothing above to share with: let go, it closes or not
      if (!this.actionsOpen && share < SPLIT_CLOSE) this.setActions(true, true);
      if (this.actionsOpen) this.setSplit(share);
    };
    const end = () => {
      this.divider.removeEventListener('pointermove', move);
      this.divider.removeEventListener('pointerup', up);
      this.divider.removeEventListener('pointercancel', end); // a cancelled pointer (a system gesture) does nothing
    };
    const up = (e: PointerEvent) => {
      end();
      if (!dragged) this.setActions(!this.actionsOpen, true);
      else if (this.opened ? share >= SPLIT_CLOSE : e.clientY - startY > 40) { this.split = before; store(SPLIT_KEY, String(before)); this.setActions(false, true); }
    };
    this.divider.addEventListener('pointermove', move);
    this.divider.addEventListener('pointerup', up);
    this.divider.addEventListener('pointercancel', end);
  }

  /** Back to where it opens: the section's toggle, or on a phone its button. */
  private focusToggle(): void {
    (this.root.dataset.sheet === 'true' ? this.phoneButton : this.toggles.get(this.current))?.focus?.({ preventScroll: true });
  }

  /** Up and down move between the strip's toggles (Home and End to the ends; on a phone's sheet, left and right). */
  private stripArrows(event: KeyboardEvent): void {
    const keys = ['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'];
    if (!keys.includes(event.key)) return;
    const buttons = [...this.toggles.values(), this.actionsToggle].filter((b) => !b.hidden);
    const at = buttons.indexOf(event.target as HTMLButtonElement);
    if (at < 0) return;
    const step = event.key === 'ArrowDown' || event.key === 'ArrowRight' ? 1 : -1;
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1 : Math.min(buttons.length - 1, Math.max(0, at + step));
    event.preventDefault();
    buttons[next]?.focus?.({ preventScroll: true });
  }
}
