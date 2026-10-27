import { expect, it, vi } from 'vitest';
import { workspaceProfile } from './workspace-startup';

it('retries a failed startup without losing the eventual profile identity', async () => {
  const request = vi.fn().mockRejectedValueOnce(new Error('Disconnected')).mockResolvedValue({ profile: 'private-profile', review: true });
  let resume!: () => void;
  const retry = vi.fn(() => new Promise<void>(resolve => { resume = resolve; }));
  const result = workspaceProfile(request, retry);
  await vi.waitFor(() => expect(retry).toHaveBeenCalledWith('Disconnected'));
  expect(request).toHaveBeenCalledTimes(1);
  resume();
  expect(await result).toEqual({ profile: 'private-profile', review: true });
});

it('does not use default storage when an old server omits the profile', async () => {
  const request = vi.fn().mockResolvedValueOnce({ ok: false }).mockResolvedValue({ profile: 'updated-profile' });
  const retry = vi.fn(async () => {});
  expect(await workspaceProfile(request, retry)).toEqual({ profile: 'updated-profile', review: false });
  expect(retry).toHaveBeenCalledOnce();
});
