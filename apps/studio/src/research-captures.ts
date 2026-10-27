import type { ResearchCaptureTarget } from './protocol';
/** App-retained capture metadata. No pixel acquisition or inferred measurements. */
export interface ResearchCapture {
  title: string;
  kind: 'clip' | 'tracks' | 'population';
  state: string;
  summary: string;
  at: number | null;
  source: {day:string; id:string; version:number|null; contentRevision:string; basisVersion?:number; basisDigest?:string};
  frames: {picture:number; frame?:number; seq?:number|null; boot?:string; bootId?:string; at?:number; sha256?:string}[];
}

export function researchCaptureImageUrl(target:ResearchCaptureTarget): string {
  return `/api/research/${encodeURIComponent(target.rid)}/findings/${target.findingVersion}/captures/${target.sequence}/${target.picture}?day=${encodeURIComponent(target.day)}&digest=${encodeURIComponent(target.findingDigest)}`;
}

export function renderResearchCaptures(captures: ResearchCapture[], select?: (anchor:string)=>void,
  openCapture?: (sequence:number,picture:number)=>void|Promise<void>, preview?: (sequence:number,picture:number)=>string): HTMLElement[] {
  return captures.map((capture, sequence) => {
    const section = document.createElement('section'); section.className = 'research-capture'; section.dataset.anchor = `capture:${sequence}`;
    const heading = document.createElement('h3'); heading.textContent = capture.title;
    const kind = document.createElement('p'); kind.className = 'sources';
    kind.textContent = `Captured view · ${capture.state} · ${capture.frames.length} retained frames`;
    const summary = document.createElement('p'); summary.textContent = capture.summary;
    section.append(heading, kind, summary);
    if (capture.frames.length) {
      const controls = document.createElement('div'); controls.className = 'research-capture-controls';
      const frames = document.createElement('select'); frames.className = 'mm-control'; frames.setAttribute('aria-label', `Frame from ${capture.title}`);
      for (const frame of capture.frames) {
        const option = document.createElement('option'); option.value = String(frame.picture);
        option.textContent = `Frame ${frame.picture+1}${frame.at == null ? '' : ` · ${new Date(frame.at*1000).toLocaleTimeString()}`}${frame.seq == null ? '' : ` · camera ${frame.seq}`}`;
        frames.append(option);
      }
      frames.value = String(capture.frames[0].picture);
      if (preview) {
        const figure = document.createElement('figure'); figure.className = 'research-capture-preview';
        const image = document.createElement('img'); image.alt = `${capture.title} · original captured frame`; image.loading = 'lazy';
        const note = document.createElement('figcaption'); note.textContent = capture.kind === 'tracks' ? 'Captured view · tracks drawn by the app' : 'Captured view';
        image.addEventListener('error', () => {image.hidden = true; note.textContent = 'Original preview unavailable. Findings and source identities remain available.';});
        const show = () => {image.hidden = false; note.textContent = capture.kind === 'tracks' ? 'Captured view · tracks drawn by the app' : 'Captured view'; image.src = preview(sequence,Number(frames.value));};
        frames.addEventListener('change',show); show(); figure.append(image,note); section.append(figure);
      }
      const open = document.createElement('button'); open.type = 'button'; open.className = 'mm-control';
      open.textContent = 'Open captured view'; open.dataset.captureOpen = ''; open.disabled = !openCapture;
      const status = document.createElement('p'); status.setAttribute('role','status'); status.hidden = true;
      open.addEventListener('click', async () => {
        const picture = Number(frames.value);
        if (!openCapture || open.disabled || !capture.frames.some(frame => frame.picture === picture)) return;
        open.disabled = true; status.hidden = false; status.textContent = 'Opening the original captured view…';
        try { await openCapture(sequence,picture); status.hidden = true; }
        catch (error) { status.textContent = error instanceof Error ? error.message : 'The original capture could not be opened. Findings remain available.'; }
        finally { open.disabled = false; }
      });
      controls.append(frames, open); section.append(controls,status);
    } else {
      const missing = document.createElement('p'); missing.textContent = 'Original frames unavailable. No replacement is acquired.'; section.append(missing);
    }
    const provenance = document.createElement('details');
    const label = document.createElement('summary'); label.textContent = 'Original source and frame identities';
    const facts = document.createElement('pre'); facts.textContent = JSON.stringify({source:capture.source,frames:capture.frames},null,2);
    provenance.append(label,facts); section.append(provenance);
    if (select) {
      const ask = document.createElement('button'); ask.type = 'button'; ask.className = 'mm-control';
      ask.textContent = 'Ask about this sequence'; ask.dataset.captureSelect = '';
      ask.addEventListener('click', () => select(`capture:${sequence}`)); section.append(ask);
    }
    return section;
  });
}
