// The side bar is the session (sidebar.ts) over a synthetic DOM: four toggles in a strip under the session's name
// (Assets first: this session's), one section open at a time (its toggle again closes it, another switches), the open
// section remembered per viewer (the rail's old values are no section now), the counts, the room it takes, Escape, the
// phone's sheet whose sections are tabs, and the Researchers' compact cards.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { SideBar, SPLIT_MAX, SPLIT_MIN, storedActions, storedSection, toggleLabel, type SideSection } from './sidebar';
import { SessionsModel } from './sessions';
import { PanelStack } from './panels';
import { ResearcherCards } from './researcher-cards';
import type { PresenterState, ResearchSession } from './protocol';
import { fire } from './workspace-windows.test-support';
import { list, NOW, row, sessionDOM, SessionNode, state, type SessionDOM } from './sessions.test-support';

vi.mock('./research', () => ({ clean: (value: string) => value }));
vi.mock('./viewer', () => ({ outputOf: () => undefined }));

let dom: SessionDOM;
beforeEach(() => { dom = sessionDOM(); vi.useFakeTimers(); vi.setSystemTime(NOW * 1000); });
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function bar(options: { phone?: boolean; firstVisit?: boolean } = {}) {
  // Most of these start from a closed section (a first visit opens on Assets: its own test).
  if (!options.firstVisit && !dom.storage.has('studio-sessions-rail')) dom.storage.set('studio-sessions-rail', '');
  const model = new SessionsModel(() => true, () => NOW * 1000);
  const stage = dom.node('stage', 'main');
  stage.style.width = '1000px'; stage.style.height = '700px';
  const root = new SessionNode('aside');
  root.id = 'session-rail';
  stage.append(root);
  const toolbar = dom.node('conversation-toolbar');
  const user: string[] = [];
  const shown: SideSection[] = [];
  const view = new SideBar(model, root as unknown as HTMLElement, { phone: () => !!options.phone, toolbar: toolbar as unknown as HTMLElement,
    onUserChange: (p) => user.push(p), onSection: (s) => shown.push(s) });
  return { model, root, stage, toolbar, view, user, shown };
}
const toggle = (view: SideBar, section: SideSection) => view.toggles.get(section)! as unknown as SessionNode;
const visibleSection = (root: SessionNode) =>
  (root.querySelectorAll('.sidebar-section') as SessionNode[]).filter((s) => !s.hidden).map((s) => s.dataset.section);

describe('the strip and its sections', () => {
  test("closed: four toggles stacked, this session's Assets first; a toggle opens its section, again closes it, another switches", () => {
    const { view, root, user, shown } = bar();
    expect([...view.toggles.keys()]).toEqual(['sources', 'made', 'researchers', 'activity']);
    expect([...view.toggles.values()].map((t) => (t as unknown as SessionNode).querySelector('.sidebar-toggle-label')!.textContent))
      .toEqual(['Assets', 'Made', 'AI research', 'Activity']);
    expect(view.isOpen()).toBe(false);
    expect(visibleSection(root)).toEqual([]);
    expect([...view.toggles.values()].map((t) => t.getAttribute('aria-expanded'))).toEqual(['false', 'false', 'false', 'false']);
    fire(toggle(view, 'made'), 'click');
    expect(view.isOpen()).toBe(true);
    expect(view.section()).toBe('made');
    expect(visibleSection(root)).toEqual(['made']);
    expect(toggle(view, 'made').getAttribute('aria-expanded')).toBe('true');
    expect(root.querySelector('.sidebar-title')!.textContent).toBe('Made in this project');
    fire(toggle(view, 'activity'), 'click'); // another: switches
    expect(view.isOpen()).toBe(true);
    expect(visibleSection(root)).toEqual(['activity']);
    expect(toggle(view, 'made').getAttribute('aria-expanded')).toBe('false');
    fire(toggle(view, 'activity'), 'click'); // its own again: closes
    expect(view.isOpen()).toBe(false);
    expect(view.section()).toBe('activity'); // and opens on it next
    expect(user).toEqual(['open', 'shows', 'open']);
    expect(shown).toEqual(['made', 'activity']);
    // This session's assets: its own content root (sources.ts fills it), said "Assets" everywhere.
    fire(toggle(view, 'sources'), 'click');
    expect(visibleSection(root)).toEqual(['sources']);
    expect(root.querySelector('.sidebar-title')!.textContent).toBe('Assets');
    expect(view.content('sources').id).toBe('sidebar-sources');
    expect(toggle(view, 'sources').id).toBe('sidebar-toggle-sources');
    expect(view.screenItem()).toMatchObject({ kind: 'sidebar', title: 'Assets', shows: 'sources' });
    // Every section has its own content root, filled by its own view.
    expect(['made', 'researchers', 'activity'].map((s) => view.content(s as SideSection).id)).toEqual(['sidebar-made', 'sidebar-researchers', 'sidebar-activity']);
  });

  test("the session's name heads the column: the side bar is this session's", () => {
    const { model, root, view } = bar();
    model.applySession(state('a', { name: 'Leaf epidermis' }));
    model.applyList(list('a', [row('a', { name: 'Leaf epidermis' })]));
    view.open('made');
    expect(root.querySelector('.sidebar-session')!.textContent).toBe('Leaf epidermis');
    expect((root.querySelector('.sidebar-panel') as SessionNode).getAttribute('aria-label')).toBe('Leaf epidermis: Made in this project');
    model.applySession(state('b', { name: 'Blood smear' }));
    model.applyList(list('b', [row('b', { name: 'Blood smear' })]));
    expect(root.querySelector('.sidebar-session')!.textContent).toBe('Blood smear');
  });

  test("the open section is remembered for this viewer; the rail's old values are no section now; broken storage leaves it closed", () => {
    const first = bar();
    fire(toggle(first.view, 'researchers'), 'click');
    expect(dom.storage.get('studio-sessions-rail')).toBe('researchers');
    dom.byId.clear();
    const again = bar(); // a reload
    expect(again.view.isOpen()).toBe(true);
    expect(again.view.section()).toBe('researchers');
    again.view.close();
    expect(dom.storage.get('studio-sessions-rail')).toBe('');
    expect(storedSection()).toBeNull();
    for (const old of ['1', 'sessions', 'assets', 'nonsense']) {
      dom.storage.set('studio-sessions-rail', old);
      expect(storedSection()).toBeNull();
    }
    vi.stubGlobal('localStorage', { getItem: () => { throw new Error('denied'); }, setItem: () => { throw new Error('denied'); } });
    const denied = bar();
    expect(visibleSection(denied.root)).toEqual([]); // no section open...
    expect(denied.view.actionsShown()).toBe(true); // ...but Actions is: the view's tools are there
    fire(toggle(denied.view, 'made'), 'click');
    expect(visibleSection(denied.root)).toEqual(['made']);
  });

  test('a section lights up for a moment when something joins it; its tooltip says what it holds', () => {
    const { view } = bar();
    view.flash('sources');
    expect(toggle(view, 'sources').dataset.flash).toBe('true');
    vi.advanceTimersByTime(2000);
    expect(toggle(view, 'sources').dataset.flash).toBeUndefined();
    expect(toggle(view, 'sources').title).toContain('pictures, recordings and snapshots');
    expect(toggle(view, 'made').title).toContain('what the app worked out');
  });

  test('each toggle carries its count, coloured when it needs you, and says it in its name', () => {
    const { view } = bar();
    view.count('researchers', 3, 'asking');
    const badge = toggle(view, 'researchers').querySelector('.sidebar-count') as SessionNode;
    expect(badge.textContent).toBe('3');
    expect(badge.hidden).toBe(false);
    expect(badge.dataset.state).toBe('asking');
    expect(toggle(view, 'researchers').getAttribute('aria-label')).toBe('AI researchers, 3, a researcher is asking you something');
    view.count('activity', 0);
    expect((toggle(view, 'activity').querySelector('.sidebar-count') as SessionNode).hidden).toBe(true);
    expect(toggleLabel('made', 12, 'fresh')).toBe('Made in this project, 12, new work ready');
    // A redraw keeps what it says.
    view.setOpen(true);
    expect(toggle(view, 'researchers').getAttribute('aria-label')).toBe('AI researchers, 3, a researcher is asking you something');
  });

  test('the room it takes: open (a section or Actions), the eyepiece glides right of the column', () => {
    const { root, view, stage } = bar();
    root.style.width = '364px';
    const box = stage.getBoundingClientRect();
    expect(view.reserves(box)).toEqual({ left: 372, edge: 372 });
    view.open('made');
    expect(view.reserves(box)).toEqual({ left: 372, edge: 372 });
    expect(view.footprint()).toMatchObject({ left: 0, right: 364 });
    root.hidden = true; // another page: the stage is not shown
    expect(view.reserves(box)).toEqual({});
  });

  test('Escape closes it, through the panel stack or inside it, and the focus goes back to its toggle', () => {
    const { view } = bar();
    const stack = new PanelStack();
    stack.register(view);
    view.open('activity');
    expect(stack.top()).toBe(view);
    expect(stack.onEscape({ key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} })).toBe(true);
    expect(view.isOpen()).toBe(false);
    view.open('activity', true);
    const close = view.closeButton as unknown as SessionNode;
    expect(dom.key(close, 'Escape').prevented).toBe(true);
    expect(view.isOpen()).toBe(false);
    expect(SessionNode.active).toBe(toggle(view, 'activity'));
  });

  test('present: open (on the section it names), close and hide; anything else is not its', () => {
    const { view } = bar();
    expect(view.applyPresent({ open: true, shows: 'researchers' })).toBe(true);
    expect(view.screenItem()).toMatchObject({ open: true, shows: 'researchers', title: 'AI researchers' });
    expect(view.applyPresent({ open: true, shows: 'sessions' })).toBe(true); // no section of its: the one it had
    expect(view.section()).toBe('researchers');
    expect(view.applyPresent({ visible: false })).toBe(true);
    expect(view.isOpen()).toBe(false);
    expect(view.screenItem()).toMatchObject({ open: false, visible: false }); // the whole side bar hid: what was asked
    expect(view.applyPresent({ open: true })).toBe(true);
    expect(view.screenItem()).toMatchObject({ open: true, shows: 'researchers' });
    expect(view.applyPresent({ opacity: 0.5 })).toBe(false);
  });

  test('on a phone, opening a section while the Actions tab shows takes that tab off: one tab at a time', () => {
    const { view, root } = bar({ phone: true });
    fire(view.actionsToggle as unknown as SessionNode, 'click');
    expect(view.actionsShown()).toBe(true);
    view.open('made');
    expect(view.actionsShown()).toBe(false);
    expect(visibleSection(root)).toEqual(['made']);
    expect(view.showing('made')).toBe(true);
    fire(view.actionsToggle as unknown as SessionNode, 'click');
    expect(view.showing('made')).toBe(false); // behind the Actions tab: not what shows
  });
});

describe('a first visit, and hiding it as the chat hides', () => {
  test("a first visit opens on this session's Assets, Actions below taking 30%", () => {
    const { view, root } = bar({ firstVisit: true });
    expect(visibleSection(root)).toEqual(['sources']);
    expect(view.actionsShown()).toBe(true);
    expect(view.splitShare()).toBe(0.7);
    expect(root.style.gridTemplateRows).toBe(`auto minmax(0, 0.7fr) auto minmax(0, ${1 - 0.7}fr)`);
  });

  test('‹ hides it to the slim rail; › brings back exactly what was open; nothing open before, Assets with Actions', () => {
    const { view, root, user } = bar({ firstVisit: true });
    fire(toggle(view, 'made'), 'click');
    view.setSplit(0.6);
    expect(view.hideButton.getAttribute('aria-label')).toBe('Hide the side bar');
    fire(view.hideButton as unknown as SessionNode, 'click');
    expect(view.shown()).toBe(false);
    expect(root.dataset.open).toBe('false');
    expect(view.hideButton.getAttribute('aria-label')).toBe('Show the side bar');
    expect(view.hideButton.dataset.shows).toBe('false');
    fire(view.hideButton as unknown as SessionNode, 'click');
    expect(visibleSection(root)).toEqual(['made']);
    expect(view.actionsShown()).toBe(true);
    expect(view.splitShare()).toBe(0.6);
    expect(user).toEqual(['shows', 'open', 'open']); // the switch, the hide and the show: all the user's own
    // Hidden with only Actions open: only Actions comes back.
    fire(toggle(view, 'made'), 'click');
    view.hide(true);
    view.unhide(true);
    expect(visibleSection(root)).toEqual([]);
    expect(view.actionsShown()).toBe(true);
    // Never hidden, closed some other way: › brings back the section it had, with Actions.
    dom.storage.delete('studio-sidebar-last');
    view.setActions(false);
    expect(view.shown()).toBe(false);
    view.unhide(true);
    expect(visibleSection(root)).toEqual(['made']);
    expect(view.actionsShown()).toBe(true);
  });

  test('a phone’s sheet has no hide: its button in the toolbar is its open and close', () => {
    const { view } = bar({ phone: true });
    expect(view.hideButton.hidden).toBe(true);
  });
});

describe('the phone', () => {
  test("a button in the conversation's toolbar opens a sheet whose four sections are tabs; a tab switches and never closes it", () => {
    dom.storage.set('studio-sessions-rail', 'made'); // open on the desktop: a phone's sheet never opens by itself
    const { view, root, toolbar, stage } = bar({ phone: true });
    expect(view.isOpen()).toBe(false);
    expect(root.dataset.sheet).toBe('true');
    expect(toolbar.querySelector('.session-rail-phone')).toBe(view.phoneButton);
    expect(view.phoneButton.textContent).toBe('This project…');
    fire(view.phoneButton as unknown as SessionNode, 'click');
    expect(view.isOpen()).toBe(true);
    expect(view.phoneButton.getAttribute('aria-expanded')).toBe('true');
    expect((root.querySelectorAll('.sidebar-toggle') as SessionNode[]).length).toBe(5); // and Actions, a fifth tab
    fire(toggle(view, 'sources'), 'click');
    expect(visibleSection(root)).toEqual(['sources']);
    fire(toggle(view, 'researchers'), 'click');
    expect(visibleSection(root)).toEqual(['researchers']);
    fire(toggle(view, 'researchers'), 'click'); // the open tab: the sheet stays
    expect(view.isOpen()).toBe(true);
    expect(view.reserves(stage.getBoundingClientRect())).toEqual({}); // a sheet over the page takes no room
    fire(view.phoneButton as unknown as SessionNode, 'click');
    expect(view.isOpen()).toBe(false);
    expect(dom.storage.get('studio-sessions-rail')).toBe('made'); // the desktop's choice is left as it was
  });

  test('narrowed to a phone it closes without forgetting; widened again, it is as it was', () => {
    let phone = false;
    const model = new SessionsModel(() => true, () => NOW * 1000);
    const root = new SessionNode('aside');
    dom.node('stage', 'main').append(root);
    const view = new SideBar(model, root as unknown as HTMLElement, { phone: () => phone });
    view.open('activity', true);
    phone = true; view.frame();
    expect(view.isOpen()).toBe(false);
    phone = false; view.frame();
    expect(view.isOpen()).toBe(true);
    expect(view.section()).toBe('activity');
  });
});

const T = NOW;
function researcher(rid: string, status: ResearchSession['status'], extra: Partial<ResearchSession> = {}): ResearchSession {
  return { rid, n: Number(rid.slice(1)) || 1, name: `Researcher ${rid.slice(1)}`, brief: `Look into ${rid}`, deliverable: null,
    effort: 'quick', engine: 'test', purpose: 'research', label: '', objectId: null, status, step: '', spent: 0, cap: 0.25,
    seconds: 100, created: T - 60, started: T - 25, finished: null, snapshot: `/research/s1/${rid}/inputs/frame.jpg`,
    outputs: [], items: [], findings: [], corrections: [], history: [], canFollowUp: true, sitting: 's1', day: 's1',
    result: null as unknown as ResearchSession['result'], ...extra } as ResearchSession;
}

describe("Researchers: this session's cards, compact", () => {
  test("this session's researchers, then those still working elsewhere; a click opens the work", () => {
    const { view } = bar();
    const opened: string[] = [];
    const cards = new ResearcherCards((rid) => opened.push(rid), undefined, () => false, view.content('researchers'));
    const sessions = [
      researcher('r1', 'done', { result: { title: 'Guard cells' } as ResearchSession['result'], outputs: [{ kind: 'image' } as never] }),
      researcher('r2', 'running', { created: T - 10 }),
      researcher('r3', 'running', { sitting: 's0', created: T - 30 }), // started in another session
      researcher('r4', 'running', { purpose: 'description', label: 'Quick lookup' }),
    ];
    const presenter = { type: 'presenter', open: false, rid: null, index: 0, shelfRemoved: [] } as unknown as PresenterState;
    cards.update(sessions, presenter, 's1');
    const order = ((view.content('researchers') as unknown as SessionNode).children as SessionNode[])
      .map((n) => (n.classList.contains('researcher-elsewhere') ? '— elsewhere' : n.dataset.rid));
    expect(order).toEqual(['r2', 'r1', '— elsewhere', 'r3']);
    expect(cards.count()).toBe(3); // research only (the one elsewhere too), never the descriptions' quick lookups
    const card = ((view.content('researchers') as unknown as SessionNode).children as SessionNode[]).find((n) => n.dataset.rid === 'r1')!;
    // Its picture carries its lit cell on the corner (green: its work is ready), the same as its row and pill; no ring.
    const cell = card.querySelector('.researcher-thumb')!.querySelector('[data-kind="lit-cell"]')!;
    expect(cell.getAttribute('data-state')).toBe('ready');
    const working = ((view.content('researchers') as unknown as SessionNode).children as SessionNode[]).find((n) => n.dataset.rid === 'r2')!;
    expect(working.querySelector('.researcher-thumb')!.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('working');
    expect('spin' in (working.querySelector('.researcher-thumb') as SessionNode).dataset).toBe(false);
    fire(card.querySelector('.researcher-card-main') as SessionNode, 'click');
    expect(opened).toEqual(['r1']);
    expect((view.content('researchers') as unknown as SessionNode).classList.contains('researcher-cards-compact')).toBe(true);
  });
});

describe('Actions, the lower half', () => {
  const actionsBox = (root: SessionNode) => root.querySelector('.sidebar-actions-box') as SessionNode;
  const divider = (root: SessionNode) => root.querySelector('.sidebar-divider') as SessionNode;
  const press = (root: SessionNode, from: number, to = from) => {
    fire(divider(root), 'pointerdown', { clientY: from, pointerId: 1 });
    if (to !== from) fire(divider(root), 'pointermove', { clientY: to, pointerId: 1 });
    fire(divider(root), 'pointerup', { clientY: to, pointerId: 1 });
  };

  test('open unless this viewer closed it; its bar names it, and a click on the bar closes it to the bar and opens it again', () => {
    expect(storedActions()).toBe(true); // never set: open (the view's tools are in it)
    const { view, root, user } = bar();
    fire(toggle(view, 'made'), 'click');
    expect(view.actionsShown()).toBe(true);
    expect(actionsBox(root).hidden).toBe(false);
    expect(view.actions().id).toBe('sidebar-actions');
    expect(view.actionsToggle.hidden).toBe(true); // open, its bar is its toggle
    expect(root.querySelector('#sidebar-actions-close')).toBeNull();
    expect(divider(root).querySelector('.sidebar-title')!.textContent).toBe('Actions');
    expect(divider(root).dataset.drags).toBe('true');
    press(root, 400); // a click
    expect(view.actionsShown()).toBe(false);
    expect(actionsBox(root).hidden).toBe(true);
    expect(divider(root).hidden).toBe(false); // the quiet bar at the foot
    expect(divider(root).dataset.collapsed).toBe('true');
    expect(dom.storage.get('studio-actions')).toBe('closed');
    fire(divider(root), 'keydown', { key: 'Enter' });
    expect(view.actionsShown()).toBe(true);
    expect(dom.storage.get('studio-actions')).toBe('open');
    expect(user).toEqual(['open', 'open', 'open']);
  });

  test('a cancelled pointer (a system gesture took it) changes nothing', () => {
    const { view, root } = bar();
    fire(toggle(view, 'made'), 'click');
    fire(divider(root), 'pointerdown', { clientY: 400, pointerId: 1 });
    fire(divider(root), 'pointercancel', { clientY: 400, pointerId: 1 });
    expect(view.actionsShown()).toBe(true);
    fire(divider(root), 'pointerup', { clientY: 400, pointerId: 1 }); // its listeners are gone with the cancel
    expect(view.actionsShown()).toBe(true);
  });

  test('the bar follows the pointer; dragged all the way down it closes, keeping the height it had; dragged up it opens', () => {
    const { view, root } = bar();
    root.style.height = '1000px';
    fire(toggle(view, 'made'), 'click');
    view.setSplit(0.6);
    // The bar moves as far as the pointer over the height the two halves share (the column less the tab row and the bar).
    const shared = 1000 - view.strip.getBoundingClientRect().height - divider(root).getBoundingClientRect().height;
    press(root, 600, 600 + 0.1 * shared); // a tenth of that height down: a tenth more for the section
    expect(view.splitShare()).toBeCloseTo(0.7);
    press(root, 700, 700 + 0.3 * shared); // past SPLIT_CLOSE: closes, the height kept
    expect(view.actionsShown()).toBe(false);
    expect(view.splitShare()).toBeCloseTo(0.7);
    press(root, 980, 980 - 0.45 * shared); // closed, the bar is at the foot (share 1): up again opens it there
    expect(view.actionsShown()).toBe(true);
    expect(view.splitShare()).toBeCloseTo(0.55);
  });

  test('with a section open too, the bar shares the height: arrow keys within limits, remembered; down past them closes it', () => {
    const { view, root } = bar();
    fire(toggle(view, 'made'), 'click');
    expect(visibleSection(root)).toEqual(['made']);
    expect(view.splitShare()).toBe(0.7); // Actions takes 30% by default
    view.setSplit(0.95);
    expect(view.splitShare()).toBe(SPLIT_MAX);
    view.setSplit(0);
    expect(view.splitShare()).toBe(SPLIT_MIN);
    view.setSplit(0.6);
    expect(dom.storage.get('studio-actions-split')).toBe('0.6');
    expect(divider(root).getAttribute('aria-valuenow')).toBe('60');
    fire(divider(root), 'keydown', { key: 'ArrowDown' });
    expect(view.splitShare()).toBeCloseTo(0.65);
    view.setSplit(SPLIT_MAX);
    fire(divider(root), 'keydown', { key: 'ArrowDown' });
    expect(view.actionsShown()).toBe(false);
    fire(divider(root), 'keydown', { key: 'ArrowUp' });
    expect(view.actionsShown()).toBe(true);
  });

  test('the divider is a separator the keyboard moves both ways in steps of 5%, its aria-valuenow following, within its limits', () => {
    const { view, root } = bar();
    fire(toggle(view, 'made'), 'click');
    expect(divider(root).getAttribute('role')).toBe('separator');
    expect(divider(root).getAttribute('aria-valuemin')).toBe(String(SPLIT_MIN * 100));
    expect(divider(root).getAttribute('aria-valuemax')).toBe(String(SPLIT_MAX * 100));
    expect(divider(root).getAttribute('aria-valuenow')).toBe('70'); // Actions takes 30% by default
    fire(divider(root), 'keydown', { key: 'ArrowUp' });
    expect(view.splitShare()).toBeCloseTo(0.65);
    expect(divider(root).getAttribute('aria-valuenow')).toBe('65');
    fire(divider(root), 'keydown', { key: 'ArrowDown' });
    expect(divider(root).getAttribute('aria-valuenow')).toBe('70');
    expect(dom.storage.get('studio-actions-split')).toBe(String(view.splitShare())); // remembered as moved
    view.setSplit(SPLIT_MIN);
    fire(divider(root), 'keydown', { key: 'ArrowUp' }); // never past its top limit
    expect(view.splitShare()).toBe(SPLIT_MIN);
    expect(divider(root).getAttribute('aria-valuenow')).toBe(String(SPLIT_MIN * 100));
    expect(view.actionsShown()).toBe(true);
    fire(divider(root), 'keydown', { key: 'ArrowLeft' }); // not its keys: nothing moves
    expect(view.splitShare()).toBe(SPLIT_MIN);
  });

  test('with both closed the side bar is the slim rail again, Actions a toggle at its foot', () => {
    const { view, root } = bar();
    press(root, 400); // no section open: closing Actions closes the column
    expect(view.shown()).toBe(false);
    expect(root.dataset.open).toBe('false');
    expect(divider(root).hidden).toBe(true);
    expect(view.actionsToggle.hidden).toBe(false);
    expect(view.screenItem()).toMatchObject({ open: false });
    fire(view.actionsToggle as unknown as SessionNode, 'click');
    expect(view.actionsShown()).toBe(true);
    expect(view.actionsToggle.hidden).toBe(true);
  });

  test('the server can open it (present open on the side bar showing actions)', () => {
    dom.storage.set('studio-actions', 'closed');
    const { view } = bar();
    expect(view.actionsShown()).toBe(false);
    expect(view.applyPresent({ open: true, shows: 'actions' })).toBe(true);
    expect(view.actionsShown()).toBe(true);
  });

  test('on a phone it is a fifth tab: one tab shows at a time', () => {
    const { view, root } = bar({ phone: true });
    expect(view.isOpen()).toBe(false); // a phone's sheet never opens by itself
    expect(view.actionsToggle.hidden).toBe(false);
    expect(divider(root).hidden).toBe(true);
    fire(view.actionsToggle as unknown as SessionNode, 'click');
    expect(view.actionsShown()).toBe(true);
    expect(visibleSection(root)).toEqual([]);
    fire(toggle(view, 'made'), 'click'); // another tab
    expect(view.actionsShown()).toBe(false);
    expect(visibleSection(root)).toEqual(['made']);
    fire(view.actionsToggle as unknown as SessionNode, 'click');
    expect(view.actionsShown()).toBe(true);
    expect(visibleSection(root)).toEqual([]);
    view.close();
    expect(view.shown()).toBe(false);
  });
});
