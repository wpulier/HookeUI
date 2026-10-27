// The camera menu: where the live feed comes from, and getting the camera live.
//
// It talks to the camera bridge's own control endpoints (/feed, /api/restart), never to
// the orchestrator: playing test footage into the live feed is invisible to everything
// downstream (perception, the models, the agent, the logs). Only this menu and the
// "Test footage" badge tell you it's on. The user's own recordings and pictures are the
// exception (ADR 0022): the server plays them as a session's sources (sources.ts), and
// this only shows the switch while it waits (`viaServer`).

export interface FootageItem {
  name: string; // its path under the assets store's footage/, e.g. real/2026-06-14-leaf-epidermis-3m43s.mp4
  title: string;
  group: string; // "Real microscope", "Added here", "Camera, not on the microscope", "Corpus: <specimen>", "Other"
  seconds: number | null; // null until the bridge has measured it
  truth: boolean; // a per-frame truth beside it, for benches (the app never reads it)
  bytes?: number;
  mtime?: number; // seconds; part of the thumbnail's address, so a changed file gets a new picture
  specimen?: string;
  date?: string;
  backedUp?: boolean; // in assets/manifest.json and the GitHub release (false: added here, not adopted yet)
  optics?: Optics | null; // what it was filmed with, when known (assets/manifest.json `optics`)
}

/** What a recording was filmed with. Data for tests: the app is never told it from the footage; the
 *  studio offers "Use its optics", which sets the objective as the objective menu does. */
export interface Optics {
  camera?: string | null;
  microscope?: string | null;
  objective?: number | null; // x
  eyepiece?: number | null; // x
  fieldNumber?: number | null; // mm
  umPerPx?: number | null; // micrometres per camera pixel, measured
  notes?: string | null;
}

/** "40x objective · 10x eyepiece · FN 18 · 0.29 µm/px" (only what is known; '' when nothing is). */
export function opticsLine(optics: Optics | null | undefined): string {
  if (!optics) return '';
  const x = (n: number) => `${Number.isInteger(n) ? n : n.toFixed(1)}x`;
  return [
    optics.objective ? `${x(optics.objective)} objective` : '',
    optics.eyepiece ? `${x(optics.eyepiece)} eyepiece` : '',
    optics.fieldNumber ? `FN ${optics.fieldNumber}` : '',
    optics.umPerPx ? `${optics.umPerPx} µm/px` : '',
  ].filter(Boolean).join(' · ');
}

/** What a recording added from the studio says about itself. */
export interface NewRecording {
  title: string;
  specimen: string;
  microscope: boolean;
  objective?: number | null;
  eyepiece?: number | null;
}

export interface FootageGroup {
  name: string;
  items: FootageItem[];
  folded: boolean; // the corpus's groups fold, so hundreds of tours never fill the menu
}

/** A group of the public library: public microscopy shown through a simulated eyepiece (the
 *  bridge's "Corpus: <specimen>" groups), as opposed to recordings from this microscope. */
export function isLibrary(group: string): boolean {
  return group.startsWith('Corpus: ');
}

/** The specimen a public-library group is about ("Corpus: blood smear" -> "Blood smear"). */
export function specimenOf(group: string): string {
  const name = group.replace(/^Corpus: /, '');
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/** "1 clip", "1,503 clips". */
export function clips(n: number): string {
  return `${n.toLocaleString('en-US')} ${n === 1 ? 'clip' : 'clips'}`;
}

/** The library in the bridge's order, as groups; public-library groups fold. */
export function footageGroups(library: FootageItem[]): FootageGroup[] {
  const groups: FootageGroup[] = [];
  for (const item of library) {
    let group = groups[groups.length - 1];
    if (!group || group.name !== item.group) {
      group = { name: item.group, items: [], folded: isLibrary(item.group) };
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

export interface FeedState {
  source: 'camera' | 'footage';
  footage: string | null;
  footageTitle?: string | null;
  folder: string;
  libraryVersion: string; // the library itself is GET /assets/library, fetched when this changes
  libraryCount: number;
  /** Where the recording is, when one plays; a picture is one held frame (`still`, duration 0). */
  playback?: { seconds: number; duration: number; paused?: boolean; still?: boolean } | null;
  targetDevice: string;
  cameraVisible?: boolean;
  bridge: {
    ok: boolean;
    state: string;
    stateLabel: string;
    /** The live source chosen (bridge_sources.SOURCE_KINDS): "none" is No camera, pictures and videos only. */
    sourceKind?: string;
    devicePresent: boolean;
    lastError?: string;
    /** Why there is no picture and what to do, in the bridge's own words (none while live). */
    diagnosis?: { code: string; text: string; steps?: string[]; note?: string } | null;
  };
}

/** Where a recording starts and whether it is held at once (the bridge's `/feed` `at` and `paused`). */
export interface PlayOptions {
  at?: number;
  paused?: boolean;
}

/** What the studio switches the feed to itself: the camera, or test footage by name. */
export type FeedPick =
  | { kind: 'camera' }
  | ({ kind: 'footage'; name: string } & PlayOptions);

/** What `/feed` says plays for a pick (the footage's name); null for the camera. */
export function pickKey(pick: FeedPick): string | null {
  return pick.kind === 'camera' ? null : pick.name;
}

/** The bridge's `POST /feed` query for a pick. */
export function feedQuery(pick: FeedPick): string {
  if (pick.kind === 'camera') return 'source=camera';
  const query = new URLSearchParams({ source: 'footage', footage: pick.name });
  if (pick.paused) query.set('paused', '1');
  if (typeof pick.at === 'number' && Number.isFinite(pick.at) && pick.at > 0) query.set('at', pick.at.toFixed(1));
  return query.toString();
}

export class CameraFeed {
  bridgeUrl = '';
  state: FeedState | null = null;
  busy: 'switching' | 'reconnecting' | null = null;
  /** The recording being switched to (shown at once, before the bridge has it playing). */
  choosing: FootageItem | null = null;
  /** When `state.playback` was read, for the playhead between polls. */
  playbackAt = 0;
  private wanted: FeedPick | undefined = undefined; // a switch asked for while one runs: the last wins
  /** The footage library, fetched only when someone looks at it and its version changed. */
  library: FootageItem[] = [];
  libraryVersion = '';
  private loadingLibrary = false;
  private readonly onChange: () => void;

  constructor(onChange: () => void) {
    this.onChange = onChange;
  }

  private refreshing: Promise<void> | null = null;

  async refresh(probeCamera = false): Promise<void> {
    if (!this.bridgeUrl) return;
    if (this.refreshing) return this.refreshing; // a slow device scan is never stacked
    const read = async () => {
      try {
        const response = await fetch(`${this.bridgeUrl}/feed${probeCamera ? '?probe=1' : ''}`, {
          cache: 'no-store',
          signal: AbortSignal.timeout(8000),
        });
        this.state = (await response.json()) as FeedState;
        this.playbackAt = performance.now();
      } catch {
        this.state = null;
      }
    };
    const current: Promise<void> = read().finally(() => {
      if (this.refreshing === current) this.refreshing = null;
      this.onChange();
    });
    this.refreshing = current;
    return current;
  }

  /** What plays, read again now: after any read already in flight (it may have begun before a switch). */
  async reread(): Promise<void> {
    if (this.refreshing) await this.refreshing;
    await this.refresh();
  }

  /** The server switches the feed (a session's source: commands.py `source`). Shown at once by its title (null: the
   * camera) while `run` waits on the server; what plays is read again after, and a switch of the studio's own asked for
   * meanwhile goes next. Returns why it failed, in words, or null. */
  async viaServer(shown: { title: string; seconds?: number | null } | null, run: () => Promise<unknown>): Promise<string | null> {
    this.choosing = shown ? { name: 'asset:', title: shown.title, group: 'Source', seconds: shown.seconds ?? null, truth: false } : null;
    this.busy = 'switching';
    this.onChange();
    try {
      await run();
      return null;
    } catch (error) {
      return error instanceof Error ? error.message : String(error);
    } finally {
      this.busy = null;
      await this.reread(); // what plays is shown by its title until the bridge says what plays now (no flash of the camera)
      this.choosing = null;
      const next = this.wanted;
      this.wanted = undefined;
      if (next !== undefined) void this.switchTo(next);
      this.onChange();
    }
  }

  /** The library when it changed since the last fetch (the bridge answers 304 otherwise). */
  async loadLibrary(): Promise<void> {
    if (!this.bridgeUrl || this.loadingLibrary) return;
    if (this.state && this.state.libraryVersion === this.libraryVersion && this.library.length) return;
    this.loadingLibrary = true;
    try {
      const response = await fetch(`${this.bridgeUrl}/assets/library`, {
        headers: this.libraryVersion ? { 'If-None-Match': `"${this.libraryVersion}"` } : {},
        signal: AbortSignal.timeout(8000),
      });
      if (response.status === 200) {
        const body = (await response.json()) as { version: string; items: FootageItem[] };
        this.library = body.items;
        this.libraryVersion = body.version;
        this.onChange();
      }
    } catch {
      // kept as it was; the next look tries again
    } finally {
      this.loadingLibrary = false;
    }
  }

  /** A recording's thumbnail (made by the bridge in the background, cached for good by the browser). */
  thumbUrl(item: FootageItem): string {
    return `${this.bridgeUrl}/assets/thumb?name=${encodeURIComponent(item.name)}&v=${item.mtime ?? 0}-${item.bytes ?? 0}`;
  }

  /** Sends a recording to the bridge's footage/added/; `progress` gets 0..1 as it uploads. */
  upload(file: File, about: NewRecording, progress: (share: number) => void): Promise<FootageItem> {
    const query = new URLSearchParams({ filename: file.name, title: about.title, specimen: about.specimen, microscope: about.microscope ? '1' : '0' });
    if (about.objective) query.set('objective', String(about.objective));
    if (about.eyepiece) query.set('eyepiece', String(about.eyepiece));
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest();
      request.open('POST', `${this.bridgeUrl}/assets/upload?${query}`);
      request.setRequestHeader('X-MicroscopeMind', 'add-footage');
      request.setRequestHeader('Content-Type', 'application/octet-stream');
      request.upload.onprogress = (event) => event.lengthComputable && progress(event.loaded / event.total);
      request.onload = () => {
        try {
          const body = JSON.parse(request.responseText) as { ok: boolean; item?: FootageItem; error?: string };
          if (request.status === 200 && body.ok && body.item) {
            this.libraryVersion = ''; // fetch the library again: the new recording is in it
            void this.loadLibrary();
            resolve(body.item);
          } else reject(new Error(body.error ?? `The bridge answered ${request.status}`));
        } catch {
          reject(new Error(`The bridge answered ${request.status}`));
        }
      };
      request.onerror = () => reject(new Error('The camera bridge did not answer.'));
      request.send(file);
    });
  }

  /** Holds or plays on the recording (the player's pause): paused, frames keep coming, one picture. */
  async pause(paused: boolean): Promise<string | null> {
    try {
      const response = await fetch(`${this.bridgeUrl}/feed/pause?paused=${paused ? 1 : 0}`, { method: 'POST' });
      const body = (await response.json()) as FeedState & { ok?: boolean; error?: string };
      if (!response.ok || body.ok === false) return body.error ?? 'Could not pause the recording.';
      this.state = body;
      this.playbackAt = performance.now();
      this.onChange();
      return null;
    } catch {
      return 'The camera bridge did not answer.';
    }
  }

  /** Moves the playing recording to `seconds` (the playhead). */
  async seek(seconds: number): Promise<string | null> {
    try {
      const response = await fetch(`${this.bridgeUrl}/feed/seek?seconds=${seconds.toFixed(2)}`, { method: 'POST' });
      const body = (await response.json()) as FeedState & { ok?: boolean; error?: string };
      if (!response.ok || body.ok === false) return body.error ?? 'Could not move in the recording.';
      this.state = body;
      this.playbackAt = performance.now();
      this.onChange();
      return null;
    } catch {
      return 'The camera bridge did not answer.';
    }
  }

  /** Plays test footage (by its library name) into the live feed, or the camera (null). `at` starts it there and
   * `paused` holds it at once (one call, so a returning session never flashes playing). */
  use(footage: string | null, options: PlayOptions = {}): Promise<string | null> {
    if (!footage) return this.switchTo({ kind: 'camera' });
    return this.switchTo({ kind: 'footage', name: footage, ...options });
  }

  private async switchTo(pick: FeedPick): Promise<string | null> {
    // Shown at once: the name of what was picked, while the bridge switches (about half a second).
    this.choosing = pick.kind === 'footage' ? (this.library.find((i) => i.name === pick.name) ?? null) : null;
    if (this.busy === 'switching') {
      // A switch is running: this one goes next, and only the last one asked for (no pile-up).
      this.wanted = pick;
      this.onChange();
      return null;
    }
    this.busy = 'switching';
    this.onChange();
    try {
      const response = await fetch(`${this.bridgeUrl}/feed?${feedQuery(pick)}`, { method: 'POST' });
      const body = (await response.json()) as FeedState & { error?: string };
      if (!response.ok) return body.error ?? 'Could not switch the feed.';
      this.state = body;
      this.playbackAt = performance.now();
      return null;
    } catch {
      return 'The camera bridge did not answer.';
    } finally {
      this.busy = null;
      const next = this.wanted;
      this.wanted = undefined;
      const playing = this.state?.source === 'footage' ? this.state.footage : null;
      // The last pick asked for meanwhile, unless it is what plays now (one that says where to start always goes).
      if (next !== undefined && (pickKey(next) !== playing || (next.kind !== 'camera' && (next.at !== undefined || next.paused !== undefined)))) void this.switchTo(next);
      else this.choosing = null;
      this.onChange();
    }
  }

  /** The bridge's hard reconnect: resets Continuity Camera if needed, then asks for frames. */
  async reconnect(): Promise<string> {
    this.busy = 'reconnecting';
    this.onChange();
    try {
      const response = await fetch(`${this.bridgeUrl}/api/restart`, { method: 'POST' });
      const body = (await response.json()) as { message?: string };
      return body.message ?? 'Reconnect requested.';
    } catch {
      return 'The camera bridge did not answer.';
    } finally {
      this.busy = null;
      await this.refresh(true);
    }
  }
}

export function duration(seconds: number | null): string {
  if (seconds === null) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return `${m}:${String(s).padStart(2, '0')}`;
}

/** When the Mac sees the phone but no picture arrives yet. */

export const PICTURE_STEPS = [
  'Lock the iPhone and keep it mounted on the eyepiece. Continuity Camera only sends a picture while the phone is locked and close by.',
  'Press Reconnect. If a prompt appears on the phone, accept it.',
];

/** Steps to get the iPhone live, shown when no camera frames arrive. */
export const CAMERA_STEPS = [
  'Connect the iPhone to this Mac with a USB cable and unlock it once.',
  'Lock it and mount it on the eyepiece. Continuity Camera only works while the phone is locked and close by.',
  'Press Reconnect. The first connection can take about 15 seconds.',
];
