/** Studies: the research runtime's efforts (services/orchestrator/research/effort_api.py), as the studio reads and
 * starts them. A study is one Project's research: its objective and how success is judged, its spending allowance,
 * the experiments (tasks) run for it and the findings that cite them. Execution receipts are never written from here:
 * the studio only creates a study and pauses, continues or stops it. All words for what the server keeps are here, so
 * every view of a study says the same thing. */

export type EffortStatus = 'active' | 'paused' | 'stopped';
export type TaskStatus = 'planned' | 'launching' | 'running' | 'unknown' | 'cancel_requested' | 'completed' | 'failed' | 'cancelled';
export interface EffortTask {
  status: TaskStatus | string;
  specification?: Record<string, unknown>;
  reservedMicrousd: number;
  chargedMicrousd: number | null;
  usageEstimated?: boolean;
  problem?: string | null;
  collectionProblem?: string | null;
  observation?: { sealedEvidence?: boolean; providerStatus?: string; evidence?: { sha256: string }; listCostMicrousd?: number | null; archivedAt?: string | null; continuationRequired?: boolean };
  execution?: { provider?: string; session?: string; id?: string; runId?: string; day?: string };
  managedActions?: { archive?: string };
}
export interface EffortFinding { request: string; statement: string; tasks: string[] }
export interface EffortClaim { by?:string; at?:number; request:string; statement:string; byLabel:string; limitations?:string;
  evidence:{artifact:{id:string;revision:number};relation:string}[]; dependsOn:string[] }
export interface Effort {
  id: string; session: string; revision: number; digest: string;
  createdAt?:number; updatedAt?:number; createdBy?:{by:string;byLabel:string};
  questions?:{id:string;question:string;by?:string;byLabel:string;at?:number;recipient:{actor:string;label:string};answer:null|{by:string;byLabel:string;at?:number;statement:string}}[];
  stopPending?: boolean; title?: string; objective: string; criteria: string; status: EffortStatus | string;
  controlProblems?: string[];
  capMicrousd: number; spentMicrousd: number; heldMicrousd: number; availableMicrousd: number;
  inputs: { id: string; revision: number; digest: string }[];
  tasks: Record<string, EffortTask>;
  experiments?: Record<string, {by?:string; operation:string; byLabel:string; execution:{id:string;session:string}}> ;
  findings: EffortFinding[];
  claims?: EffortClaim[];
  claimChanges?: {by?:string;at?:number;claim:string;reason:string;byLabel:string}[];
}

const HEADERS = { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'research-effort' };

/** The server's refusal, in its own words and with its next step, never a bare status code. */
export class StudyRefused extends Error {
  readonly next: string | null;
  constructor(message: string, next: string | null = null) { super(message); this.next = next; }
}
async function reply(response: Response): Promise<Record<string, unknown>> {
  const body = await response.json() as Record<string, unknown>;
  if (!response.ok || body.ok === false) throw new StudyRefused(String(body.says ?? body.error ?? 'The study could not be read.'), typeof body.next === 'string' ? body.next : null);
  return body;
}

/** This Project's studies, or null when this build has no studies (the route is absent): then nothing about them shows. */
export async function listStudies(session: string, fetcher: typeof fetch = fetch, signal?: AbortSignal): Promise<Effort[] | null> {
  const response = await fetcher(`/api/research-efforts?session=${encodeURIComponent(session)}`, { cache: 'no-store', signal });
  if (response.status === 404) return null;
  const body=await reply(response);
  if(!Array.isArray(body.efforts))throw new Error('Project Studies could not be read. Retry to refresh.');
  return body.efforts as Effort[];
}
export async function readStudy(id: string, session: string, fetcher: typeof fetch = fetch): Promise<Effort> {
  const response = await fetcher(`/api/research-efforts/${encodeURIComponent(id)}?session=${encodeURIComponent(session)}`, { cache: 'no-store' });
  return (await reply(response)).effort as Effort;
}
/** Saves a study draft without launching provider work. `request` is its identity: asking again with the same one returns the same study, never a second. */
export async function createStudy(fields: { session: string; request: string; title: string; objective: string; criteria: string; budgetUsd: number; inputs?: StudyInputRef[] },
  fetcher: typeof fetch = fetch): Promise<Effort> {
  const response = await fetcher('/api/research-efforts', { method: 'POST', headers: HEADERS, body: JSON.stringify({ ...fields, inputs: fields.inputs ?? [] }) });
  return (await reply(response)).effort as Effort;
}
export async function controlStudy(id: string, session: string, action: 'pause' | 'continue' | 'stop', fetcher: typeof fetch = fetch): Promise<Effort> {
  const response = await fetcher(`/api/research-efforts/${encodeURIComponent(id)}/control`, { method: 'POST', headers: HEADERS, body: JSON.stringify({ session, action }) });
  return (await reply(response)).effort as Effort;
}

/** Dollars from the server's micro-dollars; a spend under a cent says so rather than "$0.00". */
export function dollars(microusd: number): string {
  const usd = (Number.isFinite(microusd) ? microusd : 0) / 1e6;
  return usd > 0 && usd < 0.005 ? 'under $0.01' : `$${usd.toFixed(2)}`;
}

export const STUDY_WORDS: Record<string, string> = { active: 'Active', paused: 'Paused', stopped: 'Stopped' };
/** Each experiment's state in plain words. "Needs checking" is an execution the app could not confirm: its spending
 * stays held until someone reconciles it, and it is never shown as running or finished. */
export const TASK_WORDS: Record<string, string> = {
  planned: 'Planned', launching: 'Starting', running: 'Running', unknown: 'Needs checking', cancel_requested: 'Stop requested',
  completed: 'Accepted', failed: 'Failed', cancelled: 'Stopped',
};

/** A study's money: spent of its allowance, what is held for experiments in progress, and what is left. */
export function studySpending(e: Pick<Effort, 'capMicrousd' | 'spentMicrousd' | 'heldMicrousd' | 'availableMicrousd'>): string {
  return `${dollars(e.spentMicrousd)} spent of ${dollars(e.capMicrousd)}${e.heldMicrousd > 0 ? ` · ${dollars(e.heldMicrousd)} held` : ''} · ${dollars(e.availableMicrousd)} left`;
}

/** Its experiments counted by state, in the order they matter ("1 needs checking · 2 running · 3 finished"). */
export function taskCounts(e: Pick<Effort, 'tasks' | 'experiments'>): string {
  const order = ['review', 'unknown', 'waiting', 'running', 'launching', 'cancel_requested', 'planned', 'failed', 'finished', 'completed', 'cancelled'];
  const counts = new Map<string, number>();
  for (const task of Object.values(e.tasks ?? {})) {
    if(task.specification?.provider==='scientific-gcp-grant')continue;
    const phase = taskPhase(task); counts.set(phase, (counts.get(phase) ?? 0) + 1);
  }
  const words = order.filter(phase => counts.get(phase)).map(phase => `${counts.get(phase)} ${phaseWords(phase).toLowerCase()}`);
  const workflows = Object.keys(e.experiments ?? {}).length;
  if (workflows) words.push(`${workflows} workflow experiment${workflows === 1 ? '' : 's'}`);
  return words.length ? words.join(' · ') : 'No experiments yet';
}

/** What the study needs from you, if anything: experiments to check (their spending is held), or none. */
export function studyNeeds(e: Pick<Effort, 'tasks'>): string | null {
  const tasks = Object.values(e.tasks ?? {}).filter(task => task.specification?.provider !== 'scientific-gcp-grant');
  const review = tasks.filter(task => taskPhase(task) === 'review').length;
  const unknown = tasks.filter(task => task.status === 'unknown').length;
  const notes = [];
  if (review) notes.push(`${review} experiment${review === 1 ? ' is' : 's are'} ready for your review.`);
  if (unknown) notes.push(`${unknown} experiment${unknown === 1 ? ' needs' : 's need'} checking; its original execution must be reconciled and its spending remains held.`);
  return notes.join(' ') || null;
}

/** A study's short name: the first line of its objective, kept short. */
export function studyName(e: Pick<Effort, 'objective' | 'title'>): string {
  const first = (e.title?.trim() || e.objective || '').split('\n')[0].trim();
  return first.length > 80 ? `${first.slice(0, 79).trimEnd()}…` : first || 'Untitled study';
}


export interface StudyInputRef { id: string; revision: number; digest: string }
export interface StudyInput extends StudyInputRef {
  title: string; kind: string; managedFileEligible: boolean; scientificInputEligible?: boolean; localInputEligible?: boolean; limitation?: string | null;
  context?: { layers: { name: string; kind?: string | null }[]; arrays: { shape: number[]; axes: string[]; dtype: string; semantic?: string | null }[]; savedAt: number };
}
/** Only accepted metadata describes an input; names never imply a guessed producer. */
export function studyInputDescription(input: StudyInput): string {
  const kind = ({ 'source-file': 'Imported file', 'source-directory': 'Imported folder', scene: 'Scientific scene', animation: 'Animation' } as Record<string, string>)[input.kind] ?? input.kind.replaceAll('-', ' ');
  const layers = input.context?.layers.slice(0, 4).map(layer => `${layer.name}${layer.kind ? ` (${layer.kind})` : ''}`) ?? [];
  if ((input.context?.layers.length ?? 0) > 4) layers.push(`+${input.context!.layers.length - 4} layers`);
  const arrays = input.context?.arrays.slice(0, 3).map(array => `${array.axes.map((axis, index) => `${axis.toUpperCase()} ${array.shape[index]}`).join(' × ')} · ${array.dtype}${array.semantic ? ` · ${array.semantic.replaceAll('-', ' ')}` : ''}`) ?? [];
  const savedAt = input.context?.savedAt;
  const saved = typeof savedAt === 'number' && Number.isFinite(savedAt) ? `Saved ${new Date(savedAt * 1000).toLocaleString()}` : '';
  return [kind, layers.join(', '), arrays.join('; '), saved].filter(Boolean).join(' · ');
}
export interface StudyProvider { inputReady?: boolean; inputReason?: string | null; inputRoute?: 'question-only' | 'files' | 'scientific-tools' | null; id: string; label: string; ready: boolean; reason?: string | null; project?: string; maxReservationUsd?: number; maxBudgetCents?: number | null; accessVerified?: boolean }
export interface StudyEvidence {
  reference: StudyInputRef & { sha256: string };
  snapshot: Record<string, unknown>;
  completionVerified: boolean;
}
export interface StudyStart { request: string; provider: string; message: string; budgetCents: number | null; maximumUsd: number }
export type ReviewOutcome = 'completed' | 'failed' | 'cancelled';
const effortUrl = (id: string) => `/api/research-efforts/${encodeURIComponent(id)}`;
const taskUrl = (id: string, task: string) => `${effortUrl(id)}/tasks/${encodeURIComponent(task)}`;
async function mutate(url: string, body: Record<string, unknown>, fetcher: typeof fetch): Promise<Effort> {
  return (await reply(await fetcher(url, { method: 'POST', headers: HEADERS, body: JSON.stringify(body) }))).effort as Effort;
}
export async function studyInputs(session: string, fetcher: typeof fetch = fetch): Promise<StudyInput[]> {
  return (await reply(await fetcher(`/api/research-inputs?session=${encodeURIComponent(session)}`, { cache: 'no-store' }))).inputs as StudyInput[];
}
export async function studyReadiness(session: string, fetcher: typeof fetch = fetch, effort?: string): Promise<StudyProvider[]> {
  const query = new URLSearchParams({ session, ...(effort ? { effort } : {}) });
  return (await reply(await fetcher(`/api/research-readiness?${query}`, { cache: 'no-store' }))).providers as StudyProvider[];
}
export function startStudy(id: string, session: string, fields: StudyStart, fetcher: typeof fetch = fetch): Promise<Effort> {
  return mutate(`${effortUrl(id)}/start`, { session, ...fields }, fetcher);
}
export function collectStudyTask(id: string, session: string, task: string, action: 'sync' | 'close', fetcher: typeof fetch = fetch): Promise<Effort> {
  return mutate(`${taskUrl(id, task)}/${action}`, { session }, fetcher);
}
export function recoverStudyTask(id: string, session: string, task: string, providerSession: string, fetcher: typeof fetch = fetch): Promise<Effort> {
  return mutate(`${taskUrl(id, task)}/recover`, { session, providerSession }, fetcher);
}
export async function studyEvidence(id: string, session: string, task: string, fetcher: typeof fetch = fetch): Promise<StudyEvidence> {
  return await reply(await fetcher(`${taskUrl(id, task)}/evidence?session=${encodeURIComponent(session)}`, { cache: 'no-store' })) as unknown as StudyEvidence;
}
export function reviewStudyTask(id: string, session: string, task: string, evidenceSha256: string, outcome: ReviewOutcome, review: string, fetcher: typeof fetch = fetch): Promise<Effort> {
  return mutate(`${taskUrl(id, task)}/review`, { session, evidenceSha256, outcome, review }, fetcher);
}
export function retainStudyFinding(id: string, session: string, request: string, statement: string, tasks: string[], fetcher: typeof fetch = fetch): Promise<Effort> {
  return mutate(`${effortUrl(id)}/findings`, { session, request, statement, tasks }, fetcher);
}
/** A single observed lifecycle drives the list, summary and available actions. */
export function taskPhase(task: EffortTask): string {
  if (task.execution?.provider === 'local-researcher' && task.status === 'completed') return 'finished';
  if (task.status === 'cancel_requested' || task.status === 'unknown' || task.chargedMicrousd != null) return task.status;
  const seen = task.observation;
  if (seen?.sealedEvidence && ((task.execution?.provider === 'gemini-google' && seen.providerStatus === 'returned')
      || (seen.providerStatus === 'terminated' && seen.listCostMicrousd != null))) return 'review';
  if (task.status === 'running' && seen?.providerStatus === 'idle') return 'waiting';
  return task.status;
}
function phaseWords(phase: string): string { return ({ finished: 'Finished · findings not yet reviewed', review: 'Ready for review', waiting: 'Waiting, not finished' } as Record<string, string>)[phase] ?? TASK_WORDS[phase] ?? phase; }
export function taskState(task: EffortTask): string { return phaseWords(taskPhase(task)); }
export function providerName(provider?: string): string { return ({ 'claude-managed-google': 'Claude', 'gemini-google': 'Gemini' } as Record<string, string>)[provider ?? ''] ?? 'Researcher'; }
export function studyState(study: Effort): string {
  if (study.stopPending || (study.status === 'stopped' && Object.values(study.tasks).some(t => t.chargedMicrousd == null))) return 'Stop requested — awaiting confirmation';
  if (study.status === 'active' && !Object.keys(study.tasks).length && !Object.keys(study.experiments ?? {}).length) return 'Not started';
  return STUDY_WORDS[study.status] ?? study.status;
}

/** Scientific grants reserve their own allowance; they are not an unfinished provider session. */
export function pendingProviderTask(task: EffortTask): boolean {
  return task.chargedMicrousd == null && task.specification?.provider !== 'scientific-gcp-grant';
}
