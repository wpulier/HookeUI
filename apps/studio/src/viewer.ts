import { bindArtifactEscape } from './artifact-keyboard';
import { sectionWords } from './data-roles';
import { researchCaptureImageUrl } from './research-captures';
// The result viewer: a researcher's work beside the live view (lesson, video, picture or its
// written result). The server decides what is open (orchestrator/presenter.py), so voice and
// chat can open and close it too; this draws it and reports the user's own choices. Closing
// keeps it on the shelf; Escape closes it (it is the side column's panel, panels.ts).

import { stepNote } from './quiz';
import { FloatingWindow, type PanelStack, type WindowFacts } from './panels';
import { clean, renderResearchReport } from './research-report';
import { renderArtifactContext } from './presentation';
import type { ClientCommand, ResearchCaptureTarget, PresenterState, ResearchOutput, ResearchSession, ResultItem } from './protocol';

/** The output a result-viewer item shows (none for the written result). The items, their order
 * and their labels are the server's (ResearchSession.items, orchestrator/presenter.py items). */
export function outputOf(s: ResearchSession, item: ResultItem | undefined): ResearchOutput | undefined {
  return item?.id ? s.outputs.find((o) => o.id === item.id) : undefined;
}

/** "v2 · make question 2 easier" for a revised version, "v1 · an older version" for one it replaced. */
export function versionNote(o: ResearchOutput | undefined): string {
  if (!o) return '';
  const v = o.version ?? 1;
  if (o.current === false) return `v${v} · an older version`;
  return v > 1 ? `v${v} · ${clean(o.change ?? '') || 'revised'}` : '';
}

/** The line above an item saying what kind of thing it is (pictures, videos and reference photos; a lesson and
 * the written result say it themselves). An item with no record of how it was made is never called real. */
export function kindNote(item: ResultItem | undefined): string {
  if (!item || !['image', 'video', 'reference'].includes(item.kind)) return '';
  return item.line || 'A picture; how it was made is not recorded';
}

/** Fit the source aspect within both viewport limits, leaving room for actual chrome. */
export function fittedResultSize(maxWidth: number, maxHeight: number, chrome: number, aspect: number): [number, number] {
  const width = Math.max(1, Math.min(maxWidth, Math.max(1, maxHeight - chrome) * aspect));
  return [width, Math.max(1, Math.min(maxHeight, width / aspect + chrome))];
}

export class ResultViewer {
  private readonly root = document.getElementById('viewer')!;
  private readonly title = document.getElementById('viewer-title')!;
  private readonly from = document.getElementById('viewer-from')!;
  private readonly tabs = document.getElementById('viewer-tabs')!;
  private readonly body = document.getElementById('viewer-body')!;
  private readonly side = document.getElementById('side')!;
  private readonly send: (command: ClientCommand) => void;
  private readonly sessions: () => Map<string, ResearchSession>;
  state: PresenterState = { type: 'presenter', open: false, rid: null, index: 0 };
  private shownKey = '';
  private tabsKey = '';
  private shownWork: ResultItem['work'];
  private shownRid = '';
  private wasOpen = false;
  readonly window?: FloatingWindow;
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

  private mediaAspect = 4 / 3;
  private userSized = false;
  private readonly details = document.createElement('div');
  private readonly menuButton = document.createElement('button');
  private previousFocus: HTMLElement | null = null;


  constructor(send: (command: ClientCommand) => void, sessions: () => Map<string, ResearchSession>, chrome?: {panels: PanelStack; facts: () => WindowFacts | null; changed?: (property: string) => void}) {
    this.send = send;
    this.sessions = sessions;
    document.getElementById('viewer-close')!.addEventListener('click', () => this.close());
    const head = this.root.querySelector<HTMLElement>('.viewer-head')!;
    this.details.className = 'viewer-details'; this.details.id = 'viewer-details'; this.details.hidden = true;
    this.menuButton.type = 'button'; this.menuButton.className = 'viewer-more'; this.menuButton.textContent = '⋯';
    this.menuButton.setAttribute('aria-label', 'Result details and controls'); this.menuButton.setAttribute('aria-expanded', 'false');
    this.menuButton.setAttribute('aria-controls', this.details.id);
    this.menuButton.addEventListener('click', () => { this.details.hidden = !this.details.hidden; this.menuButton.setAttribute('aria-expanded', String(!this.details.hidden)); });
    const fit = document.createElement('button'); fit.type = 'button'; fit.textContent = 'Fit';
    fit.addEventListener('click', () => { this.userSized = false; this.fit(); this.saveGeometry(); });
    this.details.append(this.from, fit); this.root.append(this.details);
    head.insertBefore(this.menuButton, document.getElementById('viewer-close'));
    this.root.addEventListener('keydown', event => {
      if (event.key === 'Escape' && !this.details.hidden) { event.preventDefault(); event.stopPropagation(); this.details.hidden = true; this.menuButton.setAttribute('aria-expanded', 'false'); this.menuButton.focus(); }
    });
    if (chrome) {
      // Mount before creating embedded content: moving a live iframe reloads its document.
      document.getElementById('stage')!.append(this.root);
      this.window = chrome.panels.register(new FloatingWindow({id:'viewer', root:this.root, bar:head, panels:chrome.panels,
        close:() => this.close(), facts:chrome.facts, freeResize:true, chromeHeight:42,
        content:() => ['image','reference','video'].includes(this.body.dataset.kind ?? '') ? {kind:'picture', aspect:this.mediaAspect} : {kind:'text'},
        onUserChange:property => { if (property === 'resize') this.userSized = true; this.saveGeometry(); chrome.changed?.(property); },
      }));
    }

  }

  show(rid: string, index?: number): void {
    const saved = this.state.savedSession;
    if (saved?.rid === rid) {
      const item = saved.items[index ?? 0];
      const day = saved.day ?? item?.work?.resource.day;
      if (!item?.work || !day) {
        this.from.textContent = 'This saved item has no retained address. Open its original research to inspect available work.';
        this.details.hidden = false; this.menuButton.setAttribute('aria-expanded', 'true');
        return;
      }
      this.send({type:'presenter', action:'show', rid, day, savedItem:item.work});
      return;
    }
    this.send({ type: 'presenter', action: 'show', rid, index });
  }

  close(): void {
    this.state = { ...this.state, open: false };
    this.render();
    this.send({ type: 'presenter', action: 'close' });
    if (this.previousFocus?.isConnected) this.previousFocus.focus({preventScroll:true});
  }

  apply(state: PresenterState): void {
    this.state = state;
    this.render();
  }

  selectedWork(): ResultItem['work'] {
    return this.state.open ? this.shownWork : undefined;
  }

  displayedWork(): ResultItem['work'] {
    if (!this.state.open) return undefined;
    const image = this.body.querySelector<HTMLImageElement>('img');
    if (image && (!image.complete || image.naturalWidth === 0)) return undefined;
    const video = this.body.querySelector<HTMLVideoElement>('video');
    if (video && (video.readyState < 2 || video.error)) return undefined;
    const lesson = this.body.querySelector<HTMLElement>('.viewer-lesson');
    if (lesson && lesson.dataset.ready !== 'true') return undefined;
    const presentation = this.body.querySelector<HTMLElement>('.viewer-presentation');
    if (presentation && presentation.dataset.ready !== 'true') return undefined;
    return this.shownWork;
  }

  render(): void {
    const s = this.state.savedSession ?? (this.state.rid ? this.sessions().get(this.state.rid) : undefined);
    const open = this.state.open && !!s;
    this.root.hidden = !open;
    this.side.dataset.viewer = String(open);
    const opening = open && !this.wasOpen;
    this.wasOpen = open;
    if (opening) { this.previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null; this.restoreGeometry(); }
    this.window?.frame();
    for (const v of this.body.querySelectorAll('video')) {
      if (!open) v.pause();
      else if (opening) void v.play().catch(() => {});
    }
    if (!open || !s) return; // what was shown stays loaded (hidden), so reopening it is instant
    const items = s.items;
    const pinned = this.state.outputId ? items.findIndex(it => it.id === this.state.outputId)
      : this.state.findingRevision ? items.findIndex(it => it.kind === 'summary') : -1;
    const index = pinned >= 0 ? pinned : Math.max(0, Math.min(this.state.index, items.length - 1));
    const item = items[index];
    const output = outputOf(s, item);
    this.title.textContent = clean(output?.caption) || clean(s.result?.title) || s.label || s.brief.slice(0, 60);
    this.title.title = this.title.textContent;
    const day = s.day ?? item?.work?.resource.day ?? s.snapshot?.split('/')[2];
    const reference = new URLSearchParams(day ? {day} : {});
    if (output?.id) reference.set('output', output.id);
    else if (this.state.findingRevision ?? s.result?.findingRevision) reference.set('findings', String((this.state.findingRevision ?? s.result!.findingRevision)!.version));
    this.from.replaceChildren(`From ${s.name}. `, link(`#research/${s.rid}?${reference}`, 'See full research', false));
    const tabsKey = JSON.stringify([s.rid, index, items.map(it => [it.kind, it.id, it.ref, it.label])]);
    this.tabs.hidden = items.length < 2;
    if (tabsKey !== this.tabsKey) {
      this.tabsKey = tabsKey;
      this.tabs.replaceChildren(...items.map((it, i) => {
        const b = document.createElement('button');
        b.type = 'button'; b.setAttribute('role', 'tab');
        b.setAttribute('aria-selected', String(i === index)); b.textContent = it.label;
        b.addEventListener('click', () => this.show(s.rid, i));
        return b;
      }));
    }
    // Output identity is stable while current/older flags or adjacent tabs change.
    // Non-output references still need their exact item identity, not just the shared report.
    const key = JSON.stringify([s.day, s.rid,
      output ? [output.id, output.version ?? 1, output.kind, output.url, output.findingRevision?.digest]
        : [item?.kind, item?.id, item?.ref, item?.work],
      output ? null : this.state.findingRevision ?? s.result ?? null]);
    if (key === this.shownKey) {
      this.updateVersion(output);
      this.scrollToStep();
      if (opening) this.slideIn();
      return;
    }
    const fresh = opening || this.shownRid !== s.rid;
    this.shownKey = key;
    this.shownWork = item?.work && !output && this.state.findingRevision
      ? {...item.work, resource:{...item.work.resource, version:this.state.findingRevision.version}} : item?.work;
    this.shownRid = s.rid;
    this.body.dataset.kind = item?.kind ?? '';
    this.body.replaceChildren(item ? this.content(s, item, output) : document.createTextNode('Nothing to show yet.'));
    // What kind of thing it is, above it (kind (f)): an AI illustration, a labelled photo of this slide, a
    // reference photo from the web; the server's words (orchestrator/media.py), never guessed from a caption.
    for (const old of this.details.querySelectorAll('.viewer-kind, .viewer-version, figcaption')) old.remove();
    const line = kindNote(item);
    if (line) {
      const note = document.createElement('p');
      note.className = 'viewer-kind';
      note.dataset.media = item?.media ?? 'unknown';
      note.textContent = line;
      this.details.append(note);
    }
    if (['image','reference','video'].includes(item?.kind ?? '')) {
      for (const caption of this.body.querySelectorAll('figcaption')) this.details.append(caption);
    }
    this.updateVersion(output);
    this.shownStep = '';
    this.scrollToStep();
    if (!this.userSized) this.fit();
    if (fresh) this.slideIn();
  }

  // Going through a lesson step by step (voice or chat: "next question"): the lesson scrolls
  // to that step's anchor (id="q2"). A fragment is a navigation within the page, so it needs
  // no script and the lesson stays sandboxed with no way to reach the app.
  private shownStep = '';
  private scrollToStep(): void {
    const step = this.state.step ?? '';
    const frame = this.body.querySelector<HTMLIFrameElement>('.viewer-lesson iframe');
    if (!frame || !/^[a-z0-9-]{1,12}$/i.test(step)) return;
    if (step !== this.shownStep) {
      this.shownStep = step;
      const base = frame.src.split('#')[0];
      frame.src = `${base}#${step}`;
    }
    // Which step, how it is answered (a find step: on the slide) and how the quiz is going (quiz.ts);
    // the outcomes change without the step changing, so the note is redrawn every time.
    const note = this.body.querySelector<HTMLElement>('.viewer-step');
    const text = stepNote(this.state);
    if (note) note.textContent = text;
    else {
      const el = document.createElement('p');
      el.className = 'viewer-step';
      el.textContent = text;
      frame.parentElement?.prepend(el);
    }
  }

  private slideIn(): void {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    this.root.animate([{ opacity: 0 }, { opacity: 1 }],
      { duration: 260, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' });
  }

  private updateVersion(output: ResearchOutput | undefined): void {
    this.details.querySelector('.viewer-version')?.remove();
    const version = versionNote(output);
    if (!version) return;
    const note = document.createElement('p'); note.className = 'viewer-version'; note.textContent = version; this.details.append(note);
  }

  private fit(): void {
    if (!this.window) return;
    const stage = this.root.parentElement!.getBoundingClientRect();
    const media = ['image','reference','video'].includes(this.body.dataset.kind ?? '');
    const width = Math.min(window.innerWidth <= 960 ? stage.width - 20 : 520, stage.width - 20);
    const extra = (window.innerWidth <= 960 ? 44 : 42) + (this.tabs.hidden ? 0 : 36);
    const size = media ? fittedResultSize(width, stage.height - 20, extra, this.mediaAspect)
      : [Math.max(1,width), Math.max(1,Math.min(500, stage.height - 20))];
    this.window.resizeTo(size[0], size[1]);
  }

  private saveGeometry(): void {
    try { localStorage.setItem('micromind.viewer.geometry', JSON.stringify({left:this.root.style.left,top:this.root.style.top,width:this.root.style.width,height:this.root.style.height,userSized:this.userSized})); } catch { /* Browser storage is optional. */ }
  }

  private restoreGeometry(): void {
    try {
      const saved = JSON.parse(localStorage.getItem('micromind.viewer.geometry') ?? 'null') as {left:string;top:string;width:string;height:string;userSized:boolean} | null;
      if (!saved) return;
      const [x,y,w,h] = [saved.left,saved.top,saved.width,saved.height].map(parseFloat);
      if (![x,y,w,h].every(Number.isFinite) || w <= 0 || h <= 0) return;
      this.userSized = saved.userSized === true; this.window?.moveTo(x,y); this.window?.resizeTo(w,h);
    } catch { /* A corrupt preference cannot prevent opening retained work. */ }
  }

  private mediaStatus(media: HTMLImageElement | HTMLVideoElement, wrap: HTMLElement, url: string): void {
    const status = document.createElement('div'); status.className = 'viewer-media-status'; status.setAttribute('role','status'); status.textContent = 'Loading saved media…';
    const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry loading'; retry.hidden = true;
    let timeout: ReturnType<typeof setTimeout>;
    const fail = () => { clearTimeout(timeout); wrap.dataset.ready = 'false'; status.hidden = false; status.firstChild!.textContent = 'This saved media could not be loaded. '; retry.hidden = false; };
    const load = () => {
      clearTimeout(timeout); status.hidden = false; status.firstChild!.textContent = 'Loading saved media…'; retry.hidden = true; wrap.dataset.ready = 'false';
      media.src = url; if (media instanceof HTMLVideoElement) media.load();
      timeout = setTimeout(fail, 15000);
    };
    retry.addEventListener('click', load); status.append(retry); wrap.append(status);
    media.addEventListener('error', fail);
    media.addEventListener(media instanceof HTMLVideoElement ? 'loadeddata' : 'load', () => {
      clearTimeout(timeout); status.hidden = true; wrap.dataset.ready = 'true';
      const width = media instanceof HTMLVideoElement ? media.videoWidth : media.naturalWidth;
      const height = media instanceof HTMLVideoElement ? media.videoHeight : media.naturalHeight;
      // Late loads may fit their own open content, never resize a newer selection or reopen a closed result.
      if (width > 0 && height > 0 && this.body.contains(media) && this.state.open) { this.mediaAspect = width / height; if (!this.userSized) this.fit(); }
    });
    load();
  }

  private artifactContext(s: ResearchSession, output: ResearchOutput): HTMLElement {
    const basis = output.findingRevision;
    if (basis && s.result?.findingRevision?.version === basis.version && s.result.findingRevision.digest === basis.digest) {
      return renderArtifactContext(s.result);
    }
    const context = document.createElement('section');
    context.className = 'artifact-evidence-context'; context.setAttribute('aria-label', 'Retained findings and limitations');
    if (!basis) {
      context.textContent = 'No findings revision was retained for this lesson. Its original limitations are unavailable.';
      return context;
    }
    const day = s.day ?? s.snapshot?.split('/')[2];
    if (!day) { context.textContent = 'The original findings day is unavailable; latest findings have not been substituted.'; return context; }
    context.textContent = 'Loading this lesson’s retained findings and limitations…';
    void fetch(`/api/research/${encodeURIComponent(s.rid)}/findings/${basis.version}?day=${encodeURIComponent(day)}`)
      .then(async response => {
        if (!response.ok) throw new Error('This lesson’s original findings and limitations are unavailable.');
        const record = await response.json() as {version:number; digest:string; result:ResearchSession['result']};
        if (record.version !== basis.version || record.digest !== basis.digest || !record.result) throw new Error('The retained findings identity did not match this lesson.');
        context.replaceChildren(renderArtifactContext(record.result));
      }).catch((error:unknown) => { context.textContent = error instanceof Error ? error.message : 'Could not load this lesson’s original findings.'; });
    return context;
  }

  private content(s: ResearchSession, item: ResultItem, o: ResearchOutput | undefined): HTMLElement {
    const kind = item.kind;
    const ref = kind === 'reference' && typeof item.ref === 'number' ? s.result?.refs?.[item.ref] : undefined;
    if (ref) {
      // A reference photo it found on the web: a real photo, not this slide, with its source.
      const fig = document.createElement('figure');
      const a = link(ref.page, '', true);
      const img = document.createElement('img');
      this.mediaStatus(img, fig, ref.url);
      img.alt = ref.title || 'Reference photo';
      a.append(img);
      fig.append(a, caption(clean(ref.title) || 'Reference photo', link(ref.page, ref.credit || 'its page', true)));
      return fig;
    }
    if (kind === 'html' && o) {
      const wrap = document.createElement('div');
      wrap.className = 'viewer-lesson';
      const frame = document.createElement('iframe');
      frame.src = o.url;
      frame.title = o.caption;
      frame.setAttribute('sandbox', 'allow-scripts');
      // The server serves every lesson under a sandbox policy (no same-origin, no network:
      // orchestrator/research/files.py), so it cannot reach the app. Only our own files.
      if (!o.url.startsWith('/research/')) return document.createTextNode('This lesson is not one of ours.') as unknown as HTMLElement;
      frame.setAttribute('referrerpolicy', 'no-referrer');
      bindArtifactEscape(frame, () => this.close());
      // Until the lesson has drawn, say so rather than show an empty panel.
      wrap.dataset.loading = '';
      let loaded = false, available = false;
      const ready = () => {
        if (loaded && available) { wrap.dataset.ready = 'true'; delete wrap.dataset.loading; }
      };
      frame.addEventListener('load', () => { loaded = true; ready(); }, { once: true });
      void fetch(o.url, { method: 'HEAD' }).then(response => {
        available = response.ok && (response.headers.get('content-type') ?? '').startsWith('text/html');
        ready();
        if (!available) wrap.dataset.error = 'Lesson unavailable';
      }).catch(() => { wrap.dataset.error = 'Lesson unavailable'; });
      window.setTimeout(() => delete wrap.dataset.loading, 8000); // never hide a lesson for good
      wrap.append(this.artifactContext(s, o), frame, caption(o.caption, link(o.url, 'Open it full size', true)));
      return wrap;
    }
    if (kind === 'presentation' && o) {
      const wrap = document.createElement('div'); wrap.className = 'viewer-presentation';
      wrap.textContent = 'Loading the exact saved presentation…';
      const day = s.day ?? s.snapshot.split('/')[2];
      void fetch(`/api/research/${encodeURIComponent(s.rid)}/presentations/${encodeURIComponent(o.id ?? '')}?day=${encodeURIComponent(day)}`)
        .then(async (response) => {
          if (!response.ok) throw new Error(`The saved presentation is unavailable. Its findings remain in Data under ${sectionWords('writeup')}.`);
          const result = await response.json() as ResearchSession['result'];
          if (result?.findingRevision?.digest !== o.findingRevision?.digest || result?.presentation?.id !== o.id) throw new Error('The saved presentation identity did not match.');
          wrap.replaceChildren(renderResearchReport(result, undefined, this.captureOpener(s, result), this.capturePreview(s, result))); wrap.dataset.ready = 'true';
        }).catch((error: unknown) => { wrap.textContent = error instanceof Error ? error.message : 'Could not load the saved presentation.'; });
      return wrap;
    }
    if (kind === 'video' && o) {
      const wrap = document.createElement('figure');
      const video = document.createElement('video');
      this.mediaStatus(video, wrap, o.url);
      video.controls = true;
      video.muted = true; // Veo always adds generated sound (spike S6)
      video.autoplay = true;
      video.loop = true;
      video.playsInline = true;
      wrap.append(video, caption(o.caption));
      return wrap;
    }
    if (kind === 'image' && o) {
      const wrap = document.createElement('figure');
      const a = link(o.url, '', true);
      const img = document.createElement('img');
      this.mediaStatus(img, wrap, o.url);
      img.alt = o.caption;
      a.append(img);
      // What kind it is is said above it (kindNote); the caption says what is in it.
      const text = o.altered ? `The picture model may have changed the photo. ${o.caption}` : o.caption;
      wrap.append(a, caption(text));
      return wrap;
    }
    const basis = this.state.findingRevision;
    if (basis && basis.digest !== s.result?.findingRevision?.digest) {
      const wrap = document.createElement('div'); wrap.className = 'viewer-presentation'; wrap.textContent = 'Loading retained findings…';
      void fetch(`/api/research/${encodeURIComponent(s.rid)}/findings/${basis.version}?day=${encodeURIComponent(s.day ?? s.snapshot.split('/')[2])}`)
        .then(async response => {
          if (!response.ok) throw new Error('The exact saved findings are unavailable.');
          const record = await response.json() as {version:number; digest:string; complete:boolean; result:NonNullable<ResearchSession['result']>};
          if (record.digest !== basis.digest) throw new Error('Saved findings identity did not match.');
          wrap.replaceChildren(renderResearchReport({...record.result, findingRevision:basis}, undefined, this.captureOpener(s, {...record.result, findingRevision:basis}), this.capturePreview(s, {...record.result, findingRevision:basis}))); wrap.dataset.ready = 'true';
        }).catch((error:unknown) => {wrap.textContent = error instanceof Error ? error.message : 'Could not load retained findings.';});
      return wrap;
    }
    return renderResearchReport(s.result, undefined, this.captureOpener(s, s.result), this.capturePreview(s, s.result));
  }
}

function caption(text: string, extra?: HTMLElement): HTMLElement {
  const c = document.createElement('figcaption');
  c.textContent = text;
  if (extra) c.append(' ', extra);
  return c;
}

function link(url: string, text: string, external: boolean): HTMLAnchorElement {
  const a = document.createElement('a');
  a.href = url;
  if (external) {
    a.target = '_blank';
    a.rel = 'noopener noreferrer';
  }
  a.textContent = text;
  return a;
}
