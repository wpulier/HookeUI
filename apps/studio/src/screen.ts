// What the studio shows, reported to the server (contract 10; Amendment 2 of
// docs/design/2026-10-06-captures-operations-evidence.md): every layer and panel, what it shows, where it
// is (workspace px), its stacking order and its style. A change the server sends (`present`) counts as
// done only once a report says it was applied and shows it; the user's own changes (a drag, a resize, a
// toggle) are reported as theirs. A look at the workspace (look_at_screen) is composed here from what is
// drawn, and goes to that one look only.
import type { ClientCommand, Connection, ServerMessage, ScreenReportFailure, WorkspaceHello, WorkspaceReady, WorkspaceCaptureRequest, WorkspaceCaptureReply, WorkspaceCaptureCode, ResearchCaptureTarget } from './protocol';
import type { PanelStack, WindowFacts } from './panels';
import type { MagnifierPanel } from './magnifier';
import { WorkspaceRuntime } from './workspace-runtime';
import type { WorkspaceViewport, WorkspaceStructuralInput } from './workspace-runtime';
import { WorkspaceWindows } from './workspace-windows';
import { sameStudyResource, studyResourceReference } from './workspace-study-restoration';
import { itemSpace } from './spaces';
import { scientificPaint, type ScientificPaintObservation } from './scientific-presentation';
import type { Space } from './spaces';
import { base64Of, paintSize, paintSteps, type PaintPlan, type PaintText } from './window-paint';

export type Rect4 = [number, number, number, number]; // x, y, width, height (workspace px)

/** Whether a passing reason (`key`) was said already in this browser tab, remembering it now if not: a reason is said
 * once, across reloads too (sessionStorage), and its × dismisses it. Without storage it is said again after a reload. */
export function saidOnce(key: string, storage: Pick<Storage, 'getItem' | 'setItem'> | null = typeof sessionStorage === 'undefined' ? null : sessionStorage): boolean {
  try {
    if (!storage) return false;
    const said: unknown = JSON.parse(storage.getItem('micromind-said-once') ?? '[]');
    const list = Array.isArray(said) ? said.filter((k): k is string => typeof k === 'string') : [];
    if (list.includes(key)) return true;
    storage.setItem('micromind-said-once', JSON.stringify([...list, key].slice(-50)));
  } catch { /* optional convenience */ }
  return false;
}

const hintOwner = Symbol('status hint owner');
/** Each update replaces the one DOM hint owner, even when its text is identical. */
export function renderHint(hint: HTMLElement, text: string, isError = false,
  action?: { label: string; run: () => void }): () => void {
  const target = hint as HTMLElement & { [hintOwner]?: object }, owner = {};
  target[hintOwner] = owner;
  hint.textContent = text;
  hint.dataset.tone = isError ? 'error' : '';
  const clear = () => {
    if (target[hintOwner] !== owner) return;
    delete target[hintOwner]; hint.textContent = ''; hint.dataset.tone = '';
  };
  if (action && typeof document !== 'undefined' && typeof document.createElement === 'function') {
    const act = document.createElement('button');
    act.type = 'button'; act.className = 'hint-action'; act.textContent = action.label;
    act.addEventListener('click', () => { clear(); action.run(); });
    hint.append(act);
  }
  if (text && typeof document !== 'undefined' && typeof document.createElement === 'function') {
    // Dismissed by the user at any time (the user's notes of 2026-10-11); its mark is drawn by CSS, so the line's
    // text stays what it says.
    const close = document.createElement('button');
    close.type = 'button'; close.className = 'hint-close'; close.setAttribute('aria-label', 'Dismiss');
    close.addEventListener('click', clear);
    hint.append(close);
  }
  return clear;
}

function validScreenReportFailure(value: unknown): value is ScreenReportFailure {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const ref = value as Partial<ScreenReportFailure>, bounded = (field: unknown) => typeof field === 'string' && field.length > 0 && field.length <= 256;
  if (Object.keys(value).sort().join(',') !== 'appRunId,client,seq,sourceKey' || !bounded(ref.appRunId) || !bounded(ref.sourceKey)
    || typeof ref.seq !== 'number' || !Number.isSafeInteger(ref.seq) || ref.seq < 1 || !ref.client || typeof ref.client !== 'object' || Array.isArray(ref.client)) return false;
  return Object.keys(ref.client).sort().join(',') === 'clientId,connectionEpoch,pageEpoch,workspaceId'
    && Object.values(ref.client).every(bounded);
}

export interface ScreenItem {
  work?: StudioSurfaceLegacy['work'];
  workspace?:{project:string;tab:string;tabTitle?:string};
  document?: {project:string;tab:string;placement:string;resource:{day:string;id:string;version:number|null;picture:number|null;outputId?:string};representation:string;state:string;focused:boolean};
  workReady?: boolean;
  id: string; // live | layer:<population> | outlines | magnifier | viewer | slidemap | card
  kind: string;
  title?: string;
  shows?: string | null;
  /** The magnifier's picture now: a picture's URL, or "held" / "live" (a picture is shown once reported). */
  source?: string | null;
  /** The picture the magnifier shows beside its close-up, both at once (its URL), or null. */
  beside?: string | null;
  /** The magnifier's level (x the fitted view); the result viewer's lesson step ("q2"). */
  level?: number | null;
  step?: string | null;
  pictures?: readonly { source: string; scale: number }[];
  /** The magnifier covers its object (only ever because someone asked for it over the view: present's "over"). */
  covers?: boolean;
  open?: boolean;
  visible?: boolean;
  opacity?: number;
  rect?: Rect4 | null; // always in the workspace's space: the studio's CSS px (invariant 14)
  /** The space this item's contents are in (spaces.ts): `rect` is the workspace's, this is what is inside it. */
  space?: Space;
  z?: number;
  style?: Record<string, unknown>;
  placement?: string;
}

export interface UserChange {
  id: string;
  prop: string;
}

export interface PresentMessage {
  type: 'present';
  change: string;
  id: string;
  kind: string;
  verb: string;
  set: Record<string, unknown>;
}

/** A rect as [x, y, width, height] (workspace px), from a DOMRect or a {left, top, right, bottom}. */
export function rectOf(r: { left: number; top: number; right: number; bottom: number } | null | undefined): Rect4 | null {
  if (!r) return null;
  const width = r.right - r.left;
  const height = r.bottom - r.top;
  return width > 0 && height > 0 ? [Math.round(r.left), Math.round(r.top), Math.round(width), Math.round(height)] : null;
}

/** The report's key: a report is sent when this changes (and at least every few seconds). */
export function reportKey(items: ScreenItem[], applied: string[], user: UserChange[]): string {
  return JSON.stringify([items, applied, user]);
}

export type WorkspaceObservation = ReturnType<WorkspaceRuntime['collect']>;
export type WorkspaceReportSample = Readonly<{ seq: number; nowMs: number }>;
export type WorkspaceReportCollector = (sample: WorkspaceReportSample) => WorkspaceObservation | null;
// A fixed DOM control has no native property revision; null explicitly records that gap.
// WorkspaceWindows' native fact remains strict and is not widened by this projection.
export type StudioControlFact = Omit<ReturnType<WorkspaceWindows['facts']>[number]['controls'][number], 'stateRevision'>
  & { stateRevision: number | null };
export interface StudioSurfaceLegacy {
  schema: 'studio-surface-v1'; scope: 'dom-observation';
  title?: string; shows?: string | null; source?: string | null; beside?: string | null;
  level?: number | null; pictures?: readonly { source: string; scale: number }[];
  step?: string | null; placement?: string; covers?: boolean; opacity?: number;
  style?: { opacity?: number; fillOpacity?: number; lineWidth?: number; colour?: string; color?: string;
    stroke?: string; fill?: boolean; emphasis?: { members: readonly string[]; rest: 'hide' | 'fade' } };
  workspace?:{project:string;tab:string;tabTitle?:string};
  document?: {project:string;tab:string;placement:string;resource:{day:string;id:string;version:number|null;picture:number|null;outputId?:string};representation:string;state:string;focused:boolean};
  workReady?: boolean;
  work?: { researcherId: string; resource: { day: string; id: string; version: number | null } };
}
export interface StudioSurfaceSnapshot {
  kind: string; open: boolean; visible: boolean; rect: Rect4 | null; z: number | null;
  space: Space; legacy: StudioSurfaceLegacy; contentKey: string | null;
  controls: readonly { node: HTMLElement; fact: StudioControlFact }[];
  scientificPaint?: ScientificPaintObservation | null;
  scientificPanelPaint?: ScientificPaintObservation | null;
}
export interface StudioSurface { id: string; root: HTMLElement; read(): StudioSurfaceSnapshot }
/** Semantic openness is separate from whether this surface occupies visible DOM space. */
export function surfaceVisible(root: HTMLElement, open: boolean, visible: boolean, rect: Rect4 | null): boolean {
  if (!open || !visible || !rect || !rect.every(Number.isFinite) || rect[2] <= 0 || rect[3] <= 0 || root.closest('[hidden]')) return false;
  for (let node: HTMLElement | null = root; node; node = node.parentElement) {
    const css = getComputedStyle(node);
    if (node.hidden || css.display === 'none' || css.visibility === 'hidden' || css.visibility === 'collapse') return false;
  }
  return true;
}
/** The actual magnifier producer used by main; no shadow getter/content algorithm. */
export function magnifierSurface(panel: MagnifierPanel, root: HTMLElement, z: () => number): StudioSurface {
  return { id: 'magnifier', root, read: () => {
    const open = panel.isOpen();
    const legacy: StudioSurfaceLegacy = { schema: 'studio-surface-v1', scope: 'dom-observation',
      shows: panel.objectId(), source: panel.source(), beside: panel.besideShown(), level: panel.levelShown(),
      covers: panel.covers(), pictures: panel.picturesShown() };
    const rect = (open ? rectOf(panel.footprint()) : null) ?? rectOf(root.getBoundingClientRect());
    return { kind: 'magnifier', open, visible: surfaceVisible(root, open, true, rect), rect, z: z(),
      space: itemSpace('magnifier', legacy.source), legacy,
      // Loaded pane identities are content; their display scales only change the revision.
      contentKey: JSON.stringify([legacy.shows ?? null, legacy.source ?? null, legacy.beside ?? null,
        legacy.step ?? null, legacy.work ?? null, (legacy.pictures ?? []).map((picture) => picture.source)]),
      controls: ['magnifier-less', 'magnifier-more', 'magnifier-close'].map((id) => {
        const node = document.getElementById(id);
        if (!node) throw new Error(`Missing #${id}`);
        const pressed = node.getAttribute('aria-pressed');
        return { node, fact: { id, targetId: 'magnifier', role: node.getAttribute('role') ?? (node.tagName === 'A' ? 'link' : 'button'),
          present: open && root.contains(node) && !node.hidden && !node.closest('[hidden]'),
          enabled: !node.matches(':disabled') && node.getAttribute('aria-disabled') !== 'true', editable: false,
          stateRevision: null, observedValue: pressed === 'true' ? true : pressed === 'false' ? false : null } };
      }) };
  } };
}
export interface StudioSurfaceCollection {
  surfaces: readonly StudioSurface[];
  coverage: { complete: false; omittedItems: number; unknown: readonly string[] };
}
export type StudioBiologicalSelection = { kind: 'outline'; outlineId: string; revision?: number }
  | { kind: 'member'; populationId: string; memberId: string; memberRevision?: number };
export interface StudioSelection { presentations: readonly string[]; biological: readonly StudioBiologicalSelection[] }
export interface WorkspaceSessionOptions {
  focusFallback?: () => HTMLElement | null;
  onPresentation?: (id: string, root: HTMLElement, title: () => string, foreground: boolean) => void;
  hiddenHost?: HTMLElement;
  connection: Connection; reporter: ScreenReporter; stage: HTMLElement; panels: PanelStack; interactionHost: HTMLElement;
  proposal: Readonly<Omit<WorkspaceHello, 'type' | 'requestId'>>;
  legacy(): StudioSurfaceCollection; viewport(): WorkspaceViewport; frame(): number | undefined;
  selected(): StudioSelection; windowFacts(): WindowFacts | null;
  platform: { fetch: typeof fetch; createObjectURL(blob: Blob): string; revokeObjectURL(url: string): void;
    now(): number; sourceOrigin: string };
  onError(error: Error): void;
  onReadyChange?(ready: boolean): void;
  onReportError?(error: string): () => void; // cleanup owns only this displayed report error
  onWorkspaceSharingChange?(state: 'preparing' | 'sharing' | 'muted' | WorkspaceCaptureCode): void;
}
function deepFreeze<T>(value: T): T {
  if (value && typeof value === 'object') { Object.values(value).forEach(deepFreeze); Object.freeze(value); }
  return value;
}
function boundedIdentity(value: string): string {
  if (typeof value !== 'string' || !value || value.length > 256) throw new Error('Invalid observed identity');
  return value;
}
function observedLegacy(value: StudioSurfaceLegacy): StudioSurfaceLegacy {
  // This closed projection deliberately has no native source, paint or ownership fields.
  const result: StudioSurfaceLegacy = { schema: 'studio-surface-v1', scope: 'dom-observation' };
  if (value.schema !== result.schema || value.scope !== result.scope) throw new Error('Invalid legacy observation scope');
  const allowed = ['schema', 'scope', 'title', 'shows', 'source', 'beside', 'level', 'pictures', 'step', 'placement', 'covers', 'opacity', 'style', 'work', 'workReady', 'document', 'workspace'];
  if (Object.keys(value).some((key) => !allowed.includes(key))) throw new Error('Unknown legacy observation field');
  for (const key of ['title', 'shows', 'source', 'beside', 'step', 'placement'] as const) {
    const field = value[key]; if (field === undefined) continue;
    const limit = key === 'source' || key === 'beside' ? 2048 : key === 'title' ? 120 : 256;
    if (field !== null && (typeof field !== 'string' || field.length > limit)) throw new Error('Invalid legacy observation text');
    if (field === null && (key === 'title' || key === 'placement')) throw new Error('Invalid null observation');
    Object.assign(result, { [key]: field });
  }
  for (const key of ['level', 'opacity'] as const) if (value[key] !== undefined) {
    const number = value[key];
    if (!(key === 'level' && number === null) && (typeof number !== 'number' || !Number.isFinite(number) || (key === 'opacity' ? number < 0 || number > 1 : number <= 0))) throw new Error('Invalid observed level/opacity');
    Object.assign(result, { [key]: number });
  }
  if (value.covers !== undefined) { if (typeof value.covers !== 'boolean') throw new Error('Invalid covers observation'); result.covers = value.covers; }
  if (value.pictures !== undefined) {
    if (!Array.isArray(value.pictures) || value.pictures.length > 2) throw new Error('Invalid observed pictures');
    result.pictures = value.pictures.map((picture) => {
      if (Object.keys(picture).some((key) => !['source', 'scale'].includes(key)) || typeof picture.source !== 'string' || !picture.source || picture.source.length > 2048 || !Number.isFinite(picture.scale) || picture.scale <= 0) throw new Error('Invalid observed picture');
      return { source: picture.source, scale: picture.scale };
    });
  }
  if (value.style !== undefined) {
    const style: NonNullable<StudioSurfaceLegacy['style']> = {};
    for (const [key, field] of Object.entries(value.style)) {
      if (['opacity', 'fillOpacity', 'lineWidth'].includes(key)) {
        if (typeof field !== 'number' || !Number.isFinite(field) || field < 0 || key !== 'lineWidth' && field > 1) throw new Error('Invalid observed style number');
      } else if (['colour', 'color', 'stroke'].includes(key)) {
        if (typeof field !== 'string' || !field || field.length > 120) throw new Error('Invalid observed style colour');
      } else if (key === 'fill') { if (typeof field !== 'boolean') throw new Error('Invalid observed fill'); }
      else if (key === 'emphasis') {
        const emphasis = field as NonNullable<typeof style.emphasis>;
        if (!emphasis || Object.keys(emphasis).some((name) => !['members', 'rest'].includes(name)) || !Array.isArray(emphasis.members) || emphasis.members.length > 40 || !['hide', 'fade'].includes(emphasis.rest)) throw new Error('Invalid observed emphasis');
        emphasis.members.forEach(boundedIdentity);
      } else throw new Error('Unknown observed style');
      Object.assign(style, { [key]: structuredClone(field) });
    }
    result.style = style;
  }
  if (value.workReady !== undefined) {
    if (typeof value.workReady !== 'boolean') throw new Error('Invalid work readiness');
    result.workReady = value.workReady;
  }
  if (value.work !== undefined) {
    const work = value.work, resource = work.resource;
    if (Object.keys(work).some((key) => !['researcherId', 'resource'].includes(key)) || Object.keys(resource).some((key) => !['day', 'id', 'version'].includes(key))) throw new Error('Unknown observed work field');
    boundedIdentity(work.researcherId); boundedIdentity(resource.day); boundedIdentity(resource.id);
    if (resource.version !== null && (!Number.isSafeInteger(resource.version) || resource.version < 0)) throw new Error('Invalid observed work version');
    result.work = structuredClone(work);
  }
  if(value.workspace){const keys=Object.keys(value.workspace).sort().join(',');if(keys!=='project,tab'&&keys!=='project,tab,tabTitle')throw new Error('Invalid workspace context');boundedIdentity(value.workspace.project);boundedIdentity(value.workspace.tab);
    // The Tab in front's title (main.ts), at most 80 characters as a Tab title is (workspace_observation.py checks the same).
    if(value.workspace.tabTitle!==undefined&&(typeof value.workspace.tabTitle!=='string'||!value.workspace.tabTitle||value.workspace.tabTitle.length>80))throw new Error('Invalid workspace context');
    result.workspace={...value.workspace};}
  if(value.document){
    const d=value.document;
    if(Object.keys(d).some(k=>!['project','tab','placement','resource','representation','state','focused'].includes(k)))throw new Error('Unknown document observation');
    for(const v of [d.project,d.tab,d.placement,d.representation,d.state,d.resource.day,d.resource.id])boundedIdentity(v);
    if(typeof d.focused!=='boolean'||[d.resource.version,d.resource.picture].some(v=>v!==null&&(!Number.isSafeInteger(v)||v<0)))throw new Error('Invalid document observation');
    result.document=structuredClone(d);
  }
  return result;
}

function legacyMeaning(legacy: StudioSurfaceLegacy): string {
  return JSON.stringify([legacy.shows ?? null, legacy.source ?? null, legacy.beside ?? null,
    legacy.step ?? null, legacy.work ?? null]);
}
interface LegacyCompletion {
  id: string; applied: boolean; target?: string; command?: string;
  current?: () => boolean; complete?: () => void;
  foreground?: () => boolean; foregroundClaimed?: boolean;
}

type CaptureHandleTrack = MediaStreamTrack & { getCaptureHandle?(): { origin?: string; handle?: string } | null };
type CaptureHandleDevices = MediaDevices & { setCaptureHandleConfig?(config: { exposeOrigin: boolean; handle: string; permittedOrigins: string[] }): void };
interface WorkspacePair {
  windows: WorkspaceWindows; runtime: WorkspaceRuntime; context: WorkspaceReady['client'] & { appRunId: string };
}
interface TabResource {
  pair: WorkspacePair; stream: MediaStream; track: CaptureHandleTrack; video: HTMLVideoElement;
  id: string; removeListeners(): void; cancelReady: (() => void) | null;
}
interface TabSample {
  request: WorkspaceCaptureRequest; pair: WorkspacePair; resource: TabResource;
  deadline: number; timer: ReturnType<typeof setTimeout>; active: boolean;
  cancel: ((code: WorkspaceCaptureCode) => void) | null; frameId: number | null; canvas: HTMLCanvasElement | null;
}
class CaptureUnavailable extends Error {
  readonly code: WorkspaceCaptureCode;
  constructor(code: WorkspaceCaptureCode) { super(code); this.code = code; }
}

export class ScreenReporter {
  private last = ''; private sentAt = 0; private seq = 0;
  private applied: LegacyCompletion[] = []; private user: UserChange[] = [];
  private checkedAt = 0; private forced = false;
  private workspace: WorkspaceReportCollector | null = null;
  private latestWorkspace: WorkspaceObservation | null = null;
  private workspaceSent: (() => void) | null = null;
  private readonly roots = new Map<string, { root: HTMLElement; lifetime: string; kind: string; content: string | null;
    open: boolean; legacy: StudioSurfaceLegacy; signature: string; generation: number; revision: number }>();
  private readonly send: (command: ClientCommand) => void;
  private readonly collect: () => ScreenItem[];
  private readonly everyMs: number; private readonly keepAliveMs: number;
  constructor(send: (command: ClientCommand) => void, collect: () => ScreenItem[], everyMs = 200, keepAliveMs = 5000) {
    this.send = send; this.collect = collect; this.everyMs = everyMs; this.keepAliveMs = keepAliveMs;
  }
  markApplied(change: string, current?: () => boolean): void {
    if (current && !current()) return;
    this.applied.push({ id: change, applied: true, current }); this.boundApplied(); this.checkedAt = 0;
  }
  private boundApplied(): void {
    if (this.applied.length > 40) this.applied.splice(0, this.applied.length - 40);
  }
  /** Stage the original Change in the same bounded buffer its completion/history uses. */
  legacyApplied(change: string, target: string, accepted: () => boolean, surfaces: () => readonly StudioSurface[], original?: PresentMessage): () => void {
    boundedIdentity(change); boundedIdentity(target);
    const command = original ? JSON.stringify(original) : undefined;
    if (command && (command.length > 16384 || original!.change !== change || original!.id !== target)) return () => {};
    const existing = this.applied.find((entry) => entry.id === change);
    if (existing) return existing.target === target && (!command || existing.command === command) ? existing.complete ?? (() => {}) : () => {};
    const stamp = this.roots.get(target);
    if (!stamp || !accepted()) return () => {};
    const surface = surfaces().find((entry) => entry.id === target);
    if (!surface || surface.root !== stamp.root) return () => {};
    const read = surface.read(), published = observedLegacy(read.legacy);
    const { root, lifetime, kind, content, generation } = stamp, rootId = root.id;
    if (read.kind !== kind) return () => {};
    const alive = () => accepted() && this.roots.get(target) === stamp && stamp.lifetime === lifetime
      && stamp.kind === kind && root.id === rootId && document.body.contains(root);

    // Only the original open/close permits its own content transition. A retained
    // entry's shows is its resource ID; the image's shows is its outline subject.
    const closing = original?.verb === 'close' && original.set.open === false && original.kind === kind;
    const opening = original?.verb === 'open' && original.set.open === true
      && (original.kind === kind || original.kind === 'entry') && read.open
      && (original.set.source === undefined || original.set.source === published.source)
      && (original.kind !== 'entry' || (kind === 'magnifier'
        ? typeof original.set.source === 'string' && original.set.source === published.source
        : typeof original.set.shows === 'string' && original.set.shows === published.shows));
    // A publication that does not match the original open cannot fall back to
    // treating whichever content is currently displayed as an unchanged command.
    const blocked = original?.verb === 'open' && original.set.open === true && !opening;
    let intended: ((legacy: StudioSurfaceLegacy, open: boolean) => boolean) | null = null;
    if (closing || opening) {
      const expected = closing && kind === 'magnifier'
        ? { ...published, shows: null, source: null, beside: null, pictures: [] } : published;
      const meaning = legacyMeaning(expected), expectedOpen = !closing;
      const panes = closing ? [] : [expected.source, expected.beside]
        .filter((source): source is string => typeof source === 'string' && source !== 'live' && source !== 'held');
      let loaded = closing ? [] : (expected.pictures ?? []).map((picture) => picture.source);
      intended = (legacy, open) => {
        if (open !== expectedOpen || legacyMeaning(legacy) !== meaning) return false;
        const pictures = (legacy.pictures ?? []).map((picture) => picture.source);
        let previous = -1;
        for (const picture of pictures) {
          const index = panes.indexOf(picture);
          if (index <= previous) return false;
          previous = index;
        }
        // Permit only completion of this published arrangement's original panes,
        // never an unrelated URL/order or removal of one already observed loaded.
        if (loaded.some((picture) => !pictures.includes(picture))) return false;
        loaded = pictures; return true;
      };
    }
    const unchanged = () => stamp.generation === generation && stamp.content === content;
    const entry: LegacyCompletion = { id: change, target, command, applied: false,
      current: () => alive() && !blocked && (intended ? stamp.generation >= generation && intended(stamp.legacy, stamp.open) : unchanged()) };
    const matchesSurface = () => {
      if (!alive() || blocked || !this.applied.includes(entry)) return false;
      const currentSurface = surfaces().find((candidate) => candidate.id === target);
      if (!currentSurface || currentSurface.root !== root) return false;
      const currentRead = currentSurface.read();
      return currentRead.kind === kind && (intended ? intended(observedLegacy(currentRead.legacy), currentRead.open)
        : unchanged() && currentRead.contentKey === content);
    };
    entry.foreground = matchesSurface;
    entry.complete = () => {
      if (!matchesSurface()) return;
      entry.applied = true; this.checkedAt = 0;
    };
    this.applied.push(entry); this.boundApplied(); return entry.complete;
  }
  /** One local selection for the exact still-valid original occurrence, never wire replay. */
  claimLegacyForeground(original: PresentMessage): boolean {
    const entry = this.applied.find(candidate => candidate.id === original.change);
    if (!entry || entry.foregroundClaimed || entry.target !== original.id || entry.command !== JSON.stringify(original)
        || !entry.foreground?.()) return false;
    entry.foregroundClaimed = true; return true;
  }
  private appliedIds(): string[] {
    // Fresh collection precedes pruning; pending entries do not assert completion.
    this.applied = this.applied.filter((entry) => !entry.current || entry.current());
    return this.applied.filter((entry) => entry.applied).map((entry) => entry.id);
  }
  reset(): void { this.applied = []; this.checkedAt = 0; }
  retireWorkspace(): void { this.applied = []; this.user = []; this.last = ''; this.latestWorkspace = null; this.askNow(); }
  askNow(): void { this.forced = true; this.checkedAt = 0; }
  userChanged(id: string, prop: string): void { this.user.push({ id, prop }); this.checkedAt = 0; }
  useWorkspace(collect: WorkspaceReportCollector, sent?: () => void): void {
    if (this.workspace) throw new Error('Workspace reporter already bound'); this.workspace = collect; this.workspaceSent = sent ?? null; this.askNow();
  }
  /** Admission for a named structural action, compared with this live DOM opening. */
  matchesStructuralPin(pin:{client:Record<string,unknown>;id:string;rootId:string;kind:string;lifetimeGeneration:string|number;sourceGeneration:number}):boolean {
    try {
      const now=this.candidate(performance.now());
      if(!pin||Object.entries(now.client).some(([key,value])=>pin.client?.[key]!==value))return false;
      const item=now.items.find(i=>i.id===pin.id);
      return !!item&&item.open&&item.connected&&(['rootId','kind','lifetimeGeneration','sourceGeneration'] as const).every(key=>item[key]===pin[key]);
    }catch{return false;}
  }
  /** Local last-sent facts only; this never implies server admission or media authority. */
  sourceReportState(ref: ScreenReportFailure, originallyKnown = false): 'unknown' | 'current' | 'retired' {
    const latest = this.latestWorkspace;
    if (!latest || ref.appRunId !== latest.appRunId || ref.seq > latest.seq
      || Object.entries(latest.client).some(([key, value]) => ref.client[key as keyof typeof ref.client] !== value)) return 'unknown';
    const known = latest.sources.some((source) => source.key === ref.sourceKey);
    if (!known && !originallyKnown) return 'unknown';
    if (ref.seq === latest.seq || latest.omitted.sources > 0 || latest.omitted.edges > 0) return 'current';
    for (const item of latest.items) {
      if (!('sourceKey' in item) || item.sourceKey !== ref.sourceKey) continue;
      if (item.open || item.connected || item.visible || item.load.state !== 'empty' || item.load.drawToken != null
        || 'heldSample' in item && item.heldSample != null || 'liveSample' in item && item.liveSample != null || 'viewDrawRef' in item && item.viewDrawRef != null) return 'current';
    }
    if (latest.targets.some((target) => 'sourceKey' in target && target.sourceKey === ref.sourceKey)
      || latest.edges.some((edge) => edge.to === ref.sourceKey || edge.from === ref.sourceKey)) return 'current';
    return 'retired';
  }
  private candidate(nowMs: number): WorkspaceObservation {
    const screen = this.workspace?.({ seq: this.seq + 1, nowMs });
    if (!screen) throw new Error('Workspace is not accepted on this connection');
    return deepFreeze(structuredClone({ ...screen, applied: this.appliedIds(), user: this.ownable(screen) }));
  }
  /** A user's change is theirs only against what the last report showed: one made before its item was ever
   * reported (a toggle clicked before the first report) has no original to own, and the server refuses the
   * whole report for it, so it is left out (2026-10-12: the sidebar opened as the page loaded). */
  private ownable(screen: Pick<WorkspaceObservation, 'items'>): UserChange[] {
    const sent = new Map((this.latestWorkspace?.items ?? []).map((item) => [item.id, item]));
    return this.user.filter(({ id }) => {
      const before = sent.get(id), now = screen.items.find((item) => item.id === id);
      return before !== undefined && now !== undefined && before.rootId === now.rootId && before.lifetimeGeneration === now.lifetimeGeneration;
    });
  }
  private key(screen: WorkspaceObservation): string {
    const { seq: _seq, capturedMonoMs: _clock, interaction, commandResults, user: _user, ...facts } = screen;
    return JSON.stringify({ ...facts, interaction, commandResults: commandResults.map(({ reportSeq: _report, ...result }) => result) },
      (key, value) => ['ageMs', 'eventAgeMs', 'sampledMonoMs'].includes(key) ? undefined : value);
  }
  private commit(screen: WorkspaceObservation, nowMs: number, count: number): void {
    this.seq = screen.seq; this.last = this.key(screen); this.sentAt = nowMs; this.forced = false; this.user.splice(0, count);
    this.latestWorkspace = screen; this.workspaceSent?.();
  }
  sampleAtSend(nowMs: number, submit: (screen: WorkspaceObservation) => void): WorkspaceObservation {
    const screen = this.candidate(nowMs), count = this.user.length;
    submit(screen); this.commit(screen, nowMs, count); return screen;
  }
  observeLegacy(collection: StudioSurfaceCollection, workspaceId: string, selection: StudioSelection): WorkspaceStructuralInput {
    if (collection.surfaces.length > 256 || collection.coverage.complete !== false || collection.coverage.unknown.length > 40 || !Number.isSafeInteger(collection.coverage.omittedItems) || collection.coverage.omittedItems < 0) throw new Error('Unbounded surface collection');
    collection.coverage.unknown.forEach(boundedIdentity);
    if (selection.presentations.length + selection.biological.length > 16) throw new Error('Unbounded selection');
    selection.presentations.forEach(boundedIdentity);
    const selectedIds = [...selection.presentations];
    const input: WorkspaceStructuralInput = { items: [], nodes: [], targets: [], selected: selectedIds,
      complete: false, omittedItems: collection.coverage.omittedItems, unknown: [...collection.coverage.unknown] };
    const ids = new Set<string>();
    for (const surface of collection.surfaces) {
      const id = boundedIdentity(surface.id); if (ids.has(id)) throw new Error('Duplicate surface identity'); ids.add(id);
      const read = surface.read(), legacy = observedLegacy(read.legacy), root = surface.root;
      boundedIdentity(read.kind);
      if (typeof read.open !== 'boolean' || typeof read.visible !== 'boolean' || !['frame', 'capture', 'workspace', 'map', 'picture'].includes(read.space)
        || read.z !== null && !Number.isFinite(read.z) || read.rect !== null && (read.rect.length !== 4 || !read.rect.every(Number.isFinite) || read.rect[2] <= 0 || read.rect[3] <= 0)
        || read.contentKey !== null && (typeof read.contentKey !== 'string' || read.contentKey.length > 16384)) throw new Error('Invalid surface observation');
      if (!root.id) root.id = `studio-root-${crypto.randomUUID()}`;
      boundedIdentity(root.id);
      const connected = document.body.contains(root);
      if (read.controls.length > 40) throw new Error('Unbounded surface controls');
      const controlIds = new Set<string>();
      const controls = read.controls.map(({ node, fact }) => {
        boundedIdentity(fact.id);
        if (controlIds.has(fact.id) || fact.targetId !== id || fact.editable !== false || typeof fact.present !== 'boolean' || typeof fact.enabled !== 'boolean'
          || fact.stateRevision != null && (!Number.isSafeInteger(fact.stateRevision) || fact.stateRevision < 0)
          || fact.observedValue != null && typeof fact.observedValue !== 'boolean' && (typeof fact.observedValue !== 'number' || !Number.isFinite(fact.observedValue))) throw new Error('Invalid registered control observation');
        controlIds.add(fact.id);
        return { id: fact.id, targetId: id, role: boundedIdentity(fact.role), present: fact.present && root.contains(node), enabled: fact.enabled,
          editable: false as const, stateRevision: fact.stateRevision ?? null, observedValue: fact.observedValue ?? null };
      });
      const preview = read.scientificPaint === undefined ? undefined :
        read.scientificPaint === null || !read.open || !read.visible || !connected ? null : scientificPaint(read.scientificPaint);
      const panelPreview = read.scientificPanelPaint === undefined ? undefined :
        read.scientificPanelPaint === null || !read.open || !read.visible || !connected ? null : scientificPaint(read.scientificPanelPaint);
      if ((preview !== undefined || panelPreview !== undefined) && (read.kind !== 'document' || legacy.document?.representation !== 'scientific-review'))
        throw new Error('Scientific paint belongs only to its scientific document window.');
      // An undeclared surface omits the paint field; a declared but unpainted
      // surface sends null. JSON array slots collapse both to null, leaving the
      // structural revision unchanged when a saved document finishes opening.
      const paintFacts = { ...(preview === undefined ? {} : {scientificPaint: preview}),
        ...(panelPreview === undefined ? {} : {scientificPanelPaint: panelPreview}) };
      const signature = JSON.stringify([read.kind, read.open, connected, read.visible, read.rect, read.z, read.space, legacy, controls, paintFacts, collection.coverage.unknown]);
      let stamp = this.roots.get(id);
      if (!stamp || stamp.root !== root) {
        stamp = { root, lifetime: crypto.randomUUID(), kind: read.kind, content: read.contentKey,
          open: read.open, legacy: deepFreeze(structuredClone(legacy)), signature, generation: 1, revision: 1 }; this.roots.set(id, stamp);
      } else {
        if (stamp.content !== read.contentKey) { stamp.generation++; stamp.content = read.contentKey; stamp.revision++; }
        else if (stamp.signature !== signature) stamp.revision++;
        stamp.kind = read.kind; stamp.open = read.open; stamp.legacy = deepFreeze(structuredClone(legacy)); stamp.signature = signature;
      }
      input.items.push({ id, rootId: root.id, workspaceId, kind: read.kind, lifetimeGeneration: stamp.lifetime,
        sourceGeneration: stamp.generation, revision: stamp.revision, open: read.open, connected,
        visible: read.visible && connected, ...(read.rect ? { rect: [...read.rect] } : {}), ...(read.z !== null ? { z: read.z } : {}),
        space: read.space, view: { legacy }, controls, propertyVersions: {}, propertyOwners: {},
        ...(preview === undefined ? {} : { scientificPaint: preview }),
        ...(panelPreview === undefined ? {} : { scientificPanelPaint: panelPreview }),
        load: { state: 'unknown', unknown: ['native-load-unobserved'] }, unknown: [...collection.coverage.unknown] });
      input.nodes.push({ itemId: id, root, controls: read.controls.map((control) => control.node) });
      if (read.open && connected) input.targets.push({ id: `target:surface:${id}`, type: 'studio-surface', presentationId: id, basis: 'dom-observation' });
    }
    for (const id of this.roots.keys()) if (!ids.has(id)) this.roots.delete(id);
    for (const selected of selection.biological) {
      const revision = selected.kind === 'outline' ? selected.revision : selected.memberRevision;
      if (revision !== undefined && (!Number.isSafeInteger(revision) || revision < 0)) throw new Error('Invalid biological selection revision');
      if (selected.kind === 'outline') {
        const outlineId = boundedIdentity(selected.outlineId), id = `target:outline:${outlineId}`;
        input.targets.push({ id, type: 'outline', outlineId, ...(selected.revision !== undefined ? { revision: selected.revision } : {}), basis: 'scene-selection' }); selectedIds.push(id);
      } else {
        const populationId = boundedIdentity(selected.populationId), memberId = boundedIdentity(selected.memberId), id = `target:member:${populationId}:${memberId}`;
        input.targets.push({ id, type: 'member', populationId, memberId, ...(selected.memberRevision !== undefined ? { memberRevision: selected.memberRevision } : {}), basis: 'population-selection' }); selectedIds.push(id);
      }
    }
    return input;
  }
  tick(now: number): void {
    if (now - this.checkedAt < this.everyMs) return; this.checkedAt = now;
    if (this.workspace) {
      // An unavailable accepted pair emits nothing and consumes no sequence.
      const collected = this.workspace({ seq: this.seq + 1, nowMs: now }); if (!collected) return;
      // A page not laid out yet (loading in a hidden or zero-size pane) has no geometry to report, and the app refuses a
      // zero layout size (INVALID_GEOMETRY), which Activity said at a fresh profile's first load (Window C, 2026-10-14).
      // Nothing is sent and no sequence used until it has a size; the report stays asked for until then.
      const [width, height] = collected.viewport.layoutSize;
      if (!(width > 0 && height > 0)) return;
      const screen = deepFreeze(structuredClone({ ...collected, applied: this.appliedIds(), user: this.ownable(collected) }));
      if (this.key(screen) === this.last && !this.user.length && !this.forced && now - this.sentAt < this.keepAliveMs) return;
      const count = this.user.length;
      if ((this.send(screen as unknown as ClientCommand) as unknown) === false) throw new Error('Workspace report transport is not open');
      this.commit(screen, now, count); return;
    }
    const items = this.collect().map((i) => ({ ...i, space: i.space ?? itemSpace(i.kind, i.source) }));
    const applied = this.appliedIds(), key = reportKey(items, applied, []);
    if (key === this.last && !this.user.length && !this.forced && now - this.sentAt < this.keepAliveMs) return;
    this.forced = false; this.last = key; this.sentAt = now;
    const viewport: [number, number] = typeof innerWidth === 'number' ? [innerWidth, innerHeight] : [0, 0];
    const visible = typeof document === 'undefined' || document.visibilityState === 'visible';
    this.send({ type: 'screen', seq: ++this.seq, viewport, visible, items, applied, user: this.user }); this.user = [];
  }
}

/** A connection-scoped pair of the real owners; Connection owns hello correlation/retries. */
export type ScientificTransport = {
  request(path: string, init?: RequestInit): Promise<Response>;
  check(): void;
};
export class WorkspaceSession {
  private voiceGrant: string | null = null;
  voiceTarget(): { connectionGrant: string } {
    if (!this.pair || !this.voiceGrant) throw new Error('Reconnect to the studio before starting Talk.');
    this.options.reporter.askNow();
    return { connectionGrant: this.voiceGrant };
  }
  scientificTransport(): ScientificTransport {
    const pair = this.pair, grant = this.voiceGrant;
    const current = () => {
      if (this.disposed || !pair || !grant || this.pair !== pair || this.voiceGrant !== grant)
        throw new Error('The scientific workspace connection ended. Reopen its saved document.');
    };
    current();
    const request: ScientificTransport['request'] = async (path, init = {}) => {
      current();
      if (!/^\/api\/scientific-reviews(?:\/[A-Za-z0-9_-]+(?:\/(?:request|script|attach|jupyter|exports)|\/(?:frames|native-panels)\/[a-f0-9]{64})?)?$/.test(path))
        throw new Error('Use a declared scientific review endpoint.');
      const method = init.method ?? 'GET';
      if (!['GET', 'POST', 'DELETE'].includes(method) || init.signal && method !== 'GET')
        throw new Error('Stop the scientific session explicitly; hiding its window does not cancel admitted work.');
      if (method === 'POST') {
        let acknowledged: Promise<void> = Promise.resolve();
        this.options.reporter.sampleAtSend(this.options.platform.now(), observation => {
          acknowledged = this.options.connection.request('screen', observation).then(reply => {
            if (!reply.ok) throw new Error(reply.error || 'The scientific window could not be observed.');
          });
        });
        await acknowledged;
        current();
      }
      const headers = new Headers(init.headers);
      headers.set('X-Micromind-Workspace-Grant', grant!);
      headers.set('X-MicroscopeMind', 'scientific-review');
      const response = await this.options.platform.fetch(path, { ...init, method, headers,
        credentials: 'same-origin', redirect: 'error' });
      try { current(); }
      catch (error) { await response.body?.cancel().catch(() => {}); throw error; }
      return response;
    };
    return { request, check: current };
  }
  private attempt: { promise: Promise<void> } | null = null;
  private pair: WorkspacePair | null = null;
  // Local placement intent is bound before a delayed native delivery can be adopted.
  // Keep unanswered intents until delivery/disconnect; timeouts cannot retarget them.
  private readonly retainedOpenOrigins = new Map<string, { project: string; tab: string; foreground:boolean; executionRequestId?: string; saved: import('./workspace-study-restoration').StudyRetainedView }>();
  private reportError: { report: ScreenReportFailure; cleanup: () => void } | null = null;
  // All track, picker and pending acquisition state belongs to this accepted Session.
  private tab: TabResource | null = null;
  private picking: object | null = null;
  private sample: TabSample | null = null;
  private unavailable: WorkspaceCaptureCode = 'share-required';
  private disposed = false;
  private readonly proposal: WorkspaceSessionOptions['proposal'];
  private readonly options: WorkspaceSessionOptions;
  constructor(options: WorkspaceSessionOptions) {
    this.options = options; this.proposal = Object.freeze({ ...options.proposal });
    options.reporter.useWorkspace((sample) => this.collect(sample), () => this.retireReportError());
  }
  connected(value: boolean): Promise<void> {
    if (!value) { this.retire(); return Promise.resolve(); }
    if (this.disposed) return Promise.reject(new Error('Workspace connection is disposed'));
    if (this.attempt) return this.attempt.promise;
    const attempt = { promise: Promise.resolve() }; this.attempt = attempt;
    attempt.promise = this.options.connection.request('workspace-hello', this.proposal).then((ready) => {
      if (this.disposed || this.attempt !== attempt) return;
      const context = { ...ready.client, appRunId: ready.appRunId }, options = this.options;
      let windows: WorkspaceWindows | null = null, runtime: WorkspaceRuntime | null = null;
      try {
        windows = new WorkspaceWindows({ background: requestId => [...this.retainedOpenOrigins.values()].some(origin=>origin.executionRequestId===requestId&&!origin.foreground), focusFallback: options.focusFallback, onPresentation: (id, root, title, foreground, requestId) => {
          const entry = [...this.retainedOpenOrigins].find(([, origin]) => origin.executionRequestId === requestId);
          const origin = entry?.[1];
          if (origin) {
            const ref = studyResourceReference(JSON.parse(root.dataset.studyRetained ?? 'null')?.resource);
            if (!ref || !sameStudyResource(ref, origin.saved.resource)) throw new Error('The reopened evidence does not match its requested resource.');
            root.dataset.project = origin.project; root.dataset.workspaceView = origin.tab;
            if (origin.saved.placementId) root.dataset.placementId = origin.saved.placementId;
            root.dataset.workspaceHidden = String(document.body.dataset.workspaceProject !== origin.project || document.body.dataset.activeWorkspaceView !== origin.tab);
            this.retainedOpenOrigins.delete(entry![0]);
          }
          options.onPresentation?.(id, root, title, foreground && root.dataset.workspaceHidden !== 'true');
        }, host: options.stage, hiddenHost: options.hiddenHost, panels: options.panels, context, facts: options.windowFacts,
          openCapture: (target, parent) => this.openResearchCapture(target, parent),
          retryLive: (id) => runtime?.retryLive(id),
          send: (command) => { if (!runtime || this.pair?.runtime !== runtime) throw new Error('Workspace control is no longer accepted'); runtime.manual(command); } });
        runtime = new WorkspaceRuntime({ context, host: options.interactionHost, foregroundHost: options.stage, windows, viewport: options.viewport, now: options.platform.now,
          send: (command) => { if (!options.connection.send(command as ClientCommand)) throw new Error('Workspace control transport is not open'); },
          structural: () => options.reporter.observeLegacy(options.legacy(), context.workspaceId, options.selected()),
          fetch: (input, init) => {
            const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, options.platform.sourceOrigin);
            if (url.origin !== options.platform.sourceOrigin || !/^\/api\/presentation-sources\/[A-Za-z0-9_-]+$/.test(url.pathname) || url.search || url.hash) throw new Error('Native source URL is outside the accepted origin/route');
            const headers = new Headers(init?.headers); headers.set('X-Micromind-Workspace-Grant', ready.connectionGrant);
            return options.platform.fetch(input, { ...init, headers });
          }, createObjectURL: options.platform.createObjectURL, revokeObjectURL: options.platform.revokeObjectURL });
        this.pair = { windows, runtime, context }; this.voiceGrant = ready.connectionGrant;
        options.reporter.askNow(); options.onReadyChange?.(true);
      } catch (error) { runtime?.dispose(); windows?.dispose(); throw error; }
    }).catch((error) => { if (!this.disposed && this.attempt === attempt) throw error; });
    return attempt.promise;
  }
  private retire(): void {
    this.voiceGrant = null;
    this.retainedOpenOrigins.clear();
    this.attempt = null; const pair = this.pair; this.pair = null;
    this.options.onReadyChange?.(false);
    this.reportError?.cleanup(); this.reportError = null;
    this.releaseTab('session-retired');
    pair?.runtime.dispose(); pair?.windows.dispose(); this.options.reporter.retireWorkspace();
  }
  message(message: ServerMessage): boolean {
    if (message.type === 'session-cleared') {
      // App.reset_all has released native source leases. Tab hiding alone never enters here.
      this.retainedOpenOrigins.clear(); this.pair?.runtime.clearProject();
      this.reportError?.cleanup(); this.reportError = null;
      this.options.reporter.retireWorkspace();
      return false; // main still owns Project/source navigation and scientific document lifetimes.
    }
    if (message.type === 'presentation-admitted') {
      const pair = this.pair, origin = this.retainedOpenOrigins.get(message.requestId);
      if (pair && origin && !origin.executionRequestId && message.appRunId === pair.context.appRunId
        && typeof message.executionRequestId === 'string' && message.executionRequestId.length > 0 && message.executionRequestId.length <= 256
        && Object.entries(pair.context).filter(([key]) => key !== 'appRunId')
          .every(([key, value]) => message.client?.[key as keyof typeof message.client] === value)
        && ![...this.retainedOpenOrigins.values()].some(item => item.executionRequestId === message.executionRequestId)) {
        origin.executionRequestId = message.executionRequestId;
      }
      return true;
    }
    if (message.type === 'result' && message.for === 'present') this.pair?.runtime.manualResult(message);
    if (message.type === 'result' && message.for === 'screen' && message.ok === false
      && message.code === 'SOURCE_UNRESOLVED' && typeof message.error === 'string' && validScreenReportFailure(message.screenReport)) {
      const ref = message.screenReport, pair = this.pair;
      if (pair && !this.disposed && ref.appRunId === pair.context.appRunId
        && Object.entries(pair.context).filter(([key]) => key !== 'appRunId')
          .every(([key, value]) => ref.client[key as keyof typeof ref.client] === value)) {
        const state = this.options.reporter.sourceReportState(ref);
        if (state === 'retired') return true;
        if (state === 'current' && this.options.onReportError) {
          this.reportError?.cleanup(); this.reportError = null;
          const cleanup = this.options.onReportError(message.error);
          if (typeof cleanup === 'function') {
            this.reportError = { report: deepFreeze(structuredClone(ref)), cleanup }; return true;
          }
        }
      }
    }
    if (message.type === 'screen-look' && message.schema === 'workspace-capture-v1') {
      this.captureTab(message); return true;
    }
    if (message.type !== 'present') return false;
    if (!('schema' in message)) {
      // Bind the full original legacy wire before main's two-argument completion
      // caller; no second registry or newest-target lookup supplies its intent.
      this.legacyApplied(message.change, message.id, message); return false;
    }
    if (message.schema !== 'presentation-command-v2') return false;
    const pair = this.pair;
    if (!pair || this.disposed) { if (!this.disposed) this.options.onError(new Error('Canonical presentation arrived before workspace acceptance')); return true; }
    void pair.runtime.receive(message).then(() => { if (this.pair === pair) this.reportApplied(); }, (error) => {
      if (this.pair !== pair) return; this.options.onError(error instanceof Error ? error : new Error(String(error))); this.reportApplied();
    }); return true;
  }
  /** Reported once applied: from the next animation frame, or at once in the background, which draws no frames
   * (as the legacy panels' changes are, main.ts applyPresent), so the app knows what this studio was given. */
  private reportApplied(): void {
    this.options.reporter.askNow();
    if (typeof document !== 'undefined' && document.hidden) {
      try { this.options.reporter.tick(performance.now()); } catch (error) { this.options.onError(error instanceof Error ? error : new Error(String(error))); }
    }
  }
  private retireReportError(): void {
    const owned = this.reportError;
    if (owned && this.options.reporter.sourceReportState(owned.report, true) === 'retired') {
      this.reportError = null; owned.cleanup();
    }
  }
  /** Both immediate and deferred callers retain the original accepted pair and target. */
  legacyApplied(changeId: string, targetId: string, original?: PresentMessage): () => void {
    const pair = this.pair;
    return this.options.reporter.legacyApplied(changeId, targetId,
      () => !this.disposed && pair !== null && this.pair === pair, () => this.options.legacy().surfaces, original);
  }
  claimLegacyForeground(original: PresentMessage): boolean {
    return !!this.pair && !this.disposed && this.options.reporter.claimLegacyForeground(original);
  }
  private sharingState(state: 'preparing' | 'sharing' | 'muted' | WorkspaceCaptureCode): void {
    this.options.onWorkspaceSharingChange?.(state);
  }
  private stopTracks(stream: MediaStream): void {
    for (const track of stream.getTracks()) if (track.readyState !== 'ended') track.stop();
  }
  private releaseTab(code: WorkspaceCaptureCode): void {
    this.picking = null; this.unavailable = code;
    const sample = this.sample;
    if (sample) {
      sample.active = false; clearTimeout(sample.timer);
      if (sample.frameId !== null) sample.resource.video.cancelVideoFrameCallback(sample.frameId);
      sample.frameId = null; sample.cancel?.(code);
      if (sample.canvas) { sample.canvas.width = 0; sample.canvas.height = 0; }
    }
    const tab = this.tab; this.tab = null;
    if (tab) {
      tab.removeListeners(); tab.cancelReady?.(); tab.video.pause(); tab.video.srcObject = null; this.stopTracks(tab.stream);
    }
    this.sharingState(code);
  }
  stopWorkspaceSharing(): void { this.releaseTab('share-required'); }
  /** The granted tab share's newest frame, at most `maxSide` px (the one-brain voice's window snapshot): copied in the
   * asking task, drawn and encoded off it; null without a live share, so the caller captures the window instead. */
  async shareSnapshot(maxSide = 1280, quality = 0.8): Promise<{ jpeg: string; w: number; h: number } | null> {
    const tab = this.tab;
    if (!tab || this.disposed || !this.pair || tab.track.readyState !== 'live' || !tab.track.enabled || tab.track.muted
      || tab.video.srcObject !== tab.stream || tab.video.readyState < 2 || !tab.video.videoWidth || !tab.video.videoHeight) return null;
    const bitmap = await createImageBitmap(tab.video).catch(() => null);
    if (!bitmap) return null;
    try { return await encodePicture(bitmap, maxSide, quality); } catch { return null; } finally { bitmap.close(); }
  }
  /** Must be called directly by the user's share click, before transient activation ends. */
  async shareWorkspaceTab(): Promise<void> {
    this.releaseTab('share-required');
    const pair = this.pair, devices = navigator.mediaDevices as CaptureHandleDevices | undefined;
    if (this.disposed || !pair) { this.unavailable = 'session-retired'; this.sharingState(this.unavailable); return; }
    if (this.options.platform.sourceOrigin !== location.origin) { this.unavailable = 'wrong-surface'; this.sharingState(this.unavailable); return; }
    if (!devices?.getDisplayMedia || !devices.setCaptureHandleConfig) { this.unavailable = 'unsupported-api'; this.sharingState(this.unavailable); return; }
    const picking = {}; this.picking = picking;
    let stream: MediaStream | null = null;
    try {
      devices.setCaptureHandleConfig({ exposeOrigin: true, handle: this.proposal.pageEpoch, permittedOrigins: [location.origin] });
      // The picker is entered synchronously. Preference labels are never identity proof.
      const returned = devices.getDisplayMedia({ video: { width: { ideal: 1920 }, height: { ideal: 1080 } }, audio: false,
        preferCurrentTab: true, selfBrowserSurface: 'include' } as DisplayMediaStreamOptions);
      stream = await returned;
      if (this.disposed || this.pair !== pair || this.picking !== picking) { this.stopTracks(stream); return; }
      const tracks = stream.getVideoTracks();
      if (tracks.length !== 1 || stream.getTracks().length !== 1) throw new CaptureUnavailable('wrong-surface');
      const track = tracks[0] as CaptureHandleTrack;
      this.trackHandle(track);
      if (track.readyState !== 'live' || !track.enabled) throw new CaptureUnavailable('track-unavailable');
      const video = document.createElement('video'); video.playsInline = true; video.muted = true; video.autoplay = true;
      const tab: TabResource = { pair, stream, track, video, id: crypto.randomUUID(), removeListeners: () => {}, cancelReady: null };
      const ended = () => { if (this.tab === tab) this.releaseTab('track-unavailable'); };
      const changed = () => {
        if (this.tab !== tab) return;
        try { this.trackHandle(track); this.releaseTab('wrong-surface'); }
        catch (error) { this.releaseTab(error instanceof CaptureUnavailable ? error.code : 'wrong-surface'); }
      };
      const muted = () => {
        if (this.tab !== tab) return;
        if (track.muted) this.sample?.cancel?.('track-unavailable');
        this.sharingState(track.muted ? 'muted' : 'sharing');
      };
      track.addEventListener('ended', ended); track.addEventListener('capturehandlechange', changed);
      track.addEventListener('mute', muted); track.addEventListener('unmute', muted);
      tab.removeListeners = () => {
        track.removeEventListener('ended', ended); track.removeEventListener('capturehandlechange', changed);
        track.removeEventListener('mute', muted); track.removeEventListener('unmute', muted);
      };
      this.tab = tab; video.srcObject = stream;
      this.sharingState('preparing');
      if (this.tab !== tab || this.pair !== pair || this.picking !== picking || this.disposed) return;
      let timer: ReturnType<typeof setTimeout> | undefined;
      try {
        await Promise.race([video.play(), new Promise<never>((_, reject) => {
          tab.cancelReady = () => reject(new CaptureUnavailable('session-retired'));
          timer = setTimeout(() => reject(new CaptureUnavailable('track-unavailable')), 5000);
        })]);
      } finally { if (timer !== undefined) clearTimeout(timer); tab.cancelReady = null; }
      if (this.tab !== tab || this.pair !== pair || this.picking !== picking || this.disposed) return;
      this.trackHandle(track); this.picking = null; this.sharingState(track.muted ? 'muted' : 'sharing');
    } catch (error) {
      if (stream) this.stopTracks(stream);
      if (this.picking !== picking) return;
      const code = error instanceof CaptureUnavailable ? error.code
        : error instanceof DOMException && error.name === 'NotAllowedError' ? 'permission-declined' : 'unsupported-api';
      this.releaseTab(code);
    }
  }
  private trackHandle(track: CaptureHandleTrack): { origin: string; handle: string } {
    if (!track.getCaptureHandle) throw new CaptureUnavailable('unsupported-handle');
    const handle = track.getCaptureHandle();
    if (!handle || !handle.origin || !handle.handle) throw new CaptureUnavailable('unsupported-handle');
    if (handle.origin !== location.origin || handle.origin !== this.options.platform.sourceOrigin || handle.handle !== this.proposal.pageEpoch
      || track.getSettings().displaySurface !== 'browser') throw new CaptureUnavailable('wrong-surface');
    return { origin: boundedIdentity(handle.origin), handle: boundedIdentity(handle.handle) };
  }
  private correlation(request: WorkspaceCaptureRequest) {
    return { type: 'screen-image' as const, schema: 'workspace-capture-v1' as const, id: request.id, lookId: request.lookId,
      requestId: request.requestId, appRunId: request.appRunId, client: request.client, originSnapshotId: request.originSnapshotId };
  }
  private refuseCapture(request: WorkspaceCaptureRequest, pair: WorkspacePair, code: WorkspaceCaptureCode): void {
    if (this.disposed || this.pair !== pair) return;
    this.options.connection.send({ ...this.correlation(request), status: 'unavailable', code });
  }
  private captureTab(packet: WorkspaceCaptureRequest): void {
    const pair = this.pair;
    // Foreign or malformed packets never start acquisition or settle a different look.
    if (!pair || this.disposed) return;
    const keys = ['type', 'schema', 'id', 'lookId', 'requestId', 'appRunId', 'client', 'originSnapshotId', 'layoutRevision', 'targetIds', 'maxBytes', 'maxPixels', 'maxInputEdge', 'deadlineMono', 'timeoutMs'];
    const context = pair.context;
    try {
      if (Object.keys(packet).some((key) => !keys.includes(key)) || !packet.client
        || Object.keys(packet.client).length !== 4 || !['workspaceId', 'clientId', 'pageEpoch', 'connectionEpoch'].every((key) =>
          packet.client[key as keyof WorkspaceReady['client']] === context[key as keyof WorkspaceReady['client']])
        || packet.appRunId !== context.appRunId) return;
      [packet.id, packet.lookId, packet.requestId, packet.originSnapshotId].forEach(boundedIdentity);
      if (!Number.isSafeInteger(packet.layoutRevision) || packet.layoutRevision < 0 || packet.id !== packet.lookId
        || !Array.isArray(packet.targetIds) || packet.targetIds.length > 40) return;
      packet.targetIds.forEach(boundedIdentity);
      if (![packet.maxBytes, packet.maxPixels, packet.maxInputEdge].every((v) => Number.isSafeInteger(v) && v > 0)
        || !Number.isFinite(packet.deadlineMono) || typeof packet.timeoutMs !== 'number' || !Number.isFinite(packet.timeoutMs)
        || packet.timeoutMs <= 0 || packet.timeoutMs > 30000) return;
    } catch { return; }
    const request = deepFreeze(structuredClone(packet));
    if (this.sample) {
      const signature = (value: WorkspaceCaptureRequest) => JSON.stringify([value.id, value.lookId, value.requestId, value.appRunId,
        value.client.workspaceId, value.client.clientId, value.client.pageEpoch, value.client.connectionEpoch, value.originSnapshotId,
        value.layoutRevision, value.targetIds, value.maxBytes, value.maxPixels, value.maxInputEdge, value.deadlineMono, value.timeoutMs]);
      if (signature(this.sample.request) !== signature(request)) this.refuseCapture(request, pair, 'sample-unavailable');
      return; // Identical packet joins; it does not refresh the original deadline.
    }
    const resource = this.tab;
    if (!resource) { this.refuseCapture(request, pair, this.unavailable); return; }
    const sample: TabSample = { request, pair, resource, active: true, cancel: null, frameId: null, canvas: null,
      deadline: this.options.platform.now() + request.timeoutMs, timer: setTimeout(() => {
        sample.active = false; sample.cancel?.('sample-unavailable');
      }, request.timeoutMs) };
    this.sample = sample;
    void this.sampleTab(sample).catch((error) => {
      this.refuseCapture(request, pair, error instanceof CaptureUnavailable ? error.code : 'sample-unavailable');
    }).finally(() => {
      sample.active = false; clearTimeout(sample.timer);
      if (sample.frameId !== null) resource.video.cancelVideoFrameCallback(sample.frameId);
      if (sample.canvas) { sample.canvas.width = 0; sample.canvas.height = 0; }
      sample.cancel = null; if (this.sample === sample) this.sample = null;
    });
  }
  private checkSample(sample: TabSample): { origin: string; handle: string } {
    const { resource } = sample;
    if (!sample.active || this.disposed || this.pair !== sample.pair || this.tab !== resource) throw new CaptureUnavailable('session-retired');
    if (this.options.platform.now() > sample.deadline) throw new CaptureUnavailable('sample-unavailable');
    if (resource.track.readyState !== 'live' || !resource.track.enabled || resource.track.muted || resource.video.srcObject !== resource.stream
      || resource.video.readyState < 2) throw new CaptureUnavailable('track-unavailable');
    return this.trackHandle(resource.track);
  }
  private awaitSample<T>(sample: TabSample, start: (done: (value: T) => void, fail: (code: WorkspaceCaptureCode) => void) => void): Promise<T> {
    return new Promise((resolve, reject) => {
      const fail = (code: WorkspaceCaptureCode) => { if (sample.cancel === fail) sample.cancel = null; reject(new CaptureUnavailable(code)); };
      sample.cancel = fail;
      const done = (value: T) => { if (sample.cancel !== fail) return; sample.cancel = null; resolve(value); };
      try { start(done, fail); } catch { fail('sample-unavailable'); }
    });
  }
  private fullViewport(viewport: WorkspaceViewport): void {
    const [width, height] = viewport.layoutSize;
    if (!viewport.visible || !viewport.documentFocused || document.visibilityState !== 'visible' || !document.hasFocus()) throw new CaptureUnavailable('sample-unavailable');
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0 || viewport.visualScale !== 1
      || JSON.stringify(viewport.visualRect) !== JSON.stringify([0, 0, width, height])) throw new CaptureUnavailable('unsupported-mapping');
  }
  private async sampleTab(sample: TabSample): Promise<void> {
    this.checkSample(sample);
    const before = this.collect({ seq: 0, nowMs: this.options.platform.now() });
    if (!before) throw new CaptureUnavailable('session-retired');
    this.fullViewport(before.viewport);
    if (before.layoutRevision !== sample.request.layoutRevision) throw new CaptureUnavailable('layout-changed');
    const { video, track } = sample.resource, request = sample.request;
    const maxPixels = Math.min(request.maxPixels, 4_000_000), maxEdge = Math.min(request.maxInputEdge, 1920), maxBytes = Math.min(request.maxBytes, 6 * 1024 * 1024);
    const dimensions = (width: number, height: number) => {
      if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0
        || width * height > maxPixels) throw new CaptureUnavailable('bounds-exceeded');
    };
    const settings = track.getSettings();
    if (settings.width !== undefined && settings.height !== undefined) dimensions(settings.width, settings.height);
    dimensions(video.videoWidth, video.videoHeight); // Probe actual decoder size before allocating any canvas.
    if (typeof video.requestVideoFrameCallback !== 'function') throw new CaptureUnavailable('unsupported-api');
    const sampledBefore = this.options.platform.now();
    const frame = await this.awaitSample<VideoFrameCallbackMetadata>(sample, (done) => {
      sample.frameId = video.requestVideoFrameCallback((_now, metadata) => {
        sample.frameId = null; if (sample.active && this.sample === sample) done(metadata);
      });
    });
    this.checkSample(sample);
    const width = video.videoWidth, height = video.videoHeight;
    dimensions(width, height); dimensions(frame.width, frame.height);
    if (frame.width !== width || frame.height !== height) throw new CaptureUnavailable('unsupported-mapping');
    const [cssWidth, cssHeight] = before.viewport.layoutSize;
    if (Math.abs(width / height - cssWidth / cssHeight) > 0.001) throw new CaptureUnavailable('unsupported-mapping');
    const factor = Math.min(1, maxEdge / Math.max(width, height));
    const canvas = document.createElement('canvas'); sample.canvas = canvas;
    canvas.width = Math.max(1, Math.round(width * factor)); canvas.height = Math.max(1, Math.round(height * factor));
    const context = canvas.getContext('2d'); if (!context) throw new CaptureUnavailable('encode-failed');
    context.drawImage(video, 0, 0, canvas.width, canvas.height);
    const sampledAfter = this.options.platform.now();
    const blob = await this.awaitSample<Blob>(sample, (done, fail) => {
      canvas.toBlob((value) => { if (!value) fail('encode-failed'); else done(value); }, 'image/jpeg', 0.8);
    });
    this.checkSample(sample);
    if (blob.type !== 'image/jpeg' || !blob.size || blob.size > maxBytes) throw new CaptureUnavailable('bounds-exceeded');
    const buffer = await this.awaitSample<ArrayBuffer>(sample, (done, fail) => { void blob.arrayBuffer().then(done, () => fail('encode-failed')); });
    this.checkSample(sample);
    const hash = await this.awaitSample<ArrayBuffer>(sample, (done, fail) => { void crypto.subtle.digest('SHA-256', buffer).then(done, () => fail('encode-failed')); });
    this.checkSample(sample);
    let binary = ''; const bytes = new Uint8Array(buffer);
    for (let at = 0; at < bytes.length; at += 8192) binary += String.fromCharCode(...bytes.subarray(at, at + 8192));
    const jpeg = btoa(binary), sha256 = [...new Uint8Array(hash)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
    const after = this.collect({ seq: 0, nowMs: this.options.platform.now() });
    if (!after || after.layoutRevision !== before.layoutRevision || JSON.stringify(after.viewport) !== JSON.stringify(before.viewport)) throw new CaptureUnavailable('layout-changed');
    this.fullViewport(after.viewport);
    // Reporter publishes the actual paired sample; success follows in the SAME synchronous stack.
    const report = this.options.reporter.sampleAtSend(this.options.platform.now(), (report) => {
      this.checkSample(sample);
      if (report.layoutRevision !== before.layoutRevision || JSON.stringify(report.viewport) !== JSON.stringify(before.viewport)) throw new CaptureUnavailable('layout-changed');
      if (!this.options.connection.send(report as unknown as ClientCommand)) throw new CaptureUnavailable('session-retired');
    });
    const handle = this.checkSample(sample);
    const reply: WorkspaceCaptureReply = { ...this.correlation(request), status: 'captured', jpeg, sha256,
      capturedObservationId: request.lookId, layoutRevisionBefore: before.layoutRevision, layoutRevisionAfter: report.layoutRevision,
      originalSize: [width, height], inputSize: [canvas.width, canvas.height], workspaceToImage: [canvas.width / cssWidth, 0, 0, canvas.height / cssHeight, 0, 0],
      captureTimeMonoMs: sampledAfter, uncertaintyMs: sampledAfter - sampledBefore, fidelity: 'actual-tab-capture',
      captureMethod: 'authorized-tab-track', surfaceProof: 'same-origin-page-handle', coverage: 'complete', missing: [], redactedRects: [],
      acquisitionBasis: { schema: 'installed-studio-tab-v1', captureSessionId: sample.resource.id, surfaceOrigin: handle.origin,
        pageHandle: handle.handle, structuralReportSeq: report.seq, viewport: structuredClone(report.viewport),
        sampledBeforeMonoMs: sampledBefore, sampledAfterMonoMs: sampledAfter,
        mediaTimeSeconds: Number.isFinite(frame.mediaTime) && frame.mediaTime >= 0 ? frame.mediaTime : null,
        rootPaintInclusion: 'unknown', rootPaintReason: 'tab-track-has-no-atomic-root-paint-manifest' } };
    if (!this.options.connection.send(reply)) throw new CaptureUnavailable('session-retired');
  }
  ask(text: string, fields: { frameId?: number; objectId?: string; capabilityTrial?: string; scientificInput?: import('./protocol').ScientificAskInput } = {}): string {
    if (this.disposed || !this.pair) throw new Error('Workspace is not accepted on this connection');
    if (typeof text !== 'string' || !text.trim()) throw new Error('Question is empty');
    const scientificInput = fields.scientificInput ? structuredClone(fields.scientificInput) : undefined;
    if (scientificInput && (fields.frameId !== undefined || fields.objectId !== undefined || fields.capabilityTrial))
      throw new Error('A saved scientific attachment cannot be combined with a camera selection or trial.');
    const frameId = scientificInput ? undefined : fields.frameId ?? this.options.frame();
    const requestId = crypto.randomUUID();
    this.options.reporter.sampleAtSend(this.options.platform.now(), (screen) => {
      const command = { type: 'ask' as const, requestId, text, ...(frameId !== undefined ? { frameId } : {}),
        ...(fields.objectId !== undefined ? { objectId: fields.objectId } : {}),
        ...(fields.capabilityTrial ? { capabilityTrial: fields.capabilityTrial } : {}), ...(scientificInput ? { scientificInput } : {}), screen };
      if (!this.options.connection.send(command)) throw new Error('Workspace ask transport is not open');
    });
    return requestId;
  }
  openResource(source: { day: string; id: string; version: number | null; picture: number | null }, title: string): void {
    if (this.disposed || !this.pair) throw new Error('Workspace is not accepted on this connection');
    const { appRunId, ...client } = this.pair.context;
    const requestId = crypto.randomUUID(), actionId = crypto.randomUUID();
    this.options.reporter.sampleAtSend(this.options.platform.now(), (screen) => {
      const address = { day: source.day, id: source.id, version: source.version, picture: source.picture };
      const command = { type: 'present' as const, do: 'open' as const, source: address, title, client, appRunId, requestId, actionId, screen };
      if (!this.options.connection.send(command)) throw new Error('Workspace control transport is not open');
    });
  }
  openResearchCapture(target: ResearchCaptureTarget, parentInstance?: string): Promise<void> {
    if (this.disposed || !this.pair) return Promise.reject(new Error('Workspace is not accepted on this connection'));
    const pair = this.pair, selected = structuredClone(target);
    const { appRunId, ...client } = pair.context;
    // The server retains manual request identities across browser connections;
    // Link's per-page rN counter cannot identify this capture globally.
    const requestId = crypto.randomUUID(), actionId = crypto.randomUUID();
    return new Promise((resolve, reject) => {
      this.options.reporter.sampleAtSend(this.options.platform.now(), (screen) => {
        if (this.disposed || this.pair !== pair) { reject(new Error('The requesting workspace connection changed')); return; }
        void this.options.connection.request('research-open-capture', {
          ...selected, ...(parentInstance ? {parentInstance} : {}), client, appRunId,
          actionId, screen,
        }, requestId).then(reply => {
          if (reply.ok !== true || reply.pending === true) {
            const steps = Array.isArray(reply.steps) ? reply.steps : [reply.run];
            const failed = steps.find(row => row && typeof row === 'object' && row.ok === false);
            throw new Error(typeof reply.error === 'string' ? reply.error
              : failed && typeof failed.summary === 'string' ? failed.summary : 'The captured view could not be confirmed in this browser');
          }
          resolve();
        }).catch(reject);
      });
    });
  }
  viewportChanged(): void { this.pair?.windows.viewportChanged(); }
  shownAgain(): void { this.pair?.windows.shownAgain(); }
  retainedViews() { return this.pair?.windows.retainedViews() ?? []; }
  retainedPresentation() { return this.pair?.windows.retainedPresentation() ?? { views: [], missing: [] }; }
  restorePresentation(saved: import('./workspace-study-restoration').StudyRetainedView): boolean {
    return this.pair?.windows.restorePresentation(saved) ?? false;
  }
  frontPresentation(saved: import('./workspace-study-restoration').StudyRetainedView): boolean {
    return this.pair?.windows.frontPresentation(saved) ?? false;
  }
  openRetainedResource(saved: import('./workspace-study-restoration').StudyRetainedView, foreground = true): Promise<void> {
    if (this.disposed || !this.pair) return Promise.reject(new Error('Reconnect before reopening retained evidence.'));
    const existing = this.pair.windows.retainedViews().find(view => { const ref=studyResourceReference(view.resource); return this.pair?.windows.root(view.id).dataset.workspaceView === document.body.dataset.activeWorkspaceView && ref && sameStudyResource(ref,saved.resource) && (!saved.placementId || this.pair?.windows.root(view.id).dataset.placementId===saved.placementId); });
    if(existing){this.pair.windows.restore(existing.id,foreground);return Promise.resolve();}
    const pair = this.pair, { appRunId, ...client } = pair.context;
    const before=new Set(pair.windows.retainedViews().map(view=>view.id));
    const tab=document.body.dataset.activeWorkspaceView, project=document.body.dataset.workspaceProject;
    const origin=project&&tab?{project,tab,foreground,saved:structuredClone(saved)}:null;
    if(origin&&origin.project!==saved.resource.day)return Promise.reject(new Error('Return to the originating Project before reopening this evidence.'));
    if(origin&&this.retainedOpenOrigins.size>=64)return Promise.reject(new Error('Too many views are still opening. Reconnect before retrying.'));
    const requestId=crypto.randomUUID();
    return new Promise((resolve, reject) => {
      this.options.reporter.sampleAtSend(this.options.platform.now(), screen => {
        if (this.disposed || this.pair !== pair) { reject(new Error('The workspace connection changed.')); return; }
        const fields = { do: 'open' as const, source: saved.resource, title: saved.title,
          client, appRunId, actionId: crypto.randomUUID(), screen };
        if(origin)this.retainedOpenOrigins.set(requestId,origin);
        void this.options.connection.request('present', fields, requestId).then(reply => {
          if (reply.ok === false) reject(new Error(reply.error || 'The retained evidence could not be opened.'));
          else {
            const opened=pair.windows.retainedViews().find(view=>{const ref=studyResourceReference(view.resource);return !before.has(view.id)&&ref&&sameStudyResource(ref,saved.resource);});
            if(!origin&&opened&&saved.placementId)pair.windows.root(opened.id).dataset.placementId=saved.placementId;
            resolve();
          }
        }, error => {
          // A server refusal is terminal. A transport timeout is not: a late
          // delivery still needs its original destination until disconnect.
          if (error && typeof error === 'object' && 'reply' in error) this.retainedOpenOrigins.delete(requestId);
          reject(error);
        });
      });
    });
  }
  closeRetainedResource(resource: import('./workspace-study-restoration').StudyResourceReference): void {
    const pair=this.pair;if(!pair)return;
    for(const view of pair.windows.retainedViews()){
      const ref=studyResourceReference(view.resource);
      if(pair.windows.root(view.id).dataset.workspaceView===document.body.dataset.activeWorkspaceView&&ref&&sameStudyResource(ref,resource))pair.windows.floating(view.id).close();
    }
  }
  /** A retained population drawn on (or taken off) this Tab's specimen through the present verb: the Data reader's Show on
   * picture. Resolves once the server has the studio's report that it is shown (done when the studio shows it). */
  showRetainedLayer(resource: import('./workspace-study-restoration').StudyResourceReference, verb: 'show' | 'hide' = 'show'): Promise<void> {
    if (this.disposed || !this.pair) return Promise.reject(new Error(verb === 'hide' ? 'Reconnect to the studio before taking the outlines off the picture.' : 'Reconnect to the studio before showing the outlines on the picture.'));
    const pair = this.pair;
    return new Promise((resolve, reject) => {
      this.options.reporter.sampleAtSend(this.options.platform.now(), screen => {
        if (this.disposed || this.pair !== pair) { reject(new Error('The workspace connection changed.')); return; }
        // Shown by its entry (as Measurements' Show on the view does); hidden as the layer on screen (layer-controls.ts).
        void this.options.connection.request('present', {what:verb === 'hide' ? `layer:${resource.id}` : resource.id,do:verb,revision:resource.version??0,screen}).then(reply => {
          if (reply.ok === false) reject(new Error(String(reply.error || reply.summary || (verb === 'hide' ? 'The outlines could not be taken off the picture.' : 'The outlines could not be shown on the picture.'))));
          else resolve();
        },reject);
      });
    });
  }
  livePlacements(point: Parameters<WorkspaceWindows['livePlacements']>[0]) {
    return this.pair?.windows.livePlacements(point) ?? [];
  }
  restoreWindow(id: string): void {
    if (!this.pair) throw new Error('Workspace is not accepted on this connection');
    this.pair.windows.restore(id);
  }
  collect(sample: WorkspaceReportSample): WorkspaceObservation | null {
    if (this.disposed || !this.pair) return null;
    return this.pair.runtime.collect({ ...sample, selected: [] });
  }
  dispose(): void { if (this.disposed) return; this.disposed = true; this.retire(); }
}

/** The windows and panels captureWindow draws: each is a frame of its own, filled with its background, with its
 * pictures inside it and its title on its bar. */
export const SCREEN_FRAMES = '[data-screen-title], section.workspace-window, #study-artifacts > [data-placement-id], #workspace-tabs-strip, #session-rail';
const SCREEN_TEXT_MS = 70; // text (titles, Tab names, table rows) stops being read past this much work
/** captureWindow's own work in any one task stays about this short (ms), so the live view keeps every frame. */
export const SLICE_MS = 6;

type Box = { left: number; top: number; width: number; height: number };
interface ScreenNode { el: Element; rect: Box }
type ScreenText = PaintText;
/** What captureWindow draws, gathered from the page (screenLayout): frames in paint order, pictures with the frame
 * they are in, and text lines at their places. Plain data, so it is tested without a browser. */
export interface ScreenLayout {
  frames: (ScreenNode & { title: string; background: string; z: number })[];
  pictures: (ScreenNode & { frame: number | null; opacity: number })[];
  text: ScreenText[];
}

const shown = (el: Element, view: [number, number]): Box | null => {
  const r = el.getBoundingClientRect();
  if (r.width <= 4 || r.height <= 4 || r.right <= 0 || r.bottom <= 0 || r.left >= view[0] || r.top >= view[1]) return null;
  if ((el as HTMLElement).closest?.('[hidden], [data-workspace-hidden="true"]')) return null;
  const style = getComputedStyle(el);
  if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return null;
  return { left: r.left, top: r.top, width: r.width, height: r.height };
};
const words = (value: string | null | undefined, max = 120) => (value ?? '').replace(/\s+/g, ' ').trim().slice(0, max);

/** A frame's title: a window's own title bar, else its heading, else what it is called. */
export function frameTitle(el: Element): string {
  const own = el.querySelector('.workspace-window-title, [id$="-title"], h1, h2, h3, header');
  const title = words(own?.textContent, 80);
  const name = (el as HTMLElement).dataset?.screenTitle ?? el.getAttribute('aria-label') ?? '';
  return title && name && title !== name ? `${name}: ${title}` : title || words(name, 80);
}

/** The windows and panels shown, in paint order: by z-index, then page order (a later sibling covers an earlier one). */
function layoutFrames(root: ParentNode, view: [number, number]): ScreenLayout['frames'] {
  const frames: ScreenLayout['frames'] = [];
  for (const el of Array.from(root.querySelectorAll(SCREEN_FRAMES))) {
    const rect = shown(el, view);
    if (!rect) continue;
    const style = getComputedStyle(el);
    frames.push({ el, rect, title: frameTitle(el), background: style.backgroundColor, z: Number(style.zIndex) || 0 });
  }
  return frames.map((f, i) => ({ f, i })).sort((a, b) => a.f.z - b.f.z || a.i - b.i).map(x => x.f);
}

/** The pictures shown (canvas, img, video) with the frame each is in; never the voice's own marks. */
function layoutPictures(root: ParentNode, view: [number, number], frames: ScreenLayout['frames']): ScreenLayout['pictures'] {
  const pictures: ScreenLayout['pictures'] = [];
  for (const el of Array.from(root.querySelectorAll('canvas, img, video'))) {
    if ((el as HTMLElement).closest?.('#voice-marks')) continue;
    const rect = shown(el, view);
    if (!rect) continue;
    const holder = (el as HTMLElement).closest?.(SCREEN_FRAMES);
    const at = holder ? frames.findIndex(f => f.el === holder) : -1;
    pictures.push({ el, rect, frame: at < 0 ? null : at, opacity: Number(getComputedStyle(el).opacity || 1) });
  }
  return pictures;
}

/** The text that says what is shown, one element read at a time (null when it had nothing), so a reader can stop or
 * pause between any two: the frames' titles, the Tab names (the one in front selected), the table rows in view cell
 * by cell (60 rows, 12 cells at most) and headings. */
function* layoutText(root: ParentNode, view: [number, number], frames: ScreenLayout['frames']): Generator<ScreenText | null, void> {
  for (const f of frames) if (f.title) yield { text: f.title, rect: { left: f.rect.left, top: f.rect.top, width: f.rect.width, height: 20 }, kind: 'title' };
  for (const tab of Array.from(root.querySelectorAll('.workspace-tab-label'))) {
    const rect = shown(tab, view);
    yield rect ? { text: words(tab.textContent, 40), rect, kind: 'tab', selected: !!tab.closest('[aria-selected="true"]') } : null;
  }
  let rows = 0;
  for (const table of Array.from(root.querySelectorAll('table'))) {
    if (rows >= 60) break;
    if (!shown(table, view)) { yield null; continue; }
    for (const row of Array.from((table as HTMLTableElement).rows ?? [])) {
      if (rows >= 60) break;
      const rect = shown(row, view);
      if (!rect) { yield null; continue; }
      rows++;
      for (const cell of Array.from(row.cells ?? []).slice(0, 12)) {
        const box = shown(cell, view), value = words(cell.textContent, 40);
        yield box && value ? { text: value, rect: box, kind: cell.tagName === 'TH' ? 'header' : 'cell' } : null;
      }
    }
  }
  let headings = 0;
  for (const heading of Array.from(root.querySelectorAll('h1, h2, h3, .mm-row-title'))) {
    if (headings++ > 200) break;
    if (heading.closest('table')) continue;
    const rect = shown(heading, view), value = words(heading.textContent, 80);
    yield rect && value ? { text: value, rect, kind: heading.classList.contains('mm-row-title') ? 'row' : 'heading' } : null;
  }
}

/** Kept unless it says again what is already written there (a heading that is also a frame's title). */
function addText(text: ScreenText[], item: ScreenText): void {
  if (text.length >= 400) return;
  if ((item.kind === 'heading' || item.kind === 'row') && text.some(t => t.text === item.text && Math.abs(t.rect.top - item.rect.top) < 24)) return;
  text.push(item);
}

/** The page as captureWindow draws it, in the viewport `view` (CSS px), at once (tests and tools). */
export function screenLayout(root: ParentNode, view: [number, number], budgetMs = SCREEN_TEXT_MS, now: () => number = () => performance.now()): ScreenLayout {
  const started = now();
  const frames = layoutFrames(root, view);
  const pictures = layoutPictures(root, view, frames);
  const text: ScreenText[] = [];
  for (const item of layoutText(root, view, frames)) {
    if (item) addText(text, item);
    if (now() - started > budgetMs) break;
  }
  return { frames, pictures, text };
}

export type WindowPicture = { jpeg: string; w: number; h: number };
type Surface = { width: number; height: number; context: CanvasRenderingContext2D | OffscreenCanvasRenderingContext2D | null };
/** What a capture copies pictures and paints with: the browser's own (browserCapture), or a test's. */
export interface CaptureEnv {
  /** The picture as it is now; null when it cannot be read back (another origin's, not loaded). Called in the task
   * the capture starts in, so the picture is the one on screen then. */
  grab(el: Element): Promise<ImageBitmap | null>;
  background(): string;
  /** Paint and encode the plan (the bitmaps are the painter's to close): a worker's, else here in short steps. */
  paint(plan: PaintPlan, quality: number, pause: () => Promise<void>, now: () => number): Promise<WindowPicture>;
}

const sameOrigin = (src: string) => {
  try { const url = new URL(src, location.href); return url.protocol === 'blob:' || url.protocol === 'data:' || url.origin === location.origin; }
  catch { return false; }
};

/** Paint and encode on this thread, a step at a time (no step over SLICE_MS), then encode in a task of its own: for a
 * browser whose workers have no canvas. */
export async function paintInSteps(plan: PaintPlan, quality: number, pause: () => Promise<void>, now: () => number, io: {
  surface(width: number, height: number): Surface; encode(surface: Surface, quality: number): Promise<Blob>; base64(blob: Blob): Promise<string> }): Promise<WindowPicture> {
  const [w, h] = paintSize(plan);
  const surface = io.surface(w, h);
  try {
    if (!surface.context) return { jpeg: '', w: 0, h: 0 };
    let sliceAt = now();
    for (const step of paintSteps(surface.context, plan)) {
      void step;
      if (now() - sliceAt >= SLICE_MS) { await pause(); sliceAt = now(); }
    }
    await pause();
    const jpeg = await io.base64(await io.encode(surface, quality));
    return { jpeg, w: surface.width, h: surface.height };
  } finally {
    for (const p of plan.pictures) p.bitmap?.close();
  }
}

const browserIO = {
  surface: (width: number, height: number): Surface => {
    if (typeof OffscreenCanvas === 'function') {
      const canvas = new OffscreenCanvas(width, height);
      return Object.assign(canvas, { context: canvas.getContext('2d') });
    }
    const canvas = Object.assign(document.createElement('canvas'), { width, height });
    return Object.assign(canvas, { context: canvas.getContext('2d') });
  },
  encode: (surface: Surface, quality: number): Promise<Blob> => 'convertToBlob' in surface
    ? (surface as unknown as OffscreenCanvas).convertToBlob({ type: 'image/jpeg', quality })
    : new Promise((resolve, reject) => (surface as unknown as HTMLCanvasElement).toBlob((blob) => blob ? resolve(blob) : reject(new Error('encode failed')), 'image/jpeg', quality)),
  base64: base64Of,
};

/** The capture worker (capture-worker.ts), made on first use; null once it has shown it cannot paint here. */
type Painter = { worker: Worker; jobs: Map<number, { resolve: (picture: WindowPicture) => void; reject: (error: Error) => void }>; next: number };
let painter: Painter | null = null;
let painterBroken = typeof Worker !== 'function' || typeof OffscreenCanvas !== 'function';
function paintWorker() {
  if (painterBroken) return null;
  if (painter) return painter;
  try {
    const worker = new Worker(new URL('./capture-worker.ts', import.meta.url), { type: 'module' });
    const made: Painter = { worker, jobs: new Map(), next: 0 };
    worker.onmessage = (event: MessageEvent<{ id: number; jpeg?: string; w?: number; h?: number; error?: string }>) => {
      const job = made.jobs.get(event.data.id);
      made.jobs.delete(event.data.id);
      if (!job) return;
      if (event.data.error !== undefined) {
        painterBroken = true; painter = null; worker.terminate(); // its canvas cannot paint here: paint on this thread from now on
        job.reject(new Error(event.data.error));
      } else job.resolve({ jpeg: event.data.jpeg ?? '', w: event.data.w ?? 0, h: event.data.h ?? 0 });
    };
    worker.onerror = () => {
      painterBroken = true; painter = null; worker.terminate();
      for (const job of made.jobs.values()) job.reject(new Error('the capture worker stopped'));
      made.jobs.clear();
    };
    painter = made;
    return made;
  } catch {
    painterBroken = true;
    return null;
  }
}

/** The browser's: pictures copied with createImageBitmap in the asking task, painted and encoded in the capture worker
 * (the bitmaps moved to it, not copied), else on this thread in short steps. */
export const browserCapture: CaptureEnv = {
  grab: async (el) => {
    if (el instanceof HTMLImageElement && (!el.complete || !el.naturalWidth || !sameOrigin(el.currentSrc || el.src))) return null;
    if (el instanceof HTMLVideoElement && el.readyState < 2) return null;
    if (el instanceof HTMLCanvasElement && (!el.width || !el.height)) return null;
    try { return await createImageBitmap(el as ImageBitmapSource); } catch { return null; }
  },
  background: () => getComputedStyle(document.body).backgroundColor || '#111',
  paint: (plan, quality, pause, now) => {
    const worker = paintWorker();
    if (!worker) return paintInSteps(plan, quality, pause, now, browserIO);
    return new Promise<WindowPicture>((resolve, reject) => {
      const id = ++worker.next;
      const timer = setTimeout(() => { if (worker.jobs.delete(id)) reject(new Error('the capture took too long')); }, 3_000);
      worker.jobs.set(id, { resolve: (p) => { clearTimeout(timer); resolve(p); }, reject: (e) => { clearTimeout(timer); reject(e); } });
      const bitmaps = plan.pictures.map(p => p.bitmap).filter((b): b is ImageBitmap => !!b);
      worker.worker.postMessage({ id, plan, quality }, bitmaps);
    });
  },
};

/** The next task (a message to ourselves): the browser may paint a frame before it. */
export function nextTask(): Promise<void> {
  return new Promise((resolve) => {
    const channel = new MessageChannel();
    channel.port1.onmessage = () => { channel.port1.close(); resolve(); };
    channel.port2.postMessage(null);
  });
}

/** A picture (a shared tab's frame) at most `maxSide` px, JPEG, base64: painted and encoded off the asking task. */
export async function encodePicture(source: ImageBitmap, maxSide: number, quality: number, env: CaptureEnv = browserCapture): Promise<WindowPicture | null> {
  const box = { left: 0, top: 0, width: source.width, height: source.height };
  const plan: PaintPlan = { view: [source.width, source.height], scale: Math.min(1, maxSide / Math.max(source.width, source.height, 1)),
    background: '#000', pictures: [{ bitmap: source, rect: box, frame: null, opacity: 1 }], frames: [], text: [] };
  const picture = await env.paint(plan, quality, nextTask, () => performance.now());
  return picture.jpeg ? picture : null;
}

/** A picture of the window as it is now, for a model to read, without holding up the live view (review 2026-10-29):
 * the pictures are copied in the task that asks (so they are the ones on screen at the key press), then the text is
 * read, the picture drawn and encoded in later tasks, none with more than about SLICE_MS of this work. The live view
 * and every picture are at their places, each window and panel filled, outlined and titled, and the text that says
 * what they are (Tab names, table rows in view, headings) written where it is. At most `maxSide` px; JPEG, base64.
 * `longestTaskMs` is the longest single task of its own work (the frame-timing probe's number). */
export async function captureWindow(root: HTMLElement = document.body, options: { quality?: number; maxSide?: number; view?: [number, number];
  now?: () => number; yieldTask?: () => Promise<void>; env?: CaptureEnv } = {}): Promise<WindowPicture & { longestTaskMs: number; totalMs: number }> {
  const { quality = 0.7, maxSide = 1280, env = browserCapture, yieldTask = nextTask } = options;
  const now = options.now ?? (() => performance.now());
  const view = options.view ?? [innerWidth, innerHeight];
  const started = now();
  let sliceAt = started, longest = 0;
  const pause = async () => { longest = Math.max(longest, now() - sliceAt); await yieldTask(); sliceAt = now(); };
  const wait = async <T>(work: Promise<T>): Promise<T> => { longest = Math.max(longest, now() - sliceAt); const value = await work; sliceAt = now(); return value; };
  // In the asking task: where the windows are, and their pictures as they are now.
  const frames = layoutFrames(root, view);
  const pictures = layoutPictures(root, view, frames);
  const grabs = pictures.map((p) => env.grab(p.el).catch(() => null));
  const background = env.background();
  let painted = false;
  try {
    await pause();
    // The text, a slice at a time, stopping after SCREEN_TEXT_MS of reading in all.
    const text: ScreenText[] = [];
    let read = 0, sliceStart = now();
    for (const item of layoutText(root, view, frames)) {
      if (item) addText(text, item);
      if (now() - sliceStart < SLICE_MS) continue;
      read += now() - sliceStart;
      if (read > SCREEN_TEXT_MS) break;
      await pause();
      sliceStart = now();
    }
    await pause(); // the last of the reading, and the painting, are tasks of their own
    const bitmaps = await wait(Promise.all(grabs));
    const plan: PaintPlan = { view, scale: Math.min(1, maxSide / Math.max(view[0], view[1], 1)), background,
      pictures: pictures.map((p, i) => ({ bitmap: bitmaps[i], rect: p.rect, frame: p.frame, opacity: p.opacity })),
      frames: frames.map(f => ({ rect: f.rect, background: f.background })), text };
    painted = true; // the painter closes the bitmaps from here
    const picture = await wait(env.paint(plan, quality, pause, now));
    longest = Math.max(longest, now() - sliceAt);
    return { ...picture, longestTaskMs: Math.round(longest * 10) / 10, totalMs: Math.round(now() - started) };
  } finally {
    if (!painted) for (const bitmap of await Promise.all(grabs)) bitmap?.close();
  }
}
