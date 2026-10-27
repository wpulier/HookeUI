// The view follows the session (session-view.ts): where the stage lands when a session opens. A new session opens its
// home; a returning one lands on its last source, held (a recording where it was left, a picture as it is), or live;
// a start keeps what plays. Nothing is read from or kept in the browser (#252's remembered map is gone).
import { describe, expect, test } from 'vitest';
import { LAND_GRACE_MS, LAND_WAIT_MS, StageGate, cameraHomeWanted, emptyCameraHomeWanted, ConnectionStart, lookState, onStart, startMayLand, viewOnOpen, type StageFacts } from './session-view';

const facts = (onStage: string | null, sources: StageFacts['sources'] = []): StageFacts => ({ onStage, sources });
const sperm = { id: 'a7', kind: 'recording', position: 192.4 };
const smear = { id: 'a9', kind: 'still', position: null };

test('accepting a Project image keeps the input chooser closed while camera facts catch up', () => {
  const before = { cameraOff: true, asset: false, footage: false, busy: false, holding: false, onStage: null };
  expect(emptyCameraHomeWanted(before)).toBe(true);
  // The successful source broadcast arrives before the next /feed response.
  expect(emptyCameraHomeWanted({ ...before, onStage: 'a9' })).toBe(false);
  expect(emptyCameraHomeWanted({ ...before, onStage: 'a9', asset: true, footage: true })).toBe(false);
  // Removing the input or explicitly returning to a disabled camera restores the chooser.
  expect(emptyCameraHomeWanted({ ...before, onStage: 'live' })).toBe(true);
  expect(emptyCameraHomeWanted(before)).toBe(true);
});

test('an opening, active source or camera never becomes an automatic empty input choice', () => {
  const empty = { cameraOff: true, asset: false, footage: false, busy: false, holding: false, onStage: null };
  for (const changed of [{ cameraOff: false }, { asset: true }, { footage: true }, { busy: true }, { holding: true }])
    expect(emptyCameraHomeWanted({ ...empty, ...changed })).toBe(false);
});

describe('viewOnOpen: where the stage lands when a session opens', () => {
  test('a new session opens its home, whatever its sources say', () => {
    expect(viewOnOpen('new', null)).toBe('home');
    expect(viewOnOpen('new', facts('a7', [sperm]))).toBe('home');
  });

  test('a start (the app starting, or a studio connecting) keeps what plays; so does an older server that says no why', () => {
    expect(viewOnOpen('start', facts('a7', [sperm]))).toBe('keep');
    expect(viewOnOpen(undefined, facts('a7', [sperm]))).toBe('keep');
    expect(viewOnOpen(null, null)).toBe('keep');
  });

  test('going back: a recording held where it was left, a picture held as it is', () => {
    expect(viewOnOpen('switch', facts('a7', [smear, sperm]))).toEqual({ asset: 'a7', at: 192.4, paused: true });
    expect(viewOnOpen('switch', facts('a9', [smear, sperm]))).toEqual({ asset: 'a9', at: 0, paused: true });
    // A picture has no place in it, whatever was kept.
    expect(viewOnOpen('switch', facts('a9', [{ ...smear, position: 40 }]))).toEqual({ asset: 'a9', at: 0, paused: true });
  });

  test('a recording never left anywhere, or left at a place that is not a time, starts held at its beginning', () => {
    for (const position of [null, 0, -3, NaN, Infinity]) {
      expect(viewOnOpen('switch', facts('a7', [{ ...sperm, position }]))).toEqual({ asset: 'a7', at: 0, paused: true });
    }
  });

  test('the live camera, or nothing kept, lands live; so does a source taken out of the session since', () => {
    expect(viewOnOpen('switch', facts('live', [sperm]))).toBe('live');
    expect(viewOnOpen('switch', facts(null, [sperm]))).toBe('live');
    expect(viewOnOpen('switch', facts('a3', [sperm]))).toBe('live'); // removed, or its file gone
    expect(viewOnOpen('switch', null)).toBe('live'); // its sources could not be read
  });
});

describe('the stage between two sessions: nothing of the last one is drawn', () => {
  test('from the opening until its source lands, no frame is drawn and the stage says what is opening', () => {
    const gate = new StageGate();
    expect(gate.accept({ ba: 100 }, 0)).toBe(true);
    gate.hold(1000, 'Opening “Leaf epidermis”…');
    expect(gate.holding(1000)).toBe(true);
    expect(gate.words(1500)).toBe('Opening “Leaf epidermis”…');
    expect(gate.accept({ ba: 101 }, 1500)).toBe(false); // the last session's footage, still playing
    // Landed at 102.0 on the bridge's clock: a frame it took before is the last source's, for a moment.
    gate.settle(2000, 102);
    expect(gate.words(2000)).toBe('');
    expect(gate.accept({ ba: 101.9 }, 2100)).toBe(false);
    expect(gate.accept({ ba: 102.1 }, 2150)).toBe(true);
    expect(gate.accept({ ba: 101.9 }, 2200)).toBe(true); // once the new source is drawn, every frame is
  });

  test('a landing that never comes lets frames through after a while; the grace after a landing is short', () => {
    const gate = new StageGate();
    gate.hold(0, 'Opening “Blood smear”…');
    expect(gate.accept({ ba: 1 }, LAND_WAIT_MS - 1)).toBe(false);
    expect(gate.accept({ ba: 1 }, LAND_WAIT_MS)).toBe(true);
    expect(gate.words(LAND_WAIT_MS)).toBe('');
    gate.settle(10_000, 500); // a bridge on another clock: its frames are never left out for long
    expect(gate.accept({ ba: 400 }, 10_000 + LAND_GRACE_MS - 1)).toBe(false);
    expect(gate.accept({ ba: 400 }, 10_000 + LAND_GRACE_MS)).toBe(true);
    gate.settle(20_000, 500);
    expect(gate.accept({}, 20_001)).toBe(true); // a frame that does not say when it was taken is drawn
  });

  test("a new session's home covers the stage: it says nothing, and once released frames are drawn at once", () => {
    const gate = new StageGate();
    gate.hold(0);
    expect(gate.holding(10)).toBe(true);
    expect(gate.words(10)).toBe('');
    gate.release();
    expect(gate.accept({ ba: 1 }, 20)).toBe(true);
  });
});

describe('cameraHomeWanted: the empty home never sits behind a saved result', () => {
  test('a saved result shown in its own window keeps the home closed; tucked away or closed, it opens again', () => {
    expect(cameraHomeWanted([])).toBe(true);
    expect(cameraHomeWanted([{ visible: true }])).toBe(false);
    expect(cameraHomeWanted([{ visible: false }])).toBe(true);
    expect(cameraHomeWanted([{ visible: false }, { visible: true }])).toBe(false);
  });
});

describe('viewOnOpen on a start: returning work after a full restart', () => {
  test('nothing plays and the session was on one of its assets: it comes back held', () => {
    expect(viewOnOpen('start', facts('a7', [sperm]), true)).toEqual({ asset: 'a7', at: 192.4, paused: true });
    expect(viewOnOpen('start', facts('a9', [smear]), true)).toEqual({ asset: 'a9', at: 0, paused: true });
  });
  test('something plays (the live camera with a picture, an asset, footage), or it is not known: what plays is kept', () => {
    expect(viewOnOpen('start', facts('a7', [sperm]), false)).toBe('keep');
    expect(viewOnOpen('start', facts('a7', [sperm]))).toBe('keep');
  });
  test('a session last on the live camera, on nothing, or on an asset no longer its own: kept, never the camera taken over', () => {
    expect(viewOnOpen('start', facts('live', [sperm]), true)).toBe('keep');
    expect(viewOnOpen('start', facts(null, [sperm]), true)).toBe('keep');
    expect(viewOnOpen('start', facts('a3', [sperm]), true)).toBe('keep');
  });
});

describe('onStart: a start lands returning work only when the camera is known to have no picture', () => {
  const camera = { source: 'camera', busy: false };
  test('No camera restores the retained picture or paused recording after restart', () => {
    expect(onStart(facts('a9', [smear]), camera, { state: 'no_camera' })).toEqual({ asset: 'a9', at: 0, paused: true });
    expect(onStart(facts('a7', [sperm]), camera, { state: 'no_camera' })).toEqual({ asset: 'a7', at: 192.4, paused: true });
    expect(onStart(facts(null), camera, { state: 'no_camera' })).toBe('keep');
    expect(onStart(facts('live'), camera, { state: 'no_camera' })).toBe('keep');
    expect(onStart(facts('a9', [smear]), camera, { state: 'no_camera' }, true)).toBe('keep');
    expect(onStart(facts('a9', [smear]), { source: 'footage', busy: false }, { state: 'no_camera' })).toBe('keep');
    expect(onStart(facts('a9', [smear]), { source: 'camera', busy: true }, { state: 'no_camera' })).toBe('keep');
  });
  test('the feed or the health not read yet (or the health failed): it waits, to look again once both are known', () => {
    expect(onStart(facts('a7', [sperm]), null, { state: 'waiting' })).toBe('wait');
    expect(onStart(facts('a7', [sperm]), camera, null)).toBe('wait');
  });
  test('the camera up with no picture: the session\'s asset comes back held', () => {
    for (const state of ['waiting', 'stale', 'bridge_not_live']) {
      expect(onStart(facts('a7', [sperm]), camera, { state })).toEqual({ asset: 'a7', at: 192.4, paused: true });
    }
  });
  test('a live picture, a bridge offline, a camera switching or footage playing: kept, the camera never taken over', () => {
    expect(onStart(facts('a7', [sperm]), camera, { state: 'live' })).toBe('keep');
    expect(onStart(facts('a7', [sperm]), camera, { state: 'bridge_offline' })).toBe('keep');
    expect(onStart(facts('a7', [sperm]), { source: 'camera', busy: true }, { state: 'waiting' })).toBe('keep');
    expect(onStart(facts('a7', [sperm]), { source: 'footage', busy: false }, { state: 'waiting' })).toBe('keep');
  });
});

describe('onStart guards a fresh frame over a stale health read', () => {
  test('a frame drawn now keeps what plays, even when the last health said there was no picture or was not read', () => {
    const camera = { source: 'camera', busy: false };
    expect(onStart(facts('a7', [sperm]), camera, { state: 'waiting' }, true)).toBe('keep');
    expect(onStart(facts('a7', [sperm]), null, null, true)).toBe('keep');
    expect(onStart(facts('a7', [sperm]), camera, { state: 'waiting' }, false)).toEqual({ asset: 'a7', at: 192.4, paused: true });
  });
});

describe('a start never overrides the user: their choice during the load, another studio, another opening', () => {
  test('a choice made while the sources loaded keeps it; other openings land as before', () => {
    expect(startMayLand('start', 3, 3)).toBe(true);
    expect(startMayLand('start', 3, 4)).toBe(false);
    expect(startMayLand('switch', 3, 4)).toBe(true);
  });
  test('the second look: looked at only in view and in time, dropped after a choice, another opening or session', () => {
    const look = { token: 1, sid: 's', until: 100, choice: 2, stage: 'a7' };
    const now = { token: 1, sid: 's', choice: 2, visible: true, at: 50, stage: 'a7' as string | null };
    expect(lookState(look, now)).toBe('look');
    expect(lookState(look, { ...now, visible: false })).toBe('hold');
    expect(lookState(look, { ...now, choice: 3 })).toBe('drop');
    expect(lookState(look, { ...now, token: 2 })).toBe('drop');
    expect(lookState(look, { ...now, sid: 't' })).toBe('drop');
    expect(lookState(look, { ...now, at: 101 })).toBe('drop');
  });
});

describe('the session\'s stage is owned by whoever set it last: another studio\'s choice is never undone by a start', () => {
  test('its stage changed while it loaded (another studio went live): the start keeps it', () => {
    expect(startMayLand('start', 3, 3, 'a7', 'a7')).toBe(true);
    expect(startMayLand('start', 3, 3, 'a7', 'live')).toBe(false);
    expect(startMayLand('switch', 3, 3, 'a7', 'live')).toBe(true);
  });
  test('its stage changed while the second look waited: dropped', () => {
    const look = { token: 1, sid: 's', until: 100, choice: 2, stage: 'a7' };
    const now = { token: 1, sid: 's', choice: 2, visible: true, at: 50 };
    expect(lookState(look, { ...now, stage: 'a7' })).toBe('look');
    expect(lookState(look, { ...now, stage: 'live' })).toBe('drop');
    expect(lookState(look, { ...now, stage: null })).toBe('drop');
  });
});

describe('a connection\'s start: the first session message after hello lands, once, unless an opening came first', () => {
  test('an automatic empty-camera home permits restoration; an explicit empty input does not', () => {
    const start = new ConnectionStart();
    start.hello();
    expect(start.session('empty-camera')).toBe(true);
    expect(onStart(facts('a9', [smear]), { source: 'camera', busy: false }, { state: 'no_camera' }))
      .toEqual({ asset: 'a9', at: 0, paused: true });
    start.hello();
    expect(start.session('chosen')).toBe(false);
    expect(start.session()).toBe(false);
    start.hello();
    expect(start.session()).toBe(true);
  });
  test('choosing an empty input after start admission invalidates both delayed landing paths', () => {
    const look = { token: 1, sid: 'project', until: 15000, choice: 0, stage: 'a9' };
    expect(startMayLand('start', 0, 1, 'a9', 'a9')).toBe(false);
    expect(lookState(look, { token: 1, sid: 'project', choice: 1, visible: true, at: 1000, stage: 'a9' })).toBe('drop');
  });
  test('a studio loading or reconnecting after a restart: its first session message is a start, the next ones are not', () => {
    const start = new ConnectionStart();
    expect(start.session()).toBe(false); // before any connection
    start.hello();
    expect(start.session()).toBe(true);
    expect(start.session()).toBe(false); // the session's updates are not starts
    start.hello(); // reconnected (the app restarted)
    expect(start.session()).toBe(true);
  });
  test('a session-cleared first (new, switched): that opening lands it, never twice', () => {
    const start = new ConnectionStart();
    start.hello();
    start.cleared();
    expect(start.session()).toBe(false);
  });
});
