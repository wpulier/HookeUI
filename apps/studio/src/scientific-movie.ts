/** Export an accepted revision; this component never borrows the live scientific session. */
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => {
  const element = document.createElement(tag); element.textContent = text; return element;
};
const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
type Source = { id: string; day: string; version: number };
/** Where the movie's camera keyframes are edited: napari-animation's own panel, never this export. */
export const KEYFRAME_WORDS = 'Keyframes are made and edited in napari-animation, not here: its keyframe panel is in Tool while its session is open. '
  + 'If this window uses another tool, open it from Tool › Continue with this window’s saved input › Find interactive tools › Continue with napari-animation, then Open napari-animation and Open session. '
  + 'Save tool and all layers keeps the keyframes; export from that saved document.';

export function savedMovieExport(root: HTMLElement, source: Source): {cancel():void;dispose():void} {
  const section = node('section'), status = node('p'), start = node('button', 'Export saved movie'), cancel = node('button', 'Cancel'), result = node('div');
  section.className = 'scientific-movie-export';
  status.setAttribute('role', 'status'); cancel.hidden = true; cancel.setAttribute('aria-label', 'Cancel movie export');
  const actions = node('div'), details = node('details'); actions.className = 'scientific-movie-actions';
  actions.append(result, start, cancel);
  details.append(node('summary', 'Format and saved changes'),
    node('p', 'Uncompressed MOV · 512 × 384 · 10 frames/second. Exact rendered display pixels; larger files. Original scientific values stay in the saved document.'),
    node('p', 'Save tool and all layers, then open that document to include your latest changes. Export uses a separate session.'));
  const keyframes = node('p', KEYFRAME_WORDS); keyframes.className = 'scientific-movie-keyframes';
  section.append(node('h4', 'Movie from saved work'), node('p', `Exports saved revision ${source.version}. Unsaved changes are excluded.`), keyframes, actions, status, details);
  root.append(section);
  const request = crypto.randomUUID(); let controller: AbortController | null = null, disposed = false;
  const abort = () => { controller?.abort(); if(controller) status.textContent = 'Cancellation requested. Check Project Data if the export was already accepted.'; };
  cancel.addEventListener('click', abort);
  start.addEventListener('click', () => { void (async () => {
    if (controller || disposed) return;
    const active = controller = new AbortController(); start.disabled = true; cancel.hidden = false;
    status.textContent = `Rendering saved revision ${source.version}, encoding and verifying the movie…`;
    try {
      const response = await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/movie`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'export-scientific-movie' }, signal: active.signal,
        body: JSON.stringify({ session: source.day, revision: source.version, request, settings: { format: 'mov-argb-v1', width: 512, height: 384, fps: 10 } }),
      });
      const value: unknown = await response.json();
      if (!object(value) || !response.ok || value.ok !== true) throw new Error(object(value) ? `${String(value.says ?? 'Movie export failed.')} ${String(value.next ?? '')}` : 'Movie export returned an invalid response.');
      const project = value.project, document = object(project) ? project.document : null, state = object(document) ? document.state : null, movie = object(state) ? state.movieExport : null;
      if (!object(project) || typeof project.id !== 'string' || !/^p[0-9a-f]{32}$/.test(project.id) || project.session !== source.day || project.revision !== 1
          || !object(document) || document.kind !== 'source-file' || !validMovieFile(object(state) ? state.sourceFile : null, {id:project.id,session:source.day}, document.resources)
          || !validMovie(movie) || movie.settings.format !== 'mov-argb-v1' || movie.settings.width !== 512 || movie.settings.height !== 384 || movie.settings.fps !== 10
          || movie.source.id !== source.id || movie.source.session !== source.day || movie.source.revision !== source.version)
        throw new Error('The export receipt differs from the selected saved revision. Check Project Data before retrying.');
      if (disposed) return;
      const download = node('a', 'Download rendered movie');
      download.href = `/api/scientific-files/${encodeURIComponent(project.id)}?${new URLSearchParams({ session: source.day, revision: '1' })}`;
      download.download = 'Rendered movie.mov'; result.replaceChildren(download);
      status.textContent = `Rendered movie saved in Project Data${value.sessionChanged ? ' in the original Project' : ''}. Your editable tool and other work are unchanged.`;
      const initiatedHere = document.activeElement === start;
      start.textContent = 'Check saved export'; details.append(start);
      if (initiatedHere) download.focus();
    } catch (error) {
      if (!disposed) status.textContent = active.signal.aborted ? 'Export cancelled or response interrupted. Retry to recover this request’s accepted result, or check Project Data.' : String(error instanceof Error ? error.message : error);
    } finally {
      if (controller === active) controller = null;
      start.disabled = false; cancel.hidden = true;
    }
  })(); });
  return {cancel:abort,dispose:()=>{ disposed = true; controller?.abort(); }};
}

const hex = (value:unknown) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const integer = (value:unknown, maximum:number) => typeof value === 'number' && Number.isInteger(value) && value >= 1 && value <= maximum;
type Movie = {format:string;derivedDisplay:true;settings:{format:string;width:number;height:number;fps:number};sequence:{format:string;name:string;count:number};source:{id:string;session:string;revision:number;digest:string}};
function validMovie(value:unknown): value is Movie {
  if (!object(value) || value.format !== 'scientific-movie-export-v1' || value.derivedDisplay !== true
      || !object(value.settings) || !object(value.sequence) || !object(value.source) || !object(value.encoder) || !object(value.runtime)) return false;
  const {settings:s,sequence:q,source:r,encoder:e,runtime:t}=value;
  return ((s.format === 'mov-qtrle-v1' && e.codec === 'qtrle') || (s.format === 'mov-argb-v1' && e.codec === 'rawvideo')) && integer(s.width,1024) && integer(s.height,1024) && integer(s.fps,60)
    && q.format === 'scientific-frame-sequence-v1' && typeof q.name === 'string' && /^[a-z][a-z0-9.-]{0,79}$/.test(q.name) && integer(q.count,4096)
    && typeof r.id === 'string' && /^p[a-f0-9]{32}$/.test(r.id) && typeof r.session === 'string' && r.session.length>0 && integer(r.revision,Number.MAX_SAFE_INTEGER) && hex(r.digest)
    && hex(e.sha256) && hex(value.rgbaSha256) && Array.isArray(value.inputs)
    && typeof t.bundle === 'string' && typeof t.operation === 'string' && typeof t.generation === 'string' && /^[a-f0-9]{16}$/.test(t.generation);
}
export function validMovieFile(value:unknown, owner:{id:string;session:string}, resources:unknown):boolean {
  if (!object(value) || value.format!==1 || typeof value.name!=='string' || !value.name || !integer(value.size,256*1024*1024) || !hex(value.sha256) || !object(value.reference) || !Array.isArray(resources)) return false;
  const ref=value.reference;
  return ref.id===owner.id && ref.session===owner.session && ref.component==='file.source' && hex(ref.revision)
    && Object.keys(ref).length===4 && resources.some(item=>object(item) && Object.keys(item).length===4 && Object.keys(ref).every(key=>item[key]===ref[key]));
}

export function renderedMovieDescription(root: HTMLElement, value: unknown): boolean {
  if (value === undefined) return false;
  if (!validMovie(value))
    throw new Error('This movie provenance version is unsupported. Keep its saved bytes and use a compatible Hooke version.');
  root.append(node('p', `Rendered movie · ${String(value.sequence.count)} frames · ${String(value.settings.width)} × ${String(value.settings.height)} · ${String(value.settings.fps)} frames/second.`),
    node('p', `Created from saved revision ${String(value.source.revision)}. These are display pixels, not native scientific measurements. The original tool and arrays are retained separately.`));
  const details = node('details'); details.append(node('summary', 'Movie provenance'), node('p', `Sequence: ${String(value.sequence.name)}. Codec: ${value.settings.format === 'mov-argb-v1' ? 'uncompressed ARGB' : 'lossless qtrle (legacy)' }. Input: ${String(value.source.id)} · ${String(value.source.digest)}.`)); root.append(details);
  return true;
}
