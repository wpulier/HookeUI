/** Presentation of existing native feature text; no feature/string/color conversion. */
export type TextAppearance={visible:boolean;size:number;scaling:boolean;blending:string;anchor:string;translation:number|number[];rotation:number};
export const textAnchors=['center','upper_left','upper_right','lower_left','lower_right'] as const;
export const textBlending=['translucent','translucent_no_depth','additive','minimum','multiplicative'] as const;
export function textAppearance(raw:unknown,ndim:number):TextAppearance {
  const fail=():never=>{throw new Error('Text display needs a positive size, finite rotation and an offset with one value or one per layer axis.');};
  if(!raw||typeof raw!=='object'||Array.isArray(raw))return fail();
  const value=raw as Record<string,unknown>;
  const finite=(n:unknown):n is number=>typeof n==='number'&&Number.isFinite(n);
  if(Object.keys(value).sort().join(' ')!=='anchor blending rotation scaling size translation visible'||typeof value.visible!=='boolean'||typeof value.scaling!=='boolean'||!finite(value.size)||value.size<=0||!finite(value.rotation)||
    typeof value.anchor!=='string'||!textAnchors.some(v=>v===value.anchor)||typeof value.blending!=='string'||!textBlending.some(v=>v===value.blending)||
    !(finite(value.translation)||Array.isArray(value.translation)&&[1,ndim].includes(value.translation.length)&&value.translation.every(finite)))return fail();
  return structuredClone(raw) as TextAppearance;
}
