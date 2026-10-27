import { describe, expect, it } from 'vitest';
import { surfaceTrail } from './workspace-study-navigation';
describe('nested surface navigation', () => {
  it('unwinds Back without creating a reverse Back destination', () => {
    const detail = surfaceTrail([], '#models', '#models/bright');
    expect(detail).toEqual(['#models']);
    expect(surfaceTrail(detail, '#models/bright', '#models')).toEqual([]);
  });
  it('unwinds multiple details and supports Forward after Back', () => {
    expect(surfaceTrail(['#assets', '#asset/a'], '#measurements/e1', '#assets')).toEqual([]);
    expect(surfaceTrail([], '#assets', '#asset/a')).toEqual(['#assets']);
  });
});

import { ActivityNavigation } from './workspace-study-navigation';
import { vi } from 'vitest';
it('chat toggle and reply links use the current Activity owner, including close/reopen', () => {
  const legacy = {showing: () => false, show: vi.fn()};
  const navigation = new ActivityNavigation(legacy);
  let visible = false;
  const workspace = {showing: () => visible, show: vi.fn((open: boolean) => { visible = open; })};
  navigation.bind(workspace);
  navigation.toggle(); expect(visible).toBe(true);
  navigation.show(true); expect(visible).toBe(true); // reply detail never toggles closed
  navigation.toggle(); expect(visible).toBe(false);
  navigation.show(true); expect(visible).toBe(true);
  expect(legacy.show).not.toHaveBeenCalled();
});


import { afterEach } from 'vitest';
import { bindResearchStageRoute } from './workspace-study-navigation';
import { navigateStudio } from './studio-shell';
afterEach(() => vi.unstubAllGlobals());
it('keeps a mounted Study through the synchronous drawer-to-workspace route before its first draw', () => {
  const location = { hash: '#researchers', pathname: '/workspace.html', search: '' };
  vi.stubGlobal('location', location); vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('history', { pushState: (_state: unknown, _title: string, url: string) => { location.hash = url.startsWith('#') ? url : ''; } });
  let hasPage = true;
  const close = vi.fn(() => { hasPage = false; });
  const dispose = bindResearchStageRoute(() => hasPage, close);
  // openStudy has claimed its page, but draw(study) has not populated openStudyNow.
  navigateStudio('#');
  expect(close).not.toHaveBeenCalled(); expect(hasPage).toBe(true);
  // Legacy researcher details remain address-owned, and close when leaving their route.
  hasPage = false; navigateStudio('#research/legacy'); expect(close).not.toHaveBeenCalled();
  navigateStudio('#'); expect(close).toHaveBeenCalledTimes(1); dispose();
});

import { openResearchersDrawer, researchListRoute } from './workspace-study-navigation';
describe('Researchers keeps a researcher in front', () => {
  function stage(hash: string) {
    const location = { hash, pathname: '/workspace.html', search: '' };
    vi.stubGlobal('location', location); vi.stubGlobal('window', new EventTarget());
    vi.stubGlobal('history', { pushState: (_state: unknown, _title: string, url: string) => { location.hash = url.startsWith('#') ? url : ''; } });
    let visible = hash.startsWith('#research/');
    const close = vi.fn(() => { visible = false; });
    const dispose = bindResearchStageRoute(() => false, close);
    // workspace-study.ts setDrawer(false): closing the drawer leaves only its own list routes.
    const closeDrawer = () => { if (researchListRoute(location.hash)) navigateStudio('#'); };
    return { location, close, dispose, closeDrawer, visible: () => visible, open: () => { visible = true; } };
  }
  it('opening a row (or ⋯ › Open) closes the drawer and stays on the researcher it opened', () => {
    const s = stage('#researchers');
    navigateStudio('#research/r602534?day=2026-10-25'); s.open(); // the row's open, then openResearchStage
    s.closeDrawer(); // openResearchStage closes the Researchers drawer
    expect(s.location.hash).toBe('#research/r602534?day=2026-10-25');
    expect(s.close).not.toHaveBeenCalled(); expect(s.visible()).toBe(true);
    s.dispose();
  });
  it('Researchers on the rail over a researcher Tab opens the drawer and keeps the hash and the Tab', () => {
    const s = stage('#research/r1?day=2026-10-25');
    const show = vi.fn();
    openResearchersDrawer(false, (hash) => navigateStudio(hash), show);
    expect(show).toHaveBeenCalledOnce();
    expect(s.location.hash).toBe('#research/r1?day=2026-10-25');
    expect(s.close).not.toHaveBeenCalled(); expect(s.visible()).toBe(true);
    s.closeDrawer(); // pressing it again closes only the drawer
    expect(s.location.hash).toBe('#research/r1?day=2026-10-25');
    s.dispose();
  });
  it('with no researcher in front, Researchers takes its list route, and closing it leaves it', () => {
    const s = stage('');
    openResearchersDrawer(true, (hash) => navigateStudio(hash), () => {});
    expect(s.location.hash).toBe('#researchers');
    s.closeDrawer(); expect(s.location.hash).toBe('');
    for (const hash of ['#researchers', '#research', '#research?day=2026-10-10', '#researchers?x=1']) expect(researchListRoute(hash)).toBe(true);
    for (const hash of ['#research/r1', '#research/r1?day=2026-10-25.3', '#models', '']) expect(researchListRoute(hash)).toBe(false);
    s.dispose();
  });
});

import { escapeIsPopups } from './workspace-study-navigation';
it('Escape on a control whose popup is open is the popup\'s: the panel it sits in stays', () => {
  const chip = { getAttribute: (name: string) => (name === 'aria-expanded' ? 'true' : null) } as unknown as Element;
  const plain = { getAttribute: () => null } as unknown as Element;
  expect(escapeIsPopups(chip)).toBe(true);
  expect(escapeIsPopups(plain)).toBe(false);
  expect(escapeIsPopups(plain, () => true)).toBe(true); // the models popover open, focus elsewhere in it
  expect(escapeIsPopups(null)).toBe(false);
});

// Closing the drawer with its rail item or × (workspace-study.ts press: the layout is written, focus moves, then the
// sides move): focus is on the rail item at once, while the drawer slides out behind it, inert, and is hidden only
// when it has left (b041).
it('closing the drawer: focus moves at once; the drawer stays shown, inert, through its exit and is hidden after it', async () => {
  const { playSideMotion, sideTracks } = await import('./side-motion');
  vi.stubGlobal('document', { body: { dataset: {} }, hidden: false, activeElement: null as unknown });
  vi.stubGlobal('matchMedia', () => ({ matches: false }));
  const ends: (() => void)[] = [];
  const node = (name: string) => ({ name, hidden: false, inert: false, focus() { (document as { activeElement: unknown }).activeElement = this; },
    animate() { let done!: () => void; const finished = new Promise<void>((resolve) => { done = resolve; }); ends.push(() => done());
      return { startTime: null, finished, cancel() {}, effect: { getComputedTiming: () => ({ progress: 0 }) } }; } });
  const drawer = node('drawer'), rail = node('Inputs'), studio = node('studio');
  const open = { viewport: 1440, rail: 72, stage: { left: 412, right: 360 }, left: { node: drawer as unknown as HTMLElement, width: 340 }, right: null, under: 360 };
  const closed = { ...open, stage: { left: 72, right: 360 }, left: null };
  let drawerOpen = true;
  // The press: state, the one layout write (drawer.hidden), focus to the rail item, then the motion.
  drawerOpen = false; drawer.hidden = true; rail.focus();
  playSideMotion(sideTracks(open, closed, studio as unknown as HTMLElement, null), (n) => n === (drawer as unknown) && !drawerOpen);
  expect(document.activeElement).toBe(rail);
  expect(drawer.hidden).toBe(false); expect(drawer.inert).toBe(true);
  ends.forEach((end) => end()); for (let i = 0; i < 5; i++) await Promise.resolve();
  expect(drawer.hidden).toBe(true); expect(drawer.inert).toBe(false);
  expect(document.activeElement).toBe(rail);
});
