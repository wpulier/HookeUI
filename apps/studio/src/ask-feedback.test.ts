import { expect, test } from 'vitest';
import { AskRequests } from './ask-feedback';
const first = { day: 'd1', thread: 't1' }, second = { day: 'd1', thread: 't2' };
test('rejected Ask exposes actual reason and restores its cleared draft; a duplicate result is ignored', () => {
  const requests = new AskRequests();
  requests.started('a', 'Zoom the eye', first);
  expect(requests.settle('a', false, 'This screen observation has expired', first, '')).toMatchObject({
    status: 'Request failed: This screen observation has expired', draft: 'Zoom the eye', current: true,
  });
  expect(requests.pending(first)).toBe(false);
  expect(requests.settle('a', false, 'same', first, '')).toBeNull();
});
test('late results cannot clear another request or overwrite a new draft/conversation', () => {
  const requests = new AskRequests();
  requests.started('a', 'Original words', first);
  requests.started('b', 'New conversation words', second);
  expect(requests.settle('a', false, 'Refused', second, 'Typing')).toMatchObject({ ...first, current: false, draft: 'Typing' });
  expect(requests.pending(second)).toBe(true);
  expect(requests.settle('unrelated', true, '', second, '')).toBeNull();
  expect(requests.pending(second)).toBe(true);
  expect(requests.settle('b', false, 'Refused', second, 'My newer draft')?.draft).toBe('My newer draft');
});
test('disconnect preserves current draft without implying the server cancelled work; retired results do not revive it', () => {
  const requests = new AskRequests();
  requests.started('a', 'Original words', first);
  expect(requests.retire(first, '')).toBe('Original words');
  expect(requests.pending(first)).toBe(false);
  expect(requests.settle('a', false, 'late', first, 'Editing')).toBeNull();
  requests.started('b', 'Other words', first);
  expect(requests.retire(second, '')).toBe('');
});

test('the server’s keyless reason is said in words a person can act on',async()=>{
 const {askError}=await import('./ask-feedback');
 expect(askError(new Error('the workspace notes planner did not answer; ask again'))).toBe('The Assistant needs its Google Gemini key before it can answer. Add it in Models, then ask again.');
 expect(askError(new Error('something else went wrong'))).toBe('something else went wrong');
});

test('a lost window link is said in words as the whole status; any other leading code is dropped, the server\'s words kept', async () => {
  const { askError } = await import('./ask-feedback');
  const requests = new AskRequests();
  requests.started('a', 'Spawn two researchers', first);
  expect(requests.settle('a', false, 'CLIENT_MISMATCH: this socket has no accepted workspace binding', first, '')).toMatchObject({
    status: 'This window lost its link to the studio. Reload to reconnect.', draft: 'Spawn two researchers' });
  expect(askError('SESSION_BUSY: the session is switching')).toBe('The session is switching');
  requests.started('b', 'Zoom', first);
  expect(requests.settle('b', false, 'WORKSPACE_GONE: that workspace closed', first, '')?.status).toBe('Request failed: That workspace closed');
});
