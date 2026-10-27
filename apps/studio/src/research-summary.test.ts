import { expect, test } from 'vitest';
import { holdsText, retainedFigure } from './research-summary';
import type { ResearchSession } from './protocol';
import { artifactReceipt, presentationTiming, spendingSummary } from './research-summary';

test('selected output receipts and recorded costs stay separate from outstanding estimates', () => {
  const session = {spent:0.1,spentEstimated:0.03,cap:1,presentationJobs:[
    {id:'old',output:'o1',cost:0.02,estimatedCost:0,state:'ready',created:100,dispatchedAt:101,finished:111,deadlineSeconds:120},
    {id:'new',output:'o2',cost:0.04,estimatedCost:0.05,state:'interrupted',created:200},
  ]} as ResearchSession;
  expect(artifactReceipt(session, {id:'o1',job:'old',change:'Larger labels'} as ResearchSession['outputs'][number])).toContain('Larger labels · $0.020 presentation');
  expect(artifactReceipt(session, {id:'legacy'} as ResearchSession['outputs'][number])).toBe('Time not recorded · Presentation cost not recorded');
  expect(presentationTiming(session.presentationJobs![0], 999)).toBe('10 s elapsed · 120 s production limit');
  expect(presentationTiming({...session.presentationJobs![0],finished:90}, 999)).toContain('Timing unavailable (clock changed)');
  expect(presentationTiming({...session.presentationJobs![0],finished:NaN}, 999)).toContain('Timing unavailable (clock changed)');
  expect(presentationTiming(session.presentationJobs![1], 999)).toBe('Elapsed time not recorded · Production limit not recorded');
  expect(spendingSummary(session)).toBe('$0.160 total recorded · $0.100 research · $0.060 presentations · $0.080 additional estimated, receipts pending');
});

test('card counts retained observations and current outputs, excluding superseded artifacts', () => {
  const session = {findings:[{},{}], findingCount:3, result:{sources:['a','b']}, outputs:[
    {kind:'html',current:false},{kind:'html',contentKind:'lesson',current:true},{kind:'image',current:true},
  ]} as unknown as ResearchSession;
  expect(holdsText(session)).toBe('3 observations, 2 sources, 1 picture, 1 lesson');
});

test('a card copies a labelled structured value and denominator without inventing a trend', () => {
  const result = {summary:'A prose claim of 500 cells', charts:[{title:'Cell count',transformation:'identity',
    rows:[{label:'Capture A',value:7,unit:'cells',denominator:9,source:{contentRevision:'exact-a'}},
          {label:'Capture B',value:3,unit:'cells',denominator:9,source:{contentRevision:'exact-b'}}]}]
  } as unknown as ResearchSession['result'];
  const before = JSON.stringify(result);
  expect(retainedFigure(result)).toBe('Cell count · Capture A: 7 cells · denominator 9 · 2 retained observations');
  expect(JSON.stringify(result)).toBe(before);
  expect(retainedFigure({summary:'Median speed 12.4 µm/s'} as ResearchSession['result'])).toBeNull();
  expect(retainedFigure({charts:[{...result!.charts![0],transformation:'invented'}]} as unknown as ResearchSession['result'])).toBeNull();
});


test('HTML format alone never classifies a retained output as a lesson', () => {
  expect(holdsText({findings:[],outputs:[{kind:'html'},{kind:'html',contentKind:'report'}]} as unknown as ResearchSession)).toBe('1 report, 1 interactive artifact');
});
