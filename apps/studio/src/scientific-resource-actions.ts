/** Measurements and native export always refer to an accepted revision. */
export type SelectedScientificArray={component:string;digest:string;name:string};
type Source = {id:string;day:string;version:number};
const object=(v:unknown):v is Record<string,unknown>=>v!==null&&typeof v==='object'&&!Array.isArray(v);
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const el=document.createElement(tag);el.textContent=text;return el;};
const words=(error:unknown)=>error instanceof Error?error.message:String(error);
const url=(source:Source,path:string,extra:Record<string,string>={})=>`/api/scientific-projects/${encodeURIComponent(source.id)}/${path}?${new URLSearchParams({session:source.day,revision:String(source.version),...extra})}`;
async function reply(response:Response){
  const value:unknown=await response.json();
  if(!object(value)||!response.ok||value.ok!==true)throw new Error(object(value)?`${String(value.says??'This action did not finish.')} ${String(value.next??'')}`:'The response was incomplete.');
  return value;
}
const columnLabel=(name:string,unit:unknown,count:number)=>name==='object_id'?'Object ID'
  :name==='voxel_count'?(unit==='pixel'?'Pixels':unit==='voxel'||count>4?'Voxels':'Pixels')
  :/^centroid_.+_px$/.test(name)?name.replace(/^centroid_(.+)_px$/,(_,axis:string)=>`${axis.toUpperCase()} (px)`)
  :typeof unit==='string'?`${name} (${unit})`:name;
function columnDescriptions(root:HTMLElement,columns:Record<string,unknown>[]){
  const list=node('ul');
  for(const column of columns){
    if(typeof column.description==='string'&&column.description)
      list.append(node('li',`${columnLabel(String(column.name),column.unit,columns.length)}: ${column.description}`));
  }
  root.append(list);
}
/** Display the server's metadata-only plan; missing details cannot authorize a run. */
function measurementPlan(root:HTMLElement,value:unknown,canMeasure:boolean):boolean{
  if(!object(value)||value.format!=='scientific-object-measurement-plan-v1'||value.available!==canMeasure
    ||typeof value.reason!=='string'||typeof value.method!=='string'||typeof value.calibrationReason!=='string'
    ||!Array.isArray(value.columns)||value.columns.length>6
    ||!value.columns.every(c=>object(c)&&typeof c.name==='string'&&typeof c.description==='string'
      &&(c.unit===undefined||c.unit===null||typeof c.unit==='string'))
    ||(canMeasure?(!value.method||!value.columns.length):!value.reason)){
    root.append(node('p','Measurement details are unavailable. Reload saved data before measuring.'));
    return false;
  }
  if(!canMeasure){root.append(node('p',value.reason));return false;}
  root.append(node('p',value.method));
  columnDescriptions(root,value.columns.filter(object));
  if(value.calibrationReason)root.append(node('p',`No physical area or volume: ${value.calibrationReason}.`));
  return true;
}
/** A typed table of the saved revision, read a page of rows at a time from its retained columns. */
function typedMeasurements(root:HTMLElement,value:Record<string,unknown>,source:Source):boolean{
  if(typeof value.table!=='string'||!/^[a-z][a-z0-9_]{0,63}$/.test(value.table)||!object(value.source)
    ||value.source.id!==source.id||value.source.session!==source.day)
    throw new Error('These measurements use an unsupported saved format. Your original arrays remain retained.');
  const section=node('section'),heading=node('h3','Measured objects'),status=node('p','Reading the saved measurement table…');
  section.className='scientific-measurements';status.setAttribute('role','status');
  section.append(heading,node('p',`Saved labels, revision ${String(value.source.revision)}. Background 0 excluded. Units and methods are recorded with each column.`));
  const download=node('a','Download full CSV');download.href=url(source,'measurements.csv');download.download='';section.append(download,status);
  root.append(section);
  void(async()=>{
    try{
      const result=await reply(await fetch(url(source,`tables/${value.table}`,{offset:'0',limit:'100'})));
      const table=result.table,rows=result.rows;
      if(!object(table)||typeof table.rows!=='number'||!Array.isArray(table.columns)||!Array.isArray(rows))throw new Error('The saved measurement table is incomplete.');
      const columns=table.columns.filter(object);
      heading.textContent=`${table.rows} measured ${table.rows===1?'object':'objects'}`;
      columnDescriptions(section,columns);
      const scroll=node('div');scroll.style.cssText='overflow:auto;max-height:320px';
      const grid=node('table');grid.style.cssText='border-collapse:collapse;font-size:12px';
      const head=node('tr');for(const column of columns)head.append(node('th',columnLabel(String(column.name),column.unit,columns.length)));
      const thead=node('thead');thead.append(head);grid.append(thead);
      const body=node('tbody');for(const row of rows){const tr=node('tr');for(const cell of Array.isArray(row)?row:[])tr.append(node('td',cell===null?'—':String(cell)));body.append(tr);}
      grid.append(body);scroll.append(grid);status.textContent='';status.hidden=true;section.append(scroll);
      if(table.rows>rows.length)section.append(node('p',`Showing the first ${rows.length} objects. The CSV contains every measured object.`));
      if(!table.rows)section.append(node('p','No nonzero object labels were found. Inspect or correct the segmentation before measuring again.'));
    }catch(error){status.textContent=`${words(error)} The saved revision is unchanged.`;}
  })();
  return true;
}
export function measurementTable(root:HTMLElement,value:unknown,source:Source):boolean{
  if(value===undefined)return false;
  if(object(value)&&value.format==='scientific-object-measurements-v2')return typedMeasurements(root,value,source);
  if(!object(value)||value.format!=='scientific-object-measurements-v1'||!Array.isArray(value.columns)
    ||!value.columns.every(c=>typeof c==='string')||!Array.isArray(value.rows)||value.rows.length>10000
    ||!value.rows.every(r=>Array.isArray(r)&&r.length===(value.columns as unknown[]).length&&r.every(v=>typeof v==='string'||typeof v==='number'))
    ||!object(value.source)||value.source.id!==source.id||value.source.session!==source.day)
    throw new Error('These measurements use an unsupported saved format. Your original arrays remain retained.');
  const section=node('section');section.className='scientific-measurements';
  section.append(node('h3',`${value.rows.length} measured ${value.rows.length===1?'object':'objects'}`),
    node('p',`Size and center in pixels · no calibration applied. Saved labels, revision ${String(value.source.revision)}. Background 0 excluded.`));
  const download=node('a','Download full CSV');download.href=url(source,'measurements.csv');download.download='';section.append(download);
  const scroll=node('div');scroll.style.cssText='overflow:auto;max-height:320px';
  const table=node('table');table.style.cssText='border-collapse:collapse;font-size:12px';
  const head=node('tr');for(const name of value.columns){const label=name==='object_id'?'Object ID':name==='voxel_count'?(value.columns.length===4?'Pixels':'Voxels'):name.replace(/^centroid_(.+)_px$/,(_,axis:string)=>`${axis.toUpperCase()} (px)`);head.append(node('th',label))};const heading=node('thead');heading.append(head);table.append(heading);
  const body=node('tbody');for(const row of value.rows.slice(0,100)){const tr=node('tr');for(const cell of row as (string|number)[])tr.append(node('td',String(cell)));body.append(tr);}
  table.append(body);scroll.append(table);section.append(scroll);
  if(value.rows.length>100)section.append(node('p','Showing the first 100 objects. The CSV contains every measured object.'));
  if(!value.rows.length)section.append(node('p','No nonzero object labels were found. Inspect or correct the segmentation before measuring again.'));
  root.append(section);return true;
}

const describeResource=(item:Record<string,unknown>)=>{
  const name=String(item.name??'?');
  if(item.readable!==true)return `${name} · cannot be read: ${String(item.reason??'unknown reason')}`;
  if(item.family==='table')return `${name} · ${String(item.semantic)} · ${String(item.rows)} rows · ${String(item.columns)} columns`;
  if(item.family==='embeddings')return `${name} · embeddings · ${String(item.rows)} × ${String(item.dims)} features, keyed to ${String(item.table)}`;
  return `${name} · ${String(item.semantic).replace('-',' ')} · ${String(item.size)} bytes · ${String(item.mediaType)}`;
};
/** The revision's non-layer resources, each listed by its own family's checks. */
async function savedResources(list:HTMLElement,source:Source,signal:AbortSignal){
  const result=await reply(await fetch(url(source,'resources'),{signal}));
  if(!Array.isArray(result.resources))throw new Error('The saved resource list is incomplete.');
  const others=result.resources.filter(object).filter(item=>['table','embeddings','artifact'].includes(String(item.family)));
  if(!others.length)return;
  const section=node('section');section.append(node('h4','Tables, embeddings and files'));
  for(const item of others){
    const row=node('p',describeResource(item));section.append(row);
    if(item.family==='artifact'&&item.readable===true&&typeof item.name==='string'){
      const download=node('a','Download');download.href=url(source,`artifacts/${encodeURIComponent(item.name)}`);download.download='';row.append(' ',download);
    }
  }
  list.append(section);
}
function editableMeasurements(root:HTMLElement,revision:number,options:{signal:AbortSignal;current:()=>boolean;editable?:(revision:number)=>Promise<void>}){
  if(!options.editable)return;
  const open=node('button','Open editable table'),status=node('p');status.setAttribute('role','status');
  root.append(open,node('p','Add columns, analyze or graph these saved measurements. The measured values and object IDs stay unchanged.'),status);
  let busy=false;
  open.addEventListener('click',()=>{void(async()=>{
    if(busy||options.signal.aborted)return;
    if(!options.current()){status.textContent='Return to this result’s Project and Tab before opening its table.';return;}
    busy=true;open.disabled=true;status.textContent='Keeping an editable table of these measurements…';
    try{await options.editable!(revision);status.textContent='Table saved in this Project’s Data.';}
    catch(error){status.textContent=words(error);}
    finally{busy=false;open.disabled=false;}
  })();});
}
export function scientificResourceActions(root:HTMLElement,source:Source,state:unknown,open:(revision:number)=>Promise<void>,
  options:{signal:AbortSignal;current:()=>boolean;selection?:SelectedScientificArray;editable?:(revision:number)=>Promise<void>}):void{
  if(object(state)&&measurementTable(root,state.scientificMeasurements,source)){
    const measured=state.scientificMeasurements;
    const inputs=object(state.scientificView)&&object(state.scientificView.inputs)?state.scientificView.inputs:{};
    if(object(measured)&&object(measured.source)&&JSON.stringify(inputs[String(measured.source.input)])!==JSON.stringify(measured.source.reference)){
      root.append(node('p','These measurements belong to an earlier saved array. The current labels have changed; measure them again before using these rows for the current result.'));
    }else if(object(measured)&&measured.format==='scientific-object-measurements-v2')editableMeasurements(root,source.version,options);
  }
  const section=node('section'),load=node('button','Choose saved data'),status=node('p'),list=node('div');
  section.append(node('h3','Saved data'),node('p',`Uses saved revision ${source.version}. Save corrections first; unsaved edits are not included.`),load,status,list);
  status.setAttribute('role','status');root.append(section);
  let busy=false;let action:AbortController|null=null;
  options.signal.addEventListener('abort',()=>action?.abort(),{once:true});
  const loadArrays=async()=>{
    if(busy||action||options.signal.aborted||!options.current())return;busy=true;load.disabled=true;status.textContent='Reading saved arrays…';
    try{
      const result=await reply(await fetch(url(source,'arrays'),{signal:options.signal}));
      if(options.signal.aborted||!options.current())return;
      if(!object(result.source)||result.source.id!==source.id||result.source.session!==source.day||result.source.revision!==source.version||!Array.isArray(result.arrays))throw new Error('The arrays do not match this saved revision.');
      const digest=result.source.digest;list.replaceChildren();
      const selected=options.selection;
      const rows=selected?result.arrays.filter(item=>object(item)&&object(item.reference)&&item.reference.component===selected.component):result.arrays;
      if(selected&&(digest!==selected.digest||rows.length!==1||!object(rows[0])||!['instance-labels','class-labels'].includes(String(rows[0].semantic))))
        throw new Error('The chosen labels no longer match this exact saved revision. Reopen the retained comparison.');
      if(selected)list.append(node('p',`Chosen result: ${selected.name} · saved revision ${source.version}. Other arrays remain in the document; these actions use only the chosen labels.`));
      const operation=result.operation;
      const available=object(operation)&&operation.id==='scientific-object-measurements-v2'
        &&operation.implementation==='builtin'&&operation.preparation==='none'
        &&object(operation.input)&&operation.input.kind==='array'&&operation.input.semantic==='instance-labels'
        &&operation.input.revision==='exact-accepted'&&object(operation.output)&&operation.output.kind==='table'
        &&operation.output.semantic==='object-table'&&operation.output.publication==='project-revision';
      if(!available)list.append(node('p','Measurement operation is unavailable. Reload saved data before measuring.'));
      for(const item of rows){
        if(!object(item)||typeof item.key!=='string'||typeof item.name!=='string'||typeof item.canMeasure!=='boolean'||!Array.isArray(item.shape))throw new Error('A saved array description is incomplete.');
        const key=item.key,row=node('section'),message=node('p');message.setAttribute('role','status');
        row.append(node('h4',item.name),node('p',`${item.shape.join(' × ')} · ${String(item.dtype)} · ${String(item.semantic??'meaning not declared')}`));
        const download=node('a','Export native data + metadata');download.href=url(source,'native-export',{input:key});download.download='';row.append(download);
        if(measurementPlan(row,item.measurementPlan,item.canMeasure)&&available){
          const measure=node('button','Measure objects'),stop=node('button','Stop measurement');stop.hidden=true;
          stop.addEventListener('click',()=>{action?.abort();stop.disabled=true;message.textContent='Stopping the request. Check saved data before retrying; acceptance may already have finished.';});
          measure.addEventListener('click',()=>{void(async()=>{
            if(action||options.signal.aborted)return;
            if(!options.current()){message.textContent='Return to this document’s Project and Tab before measuring.';return;}
            action=new AbortController();load.disabled=true;measure.disabled=true;stop.hidden=false;stop.disabled=false;message.textContent='Measuring saved object labels…';
            try{
              const result=await reply(await fetch(`/api/scientific-projects/${encodeURIComponent(source.id)}/measure`,{
                method:'POST',headers:{'Content-Type':'application/json','X-MicroscopeMind':'measure-scientific-objects'},
                signal:action.signal,body:JSON.stringify({operation:operation.id,session:source.day,revision:source.version,input:key})}));
              if(options.signal.aborted)return;
              const project=result.project,doc=object(project)?project.document:null,table=object(doc)&&object(doc.state)?doc.state.scientificMeasurements:null;
              if(!object(project)||project.id!==source.id||project.session!==source.day||project.revision!==source.version+1
                ||!object(table)||!object(table.source)||table.source.digest!==digest||table.source.input!==key)throw new Error('The saved measurement receipt did not match this input. Check Project Data before retrying.');
              message.textContent=`Measurements saved as revision ${project.revision}. The original arrays and your private session are unchanged.`;
              const target={...source,version:Number(project.revision)};measurementTable(row,table,target);
              if(table.format==='scientific-object-measurements-v2')editableMeasurements(row,target.version,options);
              if(options.current()){message.tabIndex=-1;message.focus();}
              const show=node('button','Open saved measurements');row.append(show);measure.hidden=true;
              show.addEventListener('click',()=>{if(!options.current()){message.textContent='Return to the original Project before opening its measurements.';return;}void open(target.version).catch(error=>{message.textContent=words(error);});});
            }catch(error){if(!options.signal.aborted)message.textContent=action?.signal.aborted?'The request stopped. Check Project Data before retrying; accepted work is preserved.':words(error);}
            finally{action=null;load.disabled=false;measure.disabled=false;stop.hidden=true;}
          })();});
          row.append(measure,stop);
        }
        row.append(message);list.append(row);
      }
      status.textContent=result.arrays.length?'Choose the saved array you want to use.':'This document has no retained native arrays yet. Save an output first.';
      try{await savedResources(list,source,options.signal);}
      catch(error){if(!options.signal.aborted)list.append(node('p',`Tables and files could not be listed: ${words(error)}`));}
    }catch(error){if(!options.signal.aborted)status.textContent=words(error);}
    finally{busy=false;load.disabled=false;}
  };
  load.addEventListener('click',()=>{void loadArrays();});
  if(options.selection)void loadArrays();
}
