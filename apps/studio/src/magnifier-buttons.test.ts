// The magnifier's zoom buttons are updated every frame (magnifier.ts updateButtons): `hidden` is written only when it
// changes. An unchanged `hidden = true` still queues a mutation record, which the capability deck's observer answered
// with a refresh about 69 times a second at idle (b041).
import { afterEach, expect, test, vi } from 'vitest';
import { MagnifierPanel } from './magnifier';
import { TestElement } from './workspace-windows.test-support';
import type { PanelStack } from './panels';
import type { View } from './view';

afterEach(() => vi.unstubAllGlobals());

test('the zoom buttons’ hidden is written only when it changes, never every frame', () => {
  const nodes = new Map<string, TestElement>();
  vi.stubGlobal('document', { getElementById: (id: string) => { if (!nodes.has(id)) nodes.set(id, new TestElement('div')); return nodes.get(id); } });
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {} });
  const zoom = new TestElement('div');
  let hidden = false, writes = 0;
  Object.defineProperty(zoom, 'hidden', { get: () => hidden, set: (value: boolean) => { writes++; hidden = value; } });
  nodes.set('magnifier-zoom', zoom);
  const panel = new MagnifierPanel({} as View, {} as PanelStack, () => {});
  const frame = () => (panel as unknown as { updateButtons(): void }).updateButtons();
  frame();
  expect(hidden).toBe(true); // closed: nothing to zoom
  expect(writes).toBe(1);
  for (let i = 0; i < 60; i++) frame(); // a second of frames at idle
  expect(writes).toBe(1);
  // A live magnifier opens: shown once, then left alone.
  (panel as unknown as { state: unknown }).state = { open: true, index: 0, sources: [{ kind: 'live' }], level: 1 };
  frame(); frame(); frame();
  expect(hidden).toBe(false);
  expect(writes).toBe(2);
});
