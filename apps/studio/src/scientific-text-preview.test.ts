// Synthetic DOM/transport, real component. No browser or scientific reader qualification.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import { textPreview } from './scientific-text-preview';
const reference = { id: 'p' + 'a'.repeat(32), day: '2026-10-29', version: 3 };
const hash = 'b'.repeat(64), request = vi.fn<typeof fetch>();
let root: TestElement;
const button = (name: string) => root.querySelectorAll('button').find(item => item.textContent === name)!;
const response = (offset=0,size=70000,changes:Record<string,unknown>={}) => new Response(JSON.stringify({ok:true,
  id:reference.id,session:reference.day,revision:3,sha256:hash,verification:'displayed-chunks-only',
  content:{text:'<script>unsafe()</script>',offset,bytes:Math.min(65536,size-offset),totalBytes:size,
    nextOffset:offset+65536<size?offset+65536:null},...changes}));
async function settle() { for(let i=0;i<8;i++)await settleMicrotasks(); }
beforeEach(() => {
  vi.stubGlobal('document',syntheticDOM().document); vi.stubGlobal('fetch',request);request.mockReset();
  root=textPreview(reference,hash,70000) as unknown as TestElement;
});
afterEach(() => vi.unstubAllGlobals());

test('explicit text request keeps original identity, renders literal content and pages with bounded memory',async()=>{
  expect(request).not.toHaveBeenCalled();request.mockResolvedValueOnce(response()).mockResolvedValueOnce(response(65536));
  fire(button('Preview as text'),'click');await settle();
  expect(request.mock.calls[0][0]).toContain(`session=${reference.day}&revision=3&offset=0&length=65536`);
  expect(root.querySelector('pre')?.textContent).toBe('<script>unsafe()</script>');
  expect(root.querySelector('script')).toBeNull();expect(button('Previous page').disabled).toBe(true);
  fire(button('Next page'),'click');await settle();
  expect(request.mock.calls[1][0]).toContain('offset=65536');expect(button('Next page').disabled).toBe(true);
  expect(button('Previous page').disabled).toBe(false);expect(root.querySelectorAll('pre')).toHaveLength(1);
});

test.each([{session:'foreign'},{revision:4},{sha256:'c'.repeat(64)},
  {content:{text:'wrong range',offset:1,bytes:1,totalBytes:70000,nextOffset:null}}])('wrong saved identity/range never displays (%j)',async changes=>{
  request.mockResolvedValue(response(0,70000,changes));fire(button('Preview as text'),'click');await settle();
  expect(root.querySelector('pre')?.textContent).toBe('');expect(root.textContent).toContain('did not match');
  expect(button('Retry preview').hidden).toBe(false);
});

test('failed next page preserves previous text; retry uses the failed range',async()=>{
  request.mockResolvedValueOnce(response()).mockRejectedValueOnce(new Error('temporarily unavailable')).mockResolvedValueOnce(response(65536));
  fire(button('Preview as text'),'click');await settle();fire(button('Next page'),'click');await settle();
  expect(root.textContent).toContain('temporarily unavailable');expect(root.querySelector('pre')?.textContent).toBe('<script>unsafe()</script>');
  fire(button('Retry preview'),'click');await settle();expect(request.mock.calls[2][0]).toBe(request.mock.calls[1][0]);
  expect(button('Retry preview').hidden).toBe(true);
});
