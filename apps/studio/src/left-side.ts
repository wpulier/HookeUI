import { controlStageInsets } from './capability-controls';

// The left side's decisions (workspace-feel plan, step 4; the mock's rail(), drawers and run page), as pure functions
// of the workspace's state so they are tested once here and workspace-study.ts only applies them: what the Tools rail
// item does when pressed, which rail item is current, the Tools item's mark, the Assistant header's "<tool> controls"
// chip and its button's pressed state, what the drawer does when a docked control comes into view, and how wide the
// left column is.

/** What the workspace shows on the left now. `leftDocked` is what is docked on the left in view: a tool page (it
 * belongs to Tools), a scientific inspector, or nothing. `parked` is a tool page of this Project and Tab hidden by a
 * rail switch (docked left or floating). */
export interface LeftState {
  drawerOpen: boolean;
  /** The drawer's place (it keeps its last place while closed). */
  section: string;
  dataOpen: boolean;
  dataMode: 'outputs' | 'history';
  leftDocked: 'page' | 'inspector' | null;
  parked: boolean;
  /** A hand-started run is working. */
  running: boolean;
}

/** The Tools rail item: a parked page comes back; with a page in view the page parks and the list shows; the list
 * closes when it is the place showing; else the list opens. */
export type ToolsPress = 'show-parked' | 'park-and-list' | 'close' | 'open-list';
export function toolsPress(s: LeftState): ToolsPress {
  if (s.parked) return 'show-parked';
  if (s.leftDocked === 'page') return 'park-and-list';
  if (s.drawerOpen && !s.dataOpen && s.section === 'tools') return 'close';
  return 'open-list';
}

/** Which rail item is current (rail and content always agree): Tools under a run page, none under an inspector (its
 * window title says what it is), Data or Activity for the Data window's tab, else the drawer's place while it is open. */
export function railCurrent(s: Pick<LeftState, 'drawerOpen' | 'section' | 'dataOpen' | 'dataMode' | 'leftDocked'>): string {
  if (s.leftDocked) return s.leftDocked === 'page' ? 'tools' : '';
  if (s.dataOpen) return s.dataMode === 'history' ? 'activity' : 'data';
  return s.drawerOpen ? s.section : '';
}

/** The Tools item's mark, only while Tools is not the place showing (the mock's `S.drawer !== 'tools'`): the lit cell
 * while a run works, else a dot while a run page is parked. The current item never carries a redundant badge. */
export type ToolsMark = { kind: 'cell'; label: string } | { kind: 'dot'; label: string } | null;
export function toolsMark(s: LeftState, parkedTitle = ''): ToolsMark {
  if (railCurrent(s) === 'tools') return null;
  if (s.running) return { kind: 'cell', label: 'A run is working' };
  if (s.parked) return { kind: 'dot', label: `${parkedTitle || 'A run page'} is open; Tools brings it back` };
  return null;
}

/** The Assistant's header and its Stage-bar button: the chip names a page parked on the right ("Bright objects
 * controls"); the button is pressed only while the Assistant itself shows (not a page docked over it). */
export function assistantHeader(rightParkedTitle: string | null, inspectorOpen: boolean, rightVisible: boolean): { chip: string | null; pressed: boolean } {
  return { chip: rightParkedTitle ? `${rightParkedTitle} controls` : null, pressed: inspectorOpen && !rightVisible };
}

/** A docked control came into view on a side. A tool page on the left is a page of Tools: the drawer is on Tools under
 * it and the Data window closes. An inspector on the left closes the drawer and the Data window. The right side leaves
 * the left alone. */
export function afterShown(s: Pick<LeftState, 'drawerOpen' | 'section' | 'dataOpen'>, side: 'left' | 'right', page: boolean): { drawerOpen: boolean; section: string; dataOpen: boolean } {
  if (side === 'right') return { drawerOpen: s.drawerOpen, section: s.section, dataOpen: s.dataOpen };
  return page ? { drawerOpen: true, section: 'tools', dataOpen: false } : { drawerOpen: false, section: s.section, dataOpen: false };
}

/** The left column's width bounds (its resize edge). */
export const LEFT_MIN = 320, LEFT_MAX = 720;
/** The left column's width (the drawer, the Data window and a left page): the one the person set by dragging, else the
 * mock's 340 px at 1200 px and wider and 320 px below. */
export function leftColumnWidth(set: number | null, viewport: number): number {
  if (set !== null && Number.isFinite(set)) return Math.max(LEFT_MIN, Math.min(LEFT_MAX, Math.round(set)));
  return viewport >= 1200 ? 340 : 320;
}
/** A saved width, or null for "the default". Before this version (`panelSizing` 4 and 5) the width was saved whether
 * or not the person set it, so the old defaults (420, then 360) read back as the default. */
export function savedLeftWidth(panelSizing: unknown, value: unknown): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null;
  if ((panelSizing === 4 && value === 420) || (panelSizing === 5 && value === 360)) return null;
  if (panelSizing !== 4 && panelSizing !== 5 && panelSizing !== 6) return null;
  return Math.max(LEFT_MIN, Math.min(LEFT_MAX, Math.round(value)));
}
// Whether the left column sits beside the Stage or over it (R-16) is the deck's stage insets' decision
// (controlStageInsets in capability-controls.ts), so the column, a docked page and the Stage always agree.

/** What the sides take now (px): the rail; the left column's width when one shows (the drawer or the Data window), else
 * 0; the right column's width as the deck reserves it (the Assistant, also as a layer beside a researcher), else 0; the
 * Assistant's width in the layout's CSS (--study-inspector-width: 0 while it is a layer or closed); and the width of a
 * page docked on either side, or null. */
export interface SideRoom { viewport: number; rail: number; left: number; right: number; inspector: number; leftPage: number | null; rightPage: number | null }
/** Where the Stage sits between the sides, as the layout's CSS decides it (its insets from the window's left and right
 * edges): beside the rail and the left column, left of the Assistant (workspace-study.css); the column floating over it
 * when the Stage would keep less than 320 px (`over`, workspace-feel.css); or a docked page's reservation
 * (capability-controls.css). From state alone, so a press's motion knows where the Stage starts and lands without
 * measuring (workspace-study.ts sidesShown). */
export function stageInsets(room: SideRoom): { left: number; right: number; over: boolean } {
  const { viewport, rail, left, right, inspector, leftPage, rightPage } = room;
  const decided = controlStageInsets(viewport, leftPage === null ? null : rail + leftPage, rightPage, rail + left, right, rail);
  if (leftPage !== null || rightPage !== null) return { left: decided.left, right: decided.right, over: decided.overLeft };
  return { left: decided.overLeft ? rail : rail + left, right: inspector, over: decided.overLeft };
}
