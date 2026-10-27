import { describe, expect, it, vi } from 'vitest';
import { readStudyPresentation, restoreStudyItem, sameStudyResource, StudySessionSwitch, studyResourceReference, studyEntryReference } from './workspace-study-restoration';

describe('workspace study restoration', () => {
  it('opens retained images by picture index and refuses same-numbered resources in another session', () => {
    const entry = {day:'a', id:'e13', version:0, pictures:['/retained/picture']};
    expect(studyEntryReference(entry,'a')).toEqual({day:'a',id:'e13',version:0,picture:0});
    expect(() => studyEntryReference(entry,'b')).toThrow('originating project');
    expect(studyEntryReference({...entry,pictures:[]},'a').picture).toBeNull();
  });
  it('keeps exact resource revision and picture identities', () => {
    const ref = { day: '2026-10-15.7', id: 'e4', version: 0, picture: null };
    expect(studyResourceReference(ref)).toEqual(ref);
    expect(studyResourceReference({ day: ref.day, id: ref.id })).toBeNull();
    expect(sameStudyResource(ref, { ...ref, version: 1 })).toBe(false);
    expect(sameStudyResource(ref, { ...ref, day: '2026-10-15.8' })).toBe(false);
  });
  it('opens research findings rather than the first image attached to the report', () => {
    expect(studyEntryReference({ day: 'here', id: 'r1', kind: 'research', version: 3,
      pictures: ['/retained/research/image'] }, 'here')).toEqual({ day: 'here', id: 'r1', version: 3, picture: null });
  });
  it.each([null, 0])('preserves exact research output identities through persistence (picture %s)', picture => {
    const output = { day: 'here', id: 'r1', version: 2, picture, outputId: 'output-original' };
    const saved = { schema: 1, session: 'here', source: null, draft: '', views: [
      { resource: output, title: 'Original research output', zoom: 1, pan: [.5, .5] },
    ] };
    const restored = readStudyPresentation(JSON.parse(JSON.stringify(saved)), 'here')!.views[0].resource;
    expect(restored).toEqual(output);
    expect(sameStudyResource(restored, { ...output, outputId: 'output-replacement' })).toBe(false);
    expect(sameStudyResource(restored, { day: output.day, id: output.id, version: output.version, picture })).toBe(false);
    expect(sameStudyResource(restored, output)).toBe(true);
  });
  it('rejects malformed output identities instead of silently reopening different research', () => {
    const ref = { day: 'here', id: 'r1', version: 2, picture: null };
    for (const outputId of ['', null, 7, 'x'.repeat(257)]) {
      expect(studyResourceReference({ ...ref, outputId })).toBeNull();
    }
  });
  it('rejects another session and unversioned storage', () => {
    expect(readStudyPresentation({ schema: 1, session: 'other', draft: '', views: [] }, 'here')).toBeNull();
    expect(readStudyPresentation({ session: 'here', draft: '', views: [] }, 'here')).toBeNull();
  });
  it('preserves drafts, retained view transforms and recovery reasons without scientific payloads', () => {
    const saved = { schema: 1, session: 'here', source: 'a6', position: 12.5, paused: false, wholeFrame: true,
      conversation: 't2', draft: 'Count these', reading: 124, views: [{ resource: { day: 'here', id: 'e4', version: 0, picture: null },
        title: 'Bright regions', presentation: 'masks', zoom: 2, pan: [.2, .8], position: [100, 120], size: [360, 320], pixels: 'must not be stored' }],
      missing: ['A temporary held frame has no retained reference.'] };
    const read = readStudyPresentation(saved, 'here');
    expect(read?.draft).toBe('Count these');
    expect(read?.position).toBe(12.5);
    expect(read?.views[0].pan).toEqual([.2, .8]);
    expect(read?.views[0].presentation).toBe('masks');
    expect(read?.views[0]).not.toHaveProperty('pixels');
    expect(read?.missing).toEqual(saved.missing);
  });
  it('does not dispatch overlapping switches or a blocked voice/recording switch', async () => {
    const switches = new StudySessionSwitch();
    let finish!: () => void, sent = 0;
    const first = switches.run(() => null, () => new Promise<void>(resolve => { sent++; finish = resolve; }));
    await expect(switches.run(() => null, async () => { sent++; })).rejects.toThrow('finish opening');
    finish(); await first;
    await expect(switches.run(() => 'End voice first.', async () => { sent++; })).rejects.toThrow('End voice first.');
    expect(sent).toBe(1);
    expect(switches.busy).toBe(false);
  });
  it('permits retry after a refused server switch', async () => {
    const switches = new StudySessionSwitch();
    await expect(switches.run(() => null, async () => { throw new Error('missing session'); })).rejects.toThrow('missing session');
    await expect(switches.run(() => null, async () => 'acknowledged')).resolves.toBe('acknowledged');
  });
});
it('preserves placement and display intent through the restoration boundary',()=>{
 const value={schema:1,session:'p',draft:'',views:[{resource:{day:'p',id:'e1',version:0,picture:0},title:'Masks',zoom:1,pan:[.5,.5],placementId:'stable-placement',coordinates:'stage',display:{background:false,reading:42},geometryUnavailable:true}]};
 expect(readStudyPresentation(value,'p')!.views[0]).toMatchObject({placementId:'stable-placement',coordinates:'stage',display:{background:false,reading:42},geometryUnavailable:true});
});
it('restores HTTP documents while native workspace acceptance is unavailable',async()=>{
 const renderers={scientific:vi.fn(),text:vi.fn(),capture:vi.fn(),table:vi.fn(),masks:vi.fn(),nativeReady:vi.fn(()=>new Promise<void>(()=>{})),native:vi.fn(async()=>{})};
 const base={resource:{day:'p',id:'e22',version:0,picture:0},title:'Result',zoom:1,pan:[.5,.5]};
 for(const presentation of ['masks','table','capture'] as const)await restoreStudyItem({...base,presentation},renderers);
 await restoreStudyItem({...base,resource:{...base.resource,id:'d1',picture:null}},renderers);
 expect(renderers.masks).toHaveBeenCalledOnce();expect(renderers.table).toHaveBeenCalledOnce();expect(renderers.capture).toHaveBeenCalledOnce();expect(renderers.text).toHaveBeenCalledOnce();
 for(const renderer of [renderers.masks,renderers.table,renderers.capture,renderers.text])expect(renderer.mock.calls[0][1]).toEqual({background:true});
 expect(renderers.nativeReady).not.toHaveBeenCalled();expect(renderers.native).not.toHaveBeenCalled();
});
it('still waits for native acceptance before restoring a native resource',async()=>{
 let ready!:()=>void;
 const renderers={scientific:vi.fn(),text:vi.fn(),capture:vi.fn(),table:vi.fn(),masks:vi.fn(),nativeReady:vi.fn(()=>new Promise<void>(resolve=>{ready=resolve;})),native:vi.fn(async()=>{})};
 const item={resource:{day:'p',id:'r1',version:1,picture:0},title:'Research result',zoom:1,pan:[.5,.5]};
 const restoring=restoreStudyItem(item,renderers);expect(renderers.native).not.toHaveBeenCalled();ready();await restoring;expect(renderers.native).toHaveBeenCalledWith(item);
});
it('opens exact saved scientific documents without starting native image delivery or a worker',async()=>{
 const renderers={scientific:vi.fn(),text:vi.fn(),capture:vi.fn(),table:vi.fn(),masks:vi.fn(),nativeReady:vi.fn(async()=>{}),native:vi.fn(async()=>{})};
 const item={resource:{day:'p',id:'p'+'a'.repeat(32),version:2,picture:null},title:'Scientific work',zoom:1,pan:[.5,.5]};
 await restoreStudyItem(item,renderers);
 expect(renderers.scientific).toHaveBeenCalledWith(item,{background:true});expect(renderers.native).not.toHaveBeenCalled();expect(renderers.nativeReady).not.toHaveBeenCalled();
});

it('explicit completed-result opening retains foreground intent through the renderer boundary',async()=>{
 const renderers={scientific:vi.fn(),text:vi.fn(),capture:vi.fn(),table:vi.fn(),masks:vi.fn(),nativeReady:vi.fn(async()=>{}),native:vi.fn(async()=>{})};
 const item={resource:{day:'p',id:'p'+'a'.repeat(32),version:2,picture:null},title:'Scientific work',zoom:1,pan:[.5,.5]};
 await restoreStudyItem(item,renderers,{background:false});
 expect(renderers.scientific).toHaveBeenCalledWith(item,{background:false});
});
