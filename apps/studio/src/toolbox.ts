import type { ModelRow, Readiness } from './protocol';
import { runnableWords } from './run-spec';

/** One area, two complete views. Legacy links remain aliases so original-client page acknowledgements stay exact. */
export function toolboxTarget(hash: string): { tab: 'tools' | 'all'; id: string | null } | null {
  if (hash === '#capabilities' || hash === '#toolbox/all') return { tab: 'all', id: null };
  const found = hash.match(/^#(?:models|toolbox)(?:\/([^?#]+))?$/);
  if (!found) return null;
  if (!found[1]) return { tab: 'tools', id: null };
  try {
    const id = decodeURIComponent(found[1]);
    return /^[\w./-]+$/.test(id) ? { tab: 'tools', id } : null;
  } catch { return null; } // Malformed links never interrupt workspace navigation.
}

export function toolboxTabs(active: 'tools' | 'all'): HTMLElement {
  const nav = document.createElement('nav');
  nav.className = 'toolbox-tabs'; nav.setAttribute('aria-label', 'Toolbox views');
  for (const [id, title, href] of [['tools', 'Toolbox', '#toolbox'], ['all', 'Everything it can do', '#toolbox/all']]) {
    const link = document.createElement('a'); link.textContent = title; link.setAttribute('href', href);
    link.setAttribute('aria-current', id === active ? 'page' : 'false'); nav.append(link);
  }
  return nav;
}

/** One frame for both views, in one order: the tabs, the shared band (keys, Add a capability), the page's header, then
 * its body. Both pages lay it out with the same width, padding and gutter (modelspage.css "The Toolbox frame"), so a
 * switch between them moves nothing above the body (Will, 2026-10-15: the tabs jumped on every switch). */
export function toolboxFrame(active: 'tools' | 'all', band: HTMLElement, header: HTMLElement, ...body: (HTMLElement | null)[]): HTMLElement {
  const frame = document.createElement('div');
  frame.className = 'toolbox-frame'; frame.dataset.toolboxView = active;
  frame.append(toolboxTabs(active), band, header, ...body.filter((b): b is HTMLElement => !!b));
  return frame;
}

/** The Fiji list's counts (GET /api/catalog/inventories/fiji): extensions by how they install, and `bound`, the Fiji
 * functions connected to the app by a reviewed binding (inventories.bound). Connected is not ready: whether one runs here
 * is its own row's readiness on the capabilities wire (catalog_view.py from readiness.py, ADR 0019), never said here. */
export interface FijiCounts { listed: number; routes: Record<string, number>; bound?: { id: string; name: string }[] }

/** Whether a catalog entry is ready on this Mac, from its own row on the capabilities wire (`models`: catalog_view.py's
 * rows, readiness.py's status): the one readiness source. No row (the wire not here yet) is not ready. */
export function rowReady(rows: readonly Pick<ModelRow, 'id' | 'status' | 'execution'>[] | undefined, id: string): boolean {
  const row = rows?.find((r) => r.id === id);
  return row?.status === 'ready' && !row.execution;
}

/** Fiji in two honest lines, the same on both views: the functions connected and how many are ready here ("1 Fiji
 * function connected · 1 ready here", or "· set up Fiji to use it" while Fiji itself isn't, "· not ready on this Mac"
 * while it is but the function isn't proven), then "555 extensions listed: 209 install in Fiji, 346 have no install route
 * yet". Never that a function runs where its row isn't ready; what installs is never said to be usable (Will, 2026-10-15). */
export function fijiWords(c: FijiCounts, rows: readonly Pick<ModelRow, 'id' | 'status' | 'execution'>[] | undefined): { runs: string; listed: string } {
  const none = c.routes['none-known'] ?? 0, bound = c.bound ?? [], n = bound.length;
  const ready = bound.filter((b) => rowReady(rows, b.id)).length;
  const connected = `${n} Fiji function${n === 1 ? '' : 's'} connected`;
  const state = ready ? `${ready} ready here` : rowReady(rows, 'fiji') ? 'not ready on this Mac' : `set up Fiji to use ${n === 1 ? 'it' : 'them'}`;
  return { runs: n ? `${connected} · ${state}` : 'No Fiji function connected yet',
    listed: `${c.listed} extensions listed: ${c.listed - none} install in Fiji${none ? `, ${none} have no install route yet` : ''}` };
}

/** A readiness as one sentence, the server's own (readiness.said / said_wire, ADR 0019): "Ready", "Needs setup:
 * Download 1.2 GB", "Unavailable here: Not on this Mac (it runs on …)", "Broken: Its last check failed: …". Every
 * surface that says a status in words says this, never "not available" for something that only needs setup (L7b). */
export function readinessSaid(readiness: Pick<Readiness, 'status' | 'word' | 'next'> | null | undefined, why = '',
  entry?: ModelRow): string {
  if (!readiness) return why ? `Unavailable here: ${why}` : 'Ready';
  const { status } = readiness;
  if (status === 'ready') return runnableWords(entry); // Ready only once Hooke has checked it here, as Models says
  const group = status === 'broken' ? 'Broken' : status === 'needs-setup' ? 'Needs setup' : 'Unavailable here';
  const word = status === 'broken' && readiness.next?.says ? readiness.next.says : readiness.word;
  let detail = word && word !== group && word !== 'Broken' ? word : '';
  if (why && why !== detail && !detail.includes(why)) detail = detail ? `${detail} (${why})` : why;
  return detail ? `${group}: ${detail}` : group;
}
