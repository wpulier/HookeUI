import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountScientificAdmission, type ScientificAdmission } from './research-scientific-admission';
import { TestElement, fire, syntheticDOM } from './workspace-windows.test-support';
afterEach(() => vi.unstubAllGlobals());
const ready: ScientificAdmission = { ready: true, reviewSha256: 'displayed-policy-proof', policies: [{ id: 'cells', title: 'Segment selected region', capability: 'cells', generation: 'g1', environment: { runner: 'python-env', token: 'NEVER_SHOW' },
  selection: { id: 'input', revision: 3, digest: 'digest' }, inputTitle: 'Saved microscope image', component: 'array.image', region: [[0, 64], [0, 64]], params: { diameter: 30 }, maximumMicros: 500000, seconds: 120 }], grants: [], jobs: [] };
const response = (value: ScientificAdmission) => ({ ok: true, json: async () => ({ ok: true, ...value }) }) as Response;
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const button = (root: TestElement, label: string) => root.querySelectorAll('button').find(item => item.textContent === label)!;
function setup(fetcher: typeof fetch, current = () => true) {
  vi.stubGlobal('document', syntheticDOM().document); const root = new TestElement('article'), openResult = vi.fn().mockResolvedValue(undefined);
  const dispose = mountScientificAdmission(root as unknown as HTMLElement, { id: 'study', session: 'p', current, openResult }, fetcher);
  return { root, dispose, openResult };
}
const granted: ScientificAdmission = { ...ready, grants: [{ request: 'grant', status: 'ready', policies: ['cells'], maximumMicros: 500000, expiresAt: 100, claudeReady: false }] };
describe('scientific tool admission and original jobs (synthetic HTTP/DOM)', () => {
  it('reads without granting, displays reviewed pinned settings, and retries only the same policy admission', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(ready)).mockRejectedValueOnce(new Error('Unknown admission reply')).mockResolvedValue(response(granted));
    const { root, dispose } = setup(fetcher); await settle();
    expect(fetcher.mock.calls).toHaveLength(1); expect(fetcher.mock.calls[0][1].method).toBeUndefined();
    expect(root.textContent).toContain('Axis 1: 0–64'); expect(root.textContent).toContain('diameter'); expect(root.textContent).not.toContain('NEVER_SHOW');
    expect(button(root, 'Allow selected tools').disabled).toBe(true);
    const check = root.querySelector('input')!; Object.assign(check, { checked: true }); fire(check, 'input');
    fire(button(root, 'Allow selected tools'), 'click'); await settle();
    expect(fetcher.mock.calls[1][0]).toBe('/api/research-efforts/study/scientific/admission/admit');
    const admission = JSON.parse(fetcher.mock.calls[1][1].body); expect(admission).toEqual({ session: 'p', request: expect.any(String), policies: ['cells'], reviewSha256: 'displayed-policy-proof' });
    fire(button(root, 'Allow selected tools'), 'click'); await settle();
    expect(fetcher.mock.calls[2][1].body).toBe(fetcher.mock.calls[1][1].body);
    expect(root.textContent).toContain('Claude tool access is not yet verified'); dispose();
  });
  it('checks and stops only the original job, then retrieves and opens retained native results explicitly', async () => {
    const job = { job: 'hooke-original', status: 'uncertain', operation: 'cells', reservedMicros: 500000, completionVerified: false as const };
    const fetcher = vi.fn().mockResolvedValue(response({ ...granted, jobs: [job] }));
    const { root, openResult, dispose } = setup(fetcher); await settle();
    expect(root.textContent).toContain('Needs checking'); expect(openResult).not.toHaveBeenCalled();
    fire(button(root, 'Check original job'), 'click'); await settle(); expect(fetcher.mock.calls[1][0]).toContain('/jobs/hooke-original/status');
    fetcher.mockResolvedValue(response({ ...granted, jobs: [{ ...job, status: 'stop_requested' }] }));
    fire(button(root, 'Stop original job'), 'click'); await settle(); expect(fetcher.mock.calls[2][0]).toContain('/jobs/hooke-original/stop'); expect(root.textContent).toContain('Stop requested');
    fetcher.mockResolvedValue(response({ ...granted, jobs: [{ ...job, status: 'succeeded' }] }));
    fire(button(root, 'Check original job'), 'click'); await settle();
    const retained = { id: 'native-result', revision: 2, digest: 'result-hash' };
    fetcher.mockResolvedValue(response({ ...granted, jobs: [{ ...job, status: 'succeeded', retained }] }));
    fire(button(root, 'Retrieve native result'), 'click'); await settle(); expect(fetcher.mock.calls[4][0]).toContain('/jobs/hooke-original/retrieve'); expect(openResult).not.toHaveBeenCalled();
    fire(button(root, 'Open retained result'), 'click'); await settle(); expect(openResult).toHaveBeenCalledWith(retained); dispose();
  });
  it('ignores a late reply after its originating Project closes', async () => {
    let current = true, resolve!: (value: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
    const { root, dispose } = setup(fetcher, () => current); current = false; resolve(response(ready)); await settle();
    expect(root.textContent).not.toContain('Segment selected region'); expect(button(root, 'Allow selected tools').disabled).toBe(true); dispose();
  });
  it('requires a fresh selection after a changed policy proof instead of retrying unseen settings', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(ready)).mockRejectedValueOnce(new Error('Policy changed'))
      .mockResolvedValue(response({ ...ready, reviewSha256: 'new-proof', policies: [{ ...ready.policies[0], params: { diameter: 60 } }] }));
    const { root, dispose } = setup(fetcher); await settle();
    const check=root.querySelector('input')!;Object.assign(check,{checked:true});fire(check,'input');
    fire(button(root,'Allow selected tools'),'click');await settle();
    fire(button(root,'Check again'),'click');await settle();
    expect(button(root,'Allow selected tools').disabled).toBe(true);expect(root.textContent).toContain('60');
    const changed=root.querySelector('input')!;Object.assign(changed,{checked:true});fire(changed,'input');
    fire(button(root,'Allow selected tools'),'click');await settle();
    const first=JSON.parse(fetcher.mock.calls[1][1].body),second=JSON.parse(fetcher.mock.calls[3][1].body);
    expect(second.reviewSha256).toBe('new-proof');expect(second.request).not.toBe(first.request);dispose();
  });

  it('connects Claude tools explicitly with one request across an ambiguous reply and never claims live access', async () => {
    const available = { ...granted, claudeSetupReady: true };
    const connected = { ...available, grants: [{ ...granted.grants[0], claudeReady: true, claudeConnectionStatus: 'connected', claudeAccessVerified: false as const }] };
    const fetcher = vi.fn().mockResolvedValueOnce(response(available)).mockRejectedValueOnce(new Error('Reply lost')).mockResolvedValue(response(connected));
    const { root, dispose } = setup(fetcher); await settle();
    expect(fetcher).toHaveBeenCalledTimes(1);
    fire(button(root, 'Connect Claude tools'), 'click'); await settle();
    expect(fetcher.mock.calls[1][0]).toContain('/connect-claude');
    expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual({ session: 'p', request: expect.any(String) });
    fire(button(root, 'Connect Claude tools'), 'click'); await settle();
    expect(fetcher.mock.calls[2][1].body).toBe(fetcher.mock.calls[1][1].body);
    expect(root.textContent).toContain('Live access is not yet verified');
    expect(root.querySelectorAll('button').some(item => item.textContent === 'Connect Claude tools')).toBe(false); dispose();
  });
  it('keeps uncertain setup read-only and resumes only a confirmed checkpoint using its retained request', async () => {
    const available = { ...granted, claudeSetupReady: true };
    const fetcher = vi.fn().mockResolvedValue(response({ ...available, grants: [{ ...granted.grants[0], claudeConnectionStatus: 'uncertain-credential', claudeConnectionRequest: 'original-connection' }] }));
    const { root, dispose } = setup(fetcher); await settle();
    expect(fetcher).toHaveBeenCalledTimes(1); expect(root.textContent).toContain('Reconcile the original provider attempt');
    expect(root.querySelectorAll('button').some(item => item.textContent === 'Connect Claude tools')).toBe(false);
    fetcher.mockResolvedValue(response({ ...available, grants: [{ ...granted.grants[0], claudeConnectionStatus: 'vault-created', claudeConnectionRequest: 'original-connection' }] }));
    fire(button(root, 'Check again'), 'click'); await settle();
    fire(button(root, 'Continue Claude tool connection'), 'click'); await settle();
    expect(JSON.parse(fetcher.mock.calls[2][1].body)).toEqual({ session: 'p', request: 'original-connection' }); dispose();
  });

});
