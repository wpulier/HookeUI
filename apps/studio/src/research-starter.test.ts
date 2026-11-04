import {afterEach, expect, test, vi} from 'vitest';
import {ResearchStarterDashboard} from './research-starter';
import {syntheticDOM} from './workspace-windows.test-support';

afterEach(() => { vi.unstubAllGlobals(); });

test('a late source cannot displace a newer Project and empty Projects inherit no brief', async () => {
  vi.stubGlobal('document', syntheticDOM().document);
  let finish!: (r: Response) => void;
  vi.stubGlobal('fetch', vi.fn().mockReturnValueOnce(new Promise<Response>(resolve => { finish = resolve; }))
    .mockResolvedValueOnce(Response.json({session: 'two', starter: {
      title: 'My research', question: 'What does this show?', source: 'https://example.org/brief',
      original: '<script>raw</script>', context: 'My interpretation', status: 'read',
    }})).mockResolvedValueOnce(Response.json({session: 'blank', starter: null})));
  const host = document.createElement('div');
  const dash = new ResearchStarterDashboard(host, () => {});
  dash.update('one', 0);
  dash.update('two', 0);
  await vi.waitFor(() => expect(host.textContent).toContain('My interpretation'));
  expect(host.querySelector('script')).toBeNull();
  expect(host.textContent).toContain('No inputs added');
  dash.update('two', 2);
  expect(host.textContent).toContain('2 items in this Project');
  finish(Response.json({session: 'one', starter: {title: 'Wrong Project'}}));
  await Promise.resolve();
  expect(host.textContent).not.toContain('Wrong Project');
  dash.update('blank', 0);
  expect(host.textContent).not.toContain('My research');
  expect(host.querySelector('section')?.hidden).toBe(true);
});
