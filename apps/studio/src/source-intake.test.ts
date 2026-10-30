import { expect, test, vi } from 'vitest';
import { SourceIntake } from './source-intake';

test('a later live choice wins over a pending copied source', async () => {
  let finish!: (value: {ok: boolean}) => void;
  const retain = vi.fn(() => new Promise<{ok: boolean}>(resolve => { finish = resolve; }));
  const intake = new SourceIntake(() => 'project-a', retain);
  const first = intake.prepare('a1', 'project-a', false);
  expect(await intake.prepare('live', 'project-a', false)).toBe(true);
  finish({ok: true});
  expect(await first).toBe(false);
  expect(retain.mock.calls).toEqual([['a1', 'project-a']]);
});

test('copy completion cannot navigate a different Project', async () => {
  let current = 'project-a';
  const intake = new SourceIntake(() => current, async () => {
    current = 'project-b'; return {ok: true};
  });
  await expect(intake.prepare('a1', 'project-a', false)).rejects.toThrow('kept in its Project');
});

test('saved references never resolve or copy an original again', async () => {
  const retain = vi.fn(async () => { throw new Error('Original deleted'); });
  const intake = new SourceIntake(() => 'project-a', retain);
  expect(await intake.prepare('a1', 'project-a', true)).toBe(true);
  expect(retain).not.toHaveBeenCalled();
});

test('leaving and reopening the same Project does not revive pending navigation', async () => {
  let opening = 1;
  const intake = new SourceIntake(() => 'project-a', async () => {
    opening += 2; return {ok: true};
  }, () => opening);
  await expect(intake.prepare('a1', 'project-a', false)).rejects.toThrow('Choose it again');
});

test('copy refusal preserves the source error and prevents navigation', async () => {
  const intake = new SourceIntake(() => 'project-a', async () => ({ok: false, error: 'Not enough space'}));
  await expect(intake.prepare('a1', 'project-a', false)).rejects.toThrow('Not enough space');
});
