/** Show me's bar (services/orchestrator/showing.py): while a researcher's finding is being shown, the
 * dock says so with a Stop. Stop, or Escape (it is the top of the panel stack), ends the showing; the
 * server puts back only what the showing changed, and what the user changed meanwhile stays. */
import type { Panel, Rect } from './panels';
import type { ClientCommand, ShowingState } from './protocol';

/** Whether a command the user sends is their own change to the result viewer: work opened from the shelf,
 * the chat's Open or the Research tab (a showing never puts back over it). */
export function opensWorkForUser(command: ClientCommand): boolean {
  return command.type === 'presenter' && command.action === 'show';
}

export function showingText(state: ShowingState): string {
  return `${state.about[0].toUpperCase()}${state.about.slice(1)}. Stop puts back only what it changed.`; // the pill says "Showing"
}

export class ShowingBar implements Panel {
  readonly id = 'showing';
  readonly escapePriority = 50; // stop the showing before leaving a page; menus still close first
  private state: ShowingState | null = null;
  private readonly root: HTMLElement;
  private readonly text: HTMLElement;
  private readonly send: (command: ClientCommand) => void;

  constructor(root: HTMLElement, text: HTMLElement, stop: HTMLButtonElement, send: (command: ClientCommand) => void) {
    this.root = root;
    this.text = text;
    this.send = send;
    stop.addEventListener('click', () => this.close());
  }

  set(state: ShowingState): void {
    this.state = state.active ? state : null;
    this.root.hidden = this.state === null;
    if (this.state) this.text.textContent = showingText(this.state);
  }

  isOpen(): boolean {
    return this.state !== null;
  }

  close(): void {
    if (!this.state) return;
    this.send({ type: 'showing', action: 'stop' });
    this.state = null;
    this.root.hidden = true;
  }

  /** In the dock: it takes no room from the view. */
  footprint(): Rect | null {
    return null;
  }
}
