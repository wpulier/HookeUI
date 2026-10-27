// TEST-only approved WorkspaceSession boundary, not main/bootstrap/browser acceptance.
// Real existing screen namespace/Connection/Reporter/PanelStack and, once exported,
// the real Session's Runtime/Windows. Only native DOM/event/decoder/timer/HTTP facts
// are synthetic. Every server ready/command/grant below is explicitly CANNED wire;
// no accepted App, PreparedSources producer, Registry, model or real JPEG is claimed.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import * as screen from './screen';
import { Connection } from './protocol';
import type { ServerMessage, WorkspaceReady } from './protocol';
import { PanelStack } from './panels';
import type { WindowFacts } from './panels';
import type { WorkspaceWindows } from './workspace-windows';
import { syntheticPointerPlatform } from './workspace-runtime.test-support';
import { fire, syntheticDOM, TestElement, TestImage } from './workspace-windows.test-support';

// Type-only proposed API permits inspecting an absent named export in an existing
// module. It supplies no constructor, installation, observer, result or settlement.
const exported = (screen as unknown as { WorkspaceSession?: unknown }).WorkspaceSession;
const sessionTest = test.runIf(typeof exported === 'function');
type ControlFact = Omit<ReturnType<WorkspaceWindows['facts']>[number]['controls'][number], 'stateRevision'>
  & { stateRevision: number | null; visible?: boolean };
type Legacy = {
  schema: 'studio-surface-v1'; scope: 'dom-observation'; title?: string;
  shows?: string | null; source?: string | null; beside?: string | null;
  level?: number | null; pictures?: readonly { source: string; scale: number }[];
  step?: string | null; placement?: string; covers?: boolean; opacity?: number;
  style?: { colour?: string; fill?: boolean; emphasis?: { members: readonly string[]; rest: 'hide' | 'fade' } };
  workReady?: boolean;
  work?: { researcherId: string; resource: { day: string; id: string; version: number | null } };
};
type Surface = {
  id: string; root: HTMLElement;
  read(): { kind: string; open: boolean; visible: boolean; rect: screen.Rect4 | null;
    z: number | null; space: 'capture' | 'picture' | 'frame'; legacy: Legacy; contentKey: string | null;
    controls: readonly { node: HTMLElement; fact: ControlFact }[] };
};
type Biological = { kind: 'outline'; outlineId: string; revision?: number }
  | { kind: 'member'; populationId: string; memberId: string; memberRevision?: number };
type Item = {
  id: string; rootId: string; workspaceId: string; lifetimeGeneration: string;
  sourceGeneration: number; revision: number; open: boolean; connected: boolean; visible: boolean;
  rect?: number[]; sourceKey?: string; sourceToken?: string;
  propertyVersions: Record<string, number>; propertyOwners: Record<string, string | null>;
  view: { legacy?: Legacy; zoom?: number; pan?: number[] };
  load: { state: string; drawToken?: string | null }; controls: { id: string; role: string }[];
};
type Report = {
  type: 'screen'; schema: 'screen-observation-v2'; client: WorkspaceReady['client'];
  appRunId: string; seq: number; complete: boolean; items: Item[];
  sources: { key: string }[]; targets: { id: string; type: string; presentationId?: string; outlineId?: string; populationId?: string; memberId?: string }[];
  interaction: { selected: string[]; pointer: { presentationId: string | null; sourceKey: string | null; sourcePoint: unknown; hitPath: string[] };
    focus: { presentationId: string | null; controlId: string | null; role: string; editable: boolean }; lastContentFocus: unknown };
  commandResults: { commandId: string; actionId: string; requestId: string; phase: string; reportSeq: number;
    drawRef: unknown; viewDrawRef?: unknown; error?: { code: string } }[];
  omitted: { items: number }; applied: string[]; user: { id: string; prop: string }[];
};
type Options = {
  connection: Connection; reporter: screen.ScreenReporter; stage: HTMLElement; panels: PanelStack; interactionHost: HTMLElement;
  proposal: typeof proposal; legacy(): { surfaces: Surface[]; coverage: { complete: false; omittedItems: number; unknown: string[] } };
  viewport(): { layoutSize: number[]; visualRect: number[]; visualScale: number; documentScroll: number[];
    dpr: number; visible: boolean; documentFocused: boolean; activeSurface: string };
  frame(): number | undefined; selected(): { presentations: string[]; biological: Biological[] }; windowFacts(): WindowFacts | null;
  platform: { fetch: typeof fetch; createObjectURL(blob: Blob): string; revokeObjectURL(url: string): void; now(): number; sourceOrigin: string };
  onError(error: Error): void;
  onReadyChange?(ready: boolean): void;
  onReportError?(error: string): () => void;
};
type Session = { scientificTransport(): screen.ScientificTransport; connected(value: boolean): Promise<void>; message(value: ServerMessage): boolean;
  ask(text: string, fields?: { frameId?: number; objectId?: string; scientificInput?: import('./protocol').ScientificAskInput }): string;
  openResource(source: { day: string; id: string; version: number | null; picture: number | null }, title: string): void;
  openRetainedResource(saved: import('./workspace-study-restoration').StudyRetainedView,foreground?:boolean): Promise<void>;
  openResearchCapture(target: import('./protocol').ResearchCaptureTarget, parentInstance?: string): Promise<void>;
  collect(sample: { seq: number; nowMs: number }): Report | null; dispose(): void };
type Constructor = new (options: Options) => Session;
type Outgoing = { type: string; requestId?: string; screen?: Report; [key: string]: unknown };
const proposal = Object.freeze({ schema: 'workspace-origin-v2', workspaceId: 'synthetic-workspace', clientId: 'synthetic-client', pageEpoch: 'synthetic-page' } as const);
const A = '627623e8-c7d5-4805-9146-8dc6c0592f05';
const B = '60e9a614-f811-4291-80b2-604987c3bb9f';
const LIFE = '4bde9c04-84ed-4b39-ab52-a4470f93785f';
const ABC = new Uint8Array([97, 98, 99]); // SHA-256 test vector, not actual JPEG bytes
const DIGEST = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';

class NativeSocket {
  static readonly CONNECTING = 0; static readonly OPEN = 1; static readonly CLOSING = 2; static readonly CLOSED = 3;
  static readonly instances: NativeSocket[] = [];
  readonly sent: string[] = []; readonly url: string; readyState = NativeSocket.CONNECTING; binaryType = '';
  onopen: ((event: Event) => unknown) | null = null;
  onclose: ((event: Event) => unknown) | null = null;
  onmessage: ((event: MessageEvent) => unknown) | null = null;
  sendFailure: Error | null = null;
  constructor(url: string) { this.url = url; NativeSocket.instances.push(this); }
  send(bytes: string) { if (this.sendFailure) throw this.sendFailure; this.sent.push(bytes); }
  opened() { this.readyState = NativeSocket.OPEN; this.onopen?.(new Event('open')); }
  disconnected() { this.readyState = NativeSocket.CLOSED; this.onclose?.(new Event('close')); }
  close() { this.disconnected(); }
  text(value: unknown) { return this.onmessage?.({ data: JSON.stringify(value) } as MessageEvent); }
  wire(): Outgoing[] { return this.sent.map((bytes) => JSON.parse(bytes) as Outgoing); }
}
function newestSocket() { const value = NativeSocket.instances.at(-1); expect(value).toBeDefined(); return value!; }
function cannedReady(native: NativeSocket, name: string): WorkspaceReady {
  const hello = native.wire().find((value) => value.type === 'workspace-hello'); expect(hello).toBeDefined();
  return { type: 'workspace-ready', schema: 'workspace-origin-v2', ok: true, for: 'workspace-hello', requestId: hello!.requestId!,
    client: { workspaceId: proposal.workspaceId, clientId: proposal.clientId, pageEpoch: proposal.pageEpoch, connectionEpoch: `synthetic-${name}` },
    appRunId: 'synthetic-run', connectionGrant: `synthetic-grant-${name}` };
}
// External server wire only: Runtime must validate/fetch/draw it; no browser factory
// is allowed to mint these canned identities as evidence of actual core admission.
function cannedCommand(ready: WorkspaceReady, id = A, label = 'open') {
  const occurrence = `${ready.client.connectionEpoch}-${label}`;
  const requestId = `synthetic-request-${occurrence}`, actionId = `synthetic-action-${occurrence}`, commandId = `synthetic-command-${occurrence}`;
  const token = `synthetic-token-${ready.client.connectionEpoch}-${label}`;
  const source = { key: `synthetic-retained:${label}`, kind: 'saved-image', media: 'capture', availability: 'retained',
    identity: { resource: { day: '2026-10-08', id: 'synthetic-result', version: 0, picture: 0 } },
    provenance: { run: 'historical-synthetic-run', boot: 'historical-synthetic-boot', seq: 17, transforms: [] }, digest: DIGEST };
  const item = { id, workspaceId: proposal.workspaceId, sourceRef: source, sourceGeneration: 1, lifetimeGeneration: id === A ? LIFE : 'd51d61bd-3957-4815-9b7d-aefc7681b062',
    revision: 1, open: true, staged: false, title: 'Canned retained-image wire',
    geometryIntent: { position: [600, 60], size: [360, 280] }, view: { zoom: 1, pan: [0.5, 0.5] },
    propertyVersions: { geometry: 1, source: 1, open: 1, zoom: 1, pan: 1, front: 1 },
    propertyOwners: { geometry: actionId, source: actionId, open: actionId, zoom: actionId, pan: actionId, front: actionId },
    delivery: { sourceToken: token, url: `/api/presentation-sources/${token}`, payloadBinding: { servedDigest: DIGEST, originalDigest: DIGEST, transforms: [] } } };
  return { type: 'present', schema: 'presentation-command-v2', op: 'open', commandId, requestId, actionId, client: ready.client, appRunId: ready.appRunId,
    targets: [id], instances: [item], fence: { requestId, actionId, commandId, client: ready.client, appRunId: ready.appRunId,
      afterReportSeq: 0, issuedMono: 1, deadlineMono: 1_000_000,
      targets: { [id]: { lifetimeGeneration: item.lifetimeGeneration, sourceGeneration: 1, committedRevision: 1,
        basePropertyVersions: { geometry: 0, source: 0, open: 0, zoom: 0, pan: 0, front: 0 }, committedPropertyVersions: item.propertyVersions, owner: actionId,
        sourceRef: source, sourceToken: token, payloadBinding: item.delivery.payloadBinding } } } };
}
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
async function microtasks() { for (let i = 0; i < 10; i++) await Promise.resolve(); }
function nativeResponse(bytes = ABC) { return new Response(new Uint8Array(bytes).buffer, { headers: { 'Content-Type': 'image/jpeg' } }); }

let dom: ReturnType<typeof syntheticDOM>;
let pointer: ReturnType<typeof syntheticPointerPlatform>;
let now = 1000, visible = true, focused = true;
const sessions: Session[] = [];
beforeEach(() => {
  dom = syntheticDOM(); pointer = syntheticPointerPlatform(dom); now = 1000; visible = true; focused = true;
  NativeSocket.instances.length = 0; vi.useFakeTimers();
  Object.defineProperties(dom.document, { visibilityState: { get: () => visible ? 'visible' : 'hidden' },
    hidden: { get: () => !visible }, hasFocus: { value: () => focused }, baseURI: { value: 'https://synthetic.invalid/' } });
  vi.stubGlobal('document', dom.document); vi.stubGlobal('window', globalThis);
  vi.stubGlobal('HTMLElement', TestElement); vi.stubGlobal('HTMLImageElement', TestImage);
  vi.stubGlobal('WebSocket', NativeSocket); vi.stubGlobal('location', { protocol: 'https:', host: 'synthetic.invalid', origin: 'https://synthetic.invalid', href: 'https://synthetic.invalid/' });
  vi.stubGlobal('requestAnimationFrame', dom.raf);
  vi.stubGlobal('getComputedStyle', (node: TestElement) => ({ ...node.style, display: node.hidden ? 'none' : 'block' }));
});
afterEach(() => {
  for (const session of sessions.splice(0)) session.dispose();
  for (const native of NativeSocket.instances) native.onopen = native.onclose = native.onmessage = null;
  vi.clearAllTimers(); vi.useRealTimers(); vi.unstubAllGlobals();
});

function make() {
  // Actual test-owned DOM roots and displayed semantic getters, not main UI mocks.
  // Their locators/unknown work version are externally observed DOM data only.
  const magnifier = new TestElement('section'); magnifier.id = 'magnifier'; magnifier.style.width = '320px'; magnifier.style.height = '240px';
  const close = new TestElement('button'); close.id = 'magnifier-close'; magnifier.append(close);
  const viewer = new TestElement('section'); viewer.id = 'viewer'; viewer.style.left = '340px'; viewer.style.width = '240px';
  const layer = new TestElement('canvas'); layer.id = 'biological-view'; layer.style.top = '300px';
  const editor = new TestElement('input'); editor.id = 'question'; editor.style.top = '600px'; editor.value = 'PRIVATE editor content';
  dom.host.append(magnifier, viewer, layer, editor); pointer.bind(dom.host);
  const magnifierMeaning: Legacy = { schema: 'studio-surface-v1', scope: 'dom-observation', shows: 'o1', source: 'held',
    beside: '/research/synthetic-side.png', level: 2, covers: false, pictures: [{ source: 'held', scale: 2 }, { source: '/research/synthetic-side.png', scale: 1 }] };
  const viewerMeaning: Legacy = { schema: 'studio-surface-v1', scope: 'dom-observation', title: 'Observed written work', shows: 'synthetic-researcher', step: 'q1',
    work: { researcherId: 'synthetic-researcher', resource: { day: '2026-10-08', id: 'synthetic-result', version: null } } };
  const layerMeaning: Legacy = { schema: 'studio-surface-v1', scope: 'dom-observation', shows: 'e3', placement: '2 of 10 verified in place',
    opacity: 0.5, style: { colour: 'pink', fill: true, emphasis: { members: ['c12'], rest: 'fade' } } };
  const selection = { presentations: [] as string[], biological: [{ kind: 'outline', outlineId: 'o1', revision: 2 },
    { kind: 'member', populationId: 'e3', memberId: 'c12', memberRevision: 0 }] as Biological[] };
  let omittedItems = 1, frameId = 7;
  function surface(id: string, node: TestElement, meaning: Legacy, kind: string): Surface {
    return { id, root: node as unknown as HTMLElement, read: () => ({ kind, open: !node.hidden, visible: !node.hidden && visible,
      rect: screen.rectOf(node.getBoundingClientRect()), z: 10, space: kind === 'population' ? 'capture' : 'picture',
      legacy: structuredClone(meaning), contentKey: JSON.stringify([meaning.shows, meaning.source, meaning.beside, meaning.work, meaning.step]),
      controls: id === 'magnifier' ? [{ node: close as unknown as HTMLElement, fact: { id: close.id, targetId: id,
        role: 'button', present: node.contains(close), visible: !close.hidden && !node.hidden, enabled: !close.disabled, editable: false,
        stateRevision: null, observedValue: null } satisfies ControlFact }] : [] }) };
  }
  const surfaces = [surface('magnifier', magnifier, magnifierMeaning, 'magnifier'), surface('viewer', viewer, viewerMeaning, 'viewer'), surface('layer:e3', layer, layerMeaning, 'population')];
  const legacy = () => ({ surfaces, coverage: { complete: false as const, omittedItems, unknown: ['unenumerated-routes'] } });
  const errors: Error[] = [], unhandled: ServerMessage[] = [], openings: Promise<void>[] = [], readiness: boolean[] = [];
  const hint = new TestElement('p'); dom.host.append(hint);
  const blobs: { url: string; blob: Blob }[] = [], revoked: string[] = [];
  let fetchReply: (input: RequestInfo | URL, init?: RequestInit) => Promise<Response> = async () => nativeResponse();
  const fetchNative = vi.fn<typeof fetch>((input, init) => fetchReply(input, init));
  let session!: Session;
  const connection = new Connection(() => {}, (value) => {
    if (!session.message(value)) {
      unhandled.push(value);
      // Exactly the shared DOM assignment installed in main's ordinary result
      // error path; capitalization is cosmetic and outside this owner test.
      if (value.type === 'result' && !value.ok && value.error) screen.renderHint(hint as unknown as HTMLElement, String(value.error), true);
    }
  }, (connected) => {
    const opening = session.connected(connected); openings.push(opening); void opening.catch((error: Error) => errors.push(error));
  });
  const reporter = new screen.ScreenReporter((value) => connection.send(value), () => surfaces.map((entry) => {
    const read = entry.read(); return { id: entry.id, kind: read.kind, open: read.open, visible: read.visible, rect: read.rect, ...read.legacy };
  }), 0, 10_000);
  const panels = new PanelStack();
  const options: Options = { connection, reporter, stage: dom.host as unknown as HTMLElement, panels, interactionHost: dom.host as unknown as HTMLElement,
    proposal, legacy, frame: () => frameId, selected: () => structuredClone(selection),
    windowFacts: () => ({ canvas: dom.host.getBoundingClientRect(), area: { width: 1200, height: 800 }, disc: null, object: null, labels: [] }),
    viewport: () => ({ layoutSize: [1200, 800], visualRect: [0, 0, 1200, 800], visualScale: 1, documentScroll: [0, 0], dpr: 2, visible, documentFocused: focused, activeSurface: 'workspace' }),
    platform: { fetch: fetchNative, createObjectURL: (blob) => { const url = `blob:https://synthetic.invalid/${blobs.length + 1}`; blobs.push({ url, blob }); return url; },
      revokeObjectURL: (url) => revoked.push(url), now: () => now, sourceOrigin: 'https://synthetic.invalid' }, onError: (error) => errors.push(error),
    onReadyChange: (ready) => readiness.push(ready),
    onReportError: (error) => screen.renderHint(hint as unknown as HTMLElement, error, true) as unknown as (() => void) };
  // ONLY instantiate the real named export. No fallback/install implementation.
  session = new (exported as Constructor)(options); sessions.push(session);
  connection.open();
  return { session, connection, reporter, panels, errors, unhandled, openings, readiness, blobs, revoked, fetchNative, selection, surfaces, hint,
    magnifier, close, viewer, editor, magnifierMeaning, viewerMeaning,
    setFetch: (reply: typeof fetchReply) => { fetchReply = reply; }, setOmitted: (count: number) => { omittedItems = count; }, setFrame: (id: number) => { frameId = id; } };
}
type World = ReturnType<typeof make>;
async function accept(world: World, name = 'first') {
  const native = newestSocket(); native.opened(); const ready = cannedReady(native, name);
  expect(native.wire().filter((value) => value.type === 'workspace-hello')).toHaveLength(1);
  await native.text(ready); await world.openings.at(-1); await microtasks(); return { native, ready };
}
function lastReport(native: NativeSocket) { const value = native.wire().filter((message) => message.type === 'screen').at(-1); expect(value).toBeDefined(); return value as unknown as Report; }
function askReport(native: NativeSocket) { const value = native.wire().filter((message) => message.type === 'ask').at(-1); expect(value?.screen).toBeDefined(); return value!.screen!; }
function report(world: World, native: NativeSocket) { now++; world.reporter.askNow(); world.reporter.tick(now); return lastReport(native); }
function item(value: Report, id: string) { const result = value.items.find((row) => row.id === id); expect(result).toBeDefined(); return result!; }
function root(id = A) { const value = dom.host.querySelector(`[data-presentation-id="${id}"]`); expect(value).not.toBeNull(); return value!; }
async function openedImage(native: NativeSocket, ready: WorkspaceReady, id = A, label = 'open') {
  const command = cannedCommand(ready, id, label); await native.text(command);
  await vi.waitFor(() => expect(root(id).querySelector('img')).toBeInstanceOf(TestImage));
  return { command, image: root(id).querySelector('img') as TestImage };
}
async function paint(image: TestImage) { image.finishDecode(148, 176); await microtasks(); dom.frame(); await microtasks(); }

function canonicalClose(original: ReturnType<typeof cannedCommand>, afterReportSeq: number) {
  const message = structuredClone(original), id = message.targets[0];
  message.op = 'close'; message.commandId += '-close'; message.requestId += '-close'; message.actionId += '-close';
  const row = message.instances[0]; row.open = false; row.revision++; row.propertyVersions.open++;
  row.propertyOwners.open = message.actionId;
  Object.assign(message.fence, { commandId: message.commandId, requestId: message.requestId,
    actionId: message.actionId, afterReportSeq });
  Object.assign(message.fence.targets[id], { owner: message.actionId, committedRevision: row.revision,
    basePropertyVersions: { open: original.instances[0].propertyVersions.open },
    committedPropertyVersions: { open: row.propertyVersions.open } });
  return message;
}
function sourceReportRefusal(value: Report, ready: WorkspaceReady, sourceKey: string) {
  // CANNED server error wire; the actual App/Core/COMMANDS producer is covered
  // separately in test_closed_presentation_source_metadata.py, not fabricated here.
  return { type: 'result', for: 'screen', requestId: null, ok: false, code: 'SOURCE_UNRESOLVED',
    error: 'SOURCE_UNRESOLVED: no exact prepared source grant for this connection',
    screenReport: { seq: value.seq, client: structuredClone(ready.client), appRunId: ready.appRunId, sourceKey } };
}

// This positive control runs even while Session is absent, validating actual existing
// public transport/reporter seams with these external platforms, not a future installer.
test('existing Connection and unbound Reporter retain ordinary request/report behavior', async () => {
  const connection = new Connection(() => {}, () => {}, () => {}); connection.open(); const native = newestSocket(); native.opened();
  const request = connection.request('scores', {}), sent = native.wire().at(-1)!;
  const result = { type: 'result', for: 'scores', requestId: sent.requestId, ok: true, scores: { votes: 1, kinds: [] } };
  await native.text(result); await expect(request).resolves.toEqual(result);
  const reporter = new screen.ScreenReporter((value) => connection.send(value), () => [{ id: 'live', kind: 'live', open: true }], 0);
  reporter.tick(now); reporter.askNow(); reporter.tick(++now);
  expect(native.wire().filter((value) => value.type === 'screen').map((value) => value.seq)).toEqual([1, 2]);
});

test('MISSING_CAPABILITY: existing screen namespace exports the real WorkspaceSession', () => {
  expect(typeof exported, 'MISSING_CAPABILITY: actual screen.WorkspaceSession public wiring boundary').toBe('function');
});

describe('real WorkspaceSession over labelled external platform and canned server wire', () => {
  sessionTest('capture control preserves exact finding, parent and Send owner until its own acknowledgement', async () => {
    const world = make();
    const target = {day:'2026-10-07',rid:'r-original',findingVersion:3,findingDigest:'saved-digest',sequence:1,picture:4};
    await expect(world.session.openResearchCapture(target, A)).rejects.toThrow('not accepted');
    const {native,ready} = await accept(world);
    const pending = world.session.openResearchCapture(target, A);
    target.picture = 9;
    const command = native.wire().at(-1)!;
    expect(command).toMatchObject({type:'research-open-capture',day:target.day,rid:target.rid,
      findingVersion:3,findingDigest:'saved-digest',sequence:1,picture:4,parentInstance:A,
      client:ready.client,appRunId:ready.appRunId,screen:{client:ready.client}});
    let completed = false; void pending.then(()=>{completed=true;}); await microtasks(); expect(completed).toBe(false);
    await native.text({type:'result',for:'research-open-capture',requestId:command.requestId,ok:true,pending:false});
    await pending; expect(completed).toBe(true);
    const failed = world.session.openResearchCapture(target, A);
    const rejected = expect(failed).rejects.toThrow('Original frame changed');
    await native.text({type:'result',for:'research-open-capture',requestId:native.wire().at(-1)!.requestId,
      ok:false,error:'Original frame changed'});
    await rejected;
  });
  sessionTest('capture clicks use globally distinct identities across accepted browser sessions', async () => {
    const target = {day:'2026-10-07',rid:'r-original',findingVersion:3,findingDigest:'saved-digest',sequence:1,picture:4};
    const first = make(), desktop = await accept(first, 'desktop');
    const second = make(), phone = await accept(second, 'phone');
    const firstPending = first.session.openResearchCapture(target, A);
    const secondPending = second.session.openResearchCapture(target, A);
    const firstCommand = desktop.native.wire().at(-1)!, secondCommand = phone.native.wire().at(-1)!;
    let secondCompleted = false; void secondPending.then(() => { secondCompleted = true; });
    await desktop.native.text({type:'result',for:'research-open-capture',requestId:firstCommand.requestId,ok:true,pending:false});
    await firstPending; await microtasks(); expect(secondCompleted).toBe(false);
    await phone.native.text({type:'result',for:'research-open-capture',requestId:secondCommand.requestId,ok:true,pending:false});
    await secondPending;
    expect(firstCommand.client).toEqual(desktop.ready.client);
    expect(secondCommand.client).toEqual(phone.ready.client);
    expect(firstCommand.requestId).not.toBe(secondCommand.requestId);
    expect(firstCommand.actionId).not.toBe(secondCommand.actionId);
    for (const command of [firstCommand, secondCommand]) {
      expect(command.requestId).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
      expect(command.screen).toMatchObject({client:command.client});
    }
  });
  sessionTest('retained resource Open pins its exact day/revision and requesting browser at Send', async () => {
    const world = make(), source = { day: '2026-10-07', id: 'd6', version: null, picture: 0 };
    expect(() => world.session.openResource(source, 'Earlier photo')).toThrow();
    const { native, ready } = await accept(world);
    world.session.openResource({ ...source, title: 'Card label', kind: 'Reference photo' } as typeof source, 'Earlier photo');
    const command = native.wire().at(-1)!;
    expect(command).toMatchObject({ type: 'present', do: 'open', source, title: 'Earlier photo',
      client: ready.client, appRunId: ready.appRunId, screen: { client: ready.client } });
    expect(command.source).toEqual(source); // display metadata must not enter the strict native address
    expect(command.requestId).toBeTruthy(); expect(command.actionId).toBeTruthy();
    native.disconnected();
    expect(() => world.session.openResource(source, 'Earlier photo')).toThrow();
  });
  sessionTest('unavailable before matching ready sends no report/ask/fetch and creates no native root or loop', async () => {
    const world = make(), native = newestSocket();
    world.reporter.tick(now); expect(world.session.collect({ seq: 1, nowMs: now })).toBeNull();
    expect(() => world.session.ask('before open')).toThrow(); expect(native.sent).toEqual([]);
    native.opened(); const ready = cannedReady(native, 'first');
    await native.text({ ...ready, requestId: 'unrelated' }); await microtasks();
    world.reporter.tick(++now); expect(world.session.collect({ seq: 1, nowMs: now })).toBeNull();
    expect(() => world.session.ask('still opening')).toThrow(); expect(world.fetchNative).not.toHaveBeenCalled(); expect(dom.frames).toHaveLength(0);
    expect(world.readiness).not.toContain(true);
    await native.text(ready); await world.openings.at(-1); const accepted = report(world, native);
    expect(accepted).toMatchObject({ client: ready.client, appRunId: ready.appRunId, complete: false });
    expect(accepted.items.map((row) => row.id)).toEqual(expect.arrayContaining(['magnifier', 'viewer', 'layer:e3']));
    expect(accepted.sources).toEqual([]); expect(accepted.commandResults).toEqual([]); expect(dom.frames).toHaveLength(0);
    expect(world.readiness.at(-1)).toBe(true);
    native.disconnected(); expect(world.readiness.at(-1)).toBe(false);
  });
  sessionTest('one Reporter sequence samples fresh inline Send without a tick and keeps the original explicit frame/outline', async () => {
    const world = make(), { native } = await accept(world); const first = report(world, native);
    world.magnifierMeaning.level = 4; world.viewerMeaning.step = 'q2'; world.setFrame(8);
    const wireBefore = native.sent.length; const requestId = world.session.ask('fresh typed question', { objectId: 'o1', frameId: 7 });
    expect(native.sent).toHaveLength(wireBefore + 1); const ask = native.wire().at(-1)!; const pin = askReport(native), frozen = JSON.stringify(pin);
    expect(requestId).toEqual(expect.any(String));
    expect(ask).toMatchObject({ type: 'ask', requestId, text: 'fresh typed question', objectId: 'o1', frameId: 7 });
    expect(pin.seq).toBe(first.seq + 1); expect(item(pin, 'magnifier').view.legacy?.level).toBe(4); expect(item(pin, 'viewer').view.legacy?.step).toBe('q2');
    const selected = pin.targets.filter((target) => pin.interaction.selected.includes(target.id));
    expect(selected).toEqual(expect.arrayContaining([expect.objectContaining({ outlineId: 'o1' }), expect.objectContaining({ populationId: 'e3', memberId: 'c12' })]));
    expect(pin.interaction.selected).not.toContain(`target:${A}`); expect(pin.complete).toBe(false);
    world.viewerMeaning.step = 'q3'; const later = report(world, native);
    expect(later.seq).toBe(pin.seq + 1); expect(JSON.stringify(pin)).toBe(frozen);
    const nextRequestId = world.session.ask('use current frame');
    expect(nextRequestId).not.toBe(requestId);
    expect(native.wire().at(-1)).toMatchObject({ frameId: 8, requestId: nextRequestId });
  });
  sessionTest('saved scientific Ask captures an immutable input and omits the current camera frame', async () => {
    const world = make(), { native } = await accept(world); world.setFrame(8);
    const input: import('./protocol').ScientificAskInput = {reference:{kind:'array',session:'p1',id:'native1',revision:2,component:'array.image'},digest:'d'.repeat(64),tab:'main'};
    world.session.ask('this saved input', {scientificInput:input});
    input.reference.revision=3;
    const sent=native.wire().at(-1)!;
    expect(sent.scientificInput).toMatchObject({reference:{revision:2}});
    expect(sent).not.toHaveProperty('frameId'); expect(sent).not.toHaveProperty('objectId');
    expect(()=>world.session.ask('ambiguous', {scientificInput:input,frameId:8})).toThrow('cannot be combined');
  });
  sessionTest('failed native serialization does not consume the sample sequence or queued actual user change', async () => {
    const world = make(), { native } = await accept(world), first = report(world, native);
    world.magnifier.style.left = '20px'; world.reporter.userChanged('magnifier', 'rect');
    native.sendFailure = new Error('synthetic native send failure');
    expect(() => world.session.ask('fails to serialize')).toThrow('synthetic native send failure');
    expect(native.wire().filter((value) => value.type === 'ask')).toHaveLength(0);
    native.sendFailure = null; world.session.ask('retry same current state'); const pin = askReport(native);
    expect(pin.seq).toBe(first.seq + 1); expect(pin.user).toEqual([{ id: 'magnifier', prop: 'rect' }]);
    const after = report(world, native); expect(after.seq).toBe(pin.seq + 1); expect(after.user).toEqual([]);
  });
  sessionTest('a user change made before its item was ever reported is left out, not sent to be refused', async () => {
    const world = make(), { native } = await accept(world); report(world, native);
    world.reporter.userChanged('never-reported', 'open');
    world.magnifier.style.left = '40px'; world.reporter.userChanged('magnifier', 'rect');
    expect(report(world, native).user).toEqual([{ id: 'magnifier', prop: 'rect' }]);
    expect(report(world, native).user).toEqual([]);
  });
  sessionTest('native not-open before its close callback concretely refuses Send rather than silently dropping it', async () => {
    const world = make(), { native } = await accept(world), first = report(world, native);
    native.readyState = NativeSocket.CLOSING; expect(() => world.session.ask('transport is closing')).toThrow();
    expect(native.wire().filter((value) => value.type === 'ask')).toHaveLength(0);
    native.disconnected(); world.connection.open(); const current = await accept(world, 'replacement');
    expect(report(world, current.native).seq).toBe(first.seq + 1);
  });
  sessionTest('cold viewer loading preserves the owned ACK but selecting other work invalidates it', async () => {
    const world = make(), { native } = await accept(world);
    world.viewerMeaning.workReady = false;
    report(world, native);
    const complete = world.reporter.legacyApplied('cold-viewer', 'viewer', () => true, () => world.surfaces, {
      type: 'present', change: 'cold-viewer', id: 'viewer', kind: 'entry', verb: 'open',
      set: { open: true, shows: 'synthetic-researcher' },
    });
    complete();
    const loading = report(world, native);
    expect(loading.applied).toContain('cold-viewer');
    expect(item(loading, 'viewer').view.legacy?.workReady).toBe(false);
    world.viewerMeaning.workReady = true;
    const loaded = report(world, native);
    expect(loaded.applied).toContain('cold-viewer');
    expect(item(loaded, 'viewer').view.legacy?.workReady).toBe(true);
    world.viewerMeaning.work!.resource.version = 2;
    expect(report(world, native).applied).not.toContain('cold-viewer');
  });
  sessionTest('legacy phone focus requires the exact live original occurrence and is consumed once', async () => {
    const world = make(), {native} = await accept(world); report(world,native);
    const wire = {type:'present' as const,change:'focus-entry',id:'viewer',kind:'entry',verb:'open',set:{open:true,shows:'synthetic-researcher'}};
    expect(world.reporter.claimLegacyForeground(wire)).toBe(false);
    world.reporter.legacyApplied(wire.change,wire.id,()=>true,()=>world.surfaces,wire);
    expect(world.reporter.claimLegacyForeground({...wire,set:{...wire.set,shows:'another'}})).toBe(false);
    expect(world.reporter.claimLegacyForeground(wire)).toBe(true);
    expect(world.reporter.claimLegacyForeground(wire)).toBe(false);
    // Selecting another version invalidates even an unconsumed old occurrence.
    const next = {...wire,change:'focus-stale'};
    world.reporter.legacyApplied(next.change,next.id,()=>true,()=>world.surfaces,next);
    world.viewerMeaning.work!.resource.version = 2;
    expect(world.reporter.claimLegacyForeground(next)).toBe(false);
    const rejected = {...wire,change:'focus-rejected'};
    world.reporter.legacyApplied(rejected.change,rejected.id,()=>false,()=>world.surfaces,rejected);
    expect(world.reporter.claimLegacyForeground(rejected)).toBe(false);
  });
  sessionTest('real structural content/root transitions preserve unknown displayed work and cannot acquire native authority', async () => {
    const world = make(), { native } = await accept(world), first = report(world, native), original = item(first, 'magnifier');
    expect(item(first, 'viewer').view.legacy?.work?.resource.version).toBeNull();
    for (const row of first.items) {
      expect(row.propertyVersions).toEqual({}); expect(row.propertyOwners).toEqual({}); expect(row.load.state).toBe('unknown');
      expect(row).not.toHaveProperty('sourceKey'); expect(row).not.toHaveProperty('sourceToken'); expect(row).not.toHaveProperty('viewDrawRef');
    }
    expect(original.view.legacy).toMatchObject({ shows: 'o1', source: 'held', beside: '/research/synthetic-side.png', level: 2 });
    expect(original.view.legacy?.pictures).toEqual([{ source: 'held', scale: 2 }, { source: '/research/synthetic-side.png', scale: 1 }]);
    expect(first.omitted.items).toBe(1);
    world.magnifierMeaning.shows = 'o2'; world.magnifierMeaning.source = '/research/synthetic-new.png'; const second = report(world, native), changed = item(second, 'magnifier');
    expect(changed.sourceGeneration).toBeGreaterThan(original.sourceGeneration); expect(changed.revision).toBeGreaterThan(original.revision);
    expect(changed.lifetimeGeneration).toBe(original.lifetimeGeneration); expect(original.view.legacy?.shows).toBe('o1');
    const replacement = new TestElement('section'); replacement.id = 'magnifier-replacement'; dom.host.append(replacement);
    world.magnifier.remove(); const meaning = structuredClone(world.magnifierMeaning);
    world.surfaces[0] = { id: 'magnifier', root: replacement as unknown as HTMLElement,
      read: () => ({ kind: 'magnifier', open: !replacement.hidden, visible: !replacement.hidden && visible,
        rect: screen.rectOf(replacement.getBoundingClientRect()), z: 10, space: 'picture', legacy: meaning, contentKey: JSON.stringify(meaning), controls: [] }) };
    const third = report(world, native); expect(item(third, 'magnifier').rootId).toBe(replacement.id);
    expect(item(third, 'magnifier').lifetimeGeneration).not.toBe(original.lifetimeGeneration);
    world.viewer.hidden = true; const closed = report(world, native);
    expect(item(closed, 'viewer')).toMatchObject({ open: false, view: { legacy: { work: { resource: { version: null } } } } });
    expect(closed.sources).toEqual([]); expect(closed.commandResults).toEqual([]); expect(closed.complete).toBe(false);
    world.setOmitted(0); expect(report(world, native).complete).toBe(false); // unknown routes still exist
  });
  sessionTest('registered legacy control and editor focus use the installed interaction owner without editor contents', async () => {
    const world = make(), { native } = await accept(world);
    const box = world.close.getBoundingClientRect(), point = [box.left + 1, box.top + 1];
    pointer.pointer(world.close, 'pointermove', point); pointer.focus(world.close); world.session.ask('about this control'); const control = askReport(native);
    expect(control.interaction.focus).toMatchObject({ presentationId: 'magnifier', controlId: world.close.id, role: 'button', editable: false });
    expect(control.interaction.pointer).toMatchObject({ presentationId: 'magnifier', sourceKey: null, sourcePoint: null });
    expect(control.interaction.pointer.hitPath).toContain(world.close.id);
    pointer.focus(world.editor); world.session.ask('editor focus'); const editor = askReport(native);
    expect(editor.interaction.focus).toMatchObject({ role: 'textbox', editable: true }); expect(editor.interaction.lastContentFocus).toMatchObject({ presentationId: 'magnifier' });
    expect(JSON.stringify(editor)).not.toContain('PRIVATE'); expect(editor.interaction.focus).not.toHaveProperty('value');
  });
  sessionTest('canonical routing uses captured grant and actual native bytes/decode/frame before a drawn causal receipt', async () => {
    const world = make(), { native, ready } = await accept(world); report(world, native);
    const { command, image } = await openedImage(native, ready); const loading = report(world, native);
    expect(world.unhandled).not.toContainEqual(command); expect(world.fetchNative).toHaveBeenCalledTimes(1);
    const [url, init] = world.fetchNative.mock.calls[0]; expect(url).toBe(command.instances[0].delivery.url);
    expect(new Headers(init?.headers).get('X-Micromind-Workspace-Grant')).toBe(ready.connectionGrant);
    expect(init).toMatchObject({ credentials: 'same-origin', redirect: 'error', signal: expect.any(AbortSignal) });
    expect(Array.from(new Uint8Array(await world.blobs[0].blob.arrayBuffer()))).toEqual([97, 98, 99]);
    expect(item(loading, A).load.state).toBe('loading'); expect(loading.commandResults.find((row) => row.commandId === command.commandId)?.drawRef).toBeNull();
    image.finishDecode(148, 176); await microtasks(); const decoded = report(world, native);
    expect(item(decoded, A).load.state).toBe('loaded'); expect(decoded.commandResults.find((row) => row.commandId === command.commandId)?.drawRef).toBeNull();
    dom.frame(); const drawn = report(world, native), receipt = drawn.commandResults.find((row) => row.commandId === command.commandId)!;
    expect(receipt).toMatchObject({ requestId: command.requestId, actionId: command.actionId, phase: 'drawn', reportSeq: drawn.seq });
    expect(receipt.drawRef).toBe(item(drawn, A).load.drawToken); expect(receipt.drawRef).toEqual(expect.any(String));
    expect(drawn.applied).not.toContain(command.commandId); expect(drawn.complete).toBe(false); expect(world.errors).toEqual([]);
  });
  sessionTest('a refused pre-close source report cannot leave an orphan hint after the real closed report is sent', async () => {
    const world = make(), { native, ready } = await accept(world), opened = await openedImage(native, ready);
    await paint(opened.image); const old = report(world, native);
    expect(item(old, A)).toMatchObject({ open: true, connected: true, load: { state: 'drawn' } });
    const refusal = sourceReportRefusal(old, ready, opened.command.instances[0].sourceRef.key);
    await native.text(canonicalClose(opened.command, old.seq)); await microtasks();
    const closed = report(world, native);
    expect(closed.seq).toBeGreaterThan(old.seq);
    expect(item(closed, A)).toMatchObject({ open: false, connected: false, visible: false, load: { state: 'empty' } });
    expect(closed.commandResults).toContainEqual(expect.objectContaining({ phase: 'closed', absent: true }));
    await native.text(refusal);
    // This is the installed main boundary: Session.message returning false
    // passes this exact error to main's global showHint result handler.
    expect(world.unhandled).not.toContainEqual(refusal);
    expect(world.hint.textContent ?? '').toBe('');
    expect(world.errors).toEqual([]);
    const current = sourceReportRefusal(closed, ready, opened.command.instances[0].sourceRef.key);
    await native.text(current); expect(world.hint.textContent).toBe(current.error);
  });
  sessionTest.each([null, 'The camera is unavailable', 'same-text'])('an already-visible pre-close report error retires only its own hint (newer unrelated hint=%s)', async (unrelated) => {
    const world = make(), { native, ready } = await accept(world), opened = await openedImage(native, ready);
    await paint(opened.image); const old = report(world, native);
    const refusal = sourceReportRefusal(old, ready, opened.command.instances[0].sourceRef.key);
    await native.text(refusal);
    expect(world.hint.textContent).toBe(refusal.error); expect(world.hint.dataset.tone).toBe('error');
    const replacement = unrelated === 'same-text' ? refusal.error : unrelated;
    if (replacement) screen.renderHint(world.hint as unknown as HTMLElement, replacement, true);
    await native.text(canonicalClose(opened.command, old.seq)); await microtasks();
    expect(world.hint.textContent).toBe(replacement ?? refusal.error); // an unsent local collect is no retirement evidence
    const latest = report(world, native); expect(item(latest, A).open).toBe(false);
    expect(world.hint.textContent ?? '').toBe(replacement ?? '');
    expect(world.hint.dataset.tone).toBe(unrelated ? 'error' : '');
  });
  sessionTest('a surviving actual sibling keeps a refused shared source report visible', async () => {
    const world = make(), { native, ready } = await accept(world), opened = await openedImage(native, ready);
    await paint(opened.image);
    const sibling = cannedCommand(ready, B, 'sibling');
    sibling.instances[0].sourceRef = structuredClone(opened.command.instances[0].sourceRef);
    sibling.instances[0].delivery = structuredClone(opened.command.instances[0].delivery);
    Object.assign(sibling.fence.targets[B], { sourceRef: sibling.instances[0].sourceRef,
      sourceToken: sibling.instances[0].delivery.sourceToken, payloadBinding: sibling.instances[0].delivery.payloadBinding });
    await native.text(sibling); await vi.waitFor(() => expect(root(B).querySelector('img')).toBeInstanceOf(TestImage));
    await paint(root(B).querySelector('img') as TestImage); const old = report(world, native);
    await native.text(canonicalClose(opened.command, old.seq)); await microtasks();
    const latest = report(world, native);
    expect(item(latest, A).open).toBe(false); expect(item(latest, B).load.state).toBe('drawn');
    const refusal = sourceReportRefusal(old, ready, opened.command.instances[0].sourceRef.key);
    await native.text(refusal); expect(world.hint.textContent).toBe(refusal.error);
  });
  sessionTest.each(['missing', 'foreign-origin', 'foreign-app-run', 'unknown-source', 'future-sequence', 'other-code', 'ask-result', 'tool-result'])
  ('a closed consumer cannot silence a %s report error', async (changed) => {
    const world = make(), { native, ready } = await accept(world), opened = await openedImage(native, ready);
    await paint(opened.image); const old = report(world, native);
    await native.text(canonicalClose(opened.command, old.seq)); await microtasks(); const latest = report(world, native);
    expect(item(latest, A).open).toBe(false);
    const refusal = sourceReportRefusal(old, ready, opened.command.instances[0].sourceRef.key);
    if (changed === 'missing') Reflect.deleteProperty(refusal, 'screenReport');
    if (changed === 'foreign-origin') refusal.screenReport.client.connectionEpoch = 'other-accepted-incarnation';
    if (changed === 'foreign-app-run') refusal.screenReport.appRunId = 'other-app-run';
    if (changed === 'unknown-source') refusal.screenReport.sourceKey = 'unknown-original-source';
    if (changed === 'future-sequence') refusal.screenReport.seq = latest.seq + 1;
    if (changed === 'other-code') refusal.code = 'INVALID_REPORT';
    if (changed === 'ask-result') refusal.for = 'ask';
    if (changed === 'tool-result') refusal.for = 'tool';
    await native.text(refusal); expect(world.unhandled).toContainEqual(refusal);
    expect(world.hint.textContent).toBe(refusal.error); expect(world.hint.dataset.tone).toBe('error');
  });
  sessionTest('a failed closed-report send cannot retire the already-visible pre-close error', async () => {
    const world = make(), { native, ready } = await accept(world), opened = await openedImage(native, ready);
    await paint(opened.image); const old = report(world, native);
    const refusal = sourceReportRefusal(old, ready, opened.command.instances[0].sourceRef.key);
    await native.text(refusal); expect(world.hint.textContent).toBe(refusal.error);
    await native.text(canonicalClose(opened.command, old.seq)); await microtasks();
    native.sendFailure = new Error('synthetic native send failure');
    expect(() => report(world, native)).toThrow('synthetic native send failure');
    expect(lastReport(native).seq).toBe(old.seq); expect(world.hint.textContent).toBe(refusal.error);
    native.sendFailure = null; const latest = report(world, native);
    expect(latest.seq).toBe(old.seq + 1); expect(item(latest, A).open).toBe(false);
    expect(world.hint.textContent ?? '').toBe('');
  });
  sessionTest('wrong command origin refuses before another fetch/mutation and preserves an existing drawn source', async () => {
    const world = make(), { native, ready } = await accept(world), good = await openedImage(native, ready); await paint(good.image);
    const message = cannedCommand(ready, B, 'bad-origin'), requests = world.fetchNative.mock.calls.length;
    message.client = { ...ready.client, connectionEpoch: 'synthetic-wrong-origin' }; await native.text(message);
    await vi.waitFor(() => expect(world.errors).toHaveLength(1)); expect(world.fetchNative).toHaveBeenCalledTimes(requests);
    expect(dom.host.querySelector(`[data-presentation-id="${B}"]`)).toBeNull();
    const value = report(world, native); expect(item(value, A).load.state).toBe('drawn');
    expect(value.commandResults.some((row) => row.commandId === message.commandId)).toBe(false);
  });
  sessionTest('changed native bytes retain a delivery error without losing an existing useful source', async () => {
    const world = make(), { native, ready } = await accept(world), good = await openedImage(native, ready); await paint(good.image);
    world.setFetch(async () => nativeResponse(new Uint8Array([97, 98, 100])));
    const message = cannedCommand(ready, B, 'bad-bytes'); await native.text(message); await vi.waitFor(() => expect(world.errors).toHaveLength(1));
    const value = report(world, native); expect(world.blobs).toHaveLength(1); expect(dom.images).toHaveLength(1);
    expect(dom.host.querySelector(`[data-presentation-id="${B}"]`)).toBeNull(); expect(item(value, A).load.state).toBe('drawn');
    expect(value.commandResults).toEqual(expect.arrayContaining([
      expect.objectContaining({ commandId: good.command.commandId, phase: 'drawn' }),
      expect.objectContaining({ commandId: message.commandId, phase: 'error', drawRef: null, error: expect.objectContaining({ code: 'source_mismatch' }) }),
    ]));
    world.session.ask('the retained good source'); expect(item(askReport(native), A).load.state).toBe('drawn');
  });
  sessionTest('real native zoom/close controls route one original manual proposal and keep the authoritative base unchanged', async () => {
    const world = make(), { native, ready } = await accept(world), { command, image } = await openedImage(native, ready); await paint(image);
    const before = report(world, native), row = root(), zoom = row.querySelector('[data-control="zoom-in"]')!;
    fire(zoom, 'click'); const manual = native.wire().filter((value) => value.schema === 'presentation-manual-v2').at(-1)!;
    expect(manual).toMatchObject({ type: 'present', op: 'zoom', targets: [A], values: { factor: 2 }, client: ready.client, appRunId: ready.appRunId,
      base: { [A]: { sourceGeneration: 1, lifetimeGeneration: LIFE, propertyVersions: command.instances[0].propertyVersions } } });
    expect(manual).toMatchObject({ requestId: expect.any(String), commandId: expect.any(String), actionId: expect.any(String) });
    await native.text(structuredClone(command)); await microtasks(); world.session.ask('after manual preview'); const pin = askReport(native);
    expect(item(pin, A).view.zoom).toBe(2); expect(item(pin, A).propertyVersions).toEqual(item(before, A).propertyVersions);
    expect(item(pin, A).propertyOwners).toEqual(item(before, A).propertyOwners); expect(world.fetchNative).toHaveBeenCalledTimes(1);
    expect(pin.commandResults).not.toContainEqual(expect.objectContaining({ commandId: manual.commandId, phase: 'drawn' }));
    fire(row.querySelector('[data-control="close"]')!, 'click'); expect(native.wire().filter((value) => value.schema === 'presentation-manual-v2')).toHaveLength(2);
    const closeReport = report(world, native), closeProposal = native.wire().filter((value) => value.schema === 'presentation-manual-v2').at(-1)!;
    expect(item(closeReport, A).propertyVersions.open).toBe(1);
    expect(closeReport.commandResults).not.toContainEqual(expect.objectContaining({ commandId: closeProposal.commandId, phase: 'closed' }));
  });
  sessionTest('disconnect and explicit reopen dispose old roots/URLs/controls and late old decode cannot enter the fresh pair', async () => {
    const world = make(), first = await accept(world), painted = await openedImage(first.native, first.ready); await paint(painted.image);
    const firstReport = report(world, first.native), oldRoot = root(), oldControl = oldRoot.querySelector('[data-control="zoom-in"]')!;
    const pending = await openedImage(first.native, first.ready, B, 'held-decode'), finishOld = pending.image.captureDecode();
    first.native.disconnected(); await microtasks();
    expect(dom.host.contains(oldRoot)).toBe(false); expect(world.revoked).toContain(world.blobs[0].url);
    expect(world.session.collect({ seq: firstReport.seq + 1, nowMs: now })).toBeNull(); expect(() => world.session.ask('offline')).toThrow();
    const count = first.native.sent.length; fire(oldControl, 'click'); expect(first.native.sent).toHaveLength(count);
    world.connection.open(); const current = await accept(world, 'second');
    const hello = current.native.wire().find((value) => value.type === 'workspace-hello')!;
    expect(hello).toMatchObject({ ...proposal }); expect(hello.requestId).not.toBe(first.ready.requestId);
    await first.native.text(cannedCommand(first.ready, B, 'stale-wire')); finishOld(); await microtasks(); dom.frame();
    const replacement = await openedImage(current.native, current.ready); await paint(replacement.image); const value = report(world, current.native);
    expect(item(value, A).connected).toBe(true); expect(value.items.some((row) => row.id === B)).toBe(false);
    expect(value.commandResults.every((row) => row.commandId !== pending.command.commandId)).toBe(true);
    expect(new Headers(world.fetchNative.mock.calls.at(-1)![1]?.headers).get('X-Micromind-Workspace-Grant')).toBe(current.ready.connectionGrant);
    expect(value.client.connectionEpoch).toBe(current.ready.client.connectionEpoch); expect(value.seq).toBeGreaterThan(firstReport.seq);
    vi.advanceTimersByTime(500); expect(NativeSocket.instances).toHaveLength(2); expect(world.errors).toEqual([]);
  });
  sessionTest('retired native fetch is aborted and even an abort-ignoring late Response cannot publish roots or errors into its successor', async () => {
    const world = make(), first = await accept(world), held = deferred<Response>(); world.setFetch(() => held.promise);
    await first.native.text(cannedCommand(first.ready)); await vi.waitFor(() => expect(world.fetchNative).toHaveBeenCalledTimes(1));
    const signal = world.fetchNative.mock.calls[0][1]?.signal; expect(signal?.aborted).toBe(false);
    first.native.disconnected(); expect(signal?.aborted).toBe(true); world.connection.open();
    const current = await accept(world, 'second'); world.setFetch(async () => nativeResponse());
    const replacement = await openedImage(current.native, current.ready); await paint(replacement.image);
    held.resolve(nativeResponse()); await microtasks(); const value = report(world, current.native);
    expect(world.blobs).toHaveLength(1); expect(value.commandResults.map((row) => row.commandId)).toEqual([replacement.command.commandId]);
    expect(world.errors).toEqual([]); expect(value.client).toEqual(current.ready.client);
  });
  sessionTest('terminal Session disposal fences a held hello and preserves the existing UI without pretending to stop Connection retries', async () => {
    const world = make(), native = newestSocket(); native.opened(); const ready = cannedReady(native, 'held');
    world.session.dispose(); await native.text(ready); await world.openings.at(-1); await microtasks();
    world.reporter.tick(++now); expect(native.wire().filter((value) => value.type === 'screen')).toEqual([]);
    expect(world.session.collect({ seq: 1, nowMs: now })).toBeNull(); expect(() => world.session.ask('disposed')).toThrow();
    expect(dom.host.contains(world.magnifier)).toBe(true); expect(dom.host.contains(world.viewer)).toBe(true); expect(world.blobs).toEqual([]);
    // No pagehide/pageshow or claim that Session owns/closes the native socket.
  });
});

sessionTest('two arrangements can retain the same image without stealing placement identity',async()=>{
 const ref={day:'2026-10-16',id:'d1',version:null,picture:0};
 document.body.dataset.activeWorkspaceView='tab';
 const roots=new Map([['one',{dataset:{workspaceView:'tab',placementId:'first'}}],['two',{dataset:{workspaceView:'tab',placementId:'generated'}}]]);
 const rows=[{id:'one',resource:ref}],restore=vi.fn();
 const request=vi.fn(async()=>{rows.push({id:'two',resource:ref});return {ok:true};});
 const session=Object.assign(Object.create(screen.WorkspaceSession.prototype),{disposed:false,
  pair:{context:{appRunId:'run'},windows:{retainedViews:()=>rows,root:(id:string)=>roots.get(id),restore}},
  options:{platform:{now:()=>0},reporter:{sampleAtSend:(_:number,cb:(value:object)=>void)=>cb({})},connection:{request}},
 });
 await session.openRetainedResource({resource:ref,placementId:'second',title:'Same picture',zoom:1,pan:[.5,.5]});
 expect(request).toHaveBeenCalledOnce();expect(restore).not.toHaveBeenCalled();
 expect(roots.get('one')!.dataset.placementId).toBe('first');expect(roots.get('two')!.dataset.placementId).toBe('second');
});
sessionTest('changing representation never closes the same resource in another work tab',()=>{
 const ref={day:'2026-10-16',id:'d1',version:null,picture:0};document.body.dataset.activeWorkspaceView='here';
 const closed:string[]=[];
 const session=Object.assign(Object.create(screen.WorkspaceSession.prototype),{pair:{windows:{
  retainedViews:()=>[{id:'here',resource:ref},{id:'elsewhere',resource:ref}],root:(id:string)=>({dataset:{workspaceView:id}}),floating:(id:string)=>({close:()=>closed.push(id)}),
 }}});
 session.closeRetainedResource(ref);expect(closed).toEqual(['here']);
});

sessionTest('scientific transport waits for canonical observation acknowledgement and stays on its accepted connection', async () => {
  const world = make(), { native, ready } = await accept(world);
  const transport = world.session.scientificTransport().request;
  const pending = transport('/api/scientific-reviews', { method: 'POST', body: '{}' });
  const report = native.wire().at(-1)!;
  expect(report.type).toBe('screen');
  expect(report.schema).toBe('screen-observation-v2');
  expect(world.fetchNative).not.toHaveBeenCalled();
  native.text({ type: 'result', for: 'screen', requestId: report.requestId, ok: true });
  await pending;
  const [path, init] = world.fetchNative.mock.calls.at(-1)!;
  expect(path).toBe('/api/scientific-reviews');
  expect(new Headers(init?.headers).get('X-Micromind-Workspace-Grant')).toBe(ready.connectionGrant);
  expect(new Headers(init?.headers).get('X-MicroscopeMind')).toBe('scientific-review');
  expect(init).toMatchObject({ credentials: 'same-origin', redirect: 'error' });
  native.disconnected();
  await expect(transport('/api/scientific-reviews')).rejects.toThrow('connection ended');
  expect(world.fetchNative).toHaveBeenCalledTimes(1);
});

sessionTest('larger scripts use the same acknowledged scientific transport and connection grant', async () => {
  const world = make(), { native, ready } = await accept(world);
  const transport = world.session.scientificTransport().request;
  const path = '/api/scientific-reviews/private-review/script';
  const body = JSON.stringify({ request: { op: 'python-resources', source: '# calibration\n'.repeat(3000) }, draw: null });
  const pending = transport(path, { method: 'POST', body });
  const report = native.wire().at(-1)!;
  expect(report.type).toBe('screen');
  expect(world.fetchNative).not.toHaveBeenCalled();
  native.text({ type: 'result', for: 'screen', requestId: report.requestId, ok: true });
  await pending;
  const [sent, init] = world.fetchNative.mock.calls.at(-1)!;
  expect(sent).toBe(path); expect(init?.body).toBe(body);
  expect(new Headers(init?.headers).get('X-Micromind-Workspace-Grant')).toBe(ready.connectionGrant);
  expect(init).toMatchObject({ credentials: 'same-origin', redirect: 'error' });
  await expect(transport(path + '/other')).rejects.toThrow('declared');
  native.disconnected();
  await expect(transport(path, { method: 'POST', body })).rejects.toThrow('connection ended');
  expect(world.fetchNative).toHaveBeenCalledOnce();
});

sessionTest('scientific transport refuses unrelated endpoints, mutation abort signals and rejected observations', async () => {
  const world = make(), { native } = await accept(world);
  const transport = world.session.scientificTransport().request;
  await expect(transport('https://elsewhere.invalid')).rejects.toThrow('declared');
  await expect(transport('/api/scientific-reviews', { method: 'POST', signal: new AbortController().signal })).rejects.toThrow('explicitly');
  const pending = transport('/api/scientific-reviews', { method: 'POST' });
  const report = native.wire().at(-1)!;
  native.text({ type: 'result', for: 'screen', requestId: report.requestId, ok: false, error: 'Window unavailable' });
  await expect(pending).rejects.toThrow('Window unavailable');
  expect(world.fetchNative).not.toHaveBeenCalled();
});

sessionTest('scientific transport releases a late response and never submits after a lost observation acknowledgement', async () => {
  const world = make(), { native } = await accept(world);
  const transport = world.session.scientificTransport().request;
  let finish!: (response: Response) => void;
  world.setFetch(() => new Promise<Response>(resolve => { finish = resolve; }));
  const reading = transport('/api/scientific-reviews');
  native.disconnected();
  const released = vi.fn();
  finish(new Response(new ReadableStream({ cancel: released })));
  await expect(reading).rejects.toThrow('connection ended');
  expect(released).toHaveBeenCalledOnce();

  const second = make(), accepted = await accept(second, 'second');
  const sending = second.session.scientificTransport().request('/api/scientific-reviews', { method: 'POST' });
  accepted.native.disconnected();
  await expect(sending).rejects.toThrow('Connection closed');
  expect(second.fetchNative).not.toHaveBeenCalled();
});

// The native delivery can arrive before its request reply, after the user navigates.
sessionTest.each(['tab','project'])('retained delivery keeps its initiating arrangement across a %s switch',async changed=>{
 const world=make(),{native,ready}=await accept(world);
 document.body.dataset.workspaceProject='2026-10-08';document.body.dataset.activeWorkspaceView='original-tab';
 const saved={resource:{day:'2026-10-08',id:'synthetic-result',version:0,picture:0},placementId:'original-placement',title:'Saved image',zoom:1,pan:[.5,.5]};
 const pending=world.session.openRetainedResource(saved);
 await microtasks();
 const request=native.wire().find(row=>row.type==='present'&&row.do==='open')!;
 expect(request).toBeDefined();
 document.body.dataset.activeWorkspaceView='new-tab';
 if(changed==='project')document.body.dataset.workspaceProject='another-project';
 const command=cannedCommand(ready);
 expect(command.requestId).not.toBe(request.requestId);
 // A previous connection's mapping cannot poison the pending destination.
 await native.text({type:'presentation-admitted',requestId:request.requestId,executionRequestId:'stale-execution',client:{...ready.client,connectionEpoch:'retired'},appRunId:ready.appRunId});
 await native.text({type:'presentation-admitted',requestId:request.requestId,executionRequestId:command.requestId,client:ready.client,appRunId:ready.appRunId});
 // An accepted association cannot later be replaced.
 await native.text({type:'presentation-admitted',requestId:request.requestId,executionRequestId:'replacement',client:ready.client,appRunId:ready.appRunId});
 await native.text(command);await vi.waitFor(()=>expect(dom.host.querySelector(`[data-presentation-id="${A}"]`)).not.toBeNull());
 expect(root().dataset).toMatchObject({project:saved.resource.day,workspaceView:'original-tab',placementId:'original-placement',workspaceHidden:'true'});
 await native.text({type:'result',for:'present',requestId:request.requestId,ok:true});await pending;
 expect(root().dataset.placementId).toBe('original-placement');
});

sessionTest('concurrent reopens of identical bytes preserve each placement when delivery order reverses',async()=>{
 const world=make(),{native,ready}=await accept(world);
 document.body.dataset.workspaceProject='2026-10-08';
 const saved={resource:{day:'2026-10-08',id:'synthetic-result',version:0,picture:0},title:'Saved image',zoom:1,pan:[.5,.5]};
 document.body.dataset.activeWorkspaceView='first-tab';
 const first=world.session.openRetainedResource({...saved,placementId:'first-placement'});
 document.body.dataset.activeWorkspaceView='second-tab';
 const second=world.session.openRetainedResource({...saved,placementId:'second-placement'});
 await microtasks();const requests=native.wire().filter(row=>row.type==='present'&&row.do==='open');
 expect(requests).toHaveLength(2);
 for(const [index,id] of [[1,B],[0,A]] as const){
  const command=cannedCommand(ready,id,`concurrent-${index}`);
  expect(command.requestId).not.toBe(requests[index].requestId);
  await native.text({type:'presentation-admitted',requestId:requests[index].requestId,executionRequestId:command.requestId,client:ready.client,appRunId:ready.appRunId});
  await native.text(command);await vi.waitFor(()=>expect(dom.host.querySelector(`[data-presentation-id="${id}"]`)).not.toBeNull());
  await native.text({type:'result',for:'present',requestId:requests[index].requestId,ok:true});
 }
 await Promise.all([first,second]);
 expect(root(A).dataset).toMatchObject({project:saved.resource.day,workspaceView:'first-tab',placementId:'first-placement',workspaceHidden:'true'});
 expect(root(B).dataset).toMatchObject({project:saved.resource.day,workspaceView:'second-tab',placementId:'second-placement',workspaceHidden:'false'});
});

sessionTest.each([true,false])('retained admission keeps local foreground intent: %s',async foreground=>{
 const world=make(),{native,ready}=await accept(world);
 document.body.dataset.workspaceProject='2026-10-08';document.body.dataset.activeWorkspaceView='original-tab';
 const sentinel={id:'selected',group:'workspace-window',isOpen:()=>true,close:()=>{},footprint:()=>null,setStackOrder:vi.fn()};
 world.panels.register(sentinel);
 const saved={resource:{day:'2026-10-08',id:'synthetic-result',version:0,picture:0},placementId:'original-placement',title:'Saved image',zoom:1,pan:[.5,.5]};
 const pending=world.session.openRetainedResource(saved,foreground);await microtasks();
 const request=native.wire().find(row=>row.type==='present'&&row.do==='open')!;
 const command=cannedCommand(ready);
 await native.text({type:'presentation-admitted',requestId:request.requestId,executionRequestId:command.requestId,client:ready.client,appRunId:ready.appRunId});
 await native.text(command);await vi.waitFor(()=>expect(root().querySelector('img')).toBeInstanceOf(TestImage));
 await paint(root().querySelector('img') as TestImage);
 expect(world.panels.top()?.id).toBe(foreground?A:'selected');
 await native.text({type:'result',for:'present',requestId:request.requestId,ok:true});await pending;
});

sessionTest('native panel pixels use the same connection grant and exact bounded-frame route', async () => {
  const world=make(), {native,ready}=await accept(world);
  const transport=world.session.scientificTransport().request;
  const path='/api/scientific-reviews/private-review/native-panels/'+'b'.repeat(64);
  await transport(path);
  const [sent,init]=world.fetchNative.mock.calls.at(-1)!;
  expect(sent).toBe(path);
  expect(new Headers(init?.headers).get('X-Micromind-Workspace-Grant')).toBe(ready.connectionGrant);
  for(const invalid of [path+'/other',path.slice(0,-1),path.replace('native-panels','unknown')])
    await expect(transport(invalid)).rejects.toThrow('declared');
  native.disconnected();await expect(transport(path)).rejects.toThrow('connection ended');
  expect(world.fetchNative).toHaveBeenCalledOnce();
});

sessionTest('view exports use the acknowledged scientific transport without admitting other paths', async () => {
  const world=make(), {native,ready}=await accept(world);
  const transport=world.session.scientificTransport().request;
  const path='/api/scientific-reviews/private-review/exports';
  const pending=transport(path,{method:'POST',body:'{"frame":"captured-frame"}'});
  void pending.catch(()=>undefined); // Keep a failed admission observable without an unhandled rejection.
  const report=native.wire().at(-1)!;
  expect(report.type).toBe('screen');expect(world.fetchNative).not.toHaveBeenCalled();
  native.text({type:'result',for:'screen',requestId:report.requestId,ok:true});
  await pending;
  await transport(path);
  expect(world.fetchNative).toHaveBeenCalledTimes(2);
  expect(new Headers(world.fetchNative.mock.calls[0][1]?.headers).get('X-Micromind-Workspace-Grant')).toBe(ready.connectionGrant);
  for(const invalid of [path+'/other',path+'?file=elsewhere',path.replace('/exports','/export')])
    await expect(transport(invalid)).rejects.toThrow('declared');
  native.disconnected();await expect(transport(path)).rejects.toThrow('connection ended');
  expect(world.fetchNative).toHaveBeenCalledTimes(2);
});

sessionTest('Project clear retires revoked native sources while hidden same-Project Tabs remain reportable', async () => {
  const world = make(), { native, ready } = await accept(world);
  const original = await openedImage(native, ready); await paint(original.image);
  root().dataset.project = '2026-10-08'; root().dataset.workspaceView = 'first-tab';
  root().dataset.workspaceHidden = 'true';
  const before = report(world, native);
  expect(before.sources.map(source => source.key)).toContain(original.command.instances[0].sourceRef.key);
  await native.text({ type: 'session-cleared', previous: '2026-10-08', session: '2026-10-09', why: 'switch' });
  const cleared = report(world, native);
  expect(cleared.sources).toEqual([]); expect(cleared.commandResults).toEqual([]);
  expect(cleared.seq).toBeGreaterThan(before.seq); expect(cleared.client).toEqual(before.client);
  expect(dom.host.querySelector(`[data-presentation-id="${A}"]`)).toBeNull();
  expect(world.revoked).toEqual(world.blobs.map(blob => blob.url));
  await native.text(original.command); await microtasks();
  expect(report(world, native).sources).toEqual([]);

  document.body.dataset.workspaceProject = '2026-10-08'; document.body.dataset.activeWorkspaceView = 'first-tab';
  const saved = { resource: original.command.instances[0].sourceRef.identity.resource, placementId: 'retained-placement', title: 'Saved image', zoom: 1, pan: [.5, .5] };
  const reopening = world.session.openRetainedResource(saved); await microtasks();
  const request = native.wire().filter(row => row.type === 'present' && row.do === 'open').at(-1)!;
  expect(request.source).toEqual(saved.resource); expect(request.screen!.sources).toEqual([]);
  const replacement = cannedCommand(ready, B, 'fresh-grant');
  await native.text({ type: 'presentation-admitted', requestId: request.requestId, executionRequestId: replacement.requestId, client: ready.client, appRunId: ready.appRunId });
  await native.text(replacement); await vi.waitFor(() => expect(root(B).querySelector('img')).toBeInstanceOf(TestImage));
  await paint(root(B).querySelector('img') as TestImage);
  await native.text({ type: 'result', for: 'present', requestId: request.requestId, ok: true }); await reopening;
  expect(root(B).dataset.placementId).toBe(saved.placementId);
  expect(report(world, native).sources.map(source => source.key)).toEqual([replacement.instances[0].sourceRef.key]);
  expect(native.wire().filter(row => row.type === 'workspace-hello')).toHaveLength(1);
  expect(world.errors).toEqual([]);
});

sessionTest('Project clear aborts pending native delivery and fences an abort-ignoring late response', async () => {
  const world = make(), { native, ready } = await accept(world), held = deferred<Response>();
  world.setFetch(() => held.promise);
  const old = cannedCommand(ready); await native.text(old);
  await vi.waitFor(() => expect(world.fetchNative).toHaveBeenCalledOnce());
  const signal = world.fetchNative.mock.calls[0][1]?.signal;
  await native.text({ type: 'session-cleared', previous: '2026-10-08', session: '2026-10-09', why: 'switch' });
  expect(signal?.aborted).toBe(true);
  world.setFetch(async () => nativeResponse());
  const current = await openedImage(native, ready, B, 'new-project'); await paint(current.image);
  held.resolve(nativeResponse()); await microtasks();
  await native.text(old); await microtasks();
  const value = report(world, native);
  expect(world.blobs).toHaveLength(1); expect(value.sources.map(source => source.key)).toEqual([current.command.instances[0].sourceRef.key]);
  expect(value.commandResults.map(result => result.commandId)).toEqual([current.command.commandId]);
  expect(world.errors).toEqual([]);
});
