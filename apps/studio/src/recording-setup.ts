import { StudioSheet } from './sheet';
import type { RecordingOptions } from './recorder';

const element = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const node = document.createElement(tag); node.textContent = text; return node;
};

/** Record chooses what is captured before requesting any device or screen permission. */
export class RecordingSetup {
  private readonly sheet = new StudioSheet('What would you like to record?');
  private readonly content = element('section');
  private readonly mode = element('select');
  private readonly explanation = element('p');
  private readonly microphone = element('input');
  private readonly appAudio = element('input');
  private readonly audio = element('fieldset');
  private readonly start = element('button', 'Record camera');
  private readonly status = element('p');
  private readonly annotatedUnavailable: () => string | null;
  private readonly cameraUnavailable: () => string | null;
  private readonly begin: (options: RecordingOptions) => void;

  constructor(cameraUnavailable: () => string | null, begin: (options: RecordingOptions) => void,
    annotatedUnavailable: () => string | null = () => 'Annotated recording is unavailable.') {
    this.annotatedUnavailable = annotatedUnavailable;
    this.cameraUnavailable = cameraUnavailable; this.begin = begin;
    this.content.className = 'recording-setup';
    const label = element('label', 'Record'); label.append(this.mode);
    for (const [value, title] of [['camera', 'Camera source'], ['annotated', 'Annotated view'], ['workspace', 'Workspace']]) {
      const option = element('option', title); option.value = value; this.mode.append(option);
    }
    this.mode.value = 'camera';
    const audioLabel = (input: HTMLInputElement, title: string) => {
      input.type = 'checkbox'; const label = element('label'); label.append(input, document.createTextNode(title)); return label;
    };
    this.audio.append(element('legend', 'Workspace audio (optional)'), audioLabel(this.appAudio, ' Include app audio, when available'), audioLabel(this.microphone, ' Include microphone'));
    const retention = element('p', 'Record also keeps a dataset and the available camera source. Stop finishes this recording; Data reports what actually saved.');
    const cancel = element('button', 'Cancel'); cancel.type = 'button'; cancel.addEventListener('click', () => this.sheet.close());
    this.start.type = 'button'; this.start.className = 'mm-primary';
    this.status.setAttribute('role', 'status');
    this.mode.addEventListener('change', () => this.refresh());
    this.start.addEventListener('click', () => {
      this.refresh(); if (this.start.disabled) return;
      const mode = this.mode.value === 'camera' ? 'camera' : this.mode.value === 'annotated' ? 'annotated' : 'workspace';
      this.sheet.close();
      this.begin({ mode, microphone: mode === 'workspace' && this.microphone.checked, appAudio: mode === 'workspace' && this.appAudio.checked });
    });
    this.content.append(label, this.explanation, this.audio, retention, this.status, this.start, cancel);
  }

  open(opener: HTMLElement): void { this.refresh(); this.sheet.open(this.content, opener); }
  refresh(): void {
    const camera = this.mode.value === 'camera',annotated=this.mode.value==='annotated';
    this.audio.hidden = camera||annotated;
    this.explanation.textContent = camera
      ? 'The incoming camera picture, without Hooke overlays or panels. No window chooser, app audio or microphone.'
      : annotated ? 'The input picture and Hooke overlays drawn on it, without panels, other windows or audio. No window chooser. Keep this Tab and input view visible; changing or hiding it stops the recording.'
      : 'The Hooke window or browser tab, including visible panels and overlays. The system will ask you to choose that window or tab. Choose only Hooke.';
    const reason = camera ? this.cameraUnavailable() : annotated ? this.annotatedUnavailable() : null;
    this.status.textContent = reason ?? '';
    this.start.disabled = Boolean(reason);
    this.start.textContent = camera ? 'Record camera' : annotated ? 'Record annotated view' : 'Choose window and record';
  }
}
