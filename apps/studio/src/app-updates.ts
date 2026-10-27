import './app-updates.css';
import type { UpdateReadiness } from './restart-readiness';

/** Sparkle's state as the Mac app says it (packaging/macos/HookeUI/Updates.swift): an update on offer, or one already
 * downloaded and waiting for Hooke to restart (`downloadedVersion`: the person chose Later on the restart sheet). */
export interface AppUpdateState { currentVersion: string; availableVersion: string | null; downloadedVersion: string | null; error: string | null }
type NativeUpdates = {postMessage(message: {op: 'status' | 'check'}): Promise<unknown>};
export function updateState(value: unknown): AppUpdateState {
  if (!value || typeof value !== 'object') throw new Error('Update status unavailable.');
  const v = value as Record<string, unknown>;
  if (typeof v.currentVersion !== 'string' || !(v.availableVersion === null || typeof v.availableVersion === 'string') ||
      !(v.downloadedVersion === undefined || v.downloadedVersion === null || typeof v.downloadedVersion === 'string') ||
      !(v.error === null || typeof v.error === 'string')) throw new Error('Update status unavailable.');
  return {currentVersion:v.currentVersion, availableVersion:v.availableVersion, downloadedVersion:typeof v.downloadedVersion==='string'?v.downloadedVersion:null, error:v.error};
}

/** What the rail reminder, its tooltip, Settings' status line and its button say for a state. */
export function updateWords(s: AppUpdateState): { reminder: string | null; title: string; status: string; button: string } {
  if (s.downloadedVersion) {
    const waiting = `Hooke ${s.downloadedVersion} is downloaded. Restart Hooke to finish updating.`;
    return { reminder: 'Restart to update', title: waiting, status: s.error ?? waiting, button: 'Restart to update…' };
  }
  if (s.availableVersion) return { reminder: '↑ Update', title: `Hooke ${s.availableVersion} is available. Review before installing.`,
    status: s.error ?? `Hooke ${s.availableVersion} is available · installed ${s.currentVersion}`, button: 'Review update…' };
  return { reminder: null, title: '', status: s.error ?? `Installed version ${s.currentVersion}. Updates are checked periodically.`, button: 'Check for updates…' };
}

type UpdateHost = Window & {webkit?: {messageHandlers?: {hookeUpdates?: NativeUpdates}}; hookeUpdateReadiness?: () => Promise<UpdateReadiness>};
/** The signed Mac app's Sparkle bridge; a source checkout in a browser has none. */
const nativeUpdates = (host: UpdateHost): NativeUpdates | undefined => host.webkit?.messageHandlers?.hookeUpdates;

/** How this copy is really updated: the signed app through Sparkle's Check for Updates, a source checkout through its
 *  checkout (F-L8-5: the packaged app's Hooke menu said "Updates through your source checkout"). */
export function updatePathWords(host: Window = window): string {
  return nativeUpdates(host as UpdateHost) ? 'Updates through Check for Updates… in Settings or the HookeUI menu'
    : 'Updates through your source checkout';
}

/** Only the signed native app can offer installation; a browser has no fake updater. `readiness` is what restarting or
 *  quitting would stop (restart-readiness.ts): the Mac app reads it before its sheet, and a rejection means it can't
 *  tell, never that nothing is running. */
export function mountAppUpdates(utilities: HTMLElement, settings: HTMLElement, readiness: () => Promise<UpdateReadiness>, anchor: HTMLElement | null = null) {
  const host = window as UpdateHost;
  const native = nativeUpdates(host);
  if (!native) return;
  host.hookeUpdateReadiness = () => readiness();
  const reminder = document.createElement('button'); reminder.type='button'; reminder.className='app-update-reminder'; reminder.hidden=true;
  reminder.textContent='↑ Update'; reminder.setAttribute('aria-label','Update available — review update');
  const section=document.createElement('section');section.className='app-update-settings';section.setAttribute('aria-label','App updates');
  const heading=document.createElement('h3');heading.textContent='Hooke updates';
  const status=document.createElement('p');status.setAttribute('role','status');status.textContent='Checking update status…';
  const detail=document.createElement('p');detail.textContent='Hooke downloads an update when you choose and restarts only when you say so. Before it restarts it names what is running or unsaved. Your Projects and data are kept.';
  const check=document.createElement('button');check.type='button';check.textContent='Check for updates…';
  const render=(raw:unknown)=>{const s=updateState(raw),words=updateWords(s);reminder.hidden=!words.reminder;
    reminder.textContent=words.reminder??'↑ Update';reminder.title=words.title;
    reminder.setAttribute('aria-label',s.downloadedVersion?words.title:'Update available — review update');
    status.textContent=words.status;check.textContent=words.button;};
  let pending=false;
  const review=async()=>{if(pending)return;pending=true;check.disabled=true;reminder.disabled=true;
    try{render(await native.postMessage({op:'check'}));}catch{status.textContent='Could not open updates. Use HookeUI → Check for Updates… in the Mac menu bar.';}
    finally{pending=false;check.disabled=false;reminder.disabled=false;}};
  check.onclick=()=>void review();reminder.onclick=()=>void review();
  section.append(heading,status,check,detail);settings.prepend(section);utilities.insertBefore(reminder,anchor);
  window.addEventListener('hooke-update-state',event=>{try{render((event as CustomEvent).detail);}catch{status.textContent='Update status unavailable. Try Check for updates…';}});
  void native.postMessage({op:'status'}).then(render).catch(()=>{status.textContent='Update status unavailable. Try Check for updates…';});
}
