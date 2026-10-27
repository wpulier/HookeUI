// Synthetic DOM regression: scientific revision updates, not browser layout evidence.
import { afterEach, expect, test, vi } from 'vitest';
import { ResultViewer, fittedResultSize } from './viewer';
import { syntheticDOM, TestElement } from './workspace-windows.test-support';
import type { ResearchSession } from './protocol';

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

function setup() {
  const dom = syntheticDOM();
  const elements = new Map(['viewer', 'viewer-title', 'viewer-from', 'viewer-tabs', 'viewer-body', 'side', 'viewer-close']
    .map(id => [id, new TestElement('div')]));
  const head = new TestElement('header'); head.className = 'viewer-head';
  head.append(elements.get('viewer-title')!, elements.get('viewer-from')!, elements.get('viewer-close')!);
  elements.get('viewer')!.append(head, elements.get('viewer-tabs')!, elements.get('viewer-body')!);
  vi.stubGlobal('document', Object.assign(dom.document, { getElementById: (id: string) => elements.get(id) }));
  vi.stubGlobal('HTMLElement', TestElement);
  vi.stubGlobal('HTMLVideoElement', class Video extends TestElement {});
  vi.stubGlobal('matchMedia', () => ({ matches: true }));
  vi.useFakeTimers();
  vi.stubGlobal('window', {setTimeout, clearTimeout, addEventListener:vi.fn()});
  return { elements, dom };
}

test('reopening renders corrected findings even when the summary stays the same', () => {
  const { elements } = setup();
  const session = { rid: 'r1', name: 'Researcher', brief: 'Study', outputs: [],
    items: [{ kind: 'summary', label: 'Findings' }], result: { title: 'Study', summary: 'Same preview',
      findingRevision: { version: 1, digest: 'one', complete: true },
      report: { methods: 'Observed', observations: 'First observation', measurements: '3 cells', limitations: 'Limited field' } } } as unknown as ResearchSession;
  const viewer = new ResultViewer(() => {}, () => new Map([['r1', session]]));
  viewer.apply({ type: 'presenter', open: true, rid: 'r1', index: 0 });
  expect(elements.get('viewer-body')!.textContent).toContain('3 cells');
  viewer.close();
  session.result = { ...session.result!, findingRevision: { version: 2, digest: 'two', complete: true },
    report: { ...session.result!.report!, measurements: '4 cells' } };
  viewer.apply({ type: 'presenter', open: true, rid: 'r1', index: 0 });
  expect(elements.get('viewer-body')!.textContent).toContain('4 cells');
  expect(elements.get('viewer-body')!.textContent).toContain('revision 2');
  expect(elements.get('viewer-body')!.textContent).not.toContain('3 cells');
});


test('selected result identity survives loading while displayed readiness does not', () => {
  const { elements } = setup();
  const work = { researcherId: 'r1', resource: { day: '2026-10-10', id: 'result1', version: 1 } };
  const session = { rid: 'r1', name: 'Researcher', brief: 'Study', outputs: [],
    items: [{ kind: 'summary', label: 'Findings', work }], result: { summary: 'Retained findings' } } as unknown as ResearchSession;
  const viewer = new ResultViewer(() => {}, () => new Map([['r1', session]]));
  viewer.apply({ type: 'presenter', open: true, rid: 'r1', index: 0 });
  const image = { complete: false, naturalWidth: 0 };
  vi.spyOn(elements.get('viewer-body')!, 'querySelector').mockImplementation((selector: string) =>
    selector === 'img' ? image as unknown as TestElement : null);
  expect(viewer.selectedWork()).toEqual(work);
  expect(viewer.displayedWork()).toBeUndefined();
  image.complete = true; image.naturalWidth = 640;
  expect(viewer.selectedWork()).toEqual(work);
  expect(viewer.displayedWork()).toEqual(work);
  viewer.close();
  expect(viewer.selectedWork()).toBeUndefined();
  expect(viewer.displayedWork()).toBeUndefined();
});


test('distinct non-output selections replace retained content and selected identity', () => {
  const { elements } = setup();
  const one = { researcherId: 'r1', resource: { day: '2026-10-10', id: 'r1', version: 1, picture: 0 } };
  const two = { researcherId: 'r1', resource: { day: '2026-10-10', id: 'r1', version: 1, picture: 1 } };
  const session = { rid: 'r1', name: 'Researcher', brief: 'Study', outputs: [],
    items: [{ kind: 'reference', ref: 0, label: 'First', work: one }, { kind: 'reference', ref: 1, label: 'Second', work: two }],
    result: { summary: 'Retained findings', refs:[{url:'/first.jpg',page:'https://example.org/first',title:'First'},{url:'/second.jpg',page:'https://example.org/second',title:'Second'}] } } as unknown as ResearchSession;
  const viewer = new ResultViewer(() => {}, () => new Map([['r1', session]]));
  viewer.apply({ type: 'presenter', open: true, rid: 'r1', index: 0 });
  const first = elements.get('viewer-body')!.children[0];
  viewer.apply({ type: 'presenter', open: true, rid: 'r1', index: 1 });
  expect(viewer.selectedWork()).toEqual(two);
  expect(elements.get('viewer-body')!.querySelector('img')).toHaveProperty('src','/second.jpg');
  expect(elements.get('viewer-body')!.children[0]).not.toBe(first);
});

test('new tabs and mutable current flag update without remounting an immutable output', () => {
  const { elements } = setup();
  vi.stubGlobal('fetch', async () => ({ ok: true, json: async () => ({findingRevision:{digest:'finding'}, presentation:{id:'p1'}}) }));
  const output = {id:'p1',kind:'presentation',url:'/saved/p1',version:1,current:true,caption:'Original',findingRevision:{version:1,digest:'finding'}};
  const session = {rid:'r1', day:'2026-10-10', name:'Researcher', brief:'Study', outputs:[output],
    items:[{kind:'presentation',id:'p1',label:'Original'}], result:{summary:'Finding'}} as unknown as ResearchSession;
  const viewer = new ResultViewer(() => {}, () => new Map([['r1',session]]));
  viewer.apply({type:'presenter',open:true,rid:'r1',index:0});
  const retained = elements.get('viewer-body')!.children[0];
  output.current = false;
  session.items.push({kind:'summary',id:null,label:'Findings'});
  viewer.render();
  expect(elements.get('viewer-body')!.children[0]).toBe(retained);
  expect(elements.get('viewer-tabs')!.hidden).toBe(false);
  expect(elements.get('viewer-tabs')!.children).toHaveLength(2);
  expect(elements.get('viewer')!.textContent).toContain('an older version');
});


test('lesson caveats use exact historical findings outside generated HTML', async () => {
  const { elements } = setup();
  const basis = {version:1,digest:'original',complete:true};
  const historical = {summary:'Original observed summary',limitations:['Original uncertainty']};
  const fetched: string[] = [];
  vi.stubGlobal('fetch', async (url: string) => {
    fetched.push(url);
    return url.includes('/findings/')
      ? {ok:true,json:async () => ({version:1,digest:'original',result:historical})}
      : {ok:true,headers:{get:() => 'text/html'}};
  });
  const output = {id:'lesson-v1',kind:'html',url:'/research/2026-10-10/r1/lesson.html',version:1,caption:'Lesson',findingRevision:basis};
  const session = {rid:'r1',day:'2026-10-10',name:'Researcher',brief:'Study',outputs:[output],
    items:[{kind:'html',id:'lesson-v1',label:'Lesson'}],
    result:{summary:'NEW incompatible summary',limitations:['NEW caveat'],findingRevision:{version:2,digest:'new',complete:true}}} as unknown as ResearchSession;
  const viewer = new ResultViewer(() => {}, () => new Map([['r1',session]]));
  viewer.apply({type:'presenter',open:true,rid:'r1',index:0});
  for (let i=0;i<5;i++) await Promise.resolve();
  const body=elements.get('viewer-body')!;
  expect(body.textContent).toContain('Original uncertainty');
  expect(body.textContent).not.toContain('NEW caveat');
  expect(fetched).toContain('/api/research/r1/findings/1?day=2026-10-10');
  expect(body.querySelector('.artifact-evidence-context')?.contains(body.querySelector('iframe')!)).toBe(false);
});

test('lesson without a retained findings revision never borrows latest caveats', () => {
  const {elements}=setup();
  vi.stubGlobal('fetch', async () => ({ok:true,headers:{get:()=>'text/html'}}));
  const session={rid:'r1',day:'2026-10-10',name:'Researcher',brief:'Study',outputs:[{id:'legacy',kind:'html',url:'/research/legacy.html',caption:'Legacy'}],
    items:[{kind:'html',id:'legacy',label:'Legacy'}],result:{summary:'Unrelated latest summary'}} as unknown as ResearchSession;
  const viewer=new ResultViewer(()=>{},()=>new Map([['r1',session]]));
  viewer.apply({type:'presenter',open:true,rid:'r1',index:0});
  expect(elements.get('viewer-body')!.textContent).toContain('No findings revision was retained');
  expect(elements.get('viewer-body')!.textContent).not.toContain('Unrelated latest summary');
});


test('Fit preserves tall source aspect when height is the limiting dimension, including tabs', () => {
  const [width,height]=fittedResultSize(520,480,78,0.5);
  expect(height).toBe(480);
  expect(width).toBe(201);
  expect(width/(height-78)).toBe(0.5);
  const [phoneWidth,phoneHeight]=fittedResultSize(370,650,80,2);
  expect(phoneWidth).toBe(370);
  expect(phoneHeight).toBe(265);
});

test('saved-session tab selection submits the selected retained item, not its first output', () => {
  setup();
  const sent: unknown[] = [];
  const first = {researcherId:'r1',resource:{day:'2026-10-09',id:'r1',version:1},outputId:'lesson1'};
  const second = {researcherId:'r1',resource:{day:'2026-10-09',id:'r1',version:2},outputId:'lesson2'};
  const reference = {researcherId:'r1',resource:{day:'2026-10-09',id:'r1',version:2,picture:1}};
  const saved = {rid:'r1',day:'2026-10-09',outputs:[{id:'lesson1',version:1},{id:'lesson2',version:2}],
    items:[{kind:'html',id:'lesson1',label:'First',work:first},{kind:'html',id:'lesson2',label:'Second',work:second},
      {kind:'reference',id:null,ref:1,label:'Reference',work:reference}]} as unknown as ResearchSession;
  const viewer = new ResultViewer(command => sent.push(command), () => new Map());
  viewer.state = {type:'presenter',open:false,rid:'r1',index:0,savedSession:saved};
  viewer.show('r1',1);
  viewer.show('r1',2);
  expect(sent).toEqual([
    {type:'presenter',action:'show',rid:'r1',day:'2026-10-09',savedItem:second},
    {type:'presenter',action:'show',rid:'r1',day:'2026-10-09',savedItem:reference},
  ]);
});

test('a saved item without a retained address refuses instead of opening current work', () => {
  const {elements}=setup();
  const sent: unknown[]=[];
  const viewer=new ResultViewer(command=>sent.push(command),()=>new Map());
  viewer.state={type:'presenter',open:false,rid:'r1',index:0,
    savedSession:{rid:'r1',day:'2026-10-09',outputs:[],items:[{kind:'summary',id:null,label:'Old summary'}]} as unknown as ResearchSession};
  viewer.show('r1',0);
  expect(sent).toEqual([]);
  expect(elements.get('viewer-from')!.textContent).toContain('retained address');
});
