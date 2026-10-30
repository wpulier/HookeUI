import {expect,test,vi} from 'vitest';
import {listStudies} from './research-efforts';
test('interrupted body and malformed Study responses are failures, never empty Studies',async()=>{
 const aborted=new Error('Body aborted');
 const fetcher=vi.fn(async()=>({ok:true,status:200,json:async()=>{throw aborted;}})) as unknown as typeof fetch;
 await expect(listStudies('p1',fetcher)).rejects.toThrow('Body aborted');
 const missing=vi.fn(async()=>({ok:true,status:200,json:async()=>({})})) as unknown as typeof fetch;
 await expect(listStudies('p1',missing)).rejects.toThrow('could not be read');
});
