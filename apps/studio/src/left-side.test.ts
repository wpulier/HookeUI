import { describe, expect, it } from 'vitest';
import { afterShown, assistantHeader, leftColumnWidth, railCurrent, savedLeftWidth, stageInsets, toolsMark, toolsPress, type LeftState, type SideRoom } from './left-side';

const base: LeftState = { drawerOpen: false, section: 'inputs', dataOpen: false, dataMode: 'outputs', leftDocked: null, parked: false, running: false };
const at = (state: Partial<LeftState>): LeftState => ({ ...base, ...state });

describe('the left side’s decisions', () => {
  it('Tools brings back a parked page, else parks a page in view and shows the list, else opens or closes the list', () => {
    expect(toolsPress(at({ parked: true }))).toBe('show-parked');
    expect(toolsPress(at({ parked: true, drawerOpen: true, section: 'tools' }))).toBe('show-parked'); // the list showing: the page comes back
    expect(toolsPress(at({ leftDocked: 'page', drawerOpen: true, section: 'tools' }))).toBe('park-and-list');
    expect(toolsPress(at({ drawerOpen: true, section: 'tools' }))).toBe('close');
    expect(toolsPress(at({ drawerOpen: true, section: 'research' }))).toBe('open-list');
    expect(toolsPress(at({ dataOpen: true }))).toBe('open-list');
    // An inspector docked left is not a page of Tools: the list opens over it.
    expect(toolsPress(at({ leftDocked: 'inspector' }))).toBe('open-list');
  });

  it('says one current place: Tools under a page, none under an inspector, Data or Activity, else the drawer’s place', () => {
    expect(railCurrent(at({ leftDocked: 'page', drawerOpen: true, section: 'tools' }))).toBe('tools');
    expect(railCurrent(at({ leftDocked: 'inspector', dataOpen: true }))).toBe('');
    expect(railCurrent(at({ dataOpen: true }))).toBe('data');
    expect(railCurrent(at({ dataOpen: true, dataMode: 'history' }))).toBe('activity');
    expect(railCurrent(at({ drawerOpen: true, section: 'research' }))).toBe('research');
    expect(railCurrent(at({ section: 'research' }))).toBe(''); // closed: nothing is current
  });

  it('marks Tools only while Tools is not the place showing: the lit cell while a run works, else the parked dot', () => {
    // Back on the Tools list with the page parked: Tools is current, so no redundant dot.
    expect(toolsMark(at({ parked: true, drawerOpen: true, section: 'tools' }), 'Bright objects')).toBe(null);
    expect(toolsMark(at({ leftDocked: 'page', drawerOpen: true, section: 'tools', running: true }))).toBe(null);
    expect(toolsMark(at({ parked: true, drawerOpen: true, section: 'research' }), 'Bright objects'))
      .toEqual({ kind: 'dot', label: 'Bright objects is open; Tools brings it back' });
    expect(toolsMark(at({ parked: true, running: true }))).toEqual({ kind: 'cell', label: 'A run is working' });
    expect(toolsMark(at({ leftDocked: 'inspector', running: true }))).toEqual({ kind: 'cell', label: 'A run is working' });
    expect(toolsMark(at({ drawerOpen: true, section: 'research' }))).toBe(null);
    // The Tools list closed (the drawer keeps "tools" as its place): the mark shows again.
    expect(toolsMark(at({ section: 'tools', parked: true }), 'Bright objects')?.kind).toBe('dot');
  });

  it('offers a page parked on the right from the Assistant’s header, and presses its button only while it shows itself', () => {
    expect(assistantHeader('Bright objects', true, false)).toEqual({ chip: 'Bright objects controls', pressed: true });
    // The page shows over the Assistant: the button is not pressed (pressing it shows the Assistant).
    expect(assistantHeader(null, true, true)).toEqual({ chip: null, pressed: false });
    // Parked on the right with the Assistant closed: the chip waits in the closed panel; the button is not pressed.
    expect(assistantHeader('Bright objects', false, false)).toEqual({ chip: 'Bright objects controls', pressed: false });
    expect(assistantHeader(null, false, false)).toEqual({ chip: null, pressed: false });
  });

  it('turns the drawer to Tools under a page shown on the left, closes it under an inspector, and leaves it for the right', () => {
    expect(afterShown({ drawerOpen: true, section: 'research', dataOpen: false }, 'left', true)).toEqual({ drawerOpen: true, section: 'tools', dataOpen: false });
    expect(afterShown({ drawerOpen: false, section: 'inputs', dataOpen: true }, 'left', true)).toEqual({ drawerOpen: true, section: 'tools', dataOpen: false });
    expect(afterShown({ drawerOpen: true, section: 'research', dataOpen: false }, 'left', false)).toEqual({ drawerOpen: false, section: 'research', dataOpen: false });
    expect(afterShown({ drawerOpen: true, section: 'research', dataOpen: false }, 'right', true)).toEqual({ drawerOpen: true, section: 'research', dataOpen: false });
  });

  it('is 340 px wide at 1200 px and wider and 320 px below, unless the person set a width by dragging', () => {
    expect(leftColumnWidth(null, 1440)).toBe(340);
    expect(leftColumnWidth(null, 1200)).toBe(340);
    expect(leftColumnWidth(null, 1100)).toBe(320);
    expect(leftColumnWidth(480, 1100)).toBe(480);
    expect(leftColumnWidth(200, 1440)).toBe(320);
    expect(leftColumnWidth(900, 1440)).toBe(720);
  });

  it('reads a saved width back, taking the old always-saved defaults (420, then 360) as the default', () => {
    expect(savedLeftWidth(5, 360)).toBe(null);
    expect(savedLeftWidth(4, 420)).toBe(null);
    expect(savedLeftWidth(5, 480)).toBe(480);
    expect(savedLeftWidth(6, 360)).toBe(360);
    expect(savedLeftWidth(6, null)).toBe(null);
    expect(savedLeftWidth(6, 1000)).toBe(720);
    expect(savedLeftWidth(undefined, 480)).toBe(null); // before panel sizing: the default
  });

  // The Stage's place from state alone, as the CSS lays it out (workspace-study.css, workspace-feel.css's floating
  // column, capability-controls.css): a side press's motion starts and lands there without measuring (b041; the
  // widths are the ones measured at 1440x900 and 1100x800).
  const room = (r: Partial<SideRoom>): SideRoom => ({ viewport: 1440, rail: 72, left: 0, right: 360, inspector: 360, leftPage: null, rightPage: null, ...r });
  it('places the Stage beside the rail, the left column and the Assistant', () => {
    expect(stageInsets(room({}))).toEqual({ left: 72, right: 360, over: false }); // 1008 wide
    expect(stageInsets(room({ left: 340 }))).toEqual({ left: 412, right: 360, over: false }); // 668
    expect(stageInsets(room({ right: 0, inspector: 0 }))).toEqual({ left: 72, right: 0, over: false }); // 1368
    expect(stageInsets(room({ left: 340, right: 0, inspector: 0 }))).toEqual({ left: 412, right: 0, over: false }); // 1028
    // Below 1200 px the left column and the Assistant are never open together.
    expect(stageInsets(room({ viewport: 1100, left: 320, right: 0, inspector: 0 }))).toEqual({ left: 392, right: 0, over: false }); // 708
    // Beside a researcher the Assistant is a layer: the deck counts it, the Stage's CSS does not.
    expect(stageInsets(room({ inspector: 0 }))).toEqual({ left: 72, right: 0, over: false });
  });

  it('floats the left column over the Stage when the Stage would keep less than 320 px, and follows a docked page', () => {
    expect(stageInsets(room({ viewport: 1100, left: 720, right: 0, inspector: 0 }))).toEqual({ left: 72, right: 0, over: true });
    // A page docked left takes the Tools width beside the rail; one docked right takes its own width.
    expect(stageInsets(room({ left: 340, leftPage: 340 }))).toEqual({ left: 412, right: 360, over: false });
    expect(stageInsets(room({ rightPage: 480 }))).toEqual({ left: 72, right: 480, over: false });
  });
});
