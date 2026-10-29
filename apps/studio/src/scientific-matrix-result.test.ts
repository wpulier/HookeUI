import {afterEach,expect,it,vi} from 'vitest';
import {syntheticDOM,TestElement,fire} from './workspace-windows.test-support';
import {showMatrixResult} from './scientific-matrix-result';
afterEach(()=>vi.unstubAllGlobals());
it('renders saved numerical evidence, exact export and same-Project input links without executing a model',async()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const root=new TestElement('section'),open=vi.fn().mockResolvedValue(undefined);
 const state={sourceFile:{format:1,name:'model.npz',size:42,sha256:'a'.repeat(64)},matrixOperation:{format:1,capability:'response-fit',
  output:{summary:{shape:[2,16],warning:'<script>untrusted</script>'}},inputs:{effects:{id:'input',session:'project',revision:3},foreign:{id:'other',session:'elsewhere',revision:1}}}};
 showMatrixResult(root as unknown as HTMLElement,state,{id:'result',day:'project',version:2},[],open);
 expect(root.textContent).toContain('not an independent validation');
 expect(root.textContent).toContain('[2,16]');expect(root.querySelector('script')).toBeNull();
 const link=root.querySelector('a') as unknown as HTMLAnchorElement;
 expect(link.href).toBe('/api/scientific-files/result?session=project&revision=2');
 expect(link.textContent).toBe('Download result file');
 const methods=root.querySelectorAll('details').find(d=>d.className==='scientific-result-method')!;
 const buttons=methods.querySelectorAll('button');expect(buttons).toHaveLength(1);
 expect(buttons[0].textContent).toBe('Open effects input · v3');fire(buttons[0],'click');
 expect(open).toHaveBeenCalledWith({id:'input',version:3,title:'effects'});
});

it('keeps large nested records collapsed and reveals exact text only on request',()=>{
 const dom=syntheticDOM();vi.stubGlobal('document',dom.document);
 const root=new TestElement('section');
 const genes=Array.from({length:200},(_,i)=>`GENE_${i}`);
 const state={sourceFile:{format:1,name:'model.npz',size:42,sha256:'b'.repeat(64)},matrixOperation:{format:1,capability:'response-fit',
 output:{summary:{missingNormalization:{genes},contextWeighting:'equal-total-weight'}},inputs:{}}};
 showMatrixResult(root as unknown as HTMLElement,state,{id:'result',day:'project',version:2},[],vi.fn());
 expect(root.textContent).toContain('Missing normalization');
 expect(root.textContent).toContain('View 1 field');
 expect(root.textContent).not.toContain('GENE_199');
 const detail=root.querySelectorAll('details').find(d=>d.className==='scientific-result-value')!;
 (detail as unknown as HTMLDetailsElement).open=true;fire(detail,'toggle');
 expect(detail.textContent).toContain('GENE_199');
 fire(detail,'toggle');expect(detail.querySelectorAll('pre')).toHaveLength(1);
 expect(root.querySelectorAll('table')).toHaveLength(0);
});
