// The Working strip over a synthetic DOM (workspace-feel plan, step 3): its order (needs you, working, finished), pills
// drawn in place with their clocks ticking, work under a second never shown, compact before anything folds into +N, ×
// and click handed to each pill's owner, the compact meters under a narrow Stage, and the reach under the Assistant.
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { clock, orderPills, pillWords, TIGHT_STAGE, WorkingStrip, type StripPill } from './working-strip';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';

const NOW = 1_791_600_000_000;
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
  const dom = syntheticDOM();
  vi.stubGlobal('document', Object.assign(dom.document, {
    createElementNS: (_ns: string, tag: string) => new TestElement(tag),
    documentElement: { clientWidth: 1440 },
    body: Object.assign(dom.host, { dataset: {} as Record<string, string> }),
  }));
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const calls: string[] = [];
function pill(id: string, fields: Partial<StripPill> = {}): StripPill {
  return { id, name: id, cell: 'working', urgency: 1, since: NOW - 5000, counting: true, title: `${id} title`,
    hide: `Hide ${id} from the strip`, open: () => calls.push(`open ${id}`), dismiss: () => calls.push(`hide ${id}`), ...fields };
}
/** A row whose width is its shown pills' (120 px each, 80 compact, +N 50, 6 px apart): what the fit measures in a browser. */
function measuredRow(row: TestElement, room: number) {
  Object.defineProperty(row, 'clientWidth', { configurable: true, get: () => room });
  Object.defineProperty(row, 'scrollWidth', { configurable: true, get: () => {
    const shown = row.children.filter((child) => !child.hidden);
    const compact = row.parentElement?.dataset.compact !== undefined;
    return shown.reduce((sum, child) => sum + (child.classList.contains('strip-more') ? 50 : compact ? 80 : 120), 0) + Math.max(0, shown.length - 1) * 6;
  } });
}
function strip(options: { room?: number; stage?: number; studioRight?: number } = {}) {
  const pills = new TestElement('section'); pills.id = 'shelf';
  const hint = new TestElement('p'); hint.id = 'hint'; hint.textContent = 'Click anything in the view to outline it.';
  const end = new TestElement('div');
  const stage = new TestElement('main');
  Object.defineProperty(stage, 'clientWidth', { get: () => options.stage ?? 1016 });
  const studio = new TestElement('div');
  studio.getBoundingClientRect = () => ({ right: options.studioRight ?? 1080 }) as DOMRect;
  if (options.room !== undefined) measuredRow(pills, options.room);
  const s = new WorkingStrip({ pills: pills as unknown as HTMLElement, hint: hint as unknown as HTMLElement, end: end as unknown as HTMLElement,
    stage: stage as unknown as HTMLElement, studio: studio as unknown as HTMLElement, now: () => Date.now() });
  const root = s.root as unknown as TestElement;
  const shown = () => pills.children.filter((c) => !c.hidden && !c.classList.contains('strip-more')).map((c) => c.dataset.id);
  return { s, root, pills, hint, end, shown };
}

test('a clock, never a percent', () => {
  expect(clock(2_000)).toBe('0:02');
  expect(clock(754_000)).toBe('12:34');
  expect(clock(3_674_000)).toBe('1:01:14');
  expect(clock(-5)).toBe('0:00');
  expect(pillWords(pill('a', { counting: false, words: 'Needs you' }), NOW)).toBe('Needs you');
  expect(pillWords(pill('a'), NOW)).toBe('0:05');
});

test('needs you first, then working, then finished; oldest first within each', () => {
  const order = orderPills([
    pill('done', { urgency: 2, since: NOW - 9000 }), pill('newer', { since: NOW - 1000 }), pill('needs', { urgency: 0, since: NOW }),
    pill('older', { since: NOW - 8000 }), pill('failed', { urgency: 0, since: NOW - 100 }),
  ]);
  expect(order.map((p) => p.id)).toEqual(['failed', 'needs', 'older', 'newer', 'done']);
});

test('the strip holds the pills, the passing line and the meters; the line leaves room to the pills when they come', () => {
  const { s, root, pills, hint, end } = strip();
  expect(root.tagName).toBe('FOOTER');
  expect(root.getAttribute('aria-label')).toBe('Working now');
  expect(root.children.slice(0, 2)).toEqual([pills, hint]);
  expect(root.children.at(-1)).toBe(end);
  expect(pills.hidden).toBe(true);
  expect('busy' in root.dataset).toBe(false);
  s.set('researchers', [pill('r1')]);
  s.flush();
  expect(pills.hidden).toBe(false);
  expect('busy' in root.dataset).toBe(true); // the stylesheet hides the idle line, keeps one with news
  s.set('researchers', []);
  s.flush();
  expect(pills.hidden).toBe(true);
  expect('busy' in root.dataset).toBe(false);
  const css = readFileSync(new URL('./workspace-feel.css', import.meta.url), 'utf8');
  expect(css).toContain(".working-strip[data-busy] > .hint:not(:has(.hint-close)) { display: none; }");
  expect(css).toContain("grid-template-rows: 44px 32px minmax(0, 1fr) auto 40px !important;");
});

test('pills are drawn in place: the same element, its cell changed in place, its clock ticking every second', () => {
  const { s, pills, shown } = strip();
  s.set('researchers', [pill('r1'), pill('r2', { urgency: 0, counting: false, words: 'Needs you', cell: 'needs' })]);
  s.flush();
  expect(shown()).toEqual(['r2', 'r1']);
  const first = pills.querySelector('[data-id="r1"]')!;
  const cell = first.querySelector('[data-kind="lit-cell"]')!;
  expect(first.querySelector('.strip-pill-state')!.textContent).toBe('0:05');
  expect(first.querySelector('.strip-pill-open')!.getAttribute('aria-label')).toBe('r1, 0:05');
  vi.advanceTimersByTime(3000);
  expect(first.querySelector('.strip-pill-state')!.textContent).toBe('0:08'); // ticked, not rebuilt
  s.set('researchers', [pill('r1', { cell: 'ready', urgency: 2, counting: false, words: 'Lesson ready' }), pill('r2', { urgency: 0, counting: false, words: 'Needs you', cell: 'needs' })]);
  s.flush();
  expect(pills.querySelector('[data-id="r1"]')).toBe(first);
  expect(first.querySelector('[data-kind="lit-cell"]')).toBe(cell);
  expect(cell.getAttribute('data-state')).toBe('ready');
  expect(cell.classList.contains('lit-cell-settle')).toBe(true); // one settle as its work turns ready
  expect(first.dataset.cell).toBe('ready');
  expect(first.querySelector('.strip-pill-state')!.textContent).toBe('Lesson ready');
  expect(shown()).toEqual(['r2', 'r1']);
});

test('a pill that lights after the first look rises and settles once; those there at the first look do not', () => {
  const { s, pills } = strip();
  s.set('researchers', [pill('r1')]);
  s.flush();
  const first = pills.querySelector('[data-id="r1"]')!;
  expect(first.classList.contains('strip-pill-rise')).toBe(false);
  s.set('researchers', [pill('r1'), pill('r2')]);
  s.flush();
  const second = pills.querySelector('[data-id="r2"]')!;
  expect(second.classList.contains('strip-pill-rise')).toBe(true);
  expect(second.querySelector('[data-kind="lit-cell"]')!.classList.contains('lit-cell-settle')).toBe(true);
});

test('work under a second never flashes a pill', () => {
  const { s, shown } = strip();
  s.set('run', [pill('run:1', { since: NOW, after: NOW + 1000 })]);
  s.flush();
  expect(shown()).toEqual([]);
  vi.advanceTimersByTime(1100);
  s.flush();
  expect(shown()).toEqual(['run:1']);
});

test('click opens where the work lives; × hides the pill through its owner', () => {
  calls.length = 0;
  const { s, pills } = strip();
  s.set('researchers', [pill('r1')]);
  s.flush();
  const node = pills.querySelector('[data-id="r1"]')!;
  node.querySelector('.strip-pill-open')!.dispatchEvent(new Event('click'));
  node.querySelector('.strip-pill-x')!.dispatchEvent(new Event('click'));
  expect(calls).toEqual(['open r1', 'hide r1']);
  expect(node.querySelector('.strip-pill-x')!.getAttribute('aria-label')).toBe('Hide r1 from the strip');
});

test('out of room: compact first, then the least urgent fold into +N, which opens Researchers; the first always shows', () => {
  const { s, root, pills, shown } = strip({ room: 380 });
  let listed = 0;
  s.openList = (where) => { listed++; expect(where).toBe('researchers'); };
  // Three full pills (120 px) fit in 380 px with their gaps.
  s.set('researchers', [pill('a', { since: NOW - 3 }), pill('b', { since: NOW - 2 }), pill('c', { since: NOW - 1 })]);
  s.flush();
  expect(shown()).toEqual(['a', 'b', 'c']);
  expect('compact' in root.dataset).toBe(false);
  // Four do not; compact (80 px) they do.
  s.set('researchers', [pill('a', { since: NOW - 3 }), pill('b', { since: NOW - 2 }), pill('c', { since: NOW - 1 }), pill('d', { since: NOW })]);
  s.flush();
  expect('compact' in root.dataset).toBe(true);
  expect(shown()).toEqual(['a', 'b', 'c', 'd']);
  // Six compact ones do not: the last fold into +N, its cell the most urgent of them.
  s.set('researchers', [pill('research:n', { urgency: 0, cell: 'needs', counting: false, words: 'Needs you' }), ...['a', 'b', 'c', 'd'].map((id, i) => pill(`research:${id}`, { since: NOW - 10 + i })),
    pill('research:done', { urgency: 2, cell: 'ready', counting: false, words: 'Done' })]);
  s.flush();
  const more = pills.querySelector('.strip-more')!;
  expect(more.hidden).toBe(false);
  expect(shown()).toEqual(['research:n', 'research:a', 'research:b']);
  expect(more.querySelector('.strip-pill-name')!.textContent).toBe('+3');
  expect(more.querySelector('[data-kind="lit-cell"]')!.getAttribute('data-state')).toBe('working');
  expect(more.querySelector('.strip-pill-open')!.getAttribute('aria-label')).toBe('3 more. Open Researchers');
  more.querySelector('.strip-pill-open')!.dispatchEvent(new Event('click'));
  expect(listed).toBe(1);
  // A tiny strip still shows the most urgent one.
  measuredRow(pills, 60);
  s.set('steps', []);
  s.flush();
  expect(shown()).toEqual(['research:n']);
});

test('+N says and opens Activity when it folded a run, a load or a step; Researchers only for researchers alone', () => {
  const { s, pills } = strip({ room: 380 });
  const opened: string[] = [];
  s.openList = (where) => { opened.push(where); };
  s.set('researchers', ['a', 'b', 'c', 'd', 'e'].map((id, i) => pill(`research:${id}`, { since: NOW - 10 + i })));
  s.set('runs', [pill('run:r1', { since: NOW })]);
  s.flush();
  const more = pills.querySelector('.strip-more')!;
  expect(more.querySelector('.strip-pill-open')!.getAttribute('aria-label')).toMatch(/more\. Open Activity$/);
  more.querySelector('.strip-pill-open')!.dispatchEvent(new Event('click'));
  expect(opened).toEqual(['activity']);
});

test('with the pills too narrow even for the first and +N, the passing line has given way: +N is never cut', () => {
  const { s, root, pills } = strip();
  // The row's room grows when the line gives way (data-squeezed: it steps aside).
  measuredRow(pills, 120);
  Object.defineProperty(pills, 'clientWidth', { configurable: true, get: () => ('squeezed' in root.dataset ? 220 : 120) });
  s.set('researchers', ['a', 'b', 'c', 'd'].map((id, i) => pill(`research:${id}`, { since: NOW - 10 + i })));
  s.flush();
  const more = pills.querySelector('.strip-more')!;
  expect(more.hidden).toBe(false);
  expect('squeezed' in root.dataset).toBe(true);
  const row = pills as unknown as { scrollWidth: number; clientWidth: number };
  expect(row.scrollWidth).toBeLessThanOrEqual(row.clientWidth);
  // Room again: nothing squeezed.
  Object.defineProperty(pills, 'clientWidth', { configurable: true, get: () => 600 });
  s.set('researchers', [pill('research:a')]);
  s.flush();
  expect('squeezed' in root.dataset).toBe(false);
});

test('beside a long passing line, pills keep their words: the line shrinks first and steps aside before a pill would', () => {
  const css = readFileSync(new URL('./workspace-feel.css', import.meta.url), 'utf8');
  // As wide as its words (its × follows them, never at the row's far end); among pills it takes all the shrinking.
  expect(css).toContain("position: relative; flex: 0 1 auto; min-width: 0; margin: 0 auto 0 0;");
  expect(css).toContain(".working-strip[data-busy] > .hint { flex: 0 100000 auto; min-width: 0; }");
  expect(css).toContain(".working-strip[data-busy] > .hint:has(.hint-close, .hint-action) { min-width: 8em; }");
  expect(css).toContain(".working-strip[data-squeezed] > .hint { min-width: 0; }");
  expect(css).toContain(".working-strip[data-squeezed] > .hint:not(:has(.hint-action)) { display: none; }");
  expect(css).not.toContain('min-width: 14em');
});

test('a pill never folds or goes compact to make room for a passing line: the line steps aside first', () => {
  const { s, root, pills, shown } = strip();
  // Four pills fit in 500 px only once the line's floor gives way (it held 120 px of the row).
  measuredRow(pills, 380);
  Object.defineProperty(pills, 'clientWidth', { configurable: true, get: () => ('squeezed' in root.dataset ? 500 : 380) });
  s.set('researchers', ['a', 'b', 'c', 'd'].map((id, i) => pill(`research:${id}`, { since: NOW - 10 + i })));
  s.flush();
  expect('squeezed' in root.dataset).toBe(true);
  expect('compact' in root.dataset).toBe(false);
  expect(pills.querySelector('.strip-more')!.hidden).toBe(true);
  expect(shown()).toEqual(['research:a', 'research:b', 'research:c', 'research:d']);
  // Room for the pills beside it: the line stays.
  Object.defineProperty(pills, 'clientWidth', { configurable: true, get: () => 600 });
  s.set('researchers', ['a', 'b', 'c'].map((id, i) => pill(`research:${id}`, { since: NOW - 10 + i })));
  s.flush();
  expect('squeezed' in root.dataset).toBe(false);
});

test('a narrow Stage keeps the meters compact; the strip reaches under the Assistant unless a tool is docked there', () => {
  const wide = strip({ stage: 1016, studioRight: 1080 });
  wide.s.flush();
  expect('tight' in wide.root.dataset).toBe(false);
  expect(wide.root.style.marginRight).toBe('-360px');
  const narrow = strip({ stage: TIGHT_STAGE - 1, studioRight: 1440 });
  narrow.s.flush();
  expect('tight' in narrow.root.dataset).toBe(true);
  expect(narrow.root.style.marginRight ?? '').toBe('');
  (document.body as unknown as { dataset: Record<string, string> }).dataset.controlRightActive = 'true';
  const docked = strip({ stage: 900, studioRight: 1000 });
  docked.s.flush();
  expect(docked.root.style.marginRight ?? '').toBe('');
});

test('a page docking on the right, or leaving, re-places the strip though nothing changed size, in the next frame', () => {
  // The docked page takes the Assistant's width, so the grid keeps its size and no resize fires: only the body's
  // docking attributes say the reach must change. They change inside a press, so the strip measures in the next frame,
  // once however many changed, never inside the press (b041: that read was a full style pass).
  const frames: FrameRequestCallback[] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
  const paint = () => frames.splice(0).forEach((callback) => callback(0));
  const watched: { callback: () => void; target: unknown; filter: string[] }[] = [];
  vi.stubGlobal('MutationObserver', class {
    private readonly callback: () => void;
    constructor(callback: () => void) { this.callback = callback; }
    observe(target: unknown, options: { attributeFilter?: string[] }) { watched.push({ callback: this.callback, target, filter: options.attributeFilter ?? [] }); }
    disconnect() {}
  });
  const body = document.body as unknown as { dataset: Record<string, string> };
  delete body.dataset.controlRightActive;
  const { s, root } = strip({ stage: 1016, studioRight: 1080 });
  s.flush();
  expect(root.style.marginRight).toBe('-360px');
  const docking = watched.filter((w) => w.target === document.body);
  expect(docking.map((w) => w.filter)).toEqual([['data-control-right-active', 'data-study-inspector']]);
  body.dataset.controlRightActive = 'true';
  docking[0].callback(); docking[0].callback();
  expect(root.style.marginRight).toBe('-360px'); // not inside the press
  const measured = vi.spyOn((s as unknown as { studio: TestElement }).studio, 'getBoundingClientRect');
  paint();
  expect(root.style.marginRight ?? '').toBe('');
  expect(measured).toHaveBeenCalledTimes(1); // once for both changes
  body.dataset.controlRightActive = 'false';
  docking[0].callback(); paint();
  expect(root.style.marginRight).toBe('-360px');
});

test('several sources share the strip: each replaces only its own pills', () => {
  const { s, shown } = strip();
  s.set('researchers', [pill('r1', { since: NOW - 10 })]);
  s.set('run', [pill('run:1', { since: NOW - 5 })]);
  s.set('loads', [pill('load:1', { since: NOW - 1 })]);
  s.flush();
  expect(shown()).toEqual(['r1', 'run:1', 'load:1']);
  s.set('run', []);
  s.flush();
  expect(shown()).toEqual(['r1', 'load:1']);
});

test('while the side columns move, the strip waits (no measuring, no folding per frame) and lays out once when they land', async () => {
  const { expectSideMotion, playSideMotion } = await import('./side-motion');
  const { s, root } = strip({ stage: 1016, studioRight: 1080 });
  s.flush();
  expect(root.style.marginRight).toBe('-360px');
  const measured = vi.spyOn((s as unknown as { studio: TestElement }).studio, 'getBoundingClientRect');
  // A press is about to move the sides: what its layout asks for waits, however many times a frame it is asked.
  expectSideMotion();
  for (let i = 0; i < 12; i++) (s as unknown as { layout(): void }).layout();
  expect(measured).not.toHaveBeenCalled();
  // The sides land (no Web Animations here: at once): one layout, one measure.
  playSideMotion([{ node: root as unknown as HTMLElement, props: { marginRight: [-360, 0] }, closing: false }], () => true);
  expect(measured).toHaveBeenCalledTimes(1);
});

test('the voice\'s presence leads the strip, wears its state mark instead of a lit cell, and has no ×', () => {
  const { s, pills, shown } = strip();
  s.set('researchers', [pill('research:r1', { since: NOW - 90_000 })]);
  s.set('voice', [pill('voice:presence', { name: 'Hooke', counting: false, words: 'thinking', voice: 'thinking', lead: true, fixed: true,
    since: NOW, urgency: 1 })]);
  s.flush();
  expect(shown()).toEqual(['voice:presence', 'research:r1']);
  const node = pills.children.find((c) => c.dataset.id === 'voice:presence')!;
  expect(node.classList.contains('strip-pill-voice')).toBe(true);
  expect(node.dataset.voice).toBe('thinking');
  const mark = node.querySelector('.voice-state-mark')!;
  expect(mark.dataset.state).toBe('thinking');
  expect(node.querySelector('.strip-pill-x')!.hidden).toBe(true);
  expect(node.textContent).toContain('Hookethinking');
  s.set('voice', [pill('voice:presence', { name: 'Hooke', counting: false, words: 'working · 2', voice: 'working', lead: true, fixed: true, since: NOW })]);
  s.flush();
  expect(pills.children.find((c) => c.dataset.id === 'voice:presence')!.querySelector('.voice-state-mark')).toBe(mark); // in place
  expect(mark.dataset.state).toBe('working');
  expect(orderPills([pill('a', { urgency: 0 }), pill('v', { lead: true, urgency: 2 })]).map((p) => p.id)).toEqual(['v', 'a']);
});
