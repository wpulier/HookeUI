// The voice's state mark (voice-presence.ts): one small sign, beside Talk's status, in the Assistant header's chip and
// on the presence pill in the Working strip. It echoes the heard cue's ring (marks.css .talk-hold-dot) and the hand-off
// row's breathing gold cell (handoffs.css): the accent dot with its halo while the voice listens, has heard, thinks or
// speaks, the gold lit cell while Hooke works, a quiet still dot while it is live and ready. Its look and motion are
// voice-presence.css; the element is drawn once and only its state changes, so its motion never restarts.

import './voice-presence.css';
import { litCell } from './lit-cell';

export type ShownVoice = 'off' | 'connecting' | 'listening' | 'heard' | 'thinking' | 'speaking' | 'working' | 'idle';

/** A mark for `state`: a span holding the dot and the gold cell; CSS shows the one the state uses. */
export function voiceMark(state: ShownVoice, size = 10): HTMLSpanElement {
  const mark = document.createElement('span');
  mark.className = 'voice-state-mark';
  mark.setAttribute('aria-hidden', 'true');
  mark.style.setProperty('--voice-mark', `${size}px`);
  const dot = document.createElement('span');
  dot.className = 'voice-state-dot';
  mark.append(dot, litCell('working', { size, decorative: true }));
  setVoiceMark(mark, state);
  return mark;
}

/** Its state now (in place: the element and its running motion are kept). */
export function setVoiceMark(mark: Element, state: ShownVoice): void {
  const el = mark as HTMLElement;
  if (el.dataset.state !== state) el.dataset.state = state;
}
