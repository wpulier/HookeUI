// Synthetic dialog lifecycle/React boundary; real browser admission is separate evidence.
import {afterEach,expect,it,vi} from 'vitest';
import type {ReactElement,ComponentProps} from 'react';
import {TestElement,fire} from './workspace-windows.test-support';
import {openMaterialCapability,type ManualCapabilities} from './workflow-capabilities';
const renderer=vi.hoisted(()=>({render:vi.fn(),unmount:vi.fn()}));
vi.mock('react-dom/client',()=>({createRoot:()=>renderer}));
afterEach(()=>{vi.unstubAllGlobals();renderer.render.mockReset();renderer.unmount.mockReset();});
it('dismissal blocks a late admission reply before the queued close event runs',async()=>{
 const body=new TestElement('body');let dialog:TestElement&{open:boolean;close:()=>void;showModal:()=>void};
 vi.stubGlobal('document',{body,createElement:(tag:string)=>{
  const element=new TestElement(tag);
  if(tag==='dialog'){dialog=Object.assign(element,{open:false,showModal(){this.open=true;},close(){this.open=false;}});return dialog;}
  return element;
 }});
 const started=vi.fn();
 openMaterialCapability({material:{input:{session:'s',id:'p1',revision:1,digest:'a'},title:'Saved matrix'}},()=> 's',started);
 const props=(renderer.render.mock.calls[0][0] as ReactElement<ComponentProps<typeof ManualCapabilities>>).props;
 let resolve!:()=>void;const pending=new Promise<void>(r=>{resolve=r;}).then(()=>props.started({} as Parameters<typeof props.started>[0]));
 expect(props.currentSession()).toBe('s');
 fire(dialog!.querySelector('button')!,'click');
 expect(props.currentSession()).toBe('');
 resolve();await pending;expect(started).not.toHaveBeenCalled();
 expect(renderer.unmount).not.toHaveBeenCalled(); // close event has deliberately not run yet
 fire(dialog!,'close');expect(renderer.unmount).toHaveBeenCalledOnce();
});
