import {ProjectRead} from './project-read';
import {workingCopyOperation} from './project-export';
import { organizeMaterial } from './material-organization';
import { showOpenProject } from './project-open';
import { openProjectFolder } from './project-folder';
import {inputChangeBlockReason} from './workspace-input';
import {isRetainedMaterial,retainedSource,sourceReaderEntry} from './material-identity';
import {sameMediaReference} from './project-media-reference';
import { projectInputs, materialFacts, projectEntry, retainedProjectEntry, indexedRevision } from './project-inputs';
import {readProjectActivity,type ActivitySnapshot} from './project-research-history';
import { ProjectResearchHome } from './project-research-home';
import {configureWorkspaceCompare} from './workspace-compare';
import {configureDataTableDocuments} from './datatable-presentation';
import { scientificAskSelection, clearScientificAsk } from './scientific-ask-input';
import { configureControlDeck, dockControls, parkControls, type ControlDeck } from './capability-controls';
import { expectSideMotion, holdUntilLanded, playSideMotion, sideLeaving, sideTracks, type SideColumn, type Sides } from './side-motion';
import { mountAppUpdates } from './app-updates';
import { agentConnections } from './agent-connections';
import { DISCONNECTED, readReadiness, researcherLines, runLines, studyLines, toolSessionLines, unsentLines } from './restart-readiness';
import './capability-controls.css';
import { daysAgo, monthDay, projectRowWords, inputCountWords, rowView, savedWhereWords, sessionName, sortSessions, type SessionRowView } from './sessions';
import { ENTRY_DRAG, draggedEntry, WorkspaceViews } from './workspace-views';
import { madeByWords } from './workspace-data-card';
import { specimenName } from './camera-label';
import { WorkspaceResearchTabs } from './workspace-research-tabs';
import { requestStop, stopButtonWords } from './research-stop';
import { researchStageBar } from './research-tab';
import { AssistantLayer } from './assistant-layer';
import { clock } from './working-strip';
import { tabMark } from './tab-marks';
import { ResearchersBoard, type StudyRow } from './researchers-board';
import { litCell, type LitCellState } from './lit-cell';
import { drawActivityCells } from './activity-render';
import { asking, isReady, researcherCell, researcherWords, todaysResearch, workingElsewhere } from './shelf';
import { RunPills } from './strip-sources';
import { StudyRefused, controlStudy, createStudy, listStudies, readStudy, studyName, studyNeeds, studySpending, taskCounts, type Effort, type StudyProvider, type StudyInput, studyInputDescription, studyState, studyInputs, studyReadiness, startStudy, collectStudyTask, recoverStudyTask, studyEvidence, reviewStudyTask, retainStudyFinding } from './research-efforts';
import { openMaterialCapability } from './workflow-capabilities';
import type { Reference } from './study-conversation';
import { studyConversation } from './study-conversation';
import { renderStudy, studyAllowanceTitle, studyAllowanceWords } from './study-view';
import { ADOPT_WAIT_MS, adoptedResearcher, beginStart, draftLine, notStartedWords, settleStart, type DraftStart } from './researcher-draft';
import { mountScientificStaging } from './research-scientific';
import { mountScientificAdmission } from './research-scientific-admission';
import { workspaceProfile } from './workspace-startup';
import { WorkspaceAppMenu } from './workspace-app-menu';
import hookeMark from './assets/core-mark-dark.svg';
import hookeLightMark from './assets/core-mark-light.svg';
import { studyArtifacts } from './workspace-study-artifacts';
import { openScientificFileImport } from './scientific-file-import';
import { openScientificBundleImport } from './scientific-bundle-import';
import { configureScientificDocuments, openScientificSessions, openStudyScientific, openToolSessionTitles } from './scientific-document';
import { openStudyTable, openStudyCapture } from './workspace-study-content';
import { studyEntryReference } from './workspace-study-restoration';
import template from '../index.html?raw';
import { toolsBrowser, toolIcon } from './workspace-study-tools';
import { StudySurfaces, sheetAlone } from './workspace-study-surfaces';
import { bindResearchStageRoute, escapeIsPopups, openResearchersDrawer, researchListRoute } from './workspace-study-navigation';
import { failureHelp, workspaceHelp } from './workspace-help';
import { exampleProject } from './onboarding';
import { starterOffer } from './starter-models';
import { activateTab, closeTab, emptyTabs, moveTab, readTabs } from './workspace-study-tabs';
import type { AssetMeta, ReaderRequestMessage, ResearchSession } from './protocol';
import { sourceMeta } from './sources';
import { projectChipShown, runChipWords } from './run-chip-words';
import { applyRailBadge, applyRailMark, railBadges, researchersMark } from './rail-badges';
import { moreButton, popMenu, type MenuItem } from './drawer-menu';
import { afterShown, assistantHeader, LEFT_MAX, LEFT_MIN, leftColumnWidth, railCurrent, savedLeftWidth, stageInsets, toolsMark, toolsPress, type LeftState } from './left-side';
import { DataWindowBody } from './data-window-body';
import { menuActions } from './workspace-data-card';
import { DataList, type DataRowView } from './data-list';
import { dataRows, madeWords, makerChoices, partWord, researcherKey, researcherWork, rowFrom, rowMaker, rowMatches, unseenRows, type DataEntry, type DataRow, type MakerKey } from './data-roles';
import { clean, isWorking } from './research';
import { reconcileBackgroundRun, capturedRun, panelWidth, sourceOf, stageKey, type StudyLayout, type StudyResource, type StudyRun, type StudySection } from './workspace-study-model';
import { DataReader, readerMark, readerTitle, readerWidth, sameItem, type ReaderEntry } from './data-reader';
import { openOnResearcherTab, provideResearchReader, researchReader, researchReaderEntriesChanged, showInResearchReader } from './research-workspace';
import { legacyCard, legacyResearcher, legacyTitle, legacyWords } from './legacy-output-tab';
import { readerItem } from './workspace-views-model';
import { artifactPresentation } from './workspace-study-content';
import type { StudyRetainedView } from './workspace-study-restoration';

// Activity's entries carry the lit cell here (activity-render.ts); set before main.ts draws the first entry.
drawActivityCells((state, label) => litCell(state, { size: 11, label }));
const parsed = new DOMParser().parseFromString(template, 'text/html');
parsed.querySelectorAll('script').forEach(script => script.remove());
document.body.replaceChildren(...Array.from(parsed.body.childNodes));
document.body.dataset.workspaceStudy = 'true';
document.documentElement.dataset.workspaceStudy = 'true'; // html's own overflow and height (workspace-views.css), never a :has()
const { workspaceStudyBridge } = await import('./main');
await import('./workspace-study.css');
await import('./workspace-study-v2.css');
await import('./workspace-calm.css'); // the calm pass, so it settles presentation only
await import('./workspace-feel.css'); // the workspace-feel components (the lit cell), last of all
const bridge = workspaceStudyBridge();
const el = <K extends keyof HTMLElementTagNameMap>(tag: K, className = '', text = ''): HTMLElementTagNameMap[K] => {
  const node = document.createElement(tag); node.className = className; node.textContent = text; return node;
};
const button = (text: string, click: () => void, className = '') => {
  const node = el('button', className, text); node.type = 'button'; node.addEventListener('click', click); return node;
};
const find = (id: string) => document.getElementById(id)!;
/** The Assistant is a layer of its own (assistant-layer.ts) where the Stage holds a page: a researcher's or a Study's
 * Tab, and a reader Tab (one Data item full width). */
const assistantAside = () => document.body.dataset.researchOpen === 'true' || document.body.dataset.readerTab === 'true';
// The Data reader (data-reader.ts, ticket 66 D1): on the right edge in the Assistant's place, one of the two at a time;
// `readerShown` is whether this work Tab has an item open in it (remembered per Tab for the session), `readerBack`
// whether it took the Data drawer's place below 1200 px (‹ Data). A reader Tab shows its one item in `tabReader`.
// Declared before anything lays the sides out (the control deck's `sides` reads them).
let readerShown = false, readerBack = false;
const reader: DataReader = new DataReader(readerDeps('side'), 'side');
const tabReader: DataReader = new DataReader(readerDeps('tab'), 'tab');
find('stage').append(tabReader.root);
const profileDeadline=Date.now()+30000;
while(!bridge.state().connected&&Date.now()<profileDeadline) await new Promise(resolve=>setTimeout(resolve,50));
const profileReply = await workspaceProfile(() => bridge.request('workspace-profile', {}), message => new Promise(resolve => {
  const notice = el('section', 'workspace-startup-error'); notice.setAttribute('role', 'alert');
  const retry = button('Retry workspace', () => { notice.remove(); resolve(); });
  notice.append(el('strong', '', 'Workspace unavailable'), el('p', '', message), retry);
  document.body.prepend(notice); retry.focus();
}));
const storageKey = `workspace-study-v2:${profileReply.profile}`;
// Tool pages belong to Tools (capability-controls.ts): docked left they take the Tools drawer's width, the rail parks
// them instead of closing them, and their own × closes them with the drawer. Inspectors docked left close the drawer.
const controlDeck:ControlDeck=configureControlDeck(profileReply.profile,{
  reveal:side=>{if(side==='left')setDrawer(false,false);else setInspector(true);},
  leftWidth:()=>toolsNow(),
  setLeftWidth:width=>{toolsWidth=leftColumnWidth(width,innerWidth);applyLayout();},
  toolsShowing:()=>(drawerOpen&&!dataOpen&&section==='tools')||!!controlDeck.visibleOn('left')?.page,
  shown:(side,page)=>controlShown(side,page),
  back:()=>{surfaces.dismiss();showSection('tools');setDrawer(true);railButtons.get('tools')?.focus({preventScroll:true});},
  focusBase:side=>(side==='left'?railButtons.get('tools'):agentButton)?.focus({preventScroll:true}),
  changed:()=>{renderControlMarks();},
  sides:()=>sideWidths(),
});
const tabStorageKey = `${storageKey}:tabs`;
let tabs = readTabs(sessionStorage.getItem(tabStorageKey)??localStorage.getItem(tabStorageKey));
let switching = false;
const PROJECT_OPENING = 'A project is opening. Please wait.';
let initialized = false;
// One layout write per press (b041): a rail, drawer, Data window or Assistant press changes the state, and the layout
// is written once at its end (`press`). It ran 1 to 5 times per press, each run re-styling the page.
let layoutHeld = 0;
let layoutWanted = false;
const afterLayout: (() => void)[] = [];
// The left column's widths as the person set them by dragging (the Data window and other drawers; Tools and its pages),
// or null for the default: 340 px at 1200 px and wider, 320 px below (left-side.ts).
let leftWidth: number | null = null;
let toolsWidth: number | null = null;
const leftNowWidth = () => leftColumnWidth(leftWidth, innerWidth);
const toolsNow = () => leftColumnWidth(toolsWidth, innerWidth);
const presentationKey = `${storageKey}:sessions`;
type Presentation = Awaited<ReturnType<typeof bridge.capturePresentation>>;
type SessionUI = { presentation: Presentation; section: StudySection; scroll: number; agent: boolean; drawer: boolean; dataOpen?: boolean; dataMode?: 'outputs'|'history'; dataScroll?: number };
let sessionUI: Record<string, SessionUI> = {};
try { sessionUI = JSON.parse(localStorage.getItem(presentationKey) ?? '{}'); } catch {}
function readPreferences(): { layout: StudyLayout; inspector: boolean; width: number; drawer: boolean; section: StudySection; rightTab: 'agent' | 'analyze' } {
  try {
    const saved = JSON.parse(localStorage.getItem(storageKey) ?? '{}');
    return { layout: ['baseline', 'global', 'contextual'].includes(saved.layout) ? saved.layout : 'global',
      rightTab: saved.rightTab === 'analyze' ? 'analyze' : 'agent', inspector: saved.rightTab ? saved.inspector !== false : true, width: saved.width ? panelWidth(saved.width) : 360, drawer: saved.drawer === true, section: ['inputs', 'projects', 'library', 'tools', 'results', 'history', 'research'].includes(saved.section) ? saved.section : 'inputs' };
  } catch { return { layout: 'global', inspector: true, width: 360, drawer: false, section: 'inputs', rightTab: 'agent' }; }
}
const preferences = readPreferences(); preferences.layout='global';
if(preferences.section==='library'){preferences.section='inputs';preferences.drawer=false;}
try { const saved=JSON.parse(localStorage.getItem(storageKey)??'{}'); toolsWidth=savedLeftWidth(saved.panelSizing,saved.toolsWidth); leftWidth=savedLeftWidth(saved.panelSizing,saved.leftWidth); } catch {}
let snapshot = bridge.state();
let section: StudySection = preferences.section;
let run: StudyRun | null = null;
// The hand-started runs' pills on the Working strip (renderRunPill), one per run: `run` is the newest, and a run that
// works, failed or finished keeps its pill when another starts, until its results are opened or it is hidden.
const runPills=new RunPills<StudyRun>(()=>showSection('results'),capability=>openRunPage(capability),pills=>bridge.strip.set('run',pills));
// Every hand-started run until it ends, whatever its pill does (× hides the pill, never the run): what restarting or
// quitting Hooke would stop (restart-readiness.ts runLines).
const handRuns=new Map<string,{run:StudyRun;name:string}>();
function liveHandRuns():{status:string;name:string;project:string}[]{
  for(const [id,kept] of handRuns)if(['completed','failed','stopped'].includes(kept.run.status))handRuns.delete(id);
  return [...handRuns.values()].map(kept=>({status:kept.run.status,name:kept.name,project:kept.run.input.title}));
}
let entries: DataEntry[] = [];
let resourceSession = '';
/** The index's revision as Data last read it (ticket 66, S7): the poll sends it back, and an unchanged index draws nothing. */
let resourceRevision = '';
/** Data shows that its last read failed (its notice and Retry): the next good read draws the list again. */
let resultsFailedSession = '';
const resultsFailed = () => !!resultsFailedSession && resultsFailedSession === snapshot.session;
/** Data's Made by: everyone (null), you, the Assistant or one researcher (data-roles.ts MakerKey). */
let makerFilter: MakerKey | null = null;
let sourceFilter: string | null = null;
let refreshGeneration = 0;
let lastSnapshotKey = '';
let lastDataKey = '';
let liveCameraStatus: HTMLElement | null = null;
let drawerOpen = preferences.drawer;
let drawerPresentation = 0;
let activePreview: AssetMeta | null = null;
let lastSource = '';
const sourceDimensions = new Map<string, { width: number; height: number } | null>();
const notebook = find('notebook');
// The Assistant's composer (composer.ts, built by main.ts): its chip line says the specimen and where answers go.
const composer = bridge.composer;
const historyNode = bridge.section('activity');

const surfaces = new StudySurfaces(bridge.navigate);
const icon = (name: string): SVGSVGElement => {
  const paths: Record<string, string> = {
    help: 'M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20 M9 8a3 3 0 0 1 6 0c0 2-3 2-3 5 M12 17h.01',
    shortcuts: 'M3 5h18v14H3z M6 9h.01 M10 9h.01 M14 9h.01 M18 9h.01 M6 13h.01 M10 13h.01 M14 13h.01 M18 13h.01 M8 16h8',
    settings: 'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1z M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8',
    rename: 'M4 16l-1 5 5-1L20 8l-4-4L4 16z M13 7l4 4',
    activity: 'M4 6h16 M4 12h16 M4 18h10',
    data: 'M4 4h16v16H4z M4 10h16 M10 4v16 M10 15h10',
    researchers: 'M9 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6 M3 20v-4a6 6 0 0 1 12 0v4 M17 4a3 3 0 0 1 0 6 M18 13a5 5 0 0 1 4 5v2',
    session: 'M4 3h16v18H4z M4 8h16 M8 12h8 M8 16h6',
    assets: 'M3 4h18v16H3z M3 16l5-5 4 4 3-3 6 6 M15 8h.01',
    tools: 'M14 5a5 5 0 0 0-6 6L3 16v5h5l5-5a5 5 0 0 0 6-6l-4 4-5-5z',
    models: 'M12 3l9 5-9 5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',
  };
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', paths[name]); svg.append(path); return svg;
};
const bar = el('div', 'study-bar');
const tabStrip = el('div', 'study-tabs'); tabStrip.setAttribute('role', 'tablist'); tabStrip.setAttribute('aria-label', 'Open projects');
const newTabButton = button('+', () => void changeSession(), 'study-new-tab'); newTabButton.setAttribute('aria-label', 'New project');
const allSessionsButton = button('', () => toggleSessionMenu(), 'study-hooke-menu');
const fixtureButton = button('Test scenarios', () => fixtureDialog.showModal(), 'study-quiet');
const developer = el('details', 'study-developer'); developer.append(el('summary', '', 'Preview'), el('p', '', 'Local review profile · uncalibrated microscope example'), el('p', '', 'Persistent workspace · comparison evidence is in the review package'), fixtureButton);
const delayLabel=el('label','study-delay','Next real analysis delay'); const delayInput=el('input'); delayInput.type='checkbox';delayInput.setAttribute('aria-label','Delay next real analysis for switching test');delayLabel.append(delayInput);developer.append(delayLabel);
developer.hidden = profileReply.review !== true;
const cameraDisclaimer = el('span', 'study-camera-note', 'Private test feed'); cameraDisclaimer.hidden = profileReply.review !== true; find('source').after(cameraDisclaimer);
// The Stage bar's Assistant: pressed while the Assistant is open, and a second press closes it (its panel's × too).
const agentButton = button('Assistant', () => press(() => { if (assistantAside()) { assistantLayer.toggle(); return; } if (preferences.inspector && !readerVisible() && !controlDeck.visibleOn('right')) setInspector(false); else openRightTab('agent'); }), 'study-agent-toggle');
agentButton.title = 'Open or close the Assistant (Alt+A)';
// Projects ▾ opens the switcher (the Hooke menu's Recent projects, with All Projects beside it) at every width.
const projectsButton:HTMLButtonElement=button('Projects ▾',()=>appMenu.showProjects(projectsButton),'study-projects-label');projectsButton.setAttribute('aria-label','Projects: switch to another or find one');projectsButton.title='Switch Project, find one, or open All Projects';
// Below 1200 px the Project row is the switcher; its × closes the open Project's Tab (F-tabs-6: it was unreachable there).
const tabArea=el('div','study-tab-area');tabArea.append(projectsButton,tabStrip,newTabButton);
// One + Tab, on the Tabs row (workspace-views.ts): the header's second "+ New tab" did the same and is gone.
bar.append(tabArea,agentButton); document.body.prepend(bar);
const rail = el('nav', 'study-rail'); rail.setAttribute('aria-label', 'Workspace navigation');
const railButtons = new Map<StudySection, HTMLButtonElement>();
const railItem=(name:string,title:string,click:()=>void,className='')=>{const b=button('',click,className);b.append(icon(name),el('span','',title));b.setAttribute('aria-label',title);return b;};
/** The rail's hairline between its groups (the mock's rail-gap). */
const railGap=()=>{const gap=el('span','study-rail-gap');gap.setAttribute('aria-hidden','true');return gap;};
// Inputs keeps its own rail entry: closing its panel must not hide where data is opened (L7e walk, 2026-10-20).
const inputsButton=railItem('session','Inputs',()=>press(()=>{bridge.navigate('#');if(section==='inputs'&&drawerOpen)setDrawer(false);else{showSection('inputs');setDrawer(true);}}));
railButtons.set('inputs',inputsButton);
// Tools brings back a parked run page; with a page in view it shows the Tools list (the page parks); else the list
// opens or closes like every other place.
const toolsButton=railItem('tools','Tools',()=>press(openTools));railButtons.set('tools',toolsButton);
// Data and Activity switch the one panel between its two modes; a second press on the mode already shown closes it.
const dataButton=button('',()=>press(()=>{if(dataOpen&&dataMode==='outputs')setDataOpen(false);else{surfaces.dismiss();bridge.navigate('#');dataMode='outputs';setDataOpen(true);}}));dataButton.append(icon('data'),el('span','','Data'));dataButton.setAttribute('aria-label','Data');
const activityButton=button('',()=>press(()=>showActivity(!dataOpen||dataMode!=='history')));activityButton.append(icon('activity'),el('span','','Activity'));activityButton.setAttribute('aria-label','Activity');
const researchersButton=button('',()=>press(()=>{if(drawerOpen&&section==='research')setDrawer(false);else openResearchers();}));researchersButton.append(icon('researchers'),el('span','','Researchers'));researchersButton.setAttribute('aria-label','Researchers');
const hookeImage=el('img','hooke-dark-mark');hookeImage.src=hookeMark;hookeImage.alt='';
const hookeLightImage=el('img','hooke-light-mark');hookeLightImage.src=hookeLightMark;hookeLightImage.alt='';
const chevron=document.createElementNS('http://www.w3.org/2000/svg','svg');chevron.setAttribute('viewBox','0 0 12 12');chevron.setAttribute('aria-hidden','true');const chevronPath=document.createElementNS(chevron.namespaceURI,'path');chevronPath.setAttribute('d','M2 4l4 4 4-4');chevron.append(chevronPath);
allSessionsButton.append(hookeImage,hookeLightImage,chevron);allSessionsButton.setAttribute('aria-label','HookeUI menu');allSessionsButton.title='HookeUI menu';
// The Hooke menu is the Project row's first control, above the rail's column (the mock's mark), so the rail is places only.
bar.prepend(allSessionsButton);
const modelsButton = button('', () => bridge.navigate('#models')); modelsButton.append(icon('models'), el('span', '', 'Models')); modelsButton.setAttribute('aria-label', 'Models');
const libraryButton = button('', () => bridge.navigate('#assets'), 'study-library-button');
libraryButton.append(icon('assets'),el('span','','Library'));libraryButton.setAttribute('aria-label','Library');libraryButton.title='All recordings, images and uploads across Projects';
// Hairline groups (the mock's rail): where the Project's work is, what runs on it, the Library; Help, Shortcuts and
// Settings at the foot.
const workflowsButton=button('',()=>bridge.navigate('#workflows'));workflowsButton.append(icon('tools'),el('span','','Workflows'));workflowsButton.setAttribute('aria-label','Workflows');
rail.append(dataButton,activityButton,researchersButton,inputsButton,railGap(),toolsButton,workflowsButton,modelsButton,railGap(),libraryButton);
const drawer = el('aside', 'study-drawer'); drawer.setAttribute('aria-label', 'Project and tools');
// One header for every place in the drawer (the mock's dhead): its title, at most one action, and ×.
const drawerHeader = el('header', 'study-panel-header mm-dhead');
const drawerTitle = el('h2', '', 'Project');
const drawerAction = el('span', 'mm-dhead-action');
const closeLeft = button('×', () => press(() => setDrawer(false)), 'study-close'); closeLeft.setAttribute('aria-label', 'Close left panel');
drawerHeader.append(drawerTitle,drawerAction,closeLeft);
const sessionSections = el('div', 'study-session-sections');
for (const [id,label] of [['inputs','Inputs'],['results','Results'],['research','Research'],['history','Activity']] as const) {
  const b = button(label, () => showSection(id)); b.dataset.section = id; sessionSections.append(b);
}
const drawerBody = el('div', 'study-drawer-body'); drawer.append(drawerHeader, sessionSections, drawerBody);
const inspector = el('section', 'study-inspector'); inspector.setAttribute('aria-label', 'Tools and analysis');
const rightPanel = el('aside', 'study-right-panel'); rightPanel.setAttribute('aria-label', 'Assistant');
// On a researcher's or a Study's Tab the Assistant panel steps aside and opens over the artifact instead (workspace-feel
// step 7; assistant-layer.ts): a layer of its own, not the person's panel preference, so going back to a work Tab finds
// the panel as it was. Escape closes it, and focus goes back to its Stage bar button.
const assistantLayer = new AssistantLayer({ panel: rightPanel, button: agentButton, field: () => find('question'),
  active: assistantAside, changed: () => applyLayout() });
const rightHeader = el('header', 'study-panel-header study-right-header mm-dhead');
const closeRight = button('×', () => press(() => setInspector(false)), 'study-close'); closeRight.setAttribute('aria-label', 'Close Assistant');
// A page docked on the right is reached from here once the Assistant is in front: "<tool> controls" (it was the
// right sidebar's Assistant | Controls switch).
const controlsChip = button('', () => controlDeck.parkedRight()?.show(), 'study-controls-chip'); controlsChip.hidden = true;
// The header is "Assistant" and × (the mock's ahead), with the chip above; conversations (switch, New, Rename) and
// Activity are in its ⋯ (they were the toolbar row under it: moved.md, 2026-10-25). Where answers go is said on the
// composer's chip line now.
const threadPick = find('thread') as HTMLSelectElement;
const chooseThread = (value: string) => { threadPick.value = value; threadPick.dispatchEvent(new Event('change')); };
function assistantMenu(): MenuItem[] {
  const threads = Array.from(threadPick.options).filter((option) => !option.value.startsWith('__'));
  return [{ heading: 'Conversations' },
    ...threads.map((option): MenuItem => ({ label: option.textContent ?? option.value, checked: option.value === threadPick.value,
      title: option.value === threadPick.value ? 'The conversation open now' : 'Go back to this conversation', act: () => chooseThread(option.value) })),
    { label: 'New conversation', title: 'Start a new conversation; this one is kept', act: () => chooseThread('__new') },
    { label: 'Rename this one…', disabled: !threads.length, act: () => chooseThread('__rename') },
    'separator',
    { label: 'Activity', title: 'What the Assistant and the app did, request by request', act: () => showActivity(true) },
    'separator',
    { label: 'Connect your own agent…', title: 'Copy the setup prompt for your own agent, or manage connections',
      act: () => agentConnections.openEntry(assistantMore, manageAgents, () => snapshot.session || undefined) }];
}
const assistantMore = moreButton('Conversations and Activity', assistantMenu, 'mm-dhead-more study-assistant-more');
rightHeader.append(el('h2','','Assistant'), controlsChip, assistantMore, closeRight);
agentConnections.mountDot(assistantMore); // a small dot while an agent is connected (agent-connections.ts)
const resize = el('div', 'study-resize');
function resizePanel(handle: HTMLElement, side: 'left'|'right'): void {
  handle.tabIndex=0; handle.setAttribute('role','separator'); handle.setAttribute('aria-label',`Resize ${side === 'left' ? 'left panel' : 'Assistant'}`); handle.setAttribute('aria-orientation','vertical');
  handle.setAttribute('aria-valuemin',side==='left'?String(LEFT_MIN):'260'); handle.setAttribute('aria-valuemax',side==='left'?String(LEFT_MAX):'520');
  const isTools = () => side==='left' && !dataOpen && section==='tools';
  const currentWidth = () => side==='left' ? (isTools()?toolsNow():leftNowWidth()) : preferences.width;
  // A width the person sets by dragging wins over the default from then on.
  const update = (width: number) => { const value = Math.max(side==='left'?LEFT_MIN:260,Math.min(side==='left'?LEFT_MAX:520,Math.round(width))); if(isTools()) toolsWidth=value; else if(side==='left') leftWidth=value; else preferences.width=value; applyLayout(); };
  handle.addEventListener('keydown',event=>{ if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return; event.preventDefault(); const width=currentWidth();
    update(event.key==='Home'?(side==='left'?LEFT_MIN:260):event.key==='End'?(side==='left'?LEFT_MAX:520):width+(event.key==='ArrowRight'?1:-1)*(side==='left'?20:-20)); });
  handle.addEventListener('pointerdown',event=>{handle.setPointerCapture(event.pointerId);const x=event.clientX,width=currentWidth();
    const move=(next:PointerEvent)=>update(width+(next.clientX-x)*(side==='left'?1:-1));
    const stop=()=>{handle.removeEventListener('pointermove',move);handle.removeEventListener('pointerup',stop);handle.removeEventListener('pointercancel',stop);};
    handle.addEventListener('pointermove',move);handle.addEventListener('pointerup',stop);handle.addEventListener('pointercancel',stop); });
}
resizePanel(resize,'right');
const leftResize=el('div','study-resize study-resize-left'); resizePanel(leftResize,'left'); drawer.append(leftResize);
const context = el('section', 'study-context'); context.setAttribute('aria-label', 'Confirmed analysis input');
const runBox = el('section', 'study-run-box');
const runTitle = el('div', 'study-run-heading'); runTitle.append(el('strong', '', 'Bright objects'), el('span', 'study-real', 'Real · on this Mac'));
const runDescription = el('p', '', 'Find bright regions in the whole image. No API key or model download.');
const runButton = button('Run Bright objects', () => void startRun(), 'study-primary'); runButton.id = 'study-run';
const stopButton = button('Stop analysis', () => void stopRun(), 'study-danger'); stopButton.hidden = true;
const runStatus = el('p', 'study-run-status'); runStatus.setAttribute('role', 'status');
const actionNotice = el('p', 'study-action-notice'); actionNotice.setAttribute('role', 'status');
const resultButton = button('View retained results', () => { void (async () => {
  if (run && run.input.session !== snapshot.session) await changeSession(run.input.session);
  showSection('results'); await refreshResults();
})(); }); resultButton.hidden = true;
// The input and owning Project/tab travel with the Run button, docked or inline, so both are seen before running.
runBox.append(runTitle, runDescription, context, runButton, stopButton, runStatus, actionNotice, resultButton);
const allTools = el('section', 'study-all-tools');
const actionSlot = el('div', 'study-action-slot'); allTools.append(actionSlot);
const toolSearch=bridge.actions.querySelector<HTMLInputElement>('input[type=search]')!;
const brightDetails=el('details','study-bright-tool');
const brightSummary=el('summary'); brightSummary.append(toolIcon('Bright objects'),el('span','','Bright objects'),el('small','','Local · ready'));
brightDetails.append(brightSummary,runBox);
const toolBrowser=toolsBrowser(bridge.actions,toolSearch,brightDetails,bridge.navigate);
/** Tools lists what Actions can run; a tool known only by its name ("cellpose") or not set up yet is in the catalog.
 * Its search opens with the same words. Opening it prepares and runs nothing. */
function searchAllTools(task: string): void {
  bridge.navigate('#toolbox/add-find');
  const deadline = Date.now() + 5000;
  const fill = () => {
    const field = Array.from(document.querySelectorAll<HTMLInputElement>('.capability-entry input.models-own-input'))
      .find(input => input.closest('label')?.textContent?.startsWith('Search by job'));
    if (!field) { if (Date.now() < deadline) setTimeout(fill, 100); return; }
    field.value = task; field.dispatchEvent(new Event('input')); field.focus();
  };
  fill();
}
const catalogSearch = button('', () => searchAllTools(toolSearch.value.trim()), 'study-search-catalog');
// Also beside closest guesses (Actions marks them), not only beside an empty list.
const showCatalogSearch = () => { const task = toolSearch.value.trim(); catalogSearch.hidden = !task || (toolBrowser.empty.hidden && bridge.actions.dataset.taskPartial !== 'true');
  catalogSearch.textContent = `Search all models and packages for “${task}”`; };
toolSearch.addEventListener('input', showCatalogSearch);
new MutationObserver(showCatalogSearch).observe(toolBrowser.empty, { attributes: true, attributeFilter: ['hidden'] });
new MutationObserver(showCatalogSearch).observe(bridge.actions, { attributes: true, attributeFilter: ['data-task-partial'] });
showCatalogSearch();
// Tools as the concept mock draws them: search, then Add model or tool (its three paths are one sheet), then the
// By task / By model switch and the list; the three paths stay listed below it too.
// Add model or tool is the Tools drawer's one header action (the mock's dhead); its three paths are one sheet.
const addTool=button('Add model or tool',()=>bridge.navigate('#toolbox/add'),'study-tools-add mm-dhead-button');addTool.title='Find one in the catalog, bring a link or file, or have AI build it';
inspector.append(toolSearch,toolBrowser.tabs,toolBrowser.hint,allTools,toolBrowser.empty,catalogSearch,toolBrowser.entry);
toolSearch.placeholder='Search tools or models';toolSearch.setAttribute('aria-label','Search tools or models');
rightPanel.append(resize, rightHeader, notebook);
// The composer's +: the workspace's own ways to put a specimen on the Stage (Data's inputs; a picture or video from this
// Mac, as the Specimen menu and Inputs offer it).
bridge.setComposerAttach(() => [
  { label: 'Choose from Data…', title: 'Open an input of this Project on the Stage', act: () => { surfaces.dismiss(); bridge.navigate('#'); dataMode = 'outputs'; setDataOpen(true); } },
  { label: 'Open a picture or video…', title: 'From this Mac; it joins this Project', act: () => pickInputFiles(workspaceViews.tabOwner()) },
]);
// "New messages ↓" sits 8 px above the composer: the log is wrapped so the pill can float at its foot.
{
  const log = notebook.querySelector<HTMLElement>('#log')!, wrap = el('div', 'chat-log-wrap');
  log.before(wrap); wrap.append(log, bridge.newMessages);
  // After the New messages jump the pill goes: focus moves on to the composer's field, not the page.
  bridge.newMessages.addEventListener('click', () => (find('question') as HTMLTextAreaElement | null)?.focus({ preventScroll: true }));
}
const analyzeButton = button('Analyze', () => { bridge.navigate('#'); openRightTab('analyze'); }, 'study-analyze');
find('source').before(analyzeButton);
const sourceBar = el('div', 'study-source-bar');
const sourceWords = el('span');
const sourceMode = el('span', 'study-source-mode');
sourceBar.append(sourceWords, sourceMode);
find('stage').append(sourceBar);
document.body.append(rail, drawer, rightPanel, reader.root);

let dataOpen=false;
let dataMode: 'outputs'|'history'='outputs';
const dataWindow=el('section','study-data-window');dataWindow.hidden=true;dataWindow.setAttribute('aria-label','Project DATA');
// The drawer's one header (the mock's dataDrawer()): Data, its two doors Contents | Activity as quiet tabs, ⋯ (Import)
// and ×. "One home, two doors": the rail's Data and Activity open the same window on either tab.
const dataHeader=el('header','study-panel-header mm-dhead');dataHeader.setAttribute('aria-label','Project DATA');
const dataClose=button('×',()=>press(()=>setDataOpen(false)),'study-close');dataClose.setAttribute('aria-label','Close DATA');
const dataSearch=el('input');dataSearch.type='search';dataSearch.placeholder='Search Data';dataSearch.setAttribute('aria-label','Search Project Data');dataSearch.oninput=()=>renderResults();
const dataSearchWrap=el('div','mm-search-wrap study-data-search');dataSearchWrap.append(tabMark('search','data-search-mark'),dataSearch);
const dataBody=el('div','study-data-body');
// Data by what each thing is (ticket 66, D2; data-list.ts): Made by, then Pictures and recordings, Analyses, Write-ups,
// Sources and Other folded. A row does what it did before (dataRowView). Search Data, the filter and the sections stay
// mounted while they show (data-window-body.ts), so Search Data keeps focus as you type.
const dataList=new DataList({view:row=>dataRowView(row),
  choose:key=>{makerFilter=key;renderResults();},
  openResearcher:rid=>{setDataOpen(false,false);openResearcher(rid);},
  clearSource:()=>{sourceFilter=null;renderResults();},
  addInput:()=>{setDataOpen(false,false);pickInputFiles(workspaceViews.tabOwner());},
  // The chosen researcher's lit cell on its chip, when it is one of this Project's.
  researcherCell:name=>{const row=todaysResearch(bridge.researchers(),snapshot.session).find(item=>item.name===name);return row?{state:researcherCell(row),label:`${row.name}: ${researcherWords(row)}`}:null;}});
const dataView=new DataWindowBody(dataBody,dataSearchWrap,dataList.filter,dataList.list);
const dataTabs=el('div','mm-dtabs study-data-tabs');dataTabs.setAttribute('role','tablist');dataTabs.setAttribute('aria-label','Data');
for(const [id,label] of [['outputs','Contents'],['history','Activity']] as const){const tab=button(label,()=>{dataMode=id;setDataOpen(true,false);},'');tab.dataset.mode=id;tab.setAttribute('role','tab');dataTabs.append(tab);}
// Imports are not views: they sit in the header's ⋯.
// Inputs has Data's one header: its title, ⋯ with the ways to bring an input in (in their old order), and ×.
const inputsMore=moreButton('More for Inputs',()=>{const owner=workspaceViews.tabOwner();return [
  {label:'Import image or video…',title:'A picture or video from this Mac, kept as an input of this Project',disabled:busy(),act:()=>pickInputFiles(owner)},
  {label:'From your Library…',title:'Everything you have recorded or imported',act:()=>showSection('library')},
  {label:'Import scientific source…',title:'A scientific file kept as a saved input in this Project',act:importScientificSource},
  {label:'Import native bundle…',title:'A native bundle kept as saved work in this Project',act:importNativeBundle}];},'study-close mm-dhead-more');
const dataMore=moreButton('More for Data',()=>[{label:'Import scientific source…',title:'A scientific file kept as a saved input in this Project',act:importScientificSource},{label:'Import native bundle…',title:'A native bundle kept as saved work in this Project',act:importNativeBundle}],'study-close mm-dhead-more');
const dataTitle=el('h2','','Data');
dataHeader.append(dataTitle,dataTabs,el('span','mm-dhead-space'),dataMore,dataClose);
dataWindow.append(dataHeader,dataBody);document.body.append(dataWindow);
function showActivity(open:boolean):void {
  if(open){surfaces.dismiss();bridge.navigate('#');dataMode='history';}
  setDataOpen(open);
}
bridge.bindActivity({showing:()=>dataOpen&&dataMode==='history',show:showActivity});
/** Opens or closes the Data window on its tab. Opening it parks a docked page (`park`: the rail switched), never
 * closing it; a page coming into view closes it without parking anything (controlShown). */
function setDataOpen(open:boolean,focus=true,park=true){
  const opened=open&&!dataOpen;
  if(open&&park)parkControls('left');
  const activityOpen=open&&dataMode==='history';
  activityButton.setAttribute('aria-pressed',String(activityOpen));
  find('activity-toggle').setAttribute('aria-expanded',String(activityOpen));
  dataWindow.setAttribute('aria-label',activityOpen?'Project Activity':'Project DATA');
  dataHeader.setAttribute('aria-label',activityOpen?'Project Activity':'Project DATA');
  dataClose.setAttribute('aria-label',activityOpen?'Close Activity':'Close DATA');
  dataSearchWrap.hidden=activityOpen;
  dataOpen=open;dataWindow.hidden=!open&&!sideLeaving(dataWindow);dataButton.setAttribute('aria-pressed',String(open&&!activityOpen));
  if(open){drawerOpen=false;if(innerWidth<1200)preferences.inspector=false;}applyLayout();
  if(open){renderResults(opened);void refreshResults();if(focus)focusOpened(()=>dataWindow.hidden?null:dataTabs.querySelector<HTMLButtonElement>('[aria-selected="true"]'));}else if(focus)dataButton.focus();
}
const dataResize=el('div','study-resize study-resize-left');resizePanel(dataResize,'left');dataWindow.append(dataResize);

const researcherBrowser=el('section','study-researcher-browser');
// Studies (the research runtime's efforts): one Project's research with its objective, allowance, experiments and
// findings. Shown only when this build has them; each opens as a Tab, as a researcher does. They are rows of the
// Researchers list (researchers-board.ts), after its researchers.
/** This Project's studies as last read (All Tabs ⌄ and the Researchers list); null when this build has none. */
let knownStudies:Effort[]=[];
let projectActivity:Record<string,ActivitySnapshot>={};
const projectResearchHome = new ProjectResearchHome(find('session-home'), {
  startResearch: () => newStudy(),
  openResearcher: id => openResearcher(id),
  retry: () => retryProjectReads(),
  current: () => bridge.state().session, openStudy: id => { void openStudy(id); },
  data: () => setDataOpen(true), inputs: () => { showSection('inputs'); setDrawer(true); },
});
let studiesAvailable=false;
let studiesNote='';
let studiesLoadedSession='';
let studyReadGeneration = 0;
const studyReads = new ProjectRead(()=>renderBoard());
function refreshStudies():Promise<void>{
  return studyReads.run(snapshot.session, (signal, ownsRead) => loadStudies(signal, ownsRead));
}
async function loadStudies(signal:AbortSignal, ownsRead:()=>boolean):Promise<void>{
  const session = snapshot.session, generation = ++studyReadGeneration;
  const current = () => snapshot.session === session && generation === studyReadGeneration && ownsRead();
  if(!session){studiesAvailable=false;knownStudies=[];projectActivity={};renderBoard();return;}
  let studies:Effort[]|null;
  try{studies=await listStudies(session, fetch, signal);}catch(error){if(!current())return;studiesLoadedSession=session;studiesAvailable=true;studiesNote=error instanceof Error?error.message:String(error);renderBoard();return;}
  if(!current())return;
  studiesLoadedSession=session;studiesNote='';knownStudies=studies??[];studiesAvailable=studies!==null;
  renderBoard();
  const activity=await readProjectActivity(knownStudies, fetch, signal);
  if(!current())return;
  projectActivity=activity;
  knownStudies=knownStudies.map(study=>activity[study.id]?.study?{...study,...activity[study.id].study}:study);
  renderBoard();
}
/** A Study's lit cell: needs you (an experiment to review or check), Stop requested, working, or quiet. */
function studyCell(study:Effort):{cell:LitCellState;label:string}{
  if(studyNeeds(study))return {cell:'needs',label:'Needs you'};
  if(study.stopPending)return {cell:'still',label:'Stop requested'};
  const tasks=Object.values(study.tasks??{});
  if(study.status==='active'&&tasks.some(task=>['running','launching','waiting'].includes(task.status)))return {cell:'working',label:'Working'};
  return study.status==='active'?{cell:'idle',label:studyState(study)}:{cell:'stopped',label:studyState(study)};
}
function studyRows():StudyRow[]{
  return knownStudies.map(study=>{const {cell,label}=studyCell(study);const counts=taskCounts(study);
    // What it is doing and what it has spent, on one quiet line ("1 running · $0.32 spent of $1.00").
    return {id:study.id,title:studyName(study),sub:[counts||studyState(study),studySpending(study).split(' · ')[0]].filter(Boolean).join(' · '),cell,cellLabel:label,
      open:()=>void openStudy(study.id),current:researchTabs.selectedId()===`study:${study.id}`};});
}
// "+ New" is the Researchers header's one action: a researcher (the Assistant's starter, in a Tab) or a Study.
const researchNew=button('New',()=>popMenu(researchNew,[
  {label:'New researcher',title:'A researcher Tab, ready for you to say what it should work on. Closing it never stops it.',act:()=>openDraftResearcher()},
  ...(studiesAvailable?[{label:'New study…',title:'Your question, the inputs you choose and an allowance. You review each experiment before it counts.',act:()=>newStudy()}]:[]),
],'New researcher or study'),'mm-dhead-button study-research-new');
researchNew.prepend(tabMark('plus','mm-dhead-icon'));researchNew.setAttribute('aria-label','New researcher or study');researchNew.setAttribute('aria-haspopup','menu');researchNew.setAttribute('aria-expanded','false');
// Every researcher of this Project is one row of the list at any count (researchers-board.ts): grouped Needs you,
// Working, Done; a click opens its Tab; ⋯ holds Stop, Activity and Open. Stop goes through the same command the cards send.
const sendResearch=(command:unknown)=>{const {type,...rest}=command as {type:string}&Record<string,unknown>;return (bridge.request as unknown as (type:string,body:Record<string,unknown>)=>Promise<unknown>)(type,rest);};
/** A researcher's Tab (its row in Researchers, and Data's Open Researcher N). */
function openResearcher(rid:string):void{const row=bridge.researchers().find(item=>item.rid===rid);bridge.navigate(`#research/${encodeURIComponent(rid)}?day=${encodeURIComponent(row?.day||snapshot.session)}`);}
const board=new ResearchersBoard({open:rid=>openResearcher(rid),
  // "Made a presentation, a table and outlines ›" under its row, leading to its work in Data (ticket 66, D4).
  made:row=>madeBy(row),
  send:sendResearch,isNew:row=>bridge.researchNew(row),current:()=>researchTabs.selectedId()??'',
  activity:row=>{void (async()=>{if(row.sitting&&row.sitting!==snapshot.session&&!(await changeSession(row.sitting)))return;showActivity(true);})();},
  project:id=>{const p=snapshot.projects.find(item=>item.id===id);return p?sessionName(p,Date.now()/1000):'';},
  start:()=>openDraftResearcher(),
  // No picture of its own: the input it started from (this Project's, by name), else the specimen on this Project's Stage.
  cover:row=>{if(row.sitting&&row.sitting!==snapshot.session)return '';
   const picture=(asset:typeof snapshot.sources[number]|null|undefined)=>asset?asset.thumb||(asset.kind==='recording'?'':`/api/library/${encodeURIComponent(asset.id)}/file`):'';
   const named=(row.specimen??'').trim().toLowerCase();
   const started=named?snapshot.sources.find(asset=>[asset.title,asset.specimen].some(words=>(words??'').trim().toLowerCase()===named)):undefined;
   return picture(started)||picture(snapshot.source);}});
researcherBrowser.append(board.root);
function renderBoard():void{
  const material=resourceSession===snapshot.session?projectInputs(entries):[];
  projectResearchHome.update(snapshot.session, knownStudies, studiesNote, projectActivity, bridge.researchers(),
    {count:material.length,slow:resourceReads.slow||studyReads.slow,loading:studiesLoadedSession!==snapshot.session||(resourceSession!==snapshot.session&&!resultsFailed()),error:resultsFailed()?'Project material could not be refreshed.':'',groups:[...new Set(material.map(entry=>entry.material?.organization?.role).filter((role):role is string=>!!role))]});
  const all=bridge.researchers();
  board.update(todaysResearch(all,snapshot.session),{elsewhere:workingElsewhere(all,snapshot.session),studies:studiesAvailable?studyRows():null,studiesNote});
}
// New researcher (Will, 2026-10-23): "+" › New researcher opens a researcher Tab with an empty workspace, ready to start.
// Start sends the person's words to the Assistant as their request: the authorized way a researcher starts (the server
// starts one only on the user's own words in the conversation), and it shows in the conversation too. When the new
// researcher appears it takes over this Tab. Asking the Assistant directly remains the other way.
const DRAFT_RESEARCHER='draft:new-researcher';
let draftStart:DraftStart|null=null,draftWait=0;
const draftTab=()=>({id:DRAFT_RESEARCHER,name:'New researcher',line:draftLine(draftStart),kind:'',closable:!draftStart,closeTitle:draftStart?'Starting through the Assistant; this Tab closes once that settles.':'Close this Tab. Nothing was started.'});
const draftPage=el('article','study-view researcher-draft');
const draftBrief=el('textarea');draftBrief.rows=4;draftBrief.setAttribute('aria-label','What should it work on?');
draftBrief.placeholder='For example: measure how fast the cells in this recording swim, and compare the first and second halves.';
const draftStatus=el('p','study-view-notice');draftStatus.setAttribute('role','status');
const draftGo=button('Start',()=>startDraftResearcher(),'study-primary');draftGo.disabled=true;
const draftUses=el('p','draft-uses');
/** What it can use, named: this Project's inputs (the first few by name) and the models set up here. */
function renderDraftUses():void{const names=[...new Set([...snapshot.sources.map(asset=>asset.title),...(resourceSession===snapshot.session?projectInputs(entries).map(entry=>entry.title):[])])];const list=names.length?`Can use ${names.slice(0,3).join(', ')}${names.length>3?` and ${names.length-3} more`:''}, and the models set up in Models.`:'Can use this Project’s inputs (none yet) and the models set up in Models.';draftUses.textContent=`${list} Starts through the Assistant, so it shows in the conversation too.`;}
draftBrief.addEventListener('input',()=>{if(!draftStart)draftGo.disabled=!draftBrief.value.trim();});
// One column, one box, one button (Will, 2026-10-23: "cleaner"): the words are the page. What it may use and how it
// starts are one quiet line; a Study is one quiet sentence under it.
function buildDraftPage():void{
  const page=el('div','draft-page'),head=el('header','draft-head');
  head.append(el('h1','','New researcher'),el('p','','Works on something for you in this Project while you keep working. Closing its Tab never stops it.'));
  const composer=el('section','draft-composer');composer.setAttribute('aria-label','What should it work on?');
  draftBrief.rows=5;draftBrief.placeholder='What should it work on?\nFor example: measure how fast the cells in this recording swim and compare the first and second halves; or make an interactive lesson about what is on the Stage.';
  const foot=el('footer','draft-foot');
  foot.append(draftUses,draftGo);
  composer.append(draftBrief,foot,draftStatus);
  const also=el('p','draft-else');also.append('Prefer a question with experiments you review one by one? ',button('New study…',()=>newStudy(),'draft-link'));
  page.append(head,composer,also);draftPage.replaceChildren(page);
}
buildDraftPage();
function openDraftResearcher():void{
  surfaces.dismiss();if(drawerOpen&&section==='research')setDrawer(false);
  if(!draftStart){draftBrief.disabled=false;draftGo.disabled=!draftBrief.value.trim();}
  renderDraftUses();openResearchStage(draftPage);researchTabs.setVirtual(draftTab());draftBrief.focus({preventScroll:true});
}
function startDraftResearcher():void{
  const words=draftBrief.value.trim();
  if(!words){delete draftStatus.dataset.tone;draftStatus.textContent=notStartedWords.empty;draftBrief.focus();return;}
  const question=find('question') as HTMLTextAreaElement,form=question.closest('form') as HTMLFormElement|null;
  if(!form){draftNotStarted(notStartedWords.unavailable);return;}
  const before=question.value,request=`Start a researcher to ${words.replace(/^start a researcher to\s*/i,'')}`;
  let sent:string|null=null;const heard=(event:Event)=>{const detail=(event as CustomEvent<{requestId:string;text:string}>).detail;if(detail?.text===request)sent=detail.requestId;};
  // Through the composer (composer.set), so Send and the field's height follow the words it holds.
  document.addEventListener('hooke-ask-sent',heard);composer.set(request);form.requestSubmit();document.removeEventListener('hooke-ask-sent',heard);
  if(question.value===request){composer.set(before);draftNotStarted(notStartedWords.notReady);return;}
  draftStart=beginStart(snapshot.session,bridge.researchers().map(row=>row.rid),sent);
  draftBrief.disabled=true;draftGo.disabled=true;
  delete draftStatus.dataset.tone;draftStatus.textContent='Sent to the Assistant. This Tab becomes the researcher when it starts.';
  researchTabs.setVirtual(draftTab(),shownResearch===draftPage);setInspector(true);
}
/** Nothing started: the words stay, editable, with what happened. */
function draftNotStarted(words:string):void{
  clearTimeout(draftWait);draftStart=null;draftBrief.disabled=false;draftGo.disabled=!draftBrief.value.trim();
  draftStatus.dataset.tone='problem';draftStatus.replaceChildren(el('strong','','Not started'),el('span','',words));researchTabs.setVirtual(draftTab(),shownResearch===draftPage);
}
// The Assistant's answer to the request decides what this Tab says: a failure is said at once; an accepted request
// waits for the researcher, and says so plainly if none has started after a while (its reply explains why).
document.addEventListener('hooke-ask-settled',event=>{
  const settled=settleStart(draftStart,(event as CustomEvent<{requestId:string;ok:boolean;error:string|null}>).detail);
  if(settled.kind==='ignored')return;
  if(settled.kind==='failed'){draftNotStarted(notStartedWords.refused(settled.reason));return;}
  if(settled.kind==='declined'){draftNotStarted(notStartedWords.declined(settled.words));return;}
  draftStart=settled.start;draftStatus.textContent='The Assistant took your request. This Tab becomes the researcher when it starts.';
  researchTabs.setVirtual(draftTab(),shownResearch===draftPage);
  clearTimeout(draftWait);draftWait=window.setTimeout(()=>{if(draftStart)draftNotStarted(notStartedWords.noResearcher);},ADOPT_WAIT_MS);
});
/** The researcher started from this Tab's words takes it over: the Tab opens that researcher. */
function adoptStartedResearcher():void{
  const started=adoptedResearcher(draftStart,bridge.researchers(),snapshot.session);
  if(!started)return;
  clearTimeout(draftWait);draftStart=null;draftBrief.value='';draftStatus.textContent='';delete draftStatus.dataset.tone;draftBrief.disabled=false;draftGo.disabled=true;
  // The draft's pill becomes the researcher's in place; the Stage shows the researcher where the draft was.
  researchTabs.adopt(DRAFT_RESEARCHER,started.rid);bridge.navigate(`#research/${encodeURIComponent(started.rid)}?day=${encodeURIComponent(started.day||snapshot.session)}`);
}
function openResearchers(){bridge.refreshResearch();openResearchersDrawer(!!researchStage.hidden,hash=>bridge.navigate(hash),()=>{showSection('research');setDrawer(true);});}

const preview = el('section', 'study-preview'); preview.setAttribute('aria-label', 'Library preview');
const fixtureDialog = el('dialog', 'study-fixtures'); fixtureDialog.setAttribute('aria-label', 'Simulated scenarios');
document.body.append(fixtureDialog);
fixtureDialog.append(el('span', 'study-badge', 'SIMULATED · no model calls'), el('h2', '', 'Interaction scenarios'),
  el('p', '', 'These fixtures exercise interface states only. They create no project results and do not operate the camera.'));
const fixtureStatus = el('p', 'study-fixture-status', 'Choose a scenario.'); fixtureStatus.setAttribute('role', 'status');
let fixtureTimer = 0;
let fixtureSession = snapshot.title;
const fixtureFinding = button('View simulated finding', () => { fixtureStatus.textContent = `SIMULATED · Finding for ${fixtureSession}. Canned example text about bright regions. No scientific analysis or source lookup ran.`; });
const fixtureConversation = button('View simulated conversation', () => { fixtureStatus.textContent = `SIMULATED · Conversation in ${fixtureSession}. User asked for a study of bright regions. The fixture response is canned and is not saved to a real conversation.`; });
fixtureFinding.hidden = true; fixtureConversation.hidden = true;
const reuseSketch = el('section', 'study-reuse-sketch'); reuseSketch.hidden = true;
const methodName = el('input'); methodName.value = 'Bright region check'; methodName.setAttribute('aria-label', 'Simulated method name');
const methodLabel = el('label', '', 'Method name '); methodLabel.append(methodName);
const nextInput = el('select'); nextInput.setAttribute('aria-label', 'Simulated next input');
for (const [value, text] of [['mask', 'Retained masks · fixture r1 · revision 0'], ['preview', 'JPEG preview · display only']]) {
  const option = el('option', '', text); option.value = value; nextInput.append(option);
}
const inputLabel = el('label', '', 'Next input '); inputLabel.append(nextInput);
let simulatedMethod = '';
reuseSketch.append(el('h3', '', 'Simulated method and output reuse'),
  el('p', '', 'Fixture r1 is invented. These drafts last until reload and never change the staged source or scientific records.'),
  methodLabel, el('p', '', 'Settings: Bright objects; Gaussian 5 × 5, Otsu threshold, minimum 20 pixels. This draft refers to an operation, not a new model.'),
  button('Save simulated method', () => { simulatedMethod = methodName.value.trim() || 'Untitled method'; fixtureStatus.textContent = `SIMULATED · Saved draft “${simulatedMethod}”, version 1. No method was added to the capability catalog.`; }),
  inputLabel,
  button('Use output in simulated next step', () => {
    fixtureStatus.textContent = nextInput.value === 'preview'
      ? 'SIMULATED · JPEG preview refused as a mask input. Choose retained masks; display pixels do not preserve scientific labels.'
      : simulatedMethod
        ? `SIMULATED · Draft “${simulatedMethod}” → fixture r1 revision 0 masks → measurements. Scope: original pixel grid; destination: fixture project. Ready to review, no execution performed.`
        : 'SIMULATED · Save a method draft first, then bind the retained output revision.';
  }));
fixtureDialog.append(button('Slow research', () => { clearTimeout(fixtureTimer); fixtureSession = snapshot.title; fixtureFinding.hidden = true; fixtureConversation.hidden = true; fixtureStatus.textContent = `SIMULATED · Research running for ${fixtureSession}. Closing this panel will not reopen it on completion.`;
  fixtureTimer = window.setTimeout(() => { fixtureStatus.textContent = `SIMULATED · Research complete for ${fixtureSession}. Its finding and conversation remain linked to that project.`; fixtureFinding.hidden = false; fixtureConversation.hidden = false; fixtureButton.textContent = 'Test scenarios · complete'; }, 4500); }),
  button('Cloud failure', () => { fixtureStatus.textContent = 'SIMULATED · Provider unavailable. Your input is preserved. Retry runs this fixture again.'; }),
  button('Camera disconnect', () => { fixtureStatus.textContent = 'SIMULATED · Camera disconnected. Last frame held. Reconnect would request the camera. The actual camera is unchanged.'; }),
  button('Empty result', () => { fixtureStatus.textContent = 'SIMULATED · No regions matched. Input retained. Try another threshold or image.'; }),
  button('Reuse settings and output', () => { reuseSketch.hidden = !reuseSketch.hidden; fixtureStatus.textContent = 'SIMULATED · Explore a saved settings draft and a typed output-to-input transition below. Real Run still uses the staged source.'; }),
  fixtureFinding, fixtureConversation, reuseSketch,
  button('Retry fixture', () => { fixtureStatus.textContent = 'SIMULATED · Retry succeeded. No analysis was performed.'; }),
  button('Cancel fixture', () => { clearTimeout(fixtureTimer); fixtureStatus.textContent = 'SIMULATED · Cancelled. No work remains running.'; }), fixtureStatus,
  button('Close scenarios', () => fixtureDialog.close()));

function busy(): boolean { return run?.status === 'queued' || run?.status === 'running' || run?.status === 'stopping' || run?.status === 'unknown'; }
function savePreferences(): void { try { localStorage.setItem(storageKey, JSON.stringify({...preferences,leftWidth,toolsWidth,panelSizing:6})); } catch {} }
/** Opens or closes the left drawer. Opening it parks a docked page (the rail switched); the page stays open and the Tools
 * rail item brings it back. */
function setDrawer(open: boolean, park = true): void {
  if(open&&park)parkControls('left');
  if(open && dataOpen)setDataOpen(false,false,false);
  if(open&&innerWidth<1200)preferences.inspector=false;
  drawerOpen = open; preferences.drawer = open; applyLayout();
  if (!open) {if(section==='research'){if(researchListRoute(location.hash))bridge.navigate('#');researchersButton.focus();}else (railButtons.get(section) ?? railButtons.get('library'))?.focus();}
}
const brightControls=dockControls({id:'bright-objects',title:'Bright objects',content:runBox});
if(brightControls){
  // Its run page lives in Tools (capability-controls.ts), so the open card holds only a way back to it: one secondary
  // button, and no accent while the page is elsewhere (parked, floating or on the right).
  const show=brightControls.launcher;show.className='capability-controls-launcher';show.textContent='Show page';
  show.setAttribute('aria-label','Show the Bright objects page');show.title='Show the Bright objects page; it stays open while you look elsewhere';
  brightDetails.dataset.docked='true';brightDetails.append(show);
  brightDetails.addEventListener('toggle',()=>{if(brightDetails.open)brightControls.show();});
}
window.addEventListener('workspace-show-agent',()=>openRightTab('agent')); // "Ask the Assistant which to trust"
function openRightTab(tab: 'agent' | 'analyze'): void {
  if (tab === 'analyze') { showSection('tools'); setDrawer(true); toolBrowser.showReady(); brightDetails.open=true; runButton.focus(); }
  else { setInspector(true); }
}
function setInspector(open: boolean): void {
  if(assistantAside()){assistantLayer.set(open);return;}
  // The Assistant and the reader share the right edge, one at a time: the Assistant coming back closes the reader.
  if(open&&readerShown)closeReader(false,false);
  if(open)parkControls('right');
  preferences.inspector = open; if(open&&innerWidth<1200){drawerOpen=false;setDataOpen(false,false);} applyLayout();
  if(open) focusOpened(()=>rightPanel.hidden?null:find('question')); else agentButton.focus();
}
/** What the left side shows now, for its decisions (left-side.ts). */
function leftState(): LeftState {
  const docked = controlDeck.visibleOn('left');
  return { drawerOpen, section, dataOpen, dataMode, leftDocked: docked ? (docked.page ? 'page' : 'inspector') : null, parked: !!controlDeck.parkedPage(), running: busy() };
}
/** The Tools rail item: a parked run page comes back; with a page in view the Tools list shows (the page parks); else
 * the list opens, or closes when it is the place showing. */
function openTools(): void {
  bridge.navigate('#');
  const press = toolsPress(leftState());
  if (press === 'show-parked') { controlDeck.parkedPage()?.show(); return; }
  if (press === 'park-and-list') { controlDeck.visibleOn('left')?.park(); showSection('tools'); setDrawer(true); return; }
  if (press === 'close') setDrawer(false); else { showSection('tools'); setDrawer(true); }
}
/** A control came into view docked on a side. A tool page on the left is a page of Tools: the drawer is on Tools under
 * it, so the rail says Tools (nothing is parked). An inspector on the left closes the drawer and the Data window, and
 * the rail shows no place as current: the inspector's window title says what it is. */
function controlShown(side: 'left' | 'right', page: boolean): void {
  if (side === 'left') {
    const next = afterShown({ drawerOpen, section, dataOpen }, side, page);
    if (dataOpen && !next.dataOpen) { dataOpen = false; dataWindow.hidden = true; }
    if (next.section !== section) showSection(next.section as StudySection);
    drawerOpen = next.drawerOpen; preferences.drawer = drawerOpen; applyLayout();
  }
  renderControlMarks();
}
/** Which rail item is current: the place the drawer or the Data window shows, Tools under a run page, none under an
 * inspector. Rail and content always agree. */
function renderRailCurrent(): void {
  // A surface over the workspace (Models, Library, Help, Shortcuts, Settings) is the one current item while it shows: the
  // drawer under it is not also current, so the rail and the content agree; it comes back when the surface closes.
  const current = document.body.dataset.studyOverlay ? '' : railCurrent(leftState());
  railButtons.forEach((b, key) => b.setAttribute('aria-current', current === key ? 'page' : 'false'));
  researchersButton.setAttribute('aria-pressed', String(current === 'research'));
  dataButton.setAttribute('aria-pressed', String(current === 'data'));
  activityButton.setAttribute('aria-pressed', String(current === 'activity'));
}
/** The marks that follow docked pages: the Tools item's lit cell (a run works) or dot (a run page is parked), only
 * while Tools is not the place showing; the Assistant's "<tool> controls" chip; and the Assistant button pressed only
 * while the Assistant itself shows. */
function renderControlMarks(): void {
  renderRailCurrent();
  applyRailMark(toolsButton, toolsMark(leftState(), controlDeck.parkedPage()?.options.title), () => litCell('working', { size: 10, decorative: true }));
  const right = controlDeck.parkedRight();
  const aside = assistantAside();
  const head = assistantHeader(right?.options.title ?? null, aside ? assistantLayer.open : preferences.inspector && !readerVisible(), !aside && !!controlDeck.visibleOn('right'));
  controlsChip.hidden = !head.chip; if (controlsChip.textContent !== (head.chip ?? '')) controlsChip.textContent = head.chip ?? '';
  if (right) controlsChip.title = `Show ${right.options.title} on the right again; the Assistant stays open underneath`;
  agentButton.setAttribute('aria-expanded', String(head.pressed)); agentButton.setAttribute('aria-pressed', String(head.pressed));
}
/** A press of a rail item, a panel's ×, the Assistant or its shortcut: the state changes inside `change`, and the
 * layout is written once when it returns, however many of setDrawer, setDataOpen, showSection and setInspector it
 * went through. Focus moving into a panel it opens waits for the first frame (`focusOpened`). When a side column opens
 * or closes, the Stage and the columns move to the new layout together, in one motion (side-motion.ts). */
function press(change: () => void): void {
  const before = layoutHeld === 0 ? sidesShown() : null;
  layoutHeld++;
  try { change(); } finally {
    if (--layoutHeld === 0) {
      sideRules();
      const after = before ? sidesShown() : null;
      const tracks = before && after ? sideTracks(before, after, studioBlock, document.getElementById('working-strip')) : [];
      if (tracks.length) expectSideMotion(); // what the layout write asks to measure waits for the motion's end
      try { if (layoutWanted) { layoutWanted = false; applyLayout(); } }
      finally {
        if (before && after) moveSides(before, after, tracks); // also ends the wait if the layout failed
        for (const run of afterLayout.splice(0)) run();
      }
    }
  }
}
const studioBlock = document.querySelector<HTMLElement>('.studio')!;
const stageWidth = (sides: Sides) => sides.viewport - sides.stage.left - sides.stage.right;
/** The sides as the state lays them out (window px), for a press's motion: the column each side shows, the Stage's
 * insets as the CSS decides them (workspace-study.css; the floating drawer of workspace-feel.css; a docked page's
 * reservation, capability-controls.ts) and the Working strip's reach under the Assistant. Nothing is measured. */
function sidesShown(): Sides {
  const vw = innerWidth, aside = assistantAside(), reading = readerVisible();
  const leftDock = controlDeck.visibleOn('left'), rightDock = controlDeck.visibleOn('right');
  const { rail, left: leftNow, right: reserved } = sideWidths();
  // A docked page takes the Tools width on the left, its own on the right (the deck's sideRect).
  const leftPage = leftDock ? Math.min(toolsNow(), vw - rail) : null, rightPage = rightDock ? Math.min(rightDock.preference.width, vw) : null;
  const inspector = reading ? readerNow() : preferences.inspector && !aside ? preferences.width : 0;
  const { left: stageLeft, right: stageRight } = stageInsets({ viewport: vw, rail, left: leftNow, right: reserved, inspector, leftPage, rightPage });
  const stage = { left: stageLeft, right: stageRight };
  const left: SideColumn = leftDock ? { node: leftDock.shell, width: leftPage!, layer: true }
    : dataOpen ? { node: dataWindow, width: leftNow } : drawerOpen ? { node: drawer, width: leftNow } : null;
  // Beside a researcher's or a Study's page the Assistant is a layer with its own entrance (assistant-layer.ts).
  const right: SideColumn = rightDock ? { node: rightDock.shell, width: rightPage!, layer: true }
    : reading ? { node: reader.root, width: Math.min(readerNow(), vw - rail) }
    : !aside && preferences.inspector ? { node: rightPanel, width: Math.min(preferences.width, vw - rail) } : null;
  return { viewport: vw, rail, stage, left, right, under: rightDock ? 0 : stage.right };
}
/** Moves the Stage and the side columns from `before` to `after` (nothing when no column opens or closes). A Stage
 * about to narrow fits its Tab row once, in the first frame, for the width it lands at, so no Tab is cut meanwhile. */
function moveSides(before: Sides, after: Sides, tracks: ReturnType<typeof sideTracks>): void {
  playSideMotion(tracks, node => node === drawer ? !drawerOpen : node === dataWindow ? !dataOpen
    : node === rightPanel ? !(assistantAside() ? assistantLayer.open : preferences.inspector && !readerVisible()) : node === reader.root ? !readerVisible() : true);
  if (tracks.length && stageWidth(after) < stageWidth(before)) requestAnimationFrame(() => workspaceViews.fitAhead(stageWidth(after)));
}
/** The side rules every layout keeps: below 1200 px the left column and the Assistant are never open together. */
function sideRules(): void {
  if(innerWidth<1200&&(drawerOpen||dataOpen)){preferences.inspector=false;if(readerShown)closeReader(false,false);}
}
const setShown=(node:HTMLElement,shown:boolean)=>{if(node.hidden===shown)node.hidden=!shown;};
/** Focus moving into a panel a press opens (the Assistant's composer, Data's tab): in the press's first frame, since
 * focusing needs the new styles and inside the press it forced the page's style pass (b041); at once outside a press.
 * Nothing moves if the panel closed meanwhile. */
function focusOpened(target: () => HTMLElement | null | undefined): void {
  const go = () => target()?.focus({ preventScroll: true });
  if (layoutHeld) afterLayout.push(() => requestAnimationFrame(go)); else go();
}
/** The rail's and the side columns' widths as this layout shows them, from state alone (never measured): the deck
 * reserves them beside the Stage (capability-controls.ts occupy). The rail is --study-rail (workspace-feel.css). */
function sideWidths(): { rail: number; left: number; right: number } {
  const rail = 72, aside = assistantAside();
  const left = drawerOpen || dataOpen ? Math.min(!dataOpen && section === 'tools' ? toolsNow() : leftNowWidth(), innerWidth - rail) : 0;
  const right = readerVisible() ? readerNow() : (aside ? assistantLayer.open : preferences.inspector) ? (aside ? 360 : preferences.width) : 0;
  return { rail, left, right };
}
function applyLayout(): void {
  if (layoutHeld) { layoutWanted = true; return; }
  drawerPresentation++;
  sideRules();

  document.body.dataset.studyLayout = 'global';
  const aside = assistantAside(), reading = readerVisible();
  document.body.dataset.studyInspector = String((preferences.inspector || reading) && !aside);
  document.body.dataset.assistantOver = String(aside && assistantLayer.open);
  document.body.dataset.studyDrawer = String(drawerOpen||dataOpen);
  // The right edge holds the reader or the Assistant (one at a time); the Stage keeps the room of the one shown.
  document.body.style.setProperty('--study-inspector-width', `${reading ? readerNow() : preferences.width}px`);
  { const width = `${readerNow()}px`; if (reader.root.style.getPropertyValue('--reader-width') !== width) reader.root.style.setProperty('--reader-width', width); }
  const leftNow=!dataOpen&&section==='tools'?toolsNow():leftNowWidth();
  document.body.style.setProperty('--study-left-width', `${leftNow}px`);
  // Whether the drawer and the Data window sit beside the Stage or float over it under the Tab row and the Stage bar
  // (never over the Tabs) is the deck's stage insets' decision (data-drawer-dock, capability-controls.ts), made with
  // what is docked on the right too; it runs once this layout is in place (refreshSoon, below).
  resize.setAttribute('aria-valuenow',String(preferences.width)); leftResize.setAttribute('aria-valuenow',String(leftNow));dataResize.setAttribute('aria-valuenow',String(leftNow));
  drawer.dataset.section=section;
  // Written only when they change: an unchanged `hidden = true` still queues a mutation record for every observer. A
  // column sliding out is left to the motion, which hides it when it has left.
  setShown(rail,true); setShown(drawer,drawerOpen||sideLeaving(drawer)); setShown(rightPanel,(aside?assistantLayer.open:preferences.inspector&&!reading)||sideLeaving(rightPanel));
  setShown(reader.root,(reading&&!!reader.current())||sideLeaving(reader.root));
  setShown(notebook,true); setShown(inspector,true);
  renderControlMarks();
  setShown(find('session-rail'),false);
  if(actionSlot.lastChild!==bridge.actions)actionSlot.append(bridge.actions);
  savePreferences(); bridge.sidesChanged();
  controlDeck.refreshSoon();
}
const notice=el('div','study-shell-notice');notice.setAttribute('role','status');document.body.append(notice);
function actionError(error: unknown): void { notice.setAttribute('role','alert'); actionNotice.textContent = error instanceof Error ? error.message : String(error); const dismiss=button('×',()=>notice.replaceChildren());dismiss.setAttribute('aria-label','Dismiss notification');notice.replaceChildren(el('span','',actionNotice.textContent??''),failureHelp(()=>{notice.replaceChildren();bridge.navigate('#help');}),dismiss); }
const workspaceViews:WorkspaceViews = new WorkspaceViews({
 // "+" adds a Tab, a researcher or a study; each opens as a Tab of its own (the row holds Tabs only).
 extraClosed:()=>researchTabs.closedVirtual().map(tab=>({id:tab.id,name:tab.name,reopen:()=>{researchTabs.reopenVirtual(tab.id);}})),
 // ⌄ All Tabs: closed researcher and Study Tabs, then this Project's studies not open as a Tab.
 menuTabs:()=>({closed:researchTabs.closedTabs().map(tab=>({id:tab.id,name:tab.name,title:tab.title,mark:tab.mark,act:tab.reopen})),
  studies:knownStudies.filter(study=>!researchTabs.report().some(tab=>tab.id===`study:${study.id}`)).map(study=>({id:`study:${study.id}`,name:studyName(study),title:`${studyName(study)} · ${studyState(study)}`,mark:tabMark('study'),act:()=>void openStudy(study.id)}))}),
 closeTarget:kind=>kind==='data'?dataButton:researchersButton,
 // A click on a work Tab leaves a researcher's page first (the capture listener below); the Assistant's switch too.
 leaveResearch:()=>{if(researchStage.hidden)return true;if(!canLeaveStudy())return false;clearStudyDraftMarkers();closeResearchStage();return true;},
 research:{report:()=>researchTabs.report(),close:id=>researchTabs.closeTab(id),
  switch:async id=>{if(!researchTabs.show(id))return false;const until=Date.now()+3000;while(Date.now()<until){if(researchTabs.selectedId()===id&&!researchStage.hidden)return true;await new Promise(resolve=>setTimeout(resolve,50));}return researchTabs.selectedId()===id;}},
 researcherTab:rid=>researchTabs.has(rid),
 describeInput:source=>{const asset=snapshot.sources.find(item=>item.id===source);return asset?asset.kind==='recording'?'recording':'picture':null;},
 // A picture's own file stands in for a missing thumbnail (as Inputs does); a recording has only its thumbnail.
 inputThumb:source=>{const asset=snapshot.sources.find(item=>item.id===source);return asset?asset.thumb||(asset.kind==='recording'?null:`/api/library/${encodeURIComponent(asset.id)}/file`):null;},
 addChoices:()=>[{label:'New researcher',title:'A researcher Tab, ready for you to say what it should work on',act:()=>openDraftResearcher()},
  {label:'New study…',title:'A question with experiments you review before they count',act:()=>newStudy()}],
 // While a researcher's study holds the Stage, ⌘W goes back from it (never closing the hidden work Tab); any other Tab
 // command goes back first, then acts on the Tabs you can see.
 intercept:name=>{if(!researchStage.hidden&&!canLeaveStudy(()=>workspaceViews.command(name as Parameters<WorkspaceViews['command']>[0])))return true;clearStudyDraftMarkers();if(researchTabs.command(name))return true;if(researchStage.hidden)return false;closeResearchStage();return name==='close';},
 captureInput:()=>{const state=bridge.state();if(!state.ready)return null;const saved=bridge.capturePresentationSync();return saved.source?{source:saved.source,position:saved.source==='live'?0:saved.position,...(saved.retained?{retained:saved.retained}:{})}:null;},
 selectInput:bridge.selectTabInput,
 inputBlockReason:input=>inputChangeBlockReason(input,{onStage:bridge.state().onStage, onStageRetained:bridge.capturePresentationSync().retained},busy(),bridge.switchBlockReason()?.replaceAll('switching projects','changing the input')??null),
 // A reader Tab (Open in new Tab): the same reader, full width; its mark is its item's kind.
 reader:view=>showReaderTab(view),legacy:view=>showLegacyTab(view),openResearcher:rid=>openResearcherTab(rid),readerMark:view=>{const item=readerItem(view),entry=item?readerEntryOf({id:item.resource.id,day:item.resource.day,version:item.resource.version}):null;return entry?readerMark(entry):'output';},
 storageKey,request:bridge.request,restoreItems:bridge.restoreItems,placeItem:bridge.placeItem,frontItem:bridge.frontItem,captureItems:()=>bridge.capturePresentationSync().views,openData:()=>setDataOpen(true),notice:actionError,layoutChanged:()=>{snapshot=bridge.state();bridge.layoutChanged();renderContext();}});
window.addEventListener('workspace-run-destination',event=>{const detail=(event as CustomEvent).detail;
 if(detail.session!==bridge.state().session){detail.result=Promise.reject(new Error('The originating Project changed.'));return;}
 if(!canLeaveStudy()){detail.result=Promise.reject(new Error('Resolve the current Study form before choosing a new destination.'));return;}clearStudyDraftMarkers();
 closeResearchStage();detail.result=workspaceViews.newRunOrigin(detail.run,detail.title,detail.session);
});
configureWorkspaceCompare({owner:()=>workspaceViews.tabOwner(),current:owner=>workspaceViews.ownsTabChoice(owner)&&bridge.state().session===owner.session,
 state:()=>workspaceViews.comparisonState(),create:(selections,owner)=>workspaceViews.createComparison(selections,owner),
 setSync:(sync,owner)=>workspaceViews.setComparisonSync(sync,owner)});
bridge.bindInputNavigation((source,title,project,position,retained)=>{
 if(project!==bridge.state().session)return Promise.reject(new Error('The Project changed. Reopen the input in its original Project.'));
 return workspaceViews.openInput({source,position,...(retained?{retained}:{})},title);
});
// A Data card dropped on the Tab row opens its result there, as Open in Tab… does, once it is checked to be a result
// of the Project still open.
workspaceViews.acceptEntryDrops((data,to)=>{
  const owner=workspaceViews.tabOwner(),entry=draggedEntry(data,owner,entries);
  if(!entry||owner.session!==snapshot.session){actionError(new Error('That result or Project changed since the drag began. Drag its current Data card again.'));return;}
  void openEntryIn(entry,owner,()=>'tab' in to?workspaceViews.goTo(to.tab,owner):workspaceViews.newTab(owner));
});
configureDataTableDocuments({owner:()=>workspaceViews.tabOwner(),current:owner=>workspaceViews.ownsTabChoice(owner)&&bridge.state().session===owner.session,
 settle:()=>workspaceViews.prepareScientificView(),remember:(...args)=>workspaceViews.rememberResource(...args),
 analyze:async(pin,table,owner)=>{const tab=document.body.dataset.activeWorkspaceView;const {setupTableDerived}=await import('./datatable-derived-presentation');if(workspaceViews.ownsTabChoice(owner)&&tab===document.body.dataset.activeWorkspaceView)setupTableDerived('analysis',pin,table,owner);},
 graph:async(pin,table,owner)=>{const tab=document.body.dataset.activeWorkspaceView;const {setupTableDerived}=await import('./datatable-derived-presentation');if(workspaceViews.ownsTabChoice(owner)&&tab===document.body.dataset.activeWorkspaceView)setupTableDerived('graph',pin,table,owner);},
 export:async pin=>{window.open(`/api/datatables/${encodeURIComponent(pin.id)}.xlsx?${new URLSearchParams({session:pin.session,version:String(pin.version)})}`,'_blank','noopener');}});
configureScientificDocuments({currentProject:()=>snapshot.session,recovery:{storage:localStorage,prefix:`${storageKey}:scientific-recovery`},drafts:{storage:localStorage,prefix:`${storageKey}:scientific-scripts`},prepare:()=>workspaceViews.prepareScientificView(),transport:bridge.scientificTransport,
 remember:(project,placement,resource,settings)=>workspaceViews.rememberView(project,placement,resource,settings),
 location:(project,tab)=>{const row=snapshot.projects.find(p=>p.id===project);const title=row?sessionName(row,Date.now()/1000):project;const view=workspaceViews.tabTitle(project,tab);return view?`${title} › ${view}`:`${title} · another work Tab`;}});
drawerBody.id='workspace-research-panel';
// Closed researcher Tabs and the row's order are remembered per Project (localStorage, so a closed Tab does not return on
// relaunch); researchers opened this session keep their Tab (sessionStorage).
const researchTabs:WorkspaceResearchTabs=new WorkspaceResearchTabs({host:()=>workspaceViews.researchHost(),storage:localStorage,session:sessionStorage,key:`${storageKey}:research-tabs`,
 open:row=>{bridge.researchOpened(row.rid);bridge.navigate(`#research/${encodeURIComponent(row.rid)}?day=${encodeURIComponent(row.day||snapshot.session)}`);},
 close:()=>closeResearchStage(),isNew:row=>bridge.researchNew(row),isNews:row=>bridge.researchNews(row),
 closed:tab=>workspaceViews.researchClosed(tab.text,tab.name,tab.reopen,tab.from,tab.mark),
 openVirtual:id=>{if(id===DRAFT_RESEARCHER)openDraftResearcher();else if(id.startsWith('study:'))void openStudy(id.slice(6));}});
// A researcher opens as a Tab of its own (Will, 2026-10-23): its study fills the Stage's place, its pill in the row below
// is the selected Tab, and closing it (or choosing a work Tab) goes back without stopping it. The specimen and the
// Tab's windows stay mounted under it, hidden, never detached: the live view keeps running.
const researchStage=el('section','study-research-stage');researchStage.hidden=true;researchStage.setAttribute('aria-label','Researcher');
const researchHolder=el('div','study-research-holder');
const researchBack=button('',()=>closeResearchStage(),'study-research-back');researchBack.title='Back to the work Tab (the researcher keeps working)';
const researchBackWords=el('span','','Main');researchBack.append(tabMark('left','study-research-back-icon'),researchBackWords);
// The research stage's bar sits in the Stage bar's left (workspace-feel plan): back, what it is working on, and Stop while
// it can be stopped. Stop says "Stop requested" until the server lists the researcher as stopped (research-stop.ts).
const researchBar=el('header','study-research-stage-bar');researchBar.setAttribute('aria-label','Researcher');
// The study at a glance: its objective, its state, what it has spent of its cap, and what it needs from you.
const researchStudy=el('div','study-research-summary');researchStudy.setAttribute('aria-live','polite');
let researchStopRid='';
const researchStop=button('',()=>{if(researchStopRid)requestStop(researchStopRid,sendResearch);renderResearchStudy();},'study-research-stop');researchStop.hidden=true;
const researchStopWords=el('span','','Stop');researchStop.append(tabMark('stop','study-research-stop-icon'),researchStopWords);
researchBar.append(researchBack,el('span','study-research-divider'),researchStudy,researchStop);
let researchStudyKey='',researchClockTimer:ReturnType<typeof setInterval>|null=null,researchSince:number|null=null;
const researchClock=el('span','study-research-clock mm-num');
/** The bar's clock, once a second while a researcher in front works (text only; nothing per frame). */
function tickResearchClock():void{
  const counting=researchSince!==null&&!researchStage.hidden;
  if(counting){const words=clock(Math.max(0,Date.now()/1000-researchSince!)*1000);if(researchClock.textContent!==words)researchClock.textContent=words;}
  if(counting&&researchClockTimer===null)researchClockTimer=setInterval(tickResearchClock,1000);
  else if(!counting&&researchClockTimer!==null){clearInterval(researchClockTimer);researchClockTimer=null;}
}
function renderResearchStudy():void{
  if(researchStage.hidden){tickResearchClock();return;}
  const rid=location.hash.startsWith('#research/')?decodeURIComponent(location.hash.slice(10).split('?')[0]):'';
  // The open Study is the row's selected Tab; a researcher's own pill is its Tab already.
  if(openStudyNow)researchTabs.setVirtual({id:`study:${openStudyNow.id}`,name:studyName(openStudyNow),line:studyNeeds(openStudyNow)?'Needs you':studyState(openStudyNow)});
  else if(shownResearch===draftPage)researchTabs.setVirtual(draftTab());else researchTabs.virtualLeft();
  const row=openStudyNow?null:bridge.researchers().find(s=>s.rid===rid);
  // Work that finishes while its page is in front is being read: its pill does not linger green.
  if(row&&isReady(row)&&!isWorking(row))bridge.researchOpened(row.rid);
  // Stop, while this researcher can be stopped (starting, working, queued or waiting for you). Once pressed it goes, and
  // the words say "Stop requested" until the server lists the researcher as stopped (research-stop.ts).
  const bar=row?researchStageBar(row):null;
  researchStopRid=row&&bar!.stop?row.rid:'';researchStop.hidden=!researchStopRid;
  if(row&&researchStopRid){const words=stopButtonWords(row);if(researchStopWords.textContent!==words.text)researchStopWords.textContent=words.text;researchStop.disabled=words.disabled;researchStop.title=words.title;}
  // A Study's bar says what it is and its allowance, once (the page never repeats it), with Pause and Stop (moved in
  // from its page); what needs you is in its conversation.
  if(!openStudyNow)researchBar.querySelector('.study-view-controls')?.remove();
  researchSince=bar?.since??null;
  // The bar names what it was asked to do (research-tab.ts researchStageBar), not the findings' title its page shows.
  const summary=openStudyNow?{title:studyName(openStudyNow),words:studyAllowanceWords(openStudyNow),tip:studyAllowanceTitle(openStudyNow)}
    :row&&bar?{title:bar.title,words:bar.words,tip:bar.tip}:null;
  const key=JSON.stringify([summary,researchSince!==null]);if(key===researchStudyKey){tickResearchClock();return;}researchStudyKey=key;
  researchStudy.replaceChildren();
  // Its Tab's mark says what kind of page it is (and its lit cell); the bar says what it works on and how it stands.
  if(!summary&&shownResearch===draftPage){researchStudy.append(el('strong','study-research-objective','New researcher'));tickResearchClock();return;}
  if(!summary){researchStudy.append(el('span','study-research-stage-note','Closing this never stops the researcher.'));tickResearchClock();return;}
  const objective=el('strong','study-research-objective',summary.title);objective.title=summary.title;
  const facts=el('span','study-research-facts');facts.append(el('span','',summary.words));if(summary.tip)facts.title=summary.tip;
  if(researchSince!==null)facts.append(researchClock);
  researchStudy.append(objective,facts);
  tickResearchClock();
}
researchStage.append(researchHolder);find('stage').append(researchStage);workspaceViews.stageSlots().research.append(researchBar);
// The pages stay mounted and hidden rather than rebuilt (the researcher's page, the draft, the open Study): a Tab
// switch shows one and hides the rest, so coming back is instant. Older Study pages go; the two the person may return
// to any moment stay.
let shownResearch:HTMLElement|null=null;
function openResearchStage(node:HTMLElement):void{
  if(node!==currentStudyPage){if(!canLeaveStudy(()=>openResearchStage(node)))return;++studyOpening;retireStudyStaging?.();retireStudyStaging=null;currentStudyPage=null;openStudyNow=null;clearInterval(studyTimer);}
  if(drawerOpen&&section==='research')setDrawer(false);
  for(const child of [...researchHolder.children] as HTMLElement[])if(child!==node&&child!==draftPage&&!child.id)child.remove();
  if(node.parentElement!==researchHolder)researchHolder.append(node);
  for(const child of researchHolder.children as HTMLCollectionOf<HTMLElement>)child.hidden=child!==node;
  shownResearch=node;researchStage.hidden=false;document.body.dataset.researchOpen='true';researchStudyKey='';
  // The Assistant panel steps aside on a researcher's or a Study's Tab (its Stage bar button opens it over the page).
  applyLayout();
  // Opening a researcher's page counts as reading it: its finished pill leaves the Working strip (shelf.ts markOpened).
  const openedRid=location.hash.startsWith('#research/')?decodeURIComponent(location.hash.slice(10).split('?')[0]):'';
  if(openedRid&&bridge.researchers().some(s=>s.rid===openedRid))bridge.researchOpened(openedRid);
  renderResearchStudy();
  // Back names the work Tab it returns to, on every researcher, Study and draft page (the mock's "‹ Main").
  researchBackWords.textContent=openTab()||'Main';researchBack.setAttribute('aria-label',`Back to ${openTab()||'Main'}`);
  renderContext();
}
let openStudyNow:Effort|null=null,studyTimer=0,studyOpening=0;
let currentStudyPage:HTMLElement|null=null;
let retireStudyStaging:(()=>void)|null=null;
/** Capture a Project opening, not only its ID: away-and-back never renews an old request. */
window.addEventListener('workflow-open-study',event=>{const value=(event as CustomEvent<{id:string;session:string}>).detail;if(value.session===snapshot.session&&/^p[0-9a-f]{32}$/.test(value.id)){bridge.navigate('#');void openStudy(value.id);}});
async function openStudy(id:string,material?:{reference:Reference;title:string}):Promise<void>{
  if(!canLeaveStudy(()=>void openStudy(id,material)))return;
  const owner=workspaceViews.tabOwner(),session=owner.session,opening=++studyOpening;
  const current=()=>opening===studyOpening&&inputOwnerCurrent(owner)&&!switching;
  let study:Effort,providers:StudyProvider[]=[],availableInputs:StudyInput[]=[];
  try{[study,providers,availableInputs]=await Promise.all([readStudy(id,session),studyReadiness(session,fetch,id).catch(()=>[]),studyInputs(session).catch(()=>[])]);}catch(error){if(current())actionError(error);return;}
  if(!current())return;
  const page=el('article');currentStudyPage=page;
  const apply=async(work:()=>Promise<Effort>)=>{
    if(!current()||currentStudyPage!==page||researchStage.hidden)throw new Error('The Project or Study view changed. Return to the original Study.');
    const next=await work();if(current()&&currentStudyPage===page&&!researchStage.hidden)draw(next);
  };
  const openRetained=async(ref:{id:string;revision:number},title:string)=>{
    if(!current()||currentStudyPage!==page||researchStage.hidden)throw new Error('Return to the originating Study to open its result.');
    if(!canLeaveStudy(()=>void openRetained(ref,title)))return;
    clearStudyDraftMarkers();
    // Evidence opens in its own workspace Tab; it must never appear behind the Study on Main.
    if(!await workspaceViews.newTab(owner)||!inputOwnerCurrent(owner)||switching||!current()||currentStudyPage!==page||researchStage.hidden)throw new Error('The evidence Tab could not be opened. Its result stays in Project Data.');
    closeResearchStage();
    const opened=await openStudyScientific({title,resource:{id:ref.id,day:session,version:ref.revision,picture:null},zoom:1,pan:[.5,.5]});
    if(!opened)throw new Error('It could not be opened. Its saved revision remains in Project Data.');
  };
  const conversation=studyConversation(study,{
    material,
    current:()=>current()&&currentStudyPage===page&&!researchStage.hidden,
    open:async(ref,title)=>{
      if(ref.session!==session)throw new Error('Return to the originating Study.');
      await openRetained(ref,title);
    },
  });
  const draw=(next:Effort)=>{
    if(!current())return;openStudyNow=next;retireStudyStaging?.();
    conversation.update(next);
    renderStudy(page,next,{
      conversation:conversation.root,
      localWorkflows:true,
      readiness:async()=>{const latest=await studyReadiness(session,fetch,next.id);if(!current()||currentStudyPage!==page)throw new Error('Return to the original Study.');providers=latest;return latest;},
      control:action=>apply(()=>controlStudy(next.id,session,action)),
      start:fields=>apply(()=>startStudy(next.id,session,fields)),
      collect:(task,action)=>apply(()=>collectStudyTask(next.id,session,task,action)),
      recover:(task,providerSession)=>apply(()=>recoverStudyTask(next.id,session,task,providerSession)),
      evidence:async task=>{if(!current())throw new Error('Return to the original Project.');const evidence=await studyEvidence(next.id,session,task);if(!current())throw new Error('The Project changed while reading evidence.');return evidence;},
      review:(task,hash,outcome,review)=>apply(()=>reviewStudyTask(next.id,session,task,hash,outcome,review)),
      finding:(request,statement,tasks)=>apply(()=>retainStudyFinding(next.id,session,request,statement,tasks)),
      openFile:(ref,name)=>openRetained(ref,name),
      // Use the shared Assistant admission path without replacing its draft. Open the reply panel only after
      // admission; unavailable or pending requests leave the question in this Study.
      askBlocked:()=>!current() || currentStudyPage!==page || researchStage.hidden ? 'Return to this Study before asking.' : bridge.askBlocked(),
      setupAssistant:()=>bridge.navigate('#models'),
      ask:(text:string)=>{
        if(!current() || currentStudyPage!==page || researchStage.hidden || bridge.askBlocked())return false;
        if(!bridge.askAssistant(text))return false;
        assistantLayer.set(true);return true;
      },
    },providers,availableInputs);
    // Pause and Stop sit in the Study's bar (Study target); they keep their own actions and guards.
    researchBar.querySelector('.study-view-controls')?.remove();
    const pageControls=page.querySelector<HTMLElement>('.study-view-controls');if(pageControls)researchBar.append(pageControls);
    const ownsView=()=>current()&&currentStudyPage===page&&!researchStage.hidden;
    const preparation=page.querySelector<HTMLElement>('.study-view-preparation');
    if(preparation && providers.length){
    const stopStaging=mountScientificStaging(preparation,{id:next.id,session,current:ownsView});
    const stopAdmission=mountScientificAdmission(preparation,{id:next.id,session,current:ownsView,openResult:ref=>openRetained(ref,'Retained scientific result')});
    retireStudyStaging=()=>{stopStaging();stopAdmission();};
    }else retireStudyStaging=null;
    researchStudyKey='';renderResearchStudy();
  };
  if(location.hash.startsWith('#research/'))bridge.navigate('#');
  openResearchStage(page);draw(study);void conversation.refresh();clearInterval(studyTimer);
  // Only local retained state is read. Provider collection is a separate explicit action.
  studyTimer=window.setInterval(()=>{
    if(!current()||researchStage.hidden||currentStudyPage!==page){clearInterval(studyTimer);return;}
    void conversation.refresh();
    if(page.dataset.pending||page.querySelector('[data-pending]')||page.querySelector('[data-draft]')||page.dataset.draft||page.dataset.reviewOpen||page.contains(document.activeElement))return;
    void readStudy(id,session).then(next=>{if(current()&&currentStudyPage===page&&!page.dataset.pending&&!page.querySelector('[data-pending]')&&!page.querySelector('[data-draft]')&&!page.dataset.draft&&!page.dataset.reviewOpen&&!page.contains(document.activeElement)&&(next.revision!==openStudyNow?.revision||next.stopPending!==openStudyNow?.stopPending))draw(next);}).catch(()=>{});
  },4000);
}
/** New Study saves a draft. Provider execution requires the separate Start action. */
function newStudy(initial?:{reference:Reference;title:string},created?:(study:Effort)=>void):void{
  const owner=workspaceViews.tabOwner(),session=owner.session;if(!session)return;
  const dialog=el('dialog','workspace-recovery-dialog study-new-study'),form=el('form');form.method='dialog';
  const current=()=>inputOwnerCurrent(owner)&&!switching&&dialog.isConnected;
  const request=crypto.randomUUID();let pending=false,captured:Parameters<typeof createStudy>[0]|null=null;
  const title=el('input');title.required=true;title.placeholder='Name this study';
  const objective=el('textarea');objective.rows=3;objective.className='study-question-input';objective.required=true;objective.placeholder='For example: which segmentation model transfers best to the withheld cell lines?';
  const criteria=el('textarea');criteria.rows=2;criteria.className='study-criteria-input';criteria.required=true;criteria.placeholder='For example: mean IoU against the hand-corrected masks, by line';
  const budget=el('input');budget.type='number';budget.min='0';budget.step='0.10';budget.value='0.00';budget.required=true;
  const field=(label:string,control:HTMLElement)=>{const checkbox=control.tagName==='INPUT'&&(control as HTMLInputElement).type==='checkbox';const row=el('label',checkbox?'study-new-study-field study-choice':'study-new-study-field');if(checkbox)row.append(control,el('span','',label));else row.append(el('span','',label),control);return row;};
  const status=el('p','study-note');status.setAttribute('role','status');
  const choices=el('fieldset','study-pick-field');choices.append(el('legend','visually-hidden','What it may use'),el('p','study-note','Loading this Project’s saved inputs…'));
  const draftKey=`hooke:research-draft:${session}`;
  try{const kept=JSON.parse(localStorage.getItem(draftKey)||'null');if(kept){title.value=kept.title||'';objective.value=kept.objective||'';criteria.value=kept.criteria||'';budget.value=kept.budget||'0.00';}}catch{/* Storage may be disabled; the visible form remains usable. */}
  form.addEventListener('input',()=>{try{localStorage.setItem(draftKey,JSON.stringify({title:title.value,objective:objective.value,criteria:criteria.value,budget:budget.value}));}catch{status.textContent='Draft stays in this window; browser storage is unavailable.';}});
  const selected=new Map<HTMLInputElement,StudyInput>();
  const save=button('Continue to research plan',()=>{},'study-primary');save.type='submit';save.disabled=true;
  const cancel=button('Cancel',()=>{if(!pending)dialog.close();},'study-quiet');
  const body=el('div','study-new-study-body'),footer=el('footer','study-new-study-footer');
  // The question first, then how it will be judged; the short name third, since people name a question after writing it.
  title.placeholder='A few words';
  const dollar=el('span','study-dollar');dollar.append('$',budget);
  const allowance=el('label','study-new-study-field study-allowance');const allowanceWords=el('span','study-allowance-words');
  allowanceWords.append(el('span','','Most it may spend'));allowance.title='The most all its experiments may spend together';allowance.append(allowanceWords,dollar);
  // A header that says what a study is, then three groups: the question, what it may use, and what it may spend.
  const head=el('header','study-new-study-head');head.append(el('h2','','Start research'),el('p','','A question you want answered, the inputs it may use and what it may spend. You review each experiment before it counts.'));
  const group=(name:string,...nodes:HTMLElement[])=>{const box=el('section','study-new-study-group');if(name)box.append(el('h3','',name));box.append(...nodes);return box;};
  const pair=el('div','study-new-study-pair');pair.append(field('Short name',title),allowance);
  body.append(head,group('',field('What do you want to find out?',objective),field('How will you judge the result?',criteria),pair),
    group('What it may use',choices),status);
  footer.append(el('p','study-note study-new-study-starts','Creating a study starts nothing. You start each experiment.'),cancel,save);form.append(body,footer);
  const load=()=>{void studyInputs(session).then(inputs=>{
    if(!current())return;choices.replaceChildren(el('legend','visually-hidden','What it may use'));selected.clear();const pick=el('ul','study-pick');
    if(initial&&!inputs.some(i=>i.id===initial.reference.id&&i.revision===initial.reference.revision&&i.digest===initial.reference.digest))inputs=[{...initial.reference,title:initial.title,kind:'retained material',localInputEligible:true,managedFileEligible:false},...inputs.filter(i=>i.id!==initial.reference.id)];
    // Each input says how it will be used (or why it can't be), by name; its kind and version are on hover.
    // One row an input (concept frame): its name and kind · version, then how it will be used, or why it can't be.
    for(const input of inputs){const check=el('input');check.type='checkbox';check.disabled=!(input.localInputEligible||input.managedFileEligible||input.scientificInputEligible);selected.set(check,input);check.checked=!!initial&&input.id===initial.reference.id&&input.revision===initial.reference.revision&&input.digest===initial.reference.digest&&!check.disabled;
      const use=input.localInputEligible?'Exact saved version available to this Study’s local tools and researchers':input.managedFileEligible?'Sent to each experiment':input.scientificInputEligible?'Too large to send; copied to your private Google Cloud storage when an experiment runs':`Can’t be used yet: ${(input.limitation||'not supported by this file route').replace(/^./,c=>c.toLowerCase())}`;
      const kind=studyInputDescription(input).split(' · ')[0]||'Input';
      const row=el('li',check.disabled?'study-pick-off':'');if(check.disabled)row.setAttribute('aria-disabled','true');const name=el('label','study-pick-name'),words=el('span','study-pick-words');
      words.append(el('strong','',input.title),el('span','study-pick-kind',`${kind} · v${input.revision}`),el('span','study-pick-why',use));
      name.append(check,words);row.append(name);row.title=studyInputDescription(input);pick.append(row);}
    if(inputs.length)choices.append(pick);
    choices.append(el('p','study-note',inputs.length?'Kept at these versions; later edits don’t change the study. Each tool checks its supported inputs. Starting a model researcher shares the context it reads with your configured provider.':'No saved input documents yet. You can save a question-only Study.'));save.disabled=false;
  }).catch(error=>{if(current()){status.textContent=error instanceof Error?error.message:String(error);choices.replaceChildren(button('Retry inputs',load));}});};
  form.addEventListener('submit',event=>{event.preventDefault();
    if(pending)return;if(!current()){status.textContent='The Project changed; open New study again from it.';return;}
    captured??={session,request,title:title.value.trim(),objective:objective.value.trim(),criteria:criteria.value.trim(),budgetUsd:Number(budget.value),inputs:[...selected].filter(([check])=>check.checked).map(([,input])=>({id:input.id,revision:input.revision,digest:input.digest}))};
    pending=true;save.disabled=cancel.disabled=true;[title,objective,criteria,budget,...selected.keys()].forEach(control=>control.disabled=true);status.textContent='Saving the study draft…';
    void createStudy(captured).then(study=>{try{localStorage.removeItem(draftKey);}catch{/* Saved Study is authoritative. */}if(!current())return;dialog.close();void refreshStudies();if(created)created(study);else void openStudy(study.id);}).catch(error=>{if(current()){save.disabled=false;if(error instanceof StudyRefused){captured=null;[title,objective,criteria,budget].forEach(control=>control.disabled=false);selected.forEach((input,check)=>check.disabled=!(input.localInputEligible||input.managedFileEligible||input.scientificInputEligible));}status.textContent=error instanceof StudyRefused?`${error.message}${error.next?` (${error.next})`:''}`:String(error);}}).finally(()=>{pending=false;cancel.disabled=false;});
  });
  dialog.addEventListener('cancel',event=>{if(pending)event.preventDefault();});
  dialog.append(form);dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();objective.focus();load();
}
function clearStudyDraftMarkers():void{
  if(!currentStudyPage)return;delete currentStudyPage.dataset.draft;currentStudyPage.querySelectorAll<HTMLElement>('[data-draft]').forEach(element=>delete element.dataset.draft);
}
/** Leaving a Study with unsaved form changes asks first, in a sheet that never blocks the page (the live view keeps
 * running; `window.confirm` froze it). `then` runs if the person discards, so the action they chose completes. */
function canLeaveStudy(then?:()=>void):boolean{
  if(currentStudyPage?.dataset.pending||currentStudyPage?.querySelector('[data-pending]')){actionError('Wait for the Study action to be acknowledged before leaving.');return false;}
  if(!(currentStudyPage?.dataset.draft||currentStudyPage?.querySelector('[data-draft]')))return true;
  leaveStudyDialog(then);return false;
}
function leaveStudyDialog(then?:()=>void):void{
  if(document.querySelector('dialog.study-leave'))return;
  const dialog=el('dialog','workspace-recovery-dialog study-leave'),opener=document.activeElement as HTMLElement|null;
  dialog.setAttribute('aria-labelledby','study-leave-title');
  dialog.append(el('h2','','Discard unsaved changes to this Study form?'),el('p','','Saved inputs, evidence and results stay kept. Only the words you typed and did not save would go.'));
  const row=el('div','study-leave-actions');
  row.append(button('Keep editing',()=>dialog.close(),'study-quiet'),button('Discard changes',()=>{clearStudyDraftMarkers();dialog.close();then?.();},'study-primary'));
  dialog.append(row);dialog.firstElementChild!.id='study-leave-title';
  dialog.addEventListener('close',()=>{dialog.remove();if(opener?.isConnected)opener.focus({preventScroll:true});});
  document.body.append(dialog);dialog.showModal();
}
window.addEventListener('beforeunload',event=>{if(currentStudyPage?.dataset.draft||currentStudyPage?.querySelector('[data-draft]')||currentStudyPage?.dataset.pending||currentStudyPage?.querySelector('[data-pending]')){event.preventDefault();event.returnValue='';}});
function closeResearchStage(navigate=true):void{
  if(!canLeaveStudy(()=>closeResearchStage(navigate)))return;
  ++studyOpening;retireStudyStaging?.();retireStudyStaging=null;currentStudyPage=null;openStudyNow=null;clearInterval(studyTimer);
  if(researchStage.hidden)return;
  researchStage.hidden=true;delete document.body.dataset.researchOpen;researchTabs.virtualLeft();researchBar.querySelector('.study-view-controls')?.remove();
  assistantLayer.reset();applyLayout();tickResearchClock();
  if(navigate&&location.hash.startsWith('#research/'))bridge.navigate('#');
  renderContext();
}
// Choosing a work Tab, or leaving the researcher's address, goes back to the work Tab.
document.querySelector('.workspace-view-tabs')?.addEventListener('click',event=>{const tab=(event.target as Element).closest<HTMLElement>('[data-workspace-view-tab]');if(tab){if(!canLeaveStudy(()=>{closeResearchStage();tab.click();})){event.preventDefault();event.stopImmediatePropagation();return;}clearStudyDraftMarkers();closeResearchStage();}},true);
// (A study is not an address: it closes by its back, a work Tab or a Project switch, not by a page opening over it.)
bindResearchStageRoute(() => currentStudyPage !== null, () => closeResearchStage(false));
async function rememberSession(): Promise<void> {
  if (!snapshot.session || !initialized || tabs?.active === null) return;
  const presentation = bridge.capturePresentationSync();
  sessionUI[snapshot.session] = { presentation, section, scroll: drawerBody.scrollTop, agent: preferences.inspector, drawer: drawerOpen, dataOpen, dataMode, dataScroll:dataBody.scrollTop };
  try {localStorage.setItem(presentationKey,JSON.stringify(sessionUI));}catch{actionError('Browser storage is full. Workspace changes cannot be remembered.');}
}
function persistTabs(): void { if(tabs) sessionStorage.setItem(tabStorageKey,JSON.stringify(tabs)); renderTabs(); }
async function changeSession(id?: string, restoring = false): Promise<boolean> {
  if(!restoring && id===tabs?.active && id===snapshot.session) { surfaces.dismiss(); return true; }
  if(switching) { actionError(PROJECT_OPENING); return false; }
  if(!canLeaveStudy(()=>void changeSession(id,restoring)))return false;
  clearStudyDraftMarkers();
  const blocked=studyArtifacts().blockReason(snapshot.session)||bridge.switchBlockReason(); if(blocked){actionError(blocked);return false;}
  switching=true;
  try {
    if(!restoring) { await workspaceViews.flush(); await rememberSession(); }
    workspaceViews.pause();
    const target=await bridge.switchSession(id);
    tabs=activateTab(tabs??emptyTabs(),target); snapshot=bridge.state(); entries=[];resourceSession='';if(dataOpen)renderResults();void refreshResults();
    const saved=sessionUI[target];snapshot=bridge.state();
    if(saved) { bridge.restoreDraft(saved.presentation);const recovery={recovered:true,missing:[] as string[]}; snapshot=bridge.state(); preferences.inspector=saved.agent; drawerOpen=saved.section==='library'?false:saved.drawer; showSection(saved.section==='library'?'inputs':saved.section); drawerBody.scrollTop=saved.scroll; dataMode=saved.dataMode??'outputs';setDataOpen(saved.dataOpen??false,false);dataBody.scrollTop=saved.dataScroll??0;
      if(!recovery.recovered) actionError(`Some evidence could not be restored: ${recovery.missing.join('; ')}`); }
    else {showSection('inputs');setDataOpen(false,false);}
    await workspaceViews.load(target);
    if(!restoring)bridge.navigate('#'); persistTabs(); applyLayout(); return true;
  } catch(error){workspaceViews.resume();actionError(error); return false;} finally {
    switching=false;
    // The wait was true only while that Project opened: once it has, the line is out of date and goes.
    if(notice.textContent?.startsWith(PROJECT_OPENING)) notice.replaceChildren();
  }
}
async function closeSessionTab(id:string): Promise<void> {
  if(switching || !tabs)return;
  const blocked=studyArtifacts().blockReason(snapshot.session)||bridge.switchBlockReason(); if(blocked){actionError(blocked);return;}
  const next=closeTab(tabs,id);
  if(id===tabs.active) { await rememberSession(); if(next.active && !await changeSession(next.active)) return; }
  tabs=next; persistTabs();
}
let tabRenderKey='';
function renderTabs(): void {
  if(!tabs||tabStrip.querySelector('input'))return;
  if(tabs.active===null)renderLauncher();
  const key=JSON.stringify([tabs,snapshot.projects.map(row=>[row.id,row.name,row.label]),run?.status,run?.input.session]);
  if(key===tabRenderKey)return;tabRenderKey=key;
  tabStrip.replaceChildren();
  for(const id of tabs.order){
    const row=snapshot.projects.find(item=>item.id===id); const title=row ? sessionName(row, Date.now()/1000) : 'Untitled project';
    const holder=el('div','study-session-tab'); holder.dataset.session=id; holder.draggable=true;
    const activate=button(title,()=>void changeSession(id));activate.setAttribute('role','tab');activate.setAttribute('aria-selected',String(tabs.active===id));activate.title=`${title} · Double-click or F2 to rename. Alt+arrows to reorder.`;
    const rename=()=>{
      if(tabStrip.querySelector('input'))return;
      const input=el('input');input.value=title;input.setAttribute('aria-label','Rename project');
      input.title='Enter to save · Escape to cancel';holder.draggable=false;holder.dataset.renaming='true';
      activate.replaceWith(input);input.focus();input.select();
      let finishing=false;
      const restore=()=>{input.remove();tabRenderKey='';renderTabs();tabStrip.querySelector<HTMLElement>(`[data-session="${id}"] [role="tab"]`)?.focus();};
      const finish=async()=>{
        if(finishing||!input.isConnected)return;
        const name=input.value.trim();
        if(!name||name===title){finishing=true;restore();return;}
        finishing=true;input.readOnly=true;input.setAttribute('aria-busy','true');
        try{await bridge.renameSession(id,name);snapshot=bridge.state();restore();}
        catch(error){actionError(error);finishing=false;input.readOnly=false;input.removeAttribute('aria-busy');input.focus();}
      };
      input.addEventListener('keydown',event=>{
        if(event.key==='Enter'){event.preventDefault();void finish();}
        if(event.key==='Escape'&&!finishing){event.preventDefault();event.stopPropagation();finishing=true;restore();}
      });
      input.addEventListener('blur',()=>void finish());
    };
    const renameButton=button('',rename,'study-tab-rename');renameButton.append(icon('rename'));
    renameButton.setAttribute('aria-label',`Rename project ${title}`);renameButton.title='Rename project (F2)';
    activate.addEventListener('dblclick',rename);
    activate.addEventListener('keydown',event=>{if(event.key==='F2'){event.preventDefault();rename();}if(event.altKey&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();tabs=moveTab(tabs!,id,tabs!.order.indexOf(id)+(event.key==='ArrowLeft'?-1:1));persistTabs();tabStrip.querySelector<HTMLElement>(`[data-session="${id}"] [role="tab"]`)?.focus();}
      else if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();const index=event.key==='Home'?0:event.key==='End'?tabs!.order.length-1:(tabs!.order.indexOf(id)+(event.key==='ArrowLeft'?-1:1)+tabs!.order.length)%tabs!.order.length;tabStrip.querySelector<HTMLElement>(`[data-session="${tabs!.order[index]}"] [role="tab"]`)?.focus();}});
    const close=button('×',()=>void closeSessionTab(id),'study-tab-close');close.setAttribute('aria-label',`Close project ${title}`);
    holder.addEventListener('dragstart',event=>event.dataTransfer?.setData('text/plain',id));holder.addEventListener('dragover',event=>event.preventDefault());holder.addEventListener('drop',event=>{event.preventDefault();const from=event.dataTransfer?.getData('text/plain');if(from&&tabs){tabs=moveTab(tabs,from,tabs.order.indexOf(id));persistTabs();}});
    holder.append(activate); if(run?.input.session===id&&projectChipShown(run,tabs.active===id)){const chip=runChipWords(run);const status=el('span','study-tab-status',chip.label);status.dataset.tone=chip.tone;if(chip.title)status.title=chip.title;holder.append(status);}holder.append(renameButton);if(tabs.active===id){const folder=button('',()=>openCurrentProjectFolder(),'study-tab-rename');folder.append(tabMark('folder'));folder.setAttribute('aria-label',`Project folder for ${title}`);folder.title='Project folder';holder.append(folder);}holder.append(close);tabStrip.append(holder);
  }
  requestAnimationFrame(()=>tabStrip.querySelector<HTMLElement>('[aria-selected=true]')?.scrollIntoView({block:'nearest',inline:'nearest'}));
  document.body.dataset.studyNeutral=String(tabs.active===null);
  launcher.hidden=tabs.active!==null;
  for(const node of [document.querySelector<HTMLElement>('.studio'),drawer,rightPanel]) if(node)node.inert=tabs.active===null;
}
const sessionBrowser=el('section','study-session-browser');
const sessionSearch=el('input');sessionSearch.type='search';sessionSearch.placeholder='Search projects';sessionSearch.setAttribute('aria-label','Search projects');
const sessionList=el('div','study-session-list');
sessionSearch.addEventListener('input',renderSessionBrowser);
// Where every Project is saved, with Show in Finder (thesis question 12; sessions.ts `savedWhereWords`).
const sessionSaved=el('p','study-session-saved');const sessionSavedWords=el('span');
const sessionReveal=button('Show in Finder',()=>void bridge.request('sessions-reveal',{}).catch((error:unknown)=>{sessionSavedWords.textContent=error instanceof Error?error.message:String(error);}),'mm-control');
sessionSaved.append(sessionSavedWords,' · ',sessionReveal);sessionSaved.hidden=true;
sessionBrowser.append(sessionSearch,button('+ New project',()=>void changeSession()),sessionSaved,sessionList);surfaces.pages.set('sessions',sessionBrowser);
function renderSessionBrowser():void {
  sessionSavedWords.textContent=savedWhereWords(snapshot.projectsRoot);sessionSaved.hidden=!snapshot.projectsRoot;
  sessionList.replaceChildren();
  const query=sessionSearch.value.toLowerCase();
  for(const row of snapshot.projects.filter(row=>(sessionName(row, Date.now()/1000)).toLowerCase().includes(query))){
    const item=button('',()=>void changeSession(row.id),'study-session-row');
    if(row.thumb){const img=el('img');img.src=row.thumb;img.alt='';item.append(img);}
    const words=el('span');words.append(el('strong','',sessionName(row, Date.now()/1000)),el('small','',projectRowWords(row.counts,!!tabs?.order.includes(row.id))));item.append(words);sessionList.append(item);
  }
  if(!sessionList.children.length)sessionList.append(el('p','study-note','No projects match.'));
  appMenu.refresh();
}
/** A Project's row in Recent projects: when it was last used and its inputs, so rows that share a name differ. */
function projectNote(row:{lastActive:number;counts?:{sources?:number;sourcesIncomplete?:boolean}}):string{
  const now=Date.now()/1000,when=daysAgo(row.lastActive,now)===0?new Date(row.lastActive*1000).toLocaleTimeString([],{hour:'numeric',minute:'2-digit'}):monthDay(row.lastActive,now);
  return `${when} · ${inputCountWords(row.counts)}`;
}
function openCurrentProjectFolder():void {
    const owner = workspaceViews.tabOwner();
    if (!owner.session || !inputOwnerCurrent(owner) || switching) return;
    const row = snapshot.projects.find(item => item.id === owner.session);
    openProjectFolder({title: row ? sessionName(row, Date.now()/1000) : 'Project', session: owner.session,
      current: () => inputOwnerCurrent(owner) && !switching,
      exportActions: {
        prepare: async () => { const r = await bridge.request('project-export-prepare', {session: owner.session}); if (!r.export) throw new Error('Export preparation is unavailable.'); return r.export; },
        start: async requestKey => { const r = await bridge.request('project-export', {session: owner.session, requestKey, acknowledgeIncomplete: true}); if (!r.operation) throw new Error('Export admission was not acknowledged. Retry this request.'); return r.operation; },
        read: async operation => { const r = await bridge.request('project-export-status', {session: owner.session, operation}); if (!r.operation) throw new Error('Export status is unavailable.'); return r.operation; },
        stop: async operation => { const r = await bridge.request('project-export-stop', {session: owner.session, operation}); if (!r.operation) throw new Error('Stop was not acknowledged. Check its status.'); return r.operation; },
        reveal: async operation => { const r = await bridge.request('project-export-reveal', {session: owner.session, operation}); if (!r.ok) throw new Error(r.error ?? 'The export folder could not be revealed.'); },
      },
      restorationActions: source => ({
        prepare: async () => { const r = await bridge.request('project-copy-prepare', {session: owner.session, operation: source}); if (!r.copy) throw new Error('Restoration preparation is unavailable.'); return r.copy; },
        start: async requestKey => { const r = await bridge.request('project-copy-restore', {session: owner.session, operation: source, requestKey, acknowledgeIncomplete: true}); if (!r.operation) throw new Error('Restoration admission was not acknowledged. Retry this request.'); return workingCopyOperation(r.operation); },
        read: async operation => { const r = await bridge.request('project-copy-status', {session: owner.session, operation}); if (!r.operation) throw new Error('Restoration status is unavailable.'); return workingCopyOperation(r.operation); },
        stop: async operation => { const r = await bridge.request('project-copy-stop', {session: owner.session, operation}); if (!r.operation) throw new Error('Stop was not acknowledged. Check its status.'); return workingCopyOperation(r.operation); },
        reveal: async operation => { const r = await bridge.request('project-copy-reveal', {session: owner.session, operation}); if (!r.ok) throw new Error(r.error ?? 'The working copy could not be revealed.'); },
      }),
      recordActions: {
        list: async () => {
          const response = await fetch('/api/datasets', {cache: 'no-store'});
          if (!response.ok) throw new Error('Recorded datasets could not be read.');
          const value = await response.json();
          if (!Array.isArray(value.datasets)) throw new Error('Recorded dataset listing is unavailable.');
          return value.datasets;
        },
        review: async dataset => { const r = await bridge.request('project-record-review', {session: owner.session, dataset}); if (!r.review) throw new Error('Recording review is unavailable.'); return r.review; },
        start: async (dataset, reviewDigest, requestKey) => { const r = await bridge.request('project-record-include', {session: owner.session, dataset, reviewDigest, requestKey, includeWholeDataset: true}); if (!r.operation) throw new Error('Inclusion was not acknowledged. Retry this request.'); return r.operation; },
        read: async operation => { const r = await bridge.request('project-record-status', {session: owner.session, operation}); if (!r.operation) throw new Error('Inclusion status is unavailable.'); return r.operation; },
        stop: async operation => { const r = await bridge.request('project-record-stop', {session: owner.session, operation}); if (!r.operation) throw new Error('Stop was not acknowledged. Check its status.'); return r.operation; },
      },
      read: async () => { const reply = await bridge.request('project-storage', {session: owner.session});
        if (!reply.storage) throw new Error('Project storage details are unavailable.'); return {storage: reply.storage}; },
      reveal: async () => { const reply = await bridge.request('project-reveal', {session: owner.session});
        if (!reply.path) throw new Error('Project location is unavailable.'); return {ok: reply.ok === true, path: reply.path, error: reply.error}; },
    });
}
const appMenu = new WorkspaceAppMenu({
  opener: allSessionsButton, storageKey, scientificSessions: openScientificSessions, spending:()=>document.querySelector<HTMLButtonElement>('.spending-button')?.click(),
  openProjectFolder: () => {
    const owner = workspaceViews.tabOwner();
    if (!owner.session || !inputOwnerCurrent(owner) || switching) return;
    showOpenProject({current: () => inputOwnerCurrent(owner) && !switching,
      open: async (path, useLocation) => {
        const reply = await bridge.request('project-open-folder', {session: owner.session, path, useLocation});
        if (!reply.project) throw new Error(reply.error ?? 'The Project folder could not be opened.');
        return reply.project;
      },
      navigate: session => changeSession(session),
    });
  },
  projectFolder: openCurrentProjectFolder,
  sessions: () => [...snapshot.projects].sort((a,b)=>Number(b.id===snapshot.session)-Number(a.id===snapshot.session)).map(p => ({id:p.id,title:sessionName(p, Date.now()/1000),current:p.id===snapshot.session,open:tabs?.order.includes(p.id) ?? false,note:projectNote(p)})),
  newSession: () => void changeSession(), openSession: id => void changeSession(id), navigate: bridge.navigate,
});
function toggleSessionMenu():void { appMenu.toggle(); }
const settings=el('section','study-settings');settings.append(...Array.from(find('gear-panel').children),find('behaviour'));surfaces.pages.set('settings',settings);
find('gear').addEventListener('click',event=>{event.preventDefault();event.stopImmediatePropagation();bridge.navigate('#settings');},true);
const datasetsButton=button('Datasets',()=>bridge.navigate('#datasets'));
settings.append(developer,datasetsButton);
// The sheet's own title says Settings: the menu's first heading goes. Each row is a title and one quiet line (the
// line rides on `data-sub`, so a control whose words main.ts rewrites keeps it), with its switch or chevron at the right.
settings.querySelector('.feed-group')?.remove();
{const choices=settings.querySelector('#behaviour');if(choices)settings.querySelector('#behaviour-toggle')?.after(choices);} // its choices open under it, where its chevron points
for(const [id,sub,opens] of [['behaviour-toggle','How it works with the models and with you',true],['tab-capabilities','Everything the app can do, who does it, and what powers it',true],
 ['blind-switch','Every option answers the same click or question; you pick the best before seeing names',false],['source-menu','Plays a recording into the live feed instead of the camera',true],
 ['tab-footage','Every test recording, and adding more',true]] as const){const row=settings.querySelector<HTMLElement>(`#${id}`);if(!row)continue;row.dataset.sub=sub;if(opens)row.classList.add('study-settings-nav');}
datasetsButton.dataset.sub='What Record and Save this project captured';datasetsButton.classList.add('study-settings-nav');
const question=find('question') as HTMLInputElement;
const help = workspaceHelp({
  findTools: task => { surfaces.dismiss(); bridge.navigate('#'); showSection('tools'); setDrawer(true); toolBrowser.showReady();
    toolSearch.value = task; toolSearch.dispatchEvent(new Event('input'));
    // Nothing ready for that task: show the tools that could be set up for it instead of an empty list.
    if (task && !toolBrowser.empty.hidden) Array.from(toolBrowser.tabs.querySelectorAll('button')).find(tab => tab.textContent === 'Add tools')?.click();
    // Still nothing, or only closest guesses: the whole catalog, with the same words.
    if (task && (!toolBrowser.empty.hidden || bridge.actions.dataset.taskPartial === 'true')) { searchAllTools(task); return; }
    toolSearch.focus(); },
  askAgent: task => { surfaces.dismiss(); bridge.navigate('#'); setInspector(true); if (task) composer.set(task); question.focus(); },
  inputs: () => { surfaces.dismiss(); bridge.navigate('#'); showSection('inputs'); setDrawer(true); },
  data: () => { surfaces.dismiss(); bridge.navigate('#'); dataMode = 'outputs'; setDataOpen(true); },
  activity: () => showActivity(true),
  models: () => bridge.navigate('#models'),
});
const walkthrough=settings.querySelector<HTMLElement>('#example-walkthrough')!;walkthrough.addEventListener('click',()=>bridge.navigate('#'),{capture:true});
help.append(walkthrough,button('Keyboard shortcuts',()=>bridge.navigate('#shortcuts'),'study-guide-action'));
surfaces.pages.set('help',help);
const shortcuts=el('section','study-guide');shortcuts.append(el('p','study-guide-intro','Shortcuts act on the control or window that has keyboard focus. The ⌘ Tab shortcuts work anywhere in the Mac app (its Tab menu lists them); a web browser keeps most of them for its own tabs, so use Tabs… there.'));
const shortcutList=el('dl','study-shortcut-list');
for(const [keys,description] of [
  ['Alt + A','Open Assistant'],['F2 or double-click a project in the top bar','Rename project'],['Enter / Escape','Save / cancel a project rename'],
  ['← / → on a project in the top bar','Focus the previous / next project'],['Home / End on a project in the top bar','Focus the first / last project'],
  ['Alt + ← / → on a project in the top bar','Reorder open projects'],['Arrow keys on an artifact header','Move the artifact'],
  ['Shift + Arrow keys on an artifact header','Move the artifact in larger steps'],['← / → on a panel divider','Resize the panel'],
  ['⌘T','New Tab'],['⌘W','Close this Tab (Main stays open: it holds the specimen)'],['⌘⇧T','Reopen the last closed Tab, where it was'],
  ['⌘1 – ⌘8 / ⌘9','Go to that Tab / the last Tab'],['⌘⇧[ / ⌘⇧]','Previous / next Tab'],
  ['⌄ All Tabs → Manage Tabs… → Duplicate, or Tab → Duplicate Tab in the Mac app','A copy of a Tab beside it, with the same input and results'],
  ['F2 or double-click a workspace tab','Rename the tab'],['Alt + ← / → on a workspace tab','Reorder tabs'],
  ['Arrow keys on a window resize grip','Resize the specimen or artifact'],['Add to Tab… in a window header','Show a window in another Tab too, or move it there; Data is unchanged'],
  ['Drag a window header onto a Tab (Alt: also show)','Move it there; onto + for a new Tab with it'],['Drag a Data card onto a Tab','Open that result there'],
  ['Escape','Close the popup, or the focused workspace panel'],['Tab / Shift + Tab','Move between controls'],
])shortcutList.append(el('dt','',keys),el('dd','',description));
shortcuts.append(shortcutList);surfaces.pages.set('shortcuts',shortcuts);
const utilities=el('div','study-rail-bottom');utilities.setAttribute('role','group');utilities.setAttribute('aria-label','Help and settings');
const utilityButtons=new Map<string,HTMLElement>();
for(const [id,label] of [['help','Help'],['shortcuts','Shortcuts']] as const){
  const b=button('',()=>bridge.navigate(`#${id}`));b.append(icon(id),el('span','',label));b.setAttribute('aria-label',label);b.setAttribute('aria-haspopup','dialog');utilities.append(b);utilityButtons.set(id,b);
}
const settingsButton=find('gear');settingsButton.replaceChildren(icon('settings'),el('span','','Settings'));
settingsButton.title='Settings';settingsButton.setAttribute('aria-haspopup','dialog');settingsButton.removeAttribute('aria-controls');utilities.append(settingsButton);utilityButtons.set('settings',settingsButton);rail.append(utilities);
// What restarting Hooke to update, or quitting it, would stop or lose, read from each kind of work's own source
// (restart-readiness.ts): the Mac app's sheet names it. A source that can't be read makes the whole answer unreadable.
const readJSON=async(url:string):Promise<unknown>=>{const response=await fetch(url,{cache:'no-store'});if(!response.ok)throw new Error(`${url} could not be read.`);return response.json();};
agentConnections.mountSettings(settings); // Settings → Connected agents (ADR 0026)
mountAppUpdates(utilities,settings,async()=>{
  if(!bridge.state().connected)return {blockers:[DISCONNECTED],running:[]};
  const work=bridge.restartWork(),current=bridge.state(),owner=workspaceViews.tabOwner().session;
  const projectName=(sid:string)=>{const row=current.projects.find(project=>project.id===sid);return row?(row.label||row.name||undefined):undefined;};
  return readReadiness({blockers:()=>[...workspaceViews.updateRestartBlockers(),...work.blockers],running:[
    ()=>runLines(liveHandRuns()),
    ()=>work.running,
    ()=>unsentLines([...document.querySelectorAll<HTMLTextAreaElement>('textarea.mm-composer-field')]),
    async()=>researcherLines(await readJSON('/api/research'),current.session,projectName),
    async()=>studyLines(owner?await listStudies(owner):null),
    async()=>toolSessionLines(await openToolSessionTitles()),
  ]});
},settingsButton);
window.addEventListener('workspace-study-surface',event=>{const page=(event as CustomEvent).detail.page;utilityButtons.forEach((b,id)=>{b.setAttribute('aria-current',page===id?'page':'false');b.setAttribute('aria-expanded',String(page===id));});renderRailCurrent();});

// Camera, Record (with its Data light) and Assistant sit on the Stage bar's right, on every Tab (workspace-feel plan; the
// Tab row holds only Tabs). Same buttons, same behaviour and scope: they act on the Project, not on the Tab above them.
workspaceViews.stageSlots().right.append(find('source'),cameraDisclaimer,find('record-group'),el('span','study-stage-divider'),agentButton);
document.body.append(find('phone-panel'));
/** Writes a custom property on `node` itself (never on body, where a changed value re-styles the whole page), only
 * when it changes. */
const placeVar=(node:HTMLElement,name:string,value:string)=>{if(node.style.getPropertyValue(name)!==value)node.style.setProperty(name,value);};
// The camera menu opens under the camera control wherever that is, kept on screen: placed when it opens, when the
// Stage changes size (the Assistant opening moves the control) or the window does, and after a Tab switch.
function placeCameraMenu():void{
  const menu=find('phone-panel');if(menu.hidden||holdUntilLanded(placeCameraMenu))return;
  const anchor=find('source').getBoundingClientRect();if(!anchor.width)return;
  placeVar(menu,'--camera-menu-top',`${Math.round(anchor.bottom+6)}px`);
  const width=menu.getBoundingClientRect().width||440;
  placeVar(menu,'--camera-menu-right',`${Math.round(Math.max(10,Math.min(innerWidth-anchor.right,innerWidth-width-10)))}px`);
}
new MutationObserver(placeCameraMenu).observe(find('phone-panel'),{attributes:true,attributeFilter:['hidden']});
// The walkthrough card sits on the Stage, above its bottom row of status chips, never over the Assistant's box. Its
// place is written on the card itself.
function placeGuide():void{
  const stage=find('stage').getBoundingClientRect();if(!stage.width)return;
  const guide=find('example-guide');
  placeVar(guide,'--guide-right',`${Math.max(12,Math.round(innerWidth-stage.right+16))}px`);
  placeVar(guide,'--guide-bottom',`${Math.max(48,Math.round(innerHeight-stage.bottom+64))}px`);
}
new ResizeObserver(()=>{placeGuide();placeCameraMenu();}).observe(find('stage'));window.addEventListener('resize',placeGuide);
window.addEventListener('resize',placeCameraMenu);window.addEventListener('workspace-tab-context',placeCameraMenu);
find('source').title='Specimen';
allSessionsButton.setAttribute('aria-haspopup','dialog');
libraryButton.setAttribute('aria-haspopup','dialog');
window.addEventListener('workspace-study-surface',event=>{const page=(event as CustomEvent).detail.page;workflowsButton.setAttribute('aria-current',page==='workflows'?'page':'false');libraryButton.setAttribute('aria-current',page==='assets'?'page':'false');modelsButton.setAttribute('aria-current',page==='models'?'page':'false');});
// Library's Open in new Project: begin a Project, then open the recording or picture in it (it becomes its first input).
window.addEventListener('hooke-open-in-new-project',event=>{const asset=(event as CustomEvent<{asset:AssetMeta}>).detail?.asset;if(!asset)return;void (async()=>{if(!(await changeSession()))return;surfaces.dismiss();await openSource(asset);})();});
// A setup sheet opened from Tools or Activity (#models/<id>/setup) closes back to the Project; opened inside Models, it closes to Models.
window.addEventListener('hooke-setup-sheet-closed',()=>{if(sheetAlone(location.hash))surfaces.dismiss();});
// With no Project open: the most recent one first, a few more beside it, and a new one (Will, 2026-10-23: the bare
// two-button screen needed the concept's Your Projects, at the app's scale). Opening a card opens that Project.
const launcher=el('section','study-session-launcher');launcher.hidden=true;launcher.setAttribute('aria-label','Your Projects');document.body.append(launcher);
/** A first run: nothing stored for Tabs in this browser and no Project with an input or a result yet. The launcher is
 * then the first-launch overview (#440), and the server's fresh Project is not opened until the person chooses. */
function firstRun(state:{projects:{counts?:{sources?:number;made:number}}[]}):boolean{
  // Needs the Project list itself: before it has arrived, "every" of nothing would read as a first run.
  // Needs each Project's counts too: a list without them yet says nothing about a first run.
  return !sessionStorage.getItem(tabStorageKey)&&state.projects.length>0&&state.projects.every(row=>!!row.counts&&!(row.counts.sources||row.counts.made));
}
/** The first screen (#440's overview): what Hooke is for and the three ways in, plus the guided example. Every way begins
 * the fresh Project the server already holds; nothing runs until a choice is made. Under the ways, the small-models
 * offer (starter-models.ts): not a modal, so any way in can start at once; asked once, its answer kept on the server. */
function firstLaunch():HTMLElement{
  const page=el('div','first-launch');
  const head=el('header','first-launch-head');head.append(el('h1','','Welcome to Hooke'),el('p','','Look at a specimen, run models on it, and keep every result with its Project. Start with one of these.'));
  const ways=el('div','first-launch-ways');
  const way=(title:string,what:string,go:()=>void,primary=false)=>{const b=button('',go,`first-launch-way${primary?' primary':''}`);b.append(el('strong','',title),el('span','',what));return b;};
  const begin=async(then:()=>void|Promise<void>)=>{const session=snapshot.projects[0]?.id;if(!(await changeSession(session)))return;await then();};
  ways.append(
    way('Open a picture or video','From this Mac. It becomes the first input of your first Project.',()=>void begin(()=>pickInputFiles(workspaceViews.tabOwner())),true),
    way('Connect a camera','An iPhone, a Blackmagic or another camera on this Mac; the live view is the specimen.',()=>void begin(()=>openLiveCamera())),
    way('Try the guided example','A real microscope picture and a short walkthrough. No account or key needed.',()=>bridge.example()));
  const quiet=el('p','first-launch-quiet');quiet.append('Or ',button('start with an empty Project',()=>void begin(()=>{}),'draft-link'),'. Your work lives on this Mac; the Assistant and cloud models are set up in Models when you want them.');
  page.append(head,ways,starterOffer('welcome'),quiet);return page;
}
let launcherKey='';
function renderLauncher():void {
  const key=JSON.stringify(snapshot.projects);if(key===launcherKey)return;launcherKey=key;
  const now=Date.now()/1000, rows=sortSessions(snapshot.projects).map(row=>rowView(row,'',now));
  if(firstRun(snapshot)){launcher.replaceChildren(firstLaunch());return;}
  const head=el('header','launcher-head'),words=el('div');
  words.append(el('h1','',rows.length?'Pick up where you left off':'Start your first Project'),
    el('p','',rows.length?'Each Project keeps its inputs, results, Tabs and conversation. Open one to continue.':'A Project holds the pictures and recordings you work on, what you make from them, and your conversation.'));
  const actions=el('div','launcher-actions');
  if(rows.length)actions.append(button(`All Projects (${rows.length})`,()=>bridge.navigate('#sessions'),'launcher-btn'));
  actions.append(button('New Project',()=>void changeSession(),rows.length?'launcher-btn':'launcher-btn primary'));
  head.append(words,actions);
  const picture=(row:SessionRowView,className:string)=>{const frame=el('span',className);if(row.thumb){const img=el('img');img.src=row.thumb;img.alt='';img.loading='lazy';frame.append(img);}else{const empty=el('span','launcher-empty-pic'),mark=el('img');mark.src=hookeMark;mark.alt='';empty.append(mark,el('span','','No pictures yet'));frame.append(empty);}return frame;};
  const meta=(row:SessionRowView)=>[`Last used ${row.lastUsed}`,row.counts].filter(Boolean).join(' · ');
  const working=(row:SessionRowView)=>row.working?el('span','launcher-working',`${row.working} researcher${row.working===1?'':'s'} working`):null;
  const parts:HTMLElement[]=[head];
  const [first,...rest]=rows;
  if(first){
    const hero=button('',()=>void changeSession(first.id),'launcher-hero');hero.title=first.title;hero.setAttribute('aria-label',`Open ${first.name}. ${meta(first)}`);
    const text=el('span','launcher-hero-text');text.append(el('span','launcher-kicker','Most recent'),el('strong','launcher-hero-name',first.name),el('span','launcher-meta',meta(first)));
    const w=working(first);if(w)text.append(w);text.append(el('span','launcher-open','Open Project'));
    hero.append(picture(first,'launcher-hero-pic'),text);parts.push(hero);
  }
  if(rest.length){
    const section=el('section','launcher-recent'),grid=el('div','launcher-grid');
    section.append(el('h2','',`Recent`));
    for(const row of rest.slice(0,7)){
      const card=button('',()=>void changeSession(row.id),'launcher-card');card.title=row.title;card.setAttribute('aria-label',`Open ${row.name}. ${meta(row)}`);
      card.append(picture(row,'launcher-card-pic'),el('strong','launcher-card-name',row.name),el('span','launcher-meta',meta(row)));
      const w=working(row);if(w)card.append(w);grid.append(card);
    }
    const fresh=button('',()=>void changeSession(),'launcher-card launcher-new');
    const plus=el('span','launcher-card-pic launcher-new-pic','+');plus.setAttribute('aria-hidden','true');fresh.append(plus,el('strong','launcher-card-name','New Project'));grid.append(fresh);
    section.append(grid);
    if(rest.length>7)section.append(button(`See all ${rows.length} Projects`,()=>bridge.navigate('#sessions'),'launcher-more'));
    parts.push(section);
  }
  launcher.replaceChildren(...parts);
}
// Models' Use it here: back to the workspace, on the run page of the first task the model serves, with it chosen and
// the current specimen as its input (actions-panel.ts useModel). Nothing runs. A model that serves no task here (one the
// Assistant and researchers use) opens Tools By model at its row instead.
window.addEventListener('hooke-use-model',event=>{
  const {model,task}=(event as CustomEvent<{model:string;task?:string}>).detail??{model:''};if(!model)return;
  surfaces.dismiss();bridge.navigate('#');showSection('tools');setDrawer(true);
  if(bridge.useModel(model,task))return;
  toolBrowser.tabs.querySelector<HTMLButtonElement>('[data-mode="model"]')?.click();
  requestAnimationFrame(()=>{const entry=bridge.actions.querySelector<HTMLElement>(`[data-entry="${CSS.escape(model)}"]`);if(!entry)return;
    entry.scrollIntoView({block:'center'});entry.querySelector<HTMLElement>('button')?.focus({preventScroll:true});});
});
function showSection(next: StudySection): void {
  if(next==='results'){dataMode='outputs';setDataOpen(true);return;}
  if(next==='library'){bridge.navigate('#assets');return;}
  if(next==='projects'){bridge.navigate('#sessions');return;}
  if(next==='history'){dataMode='history';setDataOpen(true);return;}
  section = next; preferences.section = next; applyLayout();
  sessionSections.hidden=true;
  sessionSections.querySelectorAll<HTMLButtonElement>('button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.section===section)));
  const title = { inputs: 'Inputs', projects: 'All projects', library: 'Library', tools: 'Tools', results: 'Project', history: 'Project', research: 'Researchers' }[section];
  drawerTitle.textContent = title; closeLeft.setAttribute('aria-label', `Close ${title}`);
  // The header's one action: Add model or tool in Tools, + New in Researchers, ⋯ (the ways in) in Inputs.
  drawerAction.replaceChildren(...(section === 'tools' ? [addTool] : section === 'research' ? [researchNew] : section === 'inputs' ? [inputsMore] : []));

  drawerBody.replaceChildren();
  if (section === 'tools') drawerBody.append(inspector);
  if (section === 'research') {drawerBody.append(researcherBrowser);renderBoard();bridge.refreshResearch();void refreshStudies();}
  if (section === 'inputs') renderInputs();
}
function importNativeBundle(): void {
  openScientificBundleImport({saved:()=>{void refreshResults();},open:async target=>{
    if(target.session!==snapshot.session && !await changeSession(target.session))throw new Error('The bundle’s original Project is not available here.');
    const opened=await openStudyScientific({title:'Imported scientific work',resource:{id:target.id,day:target.session,version:target.revision,picture:null},zoom:1,pan:[.5,.5]});
    if(!opened)throw new Error('The accepted native revision could not be opened.');
    setDataOpen(true,false);
  }});
}
function importScientificSource(): void {
  openScientificFileImport({session:snapshot.session,title:snapshot.title,
    saved:()=>{void refreshResults();},open:async source=>{
      if(source.session!==snapshot.session && !await changeSession(source.session))throw new Error('Return to the source’s Project before opening it.');
      const presentation = drawerPresentation;
      const opened = await openStudyScientific({title:source.title,resource:{id:source.id,day:source.session,version:source.revision,picture:null},zoom:1,pan:[.5,.5]});
      if (!opened) throw new Error('The saved source could not be opened. Retry opening it; your original file is kept.');
      return () => {
        if (snapshot.session === source.session && drawerPresentation === presentation && section === 'inputs' && drawerOpen) setDrawer(false);
      };
    }});
}
/** Inputs (the drawer): the live camera and this Project's pictures and recordings as rows of the shared anatomy
 * (workspace-feel step 4), each opening its preview; ⋯ holds Open in Tab and its Results. The ways to bring an input in
 * are the drawer header's ⋯ (`inputsMore`), as Data's header has them. */
let inputQuery = '', inputQueryOwner = '';
let refreshInputRows: (() => void) | null = null;
function renderInputs(): void {
  const owner=workspaceViews.tabOwner();
  if (inputQueryOwner !== owner.session) { inputQuery = ''; inputQueryOwner = owner.session; }
  const mediaRows: {node: HTMLElement; text: string; asset: AssetMeta}[] = [];
  const list=el('div','study-specimens');
  // The live camera: green only while it is truly live (refreshLiveCameraStatus); its words say which camera.
  const liveLead=el('span','mm-row-icon');liveLead.append(el('span','study-live-mark'));
  liveCameraStatus=el('span','study-membership');
  const live=button('',()=>void openLiveCamera(owner),'mm-row study-live-card');
  const liveWords=el('span','mm-row-words');liveWords.append(el('span','mm-row-title','Live camera'),liveCameraStatus);liveCameraStatus.classList.add('mm-row-sub');
  live.append(liveLead,liveWords);live.title='Show the live camera in Main; each input opens in its own Tab';
  live.classList.toggle('active',snapshot.onStage==='live');list.append(live);
  for (const asset of snapshot.sources) {
    const thumb=el('span','mm-row-thumb');const image=el('img');image.src=asset.thumb||`/api/library/${encodeURIComponent(asset.id)}/file`;image.alt='';image.loading='lazy';thumb.append(image);
    const kind=asset.kind==='still'?'Image':'Recording',where=asset.id===snapshot.onStage?'On the Stage':'In this Project';
    const {wrap,row}=dataRow(thumb,asset.title,`${kind} · ${where}`,`${asset.title}\nPreview; Open in Tab gives it a Tab of its own`,()=>showPreview(asset,owner),
      ()=>[{label:'Open in Tab',title:'Open it in a Tab of its own',act:()=>void openSource(asset,owner)},{label:'Results',title:'What was made from it, in Data',act:()=>{sourceFilter=asset.id;showSection('results');}}],`More for ${asset.title}`);
    wrap.classList.add('study-input');row.classList.toggle('active',asset.id===snapshot.onStage);if(asset.id===snapshot.onStage)row.setAttribute('aria-current','true');
    list.append(wrap); mediaRows.push({node: wrap, text: `${asset.title} ${kind}`.toLocaleLowerCase(),asset});
  }
  const material = resourceSession === snapshot.session ? projectInputs(entries, inputQuery) : [];
  const controls = el('div', 'project-input-controls');
  const search = el('input', 'mm-control'); search.type = 'search'; search.placeholder = 'Search Project material';
  search.setAttribute('aria-label', 'Search Project material'); search.value = inputQuery;
  const materialList = el('div', 'project-input-material');
  const drawMaterial = () => {
    if (!inputOwnerCurrent(owner)) return;
    materialList.replaceChildren();
    const empty = drawerBody.querySelector<HTMLElement>('.project-input-empty');
    if (empty) empty.hidden = resultsFailed() || resourceSession !== snapshot.session || !!inputQuery || !!snapshot.sources.length || !!projectInputs(entries).length;
    if (resultsFailed() || resourceSession !== snapshot.session || resourceReads.slow) {
      const notice = el('p', resultsFailed() ? 'study-error' : 'study-muted', resultsFailed() ? (resourceSession === snapshot.session ? 'Could not refresh material. Showing the last saved list.' : 'Project material could not be loaded.') : resourceReads.slow?'Loading is taking longer than usual.': 'Loading Project material…');
      notice.setAttribute('role', 'status'); materialList.append(notice);
      if (resultsFailed() || resourceReads.slow) materialList.append(button('Retry', () => { if (inputOwnerCurrent(owner)) retryProjectReads(); }, 'mm-control'));
    }
    const needle = inputQuery.trim().toLocaleLowerCase();
    let matchingMedia = 0;
    const retained=resourceSession===snapshot.session?projectInputs(entries):[];
    for (const row of mediaRows) { row.node.hidden = retained.some(entry=>isRetainedMaterial(entry,row.asset))||!!needle && !row.text.includes(needle); if (!row.node.hidden) matchingMedia++; }
    const selected = resourceSession === snapshot.session ? projectInputs(entries, inputQuery, snapshot.sources) : [];
    const grouped = [...selected].sort((a, b) => (a.material?.organization?.role || 'Material').localeCompare(b.material?.organization?.role || 'Material'));
    let previousGroup = '';
    for (const entry of grouped) {
      const group = entry.material?.organization?.role || 'Material';
      if (group !== previousGroup) { materialList.append(el('h3', 'project-input-group', group)); previousGroup = group; }
      const actions = entryActions(entry);
      const source=retainedSource(entry,snapshot.sources);
      const open = () => { if (!inputOwnerCurrent(owner)) return;
        if (entry.material?.kind === 'assignment') void openStudy(entry.id);
        else if(source)showPreview(source,owner);
        else actions.open(); };
      const lead=el('span', source?.thumb?'mm-row-thumb':'mm-row-icon',entry.material?.kind==='assignment'?'◇':source?.thumb?'':'▤');
      if(source?.thumb){const picture=el('img');picture.src=source.thumb;picture.alt='';picture.loading='lazy';lead.append(picture);}
      const title=source?.title??entry.title;
      const onStage=!!source&&snapshot.onStage===source.id&&sameMediaReference(source.retained,bridge.capturePresentationSync().retained);
      const facts=[materialFacts(entry),onStage?'On the Stage':''].filter(Boolean).join(' · ');
      const {wrap,row} = dataRow(lead,
        title, facts, `${title}\n${facts}\nPreview retained material`, open,
        () => entry.material?.kind === 'assignment' ? [{label: 'Open Study', act: open}] :
          source?[{label:'Open image or recording in Tab',act:()=>void openSource(source,owner)},...actions.menu()]:actions.menu(), `More for ${title}`);
      row.classList.toggle('active',onStage);if(onStage)row.setAttribute('aria-current','true');
      materialList.append(wrap);
    }
    if (resourceSession === snapshot.session && inputQuery && !selected.length && !matchingMedia) materialList.append(el('p', 'study-muted', 'No matching material.'));
  };
  refreshInputRows = drawMaterial;
  search.addEventListener('input', () => { inputQuery = search.value; drawMaterial(); });
  controls.append(button('Add material', () => { if (inputOwnerCurrent(owner)) importScientificSource(); }, 'study-primary'), search);
  drawerBody.append(controls, materialList, list); drawMaterial();
  if (!snapshot.sources.length && !material.length && !inputQuery) {
    const empty=el('div','mm-drawer-empty project-input-empty');empty.append(el('p','','Add files, datasets, images or recordings to this Project. They stay here with their research context.'));
    empty.append(button('Browse Project data',()=>{if(inputOwnerCurrent(owner))showSection('results');},'mm-control'));
    const example = button('Try the guided example', () => { if (!busy()) void bridge.example().catch(actionError); },'mm-control'); example.disabled = busy();
    empty.append(example);empty.hidden = resultsFailed() || resourceSession !== snapshot.session;drawerBody.append(empty);
  }
  refreshLiveCameraStatus();
}
function refreshLiveCameraStatus(): void {
  if (!liveCameraStatus?.isConnected) return;
  const status = bridge.cameraStatus(), none = status === 'No camera selected';
  // Green only while the camera is truly live (the Tab row's specimen control says so), never for "live" with no picture.
  const card = liveCameraStatus.closest<HTMLElement>('.study-live-card');
  if (card) card.dataset.camera = String(document.getElementById('source')?.dataset.state === 'live');
  const text = snapshot.onStage === 'live' && !none ? `${status} · On the Stage` : none ? 'No camera chosen' : status;
  if (liveCameraStatus.textContent !== text) liveCameraStatus.textContent = text;
}
function showPreview(asset: AssetMeta,owner=workspaceViews.tabOwner()): void {
  if(!inputOwnerCurrent(owner)){actionError(new Error('The Project changed. Reopen the input in its intended Project.'));return;}
  activePreview = asset;
  preview.replaceChildren();
  const header = el('header', 'study-panel-header'); header.append(el('h2', '', asset.title), button('×', () => surfaces.dismiss(), 'study-close'));
  const media = asset.kind === 'recording' ? el('video') : el('img');
  const url=asset.retained?asset.thumb:`/api/library/${encodeURIComponent(asset.id)}/file`;
  if(url)media.src=url;
  if (media instanceof HTMLVideoElement) media.controls = true; else media.alt = asset.title;
  const add = button('Open in Tab', () => { surfaces.dismiss(); void openSource(asset,owner); }, 'study-primary');
  add.disabled = busy();
  preview.append(header, el('p', 'study-note', 'Preview only. Your live camera and selected tool input stay unchanged.'), add, media,
    el('p', '', snapshot.sources.some(source => source.id === asset.id) ? 'Saved Data in this Project.' : 'Opening a work Tab adds this Data to the current Project.'),
    button('Find results from this source', () => { surfaces.dismiss(); sourceFilter = asset.id; showSection('results'); }));
  surfaces.detail(asset.title, preview);
}
async function openSource(asset: AssetMeta,owner=workspaceViews.tabOwner()): Promise<void> {
  if(!inputOwnerCurrent(owner)){actionError(new Error('The Project changed. Choose the input again in its intended Project.'));return;}
  if (busy()) { runStatus.textContent = 'This run keeps its captured input. You can preview other images while it finishes.'; return; }
  bridge.navigate('#');
  try {
    const admitted=await bridge.prepareInput(asset,owner.session);
    if(!admitted.ready||!inputOwnerCurrent(owner))return;
    await workspaceViews.openInput({source:asset.id,position:0,...(admitted.retained?{retained:admitted.retained}:{})},asset.title,owner);
  } catch (error) { actionError(error); }
}
function inputOwnerCurrent(owner:ReturnType<WorkspaceViews['tabOwner']>):boolean {
 return workspaceViews.ownsTabChoice(owner)&&bridge.state().session===owner.session;
}
/** A file chooser belongs to its opening Project; an away-and-back switch does not renew it. */
function pickInputFiles(owner:ReturnType<WorkspaceViews['tabOwner']>):void {
 if(!inputOwnerCurrent(owner)){actionError(new Error('The Project changed. Choose your files again in the intended Project.'));return;}
 if(busy()){actionError(new Error('This run keeps its captured input. Wait before importing another input.'));return;}
 const upload=el('input');upload.type='file';upload.multiple=true;upload.accept='image/*,video/*,.tif,.tiff';
 upload.addEventListener('change',()=>{
  if(!upload.files?.length)return;
  if(busy()){actionError(new Error('This run keeps its captured input. Wait before importing another input.'));return;}
  void bridge.upload(Array.from(upload.files),owner.session,()=>inputOwnerCurrent(owner)).catch(actionError);
 });upload.click();
}
// The Specimen menu's ways to change the specimen: this Project's inputs (each opens in its own Tab, as from Inputs;
// Main keeps its live camera), a new picture or video, or the Library.
bridge.setSpecimenChoices(()=>{
  const owner=workspaceViews.tabOwner();
  const choices=snapshot.sources.slice(0,6).map(asset=>({id:`source:${asset.id}`,owner,label:asset.title,detail:sourceMeta(asset,Date.now()),current:asset.id===snapshot.onStage,act:()=>void openSource(asset,owner)}));
  return [...choices,
    {id:'open-file',owner,label:'Open a picture or video…',detail:'From this Mac; it joins this Project',act:()=>pickInputFiles(owner)},
    {id:'library',owner,label:'From your Library…',detail:'Everything you have recorded or imported',act:()=>showSection('library')}];
});
async function openLiveCamera(owner=workspaceViews.tabOwner()):Promise<void>{
 if(!inputOwnerCurrent(owner)){actionError(new Error('The Project changed. Choose the live input again.'));return;}
 bridge.navigate('#');
 try{await workspaceViews.openInput({source:'live',position:0},'Live camera',owner);}catch(error){actionError(error);}
}
async function refreshBackgroundRun(): Promise<void> {
  const owner = run; const job = owner?.backgroundJob;
  if (!owner || !job || ['completed', 'failed', 'stopped'].includes(owner.status)) return;
  try {
    const response = await fetch(`/api/resources?kind=job&day=${encodeURIComponent(job.session)}`, { cache: 'no-store' });
    if (!response.ok) return;
    const data = await response.json() as { entries: StudyResource[] };
    if (run === owner && reconcileBackgroundRun(owner, data.entries, job.session)) { renderContext(); renderTabs(); }
  } catch { /* Retained state is authoritative; a failed poll never marks a job complete. */ }
}
const resourceReads = new ProjectRead(()=>{renderBoard();if(drawerOpen&&section==='inputs')refreshInputRows?.();});
function retryProjectReads():void { resourceReads.cancel();studyReads.cancel();void refreshResults(); }
function refreshResults():Promise<void>{
  void refreshStudies();
  return resourceReads.run(snapshot.session,(signal,ownsRead)=>loadResults(signal,ownsRead));
}
async function loadResults(signal:AbortSignal,ownsRead:()=>boolean): Promise<void> {
  // Project research is visible on return, independent of which drawer is open.
  void refreshBackgroundRun();
  const session = snapshot.session; if (!session) return;
  const generation = ++refreshGeneration;
  renderBoard();
  try {
    // The 10 s poll and the server's events send back the revision Data last read (ticket 66, S7): an unchanged index
    // answers in a few bytes, and nothing is redrawn.
    const since = resourceSession === session && resourceRevision ? `&since=${encodeURIComponent(resourceRevision)}` : '';
    const response = await fetch(`/api/resources?day=${encodeURIComponent(session)}${since}`, { cache: 'no-store', signal });
    if (!response.ok) throw new Error('Retained results could not be read.');
    const data = await response.json() as { entries?: DataEntry[]; unreadable?: string[]; revision?: string; unchanged?: boolean };
    if (!ownsRead() || generation !== refreshGeneration || snapshot.session !== session) return;
    // Unchanged: nothing is drawn, unless a failed read's notice stands in Data's place.
    if (data.unchanged === true && resourceSession === session) { if (resultsFailed()) { resultsFailedSession = ''; renderResults(); renderBoard(); if (drawerOpen && section === 'inputs') refreshInputRows?.(); } if (readerPending || tabReaderPending) readerEntriesChanged(); return; }
    // A researcher's report is dated by its start on the server; here it rises when it finishes (the server fix is a
    // ticket). Its lessons, pictures, tables and sources are the server's own entries now (S4), no longer made up here.
    const next = (data.entries ?? []).map(entry => {const base={...entry,day:entry.day??session};if(entry.kind!=='research')return base;
      const row=bridge.researchers().find(item=>item.rid===entry.id);return row?.finished?{...base,at:row.finished}:base;});
    // An index without a revision (before ticket 66) is compared whole, as before.
    const changed = resourceSession !== session || (data.revision ? data.revision !== resourceRevision : JSON.stringify(entries) !== JSON.stringify(next));
    entries = next; resourceSession = session; resourceRevision = data.revision ?? '';
    if (!changed && !resultsFailed()) { if (readerPending || tabReaderPending) readerEntriesChanged(); return; }
    resultsFailedSession = '';
    madeMemo.clear();
    if (dataOpen) renderResults();
    if (drawerOpen && section === 'inputs') refreshInputRows?.();
    renderRailBadges(); renderBoard();
    readerEntriesChanged();
  } catch (error) {
    if (ownsRead() && generation === refreshGeneration && snapshot.session === session) {
      resultsFailedSession = session;
      renderBoard();
      if (dataOpen) {
        if(resourceSession===session)renderResults();
        else dataView.show(el('p', 'study-error', String(error)), button('Retry results', () => retryProjectReads()));
      }
      if (drawerOpen && section === 'inputs') refreshInputRows?.();
    }
  }
}
/** One row of Data (the mock's shared row anatomy): its picture or kind, its title, one quiet line, the New dot and ⋯. */
function dataRow(lead:HTMLElement,title:string,sub:string,tip:string,open:()=>void,menu:()=>MenuItem[],menuLabel:string,name=title):{wrap:HTMLElement;row:HTMLButtonElement}{
  const wrap=el('div','mm-row-wrap'),row=button('',open,'mm-row');row.title=tip;row.setAttribute('aria-label',`Open ${name}`);
  const words=el('span','mm-row-words');words.append(el('span','mm-row-title',title),el('span','mm-row-sub',sub));
  const side=el('span','mm-row-side');const dot=el('span','mm-new-dot');dot.setAttribute('aria-hidden','true');dot.title='New since you last looked';side.append(dot);
  row.append(lead,words,side);wrap.append(row,moreButton(menuLabel,menu));return {wrap,row};
}
/** Data's rows (data-roles.ts), read again only when the index or the Project's inputs changed. */
const NO_ENTRIES:DataEntry[]=[];
let rowsMemo:{entries:DataEntry[];sources:AssetMeta[];rows:DataRow[]}|null=null;
function dataRowsNow():DataRow[]{
  const loaded=resourceSession===snapshot.session?entries:NO_ENTRIES;
  if(rowsMemo?.entries!==loaded||rowsMemo.sources!==snapshot.sources)rowsMemo={entries:loaded,sources:snapshot.sources,rows:dataRows(loaded,snapshot.sources)};
  return rowsMemo.rows;
}
/** What a researcher made, for its row in Researchers (D4), kept until the index changes. */
const madeMemo=new Map<string,string>();
function madeBy(row:ResearchSession):{words:string;show:()=>void}|null{
  if(resourceSession!==snapshot.session)return null;
  let words=madeMemo.get(row.rid);if(words===undefined){words=madeWords(entries,{rid:row.rid,name:row.name});madeMemo.set(row.rid,words);}
  return words?{words,show:()=>showMadeBy(researcherKey(row.name))}:null;
}
/** Data with only what one maker made: a researcher's "Made …" in Researchers leads here. */
function showMadeBy(key:MakerKey):void{
  makerFilter=key;sourceFilter=null;dataSearch.value='';dataMode='outputs';
  press(()=>setDataOpen(true));
}
/** What is on the Tab you are on (ticket 66, "On this Tab"): the things its saved layout holds, the windows open on it
 * now whether saved yet or not, the outlines or motion drawn on its specimen, and its input. Read from the page's own
 * marks and the saved layout, never measured. */
function hereNow(){
  const tab=document.body.dataset.activeWorkspaceView??'',ids=new Set<string>(bridge.drawnOnStage());
  for(const root of document.querySelectorAll<HTMLElement>('[data-study-retained],[data-note-id]')){
    if(root.hidden||root.dataset.workspaceHidden==='true'||root.dataset.dismissed==='true'||root.dataset.resultTucked==='true')continue;
    if(root.dataset.workspaceView&&root.dataset.workspaceView!==tab)continue;
    if(root.dataset.noteId){ids.add(root.dataset.noteId);continue;}
    try{const id=JSON.parse(root.dataset.studyRetained!)?.resource?.id;if(typeof id==='string')ids.add(id);}catch{/* not a kept thing's window */}
  }
  return {tab,ids,onStage:snapshot.onStage,retained:bridge.capturePresentationSync().retained};
}
const hereKey=(here:ReturnType<typeof hereNow>)=>`${here.tab}|${here.onStage}|${JSON.stringify(here.retained)}|${[...here.ids].sort().join(' ')}`;
function rowHere(row:DataRow,here:ReturnType<typeof hereNow>):boolean{
  if(row.asset&&row.asset.id===here.onStage&&sameMediaReference(row.asset.retained,here.retained))return true;
  return row.parts.some(part=>here.ids.has(part.id)||workspaceViews.places(part.id).some(place=>place.id===here.tab));
}
// A Tab switch, a window opened, moved or closed, or outlines drawn or taken off: Data's "On this Tab" marks are checked
// again once things settle, and the list is drawn only when they changed.
let lastHereKey='',hereTimer=0;
function hereChanged():void{clearTimeout(hereTimer);hereTimer=window.setTimeout(()=>{if(dataOpen&&dataMode==='outputs'&&hereKey(hereNow())!==lastHereKey)renderResults();},200);}
for(const name of ['workspace-tab-context','workspace-layout-changed','hooke-stage-drawn'])window.addEventListener(name,hereChanged);
/** What a Data row does (ticket 66): it opens on the right of the Tab you are on, in the reader; nothing moves you to
 * another Tab. An input's ⋯ keeps Open in its Tab and Show in Inputs; a thing made keeps every action its card had in
 * its ⋯, and a run's other parts (its answer) keep theirs there under their own names. */
function dataRowView(row:DataRow):DataRowView{
  const asset=row.asset;
  if(asset)return {tip:'Open it here, on the right; Open in its Tab is in ⋯',open:()=>openInReader(inputEntry(asset)),current:readerShowing(row.entry??inputEntry(asset)),
    menu:()=>[{label:'Open in its Tab',act:()=>{setDataOpen(false,false);void openSource(asset);}},{label:'Show in Inputs',act:()=>{showSection('inputs');setDrawer(true);}},...(row.entry?.kind==='project'?entryActions(row.entry).menu():[])]};
  const own=entryActions(row.entry!),others=row.parts.slice(1).map(part=>({part,actions:entryActions(part)}));
  return {tip:own.tip,open:own.open,drag:own.drag,current:row.parts.some(part=>readerShowing(part)),menu:()=>[...own.menu(),...others.flatMap(({part,actions}):MenuItem[]=>
    ['separator',{heading:partWord(part)},{label:'Open',title:part.title,aria:`Open ${part.title}`,act:actions.open},...actions.menu()])]};
}
/** One kept thing's open (in the reader), ⋯, tooltip and drag. */
function entryActions(entry:DataEntry):{open:()=>void;menu:()=>MenuItem[];tip:string;drag:(wrap:HTMLElement)=>void}{
    // A row opens on the right of the Tab you are on, in the reader (ticket 66): nothing moves you to another Tab.
    const open=()=>{ if (entry.material?.kind === 'assignment') void openStudy(entry.id); else openInReader(entry as ReaderEntry); };
    const places=workspaceViews.places(entry.id,entry.version??0),source=sourceOf(entry),owner=workspaceViews.tabOwner();
    // Its maker by display name (the model's one name, a researcher's name), never a raw key.
    const maker=madeByWords(entry,entry.kind==='research'?bridge.researchers().find(item=>item.rid===entry.id)?.name??null:null,
      {model:made=>bridge.makerName(made),researcher:rid=>bridge.researchers().find(item=>item.rid===rid)?.name});
    const summary=entry.untrusted?'':entry.summary||entry.plain;
    const tip=[summary,maker?`Made ${maker}${entry.madeBy?.tool?` · ${entry.madeBy.tool}`:''}`:'','Open it here, on the right · drag onto a Tab to open it there'].filter(Boolean).join('\n');
    // Every action the card had stays, in its ⋯ (workspace-data-card.ts places them): the Tabs it is on, its kind's
    // next look, Add to Tab…, How it was made and the rest.
    const actions=menuActions(entry,{placed:places.length>0,source:!!source});
    const menu=():MenuItem[]=>{
      const items:MenuItem[]=[];
      for(const action of actions){
        if(action==='reveal'){for(const place of places)items.push({label:place.open?`Go to ${place.title}`:`Go to ${place.title} (closed)`,title:`Go to ${place.title}`,act:()=>{setDataOpen(false,false);void workspaceViews.reveal(entry.id,place.id,entry.version??null,owner);}});continue;}
        if(action==='open-in')items.push({label:'Add to Tab…',act:()=>chooseTabFor(entry)});
        if(action==='masks')items.push({label:'Show masks',act:()=>{setDataOpen(false,false);void bridge.openMasks(entry).catch(actionError);}});
        if(action==='measurements')items.push({label:'Measurements',act:()=>{setDataOpen(false,false);openDataTable(entry);}});
        if(action==='capture')items.push({label:'Open captured input',act:()=>{setDataOpen(false,false);void openStudyCapture({resource:studyEntryReference(entry,snapshot.session),title:entry.title,zoom:1,pan:[.5,.5],presentation:'capture'});}});
        if(action==='use-source'){const asset=snapshot.sources.find(item=>item.id===source);items.push({label:`Use ${asset?.title ?? 'its source'} as analysis input`,act:()=>{if(asset){setDataOpen(false,false);void openSource(asset);}else openInReader(entry as ReaderEntry,'how');}});}
        if(action==='provenance')items.push('separator',{label:'How it was made',title:'Who made it, from what and with what, in the reader on the right',act:()=>openInReader(entry as ReaderEntry,'how')});
      }
      // The mock's row ⋯: what its researcher made (Data under that maker's chip), and a Tab of its own.
      const who=(entry as DataEntry).maker;
      if(who?.kind==='researcher'&&who.name)items.push({label:`Show what ${who.name} made`,act:()=>showMadeBy(researcherKey(who.name))});
      if (entry.kind === 'project' && entry.material && !entry.material.record && entry.material.kind !== 'assignment') {
        items.push({label:'Organize material…',act:()=>organizeMaterial(entry, owner.session,
          ()=>inputOwnerCurrent(owner), ()=>void refreshResults())});
      }
      items.push({label:'Open in new Tab',act:()=>void openReaderTab(entry as ReaderEntry)});
      return items;
    };
    // Drag a row onto a Tab (or +) to open it there; the drop is checked again against the open Project.
    const drag=(wrap:HTMLElement)=>{wrap.draggable=true;
      wrap.addEventListener('dragstart',event=>{if(!event.dataTransfer)return;const now=workspaceViews.tabOwner();if(entry.day!==now.session||snapshot.session!==now.session){event.preventDefault();return;}event.dataTransfer.setData(ENTRY_DRAG,JSON.stringify({owner:now,entry:{day:entry.day,id:entry.id,version:entry.version??null}}));event.dataTransfer.setData('text/plain',entry.title);event.dataTransfer.effectAllowed='copy';});};
    return {open,menu,tip,drag};
}
/** Draws Data's list again, keeping the reading position; `opened`: the window has just been shown, so it is at the top
 * and nothing is measured (inside a press, a read here was a forced style pass). */
function renderResults(opened = false): void {
  if (!dataOpen) return;
  dataTabs.querySelectorAll<HTMLButtonElement>('button').forEach(b=>b.setAttribute('aria-selected',String(b.dataset.mode===dataMode)));
  if(dataMode==='history'){historyNode.hidden=false;dataView.show(historyNode);return;}
  const readingPosition=opened?0:dataBody.scrollTop;
  // Data by what each thing is (data-roles.ts): narrowed to one maker (Made by, or a researcher's "Made …" in
  // Researchers), to what was made from one input (Inputs' Results), then by the search.
  const all=dataRowsNow(),here=hereNow();lastHereKey=hereKey(here);
  const shown=all.filter(row=>(!makerFilter||rowMaker(row)===makerFilter)&&(!sourceFilter||rowFrom(row,sourceFilter))&&rowMatches(row,dataSearch.value));
  dataList.render({rows:shown,choices:makerChoices(all),maker:makerFilter,query:dataSearch.value,loading:resourceSession!==snapshot.session,
    source:sourceFilter===null?null:snapshot.sources.find(item=>item.id===sourceFilter)?.title??'one input',
    here:row=>rowHere(row,here),seen:dataSeen(),now:Date.now()/1000,model:entry=>entry.madeBy?bridge.makerName(entry.madeBy):''});
  if(resultsFailed()){const notice=el('div','study-error');notice.setAttribute('role','status');notice.append(el('p','','Could not refresh. Showing saved Project data.'),button('Retry results',()=>retryProjectReads()));dataList.list.prepend(notice);}
  dataView.showResults();
  if(readingPosition)dataBody.scrollTop=readingPosition;
  watchSeen();
}
// New since you last looked (I-2): what has been in view in Data for a moment is seen, per Project, in this browser.
const SEEN_KEY='studio-data-seen';
function dataSeen():Set<string>{try{return new Set(JSON.parse(localStorage.getItem(`${SEEN_KEY}:${snapshot.session}`)??'[]') as string[]);}catch{return new Set();}}
function markSeen(keys:string[]):void{if(!keys.length)return;const seen=dataSeen();for(const key of keys)seen.add(key);try{localStorage.setItem(`${SEEN_KEY}:${snapshot.session}`,JSON.stringify([...seen].slice(-4000)));}catch{/* a convenience only */}renderRailBadges();}
/** Data's badge counts rows, not entries (a run's outlines and answer are one), and never an input or a source. */
function unseenCount():number{if(resourceSession!==snapshot.session)return 0;return unseenRows(dataRowsNow(),dataSeen());}
let seenWatcher:IntersectionObserver|null=null;const seenTimers=new Map<Element,number>();
function watchSeen():void{
  seenWatcher?.disconnect();seenTimers.forEach(timer=>clearTimeout(timer));seenTimers.clear();
  if(typeof IntersectionObserver==='undefined')return;
  seenWatcher=new IntersectionObserver(records=>{for(const record of records){const card=record.target as HTMLElement;if(record.isIntersecting&&card.dataset.fresh){seenTimers.set(card,window.setTimeout(()=>{seenTimers.delete(card);delete card.dataset.fresh;markSeen((card.dataset.seen??'').split(' ').filter(Boolean));},1200));}else{const timer=seenTimers.get(card);if(timer){clearTimeout(timer);seenTimers.delete(card);}}}},{root:dataBody,threshold:0.6});
  for(const card of dataBody.querySelectorAll<HTMLElement>('.data-row[data-fresh]'))seenWatcher.observe(card);
}
/** Data's Open in Tab…: a Tab of this Project, or a new one, comes into view and the result opens there. Opening it is
 * the same as Open result; nothing opens unless that Tab really is the one in view. */
/** Reaches a Tab, then opens a Data result there; nothing opens if the Tab or the Project changed on the way. */
async function openEntryIn(entry:StudyResource,owner:ReturnType<typeof workspaceViews.tabOwner>,reach:()=>Promise<boolean>):Promise<void>{
  const current=()=>snapshot.session===owner.session&&entry.day===owner.session&&workspaceViews.ownsTabChoice(owner);
  if(!current()||!await reach()||!current()){actionError(new Error('The Tab did not open, so the result was not opened. It stays in Data.'));return;}
  setDataOpen(false,false);void openDataEntry(entry).catch(actionError);
}
/** Add to Tab… (Data's row ⋯ and the reader's): the result goes into that Tab, or a new one named after it, and you stay
 * where you are; the Tab row's toast says "Added to Tab 2" with Go to Tab 2 once the save was accepted. */
function chooseTabFor(entry:StudyResource):void{
  const owner=workspaceViews.tabOwner();
  const current=()=>snapshot.session===owner.session&&entry.day===owner.session&&workspaceViews.ownsTabChoice(owner);
  if(!current())return;
  const dialog=el('dialog','workspace-recovery-dialog study-tab-chooser');dialog.setAttribute('aria-label',`Add ${entry.title} to a Tab`);
  const go=(tab:string|null)=>{dialog.close();if(!current())return;let item:StudyRetainedView;try{item=retainedItem(entry);}catch(error){actionError(error);return;}void workspaceViews.addItem(tab,item,owner).catch(actionError);};
  dialog.append(el('h2','',`Add “${readerTitle(entry.title,entry.kind)}” to`));
  for(const tab of workspaceViews.tabChoices().filter(choice=>choice.kind!=='reader'))dialog.append(button(tab.current?`${tab.title} (this Tab)`:tab.title,()=>go(tab.id)));
  dialog.append(button('+ New Tab',()=>go(null)),button('Cancel',()=>dialog.close(),'study-quiet'));
  dialog.addEventListener('close',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
}
/** A Data result as a window placement for a Tab: its exact identity and the window it opens in (masks, a table, a data
 * table, an analysis or graph; else the kind's own, as Data opened it before). */
function retainedItem(entry:StudyResource):StudyRetainedView{
  const resource=studyEntryReference(entry,snapshot.session);
  const presentation:StudyRetainedView['presentation']=entry.kind==='population'?'masks':entry.kind==='dataset-table'?'datatable':entry.kind==='analysis'||entry.kind==='graph'?entry.kind:artifactPresentation(entry)==='table'?'table':undefined;
  return {resource,title:entry.title,zoom:1,pan:[.5,.5],...(presentation?{presentation}:{})};
}
async function openDataEntry(entry:StudyResource):Promise<void>{
  if(entry.kind==='recording'){
    const owner=snapshot.session;
    const response=await fetch(`/api/recordings/${encodeURIComponent(entry.id)}/asset?day=${encodeURIComponent(entry.day??owner)}`,{method:'POST'});
    const data=await response.json() as {asset?:AssetMeta;error?:string};
    if(!response.ok||!data.asset)throw new Error(data.error??'The recording could not be opened.');
    if(snapshot.session!==owner)throw new Error('The Project changed before the recording opened.');
    showPreview(data.asset);return;
  }
  if(entry.kind==='annotation'&&await workspaceViews.openNote(entry.id))return;
  // A researcher's lesson, video, picture, table, chart or source (ticket 66, S4) opens its Tab on it, as Data's own rows
  // for its lessons and presentations did before the server listed them.
  const work=researcherWork(entry);if(work){const row=bridge.researchers().find(item=>item.rid===work.rid);bridge.navigate(`#research/${encodeURIComponent(work.rid)}?day=${encodeURIComponent(row?.day||entry.day||snapshot.session)}${work.output?`&output=${encodeURIComponent(work.output)}`:''}`);return;}
  // A researcher's work opens as its Tab (one view of it, never a second window); one of its lessons or presentations opens that Tab on it.
  // Its report opens on its current form when that is its presentation, as the presentation's own row did (ticket 66: a
  // presentation is a form of the report, never a row).
  if(entry.kind==='research'||entry.id.includes('#')){const [rid,own]=entry.id.split('#');const current=(entry as DataEntry).forms?.[0];const output=own??(current?.label==='Presentation'?current.outputId:undefined);const row=bridge.researchers().find(item=>item.rid===rid);bridge.navigate(`#research/${encodeURIComponent(rid)}?day=${encodeURIComponent(row?.day||entry.day||snapshot.session)}${output?`&output=${encodeURIComponent(output)}`:''}`);return;}
  await bridge.openResult(entry);
}
function openDataTable(entry: StudyResource): void {
  openStudyTable({resource:studyEntryReference(entry,snapshot.session),title:entry.title,zoom:1,pan:[.5,.5],presentation:'table'});
}
// ---- The Data reader (data-reader.ts; ticket 66, D1) ------------------------------------------------------------
/** The reader's width now: the mock's 600 px beside Data, narrower when the Stage would keep less; 540 px below 1200 px,
 * where it takes the Data drawer's place. */
function readerNow(): number {
  return readerWidth(innerWidth, 72, drawerOpen || dataOpen ? (!dataOpen && section === 'tools' ? toolsNow() : leftNowWidth()) : 0);
}
/** The side reader is on the right edge: this work Tab has an item open, and no page holds the Stage (a researcher's,
 * a Study's or a reader Tab's, where the reader belongs to that page). */
function readerVisible(): boolean { return readerShown && !assistantAside(); }
/** The item the side reader shows is this one (Data marks its row). */
function readerShowing(entry: { id: string; day?: string; version?: number | null }): boolean {
  // A researcher's Tab in front: the row its reader shows.
  const shown = researchPageInFront() ? researchReader()?.current() ?? null : readerShown ? reader.current() : null;
  return !!shown && shown.id === entry.id && (shown.day ?? snapshot.session) === (entry.day ?? snapshot.session);
}
type ReaderMemory = { id: string; day: string; version: number | null; view: 'thing' | 'how' };
/** Each Tab's open item, for the session (never in the saved layout): switching back to a Tab shows it again. */
function readerTabKey(): string { return `${snapshot.session}::${document.body.dataset.activeWorkspaceView ?? ''}`; }
function readerMemory(): Record<string, ReaderMemory> {
  try { const value = JSON.parse(sessionStorage.getItem(`${storageKey}:reader`) ?? '{}'); return value && typeof value === 'object' ? value : {}; } catch { return {}; }
}
function rememberReader(entry: ReaderEntry | null, view: 'thing' | 'how' = 'thing'): void {
  const all = readerMemory(), key = readerTabKey();
  if (entry) all[key] = { id: entry.id, day: entry.day ?? snapshot.session, version: entry.version ?? null, view }; else delete all[key];
  try { sessionStorage.setItem(`${storageKey}:reader`, JSON.stringify(all)); } catch { /* a convenience: the reader still shows */ }
}
/** An input of this Project as the reader reads it. */
function inputEntry(asset: AssetMeta): ReaderEntry {
  return sourceReaderEntry(asset,snapshot.session);
}
/** A Data item by its identity: this Project's exact index or pinned input revision, or one of its inputs. */
function readerEntryOf(ref: { id: string; day: string; version: number | null }): ReaderEntry | null {
  const found = projectEntry(entries, ref, snapshot.session);
  if (found) return found as ReaderEntry;
  const asset = ref.day === snapshot.session ? snapshot.sources.find((item) => item.retained ?
    item.retained.document===ref.id && item.retained.session===ref.day && item.retained.revision===ref.version : item.id===ref.id) : undefined;
  return asset ? inputEntry(asset) : retainedProjectEntry(ref, snapshot.session) as ReaderEntry | null;
}
/** A Data row (or a link inside the reader) opens on the right of the Tab you are on. Below 1200 px it takes the Data
 * drawer's place (‹ Data brings the list back). The Assistant steps aside (× brings it back if it was showing); a page
 * docked on the right parks, as it does when the Assistant opens. One layout write (press). */
function openInReader(entry: ReaderEntry, view: 'thing' | 'how' = 'thing'): void {
  // A retained reader Tab owns one exact item. A different input gets its own
  // retained Tab so the label, visible content and restored reference stay aligned.
  if (document.body.dataset.readerTab === 'true' && researchStage.hidden) {
    const current = tabReader.current();
    if (current && sameItem(current, entry)) { tabReader.show(entry, view); renderReaderLabel(); }
    else void openReaderTab(entry, view);
    return;
  }
  // On a researcher's Tab its own reader is on the right (ticket 66 D3): the item shows there, and the Tab stays.
  if (researchPageInFront() && showInResearchReader(entry, view)) { markReaderRow(); return; }
  const fromData = dataOpen;
  press(() => {
    if (!researchStage.hidden) { if (!canLeaveStudy()) return; clearStudyDraftMarkers(); closeResearchStage(); }
    const narrow = innerWidth < 1200;
    readerBack = narrow && fromData;
    if (narrow) { if (dataOpen) setDataOpen(false, false, false); drawerOpen = false; preferences.drawer = false; }
    parkControls('right');
    readerShown = true;
    rememberReader(entry, view);
    reader.show(entry, view, { narrow: readerBack });
    applyLayout();
  });
  markReaderRow();
}
/** × on the reader: the item closes (forgotten for this Tab) and the Assistant comes back if it was showing; ‹ Data
 * (`back`) gives the Data drawer its place again. */
function closeReader(back = false, layout = true): void {
  readerShown = false; readerBack = false; rememberReader(null); reader.clear(); markReaderRow();
  if (back) { dataMode = 'outputs'; setDataOpen(true, true); }
  if (layout) applyLayout();
}
/** Data's rows say which one the reader shows (the mock's data-current). */
function markReaderRow(): void {
  for (const wrap of dataBody.querySelectorAll<HTMLElement>('.mm-row-wrap[data-resource]')) {
    const on = readerShowing({ id: wrap.dataset.resource!, day: snapshot.session });
    if (on) wrap.dataset.current = 'true'; else delete wrap.dataset.current;
  }
}
/** Switching Tabs shows each Tab's own item again, or none. */
let readerTabShown = '';
let readerPending: ReaderMemory | null = null;
function syncReaderToTab(): void {
  const key = readerTabKey(); if (key === readerTabShown) return; readerTabShown = key;
  if (document.body.dataset.workspaceOpening === 'true' || document.body.dataset.readerTab === 'true') { readerPending = null; if (readerShown) { readerShown = false; reader.clear(); applyLayout(); } return; }
  const saved = readerMemory()[key] ?? null;
  const entry = saved ? readerEntryOf(saved) : null;
  readerPending = saved && !entry ? saved : null;
  if (entry) { readerShown = true; readerBack = false; reader.show(entry, saved!.view, { narrow: false }); }
  else if (readerShown) { readerShown = false; reader.clear(); }
  else return;
  applyLayout(); markReaderRow();
}
window.addEventListener('workspace-tab-context', () => syncReaderToTab());
/** The Assistant opens a Data item the way a person does (ticket 66, D6: `present` open, show_result): in the reader on
 * the Tab you are on, through openInReader as a row's click; its close is the reader's ×. The item comes with the
 * request as Data lists it, so one Data has not caught up with yet still opens. Only in the workspace opening it was
 * asked in (`check`, the Tab strip's pin); it is done once the screen report shows it (orchestrator/data_reader.py). */
window.addEventListener('hooke-reader-request', (event) => {
  const detail = (event as CustomEvent<ReaderRequestMessage & { check: () => boolean }>).detail;
  const ref = detail?.item?.resource;
  if (!ref || detail.session !== snapshot.session || !detail.check()) return;
  const item = { id: ref.id, day: ref.day, version: ref.version ?? undefined };
  if (detail.do === 'close') {
    const shown = readerShown ? reader.current() : null;
    if (shown && sameItem(shown, item)) press(() => closeReader());
    return;
  }
  const listed = readerEntryOf(ref);
  const sent = detail.entry?.id === ref.id ? { ...detail.entry, day: ref.day } as unknown as ReaderEntry : null;
  const entry = listed && sameItem(listed, item) ? listed : sent ?? listed;
  if (!entry) return;
  openInReader(entry);
  if (entry !== listed) void refreshResults();
});
/** New Data arrived: a remembered item waiting for it opens, the open items' headers follow their entries, and a reader
 * Tab waiting for its item shows it (or says it is gone). */
function readerEntriesChanged(): void {
  if (readerPending && readerTabKey() === readerTabShown) {
    const entry = readerEntryOf(readerPending);
    if (entry) { readerShown = true; reader.show(entry, readerPending.view, { narrow: false }); readerPending = null; applyLayout(); markReaderRow(); }
  }
  for (const shown of [reader, tabReader]) { const current = shown.current(); if (current && current.kind !== 'asset') { const next = readerEntryOf({ id: current.id, day: current.day ?? snapshot.session, version: current.version ?? null }); if (next) shown.refresh(next); } }
  if (tabReaderPending) showReaderTab(null, tabReaderPending);
  researchReaderEntriesChanged();
  if (legacyShown) showLegacyTab(legacyShown);
}
/** A reader Tab came into view (its one item, full width; the Assistant opens as a layer over it), or left it. */
let tabReaderPending: ReturnType<typeof readerItem> = null;
function showReaderTab(view: Parameters<NonNullable<ConstructorParameters<typeof WorkspaceViews>[0]['reader']>>[0], waiting: ReturnType<typeof readerItem> = null): void {
  const item = waiting ?? (view ? readerItem(view) : null);
  if (!item) { tabReaderPending = null; if (tabReader.current() || !tabReader.root.hidden) tabReader.clear(); assistantLayer.reset(); renderReaderLabel(); applyLayout(); return; }
  const entry = readerEntryOf({ id: item.resource.id, day: item.resource.day, version: item.resource.version });
  if (entry) { tabReaderPending = null; if (!tabReader.current() || !sameItem(tabReader.current()!, entry)) tabReader.show(entry, 'thing'); }
  else if (resourceSession === snapshot.session && !waiting) { tabReaderPending = item; void refreshResults(); }
  else if (resourceSession === snapshot.session) { tabReaderPending = null; tabReader.showMissing(item.title, 'It is not in this Project’s Data any more. This Tab can be closed; nothing else changes.'); }
  else tabReaderPending = item;
  renderReaderLabel(); applyLayout();
}
/** A Tab 0.3.0 made for a researcher's results (ticket 66 D5; legacy-output-tab.ts): the pointer card on the Stage and
 * its name on the Stage bar, instead of the old report window; its saved windows are kept as they were. */
const legacyHost = el('div', 'legacy-tab-host'); legacyHost.hidden = true; find('stage').append(legacyHost);
let legacyShown: Parameters<NonNullable<ConstructorParameters<typeof WorkspaceViews>[0]['legacy']>>[0] = null, legacyKey = '';
function showLegacyTab(view: typeof legacyShown): void {
  legacyShown = view;
  const rid = legacyResearcher(view);
  if (!view || !rid) { legacyHost.hidden = true; legacyHost.replaceChildren(); legacyKey = ''; return; }
  const row = bridge.researchers().find((item) => item.rid === rid) ?? null;
  const made = resourceSession === snapshot.session ? entries.map((entry) => ({ ...entry, day: entry.day ?? snapshot.session }) as ReaderEntry) : [];
  const words = legacyWords({ rid, name: row?.name ?? null, title: legacyTitle(made, rid, row ? clean(row.result?.title || row.label || row.brief || '') : ''), entries: made });
  const cell = row ? researcherCell(row) : null;
  // Drawn again only when what it says changed (Data's refresh, the researcher's state).
  const key = JSON.stringify([view.id, words, cell]);
  if (key !== legacyKey) {
    legacyKey = key;
    legacyHost.replaceChildren(legacyCard(words, cell, { open: () => openResearcherTab(rid), close: () => workspaceViews.command('close') }));
    const slot = workspaceViews.stageSlots().reader;
    slot.replaceChildren(el('strong', '', view.title), el('span', '', 'Made by an earlier version'));
  }
  legacyHost.hidden = false;
}
/** A researcher's own Tab, from a 0.3.0 results Tab's pointer or its old "Go to results" notice. */
function openResearcherTab(rid: string): void {
  const row = bridge.researchers().find((item) => item.rid === rid);
  if (!row) { actionError(new Error('That researcher is not in this Project now. What it made stays in Data.')); return; }
  bridge.researchOpened(rid);
  bridge.navigate(`#research/${encodeURIComponent(rid)}?day=${encodeURIComponent(row.day || snapshot.session)}`);
}
/** A reader Tab's Stage bar names its item where a work Tab names its specimen ("Bright regions in Human cells ·
 * Presentation · Researcher 3"). */
function renderReaderLabel(): void {
  const slot = workspaceViews.stageSlots().reader;
  const shown = document.body.dataset.readerTab === 'true' && tabReader.current();
  if (!shown) { if (document.body.dataset.legacyTab !== 'true' && slot.childNodes.length) slot.replaceChildren(); return; }
  const title = el('strong', '', tabReader.title()), kind = el('span', '', tabReader.kindLine());
  title.title = tabReader.title();
  if (workspaceViews.readerTabsUnsaved()) kind.title = 'This Hooke server cannot save a Tab like this one: it stays open for this session.';
  slot.replaceChildren(title, kind);
}
/** What the reader needs from the workspace: names and facts it shows, and where each of its actions goes. */
/** Opening setup never starts work; all choices retain the originating revision. */
function materialAction(entry:ReaderEntry,action:'inspect'|'experiment'|'ask'):void{
 const material=entry.material;if(!material){actionError(new Error('Refresh this material before continuing.'));return;}
 const reference={id:entry.id,session:entry.day??snapshot.session,revision:Number(entry.version),digest:entry.source?.sha256??''},session=reference.session;
 if(!Number.isInteger(reference.revision)||!reference.digest){actionError(new Error('Refresh this material to read its exact revision.'));return;}
 if(session!==snapshot.session)return;
 const owner=workspaceViews.tabOwner(),owns=()=>inputOwnerCurrent(owner)&&!switching;
 const selected={reference,title:entry.title};
 const proceed=(study?:Effort)=>{
  if(!owns())return;
  if(action==='ask'){if(study)void openStudy(study.id,selected);return;}
  openMaterialCapability({material:{input:reference,title:entry.title},study:study?{id:study.id,title:studyName(study)}:undefined,
   capability:action==='inspect'?'matrix-inspect':undefined},()=>owns()?session:'',run=>{if(owns())bridge.navigate(`#workflows?run=${encodeURIComponent(run.id)}`);});
 };
 const dialog=el('dialog','workspace-recovery-dialog material-study-chooser');dialog.setAttribute('aria-label','Choose a Study');
 dialog.append(el('h2','','Choose a Study'),el('p','',`${entry.title} · revision ${reference.revision}`),el('p','','Keep this question or experiment with a research effort. Opening setup starts nothing.'));
 const list=el('div','material-study-options');
 for(const study of knownStudies){list.append(button(studyName(study),()=>{dialog.close();proceed(study);}));}
 list.append(button('New Study…',()=>{dialog.close();if(owns())newStudy(selected,proceed);}));
 if(action==='inspect')list.append(button('Inspect without a Study',()=>{dialog.close();proceed();}));
 dialog.append(list,button('Cancel',()=>dialog.close()));dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();
}
function readerDeps(mode: 'side' | 'tab'): import('./data-reader').ReaderDeps {
  return {
    materialAction, openStudy:id=>{void openStudy(id);},
    retainedEntry: (ref) => resourceSession === snapshot.session && ref.day === snapshot.session
      ? indexedRevision(entries, ref, snapshot.session) as ReaderEntry | null : null,
    researchers: () => bridge.researchers().map((row) => ({ rid: row.rid, name: row.name, day: row.day, status: row.status })),
    working: (rid) => { const row = bridge.researchers().find((item) => item.rid === rid); return !!row && isWorking(row); },
    method: (entry) => entry.madeBy ? bridge.makerName(entry.madeBy) : '',
    input: (id) => { const asset = snapshot.sources.find((item) => item.id === id); return asset ? { id: asset.id, title: asset.title } : null; },
    places: (entry) => entry.kind === 'asset' ? [] : workspaceViews.places(entry.id, entry.version ?? 0),
    tab: () => document.body.dataset.activeWorkspaceView ?? '',
    close: () => press(() => closeReader()),
    back: () => press(() => closeReader(true)),
    talk: (rid, entry) => talkToResearcher(rid, entry),
    openInNewTab: (entry) => void openReaderTab(entry),
    showOnPicture: (entry, show) => bridge.showLayer(studyEntryReference(entry, snapshot.session), show),
    onPicture: (entry) => bridge.shownLayer() === entry.id,
    addToTab: (entry) => chooseTabFor(entry),
    goTo: (tab) => { const owner = workspaceViews.tabOwner(); void workspaceViews.goTo(tab, owner).catch(actionError); },
    routes: (entry) => readerRoutes(entry),
    trash: (entry) => readerTrash(entry),
    editNote: (entry) => void workspaceViews.openNote(entry.id).catch(actionError),
    open: (ref) => {
      const entry = readerEntryOf({ id: ref.id, day: ref.day ?? snapshot.session, version: null });
      if (!entry) { actionError(new Error('That is not in this Project’s Data any more.')); return; }
      openInReader(entry);
    },
    changed: () => { markReaderRow(); if (mode === 'tab') renderReaderLabel(); },
  };
}
/** Talk to Researcher N: the one reader action that goes to another Tab (its own), and it says so. */
function talkToResearcher(rid: string, entry: ReaderEntry): void {
  const row = bridge.researchers().find((item) => item.rid === rid);
  // Its Tab opens with the same item on its right (a lesson or its report by its output, anything else by its id).
  openOnResearcherTab(rid, { id: entry.id, day: entry.day ?? snapshot.session });
  const output = entry.id.includes('#') ? entry.id.split('#')[1] : ['lesson', 'video', 'figure'].includes(entry.kind) && entry.id.includes(':') ? entry.id.split(':').slice(1).join(':') : '';
  bridge.navigate(`#research/${encodeURIComponent(rid)}?day=${encodeURIComponent(row?.day || entry.day || snapshot.session)}${output ? `&output=${encodeURIComponent(output)}` : ''}`);
}
/** Leave the researcher's page for the work Tab under it (false while a Study's form asks to be kept). */
function leaveResearchPage(): boolean { if (!canLeaveStudy()) return false; clearStudyDraftMarkers(); closeResearchStage(); return true; }
/** A researcher's own page is in front (not a Study's or the New researcher draft): its reader is the one on the right. */
function researchPageInFront(): boolean {
  return !researchStage.hidden && !openStudyNow && shownResearch !== draftPage && location.hash.startsWith('#research/');
}
// The reader on a researcher's Tab (research-workspace.ts, ticket 66 D3) reads this Project's Data and leaves through
// the workspace: Open in new Tab, Add to Tab…, Go to a Tab, Talk to another researcher, Show on Main ↗ (closes the
// researcher's Tab and opens it on the work Tab, where Data opens it) and All its work in Data.
provideResearchReader({
  entries: () => resourceSession === snapshot.session ? entries.map((entry) => ({ ...entry, day: entry.day ?? snapshot.session }) as ReaderEntry) : [],
  deps: {
    ...readerDeps('side'),
    open: undefined, close: undefined, back: undefined, changed: () => markReaderRow(),
    routes: () => [],
    // What goes to another Tab leaves the researcher's page first, as a click on a work Tab does.
    openInNewTab: (entry) => { if (leaveResearchPage()) void openReaderTab(entry); },
    goTo: (tab) => { if (!leaveResearchPage()) return; const owner = workspaceViews.tabOwner(); void workspaceViews.goTo(tab, owner).catch(actionError); },
    leave: (entry) => { if (leaveResearchPage()) openInReader(entry); },
    home: () => openTab() || 'Main',
    allInData: (rid) => {
      const name = bridge.researchers().find((row) => row.rid === rid)?.name ?? '';
      // Data under this researcher's chip (the maker filter, ticket 66 D2); with no name known, Data unfiltered.
      surfaces.dismiss();
      if (name) showMadeBy(researcherKey(name)); else press(() => { dataMode = 'outputs'; setDataOpen(true); });
    },
  },
});
/** Open in new Tab: the item in a Tab of its own, the same reader full width. */
async function openReaderTab(entry: ReaderEntry, view: 'thing' | 'how' = 'thing'): Promise<void> {
  // Its Tab is named as the reader names it (a researcher's report by its findings' title once read).
  const shown = reader.current(), title = shown && sameItem(shown, entry) ? reader.title() : readerTitle(entry.title, entry.kind);
  try {
    const opened = await workspaceViews.openReaderTab({ resource: { day: entry.day ?? snapshot.session, id: entry.id, version: entry.version ?? null }, title });
    const current = tabReader.current();
    if (opened && view === 'how' && current && sameItem(current, entry)) tabReader.show(entry, view);
  }
  catch (error) { actionError(error); }
}
/** Every route the item's Data row had, by name, in the reader's ⋯ (nothing is removed: the kind's full tool opens as
 * its window on this Tab, as a row click did before). */
function readerRoutes(entry: ReaderEntry): MenuItem[] {
  const items: MenuItem[] = [];
  if (entry.kind === 'asset' && entry.asset) {
    const asset = entry.asset;
    items.push({ label: 'Open in its Tab', title: 'This input in a Tab of its own, on the Stage', act: () => void openSource(asset) },
      { label: 'Show in Inputs', title: 'Where this Project’s inputs are listed', act: () => press(() => { showSection('inputs'); setDrawer(true); }) });
    return items;
  }
  if (entry.kind === 'recording') items.push({ label: 'Open in its Tab', title: 'The recording in a Tab of its own, on the Stage', act: () => void openRecordingTab(entry) });
  if (entry.kind === 'population') items.push({ label: 'Open masks window', title: 'Its masks in a window on this Tab, for corrections', act: () => void bridge.openMasks(entry).catch(actionError) });
  if (['population', 'tracks'].includes(entry.kind) || artifactPresentation(entry) === 'table') items.push({ label: 'Open in Measurements', title: 'Its measurements in a window on this Tab', act: () => openDataTable(entry) });
  if (entry.kind === 'project') items.push({ label: 'Open the scientific viewer', title: 'In the scientific viewer, on this Tab', act: () => void bridge.openResult(entry).catch(actionError) });
  if (entry.kind === 'dataset-table') items.push({ label: 'Open the data table', title: 'In the data table window, on this Tab', act: () => void bridge.openResult(entry).catch(actionError) });
  if (entry.kind === 'analysis' || entry.kind === 'graph') items.push({ label: entry.kind === 'graph' ? 'Open the graph window' : 'Open the analysis window', title: 'In its window, on this Tab', act: () => void bridge.openResult(entry).catch(actionError) });
  if (entry.kind === 'population') items.push({ label: 'Open captured input', title: 'The picture it was made from, as captured', act: () => void openStudyCapture({ resource: studyEntryReference(entry, snapshot.session), title: entry.title, zoom: 1, pan: [.5, .5], presentation: 'capture' }) });
  const source = sourceOf(entry);
  if (source) { const asset = snapshot.sources.find((item) => item.id === source); items.push({ label: `Use ${asset?.title ?? 'its source'} as analysis input`, title: 'Puts the picture it was made from on the Stage, in its Tab', disabled: !asset, act: () => { if (asset) void openSource(asset); } }); }
  if (entry.kind === 'annotation') items.push({ label: 'Edit in a note window', title: 'The note window on this Tab', act: () => void workspaceViews.openNote(entry.id).catch(actionError) });
  // What else Data opened as a window (an answer, tracks, a snapshot…): that window, on this Tab.
  const own = ['population', 'project', 'dataset-table', 'analysis', 'graph', 'annotation', 'recording', 'research', 'source', 'lesson', 'video', 'figure', 'presentation', 'finding-table', 'finding-chart'];
  if (!own.includes(entry.kind) && !entry.id.includes('#') && artifactPresentation(entry) !== 'table') items.push({ label: 'Open in a window', title: 'In a window on this Tab, as Data opened it before', act: () => void bridge.openResult(entry).catch(actionError) });
  return items;
}
/** Move to trash: an input goes from the Library, where it asks first (the work made from it stays). Nothing else in
 * Data has a way to the trash yet, and the menu says so rather than pretend. */
function readerTrash(entry: ReaderEntry): MenuItem {
  if (entry.kind === 'asset') return { label: 'Move to trash…', title: 'In the Library, where Move to trash asks first; the work made from it stays', act: () => bridge.navigate('#assets') };
  return { label: 'Move to trash', danger: true, disabled: true, title: 'Not yet: results stay kept in Data. Moving one to the trash comes in a later version.', act: () => {} };
}
/** A recording's own Tab: the recording as an input (the route its preview's Open in Tab took). */
async function openRecordingTab(entry: ReaderEntry): Promise<void> {
  try {
    const response = await fetch(`/api/recordings/${encodeURIComponent(entry.id)}/asset?day=${encodeURIComponent(entry.day ?? snapshot.session)}`, { method: 'POST' });
    const data = await response.json() as { asset?: AssetMeta; error?: string };
    if (!response.ok || !data.asset) throw new Error(data.error ?? 'The recording could not be opened.');
    await openSource(data.asset);
  } catch (error) { actionError(error); }
}
/** The open work tab's name; while a Project's arrangement is still loading, that, never the previous Project's tab. */
function openTab(): string | undefined {
  return document.body.dataset.workspaceOpening === 'true' ? 'Opening…' : workspaceViews.tabTitle(workspaceViews.researchHost().project,workspaceViews.researchHost().activeWorkId)??undefined;
}
let agentScopeKey='';
/** The Assistant panel's two states (the concept mock's structure). Where it can work: its box starts with the specimen
 * attached (picture and name, where answers go beneath; a click shows it in this Tab, or with nothing on the Stage
 * opens Inputs). In the example Project, where it is off, the panel says so once in its body and offers a Project of
 * your own; there is no box to type into, since nothing typed could be answered. */
const assistantOff=el('section','study-assistant-off');assistantOff.hidden=true;
// What it would do here, shown with the starters' own words and marks (as examples, not buttons), so the column says
// what the Assistant is rather than only that it is off.
const assistantExamples=el('ul','study-assistant-examples');
for(const starter of find('starters').querySelectorAll<HTMLElement>('button[data-starter]')){
  const item=el('li'),mark=starter.querySelector('svg')?.cloneNode(true);if(mark)item.append(mark);
  const words=el('span');words.append(el('strong','',starter.querySelector('strong')?.textContent??''),el('small','',starter.querySelector('span')?.textContent??''));
  item.append(words);assistantExamples.append(item);
}
assistantOff.append(el('h3','','This example Project'),
  el('p','','Your example inputs and results are kept here. You can inspect them with the local tools. The Assistant is off in this example to keep it local; open another Project to use it.'),
  assistantExamples,button('Open your Projects',()=>bridge.navigate('#sessions'),'mm-primary'));
find('starters').before(assistantOff);
function manageAgents(): void {
  bridge.navigate('#settings');
  requestAnimationFrame(() => settings.querySelector<HTMLElement>('.agent-connections-settings')?.scrollIntoView({ block: 'start' }));
}
function renderAgentScope(using: { name: string; thumb?: string; none: boolean }, output: string): void {
  const local = !!snapshot.session && snapshot.session === exampleProject();
  const scientific = scientificAskSelection();
  const key = JSON.stringify([using, output, local, scientific]); if (key === agentScopeKey) return; agentScopeKey = key;
  document.body.dataset.assistantLocal = String(local);
  assistantOff.hidden = !local;
  if (scientific) {
    // A saved scientific attachment for typed Ask: its chip, what goes with it, and Remove.
    const chip = el('span', 'mm-composer-chip mm-composer-chip-doc'); chip.append(el('span', 'mm-composer-chip-name', scientific.title));
    chip.title = `${scientific.title} · saved revision ${scientific.input.reference.revision} · ${scientific.input.reference.component}`;
    const remove = button('Remove attachment', clearScientificAsk, 'mm-composer-chip-remove');
    composer.setChips(chip, el('span', 'mm-composer-where', `Typed Ask · saved revision ${scientific.input.reference.revision} · ${scientific.input.reference.component} · Talk uses the Stage`), remove);
    return;
  }
  // The specimen as an attachment (its picture and name; a click shows it in this Tab, or with nothing on the Stage
  // opens Inputs), then where the answers go: the Tab that is showing.
  const attach = button('', () => {
    if (using.none) { showSection('inputs'); setDrawer(true); return; }
    workspaceViews.showInput();
  }, 'mm-composer-chip');
  // The specimen by its short name, as the Stage bar says it (camera-label.ts specimenName); its whole name in the
  // tooltip and the accessible name.
  attach.title = using.none ? 'Nothing is on the Stage yet. Add a specimen in Inputs.' : `${using.name}: your specimen, as the Assistant sees it. Click to show it in this Tab.`;
  attach.setAttribute('aria-label', using.none ? 'Add a specimen' : `Specimen: ${using.name}. Show it`);
  const thumb = el('span', 'mm-composer-thumb');
  if (using.thumb) { const picture = el('img'); picture.src = using.thumb; picture.alt = ''; thumb.append(picture); }
  attach.append(thumb, el('span', 'mm-composer-chip-name', using.none ? 'Add a specimen' : specimenName(using.name) || using.name));
  composer.setChips(attach, el('span', 'mm-composer-where', using.none ? 'The Assistant answers about what is on the Stage' : `answers go to ${output}`));
}
window.addEventListener('example-project', () => renderContext());
/** The rail's badges, from state already here: this Project's results once read, this workspace's run, researchers
 * working and the models that need you (the header chip's count). */
let lastUnseen=0;
function renderRailBadges(): void {
  // Counted as Data shows its rows (a run is one; not the inputs, sources or outcomes), so the badge and the panel agree.
  const unseen=unseenCount();if(unseen>lastUnseen&&!dataOpen){dataButton.dataset.pulse='true';window.setTimeout(()=>delete dataButton.dataset.pulse,700);}lastUnseen=unseen;
  // Researchers: a blue count of those waiting on you, else the lit cell while any researcher works (here or in
  // another Project, as the drawer lists them).
  const all=bridge.researchers(),listed=[...todaysResearch(all,snapshot.session),...workingElsewhere(all,snapshot.session)];
  const counts={ results: resourceSession === snapshot.session ? dataRowsNow().length : null, resultsNew: unseen,
    researchers: listed.filter(isWorking).length, researchersNeedYou: listed.filter(row=>!!asking(row)).length, modelsNeedYou: bridge.modelsNeedYou() };
  const badges = railBadges(counts);
  applyRailBadge(dataButton, 'Data', badges.data); applyRailBadge(activityButton, 'Activity', undefined);
  applyRailBadge(researchersButton, 'Researchers', badges.researchers); applyRailBadge(modelsButton, 'Models', badges.models);
  applyRailMark(researchersButton, researchersMark(counts), () => litCell('working', { size: 10, decorative: true }));
}
function renderContext(): void {
  const input = busy() && run ? run.input : snapshot;
  const scientific = input.operationInput && !('source' in input.operationInput) ? input.operationInput : null;
  context.replaceChildren(el('span', 'study-eyebrow', busy() ? 'INPUT CAPTURED FOR THIS RUN' : 'ANALYSIS INPUT'));
  const list = el('dl');
  for (const [label, value] of [['Input', input.operationInput && 'resource' in input.operationInput ? `Saved result ${input.operationInput.resource.id}` : input.operationInput && 'volume' in input.operationInput ? `Volume ${input.operationInput.volume}` : input.source?.title || (input.onStage === 'live' ? 'Live camera' : 'Choose an image')], ['Scope', busy() && run ? `${run.action} · ${run.scope}` : input.source?.kind === 'still' ? 'Bright objects · whole original image' : 'Bright objects · current camera field'],
    ['Selected outline', scientific ? 'Not used for this input' : input.selection?.label || 'None'], ['Output project', input.title], ['Output tab',busy()&&run?.outputTab?run.outputTab:openTab()??'Open a tab first'], [scientific ? 'Scientific input' : input.source ? 'Original image' : 'Display frame', scientific ? 'resource' in scientific ? `${scientific.resource.component} · revision ${scientific.resource.revision}` : scientific.volume : input.source ? input.sourceSize ? `${input.sourceSize.width} × ${input.sourceSize.height} pixels` : 'Dimensions not loaded' : input.frame ? `${input.frame.id} · ${input.frame.w} × ${input.frame.h}` : 'No image available']]) {
    list.append(el('dt', '', label), el('dd', '', value));
  }
  context.append(list);
  const activeTab=openTab()??'No open tab';
  // The live camera counts only while its pictures arrive: "Live camera" with nothing coming would claim a specimen.
  const liveSeen=snapshot.onStage==='live'&&!!snapshot.frame;
  renderAgentScope({name:snapshot.source?.title||(liveSeen?'Live camera':''),none:!snapshot.source&&!liveSeen,...(snapshot.source?.thumb?{thumb:snapshot.source.thumb}:{})},activeTab);
  runButton.disabled = !document.body.dataset.activeWorkspaceView || document.body.dataset.workspaceOpening === 'true' || !snapshot.ready || (!snapshot.frame && !snapshot.source) || busy(); stopButton.hidden = !busy();
  stopButton.disabled = run?.status === 'stopping';
  // A run started in another Project stays that Project's: its status names it here.
  if (run) runStatus.textContent = run.input.session === snapshot.session || run.message.includes(run.input.title) ? run.message : `${run.input.title}: ${run.message}`;
  resultButton.hidden = !run || run.status !== 'completed';
  sourceWords.textContent = snapshot.source?.title || (snapshot.onStage === 'live' ? 'Live camera' : 'No specimen yet');
  // "No specimen yet" only once the bridge has said nothing is on the Stage; before its first report the Stage bar's
  // left stays empty, so the chrome does not move when the specimen is named (layout shift 0 on opening).
  if (snapshot.heard !== false) workspaceViews.specimenLabel(sourceWords.textContent, snapshot.source?.thumb ?? '');
  sourceMode.textContent = snapshot.source ? 'HELD IMAGE' : snapshot.held ? 'HELD VIEW' : snapshot.frame ? 'LIVE VIEW' : 'NO CAMERA REQUIRED';
  document.body.dataset.studyRunning = String(busy());

  (find('source') as HTMLButtonElement).disabled = busy();
  document.querySelectorAll<HTMLButtonElement>('#example-guide button').forEach(control => { control.disabled = busy(); });
  renderRunPill();
  renderControlMarks();
}
// The hand-started runs' pills on the Working strip (strip-sources.ts RunPills, one per run): each its model's name and
// its time while it runs, then what happened; done stays green until its results are opened in Data or it is hidden. A
// run under a second shows none. Its click opens its run page while it runs (or when it failed), and Data once done.
function renderRunPill():void{runPills.render();}
// Its results opened from its run page: the run box's View retained results (the newest run's), or a Tools run page's
// Open in Data (RunPills.resultsOpened: the one run done in the Project in front).
resultButton.addEventListener('click',()=>{if(run?.status==='completed')runPills.leave(run.id);});
window.addEventListener('workspace-study-section',event=>{if((event as CustomEvent).detail==='results')runPills.resultsOpened(snapshot.session);});
// A kept run's background job that a newer start left behind (refreshBackgroundRun follows the newest run only) is read
// from the retained jobs on the same 10 s beat, so its pill never stays Queued once the job is over; while its stop is
// requested only the job's end changes what it says.
async function followKeptJobs():Promise<void>{
  for(const kept of new Set([...runPills.runs(),...[...handRuns.values()].map(hand=>hand.run)])){
    const job=kept.backgroundJob;
    if(kept===run||!job||['completed','failed','stopped'].includes(kept.status))continue;
    try{
      const response=await fetch(`/api/resources?kind=job&day=${encodeURIComponent(job.session)}`,{cache:'no-store'});
      if(!response.ok)continue;
      const data=await response.json() as {entries:StudyResource[]};const next={...kept};
      if(reconcileBackgroundRun(next,data.entries,job.session)&&!(kept.status==='stopping'&&next.status==='queued')&&(runPills.get(kept.id)===kept||handRuns.get(kept.id)?.run===kept)){
        kept.status=next.status;kept.message=next.message;kept.interrupted=next.interrupted;runPills.render();}
    }catch{/* Retained state is authoritative; a failed read never settles a job. */}
  }
}
setInterval(()=>void followKeptJobs(),10_000);
/** A run's own page: its task in Tools (or its entry in Models, for a catalog entry's run), as Use it here opens one. */
function openRunPage(capability:string):void{
  surfaces.dismiss();bridge.navigate('#');
  showSection('tools');setDrawer(true);
  bridge.openRun(capability);
}
async function startRun(): Promise<void> {
  const current = bridge.state();
  if (busy() || !current.ready || (!current.frame && !current.source)) return;
  bridge.navigate('#');
  try { await bridge.runAction({ capability: 'cells', args: {}, use: { cells: 'basic-bright-objects' }, run: crypto.randomUUID(), ...(delayInput.checked?{workspaceStudyDelayMs:10000}:{}) }); delayInput.checked=false; }
  catch (error) { if (run?.status !== 'failed') actionError(error); }
}

async function stopRun(): Promise<void> {
  if (!run || !busy()) return;
  const thisRun = run; thisRun.status = 'stopping'; thisRun.message = 'Stop requested. Waiting for the work to confirm it has ended.'; renderContext();
  try { if (thisRun.backgroundJob) await bridge.request('job-cancel', { id: thisRun.backgroundJob.id, session: thisRun.backgroundJob.session }); else await bridge.request('action-cancel', { run: thisRun.id }); }
  catch (error) { thisRun.message = `Cancellation could not be confirmed. ${String(error)}`; renderContext(); }
}
window.addEventListener('workspace-study-action', (event) => {
  const data = (event as CustomEvent).detail;
  if (data.phase === 'start') {
    actionNotice.textContent = '';
    const input = data.input; input.sourceSize = input.source ? sourceDimensions.get(input.source.id) ?? null : null;
    const target = data.args?.object ?? data.args?.a;
    run = capturedRun(data.run, input, data.capability, target ? `Outline ${target}` : data.capability === 'cells' ? input.source?.kind === 'still' ? 'Whole original image' : 'Current camera field' : 'Scope shown in the tool form');run.outputTab=openTab()??'Unknown tab';
    runPills.start(run,String(data.capability),bridge.runName(String(data.capability),data.use)); // beside the earlier runs' pills
    handRuns.set(run.id,{run,name:bridge.runName(String(data.capability),data.use)});
  }
  else if (run && run.id === data.run && !['completed', 'failed', 'stopped'].includes(run.status)) { run.status = data.phase === 'unknown' ? 'unknown' : data.phase === 'error' || data.reply?.ok === false ? 'failed' : data.reply?.stopped ? 'stopped' : data.reply?.stopping ? 'stopping' : data.reply?.backgroundJob ? 'queued' : 'completed';
    if (data.reply?.backgroundJob) run.backgroundJob = data.reply.backgroundJob;
    const said = String(data.error || data.reply?.error || data.reply?.said || data.reply?.summary || 'Done');
    // Where the result belongs comes first: the Project and tab the run captured, whatever is open now.
    run.message = run.status === 'completed' ? `Done in ${run.input.title}${run.outputTab ? ` › ${run.outputTab}` : ''}. ${said}` : said; void refreshResults(); }
  renderContext(); renderTabs();
});
window.addEventListener('workspace-study-delay',event=>{const data=(event as CustomEvent).detail;if(run&&run.id===data.run){run.message=`Input captured: ${run.input.source?.title ?? 'the camera frame'}, for ${run.input.title}. Controlled ${data.milliseconds/1000} s test delay before the real run.`;renderContext();}});
window.addEventListener('workspace-study-settled', event => {
  const data = (event as CustomEvent).detail;
  if (run && run.id === data.run) { run.status = data.outcome; run.message = data.text; renderContext(); void refreshResults(); }
  else { const kept = runPills.get(data.run) ?? handRuns.get(data.run)?.run; if (kept) { kept.status = data.outcome; kept.message = data.text; runPills.render(); } } // an earlier run (its pill, or a hidden one's run)
});
window.addEventListener('keydown', event => {
  if (event.altKey && event.code === 'KeyA' && preferences.layout !== 'baseline' && !Array.from(document.querySelectorAll('dialog[open]')).some(dialog => dialog.getBoundingClientRect().width > 0)) { event.preventDefault(); press(() => openRightTab('agent')); return; }
  if (event.key !== 'Escape' || event.defaultPrevented || Array.from(document.querySelectorAll('dialog[open]')).some(dialog => dialog.getBoundingClientRect().width > 0) || preferences.layout === 'baseline') return;
  const active = document.activeElement;
  if (active instanceof HTMLElement && active.matches('input, textarea, select, [contenteditable=true]')) return;
  // A popup open from the focused control (the model chip's models popover) takes this Escape: it closes alone and
  // focus goes back to its control (panels.ts), the Assistant stays.
  if (escapeIsPopups(active, () => !!document.querySelector('form#models:not([hidden])[data-opener]'))) return;
  if(active && reader.root.contains(active) && readerShown){event.preventDefault();event.stopPropagation();press(()=>closeReader());}
  else if(active && dataWindow.contains(active) && dataOpen){event.preventDefault();event.stopPropagation();press(()=>setDataOpen(false));}
  else if (active && rightPanel.contains(active) && preferences.inspector) { event.preventDefault(); event.stopPropagation(); press(() => setInspector(false)); }
  else if (active && drawer.contains(active) && drawerOpen) { event.preventDefault(); event.stopPropagation(); press(() => setDrawer(false)); }
}, { capture: true });
bridge.subscribe(next => {
  const previous = snapshot;
  snapshot = next;
  if (next.session !== previous.session) {
    resourceReads.cancel();studyReads.cancel();
    // Retire the inventory owner with its contents before any view renders. An A→B→A switch
    // can otherwise mistake A's emptied cache for a completed read while B is still pending.
    entries = []; resourceSession = ''; resourceRevision = ''; resultsFailedSession = '';
    studyReadGeneration++; studiesLoadedSession = ''; knownStudies = []; studiesNote = ''; studiesAvailable = false;
  }
  if(next.session===previous.session&&next.ready)workspaceViews.observeInput();
  renderRailBadges();
  renderResearchStudy();
  researchTabs.update(next.session,bridge.researchers(),!researchStage.hidden&&location.hash.startsWith('#research/')?decodeURIComponent(location.hash.slice(10).split('?')[0]):'');renderBoard();
  adoptStartedResearcher();
  if(next.session!==previous.session){draftStart=null;closeResearchStage();workspaceViews.pause();studyArtifacts().clearDocuments();} // a researcher's study belongs to its Project
  // The server's current Project opens as before (its empty Stage is the first screen's own overview); the Welcome
  // launcher stands in only while no Project Tab is open on a fresh profile. Counts arrive after the list, so a
  // first run cannot be told at the first ready moment.
  if(!initialized && next.ready && next.session){initialized=true;tabs=activateTab(tabs??emptyTabs(),next.session);persistTabs();void workspaceViews.load(next.session);const saved=sessionUI[next.session];if(saved)bridge.restoreDraft(saved.presentation);}
  if(initialized && !switching && tabs?.active && next.session!==previous.session && next.session!==tabs.active){tabs=activateTab(tabs,next.session);persistTabs();void workspaceViews.load(next.session);}
  if(next.title!==previous.title)renderTabs();
  if (next.source?.kind === 'still') {
    const asset = next.source;
    next.sourceSize = sourceDimensions.get(asset.id) ?? null;
    if (!sourceDimensions.has(asset.id)) {
      sourceDimensions.set(asset.id, null);
      const image = new Image();
      image.onload = () => { sourceDimensions.set(asset.id, { width: image.naturalWidth, height: image.naturalHeight }); };
      image.src = `/api/library/${encodeURIComponent(asset.id)}/file`;
    }
  }
  const key = JSON.stringify([next.session, next.title, next.sources, next.projects, next.projectsRoot]);
  if (next.session !== previous.session) { madeMemo.clear(); sourceFilter = null; makerFilter = null; void refreshResults(); }
  // Data's Pictures and recordings are the Project's inputs, and "On this Tab" marks the one on the Stage: drawn again
  // when either changes.
  const dataKey = JSON.stringify([next.sources.map(asset => [asset.id, asset.title, asset.thumb ?? '', asset.seconds]), next.onStage]);
  if (dataKey !== lastDataKey) { lastDataKey = dataKey; if (dataOpen && next.session === previous.session) renderResults(); }
  if (key !== lastSnapshotKey) { lastSnapshotKey = key; renderSessionBrowser(); renderTabs(); if (!switching && (section === 'inputs' || section === 'projects' || section === 'library')) showSection(section); }
  if (stageKey(next.onStage) !== lastSource) { lastSource = stageKey(next.onStage); if (!switching && section === 'inputs') showSection(section); }
  refreshLiveCameraStatus();
  if (activePreview && surfaces.dialog.open && preview.isConnected) preview.querySelector<HTMLButtonElement>('.study-primary')?.toggleAttribute('disabled', busy());
  renderContext();
});
matchMedia('(max-width: 1199px)').addEventListener('change', () => applyLayout());
setInterval(() => void refreshResults(), 10_000);
// Results arrive on the server's events (main.ts relays them as hooke-results-changed), debounced; the poll above is the fallback.
let resultsRefresh=0;window.addEventListener('hooke-results-changed',()=>{clearTimeout(resultsRefresh);resultsRefresh=window.setTimeout(()=>void refreshResults(),350);});
window.addEventListener('workspace-study-assistant',()=>setInspector(true));
window.addEventListener('workspace-study-section',event=>{const next=(event as CustomEvent).detail;showSection(next);if(['inputs','tools','research'].includes(next))setDrawer(true);
  // On › Change (a run page): focus lands on the Inputs drawer's first row, where the choice is made.
  if(next==='inputs')drawerBody.querySelector<HTMLElement>('.mm-row')?.focus({preventScroll:true});});
const saveOnExit=()=>{if(!switching&&initialized&&snapshot.ready)void rememberSession().catch(actionError);};
find('question').addEventListener('input',saveOnExit);
window.addEventListener('pagehide',saveOnExit);
let saving=false;setInterval(()=>{if(!saving&&!switching&&initialized&&snapshot.ready){saving=true;void rememberSession().catch(actionError).finally(()=>{saving=false;});}},1500);
window.addEventListener('workspace-study-research-detail',event=>openResearchStage((event as CustomEvent<HTMLElement>).detail));
// A researcher's Tab asks for what lives elsewhere (research.ts): Ask a new researcher opens a New researcher Tab; a step
// that went wrong opens Activity beside it. What it made and read opens in its own reader (research-workspace.ts).
window.addEventListener('hooke-research-new',()=>openDraftResearcher());
window.addEventListener('hooke-research-activity',()=>showActivity(true));
applyLayout(); showSection(section); renderContext(); void refreshResults(); window.dispatchEvent(new Event('hashchange'));


let lastViewport=`${innerWidth}:${innerHeight}`;
window.addEventListener('resize',()=>{const current=`${innerWidth}:${innerHeight}`;if(current===lastViewport)return;lastViewport=current;applyLayout();requestAnimationFrame(()=>tabStrip.querySelector<HTMLElement>('[aria-selected=true]')?.scrollIntoView({block:'nearest',inline:'nearest'}));});

window.addEventListener('workspace-tab-context',()=>renderContext());
window.addEventListener('scientific-ask-input',()=>{ renderContext(); setInspector(true); });
