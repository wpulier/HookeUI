// The playhead for test footage: where the recording is, between the bridge's polls, and the time in
// words. The bridge says where it was when asked (GET /feed `playback`); the recording plays on in
// real time and loops, so the position is that plus the time since, wrapped at its length.

export interface Playback {
  seconds: number; // where it was when the bridge answered
  duration: number;
  paused?: boolean; // held: the picture and the position stand still
}

/** Where the recording is `elapsedMs` after the bridge said `playback` (it loops). */
export function positionAt(playback: Playback, elapsedMs: number): number {
  if (!(playback.duration > 0)) return 0;
  const at = playback.seconds + (playback.paused ? 0 : Math.max(0, elapsedMs) / 1000);
  return ((at % playback.duration) + playback.duration) % playback.duration;
}

/** "0:42", "3:43", "37:57", "1:02:05". */
export function clock(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  const h = Math.floor(whole / 3600);
  const m = Math.floor((whole % 3600) / 60);
  const s = whole % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`;
}
