// One optional guide over ordinary sessions, source playback, analysis and measurements.
// Persistent progress belongs to the server. No timers, settings changes or simulated results.
import type { AssetMeta } from './protocol';
import type { MeasurementPin } from './measurements';

export interface OnboardingState {
  version: 1;
  status: 'idle' | 'active' | 'dismissed' | 'complete';
  step: 'analyze' | 'measurements' | 'return' | 'complete';
  session: string | null;
  asset: AssetMeta | null;
  result: { id: string; revision: number } | null;
  localOnly: true;
}
export type GuideAction = 'status' | 'start' | 'restart' | 'analyze' | 'measurements' | 'finish' | 'dismiss';
export interface GuideRender { session: string; id: string; revision: number }
interface Reply { ok: boolean; error?: string | null; onboarding: OnboardingState; rendered?: GuideRender | null }
export interface ExampleGuideDeps {
  act(action: GuideAction, identity?: { session: string; result?: { id: string; revision: number } }): Promise<Reply>;
  session(): string;
  /** Source/session are already opened by the server. Bring the ordinary workspace into view. */
  specimen(): void;
  measurements(id: string, pin: MeasurementPin): void;
  addFiles(): void;
  capabilities(): void;
  visible?(visible: boolean): void;
  /** Whether the Toolbox is in view: a finished walkthrough's bar steps aside there (its "Explore capabilities" is done). */
  onToolbox?(): boolean;
  /** Its example's opening (start, restart) has its reply: `ok`, the server's session with the example on stage; not, a
   * refusal (the session it opened, if any, is the studio's to land as any new one). */
  opened?(ok: boolean): void;
}
/** A refusal in the user's words: one that names a file or the app's insides ("the camera bridge could not play it:
 * /…/human_mitosis.png is not in a folder…", seen 2026-10-15) is said plainly; its whole text stays in the tooltip. */
export function plainProblem(text: string): string {
  return /\/(?:Users|private|tmp|var|home)\/|camera bridge|Traceback|\b\w+Error\b/.test(text)
    ? 'The example could not be opened. Try again, or skip the walkthrough.' : text;
}

/** The example's Project, once the server has said it: it stays on this Mac (`localOnly`), so the workspace can say
 * the Agent and cloud models are off there before a question is refused. Earlier example Projects kept by Restart
 * walkthrough are not known here. */
let localExample: string | null = null;
export function exampleProject(): string | null { return localExample; }
function announce(session: string | null): void {
  if (session === localExample) return;
  localExample = session;
  if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('example-project', { detail: { session, localOnly: true } }));
}

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className: string, text = ''): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag); el.className = className; el.textContent = text; return el;
}

export class ExampleGuide {
  readonly root: HTMLElement;
  private state: OnboardingState | null = null;
  private busy: GuideAction | null = null;
  private problem = '';
  private retry: (() => Promise<void>) | null = null;
  private connected = true;
  private serial = 0;
  private completion = false;
  private lastSession = '';
  private locallyHidden = false;
  private skipOpening: number | null = null;
  private waitingForDrawing = false;
  private drawn: { id: string; revision: number; session: string } | null = null;
  private openedFor: string | null = null;
  /** The step whose failure `problem` is (only an opening's is said as "could not be opened"). */
  private problemAction: GuideAction | null = null;
  private readonly progress = node('span', 'example-progress');
  private readonly words = node('p', 'example-words');
  private readonly status = node('p', 'example-status');
  private readonly primary = node('button', 'example-primary mm-primary');
  private readonly secondary = node('button', 'example-secondary', 'Explore capabilities');
  private readonly skip = node('button', 'example-skip', 'Skip walkthrough');
  private readonly more = node('details', 'example-more');
  private readonly restart = node('button', 'example-restart', 'Restart walkthrough');

  private readonly deps: ExampleGuideDeps;

  constructor(root: HTMLElement, deps: ExampleGuideDeps) {
    this.deps = deps;
    this.root = root;
    root.classList.add('example-guide'); root.setAttribute('aria-label', 'Example walkthrough');
    const text = node('div', 'example-text'), controls = node('div', 'example-controls');
    text.append(this.progress, this.words, this.status);
    this.status.setAttribute('role', 'status');
    this.words.setAttribute('aria-live', 'polite');
    this.words.setAttribute('aria-atomic', 'true');
    for (const button of [this.primary, this.secondary, this.skip, this.restart]) button.type = 'button';
    this.primary.addEventListener('click', () => { void this.next(); });
    this.secondary.addEventListener('click', () => this.deps.capabilities());
    this.skip.addEventListener('click', () => { void this.dismiss(); });
    this.restart.title = 'Start a new example project. Your previous image and results stay saved.';
    this.restart.addEventListener('click', () => { this.more.open = false; void this.start(true); });
    const more = node('summary', '', '⋯');
    more.setAttribute('aria-label', 'More walkthrough options'); more.title = 'More walkthrough options';
    this.more.append(more, this.restart);
    controls.append(this.primary, this.secondary, this.skip, this.more);
    root.append(text, controls); this.draw();
  }

  /** Opening its example now (start, restart): the server opens the session and puts the example on stage itself. */
  opening(): boolean {
    return this.busy === 'start' || this.busy === 'restart';
  }

  /** Whether `session` is the one its last opening made, its reply in already (the server's `session-cleared` for it
   * came after the reply); asked once: true only the first time. */
  openedNow(session: string): boolean {
    const mine = this.openedFor === session;
    if (mine) this.openedFor = null;
    return mine;
  }

  async refresh(): Promise<void> {
    const serial = this.serial;
    try {
      const reply = await this.deps.act('status');
      if (serial !== this.serial || this.busy || !reply.ok) return;
      this.state = reply.onboarding;
      if (this.locallyHidden && this.state.status === 'active' && this.state.session === this.deps.session()) {
        await this.dismiss(); return;
      }
      if (this.state.status === 'active' && this.state.step === 'measurements')
        this.waitingForDrawing = !this.drawn || !this.matches(this.drawn);
      this.draw();
    } catch { /* reconnect will retry; no automatic takeover of the workspace */ }
  }

  /** An agent ran the example through Hooke's door (ADR 0026): read where the walkthrough is and, when its result is
   *  kept but this window hasn't drawn it through the guide, show it as Retry would, so the card moves on. */
  async agentAdvanced(): Promise<void> {
    await this.refresh();
    if (!this.busy && this.waitingForDrawing && this.state?.status === 'active' && this.state.session === this.deps.session())
      await this.run('start');
  }

  sessionChanged(): void {
    if (this.lastSession === this.deps.session()) return;
    this.lastSession = this.deps.session();
    this.drawn = null;
    this.completion = false;
    this.draw();
    void this.refresh();
  }

  connectionChanged(connected: boolean): void {
    this.connected = connected;
    if (!connected) this.drawn = null;
    this.draw();
    if (connected) void this.refresh();
  }

  async start(restart = false): Promise<void> {
    if (this.busy) return;
    this.completion = false; this.waitingForDrawing = false; this.locallyHidden = false;
    await this.run(restart ? 'restart' : 'start');
  }

  async measurementsLoaded(identity: { id: string; revision: number; session: string }): Promise<void> {
    if (this.busy || this.waitingForDrawing || this.state?.status !== 'active' || this.state.step !== 'measurements' || !this.matches(identity)) return;
    await this.run('measurements');
  }

  private matches(identity: { id: string; revision: number; session: string }): boolean {
    return this.state?.session === this.deps.session() && this.state.session === identity.session
      && this.state.result?.id === identity.id && this.state.result.revision === identity.revision;
  }

  private async next(): Promise<void> {
    if (this.busy || !this.connected) return;
    if (this.retry) { await this.retry(); return; }
    if (!this.state || this.state.session !== this.deps.session()) return;
    if (this.completion) { this.deps.addFiles(); return; }
    if (this.waitingForDrawing) { await this.run('start'); return; }
    if (this.state.step === 'analyze') {
      // Waiting for the outlines is decided from the reply and its render receipt (run, done), never before: set while
      // the analysis ran, the bar read "Finding bright regions… Retry", also for the seconds after the outlines were
      // drawn while the server kept the result and confirmed its pixels (the post-merge P3 on #312).
      await this.run('analyze', () => {
        this.waitingForDrawing = !this.drawn || !this.matches(this.drawn);
      });
    } else if (this.state.step === 'measurements' && this.state.result && !this.waitingForDrawing) {
      this.deps.measurements(this.state.result.id, { session: this.state.session!, revision: this.state.result.revision });
    } else if (this.state.step === 'return') {
      this.deps.specimen();
      await this.run('finish', () => { this.completion = true; });
    }
    this.draw();
  }

  private async dismiss(): Promise<void> {
    if (this.busy === 'start' || this.busy === 'restart') {
      this.skipOpening = this.serial; this.locallyHidden = true; this.draw(); return;
    }
    this.locallyHidden = true;
    if (!this.state?.session) { this.problem = ''; this.retry = null; this.draw(); return; }
    // Skip remains available during a run. The server retains progress and serializes dismissal.
    const serial = ++this.serial;
    const session = this.state.session;
    this.state = { ...this.state, status: 'dismissed' }; this.completion = false; this.problem = ''; this.retry = null;
    this.busy = null; this.draw();
    try {
      const reply = await this.deps.act('dismiss', { session });
      if (serial !== this.serial) return;
      if (!reply.ok) throw new Error(reply.error || 'Could not save your choice.');
      this.state = reply.onboarding;
    } catch (error) {
      if (serial !== this.serial) return;
      this.problem = error instanceof Error ? error.message : String(error);
      this.problemAction = 'dismiss';
      this.retry = () => this.dismiss();
    }
    this.draw();
  }

  private async run(action: GuideAction, done?: () => void): Promise<void> {
    // Explicit opening and Retry bring the specimen into view before its normal
    // presentation waits for visible pixels. Never navigate after the await: a
    // page the user opens while this runs belongs to them.
    if (action === 'start' || action === 'restart' || action === 'analyze') this.deps.specimen();
    const serial = ++this.serial;
    this.busy = action; this.problem = ''; this.problemAction = null; this.retry = null; this.draw();
    const identity = this.state?.session ? { session: this.state.session, ...(this.state.result ? { result: this.state.result } : {}) } : undefined;
    try {
      const reply = await this.deps.act(action, identity);
      if (serial !== this.serial) return;
      if (!reply.ok) throw new Error(reply.error || 'That step could not finish. Try again.');
      this.state = reply.onboarding;
      // The ordinary native presentation path confirms decoded pixels in the requesting
      // Studio before returning this transient, exact retained-result identity.
      if (reply.rendered && this.matches(reply.rendered)) this.drawn = reply.rendered;
      if (action === 'start' || action === 'restart') this.openedFor = this.state.session;
      if (this.skipOpening === serial && this.state.session) {
        this.skipOpening = null;
        const dismissed = await this.deps.act('dismiss', { session: this.state.session });
        if (!dismissed.ok) throw new Error(dismissed.error || 'Could not save your choice.');
        if (serial === this.serial) this.state = dismissed.onboarding;
        return;
      }
      if (this.state.status === 'active' && this.state.step === 'measurements')
        this.waitingForDrawing = !this.drawn || !this.matches(this.drawn);
      // A user who switched sessions while a request ran owns the current screen.
      if (this.state.session === this.deps.session()) done?.();
    } catch (error) {
      if (serial !== this.serial) return;
      this.problem = error instanceof Error ? error.message : String(error);
      this.problemAction = action;
      this.retry = () => this.run(action, done);
    } finally {
      if (serial === this.serial) { this.busy = null; this.draw(); }
      if (action === 'start' || action === 'restart') this.deps.opened?.(!this.problem);
    }
  }

  /** The page in view changed (main.ts route): the bar steps aside on the Toolbox once the example is finished. */
  routeChanged(): void { this.draw(); }

  private draw(): void {
    const state = this.state;
    if (state?.session) announce(state.session);
    const same = state?.session === this.deps.session();
    const visible = !this.locallyHidden && Boolean(this.busy === 'start' || this.busy === 'restart' || (!state?.session && this.problem)
      || (same && (state?.status === 'active' || this.completion || this.problem)))
      // On the Toolbox a finished example's bar steps aside; mid-walkthrough it stays, so the way back is never lost.
      && !(this.completion && !this.busy && !this.problem && this.deps.onToolbox?.());
    this.root.hidden = !visible; this.deps.visible?.(visible);
    const step = state?.step ?? 'analyze';
    this.progress.textContent = this.completion ? 'Example complete' : `${step === 'return' ? 3 : step === 'measurements' ? 2 : 1} of 3`;
    this.words.textContent = this.completion ? 'Ready to explore your own?' : step === 'return'
      ? 'Your image and results are saved in this project.' : step === 'measurements' && !this.waitingForDrawing
        ? 'See how large and bright they are.' : 'Let’s find the bright objects in this image.';
    const opening = this.problemAction === 'start' || this.problemAction === 'restart';
    this.status.textContent = (this.problem && (opening ? plainProblem(this.problem) : this.problem)) || (!this.connected ? 'Connection lost. Your progress is saved. Reconnect to continue.'
      : this.busy === 'start' || this.busy === 'restart' ? 'Opening your example…'
        : this.busy === 'analyze' ? 'Finding bright regions on this Mac…'
          : this.waitingForDrawing ? 'Waiting for the outlines to appear…' : '');
    this.status.hidden = !this.status.textContent;
    this.status.title = this.problem; // the whole refusal, for whoever is helping
    this.primary.textContent = this.retry || this.waitingForDrawing ? 'Retry' : this.completion ? 'Open your image or video'
      : step === 'return' ? 'Back to specimen' : step === 'measurements' ? 'See measurements' : 'Find bright objects';
    this.primary.disabled = Boolean(this.busy || !this.connected);
    this.secondary.hidden = !this.completion;
    this.skip.textContent = this.completion ? 'Close walkthrough' : 'Skip walkthrough';
    this.more.hidden = !state?.session;
    this.restart.disabled = Boolean(this.busy || !this.connected);
  }
}
