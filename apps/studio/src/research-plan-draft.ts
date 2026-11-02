/** Three-way field merge: preserve remote work unless both sides changed that field. */
export type PlanFields=Record<string,string>;
export function mergePlanDraft(base:PlanFields|undefined,draft:PlanFields,saved:PlanFields){
 const values={...saved},conflicts:string[]=[];
 for(const key of Object.keys(saved)){
  if(draft[key]===undefined||draft[key]===saved[key])continue;
  if(base&&draft[key]===base[key])continue;
  if(base&&saved[key]===base[key])values[key]=draft[key];
  else conflicts.push(key);
 }
 return {values,conflicts};
}
