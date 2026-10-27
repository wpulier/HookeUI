// Synthetic renderer tests of the Toolbox's band (keystrip.ts): where focus goes after the Add key sheet closes.
import { afterEach, expect, test, vi } from 'vitest';
import type { KeyFacts } from './protocol';
import { TestElement, fire } from './workspace-windows.test-support';

const sheet = { onDismiss: () => {} };
vi.mock('./addkey', async (original) => ({ ...(await original<typeof import('./addkey')>()), openAddKey: vi.fn(() => sheet) }));
const { toolboxBand } = await import('./keystrip');

afterEach(() => vi.unstubAllGlobals());

const key = (over: Partial<KeyFacts> = {}): KeyFacts => ({ service: 'openai', name: 'OpenAI API', short: 'OpenAI', getKey: '',
  enables: 'Talking', present: false, ...over } as KeyFacts);

test('after a key is saved and the band drawn again, focus returns to that key\'s button in the band that is shown', () => {
  let focused: TestElement | null = null;
  class Focusable extends TestElement { focus() { focused = this; } }
  const body = new TestElement('body');
  vi.stubGlobal('document', { createElement: (tag: string) => new Focusable(tag), querySelectorAll: (s: string) => body.querySelectorAll(s),
    get activeElement() { return focused; } });
  const hiddenView = new TestElement('section'); hiddenView.hidden = true; // the other view, its band with the same key
  const shownView = new TestElement('section');
  body.append(hiddenView, shownView);
  hiddenView.append(toolboxBand({ OPENAI_API_KEY: key() }, undefined, { add: () => {} }, 'all') as unknown as TestElement);
  shownView.append(toolboxBand({ OPENAI_API_KEY: key() }, undefined, { add: () => {} }, 'tools') as unknown as TestElement);
  const pressed = shownView.querySelector('[data-key-act="openai"]')!;
  fire(pressed, 'click');
  // Saved: the wire changed and the band was drawn again, so the button pressed is gone.
  shownView.replaceChildren(toolboxBand({ OPENAI_API_KEY: key({ present: true, source: 'keychain' }) }, undefined, { add: () => {} }, 'tools') as unknown as TestElement);
  Object.defineProperty(pressed, 'isConnected', { value: false });
  sheet.onDismiss();
  const now = shownView.querySelector('[data-key-act="openai"]')!;
  expect(focused).toBe(now);
  expect(now.textContent).toBe('Change');
});

test('a keys file macOS keeps this app from reading is said as that, never as "Not added" (F-L8-3)', () => {
  vi.stubGlobal('document', { createElement: (tag: string) => new TestElement(tag) });
  const why = 'This app isn\'t allowed to read your keys file (~/Desktop/micromind.txt), so its keys can\'t be used.';
  const band = toolboxBand({ OPENAI_API_KEY: key({ fileBlocked: why }), GEMINI_API_KEY: key({ service: 'google', short: 'Google', fileBlocked: why }) },
    { unlock: [{ key: 'OPENAI_API_KEY', adds: 15 }, { key: 'GEMINI_API_KEY', adds: 15 }] } as never, { add: () => {} }, 'tools') as unknown as TestElement;
  expect(band.textContent).not.toContain('Not added');
  expect(band.textContent).toContain('Can\'t read your keys file');
  expect(band.querySelector('.toolbox-key-blocked')?.textContent).toBe(why);
});
