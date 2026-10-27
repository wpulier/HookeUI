// The Measurements page (#measurements/<id>; docs/design/2026-10-13-researcher-ready.md): what a cell population or a
// motion item measured, as a table a researcher reads, sorts and takes away. Its rows are the server's
// (GET /api/resources/<id>/table, orchestrator/tables.py: the kept evidence, in µm only by its own capture's scale);
// Download CSV is the same table as a file. The session's other measured items are a click away at the top. It is
// reached from Made (a row's Table), the cells' control (Table), Actions (Measurements table) and by asking
// ("show me the table": show_measurements).

/** One column of a table: its key in a row, its words, its unit and kind. */
export interface TableColumn { key: string; label: string; unit: string; type: 'number' | 'text' | 'bool' }
export interface TableStat {
  key: string; n: number; median: number; q1: number; q3: number; min: number; max: number;
  // stats.describe (the server's, never computed here): null where one value cannot give a spread; absent from an older server
  mean?: number | null; sd?: number | null; sem?: number | null; ci95?: [number, number] | null;
}
export interface MeasurementPin { session: string; revision: number }
export interface MeasurementTable extends MeasurementPin {
  id: string; kind: 'population' | 'tracks' | 'result-table'; title: string; count: number; note: string; revision: number;
  memberLabel: string; definitions?: Record<string, string>;
  columns: TableColumn[]; rows: Record<string, unknown>[]; stats: TableStat[];
}
/** A measured item of this session, for the picker (the index's entries of kind population and tracks, and the results
 * that keep a table: a capability's, kinds.py). */
export interface Measured { id: string; kind: string; title: string; at?: number; day: string; version?: number; table?: boolean }

export type SortDir = 'asc' | 'desc';

/** Rows sorted by a column: numbers by value, text by its words; empty values last either way. */
export function sortRows(rows: Record<string, unknown>[], key: string, dir: SortDir): Record<string, unknown>[] {
  const sign = dir === 'asc' ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = a[key], y = b[key];
    const xe = x === null || x === undefined || x === '', ye = y === null || y === undefined || y === '';
    if (xe || ye) return xe === ye ? 0 : xe ? 1 : -1;
    if (typeof x === 'number' && typeof y === 'number') return (x - y) * sign;
    if (typeof x === 'boolean' && typeof y === 'boolean') return (Number(x) - Number(y)) * sign;
    return String(x).localeCompare(String(y), undefined, { numeric: true }) * sign;
  });
}

/** A value as the table shows it: numbers to 3 significant figures (thousands grouped), flags as yes, empty as —. */
export function formatValue(value: unknown, column: TableColumn): string {
  if (value === null || value === undefined || value === '') return '—';
  if (column.type === 'bool') return value ? 'yes' : '';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—';
    if (Math.abs(value) >= 1000) return Math.round(value).toLocaleString('en-US');
    if (Number.isInteger(value)) return String(value);
    return Number(value.toPrecision(3)).toString();
  }
  return String(value);
}

/** A column's header: its words and its unit ("Area (µm²)"). */
export function header(column: TableColumn): string {
  return column.unit ? `${column.label} (${column.unit})` : column.label;
}

/** A summary line for a column: its median and middle half, in its unit. */
export function statWords(stat: TableStat, column: TableColumn): string {
  const f = (v: number) => formatValue(v, column);
  const unit = column.unit ? ` ${column.unit}` : '';
  return `${f(stat.median)}${unit} (middle half ${f(stat.q1)}–${f(stat.q3)})`;
}

/** Its mean ± SD line, in its unit: the mean alone for one value, nothing when the server sent no mean. */
export function meanWords(stat: TableStat, column: TableColumn): string {
  if (typeof stat.mean !== 'number') return '';
  const f = (v: number) => formatValue(v, column);
  const unit = column.unit ? ` ${column.unit}` : '';
  return typeof stat.sd === 'number' ? `mean ${f(stat.mean)} ± ${f(stat.sd)}${unit} (SD)` : `mean ${f(stat.mean)}${unit}`;
}

/** Where a page shows an item ("#measurements/e3"), and the item a page shows. */
export function measurementsHash(id?: string | null, pin?: MeasurementPin): string {
  return id ? `#measurements/${encodeURIComponent(id)}${pin ? `?${new URLSearchParams({ session: pin.session, revision: String(pin.revision) })}` : ''}` : '#measurements';
}
export function measurementsTarget(hash: string): string | null | undefined {
  const match = hash.split('?')[0].match(/^#measurements(?:\/([\w-]+))?$/);
  return match ? (match[1] ?? null) : undefined; // undefined: not this page
}

export function measurementsPin(hash: string): MeasurementPin | undefined {
  const query = hash.split('?')[1];
  if (!query) return undefined;
  const params = new URLSearchParams(query);
  return { session: params.get('session') ?? '', revision: Number(params.get('revision')) };
}

export function tableURL(id: string, pin: MeasurementPin, csv = false): string {
  return `/api/resources/${encodeURIComponent(id)}/table${csv ? '.csv' : ''}?${new URLSearchParams({ session: pin.session, revision: String(pin.revision) })}`;
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, props: Record<string, unknown> = {}, ...children: (Node | string)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (key.startsWith('data-') || key.startsWith('aria-') || key === 'role') node.setAttribute(key, String(value));
    else (node as unknown as Record<string, unknown>)[key] = value;
  }
  node.append(...children);
  return node;
}

/** One result's measurement table at its exact revision (the existing `/table` route), checked to be that one: the
 * Measurements page and the Data reader read it the same way. */
export async function readMeasurementTable(id: string, pin: MeasurementPin, signal?: AbortSignal): Promise<MeasurementTable> {
  const response = await fetch(tableURL(id, pin), { cache: 'no-store', signal });
  if (!response.ok) {
    const said = await response.json().catch(() => ({})) as { error?: string };
    throw new Error(said.error || `No measurements for ${id}`);
  }
  const table = await response.json() as MeasurementTable;
  if (table.id !== id || table.session !== pin.session || table.revision !== pin.revision)
    throw new Error('The measurements changed. Reopen this table before using it.');
  return table;
}

export interface MeasurementsDeps {
  /** Workspace windows keep rows visible, with summary statistics expandable. */
  compact?: boolean;
  /** A verified table has rendered successfully; never called for a stale/failed read. */
  loaded?: (table: MeasurementTable) => void;
  session: () => string;
  /** Shows the item over the live view (the cells' layer or the tracks) and goes back to it. */
  showOnView: (item: MeasurementTable) => void;
}

export class MeasurementsPage {
  readonly root: HTMLElement;
  private readonly deps: MeasurementsDeps;
  private items: Measured[] = [];
  private table: MeasurementTable | null = null;
  private sort: { key: string; dir: SortDir } | null = null;
  private loading = false;
  private problem = '';
  private serial = 0;
  private pending: AbortController | null = null;
  private target: string | null = null;
  private active = false;
  private statsOpen = false;
  private waitingForSession = false;
  private openingPin: MeasurementPin | undefined;

  constructor(root: HTMLElement, deps: MeasurementsDeps) {
    this.root = root;
    this.deps = deps;
    root.classList.add('measurements-page');
  }

  /** The page is shown at an exact result (null selects this session's newest measured item). */
  async show(id: string | null, pin?: MeasurementPin): Promise<void> {
    this.pending?.abort();
    const controller = new AbortController(), serial = ++this.serial, session = this.deps.session();
    this.pending = controller;
    this.active = true;
    this.target = id;
    this.openingPin = pin;
    this.waitingForSession = !session;
    this.items = [];
    this.table = null;
    this.loading = true;
    this.problem = '';
    this.render();
    if (this.waitingForSession) return;
    const current = () => serial === this.serial && this.deps.session() === session && !controller.signal.aborted;
    try {
      if (pin && pin.session !== session) throw new Error('Open the result’s project before reading these measurements.');
      const items = await this.list(session, controller.signal);
      if (!current()) return;
      const selected = id ? items.find(item => item.id === id) : items[0];
      if (id && !selected) throw new Error('These measurements are unavailable in this project. Restore their evidence or open their project.');
      const expected = selected ? pin ?? { session, revision: selected.version ?? 0 } : null;
      const table = selected && expected ? await this.read(selected.id, expected, controller.signal) : null;
      if (!current()) return;
      this.items = items;
      this.table = table;
      this.target = selected?.id ?? id;
      if (table && this.sort && !table.columns.some((c) => c.key === this.sort!.key)) this.sort = null;
    } catch (error) {
      if (!current()) return;
      this.problem = error instanceof Error ? error.message : String(error);
    } finally {
      if (serial === this.serial) {
        this.loading = false;
        if (this.deps.session() !== session) { this.table = null; this.items = []; this.target = null;
          this.problem = 'The project changed. Reopen Measurements for the current project.'; }
        this.render();
        if (this.table && !this.problem && current()) this.deps.loaded?.(this.table);
      }
    }
  }

  hide(): void { this.active = false; this.waitingForSession = false; this.pending?.abort(); this.serial++; this.table = null; }

  async sessionAvailable(): Promise<void> {
    if (this.active && this.waitingForSession && this.deps.session()) await this.show(this.target, this.openingPin);
  }

  sessionChanged(): void {
    if (!this.active) return;
    this.pending?.abort(); this.serial++; this.table = null; this.items = []; this.target = null; this.loading = false;
    this.problem = 'The project changed. Reopen Measurements for the current project.';
    this.render();
  }

  evidenceChanged(id: string, pin: MeasurementPin): void {
    if (!this.active || this.table?.id !== id || this.table.session !== pin.session || this.table.revision === pin.revision) return;
    this.pending?.abort(); this.serial++; this.table = null; this.loading = false;
    this.problem = 'The measurements revision changed. Reopen this table before using it.';
    this.render();
  }

  private async list(session: string, signal: AbortSignal): Promise<Measured[]> {
    const read = async (kind: string) => {
      const response = await fetch(`/api/resources?kind=${kind}&day=${encodeURIComponent(session)}`, { cache: 'no-store', signal });
      if (!response.ok) throw new Error('Could not read this project’s measurements. Try again.');
      const data = await response.json() as { day: string; entries: Measured[] };
      if (data.day !== session || data.entries.some(item => item.day !== session)) throw new Error('The project changed. Reopen Measurements.');
      return data.entries;
    };
    // A capability's kept table is a result that says it holds one (resources.py `table`; live 2026-10-15 its answer said
    // "shown in the measurements table" and this page called it unavailable).
    const [cells, motion, results] = await Promise.all([read('population'), read('tracks'), read('result')]);
    return [...cells, ...motion, ...results.filter(item => item.table)].sort((a, b) => (b.at ?? 0) - (a.at ?? 0));
  }

  private async read(id: string, pin: MeasurementPin, signal: AbortSignal): Promise<MeasurementTable> {
    return readMeasurementTable(id, pin, signal);
  }

  private async download(table: MeasurementTable): Promise<void> {
    const serial = this.serial;
    if (this.deps.session() !== table.session) { this.sessionChanged(); return; }
    try {
      const response = await fetch(tableURL(table.id, table, true), { cache: 'no-store', signal: this.pending?.signal });
      if (!response.ok) {
        const said = await response.json().catch(() => ({})) as { error?: string };
        throw new Error(said.error || 'Could not download these measurements. Try again.');
      }
      const blob = await response.blob();
      if (serial !== this.serial || this.deps.session() !== table.session) return;
      const url = URL.createObjectURL(blob);
      const link = el('a', { href: url, download: `${table.session}_${table.id}_r${table.revision}.csv` });
      link.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (serial !== this.serial || this.deps.session() !== table.session) return;
      this.table = null;
      this.problem = error instanceof Error ? error.message : String(error);
      this.render();
    }
  }

  private render(): void {
    const table = this.table;
    const title = el('h2', { className: 'lib-title', id: 'measurements-title', textContent: 'Measurements' });
    const lead = el('p', { className: 'lib-lead', textContent: table
      ? `${table.title} · ${table.count} ${table.memberLabel}${table.count === 1 ? '' : 's'}`
      : 'Every measured particle, cell or track in this project, as a table you can sort and download.' });
    const right = el('div', { className: 'lib-head-right' });
    if (this.items.length > 1) {
      const pick = el('select', { className: 'meas-pick', 'aria-label': 'Which measurements' });
      for (const item of this.items) pick.append(el('option', { value: item.id, textContent: item.title }));
      pick.value = table?.id ?? '';
      pick.addEventListener('change', () => { location.hash = measurementsHash(pick.value); });
      right.append(pick);
    }
    if (table) {
      if (table.kind !== 'result-table') { // a capability's table has no layer over the view to show it on
        const show = el('button', { type: 'button', className: 'meas-button', textContent: 'Show on the view' });
        show.addEventListener('click', () => { if (this.deps.session() === table.session) this.deps.showOnView(table); else this.sessionChanged(); });
        right.append(show);
      }
      const download = el('button', { type: 'button', className: 'meas-button meas-primary', textContent: 'Download CSV' });
      download.addEventListener('click', () => { void this.download(table); });
      right.append(download);
    }
    const head = el('header', { className: 'lib-head' }, el('div', { className: 'lib-words' }, title, lead), right);
    const body: Node[] = [];
    if (this.loading && !table) body.push(el('p', { className: 'meas-empty', textContent: 'Reading the measurements…' }));
    else if (this.problem) {
      const retry = el('button', { type: 'button', className: 'meas-button', textContent: 'Reopen measurements' });
      retry.addEventListener('click', () => { void this.show(this.target, this.openingPin); });
      body.push(el('p', { className: 'meas-empty', role: 'status', textContent: this.problem }), retry);
    }
    else if (!table) {
      body.push(el('div', { className: 'meas-empty' },
        el('p', { textContent: 'Nothing has been measured in this project yet.' }),
        el('p', { textContent: 'Outline every cell, or measure what moves (Actions › Measure and Motion, or just ask), and its numbers are here.' })));
    } else {
      body.push(el('p', { className: 'meas-note', textContent: table.note }));
      if (this.deps.compact) {
        const summary = el('details', { className: 'meas-summary', open: this.statsOpen },
          el('summary', { textContent: 'Summary statistics' }), this.drawStats(table));
        summary.addEventListener('toggle', () => { this.statsOpen = summary.open; });
        body.push(summary);
      } else body.push(this.drawStats(table));
      body.push(this.drawTable(table));
    }
    this.root.replaceChildren(el('div', { className: 'library-page' }, head, ...body));
  }

  private drawStats(table: MeasurementTable): HTMLElement {
    const byKey = new Map(table.columns.map((c) => [c.key, c]));
    const cards = table.stats.filter((s) => byKey.get(s.key) && !['x', 'y', 'points', 'direction'].includes(s.key)).slice(0, 6)
      .map((s) => {
        const column = byKey.get(s.key)!;
        const mean = meanWords(s, column);
        return el('div', { className: 'meas-stat' },
          el('span', { className: 'meas-stat-name', textContent: `${column.label}, median` }),
          el('span', { className: 'meas-stat-value', textContent: statWords(s, column) }),
          ...(mean ? [el('span', { className: 'meas-stat-name meas-stat-mean', textContent: mean })] : []));
      });
    return el('div', { className: 'meas-stats', 'aria-label': 'Medians and middle halves, means and SDs' }, ...cards);
  }

  private drawTable(table: MeasurementTable): HTMLElement {
    const rows = this.sort ? sortRows(table.rows, this.sort.key, this.sort.dir) : table.rows;
    const headRow = el('tr');
    for (const column of table.columns) {
      const sorted = this.sort?.key === column.key ? this.sort.dir : null;
      const button = el('button', { type: 'button', className: 'meas-sort', textContent: header(column), title: table.definitions?.[column.key] ?? '' });
      button.addEventListener('click', () => {
        const dir: SortDir = sorted === 'desc' ? 'asc' : sorted === 'asc' ? 'desc' : column.type === 'number' ? 'desc' : 'asc';
        this.sort = { key: column.key, dir };
        this.render();
      });
      headRow.append(el('th', { scope: 'col', 'data-type': column.type,
        'aria-sort': sorted === 'asc' ? 'ascending' : sorted === 'desc' ? 'descending' : 'none' }, button));
    }
    const body = el('tbody');
    for (const row of rows) {
      const tr = el('tr');
      for (const column of table.columns) tr.append(el('td', { 'data-type': column.type, textContent: formatValue(row[column.key], column) }));
      body.append(tr);
    }
    return el('div', { className: 'meas-scroll' }, el('table', { className: 'meas-table' },
      el('thead', {}, headRow), body));
  }
}
