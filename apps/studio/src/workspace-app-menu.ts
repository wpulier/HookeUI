import './workspace-theme.css';
import './workspace-app-menu.css';
import { updatePathWords } from './app-updates';

type Theme = 'dark' | 'black' | 'light';
interface Session { id: string; title: string; current: boolean; open: boolean; /** When it was last used and what it holds, so rows that share a name differ. */ note?: string }
interface Options {
  opener: HTMLButtonElement;
  storageKey: string;
  sessions: () => Session[];
  newSession: () => void;
  scientificSessions?: () => void;
  /** Opens the Spending panel (the Stage's status pill owns it). */
  spending?: () => void;
  openSession: (id: string) => void;
  navigate: (hash: string) => void;
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, cls = '', text = '') => {
  const result = document.createElement(tag); result.className = cls; result.textContent = text; return result;
};

/** A single light-dismiss popover owns both columns, including the pointer corridor. */
export class WorkspaceAppMenu {
  readonly root = node('section', 'workspace-app-menu');
  private main = node('div', 'workspace-menu-main');
  private submenu = node('section', 'workspace-menu-submenu');
  private list = node('div', 'workspace-menu-sessions');
  private search = node('input');
  private branch: 'sessions' | 'theme' | null = null;
  private branchButton: HTMLButtonElement | null = null;
  private theme: Theme = 'dark';
  private themeButtons: HTMLButtonElement[] = [];
  private opts: Options;
  private sessionsItem: HTMLButtonElement | null = null;
  /** Who opened it: the Hooke button, or the header's Projects ▾ (focus goes back there on close). */
  private openedBy: HTMLButtonElement | null = null;
  constructor(opts: Options) {
    this.opts = opts;
    try { const saved = localStorage.getItem(`${opts.storageKey}:theme`); if (saved === 'dark' || saved === 'black' || saved === 'light') this.theme = saved; } catch {}
    this.applyTheme();
    this.root.id = 'workspace-app-menu'; this.root.setAttribute('popover', 'auto'); this.root.setAttribute('aria-label', 'HookeUI menu');
    this.root.setAttribute('role', 'dialog');
    this.main.setAttribute('role', 'menu'); this.main.setAttribute('aria-label', 'Application');
    this.submenu.hidden = true;
    const add = (label: string, action: () => void, suffix = '') => {
      const b = node('button', 'workspace-menu-item'); b.type = 'button'; b.setAttribute('role', 'menuitem');
      b.append(node('span', '', label), node('span', 'workspace-menu-trailing', suffix));
      b.addEventListener('click', action); this.main.append(b); return b;
    };
    const closeThen = (action: () => void) => { this.close(); action(); };
    add('New project', () => closeThen(opts.newSession), '+');
    add('Workflows', () => closeThen(() => opts.navigate('#workflows'))); this.main.append(node('hr'));
    const sessions = this.sessionsItem = add('All Projects', () => closeThen(() => opts.navigate('#sessions')), '›');
    const theme = add('Theme', () => this.openBranch('theme', theme, true), '›');
    for (const [button, branch] of [[sessions, 'sessions'], [theme, 'theme']] as const) {
      button.setAttribute('aria-haspopup', 'true'); button.setAttribute('aria-expanded', 'false');
      button.addEventListener('pointerenter', event => { if (event.pointerType !== 'touch') this.openBranch(branch, button); });
      button.addEventListener('keydown', event => { if (event.key === 'ArrowRight') { event.preventDefault(); this.openBranch(branch, button, true); } });
    }
    this.main.append(node('hr'));
    if (opts.spending) add('Spending', () => closeThen(opts.spending!));
    if (opts.scientificSessions) add('Scientific sessions', () => closeThen(opts.scientificSessions!)); // the sheet's own title
    add('Settings', () => closeThen(() => opts.navigate('#settings')));
    add('Help', () => closeThen(() => opts.navigate('#help')));
    this.main.append(node('hr'));
    const revision = document.documentElement.dataset.buildRevision ?? 'unknown';
    const build = node('div', 'workspace-menu-build');
    build.append(node('strong', '', 'Hooke'), node('span', '', `Build ${revision.slice(0, 8)}${document.documentElement.dataset.buildDirty === 'true' ? ' · local changes' : ''}`), node('span', '', updatePathWords()));
    this.main.append(build);
    this.search.type = 'search'; this.search.placeholder = 'Find a project…'; this.search.setAttribute('aria-label', 'Find a project');
    this.search.addEventListener('input', () => this.refresh());
    this.root.append(this.main, this.submenu); document.body.append(this.root);
    opts.opener.setAttribute('aria-controls', this.root.id); opts.opener.setAttribute('aria-haspopup', 'dialog'); opts.opener.setAttribute('aria-expanded', 'false');
    opts.opener.addEventListener('keydown', event => { if (event.key === 'ArrowDown') { event.preventDefault(); if (!this.root.matches(':popover-open')) this.toggle(); } });
    this.root.addEventListener('toggle', () => (this.openedBy ?? opts.opener).setAttribute('aria-expanded', String(this.root.matches(':popover-open'))));
    this.main.addEventListener('pointerover', event => {
      const b = (event.target as Element).closest('button');
      if (b && b !== sessions && b !== theme) this.hideBranch();
    });
    window.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !this.root.matches(':popover-open')) return;
      event.preventDefault(); event.stopImmediatePropagation();
      if (this.branch) this.hideBranch(true); else this.close();
    }, {capture:true});
    this.root.addEventListener('keydown', event => {
      if (event.key === 'ArrowLeft' && this.branch) { event.preventDefault(); this.hideBranch(true); return; }
      if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
      if (event.target === this.search && !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
      event.preventDefault();
      const host = this.submenu.contains(document.activeElement) ? this.submenu : this.main;
      const choices = [...host.querySelectorAll<HTMLButtonElement>('button')].filter(b => !b.hidden && getComputedStyle(b).display !== 'none');
      const current = choices.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? choices.length - 1 : current < 0 ? (event.key === 'ArrowUp' ? choices.length - 1 : 0) : (current + (event.key === 'ArrowDown' ? 1 : -1) + choices.length) % choices.length;
      choices[next]?.focus();
    });
  }
  toggle(): void {
    if (this.root.matches(':popover-open')) { this.close(); return; }
    this.openedBy = null;
    this.hideBranch();
    this.root.style.top = `${Math.max(8, Math.min(this.opts.opener.getBoundingClientRect().bottom + 4, innerHeight - 360))}px`;
    this.root.showPopover(); this.main.querySelector('button')?.focus();
  }
  /** Opens straight at Recent projects, under `from` (the header's Projects ▾), its search focused. All Projects stays
   * beside it for the full page. */
  showProjects(from: HTMLButtonElement): void {
    if (this.root.matches(':popover-open')) { this.close(); return; }
    this.openedBy = from;
    from.setAttribute('aria-controls', this.root.id); from.setAttribute('aria-haspopup', 'dialog');
    this.hideBranch();
    this.root.style.top = `${Math.max(8, Math.min(from.getBoundingClientRect().bottom + 4, innerHeight - 360))}px`;
    this.root.showPopover();
    if (this.sessionsItem) this.openBranch('sessions', this.sessionsItem, true);
  }
  private close(): void {
    const opener = this.openedBy ?? this.opts.opener;
    this.root.hidePopover(); this.hideBranch();
    // The popover's toggle event is queued; clear its actual opener before releasing that identity.
    opener.setAttribute('aria-expanded', 'false'); opener.focus(); this.openedBy = null;
  }
  private hideBranch(focus = false): void {
    const previous = this.branchButton;
    previous?.setAttribute('aria-expanded', 'false'); this.branchButton = null; this.branch = null;
    this.submenu.hidden = true; this.root.classList.remove('has-submenu'); if (focus) previous?.focus();
  }
  private openBranch(branch: 'sessions' | 'theme', opener: HTMLButtonElement, focus = false): void {
    this.branchButton?.setAttribute('aria-expanded', 'false');
    this.branch = branch; this.branchButton = opener; opener.setAttribute('aria-expanded', 'true');
    this.submenu.hidden = false; this.root.classList.add('has-submenu'); this.submenu.replaceChildren();
    this.root.style.setProperty('--submenu-offset', `${Math.max(0, opener.getBoundingClientRect().top - this.root.getBoundingClientRect().top)}px`);
    this.submenu.setAttribute('aria-label', branch === 'sessions' ? 'Recent projects' : 'Theme');
    const back = node('button', 'workspace-menu-back', '‹ Back'); back.type = 'button'; back.onclick = () => this.hideBranch(true);
    this.submenu.append(back, node('h3', '', branch === 'sessions' ? 'Recent projects' : 'Theme'));
    if (branch === 'sessions') { this.submenu.append(this.search, this.list); this.refresh(); }
    else {
      this.themeButtons = [];
      for (const [id, title] of [['dark', 'Dark'], ['black', 'Jet black'], ['light', 'Light']] as const) {
        const b = node('button', 'workspace-menu-item'); b.type = 'button'; b.setAttribute('role', 'menuitemradio'); b.dataset.themeChoice = id;
        b.append(node('span', `workspace-theme-swatch ${id}`), node('span', '', title), node('span', 'workspace-theme-check', '✓'));
        b.onclick = () => { this.theme = id; this.applyTheme(); try { localStorage.setItem(`${this.opts.storageKey}:theme`, id); } catch {} };
        this.themeButtons.push(b); this.submenu.append(b);
      }
      this.applyTheme();
    }
    if (focus) (branch === 'sessions' ? this.search : this.themeButtons[0]).focus();
  }
  refresh(): void {
    if (this.branch !== 'sessions') return;
    const focused = this.list.querySelector<HTMLButtonElement>('button:focus')?.dataset.session;
    const query = this.search.value.trim().toLowerCase(); this.list.replaceChildren();
    for (const s of this.opts.sessions().filter(s => s.title.toLowerCase().includes(query)).slice(0, 12)) {
      const b = node('button', 'workspace-menu-item'); b.type = 'button'; b.dataset.session = s.id;
      b.setAttribute('aria-label', `Open project ${s.title}`); b.setAttribute('aria-current', s.current ? 'page' : 'false');
      b.append(node('span', '', s.title), node('small', '', [s.current ? 'Current' : s.open ? 'Open' : '', s.note ?? ''].filter(Boolean).join(' · ')));
      b.onclick = () => { this.close(); this.opts.openSession(s.id); }; this.list.append(b);
      if (focused === s.id) b.focus();
    }
    if (!this.list.children.length) this.list.append(node('p', '', 'No matching projects.'));
  }
  private applyTheme(): void {
    document.documentElement.dataset.workspaceTheme = this.theme;
    for (const b of this.themeButtons) b.setAttribute('aria-checked', String(b.dataset.themeChoice === this.theme));
  }
}
