import type { PresentationJob, ResearchOutput, ResearchSession } from './protocol';

/** Counts retained records and current outputs; never infers observations from prose. */
export function holdsText(s: Pick<ResearchSession, 'findings' | 'findingCount' | 'outputs' | 'result'>): string {
  const parts: string[] = [];
  const count = (n: number, one: string, many = `${one}s`) => {
    if (n > 0) parts.push(`${n} ${n === 1 ? one : many}`);
  };
  count(s.findingCount ?? s.findings.length, 'observation');
  count(s.result?.sources?.length ?? 0, 'source');
  const current = s.outputs.filter((o) => o.current !== false);
  count(current.filter((o) => o.kind === 'image').length, 'picture');
  count(current.filter((o) => o.kind === 'video').length, 'video');
  count(current.filter(isLesson).length, 'lesson');
  count(current.filter(o => o.contentKind === 'report').length, 'report');
  count(current.filter(o => o.kind === 'html' && !isLesson(o) && o.contentKind !== 'report').length, 'interactive artifact');
  return parts.join(', ');
}

/** A labelled retained value, not a computed trend, distribution or scientific judgment. */
export function retainedFigure(result: ResearchSession['result']): string | null {
  const chart = result?.charts?.find(chart => chart.transformation === 'identity' && chart.rows.length > 0
    && chart.rows.every(row => Number.isFinite(row.value) && !!row.unit && !!row.source?.contentRevision));
  if (!chart) return null;
  const row = chart.rows[0];
  return `${chart.title} · ${row.label}: ${row.value} ${row.unit}`
    + (row.denominator === null ? '' : ` · denominator ${row.denominator}`)
    + (chart.rows.length > 1 ? ` · ${chart.rows.length} retained observations` : '');
}

/** The selected artifact's own receipt, never the newest unrelated producer job. */
export function artifactReceipt(s: ResearchSession, output?: ResearchOutput, findingVersion?: number): string {
  const job = output && s.presentationJobs?.find(j => j.id === output.job || j.output === output.id && !!output.id);
  const at = output ? job?.finished : s.findingRevisions?.find(r => r.version === findingVersion)?.at;
  const parts = [at ? new Date(at * 1000).toLocaleString() : 'Time not recorded'];
  if (output?.change) parts.push(output.change);
  if (output) parts.push(job ? `$${job.cost.toFixed(3)} presentation${job.estimatedCost ? ` + $${job.estimatedCost.toFixed(3)} estimated, receipt pending` : ''}` : 'Presentation cost not recorded');
  return parts.join(' · ');
}

export function presentationTiming(job: PresentationJob, now = Date.now() / 1000): string {
  const start = job.dispatchedAt ?? job.created;
  const end = job.finished ?? (['queued', 'preparing'].includes(job.state) ? now : undefined);
  const delta = end === undefined ? undefined : end - start;
  const elapsed = delta === undefined ? 'Elapsed time not recorded'
    : !Number.isFinite(delta) || delta < 0 ? 'Timing unavailable (clock changed)' : `${Math.floor(delta)} s elapsed`;
  return `${elapsed} · ${job.deadlineSeconds ? `${job.deadlineSeconds} s production limit` : 'Production limit not recorded'}`;
}

export function spendingSummary(s: ResearchSession, compact = false): string {
  const produced = (s.presentationJobs ?? []).reduce((sum, j) => sum + j.cost, 0);
  const estimated = (s.spentEstimated ?? 0) + (s.presentationJobs ?? []).reduce((sum, j) => sum + j.estimatedCost, 0);
  return `$${(s.spent + produced).toFixed(3)} total recorded`
    + (compact ? '' : ` · $${s.spent.toFixed(3)} research · $${produced.toFixed(3)} presentations`)
    + (estimated ? ` · $${estimated.toFixed(3)} additional estimated, receipts pending` : '');
}

/** HTML is a format. Only retained content or its declared kind identifies a lesson. */
export function isLesson(output: ResearchOutput): boolean {
  return output.contentKind ? output.contentKind === 'lesson' : !!(output.content && 'kind' in output.content && output.content.kind === 'lesson');
}

export function isReportPresentation(output: ResearchOutput): boolean {
  return output.kind === 'presentation' || output.kind === 'html' && output.contentKind === 'report';
}
