// The Assistant's measurements table (orchestrator/table_window.py): the studio opens the window a person's Open in
// Measurements opens, on the Tab the server names, and answers with its placement or why not. Done is the screen
// report showing it (voice log 2026-10-28: a #measurements page was said "shown" while the Tab held no table).
import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { openTableWindow, type TableWindowRequest } from './agent-table';

const request = (fields: Partial<TableWindowRequest> = {}): TableWindowRequest => ({
  type: 'table-window', id: 'm1', session: '2026-10-28', tab: 'tab-2', fit: true,
  workspacePin: { client: {}, id: 'workspace-tabs', rootId: 'tabs', kind: 'panel', lifetimeGeneration: 'l', sourceGeneration: 1 },
  item: { resource: { day: '2026-10-28', id: 'e3', version: 2 }, title: 'Nuclei' }, ...fields });
const tableRoot = (expanded: boolean) => ({ dataset: { placementId: 'p7' },
  querySelector: () => ({ textContent: expanded ? 'Restore' : 'Expand' }) }) as unknown as HTMLElement;
const here = (tab = 'tab-2', project = '2026-10-28') => () => ({ project, tab, opening: false });

describe('the Assistant opens the measurements table as Open in Measurements does', () => {
  it('opens that exact table and revision as a table window on the Tab named, fitted, and names its placement', () => {
    let expanded = false;
    const open = vi.fn(), expand = vi.fn(() => { expanded = true; });
    const result = openTableWindow(request(), { current: () => true, where: here(), open, expand,
      find: () => ({ dataset: { placementId: 'p7' }, querySelector: () => ({ textContent: expanded ? 'Restore' : 'Expand' }) }) as unknown as HTMLElement });
    expect(open).toHaveBeenCalledWith({ resource: { day: '2026-10-28', id: 'e3', version: 2, picture: 0 }, title: 'Nuclei', zoom: 1,
      pan: [.5, .5], presentation: 'table' });
    expect(expand).toHaveBeenCalledOnce();
    expect(result).toEqual({ ok: true, placement: 'p7', project: '2026-10-28', tab: 'tab-2', fitted: true });
  });
  it('keeps the window’s size when no Tab was named', () => {
    const expand = vi.fn();
    const result = openTableWindow(request({ fit: false }), { current: () => true, where: here(), open: vi.fn(), expand, find: () => tableRoot(false) });
    expect(expand).not.toHaveBeenCalled();
    expect(result).toMatchObject({ ok: true, fitted: false });
  });
  it('opens nothing for another Tab, another Project or another opening of the workspace, and says which', () => {
    const open = vi.fn();
    expect(openTableWindow(request(), { current: () => true, where: here('main'), open, find: () => tableRoot(false) }))
      .toEqual({ ok: false, error: 'Another Tab is in front now.' });
    expect(openTableWindow(request(), { current: () => true, where: here('tab-2', 'other'), open, find: () => tableRoot(false) }))
      .toEqual({ ok: false, error: 'Another Project is open in this window.' });
    expect(openTableWindow(request(), { current: () => false, where: here(), open, find: () => tableRoot(false) }).ok).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });
  it('says so when no window opened, never that it did', () => {
    expect(openTableWindow(request(), { current: () => true, where: here(), open: vi.fn(), find: () => null }))
      .toEqual({ ok: false, error: 'The table window did not open on this Tab.' });
    expect(openTableWindow(request(), { current: () => true, where: here(), open: () => { throw new Error('No Project is open.'); } }))
      .toEqual({ ok: false, error: 'No Project is open.' });
  });
});

describe('the studio wires it and reports at once', () => {
  const main = readFileSync(new URL('./main.ts', import.meta.url), 'utf8');
  it('answers table-window for the opening it was asked in, then reports what it shows', () => {
    expect(main).toMatch(/case 'table-window': \{[\s\S]{0,200}openTableWindow\(message, \{ current: \(\) => reporter\.matchesStructuralPin\(message\.workspacePin\) \}\);\s*connection\.send\(\{ type: 'table-window-result', id: message\.id, session: message\.session, workspacePin: message\.workspacePin, result \}\);\s*reporter\.askNow\(\);/);
  });
  it('says the Tab in front by its title in the Tab strip’s report', () => {
    expect(main).toMatch(/work-tab-\$\{tab\}`\)\?\.querySelector\('\.workspace-tab-label'\)/);
    expect(main).toMatch(/workspace:\{project,tab,\.\.\.\(tabTitle\?\{tabTitle\}:\{\}\),/); // then the open Tabs (Hands' verifier)
  });
});
