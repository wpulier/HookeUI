import {expect, test} from 'vitest';
import {assistantBlockReason} from './assistant-readiness';
import type {Role} from './protocol';
const vision = (available: boolean): Role => ({id:'vision',kind:'model',title:'Seeing',purpose:'Answer',current:'chosen',options:[{id:'chosen',label:'Selected model',note:'',available,why:'Needs configured credentials'}],powers:[],agentCanChange:[]});
test('unknown, disconnected and pending assistants refuse before a message is sent', () => {
  expect(assistantBlockReason(null,true,false)).toContain('Checking');
  expect(assistantBlockReason({roles:[vision(true)]},false,false)).toContain('Reconnect');
  expect(assistantBlockReason({roles:[vision(true)]},true,true)).toContain('previous question');
});
test('selected model readiness is authoritative, not a connected agent or another available model', () => {
  const role=vision(false);role.options.push({id:'other',label:'Other model',note:'',available:true});
  expect(assistantBlockReason({roles:[role]},true,false)).toBe('Selected model: Needs configured credentials.');
  role.options[0].readiness={status:'unavailable',word:'Unavailable in this edition',group:'Unavailable here',next:null,steps:[]};
  expect(assistantBlockReason({roles:[role]},true,false)).toBe('Selected model: Unavailable in this edition.');
  expect(assistantBlockReason({roles:[vision(true)]},true,false)).toBeNull();
});
