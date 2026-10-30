// An experiment's card in the chat (Part 2.5, W5; services/orchestrator/experiments.py): the plan the Assistant drafted
// (what is compared, on what, its steps and what it may cost) with Run it and Change, then how it is going, then where
// its results are. Every word on it is the server's (`experiment` messages): nothing here invents a step or a state,
// and Run it sends exactly the plan's version the card shows (a plan changed since is refused by the server).
//
// Which request: the plan's `requestId` (the request that drafted it) matched against the steps of this conversation's
// lines, as researcher cards are (chat-cards.ts); a plan whose request is not in the log is shown after the last line
// it arrived under, never moved after that.
import type { LogLine } from './chatmodel';
import type { ToolRun } from './protocol';
import { arrive } from './chat-cards';
import { litCell, setLitCell, type LitCellState } from './lit-cell';

/** An experiment as the server's card wire says it (experiments.py `Experiment.wire`). */
export interface ExperimentWire {
  id: string; key: string; n: number; name: string; title: string; session: string; thread: string | null;
  version: number; status: string; requestId: string | null; turnId: number | null; steps: string[]; cost: string;
  input: string; progress: string; why: string;
  outcome: { words?: string; document?: string; tab?: string; shown?: string };
  canRun: boolean; canStop: boolean; created: number; ended: number | null;
}

/** What a card shows: its cell, its head and status line, its steps (while it is a plan), its cost, and its actions. */
export interface ExperimentView {
  cell: LitCellState; title: string; status: string; steps: string[]; cost: string;
  actions: ('run' | 'change' | 'stop' | 'open')[];
}

const STATUS_WORDS: Record<string, string> = {
  proposed: 'Planned · runs on your yes', sampling: 'Holding the frames', running: 'Running', auditing: 'Auditing',
  landing: 'Putting the results in a Tab', done: 'Done', failed: 'Didn’t finish', stopped: 'Stopped',
  expired: 'Not run · the plan waited too long',
};

/** The card as its server record says it. */
export function experimentView(x: ExperimentWire): ExperimentView {
  const live = ['sampling', 'running', 'auditing', 'landing'].includes(x.status);
  const cell: LitCellState = x.status === 'proposed' ? (x.canRun ? 'needs' : 'stopped') : live ? 'working'
    : x.status === 'done' ? 'ready' : x.status === 'failed' ? 'attention' : 'stopped';
  const head = STATUS_WORDS[x.status] ?? x.status;
  const status = live ? `${head}${x.progress ? ` · ${x.progress}` : ''}`
    : x.status === 'done' ? `${head}${x.outcome.document ? ' · its results are in a Tab of their own' : ''}`
      : x.status === 'failed' || x.status === 'stopped' ? `${head}${x.why ? ` · ${x.why}` : ''}` : head;
  const actions: ExperimentView['actions'] = [];
  if (x.status === 'proposed' && x.canRun) actions.push('run', 'change');
  if (x.canStop) actions.push('stop');
  if (x.status === 'done' && x.outcome.document) actions.push('open');
  return { cell, title: `${x.name} · ${x.title}`, status, steps: x.status === 'proposed' ? x.steps : [],
           cost: x.status === 'proposed' ? x.cost : '', actions };
}

const stepsOf = (line: LogLine): ToolRun[] => [...line.actions, ...(line.event?.steps ?? [])];

export interface ExperimentGroup { key: string; after: number | null; ids: string[] }

/** The cards of this conversation, each under the request that drafted it (its answer, or the slot while it is written);
 * one whose request is not in the log stays under the line it arrived after (`anchors`, kept by the caller). */
export function experimentGroups(lines: readonly LogLine[], experiments: Iterable<ExperimentWire>,
  scope: { day: string | null; thread: string | null }, shown: (line: LogLine) => boolean,
  anchors: Map<string, number>): ExperimentGroup[] {
  const byRequest = new Map<string, { last: number | null; first: number }>();
  lines.forEach((line, index) => {
    for (const step of stepsOf(line)) {
      if (!step.requestId) continue;
      const at = byRequest.get(step.requestId) ?? { last: null, first: index };
      byRequest.set(step.requestId, at);
    }
    // The request's answer: the last line drawn after the request's steps, until the next words of the user.
  });
  const lastShown = [...lines].reverse().find((line) => shown(line));
  const groups = new Map<string, ExperimentGroup>();
  for (const x of experiments) {
    if (scope.day && x.session !== scope.day) continue;
    if (scope.thread && x.thread && x.thread !== scope.thread) continue;
    const found = x.requestId ? byRequest.get(x.requestId) : undefined;
    let after: number | null;
    let key: string;
    if (found) {
      const rest = lines.slice(found.first);
      const until = rest.findIndex((line, i) => i > 0 && line.role === 'user');
      const answer = (until < 0 ? rest : rest.slice(0, until)).filter((line) => shown(line)).at(-1);
      after = answer?.key ?? null;
      key = `request:${x.requestId}`;
    } else {
      if (!anchors.has(x.id) && lastShown) anchors.set(x.id, lastShown.key);
      after = anchors.get(x.id) ?? null;
      key = `anchor:${x.id}`;
    }
    const group = groups.get(key) ?? { key, after, ids: [] };
    group.ids.push(x.id);
    groups.set(key, group);
  }
  const order = new Map(lines.map((line, index) => [line.key, index]));
  return [...groups.values()].sort((a, b) => (a.after === null ? Infinity : order.get(a.after) ?? 0)
    - (b.after === null ? Infinity : order.get(b.after) ?? 0));
}

const make = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); if (className) node.className = className; if (text) node.textContent = text; return node;
};

const LABELS: Record<ExperimentView['actions'][number], string> = { run: 'Run it', change: 'Change…', stop: 'Stop', open: 'Open results' };

/** One card, updated in place (its cell is never rebuilt, so its breathing never restarts). */
export class ExperimentCard {
  readonly root: HTMLElement;
  readonly id: string;
  private readonly cell: SVGSVGElement;
  private readonly title: HTMLElement;
  private readonly status: HTMLElement;
  private readonly steps: HTMLOListElement;
  private readonly cost: HTMLElement;
  private readonly bar: HTMLElement;
  private state: LitCellState | null = null;
  private drawn = '';
  wire: ExperimentWire | null = null;
  busy = false;

  private readonly act: (action: ExperimentView['actions'][number], x: ExperimentWire) => Promise<void> | void;

  constructor(id: string, act: (action: ExperimentView['actions'][number], x: ExperimentWire) => Promise<void> | void) {
    this.id = id; this.act = act;
    this.root = make('div', 'chat-xcard'); this.root.dataset.experiment = id; this.root.setAttribute('role', 'group');
    this.cell = litCell('needs', { size: 13 });
    const words = make('div', 'chat-xcard-words');
    this.title = make('span', 'chat-xcard-title'); this.status = make('span', 'chat-xcard-status mm-num');
    this.status.setAttribute('aria-live', 'polite');
    this.steps = make('ol', 'chat-xcard-steps'); this.cost = make('p', 'chat-xcard-cost');
    this.bar = make('div', 'chat-xcard-bar');
    words.append(this.title, this.status, this.steps, this.cost, this.bar);
    this.root.append(this.cell, words);
  }

  update(x: ExperimentWire, settle: boolean): void {
    const view = experimentView(x);
    if (settle && this.state === null) arrive(this.root);
    if (this.state !== view.cell) setLitCell(this.cell, view.cell, { size: 13, label: view.status, settle: settle && this.state !== null });
    this.state = view.cell;
    this.wire = x;
    const key = JSON.stringify([view, this.busy]);
    if (key === this.drawn) return;
    this.drawn = key;
    this.title.textContent = view.title;
    this.status.textContent = view.status;
    this.root.dataset.state = x.status;
    this.root.setAttribute('aria-label', `${view.title}: ${view.status}`);
    this.steps.replaceChildren(...view.steps.map((step) => make('li', '', step)));
    this.steps.hidden = !view.steps.length;
    this.cost.textContent = view.cost; this.cost.hidden = !view.cost;
    this.bar.replaceChildren(...view.actions.map((action) => {
      const button = make('button', `chat-xcard-act${action === 'run' ? ' is-primary' : ''}`, LABELS[action]);
      button.type = 'button'; button.disabled = this.busy; button.dataset.action = action;
      button.addEventListener('click', () => {
        if (this.busy || !this.wire) return;
        const pending = this.act(action, this.wire);
        if (pending && typeof (pending as Promise<void>).then === 'function') {
          this.busy = true; this.update(this.wire, false);
          void (pending as Promise<void>).finally(() => { this.busy = false; if (this.wire) this.update(this.wire, false); });
        }
      });
      return button;
    }));
    this.bar.hidden = !view.actions.length;
  }
}
