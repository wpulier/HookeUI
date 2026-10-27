import { afterEach, expect, test, vi } from 'vitest';
import { analysisQuestion, headline, pWords, sig } from './analysis-readout';
import { syntheticDOM } from './workspace-windows.test-support';

afterEach(() => vi.unstubAllGlobals());
const names = { areaPx: { label: 'Area', unit: 'px²' }, intensity: { label: 'Brightness' }, condition: { label: 'Condition' } };
const pearson = { method: 'pearson', statistic: 0.15753975094524517, df: 521, p: 0.00029827221967297835,
  effect: { name: 'Pearson r', value: 0.15753975094524517, ci95: [0.072783784128174, 0.24003658181690046], ciMethod: 'Fisher transformation' },
  units: { areaPx: 'px²', intensity: '' }, notes: ['Two-sided tests; 95% confidence intervals.'], pairs: [],
  assumptions: { shapiro: [{ group: 'x', statistic: 0.2, p: 1e-20, reason: null }, { group: 'y', statistic: 0.99, p: 0.2, reason: null }], levene: { statistic: null, p: null, reason: 'not an independent-group variance comparison' } },
  groups: [{ label: null, n: 523, summary: { areaPx: { n: 523, mean: 734.6003824091778, median: 54, q1: 30, q3: 129.5, sd: 12305.939070118588, min: 20, max: 281248 },
    intensity: { n: 523, mean: 82.30502868068834, median: 82.01, q1: 76.385, q3: 87.245, sd: 7.745, min: 65.88, max: 107.77 } } }] };

test('numbers are said to 3 significant figures and p in words, never 0.00029827221967297835', () => {
  expect(sig(0.15753975094524517)).toBe('0.158'); expect(sig(12305.939)).toBe('12,306'); expect(sig(54)).toBe('54'); expect(sig(null)).toBe('—');
  expect(pWords(0.00029827)).toBe('p < 0.001'); expect(pWords(0.0234)).toBe('p = 0.023');
});

test('the title is the question, in the table\'s own words', () => {
  expect(analysisQuestion({ method: 'pearson', x: 'areaPx', y: 'intensity' }, names)).toBe('Brightness against Area');
  expect(analysisQuestion({ method: 'welch', value: 'areaPx', group: 'condition' }, names)).toBe('Area by Condition');
  expect(analysisQuestion({ method: 'welch', value: 'cellArea' }, {})).toBe('Cell Area'); // without names, the key, readable
});

test('the result leads: r = 0.158, then its interval, n, p and df, the method, a summary and the checks in words', async () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const { renderAnalysisReadout } = await import('./analysis-readout');
  expect(headline(pearson)).toBe('r = 0.158');
  const box = renderAnalysisReadout(pearson, { method: 'pearson', x: 'areaPx', y: 'intensity' }, names) as unknown as { textContent: string; querySelector(s: string): { textContent: string } | null };
  expect(box.querySelector('.analysis-headline')!.textContent).toBe('r = 0.158');
  expect(box.querySelector('.analysis-facts')!.textContent).toBe('95% CI 0.0728 to 0.24 · n = 523 · p < 0.001 (two-sided) · df = 521');
  expect(box.querySelector('.analysis-method')!.textContent).toBe('Pearson correlation · interval by Fisher transformation');
  expect(box.textContent).toContain('Area (px²)'); expect(box.textContent).toContain('54 (30–130)'); expect(box.textContent).toContain('735 ± 12,306');
  expect(box.textContent).toContain('Normality of Area (Shapiro–Wilk): p < 0.001, so it may not be normally distributed');
  expect(box.textContent).not.toContain('{'); // no raw JSON in the readout
});
