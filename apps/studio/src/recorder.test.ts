import { afterEach, expect, test, vi } from 'vitest';
import { StudioRecorder } from './recorder';
import { requestRecordingDataset } from './recording-dataset';

class Track extends EventTarget {
  readyState = 'live';
  muted = false;
  stop() { this.readyState = 'ended'; }
}
class Stream {
  readonly tracks: Track[];
  readonly audio: boolean;
  constructor(tracks: Track[] = [], audio = false) { this.tracks = tracks; this.audio = audio; }
  getVideoTracks() { return this.audio ? [] : this.tracks; }
  getAudioTracks() { return this.audio ? this.tracks : []; }
  getTracks() { return this.tracks; }
}
class Recorder {
  static last: Recorder;
  static isTypeSupported(_type: string) { return true; }
  state = 'inactive';
  ondataavailable?: (event: { data: Blob }) => void;
  onstop?: () => void;
  onerror?: () => void;
  readonly options?: MediaRecorderOptions;
  constructor(_stream?: MediaStream, options?: MediaRecorderOptions) { Recorder.last = this; this.options = options; }
  start() { this.state = 'recording'; }
  stop() { this.state = 'inactive'; this.onstop?.(); }
}
afterEach(() => vi.unstubAllGlobals());

function setup(failUpload = false) {
  const screen = new Track();
  const calls: { path: string; body: unknown }[] = [];
  const messages: string[] = [];
  vi.stubGlobal('navigator', { mediaDevices: {
    getDisplayMedia: async () => new Stream([screen]),
    getUserMedia: async () => { throw new Error('Permission denied'); },
  } });
  vi.stubGlobal('AudioContext', class {
    createMediaStreamDestination() { return { stream: new Stream([], true) }; }
    createMediaStreamSource() { return { connect() {} }; }
    async close() {}
  });
  vi.stubGlobal('MediaStream', Stream);
  vi.stubGlobal('MediaRecorder', Recorder);
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => {
    calls.push({ path, body: options.body });
    if (failUpload && path.endsWith('/append')) throw new Error('Connection lost');
    const body = path === '/api/recordings/start' ? { id: 'saved' }
      : path.includes('/api/record/start') ? { ok: false }
      : path.includes('/api/record/stop') ? { ok:true, owners:[], frames: 0 }
      : path.endsWith('/stop') ? { ok: true, playable: 'saved.mp4' } : { ok: true };
    return { ok: true, json: async () => body };
  });
  const end = vi.fn(async () => {});
  const recorder = new StudioRecorder(() => 'http://bridge', message => { if (message) messages.push(message); },
    { begin: async () => {}, end });
  return { recorder, calls, messages, screen, end };
}

test('denied microphone and absent tab audio are not reported as captured', async () => {
  const { recorder, calls, screen } = setup();
  await recorder.start();
  await vi.waitFor(() => expect(recorder.channels.camera).toBe('failed'));
  expect(recorder.channels.microphone).toBe('unavailable');
  expect(recorder.channels.tabAudio).toBe('unavailable');
  expect(recorder.summary()).toContain('Microphone: unavailable');
  recorder.stop();
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(screen.readyState).toBe('ended');
  const events = calls.filter(call => call.path.endsWith('/channels')).map(call => JSON.parse(String(call.body)));
  expect(events).toContainEqual({ channel: 'microphone', state: 'unavailable' });
  expect(events.some(event => event.channel === 'camera' && event.state === 'captured')).toBe(false);
});

test('an upload failure stops recording and reports a gap instead of a successful replay', async () => {
  const { recorder, messages, end } = setup(true);
  await recorder.start();
  Recorder.last.ondataavailable?.({ data: new Blob(['frame bytes']) });
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(recorder.channels.upload).toBe('failed');
  expect(messages.at(-1)).toContain('not a complete replay');
  expect(messages.some(message => message.startsWith('Saved the studio recording'))).toBe(false);
  expect(end).toHaveBeenCalledOnce();
});

test('inactive recorder error waits for the final chunk and finalizes only once', async () => {
  const { recorder, calls, end } = setup();
  await recorder.start();
  const media = Recorder.last;
  media.state = 'inactive';
  media.onerror?.();
  expect(recorder.state).toBe('saving');
  media.ondataavailable?.({ data: new Blob(['last chunk']) });
  media.onstop?.();
  media.onstop?.();
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(end).toHaveBeenCalledOnce();
  expect(calls.filter(call => call.path === '/api/recordings/saved/stop')).toHaveLength(1);
  expect(calls.findIndex(call => call.path.endsWith('/append'))).toBeLessThan(calls.findIndex(call => call.path === '/api/recordings/saved/stop'));
});

test('a failed camera start cannot claim stale frames from an earlier capture', async () => {
  const { recorder, calls } = setup();
  const base = fetch;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => path.includes('/api/record/stop')
    ? { ok: true, json: async () => ({ frames: 900, path: '/old-camera.mp4' }) }
    : base(path, options));
  await recorder.start();
  await vi.waitFor(() => expect(recorder.channels.camera).toBe('failed'));
  recorder.stop();
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(recorder.channels.camera).toBe('failed');
  expect(calls.filter(call => call.path.endsWith('/channels')).map(call => String(call.body)).join(' ')).not.toContain('captured');
});

test('audio initialization failure releases the selected screen and leaves Record usable', async () => {
  const { recorder, screen, messages, calls } = setup();
  vi.stubGlobal('AudioContext', class { constructor() { throw new Error('Audio device unavailable'); } });
  await expect(recorder.start()).resolves.toBeUndefined();
  expect(recorder.state).toBe('idle');
  expect(screen.readyState).toBe('ended');
  expect(calls.some(call => call.path.includes('/api/record/start'))).toBe(false);
  expect(messages.at(-1)).toContain('Could not start');
});

test('failed MediaRecorder setup never releases an unrequested camera owner', async () => {
  const { recorder, calls } = setup();
  vi.stubGlobal('MediaRecorder', class extends Recorder { constructor() { super(); throw new Error('codec'); } });
  await recorder.start();
  expect(recorder.state).toBe('idle');
  expect(calls.some(call => call.path.includes('/api/record/stop'))).toBe(false);
});

test('failed channel history remains visible after media upload succeeds', async () => {
  const { recorder, messages } = setup();
  const base = fetch;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => path.endsWith('/channels')
    ? { ok: false, status: 500 } : base(path, options));
  await recorder.start();
  recorder.stop();
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(messages.at(-1)).toContain('channel history');
  expect(messages.at(-1)).not.toMatch(/^Saved the studio recording/);
});

test.each(['error', 'sidecarError'])('fresh camera frames do not conceal a camera %s', async (field) => {
  const { recorder, messages } = setup();
  const base = fetch;
  vi.stubGlobal('fetch', async (path: string, options: RequestInit) => path.includes('/api/record/start')
    ? { ok: true, json: async () => ({ ok: true, owners: [], path: '/current.mp4', frames: 0 }) }
    : path.includes('/api/record/stop')
      ? { ok: true, json: async () => ({ ok: true, owners: [], path: '/current.mp4', frames: 120, [field]: 'disk failed' }) }
      : base(path, options));
  await recorder.start();
  recorder.stop();
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(recorder.channels.camera).toBe('failed');
  expect(messages.at(-1)).toContain('Camera capture');
});


test('camera-only recording never requests screen or microphone and verifies new frames', async () => {
  const { recorder, messages, end } = setup();
  const display = vi.fn(), microphone = vi.fn();
  vi.stubGlobal('navigator', {mediaDevices: {getDisplayMedia: display, getUserMedia: microphone}});
  const paths: string[] = [];
  vi.stubGlobal('fetch', async (path: string) => {
    paths.push(path);
    return {ok:true,json:async()=>({ok:true,owners:[],path:'/current.mp4',frames:path.includes('/start')?10:35})};
  });
  await recorder.start({mode:'camera',microphone:false,appAudio:false});
  expect(recorder.state).toBe('recording');
  expect(recorder.channels.camera).toBe('requested');
  expect(display).not.toHaveBeenCalled(); expect(microphone).not.toHaveBeenCalled();
  recorder.stop(); recorder.stop();
  await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
  expect(recorder.channels.camera).toBe('captured');
  expect(paths.filter(path=>path.includes('/stop'))).toHaveLength(1);
  expect(paths.some(path=>path.startsWith('/api/recordings'))).toBe(false);
  expect(end).toHaveBeenCalledOnce();
  expect(messages.at(-1)).toContain('Camera frames recorded');
});

test.each(['stale', 'error', 'sidecarError', 'different-file'])('camera-only %s cannot claim verified capture', async failure => {
  const {recorder,messages}=setup();
  vi.stubGlobal('fetch', async (path:string)=>({ok:true,json:async()=>path.includes('/start')
    ?{ok:true,owners:[],path:'/current.mp4',frames:10}
    :{ok:true,owners:[],path:failure==='different-file'?'/old.mp4':'/current.mp4',frames:failure==='stale'?10:50,
      ...(failure==='error'||failure==='sidecarError'?{[failure]:'disk error'}:{})}}));
  await recorder.start({mode:'camera',microphone:false,appAudio:false});recorder.stop();
  await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
  expect(messages.at(-1)).toContain('could not be verified');
});

test('camera-only rejected startup ends the dataset and remains retryable',async()=>{
  const {recorder,messages,end}=setup();
  await recorder.start({mode:'camera',microphone:false,appAudio:false});
  expect(recorder.state).toBe('idle');expect(end).toHaveBeenCalledOnce();
  expect(messages.at(-1)).toContain('could not be verified');
});

test('workspace recording requests only explicitly selected audio',async()=>{
  const {recorder}=setup();
  const display=vi.fn(async()=>new Stream([new Track()])),microphone=vi.fn();
  vi.stubGlobal('navigator',{mediaDevices:{getDisplayMedia:display,getUserMedia:microphone}});
  vi.stubGlobal('AudioContext',class{constructor(){throw new Error('No audio should be initialized');}});
  await recorder.start({mode:'workspace',microphone:false,appAudio:false});
  expect(display).toHaveBeenCalledWith(expect.objectContaining({audio:false}));
  expect(microphone).not.toHaveBeenCalled();expect(recorder.state).toBe('recording');
  recorder.stop();await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
});


test('successful camera frames cannot hide a failed dataset Stop',async()=>{
  const {recorder,messages,end}=setup();
  end.mockRejectedValue(new Error('Dataset did not stop'));
  vi.stubGlobal('fetch',async(path:string)=>({ok:true,json:async()=>({ok:true,owners:[],path:'/current.mp4',frames:path.includes('/start')?0:20})}));
  await recorder.start({mode:'camera',microphone:false,appAudio:false});recorder.stop();
  await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
  expect(messages.at(-1)).toContain('dataset could not be stopped');
  expect(messages.at(-1)).toContain('Press Stop again');
  expect(messages.some(message=>message.startsWith('Camera frames recorded'))).toBe(false);
});


test.each(['rejected', 'timeout', 'missing acknowledgement'])('dataset %s uses the production acknowledgement boundary',async failure=>{
  setup();const messages:string[]=[];
  const end=()=>requestRecordingDataset(false,()=>failure==='rejected'?Promise.reject(new Error('Disconnected'))
    :failure==='timeout'?new Promise(()=>{}):Promise.resolve({ok:true}),10).then(()=>true);
  const recorder=new StudioRecorder(()=> 'http://bridge',message=>{if(message)messages.push(message);},{begin:async()=>true,end});
  vi.stubGlobal('fetch',async(path:string)=>({ok:true,json:async()=>({ok:true,owners:[],path:'/current.mp4',frames:path.includes('/start')?0:20})}));
  await recorder.start({mode:'camera',microphone:false,appAudio:false});recorder.stop();
  await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
  expect(messages.at(-1)).toContain('Press Stop again');
});

test('unconfirmed dataset begin remains visible through camera stop',async()=>{
  setup();const messages:string[]=[];
  const recorder=new StudioRecorder(()=> 'http://bridge',message=>{if(message)messages.push(message);},
    {begin:async()=>false,end:async()=>true});
  vi.stubGlobal('fetch',async(path:string)=>({ok:true,json:async()=>({ok:true,owners:[],path:'/current.mp4',frames:path.includes('/start')?0:20})}));
  await recorder.start({mode:'camera',microphone:false,appAudio:false});
  expect(messages.at(-1)).toContain('without a confirmed dataset');
  recorder.stop();await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
  expect(messages.at(-1)).toContain('without a confirmed dataset');
});


test('unconfirmed camera Stop blocks a new start and retries only its own owner',async()=>{
  const {recorder,messages,end}=setup();let failStop=true;const paths:string[]=[];
  vi.stubGlobal('fetch',async(path:string)=>{
    paths.push(path);if(path.includes('/stop')&&failStop)throw new Error('Connection lost');
    return {ok:true,json:async()=>({ok:true,owners:path.includes('/stop')?['demo']:['studio','demo'],path:'/current.mp4',frames:path.includes('/start')?0:20})};
  });
  await recorder.start({mode:'camera',microphone:false,appAudio:false});recorder.stop();
  await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
  expect(end).toHaveBeenCalledOnce();expect(recorder.cameraStopUnconfirmed).toBe(true);
  expect(messages.at(-1)).toContain('Camera Stop is unconfirmed');
  await recorder.start({mode:'camera',microphone:false,appAudio:false});
  expect(paths.filter(path=>path.includes('/start'))).toHaveLength(1);
  failStop=false;const stopped=await recorder.releaseCamera();
  expect(stopped?.owners).toEqual(['demo']);expect(recorder.cameraStopUnconfirmed).toBe(false);
  expect(paths.filter(path=>path.includes('/stop'))).toEqual(['http://bridge/api/record/stop?owner=studio','http://bridge/api/record/stop?owner=studio']);
});

test('annotated view shares upload/dataset ownership without screen, audio or raw-camera permission',async()=>{
 const {calls,messages}=setup();
 const track=new Track(),dispose=vi.fn(),end=vi.fn(async()=>{}),begin=vi.fn(async()=>true);
 const screen=vi.fn(),mic=vi.fn();vi.stubGlobal('navigator',{mediaDevices:{getDisplayMedia:screen,getUserMedia:mic}});
 let lost:(reason:string)=>void=()=>{};
 const recorder=new StudioRecorder(()=> 'http://bridge',text=>{if(text)messages.push(text);},{begin,end},
  {start:ended=>{lost=ended;return {stream:new Stream([track]) as unknown as MediaStream,dispose};}});
 await recorder.start({mode:'annotated',microphone:true,appAudio:true});
 expect(recorder.state).toBe('recording');expect(begin).toHaveBeenCalledOnce();
 expect(screen).not.toHaveBeenCalled();expect(mic).not.toHaveBeenCalled();
 expect(calls.some(call=>call.path.includes('/api/record/'))).toBe(false);
 expect(JSON.parse(String(calls.find(call=>call.path==='/api/recordings/start')?.body))).toMatchObject({scope:'annotated'});
 expect(recorder.summary()).toContain('Annotated view: available');
 Recorder.last.ondataavailable?.({data:new Blob(['annotated picture'])});
 lost('The input changed.');recorder.stop();
 await vi.waitFor(()=>expect(recorder.state).toBe('idle'));
 expect(end).toHaveBeenCalledOnce();expect(track.readyState).toBe('ended');expect(dispose).toHaveBeenCalled();
 expect(calls.filter(call=>call.path==='/api/recordings/saved/stop')).toHaveLength(1);
 expect(messages.at(-1)).toContain('Saved the annotated view');expect(messages.at(-1)).toContain('input changed');
});

test('unavailable annotated capture refuses before starting a dataset or uploading',async()=>{
 const {calls}=setup(),begin=vi.fn(async()=>true);
 const recorder=new StudioRecorder(()=> 'http://bridge',()=>{},{begin,end:vi.fn()},
  {start:()=>{throw new Error('Show the input view');}});
 await recorder.start({mode:'annotated',microphone:false,appAudio:false});
 expect(recorder.state).toBe('idle');expect(begin).not.toHaveBeenCalled();expect(calls).toEqual([]);
});


test.each([
  ['video/mp4;codecs=avc3.42E01E', 'mp4'],
  ['video/webm;codecs=vp9', 'webm'],
  ['video/webm;codecs=vp8', 'webm'],
  ['', ''],
])('annotated recording selects a resize-compatible codec or refuses: %s', async (supported, ext) => {
  const {calls, messages} = setup();
  const queried: string[] = [];
  vi.stubGlobal('MediaRecorder', class extends Recorder {
    static isTypeSupported(type: string) {
      queried.push(type);
      return type === supported || type.includes('avc1') || type === 'video/mp4';
    }
  });
  const track = new Track(), begin = vi.fn(async () => true);
  const recorder = new StudioRecorder(() => 'http://bridge', text => {if (text) messages.push(text);},
    {begin, end: vi.fn()}, {start: () => ({stream: new Stream([track]) as unknown as MediaStream, dispose: vi.fn()})});
  await recorder.start({mode:'annotated', microphone:false, appAudio:false});
  expect(queried[0]).toBe('video/mp4;codecs=avc3.42E01E');
  expect(queried.some(type => type.includes('avc1') || type === 'video/mp4')).toBe(false);
  if (!supported) {
    expect(recorder.state).toBe('idle');
    expect(begin).not.toHaveBeenCalled();
    expect(calls).toEqual([]);
    expect(track.readyState).toBe('ended');
    expect(messages.at(-1)).toContain('cannot encode the annotated view');
    return;
  }
  expect(Recorder.last.options?.mimeType).toBe(supported);
  expect(JSON.parse(String(calls.find(call => call.path === '/api/recordings/start')?.body))).toMatchObject({ext});
  // A successful dataset/finalization response must not mask an encoder failure.
  Recorder.last.onerror?.();
  await vi.waitFor(() => expect(recorder.state).toBe('idle'));
  expect(messages.at(-1)).toContain('not a complete replay');
  expect(messages.some(message => message.startsWith('Saved the annotated view'))).toBe(false);
});
