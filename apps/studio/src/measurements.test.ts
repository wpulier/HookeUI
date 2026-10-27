// The Measurements page (measurements.ts): its route, how values are shown, sorting (empty values last), headers with
// units, the median line and the mean ± SD line.
import { describe, expect, test } from 'vitest';
import { formatValue, header, meanWords, measurementsHash, measurementsTarget, sortRows, statWords, type TableColumn } from './measurements';

const area: TableColumn = { key: 'areaPx', label: 'Area', unit: 'µm²', type: 'number' };
const flag: TableColumn = { key: 'edgeCut', label: 'Cut by the edge', unit: '', type: 'bool' };
const name: TableColumn = { key: 'cell', label: 'Cell', unit: '', type: 'text' };

describe('the route', () => {
  test('#measurements is the newest item; #measurements/e3 that one; anything else is not this page', () => {
    expect(measurementsTarget('#measurements')).toBeNull();
    expect(measurementsTarget('#measurements/e3')).toBe('e3');
    expect(measurementsTarget('#assets')).toBeUndefined();
    expect(measurementsHash('m1')).toBe('#measurements/m1');
    expect(measurementsHash(null)).toBe('#measurements');
  });
});

describe('values', () => {
  test('numbers to 3 significant figures, thousands grouped, flags as yes, nothing as —', () => {
    expect(formatValue(12.3456, area)).toBe('12.3');
    expect(formatValue(4988.2, area)).toBe('4,988');
    expect(formatValue(0.000123456, area)).toBe('0.000123');
    expect(formatValue(7, area)).toBe('7');
    expect(formatValue(null, area)).toBe('—');
    expect(formatValue(true, flag)).toBe('yes');
    expect(formatValue(false, flag)).toBe('');
    expect(formatValue('c12', name)).toBe('c12');
  });

  test('a header says its unit; the median line says the middle half', () => {
    expect(header(area)).toBe('Area (µm²)');
    expect(header(name)).toBe('Cell');
    expect(statWords({ key: 'areaPx', n: 4, median: 41.2, q1: 30.04, q3: 52.9, min: 1, max: 90 }, area))
      .toBe('41.2 µm² (middle half 30–52.9)');
  });

  test('the mean line says the mean ± SD in its unit, as the server sent the numbers', () => {
    const stat = { key: 'areaPx', n: 4, median: 41.2, q1: 30.04, q3: 52.9, min: 1, max: 90 };
    expect(meanWords({ ...stat, mean: 43.217, sd: 12.08, sem: 6.04, ci95: [24, 62.4] }, area)).toBe('mean 43.2 ± 12.1 µm² (SD)');
    expect(meanWords({ ...stat, mean: 0.8525, sd: 0.0602 }, { key: 'circularity', label: 'Roundness', unit: '', type: 'number' }))
      .toBe('mean 0.853 ± 0.0602 (SD)');
    expect(meanWords({ ...stat, n: 1, mean: 41.2, sd: null, sem: null, ci95: null }, area)).toBe('mean 41.2 µm²');
    expect(meanWords(stat, area)).toBe(''); // an older server sent no mean: no line, never one made up here
    expect(meanWords({ ...stat, mean: null }, area)).toBe('');
  });
});

describe('sorting', () => {
  const rows = [{ cell: 'c2', areaPx: 30 }, { cell: 'c10', areaPx: null }, { cell: 'c1', areaPx: 90 }, { cell: 'c3', areaPx: 41 }];
  test('numbers by value either way, empty values last', () => {
    expect(sortRows(rows, 'areaPx', 'desc').map((r) => r.cell)).toEqual(['c1', 'c3', 'c2', 'c10']);
    expect(sortRows(rows, 'areaPx', 'asc').map((r) => r.cell)).toEqual(['c2', 'c3', 'c1', 'c10']);
  });
  test('ids in their natural order (c2 before c10)', () => {
    expect(sortRows(rows, 'cell', 'asc').map((r) => r.cell)).toEqual(['c1', 'c2', 'c3', 'c10']);
  });
});

test('in a window the table is the scroll area, its header row and row names held, under a compact heading', async () => {
  const { readFileSync } = await import('node:fs');
  const css = readFileSync(new URL('./workspace-study-v2.css', import.meta.url), 'utf8');
  // One scroll area that fills the window under its heading: no viewport-sized box whose scrollbar sits out of sight.
  expect(css).toContain('#study-artifacts .measurements-page>.library-page{display:flex;flex-direction:column;height:100%}');
  expect(css).toContain('#study-artifacts .measurements-page .meas-scroll{flex:1 1 auto;min-height:96px;max-height:none}');
  expect(css).toMatch(/#study-artifacts \.meas-table td:first-child,#study-artifacts \.meas-table th:first-child\{position:sticky;left:0/);
  // Window rules are id-scoped so they win over the page's body.studio-shell sizes.
  expect(css).toContain('#study-artifacts .measurements-page .meas-note{');
  const content = readFileSync(new URL('./workspace-study-content.ts', import.meta.url), 'utf8');
  expect(content).toContain("creation.className='study-table-actions'");
});
