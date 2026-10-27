// What restarting Hooke (to finish an update) or quitting it would stop, in the workspace's own words, for the Mac
// app's sheet (packaging/macos/HookeUI/UpdateWords.swift; app-updates.ts installs window.hookeUpdateReadiness). Each
// line is read from the work's own source, never from the Working strip's pills: a pill hidden with × drops out while
// its work goes on, and work under a second is held back there. A source that cannot be read throws, so the app says
// it can't check rather than "nothing is running". The same lines serve a restart and a quit, so what comes after is
// said as "when Hooke opens again". Each line says what a restart really does to that work: a
// researcher stops and shows as interrupted (services/orchestrator/research/AGENTS.md), Record carries on
// (DatasetCollector.resume, services/orchestrator/datasets/AGENTS.md), a Study's work at its provider goes on.

import { actionName } from './activity';
import type { Effort } from './research-efforts';
import { pillName } from './strip-sources';

/** The workspace's answer: what is unsaved (restart refused) and what is running (named before it stops). */
export interface UpdateReadiness { blockers: string[]; running: string[] }

/** The words of the one blocker a lost connection gives: nothing can be read, so nothing can be said saved. */
export const DISCONNECTED = 'The workspace has lost its connection to Hooke, so Hooke can’t tell whether your work is saved. Wait until it reconnects.';

/** A short, one-line form of words from elsewhere (a researcher's or a study's title). */
function short(text: string, max = 60): string {
  const one = text.replace(/\s+/g, ' ').trim();
  return one.length > max ? `${one.slice(0, max - 1).trimEnd()}…` : one;
}

/** A hand-started run as the workspace follows it (workspace-study.ts StudyRun): its status now, its model's name and
 * the Project it captured. */
export interface HandRunFact { status: string; name: string; project: string }
export function runLines(runs: readonly HandRunFact[]): string[] {
  return runs.flatMap(run => {
    const name = pillName(run.name), where = run.project ? ` in ${run.project}` : '';
    if (run.status === 'running') return [`${name}, running${where}`];
    if (run.status === 'stopping') return [`${name}, stopping${where}`];
    if (run.status === 'queued') return [`${name}, waiting to run${where}`];
    // Its outcome was lost (a reply that never came): it may still be running, so it is named.
    if (run.status === 'unknown') return [`${name}${where}, which may still be running`];
    return [];
  });
}

/** The page's own Record (main.ts: the recorder and the dataset the server collects). */
export interface RecordFact {
  /** This page's recorder ('idle' after a reload: the dataset and the camera go on without it). */
  recorder: 'idle' | 'starting' | 'recording' | 'saving';
  /** What it records: the screen (workspace, annotated) or the camera source alone. */
  scope: 'camera' | 'annotated' | 'workspace';
  /** Recording as the Record button says it (the recorder, or the dataset still collecting). */
  on: boolean;
  /** The server is collecting its dataset: that is what carries on after a restart. */
  collecting: boolean;
  /** Stop was pressed after a reload and the camera and the dataset are being stopped. */
  stopping: boolean;
  /** A Stop that could not be confirmed: the dataset may still be collecting. */
  stopUnconfirmed: boolean;
}
export function recordWords(fact: RecordFact): { blockers: string[]; running: string[] } {
  // Its file is being finished: restarting now could cut it short.
  if (fact.recorder === 'saving' || fact.stopping) return { blockers: ['Record is still saving the recording. Wait until it says Saved.'], running: [] };
  if (fact.recorder === 'starting') return { blockers: [], running: ['Record, which is starting'] };
  if (fact.stopUnconfirmed) return { blockers: [], running: ['Record (its Stop isn’t confirmed, so it may carry on when Hooke opens again)'] };
  // The dataset carries on at the next start, the time Hooke was down written down as a gap (DatasetCollector.resume),
  // and its camera video goes on as a new part; this page's screen recording cannot.
  if (fact.recorder === 'recording' && fact.scope !== 'camera')
    return { blockers: [], running: [fact.collecting ? 'The screen recording (Record carries on when Hooke opens again, without the screen)' : 'Record'] };
  if (fact.on) return { blockers: [], running: [fact.collecting ? 'Record (it carries on when Hooke opens again)' : 'Record'] };
  return { blockers: [], running: [] };
}

/** The Assistant (main.ts): an answer still being written, its steps still running after their answer, voice. */
export interface AssistantFact { writing: boolean; steps: readonly { tool: string; ok: boolean | null }[]; voice: boolean }
export function assistantLines(fact: AssistantFact): string[] {
  return [
    ...(fact.writing ? ['An answer the Assistant is still writing'] : []),
    ...fact.steps.filter(step => step.ok === null).map(step => `${actionName(step.tool)}, started by the Assistant`),
    ...(fact.voice ? ['Your voice conversation'] : []),
  ];
}

/** Every composer's field (composer.ts: the Assistant's, a researcher's, a study's) with words not sent yet. The
 * Assistant's draft is usually kept for its Project, the others are not, so the line says only that it may not come
 * back. */
export function unsentLines(fields: readonly { value: string }[]): string[] {
  const unsent = fields.filter(field => field.value.trim()).length;
  if (!unsent) return [];
  return [unsent === 1 ? 'Writing a message you haven’t sent (it may not come back)' : `Writing ${unsent} messages you haven’t sent (they may not come back)`];
}

const ACTIVE = new Set(['queued', 'starting', 'running', 'revising']);
const WATCHING = new Set(['watching', 'checkpoint', 'finishing']);
/** Researchers still working, in every Project: `GET /api/research` lists the open Project's and those still working
 * in others (each says its Project, `sitting`; research/manager.py list_wire). A restart stops each and it shows as
 * interrupted afterwards (research/AGENTS.md); nothing carries it on by itself. */
export function researcherLines(wire: unknown, current: string, projectName: (sid: string) => string | undefined): string[] {
  const sessions = (wire as { sessions?: unknown } | null)?.sessions;
  if (!Array.isArray(sessions)) throw new Error('The researchers could not be read.');
  return sessions.flatMap(raw => {
    const s = raw as { purpose?: string; status?: string; mission?: { status?: string } | null; name?: string; label?: string; brief?: string; sitting?: string };
    if (s.purpose !== 'research' || !(ACTIVE.has(String(s.status)) || WATCHING.has(String(s.mission?.status)))) return [];
    const what = short(String(s.label || s.brief || ''));
    const where = s.sitting && s.sitting !== current ? ` in ${projectName(s.sitting) ?? 'another Project'}` : '';
    return [`${s.name || 'A researcher'}${what ? `, “${what}”` : ''}${where} (it shows as interrupted when Hooke opens again)`];
  });
}

/** The open Project's Studies with tasks still going (research-efforts.ts). Work at a provider the collector follows
 * (Claude managed research, private cloud work) goes on there and is collected when Hooke opens again
 * (research/collector.py); anything else waiting for a reply (Gemini, a start not yet confirmed) may lose it. */
const COLLECTED = new Set(['claude-managed-google', 'scientific-gcp-grant']);
const GOING = new Set(['launching', 'running', 'unknown', 'cancel_requested']);
export function studyLines(studies: readonly Effort[] | null): string[] {
  return (studies ?? []).flatMap(study => {
    const going = Object.values(study.tasks ?? {}).filter(task => GOING.has(String(task.status)));
    if (!going.length) return [];
    const name = short((study.title?.trim() || study.objective || 'Untitled study').split('\n')[0]);
    const collected = going.every(task => {
      const provider = String(task.specification?.provider ?? '');
      return COLLECTED.has(provider) && (provider === 'scientific-gcp-grant' || Boolean(task.execution));
    });
    return [collected ? `Study “${name}” (its work at the provider goes on, and Hooke collects it when it opens again)`
      : `Study “${name}” (a reply it is waiting for may be lost)`];
  });
}

/** Tool sessions open in this workspace (`GET /api/scientific-reviews`: the scientific windows' workers). A restart
 * stops each worker, and what was not saved in it is lost (Stop discards unsaved work there). */
export function toolSessionLines(titles: readonly string[]): string[] {
  return titles.map(title => `The tool session for “${short(title)}” (what you haven’t saved in it is lost)`);
}

/** One readiness answer from its parts: any part that throws makes the whole answer throw (unreadable). */
export async function readReadiness(parts: { blockers: () => string[]; running: readonly (() => string[] | Promise<string[]>)[] }): Promise<UpdateReadiness> {
  const blockers = parts.blockers();
  const running = (await Promise.all(parts.running.map(read => read()))).flat();
  return { blockers, running };
}
