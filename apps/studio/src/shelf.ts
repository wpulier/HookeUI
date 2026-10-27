// The shelf (CONTEXT.md "Shelf"): which researchers show on the Working strip under the Stage (working-strip.ts), and
// what each one's pill says. A researcher is on it while it works (queued, starting, running, revising) or needs you,
// or once it finished and its work was not opened yet: its lit cell, its name and its elapsed time or its state. Clicking
// opens its Tab; × takes it off the strip for the day (the server keeps that in the day's journal, so a reload or a
// restart does not bring it back) and it stays in Researchers, where asking for it still opens it. Will's decision of
// 2026-10-11 (plan Q22) brought it back; his of 2026-10-24 (workspace-feel plan) moved it from the Stage's corner into
// the strip, beside every run at work, so a researcher shows as its Tab and its live pill.

import type { PresenterState, ResearchSession } from './protocol';
import { clean } from './presentation';
import { ACTIVE, isWorking } from './research-state';
import { outputOf } from './viewer';
import { stopRequested } from './research-stop';
import type { LitCellState } from './lit-cell';
import { clock, type StripPill, type Urgency } from './working-strip';
import type { ResearchCardView } from './chat-cards';
export { ACTIVE, isWorking };
export { holdsText } from './research-summary';

/** Ended without work to open; a researcher the user stopped is not news. */
const STOPPED = new Set(['failed', 'timeout', 'over_budget', 'interrupted']);
const OPENED_KEY = 'studio-shelf-opened'; // per browser and day: the shelf is a convenience, never the record

/** Work there is to open: done or partial (or revising, while the version before it is still there). */
export const isReady = (s: ResearchSession): boolean =>
  (s.status === 'done' || s.status === 'partial' || s.status === 'revising') && (s.outputs.length > 0 || !!s.result);
export const isStopped = (s: ResearchSession): boolean => STOPPED.has(s.status) && !isReady(s);

/** This session's researchers as the Researchers tab lists them: research only (never the descriptions' quick
 * lookups), whose `sitting` is the session open now (its sid), newest first. */
export function todaysResearch(sessions: Iterable<ResearchSession>, sitting: string): ResearchSession[] {
  return [...sessions].filter((s) => s.purpose === 'research' && (!sitting || s.sitting === sitting))
    .sort((a, b) => b.created - a.created);
}

/** Researchers still working in other sessions (started there, they go on after a switch): the Researchers tab lists
 * them under "Still working in other sessions", and counts them on its badge. Newest first. */
export function workingElsewhere(sessions: Iterable<ResearchSession>, sitting: string): ResearchSession[] {
  if (!sitting) return [];
  return [...sessions].filter((s) => s.purpose === 'research' && !!s.sitting && s.sitting !== sitting && isWorking(s))
    .sort((a, b) => b.created - a.created);
}

/** Effort seconds are a planning budget, not a measured fraction of work completed. */
export function progressOf(_s: ResearchSession, _now = Date.now() / 1000): number | null {
  return null;
}

/** Ended since the app last started: one restored after a restart is not news, unless it worked again since (the
 * server keeps `restored` on it, so this page remembers what it saw working). */
const fresh = (s: ResearchSession, workedHere: ReadonlySet<string>) => !s.restored || workedHere.has(s.rid);

/** What "opened" is kept by: the researcher and when its run ended, so a later run's work (a follow-up, a new
 * version) is news again. */
export const openedKey = (s: Pick<ResearchSession, 'rid' | 'finished'>): string => `${s.rid}@${s.finished ?? ''}`;

/** Its question to the user, while it works and waits for an answer. */
export const asking = (s: ResearchSession) => isWorking(s) ? (s.requests ?? []).find((q) => q.status === 'asked') : undefined;

/** The researchers the strip shows, oldest first: today's researchers that work, or ended and were not opened, unless
 * taken off. Opened work leaves the strip (its Tab opened, and it stays in Researchers); one restored after a restart is
 * not news either, unless it works again. */
export function shelfChips(sessions: Iterable<ResearchSession>, presenter: PresenterState | null, sitting: string,
  opened: ReadonlySet<string>, dismissed: ReadonlySet<string> = new Set(), workedHere: ReadonlySet<string> = new Set()): ResearchSession[] {
  const removed = new Set([...dismissed, ...(presenter?.shelfRemoved ?? [])]);
  const news = (s: ResearchSession) => isWorking(s)
    || (fresh(s, workedHere) && !opened.has(openedKey(s)) && (isReady(s) || isStopped(s)));
  return [...sessions].filter((s) => s.purpose === 'research' && (!sitting || s.sitting === sitting)
    && !removed.has(s.rid) && news(s))
    .sort((a, b) => a.created - b.created);
}

export type CountState = '' | 'working' | 'fresh' | 'asking';

/** The Researchers tab's count: exactly the cards it lists (this session's research, and those still working in other
 * sessions), coloured when they need you: a question waiting (iodine), new work not opened yet (green), working
 * (accent). */
export function researchersCount(sessions: Iterable<ResearchSession>, sitting: string, fresh: (s: ResearchSession) => boolean):
  { count: number; working: number; elsewhere: number; state: CountState } {
  const all = [...sessions];
  const here = todaysResearch(all, sitting);
  const elsewhere = workingElsewhere(all, sitting);
  const listed = [...here, ...elsewhere];
  const working = listed.filter(isWorking).length;
  const state: CountState = listed.some((s) => asking(s)) ? 'asking' : here.some(fresh) ? 'fresh' : working ? 'working' : '';
  return { count: listed.length, working, elsewhere: elsewhere.length, state };
}

/** What the chip's second line says: what it is doing, or what it made, in plain words. */
export function chipStatus(s: ResearchSession): string {
  const question = asking(s);
  // A long or costly run asks whether to keep going (research/nail-four): said as that, with why.
  if (question) return question.keepGoing ? `${s.name} asks whether to keep going: ${clean(question.why)}`
    : `${s.name} asks for ${clean(question.what)}`;
  if (isWorking(s)) {
    if (s.status === 'queued') return `${s.name} · waiting for a free slot`;
    if (s.status === 'revising') return `${s.name} · making a new version`;
    return s.step ? `${s.name} · ${clean(s.step)}` : `${s.name} · working`;
  }
  if (isReady(s)) {
    const kind = madeKind(s);
    const latest = outputOf(s, s.items?.[0]);
    const version = latest && (latest.version ?? 1) > 1 ? ` v${latest.version}` : '';
    return s.status === 'partial' ? `Partial findings · ${s.name}` : `${kind}${version} ready · ${s.name}`;
  }
  // A failed run's last step is its reason (researcher.py makes the failure its step): said wherever it is named.
  if (s.status === 'failed') { const why = clean(s.step ?? '').trim(); return why ? `${s.name} could not finish: ${why}` : `${s.name} could not finish`; }
  return s.status === 'interrupted' ? `${s.name} was interrupted` : `${s.name} stopped`;
}

/** What its finished work is, in a word: a lesson, a video, a picture, else findings. */
function madeKind(s: ResearchSession): string {
  return s.outputs.some((o) => o.kind === 'html') ? 'Lesson' : s.outputs.some((o) => o.kind === 'video') ? 'Video'
    : s.outputs.some((o) => o.kind === 'image') ? 'Picture' : 'Findings';
}

/** A researcher's lit cell (lit-cell.ts), the same on its Tab and its pill: gold while it works, still once Stop is
 * asked, blue when it needs you, green when its work is ready. */
export function researcherCell(row: ResearchSession): LitCellState {
  if (stopRequested(row)) return 'still';
  const state = pillState(row);
  return state === 'asking' ? 'needs' : state === 'queued' ? 'queued' : state === 'working' ? 'working' : state === 'partial' ? 'partial'
    : state === 'ready' ? 'ready' : row.status === 'failed' ? 'attention' : 'stopped';
}

/** What a researcher's pill says when it is not counting its time (the mock's words). */
export function researcherWords(s: ResearchSession): string {
  if (asking(s)) return 'Needs you';
  if (isWorking(s)) return stopRequested(s) ? 'Stop requested' : s.status === 'queued' ? 'Waiting for a free slot' : 'Working';
  if (isReady(s)) return s.status === 'partial' ? 'Partial findings' : `${madeKind(s)} ready`;
  return s.status === 'interrupted' ? 'Interrupted' : s.status === 'failed' ? 'Could not finish' : 'Stopped';
}

/** A researcher's card in the chat (chat-cards.ts), under the request that started it: its lit cell, what it was asked,
 * and "Researcher 5 · Choosing a model" (its own progress words), "Researcher 3 · Working 0:12" (no words yet: its
 * clock), "Researcher 4 · Needs you", "Researcher 1 · Findings ready", as its Tab and pill say them. */
export function researcherCardView(s: ResearchSession, now = Date.now() / 1000): ResearchCardView {
  const working = isWorking(s), question = asking(s), stopping = stopRequested(s);
  const step = working && !question && !stopping && s.status !== 'queued' && s.status !== 'revising' ? clean(s.step ?? '').trim() : '';
  const counting = working && !question && !stopping && s.status !== 'queued' && s.status !== 'revising' && !step;
  const since = s.started ?? s.created ?? now;
  const words = step ? step.charAt(0).toUpperCase() + step.slice(1)
    : counting ? `Working ${clock(Math.max(0, now - since) * 1000)}`
    : working && s.status === 'revising' && !question && !stopping ? 'Making a new version'
    : researcherWords(s);
  const title = (s.result?.title || s.label || s.brief || s.name).trim();
  const cell = researcherCell(s);
  const timing = working && !question && s.status !== 'queued' ? ` · Working ${clock(Math.max(0, now - since) * 1000)}` : '';
  // A researcher that could not finish says why in its tooltip, as its pill and Tab do.
  const tip = s.status === 'failed' && !working ? `${title}\n${chipStatus(s)}` : `${title}${timing}`;
  return { cell, label: `${s.name}: ${researcherWords(s)}`, title, sub: `${s.name} · ${words}`, counting, tip };
}

/** A researcher's pill on the Working strip: needs you first, then working (its time counted from its start), then
 * finished; `open` and `dismiss` are the shelf's. */
export function researcherPill(s: ResearchSession, open: () => void, dismiss: () => void): StripPill {
  const working = isWorking(s), question = asking(s);
  const urgency: Urgency = question ? 0 : working ? 1 : 2;
  const counting = working && !question && !stopRequested(s) && s.status !== 'queued';
  const what = (s.result?.title || s.label || s.brief || s.name).trim();
  const action = isReady(s) && !working ? 'Open its work' : working ? 'Open its Tab to see what it is doing' : 'Open its Tab to see what happened';
  return {
    id: `research:${s.rid}`, name: s.name, cell: researcherCell(s), urgency,
    since: ((working ? s.started ?? s.created : s.finished ?? s.started ?? s.created) || 0) * 1000,
    counting, ...(counting ? {} : { words: researcherWords(s) }),
    title: `${what}\n${chipStatus(s)}\n${action}`,
    hide: `Hide ${s.name} from the strip. It stays in Researchers.`,
    open, dismiss,
  };
}

/** The picture a researcher shows: its newest picture, else the view it started from. */
export function pictureOf(s: ResearchSession): string {
  const picture = [...s.outputs].reverse().find((o) => o.kind === 'image' && o.current !== false);
  return picture?.url ?? s.snapshot;
}

export class Shelf {
  private readonly dismissed = new Set<string>(); // taken off here, until the server's list says so too
  private readonly opened = new Set<string>(); // finished work the user has opened (this browser, today)
  private readonly workedHere = new Set<string>(); // researchers seen working since this page loaded
  private readonly show: (rid: string) => void;
  private readonly remove: (rid: string) => void;
  private readonly sink: (pills: StripPill[]) => void;
  private sessions: ResearchSession[] = [];
  private presenter: PresenterState | null = null;
  private day = '';
  /** Told when what counts as opened changes (the tab's count turns from green). */
  onChange: () => void = () => {};

  /** `show` opens a researcher's Tab, `remove` tells the server it is off the strip for the day, and `sink` takes the
   * pills (the Working strip's researchers). */
  constructor(show: (rid: string) => void, remove: (rid: string) => void, sink: (pills: StripPill[]) => void = () => {}) {
    this.show = show;
    this.remove = remove;
    this.sink = sink;
  }

  /** New work the user has not opened yet (the Researchers count's green, the researchers board). */
  isNew(s: ResearchSession): boolean {
    return isReady(s) && !isWorking(s) && fresh(s, this.workedHere) && !this.opened.has(openedKey(s));
  }

  /** An ended researcher that is news: its work is ready, or it stopped on its own (failed, out of time or money,
   * interrupted), and it was not opened yet. The strip's rule (shelfChips), so a researcher's Tab and its pill stay
   * together until it is opened or closed; one restored after a restart is not news unless it worked here. */
  isNews(s: ResearchSession): boolean {
    return !isWorking(s) && fresh(s, this.workedHere) && !this.opened.has(openedKey(s)) && (isReady(s) || isStopped(s));
  }

  /** The user opened its work (from its pill, its Tab, Researchers or anywhere its page showed it): a finished pill
   * leaves the strip. */
  markOpened(rid: string): void {
    const key = openedKey(this.sessions.find((s) => s.rid === rid) ?? { rid, finished: null });
    if (this.opened.has(key)) return;
    this.opened.add(key);
    try { localStorage.setItem(`${OPENED_KEY}:${this.day}`, JSON.stringify([...this.opened].slice(-200))); } catch { /* optional */ }
    this.render();
    this.onChange();
  }

  /** This session's researchers (`sitting`, today's day). */
  update(sessions: Iterable<ResearchSession>, presenter: PresenterState | null, sitting = ''): void {
    if (sitting && sitting !== this.day) this.loadOpened(sitting);
    this.presenter = presenter;
    this.sessions = [...sessions];
    for (const s of this.sessions) if (isWorking(s)) this.workedHere.add(s.rid);
    if (presenter?.open && presenter.rid) {
      const s = this.sessions.find((x) => x.rid === presenter.rid);
      if (s && isReady(s)) this.markOpened(s.rid);
    }
    this.render();
  }

  /** The researchers on the strip now, oldest first. */
  chips(): ResearchSession[] {
    return shelfChips(this.sessions, this.presenter, this.day, this.opened, this.dismissed, this.workedHere);
  }

  private loadOpened(day: string): void {
    this.day = day;
    this.opened.clear();
    try {
      const kept: unknown = JSON.parse(localStorage.getItem(`${OPENED_KEY}:${day}`) || '[]');
      if (Array.isArray(kept)) for (const rid of kept) if (typeof rid === 'string') this.opened.add(rid);
    } catch { /* browser storage is optional: everything finished counts as new until opened here */ }
  }

  private render(): void {
    this.sink(this.chips().map((s) => researcherPill(s, () => this.open(s.rid), () => this.dismiss(s.rid))));
  }

  /** Its pill was clicked: its Tab opens (finished or stopped work counts as seen, so its pill leaves). */
  private open(rid: string): void {
    const now = this.sessions.find((x) => x.rid === rid);
    if (now && !isWorking(now)) this.markOpened(rid);
    this.show(rid);
  }

  /** Its × : off the strip at once; the server keeps it off for the day. It stays in Researchers. */
  private dismiss(rid: string): void {
    this.dismissed.add(rid);
    this.remove(rid);
    this.render();
  }
}

/** A researcher pill's state, which the stylesheet colours (its ring, its check, its line): the shelf's and the
 * workspace's researcher pills say it the same way. */
export function pillState(s: ResearchSession): 'asking' | 'queued' | 'working' | 'partial' | 'ready' | 'stopped' {
  const working = isWorking(s), ready = isReady(s) && !working;
  return asking(s) ? 'asking' : working ? (s.status === 'queued' ? 'queued' : 'working')
    : ready ? (s.status === 'partial' ? 'partial' : 'ready') : 'stopped';
}

