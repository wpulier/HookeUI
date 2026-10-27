// A live quiz on the slide (orchestrator/quiz.py): the line above a lesson that says which step
// is open, how it is answered and how the user has done so far. The server decides the step,
// its kind and the outcomes (the model judged them); this only words them.

import type { PresenterState, StepOutcome } from './protocol';

const HOW: Record<string, string> = {
  find: 'find it on your slide and click it',
  evidence: "what would confirm or refute the app's guess?",
};

/** "q1 right, q2 to go over" in a few words: how many right, which to go over (wrong or skipped). */
export function tally(outcomes: Record<string, StepOutcome> | undefined): string {
  const entries = Object.entries(outcomes ?? {});
  if (!entries.length) return '';
  const right = entries.filter(([, o]) => o === 'right').length;
  const over = entries.filter(([, o]) => o === 'wrong' || o === 'skipped').map(([id]) => id)
    .sort((a, b) => Number(a.replace(/\D/g, '')) - Number(b.replace(/\D/g, '')));
  return [`${right} right`, ...(over.length ? [`to go over: ${over.map(questionName).join(', ')}`] : [])].join(' · ');
}

function questionName(id: string): string {
  return id.replace(/^q/i, 'question ');
}

/** The note above a lesson on a step: "On question 2 · find it on your slide and click it · 1 right". */
export function stepNote(state: PresenterState): string {
  const step = state.step ?? '';
  if (!step) return '';
  const how = HOW[state.stepKind ?? ''] ?? '';
  return [`On ${questionName(step)}`, how, tally(state.outcomes)].filter(Boolean).join(' · ');
}
