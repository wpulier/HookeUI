import { bindArtifactEscape } from './artifact-keyboard';
import { researchCaptureImageUrl } from './research-captures';
import { clean, renderResearchReport } from './research-report';
import { renderResearchPresentation, renderArtifactContext } from './presentation';
import { ResearchWorkspace, researchIcon } from './research-workspace';
import { readerTitle } from './data-reader';
import { anchorWords, engineWords, itemValue, pageItem, researchFiles, researchPill, researchPlaceholder, researchQuestion, researchSteps, researchUsed, researchVersions, willMake, type ResearchStepMark } from './research-tab';
import { litCell, type LitCellState } from './lit-cell';
import type { MenuItem } from './drawer-menu';
import { renderResearchChart } from './research-chart';
import { ResearchOutbox } from './research-outbox';
import { evidenceActions } from './research-evidence';
import { holdsText, retainedFigure, artifactReceipt, presentationTiming, spendingSummary, isLesson, isReportPresentation } from './research-summary';
import './research-workspace.css';
// The Research tab: every researcher the brain started, what it is doing, and what it found.
// Researchers run in the background (orchestrator/research); the live view never waits for
// them. A card opens the session: its steps, pictures, sources and result. Quick lookups
// (the descriptions under labels) are listed separately, folded away.

import { hostOf } from './notes';
import type { ClientCommand, ResearchCaptureTarget, ResearchOutput, ResearchSession, ResearchTarget, WorkingRecord } from './protocol';

import { ACTIVE, MISSION_ACTIVE, isWorking } from './research-state';
import { stopRequested } from './research-stop';
export { isWorking };
const PRESENTATION_STATUS = {queued:'Presentation queued', preparing:'Preparing presentation', ready:'Presentation ready', conflict:'Competing presentation retained', failed:'Presentation not made', unavailable:'Presentation provider unavailable', over_budget:'Presentation spending limit reached', cancelled:'Presentation stopped', interrupted:'Presentation interrupted'};
/** A researcher working now: its own run, or a mission still watching (also the workspace rail's count). */
const STATUS: Record<string, string> = {
  queued: 'Waiting for a free slot',
  starting: 'Starting',
  running: 'Working',
  revising: 'Making a new version',
  done: 'Done',
  partial: 'Partial work kept',
  failed: 'Could not finish',
  timeout: 'Stopped answering',
  over_budget: 'Reached its spending limit',
  cancelled: 'Stopped',
  interrupted: 'Interrupted',
};

interface Step {
  at: number;
  kind: string;
  text: string;
}

export class ResearchView {
  readonly sessions = new Map<string, ResearchSession>();
  /** An earlier day's research being looked at (read-only: a link to one exact piece of work, `?day=`), or null for
   * the session open now. The header no longer picks days: sessions are switched instead. */
  day: string | null = null;
  private past = new Map<string, ResearchSession>();
  spentToday = 0;
  /** The part of spentToday that is estimated, of every researcher today (the server's ledger); null from an older
   * server, which leaves it to the researchers listed here. */
  estimatedToday: number | null = null;
  sitting = ''; // the session open now (its sid, opaque): its researchers are listed
  private openRid: string | null = null;
  private steps: Step[] = [];
  private stepsFor = '';
  private stepsTimer = 0;
  private presentationClock = 0;
  private readonly el = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;
  private readonly list = this.el<HTMLOListElement>('research-list');
  private readonly quick = this.el<HTMLDetailsElement>('research-quick');
  private readonly quickList = this.el<HTMLOListElement>('research-quick-list');
  private readonly quickCount = this.el('research-quick-count');
  private readonly empty = this.el('research-empty');
  private readonly session = this.el('research-session');
  private readonly spend = this.el('research-spend');
  private readonly send: (command: ClientCommand) => unknown;
  private readonly workspace: ResearchWorkspace;
  private readonly referenceFailures = new Map<string,string>();
  private readonly selectedArtifacts = new Map<string, string>();
  private readonly retainedFindings = new Map<string, ResearchSession['result']>();
  private readonly findingErrors = new Map<string, string>();
  private readonly findingLoads = new Set<string>();
  private readonly targets = new Map<string, ResearchTarget>();
  private readonly visibleTargets = new Map<string, ResearchTarget>();
  private readonly presentationRequests = new Map<string, string>();
  private readonly outbox = new ResearchOutbox();
  /** The words a selection was attached with ("Their areas", "areas.csv"), for its chip; the target itself is the record. */
  private readonly targetLabels = new Map<string, string>();
  onShowInMagnifier: (url: string, label: string, credit: string, objectId: string | null) => void = () => {};
  /** A tool's one name by its catalog key, and a model's by its id (main.ts, from the capabilities it has): Used and the
   * composer's engine chip name them as the catalog does. */
  toolName: (key: string) => string | null = () => null;
  modelName: (id: string) => string | null = () => null;
  /** A tool's run page (Used's tool rows), and a request to the Assistant in the person's words (Continue). */
  onOpenTool: (key: string) => void = () => {};
  onAsk: (text: string) => void = () => {};

  constructor(send: (command: ClientCommand) => unknown) {
    this.send = send;
    const sidebar = document.createElement('aside');
    sidebar.className = 'research-sidebar';
    sidebar.setAttribute('aria-label', 'Researchers');
    const root = this.el('research');
    const popout = this.el('research-popout');
    popout.classList.add('mm-control');
    const head = root.querySelector('.research-head');
    if (head) sidebar.append(head);
    const back = document.createElement('a');
    back.href = '#research'; back.textContent = '← All researchers'; back.className = 'research-back';
    sidebar.append(back);
    sidebar.append(this.empty, this.list, this.quick);
    root.prepend(sidebar);
    this.workspace = new ResearchWorkspace(this.session, (text) => {
      const s = this.openRid ? this.sessions.get(this.openRid) : undefined;
      if (!s || this.day) return;
      const identity = `${s.day ?? this.day ?? s.snapshot.split('/')[2]}/${s.rid}`;
      const day = identity.split('/')[0];
      const command = this.outbox.admit({type:isWorking(s) ? 'research-note' : 'research-followup', rid:s.rid, text,
        expectedBase:s.result?.findingRevision?.version ?? null,
        selection:this.targets.get(identity) ?? this.visibleTargets.get(identity) ?? {day,rid:s.rid}});
      this.send(command);
    });
    // Today's research spending (said at the foot of How it was made) and Open in a new window (in its page's ⋯); its
    // Stop is the Stage bar's, never a second one on the page.
    this.workspace.foot(this.spend, popout);
    this.el<HTMLButtonElement>('research-popout').addEventListener('click', () => {
      window.open(`${location.pathname}#research${this.openRid ? `/${this.openRid}` : ''}`, 'micromind-research', 'width=900,height=900');
    });
  }

  apply(message: { session?: ResearchSession; sessions?: ResearchSession[]; spentToday?: number; estimatedToday?: number }): void {
    if (message.sessions) {
      this.sessions.clear();
      for (const s of message.sessions) this.sessions.set(s.rid, s);
    }
    if (message.session) this.sessions.set(message.session.rid, message.session);
    for (const session of message.sessions ?? (message.session ? [message.session] : [])) {
      this.outbox.acknowledge(session.day ?? session.snapshot.split('/')[2], session.rid, (session.interactionJobs ?? []).map(job => job.id));
    }
    if (typeof message.spentToday === 'number') this.spentToday = message.spentToday;
    if (typeof message.estimatedToday === 'number') this.estimatedToday = message.estimatedToday;
    this.render();
  }

  /** Show the list (rid null) or one session. */
  show(rid: string | null): void {
    this.openRid = rid;
    window.clearInterval(this.presentationClock);
    if (rid) this.presentationClock = window.setInterval(() => {
      const source = this.day ? this.past : this.sessions;
      for (const node of this.session.querySelectorAll<HTMLElement>('[data-presentation-clock]')) {
        const job = source.get(rid)?.presentationJobs?.find(j => j.id === node.dataset.presentationClock);
        if (job && ['queued', 'preparing'].includes(job.state)) node.textContent = presentationTiming(job);
      }
    }, 1000);
    this.workspace.setVisible(rid !== null);
    this.render();
    if (rid) this.loadSteps(rid);
  }

  hide(): void { window.clearInterval(this.presentationClock); this.workspace.setVisible(false); }

  async openReference(day: string, rid: string, output?: string | null, finding?: string | null, digest?: string | null, outputVersion?: string | null): Promise<void> {
    const route = location.hash;
    // A researcher the server lists now is live, whether the link names its calendar day or its Project (a sid, kept
    // opaque: only compared with what the session itself says). Its work is keyed by its calendar day either way.
    const live = this.sessions.get(rid);
    const liveDay = live?.day ?? live?.snapshot?.split('/')[2];
    const own = !!live && (day === liveDay || day === live.sitting);
    const calendar = own ? liveDay ?? day : day;
    const targetDay = own ? null : calendar;
    if (output || finding) this.selectedArtifacts.set(`${calendar}/${rid}`, output ? `output:${output}` : `findings:${finding}`);
    if (this.day !== targetDay) await this.setDay(targetDay);
    if (location.hash !== route) return; // a late read never reopens a workspace the user left
    const session = (targetDay ? this.past : this.sessions).get(rid);
    const retainedOutput = output ? session?.outputs.find(row => row.id === output) : undefined;
    const retainedBasis = output ? retainedOutput?.findingRevision
      : session?.findingRevisions?.find(row => row.version === Number(finding)) ?? session?.result?.findingRevision;
    const identity = `${calendar}/${rid}`;
    if (output && !retainedOutput || digest && retainedBasis?.digest !== digest || outputVersion && (retainedOutput?.version ?? 1) !== Number(outputVersion)) {
      this.referenceFailures.set(identity,'The exact selected work is unavailable or its saved identity differs. No newer work was substituted.');
    } else this.referenceFailures.delete(identity);
    this.show(rid);
  }

  private research(): ResearchSession[] {
    const from = this.day ? this.past : this.sessions;
    return [...from.values()].filter((s) => s.purpose === 'research').sort((a, b) => b.created - a.created);
  }

  /** Look at an earlier day's session (null: today). Its work is read from disk. */
  async setDay(day: string | null): Promise<void> {
    this.day = day;
    this.past.clear();
    if (day) {
      try {
        const data = (await (await fetch(`/api/research-day/${encodeURIComponent(day)}`)).json()) as { sessions: ResearchSession[] };
        if (this.day !== day) return;
        for (const s of data.sessions) this.past.set(s.rid, s);
      } catch {
        // leave it empty; the list says so
      }
    }
    this.render();
  }

  render(): void {
    const research = this.research();
    const quick = [...this.sessions.values()].filter((s) => s.purpose === 'description').sort((a, b) => b.created - a.created);
    // Research spending as recorded; estimates (calls cut off, presentations dispatched with no receipt yet) said apart,
    // as spendingSummary says them, so a refusal that was never billed is never read as spent. Today's are the server's,
    // of every researcher today (the same ledger as spentToday): summing only the ones listed here read another
    // Project's estimates as spent (live 2026-10-25: "Research today: $1.20" beside Spending's $0.40 of research).
    const listed = (this.day ? research : [...this.sessions.values()].filter((s) => s.purpose === 'research'))
      .reduce((sum, s) => sum + (s.spentEstimated ?? 0) + (s.presentationJobs ?? []).reduce((jobs, j) => jobs + (j.estimatedCost ?? 0), 0), 0);
    const estimated = !this.day && this.estimatedToday !== null ? this.estimatedToday : listed;
    const total = this.day ? research.reduce((sum, s) => sum + s.spent + (s.presentationJobs ?? []).reduce((jobs, j) => jobs + (j.cost ?? 0), 0), 0) + estimated : this.spentToday;
    const recorded = Math.max(0, total - estimated);
    this.spend.textContent = `${this.day ? 'Research that day' : 'Research today'}: $${recorded.toFixed(2)}${estimated > 0.004 ? ` · up to $${estimated.toFixed(2)} more, receipts pending` : ''}`;
    this.empty.hidden = research.length > 0 || this.openRid !== null;
    this.el('research').dataset.open = String(this.openRid !== null);
    this.list.hidden = false;
    this.quick.hidden = !quick.length;
    // This session's researchers; of other sessions', only those still working (they go on after a switch, and file
    // their work where they began). The rest are that session's: switch to it to see them.
    const now = this.day ? research : research.filter((s) => !this.sitting || s.sitting === this.sitting);
    const earlier = this.day || !this.sitting ? [] : research.filter((s) => !!s.sitting && s.sitting !== this.sitting && isWorking(s));
    const heading = (text: string) => {
      const li = document.createElement('li');
      li.className = 'research-group';
      li.textContent = text;
      return li;
    };
    this.list.replaceChildren(
      ...(earlier.length && now.length ? [heading('This project')] : []),
      ...now.map((s) => this.card(s)),
      ...(earlier.length ? [heading('Still working in other projects')] : []),
      ...earlier.map((s) => this.card(s)),
    );
    this.quickCount.textContent = String(quick.length);
    this.quickList.replaceChildren(...quick.slice(0, 40).map((s) => this.quickItem(s)));
    this.session.hidden = this.openRid === null;
    if (this.openRid !== null) this.renderSession((this.day ? this.past : this.sessions).get(this.openRid) ?? null);
  }

  private card(s: ResearchSession): HTMLLIElement {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.className = 'research-card';
    a.href = `#research/${s.rid}`;
    a.dataset.status = s.status;
    if (s.rid === this.openRid) a.setAttribute('aria-current', 'page');
    const thumb = document.createElement('img');
    thumb.src = s.snapshot;
    thumb.alt = '';
    thumb.loading = 'lazy';
    const body = document.createElement('div');
    body.className = 'research-card-body';
    const name = document.createElement('h3');
    name.textContent = clean(s.result?.title) || s.name + (s.label ? `: ${s.label}` : '');
    const brief = document.createElement('p');
    brief.className = 'research-brief';
    const oneLiner = (s.status === 'done' || s.status === 'partial') && s.result?.one_liner;
    const original = clean(s.history.find(entry => !entry.mode)?.brief ?? s.brief);
    brief.textContent = oneLiner ? clean(oneLiner) : `Asked: “${original}”`;
    body.append(name, brief);
    if (oneLiner) {
      const asked = paragraph(`Asked: “${original}”`); asked.className = 'research-asked'; body.append(asked);
    }
    const figure = retainedFigure(s.result);
    if (figure) { const key = paragraph(figure); key.className = 'research-key'; body.append(key); }
    const cardStatus = statusLine(s);
    if (s.name) cardStatus.append(document.createTextNode(`, ${s.name}`));
    body.append(cardStatus);
    const holds = [holdsText(s), s.day].filter(Boolean).join(' · ');
    if (holds) { const retained = paragraph(holds); retained.className = 'research-holds'; body.append(retained); }
    body.append(costLine(s, true));
    const extras = [s.outputs.some((o) => o.kind === 'video') ? 'video' : '', s.outputs.some(isLesson) ? 'lesson' : '']
      .filter(Boolean);
    if (extras.length) {
      const chips = document.createElement('p');
      chips.className = 'research-made';
      chips.textContent = `Made: ${extras.join(' and ')}`;
      body.append(chips);
    }
    if (s.outputs.length) {
      const strip = document.createElement('div');
      strip.className = 'research-thumbs';
      for (const o of s.outputs.filter((o) => o.kind === 'image').slice(0, 4)) {
        const img = document.createElement('img');
        img.src = o.url;
        img.alt = o.caption;
        img.loading = 'lazy';
        strip.append(img);
      }
      body.append(strip);
    }
    a.append(thumb, body);
    li.append(a);
    return li;
  }

  private quickItem(s: ResearchSession): HTMLLIElement {
    const li = document.createElement('li');
    const a = document.createElement('a');
    a.href = `#research/${s.rid}`;
    a.textContent = `${s.label || s.brief.slice(0, 40)}: ${STATUS[s.status] ?? s.status}`;
    li.append(a);
    return li;
  }

  private loadSteps(rid: string): void {
    window.clearTimeout(this.stepsTimer);
    const day = this.day;
    void fetch(`/api/research/${encodeURIComponent(rid)}${day ? `?day=${encodeURIComponent(day)}` : ''}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data: (ResearchSession & { steps: Step[] }) | null) => {
        if (!data || this.openRid !== rid || this.day !== day) return;
        (this.day ? this.past : this.sessions).set(rid, data);
        this.steps = data.steps;
        this.stepsFor = rid;
        this.render();
        if (isWorking(data)) this.stepsTimer = window.setTimeout(() => this.loadSteps(rid), 2000);
      })
      .catch(() => {});
  }

  /** One researcher as its Tab shows it (research-workspace.ts): its conversation (the brief, its steps as compact rows,
   * its questions with their answers, your notes and follow-ups, how it ended), its files and what it used, and the
   * artifact it writes, the real output on its page. Every guard of the saved-identity reads stays as it was. */
  private renderSession(s: ResearchSession | null): void {
    if (!s) {
      const p = paragraph('This researcher is not in this project any more.'); p.className = 'm-note';
      this.workspace.update({ key: `${this.day ?? 'today'}/${this.openRid}`, title: 'Researcher unavailable', working: false, writable: false, canSend: false });
      this.workspace.renderFiles([], []);
      this.workspace.renderThread('missing', [p]);
      this.workspace.renderArtifact('missing', [], { title: 'Researcher unavailable', empty: 'Nothing to show' });
      return;
    }
    const r = s.result ?? s.history.findLast(entry => !entry.mode && entry.result)?.result ?? null;
    const day = s.day ?? this.day ?? s.snapshot.split('/')[2];
    const identity = `${day}/${s.rid}`;
    const referenceFailure = this.referenceFailures.get(identity);
    if (referenceFailure) {
      this.workspace.update({key:identity,rid:s.rid,title:'Selected work unavailable',working:false,writable:false,canSend:false});
      const available = document.createElement('button'); available.type='button'; available.className='rtab-button'; available.textContent='Show available research';
      available.addEventListener('click',()=>{this.referenceFailures.delete(identity);this.selectedArtifacts.delete(identity);this.render();});
      const failure = paragraph(referenceFailure); failure.className = 'm-note';
      this.workspace.renderFiles([], []);
      this.workspace.renderThread(referenceFailure,[failure,available]);
      this.workspace.renderArtifact(referenceFailure,[],{title:'Selected work unavailable',empty:referenceFailure});
      return;
    }
    if (!this.targets.has(identity)) {
      try {
        const target = JSON.parse(sessionStorage.getItem(`research-target:${identity}`) ?? 'null') as ResearchTarget | null;
        if (target?.day === day && target.rid === s.rid) this.targets.set(identity, target);
      } catch { /* Optional reading preference; server validates every submitted target. */ }
    }
    if (!this.selectedArtifacts.has(identity)) {
      try {
        const saved = sessionStorage.getItem(`research-artifact:${identity}`);
        if (saved) this.selectedArtifacts.set(identity, saved);
      } catch { /* optional local reading preference */ }
    }
    let selected = this.selectedArtifacts.get(identity);
    if (selected === 'notes' && (r || !s.findings.length)) { this.selectedArtifacts.delete(identity); selected = undefined; } // its findings replace its notes
    const defaultOutput = defaultResearchOutput(s, r);
    const selectedOutput = selected?.startsWith('output:') ? s.outputs.find((o) => o.id === selected.slice(7))
      : !selected ? defaultOutput : undefined;
    const job = s.presentationJobs?.at(-1);
    const presentation = selectedOutput?.kind === 'presentation' ? selectedOutput : undefined;
    const selectedVersion = selected?.startsWith('findings:') ? Number(selected.split(':')[1]) : (presentation ?? selectedOutput)?.findingRevision?.version ?? null;
    const findingKey = `${identity}/${selectedVersion}`;
    if (selectedVersion && selectedVersion !== r?.findingRevision?.version && !this.retainedFindings.has(findingKey)
      && !this.findingErrors.has(findingKey) && !this.findingLoads.has(findingKey)) {
      this.findingLoads.add(findingKey);
      void fetch(`/api/research/${encodeURIComponent(s.rid)}/findings/${selectedVersion}?day=${encodeURIComponent(day)}`)
        .then(async (response) => {
          if (!response.ok) throw new Error('This saved finding could not be loaded. Select it again to retry.');
          const record = await response.json() as { version: number; digest: string; complete: boolean; result: NonNullable<ResearchSession['result']> };
          this.retainedFindings.set(findingKey, { ...record.result, findingRevision: { version: record.version, digest: record.digest, complete: record.complete } });
        }).catch((error: unknown) => this.findingErrors.set(findingKey, error instanceof Error ? error.message : 'Could not load findings'))
        .finally(() => { this.findingLoads.delete(findingKey); if (this.openRid === s.rid) this.render(); });
    }
    const paneFinding = selectedVersion && selectedVersion !== r?.findingRevision?.version ? this.retainedFindings.get(findingKey) : r;
    const visibleOutput = presentation ?? selectedOutput;
    const visibleBasis = visibleOutput?.findingRevision ?? paneFinding?.findingRevision;
    this.visibleTargets.set(identity, {day, rid:s.rid,
      presentationBase:s.outputs.findLast(output => output.current !== false && (visibleOutput && isLesson(visibleOutput) ? output.kind === 'html' && isLesson(output) : isReportPresentation(output)))?.id ?? null,
      ...(visibleBasis ? {findingVersion:visibleBasis.version, findingDigest:visibleBasis.digest} : {}),
      ...(visibleOutput?.id ? {outputId:visibleOutput.id, outputVersion:visibleOutput.version ?? 1} : {})});
    const attached = this.targets.get(identity);
    this.workspace.attach(attached ? this.targetLabels.get(identity) ?? `${attached.anchor ?? attached.outputId ?? 'Findings'} · findings v${attached.findingVersion ?? '?'}` : null, () => {
      this.targets.delete(identity); this.targetLabels.delete(identity);
      try { sessionStorage.removeItem(`research-target:${identity}`); } catch { /* optional */ }
      this.render();
    });
    const live = !this.day && this.sessions.has(s.rid); // earlier days are read-only even if their rid is reused today
    const working = isWorking(s);
    // A file or a section it made, attached to the next message (the server checks it against the exact saved work).
    const attach = (target: ResearchTarget, label: string) => {
      this.targets.set(identity, target); this.targetLabels.set(identity, label);
      try { sessionStorage.setItem(`research-target:${identity}`, JSON.stringify(target)); } catch { /* optional */ }
      this.render();
    };
    const selectAnchor = (anchor: string, label?: string) => {
      const revision = paneFinding?.findingRevision;
      if (!revision) return;
      attach({ ...this.visibleTargets.get(identity), day, rid: s.rid, findingVersion: revision.version, findingDigest: revision.digest, anchor,
        ...((presentation ?? selectedOutput)?.id ? {outputId: (presentation ?? selectedOutput)!.id, outputVersion: (presentation ?? selectedOutput)!.version ?? 1} : {}) },
      label ?? anchorWords(anchor, paneFinding));
    };
    const showOnRight = (value: string) => {
      if (value) this.selectedArtifacts.set(identity, value); else this.selectedArtifacts.delete(identity);
      this.findingErrors.delete(`${identity}/${value.split(':')[1]}`);
      try { if (value) sessionStorage.setItem(`research-artifact:${identity}`, value); else sessionStorage.removeItem(`research-artifact:${identity}`); } catch { /* optional reading preference */ }
      this.workspace.showPresentation();
      this.render();
    };
    // "Show beside the live view": the same present/presenter routes as before. They open the work in a window beside
    // the live view on the work Tab this page returns to, and go there (Add to Tab… on a researcher's Data entry opens
    // this page again, so it is not a route to a Tab of its own).
    const showBeside = () => {
      if (presentation || !selectedOutput) {
        this.send({ type: 'present', do: 'open', source: {day, id: s.rid, picture: null,
          version: presentation?.version ?? paneFinding?.findingRevision?.version ?? null,
          ...(presentation?.id ? {outputId: presentation.id} : {})}, title: clean(paneFinding?.title || s.brief),
          requestId: crypto.randomUUID(), actionId: crypto.randomUUID() });
      } else {
        const formats: Record<string,string> = {lesson:'html', video:'video', image:'image', annotated_image:'image'};
        const output = selectedOutput ?? s.outputs.findLast(o => o.current !== false &&
          o.kind === (formats[s.deliverable ?? ''] ?? 'html'))
          ?? s.outputs.find(o => o.current !== false && ['html','video','image'].includes(o.kind));
        if (output?.id) this.send({type:'presenter', action:'show', rid:s.rid, day, outputId:output.id, version:output.version ?? 1});
      }
      location.hash = '';
    };
    const files = researchFiles(s, r, day);
    this.workspace.bind({
      show: showOnRight,
      // A Data entry its page draws, chosen in the reader's switcher (its report, a lesson, a video, a picture).
      showItem: (id) => { const value = itemValue(s, id, defaultOutput); if (value !== null) showOnRight(value); },
      tool: (key) => this.onOpenTool(key),
      main: () => { location.hash = ''; },
      attachFile: (file) => {
        if (!('show' in file.open)) {
          if ('tab' in file.open) selectAnchor(`${file.open.tab}:${file.open.index}`, file.name);
          return;
        }
        const value = file.open.show;
        if (value.startsWith('output:')) {
          const output = s.outputs.find((o) => o.id === value.slice(7));
          if (!output?.id) return;
          attach({ day, rid: s.rid, outputId: output.id, outputVersion: output.version ?? 1,
            ...(output.findingRevision ? { findingVersion: output.findingRevision.version, findingDigest: output.findingRevision.digest } : {}) }, file.name);
        } else {
          const version = Number(value.split(':')[1]);
          const revision = (r?.findingRevision?.version === version ? r.findingRevision : s.findingRevisions?.find((row) => row.version === version));
          if (revision) attach({ day, rid: s.rid, findingVersion: revision.version, findingDigest: revision.digest }, file.name);
          else attach({ day, rid: s.rid }, file.name);
        }
      },
      attachSection: (anchor, title) => selectAnchor(anchor, title),
      newResearcher: () => window.dispatchEvent(new CustomEvent('hooke-research-new')),
    });

    // --- The conversation, in time order ---
    const parts: HTMLElement[] = [];
    const at = (element: HTMLElement, id: string, time: number) => { element.dataset.messageId = id; element.dataset.at = String(time); parts.push(element); return element; };
    const brief = document.createElement('div'); brief.className = 'm-brief research-request';
    brief.append(Object.assign(document.createElement('small'), { textContent: 'Brief from the Assistant' }), paragraph(clean(s.history.find(entry => !entry.mode)?.brief ?? s.brief)));
    at(brief, 'original-request', s.created);
    const t0 = s.started ?? s.created;
    for (const row of researchSteps(this.stepsFor === s.rid ? this.steps : [], s, this.toolName)) {
      // What a run made opens in the reader beside the conversation (its outlines and table; Show on Main ↗ from its ⋯).
      // Named as Data and the reader name it ("Bright regions"; its maker and time are said where it opens).
      const chips = row.made.map((made) => chip(made.kind === 'population' ? 'mask' : 'record', readerTitle(made.title), () => this.workspace.openFileTab(`record:${made.id}`),
        `Open ${made.title} here`));
      const step = at(stepRow(row.mark, row.title, row.sub, chips, row.key), row.key, row.at === Number.MAX_SAFE_INTEGER ? Number.MAX_SAFE_INTEGER : row.at);
      if (row.detail) step.title = row.detail;
    }
    if (s.plan?.searches?.length && ACTIVE.has(s.status)) {
      const plan = said(`Plan: ${s.plan.searches.map(clean).join('; ')}${s.plan.sources ? ` (sources: ${clean(s.plan.sources)})` : ''}. `
        + 'Send it a note to skip some sources or use only some.');
      at(plan, 'plan', t0 + 0.01);
    }
    for (const q of s.requests ?? []) {
      // What it asked the person, in the server's words: present tense with its answer buttons while it waits, past
      // tense once answered (the answer as the person's words, unless their own message above says it already) or once
      // its run ended without one.
      const question = researchQuestion(q, s, !!r?.limitations?.length);
      const message = document.createElement('div'); message.className = 'm-q';
      message.dataset.question = question.waiting ? 'asked' : q.status;
      message.append(paragraph(question.words));
      if (!this.day && question.waiting) message.append(evidenceActions(s, q, this.send));
      at(message, `evidence:${q.id}`, q.at ?? s.created + .02);
      if (question.answer) {
        const you = document.createElement('div'); you.className = 'm-you';
        const bubble = document.createElement('div'); bubble.className = 'm-user'; bubble.textContent = question.answer;
        bubble.title = `Answered ${new Date(question.at * 1000).toLocaleString()}`;
        you.append(bubble);
        at(you, `answer:${q.id}`, question.at);
      }
    }
    for (const earlier of s.history.filter((entry) => !entry.mode)) {
      if (earlier.result === r) continue;
      const version = earlier.result?.findingRevision?.version;
      const time = earlier.at ?? s.findingRevisions?.find(row => row.digest === earlier.result?.findingRevision?.digest)?.at ?? s.created + .01;
      const chips = version ? [chip('report', `Findings v${version}`, () => showOnRight(`findings:${version}`), 'Show these findings on the right', `findings:${version}`)] : [];
      const row = stepRow('done', 'Earlier findings', clean(earlier.result?.one_liner || earlier.result?.summary), chips, `earlier:${version ?? time}`);
      if (!version && earlier.result) {
        const full = document.createElement('details'); full.className = 'rtab-details';
        full.append(Object.assign(document.createElement('summary'), { textContent: 'Read them' }),
          renderResearchReport(earlier.result, undefined, this.captureOpener(s, earlier.result), this.capturePreview(s, earlier.result)));
        row.querySelector('.step-words')?.append(full);
      }
      at(row, `finding:${earlier.result?.findingRevision?.digest ?? s.history.indexOf(earlier)}`, time);
    }
    for (const earlier of s.history.filter((entry) => !!entry.mode)) {
      const message = said(earlier.mode === 'revise'
        ? `Changed its ${earlier.kind ?? 'work'}${earlier.version ? ` (v${earlier.version})` : ` (${STATUS[earlier.status] ?? earlier.status})`}: ${clean(earlier.change)}`
        : `${clean(earlier.brief)}${earlier.result?.summary ? ` ${clean(earlier.result.summary)}` : ''}`,
      earlier.mode === 'revise' ? 'New version of its work' : 'Follow-up · findings unchanged');
      if (earlier.result) {
        const full = document.createElement('details'); full.className = 'rtab-details';
        full.append(Object.assign(document.createElement('summary'), { textContent: 'Its answer in full' }),
          renderResearchReport(earlier.result, undefined, this.captureOpener(s, earlier.result), this.capturePreview(s, earlier.result)));
        message.append(full);
      }
      at(message, `history:${earlier.at ?? s.history.indexOf(earlier)}`, earlier.at ?? Number.MAX_SAFE_INTEGER - 2);
    }
    const noteSteps = (this.stepsFor === s.rid ? this.steps : []).filter((step) => step.kind === 'note');
    for (const job of s.interactionJobs ?? []) {
      const you = document.createElement('div'); you.className = 'm-you';
      const bubble = document.createElement('div'); bubble.className = 'm-user'; bubble.textContent = job.request.text;
      bubble.title = `Sent ${new Date(job.created * 1000).toLocaleString()}`;
      you.append(bubble);
      const note = job.request.kind === 'note';
      if (job.outcome?.error) you.append(Object.assign(document.createElement('small'), { className: 'm-cap m-cap-problem', textContent: job.outcome.error }));
      else if (note && working && !noteSteps.some((step) => step.at >= job.created)) you.append(Object.assign(document.createElement('small'), { className: 'm-cap', textContent: 'It reads this at its next step' }));
      at(you, `request:${job.id}`, job.created);
      if (job.outcome?.answer) at(said(job.outcome.answer), `answer:${job.id}`, job.created + 0.001);
      else if (!note && !job.outcome && ['admitted','dispatched'].includes(job.state)) {
        const slot = document.createElement('div'); slot.className = 'slot';
        slot.append(litCell('working', { size: 12, label: 'Working' }), 'Working on it');
        at(slot, `slot:${job.id}`, job.created + 0.001);
      }
    }
    for (const command of this.outbox.forResearcher(day, s.rid)) {
      const you = document.createElement('div'); you.className = 'm-you';
      you.append(Object.assign(document.createElement('div'), { className: 'm-user', textContent: command.text }),
        Object.assign(document.createElement('small'), { className: 'm-cap', textContent: 'Delivery not yet confirmed' }));
      const retry = document.createElement('button'); retry.type = 'button'; retry.className = 'rtab-button rtab-button-quiet'; retry.textContent = 'Retry delivery';
      retry.disabled = !live;
      retry.addEventListener('click', () => {
        try { this.send(command); }
        catch (error) { retry.textContent = error instanceof Error ? error.message : 'Connection unavailable'; }
      });
      you.append(retry);
      at(you, `request:${command.requestId}`, Number.MAX_SAFE_INTEGER - 1);
    }
    // How it ended, in its own words: its short answer, an offer to carry on partial or interrupted work, or why not.
    const ended = s.finished ?? s.findingRevisions?.find(row => row.digest === r?.findingRevision?.digest)?.at ?? (s.started ?? s.created) + .03;
    const carryOn = (label = 'Continue') => {
      const go = document.createElement('button'); go.type = 'button'; go.className = 'rtab-button'; go.textContent = label;
      go.title = `Asks the Assistant to carry on ${s.name}’s work at its own effort. It says whether it can.`;
      go.disabled = !live;
      go.addEventListener('click', () => this.onAsk(`Continue ${s.name}’s work.`));
      return go;
    };
    if (s.mission?.acquisition) at(Object.assign(statusLine(s), { className: 'm-note research-status' }), 'mission', Number.MAX_SAFE_INTEGER - 3);
    if (r && !working) {
      const answer = s.status === 'partial' ? document.createElement('div') : said(clean(r.one_liner || r.summary));
      if (s.status === 'partial') {
        answer.className = 'm-q';
        answer.append(paragraph(clean(r.one_liner || r.summary) || 'It kept partial findings.'));
        if (live) { const actions = document.createElement('div'); actions.className = 'm-actions'; actions.append(carryOn()); answer.append(actions); }
      }
      at(answer, `finding:${r.findingRevision?.digest ?? 'current'}`, ended);
    } else if (r && working && s.status === 'revising') at(said(clean(r.one_liner || r.summary), 'Its findings so far'), `finding:${r.findingRevision?.digest ?? 'current'}`, ended);
    // What it kept, from its record: its findings when it saved them, and its record's own counts.
    const kept = [r ? 'its findings' : '', holdsText(s)].filter(Boolean).join(', ');
    // Without findings, what it kept is its notes: the chip shows them on the right.
    const keptChip = () => !r && s.findings.length ? [chip('report', `Kept ${kept}`, () => showOnRight('notes'), 'Show what it noted on the right', 'notes')] : [];
    const keptWords = keptChip().length ? '' : kept ? `Kept ${kept}` : '';
    if (s.status === 'cancelled') at(stepRow('stopped', 'Stopped by you', keptWords || (kept ? '' : 'Nothing was kept'), keptChip(), 'stopped'), 'stopped', ended + 0.001);
    if (s.status === 'interrupted' && !s.mission?.acquisition) at(stepRow('stopped', 'Interrupted when Hooke closed', keptWords, [...keptChip(), ...(live ? [carryOn()] : [])], 'interrupted'), 'interrupted', ended + 0.001);
    if (['failed', 'timeout', 'over_budget'].includes(s.status)) {
      const details = document.createElement('button'); details.type = 'button'; details.className = 'rtab-button rtab-button-quiet'; details.textContent = 'Details';
      details.title = 'Opens Activity';
      details.addEventListener('click', () => window.dispatchEvent(new CustomEvent('hooke-research-activity', { detail: { rid: s.rid } })));
      at(stepRow('attention', STATUS[s.status] ?? 'Could not finish', clean(s.step), [details], 'failed'), 'failed', ended + 0.001);
    }
    if (!r && !working && s.status === 'done') at(said('It finished without a written result.'), 'no-result', ended);
    const refs = r?.refs ?? [];
    if (refs.length) {
      const gallery = document.createElement('div');
      gallery.className = 'research-gallery';
      for (const ref of refs) gallery.append(this.picture(ref.url, ref.title || 'Reference picture', ref.credit, s.objectId, false, false, ref.page));
      const message = said('', 'Reference pictures'); message.append(gallery);
      at(message, 'refs', ended + 0.002);
    }
    if (s.corrections.length) {
      const list = document.createElement('ul');
      list.className = 'research-corrections';
      for (const c of s.corrections) {
        const li = document.createElement('li');
        li.textContent = `${c.label ? `${c.label} → ` : ''}${c.to_label}: ${clean(c.reason)} `;
        if (c.status === 'proposed') {
          const accept = document.createElement('button');
          accept.type = 'button'; accept.className = 'rtab-button';
          accept.textContent = `Call it ${c.to_label}`;
          accept.addEventListener('click', () => this.send({ type: 'accept-correction', id: c.id }));
          const dismiss = document.createElement('button');
          dismiss.type = 'button'; dismiss.className = 'rtab-button rtab-button-quiet';
          dismiss.textContent = 'Dismiss';
          dismiss.addEventListener('click', () => this.send({ type: 'dismiss-correction', id: c.id }));
          li.append(accept, dismiss);
        } else {
          const state = document.createElement('em');
          state.textContent = c.status === 'accepted' ? '(accepted)' : c.status === 'expired' ? '(from an earlier project)' : '(dismissed)';
          li.append(state);
        }
        list.append(li);
      }
      const message = said('', 'Suggested corrections'); message.append(list);
      at(message, 'corrections', ended + 0.003);
    }
    parts.sort((a, b) => Number(a.dataset.at) - Number(b.dataset.at));
    const pill = researchPill(s);
    const asked = !this.day && working && (s.requests ?? []).some((q) => q.status === 'asked');
    const canSend = (working || s.canFollowUp) && (!selectedVersion || !!paneFinding);
    // The Data entry its page shows (its report, or a lesson, video or picture line), so the reader's switcher names it.
    const item = pageItem(s, visibleOutput, selected);
    const lessonTitle = visibleOutput?.content && 'kind' in visibleOutput.content ? clean((visibleOutput.content as { title?: string }).title) : '';
    const pageTitle = item.id !== s.rid ? lessonTitle || clean(visibleOutput?.caption).replace(/\s*\((?:written|made|drawn) by [^)]+\)\s*$/i, '') || clean(r?.title || s.label || s.brief)
      : clean(paneFinding?.title || r?.title || s.label || s.brief);
    this.workspace.update({ key: identity, rid: s.rid, title: pageTitle,
      status: `${statusLine(s).textContent}${s.name ? `, ${s.name}` : ''}`, statusCode: s.mission?.acquisition ? s.mission.status : s.status, researcher: s.name,
      output: selectedOutput?.id ? `output:${selectedOutput.id}` : (paneFinding?.findingRevision ? `findings:${paneFinding.findingRevision.version}` : null),
      working, stopRequested: stopRequested(s), writable: live, canSend, pill, waiting: asked,
      engine: engineWords(s.engine, this.modelName), placeholder: researchPlaceholder(s, { writable: live, canSend }),
      project: s.sitting || day, item: item.id, kind: item.kind });
    // What it made (the composer's + asks about it) and what it worked from (How it was made says it).
    this.workspace.renderFiles(files, researchUsed(s, day, this.toolName));
    this.workspace.suggest((paneFinding?.next_questions ?? []).map(clean).filter(Boolean));
    this.workspace.renderThread(JSON.stringify([s, this.outbox.forResearcher(day, s.rid), this.stepsFor === s.rid ? this.steps : [], live]), parts);

    // --- The artifact ---
    const { nodes: hero, frame } = researchHero(selectedOutput, paneFinding ?? null, {
      escape: () => this.workspace.closePresentation(),
      picture: (o, caption) => this.picture(o.url, caption, s.name, s.objectId, o.altered, o.generated) });
    const older = s.outputs.filter((o) => o.current === false && o.kind !== 'presentation');
    if (older.length && hero.length) {
      const p = smallNote('Earlier versions: ');
      older.forEach((o, i) => {
        if (i) p.append(', ');
        p.append(link(o.url, `${isLesson(o) ? 'lesson' : { html: 'interactive artifact', video: 'video', image: 'picture' }[o.kind] ?? 'result'} v${o.version ?? 1}`));
      });
      hero.push(p);
    }
    // What the producer is doing with a presentation, and the lesson's steps to ask about: one quiet line over the page.
    const stage = document.createElement('div'); stage.className = 'rtab-stage'; stage.setAttribute('role', 'status');
    if (selectedOutput?.id) {
      const lesson = selectedOutput.content;
      if (lesson && 'kind' in lesson && lesson.kind === 'lesson') {
        const steps = document.createElement('select'); steps.className = 'rtab-select';
        steps.setAttribute('aria-label', 'Attach a lesson step');
        const empty = document.createElement('option'); empty.textContent = 'Ask about a step…'; empty.value = ''; steps.append(empty);
        for (const step of lesson.steps) {
          const option = document.createElement('option'); option.value = `step:${step.id}`;
          option.textContent = `${step.id} · ${step.prompt}`; steps.append(option);
        }
        steps.addEventListener('change', () => { if (steps.value) selectAnchor(steps.value, steps.selectedOptions?.[0]?.textContent ?? steps.value); });
        stage.append(steps);
      }
    }
    if (job && (job.state !== 'ready' || job.persistenceError || job.pendingOutput)) {
      // One clamped line in words (the server says a refusal in fixed words, S9; its raw text is in Activity), with
      // Details opening Activity; the clock only while it is being made.
      const older = r?.findingRevision && job.findingRevision.digest !== r.findingRevision.digest ? ' · older findings' : '';
      // A job saved before 2026-10-25 says it twice ("…no credits left, so the presentation was not made."): said once.
      const error = job.error?.replace(/, so the presentation was not made\.$/, '.');
      const line = paragraph(error ? `${PRESENTATION_STATUS[job.state]}: ${error}` : `${PRESENTATION_STATUS[job.state]} · for findings v${job.findingRevision.version}${older}`);
      line.className = 'rtab-stage-line'; line.title = line.textContent ?? '';
      stage.append(line);
      if (job.error) {
        const details = document.createElement('button'); details.type = 'button'; details.className = 'rtab-button rtab-button-quiet'; details.textContent = 'Details';
        details.title = 'Opens Activity';
        details.addEventListener('click', () => window.dispatchEvent(new CustomEvent('hooke-research-activity', { detail: { rid: s.rid } })));
        stage.append(details);
      }
      if (job.persistenceError) stage.append(paragraph(`Not saved: ${job.persistenceError}`));
      if (['queued', 'preparing'].includes(job.state)) {
        const timing = paragraph(presentationTiming(job));
        timing.dataset.presentationClock = job.id;
        stage.append(timing);
      }
      if (live && (job.pendingOutput || job.persistenceError)) {
        const save = document.createElement('button'); save.type = 'button'; save.textContent = 'Try saving again';
        save.className = 'rtab-button';
        save.addEventListener('click', () => this.send({type:'research-presentation', action:'save', rid:s.rid, jobId:job.id}));
        stage.append(save);
      }
    }
    // ⋯: open it in its own Tab, How it was made, ask about this page, and make (again) or stop its presentation.
    const more: MenuItem[] = [];
    if (paneFinding || selectedOutput || s.outputs.length) more.push({ label: 'Show beside the live view', act: showBeside, title: 'Opens it in a window beside the live view on your work Tab, and goes there' });
    more.push({ label: 'How it was made', act: () => this.workspace.showHow(), title: 'Its basis, what it worked from, where it read, its effort and spending' });
    more.push({ label: 'Open in a new window', act: () => this.el<HTMLButtonElement>('research-popout').click(), title: 'This researcher in a window of its own' });
    if (live && (visibleOutput || visibleBasis)) more.push({ label: 'Ask about this page', act: () => {
      const target = this.visibleTargets.get(identity);
      if (target) attach(target, visibleOutput ? clean(visibleOutput.caption) || 'This page' : `Findings v${visibleBasis?.version ?? ''}`.trim());
    } });
    const lessonSelected = !!selectedOutput && isLesson(selectedOutput);
    if (live && paneFinding?.findingRevision && (paneFinding.report || lessonSelected)) {
      const busy = s.presentationJobs?.find((j) => ['queued','preparing'].includes(j.state));
      more.push('separator', { label: busy ? 'Stop making the presentation' : presentation || selectedOutput?.model ? 'Make the presentation again' : 'Make a presentation', act: () => {
        if (busy) this.send({type:'research-presentation', action:'cancel', rid:s.rid, jobId:busy.id});
        else {
          const key = `${identity}/${paneFinding.findingRevision!.digest}/${job?.id ?? 'first'}`;
          let requestId = this.presentationRequests.get(key);
          if (!requestId) { requestId = crypto.randomUUID(); this.presentationRequests.set(key, requestId); }
          this.send({type:'research-presentation', action:'produce', rid:s.rid, requestId,
            findingVersion:paneFinding.findingRevision!.version, findingDigest:paneFinding.findingRevision!.digest,
            contentKind:lessonSelected ? 'lesson' : 'report',
            expectedBase:s.outputs.findLast((o) => (lessonSelected ? o.kind === 'html' && isLesson(o) : isReportPresentation(o)) && o.current !== false)?.id ?? null});
        }
      } });
    }
    // Versions (the pill's ▾): each real version, the one shown ticked (research-tab.ts researchVersions).
    const versions = researchVersions(s, selected === 'notes' ? undefined : selected);
    // How it was made: the artifact's basis and receipt, what it worked from, where it read, its effort and spending.
    // The legacy presenter command cannot yet carry an exact old finding. Do not silently open current work instead.
    const basis = selectedVersion ? `Findings v${selectedVersion} · standard layout, no model`
      : r?.findingRevision ? `From findings v${r.findingRevision.version}` : !r && !s.outputs.length
        ? (working ? 'Findings not ready yet' : 'No findings saved') : 'Legacy work · exact findings basis unavailable';
    let basisWords = basis;
    if (selectedOutput?.kind === 'html' && selectedOutput.model) basisWords = `Presentation v${selectedOutput.version ?? 1} · from findings v${selectedOutput.findingRevision?.version ?? '?'} · ${selectedOutput.model}`;
    if (presentation) basisWords = `Presentation v${presentation.version ?? 1} · from findings v${presentation.findingRevision?.version} · ${presentation.model ?? 'Producer not recorded'}${presentation.findingRevision?.digest !== r?.findingRevision?.digest ? ' · older findings' : ''}${presentation.conflict ? ' · competing version, not current' : ''}`;
    if (r || selectedVersion || presentation || selectedOutput) basisWords += ` · ${artifactReceipt(s, presentation ?? selectedOutput, selectedVersion ?? r?.findingRevision?.version)}`;
    const how = () => {
      const box = document.createElement('div'); box.className = 'rhow-body';
      box.append(paragraph(basisWords));
      const looked = workingLine(s.working ?? [], s.ran ?? [], s.workingDropped ?? 0);
      if (looked) box.append(paragraph(looked));
      // What it noted as it went (record_finding: each fact with where it read it).
      if (s.findings.length) {
        const total = s.findingCount ?? s.findings.length;
        box.append(paragraph(total > s.findings.length ? `What it noted (the latest ${s.findings.length} of ${total}):` : 'What it noted:'), noteList(s));
      }
      const sources = groupSources(r?.sources ?? []);
      if (sources.length) {
        const p = document.createElement('p');
        p.append('Read: ');
        sources.forEach(([host, url, n], i) => { if (i) p.append(', '); p.append(link(url, n > 1 ? `${host} (${n})` : host)); });
        p.append('. Background from the web, not proof about this slide.');
        box.append(p);
      }
      box.append(costLine(s));
      const used = (r?.evidence ?? []).filter((e) => e.found === true);
      if (used.length) box.append(paragraph(`From this project: ${used.map((e) => `${clean(e.title ?? e.kind ?? e.id)}${e.time ? `, ${e.time}` : ''}`).join('; ')}`));
      const from = document.createElement('figure'); from.className = 'research-snapshot';
      const snap = document.createElement('img'); snap.src = s.snapshot; snap.alt = 'The live view when it was asked';
      from.append(snap, Object.assign(document.createElement('figcaption'), { textContent: 'Captured · the live view when it was asked' }));
      box.append(from);
      return box;
    };
    const download = selectedOutput && ['html', 'video', 'image'].includes(selectedOutput.kind) && selectedOutput.url
      ? { url: selectedOutput.url, name: clean(selectedOutput.caption) || `${s.name} ${selectedOutput.kind}` } : null;
    this.workspace.artifactControls({ versions, choose: showOnRight, how, more, download });
    const theme = (presentation?.content && 'layout' in presentation.content ? presentation.content.style?.theme : null)
      ?? paneFinding?.presentation?.plan?.style?.theme ?? null;
    const artifactContent = hero.length ? hero : paneFinding ? [researchFindingPage(paneFinding, presentation, selectAnchor, this.captureOpener(s, paneFinding), this.capturePreview(s, paneFinding))]
      : [];
    for (const element of artifactContent) if (element.classList.contains('viewer-summary')) onPaper(element, s, (index) => this.workspace.openFileTab(`table:${index}`));
    // No findings yet, but notes it kept: they are the page (Files' "Notes so far", the Kept chip).
    if (!artifactContent.length && !r && s.findings.length && (!selected || selected === 'notes')) artifactContent.push(notesPage(s));
    artifactContent.forEach((element, index) => { element.dataset.artifactKey = `${identity}/${selected || 'current'}/${index}`; });
    const waiting = this.findingErrors.get(findingKey) ?? (selectedVersion && !paneFinding ? 'Loading retained findings…' : r ? 'Its findings remain in the conversation.' : willMake(s));
    this.workspace.renderArtifact(JSON.stringify([paneFinding, s.outputs, selected, s.presentationJobs, this.findingErrors.get(findingKey), s.status, live]), artifactContent,
      { title: clean(paneFinding?.title || visibleOutput?.caption || r?.title || s.label || s.brief), theme, frame, empty: waiting, stage: stage.children.length ? stage : null });
  }

  private capturePreview(session:ResearchSession,result:ResearchSession['result']): ((sequence:number,picture:number)=>string) | undefined {
    const basis=result?.findingRevision;
    const day=session.day ?? session.snapshot?.split('/')[2];
    if (!basis || !day) return undefined;
    const identity={day,rid:session.rid,findingVersion:basis.version,findingDigest:basis.digest};
    return (sequence,picture)=>researchCaptureImageUrl({...identity,sequence,picture});
  }

  onOpenCapture?: (target:ResearchCaptureTarget)=>Promise<void>;

  private captureOpener(session: ResearchSession, result: ResearchSession['result']): ((sequence:number,picture:number)=>Promise<void>) | undefined {
    const basis = result?.findingRevision;
    const day = session.day ?? session.snapshot?.split('/')[2];
    if (!basis || !day) return undefined;
    const selection = {day,rid:session.rid,findingVersion:basis.version,findingDigest:basis.digest};
    return this.onOpenCapture ? (sequence,picture) => this.onOpenCapture!({...selection,sequence,picture}) : undefined;
  }

  private picture(url: string, caption: string, credit: string, objectId: string | null, altered: boolean, generated: boolean,
    page?: string): HTMLElement {
    const figure = document.createElement('figure');
    const img = document.createElement('img');
    img.src = url;
    img.alt = caption;
    img.loading = 'lazy';
    const cap = document.createElement('figcaption');
    cap.textContent = caption;
    if (altered) {
      const warn = document.createElement('strong');
      warn.textContent = 'The picture model may have changed the photo. ';
      cap.prepend(warn);
    } else if (generated && !/illustration|not the specimen/i.test(caption)) {
      cap.prepend('Illustration, not the specimen. ');
    }
    if (page) {
      cap.append(' ', link(page, credit || 'source'));
    }
    const show = document.createElement('button');
    show.type = 'button';
    show.textContent = 'Show in the magnifier';
    show.addEventListener('click', () => this.onShowInMagnifier(url, caption, credit, objectId));
    figure.append(img, cap, show);
    return figure;
  }
}

/** The output a researcher's work opens on: the deliverable it was asked for (a lesson, a video, a picture), else the
 * presentation of its current findings; undefined when its findings are the page. Its Tab and the Data reader agree. */
/** An HTML page a researcher wrote with make_page: a slide presentation or a small interactive visual. */
export function isResearcherPage(output: ResearchOutput): boolean {
  const kind = output.content && 'kind' in output.content ? (output.content as { kind?: unknown }).kind : undefined;
  return output.kind === 'html' && (kind === 'presentation' || kind === 'visual');
}

export function defaultResearchOutput(s: ResearchSession, r: ResearchSession['result'] | null): ResearchOutput | undefined {
  const deliverableFormat: Record<string,string> = {lesson:'html', video:'video', image:'image', annotated_image:'image'};
  const requestedFormat = deliverableFormat[s.deliverable ?? ''];
  // A page the researcher wrote itself (make_page: slides or an interactive visual) is what its work is opened on,
  // rendered in its sandboxed frame, never its findings as text (Will, 2026-10-30).
  const page = requestedFormat ? undefined : s.outputs.findLast(o => o.current !== false && isResearcherPage(o));
  if (page) return page;
  return s.outputs.findLast(o => o.current !== false && (requestedFormat
    ? o.kind === requestedFormat : o.kind === 'presentation' && !!r?.findingRevision?.digest
      && o.findingRevision?.digest === r.findingRevision.digest));
}

/** A lesson, video or picture a researcher made, as its page shows it: the lesson's sandboxed frame (frame: the page
 * holds it at the desk's height), the video, the pictures; wrapped once, so updating the version links never recreates
 * an iframe or resets a video's playhead. Nothing for a presentation or none: the findings are then the page. The
 * researcher's Tab and the Data reader draw the same nodes. */
export function researchHero(output: ResearchOutput | undefined, finding: ResearchSession['result'] | null,
  hooks: { escape?: () => void; picture: (o: ResearchOutput, caption: string) => HTMLElement }): { nodes: HTMLElement[]; frame: boolean } {
  const hero: HTMLElement[] = [];
  const currentOutputs = output ? [output] : [];
  const version = (o: ResearchOutput) => ((o.version ?? 1) > 1 ? `v${o.version} · ${clean(o.change ?? '') || 'revised'}. ` : '');
  let frame = false;
  for (const o of currentOutputs.filter((o) => o.kind === 'html')) {
    const lesson = document.createElement('iframe');
    lesson.className = 'research-lesson';
    lesson.src = o.url;
    lesson.title = o.caption;
    lesson.setAttribute('sandbox', 'allow-scripts');
    // Sandboxed by the server's policy for every lesson (no same-origin, no network).
    lesson.setAttribute('referrerpolicy', 'no-referrer');
    if (hooks.escape) bindArtifactEscape(lesson, hooks.escape);
    hero.push(renderArtifactContext(finding ?? null), lesson, smallNote(version(o) + madeBy(o.caption), link(o.url, 'Open it in its own tab')));
    frame = true;
  }
  for (const o of currentOutputs.filter((o) => o.kind === 'video')) {
    const video = document.createElement('video');
    video.className = 'research-video';
    video.src = o.url;
    video.controls = true;
    video.muted = true; // Veo always adds generated sound (spike S6)
    video.playsInline = true;
    video.loop = true;
    hero.push(video, smallNote(version(o) + clean(o.caption)));
  }
  const pictures = currentOutputs.filter((o) => o.kind === 'image');
  if (pictures.length) {
    const gallery = document.createElement('div');
    gallery.className = 'research-gallery rs-pictures';
    for (const o of pictures) gallery.append(hooks.picture(o, version(o) + clean(o.caption)));
    hero.push(gallery);
  }
  if (hero.length) {
    const box = document.createElement('div');
    box.className = 'rs-hero';
    box.append(...hero);
    hero.splice(0, hero.length, box);
  }
  return { nodes: hero, frame };
}

/** A researcher's findings as a page: its presentation's own layout when it has one, else the report. */
export function researchFindingPage(finding: NonNullable<ResearchSession['result']>, presentation: ResearchOutput | undefined,
  select?: (anchor: string) => void, openCapture?: (sequence: number, picture: number) => void, capturePreview?: (sequence: number, picture: number) => string): HTMLElement {
  return presentation?.content && 'layout' in presentation.content
    ? renderResearchPresentation(finding, presentation.content, select, openCapture, capturePreview)
    : renderResearchReport(finding, select, openCapture, capturePreview);
}

/** One chart of a researcher's findings, as its bars. */
export function researchChartNode(result: NonNullable<ResearchSession['result']>, index: number, select?: (anchor: string, label?: string) => void): HTMLElement | null {
  const chart = result.charts?.[index]; if (!chart) return null;
  const wrap = document.createElement('div'); wrap.className = 'rtab-file rtab-chart';
  wrap.append(renderResearchChart(chart, index, (anchor) => select?.(anchor, chart.title)));
  return wrap;
}

/** One table of a researcher's findings, as a table: a column of numbers set right in tabular figures, words left. */
export function researchTableNode(result: NonNullable<ResearchSession['result']>, index: number): HTMLElement | null {
  const data = result.tables?.[index]; if (!data) return null;
  const wrap = document.createElement('div'); wrap.className = 'rtab-file';
  const scroll = document.createElement('div'); scroll.className = 'rtable-wrap'; scroll.tabIndex = 0;
  scroll.setAttribute('role', 'region'); scroll.setAttribute('aria-label', data.title);
  const table = document.createElement('table'); table.className = 'rtable';
  const numeric = data.columns.map((_, column) => data.rows.length > 0 && data.rows.every((row) => /^[-+]?[\d.,\s]+%?$/.test(String(row[column] ?? '').trim())));
  const head = document.createElement('thead'); const headers = document.createElement('tr');
  data.columns.forEach((column, index) => { const th = document.createElement('th'); th.scope = 'col'; th.textContent = column; if (numeric[index]) th.dataset.num = 'true'; headers.append(th); });
  head.append(headers);
  const body = document.createElement('tbody');
  for (const row of data.rows) {
    const tr = document.createElement('tr');
    row.forEach((value, index) => { const td = document.createElement('td'); td.textContent = value; if (numeric[index]) td.dataset.num = 'true'; tr.append(td); });
    body.append(tr);
  }
  table.append(head, body); scroll.append(table);
  const foot = document.createElement('p'); foot.className = 'rtable-foot mm-num';
  foot.textContent = `${data.rows.length} row${data.rows.length === 1 ? '' : 's'}`;
  wrap.append(scroll, foot);
  return wrap;
}

function statusLine(s: ResearchSession): HTMLElement {
  const p = document.createElement('p');
  p.className = 'research-status';
  p.dataset.status = s.status;
  if (s.mission?.acquisition) {
    const m = s.mission;
    const active = MISSION_ACTIVE.has(m.status);
    const count = m.ledger.filter((slot) => slot.outcome === 'done').length;
    const gaps = m.ledger.length - count;
    p.dataset.status = active ? 'running' : m.status;
    p.setAttribute('aria-live', 'polite');
    p.textContent = `${active ? (m.status === 'finishing' ? 'Analyzing captured clips' : 'Capture schedule active')
      : `Capture schedule ${m.status}`} · ${count}/${m.slots} clips retained${gaps ? ` · ${gaps} failed or missed` : ''}`
      + (m.whyEnded && m.status !== 'done' ? ` · ${m.whyEnded}` : '');
    return p;
  }
  const step = (ACTIVE.has(s.status) || s.status === 'failed') && s.step ? `: ${s.step}` : '';
  p.textContent = `${STATUS[s.status] ?? s.status}${step}`;
  return p;
}

function costLine(s: ResearchSession, compact = false): HTMLElement {
  const p = document.createElement('p');
  p.className = 'research-cost';
  const minutes = s.seconds >= 120 ? `${Math.round(s.seconds / 60)} min` : `${Math.round(s.seconds)} s`;
  p.textContent = `${s.effort} effort, up to ${minutes}; ${spendingSummary(s, compact)}`;
  return p;
}

export { clean } from './research-report';

/** Sources as one link per site, with how many pages it read there. */
function groupSources(urls: string[]): [string, string, number][] {
  const by = new Map<string, [string, string, number]>();
  for (const url of urls) {
    const host = hostOf(url);
    const seen = by.get(host);
    if (seen) seen[2] += 1;
    else by.set(host, [host, url, 1]);
  }
  return [...by.values()];
}

/** "Interactive lesson: X (written by gpt-6.1-sol)" -> "Written by gpt-6.1-sol." */
function madeBy(caption: string): string {
  const m = caption.match(/\((?:written|made|drawn) by ([^)]+)\)\s*$/i);
  return m ? `Written by ${m[1]}.` : '';
}

/** A step of its work as a compact row (researcher-spec.md 4c): its mark, what it did naming the tool or model, at most
 * two facts, then chips for what it made (each opens what it names) or its one action. */
function stepRow(mark: ResearchStepMark, title: string, sub: string, extra: HTMLElement[], key: string): HTMLElement {
  const row = document.createElement('div'); row.className = 'step'; row.dataset.step = key; row.dataset.mark = mark;
  const markBox = document.createElement('span'); markBox.className = 'step-mark';
  const cells: Partial<Record<ResearchStepMark, [LitCellState, string]>> = { live: ['working', 'Working'], still: ['still', 'Stop requested'],
    partial: ['partial', 'Partial findings'], stopped: ['stopped', 'Stopped'], attention: ['attention', 'Needs attention'] };
  const cell = cells[mark];
  markBox.append(cell ? litCell(cell[0], { size: 12, label: cell[1] }) : researchIcon(mark === 'note' ? 'bubble' : 'tick', mark === 'note' ? 'rtab-icon' : 'tick'));
  const words = document.createElement('span'); words.className = 'step-words';
  const b = document.createElement('b'); b.textContent = title; words.append(b);
  if (sub) { const line = document.createElement('span'); line.className = 'step-sub'; line.textContent = sub; line.title = sub; words.append(line); }
  if (extra.length) { const chips = document.createElement('span'); chips.className = 'chips-row'; chips.append(...extra); words.append(chips); }
  row.append(markBox, words);
  return row;
}

/** The findings as a page reads (the mock's report page): its byline under the title (who wrote it and when, with the
 * findings' own revision line), "In short" over its short answer, its sources as numbered references, and a long table
 * as its first rows with "Open" (the whole table opens in the reader, its Data entry). Only the page's arrangement:
 * every fact is the renderer's. */
const PAGE_ROWS = 8;
function onPaper(report: HTMLElement, s: ResearchSession, openTable: (index: number) => void): void {
  const title = [...report.children].find((child) => child.tagName === 'H2');
  const when = s.finished ?? s.started ?? s.created;
  const revision = [...report.children].find((child) => child.classList.contains('sources') && /^(Findings|Summary only)/.test(child.textContent ?? ''));
  const byline = document.createElement('p'); byline.className = 'byline';
  byline.textContent = [`${s.name}, ${new Date(when * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })}`, revision?.textContent ?? ''].filter(Boolean).join(' · ');
  revision?.remove();
  if (title) title.after(byline); else report.prepend(byline);
  const summary = report.querySelector<HTMLElement>('[data-anchor="summary"]');
  if (summary && !summary.previousElementSibling?.classList.contains('rtab-inshort')) {
    const heading = document.createElement('h3'); heading.className = 'rtab-inshort'; heading.textContent = 'In short';
    summary.before(heading);
  }
  // Sources: a real sequence, so numbered, one reference a line ("[1] nature.com ↗").
  for (const line of [...report.querySelectorAll<HTMLElement>('.sources')].filter((p) => /^Sources:/.test(p.textContent ?? ''))) {
    const links = [...line.querySelectorAll<HTMLAnchorElement>('a')];
    if (!links.length) continue;
    const section = document.createElement('section'); section.className = 'page-refs'; section.dataset.anchor = 'sources';
    const heading = document.createElement('h3'); heading.textContent = 'Sources';
    const list = document.createElement('ol');
    links.forEach((a, index) => {
      const li = document.createElement('li');
      a.textContent = `[${index + 1}] ${a.textContent ?? ''} ↗`;
      li.append(a); list.append(li);
    });
    section.append(heading, list);
    line.after(section); line.remove();
  }
  // A table: numbers set right in tabular figures; a long one shows its first rows, its count and Open.
  for (const table of [...report.querySelectorAll<HTMLElement>('.research-table')].filter((t) => (t.dataset.anchor ?? '').startsWith('table:'))) {
    const index = Number((table.dataset.anchor ?? '').split(':')[1]);
    const body = table.querySelector('tbody'), rows = body ? [...body.children] : [];
    const heads = [...(table.querySelector('thead')?.querySelectorAll('th') ?? [])];
    heads.forEach((head, column) => {
      const cells = rows.map((row) => row.children[column]).filter(Boolean);
      if (!cells.length || !cells.every((cell) => /^[-+]?[\d.,\s]+%?$/.test((cell.textContent ?? '').trim()))) return;
      for (const cell of [head, ...cells]) (cell as HTMLElement).dataset.num = 'true';
    });
    if (rows.length <= PAGE_ROWS) continue;
    for (const row of rows.slice(PAGE_ROWS)) row.remove();
    const more = document.createElement('p'); more.className = 'page-table-more';
    const name = table.getAttribute('aria-label') || 'the table';
    const open = document.createElement('button'); open.type = 'button'; open.className = 'page-table-open';
    open.textContent = `Open ${name}`; open.title = `Open all ${rows.length} rows of ${name} here`;
    open.addEventListener('click', () => openTable(index));
    more.append(`${rows.length} rows`, open);
    table.after(more);
  }
}

/** What it noted as it went (record_finding), each fact with the sites it read it on. */
function noteList(s: ResearchSession): HTMLUListElement {
  const list = document.createElement('ul'); list.className = 'research-notes';
  for (const note of s.findings) {
    const li = document.createElement('li'); li.textContent = clean(note.text);
    const hosts = groupSources(note.sources ?? []);
    if (hosts.length) {
      li.append(' (');
      hosts.forEach(([host, url], i) => { if (i) li.append(', '); li.append(link(url, host)); });
      li.append(')');
    }
    list.append(li);
  }
  return list;
}

/** Its notes as a page, when it kept notes but no findings (stopped, interrupted, or still working toward them). */
function notesPage(s: ResearchSession): HTMLElement {
  const page = document.createElement('div'); page.className = 'viewer-summary rtab-notes';
  const title = document.createElement('h2'); title.textContent = 'What it noted so far';
  const when = s.finished ?? s.started ?? s.created;
  const byline = document.createElement('p'); byline.className = 'byline';
  byline.textContent = `${s.name}, ${new Date(when * 1000).toLocaleDateString([], { month: 'short', day: 'numeric' })}`;
  const list = noteList(s); list.dataset.anchor = 'notes';
  page.append(title, byline, list);
  const total = s.findingCount ?? s.findings.length;
  if (total > s.findings.length) page.append(Object.assign(document.createElement('p'), { className: 'sources', textContent: `Its latest ${s.findings.length} of ${total} notes.` }));
  return page;
}

/** A link chip on a step: a 14 px kind and its words; ↗ when it leaves the Tab. `show` marks it pressed while what it
 * names is on the right. */
function chip(icon: string, label: string, act: () => void, title: string, show?: string): HTMLButtonElement {
  const b = document.createElement('button'); b.type = 'button'; b.className = 'chip'; b.title = title;
  if (show) { b.dataset.show = show; b.setAttribute('aria-pressed', 'false'); }
  const words = document.createElement('span'); words.textContent = label;
  b.append(researchIcon(icon), words);
  b.addEventListener('click', act);
  return b;
}

/** The researcher's own words in its conversation (an answer, its plan, its short finding): text on the panel, no card. */
function said(text: string, label?: string): HTMLElement {
  const message = document.createElement('div'); message.className = 'm-asst';
  if (label) { const small = document.createElement('small'); small.className = 'm-label'; small.textContent = label; message.append(small); }
  if (text) message.append(paragraph(text));
  return message;
}

function smallNote(text: string, extra?: HTMLElement): HTMLParagraphElement {
  const p = document.createElement('p');
  p.className = 'rs-note';
  p.textContent = text;
  if (extra) p.append(text ? ' ' : '', extra);
  return p;
}


function paragraph(text: string): HTMLParagraphElement {
  const p = document.createElement('p');
  p.textContent = text;
  return p;
}


function link(url: string, text: string): HTMLAnchorElement {
  const a = document.createElement('a');
  a.href = url;
  a.target = '_blank';
  a.rel = 'noopener noreferrer';
  a.textContent = text;
  return a;
}


const KIND_WORDS: Record<string, string> = {
  population: 'cell outlines', clip: 'a held clip', result: 'a result', research: "a researcher's work",
  snapshot: 'a snapshot', named: 'a thing named', taught: 'a label taught', scale: 'the scale', dataset: 'a dataset',
  recording: 'a recording', bookmark: 'a bookmark', track: 'a track',
};
const WORKING_SHOWN = 8;
const oneLine = (text: string) => clean(text).replace(/\s+/g, ' '); // shown as text (textContent), one line

/** The mission card's working set: what a researcher had the app make (`ran`, the app's own record of its runs) and
 * what it looked at in the session, once each, by the index's own titles (the app's words), the newest 8 of each,
 * in time order; ids one step away, in brackets; a record corrected since it was read says so (C1). */
export function workingLine(working: WorkingRecord[],
  ran: { id: string; at: number; title?: string; kind?: string; version?: number | null; now?: number }[] = [],
  dropped = 0): string {
  const say = (records: { id: string; how?: string; title?: string; kind?: string; at: number; version?: number | null; now?: number }[],
    verb = 'read') => {
    const seen = new Map<string, { id: string; title: string; kind: string; pictures: number; read: number[]; now?: number; at: number }>();
    for (const w of [...records].sort((a, b) => a.at - b.at)) {
      const item = seen.get(w.id) ?? { id: w.id, title: '', kind: '', pictures: 0, read: [], at: 0 };
      item.title ||= w.title ?? '';
      item.kind ||= w.kind ?? '';
      if ((w.how ?? '').startsWith('picture')) item.pictures += 1;
      if (typeof w.version === 'number' && !item.read.includes(w.version)) item.read.push(w.version);
      if (typeof w.now === 'number') item.now = w.now;
      item.at = w.at;
      seen.set(w.id, item);
    }
    const items = [...seen.values()].sort((a, b) => a.at - b.at);
    const shown = items.slice(-WORKING_SHOWN);
    return {
      count: items.length,
      text: shown.map((i) => {
        const latest = i.read[i.read.length - 1];
        const corrected = i.now !== undefined && latest !== undefined && i.now !== latest
          ? `, ${verb} at revision ${i.read.join(', ')}, corrected since` : '';
        const pictures = i.pictures ? `, ${i.pictures} picture${i.pictures > 1 ? 's' : ''}` : '';
        return `${oneLine(i.title || KIND_WORDS[i.kind] || 'a record')} (${oneLine(i.id)}${corrected})${pictures}`;
      }).join('; ') + (items.length > shown.length ? `; and ${items.length - shown.length} more` : ''),
    };
  };
  const parts = [];
  const made = say(ran.filter((r) => r.id), 'made');
  const looked = say(working.filter((w) => w.id && typeof w.how === 'string'));
  if (made.count) parts.push(`It had the app make: ${made.text}.`);
  if (looked.count) parts.push(`It looked at: ${looked.text}${dropped ? `; and ${dropped} earlier` : ''}.`);
  return parts.join(' ');
}
