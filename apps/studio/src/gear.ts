// The gear (docs/design/2026-10-12-workspace-and-assets.md): everything an expert sets, one step away and never on screen
// until asked for. Its menu holds the Models and Behaviour pickers (models.ts, which open under the gear), the Models,
// Capabilities and Datasets pages, the blind test, sharing this tab, and, for developers, test footage. A row that opens
// something else closes the menu; the blind test's switch keeps it open, so its new state is seen.
import type { Panel } from './panels';

export class GearMenu implements Panel {
  readonly id = 'gear';
  readonly whileTyping = true;
  readonly escapePriority = 100; // a menu: above the windows
  readonly toggle: HTMLButtonElement;
  readonly panel: HTMLElement;

  constructor(toggle: HTMLButtonElement, panel: HTMLElement) {
    this.toggle = toggle;
    this.panel = panel;
    toggle.addEventListener('click', () => this.setOpen(this.panel.hidden === true));
    // A row that goes somewhere (a page, a picker, test footage) closes the menu once it has done so.
    panel.addEventListener('click', (event) => {
      const row = (event.target as Element | null)?.closest?.('a, button');
      if (row && panel.contains(row) && !row.matches('[role="switch"], .workspace-share button')) setTimeout(() => this.setOpen(false), 0);
    });
    document.addEventListener('click', (event) => {
      const target = event.target as Node;
      if (this.panel.hidden || !target.isConnected || this.panel.contains(target) || this.toggle.contains(target)) return;
      this.setOpen(false);
    });
  }

  isOpen(): boolean {
    return !this.panel.hidden;
  }

  setOpen(open: boolean): void {
    this.panel.hidden = !open;
    this.toggle.setAttribute('aria-expanded', String(open));
  }

  close(): void {
    if (this.panel.hidden) return;
    this.setOpen(false);
    this.toggle.focus?.({ preventScroll: true });
  }

  /** A menu over everything for a moment: nothing moves for it. */
  footprint(): null {
    return null;
  }
}
