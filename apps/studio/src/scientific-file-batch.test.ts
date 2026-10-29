// Synthetic transport receipts; original bounded HTTP transaction is tested separately.
import {beforeEach,expect,test,vi} from 'vitest';
const transport=vi.hoisted(()=>({upload:vi.fn(),recover:vi.fn()}));
vi.mock('./scientific-chunk-upload',()=>({MAX_SOURCE_BYTES:64*1024**3,
  ChunkRecoveryRequired:class extends Error{},uploadScientificFile:transport.upload,recoverChunkTransfer:transport.recover}));
import {ChunkRecoveryRequired} from './scientific-chunk-upload';
import {ScientificFileBatch} from './scientific-file-batch';
const receipt=(file:File,session='original')=>({project:{id:'p'+'a'.repeat(32),session,revision:1,document:{kind:'source-file',title:file.name}}});
const files=()=>[new File(['data'],'training.h5ad'),new File(['rules'],'rules.pdf')];
beforeEach(()=>{transport.upload.mockReset();transport.recover.mockReset();});

test('different formats are separate retained files; retry skips accepted items and keeps original owner/request',async()=>{
  const selection=files(),batch=new ScientificFileBatch('original',selection),saved=vi.fn();
  transport.upload.mockImplementationOnce(async file=>receipt(file)).mockRejectedValueOnce(new Error('lost connection'));
  await expect(batch.run(new AbortController().signal,vi.fn(),saved)).rejects.toThrow('lost connection');
  expect(batch.count).toBe(1);expect(saved).toHaveBeenCalledTimes(1);
  const request=transport.upload.mock.calls[1][1].request;
  transport.upload.mockImplementationOnce(async file=>receipt(file));
  await batch.run(new AbortController().signal,vi.fn(),saved);
  expect(batch.completed).toBe(true);expect(saved).toHaveBeenCalledTimes(2);
  expect(transport.upload.mock.calls.map(call=>call[0].name)).toEqual(['training.h5ad','rules.pdf','rules.pdf']);
  expect(transport.upload.mock.calls[2][1]).toMatchObject({session:'original',request});
});

test('unknown acceptance is recovered before any further upload and accepted work is not repeated',async()=>{
  const selection=files(),batch=new ScientificFileBatch('original',selection),saved=vi.fn();
  transport.upload.mockRejectedValueOnce(new ChunkRecoveryRequired('unknown'));
  await expect(batch.run(new AbortController().signal,vi.fn(),saved)).rejects.toThrow('unknown');
  expect(batch.needsRecovery).toBe(true);
  transport.recover.mockResolvedValueOnce(receipt(selection[0]));
  transport.upload.mockImplementationOnce(async file=>receipt(file));
  await batch.run(new AbortController().signal,vi.fn(),saved);
  expect(transport.recover).toHaveBeenCalledWith('file','original',batch.items[0].request);
  expect(transport.upload.mock.calls.map(call=>call[0].name)).toEqual(['training.h5ad','rules.pdf']);
  expect(batch.completed).toBe(true);
});

test('Stop keeps accepted files and prevents admission of the next file',async()=>{
  const batch=new ScientificFileBatch('original',files()),controller=new AbortController(),saved=vi.fn(()=>controller.abort());
  transport.upload.mockImplementation(async file=>receipt(file));
  await expect(batch.run(controller.signal,vi.fn(),saved)).rejects.toThrow();
  expect(batch.count).toBe(1);expect(transport.upload).toHaveBeenCalledTimes(1);
  await batch.run(new AbortController().signal,vi.fn(),vi.fn());
  expect(batch.completed).toBe(true);expect(transport.upload).toHaveBeenCalledTimes(2);
});

test('foreign or changed receipt cannot be accepted or followed by another file',async()=>{
  const batch=new ScientificFileBatch('original',files());
  transport.upload.mockImplementation(async file=>receipt(file,'another-project'));
  await expect(batch.run(new AbortController().signal,vi.fn(),vi.fn())).rejects.toThrow('does not match');
  expect(batch.count).toBe(0);expect(batch.needsRecovery).toBe(true);
  expect(transport.upload).toHaveBeenCalledTimes(1);
});

test('limits are checked before any transfer',()=>{
  expect(()=>new ScientificFileBatch('original',[])).toThrow();
  expect(()=>new ScientificFileBatch('original',Array.from({length:257},()=>files()[0]))).toThrow();
  const file=files()[0];Object.defineProperty(file,'size',{value:64*1024**3+1});
  expect(()=>new ScientificFileBatch('original',[file])).toThrow('64 GiB');
  expect(transport.upload).not.toHaveBeenCalled();
});
