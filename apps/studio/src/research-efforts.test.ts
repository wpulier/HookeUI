import { describe, expect, it, vi } from 'vitest';
import { StudyRefused, studyInputDescription, controlStudy, createStudy, dollars, listStudies, studyName, studyNeeds, studySpending, taskCounts, type Effort, studyState, taskState, pendingProviderTask, studyInputs, studyReadiness, startStudy, studyEvidence, reviewStudyTask, retainStudyFinding, recoverStudyTask } from './research-efforts';

// As effort_api.summary returns one (services/orchestrator/research/efforts.py).
const study: Effort = { id: 'pabc', session: 'p', revision: 3, digest: 'd', objective: 'Which training data transfers best?\nmore', criteria: 'Held-out error',
  status: 'active', capMicrousd: 1_000_000, spentMicrousd: 120_000, heldMicrousd: 250_000, availableMicrousd: 630_000, inputs: [],
  tasks: { a: { status: 'running', reservedMicrousd: 250_000, chargedMicrousd: null }, b: { status: 'completed', reservedMicrousd: 200_000, chargedMicrousd: 120_000 },
    c: { status: 'unknown', reservedMicrousd: 0, chargedMicrousd: null, problem: 'launch reply lost' } },
  findings: [{ request: 'f1', statement: 'Adding the Replogle screens helped', tasks: ['b'] }] };
const ok = (body: unknown, status = 200) => vi.fn(async () => ({ ok: status < 400, status, json: async () => body })) as unknown as typeof fetch;

describe('studies as the studio says them', () => {
  it('says money from micro-dollars: spent of the allowance, held, and left; a tiny spend as one', () => {
    expect(studySpending(study)).toBe('$0.12 spent of $1.00 · $0.25 held · $0.63 left');
    expect(dollars(3_000)).toBe('under $0.01');expect(dollars(0)).toBe('$0.00');
  });
  it('counts experiments by state, what needs you first, and names a study by its objective’s first line', () => {
    expect(taskCounts(study)).toBe('1 needs checking · 1 running · 1 accepted');
    expect(taskCounts({ tasks: {} })).toBe('No experiments yet');
    expect(studyNeeds(study)).toBe('1 experiment needs checking; its original execution must be reconciled and its spending remains held.');
    expect(studyNeeds({ tasks: { b: study.tasks.b } })).toBe(null);
    expect(studyName(study)).toBe('Which training data transfers best?');
    expect(studyName({ ...study, title: 'Saved title' })).toBe('Saved title');
  });
});

describe('studies through the server', () => {
  it('lists this Project’s studies, and says none exist in a build without them', async () => {
    expect(await listStudies('p', ok({ ok: true, efforts: [study] }))).toEqual([study]);
    expect(await listStudies('p', ok({}, 404))).toBe(null);
  });
  it('starts and controls a study as research-effort requests, with no inputs and no receipts', async () => {
    const fetcher = ok({ ok: true, effort: study });
    await createStudy({ session: 'p', request: 'r1', title: 't', objective: 'o', criteria: 'c', budgetUsd: 1 }, fetcher);
    const [url, init] = (fetcher as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[0];
    expect(url).toBe('/api/research-efforts');
    expect((init.headers as Record<string, string>)['X-MicroscopeMind']).toBe('research-effort');
    expect(JSON.parse(init.body as string)).toEqual({ session: 'p', request: 'r1', title: 't', objective: 'o', criteria: 'c', budgetUsd: 1, inputs: [] });
    await controlStudy('pabc', 'p', 'pause', fetcher);
    expect((fetcher as unknown as { mock: { calls: [string, RequestInit][] } }).mock.calls[1][0]).toBe('/api/research-efforts/pabc/control');
  });
  it('a refusal is the server’s own words and next step', async () => {
    await expect(createStudy({ session: 'p', request: 'r', title: 't', objective: 'o', criteria: 'c', budgetUsd: 1 },
      ok({ ok: false, says: 'This Project is local only.', next: 'use an explicitly cloud-enabled Project' }, 403)))
      .rejects.toEqual(new StudyRefused('This Project is local only.', 'use an explicitly cloud-enabled Project'));
  });
});


describe('explicit research admission and exact evidence', () => {
  it('reads inputs/readiness/evidence without dispatch and keeps exact input refs on draft save', async () => {
    const ref = { id: 'source', revision: 4, digest: 'exact' }, fetcher = ok({ ok: true, inputs: [ref], providers: [], effort: study });
    await studyInputs('p', fetcher); await studyReadiness('p', fetcher, 'saved-study'); await studyEvidence('e', 'p', 't', fetcher);
    const calls = vi.mocked(fetcher).mock.calls;
    expect(String(calls[1][0])).toBe('/api/research-readiness?session=p&effort=saved-study');
    expect(calls.slice(0, 3).every(([, init]) => !init?.method || init.method === 'GET')).toBe(true);
    await createStudy({ session: 'p', request: 'r', title: 'Saved title', objective: 'o', criteria: 'c', budgetUsd: 1, inputs: [ref] }, fetcher);
    expect(JSON.parse(String(calls[3][1]?.body)).inputs).toEqual([ref]);
    expect(calls.map(([url]) => url).some(url => String(url).endsWith('/start'))).toBe(false);
  });
  it('submits explicit provider admission and the exact displayed review hash, without client cost claims', async () => {
    const fetcher = ok({ ok: true, effort: study });
    await startStudy('e', 'p', { request: 'same', provider: 'claude-managed-google', message: 'Compare', budgetCents: 50, maximumUsd: .75 }, fetcher);
    await reviewStudyTask('e', 'p', 't', 'displayed-hash', 'completed', 'Checked outputs', fetcher);
    await retainStudyFinding('e', 'p', 'finding', 'This result is limited.', ['t'], fetcher);
    const calls = vi.mocked(fetcher).mock.calls;
    expect(JSON.parse(String(calls[0][1]?.body))).toEqual({ session: 'p', request: 'same', provider: 'claude-managed-google', message: 'Compare', budgetCents: 50, maximumUsd: .75 });
    expect(JSON.parse(String(calls[1][1]?.body))).toEqual({ session: 'p', evidenceSha256: 'displayed-hash', outcome: 'completed', review: 'Checked outputs' });
    expect(JSON.parse(String(calls[2][1]?.body)).tasks).toEqual(['t']);
  });
  it('never turns a saved draft, pending Stop or sealed unaccepted evidence into completed work', () => {
    expect(studyState({ ...study, tasks: {} })).toBe('Not started');
    expect(studyState({ ...study, status: 'stopped' })).toBe('Stop requested — awaiting confirmation');
    expect(taskState({ ...study.tasks.a, observation: { sealedEvidence: true, providerStatus: 'terminated', listCostMicrousd: 1000 } })).toBe('Ready for review');
    expect(taskState({ ...study.tasks.a, status: 'cancel_requested' })).toBe('Stop requested');
    expect(pendingProviderTask(study.tasks.a)).toBe(true);
    expect(pendingProviderTask({ ...study.tasks.a, specification: { provider: 'scientific-gcp-grant' } })).toBe(false);
  });
});


it('describes identically named saved inputs using accepted layers and native dimensions without guessing a producer', () => {
  const input = { id: 'private-id', revision: 1, digest: 'digest', title: 'human_mitosis_crop', kind: 'scene', managedFileEligible: false,
    context: { savedAt: 0, layers: [{ name: 'Corrected cells', kind: 'labels' }], arrays: [{ axes: ['y', 'x'], shape: [512, 512], dtype: '<u2', semantic: 'instance-labels' }] } };
  const description = studyInputDescription(input);
  expect(description).toContain('Scientific scene'); expect(description).toContain('Corrected cells (labels)');
  expect(description).toContain('Y 512 × X 512'); expect(description).toContain('instance labels');
  expect(description).not.toContain('private-id'); expect(description).not.toContain('Cellpose');
  expect(studyInputDescription({ ...input, kind: 'source-directory', context: undefined })).toBe('Imported folder');
});


it('reconnects only the explicit original session using the existing Study mutation guard', async () => {
  const fetcher = ok({ok:true,effort:study});
  await recoverStudyTask('e /1','owner','task /2','sesn_original',fetcher);
  const [url,init] = vi.mocked(fetcher).mock.calls[0];
  expect(url).toBe('/api/research-efforts/e%20%2F1/tasks/task%20%2F2/recover');
  expect(init?.method).toBe('POST');
  expect(JSON.parse(String(init?.body))).toEqual({session:'owner',providerSession:'sesn_original'});
});
