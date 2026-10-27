import {object,numeric,nativeFeatures,featureTable,node,type NativeFeature} from './scientific-feature-inspection';
import {pointSymbols,rgba,colorText,type Rgba} from './scientific-geometry-style';
/** Data-only inspection of a native point row, bound to the displayed frame. */
export type PointInspection={kind:'points';frame:string;layer:string;world:number[];data:number[];axes:string[];units:string[];values?:never;
 hit:null|{selected:boolean;index:number;coordinates:string[];dtype:string;features:NativeFeature[];style?:PointElementStyle}};
export type PointElementStyle={symbol:string;size:number;borderWidth:number;faceColor:Rgba;borderColor:Rgba};
export function pointInspection(value:unknown,request:{frame:string;layer:string}):PointInspection{
 const bad=():never=>{throw new Error('The native point sample was invalid. Refresh the preview before inspecting again.');};
 if(!object(value)||Object.keys(value).sort().join(' ')!=='axes data frame hit kind layer units world'||value.kind!=='points'||value.frame!==request.frame||value.layer!==request.layer)return bad();
 const vector=(v:unknown):v is number[]=>Array.isArray(v)&&v.length>=2&&v.length<=8&&v.every(n=>typeof n==='number'&&Number.isFinite(n)&&Math.abs(n)<=Number.MAX_SAFE_INTEGER);
 if(!vector(value.world)||!vector(value.data)||value.world.length!==value.data.length)return bad();
 for(const key of ['axes','units'])if(!Array.isArray(value[key])||value[key].length!==value.data.length||!value[key].every(n=>typeof n==='string'&&n.length<=100))return bad();
 const hit=value.hit;
 if(hit!==null){
  if(!object(hit)||Object.keys(hit).filter(k=>k!=='style').sort().join(' ')!=='coordinates dtype features index selected'||typeof hit.selected!=='boolean'||!Number.isSafeInteger(hit.index)||Number(hit.index)<0||typeof hit.dtype!=='string'||!/^[<>=|][iuf][1248]$/.test(hit.dtype))return bad();
  if(!Array.isArray(hit.coordinates)||hit.coordinates.length!==value.data.length||!hit.coordinates.every(n=>typeof n==='string'&&n.length<=64&&numeric.test(n))||!Array.isArray(hit.features)||hit.features.length>256)return bad();
  if(!nativeFeatures(hit.features))return bad();
  if('style' in hit){const st=hit.style;if(!object(st)||Object.keys(st).sort().join(' ')!=='borderColor borderWidth faceColor size symbol'||!pointSymbols.some(v=>v===st.symbol)||
   typeof st.size!=='number'||!Number.isFinite(st.size)||typeof st.borderWidth!=='number'||!Number.isFinite(st.borderWidth)||!rgba(st.faceColor)||!rgba(st.borderColor))return bad();}
 }
 if(new TextEncoder().encode(JSON.stringify(value)).length>32*1024)return bad();
 return value as PointInspection;
}
export function pointDetails(sample:PointInspection):HTMLElement{
 const root=node('section');root.className='scientific-inspection-features';root.tabIndex=0;root.setAttribute('aria-label','Point features');const hit=sample.hit;if(!hit)return root;
 root.append(node('p',hit.selected?'Selected in this private view':'Not selected in this private view'));
 root.append(node('p',`Point row ${hit.index} in this layer. Coordinates (${hit.dtype}): ${hit.coordinates.join(', ')}. This row number is not an object identifier.`));
 if(hit.style)root.append(node('p',`Marker: ${hit.style.symbol.replaceAll('_',' ')}, size ${hit.style.size}, border width ${hit.style.borderWidth}; face color ${colorText(hit.style.faceColor)}; border color ${colorText(hit.style.borderColor)} (red green blue alpha).`));
 root.append(featureTable(hit.features,'Native point feature values'));return root;
}
