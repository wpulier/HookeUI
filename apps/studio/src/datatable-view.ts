import { el } from './camera/dom';
import { formatValue, header, type TableColumn } from './measurements';
import './datatable-view.css';

export interface DataTablePin { session: string; id: string; version: number }
export interface DataTableOwner { session: string; generation: number }
export interface DataTableSource { day: string; id: string; revision: number; kind: string; label: string }
export interface DataTableColumn extends TableColumn { origin: 'source'|'added'|'computed'|'excluded'; kind?: string }
export interface DataTableRow extends Record<string, unknown> { row: string; source: string; member: string; excluded: string|null }
export interface DataTableSnapshot {
 id: string; version: number; title: string; mode: 'follow'|'frozen';
 comparison?: {note:string};
 columns: DataTableColumn[]; rows: DataTableRow[]; sources: DataTableSource[];
 added: {key:string;label:string;type:string;rule?:unknown}[];
 counts: {rows:number;included:number;excluded:number}; versions: number[];
 freshness: {state:'current'|'updating'|'stale'|'frozen';words:string};
}
export type TableWhere = Record<string, unknown>;
export type DataTableEdit = {op:'set';column:string;rows:string[];value:unknown}|
 {op:'exclude';rows:string[];reason:string}|{op:'include';rows:string[]}|
 {op:'add-column';label:string;type:'category'|'text'|'number'}|{op:'mode';mode:'follow'|'frozen'};
export interface DataTableMember {session:string;id:string;revision:number;member:string;kind:string;title:string}
export interface DataTableViewDeps {
 /** Captured opening authority, including away-and-back switches; never just the active session name. */
 current(owner:DataTableOwner):boolean;
 loaded?(pin:DataTablePin,table:DataTableSnapshot):void|Promise<void>;
 /** Update the existing placement to this accepted revision; it must not open an unrelated active Tab. */
 saved?(pin:DataTablePin,table:DataTableSnapshot):void|Promise<void>;
 showMember?(member:DataTableMember):void|Promise<void>;
 analyze?(pin:DataTablePin):void|Promise<void>;
 graph?(pin:DataTablePin):void|Promise<void>;
 export?(pin:DataTablePin):void|Promise<void>;
 request?:typeof fetch;
}
export const TABLE_PAGE_ROWS=100;
export function dataTableURL(pin:DataTablePin,csv=false):string {
 return `/api/datatables/${encodeURIComponent(pin.id)}${csv?'.csv':''}?${new URLSearchParams({session:pin.session,version:String(pin.version)})}`;
}
function number(value:unknown):number|null {
 return typeof value==='number'&&Number.isFinite(value)?value:null;
}
/** The same range/equality grammar as population.select and datatables.matches; no expression evaluation. */
export function tableFilter(table:DataTableSnapshot,where:TableWhere):DataTableRow[] {
 const columns=new Map(table.columns.map(c=>[c.key,c]));
 for(const [key,bounds] of Object.entries(where)){
  const column=columns.get(key);if(!column)throw new Error(`Choose an existing column; ${key} is unavailable.`);
  if(column.type==='number')for(const bound of (Array.isArray(bounds)?bounds.slice(0,2):[bounds]))
   if(bound!==null&&number(bound)===null)throw new Error(`${column.label} needs a numeric range.`);
 }
 if(!Object.keys(where).length)return table.rows;
 return table.rows.filter(row=>Object.entries(where).every(([key,bounds])=>{
  const value=row[key];
  if(columns.get(key)!.type==='number'){
   const v=number(value);if(v===null)return false;
   const [lo,hi]=Array.isArray(bounds)?bounds:[bounds,null];
   return (lo==null||v>=number(lo)!)&&(hi==null||v<=number(hi)!);
  }
  const text=(v:unknown)=>typeof v==='boolean'?(v?'True':'False'):String(v);
  return value!=null&&(Array.isArray(bounds)?bounds:[bounds]).filter(v=>v!==null).some(v=>text(v)===text(value));
 }));
}
export function checkedDataTable(value:DataTableSnapshot,pin:DataTablePin):DataTableSnapshot {
 if(!value||value.id!==pin.id||value.version!==pin.version||!Array.isArray(value.rows)||value.rows.length>100_000||
  !Array.isArray(value.columns)||value.columns.length>256||value.rows.length*value.columns.length>2_000_000||
  !Array.isArray(value.sources)||value.sources.some(s=>s.day!==pin.session)||
  !value.counts||value.counts.rows!==value.rows.length||!Array.isArray(value.added)||
  !['current','updating','stale','frozen'].includes(value.freshness?.state))throw new Error('The table does not match this Project and saved revision.');
 const keys=new Set(value.rows.map(row=>row.row));
 if(keys.size!==value.rows.length||value.rows.some(row=>typeof row.row!=='string'||!value.sources.some(s=>s.id===row.source)))
  throw new Error('The table has ambiguous or unavailable source rows.');
 return value;
}
const button=(text:string,act:()=>void)=>{const b=el('button',{type:'button',textContent:text});b.addEventListener('click',act);return b;};
const words=(error:unknown)=>error instanceof Error?error.message:String(error);

/** Content for the shared retained-artifact shell. Never creates its own window, Project or execution path. */
export class DataTableView {
 readonly root:HTMLElement;
 private readonly deps:DataTableViewDeps;
 private pin:DataTablePin|null=null;
 private owner:DataTableOwner|null=null;
 private table:DataTableSnapshot|null=null;
 private edits:DataTableEdit[]=[];
 private selected=new Set<string>();
 private where:TableWhere={};
 private page=0;
 private serial=0;
 private pending:AbortController|null=null;
 private saving=false;
 private problem='';
 private readonly status=el('p',{role:'status','aria-live':'polite'});
 // Freshness is a button (pass doc §I): Current / Table changed · Update / Frozen; pressing it checks or updates.
 private readonly freshness=el('button',{type:'button',className:'datatable-freshness'});
 private exact=false;
 private readonly more=el('details',{className:'datatable-more'});
 private readonly toolbar=el('div',{className:'datatable-toolbar'});
 private readonly controls=el('div',{className:'datatable-controls'});
 private readonly viewport=el('div',{className:'datatable-viewport',tabIndex:0,'aria-label':'Table rows'});
 private readonly pager=el('div',{className:'datatable-pager'});
 private readonly grid=el('table');
 private readonly selection=el('span');
 private readonly title=el('h2');
 private readonly comparison=el('p',{className:'datatable-note'});
 private readonly save=button('Save',()=>void this.saveDraft());
 private readonly discard=button('Discard draft',()=>this.discardDraft());
 private readonly latest=button('Open latest saved revision',()=>{if(this.pin&&this.owner&&this.table&&!this.dirty&&!this.saving&&this.current())void this.show({...this.pin,version:this.latestVersion()},this.owner);});
 private readonly retry=button('Retry opening',()=>{if(this.pin&&this.owner)void this.show(this.pin,this.owner);});
 constructor(root:HTMLElement,deps:DataTableViewDeps){
  this.root=root;this.deps=deps;root.classList.add('datatable-view');this.viewport.append(this.grid);
  this.freshness.addEventListener('click',()=>{if(this.table?.freshness.state==='stale'&&!this.table.sources.some(source=>source.kind==='native-measurements'))void this.update();else void this.refresh();});
  root.replaceChildren(this.title,this.freshness,this.comparison,this.status,this.toolbar,this.controls,this.selection,this.viewport,this.pager);
 }
 get dirty():boolean{return this.edits.length>0;}
 get busy():boolean{return this.saving;}
 /** The shell must ask Save/Discard/Cancel before removing this content when this returns false. */
 canClose():boolean{return !this.dirty&&!this.saving;}
 hide():void {this.serial++;this.pending?.abort();this.pending=null;}
 private current():boolean{return !!this.owner&&this.deps.current(this.owner);}
 private async read(pin:DataTablePin,signal:AbortSignal):Promise<DataTableSnapshot>{
  const response=await (this.deps.request??fetch)(dataTableURL(pin),{signal,cache:'no-store'});
  const value=await response.json();if(!response.ok)throw new Error([value.error,value.next].filter(Boolean).join('. ')||'Table unavailable.');
  return checkedDataTable(value,pin);
 }
 async show(pin:DataTablePin,owner:DataTableOwner):Promise<boolean>{
  if(this.dirty||this.saving){this.problem='Save or discard this table’s draft before opening another revision.';this.state();return false;}
  if(owner.session!==pin.session||!this.deps.current(owner))return false;
  this.hide();this.pin={...pin};this.owner={...owner};this.table=null;this.problem='';this.selected.clear();this.page=0;this.where={};
  const serial=this.serial,controller=this.pending=new AbortController();this.status.textContent='Opening saved table…';this.freshness.textContent='';this.selection.textContent='';this.grid.replaceChildren();this.controls.replaceChildren();this.toolbar.replaceChildren();this.pager.replaceChildren();this.title.textContent='Table';
  try{
   const table=await this.read(pin,controller.signal);if(serial!==this.serial||!this.current())return false;
   this.table=table;this.saving=true;this.render();
   try{await this.deps.loaded?.({...pin},table);}finally{this.saving=false;if(serial===this.serial&&this.current())this.state();}
   return true;
  }catch(error){if(serial===this.serial&&this.current()){this.problem=words(error);this.status.textContent=`Opening failed. ${this.problem}`;this.toolbar.replaceChildren(this.retry);}return false;}
 }
 /** Refreshes freshness of this exact revision; it cannot replace its rows or overwrite a draft. */
 async refresh():Promise<void>{
  if(!this.table||!this.pin||!this.current()||this.saving)return;
  const serial=this.serial,pin={...this.pin},controller=new AbortController();
  try{const value=await this.read(pin,controller.signal);if(serial!==this.serial||!this.current()||this.saving||this.pin?.version!==pin.version)return;
   this.table.freshness=value.freshness;this.table.versions=value.versions;this.state();
  }catch(error){if(serial===this.serial&&this.current()&&this.pin?.version===pin.version){this.problem=`Freshness unavailable. ${words(error)}`;this.state();}}
 }
 stage(edit:DataTableEdit):void {
  if(!this.current()||!this.table||this.saving)return;
  if(this.edits.length>=1000){this.problem='Save this draft before making more changes.';this.state();return;}
  if('rows' in edit&&(!edit.rows.length||edit.rows.some(id=>!this.table!.rows.some(r=>r.row===id)))){this.problem='Select rows from this saved revision.';this.state();return;}
  if(edit.op==='set'&&typeof edit.value==='number'&&!Number.isFinite(edit.value)){this.problem='Enter a finite number.';this.state();return;}
  this.edits.push(structuredClone(edit));this.problem='';this.state();
 }
 discardDraft():void {if(this.saving)return;this.edits=[];this.problem='';this.render();}
 async saveDraft():Promise<boolean>{return this.write(false);}
 async update():Promise<boolean>{return this.write(true);}
 private async write(update:boolean):Promise<boolean>{
  if(!this.pin||!this.table||!this.current()||this.saving||update&&this.dirty||!update&&!this.dirty)return false;
  const pin={...this.pin},serial=this.serial,edits=structuredClone(this.edits);this.saving=true;this.problem='';this.state();
  try{
   const response=await (this.deps.request??fetch)(`/api/datatables/${encodeURIComponent(pin.id)}`,{method:'PATCH',
    headers:{'Content-Type':'application/json','X-MicroscopeMind':'datatable'},body:JSON.stringify({session:pin.session,version:pin.version,...(update?{update:true}:{edits})})});
   const value=await response.json();if(!response.ok)throw new Error([value.error,value.next].filter(Boolean).join('. ')||'Save was not accepted.');
   if(serial!==this.serial||!this.current())return false;
   if(!Number.isSafeInteger(value.version)||value.version<pin.version||!update&&value.version===pin.version)throw new Error('The saved table did not return a new revision. Reopen Data to check the accepted result.');
   const next={...pin,version:value.version},table=checkedDataTable(value,next);
   this.pin=next;this.table=table;this.edits=[];this.selected.clear();this.render();
   try{await this.deps.saved?.({...next},table);}catch(error){if(serial===this.serial&&this.current()){this.problem=`Saved in Data; the window reference could not be updated. ${words(error)}`;this.state();}}
   return true;
  }catch(error){if(serial===this.serial&&this.current()){this.problem=`Not saved. ${words(error)} Your draft is kept; do not overwrite a newer revision.`;}return false;}
  finally{this.saving=false;if(serial===this.serial&&this.current())this.state();}
 }
 filter(where:TableWhere):void {
  if(!this.table)return;
  try{tableFilter(this.table,where);this.where=structuredClone(where);this.page=0;this.problem='';this.renderRows();this.state();}
  catch(error){this.problem=words(error);this.state();}
 }
 /** A retained mask click selects only its exact source revision, never a same-named member from another capture. */
 selectMember(member:DataTableMember):boolean {
  if(!this.table||!this.current()||member.session!==this.pin?.session)return false;
  const source=this.table.sources.find(s=>s.id===member.id&&s.revision===member.revision);
  if(!source)return false;
  const row=this.table.rows.find(r=>r.source===member.id&&r.member===member.member);if(!row)return false;
  this.where={};this.selected=new Set([row.row]);this.page=Math.floor(this.table.rows.indexOf(row)/TABLE_PAGE_ROWS);this.renderRows();this.state();return true;
 }
 private showRow(row:DataTableRow):void {
  if(!this.current()||!this.table||!this.pin||!this.deps.showMember)return;
  const source=this.table.sources.find(s=>s.id===row.source);if(source?.kind!=='population')return;
  const serial=this.serial,member={session:this.pin.session,id:source.id,revision:source.revision,kind:source.kind,member:row.member,title:source.label};
  void Promise.resolve().then(()=>{if(serial===this.serial&&this.current())return this.deps.showMember!(member);})
   .catch(error=>{if(serial===this.serial&&this.current()){this.problem=words(error);this.state();}});
 }
 private async action(callback:((pin:DataTablePin)=>void|Promise<void>)|undefined):Promise<void>{
  if(!callback||!this.pin||!this.current()||this.dirty||this.saving)return;
  const serial=this.serial;
  try{await callback({...this.pin});}catch(error){if(serial===this.serial&&this.current()){this.problem=words(error);this.state();}}
 }
 private latestVersion():number{return this.table?.versions.reduce((last,v)=>Math.max(last,v),this.table.version)??0;}
 private state():void {
  if(!this.table)return;
  this.latest.hidden=this.latestVersion()<=this.table.version;
  this.freshness.textContent=({current:'Current',updating:'Updating…',stale:'Table changed · Update',frozen:'Frozen'})[this.table.freshness.state];
  this.freshness.dataset.state=this.table.freshness.state;this.freshness.disabled=this.dirty||this.saving||!this.current();
  this.freshness.title=this.table.freshness.words;
  this.status.textContent=this.problem||(this.saving?'Saving…':this.dirty?`Draft · ${this.edits.length} change${this.edits.length===1?'':'s'}, not saved yet · kept while this window is open`:'Saved');
  this.save.disabled=!this.dirty||this.saving||!this.current();this.discard.disabled=!this.dirty||this.saving;
  // Save and Discard draft appear while there is a draft; otherwise the status says Saved and there is nothing to press.
  this.save.hidden=this.discard.hidden=!this.dirty&&!this.saving;
  // A disabled action says why (title and accessible description), so a reader never wonders what is wrong.
  const why=this.saving?'Wait for the save to finish.':this.dirty?'Save or discard this draft first.':!this.current()?'Return to this table’s Project to use it.':'';
  for(const control of this.toolbar.querySelectorAll<HTMLButtonElement>('[data-clean-action]')){control.disabled=!!why;if(why){control.title=why;control.setAttribute('aria-description',why);}else{control.removeAttribute('title');control.removeAttribute('aria-description');}}
  for(const control of [...this.controls.querySelectorAll<HTMLInputElement|HTMLButtonElement|HTMLSelectElement>('input,button,select'),...this.grid.querySelectorAll<HTMLInputElement|HTMLButtonElement>('input,button')])control.disabled=this.saving||!this.current()||control.dataset.unsupported==='true';
 }
 private render():void {
  if(!this.table)return;
  this.title.textContent=this.table.title;this.comparison.textContent=this.table.comparison?.note??'';this.comparison.hidden=!this.table.comparison?.note;this.toolbar.replaceChildren(this.status,this.save,this.discard); // Saved / Draft on the toolbar's line
  // A few actions in view (the freshness chip, Analyze, Graph), the rest under More; every one is kept (pass doc §I).
  this.toolbar.prepend(this.freshness);
  const menu=el('div',{className:'datatable-more-items'});this.more.replaceChildren(el('summary',{textContent:'More'}),menu);
  const clean=(label:string,callback:()=>void,where:HTMLElement=this.toolbar)=>{const b=button(label,callback);b.dataset.cleanAction='true';where.append(b);return b;};
  for(const [label,callback] of [['Analyze',this.deps.analyze],['Graph',this.deps.graph]] as const)
   if(callback)clean(label,()=>void this.action(callback));
  this.toolbar.append(this.more);
  if(!this.table.sources.some(source=>source.kind==='native-measurements')){
  clean('Update',()=>void this.update(),menu);
  const freeze=button(this.table.mode==='frozen'?'Follow source':'Freeze',()=>this.stage({op:'mode',mode:this.table!.mode==='frozen'?'follow':'frozen'}));menu.append(freeze);
  }
  if(this.deps.export)clean('Export',()=>void this.action(this.deps.export),menu);
  clean('Download CSV',()=>{if(this.pin&&this.current()&&!this.dirty&&!this.saving)window.open(dataTableURL(this.pin,true),'_blank','noopener');},menu);
  this.latest.dataset.cleanAction='true';menu.append(this.latest);
  menu.append(button('Check freshness',()=>void this.refresh()));
  const exact=button(this.exact?'Show rounded values':'Show exact values',()=>{this.exact=!this.exact;exact.textContent=this.exact?'Show rounded values':'Show exact values';this.renderRows();this.state();});
  exact.setAttribute('aria-pressed',String(this.exact));menu.append(exact);
  this.renderControls();this.renderRows();this.state();
 }
 private renderControls():void {
  const table=this.table!;this.controls.replaceChildren();
  // Filtering and editing are each one disclosure (Filter rows; Columns and exclusions), so the rows come first.
  const filterBox=el('details',{className:'datatable-tools'}),editBox=el('details',{className:'datatable-tools'});
  filterBox.append(el('summary',{textContent:Object.keys(this.where).length?'Filter rows · on':'Filter rows'}));editBox.append(el('summary',{textContent:'Columns and exclusions'}));
  const into=(box:HTMLElement)=>({append:(...nodes:Node[])=>box.append(...nodes)});
  const filterTo=into(filterBox),editTo=into(editBox);
  if(table.sources.some(source=>source.kind==='native-measurements'))this.controls.append(el('p',{textContent:'Uses the selected saved measurements. Object IDs refer to the original labels; selecting rows here does not highlight them in the image.'}));
  const column=el('select',{'aria-label':'Filter column'},...table.columns.map(c=>el('option',{value:c.key,textContent:header(c)})));
  const value=el('input',{'aria-label':'Filter value or minimum',placeholder:'Value or minimum'}),upper=el('input',{'aria-label':'Filter maximum',placeholder:'Maximum (optional)'});
  filterTo.append(column,value,upper,button('Apply filter',()=>{
   const c=table.columns.find(c=>c.key===column.value);if(!c)return;
   this.filter({...this.where,[c.key]:c.type==='number'?[value.value.trim()?Number(value.value):null,upper.value.trim()?Number(upper.value):null]:value.value.split(',').map(v=>v.trim())});
  }),button('Clear filters',()=>this.filter({})));
  if(Object.keys(this.where).length)filterBox.open=true;
  const label=el('input',{'aria-label':'New column name',placeholder:'Condition'}),type=el('select',{'aria-label':'New column type'},...['category','text','number'].map(t=>el('option',{value:t,textContent:t})));
  editTo.append(label,type,button('Add column to draft',()=>{if(label.value.trim())this.stage({op:'add-column',label:label.value.trim(),type:type.value as 'category'|'text'|'number'});}));
  const editable=table.added.filter(c=>!c.rule),target=el('select',{'aria-label':'Assign column'},...editable.map(c=>el('option',{value:c.key,textContent:c.label}))),assigned=el('input',{'aria-label':'Value for selected rows',placeholder:'Value for selected rows'});
  editTo.append(target,assigned,button('Assign to selected',()=>{if(target.value)this.stage({op:'set',column:target.value,rows:[...this.selected],value:assigned.value===''?null:table.added.find(c=>c.key===target.value)?.type==='number'?Number(assigned.value):assigned.value});}));
  const reason=el('input',{'aria-label':'Reason for exclusion',placeholder:'Why leave these rows out?'});
  editTo.append(reason,button('Exclude selected',()=>{if(reason.value.trim())this.stage({op:'exclude',rows:[...this.selected],reason:reason.value.trim()});}),button('Include selected',()=>this.stage({op:'include',rows:[...this.selected]})));
  const note=el('p',{className:'datatable-note',textContent:'Measured values stay unchanged. Edit added columns; save a new column before assigning its rows. Draft changes are applied on Save.'});editTo.append(note);
  if(this.dirty)editBox.open=true;
  this.controls.append(filterBox,editBox);
 }
 private renderRows():void {
  if(!this.table)return;
  const table=this.table,rows=tableFilter(table,this.where),pages=Math.max(1,Math.ceil(rows.length/TABLE_PAGE_ROWS));this.page=Math.min(this.page,pages-1);
  const start=this.page*TABLE_PAGE_ROWS,visible=rows.slice(start,start+TABLE_PAGE_ROWS);
  const heading=el('tr',{},el('th',{scope:'col',textContent:'Select'}),...table.columns.filter(c=>!['row','source','member'].includes(c.key)).map(c=>el('th',{scope:'col',textContent:header(c),className:c.type==='number'?'datatable-number':''})),el('th',{scope:'col',textContent:'Source'}));
  const body=el('tbody');
  for(const savedRow of visible){
   const row={...savedRow};
   for(const edit of this.edits)if('rows' in edit&&edit.rows.includes(row.row)){
    if(edit.op==='set')row[edit.column]=edit.value;
    if(edit.op==='exclude')row.excluded=edit.reason;
    if(edit.op==='include')row.excluded=null;
   }
   const checkbox=el('input',{type:'checkbox',checked:this.selected.has(row.row),'aria-label':`Select ${row.member} from ${row.source}`});
   const tr=el('tr',{'data-row':row.row,'aria-selected':String(this.selected.has(row.row)),className:row.excluded?'datatable-excluded':''},el('td',{},checkbox));
   checkbox.addEventListener('change',()=>{if(checkbox.checked){this.selected.add(row.row);this.showRow(row);}else this.selected.delete(row.row);tr.setAttribute('aria-selected',String(checkbox.checked));this.selection.textContent=`${this.selected.size} selected`;});
   for(const c of table.columns.filter(c=>!['row','source','member'].includes(c.key))){
    const cell=el('td');const manual=table.added.some(a=>a.key===c.key&&!a.rule);
    if(manual){const input=el('input',{value:row[c.key]==null?'':String(row[c.key]),'aria-label':`${c.label} for ${row.member}`,inputMode:c.type==='number'?'decimal':'text'});
     input.addEventListener('change',()=>this.stage({op:'set',column:c.key,rows:[row.row],value:input.value===''?null:c.type==='number'?Number(input.value):input.value}));cell.append(input);
    }else{
     // 3–4 significant figures by default (measurements.ts formatValue); the exact value is the cell's title and one
     // switch away (More › Show exact values), and copy/export keep full precision.
     const exactText=row[c.key]==null?'—':String(row[c.key]);
     cell.textContent=this.exact?exactText:formatValue(row[c.key],c);cell.title=exactText;if(typeof row[c.key]==='number')cell.className='datatable-number';
    }
    tr.append(cell);
   }
   const source=table.sources.find(s=>s.id===row.source)!;
   const link=button(`${source.label} · ${row.member}`,()=>this.showRow(row));
   link.disabled=!this.deps.showMember||source.kind!=='population';link.dataset.unsupported=String(link.disabled);tr.append(el('td',{},link));body.append(tr);
  }
  this.grid.replaceChildren(el('thead',{},heading),body);this.selection.textContent=`${this.selected.size} selected`;
  const previous=button('Previous rows',()=>{this.page--;this.renderRows();this.state();}),next=button('Next rows',()=>{this.page++;this.renderRows();this.state();});previous.disabled=this.page===0;next.disabled=this.page===pages-1;
  this.pager.replaceChildren(previous,el('span',{textContent:`${rows.length?start+1:0}–${Math.min(start+TABLE_PAGE_ROWS,rows.length)} of ${rows.length} rows${Object.keys(this.where).length?' · Filtered':''}`}),next);
 }
}
