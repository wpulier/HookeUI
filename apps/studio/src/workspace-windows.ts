import { bindArtifactEscape } from './artifact-keyboard';
import { sameStudyResource, studyResourceReference, type StudyRetainedView } from './workspace-study-restoration';
import { renderResearchReport } from './research-report';
import { researchCaptureImageUrl } from './research-captures';
import type { ResearchSession, ResearchCaptureTarget } from './protocol';
import { FloatingWindow, PanelStack, placeAway, windowPlacementArea } from './panels';
import type { WindowFacts } from './panels';
import { fitPanel, fitPicture } from './fit';
import type { Disc, Fit } from './fit';
import './workspace-windows.css';

/** Lossless internal projection of presentation-command-v2; the actions mapper owns wire parsing. */
export interface WindowSource {
  key: string; kind: string; media: string; availability: string;
  identity: Record<string, unknown>; provenance: Record<string, unknown>;
  components?: WindowSource[]; digest?: string | null; locator?: string | null;
}
export interface WindowContext {
  workspaceId: string; clientId: string; pageEpoch: string; connectionEpoch: string; appRunId: string;
}
export interface LiveSample {
  readonly run: string; readonly boot: string | null; readonly seq: number | null;
  readonly frame?: number | null; readonly epoch?: number | null;
}
export interface WindowState {
  id: string; workspaceId: string; source: WindowSource;
  sourceGeneration: number; lifetimeGeneration: string; revision: number; open: boolean; title: string;
  geometryIntent: { position: number[]; size: number[] | null; relation?: { kind: string; reference?: string; gapPx?: number; align?: string; side?: 'left' | 'right' } | null };
  view: { zoom: number; pan: number[]; opacity?: number; hidden?: boolean; group?: string | null }; propertyVersions: Record<string, number>; propertyOwners: Record<string, string | null>;
  staged?: boolean;
  delivery: { sourceToken: string; url: string; disc?: number[] | null; originalSize?: number[]; text?: string; report?: ResearchSession['result']; payloadBinding?: Record<string, unknown> };
}
export interface WindowBatch {
  context: WindowContext; requestId: string; commandId: string; actionOccurrenceId: string;
  items: WindowState[]; op?: string;
}
export interface ManualWindowCommand {
  type: 'present'; action: string; instance: string; values: Record<string, unknown>;
  context: WindowContext; requestId: string; commandId: string; actionOccurrenceId: string;
  base: { sourceGeneration: number; lifetimeGeneration: string; propertyVersions: Record<string, number> };
  companions?: { instance: string; base: ManualWindowCommand['base'] }[];
}
export interface WindowOptions {
  host: HTMLElement; panels: PanelStack; context: WindowContext;
  hiddenHost?: HTMLElement;
  focusFallback?: () => HTMLElement | null;
  /** Local restoration intent, associated with this exact admitted request. */
  background?: (requestId: string) => boolean;
  onPresentation?: (id: string, root: HTMLElement, title: () => string, foreground: boolean, requestId: string) => void;
  openCapture?: (target: ResearchCaptureTarget, parentInstance: string) => Promise<void>;
  send(command: ManualWindowCommand): void; retryLive?(id: string): void; facts(): WindowFacts | null;
}
interface Control { node: HTMLElement; name: string; property: string; role: string }
interface MediaLoad {
  state: 'empty' | 'loading' | 'loaded' | 'drawn' | 'error' | 'unknown';
  drawToken: string | null; naturalSize: number[] | null; error?: string; errorCode?: string;
}
interface ViewPaint {
  ref: string; view: string; binding: string; job: object | null; media: HTMLElement | null;
}
interface WindowRow {
  state: WindowState; root: HTMLElement; bar: HTMLElement; title: HTMLElement; toolbar: HTMLElement; viewport: HTMLElement;
  picture: HTMLElement; caption: HTMLElement; message: HTMLElement; controls: Control[]; shell: FloatingWindow;
  events: AbortController; mediaEvents: AbortController | null; media: HTMLElement | null; image: HTMLImageElement | null;
  command: { requestId: string; commandId: string; actionOccurrenceId: string };
  view: { zoom: number; pan: number[] }; load: MediaLoad; fit: Fit | null;
  pending: Map<string, number>; expectedUrl: string | null; decodeJob: object | null; decodedToken: string | null;
  opener: HTMLElement | null;
  /** Where the window was (viewport CSS) before the user's geometry change the server has not taken yet. */
  beforeUser: number[] | null;
  allowSmall: boolean; dismissed: boolean; sizeEstablished: boolean; liveSample: LiveSample | null; heldSample: LiveSample | null; liveHeld: boolean; disposed: boolean; layoutStamp: string | null; viewPaint: ViewPaint | null;
  /** The newest live frame waiting for the next animation frame; what is on screen until then is the last paint. */
  queuedLive: { frame: LiveFrame; sample: LiveSample } | null;
}
type LiveFrame = { image: HTMLImageElement; sample: unknown; held?: boolean; sourceToken: string; sourceGeneration: number; lifetimeGeneration: string };
const CHROME = 42; // The shared design header; controls stay inside it.
const copy = <T>(value: T): T => structuredClone(value);
const uuid = () => crypto.randomUUID();
const rect = (value: DOMRect): number[] => [value.left, value.top, value.width, value.height];
const contextKeys = ['workspaceId', 'clientId', 'pageEpoch', 'connectionEpoch', 'appRunId'] as const;
const supportedSources = new Set(['live', 'saved-image', 'video', 'lesson', 'written', 'composite']);
const pictureSources = new Set(['live', 'saved-image', 'composite']);
const clampUnit = (value: number) => Math.max(0, Math.min(1, value));
const PLACE_TRIES = 4; // an Arrange's tile, sent at most this often for one window before it is left where it is
const displayKinds: Record<string, string> = { live: 'magnifier', 'saved-image': 'image', composite: 'comparison' };
const buttonLabels: Record<string, string> = { 'zoom-in': '+', 'zoom-out': '−', 'zoom-fit': 'Fit', front: '↑', close: '×', hide: '−', ungroup: '↔', retry: 'Retry' };
const derivedKind = (source: WindowSource) => displayKinds[source.kind] ?? source.kind;
/** A window's name: its own title, else what it shows ("Live magnification", "Reference photo", "Saved work"). The
 * same name is shown and saved with the Project's arrangement (an empty one was refused there, live 2026-10-29). */
export function windowTitle(state: Pick<WindowState, 'title' | 'source'>): string {
  return state.title?.trim() || (state.source.kind === 'live' ? 'Live magnification'
    : ['reference', 'external-reference'].includes(state.source.media) ? 'Reference photo' : 'Saved work');
}
function urlKey(url: string): string {
  return new URL(url, document.baseURI || 'http://localhost/').href;
}
function failure(code: string, message: string): Error & { code: string } { return Object.assign(new Error(message), { code }); }
/** Semantic comparison of bounded plain JSON; arrays keep their original order.
 * Runtime's equivalent helper is private: the renderer must not import its transport owner.
 */
function canonicalJSON(value: unknown): string {
  let nodes = 0, length = 0;
  const invalid = () => failure('invalid_descriptor', 'Source comparison requires bounded plain JSON');
  const piece = (encoded: string) => { length += encoded.length; if (length > 2 * 1024 * 1024) throw invalid(); return encoded; };
  function visit(input: unknown, depth: number): string {
    if (++nodes > 20000 || depth > 24) throw invalid();
    if (typeof input === 'string' && input.length > 65536) throw invalid();
    if (input === null || typeof input === 'boolean' || typeof input === 'string' || typeof input === 'number' && Number.isFinite(input)) {
      const encoded = JSON.stringify(input); if (encoded === undefined) throw invalid(); return piece(encoded);
    }
    if (!input || typeof input !== 'object' || Object.getOwnPropertySymbols(input).length) throw invalid();
    const descriptors = Object.getOwnPropertyDescriptors(input);
    if (Array.isArray(input)) {
      if (input.length > 20000 || Object.keys(input).length !== input.length) throw invalid();
      return `[${Array.from({ length: input.length }, (_, index) => {
        const entry = descriptors[index]; if (!entry || !Object.hasOwn(entry, 'value')) throw invalid();
        return visit(entry.value, depth + 1);
      }).join(',')}]`;
    }
    if (Object.getPrototypeOf(input) !== Object.prototype && Object.getPrototypeOf(input) !== null) throw invalid();
    return `{${Object.keys(descriptors).sort().map((key) => {
      const entry = descriptors[key]; if (!entry.enumerable || !Object.hasOwn(entry, 'value')) throw invalid();
      const encoded = JSON.stringify(key); if (encoded === undefined) throw invalid();
      return `${piece(encoded)}:${visit(entry.value, depth + 1)}`;
    }).join(',')}}`;
  }
  const result = visit(value, 0); if (result.length > 2 * 1024 * 1024) throw invalid(); return result;
}
function opaqueId(value: unknown): value is string { return typeof value === 'string' && value.length > 0 && value.length <= 256; }
function nullableCounter(value: unknown): value is number | null {
  return value === null || typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}
/** Snapshot primitives before checking them: a caller may reuse its camera header object. */
function admittedSample(value: unknown, appRunId: string): LiveSample {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) throw failure('source_mismatch', 'Live sample is not a record');
  const keys = Object.getOwnPropertyNames(value), allowed = ['run', 'boot', 'seq', 'frame', 'epoch'];
  if (Object.getOwnPropertySymbols(value).length || keys.length > allowed.length || keys.some((key) => !allowed.includes(key)) || ['run', 'boot', 'seq'].some((key) => !keys.includes(key))) throw failure('source_mismatch', 'Live sample fields are incomplete or unknown');
  const record = value as Record<string, unknown>;
  const snapshot: Record<string, unknown> = Object.fromEntries(keys.map((key) => [key, record[key]]));
  const { run, boot, seq, frame, epoch } = snapshot;
  if (!opaqueId(run) || run !== appRunId || !(boot === null || opaqueId(boot)) || !nullableCounter(seq) || (keys.includes('frame') && !nullableCounter(frame)) || (keys.includes('epoch') && !nullableCounter(epoch))) throw failure('source_mismatch', 'Live sample has invalid run or bridge facts');
  return Object.freeze({ run, boot, seq, ...(keys.includes('frame') ? { frame: frame as number | null } : {}), ...(keys.includes('epoch') ? { epoch: epoch as number | null } : {}) });
}
function pair(value: unknown, positive = false): value is number[] {
  return Array.isArray(value) && value.length === 2 && value.every((v) => typeof v === 'number' && Number.isFinite(v) && (!positive || v > 0));
}
function connected(root: HTMLElement, host: HTMLElement): boolean {
  return host.contains(root) && root.isConnected !== false;
}
function matrix(image: HTMLImageElement): number[] | null {
  const css = getComputedStyle(image).transform || image.style.transform;
  const match = /^matrix\(([^)]+)\)$/.exec(css);
  if (!match) return null;
  const values = match[1].split(',').map(Number);
  return values.length === 6 && values.every(Number.isFinite) ? values : null;
}
function cssPixels(value: unknown, extent: number): number | null {
  if (typeof value !== 'string' || !value.trim() || !Number.isFinite(extent)) return null;
  const number = Number.parseFloat(value);
  if (!Number.isFinite(number)) return null;
  const pixels = value.endsWith('%') ? number * extent / 100 : number;
  return Number.isFinite(pixels) ? pixels : null;
}
function imageGeometry(row: WindowRow): { left: number; top: number; scaleX: number; scaleY: number } | null {
  if (!row.image) return null;
  const box = row.picture.getBoundingClientRect(), style = getComputedStyle(row.image);
  const width = cssPixels(style.width || row.image.style.width, box.width);
  const left = cssPixels(style.left || row.image.style.left, box.width);
  const top = cssPixels(style.top || row.image.style.top, box.height);
  const naturalWidth = row.image.naturalWidth, naturalHeight = row.image.naturalHeight;
  if (width === null || left === null || top === null || width <= 0 || naturalWidth <= 0 || naturalHeight <= 0) return null;
  const heightStyle = style.height || row.image.style.height;
  const height = cssPixels(heightStyle, box.height) ?? (heightStyle === 'auto' ? width * naturalHeight / naturalWidth : null);
  if (height === null || height <= 0) return null;
  return { left, top, scaleX: width / naturalWidth, scaleY: height / naturalHeight };
}

/** Owns real DOM shells and transient previews only. Presentations owns revisions and ownership. */
export class WorkspaceWindows {
  private readonly opts: WindowOptions;
  private readonly rows = new Map<string, WindowRow>();
  private readonly delivered = new Map<string, string>();
  private disposed = false;
  private phoneActive: string | null = null;
  private phoneStamp = '';
  private restoring = new Set<string>();
  private restoreFailures = new Map<string, string>();
  private placing = new Map<string, { rect: string; tries: number }>(); // placeWindow's commands for one tile
  private readonly shelf = document.createElement('div');

  constructor(options: WindowOptions) {
    this.opts = { ...options, context: copy(options.context) };
    options.host.classList.add('workspace-window-host');
    this.shelf.className = 'workspace-hidden-shelf'; this.shelf.setAttribute('aria-label', 'Hidden windows'); (options.hiddenHost ?? options.host).append(this.shelf);
  }
  retainedViews(): { id: string; title: string; visible: boolean; resource: unknown }[] {
    return [...this.rows.values()].filter(row => row.state.open).map(row => ({ id: row.state.id, title: row.state.title,
      visible: row.root.dataset.workspaceHidden !== 'true' && !row.root.hidden && row.root.dataset.resultTucked !== 'true' && !row.state.view.hidden, resource: copy(row.state.source.identity.resource ?? null) }));
  }
  retainedPresentation(): { views: StudyRetainedView[]; missing: string[] } {
    const views: StudyRetainedView[] = [], missing: string[] = [];
    for (const row of this.rows.values()) {
      if (!row.state.open || row.dismissed || row.root.hidden || row.state.view.hidden || row.root.dataset.workspaceHidden === 'true') continue;
      const resource = studyResourceReference(row.state.source.identity.resource);
      if (resource) views.push({ resource, placementId:row.root.dataset.placementId, title: row.state.title, zoom: row.view.zoom, pan: row.view.pan.slice(),
        position: row.state.geometryIntent.position.slice(), size: row.state.geometryIntent.size?.slice() ?? null });
      else if (row.state.source.kind === 'live' && row.liveHeld)
        missing.push(`${row.state.title}: this held frame has no retained snapshot. It cannot be reopened after leaving the project.`);
    }
    return { views, missing };
  }
  restorePresentation(saved: StudyRetainedView): boolean {
    const candidates = [...this.rows.values()].filter(row => {
      const ref = studyResourceReference(row.state.source.identity.resource);
      return row.state.open && row.root.dataset.workspaceView === document.body.dataset.activeWorkspaceView && ref && sameStudyResource(ref, saved.resource);
    });
    const row = saved.placementId ? candidates.find(row=>row.root.dataset.placementId===saved.placementId) : candidates[0];
    if (!row || !['drawn', 'loaded'].includes(row.load.state)) return false;
    if(saved.placementId)row.root.dataset.placementId=saved.placementId;
    row.root.dataset.studyRetained=JSON.stringify(saved);
    const reason=this.restoreFailures.get(row.state.id);
    if(reason){this.restoreFailures.delete(row.state.id);throw failure('restore_failed',reason);}
    if(row.pending.size)return false;
    // Each accepted descriptor supplies the next command's property fence. Sending
    // all controls together races geometry revisions and exhausts manual admission.
    const same=(a:unknown,b:unknown)=>JSON.stringify(a)===JSON.stringify(b);
    let command:{action:string;values:Record<string,unknown>}|undefined;
    if(pictureSources.has(row.state.source.kind)&&row.state.view.zoom!==saved.zoom)
      command={action:'zoom',values:{level:saved.zoom}};
    else if(pictureSources.has(row.state.source.kind)&&!same(row.state.view.pan,saved.pan))
      command={action:'pan',values:{center:saved.pan}};
    else if(saved.size&&!same(row.state.geometryIntent.size,saved.size))
      command={action:'resize',values:{size:saved.size}};
    else if(saved.position){
      const host=this.opts.host.getBoundingClientRect();
      const position=saved.coordinates==='stage'?[saved.position[0]+host.left,saved.position[1]+host.top]:saved.position;
      if(!same(row.state.geometryIntent.position,position))command={action:'move',values:{position}};
    }
    if(command){this.restoring.add(row.state.id);this.dispatchUser({...command,instance:row.state.id});return false;}
    this.restoring.delete(row.state.id);
    return true;
  }
  /** A tile for a native window with no retained resource (the live magnifier), from the Tab's Arrange: its size, then
   *  its place, each the person's own resize and move, one at a time (each accepted descriptor fences the next, as in
   *  restorePresentation). `rect` is in the host's coordinates. True once it has both, or when there is nothing to place
   *  (gone, closed) or it was refused PLACE_TRIES times (left where it is). */
  placeWindow(id: string, rect: readonly number[]): boolean {
    const row = this.rows.get(id);
    if (!row || row.disposed || !row.state.open || row.dismissed) { this.placing.delete(id); return true; }
    if (row.pending.size) return false;
    const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);
    const size = [rect[2], rect[3]], host = this.opts.host.getBoundingClientRect();
    const position = [rect[0] + host.left, rect[1] + host.top];
    const command = !same(row.state.geometryIntent.size, size) ? { action: 'resize', values: { size } }
      : !same(row.state.geometryIntent.position, position) ? { action: 'move', values: { position } } : null;
    const key = JSON.stringify(rect), tries = this.placing.get(id)?.rect === key ? this.placing.get(id)!.tries : 0;
    if (!command || tries >= PLACE_TRIES) { this.placing.delete(id); return true; }
    this.placing.set(id, { rect: key, tries: tries + 1 });
    this.dispatchUser({ ...command, instance: id });
    return false;
  }
  restore(id: string, foreground = true): void {
    const row = this.row(id);
    const hidden = row.root.hidden || row.root.dataset.resultTucked === 'true';
    // Arrangement refresh already owns this visible placement. Bringing it to
    // front would race the next saved-geometry command's revision fence.
    if (!foreground) return;
    this.dispatchUser({ action: hidden ? 'restore' : 'front', instance: id, values: {} });
  }
  frontPresentation(saved: StudyRetainedView): boolean {
    const row=[...this.rows.values()].find(row=>{
      const reference=studyResourceReference(row.state.source.identity.resource);
      return row.state.open&&saved.placementId&&reference&&
        row.root.dataset.placementId===saved.placementId&&row.root.dataset.project===document.body.dataset.workspaceProject&&
        row.root.dataset.workspaceView===document.body.dataset.activeWorkspaceView&&sameStudyResource(reference,saved.resource);
    });
    if(!row)return false;
    this.restore(row.state.id);return true;
  }
  root(id: string): HTMLElement { return this.row(id).root; }
  /** Ephemeral references from the existing row; never discover registration by DOM IDs. */
  interactionNodes(id: string) {
    const row = this.row(id);
    return Object.freeze({ root: row.root, viewport: row.viewport,
      controls: Object.freeze(row.controls.map((control) => control.node)) });
  }
  floating(id: string): FloatingWindow { return this.row(id).shell; }
  private row(id: string): WindowRow {
    const row = this.rows.get(id); if (!row || row.disposed) throw failure('target_gone', 'Window is no longer available'); return row;
  }
  apply(batch: WindowBatch): void {
    if (this.disposed) return;
    if (contextKeys.some((key) => batch.context[key] !== this.opts.context[key])) throw failure('context_mismatch', 'Window command belongs to another workspace/page/connection/run');
    if (!batch.commandId || !batch.requestId || !batch.actionOccurrenceId || batch.items.length > 8) throw failure('invalid_command', 'Window command lacks bounded occurrence identities');
    const signature = JSON.stringify(batch);
    const previous = this.delivered.get(batch.commandId);
    if (previous !== undefined) {
      if (previous !== signature) throw failure('command_mismatch', 'A delivered command cannot change its descriptors');
      return; // replay is not another relative transform or a replacement of a local preview
    }
    for (const state of batch.items) this.validate(state);
    const ids = new Set(batch.items.map((state) => state.id));
    if (ids.size !== batch.items.length) throw failure('invalid_command', 'Duplicate target descriptor');
    const applied: WindowState[] = [];
    for (const incoming of batch.items) {
      let row = this.rows.get(incoming.id);
      if (row && incoming.revision <= row.state.revision) continue;
      if (row && incoming.lifetimeGeneration !== row.state.lifetimeGeneration) { this.remove(row); row = undefined; }
      if (!row) { row = this.create(copy(incoming), batch); this.rows.set(incoming.id, row); }
      else this.update(row, copy(incoming), batch);
      if (['open', 'restore', 'front'].includes(batch.op ?? '') && incoming.open && !incoming.view.hidden) this.phoneActive = incoming.id;
      if (batch.op === 'front' || batch.op === 'restore' && !this.opts.background?.(batch.requestId)) { row.shell.front(); this.scheduleDraw(row, row.liveSample ?? row.heldSample, true); }
      applied.push(incoming);
    }
    this.arrange(applied);
    this.syncPhone();
    for (const state of applied) {
      const row = this.rows.get(state.id)!;
      // A window this command closed is not presented: its owner would mount it again (main.ts puts presented
      // windows in the Project's artifacts container), and a closed window still in the page is reported connected
      // with live controls, never the closed window the server takes and its close confirms (live 2026-10-29 18:57).
      if (!state.open) continue;
      this.opts.onPresentation?.(state.id, row.root, () => row.state.title, !this.opts.background?.(batch.requestId) && ['open', 'restore', 'front'].includes(batch.op ?? '') && state.open && !state.view.hidden, batch.requestId);
      if (row.root.classList.contains('study-inline-result')) this.layoutMedia(row);
    }
    for (const row of this.rows.values()) this.groupChoices(row);
    this.delivered.set(batch.commandId, signature);
    // The descriptor revision still rejects old state after bounded replay history is dropped.
    if (this.delivered.size > 256) this.delivered.delete(this.delivered.keys().next().value!);
  }
  viewportChanged(): void { this.phoneStamp = ''; this.syncPhone(); }
  /** The studio is in view again: what it was given in the background is painted now, so a paint the page skipped
   * while hidden is never left undone (the app waits for it to be seen: completion.py). */
  shownAgain(): void {
    for (const row of this.rows.values()) {
      if (!row.disposed && !row.dismissed && row.state.open && row.load.state === 'loaded') this.scheduleDraw(row, row.liveSample ?? row.heldSample);
    }
  }
  private syncPhone(): void {
    const narrow = typeof window !== 'undefined' && window.innerWidth <= 960;
    const stamp = JSON.stringify([narrow, this.phoneActive, ...[...this.rows.values()].map(row => [row.state.id,row.state.open,row.dismissed,row.state.view.hidden])]);
    if (this.phoneStamp === stamp) return;
    this.phoneStamp = stamp;
    const available = [...this.rows.values()].filter(row => row.state.open && !row.dismissed && !row.state.view.hidden);
    if (!available.some(row => row.state.id === this.phoneActive)) this.phoneActive = available.at(-1)?.state.id ?? null;
    for (const row of this.rows.values()) {
      const was = row.root.dataset.phoneHidden === 'true';
      const tucked = !row.root.classList.contains('study-artifact') && narrow && available.includes(row) && row.state.id !== this.phoneActive;
      row.root.dataset.phoneHidden = String(tucked);
      if (tucked) row.root.hidden = true;
      else if (was && !row.pending.has('open') && !row.pending.has('hidden')) row.root.hidden = row.dismissed || !row.state.open || !!row.state.view.hidden;
      row.root.dataset.windowOpen = String(!row.dismissed && row.state.open);
      row.shell.frame(); this.layoutMedia(row);
    }
    this.refreshShelf();
  }
  private refreshShelf(): void {
    this.shelf.replaceChildren();
    if (this.opts.hiddenHost) return; // Made today owns combined indexed/window recovery in the Studio shell.
    for (const row of this.rows.values()) if (row.state.open && (row.state.view.hidden || row.root.dataset.phoneHidden === 'true')) {
      const button = document.createElement('button'); button.textContent = `Restore ${row.state.title}`;
      button.addEventListener('click', () => this.dispatchUser({ action: 'restore', instance: row.state.id, values: {} })); this.shelf.append(button);
    }
  }
  private validate(state: WindowState): void {
    if (!state.id || state.id.length > 256 || state.workspaceId !== this.opts.context.workspaceId || !state.lifetimeGeneration || !state.source?.key || !supportedSources.has(state.source.kind)) throw failure('invalid_descriptor', 'Invalid window/source identity');
    for (const value of [state.revision, state.sourceGeneration, ...Object.values(state.propertyVersions)]) {
      if (!Number.isSafeInteger(value) || value < 0) throw failure('invalid_descriptor', 'Authoritative revision must be a safe integer');
    }
    if (!pair(state.geometryIntent.position) || (state.geometryIntent.size !== null && !pair(state.geometryIntent.size, true)) || !pair(state.view.pan) || state.view.pan.some((v) => v < 0 || v > 1) || !Number.isFinite(state.view.zoom) || state.view.zoom <= 0) throw failure('invalid_descriptor', 'Invalid geometry or view');
    if (!state.delivery?.sourceToken || typeof state.delivery.url !== 'string') throw failure('invalid_descriptor', 'Prepared source delivery is missing');
  }
  private create(state: WindowState, batch: WindowBatch): WindowRow {
    const root = document.createElement('section'); root.className = 'workspace-window'; root.dataset.presentationId = state.id;
    // What it shows, for the workspace model (workspace-model.ts nativeKind): a magnifier, a reference photo, a video...
    root.dataset.sourceKind = state.source.kind; root.dataset.sourceMedia = state.source.media;
    const retained = studyResourceReference(state.source.identity.resource);
    if(retained)root.dataset.studyRetained=JSON.stringify({resource:retained,title:windowTitle(state),zoom:1,pan:[.5,.5]});
    root.dataset.staged = 'true'; // actual media preparation, independent of backend transaction staging
    root.id = `presentation-${uuid()}`; root.setAttribute('aria-label', state.title); root.setAttribute('role', 'region');
    Object.assign(root.style, { position: 'absolute', boxSizing: 'border-box' });
    const bar = document.createElement('header'), title = document.createElement('span'); title.className = 'workspace-window-title'; bar.append(title);
    const toolbar = document.createElement('div'); toolbar.className = 'workspace-window-tools'; toolbar.setAttribute('role', 'group'); toolbar.setAttribute('aria-label', 'Window controls'); bar.append(toolbar);
    Object.assign(bar.style, { height: `${CHROME}px`, boxSizing: 'border-box' });
    const viewport = document.createElement('div'); viewport.className = 'workspace-window-viewport';
    Object.assign(viewport.style, { position: 'absolute', left: '0px', top: `${CHROME}px`, overflow: 'hidden' });
    const picture = document.createElement('div'); picture.className = 'workspace-window-picture';
    Object.assign(picture.style, { position: 'absolute', overflow: 'hidden' });
    const caption = document.createElement('p'); caption.className = 'workspace-window-caption';
    const message = document.createElement('div'); message.className = 'workspace-window-message'; message.setAttribute('role', 'status');
    viewport.append(picture, message); toolbar.append(caption); root.append(bar, viewport); this.opts.host.append(root);
    const row: WindowRow = {
      state, root, bar, title, toolbar, viewport, picture, caption, message, controls: [], shell: null!,
      events: new AbortController(), mediaEvents: null, media: null, image: null,
      command: this.command(batch), view: copy(state.view), load: { state: 'empty', drawToken: null, naturalSize: null }, fit: null,
      opener: batch.op === 'open' && document.activeElement instanceof HTMLElement ? document.activeElement : null,
      pending: new Map(), expectedUrl: null, decodeJob: null, decodedToken: null, beforeUser: null, allowSmall: batch.op === 'resize', dismissed: false,
      sizeEstablished: state.geometryIntent.relation?.kind === 'beside' && !!state.geometryIntent.size,
      liveSample: null, heldSample: null, liveHeld: false, disposed: false, layoutStamp: null, viewPaint: null, queuedLive: null,
    };
    const controlsToggle = document.createElement('button');
    controlsToggle.type = 'button'; controlsToggle.className = 'workspace-window-options';
    controlsToggle.textContent = '⋯'; controlsToggle.setAttribute('aria-label', 'Picture controls');
    controlsToggle.setAttribute('aria-expanded', 'false'); toolbar.hidden = true;
    controlsToggle.addEventListener('click', () => {
      this.groupChoices(row);
      toolbar.hidden = !toolbar.hidden; controlsToggle.setAttribute('aria-expanded', String(!toolbar.hidden));
    });
    controlsToggle.addEventListener('pointerdown', event => event.stopPropagation());
    root.addEventListener('keydown', event => { if (event.key === 'Escape' && !toolbar.hidden) {
      event.stopPropagation(); toolbar.hidden = true; controlsToggle.setAttribute('aria-expanded', 'false'); controlsToggle.focus();
    } });
    bar.append(controlsToggle);
    toolbar.addEventListener('pointerdown', event => event.stopPropagation(), { signal: row.events.signal });
    toolbar.addEventListener('dblclick', event => event.stopPropagation(), { signal: row.events.signal });
    const background=this.opts.background?.(batch.requestId)===true;
    row.shell = this.opts.panels.register(new FloatingWindow({ id: state.id, root, bar, panels: this.opts.panels,
      close: () => this.dispatchUser({ action: 'close', instance: state.id, values: {} }),
      implicitFront: false, frontOnOpen: !background,
      freeResize: true,
      chromeHeight: CHROME,
      content: () => row.state.source.kind === 'written' || row.state.source.kind === 'lesson' ? { kind: 'text' } : { kind: 'picture', aspect: row.fit ? row.fit.crop.width / row.fit.crop.height : 4 / 3 },
      facts: this.opts.facts, onLayout: () => this.layoutMedia(row),
      onUserChange: (property) => this.shellTouch(row, property),
      onUserStart: () => this.holdGeometry(row),
    }),{background});
    bar.id = `move:${state.id}`; bar.dataset.control = 'move';
    row.controls.push({ node: bar, name: 'move', property: 'geometry', role: 'drag-handle' });
    const grip = root.querySelector<HTMLElement>('.fwin-grip')!; grip.id = `resize:${state.id}`; grip.dataset.control = 'resize';
    row.controls.push({ node: grip, name: 'resize', property: 'geometry', role: 'resize-handle' });
    this.button(row, 'zoom-in', 'Zoom in', 'zoom', () => this.dispatchUser({ action: 'zoom', instance: state.id, values: { factor: 2 } }));
    this.button(row, 'zoom-out', 'Zoom out', 'zoom', () => this.dispatchUser({ action: 'zoom', instance: state.id, values: { factor: 0.5 } }));
    this.button(row, 'zoom-fit', 'Fit picture', 'zoom', () => this.fitWindow(row));
    this.button(row, 'front', 'Bring to front', 'front', () => this.dispatchUser({ action: 'front', instance: state.id, values: {} }));
    this.button(row, 'hide', 'Hide window', 'hidden', () => this.dispatchUser({ action: 'hide', instance: state.id, values: {} }));
    const group = document.createElement('select'); group.id = `group:${state.id}`; group.dataset.control = 'group';
    group.setAttribute('aria-label', 'Group with another window'); group.title = 'Choose a window to move together';
    group.addEventListener('focus', () => this.groupChoices(row));
    group.addEventListener('change', () => {
      const companion = group.value; group.value = '';
      if (companion) this.dispatchUser({ action: 'group', instance: state.id, values: { targets: [state.id, companion] } });
    });
    row.toolbar.append(group); row.controls.push({ node: group, name: 'group', property: 'group', role: 'combobox' });
    this.groupChoices(row);
    this.button(row, 'ungroup', 'Separate grouped windows', 'group', () => this.dispatchUser({ action: 'ungroup', instance: state.id, values: {} }));
    const opacity = document.createElement('input'); opacity.type = 'range'; opacity.min = '0'; opacity.max = '1'; opacity.step = '0.05';
    opacity.id = `opacity:${state.id}`; opacity.setAttribute('aria-label', 'Window opacity'); opacity.title = 'Window opacity';
    opacity.addEventListener('input', () => { row.root.style.opacity = opacity.value; });
    opacity.addEventListener('change', () => this.dispatchUser({ action: 'opacity', instance: state.id, values: { value: Number(opacity.value) } }));
    row.toolbar.append(opacity); row.controls.push({ node: opacity, name: 'opacity', property: 'opacity', role: 'slider' });
    this.button(row, 'close', 'Close window', 'open', () => this.dispatchUser({ action: 'close', instance: state.id, values: {} }));
    const close = row.controls.find(control => control.name === 'close')!.node;
    close.classList.add('workspace-window-close'); row.bar.append(close);
    this.button(row, 'retry', 'Retry loading', 'source', () => { this.loadMedia(row); if (row.state.source.kind === 'live') this.opts.retryLive?.(row.state.id); });
    this.panControl(row, 'pan-x', 0); this.panControl(row, 'pan-y', 1);
    this.bindPan(row);
    this.reflect(row);
    const dimensions = state.delivery.originalSize;
    if (dimensions && pair(dimensions, true)) row.fit = fitPicture({ width: dimensions[0], height: dimensions[1],
      disc: this.discFor(row), bounds: { min: 240, usual: 360, max: 640 } });
    if (state.geometryIntent.position.some(n => n !== 0) || state.geometryIntent.size) this.setGeometry(row);
    else {
      const area = this.opts.host.getBoundingClientRect();
      const aspect = row.fit ? row.fit.crop.width / row.fit.crop.height : 4 / 3;
      const w = Math.min(area.width - 24, Math.max(240, 180 * aspect, Math.min(420, (area.width - 36) / 2)));
      row.shell.resizeTo(w, Math.min(Math.max(180, w / aspect) + CHROME + this.footerHeight(row), area.height));
      const facts = this.opts.facts();
      if (facts) {
        const height = row.root.getBoundingClientRect().height;
        const obstacles = this.opts.panels.obstacles(state.id).map(r =>
          [r.left - facts.canvas.left, r.top - facts.canvas.top, r.right - facts.canvas.left, r.bottom - facts.canvas.top] as [number, number, number, number]);
        const spot = placeAway({ area: facts.area, disc: facts.disc, object: facts.object,
          obstacles: [...obstacles, ...facts.labels], size: { width: w, chrome: 0, aspect: w / Math.max(1, height) } });
        row.shell.resizeTo(spot.w, height * spot.w / w);
        this.placeViewport(row, facts.canvas.left + spot.x, facts.canvas.top + spot.y);
      } else this.placeViewport(row, area.left + 12, Math.max(area.top, 0) + 12);
    }
    this.ensureUseful(row);
    row.shell.frame(); this.loadMedia(row);
    return row;
  }
  private command(batch: WindowBatch) { return { requestId: batch.requestId, commandId: batch.commandId, actionOccurrenceId: batch.actionOccurrenceId }; }
  private update(row: WindowRow, state: WindowState, batch: WindowBatch): void {
    const old = row.state;
    const returnFocus = row.root.contains(document.activeElement) && (!state.open || state.view.hidden === true);
    if (['open','restore'].includes(batch.op ?? '') && document.activeElement instanceof HTMLElement && !row.root.contains(document.activeElement)) row.opener = document.activeElement;
    const changedSource = state.sourceGeneration !== old.sourceGeneration || state.delivery.sourceToken !== old.delivery.sourceToken || canonicalJSON(state.source) !== canonicalJSON(old.source) || canonicalJSON(state.delivery.payloadBinding ?? null) !== canonicalJSON(old.delivery.payloadBinding ?? null) || state.delivery.url !== old.delivery.url;
    for (const [property, base] of row.pending) if (state.propertyVersions[property] !== base || changedSource) row.pending.delete(property);
    row.state = state; row.command = this.command(batch);
    if (batch.op === 'open' && state.propertyVersions.open > old.propertyVersions.open) row.dismissed = false;
    if (!row.pending.has('zoom')) row.view.zoom = state.view.zoom;
    if (!row.pending.has('pan')) row.view.pan = state.view.pan.slice();
    if (batch.op === 'resize') row.allowSmall = true;
    this.reflect(row);
    if (!row.pending.has('geometry') && state.propertyVersions.geometry !== old.propertyVersions.geometry) {
      const resized = batch.op === 'resize' || canonicalJSON(state.geometryIntent.size) !== canonicalJSON(old.geometryIntent.size);
      // A relation changes position only after its current-size fit is checked.
      // Restoring the descriptor's older absolute position first would itself
      // move a window even when the new relation is impossible.
      const moved = batch.op === 'move' || canonicalJSON(state.geometryIntent.position) !== canonicalJSON(old.geometryIntent.position);
      if (state.geometryIntent.relation?.kind !== 'beside' || resized) this.setGeometry(row, resized, moved);
    }
    row.shell.frame();
    if (returnFocus) this.restoreFocus(row);
    if (!state.open || row.dismissed) this.emptyMedia(row);
    else if (changedSource || !row.media) this.loadMedia(row);
    else { this.layoutMedia(row); if (row.load.state === 'loaded' || row.load.state === 'drawn') this.scheduleDraw(row, row.liveSample ?? row.heldSample); }
  }
  private reflect(row: WindowRow): void {
    const title = windowTitle(row.state);
    row.title.textContent = title; row.title.title = title; row.root.setAttribute('aria-label', title);
    if (!row.pending.has('open') && !row.pending.has('hidden')) row.root.hidden = row.dismissed || !row.state.open || !!row.state.view.hidden || row.root.dataset.phoneHidden === 'true';
    row.root.dataset.windowOpen = String(!row.dismissed && row.state.open);
    row.root.dataset.group = row.state.view.group ?? '';
    if (!row.pending.has('opacity')) row.root.style.opacity = String(row.state.view.opacity ?? 1);
    if (!row.state.open) row.root.remove();
    else if (!this.opts.host.contains(row.root)) this.opts.host.append(row.root);
    const pictureControls = ['saved-image', 'composite', 'live'].includes(row.state.source.kind) && ['retained', 'temporary'].includes(row.state.source.availability);
    for (const control of row.controls) {
      const pictureControl = control.property === 'zoom' || control.property === 'pan';
      (control.node as HTMLButtonElement).disabled = pictureControl && !pictureControls;
      if (pictureControl) control.node.hidden = !pictureControls;
    }
    const opacity = row.controls.find(c => c.name === 'opacity')?.node as HTMLInputElement | undefined;
    if (opacity) opacity.value = row.root.style.opacity;
    const separate = row.controls.find(c => c.name === 'ungroup')?.node; if (separate) separate.hidden = !row.state.view.group;
    const source = row.state.source;
    const captured = ['capture', 'raw', 'labelled', 'annotated', 'derived'].includes(source.media) && source.kind !== 'live';
    const label = `${captured ? 'Captured evidence' : source.media === 'reference' || source.media === 'external-reference' ? 'Reference image' : source.media === 'generated' ? 'Generated result' : source.kind === 'live' ? row.liveHeld ? 'Held image · tracking stopped' : 'Live view' : 'Research work'}${captured ? ' · shown on its captured view' : ''}`;
    const description = typeof source.provenance.description === 'string' ? source.provenance.description.slice(0, 600) : '';
    const web = source.provenance.web as { credit?: unknown; page?: unknown } | null;
    const credit = typeof web?.credit === 'string' ? web.credit.slice(0, 120) : '';
    // These are prepared producer facts, never HTML or a mutable view caption.
    const reference = source.media === 'reference' || source.media === 'external-reference';
    row.root.dataset.reference = String(reference);
    row.caption.textContent = reference ? (web?.page ? '' : 'Reference photo') : label;
    row.caption.title = [label, description, credit].filter(Boolean).join('\n');
    let badge = row.bar.querySelector<HTMLElement>('.window-kind');
    if (!badge) { badge = document.createElement('span'); badge.className = 'window-kind'; row.bar.append(badge); }
    badge.dataset.media = source.kind === 'live' ? 'live' : source.media;
    badge.textContent = source.kind === 'live' ? row.liveHeld ? 'Held' : 'Live' : reference ? 'Reference' : source.media === 'generated' ? 'AI illustration'
      : source.media === 'derived' ? 'Derived' : captured ? 'Captured' : 'Saved';
    let placement = row.bar.querySelector<HTMLElement>('.window-placement');
    if (!placement) { placement = document.createElement('span'); placement.className = 'window-placement'; row.bar.append(placement); }
    placement.hidden = !captured && source.kind !== 'live';
    placement.dataset.verified = 'false';
    placement.setAttribute('role', 'img');
    placement.title = row.liveHeld ? 'Last verified image · tracking stopped' : captured ? 'On its captured view' : 'Live position not currently confirmed';
    placement.setAttribute('aria-label', placement.title);
    let sourceDetails = row.toolbar.querySelector<HTMLElement>('.workspace-source-details');
    if (!sourceDetails) { sourceDetails = document.createElement('p'); sourceDetails.className = 'workspace-source-details'; row.toolbar.append(sourceDetails); }
    sourceDetails.textContent = row.caption.title;
    if (typeof web?.page === 'string' && /^https?:\/\//.test(web.page)) {
      const link = document.createElement('a'); link.href = web.page; link.textContent = reference ? new URL(web.page).hostname.replace(/^www\./, '') : 'Source'; link.target = '_blank'; link.rel = 'noopener noreferrer'; row.caption.append(' ', link);
    } else if (credit) {
      row.caption.append(credit);
    }
    row.caption.dataset.details = 'true';
  }
  private setGeometry(row: WindowRow, resize = true, move = true): void {
    const { position, size } = row.state.geometryIntent;
    // Position/relation updates retain decode's useful sizing. An explicit
    // resize still applies, including a size equal to the old descriptor.
    if (size && resize) row.shell.resizeTo(size[0], size[1]);
    if (move) this.placeViewport(row, position[0], position[1]);
  }
  /** Decorations read observed media and current scene checks; never affect completion receipts. */
  livePlacements(point: (source: WindowSource, sample: LiveSample | null) => [number, number] | null):
    { point: [number, number]; rect: number[] }[] {
    const links: { point: [number, number]; rect: number[] }[] = [];
    for (const row of this.rows.values()) {
      if (row.state.source.kind !== 'live') continue;
      const at = row.load.state === 'drawn' && row.liveSample && this.viewVisible(row)
        ? point(row.state.source, row.liveSample) : null;
      const dot = row.bar.querySelector<HTMLElement>('.window-placement');
      if (dot && dot.dataset.verified !== String(!!at)) {
        dot.dataset.verified = String(!!at);
        dot.title = row.liveHeld ? 'Last verified image · tracking stopped' : at ? 'Still in place on the live view' : 'Live position not currently confirmed';
        dot.setAttribute('aria-label', dot.title);
      }
      if (at) links.push({ point: at, rect: rect(row.root.getBoundingClientRect()) });
    }
    return links;
  }
  private placeViewport(row: WindowRow, x: number, y: number): void {
    const host = this.opts.host.getBoundingClientRect();
    row.shell.moveTo(x - host.left, y - host.top);
  }
  private button(row: WindowRow, name: string, label: string, property: string, handler: () => void): void {
    const button = document.createElement('button'); button.setAttribute('type', 'button'); button.dataset.control = name;
    const registered = name === 'zoom-in' ? 'zoom' : name === 'close' || name === 'front' ? name : null;
    button.id = registered ? `${registered}:${row.state.id}` : `${row.root.id}-${name}`;
    button.setAttribute('aria-label', label); button.title = label;
    button.textContent = buttonLabels[name] ?? label;
    button.addEventListener('click', () => { if (!button.disabled) handler(); }, { signal: row.events.signal }); row.toolbar.append(button);
    row.controls.push({ node: button, name, property, role: 'button' });
  }
  private fitWindow(row: WindowRow): void {
    if (!row.fit || !row.image) return;
    const aspect = row.fit.crop.width / row.fit.crop.height;
    const box = row.root.getBoundingClientRect(), room = this.opts.host.getBoundingClientRect();
    const heightRoom = Math.max(100, room.height - CHROME - 24);
    const width = Math.min(box.width, Math.max(160, room.width - 24), heightRoom * aspect);
    this.dispatchUser({ action: 'zoom', instance: row.state.id, values: { fit: true, size: [width, width / aspect + CHROME] } });
  }
  private panControl(row: WindowRow, name: string, axis: number): void {
    const control = document.createElement('input'); control.setAttribute('type', 'range'); control.setAttribute('min', '0'); control.setAttribute('max', '1'); control.setAttribute('step', '0.001');
    control.dataset.control = name; control.id = `${name}:${row.state.id}`; control.setAttribute('aria-label', axis === 0 ? 'Horizontal source position' : 'Vertical source position');
    control.addEventListener('change', () => { const pan = row.view.pan.slice(); pan[axis] = clampUnit(Number(control.value)); this.dispatchUser({ action: 'pan', instance: row.state.id, values: { center: pan } }); }, { signal: row.events.signal });
    row.toolbar.append(control); row.controls.push({ node: control, name, property: 'pan', role: 'slider' });
  }
  private shellTouch(row: WindowRow, property: string): void {
    if (row.disposed) return;
    const actual = row.root.getBoundingClientRect();
    if (property === 'move' || property === 'geometry') this.emit(row, 'move', { position: [actual.left, actual.top] }, 'geometry');
    else if (property === 'resize') { row.allowSmall = true; this.emit(row, 'resize', { size: [actual.width, actual.height] }, 'geometry'); }
    else if (property === 'front') this.emit(row, 'front', {}, 'front');
  }
  private groupChoices(row: WindowRow): void {
    const chooser = row.controls.find(control => control.name === 'group')?.node as HTMLSelectElement | undefined;
    if (!chooser) return;
    const empty = document.createElement('option'); empty.value = ''; empty.textContent = 'Group with…';
    chooser.replaceChildren(empty);
    for (const other of this.rows.values()) {
      if (other === row || other.disposed || !other.state.open || other.root.hidden || other.dismissed
          || !Number.isSafeInteger(other.state.propertyVersions.group)
          || row.state.view.group && row.state.view.group === other.state.view.group) continue;
      const option = document.createElement('option'); option.value = other.state.id;
      option.textContent = `${other.state.title} · ${other.state.id.slice(-6)}`; chooser.append(option);
    }
    chooser.value = ''; chooser.disabled = chooser.children.length < 2 || !Number.isSafeInteger(row.state.propertyVersions.group);
  }
  private emit(row: WindowRow, action: string, values: Record<string, unknown>, property: string): void {
    if (!Number.isSafeInteger(row.state.propertyVersions[property])) throw failure('ownership_unavailable', 'Control has no authoritative property fence');
    // Front has no drag preview to preserve. Keep its settled pre-command paint
    // observable until the server admits the new order, so manual confirmation
    // has the same real before/after evidence as a spoken or typed command.
    if (action !== 'front') row.pending.set(property, row.state.propertyVersions[property]);
    if (row.load.state === 'loaded') this.scheduleDraw(row, row.liveSample ?? row.heldSample);
    const commandId = `manual-${uuid()}`, requestId = `${this.opts.context.appRunId}:manual-${uuid()}`, actionOccurrenceId = `manual-action-${uuid()}`;
    this.opts.send({ type: 'present', action, instance: row.state.id, values: copy(values), context: copy(this.opts.context), requestId, commandId, actionOccurrenceId,
      base: { sourceGeneration: row.state.sourceGeneration, lifetimeGeneration: row.state.lifetimeGeneration, propertyVersions: { ...row.state.propertyVersions } } });
  }
  private restoreFocus(row: WindowRow): void {
    const visible = (node: HTMLElement | null | undefined) => node?.isConnected && !node.closest('[hidden], [data-result-tucked="true"]') && node.getClientRects().length > 0;
    const target = visible(row.opener) ? row.opener : this.opts.focusFallback?.();
    if (visible(target)) target?.focus({preventScroll:true});
  }
  dispatchUser(command: { action: string; instance: string; values: Record<string, unknown> }): void {
    const row = this.row(command.instance), { action, values } = command;
    if (action === 'group') {
      const targets = values.targets;
      if (!Array.isArray(targets) || targets.length < 2 || targets.length > 8 || targets[0] !== row.state.id
          || targets.some(id => typeof id !== 'string') || new Set(targets).size !== targets.length) throw failure('invalid_action', 'Choose distinct windows to group');
      const participants = targets.map(id => this.row(id));
      if (participants.some(item => !item.state.open || item.root.hidden || item.dismissed
          || !Number.isSafeInteger(item.state.propertyVersions.group))) throw failure('ownership_unavailable', 'A chosen window is no longer available for grouping');
      const base = (item: WindowRow) => ({ sourceGeneration: item.state.sourceGeneration,
        lifetimeGeneration: item.state.lifetimeGeneration, propertyVersions: { ...item.state.propertyVersions } });
      this.opts.send({ type: 'present', action, instance: row.state.id, values: {}, context: copy(this.opts.context),
        requestId: `${this.opts.context.appRunId}:manual-${uuid()}`, commandId: `manual-${uuid()}`, actionOccurrenceId: `manual-action-${uuid()}`,
        base: base(row), companions: participants.slice(1).map(item => ({ instance: item.state.id, base: base(item) })) });
      return; // grouping is rendered only after authoritative admission
    }
    const property = action === 'move' || action === 'resize' ? 'geometry' : action === 'close' ? 'open' : action === 'hide' || action === 'restore' ? 'hidden' : action === 'ungroup' ? 'group' : action;
    if (!Number.isSafeInteger(row.state.propertyVersions[property])) throw failure('ownership_unavailable', 'Control has no authoritative property fence');
    if (action === 'zoom' || action === 'pan') {
      if (!pictureSources.has(row.state.source.kind)) throw failure('unsupported_action', 'This content does not implement picture view controls');
      if (!row.image || !['retained', 'temporary'].includes(row.state.source.availability)) throw failure(row.load.errorCode ?? 'source_unavailable', row.load.error ?? 'Picture bytes are unavailable for this view');
    }
    if (action === 'zoom') {
      const zoom = values.fit === true ? 1 : typeof values.level === 'number' ? values.level : row.view.zoom * Number(values.factor);
      if (!Number.isFinite(zoom) || zoom <= 0) throw failure('invalid_action', 'Invalid zoom');
      if (values.fit === true && pair(values.size, true)) {
        this.holdGeometry(row); row.allowSmall = true; row.shell.resizeTo(values.size[0], values.size[1]);
        row.pending.set('geometry', row.state.propertyVersions.geometry);
      }
      row.view.zoom = zoom; if (values.fit === true) row.view.pan = [0.5, 0.5]; this.layoutMedia(row); this.emit(row, action, values, 'zoom');
      if (values.fit === true) row.pending.set('pan', row.state.propertyVersions.pan);
    } else if (action === 'pan') {
      if (!pair(values.center) || values.center.some((v) => v < 0 || v > 1)) throw failure('invalid_action', 'Invalid source center');
      row.view.pan = values.center.slice(); this.layoutMedia(row); this.emit(row, action, values, 'pan');
    } else if (action === 'close') {
      const focused = row.root.contains(document.activeElement);
      row.dismissed = true; row.root.hidden = true; row.root.dataset.windowOpen = 'false'; this.emptyMedia(row);
      if (focused) this.restoreFocus(row);
      this.emit(row, action, values, 'open');
    }
    else if (action === 'hide' || action === 'restore') {
      const focused = row.root.contains(document.activeElement);
      row.root.hidden = action === 'hide';
      if (action === 'hide' && focused) this.restoreFocus(row);
      this.emit(row, action, values, 'hidden');
    }
    else if (action === 'opacity') { row.root.style.opacity = String(values.value); this.emit(row, action, values, 'opacity'); }
    else if (action === 'ungroup') { this.emit(row, action, values, 'group'); }
    else if (action === 'front') { this.emit(row, action, values, 'front'); }
    else if (action === 'move' && pair(values.position)) { this.holdGeometry(row); this.placeViewport(row, values.position[0], values.position[1]); this.emit(row, action, values, 'geometry'); }
    else if (action === 'resize' && pair(values.size, true)) { this.holdGeometry(row); row.allowSmall = true; row.shell.resizeTo(values.size[0], values.size[1]); this.emit(row, action, values, 'geometry'); }
    else throw failure('unsupported_action', 'Window action is not available');
    if (action === 'restore' || action === 'front') this.phoneActive = row.state.id;
    this.syncPhone();
  }
  /** Keep where the window is before the user's geometry change, unless an earlier one is still unanswered. */
  private holdGeometry(row: WindowRow): void {
    if (!row.pending.has('geometry')) row.beforeUser = rect(row.root.getBoundingClientRect());
  }
  rejectManual(instance: string, reason?: string): void {
    const row = this.rows.get(instance); if (!row || row.disposed) return;
    if(this.restoring.delete(instance))this.restoreFailures.set(instance,reason?.slice(0,1000)||'The saved view could not be restored. Retry to reopen it.');
    const reopen = row.pending.has('open'), geometry = row.pending.has('geometry'); row.pending.clear();
    row.view = { zoom: row.state.view.zoom, pan: row.state.view.pan.slice() };
    this.reflect(row);
    // A refusal puts back only the geometry the user changed, where the window was before it: never the descriptor's
    // never-set 0,0 of a window the studio placed itself (live 2026-10-11: refused resizes jumped it to the top-left).
    const before = row.beforeUser; row.beforeUser = null;
    if (geometry && before) { row.shell.resizeTo(before[2], before[3]); this.placeViewport(row, before[0], before[1]); }
    else if (geometry) this.setGeometry(row, true, row.state.geometryIntent.position.some(n => n !== 0));
    this.layoutMedia(row);
    if (reopen && !row.dismissed) this.loadMedia(row);
  }
  private bindPan(row: WindowRow): void {
    row.viewport.addEventListener('pointerdown', (event) => {
      if (!row.image || !row.fit) return;
      row.viewport.setPointerCapture(event.pointerId);
      const start = row.view.pan.slice(), box = row.picture.getBoundingClientRect(), zoom = row.view.zoom;
      const gesture = new AbortController();
      const abort = () => gesture.abort(); row.events.signal.addEventListener('abort', abort, { once: true });
      row.viewport.addEventListener('pointermove', (next) => {
        if (next.pointerId !== event.pointerId) return;
        row.view.pan = [clampUnit(start[0] - (next.clientX - event.clientX) / Math.max(1, box.width * zoom)), clampUnit(start[1] - (next.clientY - event.clientY) / Math.max(1, box.height * zoom))]; this.layoutMedia(row);
      }, { signal: gesture.signal });
      const end = () => { gesture.abort(); row.events.signal.removeEventListener('abort', abort); this.emit(row, 'pan', { center: row.view.pan.slice() }, 'pan'); };
      row.viewport.addEventListener('pointerup', end, { signal: gesture.signal }); row.viewport.addEventListener('pointercancel', end, { signal: gesture.signal });
    }, { signal: row.events.signal });
  }
  private loadMedia(row: WindowRow): void {
    row.decodeJob = null; row.decodedToken = null; row.liveSample = null; row.heldSample = null; row.liveHeld = false; row.mediaEvents?.abort(); row.mediaEvents = new AbortController();
    row.root.dataset.staged = 'true';
    row.picture.replaceChildren(); row.media = null; row.image = null; row.fit = null; row.expectedUrl = null;
    row.load = { state: 'empty', drawToken: null, naturalSize: null }; row.message.hidden = false;
    if (!row.state.open || row.pending.has('open') && row.root.hidden) { row.message.hidden = true; return; }
    const { source, delivery } = row.state;
    if (!['retained', 'temporary'].includes(source.availability)) {
      this.mediaError(row, `Source availability: ${source.availability}`, 'source_unavailable'); return;
    }
    if (source.kind === 'written') {
      const resource = source.identity.resource as {day?: unknown; id?: unknown; version?: unknown; outputId?: unknown} | undefined;
      const basis = delivery.report?.findingRevision;
      const openCapture = this.opts.openCapture && typeof resource?.day === 'string' && typeof resource.id === 'string' && basis
        ? (sequence: number, picture: number) => {
          const selected = {day: resource.day as string, rid: resource.id as string,
            findingVersion: basis.version, findingDigest: basis.digest, sequence, picture};
          if (row.disposed || row.state.delivery.sourceToken !== delivery.sourceToken)
            return Promise.reject(new Error('This report changed; choose the frame from the displayed findings'));
          return this.opts.openCapture!(selected, row.state.id);
        } : undefined;
      const capturePreview = typeof resource?.day === 'string' && typeof resource.id === 'string' && basis
        ? (sequence: number, picture: number) => researchCaptureImageUrl({day: resource.day as string,
          rid: resource.id as string, findingVersion: basis.version, findingDigest: basis.digest, sequence, picture}) : undefined;
      const text = delivery.report ? renderResearchReport(delivery.report, undefined, openCapture, capturePreview) : document.createElement('pre');
      text.classList.add('workspace-window-written');
      if (!delivery.report) text.textContent = delivery.text ?? '';
      if (typeof resource?.day === 'string' && typeof resource.id === 'string' && basis) {
        const query = new URLSearchParams({day:resource.day,findings:String(basis.version),digest:basis.digest});
        if (typeof resource.outputId === 'string') {
          query.set('output',resource.outputId);
          if (typeof resource.version === 'number') query.set('outputVersion',String(resource.version));
        }
        const full = document.createElement('a'); full.className = 'mm-control'; full.textContent = 'See full research';
        full.href = `#research/${encodeURIComponent(resource.id)}?${query}`; text.prepend(full);
      }
      row.picture.append(text); row.media = text; row.decodedToken = delivery.sourceToken;
      row.load.state = 'loaded'; row.message.hidden = true; this.layoutMedia(row); this.scheduleDraw(row); return;
    }
    if (source.kind === 'live') { row.load.state = 'loading'; row.message.textContent = 'Waiting for the camera sample…'; return; }
    if (!delivery.url) { this.mediaError(row, 'Prepared source delivery is unavailable', 'source_unavailable'); return; }
    row.expectedUrl = urlKey(delivery.url); row.load.state = 'loading'; row.message.textContent = 'Loading…';
    const job = {}; row.decodeJob = job;
    if (source.kind === 'video') {
      const video = document.createElement('video'); video.controls = true; video.preload = 'metadata'; video.src = delivery.url;
      Object.assign(video.style, { width: '100%', height: '100%', objectFit: 'contain' }); row.media = video; row.picture.append(video);
      video.addEventListener('loadeddata', () => {
        if (!this.current(row, job) || urlKey(video.currentSrc) !== row.expectedUrl) return;
        row.load = { state: 'loaded', drawToken: null, naturalSize: [video.videoWidth, video.videoHeight] };
        row.fit = fitPicture({ width: video.videoWidth, height: video.videoHeight, bounds: { min: 240, usual: 360, max: 640 } });
        row.message.hidden = true; this.layoutMedia(row); this.scheduleDraw(row);
      }, { signal: row.mediaEvents.signal });
      video.addEventListener('error', () => { if (this.current(row, job)) this.mediaError(row, 'Video could not load. Retry the source.', 'decode_failed'); }, { signal: row.mediaEvents.signal });
      return;
    }
    if (source.kind === 'lesson') {
      const frame = document.createElement('iframe'); frame.setAttribute('sandbox', 'allow-scripts'); frame.title = row.state.title; frame.src = delivery.url;
      bindArtifactEscape(frame, () => row.controls.find(control => control.name === 'close')?.node.click());
      Object.assign(frame.style, { width: '100%', height: '100%', border: '0' }); row.media = frame; row.picture.append(frame);
      frame.addEventListener('load', () => { if (this.current(row, job)) { row.load.state = 'loaded'; row.message.hidden = true; } }, { signal: row.mediaEvents.signal });
      frame.addEventListener('error', () => { if (this.current(row, job)) this.mediaError(row, 'Lesson could not load.', 'decode_failed'); }, { signal: row.mediaEvents.signal });
      this.layoutMedia(row); return; // inner lesson pixels are not certified from an iframe load event
    }
    const image = document.createElement('img'); image.alt = row.state.title; image.draggable = false;
    Object.assign(image.style, { position: 'absolute', maxWidth: 'none', width: '100%', left: '0%', top: '0%', height: 'auto', transformOrigin: '0 0' });
    row.image = image; row.media = image; row.picture.append(image); image.src = delivery.url; this.layoutMedia(row);
    image.decode().then(() => {
      if (!this.current(row, job)) return;
      if (!this.correctImage(row)) { this.mediaError(row, 'Decoded source differs from its delivery.', 'source_mismatch'); return; }
      row.decodedToken = delivery.sourceToken;
      const disc = this.discFor(row);
      row.fit = fitPicture({ width: image.naturalWidth, height: image.naturalHeight, disc, bounds: { min: 240, usual: 360, max: 640 } });
      row.load = { state: 'loaded', drawToken: null, naturalSize: [image.naturalWidth, image.naturalHeight] };
      row.message.hidden = true; fitPanel(row.root, row.picture, image, disc); this.ensureUseful(row); this.layoutMedia(row); this.scheduleDraw(row);
    }, () => { if (this.current(row, job)) this.mediaError(row, 'Picture could not decode. Retry the source.', 'decode_failed'); });
  }
  private current(row: WindowRow, job: object): boolean { return !this.disposed && !row.disposed && row.decodeJob === job; }
  private correctImage(row: WindowRow): boolean {
    const image = row.image;
    return !!image && image.naturalWidth > 0 && image.naturalHeight > 0 && !!image.currentSrc && urlKey(image.currentSrc) === row.expectedUrl;
  }
  private discFor(row: WindowRow): Disc | null {
    const media = row.state.source.media, disc = row.state.delivery.disc;
    if (!['capture', 'raw', 'labelled', 'annotated', 'derived'].includes(media) || !disc || disc.length !== 3) return null;
    return [disc[0], disc[1], disc[2]];
  }
  private mediaError(row: WindowRow, detail: string, code: string): void {
    row.decodedToken = null;
    row.load = { state: 'error', drawToken: null, naturalSize: null, error: detail, errorCode: code };
    row.liveSample = null; row.heldSample = null; row.message.hidden = false; row.message.textContent = detail;
  }
  private emptyMedia(row: WindowRow): void {
    row.decodeJob = null; row.decodedToken = null; row.liveSample = null; row.heldSample = null; row.liveHeld = false; row.mediaEvents?.abort(); row.picture.replaceChildren();
    row.image = null; row.media = null; row.fit = null; row.expectedUrl = null;
    row.load = { state: 'empty', drawToken: null, naturalSize: null }; row.message.hidden = true;
    if (row.root.hidden) row.root.remove();
  }
  private ensureUseful(row: WindowRow): void {
    if (row.allowSmall || row.sizeEstablished || !row.fit) return;
    const aspect = row.fit.crop.width / row.fit.crop.height, box = row.root.getBoundingClientRect();
    const width = Math.max(box.width, 240, Math.ceil(180 * aspect));
    const footer = this.footerHeight(row);
    const height = Math.max(box.height, 180 + CHROME + footer, Math.ceil(240 / aspect) + CHROME + footer);
    if (width !== box.width || height !== box.height) row.shell.resizeTo(width, height);
    row.sizeEstablished = true;
  }
  private footerHeight(_row: WindowRow): number {
    return 0;
  }
  private layoutMedia(row: WindowRow): void {
    if (row.disposed) return;
    if (row.fit && !['written', 'lesson'].includes(row.state.source.kind)) {
      const width = row.root.getBoundingClientRect().width;
      row.root.style.setProperty('--phone-window-height', `${width * row.fit.crop.height / row.fit.crop.width + CHROME}px`);
    } else row.root.style.removeProperty('--phone-window-height');
    const outer = row.root.getBoundingClientRect(), width = outer.width, height = Math.max(0, outer.height - CHROME - this.footerHeight(row));
    Object.assign(row.viewport.style, { width: `${width}px`, height: `${height}px` });
    const crop = row.fit?.crop ?? { x: 0, y: 0, width: 1, height: 1 };
    const isPicture = row.state.source.kind !== 'written' && row.state.source.kind !== 'lesson';
    const pictureWidth = row.fit && isPicture ? Math.min(width, height * crop.width / crop.height) : width;
    const pictureHeight = row.fit && isPicture ? pictureWidth * crop.height / crop.width : height;
    Object.assign(row.picture.style, { width: `${pictureWidth}px`, height: `${pictureHeight}px`, left: `${(width - pictureWidth) / 2}px`, top: `${(height - pictureHeight) / 2}px` });
    if (row.image) {
      const zoom = row.view.zoom, scale = pictureWidth / crop.width;
      const tx = pictureWidth / 2 - (crop.x + crop.width * row.view.pan[0]) * scale * zoom + crop.x * scale;
      const ty = pictureHeight / 2 - (crop.y + crop.height * row.view.pan[1]) * scale * zoom + crop.y * scale;
      row.image.style.transform = `matrix(${zoom}, 0, 0, ${zoom}, ${tx}, ${ty})`;
    }
    for (const control of row.controls) if (control.role === 'slider' && control.property === 'pan') (control.node as HTMLInputElement).value = String(row.view.pan[control.name === 'pan-x' ? 0 : 1]);
    const stamp = JSON.stringify([rect(row.root.getBoundingClientRect()), row.viewport.style.width, row.viewport.style.height, row.picture.style.width, row.picture.style.height, row.picture.style.left, row.picture.style.top, row.image?.style.transform]);
    const changed = stamp !== row.layoutStamp; row.layoutStamp = stamp;
    if (changed && row.load.state === 'drawn') { const sample = row.liveSample ?? row.heldSample; row.heldSample = null; row.load.state = 'loaded'; row.load.drawToken = null; row.liveSample = null; this.scheduleDraw(row, sample); }
  }
  private observedZ(row: WindowRow): number | null {
    const value = getComputedStyle(row.root).zIndex;
    return value && Number.isFinite(Number(value)) ? Number(value) : null;
  }
  private viewBinding(row: WindowRow): string {
    return JSON.stringify([row.command, row.state.revision, row.state.sourceGeneration, row.state.lifetimeGeneration,
      canonicalJSON(row.state.source), row.state.delivery.sourceToken, canonicalJSON(row.state.delivery.payloadBinding ?? null),
      canonicalJSON(row.state.propertyVersions), canonicalJSON(row.state.propertyOwners), row.state.open, row.state.staged]);
  }
  private paintedView(row: WindowRow): string {
    return JSON.stringify([rect(row.root.getBoundingClientRect()), rect(row.picture.getBoundingClientRect()), this.observedZ(row),
      row.image ? matrix(row.image) : null, this.observedView(row), row.load.naturalSize,
      canonicalJSON(row.state.source), row.state.delivery.sourceToken, canonicalJSON(row.state.delivery.payloadBinding ?? null), row.state.sourceGeneration, row.state.lifetimeGeneration]);
  }
  private viewVisible(row: WindowRow): boolean {
    const box = row.picture.getBoundingClientRect(), outer = row.root.getBoundingClientRect(), style = getComputedStyle(row.root);
    return connected(row.root, this.opts.host) && !row.root.hidden && document.visibilityState !== 'hidden'
      && outer.width > 0 && outer.height > 0 && box.width > 0 && box.height > 0
      && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0';
  }
  private viewDrawRef(row: WindowRow): string | null {
    const paint = row.viewPaint;
    if (!paint || row.pending.size || row.state.staged === true || row.root.dataset.staged !== 'false'
      || row.load.state !== 'drawn' || !this.viewVisible(row) || paint.job !== row.decodeJob || paint.media !== row.media
      || paint.binding !== this.viewBinding(row) || paint.view !== this.paintedView(row)) return null;
    return paint.ref;
  }
  private scheduleDraw(row: WindowRow, sample: LiveSample | null = null, settledFront = false): void {
    const drawnSample = sample === null ? null : Object.freeze({ ...sample });
    const job = row.decodeJob, image = row.image, media = row.media, binding = this.viewBinding(row), view = this.paintedView(row);
    requestAnimationFrame(() => this.paint(row, drawnSample, settledFront, job, image, media, binding, view));
  }
  private paint(row: WindowRow, drawnSample: LiveSample | null, settledFront: boolean, job: object | null,
    image: HTMLImageElement | null, media: HTMLElement | null, binding: string, view: string): void {
    if (this.disposed || row.disposed || row.decodeJob !== job || row.image !== image || row.media !== media
      || this.viewBinding(row) !== binding || !['loaded', 'drawn'].includes(row.load.state)) return;
    // Layout can move between decode and its first animation frame. Retry that
    // same decoded source/sample; a changed source or command still cancels above.
    if (this.paintedView(row) !== view) { this.scheduleDraw(row, drawnSample, settledFront); return; }
    if (image && !this.correctImage(row)) { this.mediaError(row, 'Draw source differs from its delivery.', 'source_mismatch'); return; }
    if (!this.viewVisible(row)) return;
    // A front command can already be satisfied. Its fresh settled frame still
    // observes actual order, tied to this admitted occurrence. Other view
    // commands continue to require changed pixels/geometry; replay never enters here.
    if (row.load.state === 'loaded') { row.load.state = 'drawn'; row.load.drawToken = `draw-${uuid()}`; row.liveSample = row.liveHeld ? null : drawnSample; row.heldSample = row.liveHeld ? drawnSample : null; }
    row.root.dataset.staged = row.state.staged === true ? 'true' : 'false';
    if (!row.pending.size && row.state.staged !== true && this.observedZ(row) !== null && (!image || this.observedView(row) !== null)) {
      if (!row.viewPaint || row.viewPaint.view !== view || settledFront && row.viewPaint.binding !== binding) row.viewPaint = { ref: `view-${uuid()}`, view, binding, job, media };
      else if (row.viewPaint.binding === binding) row.viewPaint = { ...row.viewPaint, job, media };
    }
  }
  failLiveSource(id: string, token: string, generation: number, lifetime: string, detail: string): void {
    const row = this.rows.get(id);
    if (!row || row.state.source.kind !== 'live' || row.state.delivery.sourceToken !== token
      || row.state.sourceGeneration !== generation || row.state.lifetimeGeneration !== lifetime) return;
    this.mediaError(row, detail, 'source_unavailable');
  }
  /** A camera adapter supplies a decoded frame and its own actual sample, never a latest-hub guess. */
  drawLiveFrame(id: string, frame: LiveFrame): void {
    const row = this.row(id);
    if (row.dismissed || !row.state.open) return;
    const sample = admittedSample(frame.sample, this.opts.context.appRunId);
    if (!this.liveBinds(row, frame)) throw failure('source_mismatch', 'Live draw does not belong to this binding');
    if (!frame.image.complete || !frame.image.naturalWidth || !frame.image.naturalHeight || !frame.image.currentSrc) throw failure('not_decoded', 'Live frame is not decoded');
    // A window already painted keeps its painted frame, and says so, until the next
    // animation frame swaps the newest one in: frames arriving faster than the
    // browser paints never leave it unconfirmed (a busy or background browser).
    if (row.load.state === 'drawn' && row.state.staged !== true) {
      const waiting = row.queuedLive !== null; row.queuedLive = { frame, sample };
      if (!waiting) requestAnimationFrame(() => this.flushLive(row));
      return;
    }
    row.queuedLive = null; this.applyLive(row, frame); this.scheduleDraw(row, sample);
  }
  private liveBinds(row: WindowRow, frame: LiveFrame): boolean {
    return row.state.source.kind === 'live' && frame.sourceToken === row.state.delivery.sourceToken
      && frame.sourceGeneration === row.state.sourceGeneration && frame.lifetimeGeneration === row.state.lifetimeGeneration;
  }
  private flushLive(row: WindowRow): void {
    const queued = row.queuedLive; row.queuedLive = null;
    if (!queued || this.disposed || row.disposed || row.dismissed || !row.state.open || !this.liveBinds(row, queued.frame)) return;
    if (row.load.state !== 'drawn') { this.applyLive(row, queued.frame); this.scheduleDraw(row, queued.sample); return; }
    // Swapped and painted inside this one animation frame, so no report sees it half done.
    this.applyLive(row, queued.frame);
    this.paint(row, Object.freeze({ ...queued.sample }), false, row.decodeJob, row.image, row.media, this.viewBinding(row), this.paintedView(row));
  }
  private applyLive(row: WindowRow, frame: LiveFrame): void {
    row.decodeJob = {}; row.root.dataset.staged = 'true'; row.image = frame.image; row.media = frame.image; row.expectedUrl = urlKey(frame.image.currentSrc); row.picture.replaceChildren(frame.image);
    Object.assign(frame.image.style, { position: 'absolute', maxWidth: 'none', width: '100%', left: '0%', top: '0%', height: 'auto', transformOrigin: '0 0' });
    row.fit = fitPicture({ width: frame.image.naturalWidth, height: frame.image.naturalHeight, disc: this.discFor(row), bounds: { min: 240, usual: 360, max: 640 } });
    row.load = { state: 'loaded', drawToken: null, naturalSize: [frame.image.naturalWidth, frame.image.naturalHeight] }; row.liveSample = null; row.heldSample = null; row.liveHeld = frame.held === true; this.reflect(row);
    row.message.hidden = true; fitPanel(row.root, row.picture, frame.image, this.discFor(row)); this.layoutMedia(row);
  }
  private arrange(states: WindowState[]): void {
    for (const state of states) {
      const row = this.rows.get(state.id), relation = state.geometryIntent.relation;
      if (!row || row.pending.has('geometry') || relation?.kind !== 'beside' || !relation.reference) continue;
      const reference = this.rows.get(relation.reference); if (!reference) continue;
      const box = reference.root.getBoundingClientRect(), target = row.root.getBoundingClientRect(), host = this.opts.host.getBoundingClientRect();
      const viewport = window.visualViewport;
      const area = windowPlacementArea(host, [viewport?.offsetLeft ?? 0, viewport?.offsetTop ?? 0,
        viewport?.width ?? window.innerWidth ?? host.width, viewport?.height ?? window.innerHeight ?? host.height]);
      const gap = relation.gapPx ?? 12, side = relation.side ?? 'right';
      const x = side === 'left' ? box.left - gap - target.width : box.right + gap, y = box.top;
      // Keep actual dimensions/reference and never let ordinary window clamping
      // substitute an overlapping result for an impossible beside relation.
      if (relation.align && relation.align !== 'top' || !['left', 'right'].includes(side)
          || !Number.isFinite(gap) || gap < 0 || !area || ![x, y, target.width, target.height].every(Number.isFinite)
          || target.width <= 0 || target.height <= 0 || reference.root.hidden
          || x < area[0] || y < area[1] || x + target.width > area[0] + area[2] || y + target.height > area[1] + area[3]) continue;
      row.shell.moveTo(x - host.left, y - host.top);
    }
  }
  private observedView(row: WindowRow): { zoom: number; pan: number[] } | null {
    if (!row.image) return null;
    const applied = matrix(row.image); if (!applied || applied[0] <= 0 || applied[3] !== applied[0] || applied[1] !== 0 || applied[2] !== 0) return null;
    if (!row.image.naturalWidth || !row.image.naturalHeight) {
      // Before decode, controls/matrix are observable; source-pixel geometry is not.
      const pan = ['pan-x', 'pan-y'].map((name) => Number((row.controls.find((control) => control.name === name)?.node as HTMLInputElement | undefined)?.value));
      return pan.every(Number.isFinite) ? { zoom: applied[0], pan } : null;
    }
    const box = row.picture.getBoundingClientRect(), crop = row.fit?.crop ?? { x: 0, y: 0, width: 1, height: 1 }, geometry = imageGeometry(row);
    if (!geometry) return null;
    const tx = geometry.left + applied[4], ty = geometry.top + applied[5];
    return { zoom: applied[0], pan: [((box.width / 2 - tx) / (geometry.scaleX * applied[0]) - crop.x) / crop.width, ((box.height / 2 - ty) / (geometry.scaleY * applied[0]) - crop.y) / crop.height] };
  }
  facts() {
    return [...this.rows.values()].map((row) => {
      if (row.image && ['loaded', 'drawn'].includes(row.load.state) && !this.correctImage(row)) this.mediaError(row, 'Current image differs from its delivery.', 'source_mismatch');
      const outer = row.root.getBoundingClientRect(), picture = row.picture.getBoundingClientRect();
      const isConnected = connected(row.root, this.opts.host), style = getComputedStyle(row.root);
      // Shown in the studio's layout, and on the user's display: two facts. A studio in the background (another app or
      // tab in front) paints nothing, so nothing in it is visible; what is laid out shown there is 'tab-hidden'.
      const laidOut = isConnected && !row.root.hidden && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0' && outer.width > 0 && outer.height > 0;
      const visible = laidOut && document.visibilityState !== 'hidden';
      const actualView = this.observedView(row), applied = row.image ? matrix(row.image) : null, geometry = imageGeometry(row);
      let viewTransform = null;
      if (geometry && applied && row.load.naturalSize) {
        const sourceToContent = [geometry.scaleX * applied[0], geometry.scaleX * applied[1], geometry.scaleY * applied[2], geometry.scaleY * applied[3], geometry.left + applied[4], geometry.top + applied[5]];
        viewTransform = { sourceToContent, contentOrigin: [picture.left, picture.top], sourceToWorkspace: [...sourceToContent.slice(0, 4), sourceToContent[4] + picture.left, sourceToContent[5] + picture.top], originalSize: row.load.naturalSize.slice(), transformRevision: row.state.revision };
      }
      const relation = row.state.geometryIntent.relation, reference = relation?.reference ? this.rows.get(relation.reference)?.root.getBoundingClientRect() : null;
      const besideConflict = relation?.kind === 'beside' && (!reference
        || Math.abs(outer.top - reference.top) > 2 || (relation.align !== undefined && relation.align !== 'top')
        || (relation.side === 'left'
          ? Math.abs(outer.right + (relation.gapPx ?? 12) - reference.left) > 2 || outer.right > reference.left
          : Math.abs(outer.left - reference.right - (relation.gapPx ?? 12)) > 2 || outer.left < reference.right));
      const tooSmall = !row.allowSmall && row.fit !== null && (picture.width < 240 - 1e-6 || picture.height < 180 - 1e-6);
      const z = this.observedZ(row);
      const controls = row.controls.map((control) => ({ id: control.node.id, targetId: row.state.id, role: control.role, present: isConnected && row.root.contains(control.node), enabled: !(control.node as HTMLButtonElement).disabled,
        editable: false, stateRevision: row.state.propertyVersions[control.property] ?? null,
        observedValue: control.property === 'opacity' ? Number(style.opacity || 1) : control.role === 'slider' ? Number((control.node as HTMLInputElement).value) : control.property === 'zoom' ? actualView?.zoom ?? null : control.property === 'front' ? z : control.property === 'open' ? !row.root.hidden : null }));
      return {
        id: row.state.id, rootId: row.root.id, workspaceId: this.opts.context.workspaceId, context: copy(this.opts.context), kind: derivedKind(row.state.source),
        source: copy(row.state.source), sourceKey: row.state.source.key, sourceGeneration: row.state.sourceGeneration, lifetimeGeneration: row.state.lifetimeGeneration,
        revision: row.state.revision, title: row.title.textContent, propertyVersions: { ...row.state.propertyVersions }, propertyOwners: { ...row.state.propertyOwners },
        commandId: row.command.commandId, requestId: row.command.requestId, actionOccurrenceId: row.command.actionOccurrenceId,
        sourceToken: row.state.delivery.sourceToken, payloadBinding: this.payloadBinding(row),
        open: row.state.open, connected: isConnected, visible, staged: row.root.dataset.staged === 'true' ? true : row.root.dataset.staged === 'false' ? false : null,
        rect: rect(outer), contentRect: rect(picture), clipRect: rect(picture),
        z: z ?? 0, viewDrawRef: this.viewDrawRef(row), view: { ...(actualView ?? { zoom: null, pan: null }), ...(row.root.dataset.placementId&&row.root.dataset.project&&row.root.dataset.workspaceView?{arrangement:{project:row.root.dataset.project,tab:row.root.dataset.workspaceView,placement:row.root.dataset.placementId,focused:row.root.contains(document.activeElement)}}:{}), opacity: Number(style.opacity || 1), hidden: row.state.open && row.root.hidden, group: row.root.dataset.group || null }, viewTransform, controls,
        load: { ...copy(row.load), sourceKey: row.state.source.key, sourceGeneration: row.state.sourceGeneration, token: row.state.delivery.sourceToken },
        liveSample: row.liveSample ? copy(row.liveSample) : null,
        heldSample: row.heldSample ? copy(row.heldSample) : null,
        visibility: visible ? 'visible' : laidOut ? 'tab-hidden' : row.root.hidden ? row.state.open ? 'css-hidden' : 'closed' : 'unknown',
        limitation: besideConflict || tooSmall ? 'constraint_conflict' : undefined,
        unknown: [...(row.state.source.kind !== 'live' ? [] : row.heldSample ? ['live-source-held'] : !row.liveSample ? ['live-sample-unobserved'] : row.liveSample.boot === null || row.liveSample.seq === null ? ['live-sample-incomplete'] : []), ...(row.image && !geometry ? ['source-pixel-geometry-unobserved'] : [])],
        drawEvidence: row.load.state === 'drawn' ? 'decoded-media-render-frame' : null,
      };
    });
  }
  private payloadBinding(row: WindowRow): Record<string, unknown> | null {
    if (!row.state.delivery.payloadBinding) return null;
    const binding = copy(row.state.delivery.payloadBinding);
    // An admitted descriptor is not a decoder acknowledgement.
    delete binding.decodedToken;
    if (row.decodedToken !== null) binding.decodedToken = row.decodedToken;
    return binding;
  }
  private remove(row: WindowRow): void {
    this.restoring.delete(row.state.id);this.restoreFailures.delete(row.state.id);
    row.disposed = true; row.decodeJob = null; row.events.abort(); row.mediaEvents?.abort(); row.shell.dispose(); row.root.remove(); this.rows.delete(row.state.id);
  }
  /** Retire only native presentations; saved Project arrangements are restored by their existing owner. */
  clearProject(): void {
    for (const row of [...this.rows.values()]) this.remove(row);
    this.shelf.replaceChildren(); this.delivered.clear(); this.phoneActive = null; this.phoneStamp = '';
  }
  dispose(): void { this.disposed = true; for (const row of [...this.rows.values()]) this.remove(row); this.shelf.remove(); this.delivered.clear(); }
}
