import {expect,it} from 'vitest';
import {artifactPresentation, checkedOverlay, undrawnWords} from './workspace-study-content';
import type {StudyRetainedView} from './workspace-study-restoration';
import type {PopulationWire} from './population';
const saved:StudyRetainedView={resource:{day:'session-a',id:'e14',version:0,picture:0},title:'Bright objects',zoom:1,pan:[.5,.5]};
it('opens segmentation with a source picture as masks, table artifacts as tables, and generated images as images',()=>{
  expect(artifactPresentation({kind:'dataset-table'})).toBe('datatable');
  expect(artifactPresentation({kind:'population'})).toBe('masks');
  expect(artifactPresentation({kind:'result-table'})).toBe('table');
  // Current main keeps generic capability tables as `result`, with an explicit index flag.
  expect(artifactPresentation({kind:'result',table:true})).toBe('table');
  expect(artifactPresentation({kind:'result',table:false})).toBe('native');
  expect(artifactPresentation({kind:'result'})).toBe('native');
});
it('never substitutes a source-only reply, another session or another revision for masks',()=>{
  const data={id:'e14',session:'session-a',revision:0,width:512,height:512,count:1,members:[{id:'c1',colour:0,polygons:[[[1,1],[8,1],[8,8]]]}]} as PopulationWire & {session:string;revision:number;width:number;height:number};
  expect(checkedOverlay(data,saved).members[0].polygons).toEqual([[[1,1],[8,1],[8,8]]]);
  expect(()=>checkedOverlay({...data,members:[]},saved)).toThrow('geometry');
  expect(()=>checkedOverlay({...data,session:'session-b'},saved)).toThrow('project and revision');
  expect(()=>checkedOverlay({...data,revision:1},saved)).toThrow('project and revision');
  expect(checkedOverlay({...data,count:0,members:[]},saved).count).toBe(0);
});
it('draws every outlined member and names the ones too small to outline, never refusing the whole overlay (#359)',()=>{
  const outlined={id:'c1',colour:0,polygons:[[[1,1],[8,1],[8,8]]]};
  const members=[outlined,{id:'c276',colour:1,polygons:[]},{id:'c289',colour:2,polygons:[]},{id:'c315',colour:3,polygons:[]}];
  const data={id:'e14',session:'session-a',revision:0,width:512,height:512,count:4,members} as PopulationWire & {session:string;revision:number;width:number;height:number};
  const checked=checkedOverlay(data,saved);
  expect(checked.members).toHaveLength(4);
  expect(checked.undrawn).toEqual(['c276','c289','c315']);
  expect(undrawnWords(checked.undrawn)).toBe(' · 3 too small to outline (c276, c289, c315); their measurements are kept');
  expect(undrawnWords([])).toBe('');
  expect(undrawnWords(['a','b','c','d','e','f','g'])).toBe(' · 7 too small to outline (a, b, c, d, e and 2 more); their measurements are kept');
  // A malformed outline is still refused: only an empty one is drawn around.
  expect(()=>checkedOverlay({...data,count:1,members:[{...outlined,polygons:[[[1,1],[2,2]]]}] as never},saved)).toThrow('missing or invalid');
  expect(()=>checkedOverlay({...data,count:1,members:[{...outlined,polygons:[[[1,1],[2,Number.NaN],[3,3]]]}] as never},saved)).toThrow('missing or invalid');
  expect(()=>checkedOverlay({...data,count:1,members:[{id:'c9',colour:0} as never]},saved)).toThrow('missing or invalid');
});
