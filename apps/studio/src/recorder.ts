import type { AnnotatedCapture } from './annotated-recording';
// Record explicitly chooses the raw camera, the existing annotated input canvas, or
// the whole workspace. Browser media uses the same bounded chunk upload and finalization;
// annotated capture requests neither screen sharing nor audio. Workspace can also retain
// the bridge's raw camera under its existing studio owner.
//
// Record is also the dataset's switch (ADR 0009): `begin` starts the dataset once you have chosen
// the tab, before anything is recorded, and `end` stops it after the studio's file is saved, so the
// dataset's edges are around the recording, never inside it.

export type RecordingOptions = { mode: 'camera' | 'annotated' | 'workspace'; microphone: boolean; appAudio: boolean };

type State = 'idle' | 'starting' | 'recording' | 'saving';

const CHUNK_MS = 3000;
type Channel = 'screen' | 'tabAudio' | 'microphone' | 'camera' | 'upload';
type ChannelState = 'available' | 'unavailable' | 'muted' | 'ended' | 'requested' | 'captured' | 'failed';

export class StudioRecorder {
  state: State = 'idle';
  cameraStopUnconfirmed = false;
  startedAt = 0;
  private datasetStarted = false;
  private annotatedCapture: AnnotatedCapture | null = null;
  private captureEndReason = '';
  private mode: RecordingOptions['mode'] = 'workspace';
  /** What the recording in progress records: the screen (workspace, annotated) or the camera source alone. */
  get scope(): RecordingOptions['mode'] { return this.mode; }
  readonly channels: Record<Channel, ChannelState> = {
    screen: 'unavailable', tabAudio: 'unavailable', microphone: 'unavailable', camera: 'unavailable', upload: 'unavailable',
  };
  private channelWrites: Promise<unknown> = Promise.resolve();
  private uploadFailed = false;
  private channelHistoryFailed = false;
  private cameraRequested = false;
  private cameraStart: Promise<void> = Promise.resolve();
  private cameraCapture: { path: string; frames: number } | null = null;
  private finishing: Promise<void> | null = null;
  private recorder: MediaRecorder | null = null;
  private streams: MediaStream[] = [];
  private audio: AudioContext | null = null;
  private id = '';
  private uploads: Promise<unknown> = Promise.resolve();
  private readonly bridgeUrl: () => string;
  private readonly onChange: (message?: string, error?: boolean) => void;
  private readonly annotated?: {start: (ended: (reason: string) => void) => AnnotatedCapture};
  private readonly around: { begin: () => Promise<void | boolean>; end: () => Promise<void | boolean> };

  constructor(
    bridgeUrl: () => string,
    onChange: (message?: string, error?: boolean) => void,
    around: { begin: () => Promise<void | boolean>; end: () => Promise<void | boolean> } = { begin: async () => {}, end: async () => {} },
    annotated?: {start: (ended: (reason: string) => void) => AnnotatedCapture},
  ) {
    this.bridgeUrl = bridgeUrl;
    this.onChange = onChange;
    this.around = around;
    this.annotated = annotated;
  }

  /** The camera bridge stops recording for the studio (also after a reload, when this page no longer
   * holds the recording it started: the bridge would otherwise go on recording for it). */
  async releaseCamera(): Promise<{ ok?: boolean; error?: string; sidecarError?: string; path?: string | null; frames?: number; stopped?: boolean; owners?: string[] } | null> {
    this.cameraStopUnconfirmed = true;
    try {
      const response = await fetch(`${this.bridgeUrl()}/api/record/stop?owner=studio`, { method: 'POST', signal: AbortSignal.timeout(10000) });
      if (!response.ok) return null;
      const result = await response.json() as { ok?: boolean; error?: string; sidecarError?: string; path?: string | null; frames?: number; stopped?: boolean; owners?: string[] };
      this.cameraStopUnconfirmed = !Array.isArray(result.owners) || result.owners.includes('studio');
      return result;
    } catch { return null; }
  }

  async start(options: RecordingOptions = { mode: 'workspace', microphone: true, appAudio: true }): Promise<void> {
    if (this.state !== 'idle') return;
    if (this.cameraStopUnconfirmed) { this.onChange('Camera Stop is unconfirmed. Press Stop again before starting another recording.', true); return; }
    this.mode = options.mode;
    this.captureEndReason = '';
    if(options.mode==='annotated')options={...options,microphone:false,appAudio:false};
    this.datasetStarted = false;
    this.id = '';
    this.cameraStart = Promise.resolve();
    this.cameraCapture = null;
    this.finishing = null;
    this.uploadFailed = false;
    this.channelHistoryFailed = false;
    this.cameraRequested = false;
    this.uploads = Promise.resolve();
    this.channelWrites = Promise.resolve();
    for (const channel of Object.keys(this.channels) as Channel[]) this.channels[channel] = 'unavailable';
    this.state = 'starting';
    if (options.mode === 'camera') { await this.startCameraOnly(); return; }
    this.onChange(options.mode==='annotated'?'Starting the annotated input view. No window chooser or audio.':'Choose only the Hooke window or tab in the system chooser to record your workspace.');
    let display: MediaStream;
    try {
      if(options.mode==='annotated'){
        if(!this.annotated)throw new Error('Annotated recording is unavailable.');
        this.annotatedCapture=this.annotated.start(reason=>{this.captureEndReason=reason;this.stop();});
        display=this.annotatedCapture.stream;
      }else{
      // Chrome offers this tab first; its audio carries the voice you hear.
      display = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: 30 },
        audio: options.appAudio,
        preferCurrentTab: true,
        selfBrowserSurface: 'include',
        surfaceSwitching: 'exclude',
        systemAudio: 'exclude',
      } as DisplayMediaStreamOptions);
      }
    } catch(error) {
      this.cleanup();this.state = 'idle';
      this.onChange(options.mode==='annotated'&&error instanceof Error?error.message:'Recording was not started.', true);
      return;
    }
    this.streams.push(display);
    this.observe('screen', display.getVideoTracks()[0]);
    if (options.appAudio) this.observe('tabAudio', display.getAudioTracks()[0]);
    let stream: MediaStream;
    let type: string;
    let ext: string;
    let datasetAttempted = false;
    try {
      // Your voice too (the tab's audio has only the app's): mixed into one track.
      const ctx = options.appAudio || options.microphone ? new AudioContext() : null;
      this.audio = ctx;
      const mix = ctx?.createMediaStreamDestination();
      if (options.appAudio && display.getAudioTracks().length && ctx && mix) ctx.createMediaStreamSource(display).connect(mix);
      try {
        if (options.microphone && ctx && mix) {
        const mic = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        this.streams.push(mic);
        ctx.createMediaStreamSource(mic).connect(mix);
        this.observe('microphone', mic.getAudioTracks()[0]);
        }
      } catch {
        // Microphone unavailable; tab audio is independent and may also be absent.
      }
      stream = new MediaStream([...display.getVideoTracks(), ...(mix?.stream.getAudioTracks() ?? [])]);
      if (options.mode === 'annotated') {
        // The view canvas may resize. avc1 cannot change its codec description mid-recording;
        // avc3 carries those changes in-band. Never fall back to an unspecified MP4 codec here.
        type = ['video/mp4;codecs=avc3.42E01E', 'video/webm;codecs=vp9', 'video/webm;codecs=vp8']
          .find(t => MediaRecorder.isTypeSupported(t)) ?? '';
        if (!type) throw new Error('This browser cannot encode the annotated view. Try Workspace recording instead.');
        ext = type.startsWith('video/mp4') ? 'mp4' : 'webm';
      } else {
        const mp4 = ['video/mp4;codecs=avc1.42E01E,mp4a.40.2', 'video/mp4'].find((t) => MediaRecorder.isTypeSupported(t));
        type = mp4 ?? ['video/webm;codecs=vp9,opus', 'video/webm'].find((t) => MediaRecorder.isTypeSupported(t)) ?? '';
        ext = mp4 ? 'mp4' : 'webm';
      }
      datasetAttempted = true;
      this.datasetStarted = await this.around.begin() !== false; // the dataset first: nothing recorded falls outside it
      if(this.captureEndReason)throw new Error(this.captureEndReason);
      const started = await this.post('/api/recordings/start', JSON.stringify({ ext, scope: options.mode }), 'application/json');
      this.id = String(started.id);
      for (const channel of Object.keys(this.channels) as Channel[]) this.channel(channel, this.channels[channel]);
    } catch (error) {
      this.cleanup();
      this.state = 'idle';
      this.onChange(options.mode === 'annotated' && error instanceof Error
        ? error.message : 'Could not start saving the recording.', true);
      if (datasetAttempted) {
        try { await this.around.end(); } catch { /* Original startup failure remains visible. */ }
      }
      return;
    }
    let recorder: MediaRecorder;
    try {
      if(this.captureEndReason)throw new Error(this.captureEndReason);
      recorder = new MediaRecorder(stream, { mimeType: type, videoBitsPerSecond: 6_000_000 });
    } catch {
      this.uploadFailed = true;
      this.channel('screen', 'failed');
      await this.finish();
      return;
    }
    this.recorder = recorder;
    recorder.ondataavailable = (event) => {
      if (!event.data.size) return;
      const id = this.id;
      // In order, one after another, so the file is written as recorded.
      this.uploads = this.uploads.then(async () => {
        try {
          await this.post(`/api/recordings/${id}/append`, event.data, 'application/octet-stream');
        } catch {
          this.uploadFailed = true;
          this.channel('upload', 'failed');
          this.onChange('Recording upload failed. Stopping; the saved file may have gaps.', true);
          this.stop();
        }
      });
    };
    recorder.onstop = () => { if (this.recorder === recorder) void this.finish(); };
    display.getVideoTracks()[0]?.addEventListener('ended', () => {
      if (this.streams.includes(display)) this.stop();
    }); // "Stop sharing" in Chrome
    recorder.onerror = () => {
      this.uploadFailed = true;
      this.channel('screen', 'failed');
      this.onChange('The browser recorder failed; the saved recording may be incomplete.', true);
      this.stop();
    };
    try { recorder.start(CHUNK_MS); } catch {
      this.uploadFailed = true;
      this.channel('screen', 'failed');
      await this.finish();
      return;
    }
    this.startedAt = performance.now();
    this.state = 'recording';
    // The camera's raw feed alongside (the bridge writes it; not with test footage alone). The
    // studio is one owner of that recording: its Stop leaves a recording `make demo-start` began.
    this.channel('upload', 'available');
    if(options.mode==='workspace'){
    this.cameraRequested = true;
    this.cameraStart = fetch(`${this.bridgeUrl()}/api/record/start?owner=studio`, { method: 'POST', signal: AbortSignal.timeout(10000) })
      .then(async (response) => {
        const result = await response.json() as { ok?: boolean; path?: string; frames?: number };
        if (response.ok && result.ok && result.path) this.cameraCapture = { path: result.path, frames: result.frames ?? 0 };
        this.channel('camera', this.cameraCapture ? 'requested' : 'failed');
        this.onChange(this.summary());
      }).catch(() => { this.channel('camera', 'failed'); this.onChange(this.summary(), true); });
    }
    this.onChange(this.summary());
  }

  private async startCameraOnly(): Promise<void> {
    this.onChange('Starting camera recording. No window sharing or microphone is requested.');
    try {
      this.datasetStarted = await this.around.begin() !== false;
      this.cameraRequested = true;
      const response = await fetch(`${this.bridgeUrl()}/api/record/start?owner=studio`, { method: 'POST', signal: AbortSignal.timeout(10000) });
      const result = await response.json() as { ok?: boolean; path?: string; frames?: number };
      if (!response.ok || !result.ok || !result.path) throw new Error('Camera capture unavailable');
      this.cameraCapture = { path: result.path, frames: result.frames ?? 0 };
      this.channel('camera', 'requested');
      this.startedAt = performance.now(); this.state = 'recording';
      this.onChange(this.datasetStarted ? 'Recording the camera source. No workspace or microphone audio is included.' : 'Recording the camera source without a confirmed dataset. Data shows what is missing.', !this.datasetStarted);
    } catch {
      this.channel('camera', 'failed');
      await this.finish();
    }
  }

  stop(): void {
    if (this.state !== 'recording') return;
    if (this.mode === 'camera') {
      this.state = 'saving'; this.onChange('Finishing the camera recording…');
      void this.finish(); return;
    }
    if (!this.recorder) return;
    this.state = 'saving';
    this.annotatedCapture?.dispose();
    this.onChange('Saving the recording…');
    if (this.recorder.state !== 'inactive') this.recorder.stop();
    // An inactive recorder's error is followed by final data and onstop; wait for them.
  }

  private finish(): Promise<void> {
    return this.finishing ??= this.finishOnce();
  }

  private async finishOnce(): Promise<void> {
    await this.cameraStart;
    const cameraStop = this.cameraRequested ? await this.releaseCamera() : null;
    const cameraFailed = this.cameraRequested && (this.cameraStopUnconfirmed || !cameraStop || cameraStop.ok === false || Boolean(cameraStop.error || cameraStop.sidecarError));
    const freshCameraFrames = this.cameraCapture && cameraStop?.path === this.cameraCapture.path
      && (cameraStop.frames ?? 0) > this.cameraCapture.frames;
    this.channel('camera', cameraFailed ? 'failed' : freshCameraFrames ? 'captured' : this.channels.camera === 'failed' ? 'failed' : 'unavailable');
    if (this.mode === 'camera') {
      let datasetEnded = true;
      try { datasetEnded = await this.around.end() !== false; } catch { datasetEnded = false; }
      this.cleanup(); this.state = 'idle';
      this.onChange(this.cameraStopUnconfirmed
        ? 'Camera Stop is unconfirmed. Press Stop again to release this recording; other recording owners are preserved.'
        : !datasetEnded
        ? 'The dataset could not be stopped. Press Stop again to finish collection; retained camera footage is in Data.'
        : !this.datasetStarted
          ? 'Camera recording stopped without a confirmed dataset. Open Data for retained footage and gaps.'
        : !cameraFailed && freshCameraFrames
          ? 'Camera frames recorded. Open Data to check the saved movie and frame history.'
          : 'Camera recording could not be verified. Open Data for retained footage and recovery details.',
      cameraFailed || !freshCameraFrames || !datasetEnded || !this.datasetStarted);
      return;
    }
    await this.uploads;
    for (const channel of ['screen', 'tabAudio', 'microphone'] as Channel[]) {
      if (this.channels[channel] === 'available') this.channel(channel, 'ended');
    }
    await this.channelWrites;
    let saved: { ok?: boolean; playable?: string; folder?: string; error?: string } = {};
    try {
      saved = await this.post(`/api/recordings/${this.id}/stop`, JSON.stringify({ channelHistoryIncomplete: this.channelHistoryFailed }), 'application/json');
    } catch {
      saved = { ok: false, error: 'the orchestrator did not answer' };
    }
    try {
      await this.around.end(); // the dataset last, after the studio's file is saved
    } catch {
      this.uploadFailed = true;
    } finally {
      this.cleanup();
      this.state = 'idle';
    }
    const name = (path?: string | null) => (path ? path.split('/').pop() : '');
    if (saved.ok && !this.uploadFailed && this.channelHistoryFailed) {
      this.onChange('Recording media was saved, but channel history has gaps. Replay availability is unqualified.', true);
    } else if (saved.ok && !this.uploadFailed && cameraFailed) {
      this.onChange('Studio recording saved. Camera capture or its frame-time sidecar failed; available footage is incomplete.', true);
    } else if (saved.ok && !this.uploadFailed && this.mode==='annotated') {
      this.onChange(`Saved the annotated view (${name(saved.playable)}).${this.captureEndReason?` Stopped because ${this.captureEndReason}`:''} Checking the dataset…`);
    } else if (saved.ok && !this.uploadFailed) {
      // The camera video and the dataset are checked by the Data light, which says when they are saved.
      this.onChange(`Saved the studio recording (${name(saved.playable)}). Checking the dataset…`);
    } else {
      this.onChange(this.uploadFailed ? 'Recording stopped with gaps. Available chunks were kept; this is not a complete replay.'
        : `The studio recording was not saved: ${saved.error ?? 'unknown error'}.`, true);
    }
  }

  private cleanup(): void {
    this.annotatedCapture?.dispose();this.annotatedCapture=null;
    for (const s of this.streams) for (const t of s.getTracks()) t.stop();
    this.streams = [];
    void this.audio?.close();
    this.audio = null;
    this.recorder = null;
  }

  summary(): string {
    if (this.mode === 'camera') return `Camera source: ${this.channels.camera === 'requested' ? 'awaiting capture confirmation' : this.channels.camera} · No workspace or microphone`;
    const labels: Record<Channel, string> = { screen: this.mode==='annotated'?'Annotated view':'Workspace', tabAudio: 'Tab audio', microphone: 'Microphone', camera: 'Camera', upload: 'Saving' };
    return (Object.keys(labels) as Channel[]).map(channel => `${labels[channel]}: ${this.channels[channel] === 'requested' ? 'awaiting capture confirmation' : this.channels[channel]}`).join(' · ');
  }

  private observe(channel: Channel, track: MediaStreamTrack | undefined): void {
    if (!track) return;
    this.channels[channel] = track.readyState === 'live' ? track.muted ? 'muted' : 'available' : 'ended';
    for (const event of ['ended', 'mute', 'unmute']) track.addEventListener(event, () => {
      if (!this.streams.some(stream => stream.getTracks().includes(track))) return;
      this.channel(channel, track.readyState !== 'live' ? 'ended' : track.muted ? 'muted' : 'available');
      this.onChange(this.summary());
    });
  }

  private channel(channel: Channel, state: ChannelState): void {
    this.channels[channel] = state;
    if (!this.id) return;
    const id = this.id;
    this.channelWrites = this.channelWrites.then(() => this.post(`/api/recordings/${id}/channels`,
      JSON.stringify({ channel, state, ...(this.channelHistoryFailed ? { historyGap: true } : {}) }), 'application/json')).catch(() => {
        this.channelHistoryFailed = true;
        this.onChange('Recording channel history could not be saved; replay availability is unqualified.', true);
      });
  }

  private async post(path: string, body: BodyInit, type: string): Promise<Record<string, unknown>> {
    const response = await fetch(path, { method: 'POST', body, headers: { 'Content-Type': type }, signal: AbortSignal.timeout(15000) });
    if (!response.ok) throw new Error(String(response.status));
    return (await response.json()) as Record<string, unknown>;
  }
}
