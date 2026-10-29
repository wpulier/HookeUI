// The voice's marks (marks.ts): every target kind found by Hooke's own ids, the frame's normalised places mapped as the
// outlines are, the reply that says what was drawn, and the marks going when the speech ends.
import { expect, test, vi } from 'vitest';
import { arrow, fovCrop, fromNormalised, Marks, markShapes, parseTarget, resolveTarget, roughBox, SPEECH_TAIL_MS, UNSPOKEN_MS,
  type DrawnMark, type FrameAnchor, type MarkDocument, type MarkElement, type MarkHooks, type Place, type Point } from './marks';

interface Node { tag: string; classes: string[]; attrs: Record<string, string>; parent?: string; rect: [number, number, number, number];
  hidden?: boolean; text?: string; label?: string }
/** A page of plain elements and the selectors marks.ts uses: tag, .class, [attr] and [attr="v"], and "#id > ". */
function page(nodes: Node[]): MarkDocument {
  const matches = (n: Node, selector: string) => {
    let rest = selector.trim();
    const child = /^#([\w-]+)\s*>\s*/.exec(rest);
    if (child) { if (n.parent !== child[1]) return false; rest = rest.slice(child[0].length); }
    const tag = /^[a-z]+/.exec(rest);
    if (tag) { if (n.tag !== tag[0]) return false; rest = rest.slice(tag[0].length); }
    for (const part of rest.matchAll(/\.([\w-]+)|\[([\w-]+)(?:="((?:[^"\\]|\\.)*)")?\]/g)) {
      if (part[1] && !n.classes.includes(part[1])) return false;
      if (part[2] && (n.attrs[part[2]] === undefined || (part[3] !== undefined && n.attrs[part[2]] !== part[3].replace(/\\(.)/g, '$1')))) return false;
    }
    return true;
  };
  const element = (n: Node): MarkElement => ({
    getBoundingClientRect: () => ({ left: n.rect[0], top: n.rect[1], width: n.rect[2], height: n.rect[3] }),
    closest: (selector: string) => (n.hidden && selector.includes('[hidden]') ? {} : null),
    querySelector: (selector: string) => (selector === '.workspace-tab-label' && n.label !== undefined ? { textContent: n.label } : null),
    textContent: n.text ?? n.label ?? '',
    dataset: {},
  });
  return {
    querySelector: (selector) => { const n = nodes.find(n => matches(n, selector)); return n ? element(n) : null; },
    querySelectorAll: (selector) => nodes.filter(n => matches(n, selector)).map(element),
  };
}

const tab = (id: string, title: string, x: number): Node =>
  ({ tag: 'button', classes: ['workspace-tab'], attrs: { id: `work-tab-${id}`, 'data-workspace-view-tab': id }, rect: [x, 40, 90, 28], label: title });

function hooks(nodes: Node[], over: Partial<MarkHooks> = {}): MarkHooks {
  return {
    doc: page(nodes), viewport: () => [1440, 900],
    outline: (id) => id === 'o12' ? { rect: [300, 200, 40, 30] } : id === 'o9' ? { why: 'out-of-view' } : { why: 'not-found' },
    member: (pop, m) => pop === 'p1' && m === '3' ? { rect: [500, 400, 12, 12] } : { why: 'hidden' },
    anchor: () => ({ crop: [100, 0, 800, 800], offset: [0, 0, 1] }),
    frame: (points) => points.map(([x, y]) => [x / 2, y / 2] as Point),
    panel: (name) => name === 'magnifier' ? { rect: [1100, 80, 300, 300] } : { why: 'hidden' },
    researcherRids: (n) => n === 3 ? ['r7'] : n === 4 ? ['r8', 'r9'] : [],
    ...over,
  };
}

const rect = (place: Place) => ('rect' in place ? place.rect : place.why);

test('the target grammar: each kind by its own id, anything else a bad target', () => {
  expect(parseTarget('outline:o12')).toEqual({ kind: 'outline', id: 'o12' });
  expect(parseTarget('member:pop:a:17')).toEqual({ kind: 'member', population: 'pop:a', member: '17' });
  expect(parseTarget('window:w-3')).toEqual({ kind: 'window', id: 'w-3' });
  expect(parseTarget('document:pl-1')).toEqual({ kind: 'document', id: 'pl-1' });
  expect(parseTarget('tab:Measurements')).toEqual({ kind: 'tab', key: 'Measurements' });
  expect(parseTarget('researcher:3')).toEqual({ kind: 'researcher', key: '3' });
  expect(parseTarget('panel:magnifier')).toEqual({ kind: 'panel', name: 'magnifier' });
  expect(parseTarget('frame')).toEqual({ kind: 'frame' });
  for (const bad of ['', 'outline:', 'cell:o1', 'member:p1', 'member:p1:', 'o12', 42]) expect(parseTarget(bad)).toBe(null);
});

test('outlines and members come from the live view, out of view or hidden said as such', () => {
  const h = hooks([]);
  expect(rect(resolveTarget({ target: 'outline:o12' }, h, null))).toEqual([300, 200, 40, 30]);
  expect(rect(resolveTarget({ target: 'outline:o9' }, h, null))).toBe('out-of-view');
  expect(rect(resolveTarget({ target: 'outline:o404' }, h, null))).toBe('not-found');
  expect(rect(resolveTarget({ target: 'member:p1:3' }, h, null))).toEqual([500, 400, 12, 12]);
  expect(rect(resolveTarget({ target: 'member:p2:3' }, h, null))).toBe('hidden');
  expect(rect(resolveTarget({ target: 'nonsense' }, h, null))).toBe('bad-target');
});

test('windows, documents and panels by their own ids; a hidden one is not drawn', () => {
  const h = hooks([
    { tag: 'section', classes: ['workspace-window'], attrs: { 'data-presentation-id': 'w-1' }, rect: [600, 120, 400, 300] },
    { tag: 'section', classes: ['workspace-window'], attrs: { 'data-presentation-id': 'w-2' }, rect: [0, 0, 400, 300], hidden: true },
    { tag: 'div', classes: [], attrs: { 'data-placement-id': 'pl-1' }, parent: 'study-artifacts', rect: [50, 300, 500, 400] },
    { tag: 'div', classes: [], attrs: { 'data-placement-id': 'pl-2' }, parent: 'elsewhere', rect: [50, 300, 500, 400] },
  ]);
  expect(rect(resolveTarget({ target: 'window:w-1' }, h, null))).toEqual([600, 120, 400, 300]);
  expect(rect(resolveTarget({ target: 'window:w-2' }, h, null))).toBe('hidden');
  expect(rect(resolveTarget({ target: 'window:w-9' }, h, null))).toBe('not-found');
  expect(rect(resolveTarget({ target: 'document:pl-1' }, h, null))).toEqual([50, 300, 500, 400]);
  expect(rect(resolveTarget({ target: 'document:pl-2' }, h, null))).toBe('not-found');
  expect(rect(resolveTarget({ target: 'panel:magnifier' }, h, null))).toEqual([1100, 80, 300, 300]);
  expect(rect(resolveTarget({ target: 'panel:slidemap' }, h, null))).toBe('hidden');
});

test('a Tab by its id or its exact title; two Tabs of one title are ambiguous, never a guess', () => {
  const h = hooks([tab('main', 'Live', 0), tab('t2', 'Measurements', 100), tab('t3', 'Notes', 200), tab('t4', 'Notes', 300)]);
  expect(rect(resolveTarget({ target: 'tab:t2' }, h, null))).toEqual([100, 40, 90, 28]);
  expect(rect(resolveTarget({ target: 'tab:Measurements' }, h, null))).toEqual([100, 40, 90, 28]);
  expect(rect(resolveTarget({ target: 'tab:measurements' }, h, null))).toEqual([100, 40, 90, 28]);
  expect(rect(resolveTarget({ target: 'tab:Notes' }, h, null))).toBe('ambiguous');
  expect(rect(resolveTarget({ target: 'tab:Data' }, h, null))).toBe('not-found');
});

test('a researcher by its number or rid: its board row, else its card; two on screen with one number are ambiguous', () => {
  const row = (rid: string, x: number, hidden = false): Node =>
    ({ tag: 'div', classes: ['mm-row-wrap', 'researchers-board-row'], attrs: { 'data-rid': rid }, rect: [x, 500, 240, 48], hidden });
  const card = (rid: string): Node => ({ tag: 'div', classes: ['researcher-card'], attrs: { 'data-rid': rid }, rect: [900, 600, 300, 120] });
  const h = hooks([row('r7', 20), row('r8', 20, true), card('r8'), row('r9', 300), row('r5', 20, true)]);
  expect(rect(resolveTarget({ target: 'researcher:3' }, h, null))).toEqual([20, 500, 240, 48]);
  expect(rect(resolveTarget({ target: 'researcher:r7' }, h, null))).toEqual([20, 500, 240, 48]);
  expect(rect(resolveTarget({ target: 'researcher:r8' }, h, null))).toEqual([900, 600, 300, 120]); // its row is hidden: its card
  expect(rect(resolveTarget({ target: 'researcher:4' }, h, null))).toBe('ambiguous');
  expect(rect(resolveTarget({ target: 'researcher:r5' }, h, null))).toBe('hidden');
  expect(rect(resolveTarget({ target: 'researcher:12' }, h, null))).toBe('not-found');
});

test('a frame region is normalised in the eyepiece crop the model saw, then placed as the outlines are', () => {
  // services/orchestrator/marks.py fov_crop, the same rounding.
  expect(fovCrop([960, 540, 500], 1920, 1080)).toEqual([450, 30, 1020, 1020]);
  expect(fovCrop(null, 1920, 1080)).toEqual([0, 0, 1920, 1080]);
  expect(fovCrop([100, 100, 500], 1920, 1080)).toEqual([0, 0, 610, 610]);
  expect(fromNormalised([0.5, 0.25], [450, 30, 1020, 1020])).toEqual([960, 285]);
  const seen: Point[][] = [];
  const anchor: FrameAnchor = { crop: [100, 0, 800, 800], offset: [0, 0, 1] };
  const h = hooks([], { frame: (points) => { seen.push(points); return points.map(([x, y]) => [x / 2, y / 2] as Point); } });
  const region = resolveTarget({ target: 'frame', region: [0.25, 0.5, 0.5, 0.25] }, h, anchor);
  expect(seen[0]).toEqual([[300, 400], [700, 400], [700, 600], [300, 600]]); // frame px of the crop
  expect(rect(region)).toEqual([150, 200, 200, 100]);
  const polygon = resolveTarget({ target: 'frame', polygon: [[0, 0], [1, 0], [0.5, 1]] }, h, anchor);
  expect(polygon).toEqual({ rect: [50, 0, 400, 400], shape: [[50, 0], [450, 0], [250, 400]] });
  expect(rect(resolveTarget({ target: 'frame', region: [0.2, 0.2, 0, 0.1] }, h, anchor))).toBe('bad-target');
  expect(rect(resolveTarget({ target: 'frame', polygon: [[0, 0], [1, 1]] }, h, anchor))).toBe('bad-target');
  expect(rect(resolveTarget({ target: 'frame', region: [0.9, 0.9, 0.2, 0.2] }, h, anchor))).toEqual([410, 360, 40, 40]); // kept inside
  expect(rect(resolveTarget({ target: 'frame', region: [3, 3, 0.2, 0.2] }, h, anchor))).toBe('bad-target');
  expect(rect(resolveTarget({ target: 'frame', region: [0.2, 0.2, 0.1, 0.1] }, h, null))).toBe('out-of-view'); // no picture
  const gone = hooks([], { frame: () => ({ why: 'out-of-view' }) });
  expect(rect(resolveTarget({ target: 'frame', region: [0.2, 0.2, 0.1, 0.1] }, gone, anchor))).toBe('out-of-view');
});

function controller(h: MarkHooks = hooks([tab('t2', 'Measurements', 100)])) {
  let now = 0;
  const timers: { at: number; fn: () => void; live: boolean }[] = [];
  const painted: DrawnMark[][] = [];
  const frames: (() => void)[] = [];
  const marks = new Marks({ hooks: h, paint: (drawn) => painted.push(drawn), now: () => now,
    setTimer: (fn, ms) => { const t = { at: now + ms, fn, live: true }; timers.push(t); return t; },
    clearTimer: (t) => { (t as { live: boolean }).live = false; },
    frame: (fn) => frames.push(fn) });
  const advance = (ms: number) => {
    const end = now + ms;
    for (;;) {
      const next = timers.filter(t => t.live && t.at <= end).sort((a, b) => a.at - b.at)[0];
      if (!next) break;
      now = next.at; next.live = false; next.fn();
    }
    now = end;
  };
  const tick = () => { const fn = frames.shift(); fn?.(); };
  return { marks, painted, advance, tick, last: () => painted.at(-1) ?? [] };
}

test('the reply says which marks were drawn, with their places, and why the others were not; drawn at once', () => {
  const { marks, painted } = controller();
  const resolved = marks.show({ type: 'marks', id: 'm1', until: 'speech-end',
    marks: [{ target: 'tab:Measurements', style: 'box' }, { target: 'outline:o9' }, { target: 'tab:Data', style: 'underline' }] });
  expect(resolved).toEqual([
    { target: 'tab:Measurements', ok: true, rect: [100, 40, 90, 28] },
    { target: 'outline:o9', ok: false, why: 'out-of-view' },
    { target: 'tab:Data', ok: false, why: 'not-found' },
  ]);
  expect(painted.length).toBe(1); // painted in show itself, not a frame later
  expect(painted[0].map(m => [m.target, m.style, m.setId])).toEqual([['tab:Measurements', 'box', 'm1']]);
});

test('marks stay while the voice speaks and go about 1.5 s after it stops', () => {
  const { marks, advance, tick, last } = controller();
  marks.show({ type: 'marks', id: 'm1', until: 'speech-end', marks: [{ target: 'tab:t2' }] });
  advance(3000); // waiting for speech: still there
  expect(marks.current).toBe('m1');
  marks.phase('speaking');
  advance(4000);
  marks.phase('listening');
  advance(SPEECH_TAIL_MS - 100);
  expect(marks.current).toBe('m1');
  marks.phase('speaking'); // a pause between sentences is not the end
  advance(2000);
  marks.phase('listening');
  advance(SPEECH_TAIL_MS);
  expect(marks.current).toBe(null);
  expect(last()[0].leaving).not.toBe(null); // fading out
  advance(300); tick();
  expect(last()).toEqual([]);
});

test('marks never spoken over go after a while; a numeric until is kept; marks-clear and a new set replace', () => {
  const a = controller();
  a.marks.show({ type: 'marks', id: 'm1', marks: [{ target: 'tab:t2' }] });
  a.advance(UNSPOKEN_MS + 10);
  expect(a.marks.current).toBe(null);
  const b = controller();
  b.marks.phase('speaking');
  b.marks.show({ type: 'marks', id: 'm2', until: 4000, marks: [{ target: 'tab:t2' }] });
  b.marks.phase('listening');
  b.advance(SPEECH_TAIL_MS + 100);
  expect(b.marks.current).toBe('m2'); // its own time, not the speech
  b.advance(3000);
  expect(b.marks.current).toBe(null);
  const c = controller();
  c.marks.show({ type: 'marks', id: 'm3', marks: [{ target: 'tab:t2' }] });
  c.marks.clear('other');
  expect(c.marks.current).toBe('m3');
  c.marks.show({ type: 'marks', id: 'm4', marks: [{ target: 'tab:t2', style: 'point' }] });
  expect(c.last().map(m => [m.setId, m.leaving === null])).toEqual([['m3', false], ['m4', true]]);
  c.marks.clear();
  expect(c.marks.current).toBe(null);
});

test('each frame finds the target again: a mark follows its outline, and is not drawn while it is out of view', () => {
  let place: Place = { rect: [300, 200, 40, 30] };
  const { marks, tick, last } = controller(hooks([], { outline: () => place }));
  marks.show({ type: 'marks', id: 'm1', marks: [{ target: 'outline:o12' }] });
  place = { rect: [320, 210, 40, 30] }; // the slide moved
  tick();
  expect(last()[0].rect).toEqual([320, 210, 40, 30]);
  place = { why: 'out-of-view' };
  tick();
  expect(last()).toEqual([]);
  place = { rect: [330, 215, 40, 30] };
  tick();
  expect(last()[0].rect).toEqual([330, 215, 40, 30]);
});

test('the drawing is calm: drawn in over a moment, steady from frame to frame, faded out', () => {
  const mark: DrawnMark = { key: 'm1#0', setId: 'm1', target: 'outline:o12', style: 'box', rect: [300, 200, 40, 30], born: 0, leaving: null };
  const early = markShapes(mark, 80, [1440, 900]), done = markShapes(mark, 1000, [1440, 900]);
  expect(early.paths[0].length).toBeLessThan(done.paths[0].length); // drawn in
  expect(markShapes(mark, 2000, [1440, 900])).toEqual(done); // no wobble over time
  expect(roughBox([300, 200, 40, 30], 1)).toEqual(roughBox([300, 200, 40, 30], 1));
  const box = roughBox([300, 200, 40, 30], 1);
  for (const [x, y] of box) { expect(x).toBeGreaterThan(290); expect(x).toBeLessThan(352); expect(y).toBeGreaterThan(190); expect(y).toBeLessThan(242); }
  expect(markShapes({ ...mark, leaving: 1000 }, 1100, [1440, 900]).opacity).toBeLessThan(1);
  expect(markShapes({ ...mark, leaving: 1000 }, 1000, [1440, 900], true).opacity).toBe(0); // reduced motion: gone at once
  // An arrow comes in from the side with room and stops just short of the target.
  const { head } = arrow([1300, 100, 40, 30], [1440, 900], 1);
  expect(head[1][0]).toBeLessThan(1300); expect(head[1][1]).toBeGreaterThan(130);
});

test('show answers within a few milliseconds for a full set', () => {
  const { marks } = controller();
  const started = performance.now();
  marks.show({ type: 'marks', id: 'm1', marks: Array.from({ length: 12 }, () => ({ target: 'tab:t2' })) });
  expect(performance.now() - started).toBeLessThan(50);
  vi.restoreAllMocks();
});
