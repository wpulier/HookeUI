// Blackmagic Camera's controls, the one implementation the camera page and the studio's Phone panel both show:
// Record and Photo, then four groups (Lens, Focus and exposure, Colour, Camera). Built once and updated in place, so a
// slider being dragged or a menu open is never replaced by the next reading of the phone.
//
// What reaches the phone is only what the bridge allows (blackmagic-api.json, proven by the Phone check); a control
// the phone hasn't proven (a lens, Auto white balance) isn't offered.

import type { BridgeApi, PhoneAction, PhoneState } from './bridge-api';
import { chip, el, press } from './dom';
import {
  FRAME_RATES, ISOS, SHUTTERS, WB_MAX, WB_MIN, WHITE_BALANCES, held, lensChoices, nearest, timeLeft, tintWords, zoomWords,
} from './words';

export interface PhoneControlsOptions {
  /** Frame rate chips (the page; the panel only says the rate). */
  frameRate?: boolean;
  /** One column (the panel). */
  narrow?: boolean;
  /** After any control: the phone as the bridge read it back. */
  onPhone?: (phone: PhoneState) => void;
  onError?: (text: string) => void;
  /** A line for the user ("Photo saved: ..."). */
  onNote?: (text: string) => void;
  /** "Test lens switching" (the Phone check), when no lens is proven yet. */
  openPhoneCheck?: () => void;
}

interface Slider { input: HTMLInputElement; out: HTMLOutputElement; show: (v: number) => string; dragging: boolean }

export class PhoneControls {
  readonly root: HTMLElement;
  private readonly api: BridgeApi;
  private readonly options: PhoneControlsOptions;
  private busy = false;
  private phone: PhoneState = {};

  private readonly rec: HTMLButtonElement;
  private readonly photo: HTMLButtonElement;
  private readonly recState: HTMLElement;
  private readonly battery: HTMLElement;
  private readonly lensNow: HTMLOutputElement;
  private readonly lenses: HTMLElement;
  private readonly lensTest: HTMLButtonElement;
  private readonly zoom: Slider;
  private readonly hold: HTMLButtonElement;
  private readonly autofocus: HTMLButtonElement;
  private readonly focusOnce: HTMLButtonElement;
  private readonly focus: Slider;
  private readonly autoExposure: HTMLButtonElement;
  private readonly iso: HTMLSelectElement;
  private readonly shutter: HTMLSelectElement;
  private readonly wb: Slider;
  private readonly wbAuto: HTMLButtonElement;
  private readonly presets: HTMLButtonElement[];
  private readonly tint: Slider;
  private readonly tintZero: HTMLButtonElement;
  private readonly rates: HTMLButtonElement[];
  private readonly rateLine: HTMLOutputElement;
  private readonly stabilizationField: HTMLElement;
  private readonly stabilization: HTMLButtonElement;
  private note: { text: string; at: number; kind: 'photo' | 'clip' } | null = null;
  private readonly reveal: HTMLButtonElement;
  private lastPhoto = '';
  private recordedClip = ''; // the clip while recording, so its stop can say where it went

  constructor(api: BridgeApi, options: PhoneControlsOptions = {}) {
    this.api = api;
    this.options = options;

    // Taking: record on the phone (its own full-quality file) and a photo (the newest frame, saved on the Mac).
    this.rec = el('button', { type: 'button', className: 'cam-act cam-rec', textContent: 'Record on the phone' });
    this.rec.addEventListener('click', () => this.act('record', this.phone.recording ? 'off' : 'on'));
    this.photo = el('button', { type: 'button', className: 'cam-act', textContent: 'Photo',
      title: "Saves the picture as it is now, on this Mac, beside the recordings (the phone's own photos can't be taken from here)" });
    this.photo.addEventListener('click', () => void this.takePhoto());
    this.recState = el('span', { className: 'cam-state' });
    this.reveal = el('button', { type: 'button', className: 'cam-link', textContent: 'Show in Finder', hidden: true,
      title: "Photos are saved on this Mac, in MicroscopeMind's recordings folder" });
    this.reveal.addEventListener('click', () => void this.api.revealPhoto(this.lastPhoto).catch((e) => this.options.onError?.(`Photo: ${(e as Error).message}`)));
    this.battery = el('span', { className: 'cam-battery' });

    // Lens and zoom.
    this.lensNow = el('output');
    this.lenses = el('span', { className: 'cam-chips' });
    this.lensTest = el('button', { type: 'button', className: 'cam-link', textContent: 'Test lens switching', hidden: true });
    this.lensTest.addEventListener('click', () => this.options.openPhoneCheck?.());
    this.zoom = this.slider('Zoom', 0, 1, 0.01, 'zoom', zoomWords);

    // Focus and exposure.
    this.hold = el('button', { type: 'button', className: 'cam-hold', textContent: 'Hold' });
    this.hold.addEventListener('click', () => this.act('lock'));
    this.autofocus = chip('Auto', 'The phone keeps refocusing by itself (it hunts on a slide: Once is usually better)', false);
    this.autofocus.addEventListener('click', () => this.act('autofocus', this.phone.autofocus ? 'off' : 'on'));
    this.focusOnce = chip('Once', 'Focus once automatically on the middle of the view, then hold it');
    this.focusOnce.addEventListener('click', () => this.act('focus-once'));
    this.focus = this.slider('Focus', 0, 1, 0.005, 'focus', () => '');
    this.autoExposure = chip('Auto', 'The phone keeps adjusting brightness by itself', false);
    this.autoExposure.addEventListener('click', () => this.act('exposure', this.autoExposureOn() ? 'off' : 'on'));
    this.iso = this.select(ISOS, (v) => `ISO ${v}`, 'iso', 'ISO: how bright (higher is brighter and grainier)');
    this.shutter = this.select(SHUTTERS, (v) => `1/${v} s`, 'shutter',
      'Shutter: how long each frame takes in light (longer is brighter, but blurs motion)');

    // Colour.
    this.wb = this.slider('White balance', WB_MIN, WB_MAX, 50, 'white-balance', (v) => `${v} K`);
    this.wbAuto = chip('Auto', 'Sets white balance and tint once from what the camera sees now. On the microscope: on an empty, bright part of the slide.');
    this.wbAuto.addEventListener('click', () => this.act('white-balance-auto'));
    this.presets = WHITE_BALANCES.map(([kelvin, light]) => {
      const button = chip(String(kelvin), `${kelvin} K: ${light}`, false);
      button.addEventListener('click', () => this.act('white-balance', kelvin));
      return button;
    });
    this.tint = this.slider('Tint', -50, 50, 1, 'tint', tintWords);
    this.tintZero = chip('0', 'No green or magenta shift');
    this.tintZero.addEventListener('click', () => this.act('tint', 0));

    // Camera.
    this.rates = FRAME_RATES.map((fps) => {
      const button = chip(fps, `${fps} frames a second${fps === '30' ? ': what the app is built for' : ''}`, false);
      button.addEventListener('click', () => this.act('fps', fps));
      return button;
    });
    this.rateLine = el('output');
    this.stabilization = chip('Off', 'Off is steadier on the microscope', false);
    this.stabilization.addEventListener('click', () => this.act('stabilization', this.phone.stabilization ? 'off' : 'on'));
    this.stabilizationField = this.field('Stabilization', [this.stabilization]);

    const group = (title: string | Node, ...fields: Node[]) => el('section', { className: 'cam-group' }, el('h3', {}, title), ...fields);
    this.root = el('div', { className: 'cam-controls' },
      el('div', { className: 'cam-take' }, this.rec, this.photo, this.recState, this.reveal, this.battery),
      el('div', { className: 'cam-groups' },
        group('Lens',
          this.field('Focal length', [this.lensNow], this.lenses, this.lensTest),
          this.field('Zoom', [this.zoom.out], this.zoom.input)),
        group(el('span', { className: 'cam-group-title' }, 'Focus and exposure', this.hold),
          this.field('Focus', [this.autofocus, this.focusOnce], this.focus.input),
          this.field('Exposure', [this.autoExposure], el('div', { className: 'cam-pair' }, this.iso, this.shutter))),
        group('Colour',
          this.field('White balance', [this.wb.out, this.wbAuto], this.wb.input, el('span', { className: 'cam-chips' }, ...this.presets)),
          this.field('Tint', [this.tint.out, this.tintZero], this.tint.input)),
        group('Camera',
          this.field('Frame rate', options.frameRate ? this.rates : [this.rateLine]),
          this.stabilizationField)));
    if (options.narrow) this.root.dataset.narrow = 'true';
  }

  /** The phone as last read. Values being edited are left alone. */
  update(phone: PhoneState): void {
    this.phone = phone;
    const p = phone;
    const isHeld = held(p);
    this.hold.dataset.held = String(isHeld);
    this.hold.textContent = isHeld ? 'Held' : 'Hold';
    this.hold.title = isHeld ? 'Focus and exposure are held at 30 fps: nothing on the phone changes the view by itself'
      : 'The phone is adjusting focus or exposure by itself. Hold keeps them where they are, at 30 fps.';

    // A recording that just stopped says where it went: it is the phone's own file, in Blackmagic Camera.
    if (p.recording && p.clipName) this.recordedClip = p.clipName;
    if (!p.recording && this.recordedClip) {
      this.note = { text: `Saved on the phone: ${this.recordedClip}, in Blackmagic Camera's media`, at: Date.now(), kind: 'clip' };
      this.recordedClip = '';
    }
    this.rec.dataset.on = String(Boolean(p.recording));
    this.rec.textContent = p.recording ? 'Stop recording' : 'Record on the phone';
    const fresh = this.note && Date.now() - this.note.at < 30000 ? this.note : null;
    const note = fresh?.text ?? '';
    this.reveal.hidden = fresh?.kind !== 'photo';
    this.recState.dataset.on = String(Boolean(p.recording) && !note);
    this.recState.textContent = note || (p.recording ? `Recording ${p.clipName ?? ''}${p.timecode ? ` · ${p.timecode}` : ''}`
      : p.recordTimeLeftS != null ? `${timeLeft(p.recordTimeLeftS)} of recording left` : '');
    this.battery.textContent = p.battery != null ? `Battery ${p.battery}%` : '';

    const lenses = lensChoices(p);
    this.lensNow.textContent = p.focalLength ? `${p.focalLength} mm` : '';
    this.lensTest.hidden = lenses.length > 0 || !this.options.openPhoneCheck;
    if (this.lenses.children.length !== lenses.length || lenses.some((l, i) => (this.lenses.children[i] as HTMLElement).dataset.mm !== String(l.mm))) {
      this.lenses.replaceChildren(...lenses.map(({ mm }) => {
        const button = chip(String(mm), `${mm} mm`, false);
        button.dataset.mm = String(mm);
        button.addEventListener('click', () => { if (mm !== this.phone.focalLength) void this.act('lens', mm); });
        return button;
      }));
    }
    lenses.forEach(({ current }, i) => press(this.lenses.children[i] as HTMLElement, current));
    this.setSlider(this.zoom, p.zoom ?? 0);

    press(this.autofocus, Boolean(p.autofocus));
    this.setSlider(this.focus, p.focus ?? 0);
    this.focus.input.disabled = Boolean(p.autofocus) || this.busy;
    const autoExposure = this.autoExposureOn();
    press(this.autoExposure, autoExposure);
    if (p.iso) this.iso.value = String(nearest(ISOS, p.iso));
    if (p.shutter) this.shutter.value = String(nearest(SHUTTERS, p.shutter));
    this.iso.disabled = this.shutter.disabled = autoExposure || this.busy;

    if (p.whiteBalance) this.wb.input.min = String(Math.min(WB_MIN, p.whiteBalance));
    this.setSlider(this.wb, p.whiteBalance ?? 5600);
    this.wbAuto.hidden = !p.autoWhiteBalance;
    this.presets.forEach((button, i) => press(button, WHITE_BALANCES[i][0] === p.whiteBalance));
    this.setSlider(this.tint, p.tint ?? 0);
    this.tintZero.hidden = !p.tint;

    const fps = String(p.fps ?? '').split('.')[0];
    this.rates.forEach((button) => press(button, button.textContent === fps));
    this.rateLine.textContent = fps ? `${fps} fps` : '';
    this.stabilizationField.hidden = p.stabilization == null;
    press(this.stabilization, Boolean(p.stabilization));
    this.stabilization.textContent = p.stabilization ? 'On' : 'Off';

    for (const button of this.root.querySelectorAll('button')) {
      if (button !== this.lensTest) button.disabled = this.busy || (button === this.hold && isHeld);
    }
    this.zoom.input.disabled = this.wb.input.disabled = this.tint.input.disabled = this.busy;
  }

  private autoExposureOn(): boolean {
    return Boolean(this.phone.autoExposure && this.phone.autoExposure !== 'Off');
  }

  private async act(action: PhoneAction, value?: string | number): Promise<void> {
    if (this.busy) return;
    this.busy = true;
    this.update(this.phone);
    try {
      const phone = await this.api.act(action, value);
      this.busy = false;
      this.update(phone);
      this.options.onPhone?.(phone);
    } catch (error) {
      this.busy = false;
      this.update(this.phone);
      this.options.onError?.(`The phone: ${(error as Error).message}`);
    }
  }

  private async takePhoto(): Promise<void> {
    try {
      const body = await this.api.photo();
      this.lastPhoto = body.name;
      this.note = { text: `Photo saved on this Mac: ${body.name}`, at: Date.now(), kind: 'photo' };
      this.update(this.phone);
      this.options.onNote?.(this.note.text);
    } catch (error) {
      this.options.onError?.(`Photo: ${(error as Error).message}`);
    }
  }

  private field(label: string, head: Node[], ...below: Node[]): HTMLElement {
    return el('div', { className: 'cam-field' },
      el('div', { className: 'cam-head' }, el('span', { className: 'cam-label', textContent: label }), el('span', { className: 'cam-chips' }, ...head)),
      ...below);
  }

  private slider(label: string, min: number, max: number, step: number, action: PhoneAction, show: (v: number) => string): Slider {
    const input = el('input', { type: 'range', min: String(min), max: String(max), step: String(step), 'aria-label': label });
    const out = el('output');
    const slider: Slider = { input, out, show, dragging: false };
    input.addEventListener('input', () => { slider.dragging = true; out.textContent = show(Number(input.value)); });
    input.addEventListener('change', () => { slider.dragging = false; void this.act(action, input.value); });
    input.addEventListener('blur', () => { slider.dragging = false; });
    return slider;
  }

  private setSlider(slider: Slider, value: number): void {
    if (slider.dragging) return; // the user's hand is on it
    slider.input.value = String(value);
    slider.out.textContent = slider.show(value);
  }

  private select(values: number[], label: (v: number) => string, action: PhoneAction, title: string): HTMLSelectElement {
    const select = el('select', { title, 'aria-label': title }, ...values.map((v) => el('option', { value: String(v), textContent: label(v) })));
    select.addEventListener('change', () => void this.act(action, select.value));
    return select;
  }
}
