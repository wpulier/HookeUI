// The screen report's Tab strip says each Tab's kind and what it holds (workspace-model.ts; workspace_observation.py
// `holds`), within the bounds the server reads, so "which Tab has the measurements?" is read from the report.
import { expect, test } from 'vitest';
import { observeLegacyForTest, verifierLegacy } from './screen';

const tabs = (holds: Record<string, number>, kind = 'work') => ({ schema: 'studio-surface-v1' as const, scope: 'dom-observation' as const,
  workspace: { project: 'p', tab: 'main', tabs: [{ id: 'main', title: 'Main', front: true, kind, holds }] } });

test('the Tab strip carries each Tab\'s kind and holds, known kinds only, whole counts', () => {
  const out = verifierLegacy({ tabs: [{ id: 'main', title: 'Main', front: true, kind: 'work', holds: { table: 1, video: 2.7, spaceship: 3, image: 0 } },
    { id: 't', title: 'X', front: false, kind: 'nonsense', holds: { outlines: 1 } }] });
  expect(out.tabs).toEqual([{ id: 'main', title: 'Main', front: true, kind: 'work', holds: { table: 1, video: 2 } },
    { id: 't', title: 'X', front: false, holds: { outlines: 1 } }]);
  expect(observeLegacyForTest(tabs({ table: 1, 'web-page': 1 })).workspace?.tabs?.[0]).toMatchObject({ kind: 'work', holds: { table: 1, 'web-page': 1 } });
});

test('a Tab strip outside its contract is refused by the studio\'s own check', () => {
  expect(() => observeLegacyForTest(tabs({ spaceship: 1 }))).toThrow(/Invalid workspace Tabs/);
  expect(() => observeLegacyForTest(tabs({ table: -1 }))).toThrow(/Invalid workspace Tabs/);
  expect(() => observeLegacyForTest(tabs({ table: 1 }, 'bogus'))).toThrow(/Invalid workspace Tabs/);
});

test('the Tab strip says an empty Tab\'s live feed is minimized, and the report\'s own check accepts it (Will, 30 Oct)', () => {
  const strip = (specimen: Record<string, unknown>) => ({ schema: 'studio-surface-v1' as const, scope: 'dom-observation' as const,
    workspace: { project: 'p', tab: 't', tabs: [{ id: 'main', title: 'Main', front: false }, { id: 't', title: 'Tab 2', front: true, specimen }] } });
  const out = verifierLegacy({ tabs: [{ id: 't', title: 'Tab 2', front: true, specimen: { state: 'float', rect: [16, 600, 260, 231], minimized: true } }] });
  expect(out.tabs?.[0].specimen).toEqual({ state: 'float', rect: [16, 600, 260, 231], minimized: true });
  expect(observeLegacyForTest(strip({ state: 'float', rect: [16, 600, 260, 231], minimized: true }) as never).workspace?.tabs?.[1]).toMatchObject({ specimen: { minimized: true } });
  expect(() => observeLegacyForTest(strip({ state: 'fill', minimized: true }) as never)).toThrow(/Invalid workspace specimen/);
});
