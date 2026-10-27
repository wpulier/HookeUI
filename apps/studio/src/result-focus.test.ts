// Production visibility arbitration over synthetic roots, not pixel/browser qualification.
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { ResultFocus } from './result-focus';
import { PanelStack, type Panel } from './panels';
let changed: () => void;
let width = 390;
const roots = () => ({hidden:false, isConnected:true, dataset:{} as Record<string,string>});
beforeEach(() => {
  width = 390;
  vi.stubGlobal('MutationObserver', class { constructor(callback: () => void) { changed = callback; } observe() {} disconnect() {} });
});
afterEach(() => vi.unstubAllGlobals());
const node = (root: ReturnType<typeof roots>) => root as unknown as HTMLElement;

test('admitted native and legacy selections share one phone surface without closing or replacing media', () => {
  const focus = new ResultFocus(() => width), native = roots(), legacy = roots(), details = roots();
  focus.register('native', node(native), () => 'Retained source'); focus.activate('native');
  focus.register('legacy', node(legacy), () => 'Loaded report', true);
  // Background render registration does not steal selection.
  expect(legacy.dataset.resultTucked).toBe('true');
  expect(native.dataset.resultTucked).toBe('false');
  focus.activate('legacy'); focus.register('details', node(details), () => 'Object details', true);
  expect(native.hidden).toBe(false); expect(legacy.hidden).toBe(false);
  expect(native.dataset.resultTucked).toBe('true'); expect(details.dataset.resultTucked).toBe('true');
  expect(focus.retainedViews()).toEqual([{id:'legacy',title:'Loaded report',visible:true,resource:null},{id:'details',title:'Object details',visible:false,resource:null}]);
  expect(focus.restore('details')).toBe(true);
  expect(legacy.dataset.resultTucked).toBe('true'); expect(details.dataset.resultTucked).toBe('false');
  // Desktop restores all still-open source nodes, without a command or media recreation.
  width = 1280; focus.sync();
  expect([native,legacy,details].every(root => root.dataset.resultTucked === 'false')).toBe(true);
});

test('actual close removes recovery and exposes a retained result; detached lifetimes cannot be restored', () => {
  const focus = new ResultFocus(() => width), native = roots(), legacy = roots();
  focus.register('native',node(native),()=>'Native'); focus.activate('native');
  focus.register('legacy',node(legacy),()=>'Legacy',true); focus.activate('legacy');
  legacy.hidden = true; changed();
  expect(focus.retainedViews()).toEqual([]); expect(native.dataset.resultTucked).toBe('false');
  expect(focus.restore('legacy')).toBe(false);
  legacy.hidden = false; focus.activate('legacy'); legacy.isConnected = false; changed();
  expect(focus.restore('legacy')).toBe(false); expect(native.dataset.resultTucked).toBe('false');
});

test('Escape skips a tucked retained result instead of closing it behind the visible one', () => {
  const stack = new PanelStack(), closed: string[] = [];
  const panel = (id:string, visible:boolean): Panel => ({id,isOpen:()=>true,isVisible:()=>visible,close:()=>closed.push(id),footprint:()=>null});
  stack.register(panel('shown',true)); stack.register(panel('tucked',false));
  expect(stack.onEscape({key:'Escape',target:null,defaultPrevented:false,preventDefault(){}})).toBe(true);
  expect(closed).toEqual(['shown']);
});

test('explicit reopening of identical retained content reveals it, while passive updates and superseded opens cannot steal focus', () => {
  const focus = new ResultFocus(() => width), native = roots(), legacy = roots();
  focus.register('legacy',node(legacy),()=>'Legacy',true); focus.register('native',node(native),()=>'Native');
  focus.activate('native');
  focus.sync(); expect(legacy.dataset.resultTucked).toBe('true');
  focus.request('legacy',()=>true); expect(legacy.dataset.resultTucked).toBe('false');
  let matches = false; legacy.hidden = true;
  focus.request('legacy',()=>matches); focus.activate('native');
  legacy.hidden = false; matches = true; changed();
  expect(native.dataset.resultTucked).toBe('false'); expect(legacy.dataset.resultTucked).toBe('true');
});

// Docked artifacts share the narrow workspace instead of competing for its phone surface.
test('workspace artifacts remain independently visible on a narrow screen', () => {
  const focus=new ResultFocus(()=>390), first=roots(),second=roots();
  first.dataset.studyArtifact='true';second.dataset.studyArtifact='true';
  focus.register('first',node(first),()=>'First');focus.register('second',node(second),()=>'Second');
  focus.activate('first');expect(first.dataset.resultTucked).toBe('false');expect(second.dataset.resultTucked).toBe('false');
  first.hidden=true;changed();expect(second.hidden).toBe(false);expect(second.dataset.resultTucked).toBe('false');
});
