// The screen report (screen.ts): sent when what is shown changes, saying which changes were applied and
// which changes were the user's own (once).
import { describe, expect, test } from 'vitest';
import { observeLegacyForTest, rectOf, ScreenReporter, verifierLegacy, type ScreenItem, type WorkspaceObservation } from './screen';
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

// Hands' verifier (the live-partner spec 4.2): the studio's report says what each intent's outcome is checked against,
// within the existing screen check. The Project's open Tabs (focus, close a Tab), the outlines drawn now (highlight,
// clear), the population layer's members (outline). Bounded as workspace_observation._legacy checks them.
describe('the report carries what the verifier checks', () => {
  test('the open Tabs, in strip order, one in front; the outlines drawn; the layer\'s members', () => {
    const legacy = verifierLegacy({
      tabs: [{ id: 'main', title: 'Main', front: false }, { id: 't2', title: 'Measurements', front: true }],
      drawn: [{ id: 'o12', kind: 'highlight' }, { id: 'o3', kind: 'part' }],
      members: { count: 341, drawn: 290 },
    });
    expect(legacy).toEqual({ tabs: [{ id: 'main', title: 'Main', front: false }, { id: 't2', title: 'Measurements', front: true }],
      drawn: [{ id: 'o12', kind: 'highlight' }, { id: 'o3', kind: 'part' }], members: { count: 341, drawn: 290 } });
  });

  test('kept within the server\'s bounds: 24 Tabs, titles of 80, 60 outlines, drawn never over count', () => {
    const many = Array.from({ length: 30 }, (_, i) => ({ id: `t${i}`, title: 'x'.repeat(100), front: i === 29 }));
    const legacy = verifierLegacy({ tabs: many, drawn: Array.from({ length: 80 }, (_, i) => ({ id: `o${i}`, kind: 'part' })),
      members: { count: 10, drawn: 12 } });
    expect(legacy.tabs).toHaveLength(24);
    expect(legacy.tabs!.every((t) => t.title.length <= 80)).toBe(true);
    expect(legacy.tabs!.filter((t) => t.front)).toHaveLength(1); // the one in front is kept even past 24
    expect(legacy.drawn).toHaveLength(60);
    expect(legacy.members).toEqual({ count: 10, drawn: 10 });
  });

  test('the new fields pass the report\'s own closed check', () => {
    const observed = observeLegacyForTest({ schema: 'studio-surface-v1', scope: 'dom-observation',
      workspace: { project: 'p1', tab: 't2', tabTitle: 'Measurements', tabs: [{ id: 'main', title: 'Main', front: false }, { id: 't2', title: 'Measurements', front: true }] },
      drawn: [{ id: 'o12', kind: 'highlight' }], members: { count: 3, drawn: 2 } });
    expect(observed.workspace!.tabs).toHaveLength(2);
    expect(observed.drawn).toEqual([{ id: 'o12', kind: 'highlight' }]);
    expect(observed.members).toEqual({ count: 3, drawn: 2 });
    expect(() => observeLegacyForTest({ schema: 'studio-surface-v1', scope: 'dom-observation', members: { count: 1, drawn: 2 } })).toThrow();
  });

  // Each Tab's specimen (the live view): filling the Tab, floating with its place, or hidden (on-screen.md).
  test('each Tab carries its specimen: its state, and its place only when floating', () => {
    const legacy = verifierLegacy({ tabs: [{ id: 'main', title: 'Main', front: true, specimen: { state: 'fill', rect: [0, 0, 9, 9] } },
      { id: 't2', title: 'Stoma Reference', front: false, specimen: { state: 'float', rect: [16.4, 620.2, 240, 216] } },
      { id: 't3', title: 'Counts', front: false, specimen: { state: 'hidden' } }, { id: 't4', title: 'Older', front: false }] });
    expect(legacy.tabs!.map((t) => t.specimen)).toEqual([{ state: 'fill' }, { state: 'float', rect: [16, 620, 240, 216] }, { state: 'hidden' }, undefined]);
    const observed = observeLegacyForTest({ schema: 'studio-surface-v1', scope: 'dom-observation',
      workspace: { project: 'p1', tab: 'main', tabs: legacy.tabs } });
    expect(observed.workspace!.tabs![1].specimen).toEqual({ state: 'float', rect: [16, 620, 240, 216] });
    for (const specimen of [{ state: 'gone' }, { state: 'fill', rect: [0, 0, 1, 1] }, { state: 'float', rect: [0, 0, -1, 1] }, { state: 'float', where: 'left' }])
      expect(() => observeLegacyForTest({ schema: 'studio-surface-v1', scope: 'dom-observation',
        workspace: { project: 'p1', tab: 'main', tabs: [{ id: 'main', title: 'Main', front: true, specimen: specimen as never }] } })).toThrow();
  });
});
