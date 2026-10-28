// Synthetic document/transport tests. Canvas paint and actual worker evidence have separate suites.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import cases from '../../../contract/scientific-view-cases.json';
import preferences from '../../../contract/scientific-view-preferences-cases.json';
import { scientificViewPreferences } from './scientific-view-preferences';
import { configureControlDeck } from './capability-controls';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import type { StudyRetainedView } from './workspace-study-restoration';

const fixture = vi.hoisted(() => ({ window: null as unknown as TestElement, dispose: () => {}, shown: vi.fn(), invalidated: vi.fn(), renamed: vi.fn(), focused: vi.fn(), applied: vi.fn(), keys: [] as string[], paint: null as unknown as typeof cases.valid, sheet: null as unknown as TestElement, waiting: null as string | null, invalidFrame: false, trackInvalidation: false, drawn: () => {}, revoke: () => {}, native: null as unknown as {load(width:number,height:number):Promise<{frame:unknown;pixels:Uint8Array}>;send(command:unknown,draw:string):Promise<void>}, nativeAvailable: false, nativeDraft: false, nativeRefreshed: vi.fn(), drawReceipt: vi.fn<(frame:string)=>Promise<void>>() }));
vi.mock('./workspace-study-artifacts', () => ({ studyArtifacts: () => ({
  document: (key: string, _title: string, content: TestElement, dispose: () => void, metadata: Record<string,string>) => {
    if (fixture.keys.includes(key)) return fixture.window;
    fixture.keys.push(key); Object.assign(fixture.window.dataset, metadata);
    fixture.dispose = dispose; fixture.window.append(content); return fixture.window;
  }, applyDocument: (...args:unknown[]) => fixture.applied(...args), restoreGeometry: () => {}, focusDocument: (...args:unknown[]) => fixture.focused(...args), documentTitle: (...args:unknown[]) => fixture.renamed(...args),
}) }));
vi.mock('./sheet', () => ({ StudioSheet: class { onDismiss = () => {}; open(content: TestElement) { fixture.sheet = content; } close() {} } }));
vi.mock('./scientific-canvas', () => ({ ScientificCanvas: class {
  readonly canvas: TestElement;
  constructor(canvas: TestElement, observer: { drawn(): void; revoked(draw: string): void }) { this.canvas = canvas; fixture.drawn = () => observer.drawn(); fixture.revoke = () => observer.revoked('current-draw'); }
  element() { return this.canvas; }
  show(...args: unknown[]) { fixture.invalidFrame = false; fixture.shown(...args); }
  whenDrawn(frame:string) { return fixture.drawReceipt(frame); }
  current() { return fixture.waiting || fixture.invalidFrame ? null : { frame: fixture.paint, draw: 'current-draw' }; }
  position(_draw: string, x: number, y: number) { return [y,x]; }
  awaiting(frame: string) { return fixture.waiting === frame; }
  invalidate() { if(fixture.trackInvalidation) fixture.invalidFrame=true; fixture.invalidated(); }
  clear() { fixture.invalidated(); this.canvas.setAttribute('width','0'); this.canvas.setAttribute('height','0'); }
  close() {}
} }));
vi.mock('./scientific-native-panel', () => ({ScientificNativePanel: class {
  element = new TestElement('details');
  constructor(_root:unknown, options:typeof fixture.native) {fixture.native=options;}
  bind() {} gesturing() {return false;} layoutChanged() {} interacting() {return false;} async refreshObserved() {await fixture.nativeRefreshed();} hasDraft() {return fixture.nativeDraft;} setAvailable(value:boolean) {fixture.nativeAvailable=value;} setBlocked() {} invalidate() {} close() {}
}}));
import { configureScientificDocuments, openStudyScientific, openScientificSessions, openingWords, scientificArraySave } from './scientific-document';
const saved: StudyRetainedView = { resource: { day: 'origin', id: 'p' + 'a'.repeat(32), version: 2, picture: null }, title: 'Native input', zoom: 1, pan: [.5,.5], placementId: 'placement' };
const review = 'review-1234567890123456';
const reply = (value: object) => new Response(JSON.stringify({ ok: true, ...value }), { headers: { 'content-type': 'application/json' } });
const body = (init?: RequestInit) => JSON.parse(String(init?.body));
const calls: { path: string; init?: RequestInit }[] = [];
let request = vi.fn<(path: string, init?: RequestInit) => Promise<Response>>(), check = vi.fn<() => void>();
const button = (label: string) => fixture.window.querySelectorAll('button').find(item => item.textContent === label)!;
async function settle() { for (let i = 0; i < 12; i++) await settleMicrotasks(); }
function pending<T>() { let resolve!: (value:T) => void; const promise = new Promise<T>(done => resolve = done); return { resolve, promise }; }

beforeEach(() => {
  const dom = syntheticDOM(), createElement = dom.document.createElement;
  dom.document.createElement = tag => Object.assign(createElement(tag), {focus:vi.fn()});
  vi.stubGlobal('document', dom.document); vi.stubGlobal('window', new EventTarget());
  fixture.window = Object.assign(new TestElement('section'), { isConnected: true }); dom.host.append(fixture.window);
  Object.assign(fixture.window.dataset, { project: 'origin', workspaceView: 'tab-one', placementId: 'placement' });
  fixture.paint = structuredClone(cases.valid); fixture.waiting = null; fixture.invalidFrame=false; fixture.trackInvalidation=false;
  fixture.drawReceipt.mockReset().mockResolvedValue(undefined);
  fixture.nativeDraft=false; fixture.nativeRefreshed.mockReset(); fixture.shown.mockReset(); fixture.invalidated.mockReset(); fixture.renamed.mockReset(); fixture.focused.mockReset(); fixture.applied.mockReset(); calls.length = 0; fixture.keys.length = 0;
  dom.host.dataset.activeWorkspaceView = 'tab-one';
  vi.stubGlobal('fetch', vi.fn(async () => reply({ project: { id: saved.resource.id, session: 'origin', revision: 2,
    document: { kind: 'scene', state: { scientificView: {} } } } })));
  check = vi.fn(); request = vi.fn(async (path: string, init?: RequestInit): Promise<Response> => {
    calls.push({ path, init });
    if (path.endsWith('/frames/' + cases.valid.frame)) return new Response(new Uint8Array(32 * 24 * 4));
    if (init?.method === 'DELETE') return reply({});
    if (path.endsWith('/request')) return reply({ frame: cases.valid });
    if (init?.method === 'POST') return reply({ review });
    return reply({ reviews: [] });
  });
  configureScientificDocuments({ prepare: async () => {}, transport: () => ({ request, check }) });
});
afterEach(() => { fixture.dispose(); vi.useRealTimers(); vi.unstubAllGlobals(); });

test('saved Ask attachment is beside selected input before opening a worker, outside Save details', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => reply({project:{id:saved.resource.id,session:'origin',revision:2,digest:'d'.repeat(64),
    document:{kind:'scene',resources:[{component:'array.output'}],state:{scientificView:{}}}}})));
  await openStudyScientific(saved);
  const attach=button('Use with Assistant');
  const context=fixture.window.querySelectorAll('section').find(node=>node.getAttribute('aria-label')==='Selected scientific input and tool')!;
  expect(context.querySelectorAll('button')).toContain(attach);
  expect(attach.closest('details')).toBeNull();
  expect(fixture.window.querySelectorAll('button').filter(node=>node.textContent==='Use with Assistant')).toHaveLength(1);
  expect(request).not.toHaveBeenCalled();
});

test('opening saved data never starts a worker; explicit Start admits exact Project/revision/window and fetches bounded pixels', async () => {
  await openStudyScientific(saved); expect(request).not.toHaveBeenCalled();
  expect(button('Run code').disabled).toBe(true);
  const entry = button('Open saved view');
  expect(entry.parentElement!.hidden).toBe(false);
  fire(entry, 'click'); await settle();
  const open = calls.find(call => call.path === '/api/scientific-reviews' && call.init?.method === 'POST')!;
  expect(body(open.init)).toEqual({ session: 'origin', document: saved.resource.id, revision: 2, item: 'document:placement' });
  expect(fixture.shown).toHaveBeenCalledTimes(1); expect(fixture.shown.mock.calls[0][1]).toHaveLength(32 * 24 * 4);
  expect(fixture.window.textContent).toContain('Private view open. Nothing changed yet.');
  expect(fixture.window.textContent).not.toContain('Saved Project data is unchanged');
  expect(entry.parentElement!.hidden).toBe(true);
});

test('a slow first open says its step and seconds, and a retried attempt says where the first one stopped', async () => {
  vi.useFakeTimers();
  const opening = pending<Response>(), original = request.getMockImplementation()!;
  let state: Record<string, unknown> = { step: 'viewer', says: 'Opening the viewer', seconds: 12.4, attempt: 1, attempts: 2 };
  request.mockImplementation((path:string, init?:RequestInit) => {
    if (path === '/api/scientific-reviews' && init?.method === 'POST') { calls.push({ path, init }); return opening.promise; }
    if (path === '/api/scientific-reviews' && calls.some(call => call.init?.method === 'POST'))
      return Promise.resolve(reply({ reviews: [{ review, placement: 'placement', document: saved.resource, state: 'opening', opening: state }] }));
    return original(path, init);
  });
  await openStudyScientific(saved); fire(button('Open saved view'), 'click'); await settle();
  await vi.advanceTimersByTimeAsync(1000); await settle();
  expect(fixture.window.textContent).toContain('Opening the viewer… 12 s');
  state = { step: 'tool', says: 'Opening the tool’s panel', seconds: 3, attempt: 2, attempts: 2,
    previous: { step: 'tool', says: 'Opening the tool’s panel', seconds: 60.2 } };
  await vi.advanceTimersByTimeAsync(1000); await settle();
  expect(fixture.window.textContent).toContain('Opening the tool’s panel… 3 s (attempt 2 of 2: the first stopped at “Opening the tool’s panel” after 60 s)');
  opening.resolve(reply({ review })); await settle();
  const reads = calls.filter(call => call.path === '/api/scientific-reviews' && !call.init).length;
  await vi.advanceTimersByTimeAsync(3000); await settle();
  expect(calls.filter(call => call.path === '/api/scientific-reviews' && !call.init).length).toBe(reads);  // reading stops once it opened
});

test('opening words need the step, its seconds and its attempt; anything else says nothing', () => {
  expect(openingWords(null)).toBeNull();
  expect(openingWords({ says: 'Opening the viewer', seconds: '3', attempt: 1, attempts: 2 })).toBeNull();
  expect(openingWords({ says: 'Starting the tool’s Python', seconds: 0.4, attempt: 1, attempts: 2 })).toBe('Starting the tool’s Python… 0 s');
});

test('reattaching a hidden session uses its original identity and explicit presentation admission', async () => {
  request.mockImplementationOnce(async () => reply({ reviews: [{ review, placement: 'placement', document: saved.resource }] }));
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  expect(calls.some(call => call.path === `/api/scientific-reviews/${review}/attach`)).toBe(true);
  expect(calls.some(call => call.path === '/api/scientific-reviews' && call.init?.method === 'POST')).toBe(false);
});

test('disposal during admitted startup does not abort or stop work; inventory can recover its session', async () => {
  const opening = pending<Response>(), original = request.getMockImplementation()!;
  request.mockImplementation((path:string, init?:RequestInit) => path === '/api/scientific-reviews' && init?.method === 'POST' ? opening.promise : original(path, init));
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  fixture.dispose(); opening.resolve(reply({ review })); await settle();
  expect(calls.some(call => call.init?.method === 'DELETE')).toBe(false); expect(fixture.shown).not.toHaveBeenCalled();
});

test.each(['Project', 'placement', 'connection'])('late pixel delivery cannot paint after %s changes', async reason => {
  const pixels = pending<Response>(), original = request.getMockImplementation()!;
  request.mockImplementation((path:string, init?:RequestInit) => path.includes('/frames/') ? pixels.promise : original(path, init));
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  if (reason === 'Project') fixture.window.dataset.project = 'different';
  if (reason === 'placement') fixture.window.dataset.placementId = 'different';
  if (reason === 'connection') check.mockImplementation(() => { throw new Error('connection retired'); });
  pixels.resolve(new Response(new Uint8Array(32 * 24 * 4))); await settle();
  expect(fixture.shown).not.toHaveBeenCalled();
});

test('oversized pixels fail without consuming another operation, Stop stays available', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation((path:string, init?:RequestInit) => path.includes('/frames/') ? Promise.resolve(new Response(new Uint8Array(32 * 24 * 4 + 1))) : original(path, init));
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  expect(fixture.shown).not.toHaveBeenCalled(); expect(fixture.window.textContent).toContain('exceeded its declared size');
  fire(button('Stop scientific session'), 'click'); await settle();
  expect(calls.filter(call => call.init?.method === 'DELETE')).toHaveLength(1);
  expect(button('Run code').disabled).toBe(true); expect(fixture.window.textContent).toContain('Saved Project data stays');
});


test('moving a document opened from Data restores its existing placement instead of duplicating the window', async () => {
  const { placementId: _unused, ...fromData } = saved;
  await openStudyScientific(fromData);
  const placement = fixture.window.dataset.placementId;
  expect(placement).toBeTruthy();
  fixture.window.dataset.workspaceView = 'second-tab';
  await openStudyScientific({ ...fromData, placementId: placement });
  expect(fixture.keys).toHaveLength(1);
  const canvases=fixture.window.querySelectorAll('canvas');
  expect(canvases.filter(canvas=>canvas.getAttribute('aria-hidden')!=='true')).toHaveLength(1);
  const overlays=canvases.filter(canvas=>canvas.getAttribute('aria-hidden')==='true');
  expect(overlays).toHaveLength(1); expect(overlays[0].hidden).toBe(true);
  expect(overlays[0].style.cssText).toContain('background:transparent');
  expect(overlays[0].style.cssText).toContain('pointer-events:none');
  expect(request).not.toHaveBeenCalled();
});

test('Python waits for another operation instead of reporting uncertain execution for an unsent cell', async () => {
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  const input = fixture.window.querySelector('textarea')!; input.value = '7'; fire(input, 'input');
  const pendingRender = pending<Response>(); request.mockImplementationOnce(() => pendingRender.promise);
  fire(button('Refresh preview'), 'click');
  expect(button('Run code').disabled).toBe(true); fire(button('Run code'), 'click');
  pendingRender.resolve(reply({ frame: cases.valid })); await settle();
  expect(button('Run code').disabled).toBe(false);
  expect(fixture.window.textContent).not.toContain('No confirmed Python result');
  expect(calls.filter(call => call.init?.body && ['python','python-resources'].includes(body(call.init).request?.op))).toHaveLength(0);
});

test('a refused view change keeps the Python draft and allows explicit refresh and corrected retry in the same session', async () => {
  const original = request.getMockImplementation()!;
  let changes = 0, renders = 0;
  request.mockImplementation(async (path, init) => {
    if (path.includes('/frames/')) return new Response(new Uint8Array(32 * 24 * 4));
    const command = path.endsWith('/request') ? body(init).request : null;
    if (command?.op === 'change' || command?.op === 'render') {
      calls.push({path,init});
      if (command.op === 'change' && ++changes === 1)
        return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_CHANGE_NOT_APPLIED',
          error:'The layer changed before applying this request.', next:'Choose Check current view. Your private work is kept.'}),{status:409});
      const frame = structuredClone(cases.valid); frame.frame = (++renders === 1 ? 'a' : 'd').repeat(64);
      fixture.paint = frame; return reply({frame});
    }
    return original(path, init);
  });
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  const editor = fixture.window.querySelector('textarea')!; editor.value = 'private_value + 1'; fire(editor,'input');
  const opacity = fixture.window.querySelectorAll('input').find(input=>input.getAttribute('aria-label')==='Opacity Intensity')!;
  opacity.value='.4'; fire(opacity,'input'); fire(button('Apply view changes'),'click'); await settle();
  expect(fixture.window.textContent).toContain('private work is kept');
  expect(editor.value).toBe('private_value + 1');
  fire(button('Check current view'),'click'); await settle();
  expect(button('Apply view changes').disabled).toBe(true);
  expect(opacity.value).toBe('.4');
  fire(button('Discard unsent view changes'),'click'); opacity.value='.6'; fire(opacity,'input');
  fire(button('Apply view changes'),'click'); await settle();
  expect(changes).toBe(2); expect(fixture.window.textContent).toContain('View changes applied');
  expect(editor.value).toBe('private_value + 1');
  expect(calls.filter(call=>call.init?.method==='DELETE')).toHaveLength(0);
  expect(calls.filter(call=>call.path==='/api/scientific-reviews' && call.init?.method==='POST')).toHaveLength(1);
});

test('Interrupt targets the running resource-only cell while the document is busy without stopping its session', async () => {
  const execution = pending<Response>(), original = request.getMockImplementation()!;
  let cell = '';
  request.mockImplementation(async (path, init) => {
    const command = path.endsWith('/request') ? body(init).request : null;
    if (command?.op === 'python-resources') {
      calls.push({path, init}); cell = command.cell; return execution.promise;
    }
    if (command?.op === 'python-interrupt') {
      calls.push({path, init}); return reply({interrupt:{cell:command.cell, requested:true}});
    }
    return original(path, init);
  });
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  const input = fixture.window.querySelector('textarea')!;
  input.value = 'kept = 17\nwhile True: pass'; fire(input, 'input'); fire(button('Run code'), 'click'); await settle();
  expect(button('Run code').disabled).toBe(true); expect(button('Interrupt code').disabled).toBe(false);
  fire(button('Interrupt code'), 'click'); await settle();
  const interruption = calls.find(call => call.init?.body && body(call.init).request?.op === 'python-interrupt')!;
  expect(interruption.path).toBe(`/api/scientific-reviews/${review}/request`);
  expect(body(interruption.init)).toMatchObject({draw:null, request:{op:'python-interrupt', cell}});
  expect(body(interruption.init).request).not.toHaveProperty('frame');
  expect(calls.some(call => call.init?.method === 'DELETE')).toBe(false);
  expect(button('Run code').disabled).toBe(true);
  execution.resolve(reply({console:{format:'scientific-console-v1', sequence:1, stdout:'', stderr:'', result:'',
    error:'KeyboardInterrupt: Interrupted by the researcher', truncated:false, privatePreview:true}}));
  await settle();
  expect(button('Run code').disabled).toBe(false);
  expect(fixture.window.textContent).toContain('Variables and partial changes remain');
});

test('Stop from the global inventory disables the matching open console and control panel', async () => {
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  const input = fixture.window.querySelector('textarea')!; input.value = '7'; fire(input, 'input');
  request.mockImplementationOnce(async () => reply({ reviews: [{ review, placement: 'placement', tab: 'tab-one', state: 'idle', document: saved.resource }] }));
  openScientificSessions(); await settle();
  const stop = fixture.sheet.querySelectorAll('button').find(row => row.textContent === 'Stop this session')!;
  fire(stop, 'click'); await settle();
  expect(button('Run code').disabled).toBe(true); expect(button('Refresh preview').disabled).toBe(true);
  expect(fixture.window.textContent).toContain('Scientific session stopped');
  expect(fixture.sheet.textContent).toContain('Stopped. Saved Project data stays.');
});

test('memory refusal offers owned sessions and explicit retry preserves the exact input', async()=>{
  const original=request.getMockImplementation()!;
  let refused=false;
  request.mockImplementation(async(path,init)=>{
    if(path==='/api/scientific-reviews' && init?.method==='POST' && !refused){
      refused=true;calls.push({path,init});
      return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_MEMORY',error:'Not enough memory',next:'Review running sessions.'}),{status:503});
    }
    return original(path,init);
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  const recovery=button('Review running sessions');
  expect(recovery.hidden).toBe(false);
  expect(calls.filter(call=>call.init?.method==='POST')).toHaveLength(1);
  fire(recovery,'click');await settle();
  expect(fixture.sheet.textContent).toContain('No scientific sessions are running');
  expect(fixture.sheet.textContent).toContain('save work in other applications');
  expect(fixture.sheet.textContent).toContain('reopen a refused session, or refresh the preview for completed work');
  expect(fixture.sheet.textContent).toContain('Do not rerun code or plugin actions just to recover the preview');
  expect(calls.some(call=>call.init?.method==='DELETE')).toBe(false);
  fire(button('Open session'),'click');await settle();
  const starts=calls.filter(call=>call.path==='/api/scientific-reviews'&&call.init?.method==='POST');
  expect(starts).toHaveLength(2);expect(body(starts[1].init)).toEqual(body(starts[0].init));
  expect(recovery.hidden).toBe(true);expect(fixture.shown).toHaveBeenCalledOnce();
});

test('a failed session inventory can retry without stopping work or reopening the operation',async()=>{
  request.mockRejectedValueOnce(new Error('Connection interrupted'));
  openScientificSessions();await settle();
  expect(fixture.sheet.textContent).toContain('Connection interrupted');
  const refresh=fixture.sheet.querySelectorAll('button').find(row=>row.textContent==='Refresh sessions')!;
  fire(refresh,'click');await settle();
  expect(fixture.sheet.textContent).toContain('No scientific sessions are running');
  expect(calls.some(call=>call.init?.method==='POST'||call.init?.method==='DELETE')).toBe(false);
});

test('unrelated session failures do not claim that freeing memory is the remedy',async()=>{
  request.mockRejectedValueOnce(new Error('The integration is unavailable'));
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  expect(button('Review running sessions').hidden).toBe(true);
  expect(fixture.window.textContent).toContain('The integration is unavailable');
});


test('array save failure recovers its staged identity and retries publication without another export or Python call', async () => {
  const editable = structuredClone(cases.valid);
  editable.layers[0] = { ...editable.layers[0], editableInput: 'labels' } as typeof editable.layers[0];
  let outcome: object | undefined;
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, init) => {
    if (!path.endsWith('/request')) return original(path, init);
    calls.push({ path, init });
    const command = body(init).request;
    if (command.op === 'save-resource') {
      outcome = { request: command.request, state: 'staged', resource: null, layer: command.layer };
      return new Response(JSON.stringify({ ok: false, error: 'Controlled storage failure' }), { status: 503 });
    }
    if (command.op === 'save-retry') {
      expect(command.request).toBe((outcome as {request: string}).request);
      outcome = { ...outcome, state: 'saved', resource: { ...saved.resource, version: 3 } };
      return reply({ arraySave: outcome });
    }
    return reply({ frame: editable, arraySave: outcome });
  });
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  fire(button(`Save array changes to ${editable.layers[0].name}`), 'click'); await settle();
  expect(fixture.window.textContent).toContain('Controlled storage failure');
  expect(button('Run code').disabled).toBe(true);
  fire(button('Check array save'), 'click'); await settle();
  expect(fixture.window.textContent).toContain('same values without running');
  fire(button('Retry saving these array changes'), 'click'); await settle();
  expect(button('Open saved revision 3')).toBeTruthy();
  const ops = calls.filter(c => c.path.endsWith('/request')).map(c => body(c.init).request.op);
  expect(ops.filter(op => op === 'save-resource')).toHaveLength(1);
  expect(ops.filter(op => op === 'save-retry')).toHaveLength(1);
  expect(ops).not.toContain('python');
});

test('a successful HTTP reply without an accepted save receipt does not claim saved', async () => {
  const editable = structuredClone(cases.valid);
  editable.layers[0] = { ...editable.layers[0], editableInput: 'labels' } as typeof editable.layers[0];
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, init) => {
    if (!path.endsWith('/request')) return original(path, init);
    return body(init).request.op === 'save-resource' ? reply({}) : reply({ frame: editable });
  });
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  fire(button(`Save array changes to ${editable.layers[0].name}`), 'click'); await settle();
  expect(fixture.window.textContent).toContain('No array save outcome was confirmed');
  expect(fixture.window.textContent).not.toContain('Array changes saved.');
  expect(button('Check array save').hidden).toBe(false);
  expect(button('Run code').disabled).toBe(true);
});

test.each(['conflict', 'failed'])('terminal %s saves never offer retry or unchanged-data assurances', async state => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, init) => {
    if (!path.endsWith('/request')) return original(path, init);
    return reply({ frame: cases.valid, arraySave: { state, request: 'save-1234567890123456', resource: null, layer: 'layer' } });
  });
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  expect(button('Retry saving these array changes').hidden).toBe(true);
  expect(fixture.window.textContent).not.toContain('Private view open');
  expect(fixture.window.textContent).toContain(state === 'conflict' ? 'A newer revision was accepted' : 'Array save failed');
});

test.each(['same-document', 'other-document'])('conflict recovery opens only the originating document: %s', async kind => {
  const original = request.getMockImplementation()!;
  const latest = { ...saved.resource, version: 3, ...(kind === 'other-document' ? { id:'p'+'b'.repeat(32) } : {}) };
  request.mockImplementation(async (path, init) => {
    if (!path.endsWith('/request')) return original(path, init);
    return reply({ frame: cases.valid, arraySave: { state:'conflict', request:'save-1234567890123456',
      resource:null, layer:'layer', latest } });
  });
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  expect(fixture.window.textContent).toContain('Your edits remain in this private view');
  expect(button('Retry saving these array changes').hidden).toBe(true);
  const open = button('Open newer saved revision 3');
  if (kind === 'other-document') { expect(open).toBeUndefined(); return; }
  expect(open).toBeTruthy();
  fire(open,'click'); await settle();
  expect(fetch).toHaveBeenLastCalledWith(`/api/scientific-projects/${saved.resource.id}?session=origin&revision=3`, expect.objectContaining({ signal:expect.any(AbortSignal) }));
  expect(calls.some(call=>call.init?.method==='DELETE')).toBe(false);
});

test.each(['conflict','failed'])('session inventory distinguishes terminal array outcome %s', async state => {
  request.mockImplementationOnce(async()=>reply({ reviews:[{ review, document:saved.resource, tab:'tab-one', state:'idle',
    arraySave:{state,request:'save-1234567890123456',resource:null,layer:'layer'} }] }));
  openScientificSessions(); await settle();
  expect(fixture.sheet.textContent).toContain(state==='conflict'?'Your edits remain in this private view until Stop':'Array save failed');
  if(state==='failed')expect(fixture.sheet.textContent).not.toContain('accepted first');
});

test('remember saves applied display only after acknowledgement; failure does not claim success',async()=>{
 const frame={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,viewKey:'raw',viewGeometry:preferences.valid.layers[0].geometry}))};
 fixture.paint=frame;
 const save=pending<void>(),remember=vi.fn(()=>save.promise);
 configureScientificDocuments({prepare:async()=>{},transport:()=>({request,check}),remember});
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button('Remember this view'),'click');await settle();
 expect(remember).toHaveBeenCalledWith('origin','placement',saved.resource,expect.objectContaining({schema:1,layers:[expect.objectContaining({key:'raw'})]}));
 expect(fixture.window.textContent).not.toContain('View remembered for this window.');
 save.resolve();await settle();
 expect(fixture.window.textContent).toContain('View remembered for this window.');
 remember.mockImplementation(async()=>{throw new Error('View not remembered yet. Draft kept.');});
 fire(button('Forget remembered view'),'click');await settle();
 expect(fixture.window.textContent).toContain('Draft kept.');
 expect(fixture.window.textContent).not.toContain('Remembered view removed.');
});

test('unbound layers explain why complete view cannot be remembered',async()=>{
 const remember=vi.fn();configureScientificDocuments({prepare:async()=>{},transport:()=>({request,check}),remember});
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button('Remember this view'),'click');await settle();
 expect(remember).not.toHaveBeenCalled();expect(fixture.window.textContent).toContain('temporary layers');
});

test('starting default is explicit and cannot reset an existing live session',async()=>{
 await openStudyScientific(saved);fire(button('Start with default view'),'click');await settle();
 expect(body(calls.find(call=>call.path==='/api/scientific-reviews'&&call.init?.method==='POST')!.init).defaultView).toBe(true);
 expect(button('Start with default view').disabled).toBe(true);
});

test.each([false,true])('explicit conflict copy preserves request and opens a separate document, captured=%s', async copyCaptured => {
  const original=request.getMockImplementation()!, ops:Record<string,unknown>[]=[];
  const resource={...saved.resource,id:'p'+'c'.repeat(32),version:1};
  request.mockImplementation(async(path,init)=>{
    if(!path.endsWith('/request'))return original(path,init);
    const command=body(init).request; ops.push(command);
    if(command.op==='save-copy')return reply({arraySave:{state:'copied',request:'save-1234567890123456',resource,layer:'layer'}});
    return reply({frame:cases.valid,arraySave:{state:'conflict',request:'save-1234567890123456',resource:null,layer:'layer',copyCaptured}});
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  expect(ops.some(op=>op.op==='save-copy')).toBe(false);
  fire(button('Save as a separate document'),'click');await settle();
  expect(ops.filter(op=>op.op==='save-copy')).toEqual([{op:'save-copy',request:'save-1234567890123456',frame:cases.valid.frame}]);
  expect(button('Save as a separate document').hidden).toBe(true);
  expect(fixture.window.textContent).toContain('other private layer changes and Python variables were not saved');
  fire(button('Open saved copy'),'click');await settle();
  expect(fetch).toHaveBeenLastCalledWith(`/api/scientific-projects/${resource.id}?session=origin&revision=1`,expect.objectContaining({signal:expect.any(AbortSignal)}));
  expect(calls.some(call=>call.init?.method==='DELETE')).toBe(false);
});

test('confirmed save conflict displays recovery immediately without repeating the save', async()=>{
 const original=request.getMockImplementation()!,ops:string[]=[];
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 let conflict=false;
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;ops.push(command.op);
  if(command.op==='save-resource'){
   conflict=true;return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_CONFLICT',error:'Another edit was accepted first.'}),{status:409});
  }
  return reply({frame:editable,...(conflict?{arraySave:{state:'conflict',request:'save-1234567890123456',resource:null,layer:'layer',copyCaptured:false}}:{})});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button(`Save array changes to ${editable.layers[0].name}`),'click');await settle();
 expect(button('Save as a separate document').hidden).toBe(false);
 expect(ops.filter(op=>op==='save-resource')).toHaveLength(1);
 expect(fixture.window.textContent).toContain('Another edit was accepted first');
 expect(button('Retry saving these array changes').hidden).toBe(true);
});

test.each(['failed','missing'])('automatic conflict refresh %s keeps explicit recovery and blocks another save',async failure=>{
 const original=request.getMockImplementation()!,ops:string[]=[];
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 let conflict=false;
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;ops.push(command.op);
  if(command.op==='save-resource'){
   conflict=true;return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_CONFLICT',error:'Another edit was accepted first.'}),{status:409});
  }
  if(conflict&&failure==='failed')throw new Error('controlled refresh failure');
  return reply({frame:editable});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button(`Save array changes to ${editable.layers[0].name}`),'click');await settle();
 expect(button('Check array save').hidden).toBe(false);
 expect(button(`Save array changes to ${editable.layers[0].name}`).disabled).toBe(true);
 expect(ops.filter(op=>op==='save-resource')).toHaveLength(1);
 expect(fixture.window.textContent).toContain('Another edit was accepted first');
});

test.each(['conflict','staged','saved','copied'])('stopping a session retains accepted links and clears obsolete private recovery: %s',async state=>{
 const original=request.getMockImplementation()!;
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  return reply({frame:cases.valid,arraySave:{state,request:'save-1234567890123456',resource:['saved','copied'].includes(state)?saved.resource:null,layer:'layer'}});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 expect(fixture.window.querySelectorAll('summary').some(item=>item.textContent==='Continue with saved output')).toBe(['saved','copied'].includes(state));
 fire(button('Stop scientific session'),'click');await settle();
 expect(fixture.window.textContent).not.toContain('Your edits remain in this private view');
 if(state==='saved')expect(button('Open saved revision 2')).toBeTruthy();
 else if(state==='copied')expect(button('Open saved copy')).toBeTruthy();
 else {
  expect(fixture.window.textContent).toContain('Check Project Data for completed saves');
  expect(button('Save as a separate document').hidden).toBe(true);
  expect(button('Retry saving these array changes').hidden).toBe(true);
 }
});

test('playback owns view interaction until Stop while saved data and worker Stop remain available',async()=>{
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 expect(button('Play slices').disabled).toBe(false);
 fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Playback mode')!.value='once';
 fire(button('Play slices'),'click');
 expect(button('Remember this view').disabled).toBe(true);
 expect(button('Refresh preview').disabled).toBe(true);
 expect(button('Run code').disabled).toBe(true);
 expect(button('Stop scientific session').disabled).toBe(false);
 fire(button('Stop playback'),'click');
 expect(button('Remember this view').disabled).toBe(false);expect(button('Refresh preview').disabled).toBe(false);
 expect(calls.filter(call=>call.init?.body && body(call.init)?.request?.op==='save-resource')).toHaveLength(0);
});

test('document playback waits for exact canvas paint and keeps other editing disabled',async()=>{
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button('View'),'click');
 for(const section of fixture.window.querySelectorAll('section'))Object.assign(section,{isConnected:true});
 vi.useFakeTimers();let serial=0;
 const original=request.getMockImplementation()!;
 request.mockImplementation(async(path,init)=>{
  if(path.includes('/frames/'))return new Response(new Uint8Array(32*24*4));
  if(init?.body && body(init).request?.op==='change'){
   calls.push({path,init});const change=body(init).request.request;
   const frame=structuredClone(fixture.paint);frame.frame=(++serial).toString(16).padStart(64,'0');
   frame.dimensions.steps=change.value.steps;
   frame.dimensions.point=frame.dimensions.steps.map((step,axis)=>frame.dimensions.ranges[axis][0]+step*frame.dimensions.ranges[axis][2]);
   return reply({frame});
  }
  return original(path,init);
 });
 fixture.shown.mockImplementation(frame=>{fixture.paint=frame;fixture.waiting=frame.frame;});
 const mode=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Playback mode')!;
 mode.value='loop';fire(button('Play slices'),'click');await vi.advanceTimersByTimeAsync(2500);await settle();
 expect(serial).toBe(1);expect(button('Stop playback').disabled,fixture.window.textContent).toBe(false);
 expect(button('Run code').disabled).toBe(true);expect(button('Remember this view').disabled).toBe(true);
 fixture.waiting=null;fixture.drawn();await vi.advanceTimersByTimeAsync(500);await settle();
 expect(serial).toBe(2);fire(button('Stop playback'),'click');
 fixture.waiting=null;fixture.drawn();await vi.advanceTimersByTimeAsync(2500);expect(serial).toBe(2);
 expect(button('Remember this view').disabled).toBe(false);
});

test('history retry reconciles only the exact undispatched Python cell and preserves its newer draft', async () => {
  fixture.invalidated.mockImplementation(() => { fixture.waiting = 'invalid'; });
  fixture.shown.mockImplementation(() => { fixture.waiting = null; });
  const original = request.getMockImplementation()!;
  let submitted: { cell: string; source: string } | null = null;
  let unrelated = true, executions = 0;
  request.mockImplementation(async (path: string, init?: RequestInit) => {
    const command = path.endsWith('/request') ? body(init).request : null;
    if (command?.op === 'python-resources') {
      submitted = { cell: command.cell, source: command.source }; executions++;
      return new Response(JSON.stringify({ ok: false, error: 'Admission history not saved', next: 'Retry saving history' }), { status: 503 });
    }
    if (command?.op === 'history-save') return reply({ history: { id: 'p' + 'b'.repeat(32), day: 'origin', version: 2, picture: null },
      pythonRecovery: { id: unrelated ? 'different-cell' : submitted!.cell, source: submitted!.source, state: 'not-dispatched' } });
    return original(path, init);
  });
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  const input = fixture.window.querySelectorAll('textarea').find(item => item.getAttribute('aria-label') === 'Python code')!;
  input.value = 'never_run()'; fire(input, 'input'); fire(button('Run code'), 'click'); await settle();
  input.value = 'corrected_draft()'; fire(input, 'input');
  expect(button('Run code').disabled).toBe(true);
  fire(button('Retry saving scientific history'), 'click'); await settle();
  expect(button('Run code').disabled).toBe(true);
  unrelated = false;
  fire(button('Retry saving scientific history'), 'click'); await settle();
  expect(button('Run code').disabled).toBe(false);
  expect(input.value).toBe('corrected_draft()'); expect(executions).toBe(1);
  expect(fixture.window.textContent).toContain('This code was not run');
});

test('Python binds the saved document without inventing a rendered frame when preview fails', async () => {
  const original = request.getMockImplementation()!;
  fixture.invalidated.mockImplementation(() => { fixture.waiting = 'invalid'; });
  const submitted: Record<string, unknown>[] = [];
  request.mockImplementation(async (path, init) => {
    const command = path.endsWith('/request') ? body(init).request : null;
    if (command?.op === 'render') return new Response(JSON.stringify({ok:false,error:'Preview unavailable'}),{status:503});
    if (command?.op === 'python-resources') {
      submitted.push(body(init));
      return reply({console:{format:'scientific-console-v1',sequence:1,stdout:'',stderr:'',result:'7',error:'',truncated:false,privatePreview:true}});
    }
    return original(path, init);
  });
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  expect(fixture.shown).not.toHaveBeenCalled();
  const input = fixture.window.querySelector('textarea')!; input.value='7'; fire(input,'input');
  expect(button('Run code').disabled).toBe(false);
  fire(button('Run code'),'click'); await settle();
  expect(submitted).toHaveLength(1);
  expect(submitted[0]).toMatchObject({draw:null,request:{op:'python-resources',resource:saved.resource,source:'7'}});
  expect(submitted[0]).not.toHaveProperty('request.frame');
  expect(fixture.window.textContent).toContain('Finished. Changes remain in the private view.');
  expect(fixture.shown).not.toHaveBeenCalled();
});

test.each(['python','python-resources'])('session recovery displays the %s result without rerunning code', async operation => {
  request.mockImplementation(async () => reply({reviews:[{review,document:saved.resource,tab:'tab-one',state:'idle',
    completed:{operation,reply:{console:{format:'scientific-console-v1',sequence:1,stdout:'',stderr:'',result:'73',error:'',truncated:false,privatePreview:true}}}}]}));
  openScientificSessions(); await settle();
  expect(fixture.sheet.textContent).toContain('Last completed operation');
  expect(fixture.sheet.textContent).toContain('73');
  expect(fixture.sheet.textContent).not.toContain('A control change completed');
  expect(request.mock.calls.every(([,init]) => !init?.method || init.method==='GET')).toBe(true);
});


test('saving a declared resource stays available when the rendered preview is unavailable', async () => {
  const editable = structuredClone(cases.valid);
  editable.layers[0] = {...editable.layers[0],editableInput:'labels'} as typeof editable.layers[0];
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, init) => {
    if (!path.endsWith('/request')) return original(path,init);
    calls.push({path,init});
    const command = body(init).request;
    if (command.op === 'save-resource') {
      expect(body(init).draw).toBeNull();
      expect(command.resource).toEqual(saved.resource);
      expect(command.input).toBe('labels');
      expect(command).not.toHaveProperty('frame');
      return reply({arraySave:{request:command.request,state:'saved',resource:{...saved.resource,version:3},layer:command.layer}});
    }
    return reply({frame:editable});
  });
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  // Lost paint is not lost resource authority. The last declared layer remains
  // subject to actual worker binding validation when the save is dispatched.
  fixture.waiting = editable.frame;
  fire(button('Refresh preview'),'click'); await settle();
  const save = button(`Save array changes to ${editable.layers[0].name}`);
  expect(save.disabled).toBe(false);
  fire(save,'click'); await settle();
  expect(button('Open saved revision 3')).toBeTruthy();
});


test('resource conflict copy is enabled without a preview and status checks do not render',async()=>{
 const original=request.getMockImplementation()!,ops:string[]=[];
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 let identity='';
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;ops.push(command.op);
  if(command.op==='save-resource'){
   identity=command.request;fixture.waiting=editable.frame;
   return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_CONFLICT',error:'Another edit was accepted first.'}),{status:409});
  }
  if(command.op==='save-status')return reply({arraySave:{state:'conflict',request:identity,resource:null,layer:'layer',copyCaptured:false,copyNeedsPreview:false}});
  if(command.op==='save-copy'){
   expect(command.frame).toBeNull();expect(body(init).draw).toBeNull();
   return reply({arraySave:{state:'copied',request:identity,resource:{...saved.resource,id:'p'+'b'.repeat(32),version:1},layer:'layer'}});
  }
  return reply({frame:editable});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button(`Save array changes to ${editable.layers[0].name}`),'click');await settle();
 expect(button('Save as a separate document').disabled).toBe(false);
 fire(button('Save as a separate document'),'click');await settle();
 expect(button('Open saved copy')).toBeTruthy();
 expect(ops).toEqual(['render','save-resource','save-status','save-copy']);
});


test('a confirmed pre-capture refusal restores editing without discarding private work',async()=>{
 const original=request.getMockImplementation()!;
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  calls.push({path,init});const command=body(init).request;
  if(command.op==='save-resource')return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_NOT_STARTED',error:'Correct the changed calibration; private work remains.'}),{status:409});
  return reply({frame:editable});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 fire(button(`Save array changes to ${editable.layers[0].name}`),'click');await settle();
 expect(button('Check array save').hidden).toBe(true);
 expect(button(`Save array changes to ${editable.layers[0].name}`).disabled).toBe(false);
 expect(fixture.window.textContent).toContain('private work remains');
 expect(calls.some(call=>call.init?.method==='DELETE')).toBe(false);
});


test.each([null, 'missing'])('checking an unconfirmed save distinguishes authoritative absence from %s',async outcome=>{
 const original=request.getMockImplementation()!,ops:string[]=[];
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;ops.push(command.op);
  if(command.op==='save-resource')throw new Error('Lost admission response');
  if(command.op==='save-status')return reply(outcome===null?{arraySave:null}:{});
  return reply({frame:editable});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 const save=button(`Save array changes to ${editable.layers[0].name}`);
 fire(save,'click');await settle();expect(save.disabled).toBe(true);
 fire(button('Check array save'),'click');await settle();
 expect(button(`Save array changes to ${editable.layers[0].name}`).disabled).toBe(outcome!==null);
 expect(button('Check array save').hidden).toBe(outcome===null);
 expect(ops).toEqual(['render','save-resource','save-status']);
 expect(calls.some(call=>call.init?.method==='DELETE')).toBe(false);
});

test('refusing an uncaptured conflict copy permits correction and a new save',async()=>{
 const original=request.getMockImplementation()!;
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 let identity='';
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;
  if(command.op==='save-resource'){
   identity=command.request;
   return reply({arraySave:{state:'conflict',request:identity,resource:null,layer:'layer',copyCaptured:false,copyNeedsPreview:false}});
  }
  if(command.op==='save-copy')return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_NOT_STARTED',error:'Correct the layer; private work remains.'}),{status:409});
  return reply({frame:editable});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 const save=button(`Save array changes to ${editable.layers[0].name}`);
 fire(save,'click');await settle();fire(button('Save as a separate document'),'click');await settle();
 expect(button(`Save array changes to ${editable.layers[0].name}`).disabled).toBe(false);expect(button('Check array save').hidden).toBe(true);
 expect(fixture.window.textContent).toContain('private work remains');
 expect(calls.some(call=>call.init?.method==='DELETE')).toBe(false);
});

test('Python assistance binds the original resource without executing a cell or requiring pixels', async()=>{
 const original=request.getMockImplementation()!, submitted:Record<string,unknown>[]=[];
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const requestBody=body(init), command=requestBody.request; submitted.push(requestBody);
  if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:cases.valid.layers},arraySave:null});
  if(command.op==='python-assist')return reply({assistance:{format:'scientific-python-assist-v1',mode:'complete',expression:'viewer.add_im',matches:['add_image'],text:'No code was run.',truncated:false}});
  throw new Error('Lookup must not execute or render');
 });
 await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
 const editor=fixture.window.querySelectorAll('textarea').find(v=>v.getAttribute('aria-label')==='Python code')!;
 Object.assign(editor,{value:'viewer.add_im',selectionStart:13,selectionEnd:13});fire(editor,'input');
 fire(button('Complete name'),'click');await settle();
 expect(button('add_image')).toBeDefined();
 expect(submitted.at(-1)).toMatchObject({draw:null,request:{op:'python-assist',resource:saved.resource,mode:'complete',expression:'viewer.add_im'}});
 expect(submitted.map(row=>(row.request as {op:string}).op)).toEqual(['layer-inventory','python-assist']);
 expect(fixture.shown).not.toHaveBeenCalled();
});

test('starting without preview exposes exact layers and permits resource Python and save without pixels',async()=>{
 const original=request.getMockImplementation()!,ops:string[]=[];
 const layers=cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}));
 request.mockImplementation(async(path,init)=>{
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;ops.push(command.op);
  if(command.op==='layer-inventory'){
   expect(command.resource).toEqual(saved.resource);expect(body(init).draw).toBeNull();
   return reply({inventory:{format:'scientific-layers-v1',layers},arraySave:null});
  }
  if(command.op==='python-resources')return reply({console:{format:'scientific-console-v1',sequence:1,stdout:'native data',stderr:'',result:'',error:'',truncated:false,privatePreview:true}});
  if(command.op==='save-resource')return reply({arraySave:{state:'saved',request:command.request,resource:{...saved.resource,version:3},layer:layers[0].id}});
  throw new Error('Unexpected rendered operation');
 });
 await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
 expect(fixture.shown).not.toHaveBeenCalled();
 expect(fixture.window.textContent).toContain('without rendering a preview');
 expect(button(`Save array changes to ${layers[0].name}`).disabled).toBe(false);
 expect(button('Apply view changes').disabled).toBe(true);
 const editor=fixture.window.querySelectorAll('textarea').find(v=>v.getAttribute('aria-label')==='Python code')!;
 editor.value='print(inputs)';fire(editor,'input');fire(button('Run code'),'click');await settle();
 expect(fixture.window.textContent).toContain('native data');
 fire(button(`Save array changes to ${layers[0].name}`),'click');await settle();
 expect(button('Open saved revision 3')).toBeTruthy();
 expect(ops).toEqual(['layer-inventory','python-resources','layer-inventory','save-resource']);
 expect(fixture.shown).not.toHaveBeenCalled();
});

test('resuming without preview revokes old pixels while preserving pending save recovery',async()=>{
 const original=request.getMockImplementation()!;
 const editable={...cases.valid,layers:cases.valid.layers.map(layer=>({...layer,editableInput:'raw'}))};
 let inventories=0;
 request.mockImplementation(async(path,init)=>{
  if(path==='/api/scientific-reviews'&&!init?.method)return reply({reviews:inventories++?[{review,placement:'placement',document:saved.resource}]:[]});
  if(!path.endsWith('/request'))return original(path,init);
  const command=body(init).request;
  if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:editable.layers},arraySave:{state:'staged',request:'save-1234567890123456',resource:null,layer:editable.layers[0].id}});
  return reply({frame:editable});
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 expect(fixture.shown).toHaveBeenCalledTimes(1);
 fixture.invalidated.mockImplementation(()=>{fixture.waiting=editable.frame;});
 fire(button('Start without preview'),'click');await settle();
 expect(fixture.waiting).toBe(editable.frame);
 expect(button('Apply view changes').disabled).toBe(true);
 expect(button('Run code').disabled).toBe(true);
 expect(button('Retry saving these array changes').hidden).toBe(false);
 expect(fixture.shown).toHaveBeenCalledTimes(1);
});

test('preview uses the visible window and device scale without exceeding the renderer bound', async () => {
  Object.assign(window, { devicePixelRatio: 2 });
  await openStudyScientific(saved);
  const canvas = fixture.window.querySelector('canvas')!;
  canvas.style.width = '800px'; canvas.style.height = '600px';
  fire(button('Open session'), 'click'); await settle();
  const render = calls.find(call => call.init?.body && body(call.init).request?.op === 'render')!;
  expect(body(render.init).request).toEqual({ op: 'render', width: 1024, height: 768 });
  expect(fixture.window.textContent).toContain('Scientific data keeps its original resolution');
  canvas.style.width = '240px'; canvas.style.height = '180px';
  fire(button('Refresh preview'), 'click'); await settle();
  const renders = calls.filter(call => call.init?.body && body(call.init).request?.op === 'render');
  expect(body(renders.at(-1)!.init).request).toEqual({ op: 'render', width: 480, height: 360 });
});

test.each(['resize', 'device scale'])('a late preview after %s is refused and a fresh preview can recover', async change => {
  const pixels = pending<Response>(), original = request.getMockImplementation()!;
  request.mockImplementation((path:string, init?:RequestInit) => path.includes('/frames/') ? pixels.promise : original(path, init));
  Object.assign(window, { devicePixelRatio: 1 });
  await openStudyScientific(saved);
  const canvas = fixture.window.querySelector('canvas')!;
  canvas.style.width = '320px'; canvas.style.height = '240px';
  fire(button('Open session'), 'click'); await settle();
  if (change === 'resize') canvas.style.width = '640px';
  else Object.assign(window, { devicePixelRatio: 2 });
  pixels.resolve(new Response(new Uint8Array(32 * 24 * 4))); await settle();
  expect(fixture.shown).not.toHaveBeenCalled();
  expect(fixture.window.textContent).toContain('private work is preserved');
  request.mockImplementation(original);
  fire(button('Refresh preview'), 'click'); await settle();
  expect(fixture.shown).toHaveBeenCalledTimes(1);
  expect(calls.filter(call => call.path === '/api/scientific-reviews' && call.init?.method === 'POST')).toHaveLength(1);
});

test('late label-edit pixels after resize recover the recorded edit without repeating it', async () => {
  const frame = structuredClone(cases.valid);
  frame.layers[0] = {...frame.layers[0], type:'labels', editableInput:'labels'} as typeof frame.layers[0];
  fixture.paint = frame;
  const original = request.getMockImplementation()!, delayed = pending<Response>();
  let editId = '', dispatched = 0, checked = 0;
  request.mockImplementation(async(path,init) => {
    if (!path.endsWith('/request')) return original(path,init);
    const command = body(init).request;
    if (command.op === 'labels') { editId = command.request; dispatched++; return delayed.promise; }
    if (command.op === 'edit-status') { checked++; return reply({edit:{request:editId,state:'applied',changed:true,label:null,message:'Undo applied'}}); }
    return reply({frame});
  });
  await openStudyScientific(saved);
  const canvas = fixture.window.querySelector('canvas')!;
  canvas.style.width='320px'; canvas.style.height='240px';
  fire(button('Open session'),'click'); await settle();
  fire(button('Edit'),'click');
  fire(button('Undo label edit'),'click'); await settle();
  expect(dispatched).toBe(1);
  canvas.style.width='640px';
  delayed.resolve(reply({frame,edit:{request:editId,state:'applied',changed:true,label:null,message:'Undo applied'}})); await settle();
  expect(fixture.shown).toHaveBeenCalledTimes(1);
  expect(button('Check edit outcome').hidden).toBe(false);
  fire(button('Check edit outcome'),'click'); await settle();
  expect(checked).toBe(1); expect(dispatched).toBe(1);
  expect(fixture.shown).toHaveBeenCalledTimes(2);
  expect(fixture.window.textContent).toContain('Label edit applied to this private view');
  expect(button('Check edit outcome').hidden).toBe(true);
});


test('switching to resource-only mode clears previous preview pixels and its resolution claim', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async(path,init) => path.endsWith('/request') && body(init).request.op === 'layer-inventory'
    ? reply({inventory:{format:'scientific-layers-v1',layers:cases.valid.layers},arraySave:null}) : original(path,init));
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  expect(fixture.window.textContent).toContain('Preview: 32 × 24 pixels');
  fire(button('Start without preview'),'click'); await settle();
  expect(fixture.window.querySelector('canvas')!.getAttribute('width')).toBe('0');
  expect(fixture.window.textContent).not.toContain('Preview: 32 × 24 pixels');
  expect(fixture.window.textContent).toContain('No current preview.');
  expect(fixture.shown).toHaveBeenCalledTimes(1);
});

test('saves all bound arrays together without including a new unbound layer or requiring pixels', async () => {
  const base = structuredClone(cases.valid.layers[0]);
  const layers = [{...base,id:'a'.repeat(32),name:'First',editableInput:'first'},
    {...base,id:'b'.repeat(32),name:'Second',editableInput:'second'},
    {...base,id:'c'.repeat(32),name:'Unbound',editableInput:undefined}];
  const commands: Record<string,unknown>[]=[];
  request.mockImplementation(async (_path, init) => {
    if (!init?.body || !body(init).request) return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers},arraySave:null});
    if(command.op==='save-resources')return reply({arraySave:{state:'saved',request:command.request,
      resource:{...saved.resource,version:3},layer:layers[0].id,layers:layers.slice(0,2).map(layer=>layer.id)}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Save all 2 editable layers'),'click');await settle();
  expect(commands.map(command=>command.op)).toEqual(['layer-inventory','save-resources']);
  expect(commands[1].layers).toEqual([{layer:layers[0].id,input:'first'},{layer:layers[1].id,input:'second'}]);
  expect(commands[1].resource).toEqual(saved.resource);
  expect(button('Open saved revision 3')).toBeTruthy();
  expect(fixture.window.textContent).toContain('for 2 layers');
});

test('drafts from Python and plugins are listed, saved together in one revision and confirmed',async()=>{
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Cells',type:'labels',scale:[1,1,1],translate:[0,0,0]};
  const drafts=[{name:'objects',family:'table',semantic:'object-table',key:'object_id',rows:3,objectSpace:'labels',
      columns:[{name:'object_id'},{name:'area'}],producer:{kind:'python'},parts:[{part:'object_id',bytes:24,sha256:'0'.repeat(64)},{part:'area',bytes:24,sha256:'1'.repeat(64)}]},
    {name:'movie',family:'artifact',semantic:'animation-document',mediaType:'application/json',fileName:'movie.json',description:null,
      compatibility:null,producer:{kind:'python'},parts:[{part:'content',bytes:2048,sha256:'2'.repeat(64)}]}];
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
    if(command.op==='family-drafts')return reply({drafts,arraySave:null});
    if(command.op==='save-families')return reply({arraySave:{kind:'families',state:'saved',request:command.request,drafts:command.names,
      resource:{...saved.resource,version:3}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Check drafts from Python and plugins'),'click');await settle();
  expect(fixture.window.textContent).toContain('objects · table · 3 rows, 2 columns · 48 bytes');
  expect(fixture.window.textContent).toContain('movie · file · 2.0 KiB');
  fire(button('Save all 2 drafts together'),'click');await settle();
  expect(commands.map(command=>command.op)).toEqual(['layer-inventory','family-drafts','save-families']);
  expect(commands[2]).toMatchObject({resource:saved.resource,names:['objects','movie']});
  expect(button('Open saved revision 3')).toBeTruthy();
  expect(fixture.window.textContent).toContain('2 drafts from Python and plugins saved together as revision 3: objects, movie.');
});

test('a draft save receipt with layers or an unknown draft set is refused',()=>{
  const receipt={kind:'families',state:'saved',request:'r'.repeat(20),drafts:['objects'],resource:{...saved.resource,version:3}};
  for(const bad of [{...receipt,layer:'a'.repeat(32)},{...receipt,drafts:[]},{...receipt,drafts:['Objects']},{...receipt,drafts:undefined},
                    {...receipt,kind:'new-output',layer:'a'.repeat(32)},{...receipt,state:'copied'}])
    expect(()=>scientificArraySave(bad)).toThrow();
  expect(scientificArraySave(receipt)?.drafts).toEqual(['objects']);
});

test('new output requires scientific meaning, saves without pixels, and keeps other private work available',async()=>{
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Derived objects',type:'labels',scale:[1,1,1],translate:[0,0,0]};
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
    if(command.op==='save-output')return reply({arraySave:{kind:'new-output',state:'saved',request:command.request,layer:layer.id,
      resource:{...saved.resource,id:'p'+'e'.repeat(32),version:1}}});
    if(command.op==='save-release')return reply({arraySave:null});
    throw new Error('Unexpected request');
  });
  await openStudyScientific({ ...saved, display: { scientific: scientificViewPreferences(preferences.valid) } });fire(button('Start without preview'),'click');await settle();
  fire(button('Save as new scientific document'),'click');await settle();
  expect(commands.map(command=>command.op)).toEqual(['layer-inventory']);
  expect(fixture.window.textContent).toContain('Choose the scientific meaning');
  const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Derived objects')!;
  const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Derived objects')!;
  meaning.value='instance-labels';fire(meaning,'change');axes.value='z, y, x';fire(axes,'input');
  fire(button('Refresh layers'),'click');await settle();
  expect(fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Derived objects')!.value).toBe('z, y, x');
  fire(button('Save as new scientific document'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-output',resource:saved.resource,layer:layer.id,meaning:{semantic:'instance-labels',axes:['z','y','x']}});
  const openOutput = button('Open saved output');
  expect(openOutput).toBeTruthy();
  expect(fixture.window.textContent).toContain('New scientific output saved. Your original document and private session are unchanged.');
  const continuation = fixture.window.querySelectorAll('details').find(item => item.querySelector('summary')?.textContent === 'Continue with saved output');
  expect(continuation).toBeTruthy();
  expect(continuation!.textContent).toContain('Derived objects');
  const discover = vi.fn(async () => reply({source:{id:'p'+'e'.repeat(32),session:'origin',revision:1,digest:'f'.repeat(64)},tools:[]}));
  vi.stubGlobal('fetch', discover);
  fire(continuation!.querySelectorAll('button').find(item=>item.textContent==='Find interactive tools')!,'click');await settle();
  expect(discover.mock.calls[0]).toEqual(['/api/scientific-projects/p'+'e'.repeat(32)+'/tools?session=origin&revision=1']);
  const original = fixture.window.querySelectorAll('details').find(item => item.querySelector('summary')?.textContent === 'Continue with this window’s saved input');
  expect(original).toBeTruthy();
  fire(original!.querySelectorAll('button').find(item=>item.textContent==='Find interactive tools')!,'click');await settle();
  expect(discover.mock.calls[1]).toEqual(['/api/scientific-projects/p'+'a'.repeat(32)+'/tools?session=origin&revision=2']);
  expect(original!.textContent).toContain('another input revision');
  vi.stubGlobal('fetch', vi.fn(async()=>reply({project:{id:'p'+'e'.repeat(32),session:'origin',revision:1,document:{kind:'scene',state:{scientificView:{}}}}})));
  fire(button('Continue this private session'),'click');await settle();
  expect(commands.slice(-2).map(command=>command.op)).toEqual(['save-release','layer-inventory']);
  expect(button('Save as new scientific document').disabled).toBe(false);
  fire(openOutput,'click');await settle();
  const newWindow=JSON.parse(fixture.window.dataset.studyRetained);
  expect(newWindow.resource.id).toBe('p'+'e'.repeat(32));
  expect(newWindow.display).toBeUndefined();
  expect(newWindow.placementId).not.toBe(saved.placementId);
});

test.each(['image', 'points'])('new %s output carries declared axes but requires explicit meaning and reveals invalid fields', async type => {
  const layer = {...structuredClone(cases.valid.layers[0]), id:'d'.repeat(32), name:'Reader output', type,
    ...(type==='points' ? {elementCount:2} : {}), scale:[1,1,1], translate:[0,0,0], viewKey:'output',
    viewGeometry:{axes:['z','y','x'], units:['um','um','um'], affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]}};
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
    if(command.op==='save-output')return reply({arraySave:{kind:'new-output',state:'saved',request:command.request,layer:layer.id,
      resource:{...saved.resource,id:'p'+'e'.repeat(32),version:1}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Reader output')!;
  const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Reader output')!;
  const focusMeaning=vi.fn(),focusAxes=vi.fn(); Object.assign(meaning,{focus:focusMeaning});Object.assign(axes,{focus:focusAxes});
  expect(axes.value).toBe('z, y, x');expect(meaning.value).toBe('');
  fire(button('Save as new scientific document'),'click');await settle();
  expect(commands.map(command=>command.op)).toEqual(['layer-inventory']);
  expect(meaning.parentElement).toHaveProperty('open',true);expect(focusMeaning).toHaveBeenCalledOnce();
  expect(fixture.window.textContent).toContain('Reader output');expect(meaning.getAttribute('aria-invalid')).toBe('true');
  meaning.value=type==='image'?'intensity':'point-set';fire(meaning,'change');
  axes.value='z, z, x';fire(axes,'input');fire(button('Save as new scientific document'),'click');await settle();
  expect(focusAxes).toHaveBeenCalledOnce();expect(axes.getAttribute('aria-invalid')).toBe('true');
  axes.value='depth, row, column';fire(axes,'input');
  fire(button('Refresh layers'),'click');await settle();
  expect(fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Reader output')!.value).toBe('depth, row, column');
  fire(button('Save as new scientific document'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-output',meaning:{semantic:meaning.value,axes:['depth','row','column']}});
});

test('selected new outputs keep declarations through refresh and save together without preview authority',async()=>{
  const layers=['First objects','Second image'].map((name,index)=>({...structuredClone(cases.valid.layers[0]),
    id:(index?'e':'d').repeat(32),name,type:index?'image':'labels',scale:[1,1,1],translate:[0,0,0]}));
  const commands:Record<string,unknown>[]=[];
  let attempts=0;
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers},arraySave:null});
    if(command.op==='save-outputs' && attempts++===0)return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_NOT_STARTED',error:'Combined metadata is too large; choose fewer layers.'}),{status:409});
    if(command.op==='save-outputs')return reply({arraySave:{kind:'new-output',state:'saved',request:command.request,
      layer:layers[0].id,layers:layers.map(row=>row.id),resource:{...saved.resource,id:'p'+'f'.repeat(32),version:1}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Save selected new layers together'),'click');await settle();
  expect(commands.map(row=>row.op)).toEqual(['layer-inventory']);
  expect(fixture.window.textContent).toContain('Choose two to eight new layers to save together');
  for(const [index,layer] of layers.entries()){
    const choose=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')===`Include ${layer.name} in saved scene`)!;
    Object.assign(choose,{checked:true});fire(choose,'change');
    const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')===`Scientific meaning of ${layer.name}`)!;
    meaning.value=index?'probabilities':'instance-labels';fire(meaning,'change');
    const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')===`Axes of ${layer.name}`)!;
    axes.value='z, y, x';fire(axes,'input');
  }
  fire(button('Refresh layers'),'click');await settle();
  for(const choose of fixture.window.querySelectorAll('input').filter(item=>item.getAttribute('aria-label')?.startsWith('Include ')))expect(choose).toHaveProperty('checked',true);
  fire(button('Save selected new layers together'),'click');await settle();
  expect(fixture.window.textContent).toContain('Combined metadata is too large');
  for(const choose of fixture.window.querySelectorAll('input').filter(item=>item.getAttribute('aria-label')?.startsWith('Include ')))expect(choose).toHaveProperty('checked',true);
  fire(button('Save selected new layers together'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-outputs',resource:saved.resource,outputs:[
    {layer:layers[0].id,meaning:{semantic:'instance-labels',axes:['z','y','x']}},
    {layer:layers[1].id,meaning:{semantic:'probabilities',axes:['z','y','x']}}]});
  expect(commands.at(-1)).not.toHaveProperty('frame');
  expect(fixture.window.textContent).toContain('2 new layers saved together in one document');
  expect(button('Open saved output')).toBeTruthy();
});


test.each(['view-discard-arrow','view-apply-arrow','tool-discard-arrow','tool-apply-arrow',
  'view-discard-drag','view-apply-drag','tool-discard-drag','tool-apply-drag'])('camera waits for unsent settings and resumes after %s', async scenario => {
  const [editor,action,gesture] = scenario.split('-');
  const original=request.getMockImplementation()!;
  let revision=1;
  fixture.paint.dimensions.ndisplay=3;fixture.paint.dimensions.displayed=[...fixture.paint.dimensions.order];
  request.mockImplementation(async(path,init)=>{
    if(path.includes('/frames/'))return new Response(new Uint8Array(32*24*4));
    if(path.endsWith('/request')){
      calls.push({path,init});
      if(body(init).request.op==='change'){
        const frame=structuredClone(fixture.paint);frame.frame=(++revision).toString(16).padStart(64,'0');
        if(body(init).request.request.operation==='controls')frame.controls.revision++;
        fixture.paint=frame;
      }
      return reply({frame:fixture.paint});
    }
    return original(path,init);
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  const canvas=fixture.window.querySelectorAll('canvas').find(c=>c.getAttribute('aria-hidden')!=='true')!;
  Object.assign(canvas,{hasPointerCapture:(id:number)=>canvas.captured===id,focus:()=>{}});
  const camera=fixture.window.querySelectorAll('select').find(e=>e.getAttribute('aria-label')==='3D camera interaction')!;
  const move=()=>{
    if(gesture==='arrow'){
      const event=Object.assign(new Event('keydown',{cancelable:true}),{key:'ArrowRight',shiftKey:false});
      Object.defineProperty(event,'target',{value:canvas});window.dispatchEvent(event);
    }else for(const [type,x,y] of [['pointerdown',10,10],['pointerup',20,15]] as const)
      canvas.dispatchEvent(Object.assign(new Event(type,{cancelable:true}),{button:0,pointerId:1,clientX:x,clientY:y}));
  };
  const cameraCalls=()=>calls.filter(c=>c.init?.body&&body(c.init).request?.request?.operation==='camera-gesture');
  const input=fixture.window.querySelectorAll('input').find(e=>editor==='view'
    ?e.getAttribute('aria-label')==='Opacity Intensity':e.id.endsWith('d'.repeat(32)))!;
  expect(camera.disabled).toBe(false);expect(input).toBeTruthy();
  input.value='.4';fire(input,'input');expect(camera.disabled).toBe(true);
  move();await settle();expect(cameraCalls()).toHaveLength(0);expect(input.value).toBe('.4');
  if(action==='discard')fire(button(editor==='view'?'Discard unsent view changes':'Discard unsent changes'),'click');
  else if(editor==='view')fire(button('Apply view changes'),'click');else fire(input,'change');
  await settle();expect(camera.disabled).toBe(false);
  move();await settle();expect(cameraCalls()).toHaveLength(1);
  expect(fixture.window.textContent).not.toContain('while you were editing');
});

test('scientific inspector keeps the preview and live draft independent while scrolling controls', async () => {
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  const inspector=fixture.window.querySelector('.scientific-inspector')!;
  const preview=fixture.window.querySelector('.scientific-preview-column')!;
  expect(inspector).not.toBeNull(); expect(preview).not.toBeNull();
  expect(preview.contains(fixture.window.querySelector('canvas')!)).toBe(true);
  expect(inspector.contains(fixture.window.querySelector('canvas')!)).toBe(false);
  const inspection=fixture.window.querySelector('.scientific-inspection')!;
  expect(inspector.contains(inspection)).toBe(true);
  expect(preview.contains(inspection)).toBe(false);
  const opacity=inspector.querySelectorAll('input').find(input=>input.getAttribute('aria-label')==='Opacity Intensity')!;
  opacity.value='0.37'; fire(opacity,'input');
  const before=calls.length; const canvas=fixture.window.querySelector('canvas');
  fire(inspector,'scroll'); await settle();
  expect(calls).toHaveLength(before); expect(opacity.value).toBe('0.37');
  expect(fixture.window.querySelector('canvas')).toBe(canvas);
  expect(fixture.window.querySelector('.scientific-inspection')).toBe(inspection);
  expect(button('Apply view changes').disabled).toBe(false);
  fire(button('Apply view changes'),'click'); await settle();
  const sent=calls.find(call=>call.init?.body && body(call.init).request?.request?.operation==='layer-presentation');
  expect(body(sent!.init).request.request.value.opacity).toBe(.37);
});

test('scientific sections retain an unsent draft, expose its recovery and switch without scientific requests', async () => {
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  for (const panel of fixture.window.querySelectorAll('section')) Object.assign(panel, {focus: vi.fn()});
  fire(button('View'), 'click');
  const opacity=fixture.window.querySelectorAll('input').find(input=>input.getAttribute('aria-label')==='Opacity Intensity')!;
  opacity.value='0.37'; fire(opacity,'input');
  const before=calls.length, canvas=fixture.window.querySelector('canvas');
  fire(button('Tool'),'click'); await settle();
  expect(button('Tool').getAttribute('aria-selected')).toBe('true');
  expect(button('Review View changes').hidden).toBe(false);
  expect(opacity.value).toBe('0.37'); expect(calls).toHaveLength(before);
  fire(button('Review View changes'),'click'); await settle();
  expect(button('View').getAttribute('aria-selected')).toBe('true');
  expect(fixture.window.querySelector('canvas')).toBe(canvas);
  fire(button('Apply view changes'),'click'); await settle();
  expect(button('Review View changes').hidden).toBe(true);
  const sent=calls.find(call=>call.init?.body && body(call.init).request?.request?.operation==='layer-presentation');
  expect(body(sent!.init).request.request.value.opacity).toBe(.37);
});

test('scientific section keyboard navigation keeps one tab stop and does not start a session', async () => {
  await openStudyScientific(saved); const before=calls.length;
  const focus = vi.fn();
  for (const tab of fixture.window.querySelectorAll('button')) Object.assign(tab, {focus});
  fire(button('Inspect'),'keydown',{key:'End'});
  expect(button('Python').getAttribute('aria-selected')).toBe('true');
  expect(button('Python').getAttribute('tabindex')).toBe('0');
  expect(button('Inspect').getAttribute('tabindex')).toBe('-1');
  fire(button('Python'),'keydown',{key:'ArrowRight'});
  expect(button('Inspect').getAttribute('aria-selected')).toBe('true');
  fire(button('Inspect'),'keydown',{key:'ArrowLeft'});
  expect(button('Python').getAttribute('aria-selected')).toBe('true');
  fire(button('Python'),'keydown',{key:'Home'});
  expect(button('Inspect').getAttribute('aria-selected')).toBe('true');
  expect(focus).toHaveBeenCalledTimes(4);
  await settle(); expect(calls).toHaveLength(before);
});

test('Inspect and Edit explicitly own canvas actions and pending edit recovery cannot be hidden', async () => {
  const frame={...structuredClone(cases.valid),layers:cases.valid.layers.map(layer=>({...layer,type:'labels',editableInput:'labels'}))};
  fixture.paint=frame;
  const original=request.getMockImplementation()!, delayed=pending<Response>();
  let editId='';
  request.mockImplementation(async(path,init)=>{
    if (!path.endsWith('/request')) return original(path,init);
    const command=body(init).request; calls.push({path,init});
    if (command.op==='labels') {editId=command.request;return delayed.promise;}
    return reply({frame});
  });
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  const toggle=fixture.window.querySelectorAll('input').find(input=>input.getAttribute('aria-label')==='Inspect native values')!;
  Object.assign(toggle,{checked:true});fire(toggle,'change');
  const tool=fixture.window.querySelectorAll('select').find(input=>input.getAttribute('aria-label')==='Label tool')!;
  expect(tool.disabled).toBe(true);
  fire(button('Edit'),'click');expect(tool.disabled).toBe(false);expect(toggle.disabled).toBe(true);
  tool.value='paint';fire(tool,'change');await settle();  // the chosen tool is napari's own mode, set at once
  fire(button('Inspect'),'click');
  const canvas=fixture.window.querySelector('canvas')!;
  const before=calls.length;
  fire(canvas,'pointerdown',{pointerId:1,button:0,clientX:10,clientY:10});
  fire(canvas,'pointerup',{pointerId:1,button:0,clientX:12,clientY:12});await settle();
  expect(calls).toHaveLength(before);expect(tool.value).toBe('paint');expect(toggle.disabled).toBe(false);
  fire(button('Edit'),'click');fire(button('Undo label edit'),'click');await settle();
  expect(editId).not.toBe('');expect(button('Inspect').disabled).toBe(true);
  fire(button('Inspect'),'click');expect(button('Edit').getAttribute('aria-selected')).toBe('true');
  delayed.resolve(reply({frame,edit:{request:editId,state:'applied',changed:true,label:null,message:'Undo applied'}}));await settle();
  expect(button('Inspect').disabled).toBe(false);
});


test.each(['points','tracks','vectors','surface','shapes'])('new %s expose coordinate axes and complete saving without retargeting the Project',async family=>{
  const semantic=family==='points'?'point-set':family==='tracks'?'track-coordinates':family==='surface'?'surface-mesh':family==='shapes'?'shape-set':'vector-set',coordinateAxes=family==='tracks'?['t','y','x']:['z','y','x'];
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Measured coordinates',type:family,scale:[2,.5,.25],translate:[10,20,30]};
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
    if(command.op==='save-output')return reply({arraySave:{kind:'new-output',state:'saved',request:command.request,layer:layer.id,
      resource:{...saved.resource,id:'p'+'e'.repeat(32),version:1}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  expect(fixture.window.textContent).toContain('Coordinate axes in column order (3 dimensions)');
  expect(fixture.window.textContent).toContain('typed features');
  const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Measured coordinates')!;
  const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Measured coordinates')!;
  meaning.value=semantic;fire(meaning,'change');axes.value=coordinateAxes.join(', ');fire(axes,'input');
  fire(button('Save as new scientific document'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-output',resource:saved.resource,layer:layer.id,meaning:{semantic,axes:coordinateAxes}});
  expect(button('Open saved output')).toBeTruthy();
});

test.each(['points','vectors','shapes'])('empty %s is explicit and saveable without inventing detections',async family=>{
  const semantic=family==='points'?'point-set':family==='shapes'?'shape-set':'vector-set',coordinateAxes=['z','y','x'];
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Measured coordinates',type:family,elementCount:0,scale:[2,.5,.25],translate:[10,20,30]};
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
    if(command.op==='save-output')return reply({arraySave:{kind:'new-output',state:'saved',request:command.request,layer:layer.id,
      resource:{...saved.resource,id:'p'+'e'.repeat(32),version:1}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  expect(fixture.window.textContent).toContain('Coordinate axes in column order (3 dimensions)');
  expect(fixture.window.textContent).toContain('This empty result can be saved');
  expect(fixture.window.textContent).toContain(`0 ${family}`);
  const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Measured coordinates')!;
  const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Measured coordinates')!;
  meaning.value=semantic;fire(meaning,'change');axes.value=coordinateAxes.join(', ');fire(axes,'input');
  fire(button('Save as new scientific document'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-output',resource:saved.resource,layer:layer.id,meaning:{semantic,axes:coordinateAxes}});
  expect(button('Open saved output')).toBeTruthy();
});


test.each(['tracks'])('empty %s explains the current retention limitation without promising a save',async family=>{
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'No regions',type:family,elementCount:0};
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    if(body(init).request.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  expect(fixture.window.textContent).toContain(`No regions: 0 ${family==='tracks'?'track coordinates':'shapes'}`);
  expect(fixture.window.textContent).toContain('This pinned viewer cannot reopen empty Tracks');
  expect(fixture.window.textContent).not.toContain('This empty result can be saved');
  expect(fixture.window.textContent).not.toContain('Save new layer: No regions');
});

test('native inspection uses the current draw without saving work or changing camera input',async()=>{
 const original=request.getMockImplementation()!;
 request.mockImplementation(async(path,init)=>{
  if(path.endsWith('/request')&&body(init).request.op==='inspect'){
   calls.push({path,init});
   const q=body(init).request;
   return reply({inspection:{frame:q.frame,layer:q.layer,world:[10,20,30],data:[1,2,3],axes:['z','y','x'],units:['um','um','um'],dtype:'<u8',values:['18446744073709551615'],projection:'none',level:null}});
  }
  return original(path,init);
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 const choice=fixture.window.querySelectorAll('select').find(e=>e.getAttribute('aria-label')==='Inspect layer')!;
 choice.value=cases.valid.layers[0].id;
 const start=calls.length;fire(button('Inspect preview center'),'click');await settle();
 const inspection=calls.slice(start).find(c=>c.path.endsWith('/request'))!;
 expect(body(inspection.init)).toEqual({request:{op:'inspect',frame:cases.valid.frame,layer:choice.value,position:[12,16]},draw:'current-draw'});
 expect(fixture.window.textContent).toContain('18446744073709551615');
 expect(calls.slice(start)).toHaveLength(1);
 expect(button('Apply view changes').disabled).toBe(true);
 expect(button('Inspect preview center').disabled).toBe(false);
});


test('large script execution uses its bounded route with exact resource and existing cell identity', async () => {
  const original=request.getMockImplementation()!;
  request.mockImplementation(async(path,init)=>{
    if(path.endsWith('/script')){calls.push({path,init});return reply({console:{format:'scientific-console-v1',sequence:1,stdout:'42',stderr:'',result:'',error:'',truncated:false,privatePreview:true}});}
    return original(path,init);
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  const source='# calibrated µ input\n'.repeat(2000)+'print(42)';
  const input=fixture.window.querySelectorAll('textarea').find(item=>item.getAttribute('aria-label')==='Python code')!;
  input.value=source;fire(input,'input');fire(button('Run code'),'click');await settle();
  const sent=calls.filter(call=>call.path.endsWith('/script'));expect(sent).toHaveLength(1);
  const fields=body(sent[0].init);
  expect(fields.draw).toBe(null);expect(fields.request.op).toBe('python-resources');
  expect(fields.request.resource).toEqual(saved.resource);expect(fields.request.source).toBe(source);
  expect(fields.request.cell).toMatch(/^[a-z0-9_-]{16,128}$/i);
  expect(input.value).toBe(source);expect(fixture.window.textContent).toContain('42');
});

test('ordinary scientific documents bind browser draft recovery without starting a worker',async()=>{
 const values=new Map<string,string>();
 const storage:Storage={get length(){return values.size;},key:index=>[...values.keys()][index]??null,
  getItem:key=>values.get(key)??null,setItem:(key,value)=>{values.set(key,value);},removeItem:key=>{values.delete(key);},clear:()=>values.clear()};
 Reflect.apply(configureScientificDocuments,null,[{prepare:async()=>{},transport:()=>({request,check}),drafts:{storage,prefix:'scientific-document-test'}}]);
 await openStudyScientific(saved);
 const input=fixture.window.querySelector('textarea')!;input.value='unrun_scientific_draft = 31';fire(input,'input');
 expect(calls).toHaveLength(0);fixture.dispose();fixture.keys.length=0;fixture.window.replaceChildren();
 await openStudyScientific(saved);
 const restored=fixture.window.querySelector('textarea')!;Object.assign(restored,{focus:vi.fn()});
 expect(restored.value).toBe('');const recover=button('Restore script draft');expect(recover).toBeDefined();fire(recover,'click');
 expect(restored.value).toBe('unrun_scientific_draft = 31');expect(calls).toHaveLength(0);
 expect(button('Run code').disabled).toBe(true);
});


test('saved scientific document titles replace generic placement labels only after exact resource validation',async()=>{
 vi.mocked(fetch).mockResolvedValueOnce(reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',title:'Mixed mesh + 2 layers',state:{scientificView:{}}}}}));
 await openStudyScientific({...saved,title:'3 saved scientific layers'});
 expect(fixture.renamed).toHaveBeenCalledWith(fixture.window,'Mixed mesh + 2 layers','Mixed mesh + 2 layers');
 expect(calls).toHaveLength(0);
});

test('a title from the wrong scientific revision cannot rename the retained window',async()=>{
 vi.mocked(fetch).mockResolvedValueOnce(reply({project:{id:saved.resource.id,session:'origin',revision:3,document:{kind:'scene',title:'Wrong revision',state:{scientificView:{}}}}}));
 await openStudyScientific(saved);
 expect(fixture.renamed).not.toHaveBeenCalled();expect(fixture.window.textContent).toContain('not the requested saved scientific revision');
});

test('an admitted tool callback failure keeps the session and drafts and never replays on inspection',async()=>{
  fixture.trackInvalidation=true;
  const original=request.getMockImplementation()!;let attempts=0,revision=1;
  request.mockImplementation(async(path,init)=>{
    if(path.includes('/frames/'))return new Response(new Uint8Array(32*24*4));
    const command=path.endsWith('/request')?body(init).request:null;
    if(command?.op==='render'||command?.op==='change'){
      calls.push({path,init});
      if(command.op==='change'&&++attempts===1)
        return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_CONTROL_FAILED',error:'Callback failed.',
          next:'Private work may have changed. Choose Check current view. The action was not run again.'}),{status:409});
      const frame=structuredClone(cases.valid);frame.frame=(++revision).toString(16).padStart(64,'0');
      frame.controls.revision=revision;fixture.paint=frame;return reply({frame});
    }
    return original(path,init);
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  const editor=fixture.window.querySelector('textarea')!;editor.value='private_kept + 1';fire(editor,'input');
  const input=fixture.window.querySelectorAll('input').find(e=>e.id.endsWith('d'.repeat(32)))!;
  input.value='.4';fire(input,'input');fire(input,'change');await settle();
  expect(attempts).toBe(1);expect(fixture.window.textContent).toContain('may have run');
  expect(editor.value).toBe('private_kept + 1');expect(input.value).toBe('.4');
  expect(button('Check current settings').disabled).toBe(false);
  fire(button('Check current settings'),'click');await settle();
  expect(attempts).toBe(1);expect(input.value).toBe('.4');
  fire(button('Discard unsent changes'),'click');input.value='.6';fire(input,'input');fire(input,'change');await settle();
  expect(attempts).toBe(2);expect(fixture.window.textContent).toContain('Applied.');
  expect(editor.value).toBe('private_kept + 1');
  expect(calls.filter(call=>call.init?.method==='DELETE')).toHaveLength(0);
  expect(calls.filter(call=>call.path==='/api/scientific-reviews'&&call.init?.method==='POST')).toHaveLength(1);
});


test('declared native panels use the admitted session, bounded pixels and explicit action receipt; actions refresh the image once', async () => {
  const original=request.getMockImplementation()!;
  let refuse=false;
  const panel={format:'scientific-native-panel-v1',panel:'a'.repeat(32),frame:'b'.repeat(64),width:32,height:24,pixelRatio:1,focus:null,
    accessibility:[{id:'1',parent:null,role:'Client',name:'Tool',description:'',value:'',rect:[0,0,32,24],disabled:false,focused:false,focusable:false,invisible:false,checked:false,checkable:false,readOnly:false}]};
  request.mockImplementation(async(path,init)=>{
    if(path.includes('/native-panels/')) return new Response(new Uint8Array(32*24*4));
    if(path.endsWith('/request')) {
      calls.push({path,init});const op=body(init).request.op;
      if(op==='native-panel-render')return reply({nativePanel:panel,imageFrame:cases.valid.frame});
      if(op==='native-panel-change')return refuse
        ? new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_CHANGE_NOT_APPLIED',error:'Panel changed before delivery',next:'Choose Check current view'}),{status:409})
        : reply({nativeChanged:true});
      return reply({frame:{...cases.valid,nativePanelAvailable:true}});
    }
    return original(path,init);
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  expect(fixture.nativeAvailable).toBe(true);
  fixture.invalidated.mockClear();
  expect((await fixture.native.load(32,24)).pixels).toHaveLength(32*24*4);
  expect(fixture.invalidated).not.toHaveBeenCalled();
  const command={panel:panel.panel,frame:panel.frame,op:'click',value:[1,2]};
  await fixture.native.send(command,'panel-paint');
  const mutations=calls.filter(call=>call.path.endsWith('/request')&&body(call.init).request.op==='native-panel-change');
  expect(mutations).toHaveLength(1);expect(body(mutations[0].init)).toEqual({request:{op:'native-panel-change',request:command},draw:'panel-paint'});
  expect(fixture.shown).toHaveBeenCalledTimes(2);
  refuse=true;
  await expect(fixture.native.send(command,'panel-paint')).rejects.toThrow('Panel changed before delivery');
  // The specialist panel owns this error and its Refresh control. Duplicating it
  // in the document leaves a stale warning even after the panel has recovered.
  expect(fixture.window.textContent).not.toContain('Panel changed before delivery');
  await expect(fixture.native.send(command,'panel-paint')).rejects.not.toThrow('Check current view');
  const attempts=calls.filter(call=>call.path.endsWith('/request')&&body(call.init).request.op==='native-panel-change').length;
  await fixture.native.load(32,24);
  expect(calls.filter(call=>call.path.endsWith('/request')&&body(call.init).request.op==='native-panel-change')).toHaveLength(attempts);
  request.mockImplementationOnce(async()=>new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_MEMORY',error:'Not enough memory',next:'Review running sessions.'}),{status:503}));
  await expect(fixture.native.load(32,24)).rejects.toThrow('Not enough memory');
  expect(button('Review running sessions').hidden).toBe(false);
  expect(fixture.window.textContent).not.toContain('Not enough memory');
  await fixture.native.load(32,24);
  expect(button('Review running sessions').hidden).toBe(true);
  request.mockImplementationOnce(async()=>new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_MEMORY',error:'Image preview exceeds available memory',next:'Review running sessions.'}),{status:503}));
  fire(button('Refresh preview'),'click');await settle();
  expect(button('Review running sessions').hidden).toBe(false);
  await fixture.native.load(32,24);
  expect(button('Review running sessions').hidden).toBe(false);
  expect(fixture.window.textContent).toContain('Image preview exceeds available memory');
});

test('a declared tool saves bound input and tool state together, retaining declarations after a refusal', async()=>{
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Specimen',type:'labels',
    editableInput:'specimen',scale:[1,1,1],translate:[0,0,0]};
  const commands:Record<string,unknown>[]=[];
  let attempts=0;
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},
      toolDocumentAvailable:true,arraySave:null});
    if(command.op==='save-tool' && attempts++===0)return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_NOT_STARTED',
      error:'The tool callback changed its layer set. Inspect private work before saving again.'}),{status:409});
    if(command.op==='save-tool')return reply({arraySave:{kind:'new-output',tool:true,state:'saved',request:command.request,
      layer:layer.id,resource:{...saved.resource,id:'p'+'f'.repeat(32),version:1}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Save tool and all layers'),'click');await settle();
  expect(commands.map(row=>row.op)).toEqual(['layer-inventory']);
  const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Specimen')!;
  const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Specimen')!;
  meaning.value='instance-labels';fire(meaning,'change');axes.value='z, y, x';fire(axes,'input');
  const oldSave = button('Save tool and all layers');
  fire(button('Refresh layers'),'click');await settle();
  const count = commands.length;
  fire(oldSave,'click');await settle();
  expect(commands).toHaveLength(count);
  expect(fixture.window.textContent).toContain('earlier layer view');
  fire(button('Save tool and all layers'),'click');await settle();
  expect(fixture.window.textContent).toContain('changed its layer set');
  expect(fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Specimen')!.value).toBe('z, y, x');
  fire(button('Save tool and all layers'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-tool',resource:saved.resource,
    outputs:[{layer:layer.id,meaning:{semantic:'instance-labels',axes:['z','y','x']}}]});
  expect(commands.at(-1)).not.toHaveProperty('frame');
  expect(button('Open saved tool document')).toBeTruthy();
  expect(fixture.window.textContent).toContain('Tool state and scientific layers saved together');
});

test('Save tool with more layers than a saved scene holds says why and what to do, and sends nothing (F-L8-7)',async()=>{
  const layer=(index:number,input:boolean)=>({...structuredClone(cases.valid.layers[0]),id:index.toString(16).padStart(32,'0'),
    name:input?`Kept ${index}`:`New ${index}`,type:'labels',scale:[1,1,1],translate:[0,0,0],
    ...(input?{editableInput:`input${index}`,inputMeaning:{semantic:'instance-labels',axes:['z','y','x']}}:{})});
  const layers=[...[1,2,3,4,5].map(i=>layer(i,true)),...[6,7,8,9].map(i=>layer(i,false))];
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers},toolDocumentAvailable:true,arraySave:null});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  const said='Save tool keeps every layer of this tool together, and a saved scene holds at most 8: this tool has 9 (5 came with its input, 4 are new). Save its new layers with “Save selected new layers together” (the tool’s settings are then not kept with them), or start from a scene with fewer layers, such as the original image.';
  expect(fixture.window.textContent).toContain(said);
  fire(button('Save tool and all layers'),'click');await settle();
  expect(commands.map(row=>row.op)).toEqual(['layer-inventory']);
  expect(fixture.window.textContent).not.toContain('Choose between two and eight');
  expect(button('Save selected new layers together')).toBeTruthy();
});

test('tool save uses the exact recorded input meaning without asking for it again',async()=>{
  const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Known specimen',type:'labels',
    editableInput:'specimen',scale:[1,1,1],translate:[0,0,0],inputMeaning:{semantic:'instance-labels',axes:['z','y','x']}};
  const commands:Record<string,unknown>[]=[];
  request.mockImplementation(async(_path,init)=>{
    if(!init?.body || !body(init).request)return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},toolDocumentAvailable:true,arraySave:null});
    if(command.op==='save-tool')return reply({arraySave:{kind:'new-output',tool:true,state:'saved',request:command.request,
      layer:layer.id,resource:{...saved.resource,id:'p'+'f'.repeat(32),version:1}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Save tool and all layers'),'click');await settle();
  expect(commands.at(-1)).toMatchObject({op:'save-tool',outputs:[{layer:layer.id,meaning:layer.inputMeaning}]});
  expect(button('Open saved tool document')).toBeTruthy();
});


test.each([null,'SCIENTIFIC_MEMORY'])('native acknowledgement survives image refresh failure (%s) with appropriate recovery',async(code)=>{
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  const original=request.getMockImplementation()!;
  request.mockImplementation(async(path,init)=>{
    if(path.endsWith('/request')){
      calls.push({path,init});const op=body(init).request.op;
      if(op==='native-panel-change')return reply({nativeChanged:true});
      if(op==='render')return new Response(JSON.stringify({ok:false,code,error:'Image preview unavailable'}),{status:503});
    }
    return original(path,init);
  });
  await expect(fixture.native.send({panel:'a'.repeat(32),frame:'b'.repeat(64),op:'text',value:'µm'},'panel-paint')).resolves.toBeUndefined();
  expect(fixture.window.textContent).toContain('Plugin interaction delivered');
  expect(fixture.window.textContent).toContain('Image preview unavailable');
  expect(button('Review running sessions').hidden).toBe(code!=='SCIENTIFIC_MEMORY');
  expect(calls.filter(call=>call.init?.body&&body(call.init).request?.op==='native-panel-change')).toHaveLength(1);
});
test('missing native acknowledgement is not reported as delivered',async()=>{
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  const original=request.getMockImplementation()!;
  request.mockImplementation(async(path,init)=>path.endsWith('/request')&&body(init).request.op==='native-panel-change'?reply({}):original(path,init));
  await expect(fixture.native.send({panel:'a'.repeat(32),frame:'b'.repeat(64),op:'text',value:'µm'},'panel-paint')).rejects.toThrow();
});


test('saved-document opening reports success only after rendering the exact revision', async () => {
  expect(await openStudyScientific(saved)).toBe(true);
  expect(fixture.window.dataset.displayState).toBe('rendered');
  expect(await openStudyScientific(saved)).toBe(true);
});

test('failed or cancelled saved-document loading never reports a successful open', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => reply({project:{id:'wrong'}})));
  expect(await openStudyScientific(saved)).toBe(false);
  expect(fixture.window.dataset.displayState).toBe('failed');
  expect(button('Retry saved document')).toBeDefined();
  const delayed=pending<Response>();
  vi.stubGlobal('fetch', vi.fn(() => delayed.promise));
  const opening=openStudyScientific({...saved,placementId:'cancelled-placement'});
  fixture.dispose();
  delayed.resolve(reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',state:{}}}}));
  expect(await opening).toBe(false);
});


test('reopening a failed saved document retries its existing loader and succeeds after transport recovery', async () => {
  const load=vi.fn().mockResolvedValueOnce(reply({project:{id:'wrong'}})).mockImplementation(async()=>reply({project:{
    id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',state:{scientificView:{}}}
  }}));
  vi.stubGlobal('fetch',load);
  expect(await openStudyScientific(saved)).toBe(false);
  expect(await openStudyScientific(saved)).toBe(true);
  expect(load).toHaveBeenCalledTimes(2);
  expect(fixture.window.dataset.displayState).toBe('rendered');
  expect(fixture.keys).toHaveLength(1);
});


test('saved movie export is directly reachable without starting or changing the editable session', async () => {
  await openStudyScientific(saved);
  const canvas = fixture.window.querySelector('canvas');
  fire(button('Export'), 'click'); await settle();
  expect(button('Export').getAttribute('aria-selected')).toBe('true');
  const panel = fixture.window.querySelectorAll('section').find(item =>
    item.getAttribute('role') === 'tabpanel' && item.textContent.includes('Export saved movie'))!;
  expect(panel.hidden).toBe(false);
  expect(panel.textContent).toContain('saved revision 2');
  expect(request).not.toHaveBeenCalled();
  fire(button('View'), 'click'); await settle();
  expect(panel.hidden).toBe(true);
  expect(fixture.window.querySelector('canvas')).toBe(canvas);
  expect(request).not.toHaveBeenCalled();
});

function backgroundTool() {
  const original=request.getMockImplementation()!;
  const observe=vi.fn(async () => reply({imageFrame:cases.valid.frame}));
  const renders=vi.fn(() => reply({frame:{...cases.valid,nativePanelAvailable:true}}));
  request.mockImplementation(async (path,init) => {
    const command=path.endsWith('/request') ? body(init).request : null;
    if (command?.op==='observe') {
      expect(command).toEqual({op:'observe',resource:saved.resource});
      expect(body(init).draw).toBeNull(); return observe();
    }
    if(command?.op==='render')return renders();
    return original(path,init);
  });
  return {observe,renders};
}

test('background results refresh their owning image without replay or continuous painting', async () => {
  vi.useFakeTimers(); const tool=backgroundTool();
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(1);expect(tool.renders).toHaveBeenCalledTimes(1);
  tool.observe.mockResolvedValueOnce(reply({imageFrame:null}));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.renders).toHaveBeenCalledTimes(2);expect(fixture.shown).toHaveBeenCalledTimes(2);
  expect(calls.some(call=>call.init?.body && ['native-panel-change','python','python-resources','save-output'].includes(body(call.init).request?.op))).toBe(false);
  fixture.dispose();await vi.advanceTimersByTimeAsync(6000);expect(tool.observe).toHaveBeenCalledTimes(2);
});

test('a background check for results never drops a keystroke or click: View and Python wait for it', async () => {
  // The H06a browser journey lost a typed contrast minimum and a Run click: each landed while the 1.5 s check
  // held the document busy, which disabled the field and the button for that moment.
  vi.useFakeTimers(); const tool=backgroundTool();
  let finish!: (value: unknown) => void;
  tool.observe.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }) as never);
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);
  expect(tool.observe).toHaveBeenCalledTimes(1);  // the check is in flight
  const opacity=fixture.window.querySelectorAll('input').find(input=>input.getAttribute('aria-label')==='Opacity Intensity')!;
  expect(opacity.disabled).toBe(false);
  opacity.value='.4';fire(opacity,'input');
  expect(button('Apply view changes').disabled).toBe(false);
  fire(button('Apply view changes'),'click');await settle();
  const changes=()=>calls.filter(call=>call.init?.body && body(call.init).request?.op==='change');
  expect(changes()).toHaveLength(0);  // waiting for the check, not refused
  finish(await reply({imageFrame:cases.valid.frame}));await settle();await vi.advanceTimersByTimeAsync(0);await settle();
  expect(changes().map(call=>body(call.init).request.request.value.opacity)).toEqual([.4]);
});

test('Refresh preview remains available and queues one render after a background result check', async () => {
  vi.useFakeTimers(); const tool=backgroundTool(), waiting=pending<Response>();
  tool.observe.mockReturnValueOnce(waiting.promise);
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(1);
  expect(button('Refresh preview').disabled).toBe(false);
  fire(button('Refresh preview'),'click');await settle();
  expect(tool.renders).toHaveBeenCalledTimes(1);
  waiting.resolve(reply({imageFrame:cases.valid.frame}));await settle();
  await vi.advanceTimersByTimeAsync(0);await settle();
  expect(tool.renders).toHaveBeenCalledTimes(2);
  expect(button('Refresh preview').disabled).toBe(false);
  expect(fixture.window.textContent).not.toContain('Wait for the current scientific operation');
});

test('a background check for results never drops a Python Run click: the cell runs once, after the check', async () => {
  vi.useFakeTimers(); const tool=backgroundTool(), original=request.getMockImplementation()!;
  const runs: Record<string, unknown>[] = [];
  request.mockImplementation(async (path,init) => {
    const command=path.endsWith('/request') ? body(init).request : null;
    if(command?.op==='python-resources'){runs.push(command);
      return reply({console:{format:'scientific-console-v1',sequence:1,stdout:'kept',stderr:'',result:'',error:'',truncated:false,privatePreview:true}});}
    return original(path,init);
  });
  let finish!: (value: unknown) => void;
  tool.observe.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }) as never);
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);
  expect(tool.observe).toHaveBeenCalledTimes(1);  // the check is in flight
  const input=fixture.window.querySelectorAll('textarea').find(item=>item.getAttribute('aria-label')==='Python code')!;
  input.value='print("kept")';fire(input,'input');
  expect(button('Run code').disabled).toBe(false);
  fire(button('Run code'),'click');await settle();
  expect(runs).toHaveLength(0);  // waiting for the check, not refused
  finish(await reply({imageFrame:cases.valid.frame}));await settle();await vi.advanceTimersByTimeAsync(0);await settle();
  expect(runs).toHaveLength(1);
  expect(runs[0]).toMatchObject({op:'python-resources',resource:saved.resource,source:'print("kept")'});
  expect(fixture.window.textContent).toContain('kept');
});

test('background observation pauses while hidden or editing and contains failure until explicit resume', async () => {
  vi.useFakeTimers(); const tool=backgroundTool(); fixture.trackInvalidation=true;
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  fixture.window.dataset.workspaceHidden='true';
  await vi.advanceTimersByTimeAsync(3000);expect(tool.observe).not.toHaveBeenCalled();
  fixture.window.dataset.workspaceHidden='false';
  const opacity=fixture.window.querySelectorAll('input').find(input=>input.getAttribute('aria-label')==='Opacity Intensity')!;
  opacity.value='.4';fire(opacity,'input');
  await vi.advanceTimersByTimeAsync(3000);expect(tool.observe).not.toHaveBeenCalled();
  expect(fixture.window.textContent).toContain('Live updates are waiting. Apply or discard your changes in View.');
  fire(button('Discard unsent view changes'),'click');
  tool.observe.mockRejectedValueOnce(new Error('Late callback failed'));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(fixture.window.textContent).toContain('Live results paused.');
  expect(button('Resume live results').hidden).toBe(false);
  await vi.advanceTimersByTimeAsync(6000);expect(tool.observe).toHaveBeenCalledTimes(1);
  fire(button('Resume live results'),'click');
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(2);
  expect(fixture.window.textContent).toContain('Watching for new results.');
  expect(tool.renders).toHaveBeenCalledTimes(2);expect(fixture.invalidFrame).toBe(false);
});

test('a session busy with a notebook cell makes live results wait, not pause, and they continue by themselves', async () => {
  vi.useFakeTimers(); const tool=backgroundTool();
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(1);
  tool.observe.mockResolvedValueOnce(new Response(JSON.stringify({ok:false, code:'SCIENTIFIC_REVIEW_BUSY',
    error:'Wait for this scientific interaction to finish before starting another'}), {status:409, headers:{'content-type':'application/json'}}));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(fixture.window.textContent).toContain('Live results wait while another request in this session runs, such as a notebook cell.');
  expect(fixture.window.textContent).not.toContain('Live results paused.');
  expect(button('Resume live results').hidden).toBe(true);
  tool.observe.mockResolvedValueOnce(reply({imageFrame:null}));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(3);
  expect(tool.renders).toHaveBeenCalledTimes(2);
  expect(fixture.window.textContent).toContain('Watching for new results.');
  fixture.dispose();
});

test('live updates explain resource-only sessions and resume after explicit preview', async () => {
  vi.useFakeTimers();const tool=backgroundTool(),original=request.getMockImplementation()!;
  request.mockImplementation(async(path,init)=>{
    if(path.endsWith('/request') && body(init).request.op==='layer-inventory')return reply({
      inventory:{format:'scientific-layers-v1',layers:cases.valid.layers},nativePanelAvailable:true,arraySave:null});
    return original(path,init);
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);
  expect(tool.observe).not.toHaveBeenCalled();
  expect(fixture.window.textContent).toContain('Open Refresh preview to enable live result updates.');
  fire(button('Refresh preview'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(1);
  expect(fixture.window.textContent).toContain('Watching for new results.');
  fire(button('Stop scientific session'),'click');await settle();
  expect(fixture.window.textContent).toContain('Scientific session stopped.');
  expect(fixture.window.textContent).not.toContain('Watching for new results.');
  expect(button('Resume live results').hidden).toBe(true);
});

test('only one background request is outstanding and late results cannot repaint another Project', async () => {
  vi.useFakeTimers();const tool=backgroundTool(),waiting=pending<Response>();
  tool.observe.mockReturnValueOnce(waiting.promise);
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  await vi.advanceTimersByTimeAsync(1500);await settle();
  await vi.advanceTimersByTimeAsync(6000);expect(tool.observe).toHaveBeenCalledTimes(1);
  fixture.window.dataset.project='another-project';
  waiting.resolve(reply({imageFrame:null}));await settle();
  expect(tool.renders).toHaveBeenCalledTimes(1);expect(fixture.shown).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(6000);expect(tool.observe).toHaveBeenCalledTimes(1);
});

test('observation preserves a save declaration focused during late frame delivery', async () => {
  vi.useFakeTimers();fixture.trackInvalidation=true;const tool=backgroundTool(),pixels=pending<Response>();
  const original=request.getMockImplementation()!;
  request.mockImplementation((path,init)=>path.includes('/frames/') && tool.renders.mock.calls.length>1 ? pixels.promise : original(path,init));
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  tool.observe.mockResolvedValueOnce(reply({imageFrame:null}));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.renders).toHaveBeenCalledTimes(2);
  const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Intensity')!;
  expect(axes).toBeTruthy();Object.assign(document,{activeElement:axes});axes.value='z, y, x';fire(axes,'input');
  pixels.resolve(new Response(new Uint8Array(32*24*4)));await settle();
  expect(fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Intensity')).toBe(axes);
  expect(axes.value).toBe('z, y, x');expect(fixture.shown).toHaveBeenCalledTimes(1);
  await vi.advanceTimersByTimeAsync(3000);expect(tool.observe).toHaveBeenCalledTimes(1);
  Object.assign(document,{activeElement:null});request.mockImplementation(original);
  await vi.advanceTimersByTimeAsync(1500);await settle();expect(fixture.shown).toHaveBeenCalledTimes(2);
});

test('unchanged background observations preserve a panel-owned memory recovery action', async () => {
  vi.useFakeTimers();const tool=backgroundTool();
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  request.mockResolvedValueOnce(new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_MEMORY',error:'Panel needs memory'}),{status:503}));
  await expect(fixture.native.load(32,24)).rejects.toThrow('Panel needs memory');
  expect(button('Review running sessions').hidden).toBe(false);
  await vi.advanceTimersByTimeAsync(1500);await settle();expect(tool.observe).toHaveBeenCalledTimes(1);
  expect(button('Review running sessions').hidden).toBe(false);
});

test('queued background image paint still refreshes the specialist controls', async () => {
  vi.useFakeTimers();const tool=backgroundTool();
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  // Model FrameCanvas.show: pixels are committed now, while its presentation
  // receipt becomes available only in a later animation frame.
  const original=request.getMockImplementation()!;
  let imageFrame:string|null=cases.valid.frame;
  request.mockImplementation(async(path,init)=>{
    if(path.includes('/native-panels/'))return new Response(new Uint8Array(32*24*4));
    if(path.endsWith('/request') && body(init).request.op==='native-panel-render')return reply({imageFrame,nativePanel:{
      format:'scientific-native-panel-v1',panel:'a'.repeat(32),frame:'b'.repeat(64),width:32,height:24,pixelRatio:1,focus:null,accessibility:[{id:'1',parent:null,role:'Client',name:'Tool',description:'',value:'',rect:[0,0,32,24],disabled:false,focused:false,focusable:false,invisible:false,checked:false,checkable:false,readOnly:false}]}});
    return original(path,init);
  });
  fixture.nativeRefreshed.mockImplementation(()=>fixture.native.load(32,24));
  fixture.invalidated.mockImplementation(()=>{fixture.waiting=null;});
  fixture.shown.mockImplementationOnce(()=>{fixture.waiting=cases.valid.frame;});
  tool.observe.mockResolvedValueOnce(reply({imageFrame:null}));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(fixture.waiting).toBe(cases.valid.frame);
  expect(fixture.nativeRefreshed).toHaveBeenCalledTimes(1);
  fixture.waiting=null;fixture.drawn();
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(fixture.nativeRefreshed).toHaveBeenCalledTimes(1);
  // A genuinely changed image identity must still revoke a pending paint.
  fixture.waiting=cases.valid.frame;imageFrame=null;
  await fixture.native.load(32,24);expect(fixture.waiting).toBeNull();
});

test('browser recovery key survives a lost opening reply and recreated window without rerunning code',async()=>{
 const values=new Map<string,string>();
 const storage:Storage={get length(){return values.size;},key:index=>[...values.keys()][index]??null,
  getItem:key=>values.get(key)??null,setItem:(key,value)=>{values.set(key,value);},removeItem:key=>{values.delete(key);},clear:()=>values.clear()};
 configureScientificDocuments({prepare:async()=>{},transport:()=>({request,check}),recovery:{storage,prefix:'private-recovery'}});
 const original=request.getMockImplementation()!;let lost=true;
 request.mockImplementation(async(path,init)=>{
  if(path==='/api/scientific-reviews' && init?.method==='POST'){
   calls.push({path,init});expect([...values.values()]).toContain(body(init).recoveryKey);
   if(lost){lost=false;throw new Error('Connection lost after opening');}return reply({review});
  }return original(path,init);
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 const key=body(calls.find(call=>call.init?.method==='POST')!.init).recoveryKey;
 expect(key).toMatch(/^[A-Za-z0-9_-]{32,128}$/);
 fixture.dispose();fixture.keys.length=0;fixture.window.replaceChildren();
 await openStudyScientific(saved);expect(calls.filter(call=>call.init?.method==='POST')).toHaveLength(1);
 fire(button('Open session'),'click');await settle();
 const opens=calls.filter(call=>call.path==='/api/scientific-reviews'&&call.init?.method==='POST');
 expect(opens).toHaveLength(2);expect(body(opens[1].init).recoveryKey).toBe(key);
 expect(fixture.window.textContent).not.toContain(key);
 expect(calls.some(call=>call.path.endsWith('/script'))).toBe(false);
 fire(button('Stop scientific session'),'click');await settle();expect(values.size).toBe(0);
});

test('unavailable browser recovery storage refuses startup before allocating private work',async()=>{
 const storage:Storage={length:0,key:()=>null,getItem(){throw new Error('Storage unavailable');},setItem(){},removeItem(){},clear(){}};
 configureScientificDocuments({prepare:async()=>{},transport:()=>({request,check}),recovery:{storage,prefix:'private-recovery'}});
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 expect(calls.some(call=>call.init?.method==='POST')).toBe(false);
 expect(fixture.window.textContent).toContain('recovery');
});

test('reconnecting exposes retained callback failure after successful preview without replay',async()=>{
 const original=request.getMockImplementation()!;
 request.mockImplementation(async(path,init)=>{
  if(path==='/api/scientific-reviews'&&init?.method==='POST')return reply({review,sessionNotice:'Earlier observe request failed: callback rejected the layer. This request was not replayed.'});
  return original(path,init);
 });
 await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
 expect(fixture.shown).toHaveBeenCalledTimes(1);
 expect(fixture.window.textContent).toContain('callback rejected the layer');
 expect(calls.some(call=>call.path.endsWith('/script'))).toBe(false);
});


test('unsent specialist text preserves its draft without blocking completed image results', async () => {
  vi.useFakeTimers(); const tool=backgroundTool();
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();
  fixture.nativeDraft=true;tool.observe.mockResolvedValueOnce(reply({imageFrame:null}));
  await vi.advanceTimersByTimeAsync(1500);await settle();
  expect(tool.observe).toHaveBeenCalledTimes(1);expect(tool.renders).toHaveBeenCalledTimes(2);
  expect(fixture.nativeDraft).toBe(true);expect(fixture.nativeRefreshed).not.toHaveBeenCalled();
  expect(fixture.window.textContent).toContain('Watching for new results. Your unsent tool text is kept');
  expect(calls.some(call=>call.init?.body && ['native-panel-change','python','python-resources','save-output'].includes(body(call.init).request?.op))).toBe(false);
});


test('earlier browser windows reopen exact input and placement without executing work',async()=>{
 const name='private:'+JSON.stringify(['origin',saved.resource.id,2,'original-placement']);
 const storage={length:1,key:()=>name,getItem:()=> 's'.repeat(36)} as unknown as Storage;
 configureScientificDocuments({prepare:async()=>{},transport:()=>({request,check}),currentProject:()=> 'origin',recovery:{storage,prefix:'private'}});
 openScientificSessions();await settle();
 const reopen=fixture.sheet.querySelectorAll('button').find(row=>row.textContent.startsWith('Reopen input'))!;
 expect(fixture.sheet.textContent).toContain('do not confirm a running session');
 fire(reopen,'click');await settle();
 expect(fixture.window.dataset.studyRetained).toContain('original-placement');
 expect(calls.some(call=>call.init?.method==='POST')).toBe(false);
 expect(fixture.sheet.textContent).not.toContain('s'.repeat(36));
});

test('declared output meaning reaches save while undeclared user drafts remain editable',async()=>{
 const layer={...structuredClone(cases.valid.layers[0]),id:'d'.repeat(32),name:'Declared objects',type:'labels',scale:[1,1,1],translate:[0,0,0],viewKey:'declared',viewGeometry:{axes:['z','y','x'],units:['px','px','px'],affine:[[1,0,0,0],[0,1,0,0],[0,0,1,0],[0,0,0,1]]},outputMeaning:{semantic:'instance-labels',axes:['z','y','x']}};
 const commands:Record<string,unknown>[]=[];
 request.mockImplementation(async(_path,init)=>{
  if(!init?.body||!body(init).request)return reply({review});
  const command=body(init).request;commands.push(command);
  if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers:[layer]},arraySave:null});
  if(command.op==='save-output')return reply({arraySave:{kind:'new-output',state:'saved',request:command.request,layer:layer.id,resource:{...saved.resource,id:'p'+'e'.repeat(32),version:1}}});
  throw new Error('Unexpected request');
 });
 await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
 const meaning=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Declared objects')!;
 const axes=fixture.window.querySelectorAll('input').find(item=>item.getAttribute('aria-label')==='Axes of Declared objects')!;
 expect(meaning.value).toBe('instance-labels');expect(axes.value).toBe('z, y, x');expect(meaning.disabled).toBe(true);expect(axes).toHaveProperty('readOnly',true);
 meaning.value='class-labels';fire(meaning,'change');fire(button('Refresh layers'),'click');await settle();
 expect(fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Declared objects')!.value).toBe('class-labels');
 fire(button('Save as new scientific document'),'click');await settle();
 expect(commands.some(command=>command.op==='save-output')).toBe(false);
 const current=fixture.window.querySelectorAll('select').find(item=>item.getAttribute('aria-label')==='Scientific meaning of Declared objects')!;
 fire(button('Use tool declaration'),'click');expect(current.value).toBe('instance-labels');fire(button('Save as new scientific document'),'click');await settle();
 expect(commands.at(-1)).toMatchObject({op:'save-output',meaning:layer.outputMeaning});
});

test('a confirmed layer save keeps the session open and says where it was placed', async () => {
  const base = structuredClone(cases.valid.layers[0]);
  const layers = [{...base,id:'a'.repeat(32),name:'First',editableInput:'first'},{...base,id:'b'.repeat(32),name:'Second',editableInput:'second'}];
  const commands: Record<string,unknown>[]=[];
  request.mockImplementation(async (_path, init) => {
    if (!init?.body || !body(init).request) return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers},arraySave:null});
    if(command.op==='save-resource')return reply({arraySave:{state:'saved',request:command.request,
      resource:{...saved.resource,version:command.layer===layers[0].id?4:5},layer:command.layer,
      ...(command.layer===layers[0].id?{onto:{version:3,since:2}}:{})}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Save array changes to First'),'click');await settle();
  expect(fixture.window.textContent).toContain('placed on revision 3, which another save accepted after revision 2');
  expect(fixture.window.textContent).toContain('you can keep editing and save them');
  fire(button('Continue this private session'),'click');await settle();
  expect(button('Save array changes to Second').disabled).toBe(false);
  fire(button('Save array changes to Second'),'click');await settle();
  expect(commands.filter(c=>c.op==='save-resource').map(c=>c.layer)).toEqual([layers[0].id,layers[1].id]);
  expect(button('Open saved revision 5')).toBeTruthy();
});

test('a conflict names each layer and saves only the unaffected ones on request', async () => {
  const base = structuredClone(cases.valid.layers[0]);
  const layers = [{...base,id:'a'.repeat(32),name:'First',editableInput:'first'},{...base,id:'b'.repeat(32),name:'Second',editableInput:'second'}];
  const commands: Record<string,unknown>[]=[];let identity='';
  request.mockImplementation(async (_path, init) => {
    if (!init?.body || !body(init).request) return reply({review});
    const command=body(init).request;commands.push(command);
    if(command.op==='layer-inventory')return reply({inventory:{format:'scientific-layers-v1',layers},arraySave:null});
    if(command.op==='save-resources'){identity=command.request;
      return new Response(JSON.stringify({ok:false,code:'SCIENTIFIC_SAVE_CONFLICT',error:'A newer revision (3) was accepted from another save and changed first (changed).'}),{status:409});}
    if(command.op==='save-status')return reply({arraySave:{state:'conflict',request:identity,resource:null,layer:layers[0].id,
      layers:layers.map(l=>l.id),copyCaptured:false,copyNeedsPreview:false,latest:{...saved.resource,version:3},
      conflicts:[{layer:layers[0].id,input:'first',state:'changed-in-latest'},{layer:layers[1].id,input:'second',state:'unchanged-in-latest'}]}});
    if(command.op==='save-resource')return reply({arraySave:{state:'saved',request:command.request,resource:{...saved.resource,version:4},layer:command.layer,onto:{version:3,since:2}}});
    throw new Error('Unexpected request');
  });
  await openStudyScientific(saved);fire(button('Start without preview'),'click');await settle();
  fire(button('Save all 2 editable layers'),'click');await settle();
  expect(fixture.window.textContent).toContain('First: changed by another save');
  expect(fixture.window.textContent).toContain('Second: unchanged in the newer revision');
  fire(button('Save the 1 unaffected layer'),'click');await settle();
  const last=commands[commands.length-1];
  expect(last).toMatchObject({op:'save-resource',layer:layers[1].id,input:'second',resource:saved.resource});
  expect(last.request).not.toBe(identity);
  expect(button('Open saved revision 4')).toBeTruthy();
});

test.each(['complete','failure','stop'])('Labels settings retain input availability inside the real document request boundary: %s', async outcome => {
  const frame={...structuredClone(cases.valid),layers:cases.valid.layers.map(layer=>({...layer,type:'labels',editableInput:'labels'}))};
  fixture.paint=frame;
  const original=request.getMockImplementation()!, delayed=pending<Response>();
  const changes: Record<string,unknown>[]=[];
  request.mockImplementation(async(path,init)=>{
    if (!path.endsWith('/request')) return original(path,init);
    const command=body(init).request; calls.push({path,init});
    if(command.op==='change' && command.request.operation==='labels-tool') {
      changes.push(command.request.value.settings);
      if(changes.length===1)return delayed.promise;
    }
    return reply({frame});
  });
  await openStudyScientific(saved); fire(button('Open session'),'click'); await settle();
  fire(button('Edit'),'click');
  const field=(label:string)=>[...fixture.window.querySelectorAll('input'),...fixture.window.querySelectorAll('select')].find(row=>row.getAttribute('aria-label')===label)!;
  field('Brush size').value='9'; fire(field('Brush size'),'change');
  // The old document busy flag synchronously disabled this next focus target.
  expect(field('Edit dimensions').disabled).toBe(false);
  expect(field('Label value').disabled).toBe(false);
  expect(button('Undo label edit').disabled).toBe(true);
  expect(button('Run code').disabled).toBe(true);
  field('Label value').value='42';fire(field('Label value'),'change');
  await settle();expect(changes).toEqual([{brushSize:9}]);
  if(outcome==='stop') {fire(button('Stop scientific session'),'click');await settle();}
  delayed.resolve(outcome==='failure'?new Response(JSON.stringify({error:'refused'}),{status:409}):reply({frame}));
  await settle();
  expect(changes).toEqual(outcome==='complete'?[{brushSize:9},{selectedLabel:'42'}]:[{brushSize:9}]);
  if(outcome==='stop')expect(field('Edit dimensions').disabled).toBe(true);
});

test.each(['complete','failure','stop'])('annotation mode remains usable only for its owned document request: %s', async outcome => {
  const frame={...structuredClone(cases.valid),layers:cases.valid.layers.map(layer=>({...layer,type:'shapes',
    annotation:{mode:'pan_zoom',selected:0,creating:false,undo:1,redo:0}}))};
  fixture.paint=frame;
  const original=request.getMockImplementation()!, delayed=pending<Response>(), drawn=pending<void>();
  const actions:string[]=[];
  request.mockImplementation(async(path,init)=>{
    if(!path.endsWith('/request'))return original(path,init);
    const command=body(init).request;calls.push({path,init});
    if(command.op==='change'&&command.request.operation==='annotate') {
      actions.push(command.request.value.action);
      if(actions.length===1)return delayed.promise;
    }
    return reply({frame});
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();fire(button('Edit'),'click');
  const mode=fixture.window.querySelectorAll('select').find(row=>row.getAttribute('aria-label')==='Annotation mode')!;
  Object.defineProperty(document,'activeElement',{configurable:true,value:mode});
  mode.value='vertex_remove';fire(mode,'change');
  expect(mode.disabled).toBe(false);
  expect(button('Undo annotation edit').disabled).toBe(true);
  expect(button('Run code').disabled).toBe(true);
  mode.value='add_rectangle';fire(mode,'change');await settle();
  expect(actions).toEqual(['activate_vertex_remove_mode']);
  if(outcome==='stop'){fire(button('Stop scientific session'),'click');await settle();}
  if(outcome==='complete')fixture.drawReceipt.mockImplementationOnce(()=>drawn.promise);
  delayed.resolve(outcome==='failure'?new Response(JSON.stringify({error:'refused'}),{status:409}):reply({frame}));
  await settle();
  if(outcome==='complete') {
    expect(mode.disabled).toBe(false);expect(actions).toEqual(['activate_vertex_remove_mode']);
    drawn.resolve();await settle();await new Promise(resolve=>setTimeout(resolve,0));await settle();
  }
  expect(actions).toEqual(outcome==='complete'?['activate_vertex_remove_mode','activate_add_rectangle_mode']:['activate_vertex_remove_mode']);
  if(outcome==='stop')expect(mode.disabled).toBe(true);
});

test('a Labels history action still locks settings throughout the document request', async () => {
  const frame={...structuredClone(cases.valid),layers:cases.valid.layers.map(layer=>({...layer,type:'labels',editableInput:'labels'}))};
  fixture.paint=frame;
  const original=request.getMockImplementation()!, delayed=pending<Response>();
  let holding=false;
  request.mockImplementation(async(path,init)=>{
    if (!path.endsWith('/request'))return original(path,init);
    calls.push({path,init});
    return holding?delayed.promise:reply({frame});
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();fire(button('Edit'),'click');
  holding=true;fire(button('Undo label edit'),'click');await settle();
  const dimensions=fixture.window.querySelectorAll('select').find(row=>row.getAttribute('aria-label')==='Edit dimensions')!;
  expect(dimensions.disabled).toBe(true);expect(button('Run code').disabled).toBe(true);
  delayed.resolve(reply({frame}));await settle();
});

test('a queued Labels mode survives its preceding frame waiting for canvas draw', async () => {
  const frame={...structuredClone(cases.valid),layers:cases.valid.layers.map(layer=>({...layer,type:'labels',editableInput:'labels',
    labelsTool:{mode:'paint',selectedLabel:'21',background:'0',brushSize:3,preserve:false,contiguous:true,editDimensions:2,showSelected:false}}))};
  fixture.paint=frame;
  const original=request.getMockImplementation()!, first=pending<Response>();
  const changes:Record<string,unknown>[]=[];
  request.mockImplementation(async(path,init)=>{
    if(!path.endsWith('/request'))return original(path,init);
    const command=body(init).request;calls.push({path,init});
    if(command.op==='change' && command.request.operation==='labels-tool') {
      changes.push(command.request.value.settings);
      if(changes.length===1)return first.promise;
    }
    return reply({frame});
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();fire(button('Edit'),'click');
  const tool=fixture.window.querySelectorAll('select').find(row=>row.getAttribute('aria-label')==='Label tool')!;
  tool.value='polygon';fire(tool,'change');
  tool.value='paint';fire(tool,'change');
  expect(changes).toEqual([{mode:'polygon'}]);
  const painted=pending<void>();fixture.drawReceipt.mockReturnValueOnce(painted.promise);
  fixture.waiting=frame.frame;
  const polygonFrame={...frame,layers:frame.layers.map(layer=>({...layer,labelsTool:{...layer.labelsTool,mode:'polygon'}}))};
  first.resolve(reply({frame:polygonFrame}));await settle();
  expect(changes).toEqual([{mode:'polygon'}]);
  expect(fixture.drawReceipt).toHaveBeenCalledWith(frame.frame);
  fixture.waiting=null;painted.resolve();fixture.drawn();await settle();
  expect(changes).toEqual([{mode:'polygon'},{mode:'paint'}]);
});

test.each(['cancelled','replacement'])('a queued Labels mode is not resent after its draw receipt is %s', async reason => {
  const frame={...structuredClone(cases.valid),layers:cases.valid.layers.map(layer=>({...layer,type:'labels',editableInput:'labels'}))};
  fixture.paint=frame;
  const original=request.getMockImplementation()!, first=pending<Response>();
  const changes:Record<string,unknown>[]=[];
  request.mockImplementation(async(path,init)=>{
    if(!path.endsWith('/request'))return original(path,init);
    const command=body(init).request;calls.push({path,init});
    if(command.op==='change' && command.request.operation==='labels-tool') {
      changes.push(command.request.value.settings);return first.promise;
    }
    return reply({frame});
  });
  await openStudyScientific(saved);fire(button('Open session'),'click');await settle();fire(button('Edit'),'click');
  const tool=fixture.window.querySelectorAll('select').find(row=>row.getAttribute('aria-label')==='Label tool')!;
  tool.value='polygon';fire(tool,'change');tool.value='paint';fire(tool,'change');
  let cancel!:(error:Error)=>void;
  fixture.drawReceipt.mockReturnValueOnce(new Promise<void>((_resolve,reject)=>{cancel=reject;}));
  first.resolve(reply({frame}));await settle();
  expect(fixture.drawReceipt).toHaveBeenCalledWith(frame.frame);
  if(reason==='replacement') {fire(button('Stop scientific session'),'click');await settle();}
  cancel(new Error('The scientific frame was invalidated'));await settle();
  if(reason==='replacement') {
    fire(button('Open session'),'click');await settle();fire(button('Edit'),'click');
    expect(tool.disabled).toBe(false);
  }
  fixture.drawn();await settle();
  expect(changes).toEqual([{mode:'polygon'}]);
});

test('retained native data offers explicit next-tool selection before any scientific session', async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path=String(input);
    if(path.includes('/tools?')) return reply({source:{id:saved.resource.id,session:'origin',revision:2,digest:'d'.repeat(64)},tools:[]});
    return reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'population-labels',title:'Retained corrected labels',state:{scientificView:{inputs:{}},populationSource:{format:1,source:{id:'e1',revision:3},producer:{implementation:'test-producer'},members:[],corrections:[],nativeInputs:{}}}}}});
  });
  vi.stubGlobal('fetch',fetcher);
  await openStudyScientific(saved);await settle();
  const find=button('Find interactive tools');
  expect(find).toBeDefined();expect(request).not.toHaveBeenCalled();
  const text=fixture.window.textContent;
  expect(text.indexOf('Continue with this window')).toBeLessThan(text.indexOf('Population history and provenance'));
  expect(text.indexOf('Choose saved data')).toBeLessThan(text.indexOf('Population history and provenance'));
  expect(fixture.renamed).toHaveBeenCalledWith(fixture.window,'Retained corrected labels','Retained corrected labels');
  expect(JSON.parse(fixture.window.dataset.studyRetained).resource).toEqual(saved.resource);
  fire(find,'click');await settle();
  expect(fetcher.mock.calls.some(([path])=>String(path)===`/api/scientific-projects/${saved.resource.id}/tools?session=origin&revision=2`)).toBe(true);
  expect(fixture.window.textContent).toContain('No interactive scene integration is available');
  expect(request).not.toHaveBeenCalled();
});

test('chosen result opens the captured revision and presents its selected labels without starting a session',async()=>{
  const digest='d'.repeat(64), controller=new AbortController();
  const fetcher=vi.fn(async(path:RequestInfo|URL)=>String(path).includes('/arrays?')?reply({source:{id:saved.resource.id,session:'origin',revision:2,digest},arrays:[{key:'corrected',name:'Chosen cells',shape:[2,3],dtype:'<u2',semantic:'instance-labels',reference:{component:'array.corrected'},canMeasure:false,measurementPlan:{format:'scientific-object-measurement-plan-v1',available:false,reason:'Not measurable',method:'',calibrationReason:'',columns:[]}}]}):String(path).includes('/resources?')?reply({resources:[]}):reply({project:{id:saved.resource.id,session:'origin',revision:2,digest,document:{kind:'population-labels',title:'Saved segmentation',state:{}}}}));
  vi.stubGlobal('fetch',fetcher);
  expect(await openStudyScientific(saved,{}, {selection:{component:'array.corrected',digest,name:'Chosen cells'},signal:controller.signal,current:()=>true})).toBe(true);
  await settle();expect(fixture.window.textContent).toContain('Chosen result: Chosen cells');
  expect(request).not.toHaveBeenCalled();expect(fetcher.mock.calls.every(([path])=>String(path).includes('revision=2'))).toBe(true);
});
test.each(['digest','owner'])('chosen result refuses a late %s mismatch before exposing actions',async(kind)=>{
  const pendingRead=pending<Response>(),controller=new AbortController();let current=true;
  vi.stubGlobal('fetch',vi.fn(()=>pendingRead.promise));
  const opening=openStudyScientific(saved,{}, {selection:{component:'array.corrected',digest:'d'.repeat(64),name:'Chosen cells'},signal:controller.signal,current:()=>current});
  if(kind==='owner')current=false;
  pendingRead.resolve(reply({project:{id:saved.resource.id,session:'origin',revision:2,digest:kind==='digest'?'e'.repeat(64):'d'.repeat(64),document:{kind:'population-labels',state:{}}}}));
  expect(await opening).toBe(false);expect(button('Choose saved data')).toBeUndefined();expect(request).not.toHaveBeenCalled();
});

test('scientific controls keep exact input and recorded selected tool visible without starting analysis',async()=>{
 const state={scientificView:{},scientificToolChoice:{format:1,name:'Cellpose-SAM',capability:'mine/cellpose#viewer',generation:'b'.repeat(16),source:{id:saved.resource.id,session:'origin',revision:1,digest:'c'.repeat(64)}}};
 vi.mocked(fetch).mockResolvedValueOnce(reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',title:'Human mitosis',state}}}));
 await openStudyScientific(saved);
 // Each tool chosen for one input opens its own window: its name tells them apart.
 expect(fixture.renamed).toHaveBeenCalledWith(fixture.window,'Human mitosis','Human mitosis · Cellpose-SAM');
 const context=fixture.window.querySelector('.scientific-input-context')!;
 expect(context.textContent).toContain('Input: Human mitosis');
 expect(context.textContent).toContain('Tool: Cellpose-SAM');
 expect(context.textContent).toContain('Saved revision 2');
 expect(context.textContent).not.toContain('bbbbbbbb');
 expect(calls).toHaveLength(0);
 expect(button('Run code').disabled).toBe(true);
});


test.each(['explicit','background','arranged','existing'])('only a new explicit continuation focuses its document: %s',async mode=>{
 const target={...saved,placementId:undefined,...(mode==='arranged'?{position:[20,30]}:{})};
 if(mode==='existing')await openStudyScientific(target);
 await openStudyScientific(target,{focus:true,...(mode==='background'?{background:true}:{})});
 expect(fixture.focused).toHaveBeenCalledTimes(mode==='explicit'?1:0);
 expect(calls).toHaveLength(0);
});


test.each([true,false])('only an exact structured generated population title is shortened: %s',generated=>{
 const title=generated?'Retained labels · e14 · revision 3':'My revision 3 · e14 notes';
 vi.mocked(fetch).mockResolvedValueOnce(reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'population-labels',title,state:{populationSource:{format:2,operation:'retain-population-labels-v1',source:{id:'e14',revision:3}}}}}}));
 return openStudyScientific(saved).then(()=>{
  expect(fixture.renamed).toHaveBeenCalledWith(fixture.window,title,generated?'Image and labels':title);
  expect(fixture.window.textContent).toContain(title);
  expect(fixture.window.textContent).toContain('Saved revision 2');
  expect(calls).toHaveLength(0);
 });
});

test('a foreign recorded tool never becomes the visible selected model',async()=>{
 vi.mocked(fetch).mockResolvedValueOnce(reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',title:'My input',state:{scientificView:{},scientificToolChoice:{format:1,name:'Wrong model',capability:'mine/tool#viewer',generation:'b'.repeat(16),source:{id:saved.resource.id,session:'other-project',revision:1}}}}}}));
 await openStudyScientific(saved);
 const context=fixture.window.querySelector('.scientific-input-context')!;
 expect(context.textContent).toContain('Tool: not recorded for this saved work');
 expect(context.textContent).not.toContain('Wrong model');
 expect(calls).toHaveLength(0);
});


test.each(['matching','different-runtime','missing'])('saved output context uses only verified matching retained tool: %s',async mode=>{
 const runtime={bundle:'mine/cellpose',generation:'b'.repeat(16),operation:'viewer'};
 const base={id:'p'+'1'.repeat(32),session:'origin',revision:1,digest:'c'.repeat(64)};
 const state={scientificView:{runtime},hookeOutput:{format:1,base,inputs:[],output:{},producer:{}}};
 const verified=mode==='missing'?null:{name:'Segment cells with Cellpose-SAM',source:base,runtime:{...runtime,generation:mode==='different-runtime'?'d'.repeat(16):runtime.generation}};
 vi.mocked(fetch).mockResolvedValueOnce(reply({project:{id:saved.resource.id,session:'origin',revision:2,document:{kind:'scene',title:'Segmentation',state,runtimes:[runtime]}}}));
 vi.mocked(fetch).mockResolvedValueOnce(reply({ok:true,provenance:{record:{id:saved.resource.id,session:'origin',revision:2},recorded:true,problems:[],edits:[],savedTool:verified}}));
 await openStudyScientific(saved);
 const context=fixture.window.querySelector('.scientific-input-context')!;
 // Until the records answer, the window says it is checking them, never that the name is unavailable.
 if(!context.textContent.includes('Saved tool:'))expect(context.textContent).toContain('Tool: checking the saved records');
 await vi.waitFor(()=>expect(fixture.window.textContent).toContain('Checked against the saved records'));
 if(mode==='matching')expect(context.textContent).toContain('Saved tool: Segment cells with Cellpose-SAM');
 else {await vi.waitFor(()=>expect(context.textContent).toContain('Tool: not recorded for this saved work'));expect(context.textContent).not.toContain('Cellpose');}
 expect(calls).toHaveLength(0);
});


const waitForRedraw = async () => { await new Promise(done => setTimeout(done, 320)); await settle(); };
const status = () => fixture.window.querySelectorAll('p').find(item => item.getAttribute('role') === 'status' && item.parentElement?.className === 'scientific-session-status')!;
test('a preview resized by Expand or Controls is drawn again once, keeping Inspect and View usable without Refresh preview', async () => {
  const frame = structuredClone(cases.valid); fixture.paint = frame;
  await openStudyScientific(saved);
  fire(button('Open session'), 'click'); await settle();
  const renders = () => calls.filter(call => call.init?.body && body(call.init).request?.op === 'render').length;
  expect(renders()).toBe(1);
  fixture.drawn();
  fire(button('Inspect'), 'click');
  status().textContent = 'View remembered for this window.';
  // The window grew: its painted pixels no longer match, so the canvas revokes them.
  fixture.invalidFrame = true; fixture.revoke();
  await waitForRedraw();
  expect(renders()).toBe(2);
  expect(fixture.shown).toHaveBeenCalledTimes(2);
  expect(status().textContent).toBe('View remembered for this window.');
  expect(calls.filter(call => call.path === '/api/scientific-reviews' && call.init?.method === 'POST')).toHaveLength(1);
  // One redraw per drawn preview: a failure or another revoke before it draws is left to Refresh preview.
  fixture.invalidFrame = true; fixture.revoke();
  await waitForRedraw();
  expect(renders()).toBe(2);
});

test('a resized preview in a hidden window waits for the explicit Refresh preview', async () => {
  await openStudyScientific(saved);
  fire(button('Open session'), 'click'); await settle();
  fixture.drawn();
  const original = request.getMockImplementation()!;
  let renders = 0;
  request.mockImplementation(async (path, init) => { if (path.endsWith('/request') && body(init).request.op === 'render') renders++; return original(path, init); });
  fixture.window.hidden = true;
  fixture.invalidFrame = true; fixture.revoke();
  await waitForRedraw();
  expect(renders).toBe(0);
});

test('private changes are said once, as not saved yet, with where Save keeps them', async () => {
  const original = request.getMockImplementation()!;
  request.mockImplementation(async (path, init) => path.endsWith('/request') ? reply({ frame: { ...cases.valid, dirty: true } }) : original(path, init));
  await openStudyScientific(saved); fire(button('Open session'), 'click'); await settle();
  const text = fixture.window.textContent;
  expect(text).toContain('Changes in this session, not saved yet. Save keeps them as a new version; until then they end with the session.');
  expect(text.match(/not saved yet/g)).toHaveLength(1);
  expect(text).not.toContain('Saved Project data is unchanged');
});

test('background scientific restore leaves controls closed; explicit controls reveal their exact document',async()=>{
 vi.stubGlobal('HTMLElement',TestElement);
 vi.stubGlobal('innerWidth',1100);vi.stubGlobal('innerHeight',900);
 vi.stubGlobal('localStorage',{getItem:()=>null,setItem:()=>{}});
 vi.stubGlobal('MutationObserver',class {observe(){} disconnect(){}});
 Object.assign(document,{querySelector:(selector:string)=>document.body.querySelector(selector),querySelectorAll:(selector:string)=>document.body.querySelectorAll(selector)});
 document.body.dataset.workspaceProject='origin';
 const deck=configureControlDeck('isolated',()=>{});
 try{
  await openStudyScientific(saved,{background:true});await settle();
  const handle=[...deck.controls][0];expect(handle).toBeDefined();
  expect(handle.open).toBe(false);
  expect(fixture.applied).not.toHaveBeenCalled();
  handle.launcher.dispatchEvent(new Event('click'));
  expect(handle.visible).toBe(true);
  expect(fixture.applied).toHaveBeenCalledExactlyOnceWith('document:placement',{front:true});
  expect(calls).toHaveLength(0);
  fixture.applied.mockClear();
  document.body.dataset.activeWorkspaceView='other';deck.refresh();
  handle.launcher.dispatchEvent(new Event('click'));
  expect(handle.visible).toBe(false);expect(fixture.applied).not.toHaveBeenCalled();
 }finally{deck.dispose();}
});
