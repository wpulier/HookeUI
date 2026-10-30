// Drive (drive.ts): the partner cursor seen before each intent, and the user taking control back in the same task.
import { expect, test, vi } from 'vitest';
import { aimAt, defaultStyle, MOVE_MS, PartnerCursor, Takeover, type CursorState } from './drive';
import type { MarkHooks, Place } from './marks';
import type { PartnerCommand } from './protocol';

function hooks(places: Record<string, Place>): MarkHooks {
  const doc = { querySelector: (selector: string) => {
    const id = /\[id="([^"]+)"\]/.exec(selector)?.[1] ?? /data-workspace-view-tab|work-tab-([^"]+)/.exec(selector)?.[1];
    const place = id ? places[`control:${id}`] ?? places[`tab:${id}`] : undefined;
    if (!place || !('rect' in place)) return null;
    const [x, y, w, h] = place.rect;
    return { getBoundingClientRect: () => ({ left: x, top: y, width: w, height: h }), closest: () => null, textContent: '', dataset: {} };
  }, querySelectorAll: () => [] };
  return { doc, viewport: () => [1440, 900], outline: (id) => places[`outline:${id}`] ?? { why: 'not-found' },
    member: () => ({ why: 'hidden' }), anchor: () => null, frame: () => ({ why: 'out-of-view' }),
    panel: () => ({ why: 'hidden' }), researcherRids: () => [] };
}

function setup(places: Record<string, Place>, still = false) {
  let now = 0;
  const painted: CursorState[] = [], replies: PartnerCommand[] = [], frames: (() => void)[] = [];
  const cursor = new PartnerCursor({ hooks: hooks(places), paint: (s) => painted.push({ ...s }), reply: (c) => replies.push(c),
    now: () => now, frame: (fn) => frames.push(fn), still });
  const tick = (ms: number) => { now += ms; const fn = frames.shift(); fn?.(); };
  return { cursor, painted, replies, tick, last: () => painted.at(-1)!, at: (t: number) => { now = t; } };
}

test('the cursor moves to its control by id over a few hundred ms, with its caption, then says it is there', () => {
  const t = setup({ 'control:talk': { rect: [1000, 800, 80, 28] } });
  t.cursor.show({ type: 'drive', id: 'd1', version: 4, intent: { kind: 'open', target: 'control:talk', caption: 'Opening the table' } });
  expect(t.replies).toEqual([]); // not there yet
  const first = t.last();
  expect(first.visible && first.caption === 'Opening the table' && first.style === 'cursor').toBe(true);
  t.tick(MOVE_MS / 2);
  const mid = t.last().at;
  t.tick(MOVE_MS / 2 + 20);
  expect(t.last().at).toEqual(aimAt([1000, 800, 80, 28]));
  expect(mid[0]).not.toBe(t.last().at[0]); // it travelled
  expect(t.replies).toEqual([{ type: 'drive-shown', id: 'd1', ok: true, rect: [1000, 800, 80, 28] }]);
  t.tick(16);
  expect(t.replies).toHaveLength(1); // said once
});

test('a target that is not on screen is said at once, never drawn', () => {
  const t = setup({});
  t.cursor.show({ type: 'drive', id: 'd2', version: 1, intent: { kind: 'focus', target: 'tab:Data', caption: 'Going to Data' } });
  expect(t.replies).toEqual([{ type: 'drive-shown', id: 'd2', ok: false, why: 'not-found' }]);
  expect(t.painted).toEqual([]);
});

test('things on the picture are ringed, and the ring follows its outline as the slide moves', () => {
  const places: Record<string, Place> = { 'outline:o12': { rect: [300, 200, 40, 30] } };
  const t = setup(places);
  expect(defaultStyle('outline:o12')).toBe('ring');
  expect(defaultStyle('member:p1:3')).toBe('ring');
  expect(defaultStyle('window:w-1')).toBe('cursor');
  t.cursor.show({ type: 'drive', id: 'd3', version: 1, intent: { kind: 'highlight', target: 'outline:o12', caption: 'Highlighting it' } });
  expect(t.last().style).toBe('ring');
  places['outline:o12'] = { rect: [320, 210, 40, 30] };
  t.tick(MOVE_MS + 10);
  expect(t.last().ring).toEqual([320, 210, 40, 30]);
});

test('an intent that ends shows its tick (verified) or cross (failed) a moment, then fades; uncertain fades plainly', () => {
  const t = setup({ 'control:talk': { rect: [100, 100, 60, 30] } });
  t.cursor.show({ type: 'drive', id: 'd4', version: 1, intent: { kind: 'close', target: 'control:talk' } });
  t.tick(MOVE_MS + 10);
  t.cursor.end({ type: 'drive-end', id: 'd4', outcome: 'verified' });
  expect(t.last().outcome).toBe('verified');
  for (let i = 0; i < 40; i++) t.tick(16);
  expect(t.last().opacity).toBe(0);
  expect(t.cursor.current).toBe(null);
  t.cursor.end({ type: 'drive-end', id: 'other', outcome: 'failed' }); // another intent's end changes nothing
  expect(t.last().outcome).toBe('verified');
});

function taking(acting = true) {
  let now = 1000;
  const sent: PartnerCommand[] = [];
  const cursor = { stop: vi.fn() };
  const takeover = new Takeover({ cursor, send: (c) => { sent.push(c); return true; }, now: () => now });
  if (acting) takeover.control({ type: 'control', version: 7, owner: 'partner' });
  const listeners: Record<string, (event: unknown) => void> = {};
  takeover.attach({ addEventListener: (type: string, fn: (event: unknown) => void, capture?: boolean | AddEventListenerOptions) => {
    expect(capture).toBe(true); listeners[type] = fn; }, removeEventListener: () => {} } as unknown as Window);
  return { takeover, sent, cursor, listeners, later: (ms: number) => { now += ms; } };
}
const move = (x: number, y: number, isTrusted = true) => ({ clientX: x, clientY: y, isTrusted, timeStamp: 1000 });

test('a press or a scroll of the user\'s own takes control back: the cursor stops and the server is told, in that task', () => {
  const t = taking();
  expect(t.listeners.pointermove).toBeUndefined(); // the pointer only moving is no takeover: nothing listens for it
  t.listeners.pointerdown(move(510, 505, false)); // a script's event is not a hand
  expect(t.sent).toEqual([]);
  t.listeners.pointerdown(move(504, 502));
  expect(t.cursor.stop).toHaveBeenCalledOnce();
  expect(t.sent).toEqual([{ type: 'takeover', version: 7, why: 'mouse', at: expect.any(Number) }]);
  expect(t.takeover.acting).toBe(false);
  t.listeners.pointerdown(move(600, 600));
  expect(t.sent).toHaveLength(1); // once
  const scroll = taking();
  scroll.listeners.wheel(move(700, 400));
  expect(scroll.sent.at(-1)).toMatchObject({ type: 'takeover', why: 'mouse' });
  t.later(4);
  t.takeover.control({ type: 'control', version: 8, owner: 'user', ack: true, at: Date.now() });
  expect(t.takeover.last!.knownAt! - t.takeover.last!.stoppedAt).toBe(4);
  expect(t.takeover.version).toBe(8);
});

test('Escape and the talk key take control back too; Escape stops the partner and closes nothing else', () => {
  const t = taking();
  const escape = { key: 'Escape', repeat: false, preventDefault: vi.fn(), stopPropagation: vi.fn(), timeStamp: 1000 };
  t.listeners.keydown(escape);
  expect(escape.preventDefault).toHaveBeenCalled();
  expect(escape.stopPropagation).toHaveBeenCalled();
  expect(t.sent.at(-1)).toMatchObject({ type: 'takeover', why: 'escape', version: 7 });
  const u = taking();
  expect(u.takeover.input('talk')).toBe(true);
  expect(u.sent.at(-1)).toMatchObject({ type: 'takeover', why: 'talk' });
});

test('the talk key is a pause: the server is told, and the cursor stays on the action being done until it ends', () => {
  const stopped: string[] = [];
  const sent: unknown[] = [];
  const takeover = new Takeover({ cursor: { stop: () => { stopped.push('stop'); } }, send: (c) => { sent.push(c); return true; } });
  takeover.control({ type: 'control', version: 3, owner: 'partner' });
  expect(takeover.input('talk')).toBe(true);
  expect(sent).toEqual([{ type: 'takeover', version: 3, why: 'talk', at: expect.any(Number) }]);
  expect(stopped).toEqual([]); // the action being done finishes; its drive-end fades the cursor
  expect(takeover.acting).toBe(false);
  takeover.control({ type: 'control', version: 4, owner: 'partner' });
  expect(takeover.input('escape')).toBe(true);
  expect(stopped).toEqual(['stop']);
});

test('while the user has control nothing is a takeover, and Escape is the page\'s own', () => {
  const t = taking(false);
  t.listeners.pointerdown(move(0, 0));
  t.listeners.wheel(move(200, 200));
  const escape = { key: 'Escape', repeat: false, preventDefault: vi.fn(), stopPropagation: vi.fn() };
  t.listeners.keydown(escape);
  expect(t.takeover.input('talk')).toBe(false);
  expect(t.sent).toEqual([]);
  expect(escape.stopPropagation).not.toHaveBeenCalled();
  // An older control state never takes control back from a newer one.
  t.takeover.control({ type: 'control', version: 9, owner: 'partner' });
  t.takeover.control({ type: 'control', version: 8, owner: 'user' });
  expect(t.takeover.acting).toBe(true);
});
