// Local renderer-state evidence: application receipts stay activity when no
// model answer exists. These are synthetic server events, not model or DOM proof.
import { expect, test } from 'vitest';
import { ChatModel } from './chatmodel';
import type { ChatAction, ServerMessage } from './protocol';

const receipt = (fields: Partial<ChatAction> = {}): ChatAction => ({
  type: 'chat-action', turnId: 41, tool: 'select', ok: false,
  summary: 'Original outline o404 is absent', ms: 0, thread: 't1', ...fields,
});

function current(): ChatModel {
  const model = new ChatModel(() => false);
  model.apply({ type: 'day', id: '2026-10-09', label: '', active: 't1',
    threads: [], subjects: [], prefs: {} });
  model.apply({ type: 'chat', role: 'user', text: 'Select that outline.', thread: 't1' });
  return model;
}

test('an explicit standalone receipt persists without fabricated assistant prose', () => {
  const model = current();
  const notice = { ...receipt(), standalone: true };
  const changes = model.apply(notice);
  expect(changes.some(change => change.kind === 'hint')).toBe(false);
  expect(model.lines).toHaveLength(2);
  expect(model.lines[1]).toMatchObject({ role: 'activity', text: '', turnId: 41 });
  expect(model.lines[1].actions).toEqual([receiptFacts(notice)]);
  expect(model.lines.filter(line => line.role === 'assistant')).toEqual([]);
});

test('a pending standalone receipt updates with its actual late result', () => {
  const model = current();
  model.apply({ ...receipt({ ok: null, summary: 'Reading original retained note' }), standalone: true });
  const finished = receipt({ ok: true, summary: 'Original retained note read', ms: 1800 });
  model.apply(finished);
  expect(model.lines).toHaveLength(2);
  expect(model.lines[1].actions).toEqual([receiptFacts(finished)]);
  expect(model.lines[1].role).toBe('activity');
  expect(model.lines[1].text).toBe('');
});

test('standalone activity survives replay and remains in its original conversation', () => {
  const notice = { ...receipt(), standalone: true };
  const replay: ServerMessage = { type: 'thread', day: '2026-10-09', thread: 't1',
    title: '', summary: '', lines: [
      { type: 'chat', role: 'user', text: 'Select that outline.', thread: 't1' }, notice,
    ] };
  const model = current();
  expect(model.apply(replay).some(change => change.kind === 'hint')).toBe(false);
  expect(model.lines).toHaveLength(2);
  expect(model.lines[1]).toMatchObject({ role: 'activity', text: '' });
  expect(model.lines[1].actions).toEqual([receiptFacts(notice)]);
  expect(model.apply({ ...notice, thread: 't2' })).toEqual([]);
  expect(model.lines).toHaveLength(2);
});

test('an actual late model answer can accompany activity without replacing its receipt', () => {
  const model = current();
  const original = { ...receipt({ ok: true, summary: 'Original retained note read' }), standalone: true };
  model.apply(original);
  const answer = 'The retained note is background evidence; it does not identify the current specimen.';
  model.apply({ ...receipt({ ok: true, summary: original.summary }), answer });
  expect(model.lines.filter(line => line.role === 'assistant').map(line => line.text)).toEqual([answer]);
  expect(model.lines.find(line => line.role === 'activity')?.actions).toEqual([receiptFacts(original)]);
});

function receiptFacts(message: ChatAction) {
  return { tool: message.tool, ok: message.ok, summary: message.summary, ms: message.ms };
}

test('two standalone pending occurrences never overwrite each other', () => {
  const model = current();
  model.apply({ ...receipt({ tool: 'zoom', ok: null, summary: 'Opening original A' }), standalone: true });
  model.apply({ ...receipt({ tool: 'zoom', ok: null, summary: 'Opening original B' }), standalone: true });
  expect(model.lines.filter(line => line.role === 'activity').flatMap(line => line.actions))
    .toEqual([receiptFacts(receipt({ tool: 'zoom', ok: null, summary: 'Opening original A' })),
      receiptFacts(receipt({ tool: 'zoom', ok: null, summary: 'Opening original B' }))]);
});

test('qualified late receipts update their own original occurrence in either completion order', () => {
  const model = current();
  const first = { ...receipt({ tool: 'zoom', ok: null, summary: 'Opening original A' }),
    standalone: true, requestId: 'r-original', actionId: 'a-original' };
  const second = { ...receipt({ tool: 'zoom', ok: null, summary: 'Opening original B' }),
    standalone: true, requestId: 'r-original', actionId: 'b-original' };
  model.apply(first);
  model.apply(second);
  const doneB = { ...second, standalone: false, ok: true, summary: 'Original B displayed', ms: 2000 };
  const doneA = { ...first, standalone: false, ok: false, summary: 'Original A could not be displayed', ms: 3000 };
  model.apply(doneB);
  model.apply(doneA);
  const activities = model.lines.filter(line => line.role === 'activity');
  expect(activities).toHaveLength(2);
  expect(activities[0].actions).toEqual([{ ...receiptFacts(doneA), requestId: first.requestId, actionId: first.actionId }]);
  expect(activities[1].actions).toEqual([{ ...receiptFacts(doneB), requestId: second.requestId, actionId: second.actionId }]);
  // A foreign receipt never changes the original occurrence; a failure goes to Activity as its own line,
  // never a toast of its raw summary (2026-10-11).
  const [foreign] = model.apply({ ...doneA, requestId: 'r-foreign', actionId: first.actionId });
  expect(foreign.kind).toBe('add');
  expect(model.lines.filter(line => line.role === 'activity')).toHaveLength(3);
  expect(activities[0].actions[0].summary).toBe(doneA.summary);
  expect(activities[0].actions[0].requestId).toBe(first.requestId);
});

test('an operation-absent answer status persists without an invented tool or assistant message', () => {
  const model = current();
  const status: { type: 'chat-action'; turnId: number; thread: string; standalone: boolean;
    answerStatus: 'not-published'; calls: number; model: string } = {
      type: 'chat-action', turnId: 41, thread: 't1', standalone: true,
      answerStatus: 'not-published', calls: 3, model: 'configured-model',
    };
  model.apply(status);
  expect(model.lines).toHaveLength(2);
  expect(model.lines[1]).toMatchObject({ role: 'activity', text: '', turnId: 41,
    answerStatus: 'not-published', calls: 3, model: 'configured-model', actions: [] });
  expect(model.lines.filter(line => line.role === 'assistant')).toEqual([]);
  const replay: ServerMessage = { type: 'thread', day: '2026-10-09', thread: 't1',
    title: '', summary: '', lines: [status] };
  model.apply(replay);
  expect(model.lines).toHaveLength(1);
  expect(model.lines[0]).toMatchObject({ role: 'activity', answerStatus: 'not-published', actions: [] });
});

test.each([false, true])('qualified model text stays with its actual occurrence (existing answer: %s)', existing => {
  const original = { ...receipt({ tool: 'zoom', ok: true, summary: 'Original view displayed' }),
    standalone: true, requestId: 'r-original', actionId: 'a-original' };
  const previous = 'The original request is still awaiting interpretation.';
  const answer = 'The original view was displayed; the specimen identity remains uncertain.';
  const foreign = 'A different request has completed.';
  const initial: ServerMessage[] = [original];
  if (existing) initial.push({ type: 'chat', role: 'assistant', text: previous,
    turnId: 41, thread: 't1' });
  for (const replaying of [false, true]) {
    const model = current();
    const apply = (events: ServerMessage[]) => replaying
      ? model.apply({ type: 'thread', day: '2026-10-09', thread: 't1', title: '', summary: '', lines: events })
      : events.flatMap(event => model.apply(event));
    apply(initial);
    expect(model.lines.filter(line => line.role === 'activity').flatMap(line => line.actions))
      .toEqual([{ ...receiptFacts(original), requestId: original.requestId, actionId: original.actionId }]);
    const rejected = [
      { ...original, standalone: false, requestId: 'r-foreign', answer: foreign },
      { ...original, standalone: false, actionId: 'a-foreign', answer: foreign },
    ];
    const changes = apply(replaying ? [...initial, ...rejected] : rejected);
    expect(changes.some(change => change.kind === 'hint' && change.text === foreign)).toBe(false);
    expect(model.lines.filter(line => line.role === 'assistant').map(line => line.text))
      .toEqual(existing ? [previous] : []);
    const matching = { ...original, standalone: false, answer };
    apply(replaying ? [...initial, ...rejected, matching] : [matching]);
    expect(model.lines.filter(line => line.role === 'assistant').map(line => line.text)).toEqual([answer]);
    expect(model.lines.filter(line => line.role === 'activity').flatMap(line => line.actions))
      .toEqual([{ ...receiptFacts(original), requestId: original.requestId, actionId: original.actionId }]);
  }
});

test('local diagnostics survive thread replay with original times and never leak across conversations', () => {
  let now = 100_000;
  const model = new ChatModel(() => false, () => now);
  const replay = (thread: string): ServerMessage => ({ type: 'thread', day: '2026-10-09', thread, title: '', summary: '', lines: [] });
  model.apply(replay('t1'));
  model.system('Request refused', { deduplicate: true });
  now += 100;
  expect(model.system('Request refused', { deduplicate: true })).toEqual([]);
  model.apply(replay('t2'));
  model.system('Request refused', { deduplicate: true });
  expect(model.lines).toHaveLength(1);
  expect(model.lines[0].at).toBe(100.1);
  model.apply(replay('t1'));
  expect(model.lines.map(line => [line.text, line.at])).toEqual([['Request refused', 100]]);
  // A fresh submitted request is a distinct occurrence even when its error repeats.
  model.system('Request refused');
  expect(model.lines).toHaveLength(2);
  model.apply(replay('t1'));
  expect(model.lines).toHaveLength(2);
});

test('late local diagnostic stays with its conversation; background repeats coalesce until reconnect', () => {
  let now = 100_000;
  const model = new ChatModel(() => false, () => now);
  const replay = (thread: string): ServerMessage => ({ type: 'thread', day: 'd1', thread, title: '', summary: '', lines: [] });
  model.apply(replay('t2'));
  model.system('Original request refused', { day: 'd1', thread: 't1' });
  expect(model.lines).toHaveLength(0);
  model.apply(replay('t1'));
  expect(model.lines[0].text).toBe('Original request refused');
  model.system('Reporter unavailable', { deduplicate: true });
  now += 6000;
  expect(model.system('Reporter unavailable', { deduplicate: true })).toEqual([]);
  model.apply({ type: 'hello' } as ServerMessage);
  model.apply(replay('t1'));
  model.system('Reporter unavailable', { deduplicate: true });
  expect(model.lines.filter(line => line.text === 'Reporter unavailable')).toHaveLength(2);
});

test('a spoken request\'s step row takes its late result by its own identity, whichever turn filed it', () => {
  // Voice/chat parity (live 2026-10-11): a spoken request's step is filed under the turn that did it (hands' or the
  // hand-off's); its late result names the request's turn. It updates that row in place, never a second row.
  const model = current();
  const ids = { requestId: 'r-7', actionId: 'a-photo' };
  model.apply({ ...receipt({ turnId: 12, tool: 'reference_photo', ok: null, summary: 'still working', ...ids }), standalone: true });
  const landed = receipt({ turnId: 13, tool: 'reference_photo', ok: true, summary: 'shown on screen', ms: 9000, ...ids });
  model.apply({ ...landed, standalone: true });
  const rows = model.lines.filter(line => line.role === 'activity');
  expect(rows).toHaveLength(1);
  expect(rows[0].actions[0]).toMatchObject({ ok: true, summary: 'shown on screen' });
});
