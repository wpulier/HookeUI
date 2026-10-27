import {afterEach,expect,test,vi} from 'vitest';
import {configureDataTableDocuments} from './datatable-presentation';
import {savedTableAnalyses} from './datatable-derived-presentation';
afterEach(()=>vi.unstubAllGlobals());
test('graph choices read exact accepted analyses only for the captured table revision and opening',async()=>{
 let generation=1;
 configureDataTableDocuments({owner:()=>({session:'p',generation}),current:o=>o.session==='p'&&o.generation===generation,remember:vi.fn(),settle:vi.fn()});
 const source={session:'p',id:'tb1',version:3,sha256:'a'.repeat(64)};
 const record={id:'an1',kind:'analysis',version:2,title:'Welch',mode:'frozen',source,spec:{},result:{},freshness:{state:'frozen',words:'Frozen'}};
 const reply=(value:unknown)=>({ok:true,json:async()=>value});
 const request=vi.fn().mockResolvedValueOnce(reply({entries:[
 {id:'an1',kind:'analysis',version:2,day:'p',values:{source}},
 {id:'an2',kind:'analysis',version:5,day:'p',values:{source:{...source,version:2}}},
 {id:'an3',kind:'analysis',version:1,day:'other',values:{source}},
 ]})).mockResolvedValueOnce(reply(record));
 vi.stubGlobal('fetch',request);
 expect(await savedTableAnalyses(source,{session:'p',generation:1})).toEqual([record]);
 expect(request.mock.calls[1][0]).toBe('/api/table-derivatives/an1?session=p&version=2');expect(request).toHaveBeenCalledTimes(2);
 request.mockReset().mockImplementationOnce(async()=>{generation+=2;return reply({entries:[]});});
 expect(await savedTableAnalyses(source,{session:'p',generation:1})).toEqual([]);expect(request).toHaveBeenCalledOnce();
});
