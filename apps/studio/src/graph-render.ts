/** D11: render retained server-computed graph values. No bins, statistics or fits are computed here. */
export interface GraphPoint { row: string; x?: number; y: number }
export interface GraphGroup {
  label: string | number | boolean | null; n: number; points: GraphPoint[];
  summary: { mean: number | null; median: number | null; q1: number | null; q3: number | null } | null;
  bins?: { low: number; high: number; count: number }[];
  whiskers?: [number, number]; interval?: [number, number] | null;
  density?: { value: number; density: number }[];
  densityUnavailable?: string; intervalUnavailable?: string;
}
export interface GraphData {
  schema: 1; kind: 'histogram' | 'box' | 'violin' | 'bar' | 'scatter';
  axes: { x: { label: string; unit: string }; y: { label: string; unit: string } };
  groups: GraphGroup[]; definitions: string; error: string | null;
  excluded: { row: string; reason: string }[];
  annotations?: { groups: (string | number | boolean | null)[]; p: number; method: string; adjustment?: string }[];
}
const COLORS = ['#315fa8', '#b15c32', '#3e8267', '#955994', '#827025', '#4c7f91'];
const xml = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' }[c]!));
const number = (value: number) => {
  if (!Number.isFinite(value)) throw new Error('This graph contains an unavailable numeric value.');
  return Number(value.toFixed(4));
};
const axisName = (axis: { label: string; unit: string }) => `${axis.label}${axis.unit ? ` (${axis.unit})` : ''}`;

/** Portable SVG draws exactly the accepted graph's values, with readable units and definitions. */
/** A round step for about `count` intervals: 1, 2 or 5 × 10ⁿ, so axes read 0, 10, 20 rather than -4.13, 9.322. */
export function niceStep(span: number, count = 5): number {
  const raw = (span > 0 ? span : 1) / count, power = 10 ** Math.floor(Math.log10(raw)), unit = raw / power;
  return (unit <= 1 ? 1 : unit <= 2 ? 2 : unit <= 5 ? 5 : 10) * power;
}
/** Ticks on round values within [lo, hi]. Positioning only: no value is binned or changed. */
export function niceTicks(lo: number, hi: number, count = 5): number[] {
  const step = niceStep(hi - lo, count), ticks: number[] = [];
  for (let v = Math.ceil(lo / step - 1e-9) * step; v <= hi + step * 1e-9; v += step) ticks.push(Number(v.toPrecision(12)));
  return ticks;
}
/** A stable id from the graph's own content: the same graph exports the same bytes; different graphs on one page differ. */
function contentId(text: string): string { let h = 2166136261; for (let i = 0; i < text.length; i++) h = Math.imul(h ^ text.charCodeAt(i), 16777619); return (h >>> 0).toString(36); }
export function graphSVG(data: GraphData, title: string): string {
  if (data.schema !== 1 || !['histogram', 'box', 'violin', 'bar', 'scatter'].includes(data.kind)
      || !data.groups.length || data.groups.length > 12 || data.groups.reduce((n, g) => n + g.points.length, 0) > 20000) {
    throw new Error('This saved graph format is not supported. Its table remains available.');
  }
  const annotations = data.annotations ?? [];
  const w = 960, h = 580 + annotations.length * 18, left = 86, right = 28, top = 64, bottom = 122 + annotations.length * 18;
  const pw = w - left - right, ph = h - top - bottom;
  const numericX = data.kind === 'histogram' || data.kind === 'scatter';
  const xs: number[] = [], ys: number[] = [];
  for (const g of data.groups) {
    if (g.n !== g.points.length) throw new Error('Graph observations do not match its retained count.');
    if (data.kind === 'histogram') for (const b of g.bins ?? []) { xs.push(b.low, b.high); ys.push(b.count); }
    else for (const p of g.points) { if (data.kind === 'scatter') xs.push(p.x!); ys.push(p.y); }
    if (g.interval) ys.push(...g.interval);
  }
  if (data.kind === 'histogram' || data.kind === 'bar') ys.push(0);
  if (!ys.length || [...xs, ...ys].some(v => !Number.isFinite(v))) throw new Error('No finite graph values are available.');
  // Each axis ends on round values; counts and bars start exactly at zero (never a negative count).
  const extent = (values: number[], fromZero = false): [number, number] => {
    const lo = Math.min(...values), hi = Math.max(...values);
    const pad = (hi - lo || Math.abs(lo) || 1) * 0.07, step = niceStep(hi - lo + 2 * pad);
    const low = fromZero && lo >= 0 ? 0 : Math.floor((lo - pad) / step) * step, high = Math.ceil((hi + pad) / step) * step;
    return [low, high > low ? high : low + step];
  };
  const [xmin, xmax] = numericX ? extent(xs) : [-0.5, data.groups.length - 0.5];
  const [ymin, ymax] = extent(ys, data.kind === 'histogram' || data.kind === 'bar');
  const id = `graph-${contentId(title + JSON.stringify(data))}`;
  const X = (x: number) => number(left + (x - xmin) / (xmax - xmin) * pw);
  const Y = (y: number) => number(top + ph - (y - ymin) / (ymax - ymin) * ph);
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-labelledby="${id}-title ${id}-description">`,
    `<title id="${id}-title">${xml(title)}</title><desc id="${id}-description">${xml(data.definitions)}</desc>`,
    '<rect width="100%" height="100%" fill="white"/>',
    '<g font-family="system-ui, sans-serif" font-size="12" fill="#24272d">',
    `<text x="${left}" y="30" font-size="20" font-weight="600">${xml(title)}</text>`];
  const line = (x1: number, y1: number, x2: number, y2: number, color = '#505863', width = 1) =>
    parts.push(`<line x1="${number(x1)}" y1="${number(y1)}" x2="${number(x2)}" y2="${number(y2)}" stroke="${color}" stroke-width="${width}"/>`);
  const rect = (x: number, y: number, width: number, height: number, color: string, opacity = 1) =>
    parts.push(`<rect x="${number(x)}" y="${number(y)}" width="${number(Math.max(0, width))}" height="${number(Math.max(0, height))}" fill="${color}" fill-opacity="${opacity}" stroke="${color}"/>`);
  for (const v of niceTicks(ymin, ymax)) {
    line(left, Y(v), w - right, Y(v), '#e5e7eb');
    parts.push(`<text x="${left - 10}" y="${Y(v) + 4}" text-anchor="end">${xml(Number(v.toPrecision(6)))}</text>`);
  }
  if (numericX) for (const x of niceTicks(xmin, xmax))
    parts.push(`<text x="${X(x)}" y="${top + ph + 23}" text-anchor="middle">${xml(Number(x.toPrecision(6)))}</text>`);
  line(left, top, left, top + ph); line(left, top + ph, w - right, top + ph);
  data.groups.forEach((g, index) => {
    const color = COLORS[index % COLORS.length], center = X(index);
    const half = pw / data.groups.length * .22;
    if (data.kind === 'histogram') {
      for (const b of g.bins ?? []) {
        const width = (X(b.high) - X(b.low)) / data.groups.length;
        rect(X(b.low) + width * index, Y(b.count), width, Y(0) - Y(b.count), color, .75);
      }
    } else if (data.kind === 'bar' && g.summary?.mean !== null && g.summary?.mean !== undefined) {
      const mean = g.summary.mean;
      rect(center - half, Math.min(Y(mean), Y(0)), half * 2, Math.abs(Y(mean) - Y(0)), color, .3);
      if (g.interval) {
        const [lo, hi] = g.interval;
        line(center, Y(lo), center, Y(hi), color, 2);
        line(center - half / 2, Y(lo), center + half / 2, Y(lo), color, 2);
        line(center - half / 2, Y(hi), center + half / 2, Y(hi), color, 2);
      }
    } else if (data.kind === 'box' && g.summary && g.whiskers) {
      const { q1, q3, median } = g.summary;
      if (q1 !== null && q3 !== null && median !== null) {
        line(center, Y(g.whiskers[0]), center, Y(g.whiskers[1]), color, 2);
        rect(center - half, Y(q3), half * 2, Y(q1) - Y(q3), color, .25);
        line(center - half, Y(median), center + half, Y(median), color, 2);
      }
    } else if (data.kind === 'violin' && g.density?.length) {
      const max = Math.max(...g.density.map(d => d.density));
      const outline = [...g.density.map(d => `${number(center - d.density / max * half)},${Y(d.value)}`),
        ...[...g.density].reverse().map(d => `${number(center + d.density / max * half)},${Y(d.value)}`)].join(' ');
      parts.push(`<polygon points="${outline}" fill="${color}" fill-opacity="0.25" stroke="${color}"/>`);
    }
    if (data.kind !== 'histogram') g.points.forEach((p, n) => {
      const x = data.kind === 'scatter' ? X(p.x!) : center + ((n * .61803398875) % 1 - .5) * half;
      parts.push(`<circle cx="${number(x)}" cy="${Y(p.y)}" r="2.5" fill="${color}" fill-opacity="0.65"><title>${xml(p.row)}: ${xml(p.x === undefined ? p.y : `${p.x}, ${p.y}`)}</title></circle>`);
    });
    if (!numericX) parts.push(`<text x="${center}" y="${top + ph + 23}" text-anchor="middle">${xml(g.label)}</text><text x="${center}" y="${top + ph + 40}" text-anchor="middle">n=${g.n}</text>`);
    else parts.push(`<text x="${left + index % 4 * 205}" y="${top + ph + 78 + Math.floor(index / 4) * 15}" fill="${color}">${xml(g.label)} · n=${g.n}</text>`);
  });
  annotations.forEach((a, i) => {
    if (!Number.isFinite(a.p) || a.p < 0 || a.p > 1) throw new Error('This saved comparison has an invalid probability.');
    parts.push(`<text x="${left}" y="${580 + i * 18}">${xml(a.groups.join(' versus '))}: p=${xml(a.p)} · ${xml(a.adjustment || a.method)}</text>`);
  });
  parts.push(`<text x="${left + pw / 2}" y="${top + ph + 63}" text-anchor="middle">${xml(axisName(data.axes.x))}</text>`,
    `<text transform="translate(20 ${top + ph / 2}) rotate(-90)" text-anchor="middle">${xml(axisName(data.axes.y))}</text>`,
    `<text x="${w - right}" y="${h - 9}" text-anchor="end">${xml(data.error ? `Error: ${data.error} · ` : '')}${data.excluded.length} excluded rows</text>`, '</g></svg>');
  return parts.join('');
}

export function renderGraph(data: GraphData, title: string): HTMLElement {
  const root = document.createElement('section'); root.className = 'scientific-graph';
  const figure = document.createElement('div'); figure.innerHTML = graphSVG(data, title);
  const svg = figure.querySelector('svg')!; svg.style.width = '100%'; svg.style.height = 'auto';
  const details = document.createElement('details');
  const summary = document.createElement('summary'); summary.textContent = 'Graph definition and exclusions';
  const words = document.createElement('p'); words.textContent = data.definitions;
  const reasons = document.createElement('p'); reasons.textContent = `${data.excluded.length} excluded rows. ${data.groups.map(g => g.densityUnavailable || g.intervalUnavailable || '').filter(Boolean).join(' ')}`;
  details.append(summary, words, reasons); root.append(figure, details); return root;
}
