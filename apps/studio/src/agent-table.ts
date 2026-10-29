/** The Assistant's measurements table (orchestrator/table_window.py, `show_measurements`; docs/architecture/on-screen.md).
 *
 * The same window a person's Open in Measurements opens (`openStudyTable`, workspace-study-content.ts), opened with that
 * code on the Tab the server names, for the workspace opening it was asked in. With a Tab named it is fitted to the Tab
 * with the window's own Expand. It answers at once with the window's placement (or why not); done is the screen report
 * showing that window there, in front, its rows drawn: this answer is no confirmation of its own. */
import { openStudyTable } from './workspace-study-content';
import { studyArtifacts } from './workspace-study-artifacts';
import type { StudyRetainedView } from './workspace-study-restoration';

export interface TableWindowRequest {
  type: 'table-window'; id: string; session: string; tab: string; fit: boolean;
  workspacePin: { client: Record<string, unknown>; id: string; rootId: string; kind: string; lifetimeGeneration: string | number; sourceGeneration: number };
  item: { resource: { day: string; id: string; version: number }; title: string };
}
export type TableWindowResult = { ok: true; placement: string; project: string; tab: string; fitted: boolean } | { ok: false; error: string };

export interface TableWindowDeps {
  /** The workspace opening the request was asked in is still the one open (the Tab strip's pin, screen.ts). */
  current: () => boolean;
  where?: () => { project?: string; tab?: string; opening: boolean };
  open?: (saved: StudyRetainedView) => void;
  find?: (project: string, tab: string, resource: TableWindowRequest['item']['resource']) => HTMLElement | null;
  expand?: (root: HTMLElement) => void;
}

/** The open table window of that exact table and revision on that Project's Tab, if any. */
export function tableWindowRoot(project: string, tab: string, resource: TableWindowRequest['item']['resource']): HTMLElement | null {
  for (const root of document.querySelectorAll<HTMLElement>('#study-artifacts > [data-placement-id]')) {
    if (root.dataset.project !== project || root.dataset.workspaceView !== tab || root.dataset.dismissed === 'true' || root.hidden) continue;
    try {
      const kept = JSON.parse(root.dataset.studyRetained ?? 'null') as StudyRetainedView | null;
      if (kept?.presentation === 'table' && kept.resource.day === resource.day && kept.resource.id === resource.id
          && (kept.resource.version ?? 0) === resource.version) return root;
    } catch { /* not a retained window */ }
  }
  return null;
}

/** Opens the table window the request names, or says why not: another Project or Tab in front, or no window opened. */
export function openTableWindow(message: TableWindowRequest, deps: TableWindowDeps): TableWindowResult {
  const where = deps.where ?? (() => ({ project: document.body.dataset.workspaceProject, tab: document.body.dataset.activeWorkspaceView,
    opening: document.body.dataset.workspaceOpening === 'true' }));
  const open = deps.open ?? ((saved: StudyRetainedView) => openStudyTable(saved));
  const find = deps.find ?? tableWindowRoot;
  const expand = deps.expand ?? ((root: HTMLElement) => studyArtifacts().focusDocument(root));
  try {
    const here = where(), resource = message.item.resource;
    if (!deps.current() || here.opening) return { ok: false, error: 'The Project’s workspace was opened again; ask in the Project open now.' };
    if (here.project !== message.session || resource.day !== message.session) return { ok: false, error: 'Another Project is open in this window.' };
    if (here.tab !== message.tab) return { ok: false, error: 'Another Tab is in front now.' };
    open({ resource: { ...resource, picture: 0 }, title: message.item.title, zoom: 1, pan: [.5, .5], presentation: 'table' });
    const root = find(message.session, message.tab, resource);
    if (!root?.dataset.placementId) return { ok: false, error: 'The table window did not open on this Tab.' };
    if (message.fit) expand(root);
    // The window's own Expand says Restore while it uses the Tab's space (workspace-study-artifacts.ts windowControls).
    const fitted = root.querySelector<HTMLElement>('.study-expand')?.textContent === 'Restore';
    return { ok: true, placement: root.dataset.placementId, project: message.session, tab: message.tab, fitted };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : String(error) };
  }
}
