// Hold to talk (talk.ts): the talk key held anywhere in the window, or Talk held; Escape, a tap or a chord drops it.
import { expect, test, vi } from 'vitest';
import { pressesTalkKey, TalkHold, TAP_MS, type TalkKey } from './talk';

type Listener = { type: string; fn: (event: unknown) => void; capture: boolean };
/** A window that records how it is listened to and delivers events as the browser would, capture listeners first. */
function fakeWindow() {
  const listeners: Listener[] = [];
  return {
    listeners,
    addEventListener: (type: string, fn: (event: unknown) => void, options?: boolean | AddEventListenerOptions) =>
      listeners.push({ type, fn, capture: options === true || (typeof options === 'object' && !!options.capture) }),
    removeEventListener: (type: string, fn: (event: unknown) => void) => {
      const at = listeners.findIndex(l => l.type === type && l.fn === fn);
      if (at >= 0) listeners.splice(at, 1);
    },
    fire(type: string, event: object = {}) { for (const l of listeners.filter(l => l.type === type)) l.fn(event); },
  };
}

const key = (k: string, extra: Partial<Omit<KeyboardEvent, 'target'>> & { target?: unknown } = {}) => ({
  key: k, code: extra.code ?? (k === 'Control' ? 'ControlLeft' : k === 'Alt' ? 'AltLeft' : `Key${k.toUpperCase()}`),
  repeat: false, ctrlKey: false, altKey: false, metaKey: false, shiftKey: false,
  preventDefault: vi.fn(), stopPropagation: vi.fn(), ...extra,
});

function setup(choice: TalkKey = 'control-option', enabled = true) {
  let now = 0;
  const calls: string[] = [];
  const hold = new TalkHold({ enabled: () => enabled, key: () => choice, now: () => now,
    press: (via) => calls.push(`press:${via}`), release: () => calls.push('release'), cancel: () => calls.push('cancel') });
  const win = fakeWindow();
  const doc = { visibilityState: 'visible', ...fakeWindow() };
  hold.attach(win, doc as unknown as Document);
  return { hold, win, doc, calls, at: (t: number) => { now = t; } };
}

test('the key is read on the window in the capture phase, so it works before any panel and with a text field focused', () => {
  const { win, calls, at } = setup();
  expect(win.listeners.filter(l => l.type === 'keydown' || l.type === 'keyup').every(l => l.capture)).toBe(true);
  const input = { tagName: 'INPUT' };
  win.fire('keydown', key('Control', { ctrlKey: true, target: input }));
  expect(calls).toEqual([]); // Control alone is not the talk key
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true, target: input }));
  expect(calls).toEqual(['press:key']);
  at(900);
  win.fire('keyup', key('Alt', { ctrlKey: true, target: input }));
  expect(calls).toEqual(['press:key', 'release']);
});

test('auto-repeat never presses again, and the other modifier order works', () => {
  const { win, calls, at } = setup();
  win.fire('keydown', key('Alt', { altKey: true }));
  win.fire('keydown', key('Control', { ctrlKey: true, altKey: true }));
  win.fire('keydown', key('Control', { ctrlKey: true, altKey: true, repeat: true }));
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true, repeat: true }));
  expect(calls).toEqual(['press:key']);
  at(600);
  win.fire('keyup', key('Control', { altKey: true }));
  win.fire('keyup', key('Alt'));
  expect(calls).toEqual(['press:key', 'release']); // let go once
});

test('losing the window while held is a release: what was said is sent', () => {
  const { win, doc, calls, at } = setup();
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  at(800);
  win.fire('blur');
  expect(calls).toEqual(['press:key', 'release']);
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  at(1800);
  doc.visibilityState = 'hidden';
  doc.fire('visibilitychange');
  expect(calls).toEqual(['press:key', 'release', 'press:key', 'release']);
});

test('Escape while holding drops the turn and closes nothing else', () => {
  const { win, calls, at } = setup();
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  at(900);
  const escape = key('Escape', { ctrlKey: true, altKey: true });
  win.fire('keydown', escape);
  expect(calls).toEqual(['press:key', 'cancel']);
  expect(escape.preventDefault).toHaveBeenCalled();
  expect(escape.stopPropagation).toHaveBeenCalled();
  win.fire('keyup', key('Alt'));
  expect(calls).toEqual(['press:key', 'cancel']); // the release after Escape sends nothing
  // Escape without a hold is the page's own (a panel closes).
  const other = key('Escape');
  win.fire('keydown', other);
  expect(other.stopPropagation).not.toHaveBeenCalled();
});

test('a tap stops the voice without asking anything; a chord (a VoiceOver command, Option+e) is never a turn', () => {
  const { win, calls, at } = setup();
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  at(TAP_MS - 50);
  win.fire('keyup', key('Alt', { ctrlKey: true }));
  expect(calls).toEqual(['press:key', 'cancel']);
  at(1000);
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  win.fire('keydown', key('ArrowRight', { code: 'ArrowRight', ctrlKey: true, altKey: true }));
  expect(calls).toEqual(['press:key', 'cancel', 'press:key', 'cancel']);
  // Shift joining the hold is not a chord of its own.
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  win.fire('keydown', key('Shift', { code: 'ShiftLeft', ctrlKey: true, altKey: true, shiftKey: true }));
  expect(calls.at(-1)).toBe('press:key');
});

test('Right Option is the other talk key: alone, by its own key code', () => {
  const { win, calls, at } = setup('right-option');
  win.fire('keydown', key('Alt', { code: 'AltLeft', altKey: true }));
  expect(calls).toEqual([]);
  win.fire('keydown', key('Alt', { code: 'AltRight', altKey: true }));
  expect(calls).toEqual(['press:key']);
  at(700);
  win.fire('keyup', key('Alt', { code: 'AltRight' }));
  expect(calls).toEqual(['press:key', 'release']);
  win.fire('keydown', key('Alt', { code: 'AltRight', altKey: true }));
  win.fire('keydown', key('e', { altKey: true }));
  expect(calls.at(-1)).toBe('cancel');
  expect(pressesTalkKey(key('Alt', { code: 'AltRight', altKey: true, metaKey: true }), 'right-option')).toBe(false);
});

test('with an open mic or GPT-Live the key does nothing', () => {
  const { win, calls } = setup('control-option', false);
  win.fire('keydown', key('Alt', { ctrlKey: true, altKey: true }));
  expect(calls).toEqual([]);
});

test('holding the Talk button does the same, and Escape while holding it cancels', () => {
  const { hold, win, calls, at } = setup();
  const button = fakeWindow() as ReturnType<typeof fakeWindow> & { setPointerCapture: (id: number) => void };
  button.setPointerCapture = vi.fn();
  hold.attachButton(button as unknown as HTMLElement);
  button.fire('pointerdown', { button: 0, pointerId: 4 });
  expect(calls).toEqual(['press:button']);
  expect(button.setPointerCapture).toHaveBeenCalledWith(4);
  at(1200);
  button.fire('pointerup', { button: 0, pointerId: 4 });
  expect(calls).toEqual(['press:button', 'release']);
  at(2000);
  button.fire('pointerdown', { button: 0, pointerId: 5 });
  win.fire('keydown', key('Escape'));
  button.fire('pointerup', { button: 0, pointerId: 5 });
  expect(calls).toEqual(['press:button', 'release', 'press:button', 'cancel']);
  // A right click is not a hold.
  button.fire('pointerdown', { button: 2, pointerId: 6 });
  expect(calls.at(-1)).toBe('cancel');
});
