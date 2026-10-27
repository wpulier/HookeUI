import {shapeInspection,shapeDetails,type ShapeInspection} from './scientific-shape-inspection';
import {pointInspection,pointDetails,type PointInspection} from './scientific-point-inspection';
import type {ScientificFrame} from './scientific-view';
import type {ScientificCanvas} from './scientific-canvas';
import {nativeFeatures,featureTable,type NativeFeature} from './scientific-feature-inspection';
export type GeometrySelection={kind:'points'|'shapes';layer:string;action:'replace'|'toggle'|'clear';index:number|null};
export type InspectionRequest={op:'inspect';frame:string;layer:string;position:[number,number]};
type ImageInspection={kind?:never;frame:string;layer:string;world:number[];data:number[];axes:string[];units:string[];level:number|null;projection:string;dtype:string;values:string[]|null;labelFeatures?:{row:number|null;features:NativeFeature[]}};
const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
export function inspectionReply(v:unknown,request:InspectionRequest):ImageInspection|PointInspection|ShapeInspection{
 if(object(v)&&v.kind==='shapes')return shapeInspection(v,request);
 if(object(v)&&v.kind==='points')return pointInspection(v,request);
 const bad=():never=>{throw new Error('The native sample was invalid. Refresh the preview before inspecting again.');};
 if(!object(v)||Object.keys(v).sort().join(' ')!==('axes data dtype frame layer level projection units values world'+('labelFeatures' in v?' labelFeatures':'')).split(' ').sort().join(' ')||v.frame!==request.frame||v.layer!==request.layer)return bad();
 const vector=(n:unknown):n is number[]=>Array.isArray(n)&&n.length>=2&&n.length<=8&&n.every(x=>typeof x==='number'&&Number.isFinite(x)&&Math.abs(x)<=Number.MAX_SAFE_INTEGER);
 if(!vector(v.world)||!vector(v.data)||v.world.length!==v.data.length)return bad();
 for(const key of ['axes','units'])if(!Array.isArray(v[key])||v[key].length!==v.data.length||!v[key].every(s=>typeof s==='string'&&s.length<=100))return bad();
 if(v.level!==null&&(!Number.isSafeInteger(v.level)||Number(v.level)<0||Number(v.level)>63))return bad();
 if(typeof v.projection!=='string'||!['none','sum','mean','max','min'].includes(v.projection)||typeof v.dtype!=='string'||!/^[<>=|][biuf][1248]$/.test(v.dtype))return bad();
 if(v.values!==null&&(!Array.isArray(v.values)||![1,3,4].includes(v.values.length)||!v.values.every(s=>typeof s==='string'&&s.length<=64&&/^(?:-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|nan|inf|-inf|True|False)$/.test(s))))return bad();
 if('labelFeatures' in v){
  // A label's feature row, as napari finds it: its 'index' column, else the row number. Identifiers stay strings.
  const found=v.labelFeatures;
  if(!object(found)||Object.keys(found).sort().join(' ')!=='features row'||!Array.isArray(v.values)||v.values.length!==1||!/^-?\d+$/.test(String(v.values[0]))||
     (found.row!==null&&(!Number.isSafeInteger(found.row)||Number(found.row)<0))||!nativeFeatures(found.features)||(found.row===null&&found.features.length!==0))return bad();
  if(new TextEncoder().encode(JSON.stringify(v)).length>32*1024)return bad();
 }else if(new TextEncoder().encode(JSON.stringify(v)).length>4096)return bad();
 return v as ImageInspection;
}
const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
/** Read one native displayed-slice sample, tied to exact pixels and session. */
export class ScientificInspection{
 private readonly root:HTMLElement;private readonly drawing:ScientificCanvas;private readonly options:{send(request:InspectionRequest,draw:string):Promise<unknown>;changed():void;select?(frame:string,value:GeometrySelection):Promise<void>};
 private frame:ScientificFrame|null=null;private session:string|null=null;private available=false;private pending=false;private epoch=0;private attempt=0;private closed=false;
 private readonly details=node('div');private readonly toggle=node('input');private readonly layer=node('select');private readonly status=node('p');private readonly center=node('button','Inspect preview center');
 private readonly click=(e:MouseEvent)=>{if(!this.ready()||!this.toggle.checked)return;const paint=this.drawing.current();if(!paint)return;try{void this.inspect(this.drawing.position(paint.draw,e.clientX,e.clientY));}catch(error){this.status.textContent=String(error);}};
 constructor(root:HTMLElement,drawing:ScientificCanvas,options:{send(request:InspectionRequest,draw:string):Promise<unknown>;changed():void;select?(frame:string,value:GeometrySelection):Promise<void>}){
  this.root=root;this.drawing=drawing;this.options=options;
  this.toggle.type='checkbox';this.toggle.setAttribute('aria-label','Inspect native values');const mode=node('label','Inspect native values');mode.append(this.toggle);
  this.layer.setAttribute('aria-label','Inspect layer');const choice=node('label','Layer');choice.append(this.layer);
  this.details.className='scientific-inspection-details';this.status.setAttribute('role','status');root.append(mode,choice,this.center,node('p','Click the 2D preview to inspect native slice values and calibrated coordinates. Point and shape rows include native geometry and typed features. Projected and multiscale values are identified. Saved data is unchanged.'),this.status,this.details);
  this.toggle.addEventListener('change',()=>{this.revoke();this.paint();this.options.changed();});this.layer.addEventListener('change',()=>this.revoke());
  this.center.addEventListener('click',()=>{const frame=this.drawing.current()?.frame;if(frame&&this.ready())void this.inspect([frame.height/2,frame.width/2]);});
  this.drawing.element().addEventListener('click',this.click);this.paint();
 }
 active():boolean{return this.toggle.checked;}
 blocked():boolean{return this.pending;}
 bindSession(value:string|null):void{if(value===this.session)return;this.session=value;this.frame=null;this.epoch++;this.attempt++;this.pending=false;this.status.textContent='';this.details.replaceChildren();this.paint();}
 update(frame:ScientificFrame):void{if(this.frame?.frame!==frame.frame)this.revoke();this.frame=frame;const selected=this.layer.value,eligible=frame.layers.filter(l=>l.visible&&(l.type==='image'||l.type==='labels'||l.type==='points'||l.type==='shapes'));this.layer.replaceChildren();for(const layer of eligible){const option=node('option',layer.name);option.value=layer.id;this.layer.append(option);}this.layer.value=eligible.some(l=>l.id===selected)?selected:eligible[0]?.id??'';this.paint();}
 setAvailable(value:boolean):void{this.available=value;this.paint();}
 revoke():void{this.epoch++;this.status.textContent='';this.details.replaceChildren();}
 private ready():boolean{return !this.closed&&this.available&&!!this.session&&!this.pending&&this.frame?.dimensions.ndisplay===2&&!this.frame.grid?.enabled&&!!this.layer.value;}
 private async inspect(position:[number,number]):Promise<void>{
  if(!this.ready())return;const paint=this.drawing.current();if(!paint||paint.frame.frame!==this.frame?.frame)return;
  const request:InspectionRequest={op:'inspect',frame:paint.frame.frame,layer:this.layer.value,position},epoch=this.epoch,attempt=++this.attempt;
  this.pending=true;this.details.replaceChildren();this.status.textContent='Reading native sample…';this.paint();this.options.changed();
  try{const result=inspectionReply(await this.options.send(request,paint.draw),request);if(this.closed||epoch!==this.epoch||this.drawing.current()?.draw!==paint.draw)return;
   const coordinates=result.axes.map((axis,i)=>`${axis}: ${result.world[i]} ${result.units[i]} (data ${result.data[i]})`).join(' · ');
   if(result.kind==='shapes'){this.status.textContent=`${result.hit===null?'No shape at this position':`Shape row ${result.hit.index} · ${result.hit.geometry}`} · ${coordinates}`;this.details.replaceChildren(shapeDetails(result));this.selectionActions(result,paint.draw);return;}
   if(result.kind==='points'){this.status.textContent=`${result.hit===null?'No point at this position':`Point row ${result.hit.index}`} · ${coordinates}`;this.details.replaceChildren(pointDetails(result));this.selectionActions(result,paint.draw);return;}
   this.status.textContent=`${result.values===null?'No sample at this position':`Value ${result.values.join(', ')} · ${result.dtype}`} · ${coordinates}${result.level===null?'':` · Multiscale level ${result.level}`}${result.projection==='none'?'':` · ${result.projection} slice projection`}`;
   if(result.labelFeatures){
    const section=node('section');section.className='scientific-inspection-features';section.tabIndex=0;section.setAttribute('aria-label','Label features');
    if(result.labelFeatures.row===null)section.append(node('p',`Label ${result.values![0]} has no row in this layer's features.`));
    else section.append(node('p',`Label ${result.values![0]} · feature row ${result.labelFeatures.row}`),featureTable(result.labelFeatures.features,'Native label feature values'));
    this.details.replaceChildren(section);
   }
  }catch(error){if(!this.closed&&epoch===this.epoch)this.status.textContent=(error instanceof Error?error.message:String(error))+' Refresh the preview and retry.';}
  finally{if(!this.closed&&attempt===this.attempt){this.pending=false;this.paint();this.options.changed();}}
 }
 private selectionActions(result:PointInspection|ShapeInspection,draw:string):void{
  if(!this.options.select)return;
  const actions=node('div'),epoch=this.epoch,label=result.kind==='points'?'point':'shape';
  const add=(label:string,action:GeometrySelection['action'],index:number|null)=>{const button=node('button',label);button.addEventListener('click',()=>void this.selectGeometry(result.frame,draw,epoch,{kind:result.kind,layer:result.layer,action,index}));actions.append(button);};
  if(result.hit){add(`Select this ${label}`,'replace',result.hit.index);add(result.hit.selected?'Remove from selection':'Add to selection','toggle',result.hit.index);}
  add(`Clear ${label} selection`,'clear',null);
  actions.append(node('p','Selection is private to this session. Rows are not object identifiers. Save scientific edits separately.'));
  this.details.append(actions);
 }
 private async selectGeometry(frame:string,draw:string,epoch:number,value:GeometrySelection):Promise<void>{
  if(epoch!==this.epoch||!this.options.select||this.closed)return;
  // A redraw since this inspection (or one in progress) means the row may no longer be the one shown: say so, never drop the click silently.
  if(!this.ready()||this.frame?.frame!==frame||this.drawing.current()?.draw!==draw){
   this.status.textContent=this.ready()?'The view changed after this inspection. Inspect again, then select.':'Wait for the view to finish updating, then inspect again and select.';return;}
  const session=this.session,attempt=++this.attempt;
  this.pending=true;this.details.replaceChildren();this.status.textContent='Updating selection…';this.paint();this.options.changed();
  try{await this.options.select(frame,value);if(!this.closed&&session===this.session&&attempt===this.attempt)this.status.textContent=`${value.kind==='points'?'Point':'Shape'} selection updated in this private view. Save scientific edits separately.`;}
  catch(error){if(!this.closed&&session===this.session&&attempt===this.attempt)this.status.textContent=(error instanceof Error?error.message:String(error))+' Refresh and inspect the selection before making another change.';}
  finally{if(!this.closed&&attempt===this.attempt){this.pending=false;this.paint();this.options.changed();}}
 }
 private paint():void{this.root.hidden=this.frame?.dimensions.ndisplay!==2||!!this.frame.grid?.enabled;this.layer.disabled=!this.available||this.pending;this.toggle.disabled=!this.available||this.pending;this.center.disabled=!this.ready();}
 close():void{this.closed=true;this.epoch++;this.drawing.element().removeEventListener('click',this.click);this.root.replaceChildren();}
}
