// The Microphone picker (Will, 3 Nov: "the Mac's mic plus a picker, remembered"), in the camera menu (phonepanel.ts)
// and the microphone card (mic-card.ts). It lists this Mac's inputs, names the bridge's phone "iPhone (camera)", and
// keeps the pick as `talk-mic` (mic-choice.ts); a Talk in progress switches to it at once (voice.ts). Automatic is
// Talk's own choice, named: the Mac's microphone, or a headset plugged in, never the iPhone.
import {
  chooseMicrophone, listInputs, microphoneName, onMicrophonePick, saveMicrophone, savedMicrophone, type Input,
} from './mic-choice';

const AUTOMATIC = '';
const MISSING = 'missing';

export class MicPicker {
  readonly root: HTMLElement;
  readonly select: HTMLSelectElement;
  private readonly note: HTMLElement;
  private readonly doc: Document;
  private inputs: Input[] = [];

  /** `caption`: a visible name beside the list (the card's); the camera menu names it with its own heading. */
  constructor(options: { doc?: Document; caption?: string; labelledBy?: string } = {}) {
    const doc = (this.doc = options.doc ?? document);
    this.root = doc.createElement('div');
    this.root.className = 'mic-pick';
    this.select = doc.createElement('select');
    this.select.className = 'mic-pick-select';
    if (options.labelledBy) this.select.setAttribute('aria-labelledby', options.labelledBy);
    else this.select.setAttribute('aria-label', 'Microphone for Talk');
    this.select.addEventListener('change', () => this.picked());
    this.note = doc.createElement('p');
    this.note.className = 'mic-pick-note';
    if (options.caption) {
      const caption = doc.createElement('span');
      caption.className = 'mic-pick-caption';
      caption.textContent = options.caption;
      this.root.append(caption);
    }
    this.root.append(this.select, this.note);
    this.root.dataset.state = 'unknown';
    try { globalThis.navigator?.mediaDevices?.addEventListener?.('devicechange', () => void this.refresh()); } catch { /* listed on open */ }
    onMicrophonePick(() => void this.refresh()); // the other picker, or a pick from here
  }

  /** Whether the list could be read (it has names once the microphone has been allowed). */
  get listed(): boolean {
    return this.inputs.length > 0;
  }

  async refresh(): Promise<void> {
    const devices = await listInputs();
    this.inputs = devices.filter((d) => d.kind === 'audioinput' && d.label && d.deviceId !== 'default' && d.deviceId !== 'communications');
    this.root.dataset.state = this.inputs.length ? 'listed' : 'unlisted';
    this.select.hidden = !this.inputs.length;
    this.note.textContent = this.inputs.length
      ? 'Talk uses the iPhone only if you choose it.'
      : 'Talk uses this Mac’s microphone. The others are listed once the microphone is allowed.';
    if (!this.inputs.length) return;
    const saved = savedMicrophone();
    const auto = chooseMicrophone(devices, null);
    const found = saved ? this.inputs.find((d) => d.deviceId === saved.id) ?? this.inputs.find((d) => d.label === saved.label) : undefined;
    const options: [string, string, boolean][] = [
      [AUTOMATIC, auto ? `Automatic · ${microphoneName(auto)}` : 'Automatic', false],
      ...this.inputs.map((d): [string, string, boolean] => [d.deviceId, microphoneName(d), false]),
    ];
    if (saved && !found) options.push([MISSING, `${saved.label} (not connected)`, true]);
    const made = options.map(([value, words, disabled]) => {
      const option = this.doc.createElement('option');
      option.value = value;
      option.textContent = words;
      option.disabled = disabled;
      return option;
    });
    if (typeof this.select.replaceChildren === 'function') this.select.replaceChildren(...made);
    else { this.select.innerHTML = ''; this.select.append(...made); }
    this.select.value = found ? found.deviceId : saved ? MISSING : AUTOMATIC;
  }

  private picked(): void {
    const value = this.select.value;
    if (value === MISSING) return;
    const input = this.inputs.find((d) => d.deviceId === value);
    saveMicrophone(input ? { id: input.deviceId, label: input.label } : null);
  }
}
