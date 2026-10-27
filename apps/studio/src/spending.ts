import type { Panel } from './panels';
import { positionMeter } from './meter-popover';

/** One provider/model/area's tracked spend in a period (api_usage's ledger). */
export interface SpendRow {
  provider: string; model: string; area: string; events: number; estimatedUsd: number;
  unpriced: number; basis: string; units: number;
}

export interface Spending {
  day: string;
  /** The daily research allowance (a separate setting: Settings), or null. */
  cap: number | null;
  estimatedUsd: number;
  trackingSince: number;
  unpriced: number;
  recordingErrors: number;
  entries: SpendRow[];
  /** This calendar month, and the optional monthly limit on cloud models (api_usage; C7). Unknown charges count, never free. */
  month?: { month: string; estimatedUsd: number; unknownCalls: number; inFlightUsd: number; cap: number | null;
    entries?: SpendRow[]; unknownEachUsd?: number; unknownAllowanceUsd?: number };
}

export type Period = 'today' | 'month';

export function dollars(value: number): string {
  return value > 0 && value < .01 ? '<$0.01' : `$${value.toFixed(2)}`;
}

/** What the user typed as a monthly limit: a sum of dollars, zero or more. Empty is never "remove" (Remove does that). */
export function parseLimit(text: string): { usd: number } | { error: string } {
  const cleaned = text.trim().replace(/^\$/, '').replace(/,/g, '');
  if (!cleaned) return { error: 'Type an amount in dollars, such as 20.' };
  if (!/^\d+(\.\d{1,2})?$/.test(cleaned)) return { error: 'Type an amount in dollars, such as 20 or 12.50.' };
  const usd = Number(cleaned);
  return Number.isFinite(usd) && usd >= 0 && usd <= 100000 ? { usd } : { error: 'Type an amount from $0 to $100,000.' };
}

export interface SpendingView {
  /** The optional limit's visible status: "Limit not set" or "Monthly limit: $20.00". */
  headline: string;
  /** This month against the limit (what the limit counts), or what was tracked with none, in one line. */
  used: string;
  /** What the limit counts beyond tracked spend (work in progress, unknown costs), plainly; null when nothing. */
  counted: string | null;
  /** 0..1 of the limit used (null with no limit). */
  share: number | null;
  /** Said only when the limit is reached: what stops. */
  reached: string | null;
  /** What the limit covers and how, in one short line. */
  covers: string;
  /** The daily research allowance, said apart from the monthly limit. */
  research: string;
  /** The period's analytics: tracked charges only, its total the sum of its rows. */
  period: { label: string; total: string; qualifier: string; rows: { provider: string; total: string; models: { model: string; total: string; detail: string }[] }[];
    notes: string[]; details: string[]; recordedSince: string };
}

const REACHED = 'Limit reached: the app asks no cloud model (chat, looks, naming, voice, researchers) until next month or '
  + 'until you raise it.';
const COVERS = 'Applies to cloud calls made by this app. With a limit set, calls are blocked when their estimated '
  + 'maximum cost is unknown or exceeds the remaining limit.';
const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Tracked spend in some rows: the sum of their known charges, and how many records have no known cost. */
export function tracked(rows: SpendRow[]): { usd: number; unknown: number } {
  return { usd: rows.reduce((sum, row) => sum + row.estimatedUsd, 0), unknown: rows.reduce((sum, row) => sum + row.unpriced, 0) };
}

const withUnknown = (usd: number, unknown: number) => unknown && usd <= 0 ? 'unknown cost' : `${dollars(usd)}${unknown ? ' + unknown' : ''}`;

/** Everything the panel says, from the wire alone (tested without a page). Two quantities are kept apart (Codex's
 * recheck of e38fc57b, finding 3): what the limit counts (month.estimatedUsd: tracked charges, work in progress held at
 * its most and each unknown-cost call at an allowance; conservative, for admission) and tracked spend (the rows: known
 * charges, unknown ones beside them), which the analytics show, their total the sum of the rows shown. */
export function spendingView(data: Spending, period: Period): SpendingView {
  const month = data.month;
  const limit = month?.cap ?? null;
  const counted = month?.estimatedUsd ?? 0;
  const monthTracked = tracked(month?.entries ?? []);
  const headline = limit === null ? 'Limit not set' : `Monthly limit: ${dollars(limit)}`;
  const used = limit === null
    ? `${withUnknown(monthTracked.usd, monthTracked.unknown)} tracked this month`
    : `${dollars(counted)} of ${dollars(limit)} counted toward the limit this month`;
  const parts: string[] = [];
  if (month?.inFlightUsd) parts.push(`${dollars(month.inFlightUsd)} held for work in progress`);
  if (month?.unknownCalls) parts.push(month.unknownAllowanceUsd !== undefined
    ? `${plural(month.unknownCalls, 'call')} of unknown cost: ${dollars(month.unknownAllowanceUsd)} held for the limit`
    : `${plural(month.unknownCalls, 'call')} of unknown cost at ${dollars(month.unknownEachUsd ?? 0.02)} each`);
  const countedLine = limit !== null && parts.length ? `Includes ${parts.join(' and ')}.` : null;
  const share = limit === null ? null : limit > 0 ? Math.min(1, counted / limit) : 1;
  const reached = limit !== null && counted >= limit - 1e-9 ? REACHED : null;
  const research = data.cap === null
    ? 'Research allowance: none per day (a separate setting in Settings)'
    : `Research allowance: ${dollars(data.cap)} a day, for researchers only (a separate setting in Settings)`;
  const rows = period === 'month' ? month?.entries ?? [] : data.entries;
  const total = tracked(rows);
  const providers = [...new Set(rows.map(row => row.provider))].map(provider => {
    const own = rows.filter(row => row.provider === provider);
    const sum = tracked(own);
    return {
      provider,
      total: withUnknown(sum.usd, sum.unknown),
      models: own.map(row => ({ model: row.model, total: withUnknown(row.estimatedUsd, row.unpriced),
        detail: `${row.area}${row.events ? ` \u00b7 ${plural(row.events, 'record')}` : ''} \u00b7 ${row.basis}` })),
    };
  });
  const notes: string[] = [];
  const details: string[] = [];
  if (!rows.length) notes.push('No cloud usage tracked in this period yet.');
  if (total.unknown) notes.push(`${plural(total.unknown, 'record')} of unknown cost: not in this total, never counted as free.`);
  if (period === 'month' && month?.inFlightUsd) details.push(`${dollars(month.inFlightUsd)} is held for work in progress, `
    + 'not included in the usage estimate until its cost is recorded.');
  if (data.recordingErrors) notes.push('Some usage could not be saved: this is incomplete.');
  details.push('Estimates use provider list prices, before discounts, free allowances and credits. Your provider\u2019s bill can differ.',
    'Only API use recorded by this app is included, not your whole provider account. Local computation has no API charge.');
  return {
    headline, used, counted: countedLine, share, reached, covers: COVERS, research,
    period: { label: period === 'month' ? `This month (${month?.month ?? ''})` : `Today (${data.day})`,
      total: total.unknown && total.usd <= 0 ? 'Unknown' : dollars(total.usd),
      qualifier: total.unknown ? `estimated, plus ${plural(total.unknown, 'record')} of unknown cost` : 'estimated',
      rows: providers, notes, details,
      recordedSince: `Tracked since ${new Date(data.trackingSince * 1000).toLocaleDateString()}.` },
  };
}

const element = <K extends keyof HTMLElementTagNameMap>(tag: K, text: string, className = '') =>
  Object.assign(document.createElement(tag), { textContent: text, className });

type Edit = { mode: 'none' } | { mode: 'set' | 'change'; draft: string; error?: string; saving?: boolean }
  | { mode: 'remove'; error?: string; saving?: boolean };

/** Personal API use by period, with a quiet optional limit and supporting accounting detail. Polls every 5 s only while
 * open; a draft, its focus and any message survive it. */
export class SpendingMeter implements Panel {
  readonly escapePriority = 100;
  readonly id = 'spending';
  readonly root = element('div', '', 'perf spending');
  private readonly button = element('button', 'Spending', 'perf-dot spending-button');
  private readonly panel = element('div', '', 'perf-panel spending-panel');
  private readonly limitArea = element('section', '', 'spending-limit-area');
  private readonly analytics = element('section', 'Loading spending…', 'spending-analytics');
  private readonly details = element('details', '', 'spending-details');
  private readonly detailsBody = element('div', '', 'spending-details-body');
  private open = false;
  private timer = 0;
  private generation = 0;
  /** Bumped when a save starts and when it is confirmed: a read begun before is never drawn (it may predate it). */
  private barrier = 0;
  /** The barrier of the read in flight (-1: none): one read at a time per barrier. */
  private inflight = -1;
  private lastData = '';
  private data: Spending | null = null;
  private period: Period = 'month';
  private edit: Edit = { mode: 'none' };
  private saved = '';

  private readonly load: () => Promise<Spending>;
  private readonly setCap?: (usd: number | null) => Promise<unknown>;

  constructor(load: () => Promise<Spending>, setCap?: (usd: number | null) => Promise<unknown>) {
    this.load = load;
    this.setCap = setCap;
    this.button.type = 'button';
    this.button.setAttribute('aria-haspopup', 'dialog');
    this.button.setAttribute('aria-expanded', 'false');
    this.button.setAttribute('aria-controls', 'spending-panel');
    this.panel.id = 'spending-panel';
    this.panel.hidden = true;
    this.panel.setAttribute('role', 'dialog');
    this.panel.setAttribute('aria-label', 'Spending');
    const head = element('div', '', 'spending-head');
    const close = element('button', '×', 'spending-close');
    close.type = 'button';
    close.setAttribute('aria-label', 'Close spending');
    close.onclick = () => this.close();
    head.append(element('strong', 'Spending'), close);
    const context = element('p', 'Usage history is stored on this Mac. API requests go to the providers you use.', 'spending-context');
    this.details.append(element('summary', 'How this is counted'), this.detailsBody);
    this.panel.append(head, element('p', 'Your API use', 'spending-title'), context,
      this.limitArea, this.analytics, this.details);
    this.root.append(this.button, this.panel);
    this.button.onclick = () => this.show(!this.open);
    this.root.addEventListener('keydown', event => {
      if (event.key === 'Escape' && this.open) {
        event.preventDefault();
        event.stopPropagation();
        if (this.edit.mode !== 'none' && !this.edit.saving) { this.edit = { mode: 'none' }; this.renderLimit(); return; }
        this.close();
      }
    });
    window.addEventListener('resize', () => this.open && this.position());
    window.addEventListener('scroll', () => this.open && this.position(), true);
    window.visualViewport?.addEventListener('resize', () => this.open && this.position());
    window.visualViewport?.addEventListener('scroll', () => this.open && this.position());
  }

  isOpen(): boolean { return this.open; }
  footprint(): null { return null; }
  /** Placed again by its button while open: the workspace's layout changed (a side column opened or closed). */
  place(): void { if (this.open) this.position(); }

  /** Today's tracked spend on its button once there is any ("$0.20 today"; the panel's Today total, from the same data),
   * else "Spending". Read when the page loads and when a researcher or a run ends (`refreshTotal`), never on a timer
   * while closed; open, the panel's own reads keep it current. */
  private label(): void {
    const today = this.data ? tracked(this.data.entries).usd : 0;
    const words = today > 0 ? `${dollars(today)} today` : 'Spending';
    if (this.button.textContent !== words) this.button.textContent = words;
    this.button.setAttribute('aria-label', today > 0 ? `Spending: ${words}` : 'Spending');
  }

  /** Reads today's total once for the button (the page loaded, a researcher or a run ended). */
  async refreshTotal(): Promise<void> {
    if (this.open) return; // its polling reads it
    try {
      const data = await this.load();
      if (this.open) return;
      this.data = data; this.lastData = '';
      this.label();
    } catch { /* the button keeps what it said */ }
  }
  close(): void { this.button.focus(); this.show(false); }

  private show(open: boolean): void {
    if (this.open === open) return;
    this.open = open;
    this.generation++;
    this.inflight = -1;
    this.panel.hidden = !open;
    this.button.setAttribute('aria-expanded', String(open));
    window.clearInterval(this.timer);
    if (open) {
      // Drawn again from its own state at once (a draft let go when it closed is never left on the page), then read.
      this.lastData = '';
      if (this.data) { this.renderLimit(); this.renderAnalytics(); }
      this.position();
      void this.refresh();
      this.timer = window.setInterval(() => void this.refresh(), 5000);
    } else if (!(this.edit.mode !== 'none' && this.edit.saving)) {
      this.edit = { mode: 'none' };  // a draft not saved is let go with the menu; one being saved finishes
      this.saved = '';
      if (this.data) this.renderLimit();
    }
  }

  private position(): void {
    positionMeter(this.panel, this.button);
  }

  private async refresh(): Promise<void> {
    const barrier = this.barrier;
    if (this.inflight === barrier) return;
    this.inflight = barrier;
    const generation = this.generation;
    const current = () => this.open && generation === this.generation && barrier === this.barrier;
    try {
      const data = await this.load();
      const text = JSON.stringify(data);
      if (current() && this.lastData !== text) {
        this.data = data;
        this.lastData = text;
        this.render();
        this.label();
      }
    } catch {
      if (!current()) return;
      this.lastData = '';
      const retry = element('button', 'Retry', 'spending-action');
      retry.type = 'button';
      retry.onclick = () => void this.refresh();
      // The limit stays as last confirmed; only the totals are said to be unavailable.
      this.analytics.replaceChildren(element('p', 'The totals could not be loaded just now.', 'spending-error'), retry);
    } finally {
      if (this.inflight === barrier) this.inflight = -1;
    }
  }

  private render(): void {
    // The limit's controls are drawn again only when nothing is being typed there: a draft and its focus survive polling.
    if (this.edit.mode === 'none' || !this.limitArea.contains(document.activeElement)) this.renderLimit();
    this.renderAnalytics();
  }

  private renderLimit(): void {
    const data = this.data;
    if (!data) return;
    const view = spendingView(data, this.period);
    const status = element('div', '', 'spending-limit-status');
    status.append(element('p', view.headline, 'spending-headline'));
    const nodes: HTMLElement[] = [status];
    if (this.edit.mode === 'none') status.append(this.controls(data));
    else nodes.push(this.controls(data));
    if (view.reached) {
      nodes.push(element('p', view.reached, 'spending-reached'), element('p', view.used, 'spending-note'));
      if (view.counted) nodes.push(element('p', view.counted, 'spending-note'));
    }
    if (this.saved && this.edit.mode === 'none') nodes.push(element('p', this.saved, 'spending-saved'));
    if (data.month?.cap != null) nodes.push(element('p', view.covers, 'spending-note'));
    if (data.cap !== null) nodes.push(element('p', view.research, 'spending-research'));
    this.limitArea.replaceChildren(...nodes);
  }

  private controls(data: Spending): HTMLElement {
    const box = element('div', '', 'spending-controls');
    const limit = data.month?.cap ?? null;
    if (!this.setCap) return box;
    const edit = this.edit;
    if (edit.mode === 'none') {
      if (limit === null) {
        box.append(this.action('Set a monthly limit', () => this.begin('set', '')));
      } else {
        box.append(this.action('Change', () => this.begin('change', String(limit))),
          this.action('Remove limit', () => { this.edit = { mode: 'remove' }; this.saved = ''; this.renderLimit(); }));
      }
      return box;
    }
    if (edit.mode === 'remove') {
      box.append(element('p', 'Remove the monthly limit? Cloud calls will no longer be limited by month.', 'spending-ask'));
      if (edit.error) box.append(element('p', edit.error, 'spending-error'));
      box.append(this.action(edit.saving ? 'Removing…' : 'Remove', () => void this.save(null), edit.saving),
        this.action('Keep it', () => { this.edit = { mode: 'none' }; this.renderLimit(); }, edit.saving));
      return box;
    }
    const form = element('form', '', 'spending-form');
    const label = element('label', 'Monthly limit, in dollars');
    const input = element('input', '');
    input.type = 'text';
    input.inputMode = 'decimal';
    input.value = edit.draft;
    input.disabled = !!edit.saving;
    input.setAttribute('aria-describedby', 'spending-form-message');
    input.oninput = () => { if (this.edit.mode === 'set' || this.edit.mode === 'change') this.edit.draft = input.value; };
    const currency = element('span', '$', 'spending-currency');
    currency.setAttribute('aria-hidden', 'true'); // the field's name is the label's words alone
    label.append(currency, input);
    form.append(label);
    const message = element('p', edit.error ?? '', edit.error ? 'spending-error' : 'spending-ask');
    message.id = 'spending-form-message';
    form.append(message);
    const save = this.action(edit.saving ? 'Saving…' : 'Save', () => undefined, edit.saving);
    save.type = 'submit';
    form.append(save, this.action('Cancel', () => { this.edit = { mode: 'none' }; this.renderLimit(); }, edit.saving));
    form.onsubmit = event => {
      event.preventDefault();
      const parsed = parseLimit(input.value);
      if ('error' in parsed) {
        this.edit = { mode: edit.mode, draft: input.value, error: parsed.error };
        this.renderLimit();
        this.focusInput();
        return;
      }
      void this.save(parsed.usd);
    };
    box.append(form);
    return box;
  }

  private action(text: string, onclick: () => void, disabled = false): HTMLButtonElement {
    const button = element('button', text, 'spending-action');
    button.type = 'button';
    button.disabled = disabled;
    button.onclick = onclick;
    return button;
  }

  private begin(mode: 'set' | 'change', draft: string): void {
    this.edit = { mode, draft };
    this.saved = '';
    this.renderLimit();
    this.focusInput();
  }

  private focusInput(): void {
    this.limitArea.querySelector<HTMLInputElement>('input')?.focus();
  }

  private async save(usd: number | null): Promise<void> {
    const before = this.edit;
    if (before.mode === 'none' || !this.setCap) return;
    this.edit = { ...before, saving: true, error: undefined } as Edit;
    this.barrier++;  // a read begun before this save may show the old limit: never drawn
    this.renderLimit();
    try {
      const reply = await this.setCap(usd);
      // Confirmed by the app: the limit shown is the one it saved (its own month when it says it), at once, whatever
      // any read still on its way says; the totals are read again after.
      const confirmed = reply as { month?: Spending['month']; says?: unknown } | undefined;
      const month = confirmed?.month;
      const cap = month && 'cap' in month ? month.cap : usd;
      if (this.data) this.data = { ...this.data, month: { ...(this.data.month ?? { month: '', estimatedUsd: 0, unknownCalls: 0,
        inFlightUsd: 0, cap: null }), ...(month ?? {}), cap, entries: this.data.month?.entries } };
      this.barrier++;
      this.edit = { mode: 'none' };
      const says = typeof confirmed?.says === 'string' ? confirmed.says.trim() : '';
      this.saved = says ? `Saved: ${says}` : cap === null ? 'Saved: no monthly limit.' : `Saved: monthly limit ${dollars(cap)}.`;
      this.lastData = '';
      this.renderLimit();
      this.renderAnalytics();
      if (this.open) void this.refresh();
    } catch (error) {
      const why = error instanceof Error && error.message ? error.message : 'it could not be saved';
      this.edit = { ...before, saving: false, error: `Not saved: ${why}. Try again.` } as Edit;
      this.renderLimit();
      if (before.mode !== 'remove') this.focusInput();
    }
  }

  private renderAnalytics(): void {
    const data = this.data;
    if (!data) return;
    const spending = spendingView(data, this.period);
    const view = spending.period;
    const tabs = element('div', '', 'spending-periods');
    tabs.setAttribute('role', 'tablist');
    tabs.setAttribute('aria-label', 'Period');
    for (const [value, text] of [['month', 'This month'], ['today', 'Today']] as const) {
      const tab = element('button', text, 'spending-period');
      tab.type = 'button';
      tab.setAttribute('role', 'tab');
      tab.setAttribute('aria-selected', String(this.period === value));
      tab.onclick = () => { this.period = value; this.renderAnalytics(); };
      tabs.append(tab);
    }
    const expanded = new Set(Array.from(this.analytics.querySelectorAll<HTMLDetailsElement>('details[open]')).map(d => d.dataset.provider));
    const total = element('div', '', 'spending-total');
    const said = element('span', '');
    said.append(element('span', view.qualifier), element('small', view.label));
    total.append(element('strong', view.total), said);
    const nodes: HTMLElement[] = [tabs, total];
    for (const row of view.rows) {
      const group = element('details', '', 'spending-provider');
      group.dataset.provider = row.provider;
      group.open = expanded.has(row.provider);
      const summary = element('summary', '');
      summary.append(element('span', row.provider), element('span', row.total));
      group.append(summary);
      for (const model of row.models) {
        const item = element('div', '', 'spending-model');
        item.append(element('strong', model.model), element('span', model.total), element('small', model.detail));
        group.append(item);
      }
      nodes.push(group);
    }
    nodes.push(...view.notes.map(note => element('p', note, 'spending-note')));
    nodes.push(element('p', view.recordedSince, 'spending-note spending-tracking'));
    this.analytics.replaceChildren(...nodes);
    const supporting = view.details.map(note => element('p', note, 'spending-note'));
    supporting.push(element('h3', 'Optional monthly limit'));
    if (data.month?.cap != null) supporting.push(element('p', spending.used, 'spending-note'));
    else supporting.push(element('p', spending.covers, 'spending-note'));
    if (spending.counted) supporting.push(element('p', spending.counted, 'spending-note'));
    if (data.cap === null) supporting.push(element('p', spending.research, 'spending-note'));
    this.detailsBody.replaceChildren(...supporting);
  }
}
