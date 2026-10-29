import { afterEach, expect, test, vi } from 'vitest';
import { VoiceClient } from './voice';

afterEach(() => vi.unstubAllGlobals());

test('workspace grant is sent before audio negotiation and never put in the URL', async () => {
  class Socket {
    static OPEN = 1;
    static made: Socket[] = [];
    readyState = 1;
    onopen: (() => void) | null = null;
    sent: string[] = [];
    readonly url: string;
    constructor(url: string) { this.url = url; Socket.made.push(this); }
    send(value: string) { this.sent.push(value); }
    close() {}
  }
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('location', { protocol: 'http:', host: 'localhost:8770' });
  vi.stubGlobal('AudioContext', class {
    sampleRate = 24000;
    audioWorklet = { addModule: async () => {} };
    resume() { return Promise.resolve(); }
    destination = {};
    createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
    createMediaStreamSource() { return { connect() {} }; }
    close() { return Promise.resolve(); }
  });
  vi.stubGlobal('AudioWorkletNode', class { port = { onmessage: null }; connect() {} disconnect() {} });
  vi.stubGlobal('navigator', { mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }], getAudioTracks: () => [] }) } });
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  await voice.start({ connectionGrant: 'synthetic-original-grant' });
  const socket = Socket.made[0];
  expect(socket.url).not.toContain('grant');
  socket.onopen?.();
  expect(socket.sent).toEqual([JSON.stringify({ type: 'workspace-bind', connectionGrant: 'synthetic-original-grant' })]);
  voice.stop();
});

test('stopping while microphone permission is pending prevents a late stream from connecting', async () => {
  let grant!: (stream: MediaStream) => void;
  const stopped = vi.fn();
  const socket = vi.fn();
  vi.stubGlobal('AudioContext', class { resume() { return Promise.resolve(); } close() { return Promise.resolve(); } });
  vi.stubGlobal('WebSocket', socket);
  vi.stubGlobal('navigator', { mediaDevices: {
    getUserMedia: () => new Promise<MediaStream>((resolve) => { grant = resolve; }),
  } });
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  const starting = voice.start();
  voice.stop();
  grant({ getTracks: () => [{ stop: stopped }] } as unknown as MediaStream);
  await starting;
  expect(stopped).toHaveBeenCalledOnce();
  expect(socket).not.toHaveBeenCalled();
  expect(voice.state).toBe('off');
});

// WebKit may start both contexts suspended; a socket handshake is not audio readiness.
function audioHarness() {
  const contexts: Context[] = [];
  const captures: Capture[] = [];
  class Context {
    state = 'suspended';
    destination = {};
    currentTime = 0;
    createBuffer(_channels: number, length: number, rate: number) { return { duration: length / rate, getChannelData: () => new Float32Array(length) }; }
    sources: {onended: null | (() => void)}[] = [];
    createBufferSource() { const node = {connect: vi.fn(), start: vi.fn(), stop: vi.fn(), buffer: null, onended: null as null | (() => void)}; this.sources.push(node); return node; }
    audioWorklet = { addModule: async () => {} };
    resume = vi.fn(async () => { this.state = 'running'; });
    close = vi.fn(async () => { this.state = 'closed'; });
    constructor() { contexts.push(this); }
    createMediaStreamSource() { return { connect: vi.fn() }; }
    createGain() { return { gain: { value: 1 }, connect: vi.fn(), disconnect: vi.fn() }; }
  }
  class Capture {
    port = { onmessage: null as null | ((event: {data: {pcm: ArrayBuffer; level: number}}) => void) };
    connect = vi.fn();
    disconnect = vi.fn();
    constructor() { captures.push(this); }
  }
  class Socket {
    static OPEN = 1;
    static made: Socket[] = [];
    readyState = 1;
    onopen: (() => void) | null = null;
    onmessage: ((event: {data: string | ArrayBuffer}) => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();
    close = vi.fn();
    constructor() { Socket.made.push(this); }
  }
  const track = {stop: vi.fn(), enabled: true, addEventListener: vi.fn()};
  vi.stubGlobal('AudioContext', Context);
  vi.stubGlobal('AudioWorkletNode', Capture);
  vi.stubGlobal('WebSocket', Socket);
  vi.stubGlobal('location', {protocol: 'http:', host: 'localhost:8770'});
  vi.stubGlobal('navigator', {mediaDevices: {getUserMedia: async () => ({getTracks: () => [track], getAudioTracks: () => [track]})}});
  return {contexts, captures, sockets: Socket.made, track};
}

test('Talk resumes audio capture and playback and connects a silent processing graph', async () => {
  const audio = audioHarness();
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  await voice.start();
  expect(audio.contexts.every(context => context.resume.mock.calls.length > 0)).toBe(true);
  expect(audio.captures[0].connect).toHaveBeenCalled();
  voice.stop();
});

test('late audio worklet callbacks after End cannot feed a new Talk session', async () => {
  const audio = audioHarness();
  const level = vi.fn();
  const voice = new VoiceClient(vi.fn(), vi.fn(), level);
  await voice.start();
  const old = audio.captures[0].port.onmessage!;
  voice.stop();
  await voice.start();
  level.mockClear();
  old({data: {pcm: new ArrayBuffer(1920), level: 0.8}});
  expect(audio.sockets[1].send).not.toHaveBeenCalled();
  expect(level).not.toHaveBeenCalled();
  voice.stop();
});

test('a model announcement does not claim readiness before provider acknowledgement and captured audio', async () => {
  const audio = audioHarness();
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  await voice.start();
  audio.sockets[0].onmessage!({data: JSON.stringify({type:'voice',kind:'model',data:{model:'gpt-live-1'}})});
  expect(voice.state).toBe('connecting');
  audio.sockets[0].onmessage!({data: JSON.stringify({type:'voice',kind:'ready'})});
  expect(voice.state).toBe('connecting');
  audio.captures[0].port.onmessage!({data:{pcm:new ArrayBuffer(1920),level:0}});
  expect(voice.state).toBe('live');
  voice.stop();
});

test('a provider that never acknowledges ends with a recoverable error, not an endless connection', async () => {
  vi.useFakeTimers();
  try {
    const audio = audioHarness();
    const state = vi.fn();
    const voice = new VoiceClient(vi.fn(), state, vi.fn());
    await voice.start();
    await vi.advanceTimersByTimeAsync(20_000);
    expect(voice.state).toBe('error');
    expect(audio.track.stop).toHaveBeenCalled();
    expect(audio.sockets[0].close).toHaveBeenCalled();
    voice.stop();
  } finally { vi.useRealTimers(); }
});

test('microphone audio waits for provider readiness and is never buffered for a later session', async () => {
  const audio = audioHarness();
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  await voice.start();
  const pcm = new ArrayBuffer(1920);
  audio.captures[0].port.onmessage!({data:{pcm,level:0.1}});
  expect(audio.sockets[0].send.mock.calls.filter(([value]) => value instanceof ArrayBuffer)).toHaveLength(0);
  audio.sockets[0].onmessage!({data: JSON.stringify({type:'voice',kind:'ready'})});
  expect(audio.sockets[0].send.mock.calls.filter(([value]) => value instanceof ArrayBuffer)).toHaveLength(0);
  audio.captures[0].port.onmessage!({data:{pcm,level:0.1}});
  expect(audio.sockets[0].send.mock.calls.filter(([value]) => value instanceof ArrayBuffer)).toHaveLength(1);
  voice.stop();
});

test('provider rejection remains actionable after socket failure and releases microphone', async () => {
  const audio = audioHarness();
  const state = vi.fn();
  const voice = new VoiceClient(vi.fn(), state, vi.fn());
  await voice.start();
  const oldMessage = audio.sockets[0].onmessage!;
  const oldClose = audio.sockets[0].onclose!;
  oldMessage({data:JSON.stringify({type:'voice',kind:'error',data:{message:'You have no credits remaining. Add credits to continue using the API.'}})});
  oldClose();
  expect(state).toHaveBeenLastCalledWith('error', 'You have no credits remaining. Add credits to continue using the API.');
  expect(audio.track.stop).toHaveBeenCalledOnce();
  voice.stop();
});


test('continuous silent relay packets cannot hide a stalled response', async () => {
  vi.useFakeTimers();
  const audio = audioHarness();
  const events = vi.fn();
  const voice = new VoiceClient(events, vi.fn(), vi.fn());
  try {
    await voice.start();
    audio.sockets[0].onmessage!({data: JSON.stringify({type:'voice',kind:'ready'})});
    audio.captures[0].port.onmessage!({data:{pcm:new ArrayBuffer(1920),level:0.1}});
    audio.sockets[0].onmessage!({data: JSON.stringify({type:'voice',kind:'input_transcript',text:'Hello'})});
    const silence = new Uint8Array(484);
    silence.set(new TextEncoder().encode('MMA1'));
    for (let i=0;i<21;i++) {
      audio.sockets[0].onmessage!({data:silence.buffer});
      audio.contexts.flatMap(context => context.sources).at(-1)?.onended?.();
      await vi.advanceTimersByTimeAsync(1000);
    }
    expect(events.mock.calls.some(([event]) => event.data?.stage === 'response-wait')).toBe(true);
    const phases = events.mock.calls.filter(([event]) => event.data?.phase).map(([event]) => event.data.phase);
    expect(phases.slice(phases.indexOf('processing') + 1)).not.toContain('listening');
    expect(events.mock.calls.some(([event]) => event.data?.phase === 'speaking')).toBe(false);
  } finally { voice.stop(); vi.useRealTimers(); }
});

// --- The one-brain voice (PARTNER_VOICE): WebRTC, hold to talk, barge-in, its own messages, a renewed call. ---
function partnerHarness() {
  const audio = audioHarness();
  // The reply's audio, as the output monitor hears it from the remote track (loud: it is speaking).
  const output = { loud: false };
  const context = (globalThis as unknown as { AudioContext: { prototype: Record<string, unknown> } }).AudioContext.prototype;
  context.createAnalyser = () => ({ fftSize: 256, connect: vi.fn(),
    getFloatTimeDomainData: (samples: Float32Array) => samples.fill(output.loud ? 0.5 : 0) });
  context.createMediaStreamSource = () => ({ connect: vi.fn(), disconnect: vi.fn() });
  const peers: Peer[] = [];
  const elements: Element[] = [];
  class Peer {
    connectionState = 'new';
    iceGatheringState = 'complete';
    localDescription: { type: string; sdp: string } | null = null;
    onconnectionstatechange: (() => void) | null = null;
    ontrack: ((event: { track: object }) => void) | null = null;
    remote: unknown = null;
    addTrack = vi.fn();
    createDataChannel = vi.fn();
    close = vi.fn();
    readonly n = peers.length + 1;
    constructor() { peers.push(this); }
    async createOffer() { return { type: 'offer', sdp: `offer-${this.n}` }; }
    async setLocalDescription(offer: { type: string; sdp: string }) { this.localDescription = offer; }
    async setRemoteDescription(answer: unknown) { this.remote = answer; this.ontrack?.({ track: {} }); }
    addEventListener() {}
    removeEventListener() {}
    connect(state = 'connected') { this.connectionState = state; this.onconnectionstatechange?.(); }
  }
  class Element { muted = false; autoplay = false; srcObject: unknown = null; play = vi.fn(async () => {}); constructor() { elements.push(this); } }
  vi.stubGlobal('RTCPeerConnection', Peer);
  vi.stubGlobal('Audio', Element);
  vi.stubGlobal('MediaStream', class { tracks: unknown[]; constructor(tracks: unknown[]) { this.tracks = tracks; } });
  return { ...audio, peers, elements, output };
}

async function liveHold(events = vi.fn()) {
  const h = partnerHarness();
  const voice = new VoiceClient(events, vi.fn(), vi.fn());
  await voice.start({ model: 'gpt-realtime-2.1' });
  const socket = h.sockets[0];
  socket.onopen?.();
  const say = (message: object) => socket.onmessage!({ data: JSON.stringify(message) });
  say({ type: 'voice', kind: 'model', data: { model: 'gpt-realtime-2.1', label: 'One brain', talk: 'hold' } });
  say({ type: 'voice', kind: 'ready' });
  say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-1' } });
  await Promise.resolve();
  h.peers[0].connect();
  h.captures[0].port.onmessage!({ data: { pcm: new ArrayBuffer(1920), level: 0 } });
  const sent = () => socket.send.mock.calls.map(([value]) => typeof value === 'string' ? JSON.parse(value) : value);
  return { ...h, voice, socket, say, sent };
}

test('the one-brain voice goes over WebRTC whatever the saved link, and its offer carries no grant', async () => {
  const h = partnerHarness();
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  voice.link = 'relay';
  await voice.start({ model: 'gpt-realtime-2.1' });
  h.sockets[0].onopen?.();
  expect(h.peers).toHaveLength(1);
  expect(h.sockets[0].send.mock.calls.map(([v]) => JSON.parse(v))).toEqual([{ type: 'offer', sdp: 'offer-1' }]);
  voice.stop();
});

test('held turns: the microphone is on only while held and a moment after, so the last word reaches the model', async () => {
  vi.useFakeTimers();
  try {
    const t = await liveHold();
    expect(t.voice.state).toBe('live');
    expect(t.voice.talkMode).toBe('hold');
    expect(t.track.enabled).toBe(false); // between turns OpenAI hears nothing
    t.voice.talkDown(1);
    expect(t.track.enabled).toBe(true);
    expect(t.sent().at(-1)).toEqual({ type: 'talk', down: true, n: 1 });
    t.voice.talkUp(1);
    expect(t.sent().at(-1)).toEqual({ type: 'talk', down: false, n: 1 });
    expect(t.track.enabled).toBe(true);
    await vi.advanceTimersByTimeAsync(299);
    expect(t.track.enabled).toBe(true);
    await vi.advanceTimersByTimeAsync(2);
    expect(t.track.enabled).toBe(false);
    t.voice.talkDown(2);
    t.voice.talkCancel(2);
    expect(t.sent().at(-1)).toEqual({ type: 'talk', cancel: true, n: 2 });
    expect(t.track.enabled).toBe(false); // dropped: closed at once
    t.voice.stop();
  } finally { vi.useRealTimers(); }
});

test('barge-in: pressing while it speaks silences its audio element at once, heard again for the next reply', async () => {
  vi.useFakeTimers();
  try {
    const output = vi.fn(), events = vi.fn();
    const t = await liveHold(events);
    t.voice.onOutput = output;
    const element = t.elements[0];
    expect(element.muted).toBe(false);
    t.output.loud = true; // it is speaking
    await vi.advanceTimersByTimeAsync(200);
    const phases = () => events.mock.calls.filter(([e]) => e.kind === 'status').map(([e]) => e.data.phase);
    expect(phases().at(-1)).toBe('speaking');
    const pressedAt = performance.now();
    t.voice.talkDown(1);
    expect(element.muted).toBe(true); // in the same task as the key: silent well within 150 ms
    expect(performance.now() - pressedAt).toBeLessThan(150);
    expect(output).toHaveBeenLastCalledWith(true);
    expect(phases().at(-1)).toBe('listening'); // the track still carries the cancelled reply; it is not heard as speech
    await vi.advanceTimersByTimeAsync(1200);
    expect(phases().at(-1)).toBe('listening');
    expect(element.muted).toBe(true); // still the user's turn
    t.output.loud = false;
    t.voice.talkUp(1);
    await vi.advanceTimersByTimeAsync(0);
    expect(element.muted).toBe(false);
    expect(output).toHaveBeenLastCalledWith(false);
    // A tap: silent at least BARGE_MIN_MS, so the cancelled reply's last audio is never heard.
    t.voice.talkDown(2);
    t.voice.talkCancel(2);
    await vi.advanceTimersByTimeAsync(399);
    expect(element.muted).toBe(true);
    await vi.advanceTimersByTimeAsync(2);
    expect(element.muted).toBe(false);
    t.voice.stop();
  } finally { vi.useRealTimers(); }
});

test('the partner\'s own messages go to the studio, not into the conversation', async () => {
  const events = vi.fn();
  const t = await liveHold(events);
  const partner = vi.fn();
  t.voice.onPartner = partner;
  events.mockClear();
  t.say({ type: 'snapshot-request', id: 'look-1', maxSide: 1024 });
  t.say({ type: 'marks', id: 'm1', marks: [{ target: 'outline:o12' }], until: 'speech-end' });
  t.say({ type: 'marks-clear' });
  expect(partner.mock.calls.map(([m]) => m.type)).toEqual(['snapshot-request', 'marks', 'marks-clear']);
  expect(events).not.toHaveBeenCalled();
  expect(t.voice.tellVoice({ type: 'marks-shown', id: 'm1', resolved: [{ target: 'outline:o12', ok: true, rect: [1, 2, 3, 4] }] })).toBe(true);
  expect(t.sent().at(-1)).toEqual({ type: 'marks-shown', id: 'm1', resolved: [{ target: 'outline:o12', ok: true, rect: [1, 2, 3, 4] }] });
  t.voice.stop();
});

test('a renewed call: a fresh offer on the same socket and microphone; the old connection closes once answered', async () => {
  const state = vi.fn();
  const h = partnerHarness();
  const voice = new VoiceClient(vi.fn(), state, vi.fn());
  await voice.start({ model: 'gpt-realtime-2.1' });
  const socket = h.sockets[0];
  socket.onopen?.();
  const say = (message: object) => socket.onmessage!({ data: JSON.stringify(message) });
  say({ type: 'voice', kind: 'ready' });
  say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-1' } });
  await Promise.resolve();
  h.peers[0].connect();
  h.captures[0].port.onmessage!({ data: { pcm: new ArrayBuffer(1920), level: 0 } });
  expect(voice.state).toBe('live');
  say({ type: 'reoffer', id: 're-1', why: 'limit' });
  await vi.waitFor(() => expect(h.peers).toHaveLength(2));
  await vi.waitFor(() => expect(socket.send.mock.calls.map(([v]) => JSON.parse(v)).at(-1)).toEqual({ type: 'offer', sdp: 'offer-2', id: 're-1' }));
  h.peers[0].connect('failed'); // the old call ends on its way out: not a failure of the conversation
  expect(voice.state).toBe('live');
  expect(h.peers[0].close).not.toHaveBeenCalled();
  say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-2' } });
  await vi.waitFor(() => expect(h.peers[0].close).toHaveBeenCalled());
  expect(h.peers[1].remote).toEqual({ type: 'answer', sdp: 'answer-2' });
  expect(h.peers[0].remote).toEqual({ type: 'answer', sdp: 'answer-1' });
  expect(h.elements).toHaveLength(1); // the same audio element
  expect(h.peers[1].addTrack).toHaveBeenCalledWith(h.track, expect.anything());
  h.peers[1].connect();
  expect(voice.state).toBe('live');
  expect(state).not.toHaveBeenCalledWith('error', expect.anything());
  voice.stop();
});

test('GPT-Live holds no turns: its microphone stays on and its Talk stays a toggle', async () => {
  const h = partnerHarness();
  const voice = new VoiceClient(vi.fn(), vi.fn(), vi.fn());
  await voice.start();
  h.sockets[0].onmessage!({ data: JSON.stringify({ type: 'voice', kind: 'model', data: { model: 'gpt-live-1', label: 'GPT-Live' } }) });
  expect(voice.talkMode).toBe(null);
  expect(h.track.enabled).toBe(true);
  voice.stop();
});

// Review 2026-10-29: a Wi-Fi drop failed the peer and ended Talk. Now the studio offers a new call itself on the same
// socket, microphone and audio element ({type:"offer", id:"rc-<n>"}, retried every 3.5 s, the answer's id echoed), quietly
// "Reconnecting…", and gives up only after 10 s with a plain message.
test('a dropped connection is offered again, not ended: the answer to its own offer takes over', async () => {
  vi.useFakeTimers();
  try {
    const events = vi.fn();
    const t = await liveHold(events);
    expect(t.voice.state).toBe('live');
    t.peers[0].connect('failed'); // Wi-Fi gone
    expect(t.voice.state).toBe('live');
    const phases = () => events.mock.calls.filter(([e]) => e.kind === 'status').map(([e]) => e.data.phase);
    expect(phases().at(-1)).toBe('reconnecting');
    await vi.advanceTimersByTimeAsync(0);
    expect(t.peers).toHaveLength(2);
    expect(t.sent().at(-1)).toEqual({ type: 'offer', sdp: 'offer-2', id: 'rc-1' });
    expect(t.peers[1].addTrack).toHaveBeenCalledWith(t.track, expect.anything()); // the same microphone
    t.say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-2', id: 'rc-1' } });
    await vi.advanceTimersByTimeAsync(0);
    expect(t.peers[0].close).toHaveBeenCalled();
    t.peers[1].connect();
    expect(phases().at(-1)).toBe('listening');
    await vi.advanceTimersByTimeAsync(20_000);
    expect(t.voice.state).toBe('live'); // nothing left to give up on
    expect(t.elements).toHaveLength(1); // the same audio element
    t.voice.stop();
  } finally { vi.useRealTimers(); }
});

test('it offers again every 3.5 s; a late answer to an older offer is never applied to the newer one', async () => {
  vi.useFakeTimers();
  try {
    const t = await liveHold();
    t.peers[0].connect('disconnected');
    await vi.advanceTimersByTimeAsync(2_900);
    expect(t.peers).toHaveLength(1); // a short blip may come back by itself
    await vi.advanceTimersByTimeAsync(200);
    expect(t.sent().at(-1)).toMatchObject({ type: 'offer', id: 'rc-1' });
    await vi.advanceTimersByTimeAsync(3_500);
    expect(t.sent().at(-1)).toMatchObject({ type: 'offer', id: 'rc-2' });
    expect(t.peers[1].close).toHaveBeenCalled(); // the unanswered offer is dropped
    t.say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-late', id: 'rc-1' } });
    await vi.advanceTimersByTimeAsync(0);
    expect(t.peers[2].remote).toBe(null);
    expect(t.voice.state).toBe('live');
    t.say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-3', id: 'rc-2' } });
    await vi.advanceTimersByTimeAsync(0);
    expect(t.peers[2].remote).toEqual({ type: 'answer', sdp: 'answer-3' });
    t.peers[2].connect();
    expect(t.voice.state).toBe('live');
    t.voice.stop();
  } finally { vi.useRealTimers(); }
});

test('after 10 s without a connection it gives up with a plain message', async () => {
  vi.useFakeTimers();
  try {
    const state = vi.fn();
    const h = partnerHarness();
    const voice = new VoiceClient(vi.fn(), state, vi.fn());
    await voice.start({ model: 'gpt-realtime-2.1' });
    const socket = h.sockets[0];
    socket.onopen?.();
    const say = (message: object) => socket.onmessage!({ data: JSON.stringify(message) });
    say({ type: 'voice', kind: 'ready' });
    say({ type: 'voice', kind: 'answer', data: { sdp: 'answer-1' } });
    await vi.advanceTimersByTimeAsync(0);
    h.peers[0].connect();
    h.captures[0].port.onmessage!({ data: { pcm: new ArrayBuffer(1920), level: 0 } });
    expect(voice.state).toBe('live');
    h.peers[0].connect('failed');
    await vi.advanceTimersByTimeAsync(9_900);
    expect(voice.state).toBe('live');
    await vi.advanceTimersByTimeAsync(200);
    expect(voice.state).toBe('error');
    expect(state).toHaveBeenLastCalledWith('error', expect.stringMatching(/lost its connection.*Talk again/));
  } finally { vi.useRealTimers(); }
});

test('the voice\'s speaking phase goes to the server as it changes ({type:"voice-phase"})', async () => {
  vi.useFakeTimers();
  try {
    const t = await liveHold();
    const phases = () => t.sent().filter((m) => m?.type === 'voice-phase').map((m) => m.phase);
    t.output.loud = true;
    await vi.advanceTimersByTimeAsync(300);
    t.output.loud = false;
    await vi.advanceTimersByTimeAsync(600);
    expect(phases().slice(-2)).toEqual(['speaking', 'listening']);
    const last = t.sent().filter((m) => m?.type === 'voice-phase').at(-1);
    expect(typeof last.at).toBe('number');
    const count = phases().length;
    await vi.advanceTimersByTimeAsync(1000);
    expect(phases().length).toBe(count); // only on a change
    t.voice.stop();
  } finally { vi.useRealTimers(); }
});
