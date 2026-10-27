import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import cases from '../../../contract/scientific-labels-appearance-cases.json';
import histograms from '../../../contract/scientific-image-histogram-cases.json';
import frames from '../../../contract/scientific-view-cases.json';
import views from '../../../contract/scientific-view-preferences-cases.json';
import {labelsAppearance,parseDirectColors,directColorsText} from './scientific-labels-appearance';
import {imageHistogram,contrastAction} from './scientific-image-histogram';
import {scientificFrame} from './scientific-view';
import {rememberScientificFrame,scientificViewPreferences} from './scientific-view-preferences';
import {ScientificNavigation,type ViewerChange} from './scientific-navigation';
import {inspectionReply} from './scientific-inspection';
import {TestElement,fire,settleMicrotasks,syntheticDOM} from './workspace-windows.test-support';

type Case={name:string;patch?:Record<string,unknown>;remove?:string};
const changed=(valid:Record<string,unknown>,item:Case)=>{const value={...structuredClone(valid),...structuredClone(item.patch??{})};if(item.remove)delete value[item.remove];return value;};
const labelsFrame=(appearance:unknown,remembered=true)=>{
  const raw=structuredClone(frames.valid) as Record<string,any>;
  raw.layers[0]={...raw.layers[0],type:'labels',labelsAppearance:appearance,labelsSelectedColor:[0.5,0.25,1,1],...(remembered?{viewKey:'raw',viewGeometry:views.valid.layers[0].geometry}:{})};
  return raw;
};

test.each(cases.otherValid as Case[])('labels display accepts $name',item=>{
  const value=changed(cases.valid,item);
  expect(labelsAppearance(value)).toEqual(value);
  expect(scientificFrame(labelsFrame(value,false)).layers[0].labelsAppearance).toEqual(value);
});
test.each(cases.invalid as Case[])('labels display refuses $name before rendering or dispatch',item=>{
  const value=changed(cases.valid,item);
  expect(()=>labelsAppearance(value)).toThrow();
  expect(()=>scientificFrame(labelsFrame(value,false))).toThrow();
});
test('labels display is remembered with schema 14; Python-set automatic colors are not',()=>{
  const saved=rememberScientificFrame(scientificFrame(labelsFrame(cases.valid)));
  expect(saved.schema).toBe(14);expect(saved.layers[0].labelsAppearance).toEqual(cases.valid);
  expect(()=>scientificViewPreferences({...saved,schema:13})).toThrow();
  const other={...cases.valid,colorMode:'auto',autoSource:'other',autoSeed:null};
  expect(()=>rememberScientificFrame(scientificFrame(labelsFrame(other)))).toThrow(/changed in Python/);
  const image=structuredClone(labelsFrame(cases.valid,false));image.layers[0].type='image';
  expect(()=>scientificFrame(image)).toThrow();
});
test('direct colors round-trip through their editable text without rounding identifiers',()=>{
  const colors=cases.valid.directColors as {label:string|null;color:[number,number,number,number]}[];
  expect(parseDirectColors(directColorsText(colors))).toEqual(colors);
  expect(parseDirectColors('\nother: 0 0 0 1\n18446744073709551615: 0.5 0.25 0 1\n')).toEqual([{label:null,color:[0,0,0,1]},{label:'18446744073709551615',color:[.5,.25,0,1]}]);
  expect(()=>parseDirectColors('7: 1 0 0')).toThrow(/Line 1/);
  expect(()=>parseDirectColors('7: 1 0 0 2')).toThrow(/Line 1/);
  expect(()=>parseDirectColors('7.5: 1 0 0 1')).toThrow(/Line 1/);
});
test.each(histograms.otherValid as Case[])('histogram accepts $name',item=>{
  const value=changed(histograms.valid,item);expect(imageHistogram(value)).toEqual(value);
  const raw=structuredClone(frames.valid) as Record<string,any>;raw.layers[0].imageHistogram=value;
  expect(scientificFrame(raw).layers[0].imageHistogram).toEqual(value);
});
test.each(histograms.invalid as Case[])('histogram refuses $name',item=>{
  const value=changed(histograms.valid,item);expect(()=>imageHistogram(value)).toThrow();
  const raw=structuredClone(frames.valid) as Record<string,any>;raw.layers[0].imageHistogram=value;
  expect(()=>scientificFrame(raw)).toThrow();
});
test('contrast actions match the worker contract',()=>{
  for(const value of histograms.contrast.valid)expect(contrastAction(value)).toEqual(value);
  for(const value of histograms.contrast.invalid)expect(()=>contrastAction(value)).toThrow();
});

test('a Labels sample carries its native feature row with exact identifiers',()=>{
  const request={op:'inspect' as const,frame:'a'.repeat(64),layer:'b'.repeat(32),position:[10,20] as [number,number]};
  const sample={frame:request.frame,layer:request.layer,world:[10,20],data:[1,2],axes:['y','x'],units:['pixel','pixel'],level:null,projection:'none',dtype:'<u8',
    values:['18446744073709551614'],labelFeatures:{row:3,features:[{name:'index',dtype:'uint64',kind:'integer',value:'18446744073709551614'},{name:'area',dtype:'float64',kind:'missing',value:null}]}};
  expect(inspectionReply(sample,request)).toEqual(sample);
  expect((inspectionReply({...sample,labelFeatures:{row:null,features:[]}},request) as {labelFeatures?:unknown}).labelFeatures).toEqual({row:null,features:[]});
  for(const bad of [{labelFeatures:{row:null,features:sample.labelFeatures.features}},{labelFeatures:{row:-1,features:[]}},{values:['1.5']},{values:null},
    {labelFeatures:{row:1,features:[{name:'id',dtype:'uint64',kind:'integer',value:2**53}]}}])
    expect(()=>inspectionReply({...sample,...bad},request)).toThrow();
});

let root:TestElement;
beforeEach(()=>{const dom=syntheticDOM();root=dom.host;vi.stubGlobal('document',dom.document);vi.stubGlobal('window',new EventTarget());});
afterEach(()=>vi.unstubAllGlobals());
const control=(name:string)=>[...root.querySelectorAll('input'),...root.querySelectorAll('select'),...root.querySelectorAll('textarea')].find(row=>row.getAttribute('aria-label')===name)!;
const button=(name:string)=>root.querySelectorAll('button').find(row=>row.textContent===name)!;
function open(layer:Record<string,unknown>){
  const raw=structuredClone(frames.valid) as Record<string,any>;raw.layers[0]={...raw.layers[0],...layer};
  const frame=scientificFrame(raw);
  const send=vi.fn(async(_:string,__:ViewerChange)=>{const next=structuredClone(frame);next.frame='c'.repeat(64);controls.update(next);return next;});
  const controls=new ScientificNavigation(root as unknown as HTMLElement,{drawn:async()=>{},send,refresh:async()=>{}});
  controls.bindSession('one');controls.update(frame);controls.setAvailable(true);
  return {controls,frame,send};
}
test('labels display sends exact decimal identifiers and keeps an invalid draft editable',async()=>{
  const settings={...cases.valid,colorMode:'auto',autoSource:'original',autoSeed:null};
  const {frame,send}=open({type:'labels',name:'Cells',labelsAppearance:settings,labelsSelectedColor:null});
  const contour=control('Contour width Cells'),selected=control('Selected label Cells');
  contour.value='-2';fire(contour,'input');fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).not.toHaveBeenCalled();expect(root.textContent).toContain('zero or more');expect(contour.value).toBe('-2');
  contour.value='3';fire(contour,'input');selected.value='18446744073709551614';fire(selected,'input');
  const colors=control('Label colors Cells');colors.value='seeded';fire(colors,'change');
  const seed=control('Color seed Cells');seed.value='77';fire(seed,'input');
  fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'labels-presentation',value:{layer:frame.layers[0].id,settings:{...settings,contour:3,selectedLabel:'18446744073709551614',autoSource:'seeded',autoSeed:77}}});
});
test('direct colors are edited as text and sent as a complete table',async()=>{
  const {frame,send}=open({type:'labels',name:'Cells',labelsAppearance:cases.valid,labelsSelectedColor:[0,1,0,1]});
  expect(control('Label colors Cells').value).toBe('direct');
  const table=control('Direct colors Cells');
  table.value='other: 0 0 0 1\n5: 0 1 0 1';fire(table,'input');
  fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'labels-presentation',value:{layer:frame.layers[0].id,settings:{...cases.valid,autoSource:'original',autoSeed:null,
    directColors:[{label:null,color:[0,0,0,1]},{label:'5',color:[0,1,0,1]}]}}});
});
test('the selected label color shows napari get_color, transparent for the background',()=>{
  const {controls,frame}=open({type:'labels',name:'Cells',labelsAppearance:cases.valid,labelsSelectedColor:[0,1,0,1]});
  const swatch=()=>root.querySelectorAll('span').find(e=>e.getAttribute('aria-label')?.startsWith('Selected label color Cells'))!;
  expect(swatch().getAttribute('aria-label')).toBe('Selected label color Cells: red 0, green 1, blue 0, alpha 1');
  controls.update({...frame,frame:'e'.repeat(64),layers:[{...frame.layers[0],labelsSelectedColor:null}]});
  expect(swatch().getAttribute('aria-label')).toBe('Selected label color Cells: background (transparent)');
});
test('Full range is offered for integer images only, as napari’s popup',async()=>{
  const {controls,frame,send}=open({imageHistogram:histograms.valid});
  const full=()=>root.querySelectorAll('button').find(b=>b.getAttribute('aria-label')==='Full contrast range Intensity')!;
  expect(full().hidden).toBe(true);
  controls.update({...frame,frame:'e'.repeat(64),layers:[{...frame.layers[0],imageHistogram:imageHistogram({...histograms.valid,integer:true})}]});
  expect(full().hidden).toBe(false);
  fire(button('Autoscale range to data'),'click');await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith('e'.repeat(64),{operation:'image-contrast',value:{layer:frame.layers[0].id,settings:{action:'autoscale-range'}}});
});
test('both ends of the contrast range are one draft',async()=>{
  const {frame,send}=open({imageHistogram:histograms.valid});
  const low=control('Contrast range minimum Intensity'),high=control('Contrast range maximum Intensity');
  low.value='0';fire(low,'input');high.value='40000';fire(high,'input');
  expect(high.disabled).toBe(false);
  fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(frame.frame,{operation:'image-contrast',value:{layer:frame.layers[0].id,settings:{action:'set-range',range:[0,40000]}}});
});
test('C applies auto-contrast once to the active image layer only',async()=>{
  const {controls,frame,send}=open({imageHistogram:histograms.valid});
  controls.autoContrastOnce();await settleMicrotasks();
  expect(send).not.toHaveBeenCalled();expect(root.textContent).toContain('press C');
  const active={...frame,frame:'d'.repeat(64),layerSelection:{selected:[frame.layers[0].id],active:frame.layers[0].id}};
  controls.update(active);controls.autoContrastOnce();await settleMicrotasks();
  expect(send).toHaveBeenCalledWith(active.frame,{operation:'image-contrast',value:{layer:frame.layers[0].id,settings:{action:'auto-once'}}});
});
test('histogram settings and contrast actions use the current frame',async()=>{
  const {frame,send}=open({imageHistogram:histograms.valid});
  expect(root.textContent).toContain('4 bins from 0 to 100000000000000000');
  const bins=control('Histogram bins Intensity');bins.value='0';fire(bins,'input');
  fire(button('Apply view changes'),'click');await settleMicrotasks();expect(send).not.toHaveBeenCalled();
  bins.value='128';fire(bins,'input');(control('Logarithmic counts Intensity') as unknown as HTMLInputElement).checked=true;fire(control('Logarithmic counts Intensity'),'input');
  fire(button('Apply view changes'),'click');await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith(frame.frame,{operation:'image-histogram',value:{layer:frame.layers[0].id,settings:{enabled:true,bins:128,mode:'full',logScale:true,maxSamples:1000000}}});
  fire(button('Auto-contrast once'),'click');await settleMicrotasks();
  expect(send).toHaveBeenLastCalledWith('c'.repeat(64),{operation:'image-contrast',value:{layer:frame.layers[0].id,settings:{action:'auto-once'}}});
});
