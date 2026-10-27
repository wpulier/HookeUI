// Synthetic DOM and HTTP responses; ordinary browser upload is verified separately.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
const sheet = vi.hoisted(() => ({ closes: 0, content: null as unknown as TestElement }));
vi.mock('./sheet', () => ({ StudioSheet: class { open(content: TestElement) { sheet.content=content; } close() { sheet.closes++; } } }));
import { ScientificFileImport, showScientificSource, openScientificFileImport } from './scientific-file-import';
let root: TestElement, importer: ScientificFileImport;
const saved = vi.fn(), open = vi.fn(async () => {});
const request = vi.fn<typeof fetch>();
const button = (text: string) => root.querySelectorAll('button').find(item => item.textContent === text)!;
const response = (project = 'origin') => new Response(JSON.stringify({ ok: true, project: {
  id: 'p' + 'a'.repeat(32), session: project, revision: 1, document: { kind: 'source-file', title: 'native.npy' },
} }));
function select(file = new File(['original'], 'native.npy')) {
  const picker = root.querySelector('input')!;
  Object.assign(picker, { files: [file] }); fire(picker, 'change'); return file;
}
async function settle() { for (let i = 0; i < 12; i++) await settleMicrotasks(); }
beforeEach(() => {
  vi.stubGlobal('document', syntheticDOM().document); vi.stubGlobal('fetch', request);
  request.mockReset(); saved.mockClear(); open.mockClear(); sheet.closes = 0;
  importer = new ScientificFileImport({ session: 'origin', title: 'Original Project', saved, open });
  root = importer.content as unknown as TestElement;
});
afterEach(() => vi.unstubAllGlobals());

test('upload retains exact bytes and original Project; ready response does not claim reader readiness', async () => {
  request.mockResolvedValue(response()); const file = select();
  fire(button('Save source to Project'), 'click'); await settle();
  const [url, init] = request.mock.calls[0];
  expect(new URL(String(url), 'http://local').searchParams.get('session')).toBe('origin');
  expect(init?.body).toBe(file); expect(init?.headers).toEqual({ 'X-MicroscopeMind': 'import-scientific-file' });
  expect(saved).toHaveBeenCalledTimes(1); expect(root.textContent).toContain('Reader readiness has not been established');
  expect(open).not.toHaveBeenCalled();
  fire(button('View saved source'), 'click'); await settle();
  expect(open).toHaveBeenCalledWith({ id: 'p' + 'a'.repeat(32), session: 'origin', revision: 1, title: file.name });
  expect(sheet.closes).toBe(1);
});

test('failed acceptance retries the same identity; replacing the file creates a new identity', async () => {
  request.mockRejectedValueOnce(new Error('connection lost')).mockResolvedValue(response()); select();
  fire(button('Save source to Project'), 'click'); await settle();
  expect(root.textContent).toContain('connection lost');
  fire(button('Retry saving source'), 'click'); await settle();
  expect(request.mock.calls[1][0]).toBe(request.mock.calls[0][0]);
  select(); fire(button('Save source to Project'), 'click'); await settle();
  expect(request.mock.calls[2][0]).not.toBe(request.mock.calls[0][0]);
});

test('Stop aborts only this request and retry can recover accepted work', async () => {
  request.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => reject(new Error('aborted')));
  })).mockResolvedValue(response()); select();
  fire(button('Save source to Project'), 'click'); await settle();
  expect(root.querySelector('input')!.disabled).toBe(true);
  fire(button('Stop import'), 'click'); await settle();
  expect(root.textContent).toContain('If acceptance had already completed');
  expect(saved).not.toHaveBeenCalled(); expect(root.querySelector('input')!.disabled).toBe(false);
  fire(button('Retry saving source'), 'click'); await settle();
  expect(request.mock.calls[1][0]).toBe(request.mock.calls[0][0]); expect(saved).toHaveBeenCalledTimes(1);
});

test('oversized input is refused before upload; wrong-Project response is never opened', async () => {
  const large = new File([], 'native.npy'); Object.defineProperty(large, 'size', { value: 64 * 1024 * 1024 + 1 });
  select(large); fire(button('Save source to Project'), 'click'); await settle();
  expect(request).not.toHaveBeenCalled(); expect(root.textContent).toContain('has not been uploaded');
  select(); request.mockResolvedValue(response('other')); fire(button('Save source to Project'), 'click'); await settle();
  expect(saved).not.toHaveBeenCalled(); expect(open).not.toHaveBeenCalled();
  expect(root.textContent).toContain('does not match this source and Project');
});

test('saved source uses exact revision for original-byte download and does not start execution', () => {
  const host = document.createElement('section');
  showScientificSource(host, { sourceFile: { format: 1, name: 'native.npy', size: 8, sha256: 'a'.repeat(64) } },
    { id: 'p' + 'b'.repeat(32), day: 'origin', version: 3 });
  expect(host.textContent).toContain('compatible reader integration is still needed');
  expect(host.querySelector('a')?.href).toContain('session=origin&revision=3');
  expect(request).not.toHaveBeenCalled();
});

test.each([false,true])('Project A → B → A keeps distinct drafts and original upload ownership (running=%s)', async running => {
  const a='project-a-'+running, b='project-b-'+running;
  openScientificFileImport({ session:a,title:'First Project',saved,open }); root=sheet.content;
  const first=root; select();
  let finish!: (value:Response)=>void;
  if(running) {
    request.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
    fire(button('Save source to Project'),'click'); await settle();
  }
  openScientificFileImport({session:b,title:'Second Project',saved,open});
  expect(sheet.content).not.toBe(first); expect(sheet.content.textContent).toContain('Destination: Second Project');
  openScientificFileImport({session:a,title:'First Project',saved,open});
  expect(sheet.content).toBe(first); expect(first.querySelector('input')!.disabled).toBe(running);
  if(running) { finish(response(a)); await settle(); expect(saved).toHaveBeenCalledTimes(1); }
  else expect(first.textContent).toContain('native.npy');
});


test('opening hands focus back only after closing the modal; failed opening keeps recovery available', async () => {
  const afterClose=vi.fn(() => {expect(sheet.closes).toBe(1);});
  const show=vi.fn<() => Promise<void | (() => void)>>();
  importer=new ScientificFileImport({session:'origin',title:'Original Project',saved,open:show});
  root=importer.content as unknown as TestElement;
  request.mockResolvedValue(response());select();
  fire(button('Save source to Project'),'click');await settle();
  show.mockRejectedValueOnce(new Error('Saved source could not be opened'));
  fire(button('View saved source'),'click');await settle();
  expect(sheet.closes).toBe(0);expect(root.textContent).toContain('could not be opened');
  show.mockResolvedValueOnce(afterClose);
  fire(button('View saved source'),'click');await settle();
  expect(afterClose).toHaveBeenCalledTimes(1);
});

test('folder choice uploads a bounded index and exact files, retains captured Project and retry identity', async () => {
  const mode=root.querySelector('select')!; mode.value='directory'; fire(mode,'change');
  const file=new File(['chunk bytes'],'0.0');Object.defineProperty(file,'webkitRelativePath',{value:'sample.zarr/0/0.0'});
  select(file);
  request.mockRejectedValueOnce(new Error('response lost')).mockResolvedValueOnce(new Response(JSON.stringify({ok:true,project:{
    id:'p'+'a'.repeat(32),session:'origin',revision:1,document:{kind:'source-directory',title:'sample.zarr'},
  }})));
  fire(button('Save source to Project'),'click');await settle();
  const [url,init]=request.mock.calls[0];expect(String(url)).toContain('/api/scientific-directories?');
  expect(init?.headers).toEqual({'X-MicroscopeMind':'import-scientific-directory'});
  const bytes=await (init?.body as Blob).arrayBuffer(),length=new DataView(bytes).getUint32(0);
  expect(JSON.parse(new TextDecoder().decode(bytes.slice(4,4+length)))).toEqual({format:'source-directory-v1',entries:[{path:'0/0.0',offset:0,size:11}]});
  expect(new TextDecoder().decode(bytes.slice(4+length))).toBe('chunk bytes');
  fire(button('Retry saving source'),'click');await settle();
  expect(request.mock.calls[1][0]).toBe(url);expect(request.mock.calls[1][1]?.body).toBe(init?.body);
  fire(button('View saved source'),'click');await settle();
  expect(open).toHaveBeenCalledWith({id:'p'+'a'.repeat(32),session:'origin',revision:1,title:'sample.zarr'});
});


test('native folder selection uses the same captured Project acceptance and releases its lease', async () => {
  const postMessage = vi.fn().mockResolvedValueOnce({format:'hooke-folder-selection-v1',token:'lease',name:'data.zarr',size:3,count:2,bytes:20})
    .mockResolvedValueOnce(btoa('a'.repeat(20))).mockResolvedValueOnce(true);
  vi.stubGlobal('window', {webkit:{messageHandlers:{hookeFolder:{postMessage}}}});
  importer = new ScientificFileImport({session:'origin',title:'Original Project',saved,open});root=importer.content as unknown as TestElement;
  const mode=root.querySelector('select')!;Object.assign(mode,{value:'directory'});fire(mode,'change');
  expect(button('Choose folder').hidden).toBe(false);expect(root.querySelector('input')!.hidden).toBe(true);
  fire(button('Choose folder'),'click');await settle();
  expect(root.textContent).toContain('data.zarr · 2 files');expect(postMessage).toHaveBeenLastCalledWith({op:'release',token:'lease'});
  request.mockResolvedValue(new Response(JSON.stringify({ok:true,project:{id:'p'+'b'.repeat(32),session:'origin',revision:1,document:{kind:'source-directory',title:'data.zarr'}}})));
  fire(button('Save source to Project'),'click');await settle();
  expect(String(request.mock.calls[0][0])).toContain('/api/scientific-directories?session=origin');
  expect((request.mock.calls[0][1]?.body as Blob).size).toBe(20);expect(saved).toHaveBeenCalledTimes(1);
  fire(button('View saved source'),'click');await settle();expect(open).toHaveBeenCalledWith({id:'p'+'b'.repeat(32),session:'origin',revision:1,title:'data.zarr'});
});

test('a web address downloads into the captured Project with its expected digest and keeps one request identity across retry', async () => {
  const mode = root.querySelector('select')!; mode.value = 'url'; fire(mode, 'change');
  const [address, digest] = root.querySelectorAll('input').filter(item => item.getAttribute('aria-label')?.includes('web address') || item.getAttribute('aria-label')?.includes('SHA-256'));
  expect(button('Save source to Project').disabled).toBe(true);
  address.value = 'http://data.example.org/cells.npy'; fire(address, 'input');
  expect(button('Save source to Project').disabled).toBe(true); expect(root.textContent).toContain('only from public https');
  address.value = 'https://data.example.org/cells.npy'; digest.value = 'AB'.repeat(32); fire(digest, 'input');
  request.mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, says: 'The server refused access to this address (HTTP 403).', next: 'use a public address' }), { status: 400 }));
  fire(button('Save source to Project'), 'click'); await settle();
  expect(root.textContent).toContain('HTTP 403'); expect(saved).not.toHaveBeenCalled();
  const first = JSON.parse(String(request.mock.calls[0][1]?.body));
  expect(first).toEqual({ session: 'origin', url: 'https://data.example.org/cells.npy', request: first.request, sha256: 'ab'.repeat(32), name: null });
  expect(request.mock.calls[0][0]).toBe('/api/scientific-urls');
  expect(request.mock.calls[0][1]?.headers).toEqual({ 'Content-Type': 'application/json', 'X-MicroscopeMind': 'import-scientific-url' });
  request.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, project: { id: 'p' + 'a'.repeat(32), session: 'origin', revision: 1, document: {
    kind: 'source-file', title: 'cells.npy', state: { sourceRemote: { url: 'https://data.example.org/cells.npy', sha256: 'ab'.repeat(32) } } } } })));
  fire(button('Retry saving source'), 'click'); await settle();
  expect(JSON.parse(String(request.mock.calls[1][1]?.body)).request).toBe(first.request);
  expect(saved).toHaveBeenCalledTimes(1); expect(root.textContent).toContain(`SHA-256 ${'ab'.repeat(32)}`);
  expect(root.textContent).toContain('Reader readiness has not been established');
});

test('a downloaded source says where its bytes came from', () => {
  const host = document.createElement('section');
  showScientificSource(host, { sourceFile: { format: 1, name: 'cells.npy', size: 8, sha256: 'a'.repeat(64) },
    sourceRemote: { url: 'https://data.example.org/latest', final: 'https://mirror.example.org/cells.npy', retrieved: '2026-10-21T05:00:00Z' } },
    { id: 'p' + 'b'.repeat(32), day: 'origin', version: 1 });
  expect(host.textContent).toContain('Downloaded from https://data.example.org/latest (served by https://mirror.example.org/cells.npy) on 2026-10-21T05:00:00Z');
});

test('files open as a stack in natural name order along the axis the researcher names', async () => {
  const mode = root.querySelectorAll('select')[0]; mode.value = 'stack'; fire(mode, 'change');
  const picker = root.querySelector('input')!; expect((picker as unknown as { multiple: boolean }).multiple).toBe(true);
  const files = [new File(['ten'], 'z10.npy'), new File(['two'], 'z2.npy'), new File(['one'], 'z1.npy')];
  Object.assign(picker, { files: [files[0]] }); fire(picker, 'change');
  expect(root.textContent).toContain('Choose 2–4,096 files'); expect(button('Save source to Project').disabled).toBe(true);
  Object.assign(picker, { files: [files[0], new File(['x'], 'other.tif')] }); fire(picker, 'change');
  expect(root.textContent).toContain('share one file type');
  Object.assign(picker, { files: [new File(['x'], '.hidden'), new File(['y'], '.other')] }); fire(picker, 'change');
  expect(root.textContent).toContain('share one file type');
  Object.assign(picker, { files }); fire(picker, 'change');
  expect(root.textContent).toContain('stacked in this order: z1.npy, z2.npy, z10.npy');
  const axis = root.querySelectorAll('select')[1]; axis.value = 't'; fire(axis, 'change');
  request.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, project: { id: 'p' + 'a'.repeat(32), session: 'origin', revision: 1,
    document: { kind: 'source-directory', title: 'z1.npy – z10.npy (3 files)' } } })));
  fire(button('Save source to Project'), 'click'); await settle();
  const [url, init] = request.mock.calls[0], query = new URL(String(url), 'http://local');
  expect(query.pathname).toBe('/api/scientific-stacks'); expect(query.searchParams.get('axis')).toBe('t');
  expect(query.searchParams.get('name')).toBe('z1.npy – z10.npy (3 files)');
  expect(init?.headers).toEqual({ 'X-MicroscopeMind': 'import-scientific-stack' });
  const body = new Uint8Array(await (init?.body as Blob).arrayBuffer()), length = new DataView(body.buffer).getUint32(0);
  const index = JSON.parse(new TextDecoder().decode(body.slice(4, 4 + length)));
  expect(index.entries.map((e: { path: string }) => e.path)).toEqual(['z1.npy', 'z2.npy', 'z10.npy']);
  expect(new TextDecoder().decode(body.slice(4 + length))).toBe('onetwoten');
  expect(saved).toHaveBeenCalledTimes(1);
});

test('declared samples are listed with their reader and download at their pinned digest under one request identity', async () => {
  const declared = { capability: 'mine/exact#read', reader: 'Exact NumPy', id: 'cells', name: 'Nuclei, two planes', url: 'https://data.example.org/cells.npy',
    sha256: 'c'.repeat(64), size: 400, licence: 'CC0-1.0', readerReason: '', canOpen: true, reason: '', scope: 'Declared sample data' };
  request.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, samples: [declared, { ...declared, id: 'photo', name: 'Photo', readerReason: 'This reader does not declare that it keeps byte order exact', canOpen: false, reason: 'Allow this catalog’s code in Models before using its samples' }] })));
  const mode = root.querySelector('select')!; mode.value = 'sample'; fire(mode, 'change'); await settle();
  expect(request.mock.calls[0][0]).toBe('/api/scientific-samples');
  expect(root.textContent).toContain('Exact NumPy · 400 bytes · CC0-1.0'); expect(root.textContent).toContain('keeps byte order exact');
  expect(button('Download Photo').disabled).toBe(true); expect(root.textContent).toContain('Allow this catalog’s code');
  request.mockResolvedValueOnce(new Response(JSON.stringify({ ok: false, says: 'The connection failed (ConnectError); nothing was kept.', next: 'retry' }), { status: 400 }));
  fire(button('Download Nuclei, two planes'), 'click'); await settle();
  expect(root.textContent).toContain('ConnectError');
  const first = JSON.parse(String(request.mock.calls[1][1]?.body));
  expect(first).toEqual({ session: 'origin', capability: 'mine/exact#read', sample: 'cells', request: first.request });
  expect(request.mock.calls[1][1]?.headers).toEqual({ 'Content-Type': 'application/json', 'X-MicroscopeMind': 'open-scientific-sample' });
  request.mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, project: { id: 'p' + 'a'.repeat(32), session: 'origin', revision: 1, document: { kind: 'source-file', title: 'cells.npy',
    state: { sourceRemote: { url: declared.url, sha256: declared.sha256, sample: { capability: 'mine/exact#read', id: 'cells', name: declared.name, licence: 'CC0-1.0' } } } } } })));
  fire(button('Download Nuclei, two planes'), 'click'); await settle();
  expect(JSON.parse(String(request.mock.calls[2][1]?.body)).request).toBe(first.request);
  expect(saved).toHaveBeenCalledTimes(1); expect(root.textContent).toContain(`SHA-256 ${'c'.repeat(64)}`);
});

test('a drop fills the matching choice without uploading; folders are refused with the step that keeps their metadata', async () => {
  const one = new File(['x'], 'native.npy'), planes = [new File(['b'], 'p2.tif'), new File(['a'], 'p1.tif')];
  importer.accept({ files: [one], uri: '', folder: false });
  expect(root.querySelector('select')!.value).toBe('file'); expect(root.textContent).toContain('native.npy · 1 bytes');
  importer.accept({ files: planes, uri: '', folder: false });
  expect(root.querySelector('select')!.value).toBe('stack'); expect(root.textContent).toContain('stacked in this order: p1.tif, p2.tif');
  expect(root.textContent).toContain('Dropped as a stack');
  importer.accept({ files: [], uri: '# a comment\r\nhttps://data.example.org/cells.npy\r\n', folder: false });
  expect(root.querySelector('select')!.value).toBe('url'); expect(button('Save source to Project').disabled).toBe(false);
  importer.accept({ files: [one], uri: '', folder: true });
  expect(root.textContent).toContain('Choose Folder and select it instead');
  expect(request).not.toHaveBeenCalled();
  const event = Object.assign(new Event('drop', { cancelable: true, bubbles: true }), { dataTransfer: { files: [one], items: [], getData: () => '' } });
  root.dispatchEvent(event); expect(event.defaultPrevented).toBe(true);
  expect(root.querySelector('select')!.value).toBe('file'); expect(request).not.toHaveBeenCalled();
});

test('after a drop, the picker still reads what the researcher chooses next', async () => {
  importer.accept({ files: [new File(['x'], 'dropped.npy')], uri: '', folder: false });
  select(new File(['picked bytes'], 'picked.npy'));
  expect(root.textContent).toContain('picked.npy · 12 bytes');
  request.mockResolvedValue(response());
  fire(button('Save source to Project'), 'click'); await settle();
  expect((request.mock.calls[0][1]?.body as File).name).toBe('picked.npy');
});

test('v2 native selection streams through owned chunk intake and retains captured Project outcome',async()=>{
  const token='c'.repeat(64);
  const postMessage=vi.fn().mockImplementation(async(message:{op:string})=>message.op==='choose'
    ? {format:'hooke-folder-selection-v2',token:'lease',name:'data.zarr',size:3,count:2,bytes:20}
    : message.op==='read'?btoa('a'.repeat(20)):true);
  vi.stubGlobal('window',{webkit:{messageHandlers:{hookeFolder:{postMessage}}}});
  importer=new ScientificFileImport({session:'origin',title:'Original Project',saved,open});root=importer.content as unknown as TestElement;
  const mode=root.querySelector('select')!;mode.value='directory';fire(mode,'change');
  fire(button('Choose folder'),'click');await settle();
  expect(postMessage).toHaveBeenCalledTimes(1);
  request.mockImplementation(async(url,init)=>new Response(JSON.stringify(init?.method==='PUT'?{ok:true,offset:20}
    :String(url).endsWith('/finish')?{ok:true,project:{id:'p'+'b'.repeat(32),session:'origin',revision:1,document:{kind:'source-directory',title:'data.zarr'}}}
    :init?.method==='DELETE'?{ok:true}:{ok:true,token,offset:0,chunkBytes:1024*1024})));
  fire(button('Save source to Project'),'click');await settle();
  expect(String(request.mock.calls[0][0])).toContain('/api/scientific-directory-transfers?session=origin');
  expect(request.mock.calls.map(([,init])=>init?.method)).toEqual(['POST','PUT','POST','DELETE']);
  expect(postMessage.mock.calls.map(([message])=>message.op)).toEqual(['choose','read','release']);
  expect(saved).toHaveBeenCalledTimes(1);expect(root.textContent).toContain('Source saved in Original Project');
  fire(button('View saved source'),'click');await settle();
  expect(open).toHaveBeenCalledWith({id:'p'+'b'.repeat(32),session:'origin',revision:1,title:'data.zarr'});
});

test('uncertain stopped finish keeps original recovery identity before another selection',async()=>{
  const token='d'.repeat(64);
  const postMessage=vi.fn().mockImplementation(async(message:{op:string})=>message.op==='choose'
    ? {format:'hooke-folder-selection-v2',token:'lease',name:'data.zarr',size:3,count:2,bytes:20}
    : message.op==='read'?btoa('a'.repeat(20)):true);
  vi.stubGlobal('window',{webkit:{messageHandlers:{hookeFolder:{postMessage}}}});
  importer=new ScientificFileImport({session:'origin',title:'Original Project',saved,open});root=importer.content as unknown as TestElement;
  const mode=root.querySelector('select')!;mode.value='directory';fire(mode,'change');
  fire(button('Choose folder'),'click');await settle();
  request.mockImplementation(async(url,init)=>{
    if(String(url).endsWith('/finish'))return new Promise((_resolve,reject)=>init?.signal?.addEventListener('abort',()=>reject(new Error('lost finish'))));
    if(String(url).includes('transfer-recovery'))throw new Error('offline while recovering');
    return new Response(JSON.stringify(init?.method==='PUT'?{ok:true,offset:20}
      :init?.method==='DELETE'?{ok:true}:{ok:true,token,offset:0,chunkBytes:1024*1024}));
  });
  fire(button('Save source to Project'),'click');await settle();
  const original=new URL(String(request.mock.calls[0][0]),'http://localhost').searchParams.get('request');
  fire(button('Stop import'),'click');await settle();
  expect(button('Choose folder').disabled).toBe(true);expect(mode.disabled).toBe(true);
  expect(root.textContent).toContain('outcome is not known');expect(saved).not.toHaveBeenCalled();
  importer.accept({files:[new File(['different'],'replacement.npy')],uri:'',folder:false});
  expect(mode.value).toBe('directory');expect(root.textContent).toContain('Check the original import outcome');
  request.mockImplementation(async()=>new Response(JSON.stringify({ok:true,project:{id:'p'+'b'.repeat(32),session:'origin',revision:1,document:{kind:'source-directory',title:'data.zarr'}}})));
  fire(button('Check import outcome'),'click');await settle();
  const recovery=request.mock.calls.filter(([url])=>String(url).includes('transfer-recovery'));
  expect(recovery).toHaveLength(2);
  for(const [url] of recovery)expect(new URL(String(url),'http://localhost').searchParams.get('request')).toBe(original);
  expect(request.mock.calls.filter(([url])=>String(url).includes('/scientific-directory-transfers?'))).toHaveLength(1);
  expect(saved).toHaveBeenCalledTimes(1);expect(root.textContent).toContain('Source saved in Original Project');
});
