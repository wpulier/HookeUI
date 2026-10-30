// TEST-ONLY adapter contract. Synthetic DOM/decoder/frame evidence, never browser pixels.
// Missing renderer/fit/FloatingWindow is feature-absence RED; no canned production substitute.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { PanelStack } from './panels';
import { fire, settleMicrotasks, syntheticDOM, TestElement, TestImage } from './workspace-windows.test-support';

type Source = {
  key: string; kind: string; media: string; availability: string;
  identity: Record<string, unknown>; provenance: Record<string, unknown>;
};
type Origin = { workspaceId: string; clientId: string; pageEpoch: string; connectionEpoch: string; appRunId: string };
type State = {
  id: string; workspaceId: string; source: Source; sourceGeneration: number; lifetimeGeneration: string;
  revision: number; open: boolean; title: string;
  geometryIntent: { position: number[]; size: number[] | null; relation?: { kind: string; reference: string; gapPx: number; side?: 'left' | 'right' } }; view: { zoom: number; pan: number[] };
  propertyVersions: Record<string, number>; propertyOwners: Record<string, string>;
  delivery: { sourceToken: string; url: string; disc?: number[] | null; text?: string; report?: import('./protocol').ResearchSession['result'] };
};
type Manual = {
  type: 'present'; action: string; instance: string; values: Record<string, unknown>;
  context: Origin; requestId: string; commandId: string; actionOccurrenceId: string;
  base: { sourceGeneration: number; lifetimeGeneration: string; propertyVersions: Record<string, number> };
};
type Facts = {
  id: string; rootId: string; context: Origin; source: Source; sourceGeneration: number; lifetimeGeneration: string;
  propertyVersions: Record<string, number>; view: { zoom: number; pan: number[] }; rect: number[]; contentRect: number[];
  commandId: string; sourceToken: string; load: { state: string; drawToken: string | null; naturalSize: number[] | null; error?: string };
  visible: boolean; capability?: string; limitation?: string;
};
type Shell = { frame(): void; front(byUser?: boolean): number; moveTo(x: number, y: number): void; resizeTo(width: number, height?: number): void; close(): void };
type Renderer = {
  apply(batch: { context: Origin; requestId: string; commandId: string; actionOccurrenceId: string; items: State[]; op?: string }): void;
  root(id: string): HTMLElement; floating(id: string): Shell; facts(): Facts[];
  dispatchUser(action: { action: string; instance: string; values: Record<string, unknown> }): void;
  rejectManual(instance: string, reason?: string): void;
  dispose(): void;
  viewportChanged(): void;
  retainedPresentation(): { views: import('./workspace-study-restoration').StudyRetainedView[]; missing: string[] };
  restore(id: string, foreground?: boolean): void;
  restorePresentation(saved: import('./workspace-study-restoration').StudyRetainedView): boolean;
  frontPresentation(saved: import('./workspace-study-restoration').StudyRetainedView): boolean;
};
type Factory = new (options: {
  background?: (requestId:string)=>boolean;
  host: HTMLElement; panels: PanelStack; context: Origin; send(command: Manual): void;
  onPresentation?: (id: string, root: HTMLElement, title: () => string, foreground: boolean) => void;
  facts(): { canvas: DOMRect; area: { width: number; height: number }; disc: null; object: null; labels: [] };
}) => Renderer;

const context: Origin = { workspaceId: 'workspace-a', clientId: 'client-a', pageEpoch: 'page-a', connectionEpoch: 'socket-a', appRunId: 'run-a' };
const A = 'w-627623e8-c7d5-4805-9146-8dc6c0592f05', B = 'w-60e9a614-f811-4291-80b2-604987c3bb9f';
const lifetime = '4bde9c04-84ed-4b39-ab52-a4470f93785f';
// Canonical identity shape, invented fixture values; no private resource/bytes are read.
const source = (member = 'c14', media = 'capture'): Source => ({
  key: `e2:r0:${member}`, kind: 'saved-image', media, availability: 'retained',
  identity: { resource: { day: '2026-10-08', id: 'd2', version: 0, picture: member === 'c14' ? 0 : 1 }, population: 'e2', member, revision: 0, capture: '2741', crop: member === 'c14' ? [457, 410, 148, 176] : [575, 649, 138, 71] },
  provenance: { run: 'run-a', boot: 'bridge-a', seq: 2756, transforms: [], scale: null },
});
const state = (id = A, saved = source()): State => ({
  id, workspaceId: context.workspaceId, source: saved, sourceGeneration: 1, lifetimeGeneration: lifetime,
  revision: 1, open: true, title: 'synthetic retained crop',
  geometryIntent: { position: [20.25, 40.5], size: [360, 280] }, view: { zoom: 1, pan: [0.5, 0.5] },
  propertyVersions: { geometry: 3, source: 1, open: 1, zoom: 1, pan: 1, front: 1 },
  propertyOwners: { geometry: 'request-a', source: 'request-a', open: 'request-a', zoom: 'request-a', pan: 'request-a', front: 'request-a' },
  delivery: { sourceToken: `payload-${saved.key}`, url: '/synthetic/retained.jpg' },
});

let dom: ReturnType<typeof syntheticDOM>;
let renderer: Renderer | undefined;
beforeEach(() => {
  dom = syntheticDOM();
  vi.stubGlobal('document', dom.document); vi.stubGlobal('window', globalThis); vi.stubGlobal('addEventListener', vi.fn());
  vi.stubGlobal('HTMLElement', TestElement); vi.stubGlobal('HTMLImageElement', TestImage);
  vi.stubGlobal('requestAnimationFrame', dom.raf);
  vi.stubGlobal('getComputedStyle', (element: TestElement) => ({ ...element.style, display: element.hidden ? 'none' : 'block' }));
});
afterEach(() => { renderer?.dispose(); renderer = undefined; vi.unstubAllGlobals(); });

async function moduleAt(path: string) { return import(/* @vite-ignore */ path); }
async function make(onPresentation?: (id: string, root: HTMLElement, title: () => string, foreground: boolean) => void, background?: (requestId:string)=>boolean) {
  const module = await moduleAt('./workspace-windows.ts');
  expect(typeof module.WorkspaceWindows, 'feature absent: independent Studio renderer').toBe('function');
  const sent: Manual[] = [], panels = new PanelStack();
  renderer = new (module.WorkspaceWindows as Factory)({ host: dom.host as unknown as HTMLElement, panels, context, onPresentation, background,
    send: (command) => sent.push(command), facts: () => ({ canvas: dom.host.getBoundingClientRect(), area: { width: dom.host.offsetWidth, height: dom.host.offsetHeight }, disc: null, object: null, labels: [] }) });
  return { windows: renderer, sent, panels };
}
function apply(windows: Renderer, items: State[], commandId = 'command-a') { windows.apply({ context, requestId: 'run-a:request-a', commandId, actionOccurrenceId: `occurrence-${commandId}`, items }); }
function root(windows: Renderer, id: string) { return windows.root(id) as unknown as TestElement; }
function control(windows: Renderer, id: string, name: string) {
  const found = root(windows, id).querySelector(`[data-control="${name}"]`);
  expect(found, `actual ${name} control for ${id}`).not.toBeNull();
  return found!;
}
function fact(windows: Renderer, id: string) { const result = windows.facts().find((row) => row.id === id); expect(result).toBeDefined(); return result!; }
async function decoded(windows: Renderer, id: string, width = 148, height = 176) {
  await settleMicrotasks(); const image = root(windows, id).querySelector('img');
  expect(image).toBeInstanceOf(TestImage); expect((image as TestImage).decodeCalls).toBeGreaterThan(0);
  (image as TestImage).finishDecode(width, height); await settleMicrotasks(); dom.frame();
}

describe('retained workspace presentation', () => {
  test('Reveal fronts only the exact resource, Project, tab and placement through fenced commands', async () => {
    const {windows,sent}=await make();apply(windows,[state()]);await decoded(windows,A);
    document.body.dataset.workspaceProject='current';document.body.dataset.activeWorkspaceView='main';
    Object.assign(root(windows,A).dataset,{project:'current',workspaceView:'main',placementId:'one'});
    const saved={...windows.retainedPresentation().views[0],placementId:'one'};
    expect(windows.frontPresentation({...saved,placementId:'other'})).toBe(false);
    expect(windows.frontPresentation({...saved,resource:{...saved.resource,version:1}})).toBe(false);
    document.body.dataset.workspaceProject='different';expect(windows.frontPresentation(saved)).toBe(false);
    document.body.dataset.workspaceProject='current';document.body.dataset.activeWorkspaceView='other';
    expect(windows.frontPresentation(saved)).toBe(false);expect(sent).toHaveLength(0);
    document.body.dataset.activeWorkspaceView='main';expect(windows.frontPresentation(saved)).toBe(true);
    expect(sent).toHaveLength(1);expect(sent[0].action).toBe('front');expect(sent[0].base.lifetimeGeneration).toBe(lifetime);
  });
  test('restores an exact retained revision through native fenced view controls', async () => {
    const { windows, sent } = await make();
    apply(windows, [state()]); await decoded(windows, A);
    const saved = windows.retainedPresentation().views[0];
    expect(saved.resource).toEqual({ day: '2026-10-08', id: 'd2', version: 0, picture: 0 });
    expect(saved).not.toHaveProperty('delivery');
    expect(windows.restorePresentation({ ...saved, resource: { ...saved.resource, version: 1 }, zoom: 2 })).toBe(false);
    expect(sent).toHaveLength(0);
    const wanted={...saved,zoom:2,pan:[.25,.75]};
    expect(windows.restorePresentation(wanted)).toBe(false);
    expect(sent.map(command=>command.action)).toEqual(['zoom']);
    expect(windows.restorePresentation(wanted)).toBe(false);
    expect(sent).toHaveLength(1); // Polling must not duplicate an admitted command.
    const accepted=state();accepted.revision++;accepted.view.zoom=2;accepted.propertyVersions.zoom++;
    apply(windows,[accepted],'zoom-accepted');
    expect(windows.restorePresentation(wanted)).toBe(false);
    expect(sent.map(command=>command.action)).toEqual(['zoom','pan']);
    accepted.revision++;accepted.view.pan=[.25,.75];accepted.propertyVersions.pan++;
    apply(windows,[accepted],'pan-accepted');
    expect(windows.restorePresentation(wanted)).toBe(true);
    expect(sent).toHaveLength(2); // Matching geometry needs no additional write.
    expect(sent[0].base.lifetimeGeneration).toBe(lifetime);
    expect(fact(windows,A).view).toMatchObject({zoom:2,pan:[.25,.75]});
  });
  test('places a window by its id through its own fenced resize, then move, one at a time', async () => {
    // Arrange's tile for a native window with no retained resource (the live magnifier): the person's own resize and
    // move, each waiting for the accepted descriptor that fences the next (workspace-views.ts place).
    const { windows, sent } = await make();
    apply(windows, [state()]); await decoded(windows, A);
    const placer = windows as unknown as { placeWindow(id: string, rect: number[]): boolean };
    const tile = [12, 12, 600, 400];
    expect(placer.placeWindow(A, tile)).toBe(false);
    expect(sent.map(command => command.action)).toEqual(['resize']);
    expect(sent[0].values).toEqual({ size: [600, 400] });
    expect(placer.placeWindow(A, tile)).toBe(false);
    expect(sent).toHaveLength(1); // polling never sends a second command while one is unanswered
    const accepted = state(); accepted.revision++; accepted.geometryIntent.size = [600, 400]; accepted.propertyVersions.geometry++;
    apply(windows, [accepted], 'resize-accepted');
    expect(placer.placeWindow(A, tile)).toBe(false);
    expect(sent.map(command => command.action)).toEqual(['resize', 'move']);
    const host = (dom.host as unknown as HTMLElement).getBoundingClientRect();
    expect(sent[1].values).toEqual({ position: [12 + host.left, 12 + host.top] });
    accepted.revision++; accepted.geometryIntent.position = [12 + host.left, 12 + host.top]; accepted.propertyVersions.geometry++;
    apply(windows, [accepted], 'move-accepted');
    expect(placer.placeWindow(A, tile)).toBe(true);
    expect(sent).toHaveLength(2);
    expect(placer.placeWindow('w-gone', tile)).toBe(true); // a window no longer open has nothing to place
  });
  test('does not reopen a dismissed view or apply transforms before evidence loads', async () => {
    const { windows } = await make();
    apply(windows, [state()]);
    const saved = windows.retainedPresentation().views[0];
    expect(windows.restorePresentation(saved)).toBe(false);
    await decoded(windows, A);
    windows.dispatchUser({ action: 'close', instance: A, values: {} });
    expect(windows.retainedPresentation().views).toEqual([]);
  });
});

describe('reference artifact footer from authorized source provenance', () => {
  const description = 'Two guard cells surround the pore; this is a reference example, not the current slide.';
  const web = { site: 'Wikimedia Commons', page: 'https://commons.wikimedia.org/wiki/File:Leaf_pore.jpg',
    title: 'Leaf pore', author: 'A. Botanist', licence: 'CC BY-SA 4.0',
    credit: 'A. Botanist, CC BY-SA 4.0, commons.wikimedia.org' };
  const reference = (): Source => ({ key: '2026-10-08/d8/0', kind: 'saved-image', media: 'reference',
    availability: 'retained', identity: { resource: { day: '2026-10-08', id: 'd8', version: null, picture: 0 } },
    provenance: { frame: null, seq: null, boot: null, description, web } });

  test('source link stays beneath each photo and description is available in its controls', async () => {
    const { windows } = await make(); const saved = reference(); apply(windows, [state(A, saved), state(B, saved)]);
    await decoded(windows, A, 160, 120); await decoded(windows, B, 160, 120);
    expect(fact(windows, A).load.state).toBe('drawn'); expect(fact(windows, B).load.state).toBe('drawn');
    expect(fact(windows, A).rootId).not.toBe(fact(windows, B).rootId);
    for (const id of [A, B]) {
      const window = root(windows, id), footer = window.querySelector('.workspace-window-caption')!;
      expect(footer.title).toContain('Reference image');
      expect(footer.textContent).not.toContain(description);
      expect(window.querySelector('.workspace-source-details')!.textContent).toContain(description);
      expect(footer.querySelector('a')).not.toBeNull();
      expect(footer.textContent).toContain('commons.wikimedia.org');
      expect(window.querySelector('.workspace-source-details')!.textContent).toContain(web.credit);
      expect(window.querySelector('.fwin-bar')!.contains(footer)).toBe(true);
      expect(fact(windows, id).source.provenance).toEqual(saved.provenance);
    }
  });

  test('replacing one reference clears its former description and credit without changing its sibling', async () => {
    const { windows } = await make(); apply(windows, [state(A, reference()), state(B, reference())]);
    await decoded(windows, A, 160, 120); await decoded(windows, B, 160, 120);
    const replacement = { ...state(A, source()), sourceGeneration: 2, revision: 2 };
    apply(windows, [replacement], 'replace-a'); await decoded(windows, A);
    const a = root(windows, A).querySelector('.workspace-source-details')!.textContent;
    const b = root(windows, B).querySelector('.workspace-source-details')!.textContent;
    expect(a).toContain('Captured evidence'); expect(a).not.toContain(description); expect(a).not.toContain(web.credit);
    expect(b).toContain(description); expect(b).toContain(web.credit);
    expect(fact(windows, B).source).toEqual(reference());
  });

  test('hostile description is plain text and missing description preserves the honest media label', async () => {
    const { windows } = await make(); const hostile = '<img src=x onerror=alert(1)> a quoted example';
    const described = reference(); described.provenance.description = hostile;
    const bare = reference(); delete bare.provenance.description; delete bare.provenance.web;
    apply(windows, [state(A, described), state(B, bare)]);
    await decoded(windows, A, 160, 120); await decoded(windows, B, 160, 120);
    const footer = root(windows, A).querySelector('.workspace-window-caption')!;
    expect(root(windows, A).querySelector('.workspace-source-details')!.textContent).toContain(hostile); expect(footer.querySelector('img')).toBeNull();
    expect(root(windows, A).querySelectorAll('img')).toHaveLength(1);
    expect(root(windows, B).querySelector('.workspace-window-caption')!.textContent).toBe('Reference photo');
    expect(fact(windows, A).load.state).toBe('drawn'); expect(fact(windows, B).load.state).toBe('drawn');
  });
});

describe('WorkspaceWindows actual adapter handlers with synthetic media', () => {
  test('same exact source has two UUID roots and real FloatingWindow shells', async () => {
    const { windows, panels } = await make(); const shared = source(); apply(windows, [state(A, shared), state(B, shared)]);
    const module = await moduleAt('./panels.ts');
    expect(root(windows, A)).not.toBe(root(windows, B));
    expect(root(windows, A).dataset.presentationId).toBe(A); expect(root(windows, B).dataset.presentationId).toBe(B);
    expect(windows.floating(A)).toBeInstanceOf(module.FloatingWindow); expect(windows.floating(B)).toBeInstanceOf(module.FloatingWindow);
    expect(fact(windows, A).rootId).not.toBe(fact(windows, B).rootId);
    expect(panels.obstacles()).toHaveLength(2);
    expect(fact(windows, A).source).toEqual(shared); expect(fact(windows, B).source).toEqual(shared);
  });
  test('actual zoom and close buttons act on their UUID and preserve the sibling', async () => {
    const { windows, sent } = await make(); apply(windows, [state(A), state(B)]);
    const beforeTransform = root(windows, A).querySelector('img')?.style.transform;
    fire(control(windows, A, 'zoom-in'), 'click');
    expect(sent.at(-1)).toMatchObject({ type: 'present', action: 'zoom', instance: A, context });
    expect(fact(windows, A).view.zoom).toBe(2); expect(fact(windows, B).view.zoom).toBe(1);
    expect(root(windows, A).querySelector('img')?.style.transform).not.toBe(beforeTransform);
    fire(control(windows, A, 'close'), 'click');
    expect(sent.at(-1)).toMatchObject({ action: 'close', instance: A });
    expect(root(windows, A).hidden).toBe(true);
    expect(root(windows, B).hidden).toBe(false); expect(fact(windows, B).view.zoom).toBe(1);
  });
  test('a refused close stays dismissed through unrelated source updates', async () => {
    const { windows } = await make(); apply(windows, [state(A), state(B)]);
    fire(control(windows, A, 'close'), 'click'); windows.rejectManual(A);
    expect(root(windows, A).hidden).toBe(true);
    const changed = state(A); changed.revision = 2; changed.sourceGeneration = 2;
    apply(windows, [changed], 'late-source');
    expect(root(windows, A).hidden).toBe(true);
    expect(root(windows, B).hidden).toBe(false);
  });
  test('resize acknowledgement preserves automatic placement instead of restoring the default position', async () => {
    const { windows } = await make(); const initial = state();
    initial.geometryIntent = { position: [0, 0], size: null };
    apply(windows, [initial]); await decoded(windows, A);
    const before = fact(windows, A).rect.slice();
    windows.dispatchUser({ action: 'resize', instance: A, values: { size: [400, 380] } });
    const acknowledged = structuredClone(initial); acknowledged.revision++;
    acknowledged.propertyVersions.geometry++;
    acknowledged.geometryIntent.size = [400, 380];
    windows.apply({ context, requestId: 'resize-request', commandId: 'resize-ack', actionOccurrenceId: 'resize-action', op: 'resize', items: [acknowledged] });
    expect(fact(windows, A).rect).toEqual([...before.slice(0, 2), 400, 380]);
  });
  test('a refused resize puts a window the studio placed back where it was, never at the top-left', async () => {
    // Live 2026-10-11: refused resizes of just-opened windows jumped them to 0,0, the never-set position.
    const { windows } = await make(); const initial = state();
    initial.geometryIntent = { position: [0, 0], size: null };
    apply(windows, [initial]); await decoded(windows, A);
    const before = fact(windows, A).rect.slice();
    const host = dom.host.getBoundingClientRect();
    expect([before[0], before[1]]).not.toEqual([host.left, host.top]);
    const grip = control(windows, A, 'resize');
    fire(grip, 'pointerdown', { pointerId: 9, clientX: 100, clientY: 100 });
    fire(grip, 'pointermove', { pointerId: 9, clientX: 150, clientY: 140 });
    fire(grip, 'pointerup', { pointerId: 9 });
    expect(fact(windows, A).rect).not.toEqual(before);
    windows.rejectManual(A);
    expect(fact(windows, A).rect).toEqual(before);
  });
  test('a refused opacity change leaves the window where it is', async () => {
    const { windows } = await make(); const initial = state();
    initial.geometryIntent = { position: [0, 0], size: null };
    initial.view = { zoom: 1, pan: [0.5, 0.5], opacity: 1 } as State['view'];
    initial.propertyVersions.opacity = 1;
    apply(windows, [initial]); await decoded(windows, A);
    const before = fact(windows, A).rect.slice();
    windows.dispatchUser({ action: 'opacity', instance: A, values: { value: 0.4 } });
    windows.rejectManual(A);
    expect(fact(windows, A).rect).toEqual(before);
    expect(root(windows, A).style.opacity).toBe('1');
  });
  test('real title drag and resize grip dispatch only their own geometry', async () => {
    const { windows, sent } = await make(); apply(windows, [state(A), state(B)]);
    const before = fact(windows, B).rect.slice(); const bar = root(windows, A).querySelector('.fwin-bar')!;
    fire(bar, 'pointerdown', { pointerId: 1, clientX: 30, clientY: 50 });
    fire(bar, 'pointermove', { pointerId: 1, clientX: 90, clientY: 100 }); fire(bar, 'pointerup', { pointerId: 1 });
    expect(sent.some((row) => row.instance === A && row.action === 'move')).toBe(true);
    const grip = root(windows, A).querySelector('.fwin-grip')!;
    fire(grip, 'pointerdown', { pointerId: 2, clientX: 100, clientY: 100 });
    fire(grip, 'pointermove', { pointerId: 2, clientX: 140, clientY: 130 }); fire(grip, 'pointerup', { pointerId: 2 });
    expect(sent.some((row) => row.instance === A && row.action === 'resize')).toBe(true);
    expect(sent.every((row) => row.instance === A)).toBe(true); expect(fact(windows, B).rect).toEqual(before);
  });
  test('resizing follows both pointer axes and keeps the top-left fixed', async () => {
    const { windows } = await make(); apply(windows, [state(A)]); await decoded(windows, A);
    const before = fact(windows, A).rect.slice(), grip = control(windows, A, 'resize');
    fire(grip, 'pointerdown', { pointerId: 7, clientX: 100, clientY: 100 });
    fire(grip, 'pointermove', { pointerId: 7, clientX: 140, clientY: 170 });
    fire(grip, 'pointerup', { pointerId: 7 });
    expect(fact(windows, A).rect).toEqual([before[0], before[1], before[2] + 40, before[3] + 70]);
  });
  test('a prepared photo opens at its final size before decode and stays there', async () => {
    const { windows } = await make(); const prepared = state(A);
    Object.assign(prepared.delivery, { originalSize: [850, 250] });
    prepared.geometryIntent = { position: [0, 0], size: null };
    apply(windows, [prepared]); const before = fact(windows, A).rect.slice();
    await decoded(windows, A, 850, 250);
    expect(fact(windows, A).rect).toEqual(before);
    expect(fact(windows, A).contentRect[2] / fact(windows, A).contentRect[3]).toBeCloseTo(850 / 250);
    expect(before[3] - fact(windows, A).contentRect[3]).toBeLessThanOrEqual(44);
  });
  test('same-valued geometry touch sends its authoritative base version without a duplicate counter', async () => {
    const { windows, sent } = await make(); apply(windows, [state()]);
    const bar = root(windows, A).querySelector('.fwin-bar')!;
    fire(bar, 'pointerdown', { pointerId: 1, clientX: 30, clientY: 50 }); fire(bar, 'pointermove', { pointerId: 1, clientX: 30, clientY: 50 });
    // Preview stays local; the final gesture commits once, even with identical geometry.
    expect(sent.find((row) => row.action === 'move')).toBeUndefined();
    fire(bar, 'pointerup', { pointerId: 1 });
    expect(sent.find((row) => row.action === 'move')).toMatchObject({ instance: A, base: { propertyVersions: { geometry: 3 }, sourceGeneration: 1, lifetimeGeneration: lifetime } });
    expect(fact(windows, A).propertyVersions.geometry).toBe(3);
  });
  test('manual pan preserves numeric precision and independent source view', async () => {
    const { windows, sent } = await make(); apply(windows, [state(A), state(B)]);
    const image = root(windows, A).querySelector('img')!, sibling = root(windows, B).querySelector('img')!;
    const before = image.style.transform, siblingBefore = sibling.style.transform;
    windows.dispatchUser({ action: 'pan', instance: A, values: { center: [0.2500004, 0.7499996] } });
    expect(fact(windows, A).view.pan[0]).toBeCloseTo(0.2500004, 6); expect(fact(windows, B).view.pan).toEqual([0.5, 0.5]);
    expect(image.style.transform).not.toBe(before); expect(sibling.style.transform).toBe(siblingBefore);
    expect(sent.at(-1)).toMatchObject({ action: 'pan', instance: A, context });
    expect(sent.at(-1)?.commandId).toBeTruthy(); expect(sent.at(-1)?.actionOccurrenceId).toBeTruthy();
    expect(sent.at(-1)?.requestId).toBeTruthy();
  });
  test('front changes actual stack order and Escape closes only that UUID', async () => {
    const { windows, panels, sent } = await make(); apply(windows, [state(A), state(B)]);
    windows.floating(A).front(true); expect(panels.top()?.id).toBe(A);
    const event = { key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} };
    panels.onEscape(event); expect(sent.at(-1)).toMatchObject({ action: 'close', instance: A });
    expect(root(windows, B).hidden).toBe(false);
  });
  test('assigned payload is loading; decoder success alone does not certify drawn', async () => {
    const { windows } = await make(); apply(windows, [state()]); await settleMicrotasks();
    expect(fact(windows, A).load).toMatchObject({ state: 'loading', drawToken: null });
    const image = root(windows, A).querySelector('img') as TestImage; expect(image.src).toBe('/synthetic/retained.jpg');
    image.finishDecode(); await settleMicrotasks(); expect(fact(windows, A).load.state).toBe('loaded');
    dom.frame(); expect(fact(windows, A).load.state).toBe('drawn'); expect(fact(windows, A).load.drawToken).toBeTruthy();
  });
  test('replacement never attaches old decode or draw tokens to a new source', async () => {
    const { windows } = await make(); apply(windows, [state()]); await settleMicrotasks();
    const oldImage = root(windows, A).querySelector('img') as TestImage;
    const finishOldDecode = oldImage.captureDecode();
    const next = { ...state(A, source('c17')), sourceGeneration: 2, revision: 2, delivery: { sourceToken: 'payload-new', url: '/synthetic/new.jpg' } };
    apply(windows, [next], 'command-new'); finishOldDecode(); await settleMicrotasks(); dom.frame();
    expect(fact(windows, A)).toMatchObject({ sourceGeneration: 2, sourceToken: 'payload-new', commandId: 'command-new', load: { state: 'loading', drawToken: null } });
    await decoded(windows, A); expect(fact(windows, A).load.state).toBe('drawn');
  });
  test('decoded wrong delivery URL cannot certify the desired source token as drawn', async () => {
    const { windows } = await make(); apply(windows, [state()]); await settleMicrotasks();
    const image = root(windows, A).querySelector('img') as TestImage;
    image.finishDecode(); image.currentSrc = '/synthetic/wrong-source.jpg'; await settleMicrotasks(); dom.frame();
    expect(fact(windows, A).source).toEqual(source());
    expect(fact(windows, A).load.state).not.toBe('drawn'); expect(fact(windows, A).load.drawToken).toBeNull();
  });
  test('unknown source stays unknown without substituting the latest picture or decoding a fallback', async () => {
    const { windows } = await make(); const unavailable = state(A, { ...source(), availability: 'unknown' });
    unavailable.delivery = { sourceToken: 'unknown-payload', url: '' };
    apply(windows, [unavailable, state(B)]); await decoded(windows, B);
    expect(fact(windows, A).source).toEqual(unavailable.source);
    expect(fact(windows, A).load.state).not.toBe('drawn'); expect(fact(windows, A).load.drawToken).toBeNull();
    expect(dom.images.filter((image) => image.src === '/synthetic/retained.jpg')).toHaveLength(1);
    expect(root(windows, A).textContent).toContain('unknown');
  });
  test('decode error is local and retry cannot erase a drawn sibling', async () => {
    const { windows } = await make(); apply(windows, [state(A), state(B)]); await decoded(windows, B);
    const image = root(windows, A).querySelector('img') as TestImage; image.failDecode(); await settleMicrotasks();
    expect(fact(windows, A).load.state).toBe('error'); expect(fact(windows, B).load.state).toBe('drawn');
    fire(control(windows, A, 'retry'), 'click'); await settleMicrotasks();
    expect(fact(windows, A).load.state).toBe('loading'); expect(fact(windows, B).load.state).toBe('drawn');
  });
  test('reported geometry is actual fractional CSS, not desired geometry or DPR multiplication', async () => {
    const { windows } = await make(); apply(windows, [state()]); root(windows, A).style.left = '22.26px';
    expect(fact(windows, A).rect[0]).toBeCloseTo(22.26, 6);
    expect(Math.abs(fact(windows, A).rect[0] - state().geometryIntent.position[0])).toBeGreaterThan(2);
    expect(fact(windows, A).rect[0]).not.toBe(20.25); // actual mismatch survives for completion's 2 CSS px predicate
  });
  test.each(['workspaceId', 'clientId', 'pageEpoch', 'connectionEpoch', 'appRunId'] as const)('wrong %s batch cannot replace current windows', async (key) => {
    const { windows } = await make(); apply(windows, [state()]);
    try { windows.apply({ context: { ...context, [key]: 'other' }, requestId: 'run-a:request-a', commandId: 'wrong', actionOccurrenceId: 'wrong', items: [state(A, source('c17'))] }); }
    catch (error) { expect(error).toMatchObject({ code: 'context_mismatch' }); }
    expect(fact(windows, A).source.key).toBe('e2:r0:c14'); expect(fact(windows, A).commandId).toBe('command-a');
  });
  test('replay reuses UUID roots and command occurrence without resetting local view', async () => {
    const { windows } = await make(); const items = [state()]; apply(windows, items);
    const first = root(windows, A); windows.dispatchUser({ action: 'pan', instance: A, values: { center: [0.25, 0.75] } });
    apply(windows, items); expect(root(windows, A)).toBe(first); expect(fact(windows, A).view.pan).toEqual([0.25, 0.75]);
  });
  test('research result and work float without shrinking the live canvas and keep exact output identity', async () => {
    const { windows, panels } = await make(); const picture = state(A, { ...source(), media: 'generated', provenance: { researcherId: 'r2', outputId: 'picture-7', outputVersion: 3 } });
    const work = state(B, { key: 'r2:work', kind: 'written', media: 'written', availability: 'retained', identity: { researcherId: 'r2' }, provenance: {} });
    work.delivery = { sourceToken: 'work-r2', url: '', text: 'Synthetic researcher progress' };
    apply(windows, [picture, work]); await decoded(windows, A);
    expect(windows.floating(B)).toBeTruthy(); expect(fact(windows, A).source.provenance).toMatchObject({ outputId: 'picture-7', outputVersion: 3 });
    expect(panels.reserved(dom.host.getBoundingClientRect())).toEqual({ right: 0, bottom: 0, shelf: 0, left: 0, edge: 0 });
    expect(fact(windows, A).contentRect[2]).toBeGreaterThanOrEqual(240); expect(fact(windows, A).contentRect[3]).toBeGreaterThanOrEqual(180);
  });
  test('reference delivery ignores unrelated camera disc geometry and displays the complete image', async () => {
    const { windows } = await make(); const reference = state(A, { ...source(), media: 'reference' });
    reference.delivery.disc = [640, 360, 300]; apply(windows, [reference]); await decoded(windows, A, 1280, 720);
    const image = root(windows, A).querySelector('img') as TestImage;
    expect(image.style.width).toBe('100%'); expect(image.style.left || '0%').toBe('0%'); expect(image.style.top || '0%').toBe('0%');
    expect(fact(windows, A).load.naturalSize).toEqual([1280, 720]); expect(fact(windows, A).source).toEqual(reference.source);
  });
  test('phone keeps independent roots and reports constrained simultaneous geometry', async () => {
    dom.host.style.width = '390px'; const { windows } = await make(); const left = state(A), right = state(B);
    left.geometryIntent.relation = { kind: 'beside', reference: B, gapPx: 20 };
    apply(windows, [left, right]);
    windows.floating(A).frame(); windows.floating(B).frame();
    expect(root(windows, A)).not.toBe(root(windows, B));
    for (const id of [A, B]) { const row = fact(windows, id); expect(row.rect[0]).toBeGreaterThanOrEqual(0); expect(row.rect[0] + row.rect[2]).toBeLessThanOrEqual(392); }
    expect(windows.facts().some((row) => row.limitation === 'constraint_conflict')).toBe(true);
  });
  test('explicit beside reference moves only its target through the real FloatingWindow', async () => {
    const { windows } = await make(), moved = state(A), anchor = state(B);
    moved.geometryIntent.size = [360, 400]; anchor.geometryIntent.size = [360, 400];
    moved.geometryIntent.position = [550, 250]; anchor.geometryIntent.position = [20, 40];
    apply(windows, [moved, anchor]); await decoded(windows, A); await decoded(windows, B);
    const before = structuredClone(fact(windows, A)), fixed = structuredClone(fact(windows, B));
    expect(before.load.state).toBe('drawn'); expect(fixed.load.state).toBe('drawn');
    for (const row of [before, fixed]) { expect(row.contentRect[2]).toBeGreaterThanOrEqual(240); expect(row.contentRect[3]).toBeGreaterThanOrEqual(180); }
    expect(before.rootId).not.toBe(fixed.rootId);
    const arranged = structuredClone(moved); arranged.revision += 1;
    arranged.propertyVersions.geometry += 1; arranged.propertyOwners.geometry = 'original-arrange';
    arranged.geometryIntent.relation = { kind: 'beside', reference: B, gapPx: 12 };
    apply(windows, [arranged], 'explicit-beside'); dom.frame();
    const actual = fact(windows, A);
    expect(actual.rect).not.toEqual(before.rect);
    expect(actual.rect.slice(0, 2)).toEqual([fixed.rect[0] + fixed.rect[2] + 12, fixed.rect[1]]);
    expect(fact(windows, B)).toEqual(fixed);
    expect(actual.source).toEqual(before.source); expect(actual.sourceToken).toBe(before.sourceToken);
    expect(actual.sourceGeneration).toBe(before.sourceGeneration); expect(actual.lifetimeGeneration).toBe(before.lifetimeGeneration);
    expect(actual.commandId).toBe('explicit-beside'); expect(actual.limitation).toBeUndefined();
  });
  test('beside keeps the useful portrait size established by actual decode', async () => {
    const { windows } = await make(), moved = state(A), anchor = state(B);
    moved.geometryIntent.position = [550, 250]; anchor.geometryIntent.position = [20, 40];
    apply(windows, [moved, anchor]); await decoded(windows, A); await decoded(windows, B);
    const before = structuredClone(fact(windows, A)), fixed = structuredClone(fact(windows, B));
    expect(before.rect[3]).toBeGreaterThan(moved.geometryIntent.size![1]);
    expect(before.contentRect[2]).toBeGreaterThanOrEqual(240); expect(before.contentRect[3]).toBeGreaterThanOrEqual(180);
    expect(before.limitation).toBeUndefined(); expect(before.load.state).toBe('drawn');
    const arranged = structuredClone(moved); arranged.revision += 1;
    arranged.propertyVersions.geometry += 1; arranged.propertyOwners.geometry = 'original-portrait-arrange';
    arranged.geometryIntent.relation = { kind: 'beside', reference: B, gapPx: 12 };
    apply(windows, [arranged], 'portrait-beside'); dom.frame();
    const actual = fact(windows, A);
    expect(actual.rect.slice(0, 2)).toEqual([fixed.rect[0] + fixed.rect[2] + 12, fixed.rect[1]]);
    expect(fact(windows, B)).toEqual(fixed); expect(actual.source).toEqual(before.source);
    expect(actual.sourceToken).toBe(before.sourceToken); expect(actual.sourceGeneration).toBe(before.sourceGeneration);
    expect(actual.rect.slice(2), 'arrange did not request a resize of the decoded useful portrait').toEqual(before.rect.slice(2));
    expect(actual.contentRect[2]).toBeGreaterThanOrEqual(240); expect(actual.contentRect[3]).toBeGreaterThanOrEqual(180);
    expect(actual.limitation).toBeUndefined();
  });
  test('explicit left beside uses current decoded size and leaves the reference unchanged', async () => {
    const { windows } = await make(), moved = state(A), anchor = state(B);
    moved.geometryIntent.size = [320, 400]; anchor.geometryIntent.size = [180, 120];
    moved.geometryIntent.position = [0, 200]; anchor.geometryIntent.position = [600, 10];
    apply(windows, [moved, anchor]); await decoded(windows, A); await decoded(windows, B);
    windows.dispatchUser({ action: 'resize', instance: B, values: { size: [180, 120] } }); dom.frame();
    const before = structuredClone(fact(windows, A)), fixed = structuredClone(fact(windows, B));
    expect(before.load.state).toBe('drawn'); expect(fixed.load.state).toBe('drawn');
    expect(before.rect.slice(2)).toEqual([320, 400]); expect(fixed.rect).toEqual([600, 10, 180, 120]);
    const arranged = structuredClone(moved); arranged.revision += 1;
    arranged.propertyVersions.geometry += 1; arranged.propertyOwners.geometry = 'original-left-arrange';
    arranged.geometryIntent.relation = { kind: 'beside', reference: B, gapPx: 10, side: 'left' };
    apply(windows, [arranged], 'explicit-left-beside'); dom.frame();
    const actual = fact(windows, A);
    expect(actual.rect).toEqual([270, 10, 320, 400]);
    expect(actual.rect[0] + actual.rect[2]).toBe(fixed.rect[0] - 10);
    expect(fact(windows, B)).toEqual(fixed);
    expect(actual.source).toEqual(before.source); expect(actual.sourceToken).toBe(before.sourceToken);
    expect(actual.sourceGeneration).toBe(before.sourceGeneration); expect(actual.lifetimeGeneration).toBe(before.lifetimeGeneration);
    expect(actual.limitation).toBeUndefined();
  });
  test.each(['right', 'left'] as const)('impossible %s beside preserves roots and sizes instead of clamping over the reference', async (side) => {
    dom.host.style.width = '487px'; dom.host.style.height = '600px';
    const { windows } = await make(), moved = state(A), anchor = state(B);
    moved.geometryIntent.size = [320, 380]; anchor.geometryIntent.size = [180, 120];
    moved.geometryIntent.position = [0, 200]; anchor.geometryIntent.position = [300, 10];
    apply(windows, [moved, anchor]); await decoded(windows, A); await decoded(windows, B);
    windows.dispatchUser({ action: 'resize', instance: B, values: { size: [180, 120] } }); dom.frame();
    const before = structuredClone(fact(windows, A)), fixed = structuredClone(fact(windows, B));
    expect(before.load.state).toBe('drawn'); expect(fixed.load.state).toBe('drawn');
    expect(before.rect).toEqual([0, 200, 320, 380]); expect(fixed.rect).toEqual([300, 10, 180, 120]);
    expect(before.rect[1]).toBeGreaterThan(fixed.rect[1] + fixed.rect[3]); // useful original nonoverlap
    const arranged = structuredClone(moved); arranged.revision += 1;
    arranged.propertyVersions.geometry += 1; arranged.propertyOwners.geometry = `original-impossible-${side}`;
    arranged.geometryIntent.relation = { kind: 'beside', reference: B, gapPx: 10, side };
    apply(windows, [arranged], `impossible-${side}-beside`); dom.frame();
    const actual = fact(windows, A);
    expect(actual.rect, 'no implicit resize or clamped overlap after a relation cannot fit').toEqual(before.rect);
    expect(actual.source).toEqual(before.source); expect(actual.sourceToken).toBe(before.sourceToken);
    expect(actual.sourceGeneration).toBe(before.sourceGeneration); expect(actual.lifetimeGeneration).toBe(before.lifetimeGeneration);
    expect(actual.load.state).toBe('drawn'); expect(actual.limitation).toBe('constraint_conflict');
    expect(fact(windows, B)).toEqual(fixed);
  });
  test('explicit user portrait shrink remains allowed rather than forced back to useful minimum', async () => {
    const { windows, sent } = await make(); apply(windows, [state(A)]); await decoded(windows, A);
    const before = structuredClone(fact(windows, A));
    expect(before.contentRect[2]).toBeGreaterThanOrEqual(240); expect(before.contentRect[3]).toBeGreaterThanOrEqual(180);
    windows.dispatchUser({ action: 'resize', instance: A, values: { size: [180, 200] } }); dom.frame();
    const actual = fact(windows, A);
    expect(actual.rect.slice(2)).toEqual([180, 200]); expect(actual.contentRect[2]).toBeLessThan(240);
    expect(actual.source).toEqual(before.source); expect(actual.sourceToken).toBe(before.sourceToken);
    expect(actual.limitation).toBeUndefined();
    expect(sent).toHaveLength(1); expect(sent[0].action).toBe('resize'); expect(sent[0].values).toEqual({ size: [180, 200] });
    expect(sent[0].base.sourceGeneration).toBe(before.sourceGeneration);
  });
  test('historical cells are disclosed as captured evidence and cannot be certified as live overlay', async () => {
    const { windows } = await make(); const historical = state(A, { ...source(), media: 'labelled', provenance: { capture: 'old', registeredLiveMembers: 0, totalMembers: 160, reason: 'eyepiece scale changed' } });
    apply(windows, [historical]); await decoded(windows, A);
    expect(fact(windows, A).source.media).toBe('labelled');
    expect(fact(windows, A).source.provenance.registeredLiveMembers).toBe(0);
    expect(root(windows, A).textContent).toContain('captured');
    // Drawer/render success is scoped evidence. Outcomes/brain must retain the unmet live-alignment goal.
  });
});

describe('recovered floating layout public seams', () => {
  test('opening an older registered FloatingWindow raises its actual shell before placement', async () => {
    const module = await moduleAt('./panels.ts');
    expect(typeof module.FloatingWindow, 'feature absent: recovered FloatingWindow').toBe('function');
    const panels = new PanelStack();
    const create = (id: string, open: boolean) => {
      const root = new TestElement('section'), bar = new TestElement('header'); root.hidden = !open; root.append(bar); dom.host.append(root);
      const shell = new module.FloatingWindow({ id, root, bar, panels, close: () => { root.hidden = true; }, content: () => ({ kind: 'picture', aspect: 2 }), facts: () => null });
      panels.register(shell); return { root, shell };
    };
    const first = create(A, false), second = create(B, true); first.shell.frame(); second.shell.frame(); expect(panels.top()?.id).toBe(B);
    first.root.hidden = false; first.shell.frame(); expect(panels.top()?.id).toBe(A);
    expect(Number(first.root.style.zIndex)).toBeGreaterThan(Number(second.root.style.zIndex));
    panels.onEscape({ key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} });
    expect(first.root.hidden).toBe(true); expect(second.root.hidden).toBe(false);
  });
  test('default close-up is useful at 240 by 180 without distorting the source aspect', async () => {
    const panels = await moduleAt('./panels.ts');
    expect(typeof panels.defaultWindowSize, 'feature absent: recovered window sizing').toBe('function');
    const size = panels.defaultWindowSize({ kind: 'picture', aspect: 2 }, { width: 1200, height: 800 }, 44);
    expect(size.width).toBeGreaterThanOrEqual(240); expect(size.height - 44).toBeGreaterThanOrEqual(180);
    expect(Math.abs(size.width / (size.height - 44) - 2)).toBeLessThanOrEqual(1e-6);
  });
  test('full reference image keeps its natural aspect and receives no camera disc crop', async () => {
    const fit = await moduleAt('./fit.ts'); const result = fit.fitPicture({ width: 1280, height: 720, disc: null, bounds: { min: 240, usual: 360, max: 640 }, chrome: 30 });
    expect(result.crop).toEqual({ x: 0, y: 0, width: 1280, height: 720 }); expect(result.round).toBe(false);
    expect(result.panel).toEqual({ width: 360, height: 233 });
    const frame = new TestElement('section'), picture = new TestElement('div'), image = new TestImage(); image.naturalWidth = 1280; image.naturalHeight = 720;
    fit.fitPanel(frame, picture, image, null); expect(picture.style.aspectRatio).toBe('1280 / 720'); expect(image.style.width).toBe('100%');
  });
  test('trusted camera disc is cropped as measured while partial discs remain rectangular', async () => {
    const fit = await moduleAt('./fit.ts');
    expect(fit.fitPicture({ width: 1280, height: 720, disc: [640, 360, 300], bounds: { min: 240, usual: 360, max: 640 } })).toMatchObject({ crop: { x: 340, y: 60, width: 600, height: 600 }, round: true });
    expect(fit.fitPicture({ width: 1280, height: 720, disc: [100, 360, 300], bounds: { min: 240, usual: 360, max: 640 } }).round).toBe(false);
  });
});

// Append-only CONTRACT review regressions. These are decoded synthetic images, not camera pixels.
type ReviewedSample = { run: string; boot: string | null; seq: number | null; frame?: number | null; epoch?: number | null };
type LiveFacts = Facts & { liveSample: ReviewedSample | null; unknown: string[]; staged: boolean | null };
type LiveSeam = { drawLiveFrame(id: string, frame: {
  image: HTMLImageElement; sample: unknown; sourceToken: string; sourceGeneration: number; lifetimeGeneration: string;
}): void };
function liveFact(windows: Renderer): LiveFacts { return fact(windows, A) as LiveFacts; }
async function liveWindow() {
  const { windows } = await make();
  const saved: Source = {
    key: 'live:run-a:o17:anchor-9', kind: 'live', media: 'raw', availability: 'temporary',
    identity: { run: 'run-a', objectId: 'o17', anchorCapture: 'anchor-9', epoch: 7 },
    // Deliberately unrelated descriptor facts must never fill unknown painted bridge fields.
    provenance: { run: 'run-a', boot: 'descriptor-boot', seq: 999, frame: 999, epoch: 999 },
  };
  const item = state(A, saved); apply(windows, [item]);
  const image = new TestImage(); image.src = '/synthetic/live-frame-10.jpg'; image.currentSrc = image.src;
  image.naturalWidth = 640; image.naturalHeight = 480; image.complete = true;
  const draw = (sample: unknown) => (windows as Renderer & LiveSeam).drawLiveFrame(A, {
    image: image as unknown as HTMLImageElement, sample, sourceToken: item.delivery.sourceToken,
    sourceGeneration: item.sourceGeneration, lifetimeGeneration: item.lifetimeGeneration,
  });
  return { windows, image, item, draw };
}
function expectLivePixels(windows: Renderer, image: TestImage, item: State) {
  expect(root(windows, A).querySelector('img')).toBe(image);
  expect(image.currentSrc).toBe('/synthetic/live-frame-10.jpg');
  expect(liveFact(windows)).toMatchObject({ source: item.source, sourceToken: item.delivery.sourceToken,
    sourceGeneration: item.sourceGeneration, lifetimeGeneration: item.lifetimeGeneration, staged: false,
    load: { state: 'drawn', naturalSize: [640, 480] } });
  // A paint receipt is separate from whether the bridge sample's identity is fully known.
  expect(liveFact(windows).load.drawToken).toEqual(expect.any(String));
}

describe('live draw sample CONTRACT review regressions', () => {
  test.each(['seq', 'run'] as const)('deferred draw retains the admitted sample after caller mutates %s', async (field) => {
    const { windows, image, item, draw } = await liveWindow();
    const sample: ReviewedSample = { run: 'run-a', boot: 'live-boot', seq: 10 };
    draw(sample); expect(liveFact(windows).liveSample).toBeNull();
    expect(liveFact(windows).load).toMatchObject({ state: 'loaded', drawToken: null });
    if (field === 'seq') sample.seq = 11; else sample.run = 'other-run';
    dom.frame(); expectLivePixels(windows, image, item);
    expect(liveFact(windows).liveSample).toEqual({ run: 'run-a', boot: 'live-boot', seq: 10 });
  });

  test.each([
    ['unknown-boot', { run: 'run-a', boot: null, seq: 10 }],
    ['unknown-seq', { run: 'run-a', boot: 'live-boot', seq: null }],
    ['unknown-both', { run: 'run-a', boot: null, seq: null }],
  ] as const)('%s draws the decoded image while retaining explicit unknown bridge facts', async (_label, sample) => {
    const { windows, image, item, draw } = await liveWindow(); draw(sample);
    expect(liveFact(windows).liveSample).toBeNull(); dom.frame(); expectLivePixels(windows, image, item);
    expect(liveFact(windows).liveSample).toEqual(sample);
    expect(liveFact(windows).unknown).toEqual(expect.arrayContaining([expect.stringMatching(/^live-sample-(?:incomplete|unobserved)$/)]));
    expect(liveFact(windows).liveSample?.boot).not.toBe('descriptor-boot');
    expect(liveFact(windows).liveSample?.seq).not.toBe(999);
  });

  test.each([
    ['known-fields', { run: 'run-a', boot: 'live-boot', seq: 10 }],
    ['optional-known', { run: 'run-a', boot: 'live-boot', seq: 10, frame: 12, epoch: 7 }],
    ['optional-unknown', { run: 'run-a', boot: 'live-boot', seq: 10, frame: null, epoch: null }],
    ['safe-boundary', { run: 'run-a', boot: 'b'.repeat(256), seq: Number.MAX_SAFE_INTEGER, frame: Number.MAX_SAFE_INTEGER, epoch: 0 }],
  ] as const)('%s preserves closed valid sample fields without latest-frame enrichment', async (_label, sample) => {
    const { windows, image, item, draw } = await liveWindow(); draw(sample); dom.frame(); expectLivePixels(windows, image, item);
    expect(liveFact(windows).liveSample).toEqual(sample);
    expect(liveFact(windows).unknown).not.toContain('live-sample-unobserved');
    expect(liveFact(windows).unknown).not.toContain('live-sample-incomplete');
  });

  test.each([
    ['wrong-run', { run: 'other-run' }], ['wrong-run-type', { run: 1 }],
    ['boot-number', { boot: 1 }], ['boot-boolean', { boot: true }], ['boot-empty', { boot: '' }], ['boot-too-long', { boot: 'b'.repeat(257) }],
    ['seq-boolean', { seq: true }], ['seq-string', { seq: '10' }], ['seq-fraction', { seq: 10.5 }],
    ['seq-negative', { seq: -1 }], ['seq-unsafe', { seq: Number.MAX_SAFE_INTEGER + 1 }], ['seq-infinite', { seq: Infinity }], ['seq-NaN', { seq: NaN }],
    ['frame-type', { frame: true }], ['frame-unsafe', { frame: Number.MAX_SAFE_INTEGER + 1 }],
    ['epoch-type', { epoch: '7' }], ['epoch-negative', { epoch: -1 }], ['epoch-unsafe', { epoch: Number.MAX_SAFE_INTEGER + 1 }],
    ['extra-key', { latestSeq: 99 }],
  ] as const)('%s is refused before replacing the current source image', async (_label, patch) => {
    const { windows, draw } = await liveWindow(); const before = root(windows, A).querySelector('img');
    let error: unknown;
    try { draw({ run: 'run-a', boot: 'live-boot', seq: 10, ...patch }); } catch (caught) { error = caught; }
    expect(error).toMatchObject({ code: 'source_mismatch' });
    dom.frame(); expect(root(windows, A).querySelector('img')).toBe(before);
    expect(liveFact(windows).liveSample).toBeNull(); expect(liveFact(windows).load.drawToken).toBeNull();
  });

  test.each(['boot', 'seq'] as const)('missing required %s is refused rather than treated as explicit null', async (field) => {
    const { windows, draw } = await liveWindow(); const before = root(windows, A).querySelector('img');
    const sample: Record<string, unknown> = { run: 'run-a', boot: 'live-boot', seq: 10 }; delete sample[field];
    let error: unknown;
    try { draw(sample); } catch (caught) { error = caught; }
    expect(error).toMatchObject({ code: 'source_mismatch' });
    dom.frame(); expect(root(windows, A).querySelector('img')).toBe(before);
    expect(liveFact(windows).liveSample).toBeNull(); expect(liveFact(windows).load.drawToken).toBeNull();
  });
});

// User capability contract: a supported picture operation's source failure is not a missing feature.
type ClassifiedFacts = Facts & { load: Facts['load'] & { errorCode?: string } };
function attemptView(windows: Renderer, action: 'zoom' | 'pan'): unknown {
  try { windows.dispatchUser({ action, instance: A, values: action === 'zoom' ? { factor: 2 } : { center: [0.25, 0.75] } }); }
  catch (error) { return error; }
  return undefined;
}
function classifiedFact(windows: Renderer): ClassifiedFacts { return fact(windows, A) as ClassifiedFacts; }

describe('supported view operation source and readiness classification', () => {
  test.each((['zoom', 'pan'] as const).flatMap((action) => (['unknown', 'missing', 'earlier-run'] as const).map((availability) => [action, availability] as const)))('%s on %s saved-image retains source_unavailable', async (action, availability) => {
    const { windows, sent } = await make(); const item = state(A, { ...source(), availability });
    item.delivery.url = ''; apply(windows, [item]);
    expect(classifiedFact(windows).load).toMatchObject({ state: 'error', errorCode: 'source_unavailable', drawToken: null });
    expect(attemptView(windows, action)).toMatchObject({ code: 'source_unavailable' });
    expect(classifiedFact(windows).source).toEqual(item.source); expect(sent).toHaveLength(0);
  });
  test.each(['zoom', 'pan'] as const)('%s with retained descriptor but no prepared bytes retains source_unavailable', async (action) => {
    const { windows, sent } = await make(); const item = state(); item.delivery.url = ''; apply(windows, [item]);
    expect(attemptView(windows, action)).toMatchObject({ code: 'source_unavailable' });
    expect(classifiedFact(windows).load).toMatchObject({ state: 'error', errorCode: 'source_unavailable', drawToken: null });
    expect(classifiedFact(windows).source).toEqual(item.source); expect(sent).toHaveLength(0);
  });
  test.each(['zoom', 'pan'] as const)('%s after decode failure retains the decode cause instead of a capability gap', async (action) => {
    const { windows, sent } = await make(); apply(windows, [state()]); await settleMicrotasks();
    (root(windows, A).querySelector('img') as TestImage).failDecode(); await settleMicrotasks();
    const error = attemptView(windows, action);
    // An honest pending preview is permitted; a refusal must retain the actual decoder cause.
    if (error !== undefined) expect(error).toMatchObject({ code: 'decode_failed' });
    else expect(sent.at(-1)).toMatchObject({ action, instance: A });
    expect(classifiedFact(windows).load).toMatchObject({ state: 'error', errorCode: 'decode_failed', drawToken: null });
  });
  test.each(['zoom', 'pan'] as const)('%s on an assigned loading picture is a supported preview with no draw proof', async (action) => {
    const { windows, sent } = await make(); apply(windows, [state()]);
    expect(attemptView(windows, action)).toBeUndefined(); expect(sent.at(-1)).toMatchObject({ action, instance: A });
    expect(classifiedFact(windows).load).toMatchObject({ state: 'loading', drawToken: null, naturalSize: null });
    await decoded(windows, A); expect(classifiedFact(windows).load.state).toBe('drawn');
    if (action === 'zoom') expect(fact(windows, A).view.zoom).toBe(2);
    else expect(fact(windows, A).view.pan).toEqual([0.25, 0.75]);
  });
  test.each(['zoom', 'pan'] as const)('%s on decoded pixels remains pending until the actual render callback', async (action) => {
    const { windows, sent } = await make(); apply(windows, [state()]); await settleMicrotasks();
    (root(windows, A).querySelector('img') as TestImage).finishDecode(); await settleMicrotasks();
    expect(classifiedFact(windows).load).toMatchObject({ state: 'loaded', drawToken: null });
    expect(attemptView(windows, action)).toBeUndefined(); expect(sent.at(-1)).toMatchObject({ action, instance: A });
    expect(classifiedFact(windows).load).toMatchObject({ state: 'loaded', drawToken: null });
    dom.frame(); expect(classifiedFact(windows).load.state).toBe('drawn');
  });
  test.each((['zoom', 'pan'] as const).flatMap((action) => (['lesson', 'written', 'video'] as const).map((kind) => [action, kind] as const)))('%s is genuinely unsupported for installed %s controls', async (action, kind) => {
    const { windows, sent } = await make(); const item = state(A, { ...source(), kind, media: kind === 'video' ? 'capture' : kind });
    apply(windows, [item]); expect(attemptView(windows, action)).toMatchObject({ code: 'unsupported_action' });
    expect(classifiedFact(windows).source).toEqual(item.source); expect(sent).toHaveLength(0);
  });
});

// Append-only view-paint producer contract: real Windows handlers, SYNTHETIC DOM/decode/rAF.
// This is a distinct changed-view receipt, not physical browser/compositor or live evidence.
type PaintedWindowFacts = Facts & { viewDrawRef: string | null };
function paintedWindow(windows: Renderer) { return fact(windows, A) as PaintedWindowFacts; }

describe('WorkspaceWindows completed view paint distinct from source media', () => {
  test.each([[100, 80], [0, -233]])('native positions use viewport pixels with host offset %s,%s', async (left, top) => {
    vi.stubGlobal('innerWidth', 1440); vi.stubGlobal('innerHeight', 800);
    dom.host.style.left = `${left}px`; dom.host.style.top = `${top}px`;
    dom.host.style.width = '600px'; dom.host.style.height = '800px';
    const { windows, sent } = await make(); const initial = state();
    initial.geometryIntent = { position: [200, 120], size: [360, 280] };
    apply(windows, [initial]); await decoded(windows, A, 1280, 720);
    expect(fact(windows, A).rect).toEqual([200, 120, 360, 280]);
    const bar = root(windows, A).querySelector('header')!;
    fire(bar, 'pointerdown', { pointerId: 1, clientX: 220, clientY: 130 });
    fire(bar, 'pointermove', { pointerId: 1, clientX: 250, clientY: 150 });
    fire(bar, 'pointerup', { pointerId: 1 });
    expect(fact(windows, A).rect.slice(0, 2)).toEqual([230, 140]);
    expect(sent.find(row => row.action === 'move')?.values).toEqual({ position: [230, 140] });
  });
  test('the reported placement area is the actual host intersection with the visual viewport', async () => {
    const module = await moduleAt('./panels.ts');
    expect(typeof module.windowPlacementArea).toBe('function');
    const host = { left: 30, top: -233, right: 630, bottom: 367 };
    expect(module.windowPlacementArea(host, [10, 20, 487, 803])).toEqual([30, 20, 467, 347]);
    expect(module.windowPlacementArea({ ...host, top: -600, bottom: -20 }, [0, 0, 487, 803])).toBeNull();
  });
  test('initial decoded media completes after layout moves before its first frame', async () => {
    const { windows } = await make(); apply(windows, [state()]); await settleMicrotasks();
    const image = root(windows, A).querySelector('img') as TestImage;
    image.finishDecode(148, 176); await settleMicrotasks();
    expect(paintedWindow(windows).load.state).toBe('loaded');
    windows.floating(A).moveTo(150, 100);
    dom.frame(); dom.frame();
    expect(paintedWindow(windows).load.state).toBe('drawn');
    expect(paintedWindow(windows).viewDrawRef).toEqual(expect.any(String));
    expect(paintedWindow(windows).rect.slice(0, 2)).toEqual([150, 100]);
  });
  test('initial window stays inside the visible portion of a scrolled camera host', async () => {
    vi.stubGlobal('innerWidth', 487); vi.stubGlobal('innerHeight', 803);
    dom.host.style.width = '487px'; dom.host.style.height = '578px'; dom.host.style.top = '-233px';
    const { windows } = await make(); const initial = state();
    initial.geometryIntent = { position: [0, 0], size: [320, 224] };
    apply(windows, [initial]); await decoded(windows, A, 1280, 720);
    const box = root(windows, A).getBoundingClientRect();
    expect(box.top).toBeGreaterThanOrEqual(0);
    expect(box.bottom).toBeLessThanOrEqual(345);
    expect(box.left).toBeGreaterThanOrEqual(0);
    expect(box.right).toBeLessThanOrEqual(487);
  });

  test('first actual guarded paint supplies a nullable then distinct stable view reference', async () => {
    const { windows } = await make(); apply(windows, [state()]); await settleMicrotasks();
    const image = root(windows, A).querySelector('img') as TestImage;
    expect(paintedWindow(windows).viewDrawRef).toBeNull();
    image.finishDecode(148, 176); await settleMicrotasks();
    expect(paintedWindow(windows).load).toMatchObject({ state: 'loaded', drawToken: null });
    expect(paintedWindow(windows).viewDrawRef).toBeNull();
    expect(image.style.transform).toMatch(/^matrix\(1, 0, 0, 1,/);
    dom.frame(); const painted = paintedWindow(windows);
    expect(painted.load.state).toBe('drawn'); expect(painted.load.drawToken).toEqual(expect.any(String));
    expect(painted.viewDrawRef).toEqual(expect.any(String)); expect(painted.viewDrawRef!.length).toBeGreaterThan(0);
    expect(painted.viewDrawRef!.length).toBeLessThanOrEqual(256); expect(painted.viewDrawRef).not.toBe(painted.load.drawToken);
    dom.frame(); expect(paintedWindow(windows).viewDrawRef).toBe(painted.viewDrawRef);
  });
  test.each(['hidden', 'disconnected', 'staged'] as const)('%s first paint cannot supply a view reference merely from decoded media', async (mode) => {
    const { windows } = await make(); const initial = { ...state(), staged: mode === 'staged' };
    apply(windows, [initial]); await settleMicrotasks();
    const node = root(windows, A), image = node.querySelector('img') as TestImage;
    image.finishDecode(148, 176); await settleMicrotasks();
    expect(paintedWindow(windows).load.state).toBe('loaded');
    if (mode === 'hidden') node.hidden = true;
    if (mode === 'disconnected') node.remove();
    if (mode === 'staged') expect(initial.staged).toBe(true);
    dom.frame(); expect(paintedWindow(windows).viewDrawRef).toBeNull();
    if (mode !== 'staged') expect(paintedWindow(windows).visible).toBe(false);
  });
});

test('Fit resets pan/zoom and fits the frame to the source in one fenced command', async () => {
  const { windows, sent } = await make(); const initial = state();
  initial.view = { zoom: 2, pan: [0.2, 0.8] };
  apply(windows, [initial]); await decoded(windows, A, 850, 250);
  const before = fact(windows, A).rect.slice();
  fire(control(windows, A, 'zoom-fit'), 'click'); dom.frame();
  const fitted = fact(windows, A);
  expect(fitted.rect.slice(0, 2)).toEqual(before.slice(0, 2));
  expect(fitted.contentRect[2] / fitted.contentRect[3]).toBeCloseTo(850 / 250);
  expect(fitted.view.zoom).toBe(1); expect(fitted.view.pan).toEqual([0.5, 0.5]);
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ action: 'zoom', values: { fit: true, size: fitted.rect.slice(2) } });
});

test('phone viewport transitions immediately expose restore controls for tucked windows', async () => {
  vi.stubGlobal('innerWidth', 1440);
  const { windows } = await make();
  apply(windows, [state(A), state(B, source('c15'))]);
  await decoded(windows, A); await decoded(windows, B);
  vi.stubGlobal('innerWidth', 390); windows.viewportChanged();
  expect(root(windows, A).hidden).toBe(true);
  expect(root(windows, B).hidden).toBe(false);
  const shelf = dom.host.querySelector('.workspace-hidden-shelf')!;
  expect(shelf.children.length).toBe(1);
  vi.stubGlobal('innerWidth', 1440); windows.viewportChanged();
  expect(root(windows, A).hidden).toBe(false);
  expect(shelf.children.length).toBe(0);
});


test('only new admitted presentation operations request shared phone focus; replay and background updates do not', async () => {
  const presented = vi.fn();
  const {windows} = await make(presented);
  const first = state();
  const batch = {context,requestId:'request-focus',commandId:'focus-open',actionOccurrenceId:'focus-action',op:'open',items:[first]};
  windows.apply(batch);
  expect(presented).toHaveBeenLastCalledWith(A, windows.root(A), expect.any(Function), true, 'request-focus');
  windows.apply(batch); expect(presented).toHaveBeenCalledTimes(1);
  windows.apply({...batch,commandId:'focus-update',op:'zoom',items:[{...first,revision:2,view:{zoom:2,pan:[.5,.5]}}]});
  expect(presented).toHaveBeenLastCalledWith(A, windows.root(A), expect.any(Function), false, 'request-focus');
  windows.apply({...batch,commandId:'focus-front',op:'front',items:[{...first,revision:3}]});
  expect(presented).toHaveBeenLastCalledWith(A, windows.root(A), expect.any(Function), true, 'request-focus');
});

test('closing the focused native window returns to its opener without stealing unrelated focus', async () => {
  const opener = new TestElement('button'), other = new TestElement('input');
  dom.host.append(opener, other);
  let focused: TestElement | null = opener;
  Object.defineProperty(dom.document, 'activeElement', {get:()=>focused});
  Object.defineProperty(opener, 'isConnected', {value:true});
  Object.assign(opener, {getClientRects:()=>[opener.getBoundingClientRect()], focus:vi.fn(()=>{focused=opener;})});
  const {windows} = await make();
  windows.apply({context,requestId:'focus',commandId:'focus-open',actionOccurrenceId:'focus-step',op:'open',items:[state()]});
  focused = control(windows,A,'close');
  fire(focused,'click');
  expect(focused).toBe(opener);
  expect((opener as unknown as {focus:ReturnType<typeof vi.fn>}).focus).toHaveBeenCalledTimes(1);
  windows.apply({context,requestId:'focus2',commandId:'focus-open2',actionOccurrenceId:'focus-step2',op:'open',items:[{...state(),revision:2,propertyVersions:{...state().propertyVersions,open:2}}]});
  focused = other;
  windows.dispatchUser({action:'close',instance:A,values:{}});
  expect(focused).toBe(other);
  expect((opener as unknown as {focus:ReturnType<typeof vi.fn>}).focus).toHaveBeenCalledTimes(1);
});

test('recovering a CSS-tucked native view requests restore rather than front without a visible baseline', async () => {
  const {windows,sent} = await make();
  apply(windows,[{...state(),propertyVersions:{...state().propertyVersions,hidden:0}}]);
  root(windows,A).dataset.resultTucked = 'true';
  (windows as Renderer & {restore(id:string):void}).restore(A);
  expect(sent.at(-1)?.action).toBe('restore');
});


test('full research link retains the native report finding and output identities',async()=>{
  const {windows}=await make();
  const written=state(A,{key:'retained-report',kind:'written',media:'written',availability:'retained',
    identity:{resource:{day:'2026-10-07',id:'r7',version:3,outputId:'o9'}},provenance:{}});
  written.delivery.report={title:'Retained report',summary:'Retained result',one_liner:'Retained',significance:'low',details:[],look_for:'',confidence:null,sources:[],refs:[],findingRevision:{version:2,digest:'exact-digest',complete:true}};
  apply(windows,[written]);
  const link=root(windows,A).querySelector('a')!;
  const route=new URL((link as unknown as HTMLAnchorElement).href,'http://localhost').hash;
  expect(route).toContain('#research/r7?');
  const query=new URLSearchParams(route.split('?')[1]);
  expect(Object.fromEntries(query)).toEqual({day:'2026-10-07',findings:'2',digest:'exact-digest',output:'o9',outputVersion:'3'});
});

// Live frames that arrive faster than the browser paints (a busy or background
// browser) keep the last painted frame reported as drawn; the newest is swapped in
// at the next animation frame, atomically, never left half done between paints.
describe('live frames faster than paints', () => {
  test('a painted live window stays drawn with what it painted until the next frame swaps the newest in', async () => {
    const { windows, image, item, draw } = await liveWindow();
    draw({ run: 'run-a', boot: 'live-boot', seq: 10 }); dom.frame(); expectLivePixels(windows, image, item);
    const painted = liveFact(windows).load.drawToken;
    const frameImage = (seq: number) => {
      const next = new TestImage(); next.src = `/synthetic/live-frame-${seq}.jpg`; next.currentSrc = next.src;
      next.naturalWidth = 640; next.naturalHeight = 480; next.complete = true; return next;
    };
    const later = (next: TestImage, seq: number) => (windows as Renderer & LiveSeam).drawLiveFrame(A, {
      image: next as unknown as HTMLImageElement, sample: { run: 'run-a', boot: 'live-boot', seq },
      sourceToken: item.delivery.sourceToken, sourceGeneration: item.sourceGeneration, lifetimeGeneration: item.lifetimeGeneration,
    });
    const eleven = frameImage(11), twelve = frameImage(12);
    later(eleven, 11); later(twelve, 12);
    expect(liveFact(windows)).toMatchObject({ staged: false, load: { state: 'drawn', drawToken: painted } });
    expect(liveFact(windows).liveSample).toEqual({ run: 'run-a', boot: 'live-boot', seq: 10 });
    expect(root(windows, A).querySelector('img')).toBe(image);
    dom.frame();
    expect(root(windows, A).querySelector('img')).toBe(twelve);
    expect(liveFact(windows)).toMatchObject({ staged: false, load: { state: 'drawn' } });
    expect(liveFact(windows).load.drawToken).not.toBe(painted);
    expect(liveFact(windows).liveSample).toEqual({ run: 'run-a', boot: 'live-boot', seq: 12 });
  });
});

describe('a studio in the background (another app or tab in front): laid out, not painted', () => {
  // Live 2026-10-11: a photo given to the studio while it was in the background was answered "could not ... the
  // window was hidden". The page paints nothing there; it paints what it was given once it is in view again.
  type Visibility = { visibilityState?: string };
  const page = () => document as unknown as Visibility;

  test('a window given in the background says it is laid out there, and is painted once the studio is shown', async () => {
    const { windows } = await make();
    page().visibilityState = 'hidden';
    apply(windows, [state()]);
    await decoded(windows, A); // a frame the page skipped painting in the background
    const hidden = fact(windows, A) as Facts & { visibility?: string };
    expect(hidden.visible).toBe(false);
    expect(hidden.visibility).toBe('tab-hidden');
    expect(hidden.load.state).toBe('loaded');
    page().visibilityState = 'visible';
    (windows as Renderer & { shownAgain(): void }).shownAgain();
    dom.frame();
    const shown = fact(windows, A) as Facts & { visibility?: string };
    expect(shown.load.state).toBe('drawn');
    expect(shown.visible).toBe(true);
    expect(shown.visibility).toBe('visible');
  });

  test('a window hidden in the layout is said to be, even while the studio is in the background', async () => {
    const { windows } = await make();
    apply(windows, [state()]); await decoded(windows, A);
    page().visibilityState = 'hidden';
    root(windows, A).hidden = true;
    expect((fact(windows, A) as Facts & { visibility?: string }).visibility).toBe('css-hidden');
  });
});
test('arrangement restoration preserves native placement identity and current view settings',async()=>{
 const {windows}=await make();apply(windows,[state()]);await decoded(windows,A);
 root(windows,A).dataset.placementId='placement-kept';
 const saved=windows.retainedPresentation().views[0];
 expect(windows.restorePresentation({...saved,placementId:'placement-kept',zoom:2,pan:[.2,.8]})).toBe(false);
 expect(windows.retainedPresentation().views[0]).toMatchObject({placementId:'placement-kept',zoom:2});
 windows.dispatchUser({action:'close',instance:A,values:{}});expect(windows.retainedPresentation().views).toEqual([]);
});
test('native placement restoration targets the matching placement among identical resources',async()=>{
 const {windows}=await make();apply(windows,[state(A),state(B)]);await decoded(windows,A);await decoded(windows,B);
 root(windows,A).dataset.placementId='first';root(windows,B).dataset.placementId='second';
 const saved=windows.retainedPresentation().views[0];
 windows.restorePresentation({...saved,placementId:'second',zoom:3,pan:[.3,.7]});
 expect(windows.retainedPresentation().views.map(v=>[v.placementId,v.zoom])).toEqual([['first',1],['second',3]]);
});


test('a rejected restoration stops without repeated writes and an explicit retry can recover',async()=>{
 const {windows,sent}=await make();apply(windows,[state()]);await decoded(windows,A);
 const wanted={...windows.retainedPresentation().views[0],zoom:2};
 expect(windows.restorePresentation(wanted)).toBe(false);windows.rejectManual(A);
 expect(()=>windows.restorePresentation(wanted)).toThrow('reopen');expect(sent).toHaveLength(1);
 // The caller terminated its failed wait. A subsequent explicit Retry begins anew.
 expect(windows.restorePresentation(wanted)).toBe(false);expect(sent).toHaveLength(2);
});

test('resize and move restore serially against accepted geometry revisions',async()=>{
 const {windows,sent}=await make();apply(windows,[state()]);await decoded(windows,A);
 const wanted={...windows.retainedPresentation().views[0],size:[480,400],position:[90,100]};
 expect(windows.restorePresentation(wanted)).toBe(false);expect(sent[0].action).toBe('resize');
 const accepted=state();accepted.revision++;accepted.geometryIntent.size=[480,400];accepted.propertyVersions.geometry++;
 apply(windows,[accepted],'resize-accepted');
 expect(windows.restorePresentation(wanted)).toBe(false);expect(sent[1].action).toBe('move');
 expect(sent[1].base.propertyVersions.geometry).toBe(4);
 accepted.revision++;accepted.geometryIntent.position=[90,100];accepted.propertyVersions.geometry++;
 apply(windows,[accepted],'move-accepted');expect(windows.restorePresentation(wanted)).toBe(true);
});

test('missing saved placement cannot steal another window showing identical bytes',async()=>{
 const {windows,sent}=await make();apply(windows,[state()]);await decoded(windows,A);
 root(windows,A).dataset.placementId='existing';
 const saved=windows.retainedPresentation().views[0];
 expect(windows.restorePresentation({...saved,placementId:'not-delivered',zoom:3})).toBe(false);
 expect(root(windows,A).dataset.placementId).toBe('existing');
 expect(windows.retainedPresentation().views[0].zoom).toBe(1);expect(sent).toHaveLength(0);
});


test('restoration refusal preserves the concrete recovery reason',async()=>{
 const {windows}=await make();apply(windows,[state()]);await decoded(windows,A);
 const wanted={...windows.retainedPresentation().views[0],zoom:2};
 windows.restorePresentation(wanted);
 windows.rejectManual(A,'The source changed; reopen this retained result.');
 expect(()=>windows.restorePresentation(wanted)).toThrow('The source changed; reopen this retained result.');
});


test('background arrangement restore does not race geometry with an unnecessary front command',async()=>{
 const {windows,sent}=await make();apply(windows,[state()]);await decoded(windows,A);
 windows.restore(A,false);
 expect(sent).toHaveLength(0);
 const wanted={...windows.retainedPresentation().views[0],size:[480,400]};
 expect(windows.restorePresentation(wanted)).toBe(false);
 expect(sent.map(command=>command.action)).toEqual(['resize']);
});

 test('background native delivery cannot raise above an explicitly fronted window',async()=>{
 let background=false;const presented=vi.fn();
 const {windows,panels}=await make(presented,()=>background);
 windows.apply({context,requestId:'first',commandId:'first',actionOccurrenceId:'first',items:[state(A)],op:'open'});
 await decoded(windows,A);windows.floating(A).front();const top=panels.top();
 background=true;
 windows.apply({context,requestId:'restore',commandId:'restore',actionOccurrenceId:'restore',items:[state(B)],op:'open'});
 await decoded(windows,B);
 expect(panels.top()).toBe(top);
 expect(Number(root(windows,B).style.zIndex)).toBeLessThan(Number(root(windows,A).style.zIndex));
 expect(presented.mock.calls.at(-1)?.[3]).toBe(false);
 });

test('explicit restore raises a background-created native placement after accepted delivery',async()=>{
 let background=true;const {windows,panels,sent}=await make(undefined,()=>background);
 windows.apply({context,requestId:'initial',commandId:'initial',actionOccurrenceId:'initial',items:[{...state(A),propertyVersions:{...state(A).propertyVersions,hidden:1}}],op:'open'});
 await decoded(windows,A);background=false;
 apply(windows,[state(B)],'other');await decoded(windows,B);windows.floating(B).front();
 root(windows,A).hidden=true;
 windows.restore(A,false);expect(sent).toHaveLength(0);expect(root(windows,A).hidden).toBe(true);
 windows.restore(A,true);expect(sent.at(-1)?.action).toBe('restore');
 expect(panels.top()?.id).toBe(B);
 const accepted={...state(A),revision:2,propertyVersions:{...state(A).propertyVersions,hidden:2}};
 windows.apply({context,requestId:sent.at(-1)!.requestId,commandId:'restored',actionOccurrenceId:'restored',items:[accepted],op:'restore'});
 dom.frame();expect(panels.top()?.id).toBe(A);
});

// will-tabs, 30 Oct (the isolated instance): a live close-up whose Tab was not in front kept receiving frames it could not
// paint, then its source held (the object left the frame: no more frames come). Moved to the Tab in front, it painted the
// frame it held but reported no sample, so its front was never confirmed ("No actual content draw is recorded").
describe('a live window shown again paints the frame it holds with that frame\'s sample', () => {
  test('hidden, held, then shown: its paint carries the held frame\'s sample', async () => {
    const { windows, image, item, draw } = await liveWindow();
    const sample: ReviewedSample = { run: 'run-a', boot: 'live-boot', seq: 10 };
    draw(sample); dom.frame(); expectLivePixels(windows, image, item);
    const shown = root(windows, A) as unknown as TestElement;
    shown.style.visibility = 'hidden'; // its Tab is not in front
    draw({ ...sample, seq: 11 }); dom.frame(); // the next frame arrives, and cannot be painted there
    expect(liveFact(windows).load.state).toBe('loaded');
    shown.style.visibility = ''; // its Tab comes to the front; no frame follows (the source holds its last one)
    (windows as Renderer & { shownAgain(): void }).shownAgain(); dom.frame();
    expect(liveFact(windows).load.state).toBe('drawn');
    expect(liveFact(windows).liveSample).toEqual({ ...sample, seq: 11 });
  });
  test('hidden, held, then its layout changes in sight (its Tab in front, a zoom): painted with the held frame\'s sample', async () => {
    const { windows, draw } = await liveWindow();
    const sample: ReviewedSample = { run: 'run-a', boot: 'live-boot', seq: 10 };
    draw(sample); dom.frame();
    const shown = root(windows, A) as unknown as TestElement;
    shown.style.visibility = 'hidden';
    draw({ ...sample, seq: 12 }); dom.frame();
    expect(liveFact(windows).load.state).toBe('loaded');
    shown.style.visibility = ''; windows.dispatchUser({ action: 'zoom', instance: A, values: { factor: 2 } }); dom.frame();
    expect(liveFact(windows).load.state).toBe('drawn');
    expect(liveFact(windows).liveSample).toEqual({ ...sample, seq: 12 });
  });
});
