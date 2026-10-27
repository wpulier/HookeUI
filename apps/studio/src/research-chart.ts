import type { ResearchChart } from './protocol';

/** One immutable dataset drives bars and table; highlighting never recomputes an aggregate. */
export function renderResearchChart(chart: ResearchChart, index: number, select?: (anchor: string) => void): HTMLElement {
  const section = document.createElement('section'); section.className = 'research-chart'; section.dataset.anchor = `chart:${index}`;
  const title = document.createElement('h3'); title.textContent = chart.title; section.append(title);
  if (chart.transformation !== 'identity' || !chart.rows?.length || chart.rows.length > 50
      || chart.rows.some(row => !Number.isFinite(row.value)) || new Set(chart.rows.map(row => row.unit)).size > 1) {
    const error = document.createElement('p'); error.textContent = 'This chart cannot be validated. Its full findings remain available.';
    section.append(error); return section;
  }
  const minimum = Math.min(0, ...chart.rows.map(row => row.value));
  const maximum = Math.max(0, ...chart.rows.map(row => row.value));
  const span = maximum - minimum || 1;
  const bars = document.createElement('div'); bars.className = 'research-chart-bars';
  const table = document.createElement('table');
  const caption = document.createElement('caption'); caption.textContent = `${chart.title} · retained values`;
  const head = document.createElement('thead'); const headings = document.createElement('tr');
  for (const text of ['Observation', 'Value', 'Denominator', 'Evidence']) {
    const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = text; headings.append(cell);
  }
  head.append(headings); const body = document.createElement('tbody');
  chart.rows.forEach((row, point) => {
    const tr = document.createElement('tr');
    const button = document.createElement('button'); button.type = 'button'; button.className = 'research-chart-point';
    button.setAttribute('aria-pressed', 'false');
    const label = document.createElement('span'); label.textContent = row.label;
    const value = `${row.value} ${row.unit ?? '(unit not recorded)'}`;
    const rail = document.createElement('span'); rail.className = 'research-chart-rail'; rail.setAttribute('aria-hidden', 'true');
    const bar = document.createElement('span'); bar.className = 'research-chart-bar';
    bar.style.left = `${(Math.min(0, row.value) - minimum) / span * 100}%`;
    bar.style.width = `${Math.abs(row.value) / span * 100}%`;
    rail.append(bar); const reading = document.createElement('span'); reading.textContent = value;
    button.append(label, rail, reading);
    button.addEventListener('click', () => {
      for (const other of bars.querySelectorAll('button')) other.setAttribute('aria-pressed', 'false');
      for (const other of body.children) delete (other as HTMLElement).dataset.selected;
      button.setAttribute('aria-pressed', 'true'); tr.dataset.selected = 'true';
      select?.(`chart:${index}:point:${point}`);
    });
    bars.append(button);
    for (const text of [row.label, value, row.denominator === null ? 'Not recorded' : String(row.denominator)]) {
      const cell = document.createElement('td'); cell.textContent = text; tr.append(cell);
    }
    const evidence = document.createElement('td'); const details = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = `${row.source.id}${row.source.version === null ? '' : ` · v${row.source.version}`}`;
    const facts = document.createElement('pre');
    facts.textContent = JSON.stringify({source:row.source, exclusions:row.exclusions ?? 'Not recorded', transformation:'identity'}, null, 2);
    details.append(summary, facts); evidence.append(details); tr.append(evidence); body.append(tr);
  });
  table.append(caption, head, body);
  const data = document.createElement('details'); data.className = 'research-chart-data research-table';
  const summary = document.createElement('summary'); summary.textContent = 'Data, units and sources'; data.append(summary, table);
  const note = document.createElement('p'); note.className = 'sources';
  note.textContent = 'Original retained values · no calculation or unit conversion. Selection highlights evidence; the dataset and denominator stay unchanged.';
  section.append(bars, note, data); return section;
}
