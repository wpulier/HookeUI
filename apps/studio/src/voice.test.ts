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
