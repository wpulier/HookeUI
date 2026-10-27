// The session's title and its switcher (session-menu.ts) over a synthetic DOM: which session you are in, "Opening…"
// until the server's echo, every session newest first by when it began (opening one never moves it), the search, each
// row's picture, rename in place, the list read once, Escape among the other menus, and the screen report.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { matchesSearch, SessionMenu } from './session-menu';
import { SessionsModel, READ_FAILED } from './sessions';
import { PanelStack, type EscapeEvent, type Panel } from './panels';
import type { ClientCommand } from './protocol';
import { fire } from './workspace-windows.test-support';
import { list, NOW, row, sessionDOM, SessionNode, state, at, type SessionDOM } from './sessions.test-support';

let dom: SessionDOM;
beforeEach(() => { dom = sessionDOM(); });
afterEach(() => vi.unstubAllGlobals());

function menu(options: { fetcher?: (...args: unknown[]) => unknown; online?: boolean } = {}) {
  const sent: ClientCommand[] = [];
  const model = new SessionsModel((c) => { if (options.online === false) return false; sent.push(c); return true; }, () => NOW * 1000);
  const chip = dom.node('session', 'button');
  const panel = dom.node('session-panel', 'section');
  panel.hidden = true;
  const view = new SessionMenu(model, chip as unknown as HTMLButtonElement, panel as unknown as HTMLElement,
    { fetcher: (options.fetcher ?? vi.fn().mockResolvedValue({ ok: true, json: async () => ({ current: 'a', sessions: [] }) })) as unknown as typeof fetch });
  return { sent, model, chip, panel, view };
}
const text = (node: { textContent: string } | null | undefined) => node?.textContent ?? '';
const rowsIn = (panel: SessionNode) => panel.querySelectorAll('.session-row') as SessionNode[];
const rowOf = (panel: SessionNode, sid: string) => rowsIn(panel).find((r) => r.dataset.sid === sid)!;
const openButton = (panel: SessionNode, sid: string) =>
  (panel.querySelectorAll('.session-row-open') as SessionNode[]).find((b) => b.dataset.sid === sid)!;

test('the title says the session you are in, by its name alone; what it holds is in its tooltip and accessible name', () => {
  const { model, chip } = menu();
  model.applySession(state('2026-10-12.2', { name: 'Leaf epidermis', started: at(9, 0, 1), counts: { made: 5, researchers: 2, conversations: 3 } }));
  expect(text(chip.querySelector('.session-chip-name'))).toBe('Leaf epidermis');
  expect(chip.getAttribute('aria-label')).toBe('Project: Leaf epidermis · since yesterday · 5 results · 2 researchers. Switch projects or rename this one');
  expect(chip.title).toContain('Leaf epidermis · since yesterday · 5 results · 2 researchers');
  expect(chip.getAttribute('aria-haspopup')).toBe('true');
  expect(chip.hidden).toBe(false);
  expect((chip.querySelector('.session-chip-dot') as SessionNode).hidden).toBe(true);
  // A suggestion: a dot, and said.
  model.applySession(state('2026-10-12.2', { name: 'Leaf epidermis', suggest: { specimen: 'kidney section', hours: 5 } }));
  expect((chip.querySelector('.session-chip-dot') as SessionNode).hidden).toBe(false);
  expect(chip.getAttribute('aria-label')).toContain('The app suggests a new project');
});

test('an unnamed session of today is "Untitled session" until the app or you name it', () => {
  const { model, chip } = menu();
  model.applySession(state('2026-10-12.2', { name: '', started: NOW }));
  expect(text(chip.querySelector('.session-chip-name'))).toBe('Untitled project');
});

test('opened without a sessions message, it reads the list once; a failure says so', async () => {
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('down'))
    .mockResolvedValue({ ok: true, json: async () => ({ current: 'a', sessions: [row('a'), row('b', { name: 'Blood smear' })] }) });
  const { model, chip, panel, view } = menu({ fetcher });
  model.applySession(state('a'));
  fire(chip, 'click');
  await Promise.resolve(); await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(1);
  expect(text(panel)).toContain(READ_FAILED);
  view.close(); fire(chip, 'click'); // opened again: tried again
  await Promise.resolve(); await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(2);
  expect(text(panel)).not.toContain(READ_FAILED);
  expect(rowsIn(panel).map((r) => r.dataset.sid)).toEqual(['a', 'b']); // every session, the open one marked
  expect(rowOf(panel, 'a').getAttribute('aria-current')).toBe('true');
  view.close(); fire(chip, 'click');
  await Promise.resolve();
  expect(fetcher).toHaveBeenCalledTimes(2); // read once: the list is kept, and the server's messages keep it
});

test('every session, newest first by when it began (not last used), under day separators; opening one never moves it', () => {
  const { model, chip, panel } = menu();
  model.applySession(state('a'));
  model.applyList(list('a', [row('a', { started: at(14, 10), lastActive: at(14, 20) }),
    ...[1, 2, 3].map((n) => row(`s${n}`, { name: `S${n}`, started: at(9, 0, n), lastActive: at(15, 0, 0) }))]));
  fire(chip, 'click');
  expect(rowsIn(panel).map((r) => r.dataset.sid)).toEqual(['a', 's1', 's2', 's3']);
  expect((panel.querySelectorAll('.session-day') as SessionNode[]).map(text)).toEqual(['Today', 'Yesterday', 'Oct 10', 'Oct 9']);
  model.applySession(state('s3', { name: 'S3', started: at(9, 0, 3) }));
  expect(rowsIn(panel).map((r) => r.dataset.sid)).toEqual(['a', 's1', 's2', 's3']);
});

test("each row has a picture of what the session last looked at, else the camera's icon (it only looked live)", () => {
  const { model, chip, panel } = menu();
  model.applySession(state('a'));
  model.applyList(list('a', [row('a'), row('b', { thumb: '/api/library/a7/thumbnail' })]));
  fire(chip, 'click');
  const pictured = rowOf(panel, 'b').querySelector('.session-row-thumb') as SessionNode;
  expect((pictured.querySelector('img') as SessionNode).src).toBe('/api/library/a7/thumbnail');
  const live = rowOf(panel, 'a').querySelector('.session-row-thumb') as SessionNode;
  expect(live.querySelector('img')).toBeNull();
  expect(live.dataset.icon).toBe('camera');
});

test('the search narrows the list by name or by what was seen in it; nothing matching is said', () => {
  const { model, chip, panel, view } = menu();
  model.applySession(state('a', { name: 'Leaf' }));
  model.applyList(list('a', [row('a', { name: 'Leaf' }), row('b', { name: 'Blood smear' }), row('c', { name: 'Pond', specimens: ['rotifer', 'diatom'] })]));
  fire(chip, 'click');
  view.search.value = 'rotif';
  fire(view.search as unknown as SessionNode, 'input');
  expect(rowsIn(panel).map((r) => r.dataset.sid)).toEqual(['c']);
  expect(panel.querySelector('.session-now')).toBeNull(); // searching: only the matches
  view.search.value = 'kidney';
  fire(view.search as unknown as SessionNode, 'input');
  expect(rowsIn(panel)).toEqual([]);
  expect(text(panel)).toContain('No project matches.');
  expect(matchesSearch(row('d', { name: 'Blood smear' }), 'BLOOD sm', NOW)).toBe(true);
  expect(matchesSearch(row('d', { name: 'Blood smear' }), 'blood leaf', NOW)).toBe(false);
});

test('a click on a session sends the switch, closes the menu and the title says "Opening…" until the echo', () => {
  const { model, chip, panel, sent } = menu();
  model.applySession(state('a', { name: 'Leaf' }));
  model.applyList(list('a', [row('a', { name: 'Leaf' }), row('b', { name: 'Blood smear', working: 1, lastActive: at(12) })]));
  fire(chip, 'click');
  fire(openButton(panel, 'a'), 'click'); // the open one: nothing to do
  expect(sent).toEqual([]);
  const b = openButton(panel, 'b');
  expect(text(b)).toContain('Blood smear');
  expect(text(b)).toContain('last used 12:00 pm');
  expect(b.querySelector('.session-row-pill')).not.toBeNull(); // a researcher works in it
  fire(b, 'click');
  expect(sent).toEqual([{ type: 'session', action: 'switch', id: 'b' }]);
  expect(panel.hidden).toBe(true);
  expect(text(chip.querySelector('.session-chip-name'))).toBe('Opening…');
  expect(chip.getAttribute('aria-label')).toContain('Opening Blood smear');
  expect(chip.dataset.state).toBe('opening');
  model.applySession(state('b', { name: 'Blood smear' }));
  expect(text(chip.querySelector('.session-chip-name'))).toBe('Blood smear');
  expect(chip.dataset.state).toBe('');
});

test('+ New session sends new; This session is renamed in place: Enter saves, Escape cancels', () => {
  const { model, chip, panel, sent } = menu();
  model.applySession(state('a', { name: 'Leaf' }));
  model.applyList(list('a', [row('a', { name: 'Leaf' })]));
  fire(chip, 'click');
  expect(text(panel.querySelector('.session-now-title'))).toBe('Leaf');
  expect(text(panel.querySelector('.session-now-facts'))).toBe('since 2:10 pm · 1 day · 1 conversation · 0 researchers · 0 results');
  // Rename: ✎, then Escape keeps it as it was and the menu open.
  fire(panel.querySelector('.session-now-rename') as SessionNode, 'click');
  let input = panel.querySelector('.session-rename') as SessionNode;
  expect(input.value).toBe('Leaf');
  expect(SessionNode.active).toBe(input);
  input.value = 'Never mind';
  expect(dom.key(input, 'Escape').prevented).toBe(true);
  expect(sent).toEqual([]);
  expect(panel.hidden).toBe(false);
  expect(text(panel.querySelector('.session-now-title'))).toBe('Leaf');
  // Enter saves.
  fire(panel.querySelector('.session-now-rename') as SessionNode, 'click');
  input = panel.querySelector('.session-rename') as SessionNode;
  input.value = 'Leaf epidermis';
  dom.key(input, 'Enter');
  expect(sent).toEqual([{ type: 'session', action: 'rename', id: 'a', name: 'Leaf epidermis' }]);
  expect(text(panel.querySelector('.session-now-title'))).toBe('Leaf epidermis'); // at once, kept until the echo
  // New session.
  fire(panel.querySelector('.session-new') as SessionNode, 'click');
  expect(sent.at(-1)).toEqual({ type: 'session', action: 'new' });
  expect(panel.hidden).toBe(true);
  expect(text(chip.querySelector('.session-chip-name'))).toBe('Opening…');
  expect(chip.getAttribute('aria-label')).toContain('Opening a new project');
});

test('any session is renamed from its row: the ✎ or a double-click on its name; Escape cancels and keeps the menu open', () => {
  const { model, chip, panel, view, sent } = menu();
  model.applySession(state('a'));
  model.applyList(list('a', [row('a'), row('b', { name: 'Blood smear', lastActive: at(12) })]));
  fire(chip, 'click');
  fire(rowOf(panel, 'b').querySelector('.session-row-rename') as SessionNode, 'click');
  let input = rowOf(panel, 'b').querySelector('.session-rename') as SessionNode;
  expect(input.value).toBe('Blood smear');
  input.value = 'Something else';
  expect(dom.key(input, 'Escape').prevented).toBe(true);
  expect(sent).toEqual([]);
  expect(view.isOpen()).toBe(true);
  expect(text(rowOf(panel, 'b').querySelector('.session-row-name'))).toBe('Blood smear');
  fire(rowOf(panel, 'b').querySelector('.session-row-name') as SessionNode, 'dblclick', { preventDefault: () => {}, stopPropagation: () => {} });
  input = rowOf(panel, 'b').querySelector('.session-rename') as SessionNode;
  input.value = 'Blood smear, Wright stain';
  dom.key(input, 'Enter');
  expect(sent).toEqual([{ type: 'session', action: 'rename', id: 'b', name: 'Blood smear, Wright stain' }]);
  expect(text(rowOf(panel, 'b').querySelector('.session-row-name'))).toBe('Blood smear, Wright stain');
});

test('offline: + and a click say to reconnect; nothing waits', () => {
  const { model, chip, panel } = menu({ online: false });
  model.applySession(state('a'));
  model.applyList(list('a', [row('a'), row('b', { name: 'Blood smear' })]));
  fire(chip, 'click');
  fire(panel.querySelector('.session-new') as SessionNode, 'click');
  expect(model.creating()).toBe(false);
  expect(panel.hidden).toBe(false);
  fire(openButton(panel, 'b'), 'click');
  expect(model.opening()).toBe(false);
  expect(model.error).not.toBe('');
});

test('Escape closes the menu on top: a dialog opened after it first, the menu before the side bar', () => {
  const { model, chip, view } = menu();
  model.applySession(state('a'));
  const stack = new PanelStack();
  let side = true, dialog = false;
  stack.register({ id: 'sidebar', isOpen: () => side, close: () => { side = false; }, footprint: () => null } as Panel);
  stack.register(view);
  stack.register({ id: 'scores', escapePriority: 100, isOpen: () => dialog, close: () => { dialog = false; }, footprint: () => null } as Panel);
  const press = (): EscapeEvent => ({ key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} });
  fire(chip, 'click');
  dialog = true;
  stack.onEscape(press());
  expect([dialog, view.isOpen(), side]).toEqual([false, true, true]);
  stack.onEscape(press());
  expect([view.isOpen(), side]).toEqual([false, true]);
  expect(chip.getAttribute('aria-expanded')).toBe('false');
  stack.onEscape(press());
  expect(side).toBe(false);
  // Its search and rename fields are its own: Escape there is theirs, and the stack leaves them alone.
  expect(view.whileTyping).toBe(true);
});

test('a click outside the title and its menu closes it', () => {
  const { model, chip, panel, view } = menu();
  model.applySession(state('a'));
  fire(chip, 'click');
  expect(view.outside(panel.querySelector('.session-new') as unknown as Node)).toBe(false);
  expect(view.outside(chip as unknown as Node)).toBe(false);
  expect(view.outside(dom.node('elsewhere') as unknown as Node)).toBe(true);
  expect(panel.hidden).toBe(true);
});

test('the sessions list on the screen report: kind `sessions`, open or not; "open/close the sessions list" through present', () => {
  const { model, view } = menu();
  model.applySession(state('a'));
  expect(view.screenItem()).toMatchObject({ id: 'sessions', kind: 'sessions', open: false, visible: false, rect: null });
  expect(view.applyPresent({ open: true })).toBe(true);
  expect(view.screenItem()).toMatchObject({ open: true, visible: true });
  expect(view.applyPresent({ open: false })).toBe(true);
  expect(view.isOpen()).toBe(false);
  expect(view.applyPresent({ opacity: 0.5 })).toBe(false);
});
