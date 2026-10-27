// An accepted analysis, read as a scientist reads it (docs/design/2026-10-23-part1-uiux-pass/README.md §I; Will,
// 2026-10-23: "this component kinda confuses me"): the question as its title, the result first ("r = 0.158"), then
// its interval, n, p and degrees of freedom, the method, a summary table at 3 significant figures with units, and the
// checks in words. Every number is the server's (orchestrator/analysis_math.py); nothing here calculates a statistic.
import { el } from './camera/dom';

export interface ColumnName { label: string; unit?: string }
type Effect = { name: string; value: number; ci95: [number, number] | null; ciMethod: string } | null;
type Summary = { n?: number; mean?: number; median?: number; q1?: number; q3?: number; sd?: number; min?: number; max?: number };
type Group = { label: unknown; n: number; summary: Record<string, unknown>; excluded?: unknown[] };

const METHOD_WORDS: Record<string, string> = {
  welch: 'Welch t test', 'paired-t': 'Paired t test', 'mann-whitney': 'Mann–Whitney U test', 'anova-tukey': 'One-way ANOVA with Tukey comparisons',
  'kruskal-dunn': 'Kruskal–Wallis with Dunn comparisons', pearson: 'Pearson correlation', 'linear-regression': 'Linear regression',
};
const STATISTIC: Record<string, string> = { welch: 't', 'paired-t': 't', 'mann-whitney': 'U', 'anova-tukey': 'F', 'kruskal-dunn': 'H', pearson: 'r', 'linear-regression': 't' };
const EFFECT_SYMBOL: Record<string, string> = { 'Pearson r': 'r', slope: 'Slope', 'eta squared': 'η²', 'eta squared H': 'η² (H)',
  'mean difference (first minus second)': 'Mean difference', 'rank-biserial correlation (first minus second)': 'Rank-biserial r' };

/** 3 significant figures; thousands with separators; never more digits than the reader can use. */
export function sig(value: unknown): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '—';
  if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString('en-US');
  if (value === 0) return '0';
  return Number(value.toPrecision(3)).toString();
}
export function pWords(p: unknown): string {
  if (typeof p !== 'number' || !Number.isFinite(p)) return '';
  return p < 0.001 ? 'p < 0.001' : `p = ${Number(p.toPrecision(2))}`;
}
const named = (key: unknown, names: Record<string, ColumnName>) => {
  const k = String(key ?? ''); return names[k]?.label ?? k.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, c => c.toUpperCase());
};
const withUnit = (key: string, names: Record<string, ColumnName>, units: Record<string, unknown>) => {
  const unit = names[key]?.unit || (typeof units[key] === 'string' ? units[key] as string : '');
  return unit ? `${named(key, names)} (${unit})` : named(key, names);
};

/** The question the analysis answers, from its settings: "Brightness against Area", "Area by Condition". */
export function analysisQuestion(spec: Record<string, unknown>, names: Record<string, ColumnName>): string {
  if (spec.x && spec.y) return `${named(spec.y, names)} against ${named(spec.x, names)}`;
  return spec.group ? `${named(spec.value, names)} by ${named(spec.group, names)}` : named(spec.value, names);
}

/** The headline result: the effect when there is one, else the test statistic. */
export function headline(result: Record<string, unknown>): string {
  const effect = result.effect as Effect, method = String(result.method ?? '');
  if (effect && typeof effect.value === 'number') return `${EFFECT_SYMBOL[effect.name] ?? effect.name} = ${sig(effect.value)}`;
  return typeof result.statistic === 'number' ? `${STATISTIC[method] ?? 'Statistic'} = ${sig(result.statistic)}` : 'No statistic';
}

export function renderAnalysisReadout(result: Record<string, unknown>, spec: Record<string, unknown>, names: Record<string, ColumnName>): HTMLElement {
  const box = el('section', { className: 'analysis-readout' });
  const effect = result.effect as Effect, groups = (Array.isArray(result.groups) ? result.groups : []) as Group[];
  const units = (result.units && typeof result.units === 'object' ? result.units : {}) as Record<string, unknown>;
  const n = groups.reduce((total, group) => total + (typeof group.n === 'number' ? group.n : 0), 0);
  const twoGroups = groups.length === 2 && effect?.name.includes('first minus second');
  box.append(el('p', { className: 'analysis-headline', textContent: headline(result) }));
  if (twoGroups) box.append(el('p', { className: 'analysis-sub', textContent: `${String(groups[0].label)} minus ${String(groups[1].label)}` }));
  const facts = [effect?.ci95 ? `95% CI ${sig(effect.ci95[0])} to ${sig(effect.ci95[1])}` : '', n ? `n = ${n.toLocaleString('en-US')}` : '',
    pWords(result.p) ? `${pWords(result.p)} (two-sided)` : '', typeof result.df === 'number' ? `df = ${sig(result.df)}` : ''].filter(Boolean);
  box.append(el('p', { className: 'analysis-facts', textContent: facts.join(' · ') }));
  const statistic = effect && typeof result.statistic === 'number' && STATISTIC[String(result.method)] && !(result.method === 'pearson')
    ? ` · ${STATISTIC[String(result.method)]} = ${sig(result.statistic)}` : '';
  box.append(el('p', { className: 'analysis-method', textContent: `${METHOD_WORDS[String(result.method)] ?? String(result.method)}${effect?.ciMethod ? ` · interval by ${effect.ciMethod}` : ''}${statistic}` }));

  // The numbers behind it, one row per group and value, each with its unit.
  const table = el('table', { className: 'analysis-summary' });
  table.append(el('thead', {}, el('tr', {}, ...['', 'n', 'Median (IQR)', 'Mean ± SD', 'Range'].map(text => el('th', { scope: 'col', textContent: text })))));
  const body = el('tbody');
  for (const group of groups) for (const [key, value] of Object.entries(group.summary ?? {})) {
    const s = (value && typeof value === 'object' ? value : group.summary) as Summary;
    const label = spec.x && spec.y ? withUnit(key, names, units) : groups.length > 1 ? `${String(group.label)} · ${withUnit(String(spec.value), names, units)}` : withUnit(String(spec.value), names, units);
    body.append(el('tr', {}, el('th', { scope: 'row', textContent: label }), el('td', { textContent: sig(s.n ?? group.n) }),
      el('td', { textContent: `${sig(s.median)} (${sig(s.q1)}–${sig(s.q3)})` }), el('td', { textContent: `${sig(s.mean)} ± ${sig(s.sd)}` }),
      el('td', { textContent: `${sig(s.min)}–${sig(s.max)}` })));
    if (!(value && typeof value === 'object')) break; // a group's summary of one value, not a value-keyed map
  }
  table.append(body); box.append(table);

  // Pairwise comparisons, when the method made them.
  const pairs = Array.isArray(result.pairs) ? result.pairs as { groups: unknown[]; effect: Effect; p: number | null; pAdjusted: number | null; adjustment: string }[] : [];
  if (pairs.length) {
    const list = el('table', { className: 'analysis-pairs' });
    list.append(el('thead', {}, el('tr', {}, ...['Comparison', 'Difference (95% CI)', 'Adjusted p'].map(text => el('th', { scope: 'col', textContent: text })))));
    list.append(el('tbody', {}, ...pairs.map(pair => el('tr', {}, el('th', { scope: 'row', textContent: `${String(pair.groups[0])} vs ${String(pair.groups[1])}` }),
      el('td', { textContent: pair.effect ? `${sig(pair.effect.value)}${pair.effect.ci95 ? ` (${sig(pair.effect.ci95[0])} to ${sig(pair.effect.ci95[1])})` : ''}` : '—' }),
      el('td', { textContent: `${pWords(pair.pAdjusted ?? pair.p) || '—'}${pair.adjustment ? ` · ${pair.adjustment}` : ''}` })))));
    box.append(el('h3', { textContent: 'Pairwise comparisons' }), list);
  }

  // The checks, in words; a flag where one matters for how the result is read.
  const checks = (result.assumptions && typeof result.assumptions === 'object' ? result.assumptions : {}) as Record<string, unknown>;
  const lines: string[] = [];
  for (const test of (Array.isArray(checks.shapiro) ? checks.shapiro : []) as { group: unknown; p: number | null; reason: string | null }[]) {
    const which = test.group === 'x' ? named(spec.x, names) : test.group === 'y' ? named(spec.y, names) : String(test.group);
    lines.push(test.p == null ? `Normality of ${which}: not tested (${test.reason ?? 'unavailable'})`
      : `Normality of ${which} (Shapiro–Wilk): ${pWords(test.p)}${test.p < 0.05 ? ', so it may not be normally distributed' : ''}`);
  }
  const levene = checks.levene as { p: number | null; reason?: string } | undefined;
  if (levene?.p != null) lines.push(`Equal spread across groups (Levene): ${pWords(levene.p)}${levene.p < 0.05 ? ', so the spreads may differ' : ''}`);
  for (const note of (Array.isArray(result.notes) ? result.notes : []) as string[]) lines.push(note);
  if (lines.length) box.append(el('h3', { textContent: 'Checks and notes' }), el('ul', { className: 'analysis-checks' }, ...lines.map(line => el('li', { textContent: line }))));
  return box;
}
