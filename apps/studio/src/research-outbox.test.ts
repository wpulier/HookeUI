import { afterEach, expect, test, vi } from 'vitest';
import { ResearchOutbox } from './research-outbox';

afterEach(() => vi.unstubAllGlobals());
test('reconnect/reload replays the original identity and captured selection until durable admission', () => {
  const storage = new Map<string,string>();
  vi.stubGlobal('sessionStorage', {getItem:(key:string)=>storage.get(key) ?? null, setItem:(key:string,value:string)=>storage.set(key,value)});
  const command = {type:'research-followup' as const, rid:'r1', text:'Explain this measurement', expectedBase:2,
    selection:{day:'2026-10-10',rid:'r1',findingVersion:2,findingDigest:'original',anchor:'report:measurements'}};
  const first = new ResearchOutbox().admit(command);
  const restored = new ResearchOutbox();
  expect(restored.admit(command)).toEqual(first);
  expect(restored.forResearcher('2026-10-11','r1')).toEqual([]);
  expect(restored.admit({...command, expectedBase:3}).requestId).not.toBe(first.requestId);
  restored.acknowledge('2026-10-10','r1',[first.requestId]);
  expect(new ResearchOutbox().forResearcher('2026-10-10','r1').map(row=>row.requestId)).not.toContain(first.requestId);
});
