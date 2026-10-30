import { expect, test, vi } from 'vitest';
import { SourceIntake } from './source-intake';

test('a later live choice wins over a pending copied source', async () => {
  let finish!: (value: {ok: boolean}) => void;
  const retain = vi.fn(() => new Promise<{ok: boolean}>(resolve => { finish = resolve; }));
  const intake = new SourceIntake(() => 'project-a', retain);
  const first = intake.prepare('a1', 'project-a');
  expect(await intake.prepare('live', 'project-a')).toEqual({ready:true});
  finish({ok: true});
  expect(await first).toEqual({ready:false});
  expect(retain).toHaveBeenCalledWith('a1', 'project-a', expect.stringMatching(/^material-/));
});

test('copy completion cannot navigate a different Project', async () => {
  let current = 'project-a';
  const intake = new SourceIntake(() => current, async () => {
    current = 'project-b'; return {ok: true};
  });
  await expect(intake.prepare('a1', 'project-a')).rejects.toThrow('kept in its Project');
});

test('saved references never resolve or copy an original again', async () => {
  const retain = vi.fn(async () => { throw new Error('Original deleted'); });
  const intake = new SourceIntake(() => 'project-a', retain);
  const ref = {origin:'a'.repeat(32),session:'project-a',document:'p'+'b'.repeat(32),revision:1,digest:'c'.repeat(64)};
  expect(await intake.prepare('a1', 'project-a', ref)).toEqual({ready:true,retained:ref});
  expect(retain).not.toHaveBeenCalled();
});

test('leaving and reopening the same Project does not revive pending navigation', async () => {
  let opening = 1;
  const intake = new SourceIntake(() => 'project-a', async () => {
    opening += 2; return {ok: true};
  }, () => opening);
  await expect(intake.prepare('a1', 'project-a')).rejects.toThrow('Choose it again');
});

test('copy refusal preserves the source error and prevents navigation', async () => {
  const intake = new SourceIntake(() => 'project-a', async () => ({ok: false, error: 'Not enough space'}));
  await expect(intake.prepare('a1', 'project-a')).rejects.toThrow('Not enough space');
});


test('a lost reply retries the same durable key, including after a new client instance', async () => {
  const values = new Map<string,string>();
  const store = {getItem:(key:string)=>values.get(key)??null,
    setItem:(key:string,value:string)=>{values.set(key,value);},removeItem:(key:string)=>{values.delete(key);}};
  const requests:string[]=[];
  const ref={origin:'a'.repeat(32),session:'project-a',document:'p'+'b'.repeat(32),revision:1,digest:'c'.repeat(64)};
  const retain=vi.fn(async (_asset:string,_session:string,request:string)=>{
    requests.push(request);
    if(requests.length===1)throw new Error('Connection lost after acceptance');
    return {ok:true,retained:ref};
  });
  await expect(new SourceIntake(()=> 'project-a',retain,()=>1,store).prepare('a1','project-a')).rejects.toThrow('Connection lost');
  const restarted=new SourceIntake(()=> 'project-a',retain,()=>2,store);
  expect(await restarted.prepare('a1','project-a')).toEqual({ready:true,retained:ref});
  expect(requests[1]).toBe(requests[0]);
  expect(values.size).toBe(0);
  await restarted.prepare('a1','project-a'); // acknowledged explicit new Add
  expect(requests[2]).not.toBe(requests[0]);
});
