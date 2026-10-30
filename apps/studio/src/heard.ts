// The sign it heard you (the live-partner spec, decision 1): within 0.3 s of the talk key's release, a subtle sound or
// a visual cue, never a canned word. The server sends `{type:"heard",n}` as a released turn begins (voice/partner.py
// Partner.heard); the studio settles the hold ring (marks.css) and, with HEARD_CUE "tone" (the default), plays a very
// soft short tone. Once per turn, and never for a turn older than the newest one begun.
import type { HeardMessage } from './protocol';

/** HEARD_CUE (options.py): "tone" is the ring and a soft tone, "ring" the ring alone. */
export type HeardCue = 'tone' | 'ring';
export function heardCue(value: unknown): HeardCue {
  return value === 'ring' ? 'ring' : 'tone';
}

/** The ring's settle: the dot eases to a soft filled state and the ring fades (marks.css `talk-heard-*`). */
export const SETTLE_MS = 260;
/** After a release the ring stays this long as sent, its words faded, waiting for `heard`; then it goes. */
export const SENT_WAIT_MS = 600;

type Timers = { setTimer?: (fn: () => void, ms: number) => unknown; clearTimer?: (timer: unknown) => void };

/** The hold ring after a release (main.ts renderTalk draws it): kept a moment as sent, settled once heard, then gone. A
 * hold always takes precedence over it. */
export class HeardRing {
  state: 'sent' | 'heard' | null = null;
  private timer: unknown = null;
  private readonly render: () => void;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (timer: unknown) => void;

  constructor(options: { render: () => void } & Timers) {
    this.render = options.render;
    this.setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
  }

  /** A said turn was let go: the ring stays as sent until heard, or SENT_WAIT_MS. */
  released(): void { this.enter('sent', SENT_WAIT_MS); }

  /** The server heard the turn: the ring settles, then goes. */
  settle(): void { this.enter('heard', SETTLE_MS); }

  /** A new press, a cancel or Talk ended: nothing after a release is shown any more. */
  clear(): void {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.timer = null;
    this.state = null;
  }

  private enter(state: 'sent' | 'heard', ms: number): void {
    if (this.timer !== null) this.clearTimer(this.timer);
    this.state = state;
    this.timer = this.setTimer(() => { this.timer = null; this.state = null; this.render(); }, ms);
    this.render();
  }
}

/** The few parts of an AudioContext the tone uses. */
export interface ToneContext {
  readonly state: string;
  readonly currentTime: number;
  readonly destination: object;
  createOscillator(): { type: string; frequency: { setValueAtTime(v: number, t: number): unknown; exponentialRampToValueAtTime(v: number, t: number): unknown };
    connect(node: object): unknown; start(t: number): void; stop(t: number): void; disconnect(): void; onended: unknown };
  createGain(): { gain: { setValueAtTime(v: number, t: number): unknown; linearRampToValueAtTime(v: number, t: number): unknown;
    exponentialRampToValueAtTime(v: number, t: number): unknown }; connect(node: object): unknown; disconnect(): void };
  resume?(): Promise<void>;
}

/** The tone: a sine just under 660 Hz falling a little, 80 ms, its gain at most TONE_GAIN with a 12 ms attack and an
 * exponential release, so it is felt more than heard over the voice. */
export const TONE_MS = 80;
export const TONE_GAIN = 0.05;
export function playHeardTone(context: ToneContext): boolean {
  if (context.state !== 'running') return false;
  const t = context.currentTime, end = t + TONE_MS / 1000;
  const oscillator = context.createOscillator(), gain = context.createGain();
  oscillator.type = 'sine';
  oscillator.frequency.setValueAtTime(660, t);
  oscillator.frequency.exponentialRampToValueAtTime(590, end);
  gain.gain.setValueAtTime(0.0001, t);
  gain.gain.linearRampToValueAtTime(TONE_GAIN, t + 0.012);
  gain.gain.exponentialRampToValueAtTime(0.0001, end);
  oscillator.connect(gain);
  gain.connect(context.destination);
  oscillator.onended = () => { oscillator.disconnect(); gain.disconnect(); };
  oscillator.start(t);
  oscillator.stop(end + 0.01);
  return true;
}

/** Where the tone plays: the voice's own speaker context while Talk is on (voice.ts `cueContext`), else one made the
 * first time it is needed (after the key press that started the turn). A context still suspended is resumed and the
 * tone played only if it starts at once, so a sign never sounds late. */
export class HeardTone {
  private own: ToneContext | null = null;
  private readonly shared: () => ToneContext | null;
  private readonly make: () => ToneContext | null;
  private readonly now: () => number;

  constructor(options: { shared: () => ToneContext | null; make?: () => ToneContext | null; now?: () => number }) {
    this.shared = options.shared;
    this.make = options.make ?? (() => typeof AudioContext === 'function' ? new AudioContext() : null);
    this.now = options.now ?? (() => performance.now());
  }

  play(): void {
    let context = this.shared();
    if (!context || context.state !== 'running') {
      try { this.own ??= this.make(); } catch { this.own = null; }
      context = this.own;
    }
    if (!context) return;
    if (playHeardTone(context)) return;
    const asked = this.now(), waiting = context;
    void waiting.resume?.().then(() => { if (this.now() - asked < 150) playHeardTone(waiting); }).catch(() => {});
  }
}

/** Answers `heard`: the ring settles and, with "tone", the tone plays, once for each turn; a `heard` for a turn older
 * than the newest begun (the user is already on the next one) is ignored. True when the sign was shown. */
export function heardHandler(options: { turn: () => number; cue: () => HeardCue; settle: () => void; tone: () => void }):
  (message: HeardMessage) => boolean {
  let last = -Infinity;
  return (message) => {
    const n = Number(message.n);
    if (!Number.isFinite(n) || n <= last || n < options.turn()) return false;
    last = n;
    options.settle();
    if (options.cue() === 'tone') options.tone();
    return true;
  };
}
