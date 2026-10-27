// Panels (AGENTS.md "Seeing and acting at once"): the magnifier floats over the dark surround
// beside the eyepiece, away from its object and never over it or another panel; no room there,
// the corner farthest from it; on phones top right. It stays put while it fits; a place put over its object
// (the user's drop over it, or a move asked for over the view) is kept over that object only, and any other
// place yields when it would cover the object it shows. Escape closes one panel per
// press, the one on top.
import { describe, expect, test } from 'vitest';
import { showing } from './magnifier';
import {
  coversObject, droppedByUser, FloatingWindow, forObjectNow, keptOverIt, movedByAgent, NOT_PLACED, notesBeside, PanelStack, panelWidth, placeAway,
  presentPlace, type Box, type EscapeEvent, type Panel, type Placed, type Placing, type Spot,
} from './panels';
import type { MagnifierState } from './protocol';

const CHROME = 28;
const boxOf = (s: Spot): Box => [s.x, s.y, s.x + s.w, s.y + (s.w * 3) / 4 + CHROME];
const overlaps = (a: Box, b: Box) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];
const thing = (x: number, y: number, r = 20): Box => [x - r, y - r, x + r, y + r];

// A desktop view: 248 px of surround on each side of the eyepiece.
const desk = (fields: Partial<Placing> = {}): Placing => {
  const area = { width: 1200, height: 800 };
  return { area, disc: [600, 400, 330], object: null, obstacles: [], size: { width: panelWidth(area), chrome: CHROME }, ...fields };
};

describe('placeAway', () => {
  test('a new window keeps clear of what sits in the stage\'s corners, the footage player first (live 2026-10-11)', () => {
    const player: Box = [10, 10, 230, 60]; // top-left: the footage player
    const spot = boxOf(placeAway(desk({ labels: [player] })));
    expect(overlaps(spot, player)).toBe(false);
  });

  test('floats beside the eyepiece on the side away from its object', () => {
    const right = placeAway(desk({ object: thing(820, 400) }));
    expect(right).toMatchObject({ kind: 'beside', x: 10, y: 10, w: 248 });
    const left = placeAway(desk({ object: thing(380, 400) }));
    expect(left).toMatchObject({ kind: 'beside', x: 942, y: 10 });
  });

  test('never overlaps its object or another panel', () => {
    const card: Box = [930, 10, 1190, 330]; // the details card, top right
    for (let x = 300; x <= 900; x += 50) {
      for (let y = 100; y <= 700; y += 50) {
        const object = thing(x, y);
        const spot = boxOf(placeAway(desk({ object, obstacles: [card] })));
        expect(overlaps(spot, object), `object at ${x},${y}`).toBe(false);
        expect(overlaps(spot, card), `object at ${x},${y}`).toBe(false);
      }
    }
  });

  test('a wide magnifier is narrowed until it clears its object, never placed over it (M1.5c review item 3)', () => {
    for (const width of [600, 900, 1400]) {
      for (let x = 300; x <= 900; x += 100) {
        for (let y = 150; y <= 650; y += 100) {
          const object = thing(x, y);
          const spot = placeAway(desk({ object, size: { width, chrome: CHROME } }));
          expect(overlaps(boxOf(spot), object), `width ${width}, object at ${x},${y}`).toBe(false);
          expect(spot.w).toBeLessThanOrEqual(width);
        }
      }
    }
    const phone = { width: 390, height: 844 };
    const onPhone = placeAway({ area: phone, disc: [195, 300, 180], object: thing(195, 300), obstacles: [], size: { width: 380, chrome: CHROME } });
    expect(overlaps(boxOf(onPhone), thing(195, 300))).toBe(false);
  });

  test('keeps clear of labels when it moves, and labels make way when it stays', () => {
    const label: Box = [10, 30, 200, 80]; // a margin note where it would go
    const moved = placeAway(desk({ object: thing(820, 400), labels: [label] }));
    expect(moved).toMatchObject({ x: 10, kind: 'beside' });
    expect(moved.y).toBeGreaterThan(80); // the bottom of the same side
    const first = placeAway(desk({ object: thing(820, 400) }));
    expect(placeAway(desk({ object: thing(820, 400), labels: [label], kept: first }))).toMatchObject({ x: first.x, y: first.y, kind: 'kept' });
  });

  test('stays put while it still fits, and moves when its object comes under it', () => {
    const first = placeAway(desk({ object: thing(820, 400) })); // left, top
    // The slide moved the object over to the left: its place still fits, so it stays.
    const kept = placeAway(desk({ object: thing(400, 600), kept: first }));
    expect(kept).toMatchObject({ x: first.x, y: first.y, kind: 'kept' });
    // Without that place it would go to the side away from the object.
    expect(placeAway(desk({ object: thing(400, 600) })).x).toBeGreaterThan(600);
    // The object under it: it moves.
    const object = thing(240, 120);
    const moved = placeAway(desk({ object, kept: first }));
    expect(moved.kind).not.toBe('kept');
    expect(overlaps(boxOf(moved), object)).toBe(false);
  });

  test('with no room beside the eyepiece, the corner farthest from its object', () => {
    const area = { width: 1000, height: 800 };
    const spot = placeAway({ area, disc: [500, 400, 390], object: thing(700, 150), obstacles: [], size: { width: panelWidth(area), chrome: CHROME } });
    const h = (spot.w * 3) / 4 + CHROME;
    expect(spot).toMatchObject({ kind: 'corner', x: 10 });
    expect(spot.y).toBeCloseTo(800 - h - 10);
  });

  test('on a phone, top right (top left when its object is there)', () => {
    const area = { width: 390, height: 700 };
    const size = { width: panelWidth(area), chrome: CHROME };
    const spot = placeAway({ area, disc: [195, 300, 180], object: thing(150, 400), obstacles: [], size });
    expect(spot).toMatchObject({ kind: 'corner', y: 10 });
    expect(spot.x + spot.w).toBeCloseTo(380);
    const there = placeAway({ area, disc: [195, 300, 180], object: thing(330, 60), obstacles: [], size });
    expect(there).toMatchObject({ x: 10, y: 10 });
  });

  // A place over its object stays only for the object it was put over, and only as asked (going live, G3; its
  // review, B2: a drag or an "over" from earlier must not cover every later object).
  const A = thing(600, 400);
  const over = { x: 500, y: 300, w: 300 }; // a place that covers A
  const kept = (placed: Placed, objectId: string | null, object: Box = A) =>
    placeAway(desk({ object, objectId, remembered: { ...over, placed } }));

  test('a drop over its own object stays there, until it is moved again', () => {
    const dropped = kept(droppedByUser('o1', true), 'o1');
    expect(dropped).toMatchObject({ kind: 'remembered', x: 500, y: 300, w: 300 });
    expect(overlaps(boxOf(dropped), A)).toBe(true);
    expect(dropped).not.toHaveProperty('placed');
    // Somewhere clear of the object, a drop always stays.
    expect(placeAway(desk({ object: thing(500, 400), objectId: 'o1', remembered: { x: 20, y: 500, w: 300, placed: droppedByUser('o1', false) } })))
      .toMatchObject({ kind: 'remembered', x: 20, y: 500 });
    // Nobody put it anywhere (opening, following, a new object): the default keeps clear of it.
    expect(overlaps(boxOf(placeAway(desk({ object: A, objectId: 'o1' }))), A)).toBe(false);
  });

  test('a drop made clear of its object yields when the object comes under it', () => {
    const yielded = kept(droppedByUser('o1', false), 'o1');
    expect(yielded.kind).not.toBe('remembered');
    expect(overlaps(boxOf(yielded), A)).toBe(false);
  });

  test('a place kept over object A yields for object B', () => {
    for (const placed of [droppedByUser('o1', true), movedByAgent('o1', true)]) {
      expect(kept(placed, 'o1').kind).toBe('remembered');
      const forB = kept(forObjectNow(placed, 'o2'), 'o2');
      expect(forB.kind).not.toBe('remembered');
      expect(overlaps(boxOf(forB), A)).toBe(false);
      expect(kept(placed, 'o2').kind).not.toBe('remembered'); // the object it was put over, not this one
    }
    // A drag remembered from earlier today was put over no object now.
    expect(kept(NOT_PLACED, 'o1').kind).not.toBe('remembered');
  });

  test('a place asked for over the view stays there, even over its object (going live, G3)', () => {
    // "Overlay it right on top of the live view": the user's own request, never refused for covering the object.
    const asked = kept(movedByAgent('o1', true), 'o1');
    expect(asked).toMatchObject({ kind: 'remembered', x: 500, y: 300, w: 300 });
    expect(overlaps(boxOf(asked), A)).toBe(true);
    // The same place the agent chose without being asked for it over the view keeps clear of it.
    expect(kept(movedByAgent('o1', false), 'o1').kind).not.toBe('remembered');
    expect(placeAway(desk({ object: thing(500, 400), objectId: 'o1', remembered: { x: 20, y: 500, w: 300, placed: movedByAgent('o1', false) } })))
      .toMatchObject({ kind: 'remembered', x: 20, y: 500 });
  });

  test('who put it where: a drop, a move, another object', () => {
    expect(droppedByUser('o1', true)).toEqual({ by: 'user', over: false, forObject: 'o1', coversAtDrop: true });
    expect(movedByAgent('o1', true)).toEqual({ by: 'agent', over: true, forObject: 'o1', coversAtDrop: false });
    expect(forObjectNow(movedByAgent('o1', true), 'o1')).toEqual(movedByAgent('o1', true));
    expect(forObjectNow(movedByAgent('o1', true), 'o2')).toMatchObject({ over: false, coversAtDrop: false });
    expect(forObjectNow(droppedByUser('o1', true), null)).toMatchObject({ coversAtDrop: false });
    expect(keptOverIt(droppedByUser('o1', true), 'o1')).toBe(true);
    expect(keptOverIt(droppedByUser('o1', false), 'o1')).toBe(false);
    expect(keptOverIt(movedByAgent('o1', false), 'o1')).toBe(false);
    expect(keptOverIt(undefined, 'o1')).toBe(false);
    expect(NOT_PLACED).toMatchObject({ by: 'user', coversAtDrop: false, over: false });
  });

  test('covering is read with the same margin placeAway keeps, or none for the screen report', () => {
    expect(coversObject([0, 0, 100, 100], [110, 10, 150, 50])).toBe(true); // within 14 px: placeAway would move it
    expect(coversObject([0, 0, 100, 100], [110, 10, 150, 50], 0)).toBe(false); // the report: not over it
    expect(coversObject([0, 0, 100, 100], [50, 50, 150, 150], 0)).toBe(true);
    expect(coversObject(null, [0, 0, 1, 1])).toBe(false);
    expect(coversObject([0, 0, 1, 1], null)).toBe(false);
  });

  test('a move the server sends is taken with its over, a resize with its width', () => {
    expect(presentPlace({ rect: [10, 20, 300, 200], over: true }, 'move')).toEqual({ x: 10, y: 20, w: null, over: true });
    expect(presentPlace({ rect: [10, 20, 300, 200] }, 'resize')).toEqual({ x: 10, y: 20, w: 300, over: false });
    expect(presentPlace({ rect: [10, 20, 300, 200], over: 'yes' }, 'move')?.over).toBe(false); // only a true over
    expect(presentPlace({ restore: true }, 'move')).toBeNull();
    expect(presentPlace({ rect: [10, 'a', 1, 2] }, 'move')).toBeNull();
  });
});

describe('labels beside the magnifier', () => {
  test('start below it when it floats at the top, end above it when it floats at the bottom', () => {
    expect(notesBeside([10, 10, 258, 224], 600, 800)).toEqual({ side: 'left', top: 258, bottom: 770 });
    expect(notesBeside([942, 576, 1190, 790], 600, 800)).toEqual({ side: 'right', top: 28, bottom: 542 });
    expect(notesBeside(null, 600, 800)).toBeNull();
  });
});

describe('the magnifier', () => {
  const state = (fields: Partial<MagnifierState> = {}): MagnifierState => ({
    type: 'magnifier', space: 'frame', open: true, objectId: 'o1', sources: [{ kind: 'live', space: 'frame', label: 'Live' }, { kind: 'held', space: 'capture', label: 'Held' }], index: 0, ...fields,
  });

  test("a new object or source starts its own out-of-view clock; a level change does not", () => {
    expect(showing(state())).toBe(showing(state({ zoom: 4, pixelated: true })));
    expect(showing(state())).not.toBe(showing(state({ objectId: 'o2' })));
    expect(showing(state())).not.toBe(showing(state({ index: 1 })));
    expect(showing(state())).not.toBe(showing(state({ open: false })));
  });
});

describe('PanelStack', () => {
  class Fake implements Panel {
    open = false;
    closed = 0;
    readonly id: string;
    readonly whileTyping: boolean;
    constructor(id: string, whileTyping = false) {
      this.id = id;
      this.whileTyping = whileTyping;
    }
    isOpen() {
      return this.open;
    }
    close() {
      this.closed++;
      this.open = false;
    }
    footprint() {
      return this.open ? { left: 0, top: 0, right: 10, bottom: 10 } : null;
    }
  }
  const press = (target: EventTarget | null = null): EscapeEvent & { prevented: boolean } => {
    const event = { key: 'Escape', target, defaultPrevented: false, prevented: false, preventDefault: () => (event.prevented = event.defaultPrevented = true) };
    return event;
  };
  const stack = () => {
    const panels = new PanelStack();
    const [magnifier, viewer, card, menu] = ['magnifier', 'viewer', 'card', 'menu'].map((id) => panels.register(new Fake(id, id === 'menu')));
    return { panels, magnifier, viewer, card, menu };
  };

  test('Escape closes exactly one, the one on top, per press', () => {
    const { panels, magnifier, viewer, card } = stack();
    magnifier.open = viewer.open = card.open = true;
    const first = press();
    expect(panels.onEscape(first)).toBe(true);
    expect(first.prevented).toBe(true);
    expect([magnifier.closed, viewer.closed, card.closed]).toEqual([0, 0, 1]);
    panels.onEscape(press());
    expect([magnifier.closed, viewer.closed, card.closed]).toEqual([0, 1, 1]);
    panels.onEscape(press());
    expect([magnifier.closed, viewer.closed, card.closed]).toEqual([1, 1, 1]);
  });

  test('with nothing open, Escape is not taken', () => {
    const { panels } = stack();
    const event = press();
    expect(panels.onEscape(event)).toBe(false);
    expect(event.prevented).toBe(false);
  });

  test('a text field keeps its Escape unless the panel on top owns its fields', () => {
    const { panels, card, menu } = stack();
    const field = { closest: () => ({}) } as unknown as EventTarget; // focus in an input
    card.open = true;
    expect(panels.onEscape(press(field))).toBe(false);
    menu.open = true;
    expect(panels.onEscape(press(field))).toBe(true);
    expect([card.closed, menu.closed]).toEqual([0, 1]);
  });

  test('something else took the press: nothing more closes', () => {
    const { panels, card } = stack();
    card.open = true;
    expect(panels.onEscape({ ...press(), defaultPrevented: true })).toBe(false);
    expect(card.closed).toBe(0);
  });

  test('obstacles are the other open panels; the room is the most anything asks for', () => {
    const { panels, magnifier, card } = stack();
    magnifier.open = card.open = true;
    expect(panels.obstacles('magnifier')).toHaveLength(1);
    panels.reserve(() => ({ shelf: 40 }));
    panels.register({ ...new Fake('side'), isOpen: () => true, close: () => {}, footprint: () => null, reserves: () => ({ right: 300 }) });
    expect(panels.reserved({ left: 0, top: 0, right: 1200, bottom: 800 })).toEqual({ right: 300, bottom: 0, shelf: 40, left: 0, edge: 0 });
  });
});

// Appended recovered-layout regressions. Existing cases above remain unchanged.
import { vi as layoutVi } from 'vitest';
import { fire, syntheticDOM } from './workspace-windows.test-support';

test('keyboard movement keeps native foreground settled until an admitted raise', () => {
  const dom = syntheticDOM(), panels = new PanelStack(), changes: string[] = [];
  layoutVi.stubGlobal('document', dom.document);
  layoutVi.stubGlobal('window', { innerWidth: 1200, innerHeight: 800 });
  const shells: FloatingWindow[] = [];
  try {
    const make = (id: string) => {
      const root = dom.document.createElement('section'), bar = dom.document.createElement('header');
      root.dataset.studyArtifact = 'true'; root.append(bar); dom.host.append(root);
      const shell = panels.register(new FloatingWindow({ id, root: root as unknown as HTMLElement,
        bar: bar as unknown as HTMLElement, panels, implicitFront: false,
        close: () => {}, content: () => ({ kind: 'text' }), facts: () => null,
        onUserChange: property => changes.push(`${id}:${property}`) }));
      shells.push(shell); shell.frame();
      return { root, bar, shell };
    };
    const back = make('back'), front = make('front');
    const before = back.root.getBoundingClientRect();
    const stackBefore = [back.root.style.zIndex, front.root.style.zIndex];
    fire(back.bar, 'keydown', { key: 'ArrowRight', shiftKey: false });
    expect(back.root.getBoundingClientRect().left).toBe(before.left + 10);
    expect(changes).toEqual(['back:move']);
    expect(panels.top()?.id).toBe('front');
    expect([back.root.style.zIndex, front.root.style.zIndex]).toEqual(stackBefore);
    // Native owners call front only when the admitted foreground command applies.
    back.shell.front();
    expect(panels.top()?.id).toBe('back');
  } finally {
    for (const shell of shells) shell.dispose();
    layoutVi.unstubAllGlobals();
  }
});

test('expanding a document fits the visible stage and restores its own arrangement without replacing content', () => {
  const dom=syntheticDOM(), panels=new PanelStack();
  const viewport={offsetLeft:0,offsetTop:100,width:1000,height:600};
  layoutVi.stubGlobal('document',dom.document);
  layoutVi.stubGlobal('window',{innerWidth:1200,innerHeight:800,visualViewport:viewport});
  const root=dom.document.createElement('section'), bar=dom.document.createElement('header'), draft=dom.document.createElement('input');
  draft.value='unsaved correction';root.append(bar,draft);dom.host.append(root);
  const changes:string[]=[];
  const shell=panels.register(new FloatingWindow({id:'specialist',root:root as unknown as HTMLElement,bar:bar as unknown as HTMLElement,
    panels,freeResize:true,close:()=>{},content:()=>({kind:'text'}),facts:()=>null,onUserChange:property=>changes.push(property)}));
  try {
    shell.resizeTo(420,380);shell.moveTo(120,140);
    shell.toggleExpanded();
    expect(shell.isExpanded()).toBe(true);
    expect(root.getBoundingClientRect()).toMatchObject({left:0,top:100,width:1000,height:600});
    viewport.width=800;viewport.height=500;shell.frame();
    expect(root.getBoundingClientRect()).toMatchObject({left:0,top:100,width:800,height:500});
    shell.toggleExpanded();
    expect(shell.isExpanded()).toBe(false);
    expect(root.getBoundingClientRect()).toMatchObject({left:120,top:140,width:420,height:380});
    expect(root.children).toContain(draft);expect(draft.value).toBe('unsaved correction');
    expect(changes).toEqual(['geometry','geometry']);
    shell.toggleExpanded();shell.moveTo(30,110);
    expect(shell.isExpanded()).toBe(false);
    expect(root.getBoundingClientRect()).toMatchObject({left:30,top:110,width:420,height:380});
    shell.toggleExpanded();shell.resizeTo(500,400);
    expect(shell.isExpanded()).toBe(false);
    expect(root.getBoundingClientRect()).toMatchObject({left:30,top:110,width:500,height:400});
  } finally {shell.dispose();layoutVi.unstubAllGlobals();}
});

describe('recovered floating stack ordering', () => {
  test('registering a window during layout does not visit it until the next frame', () => {
    const dom = syntheticDOM(), panels = new PanelStack(), visited: string[] = [];
    const make = (id: string): Panel => ({ id, isOpen: () => true, close: () => {}, footprint: () => null, frame: () => { visited.push(id); } });
    let added = false;
    panels.register({ ...make('a'), frame: () => { visited.push('a'); if (!added) { added = true; panels.register(make('c')); } } });
    panels.register(make('b'));
    layoutVi.stubGlobal('document', dom.document); layoutVi.stubGlobal('requestAnimationFrame', dom.raf);
    try {
      panels.start(dom.host as unknown as HTMLElement, () => {});
      dom.frame(); expect(visited).toEqual(['a', 'b']);
      dom.frame(); expect(visited).toEqual(['a', 'b', 'a', 'b', 'c']);
    } finally { layoutVi.unstubAllGlobals(); }
  });
  test('explicit raise orders UUID windows within their group without overtaking a modal', () => {
    const panels = new PanelStack(), closed: string[] = [], opened = new Set(['a', 'b', 'modal']);
    const make = (id: string, group: string): Panel & { group: string } => ({ id, group, isOpen: () => opened.has(id), close: () => { closed.push(id); opened.delete(id); }, footprint: () => null });
    panels.register(make('a', 'window')); panels.register(make('b', 'window')); panels.register(make('modal', 'modal'));
    const raising = panels as PanelStack & { raise(id: string): void };
    expect(typeof raising.raise, 'feature absent: recovered PanelStack.raise').toBe('function');
    raising.raise('a'); expect(panels.top()?.id).toBe('modal');
    const escape = () => panels.onEscape({ key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} });
    escape(); escape(); expect(closed).toEqual(['modal', 'a']); expect(opened.has('b')).toBe(true);
  });
});

test('Escape respects menu visual priority above a result window registered later', () => {
  const stack = new PanelStack();
  let menu = true, photo = true;
  stack.register({ id: 'models', escapePriority: 100, isOpen: () => menu, close: () => { menu = false; }, footprint: () => null });
  stack.register({ id: 'late-photo', group: 'window', isOpen: () => photo, close: () => { photo = false; }, footprint: () => null });
  stack.raise('late-photo');
  const event = () => ({ key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} });
  expect(stack.onEscape(event())).toBe(true);
  expect(menu).toBe(false); expect(photo).toBe(true);
  expect(stack.onEscape(event())).toBe(true); expect(photo).toBe(false);
});

test('Escape leaves the page in place until its Showing strip has been dismissed', () => {
  const stack = new PanelStack(); let page = true, showing = true;
  stack.register({ id: 'showing', escapePriority: 50, isOpen: () => showing, close: () => { showing = false; }, footprint: () => null });
  stack.register({ id: 'page', escapePriority: 20, isOpen: () => page, close: () => { page = false; }, footprint: () => null });
  stack.onEscape({ key: 'Escape', target: null, defaultPrevented: false, preventDefault: () => {} });
  expect(showing).toBe(false); expect(page).toBe(true);
});

// The side bar (sidebar.ts; once the sessions rail) on the live view's left: closed, its strip is an edge things keep
// right of; open, it is reserved as the side column is, so the eyepiece glides right of it and new windows keep clear.
import { placeFrame } from './view';
import type { FrameHeader } from './protocol';

describe('the side bar', () => {
  const header = { w: 1280, h: 720, fov: [640, 360, 300], dx: 0, dy: 0, epoch: 0 } as unknown as FrameHeader;
  const disc = (p: { scale: number; ox: number; oy: number }) => [p.ox + 640 * p.scale, p.oy + 360 * p.scale, 300 * p.scale] as const;
  const rail = (open: boolean): Panel => ({ id: 'sidebar', isOpen: () => open, close: () => {},
    footprint: () => (open ? { left: 0, top: 0, right: 240, bottom: 800 } : null), reserves: () => (open ? { left: 248, edge: 248 } : { edge: 40 }) });

  test('closed, its strip is an edge only; open, it reserves its width beside the side column\'s', () => {
    const panels = new PanelStack();
    panels.register(rail(false));
    expect(panels.reserved({ left: 0, top: 0, right: 1200, bottom: 800 })).toEqual({ right: 0, bottom: 0, shelf: 0, left: 0, edge: 40 });
    const open = new PanelStack();
    open.register(rail(true));
    open.register({ id: 'side', isOpen: () => true, close: () => {}, footprint: () => null, reserves: () => ({ right: 300 }) });
    expect(open.reserved({ left: 0, top: 0, right: 1200, bottom: 800 })).toEqual({ right: 300, bottom: 0, shelf: 0, left: 248, edge: 248 });
    expect(open.obstacles('magnifier')).toEqual([{ left: 0, top: 0, right: 240, bottom: 800 }]); // new windows keep clear
  });

  test('the eyepiece glides right of the open rail, never under it; with no rail it is where it always was', () => {
    const before = placeFrame(header, 1056, 736, 0, false, 0);
    expect(placeFrame(header, 1056, 736, 0, false, 0, 0)).toEqual(before);
    const [cx, , r] = disc(before);
    const after = placeFrame(header, 1056, 736, 0, false, 0, 248);
    const [cx2, cy2, r2] = disc(after);
    expect(cx2 - r2).toBeGreaterThanOrEqual(248); // clear of the rail
    expect(cx2).toBeGreaterThan(cx); // it moved over
    expect(cx2).toBeCloseTo(248 + (1056 - 248) / 2, 6); // centred in what is left
    expect(r2).toBeLessThanOrEqual(r);
    expect(cy2).toBeCloseTo(736 / 2, 6);
    // Halfway open (the rail's width eases), halfway over: it glides, it does not jump.
    const [cx3] = disc(placeFrame(header, 1056, 736, 0, false, 0, 124));
    expect(cx3).toBeGreaterThan(cx);
    expect(cx3).toBeLessThan(cx2);
  });

  test('new windows keep right of the rail: the strip or the open rail, wherever their object is', () => {
    for (const left of [40, 248]) {
      for (let x = 350; x <= 1000; x += 130) {
        for (let y = 100; y <= 700; y += 150) {
          const spot = placeAway(desk({ area: { width: 1200, height: 800, left }, disc: [Math.max(600, left + 360), 400, 330], object: thing(x, y),
            obstacles: left > 40 ? [[0, 0, 240, 800]] : [] }));
          expect(spot.x, `rail ${left}, object at ${x},${y}`).toBeGreaterThanOrEqual(left + 10 - 0.5);
        }
      }
    }
    // With nothing on the left, the corners are where they were.
    expect(placeAway(desk({ object: thing(1000, 120) })).x).toBe(10);
  });
});


test('detached control presentation does not raise an older overlapping window', () => {
  const dom = syntheticDOM(), panels = new PanelStack(), shells: FloatingWindow[] = [];
  layoutVi.stubGlobal('document', dom.document);
  layoutVi.stubGlobal('window', { innerWidth: 1200, innerHeight: 800 });
  try {
    const make = (id: string) => {
      const root = dom.document.createElement('section'), bar = dom.document.createElement('header');
      root.append(bar); dom.host.append(root);
      const shell = panels.register(new FloatingWindow({ id, root: root as unknown as HTMLElement,
        bar: bar as unknown as HTMLElement, panels, close: () => {}, content: () => ({ kind: 'text' }), facts: () => null }));
      shells.push(shell); shell.frame(); return { root, shell };
    };
    const older = make('revision-3'), latest = make('revision-4');
    const controls = dom.document.createElement('section'), close = dom.document.createElement('button');
    controls.className = 'capability-controls'; controls.append(close); older.root.append(controls);
    latest.shell.front();
    // Dispatch at the ancestor as its capture listener receives the nested target.
    fire(older.root, 'pointerdown', { target: close });
    expect(panels.top()?.id).toBe('revision-4');
    fire(older.root, 'pointerdown', { target: older.root });
    expect(panels.top()?.id).toBe('revision-3');
  } finally { for (const shell of shells) shell.dispose(); layoutVi.unstubAllGlobals(); }
});

test('a newly opened document stays clear of the title rows (and Close) of windows already open, and keeps its place', () => {
  const dom = syntheticDOM(), panels = new PanelStack(), shells: FloatingWindow[] = [];
  layoutVi.stubGlobal('document', dom.document);
  layoutVi.stubGlobal('window', { innerWidth: 1200, innerHeight: 800 });
  try {
    const make = (id: string, cascade: boolean, frontOnOpen = true, size: [number, number] = [640, 500]) => {
      const root = dom.document.createElement('section'), bar = dom.document.createElement('header');
      bar.style.height = '48px'; root.append(bar); dom.host.append(root);
      const shell = panels.register(new FloatingWindow({ id, root: root as unknown as HTMLElement, bar: bar as unknown as HTMLElement,
        panels, freeResize: true, cascade, frontOnOpen, close: () => {}, content: () => ({ kind: 'text' }), facts: () => null }), { background: !frontOnOpen });
      shells.push(shell); shell.resizeTo(...size); shell.frame();
      return { root, bar };
    };
    const first = make('first', true), second = make('second', true), third = make('third', true);
    const head = (w: { bar: unknown }) => (w.bar as HTMLElement).getBoundingClientRect();
    const box = (w: { root: unknown }) => (w.root as HTMLElement).getBoundingClientRect();
    const apart = (a: DOMRect, b: DOMRect) => a.right <= b.left || b.right <= a.left || a.bottom <= b.top || b.bottom <= a.top;
    // Each newer window leaves every earlier title row (with its Close) uncovered.
    expect(apart(box(second), head(first))).toBe(true);
    expect(apart(box(third), head(first)) && apart(box(third), head(second))).toBe(true);
    expect(box(third).bottom).toBeLessThanOrEqual(800);
    const kept = box(first);
    for (const shell of shells) shell.frame();
    expect(box(first)).toMatchObject({ left: kept.left, top: kept.top });
    // A result placed behind the open windows (a run's completion) keeps its own title row out from under them.
    const behind = make('behind', true, false, [400, 300]);
    for (const other of [first, second, third]) {
      const [title, over] = [head(behind), box(other)];
      expect(title.right <= over.left || over.right <= title.left || title.bottom <= over.top || over.bottom <= title.top).toBe(true);
    }
    // Its opener sizing it afterwards places it again for that size, still below the earlier title rows.
    (shells[2] as FloatingWindow).resizeTo(640, 560);
    expect(apart(box(third), head(first)) && apart(box(third), head(second))).toBe(true);
    // A position someone chose is kept through a later resize.
    (shells[1] as FloatingWindow).moveTo(300, 20); (shells[1] as FloatingWindow).resizeTo(500, 300);
    expect(box(second)).toMatchObject({ left: 300, top: 20 });
    // Without the option, placement is unchanged: the usual place, over the others.
    const restored = make('restored', false);
    expect(box(restored).top).toBe(box(first).top);
  } finally { for (const shell of shells) shell.dispose(); layoutVi.unstubAllGlobals(); }
});
