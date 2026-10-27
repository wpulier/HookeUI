// Synthetic renderer and command-boundary tests; real example/Studio acceptance is separate.
import { afterEach, expect, test, vi } from 'vitest';
import { syntheticDOM } from './workspace-windows.test-support';
import { ExampleGuide, exampleProject, type OnboardingState } from './onboarding';

afterEach(() => vi.unstubAllGlobals());
const initial: OnboardingState = { version: 1, status: 'active', step: 'analyze', session: 'example', asset: null, result: null, localOnly: true };
function setup() {
  const dom = syntheticDOM(); vi.stubGlobal('document', dom.document);
  const root = dom.document.createElement('section');
  let state = { ...initial }; let session = 'example';
  const request = vi.fn(async (action: string): Promise<{ ok: boolean; onboarding: OnboardingState; rendered?: {session: string; id: string; revision: number} | null }> => {
    if (action === 'analyze') state = { ...state, step: 'measurements', result: { id: 'e1', revision: 0 } };
    if (action === 'measurements') state = { ...state, step: 'return' };
    if (action === 'finish') state = { ...state, status: 'complete', step: 'complete' };
    if (action === 'dismiss') state = { ...state, status: 'dismissed' };
    return { ok: true, onboarding: state, rendered: ['analyze', 'start', 'restart', 'finish'].includes(action) && state.result
      ? { session, ...state.result } : null };
  });
  const measurements = vi.fn();
  const specimen = vi.fn();
  let toolbox = false;
  const guide = new ExampleGuide(root as unknown as HTMLElement, { act: request, session: () => session,
    measurements, specimen, addFiles: vi.fn(), capabilities: vi.fn(), onToolbox: () => toolbox });
  return { root, guide, request, measurements, specimen, session: (value: string) => { session = value; guide.sessionChanged(); },
    toolbox: (value: boolean) => { toolbox = value; guide.routeChanged(); } };
}
async function tick() { for (let i = 0; i < 12; i++) await Promise.resolve(); }

test('real analysis success enables measurements; only loading the exact table advances the guide', async () => {
  const w = setup(); await w.guide.refresh();
  expect(w.root.textContent).toContain('1 of 3');
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.root.textContent).not.toContain('Waiting for the outlines to appear');
  expect(w.root.textContent).toContain('See how large and bright they are.');
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.measurements).toHaveBeenCalledWith('e1', { session: 'example', revision: 0 });
  expect(w.request.mock.calls.map(c => c[0])).not.toContain('measurements');
  await w.guide.measurementsLoaded({ id: 'wrong', session: 'example', revision: 0 });
  expect(w.request.mock.calls.map(c => c[0])).not.toContain('measurements');
  await w.guide.measurementsLoaded({ id: 'e1', session: 'example', revision: 0 });
  expect(w.root.textContent).toContain('Your image and results are saved in this project.');
});

test('while the analysis runs the bar says it is finding them, never Retry, until its reply and receipt come', async () => {
  const w = setup(); await w.guide.refresh();
  let resolve!: (r: { ok: boolean; onboarding: OnboardingState; rendered: { session: string; id: string; revision: number } }) => void;
  w.request.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const button = w.root.querySelector('.example-primary')!;
  button.dispatchEvent(new Event('click')); await tick();
  // The outlines may already be drawn: the server keeps the result and confirms its pixels before it replies.
  expect(w.root.querySelector('.example-status')!.textContent).toBe('Finding bright regions on this Mac…');
  expect(button.textContent).toBe('Find bright objects');
  expect(button.disabled).toBe(true);
  resolve({ ok: true, onboarding: { ...initial, step: 'measurements', result: { id: 'e1', revision: 0 } },
    rendered: { session: 'example', id: 'e1', revision: 0 } }); await tick();
  expect(w.root.querySelector('.example-status')!.textContent).toBe('');
  expect(button.textContent).toBe('See measurements');
  expect(button.disabled).toBe(false);
});

test('a switched session pauses guidance and cannot acknowledge another session’s table', async () => {
  const w = setup(); await w.guide.refresh();
  w.session('mine'); await tick();
  expect(w.root.hidden).toBe(true);
  await w.guide.measurementsLoaded({ id: 'e1', session: 'example', revision: 0 });
  expect(w.request.mock.calls.map(c => c[0])).not.toContain('measurements');
  w.session('example'); await tick();
  expect(w.root.hidden).toBe(false);
});

test('duplicate analysis clicks share one pending action; a failed step offers Retry and Skip', async () => {
  const w = setup(); await w.guide.refresh();
  let reject!: (error: Error) => void;
  w.request.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  const button = w.root.querySelector('.example-primary')!;
  button.dispatchEvent(new Event('click')); button.dispatchEvent(new Event('click'));
  expect(w.request.mock.calls.filter(c => c[0] === 'analyze')).toHaveLength(1);
  reject(new Error('The example is missing. Restore it and retry.')); await tick();
  expect(w.root.textContent).toContain('The example is missing');
  expect(button.textContent).toBe('Retry');
  expect(button.disabled).toBe(false);
  expect(w.root.querySelector('.example-skip')!.textContent).toBe('Skip walkthrough');
});

test('disconnect preserves prompt and control identity; reconnect refreshes without starting work', async () => {
  const w = setup(); await w.guide.refresh();
  const button = w.root.querySelector('.example-primary');
  w.guide.connectionChanged(false);
  expect(w.root.textContent).toContain('Connection lost');
  expect(button!.disabled).toBe(true);
  w.guide.connectionChanged(true); await tick();
  expect(w.root.querySelector('.example-primary')).toBe(button);
  expect(w.request.mock.calls.every(c => c[0] === 'status')).toBe(true);
});

test('skipping during analysis hides guidance and a late success cannot reopen it', async () => {
  const w = setup(); await w.guide.refresh();
  let resolve!: (r: { ok: boolean; onboarding: OnboardingState }) => void;
  w.request.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click'));
  w.root.querySelector('.example-skip')!.dispatchEvent(new Event('click')); await tick();
  expect(w.root.hidden).toBe(true);
  resolve({ ok: true, onboarding: { ...initial, step: 'measurements', result: { id: 'e1', revision: 0 } } }); await tick();
  expect(w.root.hidden).toBe(true);
});

test('saved completion offers opening personal files and capabilities, but does not reappear on another session', async () => {
  const w = setup(); await w.guide.refresh();
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  await w.guide.measurementsLoaded({ id: 'e1', session: 'example', revision: 0 });
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.root.textContent).toContain('Ready to explore your own?');
  expect(w.root.querySelector('.example-primary')!.textContent).toBe('Open your image or video');
  w.session('mine'); await tick(); expect(w.root.hidden).toBe(true);
  w.session('example'); await tick(); expect(w.root.hidden).toBe(true);
});

test.each(['start', 'restart'] as const)('Skip during pending %s dismisses the returned session and never resumes the guide', async (action) => {
  const w = setup();
  if (action === 'restart') await w.guide.refresh();
  let resolve!: (r: { ok: boolean; onboarding: OnboardingState }) => void;
  w.request.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  const opening = w.guide.start(action === 'restart');
  w.root.querySelector('.example-skip')!.dispatchEvent(new Event('click'));
  expect(w.root.hidden).toBe(true);
  resolve({ ok: true, onboarding: { ...initial, session: 'new-example' } });
  await opening;
  expect(w.request).toHaveBeenCalledWith('dismiss', { session: 'new-example' });
  expect(w.root.hidden).toBe(true);
});

test('reconnect with a persisted analysis still waits for the matching masks to paint', async () => {
  const w = setup();
  w.request.mockResolvedValue({ ok: true, onboarding: { ...initial, step: 'measurements', result: { id: 'e1', revision: 0 } } });
  await w.guide.refresh();
  expect(w.root.textContent).toContain('Waiting for the outlines');
  w.request.mockResolvedValue({ ok: true, onboarding: { ...initial, step: 'measurements', result: { id: 'e1', revision: 0 } }, rendered: { session: 'other', id: 'e1', revision: 0 } });
  await w.guide.start();
  expect(w.root.textContent).toContain('Waiting for the outlines');
  w.request.mockResolvedValue({ ok: true, onboarding: { ...initial, step: 'measurements', result: { id: 'e1', revision: 0 } }, rendered: { session: 'example', id: 'e1', revision: 0 } });
  await w.guide.start();
  expect(w.root.textContent).toContain('See how large and bright they are.');
});

test('a delayed dismissal reply cannot replace a newer explicit restart', async () => {
  const w = setup(); await w.guide.refresh();
  let resolve!: (r: { ok: boolean; onboarding: OnboardingState }) => void;
  w.request.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  w.root.querySelector('.example-skip')!.dispatchEvent(new Event('click'));
  await w.guide.start(true);
  expect(w.root.hidden).toBe(false);
  resolve({ ok: true, onboarding: { ...initial, status: 'dismissed' } }); await tick();
  expect(w.root.hidden).toBe(false);
});


test('an analysis response without its native render receipt cannot enable measurements', async () => {
  const w = setup(); await w.guide.refresh();
  w.request.mockResolvedValue({ ok: true, onboarding: { ...initial, step: 'measurements', result: { id: 'e1', revision: 0 } } });
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.root.textContent).toContain('Waiting for the outlines');
  await w.guide.measurementsLoaded({ session: 'example', id: 'e1', revision: 0 });
  expect(w.request.mock.calls.map(c => c[0])).not.toContain('measurements');
  expect(w.root.querySelector('.example-skip')!.textContent).toBe('Skip walkthrough');
});


test('explicit resume shows the specimen before requesting its visible render receipt', async () => {
  const w = setup();
  w.request.mockImplementationOnce(async () => {
    expect(w.specimen).toHaveBeenCalledOnce();
    return { ok: true, onboarding: initial };
  });
  await w.guide.start();
  expect(w.root.textContent).not.toContain('Retry');
});


test('explicit analysis Retry restores the specimen before asking, but completion never takes the page back', async () => {
  const w = setup(); await w.guide.refresh();
  w.request.mockRejectedValueOnce(new Error('The retained image was not visible.'));
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.specimen).toHaveBeenCalledOnce();
  w.specimen.mockClear();
  let resolve!: (reply: {ok: boolean; onboarding: OnboardingState; rendered: {session: string; id: string; revision: number}}) => void;
  w.request.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click'));
  expect(w.specimen).toHaveBeenCalledOnce();
  // The user is free to leave the page while the request is pending.
  resolve({ ok: true, onboarding: {...initial, step:'measurements', result:{id:'e1',revision:0}},
    rendered:{session:'example',id:'e1',revision:0} }); await tick();
  expect(w.specimen).toHaveBeenCalledOnce();
});


test('Restart stays in More while the current step and Skip remain directly available', async () => {
  const w = setup(); await w.guide.refresh();
  const more = w.root.querySelector('.example-more')!;
  expect(more.tagName).toBe('DETAILS');
  expect(more.getAttribute('open')).toBeNull();
  expect(more.querySelector('summary')!.getAttribute('aria-label')).toBe('More walkthrough options');
  expect(w.root.querySelector('.example-skip')!.closest('details')).toBeNull();
  expect(w.root.querySelector('.example-primary')!.closest('details')).toBeNull();
  const restart = more.querySelector('.example-restart')!;
  expect(restart.title).toContain('previous image and results stay saved');
  restart.dispatchEvent(new Event('click')); await tick();
  expect(w.request.mock.calls.map(c => c[0])).toContain('restart');
});

test('on the Toolbox the bar steps aside only once the example is finished, never mid-walkthrough', async () => {
  const w = setup(); await w.guide.refresh();
  w.toolbox(true); expect(w.root.hidden).toBe(false); // step 1 of 3: the way back stays
  w.toolbox(false);
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  await w.guide.measurementsLoaded({ id: 'e1', session: 'example', revision: 0 });
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.root.textContent).toContain('Example complete');
  w.toolbox(true); expect(w.root.hidden).toBe(true);
  w.toolbox(false); expect(w.root.hidden).toBe(false);
});

test('opening its example: busy while it opens, then its session is said to be its own once; refusals that name files are said plainly', async () => {
  const w = setup();
  const opened = vi.fn();
  const guide = new ExampleGuide(w.root as unknown as HTMLElement, { act: w.request, session: () => 'example', measurements: vi.fn(),
    specimen: vi.fn(), addFiles: vi.fn(), capabilities: vi.fn(), opened });
  const start = guide.start();
  expect(guide.opening()).toBe(true); // the server opens the session itself: the studio must not land it
  await start;
  expect(guide.opening()).toBe(false);
  expect(opened).toHaveBeenCalledTimes(1);
  expect(guide.openedNow('other')).toBe(false);
  expect(guide.openedNow('example')).toBe(true); // a `session-cleared` that came after the reply
  expect(guide.openedNow('example')).toBe(false); // once
  expect(opened).toHaveBeenCalledWith(true);
  const { plainProblem } = await import('./onboarding');
  expect(plainProblem('the camera bridge could not play it: /tmp/x/human.png is not in a folder')).toBe('The example could not be opened. Try again, or skip the walkthrough.');
  expect(plainProblem('KeyError: \'2026-10-15.2\'')).toBe('The example could not be opened. Try again, or skip the walkthrough.');
  expect(plainProblem('Connection lost.')).toBe('Connection lost.');
  expect(plainProblem('Measured 5/10/2 regions')).toBe('Measured 5/10/2 regions'); // a slash is not a file
});

test('a refusal of another step is said as the server said it, never as the example failing to open', async () => {
  const w = setup(); await w.guide.refresh();
  w.request.mockImplementationOnce(async () => ({ ok: false, error: 'No regions were found in /tmp/x: try another threshold', onboarding: initial }));
  w.root.querySelector('.example-primary')!.dispatchEvent(new Event('click')); await tick();
  expect(w.root.querySelector('.example-status')!.textContent).toBe('No regions were found in /tmp/x: try another threshold');
});

test('the example Project is published once the server names it, so the workspace can say it stays on this Mac', async () => {
  const seen: unknown[] = [];
  vi.stubGlobal('window', { dispatchEvent: (event: CustomEvent) => { seen.push(event.detail); return true; } });
  vi.stubGlobal('CustomEvent', class { type: string; detail: unknown; constructor(type: string, init: { detail: unknown }) { this.type = type; this.detail = init.detail; } });
  const w = setup();
  const named = { ok: true, onboarding: { ...initial, session: 'example-2' } };
  w.request.mockResolvedValueOnce(named); await w.guide.refresh();
  expect(exampleProject()).toBe('example-2');
  expect(seen).toContainEqual({ session: 'example-2', localOnly: true });
  const count = seen.length; w.request.mockResolvedValueOnce(named); await w.guide.refresh();
  expect(seen.length).toBe(count);
});
