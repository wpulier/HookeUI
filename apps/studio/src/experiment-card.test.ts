// An experiment's card (experiment-card.ts; experiments.py): the plan with Run it and Change while it waits for a yes,
// then how it goes with Stop, then Open results; under the request that planned it; Run it sends the plan's version.
import { describe, expect, test, vi } from 'vitest';
import { ChatModel } from './chatmodel';
import { ExperimentCard, experimentGroups, experimentView, type ExperimentWire } from './experiment-card';
import type { ServerMessage, ToolRun } from './protocol';
import { syntheticDOM, fire, type TestElement } from './workspace-windows.test-support';

const DAY = '2026-10-29';
const day: ServerMessage = { type: 'day', id: DAY, label: '', active: 't1', threads: [], subjects: [], prefs: {} };
const user = (text: string): ServerMessage => ({ type: 'chat', role: 'user', text, thread: 't1', day: DAY });
const planned = (requestId: string): ToolRun => ({ tool: 'experiment', ok: true, summary: 'Experiment 1 planned', ms: 30, requestId, actionId: 'a1' });
const answer = (text: string, turn: number, actions: ToolRun[] = []): ServerMessage =>
  ({ type: 'chat', role: 'assistant', text, turnId: turn, thread: 't1', day: DAY, actions });

function wire(fields: Partial<ExperimentWire> = {}): ExperimentWire {
  return { id: 'x1', key: 'p1', n: 1, name: 'Experiment 1', title: 'Cellpose-SAM vs micro-sam (light microscopy)', session: DAY,
    thread: 't1', version: 1, status: 'proposed', requestId: 'RUN/r3', turnId: 2,
    steps: ['Hold 5 frames of this footage, one every 4 s (over about 16 s) and keep them in this Project',
      'Cellpose-SAM segments every cell on each frame (on this Mac, nothing spent)'],
    cost: 'Nothing spent: the models run on this Mac; the audit at most $0.25', input: '5 frames', progress: '', why: '',
    outcome: {}, canRun: true, canStop: false, created: 1, ended: null, ...fields };
}
const shown = () => true;
const scope = { day: DAY, thread: 't1' };

describe('what an experiment card says', () => {
  test('a plan shows its steps and cost, with Run it and Change; nothing else', () => {
    const view = experimentView(wire());
    expect(view).toMatchObject({ cell: 'needs', title: 'Experiment 1 · Cellpose-SAM vs micro-sam (light microscopy)',
      status: 'Planned · runs on your yes', actions: ['run', 'change'] });
    expect(view.steps).toHaveLength(2);
    expect(view.cost).toContain('$0.25');
    // How long it should take, said as an estimate, before what it may cost.
    expect(experimentView(wire({ estimate: 'About 3 min (an estimate from earlier runs here)' })).cost)
      .toBe('About 3 min (an estimate from earlier runs here) · Nothing spent: the models run on this Mac; the audit at most $0.25');
  });
  test('a plan that expired offers nothing; a running one says its progress with Stop; a finished one opens its results', () => {
    expect(experimentView(wire({ canRun: false })).actions).toEqual([]);
    expect(experimentView(wire({ status: 'running', canRun: false, canStop: true, progress: 'Frame 2 of 5: micro-sam cells' })))
      .toMatchObject({ cell: 'working', status: 'Running · Frame 2 of 5: micro-sam cells', steps: [], cost: '', actions: ['stop'] });
    expect(experimentView(wire({ status: 'done', canRun: false, outcome: { document: 'd7' } })))
      .toMatchObject({ cell: 'ready', status: 'Done · its results are in a Tab of their own', actions: ['open'] });
    expect(experimentView(wire({ status: 'failed', canRun: false, why: 'micro-sam failed on frame 1' })))
      .toMatchObject({ cell: 'attention', status: 'Didn’t finish · micro-sam failed on frame 1', actions: [] });
  });
});

describe('where a card goes', () => {
  function model(...messages: ServerMessage[]) {
    const m = new ChatModel(() => false); m.apply(day); for (const message of messages) m.apply(message); return m;
  }
  test('under the answer of the request that planned it, by the request id on its steps', () => {
    const m = model(user('compare cellpose and micro-sam on this footage'), answer('Here is the plan.', 2, [planned('RUN/r3')]),
      user('something else'), answer('Sure.', 3));
    const said = m.lines.find((l) => l.text === 'Here is the plan.')!;
    expect(experimentGroups(m.lines, [wire()], scope, shown, new Map())).toEqual([{ key: 'request:RUN/r3', after: said.key, ids: ['x1'] }]);
  });
  test('a plan whose request is not in the log stays under the line it arrived after; another Project’s is not here', () => {
    const m = model(user('hello'), answer('Hi.', 1));
    const anchors = new Map<string, number>();
    const first = experimentGroups(m.lines, [wire({ requestId: 'RUN/elsewhere' })], scope, shown, anchors);
    m.apply(user('later words'));
    const again = experimentGroups(m.lines, [wire({ requestId: 'RUN/elsewhere' })], scope, shown, anchors);
    expect(again[0].after).toBe(first[0].after);
    expect(experimentGroups(m.lines, [wire({ session: 'another-day' })], scope, shown, new Map())).toEqual([]);
  });
});

describe('its buttons', () => {
  test('Run it sends the plan the card shows, and waits on its reply', async () => {
    const dom = syntheticDOM();
    vi.stubGlobal('document', dom.document);
    try {
      let finish = () => {};
      const act = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
      const card = new ExperimentCard('x1', act);
      card.update(wire({ version: 3 }), true);
      const run = card.root.querySelector('[data-action="run"]') as unknown as TestElement;
      fire(run, 'click');
      expect(act).toHaveBeenCalledWith('run', expect.objectContaining({ id: 'x1', version: 3 }));
      expect((card.root.querySelector('[data-action="run"]') as unknown as HTMLButtonElement).disabled).toBe(true);
      finish(); await Promise.resolve(); await Promise.resolve();
      expect((card.root.querySelector('[data-action="run"]') as unknown as HTMLButtonElement).disabled).toBe(false);
    } finally { vi.unstubAllGlobals(); }
  });
});
