/** What a Project tab's run chip says. It leads with what actually happened, in a word or two, and never
 * folds different outcomes together: before, queued work read "Running" (the busy check came first) and a failed,
 * stopped or interrupted run all read "Attention". The full message stays available as the chip's title. */
import type { StudyRun } from './workspace-study-model';

export type RunChipTone = 'busy' | 'done' | 'problem' | 'quiet';
export interface RunChip { label: string; tone: RunChipTone; title: string }

export function runChipWords(run: Pick<StudyRun, 'status' | 'message'> & { interrupted?: boolean }): RunChip {
  const title = run.message || '';
  switch (run.status) {
    case 'queued': return { label: 'Queued', tone: 'busy', title };
    case 'running': return { label: 'Running', tone: 'busy', title };
    case 'stopping': return { label: 'Stop requested', tone: 'busy', title: title || 'Stop was requested; it says Stopped once the work has ended.' };
    case 'unknown': return { label: 'Status unknown', tone: 'quiet', title: title || 'The app could not confirm whether this work is still running.' };
    case 'completed': return { label: 'Done', tone: 'done', title };
    case 'stopped': return { label: 'Stopped', tone: 'quiet', title };
    case 'failed': return run.interrupted ? { label: 'Interrupted', tone: 'problem', title } : { label: 'Failed', tone: 'problem', title };
  }
}

/** Whether a Project row shows its run's chip: on the Project in front only when it needs saying (Failed, Interrupted,
 * Stop requested; the run's footer and strip pill say the rest); on a Project not in front also while it runs or waits,
 * where the chip is the only sign its run is still going. Never Done. */
export function projectChipShown(run: Pick<StudyRun, 'status'>, inFront: boolean): boolean {
  if (run.status === 'failed' || run.status === 'stopping') return true;
  return !inFront && (run.status === 'running' || run.status === 'queued');
}
