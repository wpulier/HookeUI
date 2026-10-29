/** Client-side Ask recovery. Request IDs bind late results to the original draft/conversation. */
export interface AskScope { day: string | null; thread: string | null }
export interface AskFailure extends AskScope { error: string; status: string; draft: string; current: boolean }
const same = (a: AskScope, b: AskScope) => a.day === b.day && a.thread === b.thread;
/** The server's reasons in words a person can act on (the server's own words are a ticket; until then they are mapped here). */
const PLAIN_REASONS: [RegExp, string][] = [
  [/planner did not answer/i, 'The Assistant did not return an answer. Your question is kept; check its model status or try again.'],
  [/^CLIENT_MISMATCH\b/, 'This window lost its link to the studio. Reload to reconnect.'],
];
/** A reason in words: a known one as a sentence a person can act on (`plain`), else the server's own words without a
 * leading code ("CODE_NAME: …"), never a raw code. */
export function askReason(error: unknown): { words: string; plain: boolean } {
  const raw = (error instanceof Error ? error.message : error == null ? '' : String(error)).trim();
  const plain = PLAIN_REASONS.find(([pattern]) => pattern.test(raw))?.[1];
  if (plain) return { words: plain, plain: true };
  const words = raw.replace(/^[A-Z][A-Z_]+:\s*/, '');
  const said = words !== raw && words ? words.charAt(0).toUpperCase() + words.slice(1) : words; // a sentence once its code goes
  return { words: said || 'The studio could not accept this request.', plain: false };
}
export function askError(error: unknown): string {
  return askReason(error).words;
}
export class AskRequests {
  private readonly requests = new Map<string, AskScope & { text: string }>();
  started(id: string, text: string, scope: AskScope): void {
    this.requests.set(id, { ...scope, text });
    if (this.requests.size > 32) this.requests.delete(this.requests.keys().next().value!);
  }
  pending(scope: AskScope): boolean { return [...this.requests.values()].some(request => same(request, scope)); }
  settle(id: string | null | undefined, ok: boolean, error: unknown, scope: AskScope, draft: string): AskFailure | null {
    if (!id) return null;
    const request = this.requests.get(id);
    if (!request) return null;
    this.requests.delete(id);
    if (ok) return null;
    const current = same(request, scope), { words: reason, plain } = askReason(error);
    // Never overwrite newer typing, or place another conversation's request here. A known reason is the whole status,
    // a sentence that says what happened; anything else says that the request failed, with the server's words.
    return { day: request.day, thread: request.thread, error: reason,
      status: plain ? reason : `Request failed: ${reason}`, current,
      draft: current && !draft.trim() ? request.text : draft };
  }
  retire(scope: AskScope, draft: string): string {
    const current = [...this.requests.values()].find(request => same(request, scope));
    this.requests.clear();
    return !draft.trim() && current ? current.text : draft;
  }
}
