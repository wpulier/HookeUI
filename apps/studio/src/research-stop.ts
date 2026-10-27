// Stop, said truthfully (2026-10-23 audit): pressing Stop sends `research-cancel`; until the server lists the
// researcher as stopped, every place that shows it says "Stop requested", never "Stopped". The request is remembered
// per browser tab so a redraw or a reconnect keeps the words; it clears itself once the researcher is no longer working.
import type { ClientCommand, ResearchSession } from './protocol';
import { isWorking } from './research-state';

const KEY = 'studio-stop-requested';
const pending = new Set<string>(read());
function read(): string[] {
  try { const saved = JSON.parse(sessionStorage.getItem(KEY) ?? '[]'); return Array.isArray(saved) ? saved.filter((v): v is string => typeof v === 'string') : []; } catch { return []; }
}
function save(): void { try { sessionStorage.setItem(KEY, JSON.stringify([...pending])); } catch { /* browser-local words only */ } }

/** Ask the server to stop this researcher and remember that it was asked. */
export function requestStop(rid: string, send: (command: ClientCommand) => unknown): void {
  pending.add(rid); save();
  void send({ type: 'research-cancel', rid });
}

/** Stop was requested and the server has not yet listed it as stopped. A researcher that is no longer working ends
 * the request (it stopped, or finished first). */
export function stopRequested(s: ResearchSession): boolean {
  if (!pending.has(s.rid)) return false;
  if (!isWorking(s)) { pending.delete(s.rid); save(); return false; }
  return true;
}

/** The button's words and state for a working researcher. */
export function stopButtonWords(s: ResearchSession): { text: string; disabled: boolean; title: string } {
  return stopRequested(s)
    ? { text: 'Stop requested', disabled: true, title: 'Asked to stop; it is Stopped once the server lists it so.' }
    : { text: 'Stop', disabled: false, title: `Stop ${s.name}. Work it already kept stays in this Project.` };
}
