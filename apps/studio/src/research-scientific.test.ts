import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountScientificStaging, type ScientificStaging } from './research-scientific';
import { TestElement, fire, syntheticDOM } from './workspace-windows.test-support';
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });
const prepared: ScientificStaging = { ready: true, project: 'hooke', region: 'us-central1', preparation: null, inputTitles: ['Native specimen'] };
const response = (value: ScientificStaging) => ({ ok: true, json: async () => ({ ok: true, ...value }) }) as Response;
const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };
const button = (root: TestElement, text: string) => root.querySelectorAll('button').find(item => item.textContent === text)!;
function setup(fetcher: typeof fetch, current = () => true) {
  vi.stubGlobal('document', syntheticDOM().document); const root = new TestElement('article');
  const dispose = mountScientificStaging(root as unknown as HTMLElement, { id: 'study', session: 'p', current }, fetcher);
  return { root, dispose };
}

describe('private scientific input preparation (synthetic HTTP/DOM)', () => {
  it('only reads on opening and reuses the exact explicit transfer identity after an uncertain response', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response(prepared)).mockRejectedValueOnce(new Error('Transfer reply lost')).mockResolvedValue(response({ ...prepared, preparation: { status: 'staged', completedInputs: 1, totalInputs: 1 } }));
    const { root, dispose } = setup(fetcher); await settle();
    expect(fetcher.mock.calls).toHaveLength(1); expect(fetcher.mock.calls[0][1].method).toBeUndefined();
    expect(root.textContent).toContain('starts no researcher, model or GPU job');
    fire(button(root, 'Copy to private Google Cloud storage'), 'click'); await settle();
    const first = fetcher.mock.calls[1]; expect(first[0]).toBe('/api/research-efforts/study/scientific/stage');
    expect(JSON.parse(first[1].body)).toMatchObject({ session: 'p', request: expect.any(String) });
    fire(button(root, 'Copy to private Google Cloud storage'), 'click'); await settle();
    expect(fetcher.mock.calls[2][1].body).toBe(first[1].body); expect(root.textContent).toContain('Copied privately');
    expect(button(root, 'Copy to private Google Cloud storage').disabled).toBe(true); dispose();
  });
  it('polls only retained state, exposes Stop pending, and stops observing after the Project changes', async () => {
    vi.useFakeTimers(); let current = true;
    const fetcher = vi.fn().mockResolvedValue(response({ ...prepared, preparation: { status: 'staging', completedInputs: 0, totalInputs: 1 } }));
    const { root, dispose } = setup(fetcher, () => current); await settle();
    await vi.advanceTimersByTimeAsync(4000); expect(fetcher.mock.calls).toHaveLength(2);
    expect(fetcher.mock.calls.every(([, options]) => !options.method)).toBe(true);
    fetcher.mockResolvedValue(response({ ...prepared, preparation: { status: 'stopping', completedInputs: 0, totalInputs: 1 } }));
    fire(button(root, 'Stop copying'), 'click'); await settle();
    expect(fetcher.mock.calls[2][0]).toContain('/stop-staging'); expect(root.textContent).toContain('Stop requested');
    current = false; await vi.advanceTimersByTimeAsync(4000); expect(fetcher.mock.calls).toHaveLength(3); dispose();
  });
  it('rejects a late read after disposal and never enables unapproved staging', async () => {
    let resolve!: (value: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(done => { resolve = done; }));
    const { root, dispose } = setup(fetcher); dispose(); resolve(response(prepared)); await settle(); expect(root.children).toHaveLength(0);
    const blocked = setup(vi.fn().mockResolvedValue(response({ ...prepared, ready: false, reason: 'Private staging approval required.' }))); await settle();
    expect(button(blocked.root, 'Copy to private Google Cloud storage').disabled).toBe(true); expect(blocked.root.textContent).toContain('Private staging approval required.'); blocked.dispose();
  });
  it('rotates the transfer identity only after an acknowledged terminal attempt and an explicit Retry', async () => {
    const fetcher = vi.fn().mockResolvedValueOnce(response({ ...prepared, preparation: { request: 'old-attempt', status: 'interrupted', completedInputs: 0, totalInputs: 1 } }))
      .mockRejectedValueOnce(new Error('New transfer reply lost')).mockImplementation(async (_url, options) => response({ ...prepared, preparation: { request: JSON.parse(options.body).request, status: 'failed', completedInputs: 0, totalInputs: 1 } }));
    const { root, dispose } = setup(fetcher); await settle();
    fire(button(root, 'Try copying again'), 'click'); await settle();
    const first = JSON.parse(fetcher.mock.calls[1][1].body).request; expect(first).not.toBe('old-attempt');
    fire(button(root, 'Try copying again'), 'click'); await settle();
    expect(JSON.parse(fetcher.mock.calls[2][1].body).request).toBe(first);
    fire(button(root, 'Try copying again'), 'click'); await settle();
    expect(JSON.parse(fetcher.mock.calls[3][1].body).request).not.toBe(first); dispose();
  });

});
