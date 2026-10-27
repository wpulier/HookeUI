type Section = { name: string; content: HTMLElement };

/** Presentation only: section changes retain every control and its owning session. */
export class ScientificInspector {
  private readonly tabs: HTMLButtonElement[] = [];
  private readonly panels: HTMLElement[] = [];
  private readonly notices = new Map<string, HTMLButtonElement>();
  private selected = 0;
  private locked = false;
  private readonly lockNotice = document.createElement('p');
  private readonly changed: (name: string) => void;

  private readonly root: HTMLElement;

  constructor(root: HTMLElement, sections: Section[], changed: (name: string) => void) {
    this.root = root;
    this.changed = changed;
    const id = `scientific-inspector-${crypto.randomUUID()}`;
    const tabs = document.createElement('div');
    tabs.className = 'scientific-inspector-tabs';
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Scientific work');
    const notices = document.createElement('div');
    notices.className = 'scientific-inspector-notices';
    notices.setAttribute('aria-live', 'polite');
    sections.forEach(({ name, content }, index) => {
      const tab = document.createElement('button');
      tab.textContent = name; tab.id = `${id}-tab-${index}`;
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-controls', `${id}-panel-${index}`);
      const panel = document.createElement('section');
      panel.className = 'scientific-inspector-page'; panel.id = `${id}-panel-${index}`;
      panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', tab.id);
      panel.setAttribute('tabindex', '0'); panel.append(content);
      tab.addEventListener('click', () => this.select(index));
      tab.addEventListener('keydown', event => {
        let next: number;
        if (event.key === 'ArrowRight') next = (index + 1) % sections.length;
        else if (event.key === 'ArrowLeft') next = (index + sections.length - 1) % sections.length;
        else if (event.key === 'Home') next = 0;
        else if (event.key === 'End') next = sections.length - 1;
        else return;
        event.preventDefault(); this.select(next); this.tabs[next].focus();
      });
      const notice = document.createElement('button');
      notice.textContent = `Review ${name} changes`; notice.hidden = true;
      notice.addEventListener('click', () => { this.select(index); panel.focus(); });
      this.notices.set(name, notice); notices.append(notice);
      this.tabs.push(tab); this.panels.push(panel); tabs.append(tab);
    });
    this.lockNotice.textContent = 'Finish, cancel or check the current edit before changing sections.';
    this.lockNotice.hidden = true; notices.append(this.lockNotice);
    root.append(tabs, ...this.panels, notices);
    this.select(0);
  }

  show(name: string): void {
    const index=this.tabs.findIndex(tab=>tab.textContent===name);
    if(index>=0)this.select(index);
  }

  attention(name: string, needed: boolean): void {
    const notice = this.notices.get(name);
    if (notice) notice.hidden = !needed;
  }

  lock(value: boolean): void {
    this.locked = value; this.lockNotice.hidden = !value;
    this.tabs.forEach((tab, index) => { tab.disabled = value && index !== this.selected; });
    for (const notice of this.notices.values()) notice.disabled = value;
  }

  private select(index: number): void {
    if (this.locked && index !== this.selected) return;
    const changed = index !== this.selected;
    this.selected = index;
    this.tabs.forEach((tab, current) => {
      const selected = current === index;
      tab.setAttribute('aria-selected', String(selected)); tab.setAttribute('tabindex', selected ? '0' : '-1');
      this.panels[current].hidden = !selected;
    });
    if (changed) {
      // Each section starts with its entry controls. Keep scrolling local to the
      // dock; scrollIntoView would also move the scientific workspace.
      const scroller = this.root.closest<HTMLElement>('.capability-controls-body');
      if (scroller) scroller.scrollTop = 0;
      this.panels[index].scrollTop = 0;
    }
    this.changed(this.tabs[index].textContent!);
  }
}
