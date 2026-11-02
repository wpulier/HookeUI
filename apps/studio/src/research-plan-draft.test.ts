import {expect,test} from 'vitest';
import {mergePlanDraft} from './research-plan-draft';
test('merges unrelated remote edits and asks about the exact conflicting field',()=>{
 const base={method:'old method',output:'old output',limit:'2'};
 const mine={method:'my method',output:'old output',limit:'3'};
 const saved={method:'old method',output:'remote output',limit:'4'};
 expect(mergePlanDraft(base,mine,saved)).toEqual({values:{method:'my method',output:'remote output',limit:'4'},conflicts:['limit']});
});
test('legacy drafts never silently overwrite a saved field',()=>{
 expect(mergePlanDraft(undefined,{method:'mine'},{method:'saved'})).toEqual({values:{method:'saved'},conflicts:['method']});
});
