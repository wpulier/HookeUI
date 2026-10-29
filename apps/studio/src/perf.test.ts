// The performance meter (perf.ts): each part now against its ideal, the dot the worst of them.
import { describe, expect, test } from 'vitest';
import { atLeast, atMost, atMostHeld, inUseRows, modelLines, overall, readings, verdict, viewRows, type PerfInput, type Reading } from './perf';

const calm: PerfInput = {
  live: true, fps: 30, screenMs: 36, analyseMs: 15, gpu: { ready: true, device: 'mps', error: '' },
  machine: { cores: 14, load1: 6, memFreePct: 61, swapTotalMb: 2048, swapUsedMb: 200, diskFreeGb: 300, clickP95Ms: 180 },
};
// The sim run of 2026-10-06: load 200-300 on 14 cores, swap nearly full, the disk 95% full.
const pressed: PerfInput = {
  ...calm, fps: 16, screenMs: 131,
  // That run swapped hard (load 237 on 14 cores, swap 8.8 of 9.2 GB): pages coming back in every second.
  machine: { cores: 14, load1: 237, memFreePct: 45, swapTotalMb: 9216, swapUsedMb: 8846, swapInsPerS: 900, diskFreeGb: 44, clickP95Ms: 910 },
};
const level = (input: PerfInput, key: string) => readings(input).find((r) => r.key === key)?.level;

describe('the performance meter', () => {
  test('higher or lower is better, unknown without a number', () => {
    expect(atLeast(30, { good: 27, fair: 20 })).toBe('good');
    expect(atLeast(22, { good: 27, fair: 20 })).toBe('fair');
    expect(atLeast(16, { good: 27, fair: 20 })).toBe('poor');
    expect(atMost(300, { good: 300, fair: 600 })).toBe('good');
    expect(atMost(910, { good: 300, fair: 600 })).toBe('poor');
    expect(atMost(null, { good: 1, fair: 2 })).toBe('unknown');
  });

  test('a calm machine and a smooth feed are green', () => {
    expect(overall(readings(calm))).toBe('good');
    expect(verdict(readings(calm))).toBe('Running as it should.');
  });

  test('the overloaded sim run is red, and says the CPU is the reason', () => {
    const rs = readings(pressed);
    expect(overall(rs)).toBe('poor');
    expect([level(pressed, 'fps'), level(pressed, 'cpu'), level(pressed, 'swap'), level(pressed, 'clicks')]).toEqual(['poor', 'poor', 'poor', 'poor']);
    expect(level(pressed, 'disk')).toBe('fair');
    expect(verdict(rs, [{ name: 'Xcode', cpu: 297, memMb: 8444, ours: false }, { name: 'MicroscopeMind server :8770', cpu: 106, memMb: 200, ours: true }]))
      .toBe('Other programs want more CPU than the computer has: Xcode.');
  });

  test('our own load is named when it is the cause, not another program', () => {
    // Live 2026-10-11 02:38: micro-sam loading on the analysis worker at load 142; the meter blamed WindowServer.
    const rs = readings(pressed);
    const windowServer = [{ name: 'WindowServer', cpu: 80, memMb: 900, ours: false }];
    const loading = [
      { pid: 11, role: 'analysis worker', name: 'MicroscopeMind: analysis worker (loading micro-sam)', cpu: 190, memMb: 2400, loading: 'micro-sam' },
      { pid: 12, role: 'outline worker', name: 'MicroscopeMind: outline worker', cpu: 30, memMb: 900 },
    ];
    expect(verdict(rs, windowServer, loading)).toBe('micro-sam is loading: the live view is slowed.');
    const two = [...loading, { pid: 13, role: 'Molmo worker', name: 'x', cpu: 40, memMb: 9000, loading: 'Molmo 2 4B' }];
    expect(verdict(rs, windowServer, two)).toBe('micro-sam and Molmo 2 4B are loading: the live view is slowed.');
    // Nothing loading, but our workers use more of the CPU than the others: said as ours.
    const busy = loading.map(({ loading: _, ...w }) => w);
    expect(verdict(rs, windowServer, busy)).toBe("This app's own work wants more CPU than the computer has: analysis worker and outline worker.");
    // Another program using more: as before.
    expect(verdict(rs, [{ name: 'Xcode', cpu: 600, memMb: 8444, ours: false }], busy))
      .toBe('Other programs want more CPU than the computer has: Xcode.');
    // Many small ones of one name count together, and are named as such, never blamed on this app (2026-10-11 gate:
    // fourteen at ~45% each were counted as none, and "this app's own work" was said).
    const many = Array.from({ length: 14 }, () => ({ name: 'Python', cpu: 45, memMb: 15, ours: false }));
    expect(verdict(rs, many, busy)).toBe('Other programs want more CPU than the computer has: Python ×14.');
    // A calm machine says it runs well, loading or not; the outline model starting is said as that.
    expect(verdict(readings(calm), [], loading)).toBe('Running as it should.');
    const starting = { ...calm, gpu: { ready: false, device: '', error: '' } };
    expect(verdict(readings(starting), [], [{ ...loading[1], loading: 'EdgeTAM' }])).toBe('The outline model is not ready: clicks cannot be outlined yet.');
  });

  test('with no camera the feed is not rated, the computer still is', () => {
    const idle = { ...calm, live: false, fps: 0 };
    expect([level(idle, 'fps'), level(idle, 'screen')]).toEqual(['unknown', 'unknown']);
    expect(overall(readings(idle))).toBe('good');
    expect(overall(readings({ ...idle, gpu: null, machine: null }))).toBe('unknown');
  });

  test('no clicks yet is not a problem', () => {
    const fresh = { ...calm, machine: { ...calm.machine, clickP95Ms: undefined } };
    expect(level(fresh, 'clicks')).toBe('unknown');
    expect(overall(readings(fresh))).toBe('good');
  });

  test("the view's numbers, once on the status line, are the panel's first rows", () => {
    expect(viewRows({ delayMs: 33.4, slide: 'still', outlines: 2 })).toEqual([
      ['Delay', '33 ms'], ['Slide', 'still'], ['Outlines', '2'],
    ]);
    // a frame stamped a little ahead of this clock is no delay, not a negative one; before the
    // first frame the slide is not known
    expect(viewRows({ delayMs: -12, slide: '', outlines: 0 })).toEqual([
      ['Delay', '0 ms'], ['Slide', '–'], ['Outlines', '0'],
    ]);
  });

  test('the frame rate the status line showed is the table row, not said twice', () => {
    expect(readings(calm).find((r) => r.key === 'fps')?.now).toBe('30 fps');
    expect(viewRows({ delayMs: 0, slide: 'moving', outlines: 1 }).map(([name]) => name)).not.toContain('Frame rate');
  });
});

describe('Models, the last 10 minutes (which defaults really ran; live 2026-10-11)', () => {
  test('the defaults ran, said plainly', () => {
    expect(modelLines({ fallbacks: [], providers: { Google: { ok: 12 } } })).toEqual(['The default models ran.']);
  });
  test('each exception with how often and why, and a provider whose calls did not all return', () => {
    const lines = modelLines({
      fallbacks: [{ role: 'quick decisions', default: 'Gemini 3.8 Flash', used: 'the full look', times: 3, why: 'no answer within 15 s', agoS: 40 }],
      providers: { Google: { ok: 20, 'cut-off': 4 }, OpenAI: { ok: 2, 'rate-limit': 1 } },
    });
    expect(lines[0]).toBe('Quick decisions: the full look instead of Gemini 3.8 Flash, 3× (no answer within 15 s)');
    expect(lines).toContain("Google: 4 cut off by the app's deadline of 24 calls");
    expect(lines).toContain('OpenAI: 1 rate limited or out of quota of 3 calls');
  });
  test('nothing known, nothing said', () => {
    expect(modelLines(null)).toEqual([]);
  });
});

describe('Doing each job now (models_in_use.py; live 2026-10-28: "Gemini and GPT")', () => {
  test('each job its model, where it runs and its typical time, and where to change it on hover', () => {
    const rows = inUseRows({ fallbacks: [], providers: {}, inUse: [
      { role: 'brain', title: 'Thinking (the brain)', model: 'Gemini 3.8 Flash', runs: 'cloud', where: 'cloud', setting: 'gemini-3.8-flash',
        how: 'Answer with actions', speed: { p50Ms: 1940, calls: 6 }, speedWords: 'its calls here typically took 1.9 s (last 6 in 10 minutes)',
        change: 'Models › Seeing (the brain answers with it)' },
      { role: 'cells', title: 'Cells', model: 'Cellpose-SAM', runs: 'mac', where: 'this Mac', setting: 'cellpose-sam', speed: null,
        speedWords: 'no calls in the last 10 minutes', change: 'Models › Cells' },
    ] });
    expect(rows.map((r) => [r.job, r.doing])).toEqual([['Thinking (the brain)', 'Gemini 3.8 Flash · cloud · 1.9 s'], ['Cells', 'Cellpose-SAM · this Mac']]);
    expect(rows[1].title).toBe('no calls in the last 10 minutes. Change: Models › Cells');
    expect(inUseRows(null)).toEqual([]);
  });
});

test('swap that is full but idle is not a problem; swapping now is (red all of 2026-10-11 for a full, idle swap)', () => {
  const idle: PerfInput = { ...calm, machine: { cores: 14, load1: 4, memFreePct: 40, swapTotalMb: 14300, swapUsedMb: 13400, swapInsPerS: 0, diskFreeGb: 300 } };
  expect(level(idle, 'swap')).toBe('good');
  expect(readings(idle).find((r) => r.key === 'swap')?.now).toBe('0 pages/s back in over 30 s · 13.1 GB swapped out');
  const busy: PerfInput = { ...idle, machine: { ...idle.machine, swapInsPerS: 450 } };
  expect(level(busy, 'swap')).toBe('poor');
});

test('the swap level holds while the reading hovers at a line, as machine.py rates it (flipped every 5 s, 2026-10-11)', () => {
  const lines = { good: 20, fair: 200, hysteresis: 0.5 };
  expect(atMostHeld(18, lines, 'fair')).toBe('fair'); // back to good only at 10 or less
  expect(atMostHeld(10, lines, 'fair')).toBe('good');
  expect(atMostHeld(150, lines, 'poor')).toBe('poor'); // back to fair only at 100 or less
  expect(atMostHeld(99, lines, 'poor')).toBe('fair');
  expect(atMostHeld(21, lines, 'good')).toBe('fair'); // worse is said at once
  expect(atMostHeld(18, lines, undefined)).toBe('good');
  expect(atMostHeld(18, { good: 20, fair: 200 }, 'fair')).toBe('good'); // a line without one holds nothing
  // The meter keeps its last readings: a swap reading that falls to just under the line stays "fair".
  const at = (swapInsPerS: number, was: Reading[] = []) =>
    readings({ ...calm, machine: { ...calm.machine, swapInsPerS } }, was);
  const fair = at(23);
  expect(fair.find((r) => r.key === 'swap')?.level).toBe('fair');
  expect(at(16.9, fair).find((r) => r.key === 'swap')?.level).toBe('fair');
  expect(at(9.9, at(16.9, fair)).find((r) => r.key === 'swap')?.level).toBe('good');
});
