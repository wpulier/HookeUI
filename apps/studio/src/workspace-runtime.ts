import { LiveWindowDelivery } from './live-window-delivery';
import { workspaceForeground } from './workspace-foreground';
import type { ManualWindowCommand, WindowContext, WindowSource, WindowState, WorkspaceWindows } from './workspace-windows';
import { createInteractionState, reduceInteraction, snapshotInteraction, workspaceToSource } from './workspace-observation';
import type { FocusFacts, HitFacts, InteractionEvent } from './workspace-observation';
import type { StudioControlFact, StudioSurfaceLegacy } from './screen';

type Client = Omit<WindowContext, 'appRunId'>;
type Fact = ReturnType<WorkspaceWindows['facts']>[number];
type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type RecordValue = Record<string, unknown>;
type Versions = Record<string, number>;
interface Delivery {
  sourceToken: string; url: string; payloadBinding: RecordValue;
  disc?: number[] | null; originalSize?: number[]; transform?: number[]; mediaType?: string; text?: string;
  report?: WindowState['delivery']['report'];
}
interface Descriptor extends Omit<WindowState, 'source' | 'delivery'> { sourceRef: WindowSource; delivery: Delivery }
interface TargetFence {
  lifetimeGeneration: string; sourceGeneration: number; committedRevision: number;
  basePropertyVersions: Versions; committedPropertyVersions: Versions; owner: string;
  sourceRef: WindowSource; sourceToken: string; payloadBinding: RecordValue; baseViewDrawRef?: string | null;
}
interface Command {
  type: 'present'; schema: 'presentation-command-v2'; op: string;
  commandId: string; requestId: string; actionId: string; client: Client; appRunId: string;
  targets: string[]; instances: Descriptor[];
  fence: { requestId: string; actionId: string; commandId: string; client: Client; appRunId: string;
    afterReportSeq: number; issuedMono: number; deadlineMono: number; targets: Record<string, TargetFence> };
}
interface NativeDelivery { signature: string; url: string; binding: RecordValue }
interface Occurrence {
  requestId: string; actionId: string; commandId: string; appRunId: string; presentationId: string;
  op: string; lifetimeGeneration: string; sourceGeneration: number; committedRevision: number;
  committedPropertyVersions: Versions; owner: string; sourceToken: string; source: WindowSource;
  retainUntilMs: number; // local monotonic confirmation window, never server-clock alignment
  payloadBinding: RecordValue; baseViewDrawRef: string | null; error?: { code: string; detail: string };
}
export interface WorkspaceSample { seq: number; nowMs: number; selected: readonly string[] }
export interface WorkspaceViewport {
  layoutSize: number[]; visualRect: number[]; visualScale: number; documentScroll: number[];
  dpr: number; visible: boolean; documentFocused: boolean; activeSurface: string;
  presentationArea?: number[] | null; // actual visible native host, in viewport CSS pixels
}
/** Observed fixed UI only; no native source, commit, paint or result authority. */
export interface WorkspaceStructuralItem {
  id: string; rootId: string; workspaceId: string; kind: string;
  lifetimeGeneration: string; sourceGeneration: number; revision: number;
  open: boolean; connected: boolean; visible: boolean; rect?: number[]; z?: number; space: string;
  view: { legacy: StudioSurfaceLegacy }; controls: StudioControlFact[];
  propertyVersions: Record<string, never>; propertyOwners: Record<string, never>;
  load: { state: 'unknown'; unknown: string[] }; unknown: string[];
  scientificPaint?: import('./scientific-presentation').ScientificPaintObservation | null;
  scientificPanelPaint?: import('./scientific-presentation').ScientificPaintObservation | null;
}
export interface WorkspaceStructuralTarget {
  id: string; type: string; basis: string; presentationId?: string;
  outlineId?: string; populationId?: string; memberId?: string; revision?: number; memberRevision?: number;
}
export interface WorkspaceStructuralInput {
  items: WorkspaceStructuralItem[];
  nodes: { itemId: string; root: HTMLElement; controls: readonly HTMLElement[] }[];
  targets: WorkspaceStructuralTarget[]; selected: readonly string[];
  complete: false; omittedItems: number; unknown: readonly string[];
}
type DisplayFact = Fact | WorkspaceStructuralItem;
interface InteractionRegistry {
  native: Fact[]; facts: DisplayFact[]; structural: WorkspaceStructuralInput | undefined;
  nodes: Map<string, { root: HTMLElement; controls: readonly HTMLElement[]; viewport?: HTMLElement }>;
}
function nativeFact(item: DisplayFact): item is Fact { return 'sourceKey' in item; }
export interface WorkspaceRuntimeOptions {
  /** Trusted accepted handshake and scoped fetch are supplied by the connection owner. */
  context: WindowContext; host: HTMLElement; windows: WorkspaceWindows;
  foregroundHost?: HTMLElement;
  send(command: unknown): void; fetch: typeof fetch;
  createObjectURL(blob: Blob): string; revokeObjectURL(url: string): void;
  viewport(): WorkspaceViewport; now(): number;
  /** Same interaction owner, using real registered fixed roots/control nodes. */
  structural?(): WorkspaceStructuralInput;
}

const CLIENT_KEYS = ['workspaceId', 'clientId', 'pageEpoch', 'connectionEpoch'];
const PROPERTIES = ['geometry', 'source', 'open', 'zoom', 'pan', 'front', 'title', 'opacity', 'hidden', 'group'];
const OPS = ['open', 'duplicate', 'close', 'move', 'resize', 'zoom', 'pan', 'front', 'arrange', 'split', 'opacity', 'hide', 'restore', 'group', 'ungroup'];
const SOURCE_KINDS = ['live', 'saved-image', 'composite', 'video', 'lesson', 'written'];
const MEDIA = ['raw', 'derived', 'annotated', 'external-reference', 'capture', 'labelled', 'reference', 'generated', 'lesson', 'written', 'unknown'];
const MAX_BYTES = 10 * 1024 * 1024; // Prepared native delivery bound; not Screen capture's smaller bound.
const MAX_RESULTS = 40;
const MAX_COMMANDS = 256;
const MAX_PENDING = 8;
function failed(code: string, detail: string): Error & { code: string; payloadBinding?: RecordValue } {
  return Object.assign(new Error(detail), { code });
}
function record(value: unknown): RecordValue {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw failed('invalid_command', 'Expected a record');
  return value as RecordValue;
}
function fields(value: RecordValue, required: string[], optional: string[] = []): void {
  if (required.some((key) => !Object.hasOwn(value, key)) || Object.keys(value).some((key) => !required.includes(key) && !optional.includes(key))) {
    throw failed('invalid_command', 'Missing or unrecognized command fields');
  }
}
function id(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 256) throw failed('invalid_command', 'Expected a bounded identity');
  return value;
}
function counter(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) throw failed('invalid_command', 'Expected a safe nonnegative revision');
  return value;
}
function numeric(value: unknown): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) throw failed('invalid_command', 'Expected a finite number');
  return value;
}
function vector(value: unknown, length: number): number[] {
  if (!Array.isArray(value) || value.length !== length) throw failed('invalid_command', 'Invalid coordinate dimensions');
  return value.map(numeric);
}
function uuid(value: unknown, window = false): string {
  const result = id(value);
  const raw = window && result.startsWith('w-') ? result.slice(2) : result;
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(raw)) throw failed('invalid_command', 'Expected an opaque lifetime or window UUID');
  return result;
}
function versionMap(value: unknown): Versions {
  const result: Versions = {};
  for (const [key, revision] of Object.entries(record(value))) {
    if (!PROPERTIES.includes(key)) throw failed('invalid_command', 'Unknown authoritative property');
    result[key] = counter(revision);
  }
  return result;
}
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (value !== null && typeof value === 'object') return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical((value as RecordValue)[key])}`).join(',')}}`;
  const encoded = JSON.stringify(value);
  if (encoded === undefined) throw failed('invalid_command', 'Cannot compare non-JSON facts');
  return encoded;
}
/** Detach only bounded plain JSON. No DOM/editor/event objects enter a command or snapshot. */
function jsonCopy<T>(value: T): T {
  let nodes = 0;
  function visit(input: unknown, depth: number): Json {
    if (++nodes > 20000 || depth > 24) throw failed('invalid_command', 'Command nesting or node limit');
    if (input === null || typeof input === 'boolean') return input;
    if (typeof input === 'string') { if (input.length > 65536) throw failed('invalid_command', 'Command text limit'); return input; }
    if (typeof input === 'number' && Number.isFinite(input) && (!Number.isInteger(input) || Number.isSafeInteger(input))) return input;
    if (Array.isArray(input)) {
      if (input.length > 20000 || Object.getOwnPropertySymbols(input).length || Object.keys(input).length !== input.length) throw failed('invalid_command', 'Command array is not bounded dense JSON');
      return Array.from({ length: input.length }, (_, index) => {
        const descriptor = Object.getOwnPropertyDescriptor(input, String(index));
        if (!descriptor || !Object.hasOwn(descriptor, 'value')) throw failed('invalid_command', 'Command array accessors are not JSON');
        return visit(descriptor.value, depth + 1);
      });
    }
    if (input && typeof input === 'object' && (Object.getPrototypeOf(input) === Object.prototype || Object.getPrototypeOf(input) === null)) {
      if (Object.getOwnPropertySymbols(input).length) throw failed('invalid_command', 'Command symbols are not JSON');
      const pairs = Object.entries(Object.getOwnPropertyDescriptors(input)).map(([key, descriptor]) => {
        if (!descriptor.enumerable || !Object.hasOwn(descriptor, 'value')) throw failed('invalid_command', 'Command accessors are not JSON');
        return [key, visit(descriptor.value, depth + 1)];
      });
      return Object.fromEntries(pairs);
    }
    throw failed('invalid_command', 'Command contains non-JSON facts');
  }
  const copied = visit(value, 0);
  if (new TextEncoder().encode(JSON.stringify(copied)).byteLength > 2 * 1024 * 1024) throw failed('invalid_command', 'Command wire limit');
  return copied as T;
}
function freeze<T>(value: T): T {
  if (value && typeof value === 'object') { for (const child of Object.values(value)) freeze(child); Object.freeze(value); }
  return value;
}
function checkSource(value: unknown, depth = 0): WindowSource {
  const source = record(value);
  fields(source, ['key', 'kind', 'media', 'availability', 'identity', 'provenance'], ['components', 'digest', 'locator']);
  id(source.key);
  if (!SOURCE_KINDS.includes(id(source.kind)) || !MEDIA.includes(id(source.media)) || !['retained', 'temporary', 'missing', 'earlier-run', 'unknown'].includes(id(source.availability))) throw failed('invalid_command', 'Unknown canonical source class');
  const identity = record(source.identity); record(source.provenance);
  if (identity.resource != null) {
    const resource = record(identity.resource); id(resource.day); id(resource.id);
    for (const key of ['version', 'picture']) { if (!Object.hasOwn(resource, key)) throw failed('invalid_command', 'Unknown resource revision must be explicit'); if (resource[key] !== null) counter(resource[key]); }
  }
  if (source.components !== undefined) {
    if (depth >= 8 || !Array.isArray(source.components) || source.components.length > 8) throw failed('invalid_command', 'Source component limit');
    source.components.forEach((component) => checkSource(component, depth + 1));
  }
  for (const key of ['digest', 'locator']) if (source[key] != null) id(source[key]);
  return source as unknown as WindowSource;
}
function descriptor(value: unknown, workspace: string): Descriptor {
  const item = record(value);
  fields(item, ['id', 'workspaceId', 'sourceRef', 'sourceGeneration', 'lifetimeGeneration', 'revision', 'open', 'title', 'geometryIntent', 'view', 'propertyVersions', 'propertyOwners', 'delivery'], ['staged']);
  uuid(item.id, true); uuid(item.lifetimeGeneration);
  if (item.workspaceId !== workspace) throw failed('context_mismatch', 'Descriptor belongs to another workspace');
  counter(item.sourceGeneration); counter(item.revision); const source = checkSource(item.sourceRef);
  if (typeof item.open !== 'boolean' || item.staged !== undefined && typeof item.staged !== 'boolean' || typeof item.title !== 'string' || item.title.length > 1024) throw failed('invalid_command', 'Invalid descriptor display facts');
  const geometry = record(item.geometryIntent); fields(geometry, ['position', 'size'], ['relation']); vector(geometry.position, 2);
  if (geometry.size !== null && vector(geometry.size, 2).some((number) => number <= 0)) throw failed('invalid_command', 'Nonpositive window size');
  if (geometry.relation != null) {
    const relation = record(geometry.relation); fields(relation, ['kind'], ['reference', 'gapPx', 'align', 'side']); id(relation.kind);
    if (relation.reference != null) uuid(relation.reference, true);
    if (relation.gapPx != null && numeric(relation.gapPx) < 0) throw failed('invalid_command', 'Negative arrangement gap');
    if (relation.align != null) id(relation.align);
    if (relation.side !== undefined && !['left', 'right'].includes(relation.side as string)) throw failed('invalid_command', 'Arrangement side must be left or right');
  }
  const view = record(item.view); fields(view, ['zoom', 'pan'], ['opacity', 'hidden', 'group']);
  if (view.opacity !== undefined && (numeric(view.opacity) < 0 || numeric(view.opacity) > 1) || view.hidden !== undefined && typeof view.hidden !== 'boolean' || view.group != null && typeof view.group !== 'string') throw failed('invalid_command', 'Invalid window visibility or group');
  if (numeric(view.zoom) <= 0 || vector(view.pan, 2).some((number) => number < 0 || number > 1)) throw failed('invalid_command', 'Invalid source view');
  const versions = versionMap(item.propertyVersions), owners = record(item.propertyOwners);
  if (canonical(Object.keys(versions).sort()) !== canonical(Object.keys(owners).sort())) throw failed('invalid_command', 'Ownership and version properties differ');
  Object.values(owners).forEach((owner) => { if (owner !== null) id(owner); });
  const delivery = record(item.delivery); fields(delivery, ['sourceToken', 'url', 'payloadBinding'], ['disc', 'originalSize', 'transform', 'mediaType', 'text', 'report']);
  if (delivery.text !== undefined && (source.kind !== 'written' || typeof delivery.text !== 'string')) throw failed('invalid_command', 'Text belongs only to a written source');
  if (delivery.report !== undefined && (source.kind !== 'written' || !delivery.report || typeof delivery.report !== 'object' || Array.isArray(delivery.report))) throw failed('invalid_command', 'A report belongs only to a written source');
  const token = id(delivery.sourceToken);
  if (!/^[A-Za-z0-9_-]+$/.test(token) || delivery.url !== `/api/presentation-sources/${token}`) throw failed('source_mismatch', 'Source URL is not its native token route');
  const binding = record(delivery.payloadBinding);
  if (source.kind !== 'live' && (typeof binding.servedDigest !== 'string' || !/^[a-f0-9]{64}$/.test(binding.servedDigest))) throw failed('source_mismatch', 'Prepared served digest is unavailable');
  if (binding.originalDigest != null && (typeof binding.originalDigest !== 'string' || !/^[a-f0-9]{64}$/.test(binding.originalDigest))) throw failed('source_mismatch', 'Invalid original digest');
  if (delivery.disc != null) vector(delivery.disc, 3);
  if (delivery.originalSize !== undefined && vector(delivery.originalSize, 2).some((number) => number <= 0)) throw failed('source_mismatch', 'Invalid prepared image size');
  // Native bytes are unchanged. Nonidentity delivery needs a separately adopted renderer mapping.
  if (delivery.transform !== undefined && canonical(vector(delivery.transform, 6)) !== '[1,0,0,1,0,0]') throw failed('source_mismatch', 'Non-native delivery transform is not installed');
  if (delivery.mediaType !== undefined) id(delivery.mediaType);
  return item as unknown as Descriptor;
}

/** Adapts accepted transport to existing windows and their physical observations. No scheduler. */
export class WorkspaceRuntime {
  private readonly options: WorkspaceRuntimeOptions;
  private readonly context: WindowContext;
  private readonly client: Client;
  private readonly events = new AbortController();
  private deliveries = new AbortController();
  private interaction = createInteractionState();
  private readonly commands = new Map<string, { signature: string; pending: Promise<void> | null; failure?: unknown }>();
  private readonly native = new Map<string, NativeDelivery>();
  private readonly live: LiveWindowDelivery;
  private readonly preparing = new Map<string, { signature: string; promise: Promise<NativeDelivery> }>();
  private readonly results = new Map<string, Occurrence>();
  private readonly physicalTargets = new Map<number, EventTarget | null>();
  private focusedTarget: EventTarget | null = null;
  private disposed = false;
  private pending = 0;
  private layoutKey = '';
  private hiddenAtMs: number | null = null; // since when the studio has been in the background (another app or tab in front)
  private layoutRevision = 0;
  private manualSeq = 0;

  constructor(options: WorkspaceRuntimeOptions) {
    this.live = new LiveWindowDelivery({ ...options, run: options.context.appRunId });
    this.context = freeze(jsonCopy(options.context)); this.options = { ...options, context: this.context };
    fields(record(this.context), [...CLIENT_KEYS, 'appRunId']); Object.values(this.context).forEach(id);
    this.client = freeze({ workspaceId: this.context.workspaceId, clientId: this.context.clientId, pageEpoch: this.context.pageEpoch, connectionEpoch: this.context.connectionEpoch });
    const listen = (type: string, handler: EventListener) => options.host.addEventListener(type, handler, { capture: true, signal: this.events.signal });
    for (const [dom, type] of [['pointermove', 'pointer-move'], ['pointerdown', 'pointer-down']] as const) listen(dom, (event) => this.pointer(event as PointerEvent, type));
    for (const [dom, type] of [['pointerup', 'pointer-up'], ['pointercancel', 'pointer-cancel'], ['pointerleave', 'pointer-leave']] as const) {
      listen(dom, (event) => { const pointer = event as PointerEvent; this.reduce({ type, pointerId: pointer.pointerId, atMs: this.options.now() }); });
    }
    listen('focusin', (event) => { this.focusedTarget = event.target; this.reduce({ type: 'focus', focus: this.focus(event.target), atMs: this.options.now() }); });
    document.addEventListener('visibilitychange', () => this.reduce({ type: 'visibility', visible: this.options.viewport().visible, atMs: this.options.now() }), { signal: this.events.signal });
  }
  private active(): void { if (this.disposed) throw failed('context_mismatch', 'Workspace runtime connection has been disposed'); }
  private checkClient(value: unknown, run: unknown): void {
    const candidate = record(value);
    if (canonical(Object.keys(candidate).sort()) !== canonical(CLIENT_KEYS.slice().sort()) || CLIENT_KEYS.some((key) => candidate[key] !== this.client[key as keyof Client]) || run !== this.context.appRunId) throw failed('context_mismatch', 'Command does not match the accepted connection and run');
  }
  private parse(value: unknown): Command {
    const message = record(jsonCopy(value));
    this.checkClient(message.client, message.appRunId);
    fields(message, ['type', 'schema', 'op', 'commandId', 'requestId', 'actionId', 'client', 'appRunId', 'targets', 'instances', 'fence']);
    if (message.type !== 'present' || message.schema !== 'presentation-command-v2' || !OPS.includes(id(message.op))) throw failed('invalid_command', 'Unknown presentation command');
    for (const key of ['commandId', 'requestId', 'actionId']) id(message[key]);
    if (!Array.isArray(message.targets) || !Array.isArray(message.instances) || !message.targets.length || message.targets.length > 8 || message.targets.length !== message.instances.length) throw failed('invalid_command', 'Command participant limit or mismatch');
    const targets = message.targets.map((target) => uuid(target, true));
    if (new Set(targets).size !== targets.length) throw failed('invalid_command', 'Duplicate command target');
    const items = message.instances.map((item) => descriptor(item, this.context.workspaceId));
    if (items.some((item, index) => item.id !== targets[index])) throw failed('invalid_command', 'Ordered descriptors and targets disagree');
    const fence = record(message.fence); fields(fence, ['requestId', 'actionId', 'commandId', 'appRunId', 'client', 'afterReportSeq', 'issuedMono', 'deadlineMono', 'targets']);
    this.checkClient(fence.client, fence.appRunId);
    if (['requestId', 'actionId', 'commandId'].some((key) => fence[key] !== message[key])) throw failed('command_mismatch', 'Fence names a different occurrence');
    counter(fence.afterReportSeq);
    if (numeric(fence.issuedMono) < 0 || numeric(fence.deadlineMono) < numeric(fence.issuedMono)) throw failed('invalid_command', 'Invalid server fence interval');
    const leases = record(fence.targets);
    if (canonical(Object.keys(leases).sort()) !== canonical(targets.slice().sort())) throw failed('invalid_command', 'Fence participants disagree');
    for (const item of items) {
      const lease = record(leases[item.id]);
      fields(lease, ['lifetimeGeneration', 'sourceGeneration', 'committedRevision', 'basePropertyVersions', 'committedPropertyVersions', 'owner', 'sourceRef', 'sourceToken', 'payloadBinding'], ['baseViewDrawRef']);
      if (lease.baseViewDrawRef != null) id(lease.baseViewDrawRef);
      const base = versionMap(lease.basePropertyVersions), committed = versionMap(lease.committedPropertyVersions); id(lease.owner);
      if (!Object.keys(committed).length || canonical(Object.keys(base).sort()) !== canonical(Object.keys(committed).sort()) || Object.keys(committed).some((key) => base[key] > committed[key] || item.propertyVersions[key] !== committed[key] || item.propertyOwners[key] !== lease.owner)) throw failed('command_mismatch', 'Touched version or owner fence differs from the committed descriptor');
      if (lease.lifetimeGeneration !== item.lifetimeGeneration || lease.sourceGeneration !== item.sourceGeneration || lease.committedRevision !== item.revision || lease.sourceToken !== item.delivery.sourceToken || canonical(lease.sourceRef) !== canonical(item.sourceRef) || canonical(lease.payloadBinding) !== canonical(item.delivery.payloadBinding)) throw failed('source_mismatch', 'Source, payload or lifetime fence differs from its descriptor');
    }
    return freeze(message as unknown as Command);
  }
  async receive(value: unknown): Promise<void> {
    this.active(); const command = this.parse(value), signature = canonical(command);
    const previous = this.commands.get(command.commandId);
    if (previous) {
      if (previous.signature !== signature) throw failed('command_mismatch', 'An original command cannot change on replay');
      if (previous.pending) await previous.pending;
      if (Object.hasOwn(previous, 'failure')) throw previous.failure;
      return;
    }
    if (this.pending >= MAX_PENDING) throw failed('source_unavailable', 'Native delivery preparations are busy');
    const retained: { signature: string; pending: Promise<void> | null; failure?: unknown } = { signature, pending: null };
    this.commands.set(command.commandId, retained); this.pending++;
    retained.pending = this.apply(command);
    try { await retained.pending; }
    catch (cause) { retained.failure = cause; throw cause; }
    finally {
      this.pending--; retained.pending = null;
      while (this.commands.size > MAX_COMMANDS) {
        const evictable = [...this.commands].find(([, entry]) => entry.pending === null);
        if (!evictable) break;
        this.commands.delete(evictable[0]);
      }
    }
  }
  private async apply(command: Command): Promise<void> {
    const signal = this.deliveries.signal;
    let current: Descriptor | null = null;
    let renderingStarted = false;
    const deliveries = new Map<string, NativeDelivery>();
    try {
      for (const item of command.instances) {
        current = item;
        if (item.open) deliveries.set(item.id, await this.prepare(item, signal));
      }
      this.activeDelivery(signal);
      const items: WindowState[] = command.instances.map(({ sourceRef, delivery, ...state }) => ({ ...state, source: sourceRef,
        delivery: { ...delivery, url: deliveries.get(state.id)?.url ?? delivery.url, payloadBinding: deliveries.get(state.id)?.binding ?? delivery.payloadBinding } }));
      renderingStarted = true;
      this.options.windows.apply({ context: this.context, requestId: command.requestId, commandId: command.commandId, actionOccurrenceId: command.actionId, op: command.op, items });
      for (const item of command.instances) this.keepResult(command, item, deliveries.get(item.id)?.binding ?? item.delivery.payloadBinding);
      this.live.sync(items);
    } catch (cause) {
      if (signal.aborted && !this.disposed) return; // Project reset retired this occurrence, not a new failure.
      if (!this.disposed) {
        const error = cause instanceof Error ? cause : failed('source_unavailable', 'Native delivery failed');
        const detail = error.message, code = 'code' in error && typeof error.code === 'string' ? error.code : 'source_unavailable';
        for (const item of command.instances) {
          const affected = current?.id === item.id;
          const binding = affected && 'payloadBinding' in error && error.payloadBinding ? record(error.payloadBinding) : deliveries.get(item.id)?.binding ?? item.delivery.payloadBinding;
          this.keepResult(command, item, binding, { code: affected || renderingStarted ? code : 'batch_not_applied', detail: renderingStarted ? `Renderer could not finish the command: ${detail}` : affected ? detail : 'The command was not applied because another delivery failed' });
        }
      }
      throw cause;
    } finally { this.releaseUnused(); }
  }
  private async prepare(item: Descriptor, signal: AbortSignal): Promise<NativeDelivery> {
    this.activeDelivery(signal);
    if (item.sourceRef.kind === 'live') {
      if (item.sourceRef.identity.run !== this.context.appRunId || item.sourceRef.availability !== 'temporary') throw failed('source_mismatch', 'Live source belongs to another run');
      return { signature: canonical(item.delivery), url: item.delivery.url, binding: freeze(jsonCopy(item.delivery.payloadBinding)) };
    }
    if (!['saved-image', 'composite', 'written'].includes(item.sourceRef.kind) || !['retained', 'temporary'].includes(item.sourceRef.availability)) throw failed('source_unavailable', 'This source has no prepared delivery');
    const token = item.delivery.sourceToken, signature = canonical({ source: item.sourceRef, delivery: item.delivery });
    const existing = this.native.get(token), preparing = this.preparing.get(token);
    if (existing) { if (existing.signature !== signature) throw failed('source_mismatch', 'Prepared token changed its source or bytes'); return existing; }
    if (preparing) { if (preparing.signature !== signature) throw failed('source_mismatch', 'Pending token changed its source or bytes'); return preparing.promise; }
    const promise = this.fetchNative(item, signature, signal);
    this.preparing.set(token, { signature, promise });
    try { return await promise; } finally { if (this.preparing.get(token)?.promise === promise) this.preparing.delete(token); }
  }
  private async fetchNative(item: Descriptor, signature: string, signal: AbortSignal): Promise<NativeDelivery> {
    const response = await this.options.fetch(item.delivery.url, { credentials: 'same-origin', redirect: 'error', signal });
    if (signal.aborted) { await response.body?.cancel().catch(() => {}); this.activeDelivery(signal); }
    this.activeDelivery(signal);
    if (!response.ok || response.redirected) throw failed('source_unavailable', 'Prepared native source could not be read');
    const contentType = response.headers.get('content-type')?.split(';', 1)[0].trim().toLowerCase();
    const allowed = item.sourceRef.kind === 'written' ? ['text/plain', 'application/vnd.micromind.findings+json'] : ['image/jpeg', 'image/png', 'image/webp', 'image/gif'];
    if (!contentType || !allowed.includes(contentType)) throw failed('source_mismatch', 'Native response is not a supported source type');
    if (item.delivery.mediaType !== undefined && item.delivery.mediaType !== contentType) throw failed('source_mismatch', 'Native response type differs from its lease');
    const length = response.headers.get('content-length');
    if (length !== null && (!/^\d+$/.test(length) || Number(length) > MAX_BYTES)) throw failed('source_unavailable', 'Native response exceeds its byte bound');
    if (!response.body) throw failed('source_unavailable', 'Native response has no bytes');
    const reader = response.body.getReader(), chunks: Uint8Array<ArrayBuffer>[] = [];
    let total = 0;
    try {
      while (true) {
        const part = await reader.read(); this.activeDelivery(signal); if (part.done) break;
        total += part.value.byteLength;
        if (total > MAX_BYTES) throw failed('source_unavailable', 'Native response exceeds its byte bound');
        chunks.push(new Uint8Array(part.value));
      }
    } catch (cause) { await reader.cancel().catch(() => {}); throw cause; }
    finally { reader.releaseLock(); }
    if (!total || length !== null && total !== Number(length)) throw failed('source_unavailable', 'Native response bytes are incomplete');
    const bytes = new Uint8Array(total); let at = 0;
    for (const chunk of chunks) { bytes.set(chunk, at); at += chunk.byteLength; }
    if (item.sourceRef.kind === 'written') {
      const text = new TextDecoder().decode(bytes);
      const matches = contentType === 'application/vnd.micromind.findings+json'
        ? canonical(JSON.parse(text)) === canonical(item.delivery.report)
        : text === item.delivery.text;
      if (!matches) throw failed('source_mismatch', 'Written content differs from its original bytes');
    }
    const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), (byte) => byte.toString(16).padStart(2, '0')).join('');
    this.activeDelivery(signal); const binding = { ...item.delivery.payloadBinding, servedDigest: digest };
    if (digest !== item.delivery.payloadBinding.servedDigest) throw Object.assign(failed('source_mismatch', 'Fetched native bytes differ from the prepared served digest'), { payloadBinding: binding });
    if (item.delivery.payloadBinding.originalDigest != null && digest !== item.delivery.payloadBinding.originalDigest || item.sourceRef.digest != null && digest !== item.sourceRef.digest) throw Object.assign(failed('source_mismatch', 'Unchanged native bytes differ from their original integrity'), { payloadBinding: binding });
    const url = this.options.createObjectURL(new Blob([bytes], { type: contentType }));
    const delivery = { signature, url, binding: freeze(jsonCopy(binding)) };
    this.native.set(item.delivery.sourceToken, delivery); return delivery;
  }
  private keepResult(command: Command, item: Descriptor, payloadBinding: RecordValue, error?: Occurrence['error']): void {
    const lease = command.fence.targets[item.id];
    const key = `${command.commandId}\n${item.id}`;
    this.results.set(key, freeze({ requestId: command.requestId, actionId: command.actionId, commandId: command.commandId, appRunId: command.appRunId,
      retainUntilMs: this.options.now() + (command.fence.deadlineMono - command.fence.issuedMono) * 1000,
      presentationId: item.id, op: command.op, lifetimeGeneration: lease.lifetimeGeneration, sourceGeneration: lease.sourceGeneration, committedRevision: lease.committedRevision,
      committedPropertyVersions: jsonCopy(lease.committedPropertyVersions), owner: lease.owner, sourceToken: lease.sourceToken, source: jsonCopy(lease.sourceRef), payloadBinding: jsonCopy(payloadBinding), baseViewDrawRef: lease.baseViewDrawRef ?? null, ...(error ? { error } : {}) }));
    while (this.results.size > MAX_RESULTS) this.results.delete(this.results.keys().next().value!);
  }
  private releaseUnused(): void {
    // Keep a shared Blob while any actual media root still uses it. Pending batches retain pins.
    if (this.pending > 1 || this.preparing.size) return;
    for (const [pointer, target] of this.physicalTargets) if (!(target instanceof HTMLElement) || !this.options.host.contains(target)) this.physicalTargets.delete(pointer);
    const used = new Set(this.options.windows.facts().filter((item) => item.open && item.connected).map((item) => item.sourceToken));
    for (const [token, delivery] of this.native) if (this.disposed || !used.has(token)) { this.options.revokeObjectURL(delivery.url); this.native.delete(token); }
  }
  private reduce(event: InteractionEvent): void {
    if (this.disposed) return;
    this.interaction = reduceInteraction(this.interaction, event);
    const retained = new Set([this.interaction.pointer, ...this.interaction.contacts].filter((pointer) => pointer.state === 'present' && pointer.css !== null).map((pointer) => pointer.pointerId));
    for (const pointer of this.physicalTargets.keys()) if (!retained.has(pointer)) this.physicalTargets.delete(pointer);
  }
  /** Ephemeral node references from the actual owners, never another source catalogue. */
  private registry(): InteractionRegistry {
    const native = this.options.windows.facts(), structural = this.options.structural?.();
    const nodes: InteractionRegistry['nodes'] = new Map();
    for (const item of native) if (item.open && item.connected) nodes.set(item.id, this.options.windows.interactionNodes(item.id));
    for (const entry of structural?.nodes ?? []) {
      if (nodes.has(entry.itemId) || native.some((item) => item.id === entry.itemId)) throw failed('context_mismatch', 'Fixed surface overlaps a native presentation identity');
      nodes.set(entry.itemId, entry);
    }
    return { native, structural, facts: [...native, ...(structural?.items ?? [])], nodes };
  }
  private registered(target: EventTarget | null, registry = this.registry()): { item: DisplayFact; root: HTMLElement; control: StudioControlFact | null; node: HTMLElement | null } | null {
    if (!(target instanceof HTMLElement)) return null;
    const containing = registry.facts.filter((item) => item.open && item.connected && registry.nodes.get(item.id)?.root.contains(target));
    // A nested real root is more specific; several layers on one canvas are ambiguous.
    const closest = containing.filter((item) => !containing.some((other) => other.id !== item.id
      && registry.nodes.get(item.id)!.root !== registry.nodes.get(other.id)!.root
      && registry.nodes.get(item.id)!.root.contains(registry.nodes.get(other.id)!.root)));
    if (closest.length !== 1) return null;
    const item = closest[0], nodes = registry.nodes.get(item.id)!, root = nodes.root;
    for (let node: HTMLElement | null = target; node && node !== root; node = node.parentElement) {
      const index = nodes.controls.indexOf(node), control = item.controls[index];
      if (control?.present) return { item, root, control, node };
    }
    return { item, root, control: null, node: null };
  }
  private capturedBy(pointerId: number | null, registry = this.registry()): string | null {
    if (pointerId === null) return null;
    for (const item of registry.facts) {
      if (!item.open || !item.connected) continue;
      const nodes = registry.nodes.get(item.id); if (!nodes) continue;
      const captured = (node: HTMLElement) => nodes.root.contains(node) && typeof node.hasPointerCapture === 'function' && node.hasPointerCapture(pointerId);
      for (let index = 0; index < nodes.controls.length; index++) {
        if (item.controls[index]?.present && captured(nodes.controls[index])) return item.controls[index].id;
      }
      if (nodes.viewport && captured(nodes.viewport)) return item.id;
    }
    return null;
  }
  private focus(target: EventTarget | null, registry = this.registry()): FocusFacts {
    if (target instanceof HTMLElement && this.options.host.contains(target)) {
      let editable = target.tagName === 'TEXTAREA' || target.tagName === 'INPUT' && !['range', 'button', 'checkbox', 'radio', 'submit', 'reset'].includes(target.getAttribute('type') ?? 'text');
      for (let node: HTMLElement | null = target; node && node !== this.options.host; node = node.parentElement) if (node.getAttribute('contenteditable') === 'true' || node.getAttribute('contenteditable') === '') editable = true;
      const registered = this.registered(target, registry);
      if (editable) return { presentationId: registered?.item.id ?? null, controlId: registered && registered.node === target ? registered.control?.id ?? null : null, role: 'textbox', editable: true };
      if (registered) return { presentationId: registered.item.id, controlId: registered.control?.id ?? null, role: registered.control?.role ?? 'group', editable: false };
    }
    return { presentationId: null, controlId: null, role: 'none', editable: false };
  }
  private hitAt(target: EventTarget | null, css: readonly number[], registry = this.registry()) {
    const registered = this.registered(target, registry);
    const hit: HitFacts = { presentationId: null, sourceKey: null, sourceGeneration: undefined, sourcePoint: null, hitPath: [], targetCandidates: [] };
    if (registered) {
      const { item, root, control } = registered, image = root.querySelector('img');
      const point = nativeFact(item) && image && target instanceof HTMLElement && image.contains(target) && item.viewTransform && item.load.state === 'drawn'
        ? workspaceToSource(css, { contentRect: item.contentRect, clipRect: item.clipRect, viewTransform: item.viewTransform }) : null;
      Object.assign(hit, { presentationId: item.id, sourceKey: point && nativeFact(item) ? item.sourceKey : null, sourceGeneration: point ? item.sourceGeneration : undefined, sourcePoint: point,
        hitPath: control ? [control.id, item.id] : [item.id], targetCandidates: [nativeFact(item) ? `target:${item.id}` : `target:surface:${item.id}`] });
    }
    return { hit };
  }
  private pointer(event: PointerEvent, type: 'pointer-move' | 'pointer-down'): void {
    if (this.disposed) return;
    const css = [event.clientX, event.clientY], registry = this.registry();
    const target = typeof document.elementFromPoint === 'function' ? document.elementFromPoint(css[0], css[1]) : event.target;
    const { hit } = this.hitAt(target, css, registry), capturedBy = this.capturedBy(event.pointerId, registry);
    const modality = event.pointerType;
    if (!['mouse', 'touch', 'pen'].includes(modality)) return;
    this.physicalTargets.set(event.pointerId, target);
    this.reduce({ type, pointerId: event.pointerId, modality: modality as 'mouse' | 'touch' | 'pen', primary: event.isPrimary, css, buttons: event.buttons, capturedBy, atMs: this.options.now(), hit });
  }
  collect(options: WorkspaceSample) {
    this.active(); counter(options.seq); numeric(options.nowMs);
    const viewport = jsonCopy(this.options.viewport());
    if (viewport.visible !== this.interaction.visible) this.reduce({ type: 'visibility', visible: viewport.visible, atMs: options.nowMs });
    const registry = this.registry(), { native, structural, facts } = registry;
    // Confirmation receipts stop being useful after their admitted deadline, which counts only time the studio is
    // in view (as the app's own wait does: completion.py): in the background nothing is drawn, so a receipt is kept
    // until it can be, and its window is extended by the time away. Keep the actual windows and replay ledger;
    // retire only report history.
    if (!viewport.visible) this.hiddenAtMs ??= options.nowMs;
    else if (this.hiddenAtMs !== null) {
      const away = Math.max(0, options.nowMs - this.hiddenAtMs); this.hiddenAtMs = null;
      for (const [key, entry] of this.results) this.results.set(key, freeze({ ...entry, retainUntilMs: entry.retainUntilMs + away }));
    }
    if (this.hiddenAtMs === null) for (const [key, entry] of this.results) if (options.nowMs > entry.retainUntilMs) this.results.delete(key);
    const confirming = new Set([...this.results.values()].map(entry => entry.presentationId));
    const selected = structural?.selected ?? options.selected;
    const sampled = snapshotInteraction(this.interaction, { ...options, selected });
    const rehit = (pointer: typeof sampled.pointer) => {
      if (pointer.state !== 'present' || !pointer.css) return pointer;
      const target = typeof document.elementFromPoint === 'function' ? document.elementFromPoint(pointer.css[0], pointer.css[1]) : this.physicalTargets.get(pointer.pointerId!) ?? null;
      const { hit } = this.hitAt(target, pointer.css, registry);
      const active = pointer.buttons > 0 || sampled.contacts.some((contact) => contact.pointerId === pointer.pointerId && contact.state === 'present');
      const captured = active ? this.capturedBy(pointer.pointerId, registry) : null;
      return Object.freeze({ ...pointer, ...hit, capturedBy: captured });
    };
    const focus = viewport.visible ? this.focus(document.activeElement ?? this.focusedTarget, registry) : sampled.focus;
    const interaction = Object.freeze({ ...sampled, pointer: rehit(sampled.pointer), contacts: Object.freeze(sampled.contacts.map(rehit)), focus });
    // Sample physical hits before applying report bounds: a stationary pointer can
    // now be over a different window. Retained history and pending commands stay
    // representable too, without granting them fresh rendered evidence.
    const pointers = [interaction.pointer, ...interaction.contacts];
    const needed = new Set([interaction.focus.presentationId, ...pointers.map(pointer => pointer.presentationId),
      this.interaction.lastContentPointer?.presentationId, this.interaction.lastContentFocus?.presentationId]);
    for (const target of structural?.targets ?? []) if (selected.includes(target.id) && target.presentationId) needed.add(target.presentationId);
    for (const item of native) if (selected.includes(`target:${item.id}`)) needed.add(item.id);
    for (const targets of this.manualTargets.values()) for (const target of targets) needed.add(target);
    const reportable = facts.filter(item => item.open || item.connected || confirming.has(item.id) || needed.has(item.id));
    const ordered = [...reportable.filter(item => needed.has(item.id)),
      ...reportable.filter(item => item.open && item.connected && !needed.has(item.id)),
      ...reportable.filter(item => !(item.open && item.connected) && !needed.has(item.id)).reverse()];
    const items = ordered.slice(0, 40), nativeItems = items.filter(nativeFact);
    if (structural && [...needed].some((value) => value != null && facts.some((item) => item.id === value) && !items.some((item) => item.id === value))) throw failed('target_not_representable', 'Current or historical content focus cannot fit this report');
    const sources = new Map<string, WindowSource>();
    for (const item of nativeItems) {
      const previous = sources.get(item.sourceKey);
      if (previous && canonical(previous) !== canonical(item.source)) throw failed('source_mismatch', 'One source key reports conflicting identities');
      sources.set(item.sourceKey, item.source);
    }
    const nativeTargets = (structural ? native : nativeItems).filter((item) => item.open && item.connected).map((item) => ({ id: `target:${item.id}`, type: 'presentation', presentationId: item.id, sourceKey: item.sourceKey, basis: 'registered-window' }));
    const allTargets: WorkspaceStructuralTarget[] = [...nativeTargets, ...(structural?.targets ?? [])];
    const required = new Set([...selected, ...pointers.flatMap(pointer => pointer.targetCandidates)]);
    for (const target of allTargets) if (target.presentationId && needed.has(target.presentationId)) required.add(target.id);
    const representable = structural ? allTargets.filter((target) => !target.presentationId || items.some((item) => item.id === target.presentationId)) : allTargets;
    const targets = structural ? [...representable.filter((target) => required.has(target.id)), ...representable.filter((target) => !required.has(target.id))].slice(0, 16) : allTargets;
    if (targets.length > 16 || [...required].some((value) => !targets.some((target) => target.id === value))) throw failed('target_not_representable', 'Selected or active target is not a bounded registered referent');
    const layoutKey = canonical([viewport, items.map((item) => nativeFact(item)
      ? [item.id, item.rootId, item.connected, item.visible, item.rect, item.contentRect, item.clipRect, item.z, item.viewTransform, item.controls, item.load]
      : [item.id, item.rootId, item.connected, item.visible, item.rect ?? null, item.z ?? null, item.view, item.controls, item.revision])]);
    if (layoutKey !== this.layoutKey) { this.layoutKey = layoutKey; this.layoutRevision = counter(this.layoutRevision + 1); }
    const commandResults = [...this.results.values()].map((entry) => this.result(entry, native.find((item) => item.id === entry.presentationId), options.seq));
    const omitted = { items: Math.max(0, facts.length - items.length) + (structural?.omittedItems ?? 0), sources: new Set(native.filter((item) => !sources.has(item.sourceKey)).map((item) => item.sourceKey)).size,
      targets: allTargets.length - targets.length, edges: structural ? native.filter((item) => item.open && item.connected && !nativeItems.includes(item)).length : 0 };
    return { type: 'screen' as const, schema: 'screen-observation-v2' as const, client: { ...this.client }, appRunId: this.context.appRunId,
      seq: options.seq, layoutRevision: this.layoutRevision, capturedMonoMs: options.nowMs, viewport, visible: viewport.visible,
      items, sources: [...sources.values()], targets, edges: nativeItems.filter((item) => item.open && item.connected).map((item) => ({ type: 'shows', from: item.id, to: item.sourceKey })),
      interaction, commandResults, complete: structural ? false : facts.length === items.length, omitted,
      ...(this.options.foregroundHost ? { foreground: workspaceForeground(this.options.foregroundHost, [...registry.nodes.values()].map((node) => node.root)) } : {}),
      ...(structural ? { totals: { items: items.length + omitted.items, sources: sources.size + omitted.sources, targets: targets.length + omitted.targets,
        edges: nativeItems.filter((item) => item.open && item.connected).length + omitted.edges } } : {}), applied: [] as string[], user: [] as { id: string; prop: string }[] };
  }
  private result(entry: Occurrence, item: Fact | undefined, reportSeq: number) {
    // The renderer adds only its actual post-decode acknowledgement. Every
    // admitted payload field still has to match the original fetched binding.
    const { decodedToken, ...binding } = item?.payloadBinding ?? {};
    const payloadMatches = (decodedToken === undefined || decodedToken === entry.sourceToken)
      && canonical(binding) === canonical(entry.payloadBinding);
    const actual = item && item.lifetimeGeneration === entry.lifetimeGeneration && item.sourceGeneration === entry.sourceGeneration && item.sourceToken === entry.sourceToken && canonical(item.source) === canonical(entry.source) && payloadMatches && item.revision >= entry.committedRevision && Object.entries(entry.committedPropertyVersions).every(([key, version]) => item.propertyVersions[key] === version && item.propertyOwners[key] === entry.owner) ? item : null;
    const absent = actual !== null && entry.op === 'close' && !actual.open && !actual.connected && !actual.visible && actual.load.state === 'empty';
    const phase = entry.error ? 'error' : absent ? 'closed' : actual?.open ? actual.load.state : 'unknown';
    const drawRef = phase === 'drawn' && actual?.connected && actual.visible && actual.staged === false
      ? actual.source.kind === 'live' ? actual.heldSample ? { held: actual.heldSample } : actual.liveSample : actual.load.drawToken : null;
    const viewDrawRef = phase === 'drawn' && actual?.connected && actual.visible && actual.staged === false
      && actual.commandId === entry.commandId && actual.requestId === entry.requestId && actual.actionOccurrenceId === entry.actionId
      && actual.viewDrawRef !== entry.baseViewDrawRef ? actual.viewDrawRef : null;
    const error = entry.error ?? (actual?.load.state === 'error' ? { code: actual.load.errorCode ?? 'decode_failed', detail: actual.load.error ?? 'Source could not decode' } : undefined);
    return { requestId: entry.requestId, actionId: entry.actionId, commandId: entry.commandId, appRunId: entry.appRunId, presentationId: entry.presentationId,
      phase, reportSeq, owner: entry.owner, lifetimeGeneration: entry.lifetimeGeneration, sourceGeneration: entry.sourceGeneration, committedRevision: entry.committedRevision,
      committedPropertyVersions: { ...entry.committedPropertyVersions }, propertyVersions: item ? { ...item.propertyVersions } : {}, propertyOwners: item ? { ...item.propertyOwners } : {},
      committedPropertyOwners: Object.fromEntries(Object.keys(entry.committedPropertyVersions).map((key) => [key, entry.owner])),
      sourceToken: entry.sourceToken, sourceKey: entry.source.key, payloadBinding: jsonCopy(entry.payloadBinding), drawRef, viewDrawRef, absent, ...(error ? { error: { ...error } } : {}) };
  }
  snapshotAtSend(options: WorkspaceSample) { return freeze(structuredClone(this.collect(options))); }
  private manualTargets = new Map<string, string[]>();
  manualResult(message: { requestId?: string | null; ok?: boolean; pending?: boolean; error?: string | null; summary?: string | null }): void {
    if (!message.requestId) return;
    const target = this.manualTargets.get(message.requestId); if (!target) return;
    if (message.ok === false) for (const id of target) this.options.windows.rejectManual(id, message.error || message.summary || undefined);
    if (!message.pending) this.manualTargets.delete(message.requestId);
  }
  manual(value: ManualWindowCommand): void {
    this.active(); const command = jsonCopy(value);
    fields(record(command), ['type', 'action', 'instance', 'values', 'context', 'requestId', 'commandId', 'actionOccurrenceId', 'base'], ['companions']);
    if (command.type !== 'present') throw failed('invalid_command', 'Invalid manual proposal type');
    fields(record(command.base), ['sourceGeneration', 'lifetimeGeneration', 'propertyVersions']);
    const { appRunId, ...client } = command.context; this.checkClient(client, appRunId);
    for (const key of ['requestId', 'commandId', 'actionOccurrenceId'] as const) id(command[key]);
    if (command.action === 'group' ? !Array.isArray(command.companions) || command.companions.length < 1 || command.companions.length > 7 : command.companions !== undefined) throw failed('invalid_action', 'Only grouping accepts explicit companion windows');
    const participants = [{ instance: command.instance, base: command.base }, ...(command.companions ?? [])];
    const facts = this.options.windows.facts();
    for (const participant of participants) {
      fields(record(participant), ['instance', 'base']);
      id(participant.instance); fields(record(participant.base), ['sourceGeneration', 'lifetimeGeneration', 'propertyVersions']);
      const item = facts.find((candidate) => candidate.id === participant.instance), base = participant.base;
      if (!item || base.sourceGeneration !== item.sourceGeneration || base.lifetimeGeneration !== item.lifetimeGeneration || canonical(versionMap(base.propertyVersions)) !== canonical(item.propertyVersions)
          || command.action === 'group' && (!item.open || !item.visible || !Number.isSafeInteger(item.propertyVersions.group))) throw failed('ownership_unavailable', 'Manual control no longer has its authoritative base');
    }
    const targets = participants.map(participant => participant.instance);
    if (new Set(targets).size !== targets.length) throw failed('invalid_action', 'Grouping needs distinct windows');
    const allowed: Record<string, string[]> = { zoom: ['factor', 'level', 'fit', 'size'], pan: ['center'], move: ['position'], resize: ['size'], front: [], close: [], opacity: ['value'], hide: [], restore: [], group: [], ungroup: [] };
    if (!Object.hasOwn(allowed, command.action)) throw failed('unsupported_action', 'Unregistered manual operation');
    fields(record(command.values), [], allowed[command.action]);
    if (command.action === 'zoom') {
      const keys = Object.keys(command.values).filter(key => key !== 'size');
      if (keys.length !== 1 || !(command.values.fit === true || numeric(command.values.factor ?? command.values.level) > 0)) throw failed('invalid_action', 'Invalid manual zoom');
      if ('size' in command.values && (command.values.fit !== true || vector(command.values.size, 2).some(number => number <= 0))) throw failed('invalid_action', 'Invalid manual fit size');
    }
    if (command.action === 'pan' && vector(command.values.center, 2).some((number) => number < 0 || number > 1)) throw failed('invalid_action', 'Invalid manual source position');
    if (command.action === 'opacity' && (numeric(command.values.value) < 0 || numeric(command.values.value) > 1)) throw failed('invalid_action', 'Invalid opacity');
    if (command.action === 'move') vector(command.values.position, 2);
    if (command.action === 'resize' && vector(command.values.size, 2).some((number) => number <= 0)) throw failed('invalid_action', 'Invalid manual size');
    if (this.manualSeq >= Number.MAX_SAFE_INTEGER) throw failed('capacity_unavailable', 'Manual sequence exhausted; reconnect with a fresh server incarnation');
    const manualSeq = ++this.manualSeq;
    this.manualTargets.set(command.requestId, command.action === 'group' ? [] : targets);
    if (this.manualTargets.size > 64) this.manualTargets.delete(this.manualTargets.keys().next().value!);
    this.options.send({ type: 'present', schema: 'presentation-manual-v2', manualSeq, op: command.action, targets, values: command.values,
      client: { ...this.client }, appRunId: this.context.appRunId, requestId: command.requestId, commandId: command.commandId, actionId: command.actionOccurrenceId,
      base: Object.fromEntries(participants.map(participant => [participant.instance, participant.base])) });
  }
  private activeDelivery(signal: AbortSignal): void {
    this.active();
    if (signal.aborted) throw failed('context_mismatch', 'The Project presentation was retired');
  }
  /** Mirror the server's Project reset, retaining socket sequence and command replay fences. */
  clearProject(): void {
    this.deliveries.abort(); this.deliveries = new AbortController(); this.live.dispose();
    this.options.windows.clearProject();
    for (const delivery of this.native.values()) this.options.revokeObjectURL(delivery.url);
    this.native.clear(); this.preparing.clear(); this.results.clear(); this.manualTargets.clear();
    this.physicalTargets.clear(); this.focusedTarget = null; this.interaction = createInteractionState();
  }
  retryLive(id: string): void { if (!this.disposed) this.live.retry(id); }
  dispose(): void {
    this.live.dispose();
    if (this.disposed) return; this.disposed = true; this.events.abort(); this.deliveries.abort();
    for (const delivery of this.native.values()) this.options.revokeObjectURL(delivery.url);
    this.native.clear(); this.results.clear(); this.commands.clear(); this.physicalTargets.clear(); this.focusedTarget = null;
  }
}
