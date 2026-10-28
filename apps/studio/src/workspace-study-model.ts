import type { AssetMeta, FrameHeader, SessionRow } from './protocol';
import type { ResourceEntry } from './madetoday';

export type StudyLayout = 'baseline' | 'global' | 'contextual';
export type StudySection = 'inputs' | 'projects' | 'library' | 'tools' | 'results' | 'history' | 'research';
export interface StudySnapshot {
  operationInput?: import('./workspace-views-model').WorkspaceInput;
  connected: boolean;
  ready: boolean;
  session: string;
  title: string;
  projects: SessionRow[];
  /** Where every Project is saved on this Mac, with ~ (the sessions wire's `root`), for All projects' Show in Finder. */
  projectsRoot?: string;
  sources: AssetMeta[];
  source: AssetMeta | null;
  onStage: string | null;
  /** The bridge has said what is on the Stage (its sources' first report); until then `onStage` null means not heard
   * yet, never "nothing on the Stage". */
  heard?: boolean;
  selection: { id: string; label: string } | null;
  frame: Pick<FrameHeader, 'id' | 'boot' | 'w' | 'h'> | null;
  held: boolean;
  sourceSize?: { width: number; height: number } | null;
}
export interface StudyRun {
  id: string;
  input: StudySnapshot;
  status: 'running' | 'stopping' | 'completed' | 'queued' | 'failed' | 'stopped' | 'unknown';
  message: string;
  action: string;
  scope: string;
  outputTab?: string;
  backgroundJob?: {id: string; session: string};
  /** The background job ended as interrupted (it still counts as failed work). */
  interrupted?: boolean;
}
export interface StudyResource extends ResourceEntry {
  source?: ResourceEntry['source'] & { sourceImage?: { asset?: string; [key: string]: unknown } };
  madeBy?: ResourceEntry['madeBy'] & { execution?: string; operation?: string; policy?: string; origin?: string };
}
export type ResultGroup = 'artifacts' | 'answers' | 'records';
const ARTIFACT_KINDS = new Set(['annotation', 'population', 'tracks', 'research', 'recording', 'snapshot', 'dataset', 'bookmark', 'clip', 'individual', 'job', 'named-outline', 'result-table']);
const INPUT_KINDS = new Set(['asset', 'snapshot', 'dataset', 'volume']);
export function resultGroup(entry: StudyResource): ResultGroup {
  if (ARTIFACT_KINDS.has(entry.kind) || entry.kind === 'result' && (entry.product === true || entry.table === true)) return 'artifacts';
  if (entry.kind === 'result') return 'answers';
  return 'records';
}
export function isResult(entry: StudyResource): boolean {
  return !INPUT_KINDS.has(entry.kind) && !(entry.kind === 'result' && ['snapshot', 'enhance'].includes(entry.madeBy?.tool ?? ''));
}
export function sourceOf(entry: StudyResource): string | null {
  return entry.source?.sourceImage?.asset ?? null;
}
export function visibleResults(entries: StudyResource[], group: string, source: string | null): StudyResource[] {
  return entries.filter(entry => isResult(entry) && (group === 'all' || resultGroup(entry) === group) && (!source || sourceOf(entry) === source))
    .sort((a, b) => Number(resultGroup(a) === 'records') - Number(resultGroup(b) === 'records') || (b.at ?? 0) - (a.at ?? 0));
}
/** DATA is session-produced work, not the calendar-day model statistics or imported source library. */
export function visibleData(entries: StudyResource[], group: string, source: string | null): StudyResource[] {
  return entries.filter(entry => !['asset', 'volume', 'outcome'].includes(entry.kind)
    && (group === 'all' || resultGroup(entry) === group) && (!source || sourceOf(entry) === source))
    .sort((a,b) => (b.at ?? 0) - (a.at ?? 0));
}
export function capturedRun(id: string, snapshot: StudySnapshot, action = 'Bright objects', scope = snapshot.source?.kind === 'still' ? 'Whole original image' : 'Current camera field'): StudyRun {
  return { id, input: structuredClone(snapshot), status: 'running', message: 'Running on the confirmed input.', action, scope };
}
/** The input on stage, as the Inputs drawer remembers it: nothing on stage is ''. Compared raw, null never equalled
 * the remembered '' and every update rebuilt the drawer (every ~350 ms on a new Project; VM, 2026-10-19), so a click
 * spanning a rebuild landed on a removed button. */
export function stageKey(onStage: string | null | undefined): string {
  return onStage ?? '';
}
export function panelWidth(value: number): number {
  return Number.isFinite(value) ? Math.max(280, Math.min(520, value)) : 340;
}

/** A background reply is admission only. Settle against the exact retained job and owner. */
export function reconcileBackgroundRun(run: StudyRun, entries: StudyResource[], session: string): boolean {
  const job = run.backgroundJob;
  if (!job || job.session !== session || ['completed', 'failed', 'stopped'].includes(run.status)) return false;
  const item = entries.find(entry => entry.kind === 'job' && entry.id === job.id && entry.day === session);
  if (!item) return false;
  const status = item.state === 'done' ? 'completed' : item.state === 'failed' || item.state === 'interrupted' ? 'failed' : item.state === 'cancelled' ? 'stopped' : 'queued';
  const message = item.summary || (status === 'completed' ? 'Done' : item.state);
  const interrupted = item.state === 'interrupted';
  const changed = run.status !== status || run.message !== message || !!run.interrupted !== interrupted;
  run.status = status; run.message = message; run.interrupted = interrupted;
  return changed;
}
