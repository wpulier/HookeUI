import { cameraNetworkDoctor, nativeCameraNetwork } from './native-network';
// The camera bridge's wire, as the camera page and the studio's Phone panel use it (the bridge:
// experiments/iphone-camera-feed/scripts/iphone_bridge_server.py; README there lists every endpoint). Writes carry the
// header the bridge asks for, so another site can't drive the camera; one error rule for every call.

export interface DoctorItem {
  id: string;
  title: string;
  ok: boolean | null;
  evidence: string; // plain words, for the page
  detail?: string; // the technical fact behind it, for whoever is helping (a hover, Copy report)
  fix: string;
  action: string | null;
  problem: string;
}

/** The camera's checklist (camera_doctor.checklist): facts read, never guessed; the first that fails has the fix. */
export interface Doctor {
  source: string;
  chosen?: string; // the live source chosen, also while test footage plays over it
  via?: string | null; // Blackmagic's link, picked by the bridge: "the cable" or "Wi-Fi"
  live: boolean;
  headline: string;
  /** live; starting (a camera just asked for); connect (not connected yet: a guide, never a fault); problem (it broke). */
  mode?: 'live' | 'starting' | 'connect' | 'problem' | 'off';
  /** Mode off (No camera chosen): one calm line under the headline. */
  line?: string;
  /** connect: what to do, in order; what to check if it still doesn't come; what the page waits for. */
  steps?: string[];
  help?: string[];
  waiting?: string;
  first: string | null;
  items: DoctorItem[];
  events?: { at: string; text: string }[];
  report?: string;
  /** The Blackmagic Camera app has been set up on this Mac (seen once): the first-time walkthrough never shows again. */
  setUp?: boolean;
}

/** Blackmagic Camera as the bridge's phone link last read it. */
export interface PhoneState {
  reachable?: boolean;
  name?: string;
  model?: string | null;
  app?: string | null;
  via?: string | null;
  link?: string | null;
  autofocus?: boolean | null;
  focus?: number | null;
  autoExposure?: string | null;
  iso?: number | null;
  shutter?: number | null;
  whiteBalance?: number | null;
  tint?: number | null;
  fps?: string | null;
  battery?: number | null;
  recording?: boolean;
  clipName?: string | null;
  timecode?: string | null;
  clips?: number | null;
  recordTimeLeftS?: number | null;
  focalLength?: number | null;
  zoom?: number | null; // within the lens in use, 0 to 1
  lenses?: number[]; // focal lengths the Phone check proved switch from the Mac
  stabilization?: boolean | null;
  autoWhiteBalance?: boolean; // white balance set once from the view: proven on this phone
  streaming?: boolean;
  streamStatus?: string | null;
  server?: string | null;
  addresses?: { wifi?: string | null; usb?: string | null };
  error?: string;
}

export interface Cadence { fps: number; p50Ms: number | null; p95Ms: number | null; maxGapMs: number | null; even: boolean }

export interface Trial { title: string; fps: number; p95Ms: number | null; gaps: number; startedAt?: string }

export interface Health {
  ok: boolean;
  sourceKind?: string;
  targetDevice?: string;
  stateLabel?: string;
  frameCount?: number;
  lastFrameAgeSeconds?: number | null;
  requestCount?: number;
  restartCount?: number;
  lastError?: string;
  lastHeal?: { at?: string } | null;
  phone?: PhoneState;
  cadence?: Cadence;
  trial?: { title: string } | null;
  trialCount?: number;
  sourceChoices?: SourceChoice[];
}

/** A live source as the bridge offers it; for the other camera, what was last chosen (a camera's name, or a stream). */
export interface SourceChoice { kind: string; title: string; active: boolean; device?: string | null; stream?: boolean }

/** A camera this Mac sees (Other camera's picker); `iphone`: the iPhone itself, which has its own tab. */
export interface CameraDevice { name: string; iphone: boolean; chosen: boolean }

export interface PhoneCheckState {
  running?: boolean;
  finished?: boolean;
  done?: number;
  total?: number;
  step?: string;
  needsYou?: string;
  summary?: string[];
  error?: string;
}

/** The phone's controls: `/api/phone?do=` actions (phone_control.PhoneLink.act). */
export type PhoneAction = 'lock' | 'autofocus' | 'focus-once' | 'focus' | 'exposure' | 'iso' | 'shutter' | 'white-balance'
  | 'white-balance-auto' | 'tint' | 'fps' | 'zoom' | 'lens' | 'stabilization' | 'record' | 'start' | 'stop';

export class BridgeError extends Error {}

export class BridgeApi {
  private readonly base: () => string;
  private readonly fetcher: typeof fetch;

  /** `base` is the bridge's address ('' on the bridge's own page). */
  constructor(base: () => string, fetcher: typeof fetch = (...args) => fetch(...args)) {
    this.base = base;
    this.fetcher = fetcher;
  }

  url(path: string): string {
    return `${this.base()}${path}`;
  }

  private async get<T>(path: string): Promise<T> {
    const response = await this.fetcher(this.url(path), { cache: 'no-store' });
    if (!response.ok) throw new BridgeError(`${response.status}`);
    return (await response.json()) as T;
  }

  /** A write: only the bridge's own pages send its header. A refusal is `ok: false` or a status that isn't 2xx. */
  private async post<T>(path: string, header: 'camera-control' | 'camera-source'): Promise<T> {
    const response = await this.fetcher(this.url(path), { method: 'POST', headers: { 'X-MicroscopeMind': header } });
    const body = (await response.json().catch(() => ({}))) as { ok?: boolean; error?: string; message?: string };
    if (!response.ok || body.ok === false) throw new BridgeError(body.error || body.message || `Camera request failed (${response.status}). Try again or copy the camera report.`);
    return body as T;
  }

  async doctor(quiet = false): Promise<Doctor> {
    const doctor = await this.get<Doctor>(`/api/doctor${quiet ? '?quiet=1' : ''}`);
    return cameraNetworkDoctor(doctor, nativeCameraNetwork());
  }
  health(): Promise<Health> { return this.get('/api/health'); }
  phone(): Promise<PhoneState> { return this.get('/api/phone'); }
  cadence(): Promise<{ ago: number[]; windowS: number }> { return this.get('/api/cadence'); }
  async trials(): Promise<Trial[]> { return (await this.get<{ trials?: Trial[] }>('/api/trials')).trials ?? []; }
  phoneCheck(): Promise<PhoneCheckState> { return this.get('/api/phone-check'); }

  async act(action: PhoneAction, value?: string | number): Promise<PhoneState> {
    const query = value === undefined ? '' : `&value=${encodeURIComponent(String(value))}`;
    return (await this.post<{ phone: PhoneState }>(`/api/phone?do=${action}${query}`, 'camera-control')).phone;
  }
  photo(): Promise<{ name: string; path: string }> { return this.post('/api/photo', 'camera-control'); }
  /** Show a saved photo in the Finder (on this Mac). */
  revealPhoto(name: string): Promise<unknown> { return this.post(`/api/photo/reveal?name=${encodeURIComponent(name)}`, 'camera-control'); }
  startPhoneCheck(): Promise<PhoneCheckState> { return this.post('/api/phone-check', 'camera-control'); }
  reset(): Promise<unknown> { return this.post('/api/restart?reset=1', 'camera-control'); }
  reconnect(): Promise<unknown> { return this.post('/api/restart', 'camera-source'); }
  /** Choose the live source; the other camera with a camera's name (`device`) or a stream address (`url`), else the
   * one it had last. */
  source(kind: string, choice: { device?: string; url?: string } = {}): Promise<{ bridge?: Health }> {
    const extra = choice.device ? `&device=${encodeURIComponent(choice.device)}` : choice.url ? `&url=${encodeURIComponent(choice.url)}` : '';
    return this.post(`/api/source?kind=${encodeURIComponent(kind)}${extra}`, 'camera-source');
  }
  async cameras(): Promise<CameraDevice[]> { return (await this.get<{ cameras?: CameraDevice[] }>('/api/cameras')).cameras ?? []; }
  startTrial(seconds = 60): Promise<unknown> { return this.post(`/api/trial?seconds=${seconds}`, 'camera-source'); }

  get settingsFile(): string { return this.url('/api/blackmagic.xml'); }
  get settingsQr(): string { return this.url(`/api/settings-qr.svg?t=${Date.now()}`); }
  get appStoreQr(): string { return this.url('/api/app-store-qr.svg'); }
  get live(): string { return this.url(`/iphone.mjpeg?t=${Date.now()}`); }
  page(hash = ''): string { return this.url(`/camera/${hash}`); }
}
