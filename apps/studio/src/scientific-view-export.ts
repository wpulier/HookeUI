/** Native rendered view export. Scientific data and movies remain separate. */
import { scientificViewPreferences, type ScientificViewPreferences } from './scientific-view-preferences';
import { validMovieFile } from './scientific-movie';
const node = <K extends keyof HTMLElementTagNameMap>(tag: K, text='') => { const element=document.createElement(tag); element.textContent=text; return element; };
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
export type ViewRegion={x:number;y:number;width:number;height:number};
export type ViewExportSelection={scope:'current-view'|'selected-region';region:ViewRegion|null;previous?:string};
function regionValue(value:unknown):ViewRegion|null {
  if(value===null)return null;
  if(!object(value)||Object.keys(value).sort().join(' ')!=='height width x y'||typeof value.x!=='number'||typeof value.y!=='number'||typeof value.width!=='number'||typeof value.height!=='number'||![value.x,value.y,value.width,value.height].every(Number.isSafeInteger))throw new Error('The captured export region is invalid; keep the original file.');
  return {x:value.x,y:value.y,width:value.width,height:value.height};
}
type Actions={export(selection:ViewExportSelection,request:string):Promise<unknown>;list():Promise<unknown>;restore(view:ScientificViewPreferences):Promise<void>};

export function scientificViewExport(root:HTMLElement, source:{id:string;day:string;version:number}, actions:Actions):{dispose():void} {
  const section=node('section'), status=node('p'), result=node('div'), history=node('div');
  status.setAttribute('role','status');
  const scope=node('select'); scope.setAttribute('aria-label','Picture export scope');
  for(const [value,label] of [['current-view','Current view'],['selected-region','Selected region']]) { const option=node('option',label); option.value=value; scope.append(option); }
  scope.value='current-view';
  const bounds=node('fieldset'); bounds.append(node('legend','Region in preview pixels'));
  const inputs={} as Record<keyof ViewRegion,HTMLInputElement>;
  for(const key of ['x','y','width','height'] as const) { const label=node('label',key), input=node('input'); input.type='number'; input.min=key==='x'||key==='y'?'0':'1'; input.step='1'; input.value=key==='x'||key==='y'?'0':'128'; input.setAttribute('aria-label',`Export region ${key}`); label.append(input); bounds.append(label); inputs[key]=input; }
  bounds.hidden=true; scope.addEventListener('change',()=>{bounds.hidden=scope.value!=='selected-region';});
  const start=node('button','Export view PNG'), refresh=node('button','Previous view exports');
  section.append(node('h4','Picture of this view'),node('p','Exports the current native preview at its displayed pixel resolution, with captured axes, camera and layer appearance. Save scientific changes and reopen the accepted revision first. Native scientific values and movies have separate exports.'),scope,bounds,start,status,result,refresh,history); root.append(section);
  let disposed=false,busy=false;
  const perform=async(action:()=>Promise<void>)=>{if(disposed||busy)return;busy=true;start.disabled=refresh.disabled=true;try{await action();}catch(error){if(!disposed)status.textContent=error instanceof Error?error.message:String(error);}finally{busy=false;start.disabled=refresh.disabled=false;}};
  const download=(project:unknown)=>{
    if(!object(project)||typeof project.id!=='string'||!/^p[a-f0-9]{32}$/.test(project.id)||project.session!==source.day||project.revision!==1)throw new Error('The export result belongs to another Project or revision. Check Project Data.');
    const link=node('a','Download view PNG');link.download='Scientific view.png';link.href=`/api/scientific-files/${project.id}?${new URLSearchParams({session:source.day,revision:'1'})}`;return link;
  };
  const exporting=async(selection:ViewExportSelection)=>{
    status.textContent='Verifying and saving the captured rendered view…';
    const response=await actions.export(selection,crypto.randomUUID());
    if(!object(response)||response.ok!==true||!object(response.project))throw new Error('Export acceptance was not confirmed. Check Project Data before trying again.');
    const project=response.project, document=project.document,state=object(document)?document.state:null, kept=object(state)?state.viewExport:null;
    if(!object(kept)||kept.ok!==true||!object(kept.source)||kept.source.id!==source.id||kept.source.session!==source.day||kept.source.revision!==source.version||kept.scope!==selection.scope||JSON.stringify(regionValue(kept.region))!==JSON.stringify(selection.region)||kept.reexportOf!==(selection.previous??null)||!object(document)||!object(state)||!validMovieFile(state.sourceFile,{id:String(project.id),session:source.day},document.resources))throw new Error('The export receipt differs from this captured selection. Check Project Data; no success is claimed.');
    if(!disposed){result.replaceChildren(download(project));status.textContent='View PNG verified and saved in Project Data. Original scientific values and previous exports are unchanged.';}
  };
  start.addEventListener('click',()=>void perform(async()=>{
    const region=scope.value==='selected-region'?{x:Number(inputs.x.value),y:Number(inputs.y.value),width:Number(inputs.width.value),height:Number(inputs.height.value)}:null;
    if(region&&Object.values(region).some(v=>!Number.isSafeInteger(v)))throw new Error('Choose integer pixel bounds for the region.');
    await exporting({scope:region?'selected-region':'current-view',region});
  }));
  refresh.addEventListener('click',()=>void perform(async()=>{
    const value=await actions.list();if(!object(value)||!Array.isArray(value.items))throw new Error('Previous export receipts could not be read.');
    if(disposed)return;history.replaceChildren();
    if(!value.items.length)history.append(node('p','No view exports retained for this document yet.'));
    if(value.limited)history.append(node('p','Showing the first 100 retained export receipts. Older files remain preserved.'));
    for(const row of value.items){
      if(!object(row))continue;
      const item=node('section');item.append(node('p',`${String(row.scope??'View export')} · ${String(row.at??'')} · ${row.ok?'Verified pixels':'Not accepted'}`));
      if(row.project)item.append(download(row.project));
      if(Array.isArray(row.problems)&&row.problems.length)item.append(node('p',row.problems.map(String).join(' ')));
      const affected=Array.isArray(row.affected)?row.affected.filter(object):[];
      if(affected.length){
        const fixed=affected.some(f=>typeof f.fixedIn==='string'&&f.fixedIn);
        item.append(node('p',fixed?'This renderer version has a declared result fix. Re-export requires the reviewed fixed build and the same saved inputs/view.':'Fix identified — compatible build pending. The original is preserved; no corrected output is claimed.'));
        if(fixed&&typeof row.request==='string'){
          const view=scientificViewPreferences(row.preferences), restore=node('button','Restore export view'), redo=node('button','Re-export with fixed renderer');
          restore.addEventListener('click',()=>void perform(async()=>{await actions.restore(view);if(!disposed)status.textContent='Captured view restored. Check its preview and size, then explicitly re-export.';}));
          redo.addEventListener('click',()=>void perform(async()=>{
            // The server owns and revalidates the original region and source.
            await exporting({scope:row.scopeKind==='selected-region'?'selected-region':'current-view',region:regionValue(row.region),previous:String(row.request)});
          }));item.append(restore,redo);
        }
      }
      history.append(item);
    }
  }));
  return {dispose(){disposed=true;}};
}
