import { afterEach, expect, test, vi } from 'vitest';
import type { CapabilitiesWire } from './protocol';
import { setupInProgress } from './setup-progress';

afterEach(() => { vi.unstubAllGlobals(); });

const models = [
  { id: 'big', name: 'Big model', where: 'on this Mac' },
  { id: 'fiji', name: 'Fiji', where: 'on this Mac' },
  { id: 'gone', name: 'Gone', where: 'on this Mac' },
] as unknown as CapabilitiesWire['models'];

const wire = (setup: Partial<NonNullable<CapabilitiesWire['setup']>>) => ({ models, setup: { edition: 'open', options: {}, ...setup } }) as unknown as CapabilitiesWire;

test('lists each model being set up once, in the setup sheet\'s words, with measured download progress', () => {
  const rows = setupInProgress(wire({
    turning: { cells: { job: 'cells', option: 'big', model: 'big', label: 'Big', state: 'running', says: 'Downloading its weights' } },
    downloads: [{ model: 'big', name: 'Big', state: 'downloading', got: 21e6, total: 88e6, says: 'Fetching weights' }],
    installs: [{ model: 'fiji', name: 'Fiji', state: 'installing', says: 'Installing Fiji' }],
  }));
  expect(rows.map(r => [r.name, r.says, r.progress, r.where])).toEqual([
    ['Big model', 'Downloading its weights', '21 MB of 88 MB · 23%', 'on this Mac'],
    ['Fiji', 'Installing Fiji', null, 'on this Mac'],
  ]);
});

test('a failed setup, a finished download and a cancelled install are not progress', () => {
  expect(setupInProgress(wire({
    turning: { cells: { job: 'cells', option: 'gone', model: 'gone', label: 'Gone', state: 'failed', says: 'It failed' } },
    downloads: [{ model: 'big', name: 'Big', state: 'done', got: 1, total: 1, says: '' }],
    installs: [{ model: 'fiji', name: 'Fiji', state: 'cancelled', says: '' }],
  }))).toEqual([]);
  expect(setupInProgress(null)).toEqual([]);
});

test('checking and queued downloads say so instead of a percentage', () => {
  const said = (state: 'checking' | 'queued') => setupInProgress(wire({ downloads: [{ model: 'big', name: 'Big', state, got: 0, total: 9, says: 'x' }] }))[0].progress;
  expect(said('checking')).toBe('Checking downloaded files');
  expect(said('queued')).toBe('Waiting to download');
});

test('the section sits above Activity\'s list, follows it when the list moves, hides when nothing is being set up, and See progress opens that model', async () => {
  const { syntheticDOM, fire } = await import('./workspace-windows.test-support');
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const { SetupProgress } = await import('./setup-progress');
  const panel = dom.document.createElement('div'), data = dom.document.createElement('div'), list = dom.document.createElement('ol');
  panel.append(list);
  const opened: string[] = [];
  const section = new SetupProgress(list as unknown as HTMLElement, (model) => opened.push(model));
  const busy = wire({ installs: [{ model: 'fiji', name: 'Fiji', state: 'installing', says: 'Installing Fiji' }] });
  section.update(busy);
  expect(panel.children[0]).toBe(section.root); expect(panel.children[1]).toBe(list);
  expect(section.root.hidden).toBe(false);
  fire(section.root.querySelector('.setup-progress-open') as never, 'click');
  expect(opened).toEqual(['fiji']);
  data.append(list); section.update(busy);
  expect(data.children[0]).toBe(section.root);
  section.update(wire({}));
  expect(section.root.hidden).toBe(true);
});
