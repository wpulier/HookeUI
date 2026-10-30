import { showMatrixResult } from './scientific-matrix-result';
import {makeEditableTable} from './datatable-presentation';
import { attachScientificAsk } from './scientific-ask-input';
import { selectedScientificReader } from './scientific-reader-context';
import { methodChooser, showMethodComparison } from './method-comparison';
import { recipeEntry, showScientificRecipe } from './scientific-recipe';
import { showScientificComparison } from './scientific-comparison';
import { dockControls, type ControlHandle } from './capability-controls';
import { scientificRecoveryWindows, ScientificSessionRecovery, type ScientificRecoveryStorage } from './scientific-session-recovery';
import { scientificResourceActions, type SelectedScientificArray } from './scientific-resource-actions';
import { showPopulationProvenance } from './population-native';
import { savedMovieExport } from './scientific-movie';
import { scientificViewExport } from './scientific-view-export';
import { ScientificInspection, type GeometrySelection } from './scientific-inspection';
import { ScientificInspector } from './scientific-inspector';
import { showScientificFolder, showScientificSource } from './scientific-file-import';
import { scientificTools, scientificToolChoice, selectedScientificTool, savedScientificTool } from './scientific-tools';
import { ScientificCameraGestures } from './scientific-camera-gestures';
import './scientific-document.css';
import { studyArtifacts } from './workspace-study-artifacts';
import type { StudyRetainedView, StudyOpenOptions } from './workspace-study-restoration';
import { ScientificNativePanel } from './scientific-native-panel';
import { nativePanelFrame } from './scientific-native-panel-wire';
import { ScientificCanvas } from './scientific-canvas';
import { ScientificControls, type ControlRequest } from './scientific-controls';
import { ScientificLabels } from './scientific-labels';
import type { LabelsToolChange } from './scientific-labels-tool';
import { ScientificAnnotate, type AnnotateChange } from './scientific-annotate';
import { ScientificNavigation, type ViewerChange } from './scientific-navigation';
import { rememberScientificFrame, type ScientificViewPreferences } from './scientific-view-preferences';
import { StudioSheet } from './sheet';
import { ScientificConsole, PythonNotSent, pythonTranscript } from './scientific-console';
import { showScientificHistory } from './scientific-history';
import { showScientificProvenance } from './scientific-provenance';
import { scientificViewport, sameScientificViewport, type ScientificViewport } from './scientific-viewport';
import { scientificFrame, scientificInventory, type ScientificFrame, type ScientificLayer } from './scientific-view';
import { bindScientificPresentation, declareScientificPresentation } from './scientific-presentation';

import type { ScientificTransport as Transport } from './screen';
type Options = { currentProject?(): string; recovery?: ScientificRecoveryStorage; drafts?:{storage:Storage;prefix:string}; prepare(): Promise<void>; transport(): Transport; location?(project: string, tab: string): string; remember?(project: string, placement: string, resource: StudyRetainedView['resource'], settings: ScientificViewPreferences | null): Promise<void> };
let options: Options | null = null;
const documents = new Map<string, { root: HTMLElement; resource: StudyRetainedView['resource']; reload: () => Promise<boolean> }>();
const stopped = new Set<(review: string) => void>();
const notifyStopped = (review: string) => { for (const listener of stopped) listener(review); };
export function configureScientificDocuments(value: Options): void { options = value; }
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};
const button = (title: string, action: () => void) => {
  const element = node('button', title); element.addEventListener('click', action); return element;
};
const words = (error: unknown) => error instanceof Error ? error.message : String(error);
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
// How often an opening tool's step is read while it opens (GET /api/scientific-reviews: its `opening`).
const OPENING_POLL_MS = 1000;
/** An opening tool's step, its seconds so far and its attempt, in words; null when the reply has none. */
export function openingWords(opening: unknown): string | null {
  if (!object(opening) || typeof opening.says !== 'string' || typeof opening.seconds !== 'number'
      || typeof opening.attempt !== 'number' || typeof opening.attempts !== 'number') return null;
  const now = `${opening.says.slice(0, 120)}… ${Math.floor(opening.seconds)} s`;
  const previous = opening.previous;
  if (opening.attempt < 2 || !object(previous) || typeof previous.says !== 'string' || typeof previous.seconds !== 'number') return now;
  return `${now} (attempt ${opening.attempt} of ${opening.attempts}: the first stopped at “${previous.says.slice(0, 120)}” after ${Math.floor(previous.seconds)} s)`;
}

type OutputPresentation = { coordinates: boolean; choices: [string, string][]; help: string };
const outputPresentation: Record<ScientificLayer['type'], OutputPresentation> = {
  image: { coordinates: false, choices: [['intensity', 'Image intensity'], ['probabilities', 'Probabilities']],
    help: 'Saves this new native array. Its original code is not rerun when you reopen it. Other unsaved layers stay private.' },
  labels: { coordinates: false, choices: [['class-labels', 'Classes (same value = same class)'], ['instance-labels', 'Individual objects (each object has its own value)']],
    help: 'Saves this new native array. Its original code is not rerun when you reopen it. Other unsaved layers stay private.' },
  points: { coordinates: true, choices: [['point-set', 'Point coordinates with typed features']],
    help: 'Saves coordinates, typed features, transforms and point appearance as a new document. Other private layers stay available.' },
  tracks: { coordinates: true, choices: [['track-coordinates', 'Track coordinates, typed features and lineage']],
    help: 'Saves native track IDs, time coordinates, typed features, parent/child edges and appearance together. Begin coordinate axes with t; track IDs are a separate column.' },
  vectors: { coordinates: true, choices: [['vector-set', 'Vector origins and directions with typed features']],
    help: 'Saves each vector’s origin and displacement, typed features and appearance together. Translation moves origins; it does not change directions. Display length does not change scientific vector values.' },
  surface: { coordinates: true, choices: [['surface-mesh', 'Surface mesh: vertices, triangles and measured values']],
    help: 'Saves vertices, triangle indices, measured values, typed features and native appearance together. Each component keeps its own numeric type; incomplete transfers save nothing. Static meshes use the declared coordinate axes.' },
  shapes: { coordinates: true, choices: [['shape-set', 'Shapes with boundaries, geometry types and typed features']],
    help: 'Saves each shape’s vertices, geometry type, typed features and native styling together. Boundaries stay distinct; a line never becomes part of another polygon.' },
};

class ScientificFailure extends Error {
  readonly code: unknown;
  constructor(message: string, code: unknown) { super(message); this.code = code; }
}

async function json(response: Response, panelRecovery = false): Promise<Record<string, unknown>> {
  const value: unknown = await response.json();
  if (!object(value)) throw new Error('The scientific view returned an invalid response. Reopen its saved document.');
  if (!response.ok || value.ok !== true) {
    const reason = String(value.error ?? value.says ?? 'The scientific view could not finish.');
    const panelChange = panelRecovery && ['SCIENTIFIC_CHANGE_NOT_APPLIED', 'SCIENTIFIC_CONTROL_FAILED'].includes(String(value.code));
    throw new ScientificFailure(!panelChange && typeof value.next === 'string' ? `${reason} ${value.next}` : reason, value.code);
  }
  return value;
}
async function pixels(response: Response, expected: number): Promise<Uint8Array> {
  if (!response.ok) { await json(response); throw new Error('Scientific preview unavailable.'); }
  if (!response.body) throw new Error('Scientific preview pixels are missing.');
  const reader = response.body.getReader(), output = new Uint8Array(expected);
  let offset = 0;
  try {
    while (true) {
      const next = await reader.read(); if (next.done) break;
      if (offset + next.value.byteLength > expected) throw new Error('Scientific preview exceeded its declared size.');
      output.set(next.value, offset); offset += next.value.byteLength;
    }
    if (offset !== expected) throw new Error('Scientific preview was incomplete. Refresh it before interacting.');
    return output;
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock(); }
}

type Binding = { review: string; transport: Transport; tab: string; placement: string };
/** A saved scene reopens with at most eight layers (the host's view-input allowance, scientific_outputs.py), and Save tool
 * keeps the tool's whole current layer set. Beyond eight it can't, and says why and what to do instead (F-L8-7: an A/B/C
 * alternative's working input already held an earlier run's five layers, its run added four, and Save tool answered
 * "Choose between two and eight new layers for a saved scene", which named neither the count nor a way on). */
export const MAX_SCENE_LAYERS = 8;
export function toolSaveLimit(layers: readonly {editableInput?: unknown}[]): string {
  if (layers.length <= MAX_SCENE_LAYERS) return '';
  const kept = layers.filter(layer => layer.editableInput).length, added = layers.length - kept;
  return `Save tool keeps every layer of this tool together, and a saved scene holds at most ${MAX_SCENE_LAYERS}: this tool has ${layers.length}`
    + (kept && added ? ` (${kept} came with its input, ${added} are new)` : '') + '. '
    + (added > 1 ? 'Save its new layers with “Save selected new layers together” (the tool’s settings are then not kept with them), or start' : added === 1 ? 'Save its new layer as a new scientific document, or start' : 'Start')
    + ' from a scene with fewer layers, such as the original image. Nothing was saved; your private work is unchanged.';
}

function historyButton(raw: unknown): HTMLButtonElement | null {
  if (!object(raw)) return null;
  if (typeof raw.id !== 'string' || !/^p[0-9a-f]{32}$/.test(raw.id) || typeof raw.day !== 'string' ||
      !Number.isSafeInteger(raw.version) || Number(raw.version) < 1 || raw.picture !== null)
    throw new Error('The saved scientific history reference is invalid. Reopen it from Project Data.');
  const saved: StudyRetainedView = { resource: { id: raw.id, day: raw.day, version: Number(raw.version), picture: null },
    title: 'Saved scientific history', zoom: 1, pan: [.5, .5] };
  return button(`Open saved scientific history · revision ${raw.version}`, () => { void openStudyScientific(saved); });
}
type SaveConflictRow = { layer: string; input: string; state: 'unchanged-in-latest' | 'changed-in-latest' | 'removed-in-latest' };
type ArraySave = { kind?: 'new-output' | 'families'; tool?: boolean; request: string; state: 'staged' | 'saved' | 'copied' | 'conflict' | 'failed'; resource: StudyRetainedView['resource'] | null; layer: string; layers?: string[]; drafts?: string[]; latest?: StudyRetainedView['resource']; copyCaptured?: boolean; copyNeedsPreview?: boolean; conflicts?: SaveConflictRow[]; onto?: { version: number; since: number } };
function arraySave(value: unknown): ArraySave | null {
  if (value === undefined || value === null) return null;
  if (!object(value) || typeof value.request !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/.test(value.request) ||
      !['staged', 'saved', 'copied', 'conflict', 'failed'].includes(String(value.state)) ||
      (value.kind === 'families' ? value.layer !== undefined : typeof value.layer !== 'string'))
    throw new Error('The array save outcome is invalid. Inspect Open analysis windows before retrying.');
  if (value.kind !== undefined && value.kind !== 'new-output' && value.kind !== 'families') throw new Error('Unknown scientific save kind. Check the original session.');
  // Drafts from Python and plugins: the tables, embeddings and files saved together, by name.
  if ((value.kind === 'families') !== (value.drafts !== undefined) || (value.drafts !== undefined && (!Array.isArray(value.drafts) ||
      !value.drafts.length || value.drafts.length > 16 || new Set(value.drafts).size !== value.drafts.length ||
      value.drafts.some(name => typeof name !== 'string' || !/^[a-z][a-z0-9_]{0,63}$/.test(name)) ||
      !['staged', 'saved'].includes(String(value.state)))))
    throw new Error('The saved draft set is invalid. Check array save before retrying.');
  if (value.tool !== undefined && (value.kind !== 'new-output' || typeof value.tool !== 'boolean')) throw new Error('Invalid tool document save receipt. Check the original session.');
  if (value.copyCaptured !== undefined && (value.state !== 'conflict' || typeof value.copyCaptured !== 'boolean'))
    throw new Error('The copy capture status is invalid. Inspect Open analysis windows.');
  if (value.copyNeedsPreview !== undefined && (value.state !== 'conflict' || typeof value.copyNeedsPreview !== 'boolean'))
    throw new Error('The copy requirements are invalid. Inspect Open analysis windows.');
  if (value.layers !== undefined && (!Array.isArray(value.layers) || value.layers.length < 2 || value.layers.length > 64 ||
      value.layers.some(layer => typeof layer !== 'string' || !/^[0-9a-f]{32}$/.test(layer)) ||
      new Set(value.layers).size !== value.layers.length || value.layers[0] !== value.layer))
    throw new Error('The saved layer set is invalid. Check array save before retrying.');
  const ref = value.resource;
  if (value.state === 'saved' || value.state === 'copied') {
    if (!object(ref) || typeof ref.day !== 'string' || typeof ref.id !== 'string' || !/^p[0-9a-f]{32}$/.test(ref.id) ||
        !Number.isSafeInteger(ref.version) || Number(ref.version) < 1 || ref.picture !== null)
      throw new Error('The saved scientific revision is invalid. Open accepted Data to recover it.');
  } else if (ref !== null) throw new Error('An unconfirmed array save cannot name an accepted revision.');
  if (value.latest !== undefined) {
    const latest = value.latest;
    if (value.state !== 'conflict' || !object(latest) || typeof latest.day !== 'string' ||
        typeof latest.id !== 'string' || !/^p[0-9a-f]{32}$/.test(latest.id) ||
        !Number.isSafeInteger(latest.version) || Number(latest.version) < 1 || latest.picture !== null)
      throw new Error('The conflict recovery reference is invalid. Inspect saved Project Data.');
  }
  if (value.conflicts !== undefined && (value.state !== 'conflict' || !Array.isArray(value.conflicts) || !value.conflicts.length ||
      value.conflicts.length > 64 || value.conflicts.some(row => !object(row) || typeof row.layer !== 'string' ||
        typeof row.input !== 'string' || !['unchanged-in-latest', 'changed-in-latest', 'removed-in-latest'].includes(String(row.state)))))
    throw new Error('The per-layer conflict report is invalid. Inspect Open analysis windows.');
  if (value.onto !== undefined && (value.state !== 'saved' || !object(value.onto) || !Number.isSafeInteger(value.onto.version) ||
      !Number.isSafeInteger(value.onto.since) || Number(value.onto.version) <= Number(value.onto.since)))
    throw new Error('The saved revision placement is invalid. Open accepted Data to check it.');
  return structuredClone(value) as ArraySave;
}

export const scientificArraySave = arraySave;  // the save receipt every reply carries, checked
type FamilyDraft = { name: string; family: 'table' | 'embeddings' | 'artifact'; rows?: number; columns?: unknown[]; dims?: number; parts: { bytes: number }[] };
function familyDrafts(value: unknown): FamilyDraft[] {
  if (!Array.isArray(value) || value.length > 16 || value.some(draft => !object(draft) || typeof draft.name !== 'string' ||
      !/^[a-z][a-z0-9_]{0,63}$/.test(draft.name) || !['table', 'embeddings', 'artifact'].includes(String(draft.family)) ||
      !Array.isArray(draft.parts) || draft.parts.some(part => !object(part) || !Number.isSafeInteger(part.bytes) || Number(part.bytes) < 0)))
    throw new Error('The session listed invalid drafts. Check them again.');
  return structuredClone(value) as FamilyDraft[];
}
function draftSummary(draft: FamilyDraft): string {
  const bytes = draft.parts.reduce((total, part) => total + part.bytes, 0);
  const size = bytes < 1024 ? `${bytes} bytes` : bytes < 1 << 20 ? `${(bytes / 1024).toFixed(1)} KiB` : `${(bytes / (1 << 20)).toFixed(1)} MiB`;
  return draft.family === 'table' ? `${draft.name} · table · ${draft.rows} rows, ${draft.columns?.length} columns · ${size}`
    : draft.family === 'embeddings' ? `${draft.name} · embeddings · ${draft.rows} × ${draft.dims} · ${size}`
    : `${draft.name} · file · ${size}`;
}

// No verified name: say so plainly and where the saved record is, never a guessed tool.
const NO_RECORDED_TOOL = 'Tool: not recorded for this saved work. Save › Document, inputs and history shows what was kept.';

class ScientificDocument {
  private disposed = false;
  private recovery: ScientificSessionRecovery | null = null;
  private observationTimer: ReturnType<typeof setTimeout> | null = null;
  private observationAvailable = false;
  private observationPaused = false;
  private observationRun: Promise<unknown> | null = null;
  private observing = false;
  private readonly observationStatus = node('p');
  private readonly resumeObservation = button('Resume live results', () => {
    this.observationPaused = false; this.observationStatus.textContent = '';
    this.resumeObservation.hidden = true; this.scheduleObservation();
  });
  private busy = false;
  private previewRefresh: ReturnType<typeof setTimeout> | null = null;
  private previewRefreshArmed = false;
  private readonly resized: ResizeObserver | null;
  private queuedControlOwner: 'labels' | 'annotation' | null = null;
  private dimensionNavigation: {id:string;sequence:number;binding:Binding;draw:string} | null = null;
  private binding: Binding | null = null;
  private unbind = () => {};
  private readonly undeclare: () => void;
  private readonly drawing: ScientificCanvas;
  private readonly controls: ScientificControls;
  private readonly nativePanel: ScientificNativePanel;
  private readonly console: ScientificConsole;
  private readonly navigation: ScientificNavigation;
  private readonly cameraGestures: ScientificCameraGestures;
  private readonly inspection: ScientificInspection;
  private readonly labels: ScientificLabels;
  private readonly annotate: ScientificAnnotate;
  private readonly status = node('p', 'Saved scientific work. Start its private view to inspect or run code.');
  private readonly start = button('Open session', () => void this.startView());
  private readonly controlStart = button('Open saved view', () => void this.startView());
  private readonly controlWelcome = node('div');
  private readonly memoryRecovery = button('Review open analysis windows', openScientificSessions);
  private memoryRecoveryOwner: 'document' | 'panel' | null = null;
  private readonly withoutPreview = button('Start without preview', () => void this.startView(false, false));
  private readonly refreshLayers = button('Refresh layers', () => void this.run(binding => this.loadLayers(binding)));
  private previewEnabled = true;
  private layers: ScientificLayer[] = [];
  private toolDocumentAvailable = false;
  private outputDrafts = new Map<string, { semantic: string; axes: string }>();
  private outputForms = new Map<string, { details: HTMLDetailsElement; meaning: HTMLSelectElement; axes: HTMLInputElement }>();
  private selectedOutputs = new Set<string>();
  private readonly defaultView = button('Start with default view', () => void this.startView(true));
  private readonly remember = button('Remember this view', () => void this.rememberView(false));
  private readonly forget = button('Forget remembered view', () => void this.rememberView(true));
  private readonly refresh = button('Refresh preview', () => void this.run(async binding => { this.previewEnabled = true; await this.render(binding); }));
  private readonly stopButton = button('Stop scientific session', () => void this.stop());
  private readonly history = node('div');
  private pendingPython: { review: string; cell: string; source: string } | null = null;
  private readonly retryHistory = button('Retry saving scientific history', () => void this.run(async binding => {
    const result = await this.request(binding, { op: 'history-save' }, null);
    const pending = this.pendingPython, recovered = result.pythonRecovery;
    if (pending && pending.review === binding.review && object(recovered) && recovered.id === pending.cell &&
        recovered.source === pending.source && recovered.state === 'not-dispatched') {
      this.console.notDispatched(binding.review, pending.source); this.pendingPython = null;
      // Admission failure invalidates the rendered frame. Restore it before
      // offering another operation; saving history never replays the code.
      await this.refreshSession(binding);
    }
    this.showHistory(result.history); this.status.textContent = result.history
      ? 'Scientific history saved. No code or gesture was run again.' : 'No scientific code or edits have been submitted in this session.';
  }));
  private readonly layerInventory = node('section');
  private readonly resolution = node('p');
  private readonly arraySaves = node('section');
  private readonly familyDrafts = node('section');
  private saveState: ArraySave | null = null;
  private saveRequest: string | null = null;
  private readonly checkSave = button('Check array save', () => void this.run(binding => this.checkArraySave(binding)));
  private readonly retrySave = button('Retry saving these array changes', () => void this.run(async binding => {
    if (!this.saveRequest) return;
    const result = await this.request(binding, { op: 'save-retry', request: this.saveRequest }, null);
    this.confirmArraySave(result.arraySave);
  }));
  private readonly copySave = button('Save as a separate document', () => void this.run(async binding => {
    if (!this.saveRequest || this.saveState?.state !== 'conflict') return;
    const paint = this.drawing.current();
    if (!this.saveState.copyCaptured && this.saveState.copyNeedsPreview !== false && !paint) throw new Error('Check array save and inspect the preview before saving a separate copy.');
    this.status.textContent = this.saveState?.layers ? 'Saving the selected layers together in a separate scientific document…' : 'Saving this layer in a separate scientific document…';
    try {
      const result = await this.request(binding, { op: 'save-copy', request: this.saveRequest, frame: paint?.frame.frame ?? null }, paint?.draw ?? null);
      this.confirmArraySave(result.arraySave);
    } catch (error) {
      if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_SAVE_NOT_STARTED') {
        this.saveRequest = null; this.showArraySave(null);
      }
      throw error;
    }
  }));
  private frame: ScientificFrame | null = null;
  private readonly sessionStopped = (review: string) => {
    if (this.binding?.review !== review) return;
    this.stopObservation(); this.observationAvailable = false; this.recovery?.stopped();
    this.observationStatus.textContent = ''; this.resumeObservation.hidden = true;
    this.binding = null; this.nativePanel.bind(null); this.nativePanel.setAvailable(false); this.unbind(); this.drawing.invalidate(); this.controls.suspend(); this.navigation.bindSession(null); this.labels.bindSession(null); this.annotate.bindSession(null); this.cameraGestures.bindSession(null); this.inspection.bindSession(null); this.console.sessionStopped(review);
    if (!this.disposed) {
      this.status.textContent = 'Scientific session stopped. Saved Project data stays.';
      if (this.saveRequest && this.saveState?.state !== 'saved' && this.saveState?.state !== 'copied') {
        this.saveState = null; this.saveRequest = null; this.frame = null;
        this.arraySaves.replaceChildren(node('p', 'This private session stopped. Check Project Data for completed saves; unsaved changes are no longer available.'));
      }
      this.paintButtons();
    }
  };

  private readonly root: HTMLElement;
  private readonly saved: StudyRetainedView;
  private inspector?: ScientificInspector;
  private controlWindow?: ControlHandle;
  private movieExport?: ReturnType<typeof savedMovieExport>;
  private viewExport?: ReturnType<typeof scientificViewExport>;
  private readonly savedData = node('section');
  private readonly recipeActions = node('section');
  private inspectorSection = 'Inspect';
  private readonly toolContext = node('p');
  private toolKnown = false;
  showSavedTool(name: string | null): void {
    if (name) { this.toolContext.textContent = `Saved tool: ${name}`; this.toolKnown = true; }
  }
  /** While the saved records are checked, say so; then the verified name (showSavedTool) or that none is recorded. */
  checkingSavedTool(check: Promise<void> | undefined): void {
    if (this.toolKnown || !check) return;
    this.toolContext.textContent = 'Tool: checking the saved records…';
    void check.then(() => { if (!this.toolKnown && !this.disposed) this.toolContext.textContent = NO_RECORDED_TOOL; });
  }
  constructor(root: HTMLElement, content: HTMLElement, saved: StudyRetainedView, displayTitle?: string, selectedTool: string | null = null, openControls = true, inputTitle?: string) {
    this.root = root; this.saved = saved;
    content.classList.add('scientific-workspace');
    const askAttachment = content.querySelector<HTMLElement>('[data-scientific-ask-attachment]');
    const documentInfo = node('details');
    documentInfo.className = 'scientific-document-info';
    documentInfo.append(node('summary', 'Document, inputs and history'), ...Array.from(content.children));
    this.status.setAttribute('role', 'status');
    const canvas = node('canvas'); canvas.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;display:block;padding:0;border:0;';
    const viewport=node('div'), polygonPreview=node('canvas'), gestureControls=node('div');
    viewport.className = 'scientific-viewport';
    viewport.style.position='relative';
    polygonPreview.style.cssText='position:absolute;inset:0;width:100%;height:100%;pointer-events:none;background:transparent;';
    polygonPreview.hidden=true; polygonPreview.setAttribute('aria-hidden','true');
    gestureControls.style.cssText='position:absolute;top:8px;right:8px;display:flex;gap:6px;';
    viewport.append(canvas,polygonPreview,gestureControls);
    this.drawing = new ScientificCanvas(canvas, { drawn: () => { this.previewRefreshArmed = true; this.paintButtons(); }, revoked: () => { this.labels?.revoke(); this.annotate?.revoke(); this.cameraGestures?.revoke(); this.inspection?.revoke(); this.paintButtons(); this.previewLost(); } });
    // Expand, Controls or a moved panel resizes the preview; its pixels no longer match, so draw it again (previewLost).
    this.resized = typeof ResizeObserver === 'function' ? new ResizeObserver(() => { if (this.binding && !this.busy) { this.paintButtons(); this.previewLost(); } }) : null;
    this.resized?.observe(viewport);
    this.nativePanel = new ScientificNativePanel(root, {
      changed: () => this.paintButtons(),
      load: async (width, height) => {
        const result = await this.run(async binding => {
          const reply = await this.request(binding, {op: 'native-panel-render', width, height}, null);
          const frame = nativePanelFrame(reply.nativePanel);
          const image = reply.imageFrame;
          const pendingImage = typeof image === 'string' && image === this.frame?.frame && this.drawing.awaiting(image);
          if (image !== this.drawing.current()?.frame.frame && !pendingImage) this.drawing.invalidate();
          const data = await pixels(await binding.transport.request(`/api/scientific-reviews/${binding.review}/native-panels/${frame.frame}`), frame.width * frame.height * 4);
          this.current(binding);
          return {frame, pixels: data};
        }, true, 'panel');
        if (!result) throw new Error('The plugin panel did not return its current display.');
        return result;
      },
      send: async (command, draw) => {
        await this.run(async binding => {
          const result = await this.request(binding, {op: 'native-panel-change', request: command}, draw);
          this.drawing.invalidate();
          if (result.nativeChanged !== true) throw new Error('The plugin action was not acknowledged. Refresh the panel to inspect its current state before trying again.');
          if (this.previewEnabled) {
            try { await this.render(binding); }
            catch (error) {
              this.current(binding);
              if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_MEMORY') this.setMemoryRecovery('document');
              this.status.textContent = `Plugin interaction delivered; the image preview could not refresh. ${words(error)} Refresh the preview to inspect current state; the action is not replayed.`;
            }
          }
        }, true, 'panel');
      },
    });
    const fields = node('section'), python = node('section'), navigation = node('section'), labels = node('section');
    labels.className = 'scientific-label-editor';
    this.labels = new ScientificLabels(labels, this.drawing, {
      gestureControls,
      preview: (points, paint) => {
        polygonPreview.hidden=!points;
        if (!points || !paint) return;
        polygonPreview.width=paint.frame.width; polygonPreview.height=paint.frame.height;
        const context=polygonPreview.getContext('2d');
        if (!context) throw new Error('The polygon preview is unavailable. No edit was applied.');
        context.beginPath();
        points.forEach(([y,x],index) => index?context.lineTo(x,y):context.moveTo(x,y));
        if (points.length>2) { context.closePath(); context.fillStyle='rgba(255,204,64,0.18)'; context.fill(); }
        context.strokeStyle='#ffcc40'; context.lineWidth=2; context.setLineDash([5,3]); context.stroke();
        context.setLineDash([]); context.fillStyle='#ffcc40';
        for (const [y,x] of points) { context.beginPath(); context.arc(x,y,3,0,2*Math.PI); context.fill(); }
      },
      changed: () => this.paintButtons(),
      tool: (change, frame) => this.changeView(change, frame),
      otherOwnsKeys: () => this.annotate?.ownsKeys() ?? false,
      send: (command, draw) => this.run(async binding => {
        const viewport = scientificViewport(this.drawing.element());
        const result = await this.request(binding, command, draw);
        this.drawing.invalidate(); await this.show(binding, result.frame, result.arraySave, viewport); return result.edit;
      }, true),
      check: identity => this.run(async binding => {
        const result = await this.request(binding, { op: 'edit-status', request: identity }, null);
        await this.render(binding); return result.edit;
      }, true),
    });
    const annotations = node('section'); annotations.className = 'scientific-annotate';
    this.annotate = new ScientificAnnotate(annotations, this.drawing, {
      send: (change, frame) => this.changeView(change, frame), changed: () => this.paintButtons(),
      labelsEditing: () => this.labels?.editing() ?? false,
    });
    this.navigation = new ScientificNavigation(navigation, {
      replaceDimensions: async value => {
        const owner = this.dimensionNavigation;
        if (!owner) throw new Error('The slice navigation finished; review the current view before applying another target');
        this.current(owner.binding);
        const result = await json(await owner.binding.transport.request(`/api/scientific-reviews/${owner.binding.review}/request`, {
          method:'POST', headers:{'Content-Type':'application/json'},
          body:JSON.stringify({request:{op:'navigation-latest',navigation:owner.id,sequence:++owner.sequence,value},draw:owner.draw}),
        }));
        this.current(owner.binding);
        if (!object(result.navigation) || result.navigation.id !== owner.id || result.navigation.state !== 'pending')
          throw new Error('The replacement slice was not acknowledged');
      },
      drawn: frame => this.drawing.whenDrawn(frame),
      changed: () => this.paintButtons(),
      send: async (frame, change) => { await this.changeView(change, frame); return this.frame!; },
      refresh: async () => { await this.run(binding => this.render(binding), true); },
    });
    // napari's Apply auto-contrast key: C on the focused preview, with no modifier (the workspace keeps H and F).
    viewport.addEventListener('keydown', event => {
      if (event.key !== 'c' || event.repeat || event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
      event.preventDefault(); this.navigation.autoContrastOnce();
    });
    // napari's dimension shortcuts apply while the preview has keyboard focus (click it, or Tab to it).
    viewport.tabIndex = 0; viewport.setAttribute('aria-label', 'Scientific preview: arrow keys step slices'); this.navigation.keyboard(viewport);
    const cameraGestures = node('section'); cameraGestures.className = 'scientific-camera-controls';
    this.cameraGestures = new ScientificCameraGestures(cameraGestures, this.drawing, {
      send: (frame, change) => this.changeView(change, frame), changed: () => this.paintButtons(),
    });
    const inspection = node('section'); inspection.className='scientific-inspection';
    this.inspection = new ScientificInspection(inspection, this.drawing, {
      select: (frame,{kind,...value}) => this.changeView({operation:kind==='points'?'points-selection':'shapes-selection',value},frame),
      changed: () => this.paintButtons(),
      send: (request,draw) => this.run(async binding => {
        const result=await this.request(binding,request,draw);return result.inspection;
      },true),
    });
    this.undeclare = declareScientificPresentation(root);
    this.controls = new ScientificControls(fields, {
      changed: () => this.paintButtons(),
      send: async request => { await this.change(request); return this.frame!.controls; },
      refresh: async () => { await this.run(async binding => { await this.render(binding); }, true); return this.frame!.controls; },
    });
    this.console = new ScientificConsole(python, {
      ...(options?.drafts ? {draft:{...options.drafts,resource:this.saved.resource,placement:root.dataset.placementId!}} : {}),
      assist: (mode, expression) => this.run(async binding => {
        const result = await this.request(binding, {op:'python-assist', resource:this.saved.resource, mode, expression}, null);
        return result.assistance;
      }, true),
      execute: source => this.run(async binding => {
        const pending = { review: binding.review, cell: crypto.randomUUID(), source };
        this.pendingPython = pending;
        const result = await this.request(binding, { op: 'python-resources', cell: pending.cell,
          resource: this.saved.resource, source }, null);
        if (this.pendingPython === pending) this.pendingPython = null;
        this.showHistory(result.history);
        this.drawing.invalidate();
        try { await this.refreshSession(binding); } catch (error) { if (!this.disposed) this.status.textContent = words(error); }
        return result.console;
      }, true),
      interrupt: async () => {
        const pending = this.pendingPython, binding = this.binding;
        if (!pending || !binding || pending.review !== binding.review) return false;
        const result = await this.request(binding, {op:'python-interrupt', cell:pending.cell}, null);
        if (!object(result.interrupt) || result.interrupt.cell !== pending.cell || typeof result.interrupt.requested !== 'boolean')
          throw new Error('The running cell did not confirm its interrupt request.');
        return result.interrupt.requested;
      },
      notebook: {
        connect: async () => (await this.run(async binding => (await json(await binding.transport.request(
          `/api/scientific-reviews/${binding.review}/jupyter`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' }))).notebook, true)),
        revoke: async () => { await this.run(async binding => { await json(await binding.transport.request(`/api/scientific-reviews/${binding.review}/jupyter`, { method: 'DELETE' })); }, true); },
      },
      stop: () => this.stop(true),
    });
    this.console.sessionStopped('initial'); stopped.add(this.sessionStopped);
    const note = node('p', 'Save scientific changes to keep them. Stop discards unsaved work; accepted Project data stays.');
    const explanation = node('details');
    explanation.append(node('summary', 'What is saved?'), node('p', 'Python code and confirmed outputs are kept in Project Data. Use Save array changes for an editable layer to retain its values as a new revision. Other private changes and Python variables are discarded on Stop or application shutdown; accepted revisions stay. In this browser, Open session can reconnect private work after a page refresh while Hooke is still running.'));
    const toolbar = node('div'); toolbar.className = 'scientific-toolbar';
    const sessionOptions = node('details'), secondary = node('div');
    secondary.className = 'scientific-session-options';
    this.resumeObservation.hidden = true; this.observationStatus.setAttribute('role', 'status');
    secondary.append(this.withoutPreview, this.defaultView, this.refreshLayers, button('Open analysis windows', openScientificSessions));
    sessionOptions.append(node('summary', 'Session'), secondary);
    toolbar.append(this.start, this.refresh, sessionOptions);
    secondary.prepend(this.stopButton);
    const sessionStatus = node('div');
    sessionStatus.className = 'scientific-session-status';
    sessionStatus.tabIndex = 0; sessionStatus.setAttribute('aria-label', 'Scientific session status');
    sessionStatus.append(this.status, this.observationStatus, this.resumeObservation);
    note.className = 'scientific-notice';
    const workbench=node('div'), preview=node('section'), inspector=node('section');
    workbench.className='scientific-workbench'; preview.className='scientific-preview-column'; inspector.className='scientific-inspector';
    preview.setAttribute('aria-label','Scientific preview'); inspector.setAttribute('aria-label','Scientific controls');
    preview.append(viewport,this.resolution);
    documentInfo.append(note, explanation, this.history, this.retryHistory);
    const retained=node('section'); retained.className='scientific-save-controls';
    retained.append(node('h4','Layers and saved work'),this.arraySaves,this.layerInventory,this.checkSave,this.retrySave,this.copySave);
    const exports = node('div');
    if (saved.resource.version !== null) {
      this.viewExport = scientificViewExport(exports, {...saved.resource, version:saved.resource.version}, {
        export: (selection, request) => this.run(async binding => {
          const paint=this.drawing.current();
          if(!paint || !this.navigation.settled() || !this.controls.settled()) throw new Error('Apply or discard unsent settings and refresh the scientific preview before exporting.');
          const result=await json(await binding.transport.request(`/api/scientific-reviews/${binding.review}/exports`, {
            method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({frame:paint.frame.frame,draw:paint.draw,request,...selection}),
          }));
          this.current(binding); return result;
        },true),
        list: () => this.run(async binding => {
          const result=await json(await binding.transport.request(`/api/scientific-reviews/${binding.review}/exports`));
          this.current(binding);return result;
        },true),
        restore: async value => { await this.run(async binding => {
          const paint=this.drawing.current(); if(!paint)throw new Error('Refresh the preview before restoring a captured export view.');
          const viewport=scientificViewport(this.drawing.element());
          const result=await this.request(binding,{op:'change',width:viewport.width,height:viewport.height,request:{frame:paint.frame.frame,operation:'restore-view',value}},paint.draw);
          this.drawing.invalidate();await this.show(binding,result.frame,result.arraySave,viewport);
          await this.drawing.whenDrawn(scientificFrame(result.frame).frame);
        },true); },
      });
      this.movieExport = savedMovieExport(exports, {...saved.resource, version:saved.resource.version});
    }
    retained.append(documentInfo,this.recipeActions);
    const view = node('div'), tool = node('div'), edits = node('div');
    view.append(cameraGestures,navigation,this.remember,this.forget,
      node('p','Remember saves the applied axes, camera and layer display for this window. New sessions restore it; running sessions keep their current view. Array edits and tool values are saved separately.'));
    tool.append(this.nativePanel.element,node('h4','Tool settings'),fields);
    edits.append(node('p','Select an editable Labels layer to correct it, or a Points or Shapes layer to annotate it. Use Save to retain accepted changes.'),labels,annotations);
    this.controlWelcome.className = 'scientific-notice';
    this.controlWelcome.append(node('p','Open this saved view to inspect its data, make corrections or use its tool controls. Analysis does not run automatically.'),this.controlStart);
    const context = node('section'); context.className='scientific-input-context';
    context.setAttribute('aria-label','Selected scientific input and tool');
    this.toolContext.textContent = selectedTool ? `Tool: ${selectedTool}` : NO_RECORDED_TOOL; this.toolKnown = !!selectedTool;
    context.append(node('strong', `Input: ${inputTitle ?? displayTitle ?? saved.title}`),
      node('span', `Saved revision ${saved.resource.version}`),
      this.toolContext);
    if (askAttachment) context.append(askAttachment);
    inspector.append(context,this.controlWelcome);
    this.inspector = new ScientificInspector(inspector, [
      {name:'Inspect',content:inspection}, {name:'Edit',content:edits},
      {name:'View',content:view}, {name:'Tool',content:tool},
      {name:'Save',content:retained}, {name:'Measure',content:this.savedData}, {name:'Export',content:exports}, {name:'Python',content:python},
    ], name => { this.inspectorSection = name; this.paintButtons(); });
    workbench.append(preview,inspector);
    this.controlWindow=dockControls({id:`scientific:${saved.placementId ?? saved.resource.id}`,title:displayTitle??`${saved.title} · ${saved.resource.version === null ? "unsaved input" : `revision ${saved.resource.version}`}`,
      content:inspector,owner:root,scope:()=>[root.dataset.project??'',root.dataset.workspaceView??''],
      activate:()=>{studyArtifacts().applyDocument(`document:${root.dataset.placementId}`,{front:true});},
      canMove:()=>!this.nativePanel.gesturing(),
      geometryChanged:()=>this.nativePanel.layoutChanged()});
    if(this.controlWindow){workbench.classList.add('scientific-workbench-docked');toolbar.append(this.controlWindow.launcher);queueMicrotask(()=>{if(!this.disposed&&openControls)this.controlWindow?.show(false);});}
    // Keep the same live nodes and owners. Inspector scrolling never remounts
    // controls, discards drafts, changes input, or creates a new rendered receipt.
    this.memoryRecovery.hidden = true;
    this.memoryRecovery.className = 'scientific-session-recovery';
    content.append(toolbar,sessionStatus,this.memoryRecovery,workbench);
    const discovery = node('div'); tool.prepend(discovery);
    if (saved.resource.version !== null) scientificTools(discovery, { ...saved.resource, version: saved.resource.version }, async target => { await openStudyScientific({
      title: target.title, resource: { ...saved.resource, id: target.id, version: target.version }, zoom: 1, pan: [.5, .5],
    },{focus:true}); }, { heading: 'Continue with this window’s saved input', inputName: saved.title });
    this.paintButtons();
  }
  mountSavedActions(state:unknown,signal:AbortSignal,selection?:SelectedScientificArray):void {
    if(selection||object(state)&&state.scientificMeasurements)this.inspector?.show('Measure');
    const ref=this.saved.resource;
    recipeEntry(this.recipeActions,state,{...ref,version:Number(ref.version)},async target=>{await openStudyScientific({...this.saved,placementId:undefined,title:target.title,resource:{...ref,id:target.id,version:target.version}});});
    methodChooser(this.savedData,{...ref,version:Number(ref.version)},async target=>{await openStudyScientific({...this.saved,placementId:undefined,title:target.title,resource:{...ref,id:target.id,version:target.version}});},
      {signal,current:()=>!this.disposed&&this.root.dataset.project===ref.day&&!this.root.hidden&&this.root.dataset.workspaceHidden!=='true'});
    scientificResourceActions(this.savedData,{...ref,version:Number(ref.version)},state,
      async revision=>{await openStudyScientific({...this.saved,placementId:undefined,resource:{...ref,version:revision}});},
      {signal,selection,editable:async revision=>{await makeEditableTable({...this.saved,resource:{...ref,version:revision}},this.root);},current:()=>!this.disposed&&this.root.dataset.project===ref.day&&!this.root.hidden&&this.root.dataset.workspaceHidden!=='true'});
  }
  dismiss(): void { this.movieExport?.cancel(); }
  dispose(): void {
    this.disposed = true; this.resized?.disconnect(); if (this.previewRefresh !== null) clearTimeout(this.previewRefresh); this.controlWindow?.dispose(); this.stopObservation(); this.movieExport?.dispose(); this.viewExport?.dispose(); stopped.delete(this.sessionStopped); this.unbind(); this.undeclare();
    this.nativePanel.close(); this.drawing.close(); this.controls.close(); this.navigation.close(); this.labels.close(); this.annotate.close(); this.cameraGestures.close(); this.inspection.close(); this.console.close();
  }
  /** The drawn preview stopped matching its window (a resize, or the app lost focus). Draw it again once, as
   * Refresh preview would: a presentation request only, never analysis. Anything unsent or pending keeps the explicit
   * Refresh preview instead, and a failed redraw is said and not retried. */
  private previewLost(): void {
    if (this.disposed || this.busy || !this.binding || !this.previewEnabled || !this.previewRefreshArmed) return;
    if (this.previewRefresh !== null) clearTimeout(this.previewRefresh);  // still resizing: wait until it settles
    this.previewRefresh = setTimeout(() => { this.previewRefresh = null; void this.redrawLostPreview(); }, 250);
  }
  private async redrawLostPreview(): Promise<void> {
    if (this.disposed || this.busy || !this.binding || !this.previewEnabled || this.drawing.current() !== null) return;
    if (typeof document.hasFocus === 'function' && !document.hasFocus()) { window.addEventListener('focus', () => this.previewLost(), { once: true }); return; }
    if (this.observationWait()) return;
    this.previewRefreshArmed = false;
    const said = this.status.textContent;
    const drawn = await this.run(binding => this.render(binding));
    if (drawn && !this.disposed) this.status.textContent = said;
  }
  private stopObservation(): void {
    if (this.observationTimer !== null) clearTimeout(this.observationTimer);
    this.observationTimer = null;
  }
  private scheduleObservation(): void {
    if (this.disposed || !this.binding || !this.observationAvailable || this.observationPaused || this.observing || this.observationTimer !== null) return;
    this.observationTimer = setTimeout(() => {
      this.observationTimer = null;
      void this.observeBackground();
    }, 1500);
  }
  private editingOutput(): boolean {
    return [...this.outputForms.values()].some(form => document.activeElement === form.axes || document.activeElement === form.meaning);
  }
  private observationWait(): string | null {
    if (this.root.hidden || this.root.dataset.workspaceHidden === 'true' || document.hidden)
      return 'Return to this window to resume live result updates.';
    if (this.saveRequest) return 'Live updates are waiting. Finish or check the current save in Save.';
    if (this.pendingPython) return 'Live updates are waiting. Check the pending code result in Python.';
    if (this.busy || this.nativePanel.interacting()) return 'Live updates will resume after the current action.';
    if (!this.previewEnabled) return 'Open Refresh preview to enable live result updates.';
    if (this.labels.blocked() || this.annotate.blocked()) return 'Live updates are waiting. Finish, cancel or check your edit in Edit.';
    if (this.cameraGestures.blocked()) return 'Live updates are waiting. Finish or cancel the camera gesture.';
    if (this.inspection.blocked()) return 'Live updates are waiting for the current inspection.';
    if (this.navigation.playing()) return 'Stop playback in View to resume live result updates.';
    if (!this.navigation.settled()) return 'Live updates are waiting. Apply or discard your changes in View.';
    if (!this.controls.settled()) return 'Live updates are waiting. Check or discard your changes in Tool.';
    if (this.editingOutput()) return 'Live updates are waiting while you describe an output. Leave the field to resume.';
    return null;
  }
  private async observeBackground(): Promise<void> {
    const binding = this.binding;
    this.observing = true;
    try {
      if (!binding || this.disposed || this.observationPaused) return;
      this.current(binding);
      const waiting = this.observationWait();
      if (waiting) { this.observationStatus.textContent = waiting; return; }
      let changed = false;
      const check = this.run(async current => {
        const result = await this.request(current, {op: 'observe', resource: this.saved.resource}, null);
        if (result.imageFrame !== null && (typeof result.imageFrame !== 'string' || !/^[a-f0-9]{64}$/.test(result.imageFrame)))
          throw new Error('The tool did not confirm its current view.');
        changed = result.imageFrame !== this.frame?.frame || this.drawing.current() === null;
        if (changed) {
          this.drawing.invalidate();
          // A save declaration can receive focus while the callback is in flight.
          // Leave its DOM intact; the next idle observation will deliver the view.
          if (this.editingOutput()) { changed = false; return; }
          changed = await this.render(current, true);
        }
      }, true, 'observation');
      this.observationRun = check; this.paintButtons();
      try { await check; } finally { this.observationRun = null; }
      if (changed && !this.nativePanel.hasDraft()) await this.nativePanel.refreshObserved();
      if (!this.disposed && this.binding === binding) this.observationStatus.textContent = this.nativePanel.hasDraft()
        ? 'Watching for new results. Your unsent tool text is kept; send or clear it, then refresh the panel for updated controls.'
        : 'Watching for new results.';
    } catch (error) {
      if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_REVIEW_BUSY' && !this.disposed && this.binding === binding) {
        // Another request in this session, such as a connected notebook's cell, is running; look again next time.
        this.observationStatus.textContent = 'Live results wait while another request in this session runs, such as a notebook cell.';
      } else if (!this.disposed && this.binding === binding) {
        this.observationPaused = true;
        this.observationStatus.textContent = `Live results paused. ${words(error)} Inspect the current view, then resume. Analysis will not run again.`;
        this.resumeObservation.hidden = false;
      }
    } finally { this.observing = false; this.scheduleObservation(); }
  }
  private current(binding: Binding): void {
    binding.transport.check();
    if (this.disposed || this.binding !== binding || !this.root.isConnected || this.root.dataset.project !== this.saved.resource.day ||
        this.root.dataset.workspaceView !== binding.tab || this.root.dataset.placementId !== binding.placement)
      throw new Error('This scientific window changed. Resume its saved document before continuing.');
  }
  private paintButtons(): void {
    const busy = this.busy && !(this.observing && this.observationRun !== null);
    const playing = this.navigation?.playing() ?? false;
    const cameraBusy = this.cameraGestures?.blocked() ?? false;
    const editing = (this.labels?.blocked() ?? false) || (this.annotate?.blocked() ?? false) || cameraBusy || playing || (this.inspection?.blocked() ?? false);
    this.nativePanel?.setBlocked(busy || !this.binding || !!this.saveRequest || editing,
      busy ? 'Another action is finishing. Try opening the panel after it finishes.'
        : !this.binding ? 'Open the scientific session first.'
          : this.saveRequest ? 'Check the pending save in Save before changing tool controls.'
            : 'Finish or cancel the active edit, camera movement or playback before changing tool controls.');
    this.withoutPreview.disabled = this.busy || editing;
    this.refreshLayers.disabled = this.busy || !this.binding || editing;
    this.defaultView.disabled = this.busy || !!this.binding || editing;
    this.remember.disabled = this.busy || editing || !this.binding || !this.drawing.current() || !!this.saveRequest;
    this.forget.disabled = this.busy || editing;
    this.start.disabled = this.busy || editing; this.refresh.disabled = busy || editing || !this.binding;
    this.controlWelcome.hidden = !!this.binding;
    this.controlStart.disabled = this.busy || editing;
    this.controlStart.textContent = this.busy && !this.binding ? 'Opening saved view…' : 'Open saved view';
    this.stopButton.disabled = !this.binding;
    this.retryHistory.disabled = this.busy || !this.binding;
    const interactive = !this.busy && !!this.binding && this.drawing.current() !== null;
    // Only the admitted editor request keeps its queued controls usable.
    // Each editor still blocks gestures/actions; binding, edit section and other owners remain required.
    this.labels?.setAvailable(this.inspectorSection === 'Edit' && (interactive || (this.queuedControlOwner === 'labels' && !!this.binding)) && !this.saveRequest && !playing && !cameraBusy && !(this.inspection?.blocked() ?? false) && !(this.annotate?.blocked() ?? false));
    this.annotate?.setAvailable(this.inspectorSection === 'Edit' && (interactive || (this.queuedControlOwner === 'annotation' && !!this.binding)) && !this.saveRequest && !playing && !cameraBusy && !(this.inspection?.blocked() ?? false) && !(this.labels?.blocked() ?? false));
    this.cameraGestures?.setAvailable(interactive && !this.saveRequest && !playing && !(this.labels?.blocked() ?? false) && (this.navigation?.settled() ?? true) && (this.controls?.settled() ?? true));
    this.inspection?.setAvailable(this.inspectorSection === 'Inspect' && interactive && !this.saveRequest && !playing && !cameraBusy && !(this.labels?.blocked() ?? false) && (this.navigation?.settled() ?? true) && (this.controls?.settled() ?? true));
    const editable = interactive && !this.saveRequest && !editing;
    // A background check never makes Refresh preview, Python or View unavailable: they wait for it in run(),
    // so a click or keystroke landing during the check is kept, not dropped.
    const viewable = !busy && !!this.binding && this.drawing.current() !== null;
    this.console.setAvailable(!busy && !!this.binding && !this.saveRequest && !editing);
    this.controls.setAvailable(editable, !this.busy && !!this.binding && !this.saveRequest && !editing);
    const navigationClear = !busy && !!this.binding && !this.saveRequest && !(this.labels?.blocked() ?? false) && !cameraBusy;
    this.navigation.setAvailable((viewable && navigationClear) || (!!this.dimensionNavigation && !!this.binding && this.dimensionNavigation.binding === this.binding && !this.saveRequest && !editing),
      navigationClear && this.frame && this.drawing.awaiting(this.frame.frame) ? this.frame.frame : null);
    this.inspector?.attention('View', !this.navigation.settled());
    this.inspector?.attention('Tool', !this.controls.settled() || (this.nativePanel?.hasDraft() ?? false));
    this.inspector?.lock(this.labels?.blocked() ?? false);
    for (const button of this.arraySaves.querySelectorAll<HTMLButtonElement>('button[data-save-layer]')) button.disabled = this.busy || !this.binding || !!this.saveRequest || editing;
    this.checkSave.hidden = !this.saveRequest;
    this.checkSave.disabled = this.busy || !this.binding;
    this.retrySave.hidden = this.saveState?.state !== 'staged';
    this.retrySave.disabled = this.busy || !this.binding;
    this.copySave.hidden = this.saveState?.state !== 'conflict';
    this.copySave.disabled = this.busy || !this.binding || (!this.saveState?.copyCaptured && this.saveState?.copyNeedsPreview !== false && !this.drawing.current());
  }
  private showArraySave(raw: unknown): void {
    const state = arraySave(raw); this.saveState = state; this.saveRequest = state?.request ?? null;
    this.arraySaves.replaceChildren(); this.outputForms.clear();
    this.arraySaves.classList.toggle('scientific-save-receipt', !!state?.resource);
    if (state?.kind === 'families') {
      const resource = state.resource, count = state.drafts?.length ?? 0;
      if (state.state === 'saved' && resource) this.arraySaves.append(
        node('p', `${count} ${count === 1 ? 'draft' : 'drafts'} from Python and plugins saved together as revision ${resource.version}: ${state.drafts?.join(', ')}. Saved data lists them. Layers, view settings and Python variables were not saved; they are still here.`),
        button(`Open saved revision ${resource.version}`, () => { void openStudyScientific({ ...this.saved, resource, placementId: undefined }); }),
        button('Continue this private session', () => {
          this.showArraySave(null); this.paintButtons();
          this.status.textContent = `Revision ${resource.version} is in Project Data. Your other private work is still in this session.`;
        }));
      else this.arraySaves.append(node('p', 'These drafts are staged. Retry saves the same values without running Python again.'));
    } else if (state?.kind === 'new-output' && state.state === 'saved' && state.resource) {
      const resource = state.resource, title = state.layers ? `${state.layers.length} saved scientific layers` : this.layers.find(layer => layer.id === state.layer)?.name ?? 'Saved scientific output';
      this.arraySaves.append(node('p', state.tool ? 'Tool state and every declared scientific layer were saved together. Open the saved document to continue with its controls. Python variables are not restored.' : state.layers ? `${state.layers.length} new layers saved together in one document, with their native values, meanings and transforms. Other private changes are still in this session.` : 'New layer saved with its native values, scientific meaning and transform. Other private changes are still in this session.'),
        button(state.tool ? 'Open saved tool document' : 'Open saved output', () => { void openStudyScientific({ title, resource, zoom: 1, pan: [.5, .5] },{focus:true}); }),
        button('Continue this private session', () => { void this.run(async binding => {
          const result = await this.request(binding, { op: 'save-release', request: state.request }, null);
          if (result.arraySave !== null) throw new Error('The saved output was not released. Check array save before continuing.');
          this.showArraySave(null); await this.loadLayers(binding);
          this.status.textContent = typeof result.cleanupNotice === 'string' ? result.cleanupNotice : 'Saved output retained in Project Data. Private work is still available.';
        }); }));
    } else if (state?.state === 'copied' && state.resource) {
      const resource = state.resource;
      this.arraySaves.append(node('p', state.layers ? `${state.layers.length} editable layers were saved together in a separate document. Other private layers and Python variables were not saved.` : 'This layer was saved in a separate document. Original saved layers and bindings were copied; other private layer changes and Python variables were not saved.'),
        button('Open saved copy', () => { void openStudyScientific({ ...this.saved, title: `${this.saved.title} (saved copy)`, resource, placementId: undefined }); }));
    } else if (state?.state === 'saved' && state.resource) {
      const resource = state.resource;
      const placed = state.onto ? ` It was placed on revision ${state.onto.version}, which another save accepted after revision ${state.onto.since}; that save was kept, and the layers you saved were unchanged in it.` : '';
      this.arraySaves.append(node('p', `Array changes${state.layers ? ` for ${state.layers.length} layers` : ""} saved as revision ${resource.version}.${placed} Other private layers, view settings and Python variables were not saved; they are still here, and you can keep editing and save them.`),
        button(`Open saved revision ${resource.version}`, () => { void openStudyScientific({ ...this.saved, resource, placementId: undefined }); }),
        button('Continue this private session', () => {
          this.showArraySave(null); this.paintButtons();
          this.status.textContent = `Revision ${resource.version} is in Project Data. Your other private changes are still in this session.`;
        }));
    } else if (state) this.arraySaves.append(node('p', state.state === 'staged'
      ? 'These array changes are staged. Retry saves the same values without running the tool or Python again.'
      : state.state === 'failed' ? 'These staged changes cannot be saved. Stop this private session and reopen accepted Data; your saved revisions stay available.'
      : 'Another edit was accepted first. Your edits remain in this private view. Open the newer saved revision from Project Data to inspect it. Stop discards these unsaved edits.'));
    else {
      const editable = this.layers.filter(layer => layer.editableInput);
      if (editable.length > 1) {
        const save = button(`Save all ${editable.length} editable layers`, () => void this.saveLayers(editable.map(layer => layer.id)));
        save.dataset.saveLayer = 'all'; this.arraySaves.append(save);
        this.arraySaves.append(node('p', 'Saves these bound arrays together in one revision. Newly created or unbound layers are not included.'));
      }
      for (const layer of editable) {
        const save = button(`Save array changes to ${layer.name}`, () => void this.saveLayer(layer.id));
        save.dataset.saveLayer = layer.id; this.arraySaves.append(save);
      }
      for(const layer of this.layers)if(layer.elementCount===0)
        this.arraySaves.append(node('p',`${layer.name}: 0 ${layer.type==='tracks'?'track coordinates':layer.type}. ${layer.type==='tracks'?'This pinned viewer cannot reopen empty Tracks. Your accepted Data is unchanged.':'This empty result can be saved with its scientific meaning.'}`));
      const newLayers=this.layers.filter(layer => !layer.editableInput && layer.type in outputPresentation && !(layer.type==='tracks' && layer.elementCount===0));
      for (const id of this.selectedOutputs) if (!newLayers.some(layer=>layer.id===id)) this.selectedOutputs.delete(id);
      const describedLayers = this.toolDocumentAvailable ? this.layers : newLayers;
      for (const layer of describedLayers) {
        const details = node('details'), summary = node('summary', `${layer.editableInput ? 'Tool layer' : 'Save new layer'}: ${layer.name}`);
        details.className = 'scientific-output-save';
        if(newLayers.length>1){
          const choose=node('input'); choose.type='checkbox'; choose.checked=this.selectedOutputs.has(layer.id);
          choose.setAttribute('aria-label',`Include ${layer.name} in saved scene`);
          choose.addEventListener('change',()=>{if(choose.checked)this.selectedOutputs.add(layer.id);else this.selectedOutputs.delete(layer.id);});
          const label=node('label',`Include ${layer.name} in saved scene`); label.append(choose); this.arraySaves.append(label);
        }
        const declaredMeaning = layer.inputMeaning ?? layer.outputMeaning;
        const draft = this.outputDrafts.get(layer.id) ?? {semantic: declaredMeaning?.semantic ?? '', axes: (declaredMeaning?.axes ?? layer.viewGeometry?.axes)?.join(', ') ?? ''}; this.outputDrafts.set(layer.id, draft);
        const meaning = node('select'); meaning.setAttribute('aria-label', `Scientific meaning of ${layer.name}`);
        const presentation = outputPresentation[layer.type];
        const choices = presentation.choices;
        for (const [value, title] of [['', 'Choose what these values mean'], ...choices]) { const option = node('option', title); option.value = value; meaning.append(option); }
        meaning.value = draft.semantic; meaning.disabled = !!declaredMeaning; meaning.addEventListener('change', () => { draft.semantic = meaning.value; meaning.setAttribute('aria-invalid', 'false'); });
        const axes = node('input'); axes.value = draft.axes; axes.readOnly = !!declaredMeaning; axes.placeholder = 'For example: z, y, x';
        axes.setAttribute('aria-label', `Axes of ${layer.name}`); axes.addEventListener('input', () => { draft.axes = axes.value; axes.setAttribute('aria-invalid', 'false'); });
        this.outputForms.set(layer.id, { details, meaning, axes });
        if (layer.outputMeaning) {
          const declaration = layer.outputMeaning;
          details.append(node('p', `Tool-declared meaning: ${declaration.semantic}; axes: ${declaration.axes.join(', ')}. These values are checked again when saving.`));
          if (draft.semantic !== declaration.semantic || draft.axes.split(',').map(axis=>axis.trim()).join(',') !== declaration.axes.join(',')) {
            details.append(node('p', 'Your earlier draft differs from the tool declaration. No data has changed.'));
            details.append(button('Use tool declaration', () => {
              draft.semantic = declaration.semantic; draft.axes = declaration.axes.join(', ');
              meaning.value = draft.semantic; axes.value = draft.axes;
              meaning.setAttribute('aria-invalid', 'false'); axes.setAttribute('aria-invalid', 'false');
            }));
          }
        }
        const axisLabel = node('label', `${presentation.coordinates ? 'Coordinate axes in column order' : 'Axis names in array order'} (${layer.scale.length} dimensions)`); axisLabel.append(axes);
        const save = button('Save as new scientific document', () => { void this.saveOutput(layer, draft); });
        save.dataset.saveLayer = layer.id;
        details.append(summary, node('p', layer.editableInput ? 'This saved input is included with its current values. Its recorded scientific meaning and axes are preserved; conversion needs an explicit new layer.' : presentation.help),
          meaning, axisLabel,
          node('p', 'Layer transforms and units are kept. Alignment to another image is not inferred.'));
        if (!layer.editableInput) details.append(save);
        this.arraySaves.append(details);
      }
      if (this.toolDocumentAvailable) {
        const layers = this.layers.slice(), origin = this.binding;
        const save = button('Save tool and all layers', () => {
          if (!this.root.isConnected || save.parentElement !== this.arraySaves || this.binding !== origin ||
              layers.length !== this.layers.length || layers.some((layer, index) => layer.id !== this.layers[index].id)) {
            this.status.textContent = 'This save control belongs to an earlier layer view. Refresh layers and use its current save control.';
            return;
          }
          void this.saveOutputs(layers, false, true);
        });
        save.dataset.saveLayer = 'tool';
        save.className = 'scientific-save-primary';
        const scope = node('p', 'Keeps the tool’s saved state and every layer together in a new document. Your original stays unchanged. New layers need a scientific meaning below; Python variables are not saved.');
        const limit = toolSaveLimit(layers);
        if (limit) { const said = node('p', limit); said.setAttribute('role', 'note'); said.dataset.saveLimit = 'tool'; this.arraySaves.prepend(said); }
        this.arraySaves.prepend(scope); this.arraySaves.prepend(save);
      }
      if (!this.familyDrafts.childElementCount) this.familyDrafts.append(button('Check drafts from Python and plugins', () => { void this.checkDrafts(); }));
      this.arraySaves.append(this.familyDrafts);
      if(newLayers.length>1){
        const save=button('Save selected new layers together',()=>void this.saveOutputs(newLayers.filter(layer=>this.selectedOutputs.has(layer.id)),true));
        save.dataset.saveLayer='new-set';this.arraySaves.append(save,
          node('p','Choose at least two new layers and declare the meaning and axes of each. All selected outputs are captured together and saved as one document, or none are saved. Existing input changes use Save array changes separately.'));
      }
    }
    if (state?.state === 'conflict') this.arraySaves.append(node('p', state.copyCaptured
      ? 'Save as a separate document keeps the same captured layer values without running the tool again.'
      : state.layers ? `Save as a separate document captures all ${state.layers.length} selected layers together and preserves the original saved document.`
      : 'Save as a separate document captures this private layer now and preserves the original saved document.'));
    if (state?.state === 'conflict' && state.conflicts) {
      const name = (id: string) => this.layers.find(layer => layer.id === id)?.name ?? id;
      const list = node('ul');
      for (const row of state.conflicts) list.append(node('li', `${name(row.layer)}: ${row.state === 'unchanged-in-latest'
        ? 'unchanged in the newer revision' : row.state === 'removed-in-latest' ? 'removed by the newer revision' : 'changed by another save'}`));
      this.arraySaves.append(list);
      const unaffected = state.conflicts.filter(row => row.state === 'unchanged-in-latest').map(row => row.layer);
      if (unaffected.length) {
        const save = button(`Save the ${unaffected.length} unaffected ${unaffected.length === 1 ? 'layer' : 'layers'}`, () => {
          this.showArraySave(null); void this.saveLayers(unaffected);
        });
        this.arraySaves.append(save, node('p', 'Saves only the layers the newer revision did not change. The changed ones stay here as private work; save them as a separate document afterwards.'));
      }
    }
    if (state?.state === 'conflict' && state.latest) {
      const resource = state.latest;
      if (resource.day === this.saved.resource.day && resource.id === this.saved.resource.id &&
          Number(resource.version) > Number(this.saved.resource.version))
        this.arraySaves.append(button(`Open newer saved revision ${resource.version}`, () => {
          void openStudyScientific({ ...this.saved, resource, placementId: undefined });
        }));
    }
    if (state?.resource) {
      const resource = state.resource;
      const title = state.kind === 'new-output'
        ? state.layers ? `${state.layers.length} saved scientific layers` : this.layers.find(layer => layer.id === state.layer)?.name ?? 'Saved scientific output'
        : state.state === 'copied' ? `${this.saved.title} (saved copy)` : this.saved.title;
      // The receipt names an accepted resource. Never substitute the window's
      // original input, private arrays, or a conflicted/staged revision.
      if (resource.version !== null) scientificTools(this.arraySaves, { ...resource, version: resource.version }, async target => {
        await openStudyScientific({ title: target.title, resource: { ...resource, id: target.id, version: target.version }, zoom: 1, pan: [.5, .5] },{focus:true});
      }, { heading: 'Continue with saved output', inputName: title });
    }
    this.paintButtons();
  }
  private confirmArraySave(raw: unknown): void {
    if (!arraySave(raw)) throw new Error('No array save outcome was confirmed. Check array save before retrying.');
    this.showArraySave(raw);
    this.status.textContent = this.saveState?.kind === 'families' && this.saveState.state === 'saved'
      ? 'Drafts saved together in a new revision. Layers and Python variables were not saved; they are still here.'
      : this.saveState?.kind === 'new-output' && this.saveState.state === 'saved'
      ? (this.saveState.tool ? 'Tool state and scientific layers saved together. Your original document is unchanged.' : 'New scientific output saved. Your original document and private session are unchanged.')
      : (this.saveState?.state === 'saved' || this.saveState?.state === 'copied')
      ? 'Array changes saved. The original revision stays available.'
      : 'Array changes are not accepted yet. Inspect the save instructions below.';
  }
  private async saveOutput(layer: ScientificLayer, draft: {semantic: string; axes: string}): Promise<void> {
    this.outputDrafts.set(layer.id,draft);
    return this.saveOutputs([layer]);
  }
  private async saveOutputs(layers: ScientificLayer[], together=false, tool=false): Promise<void> {
    await this.run(async binding => {
      if(tool && toolSaveLimit(layers))throw new Error(toolSaveLimit(layers));
      if(layers.length<(together?2:1) || layers.length>MAX_SCENE_LAYERS)throw new Error(together
        ? 'Choose two to eight new layers to save together, and declare the meaning and axes of each.'
        : 'Choose one new layer to save as a new scientific document.');
      const outputs=layers.map(layer=>{
      const draft=this.outputDrafts.get(layer.id) ?? {semantic:'',axes:''};
      const declaredMeaning = layer.inputMeaning ?? layer.outputMeaning;
      if (declaredMeaning && (draft.semantic !== declaredMeaning.semantic || draft.axes.split(',').map(axis=>axis.trim()).join(',') !== declaredMeaning.axes.join(',')))
        throw new Error(`The declared meaning of ${layer.name} differs from your earlier draft. Use its declared meaning and axes shown in Save; no values were changed.`);
      const axes = draft.axes.split(',').map(axis => axis.trim());
      if (!draft.semantic || axes.length !== layer.scale.length || new Set(axes).size !== axes.length ||
          axes.some(axis => !/^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$/.test(axis))) {
        const form = this.outputForms.get(layer.id);
        if (form) {
          form.details.open = true;
          const field = draft.semantic ? form.axes : form.meaning;
          field.setAttribute('aria-invalid', 'true'); field.focus();
        }
        throw new Error(`Choose the scientific meaning and one distinct axis name per dimension for ${layer.name} before saving.`);
      }
      return {layer:layer.id,meaning:{semantic:draft.semantic,axes}};
      });
      this.saveRequest = crypto.randomUUID(); this.paintButtons();
      this.status.textContent = tool ? 'Capturing tool state and every layer together…' : outputs.length>1 ? `Capturing and saving ${outputs.length} new layers together…` : 'Capturing this new layer and saving its native scientific values…';
      try {
        const command=tool ? {op:'save-tool',outputs} : outputs.length>1 ? {op:'save-outputs',outputs} : {op:'save-output',...outputs[0]};
        const result = await this.request(binding, {...command, request:this.saveRequest, resource:this.saved.resource}, null);
        this.drawing.invalidate(); this.confirmArraySave(result.arraySave);
      } catch (error) {
        if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_SAVE_NOT_STARTED') {
          this.saveRequest = null; this.showArraySave(null);
        }
        throw error;
      }
    });
  }
  private async checkDrafts(): Promise<void> {
    await this.run(async binding => {
      const result = await this.request(binding, { op: 'family-drafts', resource: this.saved.resource }, null);
      const listed = familyDrafts(result.drafts);
      this.familyDrafts.replaceChildren(node('h4', 'Drafts from Python and plugins'), ...(listed.length
        ? listed.map(draft => node('p', draftSummary(draft)))
        : [node('p', 'No drafts yet. In Python, families.table(...), families.features(name, layer), families.embedding(...) and families.artifact(...) declare them.')]));
      if (listed.length) {
        const save = button(`Save ${listed.length === 1 ? 'this draft' : `all ${listed.length} drafts`} together`, () => { void this.saveDrafts(listed.map(draft => draft.name)); });
        save.dataset.saveLayer = 'drafts';
        this.familyDrafts.append(save, node('p', 'Adds them to this document in one new revision, or saves none. Each value, key and relation is checked again.'));
      }
      this.familyDrafts.prepend(button('Check drafts again', () => { void this.checkDrafts(); }));
      this.status.textContent = listed.length ? `${listed.length} unsaved ${listed.length === 1 ? 'draft' : 'drafts'} in this session.` : 'No unsaved drafts in this session.';
    });
  }
  private async saveDrafts(names: string[]): Promise<void> {
    await this.run(async binding => {
      this.saveRequest = crypto.randomUUID(); this.paintButtons();
      this.status.textContent = `Checking and saving ${names.length === 1 ? 'one draft' : `${names.length} drafts`} together…`;
      try {
        const result = await this.request(binding, { op: 'save-families', request: this.saveRequest, resource: this.saved.resource, names }, null);
        this.confirmArraySave(result.arraySave);
        this.familyDrafts.replaceChildren(button('Check drafts from Python and plugins', () => { void this.checkDrafts(); }));
      } catch (error) {
        if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_SAVE_NOT_STARTED') {
          this.saveRequest = null; this.showArraySave(null);
        }
        throw error;
      }
    });
  }
  private async saveLayer(layer: string): Promise<void> { return this.saveLayers([layer]); }
  private async saveLayers(ids: string[]): Promise<void> {
    await this.run(async binding => {
      const layers = ids.map(layer => {
        const input = this.layers.find(item => item.id === layer)?.editableInput;
        if (!input) throw new Error('This layer has no declared editable input. Refresh its available layers.');
        return { layer, input };
      });
      this.saveRequest = crypto.randomUUID(); this.paintButtons();
      this.status.textContent = layers.length > 1 ? 'Saving all selected layers together as one scientific revision…' : 'Saving this layer as a new scientific revision…';
      try {
        const command = layers.length > 1
          ? { op: 'save-resources', request: this.saveRequest, resource: this.saved.resource, layers }
          : { op: 'save-resource', request: this.saveRequest, resource: this.saved.resource, ...layers[0] };
        const result = await this.request(binding, command, null);
        this.drawing.invalidate(); this.confirmArraySave(result.arraySave);
      } catch (error) {
        if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_SAVE_NOT_STARTED') {
          this.saveRequest = null; this.showArraySave(null);
        }
        if (error instanceof ScientificFailure && error.code === 'SCIENTIFIC_SAVE_CONFLICT') {
          const pending = this.saveRequest;
          try { await this.checkArraySave(binding); } catch { this.saveRequest = pending; }
          if (this.saveState?.state === 'conflict') return;
        }
        throw error;
      }
    });
  }
  private showHistory(raw: unknown): void {
    const link = historyButton(raw);
    if (link) this.history.replaceChildren(link);
  }
  private async run<T>(action: (binding: Binding) => Promise<T>, propagate = false, failureOwner: 'document' | 'panel' | 'observation' = 'document', queuedControlOwner: 'labels' | 'annotation' | null = null): Promise<T | undefined> {
    // A user's operation waits for an in-flight background check for results instead of being refused.
    while (failureOwner !== 'observation' && this.busy && this.observing && this.observationRun) await this.observationRun.catch(() => undefined);
    const presentFailure = failureOwner === 'document';
    if (this.busy || !this.binding) {
      const error = new PythonNotSent(this.busy ? 'Wait for the current scientific operation to finish.' : 'Start or resume the scientific view first.');
      if (presentFailure) this.status.textContent = error.message; if (propagate) throw error; return;
    }
    const binding = this.binding; this.busy = true; this.queuedControlOwner = queuedControlOwner;
    if (presentFailure) this.setMemoryRecovery(null);
    this.paintButtons();
    try {
      this.current(binding);
      const result = await action(binding);
      if (failureOwner === 'panel' && this.memoryRecoveryOwner === 'panel') this.setMemoryRecovery(null);
      return result;
    }
    catch (error) {
      // Resource recovery belongs to the session even when a panel owns the
      // immediate error. A panel refresh cannot resolve unrelated image errors.
      if (!this.disposed) {
        if (presentFailure) this.showFailure(error);
        else if (failureOwner === 'panel' && error instanceof ScientificFailure && error.code === 'SCIENTIFIC_MEMORY' && this.memoryRecoveryOwner !== 'document') this.setMemoryRecovery('panel');
      }
      if (propagate) throw error;
    }
    finally { this.queuedControlOwner = null; this.busy = false; if (!this.disposed) this.paintButtons(); }
  }
  private showFailure(error: unknown): void {
    this.status.textContent = words(error);
    this.setMemoryRecovery(error instanceof ScientificFailure && error.code === 'SCIENTIFIC_MEMORY' ? 'document' : null);
  }
  private setMemoryRecovery(owner: 'document' | 'panel' | null): void {
    this.memoryRecoveryOwner = owner; this.memoryRecovery.hidden = owner === null;
  }
  private async request(binding: Binding, request: unknown, draw: string | null): Promise<Record<string, unknown>> {
    this.current(binding);
    try {
      const route = object(request) && ['python','python-resources'].includes(String(request.op)) &&
        typeof request.source === 'string' && new TextEncoder().encode(request.source).length > (16 << 10) ? 'script' : 'request';
      const panelRecovery = object(request) && ['observe', 'native-panel-render', 'native-panel-change'].includes(String(request.op));
      const result = await json(await binding.transport.request(`/api/scientific-reviews/${binding.review}/${route}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request, draw }),
      }), panelRecovery);
      this.current(binding); this.showHistory(result.history);
      if (object(request) && !['observe', 'native-panel-render', 'native-panel-change', 'history-save', 'save-status', 'python-assist', 'inspect', 'layer-inventory'].includes(String(request.op))) this.nativePanel.invalidate();
      return result;
    } catch (error) { this.drawing.invalidate(); this.nativePanel.invalidate(); throw error; }
  }
  private async checkArraySave(binding: Binding): Promise<void> {
    const result = await this.request(binding, { op: 'save-status' }, null);
    if (result.arraySave === null) {
      this.saveRequest = null; this.showArraySave(null);
      this.status.textContent = 'No array changes were staged. Private edits remain available; correct the requirement and save again.';
      return;
    }
    this.confirmArraySave(result.arraySave);
  }
  private async refreshSession(binding: Binding): Promise<void> {
    if (this.previewEnabled) await this.render(binding);
    else await this.loadLayers(binding);
  }
  private async loadLayers(binding: Binding): Promise<void> {
    const result = await this.request(binding, { op: 'layer-inventory', resource: this.saved.resource }, null);
    const layers = scientificInventory(result.inventory);
    if (result.nativePanelAvailable !== undefined && typeof result.nativePanelAvailable !== 'boolean') throw new Error('The integration returned an invalid plugin panel declaration.');
    this.observationAvailable = result.nativePanelAvailable === true; this.scheduleObservation();
    this.nativePanel.setAvailable(this.observationAvailable);
    if (result.toolDocumentAvailable !== undefined && typeof result.toolDocumentAvailable !== 'boolean') throw new Error('Invalid tool document declaration.');
    this.toolDocumentAvailable = result.toolDocumentAvailable === true;
    if (result.arraySave !== null && !arraySave(result.arraySave)) throw new Error('Layer inventory did not confirm the current save. Check array save before retrying.');
    this.layers = layers;
    this.layerInventory.replaceChildren(node('h4', 'Current private layers'), ...layers.map(layer => node('p', `${layer.name} · ${layer.type}`)));
    this.showArraySave(result.arraySave);
    this.status.textContent = 'Current layers loaded without rendering a preview. Refresh preview to inspect, paint or pick.';
  }
  private async render(binding: Binding, observation = false): Promise<boolean> {
    const viewport = scientificViewport(this.drawing.element());
    const result = await this.request(binding, { op: 'render', width: viewport.width, height: viewport.height }, null);
    const committed = await this.show(binding, result.frame, result.arraySave, viewport, observation);
    if (committed && typeof result.viewNotice === 'string' && result.viewNotice.length <= 2300) this.status.textContent = result.viewNotice;
    return committed;
  }
  private async show(binding: Binding, raw: unknown, savedArrays?: unknown, viewport?: ScientificViewport, observation = false): Promise<boolean> {
    const frame = scientificFrame(raw);
    const bytes = await pixels(await binding.transport.request(`/api/scientific-reviews/${binding.review}/frames/${frame.frame}`), frame.width * frame.height * 4);
    this.current(binding);
    if (observation && this.editingOutput()) return false;
    if (viewport && !sameScientificViewport(viewport, scientificViewport(this.drawing.element()))) {
      this.drawing.invalidate();
      throw new Error('The scientific window changed size while loading. Refresh preview to use its current size; private work is preserved.');
    }
    if (this.root.hidden || this.root.dataset.workspaceHidden === 'true') throw new Error('The view is hidden. Resume it to inspect the completed work.');
    this.layerInventory.replaceChildren();
    this.resolution.textContent = `Preview: ${frame.width} × ${frame.height} pixels. Scientific data keeps its original resolution.`;
    this.observationAvailable = frame.nativePanelAvailable === true; this.scheduleObservation();
    this.nativePanel.setAvailable(this.observationAvailable);
    this.toolDocumentAvailable = frame.toolDocumentAvailable === true;
    this.frame = frame; this.layers = frame.layers; this.controls.update(frame.controls); this.navigation.update(frame); this.labels.update(frame); this.annotate.update(frame); this.cameraGestures.update(frame); this.inspection.update(frame); this.drawing.show(frame, bytes); this.showArraySave(savedArrays);
    this.root.dataset.displayState = 'private-preview';
    const outcome = this.saveState?.state;
    this.status.textContent = outcome === 'copied' ? 'Array changes saved in a separate document. Open saved copy to continue.'
      : outcome === 'saved' ? 'Array changes saved. Open the accepted revision to continue.'
      : outcome === 'conflict' ? 'A newer revision was accepted. These private changes were not accepted.'
      : outcome === 'failed' ? 'Array save failed. Reopen accepted Data to continue.'
      : outcome === 'staged' ? 'Array changes are staged but not yet accepted. Retry saving the same values.'
      : frame.dirty ? 'Changes in this session, not saved yet. Save keeps them as a new version; until then they end with the session.' : 'Private view open. Nothing changed yet.';
    return true;
  }
  private async change(value: ControlRequest): Promise<void> {
    await this.changeView({ operation: 'controls', value });
  }
  private async changeView(change: ViewerChange | LabelsToolChange | AnnotateChange | { operation: 'controls'; value: ControlRequest } | {operation:'points-selection'|'shapes-selection';value:Omit<GeometrySelection,'kind'>}, expectedFrame?: string): Promise<void> {
    const queuedControlOwner = change.operation === 'labels-tool' && 'settings' in change.value ? 'labels'
      : change.operation === 'annotate' ? 'annotation' : null;
    await this.run(async binding => {
      const paint = this.drawing.current(); if (!paint) throw new Error('Refresh the preview before changing its controls.');
      if (expectedFrame && expectedFrame !== paint.frame.frame) throw new Error('The view changed. Check the current view and review your unsent changes.');
      const viewport = scientificViewport(this.drawing.element());
      const dimensions = change.operation === 'dimensions' && change.value.ndisplay === paint.frame.dimensions.ndisplay &&
        JSON.stringify(change.value.order) === JSON.stringify(paint.frame.dimensions.order);
      const navigation = dimensions ? {id:crypto.randomUUID(),sequence:1,binding,draw:paint.draw} : null;
      if (navigation) { this.dimensionNavigation = navigation; this.paintButtons(); }
      let result: Record<string, unknown>;
      try {
        result = await this.request(binding, { op: 'change', width: viewport.width, height: viewport.height,
          ...(navigation ? {navigation:navigation.id} : {}), request: { frame: paint.frame.frame, ...change } }, paint.draw);
      } finally { if (this.dimensionNavigation === navigation) this.dimensionNavigation = null; }
      this.drawing.invalidate(); await this.show(binding, result.frame, result.arraySave, viewport);
      if (queuedControlOwner) {
        // The next queued editor choice needs this exact frame's interaction receipt.
        // Releasing ownership before canvas paint can otherwise discard the queue as unavailable.
        await this.drawing.whenDrawn(scientificFrame(result.frame).frame);
        this.current(binding);
      }
    }, true, 'document', queuedControlOwner);
  }
  private async rememberView(forget: boolean): Promise<void> {
    if (this.busy || this.disposed) return;
    try {
      if (!options?.remember) throw new Error('Wait for the workspace to finish opening.');
      if (!forget && (!this.navigation.settled() || !this.controls.settled())) throw new Error('Apply or discard unsent controls before remembering the current view.');
      const paint = this.drawing.current();
      if (!forget && (!this.binding || !paint || this.saveRequest)) throw new Error('Inspect a current preview before remembering its view.');
      const settings = forget ? null : rememberScientificFrame(paint!.frame);
      const project = this.saved.resource.day, placement = this.root.dataset.placementId;
      if (!placement || this.root.dataset.project !== project) throw new Error('Return to the original scientific window.');
      this.busy = true; this.paintButtons();
      await options.remember(project, placement, this.saved.resource, settings);
      if (!this.disposed) this.status.textContent = forget ? 'Remembered view removed. The next new session starts with the integration’s default view.' : 'View remembered for this window. The next new session restores it.';
    } catch (error) { if (!this.disposed) this.status.textContent = words(error); }
    finally { this.busy = false; if (!this.disposed) this.paintButtons(); }
  }
  /** While a new session opens, its step and seconds so far (and a retried attempt) in the status line; returns its stop. */
  private watchOpening(transport: Transport, placement: string): () => void {
    let reading = false, stopped = false;
    const read = async () => {
      if (reading || stopped || this.disposed) return;
      reading = true;
      try {
        const inventory = await json(await transport.request('/api/scientific-reviews'));
        const row = Array.isArray(inventory.reviews) ? inventory.reviews.find(value => object(value) && value.placement === placement && object(value.document)
          && value.document.day === this.saved.resource.day && value.document.id === this.saved.resource.id && value.document.version === this.saved.resource.version) : undefined;
        const said = object(row) && row.state === 'opening' ? openingWords(row.opening) : null;
        if (said && !stopped && !this.disposed) this.status.textContent = said;
      } catch { /* the open's own reply says how it ended; a missed reading only leaves the last words */ }
      finally { reading = false; }
    };
    const timer = setInterval(() => { void read(); }, OPENING_POLL_MS);
    return () => { stopped = true; clearInterval(timer); };
  }
  private async startView(defaultView = false, preview = true): Promise<void> {
    if (this.busy || this.disposed) return;
    this.busy = true; this.setMemoryRecovery(null); this.paintButtons(); this.status.textContent = 'Opening the saved scientific view…';
    try {
      if (!options) throw new Error('Wait for the workspace to finish opening.');
      await options.prepare();
      if (this.disposed || this.root.dataset.project !== this.saved.resource.day) throw new Error('Return to the originating Project first.');
      const transport = options.transport(), tab = this.root.dataset.workspaceView, placement = this.root.dataset.placementId;
      if (!tab || !placement) throw new Error('Save this window in its work Tab before starting its tools.');
      const inventory = await json(await transport.request('/api/scientific-reviews'));
      const existing = Array.isArray(inventory.reviews) ? inventory.reviews.find(value => object(value) && value.placement === placement && object(value.document) &&
        value.document.day === this.saved.resource.day && value.document.id === this.saved.resource.id && value.document.version === this.saved.resource.version) : undefined;
      if (defaultView && existing) throw new Error('Stop the existing scientific session before starting a new default view.');
      const path = object(existing) && typeof existing.review === 'string' ? `/api/scientific-reviews/${existing.review}/attach` : '/api/scientific-reviews';
      this.recovery ??= options.recovery ? new ScientificSessionRecovery(options.recovery, [this.saved.resource.day, this.saved.resource.id, this.saved.resource.version, placement]) : null;
      const recoveryKey = path === '/api/scientific-reviews' ? this.recovery?.key() : undefined;
      const opening = path === '/api/scientific-reviews' ? this.watchOpening(transport, placement) : () => {};
      let result: Record<string, unknown>;
      try {
        result = await json(await transport.request(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({
          session: this.saved.resource.day, document: this.saved.resource.id, revision: this.saved.resource.version, item: `document:${placement}`, ...(defaultView ? { defaultView: true } : {}), ...(recoveryKey ? { recoveryKey } : {}),
        }) }));
      } finally { opening(); }
      if (typeof result.review !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/.test(result.review)) throw new Error('Scientific session identity was not returned. Resume to recover it.');
      const binding = { review: result.review, transport, tab, placement }; this.stopObservation(); this.observationPaused = false; this.observationStatus.textContent = ''; this.resumeObservation.hidden = true; this.binding = binding; this.current(binding);
      this.nativePanel.bind(binding.review);
      this.console.bindSession(binding.review); this.controls.bindSession(binding.review); this.navigation.bindSession(binding.review); this.labels.bindSession(binding.review); this.annotate.bindSession(binding.review); this.cameraGestures.bindSession(binding.review); this.inspection.bindSession(binding.review);
      this.unbind(); this.unbind = bindScientificPresentation(this.root, { review: binding.review, drawing: this.drawing });
      this.previewEnabled = preview;
      if (!preview) {
        this.drawing.clear();
        this.resolution.textContent = 'No current preview. Refresh preview to inspect the current private layers.';
      }
      await this.refreshSession(binding);
      if (typeof result.sessionNotice === 'string') {
        this.current(binding); this.status.textContent = result.sessionNotice.slice(0, 4096);
      }
    } catch (error) { if (!this.disposed) this.showFailure(error); }
    finally { this.busy = false; if (!this.disposed) this.paintButtons(); }
  }
  private async stop(propagate = false): Promise<void> {
    const binding = this.binding; if (!binding) return;
    try {
      await json(await binding.transport.request(`/api/scientific-reviews/${binding.review}`, { method: 'DELETE' }));
      notifyStopped(binding.review);
    } catch (error) { if (!this.disposed) this.status.textContent = words(error); if (propagate) throw error; }
  }
}

type ChosenContinuation={selection:SelectedScientificArray;signal:AbortSignal;current:()=>boolean};
export async function openStudyScientific(requested: StudyRetainedView, presentation: StudyOpenOptions = {}, continuation?:ChosenContinuation): Promise<boolean> {
  if(continuation&&(continuation.signal.aborted||!continuation.current()))return false;
  const ref = requested.resource;
  const existing = requested.placementId || continuation ? undefined : [...documents].find(([, item]) =>
    item.root.dataset.workspaceView === (document.body.dataset.activeWorkspaceView ?? 'main') &&
    item.resource.day === ref.day && item.resource.id === ref.id && item.resource.version === ref.version);
  const saved = { ...requested, placementId: requested.placementId ?? existing?.[0] ?? crypto.randomUUID() };
  const content = node('section');
  content.className = 'scientific-document';
  let controller: ScientificDocument | null = null;
  const abort = new AbortController();
  const cancelContinuation=()=>abort.abort();
  continuation?.signal.addEventListener('abort',cancelContinuation,{once:true});
  const root = studyArtifacts().document(`scientific:${ref.day}:${ref.id}:${ref.version}:${saved.placementId ?? ''}`, saved.title,
    content, () => { abort.abort(); continuation?.signal.removeEventListener('abort',cancelContinuation); controller?.dispose(); if (documents.get(saved.placementId)?.root === root) documents.delete(saved.placementId); }, { studyRetained: JSON.stringify(saved), ...(saved.placementId ? { placementId: saved.placementId } : {}) }, presentation, () => controller?.dismiss());
  if (!root.contains(content)) {
    const current = documents.get(saved.placementId);
    if (root.dataset.displayState === 'failed' && current?.root === root) return current.reload();
    return root.dataset.displayState === 'rendered';
  }
  studyArtifacts().restoreGeometry(root, { ...saved, size: saved.size ?? [640, 500] });
  // Only the explicit next-step caller opts in. Never resize a retained placement,
  // restored arrangement or a window that was already open (including its drafts).
  if(presentation.focus&&!presentation.background&&!requested.placementId&&!existing&&!requested.position&&!requested.size)
    studyArtifacts().focusDocument(root);
  const load = async () => {
    content.textContent = 'Loading saved scientific work…'; root.dataset.displayState = 'loading';
    try {
      if (!Number.isSafeInteger(ref.version) || Number(ref.version) < 1) throw new Error('Choose an exact saved scientific revision.');
      const result = await json(await fetch(`/api/scientific-projects/${encodeURIComponent(ref.id)}?${new URLSearchParams({ session: ref.day, revision: String(ref.version) })}`, { signal: abort.signal }));
      if (abort.signal.aborted) return false;
      const project = result.project;
      if (!object(project) || project.id !== ref.id || project.session !== ref.day || project.revision !== ref.version || !object(project.document)) throw new Error('This is not the requested saved scientific revision.');
      if(continuation){
        if(!continuation.current()||continuation.signal.aborted)throw new Error('Return to the original comparison’s Project and Tab before continuing.');
        if(!/^[a-f0-9]{64}$/.test(continuation.selection.digest)||project.digest!==continuation.selection.digest)
          throw new Error('The chosen result does not match its accepted revision. Reopen the retained comparison.');
      }
      const document = project.document;
      const title=typeof document.title==='string'&&document.title.trim()&&document.title.length<=240?document.title:saved.title;
      const role=document.kind==='population-labels'?'Saved image and labels':document.kind==='scene'?'Scientific view':'Saved scientific work';
      // Only a structured producer's exact generated title gets a shorter display.
      // Arbitrary user titles (including revision-like words) are never stripped.
      const population=object(document.state)&&object(document.state.populationSource)?document.state.populationSource:null;
      const origin=population&&object(population.source)?population.source:null;
      const generated=population&&(population.format===1||population.format===2)&&population.operation==='retain-population-labels-v1'
        &&origin&&typeof origin.id==='string'&&Number.isSafeInteger(origin.revision)
        ? `Retained labels · ${origin.id} · revision ${origin.revision}` : null;
      const readerContext=selectedScientificReader(document,ref.day);
      // A chosen tool is in the window's name: each choice opens its own window on the same input, so the windows differ.
      const chosenTool=document.kind==='scene'?selectedScientificTool(document.state,{...ref,version:Number(ref.version)}):null;
      const shortTitle=title===generated?'Image and labels':title;
      const displayTitle=document.kind==='population-labels'?shortTitle:chosenTool?`${shortTitle} · ${chosenTool.name}`:title;
      studyArtifacts().documentTitle(root,title,displayTitle);
      const identity=node('details');identity.append(node('summary','Document identity'),node('p',`${title} · ${role} · saved revision ${ref.version}`),node('p',`Project ${ref.day} · ${ref.id}`));
      content.replaceChildren(identity);
      const arrays = Array.isArray(document.resources) ? document.resources.filter((item: unknown): item is Record<string, unknown> => object(item) && typeof item.component === 'string' && item.component.startsWith('array.')) : [];
      if (arrays.length && typeof project.digest === 'string') {
        const attachment = node('div'), select = node('select'); attachment.dataset.scientificAskAttachment = 'true'; select.setAttribute('aria-label', 'Saved component for typed Ask');
        for (const item of arrays) { const option = node('option', String(item.component)); option.value = String(item.component); select.append(option); }
        const status = node('p', 'Typed Ask accepts saved uint8 intensity images with Y/X axes, up to one megapixel. Other data requires explicit conversion.');
        attachment.append(select, button('Use with Assistant', () => {
          try {
            if (abort.signal.aborted || root.hidden || root.dataset.workspaceHidden === 'true') throw new Error('Reopen this saved input in the current Tab.');
            attachScientificAsk({reference:{kind:'array',session:ref.day,id:ref.id,revision:Number(ref.version),component:select.value},digest:project.digest as string,tab:window.document.body.dataset.activeWorkspaceView ?? ''}, title);
            status.textContent = 'Attached to typed Ask. Focusing the question keeps this saved revision.';
          } catch(error) { status.textContent = words(error); }
        }), status);
        identity.after(attachment);
      }
      if (!(object(document.state)&&document.state.scientificView&&document.kind!=='population-labels')) content.prepend(node('p',`${role} · Saved revision ${ref.version}`));
      scientificToolChoice(content, document.state, { ...ref, version: Number(ref.version) }, async revision => {
        await openStudyScientific({ ...saved, placementId: undefined, resource: { ...ref, version: revision } }); });
      const madeChecked = showScientificProvenance(content, document.state, (resource, title) => {
        void openStudyScientific({ resource, title, zoom: 1, pan: [.5, .5] });
      }, { ...ref, version: Number(ref.version) }, fetch, value => {
        if (!abort.signal.aborted) controller?.showSavedTool(savedScientificTool(document,value));
      });
      if (document.kind === 'source-file') showScientificSource(content,document.state,{...ref,version:Number(ref.version)},async target=>{await openStudyScientific({...saved,title:target.title,placementId:undefined,resource:{...ref,id:target.id,version:target.version}});},document.resources,object(document.state) && object(document.state.workflowOutput));
      else if (document.kind === 'matrix-result') showMatrixResult(content,document.state,{...ref,version:Number(ref.version)},document.resources,async target=>{await openStudyScientific({...saved,title:target.title,placementId:undefined,resource:{...ref,id:target.id,version:target.version}});});
      else if (document.kind === 'source-directory') showScientificFolder(content,document.state,{...ref,version:Number(ref.version)},async target=>{await openStudyScientific({...saved,title:target.title,placementId:undefined,resource:{...ref,id:target.id,version:target.version}});});
      else if (document.kind === 'scientific-workflow-run') { const link=node('a','Open workflow run and its retained results');link.href=`#workflows?run=${encodeURIComponent(ref.id)}`;content.append(link); }
      else if (document.kind === 'scientific-recipe' || document.kind === 'scientific-recipe-run') await showScientificRecipe(content, document.state, {...ref,version:Number(ref.version)}, async target => { await openStudyScientific({...saved,placementId:undefined,title:target.title,resource:{...ref,id:target.id,version:target.version}}); });
      else if (document.kind === 'scientific-comparison') showScientificComparison(content, document.state, {...ref,version:Number(ref.version)}, async target => { await openStudyScientific({...saved,placementId:undefined,title:target.title,resource:{...ref,id:target.id,version:target.version}}); });
      else if (document.kind === 'scientific-method-comparison') {
        const owner={signal:abort.signal,current:()=>!abort.signal.aborted&&root.dataset.project===ref.day&&!root.hidden&&root.dataset.workspaceHidden!=='true'};
        await showMethodComparison(content,{...ref,version:Number(ref.version)},async target=>{
          const resource={...ref,id:target.id,version:target.version};
          const requested=target.selection?{title:target.title,resource,zoom:1,pan:[.5,.5]}:
            {...saved,placementId:undefined,title:target.title,resource};
          const opened=await openStudyScientific(requested,
            target.selection?{focus:true}:{},target.selection?{selection:target.selection,...owner}:undefined);
          if(!opened)throw new Error('The chosen document did not open. Keep the comparison and retry from its original Project.');
        },owner);
      }
      else if (document.kind === 'scientific-python-history') showScientificHistory(content, document.state);
      else if (document.kind === 'population-labels' || (document.kind === 'scene' && object(document.state) && object(document.state.scientificView) && object(document.state.scientificView.inputs) && !('runtime' in document.state.scientificView))) {
        content.append(node('p',document.kind === 'population-labels' ? 'Retained original image and corrected instance labels. This document opens saved data without launching its producing model.' : 'Saved native results. Inspect or measure the retained arrays, or explicitly choose a prepared interactive tool. Opening this result does not rerun its producer.'));
        scientificTools(content,{...ref,version:Number(ref.version)},async target=>{await openStudyScientific({title:target.title,resource:{...ref,id:target.id,version:target.version},zoom:1,pan:[.5,.5]},{focus:true});},
          {heading:'Continue with this window’s saved input',inputName:title});
        scientificResourceActions(content,{...ref,version:Number(ref.version)},document.state,
          async revision=>{await openStudyScientific({...saved,placementId:undefined,resource:{...ref,version:revision}});},
          {signal:abort.signal,selection:continuation?.selection,editable:async revision=>{await makeEditableTable({...saved,resource:{...ref,version:revision}},root);},current:()=>!abort.signal.aborted&&root.dataset.project===ref.day&&!root.hidden&&root.dataset.workspaceHidden!=='true'});
        methodChooser(content,{...ref,version:Number(ref.version)},async target=>{await openStudyScientific({...saved,placementId:undefined,title:target.title,resource:{...ref,id:target.id,version:target.version}});},
          {signal:abort.signal,current:()=>!abort.signal.aborted&&root.dataset.project===ref.day&&!root.hidden&&root.dataset.workspaceHidden!=='true'});
        showPopulationProvenance(content,document.state);
      }
      else if (object(document.state) && document.state.scientificView) controller = new ScientificDocument(root, content, {...saved,title},displayTitle,selectedScientificTool(document.state,{...ref,version:Number(ref.version)})?.name ?? readerContext?.name ?? null,!presentation.background,readerContext?.input);
      else content.append(node('p', 'Saved scientific document. Its integration has not declared an interactive scientific view.'));
      controller?.checkingSavedTool(madeChecked);
      controller?.mountSavedActions(document.state,abort.signal,continuation?.selection);
      root.dataset.displayState = 'rendered';
      continuation?.signal.removeEventListener('abort',cancelContinuation);
      return true;
    } catch (error) {
      if (abort.signal.aborted) return false;
      root.dataset.displayState = 'failed'; content.replaceChildren(node('p', words(error)), button('Retry saved document', () => void load()));
      return false;
    }
  };
  documents.set(saved.placementId, { root, resource: ref, reload: load });
  return load();
}


/** The tool sessions open in this workspace (`GET /api/scientific-reviews`, the same list as Open analysis windows),
 * named by their saved document: what restarting or quitting Hooke would stop (restart-readiness.ts). Throws when
 * the list can't be read. With no workspace connection for scientific tools and no scientific window open here, this
 * workspace has none. */
export async function openToolSessionTitles(): Promise<string[]> {
  if (!options) return [];
  let transport: ReturnType<Options['transport']>;
  try { transport = options.transport(); } catch (error) { if (!documents.size) return []; throw error; }
  const result = await json(await transport.request('/api/scientific-reviews'));
  transport.check();
  if (!Array.isArray(result.reviews)) throw new Error('The tool sessions could not be read.');
  return result.reviews.map(item => {
    const resource = object(item) && object(item.document) ? item.document : null;
    const known = resource && [...documents.values()].find(value => value.resource.day === resource.day && value.resource.id === resource.id && value.resource.version === resource.version);
    return known?.root.getAttribute('aria-label') ?? 'a saved document';
  });
}

export function openScientificSessions(): void {
  const sheet = new StudioSheet('Open analysis windows'), content = node('section'), list = node('div');
  content.className = 'scientific-document';
  const status = node('p'); status.setAttribute('role', 'status');
  let closed = false, loading = false;
  const load = async () => {
    if (closed || loading) return;
    loading = true; status.textContent = 'Checking this workspace connection…';
    try {
      if (!options) throw new Error('Wait for the workspace to finish opening.');
      const transport = options.transport(), result = await json(await transport.request('/api/scientific-reviews'));
      transport.check(); if (closed) return;
      if (!Array.isArray(result.reviews) || result.reviews.length > 4) throw new Error('Scientific session list is invalid. Reconnect before trying again.');
      list.replaceChildren();
      for (const item of result.reviews) {
        if (!object(item) || typeof item.review !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/.test(item.review) || !object(item.document))
          throw new Error('A scientific session has no valid identity. Reconnect before trying again.');
        const section = node('section'), id = item.review;
        const resource = item.document;
        const known = [...documents.values()].find(value => value.resource.day === resource.day && value.resource.id === resource.id && value.resource.version === resource.version);
        const title = known?.root.getAttribute('aria-label') ?? 'Saved scientific document';
        section.append(node('h3', title), node('p', `Revision ${String(item.document.version)} · ${String(item.state)}`),
          node('p', options.location?.(String(item.document.day), String(item.tab)) ?? `Project ${String(item.document.day)}`));
        if (typeof item.sessionNotice === 'string') section.append(node('p', item.sessionNotice.slice(0, 4096)));
        const history = historyButton(item.history); if (history) section.append(history);
        const savedArrays = arraySave(item.arraySave);
        if ((savedArrays?.state === 'saved' || savedArrays?.state === 'copied') && savedArrays.resource) {
          const resource = savedArrays.resource;
          section.append(button(savedArrays.state === 'copied' ? 'Open saved copy' : `Open saved array revision ${resource.version}`, () => { void openStudyScientific({ resource, title: 'Saved scientific work', zoom: 1, pan: [.5, .5] }); }));
        } else if (savedArrays) section.append(node('p', savedArrays.state === 'staged'
          ? 'Array changes are staged. Resume the original window and retry saving these changes.'
          : savedArrays.state === 'conflict'
            ? 'Another scientific revision was accepted first. Your edits remain in this private view until Stop. Open the newer saved revision from Project Data.'
            : 'Array save failed. Your saved revisions stay available in Project Data.'));
        const completed = item.completed;
        if (object(completed) && object(completed.reply)) {
          const details = node('details'); details.append(node('summary', 'Last completed operation'));
          if (completed.operation === 'python' || completed.operation === 'python-resources') {
            const transcript = pythonTranscript(completed.reply.console);
            for (const key of ['stdout', 'stderr', 'result', 'error'] as const)
              if (transcript[key]) details.append(node('pre', transcript[key]));
          } else details.append(node('p', 'A control change completed. Resume its saved document to inspect the current view.'));
          section.append(details);
        }
        const stop = button('Stop analysis window', () => { void (async () => {
          stop.disabled = true;
          try {
            await json(await transport.request(`/api/scientific-reviews/${id}`, { method: 'DELETE' }));
            transport.check(); notifyStopped(id); if (!closed) { section.replaceChildren(node('p', 'Stopped. Saved Project data stays.')); }
          } catch (error) { if (!closed) { status.textContent = words(error); stop.disabled = false; } }
        })(); });
        stop.title='Stops this private window. Any unsaved edits in it will be discarded; saved Project results remain.';
        section.append(node('p',savedArrays && !['saved','copied'].includes(savedArrays.state)?'Stopping this window discards its unsaved changes. Saved results remain.':'Stop closes this private window. Any unsaved edits will be discarded; saved results remain.'));
        section.append(stop); list.append(section);
      }
      const project = options.currentProject?.();
      if (project && options.recovery) {
        const persistence = options.recovery;
        let offset = 0;
        const recovery = node('section');
        recovery.append(node('h3', 'Earlier windows in this browser'), node('p', 'First select the original work Tab. Reopen its input below, then choose Open saved view. Recovery refuses a different Tab; it never runs analysis. These entries do not confirm a running session.'));
        const more = button('More earlier windows', () => appendPage());
        const appendPage = () => {
          if (closed || options?.currentProject?.() !== project) { status.textContent = 'Return to the original Project before recovering this window.'; return; }
          const windows = scientificRecoveryWindows(persistence, project, offset);
          more.remove();
          for (const window of windows) recovery.append(button(`Reopen input ${window.id.slice(-6)} · revision ${window.revision}`, () => {
            if (closed || options?.currentProject?.() !== project) { status.textContent = 'Return to the original Project before recovering this window.'; return; }
            void openStudyScientific({resource:{day:project,id:window.id,version:window.revision,picture:null},placementId:window.placement,title:'Recover scientific work',zoom:1,pan:[.5,.5]}).then(opened => { if(opened) sheet.close(); });
          }));
          offset += windows.length;
          if (scientificRecoveryWindows(persistence, project, offset).length) recovery.append(more);
          if (offset && !recovery.parentNode) list.append(recovery);
        };
        appendPage();
      }
      status.textContent = result.reviews.length ? 'Analysis windows can keep running while hidden. Saved results remain in Project Data.' : 'No analysis windows are open.';
    } catch (error) { if (!closed) status.textContent = words(error); }
    finally { loading = false; }
  };
  content.append(status, list, button('Refresh windows', () => void load()));
  sheet.onDismiss = () => { closed = true; content.remove(); };
  sheet.open(content); void load();
}
