/** The Toolbox's top band, the same on both of its views (#toolbox, #toolbox/all): your keys, always open, each with what
 * it turns on, and the three ways to add a capability. Will, 2026-10-15: keys are what we most want people to add, so they
 * are never folded away; the example still works without one, and the band says only that (chat and vision need a key). */
import type { KeyFacts, SetupCounts } from './protocol';
import { openAddKey, sourceChip } from './addkey';

export type AddWay = 'find' | 'add' | 'create' | 'link' | 'file' | 'build';
export interface BandActions { add: (way: AddWay) => void }

/** Add model or tool: its three paths, the same in Models and in the workspace's Tools; none starts work
 * (docs/design/2026-10-23-part1-uiux-pass/README.md, Decisions 1). */
export function capabilityWays(actions: BandActions): HTMLElement {
  const ways = el('div', { class: 'toolbox-add-ways' });
  for (const [way, title, what] of [['find', 'Find it in the catalog', 'Search what Hooke offers, set it up and use it'], ['add', 'I have a link or file', 'A Hugging Face or GitHub link, a model file or a package'],
    ['create', 'Have AI build it', 'Describe it, or adapt one you have, here or in your own coding tool']] as const) {
    const b = el('button', { type: 'button', class: 'toolbox-add-way', 'data-way': way }, el('strong', {}, title), el('span', {}, what));
    b.addEventListener('click', () => actions.add(way));
    ways.append(b);
  }
  return ways;
}

export interface KeyTile { name: string; key: KeyFacts; adds: number }

/** The keys the band shows as tiles and the rest. Tiles: those missing that turn something on (the most first), then those
 * you have (they show where each comes from and Change). The rest (a missing key that adds nothing counted, a bench's) are
 * one quiet line. */
export function keyTiles(keys: Record<string, KeyFacts>, counts: SetupCounts | undefined): { tiles: KeyTile[]; other: KeyTile[] } {
  const adds = new Map((counts?.unlock ?? []).map((u) => [u.key, u.adds]));
  const all = Object.entries(keys).map(([name, key]) => ({ name, key, adds: adds.get(name) ?? 0 }));
  const missing = all.filter((k) => !k.key.present);
  return {
    tiles: [...missing.filter((k) => k.adds > 0).sort((a, b) => b.adds - a.adds), ...all.filter((k) => k.key.present)],
    other: missing.filter((k) => k.adds <= 0),
  };
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string | boolean> = {},
  ...children: (Node | string | null)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false) continue;
    if (k === 'class') node.className = String(v); else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const c of children) if (c !== null) node.append(c);
  return node;
}

/** The shown band's button for a key's service, after the band it was pressed in was drawn again (a saved key changes the
 * wire, and both views redraw their band): focus goes back there, never to a button no longer in the page. */
export function refocusKey(service: string, scope: Pick<ParentNode, 'querySelectorAll'> = document): HTMLElement | null {
  const shown = (n: HTMLElement) => { for (let p: HTMLElement | null = n; p; p = p.parentElement) if (p.hidden) return false; return true; };
  const target = [...scope.querySelectorAll<HTMLElement>(`[data-key-act="${service}"]`)].find(shown) ?? null;
  target?.focus({ preventScroll: true });
  return target;
}

function keyButton(key: KeyFacts, label: string, cls: string): HTMLButtonElement {
  const b = el('button', { type: 'button', class: cls, 'data-key-act': key.service }, label);
  b.setAttribute('aria-label', key.present ? `Change your ${key.short} key` : `Add your ${key.short} key`);
  b.addEventListener('click', () => {
    const sheet = openAddKey(key, b);
    const dismissed = sheet.onDismiss;
    sheet.onDismiss = () => { dismissed(); if (!b.isConnected) refocusKey(key.service); }; // the sheet refocuses b itself while it is there
  });
  return b;
}

/** The band's element ids for one view: both views are in the document at once (one hidden), so each has its own
 * (Toolbox's `toolbox-keys`, Everything it can do's `toolbox-all-keys`). `#models/keys` lands by `data-model="keys"`
 * inside the page that is shown, never by id. */
export function bandIds(view: 'tools' | 'all'): { keys: string; keysHeading: string; add: string; addHeading: string } {
  const p = view === 'all' ? 'toolbox-all' : 'toolbox';
  return { keys: `${p}-keys`, keysHeading: `${p}-keys-heading`, add: `${p}-add`, addHeading: `${p}-add-heading` };
}

/** Keys and Add model or tool, for one of the Toolbox's views (`bandIds`). Drawn again on every wire; it keeps its height
 * while the keys are being read, so nothing under it moves when they arrive. */
export function toolboxBand(keys: Record<string, KeyFacts> | undefined, counts: SetupCounts | undefined, actions: BandActions,
  view: 'tools' | 'all' = 'tools'): HTMLElement {
  const ids = bandIds(view);
  const keysBlock = el('section', { class: 'toolbox-keys', id: ids.keys, 'data-model': 'keys', tabindex: '-1',
    'aria-labelledby': ids.keysHeading },
    el('header', { class: 'toolbox-band-head' }, el('h3', { id: ids.keysHeading }, 'Keys'),
      el('p', {}, 'The example works without a key. Each one turns on more; kept on this Mac, never shown.')));
  if (!keys) keysBlock.append(el('p', { class: 'toolbox-band-wait' }, 'Reading your keys…'));
  else {
    const { tiles, other } = keyTiles(keys, counts);
    keysBlock.append(el('ul', { class: 'toolbox-key-tiles' }, ...tiles.map(({ key, adds }) =>
      el('li', { class: 'toolbox-key', 'data-present': String(key.present) },
        el('strong', { class: 'toolbox-key-name' }, key.short),
        el('span', { class: 'toolbox-key-adds' }, adds ? `+${adds} things it can do` : key.present ? 'In use' : ''),
        el('span', { class: 'toolbox-key-state', title: key.enables || key.name }, key.present ? `✓ ${sourceChip(key).words}` : sourceChip(key).words),
        keyButton(key, key.present ? 'Change' : 'Add key', `mm-control ${key.present ? '' : 'mm-primary '}toolbox-key-act`)))));
    const blocked = Object.values(keys).find(key => key.fileBlocked)?.fileBlocked;
    if (blocked) keysBlock.append(el('p', { class: 'toolbox-key-blocked', role: 'status' }, blocked));
    if (other.length) {
      const line = el('p', { class: 'toolbox-key-other' }, 'Other keys: ');
      other.forEach(({ key }, i) => line.append(i ? ' · ' : '', keyButton(key, key.short, 'toolbox-key-other-act')));
      keysBlock.append(line);
    }
  }
  const add = el('section', { class: 'toolbox-add', id: ids.add, 'aria-labelledby': ids.addHeading },
    el('header', { class: 'toolbox-band-head' }, el('h3', { id: ids.addHeading }, 'Add model or tool'),
      el('p', {}, 'Anything not here: a model, a tool, or one an AI drafts for you.')));
  add.append(capabilityWays(actions));
  return el('div', { class: 'toolbox-band' }, keysBlock, add);
}
