import {afterEach, expect, test, vi} from 'vitest';
import {capabilityReport, modelReport, reportPicture} from './capability-report';
import type {CapabilitiesWire, Capability} from './protocol';
const capability: Capability = {id:'zoom',actor:'agent',family:'view',title:'Zoom',what:'A closer look',how:{},available:true,why:'',verdict:null};
const wire: CapabilitiesWire = {type:'capabilities',families:[],actors:{you:'You',agent:'Agent',auto:'Automatic',researcher:'Researcher'},
  capabilities:[capability],roles:[{id:'vision',kind:'model',title:'Vision',purpose:'Seeing',current:'gemini-test',options:[],powers:[],agentCanChange:[]}],
  preferences:[],pending:0,build:'test-revision',agentText:'Private text must not be exported',setup:{edition:'commercial',options:{apiKey:'MUST-NOT-EXPORT'}}};
afterEach(()=>vi.unstubAllGlobals());
test('model report preserves the selected option without inventing a capability verdict or exporting private setup',()=>{
  const report=modelReport(wire,wire.roles[0],{id:'alternative',label:'Alternative model',available:false,why:'Not installed',note:'No measured result',
    sources:[{title:'Declared paper',url:'https://example.org/paper',licence:'MIT'}]},'Failed or stopped','Could not load',null);
  expect(report.model).toMatchObject({role:'vision',id:'alternative',selected:false,available:false,licence:null,tier:null});
  expect(report.model.sources).toEqual([{title:'Declared paper',url:'https://example.org/paper',licence:'MIT'}]);
  expect(report).not.toHaveProperty('capability'); expect(report).not.toHaveProperty('verdictNote');
  expect(report.picture).toBeNull(); expect(report.request).toBeNull();
  expect(JSON.stringify(report)).not.toContain('MUST-NOT-EXPORT');
});
test('local export allowlists context, keeps picture off, and distinguishes unknown historical evidence',()=>{
  const report=capabilityReport(wire,capability,'Too slow',' waited ',null);
  expect(report.picture).toBeNull();expect(report.request).toBeNull();expect(report.missingEvidence[0]).toContain('not inferred');
  expect(report.setup).toEqual({edition:'commercial',models:{vision:'gemini-test'}});
  expect(JSON.stringify(report)).not.toContain('MUST-NOT-EXPORT');expect(JSON.stringify(report)).not.toContain('Private text');
  expect(report.note).toBe('waited');expect(report.verdictNote).toBe('Too slow: waited');
  expect(capabilityReport(wire,capability,'Too slow','x'.repeat(3000),null).verdictNote).toHaveLength(300);
});
test('optional picture reads only existing rendered canvas and bounds dimensions without opening recording channels',()=>{
  const draw=vi.fn();const encoded={width:0,height:0,getContext:()=>({drawImage:draw}),toDataURL:()=> 'data:image/jpeg;base64,local-pixels'};
  const create=vi.fn(()=>encoded);vi.stubGlobal('document',{createElement:create});
  const source={width:3200,height:1800} as HTMLCanvasElement;
  const result=reportPicture(source);
  expect(create).toHaveBeenCalledWith('canvas');expect(encoded.width).toBe(1280);expect(encoded.height).toBe(720);
  expect(draw).toHaveBeenCalledWith(source,0,0,1280,720);expect(result.description).toContain('not a historical request recording');
  expect(()=>reportPicture(null)).toThrow('No rendered');
});
