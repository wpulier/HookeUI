import { describe, expect, it } from 'vitest';
import { reconcileBackgroundRun, capturedRun, panelWidth, resultGroup, stageKey, visibleData, visibleResults, type StudyResource, type StudySnapshot } from './workspace-study-model';

const resource = (id: string, kind: string, extra: Partial<StudyResource> = {}): StudyResource => ({ id, kind, title: id, summary: '', state: 'kept', actions: [], ...extra });
const snapshot: StudySnapshot = { connected: true, ready: true, session: 'project-a', title: 'Original project', projects: [], sources: [], source: null,
  onStage: 'a1', selection: { id: 'o1', label: 'Original outline' }, frame: { id: 1, boot: 'boot-a', w: 512, h: 512 }, held: true };

describe('Workspace study ownership', () => {
  it('keeps the dispatched project, input and selection when browsing changes', () => {
    const current = structuredClone(snapshot);
    const run = capturedRun('run-1', current);
    current.session = 'project-b'; current.onStage = 'a2'; current.selection!.label = 'Another outline'; current.frame!.id = 2;
    expect(run.input.session).toBe('project-a');
    expect(run.input.onStage).toBe('a1');
    expect(run.input.selection?.label).toBe('Original outline');
    expect(run.input.frame?.id).toBe(1);
  });
  it('finds retained numeric answers that Made would hide without presenting raw assets as answers', () => {
    const population = resource('e1', 'population', { product: true });
    const measurement = resource('r1', 'result', { product: false, madeBy: { tool: 'measure' } });
    const asset = resource('a1', 'asset', { product: false });
    const scale = resource('f1', 'calibration', { product: false });
    const rows = [population, measurement, asset, scale];
    expect(visibleResults(rows, 'answers', null).map(item => item.id)).toEqual(['r1']);
    expect(visibleResults(rows, 'all', null).map(item => item.id)).toEqual(['e1', 'r1', 'f1']);
    expect(resultGroup(scale)).toBe('records');
  });
  it('discovers generic retained capability tables as artifacts even without a product flag', () => {
    const table = resource('r2', 'result', {table: true, product: false});
    expect(resultGroup(table)).toBe('artifacts');
    expect(visibleData([table], 'artifacts', null)).toEqual([table]);
  });
  it('filters by canonical source identity without title matching', () => {
    const a = resource('e1', 'population', { source: { sourceImage: { asset: 'a1' } }, at: 1 });
    const b = resource('e2', 'population', { source: { sourceImage: { asset: 'a2' } }, at: 2 });
    expect(visibleResults([a, b], 'all', 'a1').map(item => item.id)).toEqual(['e1']);
    expect(visibleResults([a, b], 'artifacts', null).map(item => item.id)).toEqual(['e2', 'e1']);
  });
  it('keeps source-producing result entries in Inputs rather than duplicate answer rows', () => {
    const picture = resource('r1', 'result', { product: false, madeBy: { tool: 'snapshot' } });
    expect(visibleResults([picture], 'all', null)).toEqual([]);
  });
  it('bounds persisted and keyboard-adjusted panel widths', () => {
    expect(panelWidth(NaN)).toBe(340);
    expect(panelWidth(20)).toBe(280);
    expect(panelWidth(900)).toBe(520);
  });
});


describe('Session DATA classification', () => {
  it('includes produced captures, datasets and retained answers while excluding shared daily statistics and source imports', () => {
    const items = [resource('a1','asset'),resource('v1','volume'),resource('u1','outcome'),resource('snapshot-1','snapshot'),resource('dataset-1','dataset'),resource('r1','result',{madeBy:{tool:'snapshot'}}),resource('e1','population')];
    expect(visibleData(items,'all',null).map(item=>item.id)).toEqual(['snapshot-1','dataset-1','r1','e1']);
    expect(visibleData(items,'artifacts',null).map(item=>item.id)).toEqual(['snapshot-1','dataset-1','e1']);
    expect(visibleData(items,'answers',null).map(item=>item.id)).toEqual(['r1']);
  });
  it('keeps canonical source filtering and newest-first order for produced data', () => {
    const items = [resource('e1','population',{source:{sourceImage:{asset:'a1'}},at:1}),resource('e2','population',{source:{sourceImage:{asset:'a2'}},at:3}),resource('r1','result',{source:{sourceImage:{asset:'a1'}},at:2})];
    expect(visibleData(items,'all','a1').map(item=>item.id)).toEqual(['r1','e1']);
  });
});

it.each([['done', 'completed'], ['failed', 'failed'], ['cancelled', 'stopped'], ['interrupted', 'failed']])('settles an exact background job as %s', (state, status) => {
  const run = capturedRun('run-1', snapshot); run.status = 'queued';
  run.backgroundJob = {id: 'j1', session: snapshot.session};
  const row = resource('j1', 'job', {day: snapshot.session, state, summary: state});
  expect(reconcileBackgroundRun(run, [row], 'other-project')).toBe(false);
  expect(reconcileBackgroundRun(run, [{...row, id: 'j2'}], snapshot.session)).toBe(false);
  expect(reconcileBackgroundRun(run, [row], snapshot.session)).toBe(true);
  expect(run.status).toBe(status);
  expect(reconcileBackgroundRun(run, [{...row, state:'running'}], snapshot.session)).toBe(false);
});

describe('Inputs drawer stage key', () => {
  it('treats nothing on stage as one value, so updates without a stage change do not rebuild the drawer', () => {
    expect(stageKey(null)).toBe(stageKey(undefined));
    expect(stageKey(null)).toBe('');
    expect(stageKey('a1')).toBe('a1');
    expect(stageKey('a1')).not.toBe(stageKey(null));
  });
});
