import { expect, test, vi } from 'vitest';
import { CapabilityTry, type PreparedTry } from './capabilitytry';

function rig(action: PreparedTry['action'] = 'ask') {
  let session = '1', fresh = false;
  const order: string[] = [];
  const reply: PreparedTry = { type: 'result', for: 'capability-try', ok: true, token: 'one-use', session: '1', action, words: 'Inspect this example', label: 'A',
    asset: { id: 'example', title: 'Synthetic example', kind: 'still', seconds: 0, from: 'example', capturedAt: null, captured: '', capturedOn: null,
      microscope: false, optics: {}, specimen: null, specimenBy: null, notes: null, origin: null, file: 'particles.png' } };
  const ports = { session: () => session, empty: () => true, ready: () => fresh,
    prepare: vi.fn(async () => reply),
    open: vi.fn(async () => { order.push('open'); fresh = true; return true; }),
    wait: vi.fn(async () => undefined), now: () => 0,
    show: vi.fn(() => order.push('show')), guide: vi.fn(),
    ask: vi.fn(() => { order.push('ask'); return true; }), voice: vi.fn(async () => undefined), configure: vi.fn() };
  return { trial: new CapabilityTry(ports), ports, order, reply, move: () => { session = '2'; } };
}
test('empty-stage Try opens its public input before sending an owned exact-choice request', async () => {
  const r = rig(); await r.trial.start('vision', 'a');
  expect(r.ports.prepare).toHaveBeenCalledWith('vision', 'a', true);
  expect(r.order).toEqual(['open', 'show', 'ask']);
  expect(r.ports.ask).toHaveBeenCalledWith('Inspect this example', 'one-use');
});
test('a click trial arms one gesture, and a changed session retires it', async () => {
  const r = rig('click'); await r.trial.start('outline', 'a');
  expect(r.ports.ask).not.toHaveBeenCalled();
  expect(r.trial.takeClick()).toBe('one-use'); expect(r.trial.takeClick()).toBeUndefined();
  await r.trial.start('outline', 'a'); r.move(); expect(r.trial.takeClick()).toBeUndefined();
});
test('source-open refusal and session replacement send no request to an old or new picture', async () => {
  const r = rig(); r.ports.open.mockImplementationOnce(async () => false);
  await expect(r.trial.start('vision', 'a')).rejects.toThrow(/example|open/);
  expect(r.ports.ask).not.toHaveBeenCalled();
  r.ports.open.mockImplementationOnce(async () => { r.move(); return true; });
  await expect(r.trial.start('vision', 'a')).rejects.toThrow(/session|conversation/);
  expect(r.ports.ask).not.toHaveBeenCalled();
});
test('voice Try opens the same example and starts the normal Talk path', async () => {
  const r = rig('voice'); await r.trial.start('voice', 'a');
  expect(r.ports.voice).toHaveBeenCalledWith('one-use', 'a'); expect(r.ports.ask).not.toHaveBeenCalled();
});
test('guided setup with missing input asks for a picture without opening an example or invoking an agent', async () => {
  const r = rig();
  await expect(r.trial.start('vision', 'a', false)).rejects.toThrow('Choose a picture or video');
  expect(r.ports.prepare).not.toHaveBeenCalled();
  expect(r.ports.open).not.toHaveBeenCalled();
  expect(r.ports.ask).not.toHaveBeenCalled();
});

test('local Try opens exact-model controls without sending chat or starting a run', async () => {
  const r = rig('configure'); r.reply.capability = 'group-cells';
  await r.trial.start('embed', 'dinov2-small');
  expect(r.ports.configure).toHaveBeenCalledExactlyOnceWith('group-cells', 'embed', 'dinov2-small');
  expect(r.ports.ask).not.toHaveBeenCalled();
  expect(r.ports.voice).not.toHaveBeenCalled();
});
