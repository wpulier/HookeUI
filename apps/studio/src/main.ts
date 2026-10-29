import {selectObservedWorkspaceInput} from './workspace-input';
import { scientificAskSelection } from './scientific-ask-input';
import { ActivityNavigation, type ActivitySurface } from './workspace-study-navigation';
import { workspaceInput } from './workspace-views-model';
import {documentSurfaces} from './workspace-document-surfaces';
import { bridgeAddress } from './bridge-address';
import { artifactPresentation, openStudyCapture, openStudyComparison, openStudyMasks, openStudyTable, openStudyText } from './workspace-study-content';
import { studyArtifacts } from './workspace-study-artifacts';
import { openStudyScientific } from './scientific-document';
import { SetupNote } from './setup-note';
import { SetupProgress } from './setup-progress';
import { StudySessionSwitch, readStudyPresentation, restoreStudyItem, studyEntryReference, type StudyPresentation, type StudyRecovery, type StudyOpenOptions } from './workspace-study-restoration';
import { toolboxTarget } from './toolbox';
import { CapabilityTry, type PreparedTry } from './capabilitytry';
import { MadeToday } from './madetoday';
declare const __MICROMIND_BUILD__: { revision: string | null; dirty: boolean | null; sourceDigest: string | null };
document.documentElement.dataset.buildRevision = __MICROMIND_BUILD__.revision ?? 'unknown';
document.documentElement.dataset.buildDirty = String(__MICROMIND_BUILD__.dirty);
document.documentElement.dataset.buildDigest = __MICROMIND_BUILD__.sourceDigest ?? 'unknown';
import { itemSpace } from './spaces';
import type { WorkspaceViewport } from './workspace-runtime';
import { livePlacementObject } from './window-placement';
import { composeScreen, rectOf, renderHint, saidOnce, ScreenReporter, WorkspaceSession, magnifierSurface, surfaceVisible, type StudioSurfaceCollection, type StudioSurfaceLegacy, type StudioSelection, type PresentMessage, type ScreenItem } from './screen';
import './style.css';
import { assertNever, Connection, type CapabilitiesWire, type AssetMeta, type SessionCleared, type SessionState, type DatasetState, type DayState, type ArenaState, type ClientCommand, type Health, type Reveal, type Scores, type ServerMessage, type Settings, type TopProgram, type ToolRun, type Trial } from './protocol';
import { BoxDrag, frameBox } from './boxdrag';
import { ChatLog } from './chatlog';
import { endsVoiceTurn, type LocalNoticeOptions } from './chatmodel';
import { AskRequests, askError } from './ask-feedback';
import { assistantBlockReason } from './assistant-readiness';
import { ExecutionRecordingControl } from './executionlog';
import { ExecutionHistory } from './executionhistory';
import { AssetsView } from './assets';
import { DatasetsView, elapsedText, lightLabel, lightState, lightTitle, savedText } from './datasets';
import { clock, positionAt } from './playhead';
import { CAMERA_STEPS, CameraFeed, PICTURE_STEPS, clips, duration, footageGroups, isLibrary, opticsLine, specimenOf } from './camera';
import { SOURCES, headline as cameraHeadline, dotState as cameraDot, tab as sourceTab } from './camera/words';
import { cameraControl } from './camera-label';
import { MagnifierPanel } from './magnifier';
import { ComparePicker, SettingsPicker } from './models';
import { DescriptionCard, Notes } from './notes';
import { PanelStack, presentPlace, windowPlacementArea, type Panel, type WindowFacts } from './panels';
import { PerfMeter } from './perf';
import { SpendingMeter } from './spending';
import { populationScreenItem } from './population';
import { tracksScreenItem } from './motiontracks';
import { bindLayerControls } from './layer-controls';
import { madeByName } from './tools-catalog';
import { makerName } from './workspace-data-card';
import { ResearchView } from './research';
import { isWorking, researcherCardView, researchersCount, Shelf } from './shelf';
import { Composer } from './composer';
import { litCell, setLitCell } from './lit-cell';
import type { MenuItem } from './drawer-menu';
import { DownloadPills, LoadPills, StepPills, type StepFact } from './strip-sources';
import { assistantLines, recordWords } from './restart-readiness';
import { ResearcherCards } from './researcher-cards';
import { StudioRecorder } from './recorder';
import { RecordingSetup } from './recording-setup';
import { AnnotatedRecording } from './annotated-recording';
import { requestRecordingDataset } from './recording-dataset';
import { PhonePanel } from './phonepanel';
import { SideColumn } from './side';
import { SlideMapPanel } from './slidemap';
import { ResultViewer } from './viewer';
import { CapabilityPage } from './capabilities';
import { ModelsPage, chipState, uploadModel } from './modelspage';
import { configureStarterModels, noteCapabilities } from './starter-models';
import { listVolumes } from './volumespanel';
import { openAddKey } from './addkey';
import { ARM_DASH, OUTLINE_COLORS, View } from './view';
import { VoiceClient, type VoiceEvent, type VoiceState } from './voice';
import { opensWorkForUser, ShowingBar } from './showing';
import { StudioShell, navigateStudio, workspaceConnectionNotice } from './studio-shell';
import './studio-shell.css';
import './library-pages.css';
import { StageLayout } from './stage-layout';
import { holdUntilLanded } from './side-motion';
import { ResultFocus } from './result-focus';
import { SessionsModel, sessionName } from './sessions';
import { SessionMenu } from './session-menu';
import { SideBar, type SideSection } from './sidebar';
import { cameraHomeWanted, emptyCameraHomeWanted, ConnectionStart, LAND_WAIT_MS, lookState, onStart, StageGate, startMayLand, viewOnOpen, type Landing, type StartLook } from './session-view';
import { addFiles, isAssetFeed, joinedSince, liveState, plainError, SourcesModel, SourcesSection, stageOf, type SourceRow, type SourcesState } from './sources';
import { assetFor, isOwnFootage, libraryUrl, readLibrary } from './asset-library';
import { SessionHome } from './session-home';
import { ExampleGuide, type OnboardingState } from './onboarding';
import { LibraryPage } from './library-page';
import { WorkflowPage } from './workflow-page';
import { SessionsPage } from './sessions-page';
import { GearMenu } from './gear';
import { agentConnections } from './agent-connections';
import { ActionsPanel, type ToolList } from './actions-panel';
import { MeasurementsPage, measurementsHash, measurementsTarget, measurementsPin, type MeasurementPin } from './measurements';

function element<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`Missing #${id}`);
  return el as T;
}

const shell = new StudioShell();
const view = new View(element<HTMLCanvasElement>('view'));
// The stage between two sessions (session-view.ts): from a session's opening until its source lands, no frame is drawn.
const stageGate = new StageGate();
const stageOpening = element<HTMLElement>('stage-opening'); // what it says meanwhile ("Opening “…”…")
let openingTimer = 0;
let switchAsked = ''; // the session a click here asked for ('new' for a new one), while it is waited for
const stageLayout = new StageLayout(element('stage'), view);
// Every panel over or beside the view, for Escape and placement (registered below, in order).
const panels = new PanelStack();
if (document.body.dataset.workspaceStudy === 'true') studyArtifacts().configure(panels, studioWindowFacts);
const resultFocus = new ResultFocus();
window.addEventListener('resize', () => resultFocus.sync());
const magnifier = new MagnifierPanel(view, panels, (command) => connection.send(command));
const notes = new Notes();
const card = new DescriptionCard(view, notes, (command) => {
  if (command.type === 'ask') {
    try {
      if (!workspaceSession) throw new Error('Workspace connection is unavailable');
      const id = workspaceSession.ask(command.text, { frameId: command.frameId, objectId: command.objectId });
      askRequests.started(id, command.text, askScope()); updateAskAvailability();
    } catch (error) { requestSendFailed(error); }
  } else connection.send(command);
});
view.describe = (obj) => notes.line(obj, obj.id === view.selected);
setInterval(() => card.render(), 500); // the card follows its thing in and out of view
notes.onChange = () => card.render();
// Work the user opens from the side bar's Researchers or the shelf is their change to the result viewer (a showing
// never puts back over it; showing.py).
function sendByUser(command: ClientCommand): void {
  if (opensWorkForUser(command)) reporter.userChanged('viewer', 'shows');
  const sent = connection.send(command);
  if (sent && command.type === 'presenter' && command.action === 'show') {
    const target = structuredClone(command);
    resultFocus.request('legacy:viewer', () => {
      const state = viewer.state, work = viewer.selectedWork();
      return state.open && state.rid === target.rid
        && (target.index === undefined || state.index === target.index)
        && (target.outputId === undefined || state.outputId === target.outputId)
        && (target.version === undefined || work?.resource.version === target.version)
        && (target.day === undefined || work?.resource.day === target.day)
        && (!target.savedItem || (work?.researcherId === target.savedItem.researcherId
          && work.resource.day === target.savedItem.resource.day && work.resource.id === target.savedItem.resource.id
          && work.resource.version === target.savedItem.resource.version));
    });
  }
}
const research = new ResearchView((command) => {
  if (command.type === 'research-evidence') return connection.request(command.type, command, command.requestId);
  if (['research-note', 'research-followup', 'research-presentation'].includes(command.type)) {
    if (!connection.send(command)) throw new Error('Connection is offline. Your research request is kept; retry when connected.');
  } else sendByUser(command);
});
notes.research = () => [...research.sessions.values()];
research.onShowInMagnifier = (url, label, credit, id) => {
  if (connection.send({ type: 'magnifier', action: 'image', url, label, credit, id })) resultFocus.request('legacy:magnifier', () => magnifier.source() === url);
  navigateStudio('#');
};
const viewer = new ResultViewer((command) => sendByUser(command), () => research.sessions, {panels, facts:studioWindowFacts, changed:property => reporter.userChanged('viewer', property)});
const openResearchCapture = async (target: import('./protocol').ResearchCaptureTarget): Promise<void> => {
  if (!workspaceSession) throw new Error('Workspace connection is unavailable');
  navigateStudio('#');
  const progress = showHint('Opening captured view…');
  try {
    await workspaceSession.openResearchCapture(target);
    if (hintRevision === progress) showHint('Original captured view opened.');
  } catch (error) {
    if (hintRevision === progress) showHint(error instanceof Error ? error.message : 'The original captured view could not be opened.', true);
    throw error;
  }
};
research.onOpenCapture = openResearchCapture;
viewer.onOpenCapture = openResearchCapture;
// A researcher's Tab (research.ts, research-tab.ts) names the tools it used and its engine as the catalog does, opens a
// tool's run page from Used, and asks the Assistant in the person's own words when Continue is pressed (the Assistant
// opens over the researcher's page and says whether it can carry the work on).
let catalogNames = new Map<string, string>();
research.toolName = (key) => { const name = actionsPanel.runName(key); return name && name !== key ? name : catalogNames.get(key.replace(/^catalog:/, '')) ?? null; };
research.modelName = (id) => catalogNames.get(id) ?? null;
research.onOpenTool = (key) => {
  const model = key.replace(/^catalog:/, '');
  if (actionsPanel.has(key)) actionsPanel.open(key);
  else if (!actionsPanel.useModel(model)) navigateStudio(`#models/${encodeURIComponent(model)}`);
};
research.onAsk = (text) => { window.dispatchEvent(new CustomEvent('workspace-study-assistant')); ask(text); };
const madeToday = new MadeToday((command) => connection.send(command), (source, title) => {
  if (!workspaceSession) throw new Error('Workspace connection is unavailable');
  workspaceSession.openResource(source, title);
});
madeToday.onCount = count => sideBar.count('made', count);
madeToday.onTable = (id) => openMeasurements(id);
madeToday.views = () => [...(workspaceSession?.retainedViews() ?? []), ...resultFocus.retainedViews()];
madeToday.restore = (id) => { if (!resultFocus.restore(id)) workspaceSession?.restoreWindow(id); };
const side = new SideColumn(() => viewer.close());
// The slide map (slidemap.ts): in the side column; open or closed is the server's (show_map).
const slideMap = new SlideMapPanel((command) => connection.send(command), () => view.header);
const mapButton = element<HTMLButtonElement>('map');
mapButton.addEventListener('click', () => slideMap.toggle());
slideMap.onToggle = (open) => mapButton.setAttribute('aria-pressed', String(open));
// The shelf (the Working strip's researchers: working, or new work not opened yet) and the side bar's Researchers
// (every researcher of this session). Its count is its cards (researchersCount), never the quick lookups.
function openStudyResearch(rid: string): void {
  if(document.body.dataset.workspaceStudy==='true'){const day=research.sessions.get(rid)?.day??sitting;shelf.markOpened(rid);navigateStudio(`#research/${encodeURIComponent(rid)}?day=${encodeURIComponent(day)}`);}else viewer.show(rid);
}
const shelf = new Shelf((rid) => openStudyResearch(rid), (rid) => connection.send({ type: 'presenter', action: 'remove', rid }),
  (pills) => shell.strip.set('researchers', pills));
// The strip's +N: what did not fit is in Researchers when it folded researchers only; a run, a load or a step is in
// Activity.
shell.strip.openList = (where) => { if(where==='activity'){showActivity(true);return;} if(document.body.dataset.workspaceStudy==='true')window.dispatchEvent(new CustomEvent('workspace-study-section',{detail:'research'}));else sideBar.open('researchers', true); };
function renderResearchers(): void {
  shelf.update(research.sessions.values(), viewer.state, sitting);
  // The side bar's Researchers: the cards, compact, drawn while that section shows (and when it is opened).
  if (sideBar.showing('researchers') || document.body.dataset.workspaceStudy==='true') sideResearchers.update(research.sessions.values(), viewer.state, sitting);
  countResearchers();
}
function countResearchers(): void {
  const tally = researchersCount(research.sessions.values(), sitting, (s) => shelf.isNew(s));
  sideBar.count('researchers', tally.count, tally.state);
}
shelf.onChange = () => countResearchers();
const researchPanel = element<HTMLElement>('research');
// The chat can fold into a slim rail beside the view, as tall as the chat (a dot on it when
// something new was said meanwhile); the rail brings it back. Remembered per browser.
const chatShow = element<HTMLButtonElement>('chat-show');
const chatUnread = element<HTMLSpanElement>('chat-unread');
let chatBeforeResearch: boolean | null = null;
function setChatHidden(hidden: boolean, remember = true): void {
  // Navigation folds temporarily; an explicit Show/Hide changes what we restore.
  if (remember && chatBeforeResearch !== null) chatBeforeResearch = hidden;
  document.body.dataset.chat = hidden ? 'hidden' : '';
  chatShow.setAttribute('aria-expanded', String(!hidden));
  if (!hidden) chatUnread.hidden = true;
  try {
    if (remember) localStorage.setItem('chat-hidden', hidden ? '1' : '');
  } catch {
    // private window: just not remembered
  }
}
element<HTMLButtonElement>('chat-hide').addEventListener('click', () => setChatHidden(true));
chatShow.addEventListener('click', () => {
  setChatHidden(false);
  question.focus();
});
try {
  if (localStorage.getItem('chat-hidden')) setChatHidden(true);
} catch {
  // not remembered
}

// Record is the one switch (ADR 0009): the studio and the camera feed (recorder.ts), and a dataset of
// everything for later analysis (the server's, so a reload or a restart keeps it). Data beside it is a
// light, not a button: it only says whether that dataset is really being saved (datasets.ts), and a
// click on it opens the Datasets tab.
const recordButton = element<HTMLButtonElement>('record');
const recordLabel = recordButton.querySelector<HTMLSpanElement>('.record-label')!;
const dataLight = element<HTMLAnchorElement>('dataset');
const dataLabel = dataLight.querySelector<HTMLSpanElement>('.dataset-label')!;
let dataset: DatasetState | null = null;
let datasetStopUnconfirmed = false;
let datasetError = ''; // why no dataset could be started, while the studio records without one
let stopping = false; // Stop pressed after a reload: the camera and the dataset are being stopped
let recordStartedAt = 0; // epoch seconds: when this page began recording (the dataset's start is used once known)
async function setDataset(on: boolean): Promise<boolean> {
  try {
    dataset = await requestRecordingDataset(on, () => connection.request('dataset', { on }));
    datasetError = '';
    if (!on) datasetStopUnconfirmed = false;
    return true;
  } catch (exc) {
    datasetError = (exc as Error).message;
    showHint(on ? `Recording without a confirmed dataset: ${datasetError}.` : `The dataset was not stopped: ${datasetError}. Press Stop again.`, true);
    if (!on) { datasetStopUnconfirmed = true; throw exc; }
    return false;
  } finally { renderRecord(); }
}

const annotatedRecording=new AnnotatedRecording(view.canvas,
  ()=>!view.header||view.paused?'Show an input picture on the stage before recording its annotations.':null,
  ()=>`${sessionsModel.current}:${document.body.dataset.activeWorkspaceView??'main'}:${feed.state?.source??''}:${sourcesModel.state?.onStage??''}`);
const recorder = new StudioRecorder(
  () => bridgeAddress(feed.bridgeUrl),
  (text, isError) => {
    if (recorder.state === 'recording' && !recordStartedAt) recordStartedAt = Date.now() / 1000;
    if (recorder.state === 'idle') recordStartedAt = 0;
    renderRecord();
    if (text) showHint(text, !!isError);
  },
  { begin: () => setDataset(true), end: () => setDataset(false) },
  annotatedRecording,
);

/** Recording: this page's recorder, or the dataset the server collects (after a reload the page no longer
 * holds the studio's own recording, but the camera and the dataset go on until Stop). */
function isRecording(): boolean {
  return recorder.state === 'recording' || (recorder.state === 'idle' && (Boolean(dataset?.on) || datasetStopUnconfirmed || recorder.cameraStopUnconfirmed) && !stopping);
}

/** "Stop 12:34": counted from the dataset's start (the server's, so a reload keeps it), else this page's. */
function stopLabel(): string {
  const since = dataset?.on && dataset.startedAt ? dataset.startedAt : recordStartedAt;
  if (!since) return 'Retry Stop';
  return `Stop ${elapsedText(Math.max(0, Date.now() / 1000 - since))}`;
}

function renderRecord(): void {
  const on = isRecording();
  const state = stopping ? 'saving' : recorder.state === 'idle' && on ? 'recording' : recorder.state;
  recordButton.dataset.state = state;
  recordButton.setAttribute('aria-pressed', String(on));
  recordLabel.textContent = state === 'saving' ? 'Saving…' : on ? stopLabel() : 'Record';
  recordButton.title = !on
    ? 'Choose Camera source, Annotated view or Workspace, then review what will be recorded.'
    : recorder.state === 'recording'
      ? `${recorder.summary()}. Click to stop.`
      : recorder.cameraStopUnconfirmed || datasetStopUnconfirmed
        ? 'Stop is not confirmed. Click to retry; retained files and unrelated recording owners stay intact.'
      : "A dataset is active. This page has no active screen or audio recorder after reload; camera capture must be checked in Data. Click to stop.";
  const light = lightState(dataset, recorder.state === 'recording');
  dataLight.dataset.state = light;
  dataLabel.textContent = lightLabel(light);
  dataLight.title = lightTitle(dataset, recorder.state === 'recording', datasetError);
  dataLight.setAttribute('aria-label', `Data: ${dataLight.title}`);
}

const recordingSetup = new RecordingSetup(
  () => feed.state?.source !== 'camera' || feed.state.bridge.sourceKind === 'none' ? 'Select Live camera and connect a camera before recording its source.' : null,
  options => { if (recorder.state === 'idle' && !dataset?.on && !datasetStopUnconfirmed && !recorder.cameraStopUnconfirmed && !stopping) void recorder.start(options); },
  ()=>annotatedRecording.unavailable(),
);
recordButton.addEventListener('click', () => {
  if (recorder.state === 'recording') {
    recorder.stop();
  } else if (recorder.state === 'idle' && (dataset?.on || datasetStopUnconfirmed || recorder.cameraStopUnconfirmed) && !stopping) {
    stopping = true; // this page no longer holds the studio's recording: stop the camera's and the dataset
    renderRecord();
    void recorder
      .releaseCamera()
      .then(async () => {
        await setDataset(false);
        if (recorder.cameraStopUnconfirmed) showHint('Camera Stop is unconfirmed. Press Stop again; another recording cannot begin yet.', true);
      })
      .catch(() => { /* setDataset preserves the failure and active Data state for retry. */ })
      .finally(() => {
        stopping = false;
        renderRecord();
      });
  } else if (recorder.state === 'idle' && !stopping) {
    recordingSetup.open(recordButton);
  }
});
setInterval(() => {
  if (isRecording()) recordLabel.textContent = stopLabel();
}, 500);
renderRecord();

// The session you are in: the project, not the day (sessions.ts). Its title, top left (#session), is the switcher.
// Nothing switches on a click: the command goes to the server, and its echo (`session-cleared`, `day`, the
// conversation, `session`, `sessions`) moves everything.
let sitting = ''; // the session open now (its sid, opaque): its researchers are on the shelf
const sessionsModel = new SessionsModel((command) => connection.send(command));
const sessionMenu = new SessionMenu(sessionsModel, element<HTMLButtonElement>('session'), element<HTMLElement>('session-panel'),
  { openAll: () => navigateStudio('#sessions') });
// All sessions (#sessions): every session as a gallery, as All assets is the library (sessions-page.ts).
const sessionsPanel = element<HTMLElement>('sessions-page');
const sessionsPage = new SessionsPage(sessionsPanel, sessionsModel, (hash) => navigateStudio(hash));
// The side bar is the session (sidebar.ts): its assets, Made, Researchers and Activity, one at a time in a column on
// the live view's left. Each section is drawn by its own view, once: Made by madetoday.ts, Activity by the chat's own
// list (chatlog.ts, so an answer's ⓘ opens it at its request), Researchers by researcher-cards.ts.
const sideBar = new SideBar(sessionsModel, element<HTMLElement>('session-rail'), {
  toolbar: document.querySelector<HTMLElement>('.conversation-toolbar'),
  onUserChange: (property) => reporter.userChanged('sidebar', property),
  onSection: (section: SideSection) => {
    if (section === 'sources') renderSources();
    if (section === 'made') void madeToday.open();
    if (section === 'researchers') renderResearchers();
  },
});
sideBar.content('made').append(element('made-today'));
element('made-today').hidden = false; // shown whenever the side bar shows Made (its own × is the tray's, hidden there)
// Windows the user hid are listed with what was made, to bring back (workspace-windows.ts hides into it).
const hiddenWindows = Object.assign(document.createElement('div'), { id: 'hidden-windows', className: 'hidden-windows' });
hiddenWindows.setAttribute('aria-label', 'Hidden windows');
element('made-today').append(hiddenWindows);
sideBar.content('activity').append(element('activity-panel'));
element('activity-panel').hidden = false;
// Activity's own history and its diagnostic settings go under its entries.
const activityMore = Object.assign(document.createElement('footer'), { className: 'activity-more' });
activityMore.append(element('execution-history'), document.querySelector('.activity-settings')!);
element('activity-panel').append(activityMore);
let activityRequestedSession = '';
const sideResearchers = new ResearcherCards((rid) => { shelf.markOpened(rid); openStudyResearch(rid); }, (command) => {
  if (!connection.send(command)) throw new Error('Reconnect, then try again');
}, (s) => shelf.isNew(s), sideBar.content('researchers'), (session) => {
  if (session && session !== sessionsModel.current) {
    activityRequestedSession = sessionsModel.switchTo(session) ? session : '';
    return;
  }
  activityRequestedSession = '';
  navigateStudio('#');
  showActivity(true);
});
// The side bar's lower half, Actions (actions-panel.ts; docs/design/2026-10-13-actions-panel.md): everything the agent
// can run on the view, run by the user's hand through the `action` command. Its rows and whether each can run are the
// capabilities message's; its forms are the tools' own arguments (GET /api/actions). The dock's view tools move in.
let studyActionOwner: string | null = null;
let studyTransition = false;
let studyInputNavigation:((source:string,title:string,session:string,position:number)=>Promise<boolean>)|null=null;
/** User choices open a dedicated work Tab; internal restoration still uses putOnStage. */
async function openUserInput(target:string,options:{asset?:AssetMeta;at?:number;paused?:boolean;session?:string;say?:(text:string)=>void}={}):Promise<boolean>{
  if(document.body.dataset.workspaceStudy!=='true')return putOnStage(target,options);
  try{
    if(!studyInputNavigation)throw new Error('Wait for the workspace to finish opening.');
    return await studyInputNavigation(target,target==='live'?'Live camera':options.asset?.title??'Saved input',options.session??sessionsModel.current,options.at??0);
  }catch(error){showHint(error instanceof Error?error.message:String(error),true);return false;}
}
const studySessionSwitch = new StudySessionSwitch();
let studyLastReference: import('./workspace-study-restoration').StudyRetainedView | null = null;
async function readRetainedInputs(session: string, capability?: string): Promise<{ inputs: import('./modelspage').RetainedInput[]; typed?: import('./actions-panel').TypedPort[] }> {
    const reply = await fetch('/api/capability-inputs', { method: 'POST',
      headers: { 'X-MicroscopeMind': 'capability-inputs', 'Content-Type': 'application/json' },
      body: JSON.stringify({ session, ...(capability ? { capability } : {}) }) });
    const result = await reply.json(); if (!reply.ok) throw new Error(result.error || 'The retained inputs could not be read.'); return result;
}
const actionsPanel = new ActionsPanel(sideBar.actions(), {
  inputResources: readRetainedInputs,
  inputVolumes: listVolumes,
  run: async (capability, args, use, run, resource, volume, inputs, destination) => {
    const fields = { capability, args, run, ...(volume ? { volume } : {}), ...(resource ? { resource } : {}), ...(inputs ? { inputs } : {}), ...(Object.keys(use).length ? { use } : {}) };
    const reply = document.body.dataset.workspaceStudy === 'true' ? await requestStudyAction(fields,destination) : await connection.request('action', fields);
    return { ok: reply.ok !== false, stopped: reply.stopped === true, stopping: reply.stopping === true, summary: reply.summary, error: reply.error ?? undefined,
      said: reply.said, table: reply.table, tableIdentity: reply.tableIdentity, comparison: reply.comparison };
  },
  stop: async (run) => {
    const reply = await connection.request('action-cancel', { run });
    return { ok: reply.ok !== false, error: reply.error ?? undefined };
  },
  selected: () => (view.selected ? { id: view.selected, label: view.labelOf(view.selected) || '' } : null),
  session: () => sessionsModel.currentRow()?.id ?? '',
  openModels: (model, opener) => {
    if (opener) modelsPage.openedFrom(opener.task); // a run page's step: its sheet's Use it here comes back to that task
    navigateStudio(model ? `#models/${model}` : '#models');
  },
  openActivity: () => showActivity(true),
  openTable: (id, pin) => openMeasurements(id, pin),
  openComparisonMasks: async (id, pin, title) => {
    if (sessionsModel.current !== pin.session) throw new Error('Return to the comparison’s Project to inspect this result.');
    await openStudyMasks({resource:{day:pin.session,id,version:pin.revision ?? 0,picture:0},title,zoom:1,pan:[.5,.5]});
  },
  ask: (text) => { if (ask(text)) window.dispatchEvent(new CustomEvent('workspace-show-agent')); },
  // A run's population drawn on the specimen by the Stage's still's fit: its footer says so (S5a).
  stillDrawn: (population) => view.stillDrawn(population),
  // A comparison's marks on the Stage, each model in its colour (only the workspace draws windows on its Stage).
  showComparison: (comparison) => {
    if (document.body.dataset.workspaceStudy !== 'true' || sessionsModel.current !== comparison.session) return;
    void openStudyComparison(comparison, (question) => { if (ask(question)) window.dispatchEvent(new CustomEvent('workspace-show-agent')); });
  },
  // The phone panel itself, as Troubleshoot opens it: never a click on the header's camera control, which while an asset
  // is on stage is "Live camera" and would put the live camera on stage instead (review finding on #313).
  openCamera: () => phonePanel.setOpen(true),
  cameraPage: () => feed.bridgeUrl ? `${bridgeAddress(feed.bridgeUrl)}/camera/` : '#',
  // Setting a model up in place on its run page: its download, as the Models page's Download sends it (downloads.py).
  download: (model, cancel) => connection.request(cancel ? 'model-download-cancel' : 'model-download', { model }),
  // A click-driven task's "Click in the view": the guided Try's click (capabilitytry.ts), never a change of the default.
  tryClicks: (role, option) => capabilityTry.start(role, option, false),
  clicksArmed: () => capabilityTry.hasClick(),
});
actionsPanel.adoptTools(document.querySelector<HTMLElement>('.tools')!);
// The Measurements page (measurements.ts; #measurements/<id>): what a population or motion item measured, as a table to
// sort and download (tables.py). Show on the view puts it over the live view and goes back there.
const measurementsPanel = element<HTMLElement>('measurements');
const measurementsPage = new MeasurementsPage(measurementsPanel, {
  loaded: (table) => { if(measurementsPanel.checkVisibility())void exampleGuide.measurementsLoaded(table); },
  session: () => sessionsModel.currentRow()?.id ?? '',
  showOnView: (table) => {
    connection.send({ type: 'present', what: table.id, do: 'show' });
    navigateStudio('#');
  },
});
/** Opens the Measurements page at an item (null: the newest). */
function openMeasurements(id?: string | null, pin?: MeasurementPin): void {
  if (pin && pin.session !== sessionsModel.current) {
    showHint('Open the result’s Project to view its measurements.');
    return; // never retarget an owned table to another Project
  }
  if(id&&pin&&pin.session===sessionsModel.current&&document.body.dataset.workspaceStudy==='true'){
    openStudyTable({resource:{day:pin.session,id,version:pin.revision,picture:0},title:'Result',zoom:1,pan:[.5,.5]},
      {shown:table=>{void exampleGuide.measurementsLoaded(table);}});
    return;
  }
  navigateStudio(measurementsHash(id, pin));
}
/** The agent's tools with their arguments, for the Actions panel's forms; asked again until it answers. */
async function loadActionTools(): Promise<void> {
  try {
    const response = await fetch('/api/actions', { cache: 'no-store' });
    if (response.ok) actionsPanel.setTools(((await response.json()) as { tools: ToolList }).tools);
  } catch { /* the server is not up yet: the next capabilities message asks again */ }
}
let actionToolsAsked = false;
// The side bar's Assets (sources.ts; ADR 0022): this session's recordings and pictures. A click puts one on stage
// through the server, × takes it out with an Undo, its details are the asset's own fields, and Add… brings one in from
// this Mac or From All assets (the library's page). The empty stage (session-home.ts) asks the same over the stage.
const sourcesModel = new SourcesModel();
const sourcesSection = new SourcesSection(sideBar.content('sources'), sourcesModel, {
  open: (target, asset) => {
    // A recording comes on held where it was left in this session (else at its start); a picture is always held.
    const row = sourcesModel.row(target);
    void openUserInput(target, { asset: row ?? asset, paused: true, at: row?.position ?? 0 });
  },
  remove: (row) => void removeSource(row),
  setMeta: (id, set) => void connection.request('asset-meta', { asset: id, set }).catch(() => {}), // a refusal is said in the chat
  addFiles: (files) => void addFromMac(files).catch(error => showHint(error instanceof Error ? error.message : String(error), true)),
  openSession: (id) => { sessionsModel.switchTo(id); }, // "Also in:": the session's own switch, its "Opening…" on the title
  fromAssets: () => navigateStudio('#assets'),
});
// All assets (#assets, library-page.ts): the library, by what is in its pictures. A click previews (the session is not
// touched); Add to this session puts it on stage and makes it the session's, and goes back to the workspace.
const libraryPage = new LibraryPage(element<HTMLElement>('assets'), {
  organize: () => {
    if(document.body.dataset.workspaceStudy==='true') navigateStudio('#settings');
    requestAnimationFrame(()=>{
      element<HTMLElement>('behaviour').hidden=false;
      const choice=document.querySelector<HTMLInputElement>('#behaviour input[name="library_looks"]:checked') ?? document.querySelector<HTMLInputElement>('#behaviour input[name="library_looks"]');
      choice?.closest('fieldset')?.scrollIntoView({block:'center'});choice?.focus({preventScroll:true});
    });
  },
  add: (asset) => addFromLibrary(asset),
  setMeta: (id, set) => connection.request('asset-meta', { asset: id, set }).then(() => {}), // the field says Saved, or Not saved and why
  trash: (asset) => trashAsset(asset),
  restore: (id) => connection.request('asset-restore', { asset: id }).then(() => true, () => false),
  upload: (files) => uploadToLibrary(files),
  openSession: (id) => { navigateStudio('#'); sessionsModel.switchTo(id); },
  turnOn: (model) => connection.request('model-turn-on', { model }),
  acquire: async (asset, key) => {
    const result = await connection.request('made-with-acquire', { asset, key }) as unknown as { model: string };
    location.hash = `#models/${encodeURIComponent(result.model)}`;
  },
}, { catalogLayout: document.body.dataset.workspaceStudy === 'true', inSession: (id) => sourcesModel.rows().some((r) => r.id === id) });
// A snapshot, a Record video or a clearer picture made here joins this session's Assets (never Made: they are pictures
// of the specimen, ADR 0022): said once in the hint, and the Assets tab lights up (Will, 2026-10-15).
let sourcesBefore: SourcesState | null = null;
sourcesModel.onChange(() => {
  renderSources(); libraryPage.refresh();
  const after = sourcesModel.state;
  const joined = joinedSince(sourcesBefore, after);
  sourcesBefore = after ? { ...after, sources: [...after.sources] } : null;
  if (!joined.length) return;
  sideBar.flash('sources');
  const said = showHint(`${joined[joined.length - 1].words} saved to this project's Assets`);
  window.setTimeout(() => { if (hintRevision === said) showHint(''); }, 6000);
});
const home = new SessionHome(element<HTMLElement>('session-home'), {
  example: () => { void exampleGuide.start(); },
  live: () => void openUserInput('live'),
  open: (asset) => void openUserInput(asset.id, { asset, paused: true }),
  addFiles: (files) => void addFromMac(files).catch(error => showHint(error instanceof Error ? error.message : String(error), true)),
  library: () => navigateStudio('#assets'),
  projects: () => navigateStudio('#sessions'),
  // Or a question: the Assistant opens and the request is sent in the user's words, admitted there as any request to
  // start a researcher (nothing needs to be on the Stage).
  research: (question) => { window.dispatchEvent(new CustomEvent('workspace-study-assistant')); return ask(`Start a researcher on this question: ${question}`); },
  reconnect: () => { stageChosen(); return feed.reconnect(); },
  troubleshoot: () => phonePanel.setOpen(true, { camera: true }),
  noCamera: () => { stageChosen(); void phonePanel.chooseSource('none'); },
  cameraPage: () => feed.bridgeUrl ? `${bridgeAddress(feed.bridgeUrl)}/camera/` : '#',
});
sessionsModel.onChange(() => home.setTitle(currentSessionName()));
const exampleGuide = new ExampleGuide(element<HTMLElement>('example-guide'), {
  act: async (action, identity) => { // a guide action, sent as the one 'onboarding' command
    const reply = await connection.request('onboarding', { action, ...identity });
    return { ok: reply.ok, error: reply.error, onboarding: reply.onboarding as OnboardingState, rendered: reply.rendered as import('./onboarding').GuideRender | null | undefined };
  },
  session: () => sessionsModel.current,
  specimen: () => navigateStudio('#'),
  measurements: (id, pin) => {
    if (document.body.dataset.workspaceStudy!=='true'&&location.hash === measurementsHash(id, pin)) void measurementsPage.show(id, pin);
    else openMeasurements(id, pin);
  },
  addFiles: () => { exampleFiles.dataset.session = sessionsModel.current; exampleFiles.click(); },
  capabilities: () => navigateStudio('#toolbox'),
  visible: (visible) => { document.body.dataset.exampleGuide = String(visible); },
  onToolbox: () => !!toolboxTarget(location.hash),
  opened: (ok) => {
    const opening = exampleOpening;
    exampleOpening = null;
    if (!opening || opening.token !== openToken) return; // another session opened meanwhile: it lands itself
    // On stage: the server put it there. Refused: the session it opened lands as any new one (its choices, never empty).
    if (ok) stageLanded();
    else void land('home', opening.sid, opening.token);
  },
});
/** The walkthrough's example is opening in this session (its opening's token): the server's to land (sessionOpened). */
let exampleOpening: { sid: string; token: number } | null = null;
const exampleFiles = document.createElement('input');
exampleFiles.type = 'file'; exampleFiles.multiple = true; exampleFiles.accept = home.fileInput.accept;
exampleFiles.hidden = true;
element<HTMLElement>('example-guide').append(exampleFiles);
exampleFiles.addEventListener('change', () => {
  const files = [...(exampleFiles.files ?? [])]; exampleFiles.value = '';
  if (!files.length) return; // cancel never creates or switches a session
  if (exampleFiles.dataset.session !== sessionsModel.current) { showHint('The project changed. Choose your files again in the project you want.', true); return; }
  void (async () => {
    try {
      const reply = await connection.request('session', { action: 'new' });
      const session = reply.session;
      if (typeof session !== 'string' || session !== sessionsModel.currentRow()?.id)
        throw new Error('The project changed. Your example is saved; open your files in the project you want.');
      navigateStudio('#');
      await addFromMac(files, session);
    } catch (error) { showHint(error instanceof Error ? error.message : 'Could not open a new project. Try again.', true); }
  })();
});
element<HTMLButtonElement>('example-walkthrough').addEventListener('click', () => { void exampleGuide.start(); });
// A switch the server never echoed is given up and said; the chip's "since" stays true as the clock moves on.
setInterval(() => { if (!sessionsModel.expire()) sessionMenu.renderChip(); }, 1000);
window.addEventListener('resize', () => { if (sessionMenu.isOpen()) sessionMenu.place(); });

/** The session open now: its researchers are on the shelf and in the side bar's Researchers. */
function renderSession(state: SessionState): void {
  sitting = state.id;
  research.sitting = state.id;
  sessionsModel.applySession(state);
  renderResearchers();
  research.render();
}

// Today's conversations (threads): the chat shows the active one; start a new one or go back.
const threadPick = element<HTMLSelectElement>('thread');
let day: DayState | null = null;

function renderDay(state: DayState): void {
  day = state;
  const renaming = document.querySelector<HTMLInputElement>('.thread-rename');
  if (renaming && renaming.dataset.session !== state.id) renaming.remove();
  const time = (t: number) => new Date(t * 1000).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
  const options = state.threads.map((t) => {
    const o = document.createElement('option');
    o.value = t.id;
    const about = t.subjects.length ? ` · ${t.subjects[t.subjects.length - 1]}` : '';
    o.textContent = `${t.title}${about} (${time(t.started)})`;
    return o;
  });
  const fresh = document.createElement('option');
  fresh.value = '__new';
  fresh.textContent = '+ New conversation';
  const rename = document.createElement('option');
  rename.value = '__rename';
  rename.textContent = 'Rename this one…';
  threadPick.replaceChildren(...options, fresh, rename);
  threadPick.value = state.active;
  threadPick.hidden = !!document.querySelector('.thread-rename'); // a rename in progress keeps its field
}

/** Rename this one…: a field in the picker's place (never a blocking prompt). Enter or leaving it renames to a
 * non-empty name; Escape keeps the old one. */
function renameThreadInline(): void {
  const session = day?.id, id = day?.active, current = day?.threads.find((t) => t.id === id);
  if (!session || !id || document.querySelector('.thread-rename')) return;
  const field = document.createElement('input');
  field.dataset.session = session;
  field.className = 'thread-pick thread-rename'; field.value = current?.title ?? ''; field.setAttribute('aria-label', 'Conversation name');
  let done = false;
  const finish = (keep: boolean, focus = false) => {
    if (done) return; done = true;
    const title = field.value.trim();
    if (keep && day?.id === session && title && title !== current?.title) connection.send({ type: 'thread', action: 'rename', session, id, title });
    // Focus goes back where the rename came from: the picker, or in the workspace (no picker there) the Assistant's ⋯.
    field.remove(); threadPick.hidden = false;
    if (focus) (document.body.dataset.workspaceStudy === 'true' ? document.querySelector<HTMLElement>('.study-assistant-more') ?? threadPick : threadPick).focus();
  };
  field.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') { event.preventDefault(); finish(true, true); }
    else if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); finish(false, true); }
  });
  field.addEventListener('blur', () => finish(true));
  threadPick.hidden = true; threadPick.after(field); field.focus(); field.select();
}
threadPick.addEventListener('change', () => {
  const value = threadPick.value;
  if (day) threadPick.value = day.active; // the server's echo moves it, not the click
  if (value === '__new') connection.send({ type: 'thread', action: 'new' });
  else if (value === '__rename') renameThreadInline();
  else if (value && value !== day?.active) connection.send({ type: 'thread', action: 'switch', id: value });
});

// The Fiji list (inventories.py): both Toolbox views say its counts, Toolbox lists it whole.
const readFijiInventory = async () => {
  const r = await fetch('/api/catalog/inventories/fiji', { cache: 'no-store' });
  if (!r.ok) throw new Error(`the list could not be read (${r.status})`);
  return r.json();
};
const tabCapabilities = element<HTMLAnchorElement>('tab-capabilities');
const capabilitiesPanel = element<HTMLElement>('capabilities');
// The Capabilities page (capabilities.ts): a verdict and a role's choice go to the app as commands.
const capabilityPage = new CapabilityPage(
  capabilitiesPanel,
  (id, state, note) => connection.request('validate', { id, state, note }),
  (role, option) => connection.send({ type: 'settings', settings: { [role]: option } as Partial<Settings> }),
  () => connection.request('capability-check', {}), // Check now: every model on this Mac, once (model_checks.py)
  (model, cancel) => connection.request(cancel ? 'model-download-cancel' : 'model-download', { model }), // downloads.py
  // The Toolbox frame: Fiji's tile, and Add a capability, which is done on Toolbox (its link box and file picker).
  { fiji: readFijiInventory, add: (way) => navigateStudio(`#toolbox/add-${way}`),
    action: (id) => { navigateStudio('#live'); actionsPanel.open(id); } },
);
// The Models page (#models, modelspage.ts): every model, why it is on or off, and the one step that turns it on; the
// header's chip says whether your current choices can all run, and opens it.
async function capabilityWorkspaceRequest(id: string, body: Record<string, unknown>): Promise<import('./modelspage').CapabilityWorkspace> {
  const reply = await fetch(`/api/capability-workspaces${id ? `/${encodeURIComponent(id)}` : ''}`, {
    method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'capability-workspace' }, body: JSON.stringify(body),
  });
  const result = await reply.json(); if (!reply.ok) throw Object.assign(new Error(result.says || 'The coding workspace could not be opened.'), { recovery: result.recovery }); return result;
}
const modelsPanel = element<HTMLElement>('models-page');
// The small-models offer (starter-models.ts): the first-launch Welcome and the row at the top of Models. Its answer is
// kept by onboarding-models; its downloads are the same commands and queue as Models' own.
configureStarterModels({
  status: async () => (await connection.request('onboarding-models', { action: 'status' })).offer!,
  answer: async (answer, models) => (await connection.request('onboarding-models', { action: answer, models })).offer!,
  download: (model) => connection.request('model-download', { model }),
  turnOn: (model) => connection.request('model-turn-on', { model }),
  reveal: () => connection.request('model-reveal', { model: '' }),
  models: () => navigateStudio('#models'),
});
const modelsPage = new ModelsPage(modelsPanel, {
  download: (model, cancel) => connection.request(cancel ? 'model-download-cancel' : 'model-download', { model }),
  reveal: (model) => connection.request('model-reveal', { model }),
  // Your own models (orchestrator/mine.py): added runs nothing; Turn on checks it first, in a process of its own.
  add: (source, name, trial) => connection.request('model-add', { source, ...(name ? { name } : {}), ...(trial ? { trial } : {}) }),
  activate: (model) => connection.request('model-activate', { model }),
  deactivate: (model) => connection.request('model-deactivate', { model }),
  install: (model) => connection.request('model-install', { model }),
  prepare: (model) => connection.request('model-prepare', { model }),
  installCancel: (model) => connection.request('model-install-cancel', { model }),
  uninstall: (model) => connection.request('model-uninstall', { model }),
  fijiInventory: readFijiInventory,
  fijiAdd: (page) => connection.request('fiji-add-site', { page }),
  fijiRollback: () => connection.request('fiji-rollback', {}),
  catalogUpdate: () => connection.request('catalog-update', {}),
  space: {
    list: async () => {
      const reply = await fetch('/api/catalog/space', { cache: 'no-store' });
      if (!reply.ok) throw new Error(`Storage could not be read (${reply.status})`);
      return reply.json();
    },
    remove: (id, revision) => connection.request('capability-remove', { id, revision }),
  },
  shelf: {  // what it can do by job, and what's new (capabilityshelf.ts; capability_shelf.py, catalog_news.py)
    turnOn: (job, plan, recheck) => connection.request('capability-turn-on', { id: job, option: plan.option, plan, recheck }),
    cancel: (job, attempt) => connection.request('capability-turn-on-cancel', { id: job, attempt }),
    tryIt: (job, option, prepareExample) => capabilityTry.start(job, option, prepareExample),
    seen: () => connection.request('catalog-news-seen', {}),
    dismiss: (key) => connection.request('catalog-news-dismiss', { key }),
    get: (key, commit, offer) => connection.request('catalog-news-get', { key, commit, offer }),
  },
  turnOnModel: (model, trial) => connection.request('model-turn-on', { model, ...(trial ? { trial } : {}) }),
  build: (link, trial, description, session) => connection.request('model-build', { link, trial, ...(description !== undefined ? { description, session } : {}) }) as unknown as Promise<{ says?: string; draft?: string }>,
  useDraft: (draft, reviewed) => connection.request('model-draft-use', { draft, reviewed }) as unknown as Promise<{ says?: string; model?: string }>,
  importCapability: async (file, session) => {
    const reply = await fetch(`/api/capability-import?session=${encodeURIComponent(session)}`, {
      method: 'POST', body: file, headers: { 'X-MicroscopeMind': 'capability-import' },
    });
    const result = await reply.json(); if (!reply.ok) throw Object.assign(new Error(result.says || 'The capability could not be imported.'), { recovery: result.recovery }); return result;
  },
  reviewBundle: async (model, session, accepted) => {
    const reply = await fetch('/api/capability-review', { method: 'POST',
      headers: { 'X-MicroscopeMind': 'capability-review', 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: accepted?.digest ? 'accept' : 'read', model, session, ...accepted }) });
    const result = await reply.json(); if (!reply.ok) throw new Error(result.says || 'The package review could not be completed.'); return result;
  },
  inputResources: readRetainedInputs,
  workspace: {
    create: (request, session) => capabilityWorkspaceRequest('', { session, description: request.description,
      source: request.source, runner: request.runner, input: request.input, output: request.output }),
    read: (id, session) => capabilityWorkspaceRequest(id, { session, action: 'read' }),
    reveal: (id, session) => capabilityWorkspaceRequest(id, { session, action: 'reveal' }),
  },
  ownPicture: async (file, job) => {
    const reply = await fetch(`/api/own-trials?job=${encodeURIComponent(job)}`, { method: 'POST', body: file, headers: { 'X-MicroscopeMind': 'own-picture' } });
    const result = await reply.json(); if (!reply.ok) throw new Error(result.says || 'The picture could not be kept.'); return result;
  },
  compareOwn: (trial, model, draft) => connection.request('model-own-compare', { trial, model, draft }) as unknown as Promise<import('./protocol').OwnComparison>,
  useOwn: (model, trial) => connection.request('model-own-use', { model, trial }) as unknown as Promise<{ says: string }>,
  communityDraft: (draft) => connection.request('model-draft-community', { draft }) as unknown as Promise<{ text: string; says: string }>,
  publishPrepare: (model, destination) => connection.request('model-publish-prepare', { model, destination }) as unknown as Promise<{ says: string; package: string }>,
  publishSubmit: (model, pkg, signoff) => connection.request('model-publish-submit', { model, package: pkg, signoff }) as unknown as Promise<{ says: string }>,
  githubStatus: () => connection.request('github-signin', { do: 'status' }) as unknown as Promise<{ setUp: boolean; signedIn: boolean; says: string }>,
  session: () => sessionsModel.currentRow()?.id ?? '',
  // Use it here and Try on … only where they lead to a run page (actions-panel.ts), named after what is on the Stage.
  served: (model) => actionsPanel.served(model),
  specimen: () => actionsPanel.stageName(),
  selectCapability: selection => connection.request('capability-select', selection),
  tryKind: (model, input) => (document.body.dataset.workspaceStudy === 'true'
    ? requestStudyCommand({ type: 'capability-run', model, ...input, run: crypto.randomUUID() })
    : connection.request('capability-run', { model, ...input })) as Promise<import('./actions-panel').ActionReply>,
  openTable: (id, pin) => openMeasurements(id, pin),
  chooseInput: () => { location.hash = '#assets'; },
  setVersion: (model, generation) => connection.request('model-version', { model, generation }) as unknown as Promise<{ says?: string }>,
  maintenance: {  // an entry's updates under the policy you record (maintenance-status.ts; ADR 0025)
    policy: (model, policy) => connection.request('maintenance-policy', { model, policy }) as Promise<{ says?: string }>,
    check: (model) => connection.request('maintenance-check', { model }) as Promise<{ says?: string }>,
    apply: (model, receipt) => connection.request('maintenance-apply', { model, ...receipt }) as Promise<{ says?: string }>,
    undo: (model) => connection.request('maintenance-undo', { model }) as Promise<{ says?: string }>,
    stop: (model) => connection.request('maintenance-stop', { model }) as Promise<{ says?: string }>,
    accept: (model, fingerprint) => connection.request('maintenance-accept', { model, fingerprint }) as Promise<{ says?: string }>,
    merge: (model) => connection.request('maintenance-merge', { model }) as Promise<{ says?: string }>,
  },
  report: (model) => connection.request('model-report', { model }) as unknown as Promise<{ title: string; text: string; url: string; says: string }>,
  channelAdd: (url, commit) => connection.request('channel-add', { url, ...(commit ? { commit } : {}) }) as Promise<{ says?: string }>,
  channelTrust: (name, allowed) => connection.request('channel-trust', { name, allowed }),
  channelRemove: (name) => connection.request('channel-remove', { name }),
  channelUpdate: (name) => connection.request('channel-update', { name }) as Promise<{ says?: string }>,
  upload: uploadModel,
  volumes: {  // brainreg's volumes and registrations (volumespanel.ts; orchestrator/registrations.py)
    list: () => listVolumes(sessionsModel.currentRow()?.id ?? ''),
    upload: async (file) => {
      const r = await fetch(`/api/volumes/upload?name=${encodeURIComponent(file.name)}`,
        { method: 'POST', headers: { 'X-MicroscopeMind': 'add-volume' }, body: file });
      return await r.json() as { ok: boolean; says?: string };
    },
    add: (path) => connection.request('volume-add', { path }),
    describe: (id, facts) => connection.request('volume-describe', { id, ...facts }),
    start: (volume) => connection.request('job-start', { volume }),
    cancel: (id) => connection.request('job-cancel', { id }),
    again: (id) => connection.request('job-again', { id }),
    open: (id) => { void connection.request('present', { what: id, do: 'open' }); },
  },
  observe: (model) => connection.request('observe-again', model ? { model } : {}), // observed.py: ask again
  confirm: (model) => connection.request('observe-confirm', { model }), // the user's word, kept as theirs
});
sessionsModel.onChange(() => modelsPage.sessionChanged()); // what can be tried depends on the session
const setupChip = element<HTMLButtonElement>('setup-chip');
const setupChipWords = element<HTMLSpanElement>('setup-chip-words');
const setupNote = element<HTMLElement>('setup-note');
const setupNotice = new SetupNote(setupNote, openAddKey, id => navigateStudio(`#models/${id}`),
  { whenEmpty: document.body.dataset.workspaceStudy === 'true' });
home.onVisibilityChange = () => setupNotice.context(day?.localOnly === true, home.isOpen());
setupChip.addEventListener('click', () => navigateStudio(toolboxTarget(location.hash) ? '#' : '#toolbox'));

/** The chip and the live view's note, from the capabilities wire's models (catalog_view.py). */
let modelsNeedingYou = 0; // the chip's count, for the workspace rail's Models badge
// Setting up, above Activity's list: what the app is preparing, findable after its sheet closes (setup-progress.ts).
const setupProgress = new SetupProgress(element<HTMLOListElement>('activity-log'), id => navigateStudio(`#models/${id}/setup`));
function renderSetup(wire: CapabilitiesWire): void {
  setupProgress.update(wire);
  const chip = chipState(wire.models);
  modelsNeedingYou = wire.models?.filter((row) => row.group === 'needs-you').length ?? 0;
  setupChip.dataset.tone = chip.tone;
  setupChip.hidden = chip.tone !== 'needs'; // shown only when something needs you (the gear has the Models page)
  setupChipWords.textContent = chip.words;
  setupChip.title = chip.title;
  setupNotice.update(wire);
}
/** The composer's model chip: the Assistant's chat model, the one that looks at the view and answers (Seeing), by its
 * one name ("Gemini 3.8 Flash"); it opens the Models defaults (every job's model), the same picker as before. */
function renderComposerModel(wire: CapabilitiesWire): void {
  const role = wire.roles.find((r) => r.id === 'vision');
  const option = role?.options.find((o) => o.id === role.current);
  const name = option?.label ?? '';
  // A chosen model without its key says so here, before the first question finds out (readiness: its next step is the key).
  const key = option && !option.available && option.readiness?.next?.kind === 'key' ? option.readiness : null;
  composer.setModel(name, key ? `${name}: ${key.word.charAt(0).toLowerCase()}${key.word.slice(1)}. Add it in Models › Keys, or choose the models for each job`
    : name ? `The Assistant answers with ${option?.said ?? name}. Choose the models for each job` : 'Choose the models for each job', key ? 'needs a key' : '');
}
const source = element<HTMLButtonElement>('source');
const cameraPanel = element<HTMLElement>('camera-panel');
const feedBadge = element<HTMLDivElement>('feed-badge');
// While a recording plays: its name, a playhead to move within it, and the time (playhead.ts).
const playhead = element<HTMLInputElement>('playhead');
const feedTime = element<HTMLSpanElement>('feed-time');
const useOptics = element<HTMLButtonElement>('use-optics');
// The header's camera control only ever opens the camera menu (Will, 2026-10-15: one click on it, reading "Live camera",
// had swapped a tester's recording for the camera). Going live from a recording or picture is the menu's Switch to live
// camera, or the empty stage's Use live camera.
const playPause = element<HTMLButtonElement>('play-pause');
let scrubbing = false;
let appObjective: number | null = null; // the objective the app is set to (the objective menu, or by voice)
const notice = element<HTMLDivElement>('notice');
const feedLabel = element<HTMLSpanElement>('feed-label');
const hint = element<HTMLParagraphElement>('hint');
let hintRevision = 0;
// The status line's performance meter: a dot and a word; on hover, focus or a tap, the view's own
// numbers (delay, slide, outlines) and each part now against its ideal (perf.ts).
const perf = new PerfMeter(async () => {
  const response = await fetch('/api/machine/top', { cache: 'no-store' });
  return response.ok ? ((await response.json()) as { top: TopProgram[] }).top : [];
});
const spending = new SpendingMeter(async () => {
  const response = await fetch('/api/spending', { cache: 'no-store' });
  if (!response.ok) throw new Error('Spending unavailable');
  return response.json();
}, (usd) => connection.request('usage-cap', { usd }));
const meters = Object.assign(document.createElement('div'), { className: 'status-meters' });
meters.append(perf.root, spending.root);
// Today's spend on the strip's Spending button: read once now, again when a researcher or a run ends (no timer).
void spending.refreshTotal();
const researchStatus = new Map<string, string>();
shell.meterSlot.append(meters);
const cellsButton = element<HTMLButtonElement>('cells');
const spotsButton = element<HTMLButtonElement>('spots');
const clearButton = element<HTMLButtonElement>('clear');
const starters = element<HTMLDivElement>('starters');
const askForm = element<HTMLFormElement>('ask');
// The composer (composer.ts; the workspace-feel plan, "The chat"): the chip line (the workspace fills it: the specimen and
// where answers go), the field (Enter sends, Shift+Enter a new line), + (the workspace's ways to add a specimen), the
// Assistant's model chip (the Models defaults, the same picker as before), Talk and Send.
let composerAttach: () => MenuItem[] = () => [];
const composer = new Composer(askForm, { fieldId: 'question', label: 'Message to the Assistant', placeholder: 'Ask anything',
  attach: { label: 'Add a specimen', items: () => composerAttach() },
  model: { words: '', title: 'The Assistant’s models', open: (chip) => {
    // The Models defaults float over everything from the chip: out of the old bar on first use.
    const form = element<HTMLFormElement>('models');
    if (document.body.dataset.workspaceStudy === 'true' && form.parentElement !== document.body) document.body.append(form);
    pickers[1].openFrom(chip);
  } },
  extra: [element<HTMLButtonElement>('talk')] });
const question = composer.field;
const askStatus = document.createElement('p');
askStatus.className = 'ask-status'; askStatus.setAttribute('role', 'status');
askStatus.textContent = 'Connecting to the studio…'; askForm.before(askStatus);
let workspaceReady = false;
const askRequests = new AskRequests();
const askScope = () => ({ day: day?.id ?? null, thread: day?.active ?? null });
function askBlocked(): string | null {
  return assistantBlockReason(actionsPanel.capabilities(), workspaceReady, askRequests.pending(askScope()));
}
function updateAskAvailability(): void {
  composer.send.disabled = Boolean(askBlocked());
  for (const form of document.querySelectorAll('.study-ask-composer')) form.dispatchEvent(new Event('assistant-readiness'));
}
updateAskAvailability();
const verdict = element<HTMLElement>('verdict');
const verdictQuestion = element<HTMLParagraphElement>('verdict-question');
const verdictChoices = element<HTMLDivElement>('verdict-choices');
const verdictNote = element<HTMLInputElement>('verdict-note');
const verdictReveal = element<HTMLOListElement>('verdict-reveal');
// Two popups alike: which models do the jobs, and how the app behaves with them (models.ts).
const sendSettings = (changes: Partial<Settings>) => {
  connection.send({ type: 'settings', settings: changes });
  // The outlines' colour or style changed from the menu: the user's own (the screen report; Amendment 4.4).
  if ('outlineColor' in changes) reporter.userChanged('outlines', 'colour');
  if ('style' in changes) reporter.userChanged('outlines', 'fill');
};
const sendObjective = (objective: number | null) => connection.send({ type: 'scale', objective });
const executionRecording = new ExecutionRecordingControl((enabled) => {
  connection.send({ type: 'execution-recording', enabled });
});
element<HTMLElement>('execution-logging').append(executionRecording.action().root);
element<HTMLElement>('execution-history').append(new ExecutionHistory().root);
// The gear (gear.ts): Behaviour opens from its menu, under it; Models is on the bar and opens under its own button.
const gear = new GearMenu(element<HTMLButtonElement>('gear'), element<HTMLElement>('gear-panel'));
const pickers = [
  new SettingsPicker('behaviour', element<HTMLFormElement>('behaviour'), element<HTMLButtonElement>('behaviour-toggle'), sendSettings, sendObjective, executionRecording.root),
  new SettingsPicker('models', element<HTMLFormElement>('models'), element<HTMLButtonElement>('models-toggle'), sendSettings, sendObjective),
];
pickers[0].anchor = gear.toggle;
const blindSwitch = element<HTMLButtonElement>('blind-switch');
const blindState = element<HTMLSpanElement>('blind-state');
const compareDialog = element<HTMLDialogElement>('compare');
const comparePicker = new ComparePicker(element<HTMLFormElement>('compare-body'), (changes) =>
  connection.request('arena', { arena: changes }).catch((error: Error) => showHint(capitalise(error.message), true)),
);

// Voice conversation (Talk button).
const talkButton = element<HTMLButtonElement>('talk');
const talkLabel = talkButton.querySelector<HTMLSpanElement>('.talk-label')!;
const voiceControls = Object.assign(document.createElement('div'), {className: 'voice-session-controls', hidden: true});
const voiceStatus = Object.assign(document.createElement('span'), {className: 'voice-session-status'});
voiceStatus.setAttribute('role', 'status');
const voiceMute = Object.assign(document.createElement('button'), {type: 'button', textContent: 'Mute microphone'});
voiceMute.onclick = () => { voice.setMuted(!voice.muted); voiceMute.textContent = voice.muted ? 'Unmute microphone' : 'Mute microphone'; voiceMute.setAttribute('aria-pressed', String(voice.muted)); };
const voiceActivity = Object.assign(document.createElement('button'), {type: 'button', textContent: 'Activity'});
voiceActivity.onclick = () => showActivity(true);
voiceControls.append(voiceStatus, voiceMute, voiceActivity);
talkButton.closest('form')!.prepend(voiceControls);
const voice = new VoiceClient(handleVoice, renderVoiceState, (level) =>
  talkButton.style.setProperty('--level', Math.min(1, level * 4).toFixed(2)),
);

// The chat: the conversation open now, typed and spoken (chatlog.ts). Its Activity is the side bar's Activity.
const activityNavigation = new ActivityNavigation({
  showing: () => sideBar.showing('activity'),
  show: open => {
    if (open) sideBar.open('activity', true);
    else if (sideBar.showing('activity')) sideBar.setOpen(false, true);
  },
});
const activityShown = () => activityNavigation.showing();
function showActivity(open: boolean): void { activityNavigation.show(open); }
// The Working strip's other pills: agent steps still running after their answer, and models loading (strip-sources.ts).
const stepPills = new StepPills(() => showActivity(true), (pills) => shell.strip.set('steps', pills));
// The Assistant's steps as they stand (the chat's steps hook), for what a restart would stop (restartWork).
let assistantSteps: StepFact[] = [];
// One pill per action: a hand-started run's own load joins its run's pill; a model is named by its display name.
const loadPills = new LoadPills(() => showActivity(true), (pills) => shell.strip.set('loads', pills),
  { claimed: (event) => actionsPanel.claimsLoad(event), models: () => actionsPanel.capabilities()?.models ?? [] });
// A model's files downloading, from any page: its pill fills from the measured bytes; it opens the run page that asked.
const downloadPills = new DownloadPills((model) => { if (!actionsPanel.showDownload(model)) navigateStudio(`#models/${encodeURIComponent(model)}`); },
  (pills) => shell.strip.set('downloads', pills));
element<HTMLButtonElement>('activity-toggle').addEventListener('click', () => showActivity(!activityShown()));
element<HTMLButtonElement>('activity-close').addEventListener('click', () => showActivity(false));
const chat = new ChatLog(element<HTMLOListElement>('log'), starters, {
  activity: element<HTMLOListElement>('activity-log'),
  openActivity: () => showActivity(true),
  activityCount: count => sideBar.count('activity', count),
  executionRecording,
  retryOperation: fields => connection.request('operation-retry', fields),
  openResult: (source) => {
    if (!workspaceSession) return;
    try {
    const existing = workspaceSession?.retainedViews().find(view => {
      const ref = view.resource as {day?: string; id?: string; version?: number | null; picture?: number | null} | null;
      return ref?.day === source.day && ref.id === source.id && ref.version === source.version && ref.picture === source.picture;
    });
    if (existing) workspaceSession.restoreWindow(existing.id);
    else workspaceSession.openResource(source, source.title);
    navigateStudio('#');
    } catch { showHint('This saved result could not be opened. Reconnect and try again.', true); }
  },
  research: (rid) => research.sessions.get(rid),
  // "Show it" opens the researcher's Tab, as its card, its pill and its row do (openStudyResearch marks it opened).
  show: (rid) => {
    if (document.body.dataset.workspaceStudy !== 'true') navigateStudio('#');
    openStudyResearch(rid);
  },
  // Researcher cards under the request that started each (chat-cards.ts), said as their Tab and pill say them.
  researchCards: { list: () => research.sessions.values(), view: researcherCardView, open: (rid) => openStudyResearch(rid) },
  cells: { make: (state, label) => litCell(state, { size: 12, label }), set: (cell, state, label) => setLitCell(cell as SVGSVGElement, state, { size: 12, label }) },
  ResizeObserver: typeof ResizeObserver === 'function' ? ResizeObserver : null,
  describe: (ids) => notes.describeObjects(ids),
  hint: (text) => chat.system(text),
  steps: (steps) => { assistantSteps = steps; stepPills.update(steps); },
  voiceLive: () => voice.state === 'live' || voice.state === 'connecting',
  unread: () => {
    if (document.body.dataset.chat === 'hidden') chatUnread.hidden = false;
  },
});


const blindBar = element<HTMLElement>('blind-bar');
const scoresDialog = element<HTMLDialogElement>('scores');
const scoresBody = element<HTMLDivElement>('scores-body');

// Blind A/B/C trials (services/orchestrator/arena.py).
let arena: ArenaState | null = null;
let trial: Trial | null = null;

let health: Health | null = null;
let connected = false;
let connectionObserved = false;
let lastAdvice: string | null = null; // the coach's advice last shown (orchestrator/coach.py)
let objectCount = 0;
const frameTimes: number[] = [];
let lastDelay = 0;

let workspaceSession: WorkspaceSession | null = null;
const connection = new Connection(
  (frame) => {
    // Another session opening: nothing of the last one is drawn until its source lands (session-view.ts StageGate).
    if (!stageGate.accept(frame.header, performance.now())) { frame.bitmap.close(); return; }
    annotatedRecording.beforeFrame();
    view.setFrame(frame);
    stageLayout.schedule();
    const now = performance.now();
    frameTimes.push(now);
    while (frameTimes.length && now - frameTimes[0] > 2000) frameTimes.shift();
    lastDelay = Date.now() - frame.header.t * 1000;
  },
  handleMessage,
  (isConnected) => {
    connectionObserved = true;
    connected = isConnected;
    exampleGuide.connectionChanged(isConnected);
    executionRecording.connected(isConnected);
    chat.connected(isConnected);
    view.live = isConnected;
    if (!isConnected) view.retireOverlayPresentations();
    if (!isConnected) actionsPanel.connectionLost(); // a row still stopping can hear no settlement: said, never stale
    if (!isConnected && document.body.dataset.workspaceStudy === 'true' && studyActionOwner)
      window.dispatchEvent(new CustomEvent('workspace-study-action', { detail: { run: studyActionOwner, phase: 'unknown', error: 'Connection lost. The server outcome is unknown. Reconnect and inspect retained results and execution history. Reload only after reconciling that outcome.' } }));
    renderStatus();
    void workspaceSession?.connected(isConnected).catch((error) => {
      diagnostic(error); askStatus.hidden = false;
      askStatus.textContent = 'Not connected to the studio. Copy anything you typed, then reload to reconnect.';
    });
  },
);

let layerWhy = ''; // the last reason given for not drawing the shown population (said once)
let layerWhyHint = -1; // the hint that said it, replaced once the cells are drawn again while it still shows
let tracksWhy = ''; // the last reason given for not drawing the shown motion tracks (said once)

/** The kept outlines and motion tracks drawn on the Stage's specimen now (Data's "On this Tab", ticket 66 D2): a layer
 * shows them, overlays are not held hidden, and some of them are checked in place. Their ids, as the index names them. */
function drawnOnStage(): string[] {
  if (view.hideOverlays) return [];
  const outlines = view.layer?.population && view.layer.verified.length ? view.layer.population : null;
  const tracks = view.tracksLayer?.tracks && view.tracksLayer.verified ? view.tracksLayer.tracks : null;
  return [outlines, tracks].filter((id): id is string => !!id);
}
let drawnKey = '';
/** Data checks its "On this Tab" marks again (workspace-study.ts) only when what is drawn on the specimen changed. */
function drawnChanged(): void {
  const key = drawnOnStage().join(' ');
  if (key !== drawnKey) { drawnKey = key; window.dispatchEvent(new Event('hooke-stage-drawn')); }
}

function handleMessage(message: ServerMessage): void {
  if (workspaceSession?.message(message)) { madeToday.visibilityChanged(); return; }
  if (['chat-action', 'research', 'result', 'day', 'cell-layer', 'app-notice', 'session'].includes(message.type)) { madeToday.changed(); window.dispatchEvent(new CustomEvent('hooke-results-changed')); }
  const beforeTracksLayer = view.tracksLayer;
  view.observeOverlayMessage(message);
  switch (message.type) {
    case 'agent-connections': // an outside agent asked to connect, or the list changed (ADR 0026)
      agentConnections.message(message);
      break;
    case 'onboarding-changed': // an agent moved the walkthrough on (the door's example): its card rereads where it is
      void exampleGuide.agentAdvanced();
      break;
    case 'presentation-admitted': // WorkspaceSession owns placement correlation.
    case 'workspace-ready': // Link owns correlated settlement; runtime installation is separate.
      break;
    case 'action-placed': // a This Tab run on the Stage's still: its result kept for the specimen, or placed on its Tab
      actionsPanel.placed(message.run, message.placed);
      break;
    case 'action-settled': // a stopped Actions run ended: its own row says how
      actionsPanel.settled(message.run, { outcome: message.outcome, text: message.text });
      loadPills.refresh(); // a load its run carried and still going now has its own pill
      void spending.refreshTotal(); // a run that ended may have spent
      if (document.body.dataset.workspaceStudy === 'true') {
        if (studyActionOwner === message.run) studyActionOwner = null;
        window.dispatchEvent(new CustomEvent('workspace-study-settled', { detail: message }));
      }
      break;
    case 'hello':
      connectionStart.hello(); // its first `session` message is a start (no `session-cleared` comes for one)
      loadPills.clear(); // loads that ended while the connection was down would stay on the strip
      reporter.reset(); // a new connection: changes applied before it are not this server's to confirm
      chat.apply(message); // (re)connected: the server replays the conversation next
      health = message.health;
      // A studio that connects is a start: what plays is kept. The returning card still covers the stage after a
      // dropped connection (the server forgets it when the last studio goes), so it is said again.
      if (viewHeld) connection.send({ type: 'view', held: true });
      renderStatus();
      break;
    case 'scene':
      view.setScene(message.objects);
      view.selected = message.selected;
      actionsPanel.selectionChanged();
      card.setObjects(message.objects);
      slideMap.setObjects(message.objects);
      magnifier.objectGone(new Set(message.objects.map((o) => o.id)));
      objectCount = message.objects.filter((o) => o.kind !== 'cell').length;
      break;
    case 'magnifier':
      magnifier.render(message); resultFocus.sync();
      break;
    case 'candidates':
      view.setCandidates(message.frameId, message.items);
      break;
    case 'day':
      if (day?.id !== message.id || day?.active !== message.active) askStatus.hidden = true;
      renderDay(message);
      setupNotice.context(message.localOnly === true, home.isOpen());
      madeToday.setSessionStart(message.sessionStart ?? null);
      updateAskAvailability();
      chat.apply(message); // the chat shows the active conversation
      break;
    case 'session-cleared': // the shared View dispatcher retired old overlay identities; Made in this session is another's
      madeToday.sessionChanged();
      measurementsPage.sessionChanged();
      connectionStart.cleared();
      sessionOpened(message); // the view follows the session (session-view.ts)
      break;
    case 'thread': // the active conversation as it was shown: replaces the log
    case 'chat':
    case 'chat-action':
    case 'app-notice':
    case 'activity': // a line of Activity: a request's record, a click, a studio action, a setting, a model, a researcher
    case 'execution':
      if (message.type === 'activity') { loadPills.apply(message); actionsPanel.activity(message); } // a model loading: its pill on the Working strip; a run's latest line under its clock
      chat.apply(message);
      break;
    case 'execution-list':
      executionRecording.apply(message.enabled);
      chat.apply(message);
      break;
    case 'hint':
      if (message.tone === 'error') diagnostic(message.text);
      else showHint(message.text);
      break;
    case 'notes':
      notes.apply(message);
      break;
    case 'research':
    case 'research-list':
      research.apply(message);
      card.render(); // suggestions and notes show on the selected outline
      viewer.render();
      renderResearchers();
      chat.researchChanged(); // a researcher a request here started: its card under that request
      notifyStudy(); // a new researcher's Tab lights with its strip pill, not up to 350 ms after it
      // A researcher that ended may have spent: the strip's Spending button reads today's total again.
      for (const s of message.type === 'research' ? [message.session] : message.sessions) {
        const was = researchStatus.get(s.rid); researchStatus.set(s.rid, s.status);
        if (was !== undefined && was !== s.status && !isWorking(s)) { void spending.refreshTotal(); break; }
      }
      break;
    case 'session':
      if (viewSid && viewSid !== message.id) measurementsPage.sessionChanged();
      renderSession(message);
      if (activityRequestedSession) {
        const requested = activityRequestedSession; activityRequestedSession = '';
        if (requested === message.id) { navigateStudio('#'); showActivity(true); }
      }
      exampleGuide.sessionChanged();
      void measurementsPage.sessionAvailable();
      viewSid = message.id; // a studio that connects learns its session here (no `session-cleared`: a start)
      if (connectionStart.session(viewHeld ? homeReason : null)) {
        // A studio loading, or reconnecting after a restart: the start's landing (session-view onStart and its guards),
        // never while the user is choosing what to look at (the new session's home stays as it was).
        sessionOpened({ type: 'session-cleared', session: message.id, why: 'start' }, { connection: true });
      } else if (message.id !== sourcesModel.session) { // its sources: the side bar's Sources, the stage's name
        sourcesModel.setSession(message.id);
        void sourcesModel.load();
      }
      break;
    case 'sessions': // every session, newest first: the chip's menu and the side bar
      sessionsModel.applyList(message);
      break;
    case 'slidemap':
      slideMap.apply(message);
      break;
    case 'rig':
      datasetsView.setRig(message);
      break;
    case 'dataset': {
      const changed = dataset?.on !== message.on || dataset?.id !== message.id || dataset?.name !== message.name;
      const was = dataset?.check;
      const live = dataset !== null; // a change seen while this page is open (not the state found at a reload)
      const stopped = (dataset?.on && !message.on) || (message.check?.state === 'saved' && dataset?.check?.state !== 'saved');
      dataset = message;
      if (stopped) { // its camera video joins this session's sources on its own (the library's scan is kept a few seconds)
        void sourcesModel.load();
        window.setTimeout(() => void sourcesModel.load(), 6000);
      }
      renderRecord();
      const light = message.check?.state;
      const problem = message.check?.problems[0] ?? '';
      // Said once when it changes: a problem (and again if its reason changes), or what was saved.
      if (live && light === 'problem' && problem && (was?.state !== 'problem' || was.problems[0] !== problem)) {
        showHint(`Data: ${problem}`, true);
      } else if (live && light === 'saved' && was?.state !== 'saved' && message.check.last) {
        showHint(savedText(message.check.last));
      }
      // The tab follows: on a start, a stop or a name, and every few seconds while one collects.
      if ((changed || message.on) && !datasetsPanel.hidden) void datasetsView.load();
      break;
    }
    case 'presenter':
      viewer.apply(message.shelfOnly ? { ...viewer.state, shelfRemoved: message.shelfRemoved } : message);
      resultFocus.sync();
      renderResearchers();
      break;
    case 'capabilities':
      capabilityPage.update(message); // everything the app can do (capabilities.py)
      modelsPage.update(message); // every model, why it is on or off (catalog_view.py)
      noteCapabilities(message); // the small-models offer's rows and progress (starter-models.ts)
      renderSetup(message);
      for (const p of pickers) p.render(message); // both menus draw this list too: one source, nothing to keep in step
      renderComposerModel(message);
      actionsPanel.setCapabilities(message); updateAskAvailability(); // the Actions panel's rows and whether each can run here
      { const names = new Map((message.models ?? []).map((row) => [row.id, row.name] as [string, string]));
        if (JSON.stringify([...names]) !== JSON.stringify([...catalogNames])) { catalogNames = names; research.render(); } }
      downloadPills.update(message.setup?.downloads ?? [], message.models ?? []); // Ready only when its model is
      if (!actionToolsAsked) { actionToolsAsked = true; void loadActionTools().finally(() => { actionToolsAsked = false; }); }
      break;
    case 'settings':
      view.outlineStyle = message.settings.style ?? 'classic';
      voice.link = message.settings.voicelink === 'webrtc' ? 'webrtc' : 'relay';
      view.outlineColor = OUTLINE_COLORS[message.settings.outlineColor ?? 'pink'] ?? OUTLINE_COLORS.pink;
      outlineColourName = message.settings.outlineColor ?? 'pink';
      break;
    case 'scale':
      view.scale = message;
      appObjective = message.objective;
      renderStatus();
      break;
    case 'coach':
      view.coach = message;
      if (message.advice && message.advice !== lastAdvice) showHint(message.advice);
      lastAdvice = message.advice;
      break;
    case 'thinking':
      chat.thinking(message.on, message.phase);
      break;
    case 'arena': {
      const wasEnabled = arena?.enabled;
      arena = message;
      comparePicker.render(message);
      renderBlindSwitch();
      if (message.trial && message.trial.id !== trial?.id) showTrial(message.trial); // e.g. after a reload
      if (!message.trial && trial && !trial.voted) {
        // The comparison was dropped (moved to another test without voting).
        trial = null;
        chat.endTrial();
        verdict.hidden = true;
        setSolo(null);
      }
      blindBar.hidden = !message.enabled;
      if (!message.enabled) {
        trial = null;
        chat.endTrial();
        verdict.hidden = true;
        setSolo(null);
      }
      talkLabel.textContent = talkLabelText(voice.state);
      if (wasEnabled !== message.enabled && (wasEnabled !== undefined || message.enabled)) {
        showHint(message.enabled ? BLIND_HINT : 'Click anything in the view to outline it.');
      }
      break;
    }
    case 'trial':
      showTrial(message.trial);
      break;
    case 'verdict':
      showVerdict(message.trialId, message.winner, message.reveal);
      break;
    case 'result': // a request's own reply has already settled it (protocol.ts Link)
      if (message.for === 'click' && zoomNextOutline) {
        zoomNextOutline = false;
        if (message.ok && typeof message.objectId === 'string') magnifier.zoom(message.objectId);
      }
      if (message.for === 'cells') cellsButton.disabled = false;
      if (message.for === 'ask') {
        chat.settled(); // the request is over: its slot goes (its answer, if any, arrived before this reply)
        const failure = askRequests.settle(message.requestId, message.ok, message.error, askScope(), question.value);
        updateAskAvailability();
        // Whoever sent this request (a New researcher Tab) learns whether the Assistant accepted it.
        // `refused`: the answer's start_researcher refusal (a missing key, the cap…), so that Tab says it at once.
        const runs = (message as { actions?: ToolRun[] }).actions ?? [];
        const refused = runs.some((run) => run.tool === 'start_researcher' && run.ok) ? null
          : runs.find((run) => run.tool === 'start_researcher' && run.ok === false)?.summary ?? null;
        document.dispatchEvent(new CustomEvent('hooke-ask-settled', { detail: { requestId: message.requestId, ok: message.ok, error: failure?.error ?? null, refused } }));
        if (failure) {
          diagnostic(failure.error, { day: failure.day, thread: failure.thread, deduplicate: false });
          if (failure.current) {
            composer.set(failure.draft);
            askStatus.hidden = false; askStatus.textContent = failure.status;
          }
        }
      } else if (!message.ok && message.error) diagnostic(message.error);
      else if (message.for === 'showing' && typeof message.summary === 'string') showHint(capitalise(message.summary));
      else if (message.for === 'cells') {
        // Plain words (Amendment 4): the count is of outlines the cell finder drew, not a cell type; ids stay one step away.
        showHint(`${message.count} outlined by the cell finder. Each is shown once it is checked in place; click one for its measurements.`);
      }
      break;
    case 'showing': // Show me: a finding being shown (showing.ts)
      showingBar.set(message);
      break;
    case 'trails': // where followed outlines went on their own (trail.ts)
      view.setTrails(message);
      break;
    case 'population': // what a cell finder or a matcher found (population.ts)
      measurementsPage.evidenceChanged(message.id, { session: message.session ?? '', revision: message.revision });
      liveLayerControls.syncCells();
      break;
    case 'layer': {
      liveLayerControls.syncCells();
      drawnChanged();
      actionsPanel.layerChanged(); // a finished run's outlines now drawn on the specimen: its footer says so
      // When none of the shown members can be drawn, say why once (the population stays as evidence).
      const why = message.population && !message.verified.length ? message.why ?? '' : '';
      // Where they are kept is said as itself ("Kept on the original image; open the saved result…"); every other reason
      // is a cause, said after what it caused.
      // Said once for that population and reason, across reloads too (this tab's sessionStorage): it came back on every
      // page load and never cleared. Its × still dismisses it; no timer (hints stay until replaced or dismissed).
      if (why && why !== layerWhy && !saidOnce(`${message.population}:${why}`)) layerWhyHint = showHint(/^kept\b/i.test(why) ? `${why[0].toUpperCase()}${why.slice(1)}.` : `Outlines are not drawn on the Stage: ${why}.`);
      else if (!why && message.verified.length && hintRevision === layerWhyHint) showHint('The outlines are drawn again: checked in place.');
      layerWhy = why;
      break;
    }
    case 'motion-tracks': // motion tracks kept as evidence (motiontracks.ts)
      liveLayerControls.syncTracks();
      break;
    case 'tracks-layer': {
      liveLayerControls.syncTracks();
      drawnChanged();
      // When the shown tracks cannot be drawn on the live view, say why once (they stay kept, and are shown
      // on the view they were measured on).
      const why = message.tracks && !message.verified ? message.why : '';
      if (why && why !== tracksWhy && beforeTracksLayer?.tracks === message.tracks) {
        showHint(`The motion tracks are not drawn now: ${why}. They are kept, and come back once checked in place.`);
      }
      tracksWhy = why;
      break;
    }
    case 'member-selected':
      view.selectedMember = message.member;
      if (message.member && message.words) showStateHint('member', message.words);
      else clearStateHint('member');
      break;
    case 'present': // a change of something on screen, reported as applied once drawn (screen.ts)
      applyPresent(message);
      break;
    case 'screen-ask': // the server waits on something on screen: a report now (a hidden tab draws no frames)
      reporter.askNow();
      if (document.hidden) reportWorkspace(performance.now());
      break;
    case 'workspace-study-delay':
      if (document.body.dataset.workspaceStudy === 'true') window.dispatchEvent(new CustomEvent('workspace-study-delay', { detail: message }));
      break;
    case 'workspace-tabs': {
      const detail:{session:string;action:Record<string,unknown>;check:()=>boolean;result?:Promise<Record<string,unknown>>}={session:message.session,action:message.action,check:()=>reporter.matchesStructuralPin(message.workspacePin)};
      window.dispatchEvent(new CustomEvent('workspace-tabs-request',{detail}));
      void (detail.result??Promise.resolve({ok:false,error:'The Project workspace is not open.'})).then(result=>{
        connection.send({type:'workspace-tabs-result',id:message.id,session:message.session,workspacePin:message.workspacePin,result});
      });
      break;
    }
    case 'reader': // the Assistant opens (or closes) a Data item in the reader on this Tab (ticket 66, D6; data_reader.py)
      // workspace-study.ts opens it as a Data row's click does; done is the screen report showing it, so report now.
      window.dispatchEvent(new CustomEvent('hooke-reader-request', { detail: { ...message, check: () => reporter.matchesStructuralPin(message.workspacePin) } }));
      reporter.askNow();
      if (document.hidden) reportWorkspace(performance.now());
      break;
    case 'open-page': { // the agent opens a page (open_models): done once this studio shows it, and said whether in view
      navigateStudio(message.page);
      // '#' is the workspace, whose address has no hash at all (navigateStudio).
      const here = location.hash === message.page || (message.page === '#' && !location.hash);
      const shown = here && (document.body.dataset.workspaceStudy === 'true'
        ? document.body.dataset.studyOverlay === message.page || message.page === '#' || document.body.dataset.studyDrawer === 'true' && (message.page.startsWith('#research') && document.querySelector<HTMLElement>('.study-drawer')?.dataset.section === 'research') : !message.page.startsWith('#models') || !modelsPanel.hidden);
      connection.send({ type: 'page-opened', id: message.id, page: location.hash, shown, visible: shown && !document.hidden });
      break;
    }
    case 'screen-look': // look_at_screen: a picture of the workspace as composed now
      // Canonical acquisition never falls back to a DOM/media approximation.
      if (message.schema !== 'workspace-capture-v1') connection.send({ type: 'screen-image', id: message.id, jpeg: composeScreen() });
      break;
    case 'sources': // the open session's sources changed (ADR 0022): the side bar's Assets draws them
      // Something was put on stage, here or in another studio: a new session's home has had its answer.
      if (sourcesModel.apply(message) && message.onStage && home.mode === 'choose') closeHome();
      annotatedRecording.beforeFrame();
      renderStatus();
      break;
    default:
      assertNever(message); // a new kind of server message must be handled here
  }
}

const BLIND_HINT = 'During a comparison, keys 1 to 4 show one option alone and 0 shows them all.';

function showTrial(next: Trial): void {
  if (trial?.id !== next.id) {
    verdictNote.value = '';
    verdictReveal.hidden = true;
    verdictReveal.replaceChildren();
    setSolo(null);
  }
  trial = next;
  verdict.hidden = false;
  verdict.dataset.state = 'voting';
  verdictNote.hidden = false;
  const waiting = next.arms.some((a) => !a.done);
  const noun = next.kind === 'ask' ? 'Answers' : 'Outlines';
  verdictQuestion.textContent = waiting
    ? `${noun} arriving: ${next.arms.filter((a) => a.done).length} of ${next.arms.length}`
    : next.kind === 'ask' ? 'Which answer is best?' : 'Which outline is best?';
  if (next.kind === 'ask') chat.thinking(waiting);
  const buttons: HTMLButtonElement[] = next.arms.map((arm) => {
    const button = choiceButton(arm.letter, arm.failed ? `${arm.letter} failed` : arm.letter, ARM_DASH[arm.letter]);
    button.disabled = !arm.done || arm.failed;
    button.addEventListener('pointerenter', () => setSolo(arm.letter));
    button.addEventListener('pointerleave', () => setSolo(null));
    button.addEventListener('focus', () => setSolo(arm.letter));
    button.addEventListener('blur', () => setSolo(null));
    return button;
  });
  buttons.push(choiceButton('tie', 'Tie'), choiceButton('none', 'None right'));
  for (const button of buttons) button.addEventListener('click', () => vote(button.dataset.winner!));
  verdictChoices.replaceChildren(...buttons);
  setSolo(view.solo);

  chat.arms(next);
}

/** Show one arm alone (keys 1-4, or hovering its vote button); null shows them all. */
function setSolo(letter: string | null): void {
  view.solo = letter;
  for (const button of verdictChoices.querySelectorAll<HTMLButtonElement>('button')) {
    if (button.dataset.winner && button.dataset.winner.length === 1) button.dataset.solo = button.dataset.winner === letter ? 'true' : '';
  }
}

function choiceButton(winner: string, text: string, dash?: number[]): HTMLButtonElement {
  const button = document.createElement('button');
  button.type = 'button';
  button.dataset.winner = winner;
  if (dash) {
    // The same line pattern the arm is drawn with, so the button and outline match.
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    svg.setAttribute('viewBox', '0 0 28 6');
    svg.setAttribute('aria-hidden', 'true');
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
    line.setAttribute('x1', '2');
    line.setAttribute('x2', '26');
    line.setAttribute('y1', '3');
    line.setAttribute('y2', '3');
    if (dash.length) line.setAttribute('stroke-dasharray', dash.map((d) => d * 0.6).join(' '));
    svg.append(line);
    button.append(svg);
  }
  button.append(text);
  return button;
}

function vote(winner: string): void {
  if (!trial || trial.voted) return;
  connection.send({ type: 'vote', trialId: trial.id, winner, note: verdictNote.value });
}

// Blind test mode: its own switch in the header, off by default. While it's on the whole
// studio says so (amber switch, framed view, test strip under the view).
blindSwitch.addEventListener('click', () => {
  connection.send({ type: 'arena', arena: { enabled: !arena?.enabled } });
});
element<HTMLButtonElement>('compare-open').addEventListener('click', () => compareDialog.showModal());
element<HTMLButtonElement>('compare-close').addEventListener('click', () => compareDialog.close());
compareDialog.addEventListener('click', (event) => {
  if (event.target === compareDialog) compareDialog.close();
});

function renderBlindSwitch(): void {
  const on = arena?.enabled ?? false;
  blindSwitch.setAttribute('aria-checked', String(on));
  blindState.textContent = on ? 'On' : 'Off';
  document.body.dataset.blind = on ? 'on' : '';
}

element<HTMLButtonElement>('scores-open').addEventListener('click', () => {
  scoresDialog.showModal();
  void refreshScores();
});
element<HTMLButtonElement>('scores-close').addEventListener('click', () => scoresDialog.close());
scoresDialog.addEventListener('click', (event) => {
  if (event.target === scoresDialog) scoresDialog.close(); // click on the backdrop
});

const KIND_TITLES: Record<string, string> = { outline: 'Outlines', ask: 'Answers' };

async function refreshScores(): Promise<void> {
  let scores: Scores;
  try {
    scores = (await connection.request('scores', {})).scores ?? { votes: 0, kinds: [] };
  } catch (error) {
    scoresBody.textContent = `Couldn't load scores: ${(error as Error).message}`;
    return;
  }
  if (!scores.votes) {
    const empty = document.createElement('p');
    empty.className = 'scores-empty';
    empty.textContent = 'No votes yet. Click something or ask a question with blind mode on, then pick the best answer.';
    scoresBody.replaceChildren(empty);
    return;
  }
  scoresBody.replaceChildren(
    ...scores.kinds.map((k) => {
      const section = document.createElement('section');
      const title = document.createElement('h3');
      title.textContent = `${KIND_TITLES[k.kind] ?? k.kind}, ${k.votes} ${k.votes === 1 ? 'vote' : 'votes'}`;
      const table = document.createElement('table');
      const head = table.createTHead().insertRow();
      for (const text of ['Option', 'Won', 'Win rate', 'Tied', 'None right', 'Median time']) {
        const th = document.createElement('th');
        th.textContent = text;
        head.append(th);
      }
      const body = table.createTBody();
      for (const o of k.options) {
        const row = body.insertRow();
        if (o.recommended) row.dataset.recommended = '';
        const name = row.insertCell();
        name.textContent = o.label;
        if (o.recommended) {
          const tag = document.createElement('span');
          tag.className = 'scores-pick';
          tag.textContent = 'Recommended';
          name.append(' ', tag);
        }
        row.insertCell().textContent = `${o.wins} of ${o.trials}`;
        row.insertCell().textContent = `${Math.round((o.wins / o.trials) * 100)}%`;
        row.insertCell().textContent = String(o.ties);
        row.insertCell().textContent = String(o.none);
        row.insertCell().textContent = o.medianMs === null ? '' : `${(o.medianMs / 1000).toFixed(o.medianMs < 1000 ? 2 : 1)} s`;
      }
      const note = document.createElement('p');
      note.className = 'scores-note';
      note.textContent = k.recommended
        ? 'Recommended: best win rate; near ties go to the faster option.'
        : `A recommendation appears once each option has been in ${k.minTrials} comparisons.`;
      section.append(title, table, note);
      return section;
    }),
  );
}

function showVerdict(trialId: string, winner: string, reveal: Record<string, Reveal>): void {
  if (trial?.id !== trialId) return;
  trial = { ...trial, voted: true };
  talkLabel.textContent = talkLabelText(voice.state);
  setSolo(null);
  verdict.dataset.state = 'revealed';
  verdictQuestion.textContent =
    winner === 'tie' ? 'You called it a tie.' : winner === 'none' ? 'You said none were right.' : `You picked ${winner}.`;
  verdictChoices.replaceChildren();
  verdictNote.hidden = true;
  const lines = Object.entries(reveal).map(([letter, r]) => {
    const item = document.createElement('li');
    if (letter === winner) item.dataset.winner = '';
    const time = r.error ? 'failed' : r.ms === null ? '' : `${(r.ms / 1000).toFixed(r.ms < 1000 ? 2 : 1)} s`;
    item.textContent = `${letter} was ${r.label}${time ? `, ${time}` : ''}`;
    return item;
  });
  verdictReveal.replaceChildren(...lines);
  verdictReveal.hidden = false;
  chat.reveal(trialId, reveal, winner);
  showHint('Click something else, or ask again, for the next comparison.');
  if (scoresDialog.open) void refreshScores();
}

// Hold "h" to see the bare specimen: outlines and labels are hidden while the key is down.
document.addEventListener('keydown', (event) => {
  if (event.key !== 'h' || event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
  if ((event.target as HTMLElement).closest('input, textarea, select')) return;
  view.hideOverlays = true;
});
document.addEventListener('keyup', (event) => {
  if (event.key === 'h') view.hideOverlays = false;
});
window.addEventListener('blur', () => (view.hideOverlays = false));

// F flags this moment for later (a demo's good and bad moments, lined up with the recordings by
// time); Shift+F asks for a note first. Never while typing.
document.addEventListener('keydown', (event) => {
  if (event.key.toLowerCase() !== 'f' || event.repeat || event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target as HTMLElement;
  if (target.closest('input, textarea, select, [contenteditable="true"]')) return;
  const note = event.shiftKey ? (window.prompt('What happened here?') ?? '').trim() : '';
  if (event.shiftKey && !note) return;
  connection.send({ type: 'flag', note, frameId: view.header?.id });
  showHint(`Flagged ${new Date().toLocaleTimeString()}${note ? `: ${note}` : ''}`);
});

document.addEventListener('keydown', (event) => {
  if (!trial || event.metaKey || event.ctrlKey || event.altKey) return;
  const target = event.target as HTMLElement;
  if (target.closest('input, textarea, select')) return;
  if (event.key === '0') setSolo(null);
  const index = Number(event.key) - 1;
  if (index >= 0 && index < trial.arms.length) setSolo(view.solo === trial.arms[index].letter ? null : trial.arms[index].letter);
});

talkButton.addEventListener('click', () => {
  if (voice.state === 'live' || voice.state === 'connecting') {
    voice.stop();
    return;
  }
  if (!workspaceSession || !workspaceReady) { showHint('Reconnect to the studio before starting Talk.'); return; }
  if (scientificAskSelection()) { showHint('Talk uses the Stage. Remove the saved attachment before starting Talk.'); return; }
  void voice.start(workspaceSession.voiceTarget());
});

// While connecting the word is "Cancel" (its accessible name says Cancel Talk, and the status above says it is
// connecting): the composer's row keeps the Assistant's model named in full beside it (hooke-words, 2026-10-25).
function talkLabelText(state: VoiceState): string {
  if (state === 'live') return 'End Talk';
  if (state === 'connecting') return 'Cancel';
  return 'Talk';
}

function renderVoiceState(state: VoiceState, detail?: string): void {
  talkButton.setAttribute('aria-pressed', String(state === 'live'));
  talkButton.dataset.state = state;
  talkLabel.textContent = talkLabelText(state);
  if (state === 'connecting') talkButton.setAttribute('aria-label', 'Cancel Talk'); else talkButton.removeAttribute('aria-label');
  voiceControls.hidden = state === 'off';
  voiceMute.hidden = state !== 'live';
  voiceStatus.textContent = state === 'connecting' ? detail ?? 'Connecting microphone and voice provider…'
    : state === 'error' ? detail ?? 'Voice needs attention. Open Activity.' : voice.muted ? 'Microphone muted' : 'Listening';
  talkButton.title = state === 'live' || state === 'connecting' ? 'End Talk (delegated work continues)' : 'Talk about the view (uses the microphone)';
  if (state === 'error' && detail) chat.system(detail);
  if (state !== 'live') {
    setVoiceWorking(false);
    chat.voice.stopped();
    clearStateHint('voice');
  }
}

function handleVoice(event: VoiceEvent): void {
  switch (event.kind) {
    case 'status': {
      const phase = String(event.data?.phase ?? '');
      const words: Record<string,string> = {listening:'Listening · speak when ready', processing:'Heard you · waiting for a response', speaking:'Speaking', muted:'Microphone muted', reconnecting:'Reconnecting audio…'};
      voiceStatus.textContent = (words[phase] ?? 'Voice needs attention') +
        (voice.muted && phase !== 'muted' ? ' · microphone muted' : '');
      if (phase === 'listening') showStateHint('voice', 'Listening. Ask about the view or your work.');
      break;
    }
    case 'diagnostic': {
      const messages: Record<string,string> = {
        'capture-stalled':'Microphone audio stopped. Check the selected microphone, then end Talk and reconnect.',
        'playback-blocked':'The reply arrived, but audio playback was blocked. Read the reply here; check audio output before reconnecting.',
        'response-wait':'No voice response yet. Your request will not be sent again automatically. Check Activity or end Talk and retry explicitly.',
      };
      const message = messages[String(event.data?.stage ?? '')];
      if (message) { voiceStatus.textContent = message; chat.system(message); }
      break;
    }
    case 'model':
      showStateHint('voice', `Connecting to ${String(event.data?.label ?? 'the voice model')}…`);
      break;
    case 'input_transcript': // interim transcripts replace each other until the final one
      if (!event.data?.journaled) chat.voice.heard(event.text, !!event.data?.interim);
      break;
    case 'output_transcript':
      if (!event.data?.journaled) chat.voice.said(event.text);
      break;
    case 'interrupted':
      break; // the user talking over the voice ends neither line; its words may go on
    case 'turn_complete':
      if (endsVoiceTurn(event)) chat.voice.turnEnd();
      break;
    // The voice handed something to the app: the Talk dot breathes while it works (the chat
    // stays for what was said; the view shows what was done).
    case 'tool_call':
      setVoiceWorking(true);
      break;
    case 'tool_result':
      if (!event.data?.interim) setVoiceWorking(false);
      break;
    case 'error':
      chat.system(`Voice: ${String(event.data?.message ?? 'something went wrong')}`);
      break;
  }
}

let workingTimer = 0;
function setVoiceWorking(on: boolean): void {
  window.clearTimeout(workingTimer);
  if (on) {
    talkButton.dataset.working = '';
    workingTimer = window.setTimeout(() => delete talkButton.dataset.working, 20000);
  } else {
    delete talkButton.dataset.working;
  }
}

/** A capability's run page (a run pill, a researcher's Used row): its task in Tools, or its Models entry. */
function openRunPage(capability: string): void {
  if (actionsPanel.has(capability)) actionsPanel.open(capability);
  else if (capability.startsWith('catalog:')) navigateStudio(`#models/${encodeURIComponent(capability.slice(8))}`);
  else actionsPanel.open(capability);
}
function ask(text: string, capabilityTrial?: string): boolean {
  const trimmed = text.trim();
  if (!trimmed) return false;
  const blocked = askBlocked();
  if (blocked) { askStatus.hidden = false; askStatus.textContent = blocked; return false; }
  // The frame on screen when you asked is the one the answer is about.
  try {
    if (!workspaceSession) throw new Error('Workspace connection is unavailable');
    const selected = scientificAskSelection();
    const id = workspaceSession.ask(trimmed, {capabilityTrial, ...(selected ? { scientificInput: selected.input } : {})});
    askRequests.started(id, trimmed, askScope()); updateAskAvailability();
    document.dispatchEvent(new CustomEvent('hooke-ask-sent', { detail: { requestId: id, text: trimmed } }));
    askStatus.hidden = true;
    chat.sent(); // your own send: the log follows to its end
    return true;
  } catch (error) {
    requestSendFailed(error);
    return false;
  }
}

function diagnostic(error: unknown, options: LocalNoticeOptions = { deduplicate: true }): () => void {
  chat.system(askError(error), options);
  return () => {}; // Activity retains local occurrences after a transient condition retires.
}
function requestSendFailed(error: unknown): void {
  updateAskAvailability(); diagnostic(error, { deduplicate: false });
  askStatus.hidden = false;
  askStatus.textContent = `Message not sent: ${askError(error)}`;
}
askForm.addEventListener('submit', (event) => {
  event.preventDefault();
  if (ask(question.value)) composer.set('');
});

for (const button of starters.querySelectorAll<HTMLButtonElement>('button')) {
  button.addEventListener('click', () => {
    // A starter either asks at once, or puts the start of a request in the box to finish (Start a researcher…).
    const fill = button.dataset.fill;
    if (fill === undefined) { ask(button.dataset.ask ?? button.textContent ?? ''); return; }
    composer.set(fill); question.focus(); question.setSelectionRange(fill.length, fill.length);
  });
}

function showHint(text: string, isError = false): number {
  stateHints.clear(); // a newer hint replaces a state's own: its clear no longer owns the line
  renderHint(hint, text, isError);
  return ++hintRevision;
}

/** A hint about a state (the selected cell, the voice being on) is that state's: shown with it, cleared when it ends
 * (the user's notes of 2026-10-11: the cell's measurements and "Talking with GPT-Live" stayed after both had ended). */
const stateHints = new Map<string, () => void>();
function showStateHint(owner: string, text: string): void {
  stateHints.clear();
  const clear = renderHint(hint, text); // owner-scoped: a later hint makes it a no-op
  ++hintRevision;
  stateHints.set(owner, clear);
}
function clearStateHint(owner: string): void {
  stateHints.get(owner)?.();
  stateHints.delete(owner);
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1) + (text.endsWith('.') ? '' : '.');
}

async function pollHealth(): Promise<void> {
  try {
    const response = await fetch('/api/health', { cache: 'no-store' });
    health = (await response.json()) as Health;
  } catch {
    health = null;
  }
  renderStatus();
}

// --- camera and feed --------------------------------------------------------------------
// The studio is a live camera app. With no camera it says so and helps get it live; test
// footage is a deliberate switch in the camera menu, and is always marked as such.
const feed = new CameraFeed(() => {
  renderStatus();
  renderCameraPanel();
  if (!footagePanel.hidden) assetsView.render();
});
// Test footage (#footage, a developer setting): the bridge's footage library as a grid, and adding to it (assets.ts).
// All assets (#assets) is the library (library-page.ts, libraryPage above).
const footagePanel = element<HTMLElement>('footage');
const assetsPage = element<HTMLElement>('assets');
const tabAssets = element<HTMLAnchorElement>('tab-assets');
const tabSessions = element<HTMLAnchorElement>('tab-sessions');
const datasetsPanel = element<HTMLElement>('datasets');
const tabDatasets = element<HTMLAnchorElement>('tab-datasets');
const datasetsView = new DatasetsView(
  datasetsPanel,
  (id, name) => connection.request('dataset-rename', { id, name }),
  (id) => connection.request('dataset-reveal', { id }),
  (field, value) => connection.request('rig', { field, value }),
  (id) => connection.request('dataset-trash', { id }),
);
// Playing one of the user's own (a real microscope recording, a file added here) opens it as the asset it is, which
// puts it in this session's Sources (ADR 0022); anything else is test footage, which stands for the live camera.
const assetsView = new AssetsView(footagePanel, feed, async (name) => {
  const item = feed.library.find((i) => i.name === name);
  if (item && isOwnFootage(item)) {
    const asset = await assetFor(item);
    if (!asset) { showHint(`“${item.title}” is not in your assets yet. Try again in a moment.`, true); return; }
    navigateStudio('#'); // back to the live view, where it is put on stage
    if (!await openUserInput(asset.id, { asset, say: (text) => showHint(text, true) })) return;
    const revision = showHint(`“${asset.title}” is on stage, and one of this project’s sources.`);
    window.setTimeout(() => { if (hintRevision === revision) showHint(''); }, UNDO_MS); // said, then gone
    return;
  }
  stageChosen();
  const error = await feed.use(name);
  if (error) showHint(error, true);
  else {
    switchedByStudio();
    navigateStudio('#'); // back to the live view, where it now plays
    showHint('Test footage is playing into the live feed.');
  }
});
/** The library, fetched only while someone looks at it (the camera menu or the Assets view). */
function libraryWanted(): void {
  if (!cameraPanel.hidden || !footagePanel.hidden) void feed.loadLibrary();
}
let noticeKey = '';

// The camera control (#source) opens the camera's panel: its checklist, the phone's controls, Reconnect and the camera
// page (phonepanel.ts). Test footage is a developer setting, in the gear (#source-menu opens its menu).
const sourceMenu = element<HTMLButtonElement>('source-menu');
sourceMenu.addEventListener('click', () => { gear.close(); setCameraPanel(cameraPanel.hidden === true); });

const bridgeBase = () => bridgeAddress(feed.bridgeUrl);
const studyFeedSelector = document.body.dataset.workspaceStudy === 'true';
const phonePanel = new PhonePanel(element('phone-panel'), source, bridgeBase, (text, problem) => showHint(text, problem),
  { feedSelector: studyFeedSelector });
phonePanel.onDoctor = () => renderStatus();
phonePanel.onStage = () => {
  if (!isAssetFeed(feed.state)) return null;
  const title = sourcesModel.row(stageOf(feed.state, sourcesModel.state))?.title || feed.state?.footageTitle || 'a recording or picture';
  return studyFeedSelector ? `${feed.state?.playback?.still ? 'Saved image' : 'Prerecorded video'} · ${title}` : title;
};
phonePanel.goLive = () => void openUserInput('live');

function setCameraPanel(open: boolean): void {
  cameraPanel.hidden = !open;
  sourceMenu.setAttribute('aria-expanded', String(open));
  if (open) { renderCameraPanel(); void feed.refresh(true).then(libraryWanted); }
}

document.addEventListener('click', (event) => {
  const target = event.target as Node;
  sessionMenu.outside(target);
  if (cameraPanel.hidden || !target.isConnected || cameraPanel.contains(target) || sourceMenu.contains(target)) return;
  setCameraPanel(false);
});

const LOADING_HINT = 'Loading the outline model…';

// The workspace's camera control shows once what it would say is settled (connected, the bridge's health, what plays and
// the camera checklist read, or 4 s at the latest), then fades in: words that changed as those facts arrived ("Live",
// then the device, then "No camera") moved the Stage bar on opening (workspace-feel greeting 1: layout shift 0).
const cameraSettleBy = performance.now() + 4000;
let cameraSettled = !studyFeedSelector;
let cameraFactsAsked = false;
if (!cameraSettled) setTimeout(() => renderStatus(), 4100);
function renderStatus(): void {
  retryStartLanding();
  renderOpening();
  if (!cameraSettled && ((connected && !!health && !!phonePanel.doctor && !!feed.state) || performance.now() >= cameraSettleBy)) cameraSettled = true;
  if (source.dataset.settled !== String(cameraSettled)) source.dataset.settled = String(cameraSettled);
  const footage = feed.state?.source === 'footage' ? feed.state.footage : null;
  document.body.dataset.feed = footage ? 'footage' : '';
  // One of the user's own recordings or pictures (the server put it on stage: the bridge's `asset:<path>`), or test
  // footage (the camera menu's, which stands for the live camera).
  const asset = isAssetFeed(feed.state) || !!feed.choosing?.name.startsWith('asset:');
  const stageRow = isAssetFeed(feed.state) ? sourcesModel.row(stageOf(feed.state, sourcesModel.state)) : undefined;
  // What plays is named by its title everywhere (the one being switched to, at once).
  const playingTitle = feed.choosing?.title ?? (footage ? stageRow?.title || feed.state?.footageTitle || footage.split('/').pop() || footage : '');
  const still = !feed.choosing && !!feed.state?.playback?.still;
  // A picture on stage needs no player (Will, 2026-10-14: no pop-up over the stage); a recording keeps its playhead.
  feedBadge.hidden = (!footage && !feed.choosing) || (asset && still);
  feedBadge.dataset.still = still ? 'true' : '';
  feedBadge.setAttribute('aria-label', !asset ? 'Test footage playing' : still ? 'Picture on stage' : 'Recording on stage');
  // While one of the user's assets is on stage, the player says which (test footage is named on the chip alone).
  const label = asset ? playingTitle : '';
  feedLabel.textContent = label;
  feedLabel.hidden = !label;
  // Its name is the camera chip; the player says it to a screen reader and on hover.
  playhead.setAttribute('aria-label', playingTitle ? `Position in ${playingTitle}` : 'Position in the recording');
  feedBadge.title = playingTitle;
  // What it was filmed with, when known; "Use its optics" sets the app's objective as the objective
  // menu does, so the app still only knows what a user on the microscope would tell it.
  const playingItem = feed.choosing ?? feed.library.find((i) => i.name === footage) ?? null;
  const optics = playingItem?.optics ?? null;
  if (optics && opticsLine(optics)) feedBadge.title = `${playingTitle}: ${opticsLine(optics)}`;
  const objective = optics?.objective ? Math.round(optics.objective) : null;
  useOptics.hidden = !objective || objective === appObjective;
  useOptics.textContent = objective ? `Use its optics (${objective}x)` : 'Use its optics';
  useOptics.dataset.objective = objective ? String(objective) : '';
  useOptics.dataset.fieldNumber = optics?.fieldNumber ? String(optics.fieldNumber) : '';
  renderPlayhead();

  // The live camera's state, for the empty stage's camera.
  const live = liveState({ connected, health: health?.state ?? null, feed: feed.state });
  home.setLive(live);
  renderSources();
  if (!connected || !health) {
    source.textContent = 'Not connected';
    const notice = workspaceConnectionNotice(connected, connectionObserved, !!health)!;
    if (studyFeedSelector) source.title = `Specimen · ${notice.title}`;
    source.dataset.state = 'offline';
    showNotice('orchestrator', notice.title, notice.lines);
    return;
  }
  feed.bridgeUrl = health.bridge.url;
  // The camera control waits for the checklist and for what plays: ask for both once the bridge's address is known,
  // rather than on their next 3 s polls (the first came before the address and could not be sent: hidden 3.6 s).
  if (!cameraSettled && !cameraFactsAsked) {
    cameraFactsAsked = true;
    if (!phonePanel.doctor) void phonePanel.refresh();
    if (!feed.state) void feed.refresh().then(libraryWanted);
  }
  // Landed on the camera and the bridge sees none: not live, though the app's last frame is not yet 3 s old (the view
  // has drawn nothing since the switch, so nothing of the last source is taken for the camera's).
  const noCamera = !view.header && feed.state?.source === 'camera' && (feed.state.cameraVisible ?? feed.state.bridge?.devicePresent) === false
    && !feed.busy;
  const isLive = health.state === 'live' && !noCamera;
  view.live = isLive;
  // The camera control: the camera's own state. While one of the session's assets is on stage the camera is not
  // (Live camera, in its menu, brings it back); the workspace's Stage bar names that asset on its left, so there the
  // control names the camera only (`named`). Test footage stands for the live camera (a developer setting).
  // No camera chosen (Will, 2026-10-15): pictures and videos only, a choice and never a fault.
  const cameraOff = health.state === 'no_camera' || phonePanel.doctor?.mode === 'off'
    || (phonePanel.doctor?.source === 'footage' && sourceTab(phonePanel.doctor.chosen) === 'none');
  const chosen = sourceTab(phonePanel.doctor?.chosen ?? phonePanel.doctor?.source);
  const words = cameraControl({ asset, still, named: !!studyFeedSelector, footage: !!(footage || feed.choosing), title: playingTitle, live: isLive, off: cameraOff,
    sourceLabel: SOURCES.find(([kind]) => kind === chosen)?.[1] ?? '',
    device: health.bridge.targetDevice || '', doctor: phonePanel.doctor ? cameraHeadline(phonePanel.doctor) : '', dot: phonePanel.doctor ? cameraDot(phonePanel.doctor) : undefined });
  source.dataset.state = words.state;
  source.textContent = words.text;
  // The Tools panel says what is on the Stage: the picture or recording by its title, else the camera's words.
  actionsPanel.setInput(asset ? playingTitle || words.text : words.text);
  source.title = studyFeedSelector ? `Specimen · ${words.title}` : words.title;

  if (emptyCameraHomeWanted({ cameraOff, asset, footage: !!footage, busy: !!feed.busy,
    holding: stageGate.holding(performance.now()), onStage: sourcesModel.state?.onStage ?? null })) {
    // No camera and nothing on stage: what will you look at? (a recording or picture), never a camera to fix.
    hideNotice();
    if (home.mode !== 'choose') openHome('empty-camera');
  } else if (home.mode === 'choose' || stageGate.holding(performance.now())) {
    // A new session is choosing what to look at, or another session is opening ("Opening “…”…"): the camera's state
    // waits until something is on stage.
    hideNotice();
    if (home.mode !== 'choose') hideNoPicture();
  } else if (isLive) {
    hideNotice();
    hideNoPicture();
  } else if (health.state === 'bridge_offline') {
    hideNoPicture();
    showNotice('bridge', 'The camera bridge is not running', ['Restart HookeUI (make start in Terminal).']);
  } else if (feed.busy === 'switching' || footage) {
    // Keyed by what starts: a session's opening may switch from one recording to another while this shows.
    hideNoPicture();
    showNotice(`switching|${playingTitle}`, footage || feed.choosing ? `Starting ${playingTitle}…` : 'Switching to the camera…', []);
  } else if (feed.state?.bridge.diagnosis) {
    // The bridge knows why (a silent phone, Desk View holding it, a stream not started) and what to do.
    const diagnosis = feed.state.bridge.diagnosis;
    showNoPicture(diagnosis.text, diagnosis.steps ?? [], cameraError());
  } else if (feed.state?.bridge.sourceKind === 'blackmagic' || feed.state?.bridge.sourceKind === 'other') {
    const pending = liveState({connected:true,health:health.state,feed:feed.state});
    showNoPicture(pending.words, [pending.help], cameraError());
  } else if (feed.state?.cameraVisible ?? feed.state?.bridge.devicePresent) {
    showNoPicture('iPhone found, waiting for its picture', PICTURE_STEPS, cameraError());
  } else {
    showNoPicture('No camera detected', CAMERA_STEPS, cameraError());
  }
  cellsButton.disabled = !health.gpu.ready;
  if (!health.gpu.ready && isLive) showHint(LOADING_HINT);
  else if (hint.textContent === LOADING_HINT) showHint('Ready. Click anything in the view to outline it, or ask.');
}

/** The playhead: where the recording is now (moved on from the last poll), unless it is being dragged. */
function renderPlayhead(): void {
  const playback = feed.state?.source === 'footage' && !feed.choosing ? feed.state.playback : null;
  // A picture is one held frame: nothing to play, pause or move in (its name stays on the player).
  const still = !!playback?.still;
  playPause.hidden = still;
  playhead.hidden = still;
  feedTime.hidden = still;
  playhead.disabled = !playback || feed.busy === 'switching';
  playPause.disabled = playhead.disabled;
  const paused = !!playback?.paused;
  playPause.dataset.paused = paused ? 'true' : '';
  playPause.setAttribute('aria-label', paused ? 'Play' : 'Pause');
  playPause.title = paused ? 'Play on from here' : 'Pause (the picture holds, as a slide at rest)';
  if (!playback) {
    feedTime.textContent = feed.choosing ? 'starting…' : '';
    return;
  }
  playhead.max = String(playback.duration);
  const at = scrubbing ? Number(playhead.value) : positionAt(playback, performance.now() - feed.playbackAt);
  if (!scrubbing) playhead.value = String(at);
  feedTime.textContent = `${clock(at)} / ${clock(playback.duration)}`;
  playhead.setAttribute('aria-valuetext', `${clock(at)} of ${clock(playback.duration)}`);
}
useOptics.addEventListener('click', () => {
  const objective = Number(useOptics.dataset.objective);
  if (!objective) return;
  const fieldNumber = Number(useOptics.dataset.fieldNumber) || undefined;
  connection.send({ type: 'scale', objective, ...(fieldNumber ? { fieldNumber } : {}) });
  showHint(`Objective set to ${objective}x, as the recording's notes say.`);
});
playPause.addEventListener('click', async () => {
  const paused = feed.state?.playback?.paused ?? false;
  playPause.disabled = true;
  const error = await feed.pause(!paused);
  if (error) showHint(error, true);
  renderPlayhead();
});
playhead.addEventListener('input', () => {
  scrubbing = true; // dragged: the time follows the hand, the recording moves on release
  renderPlayhead();
});
playhead.addEventListener('change', async () => {
  const seconds = Number(playhead.value);
  const error = await feed.seek(seconds);
  scrubbing = false;
  if (error) showHint(error, true);
  renderPlayhead();
});
setInterval(() => {
  if (!feedBadge.hidden) renderPlayhead();
}, 250);

/** What the camera capture last said when it failed (e.g. permission or a busy camera). */
function cameraError(): string {
  return (feed.state?.bridge.lastError ?? '').trim().slice(0, 240);
}

/** A notice over the stage when the app or the camera bridge is not there, or a source is starting. */
function showNotice(key: string, title: string, lines: string[]): void {
  notice.hidden = false;
  const asOf = `${key}|${title}`;
  if (asOf === noticeKey) return; // rebuilt only when it changes
  noticeKey = asOf;
  const head = document.createElement('h2');
  head.textContent = title;
  const children: HTMLElement[] = [head];
  if (lines.length) children.push(Object.assign(document.createElement('p'), { textContent: lines.join(' ') }));
  notice.replaceChildren(...children);
}

function hideNotice(): void {
  notice.hidden = true;
  noticeKey = '';
}

/** The live camera is on stage with no picture: the empty stage (session-home.ts) is what the stage shows, with the
 * camera's checklist leading (its first failing step and the one thing to do: the same list as the camera's panel and
 * the camera page). Nothing of what played before stays on the dark well, nor the hint about clicking a view there is
 * none of. */
function showNoPicture(title: string, lines: string[], detail: string): void {
  // Retire unavailable acquisition pixels, not retained result windows. The
  // workspace nests this canvas inside its specimen window; a legacy direct-child
  // CSS rule cannot be relied on to hide the previous source after a switch.
  view.clearFrame();
  const doctor = phonePanel.doctor;
  const failing = doctor?.items.find((item) => item.id === doctor.first);
  if (failing) {
    title = cameraHeadline(doctor!);
    // Not connected yet: the guide's steps, said as one line ("Connect your iPhone": close the app, lock it, near).
    lines = [doctor!.mode === 'connect' && doctor!.steps?.length ? doctor!.steps.join(' ') : failing.fix];
  }
  hideNotice();
  home.setTitle(currentSessionName());
  home.setProblem({ words: title, help: lines[0] ?? '', detail });
  // A saved result shown in its own window over the stage keeps the home closed (never an empty backdrop behind it); the
  // next camera check opens it once that window closes.
  const retained = [...(workspaceSession?.retainedViews() ?? []), ...resultFocus.retainedViews()];
  if (!cameraHomeWanted(retained)) { if (home.mode === 'camera') home.close(); }
  else if (home.mode !== 'camera') home.open('camera');
  element<HTMLElement>('stage').dataset.empty = 'true';
  document.body.dataset.stageEmpty = 'true';
}

function hideNoPicture(): void {
  if (home.mode === 'camera') home.close();
  element<HTMLElement>('stage').dataset.empty = '';
  document.body.dataset.stageEmpty = '';
}

/** The session's name, as its title says it ("Untitled session" for a new one of today). */
function currentSessionName(): string {
  const row = sessionsModel.currentRow();
  return row ? sessionName(row, sessionsModel.now()) : 'This project';
}

// --- the view follows the session (session-view.ts) and its sources (sources.ts) ----------------------------------
// A new session opens its home over the stage (session-home.ts), and the app is told the view is held, so nothing is
// looked at until the user chooses the live camera, a recording or a picture. A returning session lands on its last
// source (the server's `onStage`): a recording paused where it was left, a picture held, the camera live. Only a studio
// in view applies it, so two open studios never fight over the feed. Putting one of the user's assets on stage is the
// server's (`source`: it plays it on the camera bridge and keeps it with the session); the camera menu's test footage is
// still the studio's own switch, and stands for the live camera.
let viewSid = ''; // the session the view is for (from `session-cleared`, else `session`)
const capabilityTry = new CapabilityTry({
  session: () => viewSid, empty: () => home.isOpen() || !view.header,
  ready: () => view.isFresh() && !stageGate.holding(performance.now()) && workspaceReady,
  prepare: (id, option, empty) => connection.request('capability-try', { id, option, empty }) as Promise<PreparedTry>,
  open: (asset, session) => openUserInput(asset.id, { asset, session, paused: true }),
  now: () => performance.now(), wait: () => new Promise((resolve) => window.setTimeout(resolve, 50)),
  show: () => { location.hash = ''; }, guide: (words) => showHint(words),
  ask: (words, token) => ask(words, token),
  configure: (capability, role, option) => {
    window.dispatchEvent(new CustomEvent('workspace-study-section', { detail: 'tools' }));
    actionsPanel.open(capability, { role, option });
  },
  voice: async (token, option) => {
    if (!workspaceSession || !workspaceReady) throw new Error('Reconnect to the studio before starting Talk.');
    if (scientificAskSelection()) throw new Error('Talk uses the Stage. Remove the saved attachment before starting Talk.');
    await connection.request('capability-try-voice', { token });
    await voice.start({ ...workspaceSession.voiceTarget(), model: option });
  },
});
let openToken = 0; // the newest opening: an older one's read never applies
let viewHeld = false; // this studio told the app the live view is held (the home covers it)
let homeReason: 'chosen' | 'empty-camera' = 'chosen';
/** An opening seen while this studio was out of view: applied when it comes into view, unless what plays or what is
 * on the session's stage changed meanwhile (a studio in view applied it). */
const connectionStart = new ConnectionStart(); // a connection's first `session` message is its start (session-view.ts)
let pendingOpen: { token: number; why: SessionCleared['why']; sid: string; feedKey: string; stage: string | null; choice: number } | null = null;
/** What is being put on stage ('live' or an asset's id), said "Opening…" on its row until it settles. */
let staging: string | null = null;
let adding = false; // files from this Mac being added (one batch at a time)

/** Tell the app whether the home holds the live view (said once per change). */
function setViewHeld(held: boolean): void {
  element<HTMLElement>('stage').dataset.held = held ? 'true' : '';
  document.body.dataset.viewHeld = held ? 'true' : ''; // the hint under the stage ("Click anything in the view…") goes too
  if (held === viewHeld) return;
  viewHeld = held;
  connection.send({ type: 'view', held });
}

function openHome(reason: 'chosen' | 'empty-camera' = 'chosen'): void {
  if (reason === 'chosen') stageChosen();
  homeReason = reason;
  home.setTitle(currentSessionName());
  hideNoPicture();
  home.open('choose');
  setViewHeld(true);
  renderStatus();
}

/** The new session's choice is made (here or in another studio): the empty stage goes, and the app may look again.
 * What played behind it was not this session's choice, so its last picture goes too. (The empty stage of a camera with
 * no picture is renderStatus's: it stays while there is none.) */
function closeHome(): void {
  if (home.mode !== 'choose' && !viewHeld) return;
  if (home.mode === 'choose') { view.clearFrame(); home.close(); }
  setViewHeld(false);
  renderStatus();
}

/** What is on stage, on its row in the side bar's Assets. */
function renderSources(): void {
  sourcesSection.setStage(stageOf(feed.state, sourcesModel.state), staging);
}

/** Put a source on stage through the server (commands.py `source`): 'live', or one of the user's assets, held at `at`
 * when `paused` (a picture is always held). Opening associates: it is the session's from then on. The chip and the
 * player say it at once; a new session's home goes once it is there. Returns whether it is. */
async function putOnStage(target: string, options: { at?: number; paused?: boolean; asset?: AssetMeta; session?: string;
  say?: (text: string) => void } = {}): Promise<boolean> {
  stageChosen(); // something was put on stage: a start's landing never replaces it
  const asset = target === 'live' ? null : options.asset ?? sourcesModel.row(target) ?? null;
  // Already the camera: the session is told, and the camera is not restarted (a switch waits for its frames again).
  const already = target === 'live' && feed.state?.source === 'camera' && !feed.busy;
  staging = target;
  renderSources();
  // `session`: a landing's, so a session left meanwhile never has another's source put in it (commands.py `source`).
  const command = { open: target, ...(target !== 'live' && options.paused ? { paused: true } : {}),
    ...(asset && typeof options.at === 'number' && options.at > 0 ? { at: options.at } : {}), ...(already ? { switch: false } : {}),
    ...(options.session ? { session: options.session } : {}) };
  const error = already
    ? await connection.request('source', command).then(() => null, (e: Error) => e.message)
    : await feed.viaServer(asset ? { title: asset.title, seconds: asset.seconds } : null, () => connection.request('source', command));
  if (staging === target) staging = null;
  if (error) {
    // Said where it was asked (the chat says it too, from the command's reply); a home stays for another choice.
    if (options.say) options.say(plainError(error));
    else if (!adding) sourcesSection.setStatus(plainError(error), 'error');
    if (home.isOpen()) { home.setBusy(null); home.setStatus(plainError(error), 'error'); }
    renderSources();
    return false;
  }
  // The accepted source changes the title immediately. Retire its predecessor now,
  // rather than showing old pixels until bridge health catches up. Kept results
  // have their own windows and are unaffected.
  if (!already) view.clearFrame();
  // On stage: frames the bridge took before it are the last source's, never drawn (a landing settles the stage itself).
  if (!stageGate.holding(performance.now())) stageGate.settle(performance.now(), Date.now() / 1000);
  if (!adding && !options.say) sourcesSection.setStatus('');
  closeHome();
  renderSources();
  return true;
}

/** All assets' Add to this session: one of the user's recordings or pictures, or an example, put on stage held (as the
 * side bar's Assets does). Adding associates (ADR 0022's amendment): it is one of this session's assets from then on,
 * and the workspace shows it. */
async function addFromLibrary(asset: AssetMeta): Promise<boolean> {
  libraryPage.setStatus(`Adding “${asset.title}” to this project…`, 'busy');
  const added = await openUserInput(asset.id, { asset, paused: true, say: (text) => libraryPage.setStatus(text, 'error') });
  if (added) { libraryPage.setStatus(''); navigateStudio('#'); }
  return added;
}

/** All assets' Move to trash: out of the library and every session's list (the server's `asset-trash`). On stage here,
 * the live camera takes its place. */
async function trashAsset(asset: AssetMeta): Promise<boolean> {
  try {
    const reply = await connection.request('asset-trash', { asset: asset.id });
    if (reply.wasOnStage) void putOnStage('live');
    return true;
  } catch (error) {
    libraryPage.setStatus(error instanceof Error ? plainError(error.message) : 'It could not be moved to the trash.', 'error');
    return false;
  }
}

/** Files added on All assets: uploaded to the camera bridge (`/assets/upload`), into your assets only, never opened. */
async function uploadToLibrary(files: File[]): Promise<void> {
  for (const file of files) {
    try {
      await feed.upload(file, { title: '', specimen: '', microscope: true }, () => {});
    } catch (error) {
      throw new Error(`${file.name}: ${plainError(error instanceof Error ? error.message : String(error))}`);
    }
  }
}

/** The studio switched the feed itself (the camera menu's camera or test footage, the Assets page): test footage stands
 * for the live camera, so the session is told its stage is "live" (ADR 0022), and a new session's home goes. */
function switchedByStudio(): void {
  connection.send({ type: 'source', open: 'live', switch: false });
  closeHome();
}

/** × on a source: out of this session at once, with "Removed · Undo" for a few seconds (as an outline's removal). The
 * library keeps it, and what was made from it stays the session's. When it was on stage, what the server names next
 * (the newest other source, else the camera) goes there, held. */
async function removeSource(row: SourceRow): Promise<void> {
  sourcesModel.hide(row.id);
  let reply: Awaited<ReturnType<typeof connection.request<'source-remove'>>>;
  try {
    reply = await connection.request('source-remove', { asset: row.id });
  } catch {
    sourcesModel.unhide(row.id); // refused: said in the chat, from the command's reply
    return;
  }
  const revision = ++hintRevision;
  const clear = renderHint(hint, `Removed “${row.title}” from this project.`, false, { label: 'Undo', run: () => void restoreSource(row) });
  window.setTimeout(() => { if (hintRevision === revision) clear(); }, UNDO_MS);
  if (reply.wasOnStage && typeof reply.next === 'string') {
    const next = sourcesModel.row(reply.next);
    void putOnStage(reply.next, { asset: next, paused: true, at: next?.position ?? 0 });
  }
}

async function restoreSource(row: SourceRow): Promise<void> {
  sourcesModel.unhide(row.id);
  try {
    await connection.request('source-restore', { asset: row.id });
  } catch {
    // said in the chat, from the command's reply
  }
}

/** Files from this Mac (Add…, the home, or dropped on the stage): uploaded to the camera bridge, found in the library,
 * then opened, one after another (sources.ts addFiles). Its progress and problems are said in the side bar's Assets and on
 * the empty stage. */
async function addFromMac(files: File[], session = sessionsModel.currentRow()?.id ?? '', current:()=>boolean = ()=>true): Promise<void> {
  const owned = () => session === sessionsModel.currentRow()?.id && current();
  if (adding) {
    sourcesSection.setStatus('Still adding the files before. Try again when they are in.', 'error');
    return;
  }
  adding = true;
  let said = 0;
  const say = (text: string, tone: '' | 'busy' | 'error') => {
    if (!owned()) return;
    sourcesSection.setStatus(text, tone);
    if (home.isOpen()) home.setStatus(text, tone);
    const mine = ++said;
    if (text && tone === '') window.setTimeout(() => { if (said === mine && owned()) sourcesSection.setStatus(''); }, UNDO_MS);
  };
  try {
    await addFiles(files, {
      current: owned,
      upload: (file, progress) => {
        if (!owned()) return Promise.reject(new Error('The project changed. Choose your files again in the project you want.'));
        return feed.upload(file, { title: '', specimen: '', microscope: true }, progress);
      },
      find: async (words) => {
        const response = await fetch(libraryUrl({ q: words, scope: 'mine', fresh: true }), { cache: 'no-store' });
        return response.ok ? readLibrary(await response.json()).assets : [];
      },
      open: (asset) => owned()
        ? openUserInput(asset.id, { asset, paused: true, session }) : Promise.resolve(false),
      say,
    });
  } finally {
    adding = false;
  }
}

// Files dragged over the studio: the stage shows where to drop them (as does Sources, while it is open), and a drop on
// the stage or the side bar adds them to this session and opens them. A drop anywhere else is refused, so the browser
// never leaves the studio to show the file.
const stageDrop = element<HTMLElement>('stage-drop');
let dragDepth = 0;
const carriesFiles = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files');
const onStage = (target: EventTarget | null) => target instanceof Node && element<HTMLElement>('stage').contains(target);
function setDragging(on: boolean): void {
  stageDrop.hidden = !on;
  element<HTMLElement>('stage').dataset.dropping = on ? 'true' : '';
}
window.addEventListener('dragenter', (event) => {
  if (!carriesFiles(event)) return;
  dragDepth += 1;
  setDragging(true);
});
window.addEventListener('dragleave', (event) => {
  if (!carriesFiles(event)) return;
  dragDepth = Math.max(0, dragDepth - 1);
  if (!dragDepth) setDragging(false);
});
window.addEventListener('dragover', (event) => {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = onStage(event.target) ? 'copy' : 'none';
});
window.addEventListener('drop', (event) => {
  if (!carriesFiles(event)) return;
  event.preventDefault();
  dragDepth = 0;
  setDragging(false);
  if (onStage(event.target)) void addFromMac(Array.from(event.dataTransfer?.files ?? [])).catch(error => showHint(error instanceof Error ? error.message : String(error), true));
});

/** Another session was opened (`session-cleared`): the stage is emptied at once, its sources read, then it lands where
 * session-view.ts says. */
function sessionOpened(message: SessionCleared, opened: { connection?: boolean } = {}): void {
  const token = ++openToken;
  if (!opened.connection) {
    // The server forgets the hold on every opening; the home was the session left's. (A connection's start opens nothing:
    // what the studio shows stays, and only the start's landing runs.)
    home.close();
    viewHeld = false;
    element<HTMLElement>('stage').dataset.held = '';
    document.body.dataset.viewHeld = '';
    noticeKey = '';
  }
  const sid = message.session;
  if (!sid) { renderStatus(); return; } // an older server: the view stays as it was
  viewSid = sid;
  pendingOpen = null;
  sourcesModel.setSession(sid);
  if (message.why === 'new' && (exampleGuide.opening() || exampleGuide.openedNow(sid))) {
    // The walkthrough's example (Will, 2026-10-15: "it flashes"): the server opened this session with the example already
    // on stage. Nothing here takes it over (no home, whose landing put the camera back and raced the example off the
    // stage). One steady "Opening your example…" holds the stage until the guide's reply (its `opened`); a reply that came
    // first means it is on stage already.
    showHint('');
    void sourcesModel.load(); // this session's Assets and what is on stage, read now as for any opening
    if (exampleGuide.opening()) { exampleOpening = { sid, token }; holdStage('Opening your example…'); }
    else stageLanded();
    return;
  }
  // Nothing of the last session stays on the stage (a start keeps what plays: continuing work), nor under it: a hint of
  // the session left (its "Removed · Undo" would act on this one) goes too.
  if (message.why === 'new' || message.why === 'switch') { holdStage(openingWords(message.why, sid)); showHint(''); }
  const feedKeyAtOpen = feedKey();
  const choiceAtOpen = stageChoice; // a choice made while its sources load wins over a start's landing
  void sourcesModel.load().then((facts) => {
    if (token !== openToken) return;
    if (!startMayLand(message.why, choiceAtOpen, stageChoice, facts?.onStage ?? null, sourcesModel.state?.onStage ?? null)) {
      renderStatus();
      return; // the user's choice, or another studio's, stays
    }
    if (document.visibilityState !== 'visible') {
      pendingOpen = { token, why: message.why, sid, feedKey: feedKeyAtOpen, stage: facts?.onStage ?? null, choice: choiceAtOpen };
      renderStatus();
      return;
    }
    const decided = message.why === 'start' ? onStart(facts, playing(), health, view.isFresh()) : viewOnOpen(message.why, facts);
    if (decided === 'wait') {
      startLook = { token, sid, facts, until: performance.now() + START_LOOK_MS, choice: stageChoice, stage: facts?.onStage ?? null };
    }
    void land(decided === 'wait' ? 'keep' : decided, sid, token);
  });
  renderStatus();
}

/** A start decided before the camera's state was known (session-view.onStart said 'wait') looks again, once, when the
 * feed and the health are both read (at most START_LOOK_MS after): returning work lands only if nothing plays by then,
 * and never after something was put on stage or another session opened. */
const START_LOOK_MS = 15000;
let startLook: (StartLook & { facts: Parameters<typeof viewOnOpen>[1] }) | null = null;
/** Every explicit choice of what is on stage (Live camera, Connect, an asset opened, test footage, Reconnect) moves this on:
 * a start's landing decided around one (its sources still loading, or its second look) never replaces it. */
let stageChoice = 0;
function stageChosen(): void {
  stageChoice += 1;
  startLook = null;
}
function playing(): { source: string; busy: boolean } | null {
  return feed.state ? { source: feed.state.source, busy: !!feed.busy } : null;
}
function retryStartLanding(): void {
  const look = startLook;
  if (!look) return;
  const state = lookState(look, { token: openToken, sid: viewSid, choice: stageChoice,
    visible: document.visibilityState === 'visible', at: performance.now(), stage: sourcesModel.state?.onStage ?? null });
  if (state === 'drop') { startLook = null; return; }
  if (state === 'hold') return; // looked at again once this studio is in view, while its time lasts
  const decided = onStart(look.facts, playing(), health, view.isFresh());
  if (decided === 'wait') return;
  startLook = null;
  if (typeof decided === 'object') void land(decided, look.sid, look.token);
}

/** What the stage says while a session opens: its name (the sessions list's), or that a new one starts. */
function openingWords(why: 'new' | 'switch', sid: string): string {
  if (why === 'new') return 'Starting a new project…';
  const row = sessionsModel.rows().find((r) => r.id === sid);
  return row ? `Opening “${sessionName(row, sessionsModel.now())}”…` : 'Opening the project…';
}

// The stage between two sessions (session-view.ts StageGate; the first live try, 2026-10-12: the last session's picture
// stayed after a switch). A click on another session empties the stage at once and says "Opening “…”…"; its
// `session-cleared` does the same for a switch by voice or from another window. No frame is drawn until the landing's
// `source` reply (at most LAND_WAIT_MS), then none the bridge took before it. A new session's home covers it.

function renderOpening(): void {
  const words = home.mode === 'choose' ? '' : stageGate.words(performance.now());
  stageOpening.textContent = words;
  stageOpening.hidden = !words;
  element<HTMLElement>('stage').dataset.opening = words ? 'true' : '';
}

function holdStage(words: string): void {
  stageGate.hold(performance.now(), words);
  view.clearFrame();
  window.clearTimeout(openingTimer);
  openingTimer = window.setTimeout(renderStatus, LAND_WAIT_MS + 50); // never landed: frames are drawn again
  renderStatus();
}

/** Its source is on stage (or nothing will be: the home, a failure said): frames are drawn again, never the last
 * source's, and the camera's notice ("Not live") shows over an empty stage when there is no picture. */
function stageLanded(): void {
  stageGate.settle(performance.now(), Date.now() / 1000);
  window.clearTimeout(openingTimer);
  renderStatus();
}

sessionsModel.onChange(() => {
  const asked = sessionsModel.creating() ? 'new'
    : sessionsModel.rows().find((r) => r.id !== sessionsModel.current && sessionsModel.opening(r.id))?.id ?? '';
  if (asked && asked !== switchAsked) holdStage(asked === 'new' ? openingWords('new', '') : openingWords('switch', asked));
  switchAsked = asked;
});

/** What plays, as a key that changes when another studio switches it. */
function feedKey(): string {
  return !feed.state ? '' : feed.state.source === 'footage' ? `footage:${feed.state.footage ?? ''}` : 'camera';
}

// A session opened while this studio was out of view (by voice, or from another window) lands when it comes into view,
// unless what plays or the session's stage changed meanwhile: then a studio in view already landed it, and this one
// does not fight it.
document.addEventListener('visibilitychange', () => {
  const pending = pendingOpen;
  if (!pending || document.visibilityState !== 'visible') return;
  pendingOpen = null;
  void feed.refresh().then(() => {
    // (What played was not known yet when it opened: nothing to compare, so it lands.)
    if (pending.token !== openToken || document.visibilityState !== 'visible' || (pending.feedKey && feedKey() !== pending.feedKey)
      || (sourcesModel.state?.onStage ?? null) !== pending.stage) return;
    if (!startMayLand(pending.why, pending.choice, stageChoice, pending.stage, sourcesModel.state?.onStage ?? null)) return; // chosen since: kept
    const decided = pending.why === 'start' ? onStart(sourcesModel.state, playing(), health, view.isFresh()) : viewOnOpen(pending.why, sourcesModel.state);
    if (decided === 'wait') {
      startLook = { token: pending.token, sid: pending.sid, facts: sourcesModel.state, until: performance.now() + START_LOOK_MS, choice: stageChoice,
        stage: sourcesModel.state?.onStage ?? null };
    }
    void land(decided === 'wait' ? 'keep' : decided, pending.sid, pending.token);
  });
});

/** Put a landing on the stage: the home, the live camera, or one of the session's assets, each through the server's
 * `source` (the session's own `sid`, so one left meanwhile is refused), which lets the app look again. A landing that
 * fails falls back to the camera and says so; if nothing lands, `view {held: false}` lets the app look all the same.
 * `token`: its opening's; a later opening (even of the same session, gone back to quickly) lands itself. */
async function land(landing: Landing, sid: string, token: number): Promise<void> {
  if (landing === 'keep') { renderStatus(); return; }
  const left = () => token !== openToken || sid !== viewSid;
  if (left()) return; // another opening meanwhile: it lands itself
  if (!stageGate.holding(performance.now())) holdStage(landing === 'home' ? '' : openingWords('switch', sid)); // came into view since
  if (landing === 'home') {
    openHome();
    // Nothing of the last session plays on behind it, and the live camera's state on it is the camera's own. The app
    // looks at nothing until the user chooses (the home's own `view {held: true}`).
    if (feed.state?.source !== 'camera' || feed.busy) {
      const error = await feed.use(null);
      if (error && !left() && home.isOpen()) home.setStatus(error, 'error');
    }
    if (!left()) stageLanded();
    return;
  }
  const quiet = () => {}; // a landing's refusal is said below, never under the side bar's Assets
  let landed = landing === 'live'
    ? await putOnStage('live', { session: sid, say: quiet })
    : await putOnStage(landing.asset, { at: landing.at, paused: true, session: sid, say: quiet });
  if (left()) return;
  if (!landed && landing !== 'live') {
    if (document.body.dataset.workspaceStudy === 'true') {
      openHome();
      home.setStatus('The retained source could not be reopened. Choose an available input; no replacement frame was used.', 'error');
      stageLanded();
      return;
    }
    // Its last source can't be shown (its file moved, the bridge refused it): the camera instead, and said.
    landed = await putOnStage('live', { session: sid, say: quiet });
    if (left()) return;
    showHint(landed ? "This project's last recording or picture could not be shown, so the live camera is on."
      : "This project's last recording or picture could not be shown, and the camera bridge did not answer.", true);
  } else if (!landed) {
    showHint('The camera bridge did not answer, so the live camera could not be put on stage.', true);
  }
  if (!landed) connection.send({ type: 'view', held: false }); // nothing landed: the app looks again all the same
  stageLanded();
}

// The corpus groups the user opened in the camera menu (kept across its rebuilds).
const openFootageGroups = new Set<string>();

let cameraPanelKey = '';

function renderCameraPanel(): void {
  if (cameraPanel.hidden) {
    cameraPanelKey = '';
    return;
  }
  const state = feed.state;
  // Rebuilt only when something it shows changed (not on every 3 s poll), so pictures never blink.
  const key = JSON.stringify([state?.source, state?.footage, state?.cameraVisible, state?.targetDevice, feed.libraryVersion,
    feed.library.length, feed.busy, [...openFootageGroups], Boolean(state)]);
  if (key === cameraPanelKey) return;
  cameraPanelKey = key;
  const children: HTMLElement[] = [];
  const title = Object.assign(document.createElement('h2'), { textContent: 'Camera' });
  children.push(title);
  if (!state) {
    children.push(Object.assign(document.createElement('p'), { className: 'panel-note', textContent: "The camera bridge isn't answering. Start HookeUI (make start in Terminal)." }));
    cameraPanel.replaceChildren(...children);
    return;
  }
  const form = document.createElement('form');
  form.className = 'feed-choices';
  form.addEventListener('submit', (event) => event.preventDefault());

  const cameraNote =
    state.cameraVisible === undefined ? 'checking…' : state.cameraVisible ? 'detected' : 'not detected';
  form.append(feedOption('camera', `Live camera, ${state.targetDevice}`, `The microscope feed. Phone ${cameraNote}.`, state.source === 'camera'));

  const footageTitle = Object.assign(document.createElement('p'), {
    className: 'feed-group',
    textContent: 'Test footage',
  });
  const footageHint = Object.assign(document.createElement('p'), {
    className: 'panel-note',
    textContent: 'Plays a recording into the live feed instead of the camera. The AI and everything after it see an ordinary live camera. Your own recordings and pictures are in the Library.',
  });
  form.append(footageTitle, footageHint);
  // Test footage only: the user's own recordings are assets, opened from All assets (ADR 0022).
  const testFootage = feed.library.filter((item) => !isOwnFootage(item));
  if (!testFootage.length) {
    form.append(Object.assign(document.createElement('p'), { className: 'panel-note', textContent: state.libraryCount ? 'Loading the recordings…' : 'No recordings yet.' }));
  }
  const playing = state.source === 'footage' ? state.footage : null;
  let libraryHeading = false;
  for (const group of footageGroups(testFootage)) {
    if (isLibrary(group.name) && !libraryHeading) {
      libraryHeading = true;
      form.append(Object.assign(document.createElement('p'), { className: 'feed-subgroup', textContent: 'Public library, through a simulated eyepiece' }));
    }
    const rows = () => group.items.map((item) =>
      feedOption(`footage:${item.name}`, item.title, duration(item.seconds), playing === item.name, feed.thumbUrl(item)));
    if (!group.folded) {
      form.append(Object.assign(document.createElement('p'), { className: 'feed-subgroup', textContent: group.name }), ...rows());
      continue;
    }
    // A folded group builds its rows only while open (the menu is rebuilt every few seconds).
    const details = Object.assign(document.createElement('details'), { className: 'feed-fold' });
    const open = openFootageGroups.has(group.name) || group.items.some((item) => item.name === playing);
    details.open = open;
    const summary = document.createElement('summary');
    summary.append(
      Object.assign(document.createElement('span'), { className: 'option-name', textContent: specimenOf(group.name) }),
      Object.assign(document.createElement('span'), { className: 'option-note', textContent: clips(group.items.length) }),
    );
    details.append(summary);
    if (open) details.append(...rows());
    details.addEventListener('toggle', () => {
      if (details.open === openFootageGroups.has(group.name)) return; // set by this render, not by the user
      if (details.open) openFootageGroups.add(group.name);
      else openFootageGroups.delete(group.name);
      renderCameraPanel();
    });
    form.append(details);
  }
  form.append(Object.assign(document.createElement('p'), {
    className: 'panel-note',
    textContent: `From ${state.folder.replace(/^\/Users\/[^/]+/, '~')}, shared by every checkout. The corpus: make corpus-tours (assets/README.md).`,
  }));
  form.addEventListener('change', async (event) => {
    const value = (event.target as HTMLInputElement).value;
    const footage = value === 'camera' ? null : value.slice('footage:'.length);
    stageChosen();
    const error = await feed.use(footage);
    if (error) { showHint(error, true); return; }
    switchedByStudio(); // test footage stands for the live camera: the session's stage is "live"
    showHint(value === 'camera' ? 'Back on the live camera.' : 'Test footage is playing into the live feed.');
  });
  if (feed.busy) form.querySelectorAll('input').forEach((input) => (input.disabled = true));
  children.push(form);
  cameraPanel.replaceChildren(...children);
}

function feedOption(value: string, label: string, note: string, checked: boolean, thumb?: string): HTMLLabelElement {
  const option = document.createElement('label');
  const input = Object.assign(document.createElement('input'), { type: 'radio', name: 'feed', value, checked });
  const text = document.createElement('span');
  text.append(
    Object.assign(document.createElement('span'), { className: 'option-name', textContent: label }),
    Object.assign(document.createElement('span'), { className: 'option-note', textContent: note }),
  );
  option.append(input);
  if (thumb) {
    // What the recording shows, so you see what you pick: made by the bridge in the background,
    // loaded only when the row is in sight, then cached by the browser for good.
    option.classList.add('with-thumb');
    const img = Object.assign(document.createElement('img'), { className: 'feed-thumb', alt: '', loading: 'lazy' as const, decoding: 'async' as const, src: thumb });
    img.width = 64;
    img.height = 36;
    img.addEventListener('error', () => {
      if (img.dataset.retried) return;
      img.dataset.retried = '1';
      window.setTimeout(() => (img.src = `${thumb}${thumb.includes('?') ? '&' : '?'}try=1`), 2500); // not made yet: once more, a little later
    }, { once: false });
    option.append(img);
  }
  option.append(text);
  return option;
}

// While the camera isn't live, look for the phone each time (so "iPhone found" follows the
// cable at once); while live, only when the camera menu is open.
// With No camera chosen nothing is looked for.
setInterval(() => void feed.refresh(!cameraPanel.hidden || (health !== null && health.state !== 'live' && health.state !== 'no_camera'))
  .then(libraryWanted), 3000);

function renderReadout(): void {
  const fps = frameTimes.length > 1 ? (frameTimes.length - 1) / ((frameTimes[frameTimes.length - 1] - frameTimes[0]) / 1000) : 0;
  const h = view.header;
  perf.update({
    live: health?.state === 'live' && view.isFresh(),
    fps,
    screenMs: view.timing.recent('total'),
    analyseMs: view.timing.recent('analyse'),
    gpu: health?.gpu ?? null,
    machine: health?.machine ?? null,
    models: health?.models ?? null,
    view: {
      delayMs: lastDelay,
      slide: !h ? '' : !h.tracked ? 'searching' : h.steady ? 'still' : 'moving',
      outlines: objectCount,
    },
  });
}

// Clicks on the view: a label or outline selects it (its details stay open and the
// magnifier follows it), the × on the selected label removes it, and a click on anything
// else asks for a new outline there.
let zoomNextOutline = false; // a double-click on something not outlined yet: magnify it once outlined
const DOUBLE_CLICK_MS = 320; // a second click on the selected outline removes it once a double-click is ruled out
const UNDO_MS = 6000; // how long "Removed · Undo" is offered (the server holds the outline longer: scene.UNDO_S)
let pendingRemoval = 0;

/** The user's removal of an outline: done at once, said, and offered back for a few seconds. */
function removeWithUndo(id: string): void {
  pendingRemoval = 0;
  const name = view.labelOf(id) || 'the outline';
  connection.send({ type: 'remove', id });
  view.dropLocally(id);
  if (card.openId === id) card.close();
  card.render();
  const revision = ++hintRevision;
  const clear = renderHint(hint, `Removed ${name}.`, false, { label: 'Undo', run: () => connection.send({ type: 'restore', id }) });
  window.setTimeout(() => { if (hintRevision === revision) clear(); }, UNDO_MS);
}

/** A tap or drag to outline while the app's outlines are hidden (`present` hide: "remove the overlay on the specimen"
 * hid them, live 2026-10-12, and every tap after made an outline nobody could see): asking for an outline is asking to
 * see it, so the layer is shown again, as the user's own change. Returns whether it was hidden. */
function showOutlinesForTap(): boolean {
  if (view.outlinesVisible) return false;
  view.outlinesVisible = true;
  reporter.userChanged('outlines', 'visible');
  return true;
}
const SHOWN_AGAIN = 'Outlines are shown again (they had been hidden). ';

view.canvas.addEventListener('click', (event) => {
  if (!boxDrag.clickAllowed()) return; // the end of a dragged box (sent on release), not a click
  const h = view.header;
  if (!h || !view.isFresh()) return;
  if (event.detail > 1) return; // the second click of a double-click (handled below), not a new outline
  const label = view.labelAt(event.clientX, event.clientY);
  if (label?.close && !capabilityTry.hasClick()) {
    connection.send({ type: 'remove', id: label.id });
    view.dropLocally(label.id);
    card.render();
    showHint('Removed.');
    return;
  }
  const point = view.toFrame(event.clientX, event.clientY);
  // Hidden outlines are not there to select: a tap on one outlines what is under it, which shows them again.
  const hit = capabilityTry.hasClick() ? null : label ? { id: label.id, trial: null } : point && view.outlinesVisible ? view.hitTest(point) : null;
  if (hit?.trial) {
    showHint('Pick the best one below, or click somewhere else to start a new comparison.');
    return;
  }
  if (hit) {
    // A click selects it and opens its details; a click on it once it is selected removes it, with an Undo (the user's
    // notes of 2026-10-11: a single highlight had no visible ×). Its details close with their × or Escape. The removal
    // waits out a double-click, which magnifies it instead.
    if (view.selected === hit.id) {
      const id = hit.id;
      clearTimeout(pendingRemoval);
      pendingRemoval = window.setTimeout(() => removeWithUndo(id), DOUBLE_CLICK_MS);
      return;
    }
    view.selected = hit.id; // shown at once; the server confirms
    connection.send({ type: 'select', id: hit.id });
    notes.describeObjects([hit.id]);
    card.openFor(hit.id); resultFocus.activate('legacy:details'); // its details float over the view; drag them anywhere
    showHint('Selected. Click it again to remove it; drag its details by the title.');
    return;
  }
  if (!point) return;
  // A drawn cell of the population layer (live, or on the Stage's still through its fit): the server selects it at the
  // same place (`member_hit`), with no model to run, so it never waits for the outline model.
  if (!capabilityTry.hasClick() && !view.hideOverlays && view.memberAt(point)) {
    connection.send({ type: 'click', frameId: h.id, x: point[0], y: point[1] });
    return;
  }
  if (!health?.gpu.ready) {
    showHint('The outline model is still loading. Try again in a few seconds.', true);
    return;
  }
  const shown = showOutlinesForTap();
  connection.send({ type: 'click', frameId: h.id, x: point[0], y: point[1], capabilityTrial: capabilityTry.takeClick() });
  showHint(arena?.enabled ? BLIND_HINT : (shown ? SHOWN_AGAIN : '') + 'Click an outline to select it.');
});

// Drag on the view to outline what is inside a box: past a few px a press becomes a dashed box,
// and letting go sends one click with the box in frame px (boxdrag.ts). A shorter press is the
// click above. Escape cancels the drag (it sits on top of the panel stack while it lasts).
const boxDrag = new BoxDrag();
view.canvas.addEventListener('pointerdown', (event) => {
  if (event.button !== 0 || !event.isPrimary || !view.isFresh()) return;
  // Only from inside the eyepiece, where a click would outline too; never from a label.
  if (view.labelAt(event.clientX, event.clientY) || !view.toFrame(event.clientX, event.clientY)) return;
  boxDrag.press(event.clientX, event.clientY);
  view.canvas.setPointerCapture(event.pointerId); // the box follows even past the canvas edge
});
view.canvas.addEventListener('pointermove', (event) => {
  if (!boxDrag.move(event.clientX, event.clientY)) return;
  const rect = view.canvas.getBoundingClientRect();
  const box = boxDrag.rect();
  view.dragBox = box && [box[0] - rect.left, box[1] - rect.top, box[2] - rect.left, box[3] - rect.top];
});
view.canvas.addEventListener('pointerup', (event) => {
  const release = boxDrag.release(event.clientX, event.clientY);
  view.dragBox = null;
  if (release.kind === 'box') outlineBox(release.from, release.to);
});
view.canvas.addEventListener('pointercancel', () => cancelBoxDrag());
view.canvas.addEventListener('lostpointercapture', () => {
  if (boxDrag.dragging) cancelBoxDrag();
});

function cancelBoxDrag(hint = false): void {
  if (hint && boxDrag.dragging) showHint('Box cancelled.');
  boxDrag.cancel();
  view.dragBox = null;
}

function outlineBox(from: [number, number], to: [number, number]): void {
  const h = view.header;
  if (!h || !view.isFresh()) return;
  const box = frameBox(from, to, (x, y) => view.toFrameAnywhere(x, y), { w: h.w, h: h.h });
  if (!box) {
    showHint('That box is too small to outline in. Drag a larger one, or click.', true);
    return;
  }
  if (!health?.gpu.ready) {
    showHint('The outline model is still loading. Try again in a few seconds.', true);
    return;
  }
  const shown = showOutlinesForTap();
  connection.send({ type: 'click', frameId: h.id, box, capabilityTrial: capabilityTry.takeClick() });
  showHint(arena?.enabled ? BLIND_HINT : (shown ? SHOWN_AGAIN : '') + 'Outlining what is inside the box. Click an outline to select it.');
}

// Double-click an outline (or its label) to look closer in the magnifier; on something not
// outlined yet, the first click is outlining it and the magnifier opens when it is ready.
view.canvas.addEventListener('dblclick', (event) => {
  clearTimeout(pendingRemoval); pendingRemoval = 0; // a double-click magnifies: the click before it removes nothing
  const label = view.labelAt(event.clientX, event.clientY);
  const point = label ? null : view.toFrame(event.clientX, event.clientY);
  const id = label?.id ?? (point ? view.hitTest(point)?.id : undefined);
  if (id) magnifier.zoom(id);
  else if (point) zoomNextOutline = true;
});

// Hover: the outline under the pointer is emphasised; lingering on it opens its details.
const LINGER_MS = 700;
let lingerTimer = 0;
view.canvas.addEventListener('pointermove', (event) => {
  const label = view.labelAt(event.clientX, event.clientY);
  const point = label ? null : view.toFrame(event.clientX, event.clientY);
  const over = label ? label.id : point ? view.hitTest(point)?.id ?? null : null;
  // A drawn cell of the population layer under the pointer (live, or on the Stage's still through its fit): a click
  // selects it, so it shows as one.
  const member = !label && !over && point && !view.hideOverlays ? view.memberAt(point)?.id ?? null : null;
  view.hoveredMember = member;
  view.canvas.style.cursor = label?.close || over || member ? 'pointer' : 'crosshair';
  if (over === view.hovered) return;
  view.hovered = over;
  window.clearTimeout(lingerTimer);
  if (over) lingerTimer = window.setTimeout(() => onLinger(over), LINGER_MS);
});
view.canvas.addEventListener('pointerleave', () => {
  view.hovered = null;
  view.hoveredMember = null;
  window.clearTimeout(lingerTimer);
});

// Resting on a thing only gets its description ready (and shows its line under the name);
// its details open when it is clicked.
function onLinger(id: string): void {
  connection.send({ type: 'focus', id });
  notes.describeObjects([id]);
}

// The panels, bottom first. Escape closes whatever is on top, one press each: a dialog, the
// camera or models menu, then on a page (a researcher's, All assets…) back to the workspace, then the details card,
// a researcher's work, the magnifier. A text field keeps its Escape unless it is
// in the menu or dialog on top.
const menu = (id: string, isOpen: () => boolean, close: () => void, whileTyping = true): Panel =>
  ({ id, isOpen, close, footprint: () => null, whileTyping, escapePriority: ['research', 'capabilities', 'assets', 'sessions', 'footage', 'datasets', 'models-page'].includes(id) ? 20 : id === 'capability-card' ? 30 : id === 'box-drag' ? 300 : 100 });
// The side bar first: it is a column that stays, so Escape closes what was opened over the view before it (its own
// Escape closes it while the focus is in it).
panels.register(sideBar);
panels.register(magnifier);
panels.register(side);
panels.register(slideMap);
panels.register(card);
// Above the live view's panels: on a page they are out of sight, so Escape first goes
// back to the live view (and the next press closes what is open there).
panels.register(menu('research', () => location.hash.startsWith('#research'), () => navigateStudio('#'), false));
panels.register(menu('capabilities', () => toolboxTarget(location.hash)?.tab === 'all', () => navigateStudio('#'), false));
panels.register(menu('models-page', () => toolboxTarget(location.hash)?.tab === 'tools', () => navigateStudio('#'), false));
panels.register(menu('assets', () => location.hash === '#assets', () => navigateStudio('#'), false));
panels.register(menu('sessions', () => location.hash === '#sessions', () => navigateStudio('#'), false));
panels.register(menu('footage', () => location.hash === '#footage', () => navigateStudio('#'), false));
panels.register(menu('datasets', () => location.hash === '#datasets', () => navigateStudio('#'), false));
panels.register(menu('capability-card', () => capabilityPage.cardOpen(), () => capabilityPage.closeCard(), false));
// Show me's bar, on top: Escape stops a showing first (the server puts back only what it changed).
const showingBar = panels.register(new ShowingBar(element('showing-bar'), element('showing-text'), element<HTMLButtonElement>('showing-stop'),
  (command) => connection.send(command)));
for (const p of pickers) panels.register(p);
panels.register(gear);
panels.register(spending);
panels.register(perf); // pinned open by a click: Escape closes it like a menu
panels.register(menu('phone', () => phonePanel.open, () => {
  phonePanel.setOpen(false);
  source.focus();
}));
panels.register(menu('camera', () => !cameraPanel.hidden, () => {
  setCameraPanel(false);
  gear.toggle.focus();
}));
panels.register(sessionMenu);
for (const dialog of [compareDialog, scoresDialog]) panels.register(menu(dialog.id, () => dialog.open, () => dialog.close()));
// On top while a box is being dragged on the view: Escape cancels the drag first.
panels.register(menu('box-drag', () => boxDrag.dragging, () => cancelBoxDrag(true)));
const stage = element<HTMLElement>('stage');
resultFocus.register('legacy:magnifier', element('magnifier'), () => element('magnifier-caption').textContent || 'Magnifier', true);
resultFocus.register('legacy:viewer', element('viewer'), () => element('viewer-title').textContent || 'Research result', true);
resultFocus.register('legacy:details', element('description'), () => 'Selected object details', true);

// What the studio shows, reported to the server (contract 10): a change it sends is done only once a
// report shows it, and the user's own changes are theirs (screen.ts).
// The result viewer is its own section of the side column (the slide map can be open there alone).
const viewerElement = element<HTMLElement>('viewer');
const viewerPanel: Panel = { id: 'viewer', isOpen: () => viewer.state.open && !viewerElement.hidden, isVisible: () => viewerElement.dataset.resultTucked !== 'true', close: () => viewer.close(),
  footprint: () => (viewerElement.hidden ? null : viewerElement.getBoundingClientRect()) };
// Made in this session is the side bar's Made section: open while the side bar shows it ("close what was made" closes
// the side bar).
const madePanel: Panel = { id: 'made-today', isOpen: () => sideBar.showing('made'),
  close: () => sideBar.setOpen(false), footprint: () => (sideBar.showing('made') ? sideBar.footprint() : null) };
const SCREEN_PANELS: [string, Panel, string][] = [['magnifier', magnifier, 'magnifier'], ['viewer', viewerPanel, 'viewer'],
  ['slidemap', slideMap, 'slidemap'], ['card', card, 'description'], ['made-today', madePanel, 'made-today'],
  ['sidebar', sideBar, 'session-rail']];
const fronted: Record<string, number> = {};
let frontCount = 0;
let outlineColourName = 'pink';
function screenItems(): ScreenItem[] {
  const items: ScreenItem[] = [{ id: 'live', kind: 'live', open: true, visible: true, opacity: view.liveOpacity,
    rect: rectOf(view.canvas.getBoundingClientRect()), z: 0 }];
  const population = populationScreenItem(view);
  if (population) items.push(population);
  const tracks = tracksScreenItem(view);
  if (tracks) items.push(tracks);
  items.push({ id: 'outlines', kind: 'outlines', visible: view.outlinesVisible && !view.hideOverlays, opacity: view.outlinesOpacity,
    style: { colour: outlineColourName, fill: view.outlineStyle === 'classic' }, z: 2 });
  SCREEN_PANELS.forEach(([id, panel], i) => items.push({ id, kind: id, open: panel.isOpen(), visible: panel.isVisible?.() !== false && panel.isOpen(), rect: panel.isOpen() ? rectOf(panel.footprint()) : null,
    z: fronted[id] ?? 10 + i, shows: id === 'viewer' ? viewer.state.rid : id === 'magnifier' ? magnifier.objectId() : null,
    ...(id === 'magnifier' ? { source: magnifier.source(), beside: magnifier.besideShown(), level: magnifier.levelShown(), covers: magnifier.covers(), pictures: magnifier.picturesShown() } : {}),
    ...(id === 'viewer' ? { step: viewer.state.step ?? null, work: viewer.selectedWork(), workReady: viewer.displayedWork() !== undefined } : {}),
    ...(id === 'sidebar' ? { title: sideBar.screenItem().title, shows: sideBar.screenItem().shows } : {}) }));
  items.push({ ...sessionMenu.screenItem(), z: 40 }); // the sessions list: the title's menu, over everything while open
  return items;
}
// These are observations of the existing producers, never native source/paint receipts.
function studioSurfaces(): StudioSurfaceCollection {
  resultFocus.sync();
  const current = screenItems();
  const panelRoots = new Map(SCREEN_PANELS.map(([id, , root]) => [id, element<HTMLElement>(root)]));
  const controls: Record<string, readonly string[]> = {
    viewer: ['viewer-close'],
    slidemap: ['slidemap-close'], card: ['description-close', 'description-more', 'description-deeper', 'description-open'],
    'made-today': ['made-today-close'],
    sidebar: ['sidebar-toggle-sources', 'sidebar-toggle-made', 'sidebar-toggle-researchers', 'sidebar-toggle-activity', 'sidebar-toggle-actions', 'sidebar-hide', 'sidebar-close'],
  };
  const tabStrip=document.getElementById('workspace-tabs-strip');
  const tabSurfaces:StudioSurfaceCollection['surfaces']=tabStrip?[{id:'workspace-tabs',root:tabStrip,read:()=>{
    const project=document.body.dataset.workspaceProject,tab=document.body.dataset.activeWorkspaceView;
    const open=!!project&&!!tab&&!document.body.dataset.workspaceOpening,rect=rectOf(tabStrip.getBoundingClientRect());
    return {kind:'panel',open,visible:surfaceVisible(tabStrip,open,true,rect),rect,z:null,space:'workspace',
      legacy:{schema:'studio-surface-v1',scope:'dom-observation',title:'Project Tabs',...(project&&tab?{workspace:{project,tab}}:{})},
      contentKey:tabStrip.dataset.opening??null,controls:[]};
  }}]:[];
  return { surfaces: [...tabSurfaces,...documentSurfaces(),...current.map((item) => {
    const root = panelRoots.get(item.id) ?? view.canvas;
    if (item.id === 'magnifier') return magnifierSurface(magnifier, root, () => item.z ?? 10);
    return { id: item.id, root, read: () => {
      const legacy: StudioSurfaceLegacy = { schema: 'studio-surface-v1', scope: 'dom-observation' };
      for (const key of ['title', 'shows', 'source', 'beside', 'level', 'pictures', 'step', 'placement', 'covers', 'opacity', 'work', 'workReady'] as const) {
        if (item[key] !== undefined) Object.assign(legacy, { [key]: item[key] });
      }
      if(item.id==='live'&&document.body.dataset.workspaceProject&&document.body.dataset.activeWorkspaceView)legacy.workspace={project:document.body.dataset.workspaceProject,tab:document.body.dataset.activeWorkspaceView};
      if (item.style) {
        const style: NonNullable<StudioSurfaceLegacy['style']> = {};
        for (const key of ['opacity', 'fillOpacity', 'lineWidth', 'colour', 'color', 'stroke', 'fill', 'emphasis'] as const) {
          if (item.style[key] !== undefined) Object.assign(style, { [key]: item.style[key] });
        }
        legacy.style = style;
      }
      const open = item.open ?? true;
      const rect = item.rect ?? rectOf(root.getBoundingClientRect());
      return { kind: item.kind, open, visible: surfaceVisible(root, open, item.visible ?? true, rect),
        rect, z: item.z ?? null, space: item.space ?? itemSpace(item.kind, item.source), legacy,
        contentKey: JSON.stringify([legacy.shows ?? null, legacy.source ?? null, legacy.beside ?? null, legacy.step ?? null,
          legacy.work ?? null, (legacy.pictures ?? []).map((picture) => picture.source)]),
        // A control no longer in the page is not observed: never a reason to refuse what the user sent (live
        // 2026-10-13: every chat message was refused with "Missing #session-rail-toggle").
        controls: (controls[item.id] ?? []).filter((id) => document.getElementById(id) !== null).map((id) => {
          const node = element<HTMLElement>(id), pressed = node.getAttribute('aria-pressed');
          return { node, fact: { id, targetId: item.id, role: node.getAttribute('role') ?? (node.tagName === 'A' ? 'link' : 'button'),
            present: open && root.contains(node) && !node.hidden && !node.closest('[hidden]'),
            enabled: !node.matches(':disabled') && node.getAttribute('aria-disabled') !== 'true', editable: false as const,
            stateRevision: null, observedValue: pressed === 'true' ? true : pressed === 'false' ? false : null } };
        }) };
    } };
  })], coverage: { complete: false, omittedItems: 0,
    unknown: ['unenumerated-research-assets-datasets-capabilities', 'other-controls-unregistered', 'displayed-work-version-unavailable'] } };
}
function studioSelection(): StudioSelection {
  const biological: StudioSelection['biological'][number][] = [];
  if (view.selected) {
    // View's current selected domain ID; its private scene has no revision getter.
    biological.push({ kind: 'outline', outlineId: view.selected });
  }
  if (view.selectedMember) {
    const population = view.population;
    if (!population || !population.members.some((member) => member.id === view.selectedMember)) throw new Error('Selected cell is unavailable');
    biological.push({ kind: 'member', populationId: population.id, memberId: view.selectedMember, memberRevision: population.revision });
  }
  return { presentations: documentSurfaces().filter(s=>s.root.contains(document.activeElement)).map(s=>`target:surface:${s.id}`), biological };
}
function studioViewport(): WorkspaceViewport {
  const visual = window.visualViewport;
  const visualRect = visual ? [visual.offsetLeft, visual.offsetTop, visual.width, visual.height] : [0, 0, innerWidth, innerHeight];
  return { layoutSize: [innerWidth, innerHeight], visualRect, presentationArea: windowPlacementArea(stage.getBoundingClientRect(), visualRect),
    visualScale: visual?.scale ?? 1, documentScroll: [scrollX, scrollY], dpr: devicePixelRatio,
    visible: document.visibilityState === 'visible', documentFocused: document.hasFocus(), activeSurface: document.body.dataset.studyOverlay ? document.body.dataset.studySurface ?? 'live' : document.body.dataset.workspaceStudy === 'true' && document.body.dataset.studyDrawer === 'true' && document.querySelector<HTMLElement>('.study-drawer')?.dataset.section === 'research' ? 'research' : document.body.dataset.view ?? 'live' };
}
function studioWindowFacts(): WindowFacts {
  const canvas = view.canvas.getBoundingClientRect();
  return { canvas, area: view.viewArea(), disc: view.discOnCanvas(),
    object: view.selected ? view.objectCanvasBox(view.selected) : null,
    labels: [...view.labels.map((label) => label.rect), ...view.cornerNotes.map(box => [box.x, box.y, box.x + box.w, box.y + box.h] as [number,number,number,number]),
      ...cornerBoxes(canvas)] };
}

/** What sits in the stage's corners now (the footage player, the layer pills, the hint, the shelf), in the canvas's
 * coordinates: a new window keeps clear of them where there is room (live 2026-10-11: windows opened over the footage
 * player in the top-left). */
function cornerBoxes(canvas: DOMRect): [number, number, number, number][] {
  const boxes: [number, number, number, number][] = [];
  for (const node of document.querySelectorAll<HTMLElement>('.stage-corner > *')) {
    if (node.hidden) continue;
    const r = node.getBoundingClientRect();
    if (r.width > 0 && r.height > 0) boxes.push([r.left - canvas.left, r.top - canvas.top, r.right - canvas.left, r.bottom - canvas.top]);
  }
  return boxes;
}
view.windowLeaders = () => workspaceSession?.livePlacements((source, sample) => {
  const object = livePlacementObject(source, sample, view.header, view.isFresh());
  return object && view.objectInView(object) ? view.objectScreenPoint(object) : null;
}) ?? [];
const reporter = new ScreenReporter((command) => connection.send(command), screenItems);
const shareTab = element<HTMLButtonElement>('workspace-share');
const stopTab = element<HTMLButtonElement>('workspace-share-stop');
const shareStatus = element<HTMLElement>('workspace-share-status');
let workspaceId = crypto.randomUUID() as string;
try { workspaceId = sessionStorage.getItem('micromind-workspace') || workspaceId; sessionStorage.setItem('micromind-workspace', workspaceId); } catch { /* Storage denied: this page still has an isolated workspace. */ }
workspaceSession = new WorkspaceSession({ focusFallback: () => element('sidebar-toggle-made'), onPresentation: (id, root, title, foreground) => {
    if (document.body.dataset.workspaceStudy === 'true') {
      root.classList.add('study-inline-result');studyArtifacts().mount(root);
      const close=root.querySelector<HTMLButtonElement>('.workspace-window-close');
      if(close){close.textContent='×';close.setAttribute('aria-label',`Close ${title()}`);}

    }
    resultFocus.register(id, root, title); if (foreground) resultFocus.activate(id); else resultFocus.sync();
  }, connection, reporter, stage, hiddenHost: element('hidden-windows'), panels, interactionHost: document.body,
  proposal: Object.freeze({ schema: 'workspace-origin-v2', ...(document.body.dataset.workspaceStudy === 'true' ? {restoration:'project-layout' as const} : {}), workspaceId, clientId: crypto.randomUUID(), pageEpoch: crypto.randomUUID() }),
  legacy: studioSurfaces, viewport: studioViewport, selected: studioSelection, windowFacts: studioWindowFacts, frame: () => view.header?.id,
  platform: { fetch: window.fetch.bind(window), createObjectURL: (blob) => URL.createObjectURL(blob), revokeObjectURL: (url) => URL.revokeObjectURL(url),
    now: () => performance.now(), sourceOrigin: location.origin }, onError: diagnostic,
  onReportError: diagnostic,
  onReadyChange: (ready) => {
    if (!ready && voice.state !== 'off') voice.stop();
    workspaceReady = ready;
    if (!ready) composer.set(askRequests.retire(askScope(), question.value));
    updateAskAvailability(); askStatus.hidden = ready;
    askStatus.textContent = ready ? '' : 'Reconnecting to the studio… Your draft is kept. A sent request may still be running; check Activity before retrying.';
  },
  onWorkspaceSharingChange: (state) => {
    const active = state === 'preparing' || state === 'sharing' || state === 'muted';
    shareTab.disabled = active; stopTab.hidden = !active;
    shareTab.parentElement!.dataset.state = state === 'preparing' ? 'starting' : state === 'sharing' ? 'on' : state === 'muted' ? 'paused' : 'off';
    if (!['preparing', 'sharing', 'muted', 'share-required', 'session-retired'].includes(state)) shareStatus.dataset.error = '';
    else delete shareStatus.dataset.error;
    shareStatus.textContent = state === 'preparing' ? 'Tab sharing starting' : state === 'sharing' ? 'Tab sharing on' : state === 'muted' ? 'Tab sharing paused'
      : state === 'share-required' || state === 'session-retired' ? 'Tab sharing off' : `Tab sharing unavailable (${state})`;
  } });
shareTab.addEventListener('click', () => { void workspaceSession?.shareWorkspaceTab(); });
stopTab.addEventListener('click', () => workspaceSession?.stopWorkspaceSharing());
// Terminal teardown only. BFCache restoration is a separate unaccepted lifecycle contract.
window.addEventListener('beforeunload', () => workspaceSession?.dispose());
window.addEventListener('resize', () => workspaceSession?.viewportChanged());
function reportWorkspace(now: number): void {
  try { reporter.tick(now); } catch (error) { diagnostic(error); }
}
magnifier.onUserChange = (prop) => reporter.userChanged('magnifier', prop);
// The user's own toggles and drags are theirs (Amendment 4.4): the × buttons, the slide-map button, the card moved.
for (const [button, id] of [['magnifier-close', 'magnifier'], ['slidemap-close', 'slidemap'], ['viewer-close', 'viewer'],
  ['map', 'slidemap'], ['description-close', 'card'], ['made-today-close', 'made-today']] as const) {
  element<HTMLElement>(button).addEventListener('click', () => reporter.userChanged(id, 'open'));
}
new MutationObserver(() => {
  if (element<HTMLElement>('description').dataset.dragging === undefined) reporter.userChanged('card', 'rect');
}).observe(element<HTMLElement>('description'), { attributes: true, attributeFilter: ['data-dragging'] });
panels.closedByUser = (id) => {
  // The side column closes the result viewer (its stack id is 'side'; re-check N6).
  const entry = SCREEN_PANELS.find(([, panel]) => panel.id === (id === 'side' ? 'viewer' : id));
  if (entry) reporter.userChanged(entry[0], 'open');
};
// Hidden or shown again: said at once (the report is otherwise sent from the animation frames, which stop).
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) workspaceSession?.shownAgain(); // painted now what was given in the background
  reporter.askNow();
  reportWorkspace(performance.now());
});

function applyPresent(message: PresentMessage): void {
  if (!workspaceSession) throw new Error('Workspace connection is unavailable');
  const applied = workspaceSession.legacyApplied(message.change, message.id, message);
  const set = message.set;
  if(message.kind==='document')studyArtifacts().applyDocument(message.id,set);
  if (message.kind === 'live' && typeof set.opacity === 'number') view.liveOpacity = set.opacity;
  if (message.kind === 'outlines') {
    if (typeof set.visible === 'boolean') view.outlinesVisible = set.visible;
    if (typeof set.opacity === 'number') view.outlinesOpacity = set.opacity;
  }
  // "close the sessions list": the session switcher, the title's menu (session-menu.ts).
  if (message.id === 'sessions' || message.kind === 'sessions') sessionMenu.applyPresent(set);
  const entry = SCREEN_PANELS.find(([id]) => id === message.id && (message.kind === id || message.kind === 'entry'));
  if (entry) {
    const [id, panel, elementId] = entry;
    if (id === 'sidebar') sideBar.applyPresent(set); // open (on `shows`), close or hide
    else if (set.open === false && panel.isOpen()) panel.close();
    if (set.restore === true && id === 'magnifier') magnifier.restore();
    const place = id === 'magnifier' ? presentPlace(set, message.verb) : null;
    if (place) {
      magnifier.remember();
      magnifier.moveTo(place.x, place.y, place.over); // asked for over the view: kept over its object (G3)
      if (place.w !== null) magnifier.resizeTo(place.w);
    }
    if ((set.open === true || set.front || set.restore) && workspaceSession.claimLegacyForeground(message)) resultFocus.activate(`legacy:${id === 'card' ? 'details' : id}`);
    if (set.front) {
      fronted[id] = 30 + ++frontCount;
      element<HTMLElement>(elementId).style.zIndex = String(fronted[id]);
    }
  }
  // Applied once it has been drawn: the report after the next frame says so. A hidden tab draws no frames:
  // there the change is in the page's state at once, and said at once (M1.5b re-check, new item 1).
  if (document.hidden) {
    applied();
    reportWorkspace(performance.now());
  } else {
    requestAnimationFrame(() => requestAnimationFrame(applied));
  }
}

panels.start(stage, (room) => {
  reportWorkspace(performance.now());
  view.reserved = room;
  // The shelf keeps to the area the view has (it never runs under the column or sheet).
  stage.style.setProperty('--side-room', `${room.right}px`);
  stage.style.setProperty('--sheet-room', `${room.bottom}px`);
  // The stage's corners keep right of the sessions rail (its strip, or the open rail).
  stage.style.setProperty('--rail-room', `${Math.round(room.edge)}px`);
  stageLayout.setRail(room.left, room.edge);
});

// The workspace and the studio's pages, mirrored in the address: All assets (#assets), a researcher's work
// (#research/<id>, so it can be linked to and opened in its own window), test footage (#footage), Datasets,
// Capabilities, Models. "#research" alone is this session's Researchers: the side bar's section.
const pageBack = element<HTMLAnchorElement>('page-back');
const workflowsPanel = document.createElement('section');
workflowsPanel.id = 'workflows'; workflowsPanel.hidden = true; document.body.append(workflowsPanel);
const workflowsPage = new WorkflowPage(workflowsPanel, () => sessionsModel.current, (ref, title) => {
  if (ref.session !== sessionsModel.current) { showHint('Return to the original Project to inspect this result.'); return; }
  void import('./scientific-document').then(async ({openStudyScientific}) => {
    if (ref.session !== sessionsModel.current) throw new Error('Return to the original Project to inspect this result.');
    const opened = await openStudyScientific({resource:{id:ref.id,day:ref.session,version:ref.revision,picture:null},title,zoom:1,pan:[.5,.5]});
    if (opened) navigateStudio('#');
    else showHint('The saved result could not be opened. Retry from this run.');
  }).catch(error => showHint(error instanceof Error ? error.message : String(error)));
});
sessionsModel.onChange(() => { if(location.hash.split('?')[0] === '#workflows') void workflowsPage.show(); });

function route(): void {
  const study = document.body.dataset.workspaceStudy === 'true';
  exampleGuide.routeChanged();
  if (/^#research(?:\?.*)?$/.test(location.hash)) {
    navigateStudio('#');
    if (study) window.dispatchEvent(new CustomEvent('workspace-study-section', { detail: 'research' }));
    else sideBar.open('researchers');
    return;
  }
  const match = location.hash.match(/^#research(?:\/([\w-]+))?(?:\?(.*))?$/);
  const showResearch = match !== null;
  if (!study && showResearch && chatBeforeResearch === null) {
    chatBeforeResearch = document.body.dataset.chat === 'hidden';
    setChatHidden(true, false);
  } else if (!showResearch && chatBeforeResearch !== null) {
    setChatHidden(chatBeforeResearch);
    chatBeforeResearch = null;
  }
  const toolbox = toolboxTarget(location.hash);
  const showCapabilities = toolbox?.tab === 'all';
  const showAssets = location.hash === '#assets';
  const showWorkflows = location.hash.split('?')[0] === '#workflows';
  if (showWorkflows) void workflowsPage.show(); else workflowsPage.hide();
  const showSessions = location.hash === '#sessions';
  const showFootage = location.hash === '#footage';
  const showDatasets = location.hash === '#datasets';
  const showModels = toolbox?.tab === 'tools';
  const measured = measurementsTarget(location.hash);
  const showMeasurements = measured !== undefined;
  const page = study && location.hash === '#researchers' ? 'researchers' : study && ['#settings','#help','#shortcuts'].includes(location.hash) ? location.hash.slice(1) : showResearch ? 'research' : showCapabilities ? 'capabilities' : showWorkflows ? 'workflows' : showAssets ? 'assets' : showSessions ? 'sessions' : showFootage ? 'footage'
    : showDatasets ? 'datasets' : showModels ? 'models' : showMeasurements ? 'measurements' : 'live';
  document.body.dataset.view = study ? 'live' : page;
  document.body.dataset.studySurface = study ? page : '';
  pageBack.hidden = study || page === 'live';
  modelsPanel.hidden = !showModels;
  setupChip.setAttribute('aria-pressed', String(showModels));
  if (showModels) modelsPage.show(toolbox?.id ?? null);
  else modelsPage.hide();
  tabAssets.setAttribute('aria-selected', String(showAssets));
  tabSessions.setAttribute('aria-selected', String(showSessions));
  assetsPage.hidden = !showAssets;
  if (showAssets) libraryPage.show();
  else libraryPage.hide();
  sessionsPanel.hidden = !showSessions;
  if (showSessions) sessionsPage.show();
  else sessionsPage.hide();
  measurementsPanel.hidden = !showMeasurements;
  if (showMeasurements) void measurementsPage.show(measured ?? null, measurementsPin(location.hash));
  else measurementsPage.hide();
  footagePanel.hidden = !showFootage;
  datasetsPanel.hidden = !showDatasets;
  tabDatasets.setAttribute('aria-selected', String(showDatasets));
  datasetsView.shown(showDatasets);
  if (showFootage) {
    assetsView.render();
    void feed.refresh().then(libraryWanted);
  }
  tabCapabilities.setAttribute('aria-selected', String(showCapabilities || showModels));
  researchPanel.hidden = !showResearch;
  capabilitiesPanel.hidden = !showCapabilities;
  if (showCapabilities) capabilityPage.opened(); // Fiji's tile is read only once this view is shown
  if (!study) view.paused = page !== 'live';
  if (showResearch) {
    const reference = new URLSearchParams(match[2] ?? '');
    const day = reference.get('day'); // a session id (sid) or a date: opaque, checked only as a safe token
    if (match[1] && day && /^[\w.-]{1,64}$/.test(day)) void research.openReference(day, match[1], reference.get('output'), reference.get('findings'), reference.get('digest'), reference.get('outputVersion'));
    else research.show(match[1] ?? null);
  }
  else {
    research.hide();
    if (research.day) void research.setDay(null); // back on the live view: the session open now again
  }
  if (study) {
    if(page==='researchers') renderResearchers();
    const surfaces: Record<string, HTMLElement> = { workflows: workflowsPanel, models: modelsPanel, capabilities: capabilitiesPanel, assets: assetsPage,
      sessions: sessionsPanel, footage: footagePanel, datasets: datasetsPanel, measurements: measurementsPanel, research: researchPanel };
    window.dispatchEvent(new CustomEvent('workspace-study-surface', { detail: { page, hash: location.hash, node: surfaces[page] ?? null } }));
  }
}
window.addEventListener('hashchange', route);
route();


const liveLayerControls = bindLayerControls(view, command => { connection.send(command); },
  command => connection.request('population-correct', command), (id, pin) => openMeasurements(id, pin),
  (madeBy) => madeByName(actionsPanel.capabilities(), madeBy), (id,pin) => {
    if(pin.session!==sessionsModel.current){showHint('Open the result’s Project to inspect its saved revision.');return;}
    if(document.body.dataset.workspaceStudy==='true'){
      void openStudyMasks({resource:{id,day:pin.session,version:pin.revision,picture:0},
        title:`Population ${id} · revision ${pin.revision}`,zoom:1,pan:[.5,.5]})
        .catch(error=>showHint(error instanceof Error?error.message:'The retained population could not be opened.',true));
    }else connection.send({type:'present',what:id,do:'open',revision:pin.revision});
  });
void madeToday.refresh();
setInterval(() => { if (!document.hidden) madeToday.visibilityChanged(); }, 750);

cellsButton.addEventListener('click', () => {
  cellsButton.disabled = true;
  showHint('Outlining cells…');
  connection.send({ type: 'cells' });
});

// Whole frame: the uncropped camera picture, for lining the phone up with the eyepiece.
const wholeFrameButton = element<HTMLButtonElement>('whole-frame');
function setWholeFrame(on: boolean): void {
  view.wholeFrame = on;
  wholeFrameButton.setAttribute('aria-pressed', String(on));
  try {
    localStorage.setItem('whole-frame', on ? '1' : '');
  } catch {
    // not remembered
  }
}
wholeFrameButton.addEventListener('click', () => {
  setWholeFrame(!view.wholeFrame);
  showHint(view.wholeFrame ? 'Showing the whole camera picture. Centre the phone on the eyepiece, then switch back.'
    : 'Zoomed to the eyepiece.');
});
try {
  if (localStorage.getItem('whole-frame')) setWholeFrame(true);
} catch {
  // not remembered
}

spotsButton.addEventListener('click', () => {
  view.showCandidates = !view.showCandidates;
  spotsButton.setAttribute('aria-pressed', String(view.showCandidates));
  connection.send({ type: 'debug', candidates: view.showCandidates });
});

clearButton.addEventListener('click', () => {
  connection.send({ type: 'clear' });
  showHint('Click anything in the view to outline it.');
});

connection.open();
setInterval(pollHealth, 1000);
setInterval(() => {
  // Where camera-to-screen time goes (frametiming.ts): reported for make day-report.
  const report = view.timing.flush(Date.now() / 1000);
  if (report && connected) connection.send({ type: 'timing', ...report });
}, 2000);
setInterval(renderReadout, 500);

function requestStudyAction(fields: import('./protocol').FieldsOf<'action'> & {run:string},destination?:{newTab:boolean}) {
  return requestStudyCommand({type:'action',...fields},destination);
}

async function requestStudyCommand(fields: Extract<ClientCommand,{type:'action'|'capability-run'}> & {run:string},destination?:{newTab:boolean}) {
  if (studyActionOwner) throw new Error('An analysis is already running. Wait for it to settle before starting another.');
  const input = workspaceStudyBridge().state(),opening=document.getElementById('workspace-tabs-strip')?.dataset.opening;
  input.operationInput = workspaceInput(fields, input.onStage);
  if (!input.ready) throw new Error('Wait for the selected input and project to finish opening.');
  studyActionOwner = fields.run;

  try {
    const originRequest: {run:string;origin?:{session:string;tab:string;placementId:string;run:string}} = {run:fields.run};
    if(destination?.newTab){
      const request:{run:string;session:string;title:string;result?:Promise<typeof originRequest.origin>}={run:fields.run,session:input.session,title:'Analysis results'};
      window.dispatchEvent(new CustomEvent('workspace-run-destination',{detail:request}));
      if(!request.result)throw new Error('Open the Project workspace before choosing a result Tab.');
      originRequest.origin=await request.result;
      const now=workspaceStudyBridge().state();
      if(!now.ready||now.session!==input.session||now.onStage!==input.onStage||opening!==document.getElementById('workspace-tabs-strip')?.dataset.opening)
        throw new Error('The captured Project or input changed. Choose the input and run again.');
    }else window.dispatchEvent(new CustomEvent('workspace-origin-request',{detail:originRequest}));
    if(!originRequest.origin)throw new Error('Open a work tab before running analysis.');
    window.dispatchEvent(new CustomEvent('workspace-study-action', { detail: { run: fields.run, phase: 'start', capability: fields.type === 'action' ? fields.capability : `catalog:${fields.model}`, args: fields.type === 'action' ? fields.args : fields.params, input, ...(fields.type === 'action' && fields.use ? { use: fields.use } : {}) } }));
    // A New Tab run says so (`destination`): the Tab it opened is for its result, so the server places its masks there.
    const workspaceOrigin=originRequest.origin?{...originRequest.origin,input:input.operationInput,...(destination?.newTab?{destination:'new' as const}:{})}:undefined;
    const reply = fields.type === 'action'
      ? await connection.request('action', {...fields,workspaceOrigin})
      : await connection.request('capability-run', {...fields,workspaceOrigin});
    if (!reply.stopping && studyActionOwner === fields.run) studyActionOwner = null;
    window.dispatchEvent(new CustomEvent('workspace-study-action', { detail: { run: fields.run, phase: 'finish', reply } }));
    return reply;
  } catch (error) {
    if (connected && studyActionOwner === fields.run) studyActionOwner = null;
    window.dispatchEvent(new CustomEvent('workspace-study-action', { detail: { run: fields.run, phase: connected ? 'error' : 'unknown', error: connected ? String(error) : 'Connection lost. The server outcome is unknown. Reconnect and inspect retained results and execution history before retrying.' } }));
    throw error;
  }
}

// The workspace's listeners (workspace-study.ts, through the bridge's `subscribe`). They poll every 350 ms, and run at
// once (one frame, coalesced) when the research list changes, so a researcher's Tab and its Working strip pill (the
// shelf draws at the next frame) light together.
const studyListeners = new Set<() => void>();
let studyNotifyQueued = false;
function notifyStudy(): void {
  if (!studyListeners.size || studyNotifyQueued) return;
  studyNotifyQueued = true;
  requestAnimationFrame(() => { studyNotifyQueued = false; for (const listener of [...studyListeners]) listener(); });
}

/** The session menu and the meters' panels, while open, by their buttons again; while the side columns move, once
 * when they land (side-motion.ts). */
function placeOpenPanels(): void {
  if (holdUntilLanded(placeOpenPanels)) return;
  if (sessionMenu.isOpen()) sessionMenu.place();
  perf.place(); spending.place();
}

export function workspaceStudyBridge() {
  if (document.body.dataset.workspaceStudy !== 'true') throw new Error('The workspace study requires its separate entrypoint.');
  const state = (): import('./workspace-study-model').StudySnapshot => {
    const onStage = sourcesModel.state?.onStage ?? null;
    return { connected, ready: connected && !document.body.dataset.workspaceInputOpening && !document.body.dataset.workspaceInputUnavailable && !studyTransition && !staging && !switchAsked && !stageGate.holding(performance.now()), session: sessionsModel.current, title: currentSessionName(), projects: sessionsModel.rows(), projectsRoot: sessionsModel.root(),
      sources: sourcesModel.rows(), source: sourcesModel.rows().find(source => source.id === onStage) ?? null,
      onStage, heard: sourcesModel.state != null, selection: view.selected ? { id: view.selected, label: view.labelOf(view.selected) || view.selected } : null,
      frame: onStage && onStage !== 'live' ? null : view.header ? { id: view.header.id, boot: view.header.boot, w: view.header.w, h: view.header.h } : null, held: viewHeld };
  };
  const switchBlockReason = (): string | null => {
    if (!connected) return 'Reconnect before switching projects.';
    const documentBlock=studyArtifacts().blockReason(sessionsModel.current);if(documentBlock)return documentBlock;
    if (isRecording() || stopping || recorder.state !== 'idle') return 'Stop the recording before switching projects.';
    if (voice.state !== 'off') return 'End voice before switching projects.';
    return null;
  };
  const waitFor = async (check: () => boolean, message: string, timeout = 25_000) => {
    const deadline = performance.now() + timeout;
    while (!check()) {
      if (!connected) throw new Error('The connection was lost while restoring the project. Reconnect to recover it.');
      if (performance.now() > deadline) throw new Error(message);
      await new Promise(resolve => window.setTimeout(resolve, 50));
    }
  };
  const capturePresentationSync = (): StudyPresentation => {
    const retained = workspaceSession?.retainedPresentation() ?? { views: [], missing: [] };
    const playback = feed.state?.source === 'footage' ? feed.state.playback : null;
    const saved: StudyPresentation = { schema: 1, session: sessionsModel.current, source: sourcesModel.state?.onStage ?? null,
      position: playback ? positionAt(playback, performance.now() - feed.playbackAt) : 0, paused: playback?.paused !== false,
      wholeFrame: view.wholeFrame, conversation: day?.active ?? null, draft: question.value,
      reading: element('log').scrollTop, views: [...retained.views,...studyArtifacts().retainedPresentation()], missing: retained.missing };
    if (magnifier.hasTemporaryHeldFrame()) saved.missing.push('The held close-up has no retained snapshot. It cannot be reopened after leaving the project.');
    const legacy = magnifier.retainedIdentity();
    if (studyLastReference && legacy?.id === studyLastReference.resource.id && legacy.day === studyLastReference.resource.day
      && !element('magnifier').hidden && !saved.views.some(item => item.resource.id === studyLastReference?.resource.id)) {
      saved.views.push(structuredClone(studyLastReference));
    }
    return saved;
  };
  const restorePresentation = async (value: StudyPresentation, contentOnly = false, options: StudyOpenOptions = {background:true}): Promise<StudyRecovery> => {
    const saved = readStudyPresentation(value, sessionsModel.current);
    if (!saved) throw new Error('The saved workspace does not belong to this project.');
    const missing = [...saved.missing], sid = saved.session, tab = document.body.dataset.activeWorkspaceView;
    const current = () => {
      if (sessionsModel.current !== sid || document.body.dataset.activeWorkspaceView !== tab) throw new Error('The arrangement changed before restoration finished. Reopen its original tab.');
    };
    studyTransition = true;
    try {
      if (!contentOnly) {
      await waitFor(() => sourcesModel.state?.session === sid && !staging && !stageGate.holding(performance.now()), 'The project source did not finish opening.');
      current();
      if (saved.source === null) openHome();
      else {
        const available = saved.source === 'live' || sourcesModel.row(saved.source);
        const opened = available && await putOnStage(saved.source, { session: sid, at: saved.position, paused: true });
        current();
        if (!opened) {
          missing.push('The saved source is unavailable. Choose another input; no replacement frame was used.');
          openHome(); home.setStatus(missing.at(-1)!, 'error');
        } else if (saved.source !== 'live' && !saved.paused) {
          const error = await feed.pause(false);
          if (error) missing.push(error);
        }
      }
      view.wholeFrame = saved.wholeFrame;
      if (saved.conversation && saved.conversation !== day?.active) {
        if (day?.threads.some(thread => thread.id === saved.conversation)) {
          await connection.request('thread', { action: 'switch', id: saved.conversation }); current();
        } else missing.push('The saved conversation is unavailable. The current conversation remains open.');
      }
      composer.set(saved.draft);
      question.dispatchEvent(new Event('input', { bubbles: true }));
      chat.restoreReading(saved.reading);
      }
      for (const retained of saved.views) {
        current();
        try {
          await restoreStudyItem(retained,{
            scientific:async (...args)=>{await openStudyScientific(...args);},text:openStudyText,capture:openStudyCapture,table:openStudyTable,
            masks:async (item,options)=>{workspaceSession?.closeRetainedResource(item.resource);await openStudyMasks(item,options);},
            nativeReady:()=>waitFor(()=>{current();return workspaceReady;},'The workspace did not become ready to reopen retained evidence.'),
            native:async item=>{
              current();if(!workspaceSession)throw new Error('The workspace is unavailable.');
              await workspaceSession.openRetainedResource(item,options.background===false);studyLastReference=structuredClone(item);current();
              await waitFor(()=>{current();return workspaceSession?.restorePresentation(item)??false;},'The retained view did not finish loading.',12_000);
            },
          }, options);
        } catch (error) {
          current(); missing.push(`${retained.title}: ${error instanceof Error ? error.message : String(error)}`);
        }
      }
      return { recovered: missing.length === 0, missing };
    } finally { studyTransition = false; }
  };
  return {
    state,
    profileKey: `${location.origin}/workspace-study`,
    capturePresentationSync,
    /** The studio's ways to change the specimen, shown in the Specimen menu. */
    setSpecimenChoices: (choices: () => import('./phonepanel').SpecimenChoice[]) => { phonePanel.choices = choices; },
    capturePresentation: async () => capturePresentationSync(),
    restorePresentation,
    restoreItems: (items: import('./workspace-study-restoration').StudyRetainedView[], options?:StudyOpenOptions) => restorePresentation({...capturePresentationSync(),views:items,missing:[]}, true, options),
    placeItem: (saved: import('./workspace-study-restoration').StudyRetainedView) => {
      const sid=sessionsModel.current,tab=document.body.dataset.activeWorkspaceView;
      return waitFor(()=>{
        if(sessionsModel.current!==sid||document.body.dataset.activeWorkspaceView!==tab)throw new Error('The arrangement changed before the saved view finished opening. Reopen its original tab.');
        return workspaceSession?.restorePresentation(saved)??false;
      },'The saved view did not finish restoring. Reopen its original tab.',12_000).catch(diagnostic);
    },
    frontItem: (saved: import('./workspace-study-restoration').StudyRetainedView) =>
      workspaceSession?.frontPresentation(saved)??false,
    switchBlockReason,
    // What restarting Hooke (to update) or quitting it would stop here: Record, and the Assistant's answer, its steps
    // still running and voice (restart-readiness.ts; the workspace adds the rest).
    restartWork: (): { blockers: string[]; running: string[] } => {
      const record = recordWords({ recorder: recorder.state, scope: recorder.scope, on: isRecording(), collecting: Boolean(dataset?.on),
        stopping, stopUnconfirmed: datasetStopUnconfirmed || recorder.cameraStopUnconfirmed });
      return { blockers: record.blockers, running: [...record.running,
        ...assistantLines({ writing: chat.writing, steps: assistantSteps, voice: voice.state === 'live' || voice.state === 'connecting' })] };
    },
    switchSession: (id?: string) => studySessionSwitch.run(switchBlockReason, async () => {
      if (id === sessionsModel.current) return id;
      studyTransition = true;
      try {
        const reply = await connection.request('session', id ? { action: 'switch', id } : { action: 'new' });
        const sid = typeof reply.session === 'string' ? reply.session : '';
        if (!sid || id && sid !== id) throw new Error('The server did not acknowledge the requested project.');
        await waitFor(() => sessionsModel.current === sid && sourcesModel.state?.session === sid && !staging && !stageGate.holding(performance.now()), 'The acknowledged project did not finish opening.');
        studyLastReference = null;
        composer.set('');
        question.dispatchEvent(new Event('input', { bubbles: true }));
        return sid;
      } finally { studyTransition = false; }
    }),
    renameSession: async (id: string, name: string) => { await connection.request('session', { action: 'rename', id, name: name.trim() }); },
    restoreDraft: (saved:StudyPresentation)=>{if(saved.session===sessionsModel.current){composer.set(saved.draft);question.dispatchEvent(new Event('input',{bubbles:true}));chat.restoreReading(saved.reading);}},
    request: connection.request.bind(connection),
    runAction: requestStudyAction,
    transition: (active: boolean) => { studyTransition = active; },
    subscribe(listener: (snapshot: import('./workspace-study-model').StudySnapshot) => void) {
      const run = () => listener(state());
      run();
      studyListeners.add(run);
      const timer = window.setInterval(run, 350); // the fallback: everything else the snapshot holds
      return () => { window.clearInterval(timer); studyListeners.delete(run); };
    },
    scientificTransport: () => {
      if (!workspaceSession) throw new Error('Reconnect before opening scientific tools.');
      return workspaceSession.scientificTransport();
    },
    bindActivity: (surface: ActivitySurface) => activityNavigation.bind(surface),
    actions: sideBar.actions(),
    section: (section: SideSection) => sideBar.content(section),
    refreshResearch: renderResearchers,
    researchers: () => [...research.sessions.values()],
    // A researcher's Tab follows the shelf's rule for finished work not opened yet; opening its Tab counts as opened.
    researchNew: (s: import('./protocol').ResearchSession) => shelf.isNew(s),
    // An ended researcher not opened yet, ready or stopped on its own (the strip's rule): its Tab stays beside its pill.
    researchNews: (s: import('./protocol').ResearchSession) => shelf.isNews(s),
    researchOpened: (rid: string) => shelf.markOpened(rid),
    drawnOnStage,
    // The Assistant's composer (composer.ts): the workspace fills its chip line and its + menu, and places the chat's
    // "New messages" pill above it.
    composer,
    askBlocked,
    askAssistant: ask,
    setComposerAttach: (items: () => MenuItem[]) => { composerAttach = items; },
    newMessages: chat.newMessages,
    // The Working strip (working-strip.ts), and what a hand-started run's pill needs: its model's name and its run page.
    strip: shell.strip,
    runName: (capability: string, use?: Record<string, string>) => actionsPanel.runName(capability, use),
    // A result's maker by its display name (one name per model), '' when not known here: never its catalog key.
    makerName: (madeBy: { implementation?: string; operation?: string }) => makerName(actionsPanel.capabilities(), madeBy),
    openRun: (capability: string) => openRunPage(capability),
    // Use it here (Models, Tools By model): the first task the model serves opens with it chosen; false when it serves none.
    useModel: (model: string, task?: string) => actionsPanel.useModel(model, task),
    modelsNeedYou: () => modelsNeedingYou,
    navigate: navigateStudio,
    example: () => exampleGuide.start(),
    upload: (files: File[], session?:string, current?:()=>boolean) => addFromMac(files,session,current),
    bindInputNavigation: (handler:NonNullable<typeof studyInputNavigation>) => {studyInputNavigation=handler;},
    cameraStatus: () => feed.state?.bridge.sourceKind==='none'?'No camera selected':feed.state?.source==='camera'?feed.state.bridge.stateLabel:'Select to check camera connection',
    selectTabInput: async (input:import('./workspace-views-model').WorkspaceTabInput,session:string) => {
      await selectObservedWorkspaceInput(input.source,{
        check:()=>{
          if(session!==sessionsModel.current)throw new Error('The Project changed before its input opened.');
          const blocked=switchBlockReason();if(blocked)throw new Error(blocked.replaceAll('switching projects','changing the input'));
          if(studyActionOwner)throw new Error('This run keeps its captured input. Wait for it to finish; you can preview Data meanwhile.');
        },health:()=>health,waitFor,
        select:async()=>{
          studyTransition=true;
          try {
            if(!await putOnStage(input.source,{paused:true,at:input.position,session}))throw new Error('The input could not be opened. Your saved Tab and Data are kept; retry opening the Tab.');
            await waitFor(()=>sessionsModel.current===session&&sourcesModel.state?.onStage===input.source&&!staging&&!stageGate.holding(performance.now()),'The selected input has not finished opening. Retry its Tab before running a tool.');
          }finally{studyTransition=false;}
        },
      });
    },
    openSource: (asset: AssetMeta) => putOnStage(asset.id, { asset, paused: true, session: sessionsModel.current }),
    openResult: async (entry: import('./madetoday').ResourceEntry) => {
      const resource = studyEntryReference(entry, sessionsModel.current);
      navigateStudio('#');
      const retained: import('./workspace-study-restoration').StudyRetainedView = {resource,title:entry.title,zoom:1,pan:[.5,.5]};
      if(entry.kind==='project'){await openStudyScientific(retained);return;}
      if(entry.kind==='dataset-table'){const {openStudyDataTable}=await import('./datatable-presentation');await openStudyDataTable({...retained,presentation:'datatable'});return;}
      if(entry.kind==='analysis'||entry.kind==='graph'){const {openTableDerived}=await import('./datatable-derived-presentation');await openTableDerived({...retained,presentation:entry.kind});return;}
      if(artifactPresentation(entry)==='masks'){workspaceSession?.closeRetainedResource(resource);await openStudyMasks(retained);return;}
      if(artifactPresentation(entry)==='table'){openStudyTable(retained);return;}
      {
        if (!workspaceSession) throw new Error('Reconnect before reopening retained evidence.');
        await workspaceSession.openRetainedResource(retained);
      }
      studyLastReference = retained;
    },
    openMasks: async (entry: import('./madetoday').ResourceEntry) => {
      const resource=studyEntryReference(entry,sessionsModel.current);
      navigateStudio('#');
      await openStudyMasks({resource,title:entry.title,zoom:1,pan:[.5,.5],presentation:'masks'});
    },
    openTable: (entry: import('./madetoday').ResourceEntry) => openMeasurements(entry.id, { session: entry.day ?? sessionsModel.current, revision: entry.version ?? 0 }),
    // The Data reader's Show on picture (ticket 66, D1): a kept population drawn on this Tab's specimen, or taken off it,
    // through the present verb (screen.ts showRetainedLayer: it resolves once the studio's report shows it); and which
    // population is drawn there now, for "On the picture ✓".
    showLayer: (resource: import('./workspace-study-restoration').StudyResourceReference, show = true) =>
      workspaceSession ? workspaceSession.showRetainedLayer(resource, show ? 'show' : 'hide') : Promise.reject(new Error('Reconnect before showing outlines on the picture.')),
    shownLayer: (): string | null => { const shown = view.overlayPresentation('population'); return shown?.open && !view.hideOverlays ? shown.id : null; },
    // The workspace's layout changed (a side column, a Tab switch). This used to send a synthetic window `resize`, which
    // ran every resize listener inside the press and cost it 2 to 3 full style passes (b041). What follows the Stage's
    // own size watches it (the view, the Working strip, the Tab row, the corners, the guide and the camera menu have
    // ResizeObservers, the capability deck its observer and the workspace's refresh); the rest is told here, and a real
    // window resize still reaches every listener.
    sidesChanged: () => { stageLayout.schedule(); placeOpenPanels(); },
    layoutChanged: () => { stageLayout.schedule(); resultFocus.sync(); workspaceSession?.viewportChanged(); placeOpenPanels(); },
  };
}
