// TEST-ONLY adoption proposal: real WorkspaceWindows + reducer, synthetic transport/DOM/decode.
// No browser pixels, HTTP authority, Registry execution, camera, model or capture proof.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { PanelStack } from './panels';
import type { WorkspaceStructuralInput } from './workspace-runtime';
import { WorkspaceWindows } from './workspace-windows';
import type { ManualWindowCommand, WindowContext } from './workspace-windows';
import { snapshotInteraction, createInteractionState, workspaceToSource } from './workspace-observation';
import { fire, settleMicrotasks, syntheticDOM, TestElement, TestImage } from './workspace-windows.test-support';

const context: WindowContext = { workspaceId: 'workspace-a', clientId: 'client-a', pageEpoch: 'page-a', connectionEpoch: 'socket-a', appRunId: 'run-a' };
const { appRunId, ...client } = context;
const A = '627623e8-c7d5-4805-9146-8dc6c0592f05', B = '60e9a614-f811-4291-80b2-604987c3bb9f';
const life = '4bde9c04-84ed-4b39-ab52-a4470f93785f';
// SHA-256 standard test vector, genuine bytes; deliberately not an actual JPEG decoder fixture.
const ABC = new Uint8Array([97, 98, 99]);
const DIGEST = 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad';
const WRONG_DIGEST = 'a52d159f262b2c6ddb724a61840befc36eb30c88877a4030b65cbe86298449c9';
const source = {
  key: 'synthetic:d2:v0:picture0', kind: 'saved-image', media: 'capture', availability: 'retained',
  identity: { resource: { day: '2026-10-08', id: 'd2', version: 0, picture: 0 }, population: 'e2', member: 'c14', revision: 0 },
  provenance: { run: 'historical-run', boot: 'historical-boot', seq: 999, transforms: [] }, digest: DIGEST,
};
function descriptor(id = A) {
  return {
    id, workspaceId: context.workspaceId, sourceRef: structuredClone(source),
    sourceGeneration: 1, lifetimeGeneration: life, revision: 1, open: true, staged: false,
    title: 'synthetic retained image', geometryIntent: { position: [20.25, 40.5], size: [360, 280] },
    view: { zoom: 1, pan: [0.5, 0.5] },
    propertyVersions: { geometry: 3, source: 1, open: 1, zoom: 1, pan: 1, front: 1 },
    propertyOwners: { geometry: 'request-a', source: 'request-a', open: 'request-a', zoom: 'request-a', pan: 'request-a', front: 'request-a' },
    delivery: { sourceToken: 'opaque-prepared-lease-a', url: '/api/presentation-sources/opaque-prepared-lease-a', payloadBinding: { servedDigest: DIGEST, originalDigest: DIGEST, transforms: [] } },
  };
}
type Descriptor = ReturnType<typeof descriptor>;
function command(instances = [descriptor()], commandId = 'command-open', op = 'open') {
  return {
    type: 'present', schema: 'presentation-command-v2', op, commandId, requestId: 'request-a', actionId: 'action-open', client: { ...client }, appRunId,
    targets: instances.map((item) => item.id), instances,
    fence: {
      requestId: 'request-a', actionId: 'action-open', commandId, appRunId, client: { ...client },
      afterReportSeq: 0, issuedMono: 1, deadlineMono: 20,
      targets: Object.fromEntries(instances.map((item) => [item.id, {
        lifetimeGeneration: item.lifetimeGeneration, sourceGeneration: item.sourceGeneration, committedRevision: item.revision,
        basePropertyVersions: { open: 0 }, committedPropertyVersions: { open: item.propertyVersions.open }, owner: 'request-a',
        sourceRef: structuredClone(item.sourceRef), sourceToken: item.delivery.sourceToken, payloadBinding: structuredClone(item.delivery.payloadBinding),
      }])),
    },
  };
}
type Command = ReturnType<typeof command>;
type WindowFact = ReturnType<WorkspaceWindows['facts']>[number];
type Interaction = ReturnType<typeof snapshotInteraction>;
type Result = {
  requestId: string; actionId: string; commandId: string; appRunId: string; presentationId: string;
  phase: string; reportSeq: number; lifetimeGeneration: string; sourceGeneration: number; committedRevision: number;
  committedPropertyVersions: Record<string, number>; propertyVersions: Record<string, number>;
  propertyOwners: Record<string, string | null>; sourceToken: string; sourceKey: string;
  drawRef: string | null; absent: boolean; error?: { code: string; detail: string };
  payloadBinding?: Record<string, unknown>;
};
type Report = {
  type: 'screen'; schema: 'screen-observation-v2'; client: typeof client; appRunId: string; seq: number;
  complete: boolean; items: WindowFact[]; sources: Descriptor['sourceRef'][];
  targets: { id: string; type: string; presentationId: string; sourceKey: string }[];
  edges: { type: string; from: string; to: string }[]; interaction: Interaction; commandResults: Result[];
};
type SampleOptions = { seq: number; nowMs: number; selected: readonly string[] };
// Proposed public seam only. Production module deliberately absent at this TEST-only checkpoint.
type Runtime = {
  receive(message: unknown): Promise<void>;
  retryLive(id: string): void;
  collect(options: SampleOptions): Report;
  snapshotAtSend(options: SampleOptions): Report;
  manual(command: ManualWindowCommand): void;
  dispose(): void;
};
type Factory = new (options: {
  context: WindowContext; host: HTMLElement; windows: WorkspaceWindows;
  send(command: unknown): void; fetch: typeof fetch;
  createObjectURL(blob: Blob): string; revokeObjectURL(url: string): void;
  viewport(): { layoutSize: number[]; visualRect: number[]; visualScale: number; documentScroll: number[]; dpr: number; visible: boolean; documentFocused: boolean; activeSurface: string };
  now(): number;
  structural?(): WorkspaceStructuralInput;
}) => Runtime;

let dom: ReturnType<typeof syntheticDOM>;
let runtime: Runtime | undefined;
let windows: WorkspaceWindows | undefined;
let now = 10;
beforeEach(() => {
  dom = syntheticDOM(); now = 10;
  vi.stubGlobal('document', dom.document); vi.stubGlobal('window', globalThis);
  vi.stubGlobal('HTMLElement', TestElement); vi.stubGlobal('HTMLImageElement', TestImage);
  vi.stubGlobal('requestAnimationFrame', dom.raf);
  vi.stubGlobal('getComputedStyle', (element: TestElement) => ({ ...element.style, display: element.hidden ? 'none' : 'block' }));
});
afterEach(() => { runtime?.dispose(); windows?.dispose(); runtime = undefined; windows = undefined; vi.unstubAllGlobals(); });
let pageVisible = true; // the studio on the user's display, or in the background (another app or tab in front)
async function make(bytes = ABC, openCapture?: ConstructorParameters<typeof WorkspaceWindows>[0]['openCapture'], structural?: () => WorkspaceStructuralInput) {
  pageVisible = true;
  const path = './workspace-runtime.ts';
  const module = await import(/* @vite-ignore */ path).catch((cause: unknown) => { throw new Error('MISSING_FEATURE: actual Studio WorkspaceRuntime adoption seam', { cause }); });
  expect(typeof module.WorkspaceRuntime, 'MISSING_FEATURE: public runtime adapter').toBe('function');
  const sent: unknown[] = [], blobs: Blob[] = [], revoked: string[] = [], panels = new PanelStack();
  const fetchBytes = vi.fn<typeof fetch>(async () => new Response(new Uint8Array(bytes).buffer, { headers: { 'Content-Type': 'image/jpeg' } }));
  const real = new WorkspaceWindows({ host: dom.host as unknown as HTMLElement, context, panels, openCapture,
    send: (value) => runtime!.manual(value), retryLive: (id) => runtime!.retryLive(id), facts: () => ({ canvas: dom.host.getBoundingClientRect(), area: { width: 1200, height: 800 }, disc: null, object: null, labels: [] }) });
  windows = real;
  runtime = new (module.WorkspaceRuntime as Factory)({ context, host: dom.host as unknown as HTMLElement, windows: real,
    send: (value) => sent.push(value), fetch: fetchBytes, structural,
    createObjectURL: (blob) => { blobs.push(blob); return `blob:synthetic/prepared-${blobs.length}`; },
    revokeObjectURL: (url) => { revoked.push(url); }, now: () => now,
    viewport: () => ({ layoutSize: [1200, 800], visualRect: [0, 0, 1200, 800], visualScale: 1, documentScroll: [0, 0], dpr: 2, visible: pageVisible, documentFocused: true, activeSurface: 'workspace' }),
  });
  return { runtime, windows: real, sent, blobs, revoked, fetchBytes, panels };
}
function sample(seq = 1) { return runtime!.collect({ seq, nowMs: now, selected: [] }); }
function root(id = A) { return windows!.root(id) as unknown as TestElement; }
function item(id = A, report = sample()) { const row = report.items.find((value) => value.id === id); expect(row).toBeDefined(); return row!; }
function result(commandId = 'command-open', id = A, report = sample()) { const row = report.commandResults.find((value) => value.commandId === commandId && value.presentationId === id); expect(row).toBeDefined(); return row!; }
function backendContract(report: Report, message: Command, predicates: string[]) {
  const services = fileURLToPath(new URL('../../../services/', import.meta.url));
  const consumer = fileURLToPath(new URL('../../../services/tests/native_workspace_contract_consumer.py', import.meta.url));
  const python = process.env.MICROMIND_CONTRACT_PYTHON
    ?? fileURLToPath(new URL('../../../services/.venv/bin/python', import.meta.url));
  return JSON.parse(execFileSync(python, [consumer], {
    input: JSON.stringify({ report, command: message, predicates }), encoding: 'utf8',
    env: { ...process.env, PYTHONPATH: services },
  })) as { projected: { items: Array<{ id: string; payloadBinding: Record<string, unknown>;
    controls: Record<string, { target: string; enabled: boolean; stateRevision: number; editable: boolean }> }> };
    result: { status: string; unmatched: Array<{ code: string }> } };
}
async function decode(id = A) {
  await settleMicrotasks(); const image = root(id).querySelector('img'); expect(image).toBeInstanceOf(TestImage);
  (image as TestImage).finishDecode(148, 176); await settleMicrotasks();
}
// Deliver a physical synthetic event to the adapter's installed host listener. No fake hit reducer.
function observe(target: TestElement, type: string, fields: Record<string, unknown> = {}) {
  fire(dom.host, type, { target, ...fields });
}
function moveOver(id = A) {
  const row = item(id); const [x, y, width, height] = row.contentRect;
  const point = [x + width / 2, y + height / 2];
  now++;
  observe(root(id).querySelector('img')!, 'pointermove', { pointerId: 1, pointerType: 'mouse', isPrimary: true, clientX: point[0], clientY: point[1], buttons: 0 });
  return point;
}

describe('Studio runtime saved-image vertical through real window handlers', () => {
  test('repeated closed windows do not poison the next rendered result or Send observation', async () => {
    const { runtime } = await make();
    for (let n = 0; n < 9; n++) {
      const id = `627623e8-c7d5-4805-9146-${String(n).padStart(12, '0')}`;
      const opened = descriptor(id);
      await runtime.receive(command([opened], `history-open-${n}`)); await decode(id); dom.frame();
      for (let v = 2; v <= 4; v++) {
        const moved = structuredClone(opened); moved.revision = v; moved.propertyVersions.open = v;
        await runtime.receive(command([moved], `history-update-${n}-${v}`, 'open')); dom.frame();
      }
      const closed = structuredClone(opened); closed.open = false; closed.revision = 5;
      closed.lifetimeGeneration = `527623e8-c7d5-4805-9146-${String(n).padStart(12, '0')}`; closed.propertyVersions.open = 5;
      await runtime.receive(command([closed], `history-close-${n}`, 'close'));
      now += 20000;
    }
    const latest = command([descriptor(B)], 'latest-open');
    await runtime.receive(latest); await decode(B); dom.frame();
    const report = sample(80);
    expect(backendContract(report, latest, ['source_exact']).result.status).toBe('confirmed');
    expect(runtime.snapshotAtSend({ seq: 81, nowMs: now, selected: [] }).items.some(row => row.id === B)).toBe(true);
  });

  test('a command given while the studio is in the background keeps its receipt until it is drawn and seen', async () => {
    // Live 2026-10-11 (second stack): a zoom given in the background lost its receipt after 4 s, so once the studio
    // was shown and drew it, the app found "no matching command acknowledgment".
    const { runtime } = await make();
    pageVisible = false;
    const open = command(); open.fence.issuedMono = 100; open.fence.deadlineMono = 104;
    await runtime.receive(open); await decode();
    sample();
    now += 60000; // a minute in the background
    expect(sample().commandResults.some(row => row.commandId === open.commandId)).toBe(true);
    pageVisible = true; dom.frame(); // shown again: painted
    expect(result(open.commandId).phase).toBe('drawn');
    now += 3000; // still within its own window, counted from the return
    expect(sample().commandResults.some(row => row.commandId === open.commandId)).toBe(true);
    now += 2000;
    expect(sample().commandResults.some(row => row.commandId === open.commandId)).toBe(false);
  });

  test('closed confirmation remains through its deadline, then report history retires without losing restore state', async () => {
    const { runtime, windows } = await make();
    await runtime.receive(command()); await decode(); dom.frame();
    const closed = descriptor(); closed.open = false; closed.revision = 2; closed.propertyVersions.open = 2;
    Object.assign(closed.propertyVersions, { hidden: 1 }); Object.assign(closed.propertyOwners, { hidden: 'request-a' });
    const close = command([closed], 'bounded-close', 'close');
    close.fence.issuedMono = 100; close.fence.deadlineMono = 104;
    await runtime.receive(close);
    now += 3999;
    expect(result('bounded-close').phase).toBe('closed');
    expect(item().open).toBe(false);
    now += 2;
    const retired = sample();
    expect(retired.commandResults.some(row => row.commandId === 'bounded-close')).toBe(false);
    // The older open receipt has a longer deadline and must remain independent.
    expect(retired.commandResults.some(row => row.commandId === 'command-open')).toBe(true);
    now += 20000;
    const quiet = sample();
    expect(quiet.items).toHaveLength(0); expect(quiet.commandResults).toHaveLength(0);
    expect(windows.facts().find(row => row.id === A)?.open).toBe(false);
    windows.restore(A);
    expect(runtime.snapshotAtSend({ seq: 90, nowMs: now, selected: [] }).items.find(row => row.id === A)?.open).toBe(false);
    const reopened = descriptor(); reopened.revision = 3; reopened.propertyVersions.open = 3;
    reopened.lifetimeGeneration = 'a5ce4f17-49a4-42f4-a988-5037e80998ae';
    await runtime.receive(command([reopened], 'retained-restore')); await decode(); dom.frame();
    expect(item().load.state).toBe('drawn');
    expect(result('retained-restore').phase).toBe('drawn');
  });
  test('actual decoded native payload reaches accepted Screen and source completion without fixture enrichment', async () => {
    const { runtime } = await make(); const message = command();
    await runtime.receive(message);
    expect(item().payloadBinding).not.toHaveProperty('decodedToken');
    await decode();
    const loaded = item();
    expect(result().drawRef).toBeNull();
    dom.frame(); const report = sample(7);
    expect(item(A, report).load.state).toBe('drawn');
    const consumer = backendContract(report, message, ['source_exact']);
    expect(loaded.payloadBinding).toMatchObject({ decodedToken: descriptor().delivery.sourceToken,
      servedDigest: DIGEST, originalDigest: DIGEST });
    expect(consumer.projected.items[0].payloadBinding).toMatchObject({ decodedToken: descriptor().delivery.sourceToken,
      servedDigest: DIGEST, originalDigest: DIGEST });
    expect(consumer.result.status).toBe('confirmed');
  });
  test('actual native controls reach trusted default Screen recipes and independent control completion', async () => {
    const { runtime } = await make(); const message = command();
    await runtime.receive(message); await decode(); dom.frame();
    const report = sample(7), actual = item(A, report);
    const close = root().querySelector('[data-control="close"]')!;
    const zoom = root().querySelector('[data-control="zoom-in"]')!;
    const axes = ['pan-x', 'pan-y'].map((name) => root().querySelector(`[data-control="${name}"]`)!);
    expect(actual.controls.find((control) => control.id === close.id)).toMatchObject({ role: 'button', targetId: A, enabled: true, present: true, editable: false });
    expect(actual.controls.find((control) => control.id === zoom.id)).toMatchObject({ role: 'button', targetId: A, enabled: true, present: true, editable: false });
    for (const axis of axes) expect(actual.controls.find((control) => control.id === axis.id)).toMatchObject({ role: 'slider', targetId: A, enabled: true, present: true, editable: false });
    const consumer = backendContract(report, message, ['control']);
    const projected = consumer.projected.items.find((value) => value.id === A)!;
    for (const name of ['close', 'zoom', 'pan']) expect(projected.controls[name]).toMatchObject({ target: A, enabled: true, editable: false });
    expect(consumer.result.status).toBe('confirmed');
  });
  test('wrong fetched bytes never acquire a decoded token or a drawn completion', async () => {
    const { runtime } = await make(new Uint8Array([100, 101, 102]));
    await expect(runtime.receive(command())).rejects.toMatchObject({ code: 'source_mismatch' });
    dom.frame(); const report = sample(7);
    expect(result('command-open', A, report)).toMatchObject({ phase: 'error', drawRef: null, error: { code: 'source_mismatch' } });
    expect(result('command-open', A, report).payloadBinding).not.toHaveProperty('decodedToken');
    expect(report.items).toEqual([]);
  });
  test.each(['missing-axis', 'wrong-target', 'wrong-revision'] as const)
  ('trusted native pan pair refuses %s without borrowing the valid sibling', async (fault) => {
    const { runtime } = await make(); const message = command([descriptor(A), descriptor(B)]);
    await runtime.receive(message); await decode(A); await decode(B); dom.frame();
    const before = sample(7);
    expect(backendContract(before, message, ['control']).result.status).toBe('confirmed');
    if (fault === 'missing-axis') root(A).querySelector('[data-control="pan-y"]')!.remove();
    const report = sample(8), axis = item(A, report).controls.find((control) => control.id === `pan-y:${A}`)!;
    expect(axis).toBeDefined();
    // Adversarial external report facts, after real node collection. The
    // missing-axis case instead removes the actual installed slider node.
    if (fault === 'wrong-target') axis.targetId = B;
    if (fault === 'wrong-revision') axis.stateRevision!++;
    if (fault === 'missing-axis') expect(axis.present).toBe(false);
    const consumer = backendContract(report, message, ['control']);
    expect(consumer.projected.items.find((value) => value.id === A)!.controls).not.toHaveProperty('pan');
    expect(consumer.projected.items.find((value) => value.id === B)!.controls.pan).toMatchObject({ enabled: true, target: B });
    expect(consumer.result.status).not.toBe('confirmed');
    expect(consumer.result.unmatched).toEqual(expect.arrayContaining([expect.objectContaining({ code: 'CONTROL_UNAVAILABLE' })]));
  });
  test('accepted Screen refuses a wrong decoder acknowledgement on an otherwise genuine drawn report', async () => {
    const { runtime } = await make(); const message = command();
    await runtime.receive(message); await decode(); dom.frame(); const report = sample(7);
    expect(backendContract(report, message, ['source_exact']).result.status).toBe('confirmed');
    // Only the adversarial acknowledgement changes; actual digests, decode,
    // draw, source identity and causal result remain from the real producer.
    item(A, report).payloadBinding!.decodedToken = 'foreign-decode-token';
    const consumer = backendContract(report, message, ['source_exact']);
    expect(consumer.result.status).not.toBe('confirmed');
    expect(consumer.result.unmatched).toEqual(expect.arrayContaining([expect.objectContaining({ code: 'SOURCE_MISMATCH' })]));
  });
  test('an actual superseded image decode cannot acknowledge the replacement delivery', async () => {
    const { runtime } = await make(); const original = command(); await runtime.receive(original);
    const oldImage = root(A).querySelector('img') as TestImage;
    const replacement = descriptor(); replacement.revision++; replacement.sourceGeneration++;
    replacement.propertyVersions.source++; replacement.sourceRef.key = 'synthetic:d2:v0:replacement';
    replacement.sourceRef.identity.resource.picture = 2;
    replacement.delivery.sourceToken = 'opaque-replacement-decoder';
    replacement.delivery.url = '/api/presentation-sources/opaque-replacement-decoder';
    const message = command([replacement], 'command-replacement-decoder'); await runtime.receive(message);
    oldImage.finishDecode(148, 176); await settleMicrotasks(); dom.frame(); const loading = sample(7);
    expect(item(A, loading).load.state).toBe('loading');
    expect(item(A, loading).payloadBinding).not.toHaveProperty('decodedToken');
    expect(result(original.commandId, A, loading)).toMatchObject({ phase: 'unknown', drawRef: null });
    expect(result(message.commandId, A, loading)).toMatchObject({ phase: 'loading', drawRef: null });
    await decode(A); dom.frame(); const drawn = sample(8);
    expect(item(A, drawn).payloadBinding).toMatchObject({ decodedToken: replacement.delivery.sourceToken });
    expect(backendContract(drawn, message, ['source_exact']).result.status).toBe('confirmed');
    expect(result(original.commandId, A, drawn)).toMatchObject({ phase: 'unknown', drawRef: null });
  });
  test('canonical open uses the existing renderer and reports actual load transitions', async () => {
    const { runtime, windows, blobs, fetchBytes } = await make(); const apply = vi.spyOn(windows, 'apply');
    await runtime.receive(command()); expect(apply).toHaveBeenCalledTimes(1);
    expect(apply.mock.calls[0][0]).toMatchObject({ context, requestId: 'request-a', commandId: 'command-open', actionOccurrenceId: 'action-open', op: 'open', items: [{ id: A, source }] });
    expect(fetchBytes).toHaveBeenCalledWith(descriptor().delivery.url, expect.objectContaining({ credentials: 'same-origin' }));
    expect(Array.from(new Uint8Array(await blobs[0].arrayBuffer()))).toEqual([97, 98, 99]);
    expect(root().querySelector('img')?.getAttribute('src') ?? (root().querySelector('img') as TestImage).src).toBe('blob:synthetic/prepared-1');
    expect(item().load.state).toBe('loading'); expect(result()).toMatchObject({ phase: 'loading', drawRef: null, absent: false });
    await decode(); expect(item().load.state).toBe('loaded'); expect(result().drawRef).toBeNull();
    dom.frame(); const report = sample(7); expect(report).toMatchObject({ type: 'screen', schema: 'screen-observation-v2', client, appRunId, seq: 7, complete: true });
    expect(report.sources).toEqual([source]); expect(item(A, report)).toMatchObject({ connected: true, visible: true, staged: false, payloadBinding: { servedDigest: DIGEST }, liveSample: null });
    expect(result('command-open', A, report)).toMatchObject({ requestId: 'request-a', actionId: 'action-open', commandId: 'command-open', appRunId, presentationId: A, reportSeq: 7, phase: 'drawn', sourceToken: 'opaque-prepared-lease-a', sourceKey: source.key, lifetimeGeneration: life, committedRevision: 1, committedPropertyVersions: { open: 1 }, absent: false });
    expect(result('command-open', A, report).drawRef).toBe(item(A, report).load.drawToken);
    expect(report.edges).toContainEqual({ type: 'shows', from: A, to: source.key });
    expect(result('command-open', A, report)).not.toHaveProperty('fence');
  });
  test('same source duplicate has independent roots and close leaves the sibling drawn', async () => {
    const { runtime, panels } = await make(); await runtime.receive(command([descriptor(A), descriptor(B)], 'command-two', 'duplicate'));
    await decode(A); await decode(B); dom.frame(); const sibling = root(B);
    expect(root(A)).not.toBe(sibling); expect(panels.obstacles()).toHaveLength(2); expect(sample().sources).toEqual([source]);
    const closed = descriptor(A); closed.open = false; closed.revision = 2; closed.propertyVersions.open = 2;
    const close = command([closed], 'command-close', 'close'); close.actionId = close.fence.actionId = 'action-close'; close.fence.targets[A].basePropertyVersions.open = 1;
    await runtime.receive(close); const report = sample(9);
    expect(item(A, report)).toMatchObject({ open: false, connected: false, visible: false, load: { state: 'empty', drawToken: null } });
    expect(result('command-close', A, report)).toMatchObject({ actionId: 'action-close', phase: 'closed', absent: true, committedRevision: 2, committedPropertyVersions: { open: 2 }, reportSeq: 9, drawRef: null });
    expect(root(B)).toBe(sibling); expect(item(B, report)).toMatchObject({ connected: true, load: { state: 'drawn' } });
  });
  test('replay retains a manual preview and exactly one latest result per causal item', async () => {
    const { runtime, fetchBytes } = await make(); const message = command(); await runtime.receive(message); await decode(); dom.frame();
    const node = root(); fire(root().querySelector('[data-control="zoom-in"]')!, 'click');
    await runtime.receive(structuredClone(message)); dom.frame(); const report = sample(4);
    expect(root()).toBe(node); expect(item(A, report).view.zoom).toBe(2); expect(fetchBytes).toHaveBeenCalledTimes(1);
    expect(report.commandResults.filter((row) => row.commandId === message.commandId && row.presentationId === A)).toHaveLength(1);
    expect(result('command-open', A, report)).toMatchObject({ actionId: 'action-open', reportSeq: 4, committedPropertyVersions: { open: 1 } });
  });
  test('installed manual control emits accepted-origin occurrence and retains base versions', async () => {
    const { runtime, sent } = await make(); await runtime.receive(command()); await decode(); dom.frame();
    fire(root().querySelector('[data-control="zoom-in"]')!, 'click');
    expect(sent.at(-1)).toMatchObject({ type: 'present', schema: 'presentation-manual-v2', op: 'zoom', targets: [A], values: { factor: 2 }, client, appRunId,
      base: { [A]: { sourceGeneration: 1, lifetimeGeneration: life, propertyVersions: { zoom: 1 } } } });
    expect(sent.at(-1)).toMatchObject({ requestId: expect.any(String), commandId: expect.any(String), actionId: expect.any(String) });
    expect(item().view.zoom).toBe(2); expect(item().propertyVersions.zoom).toBe(1); expect(item().propertyOwners.zoom).toBe('request-a');
  });
  test('measured fetched bytes cannot inherit the desired served digest or reach image decode', async () => {
    const { runtime, windows, blobs } = await make(new Uint8Array([97, 98, 100])); const apply = vi.spyOn(windows, 'apply');
    await expect(runtime.receive(command())).rejects.toMatchObject({ code: 'source_mismatch' });
    expect(apply).not.toHaveBeenCalled(); expect(blobs).toHaveLength(0); expect(dom.images).toHaveLength(0);
    expect(result()).toMatchObject({ phase: 'error', drawRef: null, absent: false, error: { code: 'source_mismatch' }, payloadBinding: { servedDigest: WRONG_DIGEST } });
    expect(result().payloadBinding?.servedDigest).not.toBe(DIGEST);
  });
  test.each(['workspaceId', 'clientId', 'pageEpoch', 'connectionEpoch', 'appRunId'] as const)('wrong accepted %s is denied before fetch or renderer mutation', async (key) => {
    const { runtime, windows, fetchBytes } = await make(); await runtime.receive(command()); const node = root(); const apply = vi.spyOn(windows, 'apply'); fetchBytes.mockClear();
    const wrong = command([descriptor(B)], 'command-other');
    if (key === 'appRunId') wrong.appRunId = wrong.fence.appRunId = 'other'; else wrong.client[key] = wrong.fence.client[key] = 'other';
    await expect(runtime.receive(wrong)).rejects.toMatchObject({ code: 'context_mismatch' });
    expect(fetchBytes).not.toHaveBeenCalled(); expect(apply).not.toHaveBeenCalled(); expect(root()).toBe(node);
    expect(sample().commandResults.some((row) => row.commandId === 'command-other')).toBe(false);
  });
  test('source fence mismatch is refused before any native delivery or root', async () => {
    const { runtime, fetchBytes } = await make(); const message = command(); message.fence.targets[A].sourceToken = 'different-lease';
    await expect(runtime.receive(message)).rejects.toMatchObject({ code: 'source_mismatch' });
    expect(fetchBytes).not.toHaveBeenCalled(); expect(windows!.facts()).toHaveLength(0);
  });
  test('mutated command replay cannot alter a previously admitted occurrence', async () => {
    const { runtime, fetchBytes } = await make(); await runtime.receive(command()); const node = root(); fetchBytes.mockClear();
    const changed = command(); changed.instances[0].title = 'changed replay';
    await expect(runtime.receive(changed)).rejects.toMatchObject({ code: 'command_mismatch' });
    expect(fetchBytes).not.toHaveBeenCalled(); expect(root()).toBe(node); expect(sample().commandResults).toHaveLength(1);
  });
});

describe('Studio runtime registered DOM interaction at Send', () => {
  test('construction adds no frame loop and an empty collection uses the existing pure reducer', async () => {
    const listen = vi.spyOn(dom.host, 'addEventListener');
    const { runtime } = await make(); expect(dom.frames).toHaveLength(0);
    expect(listen.mock.calls.filter(([type]) => type === 'pointermove')).toHaveLength(1);
    expect(listen.mock.calls.filter(([type]) => type === 'focusin')).toHaveLength(1);
    expect(runtime.collect({ seq: 1, nowMs: 10, selected: [] }).interaction).toEqual(snapshotInteraction(createInteractionState(), { nowMs: 10, selected: [] }));
  });
  test('current Send maps actual image CSS to source and keeps later pointer changes out of the pin', async () => {
    const { runtime } = await make(); await runtime.receive(command([descriptor(A), descriptor(B)])); await decode(A); await decode(B); dom.frame();
    const css = moveOver(A); const target = sample().targets.find((row) => row.presentationId === A)!;
    const pin = runtime.snapshotAtSend({ seq: 6, nowMs: now, selected: [target.id] }); const serialized = JSON.stringify(pin);
    expect(pin).toMatchObject({ client, appRunId, seq: 6, interaction: { pointer: { presentationId: A, sourceKey: source.key, css }, selected: [target.id] } });
    expect(pin.interaction.pointer.sourcePoint).toEqual(workspaceToSource(css, item(A, pin) as Parameters<typeof workspaceToSource>[1]));
    expect(pin.interaction.pointer.sourcePoint?.[0]).toBeCloseTo(74, 6); expect(pin.interaction.pointer.sourcePoint?.[1]).toBeCloseTo(88, 6);
    moveOver(B); expect(sample(7).interaction.pointer.presentationId).toBe(B);
    expect(JSON.stringify(pin)).toBe(serialized); expect(Object.isFrozen(pin)).toBe(true); expect(Object.isFrozen(pin.interaction.pointer)).toBe(true);
  });
  test('registered window focus survives editor focus without retaining editor contents', async () => {
    const { runtime } = await make(); await runtime.receive(command());
    const close = root().querySelector('[data-control="close"]')!; now++; observe(close, 'focusin');
    expect(sample().interaction.focus).toMatchObject({ presentationId: A, controlId: close.id, role: 'button', editable: false });
    const editor = new TestElement('textarea'); editor.id = 'ask-input'; editor.value = 'PRIVATE EDITOR CONTENT'; editor.textContent = 'PRIVATE TEXT'; dom.host.append(editor);
    now++; observe(editor, 'focusin'); const pin = runtime.snapshotAtSend({ seq: 3, nowMs: now, selected: [] });
    expect(pin.interaction.focus).toMatchObject({ presentationId: null, role: 'textbox', editable: true });
    expect(pin.interaction.lastContentFocus).toMatchObject({ presentationId: A, controlId: close.id, historical: true });
    expect(JSON.stringify(pin)).not.toContain('PRIVATE'); expect(pin.interaction.focus).not.toHaveProperty('value');
  });
  test('an unregistered node cannot mint a target by imitating presentation data attributes', async () => {
    const { runtime } = await make(); await runtime.receive(command()); await decode(); dom.frame();
    const forged = new TestElement('img'); forged.dataset.presentationId = A; forged.dataset.control = 'close'; forged.id = 'close:' + A; dom.host.append(forged);
    now++; observe(forged, 'pointermove', { pointerId: 2, pointerType: 'mouse', isPrimary: true, clientX: 70, clientY: 100, buttons: 0 });
    const pin = runtime.snapshotAtSend({ seq: 2, nowMs: now, selected: [] });
    expect(pin.interaction.pointer).toMatchObject({ presentationId: null, sourceKey: null, targetCandidates: [] });
  });
  test('disposed runtime no longer collects stale DOM interactions or emits manual commands', async () => {
    const { runtime, sent } = await make(); await runtime.receive(command()); runtime.dispose();
    now++; observe(root().querySelector('[data-control="close"]')!, 'focusin');
    expect(() => runtime.snapshotAtSend({ seq: 2, nowMs: now, selected: [] })).toThrow(); expect(sent).toHaveLength(0);
  });
});

// These imports are intentionally only types; no production adoption module is executed here.
export type ProposedRuntimeCommand = Command;

// Append-only CONTRACT repros. Synthetic platform hit/capture/focus, actual renderer handlers.
import { syntheticPointerPlatform } from './workspace-runtime.test-support';

async function captureWorld() {
  const world = await make();
  const sibling = descriptor(B);
  sibling.geometryIntent.position = [650, 40.5];
  sibling.sourceRef.key = 'synthetic:d2:v0:picture1';
  sibling.sourceRef.identity.resource.picture = 1;
  sibling.sourceRef.identity.member = 'c15';
  sibling.delivery.sourceToken = 'opaque-prepared-lease-b';
  sibling.delivery.url = '/api/presentation-sources/opaque-prepared-lease-b';
  await world.runtime.receive(command([descriptor(A), sibling])); await decode(A); await decode(B); dom.frame();
  const platform = syntheticPointerPlatform(dom);
  platform.bind(root(A)); platform.bind(root(B));
  const viewport = platform.bind(root(A).querySelector('.workspace-window-viewport')!);
  const bar = platform.bind(root(A).querySelector('.fwin-bar')!);
  const imageA = root(A).querySelector('img')!, imageB = root(B).querySelector('img')!;
  const point = (id: string) => { const [x, y, width, height] = item(id).contentRect; return [x + width / 2, y + height / 2]; };
  const pointA = point(A), pointB = point(B);
  now++; platform.pointer(imageA, 'pointerdown', pointA);
  // Only the installed real viewport pan handler can have called this native API.
  expect(viewport.hasPointerCapture(1)).toBe(true);
  now++; platform.pointer(imageB, 'pointermove', pointB); dom.frame();
  expect((world.sent as { op?: string; targets?: string[] }[]).some((row) => row.op === 'pan' && row.targets?.[0] === A)).toBe(false); // The drag previews locally; release commits it.
  expect(item(A).view.pan?.[0]).toBe(0); expect(item(B).view.pan).toEqual([0.5, 0.5]);
  expect((dom.document as unknown as { elementFromPoint(x: number, y: number): TestElement }).elementFromPoint(pointB[0], pointB[1])).toBe(imageB);
  return { ...world, platform, viewport, bar, imageB, pointB, siblingKey: sibling.sourceRef.key };
}
function sendPin(seq: number) { return runtime!.snapshotAtSend({ seq, nowMs: now, selected: [] }); }

describe('Studio runtime actual capture APIs with synthetic physical hit surface', () => {
  test('installed viewport pan captures A while Send physically points at sibling B', async () => {
    const { viewport, siblingKey, pointB } = await captureWorld(); const eventAt = now;
    now++; const pin = sendPin(10);
    expect(viewport.hasPointerCapture(1)).toBe(true);
    expect(pin.interaction.pointer).toMatchObject({ presentationId: B, sourceKey: siblingKey, css: pointB, capturedBy: A, eventMonoMs: eventAt, buttons: 1 });
    expect(pin.interaction.pointer.sourcePoint?.[0]).toBeCloseTo(74, 6); expect(pin.interaction.pointer.sourcePoint?.[1]).toBeCloseTo(88, 6);
  });
  test('release of a recorded registered recipient without movement clears current capture and preserves the old pin', async () => {
    const { platform, viewport, bar, imageB, pointB, siblingKey } = await captureWorld();
    viewport.releasePointerCapture(1); bar.setPointerCapture(1);
    now++; platform.pointer(imageB, 'pointermove', pointB); const eventAt = now;
    const before = sendPin(11), beforeJSON = JSON.stringify(before);
    expect(before.interaction.pointer).toMatchObject({ presentationId: B, capturedBy: bar.id });
    now++; bar.releasePointerCapture(1); expect(bar.hasPointerCapture(1)).toBe(false);
    const after = sendPin(12);
    expect(after.interaction.pointer).toMatchObject({ presentationId: B, sourceKey: siblingKey, capturedBy: null, css: pointB, eventMonoMs: eventAt });
    expect(JSON.stringify(before)).toBe(beforeJSON); expect(before.interaction.pointer.capturedBy).toBe(bar.id);
  });
  test('viewport capture acquired after the last movement is observed at Send without inventing another move', async () => {
    const { viewport, pointB, siblingKey } = await captureWorld(); const eventAt = now;
    viewport.releasePointerCapture(1); const before = sendPin(13), beforeJSON = JSON.stringify(before);
    expect(before.interaction.pointer.capturedBy).toBeNull();
    now++; viewport.setPointerCapture(1); expect(viewport.hasPointerCapture(1)).toBe(true);
    const after = sendPin(14);
    expect(after.interaction.pointer).toMatchObject({ presentationId: B, sourceKey: siblingKey, capturedBy: A, css: pointB, eventMonoMs: eventAt });
    expect(JSON.stringify(before)).toBe(beforeJSON); expect(before.interaction.pointer.capturedBy).toBeNull();
  });
  test.each(['pointerup', 'pointercancel'] as const)('%s ends native viewport capture without a stale Send owner', async (end) => {
    const { platform, viewport, imageB, pointB } = await captureWorld();
    expect(viewport.hasPointerCapture(1)).toBe(true);
    now++; platform.pointer(imageB, end, pointB); expect(viewport.hasPointerCapture(1)).toBe(false);
    const pin = sendPin(15);
    expect(pin.interaction.pointer).toMatchObject({ capturedBy: null, buttons: 0, eventMonoMs: now }); expect(pin.interaction.contacts).toEqual([]);
    if (end === 'pointercancel') expect(pin.interaction.pointer).toMatchObject({ state: 'cancelled', presentationId: null, css: null });
    else expect(pin.interaction.pointer).toMatchObject({ state: 'present', presentationId: B, css: pointB });
  });
  test('an unregistered capture node cannot inherit a registered owner by copying its id or dataset', async () => {
    const { platform, viewport, bar, imageB, pointB, siblingKey } = await captureWorld();
    viewport.releasePointerCapture(1); bar.setPointerCapture(1);
    now++; platform.pointer(imageB, 'pointermove', pointB);
    expect(sendPin(16).interaction.pointer.capturedBy).toBe(bar.id);
    const forged = new TestElement('div'); forged.id = bar.id; forged.dataset.presentationId = A; forged.dataset.control = 'move'; dom.host.append(forged);
    const unknown = platform.bind(forged); now++; unknown.setPointerCapture(1);
    expect(unknown.hasPointerCapture(1)).toBe(true); expect(bar.hasPointerCapture(1)).toBe(false); expect(viewport.hasPointerCapture(1)).toBe(false);
    expect(sendPin(17).interaction.pointer).toMatchObject({ presentationId: B, sourceKey: siblingKey, capturedBy: null, css: pointB });
  });
});

describe('Studio runtime editable descendants retain actual containing presentation', () => {
  test.each(['textarea', 'contenteditable-descendant'] as const)('%s in B keeps current B apart from historical A without editor contents', async (kind) => {
    const { runtime } = await make(); await runtime.receive(command([descriptor(A), descriptor(B)]));
    const platform = syntheticPointerPlatform(dom), move = root(A).querySelector('.fwin-bar')!;
    now++; platform.focus(move);
    expect(sample().interaction.focus).toMatchObject({ presentationId: A, controlId: move.id, role: 'drag-handle', editable: false });
    const editor = new TestElement(kind === 'textarea' ? 'textarea' : 'span');
    editor.id = `unregistered-notes:${B}`; editor.value = 'PRIVATE VALUE C2'; editor.textContent = 'PRIVATE TEXT C2';
    if (kind === 'textarea') root(B).append(editor);
    else { const container = new TestElement('div'); container.setAttribute('contenteditable', 'true'); container.append(editor); root(B).append(container); }
    now++; platform.focus(editor); const pin = runtime.snapshotAtSend({ seq: 20, nowMs: now, selected: [] });
    expect(pin.interaction.focus).toMatchObject({ presentationId: B, controlId: null, role: 'textbox', editable: true });
    expect(pin.interaction.lastContentFocus).toMatchObject({ presentationId: A, controlId: move.id, historical: true });
    expect(pin.interaction.focus).not.toHaveProperty('value'); expect(pin.interaction.focus).not.toHaveProperty('textContent');
    expect(JSON.stringify(pin)).not.toContain('PRIVATE'); expect(JSON.stringify(pin)).not.toContain(editor.id);
  });
});

// Append-only view-paint adoption tracer. Actual Runtime/Windows; SYNTHETIC bytes/DOM/rAF only.
// No completion-only report, browser pixels, Screen authority or completed outcome is fabricated.
type ViewPaintItem = WindowFact & { viewDrawRef: string | null };
type ViewPaintResult = Result & { viewDrawRef: string | null };
type ViewPaintLease = Omit<Command['fence']['targets'][string], 'basePropertyVersions' | 'committedPropertyVersions'> & {
  basePropertyVersions: Record<string, number>; committedPropertyVersions: Record<string, number>; baseViewDrawRef: string | null;
};
type ViewPaintCommand = Omit<Command, 'fence'> & { fence: Omit<Command['fence'], 'targets'> & { targets: Record<string, ViewPaintLease> } };
function viewPaintItem(report = sample()) { return item(A, report) as ViewPaintItem; }
function viewPaintResult(commandId: string, report = sample()) { return result(commandId, A, report) as ViewPaintResult; }
function viewPaintCommand(before: Report, op: 'zoom' | 'pan' | 'front', commandId = `command-view-${op}`, owner = `request-view-${op}`): ViewPaintCommand {
  const prior = viewPaintItem(before), next = descriptor();
  next.revision = prior.revision + 1; next.sourceGeneration = prior.sourceGeneration; next.lifetimeGeneration = prior.lifetimeGeneration;
  next.geometryIntent = { position: prior.rect.slice(0, 2), size: prior.rect.slice(2) };
  next.view = { zoom: prior.view.zoom!, pan: prior.view.pan!.slice() };
  next.propertyVersions = { ...prior.propertyVersions } as Descriptor['propertyVersions'];
  next.propertyOwners = { ...prior.propertyOwners } as Descriptor['propertyOwners'];
  next.propertyVersions[op]++; next.propertyOwners[op] = owner;
  if (op === 'zoom') next.view.zoom++;
  if (op === 'pan') next.view.pan = [0.25, 0.75];
  const original = command([next], commandId, op), actionId = `action-${commandId}`;
  return { ...original, requestId: owner, actionId, fence: { ...original.fence, requestId: owner, actionId, afterReportSeq: before.seq,
    targets: { [A]: { ...original.fence.targets[A], owner, basePropertyVersions: { [op]: prior.propertyVersions[op] },
      committedPropertyVersions: { [op]: next.propertyVersions[op] }, baseViewDrawRef: prior.viewDrawRef } } } };
}
async function paintedRuntimeWorld() {
  const world = await make(); await world.runtime.receive(command([descriptor(A), descriptor(B)]));
  await decode(A); await decode(B); dom.frame();
  const baseline = world.runtime.snapshotAtSend({ seq: 30, nowMs: now, selected: [] }), baselineJSON = JSON.stringify(baseline);
  expect(viewPaintItem(baseline).viewDrawRef).toEqual(expect.any(String));
  expect(viewPaintResult('command-open', baseline).viewDrawRef).toBe(viewPaintItem(baseline).viewDrawRef);
  expect(viewPaintItem(baseline).viewDrawRef).not.toBe(viewPaintItem(baseline).load.drawToken);
  return { ...world, baseline, baselineJSON };
}

describe('Studio runtime original occurrence view paint through actual renderer', () => {
  test('manual front preserves settled baseline until the admitted command paints', async () => {
    const { runtime, baseline } = await paintedRuntimeWorld();
    const before = viewPaintItem(baseline);
    const control = root(A).querySelector('[data-control="front"]')!;
    // Synthetic EventTarget does not propagate: exercise the real capture handler
    // followed by the real button handler in browser event order.
    fire(root(A), 'pointerdown', { target: control });
    fire(control, 'click');
    const pending = runtime.snapshotAtSend({ seq: 31, nowMs: now, selected: [] });
    expect(viewPaintItem(pending).z).toBe(before.z);
    expect(viewPaintItem(pending).viewDrawRef).toBe(before.viewDrawRef);
    const message = viewPaintCommand(pending, 'front', 'manual-front-admitted');
    await runtime.receive(message); dom.frame();
    const after = sample(32);
    expect(viewPaintItem(after).viewDrawRef).toEqual(expect.any(String));
    expect(viewPaintItem(after).viewDrawRef).not.toBe(before.viewDrawRef);
    expect(viewPaintResult(message.commandId, after).viewDrawRef).toBe(viewPaintItem(after).viewDrawRef);
  });
  test('fronting an already foremost window gets a fresh settled observation, never from replay', async () => {
    const { runtime, baseline } = await paintedRuntimeWorld();
    await runtime.receive(viewPaintCommand(baseline, 'front', 'front-first'));
    dom.frame(); const before = runtime.snapshotAtSend({ seq: 31, nowMs: now, selected: [] });
    const prior = viewPaintItem(before), message = viewPaintCommand(before, 'front', 'front-again');
    await runtime.receive(message);
    expect(item(A).z).toBe(prior.z);
    expect(viewPaintResult(message.commandId).viewDrawRef).toBeNull();
    dom.frame(); const after = sample(32), painted = viewPaintItem(after);
    expect(painted.z).toBe(prior.z);
    expect(painted.viewDrawRef).toEqual(expect.any(String));
    expect(painted.viewDrawRef).not.toBe(prior.viewDrawRef);
    expect(viewPaintResult(message.commandId, after).viewDrawRef).toBe(painted.viewDrawRef);
    await runtime.receive(structuredClone(message)); dom.frame();
    expect(viewPaintItem(sample(33)).viewDrawRef).toBe(painted.viewDrawRef);
  });
  test.each(['zoom', 'pan', 'front'] as const)('%s retains original baseline and requires completed changed paint on the same result', async (op) => {
    const { runtime, baseline, baselineJSON } = await paintedRuntimeWorld();
    const message = viewPaintCommand(baseline, op), baseRef = message.fence.targets[A].baseViewDrawRef;
    const baseZ = item(A, baseline).z, image = root(A).querySelector('img')!, baseTransform = image.style.transform;
    await runtime.receive(message); const pending = sample(31);
    expect(viewPaintItem(pending).viewDrawRef).toBeNull(); expect(viewPaintResult(message.commandId, pending).viewDrawRef).toBeNull();
    if (op === 'zoom') { expect(item(A, pending).view.zoom).toBe(2); expect(image.style.transform).not.toBe(baseTransform); }
    if (op === 'pan') { expect(item(A, pending).view.pan).toEqual([0.25, 0.75]); expect(image.style.transform).not.toBe(baseTransform); }
    if (op === 'front') { expect(item(A, pending).z).toBeGreaterThan(baseZ); expect(Number(root(A).style.zIndex)).toBe(item(A, pending).z); }
    dom.frame(); const after = runtime.snapshotAtSend({ seq: 32, nowMs: now, selected: [] }), painted = viewPaintItem(after), ack = viewPaintResult(message.commandId, after);
    expect(painted.viewDrawRef).toEqual(expect.any(String)); expect(painted.viewDrawRef).not.toBe(baseRef); expect(painted.viewDrawRef).not.toBe(painted.load.drawToken);
    expect(ack.viewDrawRef).toBe(painted.viewDrawRef);
    expect(ack).toMatchObject({ requestId: message.requestId, actionId: message.actionId, commandId: message.commandId, presentationId: A,
      sourceToken: message.instances[0].delivery.sourceToken, committedPropertyVersions: message.fence.targets[A].committedPropertyVersions, owner: message.requestId });
    expect(message.fence.targets[A].baseViewDrawRef).toBe(baseRef); expect(viewPaintItem(baseline).viewDrawRef).toBe(baseRef);
    expect(JSON.stringify(baseline)).toBe(baselineJSON);
  });
  test('unchanged desired view, a slider number and replay cannot mint changed-view proof', async () => {
    const { runtime, baseline, baselineJSON } = await paintedRuntimeWorld(); const baseRef = viewPaintItem(baseline).viewDrawRef;
    const slider = root(A).querySelector('[data-control="pan-x"]')!; slider.value = '0.123';
    expect(item().view.pan).toEqual([0.5, 0.5]); dom.frame(); expect(viewPaintItem().viewDrawRef).toBe(baseRef);
    const message = viewPaintCommand(baseline, 'zoom', 'command-same-view'); message.instances[0].view.zoom = 1;
    await runtime.receive(message); dom.frame(); const beforeReplay = sample(33);
    expect(item(A, beforeReplay).view.zoom).toBe(1);
    expect([null, baseRef]).toContain(viewPaintItem(beforeReplay).viewDrawRef);
    expect(viewPaintResult(message.commandId, beforeReplay).viewDrawRef).toBeNull();
    await runtime.receive(structuredClone(message)); dom.frame(); const afterReplay = sample(34);
    expect(viewPaintItem(afterReplay).viewDrawRef).toBe(viewPaintItem(beforeReplay).viewDrawRef);
    expect(viewPaintResult(message.commandId, afterReplay).viewDrawRef).toBeNull();
    expect(message.fence.targets[A].baseViewDrawRef).toBe(baseRef); expect(JSON.stringify(baseline)).toBe(baselineJSON);
  });
  test('a queued view job cannot lend its paint to a replaced source or the old result', async () => {
    const { runtime, baseline, baselineJSON } = await paintedRuntimeWorld();
    const zoom = viewPaintCommand(baseline, 'zoom', 'command-before-replacement');
    await runtime.receive(zoom); const oldImage = root(A).querySelector('img');
    expect(viewPaintItem().viewDrawRef).toBeNull();
    const replacement = descriptor(); replacement.revision = 3; replacement.sourceGeneration = 2;
    replacement.sourceRef.key = 'synthetic:d2:v0:picture2'; replacement.sourceRef.identity.resource.picture = 2;
    replacement.sourceRef.identity.member = 'c16'; replacement.propertyVersions.source = 2;
    replacement.delivery.sourceToken = 'opaque-view-replacement'; replacement.delivery.url = '/api/presentation-sources/opaque-view-replacement';
    const newer = command([replacement], 'command-source-replacement'); await runtime.receive(newer);
    expect(root(A).querySelector('img')).not.toBe(oldImage);
    dom.frame(); const loading = sample(37);
    expect(item(A, loading)).toMatchObject({ sourceGeneration: 2, sourceToken: replacement.delivery.sourceToken, load: { state: 'loading', drawToken: null } });
    expect(viewPaintItem(loading).viewDrawRef).toBeNull(); expect(viewPaintResult(zoom.commandId, loading).viewDrawRef).toBeNull();
    await decode(A); dom.frame(); const after = sample(38), painted = viewPaintItem(after);
    expect(painted.source).toEqual(replacement.sourceRef); expect(painted.viewDrawRef).toEqual(expect.any(String));
    expect(painted.viewDrawRef).not.toBe(viewPaintItem(baseline).viewDrawRef); expect(painted.viewDrawRef).not.toBe(painted.load.drawToken);
    expect(viewPaintResult(newer.commandId, after).viewDrawRef).toBe(painted.viewDrawRef);
    expect(viewPaintResult(zoom.commandId, after).viewDrawRef).toBeNull();
    expect(viewPaintResult(zoom.commandId, after)).toMatchObject({ commandId: zoom.commandId, sourceGeneration: 1, sourceToken: zoom.instances[0].delivery.sourceToken });
    expect(JSON.stringify(baseline)).toBe(baselineJSON);
  });
  test('pending manual zoom and a newer owner cannot lend their view paint to the old occurrence', async () => {
    const { runtime, windows, baseline, baselineJSON, sent } = await paintedRuntimeWorld();
    const original = viewPaintCommand(baseline, 'zoom', 'command-first-owner', 'request-first-owner');
    await runtime.receive(original); windows.dispatchUser({ action: 'zoom', instance: A, values: { level: 3 } });
    expect(sent.at(-1)).toMatchObject({ op: 'zoom', targets: [A], base: { [A]: { propertyVersions: { zoom: 2 } } } });
    expect(item().view.zoom).toBe(3); dom.frame(); const preview = sample(35);
    expect(viewPaintItem(preview).viewDrawRef).toBeNull(); expect(viewPaintResult(original.commandId, preview).viewDrawRef).toBeNull();
    const newer = viewPaintCommand(preview, 'zoom', 'command-new-owner', 'request-new-owner');
    newer.instances[0].view.zoom = 3; expect(newer.fence.targets[A].baseViewDrawRef).toBeNull();
    await runtime.receive(newer); dom.frame(); const after = sample(36);
    expect(item(A, after).view.zoom).toBe(3); expect(viewPaintItem(after).viewDrawRef).toEqual(expect.any(String));
    expect(viewPaintResult(newer.commandId, after).viewDrawRef).toBe(viewPaintItem(after).viewDrawRef);
    expect(viewPaintResult(original.commandId, after).viewDrawRef).toBeNull();
    expect(viewPaintResult(original.commandId, after)).toMatchObject({ requestId: original.requestId, actionId: original.actionId, commandId: original.commandId, owner: original.requestId });
    expect(original.fence.targets[A].baseViewDrawRef).toBe(viewPaintItem(baseline).viewDrawRef); expect(JSON.stringify(baseline)).toBe(baselineJSON);
  });
});

// Append-only SPEC WV-S1 control: real Runtime/Windows with SYNTHETIC native bytes/DOM/rAF.
test('equivalent recursively reordered source and payload maps cannot mint changed view paint for a new owner', async () => {
  const { runtime, baseline, baselineJSON, fetchBytes } = await paintedRuntimeWorld();
  const prior = viewPaintItem(baseline), baseRef = prior.viewDrawRef;
  const message = viewPaintCommand(baseline, 'zoom', 'command-key-order-only', 'request-key-order-only');
  message.instances[0].view.zoom = 1;
  const reorder = <T extends object>(value: T): T => Object.fromEntries(Object.entries(value).reverse()) as T;
  const next = message.instances[0];
  next.sourceRef.identity.resource = reorder(next.sourceRef.identity.resource);
  next.sourceRef.identity = reorder(next.sourceRef.identity);
  next.sourceRef.provenance = reorder(next.sourceRef.provenance);
  next.sourceRef = reorder(next.sourceRef);
  next.delivery.payloadBinding = reorder(next.delivery.payloadBinding);
  message.fence.targets[A].sourceRef = structuredClone(next.sourceRef);
  message.fence.targets[A].payloadBinding = structuredClone(next.delivery.payloadBinding);
  const { decodedToken, ...admittedBinding } = prior.payloadBinding!;
  expect(decodedToken).toBe(prior.sourceToken);
  expect(next.sourceRef).toEqual(prior.source); expect(next.delivery.payloadBinding).toEqual(admittedBinding);
  // Actual order differs, while values, arrays, source identity and native delivery remain exact.
  expect(JSON.stringify(next.sourceRef)).not.toBe(JSON.stringify(prior.source));
  expect(JSON.stringify(next.delivery.payloadBinding)).not.toBe(JSON.stringify(admittedBinding));
  fetchBytes.mockClear(); await runtime.receive(message); expect(fetchBytes).not.toHaveBeenCalled();
  // The defective raw-key-order path reloads an actual TestImage; finish that real decoder if needed.
  if (item().load.state === 'loading') await decode(A);
  dom.frame(); const after = runtime.snapshotAtSend({ seq: 45, nowMs: now, selected: [] }), observed = viewPaintItem(after);
  expect(observed.load.state).toBe('drawn'); expect(observed.source).toEqual(prior.source);
  expect(observed).toMatchObject({ sourceToken: prior.sourceToken, sourceGeneration: prior.sourceGeneration, lifetimeGeneration: prior.lifetimeGeneration,
    rect: prior.rect, contentRect: prior.contentRect, z: prior.z, view: prior.view });
  expect(observed.viewTransform).toMatchObject({ sourceToWorkspace: prior.viewTransform!.sourceToWorkspace,
    sourceToContent: prior.viewTransform!.sourceToContent, contentOrigin: prior.viewTransform!.contentOrigin, originalSize: prior.viewTransform!.originalSize });
  expect([null, baseRef]).toContain(observed.viewDrawRef);
  expect(viewPaintResult(message.commandId, after)).toMatchObject({ requestId: message.requestId, actionId: message.actionId, commandId: message.commandId,
    owner: message.requestId, viewDrawRef: null });
  expect(message.fence.targets[A].baseViewDrawRef).toBe(baseRef);
  expect(viewPaintItem(baseline).viewDrawRef).toBe(baseRef); expect(JSON.stringify(baseline)).toBe(baselineJSON);
});

// Append-only native-manual sequence contract. Actual Runtime.manual and Windows;
// synthetic accepted context/DOM/decode/rAF/transport, no backend admission proof.
function manualSequenceProposal(id = A, correlation = 'browser-manual-one'): ManualWindowCommand {
  const fact = item(id);
  return { type: 'present', action: 'zoom', instance: id, values: { factor: 2 }, context: { ...context },
    requestId: `browser-request/${correlation}`, commandId: `browser-command/${correlation}`,
    actionOccurrenceId: `browser-action/${correlation}`,
    base: { sourceGeneration: fact.sourceGeneration, lifetimeGeneration: fact.lifetimeGeneration,
      propertyVersions: { ...fact.propertyVersions } } };
}
function requireManualSequence(packet: unknown, expected: number) {
  expect(packet, 'MISSING_FEATURE: actual Runtime.manual must emit a positive monotonic manualSeq')
    .toMatchObject({ schema: 'presentation-manual-v2', manualSeq: expected });
}
async function manualSequenceWorld() {
  const world = await make(); await world.runtime.receive(command([descriptor(A), descriptor(B)]));
  await decode(A); await decode(B); dom.frame();
  return world;
}

describe('actual Runtime manual sequence allocation at validated outbound ingress', () => {
  test('consecutive new intents allocate once and retain original correlation and complete base', async () => {
    const { runtime, sent } = await manualSequenceWorld();
    const proposal = manualSequenceProposal(), original = JSON.stringify(proposal);
    runtime.manual(proposal); requireManualSequence(sent[0], 1);
    expect(sent[0]).toMatchObject({ type: 'present', op: 'zoom', targets: [A], values: { factor: 2 }, client, appRunId,
      requestId: proposal.requestId, actionId: proposal.actionOccurrenceId, commandId: proposal.commandId, base: { [A]: proposal.base } });
    runtime.manual(manualSequenceProposal(A, 'explicit-second-intent')); requireManualSequence(sent[1], 2);
    expect(sent).toHaveLength(2); expect(JSON.stringify(proposal)).toBe(original);
  });

  test.each(['factor-zero', 'stale-base', 'foreign-client', 'unknown-field', 'missing-target'] as const)
  ('invalid %s emits nothing and does not burn the next sequence', async (bad) => {
    const { runtime, sent } = await manualSequenceWorld(); const proposal = manualSequenceProposal();
    if (bad === 'factor-zero') proposal.values.factor = 0;
    if (bad === 'stale-base') proposal.base.propertyVersions.zoom++;
    if (bad === 'foreign-client') proposal.context.connectionEpoch = 'external-unaccepted-epoch';
    if (bad === 'unknown-field') Object.assign(proposal, { externalField: true });
    if (bad === 'missing-target') proposal.instance = 'external-unregistered-target';
    expect(() => runtime.manual(proposal)).toThrow(); expect(sent).toHaveLength(0);
    runtime.manual(manualSequenceProposal()); requireManualSequence(sent[0], 1);
  });

  test('installed zoom and pan controls allocate in one Runtime sequence stream', async () => {
    const { sent } = await manualSequenceWorld();
    fire(root(A).querySelector('[data-control="zoom-in"]')!, 'click'); requireManualSequence(sent[0], 1);
    const pan = root(B).querySelector('[data-control="pan-x"]')!; pan.value = '0.25';
    fire(pan, 'input'); expect(sent).toHaveLength(1);
    fire(pan, 'change'); requireManualSequence(sent[1], 2);
    expect(sent[0]).toMatchObject({ op: 'zoom', targets: [A] });
    expect(sent[1]).toMatchObject({ op: 'pan', targets: [B], values: { center: [0.25, 0.5] } });
  });

  test('actual reports, layout and received native commands never allocate manual sequence', async () => {
    const { runtime, sent } = await manualSequenceWorld();
    runtime.manual(manualSequenceProposal()); requireManualSequence(sent[0], 1);
    runtime.collect({ seq: 900, nowMs: now + 100, selected: [`target:${B}`] });
    root(B).style.left = '450px'; dom.frame();
    runtime.snapshotAtSend({ seq: 901, nowMs: now + 101, selected: [`target:${A}`] });
    await runtime.receive(command([descriptor(B)], 'server-command-independent', 'open')); dom.frame();
    runtime.manual(manualSequenceProposal(A, 'after-server-and-reports')); requireManualSequence(sent[1], 2);
    expect(sent).toHaveLength(2);
  });

  test('transport retransmission reuses frozen packet while explicit fresh manual intent gets the next number', async () => {
    const { runtime, sent } = await manualSequenceWorld();
    runtime.manual(manualSequenceProposal()); requireManualSequence(sent[0], 1);
    // Transport retries bytes already serialized by the real method; it does
    // not invoke manual again or stand in for a server association/executor.
    const frozen = JSON.stringify(sent[0]), retransmission = JSON.parse(frozen);
    expect(JSON.stringify(retransmission)).toBe(frozen); requireManualSequence(retransmission, 1);
    runtime.manual(manualSequenceProposal(A, 'new-user-intent')); requireManualSequence(sent[1], 2);
    expect(JSON.stringify(sent[0])).toBe(frozen);
  });

  test('disposed Runtime refuses a valid proposal without emitting', async () => {
    const { runtime, sent } = await manualSequenceWorld(); const proposal = manualSequenceProposal();
    runtime.manual(proposal); requireManualSequence(sent[0], 1);
    runtime.dispose(); expect(() => runtime.manual(proposal)).toThrow(); expect(sent).toHaveLength(1);
  });

  test('the actual private counter reaches MAX_SAFE_INTEGER and refuses overflow without wrapping or sending', async () => {
    const { runtime, sent } = await manualSequenceWorld();
    runtime.manual(manualSequenceProposal()); requireManualSequence(sent[0], 1);
    expect(Object.hasOwn(runtime, 'manualSeq'), 'MISSING_FEATURE: Runtime-owned outbound counter, independent of reportSeq').toBe(true);
    // Boundary seed only: the real manual validator/allocator/send all run.
    // No copied producer or loop of quadrillions of synthetic operations.
    Reflect.set(runtime, 'manualSeq', Number.MAX_SAFE_INTEGER - 1);
    runtime.manual(manualSequenceProposal(A, 'last-safe-intent')); requireManualSequence(sent[1], Number.MAX_SAFE_INTEGER);
    expect(() => runtime.manual(manualSequenceProposal(A, 'exhausted-intent'))).toThrow();
    expect(sent).toHaveLength(2); expect(Reflect.get(runtime, 'manualSeq')).toBe(Number.MAX_SAFE_INTEGER);
  });
});


// Synthetic authenticated transport/decoder evidence. Actual producer and HTTP
// grant ownership are independently covered by backend integration tests.
describe('native live source delivery', () => {
  function liveDescriptor() {
    const value = descriptor();
    return { ...value, sourceRef: {
      key: 'live-motion-run-a-m1', kind: 'live', media: 'labelled', availability: 'temporary',
      identity: { run: appRunId, resource: { day: '2026-10-08', id: 'm1', version: null, picture: 0 }, producer: 'original-producer' },
      provenance: { basis: 'current-camera' }, digest: null,
    }, delivery: { ...value.delivery, payloadBinding: { servedDigest: null, originalDigest: null, contentType: 'image/jpeg' } } } as unknown as Descriptor;
  }
  const actualSample = { run: appRunId, boot: 'camera-boot', seq: 75, frame: 19, epoch: 2 };
  function response(token: string, changed: Record<string, string> = {}) {
    return new Response(ABC.slice().buffer, { headers: {
      'Content-Type': 'image/jpeg', 'X-Micromind-Source-Token': token,
      'X-Micromind-Live-Sample': JSON.stringify(actualSample),
      'X-Micromind-Image-Size': '[1280,720]', 'X-Micromind-Content-Sha256': DIGEST, ...changed,
    } });
  }
  test('held original pixels stop renewal and retain truthful resize and close receipts', async () => {
    const { runtime, fetchBytes, revoked } = await make(); const live = liveDescriptor();
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken, { 'X-Micromind-Live-State': 'held' }));
    await runtime.receive(command([live]));
    await vi.waitFor(() => expect(dom.images.some(image => image.decodeCalls > 0)).toBe(true));
    for (const image of dom.images) image.finishDecode(1280, 720);
    await settleMicrotasks(); dom.frame();
    expect(item().liveSample).toBeNull(); expect(item().heldSample).toEqual(actualSample);
    expect(result().drawRef).toEqual({ held: actualSample });
    expect(root(A).querySelector('.window-kind')?.textContent).toBe('Held');
    expect(item().unknown).toContain('live-source-held');
    await new Promise(resolve => setTimeout(resolve, 250));
    expect(fetchBytes).toHaveBeenCalledTimes(1);
    const resized = { ...live, revision: 2, geometryIntent: { ...live.geometryIntent, size: [420, 300] },
      propertyVersions: { ...live.propertyVersions, geometry: live.propertyVersions.geometry + 1 } };
    const resize = command([resized], 'held-resize', 'resize');
    Object.assign(resize.fence.targets[A], { basePropertyVersions: { geometry: live.propertyVersions.geometry },
      committedPropertyVersions: { geometry: resized.propertyVersions.geometry } });
    await runtime.receive(resize); dom.frame(); dom.frame();
    expect(item().load.state).toBe('drawn'); expect(item().liveSample).toBeNull();
    expect(item().heldSample).toEqual(actualSample); expect(item().rect.slice(2)).toEqual([420, 300]);
    const closed = { ...resized, open: false, revision: 3, propertyVersions: { ...resized.propertyVersions, open: 2 } };
    const close = command([closed], 'held-close', 'close'); close.fence.targets[A].basePropertyVersions.open = 1;
    await runtime.receive(close);
    expect(item().load.state).toBe('empty'); expect(item().heldSample).toBeNull();
    expect(revoked).toHaveLength(1);
  });
  test('a sibling joining during held decode receives its retained frame before polling stops', async () => {
    const { runtime, fetchBytes } = await make(); const live = liveDescriptor();
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken, { 'X-Micromind-Live-State': 'held' }));
    await runtime.receive(command([live]));
    await vi.waitFor(() => expect(dom.images.filter(image => image.decodeCalls > 0)).toHaveLength(1));
    await runtime.receive(command([{ ...live, id: B }], 'held-sibling'));
    dom.images[0].finishDecode(1280, 720);
    await settleMicrotasks(); dom.frame();
    expect(item(B).load.state).toBe('loading');
    await vi.waitFor(() => expect(fetchBytes).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => expect(dom.images.filter(image => image.decodeCalls > 0)).toHaveLength(3));
    for (const image of dom.images) image.finishDecode(1280, 720);
    await settleMicrotasks(); dom.frame();
    expect(item(A).heldSample).toEqual(actualSample); expect(item(B).heldSample).toEqual(actualSample);
    expect(item(B).liveSample).toBeNull(); expect(item(B).load.state).toBe('drawn');
    await new Promise(resolve => setTimeout(resolve, 250));
    expect(fetchBytes).toHaveBeenCalledTimes(2);
  });
  test('actual live fetch/decode/paint reports its sample and closing stops owned renewal', async () => {
    const { runtime, fetchBytes, revoked } = await make(); const live = liveDescriptor();
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken));
    const message = command([live]); await runtime.receive(message);
    await vi.waitFor(() => expect(dom.images.some((image) => image.decodeCalls > 0)).toBe(true));
    expect(item().liveSample).toBeNull(); expect(result().drawRef).toBeNull();
    const image = dom.images.find((image) => image.decodeCalls > 0)!;
    image.finishDecode(1280, 720); await settleMicrotasks(); dom.frame();
    expect(item().liveSample).toEqual(actualSample);
    expect(item().load.state).toBe('drawn');
    expect(result().drawRef).toEqual(actualSample);
    backendContract(sample(7), message, ['source_exact']);
    const closed = { ...live, open: false, revision: 2, propertyVersions: { ...live.propertyVersions, open: 2 } };
    const close = command([closed], 'live-close', 'close'); close.fence.targets[A].basePropertyVersions.open = 1;
    await runtime.receive(close); const calls = fetchBytes.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(fetchBytes).toHaveBeenCalledTimes(calls);
    expect(root().hidden).toBe(true); expect(revoked.length).toBeGreaterThan(0);
  });
  test('closing during live decode cannot resurrect the retired window', async () => {
    const { runtime, fetchBytes, revoked } = await make(); const live = liveDescriptor();
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken));
    await runtime.receive(command([live]));
    await vi.waitFor(() => expect(dom.images.some((image) => image.decodeCalls > 0)).toBe(true));
    const pending = dom.images.find((image) => image.decodeCalls > 0)!;
    const closed = { ...live, open: false, revision: 2, propertyVersions: { ...live.propertyVersions, open: 2 } };
    const close = command([closed], 'live-close', 'close'); close.fence.targets[A].basePropertyVersions.open = 1;
    await runtime.receive(close); pending.finishDecode(1280, 720); await settleMicrotasks(); dom.frame();
    expect(item().liveSample).toBeNull(); expect(item().load.state).toBe('empty');
    expect(root().hidden).toBe(true); expect(revoked).toHaveLength(1);
  });
  test('two windows share one live fetch and closing one preserves its sibling', async () => {
    const { runtime, fetchBytes } = await make(); const live = liveDescriptor(), sibling = { ...live, id: B };
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken));
    await runtime.receive(command([live, sibling]));
    await vi.waitFor(() => expect(dom.images.filter((image) => image.decodeCalls > 0)).toHaveLength(2));
    expect(fetchBytes).toHaveBeenCalledTimes(1);
    for (const image of dom.images) image.finishDecode(1280, 720);
    await settleMicrotasks(); dom.frame();
    expect(item(A).liveSample).toEqual(actualSample); expect(item(B).liveSample).toEqual(actualSample);
    const closed = { ...live, open: false, revision: 2, propertyVersions: { ...live.propertyVersions, open: 2 } };
    const close = command([closed], 'live-close', 'close'); close.fence.targets[A].basePropertyVersions.open = 1;
    await runtime.receive(close);
    expect(root(A).hidden).toBe(true); expect(root(B).hidden).toBe(false);
    expect(item(B).load.state).toBe('drawn'); expect(item(B).liveSample).toEqual(actualSample);
    await vi.waitFor(() => expect(fetchBytes.mock.calls.length).toBe(2));
    expect(root(A).hidden).toBe(true);
  });
  test('a retired sibling decode cannot block or poison the remaining live window', async () => {
    const { runtime, fetchBytes } = await make(); const live = liveDescriptor(), sibling = { ...live, id: B };
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken));
    await runtime.receive(command([live, sibling]));
    await vi.waitFor(() => expect(dom.images.filter((image) => image.decodeCalls > 0)).toHaveLength(2));
    const [retired, kept] = dom.images;
    const closed = { ...live, open: false, revision: 2, propertyVersions: { ...live.propertyVersions, open: 2 } };
    const close = command([closed], 'live-close', 'close'); close.fence.targets[A].basePropertyVersions.open = 1;
    await runtime.receive(close); kept.finishDecode(1280, 720); await settleMicrotasks(); dom.frame();
    expect(item(B).load.state).toBe('drawn');
    await vi.waitFor(() => expect(fetchBytes.mock.calls.length).toBe(2));
    retired.failDecode(); await settleMicrotasks(); dom.frame();
    expect(item(B).load.state).toBe('drawn'); expect(item(B).liveSample).toEqual(actualSample);
  });
  test('the real Retry control resumes a failed live image acquisition', async () => {
    const { runtime, fetchBytes } = await make(); const live = liveDescriptor();
    fetchBytes.mockImplementationOnce(async () => new Response(null, { status: 503 }))
      .mockImplementation(async () => response(live.delivery.sourceToken));
    await runtime.receive(command([live]));
    await vi.waitFor(() => expect(item().load.state).toBe('error'));
    fire(root().querySelector('[data-control="retry"]')!, 'click');
    await vi.waitFor(() => expect(dom.images.some((image) => image.decodeCalls > 0)).toBe(true));
    dom.images.find((image) => image.decodeCalls > 0)!.finishDecode(1280, 720);
    await settleMicrotasks(); dom.frame();
    expect(item().load.state).toBe('drawn'); expect(item().liveSample).toEqual(actualSample);
  });
  test.each([
    ['another lease', { 'X-Micromind-Source-Token': 'other-token' }],
    ['another run', { 'X-Micromind-Live-Sample': JSON.stringify({ ...actualSample, run: 'other-run' }) }],
    ['different served bytes', { 'X-Micromind-Content-Sha256': WRONG_DIGEST }],
  ])('live delivery refuses %s before decoding or claiming paint', async (_name, changed) => {
    const { runtime, fetchBytes } = await make(); const live = liveDescriptor();
    fetchBytes.mockImplementation(async () => response(live.delivery.sourceToken, changed));
    await runtime.receive(command([live]));
    await vi.waitFor(() => expect(item().load.state).toBe('error'));
    expect(item().liveSample).toBeNull(); expect(result().drawRef).toBeNull();
    expect(dom.images.filter((image) => image.decodeCalls > 0)).toHaveLength(0);
  });
});

test('the real Fit button crosses Runtime validation with size and original ownership', async () => {
  const { sent } = await manualSequenceWorld();
  const target = dom.host.querySelectorAll('.workspace-window')[0];
  const fit = target.querySelector('[data-control="zoom-fit"]')!;
  fit.dispatchEvent(new Event('click'));
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ op: 'zoom', values: { fit: true, size: expect.any(Array) }, targets: [A] });
});

test('full retained findings render from verified bytes and reject a changed display payload', async () => {
  const findings = { title: 'Retained study', summary: 'Same preview',
    findingRevision: { version: 2, digest: 'recorded', complete: true },
    report: { methods: 'Ten original captures', measurements: 'Four cells', limitations: 'Uncalibrated' } };
  const bytes = new TextEncoder().encode(JSON.stringify(findings));
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), n => n.toString(16).padStart(2, '0')).join('');
  const { runtime, fetchBytes } = await make(bytes);
  fetchBytes.mockImplementation(async () => new Response(bytes, { headers: { 'Content-Type': 'application/vnd.micromind.findings+json' } }));
  const work = descriptor();
  Object.assign(work.sourceRef, { kind: 'written', media: 'written', digest });
  Object.assign(work.delivery, { report: findings, mediaType: 'application/vnd.micromind.findings+json',
    payloadBinding: { servedDigest: digest, originalDigest: digest, transforms: [] } });
  await runtime.receive(command([work]));
  dom.frame();
  expect(root().textContent).toContain('Four cells');
  expect(root().textContent).toContain('revision 2');
  expect(item().load.state).toBe('drawn');
  const changed = structuredClone(work);
  changed.id = B;
  Object.assign(changed.delivery, { sourceToken: 'other-token', url: '/api/presentation-sources/other-token',
    report: { ...findings, summary: 'Invented replacement' } });
  await expect(runtime.receive(command([changed], 'tampered'))).rejects.toThrow('Written content differs');
});

test('Group with a named window sends both authoritative fences without guessing a target', async () => {
  const { runtime, sent } = await make();
  const first = descriptor(A), second = descriptor(B);
  first.title = 'Original capture'; second.title = 'Reference photo';
  for (const value of [first, second]) {
    Object.assign(value.propertyVersions, { group: 1 });
    Object.assign(value.propertyOwners, { group: 'request-a' });
  }
  await runtime.receive(command([first, second]));
  await decode(A); await decode(B); dom.frame();
  fire(root(A).querySelector('.workspace-window-options')!, 'click');
  const group = root(A).querySelector('[data-control="group"]');
  expect(group).not.toBeNull();
  expect(sent).toEqual([]);
  expect(group!.querySelectorAll('option').map(option => option.value)).toEqual(['', B]);
  group!.value = B;
  fire(group!, 'change');
  expect(sent).toHaveLength(1);
  expect(sent[0]).toMatchObject({ schema: 'presentation-manual-v2', op: 'group', targets: [A, B], values: {},
    client, base: { [A]: { sourceGeneration: 1, lifetimeGeneration: life, propertyVersions: first.propertyVersions },
      [B]: { sourceGeneration: 1, lifetimeGeneration: life, propertyVersions: second.propertyVersions } } });
  expect(group!.value).toBe('');
  expect(item(A).view.group ?? null).toBeNull(); // only admitted rendering may claim a group
  expect(item(B).view.group ?? null).toBeNull();
});

test('manual grouping rejects stale or foreign companion state before sending anything', async () => {
  const { runtime, sent } = await make();
  const first = descriptor(A), second = descriptor(B);
  for (const value of [first, second]) {
    Object.assign(value.propertyVersions, { group: 1 });
    Object.assign(value.propertyOwners, { group: 'request-a' });
  }
  await runtime.receive(command([first, second]));
  await decode(A); await decode(B); dom.frame();
  const base = (value: Descriptor) => ({ sourceGeneration: value.sourceGeneration,
    lifetimeGeneration: value.lifetimeGeneration, propertyVersions: { ...value.propertyVersions } });
  const manual: ManualWindowCommand = { type: 'present', action: 'group', instance: A, values: {}, context,
    requestId: 'group-test', commandId: 'group-test-command', actionOccurrenceId: 'group-test-action',
    base: base(first), companions: [{ instance: B, base: base(second) }] };
  const stale = structuredClone(manual); stale.companions![0].base.sourceGeneration++;
  expect(() => runtime.manual(stale)).toThrow('authoritative base');
  const foreign = structuredClone(manual); foreign.companions![0].instance = 'unregistered-window';
  expect(() => runtime.manual(foreign)).toThrow('authoritative base');
  const duplicate = structuredClone(manual); duplicate.companions![0] = { instance: A, base: base(first) };
  expect(() => runtime.manual(duplicate)).toThrow('distinct windows');
  const wrongBrowser = structuredClone(manual); wrongBrowser.context.clientId = 'other-browser';
  expect(() => runtime.manual(wrongBrowser)).toThrow();
  expect(sent).toEqual([]);
});

test('native retained report capture controls keep exact revision, preview and parent identity', async () => {
  const findings = { title: 'Retained study', summary: 'Original evidence',
    findingRevision: {version: 2, digest: 'saved-finding', complete: true},
    captures: [{title:'Clip',kind:'clip',state:'retained',summary:'Original frames',at:1000,
      source:{day:'2026-10-08',id:'acq-original',version:null,contentRevision:'read'},
      frames:[{picture:0,seq:17},{picture:1,seq:18}]}] };
  const bytes = new TextEncoder().encode(JSON.stringify(findings));
  const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)), n=>n.toString(16).padStart(2,'0')).join('');
  const openCapture = vi.fn(async()=>{});
  const {runtime,fetchBytes} = await make(bytes,openCapture);
  fetchBytes.mockImplementation(async()=>new Response(bytes,{headers:{'Content-Type':'application/vnd.micromind.findings+json'}}));
  const work = descriptor();
  Object.assign(work.sourceRef,{kind:'written',media:'written',digest,
    identity:{resource:{day:'2026-10-08',id:'r9',version:2,picture:null}}});
  Object.assign(work.delivery,{report:findings,mediaType:'application/vnd.micromind.findings+json',
    payloadBinding:{servedDigest:digest,originalDigest:digest,transforms:[]}});
  await runtime.receive(command([work])); dom.frame();
  const select = root().querySelectorAll('select').find(node => node.getAttribute('aria-label') === 'Frame from Clip')!;
  select.value='1'; fire(select,'change');
  const preview = root().querySelector('img')!;
  if (!(preview instanceof TestImage)) throw new Error('Original capture preview is missing');
  expect(preview.src).toContain('/api/research/r9/findings/2/captures/0/1?day=2026-10-08&digest=saved-finding');
  const button = root().querySelector('[data-capture-open]')!;
  fire(button,'click'); await settleMicrotasks();
  expect(openCapture).toHaveBeenCalledExactlyOnceWith({day:'2026-10-08',rid:'r9',findingVersion:2,
    findingDigest:'saved-finding',sequence:0,picture:1},A);
});

test.each([[148,176,300,244],[1280,100,360,224]])('initial beside placement is settled before %ix%i image first draws', async (pixelWidth,pixelHeight,width,height) => {
  const {runtime} = await make();
  const parent = descriptor(A);
  await runtime.receive(command([parent])); await decode(A); dom.frame();
  const original = root(A).getBoundingClientRect();
  const child = descriptor(B);
  Object.assign(child.geometryIntent,{position:[original.right+12,original.top],size:[width,height],
    relation:{kind:'beside',reference:A,gapPx:12,align:'top',side:'right'}});
  child.delivery.sourceToken = 'captured-frame-token'; child.delivery.url='/api/presentation-sources/captured-frame-token';
  await runtime.receive(command([child],'initial-beside'));
  expect(root(B).dataset.staged).toBe('true');
  const beforeDecode = root(B).getBoundingClientRect();
  await settleMicrotasks();
  const image = root(B).querySelector('img');
  if (!(image instanceof TestImage)) throw new Error('Prepared image is unavailable');
  image.finishDecode(pixelWidth,pixelHeight); await settleMicrotasks(); dom.frame();
  const painted = root(B).getBoundingClientRect();
  expect([painted.left,painted.top,painted.width,painted.height]).toEqual([original.right+12,original.top,width,height]);
  expect([painted.left,painted.top,painted.width,painted.height]).toEqual([beforeDecode.left,beforeDecode.top,beforeDecode.width,beforeDecode.height]);
  const unchanged = root(A).getBoundingClientRect();
  expect([unchanged.left,unchanged.top,unchanged.width,unchanged.height]).toEqual([original.left,original.top,original.width,original.height]);
  expect(item(B).load.state).toBe('drawn');
});


// Crowded retained workspaces must keep the real interaction in a bounded report.
function crowdedSurfaces(count: number) {
  const nodes = Array.from({length: count}, (_, index) => {
    const root = dom.document.createElement('section'); dom.host.append(root);
    return {itemId: `fixed-${index}`, root: root as unknown as HTMLElement, controls: []};
  });
  const input: WorkspaceStructuralInput = {
    items: nodes.map(({itemId}) => ({id:itemId, rootId:itemId, workspaceId:context.workspaceId,
      kind:'scientific', lifetimeGeneration:life, sourceGeneration:1, revision:1,
      open:true, connected:true, visible:true, space:'workspace',
      view:{legacy:{schema:'studio-surface-v1', scope:'dom-observation'}}, controls:[],
      propertyVersions:{}, propertyOwners:{}, load:{state:'unknown',unknown:['synthetic']}, unknown:[]})),
    nodes, targets:nodes.map(({itemId}) => ({id:`target:surface:${itemId}`, type:'presentation',
      presentationId:itemId, basis:'registered-surface'})), selected:[], complete:false, omittedItems:0, unknown:[],
  };
  return input;
}
test.each([17, 41])('current physical hit survives %i registered surfaces without raising report limits', async count => {
  const surfaces = crowdedSurfaces(count);
  await make(ABC, undefined, () => surfaces);
  const previous = surfaces.nodes[0].root as unknown as TestElement;
  observe(previous, 'pointermove', {pointerId:1,pointerType:'mouse',isPrimary:true,clientX:10,clientY:10,buttons:0});
  // A window can move under a stationary pointer between the event and Send.
  const current = surfaces.nodes.at(-1)!;
  Object.assign(dom.document, {elementFromPoint: () => current.root});
  const report = sample();
  expect(report.items.length).toBeLessThanOrEqual(40);
  expect(report.targets.length).toBeLessThanOrEqual(16);
  expect(report.interaction.pointer.presentationId).toBe(current.itemId);
  expect(report.targets.some(target => target.presentationId === current.itemId)).toBe(true);
  expect(report.items.some(item => item.id === current.itemId)).toBe(true);
  expect(surfaces.items).toHaveLength(count);
});


test('keyboard focus beyond the report bounds is preserved without a pointer', async () => {
  const surfaces = crowdedSurfaces(41);
  await make(ABC, undefined, () => surfaces);
  const current = surfaces.nodes.at(-1)!;
  Object.assign(dom.document, {activeElement: current.root});
  const report = sample();
  expect(report.interaction.focus.presentationId).toBe(current.itemId);
  expect(report.items).toHaveLength(40);
  expect(report.targets).toHaveLength(16);
  expect(report.targets.some(target => target.presentationId === current.itemId)).toBe(true);
});

test('mandatory selected targets exceeding the existing limit remain refused', async () => {
  const surfaces = crowdedSurfaces(17);
  surfaces.selected = surfaces.targets.map(target => target.id);
  await make(ABC, undefined, () => surfaces);
  expect(() => sample()).toThrow('TARGET_NOT_REPRESENTABLE: registered candidate bound exceeded');
});
