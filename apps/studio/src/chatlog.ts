// The studio's chat: the one owner of the log. ChatModel (chatmodel.ts) decides what is in it;
// this draws its decisions, adding and updating lines one at a time rather than redrawing. It also draws Activity
// (activity.ts decides it): newest first, one entry per request, a line per step that opens to plain details.
import { renderMarkdown } from './markdown';
import { ChatModel, type Change, type LogLine, type LocalNoticeOptions } from './chatmodel';
import {
  actionName, buildActivity, capital, entryOf, fromSessionWords, hoverLine, unresolvedFailures, type ActivityEntry,
  activityDays, hasProblem,
} from './activity';
import { renderActivityEntry, type ActivityRenderHooks } from './activity-render';
import { ExecutionLogView, type ExecutionRecordingControl } from './executionlog';
import type { ResearchSession, RetainedResultRef, ServerMessage, ToolRun, Trial } from './protocol';
import type { RetryRequest } from './operation-retry';
import type { StepFact } from './strip-sources';
import { arrive, cardGroups, ResearchCard, type ResearchCardSource } from './chat-cards';
import { ChatFollow, type ObserverClass, type ScrollBox } from './chat-scroll';
import { HandoffRow, handoffView, handoffsInFlight, type FlightEntry } from './handoffs';
import type { LitCellState } from './lit-cell';

/** How long the answer's slot waits for its answer after the server says the request stopped thinking (its answer is
 * filed just after): then it goes, so it can never stick. */
export const SLOT_GRACE_MS = 2000;

export { unresolvedFailures } from './activity';

export interface ChatHooks {
  activity?: HTMLOListElement;
  openActivity?(): void;
  activityCount?(count: number): void;
  openResult?(source: RetainedResultRef): void;
  executionRecording?: ExecutionRecordingControl; // UI diagnostics only; never passed to ChatModel
  retryOperation?: RetryRequest;
  research(rid: string): ResearchSession | undefined; // to offer "Show it" only when there is something
  show(rid: string): void; // open a researcher's work in the result viewer
  describe(objectIds: string[]): void; // an answer named these outlines: fetch their descriptions
  hint(text: string, error: boolean): void; // a passing line under the view, not a chat post
  voiceLive(): boolean; // this studio is talking now (live or connecting)
  unread(): void; // something new was said (the folded-away chat shows a dot)
  /** Each answer's steps as they stand, after every change (the Working strip shows the ones still running after their
   * answer: strip-sources.ts StepPills). Steps of a replayed log that were left running are not reported. */
  steps?(steps: StepFact[]): void;
  /** The researchers a request of this conversation started, as cards under it (chat-cards.ts); none, no cards. */
  researchCards?: ResearchCardSource;
  /** The voice's hand-offs to Hooke's brain still working in this conversation, after every change, for the strip over
   * the composer (handoffs.ts FlightStrip); each entry jumps to its row. */
  flight?(entries: FlightEntry[]): void;
  /** Draws the lit cell (lit-cell.ts) of the answer's slot and its receipts; none, words only. */
  cells?: { make(state: LitCellState, label: string): Element; set(cell: Element, state: LitCellState, label: string): void };
  /** The browser's ResizeObserver, so the end stays in view while an answer grows (chat-scroll.ts); none, only as
   * lines are drawn. */
  ResizeObserver?: ObserverClass | null;
}

export class ChatLog {
  private readonly root: HTMLOListElement;
  private readonly starters: HTMLElement; // the suggested first questions, while the log is empty
  private readonly hooks: ChatHooks;
  private readonly model: ChatModel;
  private readonly items = new Map<number, HTMLLIElement>();
  /** The answer's slot (or a receipt in its place): always the last line, under the request it answers. */
  private thinkingItem: HTMLLIElement | null = null;
  private slotCell: Element | null = null;
  private slotWords: HTMLElement | null = null;
  private slotState: LitCellState | null = null;
  /** The slot shows: from "thinking" until its answer, an unpublished-answer status, a standalone step, another
   * conversation, or SLOT_GRACE_MS after thinking ends. */
  private waiting = false;
  private waitingFloor = -Infinity; // the newest turn when it started: a later line of an earlier turn is not its answer
  private graceTimer: ReturnType<typeof setTimeout> | null = null;
  private keepWaiting = false; // the same conversation drawn again (a replay) while its answer is being written
  /** Researcher cards: one element per request's group, one card per researcher (chat-cards.ts). */
  private readonly cardItems = new Map<string, HTMLLIElement>();
  private readonly cards = new Map<string, ResearchCard>();
  private cardClock: ReturnType<typeof setInterval> | null = null;
  /** The voice's hand-offs, one row each by the line's key (handoffs.ts); their clocks tick while one works. */
  private readonly handoffRows = new Map<number, HandoffRow>();
  private handoffClock: ReturnType<typeof setInterval> | null = null;
  private flightKey = '';
  /** Scrolling that respects reading (chat-scroll.ts). */
  private readonly scroller: ChatFollow;
  private readonly execution: ExecutionLogView;
  private executionEnabled = true;
  private awaitingAnswer = false;
  private answering = false; // its steps have run; only its answer is being written (the server says so)
  private online = true;
  private replayedPending = new Set<number>();
  // Activity, as drawn: each entry's element and what it was drawn from, the lines whose details are open, and the
  // entries' request headings (where an answer's "What powers it" ⓘ goes).
  private entries: ActivityEntry[] = [];
  private readonly entryItems = new Map<string, HTMLLIElement>();
  private readonly entryVersions = new Map<string, string>();
  private readonly entryHeadings = new Map<string, HTMLElement>();
  private readonly opened = new Set<string>();
  private readonly asked = new Set<string>(); // entries an answer's ⓘ opened (shown even with nothing to say yet)
  /** Activity's Problems only: just the entries where something did not work (the rest are kept, only hidden). */
  private problemsOnly = false;
  private problemsToggle: HTMLButtonElement | null = null;
  private problemsNone: HTMLElement | null = null;
  private readonly activityHooks: ActivityRenderHooks;
  /** Told the entries each time Activity is built (the side bar's column draws the same ones). */
  private readonly activityListeners = new Set<(entries: ActivityEntry[]) => void>();

  /** Live voice transcripts, as the voice client hears and says them. */
  readonly voice = {
    heard: (text: string | undefined, interim: boolean) => this.draw(this.model.heardSpeech(text, interim)),
    said: (text: string | undefined) => this.draw(this.model.saidSpeech(text)),
    turnEnd: () => this.model.turnEnd(),
    stopped: () => this.model.voiceStopped(),
  };

  constructor(root: HTMLOListElement, starters: HTMLElement, hooks: ChatHooks) {
    this.root = root;
    this.starters = starters;
    this.hooks = hooks;
    this.model = new ChatModel(() => hooks.voiceLive());
    this.execution = new ExecutionLogView(hooks.activity ?? root, hooks.executionRecording, hooks.retryOperation);
    this.activityHooks = { research: (rid) => hooks.research(rid), show: (rid) => hooks.show(rid), opened: this.opened,
      openFull: () => hooks.openActivity?.() };
    this.scroller = new ChatFollow(root as unknown as ScrollBox, hooks.ResizeObserver ?? null);
  }

  /** "New messages ↓": the page places it 8 px above the composer (chat-scroll.ts). */
  get newMessages(): HTMLButtonElement {
    return this.scroller.pill;
  }

  /** Your own send: the log follows to its end. */
  sent(): void {
    this.scroller.follow();
  }

  /** A Project's saved reading position, put back (following only if that is the end). */
  restoreReading(scrollTop: number): void {
    this.scroller.restoreReading(scrollTop);
  }

  /** The server's researchers changed (a `research` or `research-list` message): their cards follow. */
  researchChanged(): void {
    const added = this.placeCards(true);
    if (added) this.hooks.unread();
    this.scroller.changed({ news: added });
  }

  /** Listen to Activity's entries, newest first, each time they are built (at once with those built so far). Returns
   * its removal. */
  onActivity(listener: (entries: ActivityEntry[]) => void): () => void {
    this.activityListeners.add(listener);
    listener(this.entries);
    return () => this.activityListeners.delete(listener);
  }

  /** Conversation lines, application notices and optional execution inspection. */
  apply(message: ServerMessage): void {
    if (message.type === 'execution' || message.type === 'execution-list') {
      if (message.type === 'execution-list') {
        this.execution.clear(true);
        this.execution.generation = message.generation;
        this.executionEnabled = message.enabled;
      }
      if (!this.executionEnabled) return;
      for (const entry of message.type === 'execution' ? [message.entry] : message.entries) this.execution.store.add(entry);
      this.inspectable();
      return;
    }
    if (message.type === 'hello') this.execution.clear(true);
    if (message.type === 'day') this.execution.day = message.id;
    if (message.type === 'thread') this.execution.day = message.day;
    // The conversation drawn again as it was (a replay of the open one) keeps the slot of an answer still being written.
    this.keepWaiting = message.type === 'thread' && this.awaitingAnswer && message.thread === this.model.active && message.day === this.model.activeDay;
    const changes = this.model.apply(message);
    if (message.type === 'thread' || message.type === 'day' || message.type === 'hello') {
      this.replayedPending = new Set(this.model.lines.filter(line => line.actions.some(a => a.ok === null)).map(line => line.key));
    } else for (const change of changes) if ('line' in change) this.replayedPending.delete(change.line.key);
    this.draw(changes);
    if (message.type === 'day' || message.type === 'thread') this.inspectable();
  }

  /** The conversation open now (also set by `day` and `thread` messages). */
  setActiveThread(id: string | null): void {
    this.model.active = id;
    this.inspectable();
  }

  system(text: string, options?: LocalNoticeOptions): void {
    this.draw(this.model.system(text, options));
  }

  endTrial(): void {
    this.model.endTrial();
  }

  arms(trial: Trial): void {
    this.draw(this.model.arms(trial));
  }

  reveal(trialId: string, reveal: Record<string, { label: string }>, winner: string): void {
    this.draw(this.model.reveal(trialId, reveal, winner));
  }

  /** The connection: lost, the slot says the work's status is unknown rather than that it is working. */
  connected(online: boolean): void {
    this.online = online;
    if (!online) this.awaitingAnswer = false;
    this.drawWorking();
    this.placeCards(false);
  }

  /** The request's state, as the server says it: thinking (`on`), and `answering` once its steps have run and only
   * its answer is being written, so the slot never says "Working on it" over a finished step. "Thinking" ending leaves
   * the slot for its answer, filed just after, for SLOT_GRACE_MS at most. */
  thinking(on: boolean, phase?: 'answering'): void {
    this.awaitingAnswer = on;
    if (on) {
      this.answering = phase === 'answering';
      if (this.graceTimer !== null) { clearTimeout(this.graceTimer); this.graceTimer = null; }
      if (!this.waiting) this.waitingFloor = this.model.lines.reduce((top, line) => line.turnId !== undefined && line.turnId > top ? line.turnId : top, -Infinity);
      this.waiting = true;
    } else if (this.waiting && this.graceTimer === null) {
      this.graceTimer = setTimeout(() => {
        this.graceTimer = null;
        if (this.awaitingAnswer) return;
        this.endWaiting();
        this.drawWorking();
        this.placeCards(false);
        this.scroller.changed();
      }, SLOT_GRACE_MS);
    }
    this.drawWorking();
    this.placeCards(false);
    this.scroller.changed();
  }

  /** An answer is on its way: the server said the request is thinking and its answer has not come (restart-readiness.ts). */
  get writing(): boolean { return this.waiting; }

  /** The request's own reply came (its answer, if any, was drawn before it): the slot has nothing left to wait for. */
  settled(): void {
    if (!this.waiting) return;
    this.endWaiting();
    this.drawWorking();
    this.placeCards(false);
    this.scroller.changed();
  }

  private endWaiting(): void {
    this.waiting = false;
    this.answering = false;
    if (this.graceTimer !== null) { clearTimeout(this.graceTimer); this.graceTimer = null; }
  }

  /** Whether a new line is the waiting slot's answer: a typed answer, an unpublished-answer status, or a standalone
   * step's receipt, of this request (a later line of an earlier turn is not). */
  private answers(line: LogLine): boolean {
    if (!this.waiting) return false;
    if (line.turnId !== undefined && line.turnId <= this.waitingFloor) return false;
    return (line.role === 'assistant' && !line.voice) || !!line.answerStatus
      || (line.role === 'activity' && line.actions.length > 0 && !line.notice && !line.event);
  }

  /** The assistant's own slot (the workspace-feel plan, "One slot for the answer"): a small lit cell and calm words
   * where its answer will be, aligned with answers, replaced in place by the answer (which is drawn just above it as the
   * slot goes), so the log never jumps or leaves a gap. Steps still running after their answer are on the Working strip
   * (the steps hook), not here. The same place keeps the receipts that are facts: a photo displayed, earlier work with
   * no final result recorded, a lost connection. A failure is never a card of its own (the user's rule of 2026-10-11:
   * the model answers, not an error message): its answer carries a "!" that opens Activity. */
  private drawWorking(): void {
    const pending = this.model.lines.flatMap(line => line.actions.filter(a => a.ok === null)
      .map(action => ({ action, historical: this.replayedPending.has(line.key) })));
    const active = pending.filter(p => !p.historical);
    const latestActions = latestRequestActions(this.model.lines);
    const photoFailed = latestActions.some(action => action.tool === 'reference_photo' && action.ok === false);
    const photoDelivered = latestActions.some(action => action.tool === 'reference_photo' && action.ok === true);
    const failed = unresolvedFailures(latestActions).length > 0 && pending.length === 0;
    const state: { role: string; cell: LitCellState; words: string } | null =
      this.waiting && this.online ? { role: 'thinking', cell: 'working', words: this.answering ? 'Writing the answer' : 'Working on it' }
      : !this.online && (this.waiting || active.length > 0) ? { role: 'work-unknown', cell: 'stopped', words: 'Connection lost · work status unavailable' }
      : photoDelivered && !photoFailed && pending.length === 0 && !failed ? { role: 'work-complete', cell: 'ready', words: 'Photo displayed' }
      : this.online && pending.length > 0 && active.length === 0 ? { role: 'work-unknown', cell: 'stopped', words: 'Earlier work has no final result recorded' }
      : null;
    if (!state) {
      if (this.thinkingItem) { this.scroller.unwatch(this.thinkingItem); this.thinkingItem.remove(); }
      this.thinkingItem = null; this.slotCell = null; this.slotWords = null; this.slotState = null;
      return;
    }
    if (!this.thinkingItem) {
      const item = document.createElement('li');
      item.className = 'chat-slot';
      item.setAttribute('role', 'status');
      item.setAttribute('aria-live', 'polite');
      this.slotCell = this.hooks.cells?.make(state.cell, state.words) ?? null;
      this.slotWords = document.createElement('span');
      this.slotWords.className = 'chat-slot-words';
      if (this.slotCell) item.append(this.slotCell);
      item.append(this.slotWords);
      this.thinkingItem = item;
      this.slotState = state.cell;
      this.root.append(item);
      this.scroller.watch(item);
    }
    if (this.thinkingItem.dataset.role !== state.role) this.thinkingItem.dataset.role = state.role;
    if (this.slotWords && this.slotWords.textContent !== state.words) this.slotWords.textContent = state.words;
    if (this.slotCell && this.slotState !== state.cell) this.hooks.cells?.set(this.slotCell, state.cell, state.words);
    this.slotState = state.cell;
  }

  /** The researcher cards, each group right after its request's answer (or the slot while it is written). Returns
   * whether a card arrived. `settle`: a card that arrives now lights with its one settle (not one redrawn by a replay). */
  private placeCards(settle: boolean): boolean {
    const source = this.hooks.researchCards;
    if (!source) return false;
    const sessions = [...source.list()];
    const byRid = new Map(sessions.map((s) => [s.rid, s]));
    const groups = cardGroups(this.model.lines, sessions, { day: this.model.activeDay, thread: this.model.active },
      (line) => { const item = this.items.get(line.key); return !!item && !item.hidden; });
    const keep = new Set(groups.map((group) => group.key)), listed = new Set(groups.flatMap((group) => group.rids));
    for (const [key, item] of this.cardItems) if (!keep.has(key)) { this.scroller.unwatch(item); item.remove(); this.cardItems.delete(key); }
    for (const rid of [...this.cards.keys()]) if (!listed.has(rid)) this.cards.delete(rid);
    const now = Date.now() / 1000;
    let added = false, counting = false;
    for (const group of groups) {
      let item = this.cardItems.get(group.key);
      if (!item) {
        item = document.createElement('li');
        item.className = 'chat-cards';
        item.dataset.role = 'cards';
        this.cardItems.set(group.key, item);
      }
      const wanted = group.rids.map((rid) => {
        let card = this.cards.get(rid);
        const fresh = !card;
        if (!card) { card = new ResearchCard(rid, (id) => source.open(id)); this.cards.set(rid, card); added = true; }
        const view = source.view(byRid.get(rid)!, now);
        card.update(view, fresh && settle);
        if (view.counting) counting = true;
        return card.root;
      });
      // A card that arrives is added after the others, which are not moved (moving one would restart its cell's breathing).
      const have = Array.from(item.children);
      if (have.length <= wanted.length && have.every((card, i) => wanted[i] === card)) item.append(...wanted.slice(have.length));
      else item.replaceChildren(...wanted);
      // Right after its anchor: the request's answer, or the slot (else the end) while the answer is being written.
      const anchor = group.after === null ? this.thinkingItem : this.items.get(group.after) ?? null;
      const kids = Array.from(this.root.children);
      if (anchor && kids.includes(anchor)) {
        const next = kids[kids.indexOf(anchor) + 1] ?? null;
        if (next !== item) this.root.insertBefore(item, next);
      } else if (group.after === null) {
        if (kids[kids.length - 1] !== item) this.root.append(item);
      } else if (!item.parentElement) this.root.append(item);
      this.scroller.watch(item);
    }
    this.tickCards(counting);
    return added;
  }

  /** While a card counts its time ("Working 0:12"), its line is redrawn each second; nothing else moves. */
  private tickCards(counting: boolean): void {
    if (!counting) { if (this.cardClock !== null) { clearInterval(this.cardClock); this.cardClock = null; } return; }
    if (this.cardClock !== null) return;
    this.cardClock = setInterval(() => {
      const source = this.hooks.researchCards;
      if (!source) return;
      const byRid = new Map([...source.list()].map((s) => [s.rid, s])), now = Date.now() / 1000;
      let still = false;
      for (const card of this.cards.values()) {
        const s = byRid.get(card.rid);
        if (!s || !card.view?.counting) continue;
        const view = source.view(s, now); card.update(view, false); if (view.counting) still = true;
      }
      if (!still) this.tickCards(false);
    }, 1000);
  }

  private reportSteps(): void {
    if (!this.hooks.steps) return;
    this.hooks.steps(this.model.lines.flatMap(line => this.replayedPending.has(line.key) ? [] : line.actions.map((action, i) => ({
      key: `${line.key}:${action.requestId && action.actionId ? `${action.requestId}:${action.actionId}` : i}`,
      tool: action.tool, ok: action.ok, summary: action.summary,
      ...(typeof action.at === 'number' ? { at: action.at } : {}), ...(action.progress ? { progress: action.progress } : {}) }))));
  }

  private draw(changes: Change[]): void {
    if (!changes.length) return;
    // A conversation drawn afresh (another one, or a replay) is not news, and its end is shown.
    const cleared = changes.some(change => change.kind === 'clear');
    let news = false, own = false, answered = false;
    let answer: HTMLLIElement | null = null; // a new answer, shown from its start when it is taller than the view
    for (const change of changes) {
      switch (change.kind) {
        case 'clear':
          this.execution.clear();
          for (const item of this.items.values()) { this.scroller.unwatch(item); item.remove(); }
          this.items.clear();
          for (const item of this.cardItems.values()) { this.scroller.unwatch(item); item.remove(); }
          this.cardItems.clear();
          this.cards.clear();
          this.handoffRows.clear();
          if (!this.keepWaiting) this.endWaiting(); // another conversation: its slot is not this one's
          this.marks.clear();
          this.hooks.activity?.replaceChildren();
          this.entryItems.clear();
          this.entryVersions.clear();
          this.entryHeadings.clear();
          this.asked.clear();
          this.entries = [];
          break;
        case 'add': {
          if (change.line.event) break; // an Activity line only: nothing of it in the conversation
          const item = change.line.handoff ? this.handoffItem(change.line, !cleared) : this.render(change.line);
          this.items.set(change.line.key, item);
          const after = this.model.lines.slice(this.model.lines.indexOf(change.line) + 1)
            .map(line => this.items.get(line.key)).find(Boolean);
          // Above the slot: an answer takes the slot's place, which goes as it arrives.
          this.root.insertBefore(item, after ?? this.thinkingItem);
          this.scroller.watch(item);
          if (!cleared && this.answers(change.line)) answered = true;
          if (!cleared && !item.hidden) {
            if (change.line.role === 'user') own = true; // your words: the log follows
            else if (change.line.role === 'assistant' || change.line.answerStatus) { news = true; answer = item; if (!change.line.voice) arrive(item); }
            else news = true;
          }
          if (change.unread && !item.hidden) this.hooks.unread();
          break;
        }
        case 'text': {
          const text = this.items.get(change.line.key)?.querySelector<HTMLElement>('.message-text');
          if (text) messageText(text, change.line); // keep receipts, buttons and inspection attached
          const item = this.items.get(change.line.key);
          if (item) item.hidden = !this.conversationLine(change.line);
          if (item && !item.hidden && !cleared && change.line.role !== 'user') news = true;
          break;
        }
        case 'action':
        case 'event':
          // drawActivity draws Activity again from the lines as they are now.
          break;
        case 'handoff':
          // Its later state, in place; one that settled is news for a reader scrolled away.
          if (this.drawHandoff(change.line) && !cleared) news = true;
          break;
        case 'tag': {
          const tag = this.items.get(change.line.key)?.querySelector('.arm-tag');
          if (tag) tag.textContent = change.line.tag ?? '';
          break;
        }
        case 'hint':
          this.hooks.hint(change.text, change.error);
          break;
        case 'describe':
          this.hooks.describe(change.ids);
          break;
      }
    }
    if (answered) this.endWaiting();
    this.drawActivity();
    for (const line of this.model.lines) {
      const item = this.items.get(line.key);
      if (item) this.markFailures(line, item);
      if (item && line.role !== 'user' && this.resultCards(item, line)) {
        if (!cleared) { news = true; this.hooks.unread(); }
      }
    }
    this.drawWorking();
    if (this.placeCards(!cleared) && !cleared) news = true;
    this.reportSteps();
    this.reportFlight();
    // Starters until the conversation itself begins: Activity's own lines ("You started a new session") are not it,
    // or a Project made with + never showed them.
    this.starters.hidden = this.model.lines.some(line => this.conversationLine(line));
    if (own) this.scroller.follow();
    else this.scroller.changed({ news, answer, cleared });
    this.inspectable();
  }

  /** The answer's ⓘ says "!" while a step of its request did not work, so the failure is seen where the answer is,
   * in the model's words, with the step itself one click away in Activity (no error card). Its hover says the
   * request's models on one line ("Gemini 3.8 Flash · drawn by EdgeTAM · 4.8 s"). */
  private readonly marks = new Map<number, HTMLElement>();

  private markFailures(line: LogLine, item?: HTMLLIElement): void {
    const entry = entryOf(this.entries, line);
    const button = this.marks.get(line.key);
    const models = (line.role === 'assistant' || line.answerStatus) ? hoverLine(entry, line) : '';
    if (item && line.role === 'assistant' && models) item.title = models;
    if (!button) return;
    const turn = line.turnId;
    const actions = turn === undefined ? line.actions : this.model.lines.filter(l => l.turnId === turn).flatMap(l => l.actions);
    const failed = entry?.kind === 'request' ? entry.failed : [...new Set(unresolvedFailures(actions).map(a => actionName(a.tool)))];
    // Steps that finished after the answer was written, with no model answer since to say so (its late call can time
    // out: live 2026-10-11, "still in progress" stayed under a finished micro-sam outline): a ✓ says they did.
    const later = line.role === 'assistant' && turn !== undefined
      ? this.model.lines.filter(l => l.turnId === turn && l.role === 'activity' && l.key > (line.answered ?? line.key) && l.actions.length) : [];
    const finished = !failed.length && later.length > 0 && later.every(l => l.actions.every(a => a.ok === true))
      ? [...new Set(later.flatMap(l => l.actions.map(a => actionName(a.tool))))] : [];
    button.textContent = failed.length ? '!' : finished.length ? '✓' : 'ⓘ';
    button.dataset.state = failed.length ? 'failed' : finished.length ? 'finished' : '';
    button.title = (failed.length ? `Something in this request didn't work (${failed.join(', ')}): see what happened`
      : finished.length ? `Finished after this answer (${finished.join(', ')}): see what happened`
      : 'What happened') + (models ? ` · ${models}` : '');
  }

  private inspectable(): void {
    const turns = new Map<number, HTMLElement>();
    const objects = new Map<string, HTMLElement>();
    for (const entry of this.entries) {
      const target = this.entryHeadings.get(entry.key);
      if (!target) continue;
      if (entry.turnId !== undefined) turns.set(entry.turnId, target);
      const objectId = entry.kind === 'click' ? this.clickObject(entry) : undefined;
      if (objectId) objects.set(objectId, target);
    }
    this.execution.sync(this.model.active, turns, objects);
  }

  private clickObject(entry: ActivityEntry): string | undefined {
    const line = this.model.lines.find(l => entry.lineKeys.includes(l.key));
    return line?.event?.ref?.objectId;
  }

  private conversationLine(line: LogLine): boolean {
    return !!line.handoff || !!line.answerStatus || (line.role !== 'activity' && line.role !== 'system' && !!line.text.trim());
  }

  /** A hand-off's row (handoffs.ts), as it stands. `settle`: it arrived now (not redrawn by a replay). */
  private handoffItem(line: LogLine, settle: boolean): HTMLLIElement {
    const row = new HandoffRow(line.handoff!.id, { activity: (turnId) => this.focusTurn(turnId) });
    this.handoffRows.set(line.key, row);
    row.update(line.handoff!, handoffView(line.handoff!, Date.now() / 1000, line.replayed), settle);
    return row.root;
  }

  /** A hand-off's later state drawn in place. Returns whether it settled now (done, did not work, or not known). */
  private drawHandoff(line: LogLine): boolean {
    const row = this.handoffRows.get(line.key);
    if (!row || !line.handoff) return false;
    const was = row.view?.state;
    const view = handoffView(line.handoff, Date.now() / 1000, line.replayed);
    row.update(line.handoff, view, true);
    return was === 'working' && view.state !== 'working';
  }

  /** The hand-offs still working, for the strip; while one is, the rows' clocks tick each second (nothing else moves). */
  private reportFlight(): void {
    const rows = this.model.lines.filter((line) => line.handoff && this.handoffRows.has(line.key))
      .map((line) => ({ line: line.handoff!, replayed: line.replayed }));
    const entries = handoffsInFlight(rows, (id) => this.revealHandoff(id));
    const key = entries.map((entry) => `${entry.key}|${entry.name}|${entry.doing}`).join('\n');
    if (key !== this.flightKey) { this.flightKey = key; this.hooks.flight?.(entries); }
    this.tickHandoffs(entries.length > 0);
  }

  private tickHandoffs(on: boolean): void {
    if (!on) { if (this.handoffClock !== null) { clearInterval(this.handoffClock); this.handoffClock = null; } return; }
    if (this.handoffClock !== null) return;
    this.handoffClock = setInterval(() => {
      let still = false;
      for (const line of this.model.lines) {
        const row = line.handoff ? this.handoffRows.get(line.key) : undefined;
        if (!row || row.view?.state !== 'working') continue;
        row.update(line.handoff!, handoffView(line.handoff!, Date.now() / 1000, line.replayed), false);
        still = true;
      }
      if (!still) this.tickHandoffs(false);
    }, 1000);
  }

  /** Brings a hand-off's row into view, marked for a moment, with focus on it (its strip entry was chosen). */
  revealHandoff(id: string): boolean {
    const row = [...this.handoffRows.values()].find((r) => r.id === id);
    if (!row || row.root.hidden) return false;
    this.bringIntoView(row.root, row.head);
    return true;
  }

  /** Brings a researcher's chat card into view the same way; false when this conversation has none for it. */
  revealResearcher(rid: string): boolean {
    for (const item of this.cardItems.values()) {
      const card = Array.from(item.children).find((node) => (node as HTMLElement).dataset?.rid === rid) as HTMLElement | undefined;
      if (card) { this.bringIntoView(card, card); return true; }
    }
    return false;
  }

  private bringIntoView(target: HTMLElement, focus: HTMLElement): void {
    target.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' });
    focus.focus?.({ preventScroll: true });
    target.dataset.flash = '';
    setTimeout(() => { delete target.dataset.flash; }, 1400);
  }

  /** Activity at a request, by its turn (a hand-off's row opens the brain's request there). */
  private focusTurn(turnId: number): void {
    const line = this.model.lines.find((l) => l.turnId === turnId && l.handoff) ?? this.model.lines.find((l) => l.turnId === turnId);
    if (line) this.focusEntry(line);
  }

  /** Activity, newest first: each entry drawn again only when what it shows changed. */
  private drawActivity(): void {
    this.entries = buildActivity(this.model.lines);
    for (const listener of [...this.activityListeners]) listener(this.entries);
    const root = this.hooks.activity;
    if (!root) return;
    this.problemsFilter(root);
    const keep = new Set(this.entries.map(entry => entry.key));
    for (const [key, item] of this.entryItems) {
      if (keep.has(key)) continue;
      item.remove();
      this.entryItems.delete(key); this.entryVersions.delete(key); this.entryHeadings.delete(key);
    }
    this.entries.forEach((entry, index) => {
      const version = JSON.stringify(entry);
      let item = this.entryItems.get(entry.key);
      if (!item || this.entryVersions.get(entry.key) !== version) {
        const fresh = this.renderEntry(entry);
        if (item) item.replaceWith(fresh);
        item = fresh;
        this.entryItems.set(entry.key, item);
        this.entryVersions.set(entry.key, version);
      }
      item.hidden = (!entry.lines.length && !this.asked.has(entry.key)) || (this.problemsOnly && !hasProblem(entry));
      const at = root.children[index];
      if (at !== item) root.insertBefore(item, at ?? null);
    });
    // Day separators over the entries shown ("Today", "Oct 24"): the workspace draws them over the entry (data-day).
    const days = activityDays(this.entries, key => this.entryItems.get(key)?.hidden === false, Date.now() / 1000);
    for (const [key, item] of this.entryItems) {
      const day = days.get(key);
      if (day) { if (item.dataset.day !== day) item.dataset.day = day; } else if (item.dataset.day !== undefined) delete item.dataset.day;
    }
    this.hooks.activityCount?.(this.entries.filter(entry => entry.lines.length || this.asked.has(entry.key)).length);
    if (this.problemsNone) this.problemsNone.hidden = !this.problemsOnly || this.entries.some(entry => entry.lines.length && hasProblem(entry));
  }

  /** Problems only, above the list: once, beside the Activity it filters. */
  private problemsFilter(root: HTMLElement): void {
    // Made once; put back beside the list if the list moved (the workspace moves Activity in and out of Data).
    if (this.problemsToggle && this.problemsNone) {
      if (this.problemsNone.nextSibling !== root) root.before?.(this.problemsToggle, this.problemsNone);
      return;
    }
    const toggle = document.createElement('button');
    toggle.type = 'button'; toggle.className = 'activity-problems'; toggle.textContent = 'Problems only';
    toggle.setAttribute('aria-pressed', String(this.problemsOnly));
    toggle.title = 'Show only requests where something did not work';
    toggle.addEventListener('click', () => {
      this.problemsOnly = !this.problemsOnly; toggle.setAttribute('aria-pressed', String(this.problemsOnly)); this.drawActivity();
    });
    const none = document.createElement('p');
    none.className = 'activity-problems-none'; none.textContent = 'No recorded failures in this Activity.'; none.hidden = true;
    root.before?.(toggle, none);
    this.problemsToggle = toggle; this.problemsNone = none;
  }

  /** One entry, drawn as the side bar's column draws it (activity-render.ts); its heading is where an answer's
   * "What powers it" ⓘ goes. */
  private renderEntry(entry: ActivityEntry): HTMLLIElement {
    const { item, heading } = renderActivityEntry(entry, this.activityHooks);
    this.entryHeadings.set(entry.key, heading);
    return item;
  }

  /** The answer's ⓘ or "!": Activity opens at its request, with what did not work opened. */
  private focusEntry(line: LogLine): void {
    this.hooks.openActivity?.();
    const entry = entryOf(this.entries, line);
    if (!entry) return;
    for (const step of entry.lines) if (step.state === 'failed') this.opened.add(`${entry.key}|${step.key}`);
    this.asked.add(entry.key);
    // Asked for from its answer: shown even under Problems only, which turns off rather than hide it.
    if (this.problemsOnly && !hasProblem(entry)) { this.problemsOnly = false; this.problemsToggle?.setAttribute('aria-pressed', 'false'); }
    this.entryVersions.delete(entry.key); // drawn again with those lines open
    this.drawActivity();
    this.inspectable();
    const item = this.entryItems.get(entry.key);
    if (!item) return;
    item.scrollIntoView?.({ block: 'nearest' });
    item.className = 'activity-entry activity-focus';
    setTimeout(() => { if (this.entryItems.get(entry.key) === item) item.className = 'activity-entry'; }, 1600);
  }

  private resultCards(item: HTMLLIElement, line: LogLine): boolean {
    if (!this.hooks.openResult) return false;
    const refs = retainedResults(line.actions);
    if (line.role === 'activity' && refs.length) item.hidden = false;
    const stamp = JSON.stringify(refs);
    if (item.dataset.results === stamp) return false;
    item.dataset.results = stamp;
    item.querySelector('.chat-results')?.remove();
    if (!refs.length) return false;
    const cards = document.createElement('div'); cards.className = 'chat-results';
    for (const ref of refs) {
      const card = document.createElement('button'); card.type = 'button'; card.className = 'chat-result';
      const kind = document.createElement('span'); kind.className = 'chat-result-kind'; kind.textContent = ref.kind ?? 'Saved result';
      const title = document.createElement('span'); title.className = 'chat-result-title'; title.textContent = ref.title;
      const arrow = document.createElement('span'); arrow.className = 'chat-result-go'; arrow.textContent = '↗'; arrow.setAttribute('aria-hidden', 'true');
      card.append(kind, title, arrow); card.setAttribute('aria-label', `Open saved ${ref.title}`);
      card.addEventListener('click', () => this.hooks.openResult?.(ref)); cards.append(card);
    }
    item.append(cards);
    // A completed operation without a replacement answer still exposes its retained result.
    if (line.role === 'activity') item.hidden = false;
    return true;
  }

  /** A line of the conversation (Activity is drawn by renderEntry). */
  private render(line: LogLine): HTMLLIElement {
    const item = document.createElement('li');
    item.dataset.role = line.role;
    item.hidden = !this.conversationLine(line);
    if (line.role === 'arm') {
      item.dataset.trial = line.trial ?? '';
      item.dataset.arm = line.arm ?? '';
      const tag = document.createElement('span');
      tag.className = 'arm-tag';
      tag.textContent = line.tag ?? '';
      item.append(tag, line.text);
      return item;
    }
    if (line.turnId !== undefined) item.dataset.turn = String(line.turnId);
    if (line.voice) {
      item.dataset.voice = '';
      const spoken = document.createElement('span'); spoken.className = 'spoken';
      spoken.textContent = '◖'; spoken.setAttribute('aria-label', 'Spoken'); spoken.title = 'Spoken';
      item.append(spoken);
    }
    if (line.update) item.dataset.update = '';
    if (line.proactive) item.dataset.proactive = '';
    if (line.notice) {
      const notice = line.notice;
      item.dataset.appNotice = '';
      item.dataset.state = notice.tone === 'error' ? 'failed' : 'info';
      const label = document.createElement('span');
      label.className = 'app-notice-label';
      label.textContent = 'App activity';
      const status = document.createElement('span');
      status.className = 'app-notice-summary';
      status.textContent = notice.summary;
      item.append(label, ' ', status);
      if (notice.fromSession?.name || (notice.origin.day && notice.origin.day !== notice.day)) {
        // Work that finished in another session says which, by its name ("From 'Blood smear'").
        const origin = document.createElement('span');
        origin.className = 'app-notice-origin';
        const from = fromSessionWords(notice.fromSession);
        origin.textContent = from ? capital(from) : `From ${notice.origin.day}`;
        item.append(' ', origin);
      }
      // No research button from a bare origin ID: another day can reuse it.
      // The original producer's existing UI owns opening its actual resource.
    }
    if (line.answerStatus) {
      const status = document.createElement('span');
      status.className = 'answer-status';
      status.setAttribute('role', 'status');
      status.textContent = 'Response unavailable';
      item.append(status);
      item.title = `${line.model ?? ''}; ${line.calls ?? 0} model calls; no response published`;
    }
    if (line.role !== 'activity') {
      const text = document.createElement('span');
      text.className = 'message-text';
      messageText(text, line);
      item.append(text);
    }
    const rid = line.research;
    const shown = rid ? this.hooks.research(rid) : undefined;
    if (rid && (!shown || shown.outputs.length || shown.result)) { // only when there is something to show
      const open = document.createElement('button');
      open.type = 'button';
      open.className = 'open-research';
      open.textContent = 'Show it';
      open.addEventListener('click', () => this.hooks.show(rid));
      item.append(' ', open);
    }
    if (line.actions.length || line.answerStatus || line.role === 'assistant' && line.turnId !== undefined) {
      const inspect = document.createElement('button');
      inspect.type = 'button'; inspect.className = 'activity-info'; inspect.textContent = 'ⓘ';
      inspect.title = 'What happened'; inspect.setAttribute('aria-label', 'Show activity for this reply');
      inspect.addEventListener('click', () => this.focusEntry(line));
      item.append(inspect);
      this.marks.set(line.key, inspect);
      this.markFailures(line, item);
    }
    if ((line.role === 'assistant' || line.role === 'compare') && line.thinkMs) {
      // Which model answered and how fast: on hover only, so the chat reads as a conversation (markFailures says the
      // request's models on one line once its Activity entry is known).
      const who = line.model ? `${line.model}, ` : '';
      const pointed = line.role === 'compare' && line.labels?.length ? `. Would point at: ${line.labels.join(', ')}` : '';
      if (!item.title || line.role === 'compare') item.title = `${line.role === 'compare' ? 'For comparison: ' : ''}${who}${(line.thinkMs / 1000).toFixed(1)} s${pointed}`;
      if (line.role === 'compare') {
        const meta = document.createElement('span');
        meta.className = 'meta';
        meta.textContent = item.title; // a blind comparison needs to say what it is
        item.append(meta);
      }
    }
    return item;
  }
}

/** The model's answers are formatted (lists, tables, bold, code: markdown.ts, DOM nodes only); what the user typed
 * and the app's own lines stay plain text. */
function messageText(target: HTMLElement, line: LogLine): void {
  if (line.role === 'assistant') renderMarkdown(target, line.text);
  else target.textContent = line.text;
}

export function activityTime(line: Pick<LogLine, 'at' | 'actions'>): number | null {
  const times = [line.at, ...line.actions.map(action => action.at)].filter((at): at is number => typeof at === 'number' && Number.isFinite(at));
  return times.length ? Math.max(...times) : null;
}

/** Include standalone operation receipts even when no assistant answer was published. */
export function latestRequestActions(lines: LogLine[]): ToolRun[] {
  const start = lines.findLastIndex(line => line.role === 'user');
  const current = start >= 0 ? lines[start].turnId : undefined;
  return lines.slice(Math.max(0, start)).filter(line => current === undefined || line.turnId === current).flatMap(line => line.actions);
}

export function retainedResults(actions: ToolRun[]): RetainedResultRef[] {
  const refs = new Map<string, RetainedResultRef>();
  for (const action of actions) {
    const ref = action.resource;
    if (action.ok !== true || !ref?.day || !ref.id || !ref.title
      || !(ref.version === null || Number.isSafeInteger(ref.version))
      || !(ref.picture === null || Number.isSafeInteger(ref.picture))) continue;
    refs.set(JSON.stringify([ref.day,ref.id,ref.version,ref.picture]), ref);
  }
  return [...refs.values()];
}
