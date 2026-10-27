/** Private kernel controls. The workspace owner supplies execution and persistence authority. */
import { ScientificScriptDrafts, type ScriptDraftBinding } from './scientific-script-drafts';
import { ScientificScripts, SCRIPT_BYTES } from './scientific-scripts';
import { ScientificPythonHelp, type PythonHelpMode } from './scientific-python-help';
import { ScientificNotebook, type NotebookTransport } from './scientific-notebook';
export type PythonTranscript = {
  format: 'scientific-console-v1'; sequence: number; stdout: string; stderr: string;
  result: string; error: string; truncated: boolean; privatePreview: true;
};
export type PythonCell = { source: string; transcript: PythonTranscript; session: string };
/** The owner refused the cell before sending it (for example while a view change was still drawing): nothing ran. */
export class PythonNotSent extends Error {}
type Transport = {
  draft?: ScriptDraftBinding;
  execute(source: string): Promise<unknown>;
  stop(): Promise<void>;
  interrupt?(): Promise<boolean>;
  assist?(mode: PythonHelpMode, expression: string): Promise<unknown>;
  notebook?: NotebookTransport;
};
const bytes = (value: string) => new TextEncoder().encode(value).length;
const sourceLimit = SCRIPT_BYTES;
const transcriptLimit = 16 << 10;
const historyLimit = 1 << 20;
const sessionCellLimit = 128; // Mirrors scientific_history.MAX_CELLS; server admission remains authoritative.

export function pythonTranscript(raw: unknown): PythonTranscript {
  const fail = (): never => { throw new Error('Python did not return a valid bounded transcript.'); };
  if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) return fail();
  const value = raw as Record<string, unknown>;
  if (Object.keys(value).sort().join(',') !== 'error,format,privatePreview,result,sequence,stderr,stdout,truncated' ||
      value.format !== 'scientific-console-v1' || value.privatePreview !== true || typeof value.truncated !== 'boolean' ||
      !Number.isSafeInteger(value.sequence) || Number(value.sequence) < 1) return fail();
  for (const field of ['stdout', 'stderr', 'result', 'error'])
    if (typeof value[field] !== 'string' || bytes(value[field]) > transcriptLimit) return fail();
  return structuredClone(value) as PythonTranscript;
}

/** History belongs to the console; sequence numbers belong to a kernel incarnation. */
export class ScientificConsole {
  private readonly root: HTMLElement;
  private readonly transport: Transport;
  private readonly input = document.createElement('textarea');
  private readonly run = document.createElement('button');
  private readonly stop = document.createElement('button');
  private readonly interrupt = document.createElement('button');
  private interrupting = false;
  private readonly status = document.createElement('p');
  private readonly history = document.createElement('div');
  private readonly scripts: ScientificScripts;
  private readonly drafts: ScientificScriptDrafts | null;
  private readonly help: ScientificPythonHelp | null;
  private readonly notebook: ScientificNotebook | null;
  private readonly cells: PythonCell[] = [];
  private busy = false;
  private available = true;
  private stopped = false;
  private stopping = false;
  private uncertain = false;
  private closed = false;
  private epoch = 0;
  private lastSequence = 0;
  private session = 'initial';
  private historyBytes = 0;
  private pending: { source: string; state: 'running' | 'unknown' | 'stopped' } | null = null;

  constructor(root: HTMLElement, transport: Transport) {
    this.root = root; this.transport = transport;
    const note = document.createElement('p');
    note.textContent = 'Python uses this private scientific view. Running code does not automatically save array changes to Project Data.';
    this.input.setAttribute('aria-label', 'Python code');
    this.input.spellcheck = false;
    this.input.maxLength = sourceLimit;
    this.input.addEventListener('input', () => { this.drafts?.edit(); this.paint(); });
    this.run.textContent = 'Run code';
    this.run.addEventListener('click', () => { if (!this.run.disabled) void this.execute(); });
    this.interrupt.textContent = 'Interrupt code';
    this.interrupt.addEventListener('click', () => { if (!this.interrupt.disabled) void this.interruptCode(); });
    this.stop.textContent = 'Stop Python session';
    this.stop.title = 'Stop only this session. Private variables and unsaved view changes will be lost; saved Project data stays.';
    this.stop.addEventListener('click', () => { if (!this.stop.disabled) void this.stopSession(); });
    this.status.setAttribute('role', 'status');
    this.history.setAttribute('aria-label', 'Python cell history');
    const stopHelp = document.createElement('p');
    stopHelp.textContent = 'Interrupt asks the running cell to stop while keeping its variables and partial changes. Some native calls cannot respond until they return. Stop ends this private Python session and discards its unsaved changes. Saved Project data stays.';
    this.scripts = new ScientificScripts(this.input, () => { this.drafts?.edit(); this.paint(); });
    this.drafts = transport.draft ? new ScientificScriptDrafts(this.input, transport.draft, () => this.input.dispatchEvent(new Event('input', {bubbles:true}))) : null;
    this.help = transport.assist ? new ScientificPythonHelp(this.input, transport.assist) : null;
    this.notebook = transport.notebook ? new ScientificNotebook(transport.notebook) : null;
    root.append(note, this.history, this.scripts.element, this.input);
    if(this.drafts)root.append(this.drafts.element);
    if (this.help) root.append(this.help.element);
    if (this.notebook) root.append(this.notebook.element);
    root.append(this.run, this.interrupt, this.stop, stopHelp, this.status);
    this.paint();
  }

  /** The owner can retain/export these records; this component never persists or executes them on reopen. */
  snapshot() {
    return { cells: structuredClone(this.cells), pending: structuredClone(this.pending),
      draft: this.input.value, stopped: this.stopped, uncertain: this.uncertain };
  }

  setAvailable(available: boolean): void { this.available = available; this.paint(); }

  bindSession(session: string): void {
    if (this.closed || !/^[A-Za-z0-9_-]{1,128}$/.test(session)) throw new Error('Python needs its owning scientific session.');
    if (this.session === session) return;
    this.session = session; ++this.epoch; this.lastSequence = 0;
    this.notebook?.reset();
    this.help?.reset();
    this.busy = this.stopped = this.stopping = this.uncertain = this.interrupting = false;
    this.pending = null;
    this.status.textContent = this.cells.length ? 'New private Python session. Earlier cells remain below; nothing was replayed.' : 'Private Python session ready. Code runs only when you press Run code.';
    this.paint();
  }

  /** Owner verified the exact pending request against its durable admission outcome. */
  notDispatched(session: string, source: string): void {
    if (this.closed || this.busy || this.stopped || session !== this.session ||
        !this.uncertain || this.pending?.source !== source) return;
    this.pending = null; this.uncertain = false;
    this.status.textContent = 'This code was not run. Your draft and earlier private changes are kept. Run code starts a new request.';
    this.paint();
  }

  sessionStopped(session: string): void {
    if (session !== this.session) return;
    this.notebook?.reset();
    ++this.epoch; this.stopped = true; this.stopping = this.busy = this.uncertain = this.interrupting = false;
    this.help?.reset();
    if (this.pending) this.pending.state = 'stopped';
    if (this.closed) return;
    this.status.textContent = 'Python session stopped. Saved Project data stays. The transcript remains available in this open console. A new session starts without its private variables.';
    this.paint();
  }

  /** Disposing presentation does not cancel work. Only the explicit Stop action does. */
  close(): void { this.closed = true; this.scripts.close(); this.drafts?.close(); this.help?.close(); this.root.replaceChildren(); }

  private sessionFull(): boolean {
    return this.cells.filter(cell => cell.session === this.session).length >= sessionCellLimit;
  }

  private paint(): void {
    if (this.closed) return;
    const valid = this.input.value.trim().length > 0 && bytes(this.input.value) <= sourceLimit;
    this.run.disabled = !this.available || this.busy || this.stopped || this.uncertain || !valid || this.historyBytes >= historyLimit || this.sessionFull();
    this.stop.disabled = this.stopped || this.stopping;
    this.interrupt.hidden = !this.transport.interrupt;
    this.interrupt.disabled = !this.busy || this.interrupting || this.stopped || this.stopping;
    this.root.setAttribute('aria-busy', String(this.busy || this.stopping));
    this.input.setAttribute('aria-invalid', String(bytes(this.input.value) > sourceLimit));
    this.help?.setAvailable(this.available && !this.busy && !this.stopped && !this.stopping && !this.uncertain);
    this.notebook?.setAvailable(this.available && !this.stopped && !this.stopping);
    // Keep the input element and draft intact while execution or worker updates arrive.
  }

  private async execute(): Promise<void> {
    if (!this.available || this.busy || this.stopped || this.uncertain || this.closed || this.historyBytes >= historyLimit || this.sessionFull()) return;
    const source = this.input.value;
    if (!source.trim() || bytes(source) > sourceLimit) return;
    this.help?.reset();
    const epoch = this.epoch, session = this.session, previousSequence = this.lastSequence;
    const pending = { source, state: 'running' as const };
    this.pending = pending;
    this.busy = true; this.status.textContent = 'Running Python…'; this.paint();
    try {
      const transcript = pythonTranscript(await this.transport.execute(source));
      if (transcript.sequence <= previousSequence) throw new Error('Python returned an old cell result.');
      if (epoch === this.epoch) this.lastSequence = transcript.sequence;
      if (this.pending === pending) this.pending = null;
      const cell = { source, transcript, session };
      this.cells.push(cell); this.historyBytes += bytes(JSON.stringify(cell));
      // A hidden/disposed console still owns its result record. Only painting ends.
      if (this.closed) return;
      const record = document.createElement('section');
      const heading = document.createElement('h4'); heading.textContent = `Cell ${transcript.sequence}${session !== this.session ? ' (earlier session)' : ''}`;
      const code = document.createElement('pre'); code.textContent = source;
      record.append(heading, code);
      for (const field of ['stdout', 'stderr', 'result', 'error'] as const) if (transcript[field]) {
        const output = document.createElement('pre');
        output.setAttribute('aria-label', field); output.textContent = transcript[field]; record.append(output);
      }
      if (transcript.truncated) { const note = document.createElement('p'); note.textContent = 'Output was shortened to its display limit.'; record.append(note); }
      this.history.append(record);
      if (epoch !== this.epoch) return; // A completed Stop owns the status and controls.
      this.status.textContent = transcript.error.startsWith('KeyboardInterrupt:') ? 'Python interrupted. Variables and partial changes remain in this private session; inspect them before continuing.' : transcript.error ? 'Python reported an error. Inspect the private view and correct your code.' : 'Finished. Changes remain in the private view.';
      if (this.sessionFull()) this.status.textContent += ` This session reached ${sessionCellLimit} cells. Keep the transcript and save accepted Data before starting a new scientific session. Your draft is kept.`;
      if (this.historyBytes >= historyLimit) this.status.textContent += ' This console reached its history limit. Keep the transcript before opening another session.';
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (error instanceof PythonNotSent) {
        if (this.pending === pending) this.pending = null;
        if (!this.closed) this.status.textContent = `${error.message} This code was not run; your draft is kept. Run code again when the view is ready.`;
        return;
      }
      this.uncertain = true;
      if (this.pending) this.pending.state = 'unknown';
      if (this.closed) return;
      this.status.textContent = 'No confirmed Python result arrived. The code may have run. Your draft is kept. Stop this session before starting another; nothing is rerun automatically.';
    } finally {
      if (epoch === this.epoch) { this.busy = false; this.paint(); }
    }
  }

  private async interruptCode(): Promise<void> {
    if (!this.transport.interrupt || !this.busy || this.interrupting || this.stopped || this.stopping || this.closed) return;
    const epoch = this.epoch, pending = this.pending;
    this.interrupting = true; this.status.textContent = 'Requesting interruption of this Python cell…'; this.paint();
    try {
      const requested = await this.transport.interrupt();
      if (this.closed || epoch !== this.epoch || this.pending !== pending || !this.busy) return;
      this.status.textContent = requested
        ? 'Interrupt requested. Waiting for Python to respond; native code may need to return first. Stop Python session ends it and discards unsaved work.'
        : 'That cell is not running yet or has already finished. Wait for its result, or retry Interrupt if it is still running.';
    } catch {
      if (!this.closed && epoch === this.epoch && this.pending === pending && this.busy)
        this.status.textContent = 'Interruption was not confirmed. You can retry Interrupt; Stop Python session discards unsaved work.';
    } finally { if (epoch === this.epoch) { this.interrupting = false; this.paint(); } }
  }

  private async stopSession(): Promise<void> {
    if (this.closed || this.stopped || this.stopping) return;
    const epoch = ++this.epoch, session = this.session; this.stopping = true; this.busy = false; this.uncertain = true;
    this.help?.reset();
    if (this.pending) this.pending.state = 'unknown';
    this.status.textContent = 'Stopping this Python session…'; this.paint();
    try {
      await this.transport.stop();
      if (epoch === this.epoch) this.sessionStopped(session);
    } catch {
      if (this.closed || epoch !== this.epoch) return;
      this.status.textContent = 'The session has not confirmed it stopped. Retry Stop; your draft and transcript are kept.';
    } finally { if (epoch === this.epoch) { this.stopping = false; this.paint(); } }
  }
}
