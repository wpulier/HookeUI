// The Working strip's other sources (strip-sources.ts): a hand-started run, agent steps still running after their
// answer, and models loading. Each says only what the app knows; none shows a percent; work under a second shows none.
import { expect, test } from 'vitest';
import { isVoiceSession, KEPT_RUNS, LoadPills, pillName, QUICK_MS, runPill, RunPills, StepPills, type HandRun, type RunFacts, type StepFact } from './strip-sources';
import type { ActivityEvent } from './protocol';
import type { StripPill } from './working-strip';

const T = 1_791_600_000_000;
const run = (fields: Partial<RunFacts> = {}): RunFacts => ({ id: 'run1', status: 'running', message: 'Running on the confirmed input.', started: T, ...fields });

test('a run counts its time once it has lasted a second, then says what happened', () => {
  const going = runPill(run(), 'Cellpose-SAM', () => {}, () => {})!;
  expect(going).toMatchObject({ id: 'run:run1', name: 'Cellpose-SAM', cell: 'working', urgency: 1, counting: true, since: T, after: T + QUICK_MS });
  expect(going.words).toBeUndefined();
  expect(going.hide).toBe('Hide Cellpose-SAM from the strip');
  const done = runPill(run({ status: 'completed', ended: T + 4000, message: 'Done in Untitled › Main. 271 cells' }), 'Cellpose-SAM', () => {}, () => {})!;
  expect(done).toMatchObject({ cell: 'ready', urgency: 2, words: 'Done', counting: false });
  expect(done.after).toBeUndefined(); // a finished pill stays until opened or hidden
  expect(done.title).toContain('271 cells');
  expect(done.title).toContain('Open its results in Data');
  expect(runPill(run({ status: 'stopping' }), 'X', () => {}, () => {})).toMatchObject({ cell: 'still', words: 'Stop requested', urgency: 1 });
  expect(runPill(run({ status: 'queued' }), 'X', () => {}, () => {})).toMatchObject({ cell: 'queued', words: 'Queued' });
  expect(runPill(run({ status: 'failed', ended: T + 3000 }), 'X', () => {}, () => {})).toMatchObject({ cell: 'attention', words: 'Failed', urgency: 0 });
  expect(runPill(run({ status: 'failed', interrupted: true, ended: T + 3000 }), 'X', () => {}, () => {})).toMatchObject({ words: 'Interrupted' });
  expect(runPill(run({ status: 'stopped', ended: T + 3000 }), 'X', () => {}, () => {})).toMatchObject({ cell: 'stopped', words: 'Stopped', urgency: 2 });
  expect(runPill(run({ status: 'unknown', ended: T + 3000 }), 'X', () => {}, () => {})).toMatchObject({ words: 'Status unknown' });
});

test("a run's pill says its model's short name; its tooltip keeps the whole one", () => {
  const pill = runPill(run(), 'Bright objects · Basic local method', () => {}, () => {})!;
  expect(pill.name).toBe('Bright objects');
  expect(pill.hide).toBe('Hide Bright objects from the strip');
  expect(pill.title.split('\n')[0]).toBe('Bright objects · Basic local method · Running');
  expect(pillName('SAM 3.1 (cloud)')).toBe('SAM 3.1 (cloud)'); // only " · " shortens: variants stay told apart
  expect(pillName('micro-sam (light microscopy)')).toBe('micro-sam (light microscopy)');
});

test('a run that ended within a second never shows a pill', () => {
  expect(runPill(run({ status: 'completed', ended: T + 400 }), 'X', () => {}, () => {})).toBeNull();
  expect(runPill(run({ status: 'failed', ended: T + 999 }), 'X', () => {}, () => {})).toBeNull();
  expect(runPill(run({ status: 'completed', ended: T + 1200 }), 'X', () => {}, () => {})).not.toBeNull();
});

// The runs started by hand, one pill each (RunPills, workspace-study.ts): a new start never takes another's pill.
type Hand = HandRun & { backgroundJob?: { id: string; session: string } };
function hands(session = 'p1') {
  let now = T, pills: StripPill[] = [], sinks = 0;
  const calls: string[] = [];
  const runs = new RunPills<Hand>(() => calls.push('results'), (capability) => calls.push(`page ${capability}`), (p) => { pills = p; sinks++; }, () => now);
  const hand = (id: string, fields: Partial<Hand> = {}): Hand => ({ id, status: 'running', message: 'Running on the confirmed input.', input: { session }, ...fields });
  return { runs, hand, calls, pills: () => pills, sinks: () => sinks, ids: () => pills.map((p) => p.id), tick: (ms: number) => { now += ms; } };
}

test('one run: its pill counts, turns green once done, and leaves when its results are opened, as before', () => {
  const { runs, hand, calls, pills, sinks, tick } = hands();
  runs.render();
  expect(sinks()).toBe(0); // nothing started: the strip is not touched
  const a = hand('a'); runs.start(a, 'cells', 'Bright objects · Basic local method'); runs.render();
  expect(pills()).toHaveLength(1);
  expect(pills()[0]).toMatchObject({ id: 'run:a', name: 'Bright objects', cell: 'working', counting: true, since: T });
  pills()[0].open();
  expect(calls).toEqual(['page cells']); // still running: its run page
  tick(4000); a.status = 'stopping'; a.message = 'Stop requested. Waiting for the work to confirm it has ended.'; runs.render();
  expect(pills()[0]).toMatchObject({ cell: 'still', words: 'Stop requested' }); // never Stopped until it is confirmed
  a.status = 'completed'; a.message = 'Done in Untitled › Main. 12 bright regions'; runs.render();
  expect(pills()[0]).toMatchObject({ cell: 'ready', words: 'Done', urgency: 2 });
  expect(pills()[0].words).not.toMatch(/%/);
  const before = sinks(); runs.render();
  expect(sinks()).toBe(before); // nothing changed: not redrawn
  pills()[0].open();
  expect(calls).toEqual(['page cells', 'results']);
  expect(pills()).toEqual([]);
});

test('two runs: the first finishes, the second starts, both pills stay; opening or hiding the first removes only it', () => {
  for (const leave of ['open', 'hide'] as const) {
    const { runs, hand, calls, ids, pills, tick } = hands();
    const a = hand('a'); runs.start(a, 'cells', 'Bright objects'); runs.render();
    tick(5000); a.status = 'completed'; a.message = 'Done in Untitled › Main. 12 bright regions'; runs.render();
    tick(2000); const b = hand('b'); runs.start(b, 'catalog:cellpose', 'Cellpose-SAM'); runs.render();
    expect(ids()).toEqual(['run:a', 'run:b']);
    expect(pills()[0]).toMatchObject({ words: 'Done', cell: 'ready' });
    expect(pills()[1]).toMatchObject({ cell: 'working', counting: true, since: T + 7000 });
    const first = pills()[0];
    if (leave === 'open') first.open(); else first.dismiss();
    expect(calls).toEqual(leave === 'open' ? ['results'] : []);
    expect(ids()).toEqual(['run:b']);
    expect(runs.get('a')).toBeUndefined();
    expect(runs.get('b')).toBe(b);
  }
});

test('a failed run keeps its pill (needs you) while another task runs; its click opens its run page and it stays', () => {
  const { runs, hand, calls, ids, pills, tick } = hands();
  const a = hand('a'); runs.start(a, 'cells', 'Bright objects'); runs.render();
  tick(3000); a.status = 'failed'; a.message = 'The model did not load.'; runs.render();
  const b = hand('b'); runs.start(b, 'catalog:cellpose', 'Cellpose-SAM'); runs.render();
  expect(ids()).toEqual(['run:a', 'run:b']);
  expect(pills()[0]).toMatchObject({ cell: 'attention', words: 'Failed', urgency: 0 });
  pills()[0].open();
  expect(calls).toEqual(['page cells']);
  expect(ids()).toEqual(['run:a', 'run:b']);
  // The earlier run settled later (heard through its id): its own pill says so; the newer one is untouched.
  runs.get('a')!.status = 'stopped'; runs.render();
  expect(pills()[0]).toMatchObject({ words: 'Stopped' });
  expect(pills()[1]).toMatchObject({ cell: 'working' });
});

test('Run again of the same task: an earlier run that failed or stopped gives its place; a done one stays until opened', () => {
  const { runs, hand, ids, pills, tick } = hands();
  const a = hand('a'); runs.start(a, 'cells', 'Bright objects'); runs.render();
  tick(3000); a.status = 'failed'; a.message = 'The model did not load.'; runs.render();
  expect(pills()[0]).toMatchObject({ words: 'Failed' });
  const b = hand('b'); runs.start(b, 'cells', 'Bright objects'); runs.render(); // its page's Run again
  expect(ids()).toEqual(['run:b']); // the failed pill no longer leads the strip over a run its page no longer shows
  tick(3000); b.status = 'completed'; b.message = 'Done in Untitled › Main. 12 bright regions'; runs.render();
  const c = hand('c'); runs.start(c, 'cells', 'Bright objects'); runs.render();
  expect(ids()).toEqual(['run:b', 'run:c']); // results not yet opened: kept
});

test('Data opened from a run page takes off the one run done in that Project; with two done neither is guessed', () => {
  const { runs, hand, ids, tick } = hands('p1');
  const a = hand('a'); runs.start(a, 'cells', 'Bright objects'); runs.render();
  tick(3000); a.status = 'completed'; runs.render();
  const elsewhere = hand('x', { input: { session: 'p2' } }); runs.start(elsewhere, 'cells', 'Bright objects'); runs.render();
  tick(3000); elsewhere.status = 'completed'; runs.render();
  const b = hand('b'); runs.start(b, 'cells', 'Bright objects'); runs.render();
  tick(3000); b.status = 'completed'; runs.render();
  runs.resultsOpened('p1'); // a and b are both done in p1: which run's page it was is not known
  expect(ids()).toEqual(['run:a', 'run:x', 'run:b']);
  runs.leave('a');
  runs.resultsOpened('p1');
  expect(ids()).toEqual(['run:x']); // the other Project's run is not in this Data
  runs.resultsOpened('p2');
  expect(ids()).toEqual([]);
});

test('the workspace keeps a pill per run: a start adds one, and an earlier run is still heard by its id', async () => {
  const { readFileSync } = await import('node:fs');
  const { fileURLToPath } = await import('node:url');
  const study = readFileSync(fileURLToPath(new URL('./workspace-study.ts', import.meta.url)), 'utf8');
  expect(study).toContain('new RunPills<StudyRun>(');
  expect(study).toContain('runPills.start(run,');
  expect(study).not.toMatch(/runFacts\.clear\(\)|runPills\.clear\(\)/); // a start never takes the others' pills
  // Settled later: its pill's run, or a run whose pill was hidden (× hides the pill, never the run).
  expect(study).toContain("const kept = runPills.get(data.run) ?? handRuns.get(data.run)?.run; if (kept) { kept.status = data.outcome;");
  // Every hand-started run is kept until it ends, for what a restart or a quit would stop (restart-readiness.ts).
  expect(study).toContain("handRuns.set(run.id,{run,name:bridge.runName(String(data.capability),data.use)});");
  expect(study).toContain("for(const kept of new Set([...runPills.runs(),...[...handRuns.values()].map(hand=>hand.run)])){");
  expect(study).toMatch(/resultButton\.addEventListener\('click',\(\)=>\{if\(run\?\.status==='completed'\)runPills\.leave\(run\.id\);\}\)/);
  expect(study).toContain("runPills.resultsOpened(snapshot.session)");
  expect(study).toContain("!(kept.status==='stopping'&&next.status==='queued')"); // Stop requested until confirmed
});

test('runs that ended within a second keep no place, and at most KEPT_RUNS are kept, the oldest ended first', () => {
  const { runs, hand, ids, tick } = hands();
  const quick = hand('quick'); runs.start(quick, 'cells', 'X'); runs.render();
  tick(300); quick.status = 'completed'; runs.render();
  expect(runs.get('quick')).toBeUndefined();
  const working = hand('w0'); runs.start(working, 'cells', 'X'); runs.render(); // still at work: kept past the rest
  for (let i = 1; i <= KEPT_RUNS + 2; i++) {
    tick(2000);
    const r = hand(`d${i}`); runs.start(r, 'cells', 'X'); runs.render();
    tick(2000); if (i <= KEPT_RUNS + 1) { r.status = 'completed'; runs.render(); }
  }
  expect(runs.runs()).toHaveLength(KEPT_RUNS);
  expect(ids()).toContain('run:w0');
  expect(ids()).toContain(`run:d${KEPT_RUNS + 2}`); // the newest, still running
  expect(ids()).not.toContain('run:d1');
  expect(ids()).not.toContain('run:d2');
  expect(ids()).not.toContain('run:d3');
});

test('agent steps: a pill while one runs after its answer, green once done until opened or hidden, a failure said', () => {
  let pills: StripPill[] = [];
  let opened = 0;
  const steps = new StepPills(() => { opened++; }, (p) => { pills = p; }, () => T);
  const step = (key: string, ok: boolean | null, fields: Partial<StepFact> = {}): StepFact => ({ key, tool: 'outline', ok, summary: '', ...fields });
  // A step that finished inside its answer never runs after it: no pill.
  steps.update([step('1:0', true)]);
  expect(pills).toEqual([]);
  steps.update([step('1:0', true), step('2:0', null, { at: (T - 4000) / 1000, progress: 'loading micro-sam on this Mac, usually about 2 min' })]);
  expect(pills).toHaveLength(1);
  expect(pills[0]).toMatchObject({ id: 'step:2:0', cell: 'working', urgency: 1, counting: true, since: T - 4000 });
  expect(pills[0].title).toContain('Loading micro-sam on this Mac, usually about 2 min…');
  steps.update([step('1:0', true), step('2:0', true, { summary: 'outlined 271 cells' })]);
  expect(pills[0]).toMatchObject({ cell: 'ready', words: 'Done', urgency: 2 });
  pills[0].open();
  expect(opened).toBe(1);
  expect(pills).toEqual([]); // opened: it leaves the strip
  steps.update([step('3:0', null)]);
  steps.update([step('3:0', false, { summary: 'the model did not load' })]);
  expect(pills[0]).toMatchObject({ cell: 'attention', words: 'Failed', urgency: 0 });
  pills[0].dismiss();
  expect(pills).toEqual([]);
  steps.update([step('4:0', null)]);
  steps.update([]); // another conversation: its steps are not this one's
  expect(pills).toEqual([]);
});

test('one pill per action: a run\'s own load joins its run; one loading on after its run says Loading; a key is named', () => {
  let pills: StripPill[] = [];
  let live = true;
  const loads = new LoadPills(() => {}, (p) => { pills = p; }, {
    claimed: (e) => live && e.ref?.model === 'Cellpose-SAM (on this Mac)',
    models: () => [{ id: 'swin2sr-enlarge', name: 'Swin2SR enlarge', runs: 'mac' }] });
  const event = (id: string, model: string, state: ActivityEvent['state'] = 'running'): ActivityEvent =>
    ({ type: 'activity', id, kind: 'model', text: `Loading ${model}`, at: T / 1000, state, ref: { model } });
  loads.apply(event('model:cp:1', 'Cellpose-SAM (on this Mac)'));
  expect(pills).toEqual([]); // the run's pill and footer carry it: no second pill beside the run
  live = false; loads.refresh(); // its run ended (stopped) while it still loads
  expect(pills).toHaveLength(1);
  expect(pills[0]).toMatchObject({ name: 'Cellpose-SAM (on this Mac)', counting: false, words: 'Loading', cell: 'working' });
  // A catalog model's load named by its key in the Activity line: its display name, with where it runs.
  loads.apply(event('model:sw:1', 'swin2sr-enlarge'));
  expect(pills.map((p) => p.name)).toContain('Swin2SR enlarge (on this Mac)');
  expect(pills.map((p) => p.name)).not.toContain('swin2sr-enlarge');
});

test('model loads: a pill while one loads past a second; a load that ends leaves, one that fails stays', () => {
  let pills: StripPill[] = [];
  const loads = new LoadPills(() => {}, (p) => { pills = p; });
  const event = (id: string, state: ActivityEvent['state'], text: string, kind: ActivityEvent['kind'] = 'model'): ActivityEvent =>
    ({ type: 'activity', id, kind, text, at: T / 1000, state, ref: { model: 'micro-sam' } });
  loads.apply(event('model:micro-sam:1', 'running', 'Loading micro-sam (usually about 2 min)'));
  expect(pills).toHaveLength(1);
  expect(pills[0]).toMatchObject({ id: 'load:model:micro-sam:1', name: 'micro-sam', cell: 'working', counting: true, after: T + QUICK_MS });
  expect(pills[0].title).toContain('Loading micro-sam (usually about 2 min)');
  loads.apply(event('model:micro-sam:1', 'done', 'micro-sam is ready (loaded in 1 min 50 s)'));
  expect(pills).toEqual([]);
  loads.apply(event('model:micro-sam:2', 'failed', 'micro-sam did not load: out of memory'));
  expect(pills[0]).toMatchObject({ cell: 'attention', words: 'Did not load', urgency: 0 });
  loads.apply(event('x', 'running', 'Clicked', 'click')); // not a model line
  expect(pills).toHaveLength(1);
  loads.clear();
  expect(pills).toEqual([]);
  loads.apply(event('model:micro-sam:3', 'running', 'Loading micro-sam'));
  pills[0].dismiss();
  expect(pills).toEqual([]);
});

test('a download fills its cell from the measured bytes only, turns Ready once its files are in, and opens where it was asked', async () => {
  const { DownloadPills } = await import('./strip-sources');
  let shown: StripPill[] = [];
  const opened: string[] = [];
  const pills = new DownloadPills((model) => opened.push(model), (next) => { shown = next; }, () => T);
  const row = (state: string, got: number, total: number) => ({ model: 'micro-sam-lm', name: 'micro-sam (light microscopy)', state, got, total, says: '' });
  pills.update([row('done', 413_000_000, 413_000_000)]); // an old finished download never appears
  expect(shown).toEqual([]);
  pills.update([row('downloading', 173_460_000, 413_000_000)]);
  expect(shown).toHaveLength(1);
  expect(shown[0]).toMatchObject({ id: 'download:micro-sam-lm', name: 'micro-sam (light microscopy)', cell: 'setup', words: '42%', urgency: 1, after: T + QUICK_MS });
  expect(shown[0].fraction).toBeCloseTo(0.42, 5);
  pills.update([row('downloading', 1_000_000, 0)]); // no total: no fill, no percent
  expect(shown[0]).toMatchObject({ fraction: null, words: 'Downloading' });
  pills.update([row('done', 413_000_000, 413_000_000)], [{ id: 'micro-sam-lm', status: 'ready' }]);
  expect(shown[0]).toMatchObject({ cell: 'ready', words: 'Ready', urgency: 2 });
  shown[0].open();
  expect(opened).toEqual(['micro-sam-lm']);
  expect(shown).toEqual([]); // opened: it leaves the strip
});

test('a download is Ready only when its model is: with an install still to come it says Downloaded on a quiet cell', async () => {
  const { DownloadPills } = await import('./strip-sources');
  let shown: StripPill[] = [];
  const opened: string[] = [];
  const pills = new DownloadPills((model) => opened.push(model), (next) => { shown = next; }, () => T);
  const row = (state: string) => ({ model: 'swin2sr-enlarge', name: 'Swin2SR enlarge', state, got: 4e6, total: 4e6, says: '' });
  const models = [{ id: 'swin2sr-enlarge', status: 'needs-setup' }];
  pills.update([row('downloading')], models);
  pills.update([row('done')], models);
  expect(shown[0]).toMatchObject({ cell: 'idle', words: 'Downloaded', fraction: null });
  expect(shown[0].title).toContain('its run page names the next step');
  pills.update([row('done')], [{ id: 'swin2sr-enlarge', status: 'ready' }]); // installed since: now it is Ready
  expect(shown[0]).toMatchObject({ cell: 'ready', words: 'Ready' });
  pills.update([row('done')], []); // no row for it: not said Ready
  expect(shown[0]).toMatchObject({ cell: 'idle', words: 'Downloaded' });
  shown[0].open();
  expect(opened).toEqual(['swin2sr-enlarge']); // its run page, whose primary names the next step
});


// Ticket 67, fix 4: a finished download whose model Hooke has not checked yet is not "Ready" (Models says Ready, not
// checked yet), and it can run, so its cell is the green outline, never the grey of what isn't there.
test('a finished download says Downloaded · not checked yet until Hooke has checked its model', async () => {
  const { DownloadPills } = await import('./strip-sources');
  let shown: StripPill[] = [];
  const pills = new DownloadPills(() => {}, (next) => { shown = next; }, () => T);
  const row = (state: string) => ({ model: 'cellpose-sam', name: 'Cellpose-SAM', state, got: 1e9, total: 1e9, says: '' });
  const model = (face: string) => [{ id: 'cellpose-sam', status: 'ready', group: 'ready', runs: 'this-mac', size: 1e9, face: { state: face } }];
  pills.update([row('downloading')], model('unchecked'));
  pills.update([row('done')], model('unchecked'));
  expect(shown[0]).toMatchObject({ cell: 'partial', words: 'Downloaded · not checked yet' });
  pills.update([row('done')], model('on'));
  expect(shown[0]).toMatchObject({ cell: 'ready', words: 'Ready' });
});

test('a voice session\'s Activity line is kept in Activity, never a loading pill ("gpt-realtime-2.1 18:21")', () => {
  let pills: StripPill[] = [];
  const loads = new LoadPills(() => {}, (p) => { pills = p; });
  const voice: ActivityEvent = { type: 'activity', id: 'voice-0123456789abcdef', kind: 'model', text: 'The voice provider acknowledged the session',
    at: T / 1000, state: 'running', ref: { model: 'gpt-realtime-2.1' }, details: { ids: { voiceSession: 'voice-0123456789abcdef', model: 'gpt-realtime-2.1' } } };
  expect(isVoiceSession(voice)).toBe(true);
  loads.apply(voice);
  expect(pills).toEqual([]);
  loads.apply({ ...voice, id: 'model:micro-sam:1', details: undefined, ref: { model: 'micro-sam' } });
  expect(pills.map((p) => p.name)).toEqual(['micro-sam']);
});
