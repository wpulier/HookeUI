import { afterEach, expect, test, vi } from 'vitest';
import { renderResearchReport } from './research-report';
import { fire, syntheticDOM, TestElement } from './workspace-windows.test-support';
import type { ResearchSession } from './protocol';

afterEach(() => vi.unstubAllGlobals());
test('standard findings retain full text, table values and failed evidence checks', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const result = { title: 'Measured comparison', summary: 'One sequence failed',
    findingRevision: { version: 2, digest: 'retained', complete: true },
    report: { methods: 'App measurements\nSame captured field', observations: 'Retained observations',
      measurements: 'Values below', limitations: 'One failed acquisition' },
    tables: [{ title: 'Every sequence', columns: ['Sequence', 'Speed (µm/s)'], rows: [['1', '4.3'], ['2', 'Failed']] }],
    session_claims: [{ point: 'Unverified count', found: false, evidence: ['e3'] }],
    claims: [{ point: 'Unverified source point', source: 'https://example.org', found: false }],
  } as NonNullable<ResearchSession['result']>;
  const rendered = renderResearchReport(result) as unknown as TestElement;
  expect(rendered.textContent).toContain('App measurements\nSame captured field');
  expect(rendered.querySelector('table')!.textContent).toContain('4.3');
  expect(rendered.textContent).toContain('Unverified count');
  expect(rendered.textContent).toContain('Unverified source point');
  expect(rendered.querySelector('[data-anchor="report:measurements"]')).not.toBeNull();
});

test('legacy summaries are explicitly incomplete and never gain invented charts', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const rendered = renderResearchReport({ summary: 'A retained legacy summary' } as NonNullable<ResearchSession['result']>) as unknown as TestElement;
  expect(rendered.textContent).toContain('full findings record was not retained');
  expect(rendered.querySelector('svg')).toBeNull();
});

test('chart selection retains original values, denominator and exact source table', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const select = vi.fn();
  const result = { summary:'Retained measurements', charts:[{title:'Counts', transformation:'identity', selectionRule:'Highlight only',
    rows:[{label:'Capture A', value:7, unit:'cells', denominator:9, exclusions:['edge-cut'],
      source:{day:'2026-10-10', id:'e1', version:2, contentRevision:'fixed', valuePath:'/detail/values/count'}}]}]
  } as NonNullable<ResearchSession['result']>;
  const before = JSON.stringify(result);
  const rendered = renderResearchReport(result, select) as unknown as TestElement;
  const table = rendered.querySelector('table')!;
  expect(table.textContent).toContain('7 cells');
  expect(table.textContent).toContain('9');
  expect(table.textContent).toContain('edge-cut');
  fire(rendered.querySelector('.research-chart-point')!, 'click');
  expect(select).toHaveBeenCalledWith('chart:0:point:0');
  expect(JSON.stringify(result)).toBe(before);
  expect(table.textContent).toContain('fixed');
});

test('captured sequences retain original frame identities and open the chosen frame only', () => {
  vi.stubGlobal('document', syntheticDOM().document);
  const open = vi.fn(), select = vi.fn();
  const result = {summary:'Retained sequence',captures:[{title:'Held view',kind:'clip',state:'held',summary:'Two original frames',at:123,
    source:{day:'2026-10-10',id:'clip1',version:null,contentRevision:'original-digest'},
    frames:[{picture:0,frame:41,seq:901,boot:'original',at:123},{picture:1,frame:42,seq:902,boot:'original',at:123.5}]},
    {title:'Unavailable field',kind:'clip',state:'released',summary:'Pixels released; no replacement capture',at:124,
    source:{day:'2026-10-10',id:'clip2',version:null,contentRevision:'released-digest'},frames:[]}]
  } as NonNullable<ResearchSession['result']>;
  const before = JSON.stringify(result);
  const rendered = renderResearchReport(result,select,open) as unknown as TestElement;
  expect(rendered.textContent).toContain('Two original frames');
  expect(rendered.textContent).toContain('Captured');
  expect(rendered.textContent).toContain('902');
  expect(rendered.textContent).toContain('Pixels released; no replacement capture');
  const buttons = rendered.querySelectorAll('[data-capture-open]');
  expect(buttons).toHaveLength(1);
  const frames = rendered.querySelector('select')!; frames.value = '1';
  fire(buttons[0], 'click');
  expect(open).toHaveBeenCalledWith(0,1);
  fire(rendered.querySelector('[data-capture-select]')!, 'click');
  expect(select).toHaveBeenCalledWith('capture:0');
  expect(JSON.stringify(result)).toBe(before);
});
