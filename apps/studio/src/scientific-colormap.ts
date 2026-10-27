/** Retain color definitions, including special values, independently of a palette name. */
export type ImageColormap={name:string;colors:number[][];controls:number[];interpolation:'linear'|'zero';nanColor:number[];highColor:number[]|null;lowColor:number[]|null};
export function imageColormap(raw:unknown):ImageColormap {
 const fail=():never=>{throw new Error('Use 1–1024 RGBA colors between 0 and 1 and ordered control positions from 0 to 1. Stepped colors need one extra control position.');};
 if(!raw||typeof raw!=='object'||Array.isArray(raw))return fail();
 const v=raw as Record<string,unknown>;
 const unit=(n:unknown)=>typeof n==='number'&&Number.isFinite(n)&&n>=0&&n<=1;
 const color=(n:unknown)=>Array.isArray(n)&&n.length===4&&n.every(unit);
 if(Object.keys(v).sort().join(' ')!=='colors controls highColor interpolation lowColor name nanColor'||typeof v.name!=='string'||v.name.length<1||v.name.length>240||!['linear','zero'].includes(String(v.interpolation))||typeof v.interpolation!=='string'||!Array.isArray(v.colors)||v.colors.length<1||v.colors.length>1024||!v.colors.every(color)||!Array.isArray(v.controls)||v.controls.length!==v.colors.length+Number(v.interpolation==='zero')||!v.controls.every(unit)||v.controls[0]!==0||(v.controls.length>1&&v.controls.at(-1)!==1)||v.controls.some((n,i,a)=>i>0&&n<a[i-1])||!color(v.nanColor)||![v.highColor,v.lowColor].every(n=>n===null||color(n)))return fail();
 return structuredClone(v) as ImageColormap;
}
export function colormapChoices(raw:unknown):string[] {
 if(!Array.isArray(raw)||raw.length>1024||raw.some(n=>typeof n!=='string'||n.length<1||n.length>240)||new Set(raw).size!==raw.length)throw new Error('Invalid image color choices.');
 return [...raw];
}
