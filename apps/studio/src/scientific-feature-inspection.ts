/** Bounded native feature values; identifiers are never coerced to JS numbers. */
export type NativeFeature={name:string;dtype:string;kind:'string'|'integer'|'float'|'boolean'|'missing'|'datetime'|'timedelta';value:string|null};
export const object=(v:unknown):v is Record<string,unknown>=>!!v&&typeof v==='object'&&!Array.isArray(v);
export const numeric=/^(?:-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|nan|inf|-inf)$/;
export function nativeFeatures(value:unknown):value is NativeFeature[]{
 if(!Array.isArray(value)||value.length>256)return false;
 const names=new Set<string>();
 for(const field of value){
  if(!object(field)||Object.keys(field).sort().join(' ')!=='dtype kind name value'||typeof field.name!=='string'||field.name.length>256||names.has(field.name)||typeof field.dtype!=='string'||field.dtype.length>128||typeof field.kind!=='string'||!['string','integer','float','boolean','missing','datetime','timedelta'].includes(field.kind))return false;
  names.add(field.name);
  if(field.kind==='missing'){if(field.value!==null)return false;}
  else if(typeof field.value!=='string'||field.value.length>4096||field.kind==='integer'&&!/^-?\d+$/.test(field.value)||field.kind==='float'&&!numeric.test(field.value)||field.kind==='boolean'&&!['True','False'].includes(field.value))return false;
 }
 return true;
}
export const node=<K extends keyof HTMLElementTagNameMap>(tag:K,text='')=>{const e=document.createElement(tag);e.textContent=text;return e;};
export function featureTable(features:NativeFeature[],label:string):HTMLElement{
 if(!features.length)return node('p','This row has no feature columns.');
 const table=node('table'),head=node('thead'),heading=node('tr'),body=node('tbody');table.setAttribute('aria-label',label);
 for(const title of ['Feature','Type','Value']){const cell=node('th',title);cell.scope='col';heading.append(cell);}head.append(heading);
 for(const field of features){const row=node('tr'),name=node('th',field.name);name.scope='row';row.append(name,node('td',`${field.dtype} · ${field.kind}`),node('td',field.value===null?'Missing':field.value));body.append(row);}
 table.append(head,body);return table;
}
