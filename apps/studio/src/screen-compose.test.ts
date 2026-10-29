// The composed window (screen.ts screenLayout/composeWindow): what a model needs to read it. Each window and panel with
// its title, its pictures inside it in paint order, the Tab names and the table rows in view as text; nothing hidden,
// nothing of the voice's own marks, and the text stops at its time budget.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { frameTitle, SCREEN_FRAMES, screenLayout } from './screen';

interface Fake { tag: string; rect: [number, number, number, number]; z?: number; bg?: string; hidden?: boolean; text?: string;
  title?: Fake; screenTitle?: string; label?: string; classes?: string[]; holder?: Fake; rows?: Fake[]; cells?: Fake[]; marks?: boolean;
  selected?: boolean }
const styles = new Map<object, { backgroundColor: string; zIndex: string; visibility: string; display: string; opacity: string }>();
function el(f: Fake): Element {
  const node = {
    tagName: f.tag.toUpperCase(),
    textContent: f.text ?? '',
    dataset: f.screenTitle ? { screenTitle: f.screenTitle } : {},
    classList: { contains: (c: string) => (f.classes ?? []).includes(c) },
    getAttribute: (name: string) => (name === 'aria-label' ? f.label ?? null : null),
    getBoundingClientRect: () => { const [x, y, w, h] = f.rect; return { left: x, top: y, width: w, height: h, right: x + w, bottom: y + h }; },
    closest: (selector: string) => {
      if (selector.includes('[hidden]')) return f.hidden || f.holder?.hidden ? {} : null;
      if (selector === '#voice-marks') return f.marks ? {} : null;
      if (selector === SCREEN_FRAMES) return f.holder ? el(f.holder) : null;
      if (selector === 'table') return null;
      if (selector === '[aria-selected="true"]') return f.selected ? {} : null;
      return null;
    },
    querySelector: () => (f.title ? el(f.title) : null),
    rows: f.rows?.map(el),
    cells: f.cells?.map(el),
  };
  const cached = cache.get(f);
  if (cached) return cached;
  cache.set(f, node as unknown as Element);
  styles.set(node, { backgroundColor: f.bg ?? 'rgba(0, 0, 0, 0)', zIndex: String(f.z ?? 'auto'), visibility: 'visible', display: 'block', opacity: '1' });
  return node as unknown as Element;
}
const cache = new Map<Fake, Element>();
const root = (by: Record<string, Fake[]>) => ({ querySelectorAll: (selector: string) => (by[selector] ?? []).map(el) }) as unknown as ParentNode;

beforeEach(() => vi.stubGlobal('getComputedStyle', (node: object) => styles.get(node)));
afterEach(() => { vi.unstubAllGlobals(); cache.clear(); styles.clear(); });

test('windows and panels in paint order with their titles; pictures inside them; hidden ones and the marks layer left out', () => {
  const table: Fake = { tag: 'section', rect: [600, 100, 500, 400], z: 30, bg: 'rgb(20, 20, 24)', title: { tag: 'span', rect: [600, 100, 500, 20], text: 'Measurements · 18 cells' } };
  const magnifier: Fake = { tag: 'section', rect: [1100, 80, 300, 300], z: 10, bg: 'rgb(30, 30, 30)', screenTitle: 'Magnifier', title: { tag: 'h2', rect: [1100, 80, 300, 20], text: 'Cell 3' } };
  const closed: Fake = { tag: 'section', rect: [0, 0, 300, 300], screenTitle: 'Slide map', hidden: true };
  const live: Fake = { tag: 'canvas', rect: [0, 0, 1440, 900] };
  const zoom: Fake = { tag: 'canvas', rect: [1110, 110, 280, 260], holder: magnifier };
  const marks: Fake = { tag: 'canvas', rect: [0, 0, 1440, 900], marks: true };
  const layout = screenLayout(root({ [SCREEN_FRAMES]: [table, magnifier, closed], 'canvas, img, video': [live, zoom, marks] }), [1440, 900]);
  expect(layout.frames.map(f => f.title)).toEqual(['Magnifier: Cell 3', 'Measurements · 18 cells']); // z 10 under z 30
  expect(layout.pictures.map(p => [p.rect.left, p.frame])).toEqual([[0, null], [1110, 0]]);
  expect(layout.text.filter(t => t.kind === 'title').map(t => t.text)).toEqual(['Magnifier: Cell 3', 'Measurements · 18 cells']);
});

test('the table rows in view are written cell by cell; rows scrolled away are not', () => {
  const row = (y: number, values: string[], header = false): Fake => ({ tag: 'tr', rect: [600, y, 500, 24],
    cells: values.map((v, i) => ({ tag: header ? 'th' : 'td', rect: [600 + i * 100, y, 100, 24], text: v })) });
  const grid: Fake = { tag: 'table', rect: [600, 130, 500, 900], rows: [row(130, ['Cell', 'Area µm²'], true), row(154, ['1', '182.4']), row(178, ['2', '96.0']), row(1000, ['40', '12.1'])] };
  const layout = screenLayout(root({ table: [grid] }), [1440, 900]);
  expect(layout.text.map(t => [t.kind, t.text])).toEqual([
    ['header', 'Cell'], ['header', 'Area µm²'], ['cell', '1'], ['cell', '182.4'], ['cell', '2'], ['cell', '96.0']]);
});

test('Tab names are written where they are; text stops at its time budget', () => {
  const tabs = ['Live', 'Measurements', 'Notes'].map((t, i): Fake => ({ tag: 'span', rect: [i * 100, 40, 90, 24], text: t, selected: i === 1 }));
  expect(screenLayout(root({ '.workspace-tab-label': tabs }), [1440, 900]).text.map(t => [t.text, t.selected])).toEqual(
    [['Live', false], ['Measurements', true], ['Notes', false]]); // the Tab in front is drawn as selected
  let clock = 0;
  const slow = screenLayout(root({ '.workspace-tab-label': tabs }), [1440, 900], 70, () => (clock += 40));
  expect(slow.text.length).toBeLessThan(3);
});

test('a frame is titled by its own title bar, else its name', () => {
  const titled = el({ tag: 'section', rect: [0, 0, 10, 10], screenTitle: 'Result viewer', title: { tag: 'h2', rect: [0, 0, 1, 1], text: '  Researcher 3 —\n findings ' } });
  expect(frameTitle(titled)).toBe('Result viewer: Researcher 3 — findings');
  expect(frameTitle(el({ tag: 'section', rect: [0, 0, 10, 10], label: 'Open project artifacts' }))).toBe('Open project artifacts');
});
