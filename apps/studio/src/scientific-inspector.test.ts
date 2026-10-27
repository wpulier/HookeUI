// Synthetic navigation regression; native dock visibility is checked separately.
import { afterEach, expect, test, vi } from 'vitest';
import { syntheticDOM } from './workspace-windows.test-support';
import { ScientificInspector } from './scientific-inspector';
afterEach(()=>vi.unstubAllGlobals());
test('section changes reveal entry controls without remounting drafts or scrolling other docks',()=>{
  vi.stubGlobal('document',syntheticDOM().document);
  const dock=document.createElement('div'),other=document.createElement('div'),root=document.createElement('div');
  dock.className=other.className='capability-controls-body';dock.append(root);
  const edit=document.createElement('input'),tool=document.createElement('button');edit.value='unsaved draft';
  const inspector=new ScientificInspector(root,[{name:'Edit',content:edit},{name:'Tool',content:tool}],()=>{});
  dock.scrollTop=600;other.scrollTop=400;
  inspector.show('Tool');expect(dock.scrollTop).toBe(0);expect(other.scrollTop).toBe(400);
  dock.scrollTop=200;inspector.show('Tool');expect(dock.scrollTop).toBe(200);
  inspector.show('Edit');expect(dock.scrollTop).toBe(0);expect(edit.value).toBe('unsaved draft');expect(root.contains(edit)).toBe(true);
});
