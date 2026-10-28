import type {StepKind} from './workflow-model';

/** Editable scientific choices. Tool identity and execution qualification remain pinned by the service. */
export function WorkflowSettings({kind,settings,change}:{kind:StepKind;settings:Record<string,unknown>;change:(value:Record<string,unknown>)=>void}){
 if(kind==='tool'&&settings.capability==='basic-bright-objects'&&settings.mode==='automatic'){
  const parameters=settings.parameters as Record<string,number>;
  const parameter=(key:string,value:number)=>change({...settings,parameters:{...parameters,[key]:value}});
  return <>
   <label>Smoothing (pixels)<input type="number" min={0} max={10} step={.1} value={parameters.sigma} onChange={e=>parameter('sigma',e.target.valueAsNumber)}/></label>
   <label>Minimum region size (pixels)<input type="number" min={1} max={100000} step={1} value={parameters.min_area} onChange={e=>parameter('min_area',e.target.valueAsNumber)}/></label>
   <label>Connected neighbors<select value={parameters.connectivity} onChange={e=>parameter('connectivity',Number(e.target.value))}><option value={4}>4 · sides only</option><option value={8}>8 · sides and corners</option></select></label>
   <p>Finds bright regions using the saved local method. Inspect and correct the result before accepting it; these regions have no verified biological identity.</p>
  </>;
 }
 if(kind==='researcher')return <>
  <label>Research question<textarea rows={4} value={String(settings.question||'')} maxLength={8000} onChange={e=>change({...settings,question:e.target.value})}/></label>
  <label>Stopping and acceptance criteria<textarea rows={4} value={String(settings.criteria||'')} maxLength={8000} onChange={e=>change({...settings,criteria:e.target.value})}/></label>
  <label>Maximum spend (USD)<input type="number" min={.01} max={100} step={.01} value={Number(settings.maximumUsd)} onChange={e=>change({...settings,maximumUsd:e.target.valueAsNumber})}/></label>
  <label>Time limit (seconds)<input type="number" min={1} max={3600} step={1} value={Number(settings.seconds)} onChange={e=>change({...settings,seconds:e.target.valueAsNumber})}/></label>
  <p>Gemini evidence report over selected text inputs, with no executable tools. This standalone app does not run managed report nodes; start a local or connected researcher to use capability workflows. Review the original Study before accepting its report.</p>
 </>;
 return null;
}
export function hasSettingsForm(kind:StepKind,settings:Record<string,unknown>){return kind==='researcher'||kind==='tool'&&settings.capability==='basic-bright-objects'&&settings.mode==='automatic';}
