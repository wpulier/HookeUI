// The studio's answers to the one-brain voice (partner.ts): the window now, and which marks were drawn, at once.
import { expect, test, vi } from 'vitest';
import { partnerHandler, windowSnapshot, type WindowCapture } from './partner';
import type { PartnerCommand } from './protocol';

const capture = (shared: boolean): WindowCapture & { sides: number[]; started: number[] } => {
  const sides: number[] = [], started: number[] = [];
  let task = 0;
  setTimeout(() => { task++; }, 0); // a later task
  return { sides, started,
    share: async (side) => { sides.push(side); return shared ? { jpeg: 'U0hBUkU=', w: 1280, h: 800 } : null; },
    compose: async (side) => { sides.push(side); started.push(task); return { jpeg: 'Q09NUE9TRQ==', w: side, h: 800 }; } };
};

test('a turn\'s snapshot is the shared tab when there is one, else the composed window, never over 1280 px', async () => {
  expect(await windowSnapshot('talk-3', 1280, capture(true), () => 1000)).toEqual(
    { type: 'snapshot', id: 'talk-3', jpeg: 'U0hBUkU=', w: 1280, h: 800, at: 1000, how: 'tab-share' });
  const composed = capture(false);
  expect(await windowSnapshot('look-1', 4000, composed, () => 2000)).toEqual(
    { type: 'snapshot', id: 'look-1', jpeg: 'Q09NUE9TRQ==', w: 1280, h: 800, at: 2000, how: 'composed' });
  expect(composed.sides).toEqual([1280, 1280]);
});

test('without a share the window is still taken in the asking task (its pictures are the ones at the key press)', async () => {
  const composed = capture(false);
  const asked = windowSnapshot('talk-1', 1280, composed);
  await Promise.resolve(); // the share's "none" settles in a microtask, before any other task
  expect(composed.started).toEqual([0]);
  await asked;
});

test('a capture that fails is said at once, so the server does not wait out its 1.5 s', async () => {
  const broken: WindowCapture = { share: async () => null, compose: async () => { throw new Error('canvas lost'); } };
  expect(await windowSnapshot('look-2', 768, broken)).toEqual({ type: 'snapshot', id: 'look-2', error: 'The window could not be captured: canvas lost' });
  expect(await windowSnapshot('look-3', 768, { share: async () => null, compose: async () => ({ jpeg: '', w: 0, h: 0 }) })).toMatchObject({ error: expect.any(String) });
});

test('a snapshot request is answered as soon as it is composed; a set of marks in the task it arrives in', async () => {
  const sent: PartnerCommand[] = [];
  const marks = { show: vi.fn(() => [{ target: 'outline:o12', ok: true, rect: [1, 2, 3, 4] as [number, number, number, number] }]), clear: vi.fn() };
  const handle = partnerHandler({ reply: (c) => { sent.push(c); return true; }, capture: capture(false), marks });
  handle({ type: 'snapshot-request', id: 'look-4', maxSide: 1024 });
  await vi.waitFor(() => expect(sent[0]).toMatchObject({ type: 'snapshot', id: 'look-4', how: 'composed', w: 1024 }));
  handle({ type: 'marks', id: 'm1', marks: [{ target: 'outline:o12' }], until: 'speech-end' });
  expect(sent[1]).toEqual({ type: 'marks-shown', id: 'm1', resolved: [{ target: 'outline:o12', ok: true, rect: [1, 2, 3, 4] }] });
  handle({ type: 'marks-clear', id: 'm1' });
  expect(marks.clear).toHaveBeenCalledWith('m1');
});

test('Hands: control and cues reach the cursor; a cue from before the user took control back is never drawn', () => {
  const sent: PartnerCommand[] = [];
  const shown: string[] = [], ended: string[] = [];
  let version = 0;
  const takeover = { get version() { return version; }, control: (m: { version: number }) => { version = m.version; } };
  const handle = partnerHandler({ reply: (c) => { sent.push(c); return true; }, capture: capture(false),
    marks: { show: () => [], clear: () => {} },
    drive: { cursor: { show: (m) => shown.push(m.id), end: (m) => ended.push(m.id) }, takeover } });
  handle({ type: 'control', version: 5, owner: 'partner' });
  handle({ type: 'drive', id: 'd1', version: 5, intent: { kind: 'open', target: 'control:talk', caption: 'Opening it' } });
  handle({ type: 'control', version: 6, owner: 'user', ack: true });
  handle({ type: 'drive', id: 'd2', version: 5, intent: { kind: 'close', target: 'window:w-1' } }); // planned before the takeover
  handle({ type: 'drive-end', id: 'd1', outcome: 'uncertain' });
  expect(shown).toEqual(['d1']);
  expect(ended).toEqual(['d1']);
  expect(sent).toEqual([{ type: 'drive-shown', id: 'd2', ok: false, why: 'stale' }]);
});
