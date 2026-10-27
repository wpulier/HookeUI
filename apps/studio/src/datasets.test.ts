import { describe, expect, it } from 'vitest';

import { clockText, durationText, sessionReviewLines, elapsedText, factRows, factValue, figures, lightLabel, lightState, lightTitle, ribbon, ribbonNote, rigSummary, savedText, sizeText, sourceText, spanText, whenText } from './datasets';
import type { DatasetCheck, DatasetRow, DatasetState } from './protocol';

const at = (h: number, m: number, s: number, day = 6) => new Date(2026, 9, day, h, m, s).getTime() / 1000;

const allSaved: DatasetCheck = {
  state: 'ok', id: 'ds-20261006-012923', problems: [], at: at(1, 29, 25), last: null,
  items: (['video', 'times', 'frames', 'records', 'disk'] as const).map((name) => ({ name, ok: true, problem: '' })),
};

const on: DatasetState = {
  type: 'dataset', on: true, id: 'ds-20261006-012923', name: '', startedAt: at(1, 29, 23),
  video: { state: 'recording', parts: 1, error: '' }, bytes: 0, dropped: 0, resumedAfter: null, diskLow: false,
  diskFree: null, error: '', check: allSaved,
};

const off: DatasetState = { ...on, on: false, id: null, startedAt: null, check: { ...allSaved, state: 'off', id: null, items: [] } };

function row(extra: Partial<DatasetRow> = {}): DatasetRow {
  return {
    id: 'ds-20261006-012923', name: '', status: 'complete', startedAt: null, startedAtEpoch: at(1, 29, 23),
    stoppedAt: null, stoppedAtEpoch: at(2, 3, 57), endedBy: 'user', reconstructed: false, runs: 1, gaps: [],
    parts: [{ n: 1, videoKept: 'linked', sidecarKept: 'linked', joined: false, bytes: 306_000_000 }],
    counts: { events: 1234, frames: 68321 }, rig: {}, pictures: 412, files: {}, bytes: 0, path: '/Users/w/MicroscopeMind/datasets/x',
    ...extra,
  };
}

describe('times are always read one way', () => {
  it('says clock times with seconds, so an edge is never mistaken for a length', () => {
    expect(clockText(at(1, 43, 52))).toBe('1:43:52 am');
    expect(clockText(at(13, 5, 9))).toBe('1:05:09 pm');
    expect(clockText(at(0, 0, 1))).toBe('12:00:01 am');
  });

  it('says lengths in words', () => {
    expect(durationText(0.4)).toBe('0.4 s');
    expect(durationText(12)).toBe('12 s');
    expect(durationText(13 * 60 + 19)).toBe('13 min 19 s');
    expect(durationText(9 * 3600 + 30)).toBe('9 h 0 min');
  });

  it('gives a dataset its window from start to stop', () => {
    expect(spanText(row(), 0)).toBe('Oct 6, 1:29:23 am to 2:03:57 am (34 min 34 s)');
    expect(spanText(row({ status: 'collecting', stoppedAtEpoch: null }), at(1, 30, 23))).toBe('Oct 6, 1:29:23 am to now (1 min 0 s)');
    expect(spanText(row({ stoppedAtEpoch: at(0, 10, 0, 7) }), 0)).toBe('Oct 6, 1:29:23 am to Oct 7, 12:10:00 am (22 h 40 min)');
  });
});

describe('the Data light', () => {
  it('is a light, not a switch: off until Record starts, then checking, then blue once all is seen saved', () => {
    expect(lightState(null, false)).toBe('off');
    expect(lightState(off, false)).toBe('off');
    expect(lightLabel('off')).toBe('Data');
    expect(lightTitle(off, false)).toContain('This light turns blue once it has checked');
    const checking = { ...on, check: { ...allSaved, state: 'checking' as const } };
    expect(lightState(checking, true)).toBe('checking');
    expect(lightLabel('checking')).toBe('Checking');
    expect(lightState(on, true)).toBe('ok');
    expect(lightLabel('ok')).toBe('Data');
    expect(lightTitle({ ...on, bytes: 12_400_000 }, true)).toContain('are all growing (12 MB so far)');
  });

  it('is a problem, with the reason, when something is not being saved; the recording goes on', () => {
    const problem = 'No pictures from the camera in the last 3 s, so the camera video, its frame times and the frame facts are not growing. Records are still saved.';
    const stalled = { ...on, check: { ...allSaved, state: 'problem' as const, problems: [problem] } };
    expect(lightState(stalled, true)).toBe('problem');
    expect(lightTitle(stalled, true)).toContain(problem);
  });

  it('is a problem when this page records but no dataset could be started', () => {
    expect(lightState(off, true)).toBe('problem');
    expect(lightTitle(off, true, 'the app did not answer')).toBe(
      'Recording, but no dataset is being collected: the app did not answer. The studio recording goes on.');
  });

  it('follows the server after a reload, when the page no longer records itself', () => {
    expect(lightState(on, false)).toBe('ok');
    expect(lightTitle({ ...on, resumedAfter: 9 * 3600 }, false)).toContain('off for 9 h 0 min');
  });

  it('after Stop says Saving, then what was saved, or why it was not saved whole', () => {
    const last = { id: 'ds-20261006-012923', ok: true, problems: [], notes: [], atEpoch: at(2, 4, 3),
      counts: { events: 1234, frames: 68321, pictures: 12 }, videoParts: 1 };
    const saving = { ...off, check: { ...off.check, state: 'saving' as const } };
    expect(lightLabel(lightState(saving, false))).toBe('Saving');
    const saved = { ...off, check: { ...off.check, state: 'saved' as const, last } };
    expect(lightLabel(lightState(saved, false))).toBe('Saved');
    expect(savedText(last)).toBe('Dataset saved whole: 1 camera video, 68,321 frame facts, 1,234 records, 12 pictures.');
    const broken = { ...last, ok: false, problems: ['No camera video was recorded: no camera.'] };
    expect(savedText(broken)).toBe('The dataset was not saved whole: No camera video was recorded: no camera.');
    const failed = { ...off, check: { ...off.check, state: 'problem' as const, problems: broken.problems, last: broken } };
    expect(lightTitle(failed, false)).toContain('The last dataset was not saved whole.');
  });

  it('counts Record\'s time from the server\'s start, so a reload keeps it', () => {
    expect(elapsedText(at(1, 41, 57) - at(1, 29, 23))).toBe('12:34');
    expect(elapsedText(3723)).toBe('1:02:03');
  });
});

describe('what a row shows', () => {
  it('gives the window, and its length apart', () => {
    expect(whenText(row())).toBe('Oct 6, 1:29:23 am to 2:03:57 am');
  });

  it('counts what it holds as figures', () => {
    const shown = figures(row({ research: 3, counts: { events: 1815, frames: 0 }, pictures: 219 }));
    expect(shown).toEqual([
      { value: '292 MB', what: 'video' },
      { value: '1,815', what: 'records' },
      { value: '219', what: 'pictures' },
      { value: '3', what: 'researchers' },
    ]);
    expect(figures(row({ parts: [] }))[0]).toEqual({ value: 'No', what: 'video' });
  });

  it('draws the window with the gap the app was off', () => {
    const shares = ribbon(row({ startedAtEpoch: 0, stoppedAtEpoch: 100, runSpans: [[0, 40], [60, 100]] }), 0);
    expect(shares.map((s) => [s.kind, s.left, Number(s.width.toFixed(6))])).toEqual([['run', 0, 0.4], ['gap', 0.4, 0.2], ['run', 0.6, 0.4]]);
    const live = ribbon(row({ status: 'collecting', startedAtEpoch: 0, stoppedAtEpoch: null, runSpans: [[0, null]] }), 50);
    expect(live).toEqual([{ kind: 'run', left: 0, width: 1 }]);
  });

  it('says when its times are estimates, and how long the app was off', () => {
    expect(ribbonNote(row({ gaps: [{ from: '', to: '', seconds: 0.4 }],
      parts: [{ n: 1, videoKept: 'linked', sidecarKept: null, hasSidecar: false, joined: null, bytes: 1 }] })))
      .toBe('the app was off for 0.4 s; frame times are estimates');
    expect(ribbonNote(row())).toBe('');
  });

  it('gives each microscope fact as a person reads it, with where it came from', () => {
    const { rows, unknown } = factRows({ fieldNumber: { value: 18, source: 'default' }, eyepiece: { value: null, source: 'unknown' },
      objective: { value: 40, source: 'user', at: at(1, 43, 0) }, umPerPx: { value: 0.6923, source: 'estimate' } });
    expect(rows).toEqual([
      { name: 'Field number', value: '18 mm', source: 'assumed' },
      { name: 'Objective', value: '40x', source: 'you, Oct 6' },
      { name: 'Scale', value: '0.69 µm a pixel', source: 'estimated' },
    ]);
    expect(unknown).toEqual(['eyepiece']);
    expect(factValue('cameraRequested', { size: '1280x720', fps: 30 })).toBe('1280 × 720 at 30 a second');
    expect(sourceText('bridge-config')).toBe('asked of the camera');
  });

  it('folds your microscope to one line', () => {
    expect(rigSummary({})).toBe('Not described yet');
    expect(rigSummary({ microscope: { value: 'AmScope M150C', source: 'user' }, eyepiece: { value: 25, source: 'user' },
      fieldNumber: { value: 10, source: 'user' } })).toBe('AmScope M150C, 25x eyepiece, field number 10 mm');
  });

  it('sizes', () => {
    expect(sizeText(900)).toBe('900 B');
    expect(sizeText(1_600_000_000)).toBe('1.5 GB');
  });
});


describe('saving the session: the review before Save', () => {
  const review = {
    scope: 'conversation', scopeText: 'this conversation', from: 1_000_000, to: 1_001_200,
    holds: { records: 40, turns: 6, interactions: { click: 3, tool: 9 }, pictures: 5, snapshots: 1,
      researchers: [{ rid: 'r1', name: 'Researcher 1', status: 'done', kept: true }, { rid: 'r2', name: 'Researcher 2', status: 'running', kept: true }],
      cameraSeconds: 180, collected: [], microscope: ['eyepiece'], evidence: 2, evidenceMembers: 315 },
    missing: ['Per-frame measurements are kept only while Record is on, and Record was not on in this window.'],
    pending: [{ rid: 'r2', name: 'Researcher 2', status: 'running' }],
    destination: '/Users/someone/MicroscopeMind/datasets/ds-20261007-180000',
    staysHere: 'Saved on this Mac only. Nothing is uploaded or analysed, and the session carries on.',
  };

  it('says what it holds in plain words, apart from the researchers still working', () => {
    const lines = sessionReviewLines(review);
    expect(lines.window).toMatch(/^This conversation: .* to .* \(20 min/);
    expect(lines.holds).toContain('6 exchanges in the conversation, 40 records in all');
    expect(lines.holds).toContain('3 clicks with what was outlined and named');
    expect(lines.holds).toContain('the work of 1 researcher');
    expect(lines.holds).toContain('2 saved sets of outlines (315 outlines), with their masks and measurements');
    expect(lines.holds.some((l) => l.includes('of camera video'))).toBe(true);
    expect(lines.pending).toEqual(['Researcher 2 is still working: its work is saved as it is now.']);
  });

  it('says where it goes and that nothing leaves the Mac', () => {
    const lines = sessionReviewLines(review);
    expect(lines.where.startsWith('~/MicroscopeMind/datasets/ds-20261007-180000.')).toBe(true);
    expect(lines.where).toContain('Nothing is uploaded');
    expect(lines.missing).toEqual(review.missing);
  });
});
