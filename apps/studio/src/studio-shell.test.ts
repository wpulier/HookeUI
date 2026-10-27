import { afterEach, expect, test, vi } from 'vitest';
import { resetStudioRouteScroll, STUDIO_ROUTE, workspaceConnectionNotice } from './studio-shell';

afterEach(() => vi.unstubAllGlobals());

function browser(hash: string) {
  const location = { hash };
  const frames: FrameRequestCallback[] = [];
  const scrollTo = vi.fn();
  vi.stubGlobal('location', location);
  vi.stubGlobal('window', { scrollTo });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
  return { location, scrollTo, paint: () => frames.splice(0).forEach(callback => callback(0)) };
}

test.each(['#research/r1', '#research/study-2?day=2026-10-10&findings=3'])('Research owns reading-position restoration on %s', hash => {
  const page = browser(hash);
  resetStudioRouteScroll(); // shell's hashchange listener runs before Research restores its scroll
  page.scrollTo({ top: 600 });
  page.paint();
  expect(page.scrollTo.mock.calls).toEqual([[{ top: 600 }]]);
});

test('an overview reset queued before entering a research detail cannot overwrite its restoration', () => {
  const page = browser('#research');
  resetStudioRouteScroll();
  page.location.hash = '#research/r1';
  resetStudioRouteScroll();
  page.scrollTo({ top: 600 });
  page.paint();
  expect(page.scrollTo.mock.calls).toEqual([[{ top: 600 }]]);
});

test.each(['#research', '#research?day=2026-10-10', '#assets', '#footage', '#datasets', ''])('overview/page %s still resets fragment scrolling', hash => {
  const page = browser(hash);
  resetStudioRouteScroll();
  expect(page.scrollTo).not.toHaveBeenCalled();
  page.paint();
  expect(page.scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 0, left: 0, behavior: 'instant' });
});

test.each(['#capabilities', '#toolbox', '#toolbox/all', '#models/edgetam'])('a Toolbox view (%s) resets at once, and again once painted', hash => {
  const page = browser(hash);
  resetStudioRouteScroll();
  expect(page.scrollTo).toHaveBeenCalledExactlyOnceWith({ top: 0, left: 0, behavior: 'instant' });
  page.paint();
  expect(page.scrollTo).toHaveBeenCalledTimes(2);
});

test('the studio\'s pages are its routes: the workspace, a researcher, All assets, All sessions, test footage, Datasets, Capabilities, Models', () => {
  for (const hash of ['#', '#research/r1', '#assets', '#sessions', '#footage', '#datasets', '#capabilities', '#models', '#models/edgetam', '#toolbox', '#toolbox/all', '#toolbox/mine/tool']) expect(STUDIO_ROUTE.test(hash)).toBe(true);
  for (const hash of ['#sources', '#elsewhere']) expect(STUDIO_ROUTE.test(hash)).toBe(false);
});

test.each(['#research/r2?day=2026-10-12.2&findings=1'])('a research link with a session id keeps its reading position (%s)', hash => {
  const page = browser(hash);
  resetStudioRouteScroll();
  page.paint();
  expect(page.scrollTo).not.toHaveBeenCalled();
});

test('initial connection, missing health and reconnect are not reported as an absent application',()=>{
 expect(workspaceConnectionNotice(false,false,false)?.title).toBe('Connecting to the workspace…');
 expect(workspaceConnectionNotice(true,true,false)?.title).toBe('Loading workspace status…');
 expect(workspaceConnectionNotice(true,true,true)).toBeNull();
 const lost=workspaceConnectionNotice(false,true,true)!;
 expect(lost.title).toBe('The workspace connection was interrupted');
 expect(lost.lines.join(' ')).toContain('before retrying work');
 expect(JSON.stringify([lost,workspaceConnectionNotice(false,false,false)])).not.toMatch(/Terminal|not running/);
});

test('scientific setup links are recognized by the shared Studio link handler', () => {
  expect(STUDIO_ROUTE.test('#models/mine%2Fome-zarr-reader')).toBe(true);
  expect(STUDIO_ROUTE.test('#toolbox/bundle-123%2Freader')).toBe(true);
});

test('no :has() on the page itself (html, body) or the Stage block: each change inside one re-styled the whole page (b041)', async () => {
  const { readdirSync, readFileSync } = await import('node:fs');
  const dir = new URL('.', import.meta.url).pathname;
  const offending: string[] = [];
  for (const file of readdirSync(dir).filter((name) => name.endsWith('.css'))) {
    const css = readFileSync(dir + file, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
    for (const [, selectors] of css.matchAll(/([^{}]+)\{/g)) {
      for (const selector of selectors.split(',')) {
        const at = selector.indexOf(':has(');
        if (at < 0) continue;
        // The compound :has() belongs to: what a change inside it makes the browser re-check, with all it contains.
        const subject = selector.slice(0, at).trim().split(/[\s>+~]+/).pop() ?? '';
        if (/^(html|body|:root)(?![\w-])/.test(subject) || /^\.studio(?![\w-])/.test(subject)) offending.push(`${file}: ${selector.trim()}`);
      }
    }
  }
  expect(offending).toEqual([]);
});

test('the side columns have no entrance of their own and the Stage block no transition: one motion moves them (b041)', async () => {
  const { readdirSync, readFileSync } = await import('node:fs');
  const dir = new URL('.', import.meta.url).pathname;
  const css = readdirSync(dir).filter((name) => name.endsWith('.css')).map((name) => readFileSync(dir + name, 'utf8')).join('\n');
  // The old fades (a 120 ms opacity fade and a 4 px slide, replayed when switching from Data to a drawer place, so the
  // column blinked from the page colour), on their own clock beside the Stage's.
  expect(css).not.toMatch(/workspace-drawer-in|study-panel-in/);
  const studio = readFileSync(dir + 'workspace-study.css', 'utf8').split('\n').find((line) => line.includes(".studio{") && line.includes('--study-drawer'));
  expect(studio).toMatch(/\.studio\{transition:none;/); // style.css's grid transition is dead here; nothing animates a Project switch
});
