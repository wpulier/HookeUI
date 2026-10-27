/// <reference types="node" />
// TEST-only native-platform lane: actual View/Magnifier/getter/main-shared producer,
// Connection/Session/Reporter, and the actual accepted Python Screen consumer.
// Image completion/CSS/rect/RAF/socket/clock are synthetic facts, not browser pixels,
// actual main HTML bootstrap, App/provider/model/camera or full-library evidence.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { spawn } from 'node:child_process';
import type { ChildProcessWithoutNullStreams } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import process from 'node:process';
import { Connection } from './protocol';
import type { ClientCommand, FrameBasis, FrameHeader, MagnifierState, ServerMessage, WorkspaceReady } from './protocol';
import { PanelStack, presentPlace } from './panels';
import { View } from './view';
import { tracksScreenItem, type MotionTracksWire, type TracksLayerWire } from './motiontracks';
import { populationScreenItem, type PopulationWire, type LayerWire } from './population';
import { MagnifierPanel } from './magnifier';
import { magnifierSurface, rectOf, ScreenReporter, surfaceVisible, WorkspaceSession } from './screen';
import type { PresentMessage, ScreenItem, StudioSurface, WorkspaceObservation } from './screen';
import { TestElement } from './workspace-windows.test-support';

// Only missing native APIs/geometry facts supplement the unchanged external helper.
// There is no copied HTML tree, content signature, observer or installation algorithm.
class NativeElement extends TestElement {
  bounds: DOMRect | null = null;
  get clientWidth() { return this.offsetWidth; }
  get clientHeight() { return this.offsetHeight; }
  override getBoundingClientRect(): DOMRect { return this.bounds ?? super.getBoundingClientRect(); }
  override matches(selector: string): boolean {
    if (selector === '[hidden]') return this.hidden;
    if (selector === ':disabled') return this.disabled;
    if (selector.startsWith('#')) return this.id === selector.slice(1);
    return super.matches(selector);
  }
  toggleAttribute(name: string, force?: boolean): boolean {
    const present = force ?? this.getAttribute(name) === null;
    if (present) this.setAttribute(name, ''); else delete this.attributes[name];
    return present;
  }
}
class NativeCanvas extends NativeElement {
  width = 0; height = 0;
  constructor() { super('canvas'); }
  // No drawing is tested. Actual View is paused before its delivered first frame.
  getContext(kind: string) { if (kind !== '2d') throw new Error('unsupported native canvas context'); return {}; }
}
class NativeImage extends NativeElement {
  complete = false; naturalWidth = 0; naturalHeight = 0;
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  removeAttribute(name: string) { delete this.attributes[name]; }
  constructor() { super('img'); }
  get src() { return this.getAttribute('src') ?? ''; }
  set src(value: string) { this.setAttribute('src', value); this.complete = false; this.naturalWidth = this.naturalHeight = 0; }
  finishLoad() { this.complete = true; this.naturalWidth = 640; this.naturalHeight = 480; this.onload?.(); }
}
class NativeSocket {
  static readonly OPEN = 1; static readonly CONNECTING = 0; static readonly CLOSING = 2; static readonly CLOSED = 3;
  static instances: NativeSocket[] = [];
  readyState = NativeSocket.CONNECTING; binaryType = ''; sent: string[] = [];
  onopen: (() => void) | null = null; onclose: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void | Promise<void>) | null = null;
  constructor(_url: string) { NativeSocket.instances.push(this); }
  send(value: string) { if (this.readyState !== NativeSocket.OPEN) throw new Error('native socket is closed'); this.sent.push(value); }
  close() { this.readyState = NativeSocket.CLOSED; }
  opened() { this.readyState = NativeSocket.OPEN; this.onopen?.(); }
  disconnected() { this.readyState = NativeSocket.CLOSED; this.onclose?.(); }
  async text(message: ServerMessage | { type: 'session-cleared' }) { await this.onmessage?.({ data: JSON.stringify(message) }); }
}
interface ConsumerReply {
  ok: boolean; accepted?: boolean; code?: string; detail?: string; confirmed?: boolean;
  wire?: WorkspaceObservation | PresentMessage; seq?: number; timing?: string;
  before?: Extract<ServerMessage, { type: 'day' }>; after?: Extract<ServerMessage, { type: 'day' }>;
  messages?: (ServerMessage | { type: 'session-cleared' })[];
}
class Consumer {
  private readonly child: ChildProcessWithoutNullStreams;
  private pending: { resolve(value: ConsumerReply): void; reject(error: Error): void } | null = null;
  private output = ''; private errors = ''; private terminal: Error | null = null;
  constructor() {
    const repo = fileURLToPath(new URL('../../../', import.meta.url));
    const python = process.env.MICROMIND_LEGACY_PYTHON ?? join(repo, 'services/.venv/bin/python');
    this.child = spawn(python, ['tests/test_workspace_legacy_browser_reports.py', '--stdio'], {
      cwd: join(repo, 'services'), env: { ...process.env, PYTHONPATH: join(repo, 'services') }, stdio: 'pipe',
    });
    this.child.stdout.setEncoding('utf8'); this.child.stderr.setEncoding('utf8');
    this.child.stdout.on('data', (chunk: string) => {
      this.output += chunk;
      if (this.output.length > 2 * 1024 * 1024) { this.fail(new Error('consumer output exceeded bound')); return; }
      const end = this.output.indexOf('\n'); if (end < 0) return;
      const line = this.output.slice(0, end); this.output = this.output.slice(end + 1);
      const pending = this.pending; this.pending = null;
      if (!pending) { this.fail(new Error('unexpected consumer output')); return; }
      try { pending.resolve(JSON.parse(line) as ConsumerReply); } catch (error) { pending.reject(new Error(`invalid consumer reply: ${String(error)}`)); }
    });
    this.child.stderr.on('data', (chunk: string) => { this.errors = (this.errors + chunk).slice(-10000); });
    this.child.on('error', (error) => this.fail(error));
    this.child.on('exit', (code) => this.fail(new Error(`actual consumer exited ${code}: ${this.errors}`)));
  }
  private fail(error: Error) { this.terminal = error; this.pending?.reject(error); this.pending = null; }
  async rpc(payload: Record<string, unknown>): Promise<ConsumerReply> {
    if (this.terminal) throw this.terminal;
    if (this.pending) throw new Error('test driver requires serial actual consumer operations');
    return new Promise((resolve, reject) => {
      this.pending = { resolve, reject }; this.child.stdin.write(JSON.stringify(payload) + '\n');
    });
  }
  dispose() { this.child.stdin.end(); this.child.kill(); }
}

let now = 1000;
let body: NativeElement, stage: NativeElement, canvas: NativeCanvas;
let frames: FrameRequestCallback[] = [];
const consumers: Consumer[] = [], sessions: WorkspaceSession[] = [];
function all(node: TestElement): TestElement[] { return [node, ...node.children.flatMap(all)]; }
function nativeRect(width: number, height: number): DOMRect {
  return { x: 0, y: 0, left: 0, top: 0, right: width, bottom: height, width, height, toJSON: () => ({}) };
}
function frame() { now++; for (const callback of frames.splice(0)) callback(now); }
beforeEach(() => {
  now = 1000; frames = []; NativeSocket.instances = [];
  body = new NativeElement('body'); stage = new NativeElement('main'); stage.id = 'stage';
  stage.style.width = body.style.width = '1200px'; stage.style.height = body.style.height = '800px';
  canvas = new NativeCanvas(); canvas.id = 'view'; canvas.style.width = '1200px'; canvas.style.height = '800px';
  body.append(stage); stage.append(canvas);
  const document = Object.assign(new EventTarget(), { body, visibilityState: 'visible', hidden: false,
    baseURI: 'https://synthetic.invalid/', hasFocus: () => true,
    getElementById: (id: string) => all(body).find((node) => node.id === id) ?? null,
    createElement: (tag: string) => tag === 'img' ? new NativeImage() : tag === 'canvas' ? new NativeCanvas() : new NativeElement(tag) });
  vi.stubGlobal('document', document); vi.stubGlobal('window', globalThis);
  vi.stubGlobal('HTMLElement', TestElement); vi.stubGlobal('HTMLImageElement', NativeImage); vi.stubGlobal('WebSocket', NativeSocket);
  vi.stubGlobal('location', { protocol: 'https:', host: 'synthetic.invalid', origin: 'https://synthetic.invalid' });
  vi.stubGlobal('innerWidth', 1200); vi.stubGlobal('innerHeight', 800); vi.stubGlobal('devicePixelRatio', 1);
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { frames.push(callback); return frames.length; });
  vi.stubGlobal('getComputedStyle', (node: NativeElement) => ({ display: 'block', visibility: 'visible', opacity: '1', ...node.style }));
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} });
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] }); // Connection's retry only; no driver/model time fakery.
});
afterEach(() => { sessions.splice(0).forEach((session) => session.dispose()); consumers.splice(0).forEach((consumer) => consumer.dispose()); vi.useRealTimers(); vi.unstubAllGlobals(); });

const A = '/research/synthetic-primary.png', B = '/research/synthetic-peer.png';
function pictureState(peer = false, scale = 1): MagnifierState {
  return { type: 'magnifier', space: 'picture', open: true, objectId: null, index: 0,
    sources: [{ kind: 'image', space: 'picture', label: 'Synthetic external pixels', url: A, displayScale: scale }],
    beside: peer ? { kind: 'image', space: 'picture', label: 'Synthetic peer pixels', url: B, displayScale: 1 } : null };
}
function panelDOM() {
  const root = new NativeElement('section'); root.id = 'magnifier'; root.hidden = true;
  const picture = new NativeElement('div'); picture.id = 'magnifier-picture';
  const image = new NativeImage(); image.id = 'magnifier-image';
  const live = new NativeCanvas(); live.id = 'magnifier-live';
  const peerPane = new NativeElement('figure'); peerPane.id = 'magnifier-beside';
  const peer = new NativeImage(); peer.id = 'magnifier-beside-image'; peerPane.append(peer);
  picture.append(live, image, peerPane); root.append(picture);
  for (const id of ['magnifier-bar', 'magnifier-kind', 'magnifier-beside-kind', 'magnifier-caption', 'magnifier-dots', 'magnifier-credit', 'magnifier-zoom', 'magnifier-less', 'magnifier-more', 'magnifier-close', 'magnifier-grip']) {
    const node = new NativeElement(id.includes('less') || id.includes('more') || id.includes('close') ? 'button' : 'div'); node.id = id; root.append(node);
  }
  stage.append(root); return { root, image, peer };
}
function newest() { const socket = NativeSocket.instances.at(-1); if (!socket) throw new Error('missing native socket'); return socket; }
function row(report: WorkspaceObservation, id = 'magnifier') {
  const result = report.items.find((item) => item.id === id);
  if (!result || 'sourceKey' in result) throw new Error(`missing actual structural row ${id}`); return result;
}
function outgoing(socket: NativeSocket, type: 'screen' | 'ask'): WorkspaceObservation {
  const message = socket.sent.map((text) => JSON.parse(text) as { type: string; screen?: WorkspaceObservation }).filter((value) => value.type === type).at(-1);
  if (!message) throw new Error('actual transport has no requested serialized observation');
  return type === 'ask' ? message.screen! : JSON.parse(socket.sent.filter((text) => JSON.parse(text).type === 'screen').at(-1)!) as WorkspaceObservation;
}
function canvasSurface(view: View, kind: 'live' | 'outlines'): StudioSurface {
  return { id: kind, root: view.canvas, read: () => {
    const rect = rectOf(view.canvas.getBoundingClientRect());
    const semantic = kind === 'live' || view.outlinesVisible && !view.hideOverlays;
    return { kind, open: true, visible: surfaceVisible(view.canvas, true, semantic, rect), rect, z: kind === 'live' ? 0 : 2,
      space: 'frame', legacy: { schema: 'studio-surface-v1', scope: 'dom-observation', opacity: kind === 'live' ? view.liveOpacity : view.outlinesOpacity },
      contentKey: null, controls: [] };
  } };
}
// The projection is the exact installed main-shared function. This adapter supplies
// only its existing canvas DOM/CSS facts; it cannot retain a missing producer row.
function overlaySurfaces(view: View): StudioSurface[] {
  return [tracksScreenItem(view), populationScreenItem(view)].filter((item): item is ScreenItem => item !== null).map((item) => ({
    id: item.id, root: view.canvas, read: () => {
      const open = item.open ?? true, rect = rectOf(view.canvas.getBoundingClientRect());
      return { kind: item.kind, open, visible: surfaceVisible(view.canvas, open, item.visible ?? true, rect), rect, z: item.z ?? null,
        space: 'frame', legacy: { schema: 'studio-surface-v1', scope: 'dom-observation', title: item.title, shows: item.shows,
          opacity: item.opacity, placement: item.placement, style: { colour: item.style?.colour as string | undefined } },
        contentKey: JSON.stringify([item.shows ?? null, null, null, null, null, []]), controls: [] };
    },
  }));
}
function make(overlays = false) {
  const consumer = new Consumer(); consumers.push(consumer);
  const panels = new PanelStack(), view = new View(canvas as unknown as HTMLCanvasElement); view.paused = true;
  const sendPanel = (command: ClientCommand): void => { connection.send(command); };
  let nodes = panelDOM(), panel = panels.register(new MagnifierPanel(view, panels, sendPanel)); panel.render(pictureState());
  const openings: Promise<void>[] = [], errors: Error[] = [];
  let session: WorkspaceSession;
  const connection: Connection = new Connection(() => {}, (message) => {
    if (session.message(message)) return;
    if (overlays) view.observeOverlayMessage(message); // exact installed dispatcher, not a TEST retirement callback
    if (message.type === 'present') {
      if (message.kind === 'tracks' || message.kind === 'population') {
        // Same original Change and two-paint callback as installed main. The
        // server's layer publication has already updated the real View.
        const applied = session.legacyApplied(message.change, message.id, message);
        requestAnimationFrame(() => requestAnimationFrame(applied)); return;
      }
      const applied = session.legacyApplied(message.change, message.id); // same actual public caller seam as main, before mutation
      const place = presentPlace(message.set, message.verb);
      if (place) panel.moveTo(place.x, place.y, place.over); // actual mutation, not a fabricated report
      requestAnimationFrame(() => requestAnimationFrame(applied));
    }
  }, (connected) => { openings.push(session.connected(connected)); });
  const reporter = new ScreenReporter((command) => connection.send(command), () => [], 0, 10_000);
  session = new WorkspaceSession({ connection, reporter, panels, stage: stage as unknown as HTMLElement, interactionHost: body as unknown as HTMLElement,
    proposal: { schema: 'workspace-origin-v2', workspaceId: 'synthetic-workspace', clientId: 'synthetic-client', pageEpoch: 'synthetic-page' },
    legacy: () => ({ surfaces: [magnifierSurface(panel, nodes.root as unknown as HTMLElement, () => 10), canvasSurface(view, 'live'), canvasSurface(view, 'outlines'),
      ...(overlays ? overlaySurfaces(view) : [])],
      coverage: { complete: false, omittedItems: 0, unknown: ['synthetic-native-platform', 'unenumerated-routes'] } }),
    selected: () => ({ presentations: [], biological: [] }), frame: () => view.header?.id,
    viewport: () => ({ layoutSize: [1200, 800], visualRect: [0, 0, 1200, 800], visualScale: 1, documentScroll: [0, 0], dpr: 1,
      visible: true, documentFocused: true, activeSurface: body.dataset.view ?? 'live' }),
    windowFacts: () => ({ canvas: view.canvas.getBoundingClientRect(), area: view.viewArea(), disc: view.discOnCanvas(), object: null, labels: view.labels.map((label) => label.rect) }),
    platform: { now: () => now, sourceOrigin: 'https://synthetic.invalid', fetch: () => { throw new Error('structural lane must not fetch native bytes'); },
      createObjectURL: () => { throw new Error('structural lane must not mint a native blob'); }, revokeObjectURL: () => {} },
    onError: (error) => errors.push(error) });
  sessions.push(session); connection.open();
  return { consumer, panels, view, session, connection, reporter, openings, errors,
    get nodes() { return nodes; }, get panel() { return panel; },
    replace() { panels.unregister(panel); nodes.root.remove(); nodes = panelDOM(); panel = panels.register(new MagnifierPanel(view, panels, sendPanel)); panel.render(pictureState()); },
  };
}
type World = ReturnType<typeof make>;
async function accept(world: World, key = 'current') {
  const socket = newest(); socket.opened();
  const hello = socket.sent.map((value) => JSON.parse(value) as { type: string; requestId: string }).find((value) => value.type === 'workspace-hello')!;
  const ready: WorkspaceReady = { type: 'workspace-ready', schema: 'workspace-origin-v2', ok: true, requestId: hello.requestId,
    client: { workspaceId: 'synthetic-workspace', clientId: 'synthetic-client', pageEpoch: 'synthetic-page', connectionEpoch: `synthetic-${key}` },
    appRunId: 'synthetic-run', connectionGrant: `synthetic-grant-${key}` }; // CANNED trusted transport input, not App admission.
  expect(await world.consumer.rpc({ op: 'accept', socket: key, ready })).toMatchObject({ ok: true, accepted: true });
  await socket.text(ready); await world.openings.at(-1); return socket;
}
function report(world: World, socket: NativeSocket) { now++; world.reporter.askNow(); world.reporter.tick(now); return outgoing(socket, 'screen'); }
function ask(world: World, socket: NativeSocket) { now++; world.session.ask('Synthetic renderer question'); return outgoing(socket, 'ask'); }
async function admit(world: World, socket: NativeSocket, key = 'current') {
  const wire = report(world, socket), reply = await world.consumer.rpc({ op: 'report', socket: key, wire });
  expect(reply, JSON.stringify(reply)).toMatchObject({ ok: true, accepted: true }); return wire;
}
async function pin(world: World, socket: NativeSocket, key = 'current') {
  const wire = ask(world, socket), reply = await world.consumer.rpc({ op: 'pin', socket: key, wire, requestId: `synthetic-question-${wire.seq}` });
  return { wire, reply };
}
async function change(world: World, socket: NativeSocket) {
  const reply = await world.consumer.rpc({ op: 'change' }); expect(reply, JSON.stringify(reply)).toMatchObject({ ok: true });
  if (!reply.wire || reply.wire.type !== 'present') throw new Error('actual Screen did not broadcast its original Change');
  await socket.text(reply.wire); return reply.wire;
}

// These assertions require the repaired behavior. The extracted, deliberately old
// producer must reach genuine consumer/semantic RED, never a missing-API skip.
test('real loaded getter is useful before baseline; repeated same content retains lifetime/generation and no native authority', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad();
  expect(world.panel.picturesShown()).toEqual([{ source: A, scale: 1 }]);
  const first = await admit(world, socket), second = await admit(world, socket);
  expect(row(second).lifetimeGeneration).toBe(row(first).lifetimeGeneration);
  expect(row(second).sourceGeneration).toBe(row(first).sourceGeneration); expect(row(second).revision).toBe(row(first).revision);
  expect(first.sources).toEqual([]); expect(first.commandResults).toEqual([]); expect(row(first).load.state).toBe('unknown');
  expect(row(first).propertyVersions).toEqual({}); expect(row(first).propertyOwners).toEqual({}); expect(first.complete).toBe(false);
  const sent = await pin(world, socket); expect(sent.reply).toMatchObject({ ok: true, accepted: true, timing: 'client-send', seq: sent.wire.seq });
  const close = document.getElementById('magnifier-close'); expect(close).not.toBeNull(); close!.dispatchEvent(new Event('click'));
  expect(JSON.parse(socket.sent.at(-1)!)).toMatchObject({ type: 'magnifier', action: 'close' });
  // A real control proposal is not a completed close; the server has not changed state.
  expect(world.panel.isOpen()).toBe(true); expect(world.errors).toEqual([]);
}, 20_000);

test('C1 held real primary image load advances generation and the actual canonical consumer accepts fresh inline Send', async () => {
  const world = make(), socket = await accept(world);
  expect(world.panel.picturesShown()).toEqual([]); const before = await admit(world, socket);
  world.nodes.image.finishLoad(); expect(world.panel.picturesShown()).toEqual([{ source: A, scale: 1 }]);
  const sent = await pin(world, socket);
  // This is the actual consumer's rejection on old source, retained in the failure.
  expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  expect(row(sent.wire).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(row(sent.wire).revision).toBeGreaterThan(row(before).revision);
  expect(row(sent.wire).lifetimeGeneration).toBe(row(before).lifetimeGeneration);
  expect(row(sent.wire).view.legacy.source).toBe(row(before).view.legacy.source);
  expect(sent.wire.seq).toBe(before.seq + 1); expect(before.sources).toEqual([]);
}, 20_000);

test('C1 real peer loadedness changes ordered pictures without changing source/beside metadata', async () => {
  const world = make(); world.panel.render(pictureState(true)); world.nodes.image.finishLoad();
  const socket = await accept(world), before = await admit(world, socket);
  expect(world.panel.picturesShown()).toEqual([{ source: A, scale: 1 }]);
  world.nodes.peer.finishLoad(); expect(world.panel.picturesShown()).toEqual([{ source: A, scale: 1 }, { source: B, scale: 1 }]);
  const sent = await pin(world, socket);
  expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  expect(row(sent.wire).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(row(sent.wire).view.legacy.beside).toBe(row(before).view.legacy.beside);
  expect(row(sent.wire).view.legacy.pictures).toEqual(world.panel.picturesShown());
}, 20_000);

test('scale-only real render retains the ordered source generation, changes revision and remains usable', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad(); const before = await admit(world, socket);
  world.panel.render(pictureState(false, 2));
  expect(world.panel.picturesShown()).toEqual([{ source: A, scale: 2 }]);
  const sent = await pin(world, socket); expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  expect(row(sent.wire).sourceGeneration).toBe(row(before).sourceGeneration); expect(row(sent.wire).revision).toBeGreaterThan(row(before).revision);
}, 20_000);

test('original actual Screen Change confirms after both frames and useful same-root ACK history is retained', async () => {
  const world = make(), socket = await accept(world); await admit(world, socket);
  const current = await change(world, socket); frame();
  expect(report(world, socket).applied).not.toContain(current.change); frame();
  const accepted = await admit(world, socket); expect(accepted.applied).toContain(current.change);
  expect(row(accepted).rect?.slice(0, 2)).toEqual([40, 60]);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: current.change })).toMatchObject({ ok: true, confirmed: true });
  const history = await pin(world, socket); expect(history.reply).toMatchObject({ ok: true, accepted: true }); expect(history.wire.applied).toContain(current.change);
  // The hidden/immediate caller uses the identical causal closure, no invented ID.
  const immediateReply = await world.consumer.rpc({ op: 'change' });
  if (!immediateReply.wire || immediateReply.wire.type !== 'present') throw new Error('missing actual immediate Change');
  world.session.legacyApplied(immediateReply.wire.change, immediateReply.wire.id)();
  const immediate = await admit(world, socket); expect(immediate.applied).toContain(immediateReply.wire.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: immediateReply.wire.change })).toMatchObject({ ok: true, confirmed: true });
}, 20_000);

test('C2 disconnect between frames cannot repopulate a fresh accepted profile with an old actual Change ACK', async () => {
  const world = make(), old = await accept(world, 'old'); await admit(world, old, 'old');
  const retired = await change(world, old); frame();
  old.disconnected(); expect(await world.consumer.rpc({ op: 'gone', socket: 'old' })).toMatchObject({ ok: true });
  expect(await world.consumer.rpc({ op: 'confirm', changeId: retired.change })).toMatchObject({ ok: true, confirmed: false });
  world.connection.open(); const socket = await accept(world); await admit(world, socket); frame();
  const sent = await pin(world, socket);
  expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true }); expect(sent.wire.applied).not.toContain(retired.change);
  const fresh = await change(world, socket); frame(); frame(); const useful = await admit(world, socket);
  expect(useful.applied).toContain(fresh.change); expect(await world.consumer.rpc({ op: 'confirm', changeId: fresh.change })).toMatchObject({ ok: true, confirmed: true });
}, 20_000);

test('C2 replacement root retires a delayed actual Change while the accepted pair stays current', async () => {
  const world = make(), socket = await accept(world), first = await admit(world, socket);
  const retired = await change(world, socket); frame(); world.replace();
  const replacement = await admit(world, socket); expect(row(replacement).lifetimeGeneration).not.toBe(row(first).lifetimeGeneration);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: retired.change })).toMatchObject({ ok: true, confirmed: false });
  frame(); const sent = await pin(world, socket);
  expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true }); expect(sent.wire.applied).not.toContain(retired.change);
  const fresh = await change(world, socket); frame(); frame(); await admit(world, socket);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: fresh.change })).toMatchObject({ ok: true, confirmed: true });
}, 20_000);

test('C2 already queued and confirmed ACK retires with its root, while prior same-root history stays valid', async () => {
  const world = make(), socket = await accept(world); await admit(world, socket);
  const retired = await change(world, socket); frame(); frame(); const confirmed = await admit(world, socket);
  expect(confirmed.applied).toContain(retired.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: retired.change })).toMatchObject({ ok: true, confirmed: true });
  const sameRoot = await pin(world, socket); expect(sameRoot.reply).toMatchObject({ ok: true, accepted: true }); expect(sameRoot.wire.applied).toContain(retired.change);
  world.replace(); const sent = await pin(world, socket);
  expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true }); expect(sent.wire.applied).not.toContain(retired.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: retired.change })).toMatchObject({ ok: true, confirmed: false });
}, 20_000);

test.each(['research', 'assets', 'datasets', 'capabilities'])('visibility: %s ancestor CSS plus zero canvas geometry hides shared rows while keeping them open', async (route) => {
  const world = make(), socket = await accept(world), first = await admit(world, socket);
  expect(row(first, 'live')).toMatchObject({ open: true, connected: true, visible: true });
  body.dataset.view = route; stage.style.display = 'none'; canvas.bounds = nativeRect(0, 0);
  // External native CSS/rect facts match the actual style.css route rule. No fake route evaluator.
  expect(getComputedStyle(canvas as unknown as HTMLElement).display).toBe('block');
  const sent = await pin(world, socket); expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  for (const id of ['live', 'outlines']) {
    expect(row(sent.wire, id)).toMatchObject({ open: true, connected: true, visible: false }); expect(row(sent.wire, id).rect).toBeUndefined();
    expect(row(sent.wire, id).sourceGeneration).toBe(row(first, id).sourceGeneration);
  }
  stage.style.display = 'block'; canvas.bounds = null; body.dataset.view = 'live';
  const restored = await admit(world, socket); expect(row(restored, 'live')).toMatchObject({ open: true, visible: true });
  expect(row(restored, 'live').lifetimeGeneration).toBe(row(first, 'live').lifetimeGeneration);
}, 20_000);

test.each(['ancestor-only', 'zero-only'])('visibility: %s independently hides the actual open canvas', async (reason) => {
  const world = make(), socket = await accept(world); await admit(world, socket);
  if (reason === 'ancestor-only') stage.style.display = 'none'; else canvas.bounds = nativeRect(0, 0);
  const sent = await pin(world, socket); expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  expect(row(sent.wire, 'live')).toMatchObject({ open: true, connected: true, visible: false });
  expect(sent.wire.sources).toEqual([]); expect(sent.wire.commandResults).toEqual([]);
}, 20_000);

test('actual consumer still rejects an intentionally supplied cross-profile ACK and accepts the untouched current pin', async () => {
  const world = make(), old = await accept(world, 'old'); await admit(world, old, 'old');
  const retired = await change(world, old); old.disconnected(); await world.consumer.rpc({ op: 'gone', socket: 'old' });
  world.connection.open(); const socket = await accept(world); await admit(world, socket);
  const wire = ask(world, socket);
  // Explicit malformed external wire, not a fabricated association or production result.
  const refused = await world.consumer.rpc({ op: 'pin', wire: { ...wire, applied: [retired.change] }, requestId: 'synthetic-malformed-ack' });
  expect(refused).toMatchObject({ ok: false, code: 'INVALID_REPORT' });
  expect(wire.applied).not.toContain(retired.change);
  expect(await world.consumer.rpc({ op: 'pin', wire, requestId: 'synthetic-untouched-pin' })).toMatchObject({ ok: true, accepted: true });
}, 20_000);


// C3 minimal TEST-only close: the original fifteen cases and their platform are
// unchanged. This publication is a CANNED external server-state input matching
// Magnifier.close(); actual render/getters/Reporter and original Screen own effects.
test('C3 original loaded close confirms after publication without an intervening collection', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad();
  const before = await admit(world, socket);
  expect(world.panel.picturesShown()).toEqual([{ source: A, scale: 1 }]);
  expect(row(before)).toMatchObject({ open: true, connected: true, visible: true });

  // The production server publishes the closed MagnifierState before Screen.send.
  // No Reporter collection occurs between this actual render and the original Change.
  world.panel.render({ type: 'magnifier', space: 'picture', open: false, objectId: null, index: 0, sources: [], beside: null });
  expect(world.panel.isOpen()).toBe(false); expect(world.panel.source()).toBeNull(); expect(world.panel.picturesShown()).toEqual([]);
  const reply = await world.consumer.rpc({ op: 'change-close' });
  expect(reply, JSON.stringify(reply)).toMatchObject({ ok: true });
  if (!reply.wire || reply.wire.type !== 'present') throw new Error('actual Screen did not broadcast the original close Change');
  const close = reply.wire;
  expect(close).toMatchObject({ id: 'magnifier', kind: 'magnifier', verb: 'close', set: { open: false } });
  await socket.text(close); frame(); frame();

  const closed = await admit(world, socket);
  expect(row(closed)).toMatchObject({ open: false, connected: true, visible: false, view: { legacy: { source: null, pictures: [] } } });
  expect(row(closed).lifetimeGeneration).toBe(row(before).lifetimeGeneration);
  expect(row(closed).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(closed.sources).toEqual([]); expect(closed.commandResults).toEqual([]); expect(row(closed).load.state).toBe('unknown');
  const confirmation = await world.consumer.rpc({ op: 'confirm', changeId: close.change });
  expect(confirmation, JSON.stringify(confirmation)).toMatchObject({ ok: true });
  // Both outcomes come from original production objects, never a supplied ACK or
  // a callback success boolean. On held9803 the closed report is valid but this fails.
  expect({ applied: closed.applied, confirmed: confirmation.confirmed }).toEqual({
    applied: expect.arrayContaining([close.change]), confirmed: true,
  });
}, 20_000);


// Additive C3 schedules only: original fifteen cases, minimal close and their
// platform/helper bytes remain frozen. CANNED retained publication facts are not
// an executed ResourceIndex/open_entry/brain/App path or native image authority.
function retainedPublication(source: string, scale = 1): MagnifierState {
  return { type: 'magnifier', space: 'picture', open: true, objectId: null, index: 0, beside: null,
    sources: [{ kind: 'image', space: 'picture', label: 'Synthetic retained artifact', url: source, displayScale: scale,
      resource: { id: 'synthetic-retained-entry', day: '2026-10-08', title: 'Synthetic retained artifact' } }] };
}
async function originalEntryOpen(world: World, socket: NativeSocket, source: string) {
  const reply = await world.consumer.rpc({ op: 'change-entry-open', source });
  expect(reply, JSON.stringify(reply)).toMatchObject({ ok: true });
  if (!reply.wire || reply.wire.type !== 'present') throw new Error('actual Screen did not emit an original entry-open Change');
  expect(reply.wire).toMatchObject({ id: 'magnifier', kind: 'entry', verb: 'open',
    set: { open: true, shows: 'synthetic-retained-entry', source } });
  await socket.text(reply.wire); return reply.wire;
}

// The other close ordering updates the real Reporter stamp before the old callback.
test('C3 original loaded close survives a collection before its deferred completion', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad(); const before = await admit(world, socket);
  world.panel.render({ type: 'magnifier', space: 'picture', open: false, objectId: null, index: 0, sources: [], beside: null });
  const reply = await world.consumer.rpc({ op: 'change-close' }); expect(reply, JSON.stringify(reply)).toMatchObject({ ok: true });
  if (!reply.wire || reply.wire.type !== 'present') throw new Error('missing actual original close Change');
  const close = reply.wire; await socket.text(close); frame();
  const waiting = await admit(world, socket);
  expect(row(waiting)).toMatchObject({ open: false, connected: true, view: { legacy: { source: null, pictures: [] } } });
  expect(waiting.applied).not.toContain(close.change);
  expect(row(waiting).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(row(waiting).lifetimeGeneration).toBe(row(before).lifetimeGeneration);
  frame(); const closed = await admit(world, socket);
  const confirmation = await world.consumer.rpc({ op: 'confirm', changeId: close.change });
  expect(confirmation, JSON.stringify(confirmation)).toMatchObject({ ok: true });
  expect({ applied: closed.applied, confirmed: confirmation.confirmed }).toEqual({
    applied: expect.arrayContaining([close.change]), confirmed: true,
  });
}, 20_000);

test.each(['no-collection', 'collected-before-callback'] as const)(
  'C3 original retained entry-open uses its published source through %s ordering', async (ordering) => {
    const world = make(), socket = await accept(world); world.nodes.image.finishLoad(); const before = await admit(world, socket);
    const root = world.nodes.root;
    world.panel.render(retainedPublication(B));
    expect(world.panel.source()).toBe(B); expect(world.panel.picturesShown()).toEqual([]);
    // Actual production open_entry publishes first, then sends this original kind=entry Change.
    // In both schedules no collection occurs between the publication and Change.
    const open = await originalEntryOpen(world, socket, B); frame();
    let intermediate: WorkspaceObservation | null = null;
    if (ordering === 'collected-before-callback') {
      intermediate = await admit(world, socket);
      expect(row(intermediate).view.legacy.source).toBe(B); expect(row(intermediate).view.legacy.pictures).toEqual([]);
      expect(intermediate.applied).not.toContain(open.change);
      expect(row(intermediate).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
    }
    world.nodes.image.finishLoad(); expect(world.panel.picturesShown()).toEqual([{ source: B, scale: 1 }]);
    frame(); const loaded = await admit(world, socket);
    expect(world.nodes.root).toBe(root); expect(row(loaded).lifetimeGeneration).toBe(row(before).lifetimeGeneration);
    expect(row(loaded).sourceGeneration).toBeGreaterThan(row(intermediate ?? before).sourceGeneration);
    // Resource identity and outline subject differ: the actual image getter has no live outline here.
    expect(row(loaded).view.legacy.shows).toBeNull(); expect(row(loaded).view.legacy.source).toBe(open.set.source);
    expect(row(loaded).view.legacy.pictures).toEqual([{ source: B, scale: 1 }]);
    expect(loaded.sources).toEqual([]); expect(loaded.commandResults).toEqual([]); expect(row(loaded).load.state).toBe('unknown');
    const history = await pin(world, socket); expect(history.reply, JSON.stringify(history.reply)).toMatchObject({ ok: true, accepted: true });
    const confirmation = await world.consumer.rpc({ op: 'confirm', changeId: open.change });
    expect(confirmation, JSON.stringify(confirmation)).toMatchObject({ ok: true });
    expect({ applied: loaded.applied, history: history.wire.applied, confirmed: confirmation.confirmed }).toEqual({
      applied: expect.arrayContaining([open.change]), history: expect.arrayContaining([open.change]), confirmed: true,
    });
  }, 20_000,
);

test('C3 later image load preserves original entry-open ACK across its same-root generation advance', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad(); const before = await admit(world, socket);
  world.panel.render(retainedPublication(B)); const open = await originalEntryOpen(world, socket, B);
  frame(); frame(); const published = await admit(world, socket);
  expect(row(published)).toMatchObject({ open: true, connected: true, view: { legacy: { source: B, pictures: [] } } });
  expect(row(published).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(world.panel.picturesShown()).toEqual([]); // Desired URL alone is not loaded arrangement confirmation.
  world.nodes.image.finishLoad(); const loaded = await admit(world, socket);
  expect(row(loaded).view.legacy.pictures).toEqual([{ source: B, scale: 1 }]);
  expect(row(loaded).sourceGeneration).toBeGreaterThan(row(published).sourceGeneration);
  expect(row(loaded).lifetimeGeneration).toBe(row(before).lifetimeGeneration);
  const confirmation = await world.consumer.rpc({ op: 'confirm', changeId: open.change });
  expect(confirmation, JSON.stringify(confirmation)).toMatchObject({ ok: true });
  // Confirmation is requested only after actual loading: a timed-out original Change is terminal.
  world.panel.render(retainedPublication(B, 2)); const scaled = await pin(world, socket);
  expect(scaled.reply, JSON.stringify(scaled.reply)).toMatchObject({ ok: true, accepted: true });
  expect(row(scaled.wire).sourceGeneration).toBe(row(loaded).sourceGeneration);
  expect(row(scaled.wire).revision).toBeGreaterThan(row(loaded).revision);
  expect(row(scaled.wire).view.legacy.pictures).toEqual([{ source: B, scale: 2 }]);
  expect({ published: published.applied, loaded: loaded.applied, history: scaled.wire.applied, confirmed: confirmation.confirmed }).toEqual({
    published: expect.arrayContaining([open.change]), loaded: expect.arrayContaining([open.change]),
    history: expect.arrayContaining([open.change]), confirmed: true,
  });
}, 20_000);

test('C3 unrelated source replacement cannot complete an original move held between frames', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad(); const before = await admit(world, socket);
  const move = await change(world, socket); frame();
  world.panel.render(retainedPublication(B)); world.nodes.image.finishLoad(); frame();
  const replacement = await admit(world, socket);
  expect(row(replacement).view.legacy.source).toBe(B); expect(row(replacement).view.legacy.pictures).toEqual([{ source: B, scale: 1 }]);
  expect(row(replacement).rect?.slice(0, 2)).toEqual([40, 60]); // Geometry alone still satisfies the old move; content is unrelated.
  expect(row(replacement).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(row(replacement).lifetimeGeneration).toBe(row(before).lifetimeGeneration);
  const confirmation = await world.consumer.rpc({ op: 'confirm', changeId: move.change });
  expect(confirmation).toMatchObject({ ok: true, confirmed: false }); expect(replacement.applied).not.toContain(move.change);
  const sent = await pin(world, socket); expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  expect(sent.wire.applied).not.toContain(move.change);
}, 20_000);

test('C3 unrelated later source retires queued confirmed move history on the original same root', async () => {
  const world = make(), socket = await accept(world); world.nodes.image.finishLoad(); const before = await admit(world, socket);
  const move = await change(world, socket); frame(); frame(); const moved = await admit(world, socket);
  expect(moved.applied).toContain(move.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: move.change })).toMatchObject({ ok: true, confirmed: true });
  const same = await pin(world, socket); expect(same.reply).toMatchObject({ ok: true, accepted: true }); expect(same.wire.applied).toContain(move.change);
  world.panel.render(retainedPublication(B)); world.nodes.image.finishLoad();
  const sent = await pin(world, socket); expect(sent.reply, JSON.stringify(sent.reply)).toMatchObject({ ok: true, accepted: true });
  expect(row(sent.wire).sourceGeneration).toBeGreaterThan(row(before).sourceGeneration);
  expect(row(sent.wire).lifetimeGeneration).toBe(row(before).lifetimeGeneration); expect(sent.wire.applied).not.toContain(move.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: move.change })).toMatchObject({ ok: true, confirmed: false });
}, 20_000);


test('C3 original retained entry-open reuses a closed root and confirms its loaded source', async () => {
  const world = make(), socket = await accept(world);
  world.panel.render({ type: 'magnifier', space: 'picture', open: false, objectId: null, index: 0, sources: [], beside: null });
  const closed = await admit(world, socket), root = world.nodes.root;
  expect(row(closed)).toMatchObject({ open: false, connected: true, visible: false, view: { legacy: { source: null, pictures: [] } } });
  world.panel.render(retainedPublication(B)); const open = await originalEntryOpen(world, socket, B);
  world.nodes.image.finishLoad(); frame(); frame(); const loaded = await admit(world, socket);
  expect(world.nodes.root).toBe(root); expect(row(loaded).lifetimeGeneration).toBe(row(closed).lifetimeGeneration);
  expect(row(loaded).sourceGeneration).toBeGreaterThan(row(closed).sourceGeneration);
  expect(row(loaded)).toMatchObject({ open: true, connected: true, visible: true, view: { legacy: { source: B, pictures: [{ source: B, scale: 1 }] } } });
  const confirmation = await world.consumer.rpc({ op: 'confirm', changeId: open.change });
  expect(confirmation, JSON.stringify(confirmation)).toMatchObject({ ok: true });
  expect({ applied: loaded.applied, confirmed: confirmation.confirmed }).toEqual({
    applied: expect.arrayContaining([open.change]), confirmed: true,
  });
}, 20_000);

// Additive overlay-retirement lane. Real decoded-frame and server layer publications
// enter public View setters, exactly as installed main; external pixels are synthetic.
// No source grant, native paint receipt, App/model/camera or browser pixel proof.
const motionSample: FrameBasis = { schema: 'frame-basis-v1', frameId: 75, captureId: null, run: 'synthetic-run', boot: 'synthetic-bridge',
  seq: 8075, epoch: 7, tracked: true, imageSize: [1280, 720], disc: null, offset: [0, 0], objective: null,
  umPerPx: null, scaleId: null, scale: null };
const motionItem: MotionTracksWire = { type: 'motion-tracks', space: 'frame', basis: 'camera-pixels', sample: motionSample,
  id: 'm1', title: 'Original current-camera motion', how: 'measured', capture: 60, epoch: 7, offset: [0, 0],
  from: 102, to: 102.5, seconds: .5, interval: '102–102.5', unit: 'px/s', umPerPx: null, scale: null,
  threshold: null, count: 1, left: 0, tracks: [{ id: 't1', moving: true, net: null, path: null, straightness: null,
    segments: [{ start: 'first', end: 'open', points: [[680, 330, 102], [694, 330, 102.5]] }] }] };
const motionLayer: TracksLayerWire = { type: 'tracks-layer', space: 'frame', tracks: 'm1', style: { opacity: .5, colour: 'id' },
  verified: true, why: '', correction: [0, 0], view: null };
const populationItem: PopulationWire = { type: 'population', space: 'capture', id: 'e1', title: 'Original cell population',
  operation: 'cells', madeBy: {}, capture: 60, epoch: 7, offset: [0, 0], at: 102, revision: 0, status: 'measured', count: 1,
  coverage: null, basis: { unit: 'px', umPerPx: null, scale: null, objective: null },
  members: [{ id: 'c1', polygons: [[[100, 100], [120, 100], [120, 120], [100, 120]]], colour: 0, placement: 'verified', measure: null }] };
const populationLayer: LayerWire = { type: 'layer', space: 'frame', population: 'e1', style: { opacity: .5, colour: 'id' },
  verified: ['c1'], why: '', correction: [0, 0], view: null };
type OverlayKind = 'tracks' | 'population';
function setOverlayFrame(world: World, later = false) {
  const basis = later ? { ...motionSample, frameId: 76, seq: 8076 } : motionSample;
  const header: FrameHeader = { type: 'frame', id: basis.frameId, run: basis.run, boot: basis.boot, seq: basis.seq, frameBasis: basis,
    w: 1280, h: 720, dx: 0, dy: 0, epoch: 7, tracked: true, steady: true, speed: 0, t: 102.5, sent: 102.5, fov: null,
    ...(later ? {} : { motionTracks: motionItem }) };
  world.view.setFrame({ header, bitmap: { width: 1280, height: 720, close() {} } as unknown as ImageBitmap });
}
function publishOverlay(world: World, kind: OverlayKind, replacement = false) {
  if (kind === 'tracks') {
    world.view.setMotionTracks(replacement ? { ...motionItem, id: 'm2' } : motionItem);
    world.view.setTracksLayer(replacement ? { ...motionLayer, tracks: 'm2' } : motionLayer);
  } else {
    world.view.setPopulation(replacement ? { ...populationItem, id: 'e2' } : populationItem);
    world.view.setLayer(replacement ? { ...populationLayer, population: 'e2' } : populationLayer);
  }
}
async function hiddenPublication(socket: NativeSocket, kind: OverlayKind) {
  await socket.text(kind === 'tracks' ? { ...motionLayer, tracks: null, verified: false }
    : { ...populationLayer, population: null, verified: [] });
}
async function originalOverlayHide(world: World, socket: NativeSocket, kind: OverlayKind) {
  const reply = await world.consumer.rpc({ op: 'change-overlay-hide', kind, target: kind === 'tracks' ? 'tracks:m1' : 'layer:e1' });
  expect(reply, JSON.stringify(reply)).toMatchObject({ ok: true });
  if (!reply.wire || reply.wire.type !== 'present') throw new Error('actual Screen did not emit original overlay hide');
  await socket.text(reply.wire); return reply.wire;
}
async function activeOverlay(kind: OverlayKind) {
  const world = make(true), socket = await accept(world); setOverlayFrame(world); publishOverlay(world, kind);
  if (kind === 'tracks') expect(world.view.tracksDrawnNow()).toBe(true);
  else expect(world.view.memberAt([110, 110])?.id).toBe('c1');
  const before = await admit(world, socket), id = kind === 'tracks' ? 'tracks:m1' : 'layer:e1';
  expect(row(before, id)).toMatchObject({ connected: true, open: true, visible: true, view: { legacy: { shows: kind === 'tracks' ? 'm1' : 'e1', opacity: .5 } } });
  expect(before.sources).toEqual([]); expect(before.commandResults).toEqual([]);
  const original = await pin(world, socket); expect(original.reply).toMatchObject({ ok: true, accepted: true });
  return { world, socket, before, id };
}

test.each(['tracks', 'population'] as const)('overlay hide: %s keeps the original inactive structural root and confirms its actual Change', async (kind) => {
  const { world, socket, before, id } = await activeOverlay(kind);
  // Production publishes the hidden layer before Screen.send. No observation
  // collection occurs between this publication and the original issued Change.
  await hiddenPublication(socket, kind);
  expect(kind === 'tracks' ? world.view.tracksDrawnNow() : world.view.memberAt([110, 110]) !== null).toBe(false);
  const hide = await originalOverlayHide(world, socket, kind); frame(); frame();
  const closed = await admit(world, socket), confirmation = await world.consumer.rpc({ op: 'confirm', changeId: hide.change });
  expect(confirmation).toMatchObject({ ok: true });
  expect({ represented: closed.items.some((item) => item.id === id), applied: closed.applied.includes(hide.change), confirmed: confirmation.confirmed })
    .toEqual({ represented: true, applied: true, confirmed: true });
  expect(row(closed, id)).toMatchObject({ open: false, connected: true, visible: false,
    rootId: row(before, id).rootId, lifetimeGeneration: row(before, id).lifetimeGeneration,
    sourceGeneration: row(before, id).sourceGeneration, view: { legacy: { shows: kind === 'tracks' ? 'm1' : 'e1' } } });
  expect(closed.sources).toEqual([]); expect(closed.commandResults).toEqual([]);
  expect(closed.targets.some((target) => target.presentationId === id)).toBe(false);
  publishOverlay(world, kind); const reopened = await admit(world, socket);
  expect(row(reopened, id)).toMatchObject({ open: true, visible: true, lifetimeGeneration: row(before, id).lifetimeGeneration });
  expect(world.errors).toEqual([]);
}, 20_000);

test('overlay hide: a later unpaired camera frame stays structurally open without claiming trails drawn', async () => {
  const { world, socket, before, id } = await activeOverlay('tracks'); setOverlayFrame(world, true);
  expect(world.view.tracksDrawnNow()).toBe(false);
  const current = await admit(world, socket);
  expect(row(current, id)).toMatchObject({ open: true, visible: true, lifetimeGeneration: row(before, id).lifetimeGeneration });
  expect(row(current, id).view.legacy.placement).toContain('not drawn live');
  expect(row(current, id).view.legacy.placement).not.toContain('current-camera trails on frame 76');
  expect(current.sources).toEqual([]);
}, 20_000);

test.each(['tracks', 'population'] as const)('overlay hide: replacing %s before callback retires the old target instead of retaining stale authority', async (kind) => {
  const { world, socket, id } = await activeOverlay(kind);
  await hiddenPublication(socket, kind); const hide = await originalOverlayHide(world, socket, kind);
  publishOverlay(world, kind, true); frame(); frame(); const current = await admit(world, socket);
  expect(current.items.some((item) => item.id === id)).toBe(false); expect(current.applied).not.toContain(hide.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: hide.change })).toMatchObject({ ok: true, confirmed: false });
  expect(row(current, kind === 'tracks' ? 'tracks:m2' : 'layer:e2')).toMatchObject({ open: true, visible: true });
}, 20_000);

test('overlay hide: retired connection cannot publish its old completion into replacement accepted profile', async () => {
  const { world, socket, id } = await activeOverlay('tracks');
  await hiddenPublication(socket, 'tracks'); const hide = await originalOverlayHide(world, socket, 'tracks'); frame();
  socket.disconnected(); await world.consumer.rpc({ op: 'gone' });
  world.connection.open(); const fresh = await accept(world, 'replacement'); frame();
  const current = await admit(world, fresh, 'replacement');
  expect(current.applied).not.toContain(hide.change); expect(current.sources).toEqual([]);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: hide.change })).toMatchObject({ ok: true, confirmed: false });
  publishOverlay(world, 'tracks'); const useful = await admit(world, fresh, 'replacement');
  expect(row(useful, id)).toMatchObject({ open: true, visible: true });
}, 20_000);

test('overlay hide: foreign client report cannot certify original completion and untouched current report remains useful', async () => {
  const { world, socket } = await activeOverlay('tracks');
  await hiddenPublication(socket, 'tracks'); await originalOverlayHide(world, socket, 'tracks'); frame(); frame();
  const wire = report(world, socket);
  const foreign = await world.consumer.rpc({ op: 'report', wire: { ...wire, client: { ...wire.client, clientId: 'foreign-client' } } });
  expect(foreign).toMatchObject({ ok: false, code: 'CLIENT_MISMATCH' });
  expect(await world.consumer.rpc({ op: 'report', wire })).toMatchObject({ ok: true, accepted: true });
  expect(world.errors).toEqual([]);
}, 20_000);

test('overlay hide: detached actual canvas root cannot certify its delayed original Change', async () => {
  const { world, socket } = await activeOverlay('tracks');
  await hiddenPublication(socket, 'tracks'); const hide = await originalOverlayHide(world, socket, 'tracks');
  canvas.remove(); frame(); frame(); const detached = await admit(world, socket);
  expect(detached.applied).not.toContain(hide.change);
  expect(await world.consumer.rpc({ op: 'confirm', changeId: hide.change })).toMatchObject({ ok: true, confirmed: false });
  stage.append(canvas); publishOverlay(world, 'tracks'); const useful = await admit(world, socket);
  expect(row(useful, 'tracks:m1')).toMatchObject({ open: true, connected: true, visible: true });
}, 20_000);

// Actual App-owned session/thread publications (a new session, a switch back to one, a conversation) are
// serialized by the passive Python socket; there is no invented marker. It runs outside an event
// loop, so no independent Memory/provider work starts. Pixels remain synthetic.
async function actualLifecycle(world: World, how: 'new-session' | 'switch' | 'thread') {
  const actual = await world.consumer.rpc({ op: 'app-lifecycle', how });
  expect(actual).toMatchObject({ ok: true });
  if (!actual.before || !actual.after || !actual.messages) throw new Error('SETUP: actual App lifecycle trace is incomplete');
  return { before: actual.before, after: actual.after, messages: actual.messages };
}
async function dayOwnedOverlays() {
  const world = make(true), socket = await accept(world); setOverlayFrame(world);
  publishOverlay(world, 'tracks'); publishOverlay(world, 'population');
  expect(world.view.tracksDrawnNow()).toBe(true); expect(world.view.memberAt([110, 110])?.id).toBe('c1');
  return { world, socket };
}

test.each(['new-session', 'switch'] as const)('overlay lifecycle: actual App %s retires both inactive identities, payloads and pending original Changes', async (how) => {
  const { world, socket } = await dayOwnedOverlays(), actual = await actualLifecycle(world, how);
  await socket.text(actual.before); const before = await admit(world, socket);
  const original = await pin(world, socket); expect(original.reply).toMatchObject({ ok: true, accepted: true });
  // These external null layer publications are the production _clear_view order
  // when overlays are shown; the actual App lifecycle signal below is unmodified.
  await hiddenPublication(socket, 'tracks'); await hiddenPublication(socket, 'population');
  const trackHide = await originalOverlayHide(world, socket, 'tracks'), cellHide = await originalOverlayHide(world, socket, 'population');
  for (const message of actual.messages) await socket.text(message);
  frame(); frame(); const retired = await admit(world, socket);
  expect(retired.items.filter((item) => item.kind === 'tracks' || item.kind === 'population'), 'MISSING_FEATURE: old-day inactive surfaces remain represented')
    .toEqual([]);
  expect(retired.applied).not.toContain(trackHide.change); expect(retired.applied).not.toContain(cellHide.change);
  for (const hide of [trackHide, cellHide]) expect(await world.consumer.rpc({ op: 'confirm', changeId: hide.change })).toMatchObject({ ok: true, confirmed: false });
  expect(retired.sources).toEqual([]);
  // A newly named layer cannot read the previous day's same-number payload.
  // A fresh publication, received through the same public dispatcher, restores it.
  await socket.text(motionLayer); await socket.text(populationLayer);
  expect(world.view.tracksDrawnNow()).toBe(false); expect(world.view.memberAt([110, 110])).toBeNull();
  expect(overlaySurfaces(world.view)).toEqual([]);
  await socket.text({ ...motionItem, title: 'Fresh day motion' });
  await socket.text({ ...populationItem, title: 'Fresh day population' });
  const fresh = await admit(world, socket);
  expect(row(fresh, 'tracks:m1').view.legacy.title).toBe('Fresh day motion');
  expect(row(fresh, 'layer:e1').view.legacy.title).toBe('Fresh day population');
  expect(row(fresh, 'tracks:m1').lifetimeGeneration).not.toBe(row(before, 'tracks:m1').lifetimeGeneration);
  expect(row(fresh, 'layer:e1').lifetimeGeneration).not.toBe(row(before, 'layer:e1').lifetimeGeneration);
  expect(world.view.tracksDrawnNow()).toBe(true); expect(world.view.memberAt([110, 110])?.id).toBe('c1');
  expect(world.errors).toEqual([]);
}, 20_000);

test('overlay lifecycle: actual same-day conversation changes preserve active and ordinarily hidden producer lifetimes', async () => {
  const { world, socket } = await dayOwnedOverlays(), actual = await actualLifecycle(world, 'thread');
  expect(actual.after.id).toBe(actual.before.id); expect(actual.messages.some((message) => message.type === 'session-cleared')).toBe(false);
  await socket.text(actual.before); const before = await admit(world, socket);
  for (const message of actual.messages) await socket.text(message);
  const active = await admit(world, socket);
  for (const id of ['tracks:m1', 'layer:e1']) expect(row(active, id)).toMatchObject({ open: true, visible: true,
    lifetimeGeneration: row(before, id).lifetimeGeneration, sourceGeneration: row(before, id).sourceGeneration });
  await hiddenPublication(socket, 'tracks'); await hiddenPublication(socket, 'population');
  for (const message of actual.messages) await socket.text(message);
  const hidden = await admit(world, socket);
  for (const id of ['tracks:m1', 'layer:e1']) expect(row(hidden, id)).toMatchObject({ open: false, visible: false,
    lifetimeGeneration: row(before, id).lifetimeGeneration, sourceGeneration: row(before, id).sourceGeneration });
  publishOverlay(world, 'tracks'); publishOverlay(world, 'population'); const useful = await admit(world, socket);
  for (const id of ['tracks:m1', 'layer:e1']) expect(row(useful, id)).toMatchObject({ open: true, visible: true,
    lifetimeGeneration: row(before, id).lifetimeGeneration });
}, 20_000);

test('a wide legacy reference uses its natural aspect without changing live or comparison geometry', () => {
  const nodes = panelDOM(), panels = new PanelStack(), view = new View(canvas as unknown as HTMLCanvasElement);
  view.paused = true;
  const panel = panels.register(new MagnifierPanel(view, panels, () => {}));
  const reference = pictureState(); reference.sources[0].media = 'reference';
  panel.render(reference);
  nodes.image.complete = true; nodes.image.naturalWidth = 850; nodes.image.naturalHeight = 250; nodes.image.onload?.();
  expect(nodes.image.style.aspectRatio).toBe('850 / 250');
  expect(nodes.image.style.height).toBe('auto');
  panel.render({ ...reference, beside: { kind: 'image', space: 'picture', label: 'Other', url: B } });
  expect(nodes.image.style.aspectRatio).toBe('');
  panel.render({ ...reference, sources: [{ kind: 'held', space: 'capture', label: 'Captured view', url: A }] });
  expect(nodes.image.style.aspectRatio).toBe('');
});


test('legacy image failures offer one same-source reload and never claim rendered delivery', () => {
  const nodes = panelDOM(), panels = new PanelStack(), view = new View(canvas as unknown as HTMLCanvasElement);
  view.paused = true;
  const commands: ClientCommand[] = [];
  const panel = panels.register(new MagnifierPanel(view, panels, (command) => commands.push(command)));
  panel.render(pictureState());
  nodes.image.onerror?.();
  const status = nodes.root.querySelector('.magnifier-image-status')!;
  const reload = status.querySelector('button')!;
  expect(status.hidden).toBe(false);
  expect(status.textContent).toContain('could not load');
  expect(panel.picturesShown()).toEqual([]);
  reload.dispatchEvent(new Event('click'));
  expect(nodes.image.src).toBe(A);
  expect(status.textContent).toContain('Loading');
  nodes.image.onerror?.();
  expect(reload.hidden).toBe(true);
  expect(status.textContent).toContain('saved source is unchanged');
  panel.render(pictureState());
  expect(reload.hidden).toBe(true);
  expect(commands).toEqual([]);
  panel.render({ ...pictureState(), sources: [{ kind: 'live', space: 'frame', label: 'Live' }] });
  nodes.image.onerror?.();
  expect(status.hidden).toBe(true); // a late hidden-image failure cannot cover the live canvas
  panel.render(pictureState());
  expect(status.hidden).toBe(false); // returning to failed retained pixels restores its honest state
  panel.render({ ...pictureState(), sources: [{ ...pictureState().sources[0], url: B }] });
  nodes.image.finishLoad();
  expect(panel.picturesShown()).toEqual([{ source: B, scale: 1 }]);
  expect(nodes.root.querySelector('.magnifier-image-status')!.hidden).toBe(true);
});


test('a decoded retained image has no visible picture receipt while its stage is not laid out', () => {
  const nodes = panelDOM(), panels = new PanelStack(), view = new View(canvas as unknown as HTMLCanvasElement);
  view.paused = true;
  const panel = panels.register(new MagnifierPanel(view, panels, () => {}));
  panel.render(pictureState()); nodes.image.finishLoad();
  expect(panel.picturesShown()).toEqual([{ source: A, scale: 1 }]);
  nodes.root.bounds = nativeRect(0, 0); // Browser layout when an ancestor page hides the stage.
  expect(panel.isVisible()).toBe(false);
  expect(panel.picturesShown()).toEqual([]);
  nodes.root.bounds = null;
  expect(panel.isVisible()).toBe(true);
  expect(panel.picturesShown()).toEqual([{ source: A, scale: 1 }]);
});
