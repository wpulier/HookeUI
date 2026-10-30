// A session's sources (ADR 0022; docs/design/2026-10-12-session-sources.md), the studio's half: what a session looks
// at. The side bar's Sources section lists them: the live camera first, then the session's own recordings and pictures
// (opened in it, or made in it), newest first under their specimens. A click puts one on stage (the server plays it on
// the camera bridge: commands.py `source`), × takes it out of the session with an Undo, and each row's details are the
// asset's own fields (specimen, objective, title, notes), which follow it into every session. Under a row, "Also in:"
// names the other sessions that use the same asset, each a link that opens that session (the user's Q27), so earlier
// work on it is one click away. Add… brings one in: from this Mac (uploaded to the camera bridge, then opened) or From
// Assets (the side bar's Assets section, asset-library.ts: the user's own recordings and pictures, and example data; the
// camera chip's menu keeps test footage).
//
// The model, the words and the adding are pure or take what they touch as arguments (sources.test.ts); the section is a
// view over them. What is on stage, the uploads and the commands are main.ts's: they touch the camera bridge and the
// server.
import type { AssetMeta, SourcesMessage } from './protocol';
import {readProjectMediaReference,sameMediaReference} from './project-media-reference';
import { CAMERA_STEPS, PICTURE_STEPS } from './camera';
import { clock } from './playhead';
import { lastUsedWords } from './sessions';

/** One of the session's sources as the server lists it: the asset's metadata, when it was opened, whether it joined
 * on its own (made in the session), and where a recording was left. */
export type SourceRow = SourcesMessage['sources'][number];

/** A session's sources as the studio holds them (the route's body, or the `sources` message). */
export interface SourcesState {
  session: string;
  onStage: string | null;
  onStageRetained?: AssetMeta['retained'];
  onStageAsset?: AssetMeta;
  sources: SourceRow[];
  removed: string[];
}

/** One of the user's assets, as the wire gives it; anything else (an older or newer server's row) is left out. */
export function isAsset(value: unknown): value is AssetMeta {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  return typeof v.id === 'string' && /^a\d+$/.test(v.id) && (v.kind === 'recording' || v.kind === 'still') && typeof v.title === 'string';
}

/** The route's body or the message, read safely; null when it is not a session's sources. */
export function readSources(body: unknown): SourcesState | null {
  if (!body || typeof body !== 'object') return null;
  const b = body as Record<string, unknown>;
  if (typeof b.session !== 'string' || !b.session) return null;
  const retained=b.onStageRetained===undefined?undefined:readProjectMediaReference(b.onStageRetained,b.session);
  if(retained===null||retained&&b.onStage==='live')return null;
  if(b.onStageAsset!==undefined&&(!isAsset(b.onStageAsset)||b.onStageAsset.id!==b.onStage||
    !sameMediaReference(b.onStageAsset.retained,retained)))return null;
  return {
    session: b.session,
    ...(retained?{onStageRetained:retained}:{}),
    ...(isAsset(b.onStageAsset)?{onStageAsset:b.onStageAsset}:{}),
    onStage: typeof b.onStage === 'string' && b.onStage ? b.onStage : null,
    sources: Array.isArray(b.sources) ? (b.sources.filter(isAsset) as SourceRow[]) : [],
    removed: Array.isArray(b.removed) ? b.removed.filter((id): id is string => typeof id === 'string') : [],
  };
}

/** What joins a session on its own when it is made there (ADR 0022: the user's own pictures of the specimen, in Assets,
 * never Made), as it is said once it has. */
const JOINED_WORDS: Record<string, string> = { snapshot: 'Snapshot', record: 'Recording', clearer: 'Clearer picture' };

/** The sources made in the session that joined it since `before` (the same session's, read before): what "saved to this
 * session's Assets" is said of (Will, 2026-10-15: a snapshot was looked for in Made). Only by the server's own fact that a
 * row joined here on its own (`joined`, sources.rows: made in this session, never opened in it): `from` is where an asset
 * came from, not how it came into this session, so another session's snapshot added here (All assets, a recent tile, an
 * "Also in" link) is never said to be saved here, nor is a row the user's Undo brought back (ADR 0006). */
export function joinedSince(before: SourcesState | null, after: SourcesState | null): { row: SourceRow; words: string }[] {
  if (!before || !after || before.session !== after.session) return [];
  const had = new Set([...before.sources.map((s) => s.id), ...before.removed]);
  return after.sources.filter((s) => s.joined === true && !had.has(s.id) && JOINED_WORDS[s.from])
    .map((row) => ({ row, words: JOINED_WORDS[row.from] }));
}

// --- words --------------------------------------------------------------------------------------------------------

/** The heading of the sources no specimen was given for. */
export const UNNAMED = 'Not named yet';

export interface SpecimenGroup {
  key: string; // the specimen as compared ('' when not named)
  label: string;
  rows: SourceRow[];
}

/** The sources under their specimens (the user's word, else the record's), in the order the rows come (newest first),
 * so the group seen most recently is first; one heading per specimen however it is written. */
export function groupBySpecimen(rows: readonly SourceRow[]): SpecimenGroup[] {
  const groups = new Map<string, SpecimenGroup>();
  for (const row of rows) {
    const name = (row.specimen ?? '').replace(/\s+/g, ' ').trim();
    const key = name.toLowerCase();
    let group = groups.get(key);
    if (!group) {
      group = { key, label: name ? name.charAt(0).toUpperCase() + name.slice(1) : UNNAMED, rows: [] };
      groups.set(key, group);
    }
    group.rows.push(row);
  }
  return [...groups.values()];
}

/** "40x" for its objective; '' when its magnification is not known. */
export function magnificationWords(optics: AssetMeta['optics'] | null | undefined): string {
  const x = optics?.objective;
  return typeof x === 'number' && Number.isFinite(x) && x > 0 ? `${Number(x.toFixed(1))}x` : '';
}

/** A row's second line: its kind, when, a recording's length and the magnification when known ("Recording · 1:44 am
 * · 3:12 · 40x", "Picture · Oct 9 · 100x"). */
export function sourceMeta(asset: Pick<AssetMeta, 'kind' | 'capturedAt' | 'captured' | 'seconds' | 'optics'>, now: number): string {
  const when = asset.capturedAt ? lastUsedWords(asset.capturedAt, now) : asset.captured;
  const length = asset.kind === 'recording' && typeof asset.seconds === 'number' && asset.seconds > 0 ? clock(asset.seconds) : '';
  return [asset.kind === 'still' ? 'Picture' : 'Recording', when, length, magnificationWords(asset.optics)].filter(Boolean).join(' · ');
}

/** What the bridge's `/feed` says plays (camera.ts FeedState), as much as the Sources section reads. */
export interface FeedFacts {
  source: string;
  footage: string | null;
  footageTitle?: string | null;
  targetDevice?: string;
  cameraVisible?: boolean;
  bridge?: { state?: string; sourceKind?: string; devicePresent?: boolean; diagnosis?: { text: string; steps?: string[] } | null };
}

/** The bridge's name for one of the user's assets while it plays (`asset:<path>`), as against test footage. */
export function isAssetFeed(feed: Pick<FeedFacts, 'source' | 'footage'> | null | undefined): boolean {
  return feed?.source === 'footage' && !!feed.footage?.startsWith('asset:');
}

/** What is on stage as this studio sees it: the live camera while the camera plays, or test footage (which stands for
 * it); while one of the user's assets plays, the session's own `onStage`, else the source whose file it is. Null when
 * nothing is known yet. */
export function stageOf(feed: FeedFacts | null | undefined, state: SourcesState | null | undefined): string | null {
  if (!feed) return state?.onStage ?? null;
  if (!isAssetFeed(feed)) return 'live';
  const path = feed.footage!.slice('asset:'.length);
  const rows = state?.sources ?? [];
  const named = rows.find((r) => r.id === state?.onStage);
  if (named && path.endsWith(`/${named.file}`)) return named.id;
  // A Record video's file is `part-1.mp4` in every dataset: the session's own word comes first, the file only after.
  return rows.find((r) => r.file && path.endsWith(`/${r.file}`))?.id ?? state?.onStage ?? null;
}

/** The live camera's row and the home's first choice: whether it is on, and if not what is wrong and the first thing
 * to do. `away`: one of the user's assets is on stage, so the camera's own state is not read. */
export interface LiveState {
  /** none: No camera chosen (pictures and videos only), a choice and never a fault. */
  tone: 'live' | 'footage' | 'away' | 'off' | 'none';
  words: string;
  help: string;
}

export function liveState(facts: { connected: boolean; health: string | null; feed: FeedFacts | null }): LiveState {
  const { connected, health, feed } = facts;
  if (!connected || !health) return { tone: 'off', words: 'Not connected to the app', help: 'Start everything with make start, then reload this page.' };
  if (health === 'bridge_offline' || !feed) return { tone: 'off', words: 'The camera bridge is not running', help: 'Restart everything with make start.' };
  if (feed.source === 'footage' && !isAssetFeed(feed)) {
    return { tone: 'footage', words: `Test footage · ${feed.footageTitle || feed.footage?.split('/').pop() || 'playing'}`, help: '' };
  }
  const noCamera = health === 'no_camera' || feed.bridge?.state === 'no_camera' || feed.bridge?.sourceKind === 'none';
  if (noCamera) return { tone: 'none', words: 'No camera in use', help: 'Pictures and videos only. Choose a camera when you want one.' };
  if (isAssetFeed(feed)) {
    const words = feed.cameraVisible === true ? 'Phone detected' : feed.cameraVisible === false ? 'No camera detected' : 'Not on stage';
    return { tone: 'away', words, help: '' };
  }
  if (health === 'live') return { tone: 'live', words: `Live · ${feed.targetDevice || 'camera'}`, help: '' };
  const diagnosis = feed.bridge?.diagnosis;
  if (diagnosis?.text) return { tone: 'off', words: diagnosis.text, help: diagnosis.steps?.[0] ?? '' };
  if (feed.bridge?.sourceKind === 'blackmagic') return {tone:'off',words:'Waiting for Blackmagic Camera’s picture',
    help:'Keep Blackmagic Camera open on the phone. Open camera setup to check its connection and reconnect.'};
  if (feed.bridge?.sourceKind === 'other') return {tone:'off',words:'Waiting for the selected camera’s picture',
    help:'Open camera setup to check the selected camera or stream address and reconnect.'};
  if (feed.cameraVisible ?? feed.bridge?.devicePresent) return { tone: 'off', words: 'iPhone found, waiting for its picture', help: PICTURE_STEPS[0] };
  return { tone: 'off', words: 'No camera detected', help: CAMERA_STEPS[0] };
}

/** The other sessions that use a source (the server's `alsoIn`, newest first), read safely: at most three, each with
 * its id and the words it is called by (its name, else its date); an older server sends none. */
export function alsoIn(row: Pick<SourceRow, 'alsoIn'>): { id: string; label: string }[] {
  const listed: unknown[] = Array.isArray(row.alsoIn) ? row.alsoIn : [];
  const out: { id: string; label: string }[] = [];
  for (const item of listed) {
    if (!item || typeof item !== 'object') continue;
    const { id, label } = item as Record<string, unknown>;
    if (typeof id !== 'string' || !id || out.some((s) => s.id === id)) continue;
    out.push({ id, label: (typeof label === 'string' && label.trim()) || id });
  }
  return out.slice(0, 3);
}

/** A refusal said in the section or on the home: a file's place on this Mac is said by its name alone (the server's
 * words can carry a whole path), the sentence capitalised and ended. */
export function plainError(text: string): string {
  const said = text.replace(/(?:\/[^\s/:]+)+\/([^\s/:]+)/g, '$1').trim();
  return said ? said.charAt(0).toUpperCase() + said.slice(1) + (/[.!?…]$/.test(said) ? '' : '.') : '';
}

// --- the model ----------------------------------------------------------------------------------------------------

/** The open session's sources: read from the route when the session opens (or the studio connects), and replaced by
 * the server's `sources` message when they change. A row × was pressed on is hidden at once and shown again if the
 * server refused. Nothing is kept in the browser. */
export class SourcesModel {
  private sid = '';
  private current: SourcesState | null = null;
  private readonly hidden = new Set<string>();
  private readonly listeners: (() => void)[] = [];
  private reads = 0;

  /** The session the studio is in. */
  get session(): string {
    return this.sid;
  }

  /** Its sources as last read (null until read). */
  get state(): SourcesState | null {
    return this.current;
  }

  onChange(listener: () => void): void {
    this.listeners.push(listener);
  }

  private changed(): void {
    for (const listener of this.listeners) listener();
  }

  /** The session the studio is in now: another session's sources go at once. */
  setSession(sid: string): void {
    if (sid === this.sid) return;
    this.sid = sid;
    this.current = null;
    this.hidden.clear();
    this.changed();
  }

  /** The route's body or a `sources` message: applied when it is this session's. */
  apply(body: unknown): boolean {
    const read = readSources(body);
    if (!read || read.session !== this.sid) return false;
    this.current = read;
    // Whatever applied it (a `sources` message, or a read's own answer), a read still pending is now older than this
    // state: its answer is dropped, never put over it (Codex's trace: a GET answered after a newer message brought back the
    // asset the other studio had just taken off the stage).
    this.reads += 1;
    for (const id of [...this.hidden]) if (!read.sources.some((r) => r.id === id)) this.hidden.delete(id); // gone: done
    this.changed();
    return true;
  }

  /** Read the session's sources (GET /api/sessions/{sid}/sources). A read for a session left meanwhile is dropped, and
   * the newest read wins. Resolves to what was read (or what was held, when the read failed). */
  async load(fetcher: typeof fetch = fetch): Promise<SourcesState | null> {
    const sid = this.sid;
    if (!sid) return null;
    const read = ++this.reads;
    try {
      const response = await fetcher(`/api/sessions/${encodeURIComponent(sid)}/sources`, { cache: 'no-store' });
      if (!response.ok) throw new Error(`sources ${response.status}`);
      const body: unknown = await response.json();
      if (read === this.reads && sid === this.sid) this.apply(body);
    } catch {
      // kept as it was: the next change, opening or connect reads it again
    }
    return sid === this.sid ? this.current : null;
  }

  /** The rows shown: newest first, without those being taken out. */
  rows(): SourceRow[] {
    return (this.current?.sources ?? []).filter((r) => !this.hidden.has(r.id));
  }

  groups(): SpecimenGroup[] {
    return groupBySpecimen(this.rows());
  }

  row(id: string | null | undefined): SourceRow | undefined {
    return id ? this.current?.sources.find((r) => r.id === id) : undefined;
  }

  /** × pressed: hidden until the server's list comes without it. */
  hide(id: string): void {
    this.hidden.add(id);
    this.changed();
  }

  /** The removal was refused, or undone. */
  unhide(id: string): void {
    if (this.hidden.delete(id)) this.changed();
  }
}

// --- adding from this Mac -----------------------------------------------------------------------------------------

/** What the camera bridge takes (footage_media.py: videos and pictures). */
export const ADDABLE = /\.(mp4|mov|m4v|mkv|png|jpe?g|tiff?|bmp|webp)$/i;

/** The bridge's name for an uploaded file (`added/2026-10-12-onion-picture.png`): its file and its stem, which the
 * library's key and search use. */
export function uploadedFile(name: string): { file: string; stem: string } {
  const file = name.split('/').pop() ?? name;
  const dot = file.lastIndexOf('.');
  return { file, stem: dot > 0 ? file.slice(0, dot) : file };
}

export interface AddDeps {
  /** The original Project opening still owns this file choice, including after upload/discovery waits. */
  current?(): boolean;
  /** Upload to the camera bridge (camera.ts CameraFeed.upload); resolves to its item (`name`: its path under footage/). */
  upload(file: File, progress: (share: number) => void): Promise<{ name: string }>;
  /** The library's assets matching words, read fresh (GET /api/library?q=&fresh=1). */
  find(words: string): Promise<AssetMeta[]>;
  /** Put it on stage (which makes it one of the session's sources); whether it worked. */
  open(asset: AssetMeta): Promise<boolean>;
  /** Progress and problems, in a sentence. */
  say(text: string, tone: '' | 'busy' | 'error'): void;
  wait?(ms: number): Promise<void>;
}

/** Files from this Mac, one after another: each uploaded to the camera bridge, found in the library by its file, then
 * opened (opening associates it; the last one stays on stage). Anything that is not a video or a picture is said and
 * left. Resolves to the assets opened. */
export async function addFiles(files: readonly File[], deps: AddDeps): Promise<AssetMeta[]> {
  const requireCurrent = () => { if (deps.current && !deps.current()) throw new Error('The Project changed. Choose your files again in the intended Project. Files already uploaded stay in your Library.'); };
  requireCurrent();
  const usable = files.filter((f) => ADDABLE.test(f.name));
  const skipped = files.length - usable.length;
  if (!usable.length) {
    deps.say(files.length ? 'Only videos (.mp4, .mov, .m4v, .mkv) and pictures (.png, .jpg, .tif, .bmp, .webp) can be added.' : '', files.length ? 'error' : '');
    return [];
  }
  const wait = deps.wait ?? ((ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms)));
  const opened: AssetMeta[] = [];
  let problem = '';
  for (const [i, file] of usable.entries()) {
    requireCurrent();
    const of = usable.length > 1 ? ` (${i + 1} of ${usable.length})` : '';
    deps.say(`Adding ${file.name}${of}…`, 'busy');
    let item: { name: string };
    try {
      item = await deps.upload(file, (share) => deps.say(`Adding ${file.name}${of}… ${Math.round(Math.max(0, Math.min(1, share)) * 100)}%`, 'busy'));
    } catch (error) {
      problem = `${file.name} was not added: ${error instanceof Error ? error.message : String(error)}`;
      deps.say(problem, 'error');
      continue;
    }
    requireCurrent();
    const { file: name, stem } = uploadedFile(item.name);
    let asset: AssetMeta | undefined;
    for (let attempt = 0; attempt < 2 && !asset; attempt++) {
      if (attempt) await wait(1000); // its record is written as the upload ends: once more, a moment later
      requireCurrent();
      try {
        asset = (await deps.find(stem)).find((a) => a.file === name);
      } catch {
        asset = undefined;
      }
    }
    requireCurrent();
    if (!asset) {
      problem = `${file.name} was added, but it is not in your assets yet. Find it in the Library in a moment.`;
      deps.say(problem, 'error');
      continue;
    }
    deps.say(`Opening ${asset.title}…`, 'busy');
    if (await deps.open(asset)) opened.push(asset);
    else problem = `${asset.title} was added to your assets but could not be opened.`;
  }
  requireCurrent();
  if (problem) deps.say(problem, 'error');
  else if (skipped) deps.say(`Added ${opened.length}; ${skipped} ${skipped === 1 ? 'file was' : 'files were'} not a video or a picture.`, 'error');
  else deps.say(opened.length === 1 ? `Added “${opened[0].title}” to this project.` : `Added ${opened.length} to this project.`, '');
  return opened;
}

// --- views --------------------------------------------------------------------------------------------------------

function node<K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  return el;
}

function button(className: string, text: string, label = ''): HTMLButtonElement {
  const b = node('button', className, text);
  b.type = 'button';
  if (label) b.setAttribute('aria-label', label);
  return b;
}

function icon(name: string): Element {
  const svg = document.createElementNS?.('http://www.w3.org/2000/svg', 'svg') ?? document.createElement('svg');
  svg.setAttribute('aria-hidden', 'true');
  svg.setAttribute('viewBox', '0 0 16 16');
  svg.setAttribute('class', 'source-icon');
  const use = document.createElementNS?.('http://www.w3.org/2000/svg', 'use') ?? document.createElement('use');
  use.setAttribute('href', `#i-${name}`);
  svg.append(use);
  return svg;
}

/** An asset's small picture (the server's GET /api/library/{id}/thumbnail), its kind's icon while it loads or when
 * there is none (the Made in this session cards' look: studio-shell.css .made-thumb). */
export function assetThumb(asset: Pick<AssetMeta, 'id' | 'kind' | 'thumb'>): HTMLElement {
  const thumb = node('span', 'made-thumb source-thumb');
  thumb.dataset.icon = asset.kind === 'still' ? 'picture' : 'recording';
  const img = node('img');
  img.src = asset.thumb || `/api/library/${encodeURIComponent(asset.id)}/thumbnail`;
  img.alt = '';
  img.loading = 'lazy';
  img.decoding = 'async';
  img.addEventListener('load', () => { thumb.dataset.picture = ''; }, { once: true });
  img.addEventListener('error', () => { img.remove(); }, { once: true });
  thumb.append(img);
  return thumb;
}

/** What the Sources section asks main.ts to do. */
export interface SourcesActions {
  /** Put a source on stage: 'live', or an asset (one of the session's, or one picked From Assets). */
  open(target: string, asset?: AssetMeta): void;
  /** × on a source: out of this session (with Undo). */
  remove(row: SourceRow): void;
  /** One of the asset's own fields changed in its details (null clears it). */
  setMeta(id: string, set: Record<string, string | number | null>): void;
  /** Files chosen from this Mac. */
  addFiles(files: File[]): void;
  /** "Also in:" a session's name: open that session (the sessions' own switch). */
  openSession(id: string): void;
  /** From All assets: the library's page. */
  fromAssets?(): void;
}

/** The objectives offered in an asset's details; any other number is "Other". */
export const OBJECTIVES = [4, 10, 20, 40, 60, 100] as const;

/** The side bar's Assets section (its content root, sidebar.ts `content('sources')`): Add…, then this session's
 * assets under their specimens. The live camera is no row here: it is the header's camera control (Live camera in the header
 * brings it back while an asset is on stage). */
export class SourcesSection {
  readonly root: HTMLElement;
  readonly addButton = button('sources-add', 'Add…');
  readonly addMenu = node('div', 'sources-add-menu');
  readonly fileInput = node('input', 'sources-file');
  readonly status = node('p', 'sources-status');
  readonly list = node('ol', 'sources-list');
  private readonly model: SourcesModel;
  private readonly actions: SourcesActions;
  private readonly clock: () => number;
  private readonly expanded = new Set<string>();
  private stage: string | null = null;
  private opening: string | null = null;
  private stamp = '';
  /** A redraw put off while a field of a row's details is being typed in. */
  private deferred = false;

  constructor(root: HTMLElement, model: SourcesModel, actions: SourcesActions, options: { fetcher?: typeof fetch; clock?: () => number } = {}) {
    this.root = root;
    this.model = model;
    this.actions = actions;
    this.clock = options.clock ?? (() => Date.now());
    root.classList?.add('sources');
    // Add…: from this Mac (a file picker) or From All assets (the library's page).
    const bar = node('div', 'sources-bar');
    this.addButton.setAttribute('aria-expanded', 'false');
    this.addButton.setAttribute('aria-haspopup', 'true');
    this.addButton.title = 'Add a recording or a picture to this project (or drop files here)';
    this.addButton.addEventListener('click', () => this.setMenu(this.addMenu.hidden === true));
    this.addMenu.hidden = true;
    this.addMenu.setAttribute('role', 'group');
    this.addMenu.setAttribute('aria-label', 'Add from');
    const fromMac = button('sources-add-choice', 'From your Mac…');
    fromMac.title = 'Videos and pictures from this Mac: added to your assets and opened here';
    fromMac.addEventListener('click', () => { this.setMenu(false); this.fileInput.click(); });
    const fromAssets = button('sources-add-choice', 'From Library');
    fromAssets.title = 'Library: your recordings and pictures (real microscope recordings, files you added, every project’s), and the examples';
    fromAssets.addEventListener('click', () => { this.setMenu(false); this.actions.fromAssets?.(); });
    this.addMenu.append(fromMac, fromAssets);
    this.fileInput.type = 'file';
    this.fileInput.multiple = true;
    this.fileInput.accept = 'video/*,image/*,.mov,.mkv,.m4v,.tif,.tiff';
    this.fileInput.hidden = true;
    this.fileInput.addEventListener('change', () => {
      const files = [...(this.fileInput.files ?? [])];
      this.fileInput.value = '';
      if (files.length) this.actions.addFiles(files);
    });
    bar.append(this.addButton, this.addMenu, this.fileInput);
    this.status.setAttribute('role', 'status');
    this.status.hidden = true;
    this.list.setAttribute('aria-label', "This project's assets, newest first");
    const drop = node('div', 'sources-drop', 'Drop recordings or pictures to add them to this project');
    drop.setAttribute('aria-hidden', 'true');
    root.replaceChildren(bar, this.status, this.list, drop);
    root.addEventListener('focusout', () => {
      if (!this.deferred) return;
      setTimeout(() => { if (this.deferred && !this.typing()) this.render(); }, 0);
    });
    // The Add… menu closes on a click anywhere else in the section.
    root.addEventListener('click', (event) => {
      const target = event.target as Node | null;
      if (!this.addMenu.hidden && target && !this.addMenu.contains(target as never) && !this.addButton.contains(target as never)) this.setMenu(false);
    });
    root.addEventListener('keydown', (event) => {
      if ((event as KeyboardEvent).key !== 'Escape') return;
      if (!this.addMenu.hidden) { event.preventDefault(); this.setMenu(false); this.addButton.focus?.(); }
    });
    model.onChange(() => this.render());
    this.render();
  }

  private setMenu(open: boolean): void {
    this.addMenu.hidden = !open;
    this.addButton.setAttribute('aria-expanded', String(open));
  }

  /** What is on stage, and what is being put there (shown "Opening…"). */
  setStage(stage: string | null, opening: string | null): void {
    this.stage = stage;
    this.opening = opening;
    this.render();
  }

  /** A sentence under Add… (an upload's progress, or what went wrong); '' hides it. */
  setStatus(text: string, tone: '' | 'busy' | 'error' = ''): void {
    this.status.textContent = text;
    this.status.dataset.tone = tone;
    this.status.hidden = !text;
  }

  private typing(): boolean {
    const active = (typeof document !== 'undefined' ? document.activeElement : null) as HTMLElement | null;
    return !!active && this.list.contains(active as never) && /^(INPUT|TEXTAREA|SELECT)$/.test(active.tagName ?? '');
  }

  render(): void {
    if (this.typing()) { this.deferred = true; return; }
    this.deferred = false;
    const now = this.clock() / 1000;
    const groups = this.model.groups();
    const stamp = JSON.stringify([this.stage, this.opening, [...this.expanded], this.model.state === null, Math.floor(now / 60),
      groups.map((g) => [g.label, g.rows.map((r) => [r.id, r.title, r.specimen, r.kind, r.capturedAt, r.seconds, r.optics, r.notes, r.thumb, alsoIn(r)])])]);
    if (stamp === this.stamp) return;
    this.stamp = stamp;
    const rows: HTMLElement[] = [];
    for (const group of groups) {
      const heading = node('li', 'source-group', group.label);
      heading.setAttribute('role', 'presentation');
      rows.push(heading, ...group.rows.map((row) => this.row(row, now)));
    }
    if (!groups.length) {
      rows.push(node('li', 'source-empty', this.model.state === null ? 'Reading this project’s assets…'
        : 'No pictures or recordings yet.'));
    }
    this.list.replaceChildren(...rows);
  }

  private row(row: SourceRow, now: number): HTMLElement {
    const item = node('li', 'source-row');
    item.dataset.source = row.id;
    item.dataset.kind = row.kind;
    const onStage = this.stage === row.id;
    if (onStage) item.setAttribute('aria-current', 'true');
    if (this.opening === row.id) item.dataset.opening = '';
    const main = node('div', 'source-main');
    const open = button('source-open', '', onStage ? `${row.title}, on stage` : `Put ${row.title} on stage`);
    const words = node('span', 'source-words');
    words.append(node('span', 'source-title', row.title),
      node('span', 'source-meta', this.opening === row.id ? 'Opening…' : sourceMeta(row, now)));
    open.append(assetThumb(row), words);
    open.title = [row.title, row.notes ?? ''].filter(Boolean).join('\n');
    open.addEventListener('click', () => this.actions.open(row.id, row));
    const open_ = this.expanded.has(row.id);
    const more = button('source-more', '', `Details of ${row.title}`);
    more.append(icon('more'));
    more.title = 'Details: specimen, objective, title, notes';
    more.setAttribute('aria-expanded', String(open_));
    more.addEventListener('click', () => {
      if (this.expanded.has(row.id)) this.expanded.delete(row.id);
      else this.expanded.add(row.id);
      this.render();
    });
    const remove = button('source-remove', '', `Take ${row.title} out of this project`);
    remove.append(icon('close'));
    remove.title = 'Take it out of this project (it stays in your assets)';
    remove.addEventListener('click', () => this.actions.remove(row));
    main.append(open, more, remove);
    item.append(main);
    const also = this.alsoLine(row);
    if (also) item.append(also);
    if (open_) item.append(this.details(row));
    return item;
  }

  /** "Also in: Oct 12 · Sperm motility": the other sessions that use the same asset, each a link that opens it. */
  private alsoLine(row: SourceRow): HTMLElement | null {
    const others = alsoIn(row);
    if (!others.length) return null;
    const line = node('p', 'source-also');
    line.append(node('span', 'source-also-words', 'Also in: '));
    others.forEach((session, i) => {
      if (i) line.append(node('span', 'source-also-dot', ' · '));
      const link = button('source-also-link', session.label, `Open the project ${session.label}, which also uses ${row.title}`);
      link.title = `Open “${session.label}”: its work on ${row.title}`;
      link.addEventListener('click', () => this.actions.openSession(session.id));
      line.append(link);
    });
    return line;
  }

  /** An asset's own fields: each change is sent as it is made (asset-meta), and follows the asset into every session. */
  private details(row: SourceRow): HTMLElement {
    const form = node('form', 'source-details');
    form.addEventListener('submit', (event) => event.preventDefault());
    const field = (label: string, control: HTMLElement, ...more: HTMLElement[]) => {
      const wrap = node('label', 'source-field');
      wrap.append(node('span', 'source-field-name', label), control, ...more);
      return wrap;
    };
    const text = (name: 'specimen' | 'title', value: string, placeholder: string) => {
      const input = node('input', 'source-input');
      input.name = name;
      input.value = value;
      input.placeholder = placeholder;
      input.autocomplete = 'off';
      input.addEventListener('change', () => this.actions.setMeta(row.id, { [name]: input.value.trim() || null }));
      return input;
    };
    const specimen = text('specimen', row.specimen ?? '', 'What it shows');
    const title = text('title', row.title, 'Its name');
    const objective = row.optics?.objective ?? null;
    const known = objective !== null && (OBJECTIVES as readonly number[]).includes(objective);
    const pick = node('select', 'source-select');
    pick.name = 'objective';
    for (const [value, words] of [['', 'Not known'], ...OBJECTIVES.map((x) => [String(x), `${x}x`]), ['other', 'Other…']]) {
      const option = node('option', '', words);
      option.value = value;
      pick.append(option);
    }
    pick.value = objective === null ? '' : known ? String(objective) : 'other';
    const other = node('input', 'source-input source-number');
    other.name = 'objective-other';
    other.type = 'number';
    other.min = '1';
    other.max = '200';
    other.step = 'any';
    other.placeholder = 'x';
    other.setAttribute('aria-label', 'Objective, as a number');
    other.value = objective !== null && !known ? String(objective) : '';
    other.hidden = pick.value !== 'other';
    pick.addEventListener('change', () => {
      if (pick.value === 'other') { other.hidden = false; other.focus?.(); return; }
      other.hidden = true;
      this.actions.setMeta(row.id, { objective: pick.value ? Number(pick.value) : null });
    });
    other.addEventListener('change', () => {
      const value = Number(other.value);
      this.actions.setMeta(row.id, { objective: other.value.trim() && Number.isFinite(value) && value > 0 ? value : null });
    });
    const notes = node('textarea', 'source-input source-notes');
    notes.name = 'notes';
    notes.rows = 3;
    notes.value = row.notes ?? '';
    notes.placeholder = 'Anything worth remembering about it';
    notes.addEventListener('change', () => this.actions.setMeta(row.id, { notes: notes.value.trim() || null }));
    form.append(field('Specimen', specimen), field('Objective', pick, other), field('Title', title), field('Notes', notes));
    return form;
  }
}
