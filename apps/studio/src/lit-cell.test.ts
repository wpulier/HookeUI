// The lit cell over a synthetic DOM: its shape, its state and accessible name, the setup fill only from a measured
// fraction, in-place updates, and the settle that plays once. Its look (breathing, fills, reduced motion) is CSS and is
// checked by reading workspace-feel.css.
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { LIT_CELL_WORDS, litCell, setLitCell, type LitCellState } from './lit-cell';
import { fire, syntheticDOM, TestElement } from './workspace-windows.test-support';

const SVG = 'http://www.w3.org/2000/svg';
let namespaces: string[];
beforeEach(() => {
  const dom = syntheticDOM();
  namespaces = [];
  vi.stubGlobal('document', Object.assign(dom.document, {
    createElementNS: (ns: string, tag: string) => { namespaces.push(ns); return new TestElement(tag); },
  }));
});
afterEach(() => vi.unstubAllGlobals());

const node = (cell: unknown) => cell as unknown as TestElement;
const part = (cell: unknown, name: string) => node(cell).children.find((child) => child.classList.contains(name)) ?? null;
const STATES = Object.keys(LIT_CELL_WORDS) as LitCellState[];

test('one SVG hexagon after the brand cell, in the SVG namespace, marked as a lit cell', () => {
  const cell = node(litCell('working'));
  expect(cell.tagName).toBe('SVG');
  expect(namespaces.every((ns) => ns === SVG)).toBe(true);
  expect(cell.getAttribute('viewBox')).toBe('0 0 12 14');
  expect(cell.getAttribute('data-kind')).toBe('lit-cell');
  expect(cell.dataset.kind).toBe('lit-cell');
  expect(cell.classList.contains('lit-cell')).toBe(true);
  expect(part(cell, 'lit-cell-base')!.getAttribute('points')).toBe('6,1 11,4 11,10 6,13 1,10 1,4');
  expect(part(cell, 'lit-cell-centre')!.tagName).toBe('CIRCLE');
  expect([cell.getAttribute('width'), cell.getAttribute('height')]).toEqual(['12', '14']);
});

test('every state carries its word as the accessible name, and the size keeps the cell\'s shape', () => {
  expect(STATES).toEqual(['working', 'still', 'queued', 'setup', 'needs', 'ready', 'partial', 'idle', 'attention', 'stopped']);
  for (const state of STATES) {
    const cell = node(litCell(state));
    expect(cell.dataset.state).toBe(state);
    expect(cell.getAttribute('role')).toBe('img');
    expect(cell.getAttribute('aria-label')).toBe(LIT_CELL_WORDS[state]);
  }
  expect(LIT_CELL_WORDS.needs).toBe('Needs you');
  expect(LIT_CELL_WORDS.idle).toBe('Not set up');
  const big = node(litCell('ready', { size: 14 }));
  expect([big.getAttribute('width'), big.getAttribute('height')]).toEqual(['14', '16']);
  const small = node(litCell('ready', { size: 10 }));
  expect([small.getAttribute('width'), small.getAttribute('height')]).toEqual(['10', '12']);
});

test('a caller\'s name and tooltip; a decorative cell is hidden from assistive technology', () => {
  const named = node(litCell('still', { label: 'Researcher 3 stopping', title: 'Asked to stop; it is Stopped once the server lists it so.' }));
  expect(named.getAttribute('aria-label')).toBe('Researcher 3 stopping');
  expect(named.children[0].tagName).toBe('TITLE');
  expect(named.children[0].textContent).toBe('Asked to stop; it is Stopped once the server lists it so.');
  const quiet = node(litCell('working', { decorative: true }));
  expect(quiet.getAttribute('aria-hidden')).toBe('true');
  expect(quiet.getAttribute('role')).toBeNull();
  expect(quiet.getAttribute('aria-label')).toBeNull();
});

test('setup fills bottom-up only from a measured fraction; no fraction is the empty outline', () => {
  const empty = node(litCell('setup'));
  expect(part(empty, 'lit-cell-fill')).toBeNull();
  expect(part(empty, 'lit-cell-clip')).toBeNull();
  for (const notMeasured of [null, Number.NaN, Number.POSITIVE_INFINITY]) {
    expect(part(litCell('setup', { fraction: notMeasured }), 'lit-cell-fill')).toBeNull();
  }
  const half = node(litCell('setup', { fraction: 0.5 }));
  const clip = part(half, 'lit-cell-clip')!, fill = part(half, 'lit-cell-fill')!;
  expect(clip.tagName).toBe('CLIPPATH');
  expect(fill.getAttribute('clip-path')).toBe(`url(#${clip.id})`);
  expect(clip.children[0].getAttribute('y')).toBe('7.5'); // halfway between the cell's top (1) and bottom (13)
  expect(part(litCell('setup', { fraction: 1 }), 'lit-cell-clip')!.children[0].getAttribute('y')).toBe('1');
  expect(part(litCell('setup', { fraction: 0 }), 'lit-cell-clip')!.children[0].getAttribute('y')).toBe('14');
  expect(part(litCell('setup', { fraction: 4 }), 'lit-cell-clip')!.children[0].getAttribute('y')).toBe('1'); // clamped
  expect(part(litCell('working', { fraction: 0.5 }), 'lit-cell-fill')).toBeNull(); // a fraction means nothing outside setup
  const other = part(litCell('setup', { fraction: 0.2 }), 'lit-cell-clip')!;
  expect(other.id).not.toBe(clip.id);
});

test('updates in place: the same element, the fill moved and dropped, the name kept current', () => {
  const svg = litCell('setup', { fraction: 0.25 }), cell = node(svg);
  const fill = part(cell, 'lit-cell-fill');
  setLitCell(svg, 'setup', { fraction: 0.75 });
  expect(part(cell, 'lit-cell-fill')).toBe(fill);
  expect(part(cell, 'lit-cell-clip')!.children[0].getAttribute('y')).toBe('4.25');
  setLitCell(svg, 'ready', { title: 'Ready on this Mac' });
  expect(cell.dataset.state).toBe('ready');
  expect(cell.getAttribute('aria-label')).toBe('Ready');
  expect(part(cell, 'lit-cell-fill')).toBeNull();
  expect(cell.children.filter((child) => child.tagName === 'TITLE')).toHaveLength(1);
  setLitCell(svg, 'ready');
  expect(cell.children.filter((child) => child.tagName === 'TITLE')).toHaveLength(0);
  setLitCell(svg, 'ready', { size: 14 });
  setLitCell(svg, 'working');
  expect(cell.getAttribute('width')).toBe('14'); // a size, once given, stays
});

const SETTLED = { animationName: 'mm-lit-settle' };

test('the settle plays once: on request, and gone when its animation ends', () => {
  const plain = node(litCell('ready'));
  expect(plain.classList.contains('lit-cell-settle')).toBe(false);
  const svg = litCell('ready', { settle: true }), cell = node(svg);
  expect(cell.classList.contains('lit-cell-settle')).toBe(true);
  fire(cell, 'animationend', SETTLED);
  expect(cell.classList.contains('lit-cell-settle')).toBe(false);
  setLitCell(svg, 'ready');
  expect(cell.classList.contains('lit-cell-settle')).toBe(false); // a redraw never replays it
  setLitCell(svg, 'ready', { settle: true });
  fire(cell, 'animationcancel', SETTLED);
  expect(cell.classList.contains('lit-cell-settle')).toBe(false);
});

test('a working cell turning ready in place keeps its settle while the breathing it drops is cancelled', () => {
  const svg = litCell('working'), cell = node(svg);
  const base = part(cell, 'lit-cell-base')!;
  setLitCell(svg, 'ready', { settle: true });
  expect(cell.classList.contains('lit-cell-settle')).toBe(true);
  // The polygon's breathing is cancelled and its event bubbles to the svg: not the settle.
  fire(cell, 'animationcancel', { target: base, animationName: 'mm-lit-breathe' });
  expect(cell.classList.contains('lit-cell-settle')).toBe(true);
  fire(cell, 'animationend', { target: base, animationName: 'mm-lit-settle' }); // a child's, whatever its name
  expect(cell.classList.contains('lit-cell-settle')).toBe(true);
  setLitCell(svg, 'ready', { settle: true }); // asked again while playing: left to finish, not restarted
  expect(cell.classList.contains('lit-cell-settle')).toBe(true);
  fire(cell, 'animationend', SETTLED); // the svg's own settle ends it, once
  expect(cell.classList.contains('lit-cell-settle')).toBe(false);
  setLitCell(svg, 'ready', { settle: true });
  expect(cell.classList.contains('lit-cell-settle')).toBe(true); // and a later one plays again
});

test('its look is CSS: breathing while working, still under reduced motion, a 240 ms settle, every state styled', () => {
  const css = readFileSync(new URL('./workspace-feel.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
  expect(css).toMatch(/\.lit-cell\[data-state='working'\][^{]*\{[^}]*animation:\s*mm-lit-breathe 1\.6s/);
  expect(css).toMatch(/\.lit-cell-settle\s*\{[^}]*animation:\s*mm-lit-settle 240ms/);
  const reduced = /@media \(prefers-reduced-motion: reduce\)\s*\{([\s\S]*?)\n\}/.exec(css)?.[1] ?? '';
  expect(reduced).toMatch(/\.lit-cell\[data-state='working'\][^{]*\{[^}]*animation:\s*none/);
  expect(reduced).toMatch(/\.lit-cell-settle\s*\{[^}]*animation:\s*none/);
  for (const state of STATES) expect(css, state).toContain(`.lit-cell[data-state='${state}']`);
  expect(css).toMatch(/\.mm-num\s*\{[^}]*font-variant-numeric:\s*tabular-nums/);
  expect(css).not.toMatch(/:\s*[^;{}]*#[0-9a-f]{3,8}\b/i); // colours come from --mm-* tokens only
});
