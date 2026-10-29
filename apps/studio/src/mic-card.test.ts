// The microphone moment (mic-card.ts; Will's feel check, 29 Oct): the permission read from a stubbed Permissions API,
// when Talk shows the card first, its words in each state, and its button starting Talk in its own click.
import { expect, test } from 'vitest';
import { MicCard, micCardWords, micPermission, needsMicCard } from './mic-card';

class FakeElement {
  tag: string; children: FakeElement[] = []; attributes: Record<string, string> = {}; dataset: Record<string, string> = {};
  hidden = false; className = ''; textContent = ''; innerHTML = ''; type = '';
  private listeners: Record<string, (() => void)[]> = {};
  constructor(tag: string) { this.tag = tag; }
  setAttribute(name: string, value: string) { this.attributes[name] = value; }
  addEventListener(name: string, fn: () => void) { (this.listeners[name] ??= []).push(fn); }
  append(...nodes: FakeElement[]) { this.children.push(...nodes); }
  click() { for (const fn of this.listeners.click ?? []) fn(); }
  find(className: string): FakeElement | undefined {
    for (const child of this.children) {
      if (child.className === className) return child;
      const deeper = child.find(className);
      if (deeper) return deeper;
    }
    return undefined;
  }
}
const doc = { createElement: (tag: string) => new FakeElement(tag) } as unknown as Document;

function permissions(state: string | Error) {
  const status: { state: string; onchange?: (() => void) | null } = { state: state instanceof Error ? '' : state };
  return { status, api: { query: async () => { if (state instanceof Error) throw state; return status; } } };
}

test('the permission is read from the browser, and its changes are followed', async () => {
  const { status, api } = permissions('prompt');
  const seen: string[] = [];
  expect(await micPermission(api, (s) => seen.push(s))).toBe('prompt');
  status.state = 'granted';
  status.onchange?.();
  expect(seen).toEqual(['granted']);
  expect(await micPermission(permissions('denied').api)).toBe('denied');
});

test('a browser that cannot say is unknown, and Talk then starts at once', async () => {
  expect(await micPermission(undefined)).toBe('unknown');
  expect(await micPermission(permissions(new TypeError("'microphone' is not a valid permission name")).api)).toBe('unknown');
  expect(needsMicCard('unknown', false, false)).toBeNull();
});

test('the card comes first only when the microphone is not allowed yet, or is blocked', () => {
  expect(needsMicCard('granted', false, false)).toBeNull();
  expect(needsMicCard('prompt', false, false)).toBe('ask');
  expect(needsMicCard('prompt', true, false)).toBe('ask'); // a browser that asks every time: it is said first
  expect(needsMicCard('prompt', true, true)).toBeNull(); // the Mac app, once it has heard you: its web view may keep saying "prompt"
  expect(needsMicCard('denied', true, true)).toBe('blocked');
});

test('its words: one line of what happens and one button; the prompt never called connecting; blocked says how', () => {
  const ask = micCardWords('ask', 'Control+Option', false);
  expect(ask.line).toBe('Hold Control+Option and talk; Hooke listens only while you hold it.');
  expect(ask.button).toBe('Turn on the microphone');
  const asking = micCardWords('asking', 'Control+Option', false);
  expect(asking.button).toBeNull();
  expect(`${asking.title} ${asking.line}`).not.toMatch(/connect/i);
  expect(micCardWords('blocked', 'Control+Option', false).line).toMatch(/address bar.*Microphone → Allow/);
  expect(micCardWords('blocked', 'Control+Option', true).line).toMatch(/System Settings → Privacy & Security → Microphone/);
});

test('the card shows its state, and its button starts Talk in its own click', () => {
  let started = 0;
  const card = new MicCard({ act: () => { started += 1; }, words: () => ({ keyWords: 'Right Option', macApp: false }), doc });
  const root = card.root as unknown as FakeElement;
  expect(root.hidden).toBe(true);
  card.show('ask');
  expect(root.hidden).toBe(false);
  expect(root.dataset.state).toBe('ask');
  expect(root.find('mic-card-line')?.textContent).toContain('Hold Right Option and talk');
  const button = root.find('mic-card-act')!;
  expect(button.hidden).toBe(false);
  button.click();
  expect(started).toBe(1);
  card.show('asking');
  expect(button.hidden).toBe(true);
  card.show('blocked');
  expect(button.textContent).toBe('Try again');
  root.find('mic-card-close')!.click();
  expect(card.state).toBeNull();
  expect(root.hidden).toBe(true);
});
