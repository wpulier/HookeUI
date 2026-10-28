import { recoverChunkTransfer, uploadChunks, type UploadOptions } from './scientific-chunk-upload';
import type { Folder } from './scientific-folder';

type Bridge = { postMessage(message: { op: 'choose' | 'read' | 'release' | 'cancel'; token?: string; offset?: number }): Promise<unknown> };
export type NativeFolder = { name: string; size: number; count: number; bytes: number;
  read(offset: number): Promise<Uint8Array<ArrayBuffer>>; release(): Promise<void> };
export const isNativeFolder = (folder: Folder | NativeFolder): folder is NativeFolder => 'read' in folder;
const object = (value: unknown): value is Record<string, unknown> => value !== null && typeof value === 'object' && !Array.isArray(value);
const CHUNK = 1024 * 1024, MAX_BYTES = 64 * 1024 * 1024 * 1024;
export function nativeFolderBridge(): Bridge | null {
  if (typeof window === 'undefined') return null;
  const host: unknown = window;
  if (!object(host) || !object(host.webkit) || !object(host.webkit.messageHandlers)) return null;
  const handler = host.webkit.messageHandlers.hookeFolder;
  return object(handler) && typeof handler.postMessage === 'function' ? handler as Bridge : null;
}

/** The native owner retains a disk snapshot; JavaScript holds only its opaque lease. */
export async function chooseNativeFolder(bridge: Bridge, signal?: AbortSignal): Promise<Folder | NativeFolder | null> {
  const cancel = () => { void bridge.postMessage({op:'cancel'}).catch(() => undefined); };
  signal?.throwIfAborted(); signal?.addEventListener('abort',cancel,{once:true});
  let result: unknown;
  try { result=await bridge.postMessage({ op: 'choose' }); }
  finally { signal?.removeEventListener('abort',cancel); }
  if (result === null) return null;
  if (!object(result) || typeof result.token !== 'string') throw new Error('Folder selection returned an invalid response. Choose it again.');
  const token = result.token;
  const release = async () => { await bridge.postMessage({ op: 'release', token }).catch(() => undefined); };
  try {
    signal?.throwIfAborted();
    const legacy = result.format === 'hooke-folder-selection-v1';
    if ((!legacy && result.format !== 'hooke-folder-selection-v2') || typeof result.name !== 'string' || !result.name
        || !Number.isSafeInteger(result.size) || Number(result.size) < 0 || Number(result.size) > (legacy ? 64*CHUNK : MAX_BYTES)
        || !Number.isSafeInteger(result.count) || Number(result.count) < 1 || Number(result.count) > 4096
        || !Number.isSafeInteger(result.bytes) || Number(result.bytes) <= Number(result.size) + 4
        || Number(result.bytes) > Number(result.size) + CHUNK + 4)
      throw new Error('Folder selection exceeds the supported limits or has invalid metadata.');
    const total = Number(result.bytes);
    const read = async (offset: number) => {
      if (!Number.isSafeInteger(offset) || offset < 0 || offset >= total) throw new Error('Invalid folder read offset.');
      const encoded = await bridge.postMessage({ op: 'read', token, ...(!legacy ? {offset} : {}) });
      if (typeof encoded !== 'string' || !encoded || encoded.length > 4*Math.ceil(CHUNK/3)) throw new Error('Folder selection was interrupted. Choose it again.');
      const decoded = atob(encoded), bytes = Uint8Array.from(decoded, character => character.charCodeAt(0));
      if (!bytes.length || bytes.length > CHUNK || offset+bytes.length > total) throw new Error('Folder selection returned inconsistent bytes.');
      return bytes;
    };
    if (!legacy) return { name:result.name,size:Number(result.size),count:Number(result.count),bytes:total,read,release };
    // Older installed shells retain their original bounded small-folder behavior.
    const parts: Uint8Array<ArrayBuffer>[] = [];
    let size=0;
    while (size<total) { signal?.throwIfAborted(); const bytes=await read(size); parts.push(bytes); size+=bytes.length; }
    await release();
    return {name:result.name,size:Number(result.size),count:Number(result.count),body:new Blob(parts)};
  } catch (error) { await release(); throw error; }
}

export { ChunkRecoveryRequired as NativeFolderRecoveryRequired } from './scientific-chunk-upload';
export const recoverNativeFolder = (session:string,request:string) => recoverChunkTransfer('directory',session,request);
export const uploadNativeFolder = (folder:NativeFolder,options:UploadOptions) => uploadChunks('directory',folder,options);
