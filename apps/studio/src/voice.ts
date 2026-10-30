// Talking with the voice model over /ws/voice (services/orchestrator/voice/route.py).
// Microphone: 16-bit mono PCM at 24 kHz (the model's own rate, so nothing resamples it again)
// in ~40 ms chunks; the browser converts the microphone to 24 kHz with its own filtered
// resampler, so speech reaches the model's transcription without aliasing. Speech back: "MMA1" + 16-bit mono
// PCM at 24 kHz, played gap-free and flushed the moment the model is interrupted. The model
// sees the eyepiece through the orchestrator, not through the browser.
//
// The one-brain voice (PARTNER_VOICE, WebRTC only) adds its own messages on the same socket (protocol.ts
// PartnerMessage): hold to talk (talkDown/talkUp/talkCancel; the microphone is on only while held, and a little after
// so the last word reaches the model), barge-in (pressing while it speaks silences its audio element at once), the
// window snapshots and marks it asks for (handed to `onPartner`), and a fresh offer when its call has to be renewed.
import { betterMicrophone } from './mic-choice';
import { PARTNER_MESSAGES, studioSocketURL, type PartnerCommand, type PartnerMessage } from './protocol';

/** The voice that hears, sees and acts in one model (options.py VOICE "gpt-realtime-2.1"). */
export const PARTNER_VOICE = 'gpt-realtime-2.1';
/** After release the microphone stays on this long: the last word is still in flight when the server commits. */
export const MIC_TAIL_MS = 300;
/** A barge-in keeps the voice silent at least this long, so the cancelled reply's last audio is never heard. */
export const BARGE_MIN_MS = 400;
/** A connection disconnected this long (not failed) is offered again: a short blip often comes back by itself. */
export const BLIP_MS = 3_000;
/** A new call is offered again this often while none answers and connects. */
export const RECOVER_RETRY_MS = 3_500;
/** After this long without a connection, Talk ends and says so. */
export const RECOVER_MS = 10_000;
export const RECOVER_FAILED = 'Voice lost its connection and could not get it back. Your conversation is kept. Press Talk again to reconnect.';

export interface VoiceEvent {
  type: 'voice';
  kind: string;
  text?: string;
  name?: string;
  callId?: string;
  args?: Record<string, unknown>;
  result?: Record<string, unknown>;
  data?: Record<string, unknown>;
}

export type VoiceState = 'off' | 'connecting' | 'live' | 'error';

export interface VoiceTarget {
  model?: string;
  trial?: string;
  arm?: string;
  connectionGrant?: string;
}

const AUDIO_MAGIC = 'MMA1';
const OUTPUT_RATE = 24000;
const INPUT_RATE = 24000;

// Runs on the audio thread: convert to 16-bit at 24 kHz, post 40 ms chunks. The capture context
// runs at 24 kHz, so this is a plain copy; if a browser refuses that rate, each output sample
// averages the input samples it covers (a box filter, so higher frequencies do not fold back).
const CAPTURE_WORKLET = `
class Capture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / ${INPUT_RATE};
    this.pos = 0;
    this.chunk = new Int16Array(${(INPUT_RATE * 40) / 1000});
    this.fill = 0;
    this.level = 0;
  }
  process(inputs) {
    const input = inputs[0] && inputs[0][0];
    if (!input) return true;
    for (let i = 0; i < input.length; i++) this.level = Math.max(this.level * 0.9995, Math.abs(input[i]));
    while (this.pos < input.length) {
      const i = Math.floor(this.pos);
      const end = Math.min(input.length, Math.max(i + 1, Math.floor(this.pos + this.ratio)));
      let sum = 0;
      for (let k = i; k < end; k++) sum += input[k];
      const v = Math.max(-1, Math.min(1, sum / (end - i)));
      this.chunk[this.fill++] = v < 0 ? v * 0x8000 : v * 0x7fff;
      if (this.fill === this.chunk.length) {
        this.port.postMessage({ pcm: this.chunk.buffer, level: this.level }, [this.chunk.buffer]);
        this.chunk = new Int16Array(${(INPUT_RATE * 40) / 1000});
        this.fill = 0;
      }
      this.pos += this.ratio;
    }
    this.pos -= input.length;
    return true;
  }
}
registerProcessor('capture', Capture);
`;

/** The microphone was refused: Talk's card says how to turn it back on (mic-card.ts). */
export const MIC_BLOCKED = 'The microphone is off for Hooke. Turn it on, then start Talk again.';

export class VoiceClient {
  state: VoiceState = 'off';
  muted = false;
  /** Waiting for the browser's (or the Mac's) microphone prompt: shown as the microphone card, never as connecting. */
  awaitingMic = false;
  /** The last start was refused the microphone. */
  micBlocked = false;
  /** How audio travels (the server's VOICELINK option): "relay" through the app over a WebSocket,
   * or "webrtc" directly between this browser and OpenAI (the app keeps a sideband for events). */
  link: 'relay' | 'webrtc' = 'relay';
  private peer: RTCPeerConnection | null = null;
  private remote: HTMLAudioElement | null = null;
  private mediaConnected = false;
  private mediaTimer: ReturnType<typeof setInterval> | null = null;
  private disconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private outputSpeaking = false;
  private outputLastHeard = 0;
  private outputProbe: MediaStreamAudioSourceNode | null = null;
  private socket: WebSocket | null = null;
  private mic: MediaStream | null = null;
  private micContext: AudioContext | null = null;
  private speaker: AudioContext | null = null;
  private capture: AudioWorkletNode | null = null;
  private silent: GainNode | null = null;
  private providerReady = false;
  private captured = false;
  private stageSent = new Set<string>();
  private pendingStages = new Set<string>();
  private responseTimer: ReturnType<typeof setTimeout> | null = null;
  private captureTimer: ReturnType<typeof setTimeout> | null = null;
  private startupTimer: ReturnType<typeof setTimeout> | null = null;
  private permissionTimer: ReturnType<typeof setTimeout> | null = null;
  private playing = new Set<AudioBufferSourceNode>();
  private audible = new Set<AudioBufferSourceNode>();
  private playAt = 0;
  /** How this session hears you (its model announcement): "hold" (hold to talk), "open", or null (GPT-Live); undefined
   * until the session has announced its model. */
  talkMode: 'hold' | 'open' | null | undefined = undefined;
  /** The partner's own messages (snapshot requests, marks): main.ts answers them. */
  onPartner: ((message: PartnerMessage) => void) | null = null;
  /** The voice's audio silenced (true) by a press, or heard again (false). */
  onOutput: ((muted: boolean) => void) | null = null;
  outputMuted = false;
  private outputMutedAt = 0;
  private unmuteTimer: ReturnType<typeof setTimeout> | null = null;
  private micOpen = false;
  private micTimer: ReturnType<typeof setTimeout> | null = null;
  private nextPeer: RTCPeerConnection | null = null;
  /** The id of the offer the pending connection (nextPeer) waits on: the server's reoffer id, or our own rc-<n>. */
  private nextOfferId: string | null = null;
  /** Getting the call back after the connection dropped (a Wi-Fi drop): offered again until it connects or gives up. */
  private recovery: { deadline: ReturnType<typeof setTimeout>; retry: ReturnType<typeof setTimeout> | null } | null = null;
  private recoverN = 0;
  private offerToken = 0; // the newest offer being made: an older one finishing late is dropped
  private saidSpeaking: boolean | null = null;
  private readonly onEvent: (event: VoiceEvent) => void;
  private readonly onState: (state: VoiceState, detail?: string) => void;
  private readonly onLevel: (level: number) => void;

  constructor(
    onEvent: (event: VoiceEvent) => void,
    onState: (state: VoiceState, detail?: string) => void,
    onLevel: (level: number) => void,
  ) {
    this.onEvent = onEvent;
    this.onState = onState;
    this.onLevel = onLevel;
  }

  /** `target` says which voice to talk to: a model id, or a blind arena arm. It may be a
   * promise, so the microphone and speaker can be opened inside the click while the
   * arena trial is still being created. */
  private connectionAttempt = 0;

  /** A phone the Mac made its default input (Continuity) gives way to the Mac's own microphone (mic-choice.ts).
   * Anything that goes wrong keeps the microphone already open. */
  private async ownMicrophone(mic: MediaStream, audio: MediaTrackConstraints): Promise<MediaStream> {
    try {
      const label = mic.getAudioTracks()[0]?.label ?? '';
      const better = betterMicrophone(label, label ? await navigator.mediaDevices.enumerateDevices() : []);
      if (!better) return mic;
      const own = await navigator.mediaDevices.getUserMedia({ audio: { ...audio, deviceId: { exact: better } } });
      mic.getTracks().forEach((track) => track.stop());
      return own;
    } catch {
      return mic;
    }
  }

  async start(target?: VoiceTarget | Promise<VoiceTarget>): Promise<void> {
    if (this.state === 'connecting' || this.state === 'live') return;
    const attempt = ++this.connectionAttempt;
    this.providerReady = false; this.captured = false; this.mediaConnected = false;
    this.stageSent.clear(); this.pendingStages.clear();
    this.talkMode = undefined; this.micOpen = false;
    this.micBlocked = false;
    this.awaitingMic = true; // the microphone first: until it is on, this is the card's wait, not "Connecting"
    this.setState('connecting');
    try {
      // Both contexts are created inside the click that started the conversation, so the
      // browser lets them play and record.
      this.speaker = new AudioContext({ sampleRate: OUTPUT_RATE });
      // Resume playback during the gesture, before awaiting microphone permission (WebKit).
      try { this.micContext = new AudioContext({ sampleRate: INPUT_RATE }); }
      catch { this.micContext = new AudioContext(); }
      const audioReady = Promise.all([this.speaker.resume(), this.micContext.resume()]);
      void audioReady.catch(() => {});
      this.permissionTimer = setTimeout(() => {
        if (attempt === this.connectionAttempt) this.setState('connecting',
          'Waiting for microphone access. Allow the microphone prompt, or end Talk and check microphone permissions.');
      }, 8_000);
      let mic: MediaStream;
      const audio = { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true };
      try {
        mic = await navigator.mediaDevices.getUserMedia({ audio });
        if (attempt === this.connectionAttempt) mic = await this.ownMicrophone(mic, audio);
      } finally {
        this.awaitingMic = false;
      }
      if (attempt !== this.connectionAttempt) { mic.getTracks().forEach((track) => track.stop()); return; }
      if (this.permissionTimer !== null) clearTimeout(this.permissionTimer);
      this.permissionTimer = null;
      this.setState('connecting'); // the microphone is on: what is shown now is the real connect
      this.mic = mic;
      await audioReady;
      if (attempt !== this.connectionAttempt) return;
      const url = URL.createObjectURL(new Blob([CAPTURE_WORKLET], { type: 'text/javascript' }));
      try { await this.micContext.audioWorklet.addModule(url); }
      finally { URL.revokeObjectURL(url); }
      if (attempt !== this.connectionAttempt) return;
      const source = this.micContext.createMediaStreamSource(this.mic);
      const capture = new AudioWorkletNode(this.micContext, 'capture');
      this.capture = capture;
      capture.port.onmessage = (event: MessageEvent<{ pcm: ArrayBuffer; level: number }>) => {
        if (attempt !== this.connectionAttempt) return;
        this.captured = true;
        this.report('capture');
        if (!this.muted && event.data.level > 0.02) this.report('signal');
        this.maybeReady();
        if (this.captureTimer !== null) clearTimeout(this.captureTimer);
        this.captureTimer = setTimeout(() => {
          if (attempt === this.connectionAttempt && !this.muted) this.report('capture-stalled');
        }, 5_000);
        this.onLevel(this.muted ? 0 : event.data.level);
        // Over WebRTC the microphone goes to OpenAI on its own track; here it only drives the meter.
        // The provider must acknowledge session.start before accepting input. Do not
        // retain pre-ready speech and replay it after setup or into another session.
        if (this.providerReady && !this.peer && !this.muted && this.socket?.readyState === WebSocket.OPEN) this.socket.send(event.data.pcm);
      };
      source.connect(capture);
      // Keep the worklet in the rendered graph. Silence prevents microphone feedback.
      this.silent = this.micContext.createGain();
      this.silent.gain.value = 0;
      capture.connect(this.silent);
      this.silent.connect(this.micContext.destination);
      this.mic.getAudioTracks().forEach(track => track.addEventListener('ended', () => {
        if (attempt !== this.connectionAttempt) return;
        this.fail('The microphone disconnected. Choose an available microphone, then start Talk again.');
      }));
    } catch (error) {
      this.awaitingMic = false;
      if (attempt !== this.connectionAttempt) return;
      this.cleanup();
      const denied = error instanceof DOMException && error.name === 'NotAllowedError';
      this.micBlocked = denied;
      this.setState('error', denied ? MIC_BLOCKED : String(error));
      return;
    }

    let resolved: VoiceTarget = {};
    try {
      resolved = (await target) ?? {};
    } catch (error) {
      if (attempt !== this.connectionAttempt) return;
      this.cleanup();
      this.setState('error', String(error));
      return;
    }
    if (attempt !== this.connectionAttempt) return;
    const { connectionGrant, ...queryTarget } = resolved;
    const params = new URLSearchParams(Object.entries(queryTarget).filter(([, v]) => v) as [string, string][]);
    params.set('rate', String(INPUT_RATE));
    try {
      let offer: { sdp: string } | null = null;
      // The one-brain voice talks over WebRTC only (the server refuses the relay for it).
      if ((this.link === 'webrtc' || resolved.model === PARTNER_VOICE) && this.mic) {
        // OpenAI's browser flow (voice-webrtc guide): the microphone on a track, a data channel named
        // "oai-events", and the offer exchanged through the app, which holds the API key.
        const made = await this.offerPeer(attempt);
        if (!made || attempt !== this.connectionAttempt) return;
        this.peer = made.peer;
        offer = { sdp: made.sdp };
        params.set('link', 'webrtc');
      }
      const socket = new WebSocket(studioSocketURL('/ws/voice', params.toString()));
      socket.binaryType = 'arraybuffer';
      socket.onopen = () => {
        if (attempt !== this.connectionAttempt) { socket.close(); return; }
        if (connectionGrant) socket.send(JSON.stringify({ type: 'workspace-bind', connectionGrant }));
        if (offer) socket.send(JSON.stringify({ type: 'offer', sdp: offer.sdp }));
        for (const stage of this.pendingStages) socket.send(JSON.stringify({ type: 'diagnostic', stage }));
        this.pendingStages.clear();
      };
      socket.onmessage = (event) => {
        if (attempt !== this.connectionAttempt) return;
        if (typeof event.data === 'string') {
          let message: VoiceEvent;
          try { message = JSON.parse(event.data) as VoiceEvent; }
          catch { this.fail('Voice sent an unreadable response. End Talk and reconnect.'); return; }
          if (message.kind === 'error') {
            this.onEvent(message);
            this.fail(String(message.data?.message || 'Voice failed. Check Activity and provider access, then start Talk again.'));
            return;
          }
          const partner = message as unknown as PartnerMessage;
          if (PARTNER_MESSAGES.includes(partner.type)) {
            if (partner.type === 'reoffer') void this.reoffer(partner.id, attempt);
            else this.onPartner?.(partner);
            return;
          }
          if (message.kind === 'answer' && (this.nextPeer || this.peer)) {
            // An answer naming an offer we no longer wait on (a slow one, already offered again) is not applied.
            const id = message.data?.id;
            if (id !== undefined && id !== null && String(id) !== this.nextOfferId) return;
            // A renewed call's answer goes to its new connection, which then takes over from the old one.
            const peer = (this.nextPeer ?? this.peer)!, renewing = peer === this.nextPeer;
            void peer.setRemoteDescription({ type: 'answer', sdp: String(message.data?.sdp ?? '') }).then(() => {
              if (renewing && attempt === this.connectionAttempt && this.nextPeer === peer) this.takeOver(peer);
            }).catch(() => {
              // While getting the call back, the next offer or the deadline decides; otherwise it is said now.
              if (attempt === this.connectionAttempt && !this.recovery) this.fail('Voice audio negotiation failed. Start Talk again to reconnect.');
            });
            return;
          }
          if (message.kind === 'model') {
            const talk = message.data?.talk;
            this.talkMode = talk === 'hold' ? 'hold' : talk === 'open' ? 'open' : null;
            this.applyMic();
          }
          if (message.kind === 'ready') { this.providerReady = true; this.maybeReady(); }
          if (message.kind === 'input_transcript') {
            this.phase('processing');
            if (this.responseTimer !== null) clearTimeout(this.responseTimer);
            this.responseTimer = setTimeout(() => this.report('response-wait'), 20_000);
          }
          if (['output_transcript', 'turn_complete', 'error', 'closed'].includes(message.kind)) this.clearResponseTimer();
          if (message.kind === 'turn_complete' && !this.outputSpeaking) this.phase(this.muted ? 'muted' : 'listening');
          if (message.kind === 'interrupted') { this.clearResponseTimer(); this.flush(); }
          this.onEvent(message);
          return;
        }
        this.play(event.data as ArrayBuffer);
      };
      socket.onclose = () => {
        if (attempt !== this.connectionAttempt) return;
        const wasLive = this.state === 'live' || this.state === 'connecting';
        this.cleanup();
        if (wasLive) this.setState('error', 'Voice disconnected. Your conversation is kept. Start Talk again to reconnect.');
      };
      socket.onerror = () => {
        if (attempt === this.connectionAttempt) this.fail('Voice could not connect. Check your connection and start Talk again.');
      };
      this.socket = socket;
      this.startupTimer = setTimeout(() => {
        if (attempt === this.connectionAttempt && this.state === 'connecting') {
          this.fail('Voice did not finish connecting. Check Activity and provider access, then start Talk again.');
        }
      }, 20_000);
    } catch {
      if (attempt === this.connectionAttempt) this.fail('Voice could not establish audio. Check microphone and connection access, then start Talk again.');
    }
  }

  /** A new peer connection for the call, its microphone track and audio element shared with any before it. */
  private async offerPeer(attempt: number): Promise<{ peer: RTCPeerConnection; sdp: string } | null> {
    const mic = this.mic;
    if (!mic) return null;
    const peer = new RTCPeerConnection();
    mic.getTracks().forEach((track) => peer.addTrack(track, mic));
    peer.createDataChannel('oai-events');
    peer.onconnectionstatechange = () => {
      // Only the connection in use speaks for the call: a renewed one's old connection may fail on its way out.
      if (attempt !== this.connectionAttempt || peer !== this.peer) return;
      this.mediaConnected = peer.connectionState === 'connected';
      if (this.disconnectTimer !== null) clearTimeout(this.disconnectTimer);
      this.disconnectTimer = null;
      if (this.mediaConnected) {
        this.recovered();
        if (this.state === 'live') this.phase(this.muted ? 'muted' : 'listening'); // back after a reconnect
        this.maybeReady(); return;
      }
      if (this.nextPeer && !this.recovery) return; // being renewed by the server: the new connection takes over
      if (this.state === 'live') {
        // A live call that lost its connection (a Wi-Fi drop) is offered again rather than ended.
        if (peer.connectionState === 'failed') this.recover(attempt);
        else if (peer.connectionState === 'disconnected') {
          this.phase('reconnecting');
          this.disconnectTimer = setTimeout(() => {
            if (attempt === this.connectionAttempt && !this.mediaConnected) this.recover(attempt);
          }, BLIP_MS);
        }
        return;
      }
      if (peer.connectionState === 'failed') this.fail('Voice audio disconnected. Your conversation is kept. Start Talk again to reconnect.');
      else if (peer.connectionState === 'disconnected') {
        this.phase('reconnecting');
        this.disconnectTimer = setTimeout(() => {
          if (attempt === this.connectionAttempt && !this.mediaConnected && !this.nextPeer) this.fail('Voice audio could not reconnect. Start Talk again.');
        }, 8_000);
      }
    };
    if (!this.remote) { this.remote = new Audio(); this.remote.autoplay = true; this.remote.muted = this.outputMuted; }
    const remote = this.remote;
    peer.ontrack = (event) => {
      if (attempt !== this.connectionAttempt || (peer !== this.peer && peer !== this.nextPeer)) return;
      const stream = new MediaStream([event.track]);
      remote.srcObject = stream;
      this.monitorOutput(stream);
      void remote.play().then(() => { if (attempt === this.connectionAttempt) this.report('playback'); }).catch(() => { if (attempt === this.connectionAttempt) this.report('playback-blocked'); });
    };
    const offer = await peer.createOffer();
    if (attempt !== this.connectionAttempt) { peer.close(); return null; }
    await peer.setLocalDescription(offer);
    if (attempt !== this.connectionAttempt) { peer.close(); return null; }
    await waitForIce(peer);
    if (attempt !== this.connectionAttempt) { peer.close(); return null; }
    const sdp = peer.localDescription?.sdp;
    if (!sdp) throw new Error('Missing voice offer');
    return { peer, sdp };
  }

  /** The server renews the call (its 60-minute limit, a provider error): a fresh offer on the same socket, the same
   * microphone and audio element; the old connection closes once the new answer is applied (takeOver). */
  private async reoffer(id: string, attempt: number): Promise<void> {
    if (!this.peer || !this.mic) return;
    if (this.nextPeer && !this.recovery) return; // already answering one
    if (this.disconnectTimer !== null) { clearTimeout(this.disconnectTimer); this.disconnectTimer = null; }
    try {
      if (!await this.offerCall(String(id), attempt)) return;
    } catch {
      if (attempt === this.connectionAttempt && !this.recovery) this.fail('Voice could not renew its audio connection. Start Talk again to reconnect.');
    }
  }

  /** A new connection offered on the socket under `id`, replacing any offer still waiting; false when overtaken. */
  private async offerCall(id: string, attempt: number): Promise<boolean> {
    const token = ++this.offerToken;
    this.dropPending();
    const made = await this.offerPeer(attempt);
    if (!made) return false;
    if (attempt !== this.connectionAttempt || token !== this.offerToken) { made.peer.close(); return false; }
    this.nextPeer = made.peer; this.nextOfferId = id;
    this.tellVoice({ type: 'offer', sdp: made.sdp, id });
    return true;
  }

  private dropPending(): void {
    const pending = this.nextPeer;
    this.nextPeer = null; this.nextOfferId = null;
    if (pending) { pending.onconnectionstatechange = null; pending.ontrack = null; pending.close(); }
  }

  /** The connection dropped while live: say so quietly and offer a new call (rc-<n>) on the same socket, microphone and
   * audio element, again every RECOVER_RETRY_MS until one connects; after RECOVER_MS Talk ends and says so. */
  private recover(attempt: number): void {
    if (attempt !== this.connectionAttempt) return;
    if (!this.recovery) {
      this.phase('reconnecting');
      const deadline = setTimeout(() => {
        if (attempt === this.connectionAttempt && this.recovery) this.fail(RECOVER_FAILED);
      }, RECOVER_MS);
      this.recovery = { deadline, retry: null };
    }
    this.offerAgain(attempt);
  }

  private offerAgain(attempt: number): void {
    const recovery = this.recovery;
    if (!recovery) return;
    if (recovery.retry !== null) clearTimeout(recovery.retry);
    recovery.retry = setTimeout(() => { if (attempt === this.connectionAttempt && this.recovery === recovery) this.offerAgain(attempt); }, RECOVER_RETRY_MS);
    void this.offerCall(`rc-${++this.recoverN}`, attempt).catch(() => {}); // the next try or the deadline
  }

  /** Connected again: the call is back. */
  private recovered(): void {
    const recovery = this.recovery;
    if (!recovery) return;
    clearTimeout(recovery.deadline);
    if (recovery.retry !== null) clearTimeout(recovery.retry);
    this.recovery = null;
  }

  private takeOver(peer: RTCPeerConnection): void {
    const old = this.peer;
    this.peer = peer; this.nextPeer = null; this.nextOfferId = null;
    if (old && old !== peer) { old.onconnectionstatechange = null; old.ontrack = null; old.close(); }
    this.mediaConnected = peer.connectionState === 'connected';
    if (this.mediaConnected) this.recovered();
    if (this.mediaConnected && this.state === 'live') this.phase(this.muted ? 'muted' : 'listening');
  }

  /** One of the partner's messages to the server (talk, snapshot, marks-shown, offer); false when not connected. */
  tellVoice(command: PartnerCommand): boolean {
    if (this.socket?.readyState !== WebSocket.OPEN) return false;
    this.socket.send(JSON.stringify(command));
    return true;
  }

  /** The speaker's context (made in the press that started Talk, so it may play), for the sign it heard you
   * (heard.ts HeardTone); null while Talk is off. */
  cueContext(): AudioContext | null {
    return this.speaker;
  }

  /** The talk key or button pressed: the microphone opens, the voice falls silent (barge-in), the turn starts. */
  talkDown(n: number): void {
    this.muteOutput();
    if (this.micTimer !== null) { clearTimeout(this.micTimer); this.micTimer = null; }
    this.micOpen = true; this.applyMic();
    this.tellVoice({ type: 'talk', down: true, n });
  }

  /** Released: the turn goes to the model; the microphone closes a moment later (MIC_TAIL_MS). */
  talkUp(n: number): void {
    this.tellVoice({ type: 'talk', down: false, n });
    this.closeMicSoon(MIC_TAIL_MS);
    this.restoreOutputSoon();
  }

  /** Dropped (Escape, a tap, a chord): no answer, the microphone closes at once. */
  talkCancel(n: number): void {
    this.tellVoice({ type: 'talk', cancel: true, n });
    this.closeMicSoon(0);
    this.restoreOutputSoon();
  }

  private closeMicSoon(ms: number): void {
    if (this.micTimer !== null) clearTimeout(this.micTimer);
    const close = () => { this.micTimer = null; this.micOpen = false; this.applyMic(); };
    if (ms <= 0) { this.micTimer = null; close(); } else this.micTimer = setTimeout(close, ms);
  }

  /** In hold mode the microphone is on only while held; otherwise as muted says. */
  private applyMic(): void {
    const on = !this.muted && (this.talkMode !== 'hold' || this.micOpen);
    this.mic?.getAudioTracks().forEach((track) => (track.enabled = on));
  }

  /** Silence the voice now (its audio element muted, queued relay audio dropped): heard again by restoreOutputSoon. */
  private muteOutput(): void {
    if (this.unmuteTimer !== null) { clearTimeout(this.unmuteTimer); this.unmuteTimer = null; }
    if (!this.outputMuted) this.outputMutedAt = performance.now();
    this.outputMuted = true;
    if (this.remote) this.remote.muted = true;
    const speaking = this.outputSpeaking;
    this.flush(); // relay audio still queued (and it stops counting as speech)
    if (speaking) this.phase(this.muted ? 'muted' : 'listening');
    this.onOutput?.(true);
  }

  private restoreOutputSoon(): void {
    if (!this.outputMuted) return;
    if (this.unmuteTimer !== null) clearTimeout(this.unmuteTimer);
    const wait = Math.max(0, this.outputMutedAt + BARGE_MIN_MS - performance.now());
    this.unmuteTimer = setTimeout(() => this.restoreOutput(), wait);
  }

  private restoreOutput(): void {
    if (this.unmuteTimer !== null) clearTimeout(this.unmuteTimer);
    this.unmuteTimer = null;
    if (!this.outputMuted) return;
    this.outputMuted = false;
    if (this.remote) this.remote.muted = false;
    this.onOutput?.(false);
  }

  private maybeReady(): void {
    if (this.providerReady && this.captured && (!this.peer || this.mediaConnected) && this.state === 'connecting') {
      this.clearStartupTimer();
      this.setState('live');
      this.phase(this.muted ? 'muted' : 'listening');
    }
  }

  private phase(phase: string): void {
    this.onEvent({type: 'voice', kind: 'status', data: {phase}});
    // The server may take the studio's measured speaking as the truth (its sideband may not hear the output buffer).
    const speaking = phase === 'speaking';
    if (speaking !== this.saidSpeaking && this.tellVoice({ type: 'voice-phase', phase: speaking ? 'speaking' : 'listening', at: Date.now() })) this.saidSpeaking = speaking;
  }

  private report(stage: string): void {
    if (this.stageSent.has(stage)) return;
    this.stageSent.add(stage);
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({type: 'diagnostic', stage}));
    else this.pendingStages.add(stage);
    this.onEvent({type: 'voice', kind: 'diagnostic', data: {stage}});
  }

  private clearResponseTimer(): void {
    if (this.responseTimer !== null) clearTimeout(this.responseTimer);
    this.responseTimer = null;
  }

  private clearStartupTimer(): void {
    if (this.startupTimer !== null) clearTimeout(this.startupTimer);
    this.startupTimer = null;
  }

  private fail(detail: string): void {
    ++this.connectionAttempt;
    this.cleanup();
    this.setState('error', detail);
  }

  stop(): void {
    ++this.connectionAttempt;
    this.cleanup();
    this.setState('off');
  }

  setMuted(muted: boolean): void {
    this.muted = muted;
    this.phase(muted ? 'muted' : 'listening');
    this.applyMic();
    // The session is told (session.input_audio.mute), not just sent nothing.
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: 'mute', muted }));
  }

  private play(data: ArrayBuffer): void {
    const attempt = this.connectionAttempt;
    const speaker = this.speaker;
    if (!speaker || data.byteLength < 6 || data.byteLength % 2 !== 0 || String.fromCharCode(...new Uint8Array(data, 0, 4)) !== AUDIO_MAGIC) return;
    if (this.outputMuted) return; // a barge-in: what was still coming of the cancelled reply is not played
    if (speaker.state !== 'running') { this.report('playback-blocked'); return; }
    const pcm = new Int16Array(data.slice(4));
    // Live sends silence continuously. Receiving a packet is not speech.
    const speech = pcm.some(sample => Math.abs(sample) >= 400);
    if (speech) {
      this.clearResponseTimer();
      this.report('playback');
      this.outputSpeaking = true;
      this.phase('speaking');
    }
    const buffer = speaker.createBuffer(1, pcm.length, OUTPUT_RATE);
    const channel = buffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) channel[i] = pcm[i] / 0x8000;
    const node = speaker.createBufferSource();
    node.buffer = buffer;
    node.connect(speaker.destination);
    // A little headroom on the first chunk absorbs network jitter without audible gaps.
    this.playAt = Math.max(this.playAt, speaker.currentTime + 0.05);
    node.start(this.playAt);
    this.playAt += buffer.duration;
    this.playing.add(node);
    if (speech) this.audible.add(node);
    node.onended = () => { if (attempt !== this.connectionAttempt) return; this.playing.delete(node); this.audible.delete(node); if (this.outputSpeaking && !this.audible.size && this.state === 'live') { this.outputSpeaking = false; this.phase(this.muted ? 'muted' : 'listening'); } };
  }

  /** The model was interrupted: drop everything still queued to play. */
  private flush(): void {
    for (const node of this.playing) {
      try {
        node.stop();
      } catch {
        // already finished
      }
    }
    this.playing.clear();
    this.audible.clear();
    this.outputSpeaking = false;
    this.playAt = 0;
  }

  private monitorOutput(stream: MediaStream): void {
    const context = this.speaker;
    if (!context) return;
    const probe = context.createMediaStreamSource(stream);
    const analyser = context.createAnalyser();
    analyser.fftSize = 256;
    probe.connect(analyser); // analysis only; the HTML audio element owns playback
    this.outputProbe?.disconnect();
    this.outputProbe = probe;
    if (this.mediaTimer !== null) clearInterval(this.mediaTimer);
    const samples = new Float32Array(analyser.fftSize);
    this.mediaTimer = setInterval(() => {
      analyser.getFloatTimeDomainData(samples);
      const audible = !this.outputMuted && samples.some(sample => Math.abs(sample) >= 0.012);
      if (audible) { this.outputLastHeard = performance.now(); this.clearResponseTimer(); }
      const speaking = audible || performance.now() - this.outputLastHeard < 350;
      if (speaking !== this.outputSpeaking) {
        this.outputSpeaking = speaking;
        this.phase(speaking ? 'speaking' : this.muted ? 'muted' : 'listening');
      }
    }, 100);
  }

  private cleanup(): void {
    if (this.mediaTimer !== null) clearInterval(this.mediaTimer);
    if (this.disconnectTimer !== null) clearTimeout(this.disconnectTimer);
    this.mediaTimer = this.disconnectTimer = null;
    this.outputProbe?.disconnect(); this.outputProbe = null;
    this.outputSpeaking = this.mediaConnected = false;
    if (this.permissionTimer !== null) clearTimeout(this.permissionTimer);
    this.permissionTimer = null;
    this.clearStartupTimer();
    this.clearResponseTimer();
    if (this.captureTimer !== null) clearTimeout(this.captureTimer);
    this.captureTimer = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) { socket.onclose = null; socket.close(); }
    if (this.capture) { this.capture.port.onmessage = null; this.capture.disconnect(); }
    this.capture = null;
    this.silent?.disconnect();
    this.silent = null;
    this.flush();
    this.peer?.close();
    this.peer = null;
    this.nextPeer?.close();
    this.nextPeer = null; this.nextOfferId = null;
    this.recovered();
    this.saidSpeaking = null;
    if (this.micTimer !== null) clearTimeout(this.micTimer);
    this.micTimer = null; this.micOpen = false;
    if (this.unmuteTimer !== null) clearTimeout(this.unmuteTimer);
    this.unmuteTimer = null;
    if (this.outputMuted) { this.outputMuted = false; this.onOutput?.(false); }
    if (this.remote) this.remote.srcObject = null;
    this.remote = null;
    this.mic?.getTracks().forEach((track) => track.stop());
    this.mic = null;
    void this.micContext?.close();
    this.micContext = null;
    void this.speaker?.close();
    this.speaker = null;
    this.socket = null;
    this.onLevel(0);
  }

  private setState(state: VoiceState, detail?: string): void {
    this.state = state;
    this.onState(state, detail);
  }
}

/** GPT-Live's documented offer includes gathered candidates; don't race negotiation. */
export async function waitForIce(peer: RTCPeerConnection): Promise<void> {
  if (peer.iceGatheringState === 'complete') return;
  await new Promise<void>((resolve, reject) => {
    const done = () => {
      if (peer.iceGatheringState !== 'complete') return;
      clearTimeout(timeout);
      peer.removeEventListener('icegatheringstatechange', done);
      resolve();
    };
    const timeout = setTimeout(() => {
      peer.removeEventListener('icegatheringstatechange', done);
      reject(new Error('Voice could not gather a network route'));
    }, 10_000);
    peer.addEventListener('icegatheringstatechange', done);
    done();
  });
}
