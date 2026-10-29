// Hold to talk (the one-brain voice with TALK "hold"; the partner contract, amendment 10): the talk key held anywhere
// in the window, or the Talk button held. Pressed starts a turn, released sends it, Escape while held drops it.
//
// Keys are read on the window in the capture phase, so the hold works while a text field has focus and before any
// panel's own key handling. Auto-repeat is ignored. Another key pressed during the hold is a chord (a VoiceOver
// command, Option+e for é), never a turn, so it cancels; a tap shorter than TAP_MS cancels too, which makes a tap the
// way to stop the voice mid-sentence without asking anything. Losing the window (another app in front) ends the hold
// as a release: what was said is sent.
//
// Control+Option is VoiceOver's modifier, so with Talk off the key connects only once it has been held alone ARM_MS
// (heldAlone), and a chord or Escape after that drops the connection the hold started (TalkTurns): a VoiceOver command
// never opens the microphone or a paid call. The Talk button, an explicit press, connects at once.

export type TalkKey = 'control-option' | 'right-option';
export type HoldVia = 'key' | 'button';

/** A press shorter than this is a tap: it stops the voice and sends nothing (an empty turn would get an answer). */
export const TAP_MS = 250;
/** The talk key held alone this long, with Talk off, connects it. */
export const ARM_MS = 300;
export type CancelWhy = 'escape' | 'chord' | 'tap';

/** The key's name as the settings and the status say it. */
export function talkKeyWords(key: TalkKey): string {
  return key === 'right-option' ? 'Right Option' : 'Control+Option';
}

export function talkKey(value: unknown): TalkKey {
  return value === 'right-option' ? 'right-option' : 'control-option';
}

type KeyLike = Pick<KeyboardEvent, 'key' | 'code' | 'repeat' | 'ctrlKey' | 'altKey' | 'metaKey' | 'shiftKey' | 'preventDefault' | 'stopPropagation'>;

/** Whether this keydown completes the talk key (and nothing else is held). */
export function pressesTalkKey(event: KeyLike, key: TalkKey): boolean {
  if (event.metaKey || event.shiftKey) return false;
  if (key === 'right-option') return event.code === 'AltRight' && !event.ctrlKey;
  return (event.key === 'Control' || event.key === 'Alt') && event.ctrlKey && event.altKey;
}

/** Whether this keyup lets go of the talk key. */
export function releasesTalkKey(event: KeyLike, key: TalkKey): boolean {
  if (key === 'right-option') return event.code === 'AltRight';
  return event.key === 'Control' || event.key === 'Alt';
}

const MODIFIERS = new Set(['Control', 'Alt', 'Shift', 'Meta', 'CapsLock', 'Fn', 'AltGraph', 'OS']);

export interface TalkHoldOptions {
  /** Whether holding means a turn now (the one-brain voice in hold mode). Asked at each press. */
  enabled: () => boolean;
  key: () => TalkKey;
  press: (via: HoldVia) => void;
  /** Held alone ARM_MS (the key), or at once (the button): only then may Talk be connected for it. */
  heldAlone?: (via: HoldVia) => void;
  release: () => void;
  cancel: (why: CancelWhy) => void;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (timer: unknown) => void;
}

type Listening = Pick<Window, 'addEventListener' | 'removeEventListener'>;

export class TalkHold {
  /** What is holding now, or null. */
  held: HoldVia | null = null;
  private pressedAt = 0;
  private pointer: number | null = null;
  private arming: unknown = null;
  private readonly options: TalkHoldOptions;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => unknown;
  private readonly clearTimer: (timer: unknown) => void;

  constructor(options: TalkHoldOptions) {
    this.options = options;
    this.now = options.now ?? (() => performance.now());
    this.setTimer = options.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
    this.clearTimer = options.clearTimer ?? ((timer) => clearTimeout(timer as ReturnType<typeof setTimeout>));
  }

  /** Listen on the window (capture phase) for the key, Escape and losing the window. Returns how to stop listening. */
  attach(win: Listening, doc?: Pick<Document, 'addEventListener' | 'removeEventListener' | 'visibilityState'>): () => void {
    const down = (event: Event) => this.keydown(event as KeyboardEvent);
    const up = (event: Event) => this.keyup(event as KeyboardEvent);
    const blur = () => this.end();
    const hidden = () => { if (doc?.visibilityState === 'hidden') this.end(); };
    win.addEventListener('keydown', down, true);
    win.addEventListener('keyup', up, true);
    win.addEventListener('blur', blur);
    doc?.addEventListener('visibilitychange', hidden);
    return () => {
      win.removeEventListener('keydown', down, true);
      win.removeEventListener('keyup', up, true);
      win.removeEventListener('blur', blur);
      doc?.removeEventListener('visibilitychange', hidden);
    };
  }

  /** Holding the Talk button: pointer down presses, up (or the pointer lost) releases. */
  attachButton(button: Pick<HTMLElement, 'addEventListener'> & Partial<Pick<HTMLElement, 'setPointerCapture'>>): void {
    button.addEventListener('pointerdown', (event) => {
      const e = event as PointerEvent;
      if (e.button !== 0 || this.held || !this.options.enabled()) return;
      this.pointer = e.pointerId;
      try { button.setPointerCapture?.(e.pointerId); } catch { /* the pointer is already gone */ }
      this.press('button');
    });
    const up = (event: Event) => {
      if (this.held !== 'button' || (event as PointerEvent).pointerId !== this.pointer) return;
      this.end();
    };
    button.addEventListener('pointerup', up);
    button.addEventListener('pointercancel', up);
    button.addEventListener('lostpointercapture', up);
  }

  keydown(event: KeyLike): void {
    if (event.repeat) return;
    if (this.held) {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation(); // Escape drops the turn; it closes nothing else
        this.cancel('escape');
        return;
      }
      // Another key while the talk key is held is a chord, not a turn (a VoiceOver command, Option+e).
      if (this.held === 'key' && !MODIFIERS.has(event.key) && event.code !== 'AltRight') this.cancel('chord');
      return;
    }
    if (!pressesTalkKey(event, this.options.key()) || !this.options.enabled()) return;
    this.press('key');
  }

  keyup(event: KeyLike): void {
    if (this.held === 'key' && releasesTalkKey(event, this.options.key())) this.end();
  }

  private press(via: HoldVia): void {
    this.held = via;
    this.pressedAt = this.now();
    this.options.press(via);
    if (via === 'button') { this.options.heldAlone?.(via); return; }
    this.arming = this.setTimer(() => { this.arming = null; if (this.held === via) this.options.heldAlone?.(via); }, ARM_MS);
  }

  private disarm(): void {
    if (this.arming !== null) this.clearTimer(this.arming);
    this.arming = null;
  }

  /** Let go: a turn, or a tap (which only stops the voice). */
  end(): void {
    if (!this.held) return;
    const tap = this.now() - this.pressedAt < TAP_MS;
    this.held = null; this.pointer = null; this.disarm();
    if (tap) this.options.cancel('tap'); else this.options.release();
  }

  cancel(why: CancelWhy = 'escape'): void {
    if (!this.held) return;
    this.held = null; this.pointer = null; this.disarm();
    this.options.cancel(why);
  }
}

/** What Talk can be asked to do by a hold (voice.ts VoiceClient). */
export interface TurnVoice {
  readonly state: string;
  readonly talkMode?: 'hold' | 'open' | null;
  talkDown(n: number): void;
  talkUp(n: number): void;
  talkCancel(n: number): void;
  stop(): void;
}

/** Held turns over a voice session: a press starts a turn when Talk is live; a press held alone (heldAlone) connects
 * Talk when it is off, the turn starting once live if still held; a chord or Escape drops a connection the key
 * started. */
export class TalkTurns {
  turn = 0;
  /** A turn is being said now (talk down sent, not yet released). */
  open = false;
  /** Held while Talk is off or connecting: the turn starts if it is still held once live. */
  pending = false;
  private startedByKey = false;
  private readonly options: { voice: TurnVoice; start: () => void; began: (n: number) => void; changed: () => void };

  constructor(options: { voice: TurnVoice; start: () => void; began: (n: number) => void; changed: () => void }) {
    this.options = options;
  }

  press(): void {
    if (this.options.voice.state === 'live') { this.begin(); return; }
    this.pending = true;
    this.options.changed();
  }

  heldAlone(via: HoldVia): void {
    const state = this.options.voice.state;
    if (!this.pending || (state !== 'off' && state !== 'error')) return;
    this.options.start();
    this.startedByKey = via === 'key';
    this.options.changed();
  }

  /** The voice's state changed, with the key or button still held or not. */
  stateChanged(state: string, held: boolean): void {
    if (state === 'live') {
      if (this.pending && held && this.options.voice.talkMode === 'hold') this.begin();
      else if (!held) this.pending = false;
    } else if (state !== 'connecting') {
      this.pending = false; this.open = false; this.startedByKey = false;
    } else this.open = false;
  }

  release(): void {
    this.pending = false;
    this.startedByKey = false;
    if (this.open) { this.open = false; this.options.voice.talkUp(this.turn); }
    this.options.changed();
  }

  cancel(why: CancelWhy): void {
    this.pending = false;
    if (this.open) { this.open = false; this.options.voice.talkCancel(this.turn); }
    // A chord (a VoiceOver command) or Escape: the connection this key started was never wanted.
    if (this.startedByKey && why !== 'tap') this.options.voice.stop();
    this.startedByKey = false;
    this.options.changed();
  }

  private begin(): void {
    this.pending = false;
    this.open = true;
    const n = ++this.turn;
    this.options.voice.talkDown(n);
    this.options.began(n);
    this.options.changed();
  }
}
