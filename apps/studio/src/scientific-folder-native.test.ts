import { expect, test, vi } from 'vitest';
import { chooseNativeFolder, isNativeFolder, uploadNativeFolder, type NativeFolder } from './scientific-folder-native';
const selection = { format: 'hooke-folder-selection-v1', token: 'private', name: 'data.zarr', size: 3, count: 2, bytes: 20 };
test('native selected bytes survive transfer and their disk lease releases', async () => {
  const postMessage = vi.fn().mockResolvedValueOnce(selection).mockResolvedValueOnce(btoa('a'.repeat(20))).mockResolvedValueOnce(true);
  const folder = await chooseNativeFolder({postMessage});
  expect(folder).toMatchObject({name:'data.zarr',size:3,count:2}); expect(folder && !isNativeFolder(folder) ? folder.body.size : null).toBe(20);
  expect(postMessage.mock.calls.map(call=>call[0].op)).toEqual(['choose','read','release']);
});
test('cancelled chooser publishes nothing',async()=>{expect(await chooseNativeFolder({postMessage:vi.fn().mockResolvedValue(null)})).toBeNull();});
test.each([[''],[btoa('a'.repeat(21))],[42]])('truncated or inconsistent native transfer releases and refuses: %s',async(chunk)=>{
  const postMessage=vi.fn().mockResolvedValueOnce(selection).mockResolvedValueOnce(chunk).mockResolvedValue(true);
  await expect(chooseNativeFolder({postMessage})).rejects.toThrow(); expect(postMessage).toHaveBeenLastCalledWith({op:'release',token:'private'});
});
test('read failure releases the lease without disguising the error',async()=>{
  const postMessage=vi.fn().mockResolvedValueOnce(selection).mockRejectedValueOnce(new Error('disk failed')).mockResolvedValue(true);
  await expect(chooseNativeFolder({postMessage})).rejects.toThrow('disk failed'); expect(postMessage).toHaveBeenLastCalledWith({op:'release',token:'private'});
});
test('oversized native declaration refuses before reads and releases',async()=>{
  const postMessage=vi.fn().mockResolvedValueOnce({...selection,size:65*1024*1024}).mockResolvedValue(true);
  await expect(chooseNativeFolder({postMessage})).rejects.toThrow();expect(postMessage.mock.calls.map(call=>call[0].op)).toEqual(['choose','release']);
});


test('v2 selection keeps a disk lease and reads only a requested bounded chunk',async()=>{
  const postMessage=vi.fn().mockResolvedValueOnce({...selection,format:'hooke-folder-selection-v2',size:128*1024*1024,bytes:128*1024*1024+100})
    .mockResolvedValueOnce(btoa('abc')).mockResolvedValue(true);
  const folder=await chooseNativeFolder({postMessage});
  expect(postMessage).toHaveBeenCalledTimes(1);expect(folder && isNativeFolder(folder)).toBe(true);
  if (!folder || !isNativeFolder(folder)) throw new Error('missing disk lease');
  expect(await folder.read(50)).toEqual(new Uint8Array([97,98,99]));
  expect(postMessage).toHaveBeenLastCalledWith({op:'read',token:'private',offset:50});
  await folder.release();expect(postMessage).toHaveBeenLastCalledWith({op:'release',token:'private'});
});

test('native upload reads the next chunk only after its acknowledgement and releases on completion',async()=>{
  const events:string[]=[];const token='a'.repeat(64);
  const folder:NativeFolder={name:'volume.zarr',size:3,count:1,bytes:6,
    read:vi.fn(async offset=>{events.push('read'+offset);return new Uint8Array(3).fill(offset);}),release:vi.fn(async()=>{})};
  vi.stubGlobal('fetch',vi.fn(async(url:string,init:RequestInit)=>{
    if (init.method==='DELETE') return {ok:true,json:async()=>({ok:true})};
    if (init.method==='PUT') {const offset=Number(new URL(url,'http://localhost').searchParams.get('offset'));events.push('ack'+offset);return {ok:true,json:async()=>({ok:true,offset:offset+3})};}
    return {ok:true,json:async()=>url.endsWith('/finish')?{ok:true,project:{id:'saved'}}:{ok:true,token,offset:0,chunkBytes:1024*1024}};
  }));
  const progress=vi.fn();
  try {
    expect(await uploadNativeFolder(folder,{session:'project',request:'request',signal:new AbortController().signal,progress})).toMatchObject({project:{id:'saved'}});
    expect(events).toEqual(['read0','ack0','read3','ack3']);expect(progress).toHaveBeenLastCalledWith(6,6);expect(folder.release).toHaveBeenCalledTimes(1);
  } finally {vi.unstubAllGlobals();}
});

test('Stop revokes server transfer and native snapshot before another chunk',async()=>{
  const controller=new AbortController(), token='a'.repeat(64);
  const folder:NativeFolder={name:'volume.zarr',size:3,count:1,bytes:6,
    read:vi.fn(async()=>{controller.abort();return new Uint8Array(3);}),release:vi.fn(async()=>{})};
  const fetcher=vi.fn(async()=>({ok:true,json:async()=>({ok:true,token,offset:0,chunkBytes:1024*1024})}));vi.stubGlobal('fetch',fetcher);
  try {
    await expect(uploadNativeFolder(folder,{session:'project',request:'request',signal:controller.signal,progress:vi.fn()})).rejects.toThrow();
    expect(folder.read).toHaveBeenCalledTimes(1);expect(folder.release).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls.at(-1)).toMatchObject([`/api/scientific-directory-transfers/${token}`,{method:'DELETE'}]);
  } finally {vi.unstubAllGlobals();}
});
