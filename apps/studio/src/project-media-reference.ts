/** contract/agent/project-media-reference.schema.json; identity, not authority. */
export interface ProjectMediaReference {origin:string;session:string;document:string;revision:number;digest:string}
export function readProjectMediaReference(value:unknown,session:string):ProjectMediaReference|null {
  if(!value||typeof value!=='object'||Array.isArray(value))return null;
  const r=value as Record<string,unknown>;
  if(Object.keys(r).sort().join(',')!=='digest,document,origin,revision,session'||r.session!==session||
    typeof r.origin!=='string'||!/^[a-f0-9]{32}$/.test(r.origin)||typeof r.document!=='string'||!/^p[a-f0-9]{32}$/.test(r.document)||
    typeof r.digest!=='string'||!/^[a-f0-9]{64}$/.test(r.digest)||typeof r.revision!=='number'||!Number.isInteger(r.revision)||r.revision<1)return null;
  return {origin:r.origin,session,document:r.document,revision:r.revision,digest:r.digest};
}
export function sameMediaReference(a:ProjectMediaReference|undefined,b:ProjectMediaReference|undefined):boolean {
  return a===b||!!a&&!!b&&a.origin===b.origin&&a.session===b.session&&a.document===b.document&&a.revision===b.revision&&a.digest===b.digest;
}
