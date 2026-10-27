// The Phone panel: the camera's state and controls, always one click away in the header (Will, 2026-10-12: "we keep
// having to go to you to fix it"). The checklist and the phone's controls are the camera page's own (camera/), so the
// two never say different things; this file is only the panel around them: its button, its dot and its polling.
// Like the camera menu it talks only to the bridge, never to the orchestrator or the phone.
//
// Top to bottom (Will, 2026-10-15: "a lot more intuitive, labeled, and the way to open the full camera setup at top"):
// its title, Camera, with Open camera setup beside it; what is on stage when it isn't the camera, with Switch to live
// camera (the header button only ever opens this menu: one click once swapped a recording for the camera); the state
// in a sentence; Source, its four choices (No camera among them) and what the chosen one is; then the checklist and
// the phone's controls.

import { BridgeApi, type Doctor, type PhoneState } from './camera/bridge-api';
import { Checklist } from './camera/checklist';
import { el } from './camera/dom';
import { PhoneControls } from './camera/phone-controls';
import { SOURCES, SOURCE_NOTES, dotState, headline, setupSteps, tab } from './camera/words';
import './camera/controls.css';

/** One way to change the specimen, offered in the studio's Specimen menu (an input of this Project, open a file, ...). */
export interface SpecimenChoice { id: string; owner: { session: string; generation: number }; label: string; detail?: string; current?: boolean; act: () => void }

export class PhonePanel {
  doctor: Doctor | null = null;
  phone: PhoneState | null = null;
  private switching = false;

  private readonly root: HTMLElement;
  private readonly toggle: HTMLButtonElement;
  private readonly api: BridgeApi;
  private readonly hint: (text: string, problem?: boolean) => void;
  private readonly head: HTMLElement;
  private readonly title: HTMLElement;
  private readonly setupLink: HTMLAnchorElement;
  private readonly stageRow: HTMLElement;
  private readonly stageWords: HTMLElement;
  private readonly liveButton: HTMLButtonElement;
  private readonly sourceNote: HTMLElement;
  private readonly sources: HTMLButtonElement[];
  private readonly checklist: Checklist;
  private readonly controls: PhoneControls;
  private readonly controlsBox: HTMLElement;
  /** Told each time the checklist is read (the header's camera control words its tooltip from it). */
  onDoctor: () => void = () => {};
  /** What is on stage when it isn't the live camera (a recording or picture), by its title; null on the camera. */
  onStage: () => string | null = () => null;
  /** Put the live camera on stage: only ever a press of Switch to live camera. */
  goLive: () => void = () => {};
  /** The studio's ways to change the specimen (its Project's inputs, Open a picture or video, the Library). */
  choices: () => SpecimenChoice[] = () => [];
  private readonly choicesBox: HTMLElement;
  private readonly choiceList: HTMLElement;
  private readonly choiceRows = new Map<string, { row: HTMLButtonElement; label: HTMLElement; detail: HTMLElement; choice: SpecimenChoice }>();
  private readonly cameraBox: HTMLDetailsElement | null = null;
  private readonly liveRow: HTMLButtonElement;
  private readonly liveDetail: HTMLElement;
  private readonly cameraSummary: HTMLElement;
  private readonly setupBox: HTMLElement;

  constructor(root: HTMLElement, toggle: HTMLButtonElement, bridgeUrl: () => string, hint: (text: string, problem?: boolean) => void,
    options: { feedSelector?: boolean } = {}) {
    this.root = root;
    this.toggle = toggle;
    this.api = new BridgeApi(bridgeUrl);
    this.hint = hint;
    const openPage = (hash = '') => { try { window.open(this.api.page(hash), '_blank', 'noopener'); } catch (error) { this.hint(String(error), true); } };

    // The full camera setup, first thing in the menu: a real button, never small grey text.
    this.setupLink = el('a', { className: 'cam-act phone-setup-link', href: '#', target: '_blank', rel: 'noopener',
      textContent: 'Open camera setup ↗', title: 'The camera page, in a new tab: the picture, first-time setup, every check and control' });
    this.setupLink.addEventListener('click', (event) => { event.preventDefault(); openPage(); });
    const top = el('div', { className: 'phone-top' }, el('h2', { textContent: options.feedSelector ? 'Specimen' : 'Camera' }), el('span', { className: 'cam-spacer' }), this.setupLink);
    const live = (this.liveButton = el('button', { type: 'button', className: 'cam-act', textContent: 'Switch to live camera' }));
    live.addEventListener('click', () => { this.goLive(); this.setOpen(false); });
    this.stageWords = el('p', { className: 'phone-stage-words' });
    this.stageRow = el('div', { className: 'phone-stage', hidden: true }, this.stageWords, live);
    this.title = el('p', { className: 'phone-state' });
    this.head = el('div', { className: 'phone-head' }, el('span', { className: 'phone-dot', 'aria-hidden': 'true' }), this.title);
    this.sources = SOURCES.map(([kind, label]) => {
      const button = el('button', { type: 'button', textContent: label, 'data-kind': kind });
      button.addEventListener('click', () => void this.choose(kind));
      return button;
    });
    this.sourceNote = el('p', { className: 'phone-source-note' });
    this.checklist = new Checklist(this.api, {
      openPhoneCheck: () => openPage('#phone-check'), // guided, on the camera page: it needs the whole page and some minutes
      onChanged: () => void this.refresh(true),
      onError: (text) => this.hint(text, true),
      onNote: (text) => this.hint(text),
    });
    this.controls = new PhoneControls(this.api, {
      narrow: true,
      onPhone: (phone) => { this.phone = phone; },
      onError: (text) => this.hint(text, true),
      onNote: (text) => this.hint(text),
      openPhoneCheck: () => openPage('#phone-check'),
    });
    this.controlsBox = el('div', { className: 'phone-controls', hidden: true }, el('h3', { textContent: 'On the phone' }), this.controls.root);
    // The first time, the setup is a guided walk through on the camera page (it needs the whole page and the phone in hand).
    const setUp = el('button', { type: 'button', className: 'cam-act', 'data-strong': 'true', textContent: 'Set it up' });
    setUp.addEventListener('click', () => openPage());
    this.setupBox = el('div', { className: 'phone-setup', hidden: true },
      el('p', { textContent: 'First time with the Blackmagic Camera app? The camera page walks you through it, step by step: about 5 minutes, once.' }),
      setUp);
    this.choiceList = el('div', { className: 'phone-choice-list', role: 'list' });
    // Live camera is always the first way to change the specimen, never folded away (Will, 2026-10-23: "an obvious Live
    // camera input and return path"). With no camera chosen it opens Camera below to choose one; it switches nothing.
    this.liveDetail = el('span', { className: 'phone-choice-detail' });
    this.liveRow = el('button', { type: 'button', className: 'phone-choice phone-choice-live', role: 'listitem' },
      el('span', { className: 'phone-choice-label', textContent: 'Live camera' }), this.liveDetail);
    this.liveRow.addEventListener('click', () => {
      if (tab(this.doctor?.chosen ?? this.doctor?.source) === 'none') {
        if (this.cameraBox) { this.cameraBox.open = true; const first = this.sources[0]; if (typeof first?.focus === 'function') first.focus(); }
        return;
      }
      this.goLive(); this.setOpen(false);
    });
    this.choicesBox = el('div', { className: 'phone-choices', hidden: true }, el('h3', { textContent: 'Change to' }), this.liveRow, this.choiceList);
    this.cameraSummary = el('summary', { className: 'phone-camera-summary' });
    const camera = [this.head,
      el('div', { className: 'phone-source' },
        el('h3', { id: 'phone-source-label', textContent: options.feedSelector ? 'Live camera source' : 'Source' }),
        el('div', { className: 'phone-sources', role: 'group', 'aria-labelledby': 'phone-source-label' }, ...this.sources),
        this.sourceNote),
      this.setupBox, this.checklist.root, this.controlsBox];
    if (options.feedSelector) {
      // The studio's Specimen menu: what is on the Stage, the ways to change it, then the camera, folded to one line
      // while no camera is chosen (its checks and fixes are for a camera, never for pictures and videos).
      this.cameraBox = el('details', { className: 'phone-camera' }, this.cameraSummary, ...camera) as HTMLDetailsElement;
      this.root.replaceChildren(top, this.stageRow, this.choicesBox, this.cameraBox);
    } else {
      this.root.replaceChildren(top, this.stageRow, ...camera);
    }

    toggle.addEventListener('click', () => this.setOpen(this.root.hidden === true));
    document.addEventListener('click', (event) => {
      const target = event.target as Node;
      if (this.root.hidden || !target.isConnected || this.root.contains(target) || this.toggle.contains(target)) return;
      this.setOpen(false);
    });
    void this.poll();
  }

  get open(): boolean {
    return this.root.hidden !== true;
  }

  /** `camera`: opened to choose or check a camera (Choose a camera, Troubleshoot), so its section starts unfolded. */
  setOpen(open: boolean, options: { camera?: boolean } = {}): void {
    this.root.hidden = !open;
    this.toggle.setAttribute('aria-expanded', String(open));
    if (open) {
      // Opened, the camera section shows when a camera is chosen (its state matters then) or was asked for, and folds
      // otherwise.
      if (this.cameraBox) this.cameraBox.open = !!options.camera || tab(this.doctor?.chosen ?? this.doctor?.source) !== 'none';
      this.render(); void this.refresh(true);
    }
  }

  /** While open the checklist is read every second and the phone every two; closed, only the header dot (quietly). */
  private async poll(): Promise<void> {
    let tick = 0;
    for (;;) {
      if (!document.hidden) await this.refresh(this.open && tick % 2 === 0);
      tick += 1;
      await new Promise((resolve) => setTimeout(resolve, this.open ? 1000 : 3000));
    }
  }

  async refresh(withPhone = false): Promise<void> {
    try {
      const [doctor, phone] = await Promise.all([this.api.doctor(!this.open), withPhone ? this.api.phone() : Promise.resolve(this.phone)]);
      this.doctor = doctor;
      this.phone = phone;
    } catch {
      this.doctor = null;
    }
    // The header's camera control is the toggle: its own state and words are main.ts's (what is on stage); the
    // checklist's verdict rides along for its dot and its tooltip.
    this.toggle.dataset.doctor = dotState(this.doctor);
    this.onDoctor();
    if (this.open) this.render();
  }

  /** Choose a live source from elsewhere (the empty stage's Just use pictures and videos). */
  chooseSource(kind: string): Promise<void> {
    return this.choose(kind);
  }

  private async choose(kind: string): Promise<void> {
    if (this.switching || kind === tab(this.doctor?.chosen ?? this.doctor?.source)) return;
    this.switching = true;
    this.render();
    try {
      await this.api.source(kind);
    } catch (error) {
      if (kind === 'other') { // no camera chosen yet: the camera page has the picker (the cameras this Mac sees)
        try {
          window.open(this.api.page('#other'), '_blank', 'noopener');
          this.hint('Choose the other camera on the camera page.');
        } catch { this.hint('Camera setup is unavailable for this preview.', true); }
      } else {
        this.hint(`Switching: ${(error as Error).message}`, true);
      }
    } finally {
      this.switching = false;
      await this.refresh(true);
    }
  }

  private render(): void {
    try { this.setupLink.href = this.api.page(); this.setupLink.setAttribute('aria-disabled', 'false'); }
    catch { this.setupLink.href = '#'; this.setupLink.setAttribute('aria-disabled', 'true'); }
    const d = this.doctor;
    this.head.dataset.state = dotState(d);
    this.title.textContent = headline(d);
    const chosen = tab(d?.chosen ?? d?.source);
    const onStage = this.onStage();
    this.stageRow.hidden = !onStage;
    this.stageWords.textContent = onStage ? `On the Stage: ${onStage}` : '';
    const choices = this.choices();
    this.choicesBox.hidden = choices.length === 0 && !this.cameraBox;
    // The list marks what is on the Stage; the row above stays only to offer Switch to live camera.
    if (choices.some((choice) => choice.current) && chosen === 'none') this.stageRow.hidden = true;
    if (this.cameraBox) {
      // The Specimen menu: Live camera leads the list and says its state, so the row above would repeat it.
      this.stageRow.hidden = true;
      const liveNow = !onStage && chosen !== 'none' && !!d?.live;
      this.liveDetail.textContent = chosen === 'none' ? 'No camera chosen yet · choose one under Camera'
        : liveNow ? `${headline(d)} · on the Stage` : headline(d);
      this.liveRow.disabled = liveNow;
      if (liveNow) this.liveRow.setAttribute('aria-current', 'true'); else this.liveRow.removeAttribute('aria-current');
    }
    // A poll updates words and actions in place. Identity includes the Project opening:
    // a focused button can never become an action belonging to a different owner.
    const keys = new Set(choices.map(choice => JSON.stringify([choice.owner.session, choice.owner.generation, choice.id])));
    for (const [key, entry] of this.choiceRows) if (!keys.has(key)) { entry.row.remove(); this.choiceRows.delete(key); }
    choices.forEach((choice, index) => {
      const key = JSON.stringify([choice.owner.session, choice.owner.generation, choice.id]);
      let entry = this.choiceRows.get(key);
      if (!entry) {
        const label = el('span', { className: 'phone-choice-label' });
        const detail = el('span', { className: 'phone-choice-detail' });
        const row = el('button', { type: 'button', className: 'phone-choice', role: 'listitem' }, label, detail);
        entry = { row, label, detail, choice };
        const owned = entry;
        row.addEventListener('click', () => { this.setOpen(false); owned.choice.act(); });
        this.choiceRows.set(key, entry);
      }
      entry.choice = choice;
      if (entry.label.textContent !== choice.label) entry.label.textContent = choice.label;
      if (entry.detail.textContent !== (choice.detail ?? '')) entry.detail.textContent = choice.detail ?? '';
      entry.detail.hidden = !choice.detail;
      entry.row.disabled = !!choice.current;
      if (choice.current) entry.row.setAttribute('aria-current', 'true'); else entry.row.removeAttribute('aria-current');
      const at = this.choiceList.children[index];
      if (at !== entry.row) this.choiceList.insertBefore(entry.row, at ?? null);
    });
    this.cameraSummary.textContent = chosen === 'none' ? 'Camera · none chosen' : `Camera · ${headline(d)}`;
    this.liveButton.hidden = chosen === 'none'; // no camera to switch to
    this.sourceNote.textContent = SOURCE_NOTES[chosen] ?? '';
    // Strong while nothing is live: the setup is then where to go.
    this.setupLink.dataset.strong = String(!d?.live && chosen !== 'none');
    for (const button of this.sources) {
      button.setAttribute('aria-pressed', String(button.dataset.kind === chosen));
      button.disabled = this.switching;
    }
    this.setupBox.hidden = !setupSteps(d).show;
    this.checklist.update(d);
    const p = this.phone;
    const show = Boolean(p?.reachable) && chosen === 'blackmagic';
    this.controlsBox.hidden = !show;
    if (show && p) this.controls.update(p);
  }
}
