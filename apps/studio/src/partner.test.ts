// The studio's answers to the one-brain voice (partner.ts): the window now, and which marks were drawn, at once.
import { expect, test, vi } from 'vitest';
import { partnerHandler, windowSnapshot, type WindowCapture } from './partner';
import type { PartnerCommand } from './protocol';

const capture = (shared: boolean): WindowCapture & { sides: number[] } => {
  const sides: number[] = [];
  return { sides,
    share: (side) => { sides.push(side); return shared ? { jpeg: 'U0hBUkU=', w: 1280, h: 800 } : null; },
    compose: (side) => { sides.push(side); return { jpeg: 'Q09NUE9TRQ==', w: side, h: 800 }; } };
};

test('a turn\'s snapshot is the shared tab when there is one, else the composed window, never over 1280 px', () => {
  expect(windowSnapshot('talk-3', 1280, capture(true), () => 1000)).toEqual(
    { type: 'snapshot', id: 'talk-3', jpeg: 'U0hBUkU=', w: 1280, h: 800, at: 1000, how: 'tab-share' });
  const composed = capture(false);
  expect(windowSnapshot('look-1', 4000, composed, () => 2000)).toEqual(
    { type: 'snapshot', id: 'look-1', jpeg: 'Q09NUE9TRQ==', w: 1280, h: 800, at: 2000, how: 'composed' });
  expect(composed.sides).toEqual([1280, 1280]);
});

test('a capture that fails is said at once, so the server does not wait out its 1.5 s', () => {
  const broken: WindowCapture = { share: () => null, compose: () => { throw new Error('canvas lost'); } };
  expect(windowSnapshot('look-2', 768, broken)).toEqual({ type: 'snapshot', id: 'look-2', error: 'The window could not be captured: canvas lost' });
  expect(windowSnapshot('look-3', 768, { share: () => null, compose: () => ({ jpeg: '', w: 0, h: 0 }) })).toMatchObject({ error: expect.any(String) });
});

test('a snapshot request and a set of marks are answered in the same task they arrive in', () => {
  const sent: PartnerCommand[] = [];
  const marks = { show: vi.fn(() => [{ target: 'outline:o12', ok: true, rect: [1, 2, 3, 4] as [number, number, number, number] }]), clear: vi.fn() };
  const handle = partnerHandler({ send: (c) => { sent.push(c); return true; }, capture: capture(false), marks });
  handle({ type: 'snapshot-request', id: 'look-4', maxSide: 1024 });
  expect(sent[0]).toMatchObject({ type: 'snapshot', id: 'look-4', how: 'composed', w: 1024 });
  handle({ type: 'marks', id: 'm1', marks: [{ target: 'outline:o12' }], until: 'speech-end' });
  expect(sent[1]).toEqual({ type: 'marks-shown', id: 'm1', resolved: [{ target: 'outline:o12', ok: true, rect: [1, 2, 3, 4] }] });
  handle({ type: 'marks-clear', id: 'm1' });
  expect(marks.clear).toHaveBeenCalledWith('m1');
});
