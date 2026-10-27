// Add, replace or remove a key (addkey.ts): what the sheet offers for each source, and that the key only travels in the
// request's body, with the header that lets the studio, and only it, save one.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { keyActions, sendKey, sourceWords } from './addkey';
import type { KeyFacts } from './protocol';

const key = (over: Partial<KeyFacts> = {}): KeyFacts => ({
  service: 'openai', name: 'OpenAI API', short: 'OpenAI', getKey: 'https://platform.openai.com/api-keys',
  enables: 'Talking by voice, research, and the pictures researchers make', present: false,
  source: null, sourceSays: '', canSave: true, ...over,
});

describe('what the sheet offers', () => {
  it('adds a missing key', () => {
    expect(keyActions(key())).toEqual({ save: true, remove: false, note: '' });
    expect(sourceWords(key())).toBe('not added');
  });

  it('replaces or removes a key this app saved', () => {
    const saved = key({ present: true, source: 'keychain', sourceSays: 'saved by this app' });
    expect(keyActions(saved)).toEqual({ save: true, remove: true, note: '' });
    expect(sourceWords(saved)).toBe('saved by this app');
  });

  it('can save over a keys-file key, saying the saved one is used instead, and never removes the file\'s', () => {
    const fromFile = key({ present: true, source: 'file', sourceSays: 'from your keys file' });
    expect(keyActions(fromFile)).toEqual({ save: true, remove: false, note: 'Your OpenAI key comes from your keys file. A key saved here is used instead.' });
  });

  it('never touches a key set outside the app, and says where to change it', () => {
    const outside = key({ present: true, source: 'environment', sourceSays: 'set outside the app', canSave: false });
    expect(keyActions(outside)).toEqual({ save: false, remove: false, note: 'Your OpenAI key is set outside the app; change it where it is set.' });
  });
});

describe('a keys file this app may not read (F-L8-3)', () => {
  it('says it can\'t read the file and how to fix it, and still offers the Keychain', async () => {
    const { sourceChip } = await import('./addkey');
    const why = 'This app isn\'t allowed to read your keys file (~/Desktop/micromind.txt). Allow it in System Settings.';
    const blocked = key({ fileBlocked: why });
    expect(sourceChip(blocked)).toEqual({ words: 'Can\'t read your keys file', tone: 'blocked' });
    expect(sourceWords(blocked)).toBe('can\'t read your keys file');
    expect(keyActions(blocked)).toEqual({ save: true, remove: false, note: why });
  });
});

describe('the request', () => {
  afterEach(() => vi.unstubAllGlobals());

  it('sends the key in the body only, with the studio header', async () => {
    const fetch = vi.fn(async () => new Response(JSON.stringify({ ok: true, state: 'accepted', says: 'OpenAI accepted the key.' })));
    vi.stubGlobal('fetch', fetch);
    const result = await sendKey('openai', 'POST', { value: 'sk-planted' });
    const [url, init] = fetch.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/keys/openai');
    expect(url).not.toContain('sk-planted');
    expect(init.headers).toMatchObject({ 'X-MicroscopeMind': 'save-key' });
    expect(JSON.parse(String(init.body))).toEqual({ value: 'sk-planted' });
    expect(result).toEqual({ ok: true, state: 'accepted', says: 'OpenAI accepted the key.' });
  });

  it('says so plainly when the app does not answer', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline'); }));
    expect(await sendKey('openai', 'DELETE')).toEqual({ ok: false, says: 'The app did not answer. Is it running?' });
  });
});

describe('the chip that says where the key comes from', () => {
  it('names the source and its tone', async () => {
    const { sourceChip } = await import('./addkey');
    expect(sourceChip(key())).toEqual({ words: 'Not added', tone: 'none' });
    expect(sourceChip(key({ present: true, source: 'keychain', checked: { outcome: 'accepted', at: '14:02' } })))
      .toEqual({ words: 'Saved by this app · checked 14:02', tone: 'saved' });
    expect(sourceChip(key({ present: true, source: 'file' }))).toEqual({ words: 'From your keys file', tone: 'file' });
    expect(sourceChip(key({ present: true, source: 'environment' }))).toEqual({ words: 'Set outside the app', tone: 'outside' });
  });
});

it('an inline key form retries rejection, retains unchecked confirmation and ignores dismissed replies', async () => {
  const { keyForm } = await import('./addkey');
  const { syntheticDOM, fire, settleMicrotasks } = await import('./workspace-windows.test-support');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const saved = vi.fn();
  let finish: ((value: import('./addkey').KeyResult) => void) | undefined;
  const send = vi.fn(() => new Promise<import('./addkey').KeyResult>(resolve => { finish = resolve; }));
  const form = keyForm(key(), saved, send);
  const host = form.element as unknown as import('./workspace-windows.test-support').TestElement;
  const field = host.querySelector('input')!;
  field.value = 'rejected-fixture'; fire(host, 'submit'); fire(host, 'submit');
  expect(send).toHaveBeenCalledTimes(1);
  finish!({ ok: false, state: 'rejected', says: 'Rejected' }); await settleMicrotasks();
  expect(field.value).toBe('rejected-fixture'); expect(host.textContent).toContain('Rejected');
  field.value = 'corrected-fixture'; fire(host, 'submit');
  finish!({ ok: false, state: 'unchecked', says: 'Could not check' }); await settleMicrotasks();
  const unchecked = host.querySelectorAll('button').find(b => b.textContent === 'Save unchecked')!;
  expect(unchecked.hidden).toBe(false); fire(unchecked, 'click');
  expect(send).toHaveBeenLastCalledWith('openai', 'POST', { value: 'corrected-fixture', unchecked: true });
  form.dispose(); expect(field.value).toBe('');
  finish!({ ok: true, says: 'Saved' }); await settleMicrotasks();
  expect(saved).not.toHaveBeenCalled(); expect(field.value).toBe('');
  vi.unstubAllGlobals();
});

it('says before anything is pasted that a key is an API key, not a subscription, and that adding one is free (F061, F064)', async () => {
  const { keyForm } = await import('./addkey');
  const { syntheticDOM } = await import('./workspace-windows.test-support');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const send = vi.fn();
  const text = (keyForm(key()).element as unknown as import('./workspace-windows.test-support').TestElement).textContent;
  expect(text).toContain('an API key from their developer settings: a chat subscription doesn\'t include API use');
  expect(text).toContain('Checking it is one free call: adding a key starts no paid work');
  const outside = keyForm(key({ present: true, source: 'environment', canSave: false }), () => {}, send);
  expect((outside.element as unknown as import('./workspace-windows.test-support').TestElement).textContent)
    .not.toContain('free call');  // nothing is checked or saved there: nothing to say about checking
  expect(send).not.toHaveBeenCalled();
  vi.unstubAllGlobals();
});
