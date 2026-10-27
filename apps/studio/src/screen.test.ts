// The screen report (screen.ts): sent when what is shown changes, saying which changes were applied and
// which changes were the user's own (once).
import { describe, expect, test } from 'vitest';
import { rectOf, ScreenReporter, type ScreenItem, type WorkspaceObservation } from './screen';
import type { ClientCommand } from './protocol';
import { SPACES } from './spaces';

describe('screen report', () => {
  test('a report goes out when what is shown changes, with the applied changes and the user\'s own', () => {
    const sent: ClientCommand[] = [];
    let items: ScreenItem[] = [{ id: 'live', kind: 'live', opacity: 1 }];
    const reporter = new ScreenReporter((c) => sent.push(c), () => items, 100, 10_000);
    reporter.tick(0);
    reporter.tick(150); // nothing changed
    expect(sent.length).toBe(1);
    items = [{ id: 'live', kind: 'live', opacity: 0.35 }];
    reporter.markApplied('c7');
    reporter.userChanged('magnifier', 'rect');
    reporter.tick(300);
    expect(sent.length).toBe(2);
    const last = sent[1];
    if (last.type !== 'screen' || 'schema' in last) throw new Error('Expected the legacy screen report');
    expect(last.applied).toEqual(['c7']);
    expect(last.user).toEqual([{ id: 'magnifier', prop: 'rect' }]);
    expect(last.items[0].opacity).toBe(0.35);
    reporter.tick(500);
    expect(sent.length).toBe(2); // the user's change is said once; nothing new since
  });

  test('rects are [x, y, width, height]', () => {
    expect(rectOf({ left: 10, top: 20, right: 110, bottom: 70 })).toEqual([10, 20, 100, 50]);
    expect(rectOf({ left: 10, top: 20, right: 10, bottom: 70 })).toBeNull();
  });

  test('every item of a report states the space of its contents, whatever the kind', () => {
    const sent: ClientCommand[] = [];
    const kinds = ['live', 'population', 'tracks', 'outlines', 'magnifier', 'viewer', 'slidemap', 'card', 'made-today'];
    const items: ScreenItem[] = kinds.map((kind) => ({ id: kind, kind }));
    items.push({ id: 'm2', kind: 'magnifier', source: 'held' }, { id: 'm3', kind: 'magnifier', source: '/research/x.png' },
      { id: 'own', kind: 'viewer', space: 'workspace' });
    new ScreenReporter((c) => sent.push(c), () => items, 100, 10_000).tick(150);
    const report = sent[0];
    if (report.type !== 'screen' || 'schema' in report) throw new Error('Expected the legacy screen report');
    const reported = report.items;
    expect(reported.filter((i) => !i.space || !SPACES.includes(i.space)).map((i) => i.id)).toEqual([]);
    const by = Object.fromEntries(reported.map((i) => [i.id, i.space]));
    expect(by).toMatchObject({ live: 'frame', population: 'capture', outlines: 'frame', magnifier: 'frame', m2: 'capture',
      m3: 'picture', viewer: 'picture', slidemap: 'map', card: 'workspace', own: 'workspace' });
  });
});

// The side bar is on the screen report as kind `sidebar` (the voice knows it is open, and which section it shows), and
// the server's `present` opens, closes or hides it; `sessions` is an alias for it on its Sessions section ("close the
// sessions list").
import { afterEach as afterEachTest, vi as viTest } from 'vitest';
import { SideBar } from './sidebar';
import { SessionsModel } from './sessions';
import { list, NOW, row, sessionDOM, SessionNode, state } from './sessions.test-support';

describe('the side bar on the screen', () => {
  afterEachTest(() => viTest.unstubAllGlobals());
  function bar() {
    const dom = sessionDOM();
    dom.storage.set('studio-sessions-rail', ''); // no section open: Actions alone (a first visit opens on Assets)
    const model = new SessionsModel(() => true, () => NOW * 1000);
    model.applySession(state('2026-10-12.2'));
    model.applyList(list('2026-10-12.2', [row('2026-10-12.2')]));
    const stage = dom.node('stage', 'main');
    const root = new SessionNode('aside');
    root.id = 'session-rail'; root.style.width = '280px'; root.style.height = '700px';
    stage.append(root);
    return new SideBar(model, root as unknown as HTMLElement, { phone: () => false });
  }

  test('reported as kind sidebar: open or not, where, and the section it shows (titled by its name); its contents are the workspace\'s', () => {
    const view = bar();
    const sent: ClientCommand[] = [];
    let items: ScreenItem[] = [view.screenItem()];
    const reporter = new ScreenReporter((c) => sent.push(c), () => items, 100, 10_000);
    reporter.tick(150);
    let item = (sent[0] as Extract<ClientCommand, { type: 'screen' }>).items[0];
    // Actions is always open on the desktop: the side bar is on screen, showing Actions, with no section open above it.
    expect(item).toMatchObject({ id: 'sidebar', kind: 'sidebar', open: true, visible: true, rect: [0, 0, 280, 700], shows: 'actions', title: 'Actions', space: 'workspace' });
    view.setOpen(true);
    items = [view.screenItem()];
    reporter.tick(300);
    item = (sent[1] as Extract<ClientCommand, { type: 'screen' }>).items[0];
    expect(item).toMatchObject({ id: 'sidebar', kind: 'sidebar', open: true, visible: true, rect: [0, 0, 280, 700], shows: 'sources', title: 'Assets' });
    view.open('activity');
    expect(view.screenItem()).toMatchObject({ shows: 'activity', title: 'Activity', open: true });
  });

  test('present: open (on the section it names), close and hide; anything else is not its', () => {
    const view = bar();
    expect(view.applyPresent({ open: true })).toBe(true);
    expect(view.isOpen()).toBe(true);
    expect(view.applyPresent({ open: true, shows: 'made' })).toBe(true);
    expect(view.screenItem().shows).toBe('made');
    expect(view.applyPresent({ open: false })).toBe(true);
    expect(view.isOpen()).toBe(false);
    view.applyPresent({ open: true });
    expect(view.section()).toBe('made'); // opened again on the section it had
    expect(view.applyPresent({ visible: false })).toBe(true);
    expect(view.isOpen()).toBe(false);
    expect(view.applyPresent({ opacity: 0.5 })).toBe(false);
    expect(view.screenItem()).toMatchObject({ open: false }); // hidden as the agent asked: the report says so
  });

});

describe('the accepted workspace report', () => {
  test('a page with no layout size yet sends nothing, then reports once it has one (Window C, 2026-10-14)', () => {
    // A fresh profile's first load in a hidden or zero-size pane: innerWidth and innerHeight are 0, and the app refuses
    // that report ("INVALID_GEOMETRY: dimensions must be positive"), which reached Activity as a raw notice.
    const sent: ClientCommand[] = [];
    let layoutSize = [0, 0];
    const reporter = new ScreenReporter((c) => sent.push(c), () => [], 100, 10_000);
    reporter.useWorkspace(({ seq, nowMs }) => ({ type: 'screen', seq, capturedMonoMs: nowMs, items: [], interaction: {},
      commandResults: [], viewport: { layoutSize, visualRect: [0, 0, ...layoutSize], visualScale: 1, documentScroll: [0, 0],
        dpr: 2, visible: true, documentFocused: true } }) as unknown as WorkspaceObservation);
    reporter.tick(150);
    reporter.tick(300);
    expect(sent).toEqual([]);
    layoutSize = [1440, 900];
    reporter.tick(450);
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ type: 'screen', seq: 1, viewport: { layoutSize: [1440, 900] } });
  });
});
