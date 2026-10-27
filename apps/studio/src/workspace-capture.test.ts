/** TEST-only optional tab capture at the real Session/Connection/Reporter boundary.
 * DOM, server wire, returned media track, video frames and JPEG encoder are EXTERNAL
 * scripted facts. No browser pixels, App authority, atomic paint or source proof is
 * claimed. The real Session must join these facts and own every callback/resource.
 */
import {afterEach, beforeEach, expect, test, vi} from 'vitest';
import {Connection} from './protocol';
import type {ServerMessage, WorkspaceReady} from './protocol';
import {ScreenReporter, WorkspaceSession} from './screen';
import {PanelStack} from './panels';
import {syntheticDOM, TestElement, TestImage} from './workspace-windows.test-support';
import {syntheticPointerPlatform} from './workspace-runtime.test-support';

const ORIGIN = 'http://127.0.0.1:5173';
const proposal = {schema: 'workspace-origin-v2', workspaceId: 'capture-workspace', clientId: 'capture-client', pageEpoch: 'capture-page'} as const;
type Wire = Record<string, any>; // external JSON wire, checked at each observation
class Socket {
  static readonly CONNECTING=0; static readonly OPEN=1; static readonly CLOSING=2; static readonly CLOSED=3;
  static all: Socket[]=[];
  readyState=0; binaryType=''; sent: string[]=[];
  onopen: ((e: Event)=>unknown)|null=null; onclose: ((e: Event)=>unknown)|null=null; onmessage: ((e: MessageEvent)=>unknown)|null=null;
  readonly url: string;
  constructor(url: string) {this.url=url; Socket.all.push(this);}
  send(data: string) {this.sent.push(data);}
  open() {this.readyState=1; this.onopen?.(new Event('open'));}
  close() {this.readyState=3; this.onclose?.(new Event('close'));}
  receive(value: unknown) {return this.onmessage?.({data: JSON.stringify(value)} as MessageEvent);}
  wire(): Wire[] {return this.sent.map(v=>JSON.parse(v));}
}
class Track extends EventTarget {
  readonly id='external-returned-track'; kind='video'; enabled=true; readyState='live'; muted=false; stopped=0; handleReads=0; settingsReads=0;
  handle: {origin: string; handle: string}|null={origin: ORIGIN, handle: proposal.pageEpoch};
  surface='browser'; width=2400; height=1600;
  getCaptureHandle() {this.handleReads++; return this.handle && {...this.handle};}
  getSettings() {this.settingsReads++; return {displaySurface:this.surface, width:this.width, height:this.height};}
  stop() {this.stopped++; this.readyState='ended';}
  mute() {this.muted=true; this.dispatchEvent(new Event('mute'));}
  unmute() {this.muted=false; this.dispatchEvent(new Event('unmute'));}
  end() {this.readyState='ended'; this.dispatchEvent(new Event('ended'));}
}
class Video extends TestElement {
  private stream: unknown=null;
  get srcObject() {return this.stream;}
  set srcObject(value: unknown) {this.stream=value; if(value!==null)queueMicrotask(()=>this.dispatchEvent(new Event('loadedmetadata')));}
  videoWidth=2400; videoHeight=1600; readyState=4; currentTime=12; paused=true; playsInline=false; autoplay=false; muted=false;
  callbacks=new Map<number, VideoFrameRequestCallback>(); registered=0; cancelled=0;
  constructor() {super('video'); videos.push(this);}
  async play() {this.paused=false; queueMicrotask(()=>this.dispatchEvent(new Event('loadeddata')));}
  pause() {this.paused=true;}
  requestVideoFrameCallback(callback: VideoFrameRequestCallback) {const id=++this.registered; this.callbacks.set(id,callback); return id;}
  cancelVideoFrameCallback(id: number) {this.cancelled++; this.callbacks.delete(id);}
  frame() {
    for(const [id, callback] of [...this.callbacks]) {this.callbacks.delete(id); callback(now, {
      width:this.videoWidth,height:this.videoHeight,mediaTime:this.currentTime,presentedFrames:1,
      presentationTime:now,expectedDisplayTime:now,processingDuration:0,
    });}
  }
}
const ENCODED = new Uint8Array([255,216,255,224,23,42,255,217]); // opaque EXTERNAL encoder bytes; Python tracer uses real decoded JPEG
class Canvas extends TestElement {
  width=0; height=0;
  draws: unknown[][]=[]; encoder: BlobCallback|null=null; encoderType: string|undefined;
  constructor() {super('canvas'); canvases.push(this);}
  getContext() {return {drawImage:(...args: unknown[])=>this.draws.push(args)};}
  toBlob(callback: BlobCallback, type?: string) {this.encoderType=type; this.encoder=callback;}
  finish(bytes: Uint8Array=ENCODED) {const callback=this.encoder; this.encoder=null; callback?.(new Blob([new Uint8Array(bytes).buffer],{type:'image/jpeg'}));}
}
const videos: Video[]=[], canvases: Canvas[]=[], sessions: WorkspaceSession[]=[];
let dom: ReturnType<typeof syntheticDOM>, now=1000;
let picker: ReturnType<typeof vi.fn>, configuration: ReturnType<typeof vi.fn>;
let pick: ()=>Promise<unknown>;
let activated=false;
const pickerEntries: {activated: boolean; configuration: unknown}[]=[];
const tick=async()=>{for(let i=0;i<20;i++) await Promise.resolve();};
beforeEach(()=>{
  dom=syntheticDOM(); syntheticPointerPlatform(dom).bind(dom.host); now=1000;
  Socket.all=[]; videos.length=canvases.length=0; vi.useFakeTimers();
  const create=dom.document.createElement;
  dom.document.createElement=(tag:string)=>tag==='video'?new Video():tag==='canvas'?new Canvas():create(tag);
  Object.defineProperties(dom.document,{visibilityState:{value:'visible'},hidden:{value:false},hasFocus:{value:()=>true},baseURI:{value:ORIGIN+'/'} });
  configuration=vi.fn(); activated=false; pickerEntries.length=0;
  pick=async()=>{throw new Error('unexpected automatic picker');};
  picker=vi.fn(()=>{pickerEntries.push({activated,configuration:configuration.mock.calls.at(-1)?.[0]});return pick();});
  vi.stubGlobal('document',dom.document); vi.stubGlobal('window',globalThis);
  vi.stubGlobal('HTMLElement',TestElement); vi.stubGlobal('HTMLImageElement',TestImage); vi.stubGlobal('HTMLVideoElement',Video); vi.stubGlobal('HTMLCanvasElement',Canvas);
  vi.stubGlobal('location',{protocol:'http:',host:'127.0.0.1:5173',origin:ORIGIN,href:ORIGIN+'/'});
  vi.stubGlobal('navigator',{mediaDevices:{getDisplayMedia:picker,setCaptureHandleConfig:configuration}});
  vi.stubGlobal('WebSocket',Socket); vi.stubGlobal('requestAnimationFrame',dom.raf);
  vi.stubGlobal('innerWidth',1200); vi.stubGlobal('innerHeight',800);
  vi.stubGlobal('getComputedStyle',(node:TestElement)=>({...node.style,display:node.hidden?'none':'block'}));
});
afterEach(()=>{for(const s of sessions.splice(0))s.dispose(); for(const socket of Socket.all)socket.onopen=socket.onclose=socket.onmessage=null; vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();});
async function accepted(connectionEpoch='external-original-socket', onWorkspaceSharingChange?: (state:string)=>void) {
  const pickerCount=picker.mock.calls.length;
  const root=new TestElement('section'); root.id='actual-observed-panel'; dom.host.append(root);
  let viewport={layoutSize:[1200,800],visualRect:[0,0,1200,800],visualScale:1,documentScroll:[0,0],dpr:2,visible:true,documentFocused:true,activeSurface:'workspace'};
  const openings: Promise<void>[]=[], errors: Error[]=[]; let session!:WorkspaceSession;
  const connection=new Connection(()=>{},message=>{session.message(message);},connected=>{const p=session.connected(connected);openings.push(p);void p.catch(e=>errors.push(e));});
  const reporter=new ScreenReporter(value=>connection.send(value),()=>[],0,10_000);
  session=new WorkspaceSession({connection,reporter,stage:dom.host as unknown as HTMLElement,interactionHost:dom.host as unknown as HTMLElement,panels:new PanelStack(),proposal,
    legacy:()=>({surfaces:[{id:'observed-panel',root:root as unknown as HTMLElement,read:()=>({kind:'viewer',open:true,visible:true,rect:(()=>{const r=root.getBoundingClientRect();return [r.left,r.top,r.width,r.height];})(),z:1,space:'picture',contentKey:'external-visible-work',
      legacy:{schema:'studio-surface-v1',scope:'dom-observation',title:'External observed written work'},controls:[]})}],coverage:{complete:false,omittedItems:1,unknown:['other-roots-unenumerated']}}),
    viewport:()=>viewport,frame:()=>7,selected:()=>({presentations:[],biological:[]}),windowFacts:()=>null,
    platform:{fetch:async()=>new Response(null,{status:404}),createObjectURL:()=>{throw new Error('no native source URL requested');},revokeObjectURL:()=>{},now:()=>now,sourceOrigin:ORIGIN},onError:e=>errors.push(e),onWorkspaceSharingChange});
  sessions.push(session); connection.open(); const socket=Socket.all.at(-1)!; socket.open();
  const hello=socket.wire().find(v=>v.type==='workspace-hello'); expect(hello).toMatchObject(proposal);
  const ready:WorkspaceReady={type:'workspace-ready',schema:'workspace-origin-v2',ok:true,for:'workspace-hello',requestId:hello!.requestId,
    client:{workspaceId:proposal.workspaceId,clientId:proposal.clientId,pageEpoch:proposal.pageEpoch,connectionEpoch},appRunId:'external-app-run',connectionGrant:'external-server-grant'};
  await socket.receive(ready); await openings.at(-1); await tick();
  session.ask('What is visible?'); const inline=socket.wire().find(v=>v.type==='ask')!.screen;
  expect(inline.client).toEqual(ready.client); expect(inline.items.some((i:Wire)=>i.id==='observed-panel'&&i.open&&i.connected)).toBe(true);
  expect(inline.items.find((i:Wire)=>i.id==='observed-panel').view.legacy.title).toBe('External observed written work');
  expect(errors).toEqual([]); expect(picker).toHaveBeenCalledTimes(pickerCount);
  const request={type:'screen-look',schema:'workspace-capture-v1',id:'original-look',lookId:'original-look',requestId:'original-request',appRunId:ready.appRunId,client:ready.client,
    originSnapshotId:'server-original-send-pin',layoutRevision:inline.layoutRevision,targetIds:[],maxBytes:6*1024*1024,maxPixels:4_000_000,maxInputEdge:1920,deadlineMono:90,timeoutMs:5000};
  return {session,connection,reporter,socket,ready,inline,request,root,openings,setViewport:(value:typeof viewport)=>{viewport=value;}};
}
type World=Awaited<ReturnType<typeof accepted>>;
function replies(w:World) {return w.socket.wire().filter(v=>v.type==='screen-image');}
function receive(w:World, request=w.request) {expect(w.session.message(request as unknown as ServerMessage),'MISSING_FEATURE: canonical capture is owned by the accepted Session, never main legacy compositor').toBe(true);}
function unavailable(w:World,code:string) {expect(replies(w)).toEqual([{type:'screen-image',schema:'workspace-capture-v1',status:'unavailable',id:w.request.id,lookId:w.request.lookId,requestId:w.request.requestId,appRunId:w.ready.appRunId,client:w.ready.client,originSnapshotId:w.request.originSnapshotId,code}]);}
function method(w:World,name:'shareWorkspaceTab'|'stopWorkspaceSharing') {const value=Reflect.get(w.session,name);expect(typeof value,`MISSING_FEATURE: real accepted Session.${name}`).toBe('function');return ()=>Reflect.apply(value,w.session,[]);}
function activate(w:World) {
  // External transient user activation ends as the synchronous event returns.
  // No core guard/state or browser time constant is changed.
  activated=true;
  try {return method(w,'shareWorkspaceTab')();} finally {activated=false;}
}
async function reaccept(w:World) {
  const pickerCount=picker.mock.calls.length;
  w.socket.close(); await tick(); w.connection.open();
  const replacement=Socket.all.at(-1)!; expect(replacement).not.toBe(w.socket); replacement.open();
  const hello=replacement.wire().find(v=>v.type==='workspace-hello')!;
  const ready={...w.ready,requestId:hello.requestId,client:{...w.ready.client,connectionEpoch:'external-replacement-socket'}};
  await replacement.receive(ready); await w.openings.at(-1); await tick();
  w.session.ask('New connection structural control.');
  const inline=replacement.wire().find(v=>v.type==='ask')!.screen;
  expect(inline.client).toEqual(ready.client);
  expect(picker).toHaveBeenCalledTimes(pickerCount);
  return {...w,socket:replacement,ready,inline,request:{...w.request,client:ready.client,layoutRevision:inline.layoutRevision,originSnapshotId:'server-replacement-send-pin'}};
}
async function share(w:World,track=new Track()) {const count=picker.mock.calls.length;const stream={getVideoTracks:()=>[track],getTracks:()=>[track]};pick=async()=>stream;await activate(w);await tick();expect(picker).toHaveBeenCalledTimes(count+1);expect(track.handleReads).toBeGreaterThan(0);expect(pickerEntries.slice(count)).toEqual([{activated:true,configuration:{exposeOrigin:true,handle:proposal.pageEpoch,permittedOrigins:[ORIGIN]}}]);return {track,stream};}
async function waitingSample(w:World) {
  const before=replies(w), drawCounts=new Map(canvases.map(c=>[c,c.draws.length]));
  receive(w); await tick();
  const video=videos.find(v=>v.srcObject!==null);
  expect(video,'actual returned-track video sink').toBeDefined(); expect(video!.callbacks.size).toBe(1); expect(replies(w)).toEqual(before);
  video!.frame(); await tick();
  const canvas=canvases.find(c=>c.encoder);
  expect(canvas,'new frame must reach real encoder callback').toBeDefined();
  expect(canvas!.draws.length).toBeGreaterThan(drawCounts.get(canvas!)??0);
  expect(canvas!.draws.at(-1)![0]).toBe(video); expect(canvas!.encoderType).toBe('image/jpeg');
  return {video:video!,canvas:canvas!};
}
function captureIdentity(reply:Wire) {
  const identity=reply.acquisitionBasis.captureSessionId;
  expect(typeof identity).toBe('string'); expect(identity.length).toBeGreaterThan(0); expect(identity.length).toBeLessThanOrEqual(256);
  return identity as string;
}
async function completedSample(w:World) {
  const count=replies(w).length; const {video,canvas}=await waitingSample(w); canvas.finish();
  await vi.waitFor(()=>expect(replies(w)).toHaveLength(count+1));
  const reply=replies(w).at(-1)!;
  expect(reply).toMatchObject({status:'captured',lookId:w.request.lookId,requestId:w.request.requestId,client:w.ready.client,originSnapshotId:w.request.originSnapshotId});
  expect(Uint8Array.from(atob(reply.jpeg),c=>c.charCodeAt(0))).toEqual(ENCODED);
  const wire=w.socket.wire(),position=wire.findIndex(v=>v.type==='screen-image'&&v.lookId===w.request.lookId);
  expect(wire[position-1].type).toBe('screen'); expect(reply.acquisitionBasis.structuralReportSeq).toBe(wire[position-1].seq);
  return {reply,video};
}

test('useful accepted Session Send remains structural and never starts sharing automatically',async()=>{const w=await accepted();expect(w.inline.sources).toEqual([]);expect(replies(w)).toEqual([]);});
test('unshared canonical request immediately returns original correlated share-required data',async()=>{const w=await accepted();receive(w);await tick();unavailable(w,'share-required');expect(picker).not.toHaveBeenCalled();expect(canvases).toEqual([]);});
test('owned returned track exposes active preparing status and Stop before held video play settles',async()=>{
  const states:string[]=[];
  const w=await accepted('external-original-socket',state=>states.push(state));
  const track=new Track(),stream={getVideoTracks:()=>[track],getTracks:()=>[track]};
  pick=async()=>stream;
  let releasePlay!:()=>void;
  const heldPlay=new Promise<void>(done=>{releasePlay=done;});
  // Hold only the returned browser video's external readiness boundary. Session
  // ownership, notification, Stop and original-pair guards remain production code.
  const play=vi.spyOn(Video.prototype,'play').mockImplementation(function(this:Video){this.paused=false;return heldPlay;});
  try {
    const pending=activate(w); await tick();
    expect(picker).toHaveBeenCalledTimes(1); expect(play).toHaveBeenCalledTimes(1);
    expect(pickerEntries).toEqual([{activated:true,configuration:{exposeOrigin:true,handle:proposal.pageEpoch,permittedOrigins:[ORIGIN]}}]);
    expect(track.handleReads).toBeGreaterThan(0); expect(track.settingsReads).toBeGreaterThan(0);
    expect(track.readyState).toBe('live'); expect(track.stopped).toBe(0);
    expect(videos).toHaveLength(1); const video=videos[0];
    expect(video.srcObject).toBe(stream); expect(video.paused).toBe(false);
    expect(replies(w)).toEqual([]); expect(canvases).toEqual([]);
    const ownedState=states.at(-1);

    // Exercise cleanup before the missing status assertion, so a status RED still
    // reaches actual Stop, cancellation of readiness and the late browser result.
    method(w,'stopWorkspaceSharing')(); await pending; await tick();
    expect(track.stopped).toBe(1); expect(track.readyState).toBe('ended');
    expect(video.srcObject).toBeNull(); expect(video.paused).toBe(true);
    expect(video.callbacks.size).toBe(0); expect(states.at(-1)).toBe('share-required');
    const stoppedStates=[...states];
    releasePlay(); await tick();
    expect(states).toEqual(stoppedStates); expect(track.stopped).toBe(1);
    expect(video.srcObject).toBeNull(); expect(video.callbacks.size).toBe(0);
    expect(picker).toHaveBeenCalledTimes(1); expect(canvases).toEqual([]);
    receive(w); await tick(); unavailable(w,'share-required');
    expect(replies(w).filter(v=>v.status==='captured')).toEqual([]);
    expect(['preparing','sharing'],'STC-S1: an owned live track must expose active/preparing status through the actual Session callback before video readiness, making Stop available').toContain(ownedState);
  } finally {releasePlay();play.mockRestore();}
});
test('returned same-tab track frame and encoder supply one paired report with unknown root paint',async()=>{
  const w=await accepted(); const {track,stream}=await share(w); const {video,canvas}=await waitingSample(w);
  expect(video.srcObject).toBe(stream); expect(track.stopped).toBe(0); expect([canvas.width,canvas.height]).toEqual([1920,1280]);
  canvas.finish(); await vi.waitFor(()=>expect(replies(w)).toHaveLength(1));
  const wire=w.socket.wire(),reply=replies(w)[0];
  expect(reply).toMatchObject({schema:'workspace-capture-v1',status:'captured',client:w.ready.client,originSnapshotId:w.request.originSnapshotId,originalSize:[2400,1600],inputSize:[1920,1280],workspaceToImage:[1.6,0,0,1.6,0,0],fidelity:'actual-tab-capture',captureMethod:'authorized-tab-track',surfaceProof:'same-origin-page-handle',coverage:'complete',missing:[]});
  expect(replies(w)).toHaveLength(1); expect(Uint8Array.from(atob(reply.jpeg),c=>c.charCodeAt(0))).toEqual(ENCODED);
  const report=wire[wire.findIndex(v=>v.type==='screen-image'&&v.status==='captured')-1]; expect(report.type).toBe('screen');
  expect(reply.acquisitionBasis).toMatchObject({schema:'installed-studio-tab-v1',surfaceOrigin:ORIGIN,pageHandle:proposal.pageEpoch,structuralReportSeq:report.seq,viewport:report.viewport,rootPaintInclusion:'unknown',rootPaintReason:'tab-track-has-no-atomic-root-paint-manifest'});
  const identity=captureIdentity(reply);
  expect(report.items.some((i:Wire)=>i.id==='observed-panel')).toBe(true);
  expect(reply.acquisitionBasis.sampledBeforeMonoMs).toBeLessThanOrEqual(reply.captureTimeMonoMs); expect(reply.acquisitionBasis.sampledAfterMonoMs).toBeGreaterThanOrEqual(reply.captureTimeMonoMs);
  w.request={...w.request,id:'same-track-look',lookId:'same-track-look',requestId:'same-track-request'};
  const next=await completedSample(w);
  expect(next.video.srcObject).toBe(stream); expect(track.stopped).toBe(0);
  expect(captureIdentity(next.reply)).toBe(identity); expect(picker).toHaveBeenCalledTimes(1);
});
test.each(['stop','dispose','reconnect'] as const)('new actual track resource after %s has a distinct opaque capture identity',async(reason)=>{
  let w=await accepted(); const original=await share(w); const first=await completedSample(w); const identity=captureIdentity(first.reply);
  if(reason==='stop')method(w,'stopWorkspaceSharing')();
  if(reason==='dispose') {w.session.dispose(); w.socket.close(); w.root.remove(); await tick(); w=await accepted('external-disposed-replacement-socket');}
  if(reason==='reconnect')w=await reaccept(w);
  await tick(); expect(original.track.stopped).toBe(1); expect(first.video.srcObject).toBeNull(); expect(first.video.callbacks.size).toBe(0);
  const track=new Track(); expect(track).not.toBe(original.track); expect(track.id).toBe(original.track.id);
  const replacement=await share(w,track);
  w.request={...w.request,id:'replacement-track-look',lookId:'replacement-track-look',requestId:'replacement-track-request'};
  const next=await completedSample(w);
  expect(next.video.srcObject).toBe(replacement.stream); expect(track.stopped).toBe(0);
  expect(captureIdentity(next.reply)).not.toBe(identity); expect(picker).toHaveBeenCalledTimes(2);
});
test.each(['wrong-tab','wrong-origin','window','monitor','missing-handle'] as const)('rejects actual returned %s surface and stops its track',async(reason)=>{const w=await accepted(),track=new Track();if(reason==='wrong-tab')track.handle!.handle='other-page';if(reason==='wrong-origin')track.handle!.origin='http://localhost:9999';if(reason==='window'||reason==='monitor')track.surface=reason;if(reason==='missing-handle')track.handle=null;pick=async()=>({getVideoTracks:()=>[track],getTracks:()=>[track]});await activate(w);await tick();expect(track.stopped).toBe(1);if(reason==='window'||reason==='monitor')expect(track.settingsReads).toBeGreaterThan(0);else expect(track.handleReads).toBeGreaterThan(0);receive(w);await tick();expect(replies(w)[0]).toMatchObject({status:'unavailable',code:reason==='missing-handle'?'unsupported-handle':'wrong-surface'});expect(canvases).toEqual([]);});
test('mute refuses, and a fresh unmuted frame can recover on the same owned track',async()=>{const w=await accepted();const {track}=await share(w);track.mute();receive(w);await tick();unavailable(w,'track-unavailable');track.unmute();w.request={...w.request,id:'recovery-look',lookId:'recovery-look',requestId:'recovery-request'};const {canvas}=await waitingSample(w);canvas.finish();await vi.waitFor(()=>expect(replies(w)).toHaveLength(2));expect(replies(w).at(-1)).toMatchObject({status:'captured',lookId:'recovery-look'});});
test.each(['stop','ended','reconnect'] as const)('held encode cannot publish success after %s and cleans original media callbacks',async(reason)=>{const w=await accepted();const {track}=await share(w);const {video,canvas}=await waitingSample(w);if(reason==='stop')method(w,'stopWorkspaceSharing')();if(reason==='ended')track.end();if(reason==='reconnect'){await reaccept(w);}canvas.finish();await tick();expect(Socket.all.flatMap(s=>s.wire()).filter(v=>v.type==='screen-image'&&v.status==='captured')).toEqual([]);expect(video.srcObject).toBeNull();expect(video.callbacks.size).toBe(0);if(reason!=='ended')expect(track.stopped).toBe(1);});
test('layout change during held encoding immediately refuses original nonce, never success',async()=>{const w=await accepted();await share(w);const {canvas}=await waitingSample(w);w.root.style.left='77px';w.reporter.askNow();w.reporter.tick(++now);canvas.finish();await vi.waitFor(()=>expect(replies(w)).toHaveLength(1));unavailable(w,'layout-changed');});
test('late picker result after original connection retirement is stopped and never borrowed',async()=>{const w=await accepted(),track=new Track();let resolve!:(s:unknown)=>void;pick=()=>new Promise(done=>{resolve=done;});const pending=activate(w);await tick();w.socket.close();await tick();resolve({getVideoTracks:()=>[track],getTracks:()=>[track]});await pending;await tick();expect(track.stopped).toBe(1);expect(videos.every(v=>v.srcObject===null)).toBe(true);expect(replies(w)).toEqual([]);});
test('stop cancels the original waiting frame callback and a late invocation cannot encode',async()=>{
  const w=await accepted(); const {track}=await share(w); receive(w); await tick();
  const video=videos.find(v=>v.srcObject!==null)!;
  expect(video.callbacks.size).toBe(1);
  const callback=[...video.callbacks.values()][0];
  method(w,'stopWorkspaceSharing')(); await tick();
  expect(video.cancelled).toBeGreaterThan(0); expect(video.callbacks.size).toBe(0);
  expect(video.srcObject).toBeNull(); expect(track.stopped).toBe(1);
  callback(now,{width:2400,height:1600,mediaTime:13,presentedFrames:2,presentationTime:now,expectedDisplayTime:now,processingDuration:0});
  await tick(); expect(canvases.filter(c=>c.encoder)).toEqual([]);
  expect(replies(w).filter(v=>v.status==='captured')).toEqual([]);
});
test('declined user picker is data and never starts a second automatic picker',async()=>{
  const w=await accepted(); pick=async()=>{throw new DOMException('external decline','NotAllowedError');};
  await activate(w); receive(w); await tick();
  unavailable(w,'permission-declined'); expect(picker).toHaveBeenCalledTimes(1); expect(videos).toEqual([]);
});
test.each(['pixel-bound','visual-offset'] as const)('unsupported measured %s produces data without encoding',async(reason)=>{const w=await accepted(),track=new Track();if(reason==='pixel-bound'){track.width=3000;track.height=2000;}await share(w,track);if(reason==='visual-offset')w.setViewport({layoutSize:[1200,800],visualRect:[5,0,1195,800],visualScale:1,documentScroll:[0,0],dpr:2,visible:true,documentFocused:true,activeSurface:'workspace'});if(reason==='visual-offset'){w.session.ask('Inspect this viewport.');const pinned=w.socket.wire().filter(v=>v.type==='ask').at(-1)!.screen;w.request={...w.request,layoutRevision:pinned.layoutRevision,originSnapshotId:'server-original-offset-pin'};}receive(w);await tick();for(const video of videos){video.videoWidth=track.width;video.videoHeight=track.height;video.frame();}await tick();expect(replies(w)[0]).toMatchObject({status:'unavailable',code:reason==='pixel-bound'?'bounds-exceeded':'unsupported-mapping'});expect(canvases.every(c=>c.encoder===null)).toBe(true);});
