// Synthetic renderer tests of Everything it can do (#toolbox/all): every family at once as a tile, nothing folded.
import { afterEach, expect, test, vi } from 'vitest';
import { CapabilityPage } from './capabilities';
import type { CapabilitiesWire, Capability, Role } from './protocol';
import { fire, settleMicrotasks, syntheticDOM, type TestElement } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());

const cap = (id: string, family: string, over: Partial<Capability> = {}): Capability => ({ id, family, actor: 'you', title: id,
  what: `What ${id} does`, how: {}, available: true, why: '', verdict: null, ...over });
const keyedRole: Role = { id: 'naming', title: 'Naming', kind: 'model', purpose: '', current: 'cloud', powers: ['name-it'],
  options: [{ id: 'cloud', label: 'Cloud namer', note: '', available: false,
    readiness: { status: 'needs-setup', word: 'Needs your key', group: 'Needs setup', next: { kind: 'key', says: 'Add your key', service: 'gemini' }, steps: [] } }] } as never;

type FijiRead = () => Promise<{ summary: { listed: number; routes: Record<string, number>; bound?: { id: string; name: string }[] } }>;
function mounted(capabilities: Capability[], extra: Partial<CapabilitiesWire> = {}, opts: { hidden?: boolean; fiji?: FijiRead } = {}) {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document); vi.stubGlobal('requestAnimationFrame', dom.raf);
  const make = dom.document.createElement; // a select's options are its children, as in a browser
  dom.document.createElement = (tag: string) => { const node = make(tag);
    if (tag === 'select') Object.defineProperty(node, 'options', { get: () => (node as TestElement).children }); return node; };
  const root = dom.document.createElement('section') as unknown as TestElement;
  root.hidden = !!opts.hidden;
  const fiji = vi.fn(opts.fiji ?? (async () => ({ summary: { listed: 555, routes: { 'ships-with-fiji': 133, 'update-site': 76, 'none-known': 346 },
    bound: [{ id: 'fiji-analyze-particles', name: 'Analyze Particles (Fiji)' }] } })));
  const add = vi.fn();
  const page = new CapabilityPage(root as unknown as HTMLElement, vi.fn(), vi.fn(), null, null, { fiji, add });
  const wire: CapabilitiesWire = { type: 'capabilities', actors: { you: 'You', agent: 'I', auto: 'Automatic', researcher: 'Researchers' },
    families: [{ id: 'camera', title: 'Camera feed', what: 'Getting the picture in' }, { id: 'naming', title: 'Naming things', what: 'What things are' }],
    capabilities, roles: [keyedRole], preferences: [], pending: 0, build: 'test', agentText: '', ...extra };
  page.update(wire);
  return { root, page, wire, fiji, add };
}

const wireCaps = () => [cap('go-live', 'camera'), cap('record', 'camera', { verdict: { id: 'record', state: 'broken', note: '', at: '', build: 'test', by: 'you' } }),
  cap('name-it', 'naming', { available: false, why: 'Needs your Gemini key', roles: ['naming'] }), cap('teach', 'naming')];

test('every family is a tile at once, counted by what runs here, never by your verdicts, and nothing folds', () => {
  const { root } = mounted(wireCaps());
  const tiles = root.querySelectorAll('.capt-tile').filter(t => t.dataset.family !== 'fiji');
  expect(tiles.map(t => t.querySelector('h3')!.textContent)).toEqual(['Camera feed', 'Naming things']);
  expect(tiles.map(t => t.querySelector('.capt-tally')!.textContent)).toEqual(['2 of 2 run here · 1 broken', '1 of 2 run here']);
  expect(tiles[0].querySelectorAll('.capt-chip').map(c => c.textContent)).toEqual(['go-live', 'record']);
  expect(root.querySelector('[data-cap="name-it"]')!.getAttribute('data-unavailable')).toBe('');
  expect(root.querySelectorAll('[aria-expanded]').length).toBe(0); // no fold anywhere
  expect(root.querySelector('.toolbox-head-sub')!.textContent).toBe('4 capabilities · 3 run here · 1 broken');
});

test('the frame is the Toolbox one: the tabs, then the keys band, then this header', () => {
  const { root, add } = mounted(wireCaps(), { setup: { edition: 'open', options: {}, keys: {} } });
  const frame = root.querySelector('.toolbox-frame')!;
  expect(frame.children.map(c => c.className)).toEqual(['toolbox-tabs', 'toolbox-band', 'toolbox-head capc-head', 'cap-body']);
  // Both views are in the document at once: this one's band has ids of its own (Toolbox's are toolbox-keys…).
  const band = root.querySelector('.toolbox-band')!;
  expect([band.querySelector('[data-model="keys"]')!, ...band.querySelectorAll('h3'), band.querySelector('.toolbox-add')!].map(n => n.id))
    .toEqual(['toolbox-all-keys', 'toolbox-all-keys-heading', 'toolbox-all-add-heading', 'toolbox-all-add']);
  const ways = root.querySelectorAll('.toolbox-add-way');
  expect(ways.map(b => b.dataset.way)).toEqual(['find', 'add', 'create']);
  for (const way of ways) fire(way, 'click');
  expect(add.mock.calls).toEqual([['find'], ['add'], ['create']]); // the same guided sheet on Toolbox
});

test('Fiji is its own tile: connected functions, never said to run where Fiji is not set up', async () => {
  const notHere = [{ id: 'fiji-analyze-particles', status: 'needs-setup', word: 'Set up Fiji first' }, { id: 'fiji', status: 'needs-setup' }];
  const { root, fiji, page, wire } = mounted(wireCaps(), { models: notHere as never });
  await settleMicrotasks(); await settleMicrotasks();
  const tile = root.querySelector('[data-family="fiji"]')!;
  expect(tile.querySelectorAll('.capt-tally').map(n => n.textContent)).toEqual(['1 Fiji function connected · set up Fiji to use it',
    '555 extensions listed: 209 install in Fiji, 346 have no install route yet']);
  expect(tile.querySelectorAll('.capt-chip').map(c => c.textContent)).toEqual(['Analyze Particles (Fiji)', 'Every extension, on Toolbox']);
  expect(tile.querySelector('.capt-more')!.getAttribute('href')).toBe('#toolbox/fiji-extensions'); // the list, not Fiji's card
  const chip = tile.querySelector('.capt-chip')!;
  expect(chip.getAttribute('data-unavailable')).toBe(''); // drawn dashed, as any capability that can't run here
  expect(chip.getAttribute('title')).toBe('Not ready on this Mac: Set up Fiji first');
  page.update(wire);
  expect(fiji).toHaveBeenCalledTimes(1); // read once
});

test('Fiji\'s tile says a connected function is ready only when its own row is', async () => {
  const ready = [{ id: 'fiji-analyze-particles', status: 'ready', word: 'Ready' }, { id: 'fiji', status: 'ready' }];
  const { root } = mounted(wireCaps(), { models: ready as never });
  await settleMicrotasks(); await settleMicrotasks();
  const tile = root.querySelector('[data-family="fiji"]')!;
  expect(tile.querySelector('.capt-tally')!.textContent).toBe('1 Fiji function connected · 1 ready here');
  expect(tile.querySelector('.capt-chip')!.getAttribute('data-unavailable')).toBeNull();
});

test('Fiji\'s list is read only once this view is shown, never on a capabilities message while it is hidden', async () => {
  const { root, fiji, page, wire } = mounted(wireCaps(), {}, { hidden: true });
  page.update(wire); page.update(wire); await settleMicrotasks();
  expect(fiji).not.toHaveBeenCalled(); // every studio gets the message; only the one showing this view reads the list
  root.hidden = false; page.opened(); await settleMicrotasks(); await settleMicrotasks();
  expect(fiji).toHaveBeenCalledTimes(1);
  expect(root.querySelector('[data-family="fiji"]')).not.toBeNull();
});

test('a failed Fiji read is said on its tile with Try again, and not asked again on every message', async () => {
  let fail = true;
  const good = { summary: { listed: 3, routes: { 'update-site': 3 }, bound: [] } };
  const { root, fiji, page, wire } = mounted(wireCaps(), {}, { fiji: async () => { if (fail) throw new Error('503'); return good; } });
  await settleMicrotasks(); await settleMicrotasks();
  expect(root.querySelector('[data-family="fiji"]')!.textContent).toBe('FijiCouldn’t read Fiji’s list. Try again');
  page.update(wire); page.update(wire); await settleMicrotasks();
  expect(fiji).toHaveBeenCalledTimes(1);
  fail = false;
  fire(root.querySelector('.capt-fiji-again')!, 'click'); await settleMicrotasks(); await settleMicrotasks();
  expect(fiji).toHaveBeenCalledTimes(2);
  expect(root.querySelector('[data-family="fiji"]')!.querySelector('.capt-tally')!.textContent).toBe('No Fiji function connected yet');
});

test('two capabilities with one title (yours and the agent\'s) each say who', () => {
  const { root } = mounted([cap('close', 'camera', { title: 'Close it' }), cap('close-agent', 'camera', { title: 'Close it', actor: 'agent' })]);
  expect(root.querySelectorAll('.capt-chip').filter(c => c.dataset.cap).map(c => c.textContent)).toEqual(['Close itby you', 'Close itby asking']);
});

test('the filters hide the tiles and chips that do not match', () => {
  const { root } = mounted(wireCaps());
  const search = root.querySelector('.cap-search')!;
  search.value = 'teach'; fire(search, 'input');
  expect(root.querySelectorAll('.capt-tile').map(t => t.dataset.family)).toEqual(['naming']);
  expect(root.querySelectorAll('.capt-chip').map(c => c.textContent)).toEqual(['teach']);
});
