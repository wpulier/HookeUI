// What the chat holds, decided without the DOM (chatlog.ts draws it). The chat is the
// conversation, not an activity log: one conversation (thread) at a time, its lines as they
// were shown, and later tool results updating their answer's line in place.
import { SPEECH_LINE_GAP_MS } from './overlay';
import type { ActivityEvent, AppNotice, ChatAction, ChatAnswerStatus, ChatLine, HandoffLine, ServerMessage, ToolRun, Trial } from './protocol';

export type Role = ChatLine['role'] | 'arm' | 'activity' | 'handoff';

/** A line in the log: a chat line as shown, a live voice transcript or a blind-trial answer. */
export interface LogLine {
  at?: number; // recorded epoch seconds; old records may have no time
  key: number; // this log's own id for the line (turn ids are not unique: a user line may share one)
  role: Role;
  text: string;
  turnId?: number;
  model?: string;
  thinkMs?: number;
  labels?: string[];
  actions: ToolRun[]; // only those with something to say (a summary), as drawn
  answerStatus?: 'not-published'; // operation-absent UI status, never assistant text
  notice?: AppNotice; // producer facts outside ToolRun, never an answer or execution anchor
  event?: ActivityEvent; // an Activity line (activity.ts): a request's record, a click, a studio action, a setting...
  handoff?: HandoffLine; // a request the voice handed to Hooke's brain, as it stands (handoffs.ts)
  replayed?: boolean; // drawn from a replay and not updated live since (a hand-off's late step may never be recorded)
  calls?: number;
  update?: boolean;
  answered?: number; // the log's next key when a later model answer replaced this text (steps after it are said by it)
  research?: string;
  proactive?: boolean;
  voice?: boolean;
  speechSession?: string; // timeline scope for current Live captions
  lineId?: string; // a spoken line the server grows: updates carry the same id
  trial?: string; // arm lines: the trial and arm they answer, and the tag above them
  arm?: string;
  tag?: string;
}

/** What changed, for the DOM to draw (incrementally) and the studio to act on. */
export type Change =
  | { kind: 'clear' }
  | { kind: 'add'; line: LogLine; unread: boolean }
  | { kind: 'text'; line: LogLine }
  | { kind: 'action'; line: LogLine; index: number }
  | { kind: 'event'; line: LogLine } // an Activity line replaced by a later one with its id (running, then done)
  | { kind: 'handoff'; line: LogLine } // a hand-off's later state, or one of its steps' late receipt
  | { kind: 'tag'; line: LogLine }
  | { kind: 'hint'; text: string; error: boolean }
  | { kind: 'describe'; ids: string[] };

/** A transcript as shown: no leading punctuation left over from how speech was cut into pieces. */
export function speech(text: string | undefined): string {
  return (text ?? '').replace(/^[\s.,;:!?…-]+/, '');
}

function sentence(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Whether a voice event ends the voice's turn (its line): the voice went quiet. The user speaking over it ends
 * neither line (it may talk on through their words: live 2026-10-07 16:19); the server decides the same
 * (voice/route.py). */
export function endsVoiceTurn(event: { kind: string; data?: Record<string, unknown> | null }): boolean {
  return event.kind === 'turn_complete' && event.data?.reason !== 'user_spoke';
}

export interface LocalNoticeOptions {
  day?: string | null;
  thread?: string | null;
  /** Coalesce a repeating background condition on this connection; submitted requests are distinct. */
  deduplicate?: boolean;
}

export class ChatModel {
  readonly lines: LogLine[] = [];
  /** The conversation open now; lines tagged with another one are kept with it, not shown. */
  active: string | null = null;
  activeDay: string | null = null;
  private nextKey = 1;
  private readonly backgroundConditions = new Set<string>();
  // Browser-session notices only, never chat journal records or persisted raw errors.
  private readonly localNotices: { day: string | null; thread: string | null; at: number; text: string }[] = [];
  private heard: LogLine | null = null; // what the user is saying, while they say it
  private speaking: LogLine | null = null; // what the voice is saying, as it says it
  private heardBase = ''; // the user's line so far, up to the last final piece
  private heardAt = 0; // when each of the two live lines last got a piece (a long gap starts a new line)
  private speakingAt = 0;
  private readonly liveIds = new Set<string>(); // spoken lines this studio showed live (their echoes are not new)
  // Arms already shown, kept across resets: an arm is posted once, in the conversation it came in.
  private shownArms = new Set<string>();
  // The blind trial waiting for a vote: its answers come back after a replay (they are what the
  // user is choosing between, and a reload or another conversation must not take them away).
  private trial: Trial | null = null;
  private readonly voiceLive: () => boolean;
  private readonly now: () => number;

  /** `voiceLive`: this studio is talking now, so it already shows its own spoken lines. */
  constructor(voiceLive: () => boolean, now: () => number = Date.now) {
    this.voiceLive = voiceLive;
    this.now = now;
  }

  get empty(): boolean {
    return this.lines.length === 0;
  }

  apply(message: ServerMessage): Change[] {
    switch (message.type) {
      case 'hello':
        this.backgroundConditions.clear();
        // (Re)connected: the server replays the conversation next, so start afresh.
        return this.reset();
      case 'day':
        this.active = message.active;
        this.activeDay = message.id;
        return [];
      case 'thread': {
        // The open conversation as it was shown: replaces the log (a reload, or another
        // conversation was started or resumed). Its lines are all its own, and nothing in it
        // was shown live in this studio, so nothing is dropped and nothing becomes a hint.
        this.active = message.thread;
        this.activeDay = message.day;
        const changes = this.reset();
        for (const line of message.lines) {
          if (line.type === 'chat') changes.push(...this.chat(line, true));
          else if (line.type === 'chat-action') changes.push(...this.action(line, true));
          else if (line.type === 'app-notice') changes.push(...this.notice(line, true));
          else if (line.type === 'activity') changes.push(...this.event(line));
          else if (line.type === 'handoff') changes.push(...this.handoff(line, true));
        }
        if (this.trial) {
          const pending = this.trial;
          for (const arm of pending.arms) this.shownArms.delete(`${pending.id}:${arm.letter}`);
          changes.push(...this.arms(pending));
        }
        for (const notice of this.localNotices) {
          if (notice.day === this.activeDay && notice.thread === this.active) {
            changes.push(this.add({ role: 'system', text: notice.text, at: notice.at }));
          }
        }
        return changes;
      }
      case 'chat':
        if (this.elsewhere(message.thread) || this.otherSession(message.day)) return [];
        if (message.echo) {
          if (this.voiceLive()) {
            // Shown live already. The server's line says when a request settled or a reply ended (its
            // closing filing is not `open`), so this studio's own line ends with it: one request, one line.
            if (message.lineId) this.liveIds.add(message.lineId);
            if (!message.open && message.role === 'user') this.heard = null;
            if (!message.open && message.role === 'assistant') this.speaking = null;
            return [];
          }
          // Its line was shown live here before the voice stopped: its closing filing is not a new line.
          if (message.lineId && this.liveIds.has(message.lineId)) return [];
        }
        return this.chat(message);
      case 'chat-action':
        if (this.elsewhere(message.thread) || ('day' in message && this.otherSession(message.day))) return [];
        return this.action(message, false);
      case 'app-notice':
        return this.notice(message, false);
      case 'activity':
        return this.event(message);
      case 'handoff':
        if (this.elsewhere(message.thread) || this.otherSession(message.day)) return [];
        return this.handoff(message, false);
      default:
        return [];
    }
  }

  /** A hand-off to Hooke's brain: its first state is a row at the end of the log, under the voice line that started it
   * (the voice's next words start a line of their own below it); each later state replaces it in place. A step that a
   * late receipt settled stays settled. */
  private handoff(m: HandoffLine, replaying: boolean): Change[] {
    const existing = this.lines.find(line => line.handoff?.id === m.id);
    if (existing) {
      const before = existing.handoff?.steps ?? [];
      const steps = (m.steps ?? []).map(step => before.find(b => b.ok !== null && step.ok === null && b.requestId
        && b.requestId === step.requestId && b.actionId === step.actionId) ?? step);
      existing.handoff = { ...m, ...(m.steps ? { steps } : {}) };
      if (typeof m.turnId === 'number') existing.turnId = m.turnId;
      if (!replaying) existing.replayed = false;
      return [{ kind: 'handoff', line: existing }];
    }
    if (!replaying) this.speaking = null;
    return [this.add({ role: 'handoff', text: '', at: m.at, handoff: { ...m, steps: m.steps ? [...m.steps] : undefined },
      ...(typeof m.turnId === 'number' ? { turnId: m.turnId } : {}), replayed: replaying }, !replaying)];
  }

  /** An Activity line of this conversation and day: a later one with the same id replaces it in place. It is not a
   * chat post: it never marks the chat unread and never becomes an answer. */
  private event(m: ActivityEvent): Change[] {
    if (this.elsewhere(m.thread) || (this.activeDay && m.day && m.day !== this.activeDay)) return [];
    const existing = this.lines.find(line => line.event?.id === m.id);
    if (existing) {
      existing.event = m;
      existing.at = m.at;
      return [{ kind: 'event', line: existing }];
    }
    const turnId = m.kind === 'request' && typeof m.ref?.turnId === 'number' ? m.ref.turnId : undefined;
    return [this.add({ role: 'activity', text: '', at: m.at, event: m, turnId })];
  }

  /** A line the server says was filed in another session (its sid: opaque, compared whole). Another session's
   * conversations have their own t1, t2...: a late line of theirs must not show in this one's. */
  private otherSession(day: string | null | undefined): boolean {
    return !!day && !!this.activeDay && day !== this.activeDay;
  }

  /** A line with no conversation is the open one's (servers before conversations). */
  private elsewhere(thread: string | null | undefined): boolean {
    return !!thread && !!this.active && thread !== this.active;
  }

  private reset(): Change[] {
    this.lines.length = 0;
    this.heard = null;
    this.speaking = null;
    this.heardBase = '';
    return [{ kind: 'clear' }];
  }

  private add(fields: Omit<LogLine, 'key' | 'actions'> & { actions?: ToolRun[] }, unread = false): Extract<Change, { kind: 'add' }> {
    const line: LogLine = { ...fields, actions: fields.actions ?? [], key: this.nextKey++ };
    const before = line.speechSession && line.at !== undefined
      ? this.lines.findIndex(other => other.speechSession === line.speechSession && other.at !== undefined && other.at > line.at!) : -1;
    if (before < 0) this.lines.push(line);
    else this.lines.splice(before, 0, line);
    return { kind: 'add', line, unread };
  }

  /** `replaying`: redrawn from the server, not new: never marks the chat unread. */
  private chat(m: ChatLine, replaying = false): Change[] {
    const changes: Change[] = [];
    const grown = m.lineId ? this.lines.find((l) => l.lineId === m.lineId) : undefined;
    if (grown) {
      // A spoken line the server has grown (or finished): the same line, new text.
      grown.text = speech(m.text);
      return [{ kind: 'text', line: grown }];
    }
    if (m.role === 'assistant' && m.objectIds?.length) changes.push({ kind: 'describe', ids: m.objectIds });
    changes.push(this.add({
      at: m.at, role: m.role, text: m.voice ? speech(m.text) : m.text, turnId: m.turnId, model: m.model, thinkMs: m.thinkMs,
      labels: m.labels, actions: (m.actions ?? []).filter((a) => a.summary), update: m.update, research: m.research,
      proactive: m.proactive, voice: m.voice, lineId: m.lineId, speechSession: m.speechSession,
    }, !replaying && m.role !== 'user'));
    return changes;
  }

  private notice(m: AppNotice, replaying: boolean): Change[] {
    if (this.elsewhere(m.thread) || (this.activeDay && m.day !== this.activeDay)) return [];
    // The same filed notice told twice (its live line beside the conversation's replay, as when a session is opened
    // and its resume line is filed while the replay is sent) is one line.
    const same = JSON.stringify(m);
    if (this.lines.some((line) => line.notice && JSON.stringify(line.notice) === same)) return [];
    // Append the actual event. The producer's origin IDs do not attach activity
    // to a model answer, merge events or create ToolRuns for rendering.
    return [this.add({ role: 'activity', text: '', at: m.at, notice: m }, !replaying)];
  }

  /** A tool of an earlier answer finished: the running action of that tool in that answer
   * updates. One whose answer is not in the log is said as a hint, but not while replaying:
   * a replay only redraws what was already said. */
  private action(m: ChatAction | ChatAnswerStatus, replaying: boolean): Change[] {
    // A hand-off's step that finished after the brain answered: its row settles from this receipt (by occurrence).
    const settled: Change[] = [];
    if (!('answerStatus' in m) && m.requestId && m.actionId) {
      const { type: _type, turnId: _turn, thread: _thread, answer: _answer, standalone: _standalone, ...run } = m;
      for (const line of this.lines) {
        const steps = line.handoff?.steps;
        const index = steps?.findIndex(step => step.requestId === m.requestId && step.actionId === m.actionId) ?? -1;
        if (!steps || index < 0) continue;
        steps[index] = run;
        if (!replaying) line.replayed = false;
        settled.push({ kind: 'handoff', line });
      }
    }
    return [...this.receipt(m, replaying), ...settled];
  }

  private receipt(m: ChatAction | ChatAnswerStatus, replaying: boolean): Change[] {
    if ('answerStatus' in m) {
      return [this.add({ role: 'activity', text: '', turnId: m.turnId, answerStatus: m.answerStatus,
        calls: m.calls, model: m.model }, !replaying)];
    }
    if (m.answer) {
      if (m.requestId && m.actionId && !this.lines.some(line => line.turnId === m.turnId &&
        line.actions.some(action => action.requestId === m.requestId && action.actionId === m.actionId))) {
        return []; // supplied occurrence belongs to no retained operation in this turn
      }
      const line = this.lines.find((l) => l.role === 'assistant' && l.turnId === m.turnId);
      if (line) {
        line.text = m.answer;
        line.answered = this.nextKey;
        return [{ kind: 'text', line }]; // its tool receipt was delivered separately and remains intact
      }
      // This answer really came from the model after a turn that initially had
      // only operation activity. Its receipt remains activity; neither creates
      // a placeholder assistant message while interpretation is unavailable.
      if (this.lines.some(l => l.role === 'activity' && l.turnId === m.turnId)) {
        return [this.add({ role: 'assistant', text: m.answer, turnId: m.turnId }, !replaying)];
      }
      return replaying ? [] : [{ kind: 'hint', text: m.answer, error: m.ok === false }];
    }
    const { type: _type, turnId: _turn, thread: _thread, answer: _answer, standalone: _standalone, ...run } = m;
    for (const line of this.lines) {
      const qualified = !!m.requestId && !!m.actionId;
      // A step with its own identity is updated wherever it was filed (a spoken request's steps may be filed under
      // the turn that did them, hands' or the hand-off's); one without is matched within its answer's turn.
      if (!qualified && line.turnId !== m.turnId) continue;
      const index = line.actions.findIndex(a => qualified
        ? a.requestId === m.requestId && a.actionId === m.actionId
        : !m.standalone && !a.requestId && !a.actionId && a.tool === m.tool && a.ok === null);
      if (index < 0) continue;
      line.actions[index] = run;
      return [{ kind: 'action', line, index }];
    }
    if (m.standalone && m.summary) {
      return [this.add({ role: 'activity', text: '', turnId: m.turnId, at: m.at, actions: [run] }, !replaying)];
    }
    if (replaying || !m.summary) return [];
    // A failure with no answer line of its own goes to Activity, never a toast of its raw summary (2026-10-11).
    if (m.ok === false) return [this.add({ role: 'activity', text: '', turnId: m.turnId, at: m.at, actions: [run] }, false)];
    return [{ kind: 'hint', text: sentence(m.summary), error: false }];
  }

  /** A timestamped local notice, retained across replay in its original conversation. */
  system(text: string, options: LocalNoticeOptions = {}): Change[] {
    const day = options.day === undefined ? this.activeDay : options.day;
    const thread = options.thread === undefined ? this.active : options.thread;
    const at = this.now() / 1000;
    if (options.deduplicate) {
      const condition = JSON.stringify([day, thread, text]);
      if (this.backgroundConditions.has(condition)) return [];
      this.backgroundConditions.add(condition);
      if (this.backgroundConditions.size > 100) this.backgroundConditions.delete(this.backgroundConditions.values().next().value!);
    }
    this.localNotices.push({ day, thread, at, text });
    if (this.localNotices.length > 100) this.localNotices.shift();
    if (day !== this.activeDay || thread !== this.active) return [];
    return [this.add({ role: 'system', text, at }, true)];
  }

  /** What the user is saying: interim transcripts replace each other until the final one, and the
   * final pieces of one request add to one line (it closes when the voice's turn ends, or after a gap). */
  heardSpeech(text: string | undefined, interim: boolean): Change[] {
    const changes: Change[] = [];
    const now = this.now();
    if (this.heard && now - this.heardAt > SPEECH_LINE_GAP_MS) this.heard = null;
    if (!this.heard) {
      const added = this.add({ role: 'user', text: '', voice: true });
      this.heard = added.line;
      this.heardBase = '';
      changes.push(added);
    }
    const piece = speech(text);
    this.heard.text = this.heardBase && piece ? `${this.heardBase} ${piece}` : this.heardBase || piece;
    if (!interim) this.heardBase = this.heard.text;
    this.heardAt = now;
    changes.push({ kind: 'text', line: this.heard });
    return changes; // the voice's line, if it is being said, goes on: the two interleave
  }

  /** What the voice is saying, as it says it: pieces add to one line until its turn ends. */
  saidSpeech(text: string | undefined): Change[] {
    const changes: Change[] = [];
    const now = this.now();
    if (this.speaking && now - this.speakingAt > SPEECH_LINE_GAP_MS) this.speaking = null;
    if (!this.speaking) {
      const added = this.add({ role: 'assistant', text: '', voice: true });
      this.speaking = added.line;
      changes.push(added);
    }
    this.speaking.text = speech(this.speaking.text + (text ?? ''));
    this.speakingAt = now;
    changes.push({ kind: 'text', line: this.speaking });
    return changes;
  }

  /** The voice finished (it went quiet; the user talking over it does not end it): what is said
   * next, by either side, is a new line. */
  turnEnd(): void {
    this.speaking = null;
    this.heard = null;
  }

  /** The voice stopped: nothing more arrives for the lines being spoken. */
  voiceStopped(): void {
    this.heard = null;
    this.speaking = null;
  }

  /** The trial was dropped without a vote (the test moved on or was switched off). */
  endTrial(): void {
    this.trial = null;
  }

  /** A blind trial's answers, each posted once as it arrives (outline trials answer on the view). */
  arms(trial: Trial): Change[] {
    if (trial.kind !== 'ask') return [];
    this.trial = trial;
    const changes: Change[] = [];
    for (const arm of trial.arms) {
      const key = `${trial.id}:${arm.letter}`;
      if (!arm.done || this.shownArms.has(key)) continue;
      this.shownArms.add(key);
      changes.push(this.add({
        role: 'arm', trial: trial.id, arm: arm.letter, tag: `Answer ${arm.letter}`,
        text: arm.failed ? 'This option failed to answer.' : arm.answer || 'No answer.',
      }));
    }
    return changes;
  }

  /** After the vote, each answer's tag says which option it was. */
  reveal(trialId: string, reveal: Record<string, { label: string }>, winner: string): Change[] {
    if (this.trial?.id === trialId) this.trial = null; // voted: nothing waits any more
    const changes: Change[] = [];
    for (const line of this.lines) {
      const r = line.role === 'arm' && line.trial === trialId ? reveal[line.arm ?? ''] : undefined;
      if (!r) continue;
      line.tag = `Answer ${line.arm}, ${r.label}${line.arm === winner ? ' (your pick)' : ''}`;
      changes.push({ kind: 'tag', line });
    }
    return changes;
  }
}
