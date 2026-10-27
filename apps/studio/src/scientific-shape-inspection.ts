import {object,numeric,nativeFeatures,featureTable,node,type NativeFeature} from './scientific-feature-inspection';
import {rgba,colorText,type Rgba} from './scientific-geometry-style';
export type ShapeInspection={kind:'shapes';frame:string;layer:string;world:number[];data:number[];axes:string[];units:string[];values?:never;
 hit:null|{selected:boolean;index:number;geometry:'line'|'rectangle'|'ellipse'|'path'|'polygon';vertices:string[][];dtype:string;features:NativeFeature[];style?:ShapeElementStyle}};
export type ShapeElementStyle={edgeWidth:number;faceColor:Rgba;edgeColor:Rgba;z:number};
export function shapeInspection(value:unknown,request:{frame:string;layer:string}):ShapeInspection{
 const bad=():never=>{throw new Error('The native shape sample was invalid. Refresh the preview before inspecting again.');};
 if(!object(value)||Object.keys(value).sort().join(' ')!=='axes data frame hit kind layer units world'||value.kind!=='shapes'||value.frame!==request.frame||value.layer!==request.layer)return bad();
 const vector=(v:unknown):v is number[]=>Array.isArray(v)&&v.length>=2&&v.length<=8&&v.every(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=Number.MAX_SAFE_INTEGER);
 if(!vector(value.world)||!vector(value.data)||value.world.length!==value.data.length)return bad();
 for(const key of ['axes','units'])if(!Array.isArray(value[key])||value[key].length!==value.data.length||!value[key].every(n=>typeof n==='string'&&n.length<=100))return bad();
 const hit=value.hit,dimensions=value.data.length;
 if(hit!==null){
  if(!object(hit)||Object.keys(hit).filter(k=>k!=='style').sort().join(' ')!=='dtype features geometry index selected vertices'||typeof hit.selected!=='boolean'||!Number.isSafeInteger(hit.index)||Number(hit.index)<0||typeof hit.geometry!=='string'||!['line','rectangle','ellipse','path','polygon'].includes(hit.geometry)||typeof hit.dtype!=='string'||!/^[<>=|][iuf][1248]$/.test(hit.dtype))return bad();
  if(!Array.isArray(hit.vertices)||hit.vertices.length<(hit.geometry==='polygon'?3:2)||hit.vertices.length>256||['rectangle','ellipse'].includes(hit.geometry)&&hit.vertices.length!==4||hit.geometry==='line'&&hit.vertices.length!==2)return bad();
  if(!hit.vertices.every(row=>Array.isArray(row)&&row.length===dimensions&&row.every(n=>typeof n==='string'&&n.length<=64&&numeric.test(n)&&Number.isFinite(Number(n))))||!nativeFeatures(hit.features))return bad();
  if('style' in hit){const st=hit.style;if(!object(st)||Object.keys(st).sort().join(' ')!=='edgeColor edgeWidth faceColor z'||typeof st.edgeWidth!=='number'||!Number.isFinite(st.edgeWidth)||
   !rgba(st.faceColor)||!rgba(st.edgeColor)||!Number.isSafeInteger(st.z))return bad();}
 }
 if(new TextEncoder().encode(JSON.stringify(value)).length>64*1024)return bad();
 return value as ShapeInspection;
}
export function shapeDetails(sample:ShapeInspection):HTMLElement{
 const root=node('section');root.className='scientific-inspection-features';root.tabIndex=0;root.setAttribute('aria-label','Shape features');const hit=sample.hit;if(!hit)return root;
 root.append(node('p',hit.selected?'Selected in this private view':'Not selected in this private view'));
 root.append(node('p',`Shape row ${hit.index} · ${hit.geometry}. This row number is not an object identifier.`));
 const geometry=node('details');geometry.append(node('summary',`${hit.vertices.length} native control vertices (${hit.dtype})`));
 geometry.append(node('p',hit.geometry==='ellipse'?'These four vertices define the ellipse boundary box; they are not a polygon approximation.':'Ordered native vertices are kept together as one shape.'));
 const list=node('ol');for(const row of hit.vertices)list.append(node('li',row.join(', ')));geometry.append(list);root.append(geometry);
 if(hit.style)root.append(node('p',`Edge width ${hit.style.edgeWidth}; face color ${colorText(hit.style.faceColor)}; edge color ${colorText(hit.style.edgeColor)} (red green blue alpha); z-order ${hit.style.z}.`));
 root.append(featureTable(hit.features,'Native shape feature values'));return root;
}
