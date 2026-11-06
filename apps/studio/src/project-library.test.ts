// Synthetic rendering evidence for Open Project as a library; actual browser acceptance is separate.
import {afterEach, expect, test, vi} from 'vitest';
import {coverOf, dayWords, distinctInitials, initials, inWords, showProjectLibrary, type LibraryRow} from './project-library';
import type {SessionRow} from './protocol';
import {TestElement, syntheticDOM, fire, settleMicrotasks} from './workspace-windows.test-support';
afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const NOW = 1793927400; // 5 Nov 2026, evening
const row = (id: string, name: string, fields: Partial<SessionRow> = {}): SessionRow => ({id, name, named: true, started: NOW - 86400 * 8,
  lastActive: NOW - 3600, days: [], counts: {sources: 3, made: 1, researchers: 0, conversations: 1}, specimens: [], working: 0, ...fields});

test('the cover is the main visual, then the page, then the newest picture, then a tile', () => {
  const base = row('2026-10-28.4', 'Virtual Cell Challenge — research', {thumb: '/thumb.jpg'});
  const image = coverOf(base, {session: base.id, visual: {id: 'pv', revision: 2, kind: 'image', sha256: 'ab'}, page: {id: 'pp', revision: 1}});
  expect(image).toEqual({kind: 'image', src: '/api/scientific-files/pv/media?session=2026-10-28.4&revision=2&sha256=ab'});
  expect(coverOf(base, {session: base.id, page: {id: 'pp', revision: 1}})).toEqual({kind: 'page', src: '/api/scientific-files/pp/page?session=2026-10-28.4&revision=1'});
  expect(coverOf(base, {session: base.id, visual: {id: 'pv', revision: 1, unavailable: true}})).toEqual({kind: 'thumb', src: '/thumb.jpg'});
  expect(coverOf(row('a', 'A'))).toEqual({kind: 'tile'});
});

test('cards say what is in a Project and when it was updated and created', () => {
  expect(initials('Virtual Cell Challenge — research')).toBe('VC');
  expect(initials('Oct 28')).toBe('O2');
  const marks = distinctInitials(['Beta readiness — local research', 'Beta readiness — fresh start', 'Virtual Cell Challenge — research']);
  expect([...marks.values()]).toEqual(['BL', 'BF', 'VC']);
  expect(inWords(row('a', 'A'), 3)).toBe('3 inputs · 3 Studies · 1 result');
  expect(inWords(row('a', 'A', {counts: {sources: 0, made: 0, researchers: 0, conversations: 1}}))).toBe('Nothing added yet');
  expect(dayWords(NOW - 60, NOW)).toMatch(/^today /);
  expect(dayWords(NOW - 86400 * 8, NOW)).toBe(new Date((NOW - 86400 * 8) * 1000).toLocaleDateString(undefined, {month: 'short', day: 'numeric'}));
});

function setup(rows: SessionRow[], read: () => Promise<LibraryRow[]>, open = vi.fn(async () => true)) {
  vi.useFakeTimers({toFake: ['Date', 'setInterval', 'clearInterval']}); vi.setSystemTime(NOW * 1000);
  const dom = syntheticDOM();
  const make = dom.document.createElement;
  const document = {...dom.document, activeElement: null, createElement: (tag: string) => Object.assign(make(tag), {
    showModal() {}, focus() {}, close(this: TestElement) { this.dispatchEvent(new Event('close')); }, isConnected: true})};
  vi.stubGlobal('document', document);
  const openFolder = vi.fn(), newProject = vi.fn();
  const dialog = showProjectLibrary({rows: () => rows, current: () => rows[0].id, read, open, openFolder, newProject}) as unknown as TestElement;
  const cards = () => dialog.querySelectorAll('.pl-card');
  const button = (text: string) => dialog.querySelectorAll('button').find(b => b.textContent === text)!;
  return {dialog, cards, button, open, openFolder, newProject};
}

test('the library lists Projects newest first, enriches them, and opens one', async () => {
  const rows = [row('p1', 'Current one', {lastActive: NOW - 60}), row('p2', 'Last week', {lastActive: NOW - 86400 * 9}), row('p3', 'Yesterday', {lastActive: NOW - 86400})];
  const {dialog, cards, open} = setup(rows, async () => [{session: 'p1', about: 'What it is.', studies: 3, savedIn: 'folder'}, {session: 'p3', studies: 0, savedIn: 'mac'}]);
  expect(cards().map(c => c.dataset.session)).toEqual(['p1', 'p3', 'p2']);
  expect(dialog.textContent).toContain('This week'); expect(dialog.textContent).toContain('Earlier');
  await settleMicrotasks();
  expect(dialog.textContent).toContain('What it is.'); expect(dialog.textContent).toContain('3 Studies');
  expect(dialog.textContent).toContain('Open now'); expect(dialog.textContent).toContain('Hooke Projects folder');
  fire(cards()[1], 'click'); await settleMicrotasks();
  expect(open).toHaveBeenCalledWith('p3');
  expect(dialog.textContent).toContain('Opening');
});

test('filters, search and a failed read keep the list usable', async () => {
  const rows = [row('p1', 'Alpha', {lastActive: NOW - 60}), row('p2', 'Beta', {archived: true}), row('p3', 'Gamma')];
  const {dialog, cards, button, openFolder} = setup(rows, async () => { throw new Error('Reconnect to refresh.'); });
  await settleMicrotasks();
  expect(dialog.textContent).toContain('Covers and descriptions could not be read. Reconnect to refresh.');
  expect(cards().map(c => c.dataset.session)).toEqual(['p1', 'p3']); // put away stays out of All
  const away = dialog.querySelectorAll('button').find(b => b.textContent?.startsWith('Put away'))!;
  fire(away, 'click'); expect(cards().map(c => c.dataset.session)).toEqual(['p2']);
  fire(dialog.querySelectorAll('button').find(b => b.textContent?.startsWith('All'))!, 'click');
  const search = dialog.querySelector('.pl-search')!; search.value = 'gam'; fire(search, 'input');
  expect(cards().map(c => c.dataset.session)).toEqual(['p3']);
  fire(button('Open a folder…'), 'click'); expect(openFolder).toHaveBeenCalledOnce();
});

test('a Project that does not open says so and keeps the current one', async () => {
  const rows = [row('p1', 'Current', {lastActive: NOW - 60}), row('p2', 'Other')];
  const {dialog, cards} = setup(rows, async () => [], vi.fn(async () => false));
  fire(cards()[1], 'click'); await settleMicrotasks(); await settleMicrotasks();
  expect(dialog.textContent).toContain('Other did not open. Your current Project is unchanged.');
});

test('the current Project opens too, on its front page, rather than only closing the sheet', async () => {
  const rows = [row('p1', 'Current', {lastActive: NOW - 60}), row('p2', 'Other')];
  const open = vi.fn(async () => true);
  const {cards} = setup(rows, async () => [], open);
  fire(cards()[0], 'click'); await settleMicrotasks();
  expect(open).toHaveBeenCalledWith('p1');
});

test('Projects that arrive after the sheet opens are listed', async () => {
  const rows = [row('p1', 'Current', {lastActive: NOW - 60})];
  const {cards} = setup(rows, async () => []);
  expect(cards()).toHaveLength(1);
  rows.push(row('p2', 'Arrived later'));
  vi.advanceTimersByTime(700);
  expect(cards().map(c => c.dataset.session)).toEqual(['p1', 'p2']);
});
