/** Readiness in words (orchestrator/readiness.py; ADR 0019): one status, the next step and the rest, the same on the
 * Models menu and the Capabilities page. Pure: what the server sent, said plainly. */
import type { Readiness, ReadinessStep, SetupCounts } from './protocol';

/** The next step, with how to do it: "Needs your Google Gemini API key: put GEMINI_API_KEY=... in ~/Desktop/micromind.txt". */
export function readinessWords(r: Readiness | undefined): string {
  if (!r || r.status === 'ready') return '';
  const how = r.next?.how && r.next.how !== r.word && r.next.how !== r.next.says ? `: ${r.next.how}` : '';
  return `${r.word}${how}`;
}

/** The rest of the checklist after the next step, one line each ("" when there is none). */
export function laterSteps(r: Readiness | undefined): string[] {
  return (r?.steps ?? []).slice(1).map((s: ReadinessStep) => s.says);
}

/** An option's note in the Models menu: what we measured, then what it needs here. A model that downloads when first
 * used can still be chosen, and says so. */
export function optionNote(note: string, available: boolean, r: Readiness | undefined, why?: string): string {
  const words = readinessWords(r);
  if (available) return words ? `${note} · ${words}` : note;
  const need = words || (why?.trim() ? `${why.trim().charAt(0).toUpperCase()}${why.trim().slice(1)}` : 'Needs an API key');
  return `${note}. ${need}.`;
}

/** "155 of 172 can run here · Add your OpenAI API key: +13 · Add your fal.ai key: +1" ("" without counts). */
export function countLine(counts: SetupCounts | undefined): string {
  if (!counts) return '';
  const unlock = counts.unlock.map((u) => `Add your ${u.service} key: +${u.adds}`);
  return [`${counts.works} of ${counts.total} can run here`, ...unlock].join(' · ');
}
