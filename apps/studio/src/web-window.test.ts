// A web page or video from the web (web-window.ts; orchestrator/web_window.py, web_hosts.py): only an allowed host's
// framed address is loaded, in a sandbox with no way to navigate the app, and the window says where it is from with
// the link. "Find a YouTube video about stomata and open it" (will-display, step 6).
import { afterEach, describe, expect, it, vi } from 'vitest';
vi.mock('./workspace-study-artifacts', () => ({ studyArtifacts: () => ({}) }));
import { allowedWeb, openWebWindow, webContent, WEB_SANDBOX, type WebWindowRequest } from './web-window';
import { syntheticDOM } from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());

const EMBED = 'https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ';
const request = (item: Partial<WebWindowRequest['item']> = {}, fields: Partial<WebWindowRequest> = {}): WebWindowRequest => ({
  type: 'web-window', id: 'w1', session: '2026-10-29', tab: 'main',
  workspacePin: { client: {}, id: 'workspace-tabs', rootId: 'tabs', kind: 'panel', lifetimeGeneration: 'l', sourceGeneration: 1 },
  item: { kind: 'video', url: EMBED, page: 'https://www.youtube.com/watch?v=dQw4w9WgXcQ', site: 'youtube.com', title: 'How stomata open',
    resource: { day: '2026-10-29', id: 'd4', version: null }, ...item }, ...fields });
const here = (tab = 'main', project = '2026-10-29') => () => ({ project, tab, opening: false });

describe('only the allowed hosts are framed, exactly as their embeds', () => {
  it('takes YouTube\'s privacy-enhanced embed, Vimeo\'s player and Wikipedia pages', () => {
    expect(allowedWeb(EMBED, 'video')).toBe(true);
    expect(allowedWeb('https://player.vimeo.com/video/76979871', 'video')).toBe(true);
    expect(allowedWeb('https://en.wikipedia.org/wiki/Stoma', 'web-page')).toBe(true);
    expect(allowedWeb('https://commons.wikimedia.org/wiki/File:Stoma.jpg', 'web-page')).toBe(true);
  });
  it('refuses every other address: another host, a watch page, a query, http, a port, a script, the wrong kind', () => {
    for (const [url, kind] of [
      ['https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'video'], ['https://evil.example/embed/dQw4w9WgXcQ', 'video'],
      [`${EMBED}?autoplay=1`, 'video'], ['http://www.youtube-nocookie.com/embed/dQw4w9WgXcQ', 'video'],
      ['https://www.youtube-nocookie.com:8443/embed/dQw4w9WgXcQ', 'video'], ['javascript:alert(1)', 'web-page'],
      ['https://en.wikipedia.org.evil.example/wiki/Stoma', 'web-page'], [EMBED, 'web-page'],
      ['https://en.wikipedia.org/w/index.php', 'web-page'], ['https://user@en.wikipedia.org/wiki/Stoma', 'web-page'],
    ] as const) expect(allowedWeb(url, kind), url).toBe(false);
  });
});

describe('the window is sandboxed and says where it is from', () => {
  it('runs the frame\'s scripts only in its own origin, never navigating the app, opening popups or sending forms', () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const { content, frame } = webContent(request().item);
    expect(frame.getAttribute('sandbox')).toBe(WEB_SANDBOX);
    for (const never of ['allow-top-navigation', 'allow-popups', 'allow-forms', 'allow-downloads', 'allow-modals'])
      expect(WEB_SANDBOX).not.toContain(never);
    expect(frame.getAttribute('referrerpolicy')).toBe('strict-origin-when-cross-origin'); // YouTube refuses a frame without one
    expect(frame.src).toBe(EMBED);
    const from = content.querySelector('.web-window-source')!;
    expect(from.textContent).toContain('Video from youtube.com');
    const link = from.querySelector('a')! as unknown as HTMLAnchorElement;
    expect(link.href).toBe('https://www.youtube.com/watch?v=dQw4w9WgXcQ');
    expect(link.target).toBe('_blank'); expect(link.rel).toBe('noopener noreferrer');
  });
});

describe('the Assistant opens it on the Tab named, and answers with its placement or why not', () => {
  const opened = () => ({ dataset: { placementId: 'p9' } as Record<string, string>, querySelector: () => null }) as unknown as HTMLElement;
  it('opens it on the Tab in front and names its placement', () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const open = vi.fn(opened);
    const result = openWebWindow(request(), { current: () => true, where: here(), open });
    expect(open).toHaveBeenCalledOnce();
    expect(result).toEqual({ ok: true, placement: 'p9', project: '2026-10-29', tab: 'main' });
  });
  it('opens nothing for a host it does not frame, another Tab, another Project or another opening', () => {
    const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
    const open = vi.fn(opened);
    expect(openWebWindow(request({ url: 'https://evil.example/embed/x' }), { current: () => true, where: here(), open }))
      .toEqual({ ok: false, error: 'That site is not one Hooke shows inside the app.' });
    expect(openWebWindow(request({ kind: 'web-page' }), { current: () => true, where: here(), open }).ok).toBe(false);
    expect(openWebWindow(request(), { current: () => true, where: here('tab-2'), open }))
      .toEqual({ ok: false, error: 'Another Tab is in front now.' });
    expect(openWebWindow(request(), { current: () => true, where: here('main', 'other'), open }).ok).toBe(false);
    expect(openWebWindow(request(), { current: () => false, where: here(), open }).ok).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
});
