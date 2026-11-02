// Synthetic event regression; pointer navigation is also checked in the real preview.
import {afterEach,expect,test,vi} from 'vitest';
import {ProjectNavigation} from './project-navigation';
import {TestElement,syntheticDOM,fire} from './workspace-windows.test-support';
afterEach(()=>vi.unstubAllGlobals());
test('a pointer blur without a new focus target leaves the destination available for click',()=>{
  vi.stubGlobal('document',syntheticDOM().document);
  const choose=vi.fn(),nav=new ProjectNavigation(choose),root=nav.root as unknown as TestElement;
  const menu=root.querySelector('details') as unknown as HTMLDetailsElement;
  menu.open=true;fire(root,'focusout',{relatedTarget:null});expect(menu.open).toBe(true);
  const inputs=root.querySelectorAll('button').find(b=>b.textContent==='Inputs')!;
  fire(inputs,'click');expect(choose).toHaveBeenCalledWith('inputs');expect(menu.open).toBe(false);
  menu.open=true;fire(root,'focusout',{relatedTarget:new TestElement('button')});expect(menu.open).toBe(false);
});
