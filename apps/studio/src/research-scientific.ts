/** Private scientific staging is explicit transport, separate from provider admission and execution. */
import { StudyRefused } from './research-efforts';

export interface ScientificPreparation {
  request?: string; status: 'staging' | 'staged' | 'interrupted' | 'failed' | 'stopping' | 'stopped';
  completedInputs: number; totalInputs: number; problem?: string | null;
}
export interface ScientificStaging {
  ready: boolean; reason?: string | null; project: string; region: string;
  preparation: ScientificPreparation | null; inputTitles: string[];
}
const url = (id: string) => `/api/research-efforts/${encodeURIComponent(id)}/scientific`;
async function reply(response: Response): Promise<ScientificStaging> {
  const value = await response.json().catch(() => ({}));
  if (!response.ok || value.ok !== true) throw new StudyRefused(value.says ?? value.error ?? 'Scientific preparation could not be read.', value.next ?? null);
  return value as ScientificStaging;
}
export async function readScientificStaging(id: string, session: string, fetcher: typeof fetch = fetch): Promise<ScientificStaging> {
  return reply(await fetcher(`${url(id)}?session=${encodeURIComponent(session)}`, { cache: 'no-store' }));
}
export async function stageScientificInputs(id: string, session: string, request: string, fetcher: typeof fetch = fetch): Promise<ScientificStaging> {
  return reply(await fetcher(`${url(id)}/stage`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'research-effort' }, body: JSON.stringify({ session, request }) }));
}
export async function stopScientificStaging(id: string, session: string, fetcher: typeof fetch = fetch): Promise<ScientificStaging> {
  return reply(await fetcher(`${url(id)}/stop-staging`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-MicroscopeMind': 'research-effort' }, body: JSON.stringify({ session }) }));
}
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text = '') => { const element = document.createElement(tag); element.textContent = text; return element; };
/** GET-only observation while open; disposal/current checks retire every stale callback and never stop server work. */
export function mountScientificStaging(root: HTMLElement, owner: { id: string; session: string; current(): boolean }, fetcher: typeof fetch = fetch): () => void {
  const section = node('section'); section.className = 'study-view-start study-scientific-staging';
  const heading = node('h3', 'Copy large inputs to Google Cloud'), destination = node('p'), inputs = node('p'), status = node('p', 'Checking what has been copied…'); status.setAttribute('role', 'status');
  const progress = node('progress'); progress.hidden = true; progress.setAttribute('aria-label', 'Scientific inputs copied');
  const note = node('p', 'This step copies data only, into this Study’s private storage. It starts no researcher, model or GPU job; you start each experiment yourself.'); note.className = 'study-view-empty';
  const stage = node('button', 'Copy to private Google Cloud storage'), stop = node('button', 'Stop copying'), refresh = node('button', 'Check again');
  stage.type = stop.type = refresh.type = 'button'; stage.disabled = true; stop.hidden = true;
  refresh.setAttribute('aria-label', 'Check the copy again');
  section.append(heading, destination, inputs, status, progress, note, stage, stop, refresh); root.append(section);
  let state: ScientificStaging | null = null, disposed = false, pending = false, generation = 0, timer: ReturnType<typeof setTimeout> | null = null;
  let request: string = crypto.randomUUID(), submitted = false, terminalObserved = false;
  const current = () => !disposed && owner.current();
  const render = (value: ScientificStaging, acknowledged = true) => {
    if (!current()) return;
    const newlyStaged = value.preparation?.status === 'staged' && state?.preparation?.status !== 'staged';
    if(acknowledged){
      if(!submitted&&value.preparation?.request)request=value.preparation.request;
      terminalObserved=!!value.preparation&&['failed','interrupted','stopped'].includes(value.preparation.status)&&(!submitted||value.preparation.request===request);
    }
    state = value; root.dispatchEvent(new Event('study-preparation-changed'));
    destination.textContent = `Private storage: ${value.project} · ${value.region}${value.region.startsWith('us') ? ' (US)' : ''}`;
    inputs.textContent = value.inputTitles.length ? `To copy: ${value.inputTitles.join(' · ')}` : 'This Study has no large inputs to copy.';
    const preparation = value.preparation, active = preparation?.status === 'staging' || preparation?.status === 'stopping';
    const words = { staging: 'Copying', staged: 'Copied privately', interrupted: 'Copy interrupted', failed: 'Copy failed', stopping: 'Stop requested · waiting for the current part to finish', stopped: 'Copy stopped' };
    status.textContent = preparation ? `${words[preparation.status]} · ${preparation.completedInputs} of ${preparation.totalInputs} inputs${preparation.problem ? `. ${preparation.problem}` : ''}` : value.reason || 'Not copied yet.';
    if (!value.ready && preparation) status.textContent += ` · ${value.reason || 'Copying isn’t allowed here yet.'}`;
    progress.hidden = !active;
    if (preparation && preparation.totalInputs > 0 && preparation.completedInputs > 0) { progress.max = preparation.totalInputs; progress.value = preparation.completedInputs; }
    else progress.removeAttribute('value');
    stage.disabled = pending || !value.ready || !value.inputTitles.length || active || preparation?.status === 'staged';
    stage.textContent = preparation && ['failed', 'interrupted', 'stopped'].includes(preparation.status) ? 'Try copying again' : 'Copy to private Google Cloud storage';
    stop.hidden = !active; stop.disabled = pending || preparation?.status === 'stopping'; refresh.disabled = pending;
    // Nothing to copy and nothing copied: one line, not a disabled button and its explanation.
    const nothing = !value.inputTitles.length && !preparation; section.dataset.nothing = String(nothing);
    for (const item of [status, note, stage, refresh, destination]) item.hidden = nothing;
    if (timer) clearTimeout(timer);
    if (active) timer = setTimeout(() => { void read(); }, 4000);
    if (newlyStaged) root.dispatchEvent(new Event('study-scientific-staged'));
  };
  const error = (cause: unknown) => {
    if (!current()) return;
    status.textContent = cause instanceof StudyRefused ? `${cause.message}${cause.next ? ` Next: ${cause.next}` : ''}` : cause instanceof Error ? cause.message : String(cause);
    refresh.disabled = false;
  };
  const read = async () => { if (!current() || pending) return; const sequence = ++generation; try { const value = await readScientificStaging(owner.id, owner.session, fetcher); if (sequence === generation && !pending) render(value); } catch (cause) { if (sequence === generation) error(cause); } };
  const mutate = async (operation: () => Promise<ScientificStaging>) => {
    if (!current() || pending) return;
    ++generation; pending = true; stage.disabled = stop.disabled = refresh.disabled = true; section.dataset.pending = 'true';
    status.textContent = 'Waiting for Google Cloud to confirm…';
    try { const value = await operation(); pending = false; render(value); }
    catch (cause) { pending = false; if (state) render(state, false); error(cause); }
    finally { pending = false; delete section.dataset.pending; }
  };
  stage.addEventListener('click', () => { if (stage.disabled) return; if(terminalObserved)request=crypto.randomUUID();terminalObserved=false;submitted=true;void mutate(() => stageScientificInputs(owner.id, owner.session, request, fetcher)); });
  stop.addEventListener('click', () => { if (stop.disabled) return; void mutate(() => stopScientificStaging(owner.id, owner.session, fetcher)); });
  refresh.addEventListener('click', () => { void read(); });
  void read();
  return () => { disposed = true; if (timer) clearTimeout(timer); section.remove(); };
}
