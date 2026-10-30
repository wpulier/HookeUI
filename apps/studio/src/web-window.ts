/** A web page or a video from the web, in a sandboxed window of its own (orchestrator/web_window.py `show_web`;
 * docs/architecture/on-screen.md "Web pages and videos").
 *
 * Only a few hosts are framed (WEB_HOSTS; the server keeps the same list, web_hosts.py, and checks it first): YouTube's
 * privacy-enhanced embed, Vimeo's player, Wikipedia and Wikimedia Commons pages. The frame is sandboxed: its scripts run
 * in its own origin, never the app's; it cannot navigate the app, open popups or submit forms; and the window always
 * says where it is from, with the link. It opens as a document window on the Tab named, like the table window
 * (agent-table.ts), and answers with its placement at once; done is the screen report showing it there, its frame
 * loaded (a frame's inner pixels are never certified from its load event). Closing it stops it playing. */
import { studyArtifacts } from './workspace-study-artifacts';
import './web-window.css';

export type WebKind = 'video' | 'web-page';
export interface WebWindowItem {
  kind: WebKind; url: string; page: string; site: string; title: string;
  resource: { day: string; id: string; version: number | null };
}
export interface WebWindowRequest {
  type: 'web-window'; id: string; session: string; tab: string;
  workspacePin: { client: Record<string, unknown>; id: string; rootId: string; kind: string; lifetimeGeneration: string | number; sourceGeneration: number };
  item: WebWindowItem;
}
export type WebWindowResult = { ok: true; placement: string; project: string; tab: string } | { ok: false; error: string };

/** The frame addresses the studio loads: exactly these, by host and path (web_hosts.py `framed`). */
export const WEB_HOSTS: readonly { host: (h: string) => boolean; path: RegExp; kind: WebKind }[] = [
  { host: h => h === 'www.youtube-nocookie.com', path: /^\/embed\/[A-Za-z0-9_-]{11}$/, kind: 'video' },
  { host: h => h === 'player.vimeo.com', path: /^\/video\/\d{1,12}$/, kind: 'video' },
  { host: h => /^[a-z0-9-]+\.wikipedia\.org$/.test(h) || h === 'commons.wikimedia.org', path: /^\/wiki\/[^\s]+$/, kind: 'web-page' },
];
/** The sandbox a web frame gets: its own scripts in its own origin (a video player needs both), nothing more: no top
 * navigation, popups, forms or downloads. */
export const WEB_SANDBOX = 'allow-scripts allow-same-origin allow-presentation';

/** Whether `url` is a frame address the studio loads for `kind`: https, an allowed host, its path, no query tricks. */
export function allowedWeb(url: string, kind: WebKind): boolean {
  let parsed: URL;
  try { parsed = new URL(url); } catch { return false; }
  if (parsed.protocol !== 'https:' || parsed.username || parsed.password || parsed.port || parsed.search || parsed.hash) return false;
  return WEB_HOSTS.some(rule => rule.kind === kind && rule.host(parsed.hostname) && rule.path.test(parsed.pathname));
}
/** Whether `page` is a link fit to show beside it: an https address on the same site family (opened outside the app). */
function safeLink(page: string): string | null {
  try { const parsed = new URL(page); return parsed.protocol === 'https:' && !parsed.username && !parsed.password ? parsed.href : null; }
  catch { return null; }
}

export interface WebWindowDeps {
  current: () => boolean;
  where?: () => { project?: string; tab?: string; opening: boolean };
  open?: (item: WebWindowItem, content: HTMLElement, frame: HTMLIFrameElement) => HTMLElement;
}

/** The window's content: the sandboxed frame, then where it is from and its link. */
export function webContent(item: WebWindowItem): { content: HTMLElement; frame: HTMLIFrameElement } {
  const content = document.createElement('div'); content.className = 'web-window';
  const frame = document.createElement('iframe');
  frame.setAttribute('sandbox', WEB_SANDBOX);
  frame.setAttribute('allow', 'autoplay; encrypted-media; picture-in-picture; fullscreen');
  frame.setAttribute('referrerpolicy', 'strict-origin-when-cross-origin'); // YouTube's embed refuses a frame with no referrer
  frame.setAttribute('loading', 'eager'); frame.title = item.title || item.site; frame.className = 'web-window-frame';
  frame.src = item.url;
  const from = document.createElement('p'); from.className = 'web-window-source';
  from.append(`${item.kind === 'video' ? 'Video' : 'Web page'} from ${item.site}`);
  const link = safeLink(item.page);
  if (link) {
    const a = document.createElement('a'); a.href = link; a.target = '_blank'; a.rel = 'noopener noreferrer';
    a.textContent = 'Open on the web ↗'; a.className = 'web-window-link'; from.append(' · ', a);
  }
  content.append(frame, from);
  return { content, frame };
}

/** Opens the web window the request names on the Tab in front, or says why not. */
export function openWebWindow(message: WebWindowRequest, deps: WebWindowDeps): WebWindowResult {
  const where = deps.where ?? (() => ({ project: document.body.dataset.workspaceProject, tab: document.body.dataset.activeWorkspaceView,
    opening: document.body.dataset.workspaceOpening === 'true' }));
  try {
    const here = where(), item = message.item;
    if (!deps.current() || here.opening) return { ok: false, error: 'The Project’s workspace was opened again; ask in the Project open now.' };
    if (here.project !== message.session || item.resource.day !== message.session) return { ok: false, error: 'Another Project is open in this window.' };
    if (here.tab !== message.tab) return { ok: false, error: 'Another Tab is in front now.' };
    if (item.kind !== 'video' && item.kind !== 'web-page' || !allowedWeb(item.url, item.kind)) return { ok: false, error: 'That site is not one Hooke shows inside the app.' };
    const { content, frame } = webContent(item);
    const open = deps.open ?? ((shown: WebWindowItem, body: HTMLElement, iframe: HTMLIFrameElement) => {
      const title = (shown.title || shown.site).slice(0, 120);
      let root: HTMLElement | null = null;
      root = studyArtifacts().document(`web:${shown.url}`, title, body, () => { iframe.src = 'about:blank'; },
        { webWindow: JSON.stringify(shown), displayState: 'loading' }, {}, () => { iframe.src = 'about:blank'; if (root) root.dataset.displayState = 'closed'; });
      return root;
    });
    const root = open(item, content, frame);
    // The same page opened again on this Tab is its window brought back (studyArtifacts reuses it): its own frame is
    // loaded again if closing stopped it.
    const actual = root.querySelector<HTMLIFrameElement>('iframe.web-window-frame') ?? frame;
    if (actual !== frame || root.dataset.displayState === 'closed') {
      root.dataset.displayState = 'loading';
      if (actual.getAttribute('src') !== item.url) actual.src = item.url; else root.dataset.displayState = 'rendered';
    }
    actual.addEventListener('load', () => { if (actual.getAttribute('src') === item.url && root.dataset.displayState !== 'closed') root.dataset.displayState = 'rendered'; });
    actual.addEventListener('error', () => { root.dataset.displayState = 'failed'; });
    if (!root.dataset.placementId) return { ok: false, error: 'The window did not open on this Tab.' };
    return { ok: true, placement: root.dataset.placementId, project: message.session, tab: message.tab };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}
