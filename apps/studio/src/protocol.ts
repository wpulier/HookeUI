import type { PresentMessage, ScreenItem, UserChange, WorkspaceObservation } from './screen';
import type { Space } from './spaces';
import type { LayerWire, MemberSelected, PopulationWire } from './population';
import type { TrailsMessage } from './trail';
import type { WorkspaceViewport } from './workspace-runtime';
import type { MotionTracksWire, TracksLayerWire } from './motiontracks';
import type { ResearchCapture } from './research-captures';
import type { AgentConnectionsMessage } from './agent-connections';
// Wire protocol shared with services/orchestrator (hub.py, scene.py, server.py), both ways:
// ServerMessage is what the server sends, ClientCommand what the studio sends
// (services/orchestrator/commands.py has one handler per kind).

export type Point = [number, number];

/** Exact Capture facts paired with this packet's JPEG; no source or completed-draw authority. */
export interface FrameBasis {
  readonly schema: 'frame-basis-v1';
  readonly frameId: number;
  readonly captureId: null; // no canonical capture identity producer yet
  readonly run: string;
  readonly boot: string;
  readonly seq: number | null;
  readonly epoch: number;
  readonly tracked: boolean;
  readonly imageSize: readonly [number, number];
  readonly disc: readonly [number, number, number] | null;
  readonly offset: readonly [number, number];
  readonly objective: number | null;
  readonly umPerPx: number | null;
  readonly scaleId: null; // no canonical scale identity producer yet
  readonly scale: Readonly<Record<string, unknown>> | null; // full frozen reading and intake provenance
}

export interface FrameHeader {
  type: 'frame';
  id: number;
  ba?: number | null; // when the camera bridge took it (wall seconds): camera-to-screen is measured from here
  run?: string; // present on current packets; older headers can omit provenance
  boot?: string;
  seq?: number | null;
  frameBasis?: FrameBasis | null; // absent/null on legacy packets; never infer from current controls
  motionTracks?: MotionTracksWire; // paired observations of this exact JPEG, never a later frame
  w: number;
  h: number;
  dx: number; // cumulative content offset (px): content at p in frame A is at p + (B.dx - A.dx) in frame B
  dy: number;
  epoch: number;
  tracked: boolean;
  steady: boolean;
  speed: number; // px/frame of slide motion
  q?: string[]; // what is wrong with the view: frozen, blurry, empty, dark, bright, glare (orchestrator/quality.py)
  still?: number; // seconds the picture has not changed at all (a frozen feed still arrives)
  t: number; // wall time the frame arrived at the orchestrator (s)
  an?: number | null; // wall time its disc, motion and focus were done (s)
  sent: number; // wall time it was handed to the studios (s)
  fov: [number, number, number] | null; // cx, cy, r
}

export interface SceneObject {
  id: string;
  kind: 'highlight' | 'part' | string; // cells are a population (population.ts), not outlines
  polygons: Point[][];
  anchorFrame: number;
  anchorEpoch: number;
  anchorOffset: Point;
  source: string;
  label: string;
  labelKey: string; // one key per kind of thing; descriptions are kept per key (orchestrator/labels.py)
  status: 'outline' | 'looking' | 'unsure' | 'likely' | 'confirmed' | string;
  maybe?: string[]; // when not sure: what else it may be
  size?: { lengthUm: number; widthUm: number; areaUm2: number; measured: boolean } | null; // once the scale is known
  confidence: number | null;
  note: string;
  seen: string; // what the AI saw in this one, in words
  group: string | null;
  trial: string | null; // blind A/B/C trial this outline belongs to, until the vote
  parent: string | null; // dissected parts: the object they are part of
  arm: string | null; // its letter in that trial
  track: 'tracking' | 'refinding' | 'lost' | 'out_of_view' | string;
  revisedBy: string | null; // who changed the label (an accepted correction)
  pinned: string | null; // a researcher note pinned under the label
  taught?: TaughtMatch | null; // the closest of the labels the user taught (orchestrator/taught.py); never the label itself
  createdAt: number;
  /** The outline check's verdict while it stands: it said this outline does not fit and nothing replaced it
   * (orchestrator/outline_check.py). Drawn as uncertain, and said under its label. */
  fit?: { fits: 'too_big' | 'too_small' | 'wrong' | string; why: string };
}

/** How alike an outline is to the examples the user named (cosine similarity of option EMBED's model, 0-1). */
export interface TaughtMatch {
  label: string;
  similarity: number;
  examples: number;
}

export interface Candidate {
  id: number;
  x: number;
  y: number;
  r: number;
  dark: boolean;
}

export interface Health {
  state: string;
  stateLabel: string;
  fps: number;
  bridge: { url: string; targetDevice: string | null; ok: boolean; error: string };
  gpu: { ready: boolean; device: string | null; error: string };
  machine?: MachineState;
  models?: ModelsHealth;
}

/** Which defaults really ran and how each provider's calls ended, the last ten minutes (orchestrator/fallbacks.py,
 * research_worker/api_usage.recent). */
export interface ModelsHealth {
  fallbacks: { role: string; default: string; used: string; times: number; why: string; agoS: number }[];
  providers: Record<string, Partial<Record<'ok' | 'cut-off' | 'rate-limit' | 'auth' | 'server' | 'connection' | 'failed', number>>>;
}

/** How the computer is doing (orchestrator/machine.py), sampled every 5 s; any field may be
 * missing where the system does not say. */
export interface MachineState {
  cores?: number;
  perfCores?: number;
  load1?: number; // threads ready to run, averaged over a minute
  load5?: number;
  memFreePct?: number;
  swapTotalMb?: number;
  swapUsedMb?: number;
  diskFreeGb?: number;
  diskTotalGb?: number;
  clickP95Ms?: number; // the last 20 clicks
  clicks?: number;
  at?: number;
  ours?: OurWorker[]; // this app's own workers, sampled every tick, with what is loading there
  swapInsPerS?: number; // pages swapped back in a second over the contract's window, 30 s (programs waiting on disk)
  swapOutsPerS?: number;
}

/** One of this app's own worker processes (machine.py `ours`): its role, its share of the CPU, and the model
 * loading there now, if one is ("micro-sam"). */
export interface OurWorker {
  pid: number;
  role: string; // "analysis worker", "micro-sam (analysis worker)"
  name: string; // "HookeUI: analysis worker (loading micro-sam)"
  cpu: number; // % of one core
  memMb: number;
  loading?: string;
}

/** A program using the CPU now (GET /api/machine/top, while the meter's panel is open). */
export interface TopProgram {
  name: string;
  cpu: number; // % of one core
  memMb: number;
  ours: boolean; // HookeUI's own
}

/** What a session holds, counted by the server: the header chip, its menu and the sessions rail say the same numbers
 * (sources: the recordings and pictures it opened; made: Made in this session's products, its analyses; researchers;
 * conversations). */
export interface SessionCounts {
  sources?: number; // opened in it and not removed (ADR 0022), never the live camera; an older server sends none
  made: number;
  researchers: number;
  conversations: number;
}

/** The session you are in: the project, not the day (a session may span days, a day may hold several sessions).
 * `id` is the session id (sid), OPAQUE: "2026-10-12" or "2026-10-12.2"; the studio never parses a date from it.
 * Every field after `specimen` is optional: an older server sends only the first five, and the chip then reads
 * "Today · Oct 12" as before. */
export interface SessionState {
  type: 'session';
  id: string; // the session id (sid), opaque
  label: string; // "Session: Oct 4"
  started: number; // epoch s
  specimen: string; // what is in view now
  name?: string; // "" until named (automatically, from the first specimen or request, or by the user)
  named?: boolean; // the user named it
  lastActive?: number; // epoch s
  days?: string[]; // the calendar dates (YYYY-MM-DD) it touched, oldest first
  counts?: SessionCounts;
  /** The app's suggestion of a new session (a new specimen long after this one was last used); never automatic. */
  suggest?: { specimen: string; hours: number } | null;
}

/** A session was opened (the server's `session-opened`): the App was reset for it. `why` says how: started by the user
 * (`new`), gone back to (`switch`), or the app's start (a studio that connects gets none and treats that as `start`).
 * The studio decides from it what the stage shows (session-view.ts: a new session's home, or where a returning one was
 * left); an older server sends none of the fields. */
export interface SessionCleared {
  type: 'session-cleared';
  session?: string; // the sid opened
  previous?: string | null; // the sid left
  why?: 'new' | 'switch' | 'start';
}

/** One of the user's recordings or pictures (the server's library.py, ADR 0022): its metadata, the user's fields over
 * the app's. `from` says where it came from: a real microscope recording, a file added here, a Record video, a snapshot
 * or a clearer picture. */
export interface AssetMeta {
  id: string; // a7: the library's own id
  kind: 'recording' | 'still';
  title: string;
  from: 'recording' | 'added' | 'record' | 'snapshot' | 'clearer' | string;
  capturedAt: number | null;
  captured: string; // "Oct 12, 1:44 am"
  seconds: number | null;
  capturedOn: string | null;
  microscope: boolean | null;
  optics: { objective?: number; eyepiece?: number; fieldNumber?: number; umPerPx?: number };
  specimen: string | null;
  specimenBy: 'user' | 'record' | null;
  notes: string | null;
  origin: string | null; // the session it was made in
  file: string;
  thumb?: string;
  // Where the library files it (library.py CATEGORIES): the user's category over the app's look; null before a look.
  category?: { group: string; name: string; by: 'user' | 'app'; sure: boolean } | null;
}

/** A session's sources (GET /api/sessions/{sid}/sources, and this message for the open session when they change):
 * what is on stage ("live", an asset's id, or null before anything was opened), its assets newest first (opened, or
 * made in it: `joined`), with where a recording was left and the other sessions that use it (`alsoIn`, newest first, at
 * most 3; an older server sends none), and those removed (for Undo). */
export interface SourcesMessage {
  type: 'sources';
  session: string;
  onStage: string | null;
  sources: (AssetMeta & { opened: number | null; joined: boolean; position: number | null; alsoIn?: { id: string; label: string }[] })[];
  removed: string[];
}

/** One session as the sessions list shows it (the header chip's menu and the sessions rail). */
export interface SessionRow {
  id: string; // the sid, opaque
  name: string; // "" until named
  named: boolean;
  label?: string; // what it is called, the server's words: its name, "New session" while it began today, else its date
  started: number; // epoch s
  lastActive: number; // epoch s
  days: string[]; // calendar dates it touched, oldest first
  counts: SessionCounts;
  specimens: string[];
  working: number; // researchers working in it now
  thumb?: string; // a picture of it (an image URL), optional
}

/** Every session, newest first by `lastActive`: sent on connect, after every session change or rename, and when its
 * counts change (GET /api/sessions gives the same `{ current, sessions }`). */
export interface SessionsList {
  type: 'sessions';
  current: string; // the sid open now
  sessions: SessionRow[];
  root?: string; // where every Project is saved on this Mac, with ~ ("~/MicroscopeMind/sessions"); an older server sends none
}

/** The session's conversations (orchestrator/day/store.py). `id` is the session id (sid), opaque. */
export interface ThreadInfo {
  id: string; // "t2"
  key: string; // "2026-10-05/t2"
  title: string;
  started: number;
  last: number;
  subjects: string[];
  requests: number;
  research: string[];
}

export interface DayState {
  type: 'day';
  localOnly?: boolean; // authoritative session policy; independent of global model preferences
  id: string; // the session id (sid), opaque: never parsed for a date
  label: string;
  active: string;
  threads: ThreadInfo[];
  subjects: string[];
  prefs: Record<string, unknown>;
  /** When this session began (its first conversation's start, epoch s): "start today afresh" begins a new one, a
   * restart does not. Made in this session lists what was made since. */
  sessionStart?: number | null;
}

/** The active conversation as the chat showed it: replaces the log. */
export interface ThreadReplay {
  type: 'thread';
  day: string; // the session id (sid) it belongs to, opaque
  thread: string;
  title: string;
  lines: ServerMessage[];
  summary: string;
}

/** One line of the conversation as the chat shows it (typed or spoken). */
export interface ChatLine {
  /** Recorded server event time (epoch seconds); absent on historical lines. */
  at?: number;
  type: 'chat';
  role: 'user' | 'assistant' | 'compare' | 'system';
  text: string;
  objectIds?: string[];
  voice?: boolean; // a spoken turn (replayed after a reload; live ones come from the voice socket)
  echo?: boolean; // a spoken line sent to every studio; the one talking already shows it
  speechSession?: string; // timeline scope for current Live captions
  lineId?: string; // a spoken line that grows: a later line with this id replaces this one's text
  open?: boolean; // still growing (not yet filed); the finished line follows with the same lineId
  turnId?: number; // the request this answers (later tool results update its action lines)
  actions?: ToolRun[]; // what the brain did with its tools this turn
  update?: boolean; // a result that finished after the answer
  proactive?: boolean; // the app said this unasked (a researcher's finding, a suggestion)
  research?: string; // the researcher this is about
  thread?: string | null; // the conversation it belongs to (shown only while that one is open)
  day?: string | null; // the session (sid, opaque) it was filed in, when the server says: shown only while that one is open
  thinkMs?: number;
  totalMs?: number;
  model?: string;
  labels?: string[];
}

/** A tool of an earlier answer finished: that answer's action line updates in place. */
export type ChatAction = {
  type: 'chat-action'; turnId: number; thread?: string | null; day?: string | null; answer?: string;
  /** Actual operation activity when this turn has no model answer to attach it to. */
  standalone?: boolean;
} & ToolRun;

/** An unavailable interpretation when no operation was attempted. No invented tool receipt. */
export interface ChatAnswerStatus {
  type: 'chat-action';
  turnId: number;
  thread?: string | null;
  standalone: boolean;
  answerStatus: 'not-published';
  calls: number;
  model: string;
}

export type NoticeValue = string | number | boolean | null | NoticeValue[] | { [key: string]: NoticeValue };

/** A model that worked on a step (orchestrator/activity.py): its role ("drawn by"), its one name (names.py) and that
 * name said with where it runs; `instead`: what could not run, so this one did (a declared fallback). */
export interface StepModel {
  role: string;
  name: string;
  said: string;
  instead?: string;
  failed?: boolean;
}

/** Plain facts an Activity line opens to; `ids` stay behind "Technical". */
export interface ActivityDetails {
  models?: StepModel[];
  ms?: number;
  lines?: string[];
  ids?: Record<string, string | number>;
  /** A request's answer: whether one was given, by which model, and how long it took. */
  answer?: { published: boolean; model: string; thinkMs?: number | null; totalMs?: number | null; calls?: number | null; at?: number };
  /** A request's own look: how many things it outlined, and what found and drew them. */
  look?: { outlined: number; models: StepModel[] };
}

/** One Activity line (orchestrator/activity.py): a request's own record (`kind` "request": its words, channel and
 * steps), or something that is not a chat request (a click, a studio action, a setting, a model load, a researcher).
 * Journaled with its conversation and replayed; a later line with the same `id` replaces it (running, then done). */
export interface ActivityEvent {
  type: 'activity';
  id: string;
  kind: 'request' | 'click' | 'command' | 'setting' | 'model' | 'research';
  text: string;
  at: number;
  state: 'done' | 'running' | 'failed';
  details?: ActivityDetails;
  ref?: { turnId?: number; channel?: 'chat' | 'voice'; objectId?: string; researcher?: string; model?: string };
  steps?: ToolRun[];
  thread?: string | null;
  day?: string | null; // the session (sid, opaque) it was filed in
  lineId?: string;
  /** Work that finished in another session, filed here too: said "from 'Blood smear'". */
  fromSession?: { id: string; name: string };
}

/** Factual application activity, journaled independently of model replies and tool receipts.
 * Origin IDs are retained facts, never manufactured turn/operation/inspection anchors. */
export interface AppNotice {
  type: 'app-notice';
  day: string; // the session (sid, opaque) it is filed in, distinct from the origin's
  /** Work that finished in another session (a researcher started there): said "from 'Blood smear'". */
  fromSession?: { id: string; name: string };
  thread: string;
  at: number;
  producer: { kind: string; id?: string };
  event: string;
  summary: string;
  tone: 'info' | 'error'; // UI presentation only
  facts: Record<string, NoticeValue>;
  origin: {
    day: string | null;
    asked?: { thread: string; generation: number };
    thread?: string;
    researchId?: string;
    ticketId?: string;
    requestId?: string;
    actionId?: string;
    turnId?: number;
    delegationId?: string;
  };
}

/** Where the magnifier's picture comes from: the live view, the sharpest recent frame, or a
 * researcher's picture. */
/** What kind of picture it is (orchestrator/media.py): the camera's own pixels, a real photo with labels added,
 * a reference photo from the web (not this slide), one a model made, a lesson, a written result, or not recorded. */
export type MediaKind = 'raw' | 'capture' | 'labelled' | 'reference' | 'generated' | 'derived' | 'lesson' | 'written' | 'unknown';

export interface MagnifierSource {
  resource?: { id: string; day: string; title?: string };
  /** Subject of these retained pixels, separate from the available live target. */
  objectId?: string;
  /** Digital enlargement of an image inside its pane, independent of camera zoom. */
  displayScale?: number;
  kind: 'live' | 'held' | 'image';
  space: Space; // whose pixels it is: live frame, a still of one (capture), or a picture of its own
  label: string;
  url?: string;
  credit?: string;
  /** What kind of picture it is, and that in a line to show with it (the server's words). */
  media?: MediaKind;
  line?: string;
}

/** The magnifier as the server holds it (orchestrator/magnifier.py): the brain's tools open and
 * drive it, so the studio only shows it. */
export interface MagnifierState {
  type: 'magnifier';
  space: Space; // `center` is in the pixels of the object's anchor frame
  /** Only a temporary loss fallback returns to live; explicit source choices revoke it. */
  resumeLive?: boolean;
  open: boolean;
  objectId: string | null;
  sources: MagnifierSource[];
  index: number;
  /** A picture shown next to the close-up, both at once in the one window (null: none). */
  beside?: MagnifierSource | null;
  /** How much closer than fitting the object (null = fitted, 1). */
  zoom?: number | null;
  /** A point to centre on (frame px in the object's anchor frame), or null for its centre. */
  center?: [number, number] | null;
  /** The closest it goes for the object shown (the server works it out; the studio never does). */
  maxLevel?: number;
  /** Past the camera's detail at this level: its pixels are shown as blocks ("pixel-enlarged").
   * The server decides it (for the contract's panelCssPx); the studio only shows it. */
  pixelated?: boolean;
}

/** What the result viewer shows (orchestrator/presenter.py). */
/** The slide map (orchestrator/slidemap.py): the steady views of the current motion epoch, pasted
 * where they are on the slide, as a JPEG at `url` (null until something was pasted). Map px are
 * slide px x `scale`; `origin` is the map px of the picture's top-left, `size` its width and
 * height. Content at frame px p of a frame with offset (dx, dy) is at slide px p - (dx, dy). */
export interface SlideMapState {
  type: 'slidemap';
  space: Space; // map px: `origin` and `size` are in them
  open: boolean;
  epoch: number | null;
  scale: number;
  maps: number; // maps kept (one per epoch; the earlier ones cannot be placed against this one)
  version: number;
  url: string | null;
  origin: [number, number] | null;
  size: [number, number] | null;
}

/** The Data light (orchestrator/datasets/checks.py, docs/datasets.md): whether the dataset Record collects
 * is really being saved, from its own files, and after a stop whether it was saved whole. */
export interface DatasetCheck {
  // checking: the first seconds of a recording; ok: every file seen growing; problem: one is not (or the
  // last dataset was not saved whole); saving: stopped, the camera video still being finished; saved: done
  state: 'off' | 'checking' | 'ok' | 'problem' | 'saving' | 'saved';
  id: string | null; // the dataset it is about (after a stop, the one just saved)
  items: { name: 'video' | 'times' | 'frames' | 'records' | 'disk'; ok: boolean; problem: string }[];
  problems: string[]; // one plain sentence for each thing not being saved, and what still is
  at: number | null; // when the state last changed (epoch seconds)
  last: DatasetEndCheck | null; // the end check of the last dataset stopped
}

export interface DatasetEndCheck {
  id: string;
  ok: boolean;
  problems: string[];
  notes: string[];
  atEpoch: number;
  counts: { events?: number; frames?: number; pictures?: number };
  videoParts: number;
}

/** The dataset Record collects (orchestrator/datasets/collector.py, docs/datasets.md): on with Record, off
 * with its Stop (ADR 0009). Its state is the server's, so a reload keeps it. */
export interface DatasetState {
  type: 'dataset';
  on: boolean;
  id: string | null;
  name: string;
  startedAt: number | null; // epoch seconds: Record's time counts from it (so a reload keeps it)
  video: { state: 'off' | 'starting' | 'recording' | 'waiting' | 'error'; parts: number; error: string };
  bytes: number;
  dropped: number; // per-frame facts dropped while the disk fell behind (records never are)
  resumedAfter: number | null; // the app was down this long, then the same dataset carried on
  diskLow: boolean;
  diskFree: number | null;
  error: string;
  check: DatasetCheck;
}

/** A dataset as the Datasets tab lists it (GET /api/datasets; orchestrator/datasets/store.py). */
export interface DatasetRow {
  id: string;
  name: string;
  status: 'collecting' | 'complete';
  startedAt: string | null;
  startedAtEpoch: number | null;
  stoppedAt: string | null;
  stoppedAtEpoch: number | null;
  endedBy: string | null;
  reconstructed: boolean;
  runs: number;
  gaps: { from: string; to: string; seconds: number }[];
  parts: { n: number; videoKept: string | null; sidecarKept: string | null; hasSidecar?: boolean; joined: boolean | null; bytes: number }[];
  runSpans?: [number | null, number | null][]; // each run of the app: start and end epoch (end null: still on)
  thumb?: boolean; // it has a picture to show (GET /api/datasets/<id>/thumb)
  screens?: number; // the studio's own screen recordings kept with it
  endCheck?: { ok: boolean; problems: string[]; notes: string[] } | null; // after Stop: checked as saved whole, or why not
  enteredAfter?: { field: string; value: unknown }[]; // microscope facts entered after its start
  sample?: Record<string, unknown>; // what the user said about the sample
  research?: number; // researchers' work kept with it (a reconstructed dataset)
  counts: { events?: number; frames?: number; pictures?: number; droppedFrames?: number };
  rig: Record<string, unknown>;
  pictures: number;
  files: Record<string, number>;
  bytes: number;
  path: string;
}

/** One fact about the microscope and where it came from (orchestrator/datasets/rig.py): the user entered
 * it, the app measured it, the bridge was asked for it, the app assumed it (default), or nobody said. */
export interface RigFact {
  value: string | number | null;
  source: 'user' | 'measured' | 'bridge-config' | 'default' | 'estimate' | 'unknown' | string;
  via?: string | null;
  at?: number | null;
}

/** The microscope as the user describes it (rig.json, outlives days); the Datasets tab sets it. */
export interface RigState {
  type: 'rig';
  facts: Record<string, RigFact>;
  fields: Record<string, string>; // each fact's name -> what it is
}

export interface DatasetListing {
  root: string;
  datasets: DatasetRow[];
  collecting: DatasetState | null;
}

export interface PresenterState {
  type: 'presenter';
  open: boolean;
  rid: string | null;
  index: number;
  savedSession?: ResearchSession;
  outputId?: string | null;
  findingRevision?: {version:number; digest:string; complete:boolean} | null;
  step?: string | null; // the lesson step being gone through ("q2"): the lesson scrolls to it
  stepKind?: StepKind | null; // how that step is answered (orchestrator/quiz.py)
  outcomes?: Record<string, StepOutcome>; // how the user did on the lesson's steps today, as the model judged
  shelfOnly?: boolean; // preference update does not change any browser viewer
  shelfRemoved?: string[]; // researchers taken off the shelf today (kept in the day's journal)
}

/** A lesson step's kind (research_worker/results.py STEP_KINDS): answered in words, by finding
 * and clicking it on the slide, or by saying what would confirm or refute the app's guess. */
export type StepKind = 'question' | 'find' | 'evidence';
export type StepOutcome = 'right' | 'wrong' | 'skipped';

/** The reply to a command (commands.py handle): `for` is the command's kind, `requestId` the
 * one it was sent with (request()). */
export interface ResultMessage {
  type: 'result';
  for: string;
  ok: boolean;
  error?: string | null;
  requestId?: string | null;
  screenReport?: ScreenReportFailure;
  [key: string]: unknown;
}

/** Refused report correlation only; never an accepted observation or source grant. */
export interface ScreenReportFailure {
  client: WorkspaceReady['client']; appRunId: string; seq: number; sourceKey: string;
}

/** Ephemeral inspection facts. These are never ChatLine, ToolRun, or thread replay data. */
export interface ExecutionReference {
  id: string;
  kind: string;
  at: number;
  day: string;
  thread: string;
  generation: number;
  turnId: number | null;
  objectIds: string[];
  trialId: string | null;
  outcome: string;
  revision: number;
  incomplete: boolean;
}

export interface ExecutionStep {
  id: string;
  parent: string;
  operation: string;
  outcome: string;
  ms?: number;
  selected?: string | null;
  selection?: string;
  adapter?: string;
  model?: string;
  // Already present in inspection receipts; describes the executed adapter's option.
  role?: string;
  optionId?: string;
  reportedModel?: string | null;
  provider?: string;
  endpoint?: string;
  device?: string;
  identity?: string;
  producer?: string | null;
  fallback?: string | null;
  policy?: string;
  worker?: string;
  rpcId?: number;
  frameId?: number | null;
  errorType?: string;
  cache?: string;
  trialId?: string | null;
  arm?: string | null;
  scope?: string;
  objectId?: string;
  sessionId?: string;
  adapterMs?: number;
}

export interface ExecutionPower {
  capability: string;
  label: string;
  provider: string;
  outcome: string;
  cache?: string | null;
  stepId: string;
}

export interface ExecutionDetail {
  markers?: { id: string; note: string; at: number }[];
  markerError?: string;
  status: 'available' | 'expired' | 'blind';
  root?: ExecutionReference;
  steps: ExecutionStep[];
  powers?: ExecutionPower[];
  context?: { words?: string; owner?: Record<string, string>;
    build?: { revision: string | null; dirty: boolean | null; sourceDigest: string | null };
    settings?: Record<string, string | null> };
  durable?: boolean;
  interrupted?: boolean;
  archive?: { dropped: number; writeErrors: number; recoveryGaps: number };
}

/** Show me (services/orchestrator/showing.py): a researcher's finding being shown, and whether it still is. */
export interface ShowingState { type: 'showing'; id: string; rid: string; about: string; active: boolean }

/** Closed accepted-origin wire. Connection carries it; App remains its authority. */
export interface WorkspaceHello {
  type: 'workspace-hello'; schema: 'workspace-origin-v2'; requestId: string;
  workspaceId: string; clientId: string; pageEpoch: string;
  restoration?: 'project-layout';
}
export interface WorkspaceReady {
  type: 'workspace-ready'; schema: 'workspace-origin-v2'; ok: true; requestId: string;
  for?: 'workspace-hello'; // commands._handle keeps this field on its successful ready reply
  client: { workspaceId: string; clientId: string; pageEpoch: string; connectionEpoch: string };
  appRunId: string; connectionGrant: string;
  restoration?: 'project-layout';
}

/** Optional returned-tab pixels; structural inclusion and source paint remain unproved. */
export interface WorkspaceCaptureRequest {
  type: 'screen-look'; schema: 'workspace-capture-v1'; id: string; lookId: string; requestId: string;
  appRunId: string; client: WorkspaceReady['client']; originSnapshotId: string; layoutRevision: number;
  targetIds: string[]; maxBytes: number; maxPixels: number; maxInputEdge: number; deadlineMono: number; timeoutMs: number;
}
export type WorkspaceCaptureCode = 'share-required' | 'unsupported-api' | 'unsupported-handle' | 'permission-declined'
  | 'wrong-surface' | 'track-unavailable' | 'session-retired' | 'layout-changed' | 'unsupported-mapping'
  | 'sample-unavailable' | 'encode-failed' | 'bounds-exceeded';
interface WorkspaceCaptureCorrelation {
  type: 'screen-image'; schema: 'workspace-capture-v1'; id: string; lookId: string; requestId: string;
  appRunId: string; client: WorkspaceReady['client']; originSnapshotId: string;
}
export type WorkspaceCaptureReply = WorkspaceCaptureCorrelation & ({ status: 'unavailable'; code: WorkspaceCaptureCode }
  | { status: 'captured'; jpeg: string; sha256: string; capturedObservationId: string;
    layoutRevisionBefore: number; layoutRevisionAfter: number; originalSize: number[]; inputSize: number[];
    workspaceToImage: number[]; captureTimeMonoMs: number; uncertaintyMs: number;
    fidelity: 'actual-tab-capture'; captureMethod: 'authorized-tab-track'; surfaceProof: 'same-origin-page-handle';
    coverage: 'complete'; missing: string[]; redactedRects: number[][];
    acquisitionBasis: { schema: 'installed-studio-tab-v1'; captureSessionId: string; surfaceOrigin: string; pageHandle: string;
      structuralReportSeq: number; viewport: WorkspaceViewport; sampledBeforeMonoMs: number; sampledAfterMonoMs: number;
      mediaTimeSeconds: number | null; rootPaintInclusion: 'unknown'; rootPaintReason: 'tab-track-has-no-atomic-root-paint-manifest' } });

/** The Assistant's open (or close) of a Data item in the reader (orchestrator/data_reader.py `_message`). */
export interface ReaderRequestMessage {
  type: 'reader'; id: string; do: 'open' | 'close'; session: string;
  workspacePin: { client: Record<string, unknown>; id: string; rootId: string; kind: string; lifetimeGeneration: string | number; sourceGeneration: number };
  item: { resource: { day: string; id: string; version: number | null }; title?: string };
  entry?: Record<string, unknown>;
}
export type ServerMessage =
  // Transport correlation only; native commands retain their server-issued authority.
  | { type: 'presentation-admitted'; requestId: string; executionRequestId: string; client: WorkspaceReady['client']; appRunId: string }
  | { type: 'workspace-tabs'; id:string; session:string; action:Record<string,unknown>; workspacePin:{client:Record<string,unknown>;id:string;rootId:string;kind:string;lifetimeGeneration:string|number;sourceGeneration:number} }
  | { type: 'open-page'; id: string; page: string } // the agent opens a page (open_models): answered by page-opened
  // Outside agents waiting to connect and those connected (ADR 0026; agent-connections.ts draws the confirm and the list).
  | AgentConnectionsMessage
  // The walkthrough's state changed outside this window (an agent ran the example through the door): reread it.
  | { type: 'onboarding-changed' }
  // The Assistant opens a Data item in the reader on the Tab you are on, or closes it there (ticket 66, D6;
  // orchestrator/data_reader.py): done once the screen report shows it. `entry` is the item as Data lists it.
  | ReaderRequestMessage
  // How an Actions run the user stopped really ended, once its work ended after its "stopping" reply (commands.action):
  // sent to the studio that started it, for that exact run.
  | { type: 'action-settled'; run: string; outcome: 'stopped' | 'completed' | 'failed'; text: string }
  // Where a This Tab run on the Stage's still has its result (S5a, population.Layer._settle_specimen): 'specimen', its
  // outlines verified there (kept in Data, no window), or 'tab', its masks window placed on its Tab.
  | { type: 'action-placed'; run: string; placed: 'specimen' | 'tab' }
  | { type: 'workspace-study-delay'; run: string | null; session: string; milliseconds: number; execution: string; capture: number | null; source: string | null; phase: 'captured' }
  | WorkspaceReady
  | ShowingState
  | TrailsMessage
  | PopulationWire
  | LayerWire
  | MemberSelected
  | MotionTracksWire // motion tracks kept as evidence (motiontracks.ts), sent when one is shown
  | TracksLayerWire // the tracks shown over the live view, and whether their place is verified
  | { type: 'execution'; entry: ExecutionReference }
  | { type: 'execution-list'; enabled: boolean; entries: ExecutionReference[]; generation: number }
  | { type: 'hello'; health: Health }
  | { type: 'scene'; space: Space; version: number; objects: SceneObject[]; selected: string | null; context: Record<string, unknown> }
  | { type: 'candidates'; space: Space; frameId: number; items: Candidate[] }
  | ResultMessage
  | ChatLine
  | { type: 'settings'; settings: Settings } // values only; what can be chosen is in CapabilitiesWire
  | CapabilitiesWire
  | ScaleState
  | CoachState
  | { type: 'thinking'; on: boolean; phase?: 'answering' } // answering: its steps have run, its answer is being written
  | SessionState
  | SessionsList
  | SessionCleared // actual App reset: a new session, or a switch to another one
  | SourcesMessage // the open session's sources changed (ADR 0022)
  | DayState
  | ThreadReplay
  | ChatAction
  | ChatAnswerStatus
  | AppNotice
  | ActivityEvent // a line of Activity (activity.ts): a request's record, a click, a studio action, a setting, a load
  | { type: 'hint'; text: string; tone?: 'error' | '' } // a passing status line under the view, not a chat post
  | ({ type: 'arena' } & ArenaState)
  | { type: 'trial'; trial: Trial }
  | { type: 'verdict'; trialId: string; winner: string; reveal: Record<string, Reveal> }
  | MagnifierState
  | PresenterState
  | SlideMapState
  | DatasetState
  | RigState
  | { type: 'notes'; full?: Record<string, NoteEntry>; upsert?: Record<string, NoteEntry> }
  | { type: 'research'; session: ResearchSession; spentToday: number; estimatedToday?: number }
  | { type: 'research-list'; sessions: ResearchSession[]; spentToday: number; estimatedToday?: number }
  | PresentMessage // a change of something on screen, done once a screen report shows it (screen.ts)
  | { type: 'screen-look'; id: string; schema?: never } // legacy approximate composition only
  | WorkspaceCaptureRequest
  | { type: 'screen-ask' }; // a wait for something on screen: report now, even if nothing changed

/** Everything the app can do (contract/capabilities.json, orchestrator/capabilities.py; ADR 0007):
 * the Capabilities page draws it, the agent's capabilities tool reads `agentText`. */
export type Actor = 'you' | 'agent' | 'auto' | 'researcher';
export type VerdictState = 'works' | 'broken' | 'not-yet';
export interface Verdict { id: string; state: VerdictState; note: string; at: string; build: string; by: string; setup?: { edition: string; options: Record<string, string | null> } }
export interface Capability {
  id: string;
  family: string;
  actor: Actor;
  title: string;
  what: string;
  how: { tool?: string; args?: Record<string, unknown>; command?: string; also?: string[]; control?: string;
    process?: string; deliverable?: string; say?: string; gesture?: string; key?: string; href?: string };
  roles?: string[];
  fallbacks?: Fallback[];
  viaFallback?: string; // available only through a fallback, and why its own model cannot run
  parts?: string[]; // the code behind it (no model choice): the camera bridge, the slide tracker...
  needs?: Record<string, string>;
  pair?: string;
  evidence?: string[];
  media?: string;
  available: boolean;
  why: string;
  verdict: Verdict | null;
  /** A capability the catalog added (the one door, orchestrator/kinds.py; ADR 0024): made from its declaration, with
   * its catalog key and its parameters as form fields (actions-panel.ts Field). */
  generated?: boolean;
  catalog?: string;
  /** What runs it, in a line (capabilities.RUNS_ON): its makers' code in its own environment, or ImageJ in the app's Fiji. */
  runsOn?: string;
  fields?: {
    key: string; label: string; kind: 'text' | 'number' | 'boolean' | 'choice'; required: boolean; hint: string;
    choices: string[]; said: Record<string, string>; numeric: boolean; initial?: unknown;
    min?: number; max?: number; step?: number; unit?: string; advanced?: boolean;
  }[];
}
/** Where a model runs (capabilities.runs): a hosted service, weights on this Mac, both, or no model. */
export type Runs = 'cloud' | 'mac' | 'both' | '';
/** One thing left before a model runs here, in the user's words (orchestrator/readiness.py Step). */
export interface ReadinessStep {
  kind: 'key' | 'licence' | 'download' | 'install' | 'weights' | 'edition' | 'check' | 'activate' | 'trust' | 'workflow'; says: string; how?: string; url?: string;
  service?: string; // a key step's service (catalog/services): the Add key sheet saves it
  model?: string; // a download step's catalog model (the one whose files it gets): the Download button sends it
  bytes?: number; // a download step's bytes still to get (Turn on's sheet adds them up and weighs them against the disk)
  onAgain?: boolean; // an install step that only turns a kept, proven version back on: nothing is downloaded or installed
}
/** One environment the app made this run for a model (orchestrator/envs.py Installs.wire). */
export interface InstallRow {
  prepareOnly?: boolean; // dependency preparation for private review; never operation activation
  model: string; name: string; state: 'queued' | 'installing' | 'done' | 'failed' | 'cancelled'; says: string; packages?: number;
  by?: string; folder?: string; site?: { site: string; url: string }; // Fiji: the update site this generation adds
}

/** One download of a model's files this run (orchestrator/downloads.py Downloads.wire). */
export interface DownloadRow {
  model: string; name: string; state: 'queued' | 'downloading' | 'checking' | 'done' | 'failed' | 'cancelled';
  got: number; total: number; says: string; step?: string; url?: string;
}
/** One service's key as the studio may know it (key_admin.py facts): where it comes from, never its value. */
export interface KeyFacts {
  service: string; name: string; short: string; getKey: string; enables: string; present: boolean;
  source: 'environment' | 'keychain' | 'file' | null; sourceSays: string; canSave: boolean;
  checked?: { outcome: 'accepted' | 'unchecked'; at: string };
  /** The keys file is there but this app may not read it (macOS privacy, F-L8-3): why, and how to fix it. */
  fileBlocked?: string;
}
/** One fact behind a model's status, with its reference (catalog_view.py facts): its key and where it comes from, its
 * files and where they are, its code, this edition, its last check. `ok` null: nothing to judge (a licence page). */
/** Where catalog entries come from besides the app's own (channels.py): the reviewed catalog, or a community one. */
export interface ChannelRow {
  name: string; url: string; commit: string; reviewed: boolean; trusted: boolean; checked: string; entries: number;
  label?: string; source?: ChannelSource;
}

/** Where a channel's entries come from (channels.py `source`): the reviewed catalog, a file someone shared, a repository. */
export type ChannelSource = 'reviewed' | 'shared-file' | 'git' | '';
/** The last "Check for catalog updates": running, done (with what it added) or failed. */
export interface ChannelUpdate { state: 'running' | 'done' | 'failed'; says: string; }

export interface ModelFact {
  kind: 'key' | 'licence' | 'files' | 'code' | 'weights' | 'edition' | 'check' | 'entry' | 'runs' | 'on' | 'classes';
  ok: boolean | null; says: string; ref?: string; service?: string; model?: string;
  observed?: 'access' | 'key'; // asked of the outside world (observed.py): Check again asks again
  confirm?: 'access'; // only the user can say it (a licence on a page no probe reads): I've accepted it
  install?: string; // a Python made by hand runs it: Install makes the catalog's pinned one (envs.py)
  prepare?: string; // prepare scientific candidate dependencies without qualifying its workflow
  cached?: boolean; // files need verified publication from a local cache through the download command
  rollback?: number; // Fiji: the earlier generation "Go back" makes current (fiji_generations.py)
  trust?: string; // a community channel whose code isn't allowed yet: Allow (channels.py)
  trustLabel?: string; // how that channel is said ("Maya Okafor's shared file")
}
/** A role a model serves, its options that run it, and whether it is the current choice there. */
export interface ModelUse { role: string; title: string; option: string; options: string[]; current: boolean; fixed: boolean }
/** One catalog entry as the Models page shows it (catalog_view.rows). */
export interface ToolAbility { id: string; title: string; what: string; actor: string; say: string; preview?: string; role: string; option: string; current: boolean }
export interface ToolFace { state: string; says: string; action: ({ kind: string; says: string; model?: string; key?: string } & Partial<ReadinessStep>) | null; also: { kind: string; says: string; model?: string; key?: string }[] }
export interface ToolSetup { ready?: boolean; recipe?: string; current?: string; role: string; title: string; option: string; plan: NonNullable<import('./capabilityshelf').ShelfCard['turnOn']> | null; recheck: boolean; canTry: boolean; check?: ModelCheck | null }
export interface ToolTrial { role: string; option: string; title: string; try: { command: string; job?: string; option?: string; model?: string } }
export interface ModelRow extends Readiness {
  id: string; name: string; runs: string; where: string; what: string; kind: string; version: string;
  credit: { authors?: string; link?: string; paper?: string; cite?: string }; link: string; licence: string; tier: string;
  mine: boolean; channel?: string; channelSource?: ChannelSource; channelLabel?: string; draftedByAi?: boolean; group: 'needs-you' | 'ready' | 'available' | 'unavailable'; inUse: boolean; chosen: boolean;
  facts: ModelFact[]; usedFor: ModelUse[]; size: number; files: { path: string; bytes: number }[]; folder: string;
  demonstration?: import('./capabilityshelf').ShelfCard['demonstration']; setups?: ToolSetup[]; abilities?: ToolAbility[]; face?: ToolFace; tries?: ToolTrial[];
  installFolder?: string; // installer-owned destination; Fiji generations are created inside this folder
  revision: string; download?: DownloadRow; checking?: boolean; // being asked of the outside world now
  execution?: { action: 'compare' | 'choose-comparison-input' | 'review-bundle' | 'unsupported'; why: string; job?: string };
  shareable?: boolean; // its own adapter: it travels as a share file (share.py)
  memberKinds?: (NonNullable<ModelRow['newKind']> & { key: string; name: string; what: string })[];
  newKind?: { operation: string; input: string; output: string; gives: string; proved: boolean; activeVersion?: string; proof?: string;
    typed?: boolean; inputs?: { id: string; type: string }[];
    /** What it makes and says about it, in its folder's own words (its output's label and tooltip;
     * catalog_view._kind_description). */
    made?: string; madeNote?: string;
    /** Its declared settings (its non-picture inputs as form fields, catalog_view.py, S11): shown on its run page before
     * it is set up, disabled there until it is. */
    fields?: import('./parameter-fields').Field[] };  // typed: each input chosen from saved resources, bound at Run // the active adapter’s actual input/output and functional proof (kinds.py); `proof`: another runner's own words for it (Fiji's parity)
  versions?: { generation: string; version: string; at: string; inUse: boolean; chosen: boolean }[]; // proven, newest first
  env?: { on: boolean; installed: boolean }; // its own adapter (env_runner): running as a choice; any version made here (Turn off, Remove)
}
/** Whether a model can run here (orchestrator/readiness.py; ADR 0019): one status, the next step and the rest. A model
 * that downloads when first used is "needs-setup" and still available. */
export interface Readiness {
  status: 'ready' | 'needs-setup' | 'unavailable' | 'broken';
  word: string; // "Ready", "Needs your Google Gemini API key", "Download 3.5 GB"
  group: string; // "Ready" | "Needs setup" | "Unavailable here"
  next: ReadinessStep | null;
  steps: ReadinessStep[];
}
/** How many capabilities can run here, and what each missing key would add (capabilities.wire setup.counts). */
export interface SetupCounts { works: number; total: number; unlock: { key: string; service: string; getKey: string; adds: number }[] }
export interface RoleOption {
  id: string; label: string; note: string; tier?: string; licence?: string; available: boolean; why?: string; runs?: Runs;
  readiness?: Readiness;
  accepts?: string[];
  sources?: { title: string; url: string; licence: string }[];
  models?: string[]; // the models this choice runs (a behaviour setting's too: "EdgeTAM outline model")
  said?: string; // its name with where it runs, as the app says it (names.said: "EdgeTAM (on this Mac)")
  check?: ModelCheck; // the app's own last check of this model here (model_checks.py), absent when never checked
}
/** The app's own last check of a model (model_checks.py; "Check now", make capability-check): beside, never instead of,
 * the user's verdicts. `earlierBuild`: made on another build than the one running. */
export type ModelCheckState = 'works' | 'broken' | 'not-installed' | 'needs-key' | 'not-in-edition' | 'not-checked';
export interface ModelCheck {
  state: ModelCheckState; why: string; at: string; ms?: number; loadMs?: number; detail?: string; costUsd?: number;
  by?: string; earlierBuild?: string;
}
/** A model fixed in the code: the one it names first, or the fallback the code takes when that cannot run. */
export interface FixedModel { label: string; value: string; runs: Runs; available: boolean; why?: string; when?: string; active?: boolean; check?: ModelCheck; readiness?: Readiness }
export interface Role {
  id: string;
  kind: 'model' | 'method' | 'policy';
  title: string;
  purpose: string;
  hint?: string;
  fixed?: boolean;
  current: string | null;
  default?: string | null;
  defaultReason?: string | null; // absent means no declared selection rationale
  runs?: Runs; // a fixed role's
  label?: string; // a fixed role's model in use, by name ("SAM 3.1 (cloud)"); `current` is the code's value
  primary?: FixedModel; // a fixed role's first model
  fallback?: FixedModel | null; // ...and the one the code takes when that cannot run (`active` when it is in use)
  check?: ModelCheck; // a fixed role's model in use: its last check
  tier?: string;
  licence?: string;
  options: RoleOption[];
  powers: string[];
  fallbackFor?: string[]; // capabilities it takes over when their own model cannot run
  operation?: { id: string; returns: string; accepts: Record<string, string[]> };
  agentCanChange: string[];
}
/** What a capability falls back to when its own model cannot run, as the code does it. */
export interface Fallback { when: string; does: string; roles: string[] }
export interface Preference { id: string; title: string; purpose: string; value: unknown; agentCanChange: string[] }
/** A part of the code a capability runs on, with no model to choose (the camera bridge, the slide tracker). */
export interface Part { id: string; title: string; kind?: 'algorithm' | 'app'; purpose: string; source: string; powers: string[] }
export interface CapabilitiesWire {
  type: 'capabilities';
  families: { id: string; title: string; what?: string }[];
  actors: Record<Actor, string>;
  capabilities: Capability[];
  roles: Role[];
  parts?: Part[];
  preferences: Preference[];
  pending: number; // verdicts not yet in git (make verdicts)
  build: string;
  agentText: string;
  setup?: { edition: string; options: Record<string, string | null>; counts?: SetupCounts; keys?: Record<string, KeyFacts>;
    downloads?: DownloadRow[]; installs?: InstallRow[];
    maintenance?: Record<string, import('./maintenance-status').EntryUpdate>; // each maintained entry's update state, and each built-in's line (maintenance/status.py)
    disk?: { freeBytes?: number; spareBytes?: number }; // the models folder's disk (downloads.disk): Turn on's sheet says whether a plan fits
    channels?: ChannelRow[]; channelUpdate?: ChannelUpdate; catalogProblems?: { entry: string; problems: string[]; incompatible?: boolean }[];
    loose?: { path: string; name: string; bytes: number }[]; // files dropped straight into your folder (mine.py)
    mineFolder?: string; // where your own entries live on this instance ("~/MicroscopeMind/catalog/mine/models"; manifests.mine_root)
    turning?: Record<string, import('./capabilityshelf').Turning>; // exact owned job and entry setup intents
    news?: import('./capabilityshelf').News; shelf?: import('./capabilityshelf').Shelf;
    ownTrials?: OwnTrial[];
    drafts?: { trial?: string; job?: string; comparison?: OwnComparison; id: string; name?: string; link: string; state: string; tries: number; last: { ok?: boolean; count?: number; error?: string } | null; model: string;
      input?: { evidenceClass?: string; identity?: string; sha256?: string }; terminal?: { status: string };
      review?: DraftReview | null; reviewed?: { digest: string; at: string } | null }[] }; // C1 New for you, C2 by job
  models?: ModelRow[]; // the Models page: every catalog entry, why it is on or off (orchestrator/catalog_view.py)
  usageCoverage?: { complete: boolean; reason: string };
  checking?: { by: string; done: number; of: number; now: string }; // a model check under way ("Check now")
}

/** What Use it would admit from an AI draft, to read first (orchestrator/drafts.py `review`; ticket 62 L13). */
export interface DraftReview {
  unavailable?: string; busy?: boolean; digest?: string; source?: string; adapter?: string; requirements?: string[]; packages?: string[];
  files?: { path: string; bytes: number; sha256: string; url?: string }[]; imports?: string[];
  checks?: { blocks: boolean; says: string }[]; facts?: string[]; says?: string; scanned?: string[]; byHash?: string[];
  tries?: { at?: string; ok?: boolean; count?: number; error?: string }[];
}

/** A description of one kind of thing (orchestrator/enrich.py). */
export interface OwnComparison {
  inputSha256: string;
  candidate: { says: string; preview?: string; count?: number | null; ms?: number; text?: string; rows?: number | null };
  baseline: { says: string; available: boolean; preview?: string; count?: number | null; ms?: number; text?: string; rows?: number | null };
}
export interface OwnTrial { id: string; job: string; model: string; name?: string; link?: string; needsAdapter?: boolean; comparison?: OwnComparison; detail?: string /* what tells two of one name apart */ }

export interface NoteEntry {
  key: string;
  label: string;
  status: 'pending' | 'ready' | 'failed' | 'unavailable';
  rid?: string;
  description?: {
    title: string;
    summary: string;
    details: string[];
    look_for: string;
    confidence: number | null;
    sources: string[];
  };
  refs?: { url: string; page: string; title: string; credit: string }[];
}

export interface Correction {
  id: string;
  object_id: string | null;
  label: string; // what it was called when suggested
  to_label: string;
  reason: string;
  confidence: number | null;
  status: 'proposed' | 'accepted' | 'dismissed' | string;
  from: string;
}

export interface ResearchOutput {
  job?: string;
  format?: 'blocks' | 'html' | string;
  contentKind?: 'report' | 'lesson' | string;
  content?: PresentationPlan | ResearchLesson;
  model?: string;
  conflict?: boolean;
  findingRevision?: { version: number; digest: string; complete: boolean };
  kind: 'image' | 'video' | 'html' | string;
  path: string;
  url: string;
  caption: string;
  generated: boolean;
  altered: boolean;
  // Versions (orchestrator/versions.py): a revision is a new output that replaces the old one.
  id?: string;
  version?: number;
  replaces?: string | null;
  change?: string | null;
  current?: boolean;
}

export interface PresentationPlan {
  layout: 'report' | 'comparison' | 'briefing';
  blocks: { ref: string }[];
  /** How it looks, the producer's choice (research_worker/presentation.py STYLE). */
  style?: PresentationStyle;
}

export interface PresentationStyle {
  theme?: 'graphite' | 'paper' | 'contrast' | 'accent';
  density?: 'compact' | 'comfortable' | 'spacious';
  pictures?: 'inline' | 'lead' | 'gallery';
  emphasis?: string[];
}

export interface ResearchLesson {
  kind: 'lesson';
  title: string;
  key_ideas: string[];
  steps: {id:string; prompt:string; answer:string; hint:string; kind?:string}[];
}

export interface ResearchChart {
  title: string;
  transformation: 'identity';
  selectionRule: string;
  rows: { label: string; value: number; unit: string | null; denominator: number | null;
    exclusions: string | unknown[] | Record<string, unknown> | null;
    source: { day: string; id: string; version: number | null; contentRevision: string;
      valuePath: string; unitPath?: string; denominatorPath?: string; exclusionsPath?: string } }[];
}

export interface PresentationJob {
  id: string;
  state: 'queued' | 'preparing' | 'ready' | 'failed' | 'unavailable' | 'over_budget' | 'cancelled' | 'interrupted' | 'conflict';
  findingRevision: { version: number; digest: string; complete: boolean };
  model?: string;
  output?: string;
  cost: number;
  estimatedCost: number;
  error?: string;
  persistenceError?: string;
  pendingOutput?: ResearchOutput;
  created: number;
  dispatchedAt?: number;
  finished?: number;
  deadlineSeconds?: number;
}

/** Immutable artifact context captured by the research composer at Send. */
export interface ResearchTarget {
  presentationBase?: string | null;
  day: string;
  rid: string;
  findingVersion?: number;
  findingDigest?: string;
  outputId?: string;
  outputVersion?: number;
  anchor?: string;
}

/** Indexes address one frame only within this exact saved scientific finding. */
export interface ResearchCaptureTarget {
  day: string; rid: string; findingVersion: number; findingDigest: string;
  sequence: number; picture: number;
}

/** One thing in the result viewer: an output (by id) or the written result ("summary"),
 * with the label its tab shows ("Lesson v2", "Video", "What it found"). */
export interface ResultItem {
  work?: { researcherId: string; resource: { day: string; id: string; version: number | null } };
  kind: 'html' | 'video' | 'image' | 'reference' | 'summary' | string;
  label: string;
  id: string | null;
  /** What kind of thing it is, and that in a line shown above it (orchestrator/media.py, presenter.items). */
  media?: MediaKind;
  line?: string;
  /** A reference photo: which of result.refs it is. */
  ref?: number;
}

/** A record a researcher looked at in the session's index (Session.working; the mission card's working set). */
export interface WorkingRecord {
  id: string;
  how: string; // "read" | "picture N"
  title: string;
  kind: string;
  at: number;
  version?: number | null;
  now?: number; // its revision now, when corrected since it was read
  run?: number;
}

/** One researcher (orchestrator/research/manager.py Session.wire). */
export interface ResearchSession {
  day?: string;
  presentationJobs?: PresentationJob[];
  findingRevisions?: { version: number; digest: string; at: number; complete: boolean }[];
  interactionJobs?: { id: string; state: string; created: number; request: { text: string; kind?: 'note' | 'followup' | 'evidence' | string; selection?: ResearchTarget };
    outcome?: { ok: boolean; answer?: string; error?: string } }[];
  mission?: { status: string; slots: number; ledger: { n: number; outcome: string }[];
    acquisition?: { seconds: number }; whyEnded?: string };
  rid: string;
  n: number;
  name: string;
  brief: string;
  deliverable: string | null;
  effort: 'instant' | 'describe' | 'quick' | 'standard' | 'deep';
  engine: string;
  purpose: 'research' | 'description' | 'brain';
  label: string;
  objectId: string | null;
  status: 'queued' | 'starting' | 'running' | 'revising' | 'done' | 'partial' | 'failed' | 'timeout' | 'over_budget' | 'cancelled' | 'interrupted';
  step: string;
  stepAt?: number; // service-observed time of the latest worker step; older records may omit it
  spent: number;
  cap: number;
  seconds: number;
  created: number;
  started: number | null;
  finished: number | null;
  snapshot: string;
  outputs: ResearchOutput[];
  /** The result viewer's tabs, in the server's order (orchestrator/presenter.py items): the
   * presenter's index counts these. */
  items: ResultItem[];
  findings: { kind: string; text: string; sources: string[] }[];
  findingCount?: number; // all of them (findings lists the last 20)
  corrections: Correction[];
  history: { brief?: string; status: string; result?: ResearchSession['result']; mode?: 'answer' | 'revise';
    at?: number; change?: string; kind?: string; version?: number | null }[];
  spentEstimated?: number; // calls cut off by the time limit (billed; estimated)
  answering?: string | null; // a follow-up question it is answering now
  revising?: { target: string; kind: string; change: string } | null; // a new version being made
  notes?: number; // notes the user sent it while it worked
  thread?: string | null;
  /** The request that started it, or last continued it (the server's own request id: the same as its start step's
   * `requestId` on the answer's steps), and that request's turn when it was typed; null for a voice or manual start
   * (research/manager.py Session.wire, S1). The chat matches its card to the ask by these. */
  turnId?: number | null;
  requestId?: string | null;
  canFollowUp: boolean;
  restored?: boolean; // finished before the app last restarted
  sitting?: string; // the view session it belongs to
  specimen?: string; // what was in view when it started
  plan?: { searches: string[]; sources: string }; // what a deep run said it would look up first
  /** What it looked at in the session's index (the mission card's working set), in the index's own words: the
   * revision it read, and `now` when the record has been corrected since; the run (0: its result, then follow-ups). */
  working?: WorkingRecord[];
  workingDropped?: number; // older records it looked at, let go (the newest are kept)
  /** What it had the app make (hands.py's own record of its runs). */
  ran?: { id: string; operation: string; run?: number; at: number; title?: string; kind?: string; version?: number; now?: number;
    /** A catalog capability's key, and how many things it made, when the app recorded them (hands.py `made["ran"]`). */
    capability?: string; count?: number }[];
  /** Evidence it asked the user for (request_evidence): answered by the user's next note. */
  requests?: { id: string; run?: number; what: string; why: string; seconds?: number | null; at: number; keepGoing?: boolean;
    status: 'asked' | 'answered' | 'unanswered'; answer?: string; answeredAt?: number }[];
  result: {
    presentation?: { id: string; version: number; model?: string; plan: PresentationPlan };
    report?: { methods: string; observations: string; measurements: string; limitations: string };
    findingRevision?: { version: number; digest: string; complete: boolean };
    title: string;
    summary: string;
    one_liner: string;
    significance: string;
    details: string[];
    tables?: { title: string; columns: string[]; rows: string[][] }[];
    charts?: ResearchChart[];
    captures?: ResearchCapture[];
    look_for: string;
    confidence: number | null;
    sources: string[];
    refs: { url: string; page: string; title: string; credit: string }[];
    /** Points it said came from a page, checked by the app against that page's text after it
     * finished (research_worker/claims.py): found false is "not found in its sources", null
     * that the page could not be read. The points are its words, unchanged. */
    claims?: { point: string; source: string; found: boolean | null; missing?: string[]; why?: string }[];
    /** What it says it cannot settle from what it saw and found, and the questions it suggests next
     * (the partner §2.2): its words, shown as they are. */
    limitations?: string[];
    next_questions?: string[];
    /** The session's records it cites (index ids), looked up in the session's index before it was announced
     * (research_worker/evidence.py, ADR 0015): found false is "not in this session", null "not checked". */
    evidence?: { id: string; found: boolean | null; title?: string | null; kind?: string | null; time?: string | number | null; why?: string }[];
    session_claims?: { point: string; evidence: string[]; found?: boolean | null; missing?: string[] }[];
  } | null;
}



export interface ScoreOption {
  option: string;
  label: string;
  trials: number;
  wins: number;
  ties: number;
  none: number;
  failed: number;
  medianMs: number | null;
  recommended: boolean;
}

export interface Scores {
  votes: number;
  kinds: { kind: ArenaKind; votes: number; options: ScoreOption[]; recommended: string | null; minTrials: number }[];
}

export type ArenaKind = 'outline' | 'ask';

export interface RetainedResultRef {
  day: string;
  id: string;
  version: number | null;
  picture: number | null;
  title: string;
  kind?: string;
}

export interface ToolRun {
  /** Exact saved result; legacy partial addresses are not reopenable cards. */
  resource?: RetainedResultRef;
  /** Recorded event time, never a browser arrival estimate. */
  at?: number;
  tool: string;
  ok: boolean | null; // null: still running when the answer was shown
  summary: string;
  ms: number;
  /** Original occurrence assigned by the server before queueing; absent on old servers. */
  requestId?: string;
  actionId?: string;
  /** A refusal's plain next step (also in its summary). */
  next?: string;
  /** The models that worked on it, named when it ended (activity.models_of); none of an unvoted blind trial's. */
  models?: StepModel[];
  /** What a step still running is waiting on (loads.py): "loading micro-sam (light microscopy) on this Mac, usually about 2 min". */
  progress?: string;
  /** What the step made, by identity (brain/registry.py ToolOutput): a started researcher is a `resource` whose reference
   * is its `research-run` (its researcherId), so the answer's researcher cards reconcile with the ones listed earlier. */
  outputs?: ToolRunOutput[];
}

/** One thing a step made, as its step's wire says it: its kind and identity (bounded, primitive). */
export type ToolRunOutput = { kind: string; resource?: unknown; source?: unknown; id?: string; instanceId?: string };

/** A researcher's run as a step's output names it (research/manager.py attempt_ref). */
export interface ResearchRunRef { kind: 'research-run'; researcherId: string; day: string; runId: string; run: number }

export interface ArenaState {
  enabled: boolean;
  arms: Record<ArenaKind, string[]>;
  choices: Record<ArenaKind, { id: string; label: string }[]>;
  trial: Trial | null;
}

export interface TrialArm {
  letter: string;
  done: boolean;
  failed: boolean;
  objectIds: string[];
  answer: string;
  labels: string[];
}

export interface Trial {
  id: string;
  kind: ArenaKind;
  input: { x?: number; y?: number; question?: string };
  frameId: number;
  arms: TrialArm[];
  voted: boolean;
}

export interface Reveal {
  option: string;
  label: string;
  ms: number | null;
  error: string | null;
}

/** Help getting a good view (orchestrator/coach.py): a focus meter while the knob turns (focus is
 * the share of the sweep's sharpest, null when not focusing) and advice for a bad view. The cue
 * 'phone': the eyepiece disc changed size during the sweep, so it is the phone's own lens
 * refocusing, not the knob (option COACH "lens"). */
export interface CoachState {
  type: 'coach';
  focus: number | null;
  cue: 'sharper' | 'passed' | 'phone' | null;
  advice: string | null;
}

/** How big things are (orchestrator/scale.py): the objective in use and microns per camera pixel. */
export interface ScaleState {
  type: 'scale';
  objective: number | null;
  fieldNumber: number;
  measured: number[]; // objectives calibrated with a known length
  umPerPx: number | null;
  fieldUm: number | null;
  source: 'measured' | 'estimate' | null;
}

// Every option group the server has (orchestrator/options.py GROUPS; services/tests/test_readiness.py keeps them in step).
export type OptionGroup = 'vision' | 'pointing' | 'outline' | 'voice' | 'brain' | 'dissect' | 'research' | 'presentation' | 'image' | 'sight' | 'fast' | 'cells' | 'quality' | 'speculate' | 'reflex' | 'early_look_typed' | 'answer_check' | 'completion' | 'sampling' | 'style' | 'menu' | 'outline_check' | 'track' | 'voicelink' | 'admit' | 'taught' | 'lesson_check' | 'research_pacing' | 'claim_check' | 'reference_search' | 'outline_repair' | 'seen' | 'coach' | 'closeup' | 'dust' | 'embed' | 'zeroshot' | 'outcome_lines' | 'session_context' | 'row_provenance' | 'orb_every' | 'naming' | 'library_looks';

export interface ModelOption {
  id: string;
  label: string;
  note: string;
  available: boolean;
  why?: string; // when unavailable: a missing key, or not in this edition (orchestrator/licences.py)
  tier?: 'open' | 'terms' | 'noncommercial';
  licence?: string;
  readiness?: Readiness;
}

export interface Settings {
  vision: string;
  pointing: string;
  outline: string;
  voice: string;
  brain: string;
  dissect: string;
  research: string;
  presentation?: string;
  image: string;
  sight: string;
  fast: string;
  cells: string;
  quality: string;
  speculate?: string;
  style?: string;
  outlineColor?: string;
  menu?: string;
  outline_check?: string;
  track?: string;
  voicelink?: string;
  taught?: string; // matching the labels the user teaches: "on" | "off"
  lesson_check?: string; // lessons' steps checked for needing the picture (research_worker/lesson_check.py)
  seen?: string; // telling naming the outline named earlier today most like it (orchestrator/seen.py): "on" | "off"
  coach?: string; // whether the focus meter tells the phone's own refocusing from the knob (COACH)
  closeup?: string; // close-ups when naming a click: "single" | "naming" | "naming+answers" (orchestrator/agent.py)
  dust?: string; // telling the models where dust on the optics is: "tell" | "filter" | "off" (orchestrator/dust.py)
  embed?: string; // the picture model of your labels and "seen before today": "dinov2-small" | "bioclip-2" (gpu_worker/embed.py)
  admit?: string; // how an explicit request for big research is recognised: "quote" | "keywords" (research/policy.py)
  zeroshot?: string; // MedSigLIP's zero-shot names told to naming a click: "off" | "medsiglip-subject" | "medsiglip-always" (orchestrator/zeroshot.py)
  outcome_lines?: string; // how each model has done here, pushed into the brain's prompt: "off" (looked up) | "on" (orchestrator/outcomes.py, K3)
  session_context?: string; // the index entries the conversation names, pushed capped: "off" (pulled) | "on" (brain/context.py, K3)
  row_provenance?: string; // which models made each outline, in the brain's rows: "off" (pulled with resources) | "on" (brain/context.py)
  researchCap: number | null; // USD a day for all researchers; null: no limit (the default)
  quieter: boolean;
  userLeads: boolean;
  level?: 'simple' | 'technical'; // how it explains (orchestrator/learner.py), kept for the day
  hintsFirst?: boolean;
  compare: string;
}

export interface Frame {
  header: FrameHeader;
  bitmap: ImageBitmap;
}

const MAGIC = 'MMF1';

export async function decodeFrame(data: ArrayBuffer): Promise<Frame | null> {
  const bytes = new Uint8Array(data);
  if (String.fromCharCode(...bytes.subarray(0, 4)) !== MAGIC) return null;
  const length = new DataView(data).getUint32(4);
  const header = JSON.parse(new TextDecoder().decode(bytes.subarray(8, 8 + length))) as FrameHeader;
  const jpeg = new Blob([bytes.subarray(8 + length)], { type: 'image/jpeg' });
  const bitmap = await createImageBitmap(jpeg);
  return { header, bitmap };
}

// --- what the studio sends ------------------------------------------------------------------

type Id = string | null;

/** Every command the studio sends, one per kind the server handles (commands.py). */
export type ScientificAskInput = { reference: { kind: 'array'; session: string; id: string; revision: number; component: string }; digest: string; tab: string };

export type ClientCommand =
  | { type: 'onboarding'; action: 'status' | 'start' | 'restart' | 'analyze' | 'measurements' | 'finish' | 'dismiss'; session?: string; result?: { id: string; revision: number } }
  | { type: 'onboarding-models'; action: 'status' | 'not-now' | 'download'; models?: string[] } // the small-models offer's answer (starter-models.ts)
  | WorkspaceHello
  // asking and pointing
  | { type: 'ask'; text: string; scientificInput?: ScientificAskInput; capabilityTrial?: string; frameId?: number; objectId?: string } // the frame on screen when it was asked; the outline it is about (Look deeper)
  // A click outlines the thing at (x, y); a box dragged on the view (frame px) outlines what is inside it.
  | { type: 'click'; capabilityTrial?: string; frameId: number; x: number; y: number; identify?: boolean }
  | { type: 'click'; capabilityTrial?: string; frameId: number; box: [number, number, number, number]; identify?: boolean }
  // settings and the blind A/B/C arena
  | { type: 'settings'; settings: Partial<Settings> }
  | { type: 'execution-recording'; enabled: boolean } // UI diagnostics, outside model settings/context
  | { type: 'operation-retry'; receiptId: string }
  | { type: 'operation-retry'; actionId: string }
  | { type: 'validate'; id: string; state: VerdictState; note?: string } // the user's verdict on a capability
  | { type: 'capability-check' } // Check now: every model on this Mac runs once (model_checks.py)
  | { type: 'model-download'; model: string } // a model's pinned files into the models folder (downloads.py)
  | { type: 'model-reveal'; model: string } // its folder in the Finder (the Models page); '' the models folder itself
  | { type:'workspace-tabs-result';id:string;session:string;workspacePin:Record<string,unknown>;result:Record<string,unknown> }
  | { type: 'page-opened'; id: string; page: string; shown: boolean; visible: boolean } // the answer to open-page
  | { type: 'model-turn-on'; model: string; trial?: string } // one entry's Turn it on (an added link, a new one); trial: Set up for comparison's picture
  | { type: 'model-publish-prepare'; model: string; destination?: 'review' | 'community' }
  | { type: 'made-with-acquire'; asset: string; key: string }
  | { type: 'model-build'; link: string; trial?: string } // C4: a researcher drafts an adapter, tried inside the sandbox
  | { type: 'model-own-compare'; trial: string; model?: string; draft?: string }
  | { type: 'model-own-use'; trial: string; model: string }
  | { type: 'model-draft-use'; draft: string; reviewed: string } // a draft that ran, as reviewed (its digest), becomes one of your models
  | { type: 'model-draft-community'; draft: string }
  | { type: 'model-publish-submit'; model: string; package: string; signoff: boolean } // sent, on the user's sign-off
  | { type: 'github-signin'; do: 'status' | 'start' | 'poll' | 'signout'; id?: string } // one-click GitHub sign-in
  | { type: 'capability-run'; model: string; session: string; input: string; volume?: string; resource?: import('./modelspage').RetainedInput['reference']; params?: Record<string, unknown>; run?: string; workspaceOrigin?: import('./workspace-views-model').WorkspaceOrigin }
  | { type: 'model-version'; model: string; generation: string | null } // go back to a proven version, or the newest
  | { type: 'maintenance-policy'; model: string; policy: 'manual' | 'review' | 'auto' | 'pin'; generation?: string } // ADR 0025: a person's policy
  | { type: 'maintenance-check'; model: string } // look for its update now
  | { type: 'maintenance-apply'; model: string } // the proven update runs new work
  | { type: 'maintenance-undo'; model: string } // the version before runs again
  | { type: 'maintenance-stop'; model: string } // end the update being prepared
  | { type: 'maintenance-accept'; model: string; fingerprint?: string; confirming?: boolean } // you accept what it changes
  | { type: 'maintenance-merge'; model: string } // your version onto the newest of what it came from
  | { type: 'model-report'; model: string } // Report it: the facts to read and copy, nothing sent (report.py)
  | { type: 'model-add'; source: string; name?: string; trial?: string } // a model of your own, from a link or a path (mine.py)
  | { type: 'model-activate'; model: string } // turn one of yours on: its check first, in a process of its own
  | { type: 'model-deactivate'; model: string }
  | { type: 'observe-again'; model?: string } // ask the outside world again (a licence's access, a key)
  | { type: 'observe-confirm'; model: string } // the user's word: they accepted a licence no probe can ask about
  | { type: 'model-download-cancel'; model: string }
  | { type: 'model-install'; model: string } // make a model's own Python from its lockfile (envs.py)
  | { type: 'model-prepare'; model: string }
  | { type: 'model-install-cancel'; model: string }
  | { type: 'model-uninstall'; model: string }
  | { type: 'fiji-add-site'; page: string } // turn on an extension's update site in Fiji's next generation
  | { type: 'fiji-rollback' } // the previous Fiji generation in use again
  | { type: 'catalog-update' } // the reviewed catalog at its newest commit (channels.py)
  | { type: 'catalog-news-seen'; keys?: string[] } // New for you: Got it
  | { type: 'catalog-news-dismiss'; key: string } // New for you: Not for me
  | { type: 'catalog-news-get'; key: string; commit: string; offer: string } // the displayed metadata offer
  | { type: 'capability-turn-on'; id: string; option?: string; recheck?: boolean; plan?: NonNullable<import('./capabilityshelf').ShelfCard['turnOn']> } // a job's Turn on (capability_shelf.py)
  | { type: 'capability-turn-on-cancel'; id: string; attempt: string }
  | { type: 'capability-select'; job: string; option: string; session: string; current: string; recipe: string }
  | { type: 'usage-cap'; usd: number | null }
  | { type: 'capability-remove'; id: string; revision: string }
  | { type: 'capability-try-voice'; token: string }
  | { type: 'capability-try'; id: string; option: string; empty: boolean }
  | { type: 'channel-add'; url: string; commit?: string } // a community channel, pinned at a commit
  | { type: 'channel-trust'; name: string; allowed: boolean } // allow (or no longer allow) its code (ADR 0021)
  | { type: 'channel-remove'; name: string }
  | { type: 'channel-update'; name: string } // a community channel at its newest commit (channels.update)
  | { type: 'volume-add'; path: string } // a whole-brain volume on this Mac (registrations.py)
  | { type: 'volume-describe'; id: string; session?: string; registration?: boolean; voxelUm?: number[]; orientation?: string; series?: number; channel?: number; time?: number }
  | { type: 'job-start'; volume: string } // brainreg registers it to the Allen mouse atlas
  | { type: 'job-cancel'; id: string; session?: string }
  | { type: 'job-again'; id: string }
  | { type: 'arena'; arena: { enabled?: boolean } & Partial<Record<ArenaKind, string[]>> }
  | { type: 'scores' }
  | { type: 'vote'; trialId: string; winner: string; note?: string; by?: string }
  // the view
  | { type: 'cells' }
  | { type: 'population-correct'; session: string; population: string; revision: number; change: 'remove' | 'undo'; member?: string }
  | { type: 'clear'; kind?: string }
  | { type: 'remove'; id: string }
  | { type: 'restore'; id: string } // the user took back their removal (Undo): the same outline, while the server holds it
  | { type: 'select'; id: Id }
  | { type: 'focus'; id: Id } // the pointer rested on it
  | { type: 'card-open'; id: string } // logged: do descriptions get read?
  | { type: 'reveal'; key: string } // a description is shown: fetch its reference pictures
  | { type: 'pin-note'; id: string; text: string | null }
  | { type: 'debug'; candidates: boolean }
  | { type: 'timing'; frames: number; stages: Record<string, { p50: number; p90: number }> }
  | { type: 'flag'; note?: string; frameId?: number } // a moment marked during a demo (F; Shift+F with a note)
  | { type: 'scale'; objective: number | null; fieldNumber?: number }
  | MagnifierCommand
  | { type: 'presenter'; action: 'show'; rid: string; index?: number; day?: string; outputId?: string; version?: number; savedItem?: ResultItem['work'] }
  | { type: 'presenter'; action: 'close' }
  | { type: 'showing'; action: 'stop' } // Show me's Stop, or Escape on its bar
  | { type: 'presenter'; action: 'remove' | 'restore'; rid: string }
  | { type: 'slidemap'; open: boolean } // the slide map panel (the brain's show_map)
  // sessions (the project: header chip, its menu, the sessions rail): start one, go back to one, name one, or keep
  // working here when the app suggests a new one
  | { type: 'session'; action: 'new' | 'switch' | 'rename' | 'keep'; id?: string; name?: string }
  // a new session's home covers the stage (session-home.ts): while held, the running look makes no looks and the subject
  // tracker ignores them, so nothing is written into a session before the user chose what it looks at
  | { type: 'view'; held: boolean; live?: boolean }
  // A session's sources (ADR 0022): put one on stage (`switch` false: the studio switched the feed to its test
  // footage itself, which stands for the live camera; `session`: a landing's, refused once another session is open),
  // take one out, put it back, or set an asset's own fields.
  | { type: 'source'; open: string; at?: number; paused?: boolean; switch?: boolean; session?: string }
  | { type: 'source-remove'; asset: string }
  | { type: 'source-restore'; asset: string }
  | { type: 'asset-meta'; asset: string; set: Record<string, string | number | null> }
  // All assets' Move to trash (out of the library and every session's list; the work made from it stays), and its Undo.
  | { type: 'asset-trash'; asset: string }
  | { type: 'asset-restore'; asset: string }
  // The Actions panel (actions-panel.ts): one of the agent's capabilities run by the user's hand, through its tool; `use`
  // ("Run with…") a model for that run only, {role: option}.
  // `run`: the studio's own id of this run, which its Stop names (`action-cancel`: only the window and session that started
  // it; the action's own reply then says it was stopped).
  | { type: 'action'; volume?: string; capability: string; args: Record<string, unknown>; use?: Record<string, string>; run?: string; resource?: import('./modelspage').RetainedInput['reference']; workspaceStudyDelayMs?: number; workspaceOrigin?: import('./workspace-views-model').WorkspaceOrigin }
  | { type: 'action-cancel'; run: string }
  | { type:'workspace-layout';session:string;schema?:import('./workspace-views-model').LayoutSchema;layout?:Omit<import('./workspace-views-model').WorkspaceLayout,'active'>;base?:number;action?:'remove'|'restore'|'dismiss';run?:string;tab?:string }
  | { type:'session-note';session:string;action?:'save';id?:string;base?:number;title?:string;text?:string }
  | { type: 'workspace-profile' }
  | { type: 'workspace-study-profile' }
  // conversations
  | { type: 'thread'; action: 'new'; title?: string }
  | { type: 'thread'; action: 'switch'; id: string }
  | { type: 'thread'; action: 'rename'; session?: string; id?: string; title: string }
  // datasets: on with Record and off with its Stop (only the user), and the Datasets tab
  | { type: 'dataset'; on: boolean }
  | { type: 'dataset-rename'; id: string; name: string }
  | { type: 'dataset-reveal'; id: string }
  | { type: 'sessions-reveal' } // Show in Finder: the folder every Project is saved in
  | { type: 'dataset-trash'; id: string } // after the user confirmed: moved to datasets/.trash/
  | { type: 'rig'; field: string; value: string | number | null } // a fact about the microscope
  // researchers
  | { type: 'accept-correction'; id: string }
  | { type: 'dismiss-correction'; id: string }
  | { type: 'research-followup'; rid: string; text: string; requestId?: string;
      expectedBase?: number | null; selection?: ResearchTarget }
  | { type: 'research-note'; rid: string; text: string; requestId?: string;
      expectedBase?: number | null; selection?: ResearchTarget }
  | { type: 'research-cancel'; rid: string }
  | { type: 'research-evidence'; rid: string; day: string; questionId: string; run: number; choice: 'hold' | 'decline'; requestId: string }
  | { type: 'research-presentation'; action: 'produce'; rid: string; requestId: string; findingVersion: number;
      findingDigest: string; expectedBase: string | null; model?: string; change?: string; contentKind?: 'report' | 'lesson' }
  | { type: 'research-presentation'; action: 'cancel' | 'save'; rid: string; jobId: string }
  | ({ type: 'research-open-capture'; requestId?: string; actionId: string; parentInstance?: string;
       client?: WorkspaceReady['client']; appRunId?: string; screen?: unknown } & ResearchCaptureTarget)
  // one brain tool directly: tests and the scenario harness, not a user feature
  | { type: 'tool'; name: string; args?: Record<string, unknown> }
  // what the studio shows (contract 10): its report, and a picture of it for one look_at_screen
  | WorkspaceObservation
  | { type: 'screen'; seq: number; viewport: [number, number]; visible: boolean; items: ScreenItem[]; applied: string[]; user: UserChange[] }
  | { type: 'screen-image'; id: string; jpeg: string }
  | WorkspaceCaptureReply
  // the user's own change of something on screen, with the verbs the brain uses (screen.py)
  | { type: 'present'; what: string; do: string; value?: string | number; rect?: number[]; revision?: number; screen?: unknown }
  | { type: 'present'; do: 'open'; source: { day: string; id: string; version: number | null; picture: number | null; outputId?: string }; title: string;
      requestId: string; actionId: string };

/** The magnifier's dots, a zoom, the level (wheel, pinch, + and -), close, a researcher's picture. */
export type MagnifierCommand =
  | { type: 'magnifier'; action: 'close' }
  // its object lost its place on the live view: the server may show it on the view it was made on
  | { type: 'magnifier'; action: 'lost'; id: Id; why: 'refinding' | 'out' }
  | { type: 'magnifier'; action: 'zoom'; id: string; hold?: boolean; level?: number }
  | { type: 'magnifier'; action: 'level'; level: number }
  | { type: 'magnifier'; action: 'index'; index: number }
  | { type: 'magnifier'; action: 'image'; url: string; label: string; credit?: string; id?: Id };

export type CommandKind = ClientCommand['type'];
/** The command of one kind (all its variants). */
export type CommandOf<K extends CommandKind> = Extract<ClientCommand, { type: K }>;
/** What a reply carries besides ok and error, where the studio reads it (commands.py). */
interface Replies {
  'capability-run': Replies['action'];
  'workspace-layout': { revision:number; layout:Omit<import('./workspace-views-model').WorkspaceLayout,'active'>|null; removed?:import('./workspace-views-model').WorkspaceView[]; completions?:import('./workspace-views-model').WorkspaceCompletion[]; pending?:import('./workspace-views-model').WorkspaceOrigin[] };
  'session-note': { note?:{id:string;title:string;text:string;version:number}; notes?:{id:string;title:string;text:string;version:number}[] };
  'workspace-profile': { profile: string; review: boolean };
  'workspace-study-profile': { profile: string };
  scores: { scores: Scores };
  click: { objectId: string; trialId: string };
  ask: { trialId: string; answer: string };
  cells: { count: number };
  dataset: { dataset: DatasetState };
  'capability-check': { started: boolean; of: number; said: string };
  // A session's sources (commands.py): what is on stage now; a removal says whether it was on stage, and then what goes
  // there next (the newest other source, else "live").
  source: { onStage: string; previous: string | null; title: string };
  'source-remove': { removed: string; wasOnStage: boolean; next: string; title: string };
  'source-restore': { restored: string; title: string };
  'asset-trash': { trashed: string; wasOnStage: boolean; title: string };
  'asset-restore': { restored: string; title: string };
  action: { comparison?: import("./model-comparison").ComparisonResult; backgroundJob?: {id:string;session:string}; summary: string; title: string; said: string; table: string; tableIdentity: { session: string; revision: number }; placed?: 'specimen' | 'tab' }; // said/table: what it measured, and its table; placed: a This Tab run on the Stage's still (S5a)
  'model-download': { download: DownloadRow };
  'model-install': { install: InstallRow };
  'model-prepare': { install: InstallRow };
  'model-reveal': { path: string };
  'onboarding-models': { offer: import('./starter-models').StarterAnswer };
  'model-add': { model: string; kind: string; says: string };
  'volume-add': { says: string }; // what was added, in words (registrations.py words)
  'model-download-cancel': { download: DownloadRow };
}
export type ReplyOf<K extends CommandKind> = K extends 'workspace-hello' ? WorkspaceReady : ResultMessage & (K extends keyof Replies ? Partial<Replies[K]> : unknown);
/** A command's fields besides its kind (distributed, so a union of variants stays one). */
export type FieldsOf<K extends CommandKind> = CommandOf<K> extends infer C ? (C extends unknown ? Omit<C, 'type' | 'requestId'> : never) : never;

interface PendingRequest {
  kind: CommandKind; hello?: Readonly<WorkspaceHello>;
  body: string; promise: Promise<ResultMessage | WorkspaceReady>;
  resolve(reply: ResultMessage | WorkspaceReady): void; reject(error: Error): void;
}
const originKeys = ['workspaceId', 'clientId', 'pageEpoch'] as const;
function wireRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}
function closedFields(value: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []): boolean {
  return required.every((key) => Object.hasOwn(value, key)) && Object.keys(value).every((key) => required.includes(key) || optional.includes(key));
}
function originId(value: unknown): value is string { return typeof value === 'string' && value.length > 0 && value.length <= 256; }
function validHello(value: WorkspaceHello): boolean {
  return closedFields(value as unknown as Record<string, unknown>, ['type', 'schema', 'requestId', ...originKeys], ['restoration'])
    && value.type === 'workspace-hello' && value.schema === 'workspace-origin-v2'
    && (!Object.hasOwn(value, 'restoration') || value.restoration === 'project-layout')
    && [value.requestId, ...originKeys.map((key) => value[key])].every(originId);
}
function validReady(value: WorkspaceReady, hello: Readonly<WorkspaceHello>): boolean {
  const ready = value as unknown as Record<string, unknown>, client = ready.client;
  return closedFields(ready, ['type', 'schema', 'ok', 'requestId', 'client', 'appRunId', 'connectionGrant'], ['for', 'restoration'])
    && ready.restoration === hello.restoration
    && ready.type === 'workspace-ready' && ready.schema === 'workspace-origin-v2' && ready.ok === true
    && (!Object.hasOwn(ready, 'for') || ready.for === 'workspace-hello') && ready.requestId === hello.requestId
    && originId(ready.appRunId) && originId(ready.connectionGrant) && wireRecord(client)
    && closedFields(client, [...originKeys, 'connectionEpoch']) && originId(client.connectionEpoch)
    && originKeys.every((key) => client[key] === hello[key]);
}

/** The studio's end of the socket: one original request map, with typed correlated replies. */
export abstract class Link {
  private readonly pending = new Map<string, PendingRequest>();
  private requests = 0;
  protected readonly onMessage: (message: ServerMessage) => void;

  constructor(onMessage: (message: ServerMessage) => void) {
    this.onMessage = onMessage;
  }
  abstract send(command: ClientCommand & { requestId?: string }): void;

  /** Send a command and wait for its original reply; sending failure never leaves it pending. */
  request<K extends CommandKind>(type: K, fields: FieldsOf<K>, stableId?: string): Promise<ReplyOf<K>> {
    if (stableId !== undefined && (!originId(stableId) || /^r\d+$/.test(stableId)))
      return Promise.reject(new Error('Invalid durable request identity'));
    const requestId = stableId ?? `r${++this.requests}`;
    let command: ClientCommand & { requestId: string }, body: string;
    try {
      // Detach the submitted body: caller changes cannot alter a pending retry.
      body = JSON.stringify({ ...fields, type, requestId });
      command = JSON.parse(body);
      if (command.type === 'workspace-hello' && !validHello(command)) throw new Error('Invalid workspace origin proposal');
    } catch (error) { return Promise.reject(error); }
    const pending = this.pending.get(requestId);
    if (pending) return pending.kind === type && pending.body === body
      ? pending.promise as Promise<ReplyOf<K>>
      : Promise.reject(new Error('A pending request already owns this identity with different inputs'));
    let resolve!: (reply: ResultMessage | WorkspaceReady) => void, reject!: (error: Error) => void;
    const promise = new Promise<ResultMessage | WorkspaceReady>((yes, no) => { resolve = yes; reject = no; });
    this.pending.set(requestId, {kind:type, body, promise, resolve, reject,
      ...(command.type === 'workspace-hello' ? {hello:Object.freeze({...command})} : {})});
    try { this.send(command); }
    catch (error) {
      this.pending.delete(requestId); reject(error instanceof Error ? error : new Error(String(error)));
    }
    return promise as Promise<ReplyOf<K>>;
  }
  protected rejectPending(error: Error): void {
    const retired = [...this.pending.values()]; this.pending.clear();
    for (const request of retired) request.reject(error);
  }

  /** Only a matching hello can consume ready; no uncorrelated grant reaches the consumer. */
  protected receive(message: ServerMessage): void {
    if (message.type === 'workspace-ready') {
      const request = this.pending.get(message.requestId);
      if (!request?.hello) return;
      this.pending.delete(message.requestId);
      if (!validReady(message, request.hello)) { request.reject(new Error('Invalid or mismatched workspace ready reply')); return; }
      request.resolve(message);
    } else if (message.type === 'result' && typeof message.requestId === 'string') {
      const request = this.pending.get(message.requestId);
      if (request) {
        this.pending.delete(message.requestId);
        if (message.for !== request.kind || typeof message.ok !== 'boolean') {
          request.reject(new Error('Invalid or mismatched request reply')); return;
        }
        if (!message.ok) {
          const reason = [message.error, message.summary].find((value): value is string => typeof value === 'string' && value.trim().length > 0);
          request.reject(Object.assign(new Error(reason || 'The request failed. Please try again.'), {reply:message}));
        }
        else if (request.hello) { request.reject(new Error('Workspace hello did not return an accepted ready reply')); return; }
        else request.resolve(message);
      }
    }
    this.onMessage(message);
  }
}

/** The default of a switch over every server message kind: a kind added to ServerMessage and
 * not handled fails the type check here. At run time a kind this studio does not know yet (a
 * newer server) is passed over, not thrown. */
export function assertNever(message: never): void {
  console.warn('Unhandled server message', message);
}

export class Connection extends Link {
  private socket: WebSocket | null = null;
  private retry = 0;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private readonly onFrame: (frame: Frame) => void;
  private readonly onStatus: (connected: boolean) => void;

  constructor(
    onFrame: (frame: Frame) => void,
    onMessage: (message: ServerMessage) => void,
    onStatus: (connected: boolean) => void,
  ) {
    super(onMessage);
    this.onFrame = onFrame;
    this.onStatus = onStatus;
  }

  open(): void {
    const previous = this.socket; this.socket = null;
    if (this.retryTimer !== null) { clearTimeout(this.retryTimer); this.retryTimer = null; }
    this.rejectPending(new Error('Connection replaced before the request completed'));
    if (previous) {
      const wasOpen = previous.readyState === WebSocket.OPEN;
      previous.onopen = previous.onclose = previous.onmessage = null;
      if (previous.readyState === WebSocket.OPEN || previous.readyState === WebSocket.CONNECTING) previous.close();
      if (wasOpen) {
        this.onStatus(false);
        if (this.socket !== null) return; // the status consumer opened a newer socket
      }
    }
    const url = studioSocketURL('/ws');
    const socket = new WebSocket(url); this.socket = socket;
    let ended = false;
    const owns = () => this.socket === socket;
    const active = () => owns() && !ended && socket.readyState === WebSocket.OPEN;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => {
      if (!active()) return;
      this.retry = 0; this.onStatus(true);
    };
    socket.onclose = () => {
      if (!owns() || ended) return;
      ended = true; this.rejectPending(new Error('Connection closed before the request completed')); this.onStatus(false);
      if (!owns()) return;
      const delay = Math.min(5000, 500 * 2 ** this.retry++);
      this.retryTimer = setTimeout(() => { if (!owns() || !ended) return; this.retryTimer = null; this.open(); }, delay);
    };
    // Newest only (the audit of 2026-10-12): one frame decodes at a time; a frame that arrives meanwhile replaces any
    // still waiting, and a frame older than the one shown is never shown. A busy browser drops frames, never falls behind.
    let waiting: ArrayBuffer | null = null;
    let decoding = false;
    let shownId = -Infinity;
    const pump = async () => {
      if (decoding) return;
      decoding = true;
      try {
        while (waiting) {
          const data = waiting;
          waiting = null;
          let frame: Frame | null;
          try {
            frame = await decodeFrame(data);
          } catch (error) {
            if (active()) this.rejectPending(error instanceof Error ? error : new Error(String(error)));
            continue;
          }
          if (!frame) continue;
          if (!active() || frame.header.id <= shownId) { frame.bitmap.close(); continue; }
          shownId = frame.header.id;
          this.onFrame(frame);
        }
      } finally {
        decoding = false;
      }
    };
    socket.onmessage = async (event) => {
      if (!active()) return;
      if (typeof event.data === 'string') {
        let message: unknown;
        try {
          message = JSON.parse(event.data);
          if (!wireRecord(message) || typeof message.type !== 'string') throw new Error('Invalid server message');
        } catch (error) {
          if (active()) this.rejectPending(error instanceof Error ? error : new Error(String(error)));
          return;
        }
        if (active()) this.receive(message as ServerMessage);
        return;
      }
      if (!(event.data instanceof ArrayBuffer)) {
        this.rejectPending(new Error('Invalid frame message'));
        return;
      }
      waiting = event.data;
      await pump();
    };
  }

  send(command: ClientCommand & { requestId?: string }): boolean {
    if (this.socket?.readyState === WebSocket.OPEN) { this.socket.send(JSON.stringify(command)); return true; }
    else if (command.requestId !== undefined) throw new Error('Hooke’s workspace connection is unavailable. This action was not sent. Wait for the workspace to reconnect, then try again.');
    return false;
  }
}

export interface BundleReview {
  generation: string; digest: string; says: string;
  cases: { case: string; member: string; kind?: 'reader' | 'workflow'; operation?: string; device?: string; source: string;
    parameters?: Record<string, unknown>; tolerance?: Record<string, unknown>; metrics?: Record<string, unknown>;
    inputs?: Record<string, unknown>; referenceSha256?: string;
    saved?: Record<string, unknown>; reopened?: Record<string, unknown>;
    artifacts: Record<'input' | 'actual' | 'reference', { kind: 'image' | 'json'; sha256: string; bytes: number; preview?: string; truncated?: boolean }> }[];
}


/** A WebSocket address for the studio (ADR 0026): in the Mac app it carries the studio token the app gave this window
 *  (main.swift injects `hookeStudioToken`), which the server never serves, so a script copying an Origin can't pass as
 *  the studio. A browser on a source checkout has none, and its loopback Origin is what marks it as the studio. */
export function studioSocketURL(path: string, query = ''): string {
  const token = (globalThis as { hookeStudioToken?: unknown }).hookeStudioToken;
  const params = new URLSearchParams(query.replace(/^\?/, ''));
  if (typeof token === 'string' && token) params.set('studio', token);
  const rest = params.size ? `?${params}` : '';
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}${rest}`;
}
