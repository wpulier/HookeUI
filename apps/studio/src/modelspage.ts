import { sizeWords } from './size-words';
import { capabilityEntry, sourceWords as linkSource, type CapabilityEntry, type CapabilityCreation, type CapabilitySource } from './capability-entry';
import { readFlow, saveFlow, type CapabilityFlowContext } from './capability-flow';
import { parameterControl, parameterValues, parameterError } from './parameter-fields';
import type { ActionReply } from './actions-panel';
import type { MeasurementPin } from './measurements';
import { fijiWords, toolboxFrame, type FijiCounts } from './toolbox';
import { toolboxBand, type AddWay } from './keystrip';
/** The Models page (#models; P3 of docs/design/2026-10-12-the-open-catalog.md): every model in the catalog, in four
 * groups, each with why it is on or off as facts with their references, what it is used for, and the one thing that
 * turns it on. The rows are the server's (orchestrator/catalog_view.py, the capabilities wire's `models`); this module
 * only says them. The pure parts (groups, the chip, first run, a card's action, words) are tested in modelspage.test.ts.
 *
 * - Needs you: a current choice of yours uses it and it can't run yet.
 * - Ready: it can run here now.
 * - Available to add: nothing you chose needs it now; one step turns it on (a key, a download, an install).
 * - Unavailable here: not in this edition, or broken.
 */
import './modelspage.css';
import { spaceSection, type SpaceCommands } from './capabilityspace';
import { maintenanceNode, type MaintenanceCommands, type MaintenanceMemory } from './maintenance-status';
import { openAddKey, sourceWords, keyForm, type KeyForm } from './addkey';
import { downloadControl, type Downloader } from './capabilities';
import { needs as volumeNeeds, volumesSection, type VolumeCommands, type VolumeEntry } from './volumespanel';
import { newsSection, yourControl, type ShelfCommands } from './capabilityshelf';
import type { DraftReview, OwnComparison, OwnTrial, CapabilitiesWire, ChannelRow, DownloadRow, InstallRow, KeyFacts, ModelFact, ModelRow, ToolSetup } from './protocol';
import { cardKindWords, cardLook, faceState, firstStepWords, licenceName, type CardLook, type FaceState } from './model-status';
import { keyOf, MODEL_SECTIONS, modelSection, type ModelSection } from './run-spec';
import { litCell } from './lit-cell';
import { tabMark, type TabMark } from './tab-marks';
import { starterOffer } from './starter-models';
export { whereWords, firstStepWords } from './model-status';

export type Group = ModelRow['group'];
export type RetainedInput = { title: string; reference: { session: string; id: string; revision: string; component: string; kind?: never } | { kind: 'array'; session: string; id: string; revision: number; component: string }; compatibility?: { compatible: boolean; says: string } };
export const GROUPS: { id: Group; title: string; says: string }[] = [
  { id: 'needs-you', title: 'Needs you', says: 'Your current choices use these, and they can’t run yet.' },
  { id: 'ready', title: 'Ready', says: 'Tools whose current checks passed, and downloaded data.' },
  { id: 'available', title: 'Setup and checks', says: 'Download, prepare, check or review these tools. Each card shows what remains.' },
  { id: 'unavailable', title: 'Unavailable here', says: 'Not in this edition, or broken when the app checked it.' },
];

/** The rows of each group, in the server's order. */
export function grouped(rows: ModelRow[]): Record<Group, ModelRow[]> {
  const out: Record<Group, ModelRow[]> = { 'needs-you': [], ready: [], available: [], unavailable: [] };
  for (const row of rows) out[row.group].push(row);
  return out;
}

/** The job it does for you now, for the chip and the live view's note: the first current choice that uses it. */
export function chosenFor(row: ModelRow): string {
  return row.usedFor.find((u) => u.current && !u.fixed)?.title ?? row.usedFor.find((u) => u.current)?.title ?? '';
}

/** "Seeing needs your Google Gemini API key" (the readiness word made a sentence about the job). */
export function needsWords(row: ModelRow): string {
  const job = chosenFor(row) || row.name;
  const word = row.word.replace(/^Needs /, 'needs ');
  return word.startsWith('needs ') ? `${job} ${word}` : `${job}: ${word}`;
}

/** The header's chip: green "All set" only when every model your current choices use can run (the review's correction 1:
 * your selections, not every model); otherwise how many need you, the first ones named. */
export function chipState(rows: ModelRow[] | undefined): { tone: 'ok' | 'needs' | 'unknown'; words: string; title: string } {
  if (!rows) return { tone: 'unknown', words: 'Models', title: 'Every model: what runs here and how to turn each on' };
  const needs = rows.filter((r) => r.group === 'needs-you');
  if (!needs.length) return { tone: 'ok', words: 'All set', title: 'Every model your choices use can run here. Open to see every model.' };
  const words = needs.length === 1 ? '1 thing needs you' : `${needs.length} things need you`;
  const named = needs.slice(0, 3).map(needsWords);
  return { tone: 'needs', words, title: `${named.join('. ')}${needs.length > 3 ? '. …' : ''}. Open to see how.` };
}

/** The one thing a card offers, from its next step: Add key, Download, the licence page, how to install, or nothing. */
export type Action =
  | { kind: 'key'; label: string; service: string }
  | { kind: 'download'; label: string; model: string }
  | { kind: 'licence'; label: string; url: string }
  | { kind: 'install'; label: string; how: string }
  | { kind: 'setup'; label: string; model: string } // an install the app does itself: its own Python (envs.py)
  | { kind: 'activate'; label: string; model: string }
  | { kind: 'trust'; label: string; channel: string } // a community channel's code: allowed once (channels.py)
  | { kind: 'none' };
export function action(row: ModelRow): Action {
  const next = row.status === 'unavailable' ? null : row.status === 'ready'
    ? row.newKind ? row.face?.action : null : row.next;
  if (!next) return { kind: 'none' };
  if (next.kind === 'key' && next.service) return { kind: 'key', label: 'Add key', service: next.service };
  if (next.kind === 'download' && next.model) return { kind: 'download', label: next.says, model: next.model };
  if (next.kind === 'licence' && next.url) return { kind: 'licence', label: 'Accept licence', url: next.url };
  if (next.kind === 'install' && next.model) return { kind: 'setup', label: next.says, model: next.model };
  if (next.kind === 'install' && next.how) return { kind: 'install', label: next.says, how: next.how };
  if (next.kind === 'activate' && next.model) return { kind: 'activate', label: 'Turn on', model: next.model };
  if (next.kind === 'trust' && row.channel) return { kind: 'trust', label: next.says, channel: row.channel };
  return { kind: 'none' };
}

/** "Outlining (your choice) · Cells · Find all like this (in use)": what it is used for, the current ones said. */
export function usedForWords(row: ModelRow): string {
  return row.usedFor.map((u) => u.current ? `${u.title} (${u.fixed ? 'in use' : 'your choice'})` : u.title).join(' · ');
}

/** ✓, ✗ or · for a fact. */
export function mark(f: ModelFact): '✓' | '✗' | '·' {
  return f.ok === true ? '✓' : f.ok === false ? '✗' : '·';
}

/** "3 need you · 21 ready · 2 not checked · 6 can be set up": the sections' own counts (run-spec.ts `modelSection`, the
 * same ones Tools By model says), so the line, the sections and Tools never name the same rows three ways. */
export function summary(rows: ModelRow[]): string {
  const n = (section: ModelSection) => rows.filter((r) => modelSection(r) === section).length;
  const needs = n('needs'), ready = n('ready'), unchecked = n('unchecked'), setup = n('setup'), away = n('away');
  return [needs ? `${needs} need${needs === 1 ? 's' : ''} you` : '', ready ? `${ready} ready` : 'none ready yet',
    unchecked ? `${unchecked} not checked` : '', setup ? `${setup} can be set up` : '', away ? `${away} not on this Mac` : ''].filter(Boolean).join(' · ');
}

/** The downloads your current choices are waiting on, for "Get what your choices need". */
export function neededDownloads(rows: ModelRow[]): { model: string; name: string; says: string }[] {
  const seen = new Set<string>();
  return rows.filter((r) => r.group === 'needs-you' && r.next?.kind === 'download' && r.next.model)
    .filter((r) => !seen.has(r.next!.model!) && !!seen.add(r.next!.model!))
    .map((r) => ({ model: r.next!.model!, name: r.name, says: r.next!.says }));
}

/** The note over the live view when the model your Seeing choice uses can't run (it can't see without it). */
export function liveNote(rows: ModelRow[] | undefined): { row: ModelRow; words: string } | null {
  const row = rows?.find((r) => r.group === 'needs-you' && r.usedFor.some((u) => u.role === 'vision' && u.current));
  return row ? { row, words: needsWords(row) } : null;
}

// --- the sheet before setup (C2 of docs/design/2026-10-13-capabilities-for-everyone.md) ------------------------------
/** A Turn on's plan, as a job's card or a tool's setup carries it (capability_shelf.plan). */
export type SetupPlan = NonNullable<ToolSetup['plan']>;
const SHEET_KINDS = new Set(['download', 'install', 'key', 'licence', 'trust']);
export const BIG_DOWNLOAD = 1e9; // a download over this is named as large before anything starts

/** Whether Turn on opens the sheet first: a step that downloads, installs, or needs your key, licence or Allow. A check
 * (or Check again) alone starts at once, as before. */
export function needsSheet(plan: SetupPlan, recheck = false): boolean {
  return !recheck && plan.steps.some((s) => SHEET_KINDS.has(s.kind));
}

/** Everything the sheet says, from the wire's facts only: nothing it can't read is guessed (a size not known is said so,
 * a time is a range by size, never a number of minutes). `fits` false: there is not room, and Turn on is not offered. */
export interface SheetFacts {
  steps: { says: string; whose: string }[];
  download: string; big: boolean; bytes: number;
  space: string; fits: boolean | null;
  time: string[];
  /** The licence in full (Setup details) and its short name (the sheet's licence line, model-status.ts `licenceName`). */
  licence: string; licenceShort: string; licenceLinks: { says: string; url: string }[];
  where: string; keys: string[]; cost: string; background: string;
}

/** How long a download of this size takes, as a range: "a few minutes on a typical connection". */
export function downloadTime(bytes: number): string {
  const range = bytes < 1e8 ? 'about a minute or less' : bytes < BIG_DOWNLOAD ? 'a few minutes' : bytes < 5e9 ? 'ten minutes or more'
    : 'half an hour or more';
  return `${range} on a typical connection`;
}

const TIER_WORDS: Record<string, string> = { terms: ' (its own terms: read them before you use it)', noncommercial: ' (non-commercial use only)' };

export function sheetFacts(plan: SetupPlan, row: ModelRow, models: ModelRow[], keys: Record<string, KeyFacts> | undefined,
  disk: { freeBytes?: number; spareBytes?: number } | undefined): SheetFacts {
  const downloads = plan.steps.filter((s) => s.kind === 'download');
  // An install that only turns a kept, proven version back on (after Turn off) installs nothing: no install size or
  // minutes are said for it (Window C, 2026-10-14: "Installing: usually several minutes" before a Turn on that took none).
  const installs = plan.steps.filter((s) => s.kind === 'install' && !s.onAgain && !s.recheck);
  const onAgain = plan.steps.some((s) => s.kind === 'install' && s.onAgain);
  const sized = downloads.map((s) => s.bytes ?? (models.find((m) => m.id === s.model)?.size || undefined));
  const bytes = sized.reduce<number>((sum, b) => sum + (b ?? 0), 0);
  const unknown = sized.some((b) => b === undefined);
  const big = bytes > BIG_DOWNLOAD;
  const download = !downloads.length ? onAgain && !installs.length ? 'Nothing to download or install: its installed version is turned back on.'
    : installs.length ? 'Setup reuses prepared software when possible. Missing dependencies may need downloading; their size isn’t known here.'
    : plan.steps.some((s) => s.kind === 'trust') ? 'After permission, the app checks which software and model files need preparing. Downloads may still be required.'
    : 'Nothing to download.' : !bytes ? 'It downloads files whose size isn’t known here.'
    : `It downloads ${sizeWords(bytes)}${big ? ': a large download, over 1 GB' : ''}${unknown ? ', and more whose size isn’t known here' : ''}.${installs.length ? ' Missing software dependencies may also need downloading; their size isn’t known here.' : ''}`;
  let space = '', fits: boolean | null = null;
  const free = disk?.freeBytes, spare = disk?.spareBytes ?? 0;
  if (downloads.length && typeof free !== 'number') space = 'The free space couldn’t be read here; the download checks it again before it starts.';
  else if (downloads.length && typeof free === 'number') {
    fits = bytes + spare <= free;
    space = fits
      ? `${sizeWords(free)} free on the disk models are kept on: it fits${spare ? `, with the ${sizeWords(spare)} the app keeps spare` : ''}.`
        + (unknown ? ' The rest is checked again before it starts.' : '')
      : `Not enough space: it needs ${sizeWords(bytes)}${spare ? ` and ${sizeWords(spare)} kept spare` : ''}, and the disk models are kept on has ${sizeWords(free)} free. Make room, then turn it on.`;
  } else if (installs.length && typeof free === 'number') {
    space = `Additional installation size isn’t known here; ${sizeWords(free)} free on the disk models are kept on.`;
  }
  if (downloads.length && installs.length && space) space += ' This covers model files only; the software installation may need additional space.';
  const time: string[] = [];
  if (bytes) time.push(`Downloading ${sizeWords(bytes)}: ${downloadTime(bytes)}.`);
  else if (downloads.length) time.push('Downloading: no estimate, as its size isn’t known here.');
  if (installs.length) time.push('If software needs installing, it can take several minutes depending on this Mac and the connection.');
  else if (onAgain) time.push('Turning it back on: quick, as nothing is installed again.');
  if (plan.steps.some((s) => s.kind === 'check' || s.recheck)) time.push('Then a functional check on this Mac.');
  const yours = plan.steps.filter((s) => s.by === 'you');
  if (yours.length) time.push(`It waits for you at: ${yours.map((s) => s.says).join('; ')}.`);
  if (yours.some((s) => s.kind === 'licence')) time.push('A licence’s approval is its publisher’s: it can take minutes or days.');
  const entry = models.find((m) => m.id === plan.model) ?? row;
  const licence = `${entry.licence || 'Its licence isn’t stated in the catalog'}${TIER_WORDS[entry.tier] ?? ''}.`;
  const licenceShort = entry.licence ? `Licence ${licenceName(entry.licence, entry.tier)}` : 'Licence not stated';
  const licenceLinks = plan.steps.filter((s) => s.kind === 'licence' && s.url).map((s) => ({ says: s.says, url: s.url! }));
  const said = new Set<string>();
  const keyLines: string[] = [];
  for (const service of [...plan.steps.filter((s) => s.kind === 'key').map((s) => s.service ?? ''), plan.service ?? '']) {
    if (!service || said.has(service)) continue;
    said.add(service);
    const key = Object.values(keys ?? {}).find((k) => k.service === service);
    if (!key) keyLines.push(`It needs your ${service} key, added here.`);
    else if (key.present) keyLines.push(`It uses your ${key.short} key, ${sourceWords(key)}.`);
    else keyLines.push(`Your ${key.short} key isn’t added yet: you add it here, where the app tests it before saving it (never in the chat).`);
  }
  return {
    steps: plan.steps.map((s) => ({ says: s.says, whose: s.by === 'app' ? 'the app does this' : 'needs you' })),
    download, big, bytes, space, fits, time, licence, licenceShort, licenceLinks,
    where: plan.privacy || `Runs ${entry.where}`, keys: keyLines, cost: plan.cost || 'Not recorded in the catalog',
    background: 'Once you press Turn on it carries on in the background, also after this sheet closes; Stop on its card cancels what this press started.',
  };
}

// --- the page ---------------------------------------------------------------------------------------------------------
function el<K extends keyof HTMLElementTagNameMap>(tag: K, attrs: Record<string, string | boolean> = {},
  ...children: (Node | string | null | undefined | false)[]): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === false) continue;
    if (k === 'class') node.className = String(v);
    else node.setAttribute(k, v === true ? '' : String(v));
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

export interface CapabilityWorkspace { id: string; folder: string; instructions: string; candidate: string }

export interface ModelsPageCommands {
  space?: SpaceCommands;
  download: Downloader;
  reveal: (model: string) => Promise<unknown>;
  add?: (source: string, name?: string, trial?: string) => Promise<{ says?: string; model?: string; kind?: string; why?: string }>;
  activate?: (model: string) => Promise<unknown>;
  deactivate?: (model: string) => Promise<unknown>;
  install?: (model: string) => Promise<unknown>; // make its own Python from its lockfile (model-install)
  prepare?: (model: string) => Promise<unknown>; // prepare candidate dependencies without activating it
  installCancel?: (model: string) => Promise<unknown>; // Stop an install (model-install-cancel): its build or proof ends
  uninstall?: (model: string) => Promise<unknown>; // Remove its installed versions (model-uninstall); its entry stays
  fijiInventory?: () => Promise<FijiInventory>; // every Fiji extension listed (GET /api/catalog/inventories/fiji)
  fijiAdd?: (page: string) => Promise<unknown>; // turn on its update site in Fiji's next generation
  fijiRollback?: () => Promise<unknown>; // the previous Fiji generation in use again
  upload?: (file: File, progress: (share: number) => void, trial?: string) => Promise<{ ok: boolean; says: string; model?: string; kind?: string }>;
  observe?: (model?: string) => Promise<unknown>; // ask the outside world again (a licence's access, a key: observed.py)
  confirm?: (model: string) => Promise<unknown>; // the user's word that they accepted a licence no probe can ask about
  catalogUpdate?: () => Promise<unknown>; // the reviewed catalog at its newest commit (channels.py)
  channelAdd?: (url: string, commit?: string) => Promise<{ says?: string }>; // a community channel, pinned
  channelTrust?: (name: string, allowed: boolean) => Promise<unknown>; // allow (or no longer allow) its code
  channelRemove?: (name: string) => Promise<unknown>;
  channelUpdate?: (name: string) => Promise<{ says?: string }>; // a community channel at its newest commit, said
  volumes?: VolumeCommands & { list: () => Promise<VolumeEntry[]> }; // brainreg's volumes and jobs (volumespanel.ts)
  turnOnModel?: (model: string, trial?: string) => Promise<unknown>; // trial: the picture Set up for comparison compares it on
  publishPrepare?: (model: string, destination?: 'review' | 'community') => Promise<{ says: string; package: string }>;
  build?: (link: string, trial?: string, description?: string, session?: string) => Promise<{ says?: string; draft?: string }>; // C4: have an AI draft an adapter for a link no template runs
  useDraft?: (draft: string, reviewed: string) => Promise<{ says?: string; model?: string }>; // a draft that ran, as reviewed, becomes one of yours
  ownPicture?: (file: File, job: string) => Promise<{ id: string }>;
  compareOwn?: (trial: string, model?: string, draft?: string) => Promise<OwnComparison>;
  useOwn?: (model: string, trial: string) => Promise<{ says: string }>;
  communityDraft?: (draft: string) => Promise<{ text: string; says: string }>;
  publishSubmit?: (model: string, pkg: string, signoff: boolean) => Promise<{ says: string }>; // step two, on sign-off
  githubStatus?: () => Promise<{ setUp: boolean; signedIn: boolean; says: string }>; // one-click sign-in (github_signin.py)
  reviewBundle?: (model: string, session: string, accepted?: { generation: string; digest?: string }) => Promise<import('./protocol').BundleReview>;
  importCapability?: (file: File, session: string) => Promise<{ model: string; says: string }>;
  workspace?: {
    create: (request: Extract<CapabilityCreation, { method: 'coder' }>, session: string) => Promise<CapabilityWorkspace>;
    read: (id: string, session: string) => Promise<CapabilityWorkspace>;
    reveal: (id: string, session: string) => Promise<unknown>;
  };
  session?: () => string;
  selectCapability?: (selection: { job: string; option: string; session: string; current: string; recipe: string }) => Promise<{ ok?: boolean; error?: string | null }>; // the studio's currently displayed session
  inputResources?: (session: string, capability?: string) => Promise<{ inputs: RetainedInput[] }>;
  tryKind?: (model: string, input: { session: string; input: string; volume?: string; resource?: RetainedInput['reference']; params?: Record<string, unknown> }) => Promise<Partial<ActionReply>>; // the view or an exact retained original
  openTable?: (id: string, pin?: MeasurementPin) => void;
  chooseInput?: () => void;
  setVersion?: (model: string, generation: string | null) => Promise<{ says?: string }>; // Go back, in one press (C7)
  maintenance?: MaintenanceCommands; // its updates under the policy you record (maintenance-status.ts)
  report?: (model: string) => Promise<{ title: string; text: string; url: string; says: string }>; // Report it (report.py)
  shelf?: Omit<ShelfCommands, 'open' | 'confirm' | 'trust'>; // what it can do by job, and what's new (capabilityshelf.ts)
  /** The tasks a model serves in the workspace, in Use it here's order (actions-panel.ts `served`): Use it here and Try on
   * … show only where they lead to a run page. Absent outside the workspace. */
  served?: (model: string) => { task: string; title: string }[];
  /** What is on the Stage, by its name ("Human cells"), for "Try it on …"; '' when nothing is. */
  specimen?: () => string;

}

/** Share it as a file (share.py, C5): one small file anyone can open in their HookeUI (never its weights), with
 * the name you give said where it is opened. No account. */
function shareFold(row: ModelRow, commands: ModelsPageCommands): HTMLElement {
  const name = el('input', { type: 'text', class: 'mm-control model-share-name', placeholder: 'Your name (said where it is opened)',
    'aria-label': 'Your name, said where it is opened', maxlength: '80' }) as HTMLInputElement;
  const link = el('a', { class: 'mm-control model-share-link', download: '' }, 'Download the share file');
  const href = () => { link.href = `/api/catalog/share/${encodeURIComponent(row.id).replace(/%2F/g, '/')}?by=${encodeURIComponent(name.value.trim())}`; };
  name.addEventListener('input', href);
  href();
  const fold = el('div', { class: 'model-share', 'data-model-editor': `share:${row.id}:${row.revision}:${row.version}` }, el('strong', {}, 'Share it'),
    el('p', {}, 'One small file to send any way you like: whoever opens it in HookeUI gets this capability, '
      + 'allows its code once, and turns it on. Its weights are fetched from where they are pinned, never sent.'), name, link);
  if (row.mine && commands.publishPrepare && commands.publishSubmit) {
    // Share with everyone: the reviewed catalog (publish.py). Prepared here first, read, then sent only on your sign-off.
    const said = el('pre', { class: 'model-share-said', hidden: true });
    const prepare = el('button', { type: 'button', class: 'mm-control' }, 'Prepare it for everyone');
    const community = el('button', { type: 'button', class: 'mm-control' }, 'Prepare a community link');
    const sign = el('input', { type: 'checkbox', class: 'model-share-sign' }) as HTMLInputElement;
    const send = el('button', { type: 'button', class: 'mm-control mm-primary', disabled: true }, 'Send it for review');
    const signRow = el('label', { class: 'model-share-signrow', hidden: true }, sign,
      ' I certify this contribution (DCO 1.1, above); my Git name and email become public');
    let pkg = '';
    prepare.addEventListener('click', () => {
      community.disabled = prepare.disabled = true; sign.checked = false; send.disabled = true; pkg = '';
      send.textContent = 'Send it for review';
      void commands.publishPrepare!(row.id).then((r) => { pkg = r.package; said.textContent = r.says; said.hidden = false; signRow.hidden = !pkg; })
        .catch((error: unknown) => { said.textContent = error instanceof Error ? error.message : String(error); said.hidden = false; })
        .finally(() => { community.disabled = prepare.disabled = false; });
    });
    community.addEventListener('click', () => {
      community.disabled = prepare.disabled = true; sign.checked = false; send.disabled = true; pkg = '';
      void commands.publishPrepare!(row.id, 'community').then(r => {
        pkg = r.package; said.textContent = r.says; said.hidden = false; signRow.hidden = !pkg;
        send.textContent = 'Publish my community link';
      }).catch(error => { said.textContent = String(error); said.hidden = false; })
        .finally(() => { community.disabled = prepare.disabled = false; });
    });
    sign.addEventListener('change', () => { send.disabled = !sign.checked || !pkg; });
    send.addEventListener('click', () => {
      send.disabled = true;
      void commands.publishSubmit!(row.id, pkg, sign.checked).then((r) => { said.textContent = r.says; })
        .catch((error: unknown) => { said.textContent = error instanceof Error ? error.message : String(error); send.disabled = false; });
    });
    // What sending needs today, said before anything is pressed: Git and gh signed in until one-click sign-in is set up.
    const needs = el('p', { class: 'model-share-needs', hidden: true });
    void commands.githubStatus?.().then((s) => { needs.textContent = s.says; needs.hidden = s.signedIn; }).catch(() => undefined);
    fold.append(el('strong', {}, 'Share it with everyone'),
      el('p', {}, 'Sent to the reviewed catalog as a pull request: once reviewed and merged, everyone sees it in New for you. '
        + 'Or publish a pinned link in your own public community catalog immediately, with no official review. '
        + 'Nothing leaves this Mac until you read what is prepared and send it.'), needs, prepare, community, said, signRow, send);
  }
  return fold;
}

/** Report it (report.py, C7): the facts prepared here to read and copy, and the page to post them on. Nothing is sent. */
function reportFold(row: ModelRow, commands: ModelsPageCommands): HTMLElement {
  const said = el('pre', { class: 'model-share-said', hidden: true });
  const note = el('p', { hidden: true });
  const copy = el('button', { type: 'button', class: 'mm-control', hidden: true }, 'Copy it') as HTMLButtonElement;
  const open = el('a', { class: 'mm-control model-share-link', target: '_blank', rel: 'noopener', hidden: true }, 'Open its page');
  const prepare = el('button', { type: 'button', class: 'mm-control' }, 'Report a problem') as HTMLButtonElement;
  prepare.addEventListener('click', () => {
    prepare.disabled = true;
    void commands.report!(row.id).then((r) => {
      said.textContent = r.text; said.hidden = false; note.textContent = r.says; note.hidden = false; copy.hidden = false;
      if (r.url) { (open as HTMLAnchorElement).href = `${r.url}?title=${encodeURIComponent(r.title)}`; open.hidden = false; }
    }).catch((error: unknown) => { note.textContent = error instanceof Error ? error.message : String(error); note.hidden = false; })
      .finally(() => { prepare.disabled = false; });
  });
  copy.addEventListener('click', () => { void navigator.clipboard?.writeText(said.textContent ?? '').then(() => { copy.textContent = 'Copied'; }); });
  return el('div', { class: 'model-share', 'data-model-editor': `report:${row.id}:${row.revision}:${row.version}` }, el('strong', {}, 'Report a problem'),
    el('p', {}, 'The facts of what went wrong, to read and copy: never your pictures, projects, keys or folders. Nothing is sent.'),
    prepare, note, said, copy, open);
}

/** It is on and can be turned off: one of yours (its "on" fact), or any entry with its own adapter running as a choice
 * (yours, the reviewed catalog's or a community channel's: C7 Window B, finding 5). */
export function isOn(row: ModelRow): boolean {
  return (row.mine && row.facts.some((f) => f.kind === 'on' && f.ok === true)) || row.env?.on === true;
}

/** A model file dropped on the page, streamed to the app (POST /api/catalog/mine-file, the studio only), with progress. */
export function uploadModel(file: File, progress: (share: number) => void, trial?: string): Promise<{ ok: boolean; says: string; model?: string; kind?: string }> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', `/api/catalog/mine-file?name=${encodeURIComponent(file.name)}${trial ? `&trial=${encodeURIComponent(trial)}` : ''}`);
    xhr.setRequestHeader('X-MicroscopeMind', 'add-model');
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) progress(e.loaded / e.total); };
    xhr.onload = () => {
      try { resolve(JSON.parse(xhr.responseText)); } catch { resolve({ ok: false, says: `the app answered ${xhr.status}` }); }
    };
    xhr.onerror = () => resolve({ ok: false, says: 'the app could not be reached' });
    xhr.send(file);
  });
}

/** One Fiji extension as the inventory lists it (orchestrator/inventories.py, GET /api/catalog/inventories/fiji). */
export interface FijiExtension {
  name: string; page: string; categories: string[]; link: string;
  route: { kind: 'ships-with-fiji' | 'update-site' | 'choose' | 'none-known'; site?: string; evidence?: string; why?: string };
  how: string; installed: string; usable: string;
}
export interface FijiInventory { extensions: FijiExtension[]; summary: FijiCounts; why?: string }

/** The rows a search and a category keep: by name or category, every row when both are empty. */
export function fijiFilter(rows: FijiExtension[], query: string, category: string): FijiExtension[] {
  const q = query.trim().toLowerCase();
  return rows.filter((r) => (!category || r.categories.includes(category))
    && (!q || r.name.toLowerCase().includes(q) || r.categories.some((c) => c.toLowerCase().includes(q))));
}

/** `#toolbox/fiji-extensions`: the Toolbox's Fiji section with its list of every extension open (not the Fiji card,
 * `#toolbox/fiji`). */
export const FIJI_EXTENSIONS = 'fiji-extensions';

/** A fact that holds, said as what is true now on a Ready sheet (the mock's added): plain words for files, software, a
 * check, a key and a licence; any other fact in the server's own words. */
function doneWords(fact: ModelFact): string {
  switch (fact.kind) {
    case 'files': return 'Its files are on this Mac';
    case 'code': return 'Its software is installed';
    case 'check': return 'Checked on this Mac';
    case 'key': return 'Your key is added';
    case 'licence': return 'Its licence is accepted';
    default: return fact.says;
  }
}

/** Whether Details takes the grid's place rather than standing beside it (workspace-feel.css: under 1100 px). */
function detailsReplaceGrid(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(max-width: 1100px)').matches;
}

export class ModelsPage {
  private readonly root: HTMLElement;
  private readonly commands: ModelsPageCommands;
  private channelSaid = ''; // the last channel action's words (channelsSection), kept across the page's redraws
  private volumes: VolumeEntry[] = []; // the session's volumes and registrations (the index's), read at most every 2 s
  private volumesTimer = 0; // a read of them waiting for the 2 s to pass, or for a running job's next state
  private volumesRead = 0;
  private volumesSaid = '';
  private volumesError = '';
  private volumeSession = '';
  private readonly volumeChoices = new Map<string, string>();
  private wire: CapabilitiesWire | null = null;
  private selected: string | null = null;
  private unavailableOpen = false;
  private advancedOpen = false;
  private readonly setupMessages = new Map<string, string>();
  /** A card's own step that failed (a download the server refused), said on its facts line until the next press. */
  private readonly cardMessages = new Map<string, string>();
  private readonly setupPending = new Set<string>();
  private readonly confirmingRemove = new Set<string>(); // Remove asked, waiting for its confirm (kept across renders)
  private readonly removalPending = new Set<string>();
  private readonly removalMessages = new Map<string, string>();
  private readonly optionFolds = new Set<string>();
  // The sheet before setup, open for one tool's setup (its card's, or its details'); `focus` once, as it opens. `task`: the
  // task whose run page's step opened it (show), where its Use it here goes back to.
  private sheet: (CapabilityFlowContext & { detailed: boolean; focus: boolean; detailsOpen?: boolean; choose?: boolean; task?: string }) | null = null;
  private entry: CapabilityEntry | null = null;
  private workspaceResult: CapabilityWorkspace | null = null;
  private workspaceReading = false;
  private restoredRoute = false;
  /** The add route last shown (#models/add…), so the workspace's second dispatch of its initial route opens it once; reset
   * when the page hides, so Tools' Add model or tool opens the three choices every time it is pressed. */
  private shownAddRoute: string | null = null;
  /** The task whose run page's step is opening #models/<id>/setup (`openedFrom`, said by the run page just before it
   * navigates), '' otherwise: show gives it to the sheet, whose Use it here goes back to that task's run page. */
  private openerTask = '';
  /** The setups whose Ready heading has played its settle (once, as it turns ready; never again on a redraw). */
  private readonly readySettled = new Set<string>();
  /** The entries whose "More about this model" is open in Details, kept across redraws. */
  private readonly moreOpen = new Set<string>();
  /** Where the grid was scrolled when Details took its place (under 1100 px), restored when Details closes. */
  private gridScroll = 0;
  /** The control that opened Add model or tool, focused again when it closes (closeSheet). */
  private addOpener: HTMLElement | null = null;
  private retainedFlow: CapabilityFlowContext | null = null;
  private readonly entryPending = new WeakSet<CapabilityFlowContext>();
  private readonly entryFailures = new WeakMap<CapabilityFlowContext, string>();
  private readonly bundleReviews = new Map<string, import('./protocol').BundleReview>();
  private readonly comparisonPending = new Set<string>();
  private readonly draftPending = new Set<string>();
  private readonly draftMessages = new Map<string, string>();
  private readonly comparisonMessages = new Map<string, string>();
  private entryMessage = '';
  private inlineKey: { identity: string; form: KeyForm } | null = null;
  private ownCandidate: { link: string; model?: string; kind?: string; trial?: string; job?: string } | null = null;
  private ownBusy = false;
  private ownSaid: { words: string; ok: boolean } | null = null; // Add your own's last word, kept across updates
  private readonly maintenanceMemory = new Map<string, MaintenanceMemory>(); // each Updates: open, and its last reply
  private fiji: FijiInventory | null = null; // the Fiji extensions, asked for when their section first opens
  private fijiOpen = false; // the full extension list's fold
  private fijiAsked = false;
  private fijiFailed = false; // the last read failed: said with Try again, never "Reading the list…" for ever
  private space: HTMLElement | null = null;
  /** The small-models offer, one row at the top until answered (starter-models.ts): one element, kept across redraws. */
  private starter: HTMLElement | null = null;
  private fijiQuery = '';
  private fijiCategory = '';
  private fijiDone = 0;

  constructor(root: HTMLElement, commands: ModelsPageCommands) {
    this.root = root;
    this.commands = commands;
    const restored = readFlow();
    if (restored) { this.sheet = { ...restored, detailed: false, focus: true }; this.restoredRoute = true; }
    // Escape closes the innermost layer only (a setup sheet, then Details): its default is prevented too, so the Models
    // dialog's own cancel never follows and closes the whole sheet (workspace-study-surfaces.ts, F-rail-1).
    root.addEventListener('keydown', (event) => {
      if (event.key === 'Escape' && this.sheet) { event.preventDefault(); event.stopPropagation(); this.closeSheet(); }
      else if (event.key === 'Escape' && this.selected) { event.preventDefault(); event.stopPropagation(); this.closeDetails(); }
    });
    // Add model or tool in the Models sheet's header (workspace-study-surfaces.ts): the three ways in, every time it is
    // pressed (a hash route would not fire twice).
    if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') window.addEventListener('hooke-models-add', () => { if (!this.root.hidden) this.addWay('start'); });
  }

  /** A run page's step is about to open #models/<id>/setup for `task` (actions-panel.ts doStep, through main.ts
   * openModels): the next show of a setup route gives the sheet that task, so its Use it here goes back to that run page. */
  openedFrom(task: string): void { this.openerTask = task; }

  update(wire: CapabilitiesWire): void {
    // A Fiji generation finished (an install with a site done): what is installed in Fiji is asked for again.
    const doneSites = (wire.setup?.installs ?? []).filter((r) => r.site && r.state === 'done').length;
    if (doneSites !== this.fijiDone) { this.fijiDone = doneSites; this.fiji = null; if (!this.root.hidden) this.readFiji(); }
    this.wire = wire;
    if (!this.root.hidden) this.render();
  }

  hide(): void { this.inlineKey?.form.dispose(); this.inlineKey = null; this.shownAddRoute = null; }

  /** Redraw Try when the originating session becomes known or changes. */
  sessionChanged(): void {
    if (!this.root.hidden && this.wire) this.render();
  }

  /** Shown (#models, or #models/<id> for one card: scrolled to and opened). What was asked of the outside world a while
   * ago is asked again as it opens, so the page says today's answer (an approval on Hugging Face shows by itself). */
  show(id: string | null): void {
    const resumed = this.restoredRoute; this.restoredRoute = false;
    void this.commands.observe?.().catch(() => undefined);
    this.readFiji();
    // #models/add: the three ways in (Tools' Add model or tool); #models/add-<way>: straight into one of them.
    const way = id === 'add' ? 'start' : id?.match(/^add-(find|add|create|link|file|build)$/)?.[1] as AddWay | undefined;
    if (id === FIJI_EXTENSIONS) { // Every extension, from Everything it can do's Fiji tile: the Fiji section, its list open
      this.fijiOpen = true;
      this.render();
      requestAnimationFrame(() => {
        const section = this.root.querySelector<HTMLElement>('.toolbox-fiji');
        section?.scrollIntoView({ block: 'start' });
        section?.querySelector('.models-fiji')?.querySelector<HTMLElement>('summary')?.focus({ preventScroll: true });
      });
      return;
    }
    // #models/<id>/setup (Tools' Set up, Activity's See progress): that model's setup sheet, open at once, one press.
    const wantsSetup = !!id && !way && id.endsWith('/setup');
    if (wantsSetup) id = id!.slice(0, -'/setup'.length);
    if (id && !way) this.selected = id;
    if (wantsSetup) {
      // Opened by a run page's step: that page's task, so the sheet's Use it here goes back there (its first task otherwise).
      // The same sheet shown again with no press (the workspace's second dispatch of its route) keeps the one it has.
      const opener = this.openerTask; this.openerTask = '';
      const row = this.wire?.models?.find(r => r.id === id);
      const setup = row?.setups?.find(s => this.wire?.setup?.turning?.[s.role]?.option === s.option)
        ?? row?.setups?.find(s => s.plan && !s.recheck) ?? row?.setups?.find(s => s.plan);
      if (row && setup && !row.execution) this.openSetup(row, setup, false, opener || (this.sheet?.model === row.id ? this.sheet.task : undefined));
    }
    this.render();
    const repeatedRoute = !!way && id === this.shownAddRoute;
    this.shownAddRoute = way ? id : null;
    if (way) { if (!resumed && !repeatedRoute) this.addWay(way); return; } // Add a capability, pressed on the other view
    if (id === 'space') requestAnimationFrame(() => this.space?.scrollIntoView({ block: 'start' }));
    if (id) requestAnimationFrame(() => {
      // A model's card, or a New-for-you item's (Tools' Card ↗ names either by its key).
      const card = this.root.querySelector<HTMLElement>(`[data-model="${CSS.escape(id)}"]`)
        ?? this.root.querySelector<HTMLElement>(`.news-item[data-key="${CSS.escape(id)}"]`);
      card?.scrollIntoView({ block: 'center' });
      card?.classList.add('model-card-asked');
      card?.focus({ preventScroll: true });
    });
  }

  private render(): void {
    const session = this.commands.session?.() ?? '';
    if (session !== this.volumeSession) {
      this.volumeSession = session; this.volumes = []; this.volumesRead = 0; this.volumesSaid = ''; this.volumesError = ''; this.volumeChoices.clear();
      if (this.volumesTimer) { window.clearTimeout(this.volumesTimer); this.volumesTimer = 0; }
    }
    const scroll = this.root.scrollTop;
    const sheetScroll = this.root.querySelector('.tool-setup-sheet-body')?.scrollTop ?? 0;
    // Polling refreshes facts and progress, while unfinished editors retain their DOM, handlers and drafts. A
    // volume editor's key includes its session/series facts, so a changed source cannot inherit another's choices.
    const editors = new Map([...this.root.querySelectorAll<HTMLElement>('[data-model-editor]')]
      .map((node) => [node.dataset.modelEditor!, node]));
    const focused = document.activeElement as HTMLElement | null;
    const focusedInSheet = !!focused?.closest('.tool-setup-sheet');
    const focusKey = focused?.dataset.toolboxFocus;
    const detailScroll = this.root.querySelector<HTMLElement>('.tool-details')?.scrollTop ?? 0;
    const text = focused as HTMLInputElement | null;
    const selection = typeof text?.selectionStart === 'number'
      ? { start: text.selectionStart, end: text.selectionEnd!, direction: text.selectionDirection! } : null;
    const wire = this.wire;
    const rows = wire?.models ?? [];
    const keys = wire?.setup?.keys;
    // The frame both Toolbox views share (toolbox.ts): tabs, then the band (your keys, Add a capability), then this header.
    const head = el('header', { class: 'toolbox-head' },
      el('div', { class: 'toolbox-head-title' }, el('h2', { id: 'models-page-heading' }, 'Models'),
        el('p', { class: 'toolbox-head-sub' }, wire ? summary(rows) : 'Waiting for the app…')),
      el('div', { class: 'toolbox-head-acts' },
        this.commands.space ? el('a', { href: '#models/space', class: 'toolbox-head-link' }, 'Space') : null));
    const band = toolboxBand(keys, wire?.setup?.counts, { add: (way) => this.addWay(way) }, 'tools');
    const body = el('div', { class: 'models-page-body' });
    if (wire) body.append(this.starter ??= starterOffer('row'));
    // The sections are run-spec.ts's `modelSection`, the ones Tools By model and a task's model list use (Needs you, Ready,
    // Not checked, Can be set up, then Not on this Mac folded), so the summary, the side's counts, these sections and Tools
    // agree exactly (one meaning of Ready: face on or obtained; Not checked is its own state).
    const bySection = new Map<ModelSection, ModelRow[]>();
    for (const row of rows) { const section = modelSection(row); bySection.set(section, [...(bySection.get(section) ?? []), row]); }
    for (const [section, title] of MODEL_SECTIONS) {
      const list = bySection.get(section) ?? [];
      if (section !== 'away' && list.length) body.append(this.section(section, title, list, rows, keys));
    }
    body.append(this.community());
    if (this.commands.fijiInventory) body.append(this.fijiSection(rows));
    const away = bySection.get('away') ?? [];
    if (away.length) {
      const fold = el('details', { class: 'toolbox-unavailable', 'data-section': 'away', open: this.unavailableOpen },
        el('summary', { class: 'toolbox-section-title' }, 'Not on this Mac', el('span', { class: 'models-group-count' }, String(away.length))),
        el('div', { class: 'models-grid' }, ...away.map(row => this.card(row, keys))));
      fold.addEventListener('toggle', () => { this.unavailableOpen = fold.open; });
      body.append(fold);
    }
    if (this.commands.volumes && rows.some((r) => r.id === 'brainreg' || r.newKind?.input === 'volume.3d')) body.append(this.volumesPanel());
    // The older contribution controls, folded; open across redraws once opened (the add sheet's link opens them).
    const advanced = el('details', { class: 'models-advanced-add', open: this.advancedOpen }, el('summary', {}, 'Advanced contribution controls'), this.addYourOwn());
    advanced.addEventListener('toggle', () => { this.advancedOpen = (advanced as HTMLDetailsElement).open; });
    body.append(advanced);
    if (!this.sheet && readFlow()) {
      const resume = el('button', { type: 'button', class: 'mm-control' }, 'Continue setup');
      resume.addEventListener('click', () => { const context = this.retainedFlow ?? readFlow(); if (context) { this.sheet = Object.assign(context, { detailed: false, focus: true }); this.retainedFlow = this.sheet; this.render(); } });
      head.append(resume);
    }
    const trials = this.ownTrialsSection(); if (trials) body.append(trials);
    const drafts = this.draftsSection(); if (drafts) body.append(drafts);
    if (this.commands.space) { this.space ??= spaceSection(this.commands.space); body.append(this.space); }
    const selected = rows.find(r => r.id === this.selected);
    const details = selected ? this.toolDetails(selected, keys) : null;
    const sheet = this.sheetNode(rows, keys);
    if (sheet) for (const under of [head, band, body, details]) under?.setAttribute('inert', '');
    const next = toolboxFrame('tools', band, head, body, details, sheet);
    if (sheet) next.querySelector('.toolbox-tabs')?.setAttribute('inert', '');
    for (const node of next.querySelectorAll<HTMLElement>('[data-model-editor]')) {
      const previous = editors.get(node.dataset.modelEditor!);
      if (previous && previous !== node) { node.parentElement!.insertBefore(previous, node); node.remove(); }
    }
    // Stable semantic control identities preserve keyboard position while fresh handlers use the newest facts.
    const seenFocus = new Map<string, number>();
    for (const tag of ['button', 'a', 'summary', 'input', 'select', 'aside']) {
      for (const node of next.querySelectorAll<HTMLElement>(tag)) {
        const owner = node.closest<HTMLElement>('[data-tool]')?.dataset.tool ?? node.closest<HTMLElement>('[data-model]')?.dataset.model ?? '';
        const role = node.closest<HTMLElement>('[data-role]')?.dataset.role ?? '';
        const key = [owner, role, tag, node.id, node.className, node.getAttribute('aria-label'), node.getAttribute('href'),
          tag === 'input' || tag === 'select' ? '' : node.textContent?.slice(0, 120)].join('|');
        const ordinal = seenFocus.get(key) ?? 0; seenFocus.set(key, ordinal + 1);
        node.dataset.toolboxFocus = `${key}|${ordinal}`;
      }
    }
    this.root.replaceChildren(next);
    const detail = this.root.querySelector<HTMLElement>('.tool-details'); if (detail) detail.scrollTop = detailScroll;
    if (focused && this.root.contains(focused)) {
      focused.focus({ preventScroll: true });
      if (selection) text!.setSelectionRange(selection.start, selection.end, selection.direction);
    }
    if (focusKey && focused && !this.root.contains(focused)) {
      [...this.root.querySelectorAll<HTMLElement>('[data-toolbox-focus]')].find(n => n.dataset.toolboxFocus === focusKey)?.focus({ preventScroll: true });
    }
    if (this.sheet?.focus) { this.sheet.focus = false; this.root.querySelector<HTMLElement>('.tool-setup-sheet')?.focus({ preventScroll: true }); }
    const currentSheet = this.root.querySelector<HTMLElement>('.tool-setup-sheet');
    if (currentSheet) {
      const scrollBody = currentSheet.querySelector('.tool-setup-sheet-body');
      if (scrollBody) scrollBody.scrollTop = sheetScroll;
      if (focusedInSheet && !currentSheet.contains(document.activeElement)) currentSheet.focus({ preventScroll: true });
    }
    this.root.scrollTop = scroll;
  }

  private comparison(result: OwnComparison, trial: string): HTMLElement {
    const figures = [result.candidate, result.baseline].map((part, index) => el('figure', {},
      el('figcaption', {}, `${index ? 'Current default' : 'Candidate'}: ${part.says}`,
        part.count !== undefined && part.count !== null ? ` · ${part.count} found` : '',
        part.rows !== undefined && part.rows !== null ? ` · ${part.rows} rows` : '',
        part.ms !== undefined ? ` · ${part.ms} ms` : ''),
      part.preview ? el('img', { src: part.preview, alt: `${index ? 'Default' : 'Candidate'} result on your chosen picture`, loading: 'lazy' }) : null,
      part.text ? el('pre', {}, part.text) : null,
      part.preview ? el('a', { href: `/api/own-trials/${trial}/${index ? 'baseline' : 'candidate'}.json`, target: '_blank', rel: 'noopener' }, 'Inspect retained output') : null));
    return el('div', {}, el('div', { class: 'own-comparison' }, ...figures),
      el('p', {}, `Same input SHA-256 ${result.inputSha256.slice(0, 12)}. Results are for your review; agreement does not establish accuracy.`));
  }

  private ownTrialsSection(model?: string): HTMLElement | null {
    const trials = (this.wire?.setup?.ownTrials ?? []).filter(t => !model || t.model === model);
    if (!trials.length) return null;
    return el('section', { class: 'models-own' }, el('h3', {}, 'Compare before using'), ...trials.map(t => {
      const said = el('p', { role: 'status' }, this.comparisonMessages.get(t.id) ?? '');
      const row = el('article', { class: 'own-trial', 'data-own-trial': t.id, 'data-model': t.model }, el('h4', {}, ownTrialTitle(t, trials)),
        el('p', {}, `Job: ${t.job}. Your selected picture stays on this Mac.`),
        el('img', { class: 'own-input-preview', src: `/api/own-trials/${t.id}/input`, alt: 'Your selected comparison picture', loading: 'lazy' }));
      const act = (label: string, run: () => Promise<unknown>) => {
        const b = el('button', { type: 'button', class: 'mm-control', 'data-toolbox-focus': `comparison:${t.id}:${label}` }, label);
        b.disabled = this.comparisonPending.has(t.id);
        b.addEventListener('click', () => {
          if (this.comparisonPending.has(t.id)) return;
          this.comparisonPending.add(t.id); this.comparisonMessages.delete(t.id); b.disabled = true;
          void run().catch(e => { this.comparisonMessages.set(t.id, String(e).slice(0, 4096)); })
            .finally(() => { this.comparisonPending.delete(t.id); this.render(); });
        });
        row.append(b);
      };
      if (t.needsAdapter && t.link && this.commands.build) act('Have an AI draft it (up to $2)', () => this.commands.build!(t.link!, t.id));
      else {
        if (this.commands.turnOnModel && this.wire?.models?.find(r => r.id === t.model)?.status !== 'ready')
          act('Set up for comparison', () => this.commands.turnOnModel!(t.model));
        if (this.commands.compareOwn) act('Compare on my picture', async () => {
          const result = await this.commands.compareOwn!(t.id, t.model); t.comparison = result; this.render();
        });
        if (t.comparison) {
          row.append(this.comparison(t.comparison, t.id));
          if (this.commands.useOwn) act('Use it', async () => { const result = await this.commands.useOwn!(t.model, t.id); this.comparisonMessages.set(t.id, result.says); });
        }
      }
      row.append(said); return row;
    }));
  }

  private draftsSection(only?: string): HTMLElement | null {
    const list = (this.wire?.setup?.drafts ?? []).filter((d) => d.state !== 'used' && (!only || d.id === only));
    if (!list.length) return null;
    const rows = list.map((d) => {
      const last = d.last;
      const input = d.input?.evidenceClass === 'user-input' ? 'your selected picture'
        : d.input?.evidenceClass === 'generated-fixture' ? `a generated test fixture (${d.input.identity})`
          : d.input ? `a representative test asset (${d.input.identity})` : 'an input whose origin was not retained';
      const how = d.state === 'ran' ? `It ran on ${input}${!d.job || d.job === 'find' ? ` and found ${last?.count ?? 0}` : ` for ${d.job}`}; contract smoke only`
        : d.terminal ? `Builder ${d.terminal.status}; draft and attempts kept`
          : last?.error ? `Last try: ${errorWords(last.error)}` : 'Being drafted';
      const li = el('li', { class: 'models-draft', 'data-state': d.state }, el('strong', {}, d.name || d.id), ' ',
        el('span', { class: 'model-link' }, d.link.replace(/^https?:\/\//, '')),
        el('span', { class: 'model-what' }, `${how} · ${d.tries} tr${d.tries === 1 ? 'y' : 'ies'} · drafted by AI, run only inside the sandbox`));
      const origin = only && this.sheet?.draft === d.id ? this.sheet : null;
      const said = el('p', { role: 'status' }, this.draftMessages.get(d.id) ?? '');
      const act = (label: string, work: () => Promise<void>, primary = false) => {
        const button = el('button', { type: 'button', class: `mm-control${primary ? ' mm-primary' : ''}`, 'data-toolbox-focus': `draft:${d.id}:${label}` }, label);
        button.disabled = this.draftPending.has(d.id);
        button.addEventListener('click', () => {
          if (this.draftPending.has(d.id)) return;
          this.draftPending.add(d.id); this.draftMessages.delete(d.id);
          if (origin) this.entryFailures.delete(origin);
          button.disabled = true;
          void (async () => { if (origin) this.assertFlow(origin); await work(); })()
            .catch(error => {
              this.draftMessages.set(d.id, (error instanceof Error ? error.message : String(error)).slice(0, 4096));
              if (origin) this.entryFailed(origin, error);
            }).finally(() => { this.draftPending.delete(d.id); this.render(); });
        });
        li.append(button);
      };
      if (d.state === 'ran' && d.trial && this.commands.compareOwn) {
        act('Compare on my picture', async () => {
          const result = await this.commands.compareOwn!(d.trial!, undefined, d.id);
          const current = this.wire?.setup?.drafts?.find(row => row.id === d.id);
          if (current) current.comparison = result;
        });
        if (d.comparison) li.append(this.comparison(d.comparison, d.trial));
      }
      const review = d.state === 'ran' ? d.review : null;
      if (review) li.append(draftReview(review));
      const blocked = (review?.checks ?? []).filter(c => c.blocks);
      if (blocked.length) li.append(el('p', { class: 'models-draft-blocked' }, `Not offered: ${blocked.map(c => c.says).join('; ')}`));
      if (d.state === 'ran' && (!d.trial || d.comparison) && review?.digest && !blocked.length && this.commands.useDraft) {
        const reviewed = review.digest;
        act('Use it', async () => {
          const result = await this.commands.useDraft!(d.id, reviewed);
          if (!result.model) throw new Error(result.says || 'The draft did not return a capability. Check its result and retry.');
          this.draftMessages.set(d.id, result.says || 'Your draft is now a capability.');
          if (origin && this.retainEntry(origin, { candidate: result.model })) {
            if (this.sheet === origin && origin.session === (this.commands.session?.() ?? ''))
              await this.selectEntry(result.model);
          }
        }, true);
      }
      li.append(said);
      if ((d.terminal || last?.error) && this.commands.communityDraft) {
        const ask = el('button', { type: 'button', class: 'mm-control' }, 'Ask the community');
        const text = el('textarea', { 'aria-label': 'Community question draft', hidden: true, rows: '10' });
        const note = el('p', { role: 'status' });
        const copy = el('button', { type: 'button', class: 'mm-control', hidden: true }, 'Copy draft');
        ask.addEventListener('click', () => {
          ask.disabled = true;
          void this.commands.communityDraft!(d.id).then(r => { text.value = r.text; text.hidden = copy.hidden = false; note.textContent = r.says; })
            .catch(error => { note.textContent = String(error); }).finally(() => { ask.disabled = false; });
        });
        copy.addEventListener('click', () => { void navigator.clipboard.writeText(text.value).then(() => { note.textContent = 'Copied; nothing was sent.'; }); });
        li.append(el('div', { 'data-model-editor': `draft-community:${d.id}` }, ask, text, copy, note));
      }
      return li;
    });
    return el('section', { class: 'models-own', 'aria-labelledby': 'models-drafts-title' },
      el('h3', { id: 'models-drafts-title' }, 'Capability drafts'),
      el('p', {}, 'Adapters an AI is drafting for links no template runs. You choose whether to use what it makes; whether its '
        + 'results are right is yours to judge.'), el('ul', { class: 'models-drafts' }, ...rows));
  }

  /** The Fiji list is read once the Toolbox opens (its counts are said on both views) and again after a generation that
   * added an update site finished. */
  private readFiji(): void {
    if (this.fiji || this.fijiAsked || !this.commands.fijiInventory) return;
    this.fijiAsked = true;
    this.fijiFailed = false;
    void this.commands.fijiInventory().then((got) => { this.fiji = got; }, () => { this.fijiFailed = true; })
      .finally(() => { this.fijiAsked = false; if (!this.root.hidden) this.render(); });
  }

  /** Fiji, said honestly (Will, 2026-10-15): the functions the app can run (each a reviewed binding, with its card's
   * state), then every extension imagej.net lists with how it installs, folded. Installed in Fiji is not usable here. */
  private fijiSection(rows: ModelRow[]): HTMLElement {
    const inv = this.fiji;
    const section = el('section', { class: 'toolbox-section toolbox-fiji', 'aria-labelledby': 'models-fiji-heading' },
      el('header', { class: 'toolbox-section-head' }, el('h3', { class: 'toolbox-section-title', id: 'models-fiji-heading' }, 'Fiji'),
        el('p', { class: 'toolbox-section-what' }, 'The app keeps its own Fiji. Each function must pass its check on the version it uses. Contributed scripts also require permission and run in a separate, confined process. Installing a plugin alone does not connect it to the app.')));
    const words = inv ? fijiWords(inv.summary, rows) : null;
    const counts = el('p', { class: 'toolbox-fiji-counts' }, words ? `${words.runs} · ${words.listed}` : this.fijiFailed ? 'Couldn’t read Fiji’s list. ' : 'Reading the list…');
    if (!inv && this.fijiFailed) counts.append(this.fijiAgain());
    section.append(counts);
    const functions = (inv?.summary.bound ?? []).map((b) => ({ b, row: rows.find((r) => r.id === b.id) }));
    if (functions.length) section.append(el('ul', { class: 'toolbox-fiji-functions' }, ...functions.map(({ b, row }) => {
      // Its state as its card says it: the lit cell (model-status.ts cardLook) and the face's word, no dot or pill.
      const state = row ? this.faceState(row) : null;
      const look = row ? cardLook(row, this.wire, { inWorkspace: this.inWorkspace(), served: null, shelf: !!this.commands.shelf }) : null;
      return el('li', {}, el('a', { href: `#toolbox/${b.id}` }, b.name),
        state && look ? el('span', { class: 'toolbox-fiji-state', 'data-colour': state.colour === 'red' ? 'grey' : state.colour },
          litCell(look.cell, { size: 12, decorative: true, fraction: look.fraction }), state.word) : null);
    })));
    const details = el('details', { class: 'models-fiji' }) as HTMLDetailsElement;
    details.open = this.fijiOpen;
    details.append(el('summary', {}, inv ? `Every extension on imagej.net (${inv.summary.listed})` : 'Every extension on imagej.net'));
    details.addEventListener('toggle', () => { this.fijiOpen = details.open; if (details.open) this.readFiji(); });
    section.append(details);
    if (!this.fijiOpen) return section;
    if (!inv) { details.append(el('p', { class: 'toolbox-section-what' }, this.fijiFailed ? 'Couldn’t read Fiji’s list.' : 'Reading the list…')); return section; }
    if (!inv.extensions.length) { details.append(el('p', { class: 'toolbox-section-what' }, inv.why || 'No list yet')); return section; }
    details.append(el('p', {}, 'Every extension on imagej.net’s list, with how it installs. Add to Fiji makes a new Fiji with it, checked before it is used; '
      + 'the app still runs only the functions above.'));
    const search = el('input', { type: 'search', class: 'mm-control fiji-search', placeholder: 'Search by name or category',
      'aria-label': 'Search Fiji plugins', value: this.fijiQuery }) as HTMLInputElement;
    const categories = [...new Set(inv.extensions.flatMap((e) => e.categories))].sort();
    const pick = el('select', { class: 'mm-control fiji-category', 'aria-label': 'Category' },
      el('option', { value: '' }, 'Every category'), ...categories.map((c) => el('option', { value: c }, c))) as HTMLSelectElement;
    pick.value = this.fijiCategory;
    const list = el('ul', { class: 'fiji-list' });
    const fill = () => {
      const shown = fijiFilter(inv.extensions, this.fijiQuery, this.fijiCategory);
      list.replaceChildren(...shown.slice(0, 200).map((e) => el('li', { class: 'fiji-row', 'data-route': e.route.kind },
        el('a', { href: e.link, target: '_blank', rel: 'noopener noreferrer', class: 'fiji-name' }, e.name),
        el('span', { class: 'fiji-categories' }, e.categories.join(', ')),
        el('span', { class: 'fiji-how', title: e.route.evidence ?? '' }, e.how),
        e.installed ? el('span', { class: 'fiji-installed' }, e.installed) : null,
        this.fijiAction(e),
        el('span', { class: 'fiji-usable' }, e.usable))));
      if (shown.length > 200) list.append(el('li', { class: 'fiji-more' }, `${shown.length - 200} more: search to narrow`));
    };
    search.addEventListener('input', () => { this.fijiQuery = search.value; fill(); });
    pick.addEventListener('change', () => { this.fijiCategory = pick.value; fill(); });
    fill();
    details.append(el('div', { class: 'fiji-tools' }, search, pick), list);
    return section;
  }

  /** Try again, after the Fiji list could not be read. */
  private fijiAgain(): HTMLElement {
    const again = el('button', { type: 'button', class: 'model-fact-act toolbox-fiji-again' }, 'Try again');
    again.addEventListener('click', () => { this.readFiji(); this.render(); });
    return again;
  }

  /** "Add to Fiji" for an extension on an update site not in this Fiji yet, its progress while its generation is made. */
  private fijiAction(e: FijiExtension): HTMLElement | null {
    if (e.route.kind !== 'update-site' || e.installed !== 'Not in this Fiji yet' || !this.commands.fijiAdd) return null;
    const job = (this.wire?.setup?.installs ?? []).find((r) => r.site?.site === e.route.site);
    if (job && (job.state === 'queued' || job.state === 'installing')) return el('span', { class: 'fiji-job' }, job.says);
    const add = el('button', { type: 'button', class: 'model-fact-act' }, 'Add to Fiji');
    add.title = `Turns on the ${e.route.site} update site in a new Fiji, proven before it is used`;
    add.addEventListener('click', () => {
      add.setAttribute('disabled', '');
      add.textContent = 'Adding…';
      void this.commands.fijiAdd!(e.page).catch((error: unknown) => {
        add.removeAttribute('disabled');
        add.textContent = 'Add to Fiji';
        add.title = error instanceof Error ? error.message : String(error);
      });
    });
    return el('span', {}, add, job?.state === 'failed' ? el('span', { class: 'fiji-job' }, job.says) : null);
  }

  private getAll(needed: { model: string; name: string; says: string }[]): HTMLElement {
    const button = el('button', { type: 'button', class: 'mm-control models-get-all' }, `Download all ${needed.length}`);
    button.addEventListener('click', () => {
      button.disabled = true;
      for (const n of needed) void this.commands.download(n.model).catch(() => { button.disabled = false; });
    });
    return el('div', { class: 'models-get-all-row' },
      el('span', {}, `${needed.map((n) => n.name).join(', ')}: what your choices are waiting on, downloaded one at a time.`), button);
  }

  private executionAction(row: ModelRow): HTMLElement | null {
    const blocked = row.execution;
    if (!blocked || blocked.action === 'unsupported') return null;
    const button = el('button', { type: 'button', class: 'mm-control model-action' },
      blocked.action === 'review-bundle' ? 'Review results' : blocked.action === 'compare' ? 'Review comparison' : 'Choose a comparison picture');
    button.addEventListener('click', () => {
      if (blocked.action === 'review-bundle') {
        const setup = row.setups?.find(s => s.role === `model:${row.id}`) ?? row.setups?.[0];
        if (setup) { this.openSetup(row, setup, true); this.render(); }
        return;
      }
      this.selected = null;
      if (blocked.action === 'choose-comparison-input') this.ownCandidate = { model: row.id, link: row.link, kind: 'model', job: blocked.job };
      this.render();
      const purpose = this.root.querySelector<HTMLSelectElement>('.own-job');
      if (purpose && blocked.job) purpose.value = blocked.job;
      const target = blocked.action === 'compare'
        ? this.root.querySelector<HTMLElement>(`[data-own-trial][data-model="${CSS.escape(row.id)}"]`)
        : this.root.querySelector<HTMLElement>('.own-picture');
      target?.scrollIntoView?.({ block: 'center' });
      (target?.querySelector<HTMLElement>('button') ?? target)?.focus();
    });
    return button;
  }

  private actionNode(row: ModelRow, keys: Record<string, KeyFacts> | undefined): HTMLElement | null {
    if (row.execution) return this.executionAction(row);
    const a = action(row);
    if (a.kind === 'key') {
      const key = Object.values(keys ?? {}).find((k) => k.service === a.service);
      if (!key) return null;
      const button = el('button', { type: 'button', class: 'mm-control model-action' }, a.label);
      button.addEventListener('click', () => openAddKey(key, button));
      return button;
    }
    if (a.kind === 'download') return downloadControl(a.model, a.label, this.wire?.setup?.downloads as DownloadRow[] | undefined, this.commands.download);
    if (a.kind === 'licence') return el('a', { class: 'model-action model-action-link', href: a.url, target: '_blank', rel: 'noopener noreferrer' }, a.label);
    if (a.kind === 'setup' && this.commands.install) {
      const rows = (this.wire?.setup?.installs ?? []) as InstallRow[];
      const going = rows.find((r) => r.model === a.model && (r.state === 'queued' || r.state === 'installing'));
      const failed = rows.find((r) => r.model === a.model && r.state === 'failed');
      const button = el('button', { type: 'button', class: 'mm-control model-action' }, going ? 'Installing…' : a.label);
      button.disabled = Boolean(going);
      const said = el('span', { class: 'cap-download-why', hidden: !(going || failed) }, (going ?? failed)?.says ?? '');
      button.addEventListener('click', () => {
        button.disabled = true;
        button.textContent = 'Installing…';
        void this.commands.install!(a.model).catch((error: unknown) => {
          button.disabled = false;
          button.textContent = a.label;
          said.textContent = error instanceof Error ? error.message : String(error);
          said.hidden = false;
        });
      });
      return el('span', { class: 'cap-download' }, button, said);
    }
    if (a.kind === 'trust' && this.commands.channelTrust) {
      const button = el('button', { type: 'button', class: 'mm-control model-action' }, a.label);
      const said = el('span', { class: 'cap-download-why' }, 'Not reviewed here: its code runs with your permissions, each entry after its own Install and proof.');
      button.addEventListener('click', () => {
        button.disabled = true;
        void this.commands.channelTrust!(a.channel, true).catch((error: unknown) => {
          button.disabled = false;
          said.textContent = error instanceof Error ? error.message : String(error);
        });
      });
      return el('span', { class: 'cap-download' }, button, said);
    }
    if (a.kind === 'activate' && this.commands.activate) {
      const button = el('button', { type: 'button', class: 'mm-control model-action' }, a.label);
      const said = el('span', { class: 'cap-download-why', hidden: true });
      button.addEventListener('click', () => {
        button.disabled = true;
        button.textContent = 'Checking… (about a minute)';
        void this.commands.activate!(a.model).catch((error: unknown) => {
          button.disabled = false;
          button.textContent = a.label;
          said.textContent = error instanceof Error ? error.message : String(error);
          said.hidden = false;
        });
      });
      return el('span', { class: 'cap-download' }, button, said);
    }
    if (isOn(row) && this.commands.deactivate) {
      const button = el('button', { type: 'button', class: 'mm-control model-action-quiet' }, 'Turn off');
      button.addEventListener('click', () => { button.disabled = true; void this.commands.deactivate!(row.id).catch(() => { button.disabled = false; }); });
      return button;
    }
    return null;
  }

  /** The server owns removal and retention checks. Polling never repeats or forgets this local request. */
  private removeNode(row: ModelRow): HTMLElement {
    const box = el('div', { class: 'model-card-action model-remove' });
    const pending = this.removalPending.has(row.id);
    const message = pending ? 'Removing installed software… Your saved work is kept.' : this.removalMessages.get(row.id) ?? '';
    const said = el('span', { class: 'cap-download-why', role: 'status', hidden: !message }, message);
    const installing = this.wire?.setup?.installs?.some(i => i.model === row.id && ['queued', 'installing'].includes(i.state));
    if (!this.confirmingRemove.has(row.id)) {
      const remove = el('button', { type: 'button', class: 'mm-control model-action-quiet' }, 'Remove') as HTMLButtonElement;
      remove.disabled = Boolean(installing || pending);
      if (installing) remove.title = 'It is being installed: Stop it first';
      remove.addEventListener('click', () => { this.confirmingRemove.add(row.id); this.render(); });
      box.append(remove, said);
      return box;
    }
    box.setAttribute('role', 'alertdialog');
    const yes = el('button', { type: 'button', class: 'mm-control model-remove-yes' }, 'Remove') as HTMLButtonElement;
    yes.disabled = Boolean(installing || pending);
    if (installing) yes.title = 'It is being installed: Stop it first';
    const no = el('button', { type: 'button', class: 'mm-control model-remove-no' }, 'Cancel') as HTMLButtonElement;
    no.disabled = pending;
    no.addEventListener('click', () => {
      if (this.removalPending.has(row.id)) return;
      this.confirmingRemove.delete(row.id); this.removalMessages.delete(row.id); this.render();
    });
    yes.addEventListener('click', () => {
      if (!this.confirmingRemove.has(row.id) || this.removalPending.has(row.id)
        || !this.wire?.models?.some(current => current.id === row.id && current.env?.installed)
        || this.wire?.setup?.installs?.some(i => i.model === row.id && ['queued', 'installing'].includes(i.state))) return;
      this.removalPending.add(row.id); this.removalMessages.delete(row.id); this.render();
      void (async () => {
        try {
          await this.commands.uninstall!(row.id);
          this.confirmingRemove.delete(row.id);
        } catch (error: unknown) {
          let message = error instanceof Error ? error.message : String(error);
          // Command refusals carry a separate recovery instruction; retain it beside the failure.
          const reply = error && typeof error === 'object' && 'reply' in error ? error.reply : null;
          const next = reply && typeof reply === 'object' && 'next' in reply ? reply.next : null;
          if (typeof next === 'string' && next.trim() && !message.includes(next)) message += ` ${next}`;
          this.removalMessages.set(row.id, message);
        } finally {
          this.removalPending.delete(row.id); this.render();
        }
      })();
    });
    box.append(el('p', {}, `Remove ${row.name}’s installed versions? Its entry and saved results stay. `
      + 'Running work must finish or stop first. Versions needed by saved scientific work are kept; use Turn off instead. '
      + 'After removal, prepare it again to use it.'), yes, no, said);
    return box;
  }

  private versionsNode(row: ModelRow): HTMLElement | null {
    const kept = row.versions ?? [];
    if (!kept.length || (kept.length === 1 && kept[0].inUse) || !this.commands.setVersion) return null;
    const using = kept.find((v) => v.inUse);
    const chosen = kept.some((v) => v.chosen);
    const older = kept.find((v, i) => i > kept.findIndex((x) => x.inUse) && !v.inUse);
    const said = el('span', { class: 'cap-download-why', hidden: true });
    const press = (generation: string | null, button: HTMLButtonElement) => {
      button.disabled = true;
      void this.commands.setVersion!(row.id, generation).then((r) => { said.textContent = r.says ?? ''; said.hidden = !r.says; })
        .catch((error: unknown) => { button.disabled = false; said.textContent = error instanceof Error ? error.message : String(error); said.hidden = false; });
    };
    const line = el('p', { class: 'model-how' }, `Running ${using?.version ?? 'none'}${chosen ? ' (your choice)' : ''}.`);
    const box = el('div', { class: 'model-card-action' }, line);
    if (!kept[0].inUse) {
      const newest = el('button', { type: 'button', class: 'mm-control model-action-quiet' }, 'Use the newest') as HTMLButtonElement;
      newest.addEventListener('click', () => press(kept[0].generation, newest));
      box.append(newest);
    } else if (older) {
      const goBack = el('button', { type: 'button', class: 'mm-control model-action-quiet' }, `Go back to ${older.version}`) as HTMLButtonElement;
      goBack.addEventListener('click', () => press(older.generation, goBack));
      box.append(goBack);
    }
    box.append(said);
    return box;
  }

  /** One section (run-spec.ts MODEL_SECTIONS): its title and count, then its cards, one grid of one card shape. What a
   * section means is its name; the jobs an entry does are in its Details. */
  private section(section: ModelSection, title: string, entries: ModelRow[], rows: ModelRow[], keys: Record<string, KeyFacts> | undefined): HTMLElement {
    const node = el('section', { class: 'toolbox-section models-section', 'data-section': section, 'aria-labelledby': `toolbox-group-${section}` },
      el('h3', { class: 'toolbox-section-title', id: `toolbox-group-${section}` }, title, el('span', { class: 'models-group-count' }, String(entries.length))),
      el('div', { class: 'models-grid' }, ...entries.map(row => this.card(row, keys))));
    if (section === 'needs') { const downloads = neededDownloads(rows); if (downloads.length > 1) node.append(this.getAll(downloads)); }
    return node;
  }

  /** From the community, always shown (Will, 2026-10-15: the page shows it is communal): what others added since you last
   * looked, the community catalogs you follow, and how to share one of yours. Empty parts say so rather than vanish. */
  private community(): HTMLElement {
    const section = el('section', { class: 'toolbox-section toolbox-community', 'aria-labelledby': 'toolbox-community-heading' },
      el('header', { class: 'toolbox-section-head' },
        el('h3', { class: 'toolbox-section-title', id: 'toolbox-community-heading' }, 'From the community'),
        el('p', { class: 'toolbox-section-what' }, 'What others have added, the catalogs you follow, and how to share yours. Nothing turns on by itself.')));
    const shelf: ShelfCommands | null = this.commands.shelf ? { ...this.commands.shelf, open: (model) => this.show(model),
      confirm: this.commands.confirm, trust: this.commands.channelTrust ? (c) => this.commands.channelTrust!(c, true) : undefined } : null;
    const news = shelf && this.wire?.setup?.news ? newsSection(this.wire.setup.news, shelf) : null;
    section.append(news ?? el('p', { class: 'toolbox-community-none' }, 'Nothing new in the catalog since you last looked.'));
    if (this.commands.catalogUpdate) section.append(this.channelsSection());
    section.append(el('p', { class: 'toolbox-community-share' }, el('strong', {}, 'Share yours. '),
      'Open one of your own tools (its name on its card): Share makes a file anyone can open, or sends it to the reviewed catalog for everyone, on your sign-off.'));
    return section;
  }

  private addWay(way: AddWay | 'start'): void {
    this.restoredRoute = false;
    // What opened it (the Models header's Add model or tool, a band's way in) takes focus back when it closes.
    const active = typeof document !== 'undefined' ? document.activeElement as HTMLElement | null : null;
    if (active && typeof active.focus === 'function' && !active.closest?.('.tool-setup-sheet')) this.addOpener = active;
    const previous = this.sheet ?? readFlow();
    const route = way === 'start' ? previous?.values?.route ?? 'find' : way === 'link' || way === 'file' ? 'add' : way === 'build' ? 'create' : way;
    this.inlineKey?.form.dispose(); this.inlineKey = null;
    this.entry?.dispose(); this.entry = null;
    this.sheet = { route, model: '', role: '', option: '', session: this.commands.session?.() ?? '',
      values: { ...previous?.values, route }, detailed: false, focus: true, choose: way === 'start' };
    this.entryMessage = ''; this.workspaceResult = null; this.retainedFlow = this.sheet;
    saveFlow(this.sheet); this.render();
  }

  /** Details for one entry, beside the grid; under 1100 px it takes the grid's place (workspace-feel.css), so it opens at
   * its top (its name and × in view) and closing it returns to where the grid was. */
  private openDetails(id: string, more = false): void {
    const scroller = this.scroller();
    if (more) this.moreOpen.add(id); // what was asked for is in "More about this model" (how to install it)
    if (!this.selected) this.gridScroll = scroller?.scrollTop ?? 0;
    this.selected = id; this.render();
    if (scroller && detailsReplaceGrid()) scroller.scrollTop = 0;
    this.root.querySelector<HTMLElement>('.tool-details')?.focus({ preventScroll: true });
  }

  /** What scrolls the page: the Models sheet's body, else the page itself. */
  private scroller(): HTMLElement | null {
    return this.root.closest?.<HTMLElement>('.study-overlay-body') ?? this.root;
  }

  private closeDetails(): void {
    const id = this.selected; this.selected = null; this.render();
    const scroller = this.scroller();
    if (scroller && detailsReplaceGrid()) scroller.scrollTop = this.gridScroll;
    [...this.root.querySelectorAll<HTMLElement>('[data-model]')].find(n => n.dataset.model === id)
      ?.querySelector<HTMLButtonElement>('.tool-details-open')?.focus({ preventScroll: true });
  }

  /** Closes the sheet before setup with nothing started; focus goes back to the Turn on that opened it (or its card while
   * that is busy). */
  private closeSheet(): void {
    this.restoredRoute = false;
    const open = this.sheet; if (!open) return;
    // Opened straight from Tools or Activity (#models/<id>/setup): closing returns to the Project, not to Models.
    if (typeof window !== 'undefined') window.dispatchEvent(new CustomEvent('hooke-setup-sheet-closed', { detail: { model: open.model } }));
    this.inlineKey?.form.dispose(); this.inlineKey = null;
    this.entry?.dispose(); this.entry = null;
    this.retainedFlow = open;
    this.sheet = null; saveFlow(open.model || open.values || open.workspace || open.draft ? open : null); this.render();
    const opener = [...this.root.querySelectorAll<HTMLButtonElement>('.tool-setup-act')].find(b => b.closest<HTMLElement>('[data-role]')?.dataset.role === open.role
      && (open.detailed ? !!b.closest('.tool-details') : !b.closest('.tool-details') && b.closest<HTMLElement>('[data-model]')?.dataset.model === open.model));
    const target = opener && !opener.disabled ? opener : opener?.closest<HTMLElement>(open.detailed ? '.tool-details' : '[data-model]');
    // Add model or tool (no model yet): back to the press that opened it, the header's button when that is gone.
    const added = !target && !open.model ? (this.addOpener?.isConnected !== false ? this.addOpener : null)
      ?? (typeof document !== 'undefined' ? document.querySelector?.<HTMLElement>('.study-overlay-add:not([hidden])') ?? null : null) : null;
    (target ?? added)?.focus({ preventScroll: true });
    this.addOpener = null;
  }

  /** `task`: the task whose run page opened it (show), where its Use it here goes; none from a card or Details. */
  private openSetup(row: ModelRow, setup: ToolSetup, detailed: boolean, task?: string): void {
    this.inlineKey?.form.dispose(); this.inlineKey = null;
    const context = this.sheet;
    this.entry?.dispose(); this.entry = null;
    this.sheet = { ...context, route: context?.route ?? 'find', model: row.id, role: setup.role, option: setup.option,
      session: this.commands.session?.() ?? '', recipe: setup.recipe ?? setup.plan?.recipe, detailed, focus: true, task };
    this.retainedFlow = this.sheet; saveFlow(this.sheet);
  }

  private retainEntry(open: CapabilityFlowContext, update: Partial<CapabilityFlowContext>): boolean {
    if (this.sheet !== open && this.retainedFlow !== open) return false;
    Object.assign(open, update); saveFlow(open); return true;
  }

  private assertFlow(open: CapabilityFlowContext): void {
    if (this.sheet !== open || open.session !== (this.commands.session?.() ?? ''))
      throw new Error('The session or task changed. Reopen this capability from the session you want to use.');
  }

  private entryFailed(open: CapabilityFlowContext, error: unknown): void {
    this.entryFailures.set(open, (error instanceof Error ? error.message : String(error)).slice(0, 4096));
    // A request may have published its private artifact before the session changed.
    // Keep its reference; the session gate still requires an explicit resume.
    if (error && typeof error === 'object' && 'recovery' in error) {
      const recovery = error.recovery;
      if (!recovery || typeof recovery !== 'object' || !('session' in recovery) || recovery.session !== open.session) return;
      if ('candidate' in recovery && typeof recovery.candidate === 'string' && recovery.candidate.length <= 4096)
        this.retainEntry(open, { candidate: recovery.candidate });
      if ('workspace' in recovery && recovery.workspace && typeof recovery.workspace === 'object' &&
          'id' in recovery.workspace && typeof recovery.workspace.id === 'string' && recovery.workspace.id.length <= 4096)
        this.retainEntry(open, { workspace: recovery.workspace.id });
    }
  }

  private async selectEntry(model: string): Promise<void> {
    const open = this.sheet; if (!open) throw new Error('Reopen Add model or tool to continue.');
    this.assertFlow(open);
    this.entry?.dispose(); this.entry = null;
    Object.assign(open, { model, candidate: model, role: '', option: '', recipe: undefined, focus: true });
    saveFlow(open); this.render();
  }

  private async inspectSource(source: CapabilitySource, progress?: (fraction: number) => void) {
    const open = this.sheet!; this.assertFlow(open);
    this.entryFailures.delete(open);
    this.entryPending.add(open);
    try {
    let result: { model?: string; name?: string; says?: string; kind?: string; ok?: boolean; why?: string };
    if (source.kind === 'link') {
      if (!this.commands.add) throw new Error('Adding links is unavailable. Reconnect to the Studio and retry.');
      result = await this.commands.add(source.url);
    } else if (source.kind === 'capability-package') {
      if (!this.commands.importCapability) throw new Error('Capability import is unavailable. Reconnect to the Studio and retry.');
      result = await this.commands.importCapability(source.file, open.session);
    } else {
      if (!this.commands.upload) throw new Error('Model upload is unavailable. Reconnect to the Studio and retry.');
      result = await this.commands.upload(source.file, progress ?? (() => undefined));
    }
    if (result.ok === false) throw new Error(result.says || 'This source could not be added.');
    if (result.kind === 'skeleton') return { kind: 'integration' as const, source: source.kind === 'link' ? source.url : source.file.name,
      says: integrationWords(result.why) };
    if (!result.model) return { kind: 'unsupported' as const, says: result.says || 'This source does not contain a supported model or capability. Choose another source.' };
    this.retainEntry(open, { candidate: result.model });
    return { kind: 'candidate' as const, candidate: result.model, name: result.name ?? this.wire?.models?.find(r => r.id === result.model)?.name ?? 'Your capability',
      says: result.says || 'The candidate is ready for setup and review.' };
    } catch (error) { this.entryFailed(open, error); throw error; }
    finally { this.entryPending.delete(open); if (this.sheet === open && !this.entry) this.render(); }
  }

  private async createEntry(request: CapabilityCreation): Promise<void> {
    const open = this.sheet!; this.assertFlow(open);
    if (this.entryPending.has(open)) return;
    this.entryFailures.delete(open);
    this.entryPending.add(open);
    try {
    if (request.method === 'coder') {
      if (!this.commands.workspace) throw new Error('The coding workspace service is unavailable. Reconnect and retry.');
      const result = await this.commands.workspace.create(request, open.session);
      if (this.retainEntry(open, { workspace: result.id })) this.workspaceResult = result;
    } else {
      if (!this.commands.ownPicture || !this.commands.build) throw new Error('The AI builder is unavailable. Reconnect and retry.');
      const picture = await this.commands.ownPicture(request.picture, request.job);
      this.assertFlow(open); open.input = picture.id; saveFlow(open);
      const result = await this.commands.build(request.source, picture.id, request.description, open.session);
      if (!result.draft) throw new Error(result.says || 'The builder did not return a draft. Retry after checking your research model.');
      this.retainEntry(open, { draft: result.draft });
    }
    if (this.sheet === open) { this.entry?.dispose(); this.entry = null; this.entryMessage = ''; this.render(); }
    } catch (error) { this.entryFailed(open, error); throw error; }
    finally { this.entryPending.delete(open); if (this.sheet === open) this.render(); }
  }

  private entrySheet(rows: ModelRow[]): HTMLElement {
    const open = this.sheet!;
    const promoted = this.wire?.setup?.drafts?.find(d => d.id === open.draft && d.state === 'used');
    if (!open.candidate && promoted?.model) this.retainEntry(open, { candidate: promoted.model });
    const body = el('div', { class: 'tool-setup-sheet-body' });
    const status = el('p', { role: 'status' }, this.entryFailures.get(open) ?? this.entryMessage);
    const close = el('button', { type: 'button', class: 'mm-control tool-setup-sheet-close' }, 'Close');
    close.addEventListener('click', () => this.closeSheet());
    const acts = el('div', { class: 'tool-setup-sheet-acts' }, close);
    const button = (label: string, work: () => Promise<unknown>) => {
      const b = el('button', { type: 'button', class: 'mm-control' }, label);
      b.disabled = this.entryPending.has(open);
      b.addEventListener('click', () => { if (this.entryPending.has(open)) return; this.entryFailures.delete(open); this.entryPending.add(open); b.disabled = true;
        void work().catch(error => { this.entryFailed(open, error); if (this.sheet === open) status.textContent = this.entryFailures.get(open)!; })
          .finally(() => { this.entryPending.delete(open); if (this.sheet === open) this.render(); }); });
      return b;
    };
    if (!this.wire || (this.commands.session && !this.commands.session())) {
      body.append(el('p', {}, 'Reading your session and capability requirements…'));
    } else if (open.session !== (this.commands.session?.() ?? '')) {
      body.append(el('p', {}, 'The session changed. Your request is kept. Continue in this session before creating or running a tool.'),
        button('Continue in this session', async () => { open.session = this.commands.session?.() ?? ''; saveFlow(open); this.render(); }));
    } else if (open.workspace) {
      const workspace = this.workspaceResult?.id === open.workspace ? this.workspaceResult : null;
      if (!workspace && !this.workspaceReading && !this.entryMessage && this.commands.workspace) {
        this.workspaceReading = true;
        void this.commands.workspace.read(open.workspace, open.session).then(result => {
          if (this.sheet === open) this.workspaceResult = result;
        }).catch(error => { if (this.sheet === open) this.entryMessage = String(error); })
          .finally(() => { this.workspaceReading = false; this.render(); });
      }
      body.append(el('h4', {}, 'Continue in your coding tool'), el('p', {}, 'Open this private folder in Claude Code, Codex or your coding tool. Its instructions and capability skill are included. Return the completed package here for checks and review.'));
      if (workspace) {
        body.append(el('p', { class: 'capability-workspace-path' }, workspace.folder),
          button('Open workspace folder', () => this.commands.workspace!.reveal(open.workspace!, open.session)),
          button('Copy instructions', async () => { await navigator.clipboard.writeText(workspace.instructions); if (this.sheet === open) this.entryMessage = 'Instructions copied. Open the workspace in your coding tool.'; }));
        body.append(el('details', {}, el('summary', {}, 'Read the instructions'), el('pre', {}, workspace.instructions)));
      } else body.append(el('p', {}, this.workspaceReading ? 'Reading your workspace…' : 'The workspace could not be read.'),
        button('Read workspace again', async () => { this.entryMessage = ''; this.render(); }));
      const input = el('input', { type: 'file', accept: '.hookeui.zip,.observationui.zip,.zip', 'aria-label': 'Completed capability package',
        'data-model-editor': `completed-package:${open.workspace}` });
      body.append(el('label', {}, 'Completed capability package', input), button('Import completed capability', async () => {
        const file = [...this.root.querySelectorAll<HTMLInputElement>('input')].find(node => node.getAttribute('aria-label') === 'Completed capability package')?.files?.[0];
        if (!file) throw new Error('Choose the .hookeui.zip file returned by your coding tool.');
        const result = await this.inspectSource({ kind: 'capability-package', file });
        if (result.kind !== 'candidate') throw new Error(result.says);
        if (this.sheet === open) await this.selectEntry(result.candidate); else this.retainEntry(open, { model: result.candidate, role: '', option: '' });
      }));
    } else if (open.draft) {
      body.append(this.draftsSection(open.draft) ?? el('p', {}, open.candidate
        ? 'Your draft is now a capability. Continue with it to review setup and use it.'
        : 'Reading the retained AI draft. Its checks and result will appear here.'));
      if (!this.commands.build) body.append(el('p', {}, 'The builder is unavailable on this connection. Reconnect to continue.'));
    } else if (this.entryPending.has(open) && !this.entry) {
      body.append(el('p', {}, 'Your request is still being prepared. You can close this sheet and return here.'));
    } else {
      if (!this.entry) this.entry = capabilityEntry({ select: model => this.selectEntry(model), inspect: (source, progress) => this.inspectSource(source, progress),
        create: request => this.createEntry(request), changed: values => { if (this.sheet === open) { open.values = values; open.route = values.route; open.choose = false; saveFlow(open); } } },
      { ...open.values, route: open.route }, { choose: open.choose, advanced: () => {
        // The older contribution controls (Add your own, links and paths, your folder's files): Models' own section,
        // shown first (from Tools that opens Models), then this sheet closes.
        this.advancedOpen = true;
        if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') window.dispatchEvent(new CustomEvent('hooke-models-category', { detail: 'own' }));
        this.closeSheet();
      } });
      this.entry.update(rows); body.append(this.entry.element);
      // The link or file step's Review sits beside Close on the sheet's one row of actions (the mock's add-actions).
      acts.prepend(this.entry.footer);
    }
    if (open.candidate && this.entry?.element.querySelector<HTMLElement>('.capability-inspection')?.hidden !== false && open.session === (this.commands.session?.() ?? ''))
      body.append(button('Continue with your added capability', () => this.selectEntry(open.candidate!)));
    if (open.workspace || open.draft) acts.append(button('Back to your request', async () => this.addWay('create')));
    return this.sheetFrame('Add model or tool', body, status, acts);
  }

  private sheetNode(rows: ModelRow[], keys: Record<string, KeyFacts> | undefined): HTMLElement | null {
    const open = this.sheet, commands = this.commands.shelf;
    if (!open) return null;
    if (!open.model) return this.entrySheet(rows);
    if (!commands) return null;
    const row = rows.find(r => r.id === open.model);
    const setup = open.role ? row?.setups?.find(s => s.role === open.role && s.option === open.option) : row?.setups?.[0];
    if (setup && !open.role) { Object.assign(open, { role: setup.role, option: setup.option, recipe: setup.recipe ?? setup.plan?.recipe }); saveFlow(open); }
    const all = [...(this.wire?.setup?.shelf?.cards ?? []), ...(this.wire?.setup?.shelf?.behind ?? [])];
    const found = this.wire?.setup?.turning?.[open.role] ?? all.find(c => c.id === open.role)?.turning;
    const admitted = found?.option === open.option ? found : undefined;
    const identity = `${open.role}:${open.option}`, pending = `${identity}:tool-setup-act`;
    const status = el('p', { role: 'status' }, this.setupPending.has(pending) ? 'Sending your request…' : this.setupMessages.get(identity) ?? '');
    const body = el('div', { class: 'tool-setup-sheet-body' });
    const close = el('button', { type: 'button', class: 'mm-control tool-setup-sheet-cancel' }, 'Close');
    close.addEventListener('click', () => this.closeSheet());
    const acts = el('div', { class: 'tool-setup-sheet-acts' }, close);
    const press = (label: string, run: () => Promise<unknown>, cls = '') => {
      const button = el('button', { type: 'button', class: `mm-control ${cls}` }, label);
      button.disabled = this.setupPending.has(pending);
      button.addEventListener('click', () => {
        if (this.setupPending.has(pending)) return;
        this.setupPending.add(pending); this.setupMessages.delete(identity); this.render();
        void run().then(reply => {
          const result = reply as { ok?: boolean; error?: string } | undefined;
          if (result?.ok === false) this.setupMessages.set(identity, result.error ?? 'The action did not finish. Try again.');
        }).catch(error => this.setupMessages.set(identity, error instanceof Error ? error.message : String(error)))
          .finally(() => { this.setupPending.delete(pending); this.render(); });
      });
      return button;
    };
    const staleSession = open.session !== (this.commands.session?.() ?? '');
    const staleCandidate = !!open.recipe && !!setup?.recipe && open.recipe !== setup.recipe;
    // A setup that is only its functional check (its files are here): said as the check, from its title to its button.
    // A setup that is only its check is said as the check from start to Ready (its title and its working heading).
    const asCheck = !!setup?.plan?.steps.length && setup.plan.steps.every(s => s.kind === 'check' || s.recheck);
    const checkOnly = asCheck && setup!.ready !== true;
    if (!this.wire || (this.commands.session && !this.commands.session())) body.append(el('p', {}, 'Reading the current setup requirements…'));
    else if (!row || !setup) body.append(el('p', {}, row ? row.execution?.why ?? (row.status === 'ready' ? `${row.word}: nothing is left to set up.` : `${row.word}. This entry has no supported setup here.`) : 'Reading the added capability. If it no longer appears, close this sheet and choose it again.'));
    else if (staleSession || staleCandidate) body.append(el('p', {}, staleSession
      ? 'The session changed. Close this sheet and open setup from the session you want to use.'
      : 'The candidate changed. Close this sheet and review its current requirements.'));
    else {
      const plan = setup.plan;
      const ready = setup.ready === true;
      const working = admitted && admitted.state !== 'failed';
      const complete = ready && !working && admitted?.state !== 'failed';
      body.dataset.state = admitted?.state === 'failed' ? 'failed' : working ? 'working' : complete ? 'ready' : row.status === 'unavailable' ? 'unavailable' : 'prepare';
      // Ready to use: the lit cell and "<name> is ready" lead (the mock's added), what it does is said already.
      const usable = complete && !row.execution;
      if (!usable) body.append(el('p', { class: 'tool-setup-description' }, row.what));
      // What it is for, by the tasks it serves here (modelTasks), never the job's internal title.
      const served = this.servedTasks(row.id);
      const taskNames = served ? [...new Set(served.map(t => t.title))] : null;
      if (!usable && taskNames?.length) body.append(el('p', { class: 'tool-setup-placement' }, `Tasks: ${taskNames.join(', ')}`));
      else if (!usable && !taskNames && row.usedFor.length) body.append(el('p', { class: 'tool-setup-placement' }, `Available in ${row.usedFor.map(use => use.title).join(', ')}`));
      if (admitted?.state === 'failed') body.append(el('h4', { class: 'tool-setup-state-title' }, 'Setup couldn’t finish'));
      else if (working) body.append(el('h4', { class: 'tool-setup-state-title' }, admitted.state === 'waiting' ? 'Your next step' : `${asCheck ? 'Checking' : 'Setting up'} ${row.name}`));
      // Progress belongs to this setup's actual queues. Never infer a percentage from elapsed time or another model.
      const models = new Set([row.id, admitted?.model, ...(plan?.steps.map(s => s.model) ?? [])]);
      const activeStep = admitted?.step;
      const download = working && admitted.state === 'running' && (!activeStep || activeStep.kind === 'download')
        ? [row.download, ...(this.wire.setup?.downloads ?? [])].find(d => d && models.has(d.model)
          && (!activeStep?.model || d.model === activeStep.model) && ['queued', 'downloading', 'checking'].includes(d.state)) : undefined;
      const fileFacts = row.facts.filter(f => f.kind === 'files');
      // Downloaded only once no download or file check of this setup is still going (L7b: "✓ Downloaded" stood beside
      // "Downloading 0%" and "Checking downloaded files").
      const downloaded = fileFacts.length > 0 && fileFacts.every(f => f.ok === true) && !download;
      if (complete) body.dataset.complete = 'true';
      // Cancel before anything starts, Close while it carries on behind the sheet, Done once it is ready.
      close.textContent = complete ? 'Done' : working ? 'Close' : 'Cancel';
      // Its state in the lit cell's terms, never coloured words: Ready is the green cell with "<name> is ready" (its 240 ms
      // settle once, as it turns ready); downloaded, or checked but waiting for a review, a quiet line with its tick.
      if (usable) {
        const settle = !this.readySettled.has(identity); this.readySettled.add(identity);
        body.prepend(el('h4', { class: 'tool-setup-success tool-setup-ready', role: 'status', 'data-state': 'ready' },
          litCell('ready', { size: 16, decorative: true, settle }), el('span', { class: 'visually-hidden' }, 'Ready to use: '), el('span', {}, `${row.name} is ready`)));
      } else if (complete || downloaded) body.append(el('p', { class: 'tool-setup-success', role: 'status', 'data-state': complete ? 'review' : 'downloaded' },
        tabMark('tick', 'tool-setup-done-icon'), el('span', {}, complete ? 'Checks passed · review needed' : 'Downloaded')));
      // Ready: what is now true of it (its facts that hold), in plain words, never a step it did not take.
      const held = usable ? row.facts.filter(f => f.ok === true).map(doneWords) : [];
      // Reached from a link in Add model or tool: where it was found comes first (the mock's added), from that flow's link.
      const link = usable && open.candidate === row.id && open.values?.route === 'add' ? open.values.source?.trim() : '';
      if (link) held.unshift(linkSource({ kind: 'link', url: link }).replace(/^From /, 'Found from your link · '));
      if (held.length) body.append(el('ul', { class: 'tool-setup-done' }, ...held.map(words => el('li', {}, tabMark('tick', 'tool-setup-done-icon'), el('span', {}, words)))));
      const install = working && admitted.state === 'running' && (!activeStep || activeStep.kind === 'install')
        ? this.wire.setup?.installs?.find(i => models.has(i.model) && (!activeStep?.model || i.model === activeStep.model)
          && !i.site && ['queued', 'installing'].includes(i.state)) : undefined;
      if (download || install) {
        const progress = el('div', { class: 'tool-setup-live', role: 'status', 'aria-live': 'polite' });
        const bytesKnown = download?.state === 'downloading' && Number.isFinite(download.total) && download.total > 0;
        const got = download ? Math.max(0, Math.min(download.got, download.total || download.got)) : 0;
        const label = download?.state === 'checking' ? 'Checking downloaded files'
          : download?.state === 'queued' ? 'Waiting for the download queue'
          : install?.state === 'queued' ? 'Waiting for the installation queue'
          : download ? 'Downloading files to this Mac' : 'Preparing software and checks on this Mac';
        progress.append(el('p', {}, label));
        const bar = el('progress', { 'aria-label': label });
        if (bytesKnown) {
          bar.setAttribute('max', String(download!.total)); bar.setAttribute('value', String(got));
          progress.append(el('p', {}, `${sizeWords(got)} of ${sizeWords(download!.total)} · ${Math.floor(100 * got / download!.total)}%`));
        } else if (download?.state === 'downloading') progress.append(el('p', {}, `${sizeWords(got)} downloaded · total size not yet known`));
        progress.append(bar);
        body.append(progress);
      }
      const phase = activeStep?.kind === 'check' || activeStep?.recheck ? 'Checking this tool'
        : activeStep?.kind === 'install' ? 'Preparing software and checks on this Mac'
        : activeStep?.kind === 'download' ? 'Preparing the download' : 'Preparing setup';
      if (working && !download && !install && admitted.state !== 'waiting') body.append(
        el('p', { class: 'tool-setup-sheet-progress', role: 'status' }, phase),
        el('progress', { class: 'tool-setup-check-progress', 'aria-label': phase }));
      if (admitted?.state === 'failed') body.append(el('p', { role: 'alert' }, 'Read the latest activity in Setup details, resolve the problem, then retry setup.'));
      else if (working) body.append(el('p', { class: 'tool-setup-next' }, admitted.state === 'waiting'
        ? 'Complete the requested step below to continue.'
        : 'You can keep working. This sheet will show the next required action when setup reaches it.'));
      else if (ready && row.execution) body.append(el('p', {}, row.execution.action === 'review-bundle'
        ? 'Preparation is complete. Review the recorded results before enabling this tool.' : row.execution.why));
      // The queue supplies its latest message, not a complete log or a fine-grained install phase.
      const details = el('details', { class: 'tool-setup-details', open: open.detailsOpen ?? admitted?.state === 'failed' }, el('summary', {}, 'Setup details'));
      details.addEventListener('toggle', () => { open.detailsOpen = details.open; });
      const messages = [...new Set([admitted?.says, download?.says, install?.says, ready ? row.execution?.why : undefined].filter((message): message is string => !!message))];
      if (messages.length) details.append(el('h4', {}, 'Latest setup activity'),
        el('p', {}, 'Updates while this sheet is open. This is the latest reported status, not a complete log.'),
        ...messages.map(message => el('p', { class: 'tool-setup-diagnostic' }, message.length > 4000 ? `${message.slice(0, 4000)}… (message truncated)` : message)));
      if (install?.folder) details.append(el('p', { class: 'tool-setup-destination' }, 'Software location on this Mac: ', el('code', {}, install.folder)));
      if (working) {
        body.append(el('p', { class: 'tool-setup-sheet-background' }, 'Close hides this sheet; setup continues. Activity shows its progress under Setting up, and Continue setup on its card reopens this sheet. Stop setup cancels only this attempt.'));
        if (admitted.attempt) acts.append(press('Stop setup', () => commands.cancel(open.role, admitted.attempt!), 'tool-stop'));
      }
      if (plan) {
        const facts = sheetFacts(plan, row, rows, keys, this.wire?.setup?.disk);
        // Include the active destination even before its queue row has a final folder.
        const destinations = new Set(install?.folder ? [install.folder] : []);
        for (const s of plan.steps) {
          const target = rows.find(r => r.id === s.model) ?? row;
          const folder = s.kind === 'download' ? target.folder || target.facts.find(f => f.kind === 'files')?.ref
            : s.kind === 'install' && !s.onAgain && !s.recheck ? target.installFolder || target.facts.find(f => f.kind === 'code' && f.ref)?.ref : '';
          if (folder && !destinations.has(folder)) {
            destinations.add(folder);
            details.append(el('p', { class: 'tool-setup-destination' }, s.kind === 'download' ? 'Download location on this Mac: ' : 'Software location on this Mac: ', el('code', {}, folder)));
          }
        }
        // This is a refreshed requirements plan, not the admitted attempt's remaining queue.
        // Automatic requirements may still describe work already in progress.
        const remaining = plan.steps.filter(s => !(working && admitted.state !== 'waiting' && s.by === 'app'));
        if (checkOnly && !working) {
          body.append(el('section', { class: 'tool-setup-steps', 'data-check': 'true' }, el('h4', {}, 'The check'),
            el('p', {}, row.runs === 'cloud' ? 'The check runs it on the public example to prove it works from this Mac'
              : 'Its files are here; the check runs it on the public example to prove it works on this Mac')));
        } else if (remaining.length && (!ready || working)) {
          const steps = el('section', { class: 'tool-setup-steps' }, el('h4', {}, working ? 'Setup requirements' : 'To use this tool'),
            el('ol', {}, ...remaining.map(s => el('li', { 'data-owner': s.by,
              'data-active': String(working && admitted.step?.kind === s.kind && admitted.step?.model === s.model) },
              el('div', {}, s.says, el('span', { class: 'tool-setup-owner' }, s.by === 'app' ? 'Automatic' : 'Needs you'))))));
          body.append(steps);
        }
        // What it takes, before anything runs (the mock's add-review): its size (or that it isn't known here), the room for
        // it, where pictures go, its licence and what it costs, each a line of its own in the catalog's words.
        const fact = (mark: TabMark, words: string, attrs: Record<string, string> = {}) =>
          el('li', attrs, tabMark(mark, 'tool-setup-fact-icon'), el('span', {}, words));
        // Ready: one quiet line of what holds (the mock's added: "413 MB on this Mac · pictures stay on this Mac · licence
        // MIT · free"); before, the list of what it takes, its licence by its short name (in full under Setup details).
        const lower = (words: string) => words.charAt(0).toLowerCase() + words.slice(1);
        const factList = usable
          ? el('p', { class: 'tool-setup-quiet', 'data-fact': 'held' }, [row.size ? `${sizeWords(row.size)} on this Mac` : '', facts.where,
            lower(facts.licenceShort), facts.cost].filter(Boolean).map((words, i) => i ? lower(words) : words).join(' · '))
          : el('ul', { class: 'tool-setup-facts' },
            // "Nothing to download." beside "✓ Downloaded" says the same twice: only one of them.
            ...(!ready || working ? [...(downloaded && facts.download === 'Nothing to download.' ? [] : [fact('download', facts.download, { 'data-fact': 'size' })]),
              ...(facts.space ? [fact('measure', facts.space, { 'data-fact': 'space', 'data-fits': String(facts.fits !== false), role: facts.fits === false ? 'alert' : 'status' })] : [])] : []),
            fact('eyepiece', facts.where, { 'data-fact': 'where' }), fact('cost', facts.cost, { 'data-fact': 'cost' }));
        // Its licence: a title and one quiet line, its short name (in full under Setup details).
        const licenceBlock = usable ? null : el('section', { class: 'tool-setup-licence', 'data-fact': 'licence' }, el('h4', {}, 'Licence'),
          el('p', { class: 'tool-setup-quiet' }, facts.licenceShort.replace(/^Licence /, '').replace(/^Licence not stated$/, 'Not stated in the catalog')));
        // First, ahead of its steps (the mock's add-review: what it takes, then how); once ready, under its heading.
        const steps = body.querySelector<HTMLElement>('.tool-setup-steps');
        const done = body.querySelector<HTMLElement>('.tool-setup-done');
        if (usable && done) body.insertBefore(factList, done);
        else if (steps) body.insertBefore(factList, steps); else body.append(factList);
        if (licenceBlock) factList.after(licenceBlock);
        details.append(el('p', { class: 'tool-setup-licence-full' }, `Licence: ${facts.licence}`));
        const step = working ? admitted.step : plan.steps[0];
        const key = step?.kind === 'key' ? Object.values(keys ?? {}).find(k => k.service === step.service) : undefined;
        if (key) {
          const identity = [key.service, key.source, key.canSave, key.present].join(':');
          if (this.inlineKey?.identity !== identity) {
            this.inlineKey?.form.dispose();
            this.inlineKey = { identity, form: keyForm({ ...key, enables: '' }, () => { void this.commands.observe?.(); }) };
          }
          body.append(this.inlineKey.form.element);
        } else {
          this.inlineKey?.form.dispose(); this.inlineKey = null;
          if (step?.by === 'you') body.append(yourControl(step, keys, { ...commands, open: id => this.show(id),
            confirm: this.commands.confirm, trust: this.commands.channelTrust ? c => this.commands.channelTrust!(c, true) : undefined }, status) ?? '');
        }
        details.append(...facts.time.map(t => el('p', {}, t)), ...facts.licenceLinks.map(l => el('a', { href: l.url, target: '_blank', rel: 'noopener noreferrer' }, l.says)));
        const automatic = plan.steps.filter(s => s.by === 'app');
        const buttonLabel = automatic.some(s => s.kind === 'install' && !s.onAgain && !s.recheck) ? 'Prepare and turn on'
          : automatic.some(s => s.kind === 'download') ? `Download${facts.bytes ? ` ${sizeWords(facts.bytes)}` : ''} and turn on`
          : automatic.some(s => s.kind === 'check' || s.recheck) ? 'Check and turn on' : 'Turn on';
        // Nothing starts until its primary is pressed; its label says what starts ("Download 413 MB and turn on").
        if (!working && facts.fits !== false && !ready) acts.append(press(admitted?.state === 'failed' ? 'Retry setup' : ready ? 'Check again' : checkOnly ? 'Run the check' : buttonLabel,
          () => commands.turnOn(setup.role, plan, setup.recheck), `${ready ? '' : 'mm-primary '}tool-setup-sheet-go`));
      } else {
        this.inlineKey?.form.dispose(); this.inlineKey = null;
        if (!ready) {
          body.append(el('p', { class: 'tool-setup-unavailable' }, row.facts.some(f => f.prepare)
            ? 'Prepare this tool for review. Download its files and prepare dependencies below; these steps do not enable it or qualify its results.'
            : row.execution?.why ?? `${row.word}. This tool is not ready for ordinary execution.`));
          body.append(this.facts(row, keys));
        }
      }
      // Ready and it serves a task here: Try it on <specimen> ends the sheet (below), so the trials stay in Details.
      const tasks = complete && !row.execution && !this.wire.setup?.maintenance?.[row.id]?.workflowReview ? this.servedTasks(row.id) ?? [] : [];
      const offersTry = tasks.length > 0;
      if (ready && !working) {
        const updateReview = this.wire.setup?.maintenance?.[row.id]?.workflowReview;
        if (row.execution || updateReview) {
          if ((updateReview || row.execution?.action === 'review-bundle') && this.commands.reviewBundle) {
            const cachedReview = this.bundleReviews.get(row.id);
            const review = cachedReview && (!updateReview || cachedReview.generation === updateReview) ? cachedReview : undefined;
            body.append(press(review ? 'Refresh result review' : updateReview ? 'Review update results' : 'Review results', async () => {
              this.assertFlow(open);
              const result = await this.commands.reviewBundle!(row.id, open.session!, updateReview ? {generation:updateReview} : undefined);
              this.assertFlow(open); this.bundleReviews.set(row.id, result); this.render();
            }));
            if (review) {
              body.append(el('p', {}, review.says));
              for (const check of review.cases) {
                const section = el('section', { class: 'tool-bundle-review' },
                  el('h3', {}, row.memberKinds?.find(m => m.key === check.member)?.name ?? check.member),
                  el('p', { class: 'tool-setup-success' }, check.kind === 'workflow' ? '✓ Local controls, Save and reopen observed' : check.kind === 'reader' ? '✓ Exact reader data check passed' : `✓ Reference check passed${check.device ? ` on ${check.device.toUpperCase()}` : ''}`), el('p', {}, check.source));
                const results = el('div', { class: 'tool-bundle-results' });
                for (const role of ['input', 'actual', 'reference'] as const) {
                  const artifact = check.artifacts[role];
                  const url = `/api/capability-review-artifact?${new URLSearchParams({model: row.id, generation: review.generation,
                    digest: review.digest, case: check.case, role})}`;
                  const label = check.kind === 'workflow' ? (role === 'actual' ? 'Reopened result' : role === 'reference' ? 'Saved state' : 'Captured input') : role === 'actual' ? 'Actual result' : role === 'reference' ? 'Expected reference' : 'Example input';
                  const figure = el('figure', {}, el('figcaption', {}, label));
                  if (artifact.kind === 'image') figure.append(el('img', { src: url, alt: label, loading: 'lazy', width: '240' }));
                  else figure.append(reviewPreview(artifact.preview ?? '', artifact.truncated));
                  figure.append(el('a', { href: url, target: '_blank', rel: 'noopener noreferrer' }, `Open complete ${label.toLowerCase()}`)); results.append(figure);
                }
                section.append(results, el('details', {}, el('summary', {}, 'Checked settings and comparison'),
                  el('pre', {}, JSON.stringify(check.kind === 'workflow' ? {input:check.inputs, saved:check.saved, reopened:check.reopened} : check.kind === 'reader' ? {inputs:check.inputs, referenceSha256:check.referenceSha256} : {parameters: check.parameters, tolerance: check.tolerance, metrics: check.metrics}, null, 2))));
                body.append(section);
              }
              acts.append(press(updateReview ? 'Enable this update for Apply' : 'Enable these tools', async () => {
                this.assertFlow(open);
                await this.commands.reviewBundle!(row.id, open.session!, {generation: review.generation, digest: review.digest});
                this.assertFlow(open); this.bundleReviews.delete(row.id); await this.commands.observe?.(row.id);
              }, 'mm-primary'));
            }
          } else if (row.execution?.action === 'compare') {
            const comparisons = this.ownTrialsSection(row.id); if (comparisons) body.append(comparisons);
          } else if (row.execution?.action === 'choose-comparison-input' && this.commands.ownPicture && this.commands.turnOnModel) {
            const picture = el('input', { type: 'file', accept: 'image/png,image/jpeg', 'aria-label': 'Comparison picture' }) as HTMLInputElement;
            const holder = filePicker(picture);
            holder.dataset.modelEditor = `setup-picture:${row.id}:${open.session}`; // the chosen file is kept across renders
            body.append(holder, press('Prepare comparison', async () => {
              const file = this.root.querySelector<HTMLInputElement>('[aria-label="Comparison picture"]')?.files?.[0];
              if (!file) throw new Error('Choose a PNG or JPEG comparison picture first.');
              const trial = await this.commands.ownPicture!(file, row.execution!.job!);
              if (open.session !== this.commands.session?.()) throw new Error('The session changed. Reopen setup.');
              await this.commands.turnOnModel!(row.id, trial.id);
            }));
          }
        } else if (row.memberKinds?.length && setup.role.startsWith('model:')) {
          // Ready with Try it on (below): its trials stay in Details (More about this model), not repeated here.
          if (!offersTry) for (const member of row.memberKinds) body.append(this.memberTrial(row, member, 'setup'));
        } else if (row.newKind && setup.role.startsWith('model:')) {
          if (!offersTry) {
            const trial = this.kindTrial(row, 'setup'); const run = trial.querySelector<HTMLButtonElement>('button.tool-kind-try');
            if (run) { run.textContent = 'Run now'; run.classList.add('mm-primary'); run.disabled ||= this.setupPending.has(pending); } body.append(trial);
          }
        }
        else {
          const role = this.wire?.roles.find(r => r.id === setup.role);
          const current = role?.options?.find(o => o.id === role.current);
          if (role && !role.fixed && setup.current === setup.option) body.append(el('p', {}, `${setup.title} uses ${row.name}.`));
          const canSelect = role && !role.fixed && setup.current !== setup.option && setup.recipe && this.commands.selectCapability;
          if (canSelect) {
            // "Use <name>" answers its own line ("Cells keeps using Cellpose-SAM."): beside it, not in the sheet's last row.
            const broken = current?.readiness?.status !== 'ready';
            body.append(el('div', { class: 'tool-setup-select-row' }, el('p', {}, broken ? `${setup.title} uses ${current?.label ?? setup.current}, which can’t run here. Use ${row.name}?`
              : `${setup.title} keeps using ${current?.label ?? setup.current}.`),
              press(`Use ${row.name}`, () => this.commands.selectCapability!({ job: setup.role, option: setup.option,
                session: open.session, current: setup.current ?? '', recipe: setup.recipe! }), broken ? 'mm-primary tool-setup-select' : 'tool-setup-select')));
          }
          const tryPrimary = !(canSelect && current?.readiness?.status !== 'ready');
          if (row.newKind && !offersTry) {
            const trial = this.kindTrial(row, 'setup'), run = trial.querySelector<HTMLButtonElement>('button.tool-kind-try');
            if (run) { run.textContent = `Try ${row.name}`; if (tryPrimary) run.classList.add('mm-primary'); run.disabled ||= this.setupPending.has(pending); }
            body.append(trial);
          } else if (setup.canTry && !offersTry) {
            acts.append(press(`Try ${row.name}`, () => commands.tryIt(setup.role, setup.option, false), tryPrimary ? 'mm-primary' : ''));
            if (this.commands.chooseInput) body.append(press('Choose a picture or video', async () => { this.commands.chooseInput?.(); }));
          }
        }
      }
      // Ready, and it serves a task here: its primary becomes Use it here ("Try it on Human cells"), landing on that task's
      // run page with it chosen (the run page whose step opened the sheet, when it serves that task; else its first); the
      // last row is that and Done. Choosing it for the job stays the primary only while the job's current choice can't run.
      if (tasks.length) {
        const opener = tasks.find(t => t.task === open.task);
        const select = body.querySelector('.tool-setup-select.mm-primary') ?? acts.querySelector('.tool-setup-select.mm-primary');
        if (!select) for (const b of [...body.querySelectorAll('.mm-primary'), ...acts.querySelectorAll('.mm-primary')]) b.classList.remove('mm-primary');
        const go = el('button', { type: 'button', class: `mm-control tool-setup-use${select ? '' : ' mm-primary'}`, title: `${(opener ?? tasks[0]).title} with ${row.name}` }, this.tryWords());
        go.addEventListener('click', () => { this.closeSheet(); saveFlow(null); this.retainedFlow = null; this.useHere(row.id, opener?.task); });
        acts.append(go);
      }
      details.append(press('Check requirements again', async () => { await this.commands.observe?.(); }));
      body.append(details);
    }
    // Reached from Add model or tool (its candidate): the same sheet, still called that, through to Ready.
    return this.sheetFrame(open.candidate && open.candidate === open.model ? 'Add model or tool'
      : asCheck && row ? `Check ${row.name} on this Mac` : `Set up ${row?.name ?? open.model}`, body, status, acts);
  }

  private sheetFrame(title: string, body: HTMLElement, status: HTMLElement, acts: HTMLElement): HTMLElement {
    const open = this.sheet!;
    status.classList.add('tool-setup-feedback');
    const x = el('button', { type: 'button', class: 'tool-setup-sheet-x', 'aria-label': 'Close', title: 'Close' }, tabMark('x', 'tool-setup-x-icon'));
    x.addEventListener('click', () => this.closeSheet());
    // The one primary first, Cancel, Close or Done last (the mock's add-actions), in reading and focus order alike.
    const leave = acts.querySelector<HTMLElement>('.tool-setup-sheet-cancel') ?? acts.querySelector<HTMLElement>('.tool-setup-sheet-close'); if (leave) acts.append(leave);
    const dialog = el('div', { class: 'tool-setup-sheet', role: 'dialog', 'aria-modal': 'true', tabindex: '-1',
      'aria-labelledby': 'tool-setup-sheet-title', 'data-role': open.role, 'data-tool': open.model },
      el('header', { class: 'tool-setup-heading' }, el('h3', { id: 'tool-setup-sheet-title' }, title), x), body, status, acts);
    dialog.addEventListener('keydown', event => {
      if (event.key !== 'Tab') return;
      const stops = [...dialog.querySelectorAll<HTMLElement>('a, button, input, select, textarea, summary')]
        .filter(n => {
          const fold = n.closest('details');
          return n.getAttribute('tabindex') !== '-1' && !n.hasAttribute('disabled') && !n.closest('[hidden]') && (!fold || fold.open || n.tagName === 'SUMMARY');
        });
      if (!stops.length) return;
      if (event.shiftKey && (document.activeElement === stops[0] || document.activeElement === dialog)) {
        event.preventDefault(); stops[stops.length - 1].focus();
      } else if (!event.shiftKey && document.activeElement === stops[stops.length - 1]) { event.preventDefault(); stops[0].focus(); }
    });
    return el('div', { class: 'tool-setup-sheet-backdrop' }, dialog);
  }

  private faceState(row: ModelRow): FaceState {
    return faceState(row, this.wire?.setup);
  }

  /** Whether this studio is the workspace (Hooke), where Use it here goes to a run page. */
  private inWorkspace(): boolean {
    return typeof document !== 'undefined' && document.body?.dataset?.workspaceStudy === 'true';
  }

  /** The tasks a model serves here, by Use it here's order; null when the workspace has not said (outside it). */
  private servedTasks(model: string): { task: string; title: string }[] | null {
    return this.commands.served && this.inWorkspace() ? this.commands.served(model) : null;
  }

  /** Use it here, or Try on <specimen> for one task: back to the workspace on that task's run page with this model chosen
   * and the Stage's picture as its input (workspace-study.ts `hooke-use-model`, actions-panel.ts `useModel`). Nothing runs. */
  private useHere(model: string, task?: string): void {
    if (typeof window !== 'undefined' && typeof window.dispatchEvent === 'function') window.dispatchEvent(new CustomEvent('hooke-use-model', { detail: task ? { model, task } : { model } }));
  }

  /** "Try it on Human cells" when the Stage shows a picture by name, else "Use it here". */
  private tryWords(): string {
    const name = this.commands.specimen?.().trim() ?? '';
    return name ? `Try it on ${name}` : 'Use it here';
  }

  /** One card shape for every entry (the mock's mcard): its lit cell and name, what it is and where it runs, what it does
   * in two lines, one facts line, one action at the bottom; a click anywhere on it opens its Details beside the grid. Its
   * section, cell, words and verb are model-status.ts `cardLook`'s (one verb per state); its steps, terms and facts are in
   * its setup sheet and Details. */
  private card(row: ModelRow, keys: Record<string, KeyFacts> | undefined): HTMLElement {
    const state = this.faceState(row);
    const served = this.servedTasks(row.id);
    const look = cardLook(row, this.wire, { inWorkspace: this.inWorkspace(), served: served ? served.length : null, shelf: !!this.commands.shelf });
    const card = el('article', { class: 'model-card', 'data-model': row.id, 'data-status': state.state, 'data-section': look.section,
      'data-runs': row.runs === 'cloud' ? 'cloud' : 'mac', 'data-selected': String(this.selected === row.id),
      tabindex: '-1', 'aria-labelledby': `model-${row.id}-name` });
    const open = el('button', { type: 'button', class: 'tool-details-open model-card-hit', 'aria-expanded': String(this.selected === row.id),
      'aria-controls': 'tool-details', 'aria-label': `Details for ${row.name}` });
    open.addEventListener('click', () => this.openDetails(row.id));
    // The card's own step's failure stays said only while that step is still what it offers.
    if (!['download', 'retry'].includes(look.verb.kind)) this.cardMessages.delete(row.id);
    const said = this.cardMessages.get(row.id);
    // The face's own words, on hover anywhere on the card (its words lie over the hit button and let clicks through).
    if (look.says && look.says !== look.facts) open.title = look.says;
    card.append(open,
      el('div', { class: 'model-card-top' }, litCell(look.cell, { size: 13, label: look.cellWord, fraction: look.fraction }),
        el('b', { class: 'model-card-name', id: `model-${row.id}-name` }, row.name)),
      el('span', { class: 'model-card-kind' }, look.kind),
      el('p', { class: 'model-what' }, row.what || ''),
      el('p', { class: 'model-card-facts tool-state', title: look.says && look.says !== look.facts ? look.says : '', 'data-said': String(!!said) }, said ?? look.facts),
      // Its setup's role on the action, so a closed sheet gives focus back to the press that opened it (closeSheet).
      el('div', { class: 'model-card-action', ...(look.setup ? { 'data-role': look.setup.role } : {}) }, ...this.cardAction(row, look, keys)));
    return card;
  }

  /** The card's one action, from its verb. */
  private cardAction(row: ModelRow, look: CardLook, keys: Record<string, KeyFacts> | undefined): HTMLElement[] {
    const button = (label: string, cls: string, click: () => void) => {
      const b = el('button', { type: 'button', class: `mm-btn btn-sm ${cls}` }, label);
      b.addEventListener('click', click);
      return b;
    };
    const failed = (error: unknown) => { this.cardMessages.set(row.id, error instanceof Error ? error.message : String(error)); this.render(); };
    const verb = look.verb;
    switch (verb.kind) {
      case 'use': return [button('Use it here', 'model-use-here', () => this.useHere(row.id))];
      case 'note': return verb.words ? [el('span', { class: 'model-card-note' }, verb.words)] : [];
      case 'execution': { const b = this.executionAction(row); if (b) b.className = 'mm-btn btn-sm model-action'; return b ? [b] : []; }
      case 'progress': {
        const words = el('span', { class: 'model-card-progress', role: 'status' }, verb.words);
        if (!row.download || !['queued', 'downloading', 'checking'].includes(row.download.state)) return [words];
        const cancel = button('Cancel', 'btn-quiet model-card-cancel', () => { cancel.disabled = true; void this.commands.download(verb.model, true).catch(failed); });
        cancel.title = `Stop downloading ${row.name}`;
        return [words, cancel];
      }
      case 'continue': return [button('Continue setup', 'tool-setup-act', () => { this.openSetup(row, look.setup!, false); this.render(); })];
      case 'retry': {
        const retry = verb.download
          ? button('Retry', 'model-action', () => { retry.disabled = true; this.cardMessages.delete(row.id); void this.commands.download(verb.download!).catch(failed); })
          : button('Retry', 'tool-setup-act', () => { this.openSetup(row, look.setup!, false); this.render(); });
        const details = button('Details', 'btn-quiet model-card-details', () => this.openDetails(row.id));
        return [retry, details];
      }
      case 'download': {
        // Started here; the card fills in place from the server's download row (measured bytes), and Tools and the run
        // page show the same download.
        const b = button(verb.label, 'model-action model-card-download', () => {
          b.disabled = true; b.textContent = 'Starting…'; this.cardMessages.delete(row.id);
          void this.commands.download(verb.model).catch(failed);
        });
        b.title = `Download ${row.name}'s files to this Mac`;
        return [b];
      }
      case 'sheet': {
        const b = button(verb.label, 'tool-setup-act', () => { this.openSetup(row, look.setup!, false); this.render(); });
        b.disabled = !!this.setupPending.size && [...this.setupPending].some((k) => k.startsWith(`${look.setup!.role}:${look.setup!.option}:`));
        return [b];
      }
      case 'direct': {
        const node = this.actionNode(row, keys);
        // An install only the person can carry out (a command of its own): how, in its Details, never a dead-end button.
        if (!node && action(row).kind === 'install') return [button('How to install', 'model-card-how', () => this.openDetails(row.id, true))];
        if (!node) return [];
        for (const b of ['BUTTON', 'A'].includes(node.tagName) ? [node] : [...node.querySelectorAll<HTMLElement>('button, a')]) b.classList.add('mm-btn', 'btn-sm');
        return [node];
      }
      default: return [];
    }
  }

  /** Details, beside the grid (the mock's modelDetail): its name and ×, what it is and does, its Tasks (one row each, with
   * Try it on <specimen> where it leads to a run page), its Size, Licence and Pictures; a candidate's preparation, its next
   * step, stays in view. Everything else it holds (capabilities, setup and trial controls, alternatives, facts and files,
   * versions, updates, Turn off, Remove, Share, Report) is under a closed "More about this model", one press away. */
  private toolDetails(row: ModelRow, keys: Record<string, KeyFacts> | undefined): HTMLElement {
    const close = el('button', { type: 'button', class: 'tool-details-close', 'aria-label': 'Close details', title: 'Close details' }, tabMark('x', 'tool-details-x-icon'));
    close.addEventListener('click', () => this.closeDetails());
    const pane = el('aside', { class: 'tool-details', id: 'tool-details', 'data-tool': row.id, tabindex: '-1',
      'aria-label': `${row.name} details` }, el('header', {}, el('h3', {}, row.name), close),
      el('p', { class: 'model-card-kind' }, cardKindWords(row, this.wire)), el('p', { class: 'tool-details-what' }, row.what || ''));
    const state = this.faceState(row);
    if (state.state === 'unavailable') pane.append(el('p', { class: 'tool-state', role: 'status' }, state.says));
    const preparationFacts = row.facts.filter(f => f.prepare || f.trust);
    if (preparationFacts.length) pane.append(el('section', { class: 'tool-preparation', 'aria-label': 'Prepare this package' },
      el('h4', {}, 'Prepare for review'),
      el('p', {}, 'Prepare this package on this computer, then return to your data to review it. Preparation does not enable or independently qualify the tool.'),
      this.facts(row, keys, preparationFacts, 'details')));
    // Tasks: one row per task a person can run here (by its name, once), each with Try it on <specimen> when the model can
    // run (Ready or Not checked): its run page with this model chosen. Capabilities only the Assistant uses are not tasks.
    const runnable = ['ready', 'unchecked'].includes(modelSection(row)) && !row.execution;
    const seen = new Set<string>();
    const tasks = (this.servedTasks(row.id) ?? []).filter((t) => !seen.has(t.title) && !!seen.add(t.title));
    if (tasks.length) pane.append(el('h4', {}, 'Tasks'), el('ul', { class: 'tool-tasks' }, ...tasks.map((t) => {
      const item = el('li', {}, el('span', { class: 'tool-task-name' }, t.title));
      if (runnable) {
        // Its whole words in its tooltip too: a long specimen name ends in an ellipsis on the button.
        const go = el('button', { type: 'button', class: 'mm-btn btn-sm tool-task-try', title: `${this.tryWords()}: ${t.title} with ${row.name}` }, this.tryWords());
        go.addEventListener('click', () => this.useHere(row.id, t.task));
        item.append(go);
      }
      return item;
    })));
    // Not checked, said as what it means beside Try it on: Hooke's own check is a record of its own (contract/AGENTS.md
    // "The app's own check"), which a run of yours on your picture does not make, so a model that just worked stays here.
    if (modelSection(row) === 'unchecked' && !row.execution) pane.append(el('p', { class: 'tool-check-note' },
      'Not checked: Hooke’s own check of it (one run on the public example) is not recorded on this Mac yet. Trying it here runs it on your picture, which does not record that check; Check it here in its setup does.'));
    const cloud = row.runs === 'cloud';
    const fact = (term: string, value: string) => el('div', {}, el('dt', {}, term), el('dd', {}, value));
    pane.append(el('dl', { class: 'tool-details-facts' },
      fact('Size', row.size ? sizeWords(row.size) : cloud ? 'Nothing to download' : 'Not known yet'),
      fact('Licence', licenceName(row.licence, row.tier) || 'Not stated in the catalog'),
      fact('Pictures', cloud ? `Go to ${keyOf(this.wire, row)?.short || (row.where === 'in the cloud' ? 'the cloud' : row.where)}` : 'Stay on this Mac')));
    const more = el('details', { class: 'tool-details-more', open: this.moreOpen.has(row.id) }, el('summary', {}, 'More about this model'));
    more.addEventListener('toggle', () => { if ((more as HTMLDetailsElement).open) this.moreOpen.add(row.id); else this.moreOpen.delete(row.id); });
    pane.append(more);
    const demo = row.demonstration;
    if (demo) more.append(el('figure', { class: 'tool-demo' }, demo.kind === 'video'
      ? el('video', { src: demo.url, controls: true, preload: 'none' })
      : el('img', { src: demo.url, alt: demo.caption, loading: 'lazy' }), el('figcaption', {}, demo.caption)));
    // Every capability it has, the Assistant's too, as the catalog says them (the tasks above are the ones a person runs).
    const abilities = row.abilities ?? [];
    if (abilities.length) more.append(el('h4', {}, 'What it can do'), el('ul', { class: 'tool-abilities' }, ...abilities.map(a =>
      el('li', {}, el('span', { class: 'tool-ability-words' }, el('strong', {}, a.title), el('p', {}, a.what))))));
    const how = action(row);
    if (how.kind === 'install') more.append(el('p', { class: 'model-how' }, how.how));
    if (row.newKind) more.append(this.kindTrial(row));
    for (const member of row.memberKinds ?? []) more.append(this.memberTrial(row, member, 'details'));
    for (const setup of row.setups ?? []) more.append(this.setupControl(row, setup, keys, true));
    for (const use of row.usedFor) { const alternatives = this.alternatives(use.role); if (alternatives) more.append(alternatives); }
    more.append(el('h4', {}, 'Facts and files'), this.facts(row, keys, row.facts.filter(f => !preparationFacts.includes(f))), this.details(row));
    const versions = this.versionsNode(row); if (versions) more.append(versions);
    const update = this.wire?.setup?.maintenance?.[row.id];
    const kept = maintenanceNode(row.id, update, this.commands.maintenance, this.maintenanceMemory); if (kept) more.append(kept);
    const updateSetup = row.setups?.find(setup => setup.role === `model:${row.id}`);
    if (update?.workflowReview && updateSetup && this.commands.reviewBundle) {
      const review = el('button', {type:'button',class:'mm-control'}, 'Review update results');
      review.addEventListener('click', () => { this.openSetup(row, updateSetup, true); this.render(); }); more.append(review);
    }
    if (isOn(row)) { const off = this.actionNode(row, keys); if (off) more.append(off); }
    if (row.env?.installed && this.commands.uninstall) more.append(this.removeNode(row));
    const retained = (kind: string) => [...this.root.querySelectorAll<HTMLElement>('[data-model-editor]')]
      .find(n => n.dataset.modelEditor === `${kind}:${row.id}:${row.revision}:${row.version}`);
    if (row.shareable) more.append(retained('share') ?? shareFold(row, this.commands));
    if (this.commands.report) more.append(retained('report') ?? reportFold(row, this.commands));
    return pane;
  }

  private readonly tryOptions = new Set<string>();
  private readonly tryValues = new Map<string, Record<string, unknown>>();
  private readonly retainedChoices = new Map<string, RetainedInput[]>();
  private readonly retainedSelected = new Map<string, RetainedInput>();

  private memberTrial(row: ModelRow, member: NonNullable<ModelRow['memberKinds']>[number], scope: string): HTMLElement {
    const trial = this.kindTrial({ ...row, id: member.key, name: member.name, newKind: member }, scope, row);
    const title = trial.querySelector('h4'); if (title) title.textContent = member.name;
    return trial;
  }

  private kindTrial(row: ModelRow, scope = 'card', reviewOwner = row): HTMLElement {
    const kind = row.newKind!;
    const session = this.commands.session?.() ?? '';
    const key = `kind:${session}:${row.id}`;
    const input = { 'image.2d.rgb8': 'current still image', 'clip.2d.rgb8': 'recent frames',
      'volume.3d': 'selected volume' }[kind.input];
    const part = el('section', { class: 'tool-kind-trial' }, el('h4', {}, 'Try this tool'),
      el('p', {}, `Input: ${input ?? kind.input}. Gives ${kind.gives} (${kind.output}).`));
    if (kind.proof) part.append(el('p', {}, `Active version ${kind.activeVersion}; ${kind.proof}.`));
    else if (kind.activeVersion) part.append(el('p', {}, `Active version ${kind.activeVersion}; its adapter passed a functional check, not scientific qualification.`));
    if (row.execution) {
      part.append(el('p', {}, row.execution.why));
      const next = this.executionAction(row.execution.action === 'review-bundle' ? reviewOwner : row);
      if (next) part.append(next);
      return part;
    }
    if (!input || !kind.proved || row.status !== 'ready' || !this.commands.tryKind) return part;
    const fields = this.wire?.capabilities.find(c => c.catalog === row.id && c.actor === 'agent')?.fields ?? [];
    const valuesKey = `${key}:${row.revision}:${row.version}:${kind.activeVersion ?? ''}`;
    const values = this.tryValues.get(valuesKey) ?? {};
    this.tryValues.set(valuesKey, values);
    const editor = el('div', { 'data-model-editor': `${scope}:try:${valuesKey}:${JSON.stringify(fields)}` });
    const more = el('details', { class: 'action-options' }, el('summary', {}, 'More options'));
    more.open = this.tryOptions.has(valuesKey);
    more.addEventListener('toggle', () => { if (more.open) this.tryOptions.add(valuesKey); else this.tryOptions.delete(valuesKey); });
    for (const field of fields) {
      const control = parameterControl(`try-${scope}-${row.id}`, field, values);
      (field.advanced ? more : editor).append(control);
    }
    if (fields.some(f => f.advanced)) editor.append(more);
    part.append(editor);
    const retained = this.retainedSelected.get(key);
    if (kind.input === 'image.2d.rgb8' && !['labels.instance.2d', 'tracks.graph'].includes(kind.output) && this.commands.inputResources) {
      const picker = el('select', { class: 'mm-control tool-retained-input', 'aria-label': `Input for ${row.name}` },
        el('option', { value: '' }, 'Current still image'),
        ...[...(this.retainedChoices.get(key) ?? []), ...(retained && !(this.retainedChoices.get(key) ?? []).some(item => JSON.stringify(item.reference) === JSON.stringify(retained.reference)) ? [retained] : [])].map(item => el('option', { value: JSON.stringify(item.reference) }, item.title)));
      picker.value = retained ? JSON.stringify(retained.reference) : '';
      picker.addEventListener('change', () => {
        const item = this.retainedChoices.get(key)?.find(value => JSON.stringify(value.reference) === picker.value);
        if (item) this.retainedSelected.set(key, item); else this.retainedSelected.delete(key);
        this.render();
      });
      const read = el('button', { type: 'button', class: 'model-fact-act' }, 'Find retained results');
      read.addEventListener('click', () => {
        read.disabled = true; read.textContent = 'Reading retained results…';
        void this.commands.inputResources!(session, row.id).then(reply => {
          if (this.commands.session?.() !== session) return;
          this.retainedChoices.set(key, reply.inputs);
          this.setupMessages.set(key, reply.inputs.length ? 'Choose an original result above. Its exact saved pixels will be used.' : 'No supported retained image results in this session yet. Run an image capability first.');
        }).catch((error: unknown) => { this.setupMessages.set(key, error instanceof Error ? error.message : String(error)); })
          .finally(() => this.render());
      });
      part.append(picker, read);
      if (retained) part.append(el('p', {}, `Using ${retained.title}. Its original pixels are checked again before running; the stage stays as it is.`));
      if (retained?.compatibility) part.append(el('p', { role: 'status' }, retained.compatibility.says));
    }
    let volume: VolumeEntry | undefined;
    if (kind.input === 'volume.3d') {
      const volumes = this.volumes.filter(v => v.kind === 'volume' && v.day === session);
      const selected = this.volumeChoices.get(row.id) ?? '';
      volume = volumes.find(v => v.id === selected);
      const select = el('select', { class: 'mm-control tool-volume-select', 'aria-label': 'Volume for this tool' },
        el('option', { value: '' }, 'Choose a volume…'), ...volumes.map(v => el('option', { value: v.id }, v.title)));
      select.value = volume?.id ?? '';
      select.addEventListener('change', () => { this.volumeChoices.set(row.id, select.value); this.render(); });
      const edit = el('button', { type: 'button', class: 'model-fact-act tool-volume-edit' }, 'Add a volume or complete its facts');
      edit.addEventListener('click', () => {
        this.selected = null; this.render();
        const section = this.root.querySelector<HTMLElement>('.volumes-section');
        section?.scrollIntoView?.({ block: 'start' }); section?.querySelector<HTMLInputElement>('input')?.focus();
      });
      part.append(select, edit);
    }
    const missing = volume && Object.values(volumeNeeds(volume, false)).some(Boolean);
    const run = el('button', { type: 'button', class: 'mm-control tool-kind-try' }, retained ? 'Run on selected result' : `Try this tool on the ${input}`);
    run.disabled = !session || this.setupPending.has(key) || (kind.input === 'volume.3d' && (!volume || Boolean(missing)));
    const said = el('p', { role: 'status' }, this.setupMessages.get(key) ?? (missing ? 'Complete the selected volume’s facts below first.' : ''));
    run.addEventListener('click', () => {
      if (run.disabled) return;
      const invalid = parameterError(fields, values);
      if (invalid) { this.setupMessages.set(key, invalid); this.render(); return; }
      const chosen = volume?.id;
      // Workspace runs retain their own completion placement, including while another Tab is active.
      // A second automatic Open would race that placement and save another overlapping window.
      const workspaceCompletion = document.body?.dataset.workspaceStudy === 'true';
      this.setupPending.add(key); this.setupMessages.set(key, 'Starting…'); this.render();
      void this.commands.tryKind!(row.id, { session, input: kind.input, ...(retained ? { resource: retained.reference } : {}), ...(chosen ? { volume: chosen } : {}), ...(fields.length ? { params: parameterValues(fields, values) } : {}) })
        .then(r => {
          this.setupMessages.set(key, r.ok === false ? r.error ?? 'It did not work. Check the input and try again.'
            : r.said ?? r.summary ?? 'No displayable result was returned. Inspect Activity, then choose another input and try again.');
          if (!workspaceCompletion && r.ok !== false && r.table && this.commands.session?.() === session) this.commands.openTable?.(r.table, r.tableIdentity);
        })
        .catch((e: unknown) => { this.setupMessages.set(key, e instanceof Error ? e.message : String(e)); })
        .finally(() => { this.setupPending.delete(key); this.render(); });
    });
    part.append(run, said);
    if (this.commands.chooseInput) {
      const choose = el('button', { type: 'button', class: 'model-fact-act' }, 'Choose a picture or video');
      choose.addEventListener('click', () => this.commands.chooseInput?.()); part.append(choose);
    }
    return part;
  }

  private alternatives(role: string): HTMLElement | null {
    const shelf = this.wire?.setup?.shelf;
    const card = [...(shelf?.cards ?? []), ...(shelf?.behind ?? [])].find(c => c.id === role);
    if (!card) return null;
    const ordered = [...card.options].sort((a, b) => Number(Boolean(b.default)) - Number(Boolean(a.default)));
    const list = (rows: typeof ordered, cls: string) => el('ul', { class: cls }, ...rows.map(o => {
      const model = this.wire?.models?.find(m => m.setups?.some(s => s.role === role && s.option === o.id));
      const label = model ? el('a', { href: `#toolbox/${model.id}` }, o.label) : el('strong', {}, o.label);
      return el('li', { 'data-option': o.id }, label, o.default ? ' · default' : '', ` · ${o.word} · ${o.where}`,
        o.cost ? ` · ${o.cost}` : '');
    }));
    const box = el('section', { class: 'tool-alternatives' }, el('h4', {}, `${card.title}: options`),
      list(ordered.slice(0, 3), 'tool-options-primary'));
    if (ordered.length > 3) {
      const more = el('details', { class: 'tool-options-more', open: this.optionFolds.has(role) },
        el('summary', {}, `More options (${ordered.length - 3})`), list(ordered.slice(3), 'tool-options-rest'));
      more.addEventListener('toggle', () => { if (more.open) this.optionFolds.add(role); else this.optionFolds.delete(role); });
      box.append(more);
    }
    if (card.outcomes?.length) box.append(el('h4', {}, 'Observed outcomes for this job'),
      el('ul', {}, ...card.outcomes.map(o => el('li', {}, o))), el('p', {}, 'These observations may include other tools and specimens.'));
    else box.append(el('p', {}, 'No observed outcomes for this job yet.'));
    return box;
  }

  private setupControl(row: ModelRow, setup: ToolSetup, _keys: Record<string, KeyFacts> | undefined, detailed: boolean, shown = ''): HTMLElement {
    const box = el('div', { class: 'tool-setup', 'data-role': setup.role });
    const plan = setup.plan, commands = this.commands.shelf;
    if (!commands) return box;
    const all = [...(this.wire?.setup?.shelf?.cards ?? []), ...(this.wire?.setup?.shelf?.behind ?? [])];
    const admitted = this.wire?.setup?.turning?.[setup.role] ?? all.find(c => c.id === setup.role)?.turning;
    const turning = admitted?.option === setup.option ? admitted : undefined;
    const identity = `${setup.role}:${setup.option}`;
    const message = this.setupMessages.get(identity);
    const said = el('p', { class: 'shelf-said', role: 'status', hidden: !message }, message);
    const button = (label: string, cls: string, run: () => Promise<unknown>) => {
      const b = el('button', { type: 'button', class: `mm-control ${cls}` }, label);
      const key = `${identity}:${cls}`; b.disabled = this.setupPending.has(key);
      b.addEventListener('click', () => {
        if (this.setupPending.has(key)) return;
        this.setupPending.add(key); this.setupMessages.delete(identity); b.disabled = true;
        void run().catch((error: unknown) => {
          this.setupMessages.set(identity, error instanceof Error ? error.message : String(error));
        }).finally(() => { this.setupPending.delete(key); this.render(); });
      });
      return b;
    };
    if (detailed) box.append(el('h4', {}, setup.title));
    const terms = [(turning?.state !== 'failed' ? turning?.privacy : undefined) ?? plan?.privacy, (turning?.state !== 'failed' ? turning?.cost : undefined) ?? plan?.cost].filter(Boolean).join(' · ');
    if (terms) box.append(el('p', { class: 'tool-terms' }, terms));
    // A step the card already says as its state line is not said twice (2026-10-15: "Set up Fiji first" twice).
    const steps = plan?.steps.filter(step => step.says !== shown) ?? [];
    if (steps.length) box.append(el('ol', { class: 'shelf-steps' }, ...steps.map(step => el('li', { class: 'shelf-step', 'data-by': step.by },
      el('span', { class: 'shelf-step-words' }, step.says), el('small', {}, step.by === 'app' ? 'the app does this' : 'needs you')))));
    if (turning && turning.state !== 'failed') {
      box.append(el('p', { role: 'status' }, turning.says), button('Continue setup', 'tool-setup-act', () => { this.openSetup(row, setup, detailed); return Promise.resolve(); }));

    } else if (plan) {
      // A download, an install or a step of yours: the sheet says what it takes first, and its own Turn on starts it.
      box.append(button(setup.recheck ? 'Check again' : plan.steps.every(s => s.kind === 'check') ? 'Check it here' : firstStepWords(plan.steps),
        'tool-setup-act', () => { this.openSetup(row, setup, detailed); return Promise.resolve(); }));
      if (turning?.state === 'failed') box.append(el('p', { role: 'status' }, `Didn’t finish: ${turning.says}`));
    }
    if (detailed) {
      if (setup.check) box.append(el('p', { class: 'tool-check' },
        `${setup.check.state}${setup.check.earlierBuild ? ' (an earlier build)' : ''}`,
        setup.check.at ? ` · ${setup.check.at}` : '', setup.check.ms !== undefined ? ` · ${setup.check.ms} ms` : '',
        setup.check.detail ? ` · ${setup.check.detail}` : '', ' · Functional check; scientific qualification is separate.'));
      if (setup.canTry) box.append(button(`Try ${setup.title} with this tool`, 'tool-try', () => commands.tryIt(setup.role, setup.option)));
    }
    box.append(said);
    return box;
  }

  private facts(row: ModelRow, keys: Record<string, KeyFacts> | undefined, facts: ModelFact[] = row.facts, scope = 'sheet'): HTMLElement {
    const list = el('ul', { class: 'model-facts' });
    for (const f of facts) {
      const preparing = f.prepare ?? f.install;
      const going = preparing ? this.wire?.setup?.installs?.find(i => i.model === preparing && ['queued', 'installing'].includes(i.state)) : undefined;
      const failed = !going && f.prepare ? this.wire?.setup?.installs?.find(i => i.model === f.prepare && i.state === 'failed') : undefined;
      const item = el('li', { 'data-ok': f.ok === null ? 'none' : String(f.ok), 'data-kind': f.kind, class: f.observed ? 'model-fact-observed' : '' },
        el('span', { class: 'model-fact-mark', 'aria-hidden': 'true' }, mark(f)),
        el('span', { class: 'model-fact-says' }, el('span', { class: 'visually-hidden' }, f.ok === true ? 'Yes: ' : f.ok === false ? 'Not yet: ' : ''), going && f.ok !== true ? 'Preparation is in progress. This tool is not ready yet.' : failed ? `Preparation didn’t finish: ${failed.says}` : f.says));
      if (going && f.ok !== true) item.append(el('details', { class: 'model-preparation-diagnostics' },
        el('summary', {}, 'Before this attempt'), el('pre', {}, f.says)));
      if (failed) item.append(el('details', { class: 'model-preparation-diagnostics',
        'data-model-editor': JSON.stringify(['preparation-diagnostics', scope, row.id, row.revision, row.version, f.kind, f.says, failed.says]) },
        el('summary', {}, 'Preparation details'), el('pre', {}, [...new Set([failed.says, f.says])].join('\n\n'))));
      if (f.kind === 'key' && f.service) {
        const key = Object.values(keys ?? {}).find((k) => k.service === f.service);
        if (key && (f.ok === false || key.canSave)) {
          const b = el('button', { type: 'button', class: 'model-fact-act' }, f.ok ? 'Change' : 'Add key');
          b.addEventListener('click', () => openAddKey(key, b));
          item.append(b);
        }
      } else if (f.kind === 'files') {
        const downloading = this.wire?.setup?.downloads?.some(d => d.model === f.model && ['queued', 'downloading', 'checking'].includes(d.state));
        const needsAccess = row.facts.some(fact => (fact.kind === 'licence' || fact.kind === 'key') && fact.ok !== true);
        if (f.ok === false && f.model && (downloading || !needsAccess))
          item.append(downloadControl(f.model, f.cached ? 'Prepare cached files' : 'Download files', this.wire?.setup?.downloads, this.commands.download));
        if (f.ref) {
          const b = el('button', { type: 'button', class: 'model-fact-act', title: f.ref }, 'Show in Finder');
          b.addEventListener('click', () => void this.commands.reveal(row.id).catch(() => { b.textContent = f.ref ?? ''; }));
          item.append(b);
        }
      } else if (f.ref && /^https?:/.test(f.ref)) {
        item.append(el('a', { class: 'model-fact-act', href: f.ref, target: '_blank', rel: 'noopener noreferrer' }, 'Open page'));
      }
      if (f.rollback && this.commands.fijiRollback) {
        const back = el('button', { type: 'button', class: 'model-fact-act' }, `Go back to generation ${f.rollback}`);
        back.addEventListener('click', () => { back.setAttribute('disabled', ''); void this.commands.fijiRollback!().catch(() => back.removeAttribute('disabled')); });
        item.append(back);
      }
      if (f.trust && this.commands.channelTrust) {
        const allow = el('button', { type: 'button', class: 'model-fact-act' }, `Allow code from ${f.trustLabel ?? f.trust}`);
        allow.addEventListener('click', () => { allow.setAttribute('disabled', ''); void this.commands.channelTrust!(f.trust!, true).catch(() => allow.removeAttribute('disabled')); });
        item.append(allow);
      }
      if (going && this.commands.installCancel) {
        // An update installing (or a Python made by hand being replaced): what it is doing and its Stop, as a first
        // Turn on has (C7 Window B, finding 7); Stop ends its build or its proof and says so.
        const stop = el('button', { type: 'button', class: 'model-fact-act tool-stop' }, 'Stop');
        stop.addEventListener('click', () => { stop.setAttribute('disabled', ''); void this.commands.installCancel!(preparing!).catch(() => stop.removeAttribute('disabled')); });
        item.append(el('span', { class: 'model-fact-going', role: 'status' }, going.says), stop);
      } else if (f.prepare && this.commands.prepare) {
        const prepare = el('button', { type: 'button', class: 'model-fact-act' }, 'Prepare for review');
        prepare.addEventListener('click', () => {
          prepare.textContent = 'Preparing…';
          prepare.setAttribute('disabled', '');
          void this.commands.prepare!(f.prepare!).catch(() => { prepare.textContent = 'Prepare for review'; prepare.removeAttribute('disabled'); });
        });
        item.append(prepare);
      } else if (f.install && this.commands.install) {
        // A Python made by hand runs it: Install makes the catalog's pinned one (envs.py), used from then on.
        const install = el('button', { type: 'button', class: 'model-fact-act' }, 'Install');
        install.addEventListener('click', () => {
          install.textContent = 'Installing…';
          install.setAttribute('disabled', '');
          void this.commands.install!(f.install!).catch(() => { install.textContent = 'Install'; install.removeAttribute('disabled'); });
        });
        item.append(install);
      }
      if (f.confirm && f.ok !== true && this.commands.confirm) {
        const yes = el('button', { type: 'button', class: 'model-fact-act' }, 'I’ve accepted it');
        yes.addEventListener('click', () => { yes.setAttribute('disabled', ''); void this.commands.confirm!(row.id).catch(() => undefined); });
        item.append(yes);
      }
      if (f.observed && this.commands.observe) {
        // Asked of the outside world: asked again on this press (and by itself every few minutes while it is not yet).
        const again = el('button', { type: 'button', class: 'model-fact-act' }, row.checking ? 'Checking…' : 'Check again');
        if (row.checking) again.setAttribute('disabled', '');
        again.addEventListener('click', () => {
          again.textContent = 'Checking…';
          again.setAttribute('disabled', '');
          void this.commands.observe!(row.id).catch(() => undefined);
        });
        item.append(again);
      }
      list.append(item);
    }
    return list;
  }

  private details(row: ModelRow): HTMLElement {
    const lines: [string, string | Node][] = [];
    if (row.version) lines.push(['Version', row.version]);
    if (row.revision) lines.push(['Revision', row.revision]);
    if (row.size) lines.push(['Size', sizeWords(row.size)]);
    if (row.folder) lines.push(['Folder', row.folder]);
    if (row.licence) lines.push(['Licence', row.licence]);
    if (row.credit?.authors) lines.push(['By', row.credit.authors]);
    if (row.link) lines.push(['Source', el('a', { href: row.link, target: '_blank', rel: 'noopener noreferrer' }, row.link.replace(/^https?:\/\//, ''))]);
    const dl = el('dl', { class: 'model-details-list' });
    for (const [k, v] of lines) dl.append(el('dt', {}, k), el('dd', {}, v));
    const files = row.files.length ? el('ul', { class: 'model-files' }, ...row.files.map((f) => el('li', {}, el('span', {}, f.path), el('span', {}, sizeWords(f.bytes))))) : null;
    return el('details', { class: 'model-details' }, el('summary', {}, 'Details'), dl, files);
  }

  private volumesPanel(): HTMLElement {
    const commands = this.commands.volumes!;
    // The page is drawn on every wire: the list is read again at most every 2 s, and a read skipped for that is made
    // once the 2 s pass (a job's end came 1 s after a cancel's own read: its row stayed "running"); while a job runs,
    // every 3 s.
    const later = (ms: number) => {
      if (this.volumesTimer) return;
      this.volumesTimer = window.setTimeout(() => { this.volumesTimer = 0; if (!this.root.hidden) this.render(); }, ms);
    };
    const since = Date.now() - this.volumesRead;
    if (since > 2000) {
      this.volumesRead = Date.now();
      const session = this.volumeSession;
      void commands.list().then((got) => {
        if (session !== this.volumeSession || session !== (this.commands.session?.() ?? '')) return;
        const changed = this.volumesError || JSON.stringify(got) !== JSON.stringify(this.volumes);
        this.volumesError = ''; this.volumes = got; if (changed) this.render();
        if (got.some((e) => e.kind === 'job' && (e.state === 'running' || e.state === 'queued'))) later(3000);
      }).catch((error: unknown) => {
        if (session !== this.volumeSession || session !== (this.commands.session?.() ?? '')) return;
        const message = `${error instanceof Error ? error.message : String(error)}. Retrying the volume list…`;
        if (this.volumesError !== message) { this.volumesError = message; this.render(); }
      });
    } else {
      later(2001 - since);
    }
    // An action belongs to the session it was started in, as the list's reads do: its reply re-reads the list and says
    // its words only while the page still shows that session (the Spec review of 87ff1e59: an Add pending in A that
    // ended after a switch to B said A's success in B, which held no such volume).
    const owner = () => {
      const session = this.volumeSession;
      return () => session === this.volumeSession && session === (this.commands.session?.() ?? '');
    };
    const again = <T>(act: () => Promise<T>) => {  // its reply's words are said
      const still = owner();
      return act().then((reply) => { if (still()) { this.volumesRead = 0; this.render(); } return reply; });
    };
    return volumesSection(this.volumes, {
      ...commands,
      add: (path) => again(() => commands.add(path)),
      describe: (id, facts) => again(() => commands.describe(id, facts)),
      start: (id) => again(() => commands.start(id)),
      cancel: (id) => again(() => commands.cancel(id)),
      again: (id) => again(() => commands.again(id)),
      upload: (file) => again(() => commands.upload(file)),
      owner,
    }, [this.volumesSaid, this.volumesError].filter(Boolean).join(' '), (words) => { this.volumesSaid = words; this.render(); },
    this.selected === 'brainreg' || !this.wire?.models?.some(r => r.newKind?.input === 'volume.3d'));
  }

  /** Where entries come from besides the app's own (channels.py): the reviewed catalog's updates, and community channels. */
  private channelsSection(): HTMLElement {
    const channels = (this.wire?.setup?.channels ?? []) as ChannelRow[];
    const update = this.wire?.setup?.channelUpdate;
    // What the last action here said: the page is drawn again on every wire, so an add's words are kept until the next.
    const words = this.channelSaid || update?.says || '';
    const said = el('p', { class: 'models-own-said', role: 'status', hidden: !words }, words);
    const check = el('button', { type: 'button', class: 'mm-control' }, update?.state === 'running' ? 'Checking…' : 'Check for catalog updates');
    check.disabled = update?.state === 'running';
    check.addEventListener('click', () => {
      this.channelSaid = '';
      check.disabled = true;
      check.textContent = 'Checking…';
      void this.commands.catalogUpdate!().catch((error: unknown) => {
        check.disabled = false;
        check.textContent = 'Check for catalog updates';
        said.textContent = error instanceof Error ? error.message : String(error);
        said.hidden = false;
      });
    });
    const list = el('ul', { class: 'models-channels' }, ...channels.map((c) => {
      const li = el('li', { 'data-channel': c.name }, el('span', {}, channelWords(c)), ' ', el('code', {}, c.url));
      if (!c.reviewed && this.commands.channelTrust) {
        const toggle = el('button', { type: 'button', class: 'model-fact-act' }, c.trusted ? 'No longer allow its code' : 'Allow its code');
        toggle.addEventListener('click', () => { toggle.setAttribute('disabled', ''); void this.commands.channelTrust!(c.name, !c.trusted).catch(() => toggle.removeAttribute('disabled')); });
        li.append(' ', toggle);
      }
      if (!c.reviewed && c.url && this.commands.channelUpdate) {
        const check = el('button', { type: 'button', class: 'model-fact-act' }, 'Check for updates');
        check.addEventListener('click', () => {
          check.setAttribute('disabled', ''); check.textContent = 'Checking…';
          void this.commands.channelUpdate!(c.name).then((r) => { this.channelSaid = r.says ?? ''; this.render(); })
            .catch((error: unknown) => { this.channelSaid = error instanceof Error ? error.message : String(error); this.render(); });
        });
        li.append(' ', check);
      }
      if (!c.reviewed && !c.url) li.append(' ', el('span', {}, 'To update, import a newer shared file.'));
      if (!c.reviewed && this.commands.channelRemove) {
        const drop = el('button', { type: 'button', class: 'model-fact-act' }, 'Remove');
        drop.addEventListener('click', () => { drop.setAttribute('disabled', ''); void this.commands.channelRemove!(c.name).catch(() => drop.removeAttribute('disabled')); });
        li.append(' ', drop);
      }
      return li;
    }));
    // Text, not url: a git repository on this Mac (a folder) is a channel too, which a url field would refuse.
    const url = el('input', { type: 'text', inputmode: 'url', spellcheck: 'false', class: 'mm-control', placeholder: 'https://github.com/someone/their-catalog', 'aria-label': 'A community channel’s git address, or a repository on this Mac' }) as HTMLInputElement;
    const commit = el('input', { type: 'text', class: 'mm-control', placeholder: 'commit (newest when empty)', 'aria-label': 'Its commit' }) as HTMLInputElement;
    const add = el('button', { type: 'submit', class: 'mm-control' }, 'Add channel');
    const form = el('form', { class: 'models-own-form', 'data-model-editor': 'channel-add' }, url, commit, add);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!url.value.trim() || !this.commands.channelAdd) return;
      add.disabled = true;
      void this.commands.channelAdd(url.value.trim(), commit.value.trim() || undefined)
        .then((r) => { this.channelSaid = r.says ?? 'Added.'; said.textContent = this.channelSaid; said.hidden = false; url.value = ''; commit.value = ''; })
        .catch((error: unknown) => { this.channelSaid = error instanceof Error ? error.message : String(error); said.textContent = this.channelSaid; said.hidden = false; })
        .finally(() => { add.disabled = false; this.render(); });
    });
    return el('section', { class: 'models-own', 'aria-labelledby': 'models-channels-title' },
      el('h3', { id: 'models-channels-title' }, 'Where entries come from'),
      el('p', {}, 'Entries merged into the reviewed catalog after this app was made appear when you check for updates. ',
        'A community channel is someone else’s catalog, pinned at one commit: its entries are listed as not reviewed here, and none runs until you allow its code.'),
      check, list, this.commands.channelAdd ? form : null, said);
  }

  private addYourOwn(): HTMLElement {
    const problems = this.wire?.setup?.catalogProblems ?? [];
    const loose = this.wire?.setup?.loose ?? [];
    const said = el('p', { class: 'models-own-said', role: 'status', hidden: true, 'data-model-status': 'own' });
    const say = (words: string, ok = true) => {
      this.ownSaid = { words, ok };
      const current = this.root.querySelector<HTMLElement>('[data-model-status="own"]') ?? said;
      current.textContent = words; current.dataset.ok = String(ok); current.hidden = false;
    };
    if (this.ownSaid) {
      said.textContent = this.ownSaid.words; said.dataset.ok = String(this.ownSaid.ok); said.hidden = false;
    }
    // A link or a path.
    const input = el('input', { type: 'text', class: 'models-own-input', placeholder: 'Paste a model or tool link, or a local file path',
      'aria-label': 'A link or a path to a model of your own', spellcheck: 'false' }) as HTMLInputElement;
    const job = el('select', { 'aria-label': 'What should this capability do?', class: 'own-job' },
      ...[['find', 'Find or count things'], ['name', 'Describe or name things'], ['measure', 'Measure things'], ['clear', 'Clear up a picture']]
        .map(([value, label]) => el('option', { value }, label)));
    job.value = this.ownCandidate?.job ?? 'find';
    const example = el('input', { type: 'file', accept: 'image/png,image/jpeg', 'aria-label': 'Your comparison picture', class: 'own-picture' }) as HTMLInputElement;
    const add = el('button', { type: 'submit', class: 'mm-control models-own-add' }, 'Add');
    const form = el('form', { class: 'models-own-form' }, input, job, filePicker(example),
      el('span', { class: 'models-own-hint' }, 'Your picture (PNG or JPEG, up to 4 million pixels) is needed only to set up a comparison or have an AI draft it. Following motion needs a recording workflow.'), add);
    form.addEventListener('submit', (event) => {
      event.preventDefault();
      if (!input.value.trim() || !this.commands.add) return;
      add.disabled = true;
      say('Adding…'); this.ownCandidate = null;
      const link = input.value.trim();
      // Any link is recognised without a picture; one chosen already goes with it (asked for only when it is needed).
      const file = example.files?.[0];
      const selected = this.commands.ownPicture && file ? this.commands.ownPicture(file, job.value) : Promise.resolve(undefined);
      void selected.then(trial => this.commands.add!(link, undefined, trial?.id).then(r => ({ ...r, trial: trial?.id })))
        .then((r) => { this.ownCandidate = { link, model: r.model, kind: r.kind, trial: r.trial }; say(r.says ?? 'Added.'); input.value = ''; })
        .catch((error: unknown) => say(error instanceof Error ? error.message : String(error), false))
        .finally(() => { add.disabled = false; this.render(); });
    });
    const candidate = this.ownCandidate;
    const follow = el('div', { class: 'tool-add-followup' });
    if (candidate) {
      const draft = candidate.kind === 'skeleton' && /^https:\/\//.test(candidate.link) && this.wire?.setup?.drafts !== undefined;
      const run = draft && this.commands.build ? (trial?: string) => this.commands.build!(candidate.link, trial)
        : candidate.kind !== 'skeleton' && candidate.model && this.commands.turnOnModel
          ? (trial?: string) => trial ? this.commands.turnOnModel!(candidate.model!, trial) : this.commands.turnOnModel!(candidate.model!) : null;
      if (run) {
        const label = draft ? 'Have an AI draft it (up to $2)' : 'Set up for comparison';
        const button = el('button', { type: 'button', class: `mm-control ${draft ? 'tool-draft-start' : 'tool-added-on'}` }, label);
        button.disabled = this.ownBusy;
        button.addEventListener('click', () => {
          // The picture is asked for here, where it is used: read from the form as it is now (kept across renders).
          const chosen = this.root.querySelector<HTMLInputElement>('.own-picture')?.files?.[0];
          if (!candidate.trial && this.commands.ownPicture && !chosen) {
            say(ownPictureAsk(label, draft), false); return;
          }
          const purpose = this.root.querySelector<HTMLSelectElement>('.own-job')?.value || 'find';
          const started = candidate.trial || !this.commands.ownPicture || !chosen ? run(candidate.trial)
            : this.commands.ownPicture(chosen, purpose).then(t => { candidate.trial = t.id; return run(t.id); });
          this.ownBusy = true; this.render(); void started
          .then(() => say(draft ? 'Drafting; each attempt appears below for your review.' : 'Turning on; its steps appear on its card.'))
          .catch((e: unknown) => say(e instanceof Error ? e.message : String(e), false))
          .finally(() => { this.ownBusy = false; this.render(); }); });
        follow.append(el('p', {}, draft ? 'Uses your research provider. Up to 15 minutes and $2; review the resulting adapter before using it.'
          : 'Carries through its download, installation and functional check. Its card asks for any permission only you can give.'), button);
      }
    }
    // A file dropped here (or chosen).
    const pick = el('input', { type: 'file', class: 'visually-hidden models-own-pick', 'aria-label': 'Choose a Cellpose model file' }) as HTMLInputElement;
    const drop = el('label', { class: 'models-own-drop' }, pick, el('strong', {}, 'Drop a Cellpose model file here'),
      el('span', {}, ' or click to choose one'));
    const send = (file: File | undefined) => {
      if (!file || !this.commands.upload) return;
      say(`Adding ${file.name}… 0%`);
      const chosen = example.files?.[0];
      const selected = this.commands.ownPicture && chosen ? this.commands.ownPicture(chosen, job.value) : Promise.resolve(undefined);
      void selected.then(t => this.commands.upload!(file, (share) => say(`Adding ${file.name}… ${Math.floor(share * 100)}%`), t?.id)
        .then((r) => {
          if (r.ok && r.model) { this.ownCandidate = { link: file.name, model: r.model, kind: r.kind, trial: t?.id }; this.render(); }
          say(r.says, r.ok);
        })).catch(e => say(String(e), false));
    };
    // Cleared once read, so choosing the same file again (after a failed add) fires change again.
    pick.addEventListener('change', () => { const file = pick.files?.[0]; pick.value = ''; send(file); });
    drop.addEventListener('dragover', (event) => { event.preventDefault(); drop.dataset.over = 'true'; });
    drop.addEventListener('dragleave', () => { delete drop.dataset.over; });
    drop.addEventListener('drop', (event) => { event.preventDefault(); delete drop.dataset.over; send(event.dataTransfer?.files?.[0]); });
    // Files found lying in the folder.
    const found = loose.length ? el('div', { class: 'models-own-found' }, el('h4', {}, 'Found in your folder'),
      el('ul', {}, ...loose.map((f) => {
        const b = el('button', { type: 'button', class: 'mm-control models-own-add' }, 'Add it');
        b.addEventListener('click', () => {
          if (!this.commands.add) return;
          b.disabled = true;
          void this.commands.add(f.path).then((r) => say(r.says ?? 'Added.')).catch((e: unknown) => { b.disabled = false; say(String(e), false); });
        });
        return el('li', {}, el('span', {}, `${f.name} (${sizeWords(f.bytes)})`), b);
      }))) : null;
    return el('section', { class: 'models-own', 'aria-labelledby': 'models-own-heading' },
      el('h3', { id: 'models-own-heading' }, 'Add your own'),
      el('p', {}, 'Paste a model or tool link, add a supported file, or ask for an adapter draft. ',
        'Set it up for comparison on one of your pictures, compare the results, then press ', el('strong', {}, 'Use it'), ' to add the choice.'),
      el('div', { 'data-model-editor': 'model-add' }, form, drop), found, said, follow,
      el('p', { class: 'models-own-where' }, 'Yours live in ', el('code', {}, '~/MicroscopeMind/catalog/mine/models/<name>/'),
        ', each with a ', el('code', {}, 'model.toml'), ' (catalog/README.md shows one). A GitHub repository or release holding a bioimage.io model the reviewed runner takes is added ready to set up; any other link is kept as a skeleton that says what it holds and lacks.'),
      ...problemGroups(problems).map((g) => el('div', { class: 'models-problems' }, el('h4', {}, g.title),
        el('ul', {}, ...g.rows.map((p) => el('li', {}, el('code', {}, p.entry), `: ${p.problems.join('; ')}`))))));
  }
}

/** An AI draft's review, read before Use it (orchestrator/drafts.py `review`, ticket 62 L13): exactly the code that ran,
 * its locked packages, the files it pinned and where from, what it imports, every try with its error, and the static
 * checks a contribution gets. Use it sends back its digest, so what is admitted is what was shown here. */
export function draftReview(review: DraftReview): HTMLElement {
  if (review.busy)  // a moment's lock while a try or Use it finishes: the app sends the review again when it ends
    return el('p', { class: 'models-draft-review', role: 'status' }, 'Its review is being updated; it appears here in a moment.');
  if (review.unavailable || !review.digest)
    return el('p', { class: 'models-draft-review' }, `Its review could not be read: ${review.unavailable ?? 'no digest'}. Try it again before using it.`);
  const lines = (review.adapter ?? '').replace(/\n$/, '').split('\n').length; // as the facts count them (splitlines)
  const tries = (review.tries ?? []).map((t, i) => el('li', {}, `Try ${i + 1}${t.at ? ` (${t.at})` : ''}: `
    + (t.ok ? `ran${typeof t.count === 'number' ? `, found ${t.count}` : ''}` : `failed: ${errorWords(t.error ?? 'no reason kept')}`)));
  const files = (review.files ?? []).map(f => el('li', {}, `${f.path} · ${f.bytes.toLocaleString()} bytes · sha256 ${f.sha256.slice(0, 12)}… · `,
    f.url ? el('span', { class: 'model-link' }, f.url) : 'no address'));
  return el('details', { class: 'models-draft-review', open: true, 'data-model-editor': `draft-review:${review.digest}` },
    el('summary', {}, 'Review what it runs'),
    el('p', {}, review.says ?? ''),
    el('ul', {}, ...(review.facts ?? []).map(f => el('li', {}, f))),
    (review.checks ?? []).length ? el('ul', { class: 'models-draft-checks' }, ...(review.checks ?? []).map(c =>
      el('li', { 'data-blocks': c.blocks ? 'true' : 'false' }, `${c.blocks ? 'Stops Use it' : 'Note'}: ${c.says}`)))
      : el('p', {}, `Static checks found nothing in ${(review.scanned ?? ['adapter.py']).join(', ')}.`),
    el('h5', {}, 'Every try'), el('ol', {}, ...tries),
    el('h5', {}, 'Locked packages'), el('p', {}, (review.packages ?? []).join(', ') || 'none'),
    files.length ? el('h5', {}, 'Pinned files') : null, files.length ? el('ul', {}, ...files) : null,
    el('details', {}, el('summary', {}, `adapter.py (${lines} lines)`), el('pre', { class: 'models-draft-code' }, review.adapter ?? '')),
    el('details', {}, el('summary', {}, 'requirements.in'), el('pre', { class: 'models-draft-code' }, (review.requirements ?? []).join('\n'))),
    el('p', { class: 'model-what' }, `Review ${review.digest.slice(0, 12)} · Use it adds exactly the version shown here: this `
      + 'code, these locked packages and these pinned files, each by its hash.'));
}

/** A failed try's words: the first line names the step that failed (locking, installing, running), the last its cause. */
export function errorWords(error: string): string {
  const lines = error.split('\n').map(l => l.trim()).filter(Boolean);
  if (!lines.length) return 'no reason kept';
  return lines.length === 1 ? lines[0] : `${lines[0]} … ${lines[lines.length - 1]}`;
}

/** What an added source that needs code says: what it is and lacks, as the app found it (link_recognise.py), then the
 * two ways on. Never a generic line when the app said why (ticket 62 row 7). */
export function integrationWords(why?: string): string {
  const next = 'Choose how to build that integration, then check it on your picture before enabling it.';
  const said = (why ?? '').trim().replace(/\.$/, '');
  // A sentence starts with a capital, but a name keeps its own spelling (maya/diatom-tools, never Maya/diatom-tools).
  const start = /^[^\s/]+\//.test(said) ? said : `${said.charAt(0).toUpperCase()}${said.slice(1)}`;
  return said ? `${start}. ${next}` : `This source needs code to connect it to the app. ${next}`;
}

/** What a Set up / Have an AI draft press says when no picture is chosen yet: the one place it is asked for. */
export function ownPictureAsk(label: string, draft: boolean): string {
  return `Choose your comparison picture above (PNG or JPEG), then press “${label.replace(/ \(.*\)$/, '')}” again: `
    + (draft ? 'the AI tries its draft on that picture.' : 'it is compared on that picture before you use it.');
}

/** A comparison's heading: its entry's name, and, when another in the list has the same name (the same link added again
 * with another template version), what tells them apart. */
export function ownTrialTitle(t: OwnTrial, all: OwnTrial[]): string {
  const name = t.name || t.model;
  const twins = all.filter(o => (o.name || o.model) === name);
  if (twins.length < 2) return name;
  const unique = t.detail && twins.filter(o => o.detail === t.detail).length === 1;
  return `${name} (${unique ? t.detail : [t.detail, t.model].filter(Boolean).join(' · ')})`;
}

/** Catalog kinds are wire identifiers; cards use familiar names without changing readiness. */


/** A channel in a line: "reviewed catalog at 3f2a1c9 · 31 entries", "lab-models at 9e1d0b2 · 4 entries · not reviewed here". */
/** A file input drawn as the studio's own control (ticket 67: comparing on your picture showed a raw "Choose File / No
 * file chosen"): the input itself, visually hidden and still focusable, inside a label that reads as a button and names
 * the file chosen. */
export function filePicker(input: HTMLInputElement, prompt = 'Choose a picture', none = 'No picture chosen'): HTMLElement {
  input.classList.add('visually-hidden');
  const name = el('span', { class: 'file-picker-name' }, input.files?.[0]?.name ?? none);
  input.addEventListener('change', () => { name.textContent = input.files?.[0]?.name ?? none; });
  return el('label', { class: 'file-picker' }, input, el('span', { class: 'mm-control file-picker-button' }, prompt), name);
}

export function channelWords(c: ChannelRow): string {
  const shared = c.source ? c.source === 'shared-file' : !c.reviewed && !c.url;
  const source = shared ? `${c.label || 'Shared file'} · saved copy` : `${c.reviewed ? 'The reviewed catalog' : c.name} at ${c.commit.slice(0, 7) || '(not fetched)'}`;
  return `${source} · ${c.entries} ${c.entries === 1 ? 'entry' : 'entries'}`
    + (c.reviewed ? '' : c.trusted ? ' · not reviewed here, its code allowed' : ' · not reviewed here, its code not allowed');
}

type CatalogProblem = { entry: string; problems: string[]; incompatible?: boolean };

/** Entries the app won't run, each said under why: made for another version of the app, or can't be read. */
export function problemGroups(problems: CatalogProblem[]): { title: string; rows: CatalogProblem[] }[] {
  return [
    { title: 'Made for another version of HookeUI', rows: problems.filter((p) => p.incompatible) },
    { title: 'Entries that can’t be read', rows: problems.filter((p) => !p.incompatible) },
  ].filter((g) => g.rows.length);
}

/** SI sizes as the server says them (size-words.ts, shared with Tools and the run page). */
export { sizeWords };

function reviewPreview(preview: string, truncated = false): HTMLElement {
  if (!truncated) {
    try {
      const value: unknown = JSON.parse(preview);
      if (value && typeof value === 'object' && 'columns' in value && 'rows' in value
          && Array.isArray(value.columns) && Array.isArray(value.rows)) {
        const columns = value.columns.map(c => typeof c === 'object' && c && 'name' in c ? String(c.name) : String(c));
        const rows = value.rows.filter(Array.isArray);
        return el('div', {}, el('table', {}, el('thead', {}, el('tr', {}, ...columns.map(c => el('th', {}, c)))),
          el('tbody', {}, ...rows.slice(0, 10).map(r => el('tr', {}, ...r.map(cell => el('td', {}, String(cell ?? ''))))))),
          ...(rows.length > 10 ? [el('p', {}, `Showing 10 of ${rows.length} rows.`)] : []));
      }
    } catch { /* A shortened or non-JSON result remains readable as text. */ }
  }
  return el('div', {}, el('pre', {}, preview), ...(truncated ? [el('p', {}, 'Preview shortened. Open the complete result below.')] : []));
}
