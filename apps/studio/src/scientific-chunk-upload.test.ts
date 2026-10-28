// Synthetic fetch and File slices: transport/ownership, not ordinary browser qualification.
import {afterEach,expect,test,vi} from 'vitest';
import {CHUNK,uploadScientificFile,ChunkRecoveryRequired} from './scientific-chunk-upload';
afterEach(()=>vi.unstubAllGlobals());
const json=(value:unknown)=>new Response(JSON.stringify(value));
const saved={id:'p'+'a'.repeat(32),session:'original-project',revision:1};
function selection(size:number) {
  const file=new File([],'matrix.h5ad');Object.defineProperty(file,'size',{value:size});
  const slice=vi.spyOn(file,'slice').mockImplementation((start=0,end=size)=>new Blob([new Uint8Array(end-start)]));
  const whole=vi.spyOn(file,'arrayBuffer').mockRejectedValue(new Error('No whole-file read'));
  return {file,slice,whole};
}
test('large individual files use bounded slices and preserve the captured Project',async()=>{
  const {file,slice,whole}=selection(65*CHUNK+17);let offset=0;const progress=vi.fn();
  const fetcher=vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url);
    expect(init?.headers).toEqual({'X-MicroscopeMind':'import-scientific-file-chunk'});
    if(path.includes('/scientific-file-transfers?')) {
      expect(new URL(path,'http://local').searchParams.get('session')).toBe('original-project');
      return json({ok:true,token:'b'.repeat(64),offset:0,chunkBytes:CHUNK});
    }
    if(init?.method==='PUT') {const bytes=init.body as Uint8Array;expect(bytes.length).toBeLessThanOrEqual(CHUNK);offset+=bytes.length;return json({ok:true,offset});}
    return json({ok:true,project:saved});
  });
  vi.stubGlobal('fetch',fetcher);
  const result=await uploadScientificFile(file,{session:'original-project',request:'same-request-0123456789',signal:new AbortController().signal,progress});
  expect(result).toEqual({ok:true,project:saved});expect(offset).toBe(file.size);expect(slice).toHaveBeenCalledTimes(66);expect(whole).not.toHaveBeenCalled();
  expect(progress).toHaveBeenLastCalledWith(file.size,file.size);
  expect(fetcher.mock.calls.filter(([url])=>String(url).includes('/scientific-file-transfers?'))).toHaveLength(1);
});
test('a lost finish recovers the same accepted file without a second admission',async()=>{
  const {file}=selection(3);const seen:string[]=[];
  vi.stubGlobal('fetch',vi.fn(async(url:RequestInfo|URL,init?:RequestInit)=>{
    const path=String(url);seen.push(path);
    if(path.includes('/scientific-file-transfers?'))return json({ok:true,token:'b'.repeat(64),offset:0,chunkBytes:CHUNK});
    if(init?.method==='PUT')return json({ok:true,offset:3});
    if(path.endsWith('/finish'))throw new Error('lost acknowledgement');
    return json({ok:true,project:saved});
  }));
  expect(await uploadScientificFile(file,{session:'original-project',request:'original-request-012345',signal:new AbortController().signal,progress:()=>{}})).toEqual({ok:true,project:saved});
  const recovery=seen.find(url=>url.includes('transfer-recovery'))!;
  expect(new URL(recovery,'http://local').searchParams.get('request')).toBe('original-request-012345');
  expect(seen.filter(url=>url.includes('/scientific-file-transfers?'))).toHaveLength(1);
});
test('unknown acceptance remains explicit when recovery also fails',async()=>{
  const {file}=selection(3);vi.stubGlobal('fetch',vi.fn().mockRejectedValue(new Error('offline')));
  await expect(uploadScientificFile(file,{session:'original-project',request:'original-request-012345',signal:new AbortController().signal,progress:()=>{}})).rejects.toBeInstanceOf(ChunkRecoveryRequired);
});
