// Synthetic DOM/receipts only; actual native view exports need separate evidence.
import {afterEach,beforeEach,expect,test,vi} from 'vitest';
import {fire,settleMicrotasks,syntheticDOM,TestElement} from './workspace-windows.test-support';
import {scientificViewExport} from './scientific-view-export';
const source={id:'p'+'a'.repeat(32),day:'2026-10-18',version:3};
let root:TestElement,dispose:()=>void;
const exporting=vi.fn(),listing=vi.fn(),restore=vi.fn();
const button=(name:string)=>root.querySelectorAll('button').find(el=>el.textContent===name)!;
const settle=async()=>{for(let i=0;i<10;i++)await settleMicrotasks();};
beforeEach(()=>{vi.stubGlobal('document',syntheticDOM().document);exporting.mockReset();listing.mockReset();restore.mockReset();const element=document.createElement('section');root=element as unknown as TestElement;dispose=scientificViewExport(element,source,{export:exporting,list:listing,restore}).dispose;});
afterEach(()=>{dispose();vi.unstubAllGlobals();});
test('explicit region never falls back to current view and foreign acceptance cannot say saved',async()=>{
  expect(exporting).not.toHaveBeenCalled();
  const select=root.querySelector('select')!;select.value='selected-region';fire(select,'change');
  const inputs=root.querySelectorAll('input');inputs[0].value='2';inputs[1].value='3';inputs[2].value='4';inputs[3].value='5';
  exporting.mockResolvedValue({ok:true,project:{id:'p'+'b'.repeat(32),session:'wrong',revision:1}});
  fire(button('Export view PNG'),'click');await settle();
  expect(exporting.mock.calls[0][0]).toEqual({scope:'selected-region',region:{x:2,y:3,width:4,height:5}});
  expect(root.textContent).toContain('receipt differs');expect(root.querySelector('a')).toBeNull();
});
test('pending upstream fix does not offer or execute a fake corrected export',async()=>{
  listing.mockResolvedValue({items:[{request:'retained-request-1234',scope:'Current view',at:'2026-10-23',ok:true,problems:[],affected:[{issue:'synthetic fixture',fixedIn:''}]}]});
  fire(button('Previous view exports'),'click');await settle();
  expect(root.textContent).toContain('compatible build pending');
  expect(button('Re-export with fixed renderer')).toBeUndefined();expect(exporting).not.toHaveBeenCalled();expect(restore).not.toHaveBeenCalled();
});
