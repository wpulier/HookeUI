// A Project card in the chat (Projects by voice; services/orchestrator/brain/toolbox/projects.py ProjectCards): the
// tidy Hooke proposes for this Project, with Do it and Not now, then what it did with Undo; or, when a request to open
// a Project may mean several, two or three of them, each with Open, while the voice asks which. Every word on it is the
// server's (`project-card` messages); a press sends `project-card` (Do it, Not now, Undo) or the studio's own `session`
// switch (Open). It sits under the request that made it, as an experiment's card does (experiment-card.ts).
import { arrive } from './chat-cards';
import { litCell, setLitCell, type LitCellState } from './lit-cell';

/** A Project card as the server's wire says it (projects.py ProjectCards). */
export interface ProjectCardWire {
  id: string; kind: 'tidy' | 'choose'; session: string; thread: string | null; requestId: string | null;
  title: string; status: string; said: string; lines: string[];
  choices: { session: string; name: string; detail: string }[];
  actions: { id: string; label: string; primary?: boolean }[];
}

/** What a press asks for: one of the card's actions, or opening one of its choices. */
export type ProjectCardPress = { action: string } | { open: string };

/** The card's lit cell: waiting on the user while it asks, ready once done, still once declined or undone. */
export function projectCardCell(x: ProjectCardWire): LitCellState {
  if (x.status === 'proposed' || x.status === 'asked') return 'needs';
  if (x.status === 'done') return 'ready';
  if (x.status === 'failed') return 'attention';
  return 'stopped';
}

const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node;
};

/** One card, updated in place (its cell is never rebuilt, so its breathing never restarts). */
export class ProjectCard {
  readonly root: HTMLElement;
  readonly id: string;
  private readonly cell: SVGSVGElement;
  private readonly title: HTMLElement;
  private readonly status: HTMLElement;
  private readonly lines: HTMLUListElement;
  private readonly choices: HTMLOListElement;
  private readonly bar: HTMLElement;
  private state: LitCellState | null = null;
  private drawn = '';
  wire: ProjectCardWire | null = null;
  busy = false;
  private readonly press: (press: ProjectCardPress, x: ProjectCardWire) => Promise<void> | void;

  constructor(id: string, press: (press: ProjectCardPress, x: ProjectCardWire) => Promise<void> | void) {
    this.id = id; this.press = press;
    this.root = make('div', 'chat-xcard chat-pcard'); this.root.dataset.projectCard = id; this.root.setAttribute('role', 'group');
    this.cell = litCell('needs', { size: 13 });
    const words = make('div', 'chat-xcard-words');
    this.title = make('span', 'chat-xcard-title'); this.status = make('span', 'chat-xcard-status');
    this.status.setAttribute('aria-live', 'polite');
    this.lines = make('ul', 'chat-xcard-steps chat-pcard-lines');
    this.choices = make('ol', 'chat-pcard-choices');
    this.bar = make('div', 'chat-xcard-bar');
    words.append(this.title, this.status, this.lines, this.choices, this.bar);
    this.root.append(this.cell, words);
  }

  private act(press: ProjectCardPress): void {
    if (this.busy || !this.wire) return;
    const pending = this.press(press, this.wire);
    if (pending && typeof (pending as Promise<void>).then === 'function') {
      this.busy = true; this.update(this.wire, false, this.current);
      void (pending as Promise<void>).finally(() => { this.busy = false; if (this.wire) this.update(this.wire, false, this.current); });
    }
  }

  private current = '';

  /** `current`: the Project open now (its choice says so instead of Open). */
  update(x: ProjectCardWire, settle: boolean, current = ''): void {
    const cell = projectCardCell(x);
    if (settle && this.state === null) arrive(this.root);
    if (this.state !== cell) setLitCell(this.cell, cell, { size: 13, label: x.said, settle: settle && this.state !== null });
    this.state = cell; this.wire = x; this.current = current;
    const key = JSON.stringify([x, this.busy, current]);
    if (key === this.drawn) return;
    this.drawn = key;
    this.title.textContent = x.title;
    this.status.textContent = x.said;
    this.root.dataset.state = x.status; this.root.dataset.kind = x.kind;
    this.root.setAttribute('aria-label', `${x.title}: ${x.said}`);
    this.lines.replaceChildren(...x.lines.map((line) => make('li', '', line)));
    this.lines.hidden = !x.lines.length;
    this.choices.replaceChildren(...x.choices.map((choice) => {
      const row = make('li', 'chat-pcard-choice');
      const words = make('span', 'chat-pcard-choice-words');
      words.append(make('span', 'chat-pcard-choice-name', choice.name), make('span', 'chat-pcard-choice-detail', choice.detail));
      const open = make('button', 'chat-xcard-act', choice.session === current ? 'Open now' : 'Open');
      open.type = 'button'; open.dataset.session = choice.session;
      open.disabled = this.busy || choice.session === current;
      open.setAttribute('aria-label', choice.session === current ? `${choice.name} is open now` : `Open ${choice.name}`);
      open.addEventListener('click', () => this.act({ open: choice.session }));
      row.append(words, open);
      return row;
    }));
    this.choices.hidden = !x.choices.length;
    this.bar.replaceChildren(...x.actions.map((action) => {
      const button = make('button', `chat-xcard-act${action.primary ? ' is-primary' : ''}`, action.label);
      button.type = 'button'; button.disabled = this.busy; button.dataset.action = action.id;
      button.addEventListener('click', () => this.act({ action: action.id }));
      return button;
    }));
    this.bar.hidden = !x.actions.length;
  }
}
