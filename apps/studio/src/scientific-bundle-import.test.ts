// Synthetic DOM / HTTP; actual file chooser and native bundle roundtrip remain runtime gates.
import {beforeEach,afterEach,expect,test,vi} from 'vitest';
import {TestElement,fire,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';
vi.mock('./sheet',()=>({StudioSheet:class{open(){}close(){}}}));
import {ScientificBundleImport} from './scientific-bundle-import';
let root:TestElement;
const request=vi.fn<typeof fetch>(),saved=vi.fn(),open=vi.fn(async()=>{});
const target={id:'p'+'a'.repeat(32),session:'2026-10-22.1',revision:2,digest:'b'.repeat(64)};
const owner={session:target.session,state:'matched',originDigest:'c'.repeat(64)};
const button=(name:string)=>root.querySelectorAll('button').find(e=>e.textContent===name)!;
const settle=async()=>{for(let i=0;i<16;i++)await settleMicrotasks();};
function pick(file=new File(['original native bundle'],'kept.zip')){
 const picker=root.querySelector('input')!;Object.assign(picker,{files:[file]});fire(picker,'change');return file;
}
beforeEach(()=>{
 vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',request);
 request.mockReset();saved.mockClear();open.mockClear();open.mockResolvedValue();
 root=new ScientificBundleImport({saved,open}).content as unknown as TestElement;
});
afterEach(()=>vi.unstubAllGlobals());
test('imports the selected native bytes and opens only the exact accepted original owner on request',async()=>{
 request.mockResolvedValue(new Response(JSON.stringify({ok:true,target,owner})));
 const file=pick();fire(button('Import bundle'),'click');await settle();
 expect(request).toHaveBeenCalledTimes(1);
 const [url,init]=request.mock.calls[0];expect(url).toBe('/api/scientific-bundles');
 expect(init?.body).toBe(file);expect(init?.headers).toEqual({'X-MicroscopeMind':'import-bundle'});
 expect(saved).toHaveBeenCalledWith(target);expect(open).not.toHaveBeenCalled();
 expect(root.textContent).toContain('Nothing was executed');
 fire(button('Open imported work'),'click');await settle();expect(open).toHaveBeenCalledWith(target,owner);
});

test('an uncertain response retains the same file for retry and never opens unconfirmed work',async()=>{
 request.mockRejectedValueOnce(new TypeError('Connection ended')).mockResolvedValueOnce(new Response(JSON.stringify({ok:true,target,owner})));
 const file=pick();fire(button('Import bundle'),'click');await settle();
 expect(root.textContent).toContain('Acceptance is not confirmed');expect(saved).not.toHaveBeenCalled();expect(open).not.toHaveBeenCalled();
 expect(button('Open imported work').hidden).toBe(true);expect(button('Import bundle').disabled).toBe(false);
 fire(button('Import bundle'),'click');await settle();
 expect(request.mock.calls.map(([,init])=>init?.body)).toEqual([file,file]);expect(saved).toHaveBeenCalledWith(target);
});

test.each([
 {ok:false,says:'This profile holds a different document with the same identity',next:'Keep both originals'},
 {ok:true,target:{...target,digest:'changed'}},
 {ok:true,target:{...target,revision:0}},
 {ok:true,target:{...target,id:'../other'}},
])('refused or malformed acceptance keeps existing work and offers no open action: %j',async body=>{
 request.mockResolvedValue(new Response(JSON.stringify(body),{status:body.ok?200:400}));
 pick();fire(button('Import bundle'),'click');await settle();
 expect(saved).not.toHaveBeenCalled();expect(open).not.toHaveBeenCalled();expect(button('Open imported work').hidden).toBe(true);
 expect(root.textContent).toContain('Acceptance is not confirmed');
});

test('an import stays bound to its selected bytes while pending and cannot be duplicated',async()=>{
 let respond!:(response:Response)=>void;request.mockImplementation(()=>new Promise(resolve=>{respond=resolve;}));
 const file=pick();fire(button('Import bundle'),'click');await settle();
 expect(root.querySelector('input')!.disabled).toBe(true);expect(button('Import bundle').disabled).toBe(true);
 // Even a delayed chooser event cannot substitute the captured input mid-upload.
 pick(new File(['another'],'other.zip'));fire(button('Import bundle'),'click');await settle();
 expect(request).toHaveBeenCalledTimes(1);expect(request.mock.calls[0][1]?.body).toBe(file);
 respond(new Response(JSON.stringify({ok:true,target,owner})));await settle();
 expect(saved).toHaveBeenCalledWith(target);expect(root.textContent).toContain('revision 2');
});

test('missing original Project explains retained ownership and retries Open without uploading again',async()=>{
 request.mockResolvedValue(new Response(JSON.stringify({ok:true,target,owner})));open.mockRejectedValueOnce(new Error('Original Project unavailable'));
 pick();fire(button('Import bundle'),'click');await settle();fire(button('Open imported work'),'click');await settle();
 expect(root.textContent).toContain('does not restore its full conversation or workspace');expect(button('Open imported work').disabled).toBe(false);
 fire(button('Open imported work'),'click');await settle();expect(open).toHaveBeenCalledTimes(2);expect(request).toHaveBeenCalledTimes(1);
});

test('a new file clears earlier acceptance instead of opening the previous receipt',async()=>{
 request.mockResolvedValue(new Response(JSON.stringify({ok:true,target,owner})));pick();fire(button('Import bundle'),'click');await settle();
 pick(new File(['new'],'other.zip'));expect(button('Open imported work').hidden).toBe(true);
 fire(button('Open imported work'),'click');await settle();expect(open).not.toHaveBeenCalled();
});

test('empty and transport-oversized inputs remain local and explain the bounded import',async()=>{
 pick(new File([],'empty.zip'));expect(button('Import bundle').disabled).toBe(true);
 const large=new File(['small allocation'],'large.zip');Object.defineProperty(large,'size',{value:258*1024*1024});pick(large);
 expect(button('Import bundle').disabled).toBe(true);fire(button('Import bundle'),'click');await settle();expect(request).not.toHaveBeenCalled();
 expect(root.textContent).toContain('257 MiB');
});

test.each([
 undefined,
 {session:target.session,state:'unavailable',reason:'Legacy bundle has no original owner identity.'},
 {session:'2026-10-22.9',state:'matched',originDigest:'c'.repeat(64)},
 {session:target.session,state:'restored',originDigest:'invalid'},
 {session:target.session,state:'matched'},
 {session:target.session,state:'unexpected',originDigest:'c'.repeat(64)},
])('native acceptance without validated matching ownership cannot open: %j',async owner=>{
 request.mockResolvedValue(new Response(JSON.stringify({ok:true,target,owner})));
 pick();fire(button('Import bundle'),'click');await settle();
 expect(saved).not.toHaveBeenCalled();
 expect(button('Open imported work').hidden).toBe(true);
 expect(root.textContent).toContain('Project ownership is unavailable');
 fire(button('Open imported work'),'click');await settle();expect(open).not.toHaveBeenCalled();
});


test('restored identity permits exact Open without claiming full conversation or workspace restoration',async()=>{
 const restored={...owner,state:'restored'};request.mockResolvedValue(new Response(JSON.stringify({ok:true,target,owner:restored})));
 pick();fire(button('Import bundle'),'click');await settle();
 expect(button('Open imported work').hidden).toBe(false);expect(root.textContent).toContain('the full conversation and workspace were not restored');
 fire(button('Open imported work'),'click');await settle();expect(open).toHaveBeenCalledWith(target,restored);
});

test('a later unavailable owner cannot reuse the previous successful ownership receipt',async()=>{
 request.mockResolvedValueOnce(new Response(JSON.stringify({ok:true,target,owner})))
 .mockResolvedValueOnce(new Response(JSON.stringify({ok:true,target,owner:{session:target.session,state:'unavailable',reason:'Original ownership cannot be verified.'}})));
 pick();fire(button('Import bundle'),'click');await settle();expect(button('Open imported work').hidden).toBe(false);
 fire(button('Import bundle'),'click');await settle();expect(button('Open imported work').hidden).toBe(true);
 fire(button('Open imported work'),'click');await settle();expect(open).not.toHaveBeenCalled();
 expect(root.textContent).toContain('Original ownership cannot be verified.');
});

test.each(['success','failure'])('opening captured work serializes file selection and imports until %s',async outcome=>{
 let resolve!:()=>void,reject!:(error:Error)=>void;
 open.mockImplementationOnce(()=>new Promise<void>((done,fail)=>{resolve=done;reject=fail;}));
 request.mockResolvedValue(new Response(JSON.stringify({ok:true,target,owner})));
 const original=pick();fire(button('Import bundle'),'click');await settle();
 fire(button('Open imported work'),'click');await settle();
 const disabledWhileOpening=[root.querySelector('input')!.disabled,button('Import bundle').disabled,button('Open imported work').disabled];
 // A chooser event already queued before disable must not replace captured ownership.
 pick(new File(['second bundle'],'other.zip'));fire(button('Import bundle'),'click');
 fire(button('Open imported work'),'click');await settle();
 expect(request).toHaveBeenCalledTimes(1);expect(open).toHaveBeenCalledExactlyOnceWith(target,owner);
 expect(disabledWhileOpening).toEqual([true,true,true]);
 if(outcome==='failure')reject(new Error('Project opening failed'));else resolve();
 await settle();
 expect(root.querySelector('input')!.disabled).toBe(false);expect(button('Import bundle').disabled).toBe(false);
 expect(button('Open imported work').disabled).toBe(false);
 if(outcome==='success')expect(root.textContent).toContain('Accepted revision 2 opened');
 if(outcome==='failure'){
  expect(root.textContent).toContain('retry Open');fire(button('Open imported work'),'click');await settle();
  expect(open).toHaveBeenLastCalledWith(target,owner);expect(open).toHaveBeenCalledTimes(2);
 }
 request.mockResolvedValueOnce(new Response(JSON.stringify({ok:true,target,owner})));
 fire(button('Import bundle'),'click');await settle();
 expect(request.mock.calls[1][1]?.body).toBe(original);
});
