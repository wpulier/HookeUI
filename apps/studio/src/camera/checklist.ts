// The camera's checklist, as the camera page and the studio's Phone panel both show it: the first thing wrong with its
// fix and its one button, Reconnect, Reset camera and Copy report, and every check folded into one line ("All 11 checks
// pass"). The facts and the words are the bridge's (camera_doctor.py); this only lays them out.

import type { BridgeApi, Doctor } from './bridge-api';
import { el } from './dom';
import { ACTION_DONE, ACTION_HINTS, ACTION_LABELS, ACTION_RUNNING, checksSummary } from './words';

const GUIDE_HINT = "if the picture doesn't come within a minute";

export interface ChecklistOptions {
  /** The Phone check: the page opens its own sheet, the panel opens the page there. */
  openPhoneCheck: () => void;
  /** After an action ran: read everything again. */
  onChanged: () => void;
  onError: (text: string) => void;
  onNote?: (text: string) => void;
  /** The checks that something else on the page already explains (the setup card covers the first three). */
  covered?: (doctor: Doctor) => string[];
  /** Whether the page is walking the user through something (the first-time setup): Reconnect is then not the strong
   * button, the walkthrough's own is. */
  guiding?: (doctor: Doctor) => boolean;
  /** The guide's "Waiting for …" line: off where the picture's own place says it (the page's preview). */
  waitingLine?: boolean;
  /** Recent camera events, folded (the page). */
  events?: boolean;
  /** Anything to add at the end of the buttons' row. */
  extra?: Node[];
}

export class Checklist {
  readonly root: HTMLElement;
  private readonly api: BridgeApi;
  private readonly options: ChecklistOptions;
  private readonly first: HTMLElement;
  private readonly connect: HTMLElement;
  /** No camera chosen: one calm line, and nothing to reconnect, reset or check. */
  private readonly offLine: HTMLElement;
  private readonly row: HTMLElement;
  private readonly reconnect: HTMLButtonElement;
  private readonly reset: HTMLButtonElement;
  private readonly summary: HTMLElement;
  private readonly checksFold: HTMLDetailsElement;
  private wasConnecting = false;
  private readonly checks: HTMLOListElement;
  private readonly eventsList: HTMLOListElement;
  private readonly eventsFold: HTMLDetailsElement;
  private doctor: Doctor | null = null;
  private busy = false;
  /** The action running now (its button says so, "Resetting…"), and the last one done, said for a few seconds:
   * pressing Reset or Reconnect must visibly do something (Will, 2026-10-13: "feel more active"). */
  private running: string | null = null;
  private readonly note: HTMLElement;
  private noteTimer: ReturnType<typeof setTimeout> | undefined;
  private shownKey = '';
  private checksKey = '';

  constructor(api: BridgeApi, options: ChecklistOptions) {
    this.api = api;
    this.options = options;
    this.first = el('div', { className: 'cam-first', hidden: true });
    this.reconnect = el('button', { type: 'button', className: 'cam-act', textContent: ACTION_LABELS.reconnect, title: ACTION_HINTS.reconnect });
    this.reconnect.addEventListener('click', () => void this.run('reconnect'));
    this.reset = el('button', { type: 'button', className: 'cam-act', textContent: ACTION_LABELS.reset, title: ACTION_HINTS.reset });
    this.reset.addEventListener('click', () => void this.run('reset'));
    const copy = el('button', { type: 'button', className: 'cam-link', textContent: 'Copy report',
      title: 'The checks and recent events as text, with the technical details, to paste to whoever is helping' });
    copy.addEventListener('click', () => void this.copy(copy));
    this.summary = el('summary');
    this.checks = el('ol', { className: 'cam-checks' });
    this.eventsList = el('ol', { className: 'cam-events' });
    this.eventsFold = el('details', { className: 'cam-fold', hidden: !options.events }, el('summary', {}, 'Recent camera events'), this.eventsList);
    this.connect = el('div', { className: 'cam-connect', hidden: true });
    this.note = el('p', { className: 'cam-done', role: 'status', hidden: true });
    this.offLine = el('p', { className: 'cam-off', hidden: true });
    this.row = el('div', { className: 'cam-row' }, this.reconnect, this.reset, el('span', { className: 'cam-spacer' }), copy, ...(options.extra ?? []));
    this.root = el('div', { className: 'cam-checklist' },
      this.offLine, this.first, this.connect, this.row,
      this.note,
      (this.checksFold = el('details', { className: 'cam-fold' }, this.summary, this.checks)),
      this.eventsFold);
  }

  update(doctor: Doctor | null): void {
    this.doctor = doctor;
    if (!doctor) {
      this.first.hidden = false;
      this.first.replaceChildren(el('p', { className: 'cam-fix', textContent: 'Start HookeUI (make start in Terminal), then reload this page.' }));
      this.summary.replaceChildren('Checks');
      this.checks.replaceChildren();
      return;
    }
    // No camera is the user's choice (Will, 2026-10-15): its line, and nothing to fix.
    const off = doctor.mode === 'off';
    this.offLine.hidden = !off;
    this.offLine.textContent = off ? doctor.line ?? '' : '';
    this.row.hidden = this.checksFold.hidden = off;
    this.eventsFold.hidden = off || !this.options.events;
    const covered = new Set(this.options.covered?.(doctor) ?? []);
    const first = doctor.items.find((item) => item.id === doctor.first);
    // Not connected yet is a guide, never a red box (Will, 2026-10-13: "you didn't break anything").
    const connecting = doctor.mode === 'connect' && !(first && covered.has(first.id));
    this.root.dataset.mode = connecting ? 'connect' : doctor.mode ?? '';
    this.renderConnect(connecting ? doctor : null);
    // Not connected yet, the checks are open: where it is up to, at a glance (the iPhone found, its camera offered,
    // the picture). Opened once as the guide appears, so a fold the user closed stays closed.
    if (connecting && !this.wasConnecting) this.checksFold.open = true;
    this.wasConnecting = connecting;
    const showFirst = Boolean(first) && !covered.has(first!.id) && !connecting;
    // Rebuilt only when what it says changed, so its button isn't replaced under the pointer.
    const key = JSON.stringify([showFirst && first, this.busy]);
    if (key !== this.shownKey) {
      this.shownKey = key;
      this.first.hidden = !showFirst;
      if (showFirst && first) {
        this.first.replaceChildren(
          el('span', { className: 'cam-what', textContent: first.title }),
          el('span', { className: 'cam-evidence', textContent: first.evidence, title: first.detail ?? '' }),
          el('p', { className: 'cam-fix', textContent: first.fix }),
          // Reconnect and Reset camera are always in the row below: never a second copy here.
          ...(first.action && !['reconnect', 'reset'].includes(first.action) ? [el('div', { className: 'cam-row' }, this.actionButton(first.action))] : []));
      }
    }
    // The strong button only when something failed: a camera that is starting is not a fault.
    this.reconnect.dataset.strong = String(!doctor.live && Boolean(doctor.first) && !this.options.guiding?.(doctor));
    this.reconnect.disabled = this.reset.disabled = this.busy;
    this.label(this.reconnect, 'reconnect');
    this.label(this.reset, 'reset');
    // Nothing to reconnect to yet: the guide has its own "Try connecting again".
    this.reconnect.hidden = this.reset.hidden = connecting;

    // Redrawn only when the checks say something new (a fold being read, a hover, are never replaced under the hand).
    const checksKey = JSON.stringify([doctor.items, doctor.first, this.options.events && doctor.events]);
    if (checksKey === this.checksKey) return;
    this.checksKey = checksKey;
    const summary = checksSummary(doctor);
    const dot = el('span', { className: 'cam-dot', 'data-ok': String(summary.ok) });
    this.summary.replaceChildren(dot, summary.text);
    let past = false;
    this.checks.replaceChildren(...doctor.items.map((item) => {
      const li = el('li', { 'data-ok': item.ok === null ? 'unknown' : String(item.ok) },
        el('span', { className: 'cam-mark' }),
        el('span', { className: 'cam-what', textContent: item.title }),
        el('span', { className: 'cam-evidence', textContent: item.evidence, title: item.detail ?? '' }));
      if (past) li.classList.add('cam-after'); // below the first failing check: read once that one is fixed
      if (item.id === doctor.first) past = true;
      return li;
    }));
    if (this.options.events) {
      this.eventsList.replaceChildren(...[...(doctor.events ?? [])].reverse().map((e) => el('li', { textContent: `${e.at}  ${e.text}` })));
    }
  }

  private connectKey = '';
  private readonly guideHint = el('span', { className: 'cam-hint', role: 'status' });
  private readonly guideReset = (() => {
    // When to press it is its tooltip, not a line beside it (Will, 2026-10-15: cleaner); the line says only what it did.
    const button = el('button', { type: 'button', className: 'cam-act', textContent: ACTION_LABELS['try-again'], title: `${ACTION_HINTS.reset} (${GUIDE_HINT})` });
    button.addEventListener('click', () => void this.run('try-again'));
    return button;
  })();

  /** The guide for a camera not connected yet: the steps in order, "Try connecting again" in plain view (Will,
   * 2026-10-13: "needs a new name and visible by default"), what the page waits for, and a quiet "Not connecting?"
   * with what else to check. */
  private renderConnect(doctor: Doctor | null): void {
    // The guide's button is kept, and only its words change while it runs.
    this.guideReset.disabled = this.busy;
    this.label(this.guideReset, 'try-again');
    const key = JSON.stringify(doctor && [doctor.steps, doctor.help, doctor.waiting]);
    if (key === this.connectKey) return;
    this.connectKey = key;
    this.connect.hidden = !doctor;
    if (!doctor) { this.connect.replaceChildren(); return; }
    const help = doctor.help ?? [];
    this.connect.replaceChildren(
      el('ol', { className: 'cam-steps' }, ...(doctor.steps ?? []).map((step) => el('li', { textContent: step }))),
      ...(this.options.waitingLine === false ? [] : [el('p', { className: 'cam-waiting' },
        el('span', { className: 'cam-pulse', 'aria-hidden': 'true' }), `${doctor.waiting ?? 'Waiting for the camera'}…`)]),
      el('div', { className: 'cam-row' }, this.guideReset, this.guideHint),
      ...(help.length ? [el('details', { className: 'cam-help' }, el('summary', {}, 'Not connecting?'),
        el('ul', {}, ...help.map((line) => el('li', { textContent: line }))))] : []));
    if (this.guideHint.dataset.done !== 'true') this.guideHint.textContent = '';
  }

  private actionButton(action: string): HTMLElement {
    if (action === 'settings-file') {
      return el('a', { className: 'cam-act', 'data-strong': 'true', href: this.api.settingsFile, download: 'MicroscopeMind.xml', textContent: ACTION_LABELS[action] });
    }
    const button = el('button', { type: 'button', className: 'cam-act', 'data-strong': 'true', textContent: ACTION_LABELS[action] ?? action, disabled: this.busy });
    button.addEventListener('click', () => void this.run(action));
    this.label(button, action);
    return button;
  }

  /** A button says what it is doing while it runs ("Resetting…", with a spinner), at full strength, not greyed. */
  private label(button: HTMLButtonElement, action: string): void {
    const running = this.running === action;
    button.textContent = running ? ACTION_RUNNING[action] ?? `${ACTION_LABELS[action] ?? action}…` : ACTION_LABELS[action] ?? action;
    button.dataset.busy = String(running);
    button.setAttribute('aria-busy', String(running));
  }

  private async run(action: string): Promise<void> {
    if (action === 'phone-check') {
      this.options.openPhoneCheck();
      return;
    }
    const work: Record<string, () => Promise<unknown>> = {
      reconnect: () => this.api.reconnect(),
      reset: () => this.api.reset(),
      'try-again': () => this.api.reset(), // the guide's name for it: the link to the camera started afresh
      'start-stream': () => this.api.act('start'),
      'use-iphone': () => this.api.source('continuity'),
    };
    if (!work[action] || this.busy) return;
    this.busy = true;
    this.running = action;
    this.say('');
    this.update(this.doctor);
    let done = '';
    const started = Date.now();
    try {
      await work[action]();
      done = ACTION_DONE[action] ?? '';
    } catch (error) {
      this.options.onError(`${ACTION_LABELS[action] ?? action}: ${(error as Error).message}`);
    } finally {
      // Long enough to be seen: a reconnect answered in 50 ms would only flicker.
      await new Promise((resolve) => setTimeout(resolve, Math.max(0, 700 - (Date.now() - started))));
      this.busy = false;
      this.running = null;
      this.update(this.doctor);
      if (done) this.say(this.doctor?.live ? done : `${done} ${this.doctor?.waiting ?? 'Waiting for the picture'}…`);
      this.options.onChanged();
    }
  }

  /** What was just done, for a few seconds under the buttons. */
  private say(text: string): void {
    clearTimeout(this.noteTimer);
    // Said beside the button pressed: in the guide, in place of its hint.
    const inGuide = !this.connect.hidden && this.guideHint.isConnected;
    this.guideHint.textContent = inGuide && text ? text : '';
    this.guideHint.dataset.done = String(inGuide && Boolean(text));
    this.note.textContent = text;
    this.note.hidden = !text || inGuide;
    if (text) this.noteTimer = setTimeout(() => this.say(''), 8000);
  }

  private async copy(button: HTMLButtonElement): Promise<void> {
    const text = this.doctor?.report ?? this.doctor?.headline ?? "The camera bridge isn't answering.";
    try {
      await navigator.clipboard.writeText(text);
      button.textContent = 'Copied';
      this.options.onNote?.('The camera report is copied: paste it to whoever is helping.');
      setTimeout(() => (button.textContent = 'Copy report'), 1500);
    } catch {
      window.prompt('Copy the camera report:', text);
    }
  }
}
