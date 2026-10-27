import { el } from './camera/dom';
import { checkedDataTable, dataTableURL, type DataTablePin, type DataTableOwner, type DataTableSnapshot } from './datatable-view';
import { analysisQuestion, renderAnalysisReadout, type ColumnName } from './analysis-readout';
import { renderGraph, graphSVG, type GraphData } from './graph-render';
import './table-derived-view.css';

export type DerivedKind = 'analysis' | 'graph';
export interface DerivedPin { session: string; id: string; version: number }
export interface DerivedRecord {
  comparison?: {note:string};
  id: string; kind: DerivedKind; version: number; title: string; mode: 'follow' | 'frozen';
  source: DataTablePin & { sha256: string }; spec: Record<string, unknown>; result: Record<string, unknown>;
  freshness: { state: 'current' | 'updating' | 'stale' | 'frozen'; words: string };
}
export interface DerivedViewDeps {
  current(owner: DataTableOwner): boolean;
  /** The caller captures the original placement; a changed Tab must not receive a late result. */
  saved?(record: DerivedRecord): void | Promise<void>;
  /** Existing accepted records, read by exact revision; not proposals or recomputed tests. */
  analyses?(pin: DataTablePin): Promise<DerivedRecord[]>;
  request?: typeof fetch;
}
const METHODS = [['welch', 'Welch t test'], ['paired-t', 'Paired t test'], ['mann-whitney', 'Mann–Whitney'],
  ['anova-tukey', 'ANOVA with Tukey comparisons'], ['kruskal-dunn', 'Kruskal–Wallis with Dunn comparisons'],
  ['pearson', 'Pearson correlation'], ['linear-regression', 'Linear regression']];
const GRAPHS = [['histogram', 'Histogram'], ['box', 'Box'], ['violin', 'Violin'], ['bar', 'Mean and uncertainty'], ['scatter', 'Scatter']];
const message = (error: unknown) => error instanceof Error ? error.message : String(error);
const button = (label: string, action: () => void) => { const b = el('button', { type: 'button', textContent: label }); b.addEventListener('click', action); return b; };
const select = (name: string, choices: string[][]) => el('select', { 'aria-label': name },
  el('option', { value: '', textContent: `Choose ${name.toLowerCase()}…` }), ...choices.map(([value, textContent]) => el('option', { value, textContent })));
const display = (value: unknown): string => value == null ? 'Unavailable' : typeof value === 'object' ? JSON.stringify(value) : String(value);
function detail(title: string, value: unknown): HTMLElement {
  return el('details', {}, el('summary', { textContent: title }), el('pre', { textContent: JSON.stringify(value, null, 2) }));
}
export function derivedURL(pin: DerivedPin): string {
  return `/api/table-derivatives/${encodeURIComponent(pin.id)}?${new URLSearchParams({ session: pin.session, version: String(pin.version) })}`;
}
/** Rasterizes the same exported SVG; canvas performs no scientific calculations. */
export async function graphPNG(svg: string): Promise<Blob> {
  const size = /width="(\d+)" height="(\d+)"/.exec(svg);
  if (!size || Number(size[1]) * Number(size[2]) > 4_000_000) throw new Error('The graph exceeds its supported picture size.');
  const image = new Image(), url = URL.createObjectURL(new Blob([svg], { type: 'image/svg+xml' }));
  let timer: ReturnType<typeof setTimeout> | undefined;
  let active = true;
  try {
    image.src = url;
    return await Promise.race([image.decode().then(async () => {
      if (!active) throw new Error('Picture export was cancelled.');
      const canvas = document.createElement('canvas'); canvas.width = Number(size[1]); canvas.height = Number(size[2]);
      const context = canvas.getContext('2d'); if (!context) throw new Error('Picture export is unavailable in this browser.');
      context.drawImage(image, 0, 0);
      return await new Promise<Blob>((resolve, reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('Picture encoding failed.')), 'image/png'));
    }), new Promise<Blob>((_, reject) => { timer = setTimeout(() => reject(new Error('Picture export timed out; the saved SVG remains available.')), 10000); })]);
  } finally { active = false; clearTimeout(timer); URL.revokeObjectURL(url); }
}
export function checkedDerived(value: DerivedRecord, session: string, pin?: DerivedPin): DerivedRecord {
  if (!value || !['analysis', 'graph'].includes(value.kind) || !new RegExp(value.kind === 'analysis' ? '^an[1-9][0-9]*$' : '^gr[1-9][0-9]*$').test(value.id)
      || !Number.isSafeInteger(value.version) || value.version < 1 || value.source?.session !== session
      || !/^tb[1-9][0-9]*$/.test(value.source.id) || !Number.isSafeInteger(value.source.version) || value.source.version < 1
      || !/^[a-f0-9]{64}$/.test(value.source.sha256) || !['follow', 'frozen'].includes(value.mode)
      || !['current', 'updating', 'stale', 'frozen'].includes(value.freshness?.state)
      || !value.spec || !value.result || typeof value.result !== 'object'
      || pin && (pin.id !== value.id || pin.version !== value.version)) throw new Error('This saved result does not match the selected Project and revision.');
  return value;
}

/** Retained results only: opening never recomputes. The shared artifact shell owns this content. */
export class TableDerivedView {
  readonly root: HTMLElement;
  private readonly deps: DerivedViewDeps;
  private owner: DataTableOwner | null = null;
  private record: DerivedRecord | null = null;
  private source: DataTablePin | null = null;
  private kind: DerivedKind = 'analysis';
  private serial = 0;
  private pending: AbortController | null = null;
  private working = false;
  private draft = false;
  private readonly status = el('p', { role: 'status', 'aria-live': 'polite' });
  private readonly body = el('div');
  private readonly actions = el('div', { className: 'derived-actions' });
  constructor(root: HTMLElement, deps: DerivedViewDeps) {
    this.root = root; this.deps = deps; root.classList.add('table-derived-view'); root.replaceChildren(this.body, this.status, this.actions); // the result first, then its state and actions
  }
  get busy(): boolean { return this.working; }
  get dirty(): boolean { return this.draft; }
  canClose(): boolean { return !this.draft && !this.working; }
  /** A shell must resolve a draft before hiding it; hidden requests cannot publish into another opening. */
  hide(): void { this.serial++; this.pending?.abort(); this.pending = null; }
  discardDraft(): void { if (!this.working) { this.draft = false; this.status.textContent = 'Setup discarded; no calculation was run.'; this.body.replaceChildren(); this.actions.replaceChildren(); } }
  private current(): boolean { return !!this.owner && this.deps.current(this.owner); }
  private controls(): void { for (const tag of ['button', 'input', 'select']) for (const node of this.root.querySelectorAll<HTMLInputElement>(tag)) node.disabled = this.working || !this.current(); }
  private reset(owner: DataTableOwner): boolean {
    if (!this.canClose() || !this.deps.current(owner)) return false;
    this.hide(); this.owner = { ...owner }; this.record = null; this.source = null; this.actions.replaceChildren(); this.body.replaceChildren(); return true;
  }
  setup(kind: DerivedKind, pin: DataTablePin, table: DataTableSnapshot, owner: DataTableOwner): boolean {
    if (owner.session !== pin.session) return false;
    checkedDataTable(table, pin); if (!this.reset(owner)) return false;
    this.kind = kind; this.source = { ...pin };
    this.status.textContent = `Choose settings for ${table.title}, saved revision ${pin.version}. Nothing runs until you choose Calculate and save.${table.comparison?.note ? ' '+table.comparison.note : ''}`;
    const form = el('div', { className: 'derived-setup' });
    const method = select(kind === 'analysis' ? 'Method' : 'Graph type', kind === 'analysis' ? METHODS : GRAPHS);
    const numeric = table.columns.filter(c => c.type === 'number').map(c => [c.key, `${c.label}${c.unit ? ` (${c.unit})` : ''}`]);
    const columns = table.columns.map(c => [c.key, c.label]);
    const value = select('Value', numeric), x = select('X', numeric), y = select('Y', numeric);
    const group = select('Group', columns), pair = select('Pair key', columns);
    group.querySelector('option')!.textContent = 'All rows (no grouping)';
    const groups = el('div', { className: 'derived-groups' });
    let labels: unknown[] = [], ordered: unknown[] = [];
    const renderGroups = () => {
      labels = []; ordered = []; groups.replaceChildren();
      if (!group.value) return;
      const seen = new Set<string>();
      for (const row of table.rows) { const v = row[group.value] ?? null, key = JSON.stringify([typeof v, v]); if (!seen.has(key)) { seen.add(key); labels.push(v); } }
      if (labels.length > 12) { groups.textContent = 'This column has more than 12 groups. Choose another grouping column.'; return; }
      groups.append(el('p', { textContent: 'Select groups in comparison order. No pair matching is inferred.' }));
      for (const label of labels) { const box = el('input', { type: 'checkbox' }); box.addEventListener('change', () => { ordered = ordered.filter(v => !Object.is(v, label)); if (box.checked) ordered.push(label); }); groups.append(el('label', {}, box, display(label))); }
    };
    group.addEventListener('change', renderGroups);
    const bins = el('input', { type: 'number', min: '1', max: '200', step: '1', value: '20', 'aria-label': 'Bins' });
    const error = select('Uncertainty', [['sd', 'Standard deviation'], ['sem', 'Standard error'], ['ci95', '95% Student-t interval']]);
    const annotation = select('Saved analysis annotations', []);
    annotation.querySelector('option')!.textContent = 'No analysis annotations';
    const annotationNote = el('p');
    let acceptedAnalyses: DerivedRecord[] = [];
    const fields = [value, x, y, group, pair, bins, error, annotation];
    const sync = () => {
      const xy = ['pearson', 'linear-regression', 'scatter'].includes(method.value);
      value.hidden = xy; x.hidden = y.hidden = !xy; pair.hidden = method.value !== 'paired-t';
      bins.hidden = method.value !== 'histogram'; error.hidden = method.value !== 'bar';
      annotation.hidden = annotationNote.hidden = kind !== 'graph' || !['box', 'violin', 'bar'].includes(method.value);
    };
    method.addEventListener('change', sync); sync();
    form.append(method, ...fields, annotationNote, groups);
    if (kind === 'graph') {
      annotationNote.textContent = this.deps.analyses ? 'Loading saved analyses for this table revision…' : 'No saved analysis list is available. The graph can be saved without annotations.';
      const serial = this.serial;
      void this.deps.analyses?.({ ...pin }).then(records => {
        if (serial !== this.serial || !this.current() || this.record) return;
        acceptedAnalyses = records.slice(0, 200).filter(record => {
          try { checkedDerived(record, pin.session); return record.kind === 'analysis' && record.source.id === pin.id && record.source.version === pin.version; } catch { return false; }
        });
        annotation.append(...acceptedAnalyses.map((record, i) => el('option', { value: String(i), textContent: `${record.title} · revision ${record.version}` })));
        annotationNote.textContent = acceptedAnalyses.length ? 'Optional: the saved analysis must also match these exact groups and included rows. No new test is run.' : 'No matching saved analyses. Save an analysis of this table revision first, or omit annotations.';
      }).catch(error => { if (serial === this.serial && this.current()) annotationNote.textContent = `Saved analyses unavailable: ${message(error)}. The graph can be saved without annotations.`; });
    }
    const run = button('Calculate and save', () => { void this.calculate(); });
    this.actions.append(run, button('Discard setup', () => this.discardDraft()));
    this.body.append(el('h2', { textContent: kind === 'analysis' ? 'Analyze saved table' : 'Graph saved table' }), form,
      el('p', { textContent: `${table.counts.included} included rows; ${table.counts.excluded} excluded. Missing numeric values are reported separately. Tests and group order are your explicit choices.` }));
    this.readSpec = () => {
      if (!method.value) throw new Error('Choose the method or graph type explicitly.');
      const xy = ['pearson', 'linear-regression', 'scatter'].includes(method.value);
      const spec: Record<string, unknown> = { [kind === 'analysis' ? 'method' : 'kind']: method.value };
      if (xy) { if (!x.value || !y.value) throw new Error('Choose both numeric columns.'); spec.x = x.value; spec.y = y.value; }
      else { if (!value.value) throw new Error('Choose a numeric value column.'); spec.value = value.value; }
      if (group.value) { const selected = [...ordered]; if (!selected.length || labels.length > 12) throw new Error('Select one to twelve groups explicitly.'); spec.group = group.value; spec.groups = selected; }
      if (method.value === 'paired-t') { if (!pair.value) throw new Error('Choose the pair-key column.'); spec.pair = pair.value; }
      if (method.value === 'histogram') { const n = Number(bins.value); if (!Number.isInteger(n) || n < 1 || n > 200) throw new Error('Choose 1 to 200 bins.'); spec.bins = n; }
      if (method.value === 'bar') { if (!error.value) throw new Error('Choose the uncertainty to display.'); spec.error = error.value; }
      if (kind === 'graph' && ['box', 'violin', 'bar'].includes(method.value) && annotation.value !== '') {
        const record = acceptedAnalyses[Number(annotation.value)]; if (!record) throw new Error('Choose an available saved analysis.');
        spec.analysis = { id: record.id, version: record.version };
      }
      return spec;
    };
    form.addEventListener('change', () => { this.draft = true; this.status.textContent = 'Draft settings; not calculated or saved.'; });
    return true;
  }
  private readSpec: () => Record<string, unknown> = () => { throw new Error('Open a table setup first.'); };
  async calculate(): Promise<boolean> {
    if (!this.source || !this.current() || this.working) return false;
    try { const spec = this.readSpec(); return await this.write({ session: this.source.session, kind: this.kind, table: { id: this.source.id, version: this.source.version }, spec }, true); }
    catch (error) { this.status.textContent = message(error); return false; }
  }
  async open(pin: DerivedPin, owner: DataTableOwner): Promise<boolean> {
    if (pin.session !== owner.session || !this.reset(owner)) return false;
    const serial = this.serial, controller = this.pending = new AbortController();
    this.status.textContent = 'Opening saved result…';
    try {
      const value = checkedDerived(await this.request(derivedURL(pin), { signal: controller.signal }), pin.session, pin);
      if (serial !== this.serial || !this.current()) return false;
      this.record = value; this.render(); return true;
    } catch (error) { if (serial === this.serial && this.current()) { this.status.textContent = `Unable to open: ${message(error)}`; this.actions.append(button('Retry opening', () => void this.open(pin, owner))); } return false; }
  }
  private async request(url: string, init?: RequestInit): Promise<DerivedRecord> {
    const response = await (this.deps.request ?? fetch)(url, { cache: 'no-store', ...init }); const value = await response.json();
    if (!response.ok) throw new Error([value.error, value.next].filter(Boolean).join('. ') || 'The operation was not accepted.'); return value;
  }
  async update(): Promise<boolean> { return this.mutate({ update: true }); }
  async setMode(mode: 'follow' | 'frozen'): Promise<boolean> { return this.mutate({ mode }); }
  private async mutate(change: Record<string, unknown>): Promise<boolean> {
    if (!this.record || !this.current() || this.working) return false;
    return this.write({ session: this.record.source.session, version: this.record.version, ...change }, false);
  }
  private async write(body: Record<string, unknown>, create: boolean): Promise<boolean> {
    const serial = this.serial, previous = this.record, source = this.source && { ...this.source };
    this.working = true; this.controls(); this.status.textContent = create ? 'Calculating and saving…' : 'Saving requested update…';
    let accepted = false;
    try {
      const value = checkedDerived(await this.request(`/api/table-derivatives${create ? '' : `/${encodeURIComponent(previous!.id)}`}`, {
        method: create ? 'POST' : 'PATCH', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'datatable' }, body: JSON.stringify(body),
      }), this.owner!.session);
      if (serial !== this.serial || !this.current()) return false;
      if (create && (value.kind !== this.kind || value.source.id !== source!.id || value.source.version !== source!.version)
          || !create && (value.id !== previous!.id || value.kind !== previous!.kind || value.version < previous!.version)) throw new Error('The result changed its captured source or identity. Inspect Project Data before retrying.');
      this.record = value; this.draft = false; accepted = true; this.render();
      try { await this.deps.saved?.(structuredClone(value)); } catch (error) { if (serial === this.serial && this.current()) this.status.textContent = `Saved in Data; window reference update failed: ${message(error)}`; }
      return true;
    } catch (error) { if (serial === this.serial && this.current()) this.status.textContent = `${accepted ? 'Saved, but display failed' : 'No accepted result received; inspect Data before retrying'}. ${message(error)}`; return false; }
    finally { this.working = false; if (serial === this.serial && this.current()) this.controls(); }
  }
  async refresh(): Promise<void> {
    if (!this.record || this.working || !this.current()) return;
    const previous = this.record, serial = this.serial;
    try {
      const value = checkedDerived(await this.request(derivedURL({ session: previous.source.session, id: previous.id, version: previous.version })), previous.source.session,
        { session: previous.source.session, id: previous.id, version: previous.version });
      if (serial !== this.serial || !this.current() || this.record !== previous) return;
      previous.freshness = value.freshness; this.status.textContent = `Saved · ${value.freshness.words}`;
    } catch (error) { if (serial === this.serial && this.current()) this.status.textContent = `Freshness unavailable: ${message(error)}`; }
  }
  /** Column names of the analysed table revision, read once per record, so the result speaks in the table's words. */
  private names: Record<string, ColumnName> = {};
  private namesFor = '';
  private readNames(record: DerivedRecord): void {
    const key = `${record.source.session}:${record.source.id}:${record.source.version}`; if (key === this.namesFor) return; this.namesFor = key;
    const serial = this.serial;
    // A read of names only, kept apart from the result's own transport; without it the keys are shown instead.
    if (typeof fetch !== 'function') return;
    void fetch(dataTableURL({ session: record.source.session, id: record.source.id, version: record.source.version }), { cache: 'no-store' })
      .then(response => response.ok ? response.json() : null).then((table: DataTableSnapshot | null) => {
        if (!table || serial !== this.serial || this.record !== record || !Array.isArray(table.columns)) return;
        this.names = Object.fromEntries(table.columns.map(c => [c.key, { label: c.label, unit: c.unit }])); this.render();
      }).catch(() => undefined); // the keys stay readable without it
  }
  private render(): void {
    const record = this.record!; this.actions.replaceChildren();
    this.readNames(record);
    // The question as the title (Will: the analysis window "kinda confuses me"); the generic server title stays in Details.
    // A histogram is "Distribution of Brightness"; other graphs say what is plotted against what.
    const title = record.kind === 'analysis' ? analysisQuestion(record.spec, this.names)
      : record.spec.kind === 'histogram' ? `Distribution of ${analysisQuestion(record.spec, this.names)}` : analysisQuestion(record.spec, this.names);
    this.body.replaceChildren(el('h2', { textContent: title }));
    this.status.textContent = `Saved · ${record.freshness.words}`;
    if(record.comparison?.note)this.body.append(el('p',{className:'datatable-note',textContent:record.comparison.note}));
    if (record.kind === 'graph') this.body.append(renderGraph(record.result as unknown as GraphData, title));
    else this.body.append(renderAnalysisReadout(record.result, record.spec, this.names));
    // Exact identity, settings and every raw value: one disclosure, last. Reopening never recalculates.
    const technical = el('details', { className: 'derived-technical' }, el('summary', { textContent: 'Technical details' }),
      el('p', { textContent: `From saved table ${record.source.id}, revision ${record.source.version}. Reopening does not recalculate.` }),
      detail('Settings and exact source', { title: record.title, spec: record.spec, source: record.source, id: record.id, version: record.version }));
    if (record.kind === 'analysis') { const r = record.result; technical.append(detail('Every value, as saved', { method: r.method, statistic: r.statistic, df: r.df, p: r.p, effect: r.effect, pairs: r.pairs, assumptions: r.assumptions, units: r.units, notes: r.notes }), detail('Included rows and exclusions', { groups: r.groups, exclusions: r.exclusions })); }
    this.body.append(technical);
    const more = el('details', { className: 'datatable-more' }), menu = el('div', { className: 'datatable-more-items' });
    more.append(el('summary', { textContent: 'More' }), menu);
    menu.append(button('Check freshness', () => void this.refresh()), button('Update from table', () => void this.update()),
      button(record.mode === 'frozen' ? 'Follow source' : 'Freeze', () => void this.setMode(record.mode === 'frozen' ? 'follow' : 'frozen')));
    const exportXlsx = button('Export table and analyses (XLSX)', () => { if (this.current() && !this.working) window.open(`/api/datatables/${encodeURIComponent(record.source.id)}.xlsx?${new URLSearchParams({ session: record.source.session, version: String(record.source.version) })}`, '_blank', 'noopener'); });
    if (record.kind === 'graph') this.actions.append(button('Download SVG', () => void this.downloadGraph('svg')), button('Download PNG', () => void this.downloadGraph('png')));
    this.actions.append(exportXlsx, more);
  }
  private async downloadGraph(format: 'svg' | 'png'): Promise<void> {
    if (!this.current() || this.working || this.record?.kind !== 'graph') return;
    const record = this.record, serial = this.serial; this.working = true; this.controls();
    try {
      const svg = graphSVG(record.result as unknown as GraphData, record.title);
      const blob = format === 'svg' ? new Blob([svg], { type: 'image/svg+xml' }) : await graphPNG(svg);
      if (serial !== this.serial || !this.current() || this.record !== record) return;
      const url = URL.createObjectURL(blob); const link = el('a', { href: url, download: `${record.id}-v${record.version}.${format}` });
      link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (serial === this.serial && this.current()) this.status.textContent = `Export failed: ${message(error)}. The saved result is unchanged.`; }
    finally { this.working = false; if (serial === this.serial && this.current()) this.controls(); }
  }
}
