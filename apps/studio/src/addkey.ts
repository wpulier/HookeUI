/** Add, replace or remove an API key (P1 of docs/design/2026-10-12-the-open-catalog.md; server: key_admin.py).
 *
 * A sheet: where to get a key, a paste field, Test and save. The server tests the key with its service before saving
 * it in the Keychain; a refused key is never saved, and one it couldn't check is saved only on "Save unchecked". Where
 * the key comes from is always said (saved by this app, your keys file, set outside the app). The value goes only in
 * the POST's body: never in the URL, a log or a wire. */
import type { KeyFacts } from './protocol';
import { StudioSheet } from './sheet';

export interface KeyResult { ok: boolean; state?: 'accepted' | 'rejected' | 'unchecked'; says: string }
type Send = (service: string, method: 'POST' | 'DELETE', body?: { value: string; unchecked?: boolean }) => Promise<KeyResult>;

/** The server's key route: the header and the page's own origin are what let the studio, and only it, save a key. */
export const sendKey: Send = async (service, method, body) => {
  try {
    const response = await fetch(`/api/keys/${encodeURIComponent(service)}`, {
      method, headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'save-key' },
      body: body ? JSON.stringify(body) : undefined, cache: 'no-store',
    });
    const reply = await response.json() as Partial<KeyResult>;
    return { ok: !!reply.ok, state: reply.state, says: reply.says || (response.ok ? 'Done.' : 'That did not work.') };
  } catch {
    return { ok: false, says: 'The app did not answer. Is it running?' };
  }
};

/** Where a key comes from, in words ("from your keys file"), or that it isn't added. */
export function sourceWords(key: KeyFacts): string {
  if (!key.present) return key.fileBlocked ? 'can\'t read your keys file' : 'not added';
  return key.sourceSays || 'added';
}

/** What the sheet can do for a key: add or replace one this app saves; for one set outside the app, only say where. */
export function keyActions(key: KeyFacts): { save: boolean; remove: boolean; note: string } {
  if (key.source === 'environment') return { save: false, remove: false, note: `Your ${key.short} key is set outside the app; change it where it is set.` };
  if (!key.canSave) return { save: false, remove: false, note: key.fileBlocked || 'Keys can\'t be saved on this Mac; put it in your keys file.' };
  if (key.fileBlocked) return { save: true, remove: false, note: key.fileBlocked };
  const note = key.source === 'file' ? `Your ${key.short} key comes from your keys file. A key saved here is used instead.` : '';
  return { save: true, remove: key.source === 'keychain', note };
}

/** The chip that says where the key comes from now: its words and its tone (for its colour). */
export function sourceChip(key: KeyFacts): { words: string; tone: 'none' | 'blocked' | 'saved' | 'file' | 'outside' } {
  // There but unreadable is not "Not added" (F-L8-3): the file exists and macOS privacy keeps this app from it.
  if (!key.present) return key.fileBlocked ? { words: 'Can\'t read your keys file', tone: 'blocked' } : { words: 'Not added', tone: 'none' };
  if (key.source === 'keychain') return { words: `Saved by this app${key.checked ? ` · checked ${key.checked.at}` : ''}`, tone: 'saved' };
  if (key.source === 'file') return { words: 'From your keys file', tone: 'file' };
  return { words: 'Set outside the app', tone: 'outside' };
}

export interface KeyForm {
  element: HTMLFormElement;
  focus: () => void;
  dispose: () => void;
}

export function keyForm(key: KeyFacts, saved: () => void = () => {}, send: Send = sendKey): KeyForm {
  const actions = keyActions(key);
  let disposed = false;
  let sending = false;
  const host = el('form', 'add-key');

  // What it turns on, and where the key comes from now.
  const intro = el('div', 'add-key-intro');
  if (key.enables) intro.append(el('p', 'add-key-enables', key.enables + '.'));
  const chip = sourceChip(key);
  const now = el('span', 'add-key-chip', chip.words);
  now.dataset.tone = chip.tone;
  intro.append(now);
  host.append(intro);
  if (actions.note) host.append(el('p', 'add-key-note', actions.note));

  const field = document.createElement('input');
  field.type = 'password'; field.autocomplete = 'off'; field.spellcheck = false; field.name = 'key';
  field.placeholder = 'Paste it here'; field.id = `key-${key.service}`;
  const result = el('div', 'add-key-result');
  result.setAttribute('role', 'status'); result.hidden = true;
  const save = button('Test and save', 'submit', 'mm-primary');
  const unchecked = button('Save unchecked'); unchecked.hidden = true;
  const remove = button('Remove this key', 'button', 'add-key-remove'); remove.hidden = !actions.remove;

  if (actions.save) {
    const steps = el('ol', 'add-key-steps');
    const get = el('li', 'add-key-step');
    const link = document.createElement('a');
    link.className = 'mm-control add-key-get'; link.href = key.getKey; link.target = '_blank'; link.rel = 'noopener noreferrer';
    link.textContent = `Get a ${key.short} key ↗`;
    get.append(el('span', 'add-key-step-title', 'Get a key'),
      el('span', 'add-key-step-hint', 'It opens their page; sign in and copy an API key from their developer settings: a chat '
        + 'subscription doesn\'t include API use, which they bill separately.'), link);
    const paste = el('li', 'add-key-step');
    const label = document.createElement('label');
    label.className = 'add-key-step-title'; label.htmlFor = field.id; label.textContent = 'Paste it';
    const show = button('Show', 'button', 'add-key-show');
    show.setAttribute('aria-pressed', 'false');
    show.addEventListener('click', () => {
      const shown = field.type === 'password';
      field.type = shown ? 'text' : 'password';
      show.textContent = shown ? 'Hide' : 'Show';
      show.setAttribute('aria-pressed', String(shown));
    });
    const row = el('div', 'add-key-field');
    row.append(field, show);
    paste.append(label, row, el('span', 'add-key-step-hint', 'Checking it is one free call: adding a key starts no paid work.'));
    steps.append(...(key.getKey ? [get] : []), paste);
    host.append(steps);
  }
  host.append(result);

  const footer = el('div', 'add-key-footer');
  footer.append(el('span', 'add-key-privacy', actions.save ? 'Kept in this Mac\'s Keychain. Never shown again.' : ''),
    remove, ...(actions.save ? [unchecked, save] : []));
  host.append(footer);

  const busy = (on: boolean) => { save.disabled = unchecked.disabled = remove.disabled = field.disabled = on; };
  const say = (words: string, state: string) => {
    result.hidden = false; result.dataset.state = state;
    result.replaceChildren(el('span', 'add-key-result-mark', { ok: '✓', rejected: '✕', unchecked: '!', error: '!', busy: '' }[state] ?? ''),
      el('span', '', words));
  };
  const done = (reply: KeyResult) => {
    if (disposed) return;
    sending = false; busy(false);
    say(reply.says, reply.ok ? 'ok' : reply.state ?? 'error');
    unchecked.hidden = reply.state !== 'unchecked';  // only when the service couldn't say
    if (reply.ok) { field.value = ''; saved(); }
  };
  host.addEventListener('submit', async (event) => {
    event.preventDefault();
    if (disposed || sending || !actions.save) return;
    if (!field.value.trim()) { say('Paste the key first.', 'error'); field.focus(); return; }
    sending = true; busy(true); say(`Checking with ${key.short}…`, 'busy');
    done(await send(key.service, 'POST', { value: field.value }));
  });
  unchecked.addEventListener('click', async () => {
    if (disposed || sending || !actions.save || unchecked.hidden) return;
    sending = true; busy(true); say('Saving without checking…', 'busy');
    done(await send(key.service, 'POST', { value: field.value, unchecked: true }));
  });
  remove.addEventListener('click', async () => {
    if (disposed || sending || !actions.remove) return;
    sending = true; busy(true);
    done(await send(key.service, 'DELETE'));
  });
  return { element: host, focus: () => { if (actions.save) field.focus(); },
    dispose: () => { disposed = true; field.value = ''; host.remove(); } };
}

export function openAddKey(key: KeyFacts, opener: HTMLElement | null, send: Send = sendKey): StudioSheet {
  const sheet = new StudioSheet(key.present ? `Your ${key.short} key` : `Add your ${key.short} key`);
  sheet.dialog.classList.add('add-key-sheet');
  let timer: number | undefined;
  const form = keyForm(key, () => { timer = window.setTimeout(() => sheet.close(), 1800); }, send);
  document.body.append(form.element);
  sheet.open(form.element, opener);
  sheet.onDismiss = () => { if (timer !== undefined) window.clearTimeout(timer); form.dispose(); };
  form.focus();
  return sheet;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text?: string): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text: string, type: 'button' | 'submit' = 'button', extra = ''): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = type; b.className = `mm-control ${extra}`.trim(); b.textContent = text;
  return b;
}
