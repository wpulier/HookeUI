// The sign it heard you (heard.ts; the live-partner spec, decision 1): the ring settles on `heard`, a soft tone plays
// once per turn with HEARD_CUE "tone" and never with "ring", and a `heard` for an older turn is ignored.
import { expect, test, vi } from 'vitest';
import { HeardRing, HeardTone, SENT_WAIT_MS, SETTLE_MS, TONE_GAIN, TONE_MS, heardCue, heardHandler, playHeardTone, type ToneContext } from './heard';
import { partnerHandler } from './partner';
import { PARTNER_MESSAGES, type HeardMessage, type PartnerCommand } from './protocol';

function fakeTimers() {
  const due: { fn: () => void; ms: number }[] = [];
  return { due,
    setTimer: (fn: () => void, ms: number) => { const t = { fn, ms }; due.push(t); return t; },
    clearTimer: (timer: unknown) => { const i = due.indexOf(timer as never); if (i >= 0) due.splice(i, 1); },
    run: () => { const next = due.shift(); next?.fn(); return next?.ms; } };
}

/** A stand-in AudioContext that records every tone scheduled on it. */
function fakeAudio(state = 'running') {
  const tones: { type: string; start: number; stop: number; gains: [string, number, number][]; freqs: number[] }[] = [];
  const context: ToneContext & { state: string; resumed: number } = {
    state, currentTime: 2, destination: {}, resumed: 0,
    createOscillator() {
      const tone = { type: '', start: NaN, stop: NaN, gains: [] as [string, number, number][], freqs: [] as number[] };
      tones.push(tone);
      const osc = {
        set type(v: string) { tone.type = v; }, get type() { return tone.type; },
        frequency: { setValueAtTime: (v: number) => tone.freqs.push(v), exponentialRampToValueAtTime: (v: number) => tone.freqs.push(v) },
        connect: () => {}, disconnect: () => {}, onended: null as (() => void) | null,
        start: (t: number) => { tone.start = t; }, stop: (t: number) => { tone.stop = t; } };
      return osc;
    },
    createGain() {
      const tone = tones[tones.length - 1];
      const ramp = (kind: string) => (v: number, t: number) => tone.gains.push([kind, v, t]);
      return { gain: { setValueAtTime: ramp('set'), linearRampToValueAtTime: ramp('linear'), exponentialRampToValueAtTime: ramp('exp') },
        connect: () => {}, disconnect: () => {} };
    },
    resume: async () => { context.resumed++; },
  };
  return { context, tones };
}

test('heard is one of the partner\'s messages, and the partner handler hands it to the sign', () => {
  expect(PARTNER_MESSAGES).toContain('heard');
  const heard: HeardMessage[] = [], sent: PartnerCommand[] = [];
  const handle = partnerHandler({ reply: (c) => { sent.push(c); return true; },
    capture: { share: async () => null, compose: async () => ({ jpeg: 'x', w: 1, h: 1 }) },
    marks: { show: () => [], clear: () => {} }, heard: (m) => heard.push(m) });
  handle({ type: 'heard', n: 4 });
  expect(heard).toEqual([{ type: 'heard', n: 4 }]);
  expect(sent).toEqual([]); // nothing is answered: it is a sign, not a request
  // Without a sign wired, a heard is dropped quietly.
  expect(() => partnerHandler({ reply: () => true, capture: { share: async () => null, compose: async () => ({ jpeg: 'x', w: 1, h: 1 }) },
    marks: { show: () => [], clear: () => {} } })({ type: 'heard', n: 1 })).not.toThrow();
});

test('the setting: "ring" is the ring alone; anything else, or none, is the ring and the tone', () => {
  expect(heardCue('ring')).toBe('ring');
  expect(heardCue('tone')).toBe('tone');
  expect(heardCue(undefined)).toBe('tone');
  expect(heardCue('loud')).toBe('tone');
});

test('a heard settles the ring: kept as sent after the release, settled on heard, gone after SETTLE_MS', () => {
  const timers = fakeTimers(), render = vi.fn();
  const ring = new HeardRing({ render, ...timers });
  ring.released();
  expect(ring.state).toBe('sent');
  expect(timers.due.map((t) => t.ms)).toEqual([SENT_WAIT_MS]);
  const sign = heardHandler({ turn: () => 1, cue: () => 'ring', settle: () => ring.settle(), tone: () => {} });
  expect(sign({ type: 'heard', n: 1 })).toBe(true);
  expect(ring.state).toBe('heard');
  expect(timers.due.map((t) => t.ms)).toEqual([SETTLE_MS]); // the sent wait gave way to the settle
  expect(SETTLE_MS).toBeGreaterThanOrEqual(200); expect(SETTLE_MS).toBeLessThanOrEqual(300);
  const renders = render.mock.calls.length;
  timers.run();
  expect(ring.state).toBeNull();
  expect(render.mock.calls.length).toBe(renders + 1); // drawn once more, hidden
});

test('a release never heard leaves the ring after SENT_WAIT_MS with no settle; a new press clears it at once', () => {
  const timers = fakeTimers();
  const ring = new HeardRing({ render: () => {}, ...timers });
  ring.released();
  expect(timers.run()).toBe(SENT_WAIT_MS);
  expect(ring.state).toBeNull();
  ring.released(); ring.settle();
  ring.clear();
  expect(ring.state).toBeNull();
  expect(timers.due).toEqual([]);
});

test('the tone plays once for each turn with "tone", and never with "ring"', () => {
  const { context, tones } = fakeAudio();
  const tone = new HeardTone({ shared: () => context });
  let cue: 'tone' | 'ring' = 'tone';
  const settled: number[] = [];
  let turn = 1;
  const sign = heardHandler({ turn: () => turn, cue: () => cue, settle: () => settled.push(turn), tone: () => tone.play() });
  sign({ type: 'heard', n: 1 });
  sign({ type: 'heard', n: 1 }); // the same turn again: never twice
  expect(tones).toHaveLength(1);
  turn = 2; cue = 'ring';
  sign({ type: 'heard', n: 2 });
  expect(tones).toHaveLength(1); // the ring alone
  expect(settled).toEqual([1, 2]); // the ring settles either way
  turn = 3; cue = 'tone';
  sign({ type: 'heard', n: 3 });
  expect(tones).toHaveLength(2);
});

test('a heard for a turn older than the newest begun is ignored, and so is one after a newer heard', () => {
  const settle = vi.fn(), tone = vi.fn();
  let turn = 5;
  const sign = heardHandler({ turn: () => turn, cue: () => 'tone', settle, tone });
  expect(sign({ type: 'heard', n: 4 })).toBe(false); // the user already pressed for turn 5
  expect(settle).not.toHaveBeenCalled(); expect(tone).not.toHaveBeenCalled();
  expect(sign({ type: 'heard', n: 5 })).toBe(true);
  turn = 7;
  expect(sign({ type: 'heard', n: 7 })).toBe(true);
  expect(sign({ type: 'heard', n: 6 })).toBe(false);
  expect(sign({ type: 'heard', n: Number.NaN })).toBe(false);
  expect(settle).toHaveBeenCalledTimes(2); expect(tone).toHaveBeenCalledTimes(2);
});

test('the tone is soft and short: a sine of about 80 ms, a gentle attack and release, its gain low', () => {
  const { context, tones } = fakeAudio();
  expect(playHeardTone(context)).toBe(true);
  const [t] = tones;
  expect(t.type).toBe('sine');
  expect(t.start).toBe(2);
  const length = (t.stop - t.start) * 1000;
  expect(length).toBeGreaterThanOrEqual(60); expect(length).toBeLessThanOrEqual(95);
  expect(TONE_MS).toBeGreaterThanOrEqual(60); expect(TONE_MS).toBeLessThanOrEqual(90);
  const peak = Math.max(...t.gains.map(([, v]) => v));
  expect(peak).toBe(TONE_GAIN); expect(peak).toBeLessThanOrEqual(0.08);
  expect(t.gains[0]).toEqual(['set', 0.0001, 2]); // starts from silence, no click
  expect(t.gains[1][0]).toBe('linear'); expect(t.gains[1][2] - 2).toBeGreaterThan(0.005); // an attack, not a step
  expect(t.gains[2]).toEqual(['exp', 0.0001, 2 + TONE_MS / 1000]); // released to silence
});

test('the voice\'s speaker context plays it; without one, one is made the first time, and a suspended one never sounds late', async () => {
  const shared = fakeAudio();
  const made = vi.fn(() => own.context);
  const own = fakeAudio('suspended');
  let speaker: ToneContext | null = shared.context;
  let clock = 0;
  const tone = new HeardTone({ shared: () => speaker, make: made, now: () => clock });
  tone.play();
  expect(shared.tones).toHaveLength(1); expect(made).not.toHaveBeenCalled();
  speaker = null; // Talk's speaker gone
  tone.play();
  expect(made).toHaveBeenCalledTimes(1);
  expect(own.context.resumed).toBe(1);
  expect(own.tones).toHaveLength(0); // suspended: nothing scheduled to sound whenever it starts
  own.context.state = 'running'; // resumed in time
  await Promise.resolve(); await Promise.resolve();
  expect(own.tones).toHaveLength(1);
  tone.play();
  expect(made).toHaveBeenCalledTimes(1); // made once, then kept
  expect(own.tones).toHaveLength(2);
  own.context.state = 'suspended'; clock = 0;
  const late = new HeardTone({ shared: () => null, make: () => own.context, now: () => clock });
  late.play(); clock = 400; own.context.state = 'running';
  await Promise.resolve(); await Promise.resolve();
  expect(own.tones).toHaveLength(2); // started too late to be a sign: not played
});
