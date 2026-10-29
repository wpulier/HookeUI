// Synthetic DOM/HTTP; actual populated browser qualification is separate.
import { beforeEach, afterEach, expect, test, vi } from 'vitest';
import { TestElement, fire, settleMicrotasks, syntheticDOM } from './workspace-windows.test-support';
import type { DataEntry } from './data-roles';
const sheet = vi.hoisted(() => ({closes: 0, content: null as unknown as TestElement}));
vi.mock('./sheet', () => ({StudioSheet: class {open(content: TestElement) {sheet.content = content;} close() {sheet.closes++;}}}));
import { organizeMaterial } from './material-organization';
const request = vi.fn<typeof fetch>();
const entry = (id: string): DataEntry => ({id, kind:'project', title:'Model', day:'2026-10-29', version:2,
  source:{sha256:'a'.repeat(64)}, summary:'', state:'saved', actions:[],
  material:{kind:'model',origin:'generated',input:false,section:'model',category:'analysis',record:false,dependencies:[]}});
const saved = vi.fn();
const response = (id: string, session = '2026-10-29', revision = 1) => new Response(JSON.stringify({ok:true,project:{id:'p'+'c'.repeat(32),session,revision,document:{state:{materialMembership:{reference:{id,revision:2,digest:'a'.repeat(64)}}}}}}));
async function settle() {for(let i=0;i<12;i++) await settleMicrotasks();}
beforeEach(()=>{Object.defineProperty(TestElement.prototype,'focus',{value:()=>{},configurable:true});vi.stubGlobal('document',syntheticDOM().document);vi.stubGlobal('fetch',request);request.mockReset();saved.mockClear();sheet.closes=0;});
afterEach(()=>vi.unstubAllGlobals());

test('publishes exact reference, keeps request across lost replies and preserves draft on reopening',async()=>{
  const item=entry('p'+'a'.repeat(32));
  organizeMaterial(item,item.day!,()=>true,saved);
  const input=sheet.content.querySelector('input')!;input.value='Candidate models';fire(input,'input');
  organizeMaterial(item,item.day!,()=>true,saved);
  expect(sheet.content.querySelector('input')!.value).toBe('Candidate models');
  request.mockRejectedValueOnce(new Error('network lost')).mockResolvedValue(response(item.id));
  fire(sheet.content,'submit');await settle();
  expect(sheet.content.textContent).toContain('network lost');expect(saved).not.toHaveBeenCalled();
  fire(sheet.content,'submit');await settle();
  const body=JSON.parse(String(request.mock.calls[0][1]?.body));
  expect(body.reference).toEqual({id:item.id,session:item.day,revision:2,digest:'a'.repeat(64)});
  expect(body.role).toBe('Candidate models');
  expect(request.mock.calls[1][1]?.body).toBe(request.mock.calls[0][1]?.body);
  expect(saved).toHaveBeenCalledTimes(1);expect(sheet.closes).toBe(1);
});

test('refuses changed Project before a request and never treats a foreign result as success',async()=>{
  const item=entry('p'+'b'.repeat(32));let current=false;
  organizeMaterial(item,item.day!,()=>current,saved);
  fire(sheet.content,'submit');await settle();expect(request).not.toHaveBeenCalled();
  current=true;request.mockResolvedValue(response(item.id,'other'));
  fire(sheet.content,'submit');await settle();expect(saved).not.toHaveBeenCalled();
  expect(sheet.content.textContent).toContain('did not match');
});

test('a late save preserves and rebases a newer draft from the reopened sheet',async()=>{
  const item=entry('p'+'d'.repeat(32));let finish!:(value:Response)=>void;
  request.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;}));
  organizeMaterial(item,item.day!,()=>true,saved);
  const first=sheet.content;
  first.querySelector('input')!.value='Earlier group';fire(first.querySelector('input')!,'input');
  fire(first,'submit');await settle();
  organizeMaterial(item,item.day!,()=>true,saved);
  sheet.content.querySelector('input')!.value='Newer group';fire(sheet.content.querySelector('input')!,'input');
  finish(response(item.id));await settle();
  organizeMaterial(item,item.day!,()=>true,saved);
  expect(sheet.content.querySelector('input')!.value).toBe('Newer group');
  request.mockRejectedValueOnce(new Error('inspect request'));
  fire(sheet.content,'submit');await settle();
  const second=JSON.parse(String(request.mock.calls[1][1]?.body));
  expect(second.role).toBe('Newer group');expect(second.expectedRevision).toBe(1);
});

test('an external change can be reviewed after conflict without losing typed choices',async()=>{
  const item=entry('p'+'e'.repeat(32));
  organizeMaterial(item,item.day!,()=>true,saved);
  sheet.content.querySelector('input')!.value='My protocol group';fire(sheet.content.querySelector('input')!,'input');
  request.mockResolvedValueOnce(new Response(JSON.stringify({ok:false,says:'Material organization changed. Refresh before saving your choices.'}),{status:409}));
  fire(sheet.content,'submit');await settle();expect(saved).not.toHaveBeenCalled();
  const refreshed={...item,material:{...item.material!,organization:{role:'Collaborator group',input:true,by:'agent:reviewer',revision:3}}};
  organizeMaterial(refreshed,item.day!,()=>true,saved);
  expect(sheet.content.querySelector('input')!.value).toBe('My protocol group');
  expect(sheet.content.textContent).toContain('Collaborator group');
  expect(sheet.content.textContent).toContain('review it before saving');
  request.mockResolvedValueOnce(response(item.id,item.day!,4));
  fire(sheet.content,'submit');await settle();
  expect(JSON.parse(String(request.mock.calls[1][1]?.body)).expectedRevision).toBe(3);
  expect(saved).toHaveBeenCalledTimes(1);
});
