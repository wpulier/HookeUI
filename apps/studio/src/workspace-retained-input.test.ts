import {expect,test} from 'vitest';
import {exactInputObserved,inputChangeBlockReason} from './workspace-input';

test('same Library alias is not a landed revision until its exact source update arrives',()=>{
 const first={origin:'a'.repeat(32),session:'project',document:'p'+'b'.repeat(32),revision:1,digest:'c'.repeat(64)};
 const next={...first,document:'p'+'d'.repeat(32),digest:'e'.repeat(64)};
 const input={source:'a1',retained:next};
 const stale={onStage:'a1',onStageRetained:first};
 expect(exactInputObserved(input,stale)).toBe(false);
 expect(inputChangeBlockReason(input,stale,true,null)).toContain('keeps its captured input');
 expect(exactInputObserved(input,{onStage:'a1',onStageRetained:next})).toBe(true);
 expect(inputChangeBlockReason(input,{onStage:'a1',onStageRetained:next},true,null)).toBeNull();
});

test('legacy and live inputs retain their previous source identity semantics',()=>{
 expect(exactInputObserved({source:'live'},{onStage:'live'})).toBe(true);
 expect(exactInputObserved({source:'a1'},{onStage:'a1'})).toBe(true);
 expect(inputChangeBlockReason({source:'a2'},{onStage:'a1'},false,'End voice first')).toBe('End voice first');
});
