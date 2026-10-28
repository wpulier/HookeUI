/** Shared bounded transport; publication remains the server's existing FileIntake transaction. */
export type SourceKind = 'file' | 'directory';
export type ChunkSource = { name:string; size:number; bytes:number; read(offset:number):Promise<Uint8Array<ArrayBuffer>>; release():Promise<void> };
export type UploadOptions = {session:string;request:string;signal:AbortSignal;progress(done:number,total:number):void};
const object = (value:unknown):value is Record<string,unknown> => value!==null && typeof value==='object' && !Array.isArray(value);
export const CHUNK = 1024*1024, MAX_SOURCE_BYTES = 64*1024*1024*1024;
export class ChunkRecoveryRequired extends Error {}
export async function recoverChunkTransfer(kind:SourceKind,session:string,request:string):Promise<Record<string,unknown>> {
  try {
    const query=new URLSearchParams({session,request});
    const response=await fetch(`/api/scientific-${kind}-transfer-recovery?${query}`,{method:'POST',
      headers:{'X-MicroscopeMind':`import-scientific-${kind}-chunk`},signal:AbortSignal.timeout(10000)});
    const result:unknown=await response.json();
    if (!response.ok || !object(result) || result.ok!==true || !('project' in result)
        || (result.project!==null && !object(result.project))) throw new Error('Recovery did not return an accepted outcome.');
    return result;
  } catch {
    throw new ChunkRecoveryRequired('The import outcome is not known yet. Check its original outcome before choosing another source.');
  }
}

/** One bounded request per selected-source chunk; final acceptance is the existing source transaction. */
export async function uploadChunks(kind:SourceKind,folder:ChunkSource,options:UploadOptions):Promise<unknown> {
  const headers={'X-MicroscopeMind':`import-scientific-${kind}-chunk`};
  let token:string|null=null;
  const request = async (url:string,init:RequestInit) => {
    const response=await fetch(url,{...init,headers,signal:options.signal});
    const result:unknown=await response.json();
    if (!object(result) || !response.ok || result.ok!==true) throw new Error(object(result)
      ? `${String(result.says??'Source transfer failed.')} ${String(result.next??'')}` : 'Source transfer returned an invalid response.');
    return result;
  };
  try {
    options.signal.throwIfAborted();
    const query=new URLSearchParams({session:options.session,request:options.request,name:folder.name,size:String(folder.size),bytes:String(folder.bytes)});
    const started=await request(`/api/scientific-${kind}-transfers?${query}`,{method:'POST'});
    if (typeof started.token!=='string' || !/^[a-f0-9]{64}$/.test(started.token) || started.chunkBytes!==CHUNK
        || !Number.isSafeInteger(started.offset) || Number(started.offset)<0 || Number(started.offset)>folder.bytes)
      throw new Error('The source transfer returned inconsistent admission. Retry the same selection.');
    token=started.token;
    let offset=Number(started.offset); options.progress(offset,folder.bytes);
    while (offset<folder.bytes) {
      options.signal.throwIfAborted();
      const bytes=await folder.read(offset); options.signal.throwIfAborted();
      const reply=await request(`/api/scientific-${kind}-transfers/${token}?offset=${offset}`,{method:'PUT',body:bytes});
      if (reply.offset!==offset+bytes.length) throw new Error('The source acknowledgement changed. Retry the same selection.');
      offset=Number(reply.offset); options.progress(offset,folder.bytes);
    }
    const result=await request(`/api/scientific-${kind}-transfers/${token}/finish`,{method:'POST'});
    await folder.release();
    return result;
  } catch (error) {
    // A cancelled/lost finish may already have published. Join that exact attempt
    // and recover its receipt before a new selection can acquire a new identity.
    const recovered=await recoverChunkTransfer(kind,options.session,options.request);
    if (recovered.project!==null) { await folder.release(); return recovered; }
    throw error;
  } finally {
    if (token) await fetch(`/api/scientific-${kind}-transfers/${token}`,{method:'DELETE',headers,signal:AbortSignal.timeout(5000)}).catch(()=>undefined);
    if (options.signal.aborted) await folder.release();
  }
}

/** A browser File stays on its original selection; only one 1 MiB slice enters memory. */
export function uploadScientificFile(file:File,options:UploadOptions):Promise<unknown> {
  if (!Number.isSafeInteger(file.size) || file.size<0 || file.size>MAX_SOURCE_BYTES) throw new Error('Choose a file up to 64 GiB, subject to free disk space.');
  const source:ChunkSource={name:file.name,size:file.size,bytes:file.size,release:async()=>{},read:async offset=>{
    const expected=Math.min(CHUNK,file.size-offset);
    if (!Number.isSafeInteger(offset) || offset<0 || expected<=0) throw new Error('Invalid source read offset.');
    const bytes=new Uint8Array(await file.slice(offset,offset+expected).arrayBuffer());
    if(bytes.length!==expected)throw new Error('The selected file changed or could not be read. Choose it again.');
    return bytes;
  }};
  return uploadChunks('file',source,options);
}
