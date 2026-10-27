/** napari's image histogram (HistogramModel) and the contrast slider range; display only. */
export const MAX_HISTOGRAM_BINS = 512;
export type HistogramSettings = { enabled: boolean; bins: number; mode: 'canvas' | 'full'; logScale: boolean; maxSamples: number };
export type ImageHistogram = HistogramSettings & { range: [number, number]; binEdges: number[] | null; counts: number[] | null; integer: boolean };
export type ContrastAction = { action: 'auto-once' } | { action: 'reset-range' } | { action: 'autoscale-range' } | { action: 'reset-histogram' } | { action: 'set-range'; range: [number, number] };
const finite = (n: unknown): n is number => typeof n === 'number' && Number.isFinite(n);
const message = `Choose 1 to ${MAX_HISTOGRAM_BINS} histogram bins, the displayed slice or the whole array, a linear or logarithmic scale and a positive sample limit.`;

export function histogramSettings(raw: unknown): HistogramSettings {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) throw new Error(message);
  const v = raw as Record<string, unknown>;
  if (Object.keys(v).sort().join(' ') !== 'bins enabled logScale maxSamples mode' || typeof v.enabled !== 'boolean' ||
      typeof v.bins !== 'number' || !Number.isInteger(v.bins) || v.bins < 1 || v.bins > MAX_HISTOGRAM_BINS ||
      (v.mode !== 'canvas' && v.mode !== 'full') || typeof v.logScale !== 'boolean' ||
      typeof v.maxSamples !== 'number' || !Number.isInteger(v.maxSamples) || v.maxSamples < 1 || v.maxSamples > 2 ** 31) throw new Error(message);
  return structuredClone(v) as HistogramSettings;
}

export function imageHistogram(raw: unknown): ImageHistogram {
  const fail = (): never => { throw new Error('The image histogram returned unsupported metadata. Refresh the preview.'); };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return fail();
  const v = raw as Record<string, unknown>;
  if (Object.keys(v).sort().join(' ') !== 'binEdges bins counts enabled integer logScale maxSamples mode range' || typeof v.integer !== 'boolean') return fail();
  histogramSettings({ enabled: v.enabled, bins: v.bins, mode: v.mode, logScale: v.logScale, maxSamples: v.maxSamples });
  if (!Array.isArray(v.range) || v.range.length !== 2 || !v.range.every(finite) || v.range[0] > v.range[1]) return fail();
  const edges = v.binEdges, counts = v.counts;
  if (!v.enabled) { if (edges !== null || counts !== null) return fail(); }
  else if (!Array.isArray(edges) || !Array.isArray(counts) || edges.length !== counts.length + 1 || counts.length < 1 || counts.length > MAX_HISTOGRAM_BINS ||
      !edges.every(finite) || edges.some((n, i) => i > 0 && n < edges[i - 1]) || !counts.every(n => finite(n) && n >= 0)) return fail();
  return structuredClone(v) as ImageHistogram;
}

export function contrastAction(raw: unknown): ContrastAction {
  const v = raw as Record<string, unknown>;
  if (!v || typeof v !== 'object') throw new Error('Choose a contrast action.');
  if (v.action === 'set-range') {
    if (Object.keys(v).sort().join(' ') !== 'action range' || !Array.isArray(v.range) || v.range.length !== 2 || !v.range.every(finite) || v.range[0] >= v.range[1])
      throw new Error('Enter an increasing finite contrast range.');
  } else if (!['auto-once', 'reset-range', 'autoscale-range', 'reset-histogram'].includes(String(v.action)) || Object.keys(v).length !== 1) throw new Error('Choose a contrast action.');
  return structuredClone(v) as ContrastAction;
}

/** A bar chart of napari's counts; bar heights are relative to the tallest bin. */
export function histogramChart(histogram: ImageHistogram, label: string): HTMLElement {
  const chart = document.createElement('div');
  chart.setAttribute('role', 'img'); chart.setAttribute('aria-label', label); chart.className = 'scientific-histogram';
  const counts = histogram.counts ?? [], tallest = Math.max(0, ...counts);
  for (const count of counts) {
    const bar = document.createElement('span');
    bar.style.height = `${tallest > 0 ? 100 * count / tallest : 0}%`; bar.style.width = `${100 / counts.length}%`;
    chart.append(bar);
  }
  return chart;
}
