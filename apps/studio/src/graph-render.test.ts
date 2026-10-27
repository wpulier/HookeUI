import { expect, test } from 'vitest';
import { graphSVG, type GraphData } from './graph-render';

const saved = (): GraphData => ({ schema: 1, kind: 'box',
  axes: { x: { label: 'Condition', unit: '' }, y: { label: 'Area', unit: 'µm²' } },
  groups: [{ label: 'Control', n: 3, points: [{ row: 'r1', y: 1 }, { row: 'r2', y: 2 }, { row: 'r3', y: 10 }],
    summary: { mean: 4.333, median: 2, q1: 1.5, q3: 6 }, whiskers: [1, 10] }],
  definitions: 'Retained linear quartiles', excluded: [{ row: 'r4', reason: 'focus' }], error: null });

test('portable graph retains values, units and exclusions; hostile titles remain text', () => {
  const data = saved(); data.groups[0].label = '<script>alert(1)</script>';
  const svg = graphSVG(data, '<img onerror="bad">');
  expect(svg).not.toContain('<script>'); expect(svg).not.toContain('<img');
  expect(svg).toContain('&lt;img onerror=&quot;bad&quot;&gt;');
  expect(svg).toContain('Area (µm²)'); expect(svg).toContain('1 excluded rows');
  expect(svg.match(/<circle /g)).toHaveLength(3);
  expect(svg).toContain('r3: 10'); expect(svg).toContain('n=3');
  expect(graphSVG(data, 'retained')).toBe(graphSVG(data, 'retained'));
});

test('refuses mismatched observations and nonfinite values before claiming a graph', () => {
  const data = saved(); data.groups[0].n = 4;
  expect(() => graphSVG(data, 'bad')).toThrow('retained count');
  data.groups[0].n = 3; data.groups[0].points[1].y = Infinity;
  expect(() => graphSVG(data, 'bad')).toThrow('finite');
});

test('uncertainty uses accepted endpoints and a single observation stays visible', () => {
  const data = saved(); data.kind = 'bar'; data.error = 'ci95'; data.groups[0].interval = [-20, 25];
  const svg = graphSVG(data, 'Interval');
  expect(svg).toContain('Error: ci95'); expect(svg).not.toContain('NaN');
  expect(svg).not.toContain('Infinity'); expect(svg.match(/<circle /g)).toHaveLength(3);
});
