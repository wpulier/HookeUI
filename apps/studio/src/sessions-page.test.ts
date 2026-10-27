// All sessions (sessions-page.ts) over a synthetic DOM: every session a card under its day, the open one marked, a click
// opens a session and goes back to the workspace, the search narrows it, + New session starts one; and the session
// menu's "All sessions ›" opens the page.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { SessionMenu } from './session-menu';
import { SessionsPage } from './sessions-page';
import { SessionsModel, SWITCH_WAIT_MS } from './sessions';
import type { ClientCommand } from './protocol';
import { fire } from './workspace-windows.test-support';
import { at, list, NOW, row, sessionDOM, SessionNode, state, type SessionDOM } from './sessions.test-support';

let dom: SessionDOM;
beforeEach(() => { dom = sessionDOM(); });
afterEach(() => vi.unstubAllGlobals());

function page() {
  const sent: ClientCommand[] = [];
  const went: string[] = [];
  const model = new SessionsModel((c) => { sent.push(c); return true; }, () => NOW * 1000);
  const root = dom.node('sessions-page', 'section');
  const view = new SessionsPage(root as unknown as HTMLElement, model, (hash) => went.push(hash));
  model.applySession(state('a', { name: 'Leaf' }));
  model.applyList(list('a', [row('a', { name: 'Leaf' }), row('b', { name: 'Blood smear', started: at(9), lastActive: at(12) })]));
  return { sent, went, model, root, view };
}
const cards = (root: SessionNode) => root.querySelectorAll('.session-card') as SessionNode[];
const card = (root: SessionNode, sid: string) => cards(root).find((c) => c.dataset.sid === sid)!;

test('every session is a card under its day, the open one marked; nothing is drawn while the page is hidden', () => {
  const { root, view } = page();
  expect(cards(root)).toHaveLength(0);
  view.show();
  expect(cards(root).map((c) => c.dataset.sid).sort()).toEqual(['a', 'b']);
  expect(card(root, 'a').getAttribute('aria-current')).toBe('true');
  expect(card(root, 'a').textContent).toContain('Open now');
  expect(card(root, 'b').textContent).toContain('Blood smear');
  expect(card(root, 'b').textContent).toContain('Last used 12:00 pm');
  expect((root.querySelectorAll('.sessions-day') as SessionNode[]).length).toBeGreaterThan(0);
});

test('a click opens that session and goes back to the workspace; the open one just goes back', () => {
  const { root, view, sent, went } = page();
  view.show();
  fire(card(root, 'a').querySelector('.session-card-open') as SessionNode, 'click');
  expect(sent).toEqual([]);
  expect(went).toEqual(['#']);
  fire(card(root, 'b').querySelector('.session-card-open') as SessionNode, 'click');
  expect(sent).toEqual([{ type: 'session', action: 'switch', id: 'b' }]);
  expect(went).toEqual(['#', '#']);
});

test('the search narrows the cards; + New session starts one and goes back', () => {
  const { root, view, sent, went } = page();
  view.show();
  view.search.value = 'blood';
  fire(view.search as unknown as SessionNode, 'input');
  expect(cards(root).map((c) => c.dataset.sid)).toEqual(['b']);
  view.search.value = 'nothing like it';
  fire(view.search as unknown as SessionNode, 'input');
  expect(root.querySelector('.lib-empty')!.textContent).toBe('No project matches.');
  fire(view.newButton as unknown as SessionNode, 'click');
  expect(sent.at(-1)).toEqual({ type: 'session', action: 'new' });
  expect(went.at(-1)).toBe('#');
});

test("the session menu's All sessions › opens the page, closing the menu", () => {
  const model = new SessionsModel(() => true, () => NOW * 1000);
  const chip = dom.node('session', 'button');
  const panel = dom.node('session-panel', 'section');
  panel.hidden = true;
  const opened: string[] = [];
  new SessionMenu(model, chip as unknown as HTMLButtonElement, panel as unknown as HTMLElement, {
    fetcher: vi.fn().mockResolvedValue({ ok: true, json: async () => ({ current: 'a', sessions: [] }) }) as unknown as typeof fetch,
    openAll: () => opened.push('all') });
  model.applySession(state('a', { name: 'Leaf' }));
  model.applyList(list('a', [row('a', { name: 'Leaf' })]));
  fire(chip, 'click');
  fire(panel.querySelector('.session-all-link') as SessionNode, 'click');
  expect(opened).toEqual(['all']);
  expect(panel.hidden).toBe(true);
});

test('a session that could not be opened or started is said on the page, as the menu says it, and the page stays', () => {
  let online = false;
  let clock = NOW * 1000;
  const went: string[] = [];
  const model = new SessionsModel(() => online, () => clock);
  const root = dom.node('sessions-page', 'section');
  const view = new SessionsPage(root as unknown as HTMLElement, model, (hash) => went.push(hash));
  model.applySession(state('a', { name: 'Leaf' }));
  model.applyList(list('a', [row('a', { name: 'Leaf' }), row('b', { name: 'Blood smear', started: at(9), lastActive: at(12) })]));
  view.show();
  const error = () => root.querySelector('.session-error')!;
  expect(error().hidden).toBe(true);
  fire(view.newButton as unknown as SessionNode, 'click'); // offline
  expect(went).toEqual([]);
  expect(error().hidden).toBe(false);
  expect(error().textContent).toBe('Reconnect to the studio, then try again.');
  online = true;
  fire(card(root, 'b').querySelector('.session-card-open') as SessionNode, 'click');
  expect(error().hidden).toBe(true); // asked again: the old problem goes
  clock += SWITCH_WAIT_MS + 1;
  expect(model.expire()).toBe(true); // the server never echoed it
  expect(error().textContent).toBe('That project did not open. Try again.');
  expect(error().hidden).toBe(false);
});
