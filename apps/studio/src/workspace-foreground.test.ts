import { afterEach, expect, test, vi } from 'vitest';
import { TestElement } from './workspace-windows.test-support';
import { workspaceForeground } from './workspace-foreground';
// Synthetic DOM/style facts, not browser paint evidence.
afterEach(() => vi.unstubAllGlobals());
function world() {
  vi.stubGlobal('document', { visibilityState: 'visible' });
  vi.stubGlobal('getComputedStyle', (node: TestElement) => ({ display: node.hidden ? 'none' : 'block', visibility: 'visible', opacity: '1', position: 'absolute', zIndex: 'auto', ...node.style }));
  const body = new TestElement('body'), host = new TestElement('main'); host.id = 'stage'; body.append(host);
  const canvas = new TestElement('canvas'), old = new TestElement('aside'), image = new TestElement('section');
  canvas.id = 'view'; old.id = 'side'; old.style.zIndex = '2'; image.id = 'photo'; image.style.zIndex = '31';
  const viewer = new TestElement('section'); old.append(viewer); host.append(canvas, old, image);
  const read = () => workspaceForeground(host as unknown as HTMLElement, [canvas, viewer, image] as unknown as HTMLElement[]);
  return { host, canvas, old, image, read };
}
test('measures actual native and legacy sibling roots without global source/control completeness', () => {
  const w = world(); expect(w.read()).toEqual({ scope: 'workspace-windows', hostId: 'stage', complete: true,
    roots: [{ rootId: 'view', z: 0 }, { rootId: 'side', z: 2 }, { rootId: 'photo', z: 31 }] });
  w.image.style.zIndex = '30'; expect(w.read().roots.at(-1)?.z).toBe(30);
});
test('new unregistered visible roots and non-stacking ancestors leave coverage incomplete', () => {
  const w = world(), surprise = new TestElement('div'); surprise.id = 'surprise'; surprise.style.zIndex = '100'; w.host.append(surprise);
  expect(w.read().complete).toBe(false);
  surprise.hidden = true; expect(w.read().complete).toBe(true);
  w.old.style.zIndex = 'auto'; expect(w.read().complete).toBe(false);
});
test.each(['zero-size', 'hidden-visibility'])('unknown %s wrapper cannot hide a painting descendant from coverage', (kind) => {
  const w = world(), wrapper = new TestElement('div'), popup = new TestElement('section');
  wrapper.id = 'unknown-wrapper'; popup.style.zIndex = '999'; popup.style.visibility = 'visible';
  if (kind === 'zero-size') { wrapper.style.width = '0px'; wrapper.style.height = '0px'; }
  else wrapper.style.visibility = 'hidden';
  wrapper.append(popup); w.host.append(wrapper);
  expect(w.read().complete).toBe(false);
  wrapper.style.display = 'none'; expect(w.read().complete).toBe(true);
});
