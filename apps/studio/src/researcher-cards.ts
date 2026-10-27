import { duration } from './activity';
// The side bar's Researchers: one card per researcher of this session (research only: the descriptions'
// quick lookups are not researchers you asked for), newest first, whether or not it is on the shelf (shelf.ts), then
// any still working in another session (started there, it goes on after a switch) under "Still working in other
// sessions". A
// card shows its picture with its lit cell on the corner, its title and what it is doing or made; a question it asks you
// always shows its answers; See full research and Stop show on hover or focus in the strip, always in the full view.
// Its count on the tab is the number of cards (shelf.researchersCount), so it never counts what it does not show
// (2026-10-11: the tab said 4 with one card, counting three hidden quick lookups).

import type { ClientCommand, PresenterState, ResearchSession } from './protocol';
import { evidenceActions } from './research-evidence';
import { holdsText } from './research-summary';
import { asking, chipStatus, isReady, isWorking, pictureOf, researcherCell, researcherWords, todaysResearch, workingElsewhere } from './shelf';
import { litCell } from './lit-cell';
import { requestStop, stopButtonWords } from './research-stop';

export class ResearcherCards {
  private readonly root: HTMLElement;
  private readonly timing = new Map<string, HTMLElement>();
  private readonly open: (rid: string) => void;
  private readonly send?: (command: ClientCommand) => unknown;
  private readonly activity?: (session?: string) => void;
  private readonly isNew: (s: ResearchSession) => boolean;
  private sessions: ResearchSession[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;

  /** `open` brings a researcher's work up beside the view; `isNew` is the shelf's "finished, not opened yet"; `root` is
   * where the cards go (the tray's tab, `#researcher-cards`, or the side bar's compact column). */
  constructor(open: (rid: string) => void, send?: (command: ClientCommand) => unknown,
              isNew: (s: ResearchSession) => boolean = () => false, root: HTMLElement = document.getElementById('researcher-cards')!,
              activity?: (session?: string) => void) {
    this.root = root;
    this.open = open;
    this.send = send;
    this.activity = activity;
    this.isNew = isNew;
  }

  /** This session's researchers (`sitting`, the session open now), and those still working in another session. */
  update(sessions: Iterable<ResearchSession>, presenter: PresenterState | null, sitting = ''): void {
    const all = [...sessions];
    const here = todaysResearch(all, sitting);
    const elsewhere = workingElsewhere(all, sitting);
    this.sessions = [...here, ...elsewhere];
    const focused = this.focusedKey();
    this.timing.clear();
    const nodes: HTMLElement[] = here.map((s) => this.card(s, presenter));
    if (!here.length) {
      const empty = document.createElement('p');
      empty.className = 'researcher-empty';
      empty.textContent = 'No AI researchers yet. Start one above, or ask the Assistant.';
      nodes.push(empty);
    }
    if (elsewhere.length) {
      const heading = document.createElement('p');
      heading.className = 'researcher-elsewhere';
      heading.textContent = 'Still working in other projects';
      heading.title = 'Started in another project, they go on after a switch; their work is filed in that project.';
      nodes.push(heading);
      for (const s of elsewhere) {
        const card = this.card(s, presenter);
        card.dataset.elsewhere = '';
        nodes.push(card);
      }
    }
    this.root.replaceChildren(...nodes);
    this.refocus(focused);
    const working = this.sessions.some(isWorking);
    if (working && this.timer === null) this.timer = setInterval(() => this.tick(), 1000);
    else if (!working && this.timer !== null) { clearInterval(this.timer); this.timer = null; }
  }

  /** How many cards there are: what the tab's count says. */
  count(): number {
    return this.sessions.length;
  }

  private tick(): void {
    const now = Date.now() / 1000;
    for (const s of this.sessions) {
      const timing = this.timing.get(s.rid);
      if (timing) timing.textContent = researcherTiming(s, now);
    }
  }

  private focusedKey(): string | null {
    const active = document.activeElement as HTMLElement | null;
    if (!active || !this.root.contains?.(active)) return null;
    const card = active.closest?.<HTMLElement>('[data-rid]');
    return card ? `${card.dataset.rid}|${active.className}` : null;
  }

  private refocus(key: string | null): void {
    if (!key) return;
    const [rid, className] = key.split('|');
    const card = [...this.root.children].find((c) => (c as HTMLElement).dataset.rid === rid) as HTMLElement | undefined;
    const target = className ? card?.querySelector<HTMLElement>(`.${className.split(' ')[0]}`) : card;
    target?.focus?.({ preventScroll: true });
  }

  private card(s: ResearchSession, presenter: PresenterState | null): HTMLElement {
    const wrap = document.createElement('div');
    wrap.className = 'researcher-card';
    wrap.dataset.rid = s.rid;
    wrap.dataset.status = s.status;
    const working = isWorking(s);
    const ready = isReady(s);
    const question = asking(s);
    if (question) wrap.dataset.asking = '';
    if (this.isNew(s)) wrap.dataset.fresh = '';
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'researcher-card-main';
    button.setAttribute('aria-pressed', String(!!presenter?.open && presenter.rid === s.rid));
    const thumb = document.createElement('span');
    thumb.className = 'researcher-thumb';
    thumb.style.backgroundImage = `url("${pictureOf(s)}")`;
    // Its lit cell on the picture's corner (lit-cell.ts), the same as its Tab, its pill and its row in Researchers.
    thumb.append(litCell(researcherCell(s), { size: 12, label: `${s.name}: ${researcherWords(s)}` }));
    const text = document.createElement('span');
    text.className = 'researcher-text';
    const title = document.createElement('span');
    title.className = 'researcher-title';
    title.textContent = s.result?.title || s.label || s.brief.slice(0, 40);
    const status = document.createElement('span');
    status.className = 'researcher-status';
    const holds = !working && ready ? holdsText(s) : '';
    status.textContent = chipStatus(s) + (holds ? ` · ${holds}` : '');
    if (!working && s.presentationJobs?.some((job) => ['queued', 'preparing'].includes(job.state))) {
      status.textContent += ' · Presentation preparing';
    }
    const timing = document.createElement('span');
    timing.className = 'researcher-timing';
    timing.textContent = researcherTiming(s);
    this.timing.set(s.rid, timing);
    text.append(title, status, timing);
    button.append(thumb, text);
    button.title = ready && !working ? 'Open its work beside the view' : working ? 'See what it is doing' : 'See what happened';
    button.addEventListener('click', () => {
      if (ready && !working) this.open(s.rid);
      else location.hash = `#research/${s.rid}`;
    });
    wrap.append(button);
    const extra = document.createElement('div');
    extra.className = 'researcher-more';
    if (question && this.send) extra.append(evidenceActions(s, question, this.send));
    const links = document.createElement('span');
    links.className = 'researcher-links';
    const full = document.createElement('a');
    full.className = 'researcher-full';
    full.textContent = 'Open';full.title = 'Open this researcher’s Tab';
    const selected = presenter?.rid === s.rid ? presenter : null;
    const query = new URLSearchParams();
    if (s.day) query.set('day', s.day);
    if (selected?.outputId) query.set('output', selected.outputId);
    else if (selected?.findingRevision) query.set('findings', String(selected.findingRevision.version));
    full.href = `#research/${s.rid}${query.size ? `?${query}` : ''}`;
    links.append(full);
    if (this.activity) {
      const activity = document.createElement('button');
      activity.type = 'button'; activity.className = 'researcher-activity';
      activity.textContent = 'Activity'; activity.setAttribute('aria-label', `Open Activity for ${s.name}`);
      activity.addEventListener('click', () => this.activity?.(s.sitting || s.day));
      links.append(activity);
    }
    if (working && this.send) {
      const stop = document.createElement('button');
      stop.type = 'button';
      stop.className = 'mm-control researcher-stop';
      const words = stopButtonWords(s);
      stop.textContent = words.text; stop.disabled = words.disabled; stop.title = words.title;
      stop.setAttribute('aria-label', words.disabled ? `Stop requested for ${s.name}` : `Stop ${s.name}`);
      stop.addEventListener('click', () => { if (!this.send) return; requestStop(s.rid, this.send); const now = stopButtonWords(s); stop.textContent = now.text; stop.disabled = now.disabled; stop.title = now.title; });
      links.append(stop);
    }
    extra.append(links);
    wrap.append(extra);
    return wrap;
  }
}

/** Wall time is labelled as such; no worker timestamp means the last update is unknown. */
export function researcherTiming(s: ResearchSession, now = Date.now() / 1000): string {
  const elapsed = s.started == null ? 'Not started' : `${duration(Math.max(0, (s.finished ?? now) - s.started) * 1000)} elapsed`;
  const last = s.stepAt == null ? 'Last update unknown' : `${now - s.stepAt > 30 ? 'No recent update · ' : ''}${duration(Math.max(0, now - s.stepAt) * 1000)} since last step`;
  return [s.engine || 'Model unknown', elapsed, ...(isWorking(s) ? [last] : [])].join(' · ');
}
