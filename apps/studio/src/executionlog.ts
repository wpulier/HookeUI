// A UI-only side log. It has no path into ChatModel, transcripts or replay messages.
import type { ExecutionDetail, ExecutionReference } from './protocol';
import { executionContributions } from './executionflow';
import { FailedStepRetry, type RetryRequest } from './operation-retry';

/** What the switch controls and what it keeps (the partner §1f: "turn logging off" must say what it means). */
export const LOGGING_KEEPS = 'Detailed receipts of what each model ran. Off stops new details; previously archived evidence, chat, results and essential provenance are kept.';
let actionNotes = 0;

/** Diagnostic control only: never sends model settings, chat or a tool request. */
export class ExecutionRecordingControl {
  readonly root = document.createElement('fieldset');
  private readonly button = document.createElement('button');
  private readonly state = document.createElement('span');
  private readonly error = document.createElement('p');
  private enabled: boolean | null = null;
  private online = false;
  private pending = false;
  private timeout: ReturnType<typeof setTimeout> | null = null;
  private readonly change: (enabled: boolean) => void;
  private readonly actions = new Set<{ button: HTMLButtonElement; error: HTMLElement; offOnly: boolean }>();

  constructor(change: (enabled: boolean) => void) {
    this.change = change;
    this.root.className = 'execution-recording';
    const legend = document.createElement('legend'); legend.textContent = 'Execution details';
    this.button.type = 'button';
    this.button.className = 'blind-switch';
    this.button.setAttribute('role', 'switch');
    this.button.setAttribute('aria-label', 'Record execution details');
    this.button.setAttribute('aria-describedby', 'execution-recording-help');
    const label = document.createElement('span'); label.textContent = 'Record execution details';
    this.state.className = 'switch-state';
    this.button.append(label, this.state);
    const help = document.createElement('p');
    help.id = 'execution-recording-help';
    help.className = 'option-note';
    help.textContent = LOGGING_KEEPS;
    this.error.setAttribute('role', 'status');
    this.root.append(legend, this.button, help, this.error);
    this.button.addEventListener('click', () => this.request(this.enabled !== true));
    this.render();
  }

  connected(online: boolean): void {
    this.online = online;
    if (!online) {
      this.settled();
      this.enabled = null; // reconnect must receive fresh server state
    }
    this.render();
  }

  apply(enabled: boolean): void {
    this.settled();
    this.enabled = enabled;
    this.error.textContent = '';
    this.render();
  }

  /** Shared state for the persistent on/off button and explicitly opened inspectors. */
  action(offOnly = false): { root: HTMLElement; dispose: () => void } {
    const root = document.createElement('div'); root.className = 'execution-action';
    const button = document.createElement('button'); button.type = 'button';
    button.title = `${LOGGING_KEEPS} Turn on here whenever you need details.`;
    // Said to a screen reader too, not only on hover (a phone has none).
    const note = document.createElement('span');
    note.className = 'visually-hidden';
    note.id = `execution-action-note-${++actionNotes}`;
    note.textContent = LOGGING_KEEPS;
    button.setAttribute('aria-describedby', note.id);
    const error = document.createElement('span'); error.setAttribute('role', 'status');
    root.append(button, note, error);
    const action = { button, error, offOnly };
    this.actions.add(action);
    button.addEventListener('click', () => this.request(offOnly ? false : this.enabled !== true));
    this.render();
    return { root, dispose: () => { this.actions.delete(action); root.remove(); } };
  }

  private settled(): void {
    if (this.timeout !== null) clearTimeout(this.timeout);
    this.timeout = null;
    this.pending = false;
  }

  private request(enabled: boolean): void {
    if (!this.online || this.enabled === null || this.pending || enabled === this.enabled) return;
    this.pending = true;
    this.error.textContent = '';
    this.render();
    this.timeout = setTimeout(() => this.failed(), 5000);
    try {
      this.change(enabled); // only the server's broadcast changes the displayed value
    } catch {
      this.failed();
    }
  }

  private failed(): void {
    this.settled();
    this.error.textContent = 'Recording change was not confirmed. Reconnect or try again.';
    this.render();
  }

  private render(): void {
    this.button.disabled = !this.online || this.enabled === null || this.pending;
    this.button.setAttribute('aria-checked', String(this.enabled === true));
    this.button.setAttribute('aria-busy', String(this.pending));
    this.state.textContent = this.enabled === null ? 'Connecting…' : this.enabled ? 'On' : 'Off';
    for (const action of this.actions) {
      action.button.disabled = this.button.disabled || (action.offOnly && this.enabled === false);
      action.button.setAttribute('aria-busy', String(this.pending));
      action.button.textContent = this.pending ? 'Changing execution logging…'
        : this.enabled === null ? 'Execution logging: Connecting…'
          : action.offOnly ? (this.enabled ? 'Turn execution logging off' : 'Execution logging is off')
            : `Execution logging: ${this.enabled ? 'On · Turn off' : 'Off · Turn on'}`;
      action.error.textContent = this.error.textContent;
    }
  }
}

export class ExecutionStore {
  private readonly entries = new Map<string, ExecutionReference>();
  private readonly limit: number;
  constructor(limit = 256) { this.limit = limit; }
  add(entry: ExecutionReference): void {
    this.entries.set(entry.id, entry);
    while (this.entries.size > this.limit) this.entries.delete(this.entries.keys().next().value!);
  }
  clear(): void { this.entries.clear(); }
  list(thread: string | null, day: string, generation: number | null = null): ExecutionReference[] {
    return [...this.entries.values()].filter(r => r.thread === thread && r.day === day && (generation === null || r.generation === generation));
  }
}

type Load = (id: string, signal: AbortSignal) => Promise<ExecutionDetail>;

/** Explicit opens only. No background refresh or closed-detail cache; a close cancels and
 * discards the response. Reopening sees late results and a completed blind vote. */
export class Inspection {
  detail: ExecutionDetail | null = null;
  loading = false;
  error = false;
  private serial = 0;
  private abort: AbortController | null = null;
  private readonly id: string;
  private readonly load: Load;
  private readonly changed: () => void;
  constructor(id: string, load: Load, changed = () => {}) {
    this.id = id; this.load = load; this.changed = changed;
  }
  async open(): Promise<void> {
    this.close();
    const serial = ++this.serial;
    const abort = this.abort = new AbortController();
    this.loading = true;
    this.error = false;
    this.changed();
    try {
      const detail = await this.load(this.id, abort.signal);
      if (serial === this.serial) this.detail = detail;
    } catch {
      if (serial === this.serial) this.error = true;
    } finally {
      if (serial === this.serial) { this.loading = false; this.changed(); }
    }
  }
  close(): void {
    ++this.serial;
    this.abort?.abort();
    this.abort = null;
    this.detail = null;
    this.loading = false;
  }
}

async function readDetail(id: string, signal: AbortSignal): Promise<ExecutionDetail> {
  const response = await fetch(`/api/execution/${encodeURIComponent(id)}`, { signal, cache: 'no-store' });
  if (!response.ok) throw new Error('inspection unavailable');
  return await response.json() as ExecutionDetail;
}

export class ExecutionDisclosure {
  readonly element = document.createElement('span');
  readonly summary = document.createElement('button');
  private readonly report = document.createElement('button');
  readonly standalone = document.createElement('li');
  private readonly popup = document.createElement('section');
  private readonly content = document.createElement('div');
  private readonly inspection: Inspection;
  private opened = false;
  private pinned = false;
  private context = '';
  private readonly recording?: ExecutionRecordingControl;
  private recordingAction: ReturnType<ExecutionRecordingControl['action']> | null = null;
  private readonly recordingHelp = document.createElement('p');
  private readonly markerId = crypto.randomUUID();
  private markerNote = '';
  private markerSubmitted: string | null = null;
  private markerPending = false;
  private markerSaved = false;
  private markerMessage = '';
  private readonly retry?: FailedStepRetry;
  private readonly receiptId: string;
  constructor(entry: ExecutionReference, recording?: ExecutionRecordingControl, retryRequest?: RetryRequest) {
    this.receiptId = entry.id;
    if (retryRequest) this.retry = new FailedStepRetry(retryRequest, () => { if (this.opened) this.draw(); });
    this.recording = recording;
    this.recordingHelp.className = 'execution-recording-help';
    this.recordingHelp.textContent = 'Off stops logging, not model work. Turn it back on at the top of the chat panel.';
    this.element.className = 'execution-indicator';
    this.summary.type = 'button';
    this.summary.className = 'execution-info';
    this.summary.textContent = 'ⓘ';
    this.summary.setAttribute('aria-label', 'What powers it');
    this.summary.setAttribute('aria-expanded', 'false');
    this.summary.setAttribute('aria-controls', `powers-${entry.id}`);
    this.summary.setAttribute('aria-haspopup', 'dialog');
    this.popup.id = `powers-${entry.id}`;
    this.popup.className = 'execution-popup';
    this.popup.hidden = true;
    this.popup.setAttribute('role', 'dialog');
    this.popup.setAttribute('aria-label', 'What powers it');
    this.standalone.dataset.role = 'execution';
    this.popup.append(this.content);
    this.element.append(this.summary, this.popup);
    this.report.type = 'button'; this.report.className = 'activity-report mm-control'; this.report.textContent = 'Report a problem';
    this.report.addEventListener('click', () => { this.pinned = true; this.show(); });
    this.outcome(entry.outcome);
    this.inspection = new Inspection(entry.id, readDetail, () => this.draw());
    this.element.addEventListener('pointerenter', () => this.show());
    this.element.addEventListener('pointerleave', () => {
      if (!this.pinned && !this.element.contains(document.activeElement)) this.hide();
    });
    this.element.addEventListener('focusin', () => this.show());
    this.element.addEventListener('focusout', (event) => {
      if (!this.pinned && !this.element.contains(event.relatedTarget as Node | null)) this.hide();
    });
    this.summary.addEventListener('click', () => {
      this.pinned = !this.pinned;
      if (this.pinned) this.show(); else this.hide();
    });
    this.element.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.opened) {
        // Restore focus before closing: its bubbling focusin must see an already-open inspector.
        this.summary.focus({ preventScroll: true });
        this.hide();
        event.preventDefault(); event.stopPropagation();
      }
    });
  }
  outcome(value: string): void {
    if (value === 'failed' || value === 'cancelled') {
      if (this.report.parentElement !== this.element) this.element.append(this.report);
    } else this.report.remove();
  }
  label(context: string): void {
    this.context = context;
    this.summary.setAttribute('aria-label', `What powers it${context ? ` · ${context}` : ''}`);
  }
  private show(): void {
    if (this.opened) return;
    this.opened = true;
    this.popup.hidden = false;
    this.summary.setAttribute('aria-expanded', 'true');
    if (this.recording) {
      this.recordingAction = this.recording.action(true);
      this.popup.insertBefore(this.recordingAction.root, this.content);
      this.popup.insertBefore(this.recordingHelp, this.content);
    }
    // Measured only on deliberate inspection; never on frames or receipt arrivals.
    if (typeof window !== 'undefined') {
      const box = this.summary.getBoundingClientRect();
      const width = Math.min(360, window.innerWidth - 24);
      this.popup.style.width = `${width}px`;
      this.popup.style.left = `${Math.max(12, Math.min(box.right - width, window.innerWidth - width - 12))}px`;
      const above = box.top > window.innerHeight / 2;
      this.popup.style.top = above ? 'auto' : `${box.bottom}px`;
      this.popup.style.bottom = above ? `${window.innerHeight - box.top}px` : 'auto';
      this.popup.style.maxHeight = `${Math.max(80, Math.min(340, (above ? box.top : window.innerHeight - box.bottom) - 12))}px`;
    }
    void this.inspection.open();
    void this.retry?.load(this.receiptId);
  }
  private hide(): void {
    this.opened = this.pinned = false;
    this.popup.hidden = true;
    this.summary.setAttribute('aria-expanded', 'false');
    this.inspection.close();
    this.retry?.close();
    this.recordingAction?.dispose();
    this.recordingAction = null;
    this.recordingHelp.remove();
    this.content.replaceChildren();
  }
  dispose(): void {
    this.hide();
    this.element.remove();
    this.standalone.remove();
  }
  private draw(): void {
    this.content.replaceChildren();
    const heading = document.createElement('strong');
    heading.textContent = 'What powers it';
    this.content.append(heading);
    if (this.context) {
      const context = document.createElement('p'); context.textContent = this.context; this.content.append(context);
    }
    const data = this.inspection.detail;
    const state = document.createElement('p');
    state.textContent = this.inspection.loading ? 'Loading execution details…' : this.inspection.error
      ? 'Details unavailable. Close and reopen to retry.' : data?.status === 'blind'
        ? 'Model identities are hidden until this comparison is voted on.' : data?.status === 'expired'
          ? 'This receipt is unavailable in memory and the retained archive.' : '';
    if (state.textContent) { this.content.append(state); return; }
    if (!data) return;
    if (this.retry && (data.root?.outcome === 'failed' || data.steps.some(step => step.outcome === 'failed') || this.retry.rows.length)) {
      const retryStatus = document.createElement('p'); retryStatus.setAttribute('role', 'status');
      retryStatus.textContent = this.retry.message;
      for (const row of this.retry.rows) {
        const control = document.createElement('button'); control.type = 'button'; control.className = 'mm-control';
        control.textContent = row.tool === 'reference_photo' ? 'Retry photo search' : 'Retry window control';
        control.disabled = !row.available || this.retry.pending;
        control.addEventListener('click', () => void this.retry?.retry(row.actionId));
        this.content.append(control);
        if (row.reason) { const reason = document.createElement('p'); reason.textContent = row.reason; this.content.append(reason); }
      }
      this.content.append(retryStatus);
    }
    if (data.root?.id) {
      const retainedMarker = data.markers?.[0];
      if (retainedMarker && !this.markerSaved) {
        this.markerSubmitted = retainedMarker.note;
        this.markerSaved = true;
        this.markerMessage = 'Problem saved locally · ' + new Date(retainedMarker.at * 1000).toISOString()
          + '. This did not start recording; audio/video links are not yet available here.';
      }
      const mark = document.createElement('button'); mark.type = 'button'; mark.textContent = 'Mark this problem';
      const saved = document.createElement('p'); saved.setAttribute('role', 'status');
      const note = document.createElement('input'); note.type = 'text'; note.maxLength = 2000;
      note.placeholder = 'Optional note'; note.setAttribute('aria-label', 'Problem note');
      note.value = this.markerSubmitted ?? this.markerNote;
      note.disabled = this.markerSubmitted !== null;
      note.addEventListener('input', () => { this.markerNote = note.value; });
      mark.disabled = this.markerPending || this.markerSaved;
      saved.textContent = this.markerMessage || data.markerError || '';
      mark.addEventListener('click', async () => {
        this.markerSubmitted ??= note.value;
        this.markerPending = true;
        this.markerMessage = 'Preserving available execution evidence…';
        this.draw();
        try {
          const response = await fetch(`/api/execution/${encodeURIComponent(data.root!.id)}/mark`, {
            method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'mark-problem' },
            body: JSON.stringify({ id: this.markerId, note: this.markerSubmitted }),
          });
          if (!response.ok) throw new Error('marker unavailable');
          this.markerMessage = 'Execution evidence preserved locally. This did not start recording; audio/video links are not yet available here.';
          this.markerSaved = true;
        } catch {
          this.markerMessage = 'Could not preserve this problem. Retry when the local archive is available.';
        } finally {
          this.markerPending = false;
          this.draw();
        }
      });
      this.content.append(note, mark, saved);
    }
    const facts = [data.context?.words ? `Request: ${data.context.words}` : 'Original words are unavailable in this receipt.',
      data.root?.at ? `Started: ${new Date(data.root.at * 1000).toISOString()}` : '',
      data.context?.owner?.workspaceId ? `Browser workspace: ${data.context.owner.workspaceId}` : 'Requesting-browser identity is unavailable.',
      data.context?.build?.revision ? `Build: ${data.context.build.revision.slice(0, 12)}${data.context.build.dirty ? ' · local changes' : ''} · ${data.context.build.sourceDigest?.slice(0, 12) ?? 'source digest unavailable'}` : 'Build identity was not retained.',
      data.context?.settings ? `Model settings: ${Object.entries(data.context.settings).filter(([, value]) => value != null).map(([key, value]) => `${key}: ${value}`).join(', ')}` : '',
      data.durable ? 'Recovered from the local execution archive.' : '',
      data.interrupted ? 'The original process ended without a retained terminal outcome.' : '',
      'This execution receipt is not a screen or audio recording. Rendered-delivery evidence is tracked separately.'];
    for (const fact of facts.filter(Boolean)) {
      const line = document.createElement('p'); line.textContent = fact; this.content.append(line);
    }
    if (data.archive && (data.archive.dropped || data.archive.writeErrors || data.archive.recoveryGaps)) {
      const gap = document.createElement('p');
      gap.textContent = `Archive gaps: ${data.archive.dropped} dropped events, ${data.archive.writeErrors} write errors, ${data.archive.recoveryGaps} recovery gaps.`;
      this.content.append(gap);
    }
    if (data.root?.incomplete) {
      const partial = document.createElement('p');
      partial.textContent = 'Some execution details were not retained.';
      this.content.append(partial);
    }
    const ordered = data.steps.length > 0;
    if (ordered) {
      const order = document.createElement('p');
      order.textContent = 'Started in this order; steps may overlap.';
      this.content.append(order);
    }
    const powers = document.createElement(ordered ? 'ol' : 'ul');
    powers.className = 'execution-powers';
    for (const power of executionContributions(data)) {
      const item = document.createElement('li');
      const name = document.createElement('strong'); name.textContent = power.capability;
      const outcome = power.outcome === 'completed' ? '' : ` · ${power.outcome}`;
      const result = power.provider === 'app' && power.outcome === 'failed' ? 'failed' : `${power.label}${outcome}`;
      item.append(name, ` — ${result}${power.cache && power.outcome !== 'reused' ? ' · reused' : ''}`);
      if (power.objectId || power.note) {
        const context = document.createElement('small');
        context.className = 'execution-job-context';
        context.textContent = [power.objectId ? `Outline ${power.objectId}` : '', power.note].filter(Boolean).join(' · ');
        item.append(context);
      }
      powers.append(item);
    }
    if (!powers.children.length) {
      const missing = document.createElement('li'); missing.textContent = 'No model identity recorded for this action.'; powers.append(missing);
    }
    this.content.append(powers);
    const technical = document.createElement('details');
    const summary = document.createElement('summary'); summary.textContent = 'Execution details';
    technical.append(summary);
    const context = document.createElement('p');
    context.textContent = `Receipt ${data.root?.id ?? ''} · ${data.root?.outcome ?? ''}${data.root?.incomplete ? ' · partial recording' : ''}`;
    technical.append(context);
    if (data.root?.id) {
      const exported = document.createElement('a');
      exported.href = `/api/execution/${encodeURIComponent(data.root.id)}/export`;
      exported.download = 'execution-redacted.json';
      exported.textContent = 'Download redacted diagnostics';
      technical.append(exported);
      const copy = document.createElement('button');
      copy.type = 'button'; copy.textContent = 'Copy diagnostics';
      copy.title = 'Copy a redacted receipt: build, stages and timing; no conversation, media, credentials or source addresses.';
      copy.addEventListener('click', async () => {
        copy.disabled = true; copy.textContent = 'Preparing diagnostics…';
        try {
          const response = await fetch(exported.href, {cache: 'no-store'});
          if (!response.ok) throw new Error('Diagnostics are unavailable');
          const diagnostics = await response.json();
          if (diagnostics.redacted !== true) throw new Error('Redacted diagnostics are unavailable');
          await navigator.clipboard.writeText(JSON.stringify(diagnostics, null, 2));
          copy.textContent = 'Diagnostics copied';
        } catch {
          copy.textContent = 'Copy unavailable — use Download';
        } finally { copy.disabled = false; }
      });
      technical.append(copy);
    }
    const table = document.createElement('table');
    const headings = document.createElement('tr');
    for (const text of ['Step', 'Selected', 'Executed', 'Result']) {
      const th = document.createElement('th'); th.textContent = text; headings.append(th);
    }
    table.append(headings);
    const numbers = new Map(data.steps.slice(0, 64).map((step, i) => [step.id, i + 1]));
    for (const step of data.steps.slice(0, 64)) {
      const tr = document.createElement('tr');
      const executed = [step.reportedModel ?? step.model, step.provider, step.adapter, step.endpoint,
        step.device, step.worker, step.identity, step.cache].filter(Boolean).join('\n') || '—';
      const result = [step.outcome, step.ms !== undefined ? `${step.ms} ms` : null,
        step.adapterMs !== undefined ? `Worker reported: ${step.adapterMs} ms` : null,
        step.producer ? `Retained producer: step ${numbers.get(step.producer) ?? step.producer}` : null,
        step.fallback ? `Fallback: ${step.fallback}` : null, step.policy,
        step.frameId != null ? `Frame ${step.frameId}` : null,
        step.rpcId != null ? `RPC ${step.rpcId}` : null, step.arm ? `Arm ${step.arm}` : null,
        step.objectId ? `Outline ${step.objectId}` : null,
        step.sessionId ? `Live session ${step.sessionId}` : null,
        step.errorType].filter(Boolean).join('\n');
      const parent = numbers.get(step.parent);
      for (const text of [`${numbers.get(step.id)}. ${step.operation}${step.scope ? ` (${step.scope})` : ''}${parent ? ` · parent ${parent}` : ''}`,
        [step.selected, step.selection].filter(Boolean).join('\n') || '—', executed, result]) {
        const td = document.createElement('td'); td.textContent = text; tr.append(td);
      }
      table.append(tr);
    }
    technical.append(table);
    this.content.append(technical);
    const refresh = document.createElement('button');
    refresh.type = 'button'; refresh.textContent = 'Refresh details';
    refresh.addEventListener('click', () => void this.inspection.open());
    this.content.append(refresh);
  }
}

/** Places a small indicator beside the exact turn, or in a UI-only row for a
 * click/voice session. Never guesses that an uncorrelated event belongs to the last answer. */
export class ExecutionLogView {
  readonly store = new ExecutionStore();
  private readonly disclosures = new Map<string, ExecutionDisclosure>();
  private readonly root: HTMLOListElement;
  private readonly recording?: ExecutionRecordingControl;
  private readonly retryRequest?: RetryRequest;
  day = '';
  generation: number | null = null;
  constructor(root: HTMLOListElement, recording?: ExecutionRecordingControl, retryRequest?: RetryRequest) {
    this.root = root; this.recording = recording; this.retryRequest = retryRequest;
  }
  clear(reset = false): void {
    for (const d of this.disclosures.values()) d.dispose();
    this.disclosures.clear();
    if (reset) this.store.clear();
  }
  /** `turns`: each request's Activity heading by its turn; `objects`: each click's Activity heading by the outline it
   * made (a click's receipt goes beside its own line, not in a row of its own). */
  sync(thread: string | null, turns: Map<number, HTMLElement>, objects: Map<string, HTMLElement> = new Map()): void {
    const entries = this.store.list(thread, this.day, this.generation);
    const kept = new Set(entries.map(r => r.id));
    for (const [id, d] of this.disclosures) if (!kept.has(id)) { d.dispose(); this.disclosures.delete(id); }
    for (const entry of entries) {
      const target = entry.turnId == null
        ? (entry.kind === 'click' ? entry.objectIds.map(id => objects.get(id)).find(Boolean) : undefined)
        : turns.get(entry.turnId);
      // Background work and uncorrelated speech do not create chat activity. A late
      // request gets its indicator when its exact answer arrives.
      if (!target && !['click', 'cells', 'tool', 'operation-retry'].includes(entry.kind)) continue;
      let d = this.disclosures.get(entry.id);
      if (!d) { d = new ExecutionDisclosure(entry, this.recording, this.retryRequest); this.disclosures.set(entry.id, d); }
      d.outcome(entry.outcome);
      const at = new Date(entry.at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      if (target) {
        d.label('');
        if (d.element.parentElement !== target) { d.standalone.remove(); target.append(d.element); }
      } else {
        const what = entry.kind === 'operation-retry' ? 'Retried step' : entry.kind === 'ask' ? 'Request' : entry.kind === 'voice' ? 'Spoken request' : entry.kind === 'click' ? 'Click' : entry.kind;
        d.label(`${what} · ${at}${entry.objectIds.length ? ` · ${entry.objectIds.join(', ')}` : ''}`);
        if (d.element.parentElement !== d.standalone) d.standalone.append(d.element);
        if (!d.standalone.parentElement) this.root.append(d.standalone);
      }
    }
  }
}
