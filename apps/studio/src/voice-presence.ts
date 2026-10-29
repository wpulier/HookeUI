// The voice's state, always in sight and true (W3 "alive and available"; the live-partner spec 4.8, horizontal 2). Will,
// live, 29 Oct: after the voice said "the video search is still running", "it just paused. No indication anywhere the
// LIVE model was thinking or working"; "if it's thinking, if Hooke brain is doing something while it talks, that's
// gotta be clear"; and "if the assistant tab is closed, somewhere it should be apparent the assistant is still live".
//
// The state is the server's (services/orchestrator/voice/presence.py `voice-state`: listening, heard, thinking,
// speaking, working with its hand-offs and jobs, idle), as chat's `thinking` is; the one thing known here first is the
// user's own key held (listening). Hooke's work in flight is counted from one list, the strip over the composer
// (handoffs.ts FlightStrip: the hand-off rows, this Project's researchers at work and the server's own jobs), so
// "Working · 2" says what that strip and the Working strip list. It shows:
//   - beside Talk: the state mark and the status line ("Thinking", "Speaking · 2 working", "Hooke is working · 2");
//   - in the Assistant header, by its title: a compact chip ("Thinking", "Working · 2", "Live");
//   - with the Assistant closed and Talk on: the presence pill, first in the Working strip ("Hooke · thinking", "Hooke ·
//     working · 2", "Hooke · live"); a click opens the Assistant again.
// A voice that sends no state (GPT-Live) is shown from its own events (processing, speaking, a tool call), never from a
// timer. `document.body` carries `data-voice-state` and `data-voice-working`: what is shown, for the journeys' bench.

import type { VoiceStateMessage } from './protocol';
import type { StripPill } from './working-strip';
import { setVoiceMark, voiceMark, type ShownVoice } from './voice-mark';

export type { ShownVoice } from './voice-mark';
export type TalkState = 'off' | 'connecting' | 'live' | 'error';
/** A voice without the server's state: what its own events say it is doing. */
export type FallbackPhase = 'listening' | 'processing' | 'speaking' | 'tool' | null;

export interface PresenceInput {
  talk: TalkState;
  /** A turn held now: the user's own key, known here before the server says it. */
  held: boolean;
  /** The server's last state in this Talk session (null before its first, and for a voice that sends none). */
  server: VoiceStateMessage | null;
  fallback?: FallbackPhase;
  /** Hooke's work in flight, as the strip over the composer lists it (handoffs.ts FlightStrip.working). */
  working: number;
}

export interface PresenceView {
  state: ShownVoice;
  /** Work in flight. */
  n: number;
  /** The header chip's words ("Thinking", "Working · 2", "Live"). */
  chip: string;
  /** The presence pill's words after "Hooke" ("thinking", "working · 2", "live"). */
  pill: string;
  /** The status line's words beside Talk; empty: the caller's own (hold to talk, connecting). */
  status: string;
  /** Work in flight beside an active state ("2 working"); empty otherwise. */
  also: string;
  /** What a screen reader hears. */
  label: string;
}

const ACTIVE: ReadonlySet<ShownVoice> = new Set(['listening', 'heard', 'thinking', 'speaking']);
const CHIP: Record<ShownVoice, string> = { off: '', connecting: 'Connecting', listening: 'Listening', heard: 'Heard you',
  thinking: 'Thinking', speaking: 'Speaking', working: 'Working', idle: 'Live' };
const STATUS: Record<ShownVoice, string> = { off: '', connecting: '', listening: '', heard: 'Heard you', thinking: 'Thinking',
  speaking: 'Speaking', working: 'Hooke is working', idle: '' };
const SAID: Record<ShownVoice, string> = { off: 'Talk is off', connecting: 'Connecting to the voice', listening: 'Listening',
  heard: 'Heard you', thinking: 'Thinking', speaking: 'Speaking', working: 'Hooke is working', idle: 'Live and ready' };

/** What is shown now: Talk's own state first (off, connecting), then the user's key held, then the server's state (or a
 * voice's own events), with Hooke's work in flight beside it. */
export function presenceView(input: PresenceInput): PresenceView {
  const n = Math.max(0, input.working, input.server?.n ?? 0);
  let state: ShownVoice;
  if (input.talk === 'off' || input.talk === 'error') state = 'off';
  else if (input.talk === 'connecting') state = 'connecting';
  else if (input.held) state = 'listening';
  else {
    const said = input.server?.state;
    const own: ShownVoice = said && said !== 'off' ? said
      : input.fallback === 'processing' || input.fallback === 'tool' ? 'thinking'
        : input.fallback === 'speaking' ? 'speaking' : 'idle';
    state = own === 'working' || own === 'idle' ? (n > 0 ? 'working' : 'idle') : own;
  }
  const busy = state === 'working';
  const also = n > 0 && ACTIVE.has(state) ? `${n} working` : '';
  const count = busy ? ` · ${n}` : '';
  const chip = CHIP[state] ? `${CHIP[state]}${count}` : '';
  const pill = chip ? `${CHIP[state].toLowerCase()}${count}` : '';
  const status = STATUS[state] ? `${STATUS[state]}${count}` : '';
  const things = n === 1 ? '1 piece of work in flight' : `${n} pieces of work in flight`;
  const label = state === 'off' ? SAID.off : `Hooke: ${SAID[state]}${n > 0 ? `. ${things}` : ''}`;
  return { state, n, chip, pill, status, also, label };
}

export interface PresenceHooks {
  talk(): TalkState;
  held(): boolean;
  /** Hooke's work in flight, as the strip over the composer lists it. */
  working(): number;
  /** Whether the Assistant itself shows now. */
  assistantShown(): boolean;
  /** Opens the Assistant again (the presence pill's click). */
  openAssistant(): void;
  /** The Working strip's source for the presence pill. */
  sink(pills: StripPill[]): void;
  /** The Assistant header, where the chip goes after its title (found when there is one). */
  header?(): HTMLElement | null;
  /** Talk: `data-working` while Hooke works or the voice thinks. */
  talkButton?: HTMLElement | null;
  /** Called after each render with what is shown (main.ts writes the status line from it). */
  rendered?(view: PresenceView): void;
}

/** The pill's id in the Working strip. */
export const PRESENCE_PILL = 'voice:presence';

/** The presence pill: "Hooke" and its state, first in the strip, no × (it is the voice, not a piece of work); a click
 * opens the Assistant. Shown only while Talk is on and the Assistant is closed. */
export function presencePill(view: PresenceView, open: () => void, since: number): StripPill | null {
  if (view.state === 'off' || !view.pill) return null;
  return {
    id: PRESENCE_PILL, name: 'Hooke', cell: view.state === 'working' ? 'working' : 'idle', urgency: 1, since, words: view.pill,
    voice: view.state, lead: true, fixed: true,
    title: `${view.label}.\nOpen the Assistant`, hide: '', open, dismiss: () => {},
  };
}

export class VoicePresence {
  /** The mark beside Talk's status line. */
  readonly mark = voiceMark('off', 8);
  /** The chip in the Assistant header. */
  readonly chip: HTMLSpanElement;
  private readonly chipMark = voiceMark('off', 8);
  private readonly chipWords: HTMLSpanElement;
  private readonly hooks: PresenceHooks;
  private server: VoiceStateMessage | null = null;
  private fallback: FallbackPhase = null;
  private since = Date.now();
  private signature = '';
  view: PresenceView = presenceView({ talk: 'off', held: false, server: null, working: 0 });

  constructor(hooks: PresenceHooks) {
    this.hooks = hooks;
    this.chip = document.createElement('span');
    this.chip.className = 'voice-chip';
    this.chip.setAttribute('role', 'status');
    this.chip.hidden = true;
    this.chipWords = document.createElement('span');
    this.chipWords.className = 'voice-chip-words';
    this.chip.append(this.chipMark, this.chipWords);
  }

  /** The server's state: a newer one replaces the last; an older one (a late send) changes nothing. */
  serverState(message: VoiceStateMessage): void {
    if (this.server && Number(message.seq) <= Number(this.server.seq)) return;
    this.server = message;
    this.render();
  }

  /** A voice that sends no state: what its own events say. */
  setFallback(phase: FallbackPhase): void {
    if (this.fallback === phase) return;
    this.fallback = phase;
    this.render();
  }

  /** Talk ended or a new Talk session began: nothing the last one said is kept. */
  reset(): void {
    this.server = null;
    this.fallback = null;
    this.render();
  }

  /** Whether the server has said a state in this Talk session. */
  get told(): boolean { return this.server !== null; }

  render(): PresenceView {
    const talk = this.hooks.talk();
    const view = presenceView({ talk, held: this.hooks.held(), server: this.server, fallback: this.fallback,
      working: this.hooks.working() });
    if (view.state !== this.view.state) this.since = Date.now();
    this.view = view;
    const body = document.body;
    if (body) {
      if (body.dataset.voiceState !== view.state) body.dataset.voiceState = view.state;
      if (body.dataset.voiceWorking !== String(view.n)) body.dataset.voiceWorking = String(view.n);
    }
    setVoiceMark(this.mark, view.state);
    this.mark.dataset.also = view.also ? 'true' : 'false';
    this.mountChip();
    this.chip.hidden = view.state === 'off';
    setVoiceMark(this.chipMark, view.state);
    this.chip.dataset.state = view.state;
    const words = view.also ? `${view.chip} · ${view.also}` : view.chip;
    if (this.chipWords.textContent !== words) this.chipWords.textContent = words;
    if (this.chip.getAttribute('aria-label') !== view.label) { this.chip.setAttribute('aria-label', view.label); this.chip.title = view.label; }
    const button = this.hooks.talkButton;
    if (button) {
      if (view.state === 'working' || view.state === 'thinking' || view.state === 'heard') button.dataset.working = '';
      else delete button.dataset.working;
    }
    const shown = this.hooks.assistantShown();
    const pill = !shown ? presencePill(view, () => this.hooks.openAssistant(), this.since) : null;
    const signature = pill ? JSON.stringify([pill.words, pill.voice, pill.title]) : '';
    if (signature !== this.signature) { this.signature = signature; this.hooks.sink(pill ? [pill] : []); }
    this.hooks.rendered?.(view);
    return view;
  }

  private mountChip(): void {
    if (this.chip.isConnected ?? this.chip.parentElement) return;
    const title = this.hooks.header?.()?.querySelector('h2');
    if (title) title.after(this.chip);
  }
}
